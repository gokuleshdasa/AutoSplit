"""Dialog for speed change / slow-motion export."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

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


class SpeedExportDialog(QDialog):
    """Options: speed factor, range, motion interpolation, output format + path."""

    PRESETS = [
        ("0.25x   Extreme slow-motion", 0.25),
        ("0.5x    Slow-motion",         0.5),
        ("0.75x   Slight slow",         0.75),
        ("1.5x    Slight fast",         1.5),
        ("2x      Fast forward",        2.0),
        ("4x      Very fast",           4.0),
        ("Custom…",                     0.0),
    ]

    def __init__(self, settings: AppSettings, source_path: str,
                 in_seconds: float, out_seconds: float, parent=None,
                 default_range: str = "in_out"):
        super().__init__(parent)
        self.setWindowTitle("Speed change / Slow motion")
        self.setModal(True)
        self.resize(580, 520)

        self._settings = settings
        self._source = source_path
        self._in = in_seconds
        self._out = out_seconds

        v = QVBoxLayout(self)

        v.addWidget(QLabel(
            "Change the playback speed of a clip. Values below 1x = slow-motion, "
            "above 1x = fast-forward. Audio pitch is preserved."
        ))

        # --- Speed ---
        speed_gb = QGroupBox("Speed")
        form = QFormLayout(speed_gb)

        self.preset_combo = QComboBox()
        for label, val in self.PRESETS:
            self.preset_combo.addItem(label, val)
        # Default to 0.5x for slow motion.
        self.preset_combo.setCurrentIndex(1)
        form.addRow("Preset:", self.preset_combo)

        self.custom_spin = QDoubleSpinBox()
        self.custom_spin.setRange(0.05, 10.0)
        self.custom_spin.setSingleStep(0.05)
        self.custom_spin.setDecimals(3)
        self.custom_spin.setValue(0.5)
        self.custom_spin.setSuffix("×")
        form.addRow("Custom speed:", self.custom_spin)

        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        self._on_preset_changed()

        v.addWidget(speed_gb)

        # --- Motion interpolation ---
        motion_gb = QGroupBox("Smooth motion")
        mg = QVBoxLayout(motion_gb)
        self.interp_cb = QCheckBox("Use motion interpolation (minterpolate — smoother, much slower render)")
        self.interp_cb.setToolTip(
            "Synthesises in-between frames using motion estimation. Best for slo-mo "
            "when the source fps is low. Renders 5-10x slower."
        )
        mg.addWidget(self.interp_cb)

        row = QHBoxLayout()
        row.addWidget(QLabel("Target fps for interpolation:"))
        self.interp_fps_combo = QComboBox()
        for f in (30, 48, 60, 90, 120):
            self.interp_fps_combo.addItem(f"{f} fps", f)
        self.interp_fps_combo.setCurrentIndex(2)   # 60
        row.addWidget(self.interp_fps_combo)
        row.addStretch(1)
        mg.addLayout(row)
        self.interp_cb.toggled.connect(self.interp_fps_combo.setEnabled)
        self.interp_fps_combo.setEnabled(False)
        v.addWidget(motion_gb)

        # --- Range ---
        range_gb = QGroupBox("Range")
        rgv = QVBoxLayout(range_gb)
        self.range_range = QRadioButton(
            f"IN → OUT range   ({_fmt_time(in_seconds)} → {_fmt_time(out_seconds)})"
        )
        self.range_full = QRadioButton("Full media")
        if default_range == "full":
            self.range_full.setChecked(True)
        else:
            self.range_range.setChecked(True)
        rgv.addWidget(self.range_range)
        rgv.addWidget(self.range_full)
        v.addWidget(range_gb)

        # --- Output ---
        out_gb = QGroupBox("Output")
        of = QFormLayout(out_gb)

        self.format_combo = QComboBox()
        for label, key in (("MP4 (H.264 + AAC)", "mp4_h264"),
                           ("MP4 (H.265 + AAC)", "mp4_h265"),
                           ("MOV (H.264 + AAC)", "mov_h264"),
                           ("MKV (H.264 + AAC)", "mkv_h264"),
                           ("WebM (VP9 + Opus)", "webm_vp9")):
            self.format_combo.addItem(label, key)
        idx = self.format_combo.findData(settings.output_format_video)
        if idx >= 0:
            self.format_combo.setCurrentIndex(idx)
        of.addRow("Format:", self.format_combo)

        self.keep_audio_cb = QCheckBox("Keep audio (adjust tempo to match)")
        self.keep_audio_cb.setChecked(True)
        of.addRow("Audio:", self.keep_audio_cb)

        row2 = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setText(self._suggested_path())
        row2.addWidget(self.path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        row2.addWidget(browse)
        wrap = QWidget(); wrap.setLayout(row2)
        of.addRow("Save as:", wrap)

        self.format_combo.currentIndexChanged.connect(self._sync_output_ext)
        v.addWidget(out_gb)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _on_preset_changed(self) -> None:
        val = self.preset_combo.currentData()
        if isinstance(val, float) and val > 0:
            self.custom_spin.setValue(val)
            self.custom_spin.setEnabled(False)
        else:
            self.custom_spin.setEnabled(True)

    def _ext_for_format(self) -> str:
        key = self.format_combo.currentData() or "mp4_h264"
        return {
            "mp4_h264": ".mp4", "mp4_h265": ".mp4",
            "mov_h264": ".mov", "mkv_h264": ".mkv",
            "webm_vp9": ".webm",
        }.get(key, ".mp4")

    def _suggested_path(self) -> str:
        folder = os.path.dirname(self._source) if self._source else ""
        base = os.path.splitext(os.path.basename(self._source))[0] if self._source else "output"
        val = self.custom_spin.value() if self.custom_spin.isEnabled() else self.preset_combo.currentData()
        speed_tag = f"{val:g}x".replace(".", "p")
        return os.path.join(folder, f"{base}_{speed_tag}{self._ext_for_format()}")

    def _sync_output_ext(self) -> None:
        cur = self.path_edit.text().strip()
        if cur:
            root, _ = os.path.splitext(cur)
            self.path_edit.setText(root + self._ext_for_format())

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save output as", self.path_edit.text(),
            f"*{self._ext_for_format()}"
        )
        if path:
            self.path_edit.setText(path)

    def selection(self) -> dict:
        speed = float(self.custom_spin.value())
        return {
            "speed": speed,
            "interpolate": self.interp_cb.isChecked(),
            "interpolate_fps": int(self.interp_fps_combo.currentData() or 60),
            "keep_audio": self.keep_audio_cb.isChecked(),
            "range": "full" if self.range_full.isChecked() else "in_out",
            "output_format": self.format_combo.currentData() or "mp4_h264",
            "output_path": self.path_edit.text().strip(),
        }
