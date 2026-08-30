"""Dialogs for frame extraction and GIF export."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QRadioButton, QVBoxLayout, QWidget,
)

import ffmpeg_utils as fu
from settings import AppSettings


def _fmt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - (h * 3600 + m * 60)
    if h:
        return f"{h:d}:{m:02d}:{s:06.3f}"
    return f"{m:02d}:{s:06.3f}"


# ------ Single frame ---------------------------------------------------------

class SingleFrameDialog(QDialog):
    """Choose format + destination for a single-frame grab."""

    def __init__(self, settings: AppSettings, source_path: str,
                 timestamp: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Extract Frame")
        self.setModal(True)
        self.resize(520, 240)

        self._settings = settings
        self._source = source_path
        self._timestamp = timestamp

        v = QVBoxLayout(self)
        info = QLabel(
            f"<b>Timestamp:</b> {_fmt_time(timestamp)}<br>"
            f"<b>Source:</b> {os.path.basename(source_path)}"
        )
        info.setWordWrap(True)
        v.addWidget(info)

        form = QFormLayout()
        v.addLayout(form)

        self.format_combo = QComboBox()
        for label, key in (("PNG (lossless)", "png"),
                           ("JPEG (small)",  "jpg"),
                           ("WebP",          "webp")):
            self.format_combo.addItem(label, key)
        idx = self.format_combo.findData(settings.frame_format)
        if idx >= 0:
            self.format_combo.setCurrentIndex(idx)
        form.addRow("Format:", self.format_combo)

        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(2, 31)
        self.quality_spin.setValue(settings.frame_jpg_quality)
        self.quality_spin.setSuffix("   (lower = better; PNG ignores this)")
        form.addRow("JPEG/WebP quality:", self.quality_spin)

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setText(self._suggested_path(settings.frame_format))
        row.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        row.addWidget(browse)
        wrap = QWidget(); wrap.setLayout(row)
        form.addRow("Save as:", wrap)

        self.format_combo.currentIndexChanged.connect(self._on_format_changed)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _suggested_path(self, ext: str) -> str:
        folder = self._settings.frame_output_dir or os.path.dirname(self._source)
        base = os.path.splitext(os.path.basename(self._source))[0]
        stamp = f"{int(self._timestamp):04d}s"
        return os.path.join(folder, f"{base}_frame_{stamp}.{ext}")

    def _on_format_changed(self, *_) -> None:
        fmt = self.format_combo.currentData()
        # Rewrite extension in the current path.
        current = self.path_edit.text().strip()
        if current:
            root, _ = os.path.splitext(current)
            self.path_edit.setText(f"{root}.{fmt}")

    def _on_browse(self) -> None:
        fmt = self.format_combo.currentData() or "png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save frame as", self.path_edit.text(),
            f"{fmt.upper()} (*.{fmt})"
        )
        if path:
            self.path_edit.setText(path)

    def selection(self) -> dict:
        return {
            "format": self.format_combo.currentData() or "png",
            "quality": self.quality_spin.value(),
            "output_path": self.path_edit.text().strip(),
        }


# ------ All frames ----------------------------------------------------------

class AllFramesDialog(QDialog):
    def __init__(self, settings: AppSettings, source_path: str,
                 in_seconds: float, out_seconds: float, media_fps: Optional[float],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Extract All Frames")
        self.setModal(True)
        self.resize(560, 460)

        self._settings = settings
        self._source = source_path
        self._in = in_seconds
        self._out = out_seconds
        self._fps = media_fps

        v = QVBoxLayout(self)

        gb = QGroupBox("Format")
        form = QFormLayout(gb)
        self.format_combo = QComboBox()
        for label, key in (("PNG (lossless)", "png"),
                           ("JPEG (small)",  "jpg"),
                           ("WebP",          "webp")):
            self.format_combo.addItem(label, key)
        idx = self.format_combo.findData(settings.frame_format)
        if idx >= 0:
            self.format_combo.setCurrentIndex(idx)
        form.addRow("Image format:", self.format_combo)

        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(2, 31)
        self.quality_spin.setValue(settings.frame_jpg_quality)
        form.addRow("JPEG/WebP quality (lower=better):", self.quality_spin)
        v.addWidget(gb)

        rate_gb = QGroupBox("Frame rate")
        rform = QFormLayout(rate_gb)
        self.rate_combo = QComboBox()
        self.rate_combo.addItem("Every frame (source rate)", 0.0)
        for n in (0.5, 1, 2, 5, 10, 15, 24, 30):
            self.rate_combo.addItem(f"{n} frame/s", float(n))
        cur_rate = settings.frame_all_fps
        for i in range(self.rate_combo.count()):
            if abs(self.rate_combo.itemData(i) - cur_rate) < 1e-6:
                self.rate_combo.setCurrentIndex(i)
                break
        rform.addRow("Rate:", self.rate_combo)

        if media_fps:
            rform.addRow(QLabel(f"Source: {media_fps:.3f} fps", objectName="Muted"))
        v.addWidget(rate_gb)

        range_gb = QGroupBox("Range")
        rgv = QVBoxLayout(range_gb)
        self.range_full = QRadioButton("Full media")
        self.range_range = QRadioButton(
            f"IN → OUT range   ({_fmt_time(in_seconds)} → {_fmt_time(out_seconds)})"
        )
        if settings.frame_range == "full":
            self.range_full.setChecked(True)
        else:
            self.range_range.setChecked(True)
        rgv.addWidget(self.range_full)
        rgv.addWidget(self.range_range)
        v.addWidget(range_gb)

        out_gb = QGroupBox("Output folder")
        og = QHBoxLayout(out_gb)
        self.folder_edit = QLineEdit()
        default_folder = settings.frame_output_dir or os.path.join(
            os.path.dirname(source_path),
            os.path.splitext(os.path.basename(source_path))[0] + "_frames"
        )
        self.folder_edit.setText(default_folder)
        og.addWidget(self.folder_edit, 1)
        pick = QPushButton("Browse…")
        pick.clicked.connect(self._on_browse)
        og.addWidget(pick)
        v.addWidget(out_gb)

        self.prefix_edit = QLineEdit("frame")
        form2 = QFormLayout()
        form2.addRow("Filename prefix:", self.prefix_edit)
        v.addLayout(form2)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _on_browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose output folder",
                                             self.folder_edit.text())
        if d:
            self.folder_edit.setText(d)

    def selection(self) -> dict:
        return {
            "format": self.format_combo.currentData() or "png",
            "quality": self.quality_spin.value(),
            "fps": self.rate_combo.currentData(),
            "range": "full" if self.range_full.isChecked() else "in_out",
            "output_dir": self.folder_edit.text().strip(),
            "prefix": self.prefix_edit.text().strip() or "frame",
        }


# ------ GIF export ----------------------------------------------------------

class GifExportDialog(QDialog):
    def __init__(self, settings: AppSettings, source_path: str,
                 in_seconds: float, out_seconds: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export GIF")
        self.setModal(True)
        self.resize(560, 460)

        self._settings = settings
        self._source = source_path
        self._in = in_seconds
        self._out = out_seconds

        v = QVBoxLayout(self)

        info = QLabel(
            "GIF export uses a two-pass palette (palettegen + paletteuse) — "
            "the best-quality open-source path in FFmpeg."
        )
        info.setWordWrap(True)
        info.setObjectName("Muted")
        v.addWidget(info)

        gb = QGroupBox("Quality")
        form = QFormLayout(gb)

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(2, 60)
        self.fps_spin.setValue(settings.gif_fps)
        form.addRow("Frame rate (fps):", self.fps_spin)

        self.width_combo = QComboBox()
        for w in (240, 320, 480, 640, 720, 960, 1080, 1280):
            self.width_combo.addItem(f"{w} px wide", w)
        self.width_combo.addItem("Original", 0)
        for i in range(self.width_combo.count()):
            if self.width_combo.itemData(i) == settings.gif_width:
                self.width_combo.setCurrentIndex(i)
                break
        form.addRow("Width:", self.width_combo)

        self.dither_combo = QComboBox()
        for label, key in (
            ("Bayer (fast, clean)",       "bayer"),
            ("Floyd-Steinberg (smooth)",  "floyd_steinberg"),
            ("Sierra 2-4a (smooth)",      "sierra2_4a"),
            ("None (banded, small)",      "none"),
        ):
            self.dither_combo.addItem(label, key)
        idx = self.dither_combo.findData(settings.gif_dither)
        if idx >= 0:
            self.dither_combo.setCurrentIndex(idx)
        form.addRow("Dither:", self.dither_combo)

        self.loop_cb = QCheckBox("Loop forever")
        self.loop_cb.setChecked(settings.gif_loop_forever)
        form.addRow("Playback:", self.loop_cb)
        v.addWidget(gb)

        range_gb = QGroupBox("Range")
        rgv = QVBoxLayout(range_gb)
        self.range_full = QRadioButton("Full media")
        self.range_range = QRadioButton(
            f"IN → OUT range   ({_fmt_time(in_seconds)} → {_fmt_time(out_seconds)})"
        )
        self.range_range.setChecked(True)
        rgv.addWidget(self.range_range)
        rgv.addWidget(self.range_full)
        v.addWidget(range_gb)

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setText(self._suggested_path())
        row.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        row.addWidget(browse)
        wrap = QWidget(); wrap.setLayout(row)
        form2 = QFormLayout()
        form2.addRow("Save as:", wrap)
        v.addLayout(form2)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _suggested_path(self) -> str:
        folder = self._settings.gif_output_dir or os.path.dirname(self._source)
        base = os.path.splitext(os.path.basename(self._source))[0]
        return os.path.join(folder, f"{base}.gif")

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save GIF as", self.path_edit.text(), "GIF (*.gif)"
        )
        if path:
            self.path_edit.setText(path)

    def selection(self) -> dict:
        return {
            "fps": self.fps_spin.value(),
            "width": self.width_combo.currentData() or 0,
            "dither": self.dither_combo.currentData() or "bayer",
            "loop_forever": self.loop_cb.isChecked(),
            "range": "full" if self.range_full.isChecked() else "in_out",
            "output_path": self.path_edit.text().strip(),
        }
