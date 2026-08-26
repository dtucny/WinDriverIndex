# Motherboard Driver Index — Project Specification

**Status:** Design complete, no code written yet.
**Audience:** Claude Code, picking this up cold.

---

## 1. Why this exists

There is no public, machine-readable source of truth for "what is the current
driver version for a given piece of motherboard silicon". Nobody publishes it.
The practical consequences are real:

A machine shipped with a MediaTek RZ616 Wi-Fi driver dated 2024-06-29
(v3.4.0.1063). Windows Update never offered a newer one. MSI's support page for
that board never listed a newer one. The current version — v3.5.0.1380, dated
2026-01-16 — had been available for nineteen months. The old driver leaked
kernel pool allocations continuously and its companion Bluetooth service leaked
2.5 million handles in five days, consuming ~25 GB of RAM on a 64 GB machine and
forcing a reboot every three days.

That failure was invisible. There was no way to ask "is my driver current?"
short of manually checking a vendor page that was itself stale.

**The index answers two questions:**

1. Given a driver family, what is the newest version *any* vendor ships?
2. Given a board, how far behind that line is its vendor's listing?

The second question, aggregated, produces a **vendor lag metric** — which
vendors keep their listings current and which abandon them. That metric does not
currently exist anywhere and is arguably the most valuable output.

### Prior art and why it doesn't cover this

- **Snappy Driver Installer Origin (SDIO)** — closest existing thing.
  Community-curated driverpacks with a public index. Scriptable, can run
  report-only. But: the index is hand-curated, "latest" means "latest in SDIO's
  collection" rather than latest from the vendor, and it's effectively one
  maintainer. Also currently hard to reach from some regions (see §9).
- **Windows Update catalogue** — has driver metadata by HWID, but coverage is
  incomplete in both directions: vendors publish things there they don't ship on
  their own sites, and ship things on their sites they never submit.
- **Vendor "check for updates" tools** — MSI Center, Armoury Crate, etc. Each
  covers one vendor, none are trustworthy, several bundle adware.

---

## 2. Scope

### In scope

- **Vendors:** MSI, Gigabyte, ASRock, ASUS.
- **Product line:** Desktop motherboards only.
- **Platforms:** AMD AM4 (A320–X570), AMD AM5 (A620–X870E), Intel 600/700/800
  series (12th gen onward).
- **OS:** Windows 11 x64 only. Windows 10 reached end of support on
  2025-10-14; a stale-driver warning for an unsupported OS helps nobody.
- **Artefact types:** drivers primarily. BIOS, utilities and firmware are
  available from the same endpoints and should be captured where cheap, but the
  driver index is the deliverable.

### Out of scope (for v1)

- Laptops, GPUs, and other product lines. The vendor APIs generalise to these
  (usually a single product-line parameter), so leave the door open, but don't
  build for it.
- Pre-AM4 / pre-12th-gen hardware. Gigabyte's catalogue reaches back to the
  GA-386PS and ASRock's to 1995; none of it is relevant.
- **Redistributing driver binaries.** Publish the index, not the payloads.
  Vendor EULAs generally prohibit redistribution, and mirroring turns a static
  site into a bandwidth and legal liability.

### Expected magnitude

- ~150–250 in-scope boards per vendor, so 600–1000 boards total.
- **But only ~15 distinct driver families.** The silicon set is small: AMD
  chipset, AMD graphics, AMD RAID + RAIDXpert2, Intel chipset/ME/GNA, Realtek
  audio, Realtek LAN, Intel I225/I226 LAN, MediaTek Wi-Fi, MediaTek Bluetooth,
  Realtek Wi-Fi, Realtek Bluetooth, ITE/ASMedia USB, plus each vendor's own
  utility.
- Unique artefacts across all four vendors: order 500–800, with a few versions
  of each family in circulation.

This ratio — hundreds of boards, ~15 families — is the entire justification for
the project. The dedup is enormous and the resulting table is small enough to
reason about.

---

## 3. Data model

Three entities. Keep them separate; conflating boards and artefacts is the
obvious modelling mistake.

### Artefact

A single downloadable file as published by one vendor.

