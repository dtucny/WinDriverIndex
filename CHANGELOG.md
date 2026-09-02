# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The
published data schema is versioned separately (see `schema_version` in every
`public/v1/*.json` file).

### Added (unreleased)

- The landing page shows what the latest data refresh changed: water-level
  moves (family, old → new, via, dated) and newly indexed machines, diffed
  by publish against the previously published state. A refresh that changes
  nothing keeps the last meaningful delta on display.

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

- **Realtek LAN split by silicon generation** (8168 GbE / 8125 2.5GbE /
  8126 5GbE) via the HWID-anchor machinery — validated by Realtek's own
  numbering (1168.29.50.202 and 1125.29.50.202 are the same build for two
  chips). **NVIDIA VBIOS/firmware split from the driver family** (95.x vs
  32.x lines).

- **Lenovo per-silicon combo enrichment** via pcsupport's downloads API
  (cookie warm-up + Referer): multi-silicon combo packages split into one
  listing per chip with true per-silicon versions (Realtek 8852BE/CE,
  MediaTek RZ616, Sunplus/Realtek cameras, Genesys/Realtek card readers) —
  1,663 enriched listings, recovering rows the SU catalog hides (e.g.
  Fortemedia audio).

### Changed
- **Scope tightened to era parity** (user-driven): AM4 is now 500-series
  only (A520/B550/X570) — the 300/400-series predate the Intel 600+ cutoff
  by the same margin as the excluded Intel 100–400 chipsets; and Lenovo
  consumer machines below the Windows 11 CPU floor (Kaby/Skylake ikb/isk
  codes plus the 80xx MT block) are excluded. −157 boards, −6 machines; the
  "most neglected" lists now surface genuine in-era neglect (X570 Taichi at
  7.2 yr) instead of museum pieces.

- **Same-scheme comparisons in the picker**: each board row now shows the
  newest version on the listing's own numbering line as the primary target,
  with the cross-scheme family maximum demoted to an explicit "≠ scheme"
  footnote (lag was always date-based and is unchanged). Displayed listing
  versions are now always the vendor's own strings — the INF-canonical
  'effective' version is used only for ordering (it had visibly rewritten
  MSI's 25.10.36 to a bundled component's 25.10.0.4). Ambiguous multi-chip
  Realtek bundles route by version line instead of first-anchor-wins.

