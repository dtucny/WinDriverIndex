"""Family seeding and assignment (spec §6).

Deliberately NOT a clusterer: the family set is small and hand-seeded, each
with ordered regex rules over the artefact's vendor id + title + component
hint. INF evidence (shared inf_sha256 across artefacts) is used as a
cross-check afterwards — a hash shared across two families is a rule bug and
gets reported, never silently merged.

Preinstall/boot-disk variants (F6-style, separate version lines) are split
into their own '<family> (preinstall)' families so they don't pollute the
water-level metric of the full packages.

Rule order matters: bluetooth before wlan (titles like 'MediaTek Wi-Fi 7
Bluetooth Driver' contain both), i225 before generic Intel LAN, etc.
"""

from __future__ import annotations

import json
import re
import sqlite3

# (family name, silicon_vendor, component, [patterns])
RULES: list[tuple[str, str, str, list[str]]] = [
    # Specific AMD components come before AMD Chipset: ASUS files NPU under a
    # 'chipset/amd/npu' path whose normalised text contains 'chipset amd',
    # which the chipset rule would otherwise claim.
    ("AMD Graphics", "amd", "graphics",
     [r"amd graphics", r"amd apu", r"\bapu\b", r"amd vga",
      r"amd.{0,10}graphic"]),
    ("AMD RAID", "amd", "storage",
     # 'md raid' covers ASUS's typo'd 'MD RAID Driver' listings. A generic
     # 'raid driver' pattern is a trap: it caught Intel RST via MSI's
     # 'AHCI/RAID Drivers' component hint. ASRock's F6 'SATA Floppy Image'
     # zips are keyed by their URL path (amd/sata vs intel/sata).
     [r"amd raid", r"am[45] raid", r"raidxpert", r"\bmd raid",
      r"amd/sata/"]),
    ("AMD NPU", "amd", "npu", [r"amd npu", r"npu amd"]),
    ("AMD Bluetooth", "amd", "bluetooth", [r"amd blue", r"amd bt\b"]),
    ("AMD Wi-Fi", "amd", "wlan", [r"amd wi-?fi"]),
    ("AMD Chipset", "amd", "chipset", [r"amd chipset", r"chipset amd"]),

    # NB: Gigabyte's component hint for both radio drivers is 'WLAN+BT', so
    # bluetooth rules must demand the word 'bluetooth', never a bare 'bt'.
    # BT LE-Audio companion (mtkbtacx.inf) has its own version line and is
    # also bundled inside the main BT packages.
    ("MediaTek LE Audio", "mediatek", "bluetooth",
     [r"acx.*le audio", r"mtk acx", r"mtkbtacx"]),
    ("MediaTek Bluetooth", "mediatek", "bluetooth",
     [r"mediatek.*bluetooth", r"mtk.*bluetooth", r"mtkbt", r"mtk bt\b"]),
    # AMD RZ6xx/RZ7xx radios are rebadged MediaTek silicon (ASUS labels them
    # 'AMD RZ616 ...'); rule order keeps them out of the AMD families.
    ("MediaTek Bluetooth", "mediatek", "bluetooth", [r"rz\d{3}.*bluetooth"]),
    ("MediaTek Wi-Fi", "mediatek", "wlan",
     [r"mediatek", r"\bmtk\b", r"mtk6e", r"mtk ?79", r"rz\d{3}.*wi-?fi"]),

    ("Qualcomm Bluetooth", "qualcomm", "bluetooth", [r"qualcomm.*blue", r"qca.*bluetooth"]),
    ("Qualcomm Wi-Fi", "qualcomm", "wlan", [r"qualcomm", r"\bqca\b"]),

    ("Realtek USB Audio", "realtek", "audio", [r"realtek usb audio", r"realtekusb"]),
    ("Realtek Audio", "realtek", "audio",
     [r"realtek hd (audio|universal)", r"realtekdch", r"realtek audio",
      r"audio rtk", r"realtek high definition audio",
      r"audio/realtek\("]),
    # AzureWave is a module maker; ASRock's AzureWave radio packages carry
    # Realtek silicon (same 1.1061.x / 2024.10.x version lines ASUS labels
    # RTK 8821/8822) — INF cross-check verifies.
    # 'realtek bt driver' is Gigabyte's BT-only package (mb_driver_640); it
    # carries a 'WLAN+BT' component hint that would otherwise pull it into
    # Realtek Wi-Fi, so this specific phrase must win here first. A bare
    # 'bt' can't be used — the same hint pollutes the real Wi-Fi package.
    ("Realtek Bluetooth", "realtek", "bluetooth",
     [r"realtek.*blue", r"realtek bt driver", r"rtl88\d+.*bt driver", r"bt rtk",
      r"rtk \d{4}\w* (bluetooth|bt)\b", r"azurewave.*bluetooth"]),
    ("Realtek Wi-Fi", "realtek", "wlan",
     # 'wireless' must be claimed here before the LAN rule's '.*lan' can eat
     # Lenovo's 'Realtek 8922AE Wireless LAN Driver' phrasing
     [r"realtek.*wi-?fi", r"realtek.*wlan", r"realtek.*wireless", r"wifi rtk",
      r"rtl88\d+.*wifi", r"rtk \d{4}\w* wifi", r"azurewave"]),
    ("Realtek LAN", "realtek", "lan",
     [r"realtek.*(pci-e ethernet|lan)", r"realtek8125", r"realtek8126",
      r"realtek.*ethernet", r"\b1168\.\d"]),

    ("Intel I225/I226 LAN", "intel", "lan", [r"i22[56]"]),
    ("Intel I211 LAN", "intel", "lan", [r"i211"]),
    ("Intel I219 LAN", "intel", "lan", [r"i219"]),
    ("Killer LAN", "intel", "lan", [r"killer"]),
    # MSI's '10G Super Lan' boards ship Aquantia/Marvell AQtion silicon;
    # INF evidence will confirm or split this once extracted.
    # Marvell acquired Aquantia; ASUS labels the same AQtion line 'Marvell'.
    ("Aquantia LAN", "aquantia", "lan",
     [r"aquantia", r"10g.*lan", r"marvell (ethernet|lan)", r"lan marvell"]),
    ("Intel LAN", "intel", "lan", [r"intel (lan|network|ethernet)", r"61 intel"]),
    ("Intel Bluetooth", "intel", "bluetooth", [r"intel blue", r"intel bt\b", r"ax2\d\d.*bluetooth", r"\b607\b"]),
    ("Intel Wi-Fi", "intel", "wlan",
     # bare 'Wi-Fi Driver V22.x' on ASUS Intel boards is Intel's 22.x line
     [r"intel.{0,4}wi-?fi", r"ax2\d\d.*wi-?fi", r"wifi intel", r"\b630\b",
      r"wi-fi driver v22\.", r"intel wireless"]),

    ("Intel Chipset INF", "intel", "chipset",
     [r"intel inf", r"infupdate", r"intel chipset", r"intel/inf/"]),
    ("Intel ME", "intel", "chipset",
     [r"management engine", r"\d+ consumer \d", r"intel me\b", r"\bmei\b"]),
    ("Intel IPF", "intel", "chipset", [r"innovation platform"]),
    # must precede the generic GNA rule: ASUS's 'Intel GNA Driver V31.0.101.x'
    # is a graphics-scheme version (see Intel VGA note below)
    ("Intel VGA", "intel", "graphics", [r"gna driver v3\d\.0\.101"]),
    ("Intel GNA", "intel", "npu", [r"gna"]),
    ("Intel NPU", "intel", "npu", [r"intel npu", r"neural processing"]),
    ("Intel DTT", "intel", "chipset", [r"dynamic tuning", r"\bdtt\b", r"\bdptf\b"]),
    ("Intel Serial I/O", "intel", "chipset", [r"serial i/?o", r"serialio"]),
    ("Intel HID Event Filter", "intel", "chipset", [r"hid event", r"\bhid\b"]),
    ("Intel PMT", "intel", "chipset", [r"platform monitoring", r"\bpmt\b"]),
    ("Intel Platform Performance", "intel", "chipset",
     [r"platform performance", r"\bippp\b"]),
    ("Intel VGA", "intel", "graphics",
     # ASUS mislabels at least one graphics package 'Intel GNA Driver
     # V31.0.101.x' — 3x.0.101.x is unambiguously the graphics scheme, and
     # left in GNA it poisons that family's water level (and, via the
     # behind-since-water floor, 639 boards' worst-lag).
     [r"intel s?vga", r"intel graphic", r"graphicdch",
      r"intel.{0,16}graphics driver", r"gna driver v3\d\.0\.101"]),
    # VMD is packaged inside the RST line (iaStorVD.inf), not a separate family
    ("Intel RST", "intel", "storage",
     [r"rapid storage", r"irste?\b", r"\brste?\b", r"\bvmd\b", r"intel/sata/"]),
    ("Thunderbolt", "intel", "usb", [r"thunderbolt", r"\btbt\b"]),

    ("Intel SST", "intel", "audio", [r"smart sound", r"\bsst\b"]),
    # GPU firmware/VBIOS updates version on a different line (95.x) than the
    # drivers (32.x) and must not share a family with them.
    ("GPU VBIOS", "nvidia", "graphics", [r"\bvbios\b", r"gpu firmware"]),
    ("NVIDIA Graphics", "nvidia", "graphics",
     [r"n?vidia", r"n?vdia", r"geforce", r"\bquadro\b"]),
    ("Intel ISH", "intel", "chipset", [r"sensor hub", r"\bish\b"]),
    ("Intel WWAN", "intel", "wwan", [r"intel.*wwan", r"xmm7\d+"]),
    ("WWAN (module vendors)", "oem", "wwan",
     [r"wwan", r"quectel", r"fibocom", r"\bwan driver\b"]),
    ("Camera", "oem", "camera", [r"camera"]),
    ("Card Reader", "oem", "usb", [r"card reader", r"smartcard"]),
    ("Fingerprint Reader", "oem", "usb",
     [r"fingerprint", r"goodix", r"synaptics.*(fp|fingerprint)"]),
    # Lenovo multi-silicon combo packages (one zip covering Realtek+MediaTek
    # etc.); versions aren't cross-vendor comparable, but each family's lag
    # against the newest combo across all Lenovo machines is meaningful.
    ("Notebook WLAN (multi-silicon)", "oem", "wlan",
     [r"lenovo\.com.*\bwlan driver\b"]),
    ("Notebook Bluetooth (multi-silicon)", "oem", "bluetooth",
     [r"lenovo\.com.*\bbluetooth driver\b"]),
    ("Notebook Chipset (OEM)", "oem", "chipset",
     [r"lenovo\.com.*\bchipset driver\b"]),
    ("Laptop OEM Audio", "oem", "audio",
     [r"thinkpad audio", r"senary", r"conexant", r"cirrus", r"fortemedia"]),
    ("ASPEED Graphics", "aspeed", "graphics", [r"aspeed"]),
    ("AMI Remote NDIS", "ami", "lan", [r"remote ndis"]),
    ("LHDC Audio", "oem", "audio", [r"lhdc"]),
    ("ASMedia SATA", "asmedia", "storage", [r"asmedia.*sata"]),
    ("ASMedia USB", "asmedia", "usb", [r"asmedia.*usb", r"asmedia"]),
    ("ITE", "ite", "usb", [r"\bite\b", r"it8[0-9]{2}"]),
    # ASUS-branded generic radio packages: silicon unidentifiable from the
    # listing; INF/HWID evidence will split these into real families later.
    ("Realtek UCM", "realtek", "usb", [r"\bucm\b", r"ucmcx"]),
    ("ASUS Bluetooth (unspecified)", "oem", "bluetooth",
     [r"asus bluetooth", r"\basus bt\b"]),
    ("ASUS Wi-Fi (unspecified)", "oem", "wlan", [r"asus wi-?fi"]),
    ("USB Audio Firmware Tool", "oem", "audio",
     [r"usb.?audio.*(fw|firmware)", r"audio fw update"]),
    ("DTS Audio", "dts", "audio", [r"\bdts\b"]),
]
# Deliberately unmatched, pending INF evidence: MSI's bare 'BlueTooth Driver'
# entries (silicon vendor unidentifiable from the listing alone).

