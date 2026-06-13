#!/usr/bin/env python3
"""Telegram bot — send song details, receive slide PNG."""

from __future__ import annotations

import json
import logging
import os
import re
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

HELP_TEXT = (
  "מה אפשר לעשות:\n\n"
  'צור זמר "עדן בן זקן" + תמונה (בהודעה אחת)\n'
  "או: קודם הטקסט, ואז תמונה\n\n"
  "רשימת זמרים\n"
  "← רשימה + מחיקה\n\n"
  "מחק עדן בן זקן\n"
  "← מוחק זמר ותמונה\n\n"
  "פתיחה:\n"
  "צור פתיחה | 3 שירים מוכרים | של 4 אקורדים | 5 דקות ללמוד | אקורדים בסיסיים בלבד\n\n"
  "סיום:\n"
  "צור סיום | עוד שירים כאלה | כל שבוע | עקבו | כדי לא לפספס\n\n"
  "שיר:\n"
  "דודו אהרון | שם השיר | Em,C,G,D"
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


def parse_song_request(text: str) -> tuple[str, str, list[str]]:
  """Parse: זמר | שיר | Em,C,G,D"""
  cleaned = text.strip()
  if cleaned.startswith("צור שיר"):
    cleaned = cleaned[len("צור שיר") :].strip()

  parts = [part.strip() for part in cleaned.split("|")]
  if len(parts) != 3:
    raise ValueError(
      "פורמט לא תקין.\n"
      "כתוב: זמר | שיר | אקורדים\n"
      "דוגמה: דודו אהרון | לילה טוב | Em,C,G,D"
    )

  artist, song, chords_raw = parts
  if not artist or not song or not chords_raw:
    raise ValueError("חסר זמר, שם שיר או אקורדים.")

  chord_names = [c.strip() for c in chords_raw.split(",") if c.strip()]
  if not (4 <= len(chord_names) <= 6):
    raise ValueError(f"צריך 4–6 אקורדים, קיבלתי {len(chord_names)}.")

  return artist, song, chord_names


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


def parse_intro_request(text: str) -> list[str] | None:
  return parse_text_slide_request(text, ("צור פתיחה", "פתיחה"))


def parse_outro_request(text: str) -> list[str] | None:
  return parse_text_slide_request(text, ("צור סיום", "סיום"))


def save_text_slide_yaml(lines: list[str], *, yaml_name: str, output_name: str) -> Path:
  INTROS_DIR.mkdir(parents=True, exist_ok=True)
  yaml_path = INTROS_DIR / yaml_name
  keys = ("line1", "line2", "line3", "line4")
  rows = [f'{keys[i]}: "{lines[i]}"' for i in range(len(lines))]
  rows.append(f'output: "output/{output_name}"')
  yaml_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
  return yaml_path


def save_intro_yaml(lines: list[str]) -> Path:
  return save_text_slide_yaml(lines, yaml_name="intro.yaml", output_name="intro.png")


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


def copy_to_desktop(source: Path, dest_name: str) -> Path:
  desktop = get_desktop_dir()
  desktop.mkdir(parents=True, exist_ok=True)
  dest = desktop / dest_name
  shutil.copy2(source, dest)
  return dest


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

    await status.delete()
    await update.message.reply_photo(
      photo=BytesIO(result.read_bytes()),
      caption=caption,
    )
    log.info("Created %s %s for chat %s", log_label, result, update.effective_chat.id)
  except Exception as exc:
    log.exception("%s generation failed", log_label.capitalize())
    await status.edit_text(f"שגיאה:\n{exc}")


async def generate_intro_and_reply(update: Update, text: str) -> None:
  await generate_text_slide_and_reply(
    update,
    text,
    parse_request=parse_intro_request,
    save_yaml=save_intro_yaml,
    output_name="intro.png",
    status_label="פתיחה",
    caption="פתיחה",
    log_label="intro",
  )


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

    desktop_path = copy_to_desktop(result, safe_filename(song))

    await status.delete()
    await update.message.reply_photo(
      photo=BytesIO(result.read_bytes()),
      caption=f"{artist} — {song}\n\nנשמר גם בשולחן העבודה:\n{desktop_path.name}",
    )
    log.info("Created %s for chat %s, copied to %s", result, update.effective_chat.id, desktop_path)
  except Exception as exc:
    log.exception("Generation failed")
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
      await generate_and_reply(update, text)
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
  app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

  log.info("Bot starting...")
  app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
  main()
