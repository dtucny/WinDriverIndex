"""ASUS crawler (spec §4.4).

Series (type=1) -> products per series (type=2) -> GetPDDrivers per board
with osid=52 (Windows 11 x64). Series facets overlap (a ROG board is also in
'AMD platform'), so products are deduped by PDId across series.

Empirically (2026-08-26): the honest User-Agent is accepted; `PDHashedId` is
NOT validated — empty works for newer products, confirming the spec's hunch;
`DownloadUrl.Global` is a path relative to the ASUS download CDN.
"""

from __future__ import annotations

import re
import sqlite3

from .. import db, scope, versions
from ..http import PoliteClient

VENDOR = "asus"

API = "https://www.asus.com/support/api/product.asmx"
DRIVERS_API = "https://www.asus.com/support/webapi/ProductV2/GetPDDrivers"
BIOS_API = "https://www.asus.com/support/api/product.asmx/GetPDBIOS"
CDN = "https://dlcdnets.asus.com"
WEBSITE = "ph"          # region; content believed region-identical (spec §4.4)
MB_TYPEID = 1156        # motherboards
OSID_WIN11 = 52

_BETA = re.compile(r"beta", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def crawl(conn: sqlite3.Connection, client: PoliteClient, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    products = _enumerate_products(client)
    in_scope = []
    for p in products.values():
        hit = scope.extract_chipset(p["PDName"])
        if hit:
            in_scope.append((p, *hit))
    log(f"asus: {len(products)} products, {len(in_scope)} in scope")
    if limit:
        in_scope = in_scope[:limit]

    n_boards = n_artefacts = n_new = 0
    for product, chipset, socket in in_scope:
        pdid = product["PDId"]
        board_id = db.upsert_board(
            conn, run_date, vendor=VENDOR, vendor_product_id=str(pdid),
            name=product["PDName"], chipset=chipset, socket=socket,
            support_url="https://www.asus.com/supportonly/"
                        f"{product['PDName'].replace(' ', '%20')}/helpdesk_download/")
        n_boards += 1
        data = client.get(DRIVERS_API, params={
            "website": WEBSITE, "model": product["PDName"],
            "pdhashedid": product.get("PDHashedId") or "",
            "pdid": pdid, "cpu": "", "osid": OSID_WIN11},
            snapshot=f"drivers_{pdid}.json").json()
        for category, entry in _iter_files(data):
            recorded = _record_artefact(conn, run_date, board_id, entry, category)
            if recorded is not None:
                n_artefacts += 1
                n_new += recorded
        # BIOS list (separate endpoint, same shape); Description carries AGESA
        bios = client.get(BIOS_API, params={
            "website": WEBSITE, "model": product["PDName"],
            "pdhashedid": product.get("PDHashedId") or "",
            "pdid": pdid, "cpu": ""},
            snapshot=f"bios_{pdid}.json").json()
        for category, entry in _iter_files(bios):
            recorded = _record_artefact(conn, run_date, board_id, entry,
                                        category, kind_override="bios")
            if recorded is not None:
                n_artefacts += 1
                n_new += recorded
        conn.commit()
    log(f"asus: {n_boards} boards, {n_artefacts} listings, {n_new} new artefacts")
    return {"boards": n_boards, "listings": n_artefacts, "new_artefacts": n_new}


def _enumerate_products(client: PoliteClient) -> dict[str, dict]:
    series = client.get(
        f"{API}/GetPDLevel",
        params={"website": WEBSITE, "type": 1, "typeid": MB_TYPEID, "productflag": 0},
        snapshot="series.json").json()
    items = series["Result"]["ProductLevel"]["Products"]["Items"]
    products: dict[str, dict] = {}
    for s in items:
        data = client.get(
            f"{API}/GetPDLevel",
            params={"website": WEBSITE, "type": 2, "typeid": s["Id"], "productflag": 1},
            snapshot=f"products_series_{s['Id']}.json").json()
        for p in (data.get("Result") or {}).get("Product") or []:
            products[p["PDId"]] = p
    return products


def _iter_files(data: dict):
    for group in (data.get("Result") or {}).get("Obj") or []:
        # 'Utilities: Armoury Crate' etc. stay captured; family rules sort them.
        for entry in group.get("Files") or []:
            yield group.get("Name"), entry


def _parse_size(text: str | None) -> int | None:
    if not text:
        return None
    m = re.match(r"([\d.]+)\s*(KB|MB|GB)", text, re.IGNORECASE)
    if not m:
        return None
    return int(float(m.group(1)) * {"kb": 1e3, "mb": 1e6, "gb": 1e9}[m.group(2).lower()])


def _record_artefact(conn, run_date, board_id, entry, category,
                     kind_override: str | None = None) -> bool | None:
    url = (entry.get("DownloadUrl") or {}).get("Global")
    if not url:
        return None
    if url.startswith("/"):
        url = CDN + url
    version_raw = entry.get("Version")
    ver = versions.parse(version_raw)
    release = (entry.get("ReleaseDate") or "").replace("/", "-") or None
    sha = (entry.get("sha256") or "").strip()
    title = " — ".join(filter(None, (entry.get("Title"),
                                     entry.get("Description")))).strip() or ""
    kind = kind_override or ("utility" if "utilit" in (category or "").lower() else "driver")
    # entry['Id'] embeds per-board tokens and even URLs differ per board for
    # the same payload; the published sha256 is the only stable identity
    # (13,451 listings -> 503 distinct hashes, observed 2026-08-26).
    sha_norm = sha.lower() if _SHA256.match(sha) else None
    artefact_id, is_new = db.upsert_artefact(
        conn, run_date, vendor=VENDOR,
        vendor_artefact_id=sha_norm or url.rsplit("/", 1)[-1],
        kind=kind,
        component_hint=category,
        version_raw=version_raw,
        version_normalised=ver.normalised_json,
        release_date=release,
        file_size=_parse_size(entry.get("FileSize")),
        url=url,
        sha256=sha_norm,
        os_raw="Win11 64",
        is_beta=int(ver.is_beta or bool(_BETA.search(title))
                    or str(entry.get("IsRelease")) == "0"),
        description_text=title,
    )
    db.link_board_artefact(conn, run_date, board_id, artefact_id, release)
    return is_new
