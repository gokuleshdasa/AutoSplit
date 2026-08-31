"""Three-stage AI slow-motion pipeline using rife-ncnn-vulkan.

Stage 1: extract source frames as PNGs to a temp dir (via ffmpeg)
Stage 2: interpolate with RIFE — inserts 2N-1 synthetic frames per pair
Stage 3: reassemble PNGs at target fps, mux the tempo-adjusted audio (via ffmpeg)

Each stage emits fractional progress into a single callback so the UI's
progress bar can span the whole run (30/60/10% split).
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional

import ffmpeg_utils as fu
import rife_setup


@dataclass
class RifeSpeedJob:
    input_path: str
    output_path: str
    start_seconds: float
    end_seconds: float
    speed: float                 # 0 < speed < 1 for slo-mo
    keep_audio: bool = True
    target_fps: int = 60
    source_fps: Optional[float] = None   # if None, we probe
    model: str = "rife-v4.6"
    video_crf: int = 18
    audio_bitrate: str = "192k"


def _atempo_chain(target: float) -> list[str]:
    """Product-of-atempos == target. Range: 0.5..2.0 per step."""
    filters: list[str] = []
    rem = float(target)
    while rem < 0.5:
        filters.append("atempo=0.5")
        rem *= 2.0
    while rem > 2.0:
        filters.append("atempo=2.0")
        rem /= 2.0
    if abs(rem - 1.0) > 0.001:
        filters.append(f"atempo={rem:.6f}")
    return filters


def _choose_multiplier(src_fps: float, speed: float, target_fps: int) -> int:
    """rife-ncnn-vulkan supports -n as an integer output-frame multiplier.
    Pick the smallest power-of-2 >= (target_fps / (src_fps * speed))."""
    if src_fps <= 0 or speed <= 0:
        return 2
    need = target_fps / (src_fps * speed)
    if need <= 2.0:
        return 2
    if need <= 4.0:
        return 4
    if need <= 8.0:
        return 8
    return 16


def _run_progress(cmd: list[str], total: float,
                  cb: Optional[Callable[[float], None]]) -> tuple[int, str]:
    """Run ffmpeg and report fractional progress via -progress on stderr."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        **fu._no_window_kwargs(),
    )
    assert proc.stderr is not None
    tail: list[str] = []
    time_re = re.compile(r"out_time_ms=(\d+)")
    try:
        for line in proc.stderr:
            tail.append(line)
            if len(tail) > 60:
                tail.pop(0)
            if cb is not None and total > 0:
                m = time_re.search(line)
                if m:
                    t = int(m.group(1)) / 1_000_000.0
                    cb(min(t / total, 1.0))
    finally:
        proc.wait()
    return proc.returncode, "".join(tail)


def _run_rife(rife_exe: str, in_dir: str, out_dir: str,
              multiplier: int, model_folder: str,
              on_progress: Optional[Callable[[float], None]] = None) -> tuple[int, str]:
    """Run rife-ncnn-vulkan. Progress is estimated by output-file counting."""
    cmd = [
        rife_exe,
        "-i", in_dir,
        "-o", out_dir,
        "-m", model_folder,
        "-n", str(multiplier),
        "-f", "%08d.png",
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        **fu._no_window_kwargs(),
    )
    # Estimate progress by counting output pngs.
    total_expected = len([f for f in os.listdir(in_dir) if f.endswith(".png")]) * multiplier
    tail: list[str] = []
    assert proc.stderr is not None
    try:
        while proc.poll() is None:
            # Coarse polling — RIFE processes frames in bursts.
            try:
                produced = len([f for f in os.listdir(out_dir) if f.endswith(".png")])
                if on_progress and total_expected > 0:
                    on_progress(min(1.0, produced / total_expected))
            except FileNotFoundError:
                pass
            try:
                line = proc.stderr.readline()
                if line:
                    tail.append(line)
                    if len(tail) > 60:
                        tail.pop(0)
            except Exception:
                pass
        # Drain remaining stderr.
        rest = proc.stderr.read()
        if rest:
            tail.append(rest)
    finally:
        proc.wait()
    if on_progress:
        on_progress(1.0)
    return proc.returncode, "".join(tail)


