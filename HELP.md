# AutoSplit — Help

AutoSplit is a Windows app that does two jobs:

- **Split** any audio or video file into equal-duration segments, or into named regions you mark yourself.
- **Join** multiple audio/video files into a single file.

Both are frame-accurate (segments and joins land on exact times), both output every popular format, and both integrate with Windows Explorer's right-click menu.

---

## Installing

1. Download / extract the distribution zip.
2. Right-click **`Install.ps1`** → **Run with PowerShell**.
   (Or from a PowerShell window: `powershell -ExecutionPolicy Bypass -File .\Install.ps1`.)
3. When it finishes it asks "Launch now? Y/N".

The installer is per-user — no admin rights needed. It writes to `%LOCALAPPDATA%\Programs\AutoSplit`, adds a Start Menu shortcut, and registers the right-click menu.

**To uninstall:** Start Menu → *Add or remove programs* → **AutoSplit** → *Uninstall*. Or run `Uninstall.ps1` from the install folder.

---

## Windows Explorer right-click

Select one or more audio/video files, right-click → **Show more options** *(Windows 11)* → **AutoSplit ▸**

- **Split…** — opens the Splitter with the right-clicked file.
- **Join (Quick)** — natural-sorts all selected files by name, joins them with your default settings, writes the output to your Preferences folder. No dialog beyond a small progress window.
- **Join (Customize)…** — opens the Joiner window with all selected files loaded so you can reorder, retrim, choose formats, etc.

If you don't see the menu after installing, sign out and back into Windows (or restart Explorer with `taskkill /F /IM explorer.exe` then `start explorer` — Explorer re-reads the registry on next launch).

---

## Splitter

### File input
- Right-click a media file in Explorer → **AutoSplit ▸ Split…**
- Or in-app: **Open File…**, **drag & drop**, or **Ctrl+V** to paste a file copied from Explorer.

### Two export modes
Radio buttons at the top of the right panel:

- **Auto-split by duration** — set a length (e.g. `10:00`), an optional Start and End (IN/OUT), press Export. You get sequential parts of that length.
- **Marked regions** — set IN/OUT for each region you want, name it, add it. Each region becomes one output file.

### Marking regions (the workflow you'll use most)
- Play through the clip (Space).
- Press **I** to mark IN at the playhead; press **O** to mark OUT.
- Press **M** (or the **Add** button) to save the region — you'll be asked for a name.
- Or **Alt+drag** on the waveform to highlight a range; on release you're prompted for a name and the region is added directly.
- **Right-click the timeline** for a context menu — Set IN / Set OUT / Add region / Rename / Delete.

### Keyboard defaults (all remappable in Preferences → Keyboard)

| Key | Action |
|---|---|
| Space | Play / Pause |
| ← / → | Nudge playhead (1 s, or 1 frame if snap is on) |
| Ctrl+← / Ctrl+→ | Jump 5 s |
| Shift+← / Shift+→ | Jump 10 s |
| S | Toggle snap between 1 s and 1 frame |
| I / O | Mark IN / OUT |
| M | Add region from IN/OUT |
| Ctrl+O | Open file |
| Ctrl+V | Paste file(s) from Explorer clipboard |
| Ctrl+, | Preferences |

### Output
- **Video formats:** MP4 (H.264 / H.265), MOV (H.264 / ProRes 422), MKV, WebM (VP9/Opus), AVI (H.264/MP3).
- **Audio formats:** MP3, WAV (lossless), M4A (AAC), FLAC (lossless), OGG Opus, OGG Vorbis.
- **Audio bitrate** picker: 96k / 128k / 160k / 192k / 256k / 320k. Disabled for lossless formats.
- **Video quality** picker: CRF 16 (visually lossless) → CRF 26 (small file).
- **Naming:** `{prefix}{NNN}{suffix}{ext}` where NNN is a zero-padded number you choose the width of.
- **After export:** open output folder, close app, or overwrite vs auto-rename — all toggleable.

### Frame-accurate cuts
Every segment uses `ffmpeg -ss <after -i> -t <duration>` with re-encoding, so each output is *exactly* the requested length. You can drop the resulting parts back into an editing timeline in place of the original span.

