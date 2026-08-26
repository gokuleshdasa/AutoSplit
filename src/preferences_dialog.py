"""Preferences dialog: keyboard shortcuts + jump sizes + default quality."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QKeySequenceEdit, QLabel, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
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
        return base
