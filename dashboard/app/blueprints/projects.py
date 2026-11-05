"""Project page (S3): coloured header, status strip, number tiles, beta / live cards, actions, tabs
(Chat · Changes · Deploys · Backups · Settings). Settings saves the beta safety switches (F13).
The actions (sync, deploy, Go Live, rollback, chat with Claude) arrive in Steps 7-10."""
import re
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, make_response, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app import health
from app.auth import log, owner_required, project_or_404
from app.extensions import db
from app.models import Backup, Change, ChangeAttachment, ChangeMessage, Deployment, Environment, User
from app.models.project import MESSAGE_MODES, PAYMENT_MODES
from app.models.setting import get_setting

bp = Blueprint("projects", __name__)

STEPS = [("beta", "In beta"), ("passed", "Tests passed"), ("client", "With client"), ("approved", "Approved"), ("live", "Live")]
STEP_INDEX = {"cancelled": 0, "beta": 0, "passed": 1, "client": 2, "changes": 2, "approved": 3, "live": 4}


def status_info(c):
    """(label, colour) for a change's status pill — same wording as the mock."""
    if c.status == "beta":
        return ("Tests failing", "warn") if c.tests_total is not None and not c.tests_ok else ("In beta", "accent")
    return {"passed": ("Tests passed", "ok"), "client": ("With client", "accent"),
            "changes": ("Client asked for changes", "warn"), "approved": ("Approved", "ok"),
            "cancelled": ("Cancelled", "")}.get(c.status, ("Live", ""))


@bp.get("/projects/<int:project_id>")
@login_required
def detail(project_id):
    p = project_or_404(project_id)
    r = health.summary(p)
    changes = Change.query.filter_by(project_id=p.id).order_by(Change.created_at.desc()).all()
    open_changes = [c for c in changes if c.status not in ("live", "cancelled")]
    deploys = (Deployment.query.filter_by(project_id=p.id).order_by(Deployment.created_at.desc()).all())
    live_now = next((d for d in deploys if d.environment.kind == "live" and not d.rolled_back), None)
    backups = (Backup.query.filter_by(project_id=p.id).order_by(Backup.created_at.desc()).limit(24).all())
    devs = [m.user for m in p.members if m.user.role == "developer"]
    sel = request.args.get("change", type=int)
    current = next((c for c in open_changes if c.id == sel), open_changes[0] if open_changes else None)
    from app.ai_models import PRESETS
    from app import agent, cc_models, deploy, jobs
    from app.models import Job, Server
    last_beta = next((d for d in deploys if d.environment.kind == "beta" and d.result == "ok"), None)

    def on_beta(c):
        """Is the change's latest version on the beta site? (in the newest good beta deploy, made after Claude's last run)"""
        if last_beta is None or c.id not in (last_beta.change_ids or []):
            return False
        last_claude = max((m.created_at for m in c.messages if m.sender == "claude"), default=None)
        return last_claude is None or last_beta.created_at >= last_claude

    from app.golive import rollback_target
    rb_cur, rb_target = rollback_target(p)
    last_snap = next((b for b in backups if b.kind == "snapshot" and b.ok), None)
    recent_jobs = Job.query.filter_by(project_id=p.id).order_by(Job.id.desc()).limit(10).all()
    running_job = next((j for j in recent_jobs if j.state in ("queued", "running")), None)
    from app import mobile
    from app.models import AppBuild, ChangeQuestion
    open_questions = {}
    for q in (ChangeQuestion.query.filter(ChangeQuestion.change_id.in_([c.id for c in open_changes] or [0]),
                                          ChangeQuestion.status == "open").order_by(ChangeQuestion.id).all()):
        open_questions.setdefault(q.change_id, []).append(q)
    app_builds = AppBuild.query.filter_by(project_id=p.id).order_by(AppBuild.id.desc()).limit(8).all()
    for b in app_builds:
        if b.status in mobile.RUNNING:
            mobile.refresh(b)
    return render_template("projects/detail.html", page="projects", crumb=p.name, title=p.name, p=p, r=r,
                           app_builds=app_builds, qr=mobile.qr_svg,
                           open_questions=open_questions, app_todo=mobile.needs_build(p, current),
                           short_dl=lambda b: (current_app.config.get("PUBLIC_URL") or request.url_root).rstrip("/")
                           + url_for("projects.app_download", build_id=b.id, token=mobile.download_token(b)), now_ts=__import__("app.clock", fromlist=["now"]).now(),
                           PRESETS=PRESETS, default_preset=get_setting("claude_preset") or "normal",
                           CC=cc_models.load(), cc_default=cc_models.default_choice(), cc_credit_models=cc_models.needs_credits(),
                           **_cc_picker(),
                           cc_max_turns=get_setting("claude_max_turns") or 40, limit_stop=agent.stopped_by_limit,
                           max_images=MAX_IMAGES, max_image_mb=MAX_IMAGE_BYTES // (1024 * 1024),
                           changes=changes, open_changes=open_changes, current=current, deploys=deploys,
                           live_now=live_now, backups=backups, devs=devs, STEPS=STEPS, STEP_INDEX=STEP_INDEX,
                           status_info=status_info, servers=Server.query.order_by(Server.name).all(),
                           recent_jobs=recent_jobs, running_job=running_job, busy=jobs.busy_reason(p.id),
                           env_off_example=deploy.default_env_off(p), setup=setup_steps(p, running_job),
                           rb_cur=rb_cur, rb_target=rb_target, last_snap=last_snap, on_beta=on_beta,
                           beta_login=__import__("app.betas", fromlist=["beta_auth"]).beta_auth(p.env("beta")),
                           repo_problem=__import__("app.gitcheck", fromlist=["problem"]).problem(p, p.env("live")),
                           beta_names=__import__("app.betas", fromlist=["names"]).names(p),
                           can_make_beta=__import__("app.betas", fromlist=["can_make_beta"]).can_make_beta(p))


