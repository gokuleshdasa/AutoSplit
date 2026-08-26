"""Custom timeline widget: playhead, start & end markers, segment ticks."""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QMouseEvent, QPaintEvent, QPixmap
from PySide6.QtWidgets import QWidget, QSizePolicy

from regions import Region


def _fmt(secs: float) -> str:
    if secs < 0:
        secs = 0
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = secs - (h * 3600 + m * 60)
    if h:
        return f"{h:d}:{m:02d}:{s:06.3f}"
    return f"{m:02d}:{s:06.3f}"


class Timeline(QWidget):
    """Interactive timeline.

    Interaction:
      - Left-drag: seek (playhead follows immediately).
      - Shift+left-drag or right-click-drag: set START marker.
      - Ctrl+left-drag or middle-click-drag: set END marker.
    """

    seek_requested = Signal(float)      # position_seconds — player should seek here
    scrubbing = Signal(float)           # while dragging, updates preview position
    start_changed = Signal(float)       # start_offset_seconds
    end_changed = Signal(float)         # end_offset_seconds (or -1 for "no end")
    context_requested = Signal(object, float, int)   # (global_pos QPoint, seconds_at_click, region_index_or_-1)
    range_selected = Signal(float, float)  # (start_seconds, end_seconds) — Alt-drag released

    _BG = QColor("#1e1e1e")
    _TRACK = QColor("#2d2d2d")
    _SEGMENT_BORDER = QColor("#106ebe")
    _RANGE_FILL = QColor(0, 120, 212, 55)
    _PLAYHEAD = QColor("#ffffff")
    _START = QColor("#00b294")
    _END = QColor("#ff8c00")
    _TEXT = QColor("#c8c8c8")
    _TEXT_DIM = QColor("#808080")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._duration = 0.0
        self._position = 0.0
        self._start = 0.0
        self._end = -1.0            # -1 => use duration
        self._split = 600.0
        self._dragging = False
        self._drag_mode: str = "seek"   # 'seek' | 'start' | 'end'
        self._waveform: Optional[QPixmap] = None
        self._regions: List[Region] = []
        self._show_split_ticks = True
        self._simple_mode = False   # True = hide IN/OUT markers and regions
        # Live drag-select overlay (Alt+drag).
        self._select_anchor: Optional[float] = None
        self._select_cursor: Optional[float] = None

    # --- public API ---------------------------------------------------------
    def set_duration(self, seconds: float) -> None:
        self._duration = max(0.0, seconds)
        self._position = min(self._position, self._duration)
        self._start = min(self._start, self._duration)
        if self._end > self._duration or self._end <= 0:
            self._end = -1.0
        self.update()

    def set_position(self, seconds: float) -> None:
        self._position = max(0.0, min(seconds, self._duration))
        self.update()

    def set_start(self, seconds: float, *, emit: bool = True) -> None:
        s = max(0.0, min(seconds, self._duration))
        self._start = s
        # If IN moves past OUT, clear OUT so the user can freely start a new range.
        cleared_end = False
        if self._end > 0 and s >= self._end:
            self._end = -1.0
            cleared_end = True
        self.update()
        if emit:
            self.start_changed.emit(self._start)
            if cleared_end:
                self.end_changed.emit(-1.0)

    def set_end(self, seconds: float, *, emit: bool = True) -> None:
        """Set end. Pass a value <= 0 to clear (use full duration)."""
        cleared_start = False
        if seconds is None or seconds <= 0:
            self._end = -1.0
        else:
            e = max(0.0, min(seconds, self._duration))
            self._end = e
            # If OUT moves before IN, drop IN back to 0 so the range is still valid.
            if e <= self._start:
                self._start = 0.0
                cleared_start = True
        self.update()
        if emit:
            self.end_changed.emit(self._end)
            if cleared_start:
                self.start_changed.emit(0.0)

    def set_split_duration(self, seconds: float) -> None:
        self._split = max(0.1, seconds)
        self.update()

    def set_waveform(self, pixmap: Optional[QPixmap]) -> None:
        self._waveform = pixmap
        self.update()

    def set_regions(self, regions: List[Region]) -> None:
        self._regions = list(regions)
        self.update()

    def set_show_split_ticks(self, show: bool) -> None:
        self._show_split_ticks = bool(show)
        self.update()

    def set_simple_mode(self, simple: bool) -> None:
        """Hide IN/OUT markers, region overlays, and split ticks. Just show
        waveform + playhead — useful when embedded in a preview area."""
        self._simple_mode = bool(simple)
        if simple:
            self._show_split_ticks = False
        self.update()

    def duration(self) -> float:
        return self._duration

    def start(self) -> float:
        return self._start

    def end(self) -> float:
        """Return effective end (duration if not set)."""
        return self._duration if self._end <= 0 else self._end

    def has_explicit_end(self) -> bool:
        return self._end > 0

    # --- geometry -----------------------------------------------------------
    def _track_rect(self) -> QRectF:
        m = 10
        # Track is a fat strip at the bottom; waveform fills it.
        top = self.height() - 74
        h = 62
        return QRectF(m, top, self.width() - 2 * m, h)

    def _x_for(self, seconds: float) -> float:
        r = self._track_rect()
        if self._duration <= 0:
            return r.left()
        frac = max(0.0, min(1.0, seconds / self._duration))
        return r.left() + frac * r.width()

    def _seconds_for(self, x: float) -> float:
        r = self._track_rect()
        if r.width() <= 0 or self._duration <= 0:
            return 0.0
        frac = (x - r.left()) / r.width()
        return max(0.0, min(1.0, frac)) * self._duration

    # --- helpers ------------------------------------------------------------
    def seconds_at_pixel(self, x: float) -> float:
        return self._seconds_for(x)

    def region_index_at_seconds(self, s: float) -> int:
        for i, reg in enumerate(self._regions):
            if reg.start <= s <= reg.end:
                return i
        return -1

    # --- events -------------------------------------------------------------
    def mousePressEvent(self, ev: QMouseEvent) -> None:
        if self._duration <= 0:
            return
        secs = self._seconds_for(ev.position().x())
        mods = ev.modifiers()

        # Right click → context menu (never a drag).
        if ev.button() == Qt.MouseButton.RightButton:
            idx = self.region_index_at_seconds(secs)
            self.context_requested.emit(ev.globalPosition().toPoint(), secs, idx)
            ev.accept()
            return

        # Alt+left-drag = drag-select a range (highlighted); on release, emits range_selected.
        if (ev.button() == Qt.MouseButton.LeftButton
                and mods & Qt.KeyboardModifier.AltModifier):
            self._drag_mode = "select"
            self._select_anchor = secs
            self._select_cursor = secs
            self._dragging = True
            self.update()
            ev.accept()
            return

        set_start = (ev.button() == Qt.MouseButton.LeftButton
                     and mods & Qt.KeyboardModifier.ShiftModifier)
        set_end = (
            ev.button() == Qt.MouseButton.MiddleButton
            or (ev.button() == Qt.MouseButton.LeftButton and mods & Qt.KeyboardModifier.ControlModifier)
        )
        if set_end:
            self._drag_mode = "end"
            self.set_end(secs)
        elif set_start:
            self._drag_mode = "start"
            self.set_start(secs)
        elif ev.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = "seek"
            self._position = secs
            self.seek_requested.emit(secs)
            self.scrubbing.emit(secs)
            self.update()
        else:
            return
        self._dragging = True
        ev.accept()

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:
        if not self._dragging or self._duration <= 0:
            return
        secs = self._seconds_for(ev.position().x())
        if self._drag_mode == "start":
            self.set_start(secs)
        elif self._drag_mode == "end":
            self.set_end(secs)
        elif self._drag_mode == "select":
            self._select_cursor = secs
            self.update()
        else:
            self._position = secs
            self.seek_requested.emit(secs)
            self.scrubbing.emit(secs)
            self.update()

    def mouseReleaseEvent(self, _ev: QMouseEvent) -> None:
        was_selecting = (self._drag_mode == "select"
                         and self._select_anchor is not None
                         and self._select_cursor is not None)
        self._dragging = False
        if was_selecting:
            a = min(self._select_anchor, self._select_cursor)
            b = max(self._select_anchor, self._select_cursor)
            self._select_anchor = None
            self._select_cursor = None
            self.update()
            if b - a >= 0.05:
                self.range_selected.emit(a, b)

    # --- paint --------------------------------------------------------------
    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self._BG)

        r = self._track_rect()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._TRACK)
        p.drawRoundedRect(r, 4, 4)

        if self._duration <= 0:
            p.setPen(self._TEXT_DIM)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Load a media file to see its timeline")
            return

        # Waveform image (stretched to fit the track).
        if self._waveform is not None and not self._waveform.isNull():
            p.save()
            p.setClipRect(r)
            target = QRectF(r.left(), r.top() + 4, r.width(), r.height() - 8)
            p.drawPixmap(target, self._waveform, QRectF(self._waveform.rect()))
            p.restore()

        # Named region overlays (skipped in simple mode).
        colors = [
            QColor(255, 140, 0, 90),   # orange
            QColor(0, 200, 140, 90),   # teal
            QColor(200, 100, 200, 90), # violet
            QColor(255, 209, 90, 90),  # amber
            QColor(120, 200, 255, 90), # sky
        ]
        if self._simple_mode:
            # Playhead only.
            px = self._x_for(self._position)
            p.setPen(QPen(self._PLAYHEAD, 2))
            p.drawLine(QPointF(px, r.top() - 4), QPointF(px, r.bottom() + 4))
            # Position / duration text.
            text_row = QRectF(4, 4, self.width() - 8, 20)
            p.setPen(self._TEXT)
            p.drawText(text_row,
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f" {_fmt(self._position)} / {_fmt(self._duration)}")
            return
        for idx, reg in enumerate(self._regions):
            xa = self._x_for(reg.start)
            xb = self._x_for(reg.end)
            rect = QRectF(xa, r.top(), max(1.0, xb - xa), r.height())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colors[idx % len(colors)])
            p.drawRoundedRect(rect, 3, 3)
            p.setPen(QColor(255, 255, 255, 220))
            label = reg.name or f"#{idx + 1}"
            p.drawText(rect.adjusted(4, 2, -4, -2),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, label)

        # Highlight active range [start .. end] (auto-split mode).
        eff_end = self.end()
        x0 = self._x_for(self._start)
        x1 = self._x_for(eff_end)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._RANGE_FILL)
        p.drawRoundedRect(QRectF(x0, r.top(), max(0.0, x1 - x0), r.height()), 3, 3)

        # Segment boundaries within range.
        if self._show_split_ticks:
            p.setPen(QPen(self._SEGMENT_BORDER, 1))
            for seg_start in self._segment_boundaries():
                x = self._x_for(seg_start)
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))

        # Start marker (green triangle above track).
        sx = self._x_for(self._start)
        p.setPen(QPen(self._START, 2))
        p.drawLine(QPointF(sx, r.top() - 10), QPointF(sx, r.bottom() + 4))
        p.setBrush(self._START)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon([QPointF(sx - 6, r.top() - 10), QPointF(sx + 6, r.top() - 10), QPointF(sx, r.top() - 2)])

        # End marker (orange triangle above track).
        ex = self._x_for(eff_end)
        p.setPen(QPen(self._END, 2, Qt.PenStyle.SolidLine if self.has_explicit_end() else Qt.PenStyle.DashLine))
        p.drawLine(QPointF(ex, r.top() - 10), QPointF(ex, r.bottom() + 4))
        p.setBrush(self._END)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon([QPointF(ex - 6, r.top() - 10), QPointF(ex + 6, r.top() - 10), QPointF(ex, r.top() - 2)])

        # Live drag-select overlay (Alt+drag).
        if self._select_anchor is not None and self._select_cursor is not None:
            a = min(self._select_anchor, self._select_cursor)
            b = max(self._select_anchor, self._select_cursor)
            xa = self._x_for(a); xb = self._x_for(b)
            sel = QRectF(xa, r.top() - 3, max(1.0, xb - xa), r.height() + 6)
            p.setPen(QPen(QColor(255, 255, 255, 180), 1, Qt.PenStyle.DashLine))
            p.setBrush(QColor(255, 255, 255, 60))
            p.drawRect(sel)

        # Playhead.
        px = self._x_for(self._position)
        p.setPen(QPen(self._PLAYHEAD, 2))
        p.drawLine(QPointF(px, r.top() - 4), QPointF(px, r.bottom() + 4))

        # Text row: position (left), state (center), duration (right).
        text_row = QRectF(4, 4, self.width() - 8, 20)
        p.setPen(self._TEXT)
        p.drawText(text_row, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f" Position: {_fmt(self._position)}")
        p.drawText(text_row, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"Duration: {_fmt(self._duration)} ")
        end_txt = _fmt(self._end) if self.has_explicit_end() else "end"
        n = self._segment_count()
        p.drawText(text_row, Qt.AlignmentFlag.AlignCenter,
                   f"Start {_fmt(self._start)} → End {end_txt}  ·  {n} segment{'s' if n != 1 else ''} × {_fmt(self._split)}")

    def _segment_boundaries(self) -> list[float]:
        eff_end = self.end()
        if eff_end <= self._start or self._split <= 0:
            return []
        out = []
        t = self._start
        while t < eff_end - 0.001:
            out.append(t)
            t += self._split
        return out

    def _segment_count(self) -> int:
        return len(self._segment_boundaries())
