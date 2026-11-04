"""Automatic beta environments (owner, 2026-10-17): one button (or the first change) gives any project its own beta at
<project>.beta.ivinfotech.com — no hand work, whatever the tech.

Fixed steps (the dashboard, as root over SSH — the same every time):
  website + SSL (CyberPanel) · its own MySQL database + user, filled with a copy of live (live is only read) ·
  the beta branch's code · a beta .env made from live's: own database, beta address, debug off, e-mail to the log,
  payment / cloud / SMS keys blanked (or the project's sandbox lines) · small uploads copied (big ones left out) ·
  a sudo rule so the dashboard may act as THAT website's user only.
Claude's part (Claude Code, as that website's own Linux user — it owns only this beta's folder, never root):
  make the app run — install packages, build, migrate the beta database, links, caches — fix whatever fails, check the
  site answers, and hand back the steps that worked (the "recipe"). Later beta deploys replay the recipe; if it fails,
  Claude fixes it and the recipe is updated. Nothing here touches live except reading its database and files."""
import getpass
import re
import secrets
import shlex

from flask import current_app

from app import deploy, remote
from app import workspace as ws
from app.alerts import raise_alert
from app.auth import log
from app.crypto import decrypt_secret, encrypt_secret
from app.deploy import DeployError, build_beta_branch
from app.extensions import db
from app.jobs import note
from app.models import Deployment, Environment

UPLOADS_COPY_LIMIT_MB = 500          # bigger upload folders are not copied (beta storage stays small)
SETUP_TURNS, SETUP_USD = 80, 6.0
MODEL = "claude-sonnet-5-5"
SECRET_KEYS = re.compile(r"^(?:[A-Z0-9_]*(?:RAZORPAY|STRIPE|PAYPAL|PAYU|CASHFREE|PHONEPE|PAYTM|INSTAMOJO|CCAVENUE|TWILIO|MSG91|"
                         r"FCM|FIREBASE|ONESIGNAL|PUSHER|SENDGRID|MAILGUN|POSTMARK|WHATSAPP)[A-Z0-9_]*|AWS_ACCESS_KEY_ID|"
                         r"AWS_SECRET_ACCESS_KEY|MAIL_USERNAME|MAIL_PASSWORD)$")

SETUP_RULES = """You are setting up a BETA (test) copy of a client's web app on the company server, so the team can try \
changes before they go live. You run as this beta website's own Linux user: you own only the current folder; you can't \
use sudo, and you must not try to reach other websites, the live site or its database.
- The .env file is prepared by the dashboard. Never change its DB_*, APP_URL, MAIL_* or payment / cloud / SMS keys, \
and never print its contents.
- Use only the beta database named in .env (a copy of live) — migrations there are fine.
- Install what the app needs inside this folder, build it, and make the beta address answer. Fix errors you meet.
- Don't edit the app's source code to make it run, except obvious environment glue (a missing folder, a cache); if \
the code itself is broken, stop and explain.
Finish with the recipe exactly in this form, then a 3-line note for the owner in plain words:
RECIPE:
```
one shell command per line — the steps to run again in this folder after new code is copied in
```"""


def domain():
    return current_app.config.get("BETA_DOMAIN") or "beta.ivinfotech.com"


def names(project):
    slug = re.sub(r"[^a-z0-9-]", "-", project.slug.lower()).strip("-")[:40] or f"project-{project.id}"
    under = slug.replace("-", "_")
    return {"host": f"{slug}.{domain()}", "db": f"{under[:40]}_beta", "dbuser": f"b_{under[:20]}_{project.id}"}


def _q(v):
    return shlex.quote(str(v))


def _run(server, script, job, fail, timeout=900):
    code, out = remote.run(server, "set -euo pipefail\n" + script, timeout=timeout)
    if code != 0:
        note(job, "", out)
        raise DeployError(fail)
    return out


def _value(out, key):
    return next((l.split("=", 1)[1].strip() for l in out.splitlines() if l.startswith(key + "=")), "")


