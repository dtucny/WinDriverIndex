"""Water level, vendor lag, and static JSON output (spec §7–8).

Emits versioned, self-describing JSON under public/v1/. Betas and preinstall
variants never set the water level. A board/family pairing's lag is zero when
the board lists the water-level version, else the days between the water
level's first appearance anywhere and the board listing's own date.

Published caveat (§7): 'newest' means newest *published by any vendor*, not
'known good' — a vendor may legitimately withhold a regressed driver.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections import defaultdict

from . import bios, config, versions

SCHEMA_VERSION = "1.0.0"
CAVEAT = ("Water level means the newest version any vendor has published, "
          "not a judgement that it is good; a vendor may legitimately "
          "withhold a regressed driver.")


def run(conn: sqlite3.Connection, *, log=print) -> dict:
    generated = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    out = config.PUBLIC_DIR / "v1"
    (out / "by-hwid").mkdir(parents=True, exist_ok=True)

    def emit(name: str, payload) -> None:
        (out / name).write_text(json.dumps(
            {"schema_version": SCHEMA_VERSION, "generated": generated,
             "caveat": CAVEAT, "data": payload},
            indent=1, ensure_ascii=False) + "\n")

    families = {r["family_id"]: dict(r) for r in conn.execute(
        "SELECT family_id, name, silicon_vendor, component, hwids FROM family")}
    for f in families.values():
        f["hwids"] = json.loads(f["hwids"])

    artefacts = [dict(r) for r in conn.execute(
        "SELECT artefact_id, vendor, vendor_artefact_id, kind, family_id,"
        " version_raw, version_normalised, release_date, file_size, url,"
        " sha256, md5, os_raw, is_beta, first_seen, last_seen"
        " FROM artefact WHERE kind = 'driver'")]

    boards = [dict(r) for r in conn.execute(
        "SELECT board_id, vendor, vendor_product_id, name, slug, revision,"
        " chipset, socket, release_date, support_url FROM board")]

    effective = _effective_versions(conn)
    water = _water_level(conn, families, effective)
    board_lag, vendor_lag = _lag(conn, families, water, effective)

    bios_data = bios.compute(conn)
    bios_per_board = bios_data.pop("per_board")
    emit("bios.json", bios_data)
    emit("families.json", list(families.values()))
    emit("artefacts.json", artefacts)
    emit("boards.json", boards)
    emit("water-level.json", water)
    emit("vendor-lag.json", vendor_lag)

    n_hwid = _emit_by_hwid(conn, out, families, water, generated)
    n_bb = _emit_by_board(conn, out, families, water, board_lag, bios_per_board,
                          effective, generated)

    log(f"publish: {len(families)} families, {len(artefacts)} artefacts, "
        f"{len(boards)} boards, {n_hwid} hwid files, {n_bb} board files -> {out}")
    return {"families": len(families), "artefacts": len(artefacts),
            "boards": len(boards), "hwids": n_hwid, "failed": 0}


def _effective_versions(conn) -> dict[int, versions.ParsedVersion]:
    """Comparable version per artefact. Vendors sometimes renumber the same
    driver with their own scheme (AMD's '1.8240.169' for MediaTek's
    '1.1044.x' Bluetooth line); the INF DriverVer is canonical, so when INF
    evidence disagrees with the listing the INF version replaces it.

    The override is confined to INFs on the SAME major-version line as the
    listing. A bundled package carries INFs from several components (an ASUS
    AMD-chipset zip also ships NPU/GPIO INFs numbered 32.x); taking the global
    max would misreport the chipset family's water level as the NPU version.
    Same-major scoping keeps the renumbering fix — the rebadged driver shares
    the listing's major — while rejecting unrelated bundled components."""
    eff: dict[int, versions.ParsedVersion] = {}
    for r in conn.execute("SELECT artefact_id, version_raw, sha256 FROM artefact"
                          " WHERE kind = 'driver'"):
        listing = versions.parse(r["version_raw"])
        eff[r["artefact_id"]] = listing
        if not r["sha256"] or not listing.tuple:
            continue
        same_major = [
            iv for (v,) in conn.execute(
                "SELECT driver_ver FROM inf WHERE payload_sha256 = ?", (r["sha256"],))
            if v and (iv := versions.parse(v)).tuple
            and iv.tuple[0] == listing.tuple[0]]
        if same_major and not any(iv.tuple == listing.tuple for iv in same_major):
            eff[r["artefact_id"]] = max(same_major, key=versions.compare_key)
    return eff


