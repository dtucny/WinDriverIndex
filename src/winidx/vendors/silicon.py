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
    # Adrenalin from a representative Ryzen CPU page — the honest upstream
    # for the AMD iGPU family (board vendors list internal-scheme or even
    # date-string versions; AMD's marketing scheme is YY.M.P).
    ("AMD Graphics",
     "https://www.amd.com/en/support/downloads/drivers.html/processors/ryzen/"
     "ryzen-7000-series/amd-ryzen-7-7800x3d.html",
     r"Adrenalin\s+(\d+\.\d+\.\d+)"),
    ("Intel Chipset INF",
     "https://www.intel.com/content/www/us/en/download/19347/chipset-inf-utility.html",
     r"[Vv]ersion[^0-9]{0,20}(\d+(?:\.\d+){3})"),
    ("Intel Wi-Fi",
     "https://www.intel.com/content/www/us/en/download/19351/"
     "intel-wireless-wi-fi-drivers-for-windows-10-and-windows-11.html",
     r"[Vv]ersion[^0-9]{0,20}(\d+(?:\.\d+){2,3})"),
    ("NVIDIA Graphics",
     "https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/"
     "AjaxDriverService.php?func=DriverManualLookup&psid=131&pfid=1067"
     "&osID=135&languageCode=1033&dch=1&numberOfResults=1",
     r'"Version"\s*:\s*"(\d+\.\d+)"'),
    ("Intel VGA",
     "https://www.intel.com/content/www/us/en/download/785597/"
     "intel-arc-iris-xe-graphics-windows.html",
     r"[Vv]ersion[^0-9]{0,20}(\d+\.\d+\.\d+\.\d+)"),
    ("Intel Bluetooth",
     "https://www.intel.com/content/www/us/en/download/18649/"
     "intel-wireless-bluetooth-for-windows-10-and-windows-11.html",
     r"[Vv]ersion[^0-9]{0,20}(\d+(?:\.\d+){2,3})"),
]

_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def crawl(conn: sqlite3.Connection, client, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    pages = PAGES[:limit] if limit else PAGES
    return _crawl_pages(conn, client, run_date, pages, log)


_RN_HREF = re.compile(r'href="(/en/resources/support-articles/release-notes/'
                      r'[^"]+)"')
_STORE_VER = re.compile(r"Windows Driver Store Version\s*"
                        r"(3\d\.0\.\d{4,5}\.\d+)")


def _amd_store_version(client, drivers_body: str, log) -> str | None:
    """INF-scheme version from the release notes linked on the drivers page.
    A notes page can carry several editions (Adrenalin + PRO); the max is
    the current consumer branch."""
    href = _RN_HREF.search(drivers_body)
    if not href:
        log("  silicon: AMD release-notes link not found — keeping marketing version")
        return None
    try:
        body = client.get("https://www.amd.com" + href.group(1),
                          snapshot="si_amd_graphics_rn.html"
                          ).content.decode("utf-8", "replace")
    except Exception as exc:
        log(f"  silicon: AMD release notes fetch failed — {str(exc)[:60]}")
        return None
    hits = [m.group(1) for m in _STORE_VER.finditer(body)
            if versions.parse(m.group(1)).tuple]
    return max(hits, key=lambda v: versions.compare_key(versions.parse(v))) \
        if hits else None


def _crawl_pages(conn, client, run_date, pages, log):
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
        # best-effort release date near the version match: ISO first (AMD),
        # then US format (Intel), within a generous window
        window = body[m.end():m.end() + 3000]
        iso = re.search(r"(\d{4}-\d{2}-\d{2})", window)
        mon = re.search(r"([A-Z][a-z]{2})[a-z]*\s+(\d{1,2}),\s*(\d{4})", window)
        if mon and not iso:
            import datetime as _dt
            try:
                iso = None
                date_mon = _dt.datetime.strptime(
                    f"{mon.group(1)} {mon.group(2)} {mon.group(3)}",
                    "%b %d %Y").date().isoformat()
            except ValueError:
                date_mon = None
        else:
            date_mon = None
        dm = _DATE.search(window)
        if iso and (not dm or iso.start() < dm.start()):
            date = iso.group(1)
        elif date_mon:
            date = date_mon
        else:
            date = (f"{dm.group(3)}-{int(dm.group(1)):02d}-{int(dm.group(2)):02d}"
                    if dm else None)
        desc = f"Silicon-vendor download page: {family}"
        if family == "AMD Graphics":
            # AMD's marketing string (26.8.1) can't be ordered against the
            # INF-scheme versions every board vendor lists, and the mapping
            # is a lookup table, not an algorithm (31.0.24002.92 = Adrenalin
            # 23.40.02). The linked release notes publish the INF form —
            # 'Windows Driver Store Version 32.0.31041.1004' — so record
            # THAT as the version and keep the marketing name in the text.
            store = _amd_store_version(client, body, log)
            if store:
                desc = f"Adrenalin {ver} — {desc}"
                ver = store
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
            description_text=desc,
            url=url,
        )
        n += 1
        n_new += is_new
        log(f"  silicon: {family:20} -> {ver} ({date or 'no date'})")
        conn.commit()
    log(f"silicon: {n} families matched, {n_new} new")
    return {"boards": 0, "listings": n, "new_artefacts": n_new}
