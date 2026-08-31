"""AutoSplit entry point.

Usage:
    python main.py                              # splitter, no file
    python main.py "C:\\clip.mp4"               # splitter, preloaded
    python main.py --join-custom "C:\\clip.mp4" # joiner (customize)
    python main.py --join-quick  "C:\\clip.mp4" # joiner (quick, headless-ish)

When Explorer fires --join-* once per selected file, the first invocation
opens a QLocalServer; later invocations forward their paths to it via
QLocalSocket and exit.  The server debounces and shows either the full
joiner window (custom) or a compact progress dialog (quick).
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


# --- diagnostic log ---------------------------------------------------------
# pythonw.exe swallows stderr, so when the Explorer verb misbehaves we have no
# way to see what happened. This lightweight log always writes to %TEMP%\autosplit.log
# so we can always look at recent invocations.
_LOG_PATH = os.path.join(tempfile.gettempdir(), "autosplit.log")


def _log(msg: str) -> None:
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='milliseconds')}  "
                    f"pid={os.getpid()}  {msg}\n")
    except Exception:
        pass


def _install_crash_handlers() -> None:
    """Route Python exceptions and native C crashes into the log so we can
    diagnose failures in the frozen exe (which otherwise dies silently)."""
    import faulthandler
    import traceback

    # Native crash dump (segfaults, C++ asserts, Qt violations).
    try:
        faulthandler.enable(open(_LOG_PATH, "a", buffering=1))
    except Exception:
        pass

    # Uncaught Python exceptions.
    def _excepthook(etype, value, tb):
        try:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"\n{datetime.datetime.now().isoformat(timespec='milliseconds')}  "
                        f"pid={os.getpid()}  UNCAUGHT EXCEPTION\n")
                traceback.print_exception(etype, value, tb, file=f)
                f.write("\n")
        except Exception:
            pass
        sys.__excepthook__(etype, value, tb)

    sys.excepthook = _excepthook

    # Qt slots swallow exceptions in PySide6 6.x — hook them explicitly.
    try:
        from PySide6.QtCore import qInstallMessageHandler, QtMsgType
        def qt_msg(mode, ctx, msg):
            if mode >= QtMsgType.QtCriticalMsg:
                _log(f"Qt {mode.name}: {msg}")
        qInstallMessageHandler(qt_msg)
    except Exception:
        pass


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--join-custom", action="store_true")
    ap.add_argument("--join-quick", action="store_true")
    ap.add_argument("--extract-first-frame", action="store_true")
    ap.add_argument("--extract-last-frame", action="store_true")
    # Positional: the file passed by Explorer.
    ap.add_argument("path", nargs="?", default=None)
    return ap.parse_args()


def _set_icon(app: QApplication) -> None:
    icon_path = Path(__file__).resolve().parent.parent / "assets" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))


def _run_extract_frame_headless(path: str, which: str) -> int:
    """Extract first or last frame of `path`, save next to it, exit.
    No window, no QApplication — just ffmpeg. Errors surface via a
    Windows message box (ctypes) rather than a Qt dialog."""
    import subprocess
    _log(f"extract_{which}_frame: path={path!r}")
    if not path or not os.path.exists(path):
        _msgbox_error(f"File not found:\n{path}")
        return 1

    # QSettings works without a QApplication; safe to import.
    try:
        from settings import load
        from ffmpeg_utils import probe, _no_window_kwargs
        from frame_extract import build_single_frame_cmd
    except Exception as e:
        _msgbox_error(f"Startup failed: {e}")
        return 1

    try:
        info = probe(path)
    except Exception as e:
        _msgbox_error(f"Cannot read media:\n{path}\n\n{e}")
        return 1
    if info.duration_seconds <= 0:
        _msgbox_error(f"{os.path.basename(path)} has zero duration.")
        return 1
    if not info.has_video:
        _msgbox_error(f"{os.path.basename(path)} has no video track.")
        return 1

    s = load()
    fmt = s.frame_format or "png"
    folder = s.frame_output_dir.strip() if s.frame_output_dir else ""
    if not folder or not os.path.isdir(folder):
        folder = os.path.dirname(path) or os.getcwd()
    base = os.path.splitext(os.path.basename(path))[0]

    if which == "first":
        # A tiny offset avoids a black opening frame on many encodes.
        ts = 0.05 if info.duration_seconds > 0.1 else 0.0
        suffix = "first"
    else:
        # WMF-style backoff: 2 frame periods before the end.
        ts = max(0.0, info.duration_seconds - 2 * info.frame_period())
        suffix = "last"

    out = os.path.join(folder, f"{base}_{suffix}.{fmt}")
    # Auto-increment on conflict so multi-select doesn't clobber.
    if os.path.exists(out):
        root, ext = os.path.splitext(out)
        i = 2
        while os.path.exists(f"{root} ({i}){ext}"):
            i += 1
        out = f"{root} ({i}){ext}"

    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = build_single_frame_cmd(
        input_path=path,
        timestamp_seconds=ts,
        output_path=out,
        quality=int(s.frame_jpg_quality),
    )
    try:
        proc = subprocess.run(cmd, capture_output=True, **_no_window_kwargs())
    except FileNotFoundError as e:
        _msgbox_error(f"ffmpeg not found:\n{e}")
        return 1
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode(errors="replace")[-800:]
        _msgbox_error(f"Extract {which} frame failed for:\n{path}\n\n{tail}")
        return 1
    _log(f"extract_{which}_frame: wrote {out}")
    return 0


def _msgbox_error(msg: str) -> None:
    """Show an OS-native error dialog without needing a QApplication."""
    _log(f"error: {msg[:200]}")
    if sys.platform == "win32":
        try:
            import ctypes
            MB_ICONERROR = 0x10
            MB_TOPMOST   = 0x40000
            ctypes.windll.user32.MessageBoxW(
                0, msg, "AutoSplit — Error", MB_ICONERROR | MB_TOPMOST
            )
        except Exception:
            print(msg, file=sys.stderr)
    else:
        print(msg, file=sys.stderr)


def _run_splitter(app: QApplication, initial: str | None) -> int:
    """Splitter. Uses the same single-instance IPC as the joiner so that
    'Open with AutoSplit' on multiple files loads them all as a queue.

    - First invocation opens the window, becomes the QLocalServer.
    - Subsequent invocations (same click) forward their path and exit silently.
    - After a 500 ms debounce, MainWindow receives the full list as a queue.
    - Single-file usage is unchanged: one path in = queue of size 1 = queue panel stays hidden.
    """
    from main_window import MainWindow
    from join_ipc import try_forward_to_running, JoinCollector

    collector = None
    if initial:
        if try_forward_to_running(initial, "split"):
            _log(f"split: forwarded '{initial}' to running instance; exiting.")
            return 0
        collector = JoinCollector("split")
        collector.add_path(initial)

    win = MainWindow(initial_file=initial if collector is None else None,
                     collector=collector)
    win.show()
    return app.exec()


def _run_join(app: QApplication, path: str | None, mode: str) -> int:
    """`mode` is 'custom' or 'quick'."""
    from join_ipc import try_forward_to_running, JoinCollector

    # If a peer is already collecting, hand off and exit.
    if path and try_forward_to_running(path, mode):
        _log(f"join.{mode}: forwarded '{path}' to running instance; exiting.")
        return 0

    _log(f"join.{mode}: became server. initial='{path}'")
    collector = JoinCollector(mode)
    if path:
        collector.add_path(path)

    if mode == "custom":
        from join_window import JoinWindow
        win = JoinWindow(collector=collector)
        win.show()
    else:
        from quick_join_dialog import QuickJoinDialog
        dlg = QuickJoinDialog(collector)
        dlg.show()

    return app.exec()


def main() -> int:
    args = _parse_args()
    _log(f"startup argv={sys.argv}")

    # Headless verbs — no QApplication needed.
    if args.extract_first_frame:
        return _run_extract_frame_headless(args.path, "first")
    if args.extract_last_frame:
        return _run_extract_frame_headless(args.path, "last")

    _install_crash_handlers()

    app = QApplication(sys.argv)
    app.setApplicationName("AutoSplit")
    app.setOrganizationName("AutoSplitMedia")
    _set_icon(app)

    # Route via invocation name: when launched as AutoSplitJoiner.exe (a hard
    # link registered separately in Windows' Open-with list), always open the
    # joiner regardless of flags. This lets us present Splitter and Joiner as
    # two distinct Windows apps while sharing one binary.
    invoked_as = os.path.basename(sys.argv[0]).lower()
    if invoked_as.startswith("autosplitjoiner"):
        return _run_join(app, args.path, "custom")

    if args.join_custom:
        return _run_join(app, args.path, "custom")
    if args.join_quick:
        return _run_join(app, args.path, "quick")

    initial = args.path if args.path and not args.path.startswith("-") else None
    return _run_splitter(app, initial)


if __name__ == "__main__":
    sys.exit(main())
