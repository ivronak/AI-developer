"""App-wide switches the Owner can change (e.g. "authenticator required for everyone")."""
from app.extensions import db

DEFAULTS = {
    "require_2fa": True,          # every sign-in needs the authenticator app code
    "github_owner": "",           # GitHub account or organization whose repos are the projects (F04)
    "github_owner_type": "user",  # "user" or "org"
    "github_token_enc": "",       # Fernet-encrypted fine-grained token (read access to repos)
    "github_last_sync": None,     # ISO time of the last sync
    "github_last_result": "",     # short text: "3 repos · 1 new" or the error
    "telegram_token_enc": "",     # Fernet-encrypted bot token from @BotFather (F14 alerts)
    "telegram_chats": [],         # [{"id": 123, "name": "Owner"}] — chats that confirmed with a connect code
    "telegram_connect_code": "",  # one-time code the owner sends to the bot to link a chat
    "monitor_last_run": None,     # ISO time of the last monitor run
    "claude_key_enc": "",         # Fernet-encrypted Anthropic API key (Owner only — never shown again)
    "claude_preset": "normal",    # default choice in the chat: quick (Haiku) | normal (Sonnet) | best (Opus)
    "claude_max_usd": 3.0,        # stop one agent run when it has cost this much
    "claude_max_turns": 40,       # stop one agent run after this many model calls
    "github_token_expires": "",   # YYYY-MM-DD — read from GitHub's answer header on sync / test
    "claude_key_expires": "",     # YYYY-MM-DD — typed by the owner (Anthropic doesn't report it)
    "expiry_alerts_sent": [],     # ["github_token_expires:2027-01-12:7", …] so each reminder goes once
    "cc_oauth_token_enc": "",     # RETIRED 2026-11-04 (always ""): Claude Code logs in with web-login accounts (claude_accounts table)
    "cc_last_test": "",           # "OK · subscription" / "Failed · …" — last “Test Claude Code”
    "cc_default_model": "default",   # pre-selected model in the chat (a value from Claude Code's list; "default" = its own default)
    "cc_default_effort": "",         # pre-selected effort in the chat; "" = the model's own default
    "cc_credit_models": {},          # {"fable": "2026-10-23"} — models Claude Code refused for lack of usage credits (picker warns)
    "repo_aliases": {},           # {"oldowner/name": {"id": 123, "full_name": "newowner/name"}} — old names a server still uses
    "ai_low_balance_usd": 5.0,    # Telegram alert when the estimated Anthropic balance falls below this (Step 13)
    "ai_alert_state": {},         # {"low": true, "limits": {"<project id>": "2026-10:80"}, "credit": "2026-10-15"} — each alert once
}


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.JSON)


def get_setting(key):
    row = db.session.get(AppSetting, key)
    return row.value if row is not None else DEFAULTS.get(key)


def set_setting(key, value):
    row = db.session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value=value)
        db.session.add(row)
        db.session.flush()      # a second set_setting(key) in the same request now finds this row instead of inserting again
    row.value = value