@bp.post("/projects/<int:project_id>/settings")
@login_required
def save_settings(project_id):
    """Beta safety switches (F13). Real users / live payment keys on beta are Owner-only."""
    p = project_or_404(project_id)
    beta = p.env("beta")
    if beta is None:
        abort(404)
    msg, pay = request.form.get("msg"), request.form.get("pay")
    if msg not in MESSAGE_MODES or pay not in PAYMENT_MODES:
        abort(400)
    if not current_user.is_owner and (msg == "on" or pay == "live"):
        flash("Only the owner can send real messages or use live payment keys on beta.", "bad")
        return redirect(url_for("projects.detail", project_id=p.id) + "#settings")
    beta.message_mode = msg
    beta.payment_mode = pay
    beta.test_numbers = (request.form.get("test_numbers") or "").strip()[:2000]
    log("Settings", project_id=p.id, details=f"Beta messages: {msg} · payments: {pay}")
    db.session.commit()
    flash("Saved.", "ok")
    return redirect(url_for("projects.detail", project_id=p.id) + "#settings")


def _url(v):
    v = (v or "").strip().rstrip("/")
    if v and not v.startswith(("http://", "https://")):
        v = "https://" + v
    return v


@bp.post("/projects/<int:project_id>/details")
@login_required
@owner_required
def save_details(project_id):
    """Owner fills in / changes a project's details (needed for every repo that arrives from GitHub)."""
    p = project_or_404(project_id)
    f = request.form
    name, client = (f.get("name") or "").strip(), (f.get("client") or "").strip()
    live_url, beta_url = _url(f.get("live_url")), _url(f.get("beta_url"))
    repo = (f.get("repo") or "").strip().strip("/")
    tech = [t.strip() for t in (f.get("tech") or "").split(",") if t.strip()][:6]
    if not beta_url:                                   # empty = Claude makes it at <project>.beta.ivinfotech.com
        from app.betas import names
        beta_url = "https://" + names(p)["host"]
    err = (not name and "Enter the project name.") or (not client and "Enter the client name.") or \
          (not live_url and "Enter the live address.") or \
          (repo and repo.count("/") != 1 and "GitHub repo looks like owner/name.") or None
    if err:
        flash(err, "bad")
        return redirect(url_for("projects.detail", project_id=p.id) + "#settings")
    if repo != (p.github_repo or ""):
        p.repo_missing = False
    p.name, p.client_name, p.tech_stack, p.github_repo = name, client, tech, repo or None
    p.is_active = f.get("active") == "1"
    p.monitor_enabled = f.get("watch") == "1"
    from app.models import Server
    pick, srv, relabel, host_label = f.get("server"), None, True, None
    if pick == "cpanel":
        p.host_type, host_label = "cpanel", "cPanel"
    elif pick is not None and pick.startswith("s:") and pick[2:].isdigit():
        srv = db.session.get(Server, int(pick[2:])) or abort(400)
        p.host_type, host_label = "vps", srv.name
    elif pick == "":
        p.host_type = "vps"
    else:                                               # "keep" (or an older form): leave hosting as it is
        relabel = False
    for kind, url, branch in (("live", live_url, "main"), ("beta", beta_url, "beta")):
        e = p.env(kind)
        if e is None:
            e = Environment(kind=kind, branch=branch)
            p.environments.append(e)
        e.url = url
        if relabel:
            e.server_host = host_label
    if srv is not None:
        p.env("live").server_id = srv.id
    elif pick == "":
        p.env("live").server_id = None
    p.needs_setup = False
    log("Project details", project_id=p.id, details=f"{name} · {client} · {p.host_type}")
    db.session.commit()
    from app.betas import maybe_auto_setup
    if maybe_auto_setup(p, current_user):
        flash("Project details saved. Claude is making the beta now — follow it on the Deploys tab.", "ok")
        return redirect(url_for("projects.detail", project_id=p.id) + "#deploys")
    flash("Project details saved.", "ok")
    return redirect(url_for("projects.detail", project_id=p.id) + "#settings")


@bp.post("/projects/<int:project_id>/accept-page")
@login_required
@owner_required
def accept_page(project_id):
    """The home page change is expected (e.g. the client edited it): take a new fingerprint, clear the red."""
    from app.monitor import accept_page as take_new_fingerprint
    p = project_or_404(project_id)
    take_new_fingerprint(p)
    log("Monitor", project_id=p.id, details="Home page change accepted as the new normal")
    db.session.commit()
    flash("OK — the current home page is now the normal one.", "ok")
    return redirect(url_for("projects.detail", project_id=p.id))


@bp.post("/projects/<int:project_id>/check")
@login_required
def check_now(project_id):
    from app.monitor import check
    p = project_or_404(project_id)
    res = check(p)
    flash({"ok": "Live site answered normally.", "down": "Live site is not answering.", "retry": "No answer — checking again next round.",
           "changed": "The home page text changed.", "no-live": "Add the live address first (Settings tab)."}.get(res, res),
          "ok" if res == "ok" else "bad")
    return redirect(url_for("projects.detail", project_id=p.id))


# ---------- chat with Claude (F06) ----------
IMAGE_TYPES = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 3


def _upload_dir():
    d = Path(current_app.instance_path) / "uploads" / "changes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _title_from(text):
    t = text.strip().split("\n")[0].split(". ")[0].strip()
    if len(t) > 60:
        t = t[:57].rsplit(" ", 1)[0] + "…"
    return (t[:1].upper() + t[1:]) or "Change"