def env_overrides(project, live_keys, url, host, dbname, dbuser, dbpass):
    """KEY=VALUE pairs that turn a copy of live's .env into a safe beta .env."""
    pairs = {"APP_URL": url, "DB_HOST": "localhost", "DB_DATABASE": dbname, "DB_USERNAME": dbuser, "DB_PASSWORD": dbpass}
    safe = {"APP_ENV": "staging", "APP_DEBUG": "false", "MAIL_MAILER": "log", "QUEUE_CONNECTION": "sync",
            "CACHE_STORE": "file", "SESSION_DRIVER": "file", "SESSION_DOMAIN": "", "FILESYSTEM_DISK": "local",
            "SANCTUM_STATEFUL_DOMAINS": host, "TELESCOPE_ENABLED": "false",
            "REDIS_PREFIX": f"{names(project)['db']}_", "CACHE_PREFIX": f"{names(project)['db']}_"}
    for k, v in safe.items():
        if k in live_keys or k in ("REDIS_PREFIX", "CACHE_PREFIX"):
            pairs[k] = v
    for k in live_keys:
        if SECRET_KEYS.match(k):
            pairs[k] = ""
    for k, v in deploy.parse_env_lines(project.beta_env_sandbox) + deploy.parse_env_lines(project.beta_env_off):
        pairs[k] = v                                   # the owner's own beta lines win
    return list(pairs.items())


# ---------- Step 1: every beta behind a password (owner, 2026-10-17) ----------
# OpenLiteSpeed ignores auth lines in .htaccess, so the lock is a realm + "context /" in the beta's own vhost
# (marker LOCK_MARK). The ACME path keeps its own context, so SSL renewal still works.
LOCK_MARK = "AIDASH-BETA-LOCK"
BETA_USER = "beta"
SITES_SCRIPT = ("for V in /usr/local/lsws/conf/vhosts/*/vhost.conf; do D=$(basename $(dirname $V)); "
                "[ \"$D\" = Example ] && continue; "
                "echo \"SITE $D $(curl -s -o /dev/null -w '%{http_code}' --max-time 12 https://$D/ || echo 000)\"; done\n")
LOCK_BLOCK = """
# {mark}: beta behind a password (AI Project Dashboard)
realm aidashBeta {{
  userDB  {{
    location              /home/{host}/.beta-htpasswd
  }}
}}

context / {{
  location                $DOC_ROOT/
  allowBrowse             1
  realm                   aidashBeta
  authName                Beta (test site)
  required                valid-user
  rewrite  {{
    enable                1
    autoLoadHtaccess      1
  }}
  addDefaultCharset       off
}}
"""
INSERT_PY = ("import sys,base64;p=sys.argv[1];s=open(p).read();b=base64.b64decode(sys.argv[2]).decode();"
             "open(p,'w').write(s.replace('\\nvhssl  {',b+'\\nvhssl  {',1))")


def beta_auth(env):
    pw = decrypt_secret(env.beta_password_enc or "") if env is not None else ""
    return (BETA_USER, pw) if pw else None


def _sites(server, job):
    out = _run(server, SITES_SCRIPT, job, "Could not check the websites on the server.", timeout=600)
    return {p[1]: p[2] for p in (l.split() for l in out.splitlines()) if len(p) == 3 and p[0] == "SITE"}


def _broken(code):
    return code in ("000", "") or code.startswith("5")


def restart_web(server, job, why):
    """Graceful OpenLiteSpeed restart (standing OK for beta work, owner 2026-10-17). Every website is checked before
    and after; one that answered before and not after → the job stops and the owner gets an alert."""
    before = _sites(server, job)
    note(job, "Restarting the web server gracefully (a few seconds)")
    _run(server, "/usr/local/lsws/bin/lswsctrl restart >/dev/null\nsleep 4\n", job, "Could not restart the web server.")
    after = _sites(server, job)
    hurt = [d for d, c in after.items() if not _broken(before.get(d, "000")) and _broken(c)]
    if hurt:
        raise_alert("error", f"After {why}, these sites stopped answering: {', '.join(hurt)} — check now", None)
        raise DeployError(f"Done, but {', '.join(hurt)} stopped answering after the web-server restart — check them now.")


PROXY_BLOCK = """
# AIDASH-PROXY-{port}: lets this beta's .htaccess proxy to its service (AI Project Dashboard)
extprocessor 127.0.0.1:{port} {{
  type                    proxy
  address                 127.0.0.1:{port}
  maxConns                100
  initTimeout             60
  retryTimeout            0
  respBuffer              0
}}
"""


