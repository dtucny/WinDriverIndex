"""Version normalisation (spec §6.3).

Vendors publish the same driver as '3.05.00.1380', '3.5.0.1380', or
'XB560NF_v6001.16.175.0'. Segments compare numerically so zero-padding is
irrelevant; prefixes and suffixes are retained as flags, never discarded.
The raw string is always stored alongside — this module only produces the
comparable form.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_DOTTED = re.compile(r"\d+(?:\.\d+)+")
_BETA = re.compile(r"beta", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedVersion:
    tuple: tuple[int, ...] | None   # None => unparseable, order by release date
    is_beta: bool
    raw: str

    @property
    def normalised_json(self) -> str | None:
        return json.dumps(list(self.tuple)) if self.tuple else None


def parse(raw: str | None) -> ParsedVersion:
    if not raw:
        return ParsedVersion(None, False, raw or "")
    is_beta = bool(_BETA.search(raw))
    # Longest dotted numeric run wins: handles 'XB560NF_v6001.16.175.0' and
    # '6.0.9520.1_Nahimic' alike. Ties go to the earliest occurrence.
    runs = _DOTTED.findall(raw)
    if not runs:
        return ParsedVersion(None, is_beta, raw)
    best = max(runs, key=len)
    return ParsedVersion(tuple(int(s) for s in best.split(".")), is_beta, raw)


def compare_key(v: ParsedVersion) -> tuple:
    """Sort key: parseable versions order by numeric tuple, padded for mixed
    lengths; unparseable sort lowest (callers fall back to release date)."""
    if v.tuple is None:
        return (0,)
    return (1, *v.tuple)
