"""Detection + one-shot installer for rife-ncnn-vulkan.

We use the Vulkan-based build so it works on any modern GPU (NVIDIA / AMD / Intel)
without requiring CUDA. Ships as a self-contained .exe + models folder.

Install location:
  %LOCALAPPDATA%\\AutoSplit\\rife\\rife-ncnn-vulkan.exe
  %LOCALAPPDATA%\\AutoSplit\\rife\\rife-v4.6\\        (model files)
"""
from __future__ import annotations

import os
import shutil
import ssl
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional


# Official release: https://github.com/nihui/rife-ncnn-vulkan/releases
RIFE_URL = "https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-windows.zip"
RIFE_ZIP_SIZE_BYTES = 40_000_000    # approx; used for the progress bar's total


def rife_dir() -> Path:
    """The folder we install RIFE into."""
    root = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")))
    return root / "AutoSplit" / "rife"


def rife_exe() -> Path:
    return rife_dir() / "rife-ncnn-vulkan.exe"


def is_installed() -> bool:
    return rife_exe().exists()


def install(
    on_progress: Optional[Callable[[float], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
) -> Path:
    """Download and unpack RIFE. Returns the path to the exe on success.

    Raises RuntimeError on any failure.
    """
    dest = rife_dir()
    dest.mkdir(parents=True, exist_ok=True)
    tmp_zip = Path(tempfile.gettempdir()) / "rife-ncnn-vulkan.zip"

    if on_status:
        on_status("Downloading RIFE (~40 MB)…")

    # Some corporate networks intercept SSL; fall back cleanly.
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(RIFE_URL, context=ctx, timeout=60) as r, \
             open(tmp_zip, "wb") as f:
            total = int(r.headers.get("Content-Length", RIFE_ZIP_SIZE_BYTES))
            read = 0
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                if on_progress and total > 0:
                    on_progress(min(1.0, read / total))
    except Exception as e:
        raise RuntimeError(f"Download failed: {e}") from e

    if on_status:
        on_status("Extracting…")

    try:
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(dest)
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"Downloaded file was not a valid zip: {e}") from e

    # Extracted layout is: <dest>/rife-ncnn-vulkan-20221029-windows/{exe,models…}
    # Flatten it so rife_exe() resolves.
    inner = next((p for p in dest.iterdir() if p.is_dir() and "rife" in p.name.lower()), None)
    if inner is not None:
        for child in inner.iterdir():
            target = dest / child.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(child), str(target))
        try:
            inner.rmdir()
        except OSError:
            pass

    try:
        tmp_zip.unlink()
    except OSError:
        pass

    if not rife_exe().exists():
        raise RuntimeError(
            f"Install completed but exe not found at {rife_exe()}. "
            f"The release archive layout may have changed."
        )
    return rife_exe()


def available_models() -> list[str]:
    """Return the list of model folder names present in the RIFE dir."""
    d = rife_dir()
    if not d.exists():
        return []
    return sorted(
        p.name for p in d.iterdir()
        if p.is_dir() and p.name.startswith("rife-")
    )
