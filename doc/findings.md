# Empirical findings

Answers to the spec's open questions plus operational discoveries, dated.
Spec references: doc/spec.md §9–10.

## 2026-08-26 — spec §10 step 1 probes

### Gigabyte `?v=` IS the payload MD5 — confirmed

Downloaded `PreInstall_9.3.3.117.zip` (385,737 bytes) from
`download.gigabyte.com/FileList/Driver/...?v=3e6276410f8eaa97f1b9a614a75325c0`;
local `md5sum` matches the `?v=` value exactly. Gigabyte artefact identity is
therefore free at Tier 1 — no download needed for within-vendor dedup or
change detection.

### ASRock `download.asrock.com` is NOT enumerable

- It is S3 behind CloudFront (`server: AmazonS3`, `x-amz-*` headers,
  `x-amz-cf-pop: MNL52-P1`).
- Directory paths (`/Drivers/`, `/Drivers/All/WLAN/`) return HTTP 200 with an
  **empty body** — no index page. `/Drivers/All/` oddly 403s.
- S3 `ListObjectsV2` (`/?list-type=2&prefix=...`) returns a custom 404, so
  bucket listing is blocked at the edge.
- Conclusion: the catalogue must be reached through board Download.html pages,
  as the spec's fallback assumed. Silver lining: S3 `ETag` on single-part
  objects is the payload MD5 (multipart ones carry a `-N` suffix and are not),
  and `last-modified` is served — HEAD requests give cheap change detection.

### Gigabyte www is behind Akamai TLS-fingerprint filtering

- Plain curl/httpx → 403 site-wide regardless of User-Agent
  (`errors.edgesuite.net` reference page).
- Chrome-impersonated TLS via `curl_cffi` → 200.
- Chrome TLS **with an overridden honest UA** → 403: Akamai checks UA/TLS
  consistency. The listing crawler must present full Chrome identity; the
  honest-UA policy is applied where vendors allow it.
- `download.gigabyte.com` (the payload CDN) accepts plain clients with an
  honest UA — Tier-2 fetches stay honestly identified.

### Gigabyte catalogue shape

- `GetProducts` (product line 2) returns 3,278 motherboards in one ~236 KB
  call; entries are just `productId`/`skuId`/`productName`.
- Name-based chipset scoping (`winidx.scope`) marks 313 boards in scope —
  in line with the spec's 150–250 estimate once revision variants are counted.
- `GetProductTabDataAsync/Support/{id}` matches the spec: `download` tab →
  `child[]` categories (`driver`, `bios`, `utility`, …) → `data[]` entries
  with `fileName`, `fileVersion`, `fileSize`, `fileReleaseDate`, `filePath`,
  and the `info[]` facet list (`infoParentId` 32 = component, 35 = OS with
  147 = Win11 x64, 36 = language).

### MSI (probed after Gigabyte crawl)

- Same Akamai posture as Gigabyte: full Chrome identity required on
  www.msi.com (honest UA → 403 even with Chrome TLS).
- CSRF token via `csrf-token` meta tag on any support page; needed only for
  the ~8 enumeration calls, panel API confirmed tokenless.
- Only filter type 1 (Product Segment) carries tags for motherboards: 7 series
  tags. 448 unique products across them; 177 in scope by name-based chipset
  matching.
- Panel response: `result.downloads` is a dict of
  `{category title: [entries]}` **with stray non-list keys** (`type_title`,
  `os`) mixed in, and occasionally list-of-string values — parser must
  type-check both levels.
- `download_sha256` quirk confirmed exactly as spec §4.1 (`SHA-256:{hex}<br>`);
  `download_id` 32767 = AMD chipset driver, matching the spec's example.
- **MSI download URLs are not version-addressed**
  (`dvr_exe/mb/amd_chipset_drivers_am4_am5.zip`) — the same URL is overwritten
  with new versions over time. `download_id` is the only safe artefact
  identity; never key on URL. Also means a Tier-2 fetch must happen close to
  the Tier-1 observation or the hash may not match the listing.
