"""Go Live v2 — release folders (CHANGES Step 6, owner 2026-10-17).

A live site's code folder (e.g. /home/site/public_html) becomes a symlink to /home/site/releases/<name>. Things that
must survive a release (settings files, uploads / storage, SQLite files) live once in /home/site/shared and are linked
into every release. A Go Live builds the NEXT release in its own folder while the current one keeps serving; only when
it is fully ready does the symlink switch (instant), then that site's PHP workers / services restart. If the site
doesn't answer, it switches straight back. Old releases stay for one-click rollback (last KEEP).

Converting a site to this layout is done ONCE per site, only when the owner asks (owner's answer 5 = A), with a backup
first and without taking the site down. Nothing here touches any other site."""
import base64
import gzip
import json
import posixpath
import re
import shlex

from app import deploy, remote
from app import workspace as ws
from app.alerts import raise_alert
from app.auth import log
from app.clock import now
from app.deploy import DeployError
from app.extensions import db
from app.jobs import note
from app.models import Change, ChangeMessage, Deployment

KEEP = 3
# never shared: dependencies, caches and build output (rebuilt per release)
DEPS = {".git", "node_modules", "vendor", "venv", ".venv", "__pycache__", "dist", "build", ".next", ".nuxt", ".cache",
        ".pytest_cache", ".mypy_cache"}
DB_FILE = re.compile(r"\.(db|sqlite3?)$")
PUBLISH_RE = re.compile(r"^[\w.-]+(/[\w.-]+)*$")

# Runs on the server (python3, root, read only): what in the live folder is runtime data, by comparing it with the files
# the project's repo tracks — untracked folders (uploads, logs, secrets…), settings files and SQLite databases. Loose
# untracked files elsewhere are listed but not kept (a release equals GitHub). Also: systemd services run from the folder.
SCAN_PY = r"""
import glob, os, re, sys
C, lst, deps = sys.argv[1], sys.argv[2], set(sys.argv[3].split(","))
tracked = set(x for x in open(lst, encoding="utf-8", errors="replace").read().split("\0") if x)
pre = set()
for t in tracked:
    parts = t.split("/")
    for i in range(1, len(parts)):
        pre.add("/".join(parts[:i]))
share, loose = set(), []
ALWAYS = ("storage", "public/uploads", "uploads", "media")
for top, dirs, files in os.walk(C):
    rel = os.path.relpath(top, C)
    rel = "" if rel == "." else rel
    keep = []
    for d in dirs:
        r = rel + "/" + d if rel else d
        if d in deps or r == "bootstrap/cache":
            continue
        if r in ALWAYS:
            share.add(r)
            continue
        if r in tracked:
            continue
        if os.path.islink(os.path.join(top, d)) or r not in pre:
            share.add(r)
            continue
        keep.append(d)
    dirs[:] = keep
    for f in files:
        r = rel + "/" + f if rel else f
        if r in tracked or re.search(r"\.(db|sqlite3?)-(wal|shm|journal)$", f):
            continue
        if f.startswith(".env") or re.search(r"\.(db|sqlite3?)$", f):
            share.add(r)
        elif len(loose) < 30:
            loose.append(r)
for s in ALWAYS:
    if os.path.isdir(os.path.join(C, s)):
        share.add(s)
for s in sorted(share):
    print("SHARE=" + s)
for s in loose:
    print("LOOSE=" + s)
pat = re.compile(re.escape(C.rstrip("/")) + r"(/|\s|$)")
for f in sorted(glob.glob("/etc/systemd/system/*.service")):
    try:
        lines = open(f, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        continue
    if any(re.match(r"\s*(WorkingDirectory|ExecStart)=", l) and pat.search(l.split("=", 1)[1]) for l in lines):
        print("UNIT=" + os.path.basename(f))
"""


def _q(v):
    return shlex.quote(str(v))


def _run(server, script, job, fail, timeout=900):
    code, out = remote.run(server, "set -euo pipefail\n" + script, timeout=timeout)
    if code != 0:
        note(job, "", out)
        raise DeployError(fail)
    return out


def _val(out, key):
    return next((l.split("=", 1)[1].strip() for l in out.splitlines() if l.startswith(key + "=")), "")


def _health(env):
    """The site answers (a locked rehearsal site is checked WITH its password, so a 401 never passes for 'up')."""
    from app.betas import beta_auth
    auth = beta_auth(env)
    ok, status = deploy.health_ok(env.url, auth=auth) if auth else deploy.health_ok(env.url)
    return (ok and status < 400), status          # 401 / 403 / 404 on the home page is not "up" (seen: 403 after a switch)


def paths(live):
    code = live.deploy_path.rstrip("/")
    root = posixpath.dirname(code)
    return code, root, f"{root}/releases", f"{root}/shared"


def host_of(env):
    return env.url.split("//")[-1].split("/")[0]


def restart_script(env, web_user, units=True):
    """Make the switch visible: that site's PHP workers restart (PHP caches compiled code per path) and its services."""
    names = " ".join(_q(u) for u in (env.restart_units or [])) if units else ""
    return (f"pkill -u {_q(web_user)} lsphp 2>/dev/null || true\n"
            + (f"systemctl restart {names}\nsleep 3\n" if names else "sleep 1\n"))