_PREINSTALL = re.compile(r"preinstall|bootdisk|sata floppy|sata/floppy",
                         re.IGNORECASE)

# Generation splits, anchored on HWIDs only one generation's INFs carry
# (versions interleave across generations, so version comparison inside a
# parent family is meaningless). The AMD RZ-series parents are rebadged
# MediaTek and resolve into the same subfamilies — one unified pass, so a
# 'Wi-Fi'-labelled package carrying BT INFs still lands correctly.
# Artefacts without INF evidence adopt the subfamily of an evidenced artefact
# with the same normalised version, then fall back to version-major lines.
SPLIT_PARENTS = {"MediaTek Wi-Fi", "MediaTek Bluetooth",
                 "AMD Wi-Fi", "AMD Bluetooth", "Realtek LAN"}
SUBFAMILIES: list[tuple[str, str, str, set[str]]] = [
    ("MediaTek Wi-Fi 7", "mediatek", "wlan",
     {r"PCI\VEN_14C3&DEV_0717", r"PCI\VEN_14C3&DEV_0738"}),
    ("MediaTek Wi-Fi 6E", "mediatek", "wlan",
     {r"PCI\VEN_14C3&DEV_0608", r"PCI\VEN_14C3&DEV_0616",
      r"PCI\VEN_14C3&DEV_7902"}),
    ("MediaTek Bluetooth (Wi-Fi 7)", "mediatek", "bluetooth",
     {r"USB\VID_0489&PID_E0FA", r"USB\VID_0489&PID_E10F"}),
    ("MediaTek Bluetooth (Wi-Fi 6E)", "mediatek", "bluetooth",
     {r"USB\VID_0489&PID_E0C8", r"USB\VID_0489&PID_E0CD"}),
    # Realtek Ethernet generations: 8111/8168 GbE vs 8125 2.5GbE vs 8126 5GbE
    # — distinct silicon, interleaving version schemes (1168.x vs
    # 10.x/11.x/1125.x vs 1126.x).
    ("Realtek 8168 LAN", "realtek", "lan",
     {r"PCI\VEN_10EC&DEV_8168", r"PCI\VEN_10EC&DEV_8111"}),
    ("Realtek 8125 LAN", "realtek", "lan", {r"PCI\VEN_10EC&DEV_8125"}),
    ("Realtek 8126 LAN", "realtek", "lan", {r"PCI\VEN_10EC&DEV_8126"}),
]
SPLIT_VERSION_FALLBACK: dict[str, dict[int, str]] = {
    "MediaTek Wi-Fi": {5: "MediaTek Wi-Fi 7", 3: "MediaTek Wi-Fi 6E"},
    "AMD Wi-Fi": {5: "MediaTek Wi-Fi 7", 3: "MediaTek Wi-Fi 6E"},
    # majors: 1168=8168's ASUS/Lenovo scheme; 10/11/1125 are all 8125 lines
    # (Realtek official 10.x/11.x, ASUS 1125.x); 1126/1127=8126; 1..9=legacy GbE
    "Realtek LAN": {1168: "Realtek 8168 LAN", 1166: "Realtek 8168 LAN",
                    1125: "Realtek 8125 LAN",
                    1126: "Realtek 8126 LAN", 1127: "Realtek 8126 LAN",
                    10: "Realtek 8125 LAN", 11: "Realtek 8125 LAN",
                    1: "Realtek 8168 LAN", 7: "Realtek 8168 LAN",
                    8: "Realtek 8168 LAN", 9: "Realtek 8168 LAN"},
}