- The 313-board Gigabyte crawl: 11,435 listings → 4,089 artefacts, of which
  3,909 are per-board BIOS images; **drivers dedup to 150 unique artefacts**
  (each with a distinct MD5). Spec §2's ~15-family/enormous-dedup premise is
  holding: top artefact (Realtek audio 6.0.9927.1) is shared by 220 boards.

## 2026-08-26 — §10 step 4 validation gate: PASSED

Fetched the same-version driver pairs from MSI and Gigabyte and extracted:

- **MediaTek Wi-Fi 7 v5.7.0.4669**: INF sha256 `7e16f455…` and SYS
  `e77858fc…` byte-identical across both vendors, despite different wrappers
  (Gigabyte nests a self-extracting .exe; MSI ships flat).
- **MediaTek Bluetooth v1.1044.0.556**: likewise identical (`5957917e…` /
  `86056ea8…`).
- Payload-level hashes differ everywhere, as §6.1 predicted. INF-level
  identity works; the project premise holds.

Related discoveries:

- **AMD chipset packages are unextractable statically**: a single
  `AMD_Chipset_Software.exe` — custom LZMA-packed PE, no embedded
  cab/zip/7z signatures, unpacks only at runtime. 7-Zip sees a bare PE.
  Family assignment for these rides on the (unambiguous) listing name;
  HWIDs for the AMD chipset family need another source later.
- **MSI reuses/renumbers version strings**: `download_id` 32767 now carries
  `7.12.04.858` but the identical sha256 the spec recorded against
  `8.03.25.247` — consistent with in-place URL overwrites. Also two live
  download_ids (32767, 33274) share a version string but have different
  payloads.
- MSI labels the spec's motivating RZ616 driver "AMD WIFI Drivers"
  (AMD-rebadged MediaTek). Needs INF-evidence merge into the MediaTek Wi-Fi
  family.
- **Known family-rule gap**: MediaTek Wi-Fi 6E (3.x line) and Wi-Fi 7 (5.x
  line) are separate silicon/version lines currently lumped as one family,
  which corrupts that family's water level. Split via HWID sets once the
  backfill extraction completes. Check Realtek Wi-Fi (8852 vs 8922) for the
  same issue.

## 2026-08-26 — ASRock is behind Imperva Incapsula

All listing properties (www.asrock.com, pg.asrock.com, www.asrock.com.tw)
serve an Incapsula JS-challenge interstitial (HTTP 200, ~850 bytes, iframe to
`/_Incapsula_Resource`). Not passed by: plain curl, curl_cffi Chrome
impersonation, or headless Chromium (headless-shell and full, with
webdriver masking, 30 s + reloads). Suspected IP-reputation component —
this connection is in the Converge CGNAT ranges the spec §9 already flags
for one unrelated block. `download.asrock.com` (CloudFront) is NOT
challenged, so Tier-2 payload fetches are unaffected; only listing pages are
gated.

Options, in order of preference:
1. Re-test from a different egress (alternate ISP path per spec §9) — if it
   passes there, this is IP scoring and a scheduled crawl just needs to run
   from that path.
2. Harvest `incap_ses_*`/`visid_incap_*` cookies from a real browser session
   and reuse them in curl_cffi (cookies are long-lived); breaks unattended
   weekly runs but fine for backfill.
3. Stealth-browser forks (patchright/camoufox) if 1–2 fail.

**RESOLVED (same day) via option 2 — browser-harvested cookies.** Cookies
from a real Firefox session pass Incapsula with *plain curl TLS* plus the
harvesting browser's User-Agent — no impersonation needed once the cookies
exist. Convention: `data/asrock_cookies.txt`, line 1 = Cookie header value,
line 2 = the browser's User-Agent. Key detail: each Incapsula site id needs
its own `visid_incap_*` cookie — pg.asrock.com is site 2836327,
www.asrock.com is 2784046 — so visit **both** hosts in the browser before
copying. The crawler skips a host whose cookie is missing and says so.

