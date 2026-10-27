"""quote_provenance.py — what a price actually is.

A number with a dollar sign in front of it is the most believed thing on the
page. It is also, in this app, the least described. `fetch_live_data` has four
sources that it tries in order — Polygon, Finnhub, the Yahoo chart API, and
yfinance — and the dict it returns says nothing about which one answered. The
template renders the result identically either way:

    <div class="stat-label">Current Price</div>
    <div class="stat-value">$412.18</div>

That is the same markup for a Polygon last-trade from four seconds ago and a
Yahoo close from last Friday afternoon. The second one is not wrong, exactly —
it was the price, at a time the page declines to mention.

Three claims are hiding in that one cell, and this module separates them.

WHEN. Both live sources hand back a timestamp and the app throws it away.
Finnhub's /quote returns `t`, the epoch second of the quote; Polygon's snapshot
returns `lastTrade.t` in nanoseconds and `updated` alongside it. Neither is
read anywhere. So the app has never been able to say how old a price is, which
is the one thing you need to know before acting on it — and the same shape of
loss as the earnings `hour` field that used to be flattened to "TBD".

WHETHER THE MARKET IS OPEN. A price at 11:04 on a Tuesday and a price at 11:04
on a Sunday are different kinds of fact. The second one is a Friday close being
shown with no indication that nothing has traded since.

WHAT THE DAY CHANGE IS MEASURED AGAINST. `gap_pct` is computed as
(current - prev_close) / prev_close, and `prev_close_date` is populated on
exactly one of the four paths. Without that date the app cannot tell a normal
overnight change from a change across a long weekend or a holiday: after
Thanksgiving or a Monday holiday, `previousClose` is three or four days back
and a "day change" of 6% is really a 6% move over four days. Reporting that as
today's move is a bigger error than any rounding.

None of this changes a price. It changes what the page is allowed to claim
about one.
"""

from __future__ import annotations

import datetime as _dt


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
# `realtime` is about the feed, not the plan: Polygon's free tier is delayed
# even though the endpoint is a real-time one, so the delay note carries the
# honest caveat and the flag only says whether the source quotes trades at all.
SOURCES = {
    "polygon": {
        "name": "Polygon",
        "quotes_trades": True,
        "note": "last trade; delayed 15 minutes on the free tier",
        "prev_day_range": True,
    },
    "finnhub": {
        "name": "Finnhub",
        "quotes_trades": True,
        "note": "last trade",
        # Finnhub's /quote has no previous-session high or low. It has today's
        # (h, l) — which is a different thing, and used to be stored as though
        # it were the previous day's.
        "prev_day_range": False,
    },
    "yahoo_chart": {
        "name": "Yahoo chart",
        "quotes_trades": False,
        "note": "chart meta; typically 15 minutes behind",
        "prev_day_range": True,
    },
    "yfinance": {
        "name": "Yahoo Finance",
        "quotes_trades": False,
        "note": "typically 15 minutes behind",
        "prev_day_range": True,
    },
}

UNKNOWN_SOURCE = {
    "name": "Unknown source",
    "quotes_trades": False,
    "note": "the fetch path did not say where this price came from",
    "prev_day_range": False,
}

# How old a quote may be during the regular session before the page stops
# calling it live. Fifteen minutes is the delay the free feeds already carry,
# so anything past half an hour is a stalled fetch rather than a feed delay.
STALE_AFTER_SECONDS = 30 * 60

_SESSIONS = {
    "pre_market":  ("Pre-market", True),
    "regular":     ("Market open", True),
    "after_hours": ("After hours", True),
    "closed":      ("Market closed", False),
    "weekend":     ("Weekend — market closed", False),
}


def _et_tz():
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        # Accepts a one-hour error in winter rather than failing. Every caller
        # here is labelling a time for a human, not placing an order on one.
        return _dt.timezone(_dt.timedelta(hours=-4))


def _now_et(now=None) -> _dt.datetime:
    if isinstance(now, _dt.datetime):
        return now.astimezone(_et_tz()) if now.tzinfo else now.replace(tzinfo=_et_tz())
    return _dt.datetime.now(_et_tz())


def session(now=None) -> dict:
    """Which US market session a moment falls in.

    Weekends are their own answer rather than being folded into "closed":
    "market closed" on a Tuesday evening means the day is over, and on a
    Sunday it means the price on screen is two days old.
    """
    et = _now_et(now)
    if et.weekday() >= 5:
        code = "weekend"
    else:
        mins = et.hour * 60 + et.minute
        if mins < 4 * 60:
            code = "closed"
        elif mins < 9 * 60 + 30:
            code = "pre_market"
        elif mins <= 16 * 60:
            code = "regular"
        elif mins <= 20 * 60:
            code = "after_hours"
        else:
            code = "closed"
    label, trading = _SESSIONS[code]
    return {"code": code, "label": label, "trading": trading}


