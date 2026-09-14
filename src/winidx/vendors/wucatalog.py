"""Windows Update Catalog as an upstream reference source (roadmap v0.2 §1).

Not a board vendor: it contributes `source_type='upstream'` artefacts that
raise each family's water level but never enter vendor-lag (they have no
board links, and publish excludes upstream from lag by source_type).

Mechanism: catalog.update.microsoft.com/Search.aspx?q={HWID} is queryable by
bare VEN/DEV (or VID/PID) hardware id and returns server-rendered rows with
an explicit Version column, product (OS), classification, and date — no title
parsing, no auth, honest User-Agent accepted (verified 2026-08-27). Only the
first result page (25 rows) is read per HWID; rows are near-duplicates across
Windows builds, so the max parsed version among Windows 11 rows is taken.

Caveat carried into the published output: WU coverage is incomplete in both
directions (spec §1) — e.g. MediaTek 6E sits at 3.5.x on WU while ASUS lists
3.6.0.1425, and Realtek 8125 sits newer on WU than any board vendor. The
water level is the max over all sources; disagreement is signal, not error.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

from .. import db, versions

VENDOR = "wucatalog"
SEARCH = "https://www.catalog.update.microsoft.com/Search.aspx"

# Queryable HWID shapes, most→least specific silicon identity.
_PCI = re.compile(r"^(PCI\\VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4})")
_USB = re.compile(r"^(USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4})")
_HDA = re.compile(r"^(HDAUDIO\\FUNC_01&VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4})")

_ROW = re.compile(r"<tr id=\"[0-9a-f-]{36}_R\d+\".*?</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TAG = re.compile(r"<[^>]+>")

MAX_QUERIES_PER_FAMILY = 3

# PCI vendor ids per silicon vendor: prefixes from other vendors' silicon are
# bundle contamination (a Killer package carries Intel Wi-Fi INFs) and must
# not drive the query.
_VEN_IDS = {
    # PCI VEN and USB VID both: Bluetooth silicon enumerates over USB under
    # a different id (Intel 8087, Realtek 0BDA) — without these the vendor
    # filter dropped every USB prefix and left only the combo card's Wi-Fi ids.
    "amd": {"1022", "1002"}, "intel": {"8086", "8087"}, "realtek": {"10EC", "0BDA"},
    "mediatek": {"14C3", "0E8D"}, "qualcomm": {"17CB", "0CF3"},
    "aquantia": {"1D6A", "1B4B"}, "asmedia": {"1B21"}, "aspeed": {"1A03"},
}

# Families whose HWID sets are known-contaminated by bundling: WU queries for
# them return a different component's driver, so they get no upstream row.
BLOCKLIST = {"Killer LAN", "Killer Wi-Fi", "Killer Bluetooth", "Killer Suite"}

# WU titles carry the setup class ("Realtek Semiconductor Corp. Net Driver
# Update"). Combo packages (Wi-Fi + Bluetooth) leave PCI Wi-Fi ids in
# Bluetooth families, so the query can hit the wrong component's driver:
# 2026-09 Realtek Bluetooth picked PCI\VEN_10EC&DEV_B851 and took a Net
# driver's 6101.x as its water. Reject rows whose class word is known and
# not among the family component's; unknown words (e.g. "ASPEED Driver
# Update") pass.
_TITLE_CLASS = re.compile(r"\b([A-Za-z]+) Driver Update\b")
_KNOWN_CLASSES = {"net", "bluetooth", "media", "display", "system", "scsiadapter",
                  "hdc", "extension", "computeaccelerator", "usb", "firmware",
                  "softwarecomponent", "securitydevices", "monitor", "image"}
_COMPONENT_CLASSES = {
    "bluetooth": {"bluetooth"}, "wlan": {"net"}, "lan": {"net"},
    # Intel SST's DSP bus driver and Intel GNA both ship as class System.
    "audio": {"media", "system"}, "graphics": {"display", "extension"},
    "chipset": {"system", "extension"}, "storage": {"scsiadapter", "hdc", "system"},
    "raid": {"scsiadapter", "hdc"}, "npu": {"computeaccelerator", "system"},
}
# Bluetooth silicon enumerates over USB; PCI ids in a Bluetooth family are
# the Wi-Fi half of a combo card and must not drive the query.
_COMPONENT_BUSES = {"bluetooth": ("USB",)}


def _class_ok(title: str, component: str | None) -> bool:
    allowed = _COMPONENT_CLASSES.get(component or "")
    m = _TITLE_CLASS.search(title)
    if not allowed or not m:
        return True
    word = m.group(1).lower()
    return word not in _KNOWN_CLASSES or word in allowed


def crawl(conn: sqlite3.Connection, client, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    fams = [f for f in conn.execute(
        "SELECT family_id, name, silicon_vendor, component, hwids FROM family"
        " WHERE hwids != '[]' AND name NOT LIKE '%(preinstall)%'")
        if f["name"] not in BLOCKLIST]
    if limit:
        fams = fams[:limit]
    # newest INF driver_date claiming each hwid: query selection prefers the
    # devices CURRENT drivers support, not the devices with the most SUBSYS
    # variants (popularity picked decade-old silicon for AMD Graphics and
    # missed the modern iGPU/dGPU ids entirely)
    hw_date: dict[str, str] = {}
    for r in conn.execute(
            "SELECT driver_date, hwids FROM inf WHERE driver_date IS NOT NULL"):
        for h in json.loads(r["hwids"]):
            hu = h.upper()
            if r["driver_date"] > hw_date.get(hu, ""):
                hw_date[hu] = r["driver_date"]

    n_fam = n_new = n_queries = 0
    for fam in fams:
        prefixes = _representative_prefixes(json.loads(fam["hwids"]),
                                            fam["silicon_vendor"], hw_date,
                                            buses=_COMPONENT_BUSES.get(fam["component"]))
        found = []
        for prefix in prefixes:
            n_queries += 1
            snap = "wu_" + re.sub(r"[^A-Za-z0-9]+", "_", prefix) + ".html"
            resp = client.get(SEARCH, params={"q": prefix}, snapshot=snap)
            rows = _parse_rows(resp.content.decode("utf-8", "replace"), prefix)
            kept = [r for r in rows if _class_ok(r["title"], fam["component"])]
            if len(kept) < len(rows):
                log(f"  wu: {fam['name'][:30]:30} dropped {len(rows) - len(kept)}"
                    f" off-class rows for {prefix}")
            found += kept
        if not found:
            continue
        best = max(found, key=lambda f: versions.compare_key(versions.parse(f["version"])))
        _, is_new = db.upsert_artefact(
            conn, run_date, vendor=VENDOR,
            vendor_artefact_id=f"{fam['family_id']}",
            kind="driver",
            family_id=fam["family_id"],
            source_type="upstream",
            version_raw=best["version"],
            version_normalised=versions.parse(best["version"]).normalised_json,
            release_date=best["date"],
            os_raw="Win11 64",
            description_text=best["title"][:200],
            url="https://www.catalog.update.microsoft.com/Search.aspx?q="
                + best["hwid"].replace("\\", "%5C").replace("&", "%26"),
        )
        n_fam += 1
        n_new += is_new
        log(f"  wu: {fam['name'][:30]:30} -> {best['version']} ({best['date']})")
        conn.commit()
    # A family that produced no acceptable row this run must not keep last
    # cycle's upstream version (Intel ME carried a Wi-Fi 24.40.0.4 for weeks).
    # Upstream rows have no board links, so pruning them is safe.
    stale = conn.execute(
        "DELETE FROM artefact WHERE vendor = ? AND last_seen < ?",
        (VENDOR, run_date)).rowcount
    conn.commit()
    if stale:
        log(f"wucatalog: pruned {stale} stale upstream rows")
    log(f"wucatalog: {n_fam} families matched, {n_new} new, {n_queries} queries")
    return {"boards": 0, "listings": n_fam, "new_artefacts": n_new}


def _representative_prefixes(hwids: list[str], silicon_vendor: str,
                             hw_date: dict[str, str] | None = None,
                             buses: tuple[str, ...] | None = None) -> list[str]:
    """Up to N distinct VEN/DEV (or VID/PID) prefixes — a family's HWID set
    can be huge (AMD graphics); its silicon ids are few. Ranked by the newest
    INF driver_date claiming the prefix (recency), then by frequency: the
    most-common prefix is often a legacy chip with many SUBSYS variants,
    while the water lives on the ids current drivers support. Where the
    family's silicon vendor has known PCI VEN ids, prefixes from other
    vendors' silicon (bundled INFs) are dropped."""
    allowed = _VEN_IDS.get(silicon_vendor)
    counts: Counter[str] = Counter()
    newest: dict[str, str] = {}
    for h in hwids:
        hu = h.upper()
        if buses and not hu.startswith(tuple(b + "\\" for b in buses)):
            continue
        for pat in (_PCI, _USB, _HDA):
            m = pat.match(hu)
            if m:
                prefix = m.group(1)
                if allowed and (ven := re.search(r"(?:VEN|VID)_([0-9A-F]{4})",
                                                 prefix)) \
                        and ven.group(1) not in allowed:
                    break
                counts[prefix] += 1
                d = (hw_date or {}).get(hu, "")
                if d > newest.get(prefix, ""):
                    newest[prefix] = d
                break
    ranked = sorted(counts, key=lambda p: (newest.get(p, ""), counts[p]),
                    reverse=True)
    return ranked[:MAX_QUERIES_PER_FAMILY]


def _parse_rows(html: str, hwid: str) -> list[dict]:
    out = []
    for tr in _ROW.findall(html):
        cells = [re.sub(r"\s+", " ", _TAG.sub(" ", c)).strip()
                 for c in _CELL.findall(tr)]
        cells = [c for c in cells if c]
        # title | product | classification | M/D/YYYY | version | size
        if len(cells) < 5 or not cells[2].lower().startswith("driver"):
            continue
        if "windows 11" not in cells[1].lower():
            continue
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", cells[3])
        ver = cells[4]
        if not m or not versions.parse(ver).tuple:
            continue
        out.append({"title": cells[0], "version": ver, "hwid": hwid,
                    "date": f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"})
    return out
