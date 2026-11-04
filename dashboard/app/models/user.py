"""People who sign in (Owner, Developers). Clients never get a login — they use an approval link."""
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db

from .mixins import TimestampMixin

ROLES = ("owner", "developer")


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(190), nullable=False, unique=True, index=True)
    mobile = db.Column(db.String(32), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="developer")
    password_hash = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # sign-in safety (Step 3): 5 wrong tries → locked for 15 minutes
    failed_logins = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    # bumped on password change / access off / authenticator reset → every other session signs out
    session_version = db.Column(db.Integer, nullable=False, default=1)
    # authenticator app (TOTP): secret is Fernet-encrypted; recovery codes are stored as sha256 hashes
    totp_secret_enc = db.Column(db.Text, nullable=False, default="")
    totp_enabled = db.Column(db.Boolean, nullable=False, default=False)
    totp_last_step = db.Column(db.Integer, nullable=False, default=0)      # blocks re-use of the same 30-second code
    recovery_codes = db.Column(db.JSON, nullable=False, default=list)
    # one-time "set your password" link (new user / owner reset), stored as a sha256 hash
    setup_token_hash = db.Column(db.String(64))
    setup_token_expires = db.Column(db.DateTime)

    memberships = db.relationship("ProjectMember", back_populates="user", cascade="all, delete-orphan")

    @property
    def is_owner(self):
        return self.role == "owner"

    def get_id(self):
        # Flask-Login session id carries the version, so bumping session_version signs every old session out
        return f"{self.id}.{self.session_version}"

    @property
    def initials(self):
        return "".join(w[0] for w in self.name.split()[:2]).upper()

    def set_password(self, raw):
        # pbkdf2: this Mac's Python 3.9 has no hashlib.scrypt (Werkzeug's default)
        self.password_hash = generate_password_hash(raw, method="pbkdf2:sha256")

    def check_password(self, raw):
        return bool(self.password_hash) and check_password_hash(self.password_hash, raw)

    def __repr__(self):
        return f"<User {self.email} {self.role}>"


class ProjectMember(TimestampMixin, db.Model):
    """A developer assigned to a project (F02). expires_at = temporary access for an on-call expert (F21, v2)."""
    __tablename__ = "project_members"
    __table_args__ = (db.UniqueConstraint("user_id", "project_id"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    expires_at = db.Column(db.DateTime)

    user = db.relationship("User", back_populates="memberships")
    project = db.relationship("Project", back_populates="members")
