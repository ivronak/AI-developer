"""Claude's bounded server tools for making / fixing a beta (CHANGES Step 2, owner 2026-10-17).

Claude Code decides what to do and in which order; these tools decide what is ALLOWED. Each call is tied to one
running job (its one-time key) and therefore to ONE project: the tools only ever touch that project's beta website,
beta folder and beta database, and only read from live. Root work runs here (the dashboard, over SSH) — Claude itself
never gets a shell, sudo or root. App commands run as the beta website's own Linux user, inside its folder.

If a tool itself is wrong, Claude calls report_problem (owner gets an alert with Claude's proposed fix) — Claude never
changes a tool's code itself, because the tools are the limits."""
import json
import posixpath
import re
import secrets
import shlex
import threading

from app import betas, deploy, remote
from app import workspace as ws
from app.alerts import raise_alert
from app.auth import log
from app.crypto import decrypt_secret, encrypt_secret
from app.deploy import DeployError
from app.extensions import db
from app.jobs import note
from app.models import Environment

SERVICE_PORTS = range(6100, 7000)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,19}$")
ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
BAD_COMMAND = re.compile(r"(^|[\s;&|`$(])(sudo|su|doas|pkexec|chown|systemctl|crontab|ssh|scp)(\s|$)")
MAX_FILE = 200_000
SECRET_LINE = re.compile(r"(?im)^([ \t]*(?:export[ \t]+)?[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|PRIVATE|API_?KEY|ACCESS_KEY|"
                         r"APP_KEY|_KEY|DSN|DATABASE_URL|REDIS_URL|MONGO_URI)[A-Z0-9_]*[ \t]*[=:][ \t]*)(\S.*)$")


# the same names inside a line (logs, stack traces: "... APP_KEY=base64:xyz ..." / "DB_PASSWORD: abc")
SECRET_INLINE = re.compile(r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|PRIVATE|API_?KEY|ACCESS_KEY|APP_KEY|_KEY)[A-Z0-9_]*"
                           r"\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&\"']+)")
# more shapes a command's output can carry: "db_password": "x" / 'password' => 'x', define('DB_PASSWORD', 'x'), user:pass@ in a URL,
# Authorization / Bearer values and private key blocks
SECRET_QUOTED = re.compile(r"""(?i)(["'])([A-Za-z0-9_.-]*(?:password|passwd|secret|token|private|api_?key|access_key|app_key|dsn|"""
                           r"""database_url|redis_url|mongo_uri|_key)[A-Za-z0-9_.-]*)\1(\s*(?::|=>)\s*)(["']).*?\4""")
SECRET_DEFINE = re.compile(r"""(?i)(define\(\s*(["'])[A-Za-z0-9_]*(?:PASSWORD|SECRET|KEY|TOKEN|SALT)[A-Za-z0-9_]*\2\s*,\s*)(["']).*?\3""")
SECRET_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@]+:)([^\s/@]+)@")
SECRET_AUTH = re.compile(r"(?i)\b((?:authorization\s*[:=]\s*)?(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]{8,}")
SECRET_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)


def _redact(text):
    """Hide secret values in what goes back to Claude (and so to Anthropic): KEY=value lines with secret-looking names,
    and the same names inside log lines."""
    text = SECRET_PEM.sub("[private key hidden]", text or "")
    text = SECRET_QUOTED.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}{m.group(4)}***{m.group(4)}", text)
    text = SECRET_DEFINE.sub(lambda m: f"{m.group(1)}{m.group(3)}***{m.group(3)}", text)
    text = SECRET_URL.sub(lambda m: m.group(1) + "***@", text)
    text = SECRET_AUTH.sub(lambda m: m.group(1) + "***", text)
    return SECRET_INLINE.sub(lambda m: m.group(1) + "***", SECRET_LINE.sub(lambda m: m.group(1) + "***", text))


class ToolError(Exception):
    """A refused or failed tool call — the message goes back to Claude, who can try another way."""


def _q(v):
    return shlex.quote(str(v))


# ---------- scope ----------
def _ctx(job):
    p = job.project
    live, beta = p.env("live"), p.env("beta")
    if live is None or live.server is None or not live.deploy_path:
        raise ToolError("This project has no live server / folder set — the beta is made as a copy of live.")
    n = betas.names(p)
    return p, live, beta, n


def _beta_ready(beta):
    if beta is None or not beta.managed or not beta.site_user:
        raise ToolError("The beta website doesn't exist yet — call make_website first.")
    return beta


def _rel(path, allow_root=True):
    """A path relative to a folder: no absolute paths, no '..'."""
    path = (path or "").strip()
    while path.startswith("./"):
        path = path[2:]
    norm = posixpath.normpath(path) if path and path != "." else ""
    if path.startswith("/") or norm == ".." or norm.startswith("../"):
        raise ToolError(f"“{path}” must be a path inside the folder (no leading / and no ..).")
    if not allow_root and not norm:
        raise ToolError("A file path is needed.")
    return norm


def _sh(server, script, timeout=900):
    code, out = remote.run(server, "set -euo pipefail\n" + script, timeout=timeout)
    return code, out


def _as_site(beta, command, timeout=900):
    tag = "AIDASH_" + secrets.token_hex(4)
    return remote.run(beta.server, f"runuser -u {_q(beta.site_user)} -- bash -se <<'{tag}'\nset -o pipefail\n"
                                    f"cd {_q(beta.deploy_path)}\nexport HOME=$(getent passwd {_q(beta.site_user)} | cut -d: -f6)\n"
                                    f"export PATH=/opt/aidash-node/bin:/usr/local/bin:/usr/bin:/bin\n{command}\n{tag}\n", timeout=timeout)


# ---------- tools ----------
def t_status(job, args):
    p, live, beta, n = _ctx(job)
    code, out = _sh(live.server, f"""L={_q(live.deploy_path)}; H={_q(live.url.split('//')[-1].split('/')[0])}
echo "PHP=$(grep -o 'lsphp[0-9]*' /usr/local/lsws/conf/vhosts/$H/vhost.conf 2>/dev/null | head -1)"
echo "LIVE_DOCROOT=$(grep -m1 '^docRoot' /usr/local/lsws/conf/vhosts/$H/vhost.conf 2>/dev/null | awk '{{print $2}}')"
echo "NODE=$(/opt/aidash-node/bin/node -v 2>/dev/null || node -v 2>/dev/null)"; echo "PY=$(python3 -V 2>&1)"
echo "--- live top-level files:"; ls -1A "$L" | head -60
echo "--- env files in live:"; find "$L" -maxdepth 3 -name '.env' -not -path '*/node_modules/*' -not -path '*/vendor/*' | sed "s#^$L/##"
echo "--- sqlite files in live:"; find "$L" -maxdepth 4 \\( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \\) -size +0 -not -path '*/node_modules/*' -not -path '*/vendor/*' | sed "s#^$L/##" | head
echo "--- services that run from live:"; grep -lr "$L" /etc/systemd/system/*.service 2>/dev/null | xargs -r -n1 basename
DR=$(grep -m1 '^docRoot' /usr/local/lsws/conf/vhosts/$H/vhost.conf 2>/dev/null | awk '{{print $2}}' | sed "s#\\$VH_ROOT#/home/$H#")
echo "LIVE_WEBROOT=$DR"; echo "CODE_IS_OUTSIDE_WEBROOT=$([ "${{DR%/}}" = "${{L%/}}" ] || case "${{DR%/}}" in "${{L%/}}"/*) echo no;; *) echo yes;; esac)"
echo "--- live web root files:"; ls -1A "$DR" 2>/dev/null | head -25
echo "--- live web root .htaccess:"; head -30 "$DR/.htaccess" 2>/dev/null
echo "--- live proxy rules:"; grep -hoE '127\\.0\\.0\\.1:[0-9]+' "$L/.htaccess" "$L"/*/.htaccess "$DR/.htaccess" 2>/dev/null | sort -u
""")
    info = {"project": p.name, "repo": p.github_repo, "tech_from_github": p.tech_stack, "live_url": live.url,
            "live_folder": live.deploy_path, "live_database": live.db_name, "database_engine": p.db_engine,
            "beta_address": f"https://{n['host']}", "beta_web_root": f"/home/{n['host']}/public_html",
            "beta_code_folder": beta.deploy_path if beta and beta.managed else f"/home/{n['host']}/public_html (or /home/{n['host']}/app with put_code place=app)",
            "beta_exists": bool(beta and beta.managed), "beta_database": beta.db_name if beta else None,
            "saved_recipe": p.beta_recipe or None, "server_facts": out[-4000:]}
    return json.dumps(info, indent=1)


def t_make_website(job, args):
    """The beta's website + SSL (CyberPanel) and its password lock. Safe to call again."""
    p, live, beta, n = _ctx(job)
    host, url, path = n["host"], f"https://{n['host']}", f"/home/{n['host']}/public_html"
    note(job, f"Claude: making the website {host}")
    php = betas._value(betas._run(live.server, f"grep -o 'lsphp[0-9]*' /usr/local/lsws/conf/vhosts/"
                                               f"{_q(live.url.split('//')[-1].split('/')[0])}/vhost.conf 2>/dev/null | head -1 "
                                               "| sed 's/lsphp/PHP=/' || true\n", job, "Could not read live's PHP version."), "PHP")
    php_ver = f"{php[0]}.{php[1:]}" if len(php) >= 2 else str(args.get("php") or "8.3")
    code, out = _sh(live.server, f"""H={_q(host)}
if [ ! -d /home/$H ]; then
  cp -p /usr/local/lsws/conf/httpd_config.conf /root/httpd_config.conf.before-beta-$H-$(date +%Y%m%d-%H%M%S)
  cyberpanel createWebsite --package Default --owner admin --domainName $H --email webmaster@ivinfotech.com --php {_q(php_ver)} --ssl 1 >/dev/null
  sleep 3
fi
echo "USER=$(stat -c %U /home/$H)"
""", timeout=600)
    user = betas._value(out, "USER")
    if code != 0 or not user or user == "root":
        raise ToolError("Could not create the beta website: " + out[-400:])
    if beta is None:
        beta = Environment(kind="beta", branch="beta", url=url)
        p.environments.append(beta)
    beta.url, beta.server_id, beta.deploy_path, beta.site_user, beta.managed = url, live.server_id, path, user, True
    db.session.commit()
    betas.lock(beta, job)
    return f"Website {host} is ready (SSL on, behind the beta password). Folder {path}, Linux user {user}, PHP {php_ver}."


