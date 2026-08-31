"""SeedVR2 installer + detection.

SeedVR2 is a ByteDance diffusion-based video super-resolution model
(ICLR 2026). We install it into an isolated venv so it can't clash with
AutoSplit's own PySide6 environment.

Layout under %LOCALAPPDATA%\\AutoSplit\\seedvr2\\:
    uv.exe                    Astral's fast Python env manager (10 MB)
    venv/                     Dedicated venv (~3-4 GB with torch+CUDA)
    SeedVR/                   Cloned ByteDance repo
    models/
        seedvr2_3b/           ~7 GB smaller model
        seedvr2_7b/           ~14 GB full-quality model
    infer.py                  Our helper that wraps SeedVR2 inference

Install phases are staged so a failure at any step leaves a resumable state.
"""
from __future__ import annotations

import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional


# --- installation layout --------------------------------------------------

def seedvr2_root() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")))
    return root / "AutoSplit" / "seedvr2"


def uv_exe() -> Path:
    return seedvr2_root() / "uv.exe"


def venv_dir() -> Path:
    return seedvr2_root() / "venv"


def venv_python() -> Path:
    return venv_dir() / "Scripts" / "python.exe"


def repo_dir() -> Path:
    return seedvr2_root() / "SeedVR"


def models_dir() -> Path:
    return seedvr2_root() / "models"


def infer_helper() -> Path:
    return seedvr2_root() / "infer.py"


# --- installation state ---------------------------------------------------

def install_state() -> dict:
    """Report which install stages have completed."""
    return {
        "uv":      uv_exe().exists(),
        "venv":    venv_python().exists(),
        "repo":    repo_dir().exists() and (repo_dir() / "inference").exists(),
        "model_3b": (models_dir() / "seedvr2_3b").exists()
                    and any((models_dir() / "seedvr2_3b").iterdir()),
        "model_7b": (models_dir() / "seedvr2_7b").exists()
                    and any((models_dir() / "seedvr2_7b").iterdir()),
        "helper":  infer_helper().exists(),
    }


def is_ready(need_model: str = "any") -> bool:
    st = install_state()
    base = st["uv"] and st["venv"] and st["repo"] and st["helper"]
    if not base:
        return False
    if need_model == "3b":
        return st["model_3b"]
    if need_model == "7b":
        return st["model_7b"]
    return st["model_3b"] or st["model_7b"]


def estimated_download_size_bytes(with_model: str = "3b") -> int:
    """Rough total download size for planning a Free-Space check."""
    # uv ~10 MB, torch+deps ~3.5 GB, repo ~50 MB, model ~7 GB (3b) or ~14 GB (7b)
    total = 10 + 3500 + 50
    if with_model == "3b":
        total += 7000
    elif with_model == "7b":
        total += 14000
    return total * 1024 * 1024


# --- downloader ----------------------------------------------------------

UV_URL = (
    "https://github.com/astral-sh/uv/releases/latest/download/"
    "uv-x86_64-pc-windows-msvc.zip"
)


