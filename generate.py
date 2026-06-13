#!/usr/bin/env python3
"""Generate TikTok song slides matching CapCut layout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from bidi.algorithm import get_display

ROOT = Path(__file__).resolve().parent


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def resolve_artist_image(config: dict, artist: str, explicit: str | None = None) -> Path:
  """Artist photo from YAML/CLI override, or config.json artists registry."""
  if explicit:
    path = Path(explicit)
    if not path.is_absolute():
      path = ROOT / path
    return path
  registry = config.get("artists", {})
  if artist not in registry:
    known = ", ".join(sorted(registry)) if registry else "(empty)"
    raise ValueError(
      f"Unknown artist '{artist}'. Add to config.json → artists, or set artist_image in YAML.\n"
      f"Known artists: {known}"
    )
  path = Path(registry[artist])
  if not path.is_absolute():
    path = ROOT / path
  return path


def song_slug(title: str) -> str:
  """Filesystem-safe output name from song title."""
  slug = title.strip().replace(" ", "_")
  for char in '\\/:*?"<>|':
    slug = slug.replace(char, "")
  return slug or "song"


def resolve_chord_image(chords_dir: Path, name: str) -> Path:
  """Find chord PNG by name (case-insensitive)."""
  variants = [
    name,
    name.upper(),
    name.lower(),
    name.capitalize(),
  ]
  for variant in variants:
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
      candidate = chords_dir / f"{variant}{ext}"
      if candidate.exists():
        return candidate
  raise FileNotFoundError(f"Chord image not found for '{name}' in {chords_dir}")


def size_from_scale(img: Image.Image, scale_pct: float, ref_height: int) -> tuple[int, int]:
  """CapCut scale % relative to canvas height at 100%."""
  target_h = max(1, int(ref_height * scale_pct / 100))
  ratio = target_h / img.height
  return max(1, int(img.width * ratio)), target_h


def capcut_y_to_center_y(y: float, canvas_h: int) -> int:
  """CapCut Y+ up → pixel center Y."""
  return int(canvas_h / 2 - y)


def layout_chord_positions(
  chord_sizes: list[tuple[int, int]],
  pattern: list[int],
  canvas_w: int,
  canvas_h: int,
  gap: int,
  top_y: float,
  bottom_y: float,
  center_offset_x: int = 0,
) -> list[tuple[int, int]]:
  """Place chords in centered rows with fixed gaps (no overlap)."""
  row_ys = [top_y, bottom_y]
  positions: list[tuple[int, int]] = []
  idx = 0
  for row_i, count in enumerate(pattern):
    row = chord_sizes[idx : idx + count]
    idx += count
    widths = [size[0] for size in row]
    heights = [size[1] for size in row]
    row_width = sum(widths) + gap * (count - 1)
    start_x = (canvas_w - row_width) // 2 + center_offset_x
    center_y = capcut_y_to_center_y(row_ys[row_i], canvas_h)
    x = start_x
    for width, height in zip(widths, heights, strict=True):
      positions.append((x, center_y - height // 2))
      x += width + gap
  return positions


def cover_resize(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
  """Scale and crop to fill target dimensions."""
  src_w, src_h = img.size
  scale = max(target_w / src_w, target_h / src_h)
  new_w, new_h = int(src_w * scale), int(src_h * scale)
  resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
  left = (new_w - target_w) // 2
  top = (new_h - target_h) // 2
  return resized.crop((left, top, left + target_w, top + target_h))


def load_font(config: dict, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
  for key in ("bold", "fallback"):
    path = config["fonts"].get(key)
    if path and Path(path).exists():
      return ImageFont.truetype(path, size)
  return ImageFont.load_default()


def draw_text_centered(
  draw: ImageDraw.ImageDraw,
  text: str,
  center_x: int,
  y: int,
  font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
  fill: str,
  shadow_color: str,
  shadow_offset: int,
  stroke_color: str | None = None,
  stroke_width: int = 0,
) -> None:
  display_text = get_display(text)
  bbox = draw.textbbox((0, 0), display_text, font=font, stroke_width=stroke_width)
  text_w = bbox[2] - bbox[0]
  x = center_x - text_w // 2
  if shadow_offset:
    draw.text(
      (x + shadow_offset, y + shadow_offset),
      display_text,
      font=font,
      fill=shadow_color,
      stroke_width=stroke_width,
      stroke_fill=stroke_color or shadow_color,
    )
  draw.text(
    (x, y),
    display_text,
    font=font,
    fill=fill,
    stroke_width=stroke_width,
    stroke_fill=stroke_color or fill,
  )


def pick_row_config(config: dict, chord_count: int) -> dict:
  rows = config["chord_grid"]["rows"]
  key = str(chord_count)
  if key not in rows:
    raise ValueError(
      f"Unsupported chord count {chord_count}. Supported: {', '.join(sorted(rows))}"
    )
  return rows[key]


def generate_song_slide(
  *,
  artist: str,
  song: str,
  chord_names: list[str],
  artist_image: Path,
  background: Path | None,
  output: Path,
  config: dict,
) -> Path:
  canvas_w = config["canvas"]["width"]
  canvas_h = config["canvas"]["height"]
  ref_h = config.get("scale_reference_height", canvas_h)

  if not (4 <= len(chord_names) <= 6):
    raise ValueError(f"Expected 4-6 chords, got {len(chord_names)}")

  grid_cfg = config["chord_grid"]
  row_cfg = pick_row_config(config, len(chord_names))
  pattern = row_cfg["pattern"]
  if sum(pattern) != len(chord_names):
    raise ValueError("Row pattern does not match chord count")

  bg_path = background or Path(config["background"])
  if not bg_path.exists():
    raise FileNotFoundError(f"Background not found: {bg_path}")
  if not artist_image.exists():
    raise FileNotFoundError(f"Artist image not found: {artist_image}")

  chords_dir = Path(config["chords_dir"])
  if not chords_dir.is_absolute():
    chords_dir = ROOT / chords_dir

  canvas = cover_resize(Image.open(bg_path).convert("RGB"), canvas_w, canvas_h)

  # Singer placement
  singer_cfg = config["singer"]
  singer_img = Image.open(artist_image).convert("RGBA")
  singer_w, singer_h = size_from_scale(singer_img, singer_cfg["scale"], ref_h)
  singer_img = singer_img.resize((singer_w, singer_h), Image.Resampling.LANCZOS)
  anchor = singer_cfg.get("anchor", "top_left")
  offset_x = int(singer_cfg.get("capcut_x", 0))
  offset_y = int(singer_cfg.get("y", singer_cfg.get("capcut_y", 0)))
  if anchor == "top_center":
    singer_x = (canvas_w - singer_w) // 2 + offset_x
    singer_y = offset_y
  else:
    singer_x = offset_x
    singer_y = offset_y
  canvas.paste(singer_img, (singer_x, singer_y), singer_img)

  # Chords — auto-centered rows with even gaps
  chord_scale = grid_cfg["scale"]
  chord_images: list[Image.Image] = []
  chord_sizes: list[tuple[int, int]] = []
  for chord_name in chord_names:
    chord_path = resolve_chord_image(chords_dir, chord_name)
    chord_img = Image.open(chord_path).convert("RGBA")
    cw, ch = size_from_scale(chord_img, chord_scale, ref_h)
    chord_images.append(chord_img.resize((cw, ch), Image.Resampling.LANCZOS))
    chord_sizes.append((cw, ch))

  positions = layout_chord_positions(
    chord_sizes,
    pattern,
    canvas_w,
    canvas_h,
    int(grid_cfg["horizontal_gap"]),
    float(row_cfg["top_y"]),
    float(row_cfg["bottom_y"]),
    int(grid_cfg.get("center_offset_x", 0)),
  )
  for chord_img, (tx, ty) in zip(chord_images, positions, strict=True):
    canvas.paste(chord_img, (tx, ty), chord_img)

  # Text layers: X centered, Y from top (CapCut text values)
  draw = ImageDraw.Draw(canvas)
  text_cfg = config["text"]

  artist_cfg = text_cfg["artist"]
  artist_font = load_font(config, artist_cfg["font_size"])
  draw_text_centered(
    draw,
    artist,
    int(canvas_w / 2 + artist_cfg["capcut_x"]),
    int(artist_cfg["capcut_y"]),
    artist_font,
    artist_cfg["color"],
    artist_cfg["shadow_color"],
    artist_cfg["shadow_offset"],
    artist_cfg.get("stroke_color"),
    int(artist_cfg.get("stroke_width", 0)),
  )

  song_cfg = text_cfg["song"]
  song_font = load_font(config, song_cfg["font_size"])
  draw_text_centered(
    draw,
    song,
    int(canvas_w / 2 + song_cfg["capcut_x"]),
    int(song_cfg["capcut_y"]),
    song_font,
    song_cfg["color"],
    song_cfg["shadow_color"],
    song_cfg["shadow_offset"],
    song_cfg.get("stroke_color"),
    int(song_cfg.get("stroke_width", 0)),
  )

  output.parent.mkdir(parents=True, exist_ok=True)
  canvas.save(output, "PNG", optimize=True)
  return output


def load_yaml(path: Path) -> dict:
  try:
    import yaml
  except ImportError as exc:
    raise SystemExit("PyYAML required: pip install pyyaml") from exc
  with path.open(encoding="utf-8") as f:
    return yaml.safe_load(f)


def apply_intro_overlay(canvas: Image.Image, overlay_cfg: dict) -> Image.Image:
  """Darken top/bottom so text reads cleanly on busy photo backgrounds."""
  if not overlay_cfg.get("enabled", True):
    return canvas

  w, h = canvas.size
  top_opacity = float(overlay_cfg.get("top_opacity", 0.42))
  bottom_opacity = float(overlay_cfg.get("bottom_opacity", 0.52))
  fade_ratio = float(overlay_cfg.get("fade_ratio", 0.32))
  fade_h = max(1, int(h * fade_ratio))

  overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
  draw = ImageDraw.Draw(overlay)
  for y in range(fade_h):
    alpha = int(255 * top_opacity * (1 - y / fade_h))
    draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
  for y in range(h - fade_h, h):
    alpha = int(255 * bottom_opacity * ((y - (h - fade_h)) / fade_h))
    draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

  return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def collect_intro_lines(data: dict) -> list[str]:
  if "lines" in data:
    return [str(line) for line in data["lines"] if line]
  lines = []
  for key in ("line1", "line2", "line3", "line4"):
    if key in data and data[key]:
      lines.append(str(data[key]))
  if len(lines) < 3:
    raise ValueError("Intro needs at least 3 text lines (line1, line2, line3)")
  return lines


def generate_intro_slide(
  *,
  lines: list[str],
  background: Path | None,
  output: Path,
  config: dict,
) -> Path:
  canvas_w = config["canvas"]["width"]
  canvas_h = config["canvas"]["height"]
  intro_cfg = config["intro"]
  style_templates = intro_cfg["line_styles"]

  if len(lines) > len(style_templates):
    raise ValueError(f"Intro supports up to {len(style_templates)} lines")

  bg_path = background or Path(config["background"])
  if not bg_path.is_absolute():
    bg_path = ROOT / bg_path if not bg_path.exists() else bg_path
  if not bg_path.exists():
    bg_path = ROOT / config["background"]
  if not bg_path.exists():
    raise FileNotFoundError(f"Background not found: {bg_path}")

  canvas = cover_resize(Image.open(bg_path).convert("RGB"), canvas_w, canvas_h)
  canvas = apply_intro_overlay(canvas, intro_cfg.get("overlay", {}))
  draw = ImageDraw.Draw(canvas)

  for index, text in enumerate(lines):
    style = style_templates[index]
    font = load_font(config, int(style["font_size"]))
    draw_text_centered(
      draw,
      text,
      canvas_w // 2,
      int(style["y"]),
      font,
      style["color"],
      style["shadow_color"],
      int(style["shadow_offset"]),
      style.get("stroke_color"),
      int(style.get("stroke_width", 0)),
    )

  output.parent.mkdir(parents=True, exist_ok=True)
  canvas.save(output, "PNG", optimize=True)
  return output


def is_intro_yaml(data: dict) -> bool:
  return "chords" not in data and ("line1" in data or "lines" in data)


def main() -> None:
  parser = argparse.ArgumentParser(description="Generate TikTok guitar slides")
  parser.add_argument("song_file", nargs="?", help="YAML song or intro definition")
  parser.add_argument("--intro", action="store_true", help="Force intro slide mode")
  parser.add_argument("--config", default="config.json", help="Layout config path")
  parser.add_argument("--artist", help="Artist name (Hebrew)")
  parser.add_argument("--song", help="Song title (Hebrew)")
  parser.add_argument("--chords", help="Comma-separated chord names, e.g. Em,C,G,D")
  parser.add_argument("--artist-image", help="Path to artist photo")
  parser.add_argument("--background", help="Override background image")
  parser.add_argument("--output", help="Output PNG path")
  args = parser.parse_args()

  config_path = Path(args.config)
  if not config_path.is_absolute():
    config_path = ROOT / config_path
  config = load_config(config_path)

  if args.song_file:
    song_path = Path(args.song_file)
    if not song_path.is_absolute():
      song_path = ROOT / song_path
    data = load_yaml(song_path)

    if args.intro or is_intro_yaml(data):
      lines = collect_intro_lines(data)
      background = ROOT / data["background"] if data.get("background") else None
      output = ROOT / data.get("output", f"output/{song_path.stem}.png")
      result = generate_intro_slide(
        lines=lines,
        background=background,
        output=output,
        config=config,
      )
      print(f"Created: {result}")
      return

    artist = data["artist"]
    song = data["song"]
    chord_names = data["chords"]
    artist_image = resolve_artist_image(config, artist, data.get("artist_image"))
    background = ROOT / data["background"] if data.get("background") else None
    output = ROOT / data.get("output", f"output/{song_path.stem}.png")
  else:
    if not all([args.artist, args.song, args.chords]):
      parser.error("Provide song_file or --artist --song --chords")
    artist = args.artist
    song = args.song
    chord_names = [c.strip() for c in args.chords.split(",")]
    artist_image = resolve_artist_image(config, artist, args.artist_image)
    background = Path(args.background) if args.background else None
    if background and not background.is_absolute():
      background = ROOT / background
    safe_name = song_slug(song)
    output = Path(args.output) if args.output else ROOT / f"output/{safe_name}.png"
    if not output.is_absolute():
      output = ROOT / output

  result = generate_song_slide(
    artist=artist,
    song=song,
    chord_names=chord_names,
    artist_image=artist_image,
    background=background,
    output=output,
    config=config,
  )
  print(f"Created: {result}")


if __name__ == "__main__":
  main()
