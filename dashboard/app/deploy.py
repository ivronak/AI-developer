"""Beta deploy (F08), Live → Beta sync (F07) and the beta safety switches (F13).

Beta deploy:  rebuild the `beta` branch = default branch + every open change that is ready for beta, push it, then on the
              beta server: write the safety .env lines and run the project's deploy script on branch `beta`; check the
              beta site answers. Live is never touched.
Live → Beta:  stream a dump of the live database into the beta database (live is only read), then re-apply the beta
              safety .env lines so beta never messages real users or takes real money."""
import re
import shlex

import requests

from app import remote
from app import workspace as ws
from app.auth import log
from app.clock import now
from app.extensions import db
from app.jobs import note
from app.models import Change, Deployment

ON_BETA = ("passed", "client", "changes", "approved")
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

TEMPLATES = {
    "Laravel": """# Runs on the server inside the project folder. {branch} = beta or main.
git fetch --quiet origin
git checkout --quiet -B {branch} origin/{branch}
chmod 700 .git        # the web server must never serve the git history
composer install --no-interaction --prefer-dist --optimize-autoloader --no-dev
php artisan migrate --force
php artisan optimize:clear
php artisan config:cache
php artisan route:cache
php artisan view:cache
""",
    "Flask": """# Runs on the server inside the project folder. {branch} = beta or main.
git fetch --quiet origin
git checkout --quiet -B {branch} origin/{branch}
chmod 700 .git        # the web server must never serve the git history
./venv/bin/pip install --quiet -r requirements.txt
FLASK_APP=run.py ./venv/bin/flask db upgrade
sudo systemctl restart $(basename "$PWD")
""",
    "HTML": """# Runs on the server inside the site folder. {branch} = beta or main.
git fetch --quiet origin
git checkout --quiet -B {branch} origin/{branch}
chmod 700 .git        # the web server must never serve the git history
""",
}
DEFAULT_ENV_OFF = {
    "Laravel": "MAIL_MAILER=log\nQUEUE_CONNECTION=sync\n# add the project's own switches, e.g. SMS_ENABLED=false",
    "Flask": "MAIL_SUPPRESS_SEND=true\n# add the project's own switches, e.g. SMS_ENABLED=false",
}


class DeployError(Exception):
    """A step failed; .plain is shown to the owner as is."""
    def __init__(self, plain):
        super().__init__(plain)
        self.plain = plain


def template_for(project):
    for tech in project.tech_stack or []:
        if tech in TEMPLATES:
            return TEMPLATES[tech]
    return TEMPLATES["HTML"]


def default_env_off(project):
    for tech in project.tech_stack or []:
        if tech in DEFAULT_ENV_OFF:
            return DEFAULT_ENV_OFF[tech]
    return ""


