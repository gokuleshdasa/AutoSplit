"""FFmpeg command builders for frame extraction and GIF export.

GIF export uses the two-pass `palettegen` + `paletteuse` filter pipeline —
the highest-quality path available in FFmpeg (Ubuntu-shipped `gifski` is
arguably better perceptually but adds a dependency; we stay with ffmpeg).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import ffmpeg_utils as fu


# ---------- single frame ----------------------------------------------------

def build_single_frame_cmd(
    *,
    input_path: str,
    timestamp_seconds: float,
    output_path: str,
    quality: int = 2,   # JPEG q-scale (2 = high); ignored for PNG
) -> list[str]:
    """Grab one frame at `timestamp_seconds` and write to `output_path`.
    Extension of `output_path` decides the format (.png / .jpg / .webp)."""
    ext = os.path.splitext(output_path)[1].lower()
    cmd = [
        fu.ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-ss", f"{max(0.0, timestamp_seconds):.3f}",
        "-i", input_path,
        "-frames:v", "1",
    ]
    if ext in (".jpg", ".jpeg", ".webp"):
        cmd += ["-q:v", str(quality)]
    cmd.append(output_path)
    return cmd


# ---------- all frames (range or full) --------------------------------------

@dataclass
class AllFramesJob:
    input_path: str
    output_dir: str
    start_seconds: float           # 0 for beginning
    end_seconds: float             # <=0 means end of media
    fps: Optional[float] = None    # None = source native rate
    fmt: str = "png"               # 'png' | 'jpg' | 'webp'
    prefix: str = "frame"
    quality: int = 2               # for jpg/webp

    def total_expected_frames(self, media_fps: Optional[float]) -> int:
        span = max(0.0, (self.end_seconds if self.end_seconds > 0 else 0) - self.start_seconds)
        rate = self.fps if self.fps and self.fps > 0 else (media_fps or 30.0)
        return int(span * rate) if span > 0 else 0


def build_all_frames_cmd(job: AllFramesJob) -> list[str]:
    cmd = [
        fu.ffmpeg_path(),
        "-y",
        "-hide_banner",
    ]
    if job.start_seconds > 0:
        cmd += ["-ss", f"{job.start_seconds:.3f}"]
    cmd += ["-i", job.input_path]
    if job.end_seconds > job.start_seconds:
        cmd += ["-t", f"{job.end_seconds - job.start_seconds:.3f}"]

    if job.fps and job.fps > 0:
        cmd += ["-vf", f"fps={job.fps}"]
    else:
        # Preserve source cadence.
        cmd += ["-vsync", "0"]

    if job.fmt in ("jpg", "jpeg", "webp"):
        cmd += ["-q:v", str(job.quality)]

    pattern = os.path.join(
        job.output_dir,
        f"{job.prefix}_%06d.{job.fmt}",
    )
    cmd += ["-progress", "pipe:2", pattern]
    return cmd


# ---------- GIF export -------------------------------------------------------

@dataclass
class GifJob:
    input_path: str
    output_path: str
    start_seconds: float
    end_seconds: float             # <=0 means end of media
    fps: int = 15
    width: int = 480               # -1 keeps aspect
    dither: str = "bayer"          # 'bayer' | 'floyd_steinberg' | 'sierra2_4a' | 'none'
    loop_forever: bool = True


def build_gif_cmd(job: GifJob) -> list[str]:
    """Single-pass GIF via split -> palettegen -> paletteuse (best quality path)."""
    scale = f"scale={job.width}:-2:flags=lanczos" if job.width > 0 else "scale=iw:-2:flags=lanczos"

    if job.dither in ("bayer",):
        dither_expr = "dither=bayer:bayer_scale=5:diff_mode=rectangle"
    elif job.dither in ("none",):
        dither_expr = "dither=none:diff_mode=rectangle"
    else:
        dither_expr = f"dither={job.dither}:diff_mode=rectangle"

    filter_complex = (
        f"[0:v]fps={job.fps},{scale},split[a][b];"
        f"[a]palettegen=stats_mode=diff[p];"
        f"[b][p]paletteuse={dither_expr}"
    )

    cmd = [
        fu.ffmpeg_path(),
        "-y",
        "-hide_banner",
    ]
    if job.start_seconds > 0:
        cmd += ["-ss", f"{job.start_seconds:.3f}"]
    cmd += ["-i", job.input_path]
    if job.end_seconds > job.start_seconds:
        cmd += ["-t", f"{job.end_seconds - job.start_seconds:.3f}"]

    cmd += ["-filter_complex", filter_complex]
    if job.loop_forever:
        cmd += ["-loop", "0"]
    cmd += ["-progress", "pipe:2", job.output_path]
    return cmd
