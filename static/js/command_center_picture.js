/* Morning Command Center — clock, index strip, orbit chips, breadth ring.
 * Everything here is real data: /api/market-tape quotes and /api/today/breadth.
 * Nothing is invented; a value we don't have shows "—". */
(function () {
  'use strict';
  var root = document.querySelector('.cmd27');
  if (!root) return;

  function $(s, el) { return (el || document).querySelector(s); }
  function $$(s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); }
  function num(v) { return typeof v === 'number' && isFinite(v); }
  function pct(v) { return num(v) ? (v > 0 ? '+' : '') + v.toFixed(2) + '%' : '—'; }
  function px(v) { return num(v) ? v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'; }
  function tone(el, v) {
    if (!el) return;
    el.classList.toggle('is-up', num(v) && v > 0);
    el.classList.toggle('is-down', num(v) && v < 0);
  }

  // ── ET clock / date line ─────────────────────────────────────────
  var clock = $('#cmd27-clock');
  function tick() {
    if (!clock) return;
    var d = new Date();
    try {
      var date = d.toLocaleDateString('en-US', { timeZone: 'America/New_York', weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
      var time = d.toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' });
      clock.textContent = date + ' • ' + time + ' ET';
      clock.setAttribute('datetime', d.toISOString());
    } catch (e) { clock.textContent = d.toDateString(); }
  }
  tick(); setInterval(tick, 15000);

  // ── Quotes → pulse rows, KPI strip, orbit chips ──────────────────
  var INDEX = { SPY: 1, QQQ: 1, DIA: 1, IWM: 1, VIX: 1 };
  var chartUrl = ($('[data-cc3d-orb]') || {}).getAttribute ? $('[data-cc3d-orb]').getAttribute('data-chart-url') : '';

  function applyQuotes(items) {
    var by = {};
    items.forEach(function (q) { if (q && q.symbol) by[String(q.symbol).toUpperCase()] = q; });

    // Game Plan: fill SPY/QQQ % from the tape only while the server value is missing.
    ['SPY', 'QQQ'].forEach(function (sym) {
      var el = $('#cc-' + sym.toLowerCase()), q = by[sym];
      if (el && q && num(q.change_pct) && el.textContent.trim() === '—') { el.textContent = pct(q.change_pct); tone(el, q.change_pct); }
    });
    var vq = by.VIX, ve = $('#cc-vix');
    if (ve && vq && num(vq.price) && ve.textContent.trim() === '—') ve.textContent = vq.price.toFixed(2);

    lastItems = items;
    renderOrbit();
  }

  // ── Orbit: your starred tickers (saved to your account), else top movers ──
  var orbit = [], orbitLoaded = false, lastItems = [];
  var orbitEl = $('#cmd27-orbit'), bar = $('#cmd27-orbit-bar');
  function bySym() { var m = {}; lastItems.forEach(function (q) { if (q && q.symbol) m[String(q.symbol).toUpperCase()] = q; }); return m; }
  function announce() { document.dispatchEvent(new CustomEvent('cmd27:orbit', { detail: { tickers: orbit.slice() } })); }
  function saveOrbit() {
    announce(); renderOrbit();
    fetch('/api/command/orbit', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tickers: orbit }) })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.ok) { orbit = d.tickers; announce(); renderOrbit(); } })
      .catch(function () {});
  }
  function toggle(sym) {
    sym = String(sym || '').toUpperCase(); if (!sym) return;
    var i = orbit.indexOf(sym);
    if (i >= 0) orbit.splice(i, 1); else { if (orbit.length >= 12) orbit.shift(); orbit.push(sym); }
    saveOrbit();
  }
  document.addEventListener('cmd27:orbit-toggle', function (e) { toggle(e.detail && e.detail.symbol); });

  function renderOrbit() {
    if (!orbitEl) return;
    var m = bySym(), narrow = window.innerWidth < 760, list, mine = orbit.length > 0;
    if (mine) list = orbit.map(function (s) { return m[s] || { symbol: s }; });
    else list = lastItems.filter(function (q) { return q && q.symbol && !INDEX[String(q.symbol).toUpperCase()] && num(q.change_pct) && !/[=\/^]/.test(q.symbol) && !/^(ES|NQ|YM|RTY|CL|GC|SI|NG|ZB|ZN|DXY|VIX)$/i.test(q.symbol); })
      .sort(function (a, b) { return Math.abs(b.change_pct) - Math.abs(a.change_pct); });
    list = list.slice(0, narrow ? (mine ? 8 : 4) : (mine ? 12 : 10));
    orbitEl.innerHTML = '';
    var n = list.length;
    // Ring sized from the globe itself (same maths as the globe renderer:
    // R = 0.35·min side, silhouette ≈ 1.053·R, centre at 47% height), so
    // chips always circle outside the Earth whatever the screen.
    var box = orbitEl.getBoundingClientRect(), bw = box.width || 600, bh = box.height || 500;
    var mn = Math.min(bw, bh), RS = Math.min(mn * (mn > 420 ? 0.35 : 0.33), bw * 0.30) * 1.053, cyc = bh * 0.47;
    var ry = Math.min(bh * 0.5 - 24, RS + 40), rx = Math.min(bw * 0.5 - 34, Math.max(RS + 60, ry * 1.3));
    var rects = [], halfW = narrow ? 34 : 42, halfH = narrow ? 20 : 25, clear = RS + 14;
    list.forEach(function (q, i) {
      var sym = String(q.symbol).toUpperCase(), chg = q.change_pct;
      var ang = (-Math.PI / 2) + (i / Math.max(n, 1)) * Math.PI * 2 + 0.3;
      var dx = Math.cos(ang) * rx, dy = Math.sin(ang) * ry;
      // Push out until the chip's nearest corner clears the atmosphere.
      for (var k = 0; k < 12; k++) {
        var nx = Math.max(0, Math.abs(dx) - halfW), ny = Math.max(0, Math.abs(dy) - halfH);
        if (Math.sqrt(nx * nx + ny * ny) >= clear) break;
        dx *= 1.06; dy *= 1.06;
      }
      var spill = narrow ? -4 : 26;   // phones: chips stay fully on screen
      dx = Math.max(-bw / 2 + halfW - spill, Math.min(bw / 2 - halfW + spill, dx));
      dy = Math.max(-cyc + halfH, Math.min(bh - cyc - halfH, dy));
      rects.push([bw / 2 + dx - halfW, cyc + dy - halfH, bw / 2 + dx + halfW, cyc + dy + halfH]);
      var x = (bw / 2 + dx) / bw * 100, y = (cyc + dy) / bh * 100;
      var el = document.createElement('div');
      el.className = 'cmd27-chip ' + (num(chg) ? (chg < 0 ? 'is-down' : 'is-up') : 'is-flat') + (mine ? ' is-mine' : '');
      el.setAttribute('role', 'button'); el.tabIndex = 0;
      el.style.left = x + '%'; el.style.top = y + '%';
      el.innerHTML = '<span></span><small></small>';
      el.firstChild.textContent = sym; el.lastChild.textContent = pct(chg);
      el.title = (m[sym] ? 'Spin the globe to ' : 'Open the chart for ') + sym;
      function go() {
        if (m[sym]) document.dispatchEvent(new CustomEvent('cmd27:focus', { detail: { symbol: sym } }));
        else if (chartUrl) location.href = chartUrl.replace('__T__', encodeURIComponent(sym));
      }
      el.addEventListener('click', function (e) { if (!e.target.closest('.cmd27-chip__x')) go(); });
      el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
      if (mine) {
        var x2 = document.createElement('button'); x2.type = 'button'; x2.className = 'cmd27-chip__x';
        x2.setAttribute('aria-label', 'Remove ' + sym + ' from your orbit'); x2.textContent = '×';
        x2.addEventListener('click', function (e) { e.stopPropagation(); toggle(sym); });
        el.appendChild(x2);
      }
      orbitEl.appendChild(el);
    });
    document.dispatchEvent(new CustomEvent('cmd27:chips', { detail: { tickers: list.map(function (q) { return String(q.symbol).toUpperCase(); }), rects: rects } }));
    if (bar) {
      var lbl = $('#cmd27-orbit-label', bar), reset = $('#cmd27-orbit-reset', bar);
      lbl.textContent = mine ? 'Your orbit · ' + orbit.length + '/12' : 'Orbit · top movers';
      reset.hidden = !mine;
      $('#cmd27-orbit-hint', bar).hidden = mine;
    }
  }
  if (bar) {
    var form = $('#cmd27-orbit-form', bar);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var inp = $('input', form), v = (inp.value || '').trim().toUpperCase();
      if (/^[A-Z0-9][A-Z0-9.\-]{0,9}$/.test(v) && orbit.indexOf(v) < 0) { toggle(v); }
      inp.value = '';
    });
    $('#cmd27-orbit-reset', bar).addEventListener('click', function () { orbit = []; saveOrbit(); });
  }
  var rto = null;
  window.addEventListener('resize', function () { clearTimeout(rto); rto = setTimeout(renderOrbit, 150); }, { passive: true });
  fetch('/api/command/orbit', { credentials: 'same-origin', cache: 'no-store' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { if (d && d.ok) { orbit = d.tickers || []; orbitLoaded = true; announce(); renderOrbit(); } })
    .catch(function () {});
  // The globe's pins come from the shared tape; chips follow it too.
  document.addEventListener('tradestaar:tape', function (e) { if (Array.isArray(e.detail) && e.detail.length) { lastItems = e.detail; renderOrbit(); } });

  function loadTape() {
    fetch('/api/market-tape', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        var items = Array.isArray(d) ? d : (d.items || d.tape || d.quotes || []);
        if (items.length) applyQuotes(items);
        var live = d.live === true || d.fast_live === true;
        var badge = $('#cmd27-live'), txt = $('#cmd27-live-text');
        if (badge && txt && typeof d.live === 'boolean') {
          badge.classList.toggle('is-live', live); badge.classList.toggle('is-snapshot', !live);
          txt.textContent = live ? 'Live market · real-time' : 'Latest available';
        }
      }).catch(function () {});
  }

  // ── Breadth ring (sector funds advancing) ────────────────────────
  function loadBreadth() {
    fetch('/api/today/breadth', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        var ring = $('#cmd27-ring'); if (!ring) return;
        if (!d || !d.ok) {
          ring.classList.add('is-empty'); ring.style.setProperty('--p', 0);
          var note = $('#cmd27-breadth-note'); if (note) note.textContent = 'Sector quotes unavailable right now.';
          return;
        }
        ring.classList.remove('is-empty');
        ring.style.setProperty('--p', d.pct_advancing || 0);
        $('#cmd27-ring-pct').textContent = Math.round(d.pct_advancing || 0) + '%';
        $('#cmd27-adv').textContent = d.advancing;
        $('#cmd27-dec').textContent = d.declining;
        $('#cmd27-unc').textContent = d.unchanged;
        var n = $('#cmd27-breadth-note');
        if (n && d.rows && d.rows.length) {
          var best = d.rows[0], worst = d.rows[d.rows.length - 1];
          n.textContent = d.total + ' sector funds · lead ' + best.symbol + ' ' + pct(best.change_pct) + ', lag ' + worst.symbol + ' ' + pct(worst.change_pct);
        }
      }).catch(function () {});
  }

  function refresh() { if (document.hidden) return; loadTape(); loadBreadth(); }
  refresh();
  setInterval(refresh, 30000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
  var btn = $('#cc-refresh-btn'); if (btn) btn.addEventListener('click', refresh);
})();