# Family pairs that legitimately share INFs because one package bundles the
# other's component (never merged, never flagged): Killer packages carry
# Intel Wi-Fi and I225 INFs, chipset INF bundles serial-io/GNA, DTT ships IPF.
BUNDLE_OK: set[frozenset] = {
    frozenset(p) for p in [
        ("Intel Chipset INF", "Intel Serial I/O"),
        ("Intel Chipset INF", "Intel GNA"),
        # Intel iGPU driver packages bundle GNA INFs too
        ("Intel VGA", "Intel GNA"),
        ("Intel DTT", "Intel IPF"),
        ("Killer LAN", "Intel Wi-Fi"),
        ("Killer LAN", "Intel I225/I226 LAN"),
        ("MediaTek LE Audio", "MediaTek Bluetooth (Wi-Fi 7)"),
        ("MediaTek LE Audio", "MediaTek Bluetooth (Wi-Fi 6E)"),
        # Intel PROSet-style LAN packages carry INFs for every Intel NIC, so
        # the generic family legitimately shares INFs with the per-silicon ones.
        ("Intel LAN", "Intel I211 LAN"),
        ("Intel LAN", "Intel I219 LAN"),
        ("Intel LAN", "Intel I225/I226 LAN"),
        # Killer suites bundle Intel Bluetooth alongside LAN/Wi-Fi.
        ("Killer LAN", "Intel Bluetooth"),
        # Combined Realtek LAN+WLAN packages exist on several boards.
        ("Realtek Wi-Fi", "Realtek LAN"),
        ("Realtek Wi-Fi", "Realtek 8168 LAN"),
        ("Realtek Wi-Fi", "Realtek 8125 LAN"),
        ("Realtek Wi-Fi", "Realtek 8126 LAN"),
        # Both MediaTek BT generations bundle the same LE-audio ACX INF.
        ("MediaTek Bluetooth (Wi-Fi 7)", "MediaTek Bluetooth (Wi-Fi 6E)"),
        # Gigabyte's 'ITE USB driver' package ships the Realtek UcmCx INF.
        ("ITE", "Realtek UCM"),
        # Realtek HD-audio packages bundle the USB-audio component; Gigabyte
        # ships combined WLAN+BT packages that carry both radios' INFs.
        ("Realtek Audio", "Realtek USB Audio"),
        ("Realtek Wi-Fi", "Realtek Bluetooth"),
    ]
}


