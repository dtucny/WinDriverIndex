"""INF-level identity extraction (spec §6.2).

Unpack each stored payload with 7-Zip (handles zip, cab, MSI, and most
self-extracting .exe), one level of nesting. Record every .inf and .sys:
their hashes are the cross-vendor identity, the INF text yields HWIDs,
DriverVer, provider and class for family assignment.

INF encoding is a zoo: UTF-16LE with BOM, UTF-8, or ANSI. Decoded leniently —
hashes are taken over raw bytes, decoding only feeds the metadata regexes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path

from . import config
from .fetch import stored_payload
from .hwids import is_hwid

SEVENZIP = shutil.which("7zz") or shutil.which("7z") or "7zz"
NESTED_EXTS = {".zip", ".cab", ".exe", ".msi", ".7z"}

_DRIVER_VER = re.compile(r"^\s*DriverVer\s*=\s*(\d+)/(\d+)/(\d+)\s*(?:,\s*([\w.]+))?",
                         re.IGNORECASE | re.MULTILINE)
_PROVIDER = re.compile(r"^\s*Provider\s*=\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_CLASS = re.compile(r"^\s*Class\s*=\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_SECTION = re.compile(r"^\s*\[([^\]]+)\]", re.MULTILINE)
_STRING = re.compile(r'^\s*([\w.]+)\s*=\s*"([^"]*)"', re.MULTILINE)
_MODEL = re.compile(r'^[ \t]*"?%([\w.]+)%"?[ \t]*=[^,;\r\n]+,([^;\r\n]+)', re.MULTILINE)
_HWID = re.compile(
    r"\b((?:PCI|USB|HDAUDIO|INTELAUDIO|ACPI|BTHENUM|BTH|SWC|MTP|SD|UEFI)"
    r"\\[A-Za-z0-9][A-Za-z0-9_&.\-]*)", re.IGNORECASE)


def run(conn: sqlite3.Connection, *, limit: int | None = None,
        retry_quarantined: bool = False, log=print) -> dict:
    (config.DATA_DIR / "tmp").mkdir(parents=True, exist_ok=True)
    if retry_quarantined:
        conn.execute("DELETE FROM payload_quarantine")
    done = {r[0] for r in conn.execute("SELECT DISTINCT payload_sha256 FROM payload_file")}
    quarantined = {r[0] for r in conn.execute("SELECT payload_sha256 FROM payload_quarantine")}
    todo = [r[0] for r in conn.execute(
        "SELECT DISTINCT sha256 FROM artefact"
        " WHERE sha256 IS NOT NULL AND kind = 'driver'"
        " UNION SELECT sha256 FROM upstream_payload WHERE sha256 IS NOT NULL")
        if r[0] not in done and r[0] not in quarantined]
    if limit:
        todo = todo[:limit]
    n_ok = n_noinf = n_fail = 0
    for sha in todo:
        path = stored_payload(sha)
        if not path:
            continue
        try:
            files = _extract_and_index(sha, path)
        except Exception as exc:
            # Vendor-corrupt archives (published hash matches the bytes) fail
            # identically every run; park them so the stage's failure exit
            # stays meaningful for systemic faults.
            reason = " ".join(str(exc).split())[:200]   # 7z stderr starts blank
            log(f"EXTRACT FAIL {sha[:12]}: {exc}")
            log(f"  QUARANTINED {sha[:12]} ({reason})")
            conn.execute(
                "INSERT OR REPLACE INTO payload_quarantine VALUES (?, ?, ?)",
                (sha, reason, dt.date.today().isoformat()))
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
                conn.execute("DELETE FROM inf_device_name WHERE inf_sha256 = ?",
                             (f["sha256"],))
                conn.executemany(
                    "INSERT OR IGNORE INTO inf_device_name VALUES (?, ?, ?)",
                    [(f["sha256"], h, n) for h, n in meta["names"].items()])
        n_ok += 1
        n_noinf += not infs
        log(f"extracted {sha[:12]}: {len(files)} inf/sys files, {len(infs)} INFs")
    # "failed" drives the CLI exit status (stop-before-publish guard). A few
    # corrupt vendor files are expected and now quarantined; only a run
    # where failures outnumber successes (7-Zip missing, tmp dir full)
    # should fail the stage.
    systemic = n_fail > n_ok
    stats = {"extracted": n_ok, "no_inf": n_noinf, "quarantined": n_fail,
             "skipped_quarantined": len(quarantined),
             "failed": n_fail if systemic else 0}
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
        records = _index_layers(root, (("0", 0), ("1", 1), ("2", 2)))
        if not any("meta" in r for r in records) and zipfile.is_zipfile(archive):
            # a zip behind a PE stub (Dell's Cypress/Intel self-extractors):
            # 7-Zip shows only the stub's sections, Python's zipfile reads
            # the archive from its end
            with zipfile.ZipFile(archive) as z:
                z.extractall(root / "z")   # drops '..' and absolute names
            for inner in sorted((root / "z").rglob("*")):
                if inner.is_file() and inner.suffix.lower() in NESTED_EXTS:
                    _7z(inner, root / "z1" / inner.relative_to(root / "z"),
                        ignore_errors=True)
            records += _index_layers(root, (("z", 0), ("z1", 1)))
        if not any("meta" in r for r in records) and _magic(archive) == "pe":
            # installers whose drivers 7-Zip's PE view can't see (Intel's
            # WiX Burn bundles): carve them apart by signature instead
            _carve(archive, root / "b")
            _burn_names(root / "b")
            records += _index_layers(root, (("b", 3),))
        return records


def _index_layers(root: Path, layers) -> list[dict]:
    records = []
    for base, layer in layers:
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
                if layer == 3 and not suffix and (cat := _CATALOG.search(_decode_inf(data))):
                    # carved MSI streams ('fil5639F8…') lose the file name;
                    # Intel names each INF after its catalog (ibtusb.cat)
                    rec["path"] = rec["path"] + "/" + cat.group(1).rsplit(".", 1)[0] + ".inf"
            records.append(rec)
    return records


_CATALOG = re.compile(r"^\s*CatalogFile(?:\.NTamd64)?\s*=\s*([\w.\-]+\.cat)\b",
                      re.IGNORECASE | re.MULTILINE)
_BURN_PAYLOAD = re.compile(rb'<Payload [^>]*?FilePath="([^"]+)"[^>]*?SourcePath="([^"]+)"'
                           rb'[^>]*?Container=')


def _magic(path: Path) -> str | None:
    with open(path, "rb") as fh:
        head = fh.read(8)
    for sig, kind in ((b"MZ", "pe"), (b"MSCF", "cab"), (b"\xd0\xcf\x11\xe0", "msi"),
                      (b"PK\x03\x04", "zip"), (b"7z\xbc\xaf\x27\x1c", "7z")):
        if head.startswith(sig):
            return kind
    return None


def _burn_names(root: Path) -> None:
    """Give a Burn attached container's files ('a20') their manifest paths
    ('WiFiDriver/Netwtw08.INF'). The manifest sits in the UX cab, a sibling
    of the container cab under the same split."""
    for manifest in root.rglob("*"):
        if not manifest.is_file() or manifest.stat().st_size > 20_000_000:
            continue
        with open(manifest, "rb") as fh:
            if b"<BurnManifest" not in fh.read(4096):
                continue
        split = manifest.parent.parent
        names = {src.decode(): dst.decode().replace("\\", "/")
                 for dst, src in _BURN_PAYLOAD.findall(manifest.read_bytes())}
        for container in split.glob("*.cab.x"):
            for src, dst in names.items():
                f = container / src
                if f.is_file():
                    (container / dst).parent.mkdir(parents=True, exist_ok=True)
                    f.rename(container / dst)


def _carve(path: Path, out: Path, depth: int = 0) -> None:
    """Split a PE by archive signatures (7-Zip '#'), then open what falls out:
    inner PEs the same way, cabs and MSIs as archives. A Burn bundle is a stub
    whose overlay is another PE, whose overlay holds the UX cab and the
    attached container (extensionless files: UTF-16 INFs, catalogs, MSIs).
    Pieces under 1 MB that are PEs are the stubs' own resources, not payloads."""
    if depth > 4:
        return
    kind = _magic(path)
    if kind == "pe":
        _7z(path, out, ignore_errors=True, split=True)
    elif kind:
        _7z(path, out, ignore_errors=True)
    else:
        return
    for f in sorted(out.rglob("*")):
        if not f.is_file() or f == path or f.stat().st_size <= 4096:
            continue
        k = _magic(f)
        if k and not (k == "pe" and f.stat().st_size < 1_000_000):
            _carve(f, f.parent / (f.name + ".x"), depth + 1)