def allow_proxy(env, port, job):
    """OpenLiteSpeed proxies from .htaccess only to a known "external app" named after the address. Adds one for this
    beta's service port to THIS beta's vhost (never the server-wide config); restarts the web server only when added."""
    import base64
    host = env.url.split("//")[-1].split("/")[0]
    if not host.endswith("." + domain()) or not 6100 <= int(port) < 7000:
        raise DeployError("Proxy entries are only made for beta websites and beta service ports (6100–6999).")
    block = base64.b64encode(PROXY_BLOCK.format(port=int(port)).encode()).decode()
    out = _run(env.server, f"""H={_q(host)}; V=/usr/local/lsws/conf/vhosts/$H/vhost.conf
if ! grep -q "AIDASH-PROXY-{int(port)}" "$V"; then
  cp -p "$V" /root/$H-vhost.conf.before-proxy-{int(port)}-$(date +%Y%m%d-%H%M%S)
  python3 -c {_q(INSERT_PY)} "$V" {block}
  grep -q "AIDASH-PROXY-{int(port)}" "$V" && echo CHANGED=1
fi
""", job, "Could not register the service port with the web server.")
    if "CHANGED=1" in out:
        restart_web(env.server, job, "registering a beta service port")
        return True
    return False


# A single-page app (built files in the web root) whose own login sends "Authorization: Bearer …" to /api/ can't sit
# behind the beta password: the browser then sends the app's token instead of the beta password and every API call
# answers 401 (seen on iv-crm, 2026-10-21). So for that layout: the pages (and their files) keep the password —
# asked ONCE — and /api/ goes straight to the app's service, protected by the app's own login (owner's choice A).
OPEN_API_PY = r"""
import os, re, sys
vh, ht, mark = sys.argv[1], sys.argv[2], sys.argv[3]
mode = sys.argv[4] if len(sys.argv) > 4 else "spa"
s = open(vh).read()
if "AIDASH-BETA-LOCK-PAGES" in s or mark not in s:
    sys.exit(0)
if mode == "laravel":
    # PHP (Laravel): /api/ goes to the app's own service (artisan serve) on the given port; the pages are served by
    # the web server from public/ with Laravel's front-controller rule written inline (no .htaccess in a regex context)
    port = int(sys.argv[5])
    root = os.path.dirname(ht)
    sub = "public/" if os.path.isfile(os.path.join(root, "public", "index.php")) else ""
    page_rules = "RewriteCond %{REQUEST_FILENAME} !-d\nRewriteCond %{REQUEST_FILENAME} !-f\nRewriteRule ^ /index.php [L]\n"
else:
    try:
        rules = open(ht).read()
    except OSError:
        sys.exit(0)
    m = re.search(r"RewriteRule\s+\^/?api/\S*\s+http://127\.0\.0\.1:(\d+)/", rules)
    if not m:
        sys.exit(0)
    port = int(m.group(1))
    sub = ""
    page_rules = "RewriteCond %{REQUEST_FILENAME} !-f\nRewriteCond %{REQUEST_FILENAME} !-d\nRewriteRule ^ /index.html [L]\n"
if not 6100 <= port < 7000:
    sys.exit(0)
lock = ("  realm                   aidashBeta\n  authName                Beta (test site)\n"
        "  required                valid-user\n")
i = s.index("context / {")
j = s.index("}", s.index("rewrite", i))
if lock not in s[i:j]:
    sys.exit(0)
s = s[:i] + s[i:j].replace(lock, "", 1) + s[j:]
api = ("# AIDASH-API-OPEN-%d: the app's API skips the beta password (it has its own login) (AI Project Dashboard)\n"
       "context /api/ {\n  type                    proxy\n  handler                 127.0.0.1:%d\n"
       "  addDefaultCharset       off\n}\n\n" % (port, port)) if "AIDASH-API-OPEN" not in s else ""
pages = ("# AIDASH-BETA-LOCK-PAGES: every address except /api/ needs the beta password (AI Project Dashboard)\n"
         "context exp:^/(?!api/)(.*)$ {\n  location                $DOC_ROOT/" + sub + "$1\n  allowBrowse             1\n" + lock +
         "  rewrite  {\n    enable                1\n    rules                 <<<END_rules\n" + page_rules +
         "    END_rules\n  }\n  addDefaultCharset       off\n}\n\n")
s = s.replace("\nvhssl  {", "\n" + api + pages + "vhssl  {", 1)
# OpenLiteSpeed 1.9 resets HTTP/2 POSTs (with a body) that land in a regex context with a realm — every form on the
# beta broke ("connection reset", 2026-10-22). This beta speaks HTTP/1.1 only; nothing else changes.
s = re.sub(r"(?m)^(\s*enableSpdy\s+)\d+", r"\g<1>0", s)
open(vh, "w").write(s)
print("CHANGED=1")
"""


