"""SQLite store. The DB is a rebuildable index over the raw snapshots —
losing it costs a re-parse, never a re-crawl."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

from . import config


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # Autocommit + WAL + busy timeout: stages run concurrently, and Python's
    # default deferred transactions deadlock on read->write lock upgrades
    # (SQLITE_BUSY bypasses the busy handler there). Each statement commits
    # on its own; explicit conn.commit() calls become harmless no-ops.
    conn = sqlite3.connect(path, autocommit=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(resources.files("winidx").joinpath("schema.sql").read_text())
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """CREATE IF NOT EXISTS doesn't add columns to existing tables; bring an
    older DB up to the current schema (v0.2: source_type, product_type)."""
    for table, column, ddl in [
        ("artefact", "source_type", "TEXT NOT NULL DEFAULT 'vendor'"),
        ("board", "product_type", "TEXT NOT NULL DEFAULT 'motherboard'"),
    ]:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def upsert_board(conn: sqlite3.Connection, run_date: str, *, vendor: str,
                 vendor_product_id: str, name: str, **fields) -> int:
    cols = {k: v for k, v in fields.items() if v is not None}
    row = conn.execute(
        "SELECT board_id FROM board WHERE vendor = ? AND vendor_product_id = ?",
        (vendor, vendor_product_id)).fetchone()
    if row:
        sets = ", ".join(f"{k} = ?" for k in cols)
        conn.execute(
            f"UPDATE board SET name = ?, last_seen = ?{', ' + sets if sets else ''} "
            "WHERE board_id = ?",
            (name, run_date, *cols.values(), row["board_id"]))
        return row["board_id"]
    keys = ["vendor", "vendor_product_id", "name", "first_seen", "last_seen", *cols]
    vals = [vendor, vendor_product_id, name, run_date, run_date, *cols.values()]
    cur = conn.execute(
        f"INSERT INTO board ({', '.join(keys)}) VALUES ({', '.join('?' * len(vals))})",
        vals)
    return cur.lastrowid


def upsert_artefact(conn: sqlite3.Connection, run_date: str, *, vendor: str,
                    vendor_artefact_id: str, **fields) -> tuple[int, bool]:
    """Returns (artefact_id, is_new). is_new drives the Tier-1 diff report."""
    cols = {k: v for k, v in fields.items() if v is not None}
    row = conn.execute(
        "SELECT artefact_id FROM artefact WHERE vendor = ? AND vendor_artefact_id = ?",
        (vendor, vendor_artefact_id)).fetchone()
    if row:
        sets = ", ".join(f"{k} = ?" for k in cols)
        conn.execute(
            f"UPDATE artefact SET last_seen = ?{', ' + sets if sets else ''} "
            "WHERE artefact_id = ?",
            (run_date, *cols.values(), row["artefact_id"]))
        return row["artefact_id"], False
    keys = ["vendor", "vendor_artefact_id", "first_seen", "last_seen", *cols]
    vals = [vendor, vendor_artefact_id, run_date, run_date, *cols.values()]
    cur = conn.execute(
        f"INSERT INTO artefact ({', '.join(keys)}) VALUES ({', '.join('?' * len(vals))})",
        vals)
    return cur.lastrowid, True


def link_board_artefact(conn: sqlite3.Connection, run_date: str,
                        board_id: int, artefact_id: int,
                        listed_date: str | None) -> None:
    conn.execute(
        """INSERT INTO board_artefact (board_id, artefact_id, listed_date, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (board_id, artefact_id)
           DO UPDATE SET last_seen = excluded.last_seen,
                         listed_date = COALESCE(excluded.listed_date, listed_date)""",
        (board_id, artefact_id, listed_date, run_date, run_date))
