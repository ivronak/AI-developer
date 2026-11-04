"""Go Live (F10) and Rollback (F11) — Owner only, each confirmed with the authenticator code, run as a background Job.

Go Live:  1. drift check — the live folder must have no direct edits (files changed on the server by hand)
          2. snapshot of the live database, kept on the live server (~/ai-dashboard-snapshots, Step 11 copies it to cloud storage)
          3. release = default branch + each change going live, squash-merged (a conflict stops everything; nothing is pushed)
          4. the project's tests on the release; failing tests stop everything
          5. push the default branch, run the deploy steps on live, health check, 15-minute close watch
Rollback: put the default branch back to the code live ran before (a new commit — history is never rewritten), deploy it,
          optionally load the snapshot taken before that Go Live into the live database, health check, close watch.
Squash merges (not merge commits) keep a rolled-back change shippable again later: git sees it as not yet in main."""
import shlex
from datetime import timedelta

from app import remote
from app import workspace as ws
from app.alerts import raise_alert
from app.auth import log
from app.clock import now
from app import deploy
from app.deploy import DUMP, DeployError, template_for
from app.extensions import db
from app.jobs import note
from app.models import Backup, Change, ChangeMessage, Deployment

WATCH_MINUTES = 15
SHIP_FROM = ("approved", "passed", "client", "changes")      # approved, or (Owner override) not yet approved
SNAP_DIR = "~/ai-dashboard-snapshots"
LOAD_GZ = {"mysql": "gunzip -c {file} | mysql {db}", "postgres": "gunzip -c {file} | psql --quiet {db}"}


def _live(project):
    live = project.env("live")
    if live is None or live.server is None or not live.deploy_path:
        raise DeployError("Set the live server and folder first (project → Settings → Servers & deploy).")
    return live


def _run(server, body, job, fail_msg, timeout=900):
    code, out = remote.run(server, "set -euo pipefail\n" + body, timeout=timeout)
    if code != 0:
        note(job, "", out)
        raise DeployError(fail_msg)
    return out


def drift_check(project, live, job):
    """Refuse when someone changed files directly in the live folder. Returns the commit live runs now."""
    note(job, "Checking live for direct edits on the server")
    out = _run(live.server, f"cd {shlex.quote(live.deploy_path)}\n"
                            "echo \"HEAD=$(git rev-parse HEAD)\"\necho \"ORIGIN=$(git remote get-url origin 2>/dev/null)\"\n"
                            "git status --porcelain --untracked-files=no\n",
               job, "Could not read the live folder with git — is it a git checkout of this repo?", timeout=120)
    lines = [l for l in out.splitlines() if l.strip()]
    head = next((l[5:].strip() for l in lines if l.startswith("HEAD=")), "")
    from app import gitcheck
    origin = gitcheck.repo_slug(next((l[7:].strip() for l in lines if l.startswith("ORIGIN=")), ""))
    live.git_origin, live.git_checked_at = origin, now()
    if origin and project.github_repo and origin != project.github_repo.lower():
        gitcheck._remember_alias(origin)            # an old name after a transfer may still be the same repo
    db.session.commit()
    wrong = gitcheck.problem(project, live)
    if wrong:
        raise DeployError(wrong + " Nothing was changed — a Go Live now would put the wrong code live. Point the live "
                          "folder at this project's repo first (or wait for Go Live v2, which doesn't need git on the server).")
    edited = [l[3:].strip() for l in lines if not l.startswith(("HEAD=", "ORIGIN=")) and len(l) > 3]
    if edited:
        note(job, "", "Files changed directly on the live server:\n" + "\n".join(edited[:50]))
        raise DeployError(f"Someone changed {len(edited)} file{'s' if len(edited) != 1 else ''} directly on the live server "
                          f"({', '.join(edited[:3])}{'…' if len(edited) > 3 else ''}). Nothing was changed. "
                          "Ask the developer to commit those edits to GitHub (or undo them), then Go Live again.")
    return head


def sqlite_path(env):
    """'sqlite:backend/crm.db' → the file's path on the server (inside the site's code folder), else ''."""
    name = env.db_name or ""
    return f"{env.deploy_path.rstrip('/')}/{name[7:]}" if name.startswith("sqlite:") and env.deploy_path else ""


def restore_script(project, env, file):
    """Put a snapshot back into the live database. SQLite: the site's services stop for the swap (a file replaced under a
    running app would be overwritten or corrupted), the old -wal/-shm go, the services start again on any outcome."""
    f = shlex.quote(file)
    path = sqlite_path(env)
    if not path:
        engine = project.db_engine if project.db_engine in LOAD_GZ else "mysql"
        return LOAD_GZ[engine].format(file=f, db=shlex.quote(env.db_name)) + "\n"
    units = " ".join(shlex.quote(u) for u in (env.restart_units or []))
    return (f"D=$(readlink -f {shlex.quote(path)}); [ -f \"$D\" ] || {{ echo \"no database file $D\" >&2; exit 1; }}\n"
            f"T=$(mktemp -p \"$(dirname \"$D\")\" .restore.XXXXXX); gunzip -c {f} > \"$T\"\n"
            "chown --reference=\"$D\" \"$T\"; chmod --reference=\"$D\" \"$T\"\n"
            + (f"trap 'systemctl start {units} || true' EXIT\nsystemctl stop {units}\n" if units else "")
            + "rm -f \"$D-wal\" \"$D-shm\" \"$D-journal\"\nmv -f \"$T\" \"$D\"\n")


