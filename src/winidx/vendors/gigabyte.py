"""Gigabyte crawler (spec §4.2).

Two calls per product: GetProducts enumerates every motherboard ever made
(3,278 as of 2026-08), GetProductTabDataAsync returns drivers/BIOS/utilities
for one product. The `?v=` query param on download URLs is the payload's MD5
(verified 2026-08-26 by downloading and hashing), so within-vendor dedup and
cross-run identity need no downloads at all.

Requires Chrome TLS impersonation — www.gigabyte.com sits behind Akamai
fingerprint filtering (403 for plain clients regardless of User-Agent).
"""

from __future__ import annotations

import re
import sqlite3

from .. import db, scope, versions
from ..http import PoliteClient

VENDOR = "gigabyte"
# Akamai rejects a Chrome TLS handshake paired with a non-Chrome User-Agent,
# so the honest-UA policy can't apply to www.gigabyte.com (verified 2026-08-26:
# impersonated UA 200, honest UA 403). download.gigabyte.com accepts plain
# clients, so Tier-2 payload fetches stay honestly identified.
BROWSER_HEADERS = True
API = "https://www.gigabyte.com/iisApplicationNuxt/api/proxy/api/v1.0"
PRODUCT_LINE = 2  # motherboards

WIN11_INFO_VALUE = 147   # infoParentId 35
COMPONENT_PARENT = 32
OS_PARENT = 35

_MD5_PARAM = re.compile(r"[?&]v=([0-9a-f]{32})")

# Category keys in the download tab worth capturing. Manuals/FAQ are skipped.
KINDS = {"driver": "driver", "bios": "bios", "utility": "utility", "firmware": "firmware"}


def crawl(conn: sqlite3.Connection, client: PoliteClient, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    products = client.get(
        f"{API}/Support/global/DownloadCenter/{PRODUCT_LINE}/GetProducts",
        snapshot="products.json").json()["data"]

    # Facet snapshots are kept for future chipset-code mapping even though
    # scoping currently keys off the product name.
    for prop in ("GetFirstProperty", "GetSecondProperty"):
        client.get(f"{API}/Support/global/DownloadCenter/{PRODUCT_LINE}/{prop}",
                   snapshot=f"{prop}.json")

    in_scope = []
    for p in products:
        hit = scope.extract_chipset(p["productName"])
        if hit:
            in_scope.append((p, *hit))
    log(f"gigabyte: {len(products)} products, {len(in_scope)} in scope")
    if limit:
        in_scope = in_scope[:limit]

    n_boards = n_artefacts = n_new = 0
    for product, chipset, socket in in_scope:
        pid = product["productId"]
        try:
            tab = client.get(
                f"{API}/Consumer/global/GetProductTabDataAsync/Support/{pid}",
                snapshot=f"product_{pid}.json").json()["data"]
        except Exception as exc:
            log(f"  gigabyte: skipped {product['productName']}: {str(exc)[:60]}")
            continue
        slug = re.sub(r"\s+", "-", re.sub(r"[().]", "", product["productName"])).strip("-")
        board_id = db.upsert_board(
            conn, run_date, vendor=VENDOR, vendor_product_id=str(pid),
            name=product["productName"], slug=slug, chipset=chipset, socket=socket,
            support_url=f"https://www.gigabyte.com/Motherboard/{slug}/support")
        n_boards += 1
        for entry, kind in _iter_download_entries(tab):
            recorded = _record_artefact(conn, run_date, board_id, entry, kind)
            if recorded is not None:
                n_artefacts += 1
                n_new += recorded
        conn.commit()
    log(f"gigabyte: {n_boards} boards, {n_artefacts} listings, {n_new} new artefacts")
    return {"boards": n_boards, "listings": n_artefacts, "new_artefacts": n_new}


def _iter_download_entries(tab_data):
    for tab in tab_data or []:
        if tab.get("key") != "download":
            continue
        for category in tab.get("child") or []:
            kind = KINDS.get(category.get("key", ""))
            if not kind:
                continue
            for entry in category.get("data") or []:
                if entry.get("filePath"):
                    yield entry, kind


def _record_artefact(conn, run_date, board_id, entry, kind) -> bool | None:
    """Record one listing; True/False = new/known artefact, None = out of scope."""
    info = entry.get("info") or []
    os_values = [i for i in info if i.get("infoParentId") == OS_PARENT]
    # Win11-only scope: keep entries listing Win11, plus OS-less ones (BIOS,
    # some firmware/utilities).
    if os_values and not any(i.get("infoValue") == WIN11_INFO_VALUE for i in os_values):
        return None
    component = next((i["infoName"] for i in info
                      if i.get("infoParentId") == COMPONENT_PARENT), None)
    url = entry["filePath"]
    md5_match = _MD5_PARAM.search(url)
    ver = versions.parse(entry.get("fileVersion"))
    release = (entry.get("fileReleaseDate") or "")[:10] or None
    artefact_id, is_new = db.upsert_artefact(
        conn, run_date, vendor=VENDOR,
        vendor_artefact_id=entry["fileName"],
        kind=kind,
        component_hint=component,
        version_raw=entry.get("fileVersion"),
        version_normalised=ver.normalised_json,
        release_date=release,
        file_size=entry.get("fileSize"),
        url=url,
        md5=md5_match.group(1) if md5_match else None,
        os_raw=", ".join(i["infoName"] for i in os_values) or None,
        is_beta=int(ver.is_beta),
        description_text=entry.get("fileDescription"),
    )
    db.link_board_artefact(conn, run_date, board_id, artefact_id, release)
    return is_new
