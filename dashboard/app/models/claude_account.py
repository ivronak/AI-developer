"""Claude accounts (owner, 2026-11-04): every Claude subscription the dashboard works with, each signed in once through the
web ("claude auth login" on the server, the owner opens the link in his own browser and pastes the code back).

Each account has its own Claude Code config folder for the server user (slug "a<id>" → <runner home>/claude-accounts/a<id>);
slug "" is the server user's own default login (~/.claude) — the one that existed before this table. All accounts share
one sessions folder, so a project's Claude session goes on whichever account a message picks. The login files stay in
the server user's home; the database only keeps what the page shows (plan, a masked e-mail, models, connectors)."""
from app.extensions import db

from .mixins import TimestampMixin

ACCOUNT_STATES = ("new", "ok", "logged_out", "error")


class ClaudeAccount(TimestampMixin, db.Model):
    __tablename__ = "claude_accounts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False)
    slug = db.Column(db.String(16), nullable=False, default="")       # "" = the server user's default login, else "a<id>"
    is_default = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    status = db.Column(db.String(12), nullable=False, default="new", server_default="new")
    plan = db.Column(db.String(30), nullable=False, default="", server_default="")
    email_hint = db.Column(db.String(60), nullable=False, default="", server_default="")    # j***@example.com, never the whole address
    models = db.Column(db.JSON, nullable=False, default=list, server_default="[]")         # cc_models._clean() entries, from this login
    connectors = db.Column(db.JSON)                                                         # [{"name", "status"}] or None = not known
    expires_at = db.Column(db.DateTime)              # when the current access token runs out (it renews itself; shown for the keep-alive)
    checked_at = db.Column(db.DateTime)
    last_error = db.Column(db.String(255), nullable=False, default="", server_default="")
    alerted = db.Column(db.Boolean, nullable=False, default=False, server_default="0")      # "logged out" alert sent once

    @property
    def ready(self):
        return self.status == "ok"
