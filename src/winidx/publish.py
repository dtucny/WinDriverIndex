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

from . import bios, config, hwids, versions
from .families import _inf_key, hwid_support

# 2.0.0: by-hwid lists every matching family and drops known_versions (now in
# by-family/); artefacts.json version_normalised is an array, not a string.
SCHEMA_VERSION = "2.0.0"
# Every published file carries its data license (LICENSE-DATA at repo root).
LICENSE = "CC-BY-4.0"
CAVEAT = ("Water level means the newest version any vendor has published, "
          "not a judgement that it is good; a vendor may legitimately "
          "withhold a regressed driver.")


def run(conn: sqlite3.Connection, *, log=print) -> dict:
    generated = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    out = config.PUBLIC_DIR / "v1"
    out.mkdir(parents=True, exist_ok=True)

    def emit(name: str, payload) -> None:
        (out / name).write_text(json.dumps(
            {"schema_version": SCHEMA_VERSION, "license": LICENSE,
             "generated": generated, "caveat": CAVEAT, "data": payload},
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
    for a in artefacts:   # stored as JSON text; publish the array itself
        a["version_normalised"] = json.loads(a["version_normalised"] or "null")

    boards = [dict(r) for r in conn.execute(
        "SELECT board_id, vendor, vendor_product_id, name, slug, revision,"
        " chipset, socket, product_type, release_date, support_url FROM board")]
    smbios = _smbios(conn)
    for b in boards:
        b["smbios"] = smbios.get(b["board_id"])

    effective = _effective_versions(conn)
    water = _water_level(conn, families, effective)
    lines = _lines(conn, families, effective)
    for w in water:
        w["lines"] = _water_lines(w, lines.get(w["family_id"], {}))
    board_lag, vendor_lag = _lag(conn, families, water, effective)

    # diff against the previous published state BEFORE overwriting it, so
    # the landing page can say what a refresh actually changed
    changes = _changes(out, water, boards, generated)

    bios_data = bios.compute(conn)
    bios_per_board = bios_data.pop("per_board")
    emit("bios.json", bios_data)
    emit("changes.json", changes)
    dash = _dashboard(conn, families, water, board_lag, effective, bios_data)
    dash["changes"] = changes
    emit("dashboard.json", dash)
    for f in families.values():
        f["version_equiv"] = SCHEME_EQUIV.get(f["name"])
        f["download_hint"] = DOWNLOAD_HINTS.get(f["name"])
    emit("families.json", list(families.values()))
    emit("artefacts.json", artefacts)
    emit("boards.json", boards)
    emit("water-level.json", water)
    emit("vendor-lag.json", vendor_lag)
    infs = _infs(conn)
    emit("infs.json", infs)

    n_hwid = _emit_by_hwid(conn, out, families, water, generated)
    n_fam = _emit_by_family(conn, out, families, water, generated)
    n_bb = _emit_by_board(conn, out, families, water, board_lag, bios_per_board,
                          lines, generated)
    emit("manifest.json", _manifest(out, {"by-hwid": n_hwid, "by-family": n_fam,
                                          "by-board": n_bb}))

    log(f"publish: {len(families)} families, {len(artefacts)} artefacts, "
        f"{len(boards)} boards, {len(infs)} INF versions, {n_hwid} hwid files, "
        f"{n_bb} board files -> {out}")
    return {"families": len(families), "artefacts": len(artefacts),
            "boards": len(boards), "hwids": n_hwid, "failed": 0}


# Per-family listing→canonical-scheme rules: applied for ordering, surfaced as
# listed_equiv on board pages, and published in families.json so clients
# apply the very same translation. Templates use $n group references (JS and
# .NET syntax) so a client can pass them to its regex replace verbatim;
# 'flags' holds any regex flags ('i') apart from the pattern.
SCHEME_EQUIV = {
    # NVIDIA stamps Windows packages with the INF DriverVer (3x.0.1D.DDDD)
    # while NVIDIA itself — and ASUS, and the family water level — speak the
    # marketing scheme (591.86 = ...15.9186: last digit of the third field +
    # the fourth). Intel's 32.0.101.xxxx has a three-digit third field and
    # never matches.
    "NVIDIA Graphics": {
        "pattern": r"^3\d\.0\.1(\d)\.(\d{2})(\d{2})$", "replace": "$1$2.$3",
        "flags": "",
        "note": "INF DriverVer to NVIDIA marketing version: 32.0.15.9186 = 591.86"},
    # ASRock writes Realtek UAD audio versions as the bare build ('10007.1_UAD_
    # WHQL'); the canonical form every other vendor lists is 6.0.<build>.<rev>.
    "Realtek Audio": {
        "pattern": r"^(\d{4,5})\.(\d+)[_ ]?UAD", "replace": "6.0.$1.$2",
        "flags": "i",
        "note": "ASRock bare UAD build to the 6.0.x.y form: 10007.1_UAD = 6.0.10007.1"},
}


def _equiv(family: str, raw: str | None) -> str | None:
    """A listing's version in its family's canonical scheme, if a rule applies."""
    rule = SCHEME_EQUIV.get(family)
    if not rule:
        return None
    m = re.search(rule["pattern"], (raw or "").strip(),
                  re.I if "i" in rule["flags"] else 0)
    return re.sub(r"\$(\d)", lambda g: m.group(int(g.group(1))),
                  rule["replace"]) if m else None


# Where a user gets a family's driver when their machine vendor lags behind:
# only families whose silicon vendor ships generic drivers to end users.
# Everything else is OEM-customised — use the board's support_url.
_AMD = {"label": "AMD Software: Adrenalin Edition (auto-detect) or AMD drivers page",
        "url": "https://www.amd.com/en/support/download/drivers.html"}
_INTEL = {"label": "Intel Driver & Support Assistant",
          "url": "https://www.intel.com/content/www/us/en/support/detect.html"}
DOWNLOAD_HINTS = {
    "NVIDIA Graphics": {"label": "NVIDIA App or NVIDIA driver search",
                        "url": "https://www.nvidia.com/en-us/drivers/"},
    "AMD Graphics": _AMD, "AMD Chipset": _AMD, "AMD RAID": _AMD,
    **{name: _INTEL for name in (
        "Intel VGA", "Intel Wi-Fi", "Intel Bluetooth", "Intel LAN",
        "Intel I211 LAN", "Intel I219 LAN", "Intel I225/I226 LAN",
        "Intel Chipset INF", "Intel RST", "Killer Wi-Fi", "Killer Bluetooth")},
}


def _changes(out, water, boards, generated) -> dict | None:
    """What this publish changed vs the previously published data: water-level
    movements and newly indexed machines. A publish that changes nothing
    carries the last non-empty delta forward (with its original dates), so
    the landing page keeps showing the latest meaningful refresh."""
    def _old(name):
        try:
            return json.loads((out / name).read_text())
        except Exception:
            return None
    ow, ob, oc = (_old("water-level.json"), _old("boards.json"),
                  _old("changes.json"))
    if not ow:
        return None
    prev = {w["family"]: w for w in ow["data"]}
    moves = []
    for w in water:
        p = prev.get(w["family"])
        if p is None or p["version"] != w["version"]:
            fv = versions.parse(p["version"]) if p else None
            tv = versions.parse(w["version"])
            # 'down' is not a vendor rollback: it means the previous top
            # listing was reclassified into a more specific family (INF
            # evidence) or respelled to its INF-canonical version
            dirn = None
            if fv and fv.tuple and tv.tuple:
                dirn = ("up" if versions.compare_key(tv) > versions.compare_key(fv)
                        else "down" if versions.compare_key(tv) < versions.compare_key(fv)
                        else "same")
            moves.append({"family": w["family"],
                          "from": p["version"] if p else None,
                          "to": w["version"], "dir": dirn,
                          "date": w["first_published"],
                          "published_by": w["published_by"]})
    new_boards = []
    if ob:
        old_ids = {b["board_id"] for b in ob["data"]}
        new_boards = [{"vendor": b["vendor"], "name": b["name"],
                       "board_id": b["board_id"]}
                      for b in boards if b["board_id"] not in old_ids]
    if not moves and not new_boards:
        return oc["data"] if oc and oc.get("data") else None
    return {"since": ow["generated"], "as_of": generated,
            "water": sorted(moves, key=lambda m: m["family"]),
            "new_boards_count": len(new_boards),
            "new_boards": new_boards[:20]}


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
        if mk := _equiv(r["fname"], r["version_raw"]):
            eff[r["artefact_id"]] = versions.parse(mk)
            continue   # canonical scheme reached — no INF pass
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


YEAR_LINE_FAMILIES = {"MediaTek Wi-Fi 6E", "MediaTek Wi-Fi 7",
                      "MediaTek Bluetooth (Wi-Fi 6E)", "MediaTek Bluetooth (Wi-Fi 7)"}


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
        # MediaTek's year-numbered builds (25.40.x, 26.40.x: what Windows
        # Update and Lenovo ship) are a second numbering of the same drivers
        # whose 1.x/3.x/5.x line board vendors list. Nothing ties a year
        # build to a native one, so they may not set the water over the
        # native line; they stay visible in 'lines' for same-line checks.
        if fam["name"] in YEAR_LINE_FAMILIES:
            native = [r for r in rows
                      if not 20 <= effective[r["artefact_id"]].tuple[0] <= 29]
            if native:
                rows = native
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
            tmaj = effective[top["artefact_id"]].tuple[0]

            def _span(m):
                ds = [r["release_date"] for r in rows if r["release_date"]
                      and effective[r["artefact_id"]].tuple[0] == m]
                return (min(ds), max(ds)) if ds else None
            st, sd = _span(tmaj), _span(dom)
            cand = max((r for r in rows
                        if effective[r["artefact_id"]].tuple[0] == dom),
                       key=lambda r: versions.compare_key(effective[r["artefact_id"]]))
            td, cd = top["release_date"], cand["release_date"]
            if st and sd and st[1] < sd[0]:
                top = cand   # the outlier's whole line predates the dominant one
            elif st and sd and st[0] > sd[1]:
                pass         # sequential scheme marching on (Intel ME's
                             # year-week majors: 2512 ended before 2620 began)
            else:
                # parallel lines: a single vendor's fringe line never outranks
                # the dominant one — Dell restamps handed date-newer wins to
                # Camera 81.x, Fingerprint 40.x, Thunderbolt 61.3, WWAN
                # 18300.x. Corroborated lines (Intel RST 21 by Dell+Lenovo)
                # keep the original date arbitration.
                line_vendors = {r["vendor"] for r in rows
                                if effective[r["artefact_id"]].tuple[0] == tmaj}
                if len(line_vendors) < 2 or (cd and (not td or cd > td)):
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
                 "listed_equiv": _equiv(families[fid]["name"], r["version_raw"]),
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


def _lines(conn, families, effective) -> dict[int, dict[int, dict]]:
    """Newest version and publication span per (family, major-version line).
    Vendors number the same driver in incompatible schemes, so a listing's
    honest comparison target is the newest version ON ITS OWN LINE; the
    cross-scheme family water stays as context (lag is date-derived and
    unaffected).

    Spans and dates use each version's FIRST appearance anywhere, not every
    listing: vendors keep re-listing old builds (a 31.x AMD graphics package
    dated 2026-08, two years after 32.x took over), and those late listings
    made finished lines look alive. A line's span runs from its first
    version's appearance to its newest version's."""
    first: dict[tuple, str] = {}          # (family, version tuple) -> first date
    lines: dict[int, dict[int, dict]] = defaultdict(dict)
    for r in conn.execute(
            "SELECT artefact_id, family_id, version_raw, release_date"
            " FROM artefact WHERE kind='driver' AND is_beta=0"
            " AND family_id IS NOT NULL"):
        e = effective[r["artefact_id"]]
        if not e.tuple:
            continue
        raw = r["version_raw"] or ""
        if "/" in raw and len(re.findall(r"\d+(?:\.\d+){2,}", raw)) >= 2:
            continue   # slash-combos: one arbitrary member's tuple, skip
        if d := r["release_date"]:
            k = (r["family_id"], e.tuple)
            first[k] = min(first.get(k, d), d)
        ln = lines[r["family_id"]].setdefault(
            e.tuple[0], {"top": None, "date": None, "disp": None, "span": None})
        if ln["top"] is None or versions.compare_key(e) > versions.compare_key(ln["top"]):
            ln.update(top=e, disp=_equiv(families[r["family_id"]]["name"], raw) or raw)
    for (fid, t), d in first.items():
        ln = lines[fid][t[0]]
        ln["span"] = [min(ln["span"][0], d), None] if ln["span"] else [d, None]
    # a line ends when its NEWEST version first appeared: an older build
    # surfacing late (first listed in 2026 though built in 2023) doesn't
    # keep a finished line alive
    for fid, fl in lines.items():
        for ln in fl.values():
            ln["date"] = first.get((fid, ln["top"].tuple))
            if ln["span"]:
                ln["span"][1] = max(ln["date"] or ln["span"][0], ln["span"][0])
    return lines


# Lines must overlap this long to count as parallel: a vendor's last build on
# the old line routinely lands a few weeks after the first on the new one
# (AMD 31.0.24028 appeared a week after 32.0.11002).
PARALLEL_MIN_DAYS = 90


def _parallel(a, b) -> bool:
    """Two version lines are PARALLEL when they were published
    contemporaneously (AMD's 25.x packaging vs 32.x INF overlap for years). A
    line that simply ENDED before the other began — Intel Bluetooth 21.x vs
    24.x, AMD graphics 31.x vs 32.x — is one scheme marching on, and comparing
    across them is fine. Unknown spans count as parallel."""
    if not (a and b):
        return True
    overlap = (dt.date.fromisoformat(min(a[1], b[1]))
               - dt.date.fromisoformat(max(a[0], b[0]))).days
    return overlap >= PARALLEL_MIN_DAYS


def _lines_parallel(family, major_a, span_a, major_b, span_b) -> bool:
    """_parallel, plus MediaTek's year numbering: always a separate scheme
    from the native line, however short their shared history so far."""
    if family in YEAR_LINE_FAMILIES and (20 <= major_a <= 29) != (20 <= major_b <= 29):
        return True
    return _parallel(span_a, span_b)


def _water_lines(w, fam_lines) -> list[dict]:
    """water-level.json 'lines': every major-version line of the family, so a
    client can compare an installed version against the newest on its OWN
    line when that line runs parallel to the water's."""
    wmaj = w["version_normalised"][0]
    wspan = fam_lines.get(wmaj, {}).get("span")
    out = []
    for major, ln in fam_lines.items():
        sp = ln["span"] or [None, None]
        out.append({"major": major, "newest": ln["disp"],
                    "newest_normalised": list(ln["top"].tuple),
                    "newest_date": ln["date"], "first_date": sp[0],
                    "last_date": sp[1], "water_line": major == wmaj,
                    "parallel_to_water": (major != wmaj and _lines_parallel(
                        w["family"], major, ln["span"], wmaj, wspan))})
    return sorted(out, key=lambda x: (x["last_date"] or "", x["major"]), reverse=True)


# MSI BIOS files are named <board code>v<rev> ('7C96v1L9' = MS-7C96); that
# code is in the SMBIOS baseboard product ('MAG B650 TOMAHAWK WIFI (MS-7D75)')
_MSI_CODE = re.compile(r"(7[0-9A-F]{3})v[0-9A-Z]", re.I)


def _smbios(conn) -> dict[int, dict[str, list[str]]]:
    """Deterministic machine-matching keys per board, where the vendor exposes
    one (boards.json 'smbios'; README lists the keys and match rules):

    system_sku                  Dell systemID = SMBIOS SKU Number, exact
    baseboard_product           HP platform ID = SMBIOS baseboard product, exact
    system_product_prefix       Lenovo machine types: SMBIOS product name
                                (MTM, '21K9CTO1WW') starts with one
    baseboard_product_contains  MSI board code ('MS-7D75'), a substring of the
                                SMBIOS baseboard product; several boards can
                                share one code (MEG X570 ACE/UNIFY = MS-7C35)

    Crawlers record what they parse in board.smbios; HP and Lenovo keys are
    also recoverable from vendor_product_id, and MSI's from BIOS listings."""
    out: dict[int, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for r in conn.execute("SELECT board_id, vendor, vendor_product_id, smbios,"
                          " product_type FROM board"):
        for k, vs in json.loads(r["smbios"] or "{}").items():
            out[r["board_id"]][k].update(vs)
        if r["vendor"] == "hp":
            out[r["board_id"]]["baseboard_product"].add(
                r["vendor_product_id"].split("/")[0].upper())
        elif r["vendor"] == "lenovo":
            out[r["board_id"]]["system_product_prefix"].add(r["vendor_product_id"].upper())
    for bid, ver in conn.execute("""
            SELECT b.board_id, a.version_raw FROM board b
            JOIN board_artefact ba ON ba.board_id = b.board_id
            JOIN artefact a ON a.artefact_id = ba.artefact_id
            WHERE b.vendor = 'msi' AND b.product_type = 'motherboard'
              AND a.kind = 'bios'"""):
        if m := _MSI_CODE.match(ver or ""):
            out[bid]["baseboard_product_contains"].add("MS-" + m.group(1).upper())
    return {bid: {k: sorted(v) for k, v in d.items() if v} for bid, d in out.items()}


def _hwid_filename(hwid: str) -> str:
    return hwid.replace("\\", "_").replace("&", "+") + ".json"


def _emit_by_hwid(conn, out, families, water, generated) -> int:
    """One file per HWID listing EVERY family it identifies (a shared HWID —
    Intel Wi-Fi vs Killer, one Realtek INF binding three NIC generations —
    used to hold only the last family written). Ordered by match_share: the
    fraction of that family's packages whose INFs carry the HWID. The full
    version history lives once per family in by-family/, not in each of the
    ~60k HWID files (which made this directory 9 GB)."""
    import shutil
    shutil.rmtree(out / "by-hwid", ignore_errors=True)   # drop stale HWIDs
    (out / "by-hwid").mkdir(parents=True, exist_ok=True)
    # lines stay in water-level.json / by-family: repeated in every HWID
    # file they were most of this directory's size
    level = {w["family_id"]: {k: v for k, v in w.items() if k != "lines"}
             for w in water}
    support = hwid_support(conn)
    per: dict[str, list] = defaultdict(list)
    for fid, fam in families.items():
        for h in fam["hwids"]:
            per[h].append((support.get(fid, {}).get(h, 0.0), fam["name"], fid))
    for h, fams in per.items():
        fams.sort(key=lambda x: (-x[0], x[1]))
        entries = [{"family_id": fid, "family": name, "match_share": share,
                    "water_level": level.get(fid)} for share, name, fid in fams]
        (out / "by-hwid" / _hwid_filename(h)).write_text(json.dumps(
            {"schema_version": SCHEMA_VERSION, "license": LICENSE,
             "generated": generated, "hwid": h,
             "family": entries[0]["family"], "families": entries},
            indent=1, ensure_ascii=False) + "\n")
    return len(per)


def _emit_by_family(conn, out, families, water, generated) -> int:
    """by-family/{family_id}.json: the family, its water level (with lines)
    and every version any source has listed for it."""
    import shutil
    shutil.rmtree(out / "by-family", ignore_errors=True)
    (out / "by-family").mkdir(parents=True, exist_ok=True)
    level = {w["family_id"]: w for w in water}
    for fid, fam in families.items():
        known = [dict(r) for r in conn.execute(
            "SELECT DISTINCT version_raw, release_date, vendor, url FROM artefact"
            " WHERE family_id = ? AND kind = 'driver' ORDER BY release_date DESC",
            (fid,))]
        (out / "by-family" / f"{fid}.json").write_text(json.dumps(
            {"schema_version": SCHEMA_VERSION, "license": LICENSE,
             "generated": generated, "caveat": CAVEAT,
             "family": {k: v for k, v in fam.items() if k != "hwids"},
             "hwid_count": len(fam["hwids"]),
             "water_level": level.get(fid), "known_versions": known},
            indent=1, ensure_ascii=False) + "\n")
    return len(families)


# Realtek stamps one build's INF two ways: X.Y.MMDD.YYYY and X.Y.50.MMDD
# (Dell ships rt640x64.inf as both 10.080.0407.2026 and 10.080.50.0407; the
# rt640x64.sys inside both is FileVersion 10.080.0407.2026, same size). They
# sort far apart numerically, so infs.json folds the dated spelling into the
# .50 row as an alias.
_RTK_DATED = re.compile(r"^(\d+)\.(\d+)\.(\d{4})\.(\d{4})$")
_RTK_50 = re.compile(r"^(\d+)\.(\d+)\.50\.(\d{4})$")
# AMD renames its display INFs every release (u0403049.inf, u0199286.inf, and
# the amdwin-u… SoftwareComponent INFs), so the file name alone doesn't
# connect an installed AMD driver to newer ones; inf_series does.
_AMD_RELEASE_INF = re.compile(r"^(amdwin-)?u\d{7}\.inf$")


def _inf_series(name: str | None) -> str | None:
    if name and (m := _AMD_RELEASE_INF.match(name)):
        return (m.group(1) or "") + "u*.inf"
    return name


def _fold_realtek_aliases(groups: dict) -> None:
    """Merge a dated-spelling row into its .50 twin (same INF, same date,
    same X.Y), keeping the dated string in driver_ver_aliases."""
    for (key, ver), g in list(groups.items()):
        m = _RTK_DATED.match(ver)
        d = g["driver_date"]
        if not m or not d or m.group(4) != d[:4] or m.group(3) != d[5:7] + d[8:10]:
            continue
        twin = next((t for (k2, v2), t in groups.items() if k2 == key
                     and (m2 := _RTK_50.match(v2)) and t["driver_date"] == d
                     and m2.group(3) == m.group(3)
                     and (int(m2.group(1)), int(m2.group(2)))
                     == (int(m.group(1)), int(m.group(2)))), None)
        if twin is None:
            continue
        twin["driver_ver_aliases"].append(ver)
        for f in ("family_ids", "artefact_ids", "hwids"):
            twin[f] |= g[f]
        if g["first_published"] and (not twin["first_published"]
                                     or g["first_published"] < twin["first_published"]):
            twin["first_published"] = g["first_published"]
        del groups[(key, ver)]


def _merge_upstream_infs(conn, groups: dict) -> None:
    """Add INF versions known only upstream (upstream_inf: Windows Update
    software components, AMD chipset release notes). A version a vendor
    package also carries just gains the source; a new one becomes a row with
    no artefacts, no DriverVer date, and first_published = the source's date.
    Class and families come from the same INF's vendor rows."""
    by_name: dict[str, list[dict]] = defaultdict(list)
    for (_k, _v), g in groups.items():
        if g["inf_name"]:
            by_name[g["inf_name"]].append(g)
    for r in conn.execute("SELECT source, inf_name, driver_ver, published, hwids"
                          " FROM upstream_inf"):
        name, ver = r["inf_name"], r["driver_ver"]
        norm = list(versions.parse(ver).tuple or [])
        if not norm:
            continue
        siblings = by_name.get(name, [])
        twin = next((g for g in siblings if g["driver_ver_normalised"] == norm), None)
        if twin:
            twin["sources"].add(r["source"])
            continue
        g = groups.setdefault((name, ver), {
            "inf_name": name, "inf_series": _inf_series(name),
            "driver_ver": ver, "driver_ver_aliases": [],
            "driver_ver_normalised": norm, "driver_date": None,
            "class": next((s["class"] for s in siblings if s["class"]), None),
            "family_ids": set().union(*(s["family_ids"] for s in siblings)),
            "artefact_ids": set(), "hwids": set(),
            "first_published": r["published"], "sources": set()})
        g["sources"].add(r["source"])
        g["hwids"].update(json.loads(r["hwids"]))
        if not g["hwids"]:
            g["hwids"].update(*(s["hwids"] for s in siblings))
        by_name[name].append(g)


def _infs(conn) -> list[dict]:
    """infs.json: one row per (INF file name, DriverVer). Windows reports the
    INSTALLED INF's DriverVer, which is often not the package version vendors
    list (Realtek NIC 10.79.50.1003 inside package 1125.x; NVIDIA's INF vs
    marketing scheme), so a client comparing installed against newest should
    compare INF versions of the same INF file. HWIDs are published without
    SUBSYS/REV qualifiers (hwids.base): every device also reports that less
    specific ID, and it keeps the file ~100x smaller than the raw lists."""
    arts: dict[str, list] = defaultdict(list)
    for r in conn.execute(
            "SELECT artefact_id, family_id, sha256, release_date FROM artefact"
            " WHERE kind = 'driver' AND sha256 IS NOT NULL"):
        arts[r["sha256"]].append(r)
    groups: dict[tuple, dict] = {}
    cleaned: dict[str, list[str]] = {}
    for r in conn.execute(
            "SELECT payload_sha256, path, inf_sha256, class, driver_date,"
            " driver_ver, hwids FROM inf"
            " WHERE hwids != '[]' AND driver_ver IS NOT NULL"):
        rows = arts.get(r["payload_sha256"])
        if not rows:
            continue
        sha = r["inf_sha256"]
        if sha not in cleaned:
            tokens = json.loads(r["hwids"])
            cleaned[sha] = (sorted({hwids.base(h) for h in hwids.specific(tokens)})
                            or hwids.vendor_class(tokens))
        if not cleaned[sha]:
            continue
        key = _inf_key(r["path"], sha)
        name = key if key.endswith(".inf") else None
        g = groups.setdefault((key, r["driver_ver"]), {
            "inf_name": name, "inf_series": _inf_series(name),
            "driver_ver": r["driver_ver"], "driver_ver_aliases": [],
            # Windows shows DriverVer fields as numbers (10.079.0327.2025 is
            # 10.79.327.2025 in Device Manager): compare these, not the text
            "driver_ver_normalised": list(versions.parse(r["driver_ver"]).tuple or []),
            "driver_date": r["driver_date"], "class": r["class"],
            "family_ids": set(), "artefact_ids": set(), "hwids": set(),
            "first_published": None, "sources": {"vendor"}})
        g["hwids"].update(cleaned[sha])
        if r["driver_date"] and (not g["driver_date"] or r["driver_date"] < g["driver_date"]):
            g["driver_date"] = r["driver_date"]
        for a in rows:
            g["artefact_ids"].add(a["artefact_id"])
            if a["family_id"] is not None:
                g["family_ids"].add(a["family_id"])
            d = a["release_date"]
            if d and (not g["first_published"] or d < g["first_published"]):
                g["first_published"] = d
    _fold_realtek_aliases(groups)
    _merge_upstream_infs(conn, groups)
    out = []
    for g in groups.values():
        g["sources"] = sorted(g["sources"])
        g["family_ids"] = sorted(g["family_ids"])
        g["artefact_ids"] = sorted(g["artefact_ids"])
        g["hwids"] = sorted(g["hwids"])
        out.append(g)
    return sorted(out, key=lambda g: (g["inf_name"] or "~", g["driver_ver"]))


def _manifest(out, dir_counts) -> dict:
    """Size and sha256 of every aggregate file, so a client revalidates this
    one small file and re-downloads only what changed."""
    import hashlib
    files = {}
    for p in sorted(out.glob("*.json")):
        if p.name == "manifest.json":
            continue
        data = p.read_bytes()
        files[p.name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return {"files": files, "directories": dir_counts}


def _emit_by_board(conn, out, families, water, board_lag, bios_per_board,
                   lines, generated) -> int:
    """One JSON per board for the picker page — mirrors the by-hwid pattern."""
    import shutil as _sh
    _sh.rmtree(out / "by-board", ignore_errors=True)   # drop stale boards
    (out / "by-board").mkdir(parents=True, exist_ok=True)
    level = {w["family_id"]: w for w in water}
    boards = {r["board_id"]: dict(r) for r in conn.execute(
        "SELECT board_id, vendor, name, slug, chipset, socket, product_type,"
        " support_url FROM board")}
    water_by_name = {families[w["family_id"]]["name"]: w for w in water}
    from collections import defaultdict as _dd
    per: dict[int, list] = _dd(list)
    for e in board_lag:
        per[e["board_id"]].append(e)
    n = 0
    # every board gets a page — a card whose vendor lists no drivers at all
    # (MSI's entire GPU catalogue) must say so, not 404 out of the picker
    for bid, b in boards.items():
        entries = per.get(bid, [])
        fams = []
        for e in sorted(entries, key=lambda x: families[x["family_id"]]["name"]):
            fam = families[e["family_id"]]
            w = level[e["family_id"]]
            maj = e["effective_major"]
            fl = lines.get(e["family_id"], {})
            same = fl.get(maj) if maj is not None else None
            wt = versions.parse(w["version"]).tuple
            # the footnote only for a PARALLEL line (see _parallel)
            same_differs = bool(same and wt and wt[0] != maj
                                and _lines_parallel(fam["name"], maj, same["span"], wt[0],
                                                    fl.get(wt[0], {}).get("span")))
            fams.append({
                "family": fam["name"], "component": fam["component"],
                "listed_version": e["listed_version"],
                "listed_equiv": e["listed_equiv"],
                "listed_date": e["listed_date"],
                "water_version": w["version"],
                "water_first_published": w["first_published"],
                # newest on the listing's own numbering line, when the family
                # water lives on a different (incomparable) line
                "same_line_newest": (same["disp"] if same_differs else None),
                "same_line_date": (same["date"] if same_differs else None),
                "upstream_only": w["upstream_only"],
                "lag_days": e["lag_days"],
            })
        # a graphics card with no GPU-driver row still has an authoritative
        # reference: the silicon vendor's current driver for its chip
        gpu_ref = None
        if b["product_type"] == "graphics-card":
            chip = (b["chipset"] or "").upper()
            ref_fam = ("NVIDIA Graphics" if chip.startswith("RTX")
                       else "AMD Graphics" if chip.startswith("RX")
                       else "Intel VGA" if chip.startswith("ARC") else None)
            if ref_fam and ref_fam not in {f["family"] for f in fams}:
                w = water_by_name.get(ref_fam)
                if w:
                    gpu_ref = {"family": ref_fam, "water_version": w["version"],
                               "water_first_published": w["first_published"]}
        payload = {
            "schema_version": SCHEMA_VERSION, "license": LICENSE,
            "generated": generated, "caveat": CAVEAT,
            "board": {k: b[k] for k in ("board_id", "vendor", "name", "slug",
                                        "chipset", "socket", "product_type",
                                        "support_url")},
            "bios": bios_per_board.get(bid),
            "gpu_reference": gpu_ref,
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

    # per product type within each vendor: a GPU in ASUS's "worst" slot next
    # to MSI's motherboard (MSI cards list no drivers) is not a comparison
    best_worst = {}
    for v, bids in by_vendor.items():
        groups = _dd(list)
        for b in bids:
            groups[bmeta[b]["product_type"] or "motherboard"].append(b)
        vb = {}
        for pt, tb in groups.items():
            if len(tb) < 5:
                continue   # too small a cohort for a best/worst to mean much
            ranked = sorted(tb, key=lambda b: (max(l["lag_days"] for l in per_board[b]),
                                               -len(per_board[b])))
            vb[pt] = {"boards": len(tb),
                      "best": board_summary(ranked[0]),
                      "worst": board_summary(ranked[-1])}
        best_worst[v] = vb

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
