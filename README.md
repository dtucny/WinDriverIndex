# WinDriverIndex

A public, machine-readable index of Windows driver versions across
motherboards (MSI, Gigabyte, ASRock, ASUS), laptops and desktops (Lenovo,
Dell), and graphics cards — and the **vendor lag metric**: how far behind the
newest available driver each vendor's listings sit.

## 🔗 Live index — https://windriverindex.tucny.com

Static JSON, CORS-enabled, served from Cloudflare. Start here:

| Endpoint | What it is |
|---|---|
| [`/v1/latest/water-level.json`](https://windriverindex.tucny.com/v1/latest/water-level.json) | Newest known version per driver family |
| [`/v1/latest/vendor-lag.json`](https://windriverindex.tucny.com/v1/latest/vendor-lag.json) | The vendor-lag metric |
| [`/v1/latest/families.json`](https://windriverindex.tucny.com/v1/latest/families.json) | Family table with HWID sets |
| [`/v1/latest/boards.json`](https://windriverindex.tucny.com/v1/latest/boards.json) | Device catalogue (chipset / socket / product type) |
| `/v1/latest/by-hwid/{hwid}.json` | Point lookup by hardware ID |

`latest/` tracks the newest crawl; pin an immutable dated snapshot at
`/v1/{YYYY-MM-DD}/…` for stability. Every file carries a `schema_version`.
"Latest" means the newest version any vendor has *published*, not a judgement
that it is good (see the caveat field in each file).

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
uv run winidx deploy           # sync public/ to Cloudflare R2 (needs env vars)
uv run winidx status           # row counts and recent runs
```

External tools: `7zz` (7-Zip, for `extract`), `rclone` (for `deploy`), and a
Camoufox browser (`uv run python -m camoufox fetch`, for ASRock listings).

## Deployment (Cloudflare R2)

The published JSON is served as static files from R2 behind Cloudflare's CDN
(spec §8) — chosen because the ~33k per-HWID point-lookup files exceed the
per-deployment file caps of static-site hosts, while object storage doesn't
care and R2 egress is free.

One-time Cloudflare setup:
1. Create an R2 bucket.
2. Create an R2 API token (Object Read & Write) → note the access key + secret.
3. Enable public access: connect a custom domain (production) or the bucket's
   `r2.dev` URL (testing).
4. Apply the CORS policy so browser/PowerShell checkers can fetch it:
   `uv run winidx deploy --print-cors` → paste into the bucket's CORS settings.

Then, with credentials in the environment (never commit these):

```sh
export R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=...
export R2_BUCKET=windriverindex R2_PUBLIC_BASE=https://windriverindex.tucny.com
uv run winidx deploy --dry-run   # preview
uv run winidx deploy             # publish
```

Each run writes an immutable dated snapshot (`/v1/{date}/`, cached forever)
and updates `/v1/latest/` (short TTL). Consumers pin a dated path for
stability or follow `latest` for freshness.

**Canonical base URL:** https://windriverindex.tucny.com — e.g. the current
newest-version table is
[water-level.json](https://windriverindex.tucny.com/v1/latest/water-level.json),
and a point lookup lives at `/v1/latest/by-hwid/{hwid}.json`.

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
- Windows 11 x64 scope; AM4 (500-series)/AM5 and Intel 600/700/800 desktop
  boards, Win11-era Lenovo and Dell systems, RTX 30+/RX 6000+/Arc cards.
