"""Client projects and their two environments (beta + live)."""
from app.extensions import db

from .mixins import TimestampMixin

HOST_TYPES = ("vps", "cpanel")
ENV_KINDS = ("beta", "live")
MESSAGE_MODES = ("off", "test", "on")       # F13: messages leaving beta
PAYMENT_MODES = ("sandbox", "live")         # F13: payment keys on beta


class Project(TimestampMixin, db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(120), nullable=False, unique=True)
    client_name = db.Column(db.String(120), nullable=False)
    tech_stack = db.Column(db.JSON, nullable=False, default=list)      # ["Laravel", "Flutter"]
    github_repo = db.Column(db.String(200))                            # "org/repo" (one repo per project: web/ api/ app/)
    github_repo_id = db.Column(db.BigInteger, index=True)               # GitHub's own id — follows renames / transfers
    host_type = db.Column(db.String(10), nullable=False, default="vps")
    is_active = db.Column(db.Boolean, nullable=False, default=True)    # False = dormant
    # a live problem the monitor found (F15): "Live site down for 12 min" — empty when fine
    incident = db.Column(db.String(255), nullable=False, default="")
    incident_at = db.Column(db.DateTime)
    # GitHub sync (F04): a repo that appeared on GitHub becomes a project that still needs its details filled in
    needs_setup = db.Column(db.Boolean, nullable=False, default=False)
    repo_missing = db.Column(db.Boolean, nullable=False, default=False)   # repo gone from GitHub (renamed / deleted)
    repo_private = db.Column(db.Boolean)
    default_branch = db.Column(db.String(120))
    repo_pushed_at = db.Column(db.DateTime)                               # last push to any branch
    monitor_enabled = db.Column(db.Boolean, nullable=False, default=True) # F15: check the live site every 5 minutes
    test_command = db.Column(db.String(255), nullable=False, default="")  # e.g. "php artisan test" — run by the agent, no shell
    db_engine = db.Column(db.String(10), nullable=False, default="mysql")  # mysql | postgres — for Live → Beta sync
    deploy_script = db.Column(db.Text, nullable=False, default="")        # runs on the server in the deploy folder (owner-edited)
    beta_env_off = db.Column(db.Text, nullable=False, default="")         # .env lines on beta when messages are OFF
    beta_env_test = db.Column(db.Text, nullable=False, default="")        # .env lines on beta in test-numbers mode
    beta_env_sandbox = db.Column(db.Text, nullable=False, default="")     # .env lines for sandbox / test payment keys
    ai_monthly_limit_usd = db.Column(db.Numeric(10, 2))                   # None = no limit; 100% → only the owner can start Claude
    ai_engine = db.Column(db.String(12), nullable=False, default="api", server_default="api")   # api | claude_code
    cc_session_id = db.Column(db.String(64))                              # the project's one Claude Code session (engine claude_code)
    beta_recipe = db.Column(db.Text, nullable=False, default="", server_default="")   # steps Claude found to run this app (managed beta)
    mobile_apps = db.Column(db.JSON, nullable=False, default=list, server_default="[]")   # Flutter app folders in the repo: [{"dir", "name"}]
    live_recipe = db.Column(db.Text, nullable=False, default="", server_default="")   # steps to make a new LIVE release work (Go Live v2)
    memory_mode = db.Column(db.String(8), nullable=False, default="auto", server_default="auto")   # auto: Claude manages CLAUDE.md / MEMORY.md | files: the owner's picks
    memory_files = db.Column(db.JSON, nullable=False, default=list, server_default="[]")   # repo paths of the md files Claude keeps as memory (mode files)

    environments = db.relationship("Environment", back_populates="project", cascade="all, delete-orphan")
    members = db.relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    changes = db.relationship("Change", back_populates="project", cascade="all, delete-orphan")

    def env(self, kind):
        return next((e for e in self.environments if e.kind == kind), None)

    def __repr__(self):
        return f"<Project {self.slug}>"


class Environment(TimestampMixin, db.Model):
    __tablename__ = "environments"
    __table_args__ = (db.UniqueConstraint("project_id", "kind"),)

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)                    # beta | live
    url = db.Column(db.String(255), nullable=False)
    branch = db.Column(db.String(120), nullable=False)                 # beta → "beta", live → "main"
    server_host = db.Column(db.String(190))
    server_id = db.Column(db.Integer, db.ForeignKey("servers.id"))
    ssh_user = db.Column(db.String(64))
    deploy_path = db.Column(db.String(255))
    db_name = db.Column(db.String(120))
    current_commit = db.Column(db.String(40))
    message_mode = db.Column(db.String(10), nullable=False, default="off")
    test_numbers = db.Column(db.Text, nullable=False, default="")
    payment_mode = db.Column(db.String(10), nullable=False, default="sandbox")
    last_sync_at = db.Column(db.DateTime)                              # last Live → Beta copy (beta only)
    # monitor (F15) — filled on the live environment
    last_check_at = db.Column(db.DateTime)
    last_http_status = db.Column(db.Integer)                           # 0 = no answer (timeout / DNS / TLS)
    last_response_ms = db.Column(db.Integer)
    fail_count = db.Column(db.Integer, nullable=False, default=0)      # failed checks in a row
    down_since = db.Column(db.DateTime)
    page_hash = db.Column(db.String(64))                               # fingerprint of the home page's visible text
    page_change_count = db.Column(db.Integer, nullable=False, default=0)
    watch_until = db.Column(db.DateTime)                               # close watch after Go Live / Rollback: alert on the first failed check
    managed = db.Column(db.Boolean, nullable=False, default=False, server_default="0")   # beta made and run by the dashboard itself
    git_origin = db.Column(db.String(255))                             # the repo the server folder pulls from ("" = not a git folder)
    layout = db.Column(db.String(10), nullable=False, default="", server_default="")   # "" = one folder · "releases" (Go Live v2)
    shared = db.Column(db.JSON, nullable=False, default=list)          # kept across releases: [".env", "storage", "backend/crm.db"]
    restart_units = db.Column(db.JSON, nullable=False, default=list)   # systemd services to restart after a switch
    current_release = db.Column(db.String(40))                         # folder name under <site>/releases
    git_checked_at = db.Column(db.DateTime)
    drift = db.Column(db.JSON)                                         # live folder vs GitHub: {head, ahead, behind, files, fetch_ok}
    drift_at = db.Column(db.DateTime)
    site_user = db.Column(db.String(64))                               # Linux user of a managed beta's website
    db_user = db.Column(db.String(64))                                 # managed beta: its own MySQL user (only this beta's database)
    db_password_enc = db.Column(db.Text, nullable=False, default="", server_default="")
    beta_password_enc = db.Column(db.Text, nullable=False, default="", server_default="")   # the beta's lock (user "beta")

    project = db.relationship("Project", back_populates="environments")
    server = db.relationship("Server")
