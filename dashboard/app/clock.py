"""One clock for the whole app. Times are stored as naive UTC; pages convert to the business time zone when showing them.
Never call datetime.now() / utcnow() / date.today() directly in app code — use now()."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("Asia/Kolkata")     # how times are shown to people


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ago(minutes):
    """now() minus N minutes — handy for seed / demo data."""
    return now() - timedelta(minutes=minutes)


def to_local(dt):
    """Stored naive UTC → business-time datetime for display."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(BUSINESS_TZ)


def local_day_start_utc(day):
    """A business-time calendar date (date object) → its midnight as naive UTC (for date filters)."""
    start = datetime(day.year, day.month, day.day, tzinfo=BUSINESS_TZ)
    return start.astimezone(timezone.utc).replace(tzinfo=None)