```
artefact_id          synthetic
vendor               msi | gigabyte | asrock | asus
vendor_artefact_id   vendor's own ID where one exists (see §4)
family_id            FK → Family (assigned, see §6)
version_raw          version string exactly as published
version_normalised   parsed, comparable (see §6.3)
release_date         as published
file_size            bytes
url                  download URL
sha256               where the vendor publishes one
md5                  where derivable (Gigabyte ?v=, unverified)
os_raw               OS string as published
is_beta              parsed from title/description
description_raw      HTML as published
description_text     stripped
first_seen           when this crawl first observed it
last_seen            most recent crawl that still listed it
```

### Board

```
board_id             synthetic
vendor
vendor_product_id    see §4 per vendor
name                 display name
slug                 URL path component
revision             where the vendor treats revisions as distinct products
chipset              A620 | B650 | X870E | Z790 | ...
socket               AM4 | AM5 | LGA1700 | LGA1851
release_date         where available
support_url
```

### Board↔Artefact

Many-to-many. This is where the water-level query lives.

```
board_id
artefact_id
listed_date          the date the vendor's listing shows for this pairing
```

### Family

The abstraction that makes the index useful. A family is "AMD chipset driver"
or "MediaTek RZ616 Wi-Fi", independent of vendor packaging.

```
family_id
name                 human-readable
silicon_vendor       amd | intel | realtek | mediatek | ite | asmedia | oem
component            chipset | graphics | lan | wlan | bluetooth | audio |
                     storage | usb | npu | utility
hwids                []  populated from INF extraction where available
```

Family assignment is the one genuinely hard part. See §6.

---

## 4. Vendor sources

All four are reachable without authentication. Three are JSON. Rate-limit every
one of them: sequential, 1–2 s between requests, honest User-Agent identifying
the crawler and linking to the project. Being the polite crawler matters — a
single IP hammering a vendor CDN gets range-blocked quickly.

### 4.1 MSI — JSON, publishes SHA-256

Best metadata of the four.

```
# Filter taxonomy for a product line (id=8 is motherboards)
GET https://www.msi.com/support/ajax/get_tag_list_by_product_line?id=8&_token={csrf}

# Products for a tag (2247 = MAG series, product_line=mb)
GET https://www.msi.com/support/ajax/get_product_by_tag?id={tag}&product_line=mb&_token={csrf}

# Artefacts for a product
GET https://www.msi.com/api/v1/product/support/panel?product={slug}&type={type}&os={os}
```

- `type` takes `driver`, `bios`, `utility`, `firmware`. Likely also `manual`.
- `os` takes `Win11 64`, `Win10 64`. Omit for BIOS.
- `product` is the `link` field from `get_product_by_tag` (e.g.
  `MPG-B650I-EDGE-WIFI`).
- **`_token` is Laravel CSRF**, session-bound, required only on the two
  `/support/ajax/` endpoints. Fetch a support page first, scrape the token,
  reuse for the product enumeration. The `panel` API needs no token — so cache
  the product list and run the bulk crawl tokenless.

Response quirks:

- `download_sha256` is `"SHA-256:{hex}<br>"` — strip prefix and the `<br>`.
- `download_id` is a **global chronological ID space** shared across drivers,
  BIOS and utilities (~32767 in Jan 2026 → ~84592 in Jul 2026). Same package
  shared across boards carries the same ID, so this is the primary within-vendor
  dedup key.
- Some entries have **no `download_id`** (external links to Google Play, MS
  Store). `download_size: 0` and empty hash. Filter these out.
- `os` is polymorphic: array for drivers, `false` for BIOS.
- `youtube_link` is `null` on some entries and `false` on others in the same
  array.
- Beta marked as `(Beta version)` suffix in `download_version`.
- Board revisions are separate products with separate slugs
  (`MAG-B850M-MORTAR-MAX-WIFI` vs `...-Rev-2`).

### 4.2 Gigabyte — JSON, structured facets, no hash

```
# All motherboards, one call, no token, no pagination
GET https://www.gigabyte.com/iisApplicationNuxt/api/proxy/api/v1.0/Support/global/DownloadCenter/2/GetProducts

# Facets (useful for chipset mapping)
GET .../DownloadCenter/2/GetFirstProperty     → series (AORUS, AERO, ...)
GET .../DownloadCenter/2/GetSecondProperty    → chipsets (P3V319 = AMD B650)

# Everything for one product, one call
GET https://www.gigabyte.com/iisApplicationNuxt/api/proxy/api/v1.0/Consumer/global/GetProductTabDataAsync/Support/{productId}
```

