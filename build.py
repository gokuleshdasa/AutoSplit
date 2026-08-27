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
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
VENDOR = ROOT / "vendor" / "ffmpeg"
ASSETS = ROOT / "assets"
ICON = ASSETS / "app.ico"

# OneDrive locks files under its synced folders, so PyInstaller's build/
# and dist/ subfolders sometimes can't be deleted between runs. Build in a
# local temp location and copy the finished output back into ./dist afterwards.
WORK_ROOT = Path(tempfile.gettempdir()) / "AutoSplit_build"
WORK_BUILD = WORK_ROOT / "build"
WORK_DIST  = WORK_ROOT / "dist"


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

    # Clean previous work + dist output.
    for p in (WORK_BUILD, WORK_DIST, ROOT / "dist"):
        if p.exists():
            try:
                shutil.rmtree(p, ignore_errors=False)
            except PermissionError:
                # OneDrive can hold locks; try again after a brief pause.
                import time
                time.sleep(2)
                shutil.rmtree(p, ignore_errors=True)

    WORK_ROOT.mkdir(parents=True, exist_ok=True)

    # PyInstaller 6.14+ moved `_pyi_rth_utils` into PyInstaller/fake-modules/
    # (a location its own default module search does NOT scan). The PySide6
    # runtime hook imports it, so if we don't force-include it, the frozen
    # exe crashes at startup: `ModuleNotFoundError: No module named '_pyi_rth_utils'`.
    # We put its parent dir on --paths and mark it as a hidden import.
    import PyInstaller
    fake_modules = Path(PyInstaller.__file__).parent / "fake-modules"

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", "AutoSplit",
        "--windowed",
        "--paths", str(SRC),
        "--paths", str(fake_modules),
        "--workpath", str(WORK_BUILD),
        "--distpath", str(WORK_DIST),
        # Pull in Qt plugins (qwindows.dll platform, imageformats, mediaservice, etc.)
        # via the data + binaries side of PyInstaller's PySide6 hook.
        "--collect-all", "PySide6",
        # `--collect-all` can suppress inclusion of PyInstaller's own
        # _pyi_rth_utils helper (moved into fake-modules/ in 6.14+),
        # so force-include it explicitly.
        "--hidden-import", "_pyi_rth_utils",
        "--hidden-import", "_pyi_rth_utils.qt",
        "--hidden-import", "_pyi_rth_utils.tempfile",
        "--hidden-import", "_pyi_rth_utils._win32",
        # Dynamic-imported PySide6 modules the static analysis misses.
        "--hidden-import", "PySide6.QtNetwork",
        "--hidden-import", "PySide6.QtMultimedia",
        "--hidden-import", "PySide6.QtMultimediaWidgets",
        f"--add-data={VENDOR}{os.pathsep}vendor/ffmpeg",
    ]
    if ICON.exists():
        args += ["--icon", str(ICON)]
    args.append(str(SRC / "main.py"))

    print("Running PyInstaller (working in local temp to avoid OneDrive locks):")
    print(f"  work path: {WORK_BUILD}")
    print(f"  dist path: {WORK_DIST}")
    rc = subprocess.call(args, cwd=str(ROOT))
    if rc != 0:
        _fail(f"PyInstaller exited with {rc}")

    built = WORK_DIST / "AutoSplit"
    if not built.exists():
        _fail(f"Expected built folder not found: {built}")

    dist_dir = ROOT / "dist" / "AutoSplit"
    print(f"Copying build result -> {dist_dir}")
    dist_dir.parent.mkdir(parents=True, exist_ok=True)
    # OneDrive can hold locks on files inside the sync root; use robocopy so
    # existing files are updated rather than requiring a clean create, and
    # tolerate its 0-7 success range.
    if sys.platform == "win32":
        rc = subprocess.call(
            ["robocopy", str(built), str(dist_dir),
             "/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/MT:8"],
        )
        if rc >= 8:
            _fail(f"robocopy failed (exit {rc})")
    else:
        shutil.copytree(built, dist_dir, dirs_exist_ok=True)

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
