#!/usr/bin/env python3
"""
Entry Point fuer 1mbot (Grid/Market-Making, Bitget).

Phase 1: reiner Dry-Run. live_trading=false wird zusaetzlich hart in
onembot.exchange.ws_client.WsClient geprueft -- ein Config-Fehler allein
kann keine echte Order ausloesen.
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from onembot.run_loop import run_bot  # noqa: E402

ROOT = Path(__file__).resolve().parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "logs" / "1mbot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    settings = load_json(ROOT / "settings.json")

    secret_path = ROOT / "secret.json"
    account_config = None
    telegram_cfg = {"send_status_updates": False}
    if secret_path.exists():
        secrets = load_json(secret_path)
        accounts = secrets.get("1mbot", [])
        account_config = accounts[0] if accounts else None
        telegram_secrets = secrets.get("telegram", {})
        telegram_cfg = {
            **settings.get("telegram", {}),
            "bot_token": telegram_secrets.get("bot_token"),
            "chat_id": telegram_secrets.get("chat_id"),
        }
    else:
        logger.warning("secret.json nicht gefunden -- laeuft im rein oeffentlichen Modus ohne Telegram-Reporting.")

    if settings["live_trading"]:
        logger.critical(
            "settings.json hat live_trading=true, aber Phase 1 von 1mbot unterstuetzt nur Dry-Run. Abbruch."
        )
        sys.exit(1)

    logger.info(f"Starte 1mbot (Dry-Run) fuer {len(settings['watchlist'])} Symbole.")
    asyncio.run(run_bot(settings, account_config, telegram_cfg))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Beendet durch Benutzer.")
