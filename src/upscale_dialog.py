"""Upscale export dialog. First-class engine: SeedVR2 (diffusion, GPU)."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

import seedvr2_setup as sv
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


class UpscaleDialog(QDialog):
    """Options: engine, model size, target resolution, range, output path."""

    TARGETS = [
        ("Same as source × 2",     "x2"),
        ("Same as source × 3",     "x3"),
        ("Same as source × 4",     "x4"),
        ("To 1080p  (1920×1080)",  "1080"),
        ("To 1440p  (2560×1440)",  "1440"),
        ("To 4K  (3840×2160)",     "2160"),
        ("To 8K  (7680×4320)",     "4320"),
        ("Custom…",                "custom"),
    ]

    def __init__(self, settings: AppSettings, source_path: str,
                 source_w: int, source_h: int,
                 in_seconds: float, out_seconds: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Upscale (AI, GPU)")
        self.setModal(True)
        self.resize(620, 620)

        self._settings = settings
        self._source = source_path
        self._src_w = int(source_w or 1920)
        self._src_h = int(source_h or 1080)
        self._in = in_seconds
        self._out = out_seconds

        v = QVBoxLayout(self)

        info = QLabel(
            f"Source: <b>{self._src_w}×{self._src_h}</b>  ·  "
            f"Range: {_fmt_time(in_seconds)} → {_fmt_time(out_seconds)}"
        )
        v.addWidget(info)

        # --- Engine (only SeedVR2 for now) ---
        eng_gb = QGroupBox("Engine")
        eg = QVBoxLayout(eng_gb)
        self.engine_label = QLabel(
            "<b>SeedVR2</b>  (ByteDance, ICLR 2026 — diffusion-based video super-resolution)"
        )
        self.engine_label.setWordWrap(True)
        eg.addWidget(self.engine_label)

        row = QHBoxLayout()
        row.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.addItem("3B  (smaller, ~7 GB download, 8 GB+ VRAM)", "3b")
        self.model_combo.addItem("7B  (full quality, ~14 GB download, 16 GB+ VRAM)", "7b")
        self.model_combo.setCurrentIndex(0)   # 3B default; safer for install size
        self.model_combo.currentIndexChanged.connect(self._refresh_install_status)
        row.addWidget(self.model_combo, 1)
        eg.addLayout(row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        eg.addWidget(self.status_label)

        install_row = QHBoxLayout()
        self.install_btn = QPushButton("Install SeedVR2…")
        self.install_btn.clicked.connect(self._on_install)
        install_row.addWidget(self.install_btn)
        self.cancel_install_btn = QPushButton("Cancel install")
        self.cancel_install_btn.setVisible(False)
        self.cancel_install_btn.clicked.connect(self._on_cancel_install)
        install_row.addWidget(self.cancel_install_btn)
        install_row.addStretch(1)
        eg.addLayout(install_row)

        self.install_progress = QProgressBar()
        self.install_progress.setRange(0, 1000)
        self.install_progress.setValue(0)
        self.install_progress.setVisible(False)
        eg.addWidget(self.install_progress)
        self.install_status = QLabel("")
        self.install_status.setObjectName("Muted")
        self.install_status.setVisible(False)
        eg.addWidget(self.install_status)

        v.addWidget(eng_gb)

        # --- Target resolution ---
        tgt_gb = QGroupBox("Target resolution")
        tf = QFormLayout(tgt_gb)
        self.target_combo = QComboBox()
        for label, key in self.TARGETS:
            self.target_combo.addItem(label, key)
        self.target_combo.setCurrentIndex(5)   # 4K default
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        tf.addRow("Preset:", self.target_combo)

        cust_row = QHBoxLayout()
        self.custom_w = QSpinBox(); self.custom_w.setRange(320, 8192); self.custom_w.setValue(3840)
        self.custom_h = QSpinBox(); self.custom_h.setRange(240, 8192); self.custom_h.setValue(2160)
        cust_row.addWidget(QLabel("W:"))
        cust_row.addWidget(self.custom_w)
        cust_row.addSpacing(10)
        cust_row.addWidget(QLabel("H:"))
        cust_row.addWidget(self.custom_h)
        cust_row.addStretch(1)
        wrap = QWidget(); wrap.setLayout(cust_row)
        tf.addRow("Custom:", wrap)
        v.addWidget(tgt_gb)

        # --- Range ---
        rg = QGroupBox("Range")
        rgv = QVBoxLayout(rg)
        self.range_range = QRadioButton(
            f"IN → OUT   ({_fmt_time(in_seconds)} → {_fmt_time(out_seconds)})"
        )
        self.range_range.setChecked(True)
        self.range_full = QRadioButton("Full media")
        rgv.addWidget(self.range_range)
        rgv.addWidget(self.range_full)
        v.addWidget(rg)

        # --- Output ---
        og = QGroupBox("Output")
        of = QFormLayout(og)
        self.keep_audio_cb = QCheckBox("Keep audio (untouched)")
        self.keep_audio_cb.setChecked(True)
        of.addRow("Audio:", self.keep_audio_cb)
        prow = QHBoxLayout()
        self.path_edit = QLineEdit(self._suggest_path())
        prow.addWidget(self.path_edit, 1)
        b = QPushButton("Browse…")
        b.clicked.connect(self._on_browse)
        prow.addWidget(b)
        pwrap = QWidget(); pwrap.setLayout(prow)
        of.addRow("Save as:", pwrap)
        v.addWidget(og)

        # --- Buttons ---
        self.btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.btns.accepted.connect(self.accept)
        self.btns.rejected.connect(self.reject)
        v.addWidget(self.btns)

        # Trigger initial state.
        self._on_target_changed()
        self._refresh_install_status()

        self._install_thread: Optional[QThread] = None
        self._install_worker = None
        self._install_cancel = False

    # ---------- helpers ----------
    def _target_wh(self) -> tuple[int, int]:
        key = self.target_combo.currentData()
        if key == "x2": return (self._src_w * 2, self._src_h * 2)
        if key == "x3": return (self._src_w * 3, self._src_h * 3)
        if key == "x4": return (self._src_w * 4, self._src_h * 4)
        if key == "1080": return (1920, 1080)
        if key == "1440": return (2560, 1440)
        if key == "2160": return (3840, 2160)
        if key == "4320": return (7680, 4320)
        return (int(self.custom_w.value()), int(self.custom_h.value()))

    def _on_target_changed(self, *_) -> None:
        is_custom = self.target_combo.currentData() == "custom"
        self.custom_w.setEnabled(is_custom)
        self.custom_h.setEnabled(is_custom)
        w, h = self._target_wh()
        # Snap the custom fields to the current preset for visibility.
        if not is_custom:
            self.custom_w.blockSignals(True); self.custom_h.blockSignals(True)
            self.custom_w.setValue(w); self.custom_h.setValue(h)
            self.custom_w.blockSignals(False); self.custom_h.blockSignals(False)
        self._update_output_ext()

    def _suggest_path(self) -> str:
        folder = os.path.dirname(self._source) if self._source else ""
        base = os.path.splitext(os.path.basename(self._source))[0] if self._source else "output"
        w, h = self._target_wh()
        return os.path.join(folder, f"{base}_upscaled_{w}x{h}.mp4")

    def _update_output_ext(self) -> None:
        cur = self.path_edit.text().strip()
        if not cur:
            self.path_edit.setText(self._suggest_path())
            return
        root, _ = os.path.splitext(cur)
        # Rebuild tag to reflect current target resolution.
        w, h = self._target_wh()
        parts = root.rsplit("_upscaled_", 1)
        if len(parts) == 2:
            self.path_edit.setText(f"{parts[0]}_upscaled_{w}x{h}.mp4")

    def _on_browse(self) -> None:
        p, _ = QFileDialog.getSaveFileName(
            self, "Save as", self.path_edit.text(), "MP4 (*.mp4)"
        )
        if p:
            self.path_edit.setText(p)

    def _refresh_install_status(self, *_) -> None:
        kind = self.model_combo.currentData()
        st = sv.install_state()
        model_key = f"model_{kind}"
        ready = st["uv"] and st["venv"] and st["repo"] and st["helper"] and st.get(model_key)

        # Line 1 shows overall install state.
        parts = [
            f"uv: {'✓' if st['uv'] else '✗'}",
            f"venv: {'✓' if st['venv'] else '✗'}",
            f"deps: {'✓' if (sv.seedvr2_root() / '.deps_installed').exists() else '✗'}",
            f"repo: {'✓' if st['repo'] else '✗'}",
            f"model-{kind}: {'✓' if st.get(model_key) else '✗'}",
        ]
        self.status_label.setText("  ·  ".join(parts))

        if ready:
            self.install_btn.setText(f"Reinstall / update SeedVR2-{kind.upper()}")
            self.btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
            self.btns.button(QDialogButtonBox.StandardButton.Ok).setText("Upscale")
        else:
            gb = sv.estimated_download_size_bytes(kind) / (1024 ** 3)
            self.install_btn.setText(
                f"Install SeedVR2-{kind.upper()}  (~{gb:.1f} GB download)"
            )
            self.btns.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
            self.btns.button(QDialogButtonBox.StandardButton.Ok).setText("Install first")

    # ---------- install button ----------
    def _on_install(self) -> None:
        kind = self.model_combo.currentData()
        gb = sv.estimated_download_size_bytes(kind) / (1024 ** 3)
        ans = QMessageBox.question(
            self, "Install SeedVR2",
            f"About to install SeedVR2-{kind.upper()} to:\n"
            f"  {sv.seedvr2_root()}\n\n"
            f"Download: ~{gb:.1f} GB total (uv + PyTorch + code + weights).\n"
            f"Time on a typical connection: 15-40 minutes.\n\n"
            f"You can cancel at any time; the installer resumes from where it stopped.\n\n"
            f"Continue?"
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        self.install_btn.setEnabled(False)
        self.install_btn.setText("Installing…")
        self.cancel_install_btn.setVisible(True)
        self.install_progress.setVisible(True)
        self.install_status.setVisible(True)
        self.install_progress.setValue(0)
        self._install_cancel = False

        class _Worker(QObject):
            progress = Signal(float)
            status = Signal(str)
            done = Signal(bool, str)

            def __init__(self, kind, cancel_fn):
                super().__init__()
                self.kind = kind
                self.cancel_fn = cancel_fn

            def run(self):
                try:
                    sv.install(
                        model_kind=self.kind,
                        on_progress=lambda f: self.progress.emit(f),
                        on_status=lambda s: self.status.emit(s),
                        should_cancel=self.cancel_fn,
                    )
                    self.done.emit(True, "ok")
                except Exception as e:
                    self.done.emit(False, str(e))

        thread = QThread(self)
        worker = _Worker(kind, lambda: self._install_cancel)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(lambda f: self.install_progress.setValue(int(f * 1000)))
        worker.status.connect(self.install_status.setText)

        def _on_done(ok: bool, msg: str):
            self.install_progress.setVisible(False)
            self.install_status.setVisible(False)
            self.cancel_install_btn.setVisible(False)
            self.install_btn.setEnabled(True)
            thread.quit()
            self._refresh_install_status()
            if not ok:
                QMessageBox.critical(self, "Install failed",
                                     f"SeedVR2 install failed:\n\n{msg}\n\n"
                                     f"Re-run the installer — completed stages will be skipped.")
            else:
                QMessageBox.information(self, "Installed",
                                        f"SeedVR2 installed at:\n{sv.seedvr2_root()}")

        worker.done.connect(_on_done)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._install_thread = thread
        self._install_worker = worker
        thread.start()

    def _on_cancel_install(self) -> None:
        self._install_cancel = True
        self.install_status.setText("Cancelling…")

    # ---------- returns selection ----------
    def selection(self) -> dict:
        w, h = self._target_wh()
        return {
            "engine": "seedvr2",
            "model_kind": self.model_combo.currentData(),
            "target_w": w,
            "target_h": h,
            "keep_audio": self.keep_audio_cb.isChecked(),
            "range": "full" if self.range_full.isChecked() else "in_out",
            "output_path": self.path_edit.text().strip(),
        }
