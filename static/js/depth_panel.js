/* Level 2 + Time & Sales panel on the Terminal.
   Polls /api/depth/<SYM> about once a second while the page is visible; the
   server keeps one Schwab streamer connection and writes snapshots that every
   worker serves (market_depth.py). Asking for a symbol keeps it subscribed. */
(function () {
  'use strict';
  var root = document.getElementById('dp');
  if (!root) return;
  var sym = (root.getAttribute('data-symbol') || '').toUpperCase();
  var timer = null, inflight = false, lastPrice = null;
  var BIG = 10000;            // shares: a "big print" gets highlighted
  var STATUS = {
    live: 'Live · Schwab', connecting: 'Connecting to Schwab…', starting: 'Connecting…',
    idle: 'Waiting', error: 'Reconnecting…', not_connected: 'Schwab not connected'
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function px(v) { return v == null ? '—' : Number(v).toFixed(Number(v) < 1 ? 4 : 2); }
  function sz(v) {
    if (v == null) return '';
    v = Number(v);
    return v >= 1e6 ? (v / 1e6).toFixed(1) + 'M' : v >= 1e4 ? (v / 1e3).toFixed(0) + 'K' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'K' : String(v);
  }
  function hms(ms) {
    if (!ms) return '';
    return new Date(Number(ms)).toLocaleTimeString('en-US', {hour12: false, timeZone: 'America/New_York'});
  }

  function setStatus(st) {
    var el = $('dp-status'), state = (st && st.state) || 'starting';
    var stale = st && st.at && (Date.now() / 1000 - st.at > 20) && state === 'live';
    el.setAttribute('data-state', stale ? 'error' : state);
    var text = STATUS[state] || state;
    if (state === 'not_connected') {
      $('dp-status-text').innerHTML = '<a href="/brokers">Connect Schwab</a>';
      $('dp-note').textContent = (st && st.msg) || 'Level 2 and the tape come from your Schwab connection.';
      return;
    }
    $('dp-status-text').textContent = stale ? 'Feed paused' : text;
  }

  function sideRows(levels, side, maxSize) {
    if (!levels.length) return '<div class="dp-empty">' + (side === 'bids' ? 'No bids' : 'No offers') + '</div>';
    return levels.map(function (lv, i) {
      var w = maxSize ? Math.max(4, Math.round(lv.size / maxSize * 100)) : 0;
      var first = (lv.mms || [])[0];
      var mm = (first ? first.id : '') + (lv.count > 1 ? ' +' + (lv.count - 1) : '');
      var cells = side === 'bids'
        ? '<span class="mm">' + esc(mm) + '</span><span class="sz">' + sz(lv.size) + '</span><span class="p">' + px(lv.price) + '</span>'
        : '<span class="p">' + px(lv.price) + '</span><span class="sz">' + sz(lv.size) + '</span><span class="mm">' + esc(mm) + '</span>';
      return '<div class="dp-row' + (i === 0 ? ' top' : '') + '" style="--w:' + w + '%">' + cells + '</div>';
    }).join('');
  }

  function render(d) {
    setStatus(d.status);
    var q = d.quote || {}, book = d.book || {bids: [], asks: []};
    $('dp-bid').textContent = px(q.bid); $('dp-ask').textContent = px(q.ask);
    $('dp-bidsz').textContent = q.bid_size != null ? '× ' + sz(q.bid_size) : '';
    $('dp-asksz').textContent = q.ask_size != null ? '× ' + sz(q.ask_size) : '';
    var spr = (q.bid != null && q.ask != null) ? q.ask - q.bid : null;
    $('dp-spread').textContent = spr == null ? '—' : spr.toFixed(Math.abs(spr) < 0.01 ? 4 : 2);
    if (q.last != null) {
      var dir = lastPrice == null ? '' : (q.last > lastPrice ? 'up' : q.last < lastPrice ? 'down' : '');
      $('dp-last').textContent = 'Last ' + px(q.last);
      $('dp-last').className = dir; lastPrice = q.last;
    }
    var maxSize = 0, bidTot = 0, askTot = 0;
    book.bids.forEach(function (l) { maxSize = Math.max(maxSize, l.size); bidTot += l.size; });
    book.asks.forEach(function (l) { maxSize = Math.max(maxSize, l.size); askTot += l.size; });
    $('dp-bids').innerHTML = sideRows(book.bids, 'bids', maxSize);
    $('dp-asks').innerHTML = sideRows(book.asks, 'asks', maxSize);
    var tot = bidTot + askTot;
    $('dp-imb-bid').style.width = tot ? (bidTot / tot * 100) + '%' : '50%';
    $('dp-imb-ask').style.width = tot ? (askTot / tot * 100) + '%' : '50%';
    $('dp-imb-text').textContent = tot ? 'Bids ' + sz(bidTot) + ' · Offers ' + sz(askTot) : 'Book imbalance —';

    var min = Number($('dp-min').value || 0);
    var tape = (d.tape || []).filter(function (p) { return p.size >= min; }).slice(0, 80);
    $('dp-tape').innerHTML = tape.length ? tape.map(function (p) {
      return '<div class="dp-print ' + esc(p.side) + (p.size >= BIG ? ' big' : '') + '"><span>' + hms(p.t) +
        '</span><span>' + px(p.price) + '</span><span>' + (p.combined ? '≈' : '') + sz(p.size) + '</span></div>';
    }).join('') : '<div class="dp-empty">' + (d.status && d.status.state === 'live' ? 'Waiting for prints…' : 'No prints yet') + '</div>';
    var st = d.stats || {}, b = st.buy_vol || 0, s = st.sell_vol || 0;
    $('dp-flow-buy').style.width = (b + s) ? (b / (b + s) * 100) + '%' : '50%';
    $('dp-flow-sell').style.width = (b + s) ? (s / (b + s) * 100) + '%' : '50%';
    $('dp-flow-text').textContent = 'At ask ' + sz(b) + ' · At bid ' + sz(s);
    if (d.status && d.status.state === 'live' && !book.bids.length && !book.asks.length) {
      $('dp-note').textContent = 'The book is empty — Schwab Level 2 fills during market hours.';
    }
  }

  function poll() {
    if (!sym || inflight || document.hidden) return;
    inflight = true;
    fetch('/api/depth/' + encodeURIComponent(sym), {credentials: 'same-origin', cache: 'no-store'})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.ok && d.symbol === sym) render(d); })
      .catch(function () {})
      .then(function () { inflight = false; });
  }

  function start() { stop(); poll(); timer = setInterval(poll, 1000); }
  function stop() { if (timer) clearInterval(timer); timer = null; }

  window.dpSetSymbol = function (s) {
    s = String(s || '').toUpperCase();
    if (!s || s === sym) return;
    sym = s; lastPrice = null;
    $('dp-sym').textContent = s;
    $('dp-bids').innerHTML = $('dp-asks').innerHTML = $('dp-tape').innerHTML = '';
    start();
  };

  root.addEventListener('click', function (e) {
    var t = e.target.closest('[data-dp-tab]');
    if (!t) return;
    root.querySelectorAll('[data-dp-tab]').forEach(function (b) {
      var on = b === t; b.classList.toggle('on', on); b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    root.setAttribute('data-view', t.getAttribute('data-dp-tab'));
  });
  $('dp-min').addEventListener('change', poll);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) poll(); });
  root.setAttribute('data-view', 'book');
  start();
})();
