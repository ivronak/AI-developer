"""Client approval (F09 / S5). "Send to client" makes a one-time link — no login — where the client opens beta, then
approves or asks for changes (note + optional screenshot). The link closes after LINK_DAYS days, after one answer, or when
a newer link / a follow-up to Claude replaces it. Only the sha256 of the link's secret is used to find it; the secret
itself is kept encrypted so the team can copy the link again. Every answer goes to the activity log and raises an alert."""
import secrets
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.alerts import raise_alert
from app.auth import client_ip, log, project_or_404
from app.clock import now
from app.crypto import decrypt_secret, encrypt_secret, sha256
from app.extensions import db
from app.models import Approval, Change, ChangeAttachment, ChangeMessage

bp = Blueprint("approve", __name__)

LINK_DAYS = 7
MAX_ITEMS = 12


def link_for(a):
    """The client's link for an approval, or '' when it can't be shown again (older rows)."""
    secret = decrypt_secret(a.token_enc or "") if a is not None else ""
    if not secret:
        return ""
    base = current_app.config.get("PUBLIC_URL")
    path = url_for("approve.page", token=secret)
    return base.rstrip("/") + path if base else url_for("approve.page", token=secret, _external=True)


def is_open(a):
    return a is not None and a.decision is None and a.expires_at > now() and a.change.status == "client"


@bp.post("/changes/<int:change_id>/send")
@login_required
def send(change_id):
    """Make (or remake) the client's link. Any older open link of this change closes."""
    c = db.session.get(Change, change_id) or abort(404)
    p = project_or_404(c.project_id)
    back = redirect(url_for("projects.detail", project_id=p.id, change=c.id) + "#chat")
    if c.status not in ("passed", "client"):
        flash("Only a change whose tests passed can go to the client.", "bad")
        return back
    if c.agent_state in ("queued", "working"):
        flash("Claude is still working on this change — wait for it to finish.", "bad")
        return back
    if p.env("beta") is None:
        flash("Add the beta (test site) address first — the client opens the change there.", "bad")
        return back
    items = [x.strip()[:200] for x in (request.form.get("checklist") or "").splitlines() if x.strip()][:MAX_ITEMS]
    if not items:
        flash("Write at least one thing the client should check.", "bad")
        return back
    t = now()
    for old in c.approvals:
        if old.decision is None and old.expires_at > t:
            old.expires_at = t
    secret = secrets.token_urlsafe(32)
    db.session.add(Approval(change_id=c.id, token=sha256(secret), token_enc=encrypt_secret(secret), checklist=items,
                            sent_at=t, expires_at=t + timedelta(days=LINK_DAYS)))
    again = c.status == "client"
    c.status = "client"
    log("Sent to client", project_id=p.id, details=f"{c.title}{' (new link)' if again else ''}")
    db.session.commit()
    flash("Link ready. Copy it or share it on WhatsApp.", "ok")
    return back


def _approval_or_none(token):
    if not token or len(token) > 100:
        return None
    return Approval.query.filter_by(token=sha256(token)).first()


def _page(a, **kw):
    resp = current_app.make_response(render_template("approve/page.html", a=a, LINK_DAYS=LINK_DAYS, **kw))
    resp.headers["Referrer-Policy"] = "no-referrer"          # the link is the key — never leak it to the beta site
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


def _closed_reason(a):
    if a is None:
        return ("This link is not valid", "", "Ask the team for a new link.")
    if a.decision:
        return ("Already approved" if a.decision == "approved" else "Already answered", "ok" if a.decision == "approved" else "warn",
                f"You replied on {a.decided_at.strftime('%-d %b %Y') if a.decided_at else 'an earlier day'}. This link is now closed.")
    if a.expires_at <= now() or a.change.status != "client":
        return ("This link is closed", "", f"Links stay open for {LINK_DAYS} days, until you reply, or until the team sends a newer version. "
                                           "Ask the team for a new link.")
    return None


@bp.get("/a/<token>")
def page(token):
    a = _approval_or_none(token)
    closed = _closed_reason(a)
    if closed:
        return _page(a, done=closed)
    c = a.change
    ask = next((m for m in c.messages if m.sender in ("owner", "developer")), None)
    from app.betas import beta_auth
    beta = c.project.env("beta")
    return _page(a, c=c, p=c.project, beta=beta, ask=ask, token=token, login=beta_auth(beta))


@bp.post("/a/<token>")
def answer(token):
    from app.blueprints.projects import IMAGE_TYPES, MAX_IMAGE_BYTES, _upload_dir
    a = _approval_or_none(token)
    closed = _closed_reason(a)
    if closed:
        return _page(a, done=closed)
    c, p = a.change, a.change.project
    yes = request.form.get("decision") == "approved"
    comment = (request.form.get("comment") or "").strip()[:4000]
    shot = None
    if not yes:
        f = request.files.get("shot")
        if f and f.filename:
            ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "").lower()
            data = f.read(MAX_IMAGE_BYTES + 1)
            if ext not in IMAGE_TYPES or len(data) > MAX_IMAGE_BYTES:
                return _page(a, c=c, p=p, beta=p.env("beta"), token=token, open_no=True, comment=comment,
                             error="The screenshot must be a PNG, JPG, WEBP or GIF picture, up to 5 MB.")
            shot = (ext, data, f.filename[:200])
        if not comment:
            return _page(a, c=c, p=p, beta=p.env("beta"), token=token, open_no=True, error="Please write what should change.")
    a.decision = "approved" if yes else "changes"
    a.decided_at, a.decided_ip, a.comment = now(), client_ip(), comment
    c.status = "approved" if yes else "changes"
    if not yes:
        msg = ChangeMessage(change_id=c.id, sender="client", body=comment)
        db.session.add(msg)
        db.session.flush()
        if shot:
            path = _upload_dir() / f"{secrets.token_hex(16)}.{shot[0]}"
            path.write_bytes(shot[1])
            db.session.add(ChangeAttachment(message_id=msg.id, kind="image", file_path=str(path), original_name=shot[2]))
    log("Client approved" if yes else "Client asked changes", project_id=p.id,
        details=c.title + (f" — {comment}" if comment else ""))
    db.session.commit()
    raise_alert("approval", f"{p.name}: client approved “{c.title}”" if yes else f"{p.name}: client asked for changes on “{c.title}”", p)
    done = (("Thank you — approved", "ok", "The team will put this change live. This link is now closed.") if yes else
            ("Thanks — sent to the team", "warn", "They will make the change and send you a new link."))
    return _page(a, done=done)