def snapshot(project, live, job):
    """Database snapshot on the live server, before anything changes. None when the project has no database."""
    if not live.db_name:
        note(job, "No live database set — skipping the snapshot")
        return None
    note(job, "Taking a snapshot of the live database")
    engine = project.db_engine if project.db_engine in DUMP else "mysql"
    path = sqlite_path(live)
    name = f"{project.slug}-{now().strftime('%Y%m%d-%H%M%S')}" + (".sqlite.gz" if path else ".sql.gz")
    # SQLite: a consistent copy through SQLite itself (a plain file copy of a busy database can be half-written),
    # made as the file's owner so no root-owned -wal / -shm files appear next to the live database
    dump = (f"D=$(readlink -f {shlex.quote(path)}); U=$(stat -c %U \"$D\"); T=$(runuser -u \"$U\" -- mktemp)\n"
            "runuser -u \"$U\" -- python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); "
            "s.backup(d); d.close(); s.close()' \"$D\" \"$T\"\n"
            "gzip -c \"$T\" > \"$F\"; rm -f \"$T\"\n") if path else \
        f"{DUMP[engine].format(db=shlex.quote(live.db_name))} | gzip > \"$F\"\n"
    out = _run(live.server,
               f"mkdir -p {SNAP_DIR} && chmod 700 {SNAP_DIR}\nF={SNAP_DIR}/{shlex.quote(name)}\n" + dump +
               "echo \"SIZE=$(wc -c < \"$F\")\"\necho \"PATH=$F\"\n",
               job, "The live database snapshot failed — nothing was changed on live.", timeout=3600)
    size = next((l[5:].strip() for l in out.splitlines() if l.startswith("SIZE=")), "0")
    path = next((l[5:].strip() for l in out.splitlines() if l.startswith("PATH=")), name)
    b = Backup(project_id=project.id, kind="snapshot", storage_url=f"{live.server.name}:{path}",
               size_bytes=int(size) if size.isdigit() else 0, ok=True)
    db.session.add(b)
    db.session.commit()
    return b


def build_release(project, ship, job):
    """Default branch + each change squash-merged. Runs the tests. Returns (root, short sha) — not pushed yet."""
    base = project.default_branch or "main"
    note(job, "Building the release")
    root = ws.prepare(project, base, base)
    remote_branches = {l.strip() for l in ws.git(root, "branch", "-r").splitlines()}
    for c in ship:
        if not c.branch or f"origin/{c.branch}" not in remote_branches:
            raise DeployError(f"Change #{c.id} has no branch on GitHub — it can't go live.")
        try:
            ws.git(root, "merge", "--squash", f"origin/{c.branch}")
        except ws.WorkspaceError:
            ws.git(root, "reset", "--hard", "--quiet", check=False)
            raise DeployError(f"Change #{c.id} “{c.title}” conflicts with the live code or another change. Nothing went live. "
                              "Ask Claude to update that change, then try again.")
        if ws.git(root, "status", "--porcelain").strip():
            ws.git(root, "commit", "--quiet", "-m", f"Change #{c.id}: {c.title}")
    note(job, "Running the tests on the release")
    ok, counts, out = ws.run_tests(root, project.test_command or "")
    if ok is False:
        note(job, "", out[-6000:] if out else "")
        raise DeployError("Tests failed on the release — nothing went live." +
                          (f" ({counts[0]} of {counts[1]} passed)" if counts else ""))
    return root, ws.git(root, "rev-parse", "--short", "HEAD").strip()


def _deploy_live(project, live, job):
    note(job, "Deploying on the live server")
    base = project.default_branch or "main"
    body = f"cd {shlex.quote(live.deploy_path)}\n" + (project.deploy_script or template_for(project)).replace("{branch}", base)
    code, out = remote.run(live.server, "set -euo pipefail\n" + body)
    note(job, "", out)
    if code != 0:
        raise DeployError("The deploy steps failed on the live server — see the output below. "
                          "Live may be half-updated: check the site, and use Rollback if it is broken.")


def _check_and_watch(project, live, job):
    note(job, "Checking the live site")
    ok, status = deploy.health_ok(live.url)          # via the module so tests can replace it
    live.watch_until = now() + timedelta(minutes=WATCH_MINUTES)
    return ok, status


