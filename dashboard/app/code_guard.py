"""What the code guard (deploy/aidash-code-guard, a root timer outside this folder) last found, for the Settings page.
The guard writes /etc/ai-dashboard/code-guard.json; this only reads it. If a deploy ever replaces this code with an older copy,
this page part disappears — which is exactly why the guard raises its alert through the dashboard's bell table and Telegram too."""
import json
import os
from datetime import datetime, timezone

from flask import current_app

DEFAULT_FILE = "/etc/ai-dashboard/code-guard.json"
STALE_AFTER = 45 * 60          # the guard runs every 5 minutes; a flagged deploy may pause it for 30


def load():
    """None when there is no file, else dict(ok, problems, checkpoint, checked_at, age_minutes, stale)."""
    path = current_app.config.get("CODE_GUARD_FILE") or DEFAULT_FILE
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        age = (datetime.now(timezone.utc) - datetime.strptime(str(data.get("checked_at") or ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()
    except ValueError:
        age = None
    cp = data.get("checkpoint") if isinstance(data.get("checkpoint"), dict) else {}
    return {"ok": bool(data.get("ok")), "problems": [str(p)[:300] for p in (data.get("problems") or [])][:5],
            "checkpoint": {"commit": str(cp.get("commit", ""))[:12], "at": str(cp.get("at", ""))[:20], "message": str(cp.get("message", ""))[:120]},
            "checked_at": str(data.get("checked_at", ""))[:20], "age_minutes": None if age is None else int(age // 60),
            "stale": age is None or age > STALE_AFTER}
