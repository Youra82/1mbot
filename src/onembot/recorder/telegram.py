"""Minimaler Telegram-Versand (blockierend, wird im Executor aufgerufen)."""
from __future__ import annotations

import json
import logging
import os

import requests

log = logging.getLogger("1mbot.telegram")


def load_telegram(secret_path: str = "secret.json") -> tuple[str, str] | None:
    if not os.path.exists(secret_path):
        return None
    with open(secret_path, encoding="utf-8") as f:
        tg = json.load(f).get("telegram") or {}
    if tg.get("bot_token") and tg.get("chat_id"):
        return tg["bot_token"], str(tg["chat_id"])
    return None


def send(creds: tuple[str, str] | None, text: str) -> None:
    if not creds:
        return
    token, chat = creds
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": text}, timeout=15)
    except Exception as ex:
        log.warning("Telegram fehlgeschlagen: %r", ex)
