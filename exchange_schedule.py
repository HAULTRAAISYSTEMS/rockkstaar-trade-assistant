"""Published NYSE equity schedule, not a quote-feed or trading-halt detector.

Source: https://ir.theice.com/press/news-details/2025/NYSE-Group-Announces-2026-2027-and-2028-Holiday-and-Early-Closings-Calendar/
Verified 2026-10-27. Review when the exchange publishes its next calendar.
Unknown years fail closed for execution; the UI explicitly says unavailable.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

HOLIDAYS = {
    2026: "01-01 01-19 02-16 04-03 05-25 06-19 07-03 09-07 11-26 12-25".split(),
    2027: "01-01 01-18 02-15 03-26 05-31 06-18 07-05 09-06 11-25 12-24".split(),
    2028: "01-17 02-21 04-14 05-29 06-19 07-04 09-04 11-23 12-25".split(),
}
EARLY_CLOSES = {2026: {"11-27", "12-24"}, 2027: {"11-26"}, 2028: {"07-03", "11-24"}}
LABELS = {"regular": "REGULAR SESSION", "pre_market": "PREMARKET",
          "after_hours": "AFTER HOURS", "closed": "MARKET CLOSED", "unknown": "SCHEDULE UNAVAILABLE"}


def is_trading_day(day):
    if day.year not in HOLIDAYS:
        return None
    return day.weekday() < 5 and day.strftime("%m-%d") not in HOLIDAYS[day.year]


def session_status(now=None):
    now = now or datetime.now(ZoneInfo("America/New_York"))
    if now.tzinfo is None:
        raise ValueError("Market schedule requires a timezone-aware time")
    now = now.astimezone(ZoneInfo("America/New_York"))
    known = now.year in HOLIDAYS
    key = now.strftime("%m-%d")
    early = key in EARLY_CLOSES.get(now.year, set())
    minute = now.hour * 60 + now.minute
    close, late_close = (13 * 60, 17 * 60) if early else (16 * 60, 20 * 60)
    status = "unknown"
    if known:
        if not is_trading_day(now.date()) or minute < 240 or minute >= late_close:
            status = "closed"
        elif minute < 570:
            status = "pre_market"
        elif minute < close:
            status = "regular"
        else:
            status = "after_hours"
    return {"session": status, "label": LABELS[status], "known": known,
            "early_close": early, "checked_at": now.isoformat(),
            "basis": "NYSE scheduled equity hours; not quote freshness or trading-halt status",
            "calendar_through": 2028}
