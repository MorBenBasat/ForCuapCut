#!/usr/bin/env python3
"""Web UI for ForCuapCut — same workflow as Telegram bot."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

from generate import (
  DIFFICULTY_LEVELS,
  ROOT,
  generate_intro_slide,
  generate_song_slide,
  generate_strum_slide,
  load_config,
  load_session_difficulty,
  normalize_difficulty,
  parse_strum_pattern,
  resolve_artist_image,
  save_session_difficulty,
  song_slug,
)
from generate_video import generate_song_video_from_data
from telegram_bot import (
  CONFIG_PATH,
  INPUT_DIR,
  OUTPUT_DIR,
  add_artist_to_config,
  artist_file_slug,
  find_artist_match,
  get_desktop_dir,
  remove_artist,
  safe_filename,
)

WEB_DIR = ROOT / "web"
app = Flask(
  __name__,
  template_folder=str(WEB_DIR / "templates"),
  static_folder=str(WEB_DIR / "static"),
  static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


def _json_error(message: str, status: int = 400):
  return jsonify({"ok": False, "error": message}), status


def _file_url(path: Path) -> str:
  rel = path.relative_to(OUTPUT_DIR)
  return f"/output/{rel.as_posix()}"


def _parse_chords(raw) -> list[str]:
  if isinstance(raw, list):
    parsed = raw
  else:
    text = str(raw).strip()
    if text.startswith("["):
      parsed = json.loads(text)
    else:
      parsed = [c.strip() for c in text.split(",") if c.strip()]
  chords = [str(c).strip() for c in parsed if str(c).strip()]
  if not 4 <= len(chords) <= 8:
    raise ValueError(f"צריך 4–8 אקורדים, קיבלתי {len(chords)}.")
  return chords


THUMB_PREVIEW_FILENAME = "_thumb_preview.png"
STRUM_PREVIEW_FILENAME = "_strum_preview.png"


def _parse_thumbnail_lines(form) -> list[str]:
  lines: list[str] = []
  for index in range(1, 5):
    line = (form.get(f"line{index}") or "").strip()
    if line:
      lines.append(line)
  if len(lines) < 3:
    raise ValueError("צריך לפחות 3 שורות טקסט.")
  if len(lines) > 4:
    raise ValueError("מקסימום 4 שורות.")
  return lines


def _save_thumbnail_upload(upload) -> Path:
  thumb_dir = INPUT_DIR / "thumbnails"
  thumb_dir.mkdir(parents=True, exist_ok=True)
  ext = Path(upload.filename or "").suffix.lower()
  if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
    ext = ".png"
  dest = thumb_dir / f"upload{ext}"
  upload.save(dest)
  return dest


def _thumbnail_background_from_request(config: dict):
  """Return custom background path, or None to use default guitar."""
  upload = request.files.get("thumbnail")
  if upload is None or not upload.filename:
    return None
  return _save_thumbnail_upload(upload)


def _render_thumbnail_slide(
  *,
  lines: list[str],
  difficulty: str | None,
  background: Path | None,
  output: Path,
  config: dict,
) -> Path:
  return generate_intro_slide(
    lines=lines,
    background=background,
    output=output,
    config=config,
    slide_style="intro",
    difficulty=difficulty,
    accent_strip_without_badge=True,
  )


def _parse_strum_pattern_field(raw) -> list[str]:
  if raw is None or raw == "":
    raise ValueError("חסרה פריטה.")
  if isinstance(raw, str):
    try:
      parsed = json.loads(raw)
    except json.JSONDecodeError:
      parsed = raw
  else:
    parsed = raw
  return parse_strum_pattern(parsed)


def _save_strum_background_upload(upload) -> Path:
  bg_dir = INPUT_DIR / "strum"
  bg_dir.mkdir(parents=True, exist_ok=True)
  ext = Path(upload.filename or "").suffix.lower()
  if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
    ext = ".png"
  dest = bg_dir / f"upload{ext}"
  upload.save(dest)
  return dest


def _strum_background_from_request():
  upload = request.files.get("background")
  if upload is None or not upload.filename:
    return None
  return _save_strum_background_upload(upload)


def _save_song_background_upload(upload) -> Path:
  bg_dir = INPUT_DIR / "songs"
  bg_dir.mkdir(parents=True, exist_ok=True)
  ext = Path(upload.filename or "").suffix.lower()
  if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
    ext = ".png"
  dest = bg_dir / f"upload{ext}"
  upload.save(dest)
  return dest


def _song_background_from_request():
  upload = request.files.get("background")
  if upload is None or not upload.filename:
    return None
  return _save_song_background_upload(upload)


def _save_outro_background_upload(upload) -> Path:
  bg_dir = INPUT_DIR / "outro"
  bg_dir.mkdir(parents=True, exist_ok=True)
  ext = Path(upload.filename or "").suffix.lower()
  if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
    ext = ".png"
  dest = bg_dir / f"upload{ext}"
  upload.save(dest)
  return dest


def _outro_background_from_request():
  upload = request.files.get("background")
  if upload is None or not upload.filename:
    return None
  return _save_outro_background_upload(upload)


def _render_strum_slide(
  *,
  pattern: list[str],
  background: Path | None,
  output: Path,
  config: dict,
  title: str | None,
  subtitle: str | None,
) -> Path:
  return generate_strum_slide(
    pattern=pattern,
    background=background,
    output=output,
    config=config,
    title=title,
    subtitle=subtitle,
  )


def _parse_times(chords: list[str], times_raw) -> list[dict]:
  if isinstance(times_raw, list):
    times = [float(t) for t in times_raw]
  else:
    times = [float(t) for t in json.loads(times_raw)]
  if len(times) != len(chords):
    raise ValueError(f"צריך {len(chords)} זמנים, קיבלתי {len(times)}.")
  if times[0] != 0:
    raise ValueError("האקורד הראשון חייב להתחיל ב-0 שניות.")
  for i in range(1, len(times)):
    if times[i] <= times[i - 1]:
      raise ValueError("הזמנים חייבים לעלות.")
  return [{"chord": i + 1, "at": t} for i, t in enumerate(times)]


@app.get("/")
def index():
  return render_template("index.html")


@app.get("/output/<path:filename>")
def serve_output(filename: str):
  return send_from_directory(OUTPUT_DIR, filename)


@app.get("/api/artists")
def api_artists():
  config = load_config(CONFIG_PATH)
  artists = sorted(config.get("artists", {}))
  return jsonify({"ok": True, "artists": artists})


@app.get("/api/session")
def api_session():
  return jsonify({
    "ok": True,
    "difficulty": load_session_difficulty(),
    "difficulty_levels": list(DIFFICULTY_LEVELS),
  })


@app.get("/api/chords")
def api_chords():
  chords_dir = ROOT / "assets" / "chords"
  names = sorted({p.stem for p in chords_dir.glob("*.png")})
  return jsonify({"ok": True, "chords": names})


@app.post("/api/slide")
def api_slide():
  if request.is_json:
    data = request.get_json(silent=True) or {}
    artist = (data.get("artist") or "").strip()
    song = (data.get("song") or "").strip()
    difficulty = normalize_difficulty(data.get("difficulty"))
    chords_raw = data.get("chords", [])
    background = None
  else:
    artist = (request.form.get("artist") or "").strip()
    song = (request.form.get("song") or "").strip()
    difficulty = normalize_difficulty(request.form.get("difficulty"))
    chords_raw = request.form.get("chords", "[]")
    background = _song_background_from_request()

  if not artist or not song:
    return _json_error("חסר זמר או שם שיר.")

  try:
    chords = _parse_chords(chords_raw)
  except (ValueError, json.JSONDecodeError) as exc:
    return _json_error(str(exc))

  if difficulty:
    save_session_difficulty(difficulty)
  else:
    difficulty = load_session_difficulty()

  try:
    config = load_config(CONFIG_PATH)
    artist_image = resolve_artist_image(config, artist)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / safe_filename(song)

    result = generate_song_slide(
      artist=artist,
      song=song,
      chord_names=chords,
      artist_image=artist_image,
      background=background,
      output=output,
      config=config,
      difficulty=difficulty,
    )

    desktop_dir = get_desktop_dir()
    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = desktop_dir / safe_filename(song)
    shutil.copy2(result, desktop_path)

    numbered = [{"num": i + 1, "chord": c} for i, c in enumerate(chords)]
    return jsonify({
      "ok": True,
      "file": _file_url(result),
      "filename": result.name,
      "desktop_filename": desktop_path.name,
      "chords_numbered": numbered,
      "artist": artist,
      "song": song,
      "difficulty": difficulty,
      "used_custom_background": background is not None,
    })
  except Exception as exc:
    return _json_error(str(exc), 500)


@app.post("/api/video")
def api_video():
  artist = (request.form.get("artist") or "").strip()
  song = (request.form.get("song") or "").strip()
  difficulty = normalize_difficulty(request.form.get("difficulty")) or load_session_difficulty() or "קל"

  if not artist or not song:
    return _json_error("חסר זמר או שם שיר.")

  audio = request.files.get("audio")
  if audio is None or not audio.filename:
    return _json_error("חסר קובץ שיר (MP3).")

  try:
    chords = _parse_chords(request.form.get("chords", "[]"))
    times = _parse_times(chords, request.form.get("times", "[]"))
  except (ValueError, json.JSONDecodeError) as exc:
    return _json_error(str(exc))

  end_raw = (request.form.get("end") or "").strip()
  end_seconds = float(end_raw) if end_raw else None
  if end_seconds is not None and end_seconds <= times[-1]["at"]:
    return _json_error(
      f"אורך הסרטון ({end_seconds}) חייב להיות אחרי האקורד האחרון ({times[-1]['at']})."
    )

  try:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ext = Path(audio.filename).suffix.lower() or ".mp3"
    if ext not in {".mp3", ".m4a", ".wav", ".ogg", ".aac"}:
      ext = ".mp3"
    slug = song_slug(song)
    audio_path = INPUT_DIR / f"{slug}{ext}"
    audio.save(audio_path)

    output = OUTPUT_DIR / f"{slug}.mp4"
    config = load_config(CONFIG_PATH)
    video_data = {
      "artist": artist,
      "song": song,
      "chords": chords,
      "timeline": times,
      "difficulty": difficulty,
      "audio": str(audio_path.relative_to(ROOT)).replace("\\", "/"),
      "output": str(output.relative_to(ROOT)).replace("\\", "/"),
    }
    if end_seconds is not None:
      video_data["end"] = end_seconds

    result = generate_song_video_from_data(
      video_data,
      config=config,
      config_path=CONFIG_PATH,
    )
    return jsonify({
      "ok": True,
      "file": _file_url(result),
      "filename": result.name,
    })
  except Exception as exc:
    return _json_error(str(exc), 500)


@app.post("/api/intro")
def api_intro():
  data = request.get_json(silent=True) or {}
  lines = [str(line).strip() for line in data.get("lines", []) if str(line).strip()]
  difficulty = normalize_difficulty(data.get("difficulty"))

  if len(lines) < 3:
    return _json_error("לפתיחה צריך לפחות 3 שורות.")
  if len(lines) > 4:
    return _json_error("לפתיחה יש מקסימום 4 שורות.")
  if not difficulty:
    return _json_error("חובה לבחור רמת קושי לפתיחה.")

  save_session_difficulty(difficulty)
  try:
    config = load_config(CONFIG_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = OUTPUT_DIR / "intro.png"
    result = generate_intro_slide(
      lines=lines,
      background=None,
      output=output,
      config=config,
      slide_style="intro",
      difficulty=difficulty,
    )
    return jsonify({
      "ok": True,
      "file": _file_url(result),
      "filename": result.name,
      "difficulty": difficulty,
    })
  except Exception as exc:
    return _json_error(str(exc), 500)


@app.post("/api/thumbnail/preview")
def api_thumbnail_preview():
  try:
    lines = _parse_thumbnail_lines(request.form)
  except ValueError as exc:
    return _json_error(str(exc))

  difficulty = normalize_difficulty(request.form.get("difficulty")) or None

  try:
    config = load_config(CONFIG_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    background = _thumbnail_background_from_request(config)
    preview_path = OUTPUT_DIR / THUMB_PREVIEW_FILENAME
    _render_thumbnail_slide(
      lines=lines,
      difficulty=difficulty,
      background=background,
      output=preview_path,
      config=config,
    )
    return jsonify({
      "ok": True,
      "file": _file_url(preview_path),
      "used_custom_background": background is not None,
      "has_difficulty": difficulty is not None,
    })
  except Exception as exc:
    return _json_error(str(exc), 500)


@app.post("/api/thumbnail")
def api_thumbnail():
  try:
    lines = _parse_thumbnail_lines(request.form)
  except ValueError as exc:
    return _json_error(str(exc))

  difficulty = normalize_difficulty(request.form.get("difficulty")) or None

  song_label = (request.form.get("song") or "").strip() or lines[1]
  if difficulty:
    save_session_difficulty(difficulty)

  try:
    config = load_config(CONFIG_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    background = _thumbnail_background_from_request(config)
    stem = song_slug(song_label) or "thumbnail"
    output = OUTPUT_DIR / safe_filename(f"{stem}_thumbnail", ".png")
    result = _render_thumbnail_slide(
      lines=lines,
      difficulty=difficulty,
      background=background,
      output=output,
      config=config,
    )

    desktop_dir = get_desktop_dir()
    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = desktop_dir / result.name
    shutil.copy2(result, desktop_path)

    return jsonify({
      "ok": True,
      "file": _file_url(result),
      "filename": result.name,
      "desktop_filename": desktop_path.name,
      "difficulty": difficulty,
      "has_difficulty": difficulty is not None,
      "used_custom_background": background is not None,
    })
  except Exception as exc:
    return _json_error(str(exc), 500)


@app.post("/api/strum/preview")
def api_strum_preview():
  try:
    pattern = _parse_strum_pattern_field(request.form.get("pattern"))
  except ValueError as exc:
    return _json_error(str(exc))

  title = (request.form.get("title") or "").strip() or None
  subtitle = (request.form.get("subtitle") or "").strip() or None

  try:
    config = load_config(CONFIG_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    background = _strum_background_from_request()
    preview_path = OUTPUT_DIR / STRUM_PREVIEW_FILENAME
    _render_strum_slide(
      pattern=pattern,
      background=background,
      output=preview_path,
      config=config,
      title=title,
      subtitle=subtitle,
    )
    return jsonify({
      "ok": True,
      "file": _file_url(preview_path),
      "used_custom_background": background is not None,
      "beat_count": len(pattern),
    })
  except Exception as exc:
    return _json_error(str(exc), 500)


@app.post("/api/strum")
def api_strum():
  try:
    pattern = _parse_strum_pattern_field(request.form.get("pattern"))
  except ValueError as exc:
    return _json_error(str(exc))

  title = (request.form.get("title") or "").strip() or None
  subtitle = (request.form.get("subtitle") or "").strip() or None
  song_label = (request.form.get("song") or "").strip() or subtitle or "strum"

  try:
    config = load_config(CONFIG_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    background = _strum_background_from_request()
    stem = song_slug(song_label) or "strum"
    output = OUTPUT_DIR / safe_filename(f"{stem}_strum", ".png")
    result = _render_strum_slide(
      pattern=pattern,
      background=background,
      output=output,
      config=config,
      title=title,
      subtitle=subtitle,
    )

    desktop_dir = get_desktop_dir()
    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = desktop_dir / result.name
    shutil.copy2(result, desktop_path)

    return jsonify({
      "ok": True,
      "file": _file_url(result),
      "filename": result.name,
      "desktop_filename": desktop_path.name,
      "used_custom_background": background is not None,
      "beat_count": len(pattern),
    })
  except Exception as exc:
    return _json_error(str(exc), 500)


@app.post("/api/outro")
def api_outro():
  lines: list[str] = []
  for index in range(1, 5):
    line = (request.form.get(f"line{index}") or "").strip()
    if line:
      lines.append(line)

  if len(lines) < 3:
    return _json_error("לסיום צריך לפחות 3 שורות.")
  if len(lines) > 4:
    return _json_error("לסיום יש מקסימום 4 שורות.")

  try:
    config = load_config(CONFIG_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    background = _outro_background_from_request()
    output = OUTPUT_DIR / "outro.png"
    result = generate_intro_slide(
      lines=lines,
      background=background,
      output=output,
      config=config,
      slide_style="outro",
    )
    return jsonify({
      "ok": True,
      "file": _file_url(result),
      "filename": result.name,
      "used_custom_background": background is not None,
    })
  except Exception as exc:
    return _json_error(str(exc), 500)


@app.post("/api/artists")
def api_add_artist():
  name = (request.form.get("name") or "").strip()
  photo = request.files.get("photo")

  if not name:
    return _json_error("חסר שם זמר.")
  if photo is None or not photo.filename:
    return _json_error("חסרה תמונה.")

  try:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = artist_file_slug(name)
    image_path = INPUT_DIR / f"{slug}.png"
    photo.save(image_path)
    add_artist_to_config(name, f"input/{image_path.name}")
    return jsonify({"ok": True, "name": name})
  except Exception as exc:
    return _json_error(str(exc), 500)


@app.delete("/api/artists")
def api_delete_artist():
  data = request.get_json(silent=True) or {}
  name = (data.get("name") or "").strip()
  if not name:
    return _json_error("חסר שם זמר.")

  try:
    config = load_config(CONFIG_PATH)
    registry = config.get("artists", {})
    artist = find_artist_match(name, registry)
    removed, _image_rel, image_deleted = remove_artist(artist)
    return jsonify({
      "ok": True,
      "removed": removed,
      "image_deleted": image_deleted,
    })
  except ValueError as exc:
    return _json_error(str(exc))
  except Exception as exc:
    return _json_error(str(exc), 500)


def main() -> None:
  OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
  print("ForCuapCut Web — http://127.0.0.1:5000")
  app.run(host="127.0.0.1", port=5000, debug=True)


if __name__ == "__main__":
  main()
