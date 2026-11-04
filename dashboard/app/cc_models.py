"""The Claude models that Claude Code offers — the same list its /model picker shows, with each model's effort levels.

Nothing here is hard-coded and nothing comes from the Anthropic API: a small root job on the server
(ai-dashboard-models.timer → /usr/local/sbin/aidash-claude-models) asks the VPS's Claude Code for its model list every hour
and writes it to /etc/ai-dashboard/claude-models.json (model facts, plus the VPS Claude's plan, a masked e-mail hint such as
j***@gmail.com, connector names and status, and whether its service runs — never the full e-mail, organisation or a token).
The dashboard only reads that file. When Anthropic adds a model, Claude Code lists it and it shows up in the chat within the hour. If the file
is missing the chat falls back to the Quick / Normal / Best presets; if the job stops, the last good list stays in use."""
import json
import os
import re
from datetime import datetime, timezone

from flask import current_app

from app.clock import now
from app.models.setting import get_setting, set_setting

LEVELS = ("low", "medium", "high", "xhigh", "max")
VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")      # also what may safely follow --model
DEFAULT_FILE = "/etc/ai-dashboard/claude-models.json"
STALE_AFTER = 3 * 3600          # the job runs every hour; 3 hours without a refresh means it has stopped
_cache = {}                     # (path, mtime) -> parsed file, so the chat page does not re-read it on every view


def _clean(m):
    """One model entry from the file, or None when it does not look like one (the file is root-written, but be strict)."""
    if not isinstance(m, dict) or not isinstance(m.get("value"), str) or not VALUE_RE.match(m["value"]):
        return None
    name = m.get("displayName")
    if not isinstance(name, str) or not name.strip():
        return None
    levels = m.get("supportedEffortLevels")
    levels = [lv for lv in LEVELS if isinstance(levels, list) and lv in levels]
    resolved = m.get("resolvedModel")
    return {"value": m["value"], "resolvedModel": resolved if isinstance(resolved, str) and VALUE_RE.match(resolved) else m["value"],
            "displayName": name.strip()[:80], "description": str(m.get("description") or "")[:200],
            "supportsEffort": bool(m.get("supportsEffort")) and bool(levels), "supportedEffortLevels": levels}


CONNECTOR_STATUSES = ("connected", "needs-auth", "failed")


def _clean_vps(v):
    """The root job's facts about the VPS Claude (plan, connectors, remote-control service), or None. Names and statuses only."""
    if not isinstance(v, dict):
        return None
    conns = v.get("connectors")
    if isinstance(conns, list):
        conns = [{"name": str(c.get("name") or "")[:60], "status": c.get("status")} for c in conns
                 if isinstance(c, dict) and c.get("name") and c.get("status") in CONNECTOR_STATUSES][:30]
    else:
        conns = None
    return {"plan": str(v.get("plan") or "")[:30], "account_hint": str(v.get("account_hint") or "")[:60], "connectors": conns,
            "remote_control": v.get("remote_control") if v.get("remote_control") in ("active", "inactive") else "unknown"}


def load():
    """dict(ok, models, fetched_at, age_hours, stale, vps). ok is False when the file is missing or holds no usable model.
    vps: facts about the VPS Claude (see _clean_vps) or None."""
    path = current_app.config.get("CLAUDE_MODELS_FILE") or DEFAULT_FILE
    nothing = {"ok": False, "models": [], "fetched_at": "", "age_hours": None, "stale": False, "vps": None}
    try:
        key = (path, os.stat(path).st_mtime)
        doc = _cache.get(key)
        if doc is None:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            doc = {"models": [c for c in (_clean(m) for m in (raw.get("models") or [])) if c],
                   "fetched_at": str(raw.get("fetched_at") or ""), "vps": _clean_vps(raw.get("vps_claude"))}
            _cache.clear()
            _cache[key] = doc
    except (OSError, ValueError, AttributeError):
        return nothing
    if not doc["models"]:
        return nothing
    try:
        age = (datetime.now(timezone.utc) - datetime.strptime(doc["fetched_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()
    except ValueError:
        age = None
    return {"ok": True, "models": doc["models"], "fetched_at": doc["fetched_at"], "vps": doc["vps"],
            "age_hours": None if age is None else round(age / 3600, 1), "stale": age is None or age > STALE_AFTER}


def find(models, value):
    return next((m for m in models if m["value"] == value), None)


def default_choice():
    """(model, effort) pre-selected in the chat: the owner's choice in Settings → Claude Code, else Claude Code's own default."""
    return get_setting("cc_default_model") or "default", get_setting("cc_default_effort") or ""


def resolve(value, effort, models=None):
    """What the chat form sent → the (model, effort) that is safe to store and hand to Claude Code: a model that is in the
    list (else the default one) and an effort level that model supports (else "" = the model's own default).
    models: the picked Claude account's own list (cc_logins.models_for); None = the server-wide list.
    (None, None) when there is no list — the caller then keeps the Quick / Normal / Best preset."""
    if models is None:
        cat = load()
        models = cat["models"] if cat["ok"] else []
    if not models:
        return None, None
    m = find(models, value) or find(models, default_choice()[0]) or models[0]
    return m["value"], (effort if effort in m["supportedEffortLevels"] else "")


def launch_value(value, models=None):
    """What goes after --model for a model picked in the chat. "Default" is Claude Code's own pick, which the list names
    (resolvedModel). It must be sent explicitly: a resumed session otherwise keeps the model it last used (seen: Default
    after Opus still ran on Opus), so the owner's choice would be ignored. None = no list, send no --model flag."""
    if value != "default":
        return value
    m = find(models if models else load()["models"], "default")
    return m["resolvedModel"] if m and m["resolvedModel"] != "default" else None


def needs_credits():
    """{model value: date} of models that Claude Code refused because the plan has no usage credits for them."""
    flagged = get_setting("cc_credit_models")
    return flagged if isinstance(flagged, dict) else {}


def note_needs_credits(value):
    flagged = dict(needs_credits())
    flagged[value] = now().strftime("%Y-%m-%d")
    set_setting("cc_credit_models", flagged)


def note_ok(value):
    """A run on this model worked: it no longer needs the warning."""
    flagged = dict(needs_credits())
    if flagged.pop(value, None) is not None:
        set_setting("cc_credit_models", flagged)
