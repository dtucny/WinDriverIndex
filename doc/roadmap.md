# Roadmap

Where the index goes after v0.1. Two axes: **truth** (a truer water level for
the boards already indexed) and **breadth** (more device classes and vendors).
Truth first — every breadth item inherits whatever the water level gets right.

## v0.2 — a true water level

### Groundwork: schema (do first, one migration)

- `artefact.source_type`: `vendor` (default) | `upstream`. Upstream artefacts
  **raise the water level** but are **excluded from vendor-lag denominators** —
  they are the reference line, not a competitor being scored.
- `board.product_type`: `motherboard` (default) | `laptop` | `desktop` |
  `graphics-card` — ready for breadth items without a second migration.
- Publish outputs gain `upstream` flags; vendor-lag definition unchanged
  except measured against the (now higher) water line. Expect every vendor's
  numbers to get worse — that is the point. Schema version bumps to 1.1.0.

### 1. Windows Update Catalog as an upstream source (keyed by HWID)

The only practical public source for Realtek/MediaTek "latest", and it is
queryable by HWID — which the index already extracts from every INF.

- Crawler: catalog.update.microsoft.com search per representative HWID
  (strip `&SUBSYS`, one or two per family), parse newest driver version +
  date per HWID. Server-rendered ASPX, POST paging, no auth; politeness as
  usual. No payload downloads needed — metadata only.
- Store as `vendor='wucatalog'`, `source_type='upstream'`, linked to families
  through the existing HWID sets (family assignment is already solved).
- Caveat to publish: WU coverage is incomplete in both directions (spec §1),
  so the upstream line is a strong floor, never gospel; always labeled.
- Dashboard gains: "even the best-maintained board is N days behind what
  Windows Update already ships."

### 3. Silicon-vendor direct (AMD / NVIDIA / Intel) as upstream

Authority for the biggest families, complementing WU's breadth:

- **NVIDIA**: clean AJAX driver-lookup service (gfwsl.geforce.com) — easiest;
  mostly matters for v0.3 graphics cards but trivially cheap to record now.
- **AMD**: chipset-driver and Adrenalin pages — versions on fixed URLs; some
  bot protection expected (we have the playbook: curl_cffi → camoufox).
- **Intel**: Download Center per-component pages (chipset INF, ME, RST, Wi-Fi,
  Arc/Xe graphics).
- Same `upstream` treatment as WU. Where WU and direct disagree, publish the
  max and keep both rows — disagreement is itself signal.

### 4. BIOS currency and the AGESA water level

Mostly analysis of data already captured: 6,526 BIOS artefacts are in the DB
(Gigabyte 3,909, MSI 2,617, ASRock partial), 1,760 with AGESA strings in
their descriptions ("AGESA ComboV2 1.2.0.12").

- Add ASUS BIOS crawl (GetPDBIOS endpoint, same auth-free API family).
- Parse AGESA versions out of BIOS descriptions → per-socket AGESA water
  level (AGESA is the cross-vendor comparable BIOS component on AMD; Intel
  microcode mentions are too unstructured to promise).
- Metrics: days since last BIOS per board; AGESA lag vs newest AGESA seen
  for that socket; "boards whose final BIOS predates the last security-era
  AGESA" once the data shows where those lines are.
- Publish `bios.json` + dashboard section. BIOS artefacts stay out of the
  driver water level entirely.

### 2. Lenovo (laptops first, desktops cheap to add)

First breadth item, and the cheapest vendor ever: public, machine-readable
catalogs, hashes included, no anti-bot (doc/findings.md "Lenovo" section).

- Enumerate models/machine types from the deployment catalog
  (download.lenovo.com/cdrt/td/catalogv2.xml), then per-model package lists
  via the same per-MTM API Lenovo Legion Toolkit consumes.
- `product_type='laptop'` (desktops = same pipeline, different model filter —
  include ThinkCentre/Legion towers if the catalog makes it free).
- Scope line to draw at build time: which lines/generations (suggest: Legion
  + ThinkPad/ThinkCentre, current-ish generations, mirroring the AM4/12th-gen
  cutoff philosophy).
- Payoff: laptop listings join the same silicon families (Realtek audio,
  Intel Wi-Fi, MediaTek radios), thickening the water level for everyone —
  and vendor-lag gets its first laptop column.

### 7. "How's my motherboard doing?" — visitor board picker

Interactive page on the live site: pick vendor → board (search-as-you-type
over `boards.json`), see that board's per-component currency exactly like the
dashboard's best/worst chips — every family, its listed version, the water
level, and the lag — plus deep links (`/board.html#asrock/B650M-HDV-M.2`) so
people can share "look how my board is doing".

- Needs one new publish output: per-board lag detail (`/v1/latest/by-board/
  {board_id}.json` or one boards-lag.json — pick by size; the per-board files
  mirror the by-hwid pattern and cache immutably).
- Pure static + client-side fetch; no backend, CORS already open.
- Later tier (after WU/upstream lands): a "check my actual PC" mode — paste
  the output of the §11 PowerShell one-liner and the page matches installed
  driver versions/HWIDs against the water line client-side. That is the
  end-user product the spec's motivating story wanted.

## v0.3 — breadth

### 5. Graphics cards

The vendor APIs already take a product-line parameter (Gigabyte line 3 = VGA,
MSI `product_line=vga`, ASUS/ASRock equivalents). One driver family per GPU
vendor, so the interesting metric is **AIB lag vs the NVIDIA/AMD reference**
from item 3 — which is why 3 lands first.

### 6. HP / Dell

Both publish structured catalogs in the Lenovo mold:
- **Dell**: one CatalogPC.cab from downloads.dell.com — a single XML with
  every package, version, date, and hash. Possibly the cheapest crawl of all.
- **HP**: HPIA reference files / CVA metadata per platform.
Same laptop/desktop pipeline as Lenovo once `product_type` exists.

## Explicitly not planned

- Redistributing payloads (unchanged, spec §8).
- Pre-AM4 / pre-12th-gen back-catalogue.
- A "should you update" recommendation engine — the index reports published
  facts, the §7 caveat stands.

## Sequencing

```
v0.2:  schema groundwork → WU Catalog (1) → silicon-direct (3) → BIOS/AGESA (4)
       → board picker (7) → Lenovo (2)
v0.3:  graphics cards (5) → Dell → HP (6) → "check my actual PC" picker tier
```

1/3/4 before 2: truth before breadth — Lenovo's numbers should be computed
against the corrected water line from day one, not recomputed after. Within
v0.2, WU first because it moves the most families at once through HWIDs the
index already holds. The board picker (7) slots after the truth items so the
first thing visitors compare against is the corrected line — but it has no
hard dependency and can be pulled forward if wanted.
