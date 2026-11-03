"""fundamentals_alerts.py — the page was right, it just never came to find you.

The fundamentals engine computes every ratio correctly and the peer panel
ranks them against a named group. Both are true only while you are looking
at them. A company whose return on equity crosses into negative territory,
or whose debt to equity goes through 1.0x, or that slides from in line with
its peers to materially behind them, does so quietly, and the app tells
nobody. This closes that.

It is deliberately hard to trigger. These ratios move a little every
quarter; a notification for "a number changed" would fire constantly and be
muted within a week, which is worse than silence because it also takes the
real ones with it. So three kinds of change qualify, and nothing else:

  CROSSINGS       a line that means something was crossed — profit to loss,
                  return on equity through zero, debt to equity through
                  1.0x, the current ratio under 1. Rare by construction.
  PEER SLIPPAGE   a ratio that was level with or ahead of the peer group is
                  now materially behind it, judged by the same per-ratio
                  thresholds the comparison panel uses.
  VERDICT         the scorecard's own conclusion changed.

THE RULE THAT MATTERS MOST HERE. A number that stopped arriving is not a
number that moved. If return on equity was 12.4% and is now absent — a feed
failed, a filing has not been parsed, a provider changed a field name —
that is not a collapse to zero and not a crossing. Every comparison in this
module requires BOTH sides to be real numbers, and a disappearance produces
no alert at all. Getting this wrong would send "Intel's ROE just went
negative" because a JSON key was renamed, and that notification is worse
than no notification: it is confidently false, at speed, about money.

A first sighting is likewise never a change. With no previous snapshot
there is nothing to compare, so a newly tracked company stays silent until
there is a second reading.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

TRIGGERS = ("threshold_crossing", "peer_slippage", "verdict_change")
DEFAULT_TRIGGERS = TRIGGERS

# The ratios whose snapshot is kept between runs. Narrower than everything
# the engine computes: these are the ones a crossing or a peer slide can be
# argued from, and a smaller snapshot is a smaller surface for a provider
# rename to corrupt.
TRACKED = ("roe", "roic", "net_margin", "operating_margin", "gross_margin",
           "revenue_growth", "debt_to_equity", "current_ratio")

# A line is worth a notification when being on one side of it means
# something different from being on the other. `direction` is the move that
# matters: "down" fires on a fall through the line, "up" on a rise through.
CROSSINGS = (
    {"key": "roe", "line": 0.0, "direction": "down", "unit": "%",
     "headline": "return on equity turned negative",
     "meaning": "it is now losing money on shareholder capital"},
    {"key": "roe", "line": 0.0, "direction": "up", "unit": "%",
     "headline": "return on equity turned positive",
     "meaning": "it is earning on shareholder capital again"},
    {"key": "net_margin", "line": 0.0, "direction": "down", "unit": "%",
     "headline": "swung to a net loss",
     "meaning": "nothing survives to the bottom line"},
    {"key": "net_margin", "line": 0.0, "direction": "up", "unit": "%",
     "headline": "returned to profit",
     "meaning": "the bottom line is positive again"},
    {"key": "operating_margin", "line": 0.0, "direction": "down", "unit": "%",
     "headline": "operating margin turned negative",
     "meaning": "the business is losing money before financing and tax"},
    {"key": "debt_to_equity", "line": 1.0, "direction": "up", "unit": "x",
     "headline": "now owes more than it owns",
     "meaning": "debt has passed shareholder equity"},
    {"key": "current_ratio", "line": 1.0, "direction": "down", "unit": "x",
     "headline": "short-term bills now exceed short-term assets",
     "meaning": "current liabilities are larger than current assets"},
)


def _num(value) -> Optional[float]:
    """A finite number, or None. Bools and NaN are not numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _fmt(value, unit: str) -> str:
    if value is None:
        return "—"
    return f"{value:,.1f}%" if unit == "%" else f"{value:,.2f}x"


def snapshot(ticker: str, data: dict, comparison: dict | None = None) -> dict:
    """What this company looks like now, in the shape the next run compares to.

    Ratios that did not arrive are stored as None rather than omitted, so the
    next run can tell "absent again" from "never tracked".
    """
    data = data if isinstance(data, dict) else {}
    out = {"ticker": str(ticker or "").upper(),
           "verdict": (data.get("verdict") or None)}
    for key in TRACKED:
        out[key] = _num(data.get(key))
    # Which ratios the peer panel judged materially behind the group.
    behind = []
    for row in ((comparison or {}).get("rows") or []):
        if isinstance(row, dict) and row.get("verdict") == "behind":
            behind.append(row.get("key"))
    out["behind_peers"] = sorted(k for k in behind if k)
    return out


