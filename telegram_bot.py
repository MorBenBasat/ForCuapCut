#!/usr/bin/env python3
"""Telegram bot — send song details, receive slide PNG."""

from __future__ import annotations

import json
import logging
import os
import re
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from generate import (
  ROOT,
  generate_song_slide,
  load_config,
  resolve_artist_image,
  song_slug,
)

load_dotenv(ROOT / ".env")

logging.basicConfig(
  format="%(asctime)s %(levelname)s %(name)s: %(message)s",
  level=logging.INFO,
)
log = logging.getLogger("forcuapcut.bot")

CONFIG_PATH = ROOT / "config.json"
SONGS_DIR = ROOT / "songs"
OUTPUT_DIR = ROOT / "output"
INPUT_DIR = ROOT / "input"

HEBREW_TO_LATIN = {
  "א": "", "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ו": "v", "ז": "z",
  "ח": "ch", "ט": "t", "י": "y", "כ": "k", "ך": "k", "ל": "l", "מ": "m",
  "ם": "m", "נ": "n", "ן": "n", "ס": "s", "ע": "", "פ": "p", "ף": "p",
  "צ": "tz", "ץ": "tz", "ק": "k", "ר": "r", "ש": "sh", "ת": "t",
}
PENDING_ARTIST_KEY = "pending_artist"


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
    f"ה-chat ID שלך: `{chat.id}`\n"
    "הוסף אותו ל-TELEGRAM_ALLOWED_CHAT_IDS ב-.env",
    parse_mode="Markdown",
  )
  return True


def parse_song_request(text: str) -> tuple[str, str, list[str]]:
  """Parse: זמר | שיר | Em,C,G,D"""
  cleaned = text.strip()
  if cleaned.lower().startswith("/add"):
    cleaned = cleaned[4:].strip()

  parts = [part.strip() for part in cleaned.split("|")]
  if len(parts) != 3:
    raise ValueError(
      "פורמט לא תקין.\n"
      "כתוב: זמר | שיר | אקורדים\n"
      'דוגמה: דודו אהרון | לילה טוב | Em,C,G,D'
    )

  artist, song, chords_raw = parts
  if not artist or not song or not chords_raw:
    raise ValueError("חסר זמר, שם שיר או אקורדים.")

  chord_names = [c.strip() for c in chords_raw.split(",") if c.strip()]
  if not (4 <= len(chord_names) <= 6):
    raise ValueError(f"צריך 4–6 אקורדים, קיבלתי {len(chord_names)}.")

  return artist, song, chord_names


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
    "שלח תמונה עם כיתוב שם הזמר, או /artist שם הזמר ואז תמונה.\n\n"
    f"זמרים קיימים: {known}"
  )


def artist_file_slug(name: str) -> str:
  parts: list[str] = []
  for char in name.strip().lower():
    if char in HEBREW_TO_LATIN:
      parts.append(HEBREW_TO_LATIN[char])
    elif char.isalnum():
      parts.append(char)
    elif char in (" ", "-", "_"):
      parts.append("_")
  slug = re.sub(r"_+", "_", "".join(parts)).strip("_")
  return slug or "artist"


def add_artist_to_config(artist: str, relative_path: str) -> None:
  with CONFIG_PATH.open(encoding="utf-8") as f:
    config = json.load(f)
  artists = config.setdefault("artists", {})
  artists[artist] = relative_path.replace("\\", "/")
  with CONFIG_PATH.open("w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)
    f.write("\n")


def parse_artist_name_from_caption(caption: str) -> str | None:
  text = caption.strip()
  if not text or "|" in text:
    return None
  if text.lower().startswith("/artist"):
    name = text[7:].strip()
    return name or None
  if text.startswith("/"):
    return None
  return text


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
    f"תמונה: {relative}\n\n"
    f"עכשיו שלח:\n{name} | שם השיר | Em,C,G,D"
  )
  log.info("Registered artist %s -> %s", name, image_path)


def save_song_yaml(slug: str, artist: str, song: str, chord_names: list[str], output: Path) -> Path:
  SONGS_DIR.mkdir(parents=True, exist_ok=True)
  yaml_path = SONGS_DIR / f"{slug}.yaml"
  lines = [
    f'artist: "{artist}"',
    f'song: "{song}"',
    "chords:",
    *[f"  - {name}" for name in chord_names],
    f'output: "{output.as_posix()}"',
    "",
  ]
  yaml_path.write_text("\n".join(lines), encoding="utf-8")
  return yaml_path