pg.asrock.com crawled 2026-08-26: 69 in-scope boards, 1,234 listings,
123 unique artefacts (content-addressed URLs dedup exactly as §4.3
promised; SHA-256 inline on every entry; `ClickRate(...)` onclick carries
clean category + version). The download URL *path* is the authoritative
kind taxonomy (/Drivers/, /BIOS/, /Utility/). 'SATA Floppy Image' = F6
preinstall packages; AzureWave-branded radios carry Realtek silicon.
**www.asrock.com — cookie relay does NOT work; camoufox does.** Corrected
finding: replaying real-browser cookies for www always re-challenges — the
Incapsula session is bound to the solving browser's TLS fingerprint. (An
earlier "curl reuse works" note was a misread: the 300-char preview cut off
before the `_Incapsula_Resource` marker; it was the challenge page.) Three
cleared-www-cookie pastes from the user all re-challenged through curl,
confirming binding.

**Working path: headless Camoufox** clears both hosts unaided and instantly.
Two gotchas resolved:
- The browser it fetches (152.0.4-beta.29) hits a
  `Browser.setDefaultViewport` protocol error on `new_page()` with camoufox
  0.4.11 + playwright 1.62. Fix: create the page in a `no_viewport=True`
  context.
- `camoufox fetch` has no version pin and pulls latest; keep package+browser
  in step, and use the no_viewport workaround.

Crawler ships **camoufox primary, cookie-relay fallback**. Snapshots are
client-agnostic, so pg's cookie-crawled pages cache-hit and only www is
browser-fetched on the switch-over.

Tier-2 note: 4 of 29 ASRock payloads hash-MISMATCH their published SHA-256
(Realtek_Audio v9879.1, Floppy v9.3.3.245, ASMedia_SATA3 v3.3.5, ASMedia_USB4
v1.0.0.0) — the file at the content-addressed URL was evidently repacked
after the page hash was written. So ASRock URLs are version-addressed, not
truly content-addressed; treat the published hash as advisory and keep the
computed one authoritative. The four rows keep their published hash and the
payloads are stored under their computed hash for later reconciliation.

First four-vendor publish (828 boards, 55 families): ASRock's Phantom
Gaming listings are dramatically stale — median lag 617 d, 67/69 boards
>365 d behind (pg subset only; the www catalogue may differ).

## 2026-08-26 — ASUS crawl

- Honest User-Agent accepted (no TLS gate, unlike MSI/Gigabyte).
- `PDHashedId` confirmed not validated — empty string works on GetPDDrivers
  (spec §4.4 open question closed).
- **`Id` and even download URLs vary per board for the same payload**:
  13,451 listings resolved to only 503 distinct published sha256 values and
  1,869 distinct URLs. Artefact identity = published sha256 (fallback: URL
  basename); never the Id.
- **ASUS lists deep version history**: 1,108 unique driver artefacts totalling
  183 GB — far beyond the spec's estimate, because every historical version
  stays listed. Tier-2 policy for ASUS: fetch newest per (vendor, family)
  plus unassigned only (`winidx fetch --newest-only`); the metadata rows
  carry versions/dates for the lag metric without payloads.
- Naming needs its own rule set ('AMD RZ616 Wi-Fi' = rebadged MediaTek,
  'MD RAID' typo, 'MTK'/'QCA'/'RTK' abbreviations, 'Marvell' = Aquantia
  AQtion). After normalising `_`→space and matching URL basenames:
  1,314/1,317 drivers assigned, 0 INF conflicts; only MSI's 3 bare
  'BlueTooth Driver' entries stay parked for INF evidence.

## 2026-08-26 — INF-evidence pass and first full publish

- Depth-2/3 extraction + content-sniffing (7z flattens .msi contents to
  extensionless file-table stream names) recovered INFs from installer-style
  packages. MSI's three bare 'BlueTooth Driver' entries resolved to **Intel
  Bluetooth** purely by HWID overlap.
