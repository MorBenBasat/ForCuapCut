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
MIN_CHORDS = 4
MAX_CHORDS = 8


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
  from generate_chord import chord_asset_key

  asset_key = chord_asset_key(name)
  variants: list[str] = []
  for candidate in (asset_key, name, name.upper(), name.lower(), name.capitalize()):
    if candidate and candidate not in variants:
      variants.append(candidate)
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


def crop_center_square(img: Image.Image) -> Image.Image:
  w, h = img.size
  side = min(w, h)
  left = (w - side) // 2
  top = (h - side) // 2
  return img.crop((left, top, left + side, top + side))


def apply_circular_mask(
  img: Image.Image,
  *,
  border_width: int = 0,
  border_color: str = "#FFFFFF",
) -> Image.Image:
  """Square RGBA image → circle with optional ring border."""
  size = img.size[0]
  if img.size[1] != size:
    raise ValueError("apply_circular_mask expects a square image")

  mask = Image.new("L", (size, size), 0)
  draw = ImageDraw.Draw(mask)
  draw.ellipse((0, 0, size - 1, size - 1), fill=255)

  result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
  result.paste(img, (0, 0), mask)

  if border_width > 0:
    draw = ImageDraw.Draw(result)
    inset = border_width // 2
    draw.ellipse(
      (inset, inset, size - 1 - inset, size - 1 - inset),
      outline=border_color,
      width=border_width,
    )
  return result


def capcut_y_to_center_y(y: float, canvas_h: int) -> int:
  """CapCut Y+ up → pixel center Y."""
  return int(canvas_h / 2 - y)


def layout_chord_positions(
  chord_sizes: list[tuple[int, int]],
  pattern: list[int],
  canvas_w: int,
  canvas_h: int,
  gap: int,
  row_ys: list[float],
  center_offset_x: int = 0,
) -> list[tuple[int, int]]:
  """Place chords in centered rows with fixed CapCut Y anchors."""
  if len(row_ys) != len(pattern):
    raise ValueError("row_ys length must match pattern rows")
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


