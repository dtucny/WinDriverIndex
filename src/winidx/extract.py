"""INF-level identity extraction (spec §6.2).

Unpack each stored payload with 7-Zip (handles zip, cab, MSI, and most
self-extracting .exe), one level of nesting. Record every .inf and .sys:
their hashes are the cross-vendor identity, the INF text yields HWIDs,
DriverVer, provider and class for family assignment.

INF encoding is a zoo: UTF-16LE with BOM, UTF-8, or ANSI. Decoded leniently —
hashes are taken over raw bytes, decoding only feeds the metadata regexes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from . import config
from .fetch import payload_path

SEVENZIP = shutil.which("7zz") or shutil.which("7z") or "7zz"
NESTED_EXTS = {".zip", ".cab", ".exe", ".msi", ".7z"}

_DRIVER_VER = re.compile(r"^\s*DriverVer\s*=\s*(\d+)/(\d+)/(\d+)\s*(?:,\s*([\w.]+))?",
                         re.IGNORECASE | re.MULTILINE)
_PROVIDER = re.compile(r"^\s*Provider\s*=\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_CLASS = re.compile(r"^\s*Class\s*=\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_HWID = re.compile(
    r"\b((?:PCI|USB|HDAUDIO|INTELAUDIO|ACPI|BTHENUM|BTH|SWC|MTP|SD|UEFI)"
    r"\\[A-Za-z0-9][A-Za-z0-9_&.\-]*)", re.IGNORECASE)


def run(conn: sqlite3.Connection, *, limit: int | None = None, log=print) -> dict:
    (config.DATA_DIR / "tmp").mkdir(parents=True, exist_ok=True)
    done = {r[0] for r in conn.execute("SELECT DISTINCT payload_sha256 FROM payload_file")}
    todo = [r[0] for r in conn.execute(
        "SELECT DISTINCT sha256 FROM artefact"
        " WHERE sha256 IS NOT NULL AND kind = 'driver'") if r[0] not in done]
    if limit:
        todo = todo[:limit]
    n_ok = n_noinf = n_fail = 0
    for sha in todo:
        path = next(iter(payload_path(sha).parent.glob(sha + ".*")), None)
        if not path:
            continue
        try:
            files = _extract_and_index(sha, path)
        except Exception as exc:
            log(f"EXTRACT FAIL {sha[:12]}: {exc}")
            n_fail += 1
            continue
        infs = [f for f in files if "meta" in f]
        with conn:
            conn.execute("DELETE FROM payload_file WHERE payload_sha256 = ?", (sha,))
            conn.execute("DELETE FROM inf WHERE payload_sha256 = ?", (sha,))
            for f in files:
                conn.execute(
                    "INSERT OR REPLACE INTO payload_file VALUES (?, ?, ?, ?)",
                    (sha, f["path"], f["sha256"], f["size"]))
            for f in infs:
                meta = f["meta"]
                conn.execute(
                    "INSERT OR REPLACE INTO inf VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (sha, f["path"], f["sha256"], meta["provider"], meta["class"],
                     meta["driver_date"], meta["driver_ver"],
                     json.dumps(meta["hwids"])))
        n_ok += 1
        n_noinf += not infs
        log(f"extracted {sha[:12]}: {len(files)} inf/sys files, {len(infs)} INFs")
    stats = {"extracted": n_ok, "no_inf": n_noinf, "failed": n_fail}
    log(f"extract: {stats}")
    return stats


def _extract_and_index(sha: str, archive: Path) -> list[dict]:
    with tempfile.TemporaryDirectory(prefix=f"winidx_{sha[:8]}_",
                                     dir=config.DATA_DIR / "tmp") as tmp:
        root = Path(tmp)
        _7z(archive, root / "0")
        # Two levels of nesting: vendors wrap drivers in self-extractors and
        # .msi packages whose CABs hold the INFs (e.g. MSI's Intel Bluetooth
        # zip -> .msi -> cab).
        for src, dst in (("0", "1"), ("1", "2")):
            if not (root / src).exists():
                continue
            for inner in sorted((root / src).rglob("*")):
                if inner.is_file() and inner.suffix.lower() in NESTED_EXTS:
                    out = root / dst / inner.relative_to(root / src)
                    _7z(inner, out, ignore_errors=True)   # many .exe aren't archives
        records = []
        for base, layer in (("0", 0), ("1", 1), ("2", 2)):
            layer_root = root / base
            if not layer_root.exists():
                continue
            for f in sorted(layer_root.rglob("*")):
                if not f.is_file():
                    continue
                suffix = f.suffix.lower()
                # 7z extracts .msi contents as extensionless file-table
                # stream names ('fil07F5DF…'); sniff those for INF content.
                is_inf = suffix == ".inf"
                if not suffix and layer > 0 and f.stat().st_size < 2_000_000:
                    head = f.read_bytes()[:4096]
                    text = head.decode("utf-16", errors="ignore") \
                        if head[:2] in (b"\xff\xfe", b"\xfe\xff") \
                        else head.decode("latin-1", errors="ignore")
                    is_inf = "$WINDOWS NT$" in text or "[Version]" in text
                if not (is_inf or suffix == ".sys"):
                    continue
                data = f.read_bytes()
                rec = {
                    "path": "!" * layer + str(f.relative_to(layer_root)),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                }
                if is_inf:
                    rec["meta"] = _parse_inf(data)
                records.append(rec)
        return records


def _7z(archive: Path, out: Path, *, ignore_errors: bool = False) -> None:
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [SEVENZIP, "x", "-y", "-p", f"-o{out}", str(archive)],
        capture_output=True, timeout=1800)
    if proc.returncode not in (0, 1) and not ignore_errors:   # 1 = warnings
        raise RuntimeError(f"7z exit {proc.returncode}: "
                           f"{proc.stderr.decode(errors='replace')[:200]}")


def _decode_inf(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _parse_inf(data: bytes) -> dict:
    text = _decode_inf(data)
    ver = _DRIVER_VER.search(text)
    driver_date = driver_ver = None
    if ver:
        m, d, y = int(ver.group(1)), int(ver.group(2)), int(ver.group(3))
        driver_date = f"{y:04d}-{m:02d}-{d:02d}"
        driver_ver = ver.group(4)
    hwids = sorted({h.upper() for h in _HWID.findall(text)})
    prov = _PROVIDER.search(text)
    cls = _CLASS.search(text)
    return {
        "provider": prov.group(1).strip('"') if prov else None,
        "class": cls.group(1) if cls else None,
        "driver_date": driver_date,
        "driver_ver": driver_ver,
        "hwids": hwids,
    }
