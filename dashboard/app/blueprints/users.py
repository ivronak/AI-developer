"""Users & log (S8, Owner only): add / edit developers, project assignment, access on/off, authenticator reset,
one-time set-password links, the "authenticator required" switch, and the activity log with filters."""
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.auth import log, new_setup_link, owner_required, reset_totp
from app.clock import local_day_start_utc
from app.extensions import db
from app.models import ActivityLog, Project, ProjectMember, User
from app.models.setting import get_setting, set_setting

bp = Blueprint("users", __name__)

LOG_LIMIT = 100
ACTION_ICON = {
    "Go Live": ("rocket", 150), "Rollback": ("undo", 24), "Client approved": ("check", 150),
    "Client asked changes": ("activity", 35), "Approved for client": ("check", 150), "Changes for client": ("x", 35),
    "Sent to client": ("send", 215), "Prompt": ("activity", 265), "Backup": ("db", 175), "Restore": ("db", 24),
    "Sync": ("sync", 199), "Beta deploy": ("upload", 220), "Alert": ("bell", 0), "Tests": ("check", 160),
    "Settings": ("activity", 240), "Sign in": ("users", 245), "Sign out": ("logout", 245),
    "Sign-in failed": ("x", 35), "Sign-in locked": ("x", 0), "User added": ("plus", 245), "User changed": ("users", 245),
    "Access off": ("x", 0), "Access on": ("check", 150), "Authenticator on": ("check", 150),
    "Authenticator off": ("x", 35), "Authenticator reset": ("sync", 35), "Password set": ("check", 245),
    "Password changed": ("check", 245), "Setup link": ("send", 245), "Recovery codes renewed": ("sync", 245),
}


def _user_or_404(uid):
    u = db.session.get(User, uid)
    if u is None:
        abort(404)
    return u


def _guard_other(u):
    """The owner account is changed only from My account, never from this list."""
    if u.is_owner:
        abort(403)


def _form_projects():
    ids = {int(x) for x in request.form.getlist("projects") if x.isdigit()}
    return [p.id for p in Project.query.filter(Project.id.in_(ids)).all()] if ids else []


def _validate(name, email, mobile, user=None):
    if not name:
        return "Enter a name."
    if "@" not in email or "." not in email.split("@")[-1]:
        return "Enter a valid email."
    other = User.query.filter(db.func.lower(User.email) == email).first()
    if other is not None and other is not user:
        return "Someone already uses this email."
    if len("".join(c for c in mobile if c.isdigit())) < 10:
        return "Enter a mobile number with at least 10 digits."
    return None


def _set_projects(u, pids):
    keep = set(pids)
    for m in list(u.memberships):
        if m.project_id not in keep:
            db.session.delete(m)
    have = {m.project_id for m in u.memberships}
    for pid in keep - have:
        db.session.add(ProjectMember(user_id=u.id, project_id=pid))


@bp.get("/users")
@login_required
@owner_required
def index():
    f_user, f_project = request.args.get("user", ""), request.args.get("project", "")
    f_from, f_to = request.args.get("from", ""), request.args.get("to", "")
    q = ActivityLog.query
    if f_user == "0":
        q = q.filter(ActivityLog.user_id.is_(None))
    elif f_user.isdigit():
        q = q.filter(ActivityLog.user_id == int(f_user))
    if f_project.isdigit():
        q = q.filter(ActivityLog.project_id == int(f_project))
    try:   # the dates people pick are business-time days
        if f_from:
            q = q.filter(ActivityLog.created_at >= local_day_start_utc(datetime.strptime(f_from, "%Y-%m-%d").date()))
        if f_to:
            q = q.filter(ActivityLog.created_at < local_day_start_utc((datetime.strptime(f_to, "%Y-%m-%d") + timedelta(days=1)).date()))
    except ValueError:
        pass
    total = q.count()
    rows = q.order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc()).limit(LOG_LIMIT).all()
    return render_template("users/index.html", page="users", title="Users & log",
                           users=User.query.order_by(User.role.desc(), User.name).all(),
                           projects=Project.query.order_by(Project.name).all(),
                           require_2fa=get_setting("require_2fa"), new_link=session.pop("new_link", None),
                           logs=rows, total=total, LOG_LIMIT=LOG_LIMIT, ACTION_ICON=ACTION_ICON,
                           f=dict(user=f_user, project=f_project, **{"from": f_from, "to": f_to}))