def run(conn: sqlite3.Connection, *, log=print) -> dict:
    compiled = [(name, sv, comp, [re.compile(p, re.IGNORECASE) for p in pats])
                for name, sv, comp, pats in RULES]

    def family_id(name: str, sv: str, comp: str) -> int:
        row = conn.execute("SELECT family_id FROM family WHERE name = ?", (name,)).fetchone()
        if row:
            return row["family_id"]
        return conn.execute(
            "INSERT INTO family (name, silicon_vendor, component) VALUES (?, ?, ?)",
            (name, sv, comp)).lastrowid

    n_assigned = 0
    unmatched = []
    for row in conn.execute(
            "SELECT artefact_id, vendor, vendor_artefact_id, description_text,"
            " component_hint, url, version_raw FROM artefact"
            " WHERE kind = 'driver' AND source_type = 'vendor'").fetchall():
        url_path = (row["url"] or "").split("?")[0].split("://")[-1]
        text = " ".join(filter(None, (row["vendor_artefact_id"], url_path,
                                      row["description_text"],
                                      row["component_hint"],
                                      row["version_raw"]))).lower()
        text = re.sub(r"\(r\)|®|™", " ", text)   # 'intel(r) chipset' etc.
        # Filenames tokenise with underscores ('Intel_Chipset_Driver_...');
        # hyphens are kept — 'wi-fi' patterns rely on them.
        text = text.replace("_", " ")
        hit = next(((name, sv, comp) for name, sv, comp, pats in compiled
                    if any(p.search(text) for p in pats)), None)
        if not hit:
            unmatched.append((row["vendor"], row["vendor_artefact_id"],
                              row["description_text"]))
            # a previously-assigned row that no longer matches any rule must
            # not keep its stale family (the evidence pass may re-fill it)
            conn.execute("UPDATE artefact SET family_id = NULL"
                         " WHERE artefact_id = ?", (row["artefact_id"],))
            continue
        name, sv, comp = hit
        if _PREINSTALL.search(text):
            name += " (preinstall)"
        conn.execute("UPDATE artefact SET family_id = ? WHERE artefact_id = ?",
                     (family_id(name, sv, comp), row["artefact_id"]))
        n_assigned += 1
    conn.commit()

    _apply_splits(conn, family_id, log)
    _populate_hwids(conn)
    _evidence_reassign(conn, log)
    _populate_hwids(conn)
    conn.execute("DELETE FROM family WHERE family_id NOT IN"
                 " (SELECT DISTINCT family_id FROM artefact WHERE family_id IS NOT NULL)")
    conflicts = _inf_cross_check(conn, log)

    log(f"assign: {n_assigned} assigned, {len(unmatched)} unmatched, "
        f"{conflicts} INF conflicts")
    for vendor, vid, desc in unmatched:
        log(f"  UNMATCHED {vendor} {vid} — {desc!r}")
    return {"assigned": n_assigned, "unmatched": len(unmatched),
            "conflicts": conflicts, "failed": conflicts}