def make_slug(song: str) -> str:
  slug = song_slug(song)
  slug = re.sub(r"[^\w\u0590-\u05FF-]", "", slug, flags=re.UNICODE)
  return slug or "song"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return
  chat_id = update.effective_chat.id
  await update.message.reply_text(
    "שלום! אני יוצר סליידים לטיקטוק.\n\n"
    "זמר חדש: שלח תמונה עם כיתוב שם הזמר\n"
    "או: /artist עידן עמדי ואז תמונה\n\n"
    "שיר: זמר | שיר | Em,C,G,D\n"
    "דוגמה: דודו אהרון | לילה טוב | Em,C,G,D\n\n"
    f"ה-chat ID שלך: `{chat_id}`\n"
    "שמור אותו ב-.env אם אתה מגביל גישה.",
    parse_mode="Markdown",
  )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return
  await update.message.reply_text(
    "פקודות:\n"
    "/start — התחלה\n"
    "/help — עזרה\n"
    "/artists — רשימת זמרים\n"
    "/artist שם — הוספת זמר (ואז תמונה)\n"
    "/whoami — ה-chat ID שלך\n"
    "/add זמר | שיר | אקורדים\n\n"
    "זמר חדש (הכי קל): תמונה + כיתוב \"עידן עמדי\"\n\n"
    "שיר: דודו אהרון | לילה טוב | Em,C,G,D"
  )


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  chat_id = update.effective_chat.id
  await update.message.reply_text(f"ה-chat ID שלך: `{chat_id}`", parse_mode="Markdown")


async def cmd_artist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return
  args = context.args or []
  if not args:
    await update.message.reply_text(
      "הוספת זמר חדש:\n\n"
      "אופן 1 (הכי קל): שלח תמונה עם כיתוב שם הזמר\n"
      "אופן 2: /artist עידן עמדי ואז שלח תמונה"
    )
    return
  name = " ".join(args).strip()
  context.user_data[PENDING_ARTIST_KEY] = name
  await update.message.reply_text(f"מעולה. שלח עכשיו תמונה של {name}")


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return

  caption = update.message.caption or ""
  name = parse_artist_name_from_caption(caption)
  if not name:
    name = context.user_data.get(PENDING_ARTIST_KEY)

  if not name:
    await update.message.reply_text(
      "שלח תמונה עם כיתוב שם הזמר.\n"
      "דוגמה: כיתוב על התמונה — עידן עמדי\n\n"
      "או: /artist עידן עמדי ואז תמונה"
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


async def cmd_artists(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  if await deny_if_unauthorized(update):
    return
  config = load_config(CONFIG_PATH)
  registry = config.get("artists", {})
  if not registry:
    await update.message.reply_text("אין זמרים ב-config.json")
    return
  lines = ["זמרים זמינים:"]
  lines.extend(f"• {name}" for name in sorted(registry))
  await update.message.reply_text("\n".join(lines))


async def generate_and_reply(update: Update, text: str) -> None:
  if await deny_if_unauthorized(update):
    return

  status = await update.message.reply_text("מייצר סלייד...")

  try:
    artist, song, chord_names = parse_song_request(text)
    config = load_config(CONFIG_PATH)
    artist_image = resolve_artist_or_hint_swap(config, artist, song)
    slug = make_slug(song)
    output = OUTPUT_DIR / f"{slug}.png"
    save_song_yaml(slug, artist, song, chord_names, Path("output") / f"{slug}.png")

    result = generate_song_slide(
      artist=artist,
      song=song,
      chord_names=chord_names,
      artist_image=artist_image,
      background=None,
      output=output,
      config=config,
    )

    await status.delete()
    await update.message.reply_photo(
      photo=BytesIO(result.read_bytes()),
      caption=f"{artist} — {song}",
    )
    log.info("Created %s for chat %s", result, update.effective_chat.id)
  except Exception as exc:
    log.exception("Generation failed")
    await status.edit_text(f"שגיאה:\n{exc}")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  text = update.message.text or ""
  if text.strip() == "/add":
    await update.message.reply_text(
      "כתוב אחרי /add:\n/add דודו אהרון | לילה טוב | Em,C,G,D"
    )
    return
  await generate_and_reply(update, text)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
  text = (update.message.text or "").strip()
  if "|" not in text:
    return
  await generate_and_reply(update, text)


def main() -> None:
  token = get_token()
  app = Application.builder().token(token).build()

  app.add_handler(CommandHandler("start", cmd_start))
  app.add_handler(CommandHandler("help", cmd_help))
  app.add_handler(CommandHandler("whoami", cmd_whoami))
  app.add_handler(CommandHandler("artists", cmd_artists))
  app.add_handler(CommandHandler("artist", cmd_artist))
  app.add_handler(CommandHandler("add", cmd_add))
  app.add_handler(MessageHandler(filters.PHOTO, on_photo))
  app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

  log.info("Bot starting...")
  app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
  main()
