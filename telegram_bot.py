#!/usr/bin/env python3
"""Telegram bot — send song details, receive slide PNG."""

from __future__ import annotations

import json
import logging
import os
import re
import asyncio
import shutil
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from generate import (
  ROOT,
  generate_intro_slide,
  generate_song_slide,
  load_config,
  normalize_difficulty,
  resolve_artist_image,
  save_session_difficulty,
  load_session_difficulty,
  song_slug,
)
from generate_video import generate_song_video_from_data
from whatsapp_client import (
  WhatsAppError,
  is_auto_send_enabled,
  is_whatsapp_enabled,
  send_video_to_whatsapp,
)

load_dotenv(ROOT / ".env")

logging.basicConfig(
  format="%(asctime)s %(levelname)s %(name)s: %(message)s",
  level=logging.INFO,
)
log = logging.getLogger("forcuapcut.bot")

CONFIG_PATH = ROOT / "config.json"
INTROS_DIR = ROOT / "intros"
OUTPUT_DIR = ROOT / "output"
INPUT_DIR = ROOT / "input"

HEBREW_TO_LATIN = {
  "א": "", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "v", "ז": "z",
  "ח": "ch", "ט": "t", "י": "y", "כ": "k", "ך": "k", "ל": "l", "מ": "m",
  "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ע": "e", "פ": "p", "ף": "p",
  "צ": "tz", "ץ": "tz", "ק": "k", "ר": "r", "ש": "sh", "ת": "t", "ו": "u",
}
PENDING_ARTIST_KEY = "pending_artist"
ARTISTS_MANAGE_KEY = "artists_manage"
LAST_SONG_KEY = "last_song"
PENDING_VIDEO_KEY = "pending_video"

HELP_TEXT = (
  "מה אפשר לעשות:\n\n"
  'צור זמר "עדן בן זקן" + תמונה (בהודעה אחת)\n'
  "או: קודם הטקסט, ואז תמונה\n\n"
  "רשימת זמרים\n"
  "← רשימה + מחיקה\n\n"
  "מחק עדן בן זקן\n"
  "← מוחק זמר ותמונה\n\n"
  "פתיחה:\n"
  "צור פתיחה | 3 שירים מוכרים | 7 דקות ללמוד | אקורדים בסיסיים בלבד\n"
  "רמה: קל\n"
  "(רמה: קל / בינוני / קשה — בשורה נפרדת, לא קשור לטקסט)\n\n"
  "סיום:\n"
  "צור סיום | עוד שירים כאלה | כל שבוע | עקבו | כדי לא לפספס\n\n"
  "שיר:\n"
  "דודו אהרון | שם השיר | Em,C,G,D\n"
  "רמה: קשה\n"
  "(רמה אופציונלית — בשורה נפרדת; אם חסר, נלקח מהפתיחה האחרונה)\n\n"
  "סרטון (אחרי שיצרת סלייד):\n"
  "צור סרטון | 0,5,10,15 | 30\n"
  "(זמנים + אורך סרטון בשניות — לא חובה 4 דקות!)\n"
  "או: סוף: 30 בשורה נפרדת\n"
  "ואז שלח קובץ שיר (MP3)\n\n"
  "ווצאפ (אחרי שנוצר סרטון):\n"
  "שלח את קובץ ה-MP4 לבוט — יישלח אוטומטית\n"
  "או: שלח לווצאפ (שולח את ה-MP4 האחרון מ-output/)"
)

WORD_SLUG_OVERRIDES = {
  "עדן": "eden",
  "בן": "ben",
  "בת": "bat",
  "זקן": "zaken",
  "כהן": "cohen",
  "לוי": "levi",
  "אהרון": "aharon",
  "יהודה": "yehuda",
  "אב": "av",
  "אם": "em",
}


def get_token() -> str:
  token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
  if not token:
    raise SystemExit(
      "Missing TELEGRAM_BOT_TOKEN in .env\n"
      "Get a token from @BotFather and paste it into .env"
    )
  return token


def allowed_chat_ids() -> set[int]:
  raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
  if not raw:
    return set()
  return {int(part.strip()) for part in raw.split(",") if part.strip()}


def is_authorized(chat_id: int) -> bool:
  allowed = allowed_chat_ids()
  if not allowed:
    return True
  return chat_id in allowed


async def deny_if_unauthorized(update: Update) -> bool:
  chat = update.effective_chat
  if chat is None or is_authorized(chat.id):
    return False
  await update.effective_message.reply_text(
    "אין הרשאה לצ'אט הזה.\n"
    f"מזהה הצ'אט שלך: {chat.id}"
  )
  return True


