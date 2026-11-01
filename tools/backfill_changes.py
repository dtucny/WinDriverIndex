"""Rebuild changes.json's history from the dated snapshots on the live site:
each pair of consecutive snapshots diffed the way publish diffs a refresh.
For when the local public/ has lost it (or before history was kept).

    uv run python tools/backfill_changes.py 2026-08-26 2026-08-27 ...

Writes public/v1/changes.json; the next `winidx publish` carries it on.
"""

import json
import sys

import httpx

from winidx import config, db, publish

BASE = "https://windriverindex.tucny.com/v1"


def snapshot(date: str, name: str) -> dict:
    r = httpx.get(f"{BASE}/{date}/{name}", timeout=120)
    r.raise_for_status()
    return r.json()


def main(dates: list[str]) -> None:
    conn = db.connect(config.DATA_DIR / "index.sqlite")
    silicon_of = dict(conn.execute("SELECT name, silicon_vendor FROM family"))
    history = []
    prev = None
    for date in dates:
        cur = (snapshot(date, "water-level.json"), snapshot(date, "boards.json"))
        if prev:
            delta = publish._delta(prev[0]["data"], prev[1]["data"], cur[0]["data"],
                                   cur[1]["data"], prev[0]["generated"],
                                   cur[0]["generated"], silicon_of)
            if delta:
                history.insert(0, delta)
            print(f"{date}: {len(delta['water']) if delta else 0} moves, "
                  f"{delta['new_boards_count'] if delta else 0} new machines")
        prev = cur
    history = history[:publish.CHANGES_HISTORY]
    out = config.PUBLIC_DIR / "v1" / "changes.json"
    env = json.loads(out.read_text())
    env["data"] = {**history[0], "history": history}
    out.write_text(json.dumps(env, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(history)} refreshes to {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
