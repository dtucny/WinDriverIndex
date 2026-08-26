"""winidx CLI — one subcommand per pipeline stage (spec §5, §10).

Implemented: crawl (gigabyte), status. The remaining stages are stubs that
name their spec section so the build order stays visible in the tool itself.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import config, db, deploy, extract, families, fetch, publish
from .http import PoliteClient
from .vendors import asrock, asus, gigabyte, msi

CRAWLERS = {"gigabyte": gigabyte, "msi": msi, "asus": asus, "asrock": asrock}
PLANNED: set[str] = set()


def cmd_crawl(args) -> int:
    run_date = dt.date.today().isoformat()
    vendors = [args.vendor] if args.vendor else sorted(CRAWLERS)
    conn = db.connect()
    for vendor in vendors:
        if vendor in PLANNED:
            print(f"{vendor}: crawler not yet implemented (spec §4)", file=sys.stderr)
            return 2
        module = CRAWLERS[vendor]
        if hasattr(module, "make_client"):
            client = module.make_client(run_date)
        else:
            client = PoliteClient(vendor, run_date,
                                  browser_headers=getattr(module, "BROWSER_HEADERS", False))
        started = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        stats = module.crawl(conn, client, run_date, limit=args.limit)
        conn.execute(
            "INSERT INTO crawl_run (vendor, run_date, started, finished, boards, artefacts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (vendor, run_date, started,
             dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
             stats["boards"], stats["new_artefacts"]))
        conn.commit()
    return 0


def cmd_status(args) -> int:
    conn = db.connect()
    for table in ("board", "artefact", "board_artefact", "family"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table:15} {n}")
    print("\nby vendor:")
    for row in conn.execute(
            "SELECT vendor, COUNT(DISTINCT vendor_artefact_id) AS artefacts,"
            " (SELECT COUNT(*) FROM board b WHERE b.vendor = a.vendor) AS boards"
            " FROM artefact a GROUP BY vendor"):
        print(f"  {row['vendor']:10} boards={row['boards']:<5} artefacts={row['artefacts']}")
    print("\nlast runs:")
    for row in conn.execute(
            "SELECT vendor, run_date, boards, artefacts FROM crawl_run"
            " ORDER BY run_id DESC LIMIT 8"):
        print(f"  {row['run_date']} {row['vendor']:10}"
              f" boards={row['boards']} new_artefacts={row['artefacts']}")
    return 0


def _int_stats(stats: dict) -> int:
    return 0 if not stats.get("failed") else 1


def _cmd_deploy(args) -> int:
    if args.print_cors:
        print(deploy.CORS_POLICY, end="")
        return 0
    deploy.run(dry_run=args.dry_run, date=args.date)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="winidx", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("crawl", help="Tier 1: refresh listings and diff (§5)")
    p.add_argument("vendor", nargs="?", choices=sorted(CRAWLERS.keys() | PLANNED))
    p.add_argument("--limit", type=int, help="crawl only the first N in-scope boards")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("status", help="row counts and recent runs")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("fetch", help="Tier 2: download + hash new payloads (§5)")
    p.add_argument("--vendor", choices=sorted(CRAWLERS.keys() | PLANNED))
    p.add_argument("--limit", type=int, help="fetch at most N payloads")
    p.add_argument("--newest-only", action="store_true",
                   help="only the newest artefact per vendor+family (plus unassigned)")
    p.set_defaults(func=lambda a: _int_stats(fetch.run(
        db.connect(), dt.date.today().isoformat(), vendor=a.vendor, limit=a.limit,
        newest_only=a.newest_only)))

    p = sub.add_parser("extract", help="unpack payloads, hash INF/SYS, pull HWIDs (§6.2)")
    p.add_argument("--limit", type=int, help="extract at most N payloads")
    p.set_defaults(func=lambda a: _int_stats(extract.run(db.connect(), limit=a.limit)))

    p = sub.add_parser("assign", help="family assignment via rules + INF cross-check (§6)")
    p.set_defaults(func=lambda a: _int_stats(families.run(db.connect())))

    p = sub.add_parser("publish", help="water level, lag, static JSON output (§7–8)")
    p.set_defaults(func=lambda a: _int_stats(publish.run(db.connect())))

    p = sub.add_parser("deploy", help="sync public/ to Cloudflare R2 (§8)")
    p.add_argument("--dry-run", action="store_true", help="show what would upload")
    p.add_argument("--date", help="snapshot date path (default: today)")
    p.add_argument("--print-cors", action="store_true",
                   help="print the bucket CORS policy and exit")
    p.set_defaults(func=_cmd_deploy)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