def open_api(env, job, mode="spa", port=None):
    """After built files are published next to a backend: the beta password stays on the pages (asked once), /api/ goes
    to the app's service with the app's own login. No change for other layouts. Returns True when changed."""
    import base64
    host = env.url.split("//")[-1].split("/")[0]
    if not host.endswith("." + domain()):
        return False
    code = base64.b64encode(OPEN_API_PY.encode()).decode()
    extra = f" laravel {int(port)}" if mode == "laravel" else ""
    out = _run(env.server, f"""H={_q(host)}; V=/usr/local/lsws/conf/vhosts/$H/vhost.conf; T=$(mktemp)
cp -p "$V" "$T"
python3 -c "$(echo {code} | base64 -d)" "$V" /home/$H/public_html/.htaccess {LOCK_MARK}{extra} > "$T.out"
if grep -q CHANGED=1 "$T.out"; then mv "$T" /root/$H-vhost.conf.before-open-api-$(date +%Y%m%d-%H%M%S); echo CHANGED=1; else rm -f "$T"; fi
rm -f "$T.out"
""", job, "Could not let the app's API past the beta password.")
    if "CHANGED=1" not in out:
        return False
    restart_web(env.server, job, "letting a beta's API past its password")
    _, code_api = deploy.health_ok(env.url.rstrip("/") + "/api/")
    _, code_page = deploy.health_ok(env.url)
    if code_page != 401:
        raise DeployError(f"After opening /api/ the beta's pages answer {code_page} without the password — check it now.")
    log("Beta lock", project_id=env.project_id, details=f"{host}: pages behind the password, /api/ via the app's own login")
    return True


def ensure_app_api(job, beta):
    """A project with mobile apps needs its beta API reachable by the app, which sends its own login token — the beta
    password can't sit on /api/ (CHANGES Step 10, owner 2026-10-22: "no project should get this error again").
    React + backend: /api/ already goes to a service → open_api. Laravel (code in the web root): /api/ gets its own
    `php artisan serve` service as the beta's user, then the same split. Anything else: left as it is (noted)."""
    from app import beta_tools
    host = beta.url.split("//")[-1].split("/")[0]
    out = _run(beta.server, f"""H={_q(host)}; V=/usr/local/lsws/conf/vhosts/$H/vhost.conf
if grep -q AIDASH-API-OPEN "$V"; then echo "OPEN=1"; fi
if grep -qE "RewriteRule +\\^/?api/" /home/$H/public_html/.htaccess 2>/dev/null; then echo "SPA=1"; fi
if [ -f {_q(beta.deploy_path)}/artisan ]; then echo "LARAVEL=1"; fi
echo "PHP=$(grep -oE 'lsphp[0-9]+' "$V" | head -1)"
""", job, "Could not read the beta's web server settings.")
    if "OPEN=1" in out:
        return False
    if "SPA=1" in out:
        return open_api(beta, job)
    php = _value(out, "PHP")
    if "LARAVEL=1" not in out or not re.fullmatch(r"lsphp\d+", php or ""):
        note(job, "", "Mobile apps: this beta's API is still behind the beta password (layout not recognised).")
        return False
    note(job, "Letting the mobile app reach the beta's API (pages keep the beta password)")
    msg, is_err = beta_tools.call(job, "start_service", {
        "name": "api", "workdir": ".",
        "command": f"PHP_CLI_SERVER_WORKERS=4 /usr/local/lsws/{php}/bin/php artisan serve --host=127.0.0.1 --port=$PORT"})
    m = re.search(r"127\.0\.0\.1:(\d+)", msg)
    if is_err or not m:
        raise DeployError(f"Could not start the beta's API service: {msg[:300]}")
    return open_api(beta, job, mode="laravel", port=int(m.group(1)))