def _crossed(before: float, after: float, line: float, direction: str) -> bool:
    """Did this move take the value across the line, in the direction given?

    Touching the line exactly is not a crossing on the way down — a return on
    equity of exactly 0.0 is not yet negative — but landing on it from below
    is not a crossing either. Both sides must be strictly clear of it.
    """
    if direction == "down":
        return before > line and after < line
    return before < line and after > line


def detect(previous: dict, current: dict, *, triggers=None) -> list[dict]:
    """Every change worth a notification between two snapshots.

    Returns [{key, title, body, kind}]. `key` is a stable identity for the
    change so the delivery layer can guarantee it is sent once.

    With no previous snapshot this returns nothing: a first sighting is not a
    change, and reporting one would mean every newly tracked company
    announcing its entire balance sheet.
    """
    triggers = list(triggers if triggers is not None else DEFAULT_TRIGGERS)
    if not isinstance(previous, dict) or not previous:
        return []
    if not isinstance(current, dict) or not current:
        return []

    ticker = str(current.get("ticker") or previous.get("ticker") or "").upper()
    if not ticker:
        return []
    alerts: list[dict] = []

    # ── Lines that mean something ────────────────────────────────────────
    if "threshold_crossing" in triggers:
        for rule in CROSSINGS:
            before, after = _num(previous.get(rule["key"])), _num(current.get(rule["key"]))
            # BOTH sides must be real. A ratio that stopped arriving has not
            # crossed anything, and treating its absence as a move is how a
            # renamed provider field becomes "Intel just went to a loss".
            if before is None or after is None:
                continue
            if not _crossed(before, after, rule["line"], rule["direction"]):
                continue
            alerts.append({
                "kind": "threshold_crossing",
                "key": f"fund:{ticker}:{rule['key']}:{rule['direction']}:{rule['line']}",
                "title": f"{ticker} {rule['headline']}",
                "body": (f"{_fmt(before, rule['unit'])} → {_fmt(after, rule['unit'])}"
                         f" — {rule['meaning']}."),
            })

    # ── Slipping behind the peer group ───────────────────────────────────
    if "peer_slippage" in triggers:
        was = set(previous.get("behind_peers") or [])
        now = set(current.get("behind_peers") or [])
        # Only ratios that were NOT behind and now are. A company already
        # behind on something does not re-announce it every run.
        slipped = sorted(now - was)
        if slipped:
            import peer_comparison
            labels = [peer_comparison.METRICS_BY_KEY[k]["label"].lower()
                      for k in slipped if k in peer_comparison.METRICS_BY_KEY]
            if labels:
                alerts.append({
                    "kind": "peer_slippage",
                    # Keyed on which ratios slipped, so the same slide is one
                    # alert however many times the comparison is recomputed.
                    "key": f"fund:{ticker}:behind:{'+'.join(slipped)}",
                    "title": f"{ticker} fell behind its peers",
                    "body": ("Now materially below the peer median on "
                             + ", ".join(labels[:3]) + "."),
                })

    # ── The scorecard's own conclusion ───────────────────────────────────
    if "verdict_change" in triggers:
        before, after = previous.get("verdict"), current.get("verdict")
        # A verdict that disappeared is a scoring failure, not a downgrade.
        if before and after and str(before) != str(after):
            alerts.append({
                "kind": "verdict_change",
                "key": f"fund:{ticker}:verdict:{after}",
                "title": f"{ticker}: {before} → {after}",
                "body": "The fundamentals scorecard reached a different conclusion.",
            })

    return alerts


def payload(alert: dict) -> dict:
    """A detected change as a push notification."""
    ticker = str(alert.get("key", "")).split(":")[1] if ":" in alert.get("key", "") else ""
    return {
        "title": alert.get("title") or "Fundamentals changed",
        "body": (alert.get("body") or "")[:240],
        "url": f"/fundamentals?ticker={ticker}" if ticker else "/fundamentals",
        "tag": alert.get("key", "fundamentals"),
    }