- **AMD Adrenalin as an upstream source** (Ryzen CPU download page):
  AMD Graphics water is now the real Adrenalin release (26.8.1, 2026-08-20)
  instead of an ASUS date-as-version listing. Two new water guards:
  year-shaped versions (v2026.04.15) can't set water where real schemes
  exist, and when two upstream sources top different schemes (WU's internal
  WDDM 32.x vs AMD's marketing 26.x) the date-newer one wins. Silicon-page
  date extraction widened (ISO + US formats).

- **Dell consumer lines** via CatalogIndexPC's per-model catalog cabs
  (sha256-addressed, cached across runs — 922 fetched once, 0 on refresh):
  Inspiron, Vostro, Alienware, G-series, XPS Desktop and the 2025 "Dell"
  rebrand join the business lines for 913 systems total. Era gating gained a
  third conjunct — a machine's oldest driver package dates its launch stack
  (restamps can only push dates newer) — separating out-of-charter museum
  hardware from the real finding: Win11-capable 2017-18 consumer machines
  with listings frozen at launch, up to 9.1 years behind water.
- **Dell (business lines) in the index** — the whole vendor from one file:
  Dell Command Update's CatalogPC.cab (chrome-TLS) yields 379 in-era systems
  (Latitude/Precision/OptiPlex/XPS/Dell Pro) and ~2,900 packages with real
  vendorVersions, MD5s, categories and PCI ids; one request per refresh.
  Era gating was an adventure: Dell restamps 2012 packages with fresh
  catalog dates and classes TPM firmware as BIOS, so the gate is numeric
  System-BIOS scheme AND a Win11-era System-BIOS date, plus an 18-name
  denylist of extended-support 6th/7th-gen units. Known limitation: 'XPS
  9350' merges Dell's 2015 and 2024 uses of the name. Consumer lines
  (Inspiron/Alienware/G) need a separate enumeration source (as with
  Legion). Also: Gigabyte boards gained support-page links (703 backfilled);
  the picker shows "N of M — keep typing" when results are capped; publish
  clears stale by-board files.

- **Graphics cards (v0.3)**: 1,343 AIB cards from Gigabyte (385), MSI (477)
  and ASUS (481) — RTX 30/40/50, RX 6000–9000, Arc, era-gated like the
  platforms — with NVIDIA GeForce (616.56) and Intel Arc added as upstream
  silicon sources alongside Adrenalin. Card listings join the existing GPU
  driver families, so every AIB support page is measured against what the
  GPU vendor shipped yesterday. VBIOS listings land in the GPU VBIOS family.
  ASRock's Arc cards are a noted follow-up (separate page structure). Known
  cosmetic quirk: one Dell manifest titles a GeForce driver with an
  Intel-scheme version, nudging the NVIDIA same-line footnote.

- **HP (v0.3)**: 664 commercial systems via HPIA's cloud reference files —
  the same per-platform XML catalog HP Image Assistant and CMSL consume.
  Era gate is HP's own IsWindows11 flag (436 of 603 platforms). EliteBook/
  ProBook/ZBook/desks/Z through the 2025 "G1a" AI PCs; softpaq ids dedupe
  across platforms; the catalog publishes SHA256 so fetch stays
  metadata-only. Consumer lines (Pavilion/OMEN/Victus) have no public
  catalog — documented gap, as with Lenovo consumer.

### Fixed

- Every board now gets a picker page — a card whose vendor lists no
  drivers (MSI's whole GPU catalogue) previously 404'd out of the picker;
  it now says so and shows the silicon vendor's current driver as the
  reference (with a pointer to the NVIDIA App / Adrenalin / Arc Control).
- A systematic water audit caught six more families where a single
  vendor's fringe version line had won the water on a restamped date
  (Camera 81.x, Fingerprint 40.x, Intel ME—kept, see below—Thunderbolt
  61.3, WWAN 18300.x, Realtek Audio's bare-build UAD form). The
  majority-line guard now distinguishes sequential lines (Intel ME's
  year-week majors march 2512→2620: trusted) from parallel ones, where a
  fringe line needs a second vendor's corroboration (Intel RST 21.x,
  listed by Dell and Lenovo, correctly survives).
- ASRock's bare-build Realtek UAD versions (10007.1_UAD) now translate
  to the canonical 6.0.x form for ordering, via the same per-family
  scheme-translator mechanism as NVIDIA INF→marketing.
- Intel's Killer Performance Suite page joins the silicon upstream
  sources (50.26.625.2482).
- The Killer family was one brand over three version schemes (Intel's own
  release table lays it out): the Wi-Fi/Bluetooth drivers on Intel's 2x.x
  lines, Realtek-scheme Ethernet (1168/1125/1126.x), and the Performance
  Suite/Control Center software — a suite version was serving as "family
  newest" over driver listings. Split into Killer Wi-Fi / Killer Bluetooth /
  Killer LAN (Ethernet) / Killer Suite; suite 2.x/3.x versions are separated
  from E3100-era Ethernet drivers structurally (suites always carry a ≥1000
  version component).
- The "≠ scheme max" footnote no longer fires on versions that are merely
  old: a major bump only counts as a parallel line when the two lines were
  published contemporaneously (AMD's 25.x packaging vs 32.x INF overlap for
  years; Intel Bluetooth 21.x simply ended before 24.x began). Where lines
  genuinely run in parallel the label now says so instead of claiming a
  different scheme.
- Intel UHD/HD/Iris graphics packages were landing in the Intel Wi-Fi
  family ("Intel UHD Graphics 630" matched the Wi-Fi rule's 630 token) and
  a graphics version topped the Wi-Fi water; Intel's WAPI driver similarly
  polluted the Wi-Fi 21.x line. Both routed to their own families.
- The AMD Graphics upstream now records the INF-scheme "Windows Driver
  Store Version" from AMD's release notes (32.0.31041.1004 for Adrenalin
  26.8.1) instead of the marketing string, so AMD's own newest orders
  directly against the INF versions board vendors list. AMD's INF↔marketing
  mapping is a lookup table, not an algorithm (31.0.24002.92 = Adrenalin
  23.40.02), so no NVIDIA-style translation is possible.
- Windows Update Catalog queries now prefer the device ids claimed by the
  NEWEST INFs over the most common ones — popularity kept picking legacy
  chips with many subsystem variants.
- NVIDIA versions are now one comparable line: vendors list the package's
  INF DriverVer (`32.0.15.9186`) while NVIDIA speaks marketing (`591.86`);
  the algorithmic translation is applied for ordering and shown alongside
  the raw listing, so a card row reads `32.0.15.9186 = 591.86` against
  water `616.56` instead of two unrelated-looking numbers.
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
