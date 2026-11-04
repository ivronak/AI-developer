"""Settings (Owner only). Step 5: the GitHub connection — account / organization, token (encrypted), test, sync now."""
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import login_required

from app import cc_accounts, cc_logins, cc_models, code_guard, reminders, rootd, telegram
from app.alerts import raise_alert
from app.auth import log, owner_required
from app.crypto import decrypt_secret, encrypt_secret
from app.extensions import db
from app.github import GitHubError, check_token, sync
from app.ai_models import MODELS, PRESETS, preset
from app.models import AiUsage, ClaudeAccount, Project, Server
from app.models.setting import get_setting, set_setting
from app.monitor import run_all

bp = Blueprint("settings", __name__)


def _mask(token):
    return f"•••• {token[-4:]}" if token else ""


def _public_key():
    from app import remote
    try:
        return remote.ensure_key()
    except Exception:            # ssh-keygen missing — the card says so
        return ""


@bp.get("/settings")
@login_required
@owner_required
def index():
    token = decrypt_secret(get_setting("github_token_enc") or "")
    new_projects = Project.query.filter_by(needs_setup=True).order_by(Project.created_at.desc()).all()
    missing = Project.query.filter_by(repo_missing=True).order_by(Project.name).all()
    tg_token = decrypt_secret(get_setting("telegram_token_enc") or "")
    watched = Project.query.filter_by(monitor_enabled=True, is_active=True).order_by(Project.name).all()
    from app.clock import now
    t = now()
    month_start = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_cost = db.session.query(db.func.coalesce(db.func.sum(AiUsage.cost_usd), 0)).filter(AiUsage.created_at >= month_start).scalar()
    return render_template("settings/index.html", page="settings", title="Settings",
                           gh_owner=get_setting("github_owner"), gh_type=get_setting("github_owner_type"),
                           gh_token_mask=_mask(token), gh_last=get_setting("github_last_sync"),
                           gh_result=get_setting("github_last_result"), new_projects=new_projects, missing=missing,
                           tg_token_mask=_mask(tg_token), tg_chats=get_setting("telegram_chats") or [],
                           tg_code=get_setting("telegram_connect_code"), watched=watched,
                           mon_last=get_setting("monitor_last_run"),
                           ai_key_mask=_mask(decrypt_secret(get_setting("claude_key_enc") or "")), ai_preset=get_setting("claude_preset"),
                           ai_max_usd=get_setting("claude_max_usd"), ai_max_turns=get_setting("claude_max_turns"),
                           MODELS=MODELS, PRESETS=PRESETS,
                           month_cost=float(month_cost or 0), ai_key_expires=get_setting("claude_key_expires"),
                           expiries=reminders.summary(), servers=Server.query.order_by(Server.name).all(),
                           ssh_public_key=_public_key(),
                           cc_last=get_setting("cc_last_test") or "",
                           CC=cc_models.load(), cc_default=cc_models.default_choice(), cc_levels=cc_models.LEVELS,
                           cc_credit_models=cc_models.needs_credits(), cc_accounts=_account_rows(),
                           cc_default_models=cc_logins.models_for(cc_logins.default_account()),
                           code_guard=code_guard.load(), rootd_state=rootd.status(), rootd_runner_ok=_runner_is_separate(),
                           cm_token_mask=_mask(decrypt_secret(get_setting("codemagic_token_enc") or "")),
                           cm_last=get_setting("codemagic_last_test") or "",
                           cc_projects=Project.query.filter_by(ai_engine="claude_code").order_by(Project.name).all())


def _runner_is_separate():
    from app import beta_tools
    return beta_tools._runner_is_separate()


@bp.post("/settings/github")
@login_required
@owner_required
def save_github():
    owner = (request.form.get("owner") or "").strip().strip("/")
    kind = "org" if request.form.get("owner_type") == "org" else "user"
    if owner and not all(c.isalnum() or c == "-" for c in owner):
        flash("A GitHub name has only letters, numbers and dashes.", "bad")
        return redirect(url_for("settings.index"))
    set_setting("github_owner", owner)
    set_setting("github_owner_type", kind)
    token = (request.form.get("token") or "").strip()
    if request.form.get("clear_token") == "1":
        set_setting("github_token_enc", "")
        log("Settings", details="GitHub token removed")
    elif token:
        set_setting("github_token_enc", encrypt_secret(token))
        log("Settings", details="GitHub token replaced")
    log("Settings", details=f"GitHub source: {owner or '—'} ({'organization' if kind == 'org' else 'account'})")
    db.session.commit()
    flash("GitHub settings saved.", "ok")
    return redirect(url_for("settings.index"))