def _units_up(env, job):
    """Every service of the site is running again (a static page answering doesn't prove the backend started)."""
    if not env.restart_units:
        return True
    code, out = remote.run(env.server, "".join(f"systemctl is-active --quiet {_q(u)} || echo DOWN={_q(u)}\n" for u in env.restart_units))
    if "DOWN=" in out:
        note(job, "", out)
    return "DOWN=" not in out


def tracked_files(project):
    """Files the project's repo tracks (default branch), from the dashboard's own checkout."""
    base = project.default_branch or "main"
    try:
        root = ws.prepare(project, base, base)
        return [f for f in ws.git(root, "ls-files", "-z").split("\0") if f]
    except ws.WorkspaceError as e:
        raise DeployError(f"Could not read the project's files from GitHub: {e}")


# ---------- one-time conversion (owner's OK per site) ----------
def plan_convert(env, job, tracked):
    """What a conversion would do, read from the server: the code folder, the web root, its web-server user, what must
    be shared (runtime data, by comparing with the repo's files) and which services run from the folder."""
    code, root, rel, shared = paths(env)
    lst = base64.b64encode(gzip.compress("\0".join(tracked).encode())).decode()
    scan = base64.b64encode(SCAN_PY.encode()).decode()
    out = _run(env.server, f"""C={_q(code)}; H={_q(host_of(env))}; VH=/usr/local/lsws/conf/vhosts/$H/vhost.conf
[ -L "$C" ] && {{ echo "ALREADY=1"; exit 0; }}
[ -d "$C" ] || {{ echo "no folder $C" >&2; exit 1; }}
echo "WEBUSER=$(grep -m1 extUser "$VH" 2>/dev/null | awk '{{print $2}}')"
DR=$(grep -m1 '^docRoot' "$VH" 2>/dev/null | awk '{{print $2}}' | sed "s#\\$VH_ROOT#/home/$H#")
DR=${{DR%/}}
echo "DOCROOT=$DR"
case "$DR/" in "${{C%/}}"/*) echo "WEBROOT_INSIDE=1";; esac
if [ -n "$DR" ] && [ -L "$DR" ]; then echo "DOCROOT_LINK=1"; fi
echo "OWNER=$(stat -c %U "$C")"
echo "SIZE=$(du -sm "$C" | cut -f1)"
echo "FREE=$(df -m --output=avail "$(dirname "$C")" | tail -1 | tr -d ' ')"
L=$(mktemp); echo {lst} | base64 -d | gunzip > "$L"
python3 -c "$(echo {scan} | base64 -d)" "$C" "$L" {_q(",".join(sorted(DEPS)))}
rm -f "$L"
""", job, "Could not read the live folder.")
    shares = sorted({l[6:] for l in out.splitlines() if l.startswith("SHARE=") and l[6:]})
    # a shared folder covers what is inside it
    shares = [s for s in shares if not any(s != o and s.startswith(o.rstrip("/") + "/") for o in shares)]
    inside = "WEBROOT_INSIDE=1" in out
    return {"already": "ALREADY=1" in out, "webroot_inside": inside, "docroot": _val(out, "DOCROOT"),
            "docroot_link": "DOCROOT_LINK=1" in out, "web_user": _val(out, "WEBUSER"), "owner": _val(out, "OWNER"),
            "size_mb": int(_val(out, "SIZE") or 0), "free_mb": int(_val(out, "FREE") or 0), "shared": shares,
            "databases": [s for s in shares if DB_FILE.search(s)],
            "units": sorted({l[5:] for l in out.splitlines() if l.startswith("UNIT=")}),
            "not_kept": [l[6:] for l in out.splitlines() if l.startswith("LOOSE=")],
            "code": code, "releases": rel, "shared_dir": shared}


