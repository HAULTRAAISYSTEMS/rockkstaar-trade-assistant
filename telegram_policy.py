"""Low-noise Telegram delivery. Dashboard feeds remain unfiltered.

Delivery history uses the application database (Postgres in production).
Only confirmed Telegram successes are committed; concurrent workers serialize
on a per-chat row. A crash after Telegram accepts but before commit may retry.
"""
import hashlib
import json
import logging
import os
from datetime import timedelta, date
from html import escape
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


def _db():
    from database import get_db
    return get_db()


def _transport(message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        return False
    payload = json.dumps({'chat_id': chat, 'text': message, 'parse_mode': 'HTML'}).encode()
    req = Request(f'https://api.telegram.org/bot{token}/sendMessage', data=payload,
                  headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=6) as response:
        return json.load(response).get('ok') is True


def send_once(key, message, day, category, cap=3):
    """Atomically enforce per-category daily limits and durable event identity."""
    chat = os.environ.get('TELEGRAM_CHAT_ID')
    if not chat or not os.environ.get('TELEGRAM_BOT_TOKEN'):
        return False
    # Hash recipient identifiers in the delivery ledger.
    recipient = hashlib.sha256(chat.encode()).hexdigest()
    try:
        with _db() as conn:
            conn.execute('INSERT OR IGNORE INTO telegram_locks (recipient, version) VALUES (?, 0)', (recipient,))
            conn.execute('UPDATE telegram_locks SET version = version + 1 WHERE recipient = ?', (recipient,))
            exists = conn.execute('SELECT event_key FROM telegram_deliveries WHERE recipient = ? AND event_key = ?',
                                  (recipient, key)).fetchone()
            count = conn.execute('SELECT COUNT(*) AS n FROM telegram_deliveries WHERE recipient = ? AND day = ? AND category = ?',
                                 (recipient, day, category)).fetchone()['n']
            if exists or count >= cap:
                conn.commit()
                return False
            if not _transport(message):
                return False
            conn.execute('INSERT INTO telegram_deliveries (recipient, event_key, day, category) VALUES (?, ?, ?, ?)',
                         (recipient, key, day, category))
            conn.commit()
            return True
    except Exception as exc:
        # Never log URLs containing the bot token.
        log.warning('Telegram delivery failed (%s)', type(exc).__name__)
        return False


def watchlist():
    """Use actual list membership, not the entire cached stock-data universe."""
    with _db() as conn:
        owner = os.environ.get('TELEGRAM_USER_ID')
        if owner:
            rows = conn.execute('SELECT DISTINCT ws.ticker FROM watchlist_stocks ws JOIN watchlists w ON w.id = ws.watchlist_id WHERE w.user_id = ?', (int(owner),)).fetchall()
        else:
            rows = conn.execute('SELECT DISTINCT ticker FROM watchlist_stocks').fetchall()
        return {r['ticker'] for r in rows}


def _safe(call, default):
    try:
        return call()
    except Exception as exc:
        log.warning('Telegram feed unavailable (%s)', type(exc).__name__)
        return default


def _line(item, field):
    return escape(str(item.get(field) or 'Unavailable')[:120])


def check_intel(engine):
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        return []
    now = engine._et_now()
    today = now.date()
    day = today.isoformat()
    sent = []
    wl = watchlist()
    # Sunday 6 PM ET, with weekday catch-up if the service was offline.
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    if today.weekday() == 6 and now.hour < 18:
        sunday -= timedelta(days=7)
    start = sunday + timedelta(days=1)
    end = start + timedelta(days=7)
    weekly_due = (today.weekday() == 6 and now.hour >= 18) or (today.weekday() < 5 and now.hour >= 7)
    if weekly_due:
        # Check before fetching expensive calendar sources on every loop.
        with _db() as conn:
            recipient = hashlib.sha256(os.environ.get('TELEGRAM_CHAT_ID', '').encode()).hexdigest()
            done = conn.execute('SELECT event_key FROM telegram_deliveries WHERE recipient = ? AND event_key = ?',
                                (recipient, f'weekly:{start}')).fetchone()
        if not done:
            lines = [f'📅 <b>Week of {start:%b %d}</b>']
            seen = set()
            def add(items, date_field, title_field, label, only_watchlist=False):
                rows = []
                for item in items:
                    if only_watchlist and item.get('ticker') not in wl:
                        continue
                    try:
                        d = date.fromisoformat(str(item.get(date_field, ''))[:10])
                    except ValueError:
                        continue
                    ident = (label, item.get(title_field), d)
                    if start <= d < end and ident not in seen:
                        seen.add(ident)
                        clock = item.get('time_label') or item.get('time')
                        timing = ''
                        if clock:
                            zone = item.get('time_zone') or 'timezone unconfirmed'
                            timing = ' · ' + escape(str(clock)[:35])
                            if not item.get('time_label'):
                                timing += ' ' + escape(zone)
                        rows.append((d, f'{d:%a %m/%d}: {_line(item, title_field)}{timing}'))
                if rows:
                    lines.append(f'\n<b>{label}</b>')
                    lines.extend(text for _, text in sorted(rows)[:6])
                    if len(rows) > 6:
                        lines.append(f'+ {len(rows)-6} more in Tradestaar')
            econ = _safe(engine.fetch_economic_calendar, [])
            add([e for e in econ if e.get('impact') == 'HIGH'], 'date', 'event', 'Major economic events')
            earnings = _safe(lambda: engine.fetch_earnings_calendar(tickers=sorted(wl)), {}) if wl else {}
            add([i for k,v in earnings.items() if isinstance(v, list) for i in v if isinstance(i,dict)], 'date', 'ticker', 'Watchlist earnings', True)
            add(_safe(engine.fetch_dividends, []), 'ex_date', 'ticker', 'Watchlist dividends', True)
            add(_safe(engine.fetch_stock_splits, []), 'eff_date', 'ticker', 'Watchlist splits', True)
            lines.append('\nCalendar coverage may be incomplete; check Tradestaar for updates and event times.')
            if send_once(f'weekly:{start}', '\n'.join(lines), day, 'weekly', 1):
                sent.append({'type': 'weekly'})
    if today.weekday() >= 5 or not 7 <= now.hour < 18:
        return sent
    events = [e for e in _safe(engine.fetch_economic_calendar, []) if str(e.get('date', ''))[:10] == day and e.get('impact') == 'HIGH']
    if events:
        message = '⚠️ <b>Major economic events today</b>\n' + '\n'.join(_line(e, 'event') + ' · ' + _line(e, 'time') + ' ' + escape(e.get('time_zone') or '(timezone unconfirmed)') for e in events[:10])
        if send_once(f'macro:{day}', message, day, 'macro', 1):
            sent.append({'type': 'economic'})
    macro = _safe(engine.fetch_macro_environment, {})
    extremes = []
    if (macro.get('vix_level') or 0) >= 35:
        extremes.append(f'VIX: {macro["vix_level"]:.1f}')
    if abs(macro.get('yield_change_bps') or 0) >= 15:
        extremes.append(f'10-year yield daily move: {macro["yield_change_bps"]:+.1f} bps')
    if extremes and send_once(f'volatility:{day}', '⚠️ <b>Unusual market conditions</b>\n' + '\n'.join(extremes), day, 'volatility', 1):
        sent.append({'type': 'volatility'})
    news = [n for n in _safe(engine.fetch_market_news, []) if (n.get('impact') == 'CRITICAL' and n.get('ticker') in wl | {'SPY', 'QQQ'}) or (n.get('impact') == 'HIGH' and n.get('ticker') in wl)]
    # Bundle related news into at most one compact digest per day. Headlines
    # are selected in feed order; routine news remains available in the app.
    if news:
        message = '📰 <b>Important news</b>\n' + '\n\n'.join(
            f'{_line(n, "ticker")}: {_line(n, "headline")}' for n in news[:3])
        fingerprint = hashlib.sha256(message.encode()).hexdigest()
        if send_once(f'news:{fingerprint}', message, day, 'news', 1):
            sent.append({'type': 'news'})
    return sent


def send_setups(opportunities, wl, now):
    """Watchlist: score >=7; broader scanner universe: strongest score (10).

    Both require price above VWAP and near the intraday high. The VWAP
    condition is an explicit invalidation reference, not a computed stop order.
    """
    if now.weekday() >= 5 or not (9 * 60 + 30 <= now.hour * 60 + now.minute < 16 * 60):
        return
    day = now.date().isoformat()
    for opp in sorted(opportunities, key=lambda o: o.get('scan_score', 0), reverse=True):
        ticker = opp['ticker']
        minimum = 7 if ticker in wl else 10
        if opp.get('scan_score', 0) < minimum or not opp.get('above_vwap') or not opp.get('breaking_high'):
            continue
        if not opp.get('vwap') or not opp.get('intraday_high'):
            continue
        scope = 'Watchlist' if ticker in wl else 'Market discovery'
        msg = (f'🎯 <b>{escape(ticker)} · {scope}</b>\n'
               f'{_line(opp, "primary_tag")} · score {opp["scan_score"]}/10\n'
               f'Trigger reference: intraday high ${opp["intraday_high"]:.2f}\n'
               f'Invalidation reference: loss of VWAP ${opp["vwap"]:.2f}\n'
               f'{_line(opp, "reason")}\nObserved: {_line(opp, "scanned_at")}')
        # One setup per ticker per day, regardless of small score/price changes.
        send_once(f'setup:{day}:{ticker}', msg, day, 'setup', 3)