@bp.post("/settings/github/test")
@login_required
@owner_required
def test_github():
    token = decrypt_secret(get_setting("github_token_enc") or "")
    try:
        if token:
            who = check_token(token)
            flash(f"Token works — it belongs to GitHub user “{who}”.", "ok")
        else:
            flash("No token saved: only public repos can be read. Save a token for private repos.", "bad")
    except GitHubError as e:
        flash(str(e), "bad")
    return redirect(url_for("settings.index"))


@bp.post("/settings/github/sync")
@login_required
@owner_required
def sync_now():
    try:
        new, missing, total = sync()
        msg = f"Synced {total} repo{'s' if total != 1 else ''}: {len(new)} new project{'s' if len(new) != 1 else ''}"
        flash(msg + (f", {len(missing)} with a missing repo." if missing else "."), "ok")
    except GitHubError as e:
        flash(str(e), "bad")
    return redirect(url_for("settings.index"))


# ---------- Telegram alerts (F14) ----------
@bp.post("/settings/telegram")
@login_required
@owner_required
def save_telegram():
    token = (request.form.get("token") or "").strip()
    if request.form.get("clear_token") == "1":
        set_setting("telegram_token_enc", "")
        set_setting("telegram_chats", [])
        log("Settings", details="Telegram bot removed")
        db.session.commit()
        flash("Telegram bot removed.", "ok")
        return redirect(url_for("settings.index") + "#telegram")
    if not token:
        flash("Paste the bot token from @BotFather.", "bad")
        return redirect(url_for("settings.index") + "#telegram")
    try:
        name = telegram.bot_name(token)
    except telegram.TelegramError as e:
        flash(str(e), "bad")
        return redirect(url_for("settings.index") + "#telegram")
    set_setting("telegram_token_enc", encrypt_secret(token))
    log("Settings", details=f"Telegram bot set: @{name}")
    db.session.commit()
    flash(f"Bot @{name} saved. Next: connect your Telegram chat.", "ok")
    return redirect(url_for("settings.index") + "#telegram")


@bp.post("/settings/telegram/code")
@login_required
@owner_required
def telegram_code():
    telegram.new_connect_code()
    db.session.commit()
    return redirect(url_for("settings.index") + "#telegram")


@bp.post("/settings/telegram/connect")
@login_required
@owner_required
def telegram_connect():
    try:
        chat = telegram.find_connect_chat()
    except telegram.TelegramError as e:
        flash(str(e), "bad")
        return redirect(url_for("settings.index") + "#telegram")
    if chat is None:
        flash("The code was not found yet. Send it to your bot in Telegram, wait a few seconds, then try again.", "bad")
    else:
        log("Settings", details=f"Telegram chat connected: {chat['name']}")
        db.session.commit()
        telegram.send(f"✅ Connected. Alerts from the AI Project Dashboard will come here.")
        flash(f"Connected: {chat['name']}. A test message was sent.", "ok")
    return redirect(url_for("settings.index") + "#telegram")


@bp.post("/settings/telegram/remove/<int:chat_id>")
@login_required
@owner_required
def telegram_remove(chat_id):
    chats = [c for c in (get_setting("telegram_chats") or []) if c["id"] != chat_id]
    set_setting("telegram_chats", chats)
    log("Settings", details="Telegram chat removed")
    db.session.commit()
    flash("Chat removed.", "ok")
    return redirect(url_for("settings.index") + "#telegram")


@bp.post("/settings/telegram/test")
@login_required
@owner_required
def telegram_test():
    raise_alert("test", "Test alert — if you can read this, alerts work.", link=False)
    flash("Test alert sent (it also shows under the bell).", "ok")
    return redirect(url_for("settings.index") + "#telegram")


# ---------- monitor (F15) ----------
@bp.post("/settings/monitor/run")
@login_required
@owner_required
def monitor_run():
    res = run_all()
    if not res:
        flash("No site is being watched yet. Tick “Watch this site” in a project's Settings tab.", "bad")
    else:
        flash("Checked: " + ", ".join(f"{k} — {v}" for k, v in res.items()), "ok")
    return redirect(url_for("settings.index") + "#monitor")