def setup_steps(p, running_job=None):
    """The three things a new project needs before changes can start: details, live folder, beta. Chat stays locked until all
    are done — except on projects that already have changes (they were set up before this check existed)."""
    live, beta = p.env("live"), p.env("beta")
    making = bool(running_job and running_job.kind == "beta_setup")
    steps = [
        {"title": "Fill in the project details", "done": bool(not p.needs_setup and p.client_name and live and live.url),
         "hint": "Settings tab → Project details: client, server and live address, then Save.", "go": "settings"},
        {"title": "Set the live folder and database", "done": bool(live and live.server_id and live.deploy_path),
         "hint": "Settings tab → Servers & deploy: press Auto-detect from the server, check, then Save.", "go": "settings"},
        {"title": "Make the beta (test) site", "done": bool(beta and beta.server_id and beta.deploy_path),
         "hint": "Claude is making it now — a few minutes." if making else "Starts by itself once step 2 is saved. Owner can also start it.",
         "go": "", "making": making},
    ]
    first = next((x for x in steps if not x["done"]), None)
    if first:
        first["now"] = True
    locked = first is not None and not p.changes
    return {"steps": steps, "locked": locked, "first": first, "done": sum(x["done"] for x in steps)}


@bp.post("/projects/<int:project_id>/changes/<int:change_id>/cancel")
@login_required
@owner_required
def cancel_change(project_id, change_id):
    """Owner drops a change that is not wanted any more: it leaves beta (next Beta deploy) and can never go live. Branch and chat stay."""
    p = project_or_404(project_id)
    c = db.session.get(Change, change_id)
    if c is None or c.project_id != p.id:
        abort(404)
    if c.status == "live":
        flash("This change is already live — use Rollback to undo it.", "bad")
    elif c.status == "cancelled":
        flash("This change is already cancelled.", "bad")
    elif c.agent_state in ("queued", "working"):
        flash("Claude is still working on this change — wait for it to finish, then cancel.", "bad")
    else:
        c.status = "cancelled"
        log("Change cancelled", project_id=p.id, details=f"#{c.id} {c.title[:80]}")
        db.session.commit()
        flash(f"Change #{c.id} cancelled. It will not go live. Beta drops it at the next Beta deploy.", "ok")
    return redirect(url_for("projects.detail", project_id=p.id) + "#changes")


@bp.post("/projects/<int:project_id>/changes/<int:change_id>/reopen")
@login_required
@owner_required
def reopen_change(project_id, change_id):
    p = project_or_404(project_id)
    c = db.session.get(Change, change_id)
    if c is None or c.project_id != p.id:
        abort(404)
    if c.status == "cancelled":
        c.status = "beta" if c.tests_total is not None and not c.tests_ok else "passed"
        log("Change reopened", project_id=p.id, details=f"#{c.id} {c.title[:80]}")
        db.session.commit()
        flash(f"Change #{c.id} is back. Press Beta deploy to see it on beta again.", "ok")
    return redirect(url_for("projects.detail", project_id=p.id, change=c.id) + "#chat")


@bp.post("/projects/<int:project_id>/chat")
@login_required
def chat(project_id):
    """New change or a follow-up on an open one: save the message (+ screenshots), then start Claude on it."""
    from app import agent
    p = project_or_404(project_id)
    if setup_steps(p)["locked"]:
        flash("Finish the setup steps at the top of the project first.", "bad")
        return redirect(url_for("projects.detail", project_id=p.id))
    text = (request.form.get("message") or "").strip()
    files = [f for f in request.files.getlist("shots") if f and f.filename][:MAX_IMAGES + 1]
    if not text:
        flash("Write what should change.", "bad")
        return redirect(url_for("projects.detail", project_id=p.id) + "#chat")
    if len(files) > MAX_IMAGES:
        flash(f"Up to {MAX_IMAGES} screenshots at a time.", "bad")
        return redirect(url_for("projects.detail", project_id=p.id) + "#chat")
    saved = []
    for f in files:
        ext = (f.filename.rsplit(".", 1)[-1] if "." in f.filename else "").lower()
        data = f.read(MAX_IMAGE_BYTES + 1)
        if ext not in IMAGE_TYPES or len(data) > MAX_IMAGE_BYTES:
            flash("Screenshots must be PNG, JPG, WEBP or GIF, up to 5 MB each.", "bad")
            return redirect(url_for("projects.detail", project_id=p.id) + "#chat")
        saved.append((ext, data, f.filename[:200]))

    cid = request.form.get("change_id", type=int)
    if cid:
        c = db.session.get(Change, cid)
        if c is None or c.project_id != p.id:
            abort(404)
        if c.status == "cancelled":
            flash("This change was cancelled. Reopen it from the Changes tab, or start a new change.", "bad")
            return redirect(url_for("projects.detail", project_id=p.id) + "#chat")
        if c.status in ("approved", "live"):
            flash("This change is approved or live. Start a new change for anything else.", "bad")
            return redirect(url_for("projects.detail", project_id=p.id, change=c.id) + "#chat")
        if c.agent_state in ("queued", "working"):
            flash("Claude is still working on this change — wait for it to finish.", "bad")
            return redirect(url_for("projects.detail", project_id=p.id, change=c.id) + "#chat")
        a = c.approval
        if a is not None and a.decision is None:
            a.expires_at = a.sent_at          # the old client link closes: a new version needs a new link
    else:
        c = Change(project_id=p.id, title=_title_from(text), requested_by_id=current_user.id, status="beta")
        db.session.add(c)
        db.session.flush()
    from app.ai_budget import blocked_reason
    why = blocked_reason(p, current_user)
    if why:
        db.session.rollback()
        flash(why, "bad")
        return redirect(url_for("projects.detail", project_id=p.id, change=cid) + "#chat" if cid else url_for("projects.detail", project_id=p.id) + "#chat")
    busy = Change.query.filter(Change.project_id == p.id, Change.id != c.id,
                               Change.agent_state.in_(("queued", "working"))).first()
    if busy is not None:
        db.session.rollback()
        flash(f"Claude is busy with “{busy.title}” on this project. Send this when it has finished.", "bad")
        return redirect(url_for("projects.detail", project_id=p.id, change=busy.id) + "#chat")

    msg = ChangeMessage(change_id=c.id, sender="owner" if current_user.is_owner else "developer", user_id=current_user.id, body=text)
    db.session.add(msg)
    db.session.flush()
    for ext, data, name in saved:
        path = _upload_dir() / f"{secrets.token_hex(16)}.{ext}"
        path.write_bytes(data)
        db.session.add(ChangeAttachment(message_id=msg.id, kind="image", file_path=str(path), original_name=name))
    from app.ai_models import PRESETS
    if request.form.get("engine") in ("api", "claude_code"):      # picked in the chat (default Claude Code)
        p.ai_engine = request.form["engine"]
    pick = request.form.get("preset")
    c.agent_preset = pick if pick in PRESETS else (get_setting("claude_preset") or "normal")
    if p.ai_engine == "claude_code":     # model + effort from Claude Code's own list; (None, None) = no list → the preset stays
        from app import cc_models
        debug = request.form.get("debug") == "1"
        c.agent_debug = debug             # Debug mode: more steps, the bug-hunting protocol; Sonnet 5.5 + high effort unless a model was picked
        model_in, effort_in = request.form.get("model"), request.form.get("effort")
        if debug and not model_in:
            model_in, effort_in = "sonnet", effort_in or "high"
        from app import cc_logins
        acc = cc_logins.pick(request.form.get("account", type=int))     # an unknown id falls back to the default account
        c.agent_account_id = acc.id if acc else None
        c.agent_model, c.agent_effort = cc_models.resolve(model_in, effort_in, models=cc_logins.models_for(acc) if acc else None)
    c.status = "beta"
    c.agent_state, c.agent_note = "queued", "Waiting to start"
    log("Prompt", project_id=p.id, details=(f"Change #{c.id}: follow-up" if cid else f"New change: {c.title}"), prompt=text)
    db.session.commit()
    agent.start(c.id, background=not current_app.config.get("AGENT_SYNC"))
    return redirect(url_for("projects.detail", project_id=p.id, change=c.id) + "#chat")


