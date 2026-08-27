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

- **“How’s my motherboard doing?” board picker** (`/board.html`): search
  any of the 1,124 boards, get a verdict, per-family listed-vs-newest table
  with component grouping and upstream tags, BIOS/AGESA status, and
  shareable deep links. Backed by new per-board publish output
  (`/v1/latest/by-board/{id}.json`); dateless upstream water levels fall
  back to first-observed dates for lag.

- **Lenovo (Think + Legion/LOQ) in the index**: 619 machines, 15,732
  listings via the three-layer public catalog crawl; laptop-era families
  added (NVIDIA Graphics, WWAN, Camera, Card/Fingerprint Reader, ISH, OEM
  audio). Combo multi-vendor "WLAN Driver" bundles stay unmatched by design.
- **Scope-doubling recrawl** after the form-factor-suffix fix: the published
  index now covers 2,724 machines / 64 families / 6,786 driver artefacts.
- Rule pass now clears stale family assignments when a row stops matching;
  Lenovo's `/consumer/` URLs no longer trip Gigabyte's ME pattern; Intel
  PROSet/Killer/Realtek bundle relationships whitelisted in the INF check.

- **Dashboard is now data-driven**: `publish` emits `dashboard.json` with
  every figure the landing page shows, and the page renders client-side from
  it — hardcoded-number staleness is structurally gone. Lenovo appears in
  all views; best/worst entries deep-link to the picker.

### Fixed
- Water-level integrity hardening: a majority-version-line guard stops
  vendor mislabels from topping a family with a foreign scheme (ASUS ships
  graphics 31.0.101.x and chipset 10.1.x packages titled 'Intel GNA Driver',
  which had poisoned 639 boards' worst-lag); slash-combo versions can no
  longer set water where clean rows exist; Lenovo's 'Wireless LAN' phrasing
  routes to Realtek Wi-Fi instead of Realtek LAN. Intel GNA's water is now
  3.5.0.1611, agreed by five vendors.
- Lag no longer reads "current" for behind versions that a vendor re-listed
  after the water rose: the publication-gap formula went negative and was
  clamped to 0 (found via MSI re-listing AMD RAID 9.3.3.218 days after
  9.3.3.329 shipped on WU). A behind pairing now lags at least as long as
  the newer version has existed.
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
