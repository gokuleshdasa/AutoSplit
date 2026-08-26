"""User settings persisted via QSettings (Windows registry)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from PySide6.QtCore import QSettings


ORG = "AutoSplitMedia"
APP = "AutoSplit"


# Canonical action id -> (label shown in Preferences, default key sequence).
DEFAULT_KEYBINDINGS: Dict[str, tuple[str, str]] = {
    "play_pause":   ("Play / Pause",                    "Space"),
    "nudge_left":   ("Nudge playhead left",             "Left"),
    "nudge_right":  ("Nudge playhead right",            "Right"),
    "jump_left":    ("Jump left (bigger step)",         "Ctrl+Left"),
    "jump_right":   ("Jump right (bigger step)",        "Ctrl+Right"),
    "big_jump_left":  ("Big jump left",                 "Shift+Left"),
    "big_jump_right": ("Big jump right",                "Shift+Right"),
    "mark_in":      ("Mark IN at playhead",             "I"),
    "mark_out":     ("Mark OUT at playhead",            "O"),
    "add_marker":   ("Add region from IN/OUT",          "M"),
    "toggle_snap":  ("Toggle snap (1s ↔ 1 frame)",      "S"),
    "open_file":    ("Open file…",                      "Ctrl+O"),
    "preferences":  ("Preferences…",                    "Ctrl+,"),
}


@dataclass
class AppSettings:
    last_export_dir: str = ""
    split_duration_seconds: int = 600
    output_format_video: str = "mp4_h264"
    output_format_audio: str = "mp3"
    audio_bitrate: str = "192k"
    video_crf: int = 18
    prefix: str = ""
    suffix: str = ""
    pad_width: int = 3
    close_after_export: bool = False
    open_folder_after_export: bool = True
    overwrite_existing: bool = False
    # Big-step jump sizes (seconds).
    jump_seconds: float = 5.0
    big_jump_seconds: float = 10.0
    keybindings: Dict[str, str] = field(default_factory=dict)
    # Joiner
    join_output_dir: str = ""                        # empty = same as source
    join_output_format_video: str = "mp4_h264"
    join_output_format_audio: str = "mp3"
    join_always_reencode: bool = False               # False = auto-pick fast when compatible


def _q() -> QSettings:
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, ORG, APP)


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return bool(v)


def load() -> AppSettings:
    q = _q()
    s = AppSettings()
    s.last_export_dir = q.value("last_export_dir", s.last_export_dir, type=str)
    s.split_duration_seconds = int(q.value("split_duration_seconds", s.split_duration_seconds))
    s.output_format_video = q.value("output_format_video", s.output_format_video, type=str)
    s.output_format_audio = q.value("output_format_audio", s.output_format_audio, type=str)
    s.audio_bitrate = q.value("audio_bitrate", s.audio_bitrate, type=str)
    s.video_crf = int(q.value("video_crf", s.video_crf))
    s.prefix = q.value("prefix", s.prefix, type=str)
    s.suffix = q.value("suffix", s.suffix, type=str)
    s.pad_width = int(q.value("pad_width", s.pad_width))
    s.close_after_export = _to_bool(q.value("close_after_export", s.close_after_export))
    s.open_folder_after_export = _to_bool(q.value("open_folder_after_export", s.open_folder_after_export))
    s.overwrite_existing = _to_bool(q.value("overwrite_existing", s.overwrite_existing))
    s.jump_seconds = float(q.value("jump_seconds", s.jump_seconds))
    s.big_jump_seconds = float(q.value("big_jump_seconds", s.big_jump_seconds))
    s.join_output_dir = q.value("join_output_dir", s.join_output_dir, type=str)
    s.join_output_format_video = q.value("join_output_format_video", s.join_output_format_video, type=str)
    s.join_output_format_audio = q.value("join_output_format_audio", s.join_output_format_audio, type=str)
    s.join_always_reencode = _to_bool(q.value("join_always_reencode", s.join_always_reencode))

    # Keybindings — start from defaults, override with any saved.
    kb = {aid: default for aid, (_lbl, default) in DEFAULT_KEYBINDINGS.items()}
    q.beginGroup("keybindings")
    for key in q.childKeys():
        val = q.value(key, "", type=str)
        if val:
            kb[key] = val
    q.endGroup()
    s.keybindings = kb
    return s


def save(s: AppSettings) -> None:
    q = _q()
    q.setValue("last_export_dir", s.last_export_dir)
    q.setValue("split_duration_seconds", s.split_duration_seconds)
    q.setValue("output_format_video", s.output_format_video)
    q.setValue("output_format_audio", s.output_format_audio)
    q.setValue("audio_bitrate", s.audio_bitrate)
    q.setValue("video_crf", s.video_crf)
    q.setValue("prefix", s.prefix)
    q.setValue("suffix", s.suffix)
    q.setValue("pad_width", s.pad_width)
    q.setValue("close_after_export", s.close_after_export)
    q.setValue("open_folder_after_export", s.open_folder_after_export)
    q.setValue("overwrite_existing", s.overwrite_existing)
    q.setValue("jump_seconds", s.jump_seconds)
    q.setValue("big_jump_seconds", s.big_jump_seconds)
    q.setValue("join_output_dir", s.join_output_dir)
    q.setValue("join_output_format_video", s.join_output_format_video)
    q.setValue("join_output_format_audio", s.join_output_format_audio)
    q.setValue("join_always_reencode", s.join_always_reencode)

    q.beginGroup("keybindings")
    for aid, seq in s.keybindings.items():
        q.setValue(aid, seq)
    q.endGroup()
    q.sync()
