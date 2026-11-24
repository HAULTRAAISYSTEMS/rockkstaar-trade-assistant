"""Monday night the globe showed Friday's moves: Yahoo's daily chart came back
without Monday's close, and every caller reads the day's move as
closes[-1] / closes[-2]. _daily_closes rebuilds the series from the chart's
meta so the latest session is always the last close."""
from datetime import datetime
from zoneinfo import ZoneInfo

import market_engine as me

ET = ZoneInfo("America/New_York")


def _ts(y, m, d, hh=9, mm=30):
    return int(datetime(y, m, d, hh, mm, tzinfo=ET).timestamp())


def _node(stamps, closes, price=None, price_at=None):
    meta = {"exchangeTimezoneName": "America/New_York"}
    if price is not None:
        meta.update(regularMarketPrice=price, regularMarketTime=price_at)
    return {"meta": meta, "timestamp": stamps, "indicators": {"quote": [{"close": closes}]}}


THU, FRI, MON = _ts(2026, 11, 19), _ts(2026, 11, 20), _ts(2026, 11, 23)
MON_CLOSE = _ts(2026, 11, 23, 16, 0)


def _day_move(series):
    return round((series[-1] / series[-2] - 1) * 100, 2)


def test_null_close_for_today_uses_the_regular_close_not_fridays_move():
    # Thu 100 → Fri 86 (-14%); Monday's bar has no close yet; Monday closed at 88.
    node = _node([THU, FRI, MON], [100.0, 86.0, None], price=88.0, price_at=MON_CLOSE)
    series = me._daily_closes(node)
    assert series == [100.0, 86.0, 88.0]
    assert _day_move(series) == 2.33          # Monday's move, not Friday's -14%


def test_missing_bar_for_today_is_appended_from_meta():
    node = _node([THU, FRI], [100.0, 86.0], price=88.0, price_at=MON_CLOSE)
    assert me._daily_closes(node)[-2:] == [86.0, 88.0]


def test_a_second_bar_for_the_same_day_collapses_to_the_later():
    node = _node([FRI, MON, MON_CLOSE], [86.0, 87.0, 88.0], price=88.0, price_at=MON_CLOSE)
    assert me._daily_closes(node) == [86.0, 88.0]


def test_a_complete_series_is_left_alone():
    node = _node([THU, FRI, MON], [100.0, 86.0, 88.0], price=88.0, price_at=MON_CLOSE)
    assert me._daily_closes(node) == [100.0, 86.0, 88.0]


def test_pre_market_does_not_invent_a_new_day():
    # Tuesday 7 AM: the last regular price is still Monday's close.
    node = _node([FRI, MON], [86.0, 88.0], price=88.0, price_at=MON_CLOSE)
    assert me._daily_closes(node) == [86.0, 88.0]


def test_without_timestamps_falls_back_to_closes():
    node = {"meta": {}, "indicators": {"quote": [{"close": [1.0, None, 2.0]}]}}
    assert me._daily_closes(node) == [1.0, 2.0]
