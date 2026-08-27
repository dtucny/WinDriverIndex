"""Lenovo crawler (roadmap v0.2 §2) — Think lines via public catalogs.

Three public, auth-free, honest-UA layers (the same data Lenovo System
Update / Vantage / Legion Toolkit consume):

1. Model→machine-type enumeration, two sources:
   - https://download.lenovo.com/cdrt/td/catalogv2.xml — commercial Think
     lines, plus a per-model BIOS entry (version/date/sha256).
   - pcsupport's MSE product tree
     (api/v4/mse/getproducts?productId=laptops-and-netbooks and
     desktops-and-all-in-ones) — entry ids embed the machine type
     (…/LEGION-5-15ACH6/82JW/…), which is how the consumer Legion/LOQ lines
     (LLT's original target) are enumerated without a local machine.
2. https://download.lenovo.com/catalog/{mt}_win11.xml — package list for one
   machine type: descriptor URL + category + sha256 OF THE DESCRIPTOR.
3. The package descriptor XML — version, title, release date.
4. Enrichment: pcsupport's downloads/drivers API (needs a one-page session
   warm-up, then JSON per product path) carries what the SU catalog hides —
   per-silicon combo version strings ("8852BE_6001.x,MT7921_RZ616_25.40.x")
   and silicon-suffixed versions ("6.0.9464.1_Fortemedia"). Multi-token
   combos are split into one artefact per silicon so each lands in its real
   family.

The descriptor sha256 in layer 2 makes descriptors content-addressed: they
are cached under data/raw/lenovo/descriptors/{sha}.xml across runs, so a
weekly refresh costs ~400 catalog fetches plus only the descriptors that
actually changed. Models sharing a catalog are keyed by their FIRST machine
type (sibling MTs ship identical package sets).

Boards get product_type laptop (ThinkPad) or desktop (ThinkCentre/
ThinkStation); no chipset/socket. Artefact identity is the Lenovo package id.
"""

from __future__ import annotations

import re
import sqlite3

from .. import config, db, versions

VENDOR = "lenovo"
CATALOG = "https://download.lenovo.com/cdrt/td/catalogv2.xml"
MT_CATALOG = "https://download.lenovo.com/catalog/{mt}_win11.xml"
DESC_DIR = config.RAW_DIR / "lenovo" / "descriptors"

_MODEL = re.compile(r'<Model name="([^"]+)"[^>]*>(.*?)</Model>', re.S)
_TYPE = re.compile(r"<Type>([^<]+)</Type>")
_BIOS = re.compile(r'<BIOS version="([^"]*)"[^>]*?date="([^"]*)"'
                   r'[^>]*?crc="([^"]*)"[^>]*>([^<]*)</BIOS>')
_PKG = re.compile(r"<package>\s*<location>([^<]+)</location>\s*"
                  r"<category>([^<]*)</category>.*?"
                  r'<checksum type="sha256">([0-9a-f]{64})</checksum>', re.S)
_PKG_ATTR = re.compile(r'<Package[^>]*\bid="([^"]+)"[^>]*\bversion="([^"]+)"')
_TITLE = re.compile(r"<Title[^>]*>.*?<Desc[^>]*>([^<]+)", re.S)
_DATE = re.compile(r"<ReleaseDate>([^<]+)</ReleaseDate>")

_UTIL = re.compile(r"vantage|software|utilit|diagnostic|dock|pen and keyboard",
                   re.IGNORECASE)


def make_client(run_date: str):
    from ..http import PoliteClient
    # tiny static XMLs on Lenovo's CDN — a gentler-than-default 0.5 s spacing
    # keeps the ~16k-file first run tractable; refreshes are content-addressed.
    return PoliteClient(VENDOR, run_date, min_interval=0.5)


PSUP_API = ("https://pcsupport.lenovo.com/us/en/api/v4/downloads/drivers"
            "?productId={path}")
PSUP_PAGE = "https://pcsupport.lenovo.com/us/en/products/{path}/downloads/driver-list"
_SUBVER = re.compile(r"(\d+(?:\.\d+){2,})")
_PSUP_HINT = [
    (re.compile(r"88\d\d|rtl|rts\d|realtek", re.I), "Realtek"),
    (re.compile(r"mt\d|rz\d|mediatek", re.I), "MediaTek"),
    (re.compile(r"n?v(i)?dia|geforce", re.I), "NVIDIA"),
    (re.compile(r"intel|ax2\d\d", re.I), "Intel"),
    (re.compile(r"amd", re.I), "AMD"),
]


def _psup_paths(client) -> dict[str, str]:
    """mt -> full product-tree path, from the cached MSE trees."""
    import json as _json
    out: dict[str, str] = {}
    for tree in ("laptops-and-netbooks", "desktops-and-all-in-ones"):
        try:
            data = _json.loads(client.get(
                MSE.format(tree=tree), snapshot=f"mse_{tree}.json").content)
        except Exception:
            continue
        for item in data:
            parts = item.get("Id", "").lower().split("/")
            if len(parts) >= 4:
                out.setdefault(parts[3], "/".join(parts[:4]))
    return out


