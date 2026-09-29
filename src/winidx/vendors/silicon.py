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
from ..families import inf_hwids_by_name

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
    # package version is NN.YY.mdd.build; the loose shape must not match the
    # Realtek-scheme adapter drivers also on the page (1168.28.50.1224)
    ("Killer Suite",
     "https://www.intel.com/content/www/us/en/download/19779/"
     "intel-killer-performance-suite.html",
     r"\b(\d{2}\.\d{2}\.\d{3}\.\d{4})\b"),
]

_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def crawl(conn: sqlite3.Connection, client, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    pages = PAGES[:limit] if limit else PAGES
    out = _crawl_pages(conn, client, run_date, pages, log)
    _amd_chipset_components(conn, client, run_date, log)
    _realtek_lan(conn, client, run_date, log)
    return out


# AMD's chipset release notes list every driver in the package with its
# Windows 11 version; this maps those names to the INF each installs. The
# package itself is a runtime-unpacked installer with no static INFs, so these
# versions reach infs.json only this way. Extra HWIDs are for INFs no indexed
# package carries (observed on real machines: the SMBIOS-reported instance IDs).
_AMD_CHIPSET_RN = re.compile(
    r'href="((?:https://www\.amd\.com)?/en/resources/support-articles/'
    r'release-notes/RN-RYZEN-CHIPSET-[^"]+)"')
AMD_CHIPSET_INFS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    (r"AMD PCI Device Driver", ("amdpcidev.inf",), ()),
    (r"AMD I2C Driver", ("amdi2c.inf",), ()),
    (r"AMD UART Driver", ("amduart.inf",), ()),
    (r"AMD GPIO2 Driver", ("amdgpio2.inf",), ()),
    (r"PT GPIO Driver", ("amdgpio3.inf",), ("ACPI\\AMDIF031",)),
    (r"AMD PSP Driver", ("amdpsp.inf",), ()),
    (r"AMD IOV Driver", ("amdiov.inf",), ()),
    (r"AMD SMBUS Driver", ("smbusamd.inf",), ()),
    (r"AMD SFH I2C Driver", ("amdsfhkmdfi2c.inf", "amdsfhspbi2c.inf"), ()),
    (r"AMD SFH1\.1 Driver", ("amdsfhkmdf.inf", "amdsfhumdf.inf"), ()),
    (r"AMD MicroPEP Driver", ("amdmicropep.inf",), ()),
    (r"AMD Wireless Button Driver", ("amdwirelessbutton.inf",), ()),
    (r"AMD Interface Driver", ("amdinterface.inf",), ()),
    (r"AMD PPM Provisioning File Driver", ("amdppkg.inf",), ("ACPI\\AMDI0052",)),
    (r"AMD 3D V-Cache Performance Optimizer", ("amd3dvcache.inf",), ("ACPI\\AMDI0101",)),
    (r"AMD Application Compatibility Database", ("amdappcompat.inf",), ("ACPI\\AMDI0204",)),
]


def _rn_rows(body: str) -> list[list[str]]:
    import html as _html
    rows = []
    for tr in re.findall(r"<tr.*?</tr>", body, re.S):
        cells = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        rows.append(cells)
    return rows


# Realtek's own PCIe Ethernet page lists its retail Windows 11 NetAdapterCx
# package as '11.031.50'. Each chip's INF in that package carries the chip's
# prefix instead of 11 (rt25cx21: 1125.31.50.x, rt68cx21: 1168.31.50.x; Windows
# Update's 8127 build is 1127.31.50.603), which is the scheme the family water
# uses, so the page gives each family 'prefix.31.50'. The build (fourth) part
# isn't shown and the files sit behind a CAPTCHA, so this is a version prefix,
# never an infs.json row. The NDIS package (10.80.50, rt640x64.inf) likewise
# lacks its build and is not recorded.
REALTEK_LAN_URL = "https://www.realtek.com/Download/List?cate_id=584"
REALTEK_LAN_FAMILIES = {"Realtek 8168 LAN": "1168", "Realtek 8125 LAN": "1125",
                        "Realtek 8126 LAN": "1126"}
_RTK_NETADAPTER = re.compile(r"^Win11 Auto Installation Program \(NetAdapterCx\)$")


