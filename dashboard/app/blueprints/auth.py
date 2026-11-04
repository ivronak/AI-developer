"""Sign-in (F01): password → authenticator app code (or first-time setup) · 5 wrong tries = 15-minute lock + owner alert.
Also: set-your-password links, sign out, and "My account" (password, authenticator on/off, recovery codes)."""
from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.alerts import raise_alert
from app.auth import (check_totp, log, new_recovery_codes, new_totp_secret, reset_totp, totp_qr_svg, use_recovery_code,
                      user_by_setup_token)
from app.clock import now
from app.crypto import decrypt_secret
from app.extensions import db
from app.models import User
from app.models.setting import get_setting

bp = Blueprint("auth", __name__)

PENDING_MINUTES = 10
GENERIC_FAIL = "Wrong email/mobile or password."


# ---------- helpers ----------
def _find_user(identifier):
    v = (identifier or "").strip().lower()
    if not v:
        return None
    if "@" in v:
        return User.query.filter(db.func.lower(User.email) == v).first()
    digits = "".join(c for c in v if c.isdigit())
    if len(digits) < 10:
        return None
    for u in User.query.all():
        if "".join(c for c in u.mobile if c.isdigit())[-10:] == digits[-10:]:
            return u
    return None


def _locked_minutes(user):
    if user.locked_until and user.locked_until > now():
        return max(1, int((user.locked_until - now()).total_seconds() // 60) + 1)
    return 0


def _fail(user, why):
    """Count a wrong password / code. At the limit: lock, alert the owner, log it. Returns minutes locked (0 = not)."""
    cfg = current_app.config
    user.failed_logins = (user.failed_logins or 0) + 1
    log("Sign-in failed", details=f"{user.email}: {why}", user=user)
    if user.failed_logins >= cfg["LOGIN_MAX_TRIES"]:
        user.locked_until = now() + timedelta(minutes=cfg["LOGIN_LOCK_MINUTES"])
        user.failed_logins = 0
        log("Sign-in locked", details=f"{user.email}: {cfg['LOGIN_LOCK_MINUTES']} minutes", user=user)
        db.session.commit()
        raise_alert("login_locked", f"Sign-in locked for {user.name} after {cfg['LOGIN_MAX_TRIES']} wrong tries")
    db.session.commit()
    return _locked_minutes(user)


def _set_pending(user, step):
    session["pending"] = {"uid": user.id, "ver": user.session_version, "step": step,
                          "exp": (now() + timedelta(minutes=PENDING_MINUTES)).timestamp()}


def _pending_user(step):
    p = session.get("pending")
    if not p or p.get("step") != step or p.get("exp", 0) < now().timestamp():
        return None
    u = db.session.get(User, p["uid"])
    if u is None or not u.is_active or u.session_version != p["ver"]:
        return None
    return u


def _complete(user, how):
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now()
    session.pop("pending", None)
    session.permanent = True
    login_user(user)
    log("Sign in", details=how, user=user)
    db.session.commit()
    nxt = request.args.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.projects"))


def _render(step, **kw):
    return render_template("auth/login.html", step=step, **kw)


# ---------- sign in ----------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.projects"))
    if request.method == "GET":
        return _render("password")

    ident = request.form.get("login", "")
    user = _find_user(ident)
    if user is not None and _locked_minutes(user):
        return _render("locked", minutes=_locked_minutes(user))
    if user is None or not user.check_password(request.form.get("password", "")):
        if user is not None:
            mins = _fail(user, "wrong password")
            if mins:
                return _render("locked", minutes=mins)
        else:
            log("Sign-in failed", details=f"unknown account: {ident[:80]}")
            db.session.commit()
        return _render("password", error=GENERIC_FAIL, login=ident)
    if not user.is_active:
        return _render("password", error="This account's access is turned off. Ask the owner.", login=ident)

    if user.totp_enabled:
        _set_pending(user, "code")
        return redirect(url_for("auth.login_code"))
    if get_setting("require_2fa"):
        if not user.totp_secret_enc:
            new_totp_secret(user)
            db.session.commit()
        _set_pending(user, "setup")
        return redirect(url_for("auth.login_setup"))
    return _complete(user, "Password (authenticator not required)")


@bp.route("/login/code", methods=["GET", "POST"])
def login_code():
    user = _pending_user("code")
    if user is None:
        return redirect(url_for("auth.login"))
    if request.method == "GET":
        return _render("code")
    if _locked_minutes(user):
        session.pop("pending", None)
        return _render("locked", minutes=_locked_minutes(user))
    code = request.form.get("code", "")
    if request.form.get("use") == "recovery":
        if use_recovery_code(user, code):
            return _complete(user, "Password + recovery code")
    elif check_totp(user, code):
        return _complete(user, "Password + authenticator")
    mins = _fail(user, "wrong code")
    if mins:
        session.pop("pending", None)
        return _render("locked", minutes=mins)
    return _render("code", error="That code is not right. Use the newest code in the app.",
                   recovery=request.form.get("use") == "recovery")


@bp.route("/login/setup", methods=["GET", "POST"])
def login_setup():
    """First sign-in while the authenticator is required: scan the QR, type one code, done."""
    user = _pending_user("setup")
    if user is None:
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        if check_totp(user, request.form.get("code", "")):
            user.totp_enabled = True
            codes = new_recovery_codes(user)
            log("Authenticator on", details="Set up at sign-in", user=user)
            _complete(user, "Password + authenticator (first setup)")
            session["show_codes"] = codes
            return redirect(url_for("auth.recovery_codes"))
        error = "That code is not right. Type the 6 digits the app shows now."
    else:
        error = None
    return _render("setup", qr=totp_qr_svg(user), secret=decrypt_secret(user.totp_secret_enc), error=error)


@bp.post("/logout")
@login_required
def logout():
    log("Sign out")
    db.session.commit()
    logout_user()
    session.clear()
    return redirect(url_for("auth.login"))


# ---------- one-time "set your password" link ----------
@bp.route("/setup/<token>", methods=["GET", "POST"])
def setup_password(token):
    user = user_by_setup_token(token)
    if user is None:
        return render_template("auth/setup_password.html", invalid=True)
    error = None
    if request.method == "POST":
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(pw) < 10:
            error = "Use at least 10 characters."
        elif pw != pw2:
            error = "The two passwords are not the same."
        else:
            user.set_password(pw)
            user.setup_token_hash = None
            user.setup_token_expires = None
            user.session_version += 1
            user.failed_logins = 0
            user.locked_until = None
            log("Password set", details="Set from the one-time link", user=user)
            db.session.commit()
            flash("Password saved. Sign in now.", "ok")
            return redirect(url_for("auth.login"))
    return render_template("auth/setup_password.html", user=user, error=error)


# ---------- my account ----------
@bp.get("/account")
@login_required
def account():
    setup = request.args.get("setup") == "1" and not current_user.totp_enabled and current_user.totp_secret_enc
    return render_template("auth/account.html", page="account", title="My account", crumb="My account",
                           require_2fa=get_setting("require_2fa"),
                           qr=totp_qr_svg(current_user) if setup else None,
                           secret=decrypt_secret(current_user.totp_secret_enc) if setup else None)


@bp.post("/account/password")
@login_required
def account_password():
    u = current_user
    cur, pw, pw2 = request.form.get("current", ""), request.form.get("password", ""), request.form.get("password2", "")
    if not u.check_password(cur):
        flash("Your current password is not right.", "bad")
    elif len(pw) < 10:
        flash("Use at least 10 characters for the new password.", "bad")
    elif pw != pw2:
        flash("The two new passwords are not the same.", "bad")
    else:
        u.set_password(pw)
        u.session_version += 1          # signs out every other device
        log("Password changed")
        db.session.commit()
        login_user(u)                   # keep this device signed in with the new session version
        flash("Password changed. Other devices are signed out.", "ok")
    return redirect(url_for("auth.account"))


@bp.post("/account/2fa/start")
@login_required
def account_2fa_start():
    if not current_user.totp_enabled:
        new_totp_secret(current_user)
        db.session.commit()
    return redirect(url_for("auth.account", setup=1))


@bp.post("/account/2fa/confirm")
@login_required
def account_2fa_confirm():
    u = current_user
    if u.totp_enabled:
        return redirect(url_for("auth.account"))
    if check_totp(u, request.form.get("code", "")):
        u.totp_enabled = True
        session["show_codes"] = new_recovery_codes(u)
        log("Authenticator on", details="Set up from My account")
        db.session.commit()
        return redirect(url_for("auth.recovery_codes"))
    flash("That code is not right. Type the 6 digits the app shows now.", "bad")
    return redirect(url_for("auth.account", setup=1))


@bp.post("/account/2fa/off")
@login_required
def account_2fa_off():
    u = current_user
    if get_setting("require_2fa"):
        flash("The authenticator is required for everyone. The owner can change that on the Users page.", "bad")
    elif not check_totp(u, request.form.get("code", "")):
        flash("Type the current code from your app to turn it off.", "bad")
    else:
        reset_totp(u)
        u.session_version += 1
        log("Authenticator off", details="Turned off from My account")
        db.session.commit()
        login_user(u)
        flash("Authenticator turned off.", "ok")
    return redirect(url_for("auth.account"))


@bp.post("/account/2fa/codes")
@login_required
def account_2fa_codes():
    u = current_user
    if not u.totp_enabled or not check_totp(u, request.form.get("code", "")):
        flash("Type the current code from your app to make new recovery codes.", "bad")
        return redirect(url_for("auth.account"))
    session["show_codes"] = new_recovery_codes(u)
    log("Recovery codes renewed")
    db.session.commit()
    return redirect(url_for("auth.recovery_codes"))


@bp.get("/account/recovery-codes")
@login_required
def recovery_codes():
    codes = session.pop("show_codes", None)
    if not codes:
        return redirect(url_for("auth.account"))
    return render_template("auth/codes.html", page="account", title="Recovery codes", crumb="Recovery codes", codes=codes)
