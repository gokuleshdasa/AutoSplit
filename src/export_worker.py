"""Runs the segment export loop on a worker thread with Qt signals."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from PySide6.QtCore import QObject, QThread, Signal

from ffmpeg_utils import SegmentSpec, build_ffmpeg_command, run_ffmpeg


@dataclass
class ExportJob:
    input_path: str
    segments: List[SegmentSpec]
    output_format: str
    audio_bitrate: str = "192k"
    video_crf: int = 18
    click_fade_ms: float = 0.0


class ExportWorker(QObject):
    # (segment_index_1_based, segment_progress_0_to_1, overall_progress_0_to_1)
    progress = Signal(int, float, float)
    # (segment_index_1_based, total_segments, output_path)
    segment_done = Signal(int, int, str)
    # (returncode, stderr_tail, failed_output_path)
    segment_failed = Signal(int, str, str)
    finished = Signal(bool)  # True on full success
    log = Signal(str)

    def __init__(self, job: ExportJob):
        super().__init__()
        self.job = job
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        total = len(self.job.segments)
        if total == 0:
            self.finished.emit(False)
            return

        completed_duration = 0.0
        total_duration = sum(s.duration_seconds for s in self.job.segments) or 1.0
        all_ok = True

        for seg in self.job.segments:
            if self._cancel:
                all_ok = False
                self.log.emit("Cancelled by user.")
                break

            self.log.emit(f"Exporting part {seg.index}/{total} → {seg.output_path}")

            cmd = build_ffmpeg_command(
                input_path=self.job.input_path,
                segment=seg,
                output_format=self.job.output_format,
                audio_bitrate=self.job.audio_bitrate,
                video_crf=self.job.video_crf,
                click_fade_ms=self.job.click_fade_ms,
            )

            def on_prog(t: float, _seg=seg):
                seg_frac = 0.0 if seg.duration_seconds <= 0 else min(t / seg.duration_seconds, 1.0)
                overall = (completed_duration + t) / total_duration
                overall = min(max(overall, 0.0), 1.0)
                self.progress.emit(_seg.index, seg_frac, overall)

            rc, tail = run_ffmpeg(
                cmd,
                on_progress=on_prog,
                should_cancel=lambda: self._cancel,
            )

            if rc == 0:
                completed_duration += seg.duration_seconds
                self.segment_done.emit(seg.index, total, seg.output_path)
                self.progress.emit(seg.index, 1.0, completed_duration / total_duration)
            else:
                all_ok = False
                self.segment_failed.emit(rc, tail, seg.output_path)
                # Stop on first failure — surfacing all errors is a later refinement.
                break

        self.finished.emit(all_ok and not self._cancel)


def start_export(job: ExportJob) -> tuple[QThread, ExportWorker]:
    """Wire an ExportWorker onto its own QThread. Caller owns lifetime."""
    thread = QThread()
    worker = ExportWorker(job)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    return thread, worker