def _cc_picker():
    """The chat's Claude account list, each with its own models (the default account first and pre-selected)."""
    from app import cc_logins
    dflt = cc_logins.default_account()
    rows = [{"a": a, "default": dflt is not None and a.id == dflt.id, "models": cc_logins.models_for(a)} for a in cc_logins.accounts()]
    return {"cc_accts": rows, "cc_list": cc_logins.models_for(dflt)}


@bp.get("/changes/<int:change_id>/status")
@login_required
def change_status(change_id):
    """Small JSON for the chat to show Claude's progress (polled every few seconds while it works)."""
    from app import agent
    c = db.session.get(Change, change_id)
    if c is None:
        abort(404)
    project_or_404(c.project_id)
    if c.agent_state in ("queued", "working") and not agent.is_running(c.id) and not current_app.config.get("AGENT_SYNC"):
        from app.clock import now
        started = c.agent_started_at or c.created_at
        if (now() - started).total_seconds() > 120:          # the thread is gone (server restarted)
            c.agent_state = "failed"
            c.agent_note = "Stopped because the dashboard restarted. Send the message again."
            from app.models import ChangeQuestion as _CQ
            _CQ.query.filter_by(change_id=c.id, status="open").update({"status": "expired"})      # nobody is waiting for these answers any more
            db.session.commit()
    from app.models import ChangeQuestion
    q = ChangeQuestion.query.filter_by(change_id=c.id, status="open").count()
    return jsonify(state=c.agent_state, note=c.agent_note, cost=f"{float(c.agent_cost_usd or 0):.2f}",
                   steps=(c.agent_steps or [])[-60:], open_questions=q)


@bp.post("/changes/<int:change_id>/questions/<int:qid>/answer")
@login_required
def answer_question(change_id, qid):
    """The owner answers Claude's question / permission card; Claude, waiting, continues with it."""
    from app.models import ChangeMessage, ChangeQuestion
    c = db.session.get(Change, change_id) or abort(404)
    p = project_or_404(c.project_id)
    q = ChangeQuestion.query.filter_by(id=qid, change_id=c.id).first() or abort(404)
    if q.kind in ("permission", "root") and not current_user.is_owner:
        abort(403)
    back = redirect(url_for("projects.detail", project_id=p.id, change=c.id) + "#chat")
    if q.status != "open":
        flash("That question was already answered or has expired.", "bad")
        return back
    if q.kind == "root":
        return _answer_root(c, p, q, back)
    answer = (request.form.get("answer") or "").strip()
    extra = (request.form.get("note") or "").strip()
    text = (answer + (f" — {extra}" if extra else "")).strip(" —")[:2000]
    if not text:
        flash("Pick an answer or write one.", "bad")
        return back
    q.status, q.answer, q.answered_by_id, q.answered_at = "answered", text, current_user.id, __import__("app.clock", fromlist=["now"]).now()
    db.session.add(ChangeMessage(change_id=c.id, sender="owner" if current_user.is_owner else "developer", user_id=current_user.id,
                                 body=f"Answer to Claude: {text}"))
    log("Claude question answered", project_id=p.id, details=f"Change #{c.id}: {q.question[:80]} → {text[:80]}")
    db.session.commit()
    flash("Sent — Claude continues.", "ok")
    return back