`2` is the motherboard product line. Other lines: 3 = graphics cards, 5 =
laptops, 53 = memory, 54 = SSDs, 104 = mini PCs.

Two calls per product and you have everything: driver, BIOS, utility, manual,
FAQ, CPU support.

Strengths:

- **`info[]` gives structured categorisation** nobody else provides:
  - `infoParentId 32` → component (Audio, Chipset, LAN, WLAN+BT, USB, SATA
    RAID/AHCI)
  - `infoParentId 35` → OS (112 = Win10 x64, **147 = Win11 x64**)
  - `infoParentId 36` → language (manuals)
- `fileName` encodes driver-slot ID + silicon + version:
  `mb_driver_674_realtek8852wifi_6001.16.172.0`. The `674` is stable across
  boards sharing that driver — within-vendor dedup key.

Weaknesses:

- **No published hash.** The `?v={32 hex}` query param on download URLs looks
  like MD5 — **verify this by downloading one file and hashing it.** If it
  matches, Gigabyte dedups for free.
- BIOS descriptions are HTML `<ol>` with a `Checksum:` field — that's the BIOS
  image checksum, not the zip.

### 4.3 ASRock — HTML, but publishes SHA-256 and content-addressed URLs

Server-rendered ASP. Parse the table.

```
# Latest updates across all products (not the full catalogue)
GET https://www.asrock.com/support/index.asp?cat=Drivers

# Full artefact list for one board — static HTML
GET https://www.asrock.com/mb/{AMD|Intel}/{Model Name}/Download.html
GET https://pg.asrock.com/mb/{AMD|Intel}/{Model Name}/Download.html
```

The single best structural property of any vendor here: **download URLs are
already content-addressed by artefact, not by board.**

```
https://download.asrock.com/Drivers/All/Bluetooth/MediaTek_Bluetooth(v1.1044.0.556).zip
https://download.asrock.com/Drivers/AMD/CPU/Chipset(v8.03.25.247).zip
https://download.asrock.com/Drivers/All/WLAN/MediaTek_WLAN(v3.5.0.1349).zip
```

Every board referencing the same driver points at the identical URL. Dedup is
free before hashing. **Worth testing whether `download.asrock.com` is directory-
listable** — if so, the whole catalogue may be enumerable without touching board
pages at all.

Also: **SHA-256 published on every entry**, inline in the description cell.

Quirks:

- Model slugs mangle punctuation inconsistently: `A620M-HDV/M.2` →
  `A620M-HDVM.2` (slash dropped) but `B650M-H/M.2+` → `B650M-HM.2+` (plus
  retained). **Key on the URL, never reconstruct it from the display name.**
- Spaces in paths are literal, unencoded.
- Phantom Gaming boards live on `pg.asrock.com`, same path structure.
- Version embedded in description after `ver:`; some are messy
  (`9977.1_UAD_WHQL`, `XB560NF_v18.4038.2510.0902`).
- Beta prefixed `[Beta]` in the description.

### 4.4 ASUS — JSON, publishes SHA-256

```
# Series for motherboards (typeid=1156)
GET https://www.asus.com/support/api/product.asmx/GetPDLevel?website=ph&type=1&typeid=1156&productflag=0

# Products in a series
GET .../GetPDLevel?website=ph&type=2&typeid={seriesId}&productflag=1

# OS list for a product
GET .../GetPDOS?website=ph&model={slug}&pdhashedid={hash}&pdid={id}&cpu=

# Drivers, per OS
GET https://www.asus.com/support/webapi/ProductV2/GetPDDrivers?website=ph&model={slug}&pdhashedid={hash}&pdid={id}&cpu=&osid=52
```

`osid=52` is Windows 11 x64 (45 = Win10 x64, 8 = Others).

- Products come back as `PDId` + `PDHashedId` + `PDName`. **`PDHashedId` is
  empty for newer products** but `GetPDSupportTab` accepted the bare `pdid` in
  its place — the hash appears not to be validated. Test whether `GetPDDrivers`
  behaves the same.
