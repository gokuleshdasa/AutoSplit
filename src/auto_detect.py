"""Auto-detect scene changes and silence — seed markers from ffmpeg output.

Scene detect:  ffmpeg -vf "select='gt(scene,T)',metadata=print" -f null -
Silence detect: ffmpeg -af "silencedetect=noise=Nb:d=D" -f null -

Both run under a `run_ffmpeg`-style progress function so the UI can show
a proper progress bar (as a fraction of source duration).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional

import ffmpeg_utils as fu


# --- scene changes ---------------------------------------------------------

_SCENE_LINE = re.compile(r"pts_time:([0-9.]+)")


@dataclass
class SceneOptions:
    threshold: float = 0.4        # 0.1 = very sensitive, 0.6 = only hard cuts
    min_gap_seconds: float = 1.0  # merge scenes closer than this


def detect_scenes(
    input_path: str,
    total_duration: float,
    opts: SceneOptions,
    on_progress: Optional[Callable[[float], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> List[float]:
    """Return a sorted list of scene-start timestamps (seconds)."""
    filter_expr = f"select='gt(scene,{opts.threshold})',metadata=print"
    cmd = [
        fu.ffmpeg_path(),
        "-i", input_path,
        "-vf", filter_expr,
        "-an", "-f", "null", "-",
        "-progress", "pipe:2",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, bufsize=1, **fu._no_window_kwargs(),
    )
    times: list[float] = []
    time_re = re.compile(r"out_time_ms=(\d+)")
    assert proc.stderr is not None
    try:
        for line in proc.stderr:
            m = _SCENE_LINE.search(line)
            if m:
                try:
                    t = float(m.group(1))
                    times.append(t)
                except ValueError:
                    pass
            if on_progress and total_duration > 0:
                pm = time_re.search(line)
                if pm:
                    on_progress(min(1.0, int(pm.group(1)) / 1_000_000.0 / total_duration))
            if should_cancel and should_cancel():
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return times
    finally:
        proc.wait()

    times.sort()
    # Merge scenes closer than min_gap_seconds.
    if opts.min_gap_seconds > 0 and times:
        filtered = [times[0]]
        for t in times[1:]:
            if t - filtered[-1] >= opts.min_gap_seconds:
                filtered.append(t)
        times = filtered
    return times


def scenes_to_regions(scene_times: List[float], duration: float,
                      prefix: str = "scene") -> list[tuple[float, float, str]]:
    """Turn a list of scene-start times into (start, end, name) triples.

    Region N runs from scene N to (scene N+1) or the media end.
    """
    if not scene_times:
        return [(0.0, duration, f"{prefix}_001")]
    boundaries = [0.0] + list(scene_times) + [duration]
    # Drop duplicates.
    dedup = [boundaries[0]]
    for b in boundaries[1:]:
        if b - dedup[-1] > 0.01:
            dedup.append(b)
    regions = []
    for i in range(len(dedup) - 1):
        regions.append((dedup[i], dedup[i + 1], f"{prefix}_{i + 1:03d}"))
    return regions


# --- silence -------------------------------------------------------------

_SIL_START = re.compile(r"silence_start:\s*([0-9.\-]+)")
_SIL_END   = re.compile(r"silence_end:\s*([0-9.\-]+)")


@dataclass
class SilenceOptions:
    noise_db: float = -30.0        # threshold (dB)
    min_silence_seconds: float = 0.6
    mode: str = "speech"           # 'silence' -> mark silences; 'speech' -> mark speech


def detect_silence(
    input_path: str,
    total_duration: float,
    opts: SilenceOptions,
    on_progress: Optional[Callable[[float], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> list[tuple[float, float]]:
    """Return a list of (silence_start, silence_end) pairs."""
    filter_expr = f"silencedetect=noise={opts.noise_db}dB:d={opts.min_silence_seconds}"
    cmd = [
        fu.ffmpeg_path(),
        "-i", input_path,
        "-af", filter_expr,
        "-vn", "-f", "null", "-",
        "-progress", "pipe:2",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, bufsize=1, **fu._no_window_kwargs(),
    )
    silences: list[list[float]] = []
    pending_start: Optional[float] = None
    time_re = re.compile(r"out_time_ms=(\d+)")
    assert proc.stderr is not None
    try:
        for line in proc.stderr:
            ms = _SIL_START.search(line)
            me = _SIL_END.search(line)
            if ms:
                try:
                    pending_start = float(ms.group(1))
                except ValueError:
                    pass
            elif me and pending_start is not None:
                try:
                    silences.append([pending_start, float(me.group(1))])
                except ValueError:
                    pass
                pending_start = None
            if on_progress and total_duration > 0:
                pm = time_re.search(line)
                if pm:
                    on_progress(min(1.0, int(pm.group(1)) / 1_000_000.0 / total_duration))
            if should_cancel and should_cancel():
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return [(s, e) for s, e in silences]
    finally:
        proc.wait()
    return [(s, e) for s, e in silences]


def silence_to_regions(silences: list[tuple[float, float]], duration: float,
                       mode: str, prefix: str) -> list[tuple[float, float, str]]:
    """Convert silences into regions.

    mode='silence' -> each silence is a region.
    mode='speech'  -> each non-silence span is a region.
    """
    if mode == "silence":
        return [(s, e, f"{prefix}_{i + 1:03d}") for i, (s, e) in enumerate(silences)]

    # mode == 'speech'
    boundaries = [0.0]
    for s, e in silences:
        boundaries.append(s)
        boundaries.append(e)
    boundaries.append(duration)
    regions: list[tuple[float, float, str]] = []
    idx = 1
    # Speech = odd-indexed spans (between silence pairs). We built:
    # [0, s1_start, s1_end, s2_start, s2_end, ..., duration]
    # Speech spans are [0..s1_start], [s1_end..s2_start], ...
    i = 0
    while i + 1 < len(boundaries):
        start, end = boundaries[i], boundaries[i + 1]
        if end - start >= 0.2:
            regions.append((start, end, f"{prefix}_{idx:03d}"))
            idx += 1
        i += 2
    return regions