def _claude_is_waiting(change_id):
    """Is a Claude Code run on this change still running (and so still waiting for the card's answer)?"""
    from app.models import Job
    return any((j.context or {}).get("change_id") == change_id for j in Job.query.filter_by(kind="change_debug", state="running").all())


def _answer_root(c, p, q, back):
    """A root-command card: "Deny", or "Run as root" with the 6-digit code from the owner's authenticator app. The root service
    checks the code against the card's exact command (taken from the database, not from the form) and answers with a one-use
    ticket; that waits in the card's row for Claude's tool. The code itself is never stored or logged."""
    from app import beta_tools, rootd
    from app.alerts import raise_alert
    from app.clock import now
    from app.models import ChangeMessage, ChangeQuestion
    decision = (request.form.get("answer") or "").strip()
    ticket = None
    if decision == "Run as root":
        code = re.sub(r"\s+", "", request.form.get("authcode") or "")
        if not re.fullmatch(r"[0-9]{6}", code):
            flash("Type the 6-digit code from your authenticator app.", "bad")
            return back
        if not q.code:
            flash("This card has no command, so nothing can be approved.", "bad")
            return back
        if not _claude_is_waiting(c.id):          # an answer nobody is waiting for would only leave an approval lying around
            ChangeQuestion.query.filter_by(id=q.id, status="open").update({"status": "expired"})
            db.session.commit()
            flash("Claude is no longer working on this change, so this request has expired and nothing was approved. Ask Claude again.", "bad")
            return back
        before = rootd.status()
        if before["locked"]:
            flash(f"Root commands are locked for about {before['retry_after'] // 60 + 1} more minute(s) after wrong codes. Nothing was used up.", "bad")
            return back
        try:
            ticket, why = rootd.authorize(code, q.code, current_user.name)
        except rootd.RootdError:
            flash("The root service on the server did not answer. Nothing was approved.", "bad")
            return back
        if not ticket:
            log("Root command: code not accepted", project_id=p.id, details=f"Change #{c.id}")
            if rootd.status()["locked"] and not before["locked"]:
                raise_alert("error", "Root commands are locked after wrong authenticator codes. If this was not you, someone is guessing: check /var/log/aidash-rootd.log.", None)
            db.session.commit()
            flash(why or "That code was not accepted.", "bad")
            return back
    elif decision != "Deny":
        flash("Press “Run as root” (with the code) or “Deny”.", "bad")
        return back
    won = ChangeQuestion.query.filter_by(id=q.id, status="open").update(
        {"status": "answered", "answer": decision, "answered_by_id": current_user.id, "answered_at": now(), "ticket": ticket},
        synchronize_session=False)
    if not won:
        db.session.rollback()
        flash("That question was already answered or has expired.", "bad")
        return back
    db.session.add(ChangeMessage(change_id=c.id, sender="owner", user_id=current_user.id, body=f"Answer to Claude: {decision}"))
    log("Root command " + ("approved with an authenticator code" if ticket else "denied"), project_id=p.id,
        details=f"Change #{c.id}: {beta_tools._redact(q.code or '')[:200]}")
    db.session.commit()
    flash("Approved — Claude runs it now." if ticket else "Denied.", "ok")
    return back


@bp.get("/attachments/<int:att_id>")
@login_required
def attachment(att_id):
    a = db.session.get(ChangeAttachment, att_id)
    if a is None:
        abort(404)
    project_or_404(a.message.change.project_id)
    path = Path(a.file_path).resolve()
    if _upload_dir().resolve() not in path.parents or not path.is_file():
        abort(404)
    resp = send_file(path, mimetype={"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
                                     "gif": "image/gif"}.get(path.suffix.lstrip(".").lower(), "application/octet-stream"))
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


# ---------- servers & deploy (F07 / F08) ----------
PATH_RE = re.compile(r"^/[A-Za-z0-9._/\-]{1,250}$")
DB_RE = re.compile(r"^([A-Za-z0-9_\-]{1,64}|sqlite:[A-Za-z0-9_\-][A-Za-z0-9._/\-]{0,200})$")   # name, or sqlite:<file in the folder>


@bp.post("/projects/<int:project_id>/detect-live")
@login_required
@owner_required
def detect_live(project_id):
    """Auto-detect the live folder and database from the server picked in the form (read-only; nothing is saved until the owner saves)."""
    from urllib.parse import urlparse

    from app import deploy, remote
    from app.models import Server
    p = project_or_404(project_id)
    s = db.session.get(Server, request.form.get("server", type=int) or 0)
    live = p.env("live")
    host = (urlparse(live.url or "").hostname or "") if live else ""
    if s is None:
        return jsonify(error="Pick the live server first.")
    if not re.match(r"^[A-Za-z0-9.\-]{3,190}$", host):
        return jsonify(error="Save the project's live address first.")
    try:
        found, why = deploy.detect_live(s, host)
    except remote.RemoteError as e:
        return jsonify(error=str(e))
    if found is None:
        return jsonify(error=why)
    log("Project settings", project_id=p.id, details=f"Live folder detected on {s.name}: {found['path']}")
    db.session.commit()
    return jsonify(**found)


