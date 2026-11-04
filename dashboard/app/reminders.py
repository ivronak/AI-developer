"""Expiry reminders: the GitHub token and the Anthropic API key stop working on a date. A Telegram alert goes 7 days
before, 1 day before, and once when expired — each only once. Checked with every monitor run (every 5 minutes)."""
from datetime import date, datetime

from app.alerts import raise_alert
from app.clock import to_local, now
from app.extensions import db
from app.models.setting import get_setting, set_setting

ITEMS = [
    ("github_token_expires", "The GitHub token", "GitHub sync and Claude's branches stop", "GitHub → Settings → Developer settings → make a new token, then paste it in Settings → GitHub"),
    ("claude_key_expires", "The Claude (Anthropic) API key", "the Claude chat stops", "console.anthropic.com → API keys → make a new key, then paste it in Settings → Claude"),
]
STAGES = [(0, "today — it has expired"), (1, "tomorrow"), (7, "in 7 days")]   # most urgent first


def days_left(key):
    raw = get_setting(key) or ""
    try:
        d = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None, None
    return d, (d - to_local(now()).date()).days


def check():
    """Send any reminder that is due. Returns the messages sent."""
    sent = list(get_setting("expiry_alerts_sent") or [])
    out = []
    for key, label, effect, fix in ITEMS:
        d, left = days_left(key)
        if d is None:
            continue
        stage = next(((limit, when) for limit, when in STAGES if left <= limit), None)   # the most urgent that applies
        if stage is None:
            continue
        mark = f"{key}:{d.isoformat()}:{stage[0]}"
        if mark in sent:
            continue
        if left < 0:
            msg = f"{label} expired on {d.strftime('%-d %b %Y')} — {effect}. Fix: {fix}."
        else:
            msg = f"{label} expires {stage[1]} ({d.strftime('%-d %b %Y')}) — then {effect}. Fix: {fix}."
        raise_alert("expiry", msg, link=False)
        sent.append(mark)
        out.append(msg)
    if out:
        set_setting("expiry_alerts_sent", sent[-50:])
        db.session.commit()
    return out


def summary():
    """For the Settings page: [(label, date, days left)]."""
    rows = []
    for key, label, _, _ in ITEMS:
        d, left = days_left(key)
        rows.append((label, d, left))
    return rows
