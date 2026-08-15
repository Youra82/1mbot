# src/onembot/utils/telegram.py
# 1:1 aus mbot/src/mbot/utils/telegram.py uebernommen -- gleiche Signatur.
import requests
import logging

logger = logging.getLogger(__name__)


def send_photo(bot_token, chat_id, file_path, caption=""):
    """Sendet ein Bild (PNG/JPG) an einen Telegram-Chat."""
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert.")
        return
    api_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        with open(file_path, 'rb') as img:
            response = requests.post(api_url,
                                     data={'chat_id': chat_id, 'caption': caption},
                                     files={'photo': img}, timeout=30)
            response.raise_for_status()
    except FileNotFoundError:
        logger.error(f"Bild nicht gefunden: {file_path}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler beim Senden des Fotos: {e}")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler beim Foto-Versand: {e}")


def send_message(bot_token, chat_id, message):
    """Sendet eine Textnachricht an einen Telegram-Chat."""
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert. Nachricht nicht gesendet.")
        return

    escape_chars = r'_*[]()~`>#+-=|{}.!'
    escaped = message
    for char in escape_chars:
        escaped = escaped.replace(char, f'\\{char}')

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': escaped, 'parse_mode': 'MarkdownV2'}

    try:
        response = requests.post(api_url, data=payload, timeout=10)
        response.raise_for_status()
        logger.debug(f"Telegram-Nachricht erfolgreich gesendet.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Fehler beim Senden der Telegram-Nachricht: {e}")
    except Exception as e:
        logger.error(f"Unerwarteter Fehler beim Telegram-Versand: {e}")