# ---------- Claude (F06) ----------
@bp.post("/settings/claude")
@login_required
@owner_required
def save_claude():
    import anthropic
    f = request.form
    pick = f.get("preset") if f.get("preset") in PRESETS else "normal"
    model = preset(pick)[2]
    try:
        max_usd = max(0.5, min(50.0, float(f.get("max_usd") or 3)))
        max_turns = max(5, min(150, int(f.get("max_turns") or 40)))
    except ValueError:
        flash("Limits must be numbers.", "bad")
        return redirect(url_for("settings.index") + "#claude")
    key = (f.get("key") or "").strip()
    if key:
        try:
            anthropic.Anthropic(api_key=key, max_retries=1, timeout=20.0).models.retrieve(model)
        except anthropic.AuthenticationError:
            flash("Anthropic refused this API key. Copy it again from console.anthropic.com.", "bad")
            return redirect(url_for("settings.index") + "#claude")
        except anthropic.NotFoundError:
            flash("The key works, but this model is not available on your account. Pick another model.", "bad")
            return redirect(url_for("settings.index") + "#claude")
        except anthropic.APIError as e:
            flash(f"Could not check the key with Anthropic ({e.__class__.__name__}). Try again.", "bad")
            return redirect(url_for("settings.index") + "#claude")
        set_setting("claude_key_enc", encrypt_secret(key))
        log("Settings", details="Claude API key replaced")
    exp = (f.get("key_expires") or "").strip()
    if exp:
        from datetime import datetime as _dt
        try:
            _dt.strptime(exp, "%Y-%m-%d")
        except ValueError:
            flash("The expiry date is not a valid date.", "bad")
            return redirect(url_for("settings.index") + "#claude")
    if exp != (get_setting("claude_key_expires") or ""):
        set_setting("claude_key_expires", exp)
    set_setting("claude_preset", pick)
    set_setting("claude_max_usd", max_usd)
    set_setting("claude_max_turns", max_turns)
    log("Settings", details=f"Claude: default {pick} ({model}), limit ${max_usd:.2f} / {max_turns} steps per run")
    db.session.commit()
    flash("Claude settings saved.", "ok")
    return redirect(url_for("settings.index") + "#claude")


# ---------- servers (F07 / F08) ----------
@bp.post("/settings/servers")
@login_required
@owner_required
def server_add():
    from app import remote
    f = request.form
    name, host, user = (f.get("name") or "").strip()[:80], (f.get("host") or "").strip(), (f.get("user") or "").strip()
    try:
        port = int(f.get("port") or 22)
    except ValueError:
        port = 0
    err = (not name and "Give the server a short name.") or remote.validate(host, user, port)
    if err:
        flash(err, "bad")
        return redirect(url_for("settings.index") + "#servers")
    s = Server(name=name, host=host, port=port, ssh_user=user)
    db.session.add(s)
    log("Settings", details=f"Server added: {name} ({user}@{host}:{port})")
    db.session.commit()
    flash(f"Server “{name}” added. Add the dashboard's key on it, then press Test.", "ok")
    return redirect(url_for("settings.index") + "#servers")


@bp.post("/settings/servers/<int:server_id>/test")
@login_required
@owner_required
def server_test(server_id):
    """First test pins the server's SSH host key; later tests refuse a server whose key changed."""
    from app import remote
    from app.clock import now
    s = db.session.get(Server, server_id) or abort(404)
    try:
        if not s.host_key:
            s.host_key = remote.keyscan(s)
            log("Settings", details=f"Server {s.name}: SSH host key saved")
        code, out = remote.run(s, "echo connected-ok; uname -sr; whoami", timeout=60)
        if code != 0 or "connected-ok" not in out:
            raise remote.RemoteError(out.splitlines()[-1][:150] if out else "No answer.")
        s.last_ok_at, s.last_result = now(), "OK · " + " · ".join(out.splitlines()[1:3])[:200]
        flash(f"Connected to {s.name}.", "ok")
    except remote.RemoteError as e:
        s.last_result = str(e)[:255]
        flash(f"{s.name}: {e}", "bad")
    db.session.commit()
    return redirect(url_for("settings.index") + "#servers")