def parse_song_request(text: str) -> tuple[str, str, list[str], str | None]:
  """Parse: זמר | שיר | Em,C,G,D  (+ optional רמה: קל on separate line)"""
  cleaned, difficulty = extract_difficulty_from_text(text.strip())
  if cleaned.startswith("צור שיר"):
    cleaned = cleaned[len("צור שיר") :].strip()

  parts = [part.strip() for part in cleaned.split("|")]
  if len(parts) != 3:
    raise ValueError(
      "פורמט לא תקין.\n"
      "כתוב: זמר | שיר | אקורדים\n"
      "דוגמה: דודו אהרון | לילה טוב | Em,C,G,D\n"
      "רמה: קשה"
    )

  artist, song, chords_raw = parts
  if not artist or not song or not chords_raw:
    raise ValueError("חסר זמר, שם שיר או אקורדים.")

  chord_names = [c.strip() for c in chords_raw.split(",") if c.strip()]
  if not (4 <= len(chord_names) <= 8):
    raise ValueError(f"צריך 4–8 אקורדים, קיבלתי {len(chord_names)}.")

  return artist, song, chord_names, difficulty


def is_video_request(text: str) -> bool:
  cleaned = text.strip()
  return cleaned.startswith("צור סרטון") or cleaned.startswith("סרטון")


def parse_video_request(text: str, chord_count: int) -> tuple[list[dict], float | None]:
  """Parse timeline + optional end length. Returns (timeline, end_seconds)."""
  end_seconds: float | None = None
  kept_lines: list[str] = []
  for line in text.splitlines():
    end_match = re.match(r"^סוף\s*:\s*(\d+(?:\.\d+)?)\s*$", line.strip(), re.IGNORECASE)
    if end_match:
      end_seconds = float(end_match.group(1))
      continue
    kept_lines.append(line)
  cleaned = "\n".join(kept_lines).strip()

  matched = False
  for prefix in ("צור סרטון", "סרטון"):
    if cleaned.startswith(prefix):
      cleaned = cleaned[len(prefix) :].strip()
      matched = True
      break
  if not matched:
    raise ValueError("פורמט סרטון לא תקין.")

  if cleaned.startswith("|"):
    cleaned = cleaned[1:].strip()
  if not cleaned:
    raise ValueError(
      "חסרים זמנים.\n"
      "דוגמה:\n"
      f"צור סרטון | {','.join(str(i * 4) for i in range(chord_count))} | 30"
    )

  segments = [part.strip() for part in cleaned.split("|") if part.strip()]
  times_text = segments[0]
  if len(segments) >= 2 and re.fullmatch(r"\d+(?:\.\d+)?", segments[-1]):
    end_seconds = float(segments[-1])
    if len(segments) > 2:
      times_text = "|".join(segments[:-1])
    elif len(segments) == 2:
      times_text = segments[0]

  timeline = _parse_timeline_times(times_text, chord_count)
  if end_seconds is not None and end_seconds <= timeline[-1]["at"]:
    raise ValueError(
      f"אורך הסרטון ({end_seconds}) חייב להיות אחרי האקורד האחרון "
      f"({timeline[-1]['at']} שניות)."
    )
  return timeline, end_seconds


def _parse_timeline_times(times_text: str, chord_count: int) -> list[dict]:
  explicit = re.findall(
    r"(\d+)\s*[:@-]\s*(\d+(?:\.\d+)?)",
    times_text,
  )
  if explicit:
    timeline: list[dict] = []
    for chord_raw, at_raw in explicit:
      chord = int(chord_raw)
      at = float(at_raw)
      if not 1 <= chord <= chord_count:
        raise ValueError(f"מספר אקורד חייב 1-{chord_count}, קיבלתי {chord}")
      timeline.append({"chord": chord, "at": at})
    timeline.sort(key=lambda item: item["at"])
    if timeline[0]["at"] != 0:
      raise ValueError("הזמן הראשון חייב להיות 0.")
    return timeline

  parts = [part.strip() for part in re.split(r"[,;\s]+", times_text) if part.strip()]
  try:
    times = [float(part) for part in parts]
  except ValueError as exc:
    raise ValueError(
      "זמנים לא תקינים.\n"
      "דוגמה:\n"
      "צור סרטון | 0,5,10,15 | 30"
    ) from exc

  if len(times) != chord_count:
    raise ValueError(
      f"צריך {chord_count} זמנים (אחד לכל אקורד), קיבלתי {len(times)}.\n"
      f"דוגמה: צור סרטון | {','.join(str(i * 4) for i in range(chord_count))} | 30"
    )
  if times[0] != 0:
    raise ValueError("הזמן הראשון חייב להיות 0.")
  for i in range(1, len(times)):
    if times[i] <= times[i - 1]:
      raise ValueError("הזמנים חייבים לעלות — כל זמן גדול מהקודם.")

  return [{"chord": index + 1, "at": at} for index, at in enumerate(times)]


def parse_video_timeline(text: str, chord_count: int) -> list[dict]:
  timeline, _end = parse_video_request(text, chord_count)
  return timeline


def build_video_job(
  *,
  artist: str,
  song: str,
  chord_names: list[str],
  timeline: list[dict],
  difficulty: str | None,
  end_seconds: float | None = None,
) -> dict:
  job = {
    "artist": artist,
    "song": song,
    "chords": chord_names,
    "timeline": timeline,
    "difficulty": difficulty or "קל",
  }
  if end_seconds is not None:
    job["end"] = end_seconds
  return job