def _7z(archive: Path, out: Path, *, ignore_errors: bool = False,
        split: bool = False) -> None:
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [SEVENZIP, "x", "-y", "-p", *(["-t#"] if split else []), f"-o{out}", str(archive)],
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
    # the scan also meets [Strings] keys and registry paths; keep real IDs
    hwids = sorted({h.upper() for h in _HWID.findall(text) if is_hwid(h)})
    prov = _PROVIDER.search(text)
    cls = _CLASS.search(text)
    return {
        "names": (_device_names(text)
                  if cls and cls.group(1).lower() == "display" else {}),
        "provider": prov.group(1).strip('"') if prov else None,
        "class": cls.group(1) if cls else None,
        "driver_date": driver_date,
        "driver_ver": driver_ver,
        "hwids": hwids,
    }


def _device_names(text: str) -> dict[str, str]:
    """{PCI hardware ID: device name} from a display INF: models lines
    ('%NVIDIA_DEV.2782% = Section, PCI\\VEN_10DE&DEV_2782') resolved through
    the unlocalised [Strings] section. Graphics cards are named by chip, and
    this is where the chip's marketing name meets its PCI ID."""
    strings: dict[str, str] = {}
    heads = list(_SECTION.finditer(text))
    for i, m in enumerate(heads):
        if m.group(1).strip().lower() == "strings":
            start = m.end()
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            strings.update((k.lower(), v.strip())
                           for k, v in _STRING.findall(text[start:end]))
    out: dict[str, str] = {}
    for key, ids in _MODEL.findall(text):
        name = strings.get(key.lower())
        if not name:
            continue
        for h in ids.split(","):
            h = h.strip().upper()
            if h.startswith("PCI\\"):
                out.setdefault(h, name)
    return out
