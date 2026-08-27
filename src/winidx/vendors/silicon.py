"""Silicon-vendor download pages as upstream reference sources (roadmap §3).

Complements the WU Catalog with the authoritative "what the silicon vendor
itself ships" for the biggest families. Each entry is one public download
page and a version regex; metadata only, `source_type='upstream'`, same
isolation rules as wucatalog (never in vendor-lag / fetch / rule assignment).

Verified 2026-08-27: all pages return 200 to Chrome-impersonated requests and
carry extractable versions — and each was already ahead of every board
vendor (AMD chipset 8.08.12.551 vs best-listed 8.03.25.247; Intel chipset INF
10.1.20658.8883; Intel Wi-Fi 24.60.0.3). Pages are regex-fragile by nature: a
miss is logged loudly but never fails the crawl.
"""

from __future__ import annotations

import re
import sqlite3

from .. import db, versions

VENDOR = "silicon"
BROWSER_HEADERS = True   # amd.com / intel.com want full browser identity

# (family name, page url, version regex — group 1 is the version)
PAGES: list[tuple[str, str, str]] = [
    ("AMD Chipset",
     "https://www.amd.com/en/support/downloads/drivers.html/chipsets/am5/x670.html",
     r"Chipset_Software_(\d+(?:\.\d+)+)"),
    ("Intel Chipset INF",
     "https://www.intel.com/content/www/us/en/download/19347/chipset-inf-utility.html",
     r"[Vv]ersion[^0-9]{0,20}(\d+(?:\.\d+){3})"),
    ("Intel Wi-Fi",
     "https://www.intel.com/content/www/us/en/download/19351/"
     "intel-wireless-wi-fi-drivers-for-windows-10-and-windows-11.html",
     r"[Vv]ersion[^0-9]{0,20}(\d+(?:\.\d+){2,3})"),
    ("Intel Bluetooth",
     "https://www.intel.com/content/www/us/en/download/18649/"
     "intel-wireless-bluetooth-for-windows-10-and-windows-11.html",
     r"[Vv]ersion[^0-9]{0,20}(\d+(?:\.\d+){2,3})"),
]

_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def crawl(conn: sqlite3.Connection, client, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    pages = PAGES[:limit] if limit else PAGES
    n = n_new = 0
    for family, url, pattern in pages:
        fam = conn.execute("SELECT family_id FROM family WHERE name = ?",
                           (family,)).fetchone()
        if not fam:
            log(f"  silicon: family {family!r} not in DB — skipping")
            continue
        snap = "si_" + re.sub(r"[^a-z0-9]+", "_", family.lower()) + ".html"
        try:
            body = client.get(url, snapshot=snap).content.decode("utf-8", "replace")
        except Exception as exc:
            log(f"  silicon MISS {family}: fetch failed — {str(exc)[:80]}")
            continue
        matches = [m for m in re.finditer(pattern, body)
                   if versions.parse(m.group(1)).tuple]
        if not matches:
            log(f"  silicon MISS {family}: version pattern found nothing "
                f"(page layout changed?)")
            continue
        # a page can carry both '24.60.0' and '24.60.0.3' — take the max
        m = max(matches, key=lambda x: versions.compare_key(versions.parse(x.group(1))))
        ver = m.group(1)
        # best-effort release date: first US-format date within 300 chars
        # after the version match
        dm = _DATE.search(body[m.end():m.end() + 300])
        date = (f"{dm.group(3)}-{int(dm.group(1)):02d}-{int(dm.group(2)):02d}"
                if dm else None)
        _, is_new = db.upsert_artefact(
            conn, run_date, vendor=VENDOR,
            vendor_artefact_id=family,
            kind="driver",
            family_id=fam["family_id"],
            source_type="upstream",
            version_raw=ver,
            version_normalised=versions.parse(ver).normalised_json,
            release_date=date,
            os_raw="Win11 64",
            description_text=f"Silicon-vendor download page: {family}",
            url=url,
        )
        n += 1
        n_new += is_new
        log(f"  silicon: {family:20} -> {ver} ({date or 'no date'})")
        conn.commit()
    log(f"silicon: {n} families matched, {n_new} new")
    return {"boards": 0, "listings": n, "new_artefacts": n_new}
