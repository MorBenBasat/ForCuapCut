#!/usr/bin/env python3
"""Generate guitar chord diagram PNGs matching assets/chords/ style."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
CHORDS_DIR = ROOT / "assets" / "chords"

# Layout measured from existing chord assets (353×510).
CANVAS = (353, 510)
STRINGS_X = [63, 112, 162, 211, 260, 310]
NUT_TOP = 129
NUT_BOTTOM = 138
FRET_Y = [198, 257, 316, 376, 435]
DOT_RADIUS = 22
OPEN_RADIUS = 10
LINE = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)

FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
FONT_REG = "C:/Windows/Fonts/arial.ttf"


@dataclass
class Finger:
  string: int  # 1 = high E … 6 = low E
  fret: int
  finger: int | None = None


@dataclass
class ChordDef:
  name: str
  muted: set[int]
  open_strings: set[int]
  fingers: list[Finger]
  barre: tuple[int, int, int] | None = None  # fret, from_string, to_string
  bottom: list[str] | None = None
  labels_in_dots: bool = False  # G/C style: plain dots, finger numbers below
  start_fret: int = 1  # when > 1, diagram begins here (shows "4fr" label)


CHORDS: dict[str, ChordDef] = {
  "Em": ChordDef(
    name="Em",
    muted=set(),
    open_strings={6, 3, 2, 1},
    fingers=[
      Finger(5, 2, 2),
      Finger(4, 2, 3),
    ],
    bottom=["", "2", "3", "", "", ""],
    labels_in_dots=False,
  ),
  "E": ChordDef(
    name="E",
    muted=set(),
    open_strings={6, 2, 1},
    fingers=[
      Finger(5, 2, 2),
      Finger(4, 2, 3),
      Finger(3, 1, 1),
    ],
    bottom=["", "2", "3", "1", "", ""],
    labels_in_dots=False,
  ),
  "G": ChordDef(
    name="G",
    muted=set(),
    open_strings={4, 3, 2},
    fingers=[
      Finger(6, 3, 3),
      Finger(5, 2, 2),
      Finger(1, 3, 4),
    ],
    bottom=["3", "2", "", "", "", "4"],
    labels_in_dots=False,
  ),
  "C": ChordDef(
    name="C",
    muted={6},
    open_strings={3, 1},
    fingers=[
      Finger(5, 3, 3),
      Finger(4, 2, 2),
      Finger(2, 1, 1),
    ],
    bottom=["", "3", "2", "", "", "1"],
    labels_in_dots=False,
  ),
  "D": ChordDef(
    name="D",
    muted={6, 5},
    open_strings={4},
    fingers=[
      Finger(3, 2, 1),
      Finger(1, 2, 2),
      Finger(2, 3, 3),
    ],
    bottom=["X", "X", "", "1", "3", "2"],
    labels_in_dots=False,
  ),
  "A": ChordDef(
    name="A",
    muted={6},
    open_strings={5, 1},
    fingers=[
      Finger(4, 2, 1),
      Finger(3, 2, 2),
      Finger(2, 2, 3),
    ],
    bottom=["X", "", "1", "2", "3", ""],
    labels_in_dots=False,
  ),
  "Am": ChordDef(
    name="Am",
    muted={6},
    open_strings={5, 1},
    fingers=[
      Finger(2, 1, 1),
      Finger(4, 2, 2),
      Finger(3, 2, 3),
    ],
    bottom=["X", "", "2", "3", "1", ""],
    labels_in_dots=False,
  ),
  "Dm": ChordDef(
    name="Dm",
    muted={6, 5},
    open_strings={4},
    fingers=[
      Finger(1, 1, 1),
      Finger(3, 2, 2),
      Finger(2, 3, 3),
    ],
    bottom=["X", "X", "", "2", "3", "1"],
    labels_in_dots=False,
  ),
  "F": ChordDef(
    name="F",
    muted=set(),
    open_strings=set(),
    barre=(1, 6, 1),
    fingers=[
      Finger(6, 1, 1),
      Finger(5, 3, 3),
      Finger(4, 3, 4),
      Finger(3, 2, 2),
      Finger(2, 1, 1),
      Finger(1, 1, 1),
    ],
    bottom=["1", "3", "4", "2", "1", "1"],
    labels_in_dots=False,
  ),
  "CMAJ7": ChordDef(
    name="C maj7",
    muted=set(),
    open_strings={6, 3, 2, 1},
    fingers=[
      Finger(5, 3, 3),
      Finger(4, 2, 2),
    ],
    bottom=["", "3", "2", "", "", ""],
    labels_in_dots=False,
  ),
  "Bm": ChordDef(
    name="Bm",
    muted={6},
    open_strings=set(),
    barre=(2, 5, 1),
    fingers=[
      Finger(5, 2, 1),
      Finger(4, 4, 3),
      Finger(3, 4, 4),
      Finger(2, 3, 2),
      Finger(1, 2, 1),
    ],
    bottom=["X", "1", "3", "4", "2", "1"],
    labels_in_dots=False,
  ),
  "F#": ChordDef(
    name="F#",
    muted=set(),
    open_strings=set(),
    barre=(2, 6, 1),
    fingers=[
      Finger(6, 2, 1),
      Finger(5, 4, 3),
      Finger(4, 4, 4),
      Finger(3, 3, 2),
      Finger(2, 2, 1),
      Finger(1, 2, 1),
    ],
    bottom=["1", "3", "4", "2", "1", "1"],
    labels_in_dots=False,
  ),
  "C#m7b5": ChordDef(
    name="C#m7b5",
    muted={6, 1},
    open_strings=set(),
    fingers=[
      Finger(5, 4, 1),
      Finger(3, 4, 2),
      Finger(4, 5, 3),
      Finger(2, 5, 4),
    ],
    bottom=["X", "1", "3", "2", "4", "X"],
    labels_in_dots=False,
  ),
  "GMAJ7": ChordDef(
    name="G maj7",
    muted=set(),
    open_strings=set(),
    barre=(3, 6, 1),
    fingers=[
      Finger(6, 3, 1),
      Finger(5, 3, 1),
      Finger(4, 5, 4),
      Finger(3, 4, 2),
      Finger(2, 4, 3),
      Finger(1, 3, 1),
    ],
    bottom=["1", "1", "4", "2", "3", "1"],
    labels_in_dots=False,
  ),
  "C#m": ChordDef(
    name="C#m",
    muted={6},
    open_strings=set(),
    barre=(4, 5, 1),
    fingers=[
      Finger(5, 4, 1),
      Finger(4, 6, 3),
      Finger(3, 6, 4),
      Finger(2, 5, 2),
      Finger(1, 4, 1),
    ],
    bottom=["X", "1", "3", "4", "2", "1"],
    start_fret=4,
  ),
  "D#m": ChordDef(
    name="D#m",
    muted={6},
    open_strings=set(),
    barre=(6, 5, 1),
    fingers=[
      Finger(5, 6, 1),
      Finger(4, 8, 3),
      Finger(3, 8, 4),
      Finger(2, 7, 2),
      Finger(1, 6, 1),
    ],
    bottom=["X", "1", "3", "4", "2", "1"],
    start_fret=6,
  ),
  "Ebm": ChordDef(
    name="Ebm",
    muted={6},
    open_strings=set(),
    barre=(6, 5, 1),
    fingers=[
      Finger(5, 6, 1),
      Finger(4, 8, 3),
      Finger(3, 8, 4),
      Finger(2, 7, 2),
      Finger(1, 6, 1),
    ],
    bottom=["X", "1", "3", "4", "2", "1"],
    start_fret=6,
  ),
  "G#m": ChordDef(
    name="G#m",
    muted=set(),
    open_strings=set(),
    barre=(4, 6, 1),
    fingers=[
      Finger(6, 4, 1),
      Finger(5, 6, 3),
      Finger(4, 6, 4),
      Finger(3, 4, 1),
      Finger(2, 4, 1),
      Finger(1, 4, 1),
    ],
    bottom=["1", "3", "4", "1", "1", "1"],
    start_fret=4,
  ),
  "B": ChordDef(
    name="B",
    muted={6},
    open_strings=set(),
    barre=(2, 5, 1),
    fingers=[
      Finger(5, 2, 1),
      Finger(4, 4, 3),
      Finger(3, 4, 4),
      Finger(2, 4, 2),
      Finger(1, 2, 1),
    ],
    bottom=["X", "1", "3", "4", "2", "1"],
  ),
}


CHORD_ALIASES: dict[str, str] = {
  "Cmaj7": "CMAJ7",
  "cmaj7": "CMAJ7",
  "Gmaj7": "GMAJ7",
  "gmaj7": "GMAJ7",
  "AM": "Am",
  "am": "Am",
  "DM": "Dm",
  "dm": "Dm",
  "EM": "Em",
  "em": "Em",
}


def resolve_chord_key(name: str) -> str:
  key = name.strip()
  if key in CHORDS:
    return key
  if key in CHORD_ALIASES:
    return CHORD_ALIASES[key]
  title = key[0].upper() + key[1:] if len(key) > 1 else key.upper()
  if title in CHORDS:
    return title
  if title in CHORD_ALIASES:
    return CHORD_ALIASES[title]
  raise SystemExit(f"Unknown chord '{name}'. Add it to CHORDS in generate_chord.py.")


def chord_asset_key(name: str) -> str | None:
  """Return PNG filename stem for a chord name, or None if unknown."""
  key = name.strip()
  if not key:
    return None
  try:
    return resolve_chord_key(key)
  except SystemExit:
    return None


def display_fret(absolute_fret: int, start_fret: int) -> int:
  return absolute_fret - start_fret + 1


def fret_center_y(fret: int) -> int:
  if fret == 0:
    return (NUT_BOTTOM + FRET_Y[0]) // 2
  if fret <= len(FRET_Y):
    top = NUT_BOTTOM if fret == 1 else FRET_Y[fret - 2]
    bottom = FRET_Y[fret - 1]
    return (top + bottom) // 2
  raise ValueError(f"Fret {fret} out of range")


def string_x(string_num: int) -> int:
  return STRINGS_X[6 - string_num]


def load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
  try:
    return ImageFont.truetype(path, size)
  except OSError:
    return ImageFont.load_default()


def draw_centered_text(
  draw: ImageDraw.ImageDraw,
  xy: tuple[int, int],
  text: str,
  font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
  fill=LINE,
) -> None:
  bbox = draw.textbbox((0, 0), text, font=font)
  tw = bbox[2] - bbox[0]
  th = bbox[3] - bbox[1]
  x = xy[0] - tw // 2 - bbox[0]
  y = xy[1] - th // 2 - bbox[1]
  draw.text((x, y), text, font=font, fill=fill)


def draw_open_marker(draw: ImageDraw.ImageDraw, string_num: int) -> None:
  x = string_x(string_num)
  y = 104
  draw.ellipse(
    (x - OPEN_RADIUS, y - OPEN_RADIUS, x + OPEN_RADIUS, y + OPEN_RADIUS),
    outline=LINE,
    width=2,
  )


def draw_mute_marker(draw: ImageDraw.ImageDraw, string_num: int) -> None:
  x = string_x(string_num)
  y = 104
  font = load_font(FONT_BOLD, 22)
  draw_centered_text(draw, (x, y), "X", font)


def draw_dot(
  draw: ImageDraw.ImageDraw,
  string_num: int,
  fret: int,
  label: str | None = None,
) -> None:
  x = string_x(string_num)
  y = fret_center_y(fret)
  draw.ellipse(
    (x - DOT_RADIUS, y - DOT_RADIUS, x + DOT_RADIUS, y + DOT_RADIUS),
    fill=LINE,
  )
  if label:
    font = load_font(FONT_BOLD, 28)
    draw_centered_text(draw, (x, y), label, font, fill=WHITE)


def draw_barre(
  draw: ImageDraw.ImageDraw,
  fret: int,
  from_string: int,
  to_string: int,
) -> None:
  x0 = string_x(max(from_string, to_string))
  x1 = string_x(min(from_string, to_string))
  y = fret_center_y(fret)
  height = 14
  draw.rounded_rectangle(
    (x0 - DOT_RADIUS, y - height // 2, x1 + DOT_RADIUS, y + height // 2),
    radius=8,
    fill=LINE,
  )


def draw_grid(draw: ImageDraw.ImageDraw, start_fret: int = 1) -> None:
  top = NUT_TOP
  bottom = FRET_Y[-1]
  left = STRINGS_X[0]
  right = STRINGS_X[-1]

  for y in FRET_Y:
    draw.line((left, y, right, y), fill=LINE, width=2)
  if start_fret <= 1:
    draw.rectangle((left, NUT_TOP, right, NUT_BOTTOM), fill=LINE)
  else:
    draw.line((left, NUT_TOP, right, NUT_TOP), fill=LINE, width=2)
  for x in STRINGS_X:
    draw.line((x, top, x, bottom), fill=LINE, width=2)


def render_chord(chord: ChordDef) -> Image.Image:
  img = Image.new("RGBA", CANVAS, WHITE)
  draw = ImageDraw.Draw(img)
  start = chord.start_fret

  title_font = load_font(FONT_BOLD, 38)
  bottom_font = load_font(FONT_REG, 28)
  draw_centered_text(draw, (CANVAS[0] // 2, 48), chord.name, title_font)

  draw_grid(draw, start)

  if start > 1:
    label_font = load_font(FONT_BOLD, 24)
    draw_centered_text(
      draw,
      (28, fret_center_y(1)),
      f"{start}fr",
      label_font,
    )

  for s in chord.muted:
    draw_mute_marker(draw, s)
  for s in chord.open_strings:
    draw_open_marker(draw, s)

  if chord.barre:
    fret, s_from, s_to = chord.barre
    draw_barre(draw, display_fret(fret, start), s_from, s_to)

  labeled: set[tuple[int, int]] = set()
  for finger in chord.fingers:
    disp = display_fret(finger.fret, start)
    key = (finger.string, disp)
    label = None
    if chord.labels_in_dots and finger.finger is not None:
      label = str(finger.finger)
    if chord.barre:
      barre_fret, _, _ = chord.barre
      if finger.fret == barre_fret and (
        finger.string != 5 or not chord.labels_in_dots
      ):
        if key not in labeled:
          labeled.add(key)
        continue
    if key in labeled:
      continue
    labeled.add(key)
    draw_dot(draw, finger.string, disp, label)

  if chord.bottom:
    for idx, label in enumerate(chord.bottom):
      if not label:
        continue
      string_num = 6 - idx
      draw_centered_text(draw, (string_x(string_num), 470), label, bottom_font)

  return img


def main() -> None:
  parser = argparse.ArgumentParser(description="Generate chord diagram PNGs.")
  parser.add_argument(
    "names",
    nargs="*",
    help="Chord names (e.g. Bm). Omit with --all to regenerate every chord.",
  )
  parser.add_argument(
    "--all",
    action="store_true",
    help="Regenerate all chords defined in CHORDS",
  )
  parser.add_argument(
    "--out-dir",
    type=Path,
    default=CHORDS_DIR,
    help="Output directory (default: assets/chords)",
  )
  args = parser.parse_args()

  args.out_dir.mkdir(parents=True, exist_ok=True)
  names = list(CHORDS.keys()) if args.all else args.names
  if not names:
    parser.error("Provide chord names or use --all")
  for name in names:
    lookup = resolve_chord_key(name)
    out = args.out_dir / f"{lookup}.png"
    render_chord(CHORDS[lookup]).save(out)
    print(f"Saved {out}")


if __name__ == "__main__":
  main()
