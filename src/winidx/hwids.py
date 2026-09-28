"""Hardware-ID hygiene shared by extract, assign and publish.

The INF scan in extract.py is a regex over the whole file, so besides the
real IDs in the models sections it also catches [Strings] keys
('PCI\\VEN_8086&DEV_09AB.DeviceDesc') and registry paths
('...\\Services\\pci\\Parameters'). Those are dropped here, at read time as
well as at extraction, so INF rows extracted before the filter existed need
no re-extraction.

Generic IDs — class-code or vendor-wide compatible IDs with no device part
('PCI\\CC_010802', 'PCI\\VEN_8086&CC_0108', 'USB\\CLASS_01') — are real INF
entries but match inbox Microsoft drivers too (every NVMe drive carries
PCI\\CC_010802, which AMD's RAID INF also lists). They never identify a
family, so family sets and published lookups leave them out.
"""

from __future__ import annotations

import re

# a string-table key: the ID followed by '.Name' with a letter in the suffix
# (SWC\AMDOCL-23.19 is a real software-component ID — digits only after the dot)
_STRING_KEY = re.compile(r"\.[A-Z0-9_]*[A-Z][A-Z0-9_]*$", re.I)
# the part after the enumerator must look like an ID for these enumerators
_ENUM_SHAPE = {
    "PCI": re.compile(r"(VEN|CC)_", re.I),
    "USB": re.compile(r"(VID|CLASS)_|[A-Z]+ROOT_|[A-Z]+USBD_", re.I),
    "HDAUDIO": re.compile(r"(FUNC|SUBFUNC|SGPC)_", re.I),
}
_NOT_IDS = {"PARAMETERS", "ENUM", "SERVICES"}
# the device part that makes an ID specific to one piece of hardware
_DEVICE_PART = {
    "PCI": re.compile(r"&DEV_", re.I),
    "USB": re.compile(r"&PID_?", re.I),   # ASMedia root hubs: &PID1040
    "HDAUDIO": re.compile(r"&DEV_", re.I),
    "INTELAUDIO": re.compile(r"DEV_", re.I),
}
_QUALIFIERS = re.compile(r"&(SUBSYS|REV)_[0-9A-F]+", re.I)


def is_hwid(token: str) -> bool:
    """False for [Strings] keys and registry path fragments the INF scan
    picks up alongside real hardware IDs."""
    enum, _, rest = token.partition("\\")
    if not rest or _STRING_KEY.search(rest) or rest.upper() in _NOT_IDS:
        return False
    shape = _ENUM_SHAPE.get(enum.upper())
    return not shape or bool(shape.match(rest))


def is_generic(hwid: str) -> bool:
    """True for class-code / vendor-wide compatible IDs with no device part."""
    enum, _, rest = hwid.partition("\\")
    dev = _DEVICE_PART.get(enum.upper())
    return bool(dev) and not dev.search("&" + rest)


def specific(tokens) -> list[str]:
    """Real, device-specific hardware IDs from an INF's scanned tokens."""
    return sorted({t for t in tokens if is_hwid(t) and not is_generic(t)})


def base(hwid: str) -> str:
    """Drop SUBSYS/REV qualifiers: PCI\\VEN_10EC&DEV_8125&SUBSYS_..&REV_05 ->
    PCI\\VEN_10EC&DEV_8125. Every device reports this less-specific form among
    its hardware IDs, so it is the stable key to match an installed device
    against an INF without enumerating every OEM subsystem."""
    return _QUALIFIERS.sub("", hwid)
