"""FFmpeg / ffprobe wrappers.

Frame-accurate splitting: uses `-ss` after `-i` so seek is decoded, and always
re-encodes so each segment lands on the exact requested duration.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


def _no_window_kwargs() -> dict:
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def bundled_bin_dir() -> Path:
    """Return the directory holding bundled ffmpeg/ffprobe binaries.

    Checks (in order):
      1. PyInstaller `_MEIPASS` \\vendor\\ffmpeg
      2. <project root>\\vendor\\ffmpeg (development)
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        cand = base / "vendor" / "ffmpeg"
        if cand.exists():
            return cand
    # dev: src/ffmpeg_utils.py -> project root is parent of src/
    return Path(__file__).resolve().parent.parent / "vendor" / "ffmpeg"


def _resolve_exe(name: str) -> str:
    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    bundled = bundled_bin_dir() / exe_name
    if bundled.exists():
        return str(bundled)
    # Fall back to PATH.
    return exe_name


def ffmpeg_path() -> str:
    return _resolve_exe("ffmpeg")


def ffprobe_path() -> str:
    return _resolve_exe("ffprobe")


def ffmpeg_available() -> bool:
    try:
        subprocess.run(
            [ffmpeg_path(), "-version"],
            capture_output=True,
            check=True,
            **_no_window_kwargs(),
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@dataclass
class MediaInfo:
    path: str
    duration_seconds: float
    has_video: bool
    has_audio: bool
    video_codec: Optional[str]
    audio_codec: Optional[str]
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float] = None

    @property
    def is_video(self) -> bool:
        return self.has_video

    @property
    def is_audio_only(self) -> bool:
        return self.has_audio and not self.has_video

    def frame_period(self) -> float:
        """Seconds per frame, or a sensible fallback."""
        if self.fps and self.fps > 0:
            return 1.0 / self.fps
        return 0.04   # 25 fps default


def _parse_fraction(text: str) -> Optional[float]:
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            n = float(num); d = float(den)
            if d == 0:
                return None
            return n / d
        return float(text)
    except (ValueError, TypeError):
        return None