def run_go_live(job, change_ids, reason):
    project = job.project
    live = _live(project)
    ship = Change.query.filter(Change.project_id == project.id, Change.id.in_(change_ids),
                               Change.status.in_(SHIP_FROM)).order_by(Change.id).all()
    if not ship:
        raise DeployError("Nothing to send live.")
    previous = drift_check(project, live, job)
    snap = snapshot(project, live, job)
    try:
        root, sha = build_release(project, ship, job)
    except ws.WorkspaceError as e:
        raise DeployError(f"Could not build the release: {e}")
    note(job, "Saving the release on GitHub")
    try:
        ws.git(root, "push", "--quiet", "origin", project.default_branch or "main", auth=True)
    except ws.WorkspaceError as e:
        raise DeployError(f"Could not push the release to GitHub ({e}). Nothing went live.")
    _deploy_live(project, live, job)
    ok, status = _check_and_watch(project, live, job)
    t = now()
    live.current_commit = sha
    d = Deployment(project_id=project.id, environment_id=live.id, commit=sha, previous_commit=previous or None,
                   change_ids=[c.id for c in ship], snapshot_id=snap.id if snap else None, by_user_id=job.by_user_id,
                   result="ok" if ok else "failed", override_reason=reason or "",
                   note=" · ".join(c.title for c in ship)[:255])
    db.session.add(d)
    for c in ship:
        c.status, c.live_at = "live", t
    log("Go Live", project_id=project.id, user=job.by_user,
        details=f"{len(ship)} change{'s' if len(ship) != 1 else ''} → live {sha}" + (f" · without approval: {reason}" if reason else ""))
    db.session.commit()
    if not ok:
        raise_alert("error", f"{project.name}: live site not answering right after Go Live "
                             f"({'no answer' if status == 0 else status}) — consider Rollback", project)
        raise DeployError(f"Deployed, but the live site does not answer ({'no answer' if status == 0 else status}). Check it now — use Rollback if it is broken.")
    raise_alert("golive", f"{project.name}: {len(ship)} change{'s' if len(ship) != 1 else ''} went live ({sha})", project)
    note(job, f"Live · {sha} · {len(ship)} change{'s' if len(ship) != 1 else ''} · watching closely for {WATCH_MINUTES} minutes")


def rollback_target(project):
    """(the live deployment to undo, the commit to go back to) or (None, None)."""
    live = project.env("live")
    if live is None:
        return None, None
    ds = (Deployment.query.filter_by(project_id=project.id, environment_id=live.id, rolled_back=False)
          .order_by(Deployment.created_at.desc(), Deployment.id.desc()).all())
    if not ds:
        return None, None
    cur = ds[0]
    target = cur.previous_commit or (ds[1].commit if len(ds) > 1 else None)
    return (cur, target) if target else (None, None)


def run_rollback(job, deployment_id, with_db):
    project = job.project
    live = _live(project)
    cur, target = rollback_target(project)
    if cur is None or cur.id != deployment_id:
        raise DeployError("Live changed since you opened Rollback — open it again.")
    base = project.default_branch or "main"
    note(job, f"Putting the code back to {target[:7]}")
    try:
        root = ws.prepare(project, base, base)
        ws.git(root, "read-tree", "-u", "--reset", target)
        if ws.git(root, "status", "--porcelain").strip():
            ws.git(root, "commit", "--quiet", "-m", f"Rollback to {target[:7]} (undo live release {cur.commit})")
        sha = ws.git(root, "rev-parse", "--short", "HEAD").strip()
        ws.git(root, "push", "--quiet", "origin", base, auth=True)
    except ws.WorkspaceError as e:
        raise DeployError(f"Could not prepare the earlier version ({e}). Live was not changed.")
    _deploy_live(project, live, job)
    if with_db:
        snap = cur.snapshot
        if snap is None or not snap.ok or ":" not in snap.storage_url:
            raise DeployError("Code is back, but there is no database snapshot for that release to restore.")
        note(job, "Restoring the live database from the snapshot")
        _run(live.server, restore_script(project, live, snap.storage_url.split(":", 1)[1]),
             job, "Code is back, but restoring the database snapshot failed — see the output below.", timeout=3600)
    ok, status = _check_and_watch(project, live, job)
    cur.rolled_back = True
    live.current_commit = sha
    db.session.add(Deployment(project_id=project.id, environment_id=live.id, commit=sha, previous_commit=None,
                              by_user_id=job.by_user_id, result="ok" if ok else "failed",
                              note=f"Rollback from {cur.commit}" + (" + database snapshot" if with_db else "")))
    for c in Change.query.filter(Change.id.in_(cur.change_ids or [])).all():
        c.status, c.live_at = "beta", None
        db.session.add(ChangeMessage(change_id=c.id, sender="claude",
                                     body="This change was rolled back from live. Tell me what went wrong and I will fix it on beta; "
                                          "then it goes through tests and client approval again."))
    if ok:
        project.incident, project.incident_at = "", None
    log("Rollback", project_id=project.id, user=job.by_user,
        details=f"{cur.commit} → {target[:7]} (new commit {sha})" + (" + database snapshot" if with_db else ""))
    db.session.commit()
    if not ok:
        raise DeployError(f"Rolled back, but the live site still does not answer ({'no answer' if status == 0 else status}). Check the server.")
    note(job, f"Live is back on the earlier version · {sha}")