def run_app_api(job):
    """Job kind "beta_api": make a beta's API reachable for the project's mobile apps."""
    beta = job.project.env("beta")
    if beta is None or not beta.managed or beta.server is None:
        raise DeployError("This project has no dashboard-made beta yet.")
    changed = ensure_app_api(job, beta)
    note(job, "The mobile app can reach the beta's API now (pages keep the beta password)" if changed else
         "Nothing to change — the beta's API was already open for the app (or its layout is not supported yet)")


def _apps_api_after(job, p, beta):
    """After a beta is made / updated: a project with mobile apps gets its beta API opened once. Test apps are NOT built
    by themselves (owner, 2026-10-22: check the web on beta first, then press Build) — the project page highlights a
    change that touched app code. Never fails the job."""
    if not p.mobile_apps:
        return
    try:
        ensure_app_api(job, beta)
    except DeployError as e:
        note(job, "", f"Mobile apps: could not open the beta API — {e.plain}")



def lock(env, job):
    """Put (or keep) the password on a beta. A graceful web-server restart only when its vhost had no lock yet —
    every website is checked before and after; one that answered before and not after raises an alert."""
    import base64
    host = env.url.split("//")[-1].split("/")[0]
    if not host.endswith("." + domain()):
        raise DeployError(f"{host} is not a beta address (*.{domain()}) — not locking it.")
    pw = decrypt_secret(env.beta_password_enc or "") or secrets.token_urlsafe(12)
    block = base64.b64encode(LOCK_BLOCK.format(mark=LOCK_MARK, host=host).encode()).decode()
    tag = "AIDASH_" + secrets.token_hex(4)
    note(job, "Locking the beta with a password")
    out = _run(env.server, f"""H={_q(host)}; V=/usr/local/lsws/conf/vhosts/$H/vhost.conf
[ -f "$V" ] || {{ echo "no web server settings for $H" >&2; exit 1; }}
PWH=$(openssl passwd -1 -stdin <<'{tag}'
{pw}
{tag}
)
printf '%s:%s\\n' {BETA_USER} "$PWH" > /home/$H/.beta-htpasswd
chown lsadm:nobody /home/$H/.beta-htpasswd; chmod 640 /home/$H/.beta-htpasswd
if ! grep -q {LOCK_MARK} "$V"; then
  cp -p "$V" /root/$H-vhost.conf.before-password-$(date +%Y%m%d-%H%M%S)
  python3 -c {_q(INSERT_PY)} "$V" {block}
  grep -q {LOCK_MARK} "$V" && echo CHANGED=1
fi
""", job, "Could not put the password on the beta.")
    env.beta_password_enc = encrypt_secret(pw)
    db.session.commit()
    if "CHANGED=1" in out:
        restart_web(env.server, job, "locking a beta")
    shut, code = deploy.health_ok(env.url)
    if code != 401:
        raise DeployError(f"The beta still opens without the password (answer {code}).")
    log("Beta lock", project_id=env.project_id, details=f"{host} behind a password")
    return pw


def run_lock(job):
    """Job kind "beta_lock": lock (or re-lock with a new password) a project's beta — dashboard-made or hand-made."""
    beta = job.project.env("beta")
    if beta is None or beta.server is None:
        raise DeployError("This project has no beta on a server yet.")
    if job.note.startswith("New password"):
        beta.beta_password_enc = ""
    lock(beta, job)
    note(job, f"Beta locked · user {BETA_USER} · the password is in the project's beta card")


def parse_recipe(text):
    m = re.search(r"RECIPE:\s*```[a-z]*\n(.*?)```", text or "", re.S)
    lines = [l.rstrip() for l in (m.group(1) if m else "").splitlines() if l.strip() and not l.strip().startswith("#")]
    return "\n".join(lines)