def _psup_enrich(conn, run_date, board_id, mt, path, psup, log) -> int:
    """Split multi-silicon combo packages via the pcsupport listing."""
    import json as _json
    try:
        raw = psup.get(PSUP_API.format(path=path),
                       snapshot=f"psup_{mt}.json",
                       headers={"Referer": PSUP_PAGE.format(path=path)}).content
        data = _json.loads(raw)
    except Exception as exc:
        log(f"  lenovo: psup skipped {mt}: {str(exc)[:60]}")
        return 0

    def find(n):
        if isinstance(n, dict):
            if "DownloadItems" in n:
                return n["DownloadItems"]
            for v in n.values():
                r = find(v)
                if r is not None:
                    return r
        if isinstance(n, list):
            for v in n:
                r = find(v)
                if r is not None:
                    return r
    items = find(data) or []
    n = 0
    for it in items:
        title = it.get("Title") or ""
        files = it.get("Files") or []
        if not files:
            continue
        verstr = files[0].get("Version") or ""
        tokens = [t.strip() for t in re.split(r"[,;]", verstr) if t.strip()]
        subs = []
        for tok in tokens:
            m = None
            for m in _SUBVER.finditer(tok):
                pass
            if not m:
                continue
            prefix = (tok[:m.start()] + tok[m.end():]).strip(" _-")
            word = next((w for p, w in _PSUP_HINT if p.search(prefix)), "")
            subs.append((prefix, m.group(1), word))
        # only worth emitting when the combo actually splits, or a single
        # token carries a silicon suffix the SU descriptor lacks
        if len(subs) < 2 and not (len(subs) == 1 and subs[0][0]):
            continue
        cat = re.split(r"\s+Driver\b", title)[0].strip() or "component"
        date = None
        dd = files[0].get("Date")
        if isinstance(dd, dict) and dd.get("Unix"):
            import datetime as _dt
            date = _dt.datetime.fromtimestamp(int(dd["Unix"]) / 1000,
                                              _dt.UTC).date().isoformat()
        for i, (prefix, ver, word) in enumerate(subs):
            pv = versions.parse(ver)
            desc = " ".join(filter(None, (word, prefix, cat, "driver")))
            artefact_id, _new = db.upsert_artefact(
                conn, run_date, vendor=VENDOR,
                vendor_artefact_id=f"psup:{it.get('DocId')}#{i}",
                kind="driver", component_hint=cat,
                version_raw=ver, version_normalised=pv.normalised_json,
                release_date=date, url=PSUP_PAGE.format(path=path),
                os_raw="Win11 64", description_text=desc)
            db.link_board_artefact(conn, run_date, board_id, artefact_id, date)
            n += 1
    return n


def _product_type(name: str) -> str:
    return "laptop" if name.startswith("ThinkPad") else "desktop"


