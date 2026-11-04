"""Deployments, backups and alerts."""
from app.extensions import db

from .mixins import TimestampMixin

BACKUP_KINDS = ("db", "files", "snapshot")


class Deployment(TimestampMixin, db.Model):
    __tablename__ = "deployments"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    environment_id = db.Column(db.Integer, db.ForeignKey("environments.id"), nullable=False, index=True)
    commit = db.Column(db.String(40), nullable=False)
    previous_commit = db.Column(db.String(40))                         # full sha live ran before this deploy — Rollback goes back to it
    change_ids = db.Column(db.JSON, nullable=False, default=list)      # changes shipped in this release
    snapshot_id = db.Column(db.Integer, db.ForeignKey("backups.id"))   # DB snapshot taken just before (live)
    by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    result = db.Column(db.String(10), nullable=False, default="ok")    # ok | failed
    note = db.Column(db.String(255), nullable=False, default="")
    rolled_back = db.Column(db.Boolean, nullable=False, default=False)
    override_reason = db.Column(db.Text, nullable=False, default="")   # Go Live without client approval

    environment = db.relationship("Environment")
    by_user = db.relationship("User")
    snapshot = db.relationship("Backup")


class Backup(TimestampMixin, db.Model):
    __tablename__ = "backups"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)                    # db | files | snapshot
    storage_url = db.Column(db.String(255), nullable=False, default="")
    size_bytes = db.Column(db.BigInteger, nullable=False, default=0)
    ok = db.Column(db.Boolean, nullable=False, default=True)
    error = db.Column(db.String(255), nullable=False, default="")


class Alert(TimestampMixin, db.Model):
    __tablename__ = "alerts"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), index=True)
    kind = db.Column(db.String(20), nullable=False)                    # down | page_change | error | backup_failed | approval
    message = db.Column(db.String(255), nullable=False)
    seen_at = db.Column(db.DateTime)


class AppBuild(TimestampMixin, db.Model):
    """A test build of a project's mobile app on Codemagic (CHANGES Step 10) — points at the beta, never at live."""
    __tablename__ = "app_builds"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    change_id = db.Column(db.Integer, db.ForeignKey("changes.id"), index=True)     # None = the default branch
    app_dir = db.Column(db.String(120), nullable=False)                           # e.g. eye_care_app
    platform = db.Column(db.String(10), nullable=False, default="android")
    branch = db.Column(db.String(160), nullable=False, default="")                # the build branch pushed for Codemagic
    cm_build_id = db.Column(db.String(64), index=True)
    status = db.Column(db.String(12), nullable=False, default="queued")           # queued | building | ready | failed | canceled
    file_url = db.Column(db.Text, nullable=False, default="")                     # public download link (expires)
    file_name = db.Column(db.String(200), nullable=False, default="")
    url_expires_at = db.Column(db.DateTime)
    error = db.Column(db.String(255), nullable=False, default="")
    by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    finished_at = db.Column(db.DateTime)

    project = db.relationship("Project")
    change = db.relationship("Change")
    by_user = db.relationship("User")
