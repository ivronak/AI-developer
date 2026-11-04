from app.clock import now
from app.extensions import db


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=now, index=True)
