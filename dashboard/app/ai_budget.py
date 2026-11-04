"""AI cost & balance (Step 13, F18). Spend comes from ai_usage (every Claude call is recorded with its cost). Anthropic's API
doesn't report the prepaid balance, so the Owner types it in (the Console's balance, and each top-up); the dashboard
estimates what is left. Alerts (bell + Telegram, each once): balance below the Owner's amount, a project at 80% / 100%
of its monthly limit, and Anthropic refusing for low credit. At 100% only the Owner can start new Claude work there."""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func

from app.alerts import raise_alert
from app.clock import local_day_start_utc, now, to_local
from app.extensions import db
from app.models import AiCredit, AiUsage, Project
from app.models.setting import get_setting, set_setting

ZERO = Decimal("0")


def period_starts():
    """Business-time starts (as naive UTC) of today, this week (Monday) and this month."""
    today = to_local(now()).date()
    return {"today": local_day_start_utc(today),
            "week": local_day_start_utc(today - timedelta(days=today.weekday())),
            "month": local_day_start_utc(today.replace(day=1))}


def spend(since=None, project_id=None):
    q = db.session.query(func.coalesce(func.sum(AiUsage.cost_usd), 0))
    if since is not None:
        q = q.filter(AiUsage.created_at >= since)
    if project_id is not None:
        q = q.filter(AiUsage.project_id == project_id)
    return Decimal(str(q.scalar() or 0))


def balance():
    """(estimated balance left or None, the last 'balance' entry or None)."""
    base = AiCredit.query.filter_by(kind="balance").order_by(AiCredit.created_at.desc(), AiCredit.id.desc()).first()
    if base is None:
        return None, None
    topups = db.session.query(func.coalesce(func.sum(AiCredit.amount_usd), 0)).filter(
        AiCredit.kind == "topup", AiCredit.created_at >= base.created_at, AiCredit.id != base.id).scalar()
    left = Decimal(str(base.amount_usd)) + Decimal(str(topups or 0)) - spend(since=base.created_at)
    return left.quantize(Decimal("0.01")), base


def low_amount():
    return Decimal(str(get_setting("ai_low_balance_usd") or 0))


def month_key():
    return to_local(now()).strftime("%Y-%m")


def limit_state(project):
    """(spent this month, limit or None, percent or None)."""
    spent = spend(since=period_starts()["month"], project_id=project.id)
    lim = Decimal(str(project.ai_monthly_limit_usd)) if project.ai_monthly_limit_usd is not None else None
    pct = int(spent * 100 / lim) if lim else None
    return spent, lim, pct


def blocked_reason(project, user):
    """Why this person can't start Claude on this project now, or ''. The Owner is never blocked by a limit."""
    if user.is_owner:
        return ""
    spent, lim, pct = limit_state(project)
    if lim is not None and spent >= lim:
        return (f"This project used its AI limit for this month (${spent:.2f} of ${lim:.2f}). "
                "Ask the owner to raise the limit or to start this change.")
    return ""


def _state():
    st = dict(get_setting("ai_alert_state") or {})
    st.setdefault("limits", {})
    return st


def check():
    """Send the alerts that are due (each once). Called after every Claude run and by the monitor timer."""
    st, sent = _state(), []
    left, base = balance()
    low = low_amount()
    if left is not None and low > 0:
        if left < low and not st.get("low"):
            raise_alert("ai_cost", f"Anthropic balance is low: about ${left:.2f} left (alert below ${low:.2f}). "
                                   "Add credit at console.anthropic.com → Billing, then record it on the AI cost page.", link=False)
            st["low"] = True
            sent.append("low")
        elif left >= low and st.get("low"):
            st["low"] = False                                   # topped up → alert again next time it gets low
    mk = month_key()
    for p in Project.query.filter(Project.ai_monthly_limit_usd.isnot(None)).all():
        spent, lim, pct = limit_state(p)
        if not lim:
            continue
        level = 100 if pct >= 100 else (80 if pct >= 80 else 0)
        done = st["limits"].get(str(p.id), "")
        done_level = int(done.split(":")[1]) if done.startswith(mk + ":") else 0
        if level > done_level:
            raise_alert("ai_cost", f"{p.name}: AI spend this month is ${spent:.2f} of the ${lim:.2f} limit ({pct}%)."
                                   + (" New Claude work there now needs the owner." if level == 100 else ""), p)
            st["limits"][str(p.id)] = f"{mk}:{level}"
            sent.append(f"limit:{p.id}:{level}")
    set_setting("ai_alert_state", st)
    db.session.commit()
    return sent


def credit_refused():
    """Anthropic refused a call for low credit — alert once per day."""
    st = _state()
    day = to_local(now()).strftime("%Y-%m-%d")
    if st.get("credit") == day:
        return False
    raise_alert("ai_cost", "Anthropic refused a Claude request: the credit balance is too low. "
                           "Add credit at console.anthropic.com → Billing.", link=False)
    st["credit"] = day
    set_setting("ai_alert_state", st)
    db.session.commit()
    return True


def is_credit_error(e):
    return "credit balance" in str(getattr(e, "message", "") or e).lower()
