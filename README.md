# WinDriverIndex

A public, machine-readable index of Windows driver versions across
motherboards (MSI, Gigabyte, ASRock, ASUS), laptops and desktops (Lenovo,
Dell, HP), and graphics cards — and the **vendor lag metric**: how far behind the
newest available driver each vendor's listings sit.

## 🔗 Live index — https://windriverindex.tucny.com

Static JSON, CORS-enabled, served from Cloudflare. Start here:

| Endpoint | What it is |
|---|---|
| [`/v1/latest/water-level.json`](https://windriverindex.tucny.com/v1/latest/water-level.json) | Newest known version per driver family, with every major-version line (`lines`) |
| [`/v1/latest/families.json`](https://windriverindex.tucny.com/v1/latest/families.json) | Family table: HWID sets, `version_equiv` scheme rule, `download_hint` |
| [`/v1/latest/infs.json`](https://windriverindex.tucny.com/v1/latest/infs.json) | INF-level versions: one row per (INF file, DriverVer) with its HWIDs |
| [`/v1/latest/boards.json`](https://windriverindex.tucny.com/v1/latest/boards.json) | Device catalogue (chipset / socket / product type / SMBIOS match keys) |
| [`/v1/latest/artefacts.json`](https://windriverindex.tucny.com/v1/latest/artefacts.json) | Every indexed driver listing (vendor, version, date, URL, hashes) |
| [`/v1/latest/vendor-lag.json`](https://windriverindex.tucny.com/v1/latest/vendor-lag.json) | The vendor-lag metric |
| [`/v1/latest/changes.json`](https://windriverindex.tucny.com/v1/latest/changes.json) | What the latest refresh changed (water moves, new machines) |
| [`/v1/latest/bios.json`](https://windriverindex.tucny.com/v1/latest/bios.json) | BIOS currency and AGESA water level |
| [`/v1/latest/manifest.json`](https://windriverindex.tucny.com/v1/latest/manifest.json) | Size and sha256 of every file above: revalidate this one first |
| `/v1/latest/by-hwid/{hwid}.json` | Point lookup by hardware ID: every family it identifies |
| `/v1/latest/by-family/{family_id}.json` | One family: water level and every known version |
| `/v1/latest/by-board/{board_id}.json` | Per-machine report (feeds the board picker) |

By-hwid file names replace `\` with `_` and `&` with `+`
(`PCI\VEN_10EC&DEV_8125` → `by-hwid/PCI_VEN_10EC+DEV_8125.json`). A missing
file means no indexed family claims that ID.

### Checking a machine against the index

- **Match devices by HWID.** A device reports several hardware IDs, from most
  to least specific; try each. A by-hwid file lists every family the ID
  identifies, ordered by `match_share` (how consistently that family's
  packages carry it). Generic class-code IDs (`PCI\CC_010802`) are never
  listed: they match inbox Microsoft drivers too.
- **Compare INF versions with INF versions.** Windows reports the installed
  INF's `DriverVer`, which is often not the package version vendors list
  (a Realtek NIC's `rt640x64.inf` 10.79.x ships inside package 1125.x). In
  `infs.json`, take the rows whose `hwids` include the device's ID and whose
  `inf_series` matches the installed INF's, and compare
  `driver_ver_normalised`. `inf_series` is the file name, except for AMD
  display INFs, which are renamed every release (`u0403049.inf` →
  `u*.inf`). Match the installed version against `driver_ver` and
  `driver_ver_aliases`: Realtek stamps some builds two ways
  (`10.080.50.0407` = `10.080.0407.2026`). `infs.json` HWIDs carry no
  SUBSYS/REV qualifiers; every device also reports that shorter form.
  Rows also come from upstream sources with no vendor package (`sources`:
  `vendor`, `wucatalog`, `amd-release-notes`). Windows Update updates
  software components (Realtek service/HSA/APO, Nahimic, Intel DTT) on
  their own, and AMD's chipset installer can't be unpacked, so these rows
  carry versions no board vendor lists. Upstream-only rows have no
  `artefact_ids` and no `driver_date`; `first_published` is the source's
  date. Windows Update rows list only the IDs that version is newest for
  (OEM builds of one INF register different IDs), so filter by the
  device's ID before taking the newest. Some INFs bind only a
  vendor-qualified class ID (`PCI\VEN_1002&CC_0403` for AMD's HD Audio bus);
  a device reports that among its compatible IDs.
- **Compare listings on their own line.** When a family's versions run on
  parallel numbering lines (AMD 25.x packaging vs 32.x INF), `water-level.json`
  `lines` gives the newest per major version; `parallel_to_water` marks lines
  that overlap the water's line in time (by first appearance of each
  version, for at least 90 days; MediaTek's year-numbered 2x.x builds are
  always parallel to its 1.x/3.x/5.x line). `families.json` `version_equiv`
  gives a family's translation rule into its canonical scheme (a regex with
  `$1`-style replacement, `flags` apart), e.g. NVIDIA `32.0.15.9186` = `591.86`.
- **Match machines by SMBIOS**, where `boards.json` has `smbios` keys:
  `system_sku` (Dell; SMBIOS SKU Number, exact), `baseboard_product` (HP
  platform ID; baseboard product, exact), `system_product_prefix` (Lenovo
  machine types; the product name / MTM starts with one),
  `baseboard_product_contains` (MSI board code such as `MS-7D75`; a substring
  of the baseboard product — several boards can share one code). Other
  vendors' boards match by name.
- **Check the BIOS by version, not date.** Windows reports the BIOS's build
  date, which precedes the vendor's publish date by weeks or months.
  `by-board` `bios.last_bios_version` is the newest stable BIOS's version
  string as the vendor lists it (`last_bios_version_date` is its publish
  date; `last_bios` also counts betas). Compare it with SMBIOS
  `BIOSVersion`: exact for ASUS (`1205`), Gigabyte (`F26`), ASRock
  (`3.20`), Dell (`1.28.1`) and Lenovo Legion/LOQ (`M3CN50WW`); HP's SMBIOS
  string ends with it (`U23 Ver. 02.21.00`); MSI lists `7D73v1L2` where
  SMBIOS says `1.L2` (drop the board code and `v`). Lenovo Think catalog
  strings vary (`1.36`, `M1UKT79A_1.0.0.121`, `BIOS1.63_EC1.34`): match
  SMBIOS's image ID (`M1UKT79A`) or its bracketed number (`N2JET92W
  (1.36 )`) as a token.
- **Families with no HWIDs** (`hwids: []`) are drivers whose packages the
  index cannot unpack (installers, firmware tools, or HP/Lenovo packages,
  which are indexed from catalog metadata only); show them via `by-board`.
- **Fetch cheaply.** `manifest.json` carries each file's sha256; re-download
  only files whose hash changed.

`latest/` tracks the newest crawl; pin an immutable dated snapshot at
`/v1/{YYYY-MM-DD}/…` for stability. Every file carries a `schema_version` and
a `license` field.
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
uv run winidx extract          # unpack, hash INF/SYS, pull HWIDs (--retry-quarantined re-tries vendor-corrupt payloads)
uv run winidx assign           # rule-based family assignment + INF cross-check
uv run winidx publish          # water level, vendor lag -> public/v1/*.json
uv run winidx deploy           # sync public/ to Cloudflare R2 (needs env vars)
uv run winidx status           # row counts and recent runs
```

External tools: `7zz` (7-Zip, for `extract`), `rclone` (for `deploy`), and a
Camoufox browser (`uv run python -m camoufox fetch`, for ASRock listings).

## Deployment (Cloudflare R2)

The published JSON is served as static files from R2 behind Cloudflare's CDN
(spec §8) — chosen because the ~60k per-HWID point-lookup files exceed the
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

## License

- **Code** (everything under `src/`, `tools/`, `tests/`, `ops/`, the dashboard
  page): [MIT](LICENSE).
- **Published data** (the JSON under `public/v1/` and at the live index):
  [CC BY 4.0](LICENSE-DATA). Reuse it freely, including commercially, with a
  credit such as "Driver version data from WinDriverIndex
  (https://windriverindex.tucny.com), CC BY 4.0". Every published file carries
  `"license": "CC-BY-4.0"` beside its `schema_version`.

The data is a compilation of facts from vendor support sites. The driver
packages, vendor names and brands stay with their owners; this project
indexes them and never redistributes binaries.

## Principles

- Index only, never redistribute driver binaries (spec §8).
- Polite crawling: sequential, ≥1.5 s between same-host requests, honest
  User-Agent wherever the vendor's edge allows it (Gigabyte's does not — see
  findings).
- Windows 11 x64 scope; AM4 (500-series)/AM5 and Intel 600/700/800 desktop
  boards, Win11-era Lenovo, Dell, and HP systems, RTX 30+/RX 6000+/Arc cards.
