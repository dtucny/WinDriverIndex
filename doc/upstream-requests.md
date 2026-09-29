# Consumer requests: status

Replies to the problems and requests from the driver-checker app, checked
against the 2026-09-22 data. The data changes ship as `schema_version` 2.0.0
(see CHANGELOG). README has a short guide to checking a machine against the
index.

| # | Request | Status |
|---|---|---|
| 1 | by-hwid keeps only the last family written | Fixed (breaking) |
| 2 | Family HWID sets too broad | Fixed |
| 3 | Junk HWIDs from INF string keys | Fixed |
| 4 | Generic class-code IDs in family sets | Fixed |
| 5 | JSON body for 404s | Fixed (Cloudflare Worker) |
| 6 | INF-level versions | Added: `infs.json` |
| 7 | Machine-readable scheme rules and lines | Added |
| 8 | SMBIOS match keys on boards | Added for Dell, HP, Lenovo, MSI |
| 9 | Families with no HWIDs | Explained; no new flag |
| 10 | `version_normalised` type; undocumented files | Fixed |
| 11 | Client bundle and manifest | Manifest added; bundle declined |
| 12 | Download hints | Added where a generic driver source exists |

## 1. by-hwid kept only the last family: fixed

The report was correct: 15,836 of 59,138 HWIDs belonged to more than one
family, and each file held whichever family was written last. Each file now
lists every family the HWID identifies:

```json
{"hwid": "PCI\\VEN_10EC&DEV_8125", "family": "Realtek 8125 LAN",
 "families": [{"family_id": 88, "family": "Realtek 8125 LAN",
               "match_share": 0.731, "water_level": {...}}]}
```

`match_share` is the fraction of that family's packages whose INFs carry the
HWID, and the list is sorted by it. `known_versions` moved to
`by-family/{family_id}.json`. Repeating it in every HWID file had made
by-hwid 9.1 GB (now about 40 MB), which is why this is a breaking change.
Publish also deletes HWID files that no longer exist; about 6,300 stale files
were still being served.

## 2. Family HWID sets too broad: fixed

The diagnosis was right, but a "per INF" fix alone would not work. Realtek's
`rt640x64.inf` and `rt25cx21x64.inf` really do bind 8168, 8125, 8126 and
several other chips in one file. Family sets now follow three rules
(`families.hwid_support`):

- **Chip-specific IDs go to their own family.** `DEV_8125` belongs to Realtek
  8125 LAN only, whatever package carried it. The same applies to the
  MediaTek generation splits.
- **An INF counts only for families that ship it consistently.** It must
  appear in at least half as large a share of a family's payloads as in the
  family that ships it most. The LAN INF inside 1 of about 30 Realtek Wi-Fi
  packages no longer counts for Wi-Fi.
- **Preinstall variants pool with their full family.** The full family has
  the water level; the preinstall variant never does.

Shared HWIDs fell from 15,836 to about 5,200. The ones left are real sharing:
- Intel Wi-Fi and Killer ship the same driver.
- Realtek chips with no generation anchor (such as `DEV_8136`) are bound by
  every LAN generation's INF.

Realtek Wi-Fi went from 11,212 HWIDs to 201. No water level changed.

## 3. Junk HWIDs: fixed

The filter drops `[Strings]` keys (`….DEVICEDESC`) and registry-path
fragments (`PCI\PARAMETERS`). It also drops `PCI\VID_…` tokens: PCI devices
report `VEN_`, so those can never match a real device. It applies at
extraction and when reading INF rows already in the DB, so nothing needs
re-extracting.

A blanket "no dots" rule would have been wrong: software-component IDs such
as `SWC\AMDOCL-23.19` are real.

## 4. Generic class-code IDs: fixed

IDs with no device part are left out of family sets and lookups. Examples:
`PCI\CC_010802`, `PCI\VEN_8086&CC_0108`, `USB\CLASS_01`,
`HDAUDIO\FUNC_01&VEN_10EC`.

IDs such as `PCI\VEN_8086&DEV_4D28&CC_0401` stay: they name one device, the
class code is just extra detail. Most of the 207 entries you counted were
this kind. Only 18 family HWIDs were truly generic, including the
`PCI\CC_010802` that caused the "AMD RAID" match on NVMe drives.

## 5. JSON 404s: fixed (Cloudflare Worker)

