"""Dialog for scene / silence auto-detection with live progress + preview."""
from __future__ import annotations

from PySide6.QtCore import Qt, QObject, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from auto_detect import (
    SceneOptions, SilenceOptions,
    detect_scenes, detect_silence,
    scenes_to_regions, silence_to_regions,
)


def _fmt(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    m = int(seconds // 60); s = seconds - m * 60
    return f"{m:02d}:{s:05.2f}"


class AutoDetectDialog(QDialog):
    def __init__(self, input_path: str, duration: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto-detect markers")
        self.setModal(True)
        self.resize(620, 640)

        self._input = input_path
        self._duration = duration
        self._worker: QObject | None = None
        self._thread: QThread | None = None
        self._cancel = False
        self._resulting_regions: list[tuple[float, float, str]] = []

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            f"Source: {input_path.split(chr(92))[-1]}   ·   {_fmt(duration)}"
        ))

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_scenes_tab(), "Scene changes")
        self.tabs.addTab(self._build_silence_tab(), "Silence")
        v.addWidget(self.tabs)

        # --- run + progress ---
        row = QHBoxLayout()
        self.run_btn = QPushButton("Detect")
        self.run_btn.clicked.connect(self._on_run)
        row.addWidget(self.run_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        row.addWidget(self.cancel_btn)
        row.addStretch(1)
        v.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        v.addWidget(self.progress)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("Muted")
        v.addWidget(self.progress_label)

        # --- results preview ---
        v.addWidget(QLabel("Detected:"))
        self.result_list = QListWidget()
        self.result_list.setMaximumHeight(220)
        v.addWidget(self.result_list)

        # --- add / cancel ---
        self.btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.btns.button(QDialogButtonBox.StandardButton.Ok).setText("Add as markers")
        self.btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.btns.accepted.connect(self.accept)
        self.btns.rejected.connect(self.reject)
        v.addWidget(self.btns)

    # ----------------------- tabs -----------------------

    def _build_scenes_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        info = QLabel(
            "Detects hard cuts and dissolves. Lower threshold = more sensitive."
        )
        info.setWordWrap(True)
        f.addRow(info)
        self.scene_threshold = QDoubleSpinBox()
        self.scene_threshold.setRange(0.05, 1.0)
        self.scene_threshold.setSingleStep(0.05)
        self.scene_threshold.setValue(0.4)
        f.addRow("Threshold:", self.scene_threshold)
        self.scene_min_gap = QDoubleSpinBox()
        self.scene_min_gap.setRange(0.0, 60.0)
        self.scene_min_gap.setSingleStep(0.5)
        self.scene_min_gap.setValue(1.0)
        self.scene_min_gap.setSuffix(" s")
        f.addRow("Merge scenes closer than:", self.scene_min_gap)
        self.scene_prefix = QComboBox()
        self.scene_prefix.setEditable(True)
        self.scene_prefix.addItems(["scene", "cut", "shot"])
        f.addRow("Marker prefix:", self.scene_prefix)
        return w

    def _build_silence_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        info = QLabel(
            "Finds silent stretches. Use 'Speech = regions' to mark the "
            "non-silent parts (e.g. podcast dialog segments)."
        )
        info.setWordWrap(True)
        f.addRow(info)
        self.silence_noise = QDoubleSpinBox()
        self.silence_noise.setRange(-70.0, -5.0)
        self.silence_noise.setSingleStep(1.0)
        self.silence_noise.setValue(-30.0)
        self.silence_noise.setSuffix(" dB")
        f.addRow("Noise threshold below:", self.silence_noise)
        self.silence_min = QDoubleSpinBox()
        self.silence_min.setRange(0.05, 60.0)
        self.silence_min.setSingleStep(0.1)
        self.silence_min.setValue(0.6)
        self.silence_min.setSuffix(" s")
        f.addRow("Min silence length:", self.silence_min)
        row = QHBoxLayout()
        self.sil_mode_speech = QRadioButton("Speech = regions")
        self.sil_mode_silence = QRadioButton("Silence = regions")
        self.sil_mode_speech.setChecked(True)
        row.addWidget(self.sil_mode_speech)
        row.addWidget(self.sil_mode_silence)
        row.addStretch(1)
        wrap = QWidget(); wrap.setLayout(row)
        f.addRow("Mark:", wrap)
        self.silence_prefix = QComboBox()
        self.silence_prefix.setEditable(True)
        self.silence_prefix.addItems(["speech", "clip", "part"])
        f.addRow("Marker prefix:", self.silence_prefix)
        return w

    # ----------------------- run -----------------------

    def _on_run(self) -> None:
        self.result_list.clear()
        self._resulting_regions = []
        self.btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.progress.setValue(0)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self._cancel = False

        mode_scene = self.tabs.currentIndex() == 0

        class _W(QObject):
            progress = Signal(float)
            done = Signal(object, str)     # (regions_or_none, error)

            def __init__(self, outer, mode_scene):
                super().__init__()
                self.outer = outer
                self.mode_scene = mode_scene

            def run(self):
                try:
                    if self.mode_scene:
                        opts = SceneOptions(
                            threshold=float(self.outer.scene_threshold.value()),
                            min_gap_seconds=float(self.outer.scene_min_gap.value()),
                        )
                        prefix = self.outer.scene_prefix.currentText().strip() or "scene"
                        times = detect_scenes(
                            self.outer._input, self.outer._duration, opts,
                            on_progress=lambda f: self.progress.emit(f),
                            should_cancel=lambda: self.outer._cancel,
                        )
                        regions = scenes_to_regions(times, self.outer._duration, prefix)
                    else:
                        opts = SilenceOptions(
                            noise_db=float(self.outer.silence_noise.value()),
                            min_silence_seconds=float(self.outer.silence_min.value()),
                            mode="silence" if self.outer.sil_mode_silence.isChecked() else "speech",
                        )
                        prefix = self.outer.silence_prefix.currentText().strip() or "clip"
                        sils = detect_silence(
                            self.outer._input, self.outer._duration, opts,
                            on_progress=lambda f: self.progress.emit(f),
                            should_cancel=lambda: self.outer._cancel,
                        )
                        regions = silence_to_regions(sils, self.outer._duration, opts.mode, prefix)
                    self.done.emit(regions, "")
                except Exception as e:
                    self.done.emit(None, str(e))

        self._thread = QThread(self)
        self._worker = _W(self, mode_scene)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(lambda f: self.progress.setValue(int(f * 1000)))
        self._worker.progress.connect(
            lambda f: self.progress_label.setText(f"Scanning… {int(f * 100)}%")
        )
        self._worker.done.connect(self._on_done)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_cancel(self) -> None:
        self._cancel = True
        self.progress_label.setText("Cancelling…")

    def _on_done(self, regions, err: str) -> None:
        self.run_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        if self._thread:
            self._thread.quit()
        if err:
            QMessageBox.critical(self, "Detection failed", err)
            return
        if not regions:
            self.progress_label.setText("No candidates found — try loosening the settings.")
            return
        self._resulting_regions = list(regions)
        self.result_list.clear()
        for start, end, name in regions:
            self.result_list.addItem(
                QListWidgetItem(f"{name}   ·   {_fmt(start)} → {_fmt(end)}   ({_fmt(end - start)})")
            )
        self.progress_label.setText(f"Found {len(regions)} candidates.")
        self.btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)

    def selected_regions(self) -> list[tuple[float, float, str]]:
        return list(self._resulting_regions)
