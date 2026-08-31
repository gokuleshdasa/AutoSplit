"""Three-stage AI upscaling pipeline using SeedVR2.

Stage 1: extract source frames as PNGs to a temp dir (ffmpeg)
Stage 2: run SeedVR2 inference in its dedicated venv on the frame dir
Stage 3: reassemble PNGs at source fps + mux original audio (ffmpeg)

Same shape as our RIFE pipeline in rife_pipeline.py — but here the
'engine' step upscales resolution rather than interpolating time.

Progress accounting: 20% extract / 70% SeedVR2 / 10% reassemble.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable, Optional

import ffmpeg_utils as fu
import seedvr2_setup as sv


@dataclass
class UpscaleJob:
    input_path: str
    output_path: str
    start_seconds: float
    end_seconds: float
    target_w: int
    target_h: int
    model_kind: str = "3b"       # '3b' or '7b'
    keep_audio: bool = True
    source_fps: Optional[float] = None
    video_crf: int = 18
    audio_bitrate: str = "192k"


def _log_diag(msg: str) -> None:
    try:
        log_path = os.path.join(tempfile.gettempdir(), "autosplit.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[seedvr2] {msg}\n")
    except Exception:
        pass


def _run_progress(cmd: list[str], total: float,
                  cb: Optional[Callable[[float], None]] = None) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, bufsize=1, **fu._no_window_kwargs(),
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


def _run_seedvr(in_dir: str, out_dir: str, model_kind: str,
                target_h: int, target_w: int,
                on_progress: Optional[Callable[[float], None]] = None,
                on_status:   Optional[Callable[[str], None]] = None) -> tuple[int, str]:
    """Kick off SeedVR2 inside its own venv and stream progress by counting
    output frames (the model outputs PNGs one at a time)."""
    model_dir = sv.models_dir() / f"seedvr2_{model_kind}"
    cmd = [
        str(sv.venv_python()), str(sv.infer_helper()),
        "--input", in_dir,
        "--output", out_dir,
        "--model", model_kind,
        "--target-h", str(target_h),
        "--target-w", str(target_w),
        "--model-dir", str(model_dir),
        "--repo-dir", str(sv.repo_dir()),
    ]
    _log_diag("seedvr2 cmd: " + " ".join(cmd))

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, **fu._no_window_kwargs(),
    )
    tail: list[str] = []
    total_expected = 0
    try:
        # Frame count is fixed once extract finished.
        total_expected = len([f for f in os.listdir(in_dir) if f.endswith(".png")])
        while proc.poll() is None:
            try:
                produced = len([f for f in os.listdir(out_dir) if f.endswith(".png")])
                if on_progress and total_expected > 0:
                    on_progress(min(1.0, produced / total_expected))
            except FileNotFoundError:
                pass
            try:
                line = proc.stdout.readline() if proc.stdout else ""
                if line:
                    tail.append(line)
                    if len(tail) > 80:
                        tail.pop(0)
            except Exception:
                pass
        rest = proc.stdout.read() if proc.stdout else ""
        if rest:
            tail.append(rest)
    finally:
        proc.wait()
    return proc.returncode, "".join(tail)


def run_upscale(
    job: UpscaleJob,
    on_progress: Optional[Callable[[float], None]] = None,
    on_status:   Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """Full 3-stage pipeline. Returns (ok, tail_or_message)."""
    if not sv.is_ready(job.model_kind):
        return False, (
            f"SeedVR2 (model {job.model_kind}) is not fully installed yet. "
            f"Open the Upscale dialog and use the Install button.")

    src_fps = job.source_fps
    if not src_fps or src_fps <= 0:
        try:
            info = fu.probe(job.input_path)
            src_fps = info.fps or 30.0
        except Exception:
            src_fps = 30.0
    span = max(0.001, job.end_seconds - job.start_seconds)

    scratch = tempfile.mkdtemp(prefix="autosplit_upscale_")
    in_dir = os.path.join(scratch, "in")
    out_dir = os.path.join(scratch, "out")
    audio_path = os.path.join(scratch, "audio.aac")
    os.makedirs(in_dir); os.makedirs(out_dir)

    _log_diag(f"START target={job.target_w}x{job.target_h} model={job.model_kind} "
              f"span={span:.3f}s fps={src_fps}")

    def cleanup():
        try:
            shutil.rmtree(scratch)
        except OSError:
            pass

    try:
        # --- Stage 1: extract ---
        if on_status: on_status("Extracting source frames…")
        cmd = [
            fu.ffmpeg_path(), "-y", "-hide_banner",
            "-ss", f"{job.start_seconds:.3f}",
            "-t", f"{span:.3f}",
            "-i", job.input_path,
            "-vsync", "0", "-qscale:v", "1", "-qmin", "1", "-qmax", "1",
            "-progress", "pipe:2",
            os.path.join(in_dir, "%08d.png"),
        ]
        rc, tail = _run_progress(cmd, total=span,
                                 cb=lambda f: on_progress(0.20 * f) if on_progress else None)
        if should_cancel and should_cancel(): return False, "Cancelled"
        if rc != 0:
            return False, f"Extract failed:\n{tail[-1500:]}"

        n_in = len([f for f in os.listdir(in_dir) if f.endswith(".png")])
        _log_diag(f"stage1 extracted={n_in}")

        # --- Stage 2: SeedVR2 inference ---
        if on_status:
            on_status(f"SeedVR2-{job.model_kind.upper()}  ·  "
                      f"upscaling to {job.target_w}×{job.target_h}")
        rc, tail = _run_seedvr(
            in_dir, out_dir, job.model_kind,
            target_h=job.target_h, target_w=job.target_w,
            on_progress=(lambda f: on_progress(0.20 + 0.70 * f)) if on_progress else None,
            on_status=on_status,
        )
        if should_cancel and should_cancel(): return False, "Cancelled"
        if rc != 0:
            return False, f"SeedVR2 inference failed:\n{tail[-3000:]}"

        n_out = len([f for f in os.listdir(out_dir) if f.endswith(".png")])
        _log_diag(f"stage2 produced={n_out} (expected {n_in})")
        if n_out < 1:
            return False, "SeedVR2 produced no output frames. See autosplit.log."

        # --- Stage 2.5: audio (untouched — no tempo change here) ---
        have_audio = False
        if job.keep_audio:
            acmd = [
                fu.ffmpeg_path(), "-y", "-hide_banner",
                "-ss", f"{job.start_seconds:.3f}",
                "-t", f"{span:.3f}",
                "-i", job.input_path,
                "-vn", "-c:a", "aac", "-b:a", job.audio_bitrate,
                audio_path,
            ]
            try:
                subprocess.run(acmd, capture_output=True, **fu._no_window_kwargs())
                have_audio = os.path.exists(audio_path)
            except Exception:
                have_audio = False

        # --- Stage 3: reassemble ---
        if on_status: on_status("Reassembling at source fps")
        rcmd = [
            fu.ffmpeg_path(), "-y", "-hide_banner",
            "-framerate", f"{src_fps}",
            "-i", os.path.join(out_dir, "%08d.png"),
        ]
        if have_audio:
            rcmd += ["-i", audio_path]
        rcmd += [
            "-c:v", "libx264", "-preset", "veryfast",
            "-crf", str(int(job.video_crf)),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ]
        if have_audio:
            rcmd += ["-c:a", "aac", "-b:a", job.audio_bitrate]
        rcmd += ["-progress", "pipe:2", job.output_path]

        out_total = span
        rc, tail = _run_progress(
            rcmd, total=out_total,
            cb=lambda f: on_progress(0.90 + 0.10 * f) if on_progress else None,
        )
        if rc != 0:
            return False, f"Reassemble failed:\n{tail[-1500:]}"

        try:
            info = fu.probe(job.output_path)
            _log_diag(f"OUTPUT duration={info.duration_seconds:.3f}s "
                      f"resolution={info.width}x{info.height}")
        except Exception as e:
            _log_diag(f"probe of output failed: {e}")

        if on_progress: on_progress(1.0)
        return True, job.output_path

    finally:
        cleanup()
