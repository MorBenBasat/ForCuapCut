#!/usr/bin/env python3
"""Create placeholder assets for testing. Replace with your real images."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent


def save_chord(name: str, path: Path) -> None:
  img = Image.new("RGBA", (400, 500), (255, 255, 255, 255))
  draw = ImageDraw.Draw(img)
  draw.rectangle((20, 60, 380, 440), outline="black", width=3)
  draw.text((180, 15), name, fill="black")
  for i in range(6):
    x = 50 + i * 55
    draw.line((x, 60, x, 440), fill="black", width=2)
  for i in range(5):
    y = 60 + i * 75
    draw.line((20, y, 380, y), fill="black", width=2)
  path.parent.mkdir(parents=True, exist_ok=True)
  img.save(path)


def save_background(path: Path) -> None:
  img = Image.new("RGB", (1080, 1920), (40, 35, 30))
  draw = ImageDraw.Draw(img)
  for y in range(1920):
    shade = int(40 + (y / 1920) * 80)
    draw.line((0, y, 1080, y), fill=(shade, shade // 2, shade // 3))
  draw.ellipse((340, 700, 740, 1100), outline=(80, 60, 40), width=8)
  path.parent.mkdir(parents=True, exist_ok=True)
  img.save(path, quality=90)


def save_artist(path: Path) -> None:
  img = Image.new("RGB", (500, 500), (120, 120, 120))
  draw = ImageDraw.Draw(img)
  draw.ellipse((100, 80, 400, 380), fill=(180, 140, 110))
  path.parent.mkdir(parents=True, exist_ok=True)
  img.save(path, quality=90)


def main() -> None:
  chords = ["Em", "C", "G", "D", "Am", "F", "Dm"]
  for chord in chords:
    save_chord(chord, ROOT / "assets" / "chords" / f"{chord}.png")

  save_background(ROOT / "assets" / "backgrounds" / "guitar.jpg")
  save_artist(ROOT / "input" / "dudu_aharon.jpg")
  save_artist(ROOT / "input" / "osher_cohen.jpg")
  print("Test assets created in assets/ and input/")
  print("Replace them with your real images, then run:")
  print("  python generate.py songs/bachor_ragish.yaml")


if __name__ == "__main__":
  main()
