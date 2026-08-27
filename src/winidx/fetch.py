"""Tier 2 payload fetch (spec §5): download artefacts not yet in the
content-addressed store, hash, verify against vendor-published hashes.

Store layout: data/payloads/{sha256[:2]}/{sha256}{ext}. The store holds one
copy per unique payload; artefact rows point at it via their sha256 column
(computed here for vendors that don't publish one).

MSI URLs are overwritten in place across versions (see doc/findings.md), so a
hash mismatch against the published value likely means the listing moved on —
re-crawl rather than retrying.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from . import config
from .http import PoliteClient

FETCH_KINDS = ("driver",)   # BIOS/utility payloads add ~tens of GB and no families


def payload_path(sha256: str, ext: str = ".zip") -> Path:
    return config.PAYLOAD_DIR / sha256[:2] / f"{sha256}{ext}"


def run(conn: sqlite3.Connection, run_date: str, *, vendor: str | None = None,
        limit: int | None = None, kinds=FETCH_KINDS, newest_only: bool = False,
        log=print) -> dict:
    where = (f"kind IN ({','.join('?' * len(kinds))}) AND url IS NOT NULL"
         " AND source_type = 'vendor'")
    params: list = list(kinds)
    if vendor:
        where += " AND vendor = ?"
        params.append(vendor)
    rows = conn.execute(
        f"SELECT artefact_id, vendor, vendor_artefact_id, family_id, version_raw,"
        f" url, sha256, md5, file_size"
        f" FROM artefact WHERE {where} ORDER BY vendor, artefact_id", params).fetchall()
    if newest_only:
        rows = _newest_per_family(rows)
        log(f"fetch: newest-only narrowed to {len(rows)} payloads")

    client = PoliteClient("payloads", run_date)
    n_fetched = n_skipped = n_mismatch = 0
    for row in rows:
        if _already_stored(conn, row):
            n_skipped += 1
            continue
        if limit is not None and n_fetched >= limit:
            break
        try:
            sha, md5, size, path = _download(client, row["url"])
        except Exception as exc:
            log(f"FETCH FAIL {row['vendor']} {row['vendor_artefact_id']}: {exc}")
            continue
        ok = True
        if row["sha256"] and row["sha256"] != sha:
            ok = False
            log(f"HASH MISMATCH (sha256) {row['vendor']} {row['vendor_artefact_id']}: "
                f"published {row['sha256'][:12]} != payload {sha[:12]}")
        if row["md5"] and row["md5"] != md5:
            ok = False
            log(f"HASH MISMATCH (md5) {row['vendor']} {row['vendor_artefact_id']}")
        n_mismatch += not ok
        if ok:
            conn.execute("UPDATE artefact SET sha256 = ?, md5 = ?, file_size = ?"
                         " WHERE artefact_id = ?",
                         (sha, md5, size, row["artefact_id"]))
        conn.commit()
        n_fetched += 1
        log(f"fetched {row['vendor']} {row['vendor_artefact_id']} "
            f"{size / 1e6:.1f} MB {sha[:12]}")
    stats = {"fetched": n_fetched, "skipped": n_skipped, "mismatched": n_mismatch}
    log(f"fetch: {stats}")
    return stats


def _newest_per_family(rows) -> list:
    """Newest artefact per (vendor, family), plus every family-less artefact —
    enough for INF identity and HWIDs without mirroring vendors' full version
    history (ASUS alone lists 183 GB of historical driver payloads)."""
    from . import versions
    best: dict[tuple, object] = {}
    unassigned = []
    for r in rows:
        if r["family_id"] is None:
            unassigned.append(r)
            continue
        key = (r["vendor"], r["family_id"])
        if key not in best or (versions.compare_key(versions.parse(r["version_raw"]))
                               > versions.compare_key(versions.parse(best[key]["version_raw"]))):
            best[key] = r
    return list(best.values()) + unassigned


def _already_stored(conn, row) -> bool:
    sha = row["sha256"]
    if not sha and row["md5"]:
        # Another row for the same payload (same MD5) may know the SHA-256.
        peer = conn.execute(
            "SELECT sha256 FROM artefact WHERE md5 = ? AND sha256 IS NOT NULL LIMIT 1",
            (row["md5"],)).fetchone()
        sha = peer["sha256"] if peer else None
        if sha and payload_path(sha).exists():
            conn.execute("UPDATE artefact SET sha256 = ? WHERE artefact_id = ?",
                         (sha, row["artefact_id"]))
            conn.commit()
            return True
        return False
    return bool(sha) and payload_path(sha).exists()


def _download(client: PoliteClient, url: str) -> tuple[str, str, int, Path]:
    # Payloads reach 1.3 GB (AMD graphics); the listing-tier timeout is far
    # too short for them.
    resp = client.get(url, timeout=1800)
    body = resp.content
    sha = hashlib.sha256(body).hexdigest()
    md5 = hashlib.md5(body).hexdigest()
    ext = Path(url.split("?")[0]).suffix or ".bin"
    path = payload_path(sha, ext if len(ext) <= 5 else ".bin")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return sha, md5, len(body), path
