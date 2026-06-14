"""WhatsApp Cloud API (Meta) — upload and send video messages."""

from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger("forcuapcut.whatsapp")

GRAPH_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0").strip() or "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


@dataclass(frozen=True)
class WhatsAppConfig:
  access_token: str
  phone_number_id: str
  recipient: str

  @classmethod
  def from_env(cls) -> WhatsAppConfig:
    return cls(
      access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip(),
      phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
      recipient=_normalize_recipient(os.getenv("WHATSAPP_RECIPIENT", "")),
    )

  @property
  def enabled(self) -> bool:
    return bool(self.access_token and self.phone_number_id and self.recipient)


def _normalize_recipient(raw: str) -> str:
  """International digits only, e.g. 972501234567."""
  digits = "".join(ch for ch in raw.strip() if ch.isdigit())
  return digits


def is_whatsapp_enabled() -> bool:
  return WhatsAppConfig.from_env().enabled


def is_auto_send_enabled() -> bool:
  return os.getenv("WHATSAPP_AUTO_SEND", "").strip().lower() in ("1", "true", "yes")


class WhatsAppError(RuntimeError):
  pass


class WhatsAppClient:
  def __init__(self, config: WhatsAppConfig | None = None) -> None:
    self.config = config or WhatsAppConfig.from_env()

  def _headers(self) -> dict[str, str]:
    return {"Authorization": f"Bearer {self.config.access_token}"}

  def upload_media(self, file_path: Path, media_type: str) -> str:
    url = f"{GRAPH_BASE}/{self.config.phone_number_id}/media"
    mime, _ = mimetypes.guess_type(file_path.name)
    mime = mime or "video/mp4"

    with file_path.open("rb") as handle:
      response = requests.post(
        url,
        headers=self._headers(),
        data={"messaging_product": "whatsapp", "type": media_type},
        files={"file": (file_path.name, handle, mime)},
        timeout=180,
      )

    if not response.ok:
      raise WhatsAppError(
        f"העלאת וידאו נכשלה ({response.status_code}): {response.text}"
      )

    media_id = response.json().get("id")
    if not media_id:
      raise WhatsAppError(f"תשובה לא תקינה מהעלאת מדיה: {response.text}")
    log.info("Uploaded WhatsApp media %s -> %s", file_path.name, media_id)
    return media_id

  def send_video(self, video_path: Path, *, caption: str = "") -> dict:
    if not self.config.enabled:
      raise WhatsAppError("ווצאפ לא מוגדר — חסרים משתני סביבה ב-.env")

    if not video_path.is_file():
      raise FileNotFoundError(f"קובץ וידאו לא נמצא: {video_path}")

    media_id = self.upload_media(video_path, "video")
    url = f"{GRAPH_BASE}/{self.config.phone_number_id}/messages"
    payload: dict = {
      "messaging_product": "whatsapp",
      "to": self.config.recipient,
      "type": "video",
      "video": {"id": media_id},
    }
    if caption.strip():
      payload["video"]["caption"] = caption.strip()[:1024]

    response = requests.post(
      url,
      headers={**self._headers(), "Content-Type": "application/json"},
      json=payload,
      timeout=60,
    )
    if not response.ok:
      raise WhatsAppError(
        f"שליחת וידאו נכשלה ({response.status_code}): {response.text}"
      )

    log.info("Sent WhatsApp video %s to %s", video_path.name, self.config.recipient)
    return response.json()


def send_video_to_whatsapp(video_path: Path, *, caption: str = "") -> str:
  """Send a local video file. Returns a short Hebrew status for Telegram."""
  client = WhatsAppClient()
  client.send_video(video_path, caption=caption)
  return "נשלח לווצאפ ✓"