def run_convert(job):
    """Job "live_convert": one site → release layout. Backup first; reverts on any failure.

    Two layouts: the web root inside the code folder (PHP / Laravel), or a web root of its own next to a backend in
    the code folder (React build + Flask/Node service, like iv-crm) — then the web root becomes a link into the
    release (<release>/.webroot), so ONE switch changes both. A live SQLite database is never copied: it moves into
    shared/ while the site's services are stopped for those few seconds (the restart that happens anyway)."""
    p = job.project
    live = p.env("live")
    if live is None or live.server is None or not live.deploy_path:
        raise DeployError("Set the live server and folder first.")
    plan = plan_convert(live, job, tracked_files(p))
    if plan["already"]:
        raise DeployError("This live folder already uses releases.")
    code, rel, shared = plan["code"], plan["releases"], plan["shared_dir"]
    web = "" if plan["webroot_inside"] else plan["docroot"]
    if not plan["webroot_inside"]:
        if not web or plan["docroot_link"] or code.startswith(web.rstrip("/") + "/") or web == posixpath.dirname(code):
            raise DeployError(f"Can't tell how this site's web root ({web or 'unknown'}) relates to its code folder — not converting.")
    dbs, units = plan["databases"], plan["units"]
    if dbs and not units:
        raise DeployError(f"The site uses a SQLite database ({', '.join(dbs)}) but no service of its own runs from the "
                          "folder, so it can't be paused for the move — not converting.")
    if plan["free_mb"] and plan["free_mb"] < 2048:
        raise DeployError(f"Only {plan['free_mb']} MB free on the server — not converting (need at least 2 GB).")
    note(job, "", "Plan:\n" + json.dumps(plan, indent=1))
    others = [s for s in plan["shared"] if s not in dbs]
    name = "r" + now().strftime("%Y%m%d-%H%M%S")
    owner = plan["web_user"] or plan["owner"]
    links = "\n".join(f"""S={_q(s)}; mkdir -p "$SH/$(dirname "$S")" "$(dirname "$R/$S")"
if [ -e "$R/$S" ] || [ -L "$R/$S" ]; then rm -rf "$SH/$S"; mv "$R/$S" "$SH/$S"; fi
ln -sfn "$SH/$S" "$R/$S" """ for s in others)
    db_links = "\n".join(f"""S={_q(s)}; mkdir -p "$SH/$(dirname "$S")"; rm -f "$R/$S" "$R/$S-wal" "$R/$S-shm" "$R/$S-journal"
ln -sfn "$SH/$S" "$R/$S" """ for s in dbs)
    db_backup = "\n".join(f"""S={_q(s)}; U=$(stat -c %U "$C/$S"); T=$(runuser -u "$U" -- mktemp)
runuser -u "$U" -- python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()' "$C/$S" "$T"
mv "$T" "$B/$N-$(echo "$S" | tr / _)-before-releases-$TS.sqlite" """ for s in dbs)
    db_move = "\n".join(f"""S={_q(s)}
for X in "" -wal -shm -journal; do if [ -e "$C/$S$X" ] && [ ! -L "$C/$S$X" ]; then mv -f "$C/$S$X" "$SH/$S$X"; fi; done
if [ ! -L "$C/$S" ]; then ln -s "$SH/$S" "$C/$S"; fi""" for s in dbs)
    catch_up = "\n".join(f"""S={_q(s)}
if [ -d "$1/$S" ] && [ ! -L "$1/$S" ]; then rsync -a "$1/$S/" "$SH/$S/"; elif [ -f "$1/$S" ] && [ ! -L "$1/$S" ]; then cp -a "$1/$S" "$SH/$S"; fi"""
                         for s in others) or ":"
    q_units = " ".join(_q(u) for u in units)
    base_name = posixpath.basename(code)
    db_excl = " ".join(f"--exclude={_q(base_name + '/' + d + x)}" for d in dbs for x in ("", "-wal", "-shm", "-journal"))
    # folders made inside shared/ to hold the items (e.g. shared/backend) get the owner and rights of the same folder in
    # the live code — made by root they'd stop the site's own user from writing next to its database (SQLite -wal)
    parents = sorted({"/".join(s.split("/")[:i]) for s in plan["shared"] for i in range(1, len(s.split("/")))})
    owners = "\n".join(f"""P={_q(d)}; if [ -d "$C/$P" ] && [ ! -L "$C/$P" ]; then chown --reference="$C/$P" "$SH/$P"; chmod --reference="$C/$P" "$SH/$P"; fi"""
                       for d in parents) or ":"
    note(job, "Backing up the live folder and switching it to releases"
              + (f" (services {', '.join(units)} pause for a few seconds during the switch)" if units else " (the site keeps running)"))
    out = _run(live.server, f"""C={_q(code)}; REL={_q(rel)}; SH={_q(shared)}; R={_q(rel)}/{name}; W={_q(web)}; TS=$(date +%Y%m%d-%H%M%S)
OLD="$C.before-releases-$TS"; WOLD="$W.before-releases-$TS"; B=/root/ai-dashboard-backups; N=$(basename "$(dirname "$C")")
catch_up() {{
{catch_up}
}}
mkdir -p "$B"
# a busy site keeps writing (logs…) while tar reads: tar's exit 1 = "a file changed meanwhile" is fine, 2 = real failure.
# SQLite databases are left out here — they get their own consistent backup just below
tar -C "$(dirname "$C")" -czf "$B/$N-$(basename "$C")-before-releases-$TS.tgz" --warning=no-file-changed \
    --exclude=node_modules --exclude=vendor {db_excl} "$(basename "$C")" || [ $? -eq 1 ]
if [ -n "$W" ]; then tar -C "$(dirname "$W")" -czf "$B/$N-$(basename "$W")-before-releases-$TS.tgz" "$(basename "$W")"; fi
{db_backup}
mkdir -p "$REL" "$SH"
cp -a --reflink=auto "$C" "$R"                     # copy-on-write: no extra disk until a file changes (XFS reflink)
if [ -n "$W" ]; then
  cp -a --reflink=auto "$W" "$R/.webroot"
  # the web server reaches the web root THROUGH releases/ and the release: give them the web root's group (traverse)
  chgrp --reference="$W" "$REL" "$R"; chmod g+x "$REL" "$R"
fi
{links}
{db_links}
rm -rf "$R/.git"
date -u +%FT%TZ > "$R/.release-made"
catch_up "$C"
chown --reference="$C" "$SH"; chmod --reference="$C" "$SH"
if [ -z "$W" ]; then chown --reference="$C" "$REL"; chmod --reference="$C" "$REL"; fi
{owners}
# the switch: services (if any) pause for these seconds only; any failure puts the old folders back
undo() {{
  if [ -d "$OLD" ]; then if [ -L "$C" ]; then rm -f "$C"; fi; if [ ! -e "$C" ]; then mv "$OLD" "$C"; fi; fi
  if [ -n "$W" ] && [ -d "$WOLD" ]; then if [ -L "$W" ]; then rm -f "$W"; fi; if [ ! -e "$W" ]; then mv "$WOLD" "$W"; fi; fi
}}
finish() {{ rc=$?; if [ $rc -ne 0 ]; then undo; fi; {f"systemctl start {q_units} || true; " if units else ""}exit $rc; }}
trap finish EXIT
{f"systemctl stop {q_units}" if units else ":"}
{db_move}
catch_up "$C"
mv "$C" "$OLD"
ln -sfn "$R" "$C"
if [ -n "$W" ]; then
  mv "$W" "$WOLD"
  ln -sfn "$C/.webroot" "$W"
  chown -h --reference="$WOLD" "$W"
fi
catch_up "$OLD"
echo "OLD=$OLD"; if [ -n "$W" ]; then echo "WOLD=$WOLD"; fi
""", job, "Converting the live folder failed — the old folders are back in place.", timeout=3600)
    old, wold = _val(out, "OLD"), _val(out, "WOLD")
    live.layout, live.shared, live.current_release = "releases", plan["shared"], name
    live.restart_units = units
    if dbs and not live.db_name:                     # Go Live snapshots / rollback restores need to know the database
        live.db_name, p.db_engine = "sqlite:" + dbs[0], "sqlite"
    db.session.commit()
    _run(live.server, restart_script(live, owner, units=False), job, "Could not restart the site's workers.")
    ok, status = _health(live)
    if ok and not _units_up(live, job):
        ok, status = False, "a service that did not start"
    if not ok:
        note(job, "The site didn't answer after the switch — switching back")
        back = f"C={_q(code)}; rm -f \"$C\"\nmv {_q(old)} \"$C\"\n"
        if wold:
            back += f"W={_q(web)}; rm -f \"$W\"\nmv {_q(wold)} \"$W\"\n"
        _run(live.server, back + restart_script(live, owner), job, "Switching back failed — check the site NOW.")
        live.layout, live.current_release = "", None
        db.session.commit()
        back_ok = _health(live)[0] and _units_up(live, job)
        raise_alert("error", f"{p.name}: converting live to releases failed and was undone"
                             + ("" if back_ok else " — BUT the site is still not answering, check it NOW"), p)
        raise DeployError(f"After the switch the site answered {status or 'nothing'} — switched back to the old folder. "
                          + ("Nothing lost; the site answers again." if back_ok else
                             "The site still does not answer after switching back — check it NOW (the dashboard sent an alert)."))
    log("Live converted", project_id=p.id, user=job.by_user,
        details=f"{code} → releases/{name}; shared: {', '.join(plan['shared']) or 'none'}" + (f"; web root {web} follows the release" if web else ""))
    db.session.commit()
    if not p.live_recipe.strip():
        try:
            prepare_live_steps(job, live)
        except DeployError as e:                     # the conversion itself is done; the steps can be written later
            note(job, "", f"Live steps not written: {e.plain}")
    note(job, f"Live now uses releases · {name} · shared: {', '.join(plan['shared']) or 'none'}"
              + (f" · web root follows the release" if web else "") + (f" · services: {', '.join(units)}" if units else "")
              + f" · old folder kept as {posixpath.basename(old)}")


