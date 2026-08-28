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
import re
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
        " chipset, socket, product_type, release_date, support_url FROM board")]

    effective = _effective_versions(conn)
    water = _water_level(conn, families, effective)
    board_lag, vendor_lag = _lag(conn, families, water, effective)

    bios_data = bios.compute(conn)
    bios_per_board = bios_data.pop("per_board")
    emit("bios.json", bios_data)
    emit("dashboard.json", _dashboard(conn, families, water, board_lag,
                                      effective, bios_data))
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


# NVIDIA stamps Windows packages with the INF DriverVer (3x.0.1D.DDDD) while
# NVIDIA itself — and ASUS, and the family water level — speak the marketing
# scheme (591.86 = ...15.9186: last digit of the third field + the fourth).
# Translating INF-scheme listings puts the whole family on one comparable
# line. Intel's 32.0.101.xxxx has a three-digit third field and never matches.
_NV_INF = re.compile(r"3\d\.0\.1(\d)\.(\d{4})\s*$")


def _nv_marketing(raw: str | None) -> str | None:
    m = _NV_INF.fullmatch((raw or "").strip())
    return f"{m.group(1)}{m.group(2)[:2]}.{m.group(2)[2:]}" if m else None


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
    for r in conn.execute(
            "SELECT a.artefact_id, a.version_raw, a.sha256, f.name fname"
            " FROM artefact a LEFT JOIN family f ON f.family_id = a.family_id"
            " WHERE a.kind = 'driver'"):
        listing = versions.parse(r["version_raw"])
        eff[r["artefact_id"]] = listing
        if r["fname"] == "NVIDIA Graphics":
            mk = _nv_marketing(r["version_raw"])
            if mk:
                eff[r["artefact_id"]] = versions.parse(mk)
                continue   # marketing scheme is already canonical — no INF pass
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
        # Slash-joined multi-version strings (Lenovo combo packages) parse to
        # one arbitrary member's tuple; they may not SET a family's water
        # where clean single-version rows exist (a combo's 6102.x Wi-Fi
        # member was topping Realtek LAN). Families that are combo-only
        # (Notebook *) keep their combo rows.
        import re as _re
        def _is_combo(r):
            raw = r["version_raw"] or ""
            return "/" in raw and len(_re.findall(r"\d+(?:\.\d+){2,}", raw)) >= 2
        clean = [r for r in rows if not _is_combo(r)]
        if clean:
            rows = clean
        # date-as-version listings ('AMD VGA driver v2026.04.15') outrank
        # every real scheme numerically; they may not set water where real
        # versions exist
        def _yearish(r):
            t = effective[r["artefact_id"]].tuple
            return t and 1990 <= t[0] <= 2100
        real = [r for r in rows if not _yearish(r)]
        if real:
            rows = real
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
        # Upstream-vs-upstream scheme arbitration: WU lists internal WDDM
        # versions (32.x) while AMD's own page lists marketing versions
        # (26.x) — when two upstream rows top different majors, the
        # date-newer one is the truer water.
        if top["source_type"] == "upstream":
            others = [r for r in rows if r["source_type"] == "upstream"
                      and effective[r["artefact_id"]].tuple[0]
                      != effective[top["artefact_id"]].tuple[0]]
            if others:
                alt = max(others, key=lambda r: versions.compare_key(
                    effective[r["artefact_id"]]))
                td, ad = top["release_date"], alt["release_date"]
                if ad and (not td or ad > td):
                    top = alt
        # Majority-line guard: occasional vendor mislabels put a foreign
        # version scheme atop a family (ASUS lists graphics 31.0.101.x and
        # chipset 10.1.x packages titled 'Intel GNA Driver'). When the top's
        # MAJOR isn't the family's dominant line (and the family has real
        # mass), the dominant line's top wins unless the outlier is
        # date-newer — genuine scheme migrations carry the newest dates,
        # stale one-off mislabels don't.
        from collections import Counter as _Counter
        majors = _Counter(effective[r["artefact_id"]].tuple[0] for r in rows)
        dom = majors.most_common(1)[0][0]
        if (top["source_type"] == "vendor"
                and effective[top["artefact_id"]].tuple[0] != dom
                and majors[dom] >= 3):
            cand = max((r for r in rows
                        if effective[r["artefact_id"]].tuple[0] == dom),
                       key=lambda r: versions.compare_key(effective[r["artefact_id"]]))
            td, cd = top["release_date"], cand["release_date"]
            if cd and (not td or cd > td):
                top = cand
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
            # Spec formula (water date - listed date) goes NEGATIVE when a
            # vendor re-publishes an old version after the water rose (MSI
            # re-listed RAID 9.3.3.218 eleven days after 9.3.3.329 appeared)
            # — clamping that to 0 falsely read as 'current'. A behind
            # version is behind for at least as long as the newer one has
            # existed, so take the max with (today - water date).
            wd = dt.date.fromisoformat(w["first_published"])
            lag = max((wd - dt.date.fromisoformat(r["listed_date"])).days,
                      (dt.date.today() - wd).days, 1)
        else:
            lag = None
        entry = {"board_id": board_id, "family_id": fid,
                 # what the vendor page shows — the INF-canonical 'effective'
                 # version is for ordering and lag, never for display
                 "listed_version": r["version_raw"],
                 # exception: the NVIDIA INF→marketing translation IS shown,
                 # since the water speaks marketing (32.0.15.9186 = 591.86)
                 "listed_equiv": (_nv_marketing(r["version_raw"])
                                  if families[fid]["name"] == "NVIDIA Graphics"
                                  else None),
                 "effective_major": (effective[r["artefact_id"]].tuple or (None,))[0],
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
    import shutil as _sh
    _sh.rmtree(out / "by-board", ignore_errors=True)   # drop stale boards
    (out / "by-board").mkdir(parents=True, exist_ok=True)
    level = {w["family_id"]: w for w in water}
    boards = {r["board_id"]: dict(r) for r in conn.execute(
        "SELECT board_id, vendor, name, slug, chipset, socket, support_url"
        " FROM board")}
    # newest per (family, major-version-line): vendors number the same driver
    # in incompatible schemes, so a listing's honest comparison target is the
    # newest version ON ITS OWN LINE; the cross-scheme family water stays as
    # context (lag is date-derived and unaffected).
    line_top: dict = {}
    import re as _re
    for r in conn.execute(
            "SELECT artefact_id, family_id, version_raw, release_date"
            " FROM artefact WHERE kind='driver' AND is_beta=0"
            " AND family_id IS NOT NULL"):
        e = effective[r["artefact_id"]]
        if not e.tuple:
            continue
        raw = r["version_raw"] or ""
        if "/" in raw and len(_re.findall(r"\d+(?:\.\d+){2,}", raw)) >= 2:
            continue   # slash-combos: one arbitrary member's tuple, skip
        k = (r["family_id"], e.tuple[0])
        cur = line_top.get(k)
        if cur is None or versions.compare_key(e) > versions.compare_key(cur[0]):
            disp = (_nv_marketing(raw)
                    if families[r["family_id"]]["name"] == "NVIDIA Graphics"
                    else None)
            line_top[k] = (e, r["release_date"], disp or r["version_raw"])
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
            maj = e["effective_major"]
            same = line_top.get((e["family_id"], maj)) if maj is not None else None
            same_differs = bool(
                same and versions.parse(w["version"]).tuple
                and versions.parse(w["version"]).tuple[0] != maj
                # the INF→marketing translation makes this family single-
                # scheme: 591.x vs 616.x are directly comparable, so a
                # same-line footnote would be noise, not a scheme bridge
                and fam["name"] != "NVIDIA Graphics")
            fams.append({
                "family": fam["name"], "component": fam["component"],
                "listed_version": e["listed_version"],
                "listed_equiv": e["listed_equiv"],
                "listed_date": e["listed_date"],
                "water_version": w["version"],
                "water_first_published": w["first_published"],
                # newest on the listing's own numbering line, when the family
                # water lives on a different (incomparable) line
                "same_line_newest": (same[2] if same_differs else None),
                "same_line_date": (same[1] if same_differs else None),
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


# component grouping shared with the dashboard's per-board chips
_GROUP = {"chipset": "Chipset", "npu": "Chipset", "graphics": "Graphics",
          "audio": "Audio", "lan": "LAN", "wlan": "Wireless",
          "bluetooth": "Wireless", "storage": "Storage", "usb": "USB",
          "camera": "Camera", "wwan": "WWAN"}
_GROUP_ORDER = ["Chipset", "Graphics", "Audio", "LAN", "Wireless", "Storage",
                "USB", "Camera", "WWAN", "Other"]


def _dashboard(conn, families, water, board_lag, effective, bios_data) -> dict:
    """Every figure the landing page renders, so the page never goes stale."""
    from collections import defaultdict as _dd
    today = dt.date.today()
    bmeta = {r["board_id"]: dict(r) for r in conn.execute(
        "SELECT board_id, vendor, name, socket, product_type FROM board")}

    per_board: dict[int, list] = _dd(list)
    for e in board_lag:
        if e["lag_days"] is not None:
            per_board[e["board_id"]].append(e)

    # last driver activity per board (for the silent-≥2yr metric)
    last_driver: dict[int, str] = {}
    for r in conn.execute("""
            SELECT ba.board_id, MAX(ba.listed_date) d FROM board_artefact ba
            JOIN artefact a ON a.artefact_id = ba.artefact_id
            WHERE a.kind = 'driver' AND a.family_id IS NOT NULL
            GROUP BY ba.board_id"""):
        if r["d"]:
            last_driver[r["board_id"]] = r["d"]

    def med(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else None

    vendors: dict[str, dict] = {}
    by_vendor: dict[str, list[int]] = _dd(list)
    for bid in per_board:
        by_vendor[bmeta[bid]["vendor"]].append(bid)
    for v, bids in sorted(by_vendor.items()):
        lags = sorted(l["lag_days"] for b in bids for l in per_board[b])
        worst_per_board = [max(l["lag_days"] for l in per_board[b]) for b in bids]
        silent = sum(1 for b in bids if last_driver.get(b)
                     and (today - dt.date.fromisoformat(last_driver[b])).days > 730)
        n = len(bids)
        vendors[v] = {
            "boards": n,
            "median_lag_days": med(lags),
            "p90_lag_days": lags[int(len(lags) * 0.9)] if lags else None,
            "worst_lag_days": lags[-1] if lags else None,
            "over_1yr": sum(1 for w in worst_per_board if w > 365),
            "over_1yr_pct": round(100 * sum(1 for w in worst_per_board if w > 365) / n),
            "silent_2yr": silent,
            "silent_2yr_pct": round(100 * silent / n),
        }

    heat: dict[str, dict] = {}
    for v, bids in by_vendor.items():
        cells = _dd(list)
        for b in bids:
            # graphics cards have no socket; give them their own heatmap column
            sock = bmeta[b]["socket"] or (
                "GPU" if bmeta[b]["product_type"] == "graphics-card" else None)
            if sock:
                cells[sock].append(max(l["lag_days"] for l in per_board[b]))
        if cells:
            heat[v] = {s: {"boards": len(x), "median_worst": med(x)}
                       for s, x in cells.items()}

    def board_summary(bid):
        chips: dict[str, int] = {}
        for l in per_board[bid]:
            g = _GROUP.get(families[l["family_id"]]["component"], "Other")
            chips[g] = max(chips.get(g, 0), l["lag_days"])
        lags = [l["lag_days"] for l in per_board[bid]]
        m = bmeta[bid]
        return {
            "board_id": bid, "name": m["name"],
            "socket": m["socket"] or m["product_type"],
            "families": len(lags), "current": sum(1 for x in lags if x == 0),
            "worst": max(lags),
            "chips": [[g, chips[g]] for g in _GROUP_ORDER if g in chips],
        }

    best_worst = {}
    for v, bids in by_vendor.items():
        ranked = sorted(bids, key=lambda b: (max(l["lag_days"] for l in per_board[b]),
                                             -len(per_board[b])))
        best_worst[v] = {"best": board_summary(ranked[0]),
                         "worst": board_summary(ranked[-1])}

    return {
        "tiles": {
            "boards": len(bmeta), "vendors": len(by_vendor),
            "families": len(water),
            "artefacts": conn.execute(
                "SELECT COUNT(*) FROM artefact WHERE kind='driver'").fetchone()[0],
        },
        "vendors": vendors,
        "heatmap": heat,
        "best_worst": best_worst,
        "upstream": {"ahead": sum(1 for w in water if w["upstream_only"]),
                     "total": len(water)},
        "bios": {"vendors": bios_data["vendors"],
                 "agesa_water": bios_data["agesa_water"]},
    }