def parse_difficulty_line(line: str) -> str | None:
  match = re.match(r"^רמה\s*:\s*(.+)$", line.strip(), re.IGNORECASE)
  if not match:
    return None
  raw = match.group(1).strip()
  level = normalize_difficulty(raw)
  if not level:
    raise ValueError(f"רמה לא תקינה: {raw}\nהשתמש: קל / בינוני / קשה")
  return level


def extract_difficulty_from_text(text: str) -> tuple[str, str | None]:
  """Remove 'רמה: קל' lines; return cleaned text and difficulty."""
  difficulty = None
  kept_lines: list[str] = []
  for line in text.splitlines():
    level = parse_difficulty_line(line)
    if level:
      difficulty = level
      continue
    kept_lines.append(line)
  return "\n".join(kept_lines).strip(), difficulty


def parse_text_slide_request(text: str, headers: tuple[str, ...]) -> list[str] | None:
  """Parse: צור פתיחה/סיום | שורה1 | שורה2 | שורה3 | שורה4"""
  cleaned = text.strip()
  if "|" not in cleaned:
    return None

  parts = [part.strip() for part in cleaned.split("|") if part.strip()]
  if not parts:
    return None

  header = parts[0]
  create_header = headers[0]
  if header in headers or header.startswith(create_header):
    lines = parts[1:]
  else:
    return None

  label = headers[-1]
  if len(lines) < 3:
    raise ValueError(
      f"ל{label} צריך לפחות 3 שורות.\n"
      "דוגמה:\n"
      f"{create_header} | שורה 1 | שורה 2 | שורה 3"
    )
  if len(lines) > 4:
    raise ValueError(f"ל{label} יש מקסימום 4 שורות.")

  return lines


def parse_intro_request(text: str) -> tuple[list[str], str | None] | None:
  cleaned, difficulty = extract_difficulty_from_text(text)
  lines = parse_text_slide_request(cleaned, ("צור פתיחה", "פתיחה"))
  if lines is None:
    return None
  return lines, difficulty


def parse_outro_request(text: str) -> list[str] | None:
  return parse_text_slide_request(text, ("צור סיום", "סיום"))


def save_text_slide_yaml(
  lines: list[str],
  *,
  yaml_name: str,
  output_name: str,
  difficulty: str | None = None,
) -> Path:
  INTROS_DIR.mkdir(parents=True, exist_ok=True)
  yaml_path = INTROS_DIR / yaml_name
  keys = ("line1", "line2", "line3", "line4")
  rows = [f'{keys[i]}: "{lines[i]}"' for i in range(len(lines))]
  if difficulty:
    rows.append(f'difficulty: "{difficulty}"')
  rows.append(f'output: "output/{output_name}"')
  yaml_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
  return yaml_path


def save_intro_yaml(lines: list[str], *, difficulty: str | None = None) -> Path:
  return save_text_slide_yaml(
    lines,
    yaml_name="intro.yaml",
    output_name="intro.png",
    difficulty=difficulty,
  )


def save_outro_yaml(lines: list[str]) -> Path:
  return save_text_slide_yaml(lines, yaml_name="outro.yaml", output_name="outro.png")


def parse_create_artist(text: str) -> str | None:
  """Parse: צור זמר "יהודה לוי" """
  match = re.match(r"^צור\s+זמר\s+(.+)$", text.strip())
  if not match:
    return None
  name = match.group(1).strip()
  if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'«»":
    name = name[1:-1].strip()
  return name or None


def is_list_artists_request(text: str) -> bool:
  return text.strip() in ("רשימת זמרים", "זמרים", "הצג זמרים")


def is_help_request(text: str) -> bool:
  return text.strip().lower() in ("עזרה", "help", "?")


def resolve_artist_or_hint_swap(config: dict, artist: str, song: str) -> Path:
  registry = config.get("artists", {})
  if artist in registry:
    return resolve_artist_image(config, artist)
  if song in registry:
    raise ValueError(
      "נראה שהסדר הפוך — קודם זמר, אחר כך שיר.\n\n"
      f"נסה:\n{song} | {artist} | Em,C,G,D"
    )
  known = ", ".join(sorted(registry)) if registry else "(ריק)"
  raise ValueError(
    f"הזמר '{artist}' לא ברשימה.\n"
    'צור אותו קודם: צור זמר "שם הזמר"\n\n'
    f"זמרים קיימים: {known}"
  )


def transliterate_word(word: str) -> str:
  if word in WORD_SLUG_OVERRIDES:
    return WORD_SLUG_OVERRIDES[word]

  parts: list[str] = []
  for index, char in enumerate(word):
    if char in HEBREW_TO_LATIN:
      value = HEBREW_TO_LATIN[char]
      if char == "א" and index == 0:
        value = "a"
      parts.append(value)
    elif char.isalnum():
      parts.append(char.lower())

  slug = "".join(parts)
  if word.endswith("ן") and slug.endswith("n") and len(slug) > 1:
    slug = slug[:-1] + "en"
  slug = re.sub(r"_+", "_", slug).strip("_")
  return slug or "word"


