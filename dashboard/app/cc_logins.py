"""Claude accounts with a web login (owner, 2026-11-04) — the only way Claude Code logs in now (the setup-token is retired).

How a login works: the owner adds an account with a name and presses Log in. The dashboard starts
`claude auth login --claudeai` as the server user (TEST_RUNNER_USER) with that account's own config folder
(CLAUDE_CONFIG_DIR = <runner home>/claude-accounts/a<id>). Claude Code prints a claude.com link and waits for a code; the
owner opens the link in his own browser, signs in, copies the code and pastes it into Settings; the dashboard hands the code
to the waiting process. The login (with its refresh token) then lives in that folder and renews itself — the dashboard
never sees or stores it. Slug "" is the server user's own default login (~/.claude), made by hand before this existed.

Every account's `projects/` folder points at the one shared ~/.claude/projects, so a project's Claude session goes on
whichever account a message picks. Staying logged in: the 5-minute tick checks each account about once an hour (no model
call) and, when the access token has run out without a run renewing it, makes one tiny Haiku call so Claude Code renews it.
A logged-out account raises one alert. Tests replace _run / _popen — they never start the real CLI."""
import json
import os
import pwd
import re
import subprocess
import threading
import time
from datetime import datetime

from flask import current_app

from app.clock import now
from app.extensions import db
from app.models import ClaudeAccount

ACCOUNTS_DIR = "claude-accounts"
SLUG_RE = re.compile(r"^a[0-9]{1,6}$")
NAME_RE = re.compile(r"^[^\x00-\x1f<>]{1,60}$")
URL_RE = re.compile(r"https://[^\s\"'<>]+")
LOGIN_HOSTS = ("claude.com", "claude.ai", "platform.claude.com", "console.anthropic.com")
CODE_RE = re.compile(r"^[A-Za-z0-9#_.~-]{8,600}$")
LOGIN_TTL = 15 * 60               # a started login waits this long for the code
CHECK_EVERY = 55 * 60             # the tick checks an account about once an hour
RENEW_BEFORE = 30 * 60            # keep-alive when the access token has less than this left (and nothing renewed it)
TOKEN_MIN_LEFT = 45 * 60          # a beta website's run gets the access token only with this much time left (a run is ≤ 30 min)
INIT_REQ = json.dumps({"type": "control_request", "request_id": "models", "request": {"subtype": "initialize"}}) + "\n"

_LOGINS = {}                      # account id → {"proc", "url", "started", "out"} — one gunicorn worker, so this is shared
_LOCK = threading.Lock()


class LoginError(Exception):
    """Something the owner should read (shown as a flash)."""


# ---------- running things as the server user (tests replace these two) ----------
def _run(args, **kw):
    return subprocess.run(args, **kw)


def _popen(args, **kw):
    return subprocess.Popen(args, **kw)


def _runner():
    return current_app.config.get("TEST_RUNNER_USER") or ""


def _bin():
    return current_app.config.get("CLAUDE_CODE_BIN") or "claude"


def _home():
    r = _runner()
    if r:
        return pwd.getpwnam(r).pw_dir
    return os.path.join(current_app.instance_path, "claude-home")        # local dev without a runner user


def config_dir(acc):
    """The account's Claude Code config folder, or None for the server user's own default login."""
    if not acc.slug:
        return None
    if not SLUG_RE.match(acc.slug):
        raise LoginError("This account's folder name is not valid.")
    return os.path.join(_home(), ACCOUNTS_DIR, acc.slug)


def creds_file(acc):
    d = config_dir(acc)
    return os.path.join(d, ".credentials.json") if d else os.path.join(_home() if _runner() else os.path.expanduser("~"), ".claude", ".credentials.json")


def login_parts(acc):
    """(extra Popen env, extra `KEY=value` arguments for /usr/bin/env inside the sudo wrapper) that make Claude Code use
    this account. With a runner user the folder goes as an argument (sudo keeps only a few variables of our environment)."""
    d = config_dir(acc) if acc else None
    if not d:
        return {}, []
    return ({}, [f"CLAUDE_CONFIG_DIR={d}"]) if _runner() else ({"CLAUDE_CONFIG_DIR": d}, [])


def _env():
    from app.workspace import _env as ws_env
    return dict(ws_env())


