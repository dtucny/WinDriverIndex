"""In-scope platform filter (spec §2): AM4, AM5, Intel 600/700/800 desktop."""

from __future__ import annotations

import re

CHIPSET_SOCKET: dict[str, str] = {
    # AM4
    "A320": "AM4", "B350": "AM4", "X370": "AM4",
    "B450": "AM4", "X470": "AM4",
    "A520": "AM4", "B550": "AM4", "X570": "AM4",
    # AM5 (E-variants normalised separately, B840 is AM5 despite the numbering)
    "A620": "AM5", "B650": "AM5", "X670": "AM5",
    "B840": "AM5", "B850": "AM5", "X870": "AM5",
    # Intel 600/700 series
    "H610": "LGA1700", "B660": "LGA1700", "H670": "LGA1700", "Z690": "LGA1700",
    "H770": "LGA1700", "B760": "LGA1700", "Z790": "LGA1700",
    # Intel 800 series
    "H810": "LGA1851", "B860": "LGA1851", "Z890": "LGA1851",
    "Q870": "LGA1851", "W880": "LGA1851",
}

# Chipset token anywhere in a product name: base like B650 plus optional E/A
# suffix (B650E, X870E, A620A). Longest tokens are matched first by \w rules.
_TOKEN = re.compile(r"\b([ABXZHQW]\d{3})(E|A)?\b")


def extract_chipset(name: str) -> tuple[str, str] | None:
    """Return (chipset_as_named, socket) for an in-scope board name, else None.

    'B650E EAGLE' -> ('B650E', 'AM5'); 'GA-Z270X-Gaming' -> None (out of scope).
    """
    for m in _TOKEN.finditer(name):
        base, suffix = m.group(1), m.group(2) or ""
        socket = CHIPSET_SOCKET.get(base)
        if socket:
            return base + suffix, socket
    return None