The 27 KB HTML page was Cloudflare's default 404 for the R2 custom domain.
Custom Error Rules are paid-plan only and the zone is on the Free plan, so a
Worker on the route `windriverindex.tucny.com/v1/*` replaces 404 responses
from R2 with:

```
HTTP/2 404
content-type: application/json
cache-control: public, max-age=300
access-control-allow-origin: *

{"error":"not_found"}
```

All other responses pass through unchanged. The Worker is not in this repo;
it is managed in the Cloudflare dashboard.

The zone's Browser Cache TTL was also wrong. At 4 hours it overrode shorter
origin values, so misses were cached for 4 hours and every `latest/` file
went out with `max-age=14400`. Since the setting changed to "Respect Existing
Headers" (2026-09-28):
- `latest/` files carry `max-age=300`;
- dated snapshots carry `max-age=31536000, immutable`.

## 6. INF-level versions: added (`infs.json`)

`infs.json` has one row per (INF file name, DriverVer):

```json
{"inf_name": "rt640x64.inf", "driver_ver": "10.080.50.0407",
 "driver_ver_normalised": [10, 80, 50, 407], "driver_date": "2026-04-07",
 "class": "Net", "family_ids": [87, 88], "artefact_ids": [53396, 53436],
 "first_published": "2026-09-07", "hwids": ["PCI\\VEN_10EC&DEV_8125", "..."]}
```

To check an installed driver, compare `driver_ver_normalised` with the
newest row that has the same `inf_name`. Windows shows DriverVer fields as
numbers (`10.079…` appears as `10.79…`), so compare the arrays, not the
text.

Other notes:
- **HWIDs have SUBSYS/REV removed.** Every device also reports that shorter
  form. Removing them takes the data from 94 MB to about 3 MB.
- **253 rows have `inf_name: null`.** These INFs come from MSI packages
  where the file name is lost on extraction; match them by HWID.
- **`family_ids` is every family whose packages carry the INF**, including
  ones it only rides along in. It is not the ownership set from #2.

## 7. Scheme rules and lines: added

