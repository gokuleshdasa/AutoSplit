"""Named regions (markers) for multi-clip export."""
from __future__ import annotations

import re
from dataclasses import dataclass


_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Region:
    start: float
    end: float
    name: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def sanitize_name(name: str) -> str:
    """Strip filesystem-unsafe characters and trim whitespace."""
    cleaned = _INVALID_CHARS.sub("_", name).strip().rstrip(".")
    return cleaned or ""


def default_name(index_1_based: int, pad_width: int) -> str:
    return str(index_1_based).zfill(pad_width)
