"""Main window for AutoSplit."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl, QTimer, Signal, QPoint
from PySide6.QtGui import (
    QAction, QCloseEvent, QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
    QStatusBar, QToolButton, QVBoxLayout, QWidget, QStyle,
)

import ffmpeg_utils as fu
import rife_pipeline as rp
import rife_setup
import settings as app_settings
import frame_extract as fx
import speed_export as sx
from export_worker import ExportJob, start_export
from frame_dialog import AllFramesDialog, GifExportDialog, SingleFrameDialog
from preferences_dialog import PreferencesDialog
from regions import Region, region_rgb, sanitize_name
from speed_dialog import SpeedExportDialog
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
QToolButton#Primary { background: #0078d4; border: 1px solid #0078d4; color: white; font-weight: 600; padding: 8px 20px; padding-right: 26px; border-radius: 4px; }
QToolButton#Primary:hover { background: #106ebe; }
QToolButton#Primary:disabled { background: #444; color: #888; border-color: #444; }
QToolButton#Primary::menu-button { background: #0078d4; border: none; border-left: 1px solid rgba(255,255,255,0.45); width: 22px; border-top-right-radius: 4px; border-bottom-right-radius: 4px; }
QToolButton#Primary::menu-button:hover { background: #106ebe; }
QToolButton#Primary::menu-arrow { subcontrol-position: center; }
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
    def __init__(self, initial_file: Optional[str] = None, collector=None):
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
        # Queue state (populated by an IPC collector when 'Open with' multi-file).
        self._collector = collector
        self._queue: list[str] = []
        self._queue_index: int = -1
        self._queue_completed: set[int] = set()
        # Filmstrip worker refs (kept alive to prevent Python GC dropping them).
        self._filmstrip_thread = None
        self._filmstrip_worker = None
        # Loop-play state (L toggles). When on and a marker is selected, playback
        # cycles within that marker's IN/OUT; otherwise within the top-level IN/OUT.
        self._loop_enabled: bool = False

        self._build_ui()
        self._wire_shortcuts()

        # Poll player position for timeline updates.
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(80)
        self._pos_timer.timeout.connect(self._sync_position)
        self._pos_timer.start()

        if initial_file:
            QTimer.singleShot(0, lambda: self._load_file(initial_file))

        if collector is not None:
            collector.ready.connect(self._on_queue_ready)
            collector.added.connect(self._on_queue_file_added)
            self.file_label.setText("Collecting selected files...")

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

        # Zoom + filmstrip toolbar below the timeline.
        tl_tools = QHBoxLayout()
        tl_tools.setSpacing(4)
        z_out = QPushButton("Zoom -")
        z_out.setFixedWidth(78)
        z_out.setToolTip("Zoom out (also mouse wheel over the timeline)")
        z_out.clicked.connect(lambda: self._timeline_zoom(0.7))
        tl_tools.addWidget(z_out)
        z_in = QPushButton("Zoom +")
        z_in.setFixedWidth(78)
        z_in.setToolTip("Zoom in (also mouse wheel over the timeline)")
        z_in.clicked.connect(lambda: self._timeline_zoom(1.4))
        tl_tools.addWidget(z_in)
        z_fit = QPushButton("Fit")
        z_fit.setFixedWidth(48)
        z_fit.setToolTip("Zoom out to fit the whole media")
        z_fit.clicked.connect(self._on_timeline_zoom_fit)
        tl_tools.addWidget(z_fit)
        self.filmstrip_btn = QPushButton("Filmstrip")
        self.filmstrip_btn.setCheckable(True)
        self.filmstrip_btn.setToolTip("Show / hide video thumbnails across the timeline")
        self.filmstrip_btn.toggled.connect(self._on_toggle_filmstrip)
        tl_tools.addWidget(self.filmstrip_btn)
        tl_tools.addStretch(1)
        left_lay.addLayout(tl_tools)

        hint = QLabel(
            "Left-drag = seek  ·  <b>Alt-drag = highlight to add region</b>  ·  Shift-drag = IN  ·  Ctrl-drag = OUT  ·  "
            "Right-click = menu  ·  <b>Wheel = zoom / Shift+wheel = pan</b>  ·  "
            "I/O = mark  ·  M = add  ·  S = toggle 1 s ↔ 1 frame  ·  "
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

        self.queue_group = self._build_queue_group()
        self.queue_group.setVisible(False)
        right_lay.addWidget(self.queue_group)
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
        # Split-button Export: default click runs segment export; a dropdown
        # arrow opens a menu with frame extraction and GIF options.
        self.export_btn = QToolButton()
        self.export_btn.setText("Export")
        self.export_btn.setObjectName("Primary")
        self.export_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.export_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export)   # default action

        menu = QMenu(self)
        act_segs = menu.addAction("Export segments   (default)")
        act_segs.triggered.connect(self._on_export)
        menu.addSeparator()
        act_frame_now = menu.addAction("Extract current frame…")
        act_frame_now.triggered.connect(self._on_extract_current_frame)
        act_frame_all = menu.addAction("Extract all frames…")
        act_frame_all.triggered.connect(self._on_extract_all_frames)
        menu.addSeparator()
        act_marker_first = menu.addAction("Export selected marker → first frame…")
        act_marker_first.triggered.connect(self._on_extract_marker_first_frame)
        act_marker_last = menu.addAction("Export selected marker → last frame…")
        act_marker_last.triggered.connect(self._on_extract_marker_last_frame)
        menu.addSeparator()
        act_slomo = menu.addAction("Export with speed change / slow-motion…")
        act_slomo.triggered.connect(self._on_export_speed)
        act_upscale = menu.addAction("Upscale to 4K / 8K (AI, GPU)…")
        act_upscale.triggered.connect(self._on_export_upscale)
        act_gif = menu.addAction("Export as GIF…")
        act_gif.triggered.connect(self._on_export_gif)
        self.export_btn.setMenu(menu)
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

    def _build_queue_group(self) -> QGroupBox:
        gb = QGroupBox("Queue")
        v = QVBoxLayout(gb)

        self.queue_status_label = QLabel("—")
        v.addWidget(self.queue_status_label)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.queue_prev_btn = QPushButton("◀ Prev")
        self.queue_prev_btn.clicked.connect(lambda: self._queue_step(-1))
        row.addWidget(self.queue_prev_btn)
        self.queue_next_btn = QPushButton("Next ▶")
        self.queue_next_btn.clicked.connect(lambda: self._queue_step(+1))
        row.addWidget(self.queue_next_btn)
        self.queue_skip_btn = QPushButton("Skip")
        self.queue_skip_btn.setToolTip("Mark current file as skipped and move to the next.")
        self.queue_skip_btn.clicked.connect(self._on_queue_skip)
        row.addWidget(self.queue_skip_btn)
        self.queue_clear_btn = QPushButton("Clear")
        self.queue_clear_btn.setToolTip("Clear the queue; keep the current file loaded.")
        self.queue_clear_btn.clicked.connect(self._on_queue_clear)
        row.addWidget(self.queue_clear_btn)
        v.addLayout(row)

        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(180)
        self.queue_list.itemDoubleClicked.connect(self._on_queue_item_dbl)
        v.addWidget(self.queue_list)

        self.queue_auto_cb = QCheckBox("Auto-advance to next file after successful export")
        self.queue_auto_cb.setChecked(True)
        v.addWidget(self.queue_auto_cb)

        v.addWidget(QLabel(
            "Split length, format, quality, folder and naming carry over between files. "
            "Markers and IN/OUT reset per file.", objectName="Muted"))
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

        # Second row: jump / frame actions on the selected marker.
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.jump_first_btn = QPushButton("|◀ First")
        self.jump_first_btn.setToolTip("Jump the playhead to the selected marker's IN")
        self.jump_first_btn.clicked.connect(lambda: self._on_marker_jump("first"))
        row2.addWidget(self.jump_first_btn)
        self.jump_last_btn = QPushButton("Last ▶|")
        self.jump_last_btn.setToolTip("Jump the playhead to the selected marker's OUT")
        self.jump_last_btn.clicked.connect(lambda: self._on_marker_jump("last"))
        row2.addWidget(self.jump_last_btn)
        self.loop_btn = QPushButton("Loop")
        self.loop_btn.setCheckable(True)
        self.loop_btn.setToolTip("Loop playback within the selected marker's IN/OUT (L)")
        self.loop_btn.toggled.connect(self._on_loop_button_toggled)
        row2.addWidget(self.loop_btn)
        v.addLayout(row2)

        # Third row: batch operations on the markers list.
        row3 = QHBoxLayout()
        row3.setSpacing(6)
        self.save_markers_btn = QPushButton("Save…")
        self.save_markers_btn.setToolTip("Save all markers to a JSON file")
        self.save_markers_btn.clicked.connect(self._on_save_markers)
        row3.addWidget(self.save_markers_btn)
        self.load_markers_btn = QPushButton("Load…")
        self.load_markers_btn.setToolTip("Load markers from a JSON file (replaces current)")
        self.load_markers_btn.clicked.connect(self._on_load_markers)
        row3.addWidget(self.load_markers_btn)
        self.batch_rename_btn = QPushButton("Batch rename…")
        self.batch_rename_btn.setToolTip("Rename every marker with a pattern (e.g. clip_%02d)")
        self.batch_rename_btn.clicked.connect(self._on_batch_rename_markers)
        row3.addWidget(self.batch_rename_btn)
        v.addLayout(row3)

        # Fourth row: auto-detect.
        row4 = QHBoxLayout()
        row4.setSpacing(6)
        self.autodetect_btn = QPushButton("Auto-detect scenes / silence…")
        self.autodetect_btn.setToolTip(
            "Scan the media for hard cuts or silences and seed markers from them.")
        self.autodetect_btn.clicked.connect(self._on_auto_detect)
        row4.addWidget(self.autodetect_btn)
        v.addLayout(row4)

        self.markers_list = QListWidget()
        self.markers_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.markers_list.itemDoubleClicked.connect(self._on_rename_marker)
        self.markers_list.currentRowChanged.connect(self._on_marker_selected)
        self.markers_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.markers_list.customContextMenuRequested.connect(self._on_marker_context_menu)
        v.addWidget(self.markers_list)

        tip = QLabel(
            "Tip: press I / O at the playhead to set IN and OUT, then M to add the region. "
            "Right-click a marker for a menu (rename, delete, jump, export first / last frame).",
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
        self.click_free_cb = QCheckBox(
            "Click-free audio cuts (5ms fade at boundaries)"
        )
        self.click_free_cb.setChecked(self._settings.click_free_cuts)
        self.click_free_cb.setToolTip(
            "Prevents the small 'click' you sometimes hear at a hard cut in "
            "the middle of an audio wave, by adding a tiny 5ms fade-in/out at "
            "each segment's boundaries.")
        g.addWidget(self.click_free_cb)
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
            "toggle_loop":    self._toggle_loop_play,
            "speed_down":     lambda: self._adjust_playback_rate(0.5),
            "speed_up":       lambda: self._adjust_playback_rate(2.0),
            "speed_reset":    lambda: self._set_playback_rate(1.0),
            "marker_in_left":   lambda: self._nudge_marker_edge("in", -1),
            "marker_in_right":  lambda: self._nudge_marker_edge("in", +1),
            "marker_out_left":  lambda: self._nudge_marker_edge("out", -1),
            "marker_out_right": lambda: self._nudge_marker_edge("out", +1),
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
        self.timeline.set_filmstrip_frames([])
        self.timeline.set_zoom(1.0, center_seconds=0.0)
        self._regions.clear()
        self._refresh_markers_ui()
        self._sync_split_from_input()
        self.start_input.setText("00:00:00")
        self.end_input.setText("")
        # Kick off waveform generation on a worker thread.
        self._start_waveform(path)
        # Re-generate filmstrip if the toggle is on.
        if self.filmstrip_btn.isChecked() and info.has_video:
            self._load_filmstrip()

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
        rate_txt = f"   ×{self.player.playbackRate():g}" if abs(self.player.playbackRate() - 1.0) > 0.01 else ""
        loop_txt = "   LOOP" if self._loop_enabled else ""
        self.time_label.setText(f"{fmt_time(pos_s)} / {fmt_time(dur_s)}{rate_txt}{loop_txt}")

        # Loop-play enforcement. Cycle within selected marker's IN/OUT, else
        # top-level IN/OUT if any, else full duration.
        if self._loop_enabled and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            idx = self._selected_marker_index()
            if idx >= 0:
                loop_start = self._regions[idx].start
                loop_end = self._regions[idx].end
            else:
                loop_start = self.timeline.start()
                loop_end = self.timeline.end()
            if loop_end > loop_start and pos_s >= loop_end - 0.02:
                self.player.setPosition(int(loop_start * 1000))

    def _toggle_loop_play(self) -> None:
        self._loop_enabled = not self._loop_enabled
        if self._loop_enabled:
            idx = self._selected_marker_index()
            if idx >= 0:
                reg = self._regions[idx]
                msg = f"Loop ON  ·  {reg.name or f'clip {idx+1}'}  ·  {fmt_time(reg.start)} → {fmt_time(reg.end)}"
            else:
                msg = f"Loop ON  ·  IN/OUT range  ({fmt_time(self.timeline.start())} → {fmt_time(self.timeline.end())})"
        else:
            msg = "Loop OFF"
        self.statusBar().showMessage(msg, 3000)
        self.loop_btn.setChecked(self._loop_enabled)

    def _set_playback_rate(self, rate: float) -> None:
        rate = max(0.1, min(8.0, rate))
        self.player.setPlaybackRate(rate)
        self.statusBar().showMessage(f"Playback speed  ×{rate:g}", 2500)

    def _adjust_playback_rate(self, factor: float) -> None:
        new_rate = self.player.playbackRate() * factor
        # Snap to standard rates.
        for r in (0.125, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0):
            if abs(new_rate - r) < 0.05 * r:
                new_rate = r; break
        self._set_playback_rate(new_rate)

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

    # --- queue -------------------------------------------------------------
    def _on_queue_file_added(self, path: str) -> None:
        # Live indicator while the collector is still receiving invocations.
        if not self._queue:
            self.file_label.setText(f"Collecting selected files… ({1} so far)")
        else:
            self.file_label.setText(
                f"Collecting selected files… ({len(self._queue) + 1} so far)")

    def _on_queue_ready(self, paths: list) -> None:
        if not paths:
            return
        import re
        def natural_key(p):
            base = os.path.basename(p).lower()
            return [int(t) if t.isdigit() else t
                    for t in re.split(r"(\d+)", base)]
        self._queue = sorted(paths, key=natural_key)
        self._queue_index = 0
        self._queue_completed.clear()
        self.queue_group.setVisible(len(self._queue) > 1)
        self._refresh_queue_ui()
        self._load_file(self._queue[0])
        # Collector's job is done.
        if self._collector is not None:
            self._collector.stop()
            self._collector = None

    def _queue_step(self, delta: int) -> None:
        if not self._queue:
            return
        new_idx = self._queue_index + delta
        if not (0 <= new_idx < len(self._queue)):
            return
        self._queue_index = new_idx
        self._load_file(self._queue[self._queue_index])
        self._refresh_queue_ui()

    def _on_queue_skip(self) -> None:
        if not self._queue:
            return
        self._queue_completed.add(self._queue_index)   # mark as done/skipped
        if self._queue_index < len(self._queue) - 1:
            self._queue_step(+1)
        else:
            self._refresh_queue_ui()

    def _on_queue_clear(self) -> None:
        if not self._queue:
            return
        # Keep the currently-loaded file loaded; empty the queue list itself.
        self._queue = []
        self._queue_index = -1
        self._queue_completed.clear()
        self.queue_group.setVisible(False)
        self._refresh_queue_ui()

    def _on_queue_item_dbl(self, item) -> None:
        row = self.queue_list.row(item)
        if 0 <= row < len(self._queue):
            self._queue_index = row
            self._load_file(self._queue[row])
            self._refresh_queue_ui()

    def _refresh_queue_ui(self) -> None:
        n = len(self._queue)
        if n == 0:
            self.queue_status_label.setText("—")
            self.queue_list.clear()
            self.queue_group.setVisible(False)
            return
        cur = self._queue_index + 1
        remaining = max(0, n - len(self._queue_completed))
        self.queue_status_label.setText(
            f"<b>{cur} / {n}</b>   ·   {remaining} remaining"
        )
        self.queue_list.blockSignals(True)
        self.queue_list.clear()
        for i, p in enumerate(self._queue):
            name = os.path.basename(p)
            if i == self._queue_index:
                prefix = "▶ "
            elif i in self._queue_completed:
                prefix = "✓ "
            else:
                prefix = "  "
            item = QListWidgetItem(f"{prefix}{i + 1:>2}.  {name}")
            self.queue_list.addItem(item)
        self.queue_list.setCurrentRow(self._queue_index)
        self.queue_list.blockSignals(False)
        self.queue_prev_btn.setEnabled(self._queue_index > 0)
        self.queue_next_btn.setEnabled(self._queue_index < n - 1)
        self.queue_skip_btn.setEnabled(self._queue_index < n - 1)

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

    def _selected_marker_index(self) -> int:
        row = self.markers_list.currentRow()
        return row if 0 <= row < len(self._regions) else -1

    def _on_marker_jump(self, which: str) -> None:
        idx = self._selected_marker_index()
        if idx < 0:
            return
        reg = self._regions[idx]
        if which == "first":
            target = max(0.0, reg.start)
        else:
            # Windows Media Foundation (Qt's Windows backend) seeks to the
            # NEAREST KEYFRAME by default — for H.264 with long GOPs this
            # can land 2-5 seconds before the requested time (which reads
            # as 'jumped to the middle of the region').
            # Nudge back a hair from the exact end, pause first so the
            # decoder is idle when the seek lands, then optionally play.
            frame_period = self._media_info.frame_period() if self._media_info else 0.04
            # A tiny offset (2 frame periods) is enough to keep us inside the
            # region even when the region end === media duration.
            target = max(reg.start, reg.end - 2 * frame_period)
        # Pausing before the seek makes the Windows backend more accurate.
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        self.player.setPosition(int(target * 1000))
        # Update the timeline immediately so the playhead moves before the
        # player's own polling catches up (WMF can lag by several frames).
        self.timeline.set_position(target)
        self.time_label.setText(
            f"{fmt_time(target)} / {fmt_time(self._media_info.duration_seconds if self._media_info else 0)}")

    def _on_marker_context_menu(self, pos) -> None:
        item = self.markers_list.itemAt(pos)
        if item is None:
            return
        row = self.markers_list.row(item)
        if not (0 <= row < len(self._regions)):
            return
        self.markers_list.setCurrentRow(row)
        reg = self._regions[row]

        menu = QMenu(self)
        act_jump_in  = menu.addAction(f"Jump to first frame  ({fmt_time(reg.start)})")
        act_jump_out = menu.addAction(f"Jump to last frame  ({fmt_time(reg.end)})")
        menu.addSeparator()
        act_load_range = menu.addAction("Load as current IN / OUT")
        menu.addSeparator()
        act_frame_first = menu.addAction("Export first frame…")
        act_frame_last  = menu.addAction("Export last frame…")
        act_speed       = menu.addAction("Export with speed change / slow-motion…")
        menu.addSeparator()
        act_rename = menu.addAction("Rename…")
        act_delete = menu.addAction("Delete")

        chosen = menu.exec(self.markers_list.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_jump_in:
            self._on_marker_jump("first")
        elif chosen is act_jump_out:
            self._on_marker_jump("last")
        elif chosen is act_load_range:
            self.timeline.set_start(reg.start)
            self.timeline.set_end(reg.end)
            self.start_input.setText(fmt_time(reg.start))
            self.end_input.setText(fmt_time(reg.end))
        elif chosen is act_frame_first:
            self._extract_frame_at(reg.start, suffix=f"{reg.name or 'clip'}_first")
        elif chosen is act_frame_last:
            frame_period = self._media_info.frame_period() if self._media_info else 0.04
            t = max(reg.start, reg.end - frame_period)
            self._extract_frame_at(t, suffix=f"{reg.name or 'clip'}_last")
        elif chosen is act_speed:
            self._on_export_speed(in_seconds=reg.start, out_seconds=reg.end)
        elif chosen is act_rename:
            self._on_rename_marker()
        elif chosen is act_delete:
            self._on_delete_marker()

    def _on_loop_button_toggled(self, on: bool) -> None:
        # Sync button state with keyboard-toggle state without infinite bounce.
        if on != self._loop_enabled:
            self._toggle_loop_play()

    # --- Save / load / batch-rename markers --------------------------------
    def _on_save_markers(self) -> None:
        if not self._regions:
            QMessageBox.information(self, "Nothing to save",
                                    "There are no markers yet.")
            return
        base = os.path.splitext(os.path.basename(self._current_file))[0] if self._current_file else "markers"
        default = os.path.join(self._settings.last_export_dir or "", f"{base}.markers.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save markers", default, "Markers JSON (*.json)"
        )
        if not path:
            return
        payload = {
            "source": os.path.abspath(self._current_file) if self._current_file else "",
            "duration": self._media_info.duration_seconds if self._media_info else 0,
            "regions": [
                {"name": r.name, "start": r.start, "end": r.end}
                for r in self._regions
            ],
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                import json
                json.dump(payload, f, indent=2)
            self.statusBar().showMessage(f"Saved {len(self._regions)} markers → {path}", 4000)
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def _on_load_markers(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load markers", "", "Markers JSON (*.json);;All files (*.*)"
        )
        if not path:
            return
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            raw = payload.get("regions", [])
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.critical(self, "Load failed", str(e))
            return
        new_regions: list[Region] = []
        for r in raw:
            try:
                new_regions.append(Region(
                    start=float(r["start"]),
                    end=float(r["end"]),
                    name=str(r.get("name", "")),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        if not new_regions:
            QMessageBox.warning(self, "No usable markers", "The JSON had no valid regions.")
            return
        if self._regions:
            ans = QMessageBox.question(
                self, "Replace markers",
                f"Load {len(new_regions)} marker(s)?\n\n"
                "Yes = replace current markers\nNo = append to current"
            )
            if ans == QMessageBox.StandardButton.Yes:
                self._regions.clear()
            elif ans != QMessageBox.StandardButton.No:
                return
        self._regions.extend(new_regions)
        self._refresh_markers_ui()
        self.statusBar().showMessage(f"Loaded {len(new_regions)} markers.", 3000)
        if not self._is_markers_mode():
            self.mode_markers_rb.setChecked(True)

    def _on_auto_detect(self) -> None:
        if not self._media_info or not self._current_file:
            return
        from auto_detect_dialog import AutoDetectDialog
        from regions import Region

        dlg = AutoDetectDialog(self._current_file,
                               self._media_info.duration_seconds, self)
        if dlg.exec() != AutoDetectDialog.DialogCode.Accepted:
            return
        found = dlg.selected_regions()
        if not found:
            return
        # Ask before wiping current markers.
        if self._regions:
            ans = QMessageBox.question(
                self, "Add markers",
                f"Detected {len(found)} region(s).\n\n"
                "Yes = replace current markers\nNo = append to current"
            )
            if ans == QMessageBox.StandardButton.Yes:
                self._regions.clear()
            elif ans != QMessageBox.StandardButton.No:
                return
        for start, end, name in found:
            self._regions.append(Region(start=start, end=end, name=name))
        self._refresh_markers_ui()
        if not self._is_markers_mode():
            self.mode_markers_rb.setChecked(True)
        self.statusBar().showMessage(
            f"Added {len(found)} markers from auto-detect.", 4000)

    # --- selected-marker IN/OUT nudge ---------------------------------------
    def _nudge_marker_edge(self, edge: str, sign: int) -> None:
        """Shift the selected marker's IN or OUT by one step (respects snap)."""
        idx = self._selected_marker_index()
        if idx < 0 or not self._media_info:
            return
        reg = self._regions[idx]
        step = self._media_info.frame_period() if self._snap_fine else 1.0
        delta = sign * step
        if edge == "in":
            new_start = max(0.0, min(reg.end - 0.01, reg.start + delta))
            reg.start = new_start
        else:
            new_end = max(reg.start + 0.01, min(self._media_info.duration_seconds,
                                                reg.end + delta))
            reg.end = new_end
        self._refresh_markers_ui()
        self.markers_list.setCurrentRow(idx)   # keep selection through refresh
        self.statusBar().showMessage(
            f"{reg.name or f'clip {idx + 1}'}  ·  "
            f"{fmt_time(reg.start)} → {fmt_time(reg.end)}  "
            f"({fmt_time(reg.duration)})", 2000)

    def _on_batch_rename_markers(self) -> None:
        if not self._regions:
            return
        pattern, ok = QInputDialog.getText(
            self, "Batch rename markers",
            "Filename-safe pattern.  Placeholders:\n"
            "  {n}    1-based index (padded to 3)\n"
            "  {name} original name\n"
            "  {i}    1-based unpadded index\n"
            "  {mmss} start time  mm:ss\n\n"
            "Example: clip_{n}    or   scene_{n}_{name}",
            text="clip_{n}"
        )
        if not ok or not pattern.strip():
            return
        pattern = pattern.strip()
        for i, reg in enumerate(self._regions):
            mins = int(reg.start // 60); secs = int(reg.start - mins * 60)
            new_name = (pattern
                        .replace("{n}", f"{i + 1:03d}")
                        .replace("{i}", str(i + 1))
                        .replace("{name}", reg.name)
                        .replace("{mmss}", f"{mins:02d}{secs:02d}"))
            reg.name = sanitize_name(new_name) or f"clip_{i + 1:03d}"
        self._refresh_markers_ui()

    def _extract_frame_at(self, timestamp: float, suffix: str = "") -> None:
        """Grab one frame at `timestamp` using the current defaults, then optionally
        show the options dialog based on the prompt-before-frame-export setting."""
        if not self._media_info or not self._current_file:
            return

        prompt = self._settings.prompt_before_frame_export
        if prompt:
            dlg = SingleFrameDialog(self._settings, self._current_file, timestamp, self)
            # Pre-fill the path with the suffix so region-based grabs get a
            # meaningful filename.
            if suffix:
                base = os.path.splitext(os.path.basename(self._current_file))[0]
                folder = self._settings.frame_output_dir or os.path.dirname(self._current_file)
                fmt = self._settings.frame_format
                dlg.path_edit.setText(os.path.join(folder, f"{base}_{suffix}.{fmt}"))
            if dlg.exec() != SingleFrameDialog.DialogCode.Accepted:
                return
            sel = dlg.selection()
        else:
            fmt = self._settings.frame_format
            folder = self._settings.frame_output_dir or os.path.dirname(self._current_file)
            base = os.path.splitext(os.path.basename(self._current_file))[0]
            tail = f"_{suffix}" if suffix else f"_frame_{int(timestamp):04d}s"
            sel = {
                "format": fmt,
                "quality": self._settings.frame_jpg_quality,
                "output_path": os.path.join(folder, f"{base}{tail}.{fmt}"),
            }

        out = sel["output_path"]
        if not out:
            QMessageBox.warning(self, "Missing filename", "Choose a save path.")
            return
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

        cmd = fx.build_single_frame_cmd(
            input_path=self._current_file,
            timestamp_seconds=timestamp,
            output_path=out,
            quality=int(sel["quality"]),
        )
        self._settings.frame_format = sel["format"]
        self._settings.frame_jpg_quality = int(sel["quality"])
        self._settings.frame_output_dir = os.path.dirname(out)
        app_settings.save(self._settings)
        self._run_ffmpeg_async(cmd, total_duration=0.001,
                               output_path=out, label="Extract frame")

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

    def _region_swatch(self, index: int, size: int = 14) -> QIcon:
        """Rounded filled swatch matching the timeline overlay color."""
        r, g, b = region_rgb(index)
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(r, g, b))
        p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
        p.end()
        return QIcon(pm)

    def _refresh_markers_ui(self) -> None:
        self.markers_list.blockSignals(True)
        self.markers_list.clear()
        for i, reg in enumerate(self._regions):
            label = f"{i + 1:>2}.  {reg.name}   ·   {fmt_time(reg.start)} → {fmt_time(reg.end)}   ({fmt_time(reg.duration)})"
            item = QListWidgetItem(self._region_swatch(i), label)
            self.markers_list.addItem(item)
        self.markers_list.blockSignals(False)
        self.timeline.set_regions(self._regions)
        # Enable/disable dependent buttons.
        has_any = bool(self._regions)
        self.jump_first_btn.setEnabled(has_any)
        self.jump_last_btn.setEnabled(has_any)
        self._refresh_preview()

    # --- waveform ----------------------------------------------------------
    def _start_waveform(self, path: str) -> None:
        # Cancel any previous run.
        self._cancel_waveform()
        thread, worker = start_waveform(path, width=2400, height=64)
        self._wave_thread = thread
        self._wave_worker = worker
        # Bind the result to the file it was generated for: the previous
        # worker may still be running (it blocks in subprocess.run) and would
        # otherwise paint the old file's waveform onto the new one.
        worker.ready.connect(lambda pm, p=path: self._on_waveform_ready(pm, p))
        worker.failed.connect(lambda msg, p=path: self._on_waveform_failed(msg, p))
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

    def _on_waveform_ready(self, pixmap, path: str) -> None:
        if path != self._current_file:
            return  # result from a file that was replaced meanwhile
        self.timeline.set_waveform(pixmap)

    def _on_waveform_failed(self, msg: str, path: str) -> None:
        if path != self._current_file:
            return
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
            click_fade_ms=(self._settings.click_fade_ms
                           if self._settings.click_free_cuts else 0.0),
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

    # --- timeline zoom / filmstrip -----------------------------------------
    def _timeline_zoom(self, factor: float) -> None:
        self.timeline.set_zoom(self.timeline.zoom() * factor)

    def _on_timeline_zoom_fit(self) -> None:
        self.timeline.set_zoom(1.0, center_seconds=0.0)

    def _on_toggle_filmstrip(self, on: bool) -> None:
        self.timeline.set_show_filmstrip(on)
        if on and self._media_info and self._media_info.has_video and self._current_file:
            self._load_filmstrip()

    def _load_filmstrip(self, n_frames: int = 24) -> None:
        """Grab N evenly-spaced thumbnails and hand them to the Timeline.

        QPixmap can only be constructed on the GUI thread, so the worker
        returns QImages (thread-safe) and we convert to QPixmap here."""
        if not (self._media_info and self._current_file):
            return
        source_file = self._current_file
        import subprocess, tempfile
        dur = self._media_info.duration_seconds
        if dur <= 0:
            return
        target_fps = max(n_frames / dur, 0.02)
        pattern = os.path.join(tempfile.gettempdir(), "autosplit_filmstrip_%03d.jpg")
        # Clean up any stale frames from a prior run.
        for i in range(1, 401):
            try:
                os.remove(pattern.replace("%03d", f"{i:03d}"))
            except OSError:
                pass
        cmd = [
            fu.ffmpeg_path(), "-y", "-v", "error",
            "-i", self._current_file,
            "-vf", f"fps={target_fps},scale=160:-2",
            "-q:v", "3",
            pattern,
        ]
        from PySide6.QtCore import QThread as _QThread, QObject, Signal as _Signal
        from PySide6.QtGui import QImage, QPixmap

        class _FSWorker(QObject):
            done = _Signal(list)   # list of QImage
            def __init__(self, cmd, pattern):
                super().__init__()
                self._cmd = cmd
                self._pat = pattern
            def run(self):
                try:
                    subprocess.run(self._cmd, capture_output=True,
                                   **fu._no_window_kwargs())
                except Exception:
                    self.done.emit([]); return
                images = []
                for i in range(1, 401):
                    p = self._pat.replace("%03d", f"{i:03d}")
                    if not os.path.exists(p):
                        break
                    img = QImage(p)
                    if not img.isNull():
                        images.append(img)
                self.done.emit(images)

        # Kill any prior filmstrip run so we don't race.
        if self._filmstrip_thread is not None:
            try:
                self._filmstrip_thread.quit()
                self._filmstrip_thread.wait(300)
            except Exception:
                pass
        self._filmstrip_thread = _QThread()
        self._filmstrip_worker = _FSWorker(cmd, pattern)
        self._filmstrip_worker.moveToThread(self._filmstrip_thread)
        self._filmstrip_thread.started.connect(self._filmstrip_worker.run)

        def _on_done(images):
            # Convert to QPixmap on the main GUI thread (QPixmap is not
            # thread-safe; QImage returned from the worker is).
            if self._current_file != source_file:
                return  # stale frames from a file that was replaced meanwhile
            try:
                pixmaps = [QPixmap.fromImage(img) for img in images]
                self.timeline.set_filmstrip_frames(pixmaps)
            finally:
                if self._filmstrip_thread is not None:
                    self._filmstrip_thread.quit()

        def _on_finished():
            self._filmstrip_thread = None
            self._filmstrip_worker = None

        self._filmstrip_worker.done.connect(_on_done)
        self._filmstrip_thread.finished.connect(_on_finished)
        self._filmstrip_thread.start()

    # --- Frame / GIF exports (split-button menu) ---------------------------
    def _current_range(self) -> tuple[float, float]:
        """Return (start, end) using IN/OUT if set, else the full media."""
        if not self._media_info:
            return (0.0, 0.0)
        start = self.timeline.start()
        end = self.timeline.end()
        if end <= start:
            end = self._media_info.duration_seconds
        return (start, end)

    def _run_ffmpeg_async(self, cmd: list, total_duration: float,
                          output_path: str, label: str) -> None:
        """Fire an ffmpeg command in a QThread with progress + completion UI."""
        from PySide6.QtCore import QObject, QThread as _QThread, Signal as _Signal

        class _Worker(QObject):
            progress = _Signal(float)
            done_ok  = _Signal(str)
            failed   = _Signal(str)
            def __init__(self, cmd, total, out):
                super().__init__()
                self._cmd = cmd
                self._total = float(total)
                self._out = out
                self._cancel = False
            def cancel(self): self._cancel = True
            def run(self):
                def on_progress(t):
                    if self._total > 0:
                        self.progress.emit(min(t / self._total, 1.0))
                rc, tail = fu.run_ffmpeg(
                    self._cmd,
                    on_progress=on_progress,
                    should_cancel=lambda: self._cancel,
                )
                if rc == 0:
                    self.done_ok.emit(self._out)
                else:
                    self.failed.emit(f"ffmpeg exited with {rc}\n\n{tail[-1200:]}")

        thread = _QThread()
        worker = _Worker(cmd, total_duration, output_path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def on_prog(f: float):
            self.overall_bar.setValue(int(f * 1000))
            self.status_label.setText(f"{label}: {int(f * 100)}%")

        def on_ok(out: str):
            self.overall_bar.setValue(1000)
            self.status_label.setText(f"{label} complete ✓  ·  {out}")
            self.export_btn.setEnabled(True)
            self.export_btn.setText("Export")
            self.cancel_btn.setVisible(False)
            self._export_thread = None
            self._export_worker = None
            if self.open_folder_cb.isChecked():
                self._open_folder(os.path.dirname(out) or out)
            thread.quit()

        def on_fail(msg: str):
            self.export_btn.setEnabled(True)
            self.export_btn.setText("Export")
            self.cancel_btn.setVisible(False)
            self._export_thread = None
            self._export_worker = None
            QMessageBox.critical(self, f"{label} failed", msg)
            self.status_label.setText(f"{label} failed.")
            thread.quit()

        worker.progress.connect(on_prog)
        worker.done_ok.connect(on_ok)
        worker.failed.connect(on_fail)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        # Reuse the export cancel button.
        self._export_thread = thread
        self._export_worker = worker
        self.export_btn.setEnabled(False)
        self.export_btn.setText(f"{label}…")
        self.cancel_btn.setVisible(True)
        self.overall_bar.setValue(0)
        self.status_label.setText(f"{label}…")
        thread.start()

    def _on_extract_current_frame(self) -> None:
        if not self._media_info or not self._current_file:
            return
        ts = self.player.position() / 1000.0
        self._extract_frame_at(ts)

    def _on_extract_marker_first_frame(self) -> None:
        idx = self._selected_marker_index()
        if idx < 0:
            QMessageBox.information(
                self, "No marker selected",
                "Select a marker in the Markers list, then use this action.")
            return
        reg = self._regions[idx]
        self._extract_frame_at(reg.start, suffix=f"{reg.name or 'clip'}_first")

    def _on_extract_marker_last_frame(self) -> None:
        idx = self._selected_marker_index()
        if idx < 0:
            QMessageBox.information(
                self, "No marker selected",
                "Select a marker in the Markers list, then use this action.")
            return
        reg = self._regions[idx]
        frame_period = self._media_info.frame_period() if self._media_info else 0.04
        t = max(reg.start, reg.end - frame_period)
        self._extract_frame_at(t, suffix=f"{reg.name or 'clip'}_last")

    def _on_extract_all_frames(self) -> None:
        if not self._media_info or not self._current_file:
            return
        start, end = self._current_range()

        if self._settings.prompt_before_frame_export:
            dlg = AllFramesDialog(self._settings, self._current_file,
                                  start, end, self._media_info.fps, self)
            if dlg.exec() != AllFramesDialog.DialogCode.Accepted:
                return
            sel = dlg.selection()
        else:
            folder = self._settings.frame_output_dir or os.path.join(
                os.path.dirname(self._current_file),
                os.path.splitext(os.path.basename(self._current_file))[0] + "_frames"
            )
            sel = {
                "format": self._settings.frame_format,
                "quality": self._settings.frame_jpg_quality,
                "fps": self._settings.frame_all_fps,
                "range": self._settings.frame_range,
                "output_dir": folder,
                "prefix": "frame",
            }

        out_dir = sel["output_dir"]
        os.makedirs(out_dir, exist_ok=True)
        if sel["range"] == "full":
            r_start, r_end = 0.0, self._media_info.duration_seconds
        else:
            r_start, r_end = start, end

        job = fx.AllFramesJob(
            input_path=self._current_file,
            output_dir=out_dir,
            start_seconds=r_start,
            end_seconds=r_end,
            fps=(sel["fps"] if sel["fps"] and sel["fps"] > 0 else None),
            fmt=sel["format"],
            prefix=sel["prefix"],
            quality=int(sel["quality"]),
        )
        cmd = fx.build_all_frames_cmd(job)

        self._settings.frame_format = sel["format"]
        self._settings.frame_jpg_quality = int(sel["quality"])
        self._settings.frame_all_fps = float(sel["fps"] or 0.0)
        self._settings.frame_range = sel["range"]
        self._settings.frame_output_dir = out_dir
        app_settings.save(self._settings)

        self._run_ffmpeg_async(cmd, total_duration=max(0.001, r_end - r_start),
                               output_path=out_dir, label="Extract frames")

    def _on_export_speed(self, in_seconds: float = None, out_seconds: float = None) -> None:
        """Speed-change export. If called with explicit start/end (from the
        marker menu), pre-fill the dialog with those; otherwise use current
        IN/OUT."""
        if not self._media_info or not self._current_file:
            return
        if not self._media_info.has_video:
            QMessageBox.warning(self, "No video",
                                "Speed change is only meaningful for video files.")
            return
        if in_seconds is None or out_seconds is None:
            in_seconds, out_seconds = self._current_range()

        dlg = SpeedExportDialog(self._settings, self._current_file,
                                in_seconds, out_seconds, self)
        if dlg.exec() != SpeedExportDialog.DialogCode.Accepted:
            return
        sel = dlg.selection()

        out = sel["output_path"]
        if not out:
            QMessageBox.warning(self, "Missing filename", "Choose a save path.")
            return
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

        if sel["range"] == "full":
            r_start, r_end = 0.0, self._media_info.duration_seconds
        else:
            r_start, r_end = in_seconds, out_seconds

        speed = float(sel["speed"])
        if speed <= 0:
            QMessageBox.warning(self, "Invalid speed", "Speed must be a positive number.")
            return

        # Persist a couple of defaults so re-opening the dialog remembers them.
        fmt_key = sel["output_format"]
        self._settings.output_format_video = fmt_key
        app_settings.save(self._settings)

        engine = sel.get("engine", "off")
        crf_val = int(self.video_quality_combo.currentData()) if isinstance(self.video_quality_combo.currentData(), int) else 18
        aud_bitrate = self.audio_bitrate_combo.currentText() or "192k"

        # ---- AI (RIFE) path: three-stage pipeline on its own worker thread ----
        if engine == "rife":
            if not rife_setup.is_installed():
                QMessageBox.warning(
                    self, "RIFE not installed",
                    "Install RIFE from the Speed dialog first (click the "
                    "'Install RIFE' button in the Motion smoothing section)."
                )
                return
            self._start_rife_pipeline(
                out_path=out,
                start=r_start, end=r_end,
                speed=speed,
                keep_audio=bool(sel["keep_audio"]),
                target_fps=int(sel["interpolate_fps"]),
                crf=crf_val,
                audio_bitrate=aud_bitrate,
            )
            return

        # ---- Classical / off: single-shot ffmpeg command ----
        job = sx.SpeedJob(
            input_path=self._current_file,
            output_path=out,
            start_seconds=r_start,
            end_seconds=r_end,
            speed=speed,
            interpolate=(engine == "minterpolate"),
            interpolate_fps=int(sel["interpolate_fps"]),
            keep_audio=bool(sel["keep_audio"]),
            video_crf=crf_val,
            audio_bitrate=aud_bitrate,
            output_format=fmt_key,
        )
        cmd = sx.build_speed_cmd(job)

        # Expected output duration: source_span / speed. Progress bar tracks
        # the OUTPUT duration since ffmpeg's -progress reports output PTS.
        src_span = max(0.001, r_end - r_start)
        out_span = src_span / speed
        if engine == "minterpolate":
            label = "Slow-mo (classical)" if speed < 1 else "Speed change (smooth)"
        else:
            label = "Slow-mo" if speed < 1 else "Speed change"
        self._run_ffmpeg_async(cmd, total_duration=out_span,
                               output_path=out, label=label)

    def _start_rife_pipeline(self, *, out_path: str, start: float, end: float,
                             speed: float, keep_audio: bool, target_fps: int,
                             crf: int, audio_bitrate: str) -> None:
        """Kick off the three-stage RIFE pipeline in a QThread with progress."""
        from PySide6.QtCore import QObject, QThread as _QThread, Signal as _Signal

        job = rp.RifeSpeedJob(
            input_path=self._current_file,
            output_path=out_path,
            start_seconds=start,
            end_seconds=end,
            speed=speed,
            keep_audio=keep_audio,
            target_fps=target_fps,
            source_fps=self._media_info.fps if self._media_info else None,
            video_crf=crf,
            audio_bitrate=audio_bitrate,
        )

        class _Worker(QObject):
            progress = _Signal(float)
            status   = _Signal(str)
            done_ok  = _Signal(str)
            failed   = _Signal(str)
            def __init__(self, job):
                super().__init__()
                self._job = job
                self._cancel = False
            def cancel(self): self._cancel = True
            def run(self):
                ok, msg = rp.run_rife_pipeline(
                    self._job,
                    on_progress=lambda f: self.progress.emit(f),
                    on_status=lambda s: self.status.emit(s),
                    should_cancel=lambda: self._cancel,
                )
                if ok:
                    self.done_ok.emit(self._job.output_path)
                else:
                    self.failed.emit(msg)

        thread = _QThread()
        worker = _Worker(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def on_prog(f: float):
            self.overall_bar.setValue(int(f * 1000))

        def on_status(s: str):
            self.status_label.setText(f"AI slo-mo: {s}")

        def on_ok(path: str):
            self.overall_bar.setValue(1000)
            self.status_label.setText(f"AI slo-mo complete ✓  ·  {path}")
            self.export_btn.setEnabled(True); self.export_btn.setText("Export")
            self.cancel_btn.setVisible(False)
            self._export_thread = None; self._export_worker = None
            if self.open_folder_cb.isChecked():
                self._open_folder(os.path.dirname(path) or path)
            thread.quit()

        def on_fail(msg: str):
            self.export_btn.setEnabled(True); self.export_btn.setText("Export")
            self.cancel_btn.setVisible(False)
            self._export_thread = None; self._export_worker = None
            QMessageBox.critical(self, "AI slo-mo failed", msg)
            self.status_label.setText("AI slo-mo failed.")
            thread.quit()

        worker.progress.connect(on_prog)
        worker.status.connect(on_status)
        worker.done_ok.connect(on_ok)
        worker.failed.connect(on_fail)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._export_thread = thread
        self._export_worker = worker
        self.export_btn.setEnabled(False)
        self.export_btn.setText("AI slo-mo…")
        self.cancel_btn.setVisible(True)
        self.overall_bar.setValue(0)
        self.status_label.setText("AI slo-mo: starting…")
        thread.start()

    def _on_export_upscale(self) -> None:
        if not self._media_info or not self._current_file:
            return
        if not self._media_info.has_video:
            QMessageBox.warning(self, "No video",
                                "Upscaling needs a video source.")
            return
        in_s, out_s = self._current_range()

        from upscale_dialog import UpscaleDialog
        import seedvr2_pipeline as up

        dlg = UpscaleDialog(
            self._settings, self._current_file,
            source_w=self._media_info.width or 1920,
            source_h=self._media_info.height or 1080,
            in_seconds=in_s, out_seconds=out_s, parent=self,
        )
        if dlg.exec() != UpscaleDialog.DialogCode.Accepted:
            return
        sel = dlg.selection()
        out = sel["output_path"]
        if not out:
            QMessageBox.warning(self, "Missing filename", "Choose a save path.")
            return
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

        if sel["range"] == "full":
            r_start, r_end = 0.0, self._media_info.duration_seconds
        else:
            r_start, r_end = in_s, out_s

        job = up.UpscaleJob(
            input_path=self._current_file,
            output_path=out,
            start_seconds=r_start,
            end_seconds=r_end,
            target_w=int(sel["target_w"]),
            target_h=int(sel["target_h"]),
            model_kind=sel["model_kind"],
            keep_audio=bool(sel["keep_audio"]),
            source_fps=self._media_info.fps,
            video_crf=int(self.video_quality_combo.currentData()) if isinstance(self.video_quality_combo.currentData(), int) else 18,
            audio_bitrate=self.audio_bitrate_combo.currentText() or "192k",
        )
        self._start_upscale_pipeline(job)

    def _start_upscale_pipeline(self, job) -> None:
        """Run seedvr2_pipeline.run_upscale on its own QThread."""
        from PySide6.QtCore import QObject, QThread as _QThread, Signal as _Signal
        import seedvr2_pipeline as up

        class _Worker(QObject):
            progress = _Signal(float)
            status   = _Signal(str)
            done_ok  = _Signal(str)
            failed   = _Signal(str)
            def __init__(self, job):
                super().__init__()
                self._job = job
                self._cancel = False
            def cancel(self): self._cancel = True
            def run(self):
                ok, msg = up.run_upscale(
                    self._job,
                    on_progress=lambda f: self.progress.emit(f),
                    on_status=lambda s: self.status.emit(s),
                    should_cancel=lambda: self._cancel,
                )
                if ok:
                    self.done_ok.emit(self._job.output_path)
                else:
                    self.failed.emit(msg)

        thread = _QThread()
        worker = _Worker(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def on_prog(f: float):
            self.overall_bar.setValue(int(f * 1000))

        def on_status(s: str):
            self.status_label.setText(f"Upscale: {s}")

        def on_ok(path: str):
            self.overall_bar.setValue(1000)
            self.status_label.setText(f"Upscale complete ✓  ·  {path}")
            self.export_btn.setEnabled(True); self.export_btn.setText("Export")
            self.cancel_btn.setVisible(False)
            self._export_thread = None; self._export_worker = None
            if self.open_folder_cb.isChecked():
                self._open_folder(os.path.dirname(path) or path)
            thread.quit()

        def on_fail(msg: str):
            self.export_btn.setEnabled(True); self.export_btn.setText("Export")
            self.cancel_btn.setVisible(False)
            self._export_thread = None; self._export_worker = None
            QMessageBox.critical(self, "Upscale failed", msg)
            self.status_label.setText("Upscale failed.")
            thread.quit()

        worker.progress.connect(on_prog)
        worker.status.connect(on_status)
        worker.done_ok.connect(on_ok)
        worker.failed.connect(on_fail)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._export_thread = thread
        self._export_worker = worker
        self.export_btn.setEnabled(False)
        self.export_btn.setText("Upscaling…")
        self.cancel_btn.setVisible(True)
        self.overall_bar.setValue(0)
        self.status_label.setText("Upscale: starting…")
        thread.start()

    def _on_export_gif(self) -> None:
        if not self._media_info or not self._current_file:
            return
        if not self._media_info.has_video:
            QMessageBox.warning(self, "No video",
                                "GIF export needs a video source (this file is audio-only).")
            return
        start, end = self._current_range()

        if self._settings.prompt_before_gif_export:
            dlg = GifExportDialog(self._settings, self._current_file, start, end, self)
            if dlg.exec() != GifExportDialog.DialogCode.Accepted:
                return
            sel = dlg.selection()
        else:
            folder = self._settings.gif_output_dir or os.path.dirname(self._current_file)
            base = os.path.splitext(os.path.basename(self._current_file))[0]
            sel = {
                "fps": self._settings.gif_fps,
                "width": self._settings.gif_width,
                "dither": self._settings.gif_dither,
                "loop_forever": self._settings.gif_loop_forever,
                "range": "in_out",
                "output_path": os.path.join(folder, f"{base}.gif"),
            }

        out = sel["output_path"]
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        if sel["range"] == "full":
            r_start, r_end = 0.0, self._media_info.duration_seconds
        else:
            r_start, r_end = start, end

        job = fx.GifJob(
            input_path=self._current_file,
            output_path=out,
            start_seconds=r_start,
            end_seconds=r_end,
            fps=int(sel["fps"]),
            width=int(sel["width"]),
            dither=sel["dither"],
            loop_forever=bool(sel["loop_forever"]),
        )
        cmd = fx.build_gif_cmd(job)

        self._settings.gif_fps = int(sel["fps"])
        self._settings.gif_width = int(sel["width"])
        self._settings.gif_dither = sel["dither"]
        self._settings.gif_loop_forever = bool(sel["loop_forever"])
        self._settings.gif_output_dir = os.path.dirname(out)
        app_settings.save(self._settings)

        self._run_ffmpeg_async(cmd, total_duration=max(0.001, r_end - r_start),
                               output_path=out, label="Export GIF")

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
        s.click_free_cuts = self.click_free_cb.isChecked()
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

            # Queue advance takes precedence over 'close after export': we only
            # close when the whole queue is finished.
            if self._queue and self._queue_index >= 0:
                self._queue_completed.add(self._queue_index)
                self._refresh_queue_ui()
                more_to_go = self._queue_index < len(self._queue) - 1
                if more_to_go and self.queue_auto_cb.isChecked():
                    self.status_label.setText(
                        f"Export complete ✓  ·  advancing to {self._queue_index + 2}/{len(self._queue)}...")
                    QTimer.singleShot(600, lambda: self._queue_step(+1))
                    return
                if more_to_go:
                    self.status_label.setText(
                        f"Export complete ✓  ·  {len(self._queue) - self._queue_index - 1} file(s) still queued")
                    return

            # Belt-and-braces: check both isChecked() and checkState() so a
            # third-party stylesheet or programmatic toggle can't spoof us into
            # closing on an unchecked box. Also log the decision path.
            fully_checked = (
                self.close_after_cb.isChecked()
                and self.close_after_cb.checkState() == Qt.CheckState.Checked
            )
            try:
                import datetime, os as _os
                p = _os.path.join(_os.environ.get("TEMP", "."), "autosplit.log")
                with open(p, "a", encoding="utf-8") as f:
                    f.write(
                        f"{datetime.datetime.now().isoformat(timespec='milliseconds')}  "
                        f"export-finished  close_after_cb.isChecked={self.close_after_cb.isChecked()}  "
                        f"checkState={self.close_after_cb.checkState()}  "
                        f"decision={'CLOSE' if fully_checked else 'STAY'}\n"
                    )
            except Exception:
                pass
            if fully_checked:
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
            if QMessageBox.question(
                    self, "Export in progress",
                    "An export is still running.\n\nCancel it and close AutoSplit?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            self._export_worker.cancel()
        self._cancel_waveform()
        if self._collector is not None:
            try:
                self._collector.stop()
            except Exception:
                pass
        try:
            self._save_current_settings(self.folder_edit.text().strip())
        except Exception:
            pass
        super().closeEvent(ev)
