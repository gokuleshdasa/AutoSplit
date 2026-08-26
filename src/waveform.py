"""Generate a waveform PNG via ffmpeg's showwavespic filter, on a worker thread."""
from __future__ import annotations

import subprocess

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage, QPixmap

from ffmpeg_utils import ffmpeg_path, _no_window_kwargs


class WaveformWorker(QObject):
    ready = Signal(QPixmap)     # emitted with the rendered waveform
    failed = Signal(str)

    def __init__(self, input_path: str, width: int = 2400, height: int = 96):
        super().__init__()
        self._path = input_path
        self._w = int(width)
        self._h = int(height)

    def run(self) -> None:
        cmd = [
            ffmpeg_path(),
            "-v", "error",
            "-i", self._path,
            "-filter_complex",
            f"aformat=channel_layouts=mono,"
            f"showwavespic=s={self._w}x{self._h}:colors=#4ea3ff",
            "-frames:v", "1",
            "-f", "apng", "-",
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                **_no_window_kwargs(),
            )
        except FileNotFoundError as e:
            self.failed.emit(f"ffmpeg not found: {e}")
            return

        if proc.returncode != 0 or not proc.stdout:
            tail = (proc.stderr or b"").decode(errors="replace")[-500:]
            self.failed.emit(f"waveform generation failed: {tail}")
            return

        img = QImage.fromData(proc.stdout, "PNG")
        if img.isNull():
            self.failed.emit("waveform image was empty")
            return
        self.ready.emit(QPixmap.fromImage(img))


def start_waveform(input_path: str, width: int, height: int) -> tuple[QThread, WaveformWorker]:
    thread = QThread()
    worker = WaveformWorker(input_path, width=width, height=height)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    # Caller connects ready/failed and quits the thread when done.
    return thread, worker
