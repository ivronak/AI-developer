"""One way to raise an alert (F14): a row for the bell in the dashboard + a Telegram message + the activity log."""
from flask import current_app, has_request_context, url_for

from app import telegram
from app.auth import log
from app.extensions import db
from app.models import Alert

ICON = {"down": "🔴", "back_up": "🟢", "page_change": "🟠", "error": "🔴", "backup_failed": "🟠", "approval": "✅",
        "login_locked": "🔒", "test": "🔔", "expiry": "⏳", "ai_cost": "💰", "golive": "🚀"}


def raise_alert(kind, message, project=None, link=True):
    db.session.add(Alert(project_id=project.id if project else None, kind=kind, message=message))
    log("Alert", project_id=project.id if project else None, details=message)
    db.session.commit()
    text = f"{ICON.get(kind, '🔔')} {message}"
    if link and project is not None and has_request_context():
        text += f"\n{url_for('projects.detail', project_id=project.id, _external=True)}"
    elif link and project is not None and current_app.config.get("PUBLIC_URL"):
        text += f"\n{current_app.config['PUBLIC_URL'].rstrip('/')}/projects/{project.id}"
    telegram.send(text)
