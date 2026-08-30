"""Preferences dialog: keyboard shortcuts + jump sizes + default quality."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QKeySequenceEdit,
    QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from ffmpeg_utils import AUDIO_BITRATES, VIDEO_QUALITY
from settings import AppSettings, DEFAULT_KEYBINDINGS


class PreferencesDialog(QDialog):
    def __init__(self, current: AppSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.resize(560, 620)

        self._settings = current
        self._key_editors: dict[str, QKeySequenceEdit] = {}

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_keys_tab(), "Keyboard")
        tabs.addTab(self._build_playback_tab(), "Playback")
        tabs.addTab(self._build_export_tab(), "Export defaults")
        tabs.addTab(self._build_frames_gif_tab(), "Frames && GIF")
        root.addWidget(tabs, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults
        )
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        btns.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._on_restore_defaults)
        root.addWidget(btns)

    # --- tabs ---------------------------------------------------------------
    def _build_keys_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "Click a field and press the key combination you want. "
            "Backspace / Delete clears a binding."))
        form = QFormLayout()
        form.setSpacing(6)
        for aid, (label, _default) in DEFAULT_KEYBINDINGS.items():
            edit = QKeySequenceEdit(QKeySequence(self._settings.keybindings.get(aid, _default)))
            self._key_editors[aid] = edit
            form.addRow(label + ":", edit)
        v.addLayout(form)
        v.addStretch(1)
        return w

    def _build_playback_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        info = QLabel(
            "The <b>fine</b> nudge (arrow keys with snap ON) is always 1 frame — "
            "auto-detected from the media's frame rate. Adjust larger step sizes here.")
        info.setWordWrap(True)
        v.addWidget(info)

        gb = QGroupBox("Jump sizes")
        form = QFormLayout(gb)

        self.jump_sec = QDoubleSpinBox()
        self.jump_sec.setDecimals(2)
        self.jump_sec.setRange(0.1, 3600.0)
        self.jump_sec.setSingleStep(1.0)
        self.jump_sec.setSuffix(" s")
        self.jump_sec.setValue(self._settings.jump_seconds)
        form.addRow("Jump  (default Ctrl+←/→):", self.jump_sec)

        self.big_jump_sec = QDoubleSpinBox()
        self.big_jump_sec.setDecimals(2)
        self.big_jump_sec.setRange(0.1, 3600.0)
        self.big_jump_sec.setSingleStep(1.0)
        self.big_jump_sec.setSuffix(" s")
        self.big_jump_sec.setValue(self._settings.big_jump_seconds)
        form.addRow("Big jump  (default Shift+←/→):", self.big_jump_sec)

        v.addWidget(gb)
        v.addStretch(1)
        return w

    def _build_export_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "Defaults used when a new file is loaded. You can still change them per-export in the main window."))

        gb = QGroupBox("Audio")
        form = QFormLayout(gb)
        self.audio_bitrate_combo = QComboBox()
        self.audio_bitrate_combo.addItems(AUDIO_BITRATES)
        idx = self.audio_bitrate_combo.findText(self._settings.audio_bitrate)
        self.audio_bitrate_combo.setCurrentIndex(max(0, idx))
        form.addRow("Bitrate (lossy formats):", self.audio_bitrate_combo)
        v.addWidget(gb)

        gb2 = QGroupBox("Video")
        form2 = QFormLayout(gb2)
        self.video_quality_combo = QComboBox()
        for label, crf in VIDEO_QUALITY:
            self.video_quality_combo.addItem(label, crf)
        # Restore selection by CRF.
        for i in range(self.video_quality_combo.count()):
            if self.video_quality_combo.itemData(i) == self._settings.video_crf:
                self.video_quality_combo.setCurrentIndex(i)
                break
        form2.addRow("Quality preset:", self.video_quality_combo)
        v.addWidget(gb2)

        v.addStretch(1)
        return w

    def _build_frames_gif_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel(
            "Defaults for the Export button's dropdown menu: Extract frame, "
            "Extract all frames, and Export as GIF."))

        # --- Prompt behavior ---
        gb0 = QGroupBox("When you use the Export menu")
        g0 = QVBoxLayout(gb0)
        self.prompt_frame_cb = QCheckBox("Ask for options before Extract Frame / Extract All Frames")
        self.prompt_frame_cb.setChecked(self._settings.prompt_before_frame_export)
        g0.addWidget(self.prompt_frame_cb)
        self.prompt_gif_cb = QCheckBox("Ask for options before Export as GIF")
        self.prompt_gif_cb.setChecked(self._settings.prompt_before_gif_export)
        g0.addWidget(self.prompt_gif_cb)
        g0.addWidget(QLabel(
            "Unchecked = one-click export using the defaults below.",
            objectName="Muted"))
        v.addWidget(gb0)

        # --- Frame defaults ---
        gb = QGroupBox("Frame extraction defaults")
        form = QFormLayout(gb)
        self.frame_fmt_combo = QComboBox()
        for label, key in (("PNG", "png"), ("JPEG", "jpg"), ("WebP", "webp")):
            self.frame_fmt_combo.addItem(label, key)
        idx = self.frame_fmt_combo.findData(self._settings.frame_format)
        if idx >= 0:
            self.frame_fmt_combo.setCurrentIndex(idx)
        form.addRow("Image format:", self.frame_fmt_combo)

        self.frame_q_spin = QSpinBox()
        self.frame_q_spin.setRange(2, 31)
        self.frame_q_spin.setValue(self._settings.frame_jpg_quality)
        form.addRow("JPEG/WebP quality:", self.frame_q_spin)

        self.frame_fps_spin = QDoubleSpinBox()
        self.frame_fps_spin.setRange(0.0, 240.0)
        self.frame_fps_spin.setSingleStep(0.5)
        self.frame_fps_spin.setValue(self._settings.frame_all_fps)
        self.frame_fps_spin.setSpecialValueText("Source native (0)")
        form.addRow("'All frames' rate (fps):", self.frame_fps_spin)

        row1 = QHBoxLayout()
        self.frame_dir_edit = QLineEdit(self._settings.frame_output_dir)
        self.frame_dir_edit.setPlaceholderText("Empty = save next to the source file")
        row1.addWidget(self.frame_dir_edit, 1)
        b1 = QPushButton("Browse…")
        b1.clicked.connect(lambda: self._browse_into(self.frame_dir_edit))
        row1.addWidget(b1)
        wrap1 = QWidget(); wrap1.setLayout(row1)
        form.addRow("Output folder:", wrap1)
        v.addWidget(gb)

        # --- GIF defaults ---
        gb2 = QGroupBox("GIF export defaults")
        form2 = QFormLayout(gb2)
        self.gif_fps_spin = QSpinBox()
        self.gif_fps_spin.setRange(2, 60)
        self.gif_fps_spin.setValue(self._settings.gif_fps)
        form2.addRow("Frame rate (fps):", self.gif_fps_spin)

        self.gif_width_combo = QComboBox()
        for w2 in (240, 320, 480, 640, 720, 960, 1080, 1280):
            self.gif_width_combo.addItem(f"{w2} px", w2)
        self.gif_width_combo.addItem("Original", 0)
        for i in range(self.gif_width_combo.count()):
            if self.gif_width_combo.itemData(i) == self._settings.gif_width:
                self.gif_width_combo.setCurrentIndex(i)
                break
        form2.addRow("Width:", self.gif_width_combo)

        self.gif_dither_combo = QComboBox()
        for label, key in (
            ("Bayer",           "bayer"),
            ("Floyd-Steinberg", "floyd_steinberg"),
            ("Sierra 2-4a",     "sierra2_4a"),
            ("None",            "none"),
        ):
            self.gif_dither_combo.addItem(label, key)
        idx = self.gif_dither_combo.findData(self._settings.gif_dither)
        if idx >= 0:
            self.gif_dither_combo.setCurrentIndex(idx)
        form2.addRow("Dither:", self.gif_dither_combo)

        row2 = QHBoxLayout()
        self.gif_dir_edit = QLineEdit(self._settings.gif_output_dir)
        self.gif_dir_edit.setPlaceholderText("Empty = save next to the source file")
        row2.addWidget(self.gif_dir_edit, 1)
        b2 = QPushButton("Browse…")
        b2.clicked.connect(lambda: self._browse_into(self.gif_dir_edit))
        row2.addWidget(b2)
        wrap2 = QWidget(); wrap2.setLayout(row2)
        form2.addRow("Output folder:", wrap2)
        v.addWidget(gb2)

        v.addStretch(1)
        return w

    def _browse_into(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose output folder", edit.text() or "")
        if d:
            edit.setText(d)

    # --- actions ------------------------------------------------------------
    def _on_restore_defaults(self) -> None:
        for aid, (_lbl, default) in DEFAULT_KEYBINDINGS.items():
            self._key_editors[aid].setKeySequence(QKeySequence(default))
        self.jump_sec.setValue(5.0)
        self.big_jump_sec.setValue(10.0)
        self.audio_bitrate_combo.setCurrentText("192k")
        for i in range(self.video_quality_combo.count()):
            if self.video_quality_combo.itemData(i) == 18:
                self.video_quality_combo.setCurrentIndex(i)
                break
        # Frames + GIF defaults
        self.prompt_frame_cb.setChecked(True)
        self.prompt_gif_cb.setChecked(True)
        self.frame_fmt_combo.setCurrentIndex(self.frame_fmt_combo.findData("png"))
        self.frame_q_spin.setValue(2)
        self.frame_fps_spin.setValue(1.0)
        self.frame_dir_edit.setText("")
        self.gif_fps_spin.setValue(15)
        for i in range(self.gif_width_combo.count()):
            if self.gif_width_combo.itemData(i) == 480:
                self.gif_width_combo.setCurrentIndex(i)
                break
        self.gif_dither_combo.setCurrentIndex(self.gif_dither_combo.findData("bayer"))
        self.gif_dir_edit.setText("")

    def _on_accept(self) -> None:
        # Check for duplicate bindings.
        used: dict[str, str] = {}
        for aid, edit in self._key_editors.items():
            seq = edit.keySequence().toString()
            if not seq:
                continue
            if seq in used:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "Duplicate shortcut",
                    f"‘{seq}’ is used for both:\n • {DEFAULT_KEYBINDINGS[used[seq]][0]}\n • {DEFAULT_KEYBINDINGS[aid][0]}\n\n"
                    "Please pick a different key for one of them."
                )
                return
            used[seq] = aid
        self.accept()

    def result_settings(self, base: AppSettings) -> AppSettings:
        """Return an AppSettings updated with the dialog's values."""
        base.keybindings = {
            aid: edit.keySequence().toString()
            for aid, edit in self._key_editors.items()
        }
        base.jump_seconds = float(self.jump_sec.value())
        base.big_jump_seconds = float(self.big_jump_sec.value())
        base.audio_bitrate = self.audio_bitrate_combo.currentText()
        crf = self.video_quality_combo.currentData()
        if isinstance(crf, int):
            base.video_crf = crf
        # Frames + GIF tab
        base.prompt_before_frame_export = self.prompt_frame_cb.isChecked()
        base.prompt_before_gif_export = self.prompt_gif_cb.isChecked()
        base.frame_format = self.frame_fmt_combo.currentData() or "png"
        base.frame_jpg_quality = int(self.frame_q_spin.value())
        base.frame_all_fps = float(self.frame_fps_spin.value())
        base.frame_output_dir = self.frame_dir_edit.text().strip()
        base.gif_fps = int(self.gif_fps_spin.value())
        base.gif_width = int(self.gif_width_combo.currentData() or 0)
        base.gif_dither = self.gif_dither_combo.currentData() or "bayer"
        base.gif_output_dir = self.gif_dir_edit.text().strip()
        return base