def t_copy_database(job, args):
    """A copy of live's data for the beta. MySQL: own beta database + user. SQLite: an online backup of a file."""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    engine = (args.get("engine") or p.db_engine or "mysql").lower()
    if engine == "mysql":
        if not live.db_name:
            raise ToolError("No live MySQL database is set for this project (Settings → Servers & deploy).")
        note(job, "Claude: copying the live database into the beta database (live is only read)")
        pw = decrypt_secret(beta.db_password_enc or "") or secrets.token_urlsafe(24)
        code, out = _sh(live.server, f"""mysql -e "CREATE DATABASE IF NOT EXISTS \\`{n['db']}\\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
mysql -e "CREATE USER IF NOT EXISTS '{n['dbuser']}'@'localhost' IDENTIFIED BY '{pw}'"
mysql -e "ALTER USER '{n['dbuser']}'@'localhost' IDENTIFIED BY '{pw}'"
mysql -e "GRANT ALL PRIVILEGES ON \\`{n['db']}\\`.* TO '{n['dbuser']}'@'localhost'"
mysqldump --single-transaction --quick --routines --no-tablespaces {_q(live.db_name)} | mysql {_q(n['db'])}
echo TABLES=$(mysql -N -e "select count(*) from information_schema.tables where table_schema='{n['db']}'")
""", timeout=3600)
        if code != 0:
            raise ToolError("The MySQL copy failed: " + out[-400:])
        beta.db_name, beta.db_user, beta.db_password_enc = n["db"], n["dbuser"], encrypt_secret(pw)
        db.session.commit()
        return (f"Beta database `{n['db']}` filled from live ({betas._value(out, 'TABLES')} tables). write_env points DB_* at it "
                f"automatically (user {n['dbuser']}, host localhost).")
    if engine == "sqlite":
        src, dst = _rel(args.get("live_file"), False), _rel(args.get("beta_file") or args.get("live_file"), False)
        if not re.search(r"\.(db|sqlite|sqlite3)$", src):
            raise ToolError("live_file must be an SQLite file (.db / .sqlite / .sqlite3) inside the live folder.")
        note(job, f"Claude: copying the SQLite file {src} (live is only read)")
        code, out = _sh(live.server, f"""S={_q(live.deploy_path)}/{_q(src)}; D={_q(beta.deploy_path)}/{_q(dst)}
[ -f "$S" ] || {{ echo "no such file in live: {src}" >&2; exit 1; }}
mkdir -p "$(dirname "$D")"
python3 -c 'import sqlite3,sys; a=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro", uri=True); b=sqlite3.connect(sys.argv[2]); a.backup(b); b.close(); a.close()' "$S" "$D.tmp"
mv "$D.tmp" "$D"; chown {_q(beta.site_user)}:{_q(beta.site_user)} "$D"; chmod 660 "$D"
echo SIZE=$(du -h "$D" | cut -f1)
""", timeout=3600)
        if code != 0:
            raise ToolError("The SQLite copy failed: " + out[-400:])
        beta.db_name = f"sqlite:{dst}"
        db.session.commit()
        return f"Copied {src} → beta {dst} ({betas._value(out, 'SIZE')}). The beta app must use this file (relative path {dst})."
    raise ToolError(f"Copying a {engine} database isn't supported yet — say so in report_problem.")


def t_put_code(job, args):
    """The beta branch (main + ready changes) into the beta folder. Never touches .env / vendor / node_modules / storage."""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    place = (args.get("place") or "").strip() or ("app" if beta.deploy_path.endswith("/app") else "webroot")
    if place not in ("webroot", "app"):
        raise ToolError("place is 'webroot' (PHP / Laravel / static: code served from the web root) or 'app' (code kept "
                        "outside the web root — backends, single-page apps; then publish the built files).")
    beta.deploy_path = f"/home/{n['host']}/app" if place == "app" else f"/home/{n['host']}/public_html"
    db.session.commit()
    note(job, "Claude: putting the beta branch's code into the beta folder")
    try:
        sha, merged, skipped = deploy.build_beta_branch(p, job)
    except ws.WorkspaceError as e:
        raise ToolError(f"Could not build the beta branch: {e}")
    stage = f"/root/ai-dashboard-stage/{n['host']}"
    err = remote.push_dir(beta.server, ws.root_for(p), stage)
    if err:
        raise ToolError(err)
    keep = " ".join(f"--exclude=/{x}" for x in (".env", ".htaccess", "vendor", "node_modules", "storage", ".well-known",
                                               "venv", ".venv")) + " --exclude=__pycache__ --exclude='.env'"
    code, out = _sh(beta.server, f"""P={_q(beta.deploy_path)}; L={_q(live.deploy_path)}; U={_q(beta.site_user)}
mkdir -p "$P"
rsync -a {keep} --exclude='*.db' --exclude='*.sqlite' --exclude='*.sqlite3' {_q(stage)}/ "$P"/
if [ -d "$L/storage" ] && [ ! -d "$P/storage" ]; then
  mkdir -p "$P/storage"; SZ=$(du -sm "$L/storage/app" 2>/dev/null | cut -f1); SZ=${{SZ:-0}}
  if [ "$SZ" -le {betas.UPLOADS_COPY_LIMIT_MB} ]; then rsync -a --exclude=/logs --exclude=/framework "$L/storage/" "$P/storage/"; fi
  mkdir -p "$P/storage/logs" "$P/storage/framework/cache" "$P/storage/framework/sessions" "$P/storage/framework/views"
fi
chown -R "$U:$U" "$P"; chmod -R u+rwX,go+rX,go-w "$P"; [ -f "$P/.env" ] && chmod 640 "$P/.env"
if [ "$P" = "/home/{n['host']}/public_html" ]; then chown "$U:nobody" "$P"; chmod 750 "$P"; else chmod 750 "$P"; fi
rm -rf {_q(stage)}
""", timeout=1800)
    if code != 0:
        raise ToolError("Copying the code failed: " + out[-400:])
    beta.current_commit = sha
    job.output = (job.output + f"\n[code] beta branch {sha}, {len(merged)} change(s)").strip()
    db.session.commit()
    return (f"Code of the beta branch ({sha}, {len(merged)} change(s)) is in {beta.deploy_path} (place={place}). Kept as they were: "
            ".env files, vendor, node_modules, storage, venv, .htaccess, database files. Uploads under storage/ copied when small.")


def t_write_env(job, args):
    """A beta settings file made from live's (same relative path by default): beta address, beta database, debug
    off, mail to the log, payment / cloud / SMS / mail secrets blanked. Claude may add or change other keys."""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    target = _rel(args.get("file") or ".env", False)
    source = _rel(args.get("from_live") or target, False)
    extra = args.get("set") or {}
    if not isinstance(extra, dict):
        raise ToolError("set must be an object of KEY: value.")
    for k, v in extra.items():
        if not ENV_KEY.match(str(k)):
            raise ToolError(f"“{k}” is not a valid setting name.")
        if betas.SECRET_KEYS.match(str(k)) and str(v):
            raise ToolError(f"{k} is a live secret (payment / mail / cloud / SMS) — beta keeps it empty or uses the "
                            "owner's sandbox lines.")
        if live.db_name and str(v) == live.db_name:
            raise ToolError("The beta may never point at the live database.")
    code, keys = _sh(live.server, f"grep -oE '^[A-Za-z0-9_]+=' {_q(live.deploy_path)}/{_q(source)} 2>/dev/null | tr -d '=' || true\n")
    live_keys = {k.strip() for k in keys.splitlines() if k.strip()}
    mysql = beta.db_user and beta.db_name and not beta.db_name.startswith("sqlite:")
    pairs = dict(betas.env_overrides(p, live_keys, beta.url, n["host"], beta.db_name if mysql else "",
                                     beta.db_user if mysql else "", decrypt_secret(beta.db_password_enc or "") if mysql else ""))
    if not mysql:
        for k in ("DB_HOST", "DB_DATABASE", "DB_USERNAME", "DB_PASSWORD"):
            pairs.pop(k, None)
    pairs.update({str(k): str(v) for k, v in extra.items()})
    script = deploy.env_script(f"{beta.deploy_path}/{posixpath.dirname(target) or '.'}", list(pairs.items()),
                               filename=posixpath.basename(target))
    code, out = _sh(beta.server, f"""P={_q(beta.deploy_path)}; F="$P/"{_q(target)}; L={_q(live.deploy_path)}/{_q(source)}
mkdir -p "$(dirname "$F")"
[ -f "$F" ] || {{ if [ -f "$L" ]; then cp "$L" "$F"; else touch "$F"; fi; }}
{script.replace("set -euo pipefail", "")}
chown {_q(beta.site_user)}:{_q(beta.site_user)} "$F"; chmod 640 "$F"
""")
    if code != 0:
        raise ToolError("Writing the settings file failed: " + out[-300:])
    shown = sorted(k for k in pairs if not betas.SECRET_KEYS.match(k) and "PASSWORD" not in k)
    return f"{target} written ({len(pairs)} settings set for beta: {', '.join(shown)}; secrets kept empty)."


