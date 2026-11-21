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

    Object.keys(INDEX).forEach(function (sym) {
      var q = by[sym]; if (!q) return;
      $$('[data-px="' + sym + '"]', root).forEach(function (el) { el.textContent = px(q.price); });
      $$('[data-chg="' + sym + '"]', root).forEach(function (el) { el.textContent = pct(q.change_pct); tone(el, q.change_pct); });
      var k = $('[data-kpi="' + sym + '"]', root);
      if (k) {
        var s = $('[data-kpi-chg]', k), p = $('[data-kpi-px]', k);
        if (num(q.change_pct)) { s.textContent = pct(q.change_pct); }
        if (p && num(q.price)) p.textContent = sym + ' ' + px(q.price);
        // For VIX, rising is the bad direction: colour by what it means.
        var v = sym === 'VIX' && num(q.change_pct) ? -q.change_pct : q.change_pct;
        tone(k, v);
      }
    });
    // Pulse card: fill SPY/QQQ % from the tape only while the server value is missing.
    ['SPY', 'QQQ'].forEach(function (sym) {
      var el = $('#cc-' + sym.toLowerCase()), q = by[sym];
      if (el && q && num(q.change_pct) && el.textContent.trim() === '—') { el.textContent = pct(q.change_pct); tone(el, q.change_pct); }
    });
    // The server-rendered SPY/QQQ % in the pulse card stay authoritative; the KPI
    // cards for SPY/QQQ fall back to them when the tape has no quote.
    ['SPY', 'QQQ'].forEach(function (sym) {
      var k = $('[data-kpi="' + sym + '"]', root);
      if (k && !by[sym]) {
        var src = $('#cc-' + sym.toLowerCase());
        var txt = src ? src.textContent.trim() : '';
        var v = parseFloat(txt);
        if (num(v)) { $('[data-kpi-chg]', k).textContent = txt; tone(k, v); }
      }
    });

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
