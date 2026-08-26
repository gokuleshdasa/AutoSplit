# AutoSplit

Windows desktop app for **frame-accurate splitting** and **joining** of audio and video files, with a Windows Explorer right-click menu.

**Two modules in one app:**

- **Splitter** — cut a media file into equal-duration parts, or into named regions you mark yourself with IN/OUT (I/O keys).
- **Joiner** — combine multiple files into one. Auto-picks stream-copy when possible (instant, zero quality loss), re-encodes when clips have different specs.

---

## Install

Download the latest release zip, extract, then:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install.ps1
```

Per-user; no admin required. Uninstall from Windows *Add or remove programs*, or run `Uninstall.ps1`.

See [HELP.md](HELP.md) for the full user guide.

---

## What's inside

- **Python 3.11+ / PySide6** — Fluent-styled Windows UI
- **FFmpeg 7.1.1** (bundled — no separate install)
- **QThreadPool** for background probes / thumbnails / waveforms — with disk cache
- **Single-instance IPC** (QLocalServer) for multi-file Explorer right-click

---

## Feature list

### Splitter
- Timeline scrubber with **live audio waveform** rendered by `ffmpeg showwavespic`
- Auto-split by duration, or export N named marker-regions
- IN/OUT markers, **Alt+drag** to highlight-and-name a region, right-click menu on the timeline
- Every popular output format: MP4 (H.264/H.265), MOV (H.264/ProRes), MKV, WebM, AVI, MP3, WAV, M4A, FLAC, OGG (Opus / Vorbis)
- Audio bitrate 96k–320k, video quality CRF 16–26
- Prefix + zero-padded number + suffix naming
- Fully remappable keyboard shortcuts (Preferences → Keyboard)

### Joiner
- Big video preview + waveform on the left, thumbnails + settings on the right
- **Thumbnails** grabbed at ~10% of duration (with 0.5s / 0.0s fallbacks for short clips) — cached to disk so re-adding is instant
- Drag-reorder, natural-sort by filename, reverse, per-clip preview, play joined preview
- **Compatibility check** — green badge when fast-join (stream copy) is possible; amber with reason when re-encode is required
- Same format / bitrate / quality picker as the splitter
- **Ctrl+V** to paste files copied from Explorer

### Explorer integration
Right-click any media file (or files):

```
AutoSplit ▸
    Split…
    Join (Quick)
    Join (Customize)…
```

Multi-selection works via a single-instance IPC (`QLocalServer`) with a 500 ms debounce.

---

## Build from source

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install pyinstaller

# Drop ffmpeg.exe + ffprobe.exe into vendor/ffmpeg/  (Gyan.dev full build)
python build.py

# Then install locally:
powershell -ExecutionPolicy Bypass -File .\dist\AutoSplit\Install.ps1
```

Runs from source too:

```powershell
python src\main.py                       # splitter
python src\main.py --join-custom         # joiner
```

---

## Project layout

```
src/
  main.py                entry point; dispatches --join-quick / --join-custom
  main_window.py         Splitter UI
  join_window.py         Joiner UI
  quick_join_dialog.py   headless progress window for --join-quick
  timeline.py            custom timeline widget (waveform + IN/OUT + regions)
  preferences_dialog.py  keyboard + playback + export defaults tabs
  async_tasks.py         QThreadPool workers for probe / thumbnail / waveform + disk cache
  export_worker.py       segment export runner
  join_utils.py          concat compatibility check, fast + re-encode join builders
  join_ipc.py            QLocalServer single-instance IPC for multi-file right-click
  ffmpeg_utils.py        ffprobe wrapper, format catalog, segment planner
  regions.py             Region model + filename sanitizer
  settings.py            QSettings persistence + keybinding defaults
  shell_integration.py   install/uninstall the nested Explorer menu
  waveform.py            (legacy shim — replaced by async_tasks.submit_waveform)
vendor/ffmpeg/           ffmpeg.exe + ffprobe.exe (from Gyan.dev builds)
assets/                  app.ico
build.py                 PyInstaller build script
Install.ps1              installer
Uninstall.ps1            uninstaller
HELP.md                  user guide
```

---

## License

MIT — see [LICENSE](LICENSE).

FFmpeg is bundled under the LGPL 2.1+ (Gyan.dev "essentials" build). See <https://ffmpeg.org/legal.html>.