def t_run(job, args):
    """A command as the beta website's own user, inside the beta folder (install, build, migrate, tests…)."""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    cmd = (args.get("command") or "").strip()
    if not cmd:
        raise ToolError("command is empty.")
    if BAD_COMMAND.search(cmd):
        raise ToolError("That command needs extra rights (sudo / chown / systemctl / ssh / crontab) — use the tools "
                        "(start_service, …) or report_problem.")
    timeout = max(10, min(int(args.get("timeout_seconds") or 900), 1800))
    note(job, f"Claude: running {cmd[:180]}")
    code, out = _as_site(beta, cmd, timeout=timeout)
    return f"exit code {code}\n{_redact(out[-6000:])}"


def t_read_file(job, args):
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    path = _rel(args.get("path"), False)
    if re.search(r"(^|/)\.env($|\.)", path):
        raise ToolError("Settings files are not shown (they hold secrets) — use write_env to change them.")
    code, out = _as_site(beta, f"head -c {MAX_FILE} -- {_q(path)}")
    return _redact(out) if code == 0 else f"Could not read {path}: {out[-300:]}"


HTTPS_MARK = "AIDASH-HTTPS"
HTTPS_RULES = (f"# {HTTPS_MARK}: beta is https only (AI Project Dashboard)\n"
               "RewriteCond %{HTTPS} !=on\nRewriteCond %{HTTP:X-Forwarded-Proto} !https\n"
               "RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]\n")


def https_first(content):
    """The beta's .htaccess always sends http:// to https:// first (added after RewriteEngine On; added if missing)."""
    if HTTPS_MARK in content:
        return content
    m = re.search(r"^[ \t]*RewriteEngine[ \t]+On[ \t]*\r?\n", content, re.M | re.I)
    if m:
        return content[:m.end()] + HTTPS_RULES + content[m.end():]
    return "RewriteEngine On\n" + HTTPS_RULES + content


def t_write_file(job, args):
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    raw = (args.get("path") or "").strip()
    web = raw.startswith("web:")
    path = _rel(raw[4:] if web else raw, False)
    content = args.get("content") or ""
    if re.search(r"(^|/)\.env($|\.)", path):
        raise ToolError("Use write_env for settings files.")
    if web:
        path = f"/home/{n['host']}/public_html/{path}"
    if len(content) > MAX_FILE:
        raise ToolError("File too big for write_file.")
    routing = web and path.endswith("/public_html/.htaccess")
    if routing:
        content = https_first(content)
    tag = "AIDASH_" + secrets.token_hex(6)
    code, out = _as_site(beta, f"mkdir -p \"$(dirname -- {_q(path)})\"\ncat > {_q(path)} <<'{tag}'\n{content}\n{tag}\n")
    if code != 0:
        return f"Could not write {path}: {out[-300:]}"
    if routing:
        # OpenLiteSpeed caches "no .htaccess" per folder: without a graceful restart the new routing is ignored (404 / no CSS)
        try:
            betas.restart_web(beta.server, job, "writing the beta's routing (.htaccess)")
        except DeployError as e:
            return f"Wrote {path}, but: {e.plain}"
        return (f"Wrote {path} ({len(content)} characters); the web server was restarted gracefully so it is used now. "
                "http:// is redirected to https:// automatically.")
    return f"Wrote {path} ({len(content)} characters)."


def t_publish(job, args):
    """Copy built files (e.g. web/dist) from the code folder into the beta's web root (code kept outside it)."""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    webroot = f"/home/{n['host']}/public_html"
    if beta.deploy_path == webroot:
        raise ToolError("The code is in the web root already (put_code place=webroot) — nothing to publish.")
    folder = _rel(args.get("folder"), False)
    live_host, beta_host = betas._hosts(p)
    # a beta's pages must never call the live site: a build that still has live's address is not published
    # only the address used as a server ("https://live…" / 'https://live…/api' in quotes), not live's name in a sentence
    live_url_re = "[\"'`]https?://" + live_host.replace(".", "\\.") + "(/[^\"'` ]*)?[\"'`]"
    guard = (f"""if grep -rqsE {_q(live_url_re)} "$S" --include='*.js' --include='*.mjs' --include='*.html' --include='*.json'; then
  echo "LIVE_ADDRESS_IN_BUILD" >&2; exit 3
fi
""" if live_host and live_host != beta_host else "")
    code, out = _sh(beta.server, f"""S={_q(beta.deploy_path)}/{_q(folder)}; W={_q(webroot)}; U={_q(beta.site_user)}
[ -d "$S" ] || {{ echo "no folder {folder} in the code folder (build it first)" >&2; exit 1; }}
{guard}rsync -a --delete --exclude=/.htaccess --exclude=/.well-known "$S"/ "$W"/
chown -R "$U:$U" "$W"; chmod -R u+rwX,go+rX,go-w "$W"; chown "$U:nobody" "$W"; chmod 750 "$W"
echo FILES=$(find "$W" -type f | wc -l)
""", timeout=600)
    if code != 0:
        if "LIVE_ADDRESS_IN_BUILD" in out:
            raise ToolError(f"Not published: the built files still call the LIVE site (https://{live_host}). A beta must never "
                            f"talk to live — build with the beta's address (e.g. VITE_API_URL=https://{beta_host}, or a "
                            ".env.production.local) and publish again.")
        raise ToolError("Publishing failed: " + out[-300:])
    opened = betas.open_api(beta, job)
    return (f"Published {folder} → web root ({betas._value(out, 'FILES')} files). Routing (.htaccess) stays as you wrote it."
            + (" The beta password now covers the pages only (asked once); /api/ goes to the service with the app's own login."
               if opened else ""))


def t_start_service(job, args):
    """A long-running backend (Flask / Django / FastAPI / Node / Next…) as the beta website's user, on its own local
    port (given in $PORT). Restarts it if it exists. Make the website reach it with an .htaccess proxy rule:
    RewriteRule ^api/(.*)$ http://127.0.0.1:PORT/api/$1 [P,L]  (or ^(.*)$ for the whole site)."""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    name, cmd = (args.get("name") or "").strip(), (args.get("command") or "").strip()
    workdir = _rel(args.get("workdir") or "")
    if not NAME_RE.match(name):
        raise ToolError("name: lower-case letters, digits and -, up to 20 characters.")
    if not cmd or BAD_COMMAND.search(cmd) or "\n" in cmd:
        raise ToolError("command must be one line, without sudo / systemctl / chown / ssh.")
    unit = f"aidash-{betas.names(p)['db'].replace('_', '-')}-{name}"
    note(job, f"Claude: starting the service {name}")
    code, out = _sh(beta.server, f"""U={_q(unit)}; F=/etc/systemd/system/$U.service
PORT=$(sed -n 's/^Environment=PORT=//p' "$F" 2>/dev/null || true)
if [ -z "$PORT" ]; then
  for c in $(seq {SERVICE_PORTS.start} {SERVICE_PORTS.stop - 1}); do
    ss -ltn | grep -q ":$c " && continue
    grep -qx "Environment=PORT=$c" /etc/systemd/system/aidash-*.service 2>/dev/null && continue
    PORT=$c; break
  done
fi
cat > "$F" <<UNIT
[Unit]
Description=AI Dashboard beta service {name} ({n['host']})
After=network.target
[Service]
User={beta.site_user}
Group={beta.site_user}
WorkingDirectory={beta.deploy_path}/{workdir}
Environment=PORT=$PORT
Environment=PATH=/opt/aidash-node/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash -c {_q(cmd)}
Restart=always
RestartSec=3
UMask=0022
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload; systemctl enable "$U" >/dev/null 2>&1; systemctl restart "$U"; sleep 4
echo "PORT=$PORT"; echo "STATE=$(systemctl is-active "$U")"
journalctl -u "$U" -n 15 --no-pager 2>/dev/null | tail -15
""", timeout=180)
    if code != 0:
        raise ToolError("Could not start the service: " + out[-400:])
    port = betas._value(out, "PORT")
    job.output = (job.output + f"\n[service] {unit} on 127.0.0.1:{port}").strip()
    db.session.commit()
    added = betas.allow_proxy(beta, port, job) if port.isdigit() else False
    return (f"Service {name} ({unit}) state {betas._value(out, 'STATE')} on 127.0.0.1:{port}. The web server may proxy to "
            f"http://127.0.0.1:{port}/ from web:.htaccess" + (" (just registered)" if added else "") + f".\n{out[-1500:]}")


def t_web_errors(job, args):
    """The web server's recent errors for this beta (e.g. why a page gives 500)."""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    code, out = _sh(beta.server, f"""H={_q(n['host'])}
echo "--- website error log:"; tail -n 30 /home/$H/logs/$H.error_log 2>/dev/null || true
echo "--- web server log (this beta only):"; grep -F "$H" /usr/local/lsws/logs/error.log 2>/dev/null | tail -n 20 || true
""")
    return out[-6000:]