# ---------- .env safety lines ----------
def parse_env_lines(text):
    """KEY=VALUE lines → [(key, value)]; comments / blanks skipped; bad keys refused."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise DeployError(f"“{line[:40]}” is not KEY=VALUE.")
        k, v = line.split("=", 1)
        k = k.strip()
        if not ENV_KEY.match(k):
            raise DeployError(f"“{k[:40]}” is not a valid .env name (capital letters, digits and _).")
        out.append((k, v.strip()))
    return out


def safety_lines(project, beta_env):
    """The .env lines beta must have for its current message / payment switches."""
    lines = []
    if beta_env.message_mode == "off":
        lines += parse_env_lines(project.beta_env_off)
    elif beta_env.message_mode == "test":
        lines += parse_env_lines(project.beta_env_test)
    if beta_env.payment_mode == "sandbox":
        lines += parse_env_lines(project.beta_env_sandbox)
    return lines


def env_script(path, pairs, filename=".env"):
    """Bash that sets each KEY=VALUE in <path>/<filename> (adds it if missing). Values are passed quoted — no injection."""
    f = shlex.quote(filename)
    parts = [f"cd {shlex.quote(path)}", f"EF={f}", 'test -f "$EF" || touch "$EF"']
    for k, v in pairs:
        parts.append(
            f"KEY={shlex.quote(k)}; VAL={shlex.quote(v)}; "
            "if grep -q \"^${KEY}=\" \"$EF\"; then "
            "awk -v k=\"$KEY\" -v v=\"$VAL\" 'BEGIN{FS=OFS=\"=\"} $1==k{print k\"=\"v; next} {print}' \"$EF\" > \"$EF.tmp\" && mv \"$EF.tmp\" \"$EF\"; "
            "else printf '%s=%s\\n' \"$KEY\" \"$VAL\" >> \"$EF\"; fi")
    return "set -euo pipefail\n" + "\n".join(parts) + "\n"


# ---------- beta branch ----------
def developer_commits(root, project, base, remote_branches):
    """Commits on origin/beta that are neither on the default branch nor on any change branch of this project — i.e. a
    developer pushed them to beta by hand. Rebuilding beta from scratch would wipe them."""
    if "origin/beta" not in remote_branches:
        return []
    known = {c.branch for c in Change.query.filter(Change.project_id == project.id, Change.branch.isnot(None)).all()}
    refs = [f"origin/{b}" for b in sorted(known) if f"origin/{b}" in remote_branches]
    out = ws.git(root, "log", "--no-merges", "--format=%h %an: %s", "origin/beta", "--not", f"origin/{base}", *refs)
    return [l for l in out.splitlines() if l.strip()]


def build_beta_branch(project, job):
    """beta = default branch + each ready change (merge, skip any that conflicts). Pushes it. Returns (sha, merged, skipped).
    Nobody's work is thrown away: when a developer pushed commits to beta, beta is updated on top of them (default branch and
    the ready changes are merged in, a normal push); otherwise beta is rebuilt from the default branch."""
    base = project.default_branch or "main"
    root = ws.prepare(project, "beta", base)                 # checkout (fetches everything)
    remote_branches = {l.strip() for l in ws.git(root, "branch", "-r").splitlines()}
    theirs = developer_commits(root, project, base, remote_branches)
    if theirs:
        msg = (f"A developer pushed {len(theirs)} commit{'s' if len(theirs) != 1 else ''} to beta — keeping them "
               f"(latest: {theirs[0][:80]}); beta is updated, not rebuilt")
        note(job, msg, msg)
        ws.git(root, "checkout", "--quiet", "-B", "beta", "origin/beta")
        try:
            ws.git(root, "merge", "--no-edit", "-m", f"Beta: update from {base}", f"origin/{base}")
        except ws.WorkspaceError:
            ws.git(root, "merge", "--abort", check=False)
            raise ws.WorkspaceError(f"the developer's work on beta conflicts with {base} — nothing was changed. "
                                    f"Merge {base} into beta by hand, then deploy beta again")
    else:
        ws.git(root, "checkout", "--quiet", "-B", "beta", f"origin/{base}")
    merged, skipped = [], []
    changes = Change.query.filter(Change.project_id == project.id, Change.status.in_(ON_BETA)).order_by(Change.id).all()
    for c in changes:
        if not c.branch or f"origin/{c.branch}" not in remote_branches:
            continue
        note(job, f"Adding change #{c.id} to beta")
        try:
            ws.git(root, "merge", "--no-ff", "--no-edit", "-m", f"Beta: change #{c.id} {c.title}", f"origin/{c.branch}")
            merged.append(c)
        except ws.WorkspaceError:
            ws.git(root, "merge", "--abort", check=False)
            skipped.append(c)
    ws.git(root, "push", "--quiet", *([] if theirs else ["--force"]), "origin", "beta", auth=True)
    return ws.git(root, "rev-parse", "--short", "HEAD").strip(), merged, skipped


def _require(env, what):
    if env is None or env.server is None or not env.deploy_path:
        raise DeployError(f"Set the {what} server and folder first (project → Settings → Servers & deploy).")


def health_ok(url, auth=None):
    try:
        r = requests.get(url, timeout=15, allow_redirects=True, headers={"User-Agent": "ai-project-dashboard"}, auth=auth)
        return 200 <= r.status_code < 500, r.status_code
    except requests.RequestException:
        return False, 0


_LEAK_SIGNS = {"/.env": re.compile(r"^\s*[A-Z][A-Z0-9_]*\s*=", re.M), "/.git/config": re.compile(r"^\s*\[(core|remote)", re.M)}


def exposed(base_url, path, auth=None):
    """Is a secret file really readable from the web? A 200 alone proves nothing: single-page apps (React, Vue…) answer
    every unknown address with their own index.html. Only real settings / git content counts."""
    try:
        r = requests.get(base_url.rstrip("/") + path, timeout=15, allow_redirects=True, auth=auth,
                         headers={"User-Agent": "ai-project-dashboard"})
    except requests.RequestException:
        return False, 0
    if r.status_code != 200:
        return False, r.status_code
    head = r.text[:4000]
    if "html" in (r.headers.get("Content-Type") or "").lower() or head.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        return False, r.status_code
    return bool(_LEAK_SIGNS[path].search(head)), r.status_code


def run_beta_deploy(job):
    project = job.project
    beta = project.env("beta")
    _require(beta, "beta")
    script = project.deploy_script or template_for(project)
    note(job, "Building the beta branch")
    try:
        sha, merged, skipped = build_beta_branch(project, job)
    except ws.WorkspaceError as e:
        raise DeployError(f"Could not build the beta branch: {e}")
    note(job, "Applying beta safety switches (messages / payments)")
    pairs = safety_lines(project, beta)
    if pairs:
        code, out = remote.run(beta.server, env_script(beta.deploy_path, pairs), timeout=120)
        if code != 0:
            note(job, "", out)
            raise DeployError("Could not write the beta safety settings on the server — the deploy was stopped.")
    note(job, "Deploying on the beta server")
    body = "set -euo pipefail\n" + f"cd {shlex.quote(beta.deploy_path)}\n" + script.replace("{branch}", "beta")
    code, out = remote.run(beta.server, body)
    note(job, "", out)
    if code != 0:
        raise DeployError("The deploy script failed on the beta server — see the output below.")
    note(job, "Checking the beta site")
    ok, status = health_ok(beta.url)
    beta.current_commit = sha
    db.session.add(Deployment(project_id=project.id, environment_id=beta.id, commit=sha,
                              change_ids=[c.id for c in merged], by_user_id=job.by_user_id,
                              result="ok" if ok else "failed",
                              note=f"{len(merged)} change(s) on beta" + (f"; skipped (conflict): {', '.join('#' + str(c.id) for c in skipped)}" if skipped else "")))
    log("Beta deploy", project_id=project.id, user=job.by_user,
        details=f"{sha} · {len(merged)} change(s)" + (f" · conflicts: {', '.join('#' + str(c.id) for c in skipped)}" if skipped else ""))
    db.session.commit()
    if not ok:
        raise DeployError(f"Deployed, but the beta site does not answer ({'no answer' if status == 0 else status}).")
    msg = f"Beta updated · {sha} · {len(merged)} change{'s' if len(merged) != 1 else ''}"
    if skipped:
        msg += f" · skipped #{', #'.join(str(c.id) for c in skipped)} (conflicts with another change — ask Claude to update it)"
    note(job, msg)


DUMP = {"mysql": "mysqldump --single-transaction --quick --routines --no-tablespaces {db}",
        "postgres": "pg_dump --clean --if-exists --no-owner {db}"}
LOAD = {"mysql": "mysql {db}", "postgres": "psql --quiet {db}"}


def run_sync(job):
    project = job.project
    beta, live = project.env("beta"), project.env("live")
    _require(beta, "beta")
    _require(live, "live")
    if not beta.db_name or not live.db_name:
        raise DeployError("Set the beta and live database names first (project → Settings → Servers & deploy).")
    if beta.server_id == live.server_id and beta.db_name == live.db_name:
        raise DeployError("Beta and live point at the same database — refusing to copy.")
    engine = project.db_engine if project.db_engine in DUMP else "mysql"
    note(job, "Copying the live database into beta (live is only read)")
    err = remote.pipe(live.server, DUMP[engine].format(db=shlex.quote(live.db_name)),
                      beta.server, LOAD[engine].format(db=shlex.quote(beta.db_name)))
    if err:
        raise DeployError(err)
    pairs = safety_lines(project, beta)
    if pairs:
        note(job, "Applying beta safety switches (messages / payments)")
        code, out = remote.run(beta.server, env_script(beta.deploy_path, pairs), timeout=120)
        if code != 0:
            note(job, "", out)
            raise DeployError("Data copied, but the beta safety settings could not be written — check the beta .env now.")
    beta.last_sync_at = now()
    log("Sync", project_id=project.id, user=job.by_user, details="Live → Beta database copy")
    db.session.commit()
    note(job, "Beta now has a fresh copy of the live data")


def detect_script(domain):
    """A read-only bash script: finds the folder that serves `domain` on a server (Hostinger, CyberPanel, cPanel layouts) and prints
    PATH= / GIT= / DB= / ENGINE= lines. Of the project's .env / wp-config.php it reads ONLY the database name and type — never a password."""
    d = shlex.quote(domain)
    return f"""domain={d}
bare="${{domain#www.}}"
found=""
for c in "$HOME/domains/$bare/public_html" "/home/$bare/public_html" "/home/$domain/public_html" "$HOME/$bare/public_html" "$HOME/public_html/$bare"; do
  if [ -d "$c" ]; then found="$c"; break; fi
done
guess=""
if [ -z "$found" ] && [ -d "$HOME/public_html" ]; then found="$HOME/public_html"; guess=1; fi
[ -n "$found" ] || exit 3
case "$found" in */public) if [ -f "${{found%/public}}/artisan" ]; then found="${{found%/public}}"; fi;; esac
echo "PATH=$found"
[ -n "$guess" ] && echo "GUESS=1"
[ -e "$found/.git" ] && echo "GIT=1"
if [ -f "$found/.env" ]; then
  conn=$(grep -E '^DB_CONNECTION=' "$found/.env" | head -1 | cut -d= -f2- | tr -d "\\"' \\r")
  name=$(grep -E '^DB_DATABASE=' "$found/.env" | head -1 | cut -d= -f2- | tr -d "\\"' \\r")
  echo "ENGINE=$conn"
  echo "DB=$name"
elif [ -f "$found/wp-config.php" ]; then
  name=$(sed -n "s/.*define( *['\\"]DB_NAME['\\"] *, *['\\"]\\([^'\\"]*\\)['\\"].*/\\1/p" "$found/wp-config.php" | head -1)
  echo "ENGINE=mysql"
  echo "DB=$name"
fi
"""


