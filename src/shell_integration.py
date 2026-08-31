"""Install / uninstall the nested AutoSplit Explorer context menu.

Structure (per supported extension, under HKCU):

    ...\SystemFileAssociations\.ext\shell\AutoSplit
        (Default)               = "AutoSplit"
        Icon                    = <exe path>
        MUIVerb                 = "AutoSplit"
        SubCommands             = ""     (empty = use ExtendedSubCommandsKey)
        ExtendedSubCommandsKey  = "AutoSplitCommands"

    HKCU\Software\Classes\AutoSplitCommands\shell\
        01Split       MUIVerb = "Split…"                command\(Default) = "<exe>" "%1"
        02JoinQuick   MUIVerb = "Join (Quick)"          command\(Default) = "<exe>" --join-quick "%1"
        03JoinCustom  MUIVerb = "Join (Customize)…"     command\(Default) = "<exe>" --join-custom "%1"

Run:
    python shell_integration.py install
    python shell_integration.py uninstall
"""
from __future__ import annotations

import argparse
import os
import sys
import winreg

from ffmpeg_utils import AUDIO_EXTS, VIDEO_EXTS


PARENT_KEY = "AutoSplitMedia"       # unique key under the ext's ...\shell\
SUBCMDS_KEY = "AutoSplitCommands"   # under HKCU\Software\Classes\
APP_LABEL = "AutoSplit"


def _extensions() -> list[str]:
    return sorted(VIDEO_EXTS | AUDIO_EXTS)


# --- command line builder --------------------------------------------------

def _exe_and_scriptless_prefix() -> tuple[str, list[str]]:
    """Return (exe_to_launch, args_before_placeholder).

    In dev mode: returns (pythonw.exe, [main.py path]).
    In frozen mode: returns (AutoSplit.exe, []).
    """
    if getattr(sys, "frozen", False):
        return sys.executable, []
    pyw = sys.executable
    if pyw.lower().endswith("python.exe"):
        cand = pyw[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            pyw = cand
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return pyw, [script]


def _quote(s: str) -> str:
    return f'"{s}"'


def _cmd_split() -> str:
    exe, prefix = _exe_and_scriptless_prefix()
    parts = [_quote(exe)] + [_quote(p) for p in prefix] + ['"%1"']
    return " ".join(parts)


def _cmd_join_quick() -> str:
    exe, prefix = _exe_and_scriptless_prefix()
    parts = [_quote(exe)] + [_quote(p) for p in prefix] + ["--join-quick", '"%1"']
    return " ".join(parts)


def _cmd_join_custom() -> str:
    exe, prefix = _exe_and_scriptless_prefix()
    parts = [_quote(exe)] + [_quote(p) for p in prefix] + ["--join-custom", '"%1"']
    return " ".join(parts)


def _cmd_extract_first() -> str:
    exe, prefix = _exe_and_scriptless_prefix()
    parts = [_quote(exe)] + [_quote(p) for p in prefix] + ["--extract-first-frame", '"%1"']
    return " ".join(parts)


def _cmd_extract_last() -> str:
    exe, prefix = _exe_and_scriptless_prefix()
    parts = [_quote(exe)] + [_quote(p) for p in prefix] + ["--extract-last-frame", '"%1"']
    return " ".join(parts)


def _icon() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    return ""


# --- registry helpers ------------------------------------------------------

def _write_parent(root_path: str) -> None:
    """Create the parent AutoSplit entry that delegates to the SUBCMDS_KEY."""
    key_path = fr"{root_path}\shell\{PARENT_KEY}"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, APP_LABEL)
        winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, APP_LABEL)
        winreg.SetValueEx(k, "SubCommands", 0, winreg.REG_SZ, "")
        winreg.SetValueEx(k, "ExtendedSubCommandsKey", 0, winreg.REG_SZ, SUBCMDS_KEY)
        icon = _icon()
        if icon:
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)