@bp.post("/settings/servers/<int:server_id>/forget-key")
@login_required
@owner_required
def server_forget_key(server_id):
    s = db.session.get(Server, server_id) or abort(404)
    s.host_key, s.last_ok_at, s.last_result = "", None, "Host key forgotten — press Test to save the new one."
    log("Settings", details=f"Server {s.name}: SSH host key forgotten")
    db.session.commit()
    flash("Host key forgotten. Only do this if you know the server was rebuilt or moved.", "ok")
    return redirect(url_for("settings.index") + "#servers")


@bp.post("/settings/servers/<int:server_id>/delete")
@login_required
@owner_required
def server_delete(server_id):
    from app.models import Environment
    s = db.session.get(Server, server_id) or abort(404)
    used = Environment.query.filter_by(server_id=s.id).count()
    if used:
        flash(f"“{s.name}” is still used by {used} beta / live site{'s' if used != 1 else ''}. Change those projects first.", "bad")
        return redirect(url_for("settings.index") + "#servers")
    log("Settings", details=f"Server removed: {s.name}")
    db.session.delete(s)
    db.session.commit()
    flash("Server removed.", "ok")
    return redirect(url_for("settings.index") + "#servers")


# ---------- Claude Code engine (2026-10-16) ----------
@bp.get("/settings/claude")
@login_required
@owner_required
def claude_accounts():
    """Every Claude that can work for the dashboard: which login, and what each can do / asks first / can't."""
    from app.clock import now
    t = now()
    month_start = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_cost = db.session.query(db.func.coalesce(db.func.sum(AiUsage.cost_usd), 0)).filter(AiUsage.created_at >= month_start).scalar()
    data = cc_accounts.build(month_cost=month_cost or 0, api_projects=Project.query.filter_by(ai_engine="api").count())
    return render_template("settings/claude.html", page="settings", title="Claude accounts", accounts=data["accounts"])


def _account_rows():
    """Every Claude account for the Settings card, with a waiting login's link."""
    rows = []
    for a in cc_logins.accounts():
        rows.append({"a": a, "login_url": cc_logins.pending(a.id), "left": cc_logins.fmt_left(cc_logins.expires_in(a)),
                     "models": len(a.models or [])})
    return rows


def _acc_or_404(account_id):
    return db.session.get(ClaudeAccount, account_id) or abort(404)


def _back(acc=None):
    return redirect(url_for("settings.index") + (f"#acc-{acc.id}" if acc else "#claude-code"))


@bp.post("/settings/claude-accounts")
@login_required
@owner_required
def account_add():
    """A new Claude account (only a name); logging it in is the next button."""
    name = " ".join((request.form.get("name") or "").split())
    if not cc_logins.NAME_RE.match(name):
        flash("Give the account a short name (up to 60 characters).", "bad")
        return _back()
    if ClaudeAccount.query.filter(db.func.lower(ClaudeAccount.name) == name.lower()).first():
        flash("An account with that name already exists.", "bad")
        return _back()
    first = ClaudeAccount.query.count() == 0
    acc = ClaudeAccount(name=name, slug="", is_default=first)
    db.session.add(acc)
    db.session.flush()
    acc.slug = f"a{acc.id}"
    log("Settings", details=f"Claude account added: {name}")
    db.session.commit()
    try:
        url = cc_logins.start_login(acc)
        log("Settings", details=f"Claude account login started: {name}")
        db.session.commit()
        flash(f"“{name}” added. Open the login page, sign in with that Claude account, then paste the code below.", "ok")
    except cc_logins.LoginError as e:
        flash(f"“{name}” added, but the login could not start: {e}", "bad")
    return _back(acc)


@bp.post("/settings/claude-accounts/<int:account_id>/login")
@login_required
@owner_required
def account_login(account_id):
    acc = _acc_or_404(account_id)
    try:
        cc_logins.start_login(acc)
        log("Settings", details=f"Claude account login started: {acc.name}")
        db.session.commit()
        flash("Open the login page, sign in with this Claude account, then paste the code it shows.", "ok")
    except cc_logins.LoginError as e:
        flash(str(e), "bad")
    return _back(acc)