- Generation splits are HWID-anchored (MediaTek Wi-Fi 6E DEV_0608/0616/7902
  vs Wi-Fi 7 DEV_0717/0738; BT via USB PIDs), with same-version adoption and
  a version-major fallback. AMD RZ-series families fold into the MediaTek
  subfamilies through the same pass. 'MTK ACX' turned out to be a separate
  BT LE-Audio companion driver line (mtkbtacx.inf), also bundled inside main
  BT packages.
- Known bundle relationships (Killer ⊃ Intel Wi-Fi + I225, chipset INF ⊃
  serial-io/GNA, DTT ⊃ IPF, BT ⊃ LE-Audio) are whitelisted in the INF
  cross-check; everything else flags. Final state: 1,314/1,317 rule-assigned
  + 3 evidence-assigned, **0 conflicts**.
- **Vendor renumbering caught and corrected**: AMD repacks MediaTek BT with
  its own version scheme (listing '1.8240.169' for the canonical '1.10xx'
  line), which falsely topped the 6E water level. Publish now uses each
  payload's INF DriverVer whenever INF evidence exists and none of it
  matches the listing version.
- First three-vendor publish (759 boards, 53 families, 26,868 HWID files).
  Vendor lag v0 (interpret with care until ASRock lands and preinstall/beta
  handling is reviewed): median lag ASUS 37 d, MSI 120 d, Gigabyte 158 d;
  boards >365 d behind on ≥1 family: Gigabyte 291/313, ASUS 225/269,
  MSI 65/177.

## 2026-08-26 — full four-vendor index (ASRock www included)

Camoufox unlocked www.asrock.com: ASRock 69 → 365 boards. First complete
publish: **1,124 boards, 55 families, 1,534 driver artefacts, 35,458 HWID
files, 0 INF conflicts.** Two new bundle relationships whitelisted from
ASRock www payloads: Realtek HD-audio packages bundle the USB-audio
component; Gigabyte ships combined WLAN+BT packages (its BT-only
'mb_driver_640' also needed a specific rule so its 'WLAN+BT' hint stopped
pulling it into Realtek Wi-Fi). 9 more ASRock payloads hash-MISMATCH their
published SHA-256 (in-place repacks, as before) — stored under computed hash,
listing hash kept.

Final vendor lag (all four; still interpret with the §7 caveats):
| vendor | boards | median | p90 | worst | >365d |
|---|---|---|---|---|---|
| asrock | 363 | 172 | 1298 | 2969 | 308 |
| asus | 269 | 45 | 676 | 1631 | 229 |
| gigabyte | 313 | 175 | 1367 | 1655 | 291 |
| msi | 177 | 114 | 340 | 1878 | 65 |

MSI clearly best-maintained (p90 340 d, only 37% of boards >1yr behind);
ASRock/Gigabyte worst (>90% of boards a year-plus behind on ≥1 family).
Store: 35 GB payloads, 744 MB published JSON.

## 2026-08-26 — live on Cloudflare R2

First deploy done. Canonical base: **https://windriverindex.tucny.com**
(bucket `windriverindex`, custom domain). Verified: 200 + application/json,
`access-control-allow-origin: *`, Brotli on the wire, `latest/` = 1h
revalidate, `/v1/{date}/` = immutable. Credentials live in
~/.config/winidx/r2.env (chmod 600, account API token, never committed).

Outstanding CDN tweak: responses show `cf-cache-status: DYNAMIC` — Cloudflare
doesn't edge-cache `.json` by default, so add a Cache Rule (hostname
windriverindex.tucny.com → Eligible for cache, Edge TTL = use cache-control)
to stop every request falling through to R2 and burning Class B ops.

## 2026-08-26 — human-readable landing page

`public/index.html` (self-contained, theme-aware dashboard: vendor-lag
scorecard, back-catalogue heatmap, worst-offender boards, JSON links, GitHub
links) deploys to the bucket root. Serves fine at /index.html.