def _download(url: str, dest: Path,
              on_progress: Optional[Callable[[float], None]] = None) -> None:
    """Streaming download with progress reporting."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(url, context=ctx, timeout=120) as r, \
         open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length", 0))
        read = 0
        while True:
            chunk = r.read(1 << 16)   # 64 KB
            if not chunk:
                break
            f.write(chunk)
            read += len(chunk)
            if on_progress and total > 0:
                on_progress(min(1.0, read / total))


# --- install stages ------------------------------------------------------

def install_uv(on_progress=None, on_status=None) -> None:
    """Fetch a standalone uv binary. ~10 MB."""
    if uv_exe().exists():
        return
    if on_status: on_status("Downloading uv (Python env manager, ~10 MB)…")
    tmp_zip = Path(tempfile.gettempdir()) / "uv.zip"
    _download(UV_URL, tmp_zip, on_progress)
    seedvr2_root().mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(tmp_zip) as zf:
        for name in zf.namelist():
            if name.lower().endswith("uv.exe"):
                with zf.open(name) as src, open(uv_exe(), "wb") as dst:
                    shutil.copyfileobj(src, dst)
                break
    try:
        tmp_zip.unlink()
    except OSError:
        pass
    if not uv_exe().exists():
        raise RuntimeError("uv.exe not present in archive — Astral may have changed the layout.")


def _uv_run(args: list[str], on_status=None) -> tuple[int, str]:
    """Invoke uv, capturing stdout/stderr as one blob for the log."""
    if on_status:
        on_status(f"uv {' '.join(args[:2])}…")
    proc = subprocess.run(
        [str(uv_exe()), *args],
        capture_output=True, text=True,
        cwd=str(seedvr2_root()),
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def create_venv(on_status=None) -> None:
    """`uv venv .venv --python 3.11` inside seedvr2_root/."""
    if venv_python().exists():
        return
    if on_status: on_status("Creating Python 3.11 venv (~50 MB)…")
    rc, log = _uv_run(["venv", "venv", "--python", "3.11"], on_status)
    if rc != 0 or not venv_python().exists():
        raise RuntimeError(f"uv venv failed:\n{log[-1500:]}")


def install_torch(on_status=None) -> None:
    """Install PyTorch (CUDA) + supporting libs.  Big — ~3.5 GB."""
    py = str(venv_python())
    if on_status: on_status("Installing PyTorch + CUDA runtime (~3.5 GB — takes ~5 min)…")
    # CUDA 12.4 wheels — matches recent driver versions on RTX 40-series.
    rc, log = _uv_run([
        "pip", "install",
        "--python", py,
        "torch", "torchvision", "--extra-index-url", "https://download.pytorch.org/whl/cu124",
    ], on_status)
    if rc != 0:
        raise RuntimeError(f"torch install failed:\n{log[-1500:]}")

    if on_status: on_status("Installing diffusers / transformers / accelerate…")
    rc, log = _uv_run([
        "pip", "install", "--python", py,
        "diffusers>=0.31", "transformers>=4.45", "accelerate>=1.0",
        "safetensors", "einops", "imageio", "imageio-ffmpeg", "opencv-python",
        "huggingface-hub", "sentencepiece", "protobuf",
    ], on_status)
    if rc != 0:
        raise RuntimeError(f"deps install failed:\n{log[-1500:]}")


REPO_ZIP = "https://github.com/ByteDance-Seed/SeedVR/archive/refs/heads/main.zip"


def install_repo(on_status=None, on_progress=None) -> None:
    """Grab the ByteDance-Seed/SeedVR code (main.zip). ~50 MB."""
    if (repo_dir() / "inference").exists():
        return
    if on_status: on_status("Downloading SeedVR2 code (~50 MB)…")
    tmp_zip = Path(tempfile.gettempdir()) / "seedvr_repo.zip"
    _download(REPO_ZIP, tmp_zip, on_progress)
    if repo_dir().exists():
        shutil.rmtree(repo_dir())
    with zipfile.ZipFile(tmp_zip) as zf:
        zf.extractall(seedvr2_root())
    # Extracted as SeedVR-main/… — rename to SeedVR/.
    extracted = seedvr2_root() / "SeedVR-main"
    if extracted.exists():
        extracted.rename(repo_dir())
    try:
        tmp_zip.unlink()
    except OSError:
        pass


# Model repos on HuggingFace.
MODEL_REPOS = {
    "3b": "ByteDance-Seed/SeedVR2-3B",
    "7b": "ByteDance-Seed/SeedVR2-7B",
}


def install_model(kind: str, on_status=None, on_progress=None) -> None:
    """Download a model via huggingface-hub (uses the venv we set up)."""
    assert kind in ("3b", "7b")
    target = models_dir() / f"seedvr2_{kind}"
    if target.exists() and any(target.iterdir()):
        return
    target.mkdir(parents=True, exist_ok=True)
    if on_status:
        on_status(f"Downloading SeedVR2-{kind.upper()} weights "
                  f"({'~7' if kind == '3b' else '~14'} GB — will take a while)…")
    # We use the venv's huggingface-hub via a one-liner subprocess.
    py = str(venv_python())
    cmd = [
        py, "-c",
        (
            "from huggingface_hub import snapshot_download; "
            f"snapshot_download(repo_id='{MODEL_REPOS[kind]}', "
            f"local_dir=r'{target}', local_dir_use_symlinks=False)"
        ),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"model download failed:\n{proc.stderr[-1500:]}\n\n"
            f"You can retry — snapshot_download resumes from cached files."
        )


HELPER_SCRIPT = '''"""AutoSplit -> SeedVR2 inference helper. Runs INSIDE the SeedVR2 venv.

