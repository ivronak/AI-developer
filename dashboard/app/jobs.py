"""Background jobs (beta deploy, Live → Beta sync; later Go Live, rollback, backups) with a live note + output.
Only one job — and no Claude run — per project at a time, because they share the project's checkout and servers."""
import threading
import traceback

from flask import current_app

from app.clock import now
from app.extensions import db
from app.models import Change, Job

_running = {}


def busy_reason(project_id):
    """Why a new job can't start now, or ''."""
    j = Job.query.filter(Job.project_id == project_id, Job.state.in_(("queued", "running"))).first()
    if j is not None:
        return f"Another job is running on this project ({j.kind.replace('_', ' ')})."
    c = Change.query.filter(Change.project_id == project_id, Change.agent_state.in_(("queued", "working"))).first()
    if c is not None:
        return f"Claude is working on “{c.title}” — wait for it to finish."
    return ""


def note(job, text, output=None):
    job.note = text[:255]
    if output is not None:
        job.output = (job.output + "\n\n" + output).strip()[-20000:]
    db.session.commit()


def start(job_id, fn, background=True):
    """Run fn(job) and keep the job's state right whatever happens."""
    app = current_app._get_current_object()

    def body():
        job = db.session.get(Job, job_id)
        job.state, job.started_at = "running", now()
        db.session.commit()
        try:
            fn(job)
            job = db.session.get(Job, job_id)
            if job.state == "running":
                job.state = "done"
        except Exception as e:          # a job must never stay "running"
            db.session.rollback()
            job = db.session.get(Job, job_id)
            job.state = "failed"
            job.note = (getattr(e, "plain", None) or str(e) or "Unexpected error")[:255]
            if not getattr(e, "plain", None) and not isinstance(e, (RuntimeError,)):
                current_app.logger.error("job %s failed:\n%s", job_id, traceback.format_exc())
        job.finished_at = now()
        db.session.commit()

    if not background:
        body()
        return

    def target():
        with app.app_context():
            try:
                body()
            finally:
                db.session.remove()
                _running.pop(job_id, None)

    t = threading.Thread(target=target, name=f"job-{job_id}", daemon=True)
    _running[job_id] = t
    t.start()


def is_running(job_id):
    t = _running.get(job_id)
    return bool(t and t.is_alive())