# ---------- Go Live v2 ----------
def changed_in_current(live, job):
    """Files changed directly in the live release since it was made (excluding shared things and caches)."""
    code, root, rel, shared = paths(live)
    out = _run(live.server, f"""R={_q(rel)}/{_q(live.current_release)}
[ -f "$R/.release-made" ] || exit 0
find -L "$R" -type f -newer "$R/.release-made" -not -path "*/storage/*" -not -path "*/bootstrap/cache/*" \\
     -not -path "*/node_modules/*" -not -path "*/vendor/*" -not -path "*/__pycache__/*" -not -name '*.pyc' \\
     -not -name '.env*' -not -name '*.db*' -not -name '*.sqlite*' 2>/dev/null | sed "s#^$R/##" | head -50
""", job, "Could not check the live release for direct edits.")
    shared_items = live.shared or []
    return [l for l in out.splitlines() if l.strip() and not any(l == s or l.startswith(s.rstrip("/") + "/") for s in shared_items)]


RELEASE_RULES = """A Go Live is running for a client's LIVE site. A new release folder was prepared beside the running one, and
one of its preparation steps failed. Real users are on the running release — it is not touched, and nothing switches
until every step passes. Fix the problem INSIDE the new release with the tools (mcp__beta__*):
- Only install / build / cache / config commands. Never touch the database (no migrations other than the saved
  `migrate --force` step, no seeding, no wiping, no SQL), never the shared settings / uploads / data, never git.
- Don't edit the app's source code — the release must equal the code that was approved. If the code itself is the
  problem, stop and explain: the change goes back to beta.
- When it works, save_recipe with the corrected steps; the dashboard runs them all again before switching.
- End with a 2-line note for the owner in plain words."""

RELEASE_FIX_PROMPT = """The preparation of a new live release failed. Call status, find the cause, fix it inside the new release
(release folder: {release}), and save the corrected steps."""


