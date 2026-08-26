# WinDriverIndex

A public, machine-readable index of motherboard driver versions across MSI,
Gigabyte, ASRock, and ASUS — and the **vendor lag metric**: how far behind the
newest available driver each vendor's board listings sit.

Design: [doc/spec.md](doc/spec.md). Empirical findings: [doc/findings.md](doc/findings.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/); Python 3.14 is provisioned automatically.

```sh
uv sync
uv run pytest
```

## Usage

```sh
uv run winidx crawl [vendor]   # Tier 1: refresh listings (metadata only)
uv run winidx fetch            # Tier 2: download + hash new driver payloads
uv run winidx extract          # unpack, hash INF/SYS, pull HWIDs
uv run winidx assign           # rule-based family assignment + INF cross-check
uv run winidx publish          # water level, vendor lag -> public/v1/*.json
uv run winidx status           # row counts and recent runs
```

All four vendor crawlers are implemented. ASRock needs browser-harvested
Incapsula cookies in `data/asrock_cookies.txt` (see doc/findings.md);
currently only pg.asrock.com is unlocked — visit www.asrock.com in a browser
and refresh the cookie file to cover the main catalogue. Raw responses are snapshotted under `data/raw/{vendor}/{date}/`
before parsing — the SQLite DB (`data/index.sqlite`) is always rebuildable
from snapshots without re-crawling, and re-running a crawl the same day
resumes from its snapshots.

## Principles

- Index only, never redistribute driver binaries (spec §8).
- Polite crawling: sequential, ≥1.5 s between same-host requests, honest
  User-Agent wherever the vendor's edge allows it (Gigabyte's does not — see
  findings).
- Windows 11 x64 scope; AM4/AM5 and Intel 600/700/800 desktop boards.