def parse_detect(out):
    """The detect script's lines → {path, db, engine, git, guess}; every value is checked, anything odd is left out."""
    from app.blueprints.projects import DB_RE, PATH_RE
    kv = {}
    for line in (out or "").splitlines():
        if "=" in line and line.split("=", 1)[0] in ("PATH", "GUESS", "GIT", "DB", "ENGINE"):
            k, v = line.split("=", 1)
            kv[k] = v.strip()
    path = kv.get("PATH", "")
    if not PATH_RE.match(path) or ".." in path:
        return None
    engine = {"mysql": "mysql", "mariadb": "mysql", "pgsql": "postgres", "postgres": "postgres", "sqlite": "sqlite"}.get(kv.get("ENGINE", "").lower(), "")
    db_name = kv.get("DB", "")
    if engine == "sqlite":
        db_name = "sqlite:" + (db_name.split(path + "/", 1)[-1] if db_name.startswith(path + "/") else "database/database.sqlite")
        if db_name == "sqlite:" or db_name.startswith("sqlite:/"):
            db_name = "sqlite:database/database.sqlite"
    if not DB_RE.match(db_name) or ".." in db_name:
        db_name = ""
    return {"path": path, "db": db_name, "engine": engine, "git": kv.get("GIT") == "1", "guess": kv.get("GUESS") == "1"}


def detect_live(server, domain):
    """Look at a server and say which folder serves `domain` and which database its app uses. (None, reason) when nothing is found."""
    code, out = remote.run(server, detect_script(domain), timeout=60)
    if code == 3:
        return None, f"No folder for {domain} found on this server (looked for the Hostinger, CyberPanel and cPanel layouts)."
    found = parse_detect(out)
    if code != 0 or found is None:
        return None, "The server answered, but nothing usable was found."
    return found, ""
