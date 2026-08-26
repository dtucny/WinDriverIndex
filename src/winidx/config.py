"""Paths, identity, and politeness settings shared by every stage."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("WINIDX_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PAYLOAD_DIR = DATA_DIR / "payloads"
DB_PATH = DATA_DIR / "index.sqlite"
PUBLIC_DIR = PROJECT_ROOT / "public"

USER_AGENT = "WinDriverIndex/0.1 (driver index crawler; +https://github.com/dtucny/WinDriverIndex)"

# Seconds between requests to the same host. Politeness, not necessity.
MIN_REQUEST_INTERVAL = 1.5
REQUEST_TIMEOUT = 60