def _claude_as_site(job, beta, path, prompt):  # kept for reference: Claude as the website user (pre-Step 2)
    """Claude Code as the beta website's own user, in its folder. Returns its final text."""
    from app import claude_code as cc
    out = cc.run(path, prompt, MODEL, cc.new_session_id(), False, SETUP_TURNS, SETUP_USD,
                 lambda step: note(job, f"Claude: {step}"[:255]), ws._env(), run_as=beta.site_user, rules=SETUP_RULES)
    return out


def _smoke(url, job, auth=None):
    """The beta answers (with its password), and secrets / git history are not reachable from the web."""
    note(job, "Checking the beta site")
    ok, status = deploy.health_ok(url, auth=auth)
    if not ok or status == 401:
        return f"the beta address does not answer ({'no answer' if status == 0 else status})"
    for leak in ("/.env", "/.git/config"):
        leaked, code = deploy.exposed(url, leak, auth=auth)
        if leaked:
            return f"{leak} can be opened from the web"
    return ""


TOOL_RULES = """You make and fix BETA (test) copies of client web apps on the company server, using ONLY the beta tools
(mcp__beta__*). You have no shell of your own; the tools decide what is allowed and refuse the rest.
- Work only on THIS project's beta. Live is only read (its database, settings and files are copied, never changed).
- Never stop at the first error: read it, change what you do, try again. Install what's missing, use another version,
  fix a setting, add a folder. Only if a TOOL itself is broken or can't do what's needed, call report_problem with your
  proposed fix, then carry on as far as you can.
- Don't edit the app's own source code to make it run, except obvious environment glue (.htaccess routing, a missing
  folder, a build setting); if the app's code is broken, explain it.
- Before you finish: check_site shows the main pages answering, then save_recipe with the steps to replay for new code.
- End with a 3-line note for the owner, who is not a programmer, in plain words."""

SETUP_PROMPT = """Make this project's beta from nothing, so that {url} shows the working app with a copy of live's data.
A path that usually works: status → make_website → copy_database (mysql when live has a database; sqlite when status
lists a .db file the app uses) → put_code (place=webroot for PHP / Laravel / static sites; place=app when the app has a
backend that must keep running or a front-end build — the code then stays outside the web root, which is safer, and
you publish the built folder) → write_env for every settings file the app needs (for example .env, or
backend/.env) → run the install / build / migration commands this tech needs → start_service for any backend that must
keep running, and route to it with an .htaccess proxy rule (write_file web:.htaccess) → routing for the web root
(Laravel: rewrite to public/; a single-page app: index.html fallback) → publish (place=app) → check_site → save_recipe.
Tech from GitHub (may be incomplete): {tech}. PHP for the website: the version status reports (CLI binary
/usr/local/lsws/lsphpXX/bin/php). Node 20+: /opt/aidash-node/bin when installed."""

FIX_PROMPT = """New code was copied into this project's beta ({url}) and one of its saved steps failed.
Saved steps:
{recipe}

End of the output:
{output}

Make the beta work again with the new code (use the tools), check_site, and save_recipe with the corrected steps."""


def _tool_session(job):
    """A one-time key for this job + an MCP config Claude Code reads. Returns (config path, scratch folder, key)."""
    import json
    import os
    from pathlib import Path
    key = secrets.token_urlsafe(32)
    job.tool_token_hash = __import__("app.crypto", fromlist=["sha256"]).sha256(key)
    db.session.commit()
    base = Path(current_app.config.get("WORKSPACE_ROOT") or Path(current_app.instance_path) / "workspaces")
    scratch = base / f".beta-job-{job.id}"
    scratch.mkdir(parents=True, exist_ok=True)
    cfg = scratch / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"beta": {
        "type": "http", "url": current_app.config["INTERNAL_URL"].rstrip("/") + "/internal/mcp",
        "headers": {"Authorization": f"Bearer {key}"}}}}))
    os.chmod(cfg, 0o640)                           # the dashboard user + the runner's shared group only
    return cfg, scratch, key


def _end_tool_session(job, scratch):
    import shutil
    job.tool_token_hash = None
    db.session.commit()
    shutil.rmtree(scratch, ignore_errors=True)