def artist_file_slug(name: str) -> str:
  words = [part.strip() for part in name.strip().split() if part.strip()]
  if not words:
    return "artist"
  slug = "_".join(transliterate_word(word) for word in words)
  slug = re.sub(r"_+", "_", slug).strip("_")
  return slug or "artist"


def save_config(config: dict) -> None:
  with CONFIG_PATH.open("w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)
    f.write("\n")


def add_artist_to_config(artist: str, relative_path: str) -> None:
  with CONFIG_PATH.open(encoding="utf-8") as f:
    config = json.load(f)
  artists = config.setdefault("artists", {})
  artists[artist] = relative_path.replace("\\", "/")
  save_config(config)


def parse_delete_name(text: str) -> str | None:
  cleaned = text.strip()
  for prefix in ("מחק את ", "מחק "):
    if cleaned.startswith(prefix):
      name = cleaned[len(prefix) :].strip()
      if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'«»":
        name = name[1:-1].strip()
      return name or None
  if cleaned.startswith("מחק"):
    name = cleaned[3:].strip()
    if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'«»":
      name = name[1:-1].strip()
    return name or None
  return None


def find_artist_match(query: str, registry: dict[str, str]) -> str:
  name = query.strip()
  if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'«»":
    name = name[1:-1].strip()
  if not name:
    raise ValueError("שלח שם זמר למחיקה.")
  if name in registry:
    return name

  lowered = name.casefold()
  exact_fold = [artist for artist in registry if artist.casefold() == lowered]
  if len(exact_fold) == 1:
    return exact_fold[0]
  if len(exact_fold) > 1:
    raise ValueError(f"כמה התאמות: {', '.join(exact_fold)}")

  partial = [artist for artist in registry if name in artist or artist in name]
  if len(partial) == 1:
    return partial[0]
  if len(partial) > 1:
    raise ValueError(f"כמה התאמות: {', '.join(sorted(partial))}\nפרט את השם המלא.")

  known = ", ".join(sorted(registry)) if registry else "(ריק)"
  raise ValueError(f"הזמר '{name}' לא נמצא.\n\nזמרים קיימים: {known}")


def remove_artist(name: str) -> tuple[str, str | None, bool]:
  with CONFIG_PATH.open(encoding="utf-8") as f:
    config = json.load(f)
  registry: dict[str, str] = config.get("artists", {})
  if name not in registry:
    raise KeyError(name)

  image_rel = registry.pop(name)
  save_config(config)

  image_deleted = False
  if image_rel:
    image_path = ROOT / image_rel
    if image_path.is_file():
      image_path.unlink()
      image_deleted = True

  return name, image_rel, image_deleted


async def save_artist_photo(
  update: Update,
  context: ContextTypes.DEFAULT_TYPE,
  name: str,
  image_bytes: bytes,
) -> None:
  slug = artist_file_slug(name)
  INPUT_DIR.mkdir(parents=True, exist_ok=True)
  image_path = INPUT_DIR / f"{slug}.png"
  image_path.write_bytes(image_bytes)
  relative = f"input/{image_path.name}"
  add_artist_to_config(name, relative)
  context.user_data.pop(PENDING_ARTIST_KEY, None)
  await update.message.reply_text(
    f"נשמר: {name}\n"
    f"קובץ: input/{slug}.png\n\n"
    f"ליצירת שיר:\n{name} | שם השיר | Em,C,G,D"
  )
  log.info("Registered artist %s -> %s", name, image_path)


def get_desktop_dir() -> Path:
  """User Desktop — supports OneDrive-redirected Desktop on Windows."""
  desktop = Path.home() / "Desktop"
  if desktop.is_dir():
    return desktop

  onedrive = os.environ.get("OneDrive", "").strip()
  if onedrive:
    candidate = Path(onedrive) / "Desktop"
    if candidate.is_dir():
      return candidate

  return desktop


def safe_filename(name: str, ext: str = ".png") -> str:
  sanitized = re.sub(r'[<>:"/\\|?*]', "_", name).strip()
  return (sanitized or "song") + ext


def find_latest_video() -> Path | None:
  if not OUTPUT_DIR.is_dir():
    return None
  videos = sorted(
    OUTPUT_DIR.glob("*.mp4"),
    key=lambda path: path.stat().st_mtime,
    reverse=True,
  )
  return videos[0] if videos else None


def is_send_whatsapp_request(text: str) -> bool:
  cleaned = text.strip().lower()
  return cleaned in (
    "שלח לווצאפ",
    "שלח ווצאפ",
    "שלח סרטון לווצאפ",
    "whatsapp",
  )


async def deliver_video_to_whatsapp(
  update: Update,
  video_path: Path,
  *,
  caption: str = "",
) -> None:
  if not is_whatsapp_enabled():
    await update.message.reply_text(
      "ווצאפ לא מוגדר.\n"
      "הוסף ל-.env:\n"
      "WHATSAPP_ACCESS_TOKEN\n"
      "WHATSAPP_PHONE_NUMBER_ID\n"
      "WHATSAPP_RECIPIENT"
    )
    return

  status = await update.message.reply_text("שולח לווצאפ...")
  try:
    note = await asyncio.to_thread(
      send_video_to_whatsapp,
      video_path,
      caption=caption,
    )
    await status.edit_text(f"{note}\n{video_path.name}")
  except (WhatsAppError, OSError) as exc:
    log.exception("WhatsApp send failed")
    await status.edit_text(f"שגיאה בשליחה לווצאפ:\n{exc}")


async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return

  caption = (update.message.caption or "").strip()

  video = update.message.video
  if video is None:
    await update.message.reply_text("לא התקבל קובץ וידאו.")
    return

  status = await update.message.reply_text("מוריד את הסרטון...")
  try:
    tg_file = await context.bot.get_file(video.file_id)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(video.file_name or "video", ".mp4")
    if not filename.lower().endswith(".mp4"):
      filename = safe_filename("video", ".mp4")
    video_path = OUTPUT_DIR / filename
    video_bytes = bytes(await tg_file.download_as_bytearray())
    video_path.write_bytes(video_bytes)
    await status.delete()

    await update.message.reply_text(f"נשמר: {video_path.name}")

    if is_auto_send_enabled():
      await deliver_video_to_whatsapp(update, video_path, caption=caption)
    elif not is_whatsapp_enabled():
      pass
    else:
      await update.message.reply_text(
        "לשליחה לווצאפ — הוסף WHATSAPP_AUTO_SEND=true ל-.env\n"
        "או שלח: שלח לווצאפ"
      )
  except Exception as exc:
    log.exception("Failed to handle video upload")
    await status.edit_text(f"שגיאה:\n{exc}")


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return

  document = update.message.document
  if document is None:
    return

  name = (document.file_name or "").lower()
  mime = (document.mime_type or "").lower()
  caption = (update.message.caption or "").strip()

  is_audio = (
    mime.startswith("audio/")
    or name.endswith((".mp3", ".m4a", ".wav", ".ogg", ".aac"))
  )
  if is_audio:
    if caption and is_video_request(caption):
      last_song = context.user_data.get(LAST_SONG_KEY)
      if not last_song:
        await update.message.reply_text(
          "קודם צור סלייד שיר.\n\n"
          "דוגמה:\n"
          "אייל גולן | בעירי | AM,DM,F,E,G,C"
        )
        return
      try:
        timeline, end_seconds = parse_video_request(caption, len(last_song["chords"]))
        context.user_data[PENDING_VIDEO_KEY] = build_video_job(
          artist=last_song["artist"],
          song=last_song["song"],
          chord_names=last_song["chords"],
          timeline=timeline,
          difficulty=last_song.get("difficulty"),
          end_seconds=end_seconds,
        )
      except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    status = await update.message.reply_text("מוריד את השיר...")
    try:
      tg_file = await context.bot.get_file(document.file_id)
      filename = document.file_name or "song.mp3"
      audio_bytes = bytes(await tg_file.download_as_bytearray())
      await status.delete()
      await generate_video_and_reply(
        update,
        context,
        audio_bytes,
        filename=filename,
      )
    except Exception as exc:
      log.exception("Failed to handle audio document")
      await status.edit_text(f"שגיאה:\n{exc}")
    return

  if not name.endswith(".mp4"):
    return

  status = await update.message.reply_text("מוריד MP4...")
  try:
    tg_file = await context.bot.get_file(document.file_id)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    video_path = OUTPUT_DIR / Path(document.file_name).name
    video_path.write_bytes(bytes(await tg_file.download_as_bytearray()))
    await status.delete()
    await update.message.reply_text(f"נשמר: {video_path.name}")

    caption = (update.message.caption or "").strip()
    if is_auto_send_enabled() or is_send_whatsapp_request(caption):
      await deliver_video_to_whatsapp(update, video_path, caption="")
  except Exception as exc:
    log.exception("Failed to handle MP4 document")
    await status.edit_text(f"שגיאה:\n{exc}")


async def reply_help(update: Update) -> None:
  await update.message.reply_text(HELP_TEXT)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return
  await update.message.reply_text(
    "שלום! אני יוצר סליידים לטיקטוק.\n\n" + HELP_TEXT
  )


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return

  caption = (update.message.caption or "").strip()
  name = parse_create_artist(caption) if caption else None
  if not name:
    name = context.user_data.get(PENDING_ARTIST_KEY)

  if not name:
    await update.message.reply_text(
      'שלח תמונה עם כיתוב:\nצור זמר "עדן בן זקן"\n\n'
      'או קודם: צור זמר "עדן בן זקן" ואז תמונה.'
    )
    return

  status = await update.message.reply_text(f"שומר את {name}...")
  try:
    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await tg_file.download_as_bytearray())
    await status.delete()
    await save_artist_photo(update, context, name, image_bytes)
  except Exception as exc:
    log.exception("Failed to save artist photo")
    await status.edit_text(f"שגיאה בשמירה:\n{exc}")


