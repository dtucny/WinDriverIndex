"""Dell crawler (roadmap v0.3) — the whole vendor from one file.

Dell Command Update's public catalog (downloads.dell.com/catalog/
CatalogPC.cab, chrome-TLS required, ~3 MB cab holding one UTF-16 XML) lists
every supported business system (Latitude/Precision/OptiPlex/XPS/Dell Pro —
496 systems as of 2026-08) and ~4,100 packages, each with the real
vendorVersion, a month-name releaseDate, MD5, download path, Category,
ComponentType, PCI device ids, and the systems it applies to. One download
per run; no per-model requests at all.

Consumer lines (Inspiron/Alienware/G) are not in this catalog — Dell Update
manages those separately; enumeration source TBD (the Legion story again).

Systems sharing a display name (config-variant systemIDs) are merged into
one board keyed by the name slug.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3

from .. import db, versions

VENDOR = "dell"
CATALOG = "https://downloads.dell.com/catalog/CatalogPC.cab"
DL_BASE = "https://downloads.dell.com/"
BROWSER_HEADERS = True   # downloads.dell.com 403s honest UAs

_COMPONENT = re.compile(r"<SoftwareComponent (.*?)</SoftwareComponent>", re.S)
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_NAME = re.compile(r"<Name>\s*<Display[^>]*><!\[CDATA\[(.*?)\]\]>", re.S)
_CTYPE = re.compile(r'<ComponentType value="(\w+)"')
_CAT = re.compile(r"<Category[^>]*>\s*<Display[^>]*><!\[CDATA\[(.*?)\]\]>", re.S)
_MODEL = re.compile(r'<Brand key="\d+"[^>]*>\s*<Display[^>]*><!\[CDATA\[([^\]]+)\]\]></Display>(.*?)</Brand>', re.S)
_SYS = re.compile(r'<Model systemID="([^"]+)"[^>]*>\s*<Display[^>]*><!\[CDATA\[([^\]]+)\]\]>')

KIND = {"DRVR": "driver", "BIOS": "bios", "FRMW": "firmware"}

# Pre-Win11-floor systems (2015-17, 6th/7th-gen) that survive the BIOS-scheme
# +date gate because Dell genuinely BIOS-serviced them into the Win11 era
# (rugged/extended-support lifecycles). Name-listed after data review.
# 'XPS Notebook 9350' is deliberately NOT here: Dell reused the 9350 name in
# 2024, and the catalog merges both eras — kept, imperfect, pending
# sid-level disambiguation via the per-model CatalogIndexPC.
DENY_NAMES = {
    "Latitude 3379", "Latitude 5175/5179", "Latitude 5414", "Latitude 7214",
    "Latitude 7275", "Latitude 7414", "Optiplex 3046", "Optiplex 3240 AIO",
    "Optiplex 5260 AIO", "Optiplex OptiPlex 5055 A Series",
    "Optiplex OptiPlex 5055 Ryzen APU", "Precision 5510", "Precision 5520",
    "Precision 5720 AIO", "Precision T3420", "Precision T3620",
    "XPS Notebook 9250", "XPS Notebook 9550",
}
LAPTOP_BRANDS = ("latitude", "xps", "laptop", "tablet", "rugged", "edu")


def _date(text: str) -> str | None:
    try:
        return dt.datetime.strptime(text.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


INDEX = "https://downloads.dell.com/catalog/CatalogIndexPC.cab"
# consumer brand prefixes (business lines come from CatalogPC in one file)
CONSUMER_PREFIXES = ("INS", "INSDT", "VOSNB", "VOSDT", "ANWNB", "ANWDT",
                     "XPSDT", "DL", "DD")
_GROUP = re.compile(
    r'<Brand key="\d+" prefix="([^"]*)">.*?'
    r'<ManifestInformation[^>]*path="([^"]+)"[^>]*>.*?'
    r'<Hash algorithm="SHA256">([0-9a-fA-F]{64})</Hash>', re.S)
MODEL_CAB_DIR_NAME = "modelcabs"


def _extract_cab(raw: bytes) -> str:
    import subprocess, tempfile, pathlib, shutil
    seven = shutil.which("7zz") or shutil.which("7z") or "7zz"
    with tempfile.TemporaryDirectory() as td:
        cab = pathlib.Path(td, "c.cab")
        cab.write_bytes(raw)
        subprocess.run([seven, "x", "-y", f"-o{td}", str(cab)],
                       capture_output=True, check=True)
        xml_path = next(pathlib.Path(td).glob("*.xml"))
        raw2 = xml_path.read_bytes()
        # Dell catalogs are UTF-16, HP's HPIA references are UTF-8 — sniff
        if raw2[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw2[:200]:
            return raw2.decode("utf-16", errors="replace")
        return raw2.decode("utf-8-sig", errors="replace")


def crawl(conn: sqlite3.Connection, client, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    text = _extract_cab(
        client.get(CATALOG, snapshot="CatalogPC.cab", timeout=300).content)
    stats = _ingest(conn, client, run_date, text, limit, log)

    # consumer lines: per-model cabs listed in CatalogIndexPC, sha256-
    # addressed (cached across runs; only changed model catalogs re-fetch)
    from .. import config as _config
    import pathlib
    cab_dir = _config.RAW_DIR / VENDOR / MODEL_CAB_DIR_NAME
    cab_dir.mkdir(parents=True, exist_ok=True)
    idx = _extract_cab(
        client.get(INDEX, snapshot="CatalogIndexPC.cab", timeout=300).content)
    groups = [(p_, path, sha.lower()) for p_, path, sha in _GROUP.findall(idx)
              if p_ in CONSUMER_PREFIXES]
    if limit:
        groups = groups[:limit]
    log(f"dell: {len(groups)} consumer model catalogs")
    n_fetched = 0
    for _pfx, path, sha in groups:
        cached = cab_dir / f"{sha}.cab"
        if cached.exists():
            raw2 = cached.read_bytes()
        else:
            try:
                raw2 = client.get(DL_BASE + path).content
            except Exception as exc:
                log(f"  dell: model cab failed {path[-40:]}: {str(exc)[:50]}")
                continue
            cached.write_bytes(raw2)
            n_fetched += 1
        try:
            mtext = _extract_cab(raw2)
        except Exception:
            continue
        st = _ingest(conn, client, run_date, mtext, None, lambda *a: None)
        stats = {k: stats[k] + st[k] for k in stats}
    log(f"dell total: {stats['boards']} systems, {stats['listings']} listings, "
        f"{stats['new_artefacts']} new ({n_fetched} model cabs fetched)")
    return stats


def _ingest(conn: sqlite3.Connection, client, run_date: str, text: str,
            limit, log) -> dict:
    raw = None  # (kept name-compatible with the original body below)
    # Era gate (Win11-only charter). Catalog DATES are useless for this:
    # Dell republishes 2012-era packages with fresh releaseDates (an E5420
    # BIOS 'A12' stamped 2021-07-27, a 2012 RST driver stamped 2026). The
    # honest signal is the BIOS VERSION SCHEME: legacy systems use letter
    # versions (A12), everything ~Skylake-onward uses numeric (1.x). Gating
    # per systemID also untangles Dell's name reuse (OptiPlex 7010 exists as
    # both a 2012 and a 2023 machine — only the numeric-BIOS ids survive).
    # ...and only the actual "System BIOS" package counts: legacy systems
    # carry TPM/EC firmware classed as BIOS-type with numeric versions,
    # which would whitelist a 2011 Latitude.
    modern_sids: set[str] = set()
    sys_oldest_drv: dict[str, str] = {}
    for block in _COMPONENT.findall(text):
        ct = _CTYPE.search(block)
        head = block[:block.find(">")]
        attrs = dict(_ATTR.findall(head))
        d = _date(attrs.get("releaseDate", "")) or (attrs.get("dateTime") or "")[:10]
        if ct and ct.group(1) == "DRVR" and d:
            for sid, _n in _SYS.findall(block):
                if d < sys_oldest_drv.get(sid, "9999"):
                    sys_oldest_drv[sid] = d
        if not ct or ct.group(1) != "BIOS":
            continue
        nm = _NAME.search(block)
        if not nm or "system bios" not in nm.group(1).lower():
            continue
        # numeric scheme AND a recent System BIOS: 2010-era desktops already
        # used numeric versions (Precision T1500 '2.4.0', 2012), and Dell
        # restamps old packages with fresh dates — each check catches the
        # other's blind spot.
        if re.match(r"\d+\.", attrs.get("vendorVersion") or "") \
                and (d or "") >= "2021-07":
            for sid, _n in _SYS.findall(block):
                modern_sids.add(sid)
    # third conjunct — launch-era floor: a machine's OLDEST driver package
    # dates its original stack (an Inspiron 7460's cab starts in 2016).
    # Restamps only push dates newer, so this floor can't be gamed upward
    # into a false negative; 2017-01 keeps late-2017 8th-gen launches.
    modern_sids = {sid for sid in modern_sids
                   if sys_oldest_drv.get(sid, "9999") >= "2017-01"}

    # board rows: merge config-variant systemIDs under one display name
    sys_to_board: dict[str, int] = {}
    boards_by_name: dict[str, int] = {}
    n_boards = 0
    for bm in _MODEL.finditer(text):
        brand, block = bm.group(1), bm.group(2)
        ptype = ("laptop" if any(k in brand.lower() for k in LAPTOP_BRANDS)
                 else "desktop")
        for sid, disp in _SYS.findall(block):
            if sid not in modern_sids:
                continue
            name = f"{brand} {disp}".replace("-", " ")
            name = re.sub(r"\s+", " ", name).strip()
            if name in DENY_NAMES:
                continue
            if name not in boards_by_name:
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                boards_by_name[name] = db.upsert_board(
                    conn, run_date, vendor=VENDOR, vendor_product_id=slug,
                    name=name, slug=slug, product_type=ptype,
                    support_url="https://www.dell.com/support/home/en-us"
                                f"/product-support/product/{disp.lower()}/drivers")
                n_boards += 1
            sys_to_board[sid] = boards_by_name[name]
    log(f"dell: {len(boards_by_name)} systems ({len(sys_to_board)} systemIDs)")

    comps = _COMPONENT.findall(text)
    if limit:
        comps = comps[:limit * 40]
    n_listings = n_new = 0
    for block in comps:
        head = block[:block.find(">")]
        attrs = dict(_ATTR.findall(head))
        ctype = _CTYPE.search(block)
        kind = KIND.get(ctype.group(1) if ctype else "", "utility")
        name_m = _NAME.search(block)
        cat_m = _CAT.search(block)
        ver = attrs.get("vendorVersion") or ""
        pv = versions.parse(ver)
        date = _date(attrs.get("releaseDate", "")) or \
            (attrs.get("dateTime") or "")[:10] or None
        board_ids = {sys_to_board[sid] for sid, _ in _SYS.findall(block)
                     if sid in sys_to_board and sid in modern_sids}
        if not board_ids:
            continue
        artefact_id, is_new = db.upsert_artefact(
            conn, run_date, vendor=VENDOR,
            vendor_artefact_id=attrs.get("releaseID") or attrs["packageID"],
            kind=kind,
            component_hint=(cat_m.group(1).strip() if cat_m else None),
            version_raw=ver or None,
            version_normalised=pv.normalised_json,
            release_date=date,
            file_size=int(attrs["size"]) if attrs.get("size", "").isdigit() else None,
            url=DL_BASE + attrs.get("path", ""),
            md5=(attrs.get("hashMD5") or "").lower() or None,
            os_raw="Win11 64",
            description_text=(name_m.group(1).strip() if name_m else None),
        )
        for bid in board_ids:
            db.link_board_artefact(conn, run_date, bid, artefact_id, date)
            n_listings += 1
        n_new += is_new
    conn.commit()
    log(f"dell: {n_boards} systems, {n_listings} listings, {n_new} new artefacts")
    return {"boards": n_boards, "listings": n_listings, "new_artefacts": n_new}