@bp.post("/users/new")
@login_required
@owner_required
def create():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    mobile = request.form.get("mobile", "").strip()
    err = _validate(name, email, mobile)
    if err:
        flash(err, "bad")
        return redirect(url_for("users.index"))
    u = User(name=name, email=email, mobile=mobile, role="developer", is_active=True)
    db.session.add(u)
    db.session.flush()
    _set_projects(u, _form_projects())
    token = new_setup_link(u)
    log("User added", details=f"{name} · {len(_form_projects())} project(s)")
    db.session.commit()
    session["new_link"] = {"name": name, "url": url_for("auth.setup_password", token=token, _external=True)}
    return redirect(url_for("users.index"))


@bp.post("/users/<int:uid>/edit")
@login_required
@owner_required
def edit(uid):
    u = _user_or_404(uid)
    _guard_other(u)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    mobile = request.form.get("mobile", "").strip()
    err = _validate(name, email, mobile, user=u)
    if err:
        flash(err, "bad")
        return redirect(url_for("users.index"))
    u.name, u.email, u.mobile = name, email, mobile
    pids = _form_projects()
    _set_projects(u, pids)
    log("User changed", details=f"{name} · {len(pids)} project(s)")
    db.session.commit()
    flash("Saved.", "ok")
    return redirect(url_for("users.index"))


@bp.post("/users/<int:uid>/access")
@login_required
@owner_required
def access(uid):
    u = _user_or_404(uid)
    _guard_other(u)
    turn_on = request.form.get("on") == "1"
    u.is_active = turn_on
    if not turn_on:
        u.session_version += 1          # signs them out everywhere right now
    log("Access on" if turn_on else "Access off", details=f"{u.name} — access turned {'on' if turn_on else 'off'}")
    db.session.commit()
    flash(f"{u.name}: access {'on' if turn_on else 'off'}.", "ok")
    return redirect(url_for("users.index"))


@bp.post("/users/<int:uid>/reset-2fa")
@login_required
@owner_required
def reset_2fa(uid):
    u = _user_or_404(uid)
    _guard_other(u)
    reset_totp(u)
    u.session_version += 1
    log("Authenticator reset", details=f"{u.name} — sets it up again at next sign-in")
    db.session.commit()
    flash(f"{u.name}: authenticator reset. They set it up again at their next sign-in.", "ok")
    return redirect(url_for("users.index"))


@bp.post("/users/<int:uid>/setup-link")
@login_required
@owner_required
def setup_link(uid):
    """New one-time "set your password" link. The old password stops working right away."""
    u = _user_or_404(uid)
    _guard_other(u)
    u.password_hash = None
    u.session_version += 1
    token = new_setup_link(u)
    log("Setup link", details=f"{u.name} — new set-password link, old password stopped")
    db.session.commit()
    session["new_link"] = {"name": u.name, "url": url_for("auth.setup_password", token=token, _external=True)}
    return redirect(url_for("users.index"))


@bp.post("/settings/security")
@login_required
@owner_required
def security():
    on = request.form.get("require_2fa") == "1"
    set_setting("require_2fa", on)
    log("Settings", details=f"Authenticator required for everyone: {'on' if on else 'off'}")
    db.session.commit()
    flash("Authenticator is now required for everyone." if on else "Authenticator is now optional (each person decides).", "ok")
    return redirect(url_for("users.index"))