- Richest per-artefact metadata of the four: `sha256`, `Reboot`, `Ac_power`,
  `Severity`, `installTime`, `UserSession`, `comboPackage`.
- `website=ph` is a region code; substitute as needed. Content appears
  identical across regions but this is worth spot-checking.

---

## 5. Crawl architecture

### Two-tier

**Tier 1 — metadata, weekly, cheap.**
Hit the listing endpoints. Compare against last run. Emit a diff: new artefacts,
version changes, boards whose listings changed. This is the ongoing job and it
downloads nothing.

**Tier 2 — payloads, on demand.**
Only when Tier 1 surfaces an artefact not already in the store. Download, hash,
extract INF, record. One-off for the initial backfill, then rare.

### Storage

Content-addressed by SHA-256. Keep one copy per unique payload; the crawl
metadata (which vendor, which board, which listed version, when seen) is the
index over the top. Discard duplicate payloads after hashing.

Given ~500–800 unique artefacts and packages ranging 400 KB to 1.26 GB, expect
tens of GB. Trivial.

### Bandwidth

Not a constraint. The dev environment has 1.5 Gbit aggregate. Do not build
Range-request cleverness to avoid downloads — just fetch and hash. **Do**
rate-limit per vendor out of politeness, not necessity.

---

## 6. The equivalence problem

This is the hard part and the reason the project has value.

### 6.1 Cross-vendor identity is NOT derivable from published hashes

Confirmed empirically. AMD chipset driver **v8.03.25.247**, same version string,
four vendors:

| Vendor | Size (bytes) | SHA-256 prefix |
|---|---|---|
| MSI | 74,283,959 | `e38e4840…` |
| Gigabyte | 74,750,640 | (not published) |
| ASRock | ~71.2 MB | `92af132d…` |
| ASUS | ~78.25 MB | `62581D67…` |

Every vendor repackages. Different sizes, different hashes, same underlying
driver. **Payload-level dedup works within a vendor and fails across vendors.**

### 6.2 Therefore: INF-level identity

Unpack each archive, locate the `.inf` files, hash INF content plus the
referenced `.sys` binaries. Two vendors' packages with matching INF+SYS hashes
are the same driver regardless of wrapper.

This also yields the HWID sets, which are what a family *actually is*. Populate
`Family.hwids` from here.

Practical notes:

- Archives are `.zip`. Some contain nested installers (`.exe` self-extractors,
  MSI packages) — handle at least one level of nesting.
- Some "drivers" are pure installers with no INF (utilities, Armoury Crate,
  MSI Center). Family-assign those by name/vendor heuristics; they're not
  really drivers.
- The AMD graphics package is 1.26 GB and contains many INFs. Expect this and
  don't choke on it.

### 6.3 Version normalisation

Version strings are inconsistent across vendors *for the same driver*:

- MSI: `3.05.00.1380` — zero-padded segments
- ASUS/Gigabyte: `3.5.0.1380`
- ASRock: `XB560NF_v6001.16.175.0` — vendor prefix
- Realtek audio: `9977.1_UAD_WHQL`, `6.0.9520.1_Nahimic` — suffixes carrying
  meaning
- Beta markers embedded rather than flagged

Normalise to a comparable tuple; **retain the raw string always**. Where a
version won't parse, fall back to release date for ordering and flag it.

Note the MSI/ASUS example above: `3.05.00.1380` and `3.5.0.1380` are the same
driver. Naive string comparison will report a false discrepancy.

### 6.4 Bootstrap order

Do MSI and Gigabyte first. They have the cleanest metadata and the largest
catalogues, so they seed the family table. ASRock's content-addressed URLs then
dedup almost for free against what's already there. ASUS last.

---

## 7. The water level metric

The primary output. For each family:

```
water_level(family) = max(version) across all vendors, all boards
water_level_date(family) = earliest date that version appeared anywhere
```

Then per board/family pairing:

```
lag_days = water_level_date - listed_date_of_boards_version
```

Aggregate to vendor level:

- median lag across all boards × families
- p90 / worst case
- **proportion of boards >365 days behind on at least one family** — the most
  damning single number
- cut by board age: does the vendor maintain its back catalogue, or only the
  current generation? AM4 boards are the test.

### Two distinct failure modes, worth separating

- **Slow to publish.** Ships current eventually, months late. Annoying,
  self-correcting.