def _artefact_hwids(conn, artefact_id) -> set[str]:
    hwids: set[str] = set()
    for (blob,) in conn.execute(
            "SELECT i.hwids FROM inf i JOIN artefact a ON a.sha256 = i.payload_sha256"
            " WHERE a.artefact_id = ?", (artefact_id,)):
        hwids.update(json.loads(blob))
    return hwids


def _apply_splits(conn, family_id, log) -> None:
    sub_fids = {name: family_id(name, sv, comp) for name, sv, comp, _ in SUBFAMILIES}
    by_version: dict[str, int] = {}
    undecided = []
    n_anchored = 0
    for parent in sorted(SPLIT_PARENTS):
        row = conn.execute("SELECT family_id FROM family WHERE name = ?",
                           (parent,)).fetchone()
        if not row:
            continue
        for a in conn.execute(
                "SELECT artefact_id, version_normalised FROM artefact"
                " WHERE family_id = ?", (row["family_id"],)).fetchall():
            hwids = _artefact_hwids(conn, a["artefact_id"])
            target = next((name for name, _, _, anchors in SUBFAMILIES
                           if hwids and any(h.startswith(anchor) for h in hwids
                                            for anchor in anchors)), None)
            if target:
                conn.execute("UPDATE artefact SET family_id = ? WHERE artefact_id = ?",
                             (sub_fids[target], a["artefact_id"]))
                n_anchored += 1
                if a["version_normalised"]:
                    by_version[a["version_normalised"]] = sub_fids[target]
            else:
                undecided.append((parent, a))
    moved = left = 0
    for parent, a in undecided:   # no INF evidence: adopt an evidenced sibling
        fid = by_version.get(a["version_normalised"])
        if not fid and a["version_normalised"]:
            name = SPLIT_VERSION_FALLBACK.get(parent, {}).get(
                json.loads(a["version_normalised"])[0])
            if name:
                fid = sub_fids[name]
        if fid:
            conn.execute("UPDATE artefact SET family_id = ? WHERE artefact_id = ?",
                         (fid, a["artefact_id"]))
            moved += 1
        else:
            left += 1
    log(f"  splits: {n_anchored} anchored by HWID, {moved} adopted by version, "
        f"{left} left unsplit")
    conn.commit()


