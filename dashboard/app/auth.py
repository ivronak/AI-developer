"""Who may do what (F02), the activity log (F03), and the authenticator app (TOTP) helpers.
Security is enforced here, on the server — hiding a button in a page is never the protection."""
import secrets
import time
from functools import wraps

import pyotp
import qrcode
import qrcode.image.svg
from flask import abort, current_app, has_request_context, request
from flask_login import current_user
from markupsafe import Markup

from app.clock import now
from app.crypto import decrypt_secret, encrypt_secret, sha256
from app.extensions import db, login_manager
from app.models import ActivityLog, Project, ProjectMember, User

ISSUER = "AI Project Dashboard"


# ---------- sessions ----------
@login_manager.user_loader
def load_user(session_id):
    try:
        uid, version = (int(x) for x in str(session_id).split("."))
    except ValueError:
        return None
    user = db.session.get(User, uid)
    if user is None or not user.is_active or user.session_version != version:
        return None
    return user


def owner_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.is_owner:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


# ---------- projects a person may see ----------
def visible_projects_query(user):
    """Owner: every project. Developer: only projects assigned to them (and not expired)."""
    q = Project.query
    if user.is_owner:
        return q
    t = now()
    return q.join(ProjectMember, ProjectMember.project_id == Project.id).filter(
        ProjectMember.user_id == user.id,
        db.or_(ProjectMember.expires_at.is_(None), ProjectMember.expires_at > t))


def can_see_project(user, project_id):
    if user.is_owner:
        return True
    return visible_projects_query(user).filter(Project.id == project_id).first() is not None


def project_or_404(project_id):
    p = db.session.get(Project, project_id)
    if p is None or not can_see_project(current_user, p.id):
        abort(404)
    return p


# ---------- activity log (append-only) ----------
def client_ip():
    if not has_request_context():
        return ""
    return (request.remote_addr or "")[:64]     # behind the web server, wsgi.py's ProxyFix sets this from the proxy's header


def log(action, project_id=None, details="", prompt="", user=None):
    """Append one activity row. Works in requests and in CLI jobs (no user / IP there → "System")."""
    u = user
    if u is None and has_request_context() and current_user and current_user.is_authenticated:
        u = current_user
    db.session.add(ActivityLog(user_id=u.id if u else None, project_id=project_id, action=action,
                               details=details, prompt_text=prompt, ip=client_ip()))


# ---------- authenticator app (TOTP) ----------
def new_totp_secret(user):
    secret = pyotp.random_base32()
    user.totp_secret_enc = encrypt_secret(secret)
    return secret


def totp_uri(user):
    return pyotp.TOTP(decrypt_secret(user.totp_secret_enc)).provisioning_uri(name=user.email, issuer_name=ISSUER)


def totp_qr_svg(user):
    img = qrcode.make(totp_uri(user), image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    svg = img.to_string(encoding="unicode")
    return Markup(svg)


def check_totp(user, code):
    """True if the 6-digit code is right and has not been used before (each 30-second code works once)."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6 or not user.totp_secret_enc:
        return False
    totp = pyotp.TOTP(decrypt_secret(user.totp_secret_enc))
    step_now = int(time.time()) // 30
    for offset in (-1, 0, 1):                       # allow ±30 s clock difference
        step = step_now + offset
        if step > user.totp_last_step and secrets.compare_digest(totp.at(step * 30), code):
            user.totp_last_step = step
            return True
    return False


def new_recovery_codes(user):
    """8 one-time backup codes for a lost phone. Shown once; only hashes are stored."""
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(8)]
    user.recovery_codes = [sha256(c) for c in codes]
    return codes


def use_recovery_code(user, code):
    h = sha256((code or "").strip().lower())
    if h in (user.recovery_codes or []):
        user.recovery_codes = [x for x in user.recovery_codes if x != h]
        return True
    return False


def reset_totp(user):
    user.totp_secret_enc = ""
    user.totp_enabled = False
    user.totp_last_step = 0
    user.recovery_codes = []


# ---------- one-time "set your password" link ----------
def new_setup_link(user):
    from datetime import timedelta
    token = secrets.token_urlsafe(32)
    user.setup_token_hash = sha256(token)
    user.setup_token_expires = now() + timedelta(hours=current_app.config["SETUP_LINK_HOURS"])
    return token


def user_by_setup_token(token):
    if not token:
        return None
    u = User.query.filter_by(setup_token_hash=sha256(token)).first()
    if u is None or not u.setup_token_expires or u.setup_token_expires < now():
        return None
    return u