def run_rife_pipeline(
    job: RifeSpeedJob,
    on_progress: Optional[Callable[[float], None]] = None,
    on_status:   Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """Execute all three stages. Returns (ok, tail-of-stderr-or-error-message)."""
    if not rife_setup.is_installed():
        return False, "RIFE is not installed. Use the Install button first."

    # Resolve source fps if not provided.
    src_fps = job.source_fps
    if not src_fps or src_fps <= 0:
        try:
            info = fu.probe(job.input_path)
            src_fps = info.fps or 30.0
        except Exception:
            src_fps = 30.0
    span = max(0.001, job.end_seconds - job.start_seconds)
    multiplier = _choose_multiplier(src_fps, job.speed, job.target_fps)

    # Temp scratch dirs.
    scratch = tempfile.mkdtemp(prefix="autosplit_rife_")
    in_dir = os.path.join(scratch, "in")
    out_dir = os.path.join(scratch, "out")
    audio_path = os.path.join(scratch, "audio.wav")
    os.makedirs(in_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    def cleanup():
        try:
            shutil.rmtree(scratch)
        except OSError:
            pass

    try:
        # ---- Stage 1: extract source frames ----
        if on_status: on_status(f"Extracting frames  ·  {src_fps:g} fps source")
        extract_cmd = [
            fu.ffmpeg_path(),
            "-y", "-hide_banner",
            "-ss", f"{job.start_seconds:.3f}",
            "-i", job.input_path,
            "-t", f"{span:.3f}",
            "-vsync", "0",
            "-qscale:v", "1", "-qmin", "1", "-qmax", "1",
            "-progress", "pipe:2",
            os.path.join(in_dir, "%08d.png"),
        ]
        rc, tail = _run_progress(extract_cmd, total=span,
                                 cb=lambda f: on_progress(0.3 * f) if on_progress else None)
        if should_cancel and should_cancel():
            return False, "Cancelled"
        if rc != 0:
            return False, f"Extract failed:\n{tail[-1500:]}"

        # ---- Stage 2: RIFE interpolation ----
        if on_status:
            on_status(f"RIFE interpolation ×{multiplier}  ·  target {job.target_fps} fps")
        rc, tail = _run_rife(
            str(rife_setup.rife_exe()), in_dir, out_dir,
            multiplier=multiplier, model_folder=job.model,
            on_progress=(lambda f: on_progress(0.3 + 0.6 * f)) if on_progress else None,
        )
        if should_cancel and should_cancel():
            return False, "Cancelled"
        if rc != 0:
            return False, f"RIFE failed:\n{tail[-1500:]}"

        # ---- Stage 2.5: audio pass (parallel would be nicer; simple sequential) ----
        if job.keep_audio:
            atempo = _atempo_chain(job.speed)
            audio_cmd = [
                fu.ffmpeg_path(),
                "-y", "-hide_banner",
                "-ss", f"{job.start_seconds:.3f}",
                "-i", job.input_path,
                "-t", f"{span:.3f}",
                "-vn",
            ]
            if atempo:
                audio_cmd += ["-af", ",".join(atempo)]
            audio_cmd += ["-acodec", "pcm_s16le", audio_path]
            try:
                subprocess.run(audio_cmd, capture_output=True, **fu._no_window_kwargs())
            except Exception:
                pass  # non-fatal — we'll just export without audio

        # ---- Stage 3: reassemble ----
        if on_status: on_status(f"Reassembling at {job.target_fps} fps")
        reassemble_cmd = [
            fu.ffmpeg_path(),
            "-y", "-hide_banner",
            "-framerate", str(job.target_fps),
            "-i", os.path.join(out_dir, "%08d.png"),
        ]
        have_audio = job.keep_audio and os.path.exists(audio_path)
        if have_audio:
            reassemble_cmd += ["-i", audio_path]

        reassemble_cmd += [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(int(job.video_crf)),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]
        if have_audio:
            reassemble_cmd += ["-c:a", "aac", "-b:a", job.audio_bitrate, "-shortest"]
        reassemble_cmd += [
            "-progress", "pipe:2",
            job.output_path,
        ]

        # Output duration for progress: span / speed.
        out_total = span / max(0.001, job.speed)
        rc, tail = _run_progress(
            reassemble_cmd, total=out_total,
            cb=lambda f: on_progress(0.9 + 0.1 * f) if on_progress else None,
        )
        if rc != 0:
            return False, f"Reassemble failed:\n{tail[-1500:]}"
        if on_progress:
            on_progress(1.0)
        return True, job.output_path

    finally:
        cleanup()