def _as_datetime(value) -> _dt.datetime | None:
    """Read a quote timestamp from whatever the feed gave.

    Finnhub sends epoch seconds, Polygon sends nanoseconds in `lastTrade.t`
    and milliseconds in `updated`. Rather than ask each caller to normalise,
    this picks the unit by magnitude: anything past the year 5138 in seconds
    is obviously a larger unit.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    for divisor in (1, 1e3, 1e6, 1e9):
        secs = n / divisor
        if secs < 1e11:                       # < year 5138
            break
    try:
        return _dt.datetime.fromtimestamp(secs, _dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _clock(et: _dt.datetime, now_et: _dt.datetime) -> str:
    """'3:58 PM ET' today, 'Fri 4:00 PM ET' earlier this week, else a date.

    Built by hand rather than with %-I, which is a glibc extension and is not
    available everywhere this runs.
    """
    hour = et.hour % 12 or 12
    stamp = f"{hour}:{et.minute:02d} {'AM' if et.hour < 12 else 'PM'} ET"
    days_back = (now_et.date() - et.date()).days
    if days_back == 0:
        return stamp
    if 0 < days_back <= 6:
        return f"{_DAYS[et.weekday()]} {stamp}"
    return f"{_MONTHS[et.month - 1]} {et.day} {stamp}"


def day_change(data: dict) -> dict:
    """The day's move, and what it is measured against.

    Returns `known` False when there is no previous close to compare with, and
    `baseline_verified` False when there is one but nothing says which session
    it closed. The second case is the dangerous one: the arithmetic succeeds
    and produces a number that may span a weekend or a holiday.
    """
    data = data if isinstance(data, dict) else {}
    price = data.get("current_price")
    prev = data.get("prev_close")
    base_date = data.get("prev_close_date") or None

    out = {
        "known": False, "change": None, "pct": None,
        "baseline": prev, "baseline_date": base_date,
        "baseline_verified": bool(base_date),
        "why_not": "",
    }
    if price is None:
        out["why_not"] = "no current price arrived"
        return out
    if not prev:
        out["why_not"] = "no previous close arrived, so there is nothing to measure against"
        return out

    out["known"] = True
    out["change"] = round(price - prev, 2)
    out["pct"] = round((price - prev) / prev * 100, 2)
    if not base_date:
        out["why_not"] = (
            "the previous close arrived without a date, so this is a change "
            "since the last session the feed had — which may not be yesterday"
        )
    return out


def describe(data: dict, now=None) -> dict:
    """Everything the page needs to say about a price, and nothing invented.

    Keys:
      source, source_name, source_note   where it came from
      as_of, age_seconds, timed          when, and whether that is known
      live                               quoted during a session, recently
      stale                              session is open and the quote is old
      session, session_label             the market's state now
      change                             day_change(), above
      caveat                             one line, or '' when all is well
    """
    data = data if isinstance(data, dict) else {}
    key = data.get("price_source")
    meta = SOURCES.get(key, UNKNOWN_SOURCE)
    sess = session(now)
    now_et = _now_et(now)

    quoted = _as_datetime(data.get("quote_time"))
    age = None
    as_of = ""
    if quoted:
        as_of = _clock(quoted.astimezone(_et_tz()), now_et)
        age = int((now_et - quoted.astimezone(_et_tz())).total_seconds())

    snapshot_only = data.get("data_source") == "stale_snapshot"
    # `stale` is reserved for a fetch that should have refreshed and did not,
    # which is only diagnosable during the regular session: in pre-market and
    # after hours a quote can be hours old simply because nothing has traded,
    # and flagging that would cry wolf twice a day.
    stale = bool(
        sess["code"] == "regular" and age is not None and age > STALE_AFTER_SECONDS
    ) or snapshot_only
    # `live` is the narrow claim: a trade feed, a session that is open, and a
    # quote recent enough to stand for the current price. A quote from 3 AM
    # shown in after-hours satisfies none of the last part, so it is captioned
    # with its time instead of a green dot.
    live = bool(
        meta["quotes_trades"] and sess["trading"] and not snapshot_only
        and age is not None and age <= STALE_AFTER_SECONDS
    )

    change = day_change(data)
    notes = []
    snapshot = data.get("data_source") == "stale_snapshot"
    if data.get("current_price") is None:
        notes.append("No price arrived for this ticker")
    elif snapshot:
        notes.append(
            "The live fetch failed, so this is the last price the app stored"
            + (f", from {as_of}" if as_of else " — and it does not say when")
        )
    elif not quoted:
        notes.append(
            f"{meta['name']} did not timestamp this price, so how old it is "
            "is unknown"
        )
    elif stale:
        notes.append(
            f"The market is open but this price is from {as_of} — the fetch "
            "has not refreshed"
        )
    elif not sess["trading"]:
        notes.append(f"{sess['label']}. This is the last price from {as_of}")
    elif not live:
        # Session open, quote timed and not stalled, but old enough that
        # calling it the current price would be a stretch.
        notes.append(f"{sess['label']}. The last trade this feed has is from {as_of}")

    if change["known"] and not change["baseline_verified"]:
        notes.append(
            "The day change is measured against an undated previous close, so "
            "it may span more than one session"
        )
    elif not change["known"] and data.get("current_price") is not None:
        notes.append(f"No day change: {change['why_not']}")

    return {
        "source": key or "",
        "source_name": meta["name"],
        "source_note": meta["note"],
        "quotes_trades": meta["quotes_trades"],
        "prev_day_range": meta["prev_day_range"],
        "as_of": as_of,
        "age_seconds": age,
        "timed": quoted is not None,
        "live": live,
        "stale": stale,
        "session": sess["code"],
        "session_label": sess["label"],
        "trading": sess["trading"],
        "change": change,
        "caveat": notes[0] if notes else "",
        "caveats": notes,
    }