@bp.post("/projects/<int:project_id>/deploy-settings")
@login_required
@owner_required
def save_deploy(project_id):
    """Where beta / live run (server, folder, database) and how a deploy runs there. Owner only."""
    from app import deploy
    from app.models import Server
    p = project_or_404(project_id)
    f = request.form
    back = redirect(url_for("projects.detail", project_id=p.id) + "#settings")
    for kind in ("beta", "live"):
        e = p.env(kind)
        if e is None:
            flash("Save the project details (beta and live addresses) first.", "bad")
            return back
        sid = f.get(f"{kind}_server", type=int)
        path = (f.get(f"{kind}_path") or "").strip().rstrip("/")
        dbn = (f.get(f"{kind}_db") or "").strip()
        if sid and db.session.get(Server, sid) is None:
            abort(400)
        if path and (not PATH_RE.match(path) or ".." in path):
            flash(f"The {kind} folder must be a full path like /home/user/site (letters, numbers, . _ - /).", "bad")
            return back
        if dbn and (not DB_RE.match(dbn) or ".." in dbn):
            flash(f"The {kind} database must be a name (letters, numbers, _ and -) or sqlite:path/in/the/folder.db.", "bad")
            return back
        e.server_id, e.deploy_path, e.db_name = sid or None, path or None, dbn or None
    beta, live = p.env("beta"), p.env("live")
    if beta.server_id and beta.server_id == live.server_id and (
            (beta.db_name and beta.db_name == live.db_name) or (beta.deploy_path and beta.deploy_path == live.deploy_path)):
        flash("Beta and live can't share the same folder or database on one server — beta must be a separate copy.", "bad")
        db.session.rollback()
        return back
    texts = {k: (f.get(k) or "").replace("\r\n", "\n").strip()[:4000] for k in ("beta_env_off", "beta_env_test", "beta_env_sandbox")}
    try:
        for t in texts.values():
            deploy.parse_env_lines(t)
    except deploy.DeployError as e:
        flash(e.plain, "bad")
        db.session.rollback()
        return back
    p.beta_env_off, p.beta_env_test, p.beta_env_sandbox = texts["beta_env_off"], texts["beta_env_test"], texts["beta_env_sandbox"]
    p.db_engine = f.get("db_engine") if f.get("db_engine") in ("mysql", "postgres", "sqlite") else "mysql"
    log("Project settings", project_id=p.id, details="Servers & deploy saved")
    db.session.commit()
    from app.betas import maybe_auto_setup
    if maybe_auto_setup(p, current_user):
        flash("Servers & deploy saved. Claude is making the beta now — follow it on the Deploys tab.", "ok")
        return redirect(url_for("projects.detail", project_id=p.id) + "#deploys")
    flash("Servers & deploy saved.", "ok")
    return back


@bp.post("/projects/<int:project_id>/jobs/<kind>")
@login_required
def start_job(project_id, kind):
    """Beta deploy / Live → Beta sync. Both touch beta only (live is only read by the sync)."""
    from app import betas, deploy, jobs
    from app.models import Job
    p = project_or_404(project_id)
    fn = {"beta_deploy": betas.deploy_fn(p), "sync": deploy.run_sync, "beta_setup": betas.run_setup,
          "beta_lock": betas.run_lock}.get(kind)
    if fn is None:
        abort(404)
    if kind in ("beta_setup", "beta_lock") and not current_user.is_owner:
        abort(403)
    busy = jobs.busy_reason(p.id)
    if busy:
        flash(busy, "bad")
        return redirect(url_for("projects.detail", project_id=p.id))
    j = Job(project_id=p.id, kind=kind, by_user_id=current_user.id,
            note="New password" if kind == "beta_lock" and request.form.get("new_password") == "1" else "Waiting to start")
    db.session.add(j)
    log("Job", project_id=p.id, details={"beta_deploy": "Beta deploy started", "sync": "Live → Beta sync started",
                                         "beta_setup": "Automatic beta setup started", "beta_lock": "Beta password set"}[kind])
    db.session.commit()
    jobs.start(j.id, fn, background=not current_app.config.get("AGENT_SYNC"))
    return redirect(url_for("projects.detail", project_id=p.id) + "#deploys")


@bp.get("/jobs/<int:job_id>/status")
@login_required
def job_status(job_id):
    from app import jobs
    from app.clock import now
    from app.models import Job
    j = db.session.get(Job, job_id) or abort(404)
    project_or_404(j.project_id)
    if j.state in ("queued", "running") and not jobs.is_running(j.id) and not current_app.config.get("AGENT_SYNC"):
        if (now() - (j.started_at or j.created_at)).total_seconds() > 60:      # the thread is gone (dashboard restarted)
            j.state, j.note, j.finished_at = "failed", "Stopped because the dashboard restarted. Check the server, then start it again.", now()
            db.session.commit()
    return jsonify(state=j.state, note=j.note)


# ---------- Go Live / Rollback (F10 / F11) — Owner only, authenticator code every time ----------
def _confirm_code(back):
    from app.auth import check_totp
    if not current_user.totp_enabled:
        flash("Turn on the authenticator app (My account) — Go Live and Rollback need its code.", "bad")
        return back
    if not check_totp(current_user, request.form.get("code")):
        db.session.commit()                                   # keep the replay guard even on a wrong code
        log("Confirm code wrong", details="Go Live / Rollback refused")
        db.session.commit()
        flash("The authenticator code is wrong or already used. Wait for the next code and try again.", "bad")
        return back
    return None