def _delete_parent(root_path: str) -> None:
    key_path = fr"{root_path}\shell\{PARENT_KEY}"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except FileNotFoundError:
        pass


def _write_subcommands() -> None:
    """Create HKCU\\Software\\Classes\\AutoSplitCommands\\shell\\... entries.

    Item keys are numbered (01Split, 02JoinQuick, 03JoinCustom) because
    Explorer orders subcommands alphabetically by subkey name.
    """
    root = fr"Software\Classes\{SUBCMDS_KEY}"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, root):
        pass
    # Each entry: (subkey_name, MUIVerb, command, MultiSelectModel)
    # MultiSelectModel = "Player" means "fire this command once per selected file"
    # (which is exactly what our single-instance IPC needs). Without it,
    # Windows 11 hides the verb when >1 file is selected. "Single" for Split
    # since that operates on one file at a time.
    entries = [
        ("01Split",           "Split…",                _cmd_split(),          "Single"),
        ("02JoinQuick",       "Join (Quick)",          _cmd_join_quick(),     "Player"),
        ("03JoinCustom",      "Join (Customize)…",     _cmd_join_custom(),    "Player"),
        ("04ExtractFirst",    "Extract first frame",   _cmd_extract_first(),  "Player"),
        ("05ExtractLast",     "Extract last frame",    _cmd_extract_last(),   "Player"),
    ]
    for name, muiverb, cmdline, multisel in entries:
        item = fr"{root}\shell\{name}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, item) as k:
            winreg.SetValueEx(k, "MUIVerb", 0, winreg.REG_SZ, muiverb)
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, multisel)
            icon = _icon()
            if icon:
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, fr"{item}\command") as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, cmdline)


def _delete_subcommands() -> None:
    root = fr"Software\Classes\{SUBCMDS_KEY}"
    subs = [
        fr"{root}\shell\01Split\command",
        fr"{root}\shell\01Split",
        fr"{root}\shell\02JoinQuick\command",
        fr"{root}\shell\02JoinQuick",
        fr"{root}\shell\03JoinCustom\command",
        fr"{root}\shell\03JoinCustom",
        fr"{root}\shell",
        root,
    ]
    for path in subs:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass


# --- legacy uninstaller for the old single-verb entry ---------------------
LEGACY_KEY = "AutoSplitMedia"   # original key name (same, kept to be explicit)


def _delete_legacy(root_path: str) -> None:
    for sub in (fr"{root_path}\shell\{LEGACY_KEY}\command",
                fr"{root_path}\shell\{LEGACY_KEY}"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
        except FileNotFoundError:
            pass


# --- top-level actions -----------------------------------------------------

def install() -> list[str]:
    _write_subcommands()
    done = []
    for ext in _extensions():
        root = fr"Software\Classes\SystemFileAssociations\{ext}"
        _write_parent(root)
        done.append(ext)
    return done


def uninstall() -> list[str]:
    removed = []
    for ext in _extensions():
        root = fr"Software\Classes\SystemFileAssociations\{ext}"
        _delete_parent(root)
        _delete_legacy(root)      # clear old flat entry if present
        removed.append(ext)
    _delete_subcommands()
    return removed


def _main() -> int:
    if sys.platform != "win32":
        print("Windows only.", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(description="Manage AutoSplit Explorer context menu.")
    ap.add_argument("action", choices=("install", "uninstall"))
    args = ap.parse_args()
    if args.action == "install":
        exts = install()
        print(f"Installed nested context menu for {len(exts)} extensions:")
        print("  " + " ".join(exts))
        print("Menu entries:")
        print(f"  Split…                → {_cmd_split()}")
        print(f"  Join (Quick)          → {_cmd_join_quick()}")
        print(f"  Join (Customize)…     → {_cmd_join_custom()}")
        print(f"  Extract first frame   → {_cmd_extract_first()}")
        print(f"  Extract last frame    → {_cmd_extract_last()}")
    else:
        exts = uninstall()
        print(f"Removed context menu entries for {len(exts)} extensions.")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
