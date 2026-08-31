"""FFmpeg command builder for speed change (slow-motion / fast-forward)."""
from __future__ import annotations

from dataclasses import dataclass

import ffmpeg_utils as fu


@dataclass
class SpeedJob:
    input_path: str
    output_path: str
    start_seconds: float
    end_seconds: float                # <=0 means end of media
    speed: float                      # 1.0 = normal; 0.5 = half speed (slo-mo); 2.0 = 2x fast
    interpolate: bool = False         # use minterpolate for smooth motion
    interpolate_fps: int = 60
    keep_audio: bool = True
    video_crf: int = 18
    audio_bitrate: str = "192k"
    output_format: str = "mp4_h264"


def _atempo_chain(target: float) -> list[str]:
    """Return a list of 'atempo=x' filter strings whose product == `target`.

    ffmpeg's atempo caps individual steps to [0.5 .. 100], so extreme speeds
    require multi-step chains. Product accounting:
      target = 0.25 -> [atempo=0.5, atempo=0.5]        (0.5 * 0.5)
      target = 4.0  -> [atempo=2.0, atempo=2.0]        (2.0 * 2.0)
      target = 0.75 -> [atempo=0.75]                    (single step)
    """
    if target <= 0:
        return []
    filters: list[str] = []
    rem = float(target)
    while rem < 0.5:
        filters.append("atempo=0.5")
        rem *= 2.0
    while rem > 2.0:
        filters.append("atempo=2.0")
        rem /= 2.0
    if abs(rem - 1.0) > 0.001:
        filters.append(f"atempo={rem:.6f}")
    return filters


def build_speed_cmd(job: SpeedJob) -> list[str]:
    if job.speed <= 0:
        raise ValueError("Speed must be positive.")

    # Video: change presentation time stamps. Optionally prepend motion
    # interpolation for smooth playback (needed for aggressive slo-mo).
    setpts = f"setpts={1.0 / job.speed:.6f}*PTS"
    v_filters = []
    if job.interpolate and job.speed < 1.0:
        # `mci` (motion-compensated interpolation) is the best-quality mode.
        v_filters.append(
            f"minterpolate=fps={job.interpolate_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
        )
    v_filters.append(setpts)

    # Audio: chain of atempo steps that multiply to `speed`.
    a_chain = _atempo_chain(job.speed) if job.keep_audio else []

    cmd = [
        fu.ffmpeg_path(),
        "-y",
        "-hide_banner",
    ]
    if job.start_seconds > 0:
        cmd += ["-ss", f"{job.start_seconds:.3f}"]
    cmd += ["-i", job.input_path]
    if job.end_seconds > job.start_seconds:
        cmd += ["-t", f"{job.end_seconds - job.start_seconds:.3f}"]

    # Encoder pick — mirror ffmpeg_utils' catalog for the common cases.
    key = job.output_format
    crf = str(int(job.video_crf))
    ext_cmd_video: list[str] = []
    ext_cmd_audio: list[str] = []
    if key in ("mp4_h264", "mov_h264", "mkv_h264"):
        ext_cmd_video += ["-c:v", "libx264", "-preset", "veryfast",
                          "-crf", crf, "-pix_fmt", "yuv420p"]
        ext_cmd_audio += ["-c:a", "aac", "-b:a", job.audio_bitrate]
        if key in ("mp4_h264", "mov_h264"):
            ext_cmd_audio += ["-movflags", "+faststart"]
    elif key == "mp4_h265":
        ext_cmd_video += ["-c:v", "libx265", "-preset", "medium",
                          "-crf", crf, "-pix_fmt", "yuv420p", "-tag:v", "hvc1"]
        ext_cmd_audio += ["-c:a", "aac", "-b:a", job.audio_bitrate,
                          "-movflags", "+faststart"]
    elif key == "webm_vp9":
        ext_cmd_video += ["-c:v", "libvpx-vp9", "-b:v", "0",
                          "-crf", crf, "-row-mt", "1"]
        ext_cmd_audio += ["-c:a", "libopus", "-b:a", job.audio_bitrate]
    else:
        # Fallback: mp4/h264.
        ext_cmd_video += ["-c:v", "libx264", "-preset", "veryfast",
                          "-crf", crf, "-pix_fmt", "yuv420p"]
        ext_cmd_audio += ["-c:a", "aac", "-b:a", job.audio_bitrate,
                          "-movflags", "+faststart"]

    # Apply the video filter chain.
    cmd += ["-vf", ",".join(v_filters)]
    cmd += ext_cmd_video

    # Audio path.
    if job.keep_audio and a_chain:
        cmd += ["-af", ",".join(a_chain)]
        cmd += ext_cmd_audio
    elif job.keep_audio:
        # speed == 1.0 (rare) — no atempo needed but still copy the audio path.
        cmd += ext_cmd_audio
    else:
        cmd += ["-an"]

    cmd += ["-progress", "pipe:2", job.output_path]
    return cmd