@bp.post("/projects/<int:project_id>/go-live")
@login_required
@owner_required
def go_live(project_id):
    from app import golive, jobs
    from app.models import Job
    p = project_or_404(project_id)
    back = redirect(url_for("projects.detail", project_id=p.id))
    approved = [c.id for c in Change.query.filter_by(project_id=p.id, status="approved").all()]
    others = [c.id for c in Change.query.filter(Change.project_id == p.id, Change.status.in_(("passed", "client", "changes"))).all()]
    reason = ""
    ship = list(approved)
    if request.form.get("override") == "1" and others:
        reason = (request.form.get("reason") or "").strip()[:1000]
        if not reason:
            flash("Write why these go live without client approval.", "bad")
            return back
        ship += others
    if not ship:
        flash("Nothing to send. Wait for the client to approve, or tick the box with a reason.", "bad")
        return back
    busy = jobs.busy_reason(p.id)
    if busy:
        flash(busy, "bad")
        return back
    stop = _confirm_code(back)
    if stop:
        return stop
    j = Job(project_id=p.id, kind="go_live", note="Waiting to start", by_user_id=current_user.id)
    db.session.add(j)
    log("Job", project_id=p.id, details=f"Go Live started: {len(ship)} change{'s' if len(ship) != 1 else ''}"
                                         + (f" · without approval: {reason}" if reason else ""))
    db.session.commit()
    jobs.start(j.id, lambda job: golive.run_go_live(job, ship, reason), background=not current_app.config.get("AGENT_SYNC"))
    return redirect(url_for("projects.detail", project_id=p.id) + "#deploys")


@bp.post("/projects/<int:project_id>/rollback")
@login_required
@owner_required
def rollback(project_id):
    from app import golive, jobs
    from app.models import Job
    p = project_or_404(project_id)
    back = redirect(url_for("projects.detail", project_id=p.id))
    cur, target = golive.rollback_target(p)
    if cur is None or cur.id != request.form.get("deployment", type=int):
        flash("There is no earlier version to go back to, or live changed — open Rollback again.", "bad")
        return back
    busy = jobs.busy_reason(p.id)
    if busy:
        flash(busy, "bad")
        return back
    stop = _confirm_code(back)
    if stop:
        return stop
    with_db = request.form.get("with_db") == "1"
    dep_id = cur.id
    j = Job(project_id=p.id, kind="rollback", note="Waiting to start", by_user_id=current_user.id)
    db.session.add(j)
    log("Job", project_id=p.id, details=f"Rollback started: {cur.commit} → {target[:7]}" + (" + database snapshot" if with_db else ""))
    db.session.commit()
    jobs.start(j.id, lambda job: golive.run_rollback(job, dep_id, with_db), background=not current_app.config.get("AGENT_SYNC"))
    return redirect(url_for("projects.detail", project_id=p.id) + "#deploys")


@bp.post("/projects/<int:project_id>/live-check")
@login_required
@owner_required
def live_check(project_id):
    from app import livesync, remote
    p = project_or_404(project_id)
    back = redirect(url_for("projects.detail", project_id=p.id) + "#deploys")
    try:
        st = livesync.refresh(p)
    except remote.RemoteError as e:
        flash(str(e), "bad")
        return back
    if st is None:
        flash("Set the live server and folder first (Settings → Servers & deploy).", "bad")
    elif not st["fetch_ok"]:
        flash("The live server cannot reach GitHub — check its GitHub connection.", "bad")
    return back


def _live_job(project_id, kind, label, run):
    from app import jobs
    from app.models import Job
    p = project_or_404(project_id)
    back = redirect(url_for("projects.detail", project_id=p.id) + "#deploys")
    busy = jobs.busy_reason(p.id)
    if busy:
        flash(busy, "bad")
        return back
    stop = _confirm_code(back)
    if stop:
        return stop
    j = Job(project_id=p.id, kind=kind, note="Waiting to start", by_user_id=current_user.id)
    db.session.add(j)
    log("Job", project_id=p.id, details=f"{label} started")
    db.session.commit()
    jobs.start(j.id, run, background=not current_app.config.get("AGENT_SYNC"))
    return back


@bp.post("/projects/<int:project_id>/live-push")
@login_required
@owner_required
def live_push(project_id):
    from app import livesync
    return _live_job(project_id, "live_push", "Commit & push of the live folder", livesync.run_commit_push)


@bp.post("/projects/<int:project_id>/live-update")
@login_required
@owner_required
def live_update(project_id):
    from app import livesync
    return _live_job(project_id, "live_update", "Update of live from main", livesync.run_update)


@bp.post("/projects/<int:project_id>/engine")
@login_required
@owner_required
def save_engine(project_id):
    """Claude API or Claude Code on the server, per project (owner, 2026-10-16)."""
    p = project_or_404(project_id)
    engine = request.form.get("engine")
    if engine not in ("api", "claude_code"):
        abort(400)
    if Change.query.filter(Change.project_id == p.id, Change.agent_state.in_(("queued", "working"))).first():
        flash("Claude is working on this project — change the engine when it has finished.", "bad")
        return redirect(url_for("projects.detail", project_id=p.id) + "#settings")
    fresh = request.form.get("new_session") == "1"
    if fresh:
        p.cc_session_id = None
    p.ai_engine = engine
    log("Project settings", project_id=p.id, details=f"AI engine: {'Claude Code on the server' if engine == 'claude_code' else 'Claude API'}"
                                                     + (" · fresh Claude Code session" if fresh else ""))
    db.session.commit()
    flash("AI engine saved.", "ok")
    return redirect(url_for("projects.detail", project_id=p.id) + "#settings")


@bp.post("/projects/<int:project_id>/memory")
@login_required
@owner_required
def save_memory(project_id):
    """How Claude Code keeps this project's memory: it manages md files in the repo itself (default), or keeps the files the owner picked."""
    from app import project_notes
    p = project_or_404(project_id)
    mode = "files" if request.form.get("mode") == "files" else "auto"
    files = project_notes.memory_paths(request.form.getlist("files")) if mode == "files" else []
    back = redirect(url_for("projects.detail", project_id=p.id) + "#project-memory")
    if mode == "files" and not files:
        flash("Tick at least one file, or choose “Claude manages it”.", "bad")
        return back
    p.memory_mode, p.memory_files = mode, files
    log("Project settings", project_id=p.id, details="Project memory: " + (", ".join(files) if mode == "files" else "Claude manages it"))
    db.session.commit()
    flash("Project memory saved.", "ok")
    return back