@bp.post("/settings/claude-accounts/<int:account_id>/code")
@login_required
@owner_required
def account_code(account_id):
    acc = _acc_or_404(account_id)
    try:
        cc_logins.finish_login(acc, request.form.get("code"))
        log("Settings", details=f"Claude account logged in: {acc.name} ({acc.plan or 'plan not shown'})")
        db.session.commit()
        flash(f"“{acc.name}” is logged in ({(acc.plan or 'plan not shown').title()}, {len(acc.models or [])} models). "
              "It stays logged in — the dashboard renews it by itself.", "ok")
    except cc_logins.LoginError as e:
        log("Settings", details=f"Claude account login failed: {acc.name}")
        db.session.commit()
        flash(str(e), "bad")
    return _back(acc)


@bp.post("/settings/claude-accounts/<int:account_id>/cancel")
@login_required
@owner_required
def account_cancel(account_id):
    acc = _acc_or_404(account_id)
    cc_logins.cancel_login(acc.id)
    flash("Login stopped.", "ok")
    return _back(acc)


@bp.post("/settings/claude-accounts/<int:account_id>/check")
@login_required
@owner_required
def account_check(account_id):
    """Who is logged in, its models and connectors (a few seconds, no model call)."""
    acc = cc_logins.check(_acc_or_404(account_id))
    if acc.status == "ok":
        flash(f"“{acc.name}” works ({(acc.plan or 'plan not shown').title()}, {len(acc.models or [])} models).", "ok")
    elif acc.status == "logged_out":
        flash(f"“{acc.name}” is not logged in. Press “Log in”.", "bad")
    else:
        flash(f"Claude Code could not be asked: {acc.last_error or 'no answer'}", "bad")
    return _back(acc)


@bp.post("/settings/claude-accounts/<int:account_id>/default")
@login_required
@owner_required
def account_default(account_id):
    acc = _acc_or_404(account_id)
    ClaudeAccount.query.update({"is_default": False})
    acc.is_default = True
    log("Settings", details=f"Default Claude account: {acc.name}")
    db.session.commit()
    flash(f"“{acc.name}” is now the default account (chat, beta and release runs).", "ok")
    return _back(acc)


@bp.post("/settings/claude-accounts/<int:account_id>/rename")
@login_required
@owner_required
def account_rename(account_id):
    acc = _acc_or_404(account_id)
    name = " ".join((request.form.get("name") or "").split())
    if not cc_logins.NAME_RE.match(name):
        flash("Give the account a short name (up to 60 characters).", "bad")
        return _back(acc)
    if ClaudeAccount.query.filter(db.func.lower(ClaudeAccount.name) == name.lower(), ClaudeAccount.id != acc.id).first():
        flash("An account with that name already exists.", "bad")
        return _back(acc)
    log("Settings", details=f"Claude account renamed: {acc.name} → {name}")
    acc.name = name
    db.session.commit()
    flash("Renamed.", "ok")
    return _back(acc)


@bp.post("/settings/claude-accounts/<int:account_id>/remove")
@login_required
@owner_required
def account_remove(account_id):
    """Sign the account out on the server and forget it. Changes that picked it fall back to the default account."""
    acc = _acc_or_404(account_id)
    if request.form.get("confirm") != "1":
        flash("Tick the box to confirm — nothing was removed.", "bad")
        return _back(acc)
    cc_logins.logout(acc)
    name, was_default = acc.name, acc.is_default
    db.session.delete(acc)
    db.session.flush()
    if was_default:
        nxt = cc_logins.default_account()
        if nxt is not None:
            nxt.is_default = True
    log("Settings", details=f"Claude account removed (logged out on the server): {name}")
    db.session.commit()
    flash(f"“{name}” is logged out on the server and removed. To be sure, also revoke it on claude.ai → Settings.", "ok")
    return _back()


@bp.post("/settings/claude-code/defaults")
@login_required
@owner_required
def save_cc_defaults():
    """The model and effort that are pre-selected in the chat (people can still change them per message)."""
    model, effort = cc_models.resolve(request.form.get("model"), request.form.get("effort"),
                                      models=cc_logins.models_for(cc_logins.default_account()))
    if model is None:
        flash("The model list is not available yet, so nothing was saved.", "bad")
        return redirect(url_for("settings.index") + "#claude-code")
    set_setting("cc_default_model", model)
    set_setting("cc_default_effort", effort)
    log("Settings", details=f"Claude Code default in the chat: {model}" + (f", effort {effort}" if effort else ""))
    db.session.commit()
    flash("Saved. New messages start with this model and effort.", "ok")
    return redirect(url_for("settings.index") + "#claude-code")


