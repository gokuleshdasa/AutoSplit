"""Main window for AutoSplit."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QTimer, Signal, QPoint
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QIcon, QCloseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QStatusBar, QVBoxLayout, QWidget, QStyle,
)

import ffmpeg_utils as fu
import settings as app_settings
from export_worker import ExportJob, start_export
from preferences_dialog import PreferencesDialog
from regions import Region, sanitize_name
from timeline import Timeline
from waveform import start_waveform


STYLE = """
QWidget { background: #202020; color: #e8e8e8; font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif; font-size: 10pt; }
QMainWindow, QWidget#Central { background: #202020; }
QGroupBox { border: 1px solid #333; border-radius: 8px; margin-top: 14px; padding: 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; color: #b0d0f0; }
QLineEdit, QSpinBox, QComboBox {
    background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 4px; padding: 6px 8px; selection-background-color: #0078d4;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #0078d4; }
QPushButton { background: #2f2f2f; border: 1px solid #3a3a3a; border-radius: 4px; padding: 6px 14px; }
QPushButton:hover { background: #3a3a3a; }
QPushButton:pressed { background: #262626; }
QPushButton#Primary { background: #0078d4; border: 1px solid #0078d4; color: white; font-weight: 600; padding: 8px 20px; }
QPushButton#Primary:hover { background: #106ebe; }
QPushButton#Primary:disabled { background: #444; color: #888; border-color: #444; }
QPushButton#Danger { background: #a4262c; border-color: #a4262c; color: white; }
QPushButton#Danger:hover { background: #b53037; }
QCheckBox { spacing: 8px; }
QProgressBar { background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 4px; text-align: center; padding: 1px; }
QProgressBar::chunk { background: #0078d4; border-radius: 3px; }
QLabel#Muted { color: #a0a0a0; }
QLabel#Title { font-size: 16pt; font-weight: 600; color: #ffffff; }
QSplitter::handle { background: #2a2a2a; }
QStatusBar { background: #1a1a1a; border-top: 1px solid #2a2a2a; }
QListWidget { background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 4px; padding: 2px; }
QListWidget::item { padding: 6px 6px; border-radius: 3px; }
QListWidget::item:selected { background: #0a3a63; }
QRadioButton { spacing: 10px; padding: 3px 2px; }
QRadioButton::indicator {
    width: 16px; height: 16px; border-radius: 9px;
    border: 2px solid #6a6a6a; background: #1a1a1a;
}
QRadioButton::indicator:hover { border-color: #a0a0a0; }
QRadioButton::indicator:checked {
    background: #0078d4; border: 2px solid #4ea3ff;
}
QRadioButton:checked { color: #ffffff; font-weight: 600; }
QRadioButton:!checked { color: #b8b8b8; }
QScrollArea { background: transparent; border: none; }
QMenu { background: #2a2a2a; border: 1px solid #3a3a3a; padding: 4px; }
QMenu::item { padding: 6px 22px 6px 14px; border-radius: 3px; }
QMenu::item:selected { background: #0a3a63; }
QMenu::separator { height: 1px; background: #3a3a3a; margin: 4px 6px; }
"""


def parse_time_input(text: str) -> Optional[float]:
    """Accept seconds ('90'), mm:ss ('01:30'), or hh:mm:ss ('01:00:30.5')."""
    text = text.strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    if len(vals) == 1:
        return vals[0]
    if len(vals) == 2:
        return vals[0] * 60 + vals[1]
    if len(vals) == 3:
        return vals[0] * 3600 + vals[1] * 60 + vals[2]
    return None


def fmt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds - (h * 3600 + m * 60))
    return f"{h:02d}:{m:02d}:{s:02d}"


class MainWindow(QMainWindow):
    def __init__(self, initial_file: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("AutoSplit — Media Splitter")
        self.resize(1200, 780)
        self.setStyleSheet(STYLE)
        self.setAcceptDrops(True)

        self._settings = app_settings.load()
        self._media_info: Optional[fu.MediaInfo] = None
        self._current_file: Optional[str] = None
        self._export_thread = None
        self._export_worker = None
        self._wave_thread = None
        self._wave_worker = None
        self._regions: list[Region] = []
        self._snap_fine = False   # False = arrow step 1s, True = arrow step 1 frame
        self._shortcuts: list[QShortcut] = []

        self._build_ui()
        self._wire_shortcuts()

        # Poll player position for timeline updates.
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(80)
        self._pos_timer.timeout.connect(self._sync_position)
        self._pos_timer.start()

        if initial_file:
            QTimer.singleShot(0, lambda: self._load_file(initial_file))

    # --- UI construction ---------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(objectName="Central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Header row: title + open file button.
        header = QHBoxLayout()
        title = QLabel("AutoSplit", objectName="Title")
        header.addWidget(title)
        header.addStretch(1)
        self.file_label = QLabel("No file loaded", objectName="Muted")
        self.file_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(self.file_label, 2)
        open_btn = QPushButton("Open File…")
        open_btn.clicked.connect(self._on_open_file)
        header.addWidget(open_btn)
        prefs_btn = QPushButton("Preferences…")
        prefs_btn.clicked.connect(self._on_open_preferences)
        header.addWidget(prefs_btn)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        # Left: player + timeline.
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)

        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background: #000;")
        self.video_widget.setMinimumHeight(320)
        left_lay.addWidget(self.video_widget, 1)

        # Player controls.
        ctrl = QHBoxLayout()
        self.play_btn = QPushButton()
        self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_btn.setFixedWidth(46)
        self.play_btn.clicked.connect(self._toggle_play)
        ctrl.addWidget(self.play_btn)

        stop_btn = QPushButton()
        stop_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        stop_btn.setFixedWidth(46)
        stop_btn.clicked.connect(self._on_stop)
        ctrl.addWidget(stop_btn)

        set_start_btn = QPushButton("Mark IN")
        set_start_btn.setToolTip("Set the start of the export range at the current playhead (I)")
        set_start_btn.clicked.connect(self._on_set_start_at_playhead)
        ctrl.addWidget(set_start_btn)

        set_end_btn = QPushButton("Mark OUT")
        set_end_btn.setToolTip("Set the end of the export range at the current playhead (O)")
        set_end_btn.clicked.connect(self._on_set_end_at_playhead)
        ctrl.addWidget(set_end_btn)

        clear_end_btn = QPushButton("Clear OUT")
        clear_end_btn.setToolTip("Clear the end marker — export to the end of the media")
        clear_end_btn.clicked.connect(self._on_clear_end)
        ctrl.addWidget(clear_end_btn)

        ctrl.addStretch(1)
        self.time_label = QLabel("00:00:00 / 00:00:00", objectName="Muted")
        ctrl.addWidget(self.time_label)
        left_lay.addLayout(ctrl)

        self.timeline = Timeline()
        self.timeline.seek_requested.connect(self._on_seek)
        self.timeline.scrubbing.connect(self._on_scrubbing)
        self.timeline.start_changed.connect(self._on_start_changed_from_timeline)
        self.timeline.end_changed.connect(self._on_end_changed_from_timeline)
        self.timeline.context_requested.connect(self._on_timeline_context)
        self.timeline.range_selected.connect(self._on_range_selected)
        left_lay.addWidget(self.timeline)
        hint = QLabel(
            "Left-drag = seek  ·  <b>Alt-drag = highlight to add region</b>  ·  Shift-drag = IN  ·  Ctrl-drag = OUT  ·  "
            "Right-click = menu  ·  I/O = mark  ·  M = add  ·  S = toggle 1 s ↔ 1 frame  ·  "
            "← → nudge  ·  Ctrl+arrow / Shift+arrow = jump  ·  Space = play/pause  ·  Ctrl+, = Preferences",
            objectName="Muted")
        hint.setWordWrap(True)
        left_lay.addWidget(hint)

        splitter.addWidget(left)

        # Right: settings panel.
        right = QWidget()
        right.setMinimumWidth(360)
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(10)

        right_lay.addWidget(self._build_mode_group())
        right_lay.addWidget(self._build_split_group())
        right_lay.addWidget(self._build_markers_group())
        right_lay.addWidget(self._build_output_group())
        right_lay.addWidget(self._build_naming_group())
        right_lay.addWidget(self._build_behavior_group())
        right_lay.addStretch(1)

        # Wrap right panel in a scroll area so tall settings don't blow up the layout on maximize.
        from PySide6.QtWidgets import QScrollArea  # local import keeps the top block tidy
        scroll = QScrollArea()
        scroll.setWidget(right)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(380)
        scroll.setMaximumWidth(560)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)   # left grows
        splitter.setStretchFactor(1, 0)   # right stays around its size
        splitter.setSizes([800, 440])

        # Bottom: export controls + progress.
        bottom = QFrame()
        bottom.setFrameShape(QFrame.Shape.StyledPanel)
        bottom_lay = QVBoxLayout(bottom)
        bottom_lay.setContentsMargins(12, 8, 12, 8)
        bottom_lay.setSpacing(6)

        exp_row = QHBoxLayout()
        self.status_label = QLabel("Ready", objectName="Muted")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        exp_row.addWidget(self.status_label, 1)
        self.cancel_btn = QPushButton("Cancel", objectName="Danger")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        exp_row.addWidget(self.cancel_btn)
        self.export_btn = QPushButton("Export", objectName="Primary")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export)
        exp_row.addWidget(self.export_btn)
        bottom_lay.addLayout(exp_row)

        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, 1000)
        self.overall_bar.setValue(0)
        bottom_lay.addWidget(self.overall_bar)
        root.addWidget(bottom)

        self.setStatusBar(QStatusBar())
        if not fu.ffmpeg_available():
            self.statusBar().showMessage(
                "⚠ FFmpeg not found — place ffmpeg.exe in vendor/ffmpeg/ or add to PATH."
            )

        # Media player.
        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video_widget)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.errorOccurred.connect(self._on_player_error)

    def _build_split_group(self) -> QGroupBox:
        gb = QGroupBox("Split")
        g = QGridLayout(gb)
        g.setVerticalSpacing(8)

        g.addWidget(QLabel("Split each part into:"), 0, 0)
        self.split_input = QLineEdit()
        self.split_input.setPlaceholderText("mm:ss or seconds, e.g. 10:00 or 600")
        self.split_input.setText(fmt_time(self._settings.split_duration_seconds))
        self.split_input.editingFinished.connect(self._on_split_edited)
        g.addWidget(self.split_input, 0, 1)

        g.addWidget(QLabel("Start at:"), 1, 0)
        self.start_input = QLineEdit()
        self.start_input.setPlaceholderText("hh:mm:ss or seconds")
        self.start_input.setText("00:00:00")
        self.start_input.editingFinished.connect(self._on_start_edited)
        g.addWidget(self.start_input, 1, 1)

        g.addWidget(QLabel("End at:"), 2, 0)
        self.end_input = QLineEdit()
        self.end_input.setPlaceholderText("blank = end of media")
        self.end_input.setText("")
        self.end_input.editingFinished.connect(self._on_end_edited)
        g.addWidget(self.end_input, 2, 1)

        self.segments_label = QLabel("—", objectName="Muted")
        self.segments_label.setWordWrap(True)
        g.addWidget(self.segments_label, 3, 0, 1, 2)
        return gb

    def _build_mode_group(self) -> QGroupBox:
        gb = QGroupBox("Export mode")
        v = QVBoxLayout(gb)
        self.mode_auto_rb = QRadioButton("Auto-split by duration (uses Start / End / Split length)")
        self.mode_markers_rb = QRadioButton("Marked regions (each named marker becomes one file)")
        self.mode_auto_rb.setChecked(True)
        self.mode_auto_rb.toggled.connect(self._on_mode_changed)
        v.addWidget(self.mode_auto_rb)
        v.addWidget(self.mode_markers_rb)
        return gb

    def _build_markers_group(self) -> QGroupBox:
        gb = QGroupBox("Markers")
        v = QVBoxLayout(gb)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.add_marker_btn = QPushButton("Add")
        self.add_marker_btn.setToolTip("Add a region using the current IN/OUT markers, then name it (M)")
        self.add_marker_btn.clicked.connect(self._on_add_marker)
        row.addWidget(self.add_marker_btn)

        self.rename_marker_btn = QPushButton("Rename")
        self.rename_marker_btn.clicked.connect(self._on_rename_marker)
        row.addWidget(self.rename_marker_btn)

        self.delete_marker_btn = QPushButton("Delete")
        self.delete_marker_btn.clicked.connect(self._on_delete_marker)
        row.addWidget(self.delete_marker_btn)

        self.clear_markers_btn = QPushButton("Clear all")
        self.clear_markers_btn.clicked.connect(self._on_clear_markers)
        row.addWidget(self.clear_markers_btn)

        v.addLayout(row)

        self.markers_list = QListWidget()
        self.markers_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.markers_list.itemDoubleClicked.connect(self._on_rename_marker)
        self.markers_list.currentRowChanged.connect(self._on_marker_selected)
        v.addWidget(self.markers_list)

        tip = QLabel(
            "Tip: press I / O at the playhead to set IN and OUT, then M to add the region. "
            "Or right-click the timeline for a menu.",
            objectName="Muted")
        tip.setWordWrap(True)
        v.addWidget(tip)
        return gb

    def _build_output_group(self) -> QGroupBox:
        gb = QGroupBox("Output")
        g = QGridLayout(gb)
        g.setVerticalSpacing(8)

        g.addWidget(QLabel("Format:"), 0, 0)
        self.format_combo = QComboBox()
        # Populated once a media file is loaded (options depend on video/audio).
        g.addWidget(self.format_combo, 0, 1)

        g.addWidget(QLabel("Audio bitrate:"), 1, 0)
        self.audio_bitrate_combo = QComboBox()
        self.audio_bitrate_combo.addItems(fu.AUDIO_BITRATES)
        idx = self.audio_bitrate_combo.findText(self._settings.audio_bitrate)
        self.audio_bitrate_combo.setCurrentIndex(max(0, idx))
        g.addWidget(self.audio_bitrate_combo, 1, 1)

        g.addWidget(QLabel("Video quality:"), 2, 0)
        self.video_quality_combo = QComboBox()
        for label, crf in fu.VIDEO_QUALITY:
            self.video_quality_combo.addItem(label, crf)
        for i in range(self.video_quality_combo.count()):
            if self.video_quality_combo.itemData(i) == self._settings.video_crf:
                self.video_quality_combo.setCurrentIndex(i)
                break
        g.addWidget(self.video_quality_combo, 2, 1)

        g.addWidget(QLabel("Folder:"), 3, 0)
        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setText(self._settings.last_export_dir)
        row.addWidget(self.folder_edit, 1)
        pick = QPushButton("Browse…")
        pick.clicked.connect(self._on_pick_folder)
        row.addWidget(pick)
        w = QWidget()
        w.setLayout(row)
        g.addWidget(w, 3, 1)
        return gb

    def _build_naming_group(self) -> QGroupBox:
        gb = QGroupBox("Naming")
        g = QGridLayout(gb)
        g.setVerticalSpacing(8)

        g.addWidget(QLabel("Prefix:"), 0, 0)
        self.prefix_edit = QLineEdit(self._settings.prefix)
        self.prefix_edit.setPlaceholderText("e.g. Lecture_")
        self.prefix_edit.textChanged.connect(self._refresh_preview)
        g.addWidget(self.prefix_edit, 0, 1)

        g.addWidget(QLabel("Suffix:"), 1, 0)
        self.suffix_edit = QLineEdit(self._settings.suffix)
        self.suffix_edit.setPlaceholderText("e.g. _Jan")
        self.suffix_edit.textChanged.connect(self._refresh_preview)
        g.addWidget(self.suffix_edit, 1, 1)

        g.addWidget(QLabel("Number width:"), 2, 0)
        self.pad_spin = QSpinBox()
        self.pad_spin.setRange(1, 6)
        self.pad_spin.setValue(self._settings.pad_width)
        self.pad_spin.valueChanged.connect(self._refresh_preview)
        g.addWidget(self.pad_spin, 2, 1)

        self.preview_label = QLabel("Preview: —", objectName="Muted")
        self.preview_label.setWordWrap(True)
        g.addWidget(self.preview_label, 3, 0, 1, 2)
        return gb

    def _build_behavior_group(self) -> QGroupBox:
        gb = QGroupBox("After export")
        g = QVBoxLayout(gb)
        self.close_after_cb = QCheckBox("Close app after successful export")
        self.close_after_cb.setChecked(self._settings.close_after_export)
        g.addWidget(self.close_after_cb)
        self.open_folder_cb = QCheckBox("Open output folder when done")
        self.open_folder_cb.setChecked(self._settings.open_folder_after_export)
        g.addWidget(self.open_folder_cb)
        self.overwrite_cb = QCheckBox("Overwrite existing files (otherwise auto-rename)")
        self.overwrite_cb.setChecked(self._settings.overwrite_existing)
        g.addWidget(self.overwrite_cb)
        return gb

    def _wire_shortcuts(self) -> None:
        # Clear any previous bindings (used when re-applying after Preferences).
        for sc in self._shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._shortcuts.clear()

        actions = {
            "play_pause":     self._toggle_play,
            "nudge_left":     lambda: self._nudge(-1),
            "nudge_right":    lambda: self._nudge(+1),
            "jump_left":      lambda: self._jump(-self._settings.jump_seconds),
            "jump_right":     lambda: self._jump(+self._settings.jump_seconds),
            "big_jump_left":  lambda: self._jump(-self._settings.big_jump_seconds),
            "big_jump_right": lambda: self._jump(+self._settings.big_jump_seconds),
            "mark_in":        self._on_set_start_at_playhead,
            "mark_out":       self._on_set_end_at_playhead,
            "add_marker":     self._on_add_marker,
            "toggle_snap":    self._toggle_snap,
            "open_file":      self._on_open_file,
            "preferences":    self._on_open_preferences,
        }
        for aid, handler in actions.items():
            seq_text = self._settings.keybindings.get(
                aid, app_settings.DEFAULT_KEYBINDINGS[aid][1])
            if not seq_text:
                continue
            sc = QShortcut(QKeySequence(seq_text), self)
            sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
            sc.activated.connect(handler)
            self._shortcuts.append(sc)

        # Standard Ctrl+V paste — always on (not user-remappable).
        paste_sc = QShortcut(QKeySequence.StandardKey.Paste, self)
        paste_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        paste_sc.activated.connect(self._on_paste)
        self._shortcuts.append(paste_sc)

    # --- drag & drop -------------------------------------------------------
    def dragEnterEvent(self, ev) -> None:
        if self._first_media_path_in_mime(ev.mimeData()):
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        p = self._first_media_path_in_mime(ev.mimeData())
        if p:
            self._load_file(p)

    def _on_paste(self) -> None:
        p = self._first_media_path_in_mime(QApplication.clipboard().mimeData())
        if p:
            self._load_file(p)
        else:
            self.statusBar().showMessage("Clipboard has no media file.", 2500)

    def _first_media_path_in_mime(self, md) -> Optional[str]:
        if md is None:
            return None
        if md.hasUrls():
            for u in md.urls():
                if not u.isLocalFile():
                    continue
                p = u.toLocalFile()
                if fu.is_supported_media(p):
                    return p
        if md.hasText():
            for line in md.text().splitlines():
                p = line.strip().strip('"').strip("'")
                if p and os.path.exists(p) and fu.is_supported_media(p):
                    return p
        return None

    # --- file loading ------------------------------------------------------
    def _on_open_file(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(fu.VIDEO_EXTS | fu.AUDIO_EXTS))
        path, _ = QFileDialog.getOpenFileName(
            self, "Open media", self._settings.last_export_dir or "",
            f"Media ({exts});;All files (*.*)"
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str) -> None:
        if not os.path.exists(path):
            QMessageBox.critical(self, "File not found", f"Cannot open:\n{path}")
            return
        if not fu.is_supported_media(path):
            QMessageBox.warning(self, "Unsupported file",
                                f"Extension not supported:\n{path}")
            return
        if not fu.ffmpeg_available():
            QMessageBox.critical(
                self, "FFmpeg missing",
                "FFmpeg was not found.\n\nPlace ffmpeg.exe and ffprobe.exe in vendor/ffmpeg/ "
                "next to the app, or add them to your system PATH."
            )
            return

        try:
            info = fu.probe(path)
        except Exception as e:
            QMessageBox.critical(self, "Cannot read media", str(e))
            return

        if info.duration_seconds <= 0:
            QMessageBox.critical(self, "Cannot read media", "Media duration is zero or unknown.")
            return

        self._current_file = path
        self._media_info = info
        self.file_label.setText(
            f"{os.path.basename(path)}   ·   {fmt_time(info.duration_seconds)}   ·   "
            f"{'video ' + (info.video_codec or '') if info.has_video else ''}"
            f"{' + audio ' + (info.audio_codec or '') if info.has_audio else ''}"
        )
        self.player.setSource(QUrl.fromLocalFile(path))

        self.timeline.set_duration(info.duration_seconds)
        self.timeline.set_position(0.0)
        self.timeline.set_start(0.0)
        self.timeline.set_end(-1.0)
        self.timeline.set_waveform(None)
        self._regions.clear()
        self._refresh_markers_ui()
        self._sync_split_from_input()
        self.start_input.setText("00:00:00")
        self.end_input.setText("")
        # Kick off waveform generation on a worker thread.
        self._start_waveform(path)

        # Populate format options based on media capabilities.
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        for key, meta in fu.FORMAT_CATALOG.items():
            if meta["kind"] == "video" and not info.has_video:
                continue
            if meta["kind"] == "audio" and not info.has_audio:
                continue
            self.format_combo.addItem(meta["label"], key)
        default = self._settings.output_format_video if info.has_video else self._settings.output_format_audio
        idx = self.format_combo.findData(default)
        if idx >= 0:
            self.format_combo.setCurrentIndex(idx)
        self.format_combo.blockSignals(False)
        try:
            self.format_combo.currentIndexChanged.disconnect(self._refresh_preview)
        except (TypeError, RuntimeError):
            pass
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self._on_format_changed()

        # Default export folder to source folder if none saved.
        if not self.folder_edit.text().strip():
            self.folder_edit.setText(str(Path(path).parent))

        self.export_btn.setEnabled(True)
        self.status_label.setText("Ready to export")
        self._refresh_preview()

    # --- player wiring -----------------------------------------------------
    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if self.player.source().isEmpty():
                return
            self.player.play()

    def _on_stop(self) -> None:
        self.player.pause()
        self.player.setPosition(0)

    def _on_playback_state(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def _on_player_error(self, _err, msg) -> None:
        if msg:
            self.statusBar().showMessage(f"Playback: {msg}", 5000)

    def _sync_position(self) -> None:
        if not self._media_info:
            return
        pos_s = self.player.position() / 1000.0
        dur_s = self._media_info.duration_seconds
        self.timeline.set_position(pos_s)
        self.time_label.setText(f"{fmt_time(pos_s)} / {fmt_time(dur_s)}")

    def _on_seek(self, seconds: float) -> None:
        self.player.setPosition(int(seconds * 1000))

    def _on_scrubbing(self, seconds: float) -> None:
        # Timeline is already showing the new playhead; also update the text label
        # so the numbers track the drag smoothly without waiting for the poll timer.
        if self._media_info:
            self.time_label.setText(f"{fmt_time(seconds)} / {fmt_time(self._media_info.duration_seconds)}")

    def _on_set_start_at_playhead(self) -> None:
        if not self._media_info:
            return
        secs = self.player.position() / 1000.0
        self.timeline.set_start(secs)
        self.start_input.setText(fmt_time(secs))
        self._refresh_preview()

    def _on_set_end_at_playhead(self) -> None:
        if not self._media_info:
            return
        secs = self.player.position() / 1000.0
        self.timeline.set_end(secs)
        self.end_input.setText(fmt_time(secs))
        self._refresh_preview()

    def _on_clear_end(self) -> None:
        self.timeline.set_end(-1.0)
        self.end_input.setText("")
        self._refresh_preview()

    def _on_start_changed_from_timeline(self, seconds: float) -> None:
        self.start_input.setText(fmt_time(seconds))
        self._refresh_preview()

    def _on_end_changed_from_timeline(self, seconds: float) -> None:
        if seconds <= 0:
            self.end_input.setText("")
        else:
            self.end_input.setText(fmt_time(seconds))
        self._refresh_preview()

    def _nudge(self, sign: int) -> None:
        if not self._media_info:
            return
        step = self._media_info.frame_period() if self._snap_fine else 1.0
        self._move_playhead(sign * step)

    def _jump(self, delta_seconds: float) -> None:
        if not self._media_info:
            return
        self._move_playhead(delta_seconds)

    def _move_playhead(self, delta: float) -> None:
        pos = self.player.position() / 1000.0
        new_pos = max(0.0, min(self._media_info.duration_seconds, pos + delta))
        self.player.setPosition(int(new_pos * 1000))
        self.timeline.set_position(new_pos)
        self.time_label.setText(f"{fmt_time(new_pos)} / {fmt_time(self._media_info.duration_seconds)}")

    def _toggle_snap(self) -> None:
        self._snap_fine = not self._snap_fine
        if self._snap_fine and self._media_info:
            fps = self._media_info.fps or 25.0
            step = f"1 frame  ({1000.0 / fps:.2f} ms @ {fps:g} fps)"
        else:
            step = "1 s"
        self.statusBar().showMessage(
            f"Snap: arrow-key nudge = {step}   (press the snap key to toggle)", 4000)

    def _on_open_preferences(self) -> None:
        dlg = PreferencesDialog(self._settings, self)
        if dlg.exec() == PreferencesDialog.DialogCode.Accepted:
            self._settings = dlg.result_settings(self._settings)
            app_settings.save(self._settings)
            self._wire_shortcuts()
            self.statusBar().showMessage("Preferences updated.", 2500)

    # --- mode & markers ----------------------------------------------------
    def _is_markers_mode(self) -> bool:
        return self.mode_markers_rb.isChecked()

    def _on_mode_changed(self, _checked: bool) -> None:
        markers = self._is_markers_mode()
        self.split_input.setDisabled(markers)
        self.start_input.setDisabled(markers)
        self.end_input.setDisabled(markers)
        self.timeline.set_show_split_ticks(not markers)
        self._refresh_preview()

    def _on_range_selected(self, start: float, end: float) -> None:
        """Alt-drag on the timeline: set IN/OUT to the highlighted span,
        then prompt for a region name and add it directly."""
        if not self._media_info:
            return
        self.timeline.set_start(start)
        self.timeline.set_end(end)
        self.start_input.setText(fmt_time(start))
        self.end_input.setText(fmt_time(end))

        default = f"clip_{len(self._regions) + 1:03d}"
        name, ok = QInputDialog.getText(self, "Name this region",
                                        f"Range: {fmt_time(start)} → {fmt_time(end)}   "
                                        f"({fmt_time(end - start)})\n\nName (used as filename):",
                                        text=default)
        if not ok:
            return
        name = sanitize_name(name) or default
        self._regions.append(Region(start=start, end=end, name=name))
        self._refresh_markers_ui()
        if len(self._regions) == 1 and not self._is_markers_mode():
            self.mode_markers_rb.setChecked(True)

    def _on_add_marker(self) -> None:
        if not self._media_info:
            return
        start = self.timeline.start()
        end = self.timeline.end()
        if end <= start:
            QMessageBox.information(self, "Set IN/OUT first",
                                    "Set IN and OUT markers first (I / O), then add.")
            return

        default = f"clip_{len(self._regions) + 1:03d}"
        name, ok = QInputDialog.getText(self, "Name this region",
                                        "Name (used as the filename):",
                                        text=default)
        if not ok:
            return
        name = sanitize_name(name) or default

        self._regions.append(Region(start=start, end=end, name=name))
        self._refresh_markers_ui()
        # Auto-switch to markers mode on first add — clearer for the user.
        if len(self._regions) == 1 and not self._is_markers_mode():
            self.mode_markers_rb.setChecked(True)

    def _on_rename_marker(self, *_) -> None:
        row = self.markers_list.currentRow()
        if row < 0 or row >= len(self._regions):
            return
        current = self._regions[row].name
        name, ok = QInputDialog.getText(self, "Rename region",
                                        "Name:", text=current)
        if not ok:
            return
        name = sanitize_name(name) or current
        self._regions[row].name = name
        self._refresh_markers_ui()

    def _on_delete_marker(self) -> None:
        row = self.markers_list.currentRow()
        if row < 0 or row >= len(self._regions):
            return
        del self._regions[row]
        self._refresh_markers_ui()

    def _on_clear_markers(self) -> None:
        if not self._regions:
            return
        if QMessageBox.question(
                self, "Clear markers",
                f"Remove all {len(self._regions)} markers?") != QMessageBox.StandardButton.Yes:
            return
        self._regions.clear()
        self._refresh_markers_ui()

    def _on_marker_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._regions):
            return
        reg = self._regions[row]
        # Seek to the start of the marker so the user sees where it is.
        self.player.setPosition(int(reg.start * 1000))

    def _on_timeline_context(self, global_pos: QPoint, at_seconds: float, region_idx: int) -> None:
        """Right-click on the timeline. Show a menu whose actions depend on
        whether the click landed inside an existing region."""
        if not self._media_info:
            return
        menu = QMenu(self)

        set_in = menu.addAction(f"Set IN here  ({fmt_time(at_seconds)})")
        set_out = menu.addAction(f"Set OUT here  ({fmt_time(at_seconds)})")
        menu.addSeparator()
        add_region_here = menu.addAction("Add region using IN → OUT…")
        seek_here = menu.addAction("Seek to this point")

        rename_action = delete_action = None
        if region_idx >= 0:
            menu.addSeparator()
            reg = self._regions[region_idx]
            rename_action = menu.addAction(f"Rename region “{reg.name}”…")
            delete_action = menu.addAction(f"Delete region “{reg.name}”")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is set_in:
            self.timeline.set_start(at_seconds)
            self.start_input.setText(fmt_time(at_seconds))
        elif chosen is set_out:
            self.timeline.set_end(at_seconds)
            self.end_input.setText(fmt_time(at_seconds))
        elif chosen is add_region_here:
            self._on_add_marker()
        elif chosen is seek_here:
            self.player.setPosition(int(at_seconds * 1000))
        elif rename_action is not None and chosen is rename_action:
            self.markers_list.setCurrentRow(region_idx)
            self._on_rename_marker()
        elif delete_action is not None and chosen is delete_action:
            self.markers_list.setCurrentRow(region_idx)
            self._on_delete_marker()

    def _refresh_markers_ui(self) -> None:
        self.markers_list.blockSignals(True)
        self.markers_list.clear()
        for i, reg in enumerate(self._regions):
            label = f"{i + 1:>2}.  {reg.name}   ·   {fmt_time(reg.start)} → {fmt_time(reg.end)}   ({fmt_time(reg.duration)})"
            self.markers_list.addItem(QListWidgetItem(label))
        self.markers_list.blockSignals(False)
        self.timeline.set_regions(self._regions)
        self._refresh_preview()

    # --- waveform ----------------------------------------------------------
    def _start_waveform(self, path: str) -> None:
        # Cancel any previous run.
        self._cancel_waveform()
        thread, worker = start_waveform(path, width=2400, height=64)
        self._wave_thread = thread
        self._wave_worker = worker
        worker.ready.connect(self._on_waveform_ready)
        worker.failed.connect(self._on_waveform_failed)
        worker.ready.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.ready.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _cancel_waveform(self) -> None:
        if self._wave_thread is not None:
            try:
                self._wave_thread.quit()
                self._wave_thread.wait(500)
            except Exception:
                pass
        self._wave_thread = None
        self._wave_worker = None

    def _on_waveform_ready(self, pixmap) -> None:
        self.timeline.set_waveform(pixmap)

    def _on_waveform_failed(self, msg: str) -> None:
        self.statusBar().showMessage(f"Waveform: {msg}", 4000)

    # --- input handlers ----------------------------------------------------
    def _on_split_edited(self) -> None:
        self._sync_split_from_input()
        self._refresh_preview()

    def _sync_split_from_input(self) -> None:
        v = parse_time_input(self.split_input.text())
        if v is None or v <= 0:
            self.split_input.setText(fmt_time(self._settings.split_duration_seconds))
            return
        self.timeline.set_split_duration(v)

    def _on_start_edited(self) -> None:
        v = parse_time_input(self.start_input.text())
        if v is None or v < 0:
            self.start_input.setText(fmt_time(self.timeline.start()))
            return
        if self._media_info and v >= self._media_info.duration_seconds:
            v = max(0.0, self._media_info.duration_seconds - 0.5)
        self.timeline.set_start(v)
        self.start_input.setText(fmt_time(v))
        self._refresh_preview()

    def _on_end_edited(self) -> None:
        text = self.end_input.text().strip()
        if not text:
            self.timeline.set_end(-1.0)
            self._refresh_preview()
            return
        v = parse_time_input(text)
        if v is None or v <= 0:
            # revert
            if self.timeline.has_explicit_end():
                self.end_input.setText(fmt_time(self.timeline.end()))
            else:
                self.end_input.setText("")
            return
        if self._media_info and v > self._media_info.duration_seconds:
            v = self._media_info.duration_seconds
        self.timeline.set_end(v)
        self.end_input.setText(fmt_time(self.timeline.end()))
        self._refresh_preview()

    def _on_pick_folder(self) -> None:
        current = self.folder_edit.text().strip() or self._settings.last_export_dir or ""
        d = QFileDialog.getExistingDirectory(self, "Choose export folder", current)
        if d:
            self.folder_edit.setText(d)
            self._refresh_preview()

    # --- preview & export --------------------------------------------------
    def _current_format(self) -> str:
        data = self.format_combo.currentData()
        return data if isinstance(data, str) else "mp4_h264"

    def _current_ext(self) -> str:
        return fu.format_extension(self._current_format())

    def _on_format_changed(self, *_) -> None:
        """Enable/disable bitrate + quality controls based on chosen format."""
        key = self._current_format()
        self.audio_bitrate_combo.setEnabled(key in fu.LOSSY_AUDIO_FORMATS)
        self.video_quality_combo.setEnabled(key in fu.LOSSY_VIDEO_FORMATS)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if not self._media_info:
            self.segments_label.setText("—")
            self.preview_label.setText("Preview: —")
            return

        if self._is_markers_mode():
            n = len(self._regions)
            if n == 0:
                self.segments_label.setText("No markers yet. Set IN/OUT and press M to add one.")
                self.preview_label.setText("Preview: —")
                return
            total_dur = sum(r.duration for r in self._regions)
            self.segments_label.setText(
                f"→ {n} region{'s' if n != 1 else ''} · total {fmt_time(total_dur)}"
            )
            first = self._regions[0]
            name = f"{self.prefix_edit.text()}{first.name}{self.suffix_edit.text()}{self._current_ext()}"
            self.preview_label.setText(f"Preview: {name}")
            return

        # Auto-split mode.
        split = parse_time_input(self.split_input.text()) or 0.0
        start = parse_time_input(self.start_input.text()) or 0.0
        end = self.timeline.end()
        if split <= 0:
            self.segments_label.setText("Enter a valid split duration.")
            return
        span = max(0.0, end - start)
        if span <= 0:
            self.segments_label.setText("Start is at or past the end — nothing to export.")
            return
        n = int(span // split) + (1 if span - int(span // split) * split > 0.001 else 0)
        n = max(0, n)
        last = span - (n - 1) * split if n else 0.0
        msg = f"→ {n} segment{'s' if n != 1 else ''} · span {fmt_time(span)} · last {fmt_time(last)}"
        if n == 1 and span < split:
            msg += f"\n⚠ Range is shorter than the split duration — only 1 file will be produced. Lower the split to get multiple."
        self.segments_label.setText(msg)
        pad = self.pad_spin.value()
        name = f"{self.prefix_edit.text()}{str(1).zfill(pad)}{self.suffix_edit.text()}{self._current_ext()}"
        self.preview_label.setText(f"Preview: {name}")

    def _on_export(self) -> None:
        if not self._media_info or not self._current_file:
            return
        folder = self.folder_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "Choose folder", "Please choose an export folder.")
            return
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Cannot use folder", str(e))
            return
        if not os.access(folder, os.W_OK):
            QMessageBox.critical(self, "Cannot use folder",
                                 f"No write permission for:\n{folder}")
            return

        if self._is_markers_mode():
            if not self._regions:
                QMessageBox.warning(self, "No markers",
                                    "You're in Marked regions mode but haven't added any markers.")
                return
            segments = self._build_marker_segments(folder)
        else:
            split = parse_time_input(self.split_input.text()) or 0.0
            start = parse_time_input(self.start_input.text()) or 0.0
            if split <= 0:
                QMessageBox.warning(self, "Invalid duration",
                                    "Enter a valid split duration (must be > 0).")
                return
            if start >= self._media_info.duration_seconds:
                QMessageBox.warning(self, "Invalid start",
                                    "Start time is at or past the end of the media.")
                return

            # Effective end honors the OUT marker; falls back to media duration.
            effective_end = self.timeline.end()
            if effective_end <= start:
                QMessageBox.warning(self, "Invalid range",
                                    "End time must be after start time.")
                return

            try:
                segments = fu.build_segments(
                    total_duration=effective_end,
                    start_offset=start,
                    split_duration=split,
                    output_dir=folder,
                    prefix=self.prefix_edit.text(),
                    suffix=self.suffix_edit.text(),
                    extension=self._current_ext(),
                    pad_width=self.pad_spin.value(),
                    overwrite=self.overwrite_cb.isChecked(),
                )
            except ValueError as e:
                QMessageBox.warning(self, "Invalid settings", str(e))
                return

        if not segments:
            QMessageBox.information(self, "Nothing to export",
                                    "No segments were produced with these settings.")
            return

        # Persist settings for next run.
        self._save_current_settings(folder)

        # Kick off the worker.
        crf = self.video_quality_combo.currentData()
        if not isinstance(crf, int):
            crf = 18
        job = ExportJob(
            input_path=self._current_file,
            segments=segments,
            output_format=self._current_format(),
            audio_bitrate=self.audio_bitrate_combo.currentText() or "192k",
            video_crf=crf,
        )
        thread, worker = start_export(job)
        self._export_thread = thread
        self._export_worker = worker

        worker.progress.connect(self._on_progress)
        worker.segment_done.connect(self._on_segment_done)
        worker.segment_failed.connect(self._on_segment_failed)
        worker.finished.connect(self._on_export_finished)
        worker.log.connect(lambda msg: self.statusBar().showMessage(msg, 4000))

        self.export_btn.setEnabled(False)
        self.export_btn.setText("Exporting…")
        self.cancel_btn.setVisible(True)
        self.overall_bar.setValue(0)
        self.status_label.setText(f"Exporting {len(segments)} segments…")
        thread.start()

    def _build_marker_segments(self, folder: str) -> list[fu.SegmentSpec]:
        """Turn the regions list into SegmentSpecs. Filename uses region name;
        prefix/suffix wrap around it; conflicts fall back to numbering."""
        overwrite = self.overwrite_cb.isChecked()
        ext = self._current_ext()
        prefix = self.prefix_edit.text()
        suffix = self.suffix_edit.text()
        pad = self.pad_spin.value()

        specs: list[fu.SegmentSpec] = []
        used_paths: set[str] = set()
        for i, reg in enumerate(self._regions, 1):
            base_name = reg.name.strip() or str(i).zfill(pad)
            filename = f"{prefix}{base_name}{suffix}{ext}"
            out_path = os.path.join(folder, filename)
            # De-dupe within this batch too.
            if out_path in used_paths or (not overwrite and os.path.exists(out_path)):
                out_path = fu._unique_path(out_path)
            used_paths.add(out_path)
            specs.append(fu.SegmentSpec(
                index=i,
                start_seconds=reg.start,
                duration_seconds=reg.duration,
                output_path=out_path,
            ))
        return specs

    def _save_current_settings(self, folder: str) -> None:
        s = self._settings
        s.last_export_dir = folder
        v = parse_time_input(self.split_input.text())
        if v and v > 0:
            s.split_duration_seconds = int(v)
        fmt = self._current_format()
        if self._media_info and self._media_info.has_video and fu.format_kind(fmt) == "video":
            s.output_format_video = fmt
        else:
            s.output_format_audio = fmt
        s.audio_bitrate = self.audio_bitrate_combo.currentText() or s.audio_bitrate
        crf_val = self.video_quality_combo.currentData()
        if isinstance(crf_val, int):
            s.video_crf = crf_val
        s.prefix = self.prefix_edit.text()
        s.suffix = self.suffix_edit.text()
        s.pad_width = self.pad_spin.value()
        s.close_after_export = self.close_after_cb.isChecked()
        s.open_folder_after_export = self.open_folder_cb.isChecked()
        s.overwrite_existing = self.overwrite_cb.isChecked()
        app_settings.save(s)

    def _on_progress(self, seg_index: int, seg_frac: float, overall_frac: float) -> None:
        self.overall_bar.setValue(int(overall_frac * 1000))
        self.status_label.setText(
            f"Part {seg_index}: {int(seg_frac * 100)}%  ·  overall {int(overall_frac * 100)}%"
        )

    def _on_segment_done(self, index: int, total: int, out_path: str) -> None:
        self.statusBar().showMessage(f"Wrote {out_path}", 3000)

    def _on_segment_failed(self, rc: int, tail: str, out_path: str) -> None:
        self.statusBar().showMessage(f"Failed: {out_path} (rc={rc})", 6000)
        QMessageBox.critical(
            self, "Export failed",
            f"FFmpeg returned {rc} for:\n{out_path}\n\nLast output:\n{tail[-1500:]}"
        )

    def _on_cancel(self) -> None:
        if self._export_worker:
            self._export_worker.cancel()
            self.status_label.setText("Cancelling…")

    def _on_export_finished(self, success: bool) -> None:
        self.export_btn.setEnabled(True)
        self.export_btn.setText("Export")
        self.cancel_btn.setVisible(False)
        self._export_worker = None
        self._export_thread = None

        if success:
            self.overall_bar.setValue(1000)
            self.status_label.setText("Export complete ✓")
            if self.open_folder_cb.isChecked():
                self._open_folder(self.folder_edit.text().strip())
            if self.close_after_cb.isChecked():
                QTimer.singleShot(500, self.close)
        else:
            self.status_label.setText("Export did not complete.")

    def _open_folder(self, path: str) -> None:
        if not path or not os.path.isdir(path):
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def closeEvent(self, ev: QCloseEvent) -> None:
        if self._export_worker:
            self._export_worker.cancel()
        self._cancel_waveform()
        try:
            self._save_current_settings(self.folder_edit.text().strip())
        except Exception:
            pass
        super().closeEvent(ev)
