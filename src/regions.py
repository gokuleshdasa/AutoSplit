"""Named regions (markers) for multi-clip export."""
from __future__ import annotations

import re
from dataclasses import dataclass


_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# Shared color palette (used by both the Timeline overlay and the Markers
# list icon). RGB only — the timeline applies its own alpha for the overlay.
REGION_PALETTE_RGB = [
    (255, 140,   0),   # orange
    (  0, 200, 140),   # teal
    (200, 100, 200),   # violet
    (255, 209,  90),   # amber
    (120, 200, 255),   # sky
]


def region_rgb(index: int) -> tuple[int, int, int]:
    return REGION_PALETTE_RGB[index % len(REGION_PALETTE_RGB)]


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
