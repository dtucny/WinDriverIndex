"""HP crawler (roadmap v0.3) — commercial platforms via HPIA reference files.

HP Image Assistant's cloud catalog (hpia.hpcloud.hp.com) is the same source
HP's CMSL PowerShell library consumes: ref/platformList.cab enumerates every
commercial platform (SystemID + product names + per-OS support flags), and
ref/{sid}/{sid}_64_11.0.{release}.cab is one XML per platform/OS holding all
softpaqs with real versions, ISO dates, categories, silicon vendor, URL, and
SHA256. Era gate = HP's own IsWindows11 flag; the release id in the cab URL
must be LOWERCASE (…_64_11.0.22h2.cab — 22H2 404s).

Consumer lines (Pavilion/OMEN/Victus/Envy/Spectre) are NOT in this catalog —
HP Support Assistant has no public equivalent; the Legion/Dell-consumer story
again. Commercial only: EliteBook/ProBook/ZBook/EliteDesk/ProDesk/Z/ZHAN.

Softpaq ids (spNNNNNN) are globally stable, so cross-platform dedupe is free.
A platform carries several ProductNames (840/846/850 G5 share 83b2); each
name becomes a board sharing the platform's artefacts, mirroring how Dell
merges config-variant systemIDs — in reverse.
"""

from __future__ import annotations

import re
import sqlite3

from .. import db, versions

VENDOR = "hp"
BROWSER_HEADERS = True
BASE = "https://hpia.hpcloud.hp.com/ref"

_PLATFORM = re.compile(r"<Platform [^>]*>(.*?)</Platform>", re.S)
_SYSID = re.compile(r"<SystemID>([0-9A-Fa-f]{4})</SystemID>")
_PRODNAME = re.compile(r"<ProductName[^>]*>([^<]+)</ProductName>")
_OS = re.compile(r"<OS [^>]*>(.*?)</OS>", re.S)
_RELEASE = re.compile(r"<OSReleaseIdFilename>([^<]+)</OSReleaseIdFilename>")
_UPDATE = re.compile(r"<UpdateInfo ColId=.*?</UpdateInfo>", re.S)

LAPTOP_WORDS = ("notebook", "mobile", "laptop", "book")

# Category prefix → artefact kind; everything else (Software/Utility/Dock/
# Diagnostics/Manageability) is out of scope for the driver index.
KINDS = (("Driver", "driver"), ("BIOS", "bios"), ("Firmware", "firmware"))


def _field(block: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>([^<]*)</{tag}>", block)
    return m.group(1).strip() or None if m else None


def _release_key(rel: str):
    m = re.match(r"(\d{2})H(\d)", rel)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def crawl(conn: sqlite3.Connection, client, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    from .dell import _extract_cab
    listing = _extract_cab(
        client.get(f"{BASE}/platformList.cab", snapshot="platformList.cab",
                   timeout=180).content)

    plats = []
    for block in _PLATFORM.findall(listing):
        sid_m = _SYSID.search(block)
        if not sid_m:
            continue
        win11 = [_RELEASE.search(o).group(1)
                 for o in _OS.findall(block)
                 if "<IsWindows11>true" in o and _RELEASE.search(o)]
        if not win11:
            continue
        rel = max(win11, key=_release_key)
        plats.append((sid_m.group(1).lower(), rel, _PRODNAME.findall(block)))
    log(f"hp: {len(plats)} Win11-capable platforms")
    if limit:
        plats = plats[:limit]

    n_boards = n_listings = n_new = 0
    for sid, rel, names in plats:
        try:
            xml = _extract_cab(client.get(
                f"{BASE}/{sid}/{sid}_64_11.0.{rel.lower()}.cab",
                snapshot=f"ref_{sid}.cab", timeout=300).content)
        except Exception as exc:
            log(f"  hp: ref miss {sid} ({names[0][:30]}): {str(exc)[:50]}")
            continue

        board_ids = []
        for name in names:
            name = re.sub(r"\s+", " ", name).strip()
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            ptype = ("laptop" if any(w in name.lower() for w in LAPTOP_WORDS)
                     else "desktop")
            board_ids.append(db.upsert_board(
                conn, run_date, vendor=VENDOR, vendor_product_id=f"{sid}/{slug}",
                name=name, slug=slug, product_type=ptype,
                support_url="https://support.hp.com/us-en/search?q="
                            + name.replace(" ", "+")))
            n_boards += 1

        for block in _UPDATE.findall(xml):
            cat = _field(block, "Category") or ""
            kind = next((k for pfx, k in KINDS if cat.startswith(pfx)), None)
            if not kind:
                continue
            spid = _field(block, "Id")
            ver = _field(block, "Version") or ""
            if not spid:
                continue
            url = _field(block, "Url")
            pv = versions.parse(ver)
            artefact_id, is_new = db.upsert_artefact(
                conn, run_date, vendor=VENDOR,
                vendor_artefact_id=spid,
                kind=kind,
                component_hint=cat,
                version_raw=ver or None,
                version_normalised=pv.normalised_json,
                release_date=_field(block, "DateReleased"),
                file_size=int(sz) if (sz := _field(block, "Size")) and sz.isdigit() else None,
                url=("https://" + url) if url and not url.startswith("http") else url,
                md5=(_field(block, "MD5") or "").lower() or None,
                sha256=(_field(block, "SHA256") or "").lower() or None,
                os_raw="Win11 64",
                is_beta=int(pv.is_beta),
                description_text=" — ".join(
                    x for x in (_field(block, "Name"), _field(block, "Vendor"))
                    if x),
            )
            date = _field(block, "DateReleased")
            for bid in board_ids:
                db.link_board_artefact(conn, run_date, bid, artefact_id, date)
                n_listings += 1
            n_new += is_new
        conn.commit()
    log(f"hp: {n_boards} systems, {n_listings} listings, {n_new} new artefacts")
    return {"boards": n_boards, "listings": n_listings, "new_artefacts": n_new}