def _claude_fixes_release(job, live, release, run_user, recipe, output):
    """Claude tries to fix a failing step inside the NEW release (live keeps running). True when every step then passes."""
    from app import betas
    from app import claude_code as cc
    if run_user in ("", "root"):
        note(job, "A release step failed. This site's code belongs to root, and Claude never works as root — stopping safely")
        return False
    try:
        cc.login_for()                       # these tool runs use the default Claude account (Settings → Claude accounts)
    except cc.CCError:
        return False                                  # no Claude Code login: just stop safely
    note(job, "A release step failed — Claude is fixing it inside the new release (live keeps running)")
    job.context = {"mode": "release_fix", "release": release, "run_user": run_user, "recipe": recipe, "error": output[-3000:]}
    db.session.commit()
    try:
        text = betas._claude_tools(job, RELEASE_FIX_PROMPT.format(release=release), turns=30, usd=2.0, rules=RELEASE_RULES)
        note(job, "", "Claude's note:\n" + text[-1500:])
    except DeployError as e:
        note(job, "", f"Claude could not fix it: {e.plain}")
        return False
    finally:
        job.context = None
        db.session.commit()
    db.session.refresh(job.project)
    steps = job.project.live_recipe or recipe
    note(job, "Running every release step again")
    tag = "AIDASH_" + __import__("secrets").token_hex(4)
    code_, out = remote.run(live.server, f"runuser -u {_q(run_user)} -- bash -se <<'{tag}'\nset -euo pipefail\n"
                                         f"cd {_q(release)}\nexport PATH=/opt/aidash-node/bin:/usr/local/bin:/usr/bin:/bin\n"
                                         + "\n".join(l for l in steps.splitlines() if l.strip() and not l.startswith("@"))
                                         + f"\n{tag}\n", timeout=1800)
    note(job, "", out)
    return code_ == 0


def publish(job, live, release, recipe, web_user):
    """'@publish FOLDER' steps: built files (e.g. web/dist) → the release's own web root (<release>/.webroot), for a
    site whose web root is a folder of its own. Old hashed files stay, so open browser tabs keep working."""
    folders = [l.split(" ", 1)[1].strip() for l in recipe.splitlines() if l.startswith("@publish ")]
    if not folders:
        return
    for f in folders:
        if not PUBLISH_RE.match(f) or ".." in f.split("/"):
            raise DeployError(f"'@publish {f}' is not a folder inside the release — live was NOT switched.")
    script = f"R={_q(release)}\n[ -d \"$R/.webroot\" ] || exit 0\n" + "".join(
        f"[ -d \"$R\"/{_q(f)} ] || {{ echo 'built folder {f} is missing' >&2; exit 1; }}\nrsync -a \"$R\"/{_q(f)}/ \"$R/.webroot/\"\n" for f in folders)
    script += f"chown -R {_q(web_user)}:{_q(web_user)} \"$R/.webroot\"\n"
    note(job, "Putting the built web files into the new release's web root")
    try:
        _run(live.server, script, job, "Publishing the built web files failed.")
    except DeployError:
        _run(live.server, f"rm -rf {_q(release)}\n", job, "Could not clean up the failed release.")
        raise DeployError("Publishing the built web files failed — live was NOT switched and keeps running the old release.")


LIVE_STEPS_RULES = """You write the steps that turn a fresh copy of new code into a working LIVE release of a client's site. You
can only read (mcp__beta__*): you run nothing. The dashboard runs your steps later inside a NEW release folder (a
copy of the running one, dependencies included), as the site's user, before it switches; a failing step never
switches live. So:
- Start from the beta's steps but use LIVE's own paths: the Python venv(s) that already exist there, the folders the
  services start from, the web root. Don't create a new venv when live has one; install into the existing one.
- Builds: install + build (e.g. npm ci && npm run build, composer install --no-dev --optimize-autoloader); built web
  files of a separate web root go there with a '@publish FOLDER' line. Production settings come from the files in
  the repo / live's shared settings — never beta addresses.
- Never anything that wipes or seeds data; an additive `migrate --force` is allowed when the app uses migrations.
- Each line runs in the release folder root; `cd` inside one line only (cd web && …).
- save_recipe, then end with a 2-line note for the owner in plain words."""

LIVE_STEPS_PROMPT = """Write and save the steps for a new live release of this site. Call status first; read files you need
(requirements, package.json, composer.json, service start lines)."""


def _facts(live, job):
    """What Claude needs to write live steps, read from the running release (read only)."""
    code, root, rel, shared = paths(live)
    r = f"{rel}/{live.current_release}"
    out = _run(live.server, f"""R={_q(r)}
echo "OWNER=$(stat -c %U "$R")"
find "$R" -maxdepth 4 -name pyvenv.cfg -not -path '*/node_modules/*' 2>/dev/null | sed "s#^$R/#VENV=#; s#/pyvenv.cfg##"
find "$R" -maxdepth 3 \( -name composer.json -o -name package.json -o -name requirements.txt -o -name pyproject.toml \
     -o -name artisan -o -name manage.py -o -name package-lock.json -o -name yarn.lock -o -name pnpm-lock.yaml \) \
     -not -path '*/node_modules/*' -not -path '*/vendor/*' 2>/dev/null | sed "s#^$R/#FILE=#"
if [ -d "$R/.webroot" ]; then echo "WEBROOT=1"; fi
""" + "".join(f"systemctl cat {_q(u)} 2>/dev/null | grep -E '^(ExecStart|WorkingDirectory|User)=' | sed 's#^#UNIT {u}: #'\n"
              for u in (live.restart_units or [])), job, "Could not read the live release.")
    p = live.project
    beta = p.env("beta")
    return {"running_live_release_folder": r, "steps_run_as": _val(out, "OWNER"),
            "python_venvs": [l[5:] for l in out.splitlines() if l.startswith("VENV=")],
            "build_and_package_files": [l[5:] for l in out.splitlines() if l.startswith("FILE=")],
            "separate_web_root": ("yes — built web files must be published with '@publish FOLDER'" if "WEBROOT=1" in out
                                  else "no — the web root is inside the code folder"),
            "services_restarted_after_a_switch": [l[5:] for l in out.splitlines() if l.startswith("UNIT ")],
            "shared_items_linked_into_every_release": live.shared or [],
            "node_and_npm": "/opt/aidash-node/bin is on PATH for every step",
            "beta_steps": p.beta_recipe or "(none saved)", "beta_code_folder": beta.deploy_path if beta else None}


