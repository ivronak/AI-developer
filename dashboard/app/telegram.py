"""Telegram alerts (F14). The owner makes a bot with @BotFather and saves its token in Settings; a chat is linked by
sending the bot a one-time code shown in Settings (so a stranger who finds the bot can't subscribe).
Tests replace api() — they never call the real Telegram API."""
import secrets

import requests

from app.crypto import decrypt_secret
from app.models.setting import get_setting, set_setting

TIMEOUT = 10


class TelegramError(Exception):
    pass


def _token():
    return decrypt_secret(get_setting("telegram_token_enc") or "")


def api(token, method, payload=None):
    """One Telegram Bot API call → result. Raises TelegramError with a plain message."""
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload or {}, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise TelegramError(f"Could not reach Telegram ({e.__class__.__name__}).")
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code == 401 or (r.status_code == 404 and not data.get("ok")):
        raise TelegramError("Telegram refused the bot token. Copy it again from @BotFather.")
    if not data.get("ok"):
        raise TelegramError(f"Telegram answered: {data.get('description') or r.status_code}")
    return data["result"]


def bot_name(token=None):
    me = api(token or _token(), "getMe")
    return me.get("username", "")


def new_connect_code():
    code = f"CONNECT-{secrets.randbelow(900000) + 100000}"
    set_setting("telegram_connect_code", code)
    return code


def find_connect_chat():
    """Look at the bot's recent messages for the connect code. Returns the new chat dict or None."""
    code = get_setting("telegram_connect_code")
    if not code:
        return None
    for upd in reversed(api(_token(), "getUpdates", {"limit": 50, "timeout": 0})):
        msg = upd.get("message") or {}
        if (msg.get("text") or "").strip().upper() == code:
            chat = msg.get("chat", {})
            name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("title") or "Chat"
            chats = [c for c in (get_setting("telegram_chats") or []) if c["id"] != chat["id"]]
            entry = {"id": chat["id"], "name": name}
            set_setting("telegram_chats", chats + [entry])
            set_setting("telegram_connect_code", "")
            return entry
    return None


def send(text):
    """Send to every linked chat. Returns how many got it; never raises (an alert must not break the caller)."""
    token, chats = _token(), get_setting("telegram_chats") or []
    if not token or not chats:
        return 0
    sent = 0
    for c in chats:
        try:
            api(token, "sendMessage", {"chat_id": c["id"], "text": text, "disable_web_page_preview": True})
            sent += 1
        except TelegramError:
            pass
    return sent
