#!/usr/bin/env python3
"""Generate TikTok song slides matching CapCut layout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont
from bidi.algorithm import get_display

ROOT = Path(__file__).resolve().parent
SESSION_PATH = ROOT / "intros" / "session.json"
DIFFICULTY_LEVELS = ("קל", "בינוני", "קשה")


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


def normalize_difficulty(value: str | None) -> str | None:
  if not value:
    return None
  cleaned = str(value).strip()
  if cleaned in DIFFICULTY_LEVELS:
    return cleaned
  return None


def get_difficulty_theme(config: dict, difficulty: str | None) -> dict | None:
  level = normalize_difficulty(difficulty)
  if not level:
    return None
  levels = config.get("difficulty", {}).get("levels", {})
  theme = levels.get(level)
  if not theme:
    raise ValueError(f"Unknown difficulty '{level}'. Use: {', '.join(DIFFICULTY_LEVELS)}")
  return {"level": level, **theme}


def save_session_difficulty(difficulty: str) -> None:
  level = normalize_difficulty(difficulty)
  if not level:
    raise ValueError(f"Invalid difficulty. Use: {', '.join(DIFFICULTY_LEVELS)}")
  SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
  SESSION_PATH.write_text(
    json.dumps({"difficulty": level}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
  )


def load_session_difficulty() -> str | None:
  if not SESSION_PATH.is_file():
    return None
  try:
    data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
  except json.JSONDecodeError:
    return None
  return normalize_difficulty(data.get("difficulty"))


def resolve_song_difficulty(config: dict, explicit: str | None = None) -> str | None:
  return normalize_difficulty(explicit) or load_session_difficulty()


def themed_intro_line_style(base_style: dict, theme: dict, index: int) -> dict:
  style = dict(base_style)
  if index % 2 == 1:
    style["color"] = theme["accent"]
    style["shadow_color"] = theme.get("shadow", style["shadow_color"])
  else:
    style["color"] = theme.get("text_light", "#FFFDF5")
    style["shadow_color"] = theme.get("shadow", style["shadow_color"])
  return style


def measure_text_height(
  text: str,
  font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
  stroke_width: int,
) -> int:
  probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
  bbox = probe.textbbox(
    (0, 0),
    get_display(text),
    font=font,
    stroke_width=stroke_width,
  )
  return bbox[3] - bbox[1]


def measure_difficulty_badge_box(
  difficulty: str,
  config: dict,
  canvas_w: int,
) -> tuple[int, int]:
  badge_cfg = config.get("difficulty", {}).get("badge", {})
  label = normalize_difficulty(difficulty) or difficulty
  font_size = int(badge_cfg.get("font_size", 96))
  font = load_font(config, font_size)
  stroke_width = int(badge_cfg.get("stroke_width", 5))
  padding_x = int(badge_cfg.get("padding_x", 72))
  padding_y = int(badge_cfg.get("padding_y", 26))

  probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
  text_bbox = probe.textbbox(
    (0, 0),
    get_display(label),
    font=font,
    stroke_width=stroke_width,
  )
  text_w = text_bbox[2] - text_bbox[0]
  text_h = text_bbox[3] - text_bbox[1]
  return text_w + padding_x * 2, text_h + padding_y * 2


def draw_difficulty_badge_at(
  draw: ImageDraw.ImageDraw,
  *,
  canvas_w: int,
  box_top_y: int,
  difficulty: str,
  theme: dict,
  config: dict,
) -> tuple[int, int, int, int]:
  """Draw pill badge; return (x0, y0, x1, y1)."""
  badge_cfg = config.get("difficulty", {}).get("badge", {})
  label = normalize_difficulty(difficulty) or difficulty
  font_size = int(badge_cfg.get("font_size", 96))
  font = load_font(config, font_size)
  stroke_width = int(badge_cfg.get("stroke_width", 5))
  outline_width = int(badge_cfg.get("outline_width", 4))
  padding_x = int(badge_cfg.get("padding_x", 72))
  padding_y = int(badge_cfg.get("padding_y", 26))
  radius = int(badge_cfg.get("radius", 38))
  shadow_color = theme.get("shadow", "#000000")

  display_label = get_display(label)
  text_bbox = draw.textbbox((0, 0), display_label, font=font, stroke_width=stroke_width)
  text_w = text_bbox[2] - text_bbox[0]
  text_h = text_bbox[3] - text_bbox[1]
  box_w = text_w + padding_x * 2
  box_h = text_h + padding_y * 2
  box_x = (canvas_w - box_w) // 2
  box_y = box_top_y

  badge_bg = theme.get("badge_bg", "#FB8C00")
  draw.rounded_rectangle(
    (box_x, box_y, box_x + box_w, box_y + box_h),
    radius=radius,
    fill=badge_bg,
    outline=shadow_color,
    width=outline_width,
  )

  text_x = box_x + (box_w - text_w) // 2 - text_bbox[0]
  text_y = box_y + (box_h - text_h) // 2 - text_bbox[1]
  draw.text(
    (text_x, text_y),
    display_label,
    font=font,
    fill=theme.get("badge_text", "#FFFFFF"),
    stroke_width=stroke_width,
    stroke_fill=shadow_color,
  )
  return box_x, box_y, box_x + box_w, box_y + box_h


def draw_difficulty_color_strip(
  draw: ImageDraw.ImageDraw,
  *,
  canvas_w: int,
  top_y: int,
  theme: dict,
  strip_cfg: dict,
) -> tuple[int, int, int, int]:
  """Full-width accent strip below badge; return bounds."""
  height = int(strip_cfg.get("height", 14))
  width_ratio = float(strip_cfg.get("width_ratio", 0.88))
  strip_w = max(1, int(canvas_w * width_ratio))
  x0 = (canvas_w - strip_w) // 2
  y0 = top_y
  x1 = x0 + strip_w
  y1 = y0 + height
  fill = theme.get("badge_bg", theme.get("accent", "#FB8C00"))
  outline_w = int(strip_cfg.get("outline_width", 2))
  shadow = theme.get("shadow", "#000000")
  draw.rectangle((x0, y0, x1, y1), fill=fill)
  if outline_w > 0:
    draw.rectangle((x0, y0, x1, y1), outline=shadow, width=outline_w)
  return x0, y0, x1, y1


def apply_intro_content_panel(
  canvas: Image.Image,
  bounds: tuple[int, int, int, int],
  panel_cfg: dict,
) -> Image.Image:
  if not panel_cfg.get("enabled", True):
    return canvas
  x0, y0, x1, y1 = bounds
  opacity = float(panel_cfg.get("opacity", 0.78))
  radius = int(panel_cfg.get("radius", 36))
  rgb = _parse_hex_color(panel_cfg.get("color", "#0A0A0A"))
  layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
  draw = ImageDraw.Draw(layer)
  draw.rounded_rectangle(
    (x0, y0, x1, y1),
    radius=radius,
    fill=(*rgb, int(255 * opacity)),
  )
  return Image.alpha_composite(canvas.convert("RGBA"), layer).convert("RGB")


def build_centered_intro_layout(
  *,
  canvas_w: int,
  canvas_h: int,
  lines: list[str],
  style_templates: list[dict],
  theme: dict | None,
  config: dict,
  slide_cfg: dict,
) -> dict:
  layout_cfg = slide_cfg.get("layout", {})
  font_sizes = layout_cfg.get("line_font_sizes", [135, 98, 88, 88])
  line_gap = int(layout_cfg.get("line_gap", 28))
  strip_cfg = layout_cfg.get("color_strip", {})
  strip_h = int(strip_cfg.get("height", 14))
  strip_gap = int(strip_cfg.get("gap_below_badge", 6))
  text_gap = int(strip_cfg.get("gap_above_text", 34))
  panel_cfg = layout_cfg.get("panel", {})
  panel_pad_x = int(panel_cfg.get("padding_x", 44))
  panel_pad_y = int(panel_cfg.get("padding_y", 36))

  badge_h = 0
  badge_w = 0
  if theme:
    badge_w, badge_h = measure_difficulty_badge_box(theme["level"], config, canvas_w)

  header_h = 0
  if theme:
    header_h = badge_h + strip_gap + strip_h + text_gap

  line_entries: list[dict] = []
  text_block_h = 0
  for index, text in enumerate(lines):
    style = dict(style_templates[index])
    if theme:
      style = themed_intro_line_style(style, theme, index)
    font_size = int(font_sizes[index] if index < len(font_sizes) else font_sizes[-1])
    stroke_width = int(style.get("stroke_width", 0))
    font = load_font(config, font_size)
    height = measure_text_height(text, font, stroke_width)
    line_entries.append(
      {
        "text": text,
        "style": style,
        "font_size": font_size,
        "height": height,
      }
    )
    text_block_h += height
    if index < len(lines) - 1:
      text_block_h += line_gap

  total_h = header_h + text_block_h
  block_top = max(panel_pad_y, (canvas_h - total_h) // 2)

  y = block_top
  badge_top = y
  badge_bounds = None
  strip_bounds = None
  if theme:
    badge_bounds = (0, badge_top, 0, badge_top + badge_h)  # x filled at draw
    y = badge_top + badge_h + strip_gap
    strip_top = y
    strip_bounds = (0, strip_top, 0, strip_top + strip_h)
    y = strip_top + strip_h + text_gap

  for index, entry in enumerate(line_entries):
    entry["y"] = y
    y += entry["height"]
    if index < len(line_entries) - 1:
      y += line_gap

  content_left = (canvas_w - badge_w) // 2 if theme else 0
  content_right = content_left + badge_w if theme else canvas_w
  for entry in line_entries:
    font = load_font(config, entry["font_size"])
    stroke_width = int(entry["style"].get("stroke_width", 0))
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = probe.textbbox(
      (0, 0),
      get_display(entry["text"]),
      font=font,
      stroke_width=stroke_width,
    )
    text_w = bbox[2] - bbox[0]
    content_left = min(content_left, canvas_w // 2 - text_w // 2)
    content_right = max(content_right, canvas_w // 2 + text_w // 2)

  if theme:
    strip_w = max(1, int(canvas_w * float(strip_cfg.get("width_ratio", 0.88))))
    content_left = min(content_left, (canvas_w - strip_w) // 2)
    content_right = max(content_right, (canvas_w + strip_w) // 2)

  panel_x0 = max(0, content_left - panel_pad_x)
  panel_x1 = min(canvas_w, content_right + panel_pad_x)
  panel_y0 = max(0, block_top - panel_pad_y)
  panel_y1 = min(canvas_h, y + panel_pad_y)

  return {
    "badge_top": badge_top,
    "strip_top": strip_top if theme else None,
    "lines": line_entries,
    "panel_rect": (panel_x0, panel_y0, panel_x1, panel_y1),
    "strip_cfg": strip_cfg,
  }


def draw_difficulty_badge(
  draw: ImageDraw.ImageDraw,
  *,
  canvas_w: int,
  difficulty: str,
  theme: dict,
  config: dict,
) -> None:
  """Legacy top-fixed badge (outro / non-centered intros)."""
  badge_cfg = config.get("difficulty", {}).get("badge", {})
  box_w, box_h = measure_difficulty_badge_box(difficulty, config, canvas_w)
  box_top = int(badge_cfg.get("y", 158)) - int(badge_cfg.get("padding_y", 26))
  draw_difficulty_badge_at(
    draw,
    canvas_w=canvas_w,
    box_top_y=box_top,
    difficulty=difficulty,
    theme=theme,
    config=config,
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
  difficulty: str | None = None,
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
  if config.get("song", {}).get("grayscale_background", False):
    canvas = canvas.convert("L").convert("RGB")

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
  diff_cfg = config.get("difficulty", {})
  tint_enabled = diff_cfg.get("chord_tint_enabled", False)
  tint_opacity = float(diff_cfg.get("chord_tint_opacity", 0.28))
  chord_theme = get_difficulty_theme(config, resolve_song_difficulty(config, difficulty))
  chord_images: list[Image.Image] = []
  chord_sizes: list[tuple[int, int]] = []
  for chord_name in chord_names:
    chord_path = resolve_chord_image(chords_dir, chord_name)
    chord_img = Image.open(chord_path).convert("RGBA")
    cw, ch = size_from_scale(chord_img, chord_scale, ref_h)
    chord_img = chord_img.resize((cw, ch), Image.Resampling.LANCZOS)
    if tint_enabled and chord_theme and chord_theme.get("chord_tint"):
      chord_img = apply_chord_tint(chord_img, chord_theme["chord_tint"], tint_opacity)
    chord_images.append(chord_img)
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

  draw = ImageDraw.Draw(canvas)

  border_cfg = grid_cfg.get("border", {})
  if border_cfg.get("enabled", True) and chord_theme:
    border_color = chord_theme.get("chord_label") or chord_theme.get("chord_tint")
    if border_color:
      draw_chord_borders(
        draw,
        positions,
        chord_sizes,
        color=border_color,
        padding=int(border_cfg.get("padding", 5)),
        border_width=int(border_cfg.get("width", 4)),
        stroke_width=int(border_cfg.get("stroke_width", 2)),
        stroke_color=border_cfg.get("stroke_color", "#000000"),
      )

  labels_cfg = grid_cfg.get("labels", {})
  if labels_cfg.get("enabled", False):
    label_font_size = int(labels_cfg.get("font_size", 42))
    label_font = load_font(config, label_font_size)
    stroke_width = int(labels_cfg.get("stroke_width", 0))
    gap_above = int(labels_cfg.get("gap_above", 8))
    sample_bbox = draw.textbbox((0, 0), "8", font=label_font, stroke_width=stroke_width)
    label_h = sample_bbox[3] - sample_bbox[1]
    theme = chord_theme
    label_color = (
      theme["chord_label"]
      if theme
      else labels_cfg.get("color", "#FFEB3B")
    )
    for number, ((tx, ty), (cw, _ch)) in enumerate(
      zip(positions, chord_sizes, strict=True), start=1
    ):
      draw_text_centered(
        draw,
        str(number),
        tx + cw // 2,
        ty - gap_above - label_h,
        label_font,
        label_color,
        labels_cfg.get("shadow_color", "#000000"),
        int(labels_cfg.get("shadow_offset", 0)),
        labels_cfg.get("stroke_color"),
        stroke_width,
      )

  # Text layers: X centered, Y from top (CapCut text values)
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
  song_color = chord_theme["chord_label"] if chord_theme else song_cfg["color"]
  draw_text_centered(
    draw,
    song,
    int(canvas_w / 2 + song_cfg["capcut_x"]),
    int(song_cfg["capcut_y"]),
    song_font,
    song_color,
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


def _parse_hex_color(value: str) -> tuple[int, int, int]:
  value = value.lstrip("#")
  if len(value) != 6:
    raise ValueError(f"Expected #RRGGBB color, got {value!r}")
  return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def apply_chord_tint(
  img: Image.Image,
  tint_color: str,
  opacity: float,
) -> Image.Image:
  """Subtle color wash on chord diagram PNGs."""
  if opacity <= 0:
    return img
  rgba = img.convert("RGBA")
  tint_rgb = _parse_hex_color(tint_color)
  overlay = Image.new("RGBA", rgba.size, (*tint_rgb, int(255 * opacity)))
  return Image.alpha_composite(rgba, overlay)


def draw_chord_borders(
  draw: ImageDraw.ImageDraw,
  positions: list[tuple[int, int]],
  sizes: list[tuple[int, int]],
  *,
  color: str,
  padding: int,
  border_width: int,
  stroke_width: int,
  stroke_color: str,
) -> None:
  """Colored frame around each chord with a thin black outer stroke."""
  for (tx, ty), (cw, ch) in zip(positions, sizes, strict=True):
    x0 = tx - padding
    y0 = ty - padding
    x1 = tx + cw + padding
    y1 = ty + ch + padding
    if stroke_width > 0:
      draw.rectangle(
        (
          x0 - stroke_width,
          y0 - stroke_width,
          x1 + stroke_width,
          y1 + stroke_width,
        ),
        outline=stroke_color,
        width=stroke_width,
      )
    if border_width > 0:
      draw.rectangle((x0, y0, x1, y1), outline=color, width=border_width)


def apply_intro_color_grade(canvas: Image.Image, grade_cfg: dict) -> Image.Image:
  """Boost saturation/contrast and add a warm tint for a livelier intro."""
  if not grade_cfg.get("enabled", True):
    return canvas

  img = canvas
  if grade_cfg.get("grayscale", False):
    img = img.convert("L").convert("RGB")

  saturation = float(grade_cfg.get("saturation", 1.0))
  contrast = float(grade_cfg.get("contrast", 1.0))
  brightness = float(grade_cfg.get("brightness", 1.0))
  if saturation != 1.0:
    img = ImageEnhance.Color(img).enhance(saturation)
  if contrast != 1.0:
    img = ImageEnhance.Contrast(img).enhance(contrast)
  if brightness != 1.0:
    img = ImageEnhance.Brightness(img).enhance(brightness)

  if grade_cfg.get("grayscale", False):
    return img

  warm_tint = grade_cfg.get("warm_tint") or {}
  tint_opacity = float(warm_tint.get("opacity", 0))
  if tint_opacity > 0:
    tint_rgb = _parse_hex_color(warm_tint.get("color", "#FF8A00"))
    w, h = img.size
    tint_layer = Image.new("RGBA", (w, h), (*tint_rgb, int(255 * tint_opacity)))
    img = Image.alpha_composite(img.convert("RGBA"), tint_layer).convert("RGB")

  return img


def apply_intro_overlay(canvas: Image.Image, overlay_cfg: dict) -> Image.Image:
  """Darken top/bottom so text reads cleanly on busy photo backgrounds."""
  if not overlay_cfg.get("enabled", True):
    return canvas

  w, h = canvas.size
  top_opacity = float(overlay_cfg.get("top_opacity", 0.42))
  bottom_opacity = float(overlay_cfg.get("bottom_opacity", 0.52))
  fade_ratio = float(overlay_cfg.get("fade_ratio", 0.32))
  fade_h = max(1, int(h * fade_ratio))
  edge_rgb = _parse_hex_color(overlay_cfg.get("edge_color", "#000000"))

  overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
  draw = ImageDraw.Draw(overlay)
  for y in range(fade_h):
    alpha = int(255 * top_opacity * (1 - y / fade_h))
    draw.line([(0, y), (w, y)], fill=(*edge_rgb, alpha))
  for y in range(h - fade_h, h):
    alpha = int(255 * bottom_opacity * ((y - (h - fade_h)) / fade_h))
    draw.line([(0, y), (w, y)], fill=(*edge_rgb, alpha))

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
  slide_style: str = "intro",
  difficulty: str | None = None,
) -> Path:
  canvas_w = config["canvas"]["width"]
  canvas_h = config["canvas"]["height"]
  slide_cfg = config.get(slide_style, config["intro"])
  style_templates = slide_cfg["line_styles"]

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
  canvas = apply_intro_color_grade(canvas, slide_cfg.get("color_grade", {}))
  canvas = apply_intro_overlay(canvas, slide_cfg.get("overlay", {}))

  theme = get_difficulty_theme(config, difficulty) if slide_style == "intro" else None
  layout_cfg = slide_cfg.get("layout", {})
  use_centered = slide_style == "intro" and layout_cfg.get("centered", False)

  if use_centered:
    block = build_centered_intro_layout(
      canvas_w=canvas_w,
      canvas_h=canvas_h,
      lines=lines,
      style_templates=style_templates,
      theme=theme,
      config=config,
      slide_cfg=slide_cfg,
    )
    canvas = apply_intro_content_panel(
      canvas,
      block["panel_rect"],
      layout_cfg.get("panel", {}),
    )
    draw = ImageDraw.Draw(canvas)
    if theme:
      draw_difficulty_badge_at(
        draw,
        canvas_w=canvas_w,
        box_top_y=block["badge_top"],
        difficulty=theme["level"],
        theme=theme,
        config=config,
      )
      draw_difficulty_color_strip(
        draw,
        canvas_w=canvas_w,
        top_y=block["strip_top"],
        theme=theme,
        strip_cfg=block["strip_cfg"],
      )
    for entry in block["lines"]:
      style = entry["style"]
      font = load_font(config, entry["font_size"])
      draw_text_centered(
        draw,
        entry["text"],
        canvas_w // 2,
        entry["y"],
        font,
        style["color"],
        style["shadow_color"],
        int(style["shadow_offset"]),
        style.get("stroke_color"),
        int(style.get("stroke_width", 0)),
      )
  else:
    draw = ImageDraw.Draw(canvas)
    if theme:
      draw_difficulty_badge(
        draw,
        canvas_w=canvas_w,
        difficulty=theme["level"],
        theme=theme,
        config=config,
      )
    for index, text in enumerate(lines):
      style = style_templates[index]
      if theme:
        style = themed_intro_line_style(style, theme, index)
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


def resolve_text_slide_style(data: dict, song_path: Path) -> str:
  if data.get("slide_style") in ("intro", "outro"):
    return data["slide_style"]
  output = str(data.get("output", "")).lower()
  if "outro" in output or song_path.stem == "outro":
    return "outro"
  return "intro"


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
  song_difficulty = None

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
        slide_style=resolve_text_slide_style(data, song_path),
        difficulty=data.get("difficulty"),
      )
      print(f"Created: {result}")
      return

    artist = data["artist"]
    song = data["song"]
    chord_names = data["chords"]
    artist_image = resolve_artist_image(config, artist, data.get("artist_image"))
    background = ROOT / data["background"] if data.get("background") else None
    output = ROOT / data.get("output", f"output/{song_path.stem}.png")
    song_difficulty = data.get("difficulty")
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
    difficulty=song_difficulty,
  )
  print(f"Created: {result}")


if __name__ == "__main__":
  main()