---

## Joiner

### File input
- Select multiple files in Explorer → right-click → **AutoSplit ▸ Join (Customize)…**
- Or in-app: **Add files…**, **drag & drop** onto the window, or **Ctrl+V** to paste files from Explorer.

Multi-file right-click uses a single-instance IPC: the first Windows invocation opens the window, subsequent invocations for the same click send their paths and exit. There's a 500 ms debounce so every file lands before the sort.

### Layout
- **Left:** big video preview + waveform timeline + play controls.
- **Right:** thumbnails list (drag to reorder) + output settings.
- **Bottom:** progress bar + Join and Export button.

### Compatibility badge
Just below the waveform:

- **✓ Fast join available** (green) — every clip shares codec, resolution, framerate, sample rate → will **stream-copy** (instant, zero quality loss).
- **⚠ Re-encode required** (amber) — with the reason (e.g. "Clip 3: 1280×720 vs 1920×1080") → the join re-encodes to a consistent target.
- **Force re-encode** checkbox to always re-encode even when fast is possible.

### Preview
- Click a clip → its waveform loads on the timeline and the video preview shows a first frame.
- **Play selected** — plays just that clip.
- **Play joined preview** — plays clips back-to-back sequentially. ~100 ms gap at each cut (preview only; the exported file is seamless).

### Output
Same picker set as the Splitter. Filename defaults to `joined` — override in the Filename field. Auto-increments on conflicts.

### Quick vs Customize
- **Quick** picks up your Preferences-defined defaults (folder, format, bitrate, quality) and joins immediately. Great once your defaults are dialed in.
- **Customize** exposes every knob for one-off jobs.

---

## Preferences (Ctrl+, or the "Preferences…" button)

Three tabs:

- **Keyboard** — every action has a `QKeySequenceEdit` field. Click, press your combo. Duplicates are blocked.
- **Playback** — jump-key step sizes.
- **Export defaults** — audio bitrate, video quality preset used for new files.

Preferences are per-user, stored in the Windows registry under `HKCU\Software\AutoSplitMedia\AutoSplit`.

---

## Where files live

| What | Location |
|---|---|
| Program | `%LOCALAPPDATA%\Programs\AutoSplit\` |
| Start Menu shortcut | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\AutoSplit.lnk` |
| Thumbnail / waveform cache | `%LOCALAPPDATA%\AutoSplit\thumb_cache\` |
| Diagnostic log | `%TEMP%\autosplit.log` |
| User settings | `HKCU\Software\AutoSplitMedia\AutoSplit` (registry) |

Delete the cache folder any time — it just re-generates on next use.

---

## Troubleshooting

**"Right-click menu doesn't appear."**
Windows 11 hides classic verbs under "Show more options" (or Shift+F10). If it's still missing, open `%TEMP%\autosplit.log` — nothing there means Explorer never fired the verb; reinstall (`Install.ps1`) to re-register.

**"Preview video is black."**
Windows can't decode the file with the codecs it has. Common culprit: HEVC without the "HEVC Video Extensions" from the Microsoft Store. The waveform + join/export still work.

**"App hangs when I add many files."**
Shouldn't happen post-1.0 — probes and thumbnails run on a bounded thread pool with disk caching. If it does, share the tail of `%TEMP%\autosplit.log` and the number of files you dropped.

**"Export failed."**
The error dialog shows FFmpeg's last stderr lines. Common causes: no write permission in the output folder, disk full, or a source file whose codec doesn't map to the chosen container (e.g. VP9 into MP4).

---

## What "frame-accurate" actually means

- **Splitter:** every segment is exactly the requested duration. FFmpeg's `-ss` is placed *after* `-i` and the segment is re-encoded (libx264 or libmp3lame etc.), so the output starts on the exact requested millisecond. Trade-off: re-encoding takes real-time-ish CPU vs stream-copy which is instant.
- **Joiner fast mode** stream-copies (perfect, instant, no re-encode) when all clips have identical codec parameters.
- **Joiner re-encode mode** uses `-filter_complex concat` — every boundary is a clean cut, no glitches, but takes CPU.
