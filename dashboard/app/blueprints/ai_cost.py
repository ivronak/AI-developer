"""AI cost page (Step 13, Owner only): balance left (estimate), spend today / this week / this month, by project with the
monthly limits, the most expensive changes, daily spend, by model; record the Console balance / top-ups; alert amount."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app import ai_budget
from app.ai_models import MODELS
from app.auth import log, owner_required
from app.clock import local_day_start_utc, now, to_local
from app.extensions import db
from app.models import AiCredit, AiUsage, Change, Project
from app.models.setting import set_setting

bp = Blueprint("ai_cost", __name__)
DAYS = 14


def _money(raw, lo, hi):
    try:
        v = Decimal((raw or "").strip().replace("$", "").replace(",", ""))
    except InvalidOperation:
        return None
    return v.quantize(Decimal("0.01")) if lo <= v <= hi else None


@bp.get("/ai-cost")
@login_required
@owner_required
def index():
    starts = ai_budget.period_starts()
    month = starts["month"]
    left, base = ai_budget.balance()
    low = ai_budget.low_amount()

    by_project = dict(db.session.query(AiUsage.project_id, func.sum(AiUsage.cost_usd))
                      .filter(AiUsage.created_at >= month).group_by(AiUsage.project_id).all())
    projects = []
    for p in Project.query.order_by(Project.name).all():
        spent, lim, pct = ai_budget.limit_state(p)
        if spent or lim is not None or p.is_active:
            projects.append({"p": p, "spent": spent, "lim": lim, "pct": pct})
    projects.sort(key=lambda r: (-r["spent"], r["p"].name))

    top = (db.session.query(AiUsage.change_id, func.sum(AiUsage.cost_usd).label("cost"), func.count(AiUsage.id))
           .filter(AiUsage.created_at >= month, AiUsage.change_id.isnot(None))
           .group_by(AiUsage.change_id).order_by(func.sum(AiUsage.cost_usd).desc()).limit(8).all())
    changes = {c.id: c for c in Change.query.filter(Change.id.in_([t[0] for t in top])).all()} if top else {}
    top_changes = [{"c": changes[t[0]], "cost": Decimal(str(t[1])), "calls": t[2]} for t in top if t[0] in changes]

    models = [{"model": m, "label": MODELS.get(m, (m,))[0].split(" — ")[0], "cost": Decimal(str(c)), "calls": n}
              for m, c, n in db.session.query(AiUsage.model, func.sum(AiUsage.cost_usd), func.count(AiUsage.id))
              .filter(AiUsage.created_at >= month).group_by(AiUsage.model).order_by(func.sum(AiUsage.cost_usd).desc()).all()]

    today = to_local(now()).date()
    first = today - timedelta(days=DAYS - 1)
    daily = {first + timedelta(days=i): Decimal("0") for i in range(DAYS)}
    for cost, at in db.session.query(AiUsage.cost_usd, AiUsage.created_at).filter(
            AiUsage.created_at >= local_day_start_utc(first)).all():
        d = to_local(at).date()
        if d in daily:
            daily[d] += Decimal(str(cost))
    peak = max(daily.values()) or Decimal("1")

    return render_template("ai_cost/index.html", page="ai", title="AI cost", crumb="AI cost",
                           left=left, base=base, low=low,
                           today_spend=ai_budget.spend(since=starts["today"]), week_spend=ai_budget.spend(since=starts["week"]),
                           month_spend=ai_budget.spend(since=month), projects=projects, by_project=by_project,
                           top_changes=top_changes, models=models, daily=daily, peak=peak,
                           credits=AiCredit.query.order_by(AiCredit.created_at.desc(), AiCredit.id.desc()).limit(10).all())


@bp.post("/ai-cost/credit")
@login_required
@owner_required
def add_credit():
    kind = request.form.get("kind") if request.form.get("kind") in ("balance", "topup") else None
    amount = _money(request.form.get("amount"), Decimal("0") if kind == "balance" else Decimal("0.01"), Decimal("100000"))
    if kind is None or amount is None:
        flash("Enter the amount in US dollars, e.g. 25 or 12.40.", "bad")
        return redirect(url_for("ai_cost.index"))
    note = (request.form.get("note") or "").strip()[:200]
    db.session.add(AiCredit(kind=kind, amount_usd=amount, note=note, by_user_id=current_user.id))
    log("AI credit", details=(f"Anthropic balance recorded: ${amount}" if kind == "balance" else f"Anthropic top-up: ${amount}")
        + (f" — {note}" if note else ""))
    db.session.commit()
    ai_budget.check()
    flash("Balance saved." if kind == "balance" else "Top-up saved.", "ok")
    return redirect(url_for("ai_cost.index"))


@bp.post("/ai-cost/alert")
@login_required
@owner_required
def save_alert():
    amount = _money(request.form.get("low"), Decimal("0"), Decimal("10000"))
    if amount is None:
        flash("Enter the alert amount in US dollars (0 = no alert).", "bad")
        return redirect(url_for("ai_cost.index"))
    set_setting("ai_low_balance_usd", float(amount))
    log("Settings", details=f"Low-balance alert below ${amount}")
    db.session.commit()
    ai_budget.check()
    flash("Alert amount saved.", "ok")
    return redirect(url_for("ai_cost.index"))


@bp.post("/ai-cost/limit/<int:project_id>")
@login_required
@owner_required
def save_limit(project_id):
    p = db.session.get(Project, project_id) or abort(404)
    raw = (request.form.get("limit") or "").strip()
    if raw:
        lim = _money(raw, Decimal("0.5"), Decimal("10000"))
        if lim is None:
            flash("A monthly limit is between $0.50 and $10,000 — or leave it empty for no limit.", "bad")
            return redirect(url_for("ai_cost.index") + "#limits")
    else:
        lim = None
    p.ai_monthly_limit_usd = lim
    log("Settings", project_id=p.id, details=f"AI monthly limit: {'$' + str(lim) if lim is not None else 'none'}")
    db.session.commit()
    ai_budget.check()
    flash(f"{p.name}: limit saved.", "ok")
    return redirect(url_for("ai_cost.index") + "#limits")
