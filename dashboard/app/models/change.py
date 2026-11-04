"""A change request: chat with Claude, files, tests, client approval."""
from app.extensions import db

from .mixins import TimestampMixin

# beta (Claude working / tests failing) → passed → client (link sent) → approved → live
# changes = client asked for changes (back to Claude). A rollback puts the release's changes back to "beta".
CHANGE_STATUSES = ("beta", "passed", "client", "changes", "approved", "live")
MESSAGE_SENDERS = ("owner", "developer", "claude", "client")
ATTACHMENT_KINDS = ("image", "audio", "video")


class Change(TimestampMixin, db.Model):
    __tablename__ = "changes"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    status = db.Column(db.String(12), nullable=False, default="beta", index=True)
    branch = db.Column(db.String(160))                        # change/<id>-<slug>
    ai_summary = db.Column(db.Text, nullable=False, default="")
    files = db.Column(db.JSON, nullable=False, default=list)  # files changed on beta
    tests_passed = db.Column(db.Integer)
    tests_total = db.Column(db.Integer)
    live_at = db.Column(db.DateTime)
    # Claude agent run (F06): idle | queued | working | done | failed
    agent_state = db.Column(db.String(10), nullable=False, default="idle")
    agent_note = db.Column(db.String(255), nullable=False, default="")    # last progress line / error, shown in the chat
    agent_started_at = db.Column(db.DateTime)
    agent_cost_usd = db.Column(db.Numeric(10, 4), nullable=False, default=0)
    agent_preset = db.Column(db.String(10), nullable=False, default="normal")   # quick | normal | best — picked per request
    agent_model = db.Column(db.String(64))     # Claude Code engine: a model from Claude Code's own list (cc_models); None = use the preset
    agent_debug = db.Column(db.Boolean, nullable=False, default=False, server_default="0")   # Debug mode: more steps + the bug-hunting protocol
    agent_account_id = db.Column(db.Integer, index=True)   # Claude Code engine: the Claude account picked in the chat (claude_accounts.id); None = the default one
    agent_effort = db.Column(db.String(8))     # Claude Code engine: low | medium | high | xhigh | max; None/"" = the model's default
    agent_steps = db.Column(db.JSON, nullable=False, default=list, server_default="[]")   # live steps of the current run

    project = db.relationship("Project", back_populates="changes")
    requested_by = db.relationship("User")
    messages = db.relationship("ChangeMessage", back_populates="change", cascade="all, delete-orphan",
                               order_by="ChangeMessage.created_at")
    approvals = db.relationship("Approval", back_populates="change", cascade="all, delete-orphan",
                                order_by="Approval.sent_at")

    @property
    def approval(self):
        """The newest approval link (older ones are closed)."""
        return self.approvals[-1] if self.approvals else None

    @property
    def tests_ok(self):
        return self.tests_total is not None and self.tests_passed == self.tests_total


class ChangeMessage(TimestampMixin, db.Model):
    """One chat bubble on a change (owner / developer prompt, Claude reply, client comment)."""
    __tablename__ = "change_messages"

    id = db.Column(db.Integer, primary_key=True)
    change_id = db.Column(db.Integer, db.ForeignKey("changes.id"), nullable=False, index=True)
    sender = db.Column(db.String(12), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    body = db.Column(db.Text, nullable=False)
    steps = db.Column(db.JSON)                                # Claude's reply: the steps it took ("12 steps", folded)

    change = db.relationship("Change", back_populates="messages")
    user = db.relationship("User")
    attachments = db.relationship("ChangeAttachment", back_populates="message", cascade="all, delete-orphan")


class ChangeQuestion(TimestampMixin, db.Model):
    """Claude asks the owner something while it works (a decision, or permission for something outside its rules).
    Shown as a card in the chat (+ bell / Telegram); Claude waits for the answer."""
    __tablename__ = "change_questions"

    id = db.Column(db.Integer, primary_key=True)
    change_id = db.Column(db.Integer, db.ForeignKey("changes.id"), nullable=False, index=True)
    kind = db.Column(db.String(12), nullable=False, default="question")   # question | permission | root (a root command: needs an authenticator code)
    question = db.Column(db.Text, nullable=False)
    options = db.Column(db.JSON, nullable=False, default=list)
    status = db.Column(db.String(10), nullable=False, default="open")   # open | answered | expired
    answer = db.Column(db.Text, nullable=False, default="")
    answered_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    answered_at = db.Column(db.DateTime)
    code = db.Column(db.Text)       # an exact command / input the owner must see verbatim (shown in a <pre>, never as Markdown)
    ticket = db.Column(db.String(64))   # kind "root": the root service's one-use approval for exactly `code`, waiting for Claude's tool (never shown)

    change = db.relationship("Change")
    answered_by = db.relationship("User")


class ChangeAttachment(TimestampMixin, db.Model):
    __tablename__ = "change_attachments"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("change_messages.id"), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False, default="image")
    file_path = db.Column(db.String(255), nullable=False)     # random name in a private folder, never under /static
    original_name = db.Column(db.String(255), nullable=False)

    message = db.relationship("ChangeMessage", back_populates="attachments")


class Approval(db.Model):
    """One-time client link (F09). decision: None (waiting) | approved | changes.
    recorded_by_id is set when the Owner records the client's answer (e.g. given on a call) — always with a note."""
    __tablename__ = "approvals"

    id = db.Column(db.Integer, primary_key=True)
    change_id = db.Column(db.Integer, db.ForeignKey("changes.id"), nullable=False, index=True)
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)   # sha256 of the link's secret (never the secret)
    token_enc = db.Column(db.Text, nullable=False, default="")                   # the secret, encrypted — so the team can copy the link again
    checklist = db.Column(db.JSON, nullable=False, default=list)
    sent_at = db.Column(db.DateTime, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    decision = db.Column(db.String(10))
    comment = db.Column(db.Text, nullable=False, default="")
    decided_at = db.Column(db.DateTime)
    decided_ip = db.Column(db.String(64))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    change = db.relationship("Change", back_populates="approvals")
    recorded_by = db.relationship("User")
