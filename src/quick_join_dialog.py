"""Small modeless progress window used by --join-quick.

Collects paths, probes them, picks fast vs re-encode, runs ffmpeg, and reports.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QLabel, QProgressBar, QPushButton, QMessageBox, QVBoxLayout,
    QHBoxLayout, QWidget,
)

import ffmpeg_utils as fu
import join_utils as ju
import settings as app_settings


class _JoinWorker(QThread):
    progress = Signal(float)
    done_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, cmd: list[str], total: float, out_path: str,
                 listfile: str | None = None, parent=None):
        super().__init__(parent)
        self._cmd = cmd
        self._total = total
        self._out = out_path
        self._listfile = listfile
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        rc, tail = ju.run_ffmpeg_streaming(
            self._cmd,
            total_duration=self._total,
            on_progress=lambda f: self.progress.emit(f),
            should_cancel=lambda: self._cancel,
        )
        if self._listfile and os.path.exists(self._listfile):
            try:
                os.remove(self._listfile)
            except OSError:
                pass
        if rc == 0:
            self.done_ok.emit(self._out)
        else:
            self.failed.emit(f"ffmpeg exited with {rc}\n\n{tail[-1500:]}")


class QuickJoinDialog(QDialog):
    """Small window shown during a headless --join-quick run."""

    def __init__(self, collector, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AutoSplit — Quick Join")
        self.setModal(False)
        self.resize(520, 180)
        self.setStyleSheet(
            "QDialog { background: #202020; color: #e8e8e8; }"
            "QLabel { color: #e8e8e8; font-family: 'Segoe UI', sans-serif; }"
            "QProgressBar { background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 4px; text-align: center; }"
            "QProgressBar::chunk { background: #0078d4; border-radius: 3px; }"
            "QPushButton { background: #2f2f2f; border: 1px solid #3a3a3a; border-radius: 4px; padding: 6px 14px; color: #e8e8e8; }"
        )

        self._collector = collector
        self._settings = app_settings.load()
        self._worker: _JoinWorker | None = None

        v = QVBoxLayout(self)
        self.title = QLabel("<b>Collecting selected files…</b>")
        self.title.setStyleSheet("font-size: 12pt;")
        v.addWidget(self.title)
        self.detail = QLabel("Waiting for Windows Explorer to send all selected files.")
        self.detail.setWordWrap(True)
        v.addWidget(self.detail)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        v.addWidget(self.progress)

        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        row.addWidget(self.cancel_btn)
        v.addLayout(row)

        collector.added.connect(self._on_added)
        collector.ready.connect(self._on_ready)

    def _on_added(self, path: str) -> None:
        self.detail.setText(f"Collected {len(self._collector.paths())} file(s)…")

    def _on_ready(self, paths: list) -> None:
        self._collector.stop()
        self._collector = None
        if not paths:
            self._error("No files were sent.")
            return
        self.title.setText(f"<b>Reading {len(paths)} selected file(s)…</b>")
        self._start_join(list(paths))

    def _start_join(self, paths: list[str]) -> None:
        # Natural-sort by filename.
        paths = ju.natural_sort(paths)

        infos = []
        skipped = []
        for p in paths:
            try:
                info = fu.probe(p)
                if info.duration_seconds > 0:
                    infos.append(info)
                    continue
            except Exception:
                pass
            skipped.append(p)

        if len(infos) < 2:
            if skipped:
                names = "\n".join(os.path.basename(p) for p in skipped[:12])
                if len(skipped) > 12:
                    names += f"\n… and {len(skipped) - 12} more"
                self._error("The selected files could not be read:\n\n" + names)
            else:
                self._error("Need at least 2 valid media files to join.")
            return

        if skipped:
            names = "\n".join(os.path.basename(p) for p in skipped[:12])
            if len(skipped) > 12:
                names += f"\n… and {len(skipped) - 12} more"
            self.title.setText(
                f"<b>Joining {len(infos)} clip(s)…</b>  ({len(skipped)} skipped)")
            QMessageBox.warning(
                self, "Some files skipped",
                f"{len(skipped)} selected file(s) could not be read and were skipped:\n\n{names}")
        else:
            self.title.setText(f"<b>Joining {len(infos)} clip(s)…</b>")

        compat = ju.check_compatibility(infos)
        # Pick container/format.
        if compat.common_has_video:
            fmt_key = self._settings.join_output_format_video
        else:
            fmt_key = self._settings.join_output_format_audio
        ext = fu.format_extension(fmt_key)

        out_path = ju.default_join_output_path(
            [i.path for i in infos], self._settings.join_output_dir, ext
        )
        # If pointing at an existing file, add ` (n)`.
        base, e = os.path.splitext(out_path)
        i = 2
        while os.path.exists(out_path):
            out_path = f"{base} ({i}){e}"
            i += 1

        force_reencode = self._settings.join_always_reencode or not compat.fast_ok
        # If input container differs from chosen output, we must re-encode.
        if not force_reencode:
            in_ext = os.path.splitext(infos[0].path)[1].lower()
            if in_ext != ext.lower():
                force_reencode = True

        listfile = None
        if not force_reencode:
            cmd, listfile = ju.build_fast_join_cmd([i.path for i in infos], out_path)
            self.detail.setText(f"Fast-joining (stream copy) → {os.path.basename(out_path)}")
        else:
            cmd = ju.build_reencode_join_cmd(
                [i.path for i in infos], out_path,
                output_format=fmt_key,
                audio_bitrate=self._settings.audio_bitrate,
                video_crf=self._settings.video_crf,
                has_video=compat.common_has_video,
                has_audio=compat.common_has_audio,
            )
            self.detail.setText(f"Re-encoding → {os.path.basename(out_path)}")

        total = sum(i.duration_seconds for i in infos)
        self._worker = _JoinWorker(cmd, total, out_path, listfile, parent=self)
        self._worker.progress.connect(lambda f: self.progress.setValue(int(f * 1000)))
        self._worker.done_ok.connect(self._on_done_ok)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_done_ok(self, out_path: str) -> None:
        self.progress.setValue(1000)
        self.title.setText("<b>Join complete ✓</b>")
        self.detail.setText(out_path)
        self.cancel_btn.setText("Close")
        self.cancel_btn.clicked.disconnect()
        self.cancel_btn.clicked.connect(self.accept)
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", out_path])
        except Exception:
            pass

    def _on_failed(self, msg: str) -> None:
        self.title.setText("<b>Join failed</b>")
        self.detail.setText(msg[:200])
        self.cancel_btn.setText("Close")
        self.cancel_btn.clicked.disconnect()
        self.cancel_btn.clicked.connect(self.accept)
        QMessageBox.critical(self, "Join failed", msg)

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self, "Nothing to join", msg)
        self.reject()

    def _on_cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        self.reject()