def _claude_tools(job, prompt, turns=SETUP_TURNS, usd=SETUP_USD, rules=None):
    """Claude Code with only the beta tools, as the low-rights runner user. Returns its final text."""
    from app import claude_code as cc
    cfg, scratch, key = _tool_session(job)
    try:
        out = cc.run(scratch, prompt, MODEL, cc.new_session_id(), False, turns, usd,
                     lambda step: note(job, f"Claude: {step}"[:255]), ws._env(), rules=rules or TOOL_RULES,
                     mcp_config=cfg, tools=["mcp__beta"])
    except cc.CCError as e:
        raise DeployError(f"Claude could not finish: {e}")
    finally:
        _end_tool_session(job, scratch)
    return out["text"]


def run_setup(job):
    """Make (or rebuild) a project's beta — Claude does it with the beta tools. Job kind "beta_setup"."""
    p = job.project
    live = p.env("live")
    if live is None or live.server is None or not live.deploy_path:
        raise DeployError("Set the live server and folder first (Settings → Servers & deploy) — the beta is made as a copy of live.")
    n = names(p)
    url = f"https://{n['host']}"
    note(job, "Claude is making the beta")
    text = _claude_tools(job, SETUP_PROMPT.format(url=url, tech=", ".join(p.tech_stack or []) or "unknown"))
    db.session.refresh(p)
    beta = p.env("beta")
    note(job, "", "Claude's note:\n" + text[-3000:])
    if beta is None or not beta.managed:
        raise DeployError("Claude stopped before the beta website existed. Its note is below.")
    problem = _smoke(beta.url, job, beta_auth(beta))
    db.session.add(Deployment(project_id=p.id, environment_id=beta.id, commit=beta.current_commit or "-", change_ids=[],
                              by_user_id=job.by_user_id, result="failed" if problem else "ok",
                              note=("Beta made by Claude" + (f" — {problem}" if problem else ""))[:255]))
    log("Beta setup", project_id=p.id, user=job.by_user, details=f"{beta.url} · {beta.current_commit}" + (f" · {problem}" if problem else ""))
    db.session.commit()
    if problem:
        raise DeployError(f"Beta made, but {problem}. Claude's note is below.")
    if not p.beta_recipe.strip():
        raise DeployError("The beta works, but Claude did not save its steps (recipe) — rebuild it once more.")
    _apps_api_after(job, p, beta)
    raise_alert("golive", f"{p.name}: beta is ready at {beta.url}", p)
    note(job, f"Beta ready · {beta.url} · {beta.current_commit}")


def _hosts(project):
    """(live host, beta host) — e.g. ('crm.example.com', 'crm.beta.ivinfotech.com'); live '' when unknown."""
    live, beta = project.env("live"), project.env("beta")
    h = lambda e: (e.url or "").split("//")[-1].split("/")[0].lower() if e is not None and e.url else ""
    return h(live), h(beta)


def front_env_script(code_dir, live_host, beta_host):
    """A frontend build reads its server address from .env.production (VITE_API_URL=https://live…). On a beta that
    would make the beta's pages talk to the LIVE backend. Every such line gets the beta's address in a
    .env.production.local next to it (Vite / CRA / Next.js read it first; put_code keeps it, git never has it)."""
    live_re = "//" + live_host.replace(".", "\\.")
    return f"""cd {_q(code_dir)}
find . -maxdepth 3 -name .env.production -not -path '*/node_modules/*' 2>/dev/null | while read -r f; do
  o="$(dirname "$f")/.env.production.local"
  {{ grep -E '^[A-Za-z0-9_]+=' "$f" | grep -F {_q("//" + live_host)} | sed -E {_q(f"s#{live_re}#//{beta_host}#g")} ; }} > "$o.tmp" || true
  if [ -s "$o.tmp" ]; then mv "$o.tmp" "$o"; echo "BETA_ADDRESS_IN=${{o#./}}"; else rm -f "$o.tmp"; fi
done
"""