def layout_chord_positions_stacked(
  chord_sizes: list[tuple[int, int]],
  pattern: list[int],
  canvas_w: int,
  canvas_h: int,
  horizontal_gap: int,
  *,
  first_row_capcut_y: float,
  vertical_gap: int,
  label_top_pad: int = 0,
  last_row_extra_gap: int = 0,
  center_offset_x: int = 0,
) -> list[tuple[int, int]]:
  """Stack 3+ rows with spacing derived from chord height (no overlap)."""
  chord_h = max(h for _, h in chord_sizes)
  row_step = chord_h + vertical_gap + label_top_pad
  first_center_y = capcut_y_to_center_y(first_row_capcut_y, canvas_h)
  positions: list[tuple[int, int]] = []
  idx = 0
  for row_i, count in enumerate(pattern):
    row = chord_sizes[idx : idx + count]
    idx += count
    widths = [size[0] for size in row]
    heights = [size[1] for size in row]
    row_width = sum(widths) + horizontal_gap * (count - 1)
    start_x = (canvas_w - row_width) // 2 + center_offset_x
    extra = last_row_extra_gap if row_i == len(pattern) - 1 and row_i > 0 else 0
    center_y = first_center_y + row_i * row_step + extra
    x = start_x
    for width, height in zip(widths, heights, strict=True):
      positions.append((x, center_y - height // 2))
      x += width + horizontal_gap
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


def get_thumbnail_default_strip_theme(config: dict) -> dict:
  """Accent colors for thumbnail slides without a difficulty badge."""
  defaults = config.get("thumbnail", {}).get("default_strip", {})
  return {
    "accent": defaults.get("accent", "#FFD54F"),
    "badge_bg": defaults.get("badge_bg", "#FFD54F"),
    "shadow": defaults.get("shadow", "#1A0A00"),
  }


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
  accent_strip_theme: dict | None = None,
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
  elif accent_strip_theme:
    header_h = strip_h + text_gap

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
  strip_top = None
  if theme:
    badge_bounds = (0, badge_top, 0, badge_top + badge_h)  # x filled at draw
    y = badge_top + badge_h + strip_gap
    strip_top = y
    strip_bounds = (0, strip_top, 0, strip_top + strip_h)
    y = strip_top + strip_h + text_gap
  elif accent_strip_theme:
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

  if theme or accent_strip_theme:
    strip_w = max(1, int(canvas_w * float(strip_cfg.get("width_ratio", 0.88))))
    content_left = min(content_left, (canvas_w - strip_w) // 2)
    content_right = max(content_right, (canvas_w + strip_w) // 2)

  panel_x0 = max(0, content_left - panel_pad_x)
  panel_x1 = min(canvas_w, content_right + panel_pad_x)
  panel_y0 = max(0, block_top - panel_pad_y)
  panel_y1 = min(canvas_h, y + panel_pad_y)

  return {
    "badge_top": badge_top,
    "strip_top": strip_top,
    "lines": line_entries,
    "panel_rect": (panel_x0, panel_y0, panel_x1, panel_y1),
    "strip_cfg": strip_cfg,
    "accent_strip_theme": accent_strip_theme,
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


def resolve_chord_scale(grid_cfg: dict, chord_count: int) -> float:
  """Full size for ≤6 chords; slightly smaller above 6."""
  base_scale = float(grid_cfg["scale"])
  if chord_count <= 6:
    return base_scale
  return float(grid_cfg.get("scale_compact", base_scale * 0.9))


def resolve_singer_scale(singer_cfg: dict, chord_count: int) -> float:
  """Full size for ≤6 chords; smaller photo when the grid needs more room."""
  base_scale = float(singer_cfg["scale"])
  if chord_count <= 6:
    return base_scale
  return float(singer_cfg.get("scale_compact", base_scale * 0.85))


def row_ys_from_config(row_cfg: dict) -> list[float]:
  if "row_ys" in row_cfg:
    return [float(y) for y in row_cfg["row_ys"]]
  return [float(row_cfg["top_y"]), float(row_cfg["bottom_y"])]


def layout_chords_for_pattern(
  chord_sizes: list[tuple[int, int]],
  pattern: list[int],
  canvas_w: int,
  canvas_h: int,
  grid_cfg: dict,
  row_cfg: dict,
  *,
  label_top_pad: int = 0,
) -> list[tuple[int, int]]:
  horizontal_gap = int(grid_cfg["horizontal_gap"])
  center_offset_x = int(grid_cfg.get("center_offset_x", 0))
  if len(pattern) > 2:
    return layout_chord_positions_stacked(
      chord_sizes,
      pattern,
      canvas_w,
      canvas_h,
      horizontal_gap,
      first_row_capcut_y=float(row_cfg.get("first_row_y", row_cfg.get("top_y", 215))),
      vertical_gap=int(row_cfg.get("vertical_gap", grid_cfg.get("vertical_gap", 28))),
      label_top_pad=label_top_pad,
      last_row_extra_gap=int(row_cfg.get("last_row_extra_gap", 0)),
      center_offset_x=center_offset_x,
    )
  return layout_chord_positions(
    chord_sizes,
    pattern,
    canvas_w,
    canvas_h,
    horizontal_gap,
    row_ys_from_config(row_cfg),
    center_offset_x,
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
  highlight_index: int | None = None,
) -> Path:
  canvas_w = config["canvas"]["width"]
  canvas_h = config["canvas"]["height"]
  ref_h = config.get("scale_reference_height", canvas_h)

  chord_count = len(chord_names)
  if not (MIN_CHORDS <= chord_count <= MAX_CHORDS):
    raise ValueError(
      f"Expected {MIN_CHORDS}-{MAX_CHORDS} chords, got {chord_count}"
    )

  grid_cfg = config["chord_grid"]
  row_cfg = pick_row_config(config, chord_count)
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
  singer_scale = resolve_singer_scale(singer_cfg, chord_count)
  singer_img = Image.open(artist_image).convert("RGBA")
  if singer_cfg.get("circular", True):
    singer_img = crop_center_square(singer_img)
    singer_size = max(1, int(ref_h * singer_scale / 100))
    singer_img = singer_img.resize((singer_size, singer_size), Image.Resampling.LANCZOS)
    singer_img = apply_circular_mask(
      singer_img,
      border_width=int(singer_cfg.get("border_width", 5)),
      border_color=singer_cfg.get("border_color", "#FFF8DC"),
    )
    singer_w = singer_h = singer_size
  else:
    singer_w, singer_h = size_from_scale(singer_img, singer_scale, ref_h)
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
  chord_scale = resolve_chord_scale(grid_cfg, chord_count)
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

  labels_cfg = grid_cfg.get("labels", {})
  label_top_pad = 0
  label_font = None
  label_h = 0
  gap_above = 0
  label_stroke_width = 0
  if labels_cfg.get("enabled", False):
    label_font = load_font(config, int(labels_cfg.get("font_size", 42)))
    label_stroke_width = int(labels_cfg.get("stroke_width", 0))
    gap_above = int(labels_cfg.get("gap_above", 8))
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    sample_bbox = probe.textbbox(
      (0, 0), "8", font=label_font, stroke_width=label_stroke_width
    )
    label_h = sample_bbox[3] - sample_bbox[1]
    label_top_pad = label_h + gap_above

  positions = layout_chords_for_pattern(
    chord_sizes,
    pattern,
    canvas_w,
    canvas_h,
    grid_cfg,
    row_cfg,
    label_top_pad=label_top_pad,
  )
  for chord_img, (tx, ty) in zip(chord_images, positions, strict=True):
    canvas.paste(chord_img, (tx, ty), chord_img)

  draw = ImageDraw.Draw(canvas)

  border_cfg = grid_cfg.get("border", {})
  if border_cfg.get("enabled", True) and chord_theme:
    border_color = chord_theme.get("chord_label") or chord_theme.get("chord_tint")
    if border_color:
      if highlight_index is None:
        border_positions = positions
        border_sizes = chord_sizes
      else:
        if not 1 <= highlight_index <= len(chord_names):
          raise ValueError(
            f"highlight_index must be 1-{len(chord_names)}, got {highlight_index}"
          )
        idx = highlight_index - 1
        border_positions = [positions[idx]]
        border_sizes = [chord_sizes[idx]]
      draw_chord_borders(
        draw,
        border_positions,
        border_sizes,
        color=border_color,
        padding=int(border_cfg.get("padding", 5)),
        border_width=int(border_cfg.get("width", 4)),
        stroke_width=int(border_cfg.get("stroke_width", 2)),
        stroke_color=border_cfg.get("stroke_color", "#000000"),
      )

  if labels_cfg.get("enabled", False) and label_font is not None:
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
        label_stroke_width,
      )

  # Text layers: song title + artist (hidden when >6 chords — grid uses the space)
  if chord_count <= 6:
    text_cfg = config["text"]

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


STRUM_TOKENS = frozenset({"D", "U", "X"})
STRUM_TOKEN_ALIASES = {
  "D": "D",
  "DOWN": "D",
  "↓": "D",
  "למטה": "D",
  "U": "U",
  "UP": "U",
  "↑": "U",
  "למעלה": "U",
  "X": "X",
  "MUTE": "X",
  "×": "X",
  "*": "X",
  "השתקה": "X",
}


def normalize_strum_token(value: str) -> str | None:
  cleaned = str(value).strip()
  if not cleaned:
    return None
  upper = cleaned.upper()
  if upper in STRUM_TOKENS:
    return upper
  return STRUM_TOKEN_ALIASES.get(cleaned) or STRUM_TOKEN_ALIASES.get(upper)


def parse_strum_pattern(raw) -> list[str]:
  if isinstance(raw, list):
    parts = raw
  else:
    parts = [part.strip() for part in str(raw).split(",") if part.strip()]
  tokens: list[str] = []
  for part in parts:
    token = normalize_strum_token(part)
    if not token:
      raise ValueError(f"פעימה לא תקינה: {part!r}. השתמש ב-D / U / X.")
    tokens.append(token)
  if not 2 <= len(tokens) <= 16:
    raise ValueError(f"צריך 2–16 פעימות, קיבלתי {len(tokens)}.")
  return tokens


def _strum_layout_grid(count: int, max_cols: int) -> tuple[int, int]:
  cols = min(count, max_cols)
  rows = (count + cols - 1) // cols
  return cols, rows


def _lerp_rgb(
  top: tuple[int, int, int],
  bottom: tuple[int, int, int],
  t: float,
) -> tuple[int, int, int]:
  return tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))


def _token_gradient_colors(style: dict) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
  top = _parse_hex_color(style.get("bg_top", style.get("bg", "#333333")))
  bottom = _parse_hex_color(style.get("bg_bottom", style.get("bg", "#111111")))
  return top, bottom


def _rounded_rect_mask(size: int, radius: int) -> Image.Image:
  mask = Image.new("L", (size, size), 0)
  mask_draw = ImageDraw.Draw(mask)
  mask_draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
  return mask


def _render_strum_cell_image(
  *,
  size: int,
  token: str,
  style: dict,
  config: dict,
) -> Image.Image:
  radius = int(style.get("radius", 26))
  shadow_offset = int(style.get("shadow_blur", 8))
  pad = shadow_offset + 4
  canvas_size = size + pad * 2
  sheet = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
  draw = ImageDraw.Draw(sheet)

  x0 = pad
  y0 = pad
  x1 = pad + size - 1
  y1 = pad + size - 1

  shadow_rgb = _parse_hex_color(style.get("shadow_color", "#000000"))
  shadow_alpha = int(style.get("shadow_alpha", 110))
  draw.rounded_rectangle(
    (x0 + shadow_offset, y0 + shadow_offset, x1 + shadow_offset, y1 + shadow_offset),
    radius=radius,
    fill=(*shadow_rgb, shadow_alpha),
  )

  top_rgb, bottom_rgb = _token_gradient_colors(style)
  cell = Image.new("RGBA", (size, size), (0, 0, 0, 0))
  cell_draw = ImageDraw.Draw(cell)
  for y in range(size):
    t = y / max(size - 1, 1)
    color = _lerp_rgb(top_rgb, bottom_rgb, t)
    cell_draw.line([(0, y), (size - 1, y)], fill=(*color, 255))

  mask = _rounded_rect_mask(size, radius)
  cell.putalpha(mask)
  sheet.paste(cell, (x0, y0), cell)

  outline = style.get("outline", "#000000")
  outline_w = int(style.get("outline_width", 3))
  glow = style.get("glow", outline)
  glow_w = int(style.get("glow_width", 5))
  if glow_w > 0:
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, outline=glow, width=glow_w)
  if outline_w > 0:
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, outline=outline, width=outline_w)

  shine_h = max(6, size // 5)
  shine = Image.new("RGBA", (size, shine_h), (0, 0, 0, 0))
  shine_draw = ImageDraw.Draw(shine)
  shine_draw.rounded_rectangle(
    (4, 0, size - 5, shine_h),
    radius=shine_h // 2,
    fill=(255, 255, 255, int(style.get("shine_opacity", 52))),
  )
  sheet.paste(shine, (x0 + 3, y0 + 4), shine)

  symbol = style.get("symbol", token)
  symbol_size = int(size * float(style.get("symbol_scale", 0.5)))
  symbol_font = load_font(config, symbol_size)
  stroke_width = int(style.get("stroke_width", 3))
  symbol_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
  symbol_draw = ImageDraw.Draw(symbol_layer)
  probe = symbol_draw.textbbox((0, 0), symbol, font=symbol_font, stroke_width=stroke_width)
  text_w = probe[2] - probe[0]
  text_h = probe[3] - probe[1]
  sym_x = (size - text_w) // 2 - probe[0]
  sym_y = (size - text_h) // 2 - probe[1]
  shadow_off = int(style.get("symbol_shadow_offset", 3))
  if shadow_off:
    symbol_draw.text(
      (sym_x + shadow_off, sym_y + shadow_off),
      symbol,
      font=symbol_font,
      fill=style.get("shadow", "#000000"),
      stroke_width=stroke_width,
      stroke_fill=style.get("stroke_color", "#000000"),
    )
  symbol_draw.text(
    (sym_x, sym_y),
    symbol,
    font=symbol_font,
    fill=style.get("color", "#FFFFFF"),
    stroke_width=stroke_width,
    stroke_fill=style.get("stroke_color", "#000000"),
  )
  sheet.paste(symbol_layer, (x0, y0), symbol_layer)
  return sheet


def _draw_strum_beat_number(
  draw: ImageDraw.ImageDraw,
  *,
  center_x: int,
  y: int,
  beat_number: int,
  number_cfg: dict,
  config: dict,
) -> None:
  label = str(beat_number)
  font_size = int(number_cfg.get("font_size", 30))
  font = load_font(config, font_size)
  pad_x = int(number_cfg.get("pill_padding_x", 14))
  pad_y = int(number_cfg.get("pill_padding_y", 4))
  bbox = draw.textbbox((0, 0), label, font=font)
  text_w = bbox[2] - bbox[0]
  text_h = bbox[3] - bbox[1]
  pill_w = text_w + pad_x * 2
  pill_h = text_h + pad_y * 2
  x0 = center_x - pill_w // 2
  y0 = y
  x1 = x0 + pill_w
  y1 = y0 + pill_h
  fill = number_cfg.get("pill_bg", "#141414")
  outline = number_cfg.get("pill_outline", "#FFD54F")
  draw.rounded_rectangle(
    (x0, y0, x1, y1),
    radius=int(number_cfg.get("pill_radius", 16)),
    fill=fill,
    outline=outline,
    width=int(number_cfg.get("pill_outline_width", 2)),
  )
  draw_text_centered(
    draw,
    label,
    center_x,
    y0 + pad_y - bbox[1],
    font,
    number_cfg.get("color", "#FFF8DC"),
    number_cfg.get("shadow_color", number_cfg.get("shadow", "#000000")),
    int(number_cfg.get("shadow_offset", 1)),
    number_cfg.get("stroke_color", "#000000"),
    int(number_cfg.get("stroke_width", 2)),
  )


def _draw_strum_connector(
  draw: ImageDraw.ImageDraw,
  *,
  x0: int,
  y0: int,
  x1: int,
  y1: int,
  connector_cfg: dict,
) -> None:
  color = connector_cfg.get("color", "#FFD54F")
  width = int(connector_cfg.get("width", 4))
  dot_r = int(connector_cfg.get("dot_radius", 5))
  mid_x = (x0 + x1) // 2
  mid_y = (y0 + y1) // 2
  draw.line([(x0, y0), (x1, y1)], fill=color, width=width)
  draw.ellipse(
    (mid_x - dot_r, mid_y - dot_r, mid_x + dot_r, mid_y + dot_r),
    fill=color,
    outline=connector_cfg.get("outline", "#1A0A00"),
    width=1,
  )


def _draw_strum_cell(
  canvas: Image.Image,
  *,
  center_x: int,
  center_y: int,
  size: int,
  token: str,
  beat_number: int,
  token_cfg: dict,
  number_cfg: dict,
  config: dict,
) -> None:
  style = token_cfg.get(token, {})
  if not style:
    raise ValueError(f"Missing strum token style for {token}")

  cell_img = _render_strum_cell_image(size=size, token=token, style=style, config=config)
  paste_x = center_x - cell_img.width // 2
  paste_y = center_y - cell_img.height // 2 - int(number_cfg.get("lift", 8))
  canvas.alpha_composite(cell_img, (paste_x, paste_y))

  if number_cfg.get("enabled", True):
    num_y = center_y + size // 2 + int(number_cfg.get("gap_below", 14))
    draw = ImageDraw.Draw(canvas)
    _draw_strum_beat_number(
      draw,
      center_x=center_x,
      y=num_y,
      beat_number=beat_number,
      number_cfg=number_cfg,
      config=config,
    )


def _draw_strum_legend_chip(
  canvas: Image.Image,
  *,
  center_x: int,
  center_y: int,
  token: str,
  label: str,
  token_cfg: dict,
  legend_cfg: dict,
  config: dict,
) -> int:
  style = token_cfg.get(token, {})
  chip_h = int(legend_cfg.get("chip_height", 58))
  icon_size = int(legend_cfg.get("icon_size", 38))
  gap = int(legend_cfg.get("chip_gap", 12))
  pad_x = int(legend_cfg.get("chip_padding_x", 18))
  font_size = int(legend_cfg.get("font_size", 40))
  font = load_font(config, font_size)

  draw_probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
  text_bbox = draw_probe.textbbox((0, 0), get_display(label), font=font)
  text_w = text_bbox[2] - text_bbox[0]
  chip_w = pad_x * 2 + icon_size + gap + text_w

  x0 = center_x - chip_w // 2
  y0 = center_y - chip_h // 2
  x1 = x0 + chip_w
  y1 = y0 + chip_h

  layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
  layer_draw = ImageDraw.Draw(layer)
  layer_draw.rounded_rectangle(
    (x0, y0, x1, y1),
    radius=int(legend_cfg.get("chip_radius", 28)),
    fill=(*_parse_hex_color(legend_cfg.get("chip_bg", "#141414")), 220),
    outline=legend_cfg.get("chip_outline", "#FFD54F"),
    width=int(legend_cfg.get("chip_outline_width", 2)),
  )
  canvas.alpha_composite(layer)

  icon = _render_strum_cell_image(size=icon_size, token=token, style=style, config=config)
  icon_x = x0 + pad_x + icon_size // 2 - icon.width // 2
  icon_y = center_y - icon.height // 2
  canvas.alpha_composite(icon, (icon_x, icon_y))

  draw = ImageDraw.Draw(canvas)
  text_x = x0 + pad_x + icon_size + gap
  text_y = center_y - (text_bbox[3] - text_bbox[1]) // 2 - text_bbox[1]
  draw.text(
    (text_x, text_y),
    get_display(label),
    font=font,
    fill=legend_cfg.get("color", "#FFFDF5"),
    stroke_width=int(legend_cfg.get("stroke_width", 2)),
    stroke_fill=legend_cfg.get("stroke_color", "#1A0A00"),
  )
  return chip_w


def _draw_strum_title_strip(
  draw: ImageDraw.ImageDraw,
  *,
  canvas_w: int,
  y: int,
  strip_cfg: dict,
) -> None:
  height = int(strip_cfg.get("height", 12))
  width_ratio = float(strip_cfg.get("width_ratio", 0.42))
  strip_w = max(1, int(canvas_w * width_ratio))
  x0 = (canvas_w - strip_w) // 2
  x1 = x0 + strip_w
  fill = strip_cfg.get("color", "#FFD54F")
  shadow = strip_cfg.get("outline", "#1A0A00")
  draw.rounded_rectangle((x0, y, x1, y + height), radius=height // 2, fill=fill, outline=shadow, width=2)


def generate_strum_slide(
  *,
  pattern: list[str],
  background: Path | None,
  output: Path,
  config: dict,
  title: str | None = None,
  subtitle: str | None = None,
) -> Path:
  tokens = parse_strum_pattern(pattern)
  strum_cfg = config.get("strum", {})
  canvas_w = config["canvas"]["width"]
  canvas_h = config["canvas"]["height"]

  bg_path = background or Path(config["background"])
  if not bg_path.is_absolute():
    bg_path = ROOT / bg_path if not bg_path.exists() else bg_path
  if not bg_path.exists():
    bg_path = ROOT / config["background"]
  if not bg_path.exists():
    raise FileNotFoundError(f"Background not found: {bg_path}")

  canvas = cover_resize(Image.open(bg_path).convert("RGB"), canvas_w, canvas_h)
  overlay_cfg = strum_cfg.get("overlay", config.get("intro", {}).get("overlay", {}))
  canvas = apply_intro_overlay(canvas, overlay_cfg).convert("RGBA")

  draw = ImageDraw.Draw(canvas)
  title_text = (title or strum_cfg.get("default_title", "הפריטה")).strip()
  title_style = strum_cfg.get("title", {})
  title_font = load_font(config, int(title_style.get("font_size", 118)))
  title_y = int(title_style.get("y", 280))
  draw_text_centered(
    draw,
    title_text,
    canvas_w // 2,
    title_y,
    title_font,
    title_style.get("color", "#FFD54F"),
    title_style.get("shadow_color", title_style.get("shadow", "#000000")),
    int(title_style.get("shadow_offset", 4)),
    title_style.get("stroke_color", "#1A0A00"),
    int(title_style.get("stroke_width", 5)),
  )
  title_strip_cfg = strum_cfg.get("title_strip", {})
  if title_strip_cfg.get("enabled", True):
    strip_y = title_y + int(title_style.get("font_size", 118)) + int(title_strip_cfg.get("gap", 18))
    _draw_strum_title_strip(draw, canvas_w=canvas_w, y=strip_y, strip_cfg=title_strip_cfg)

  grid_cfg = strum_cfg.get("grid", {})
  grid_base = int(grid_cfg.get("top_y", 650))
  cells_offset_y = int(grid_cfg.get("cells_offset_y", 0))
  grid_y0 = grid_base + cells_offset_y

  if subtitle and subtitle.strip():
    sub_style = strum_cfg.get("subtitle", {})
    sub_font = load_font(config, int(sub_style.get("font_size", 64)))
    sub_y = int(sub_style.get("y", 430))
    draw_text_centered(
      draw,
      subtitle.strip(),
      canvas_w // 2,
      sub_y,
      sub_font,
      sub_style.get("color", "#FFFDF5"),
      sub_style.get("shadow_color", sub_style.get("shadow", "#000000")),
      int(sub_style.get("shadow_offset", 3)),
      sub_style.get("stroke_color", "#1A0A00"),
      int(sub_style.get("stroke_width", 4)),
    )

  token_cfg = strum_cfg.get("tokens", {})
  number_cfg = strum_cfg.get("beat_numbers", {})
  connector_cfg = strum_cfg.get("connector", {})
  max_cols = int(grid_cfg.get("max_cols", 8))
  gap_x = int(grid_cfg.get("gap_x", 16))
  gap_y = int(grid_cfg.get("gap_y", 88))
  panel_cfg = grid_cfg.get("panel", {})
  cols, rows = _strum_layout_grid(len(tokens), max_cols)

  cell_size = int(grid_cfg.get("cell_size", 132))
  if cols >= 7:
    cell_size = int(grid_cfg.get("cell_size_compact", 104))

  grid_w = cols * cell_size + (cols - 1) * gap_x
  grid_h = rows * cell_size + (rows - 1) * gap_y
  if number_cfg.get("enabled", True):
    grid_h += int(number_cfg.get("extra_row_height", 52))

  grid_x0 = (canvas_w - grid_w) // 2
  panel_rect = None

  if panel_cfg.get("enabled", True):
    pad_x = int(panel_cfg.get("padding_x", 52))
    pad_y = int(panel_cfg.get("padding_y", 48))
    panel_rect = (
      grid_x0 - pad_x,
      grid_y0 - pad_y,
      grid_x0 + grid_w + pad_x,
      grid_y0 + grid_h + pad_y,
    )
    panel_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel_layer)
    rgb = _parse_hex_color(panel_cfg.get("color", "#0A0A0A"))
    opacity = float(panel_cfg.get("opacity", 0.88))
    radius = int(panel_cfg.get("radius", 44))
    panel_draw.rounded_rectangle(
      panel_rect,
      radius=radius,
      fill=(*rgb, int(255 * opacity)),
      outline=panel_cfg.get("outline", "#FFD54F"),
      width=int(panel_cfg.get("outline_width", 3)),
    )
    canvas.alpha_composite(panel_layer)
    accent_h = int(panel_cfg.get("accent_height", 10))
    accent_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    accent_draw = ImageDraw.Draw(accent_layer)
    accent_draw.rounded_rectangle(
      (
        panel_rect[0] + 24,
        panel_rect[1] + 14,
        panel_rect[2] - 24,
        panel_rect[1] + 14 + accent_h,
      ),
      radius=accent_h // 2,
      fill=panel_cfg.get("accent", "#FFD54F"),
    )
    canvas.alpha_composite(accent_layer)
    draw = ImageDraw.Draw(canvas)

  centers: list[tuple[int, int, str, int]] = []
  for index, token in enumerate(tokens):
    row = index // cols
    col_in_row = index % cols
    row_count = min(cols, len(tokens) - row * cols)
    row_w = row_count * cell_size + (row_count - 1) * gap_x
    row_x0 = grid_x0 + (grid_w - row_w) // 2
    # Beat 1 on the left, then left → right.
    col_pos = col_in_row
    cx = row_x0 + col_pos * (cell_size + gap_x) + cell_size // 2
    cy = grid_y0 + row * (cell_size + gap_y) + cell_size // 2
    centers.append((cx, cy, token, index + 1))

  if connector_cfg.get("enabled", True):
    connector_draw = ImageDraw.Draw(canvas)
    for i in range(len(centers) - 1):
      cx0, cy0, _t0, b0 = centers[i]
      cx1, cy1, _t1, b1 = centers[i + 1]
      if b1 != b0 + 1:
        continue
      if cy0 != cy1:
        continue
      _draw_strum_connector(
        connector_draw,
        x0=cx0 + cell_size // 2 + 4,
        y0=cy0,
        x1=cx1 - cell_size // 2 - 4,
        y1=cy1,
        connector_cfg=connector_cfg,
      )

  for cx, cy, token, beat_number in centers:
    _draw_strum_cell(
      canvas,
      center_x=cx,
      center_y=cy,
      size=cell_size,
      token=token,
      beat_number=beat_number,
      token_cfg=token_cfg,
      number_cfg=number_cfg,
      config=config,
    )

  legend_cfg = strum_cfg.get("legend", {})
  if legend_cfg.get("enabled", True):
    legend_y = int(legend_cfg.get("y", 1520))
    legend_gap = int(legend_cfg.get("gap", 22))
    entries = [
      ("D", legend_cfg.get("down_label", "↓ למטה")),
      ("U", legend_cfg.get("up_label", "↑ למעלה")),
      ("X", legend_cfg.get("mute_label", "× השתקה")),
    ]
    chip_widths = []
    for token_key, label in entries:
      style = token_cfg.get(token_key, {})
      icon_size = int(legend_cfg.get("icon_size", 38))
      pad_x = int(legend_cfg.get("chip_padding_x", 18))
      gap = int(legend_cfg.get("chip_gap", 12))
      font = load_font(config, int(legend_cfg.get("font_size", 40)))
      probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
      bbox = probe.textbbox((0, 0), get_display(label), font=font)
      chip_widths.append(pad_x * 2 + icon_size + gap + (bbox[2] - bbox[0]))
    total_w = sum(chip_widths) + legend_gap * (len(entries) - 1)
    x = (canvas_w - total_w) // 2
    for (token_key, label), chip_w in zip(entries, chip_widths, strict=True):
      _draw_strum_legend_chip(
        canvas,
        center_x=x + chip_w // 2,
        center_y=legend_y,
        token=token_key,
        label=label,
        token_cfg=token_cfg,
        legend_cfg=legend_cfg,
        config=config,
      )
      x += chip_w + legend_gap

  output.parent.mkdir(parents=True, exist_ok=True)
  canvas.convert("RGB").save(output, "PNG", optimize=True)
  return output


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
  accent_strip_without_badge: bool = False,
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
  accent_strip_theme = None
  if (
    slide_style == "intro"
    and not theme
    and accent_strip_without_badge
  ):
    accent_strip_theme = get_thumbnail_default_strip_theme(config)
  layout_cfg = slide_cfg.get("layout", {})
  use_centered = slide_style in ("intro", "outro") and layout_cfg.get("centered", False)

  if use_centered:
    block = build_centered_intro_layout(
      canvas_w=canvas_w,
      canvas_h=canvas_h,
      lines=lines,
      style_templates=style_templates,
      theme=theme,
      config=config,
      slide_cfg=slide_cfg,
      accent_strip_theme=accent_strip_theme,
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
    elif block.get("accent_strip_theme") and block.get("strip_top") is not None:
      draw_difficulty_color_strip(
        draw,
        canvas_w=canvas_w,
        top_y=block["strip_top"],
        theme=block["accent_strip_theme"],
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
