"""Modern QThreadPool-based background workers for probing and thumbnails.

Uses QRunnable so Qt manages threading and worker lifetime. Signals are
emitted through a paired QObject so cross-thread delivery is clean.

Includes an on-disk thumbnail cache keyed on (path, size, mtime) so
reloading the same files is instant.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

import ffmpeg_utils as fu


# --- thumbnail on-disk cache ----------------------------------------------

def _cache_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")))
    d = root / "AutoSplit" / "thumb_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(path: str, w: int, h: int) -> str:
    try:
        st = os.stat(path)
        raw = f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{w}x{h}"
    except OSError:
        raw = f"{os.path.abspath(path)}|{w}x{h}"
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()


def cached_thumbnail(path: str, w: int, h: int) -> Optional[QPixmap]:
    p = _cache_dir() / f"{_cache_key(path, w, h)}.png"
    if p.exists():
        pm = QPixmap(str(p))
        if not pm.isNull():
            return pm
    return None


def save_thumbnail(path: str, pixmap: QPixmap, w: int, h: int) -> None:
    p = _cache_dir() / f"{_cache_key(path, w, h)}.png"
    try:
        pixmap.save(str(p), "PNG")
    except Exception:
        pass


# --- pool -----------------------------------------------------------------

def _pool() -> QThreadPool:
    pool = QThreadPool.globalInstance()
    # Reasonable cap — enough to saturate an SSD, not enough to thrash a slow disk.
    if pool.maxThreadCount() > 6:
        pool.setMaxThreadCount(6)
    return pool


# --- signals bridge -------------------------------------------------------

class TaskSignals(QObject):
    """Signals for a QRunnable. Callers keep a reference until fired."""
    ready = Signal(str, object)   # (path, result)
    failed = Signal(str, str)     # (path, error message)


# --- probe ----------------------------------------------------------------

class _ProbeRunnable(QRunnable):
    def __init__(self, path: str, signals: TaskSignals):
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._sig = signals

    def run(self) -> None:
        try:
            info = fu.probe(self._path)
        except Exception as e:
            self._sig.failed.emit(self._path, str(e))
            return
        self._sig.ready.emit(self._path, info)


def submit_probe(path: str, on_ready, on_failed) -> TaskSignals:
    """Submit a probe job. Returned TaskSignals should be kept alive by the caller
    (assign to a member or dict) until it emits."""
    signals = TaskSignals()
    signals.ready.connect(on_ready)
    signals.failed.connect(on_failed)
    _pool().start(_ProbeRunnable(path, signals))
    return signals


# --- thumbnail ------------------------------------------------------------

class _ThumbRunnable(QRunnable):
    def __init__(self, path: str, duration: float, has_video: bool,
                 w: int, h: int, signals: TaskSignals):
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._dur = duration
        self._has_video = has_video
        self._w = int(w)
        self._h = int(h)
        self._sig = signals

    def _grab_video_frame(self, ts: float) -> tuple[int, bytes, bytes]:
        """Try to grab a video frame at `ts`. Returns (returncode, stdout, stderr)."""
        cmd = [
            fu.ffmpeg_path(),
            "-v", "error",
            "-ss", f"{max(0.0, ts):.3f}",
            "-i", self._path,
            "-frames:v", "1",
            "-vf", f"scale={self._w}:-2",
            "-f", "image2pipe",
            "-vcodec", "png",
            "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, **fu._no_window_kwargs())
        except FileNotFoundError:
            return (127, b"", b"ffmpeg not found")
        return (proc.returncode, proc.stdout or b"", proc.stderr or b"")

    def run(self) -> None:
        # Cache hit → return instantly.
        cached = cached_thumbnail(self._path, self._w, self._h)
        if cached is not None:
            self._sig.ready.emit(self._path, cached)
            return

        if self._has_video:
            # Try 10% into the clip; fall back to 0.5s, then 0.
            targets = []
            if self._dur > 0:
                targets.append(min(self._dur * 0.10, max(0.0, self._dur - 0.1)))
            targets += [0.5, 0.0]
            rc, out, err = 1, b"", b""
            for ts in targets:
                rc, out, err = self._grab_video_frame(ts)
                if rc == 0 and out:
                    break
            if rc != 0 or not out:
                tail = err.decode(errors="replace")[-300:]
                self._sig.failed.emit(self._path, tail or "empty output")
                return
            img = QImage.fromData(out, "PNG")
        else:
            cmd = [
                fu.ffmpeg_path(),
                "-v", "error",
                "-i", self._path,
                "-filter_complex",
                f"aformat=channel_layouts=mono,"
                f"showwavespic=s={self._w}x{self._h}:colors=#4ea3ff",
                "-frames:v", "1",
                "-f", "apng", "-",
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, **fu._no_window_kwargs())
            except FileNotFoundError as e:
                self._sig.failed.emit(self._path, f"ffmpeg not found: {e}")
                return
            if proc.returncode != 0 or not proc.stdout:
                tail = (proc.stderr or b"").decode(errors="replace")[-300:]
                self._sig.failed.emit(self._path, tail or "empty output")
                return
            img = QImage.fromData(proc.stdout, "PNG")

        if img.isNull():
            self._sig.failed.emit(self._path, "decoded image was empty")
            return
        pm = QPixmap.fromImage(img)
        save_thumbnail(self._path, pm, self._w, self._h)
        self._sig.ready.emit(self._path, pm)


def submit_thumbnail(path: str, duration: float, has_video: bool,
                     w: int, h: int, on_ready, on_failed) -> TaskSignals:
    signals = TaskSignals()
    signals.ready.connect(on_ready)
    signals.failed.connect(on_failed)
    _pool().start(_ThumbRunnable(path, duration, has_video, w, h, signals))
    return signals


# --- waveform (with disk cache) -------------------------------------------

def _wave_cache_key(path: str, w: int, h: int) -> str:
    try:
        st = os.stat(path)
        raw = f"WAVE|{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{w}x{h}"
    except OSError:
        raw = f"WAVE|{os.path.abspath(path)}|{w}x{h}"
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()


def cached_waveform(path: str, w: int, h: int) -> Optional[QPixmap]:
    p = _cache_dir() / f"{_wave_cache_key(path, w, h)}.png"
    if p.exists():
        pm = QPixmap(str(p))
        if not pm.isNull():
            return pm
    return None


def save_waveform(path: str, pixmap: QPixmap, w: int, h: int) -> None:
    p = _cache_dir() / f"{_wave_cache_key(path, w, h)}.png"
    try:
        pixmap.save(str(p), "PNG")
    except Exception:
        pass


class _WaveRunnable(QRunnable):
    def __init__(self, path: str, w: int, h: int, signals: TaskSignals):
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._w = int(w)
        self._h = int(h)
        self._sig = signals

    def run(self) -> None:
        cached = cached_waveform(self._path, self._w, self._h)
        if cached is not None:
            self._sig.ready.emit(self._path, cached)
            return

        cmd = [
            fu.ffmpeg_path(),
            "-v", "error",
            "-i", self._path,
            "-filter_complex",
            f"aformat=channel_layouts=mono,"
            f"showwavespic=s={self._w}x{self._h}:colors=#4ea3ff",
            "-frames:v", "1",
            "-f", "apng", "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, **fu._no_window_kwargs())
        except FileNotFoundError as e:
            self._sig.failed.emit(self._path, f"ffmpeg not found: {e}")
            return
        if proc.returncode != 0 or not proc.stdout:
            tail = (proc.stderr or b"").decode(errors="replace")[-300:]
            self._sig.failed.emit(self._path, tail or "empty output")
            return
        img = QImage.fromData(proc.stdout, "PNG")
        if img.isNull():
            self._sig.failed.emit(self._path, "decoded image was empty")
            return
        pm = QPixmap.fromImage(img)
        save_waveform(self._path, pm, self._w, self._h)
        self._sig.ready.emit(self._path, pm)


def submit_waveform(path: str, w: int, h: int, on_ready, on_failed) -> TaskSignals:
    signals = TaskSignals()
    signals.ready.connect(on_ready)
    signals.failed.connect(on_failed)
    _pool().start(_WaveRunnable(path, w, h, signals))
    return signals