async def show_artists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return
  config = load_config(CONFIG_PATH)
  registry = config.get("artists", {})
  if not registry:
    context.user_data.pop(ARTISTS_MANAGE_KEY, None)
    await update.message.reply_text("אין זמרים ברשימה.")
    return

  context.user_data[ARTISTS_MANAGE_KEY] = True
  lines = ["זמרים:"]
  lines.extend(f"• {name}" for name in sorted(registry))
  lines.append("")
  lines.append("למחיקה — שלח: מחק שם הזמר")
  await update.message.reply_text("\n".join(lines))


async def try_delete_artist(
  update: Update,
  context: ContextTypes.DEFAULT_TYPE,
  query: str,
) -> None:
  config = load_config(CONFIG_PATH)
  registry = config.get("artists", {})
  if not registry:
    context.user_data.pop(ARTISTS_MANAGE_KEY, None)
    await update.message.reply_text("אין זמרים למחוק.")
    return

  artist = find_artist_match(query, registry)
  removed, image_rel, image_deleted = remove_artist(artist)

  image_note = ""
  if image_deleted:
    image_note = f"\nנמחקה גם התמונה: {image_rel}"
  elif image_rel:
    image_note = f"\nלא נמצאה תמונה: {image_rel}"

  await update.message.reply_text(f"נמחק: {removed}{image_note}")
  log.info("Removed artist %s (image_deleted=%s)", removed, image_deleted)

  registry = load_config(CONFIG_PATH).get("artists", {})
  if registry:
    context.user_data[ARTISTS_MANAGE_KEY] = True
    remaining = ["נשארו:"]
    remaining.extend(f"• {name}" for name in sorted(registry))
    remaining.append("\nלמחיקה נוספת — שלח: מחק שם הזמר")
    await update.message.reply_text("\n".join(remaining))
  else:
    context.user_data.pop(ARTISTS_MANAGE_KEY, None)
    await update.message.reply_text("הרשימה ריקה.")