def prepare_live_steps(job, live):
    """Claude reads the running live release and writes the steps for new live releases (live_recipe). Returns True when
    saved. Without a Claude Code login it just returns False (the beta's steps stay in use)."""
    from app import betas
    from app import claude_code as cc
    p = job.project
    try:
        cc.login_for()                       # these tool runs use the default Claude account (Settings → Claude accounts)
    except cc.CCError:
        note(job, "No Claude Code login — the live steps are not written (the beta's steps are used)")
        return False
    web_user = _val(_run(live.server, f"echo WEBUSER=$(grep -m1 extUser /usr/local/lsws/conf/vhosts/{_q(host_of(live))}/vhost.conf | awk '{{print $2}}')\n",
                         job, "Could not read the site's users."), "WEBUSER")
    note(job, "Claude is writing the steps for new live releases (it only reads the live site)")
    job.context = {"mode": "live_steps", "release": f"{paths(live)[2]}/{live.current_release}", "run_user": web_user or "nobody",
                   "facts": _facts(live, job)}
    db.session.commit()
    before = p.live_recipe
    try:
        text = betas._claude_tools(job, LIVE_STEPS_PROMPT, turns=20, usd=1.0, rules=LIVE_STEPS_RULES)
        note(job, "", "Claude's note:\n" + text[-1500:])
    except DeployError as e:
        note(job, "", f"Claude could not write the live steps: {e.plain}")
        return False
    finally:
        job.context = None
        db.session.commit()
    db.session.refresh(p)
    saved = bool(p.live_recipe.strip()) and p.live_recipe != before
    if saved:
        log("Project settings", project_id=p.id, user=job.by_user, details="Live release steps written by Claude")
        db.session.commit()
        note(job, "Live steps saved by Claude — the next Go Live tries them in a new release before switching")
    return saved


def run_live_steps(job):
    """Job "live_steps": Claude writes the live release steps now (owner's button)."""
    live = job.project.env("live")
    if live is None or live.layout != "releases" or not live.current_release:
        raise DeployError("Switch live to release folders first.")
    if not prepare_live_steps(job, live):
        raise DeployError("The live steps were not saved — see the note below.")


def build_and_switch(job, live, root_checkout, sha, recipe, web_user, run_user):
    """New release from the dashboard's checkout → shared links → recipe → switch → check → (auto back). Returns release name."""
    code, root, rel, shared = paths(live)
    name = "r" + now().strftime("%Y%m%d-%H%M%S")
    stage = f"/root/ai-dashboard-stage/{host_of(live)}-live"
    note(job, "Copying the release to the server")
    err = remote.push_dir(live.server, root_checkout, stage)
    if err:
        raise DeployError(err)
    links = "\n".join(f"S={_q(s)}; rm -rf \"$R/$S\"; mkdir -p \"$(dirname \"$R/$S\")\"; ln -sfn \"$SH/$S\" \"$R/$S\""
                      for s in (live.shared or []))
    keep = " ".join(f"--exclude={_q('/' + s)}" for s in (live.shared or []))
    _run(live.server, f"""R={_q(rel)}/{name}; CUR={_q(rel)}/{_q(live.current_release)}; SH={_q(shared)}
# a copy-on-write copy of the current release (dependencies included, no extra disk), then the new code over it;
# rsync (by content) rewrites only files whose content changed, as new files — unchanged ones keep sharing disk, and
# the release that is live right now is never touched
cp -a --reflink=auto "$CUR" "$R"
rsync -a --checksum --delete --exclude=/vendor --exclude=/node_modules --exclude=/venv --exclude=/backend/venv --exclude=/.release-made --exclude=/.webroot {keep} {_q(stage)}/ "$R"/
{links}
# Python venv scripts (pip, gunicorn…) start with the path of the venv they were made in — here the LIVE one (through
# the code link). Point them at this release, so building it can never install into the release that is serving.
for V in $(find "$R" -maxdepth 4 -name pyvenv.cfg -not -path '*/node_modules/*' 2>/dev/null); do
  for F in "$(dirname "$V")"/bin/*; do
    if [ -f "$F" ] && [ ! -L "$F" ] && [ "$(head -c2 "$F")" = "#!" ]; then sed -i -E "1s@^#!({code.replace(".", "[.]")}|{rel.replace(".", "[.]")}/r[^/]*)/@#!$R/@" "$F"; fi
  done
done
chown -R {_q(run_user)}:{_q(run_user)} "$R"; chmod -R u+rwX,go+rX,go-w "$R"
if [ -d "$R/.webroot" ]; then chown -R {_q(web_user)}:{_q(web_user)} "$R/.webroot"; fi
rm -rf {_q(stage)}
""", job, "Could not prepare the new release folder.", timeout=1800)
    code_ = 0
    if recipe.strip():
        note(job, "Running the release steps (the live site keeps running the old release)")
        tag = "AIDASH_" + __import__("secrets").token_hex(4)
        code_, out = remote.run(live.server, f"runuser -u {_q(run_user)} -- bash -se <<'{tag}'\nset -euo pipefail\n"
                                             f"cd {_q(rel)}/{name}\nexport PATH=/opt/aidash-node/bin:/usr/local/bin:/usr/bin:/bin\n"
                                             + "\n".join(l for l in recipe.splitlines() if l.strip() and not l.startswith("@"))
                                             + f"\n{tag}\n", timeout=1800)
        note(job, "", out)
        if code_ != 0 and not _claude_fixes_release(job, live, f"{rel}/{name}", run_user, recipe, out):
            _run(live.server, f"rm -rf {_q(rel)}/{name}\n", job, "Could not clean up the failed release.")
            raise DeployError("A release step failed — live was NOT switched and keeps running the old release. "
                              "See the output below.")
    if recipe.strip() and code_ != 0:                # Claude fixed it: the corrected steps decide what is published
        recipe = job.project.live_recipe or recipe
    publish(job, live, f"{rel}/{name}", recipe, web_user)
    date_cmd = f"date -u +%FT%TZ > {_q(rel)}/{name}/.release-made"
    note(job, "Switching live to the new release")
    _run(live.server, f"""{date_cmd}
rm -f {_q(code)}.next; ln -s {_q(rel)}/{name} {_q(code)}.next; mv -T {_q(code)}.next {_q(code)}
""" + restart_script(live, web_user), job, "Could not switch to the new release.")
    ok, status = _health(live)
    if not ok:
        note(job, "The site didn't answer — switching straight back")
        _run(live.server, f"rm -f {_q(code)}.next; ln -s {_q(rel)}/{_q(live.current_release)} {_q(code)}.next; mv -T {_q(code)}.next {_q(code)}\n"
             + restart_script(live, web_user), job, "Switching back failed — check the site NOW.")
        raise_alert("error", f"{job.project.name}: new release didn't answer ({status or 'no answer'}) — switched back automatically", job.project)
        raise DeployError(f"The new release didn't answer ({status or 'no answer'}) — live switched straight back to the previous release.")
    return name