- **Abandoned listing.** Board hasn't been updated since launch while siblings
  on the same chipset get current drivers. This is the one that hurts, because
  the board is still sold and still in service.

### Caveat to publish alongside the numbers

Newest is not automatically best. A vendor may legitimately withhold a driver
that regressed. Say so — once, as a footnote. It does not excuse nineteen months
on a leaking Wi-Fi driver, and the caveat is not a reason to suppress the metric.

---

## 8. Publishing

- **Index only. Never the binaries.** Metadata (versions, dates, hashes, HWID
  sets, which vendors ship what) is factual and safely distributable. Link to
  the vendor's own download URL.
- **Static JSON on a CDN**, not a service. No runtime, no scaling, no attack
  surface, and anyone can mirror it. This also means the project survives the
  maintainer losing interest — the failure mode that has killed every prior
  attempt in this space.
- **Version the schema from day one.** People will build against it.
- Primary query shape: **by HWID** → known versions with dates and sources.
  That's the thing that doesn't exist. Everything else is presentation.
- State plainly what "latest" means: what vendors *published*, not what is
  *good*.

### Suggested outputs

```
/v1/families.json        family table with HWIDs
/v1/artefacts.json       all artefacts, all vendors
/v1/boards.json          board catalogue with chipset/socket
/v1/water-level.json     current newest version per family
/v1/vendor-lag.json      the metric
/v1/by-hwid/{hwid}.json  point lookup
```

---

## 9. Known obstacles

- **glenn.delahoy.com (SDIO) is unreachable from Philippine Converge
  connections.** TCP never establishes; packets reach the host's network
  (AS45638, Synergy Wholesale, Sydney) and die at the final hop. Reachable via a
  different ISP path. Almost certainly a CGNAT-range block at the hosting
  provider's edge. Affects `110.232.143.0/24` and `103.252.152.0/22`. Relevant
  only if you want to compare against SDIO's index; route around it.
- **Converge's routing to AU is pathological** — Manila → Singapore → Tokyo →
  Seattle → Portland → Sydney, 337 ms vs 160 ms on the alternate path. Not a
  blocker, but if crawls to AU-hosted resources are slow, that's why.
- **MSI's `_token`** expires. Handle re-fetch.
- **Gigabyte's `?v=` hash is unverified.** Test it before relying on it.
- Vendor site redesigns will break parsers. ASRock (HTML) is most fragile;
  the three JSON APIs are more stable but undocumented and unversioned.

---

## 10. Build order

1. **Verify the two open questions.** Is Gigabyte's `?v=` an MD5 of the payload?
   Is `download.asrock.com` directory-listable?
2. **Gigabyte crawler.** Cleanest API, structured component facets, full product
   list in one call. Seeds the schema.
3. **MSI crawler.** Adds published SHA-256 and a second view of the same
   families.
4. **Family assignment.** At this point you have two vendors' worth of
   artefacts; build the INF extraction and clustering. This is where the design
   gets validated or doesn't.
5. **ASRock, then ASUS.** Mostly resolving against families that already exist.
6. **Water level + lag computation.**
7. **Static publishing pipeline.**
8. **Weekly diff job.**

Do not build 6–8 before 4 works. If INF-level clustering turns out unreliable,
the whole premise needs rethinking and everything downstream is wasted.

---

## 11. Secondary application

Once the index exists, a client-side checker is trivial and is arguably the
end-user product:

```powershell
Get-CimInstance Win32_PnPSignedDriver |
  Select-Object DeviceName, DeviceID, DriverVersion, DriverDate
```

Match `DeviceID` HWIDs against the index, report anything below the water line.
Schedule it, push results to a monitoring system, and the class of failure that
started this project becomes visible within a week instead of nineteen months.

Worth pairing with symptom monitoring, which is driver-agnostic and catches the
next leak whatever causes it:

```powershell
# worst handle-count offender
(Get-Process | Sort-Object HandleCount -Desc | Select-Object -First 1).HandleCount

# non-paged pool
(Get-Counter '\Memory\Pool Nonpaged Bytes').CounterSamples.CookedValue
```

Alert above ~50,000 handles or ~2 GB non-paged pool. Either would have fired
within a day of the MediaTek driver misbehaving.