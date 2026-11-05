"""Project health + the numbers every card / tile shows (same rules as the mock's APP.health()).
red (bad)  = a live incident the monitor found (site down, page changed unexpectedly)
yellow     = last good backup older than 26 h · tests failing on beta · client approval waiting 3+ days
green (ok) = none of the above"""
from datetime import timedelta

from app.clock import now
from app.models import Backup, Change, Deployment

BACKUP_STALE = timedelta(hours=26)
APPROVAL_SLOW = timedelta(days=3)
LABEL = {"bad": "Down", "warn": "Attention", "ok": "Healthy"}
LONG_LABEL = {"bad": "Down / error", "warn": "Needs attention", "ok": "Healthy"}


def _age_text(delta):
    mins = int(delta.total_seconds() // 60)
    if mins < 60:
        return f"{mins} min"
    h = round(mins / 60)
    if h < 24:
        return f"{h} h"
    d = round(mins / 1440)
    return f"{d} day{'s' if d != 1 else ''}"


def last_deploy(project_id):
    return (Deployment.query.filter_by(project_id=project_id).join(Deployment.environment)
            .filter_by(kind="live").order_by(Deployment.created_at.desc()).first())


def last_backup(project_id):
    return (Backup.query.filter(Backup.project_id == project_id, Backup.ok.is_(True), Backup.kind != "snapshot")
            .order_by(Backup.created_at.desc()).first())


def summary(p):
    """Everything a project card needs, in one dict."""
    t = now()
    changes = Change.query.filter_by(project_id=p.id).all()
    ld, lb = last_deploy(p.id), last_backup(p.id)
    stale = lb is None or t - lb.created_at > BACKUP_STALE
    waiting = [c for c in changes if c.status == "client"]

    level, reason = "ok", ""
    if p.incident:
        level, reason = "bad", p.incident
    elif stale:
        level, reason = "warn", f"Last backup is {_age_text(t - lb.created_at)} old" if lb else "No backup yet"
    elif any(c.status == "beta" and c.tests_total is not None and not c.tests_ok for c in changes):
        level, reason = "warn", "Tests failing on beta"
    else:
        slow = [c for c in waiting if c.approval and t - c.approval.sent_at > APPROVAL_SLOW]
        if slow:
            level, reason = "warn", f"Client approval waiting {_age_text(t - slow[0].approval.sent_at)}"

    return {
        "p": p, "level": level, "label": LABEL[level], "long_label": LONG_LABEL[level], "reason": reason,
        "open": sum(1 for c in changes if c.status not in ("live", "cancelled")), "waiting": len(waiting),
        "last_deploy": ld, "last_backup": lb, "backup_stale": stale,
        "live": p.env("live"), "beta": p.env("beta"),
    }
