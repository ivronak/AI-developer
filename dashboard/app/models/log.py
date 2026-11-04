"""Activity log (F03, append-only — never update or delete rows) and Claude API usage (F18)."""
from app.extensions import db

from .mixins import TimestampMixin


class ActivityLog(TimestampMixin, db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)        # None = system / client
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), index=True)
    action = db.Column(db.String(60), nullable=False, index=True)
    details = db.Column(db.Text, nullable=False, default="")
    prompt_text = db.Column(db.Text, nullable=False, default="")
    ip = db.Column(db.String(64), nullable=False, default="")

    user = db.relationship("User")
    project = db.relationship("Project")


class AiUsage(TimestampMixin, db.Model):
    __tablename__ = "ai_usage"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    change_id = db.Column(db.Integer, db.ForeignKey("changes.id"), index=True)
    model = db.Column(db.String(60), nullable=False, default="")
    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
    cost_usd = db.Column(db.Numeric(10, 4), nullable=False, default=0)


class AiCredit(TimestampMixin, db.Model):
    """Anthropic prepaid credit, typed by the Owner (Anthropic's API doesn't report the balance).
    kind "balance" = the balance shown in the Anthropic Console right now (resets the estimate);
    kind "topup" = credit bought since. Estimated balance = last balance + top-ups after it − AI usage after it."""
    __tablename__ = "ai_credits"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)                     # balance | topup
    amount_usd = db.Column(db.Numeric(10, 2), nullable=False)
    note = db.Column(db.String(200), nullable=False, default="")
    by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    by_user = db.relationship("User")
