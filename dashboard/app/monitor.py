"""Uptime + page-change monitor (F15). Every 5 minutes (`flask monitor`, a timer on the server) each watched project's
live site is opened once:
- no answer / 5xx twice in a row → incident "Live site down" (red) + alert; when it answers again → cleared + alert
- the home page's visible text differs from the last good fingerprint twice in a row → "Home page changed unexpectedly"
  (red) + alert. A Go Live, or the owner's "This change is OK" button, takes a new fingerprint.
The checker never changes anything on the client's server — it only reads the home page."""
import hashlib
import re
import time

import requests

from app.alerts import raise_alert
from app.clock import now
from app.extensions import db
from app.models import Environment, Project
from app.models.setting import set_setting

TIMEOUT = 12
FAILS_FOR_DOWN = 2            # two checks in a row (≈10 minutes) before calling it down
CHANGES_FOR_ALERT = 2
DOWN_TEXT = "Live site down"
CHANGE_TEXT = "Home page changed unexpectedly"
_TAGS = re.compile(r"<(script|style|noscript|template)\b.*?</\1>", re.S | re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_NOISE = re.compile(r"[0-9]+")          # dates, counters, tokens change on every load — ignore digits


def fetch(url):
    """(status, ms, html). status 0 = no answer."""
    t0 = time.monotonic()
    try:
        r = requests.get(url, timeout=TIMEOUT, allow_redirects=True,
                         headers={"User-Agent": "ai-project-dashboard-monitor/1.0"})
        return r.status_code, int((time.monotonic() - t0) * 1000), r.text[:500_000]
    except requests.RequestException:
        return 0, int((time.monotonic() - t0) * 1000), ""


def fingerprint(html):
    """Fingerprint of what a visitor reads: title + visible text, without scripts, tags, digits and extra spaces."""
    text = _ANY_TAG.sub(" ", _TAGS.sub(" ", html or ""))
    text = _NOISE.sub("", " ".join(text.split())).lower()
    return hashlib.sha256(text.encode()).hexdigest()


def _minutes(since):
    return max(1, int((now() - since).total_seconds() // 60)) if since else 0


def check(project):
    """Check one project's live site. Returns a short status word for the caller."""
    live = project.env("live")
    if live is None or not live.url:
        return "no-live"
    status, ms, html = fetch(live.url)
    live.last_check_at, live.last_http_status, live.last_response_ms = now(), status, ms
    up = 200 <= status < 500

    if not up:
        live.fail_count = (live.fail_count or 0) + 1
        close_watch = live.watch_until is not None and live.watch_until > now()     # just after Go Live / Rollback
        if live.fail_count >= (1 if close_watch else FAILS_FOR_DOWN):
            live.down_since = live.down_since or now()
            was_down = project.incident.startswith(DOWN_TEXT)
            project.incident = f"{DOWN_TEXT} for {_minutes(live.down_since)} min"
            project.incident_at = project.incident_at if was_down else now()
            db.session.commit()
            if not was_down:
                why = "no answer" if status == 0 else f"error {status}"
                raise_alert("down", f"{project.name}: live site down ({why})", project)
            return "down"
        db.session.commit()
        return "retry"

    came_back = project.incident.startswith(DOWN_TEXT)
    live.fail_count, live.down_since = 0, None
    if came_back:
        project.incident, project.incident_at = "", None
        db.session.commit()
        raise_alert("back_up", f"{project.name}: live site is back up", project)

    fp = fingerprint(html)
    if not live.page_hash:
        live.page_hash = fp                                  # first look: this is the good version
    elif fp != live.page_hash:
        live.page_change_count = (live.page_change_count or 0) + 1
        if live.page_change_count >= CHANGES_FOR_ALERT and not project.incident:
            project.incident, project.incident_at = CHANGE_TEXT, now()
            db.session.commit()
            raise_alert("page_change", f"{project.name}: home page changed unexpectedly", project)
            return "changed"
    else:
        live.page_change_count = 0
    db.session.commit()
    return "ok"


def accept_page(project):
    """Owner says the current home page is fine (e.g. the client changed it): take a new fingerprint."""
    live = project.env("live")
    if live is None:
        return
    status, _, html = fetch(live.url)
    if 200 <= status < 500:
        live.page_hash = fingerprint(html)
    live.page_change_count = 0
    if project.incident == CHANGE_TEXT:
        project.incident, project.incident_at = "", None
    db.session.commit()


def run_all():
    from app import ai_budget, gitcheck, mobile, reminders
    reminders.check()
    try:
        mobile.refresh_due()                              # mobile test builds running on Codemagic
    except Exception:                                     # never let a build check stop the site checks
        db.session.rollback()
    ai_budget.check()
    gitcheck.check_due(Project.query.filter_by(is_active=True).all())
    results = {}
    watched = Project.query.filter_by(monitor_enabled=True, is_active=True).all()
    t = now()
    watched += [e.project for e in Environment.query.filter(Environment.kind == "live", Environment.watch_until > t).all()
                if e.project not in watched]
    for p in watched:
        results[p.name] = check(p)
    set_setting("monitor_last_run", now().isoformat())
    db.session.commit()
    return results