@bp.post("/settings/claude-code/test")
@login_required
@owner_required
def test_cc():
    """Ask Claude Code for one word, in an empty folder, as the runner user — proves the CLI, the login and the sudo rule."""
    import subprocess
    import tempfile
    from app import claude_code as cc
    from app.workspace import _env
    try:
        login = cc.login_for()
        auth, plan = login.env, login.plan
    except cc.CCError as e:
        flash(str(e), "bad")
        return redirect(url_for("settings.index") + "#claude-code")
    base = current_app.config.get("WORKSPACE_ROOT") or None
    with tempfile.TemporaryDirectory(dir=base) as d:
        args = cc.command("Reply with exactly the word OK.", "claude-haiku-5-5", cc.new_session_id(), False, 1, 0.05,
                          login_args=login.args)
        args = [a for a in args if a != "--verbose"]
        args[args.index("stream-json")] = "json"
        try:
            r = subprocess.run(args, cwd=d, env=dict(_env(), **auth), capture_output=True, text=True, timeout=120)
            out = (r.stdout or "") + (r.stderr or "")
        except (OSError, subprocess.TimeoutExpired) as e:
            out, r = str(e), None
    if r is not None and r.returncode == 0 and '"is_error":false' in out.replace(" ", ""):
        conns = _cc_connectors(login)
        who = login.account.name if login.account else "API key"
        set_setting("cc_last_test", f"OK · {who} · connectors: {', '.join(conns) if conns else 'none'}"[:250])
        flash(f"Claude Code works ({who}). "
              f"Connectors it can use: {', '.join(conns) if conns else 'none'}.", "ok")
    else:
        last = (out.strip().splitlines() or ["no answer"])[-1][:200]
        set_setting("cc_last_test", f"Failed · {last}")
        flash(f"Claude Code didn't answer: {last}", "bad")
    db.session.commit()
    return redirect(url_for("settings.index") + "#claude-code")


def _cc_connectors(login):
    """The MCP servers / claude.ai connectors (e.g. Mobbin) Claude Code sees as the runner user — `claude mcp list`,
    no model call. Names only."""
    import re
    import subprocess
    from app import claude_code as cc
    from app.workspace import _env
    args = cc.command("x", "claude-haiku-5-5", cc.new_session_id(), False, 1, 0.01, login_args=login.args)
    i = args.index("-p")
    args = args[:i] + ["mcp", "list"]
    try:
        r = subprocess.run(args, env=dict(_env(), **login.env), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    names = []
    for line in (r.stdout or "").splitlines():
        m = re.match(r"^\s*([^:]{1,80}):\s.*(✓|connected|Connected)", line)
        if m:
            names.append(m.group(1).strip())
    return names[:20]


@bp.post("/settings/codemagic")
@login_required
@owner_required
def save_codemagic():
    """Codemagic API token (mobile test builds, CHANGES Step 10) — stored encrypted, never shown again."""
    token = (request.form.get("token") or "").strip()
    if request.form.get("clear") == "1":
        set_setting("codemagic_token_enc", "")
        log("Settings", details="Codemagic token removed")
    elif token:
        if len(token) > 300 or any(ch.isspace() for ch in token) or len(token) < 20:
            flash("That doesn't look like a Codemagic API token (Codemagic → Account settings → API token → Show).", "bad")
            return redirect(url_for("settings.index") + "#codemagic")
        set_setting("codemagic_token_enc", encrypt_secret(token))
        log("Settings", details="Codemagic token replaced")
    else:
        flash("Paste the token first.", "bad")
        return redirect(url_for("settings.index") + "#codemagic")
    db.session.commit()
    flash("Codemagic token saved. Press “Test Codemagic”.", "ok")
    return redirect(url_for("settings.index") + "#codemagic")


@bp.post("/settings/codemagic/test")
@login_required
@owner_required
def test_codemagic():
    from app import codemagic
    try:
        n = codemagic.check()
        set_setting("codemagic_last_test", f"OK · {n} app{'s' if n != 1 else ''} in the account")
        flash(f"Codemagic works ({n} app{'s' if n != 1 else ''} in the account).", "ok")
    except codemagic.CodemagicError as e:
        set_setting("codemagic_last_test", f"Failed · {str(e)[:150]}")
        flash(str(e), "bad")
    db.session.commit()
    return redirect(url_for("settings.index") + "#codemagic")