def parse_realtek_lan(body: str) -> tuple[str, str] | None:
    """(package version, ISO date) of the Win11 NetAdapterCx package with
    power saving (the '- Not Support Power Saving' build is a variant)."""
    for cells in _rn_rows(body):
        cells = [c for c in cells if c]
        if len(cells) >= 3 and _RTK_NETADAPTER.match(cells[0]) \
                and re.fullmatch(r"\d+\.\d+\.\d+", cells[1]):
            m = re.fullmatch(r"(\d{4})/(\d{2})/(\d{2})", cells[2])
            return cells[1], (f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None)
    return None


def _realtek_lan(conn, client, run_date, log) -> int:
    try:
        body = client.get(REALTEK_LAN_URL, snapshot="si_realtek_lan.html"
                          ).content.decode("utf-8", "replace")
    except Exception as exc:
        log(f"  silicon MISS Realtek LAN: fetch failed — {str(exc)[:80]}")
        return 0
    found = parse_realtek_lan(body)
    if not found:
        log("  silicon MISS Realtek LAN: NetAdapterCx row not found (layout changed?)")
        return 0
    pkg, date = found
    major, minor, rev = pkg.split(".")
    if major != "11":
        log(f"  silicon MISS Realtek LAN: unexpected package scheme {pkg}")
        return 0
    n = 0
    for family, prefix in REALTEK_LAN_FAMILIES.items():
        fam = conn.execute("SELECT family_id FROM family WHERE name = ?",
                           (family,)).fetchone()
        if not fam:
            continue
        ver = f"{prefix}.{int(minor)}.{int(rev)}"
        db.upsert_artefact(
            conn, run_date, vendor=VENDOR, vendor_artefact_id=family,
            kind="driver", family_id=fam["family_id"], source_type="upstream",
            version_raw=ver, version_normalised=versions.parse(ver).normalised_json,
            release_date=date, os_raw="Win11 64",
            description_text=f"Realtek NetAdapterCx {pkg} — Silicon-vendor download page",
            url=REALTEK_LAN_URL)
        n += 1
        log(f"  silicon: {family:20} -> {ver} ({date or 'no date'})")
    conn.commit()
    return n


def parse_chipset_rn(body: str) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """(component title, Windows 11 version, INF name, extra HWIDs) from the
    release notes' driver table (header row: '', Windows 10, Windows 11, ...).
    'Not Applicable' and unmapped components are skipped."""
    out = []
    win11 = None
    for cells in _rn_rows(body):
        if "Windows 11" in cells:
            win11 = cells.index("Windows 11")
            continue
        if win11 is None or len(cells) <= win11 or not versions.parse(cells[win11]).tuple:
            continue
        for pattern, infs, extra in AMD_CHIPSET_INFS:
            if re.match(pattern, cells[0], re.I):
                out += [(cells[0], cells[win11], inf, extra) for inf in infs]
                break
    return out


def _amd_chipset_components(conn, client, run_date, log) -> int:
    snap = conn.execute("SELECT 1 FROM family WHERE name = 'AMD Chipset'").fetchone()
    if not snap:
        return 0
    try:
        page = client.get(next(u for f, u, _ in PAGES if f == "AMD Chipset"),
                          snapshot="si_amd_chipset.html").content.decode("utf-8", "replace")
        href = _AMD_CHIPSET_RN.search(page)
        if not href:
            log("  silicon: AMD chipset release-notes link not found")
            return 0
        url = href.group(1)
        url = url if url.startswith("http") else "https://www.amd.com" + url
        body = client.get(url, snapshot="si_amd_chipset_rn.html"
                          ).content.decode("utf-8", "replace")
    except Exception as exc:
        log(f"  silicon: AMD chipset release notes failed — {str(exc)[:60]}")
        return 0
    # the article's JSON-LD carries its publish date
    m = re.search(r'"datePublished"\s*:\s*"(\d{4}-\d{2}-\d{2})', body)
    date = m.group(1) if m else None
    n = 0
    known = inf_hwids_by_name(conn)
    for title, ver, inf, extra in parse_chipset_rn(body):
        db.upsert_upstream_inf(
            conn, run_date, source="amd-release-notes", inf_name=inf,
            driver_ver=ver, published=date,
            hwids=sorted(known.get(inf, set()) | set(extra)), title=title, url=url)
        n += 1
    pruned = db.prune_upstream_inf(conn, run_date, "amd-release-notes")
    log(f"  silicon: AMD chipset components -> {n} INF versions"
        + (f", pruned {pruned}" if pruned else ""))
    if n == 0:
        log("  silicon MISS AMD chipset components: table not parsed (layout changed?)")
    return n


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
