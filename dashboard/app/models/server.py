"""Servers the dashboard reaches over SSH (beta / live hosts), and background jobs (deploy, sync, …) with their logs."""
from app.extensions import db

from .mixins import TimestampMixin

JOB_KINDS = ("beta_deploy", "sync", "go_live", "rollback", "backup", "restore")
JOB_STATES = ("queued", "running", "done", "failed")


class Server(TimestampMixin, db.Model):
    __tablename__ = "servers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    host = db.Column(db.String(190), nullable=False)
    port = db.Column(db.Integer, nullable=False, default=22)
    ssh_user = db.Column(db.String(64), nullable=False)
    host_key = db.Column(db.Text, nullable=False, default="")      # pinned on the first successful test (known_hosts lines)
    last_ok_at = db.Column(db.DateTime)
    last_result = db.Column(db.String(255), nullable=False, default="")


class Job(TimestampMixin, db.Model):
    __tablename__ = "jobs"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False)
    state = db.Column(db.String(10), nullable=False, default="queued")
    note = db.Column(db.String(255), nullable=False, default="")       # current step / result, shown live
    output = db.Column(db.Text, nullable=False, default="")            # last part of the command output
    by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)
    tool_token_hash = db.Column(db.String(64), index=True)              # sha256 of the one-job key Claude's tools use
    context = db.Column(db.JSON)                                        # what Claude's tools may touch in this job (e.g. a new live release)

    project = db.relationship("Project")
    by_user = db.relationship("User")
