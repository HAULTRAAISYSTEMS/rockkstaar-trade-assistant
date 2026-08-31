"""Interval gating for the unattended Command Center alert sync.

The cron fires every 5 minutes. A full Form 4 sweep is expensive against the
SEC, so the gate below is what stops it from running on every firing.
"""
from datetime import datetime, timedelta, timezone

import pytest

import insider_alert_sync as sync

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()


def test_never_run_before_is_due():
    """A fresh deployment must sync immediately, not wait out an interval."""
    assert sync.is_due(None, 30, NOW) is True
    assert sync.is_due("", 30, NOW) is True


def test_unparseable_timestamp_is_due():
    assert sync.is_due("not-a-date", 30, NOW) is True


@pytest.mark.parametrize("minutes,expected", [(0, False), (5, False), (29, False), (30, True), (31, True)])
def test_interval_boundary(minutes, expected):
    assert sync.is_due(ago(minutes=minutes), 30, NOW) is expected


def test_five_minute_cron_mostly_skips_a_thirty_minute_sync():
    """Six firings per half hour; only the one past the interval may run."""
    ran = [sync.is_due(ago(minutes=m), 30, NOW) for m in (0, 5, 10, 15, 20, 25, 30)]
    assert ran.count(True) == 1


def test_naive_timestamp_is_treated_as_utc():
    naive = NOW.replace(tzinfo=None) - timedelta(minutes=45)
    assert sync.is_due(naive.isoformat(), 30, NOW) is True


def test_intervals_come_from_env(monkeypatch):
    monkeypatch.setenv("INSIDER_ALERT_SYNC_MINUTES", "10")
    monkeypatch.setenv("EARNINGS_ALERT_SYNC_MINUTES", "120")
    assert sync.insider_interval_minutes() == 10
    assert sync.earnings_interval_minutes() == 120


@pytest.mark.parametrize("bad", ["abc", "0", "-5", ""])
def test_bad_interval_falls_back_to_default(monkeypatch, bad):
    monkeypatch.setenv("INSIDER_ALERT_SYNC_MINUTES", bad)
    assert sync.insider_interval_minutes() == sync.DEFAULT_INSIDER_MINUTES


def test_insider_history_window_is_short():
    """The dashboard looks back 30 days; the cron only needs what is new."""
    assert sync.INSIDER_HISTORY_DAYS <= 3


def test_run_reports_both_sources_and_swallows_failures(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("SEC unavailable")
    monkeypatch.setattr(sync, "sync_insider_alerts", boom)
    monkeypatch.setattr(sync, "sync_earnings_alerts", lambda *a, **k: {"ran": True, "added": 3})
    result = sync.run(object(), ["NVDA"])
    assert result["earnings_alerts_added"] == 3
    assert result["insider_alerts_added"] == 0
    assert result["alert_sync_errors"] == ["insider:RuntimeError"]


def test_sync_skipped_when_no_tickers(monkeypatch):
    monkeypatch.setattr(sync, "is_due", lambda *a, **k: True)
    import database
    monkeypatch.setattr(database, "get_setting", lambda *a, **k: None, raising=False)
    result = sync.sync_insider_alerts(object(), [], now=NOW)
    assert result["ran"] is False and result["reason"] == "no_tickers"