/* Globe controls: zoom buttons, the Sectors dock and the sector panel. */
(function () {
  'use strict';
  var root = document.querySelector('.cmd27'); if (!root) return;
  function $(s, el) { return (el || document).querySelector(s); }
  function pct(v) { return typeof v === 'number' && isFinite(v) ? (v > 0 ? '+' : '') + v.toFixed(2) + '%' : '—'; }
  function tone(v) { return typeof v === 'number' && v > 0.05 ? 'is-up' : (typeof v === 'number' && v < -0.05 ? 'is-down' : ''); }
  function emit(n, d) { document.dispatchEvent(new CustomEvent(n, { detail: d })); }

  root.addEventListener('click', function (e) {
    var z = e.target.closest('[data-zoom]'); if (z) { emit('cmd27:zoom-cmd', { dir: z.getAttribute('data-zoom') }); return; }
    var sec = e.target.closest('[data-sector]'); if (sec) { emit('cmd27:sector-open', { key: sec.getAttribute('data-sector') }); return; }
  });

  // Hide the orbit chips while zoomed in — the towers carry the names then.
  var globeBox = $('.cmd27-hero__globe');
  document.addEventListener('cmd27:zoom', function (e) {
    if (globeBox) globeBox.classList.toggle('is-zoomed', (e.detail && e.detail.zoom) > 1.15);
  });

  // Sectors dock: one pill per territory, live average, tap to fly in.
  var dock = $('#cmd27-dock'), focused = null;
  document.addEventListener('cmd27:regions', function (e) {
    var list = e.detail || [];
    if (!dock) return;
    dock.hidden = !list.length;
    dock.innerHTML = '<span class="cmd27-dock__lbl">Fly to a sector</span>';
    list.forEach(function (r) {
      var b = document.createElement('button');
      b.type = 'button'; b.setAttribute('data-sector', r.key);
      b.className = 'cmd27-dock__pill ' + tone(r.avg) + (focused === r.key ? ' is-on' : '');
      b.innerHTML = '<span></span><small></small>';
      b.firstChild.textContent = r.name.replace(' · OTHER', '').replace('COMMUNICATION', 'COMMS');
      b.lastChild.textContent = pct(r.avg) + ' · ' + r.n;
      dock.appendChild(b);
    });
  });

  // Sector panel.
  var panel = $('#cmd27-sector'), list = $('#cmd27-sector-list');
  var orbitSet = {};
  document.addEventListener('cmd27:orbit', function (e) { orbitSet = {}; ((e.detail && e.detail.tickers) || []).forEach(function (t) { orbitSet[t] = 1; }); });
  document.addEventListener('cmd27:sector', function (e) {
    var d = e.detail;
    focused = d ? d.key : null;
    if (dock) Array.prototype.forEach.call(dock.querySelectorAll('[data-sector]'), function (b) { b.classList.toggle('is-on', b.getAttribute('data-sector') === focused); });
    if (!panel) return;
    if (globeBox) globeBox.classList.toggle('has-sector', !!d);
    if (!d) { panel.hidden = true; return; }
    panel.hidden = false;
    $('#cmd27-sector-name').textContent = d.name;
    var avg = $('#cmd27-sector-avg'); avg.textContent = pct(d.avg) + ' avg'; avg.className = tone(d.avg);
    list.innerHTML = '';
    d.rows.forEach(function (r, i) {
      var li = document.createElement('li');
      li.innerHTML = '<button type="button" class="cmd27-sector__go"><i></i><b></b><span></span><em></em></button><button type="button" class="cmd27-sector__star"></button>';
      var go = li.firstChild, st = li.lastChild;
      go.children[0].textContent = i + 1;
      go.children[1].textContent = r.symbol;
      go.children[2].textContent = r.price != null ? Number(r.price).toFixed(2) : '';
      go.children[3].textContent = pct(r.chg); go.children[3].className = tone(r.chg);
      go.addEventListener('click', function () { emit('cmd27:focus', { symbol: r.symbol }); });
      st.textContent = orbitSet[r.symbol] ? '★' : '☆';
      st.title = orbitSet[r.symbol] ? 'Remove from your orbit' : 'Keep in your orbit';
      st.addEventListener('click', function () { emit('cmd27:orbit-toggle', { symbol: r.symbol }); st.textContent = st.textContent === '★' ? '☆' : '★'; });
      list.appendChild(li);
    });
  });
  var x = $('#cmd27-sector-close'); if (x) x.addEventListener('click', function () { emit('cmd27:sector-close'); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && focused) emit('cmd27:sector-close'); });
})();

