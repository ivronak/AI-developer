"""Projects overview (S2): KPI tiles, Health / Activity log / Approvals panels, project cards + list.
Also: the Owner recording a client's answer from the Approvals panel, and marking alerts seen."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import health
from app.auth import can_see_project, log, owner_required, visible_projects_query
from app.clock import now
from app.extensions import db
from app.models import ActivityLog, Alert, Change, ChangeMessage, Project

bp = Blueprint("main", __name__)

ACTIVITY_ICON = {
    "Go Live": ("rocket", 150), "Rollback": ("undo", 24), "Client approved": ("check", 150),
    "Client asked changes": ("activity", 35), "Approved for client": ("check", 150), "Changes for client": ("x", 35),
    "Sent to client": ("send", 215), "Prompt": ("activity", 265), "Backup": ("db", 175), "Restore": ("db", 24),
    "Sync": ("sync", 199), "Beta deploy": ("upload", 220), "Alert": ("bell", 0), "Tests": ("check", 160),
    "Settings": ("activity", 240),
}


def _safe_back(default):
    back = request.referrer or ""
    return back if back.startswith(request.host_url) else default


@bp.get("/")
def index():
    return redirect(url_for("main.projects"))


@bp.get("/projects")
@login_required
def projects():
    rows = [health.summary(p) for p in visible_projects_query(current_user).order_by(Project.name).all()]
    rank = {"bad": 0, "warn": 1, "ok": 2}
    rows.sort(key=lambda r: (rank[r["level"]], r["p"].name))
    ids = [r["p"].id for r in rows]

    counts = {k: sum(1 for r in rows if r["level"] == k) for k in ("ok", "warn", "bad")}
    activity = (ActivityLog.query.filter(ActivityLog.project_id.in_(ids))
                .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc()).limit(14).all()) if ids else []
    pending = (Change.query.filter(Change.project_id.in_(ids), Change.status == "client").all()) if ids else []
    pending.sort(key=lambda c: c.approval.sent_at if c.approval else now())
    approved = (Change.query.filter(Change.project_id.in_(ids), Change.status == "approved").all()) if ids else []

    ai = None
    if current_user.is_owner:
        from app import ai_budget
        starts = ai_budget.period_starts()
        left, base = ai_budget.balance()
        ai = {"left": left, "low": ai_budget.low_amount(), "today": ai_budget.spend(since=starts["today"]),
              "month": ai_budget.spend(since=starts["month"]),
              "near": [r for r in (dict(zip(("spent", "lim", "pct"), ai_budget.limit_state(p)), p=p)
                                   for p in Project.query.filter(Project.ai_monthly_limit_usd.isnot(None)).all())
                       if r["pct"] is not None and r["pct"] >= 80]}
    return render_template("pages/projects.html", page="projects", crumb="Overview", title="Projects",
                           rows=rows, counts=counts, activity=activity, pending=pending, approved=approved,
                           total_open=sum(r["open"] for r in rows), total_waiting=sum(r["waiting"] for r in rows),
                           ACTIVITY_ICON=ACTIVITY_ICON, slow_after=health.APPROVAL_SLOW, now=now(), ai=ai)


@bp.post("/changes/<int:change_id>/answer")
@login_required
@owner_required
def record_answer(change_id):
    """Owner records the client's answer (e.g. given on a call). Always with a note, always logged."""
    c = db.session.get(Change, change_id)
    if c is None or not can_see_project(current_user, c.project_id):
        abort(404)
    a = c.approval
    if c.status != "client" or a is None or a.decision:
        flash("This change is not waiting for the client any more.", "bad")
        return redirect(_safe_back(url_for("main.projects")))
    yes = request.form.get("decision") == "approved"
    note = (request.form.get("note") or "").strip()
    if not note:
        flash("Write how the client approved." if yes else "Write what the client wants changed.", "bad")
        return redirect(_safe_back(url_for("main.projects")))
    a.decision = "approved" if yes else "changes"
    a.decided_at = now()
    a.comment = note
    a.recorded_by_id = current_user.id
    c.status = "approved" if yes else "changes"
    if not yes:
        db.session.add(ChangeMessage(change_id=c.id, sender="owner", user_id=current_user.id,
                                     body=f"Client asked for changes: {note}"))
    log("Approved for client" if yes else "Changes for client", project_id=c.project_id, details=f"{c.title} — {note}")
    db.session.commit()
    flash("Approved. It is ready to go live." if yes else "Sent back with the client's note.", "ok")
    return redirect(_safe_back(url_for("main.projects")))


@bp.get("/backups")
@login_required
def backups():
    return render_template("pages/placeholder.html", page="backups", title="Backups",
                           lead="Daily database + files backups and a snapshot before every Go Live.",
                           next_step="Step 11 brings backups to cloud storage and Restore.")


@bp.post("/alerts/seen")
@login_required
def alerts_seen():
    q = Alert.query.filter(Alert.seen_at.is_(None))
    if not current_user.is_owner:
        ids = [p.id for p in visible_projects_query(current_user).with_entities(Project.id)]
        q = q.filter(Alert.project_id.in_(ids))
    for a in q.all():
        a.seen_at = now()
    db.session.commit()
    return redirect(_safe_back(url_for("main.projects")))


@bp.get("/health")
def health_check():
    """Plain liveness check for the monitor / deploy script."""
    return {"ok": True}