async def generate_text_slide_and_reply(
  update: Update,
  text: str,
  *,
  parse_request,
  save_yaml,
  output_name: str,
  status_label: str,
  caption: str,
  log_label: str,
  slide_style: str = "intro",
) -> None:
  if await deny_if_unauthorized(update):
    return

  status = await update.message.reply_text(f"מייצר {status_label}...")

  try:
    lines = parse_request(text)
    if not lines:
      raise ValueError(f"פורמט {status_label} לא תקין.")

    config = load_config(CONFIG_PATH)
    output = OUTPUT_DIR / output_name
    save_yaml(lines)

    result = generate_intro_slide(
      lines=lines,
      background=None,
      output=output,
      config=config,
      slide_style=slide_style,
    )

    desktop_dir = get_desktop_dir()
    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = desktop_dir / output_name
    shutil.copy2(result, desktop_path)

    await status.delete()
    await update.message.reply_photo(
      photo=BytesIO(result.read_bytes()),
      caption=f"{caption}\n\nנשמר בשולחן העבודה:\n{desktop_path.name}",
    )
    log.info("Created %s %s for chat %s", log_label, result, update.effective_chat.id)
  except Exception as exc:
    log.exception("%s generation failed", log_label.capitalize())
    await status.edit_text(f"שגיאה:\n{exc}")


async def generate_intro_and_reply(update: Update, text: str) -> None:
  if await deny_if_unauthorized(update):
    return

  status = await update.message.reply_text("מייצר פתיחה...")

  try:
    parsed = parse_intro_request(text)
    if not parsed:
      raise ValueError("פורמט פתיחה לא תקין.")

    lines, difficulty = parsed
    if not difficulty:
      raise ValueError(
        "חסרה רמת קושי.\n"
        "הוסף בשורה נפרדת:\n"
        "רמה: קל\n"
        f"או: רמה: בינוני / רמה: קשה"
      )

    config = load_config(CONFIG_PATH)
    output = OUTPUT_DIR / "intro.png"
    save_intro_yaml(lines, difficulty=difficulty)
    save_session_difficulty(difficulty)

    result = generate_intro_slide(
      lines=lines,
      background=None,
      output=output,
      config=config,
      slide_style="intro",
      difficulty=difficulty,
    )

    desktop_dir = get_desktop_dir()
    desktop_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = desktop_dir / "intro.png"
    shutil.copy2(result, desktop_path)

    await status.delete()
    await update.message.reply_photo(
      photo=BytesIO(result.read_bytes()),
      caption=(
        f"פתיחה — רמה: {difficulty}\n\n"
        f"נשמר בשולחן העבודה:\n{desktop_path.name}"
      ),
    )
    log.info("Created intro %s (difficulty=%s) for chat %s", result, difficulty, update.effective_chat.id)
  except Exception as exc:
    log.exception("Intro generation failed")
    await status.edit_text(f"שגיאה:\n{exc}")


