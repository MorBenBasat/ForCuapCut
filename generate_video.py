#!/usr/bin/env python3
"""Build an MP4 song slide with per-chord green highlight synced to a timeline."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from generate import (
  ROOT,
  generate_song_slide,
  load_config,
  load_yaml,
  resolve_artist_image,
  song_slug,
)

DEFAULT_SEGMENT_SECONDS = 4.0


def _capture_output(cmd: list[str], *, check: bool = False) -> tuple[str, str]:
  """Run a subprocess and always return decoded stdout/stderr strings."""
  result = subprocess.run(
    cmd,
    capture_output=True,
    encoding="utf-8",
    errors="replace",
    check=check,
  )
  return result.stdout or "", result.stderr or ""


def get_ffprobe_exe(ffmpeg: str) -> str | None:
  ffprobe = shutil.which("ffprobe")
  if ffprobe:
    return ffprobe
  ffprobe_candidate = Path(ffmpeg).with_name(
    "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
  )
  if ffprobe_candidate.is_file():
    return str(ffprobe_candidate)
  return None


def get_ffmpeg_exe() -> str:
  system = shutil.which("ffmpeg")
  if system:
    return system
  try:
    import imageio_ffmpeg
  except ImportError as exc:
    raise SystemExit(
      "ffmpeg not found. Install ffmpeg or: pip install imageio-ffmpeg"
    ) from exc
  return imageio_ffmpeg.get_ffmpeg_exe()


def probe_audio_duration(audio_path: Path, ffmpeg: str) -> float:
  ffprobe = get_ffprobe_exe(ffmpeg)
  if ffprobe:
    stdout, _stderr = _capture_output(
      [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
      ],
      check=True,
    )
    duration = stdout.strip()
    if duration:
      return float(duration)

  # Fallback: ffmpeg prints duration in stderr when probing input.
  _stdout, stderr = _capture_output([ffmpeg, "-i", str(audio_path)])
  for line in stderr.splitlines():
    if "Duration:" in line:
      time_part = line.split("Duration:", 1)[1].split(",", 1)[0].strip()
      hours, minutes, seconds = time_part.split(":")
      return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
  raise RuntimeError(f"Could not read audio duration: {audio_path}")


def parse_timeline(data: dict, chord_count: int) -> list[tuple[int, float]]:
  """Return sorted (chord_index_1based, start_seconds) pairs."""
  raw = data.get("timeline")
  if not raw:
    raise ValueError(
      "Missing timeline. Example:\n"
      "timeline:\n"
      "  - { chord: 1, at: 0 }\n"
      "  - { chord: 2, at: 4 }"
    )

  entries: list[tuple[int, float]] = []
  for item in raw:
    if not isinstance(item, dict):
      raise ValueError(f"Invalid timeline entry: {item!r}")
    chord = int(item["chord"])
    at = float(item["at"])
    if not 1 <= chord <= chord_count:
      raise ValueError(f"chord must be 1-{chord_count}, got {chord}")
    if at < 0:
      raise ValueError(f"at must be >= 0, got {at}")
    entries.append((chord, at))

  entries.sort(key=lambda pair: pair[1])
  if not entries:
    raise ValueError("timeline is empty")
  if entries[0][1] != 0:
    raise ValueError("First timeline entry must start at at: 0")
  return entries


def build_segments(
  timeline: list[tuple[int, float]],
  *,
  total_seconds: float,
) -> list[tuple[int, float]]:
  """Map timeline to (chord_index, duration_seconds) segments."""
  segments: list[tuple[int, float]] = []
  for i, (chord, start) in enumerate(timeline):
    if i + 1 < len(timeline):
      duration = timeline[i + 1][1] - start
    else:
      duration = total_seconds - start
    if duration <= 0:
      raise ValueError(
        f"Segment for chord {chord} at {start}s has non-positive duration ({duration})"
      )
    segments.append((chord, duration))
  return segments


def resolve_total_seconds(data: dict, audio_path: Path | None, ffmpeg: str) -> float:
  if "end" in data:
    return float(data["end"])
  if audio_path and audio_path.is_file():
    return probe_audio_duration(audio_path, ffmpeg)
  segment_default = float(data.get("segment_seconds", DEFAULT_SEGMENT_SECONDS))
  timeline = parse_timeline(data, len(data["chords"]))
  return timeline[-1][1] + segment_default


def write_concat_file(frames: list[tuple[Path, float]], concat_path: Path) -> None:
  lines: list[str] = []
  for frame_path, duration in frames:
    escaped = str(frame_path).replace("'", "'\\''")
    lines.append(f"file '{escaped}'")
    lines.append(f"duration {duration:.3f}")
  if frames:
    last = frames[-1][0]
    escaped = str(last).replace("'", "'\\''")
    lines.append(f"file '{escaped}'")
  concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_video(
  *,
  frames: list[tuple[Path, float]],
  audio_path: Path | None,
  output: Path,
  fps: int,
) -> Path:
  ffmpeg = get_ffmpeg_exe()
  output.parent.mkdir(parents=True, exist_ok=True)

  with tempfile.TemporaryDirectory(prefix="forcuapcut_") as tmp:
    concat_path = Path(tmp) / "concat.txt"
    write_concat_file(frames, concat_path)

    cmd = [
      ffmpeg,
      "-y",
      "-f",
      "concat",
      "-safe",
      "0",
      "-i",
      str(concat_path),
    ]
    if audio_path and audio_path.is_file():
      cmd.extend(["-i", str(audio_path)])
    cmd.extend(
      [
        "-vf",
        f"fps={fps}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
      ]
    )
    if audio_path and audio_path.is_file():
      cmd.extend(["-c:a", "aac", "-shortest"])
    else:
      cmd.append("-an")
    cmd.append(str(output))

    subprocess.run(cmd, check=True, encoding="utf-8", errors="replace")
  return output


def generate_song_video_from_data(
  data: dict,
  *,
  config: dict,
  config_path: Path,
  source_path: Path | None = None,
) -> Path:
  artist = data["artist"]
  song = data["song"]
  chord_names = [str(c) for c in data["chords"]]
  difficulty = data.get("difficulty", "קל")
  fps = int(data.get("fps", 30))

  artist_image = resolve_artist_image(config, artist, data.get("artist_image"))
  background = ROOT / data["background"] if data.get("background") else None
  if background and not background.is_absolute():
    background = ROOT / background

  audio_field = data.get("audio")
  audio_path: Path | None = None
  if audio_field:
    audio_path = Path(audio_field)
    if not audio_path.is_absolute():
      audio_path = ROOT / audio_path
    if not audio_path.is_file():
      raise FileNotFoundError(f"Audio not found: {audio_path}")

  slug = song_slug(song)
  output_field = data.get("output")
  if output_field:
    output = Path(output_field)
    if not output.is_absolute():
      output = ROOT / output
  else:
    stem = source_path.stem if source_path else slug
    output = ROOT / f"output/{stem}.mp4"

  timeline = parse_timeline(data, len(chord_names))
  ffmpeg = get_ffmpeg_exe()
  total_seconds = resolve_total_seconds(data, audio_path, ffmpeg)
  segments = build_segments(timeline, total_seconds=total_seconds)

  with tempfile.TemporaryDirectory(prefix="forcuapcut_frames_") as tmp:
    tmp_path = Path(tmp)
    frame_entries: list[tuple[Path, float]] = []
    for seg_i, (chord_index, duration) in enumerate(segments):
      frame_path = tmp_path / f"frame_{seg_i:03d}.png"
      generate_song_slide(
        artist=artist,
        song=song,
        chord_names=chord_names,
        artist_image=artist_image,
        background=background,
        output=frame_path,
        config=config,
        difficulty=difficulty,
        highlight_index=chord_index,
      )
      frame_entries.append((frame_path, duration))

    return render_video(
      frames=frame_entries,
      audio_path=audio_path,
      output=output,
      fps=fps,
    )


def main() -> None:
  parser = argparse.ArgumentParser(
    description="Generate MP4 with chord highlight synced to timeline"
  )
  parser.add_argument("video_file", help="YAML with chords + timeline + optional audio")
  parser.add_argument("--config", default="config.json", help="Layout config path")
  args = parser.parse_args()

  config_path = Path(args.config)
  if not config_path.is_absolute():
    config_path = ROOT / config_path
  config = load_config(config_path)

  video_path = Path(args.video_file)
  if not video_path.is_absolute():
    video_path = ROOT / video_path
  data = load_yaml(video_path)

  result = generate_song_video_from_data(
    data,
    config=config,
    config_path=config_path,
    source_path=video_path,
  )
  print(f"Created: {result}")


if __name__ == "__main__":
  main()