def _replay(job, beta, recipe):
    """Run the saved steps as the website user; '@restart NAME' restarts that beta service. Returns (code, output)."""
    from app import beta_tools
    lines = [l for l in recipe.splitlines() if l.strip()]
    shell = "\n".join(l for l in lines if not l.startswith(("@restart ", "@publish ")))
    out_all, code = "", 0
    live_host, beta_host = _hosts(job.project)
    if shell and live_host and beta_host and live_host != beta_host:
        shell = front_env_script(beta.deploy_path, live_host, beta_host) + f"cd {_q(beta.deploy_path)}\n" + shell
    if shell:
        tag = "AIDASH_" + secrets.token_hex(4)
        code, out_all = remote.run(beta.server, f"runuser -u {_q(beta.site_user)} -- bash -se <<'{tag}'\nset -euo pipefail\n"
                                                f"cd {_q(beta.deploy_path)}\nexport PATH=/opt/aidash-node/bin:/usr/local/bin:/usr/bin:/bin\n"
                                                f"{shell}\n{tag}\n", timeout=1800)
    for l in lines:
        if l.startswith("@publish ") and code == 0:
            msg, is_err = beta_tools.call(job, "publish", {"folder": l.split(" ", 1)[1].strip()})
            out_all += f"\n[publish] {msg}"
            code = 1 if is_err else code
    for l in lines:
        if l.startswith("@restart ") and code == 0:
            name = l.split(" ", 1)[1].strip()
            if beta_tools.NAME_RE.match(name):
                unit = f"aidash-{names(job.project)['db'].replace('_', '-')}-{name}"
                c2, o2 = remote.run(beta.server, f"systemctl restart {_q(unit)} && sleep 3 && systemctl is-active {_q(unit)}\n", timeout=120)
                out_all += f"\n[restart {name}] {o2.strip()}"
                code = code or c2
    return code, out_all


def deploy_fn(project):
    """Which beta deploy a project gets: the dashboard-made beta (recipe + Claude) or the owner's own deploy script."""
    beta = project.env("beta")
    return run_managed_deploy if beta is not None and beta.managed else deploy.run_beta_deploy


def maybe_auto_setup(project, user):
    """CHANGES Step 3: once live is known and the project has no beta anywhere yet, Claude starts making one."""
    from app import jobs
    from app.models import Job
    beta = project.env("beta")
    if not can_make_beta(project) or (beta is not None and beta.server_id) or jobs.busy_reason(project.id):
        return None
    j = Job(project_id=project.id, kind="beta_setup", note="Waiting to start (automatic, details saved)", by_user_id=user.id)
    db.session.add(j)
    log("Job", project_id=project.id, user=user, details="Automatic beta setup started (project details saved)")
    db.session.commit()
    jobs.start(j.id, run_setup, background=not current_app.config.get("AGENT_SYNC"))
    return j


def can_make_beta(project):
    live = project.env("live")
    return bool(project.github_repo and live is not None and live.server_id and live.deploy_path)


def run_managed_deploy(job):
    """A new beta version for a dashboard-made beta: new code in, the saved steps replayed (no Claude); if a step
    fails, Claude fixes it with the beta tools and the steps are updated."""
    from app import beta_tools
    p, beta = job.project, job.project.env("beta")
    note(job, "Putting the new code in")
    msg, is_err = beta_tools.call(job, "put_code", {})
    if is_err:
        raise DeployError(msg)
    fixed = False
    if p.beta_recipe.strip():
        note(job, "Running the beta steps")
        code, out = _replay(job, beta, p.beta_recipe)
        note(job, "", out)
        if code != 0:
            note(job, "A step failed — Claude is fixing it")
            text = _claude_tools(job, FIX_PROMPT.format(url=beta.url, recipe=p.beta_recipe, output=out[-3000:]), turns=40, usd=3.0)
            note(job, "", "Claude's note:\n" + text[-2000:])
            fixed = True
    db.session.refresh(beta)
    problem = _smoke(beta.url, job, beta_auth(beta))
    db.session.add(Deployment(project_id=p.id, environment_id=beta.id, commit=beta.current_commit or "-", change_ids=[],
                              by_user_id=job.by_user_id, result="failed" if problem else "ok",
                              note=("New beta version" + (" · Claude fixed a step" if fixed else "") + (f" — {problem}" if problem else ""))[:255]))
    log("Beta deploy", project_id=p.id, user=job.by_user, details=f"{beta.current_commit}" + (" · fixed by Claude" if fixed else ""))
    db.session.commit()
    if problem:
        raise DeployError(f"New beta version is in, but {problem}.")
    _apps_api_after(job, p, beta)
    note(job, f"Beta updated · {beta.current_commit}" + (" · Claude fixed a step" if fixed else ""))
