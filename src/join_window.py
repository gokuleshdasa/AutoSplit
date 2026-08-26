"""AutoSplit Joiner window.

- Drag-reorder list of clips with thumbnails, name, duration
- Compatibility badge (fast join available vs re-encode required)
- Selected-clip preview + Play joined (sequential playback)
- Format / bitrate / quality / folder / filename
- Export with per-file progress
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QUrl, QSize, QTimer, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap, QCloseEvent, QAction, QColor, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListView,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSizePolicy, QSplitter, QStatusBar, QStyle, QVBoxLayout,
    QWidget,
)

import ffmpeg_utils as fu
import join_utils as ju
import settings as app_settings
from async_tasks import (
    submit_probe, submit_thumbnail, submit_waveform, TaskSignals,
)
from timeline import Timeline


THUMB_W, THUMB_H = 160, 90


def fmt_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds - (h * 3600 + m * 60))
    return f"{h:02d}:{m:02d}:{s:02d}"


@dataclass
class Clip:
    path: str
    info: Optional[fu.MediaInfo] = None
    thumb: Optional[QPixmap] = None
    error: str = ""

    @property
    def display_name(self) -> str:
        return os.path.basename(self.path)


STYLE = """
QWidget { background: #202020; color: #e8e8e8; font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif; font-size: 10pt; }
QMainWindow, QWidget#Central { background: #202020; }
QGroupBox { border: 1px solid #333; border-radius: 8px; margin-top: 14px; padding: 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; color: #b0d0f0; }
QLineEdit, QComboBox {
    background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 4px; padding: 6px 8px;
    selection-background-color: #0078d4;
}
QLineEdit:focus, QComboBox:focus { border: 1px solid #0078d4; }
QPushButton { background: #2f2f2f; border: 1px solid #3a3a3a; border-radius: 4px; padding: 6px 14px; }
QPushButton:hover { background: #3a3a3a; }
QPushButton:pressed { background: #262626; }
QPushButton#Primary { background: #0078d4; border: 1px solid #0078d4; color: white; font-weight: 600; padding: 8px 20px; }
QPushButton#Primary:hover { background: #106ebe; }
QPushButton#Primary:disabled { background: #444; color: #888; border-color: #444; }
QPushButton#Danger { background: #a4262c; border-color: #a4262c; color: white; }
QCheckBox { spacing: 8px; }
QProgressBar { background: #2a2a2a; border: 1px solid #3a3a3a; border-radius: 4px; text-align: center; padding: 1px; }
QProgressBar::chunk { background: #0078d4; border-radius: 3px; }
QLabel#Muted { color: #a0a0a0; }
QLabel#Title { font-size: 16pt; font-weight: 600; color: #ffffff; }
QLabel#GoodBadge { color: #00b294; font-weight: 600; }
QLabel#WarnBadge { color: #ffb400; font-weight: 600; }
QSplitter::handle { background: #2a2a2a; }
QStatusBar { background: #1a1a1a; border-top: 1px solid #2a2a2a; }
QListWidget { background: #1a1a1a; border: 1px solid #2f2f2f; border-radius: 4px; padding: 2px; }
QListWidget::item { padding: 8px; border-radius: 4px; margin: 2px; }
QListWidget::item:hover { background: #262626; }
QListWidget::item:selected { background: #0a3a63; }
"""


class ClipsList(QListWidget):
    """Drag-reorder-able list. Emits `order_changed` after a drop."""
    order_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setMovement(QListView.Movement.Snap)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setIconSize(QSize(THUMB_W, THUMB_H))
        self.setUniformItemSizes(False)
        self.setSpacing(2)

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self.order_changed.emit()


class JoinExportWorker(QThread):
    progress = Signal(float)             # 0..1
    finished_ok = Signal(str)            # output path
    failed = Signal(str)                 # error message

    def __init__(self, cmd: list[str], total_duration: float,
                 output_path: str, listfile_to_delete: Optional[str] = None,
                 parent=None):
        super().__init__(parent)
        self._cmd = cmd
        self._total = total_duration
        self._out = output_path
        self._listfile = listfile_to_delete
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
            self.finished_ok.emit(self._out)
        else:
            self.failed.emit(f"ffmpeg exited with {rc}\n\n{tail[-1500:]}")


class JoinWindow(QMainWindow):
    def __init__(self, initial_paths: Optional[List[str]] = None,
                 collector=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AutoSplit — Media Joiner")
        self.resize(1240, 780)
        self.setStyleSheet(STYLE)
        self.setAcceptDrops(True)

        self._settings = app_settings.load()
        self._clips: List[Clip] = []
        # Keep TaskSignals alive until they fire — otherwise Python GC drops them.
        self._pending_probes: dict[str, TaskSignals] = {}
        self._pending_thumbs: dict[str, TaskSignals] = {}
        self._pending_waves: dict[str, TaskSignals] = {}
        self._current_wave_path: Optional[str] = None
        # Debounce selection so rapid clicking doesn't spawn a queue of ffmpeg jobs.
        self._sel_debounce = QTimer(self)
        self._sel_debounce.setSingleShot(True)
        self._sel_debounce.setInterval(150)
        self._sel_debounce.timeout.connect(self._apply_pending_selection)
        self._pending_selection_row: int = -1
        self._export_thread: Optional[JoinExportWorker] = None
        self._joined_playback: Optional[List[str]] = None
        self._joined_index = 0
        self._collector = collector    # keep-alive during "collecting" state

        self._build_ui()

        if collector is not None:
            self.status_label.setText("Collecting selected files…")
            collector.added.connect(self._on_collector_added)
            collector.ready.connect(self._on_collector_ready)

        if initial_paths:
            for p in initial_paths:
                self._add_clip(p)

    # --- UI ----------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(objectName="Central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Header row
        header = QHBoxLayout()
        title = QLabel("Media Joiner", objectName="Title")
        header.addWidget(title)
        header.addStretch(1)

        self.add_btn = QPushButton("Add files…")
        self.add_btn.clicked.connect(self._on_add_files)
        header.addWidget(self.add_btn)

        self.sort_btn = QPushButton("Sort by name")
        self.sort_btn.clicked.connect(self._on_sort)
        header.addWidget(self.sort_btn)

        self.reverse_btn = QPushButton("Reverse")
        self.reverse_btn.clicked.connect(self._on_reverse)
        header.addWidget(self.reverse_btn)

        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.clicked.connect(self._on_remove_selected)
        header.addWidget(self.remove_btn)

        root.addLayout(header)

        # Main split — LEFT: big preview + waveform (splitter style)
        #                RIGHT: clip thumbnails + output settings
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        root.addWidget(split, 1)

        # ---- LEFT: big video preview + waveform timeline ------------------
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(8)

        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background:#000;")
        self.video_widget.setMinimumHeight(320)
        left_l.addWidget(self.video_widget, 1)

        # Player controls
        prow = QHBoxLayout()
        self.play_selected_btn = QPushButton()
        self.play_selected_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_selected_btn.setFixedWidth(46)
        self.play_selected_btn.setToolTip("Play the highlighted clip")
        self.play_selected_btn.clicked.connect(self._on_play_selected)
        prow.addWidget(self.play_selected_btn)

        self.stop_btn = QPushButton()
        self.stop_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_btn.setFixedWidth(46)
        self.stop_btn.clicked.connect(self._on_stop)
        prow.addWidget(self.stop_btn)

        self.play_joined_btn = QPushButton("▶ Play joined preview")
        self.play_joined_btn.setToolTip("Plays clips back-to-back — small gap at cuts (preview only).")
        self.play_joined_btn.clicked.connect(self._on_play_joined)
        prow.addWidget(self.play_joined_btn)

        prow.addStretch(1)
        self.time_label = QLabel("00:00:00 / 00:00:00", objectName="Muted")
        prow.addWidget(self.time_label)
        left_l.addLayout(prow)

        # Timeline with waveform (simple mode — no IN/OUT markers)
        self.preview_timeline = Timeline()
        self.preview_timeline.set_simple_mode(True)
        self.preview_timeline.seek_requested.connect(self._on_preview_seek)
        self.preview_timeline.scrubbing.connect(self._on_preview_seek)
        left_l.addWidget(self.preview_timeline)

        hint = QLabel(
            "Left-drag the timeline to scrub. Click a clip on the right to preview it.",
            objectName="Muted")
        hint.setWordWrap(True)
        left_l.addWidget(hint)

        self.badge_label = QLabel("—", objectName="Muted")
        left_l.addWidget(self.badge_label)

        split.addWidget(left)

        # ---- RIGHT: clip thumbnails + output settings ---------------------
        right = QWidget()
        right.setMinimumWidth(440)
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(10)

        list_group = QGroupBox("Clips  (drag to reorder)")
        list_lay = QVBoxLayout(list_group)
        self.list = ClipsList()
        self.list.currentRowChanged.connect(self._on_list_selection)
        self.list.order_changed.connect(self._on_order_changed)
        list_lay.addWidget(self.list, 1)
        right_l.addWidget(list_group, 1)

        right_l.addWidget(self._build_output_group())

        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([760, 500])

        # Bottom: export + progress
        bot = QFrame()
        bot.setFrameShape(QFrame.Shape.StyledPanel)
        bl = QVBoxLayout(bot)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(6)

        erow = QHBoxLayout()
        self.status_label = QLabel("Ready", objectName="Muted")
        erow.addWidget(self.status_label, 1)
        self.cancel_btn = QPushButton("Cancel", objectName="Danger")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._on_cancel_export)
        erow.addWidget(self.cancel_btn)
        self.export_btn = QPushButton("Join and Export", objectName="Primary")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._on_export)
        erow.addWidget(self.export_btn)
        bl.addLayout(erow)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        bl.addWidget(self.progress)
        root.addWidget(bot)

        self.setStatusBar(QStatusBar())

        # Paste shortcut: Ctrl+V pulls file URLs from the clipboard.
        paste_sc = QShortcut(QKeySequence.StandardKey.Paste, self)
        paste_sc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        paste_sc.activated.connect(self._on_paste)

        # Media player
        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.setVideoOutput(self.video_widget)
        self.player.mediaStatusChanged.connect(self._on_player_status)
        self.player.errorOccurred.connect(self._on_player_error)
        # Poll playback position for the preview timeline.
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(80)
        self._pos_timer.timeout.connect(self._sync_preview_position)
        self._pos_timer.start()
        self._playing_clip_path: Optional[str] = None

    def _build_output_group(self) -> QGroupBox:
        gb = QGroupBox("Output")
        g = QGridLayout(gb)
        g.setVerticalSpacing(8)

        g.addWidget(QLabel("Format:"), 0, 0)
        self.format_combo = QComboBox()
        # Populated once we know if the batch is video or audio.
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
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

        self.force_reencode_cb = QCheckBox("Force re-encode (ignore fast-join even if compatible)")
        self.force_reencode_cb.setChecked(self._settings.join_always_reencode)
        self.force_reencode_cb.toggled.connect(self._refresh_badge)
        g.addWidget(self.force_reencode_cb, 3, 0, 1, 2)

        g.addWidget(QLabel("Folder:"), 4, 0)
        row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        default_dir = self._settings.join_output_dir or self._settings.last_export_dir
        self.folder_edit.setText(default_dir)
        row.addWidget(self.folder_edit, 1)
        pick = QPushButton("Browse…")
        pick.clicked.connect(self._on_pick_folder)
        row.addWidget(pick)
        w = QWidget()
        w.setLayout(row)
        g.addWidget(w, 4, 1)

        g.addWidget(QLabel("Filename:"), 5, 0)
        self.filename_edit = QLineEdit()
        self.filename_edit.setPlaceholderText("joined  (extension added automatically)")
        self.filename_edit.setText("joined")
        g.addWidget(self.filename_edit, 5, 1)

        return gb

    # --- collector -------------------------------------------------
    def _on_collector_added(self, path: str) -> None:
        self._add_clip(path)

    def _on_collector_ready(self, paths: list) -> None:
        # Auto-sort by natural name once the batch is complete.
        self._on_sort()
        self.status_label.setText(f"Loaded {len(self._clips)} clip(s)")
        # We can let the collector go now.
        if self._collector is not None:
            self._collector.stop()
            self._collector = None

    # --- drag and drop into window ---------------------------------
    def dragEnterEvent(self, ev) -> None:
        if self._mime_has_media(ev.mimeData()):
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        added = self._add_paths_from_mime(ev.mimeData())
        if added:
            self.statusBar().showMessage(f"Added {added} file(s).", 3000)

    # --- clipboard paste (Ctrl+V) ----------------------------------
    def _on_paste(self) -> None:
        md = QApplication.clipboard().mimeData()
        added = self._add_paths_from_mime(md)
        if added:
            self.statusBar().showMessage(f"Pasted {added} file(s) from clipboard.", 3000)
        else:
            self.statusBar().showMessage("Clipboard has no media files.", 2500)

    # --- shared helpers for drop and paste -------------------------
    def _mime_has_media(self, md) -> bool:
        if md is None:
            return False
        if md.hasUrls():
            for u in md.urls():
                if u.isLocalFile() and fu.is_supported_media(u.toLocalFile()):
                    return True
        if md.hasText():
            for line in md.text().splitlines():
                p = line.strip().strip('"').strip("'")
                if p and os.path.exists(p) and fu.is_supported_media(p):
                    return True
        return False

    def _add_paths_from_mime(self, md) -> int:
        """Add every supported media file found in the given mime data.
        Returns count of files added."""
        if md is None:
            return 0
        added = 0
        seen: set[str] = set()

        if md.hasUrls():
            for u in md.urls():
                if not u.isLocalFile():
                    continue
                p = os.path.abspath(u.toLocalFile())
                if p in seen or not fu.is_supported_media(p):
                    continue
                seen.add(p)
                before = len(self._clips)
                self._add_clip(p)
                if len(self._clips) > before:
                    added += 1

        # Some copy sources (e.g. paths pasted from a terminal) come as text lines.
        if md.hasText():
            for line in md.text().splitlines():
                p = line.strip().strip('"').strip("'")
                if not p:
                    continue
                p = os.path.abspath(p)
                if p in seen or not os.path.exists(p) or not fu.is_supported_media(p):
                    continue
                seen.add(p)
                before = len(self._clips)
                self._add_clip(p)
                if len(self._clips) > before:
                    added += 1

        return added

    # --- clip management -------------------------------------------
    def _on_add_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(fu.VIDEO_EXTS | fu.AUDIO_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add media files", self._settings.last_export_dir or "",
            f"Media ({exts});;All files (*.*)"
        )
        for p in paths:
            self._add_clip(p)

    def _add_clip(self, path: str) -> None:
        """Non-blocking add: row appears instantly; probe + thumbnail run on
        the QThreadPool. Format dropdown updates immediately from file extensions."""
        if not path or not os.path.exists(path):
            return
        path = os.path.abspath(path)
        for c in self._clips:
            if c.path == path:
                return

        clip = Clip(path=path)
        self._clips.append(clip)

        item = QListWidgetItem(self._item_text(clip))
        item.setSizeHint(QSize(0, THUMB_H + 12))
        item.setData(Qt.ItemDataRole.UserRole, clip.path)
        item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.list.addItem(item)

        # Async probe first. The thumbnail is submitted only after the probe
        # returns real duration/has_video so we never seek past the end of a
        # short clip (which would silently produce empty output).
        self._pending_probes[path] = submit_probe(
            path,
            on_ready=self._on_probe_ready,
            on_failed=self._on_probe_failed,
        )
        self._refresh_after_change()

    def _find_clip_index(self, path: str) -> int:
        for i, c in enumerate(self._clips):
            if c.path == path:
                return i
        return -1

    def _on_probe_ready(self, path: str, info) -> None:
        self._pending_probes.pop(path, None)
        idx = self._find_clip_index(path)
        if idx < 0:
            return
        clip = self._clips[idx]
        clip.info = info
        item = self.list.item(idx)
        if item is not None:
            item.setText(self._item_text(clip))
        # Now we know real duration + has_video, submit the thumbnail job.
        if clip.thumb is None and info.duration_seconds > 0:
            self._pending_thumbs[path] = submit_thumbnail(
                path, info.duration_seconds, info.has_video,
                THUMB_W, THUMB_H,
                on_ready=self._on_thumb_ready, on_failed=self._on_thumb_failed,
            )
        self._refresh_after_change()

    def _on_probe_failed(self, path: str, msg: str) -> None:
        self._pending_probes.pop(path, None)
        idx = self._find_clip_index(path)
        if idx < 0:
            return
        clip = self._clips[idx]
        clip.error = msg
        item = self.list.item(idx)
        if item is not None:
            item.setText(f"{clip.display_name}\n(unreadable: {msg[:80]})")
        self._refresh_after_change()

    def _on_thumb_ready(self, path: str, pixmap) -> None:
        self._pending_thumbs.pop(path, None)
        idx = self._find_clip_index(path)
        if idx < 0:
            return
        clip = self._clips[idx]
        clip.thumb = pixmap
        item = self.list.item(idx)
        if item is not None:
            item.setIcon(QIcon(pixmap))

    def _on_thumb_failed(self, path: str, msg: str) -> None:
        self._pending_thumbs.pop(path, None)
        # Silent — the row keeps its placeholder icon.

    def _item_text(self, clip: Clip) -> str:
        info = clip.info
        if info is None:
            if clip.error:
                return f"{clip.display_name}\n(unreadable: {clip.error[:80]})"
            return f"{clip.display_name}\nprobing…"
        parts = [clip.display_name, fmt_time(info.duration_seconds)]
        if info.has_video and info.width and info.height:
            parts.append(f"{info.width}×{info.height} {info.video_codec or ''}")
        if info.has_audio:
            parts.append(f"audio {info.audio_codec or ''}")
        return "   ·   ".join(parts)

    def _on_remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.list.selectedIndexes()}, reverse=True)
        for r in rows:
            self.list.takeItem(r)
            del self._clips[r]
        self._refresh_after_change()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.list.hasFocus():
            self._on_remove_selected()
            return
        super().keyPressEvent(ev)

    def _on_sort(self) -> None:
        ordered_paths = ju.natural_sort([c.path for c in self._clips])
        self._reorder_by_paths(ordered_paths)

    def _on_reverse(self) -> None:
        self._reorder_by_paths(list(reversed([c.path for c in self._clips])))

    def _reorder_by_paths(self, ordered_paths: list) -> None:
        by_path = {c.path: c for c in self._clips}
        self._clips = [by_path[p] for p in ordered_paths if p in by_path]
        self.list.blockSignals(True)
        self.list.clear()
        for c in self._clips:
            item = QListWidgetItem(self._item_text(c))
            item.setSizeHint(QSize(0, THUMB_H + 12))
            item.setData(Qt.ItemDataRole.UserRole, c.path)
            if c.thumb is not None:
                item.setIcon(QIcon(c.thumb))
            else:
                item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._refresh_after_change()

    def _on_order_changed(self) -> None:
        by_path = {c.path: c for c in self._clips}
        new_order: list[Clip] = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            path = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(path, str) and path in by_path and by_path[path] not in new_order:
                new_order.append(by_path[path])
        if len(new_order) == len(self._clips):
            self._clips = new_order
        self._refresh_after_change()

    def _extension_kinds(self) -> tuple[bool, bool]:
        """Guess (has_video, has_audio) from clip extensions — used to
        populate the format dropdown before probes finish."""
        has_v = has_a = False
        for c in self._clips:
            ext = os.path.splitext(c.path)[1].lower()
            if ext in fu.VIDEO_EXTS:
                has_v = True
            elif ext in fu.AUDIO_EXTS:
                has_a = True
        return has_v, has_a

    def _refresh_after_change(self) -> None:
        n = len(self._clips)
        # Populate format dropdown eagerly from extensions so it's usable
        # before probes complete.
        ext_v, ext_a = self._extension_kinds()
        self._on_format_populate(has_video=ext_v, has_audio=ext_a)
        # Enable Export only when all probes have completed successfully.
        probing = any(c.info is None and not c.error for c in self._clips)
        self.export_btn.setEnabled(n >= 1 and not probing)
        if probing:
            self.export_btn.setToolTip("Waiting for file inspection to finish…")
        else:
            self.export_btn.setToolTip("")
        self._refresh_badge()

    def _refresh_badge(self) -> None:
        infos = [c.info for c in self._clips if c.info is not None]
        n_total = len(self._clips)
        n_probed = len(infos)

        if n_total == 0:
            self.badge_label.setText("—")
            self.badge_label.setObjectName("Muted")
            self.badge_label.style().unpolish(self.badge_label)
            self.badge_label.style().polish(self.badge_label)
            return

        if n_probed < n_total:
            self.badge_label.setText(f"Reading files…  ({n_probed}/{n_total} ready)")
            self.badge_label.setObjectName("Muted")
            self.badge_label.style().unpolish(self.badge_label)
            self.badge_label.style().polish(self.badge_label)
            return

        compat = ju.check_compatibility(infos)
        total = sum(i.duration_seconds for i in infos)
        if self.force_reencode_cb.isChecked():
            self.badge_label.setText(f"Total: {fmt_time(total)}   ·   Re-encode (forced)")
            self.badge_label.setObjectName("Muted")
        elif compat.fast_ok and len(infos) > 1:
            self.badge_label.setText(f"✓ Fast join available   ·   Total: {fmt_time(total)}")
            self.badge_label.setObjectName("GoodBadge")
        elif compat.fast_ok and len(infos) == 1:
            self.badge_label.setText(f"Single clip   ·   Total: {fmt_time(total)}   ·   will re-encode")
            self.badge_label.setObjectName("Muted")
        else:
            self.badge_label.setText(f"⚠ Re-encode required   ·   {compat.reason}   ·   Total: {fmt_time(total)}")
            self.badge_label.setObjectName("WarnBadge")
        self.badge_label.style().unpolish(self.badge_label)
        self.badge_label.style().polish(self.badge_label)

    def _on_format_populate(self, *, has_video: bool, has_audio: bool) -> None:
        current_data = self.format_combo.currentData()
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        for key, meta in fu.FORMAT_CATALOG.items():
            if meta["kind"] == "video" and not has_video:
                continue
            if meta["kind"] == "audio" and not has_audio:
                continue
            self.format_combo.addItem(meta["label"], key)
        # Restore previous selection or fall back to settings default.
        default = current_data or (
            self._settings.join_output_format_video if has_video
            else self._settings.join_output_format_audio
        )
        idx = self.format_combo.findData(default)
        if idx >= 0:
            self.format_combo.setCurrentIndex(idx)
        self.format_combo.blockSignals(False)
        self._on_format_changed()

    def _on_format_changed(self, *_) -> None:
        key = self.format_combo.currentData()
        if not isinstance(key, str):
            return
        self.audio_bitrate_combo.setEnabled(key in fu.LOSSY_AUDIO_FORMATS)
        self.video_quality_combo.setEnabled(key in fu.LOSSY_VIDEO_FORMATS)

    # --- preview timeline & waveform ------------------------------
    def _on_preview_seek(self, seconds: float) -> None:
        self.player.setPosition(int(seconds * 1000))
        self.preview_timeline.set_position(seconds)

    def _sync_preview_position(self) -> None:
        if not self._playing_clip_path:
            return
        pos_s = self.player.position() / 1000.0
        self.preview_timeline.set_position(pos_s)
        dur_s = (self.player.duration() or 0) / 1000.0
        if dur_s <= 0 and self.preview_timeline.duration() > 0:
            dur_s = self.preview_timeline.duration()
        self.time_label.setText(f"{fmt_time(pos_s)} / {fmt_time(dur_s)}")

    def _start_preview_waveform(self, path: str) -> None:
        if self._current_wave_path == path:
            return
        self._current_wave_path = path
        self.preview_timeline.set_waveform(None)
        # Submit on the shared thread pool — cached results return instantly,
        # and stale results are ignored via the `_current_wave_path` check.
        self._pending_waves[path] = submit_waveform(
            path, w=2400, h=64,
            on_ready=self._on_wave_ready,
            on_failed=self._on_wave_failed,
        )

    def _on_wave_ready(self, path: str, pixmap) -> None:
        self._pending_waves.pop(path, None)
        if self._current_wave_path == path:
            self.preview_timeline.set_waveform(pixmap)

    def _on_wave_failed(self, path: str, msg: str) -> None:
        self._pending_waves.pop(path, None)
        if self._current_wave_path == path:
            self.statusBar().showMessage(f"Waveform: {msg[:80]}", 3000)

    # --- selection & playback --------------------------------------
    def _on_list_selection(self, row: int) -> None:
        self.play_selected_btn.setEnabled(0 <= row < len(self._clips))
        # Debounce so rapid clicking doesn't submit a waveform job per click.
        # Store the target row; the timer's slot (connected once at init) reads it.
        self._pending_selection_row = row
        self._sel_debounce.start()

    def _apply_pending_selection(self) -> None:
        self._apply_selection(self._pending_selection_row)

    def _apply_selection(self, row: int) -> None:
        if not (0 <= row < len(self._clips)):
            return
        clip = self._clips[row]
        if clip.info is not None:
            self.preview_timeline.set_duration(clip.info.duration_seconds)
            self._start_preview_waveform(clip.path)
        else:
            self.preview_timeline.set_duration(0.0)
            self.preview_timeline.set_waveform(None)

    def _current_selected_clip(self) -> Optional[Clip]:
        row = self.list.currentRow()
        if 0 <= row < len(self._clips):
            return self._clips[row]
        return None

    def _on_play_selected(self) -> None:
        clip = self._current_selected_clip()
        if clip is None:
            return
        self._joined_playback = None
        self._start_playing_clip(clip)

    def _on_play_joined(self) -> None:
        if not self._clips:
            return
        self._joined_playback = list(self._clips)
        self._joined_index = 0
        self._start_playing_clip(self._joined_playback[0])

    def _start_playing_clip(self, clip) -> None:
        # Full stop before switching source — Qt's WMF backend stutters and can
        # deadlock when setSource() runs while a previous stream is still active.
        self.player.stop()
        self.player.setSource(QUrl())   # release the old source cleanly
        self._playing_clip_path = clip.path
        if clip.info is not None:
            self.preview_timeline.set_duration(clip.info.duration_seconds)
            self._start_preview_waveform(clip.path)
        else:
            self.preview_timeline.set_duration(0.0)
            self.preview_timeline.set_waveform(None)
        self.player.setSource(QUrl.fromLocalFile(clip.path))
        self.player.play()

    def _on_stop(self) -> None:
        self.player.stop()
        self._joined_playback = None
        self._playing_clip_path = None

    def _on_player_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._joined_playback:
            self._joined_index += 1
            if self._joined_index < len(self._joined_playback):
                self._start_playing_clip(self._joined_playback[self._joined_index])
            else:
                self._joined_playback = None
                self._playing_clip_path = None

    def _on_player_error(self, _err, msg) -> None:
        # Surface playback errors visibly — a black video area is often just
        # a missing codec on this Windows install (typically HEVC / VP9).
        full = (msg or "Windows can't decode this file.").strip()
        self.statusBar().showMessage(f"Preview: {full}", 8000)
        self.badge_label.setText(f"⚠ Preview: {full[:80]}")
        self.badge_label.setObjectName("WarnBadge")
        self.badge_label.style().unpolish(self.badge_label)
        self.badge_label.style().polish(self.badge_label)

    # --- export ----------------------------------------------------
    def _on_pick_folder(self) -> None:
        current = self.folder_edit.text().strip() or self._settings.last_export_dir or ""
        d = QFileDialog.getExistingDirectory(self, "Choose output folder", current)
        if d:
            self.folder_edit.setText(d)

    def _current_format(self) -> str:
        data = self.format_combo.currentData()
        return data if isinstance(data, str) else "mp4_h264"

    def _on_export(self) -> None:
        if not self._clips:
            return
        # Any unreadable clips would break ffmpeg — refuse.
        for c in self._clips:
            if c.info is None or c.info.duration_seconds <= 0:
                QMessageBox.critical(
                    self, "Unreadable clip",
                    f"Cannot use:\n{c.path}\n\n{c.error or 'zero-duration or corrupt'}"
                )
                return

        folder = self.folder_edit.text().strip()
        if not folder:
            folder = str(Path(self._clips[0].path).parent)
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Cannot use folder", str(e))
            return

        base = self.filename_edit.text().strip() or "joined"
        fmt_key = self._current_format()
        ext = fu.format_extension(fmt_key)
        out_path = os.path.join(folder, base + ext)
        if os.path.exists(out_path):
            # Auto-increment.
            i = 2
            while True:
                candidate = os.path.join(folder, f"{base} ({i}){ext}")
                if not os.path.exists(candidate):
                    out_path = candidate
                    break
                i += 1

        infos = [c.info for c in self._clips]
        compat = ju.check_compatibility(infos)
        inputs = [c.path for c in self._clips]

        listfile: Optional[str] = None
        force_reencode = self.force_reencode_cb.isChecked() or not compat.fast_ok or len(inputs) < 2
        # Fast join requires the container to be able to hold the input codec.
        # Cheap check: input extension matches selected output ext.
        if not force_reencode:
            in_ext = os.path.splitext(inputs[0])[1].lower()
            if in_ext != ext.lower():
                force_reencode = True

        if not force_reencode:
            cmd, listfile = ju.build_fast_join_cmd(inputs, out_path)
            self.status_label.setText(f"Fast-joining {len(inputs)} clips…")
        else:
            crf_val = self.video_quality_combo.currentData()
            crf = crf_val if isinstance(crf_val, int) else 18
            cmd = ju.build_reencode_join_cmd(
                inputs, out_path,
                output_format=fmt_key,
                audio_bitrate=self.audio_bitrate_combo.currentText() or "192k",
                video_crf=crf,
                has_video=compat.common_has_video,
                has_audio=compat.common_has_audio,
            )
            self.status_label.setText(f"Re-encoding join of {len(inputs)} clips…")

        # Persist quick-use settings.
        self._settings.join_output_dir = folder
        self._settings.join_always_reencode = self.force_reencode_cb.isChecked()
        if compat.common_has_video:
            self._settings.join_output_format_video = fmt_key
        else:
            self._settings.join_output_format_audio = fmt_key
        app_settings.save(self._settings)

        total = sum(i.duration_seconds for i in infos)
        worker = JoinExportWorker(cmd, total_duration=total,
                                  output_path=out_path, listfile_to_delete=listfile,
                                  parent=self)
        worker.progress.connect(lambda f: self.progress.setValue(int(f * 1000)))
        worker.finished_ok.connect(self._on_export_ok)
        worker.failed.connect(self._on_export_failed)
        self._export_thread = worker

        self.export_btn.setEnabled(False)
        self.export_btn.setText("Joining…")
        self.cancel_btn.setVisible(True)
        self.progress.setValue(0)
        worker.start()

    def _on_export_ok(self, out_path: str) -> None:
        self.progress.setValue(1000)
        self.status_label.setText(f"Done: {out_path}")
        self.export_btn.setEnabled(True)
        self.export_btn.setText("Join and Export")
        self.cancel_btn.setVisible(False)
        self._export_thread = None
        # Open output folder for convenience.
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", out_path])
        except Exception:
            pass

    def _on_export_failed(self, msg: str) -> None:
        self.export_btn.setEnabled(True)
        self.export_btn.setText("Join and Export")
        self.cancel_btn.setVisible(False)
        self._export_thread = None
        QMessageBox.critical(self, "Join failed", msg)
        self.status_label.setText("Join failed.")

    def _on_cancel_export(self) -> None:
        if self._export_thread is not None:
            self._export_thread.cancel()
            self.status_label.setText("Cancelling…")

    def closeEvent(self, ev: QCloseEvent) -> None:
        if self._export_thread is not None:
            self._export_thread.cancel()
        if self._collector is not None:
            try:
                self._collector.stop()
            except Exception:
                pass
        super().closeEvent(ev)
