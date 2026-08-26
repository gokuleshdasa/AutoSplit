"""Utilities for the media-joiner module.

Two implementations:
  * Fast join — `ffmpeg -f concat -c copy`  (no re-encode, needs identical params)
  * Re-encode join — `ffmpeg -filter_complex concat=n=N:v=V:a=A`  (always works)

Compatibility check tells the UI which one it can use.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import ffmpeg_utils as fu


# --- natural sort by filename ---------------------------------------------

_NUM_RE = re.compile(r"(\d+)")


def natural_key(text: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in _NUM_RE.split(text)]


def natural_sort(paths: List[str]) -> List[str]:
    return sorted(paths, key=lambda p: natural_key(os.path.basename(p)))


# --- compatibility --------------------------------------------------------

@dataclass
class Compatibility:
    fast_ok: bool
    reason: str = ""            # human-readable when fast_ok is False
    common_has_video: bool = False
    common_has_audio: bool = False


def check_compatibility(infos: List[fu.MediaInfo]) -> Compatibility:
    """Return whether all clips can be stream-copied together."""
    if len(infos) < 2:
        # A single clip is trivially fast (no join needed).
        if infos:
            return Compatibility(True, "",
                                 infos[0].has_video, infos[0].has_audio)
        return Compatibility(False, "No clips")

    first = infos[0]
    has_v = first.has_video
    has_a = first.has_audio

    for i, info in enumerate(infos[1:], 1):
        if info.has_video != has_v:
            return Compatibility(False, f"Clip {i + 1}: has video={info.has_video}, first has {has_v}",
                                 has_v, has_a)
        if info.has_audio != has_a:
            return Compatibility(False, f"Clip {i + 1}: has audio={info.has_audio}, first has {has_a}",
                                 has_v, has_a)
        if has_v:
            if info.video_codec != first.video_codec:
                return Compatibility(False,
                                     f"Clip {i + 1}: video codec {info.video_codec} vs {first.video_codec}",
                                     has_v, has_a)
            if (info.width, info.height) != (first.width, first.height):
                return Compatibility(False,
                                     f"Clip {i + 1}: {info.width}×{info.height} vs {first.width}×{first.height}",
                                     has_v, has_a)
            if info.fps and first.fps and abs(info.fps - first.fps) > 0.02:
                return Compatibility(False,
                                     f"Clip {i + 1}: {info.fps:.3f} fps vs {first.fps:.3f}",
                                     has_v, has_a)
        if has_a and info.audio_codec != first.audio_codec:
            return Compatibility(False,
                                 f"Clip {i + 1}: audio codec {info.audio_codec} vs {first.audio_codec}",
                                 has_v, has_a)
    return Compatibility(True, "", has_v, has_a)


# --- commands -------------------------------------------------------------

def _concat_listfile(paths: List[str]) -> str:
    """Write a temp concat listfile and return its path. Caller deletes."""
    fd, name = tempfile.mkstemp(suffix=".txt", prefix="autosplit_concat_")
    os.close(fd)
    with open(name, "w", encoding="utf-8") as f:
        for p in paths:
            # Escape single quotes for ffmpeg concat format.
            esc = p.replace("\\", "/").replace("'", r"'\''")
            f.write(f"file '{esc}'\n")
    return name


def build_fast_join_cmd(inputs: List[str], output_path: str) -> tuple[list[str], str]:
    """Return (ffmpeg_cmd, listfile_path). Delete the listfile after."""
    listfile = _concat_listfile(inputs)
    cmd = [
        fu.ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-f", "concat",
        "-safe", "0",
        "-i", listfile,
        "-c", "copy",
        "-movflags", "+faststart",
        "-progress", "pipe:2",
        output_path,
    ]
    return cmd, listfile


def build_reencode_join_cmd(
    inputs: List[str],
    output_path: str,
    *,
    output_format: str,
    audio_bitrate: str = "192k",
    video_crf: int = 18,
    has_video: bool = True,
    has_audio: bool = True,
) -> list[str]:
    """Re-encode join via `-filter_complex concat`. Handles mixed inputs."""
    cmd = [fu.ffmpeg_path(), "-y", "-hide_banner"]
    for p in inputs:
        cmd += ["-i", p]

    v = 1 if has_video else 0
    a = 1 if has_audio else 0

    # concat filter chain
    labels = []
    for i in range(len(inputs)):
        if has_video:
            labels.append(f"[{i}:v:0]")
        if has_audio:
            labels.append(f"[{i}:a:0]")
    filter_chain = "".join(labels) + f"concat=n={len(inputs)}:v={v}:a={a}"
    out_labels = []
    if has_video:
        filter_chain += "[outv]"
        out_labels += ["-map", "[outv]"]
    if has_audio:
        filter_chain += "[outa]"
        out_labels += ["-map", "[outa]"]
    cmd += ["-filter_complex", filter_chain] + out_labels

    # Encoder settings — mirror the splitter's catalog logic.
    key = output_format
    crf = str(int(video_crf))
    if key == "mp4_h264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", audio_bitrate, "-movflags", "+faststart"]
    elif key == "mp4_h265":
        cmd += ["-c:v", "libx265", "-preset", "medium", "-crf", crf, "-pix_fmt", "yuv420p",
                "-tag:v", "hvc1", "-c:a", "aac", "-b:a", audio_bitrate, "-movflags", "+faststart"]
    elif key == "mov_h264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", audio_bitrate, "-movflags", "+faststart"]
    elif key == "mov_prores":
        cmd += ["-c:v", "prores_ks", "-profile:v", "3", "-c:a", "pcm_s16le"]
    elif key == "mkv_h264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", audio_bitrate]
    elif key == "webm_vp9":
        cmd += ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", crf, "-row-mt", "1",
                "-c:a", "libopus", "-b:a", audio_bitrate]
    elif key == "avi_h264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-pix_fmt", "yuv420p",
                "-c:a", "libmp3lame", "-b:a", audio_bitrate]
    elif key == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", audio_bitrate]
    elif key == "wav":
        cmd += ["-c:a", "pcm_s16le"]
    elif key == "m4a_aac":
        cmd += ["-c:a", "aac", "-b:a", audio_bitrate, "-movflags", "+faststart"]
    elif key == "flac":
        cmd += ["-c:a", "flac", "-compression_level", "5"]
    elif key == "ogg_opus":
        cmd += ["-c:a", "libopus", "-b:a", audio_bitrate]
    elif key == "ogg_vorbis":
        cmd += ["-c:a", "libvorbis", "-b:a", audio_bitrate]
    else:
        raise ValueError(f"Unsupported output format: {key}")

    cmd += ["-progress", "pipe:2", output_path]
    return cmd


# --- default output path --------------------------------------------------

def default_join_output_path(
    inputs: List[str],
    settings_dir: str,
    extension: str,
) -> str:
    """Pick a sensible default output path for a Quick join.

    Preference: `settings_dir` (from Preferences) if set and writable;
    otherwise the folder of the first input.
    Filename: `joined_YYYYMMDD_HHMMSS.<ext>`.
    """
    import datetime
    folder = ""
    if settings_dir and os.path.isdir(settings_dir) and os.access(settings_dir, os.W_OK):
        folder = settings_dir
    else:
        folder = str(Path(inputs[0]).parent) if inputs else str(Path.home())
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(folder, f"joined_{stamp}{extension}")


# --- runner ---------------------------------------------------------------

def run_ffmpeg_streaming(
    cmd: list[str],
    *,
    total_duration: float,
    on_progress: Optional[Callable[[float], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> tuple[int, str]:
    """Run ffmpeg, reporting fractional progress (0..1) based on out_time_ms."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        **fu._no_window_kwargs(),
    )
    tail: list[str] = []
    assert proc.stderr is not None
    time_re = re.compile(r"out_time_ms=(\d+)")
    fallback_re = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
    try:
        for line in proc.stderr:
            tail.append(line)
            if len(tail) > 60:
                tail.pop(0)
            if on_progress is not None and total_duration > 0:
                m = time_re.search(line)
                if m:
                    t = int(m.group(1)) / 1_000_000.0
                    on_progress(min(t / total_duration, 1.0))
                else:
                    m2 = fallback_re.search(line)
                    if m2:
                        h, mnt, s = m2.groups()
                        t = int(h) * 3600 + int(mnt) * 60 + float(s)
                        on_progress(min(t / total_duration, 1.0))
            if should_cancel is not None and should_cancel():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return -1, "".join(tail)
    finally:
        proc.wait()
    return proc.returncode, "".join(tail)