def _water_level(conn, families, effective) -> list[dict]:
    result = []
    for fid, fam in sorted(families.items()):
        rows = [r for r in conn.execute(
            "SELECT artefact_id, version_raw, release_date, vendor, source_type,"
            " first_seen"
            " FROM artefact WHERE family_id = ? AND kind = 'driver'"
            " AND is_beta = 0", (fid,)).fetchall()
            if effective[r["artefact_id"]].tuple]
        if not rows or "(preinstall)" in fam["name"]:
            continue
        top = max(rows, key=lambda r: versions.compare_key(effective[r["artefact_id"]]))
        # Best version any *board vendor* lists, for the upstream-gap metric.
        vend_rows = [r for r in rows if r["source_type"] == "vendor"]
        vend_top = (max(vend_rows, key=lambda r: versions.compare_key(
            effective[r["artefact_id"]])) if vend_rows else None)
        # Cross-source scheme guard: sources renumber independently (MediaTek
        # is 1.x/3.x/5.x on board-vendor sites but year-based 26.x on WU), so
        # when an upstream top's MAJOR differs from the vendor top's, numeric
        # comparison is meaningless — the release date arbitrates, and the
        # vendor row wins ties or missing dates.
        if (top["source_type"] == "upstream" and vend_top is not None
                and effective[top["artefact_id"]].tuple[0]
                != effective[vend_top["artefact_id"]].tuple[0]):
            td, vd = top["release_date"], vend_top["release_date"]
            if not td or (vd and vd >= td):
                top = vend_top
        top_tuple = effective[top["artefact_id"]].tuple
        at_top = [r for r in rows if effective[r["artefact_id"]].tuple == top_tuple]
        dates = [r["release_date"] for r in at_top if r["release_date"]]
        if not dates:
            # dateless upstream sources (silicon pages): fall back to when the
            # crawl first observed the version — an upper bound on its age
            # that tightens as weekly runs accumulate.
            dates = [r["first_seen"] for r in at_top if r["first_seen"]]
        result.append({
            "family_id": fid, "family": fam["name"],
            "version": effective[top["artefact_id"]].raw,
            "version_normalised": list(top_tuple),
            "first_published": min(dates) if dates else None,
            "published_by": sorted({r["vendor"] for r in at_top}),
            # True when only an upstream reference (WU Catalog, silicon
            # vendor) ships this version — no board vendor has caught up.
            "upstream_only": all(r["source_type"] == "upstream" for r in at_top),
            "best_vendor_version": (effective[vend_top["artefact_id"]].raw
                                    if vend_top else None),
        })
    return result