Two Cloudflare notes:
- **Root `/` 404s.** R2 custom domains don't resolve an index document.
  Fix (one-time, free): tucny.com → Rules → Transform Rules → Rewrite URL →
  when hostname = windriverindex.tucny.com AND URI path = "/", rewrite path
  to /index.html. Serves the page at / without changing the URL.
- **Plain `python-urllib` UA gets 403** (Cloudflare bot protection) while
  curl/browsers work. Fine for humans, but a driver-checker using a default
  UA may be blocked — consider a WAF/bot exception for the /v1/ paths if
  programmatic consumers hit it.

## Related prior art — Lenovo publishes what §1 says nobody does (laptops)

Lenovo Legion Toolkit's "check for driver and software updates" is a Vantage
replacement that reads Lenovo's public, machine-readable update catalogs —
no scraping. Two flavors:
- Per-machine-type (MTM) catalog (what LLT/Vantage/System Update use):
  individual packages (driver/BIOS/firmware/app) with version, date,
  checksum, URL — maps directly onto our artefact model.
- Enterprise deployment catalog https://download.lenovo.com/cdrt/td/catalogv2.xml:
  coarser whole-OS driver *packs* + BIOS per model; carries version, date,
  crc (SHA-256), md5, URL. Verified current (2026-01 entries).

Implications for this project:
- Refines spec §1: the "no machine-readable source of truth" claim is
  vendor-specific. The four in-scope desktop-board vendors don't publish one;
  Lenovo (laptops) does. LLT is a working instance of the §11 client checker —
  but per-vendor only ("latest Lenovo ships for this MTM"), the same blind
  spot §1 critiques in MSI Center / Armoury Crate. Our cross-vendor,
  HWID-family water level is the differentiator LLT/Lenovo lack.
- Cheapest future laptop source (§2 "leave the door open"): clean public
  catalog, SHA-256+MD5 (slots into the content-addressed store and INF dedup),
  no anti-bot (cf. the ASRock Incapsula fight). Would add a laptop-side data
  point for the RZ616 family from the motivating story.
- LLT is GPL — learn endpoints from it, don't reuse code (we'd hit Lenovo's
  catalog directly anyway). Not built; laptops are out of v1 scope.

## Environment notes

- Python 3.14.7 via uv (system python3 is 3.12; `uv python install 3.14`).
- Crawls run from a PH Converge connection; see spec §9 for the AU routing
  pathology if anything AU-hosted is slow.

## Graphics cards (v0.3, 2026-08-27)

- Card enumeration: Gigabyte product line 3 (1,606 products), MSI product
  line 4 / `product_line=vga` (580), ASUS typeid 1233 (1,877). Era gate =
  RTX 30/40/50, RX 6000/7000/9000, Arc A/B (`scope.extract_gpu`; the token
  regex must eat `™` between brand and number — 67→388 in-scope difference).
- **MSI ships no GPU drivers at all**: every one of 477 card driver panels
  is an `os: []` husk plus a `Drivers[].downloads_html` HTML blurb pointing
  at the NVIDIA App / vendor download sites. Only VBIOS + utilities are
  versioned. Not a parser gap — verified 0/477 panels contain a file.
- The three AIB postures: ASUS updates card pages (median worst-lag 65 d),
  Gigabyte freezes them near launch (median 1,228 d), MSI delegates.
- Upstream GPU silicon sources: NVIDIA AjaxDriverService JSON (gfwsl) for
  GeForce, Intel Arc page 785597, AMD Adrenalin page (existing). NVIDIA's
  Windows driver INF scheme is 32.0.15.xxxx (=5xx.xx marketing); Intel's is
  32.0.101.xxxx — same major, so one Dell manifest that titles a GeForce
  package with an Intel-scheme version pollutes the NVIDIA major-32
  same-line footnote (cosmetic, single package).
- ASRock cards (Arc/Radeon) not yet enumerated — different page family.
