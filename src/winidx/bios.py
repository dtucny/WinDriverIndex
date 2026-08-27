"""BIOS currency and the AGESA water level (roadmap v0.2 §4).

BIOS images don't cross vendors, so there is no artefact-level dedup — but on
AMD platforms every vendor's BIOS wraps the same AMD **AGESA** microcode
bundle, and vendors name it in their release notes ("Update AGESA ComboV2
1.2.0.12", "Updated AMD AGESA ComboAm4v2PI 1.0.0.2", "AGESA ComboAM5 PI
1.3.0.1b Patch A"). That makes AGESA the cross-vendor-comparable BIOS
component: per socket+line, the newest AGESA any vendor ships is a water
level, and each board's newest stable BIOS has an AGESA lag against it.

Comparison keys: AGESA's fourth segment mixes digits and letters where
letters rank above digits chronologically (…1.2.0.8 → 1.2.0.A → 1.2.0.B →
1.2.0.Ca), which plain ordinal comparison per character already orders
correctly; an optional "Patch X" suffix ranks above the unpatched base.
AM4 has two AGESA lines (ComboPI for pre-Matisse, ComboV2 PI after); they are
tracked separately and only V2 is reported as the AM4 water line.

Beta BIOS rows count as vendor activity (days-since-last-BIOS) but never set
or satisfy the AGESA water level.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from collections import defaultdict

_AGESA = re.compile(
    r"AGESA\s*(?:to\s*)?([A-Za-z][A-Za-z0-9 /_.-]{0,24}?)?\s*"
    r"(\d\.\d\.\d(?:\.\w+)?)"
    r"(?:\s*Patch\s*([A-Z]))?",
    re.IGNORECASE)


def parse_agesa(text: str | None) -> tuple[str, str, str] | None:
    """-> (line, version, patch) or None. line ∈ {'V2','V1','AM5',''}."""
    if not text or "agesa" not in text.lower():
        return None
    m = _AGESA.search(text)
    if not m:
        return None
    raw_line = (m.group(1) or "").upper()
    if "AM5" in raw_line:
        line = "AM5"
    elif "V2" in raw_line:
        line = "V2"
    elif "AM4" in raw_line or "COMBO" in raw_line:
        line = "V1"
    else:
        line = ""
    return line, m.group(2), (m.group(3) or "").upper()


def agesa_key(version: str, patch: str = "") -> tuple:
    parts = version.split(".")
    head = tuple(int(p) for p in parts[:3])
    tail = tuple(ord(c) for c in (parts[3].upper() if len(parts) > 3 else ""))
    return (head, tail, patch)


def _line_for(socket: str | None, line: str) -> str | None:
    """Resolve a parsed line tag against the board's socket; None = untracked."""
    if socket == "AM5":
        return "AM5"
    if socket == "AM4":
        # bare 'AGESA 1.2.0.x' on AM4 is the V2 line in practice
        return {"V2": "V2", "": "V2", "V1": "V1"}.get(line)
    return None   # Intel: no AGESA


def compute(conn: sqlite3.Connection, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    boards = {r["board_id"]: dict(r) for r in conn.execute(
        "SELECT board_id, vendor, name, socket FROM board")}

    per_board: dict[int, dict] = defaultdict(
        lambda: {"last": None, "agesa": None, "agesa_line": None})
    for r in conn.execute("""
            SELECT ba.board_id, a.release_date, ba.listed_date,
                   a.description_text, a.is_beta
            FROM board_artefact ba
            JOIN artefact a ON a.artefact_id = ba.artefact_id
            WHERE a.kind = 'bios'"""):
        b = per_board[r["board_id"]]
        date = r["release_date"] or r["listed_date"]
        if date and (b["last"] is None or date > b["last"]):
            b["last"] = date            # betas count as activity
        if r["is_beta"]:
            continue
        parsed = parse_agesa(r["description_text"])
        if parsed:
            line = _line_for(boards[r["board_id"]]["socket"], parsed[0])
            if line and (b["agesa"] is None
                         or agesa_key(parsed[1], parsed[2]) > agesa_key(*b["agesa"])):
                b["agesa"] = (parsed[1], parsed[2])
                b["agesa_line"] = line

    # AGESA water per tracked line (stable BIOS only, already filtered)
    water: dict[str, tuple] = {}
    for b in per_board.values():
        if b["agesa"]:
            k = agesa_key(*b["agesa"])
            if b["agesa_line"] not in water or k > water[b["agesa_line"]][0]:
                water[b["agesa_line"]] = (k, b["agesa"])

    def days(d): return (today - dt.date.fromisoformat(d)).days

    vendors: dict[str, dict] = {}
    by_vendor: dict[str, list[int]] = defaultdict(list)
    for bid, b in per_board.items():
        by_vendor[boards[bid]["vendor"]].append(bid)
    for v, bids in sorted(by_vendor.items()):
        ages = sorted(days(per_board[b]["last"]) for b in bids
                      if per_board[b]["last"])
        n = len(ages)
        tracked = [b for b in bids if per_board[b]["agesa"]]
        at_water = sum(
            1 for b in tracked
            if agesa_key(*per_board[b]["agesa"])
            >= water[per_board[b]["agesa_line"]][0])
        vendors[v] = {
            "boards_with_bios": n,
            "median_days_since_bios": ages[n // 2] if ages else None,
            "bios_within_1yr_pct": round(100 * sum(a <= 365 for a in ages) / n) if n else None,
            "bios_silent_2yr_pct": round(100 * sum(a > 730 for a in ages) / n) if n else None,
            "amd_boards_agesa_tracked": len(tracked),
            "amd_boards_at_agesa_water_pct":
                round(100 * at_water / len(tracked)) if tracked else None,
        }

    return {
        "vendors": vendors,
        "agesa_water": {
            line: {"version": ver, "patch": patch or None}
            for line, (_, (ver, patch)) in sorted(water.items())
        },
    }
