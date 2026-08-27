"""MSI crawler (spec §4.1).

Enumeration needs a Laravel CSRF token scraped from any support page (used
only for the handful of tag/product-list calls); the per-board panel API is
tokenless, so the bulk of the crawl never touches the token. Categories come
back as a dict of {category title: [entries]} under result.downloads.

Identity: `download_id` is a global chronological ID space shared across
drivers/BIOS/utilities and across boards. Download URLs are NOT
version-addressed (e.g. amd_chipset_drivers_am4_am5.zip is overwritten in
place), so the URL is never used as identity.

Like Gigabyte, www.msi.com is behind TLS-fingerprint filtering that also
rejects honest User-Agents (verified 2026-08-26: Chrome identity 200,
honest UA 403).
"""

from __future__ import annotations

import re
import sqlite3

from .. import db, scope, versions
from ..http import PoliteClient

VENDOR = "msi"
BROWSER_HEADERS = True

SUPPORT_PAGE = "https://www.msi.com/support/mb"
AJAX = "https://www.msi.com/support/ajax"
PANEL = "https://www.msi.com/api/v1/product/support/panel"
PRODUCT_LINE_ID = 8  # motherboards
WIN11 = "Win11 64"

# (panel `type` param, artefact kind, send os param)
PANEL_TYPES = [("driver", "driver", True), ("bios", "bios", False),
               ("utility", "utility", True)]

_TOKEN = re.compile(r'csrf-token" content="([^"]+)')
_SHA256 = re.compile(r"([0-9a-fA-F]{64})")
_BETA = re.compile(r"\(beta\s*version\)", re.IGNORECASE)


def crawl(conn: sqlite3.Connection, client: PoliteClient, run_date: str,
          *, limit: int | None = None, log=print) -> dict:
    products = _enumerate_products(client, log)
    in_scope = []
    for p in products:
        hit = scope.extract_chipset(p["title"])
        if hit:
            in_scope.append((p, *hit))
    log(f"msi: {len(products)} products, {len(in_scope)} in scope")
    if limit:
        in_scope = in_scope[:limit]

    n_boards = n_artefacts = n_new = 0
    for product, chipset, socket in in_scope:
        slug = product["link"]
        board_id = db.upsert_board(
            conn, run_date, vendor=VENDOR, vendor_product_id=str(product["id"]),
            name=product["title"], slug=slug, chipset=chipset, socket=socket,
            release_date=(product.get("release") or "")[:10] or None,
            support_url=f"https://www.msi.com/Motherboard/{slug}/support")
        n_boards += 1
        for type_param, kind, with_os in PANEL_TYPES:
            params = {"product": slug, "type": type_param}
            if with_os:
                params["os"] = WIN11
            data = client.get(PANEL, params=params,
                              snapshot=f"panel_{slug}_{type_param}.json").json()
            for entry in _iter_entries(data):
                recorded = _record_artefact(conn, run_date, board_id, entry, kind)
                if recorded is not None:
                    n_artefacts += 1
                    n_new += recorded
        conn.commit()
    log(f"msi: {n_boards} boards, {n_artefacts} listings, {n_new} new artefacts")
    return {"boards": n_boards, "listings": n_artefacts, "new_artefacts": n_new}


def _enumerate_products(client: PoliteClient, log) -> list[dict]:
    """Tag list -> products per series tag, deduped by product id.

    Snapshot-cached; the CSRF token is fetched only when a listing snapshot
    is actually missing (tokens are session-bound and expire — spec §9).
    """
    token = None

    def get(url: str, snapshot: str):
        nonlocal token
        if not (client.snapshot_dir / snapshot).exists() and token is None:
            page = client.get(SUPPORT_PAGE)
            token = _TOKEN.search(page.text).group(1)
        return client.get(f"{url}&_token={token}" if token else url,
                          snapshot=snapshot).json()

    tags = get(f"{AJAX}/get_tag_list_by_product_line?id={PRODUCT_LINE_ID}",
               "tags.json")
    series = [t for t in tags["filter_tag_list"]["1"]
              if "accessory" not in t["tag_title"].lower()]
    products: dict[int, dict] = {}
    for tag in series:
        lst = get(f"{AJAX}/get_product_by_tag?id={tag['tag_id']}&product_line=mb",
                  f"products_tag_{tag['tag_id']}.json")
        for p in lst:
            products[p["id"]] = p
    return list(products.values())


def _iter_entries(panel: dict):
    downloads = (panel.get("result") or {}).get("downloads") or {}
    if not isinstance(downloads, dict):   # empty panels return []
        return
    for category, entries in downloads.items():
        if isinstance(entries, list):     # skip stray type_title / os keys
            for entry in entries:
                if isinstance(entry, dict):
                    yield entry, category


def _record_artefact(conn, run_date, board_id, pair, kind) -> bool | None:
    entry, category = pair
    if not entry.get("download_id"):
        return None   # external links (MS Store etc.), size 0, no hash
    sha = _SHA256.search(entry.get("download_sha256") or "")
    version_raw = entry.get("download_version")
    ver = versions.parse(version_raw)
    os_field = entry.get("os")
    os_raw = ",".join(os_field) if isinstance(os_field, list) else (os_field or None)
    is_beta = ver.is_beta or bool(_BETA.search(version_raw or ""))
    artefact_id, is_new = db.upsert_artefact(
        conn, run_date, vendor=VENDOR,
        vendor_artefact_id=str(entry["download_id"]),
        kind=kind,
        component_hint=category if isinstance(category, str) else None,
        version_raw=version_raw,
        version_normalised=ver.normalised_json,
        release_date=entry.get("download_release"),
        file_size=entry.get("download_size"),
        url=entry.get("download_url"),
        sha256=sha.group(1).lower() if sha else None,
        os_raw=os_raw,
        is_beta=int(is_beta),
        description_raw=entry.get("download_description"),
        description_text=" — ".join(filter(None, (
            entry.get("download_title"), entry.get("download_description")))),
    )
    db.link_board_artefact(conn, run_date, board_id, artefact_id,
                           entry.get("download_release"))
    return is_new
