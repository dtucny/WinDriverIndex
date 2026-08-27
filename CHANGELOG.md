# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The
published data schema is versioned separately (see `schema_version` in every
`public/v1/*.json` file).

## [Unreleased]

### Added (v0.2 groundwork)
- `artefact.source_type` (`vendor`/`upstream`) and `board.product_type`
  columns with automatic migration; upstream artefacts raise the water level
  but never enter vendor-lag, fetch, or text-rule assignment.
- **Windows Update Catalog as an upstream reference source**
  (`winidx crawl wucatalog`): queried per representative HWID (silicon-VEN
  filtered against bundle contamination), metadata only. First run: WU ships
  newer than every board vendor for 12 of 49 driver families.
- Cross-source version-scheme guard in the water level: when an upstream
  top's major version differs from the vendor top's (MediaTek is 1.x/3.x/5.x
  on vendor sites, year-based 26.x on WU), the release date arbitrates and
  the vendor row wins ties. `water-level.json` gains `upstream_only` and
  `best_vendor_version` fields.
- **Silicon-vendor download pages as upstream sources** (`winidx crawl
  silicon`): AMD chipset, Intel chipset INF / Wi-Fi / Bluetooth, regex over
  public pages, loud-miss on layout changes. With both upstream sources, 14
  of 49 families sit ahead of every board vendor; Intel Bluetooth's level is
  independently confirmed by both (24.60.0.4).

- **BIOS currency and the AGESA water level** (`bios.json` + dashboard
  section): ASUS GetPDBIOS and ASRock BIOS.html crawls added (ASRock BIOS
  rows use a different column layout; MSI BIOS descriptions were being
  dropped), AGESA versions parsed from release notes across all four vendors
  (~7,250 of 17,580 BIOS artefacts), per-line water level (AM5 1.3.0.1c,
  AM4-V2 1.2.0.F) with letters-above-digits ordering and Patch suffixes.
  Headline: BIOS pipelines stay alive even where driver listings were
  abandoned — Gigabyte inverts its driver reputation (28-day median, 96% of
  AMD boards on newest AGESA); ASRock trails again (12% BIOS-silent ≥2 yr).

### Fixed
- Water level no longer misreports families whose packages bundle other
  components: the INF-DriverVer override is now confined to the listing's
  major-version line (an ASUS AMD-chipset zip ships NPU INFs numbered 32.x —
  previously the global max leaked that into the AMD Chipset water level).
- AMD NPU/Graphics/RAID rules now precede AMD Chipset, so ASUS's
  `chipset/amd/npu` URL path assigns to NPU instead of Chipset.

### Known limitation
- Realtek LAN conflates distinct silicon (RTL8111/8125/8126) under
  vendor-specific version schemes (ASUS `1168.x`/`1126.x`/`1125.x`); its
  cross-vendor water level is not yet reconciled (spec §6.3).

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