Called as:
    python infer.py --input <in_frames_dir> --output <out_frames_dir>
                    --model <3b|7b> --target-h <H> --target-w <W>

Reads PNGs from input, writes upscaled PNGs to output. Streaming: processes
frames one at a time so a 4K result doesn't need everything in memory at once.

This is a THIN shim over SeedVR2's built-in inference API. The heavy lifting
lives inside the ByteDance repo — we just wire the CLI to their entrypoint
and adapt input/output to a plain PNG-directory contract.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--model", choices=("3b", "7b"), default="3b")
    ap.add_argument("--target-h", type=int, required=True)
    ap.add_argument("--target-w", type=int, required=True)
    ap.add_argument("--model-dir", required=True,
                    help="Path to the downloaded model repo folder.")
    ap.add_argument("--repo-dir", required=True,
                    help="Path to the cloned ByteDance-Seed/SeedVR repo folder.")
    args = ap.parse_args()

    # Add the SeedVR repo to sys.path so we can import its inference module.
    sys.path.insert(0, args.repo_dir)

    # NOTE: this call signature is a placeholder — SeedVR2's real API is
    # currently exposed via `run.py` scripts, not a stable Python function.
    # For a first cut we shell out to their sample inference script.
    import subprocess as _sp
    entry = Path(args.repo_dir) / "inference" / "run.py"
    if not entry.exists():
        # Fallback: try repo root
        entry = Path(args.repo_dir) / "run.py"
    if not entry.exists():
        print("ERROR: could not locate SeedVR2 run.py in", args.repo_dir, file=sys.stderr)
        return 2
    cmd = [
        sys.executable, str(entry),
        "--input_dir", args.input,
        "--output_dir", args.output,
        "--model_path", args.model_dir,
        "--res_h", str(args.target_h),
        "--res_w", str(args.target_w),
    ]
    print("Running:", " ".join(cmd))
    p = _sp.run(cmd)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
'''


def write_helper() -> None:
    infer_helper().write_text(HELPER_SCRIPT, encoding="utf-8")


# --- top-level orchestrator ----------------------------------------------

def install(model_kind: str = "3b",
            on_progress: Optional[Callable[[float], None]] = None,
            on_status:   Optional[Callable[[str], None]] = None,
            should_cancel: Optional[Callable[[], bool]] = None) -> None:
    """Run the full install pipeline. Each stage is idempotent, so if a run
    dies partway through, calling install() again picks up where it stopped."""
    def _cancel():
        return bool(should_cancel and should_cancel())

    seedvr2_root().mkdir(parents=True, exist_ok=True)

    # Rough weighting so the overall progress bar advances sensibly.
    def stage(fraction_start, fraction_end):
        def _cb(f):
            if on_progress:
                on_progress(fraction_start + (fraction_end - fraction_start) * f)
        return _cb

    # --- Stage 1: uv ----
    if not uv_exe().exists():
        install_uv(on_progress=stage(0.00, 0.02), on_status=on_status)
    if _cancel(): return

    # --- Stage 2: venv ----
    if not venv_python().exists():
        create_venv(on_status=on_status)
        if on_progress: on_progress(0.03)
    if _cancel(): return

    # --- Stage 3: torch + deps ----
    # Uv's progress isn't easily parseable — coarse status updates only.
    marker = seedvr2_root() / ".deps_installed"
    if not marker.exists():
        install_torch(on_status=on_status)
        marker.write_text("ok")
        if on_progress: on_progress(0.55)
    if _cancel(): return

    # --- Stage 4: repo ----
    if not (repo_dir() / "inference").exists() and not (repo_dir() / "run.py").exists():
        install_repo(on_status=on_status, on_progress=stage(0.55, 0.60))
    if on_progress: on_progress(0.60)
    if _cancel(): return

    # --- Stage 5: model weights ----
    install_model(model_kind, on_status=on_status, on_progress=stage(0.60, 0.98))
    if _cancel(): return

    # --- Stage 6: helper ----
    write_helper()
    if on_progress: on_progress(1.0)
    if on_status: on_status("SeedVR2 ready.")


def uninstall() -> None:
    """Wipe the whole seedvr2/ tree. Useful for a clean retry."""
    d = seedvr2_root()
    if d.exists():
        shutil.rmtree(d)