def prune(live, job):
    code, root, rel, shared = paths(live)
    # newest KEEP stay; the release that is live right now is never removed, however old it is (e.g. after a rollback)
    cur = f"{live.current_release}/" if live.current_release else "-"
    _run(live.server, f"cd {_q(rel)} && ls -1dt r*/ 2>/dev/null | grep -vxF {_q(cur)} | tail -n +{KEEP} | xargs -r rm -rf\n",
         job, "Could not remove old releases.")


def run_go_live_v2(job, change_ids, reason):
    from app import golive
    p = job.project
    live = p.env("live")
    ship = Change.query.filter(Change.project_id == p.id, Change.id.in_(change_ids),
                               Change.status.in_(golive.SHIP_FROM)).order_by(Change.id).all()
    if not ship:
        raise DeployError("Nothing to send live.")
    edited = changed_in_current(live, job)
    if edited:
        note(job, "", "Changed directly on the live server:\n" + "\n".join(edited))
        raise DeployError(f"{len(edited)} file(s) were changed directly in the live release ({', '.join(edited[:3])}…). "
                          "Nothing was changed. Bring those edits into GitHub first.")
    snap = golive.snapshot(p, live, job)
    try:
        root, sha = golive.build_release(p, ship, job)
    except ws.WorkspaceError as e:
        raise DeployError(f"Could not build the release: {e}")
    note(job, "Saving the release on GitHub")
    try:
        ws.git(root, "push", "--quiet", "origin", p.default_branch or "main", auth=True)
    except ws.WorkspaceError as e:
        raise DeployError(f"Could not push the release to GitHub ({e}). Nothing went live.")
    plan_user = _run(live.server, f"echo WEBUSER=$(grep -m1 extUser /usr/local/lsws/conf/vhosts/{_q(host_of(live))}/vhost.conf | awk '{{print $2}}')\n"
                                  f"echo OWNER=$(stat -c %U {_q(paths(live)[2])}/{_q(live.current_release)})\n", job, "Could not read the site's users.")
    web_user, run_user = _val(plan_user, "WEBUSER"), _val(plan_user, "OWNER")
    if not p.live_recipe.strip() and p.beta_recipe.strip():
        prepare_live_steps(job, live)               # first Go Live of this site: Claude writes the live steps once
        db.session.refresh(p)
    previous = live.current_release
    name = build_and_switch(job, live, root, sha, p.live_recipe or p.beta_recipe or "", web_user or run_user, run_user)
    live.current_release, live.current_commit = name, sha
    live.watch_until = now() + __import__("datetime").timedelta(minutes=golive.WATCH_MINUTES)
    t = now()
    db.session.add(Deployment(project_id=p.id, environment_id=live.id, commit=sha, previous_commit=previous,
                              change_ids=[c.id for c in ship], snapshot_id=snap.id if snap else None, by_user_id=job.by_user_id,
                              result="ok", override_reason=reason or "", note=(f"release {name} · " + " · ".join(c.title for c in ship))[:255]))
    for c in ship:
        c.status, c.live_at = "live", t
    log("Go Live", project_id=p.id, user=job.by_user, details=f"{len(ship)} change(s) → release {name} ({sha})"
                                                             + (f" · without approval: {reason}" if reason else ""))
    db.session.commit()
    prune(live, job)
    raise_alert("golive", f"{p.name}: {len(ship)} change{'s' if len(ship) != 1 else ''} went live (release {name})", p)
    note(job, f"Live · release {name} · {sha} · previous release {previous} kept for rollback")