def _evidence_reassign(conn, log) -> None:
    """Move unassigned / '(unspecified)' artefacts to the family whose HWID
    set their own INFs overlap — listing text failed, silicon doesn't lie."""
    fam_hwids = {r["family_id"]: (r["name"], set(json.loads(r["hwids"])))
                 for r in conn.execute("SELECT family_id, name, hwids FROM family"
                                       " WHERE name NOT LIKE '%(unspecified)%'")}
    candidates = conn.execute("""
        SELECT a.artefact_id, a.vendor, a.vendor_artefact_id, a.family_id
        FROM artefact a LEFT JOIN family f ON f.family_id = a.family_id
        WHERE a.kind = 'driver'
          AND (a.family_id IS NULL OR f.name LIKE '%(unspecified)%')""").fetchall()
    for a in candidates:
        hwids = _artefact_hwids(conn, a["artefact_id"])
        if not hwids:
            continue
        scores = sorted(((len(hwids & fh), fid, name)
                         for fid, (name, fh) in fam_hwids.items()), reverse=True)
        if scores and scores[0][0] > 0 and (len(scores) < 2 or scores[1][0] < scores[0][0]):
            _, fid, name = scores[0]
            conn.execute("UPDATE artefact SET family_id = ? WHERE artefact_id = ?",
                         (fid, a["artefact_id"]))
            log(f"  evidence: {a['vendor']} {a['vendor_artefact_id']} -> {name} "
                f"({scores[0][0]} HWIDs)")
    conn.commit()


def _populate_hwids(conn) -> None:
    for row in conn.execute("SELECT family_id FROM family"):
        fid = row["family_id"]
        hwids: set[str] = set()
        for (blob,) in conn.execute(
                "SELECT i.hwids FROM inf i JOIN artefact a ON a.sha256 = i.payload_sha256"
                " WHERE a.family_id = ?", (fid,)):
            hwids.update(json.loads(blob))
        conn.execute("UPDATE family SET hwids = ? WHERE family_id = ?",
                     (json.dumps(sorted(hwids)), fid))
    conn.commit()


def _inf_cross_check(conn, log) -> int:
    """An inf_sha256 appearing under two different families means a rule bug."""
    def allowed(a: str, b: str) -> bool:
        strip = lambda n: n.removesuffix(" (preinstall)")
        return (strip(a) == strip(b)) or frozenset((strip(a), strip(b))) in BUNDLE_OK

    n = 0
    for row in conn.execute("""
            SELECT i.inf_sha256, GROUP_CONCAT(DISTINCT f.name) AS fams
            FROM inf i
            JOIN artefact a ON a.sha256 = i.payload_sha256
            JOIN family f ON f.family_id = a.family_id
            GROUP BY i.inf_sha256 HAVING COUNT(DISTINCT a.family_id) > 1"""):
        names = row["fams"].split(",")
        if all(allowed(a, b) for i, a in enumerate(names) for b in names[i + 1:]):
            continue
        log(f"  INF CONFLICT {row['inf_sha256'][:12]} spans: {row['fams']}")
        n += 1
    return n
