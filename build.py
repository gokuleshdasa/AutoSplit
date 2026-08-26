"""Build a self-contained Windows distribution with PyInstaller.

Usage:
    python build.py

Produces:
    dist/AutoSplit/AutoSplit.exe        (main binary — no console)
    dist/AutoSplit/vendor/ffmpeg/*.exe  (bundled FFmpeg)
    dist/AutoSplit/Install.ps1
    dist/AutoSplit/Uninstall.ps1
    dist/AutoSplit/HELP.md
    dist/AutoSplit/README.md
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
VENDOR = ROOT / "vendor" / "ffmpeg"
ASSETS = ROOT / "assets"
ICON = ASSETS / "app.ico"


def _fail(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    if sys.platform != "win32":
        _fail("This build script targets Windows.")

    for exe in ("ffmpeg.exe", "ffprobe.exe"):
        if not (VENDOR / exe).exists():
            _fail(f"Missing bundled {exe} at {VENDOR}. "
                  "Copy the Gyan.dev full build's bin/ffmpeg.exe and ffprobe.exe there before building.")

    # Clean previous.
    for d in ("build", "dist"):
        p = ROOT / d
        if p.exists():
            shutil.rmtree(p)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", "AutoSplit",
        "--windowed",                # no console window
        "--paths", str(SRC),
        # PyInstaller doesn't auto-collect PySide6 QtNetwork used by our
        # single-instance IPC; --collect-submodules covers the essentials.
        "--collect-submodules", "PySide6",
        "--collect-binaries", "PySide6",
        f"--add-data={VENDOR}{os.pathsep}vendor/ffmpeg",
    ]
    if ICON.exists():
        args += ["--icon", str(ICON)]
    args.append(str(SRC / "main.py"))

    print("Running PyInstaller:")
    print("  " + " ".join(args))
    rc = subprocess.call(args, cwd=str(ROOT))
    if rc != 0:
        _fail(f"PyInstaller exited with {rc}")

    dist_dir = ROOT / "dist" / "AutoSplit"
    if not dist_dir.exists():
        _fail(f"Expected dist folder not found: {dist_dir}")

    # Copy support files into the distribution.
    for f in ("Install.ps1", "Uninstall.ps1", "HELP.md", "README.md"):
        src = ROOT / f
        if src.exists():
            shutil.copy2(src, dist_dir / f)
            print(f"  copied  {f}")

    print()
    print(f"✓ Built: {dist_dir}")
    print(f"  Binary: {dist_dir}\\AutoSplit.exe")
    print()
    print("To install on this machine:")
    print(f"  powershell -ExecutionPolicy Bypass -File \"{dist_dir}\\Install.ps1\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