def _lag(conn, families, water, effective) -> tuple[list[dict], list[dict]]:
    level = {w["family_id"]: w for w in water}
    per_board: dict[int, list] = defaultdict(list)
    board_meta = {r["board_id"]: dict(r) for r in conn.execute(
        "SELECT board_id, vendor, name FROM board")}

    rows = conn.execute("""
        SELECT ba.board_id, a.artefact_id, a.family_id, a.version_raw,
               ba.listed_date
        FROM board_artefact ba JOIN artefact a ON a.artefact_id = ba.artefact_id
        WHERE a.kind = 'driver' AND a.is_beta = 0 AND a.family_id IS NOT NULL
    """).fetchall()

    best: dict[tuple[int, int], sqlite3.Row] = {}
    for r in rows:
        if r["family_id"] not in level:
            continue
        key = (r["board_id"], r["family_id"])
        if key not in best or (versions.compare_key(effective[r["artefact_id"]])
                               > versions.compare_key(effective[best[key]["artefact_id"]])):
            best[key] = r

    board_lag = []
    for (board_id, fid), r in best.items():
        w = level[fid]
        if effective[r["artefact_id"]].tuple == tuple(w["version_normalised"]):
            lag = 0
        elif w["first_published"] and r["listed_date"]:
            lag = max(0, (dt.date.fromisoformat(w["first_published"])
                          - dt.date.fromisoformat(r["listed_date"])).days)
        else:
            lag = None
        entry = {"board_id": board_id, "family_id": fid,
                 "listed_version": effective[r["artefact_id"]].raw,
                 "listed_date": r["listed_date"], "lag_days": lag}
        board_lag.append(entry)
        if lag is not None:
            per_board[board_id].append(lag)

    vendor_boards: dict[str, list[list[int]]] = defaultdict(list)
    for board_id, lags in per_board.items():
        vendor_boards[board_meta[board_id]["vendor"]].append(lags)
    vendor_lag = []
    for vendor, boards in sorted(vendor_boards.items()):
        all_lags = sorted(l for lags in boards for l in lags)
        vendor_lag.append({
            "vendor": vendor,
            "boards": len(boards),
            "pairings": len(all_lags),
            "median_lag_days": all_lags[len(all_lags) // 2] if all_lags else None,
            "p90_lag_days": all_lags[int(len(all_lags) * 0.9)] if all_lags else None,
            "worst_lag_days": all_lags[-1] if all_lags else None,
            "boards_over_365d": sum(1 for lags in boards if max(lags) > 365),
        })
    return board_lag, vendor_lag


def _emit_by_hwid(conn, out, families, water, generated) -> int:
    level = {w["family_id"]: w for w in water}
    n = 0
    for fid, fam in families.items():
        w = level.get(fid)
        known = [dict(r) for r in conn.execute(
            "SELECT DISTINCT version_raw, release_date, vendor, url FROM artefact"
            " WHERE family_id = ? AND kind = 'driver' ORDER BY release_date DESC",
            (fid,))]
        for hwid in fam["hwids"]:
            safe = hwid.replace("\\", "_").replace("&", "+")
            (out / "by-hwid" / f"{safe}.json").write_text(json.dumps(
                {"schema_version": SCHEMA_VERSION, "generated": generated,
                 "hwid": hwid, "family": fam["name"],
                 "water_level": w, "known_versions": known},
                indent=1, ensure_ascii=False) + "\n")
            n += 1
    return n


def _emit_by_board(conn, out, families, water, board_lag, bios_per_board,
                   effective, generated) -> int:
    """One JSON per board for the picker page — mirrors the by-hwid pattern."""
    (out / "by-board").mkdir(parents=True, exist_ok=True)
    level = {w["family_id"]: w for w in water}
    boards = {r["board_id"]: dict(r) for r in conn.execute(
        "SELECT board_id, vendor, name, slug, chipset, socket, support_url"
        " FROM board")}
    from collections import defaultdict as _dd
    per: dict[int, list] = _dd(list)
    for e in board_lag:
        per[e["board_id"]].append(e)
    n = 0
    for bid, entries in per.items():
        b = boards[bid]
        fams = []
        for e in sorted(entries, key=lambda x: families[x["family_id"]]["name"]):
            fam = families[e["family_id"]]
            w = level[e["family_id"]]
            fams.append({
                "family": fam["name"], "component": fam["component"],
                "listed_version": e["listed_version"],
                "listed_date": e["listed_date"],
                "water_version": w["version"],
                "water_first_published": w["first_published"],
                "upstream_only": w["upstream_only"],
                "lag_days": e["lag_days"],
            })
        payload = {
            "schema_version": SCHEMA_VERSION, "generated": generated,
            "caveat": CAVEAT,
            "board": {k: b[k] for k in ("board_id", "vendor", "name", "slug",
                                        "chipset", "socket", "support_url")},
            "bios": bios_per_board.get(bid),
            "families": fams,
        }
        (out / "by-board" / f"{bid}.json").write_text(
            json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
        n += 1
    return n