def run_rollback_v2(job, deployment_id, with_db):
    """Switch live back to the release it ran before (instant), restart, optional DB snapshot, check."""
    from app import golive
    p = job.project
    live = p.env("live")
    cur = db.session.get(Deployment, deployment_id)
    if cur is None or not cur.previous_commit or cur.rolled_back:
        raise DeployError("No earlier release recorded for this Go Live.")
    target = cur.previous_commit                     # Go Live v2 keeps the previous RELEASE name here
    code, root, rel, shared = paths(live)
    users = _run(live.server, f"[ -d {_q(rel)}/{_q(target)} ] || {{ echo 'release {target} is gone' >&2; exit 1; }}\n"
                              f"echo WEBUSER=$(grep -m1 extUser /usr/local/lsws/conf/vhosts/{_q(host_of(live))}/vhost.conf | awk '{{print $2}}')\n",
                 job, f"The earlier release {target} is no longer on the server.")
    note(job, f"Switching live back to release {target}")
    _run(live.server, f"rm -f {_q(code)}.next; ln -s {_q(rel)}/{_q(target)} {_q(code)}.next; mv -T {_q(code)}.next {_q(code)}\n"
         + restart_script(live, _val(users, "WEBUSER")), job, "Could not switch back.")
    if with_db:
        snap = cur.snapshot
        if snap is None or not snap.ok or ":" not in snap.storage_url:
            raise DeployError("Code is back, but there is no database snapshot for that release to restore.")
        note(job, "Restoring the live database from the snapshot")
        _run(live.server, golive.restore_script(p, live, snap.storage_url.split(":", 1)[1]),
             job, "Code is back, but restoring the database snapshot failed.", timeout=3600)
    ok, status = _health(live)
    cur.rolled_back = True
    live.current_release = target
    live.watch_until = now() + __import__("datetime").timedelta(minutes=golive.WATCH_MINUTES)
    db.session.add(Deployment(project_id=p.id, environment_id=live.id, commit=target, by_user_id=job.by_user_id,
                              result="ok" if ok else "failed", note=f"Rollback to release {target}" + (" + database snapshot" if with_db else "")))
    for c in Change.query.filter(Change.id.in_(cur.change_ids or [])).all():
        c.status, c.live_at = "beta", None
        db.session.add(ChangeMessage(change_id=c.id, sender="claude",
                                     body="This change was rolled back from live. Tell me what went wrong and I will fix it on beta; "
                                          "then it goes through tests and client approval again."))
    if ok:
        p.incident, p.incident_at = "", None
    log("Rollback", project_id=p.id, user=job.by_user, details=f"release {live.current_release}" + (" + database snapshot" if with_db else ""))
    db.session.commit()
    if not ok:
        raise DeployError(f"Rolled back, but the live site still doesn't answer ({status or 'no answer'}). Check the server.")
    note(job, f"Live is back on release {target}")


def run_rehearse_go_live(job):
    """Rehearsal projects only ("<slug>-rehearsal", their "live" is a locked copy): a full Go Live v2 with the code that is
    on the default branch now (nothing merged, nothing pushed to GitHub), then a rollback to the release before."""
    p = job.project
    live = p.env("live")
    if not p.slug.endswith("-rehearsal") or live is None or live.layout != "releases":
        raise DeployError("Only a rehearsal project whose copy site already uses release folders.")
    base = p.default_branch or "main"
    try:
        root = ws.prepare(p, base, base)
        sha = ws.git(root, "rev-parse", "--short", "HEAD").strip()
    except ws.WorkspaceError as e:
        raise DeployError(f"Could not read the code from GitHub: {e}")
    users = _run(live.server, f"echo WEBUSER=$(grep -m1 extUser /usr/local/lsws/conf/vhosts/{_q(host_of(live))}/vhost.conf | awk '{{print $2}}')\n"
                              f"echo OWNER=$(stat -c %U {_q(paths(live)[2])}/{_q(live.current_release)})\n", job, "Could not read the site's users.")
    web_user, run_user = _val(users, "WEBUSER"), _val(users, "OWNER")
    previous = live.current_release
    started = now()
    name = build_and_switch(job, live, root, sha, p.live_recipe or p.beta_recipe or "", web_user or run_user, run_user)
    live.current_release = name
    db.session.commit()
    note(job, f"Go Live rehearsal: release {name} is live and answers ({int((now() - started).total_seconds())} s). Now rolling back")
    code = paths(live)[0]
    t = now()
    _run(live.server, f"rm -f {_q(code)}.next; ln -s {_q(paths(live)[2])}/{_q(previous)} {_q(code)}.next; mv -T {_q(code)}.next {_q(code)}\n"
         + restart_script(live, web_user or run_user), job, "Could not switch back.")
    ok, status = _health(live)
    if not ok or not _units_up(live, job):
        raise DeployError(f"After the rollback the copy site answered {status or 'nothing'} — check it.")
    live.current_release = previous
    db.session.commit()
    prune(live, job)
    log("Go Live rehearsal", project_id=p.id, user=job.by_user, details=f"release {name} ({sha}) live, then back to {previous}")
    db.session.commit()
    note(job, f"Rehearsal OK · Go Live to {name} ({sha}) and rollback to {previous} ({int((now() - t).total_seconds())} s) both worked")