- **`families.json` `version_equiv`** is the same rule the index applies
  internally, so the translation isn't duplicated: `{pattern, replace,
  flags, note}`. `replace` uses `$1`-style references, valid in JavaScript
  and .NET. Only NVIDIA (INF → marketing version) and Realtek Audio (ASRock's
  bare UAD build numbers) have rules.
- **`water-level.json` `lines`** has, for each major-version line:
  `{major, newest, newest_normalised, newest_date, first_date, last_date,
  water_line, parallel_to_water}`. `parallel_to_water` uses the same rule as
  the board pages: a line is parallel when it was published during the same
  period as the water's line (AMD 25.x vs 32.x). A line that ended before
  the water's began is just an older version of the same scheme.

AMD's INF-to-Adrenalin mapping is a lookup table, not a formula, so it gets
no rule. The index uses the "Windows Driver Store Version" from AMD's release
notes instead.

## 8. SMBIOS match keys: added for Dell, HP, Lenovo and MSI

`boards.json` now has `smbios`:

| Key | Vendor | SMBIOS field | Match | Coverage |
|---|---|---|---|---|
| `system_sku` | Dell | SKU Number | exact | 685 of 713 (the rest have left Dell's catalog) |
| `baseboard_product` | HP | Baseboard product (platform ID) | exact | all 675 |
| `system_product_prefix` | Lenovo | Product name (MTM, e.g. `21K9CTO1WW`) | starts with | all 612, with every sibling machine type |
| `baseboard_product_contains` | MSI | Baseboard product (`MS-7D75`) | substring | 361 of 362 motherboards |

Where one model has several IDs, all of them are listed; a Dell model can
have several system IDs. Several MSI boards share one code (MEG X570 ACE and
UNIFY are both MS-7C35), so check the name as well.

Gigabyte, ASUS and ASRock publish no identifier beyond the model name. Their
SMBIOS baseboard product is roughly the marketing name, so those boards still
match by name. The index does not publish "keys" it hasn't verified.

## 9. Families with no HWIDs: explained, no new flag

An empty `hwids` list already is the "board only" signal, so there is no
separate flag. The 16 empty families have three causes:

- **HP and Lenovo packages are indexed from catalog metadata only**, never
  downloaded. This covers the notebook multi-silicon families, WWAN, and
  most of Intel ISH and Card Reader.
- **Installers 7-Zip cannot unpack.** Intel Platform Performance and Dell
  Update Package EXEs for ISH, card reader, UCSI and WAPI.
- **Packages that contain no INF.** GPU VBIOS and the USB-audio firmware
  tool.

Fetching HP SoftPaqs and Lenovo payloads for these families is the fix, and
it is a crawler job for a later refresh.

## 10. Consistency: fixed

`artefacts.json` `version_normalised` is now an array. `artefacts.json`,
`changes.json`, `infs.json`, `manifest.json` and `by-family/` are now listed
on the homepage and in the README.

## 11. Client bundle and manifest: manifest added, bundle declined

`manifest.json` lists `{bytes, sha256}` for every aggregate file, plus file
counts for the per-HWID, per-family and per-board directories. A client
revalidates this one small file and re-downloads only files whose hash
changed.

A separate `client-index.json` would repeat `families.json`,
`water-level.json` and `infs.json` (about 7 MB together), and could drift
out of sync with them. With the manifest, loading those three files costs
one request when nothing has changed. If size becomes a problem, compression
is the better fix: R2 serves brotli/gzip to clients that ask for it.

## 12. Download hints: added where a generic source exists

`families.json` `download_hint` is set for:
- NVIDIA Graphics: NVIDIA App / driver search
- AMD Graphics, Chipset and RAID: AMD drivers page and Adrenalin
- The Intel graphics, Wi-Fi, Bluetooth, LAN, chipset-INF and RST families,
  plus Killer Wi-Fi and Bluetooth: Intel Driver & Support Assistant

Everything else (Realtek audio and LAN, MediaTek, Intel ME/DTT/ISH/Serial IO)
is customised by the OEM and has no generic driver for end users. For those,
`download_hint` is null; point users at the board's `support_url` from
`boards.json` or `by-board`.

# Second round (data 2.0.x)

## Two DriverVer spellings for one build: folded

Confirmed as one build. Dell ships `rt640x64.inf` both as
`10.080.0407.2026` and as `10.080.50.0407`, and the `rt640x64.sys` inside
both packages is byte-for-byte the same size with FileVersion
`10.080.0407.2026`; only the INF stamp differs. Realtek's LAN INFs show this
in 9 cases (`rt640x64`, `rtots640x64`, and the `rt25/26/27/68(d)cx21x64`
NetAdapterCx INFs).

`infs.json` now keeps one row per build, under the `X.Y.50.MMDD` spelling,
with the other in `driver_ver_aliases`. Match an installed version against
`driver_ver` or any alias.

The fold applies only to Realtek's two patterns, with the same INF, date and
`X.Y`. A general "same INF, same date" rule would be wrong: Intel stamps
every chipset INF 1968-07-18 across genuinely different versions.

## Same INF name, different numbering: MediaTek's year line now reachable

The 25.x/26.x builds were in the index already, as Lenovo listings (willow's
25.40.2.579 against Lenovo's newest 25.40.2.586). Lenovo is indexed from
metadata only, so those rows had no INF evidence and stayed in the unsplit
"MediaTek Wi-Fi" family, which `DEV_0616` doesn't resolve to.

They are now routed by the chip their title names:
- MT7920/21/22 and RZ6xx go to Wi-Fi 6E;
- MT7925/27 and RZ7xx go to Wi-Fi 7;
- Bluetooth is routed the same way.

That moved 137 listings. For `DEV_0616`, MediaTek Wi-Fi 6E's `lines` now has
a 25.x line (newest 25.40.2.586) marked `parallel_to_water`. willow's board
page shows 25.40.2.586 as the same-line newest.

The year numbering is a second scheme for the same drivers, and nothing
ties a 25.x build to a 3.x one. So:
- year-numbered builds never set these families' water level;
- they are always a parallel line.

A client on a year-numbered build should compare within its own line.

`infs.json` still has no year-numbered `mtkwl6ex.inf` rows. Those need the
payloads, which come from Lenovo or Windows Update. Neither is downloaded
today, and WU's first result page for `DEV_0616` lists only 3.5.0.1392.
Fetching them is the same crawler job as #9.

## AMD 31.x vs 32.x: not parallel; per-device answer is in infs.json

Right: 31.x was not parallel. It ended with 31.0.24028.1001 in June 2024,
a week after 32.0.11002.41 appeared. It was flagged because line spans used
every listing date, and a vendor re-listed a 31.x package in 2026. Spans now:
- use each version's first appearance;
- end at the newest version's first appearance;
- count as parallel only after 90 days of overlap.

Across all families, most spurious "parallel" flags are gone.

For a Raphael iGPU (`PCI\VEN_1002&DEV_164E`), the per-device answer is the
newest `infs.json` row binding that ID in the installed INF's series (AMD
display INFs are renamed every release, so series is `u*.inf`). That row is
**32.0.21043.5001** (2026-03-03), not the family water 32.0.31041.1004. AMD's
newest 26.x-branch INFs (32.0.31xxx) don't list RDNA2 devices at all, so the
family water overstates what such a device can install. No per-device flag
is needed: `infs.json` answers it.

# Third round (data 2.0.x)

## BIOS version, not just date: added

`by-board` `bios` now has `last_bios_version`: the vendor's version string
for the newest **stable** BIOS, with `last_bios_version_date` its publish
date (`last_bios` still counts betas as activity, so the two dates can
differ). willow: `M3CN50WW`, 2026-08-07. The README lists how each vendor's
string relates to SMBIOS `BIOSVersion`. It is an exact match for ASUS,
Gigabyte, ASRock, Dell and Lenovo Legion/LOQ. HP's SMBIOS string ends with
it. MSI needs a transform: the index publishes the listed name `7D73v1L2`,
which SMBIOS reports as `1.L2`. Lenovo Think catalog strings are mixed
BIOS/EC forms, so match on a token.

Two fixes came with it. ASUS lists Intel ME update tools and audio firmware
in its BIOS category, so "newest BIOS" could have been an ME tool
(`16.1.40.2765v3`). Those rows no longer count for BIOS versions or dates.

On dates: Lenovo publishes three for this package. The web page shows the
file date, **03 Aug** (pcsupport `Date`). System Update's descriptor, which
Vantage reads and the index uses, says **07 Aug**. The document was last
modified on 20 Aug. None of them is the build date, 2026-04-23, that
Windows reports. A version match avoids all of them; keep the 180-day date
window only as a fallback for boards without a version.

The MSI example is one release behind: MSI published `7D73v1L3` for the
MPG B650I EDGE WIFI on 2026-09-09.

## Lenovo INFs: agreed, same cause as #9

Correct. Lenovo is indexed from catalog metadata, so its customised INFs
(`hdxacplv.inf`) have no `infs.json` rows. Staying within the installed
INF's series and falling back to the package level is the right behaviour.
A cross-OEM chip-ID match picks another vendor's INF with its own
numbering. The fix is fetching Lenovo payloads. The pcsupport listing
already carries each file's URL and SHA-256, so the crawler can do this.
It is still scheduled for a later refresh.

## "Newer than the index": three causes, two fixed in the data

WILLOW and MAHOGANY exports, 2026-09-29: 14 INFs installed above the index's
newest `infs.json` row.

- **Stale index (NVIDIA `nvlti.inf` 32.0.16.1714 = 617.14).** NVIDIA
  released 617.14 on 2026-09-22, the day of the last crawl. The refresh
  picks it up.
- **Windows Update components (Realtek `realtekservice.inf`, `realtekhsa.inf`,
  `realtekusbapo.inf`; Nahimic `a-volutenhapo4swc.inf`).** Windows Update
  updates software components on their own, ahead of any board vendor's
  package. `infs.json` now has Windows Update Catalog rows for them
  (`sources: ["wucatalog"]`). Several of these machines were in fact
  **behind**: MAHOGANY's Realtek service 1.0.1026.0 against 1.0.1033.0,
  WILLOW's Nahimic 4.11.4.0 against 4.15.4.0 for its ID
  (`SWC\VEN_AVOL&AID_0802`). One Nahimic INF has OEM builds up to 5.0.9.0
  under other IDs, so filter by the device's ID before taking the newest.
- **AMD chipset components (`amdgpio2`, `amdi2c`, `amdpsp`, `amdinterface`).**
  AMD's chipset installer unpacks at run time, so no INF could be read from
  it, and `infs.json` had only what board vendors repackage. AMD's release
  notes list every component's Windows 11 version; those rows are now in
  `infs.json` (`sources: ["amd-release-notes"]`). WILLOW's versions are
  exactly AMD's 8.08.12.551 set.
- **AMD graphics components (`u*.inf`, `amdocl`, `amdogl`, `amdvlk`,
  `amdwin-u*`) at 32.0.31041.1004.** That is the family water:
  AMD's own Adrenalin 26.9.1, which the index knows only as a version, not
  as INFs. No board vendor has shipped it yet. When the installed version
  equals `family_newest` and the family's version is in the INF scheme
  (AMD Graphics is, via the release notes' Driver Store version), report
  current rather than newer.

## "Not in the index": mostly out of scope; a few fixed

Of 270 (WILLOW) and 287 (MAHOGANY) unindexed devices, 229 and 257 run
Microsoft inbox drivers. Most of the rest are outside a board index:
VPN/virtual adapters (OpenVPN, Tailscale, ZeroTier), printers, Logitech,
RØDE, Blackmagic, Xbox, NVIDIA Broadcast, monitor INFs, Bluetooth headsets.

Handled now:
- **AMD chipset extras** `amdppkg.inf`, `amd3dvcache.inf`,
  `amdappcompat.inf`, `amdgpio3.inf` (PT GPIO): from the AMD release notes
  (above). MAHOGANY's `amdppkg.inf` 8.0.0.61 is behind 8.0.0.65.
- **AMD HD Audio bus `amdafd.inf`**: indexed, but it binds only
  vendor-qualified class IDs (`PCI\VEN_1002&CC_0403`), which the #4 filter
  dropped. `infs.json` keeps them for INFs with nothing more specific.
  Match on the device's compatible IDs.

Not handled:
- **Lenovo components** (`acpivpc`, `lenovofnandfunctionkeys`, `imdriver`,
  `udc*`, `fbnetfilter`), Fortemedia, Nahimic VADs, and the Lenovo camera and
  fingerprint drivers: Lenovo payloads are not fetched (#9).
- **AMD Crash Defender `amdfendr.inf`**: it binds a root-enumerated ID
  (`ROOT\AMDLOG`), which the INF scan doesn't collect. It ships inside the
  AMD graphics package and tracks its version.
- **MSI `msiswacpi.inf`**: MSI ships it in its utility installers, not a
  driver package.

Peripherals are outside what a board index can enumerate: it has no list
of their IDs. Windows Update has about half of the ones on these two
machines. The SunplusIT camera is behind (5.0.18.203 installed,
5.0.18.269 on the Catalog). Logitech's G HUB driver is ahead of Windows
Update. The fingerprint readers and monitors have no Catalog entry. For
these, ask Windows Update from the client:
- **Windows Update Agent** (`Microsoft.Update.Session`, search
  `Type='Driver' and IsInstalled=0`): the drivers offered to this machine,
  with its hardware-ID and machine targeting applied. This is the
  authoritative answer for anything Windows Update supplies.
- **Catalog search by hardware ID** as a fallback: public and cheap, but it
  lists every OEM's build of an ID, not the one this machine would get.


## Windows Update offers (WILLOW, 2026-09-29): agreed

- **Firmware capsule 1.50.0.0 for "System Firmware".** Don't guess from
  dates. The `UEFI\RES_{guid}` device's installed firmware version is on the
  device (firmware-version property) and in the ESRT
  (`HKLM\HARDWARE\UEFI\ESRT\{guid}`). A capsule's version uses the same
  encoding. When they match, the offer re-packages the installed BIOS
  (Windows ranks Lenovo's INF above the generic `c_firmware.inf`), so don't
  count it as an update.
- **Universal Device Client 26.8.0.29.** Lenovo component bound to
  `ROOT\UDSUDCDRIVER`. The index doesn't collect `ROOT\` IDs and Lenovo
  payloads aren't fetched, so Windows Update is its only source.
- **Nothing offered for the 7 behind.** Microsoft targets OEM builds at
  machines. The same applies to `infs.json` rows whose only source is
  `wucatalog`: they are builds Windows Update ships to some OEM's machines,
  not necessarily this one. Label them as such ("newer build exists, another
  OEM, via Windows Update").

## Realtek LAN water from Realtek itself (2026-09-29)

Realtek 8168/8125/8126 LAN water now comes from Realtek's own download
page: `1168.31.50` / `1125.31.50` / `1126.31.50` (NetAdapterCx package
`11.031.50`, 2026-08-28). No board vendor ships build 31 yet
(`upstream_only`). The page omits the build number, so treat a shorter
water version as a prefix: an installed `1125.31.50.x` is current, not
newer. The files are CAPTCHA-gated, so there are no `infs.json` rows for
them.