/* ── Game Plan · Your money today · Watchlist signals ───────────────────── */
(function () {
  'use strict';
  var root = document.querySelector('.cmd27'); if (!root) return;
  function $(s, el) { return (el || document).querySelector(s); }
  function num(v) { return typeof v === 'number' && isFinite(v); }
  function pct(v) { return num(v) ? (v > 0 ? '+' : '') + v.toFixed(2) + '%' : '—'; }
  function money(v, signed) {
    if (!num(v)) return '—';
    var a = Math.abs(v), s = a >= 1e6 ? '$' + (a / 1e6).toFixed(2) + 'M' : '$' + a.toLocaleString('en-US', { minimumFractionDigits: a < 1000 ? 2 : 0, maximumFractionDigits: a < 1000 ? 2 : 0 });
    return (signed ? (v > 0 ? '+' : (v < 0 ? '−' : '')) : (v < 0 ? '−' : '')) + s;
  }
  function tone(el, v) { if (!el) return; el.classList.toggle('is-up', num(v) && v > 0.004); el.classList.toggle('is-down', num(v) && v < -0.004); }
  function get(url) { return fetch(url, { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }); }
  function esc(t) { var d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; }

  // ── Game Plan ──
  var WORD = { 'RISK-ON': 'Risk-On', 'CAUTION': 'Caution', 'RISK-OFF': 'Risk-Off' };
  function plan() {
    get('/api/command/gameplan').then(function (d) {
      var box = $('#gp-verdict'); if (!box) return;
      if (!d || !d.ok) { box.className = 'cmd27-verdict is-pending'; $('#gp-word').textContent = 'No read'; $('#gp-rule').textContent = 'Market data is unavailable right now — the check will retry.'; return; }
      box.className = 'cmd27-verdict is-' + d.verdict.toLowerCase();
      $('#gp-word').textContent = WORD[d.verdict] || d.verdict;
      $('#gp-rule').textContent = d.rule;
      box.title = (d.reasons || []).join('\n');
      function trendText(x) {
        if (!x || !x.available) return '—';
        var st = x.streak, run = Math.abs(st) >= 3 ? ' · ' + Math.abs(st) + (st > 0 ? ' up' : ' down') + ' days' : '';
        return x.trend + (num(x.off_high_pct) && x.off_high_pct < -2 ? ' · ' + x.off_high_pct.toFixed(1) + '% off high' : '') + run;
      }
      function mark(sym, state) { var li = root.querySelector('[data-check="' + sym + '"]'); if (li) li.setAttribute('data-state', state); }
      $('#gp-spy').textContent = trendText(d.spy); mark('SPY', d.spy && d.spy.trend);
      $('#gp-qqq').textContent = trendText(d.qqq); mark('QQQ', d.qqq && d.qqq.trend);
      if (d.vix && num(d.vix.level)) {
        $('#gp-vix').textContent = d.vix.label + (num(d.vix.day_pct) ? ' · ' + pct(d.vix.day_pct) + ' today' : '');
        var ve = $('#cc-vix'); if (ve && ve.textContent.trim() === '—') ve.textContent = d.vix.level.toFixed(2);
        mark('VIX', { low: 'bullish', caution: 'neutral', elevated: 'neutral', high: 'bearish', extreme: 'bearish' }[d.vix.zone] || 'neutral');
      }
      if (d.dxy && d.dxy.available) {
        $('#gp-dxy').textContent = d.dxy.direction + ' · ' + d.dxy.level.toFixed(2);
        var dc = $('#gp-dxy-chg'); dc.textContent = pct(d.dxy.week_pct) + ' wk'; tone(dc, -d.dxy.week_pct);
        mark('DXY', { falling: 'bullish', flat: 'neutral', rising: 'neutral', surging: 'bearish' }[d.dxy.direction] || 'neutral');
      } else { $('#gp-dxy').textContent = 'unavailable'; }
      var lv = $('#gp-levels');
      if (lv && d.levels && d.levels.length) {
        lv.hidden = false;
        lv.innerHTML = '<span>SPY levels</span>' + d.levels.map(function (l) { return '<b title="' + esc(l.label) + '">' + esc(l.label) + ' <i>' + l.price.toFixed(2) + '</i></b>'; }).join('');
      }
    });
  }
  plan(); setInterval(function () { if (!document.hidden) plan(); }, 5 * 60 * 1000);

  // Countdown to the next catalyst ("8:30 AM ET" on an ISO date), in ET.
  var nx = $('#gp-next');
  function etOffsetHours(d) {
    try {
      var s = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', timeZoneName: 'shortOffset' }).formatToParts(d)
        .filter(function (p) { return p.type === 'timeZoneName'; })[0].value;   // "GMT-5"
      return parseInt(s.replace('GMT', ''), 10) || -5;
    } catch (e) { return -5; }
  }
  function tick() {
    if (!nx) return;
    var ds = nx.getAttribute('data-date'), ts = nx.getAttribute('data-time') || '';
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ds); if (!m) return;
    var hm = /(\d{1,2}):(\d{2})\s*(AM|PM)/i.exec(ts), h = 9, mi = 30;
    if (hm) { h = (+hm[1] % 12) + (/pm/i.test(hm[3]) ? 12 : 0); mi = +hm[2]; } else if (/after/i.test(ts)) { h = 16; mi = 5; } else if (/before/i.test(ts)) { h = 8; mi = 0; }
    var guess = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], h, mi));
    var at = new Date(guess.getTime() - etOffsetHours(guess) * 3600e3);
    var left = at - Date.now(), el = $('#gp-countdown');
    if (left <= 0) { el.textContent = 'now'; return; }
    var dd = Math.floor(left / 864e5), hh = Math.floor(left % 864e5 / 36e5), mm = Math.floor(left % 36e5 / 6e4);
    el.textContent = 'in ' + (dd ? dd + 'd ' + hh + 'h' : hh ? hh + 'h ' + mm + 'm' : mm + 'm');
  }
  tick(); setInterval(tick, 30000);

  // ── Your money today ──
  var strip = $('#cmd27-money'), holdings = [];
  var LAST = 'cmd27.money.last';
  function setTile(key, v, sub, toneVal, label) {
    var t = strip && strip.querySelector('[data-m="' + key + '"]'); if (!t) return;
    t.querySelector('[data-v]').textContent = v;
    if (sub != null) t.querySelector('[data-s]').innerHTML = sub;
    if (label) t.querySelector('[data-l]').textContent = label;
    t.classList.toggle('is-up', num(toneVal) && toneVal > 0.004); t.classList.toggle('is-down', num(toneVal) && toneVal < -0.004);
  }
  function loadMoney() {
    if (!strip) return;
    get('/api/wealth/today').then(function (d) {
      strip.classList.remove('is-loading');
      if (!d || !d.ok) { setTile('value', '—', 'Wealth data unavailable right now'); return; }
      if (d.empty) { strip.classList.add('is-empty'); strip.querySelector('.cmd27-money__empty').hidden = false; return; }
      holdings = d.tickers || [];
      document.dispatchEvent(new CustomEvent('cmd27:holdings', { detail: holdings }));
      // Since you last looked (this browser).
      var since = '';
      try {
        var prev = JSON.parse(localStorage.getItem(LAST) || 'null');
        if (prev && num(prev.v) && num(d.value) && Date.now() - prev.t > 20 * 60 * 1000) {
          var diff = d.value - prev.v, when = new Date(prev.t);
          since = '<span class="' + (diff > 0 ? 'is-up' : diff < 0 ? 'is-down' : '') + '">' + money(diff, true) + '</span> since ' +
            when.toLocaleDateString('en-US', { weekday: 'short' }) + ' ' + when.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
        }
        window.__cmd27Money = d.value;
      } catch (e) {}
      setTile('value', money(d.value), since || (d.positions + ' positions' + (d.source === 'schwab' ? ' · Schwab' : '')));
      setTile('day', money(d.day_dollars, true), pct(d.day_pct), d.day_dollars,
        d.day_label ? d.day_label + ' close' : (d.session === 'regular' ? 'Today · live' : 'Today'));
      setTile('cash', money(d.cash), null, null);
      if (d.best) setTile('best', d.best.ticker, pct(d.best.pct) + ' · ' + money(d.best.dollars, true), d.best.pct);
      if (d.worst) setTile('worst', d.worst.ticker, pct(d.worst.pct) + ' · ' + money(d.worst.dollars, true), d.worst.pct);
    });
  }
  // Remember what you saw when you leave, for "since you last looked".
  document.addEventListener('visibilitychange', function () {
    if (document.hidden && num(window.__cmd27Money)) { try { localStorage.setItem(LAST, JSON.stringify({ v: window.__cmd27Money, t: Date.now() })); } catch (e) {} }
  });
  window.addEventListener('pagehide', function () {
    if (num(window.__cmd27Money)) { try { localStorage.setItem(LAST, JSON.stringify({ v: window.__cmd27Money, t: Date.now() })); } catch (e) {} }
  });
  loadMoney(); setInterval(function () { if (!document.hidden) loadMoney(); }, 60000);

  // ── Watchlist signals ──
  var sig = $('#cmd27-signals'), orbitT = [];
  document.addEventListener('cmd27:orbit', function (e) { orbitT = (e.detail && e.detail.tickers) || []; });
  var ZSHORT = { load: 'LOAD', accumulate: 'ACCUM', fair: 'FAIR', pricey: 'PRICEY' };
  function sigTickers() {
    var base = ((sig && sig.getAttribute('data-tickers')) || '').split(',');
    var all = holdings.concat(orbitT, base).map(function (t) { return String(t || '').trim().toUpperCase(); }).filter(Boolean);
    return all.filter(function (t, i) { return all.indexOf(t) === i; }).slice(0, 40);
  }
  var sigTimer = null;
  function loadSignals() {
    if (!sig) return;
    var ts = sigTickers(), thesis = sig.getAttribute('data-thesis-url') || '/thesis';
    if (ts.length) get('/api/thesis/zones?tickers=' + encodeURIComponent(ts.join(','))).then(function (d) {
      var ul = $('#sig-zones'); if (!ul) return;
      var zs = d && d.zones ? Object.keys(d.zones).map(function (k) { return d.zones[k]; }).filter(function (z) { return z && z.available; }) : [];
      var buy = zs.filter(function (z) { return z.zone === 'load' || z.zone === 'accumulate'; })
        .sort(function (a, b) { return (a.zone === 'load' ? 0 : 1) - (b.zone === 'load' ? 0 : 1) || (a.price / a.accum_top) - (b.price / b.accum_top); });
      var near = zs.filter(function (z) { return z.zone === 'fair'; }).sort(function (a, b) { return a.price / a.accum_top - b.price / b.accum_top; });
      var rows = buy.slice(0, 4).map(function (z) {
        var under = (1 - z.price / z.accum_top) * 100;
        return '<li><a href="' + thesis + '?ticker=' + encodeURIComponent(z.ticker) + '"><b>' + esc(z.ticker) + '</b><i class="zc z-' + z.zone + '">' + ZSHORT[z.zone] + '</i><span>' +
          under.toFixed(1) + '% under base value</span></a></li>';
      });
      if (rows.length < 3) near.slice(0, 3 - rows.length).forEach(function (z) {
        var gap = (z.price / z.accum_top - 1) * 100;
        rows.push('<li class="is-near"><a href="' + thesis + '?ticker=' + encodeURIComponent(z.ticker) + '"><b>' + esc(z.ticker) + '</b><i class="zc z-fair">FAIR</i><span>' + gap.toFixed(1) + '% above the buy zone</span></a></li>');
      });
      ul.innerHTML = rows.join('') || '<li class="cc-empty">' + (d && d.pending && d.pending.length ? 'Value zones are still being worked out — open a ticker\'s Thesis once to add it.' : 'Nothing in a buy zone right now.') + '</li>';
    });
    get('/api/alerts/price').then(function (d) {
      var ul = $('#sig-alerts'); if (!ul) return;
      var al = d && d.alerts ? d.alerts : [];
      var cut = Date.now() - 3 * 864e5;
      var fired = al.filter(function (a) { return a.triggered_at && Date.parse(String(a.triggered_at).replace(' ', 'T')) > cut; });
      var armed = al.filter(function (a) { return a.enabled && !a.triggered_at; });
      var rows = fired.slice(0, 3).map(function (a) {
        return '<li class="is-fired"><a href="/stock/' + encodeURIComponent(a.ticker) + '"><b>' + esc(a.ticker) + '</b><i class="zc z-fired">HIT</i><span>' +
          (a.direction === 'above' ? 'rose to ' : 'fell to ') + Number(a.triggered_price || a.price).toFixed(2) + '</span></a></li>';
      });
      armed.slice(0, Math.max(0, 3 - rows.length)).forEach(function (a) {
        rows.push('<li><a href="/stock/' + encodeURIComponent(a.ticker) + '"><b>' + esc(a.ticker) + '</b><i class="zc">' + (a.direction === 'above' ? '▲' : '▼') + '</i><span>alert at ' + Number(a.price).toFixed(2) + '</span></a></li>');
      });
      ul.innerHTML = rows.join('') || '<li class="cc-empty">No price alerts set — draw one on any chart.</li>';
    });
  }
  function soon() { clearTimeout(sigTimer); sigTimer = setTimeout(loadSignals, 400); }
  document.addEventListener('cmd27:holdings', soon);
  document.addEventListener('cmd27:orbit', soon);
  soon(); setInterval(function () { if (!document.hidden) loadSignals(); }, 5 * 60 * 1000);
})();
