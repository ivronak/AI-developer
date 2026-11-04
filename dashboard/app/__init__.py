"""App factory."""
import click
from flask import Flask, url_for
from flask_login import current_user

from config import get_config, validate_production

from .extensions import csrf, db, login_manager, migrate
from .icons import icon
from .ui_helpers import hue, initials, tech_hue

# Main navigation (icon pill in the top bar + phone tab bar). owner_only items are hidden from developers —
# the routes themselves are protected with @owner_required (hiding a link is never the protection).
NAV = [
    {"key": "projects", "endpoint": "main.projects", "icon": "grid", "label": "Projects", "short": "Projects"},
    {"key": "backups", "endpoint": "main.backups", "icon": "db", "label": "Backups", "short": "Backups"},
    {"key": "users", "endpoint": "users.index", "icon": "users", "label": "Users & log", "short": "Users", "owner_only": True},
    {"key": "ai", "endpoint": "ai_cost.index", "icon": "coin", "label": "AI cost", "short": "AI cost", "owner_only": True},
    {"key": "settings", "endpoint": "settings.index", "icon": "gear", "label": "Settings", "short": "Settings", "owner_only": True},
]


def _ago(dt):
    """Jinja filter: '12 min ago' / '5 h ago' / '3 days ago'."""
    from .clock import now
    if dt is None:
        return "—"
    mins = max(0, int((now() - dt).total_seconds() // 60))
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    h = round(mins / 60)
    if h < 24:
        return f"{h} h ago"
    d = round(mins / 1440)
    if d < 30:
        return f"{d} day{'s' if d != 1 else ''} ago"
    m = round(d / 30)
    return f"{m} month{'s' if m != 1 else ''} ago"


def _stamp(dt):
    from .clock import to_local
    return to_local(dt).strftime("%-d %b, %H:%M") if dt else "—"


def _size(n):
    mb = (n or 0) / (1024 * 1024)
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{int(round(mb))} MB"


def _local(dt):
    from .clock import to_local
    return to_local(dt)


def create_app():
    config = get_config()
    validate_production(config)

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None
    from . import models  # noqa: F401  (registers every table for migrations)
    from . import auth  # noqa: F401  (registers the user_loader)

    from .blueprints.ai_cost import bp as ai_cost_bp
    from .blueprints.internal_mcp import bp as internal_mcp_bp
    from .blueprints.approve import bp as approve_bp
    from .blueprints.auth import bp as auth_bp
    from .blueprints.main import bp as main_bp
    from .blueprints.projects import bp as projects_bp
    from .blueprints.settings import bp as settings_bp
    from .blueprints.users import bp as users_bp
    app.register_blueprint(ai_cost_bp)
    app.register_blueprint(internal_mcp_bp)
    csrf.exempt(internal_mcp_bp)               # local-only, one-job Bearer key, no browser / session (Claude's beta tools)
    app.register_blueprint(approve_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(users_bp)

    import seed
    seed.register(app)

    @app.cli.command("monitor")
    def monitor_command():
        """Check every watched live site once (run every 5 minutes on the server)."""
        import click
        from .monitor import run_all
        for name, res in run_all().items():
            click.echo(f"{res:8} {name}")
        from .cc_logins import tick           # Claude accounts: hourly check, renew a run-out login, alert once when logged out
        for line in tick():
            click.echo(f"claude   {line}")

    @app.cli.command("create-owner")
    @click.option("--name", required=True)
    @click.option("--email", required=True)
    @click.option("--mobile", default="")
    def create_owner_command(name, email, mobile):
        """First start on a server: make the one Owner account and print a one-time link to set the password + authenticator."""
        from .auth import new_setup_link
        from .models import User
        if User.query.filter_by(role="owner").first():
            raise click.ClickException("An owner already exists. Use Users & log to add people.")
        u = User(name=name.strip(), email=email.strip().lower(), mobile=mobile.strip(), role="owner", is_active=True)
        db.session.add(u)
        db.session.flush()
        token = new_setup_link(u)
        db.session.commit()
        base = (app.config.get("PUBLIC_URL") or "http://127.0.0.1:5070").rstrip("/")
        with app.test_request_context(base_url=base):
            click.echo(url_for("auth.setup_password", token=token, _external=True))

    @app.cli.command("beta-setup")
    @click.argument("project_id", type=int)
    def beta_setup_command(project_id):
        """Make (or rebuild) a project's automatic beta now, in this process — for the server operator."""
        from . import betas, jobs
        from .models import Job, Project, User
        p = db.session.get(Project, project_id) or (_ for _ in ()).throw(click.ClickException("No such project."))
        owner = User.query.filter_by(role="owner").first()
        j = Job(project_id=p.id, kind="beta_setup", note="Waiting to start (server command)", by_user_id=owner.id if owner else None)
        db.session.add(j)
        db.session.commit()
        jobs.start(j.id, betas.run_setup, background=False)
        j = db.session.get(Job, j.id)
        click.echo(f"{j.state}: {j.note}")
        if j.output:
            click.echo(j.output[-4000:])

    @app.cli.command("beta-lock")
    @click.argument("project_id", type=int)
    @click.option("--new-password", is_flag=True)
    def beta_lock_command(project_id, new_password):
        """Put (or renew) the password on a project's beta now — for the server operator."""
        from . import betas, jobs
        from .models import Job, Project, User
        p = db.session.get(Project, project_id) or (_ for _ in ()).throw(click.ClickException("No such project."))
        owner = User.query.filter_by(role="owner").first()
        j = Job(project_id=p.id, kind="beta_lock", note="New password" if new_password else "Waiting to start (server command)",
                by_user_id=owner.id if owner else None)
        db.session.add(j)
        db.session.commit()
        jobs.start(j.id, betas.run_lock, background=False)
        click.echo(f"{db.session.get(Job, j.id).state}: {db.session.get(Job, j.id).note}")

    @app.cli.command("rehearse-go-live")
    @click.argument("project_id", type=int)
    def rehearse_go_live_command(project_id):
        """Rehearsal projects only: Go Live v2 with the current code on the copy site, then roll back."""
        from . import jobs, releases
        from .models import Job, Project, User
        p = db.session.get(Project, project_id) or (_ for _ in ()).throw(click.ClickException("No such project."))
        owner = User.query.filter_by(role="owner").first()
        j = Job(project_id=p.id, kind="go_live", note="Waiting to start (rehearsal, server command)", by_user_id=owner.id if owner else None)
        db.session.add(j)
        db.session.commit()
        jobs.start(j.id, releases.run_rehearse_go_live, background=False)
        j = db.session.get(Job, j.id)
        click.echo(f"{j.state}: {j.note}")
        if j.output:
            click.echo(j.output[-4000:])

    @app.cli.command("beta-tool")
    @click.argument("project_id", type=int)
    @click.argument("tool")
    @click.argument("args_json", default="{}")
    def beta_tool_command(project_id, tool, args_json):
        """Run one beta tool by hand for a project (server operator; same limits as Claude's tools)."""
        import json
        from . import beta_tools
        from .models import Job, Project, User
        p = db.session.get(Project, project_id) or (_ for _ in ()).throw(click.ClickException("No such project."))
        owner = User.query.filter_by(role="owner").first()
        j = Job(project_id=p.id, kind="beta_setup", state="running", note=f"Operator tool: {tool}", by_user_id=owner.id if owner else None)
        db.session.add(j)
        db.session.commit()
        text, err = beta_tools.call(j, tool, json.loads(args_json))
        j = db.session.get(Job, j.id)
        j.state = "failed" if err else "done"
        db.session.commit()
        click.echo(("ERROR: " if err else "") + text)

    @app.cli.command("github-sync")
    def github_sync_command():
        """Bring projects in line with the GitHub repos (run every 5 minutes on the server)."""
        import click
        from .github import GitHubError, sync
        try:
            new, missing, total = sync()
            click.echo(f"GitHub sync: {total} repos, {len(new)} new, {len(missing)} missing")
        except GitHubError as e:
            raise click.ClickException(str(e))

    @app.context_processor
    def shell_context():
        """Alerts for the top bar (only for projects this person may see)."""
        if not current_user.is_authenticated:
            return {}
        from .auth import visible_projects_query
        from .models import Alert, Project
        q = Alert.query
        if not current_user.is_owner:
            ids = [p.id for p in visible_projects_query(current_user).with_entities(Project.id)]
            q = q.filter(Alert.project_id.in_(ids))
        alerts = q.order_by(Alert.created_at.desc()).limit(8).all()
        return {"top_alerts": alerts, "unseen_alerts": q.filter(Alert.seen_at.is_(None)).count()}

    def _approval_link(a):
        from .blueprints.approve import link_for
        return link_for(a)

    def _approval_open(a):
        from .blueprints.approve import is_open
        return is_open(a)

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if app.config.get("SESSION_COOKIE_SECURE"):          # production (https only): browsers never try plain http again
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    app.jinja_env.globals.update(icon=icon, NAV=NAV, approval_link=_approval_link, approval_open=_approval_open, APP_NAME=app.config["APP_NAME"], hue=hue, tech_hue=tech_hue, initials=initials)
    app.jinja_env.filters["ago"] = _ago
    app.jinja_env.filters["stamp"] = _stamp
    app.jinja_env.filters["local"] = _local
    app.jinja_env.filters["size"] = _size
    from .textfmt import simple_md
    app.jinja_env.filters["md"] = simple_md
    return app