async def generate_outro_and_reply(update: Update, text: str) -> None:
  await generate_text_slide_and_reply(
    update,
    text,
    parse_request=parse_outro_request,
    save_yaml=save_outro_yaml,
    output_name="outro.png",
    status_label="סיום",
    caption="סיום",
    log_label="outro",
    slide_style="outro",
  )


async def generate_and_reply(
  update: Update,
  context: ContextTypes.DEFAULT_TYPE,
  text: str,
) -> None:
  if await deny_if_unauthorized(update):
    return

  status = await update.message.reply_text("מייצר סלייד...")

  try:
    artist, song, chord_names, song_difficulty = parse_song_request(text)
    config = load_config(CONFIG_PATH)
    artist_image = resolve_artist_or_hint_swap(config, artist, song)
    difficulty = song_difficulty or load_session_difficulty()
    desktop_dir = get_desktop_dir()
    desktop_dir.mkdir(parents=True, exist_ok=True)
    output = desktop_dir / safe_filename(song)

    result = generate_song_slide(
      artist=artist,
      song=song,
      chord_names=chord_names,
      artist_image=artist_image,
      background=None,
      output=output,
      config=config,
      difficulty=difficulty,
    )

    context.user_data[LAST_SONG_KEY] = {
      "artist": artist,
      "song": song,
      "chords": chord_names,
      "difficulty": difficulty,
    }
    context.user_data.pop(PENDING_VIDEO_KEY, None)

    await status.delete()
    level_note = f"\nרמה: {difficulty}" if difficulty else ""
    await update.message.reply_photo(
      photo=BytesIO(result.read_bytes()),
      caption=(
        f"{artist} — {song}{level_note}\n\n"
        f"נשמר בשולחן העבודה:\n{result.name}\n\n"
        "לסרטון — שלח:\n"
        f"צור סרטון | {','.join(str(i * 4) for i in range(len(chord_names)))}\n"
        "ואז קובץ MP3"
      ),
    )
    log.info("Created %s for chat %s", result, update.effective_chat.id)
  except Exception as exc:
    log.exception("Generation failed")
    await status.edit_text(f"שגיאה:\n{exc}")


async def start_video_request(
  update: Update,
  context: ContextTypes.DEFAULT_TYPE,
  text: str,
) -> None:
  if await deny_if_unauthorized(update):
    return

  last_song = context.user_data.get(LAST_SONG_KEY)
  if not last_song:
    await update.message.reply_text(
      "קודם צור סלייד שיר.\n\n"
      "דוגמה:\n"
      "אייל גולן | בעירי | AM,DM,F,E,G,C"
    )
    return

  try:
    timeline, end_seconds = parse_video_request(text, len(last_song["chords"]))
    context.user_data[PENDING_VIDEO_KEY] = build_video_job(
      artist=last_song["artist"],
      song=last_song["song"],
      chord_names=last_song["chords"],
      timeline=timeline,
      difficulty=last_song.get("difficulty"),
      end_seconds=end_seconds,
    )
    end_note = f"\nאורך סרטון: {end_seconds} שניות" if end_seconds else ""
    await update.message.reply_text(
      f"מעולה — {last_song['song']}\n"
      f"{len(timeline)} אקורדים מסונכרנים.{end_note}\n\n"
      "שלח עכשיו קובץ שיר (MP3).\n"
      "אפשר גם אודיו עם כיתוב שמכיל את הזמנים."
    )
  except ValueError as exc:
    await update.message.reply_text(str(exc))


async def generate_video_and_reply(
  update: Update,
  context: ContextTypes.DEFAULT_TYPE,
  audio_bytes: bytes,
  *,
  filename: str,
) -> None:
  pending = context.user_data.get(PENDING_VIDEO_KEY)
  if not pending:
    await update.message.reply_text(
      "קודם שלח זמנים לסרטון.\n\n"
      "דוגמה:\n"
      "צור סרטון | 0,5,10,15,20,25"
    )
    return

  status = await update.message.reply_text("מייצר סרטון... (זה לוקח כדקה)")
  try:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    slug = song_slug(pending["song"])
    ext = Path(filename).suffix.lower() or ".mp3"
    if ext not in {".mp3", ".m4a", ".wav", ".ogg", ".aac"}:
      ext = ".mp3"
    audio_path = INPUT_DIR / f"{slug}{ext}"
    audio_path.write_bytes(audio_bytes)

    output_name = safe_filename(pending["song"], ".mp4")
    output_path = OUTPUT_DIR / output_name
    desktop_path = get_desktop_dir() / output_name

    config = load_config(CONFIG_PATH)
    video_data = {
      **pending,
      "audio": str(audio_path.relative_to(ROOT)).replace("\\", "/"),
      "output": str(output_path.relative_to(ROOT)).replace("\\", "/"),
    }

    result = await asyncio.to_thread(
      generate_song_video_from_data,
      video_data,
      config=config,
      config_path=CONFIG_PATH,
    )

    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result, desktop_path)
    context.user_data.pop(PENDING_VIDEO_KEY, None)

    await status.delete()
    await update.message.reply_video(
      video=BytesIO(result.read_bytes()),
      caption=(
        f"{pending['artist']} — {pending['song']}\n\n"
        f"נשמר:\n{desktop_path.name}"
      ),
      supports_streaming=True,
    )
    log.info("Created video %s for chat %s", result, update.effective_chat.id)

    if is_auto_send_enabled():
      await deliver_video_to_whatsapp(
        update,
        result,
        caption=f"{pending['artist']} — {pending['song']}",
      )
    elif is_whatsapp_enabled():
      await update.message.reply_text(
        "לשליחה לווצאפ — הוסף WHATSAPP_AUTO_SEND=true ל-.env\n"
        "או שלח: שלח לווצאפ"
      )
  except Exception as exc:
    log.exception("Video generation failed")
    await status.edit_text(f"שגיאה ביצירת סרטון:\n{exc}")