def cli(acc, args, umask="077"):
    """(argument list, env) that run `claude <args>` as the server user with this account's login."""
    extra_env, extra_args = login_parts(acc)
    env = dict(_env(), **extra_env)
    r = _runner()
    if r:
        return (["sudo", "-n", "-H", "-u", r, "--", "/usr/bin/env", "PATH=/usr/local/bin:/usr/bin:/bin", "BROWSER=/bin/true",
                 *extra_args, "sh", "-c", f'umask {umask}; PATH="$HOME/.local/bin:$PATH"; exec "$@"', "sh", _bin(), *args], env)
    env["BROWSER"] = "/bin/true"
    return [_bin(), *args], env


def _as_runner(script, *params, timeout=30):
    """A fixed shell script as the server user (params are positional arguments, never pasted into the script)."""
    r = _runner()
    args = (["sudo", "-n", "-H", "-u", r, "--", "/bin/sh", "-c", script, "sh", *params] if r else ["/bin/sh", "-c", script, "sh", *params])
    return _run(args, env=_env(), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


def ensure_dir(acc):
    """Make the account's folder (owner-only) with its projects/ pointing at the shared sessions folder."""
    d = config_dir(acc)
    if not d:
        return
    r = _as_runner('set -e; umask 077; mkdir -p "$HOME/.claude/projects" "$1"; '
                   '[ -e "$1/projects" ] || ln -s "$HOME/.claude/projects" "$1/projects"', d)
    if r.returncode != 0:
        raise LoginError("The account's folder could not be made on the server: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1][:150])


# ---------- picking an account ----------
def accounts():
    return ClaudeAccount.query.order_by(ClaudeAccount.is_default.desc(), ClaudeAccount.name).all()


def default_account():
    q = ClaudeAccount.query
    return (q.filter_by(is_default=True).first() or q.filter_by(status="ok").order_by(ClaudeAccount.id).first()
            or q.order_by(ClaudeAccount.id).first())


def pick(account_id):
    """The account a change asked for, or the default one when it is gone / not given."""
    acc = db.session.get(ClaudeAccount, account_id) if account_id else None
    return acc or default_account()


def models_for(acc):
    """The account's own model list (as read from its login), else the server-wide list of the hourly job."""
    from app import cc_models
    if acc is not None and acc.models:
        return acc.models
    cat = cc_models.load()
    return cat["models"] if cat["ok"] else []


# ---------- the web login ----------
def _clean_url(line):
    for m in URL_RE.finditer(line or ""):
        url = m.group(0)
        host = url[8:].split("/", 1)[0].split(":", 1)[0].lower()
        if any(host == h or host.endswith("." + h) for h in LOGIN_HOSTS):
            return url
    return ""


def _reap():
    """Forget logins that waited too long (their process is stopped)."""
    with _LOCK:
        for aid, e in list(_LOGINS.items()):
            if time.time() - e["started"] > LOGIN_TTL or e["proc"].poll() is not None:
                _stop(e)
                _LOGINS.pop(aid, None)


def _stop(e):
    try:
        if e["proc"].poll() is None:
            e["proc"].kill()
    except OSError:
        pass


def cancel_login(account_id):
    with _LOCK:
        e = _LOGINS.pop(account_id, None)
    if e:
        _stop(e)


def pending(account_id):
    """The login link of a login that waits for its code, or "" ."""
    _reap()
    e = _LOGINS.get(account_id)
    return e["url"] if e else ""


def start_login(acc, wait=20):
    """Start `claude auth login` for this account; returns the link the owner opens in his browser."""
    cancel_login(acc.id)
    ensure_dir(acc)
    args, env = cli(acc, ["auth", "login", "--claudeai"])
    try:
        proc = _popen(args, cwd="/", env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                      text=True, bufsize=1)
    except OSError as e:
        raise LoginError(f"Claude Code could not be started ({e.__class__.__name__}).")
    entry = {"proc": proc, "url": "", "started": time.time(), "out": []}
    got = threading.Event()

    def read():
        for line in proc.stdout:
            entry["out"] = (entry["out"] + [line.rstrip()[:300]])[-40:]
            if not entry["url"]:
                entry["url"] = _clean_url(line)
                if entry["url"]:
                    got.set()
        got.set()
    threading.Thread(target=read, daemon=True).start()
    got.wait(wait)
    if not entry["url"]:
        _stop(entry)
        last = next((ln for ln in reversed(entry["out"]) if ln.strip()), "no answer")
        raise LoginError(f"Claude Code did not give a login link: {last[:150]}")
    with _LOCK:
        _LOGINS[acc.id] = entry
    return entry["url"]


def finish_login(acc, code, wait=90):
    """Hand the code from the login page to the waiting `claude auth login`, then check the account."""
    _reap()
    with _LOCK:
        e = _LOGINS.pop(acc.id, None)
    if e is None:
        raise LoginError("This login has expired (it waits 15 minutes). Press “Log in” again.")
    code = (code or "").strip()
    if not CODE_RE.match(code):
        _stop(e)
        raise LoginError("That doesn't look like the code from the login page. Press “Log in” again and copy the whole code.")
    try:
        e["proc"].stdin.write(code + "\n")
        e["proc"].stdin.flush()
        e["proc"].stdin.close()
        e["proc"].wait(timeout=wait)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        _stop(e)
    check(acc)
    if acc.status != "ok":
        last = next((ln for ln in reversed(e["out"]) if ln.strip() and "http" not in ln), "")
        raise LoginError("Claude did not accept the code" + (f": {last[:150]}" if last else ".") + " Press “Log in” again.")
    acc.alerted = False
    db.session.commit()


def logout(acc):
    """Sign the account out on the server and delete its folder (the default login is only signed out)."""
    cancel_login(acc.id)
    args, env = cli(acc, ["auth", "logout"])
    try:
        _run(args, cwd="/", env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass
    d = config_dir(acc)
    if d:
        _as_runner('case "$1" in */' + ACCOUNTS_DIR + '/a[0-9]*) rm -rf -- "$1" ;; esac', d)


# ---------- checks, models, staying logged in ----------
def _mask(email):
    from app.cc_accounts import mask_email
    return mask_email(email)


def _expiry(acc):
    """When the account's access token runs out (naive UTC), or None. Only that one number is read from the login file."""
    r = _as_runner('[ -f "$1" ] || exit 0; exec python3 -c \'import json,sys; '
                   'print((json.load(open(sys.argv[1])).get("claudeAiOauth") or {}).get("expiresAt") or "")\' "$1"', creds_file(acc))
    try:
        ms = int((r.stdout or "").strip() or 0)
    except ValueError:
        return None
    return datetime.utcfromtimestamp(ms / 1000) if ms else None


def fetch_models(acc):
    """The account's own model list — the same `initialize` answer the /model picker uses. No model call, no usage."""
    from app import cc_models
    args, env = cli(acc, ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                          "--no-session-persistence"])
    try:
        r = _run(args, cwd="/", env=env, input=INIT_REQ, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in (r.stdout or "").splitlines():
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "control_response" and (ev.get("response") or {}).get("subtype") == "success":
            raw = (ev["response"].get("response") or {}).get("models") or []
            got = [c for c in (cc_models._clean(m) for m in raw) if c]
            return got or None
    return None


def check(acc, with_models=True):
    """Ask this account's Claude Code who is logged in (no model call), then read its models, connectors and token expiry.
    Saves the answer on the account and commits. Never raises."""
    from app.cc_accounts import parse_mcp
    acc.checked_at = now()
    try:
        args, env = cli(acc, ["auth", "status"])
        r = _run(args, cwd="/", env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        try:
            st = json.loads(r.stdout)
        except ValueError:
            st = None
        if not isinstance(st, dict) or "loggedIn" not in st:
            acc.status = "error" if acc.status != "logged_out" else acc.status
            acc.last_error = ((r.stdout + r.stderr).strip().splitlines() or ["no answer"])[-1][:200]
        elif not st.get("loggedIn"):
            acc.status, acc.last_error = "logged_out", ""
        else:
            acc.status, acc.last_error = "ok", ""
            acc.plan = str(st.get("subscriptionType") or "")[:30]
            acc.email_hint = _mask(st.get("email"))
            acc.expires_at = _expiry(acc)
            if with_models:
                models = fetch_models(acc)
                if models:
                    acc.models = models
                m_args, m_env = cli(acc, ["mcp", "list"])
                m = _run(m_args, cwd="/", env=m_env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
                parsed = parse_mcp(m.stdout)
                acc.connectors = parsed if parsed or "No MCP servers" in (m.stdout or "") else acc.connectors
    except (OSError, subprocess.TimeoutExpired, ValueError, LoginError) as e:
        acc.status = "error" if acc.status != "logged_out" else acc.status
        acc.last_error = f"{e.__class__.__name__}: could not ask Claude Code"[:200]
    db.session.commit()
    return acc


def keep_alive(acc):
    """One tiny Haiku call, so Claude Code renews the account's access token itself (nothing else renews an unused login)."""
    args, env = cli(acc, ["-p", "Reply with the word OK.", "--model", "haiku", "--max-turns", "1", "--output-format", "json",
                          "--no-session-persistence"])
    try:
        r = _run(args, cwd="/", env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def access_token(acc):
    """The account's current access token — ONLY for a run as a beta website's own Linux user, which can't read the account's
    folder (owner's choice, 2026-11-04). Renewed first when it has less than 45 minutes left. Kept in memory only."""
    from app.claude_code import CCError

    def read():
        r = _as_runner('[ -f "$1" ] || exit 3; exec python3 -c \'import json,sys; o=json.load(open(sys.argv[1])).get("claudeAiOauth") or {}; '
                       'print(o.get("expiresAt") or 0); print(o.get("accessToken") or "")\' "$1"', creds_file(acc))
        lines = (r.stdout or "").splitlines()
        if r.returncode != 0 or len(lines) < 2 or not lines[1].startswith("sk-ant-"):
            return None, 0
        try:
            return lines[1].strip(), int(lines[0] or 0) / 1000
        except ValueError:
            return None, 0
    token, exp = read()
    if token and exp - time.time() < TOKEN_MIN_LEFT:
        keep_alive(acc)
        token, exp = read()
    if not token or exp - time.time() < TOKEN_MIN_LEFT:
        raise CCError(f"The Claude account “{acc.name}” could not hand over a fresh login. Check it in Settings → Claude accounts.")
    return token


_usage_cache = {}


def usage(acc):
    """The account's plan usage, as Claude Code's /usage shows it: {session, session_resets, week, week_resets} (percent used,
    ISO reset times) or None when it can't be read. Asked at most once a minute per account."""
    import urllib.request
    hit = _usage_cache.get(acc.id)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    out = None
    try:
        req = urllib.request.Request("https://api.anthropic.com/api/oauth/usage", headers={
            "Authorization": "Bearer " + access_token(acc), "anthropic-beta": "oauth-2025-04-20", "User-Agent": "claude-code/2.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
        five, week = d.get("five_hour") or {}, d.get("seven_day") or {}
        out = {"session": five.get("utilization"), "session_resets": five.get("resets_at"),
               "week": week.get("utilization"), "week_resets": week.get("resets_at")}
    except Exception as e:                    # no token, offline, endpoint changed — the chip just stays hidden
        current_app.logger.info("claude usage not read for %s: %s", acc.name, e.__class__.__name__)
    _usage_cache[acc.id] = (time.time(), out)
    return out


def tick():
    """Every 5 minutes (flask monitor): check accounts not checked for an hour, renew a token that ran out, alert once
    when an account is logged out. Never raises."""
    from app.alerts import raise_alert
    done = []
    for acc in ClaudeAccount.query.order_by(ClaudeAccount.id).all():
        try:
            if acc.checked_at and (now() - acc.checked_at).total_seconds() < CHECK_EVERY:
                continue
            check(acc)
            if acc.status == "ok" and acc.expires_at and (acc.expires_at - now()).total_seconds() < RENEW_BEFORE:
                keep_alive(acc)
                acc.expires_at = _expiry(acc)
                db.session.commit()
            if acc.status == "logged_out" and not acc.alerted:
                acc.alerted = True
                db.session.commit()
                raise_alert("claude_login", f"Claude account “{acc.name}” is logged out — Settings → Claude accounts → Log in.")
            elif acc.status == "ok" and acc.alerted:
                acc.alerted = False
                db.session.commit()
            done.append(f"{acc.name}: {acc.status}")
        except Exception as e:          # one account must never stop the monitor
            db.session.rollback()
            current_app.logger.error("claude account check failed: %s", e)
    return done


def expires_in(acc):
    if not acc.expires_at:
        return None
    return acc.expires_at - now()


def fmt_left(delta):
    if delta is None:
        return "—"
    s = int(delta.total_seconds())
    if s <= 0:
        return "renews by itself on the next run"
    return "renews in " + (f"{s // 3600} h {s % 3600 // 60} min" if s >= 3600 else f"{s // 60} min")