def probe(path: str) -> MediaInfo:
    """Return media info via ffprobe. Raises RuntimeError on failure."""
    cmd = [
        ffprobe_path(),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, **_no_window_kwargs()
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

    data = json.loads(result.stdout or "{}")
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    try:
        duration = float(fmt.get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    fps = None
    if v_stream is not None:
        # Prefer avg_frame_rate; fall back to r_frame_rate.
        for k in ("avg_frame_rate", "r_frame_rate"):
            val = _parse_fraction(v_stream.get(k, ""))
            if val and val > 0:
                fps = val
                break

    return MediaInfo(
        path=path,
        duration_seconds=duration,
        has_video=v_stream is not None,
        has_audio=a_stream is not None,
        video_codec=v_stream.get("codec_name") if v_stream else None,
        audio_codec=a_stream.get("codec_name") if a_stream else None,
        width=v_stream.get("width") if v_stream else None,
        height=v_stream.get("height") if v_stream else None,
        fps=fps,
    )


# Extract HH:MM:SS.xx from ffmpeg stderr for progress tracking.
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _parse_time_stderr(line: str) -> Optional[float]:
    m = _TIME_RE.search(line)
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


@dataclass
class SegmentSpec:
    index: int              # 1-based
    start_seconds: float
    duration_seconds: float
    output_path: str


def build_segments(
    *,
    total_duration: float,
    start_offset: float,
    split_duration: float,
    output_dir: str,
    prefix: str,
    suffix: str,
    extension: str,
    pad_width: int = 3,
    overwrite: bool = False,
) -> list[SegmentSpec]:
    """Compute the list of segment specs to produce."""
    if split_duration <= 0:
        raise ValueError("Split duration must be positive.")
    if start_offset < 0:
        start_offset = 0.0
    if start_offset >= total_duration:
        raise ValueError("Start time is at or beyond the end of the media.")

    segments: list[SegmentSpec] = []
    remaining = total_duration - start_offset
    n = int(remaining // split_duration)
    if remaining - n * split_duration > 0.001:
        n += 1  # short trailing segment

    for i in range(n):
        seg_start = start_offset + i * split_duration
        seg_dur = min(split_duration, total_duration - seg_start)
        if seg_dur <= 0:
            break
        name = f"{prefix}{str(i + 1).zfill(pad_width)}{suffix}{extension}"
        out_path = os.path.join(output_dir, name)
        if not overwrite:
            out_path = _unique_path(out_path)
        segments.append(SegmentSpec(i + 1, seg_start, seg_dur, out_path))
    return segments


def _unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 2
    while True:
        candidate = f"{root} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


# ----- Format catalog ------------------------------------------------------

# Each key is what we pass to build_ffmpeg_command.
# 'ext' is the output extension. 'kind' is 'video' or 'audio'.
# 'label' is what the UI shows.
FORMAT_CATALOG: dict[str, dict] = {
    # --- Video ---
    "mp4_h264":     {"label": "MP4  ·  H.264 + AAC",           "ext": ".mp4",  "kind": "video"},
    "mp4_h265":     {"label": "MP4  ·  H.265/HEVC + AAC",      "ext": ".mp4",  "kind": "video"},
    "mov_h264":     {"label": "MOV  ·  H.264 + AAC",           "ext": ".mov",  "kind": "video"},
    "mov_prores":   {"label": "MOV  ·  ProRes 422 + PCM",      "ext": ".mov",  "kind": "video"},
    "mkv_h264":     {"label": "MKV  ·  H.264 + AAC",           "ext": ".mkv",  "kind": "video"},
    "webm_vp9":     {"label": "WebM ·  VP9 + Opus",            "ext": ".webm", "kind": "video"},
    "avi_h264":     {"label": "AVI  ·  H.264 + MP3",           "ext": ".avi",  "kind": "video"},
    # --- Audio ---
    "mp3":          {"label": "MP3  ·  libmp3lame",            "ext": ".mp3",  "kind": "audio"},
    "wav":          {"label": "WAV  ·  PCM 16-bit (lossless)", "ext": ".wav",  "kind": "audio"},
    "m4a_aac":      {"label": "M4A  ·  AAC",                   "ext": ".m4a",  "kind": "audio"},
    "flac":         {"label": "FLAC ·  lossless",              "ext": ".flac", "kind": "audio"},
    "ogg_opus":     {"label": "OGG  ·  Opus",                  "ext": ".opus", "kind": "audio"},
    "ogg_vorbis":   {"label": "OGG  ·  Vorbis",                "ext": ".ogg",  "kind": "audio"},
}

# Bitrate suggestions per lossy format. Higher is better/larger.
AUDIO_BITRATES = ["96k", "128k", "160k", "192k", "256k", "320k"]

# Video quality presets: (label, CRF or bitrate). CRF is quality-based, x264/x265 friendly.
VIDEO_QUALITY = [
    ("Visually lossless (CRF 16)", 16),
    ("High quality (CRF 18)",      18),
    ("Balanced (CRF 20)",          20),
    ("Smaller file (CRF 23)",      23),
    ("Small file (CRF 26)",        26),
]

# Whether the given format uses a lossy audio codec (i.e. bitrate matters).
LOSSY_AUDIO_FORMATS = {"mp4_h264", "mp4_h265", "mov_h264", "mkv_h264", "webm_vp9",
                       "avi_h264", "mp3", "m4a_aac", "ogg_opus", "ogg_vorbis"}
LOSSY_VIDEO_FORMATS = {"mp4_h264", "mp4_h265", "mov_h264", "mkv_h264",
                       "webm_vp9", "avi_h264"}


def format_extension(fmt_key: str) -> str:
    return FORMAT_CATALOG[fmt_key]["ext"]


def format_kind(fmt_key: str) -> str:
    return FORMAT_CATALOG[fmt_key]["kind"]


def build_ffmpeg_command(
    *,
    input_path: str,
    segment: SegmentSpec,
    output_format: str,           # key from FORMAT_CATALOG
    audio_bitrate: str = "192k",
    video_crf: int = 18,
    click_fade_ms: float = 0.0,   # >0 = apply a fade-in/out on cut boundaries
) -> list[str]:
    """Build a frame-accurate re-encode command for a single segment.

    Uses `-ss` after `-i` (decoded seek) so every segment lands on the exact
    requested duration.
    """
    key = output_format
    if key not in FORMAT_CATALOG:
        # Back-compat for legacy short keys 'mp4' / 'mov' / 'mp3'.
        legacy = {"mp4": "mp4_h264", "mov": "mov_h264", "mp3": "mp3"}
        key = legacy.get(key.lower(), None)
        if key is None:
            raise ValueError(f"Unsupported output format: {output_format}")

    crf = str(int(video_crf))

    cmd = [
        ffmpeg_path(),
        "-y",
        "-i", input_path,
        "-ss", f"{segment.start_seconds:.3f}",
        "-t", f"{segment.duration_seconds:.3f}",
    ]

    if key == "mp4_h264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", audio_bitrate,
                "-movflags", "+faststart"]
    elif key == "mp4_h265":
        cmd += ["-c:v", "libx265", "-preset", "medium", "-crf", crf,
                "-pix_fmt", "yuv420p", "-tag:v", "hvc1",
                "-c:a", "aac", "-b:a", audio_bitrate,
                "-movflags", "+faststart"]
    elif key == "mov_h264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", audio_bitrate,
                "-movflags", "+faststart"]
    elif key == "mov_prores":
        cmd += ["-c:v", "prores_ks", "-profile:v", "3",
                "-c:a", "pcm_s16le"]
    elif key == "mkv_h264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", audio_bitrate]
    elif key == "webm_vp9":
        cmd += ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", crf,
                "-row-mt", "1", "-c:a", "libopus", "-b:a", audio_bitrate]
    elif key == "avi_h264":
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                "-pix_fmt", "yuv420p", "-c:a", "libmp3lame", "-b:a", audio_bitrate]
    elif key == "mp3":
        cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", audio_bitrate]
    elif key == "wav":
        cmd += ["-vn", "-c:a", "pcm_s16le"]
    elif key == "m4a_aac":
        cmd += ["-vn", "-c:a", "aac", "-b:a", audio_bitrate,
                "-movflags", "+faststart"]
    elif key == "flac":
        cmd += ["-vn", "-c:a", "flac", "-compression_level", "5"]
    elif key == "ogg_opus":
        cmd += ["-vn", "-c:a", "libopus", "-b:a", audio_bitrate]
    elif key == "ogg_vorbis":
        cmd += ["-vn", "-c:a", "libvorbis", "-b:a", audio_bitrate]
    else:
        raise ValueError(f"Unsupported output format key: {key}")

    # Optional: fade-in/out on cut boundaries so a mid-waveform cut doesn't
    # produce an audible click. Only applied to formats that have audio.
    if click_fade_ms and click_fade_ms > 0 and key not in ("wav",):
        # Are we writing an audio-only container? Skip video streams for those.
        fade_s = click_fade_ms / 1000.0
        # Guard against segments shorter than 2x fade.
        if segment.duration_seconds > 2.5 * fade_s:
            fade_out_start = segment.duration_seconds - fade_s
            af = f"afade=t=in:st=0:d={fade_s},afade=t=out:st={fade_out_start:.3f}:d={fade_s}"
            cmd += ["-af", af]

    cmd += ["-progress", "pipe:2", segment.output_path]
    return cmd


def run_ffmpeg(
    cmd: list[str],
    *,
    on_progress: Optional[Callable[[float], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> tuple[int, str]:
    """Run ffmpeg, streaming stderr. Returns (returncode, tail_of_stderr)."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        **_no_window_kwargs(),
    )
    tail: list[str] = []
    assert proc.stderr is not None
    try:
        for line in proc.stderr:
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
            if on_progress is not None:
                t = _parse_time_stderr(line)
                if t is not None:
                    on_progress(t)
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


def is_supported_media(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in VIDEO_EXTS or ext in AUDIO_EXTS