async def on_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return

  caption = (update.message.caption or "").strip()
  if caption and is_video_request(caption):
    last_song = context.user_data.get(LAST_SONG_KEY)
    if not last_song:
      await update.message.reply_text(
        "קודם צור סלייד שיר.\n\n"
        "דוגמה:\n"
        "אייל גולן | בעירי | AM,DM,F,E,G,C"
      )
      return
    try:
      timeline, end_seconds = parse_video_request(caption, len(last_song["chords"]))
      context.user_data[PENDING_VIDEO_KEY] = build_video_job(
        artist=last_song["artist"],
        song=last_song["song"],
        chord_names=last_song["chords"],
        timeline=timeline,
        difficulty=last_song.get("difficulty"),
        end_seconds=end_seconds,
      )
    except ValueError as exc:
      await update.message.reply_text(str(exc))
      return

  audio = update.message.audio
  if audio is None:
    return

  status = await update.message.reply_text("מוריד את השיר...")
  try:
    tg_file = await context.bot.get_file(audio.file_id)
    filename = audio.file_name or f"{audio.file_unique_id}.mp3"
    audio_bytes = bytes(await tg_file.download_as_bytearray())
    await status.delete()
    await generate_video_and_reply(update, context, audio_bytes, filename=filename)
  except Exception as exc:
    log.exception("Failed to handle audio upload")
    await status.edit_text(f"שגיאה:\n{exc}")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return

  text = (update.message.text or "").strip()
  if not text:
    return

  if is_help_request(text):
    await reply_help(update)
    return

  if is_send_whatsapp_request(text):
    latest = find_latest_video()
    if latest is None:
      await update.message.reply_text(
        "לא נמצא MP4 בתיקיית output/.\n"
        "שלח קובץ וידאו לבוט, או שמור סרטון ב-output/"
      )
      return
    await deliver_video_to_whatsapp(update, latest)
    return

  if is_video_request(text):
    await start_video_request(update, context, text)
    return

  if is_list_artists_request(text):
    await show_artists(update, context)
    return

  create_name = parse_create_artist(text)
  if create_name:
    context.user_data[PENDING_ARTIST_KEY] = create_name
    context.user_data.pop(ARTISTS_MANAGE_KEY, None)
    await update.message.reply_text(f"מעולה. שלח עכשיו תמונה של {create_name}")
    return

  if "|" in text:
    context.user_data.pop(ARTISTS_MANAGE_KEY, None)
    header = text.split("|", 1)[0].strip()
    if header in ("צור פתיחה", "פתיחה") or header.startswith("צור פתיחה"):
      await generate_intro_and_reply(update, text)
    elif header in ("צור סיום", "סיום") or header.startswith("צור סיום"):
      await generate_outro_and_reply(update, text)
    else:
      await generate_and_reply(update, context, text)
    return

  delete_name = parse_delete_name(text)
  in_manage = context.user_data.get(ARTISTS_MANAGE_KEY)
  if delete_name or in_manage:
    try:
      await try_delete_artist(update, context, delete_name or text)
    except ValueError as exc:
      await update.message.reply_text(str(exc))
    except Exception as exc:
      log.exception("Failed to delete artist")
      await update.message.reply_text(f"שגיאה במחיקה:\n{exc}")


def main() -> None:
  token = get_token()
  app = Application.builder().token(token).build()

  app.add_handler(CommandHandler("start", cmd_start))
  app.add_handler(MessageHandler(filters.PHOTO, on_photo))
  app.add_handler(MessageHandler(filters.AUDIO, on_audio))
  app.add_handler(MessageHandler(filters.VIDEO, on_video))
  app.add_handler(
    MessageHandler(
      filters.Document.VIDEO
      | filters.Document.MimeType("video/mp4")
      | filters.Document.MimeType("audio/mpeg")
      | filters.Document.MimeType("audio/mp4")
      | filters.Document.MimeType("audio/wav")
      | filters.Document.MimeType("audio/ogg"),
      on_document,
    )
  )
  app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

  log.info("Bot starting...")
  app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
  main()
