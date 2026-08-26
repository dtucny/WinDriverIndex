# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The
published data schema is versioned separately (see `schema_version` in every
`public/v1/*.json` file).

## [Unreleased]

### Added
- `winidx deploy`: sync `public/` to Cloudflare R2 via rclone, writing an
  immutable dated snapshot (`/v1/{date}/`) and updating `/v1/latest/` with
  the right Cache-Control and content types. Credentials are read only from
  the environment; `--print-cors` emits the bucket CORS policy. Wired into the
  weekly `ops/` unit as an optional final step.

## [0.1.0] — 2026-08-26

First working end-to-end pipeline across all four in-scope vendors.

### Added
- Five-stage CLI pipeline: `crawl`, `fetch`, `extract`, `assign`, `publish`
  (plus `status`), each stage a subcommand over a rebuildable SQLite store.
- Snapshot-first crawling: every listing response is written under
  `data/raw/{vendor}/{date}/` before parsing, so parsers re-run against
  history without re-crawling and interrupted runs resume for free.
- Vendor crawlers for **Gigabyte, MSI, ASUS, ASRock**:
  - Gigabyte/MSI reach their APIs through Chrome-TLS impersonation (Akamai
    fingerprint filtering); ASUS accepts an honest User-Agent.
  - ASRock listings are behind Imperva Incapsula — crawled via a headless
    Camoufox browser (primary) with a browser-cookie relay fallback.
- Content-addressed payload store (SHA-256) with within/cross-run dedup; a
  `--newest-only` fetch mode caps ASUS/ASRock deep version history.
- INF-level identity extraction: unpack (7-Zip, nested installers/MSI to
  three levels), hash INF+SYS, extract HWIDs / DriverVer / class.
- Rule-based family assignment with HWID/INF cross-checks: generation splits
  (MediaTek Wi-Fi 6E vs 7), rebadge folding (AMD RZ-series → MediaTek),
  evidence-based reassignment, and a bundle whitelist for packages that
  legitimately ship another family's INF.
- Version normalisation that compares zero-padded/prefixed/suffixed strings
  numerically while retaining the raw string.
- **Water-level and vendor-lag metrics**, preferring INF `DriverVer` over the
  listing version when a vendor renumbers the same driver.
- Static, schema-versioned JSON output under `public/v1/` including per-HWID
  point-lookup files.
- Weekly refresh systemd units in `ops/`.

### Known limitations
- ASRock listing access depends on a working Camoufox install or fresh
  session cookies; a few ASRock payloads are repacked in place and no longer
  match their published SHA-256 (computed hash is authoritative).
- Family assignment is rule-based and hand-seeded; three MSI generic
  "BlueTooth Driver" entries resolve only via INF evidence.
- Laptops, GPUs, and pre-AM4/pre-12th-gen hardware are out of scope for v1.

[Unreleased]: https://github.com/dtucny/WinDriverIndex/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dtucny/WinDriverIndex/releases/tag/v0.1.0