def t_service_logs(job, args):
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    name = (args.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise ToolError("Unknown service name.")
    unit = f"aidash-{n['db'].replace('_', '-')}-{name}"
    code, out = _sh(beta.server, f"systemctl is-active {_q(unit)} || true\njournalctl -u {_q(unit)} -n 80 --no-pager\n")
    return _redact(out[-6000:])


def t_check_site(job, args):
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    paths = args.get("paths") or ["/"]
    auth = betas.beta_auth(beta)
    lines = []
    for path in paths[:15]:
        path = "/" + str(path).lstrip("/")
        ok, status = deploy.health_ok(beta.url.rstrip("/") + path, auth=auth)
        lines.append(f"{path} → {status or 'no answer'}")
    for leak in ("/.env", "/.git/config"):
        leaked, status = deploy.exposed(beta.url, leak, auth=auth)
        lines.append(f"{leak} → {status} " + ("(PROBLEM: the real file can be read from the web)" if leaked else
                                              "(ok: the app's own page, not the file)" if status == 200 else "(ok, hidden)"))
    return "\n".join(lines)


def t_save_recipe(job, args):
    """The steps to run again after new code arrives, one shell command per item (run as the website user in the
    beta folder). A line "@restart NAME" restarts a service made with start_service."""
    p = job.project
    steps = [str(s).strip() for s in (args.get("steps") or []) if str(s).strip()]
    if not steps:
        raise ToolError("steps is empty.")
    for s in steps:
        if not s.startswith(("@restart ", "@publish ")) and BAD_COMMAND.search(s):
            raise ToolError(f"Step needs extra rights: {s}")
        if s.startswith("@restart ") and not NAME_RE.match(s.split(" ", 1)[1].strip()):
            raise ToolError(f"Unknown service in: {s}")
        if s.startswith("@publish "):
            _rel(s.split(" ", 1)[1].strip(), False)
    p.beta_recipe = "\n".join(steps)
    db.session.commit()
    return f"Recipe saved ({len(steps)} steps). Beta deploys will replay it."


def t_report_problem(job, args):
    """A tool itself is broken or missing something: the owner gets an alert with Claude's proposed fix."""
    p = job.project
    tool, problem, fix = str(args.get("tool") or "?")[:60], str(args.get("problem") or "")[:800], str(args.get("proposed_fix") or "")[:1500]
    log("Tool problem", project_id=p.id, details=f"{tool}: {problem}\nProposed fix: {fix}")
    db.session.commit()
    raise_alert("error", f"{p.name}: Claude reports a problem with the beta tool “{tool}” — see the activity log", p)
    job.output = (job.output + f"\n[tool problem] {tool}: {problem}\nProposed fix: {fix}").strip()
    db.session.commit()
    return "Reported to the owner. Continue with another way if you can; otherwise finish and explain."


# ---------- release-fix mode (Go Live v2): a failing step in a NEW live release, while live keeps running ----------
LIVE_DANGER = re.compile(r"(migrate:(fresh|refresh|reset|rollback)|db:(wipe|seed)|\bseed(er)?\b|\btinker\b|"
                         r"\b(mysql|mysqldump|mariadb|psql|pg_dump|pg_restore|sqlite3|redis-cli|mongo|mongosh)\b|"
                         r"\b(drop|truncate)\b|\bdelete\s+from\b|\bupdate\s+\w+\s+set\b|\balter\s+table\b|"
                         r"\bgit\s+(push|commit|reset|checkout|clean)\b|\.\./)", re.I)
ABS_PATH = re.compile(r"(?<![\w.~$-])/[\w./@+-]+")
SAFE_ABS = ("/usr/", "/bin/", "/opt/aidash-node", "/dev/null", "/tmp/", "/etc/alternatives/")


def _rx(job):
    ctx = job.context or {}
    if ctx.get("mode") not in ("release_fix", "live_steps") or not ctx.get("release"):
        raise ToolError("No live release is being worked on in this job.")
    if ctx.get("run_user") in ("", None, "root"):
        raise ToolError("This live site's code belongs to root — Claude never works as root. Stop and explain.")
    return ctx, job.project.env("live")


def rx_status(job, args):
    ctx, live = _rx(job)
    return json.dumps({"new_release_folder": ctx["release"], "runs_as": ctx["run_user"], "live_address": live.url,
                       "shared_items_linked_in": live.shared, "failing_steps": ctx.get("recipe"),
                       "end_of_the_failing_output": ctx.get("error", "")[-3000:]}, indent=1)


def rx_run(job, args):
    ctx, live = _rx(job)
    cmd = (args.get("command") or "").strip()
    if not cmd:
        raise ToolError("command is empty.")
    if BAD_COMMAND.search(cmd) or LIVE_DANGER.search(cmd):
        raise ToolError("Not allowed on a live release: it could change the live database, the shared files or git. "
                        "Only install / build / cache / config commands — no database commands, no seeding, no wiping.")
    for path in ABS_PATH.findall(cmd):
        if not (path.startswith(ctx["release"]) or path.startswith(SAFE_ABS) or re.match(r"^/usr/local/lsws/lsphp\d+/bin/", path)):
            raise ToolError(f"{path} is outside the new release folder.")
    timeout = max(10, min(int(args.get("timeout_seconds") or 900), 1800))
    note(job, f"Claude (release): {cmd[:170]}")
    tag = "AIDASH_" + secrets.token_hex(4)
    code, out = remote.run(live.server, f"runuser -u {_q(ctx['run_user'])} -- bash -se <<'{tag}'\nset -o pipefail\n"
                                        f"cd {_q(ctx['release'])}\nexport PATH=/opt/aidash-node/bin:/usr/local/bin:/usr/bin:/bin\n"
                                        f"{cmd}\n{tag}\n", timeout=timeout)
    return f"exit code {code}\n{_redact(out[-6000:])}"


def rx_read_file(job, args):
    ctx, live = _rx(job)
    path = _rel(args.get("path"), False)
    if re.search(r"(^|/)\.env($|\.)", path) or any(path == s or path.startswith(s.rstrip("/") + "/") for s in (live.shared or [])):
        raise ToolError("Shared live files (settings, uploads, data) are not shown.")
    code, out = remote.run(live.server, f"runuser -u {_q(ctx['run_user'])} -- head -c {MAX_FILE} -- {_q(ctx['release'])}/{_q(path)}\n")
    return _redact(out) if code == 0 else f"Could not read {path}: {out[-300:]}"


def rx_web_errors(job, args):
    ctx, live = _rx(job)
    host = live.url.split("//")[-1].split("/")[0]
    code, out = remote.run(live.server, f"tail -n 30 /home/{_q(host)}/logs/{_q(host)}.error_log 2>/dev/null || true\n")
    return _redact(out[-4000:])


def rx_save_recipe(job, args):
    steps = [str(x).strip() for x in (args.get("steps") or []) if str(x).strip()]
    if not steps:
        raise ToolError("steps is empty.")
    for x in steps:
        if x.startswith("@"):
            continue
        if BAD_COMMAND.search(x) or (LIVE_DANGER.search(x) and "migrate --force" not in x):
            raise ToolError(f"Step not allowed for live: {x}")
    job.project.live_recipe = "\n".join(steps)
    db.session.commit()
    return f"Live steps saved ({len(steps)}). The dashboard runs them all again in the new release before switching."


# ---------- "change_debug" mode: while making a change, Claude can look at the BETA site (CHANGES Step 8 groundwork) ----
MAX_BODY = 4000


def _page_text(html):
    """Readable text of a page for Claude: scripts / styles dropped, tags removed, whitespace squeezed."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def dbg_status(job, args):
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    return json.dumps({"beta_address": beta.url, "beta_runs_commit": beta.current_commit,
                       "note": "The beta runs the LAST DEPLOYED version. Your edits in this folder reach the beta only after "
                               "you finish (the dashboard deploys them). Use the beta to reproduce the owner's problem and "
                               "read its logs; then fix the code here.",
                       "change_id": (job.context or {}).get("change_id")}, indent=1)


def dbg_request(job, args):
    """Open a beta page (with the beta password, cookies kept for this run). POST forms get Laravel's CSRF token by
    themselves (taken from the page in csrf_from, default the same path)."""
    import requests
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    path = str(args.get("path") or "/").strip()
    if not path.startswith("/") or "//" in path or "\\" in path:
        raise ToolError("path must be a path on the beta, starting with / (e.g. /forgot-password).")
    method = str(args.get("method") or "GET").upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        raise ToolError("method: GET, POST, PUT, PATCH or DELETE.")
    ctx = dict(job.context or {})
    sess = requests.Session()
    sess.auth = betas.beta_auth(beta)
    sess.headers["User-Agent"] = "ai-project-dashboard (Claude, beta check)"
    for k, v in (ctx.get("cookies") or {}).items():
        sess.cookies.set(k, v)
    base = beta.url.rstrip("/")
    form, body_json = args.get("form"), args.get("json")
    headers = {"Accept": "application/json"} if body_json is not None else {}
    if isinstance(args.get("headers"), dict):
        headers.update({str(k)[:60]: str(v)[:500] for k, v in args["headers"].items() if str(k).lower() not in ("host", "cookie")})
    try:
        if method != "GET" and isinstance(form, dict) and "_token" not in form:
            page = sess.get(base + str(args.get("csrf_from") or path), timeout=30)
            m = re.search(r'name="_token"\s+value="([^"]+)"', page.text) or re.search(r'name="csrf-token"\s+content="([^"]+)"', page.text)
            if m:
                form = dict(form, _token=m.group(1))
        r = sess.request(method, base + path, data=form if isinstance(form, dict) else None,
                         json=body_json if body_json is not None else None, headers=headers, timeout=60,
                         allow_redirects=bool(args.get("follow_redirects", True)))
    except requests.RequestException as e:
        return f"The beta did not answer properly: {e.__class__.__name__} — {str(e)[:300]}. (A dropped connection usually " \
               "means PHP crashed — read beta_logs.)"
    ctx["cookies"] = {c.name: c.value for c in sess.cookies}
    job.context = ctx
    db.session.commit()
    ctype = r.headers.get("Content-Type", "")
    body = r.text if "json" in ctype else _page_text(r.text)
    hops = " → ".join(f"{h.status_code} {h.headers.get('Location', '')}" for h in r.history)
    return (f"{method} {path} → {r.status_code} {ctype[:40]}" + (f"\nredirects: {hops}" if hops else "")
            + f"\nfinal address: {r.url.replace(base, '') or '/'}\n\n{_redact(body[:MAX_BODY])}")


def dbg_logs(job, args):
    """The beta's own error logs: the app log (Laravel / Django / Node…), the website error log, PHP worker messages."""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    lines = max(20, min(int(args.get("lines") or 80), 300))
    code, out = _sh(beta.server, f"""H={_q(n['host'])}; D={_q(beta.deploy_path)}; U={_q(beta.site_user)}
echo "--- app log:"; F=$(ls -1t "$D"/storage/logs/*.log "$D"/backend/logs/*.log "$D"/logs/*.log 2>/dev/null | head -1)
if [ -n "$F" ]; then echo "($F)"; tail -n {lines} "$F"; else echo "(no app log file found)"; fi
echo "--- website error log:"; tail -n 20 /home/$H/logs/$H.error_log 2>/dev/null || true
echo "--- PHP workers of this site:"; grep -F "[UID:$(id -u "$U")]" /usr/local/lsws/logs/stderr.log 2>/dev/null | tail -n 15 || true
echo "--- services of this beta:"; for u in $(ls /etc/systemd/system/ | grep -F "aidash-{n['db'].replace('_', '-')}-"); do echo "[$u]"; journalctl -u "$u" -n 15 --no-pager 2>/dev/null; done
""")
    return _redact(out[-12000:])


CURL_EXIT = {6: "address not found", 7: "could not connect", 28: "timed out", 35: "TLS handshake failed",
             52: "empty reply — the server closed without answering", 56: "connection reset while receiving (crash / web-server drop)",
             92: "HTTP/2 stream error — the web server reset this request", 16: "HTTP/2 framing error"}


def dbg_probe(job, args):
    """Like beta_request, but through curl on the server with a chosen protocol (HTTP/1.1 or HTTP/2) — to tell an app
    error from a web-server / protocol problem (e.g. HTTP/2 POSTs reset, 2026-10-22). Shows curl's exit reason. Read only."""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    path = str(args.get("path") or "/").strip()
    if not path.startswith("/") or "//" in path or "\\" in path or any(ch in path for ch in "'\"`$ "):
        raise ToolError("path must be a plain path on the beta, starting with / (no spaces or quotes).")
    method = str(args.get("method") or "GET").upper()
    if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        raise ToolError("method: GET, HEAD, POST, PUT, PATCH or DELETE.")
    http = {"1.1": "--http1.1", "2": "--http2", "auto": ""}.get(str(args.get("http") or "auto"), "")
    data = args.get("form") or {}
    if not isinstance(data, dict):
        raise ToolError("form must be an object of fields.")
    auth = betas.beta_auth(beta) if args.get("password", True) else None
    clean = lambda t, k: str(t)[:k].replace("\n", " ").replace('"', "'")
    cfg = [f'url = "{beta.url.rstrip("/")}{path}"', f'request = "{method}"', "silent", "show-error", "max-time = 60",
           'write-out = "\\n---\\nstatus %{http_code} · http %{http_version} · %{size_download} bytes · %{time_total}s\\n"']
    if auth:
        cfg.append(f'user = "{auth[0]}:{auth[1]}"')
    cfg += [f'data-urlencode = "{clean(k, 60)}={clean(v, 500)}"' for k, v in data.items()]
    cfg += [f'header = "{clean(k, 60)}: {clean(v, 300)}"' for k, v in (args.get("headers") or {}).items()]
    tag = "AIDASH_" + secrets.token_hex(4)
    code, out = _sh(beta.server, f"curl {http} -K - <<'{tag}' > /tmp/aidash-probe.$$ 2>&1; X=$?; tail -c 5000 /tmp/aidash-probe.$$; "
                                 f"rm -f /tmp/aidash-probe.$$; echo; echo \"CURL_EXIT=$X\"\n" + "\n".join(cfg) + f"\n{tag}\n", timeout=90)
    m = re.search(r"CURL_EXIT=(\d+)", out)
    ex = int(m.group(1)) if m else -1
    body = out[:m.start()] if m else out
    meaning = "ok" if ex == 0 else CURL_EXIT.get(ex, "curl error")
    return f"curl exit {ex} ({meaning})\n{_redact(_page_text(body) if '<html' in body.lower() else body)[-4000:]}"


def dbg_access_log(job, args):
    """The beta's own access log: did the browser's request arrive at all? (time, address, method, path, status, protocol)"""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    lines = max(10, min(int(args.get("lines") or 60), 400))
    flt = str(args.get("filter") or "")[:80]
    code, out = _sh(beta.server, f"""H={_q(n['host'])}; L=/home/$H/logs/$H.access_log
[ -f "$L" ] || {{ echo "no access log"; exit 0; }}
tail -n 5000 "$L" | grep -F -- {_q(flt or ' ')} | tail -n {lines}
""")
    return _redact(out[-12000:]) or "(nothing matched)"


def dbg_webserver(job, args):
    """The beta's web-server settings (OpenLiteSpeed vhost: contexts, password lock, proxies, protocols), its .htaccess
    and the web server's own messages about this beta. Read only — changes to it go to the owner (report_problem)."""
    p, live, beta, n = _ctx(job)
    beta = _beta_ready(beta)
    code, out = _sh(beta.server, f"""H={_q(n['host'])}
echo "--- vhost.conf:"; sed -E 's#(keyFile|certFile|certChain).*#\\1 (hidden)#' /usr/local/lsws/conf/vhosts/$H/vhost.conf
echo "--- web root .htaccess:"; cat /home/$H/public_html/.htaccess 2>/dev/null
echo "--- web server messages for this beta:"; grep -F "$H" /usr/local/lsws/logs/error.log 2>/dev/null | grep -v "failed to authenticate" | tail -n 30
echo "--- OpenLiteSpeed version:"; /usr/local/lsws/bin/lshttpd -v 2>&1 | head -1
""")
    return _redact(out[-14000:])


WEB_DEBUG = {
    "beta_probe": (dbg_probe, "Send a request to the beta with curl, choosing the protocol (http: '1.1', '2' or 'auto') and "
                              "whether to send the beta password — tells an app error from a web-server / protocol problem "
                              "(curl's exit reason is explained).",
                   {"method": {"type": "string"}, "path": {"type": "string"}, "http": {"type": "string", "enum": ["1.1", "2", "auto"]},
                    "password": {"type": "boolean"}, "form": {"type": "object"}, "headers": {"type": "object"}}),
    "beta_access_log": (dbg_access_log, "The beta's access log: did the owner's request reach the server at all? Status and protocol per request.",
                        {"filter": {"type": "string"}, "lines": {"type": "integer"}}),
    "beta_webserver": (dbg_webserver, "The beta's web-server settings (vhost, contexts, password lock, proxies, protocols), its "
                                      ".htaccess and the web server's messages. Read only: a needed change goes to the owner with report_problem.", {}),
}


ASK_WAIT_SECONDS = 1800


def _wait_answer(qid, seconds):
    """Wait for the owner's answer (the dashboard chat card). Returns the row (answered or not). Tests replace this."""
    import time
    from app.models import ChangeQuestion
    end = time.time() + seconds
    while True:
        db.session.expire_all()
        row = db.session.get(ChangeQuestion, qid)
        if row is None or row.status != "open" or time.time() >= end:
            return row
        time.sleep(3)


def _ask(job, args):
    """Ask the owner (decision or permission) and wait — a card in the change's chat + bell + Telegram.
    Returns (row, text): row is the ChangeQuestion (answered, expired or still open) or None; text is what Claude is told.
    args["code"] is shown verbatim in its own box (never through the Markdown filter): use it for anything the owner must
    read exactly, such as a command."""
    from app.alerts import raise_alert
    from app.models import Change, ChangeQuestion
    cid = (job.context or {}).get("change_id")
    change = db.session.get(Change, cid) if cid else None
    if change is None:
        raise ToolError("ask_owner works only while making a change.")
    q = str(args.get("question") or "").strip()[:2000]
    if not q:
        raise ToolError("question is empty.")
    if ChangeQuestion.query.filter_by(change_id=change.id, status="open").count() >= 3:
        raise ToolError("Three questions are already waiting for the owner. Wait for their answers before asking more.")
    opts = [str(o).strip()[:200] for o in (args.get("options") or []) if str(o).strip()][:6]
    kind = "root" if args.get("root") else ("permission" if args.get("permission") else "question")
    code = str(args.get("code") or "")[:3000] or None
    row = ChangeQuestion(change_id=change.id, kind=kind, question=q, options=opts, code=code)
    db.session.add(row)
    change.agent_note = ("Waiting for your permission and authenticator code" if kind == "root"
                         else "Waiting for your permission" if kind == "permission" else "Waiting for your answer")[:255]
    db.session.commit()
    raise_alert("approval", f"{job.project.name}: Claude {'asks to run a ROOT command' if kind == 'root' else 'asks for permission' if kind == 'permission' else 'asks'} — {q[:200]}", job.project)
    row = _wait_answer(row.id, ASK_WAIT_SECONDS)
    if row is not None and row.status == "answered":
        who = row.answered_by.name if row.answered_by else "The owner"
        return row, f"{who} answered: {row.answer}"
    if row is not None and row.status == "open":
        expired = ChangeQuestion.query.filter_by(id=row.id, status="open").update({"status": "expired", "ticket": None})
        db.session.commit()
        if not expired:                     # the owner answered in the last moment: that answer stands
            db.session.expire_all()
            row = db.session.get(ChangeQuestion, row.id)
            if row is not None and row.status == "answered":
                who = row.answered_by.name if row.answered_by else "The owner"
                return row, f"{who} answered: {row.answer}"
    return row, ("No answer within 30 minutes. Do NOT do the thing that needed it; finish what you safely can and say in "
                 "your note what is still waiting for the owner.")


def ask_owner(job, args):
    """Claude's question to the owner. Only the question, the options and "permission" come from Claude: the card kinds that carry
    a verbatim command (server_command, root_command, tool permissions) are made by the dashboard's own code, never by these arguments."""
    clean = {"question": args.get("question"), "options": args.get("options"), "permission": bool(args.get("permission"))}
    return _ask(job, clean)[1]


def _decision(row):
    """The button the owner pressed ("Run it", "Deny", …) — a note typed beside it never changes it. "" = not answered."""
    if row is None or row.status != "answered":
        return ""
    return (row.answer or "").split(" — ")[0].strip()


def _run_alive(job):
    """Is this still the running change job? A card answered after Claude was stopped must not act any more."""
    db.session.refresh(job)
    return job.state == "running" and (job.context or {}).get("mode") == "change_debug"


# ---------- server_command: the owner approves ONE exact command, it runs as the live site's own Linux user ----------
SERVER_CMD_MAX = 1500            # characters; the owner reads the whole command in the card
SERVER_CMD_ASKS = 4              # requests per run: a looping Claude must not wear the owner down
SECRET_HINT = re.compile(r"(\.env\b|wp-config|\.ssh|id_rsa|id_ed25519|id_ecdsa|\.pem\b|printenv|(^|[\s;&|(])env(\s|$)|\bcredentials\b|"
                         r"\bshadow\b|totp\.json|\.my\.cnf|\.pgpass|dashboard\.env|app\.db|/etc/ssl/private|\.aws\b|\.kube\b)", re.I)


VISIBLE_LINES = 12


def _hard_to_read(cmd, ascii_only=False):
    """Why the owner could not take this command in at a glance (hidden characters, a tail pushed out of sight by blank lines or
    spaces, look-alike letters), or "". The card shows the command exactly; this keeps what is shown readable."""
    import unicodedata
    if any(unicodedata.category(ch) in ("Cc", "Cf") and ch not in "\n\t" for ch in cmd) or "```" in cmd:
        return "hidden or control characters (or a ``` line)"
    if ascii_only and not all(ch in "\n\t" or " " <= ch <= "~" for ch in cmd):
        return "characters that are not plain ASCII (look-alike letters can hide what a command does)"
    if len(cmd.split("\n")) > VISIBLE_LINES:
        return f"more than {VISIBLE_LINES} lines"
    if re.search(r"\n[ \t]*\n[ \t]*\n[ \t]*\n", cmd) or re.search(r"[ \t]{40}", cmd):
        return "long runs of blank lines or spaces"
    return ""


def _kv(out):
    return dict(l.split("=", 1) for l in out.splitlines() if "=" in l and l.split("=", 1)[0].isupper())


def _site_user(live):
    """The Linux user that owns the live folder, if it is a safe, ordinary account: (user, "") or ("", why not)."""
    import getpass
    from flask import current_app
    code, out = remote.run(live.server, f"P={_q(live.deploy_path)}\nU=$(stat -L -c %U \"$P\" 2>/dev/null)\necho \"USER=$U\"\n"
                                        "if [ -n \"$U\" ]; then echo \"UID=$(id -u \"$U\" 2>/dev/null)\"; "
                                        "if runuser -u \"$U\" -- sudo -n true >/dev/null 2>&1; then echo SUDO=yes; else echo SUDO=no; fi; fi\n",
                           timeout=60)
    kv = _kv(out)
    user, uid = kv.get("USER", ""), kv.get("UID", "")
    if not re.match(r"^[a-z_][a-z0-9_.-]{0,31}$", user) or user == "root":
        return "", "This live site's code belongs to root (or its owner could not be read)"
    if not uid.isdigit() or int(uid) < 1000:
        return "", f"This live site's code belongs to “{user}”, a system account"
    if kv.get("SUDO") == "yes":
        return "", f"This live site's user “{user}” can use sudo without a password"
    try:
        me = getpass.getuser()
    except Exception:
        me = ""
    if user in (me, current_app.config.get("TEST_RUNNER_USER")):
        return "", f"This live site's code belongs to the dashboard's own user “{user}”"
    return user, ""


def _open_permission(ctx):
    """Is a permission / root-command card of this change still waiting for the owner?"""
    from app.models import ChangeQuestion
    return ChangeQuestion.query.filter(ChangeQuestion.change_id == ctx.get("change_id"), ChangeQuestion.status == "open",
                                       ChangeQuestion.kind.in_(("permission", "root"))).first() is not None


def t_server_command(job, args):
    """One command on the project's LIVE site, as that site's own Linux user (never root), inside the live folder — only after
    the owner has read the exact command in a card and pressed "Run it". Returns the command's exit code and output."""
    import hashlib
    import unicodedata
    from app.models import ChangeQuestion
    p = job.project
    live = p.env("live")
    if live is None or live.server is None or not live.deploy_path:
        raise ToolError("This project has no live server / folder set in the dashboard.")
    cmd = (args.get("command") or "").strip()
    why = " ".join(str(args.get("why") or "").split())[:300]
    try:
        timeout = max(10, min(int(args.get("timeout_seconds") or 600), 1800))
    except (TypeError, ValueError):
        timeout = 600
    if not cmd:
        raise ToolError("command is empty.")
    if len(cmd) > SERVER_CMD_MAX:
        raise ToolError(f"The command is longer than {SERVER_CMD_MAX} characters — too long for the owner to read in full. Split it.")
    problem = _hard_to_read(cmd)
    if problem:
        raise ToolError(f"The command has {problem}, so the owner could not see exactly what runs. Write it as plain visible text, in a few lines.")
    if BAD_COMMAND.search(cmd):
        raise ToolError("That command needs extra rights (sudo / su / chown / systemctl / ssh / scp / crontab). This tool runs as the "
                        "site's own user only. Use report_problem with the exact change; the owner or the VPS Claude does root work.")
    if live.server.ssh_user != "root":
        raise ToolError("The dashboard's SSH user for this server is not root, so it can't switch to the site's own user.")
    ctx = dict(job.context or {})
    digest = hashlib.sha1(cmd.encode()).hexdigest()[:12]
    if digest in (ctx.get("server_cmd_denied") or []):
        raise ToolError("The owner already denied this exact command in this run. Carry on without it.")
    if int(ctx.get("server_cmd_asks") or 0) >= SERVER_CMD_ASKS:
        raise ToolError(f"{SERVER_CMD_ASKS} command requests were already made in this run. Finish what you can and say in your note what is still waiting.")
    if _open_permission(ctx):
        raise ToolError("Another permission request is still waiting for the owner. Wait for it before asking again.")
    try:
        user, problem = _site_user(live)
    except remote.RemoteError as e:
        raise ToolError(f"Could not reach the server: {e}")
    if problem:
        raise ToolError(problem + " — Claude never works as root, a system account or a user with sudo. Use report_problem with the exact command and why.")

    host = live.url.split("//")[-1].split("/")[0]
    lines = [f"⚠ LIVE site — {p.name} ({host})",
             f"Claude wants to run ONE command as the site's own user “{user}” (not root), in {live.deploy_path}. "
             "This is not the dashboard's Go Live: no snapshot and no automatic switch-back.",
             f"Why: {why or '—'}"]
    if LIVE_DANGER.search(cmd):
        lines.append("⚠ This command can change the live database or git history.")
    if SECRET_HINT.search(cmd):
        lines.append("⚠ This command may print passwords or keys. Its output goes to Claude: KEY=value lines are hidden, other shapes may not be.")
    ctx["server_cmd_asks"] = int(ctx.get("server_cmd_asks") or 0) + 1
    job.context = ctx
    db.session.commit()
    row, text = _ask(job, {"question": "\n\n".join(lines), "code": cmd, "options": ["Deny", "Run it"], "permission": True})
    if not _run_alive(job):
        return "The run ended before the owner answered, so nothing was executed."
    if _decision(row) != "Run it":
        ctx = dict(job.context or {})
        ctx["server_cmd_denied"] = sorted(set((ctx.get("server_cmd_denied") or []) + [digest]))
        job.context = ctx
        db.session.commit()
        return f"The owner did not allow this command ({text[:200]}). Carry on without it."

    safe_cmd = _redact(cmd)
    note(job, f"Claude: running on live — {safe_cmd[:160]}")
    tag = "AIDASH_" + secrets.token_hex(4)
    script = (f"set +e\nrunuser -u {_q(user)} -- timeout --kill-after=30 {timeout} bash -se <<'{tag}'\nset -o pipefail\n"
              f"cd {_q(live.deploy_path)}\nexport HOME=$(getent passwd {_q(user)} | cut -d: -f6)\n"
              f"export PATH=/opt/aidash-node/bin:/usr/local/bin:/usr/bin:/bin\n{cmd}\n{tag}\nrc=$?\necho \"AIDASH_EXIT=$rc\"\nexit 0\n")
    try:
        _, out = remote.run(live.server, script, timeout=timeout + 90)
    except remote.RemoteError as e:
        log("Claude command on live: connection lost", project_id=p.id, user=job.by_user, details=f"as {user}: {safe_cmd[:400]} — {e}")
        db.session.commit()
        raise ToolError(f"The connection to the server failed or timed out while the command ran ({e}). It may have partly run — "
                        "do NOT run it again without asking the owner.")
    m = re.search(r"AIDASH_EXIT=(\d+)\s*$", out)
    rc = int(m.group(1)) if m else None
    body = out[:m.start()] if m else out
    log("Claude ran a command on live", project_id=p.id, user=job.by_user,
        details=f"as {user} in {live.deploy_path}: {safe_cmd[:500]} → exit {rc if rc is not None else '?'}")
    db.session.commit()
    head = ("exit code unknown" if rc is None else f"timed out after {timeout} s and was stopped (it may have partly run)" if rc in (124, 137) else f"exit code {rc}")
    return f"{head}\n{_redact(body)[-6000:]}"


# ---------- root_command: ANY command as root on this server — the owner reads it AND types a fresh authenticator code ----------
ROOT_CMD_MAX = 1500
ROOT_CMD_ASKS = 3
ROOT_DANGER = re.compile(r"(\brm\s+-\w*[rf]|\bmkfs|\bdd\s+if=|>\s*/dev/(sd|nvme|vd)|\b(shutdown|reboot|halt|poweroff)\b|\bchmod\s+-\w*R|"
                         r"\bchown\s+-\w*R|\buserdel\b|\bdrop\s+(database|table)\b|\|\s*(ba)?sh\b|\biptables\b|\bfirewall-cmd\b|\bpasswd\b|"
                         r"authorized_keys|\bvisudo\b|/etc/sudoers|\bsystemctl\s+(stop|disable|mask)\b)", re.I)
_ASK_LOCK = threading.Lock()         # the checks "no other card is open" and "asks so far" + their update must not interleave


def _runner_is_separate():
    """Claude Code must run as ANOTHER Linux user than the dashboard's: otherwise its shell could read the database and
    reach the root service's socket itself, and the whole idea (Claude never holds root) would not hold."""
    import getpass
    from flask import current_app
    runner = current_app.config.get("TEST_RUNNER_USER")
    try:
        me = getpass.getuser()
    except Exception:
        me = ""
    return bool(runner) and runner != me


def t_root_command(job, args):
    """ONE command as root on the dashboard's own server, in / — only after the owner has read the exact command in a card and typed
    a fresh code from the authenticator app (checked by the root service, not by this process). Returns the exit code and output."""
    import hashlib
    from app import rootd
    from app.models import Change, ChangeMessage
    p = job.project
    cmd = (args.get("command") or "").strip()
    why = re.sub(r"[`*_#>\[\]<|~\\]", "", " ".join(str(args.get("why") or "").split()))[:300]      # plain words: no Markdown tricks in the card
    try:
        timeout = max(10, min(int(args.get("timeout_seconds") or 600), 1800))
    except (TypeError, ValueError):
        timeout = 600
    if not cmd:
        raise ToolError("command is empty.")
    if len(cmd) > ROOT_CMD_MAX:
        raise ToolError(f"The command is longer than {ROOT_CMD_MAX} characters — too long for the owner to read in full. Split it.")
    problem = _hard_to_read(cmd, ascii_only=True)
    if problem:
        raise ToolError(f"The command has {problem}, so the owner could not see exactly what runs. Write it as plain ASCII text, in a few lines.")
    if not _runner_is_separate():
        raise ToolError("Claude Code does not run as a separate low-rights Linux user on this server, so root commands are switched off "
                        "(its shell could reach the root service itself). Use report_problem with the exact command and why.")
    st = rootd.status()
    if not st["available"]:
        raise ToolError("The root service is not running on the server, so no root command is possible. Use report_problem with the exact command and why.")
    if not st["enrolled"]:
        raise ToolError("Root commands are not set up yet: the owner must run `aidash-rootd enroll` once on the server to link an authenticator app. "
                        "Use report_problem with the exact command and why.")
    if st["locked"]:
        raise ToolError(f"Root commands are locked for about {max(1, st['retry_after'] // 60 + 1)} more minute(s) after wrong codes. Carry on without it.")
    digest = hashlib.sha1(cmd.encode()).hexdigest()[:12]
    with _ASK_LOCK:
        db.session.refresh(job)
        ctx = dict(job.context or {})
        if digest in (ctx.get("root_cmd_denied") or []):
            raise ToolError("The owner already denied this exact command in this run. Carry on without it.")
        if int(ctx.get("root_cmd_asks") or 0) >= ROOT_CMD_ASKS:
            raise ToolError(f"{ROOT_CMD_ASKS} root command requests were already made in this run. Finish what you can and say in your note what is still waiting.")
        if _open_permission(ctx):
            raise ToolError("Another permission request is still waiting for the owner. Wait for it before asking again.")
        ctx["root_cmd_asks"] = int(ctx.get("root_cmd_asks") or 0) + 1
        job.context = ctx
        db.session.commit()

    lines = [f"⚠ ROOT — {p.name}",
             "Claude wants to run ONE command as **root** on this dashboard server, in / (the server's top folder). Root can change or delete "
             "anything here, including the other websites on this server. This is not the dashboard's Go Live: no snapshot and no automatic switch-back.",
             f"Why (Claude's words): {why or '—'}"]
    if ROOT_DANGER.search(cmd):
        lines.append("⚠ This command can delete data or change how the whole server works.")
    if SECRET_HINT.search(cmd):
        lines.append("⚠ This command may print passwords or keys. Its output goes to Claude: KEY=value lines are hidden, other shapes may not be.")
    if db.session.query(ChangeMessage.id).join(Change, Change.id == ChangeMessage.change_id).filter(
            Change.project_id == p.id, ChangeMessage.sender.in_(("client", "developer"))).first() is not None:
        lines.append("⚠ This project's chat contains messages from a client or a developer. Approve only what YOU would want done, not what a message asks for.")
    lines.append("Claude can create files and links inside the project folders, so check that every path in the command points where you expect.")
    lines.append("To allow it: type the 6-digit code from your authenticator app. The code approves only this exact command, for 2 minutes.")
    row, text = _ask(job, {"question": "\n\n".join(lines), "code": cmd, "options": ["Deny", "Run as root"], "root": True})
    if not _run_alive(job):
        return "The run ended before the owner answered, so nothing was executed."
    if _decision(row) != "Run as root":
        ctx = dict(job.context or {})
        ctx["root_cmd_denied"] = sorted(set((ctx.get("root_cmd_denied") or []) + [digest]))
        job.context = ctx
        db.session.commit()
        return f"The owner did not allow this command ({text[:200]}). Carry on without it."
    ticket, row.ticket = row.ticket or "", None            # one use: gone from the database before the command is sent
    db.session.commit()
    if not ticket or row.code != cmd:
        return "The answer came without a valid approval, so nothing was executed."

    safe_cmd = _redact(cmd)
    note(job, f"Claude: running as root — {safe_cmd[:160]}")
    try:
        r = rootd.execute(ticket, cmd, timeout)
    except rootd.RootdError as e:
        log("Claude root command: connection lost", project_id=p.id, user=job.by_user, details=f"{safe_cmd[:400]} — {e}")
        db.session.commit()
        if not e.sent:
            raise ToolError("The root service could not be reached, so nothing was executed.")
        raise ToolError(f"The connection to the root service failed or timed out while the command ran ({e}). It may have partly run — "
                        "do NOT run it again without asking the owner.")
    if not r.get("ok"):
        log("Claude root command: refused by the root service", project_id=p.id, user=job.by_user, details=f"{safe_cmd[:400]} — {str(r.get('error'))[:200]}")
        db.session.commit()
        return f"The root service did not run it ({str(r.get('error'))[:200]}). Nothing was executed."
    rc = r.get("exit")
    log("Claude ran a ROOT command", project_id=p.id, user=job.by_user,
        details=f"{safe_cmd[:500]} → {'timed out and was stopped' if r.get('timed_out') else 'exit ' + (str(rc) if rc is not None else '?')}")
    db.session.commit()
    head = (f"timed out after {timeout} s and was stopped (it may have partly run)" if r.get("timed_out")
            else "exit code unknown" if rc is None else f"exit code {rc}")
    return f"{head}\n{_redact(str(r.get('output') or ''))[-6000:]}"


RUN_WIDE_CONNECTORS = ("mcp__claude_ai_Mobbin__",)     # connectors that may be allowed for a whole run (read-only)


def permission_prompt(job, args):
    """Claude Code's permission hook (--permission-prompt-tool): a tool outside the allowed list (e.g. a claude.ai
    connector like Mobbin) is shown to the owner as a card — Deny / Allow once / Allow for this run (Deny first, so pressing
    Enter in the note field never allows anything)."""
    tool = str(args.get("tool_name") or "?")[:120]
    inp = args.get("input") if isinstance(args.get("input"), dict) else {}
    ctx = dict(job.context or {})
    # a claude.ai connector other than Mobbin (a read-only design library) can write or send things: each call is asked
    # separately — never "for this run" — and shows its whole input
    strict = tool.startswith("mcp__claude_ai_") and not tool.startswith(RUN_WIDE_CONNECTORS)
    if ctx.get("trusted") and tool.startswith("mcp__claude_ai_"):
        # a project whose chat holds only the owner's text: the account's connectors work at full power, no card
        # (owner, 2026-11-04). The call still shows in the run's steps.
        return json.dumps({"behavior": "allow", "updatedInput": inp})
    if tool in (ctx.get("allowed_tools") or []) and not strict:
        return json.dumps({"behavior": "allow", "updatedInput": inp})
    shown = _redact(json.dumps(inp, ensure_ascii=False)[:3000 if strict else 800])
    row, text = _ask(job, {"question": f"Claude wants to use the tool **{tool}**.", "code": shown,
                           "options": ["Deny", "Allow once"] if strict else ["Deny", "Allow once", "Allow for this run"], "permission": True})
    if not _run_alive(job):
        return json.dumps({"behavior": "deny", "message": "The run ended before the owner answered."})
    decision = _decision(row)
    if decision in ("Allow once", "Allow for this run"):
        if decision == "Allow for this run" and not strict:
            ctx = dict(job.context or {})
            ctx["allowed_tools"] = sorted(set((ctx.get("allowed_tools") or []) + [tool]))
            job.context = ctx
            db.session.commit()
        return json.dumps({"behavior": "allow", "updatedInput": inp})
    return json.dumps({"behavior": "deny", "message": f"The owner did not allow {tool} ({text[:200]}). Carry on without it."})


BETA_DEBUG_TOOLS = {
    "permission_prompt": (permission_prompt, "Used by Claude Code itself to ask the owner before a tool outside the allowed list. Don't call it directly.",
                          {"tool_name": {"type": "string"}, "input": {"type": "object"}, "tool_use_id": {"type": "string"}}),
    "ask_owner": (ask_owner, "Ask the owner and WAIT for the answer: a decision you can't make, or permission (permission=true) "
                             "for something outside your rules. Give short options when you can.",
                  {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}, "permission": {"type": "boolean"}}),
    "beta_status": (dbg_status, "Facts: the beta address and which version it runs. Call first when the owner reports a problem on beta.", {}),
    "beta_request": (dbg_request, "Open a page or API on the BETA site, like a browser (beta password + cookies kept for this run). "
                                  "Use it to reproduce what the owner saw. POST forms get the CSRF token by themselves.",
                     {"method": {"type": "string"}, "path": {"type": "string"}, "form": {"type": "object"}, "json": {"type": "object"},
                      "headers": {"type": "object"}, "csrf_from": {"type": "string"}, "follow_redirects": {"type": "boolean"}}),
    "beta_logs": (dbg_logs, "The beta's error logs (app log, website errors, PHP worker crashes, its services). Read them right after a failing request.",
                  {"lines": {"type": "integer"}}),
    **WEB_DEBUG,
    "beta_run": (t_run, "Run one command on the BETA as its website user, in the beta folder (e.g. php artisan route:list, "
                        "php artisan tinker --execute=\"…\", php -l file). Beta only — never live.",
                 {"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}),
    "server_command": (t_server_command, "Ask the owner to let you run ONE command on the project's LIVE site, as the site's own Linux "
                                         "user (never root), inside the live folder: look at logs or files, run a deploy or debug "
                                         "command that your own shell can't. The owner reads the exact command in a card and decides; "
                                         "this WAITS for the answer. Give a short 'why'. Not for sudo / systemctl / ssh / chown / "
                                         "crontab — use report_problem for those.",
                       {"command": {"type": "string"}, "why": {"type": "string"}, "timeout_seconds": {"type": "integer"}}),
    "root_command": (t_root_command, "Ask the owner to let you run ONE command as ROOT on this dashboard server (in /): a service restart, a "
                                     "web-server or system setting, a package, a file only root can touch. The owner reads the exact command "
                                     "in a card and types a fresh authenticator code for it; this WAITS for the answer. Give a short 'why'. "
                                     "This server also runs other clients' websites: ask for the smallest command that does the job, with "
                                     "absolute paths. Use server_command instead when the site's own user is enough.",
                     {"command": {"type": "string"}, "why": {"type": "string"}, "timeout_seconds": {"type": "integer"}}),
    "report_problem": (None, "A tool is broken or can't do what is needed: tell the owner, with your proposed fix.",
                       {"tool": {"type": "string"}, "problem": {"type": "string"}, "proposed_fix": {"type": "string"}}),
}


def ls_status(job, args):
    ctx, live = _rx(job)
    return json.dumps(ctx.get("facts") or {}, indent=1)


# "live_steps" mode: Claude only READS the running live release (as the web server's user) and saves the steps — it
# runs nothing on live; the next Go Live tries the steps in a new release, and a failing step never switches live
LIVE_STEPS_TOOLS = {
    "status": (ls_status, "Facts about the live site: its release folder, Python venvs, services (how they start), build files, web root, shared items, and the beta's steps. Call first.", {}),
    "read_file": (rx_read_file, "Read a file of the running live release (not the shared settings / uploads / data).", {"path": {"type": "string"}}),
    "save_recipe": (rx_save_recipe, "Save the live steps (one shell command per item; '@publish FOLDER' = built web files → the web root).", {"steps": {"type": "array", "items": {"type": "string"}}}),
    "report_problem": (None, "Something can't be worked out from here: tell the owner, with your proposed fix.", {"tool": {"type": "string"}, "problem": {"type": "string"}, "proposed_fix": {"type": "string"}}),
}


RELEASE_TOOLS = {
    "status": (rx_status, "The new release folder, the steps that failed and the end of their output. Call first.", {}),
    "run": (rx_run, "Run one command as the site's user inside the NEW release folder (install, build, cache). No database commands.", {"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}),
    "read_file": (rx_read_file, "Read a file of the new release (not the shared settings / uploads / data).", {"path": {"type": "string"}}),
    "web_errors": (rx_web_errors, "The live site's recent web-server errors.", {}),
    "save_recipe": (rx_save_recipe, "Save the corrected live steps (one shell command per item).", {"steps": {"type": "array", "items": {"type": "string"}}}),
    "report_problem": (None, "Something can't be fixed from here: tell the owner, with your proposed fix.", {"tool": {"type": "string"}, "problem": {"type": "string"}, "proposed_fix": {"type": "string"}}),
}


TOOLS = {
    "status": (t_status, "Facts about this project, its live site (folder, files, settings files, SQLite files, services, PHP / Node versions) and its beta. Call first.", {}),
    "make_website": (t_make_website, "Create the beta website (SSL + password lock). Safe to call again.", {"php": {"type": "string", "description": "Only if live's PHP version can't be read, e.g. 8.3"}}),
    "copy_database": (t_copy_database, "Copy live's data into the beta. engine 'mysql' (own beta database) or 'sqlite' (give live_file, a path inside the live folder; beta_file defaults to the same path). Live is only read.",
                      {"engine": {"type": "string", "enum": ["mysql", "sqlite"]}, "live_file": {"type": "string"}, "beta_file": {"type": "string"}}),
    "put_code": (t_put_code, "Copy the beta branch's code into the beta's code folder (settings files, dependencies, storage and database files are kept). place='webroot' (PHP / Laravel / static: served from the web root) or place='app' (code outside the web root — use for backends and single-page apps; then publish the built files).",
                 {"place": {"type": "string", "enum": ["webroot", "app"]}}),
    "publish": (t_publish, "Copy a built folder (e.g. web/dist) from the code folder into the beta's web root. Only with place='app'.", {"folder": {"type": "string"}}),
    "write_env": (t_write_env, "Make a beta settings file from live's (default .env; for a backend in a subfolder pass file, e.g. backend/.env). Beta address, beta database, safe values and empty secrets are set for you; add other keys in set.",
                  {"file": {"type": "string"}, "from_live": {"type": "string"}, "set": {"type": "object"}}),
    "run": (t_run, "Run one shell command as the beta website's user inside the beta folder (composer, npm, pip, artisan, migrations, builds, tests). Returns exit code and output. No sudo.",
            {"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}),
    "read_file": (t_read_file, "Read a file in the beta folder (not settings files).", {"path": {"type": "string"}}),
    "write_file": (t_write_file, "Write a file in the beta's code folder, or in its web root with the prefix 'web:' (e.g. web:.htaccess for routing / an API proxy). Not settings files — use write_env.", {"path": {"type": "string"}, "content": {"type": "string"}}),
    "start_service": (t_start_service, "Run a backend (Flask, Django, FastAPI, Node…) as a service on its own local port ($PORT, returned). command runs in workdir (relative to the beta folder). Then add an .htaccess proxy rule with write_file, e.g. RewriteRule ^api/(.*)$ http://127.0.0.1:PORT/api/$1 [P,L].",
                      {"name": {"type": "string"}, "command": {"type": "string"}, "workdir": {"type": "string"}}),
    "service_logs": (t_service_logs, "The last log lines of a service made with start_service.", {"name": {"type": "string"}}),
    "web_errors": (t_web_errors, "The web server's recent error lines for this beta (why a page or a proxy gives 4xx/5xx).", {}),
    "check_site": (t_check_site, "Open beta pages (with the beta password) and report the answer codes; also checks that .env and .git are hidden.", {"paths": {"type": "array", "items": {"type": "string"}}}),
    "save_recipe": (t_save_recipe, "Save the steps that make a new beta version work, to replay without you next time. Items are shell commands (run as the website user in the code folder), '@restart NAME' (a service) or '@publish FOLDER' (built files → web root).", {"steps": {"type": "array", "items": {"type": "string"}}}),
    "report_problem": (t_report_problem, "A tool is broken or can't do what is needed: tell the owner, with your proposed fix.", {"tool": {"type": "string"}, "problem": {"type": "string"}, "proposed_fix": {"type": "string"}}),
}
REQUIRED = {"ask_owner": ["question"], "beta_run": ["command"], "server_command": ["command", "why"], "root_command": ["command", "why"], "beta_request": ["path"], "beta_probe": ["path"], "publish": ["folder"], "run": ["command"], "read_file": ["path"], "write_file": ["path", "content"], "start_service": ["name", "command"],
            "service_logs": ["name"], "save_recipe": ["steps"], "report_problem": ["tool", "problem"]}


def _toolset(job):
    if job is not None and (job.context or {}).get("mode") == "release_fix":
        return RELEASE_TOOLS
    if job is not None and (job.context or {}).get("mode") == "live_steps":
        return LIVE_STEPS_TOOLS
    if job is not None and (job.context or {}).get("mode") == "change_debug":
        return BETA_DEBUG_TOOLS
    if job is not None and job.kind == "go_live":
        return {}
    return TOOLS


def tool_list(job=None):
    return [{"name": k, "description": v[1],
             "inputSchema": {"type": "object", "properties": v[2], "required": REQUIRED.get(k, []), "additionalProperties": False}}
            for k, v in _toolset(job).items()]


def call(job, name, args):
    """Run a tool for this job. Returns (text, is_error). Refusals and failures go back to Claude as text."""
    tools = _toolset(job)
    if name not in tools:
        return f"Unknown tool {name}.", True
    try:
        fn = tools[name][0] or TOOLS[name][0]
        return fn(job, args or {}), False
    except (ToolError, DeployError) as e:
        return str(getattr(e, "plain", None) or e), True
    except Exception as e:                       # never crash the request — Claude decides what to do next
        db.session.rollback()
        return f"The tool failed unexpectedly ({e.__class__.__name__}: {str(e)[:200]}). Try again or report_problem.", True