def crawl(conn: sqlite3.Connection, client, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    text = client.get(CATALOG, snapshot="catalogv2.xml").content.decode(
        "utf-8", "replace")
    models = []
    for name, block in _MODEL.findall(text):
        if "win11" not in block or not name.startswith(
                ("ThinkPad", "ThinkCentre", "ThinkStation")):
            continue
        types = _TYPE.findall(block)
        if types:
            models.append((name, types, _BIOS.search(block)))
    log(f"lenovo: {len(models)} win11-era Think models in catalog")
    if limit:
        models = models[:limit]
    DESC_DIR.mkdir(parents=True, exist_ok=True)

    legion = _legion_models(client, log)
    if limit:
        legion = legion[:limit]

    paths = _psup_paths(client)
    from ..http import PoliteClient
    psup = PoliteClient(VENDOR, run_date, browser_headers=True)
    psup_warm = [False]

    def enrich(board_id, mt):
        path = paths.get(mt)
        if not path:
            return 0
        if not psup_warm[0]:
            try:
                psup.get(PSUP_PAGE.format(path=path))   # cookie warm-up
            except Exception:
                pass
            psup_warm[0] = True
        return _psup_enrich(conn, run_date, board_id, mt, path, psup, log)

    n_boards = n_listings = n_new = 0
    for display, mt, ptype in legion:
        board_id = db.upsert_board(
            conn, run_date, vendor=VENDOR, vendor_product_id=mt,
            name=display, slug=mt, product_type=ptype,
            support_url=f"https://pcsupport.lenovo.com/products/{mt}")
        n_boards += 1
        li, ln = _crawl_mt(conn, client, run_date, board_id, mt, display, log)
        n_listings += li + enrich(board_id, mt)
        n_new += ln
        conn.commit()

    for name, types, bios_m in models:
        mt = types[0].lower()
        display = re.sub(r"\s+Type\s+.*$", "", name)
        board_id = db.upsert_board(
            conn, run_date, vendor=VENDOR, vendor_product_id=mt,
            name=display, slug=mt, product_type=_product_type(name),
            support_url=f"https://pcsupport.lenovo.com/products/{mt}")
        n_boards += 1
        if bios_m:
            ver, date, crc, url = bios_m.groups()
            _, is_new = db.upsert_artefact(
                conn, run_date, vendor=VENDOR,
                vendor_artefact_id=f"bios:{mt}:{ver}", kind="bios",
                version_raw=ver, release_date=date or None,
                sha256=crc.lower() if len(crc) == 64 else None,
                url=url.strip() or None,
                description_text=f"BIOS {ver} for {display}")
            db.link_board_artefact(conn, run_date, board_id, _, date or None)
            n_new += is_new
        li, ln = _crawl_mt(conn, client, run_date, board_id, mt, display, log)
        n_listings += li + enrich(board_id, mt)
        n_new += ln
        conn.commit()
    log(f"lenovo: {n_boards} machines, {n_listings} listings, {n_new} new artefacts")
    return {"boards": n_boards, "listings": n_listings, "new_artefacts": n_new}


def _descriptor(client, url: str, sha: str, log) -> dict | None:
    """Fetch (or reuse, content-addressed by the catalog's sha) one package
    descriptor and extract id/version/title/date."""
    path = DESC_DIR / f"{sha}.xml"
    if path.exists():
        body = path.read_text(encoding="utf-8", errors="replace")
    else:
        try:
            body = client.get(url).content.decode("utf-8", "replace")
        except Exception as exc:
            log(f"  lenovo: descriptor fetch failed {url[-40:]}: {str(exc)[:50]}")
            return None
        path.write_text(body, encoding="utf-8")
    m = _PKG_ATTR.search(body)
    if not m:
        return None
    t = _TITLE.search(body)
    d = _DATE.search(body)
    return {"id": m.group(1), "version": m.group(2),
            "title": re.sub(r"\s+", " ", t.group(1)).strip() if t else None,
            "date": (d.group(1)[:10] if d else None)}


MSE = ("https://pcsupport.lenovo.com/us/en/api/v4/mse/getproducts"
       "?productId={tree}")


def _pretty(slug: str) -> str:
    return " ".join(w if any(c.isdigit() for c in w) else w.capitalize()
                    for w in slug.replace("-", " ").lower().split())


def _legion_models(client, log) -> list[tuple[str, str, str]]:
    """Consumer Legion/LOQ (model display, MT, product_type) from the MSE
    product tree — the enumeration LLT gets from local WMI instead."""
    out, seen = [], set()
    for tree, ptype in (("laptops-and-netbooks", "laptop"),
                        ("desktops-and-all-in-ones", "desktop")):
        try:
            import json as _json
            data = _json.loads(client.get(
                MSE.format(tree=tree), snapshot=f"mse_{tree}.json").content)
        except Exception as exc:
            log(f"  lenovo: MSE tree {tree} failed: {str(exc)[:60]}")
            continue
        for item in data:
            parts = item.get("Id", "").split("/")
            if len(parts) < 4 or not any(k in parts[1].upper() or k in parts[2].upper()
                                         for k in ("LEGION", "LOQ")):
                continue
            model, mt = parts[2], parts[3].lower()
            # Win11-only charter scope: exclude pre-floor CPUs. Kaby/Skylake
            # codes (ikb/isk — Y520/Y720/Y920 era, incl. late 81xx refreshes)
            # sit below the Windows 11 support floor; a decade-old laptop
            # topping "most neglected" is a scope leak, not a finding.
            if re.search(r"i(kb|sk)", model.lower()):
                continue
            if mt in seen:
                continue
            seen.add(mt)
            out.append((f"{_pretty(model)} ({mt.upper()})", mt, ptype))
    log(f"lenovo: {len(out)} consumer Legion/LOQ machines enumerated")
    return sorted(out)


def _crawl_mt(conn, client, run_date, board_id, mt, display, log) -> tuple[int, int]:
    try:
        cat = client.get(MT_CATALOG.format(mt=mt),
                         snapshot=f"mt_{mt}.xml").content.decode("utf-8", "replace")
    except Exception as exc:
        log(f"  lenovo: no catalog for {display} ({mt}): {str(exc)[:60]}")
        return 0, 0
    n_listings = n_new = 0
    for loc, category, sha in _PKG.findall(cat):
        entry = _descriptor(client, loc, sha, log)
        if not entry:
            continue
        kind = ("bios" if "bios" in category.lower()
                else "utility" if _UTIL.search(category or "")
                else "driver")
        # NOTE: combo packages ('6001.x/6102.x/25.40.x') carry no silicon
        # names in the SU descriptor — the '(Realtek, Mediatek)' labels exist
        # only on the pcsupport website layer. Per-silicon splitting therefore
        # needs that API (roadmap); combos stay whole in category families.
        ver = versions.parse(entry["version"])
        artefact_id, is_new = db.upsert_artefact(
            conn, run_date, vendor=VENDOR,
            vendor_artefact_id=entry["id"], kind=kind,
            component_hint=category or None,
            version_raw=entry["version"],
            version_normalised=ver.normalised_json,
            release_date=entry["date"], url=loc,
            os_raw="Win11 64",
            description_text=entry["title"])
        db.link_board_artefact(conn, run_date, board_id, artefact_id,
                               entry["date"])
        n_listings += 1
        n_new += is_new
    return n_listings, n_new