@bp.get("/projects/<int:project_id>/memory-files")
@login_required
@owner_required
def memory_files(project_id):
    """The md / txt files of the project's repo, for the memory picker (JSON)."""
    from app import github
    p = project_or_404(project_id)
    if not p.github_repo:
        return jsonify(error="This project has no GitHub repo yet.", files=[])
    try:
        return jsonify(files=github.list_doc_files(p.github_repo, p.default_branch or "main"))
    except github.GitHubError as e:
        return jsonify(error=str(e), files=[])


@bp.post("/changes/<int:change_id>/continue")
@login_required
def continue_change(change_id):
    """Claude Code stopped at a limit (steps, cost, 30 minutes): pick up where it stopped, in the same session."""
    from app import agent
    from app.ai_budget import blocked_reason
    from app.models import ChangeMessage
    c = db.session.get(Change, change_id) or abort(404)
    p = project_or_404(c.project_id)
    back = redirect(url_for("projects.detail", project_id=p.id, change=c.id) + "#chat")
    if p.ai_engine != "claude_code" or not agent.stopped_by_limit(c) or c.status in ("approved", "live"):
        flash("There is nothing to continue on this change.", "bad")
        return back
    why = blocked_reason(p, current_user)
    if why:
        flash(why, "bad")
        return back
    busy = Change.query.filter(Change.project_id == p.id, Change.id != c.id, Change.agent_state.in_(("queued", "working"))).first()
    if busy is not None:
        flash(f"Claude is busy with “{busy.title}” on this project. Continue when it has finished.", "bad")
        return back
    # claim the change in one step: two clicks (two tabs, owner and developer) must not start two runs on the same checkout
    claimed = Change.query.filter(Change.id == c.id, Change.agent_state == "failed").update(
        {"agent_state": "queued", "agent_note": "Waiting to start"}, synchronize_session=False)
    if claimed != 1:
        db.session.rollback()
        flash("Claude has already been asked to continue.", "bad")
        return back
    # self-contained, so it also works when the saved session is gone or another change has used it since
    text = ("Continue: your previous run stopped at its limit. Look at git status and the diff to see what is already done, "
            "then finish the original request.")
    db.session.add(ChangeMessage(change_id=c.id, sender="owner" if current_user.is_owner else "developer", user_id=current_user.id, body=text))
    log("Prompt", project_id=p.id, details=f"Change #{c.id}: continue after a limit", prompt=text)
    db.session.commit()
    agent.start(c.id, background=not current_app.config.get("AGENT_SYNC"))
    return back


@bp.get("/d/<int:build_id>/<token>")
def app_download(build_id, token):
    """Public short link to a test app (opened on a phone from the QR code — no dashboard login there). Only with the
    build's secret token, only while the build is ready and its Codemagic link is valid."""
    import hmac
    from app import mobile
    from app.models import AppBuild
    b = db.session.get(AppBuild, build_id)
    if (b is None or b.status != "ready" or not b.file_url or not hmac.compare_digest(token, mobile.download_token(b))
            or (b.url_expires_at and b.url_expires_at < __import__("app.clock", fromlist=["now"]).now())):
        resp = make_response("This download link is not valid any more — open the project in the dashboard for a new one.", 404)
    else:
        resp = redirect(b.file_url)
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Robots-Tag"] = "noindex"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/projects/<int:project_id>/apps/scan")
@login_required
def apps_scan(project_id):
    """Look for Flutter apps in the repo (CHANGES Step 10)."""
    from app import mobile
    p = project_or_404(project_id)
    try:
        apps = mobile.scan(p)
        log("Project settings", project_id=p.id, details=f"Mobile apps found: {', '.join(a['dir'] for a in apps) or 'none'}")
        db.session.commit()
        flash(f"Found {len(apps)} mobile app{'s' if len(apps) != 1 else ''}." if apps else "No Flutter app in this repo.", "ok")
        beta = p.env("beta")
        from app import betas, jobs
        from app.models import Job
        if apps and beta is not None and beta.managed and beta.server_id and not jobs.busy_reason(p.id):
            j = Job(project_id=p.id, kind="beta_api", note="Waiting to start (mobile apps found)", by_user_id=current_user.id)
            db.session.add(j)
            log("Job", project_id=p.id, details="Opening the beta API for the mobile apps")
            db.session.commit()
            jobs.start(j.id, betas.run_app_api, background=not current_app.config.get("AGENT_SYNC"))
    except mobile.MobileError as e:
        flash(str(e), "bad")
    return redirect(url_for("projects.detail", project_id=p.id) + "#apps")


@bp.post("/projects/<int:project_id>/apps/build")
@login_required
def apps_build(project_id):
    """A test build (Android APK, pointed at the beta) of one app — from a change or the default branch."""
    from app import mobile
    p = project_or_404(project_id)
    from app import jobs
    busy = jobs.busy_reason(p.id)
    if busy:
        flash(busy + " — start the test app build when it has finished.", "bad")
        return redirect(url_for("projects.detail", project_id=p.id) + "#apps")
    change = None
    cid = request.form.get("change_id", type=int)
    if cid:
        change = Change.query.filter_by(id=cid, project_id=p.id).first() or abort(404)
    try:
        mobile.start(p, (request.form.get("app_dir") or "").strip(), current_user, change)
        flash("Test app build started on Codemagic — it takes 10–20 minutes; this page shows the download link when it is ready.", "ok")
    except mobile.MobileError as e:
        flash(str(e), "bad")
    return redirect(url_for("projects.detail", project_id=p.id) + "#apps")
