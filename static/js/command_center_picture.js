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
  var editing = false, mode = 'market', held = null, flags = { alert: {}, zone: {} }, earn = {};
  var orbHost = $('[data-cc3d-orb]'), globeBox = $('.cmd27-hero__globe');
  try {
    JSON.parse((orbHost && orbHost.getAttribute('data-earnings')) || '[]').forEach(function (e) {
      var s = String(e.s || '').toUpperCase(); if (s && (!earn[s] || e.d < earn[s].d)) earn[s] = e;
    });
  } catch (e) { earn = {}; }
  // Calendar days from today (ET) to an ISO date: 0 = today.
  function daysTo(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || '')); if (!m) return null;
    var now = new Date(), et;
    try { et = now.toLocaleDateString('en-CA', { timeZone: 'America/New_York' }); } catch (e) { et = now.toISOString().slice(0, 10); }
    var a = Date.UTC(+et.slice(0, 4), +et.slice(5, 7) - 1, +et.slice(8, 10)), b = Date.UTC(+m[1], +m[2] - 1, +m[3]);
    return Math.round((b - a) / 864e5);
  }
  function earnLabel(sym) {
    var e = earn[sym]; if (!e) return '';
    var d = daysTo(e.d); if (d == null || d < 0 || d > 7) return '';
    return d === 0 ? 'TODAY' : d + 'd';
  }
  // Wide screens: the clear space between the title column and the Game Plan.
  // The globe sizes itself to it (cmd27:safe) and the chips stay inside it.
  function safeHalf() {
    if (globeBox) globeBox.style.removeProperty('--safe-r');
    if (!globeBox || globeBox.classList.contains('is-full') || window.innerWidth <= 1080) return null;
    var g = globeBox.getBoundingClientRect(), L = $('.cmd27-hero__left'), P = $('.cmd27-hero__pulse');
    if (!L || !P) return null;
    var cx = g.left + g.width / 2, l = L.getBoundingClientRect().right, r = P.getBoundingClientRect().left;
    if (!(r > l)) return null;
    // Controls on the right edge of the globe stop short of the Game Plan.
    globeBox.style.setProperty('--safe-r', Math.max(6, g.right - r + 10) + 'px');
    return Math.max(160, Math.min(cx - l, r - cx) - 6);
  }
  // Tape quotes first; orbit-only tickers (not on any watchlist, e.g. VOO)
  // fall back to the orbit's own quotes.
  var orbitQ = {};
  var SESS = { pre: 'Pre-market', post: 'After hours', overnight: 'Overnight (24H)' };
  var GLYPH = {
    overnight: '<svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M10.8 2.2a6 6 0 1 0 3 9.9A6.3 6.3 0 0 1 10.8 2.2z" fill="currentColor"/></svg>',
    post: '<svg viewBox="0 0 16 16" width="11" height="10" aria-hidden="true"><path d="M3 11a5 5 0 0 1 10 0z" fill="currentColor"/><path d="M1 13.5h14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
    pre: '<svg viewBox="0 0 16 16" width="11" height="10" aria-hidden="true"><circle cx="8" cy="9" r="3.2" fill="currentColor"/><path d="M8 2.5v1.6M3 5l1.1 1.1M13 5l-1.1 1.1M1 13.5h14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>'
  };
  function esc(t) { return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function money0(v, sign) {
    if (!num(v)) return '—';
    var a = Math.abs(v), str = a >= 1e6 ? (a / 1e6).toFixed(2) + 'M' : a >= 1e4 ? (a / 1e3).toFixed(1) + 'K' : a.toLocaleString('en-US', { maximumFractionDigits: a >= 100 ? 0 : 2 });
    return (sign ? (v > 0 ? '+' : v < 0 ? '−' : '') : (v < 0 ? '−' : '')) + '$' + str;
  }
  // Hover card for a holding: shares and cost, unrealized, buy zone, alerts.
  function holdingRows(q) {
    var sym = String(q.symbol).toUpperCase(), out = '';
    if (num(q.shares)) out += '<span class="p-row">' + (+q.shares.toFixed(4)) + ' sh' + (num(q.avg_cost) ? ' · avg $' + q.avg_cost.toFixed(2) : '') + '</span>';
    if (num(q.value)) out += '<span class="p-row">Worth ' + money0(q.value) + '</span>';
    if (num(q.unrealized)) out += '<span class="p-row">Unrealized <em class="' + (q.unrealized < 0 ? 'dn' : 'up') + '">' + money0(q.unrealized, true) + (num(q.unrealized_pct) ? ' (' + pct(q.unrealized_pct) + ')' : '') + '</em></span>';
    var z = (window.__cmd27Zones || {})[sym], px = num(q.price) ? q.price : (z && z.price);
    if (z && z.available && num(z.accum_top) && num(px)) {
      var zl = { load: 'In the LOAD zone', accumulate: 'In the buy zone' }[z.zone];
      out += '<span class="p-row">' + (zl || ((px / z.accum_top - 1) * 100).toFixed(1) + '% above the buy zone ($' + z.accum_top.toFixed(2) + ')') + '</span>';
    }
    var al = (window.__cmd27Alerts || []).filter(function (a) { return String(a.ticker).toUpperCase() === sym && a.enabled && !a.triggered_at && num(Number(a.price)); });
    if (al.length && num(px)) {
      var below = al.filter(function (a) { return a.price < px; }).sort(function (a, b) { return b.price - a.price; })[0];
      var above = al.filter(function (a) { return a.price >= px; }).sort(function (a, b) { return a.price - b.price; })[0];
      if (below) out += '<span class="p-row">Alert ↓ $' + Number(below.price).toFixed(2) + ' <em class="dn">' + pct((below.price / px - 1) * 100) + '</em></span>';
      if (above) out += '<span class="p-row">Alert ↑ $' + Number(above.price).toFixed(2) + ' <em class="up">' + pct((above.price / px - 1) * 100) + '</em></span>';
    }
    return out ? '<span class="p-sep"></span>' + out : '';
  }
  function show24h() { return document.documentElement.getAttribute('data-show-24h') === '1'; }
  document.addEventListener('tradestaar:prefs', function () { renderOrbit(); });
  function bySym() {
    var m = {};
    Object.keys(orbitQ).forEach(function (k) { m[k] = orbitQ[k]; });
    lastItems.forEach(function (q) { if (q && q.symbol && (num(q.change_pct) || num(q.ext_pct))) m[String(q.symbol).toUpperCase()] = q; });
    return m;
  }
  var oqT = null;
  function loadOrbitQuotes() {
    clearTimeout(oqT);
    if (!orbit.length) return;
    var tape = {}; lastItems.forEach(function (q) { if (q && q.symbol && (num(q.change_pct) || num(q.ext_pct))) tape[String(q.symbol).toUpperCase()] = 1; });
    if (orbit.every(function (s) { return tape[s]; })) { oqT = setTimeout(loadOrbitQuotes, 60000); return; }
    fetch('/api/command/orbit?quotes=1', { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.ok && Array.isArray(d.quotes)) {
          d.quotes.forEach(function (q) { if (q && q.symbol) orbitQ[String(q.symbol).toUpperCase()] = q; });
          renderOrbit();
        }
      })
      .catch(function () {})
      .then(function () { oqT = setTimeout(loadOrbitQuotes, document.hidden ? 180000 : 60000); });
  }
  function announce() { document.dispatchEvent(new CustomEvent('cmd27:orbit', { detail: { tickers: orbit.slice() } })); }
  function saveOrbit() {
    announce(); renderOrbit();
    fetch('/api/command/orbit', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tickers: orbit }) })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.ok) { orbit = d.tickers; announce(); renderOrbit(); loadOrbitQuotes(); } })
      .catch(function () {});
  }
  function toggle(sym) {
    sym = String(sym || '').toUpperCase(); if (!sym) return;
    var i = orbit.indexOf(sym);
    if (i >= 0) orbit.splice(i, 1); else { if (orbit.length >= 12) orbit.shift(); orbit.push(sym); }
    saveOrbit();
  }
  document.addEventListener('cmd27:orbit-toggle', function (e) { toggle(e.detail && e.detail.symbol); });
  // Right-click menu: put a ticker in (on) or take it out of (on=false) the orbit, or start over.
  document.addEventListener('cmd27:orbit-set', function (e) {
    var d = e.detail || {}, sym = String(d.symbol || '').toUpperCase();
    if (!sym || (orbit.indexOf(sym) >= 0) === (d.on !== false)) return;
    toggle(sym);
  });
  document.addEventListener('cmd27:orbit-reset', function () { orbit = []; editing = false; saveOrbit(); });
  document.addEventListener('cmd27:orbit-edit', function () { if (orbit.length && mode !== 'portfolio') { editing = !editing; renderOrbit(); } });

  function renderOrbit() {
    if (!orbitEl) return;
    var m = bySym(), narrow = window.innerWidth < 760, list, mine = orbit.length > 0 && mode !== 'portfolio';
    var port = mode === 'portfolio' && held && held.length;
    if (port) list = held.map(function (h) {
      return { symbol: h.ticker, change_pct: h.pct, weight: h.weight, price: h.price, dollars: h.dollars, value: h.value,
               ext: h.ext, ext_pct: h.ext_pct, ext_price: h.ext_price, ext_dollars: h.ext_dollars,
               shares: h.shares, avg_cost: h.avg_cost, unrealized: h.unrealized, unrealized_pct: h.unrealized_pct };
    });
    else if (mine) list = orbit.map(function (s) { return m[s] || { symbol: s }; });
    else list = lastItems.filter(function (q) { return q && q.symbol && !INDEX[String(q.symbol).toUpperCase()] && num(q.change_pct) && !/[=\/^]/.test(q.symbol) && !/^(ES|NQ|YM|RTY|CL|GC|SI|NG|ZB|ZN|DXY|VIX)$/i.test(q.symbol); })
      .sort(function (a, b) { return Math.abs(b.change_pct) - Math.abs(a.change_pct); });
    list = list.slice(0, narrow ? (mine ? 8 : 4) : (mine ? 12 : 10));
    orbitEl.innerHTML = '';
    var n = list.length;
    // Ring sized from the globe itself (same maths as the globe renderer),
    // so chips always circle outside the Earth whatever the screen.
    var box = orbitEl.getBoundingClientRect(), bw = box.width || 600, bh = box.height || 500;
    // The globe box hangs a little outside the hero panel (negative margins),
    // and the panel hides what pokes out. Keep chips and the view switch
    // inside the part of the box that is actually visible.
    var clipT = 0, clipB = 0, hero = globeBox && globeBox.closest('.cmd27-hero');
    if (hero && !(globeBox.classList.contains('is-full')) && getComputedStyle(hero).overflowY !== 'visible') {
      var hr = hero.getBoundingClientRect();
      // On phones the hero is a layout-less wrapper (0×0); it clips nothing.
      if (hr.height > 0) { clipT = Math.max(0, Math.ceil(hr.top - box.top)); clipB = Math.max(0, Math.ceil(box.bottom - hr.bottom)); }
    }
    if (globeBox) globeBox.style.setProperty('--clip-t', clipT + 'px');
    var half = safeHalf();
    var mn = Math.min(bw, bh), R0 = mn * (mn > 420 ? 0.35 : 0.33);
    R0 = half ? Math.min(R0, (half - 70) / 1.053) : Math.min(R0, bw * 0.30);
    R0 = Math.min(R0, (bh * 0.47 - 16) / 1.22);   // tallest spike stays inside the frame
    var RS = R0 * 1.053, cyc = bh * 0.47;
    var ry = Math.min(bh * 0.5 - 24, RS + 40), rx = Math.max(RS + 60, ry * 1.3);
    if (half) rx = Math.min(rx, half - 40); else rx = Math.min(rx, bw * 0.5 - 40);
    var rects = [], clear = RS + 14, GAP = 6;
    // Things a chip must never cover: the view switch, legend and zoom buttons.
    var obst = ['.cmd27-mode', '.cmd27-legend', '.cmd27-zoom'].map(function (sel) {
      var o = globeBox && globeBox.querySelector(sel); if (!o) return null;
      var r = o.getBoundingClientRect(); if (!r.width || !r.height) return null;
      return [r.left - box.left - GAP, r.top - box.top - GAP, r.right - box.left + GAP, r.bottom - box.top + GAP];
    }).filter(Boolean);
    // The globe keeps its signs inside the clear space and off these controls.
    document.dispatchEvent(new CustomEvent('cmd27:safe', { detail: { half: half, obst: obst } }));
    function hit(b) {
      var all = obst.concat(rects);
      for (var q = 0; q < all.length; q++) { var o = all[q]; if (b[0] < o[2] + GAP && b[2] + GAP > o[0] && b[1] < o[3] + GAP && b[3] + GAP > o[1]) return true; }
      return false;
    }
    var els = [];
    list.forEach(function (q, i) {
      var sym = String(q.symbol).toUpperCase(), chg = q.change_pct;
      var el = document.createElement('div');
      var fl = flags.alert[sym] ? 'alert' : (flags.zone[sym] ? 'zone' : ''), ek = earnLabel(sym);
      el.className = 'cmd27-chip ' + (num(chg) ? (chg < 0 ? 'is-down' : 'is-up') : 'is-flat') + (mine ? ' is-mine' : '') +
        (fl ? ' is-' + fl : '') + (num(q.weight) ? ' is-hold' : '');
      el.setAttribute('role', 'button'); el.tabIndex = 0;
      el.setAttribute('data-ticker', sym); el.setAttribute('data-orbit', orbit.indexOf(sym) >= 0 ? '1' : '0');
      if (num(q.price)) el.setAttribute('data-price', String(q.price));
      if (m[sym] || mode === 'portfolio') el.setAttribute('data-spin', '1');
      el.style.left = '0px'; el.style.top = '0px'; el.style.visibility = 'hidden';
      el.innerHTML = '<span></span><small></small>';
      // One number per chip. With "24H prices" on (avatar menu), a stock
      // trading pre-market / after hours / overnight shows that move, labelled;
      // otherwise the last regular-session move. No day's move at all → the
      // extended one, labelled, rather than a dash.
      var xl = { pre: 'PRE', post: 'AH', overnight: '24H' }[q.ext], xp = q.ext_pct;
      var useX = !!(xl && num(xp) && (show24h() || !num(chg))), shown = useX ? xp : chg;
      el.className = el.className.replace(/\bis-(down|up|flat)\b/, num(shown) ? (shown < 0 ? 'is-down' : 'is-up') : 'is-flat');
      el.firstChild.textContent = sym;
      // Compact capsule: ticker · session glyph · %. A hairline under it
      // grows with the size of the move (full width at 10%). Price, dollar
      // change and the other session open in a small card on hover / focus.
      var small = el.lastChild;
      if (useX) { var g = document.createElement('i'); g.className = 'cmd27-chip__sess is-' + q.ext; g.setAttribute('aria-label', SESS[q.ext]); g.innerHTML = GLYPH[q.ext] || ''; small.appendChild(g); }
      small.appendChild(document.createTextNode(pct(shown)));
      el.style.setProperty('--mag', num(shown) ? Math.min(1, Math.abs(shown) / 10).toFixed(3) : '0');
      var px = useX ? q.ext_price : q.price, pop = document.createElement('b');
      pop.className = 'cmd27-chip__pop';
      var rows = '';
      if (num(px) && px > 0) {
        var d$ = num(shown) ? px - px / (1 + shown / 100) : null;
        rows += '<span class="p-px">$' + px.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '</span>' +
          (num(d$) ? '<span class="p-d ' + (d$ < 0 ? 'dn' : 'up') + '">' + (d$ > 0 ? '+' : d$ < 0 ? '−' : '') + Math.abs(d$).toFixed(2) + '</span>' : '');
      }
      if (useX && num(chg)) rows += '<span class="p-alt">Last session <em class="' + (chg < 0 ? 'dn' : 'up') + '">' + pct(chg) + '</em></span>';
      else if (!useX && xl && num(xp)) rows += '<span class="p-alt">' + SESS[q.ext] + ' <em class="' + (xp < 0 ? 'dn' : 'up') + '">' + pct(xp) + '</em></span>';
      if (useX) rows += '<span class="p-sess">' + SESS[q.ext] + '</span>';
      if (rows) { pop.innerHTML = rows; el.appendChild(pop); }
      if (num(q.weight)) {
        // Holdings: what the move did to your money, then your weight.
        var usd = useX ? q.ext_dollars : q.dollars;
        if (num(usd)) { var u = document.createElement('b'); u.className = 'cmd27-chip__usd ' + (usd < 0 ? 'is-dn' : 'is-up'); u.textContent = money0(usd, true); el.insertBefore(u, pop.parentNode ? pop : null); }
        var w = document.createElement('em'); w.className = 'cmd27-chip__w'; w.textContent = q.weight.toFixed(q.weight < 10 ? 1 : 0) + '%'; w.title = 'of your portfolio';
        el.insertBefore(w, pop.parentNode ? pop : null);
        if (!pop.parentNode) { pop.innerHTML = ''; el.appendChild(pop); }
        pop.innerHTML += holdingRows(q);
      }
      var tagTxt = fl === 'alert' ? 'HIT' : (fl === 'zone' ? flags.zone[sym] : ek);
      if (tagTxt) {
        var tg = document.createElement('i'); tg.className = 'cmd27-chip__tag' + (fl ? ' is-' + fl : ''); tg.textContent = tagTxt; el.appendChild(tg);
      }
      el.title = (m[sym] || mode === 'portfolio' ? 'Spin the globe to ' : 'Open the chart for ') + sym +
        (useX && num(chg) ? ' · last session ' + pct(chg) : '') +
        (!useX && xl && num(xp) ? ' · ' + ({ PRE: 'pre-market', AH: 'after hours', '24H': 'overnight (24H)' })[xl] + ' ' + pct(xp) + ' (turn on 24H prices in your profile menu to show it)' : '') +
        (fl === 'alert' ? ' · price alert hit' : fl === 'zone' ? ' · in its buy zone' : '') + (ek ? ' · reports ' + (ek === 'TODAY' ? 'today' : 'in ' + ek) : '');
      function go() {
        if (m[sym] || mode === 'portfolio') document.dispatchEvent(new CustomEvent('cmd27:focus', { detail: { symbol: sym } }));
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
      els.push(el);
    });
    // Place each chip at its real size: fully inside the frame (tags included),
    // outside the atmosphere, and clear of the controls and the other chips.
    // My Portfolio: an allocation ring just outside the atmosphere, one arc
    // per holding sized by its weight and coloured by its move; each chip
    // sits beside its own slice.
    var mids = null;
    if (port) {
      var tot = 0; list.forEach(function (q) { tot += num(q.weight) ? q.weight : 0; });
      var full = Math.max(tot, 100), acc = 0, rR = RS + 9, ccx = bw / 2, arcs = '';
      mids = list.map(function (q) {
        var w = num(q.weight) ? q.weight : 0, a0 = -Math.PI / 2 + acc / full * Math.PI * 2, a1 = -Math.PI / 2 + (acc + w) / full * Math.PI * 2;
        acc += w;
        var g = Math.min(0.025, (a1 - a0) * 0.2), b0 = a0 + g, b1 = a1 - g;
        if (b1 > b0) {
          var xp0 = q.ext && num(q.ext_pct) && show24h() ? q.ext_pct : q.change_pct;
          var col = !num(xp0) ? 'rgba(160,175,200,.55)' : xp0 < 0 ? 'rgba(255,84,112,' + (0.45 + Math.min(1, Math.abs(xp0) / 8) * 0.5).toFixed(2) + ')' : 'rgba(34,227,154,' + (0.45 + Math.min(1, Math.abs(xp0) / 8) * 0.5).toFixed(2) + ')';
          arcs += '<path d="M' + (ccx + rR * Math.cos(b0)).toFixed(1) + ' ' + (cyc + rR * Math.sin(b0)).toFixed(1) + ' A' + rR.toFixed(1) + ' ' + rR.toFixed(1) + ' 0 ' + (b1 - b0 > Math.PI ? 1 : 0) + ' 1 ' +
            (ccx + rR * Math.cos(b1)).toFixed(1) + ' ' + (cyc + rR * Math.sin(b1)).toFixed(1) + '" stroke="' + col + '" style="color:' + col + '"><title>' + esc(q.symbol) + ' ' + q.weight.toFixed(1) + '% of your portfolio</title></path>';
        }
        return (a0 + a1) / 2;
      });
      if (acc < full - 0.5) {
        var o0 = -Math.PI / 2 + acc / full * Math.PI * 2 + 0.02, o1 = Math.PI * 1.5 - 0.02;
        if (o1 > o0) arcs += '<path class="is-rest" d="M' + (ccx + rR * Math.cos(o0)).toFixed(1) + ' ' + (cyc + rR * Math.sin(o0)).toFixed(1) + ' A' + rR.toFixed(1) + ' ' + rR.toFixed(1) + ' 0 ' + (o1 - o0 > Math.PI ? 1 : 0) + ' 1 ' + (ccx + rR * Math.cos(o1)).toFixed(1) + ' ' + (cyc + rR * Math.sin(o1)).toFixed(1) + '"><title>Other holdings</title></path>';
      }
      var ring = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      ring.setAttribute('class', 'cmd27-alloc'); ring.setAttribute('viewBox', '0 0 ' + bw + ' ' + bh); ring.setAttribute('aria-hidden', 'true');
      ring.innerHTML = arcs;
      orbitEl.insertBefore(ring, orbitEl.firstChild);
    }
    els.forEach(function (el, i) {
      var hw = el.offsetWidth / 2 + 6, hh = el.offsetHeight / 2 + 6;   // +6: the ★ and tag badges stick out
      var base = mids && !narrow ? mids[i] : (-Math.PI / 2) + (i / Math.max(n, 1)) * Math.PI * 2 + 0.3, pick = null, first = null;
      for (var t = 0; t < 13 && !pick; t++) {
        var a = base + (t === 0 ? 0 : (t % 2 ? 1 : -1) * Math.ceil(t / 2) * 0.13);
        var dx = Math.cos(a) * rx, dy = Math.sin(a) * ry;
        for (var k = 0; k < 14; k++) {   // push out until the nearest corner clears the atmosphere
          var nx = Math.max(0, Math.abs(dx) - hw), ny = Math.max(0, Math.abs(dy) - hh);
          if (Math.sqrt(nx * nx + ny * ny) >= clear) break;
          dx *= 1.06; dy *= 1.06;
        }
        var lim = Math.min(bw / 2 - hw - 2, half ? half - hw : Infinity);
        dx = Math.max(-lim, Math.min(lim, dx));
        dy = Math.max(-cyc + hh + 2 + clipT, Math.min(bh - cyc - hh - 2 - clipB, dy));
        var r = [bw / 2 + dx - hw, cyc + dy - hh, bw / 2 + dx + hw, cyc + dy + hh];
        if (!first) first = r;
        if (!hit(r)) pick = r;
      }
      pick = pick || first;
      rects.push(pick);
      // Chips in the lower part of the frame open their card upward.
      el.classList.toggle('is-low', (pick[1] + pick[3]) / 2 > bh * 0.58);
      el.style.left = ((pick[0] + pick[2]) / 2 / bw * 100) + '%';
      el.style.top = ((pick[1] + pick[3]) / 2 / bh * 100) + '%';
      el.style.visibility = '';
    });
    document.dispatchEvent(new CustomEvent('cmd27:chips', { detail: { tickers: list.map(function (q) { return String(q.symbol).toUpperCase(); }), rects: rects } }));
    orbitEl.classList.toggle('is-editing', editing && mine);
    if (bar) {
      var lbl = $('#cmd27-orbit-label', bar), reset = $('#cmd27-orbit-reset', bar), edit = $('#cmd27-orbit-edit', bar);
      lbl.textContent = mode === 'portfolio' ? 'My Portfolio · top ' + n + ' by value' : (mine ? 'Your orbit · ' + orbit.length + '/12' : 'Orbit · top movers');
      if (!mine && editing) editing = false;
      reset.hidden = !(mine && editing);
      if (edit) { edit.hidden = !mine; edit.textContent = editing ? 'Done' : 'Edit'; edit.setAttribute('aria-pressed', editing ? 'true' : 'false'); }
      $('#cmd27-orbit-hint', bar).hidden = mine || mode === 'portfolio';
    }
  }
  document.addEventListener('cmd27:flags', function (e) { if (e.detail) { flags = e.detail; renderOrbit(); } });
  document.addEventListener('cmd27:portfolio-data', function (e) { held = (e.detail || []).slice(0, 10); if (mode === 'portfolio') renderOrbit(); });
  document.addEventListener('cmd27:mode', function (e) { mode = (e.detail && e.detail.mode) || 'market'; editing = false; renderOrbit(); });
  document.addEventListener('cmd27:relayout', function () { renderOrbit(); });
  if (bar) {
    var form = $('#cmd27-orbit-form', bar);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var inp = $('input', form), v = (inp.value || '').trim().toUpperCase();
      if (/^[A-Z0-9][A-Z0-9.\-]{0,9}$/.test(v) && orbit.indexOf(v) < 0) { toggle(v); }
      inp.value = '';
    });
    $('#cmd27-orbit-reset', bar).addEventListener('click', function () { orbit = []; editing = false; saveOrbit(); });
    var editBtn = $('#cmd27-orbit-edit', bar);
    if (editBtn) editBtn.addEventListener('click', function () { editing = !editing; renderOrbit(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && editing) { editing = false; renderOrbit(); } });
  }
  var rto = null;
  window.addEventListener('resize', function () { clearTimeout(rto); rto = setTimeout(renderOrbit, 150); }, { passive: true });
  fetch('/api/command/orbit', { credentials: 'same-origin', cache: 'no-store' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { if (d && d.ok) { orbit = d.tickers || []; orbitLoaded = true; announce(); renderOrbit(); setTimeout(loadOrbitQuotes, 1500); } })
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
      // 24H prices on: the sector's overnight / after-hours average, with a
      // moon, and how many of its stocks are up and down on that move.
      if (r.pm && typeof r.value === 'number') {
        // My Portfolio: your money in the sector, and its share of the whole.
        var av = Math.abs(r.value), vs = av >= 1e6 ? (av / 1e6).toFixed(2) + 'M' : av >= 1e3 ? (av / 1e3).toFixed(1) + 'K' : av.toFixed(0);
        b.lastChild.textContent = '$' + vs + ' · ' + (r.w >= 10 ? r.w.toFixed(0) : r.w.toFixed(1)) + '%';
        b.title = r.name + ': $' + Math.round(r.value).toLocaleString('en-US') + ' of your money (' + r.w.toFixed(1) + '%), ' + pct(r.avg) + (r.ext ? ' on the 24H move' : ' today');
        if (r.ext) b.classList.add('is-24h');
      } else if (r.ext) {
        b.classList.add('is-24h');
        b.lastChild.innerHTML = '<i class="cmd27-dock__moon" aria-hidden="true"><svg viewBox="0 0 16 16" width="9" height="9"><path d="M10.8 2.2a6 6 0 1 0 3 9.9A6.3 6.3 0 0 1 10.8 2.2z" fill="currentColor"/></svg></i>';
        b.lastChild.appendChild(document.createTextNode(pct(r.avg) + ' · ▲' + r.up + ' ▼' + r.dn));
        b.title = r.name + ': average 24H / extended-hours move of ' + r.n + ' stocks (' + r.ext + ' traded since the close)';
      } else {
        b.lastChild.textContent = pct(r.avg) + ' · ' + r.n;
      }
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
    var avg = $('#cmd27-sector-avg'); avg.textContent = (d.ext ? '24H ' : '') + pct(d.avg) + ' avg'; avg.className = tone(d.avg);
    list.innerHTML = '';
    d.rows.forEach(function (r, i) {
      var li = document.createElement('li');
      li.innerHTML = '<button type="button" class="cmd27-sector__go"><i></i><b></b><span></span><em></em></button><button type="button" class="cmd27-sector__star"></button>';
      var go = li.firstChild, st = li.lastChild;
      go.children[0].textContent = i + 1;
      go.children[1].textContent = r.symbol;
      // Price and dollar change for the move shown (24H when that is on).
      var pv = r.price != null ? Number(r.price) : null, dv = (pv && typeof r.chg === 'number') ? pv - pv / (1 + r.chg / 100) : null;
      go.children[2].textContent = pv != null ? '$' + pv.toFixed(2) : '';
      if (dv != null) { var dd = document.createElement('small'); dd.className = 'cmd27-sector__d ' + tone(r.chg); dd.textContent = (dv > 0 ? ' +' : dv < 0 ? ' −' : ' ') + Math.abs(dv).toFixed(2); go.children[2].appendChild(dd); }
      go.children[3].textContent = (r.session ? ({ pre: 'PRE ', post: 'AH ', overnight: '24H ' })[r.session] || '' : '') + pct(r.chg); go.children[3].className = tone(r.chg);
      go.addEventListener('click', function () { emit('cmd27:focus', { symbol: r.symbol }); });
      st.textContent = orbitSet[r.symbol] ? '★' : '☆';
      st.title = orbitSet[r.symbol] ? 'Remove from your orbit' : 'Keep in your orbit';
      st.addEventListener('click', function () { emit('cmd27:orbit-toggle', { symbol: r.symbol }); st.textContent = st.textContent === '★' ? '☆' : '★'; });
      list.appendChild(li);
    });
  });
  var x = $('#cmd27-sector-close'); if (x) x.addEventListener('click', function () { emit('cmd27:sector-close'); });
  var back = $('#cmd27-back'); if (back) back.addEventListener('click', function () { emit('cmd27:sector-close'); });
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
      if (d.empty) {
        strip.classList.add('is-empty'); strip.querySelector('.cmd27-money__empty').hidden = false;
        document.dispatchEvent(new CustomEvent('cmd27:portfolio-data', { detail: [] }));
        return;
      }
      holdings = d.tickers || [];
      document.dispatchEvent(new CustomEvent('cmd27:portfolio-summary', { detail: d }));
      document.dispatchEvent(new CustomEvent('cmd27:holdings', { detail: holdings }));
      document.dispatchEvent(new CustomEvent('cmd27:portfolio-data', { detail: d.holdings || [] }));
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
  // Alert hits + buy-zone entries light up the globe: chips glow while the
  // condition holds, and a pin sends out a shockwave the first time this
  // browser sees it (remembered so a reload doesn't replay old news).
  var flagState = { alert: {}, zone: {} }, flagKeys = { alert: {}, zone: {} }, SEEN = 'cmd27.waves.seen';
  function sendFlags() {
    var seen = {}, fresh = [];
    try { seen = JSON.parse(localStorage.getItem(SEEN) || '{}') || {}; } catch (e) { seen = {}; }
    ['alert', 'zone'].forEach(function (k) {
      Object.keys(flagKeys[k]).forEach(function (sym) {
        var key = k + ':' + sym + ':' + flagKeys[k][sym];
        if (!seen[key]) { seen[key] = Date.now(); fresh.push({ symbol: sym, kind: k }); }
      });
    });
    var cut = Date.now() - 14 * 864e5;
    Object.keys(seen).forEach(function (k) { if (seen[k] < cut) delete seen[k]; });
    try { localStorage.setItem(SEEN, JSON.stringify(seen)); } catch (e) {}
    document.dispatchEvent(new CustomEvent('cmd27:flags', { detail: { alert: flagState.alert, zone: flagState.zone, fresh: fresh } }));
  }
  function loadSignals() {
    if (!sig) return;
    var ts = sigTickers(), thesis = sig.getAttribute('data-thesis-url') || '/thesis';
    if (ts.length) get('/api/thesis/zones?tickers=' + encodeURIComponent(ts.join(','))).then(function (d) {
      var ul = $('#sig-zones'); if (!ul) return;
      window.__cmd27Zones = (d && d.zones) || {};
      var zs = d && d.zones ? Object.keys(d.zones).map(function (k) { return d.zones[k]; }).filter(function (z) { return z && z.available; }) : [];
      flagState.zone = {}; flagKeys.zone = {};
      var today = new Date().toISOString().slice(0, 10);
      zs.forEach(function (z) {
        if (z.zone === 'load' || z.zone === 'accumulate') {
          var t = String(z.ticker).toUpperCase(); flagState.zone[t] = ZSHORT[z.zone]; flagKeys.zone[t] = z.zone + ':' + today;
        }
      });
      sendFlags();
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
      window.__cmd27Alerts = al;
      var cut = Date.now() - 3 * 864e5;
      var fired = al.filter(function (a) { return a.triggered_at && Date.parse(String(a.triggered_at).replace(' ', 'T')) > cut; });
      var armed = al.filter(function (a) { return a.enabled && !a.triggered_at; });
      flagState.alert = {}; flagKeys.alert = {};
      fired.forEach(function (a) { var t = String(a.ticker).toUpperCase(); flagState.alert[t] = a.direction || 'hit'; flagKeys.alert[t] = String(a.id || '') + ':' + a.triggered_at; });
      sendFlags();
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


/* ── Globe: Market / My Portfolio switch + full-screen mode ── */
(function () {
  'use strict';
  var box = document.querySelector('.cmd27-hero__globe');
  if (!box) return;
  var seg = document.getElementById('cmd27-mode'), note = document.getElementById('cmd27-mode-note');
  var fsBtn = document.getElementById('cmd27-fs'), held = null, mode = 'market';
  var KEY = 'cmd27.globe.mode';
  function emit(n, d) { document.dispatchEvent(new CustomEvent(n, { detail: d })); }
  function showNote(html) { if (!note) return; note.innerHTML = html; note.hidden = !html; }
  function items() {
    return (held || []).map(function (h) {
      return { symbol: h.ticker, price: h.price, change_pct: h.pct, value: h.value, weight: h.weight, dollars: h.dollars, sector: h.sector,
               ext: h.ext, ext_pct: h.ext_pct, ext_price: h.ext_price, ext_dollars: h.ext_dollars };
    });
  }
  function setMode(m, quiet) {
    if (m === 'portfolio' && held && !held.length) {
      showNote('No holdings yet — <a href="/wealth">add a portfolio in Wealth</a>');
      setTimeout(function () { showNote(''); }, 5000);
      m = 'market';
    }
    mode = m;
    if (seg) Array.prototype.forEach.call(seg.querySelectorAll('[data-mode]'), function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-mode') === m ? 'true' : 'false');
    });
    box.classList.toggle('is-portfolio', m === 'portfolio');
    if (m === 'portfolio' && !held) showNote('Loading your holdings…'); else if (!quiet || m === 'market') showNote('');
    emit('cmd27:portfolio', { items: m === 'portfolio' && held ? items() : null });
    emit('cmd27:mode', { mode: m });
    try { localStorage.setItem(KEY, m); } catch (e) {}
  }
  // ── My Portfolio summary card (replaces the orbit bar in this view) and
  //    the concentration warning (shown in the Game Plan whatever the view).
  var psum = document.getElementById('cmd27-psum'), obar = document.getElementById('cmd27-orbit-bar'), sumD = null;
  function nm(v) { return typeof v === 'number' && isFinite(v); }
  function usd(v, sign) {
    if (!nm(v)) return '—';
    var a = Math.abs(v), t = a.toLocaleString('en-US', { minimumFractionDigits: a >= 1000 ? 0 : 2, maximumFractionDigits: a >= 1000 ? 0 : 2 });
    return (sign ? (v > 0 ? '+' : v < 0 ? '−' : '') : (v < 0 ? '−' : '')) + '$' + t;
  }
  function pc(v) { return nm(v) ? (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) + '%' : '—'; }
  function tn(v) { return nm(v) ? (v > 0 ? 'up' : v < 0 ? 'dn' : '') : ''; }
  function e2(t) { var d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; }
  function on24() { return document.documentElement.getAttribute('data-show-24h') === '1'; }
  function concentration(d) {
    var h = (d && d.holdings) || []; if (h.length < 2) return null;
    var a = h[0], b = h[1], w1 = a.weight || 0, w2 = (a.weight || 0) + (b.weight || 0);
    if (w1 >= 30 && h.length > 1 && w2 >= 50) return { names: [a.ticker, b.ticker], w: w2, hit: ((a.value || 0) + (b.value || 0)) * 0.10 };
    if (w1 >= 30) return { names: [a.ticker], w: w1, hit: (a.value || 0) * 0.10 };
    return null;
  }
  function renderSum() {
    var d = sumD, isP = mode === 'portfolio' && d && !d.empty;
    if (obar) { obar.hidden = !!isP; obar.style.display = isP ? 'none' : ''; }
    if (psum) psum.hidden = !isP;
    var cbox = document.getElementById('gp-conc'), c = d && !d.empty ? concentration(d) : null;
    if (cbox) {
      cbox.hidden = !c;
      if (c) {
        var word = (document.getElementById('gp-word') || {}).textContent || '';
        cbox.innerHTML = '<b>⚠ Concentrated</b> ' + e2(c.names.join(' + ')) + (c.names.length > 1 ? ' are ' : ' is ') + Math.round(c.w) + '% of your money. ' +
          (/risk-off/i.test(word) ? 'With the market Risk-Off, a' : 'A') + ' 10% drop there costs about <em class="dn">' + usd(-c.hit) + '</em>.';
      }
    }
    if (!isP || !psum) return;
    var useX = on24() && d.ext_count > 0 && nm(d.ext_dollars);
    var hs = d.holdings || [], unr = 0, cost = 0, kn = 0;
    hs.forEach(function (h) { if (nm(h.unrealized) && nm(h.cost_basis)) { unr += h.unrealized; cost += h.cost_basis; kn++; } });
    var best = d.best, worst = d.worst;
    if (useX) {
      var xs = hs.filter(function (h) { return nm(h.ext_pct); }).sort(function (a, b) { return a.ext_pct - b.ext_pct; });
      if (xs.length) { worst = { ticker: xs[0].ticker, pct: xs[0].ext_pct, dollars: xs[0].ext_dollars }; best = { ticker: xs[xs.length - 1].ticker, pct: xs[xs.length - 1].ext_pct, dollars: xs[xs.length - 1].ext_dollars }; }
    }
    var html = '<header><span>My portfolio</span><small>' + (d.positions || hs.length) + ' positions' + (d.source === 'schwab' ? ' · Schwab' : '') + '</small></header>' +
      '<strong class="psum-val">' + usd(d.value) + '</strong>' +
      '<div class="psum-rows">' +
      '<div><span>' + e2(d.day_label ? d.day_label + ' close' : 'Today') + '</span><b class="' + tn(d.day_dollars) + '">' + usd(d.day_dollars, true) + ' <small>' + pc(d.day_pct) + '</small></b></div>' +
      (d.ext_count ? '<div class="' + (useX ? 'is-on' : 'is-off') + '"><span><i class="psum-moon" aria-hidden="true"></i>24H</span><b class="' + tn(d.ext_dollars) + '">' + usd(d.ext_dollars, true) + ' <small>' + pc(d.ext_pct) + '</small></b></div>' : '') +
      (kn ? '<div><span>Unrealized</span><b class="' + tn(unr) + '">' + usd(unr, true) + ' <small>' + pc(cost > 0 ? unr / cost * 100 : null) + '</small></b></div>' : '') +
      (nm(d.cash) && d.cash > 0 ? '<div><span>Cash</span><b>' + usd(d.cash) + '</b></div>' : '') +
      '</div>' +
      (best && worst && best.ticker !== worst.ticker ? '<div class="psum-bw"><span class="up">▲ ' + e2(best.ticker) + ' ' + pc(best.pct) + '</span><span class="dn">▼ ' + e2(worst.ticker) + ' ' + pc(worst.pct) + '</span></div>' : '') +
      (useX ? '<p class="psum-note">Showing overnight / extended-hours moves (24H prices is on)</p>' : '');
    psum.innerHTML = html;
  }
  document.addEventListener('cmd27:portfolio-summary', function (e) { sumD = e.detail; renderSum(); });
  (function () { var gw = document.getElementById('gp-word'); if (gw && window.MutationObserver) new MutationObserver(function () { if (sumD) renderSum(); }).observe(gw, { childList: true, characterData: true, subtree: true }); })();
  document.addEventListener('cmd27:mode', function () { renderSum(); });
  document.addEventListener('tradestaar:prefs', function () { renderSum(); });
  document.addEventListener('cmd27:portfolio-data', function (e) {
    held = e.detail || [];
    if (mode === 'portfolio') { if (!held.length) setMode('market'); else { showNote(''); emit('cmd27:portfolio', { items: items() }); } }
  });
  if (seg) seg.addEventListener('click', function (e) {
    var b = e.target.closest('[data-mode]'); if (b) setMode(b.getAttribute('data-mode'));
  });
  try { if (localStorage.getItem(KEY) === 'portfolio') setMode('portfolio', true); } catch (e) {}

  // Full screen: the globe fills the screen (the real Fullscreen API where the
  // browser has it, a fixed overlay everywhere else). Esc, F or ⛶ exits.
  var fsClock = document.getElementById('cmd27-fs-clock');
  function tickFs() {
    if (!fsClock || !box.classList.contains('is-full')) return;
    try { fsClock.textContent = new Date().toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }) + ' ET · live globe'; } catch (e) {}
  }
  setInterval(tickFs, 15000);
  function relayout() { setTimeout(function () { emit('cmd27:relayout'); window.dispatchEvent(new Event('resize')); }, 60); }
  // Full screen brings the app's 3D scene (sky, stars, mountains, neon floor)
  // in behind the globe; it goes back to the page on exit.
  var scene = document.querySelector('.ts-scene'), sceneHome = scene && scene.parentNode, sceneNext = scene && scene.nextSibling;
  function moveScene(on) {
    if (!scene) return;
    if (on) box.insertBefore(scene, box.firstChild);
    else if (sceneHome) sceneHome.insertBefore(scene, sceneNext && sceneNext.parentNode === sceneHome ? sceneNext : sceneHome.firstChild);
  }
  function mood() {
    var w = ((document.getElementById('gp-word') || {}).textContent || '').toUpperCase();
    return /RISK-OFF/.test(w) ? 'off' : /RISK-ON/.test(w) ? 'on' : /CAUTION/.test(w) ? 'caution' : 'neutral';
  }
  document.addEventListener('cmd27:sun', function (e) {
    var d = e.detail || {}; if (d.x == null) return;
    var l = Math.sqrt(d.x * d.x + d.y * d.y) || 1;
    box.style.setProperty('--sun-x', (50 + d.x / l * 46).toFixed(1) + '%');
    box.style.setProperty('--sun-y', (47 + d.y / l * 40).toFixed(1) + '%');
  });
  function setFull(on) {
    if (on === box.classList.contains('is-full')) return;
    moveScene(on);
    box.setAttribute('data-mood', mood());
    box.classList.toggle('is-full', on);
    document.documentElement.classList.toggle('cmd27-fs', on);
    if (fsBtn) { fsBtn.setAttribute('aria-pressed', on ? 'true' : 'false'); fsBtn.textContent = on ? '⤡' : '⛶';
      fsBtn.setAttribute('aria-label', on ? 'Exit full screen' : 'Full-screen globe'); }
    var req = box.requestFullscreen || box.webkitRequestFullscreen, exit = document.exitFullscreen || document.webkitExitFullscreen;
    var cur = document.fullscreenElement || document.webkitFullscreenElement;
    try {
      if (on && req && !cur) { var p = req.call(box); if (p && p.catch) p.catch(function () {}); }
      if (!on && cur && exit) { var q = exit.call(document); if (q && q.catch) q.catch(function () {}); }
    } catch (e) {}
    tickFs(); relayout();
  }
  if (fsBtn) fsBtn.addEventListener('click', function () { setFull(!box.classList.contains('is-full')); });
  function onFsChange() { if (!(document.fullscreenElement || document.webkitFullscreenElement) && box.classList.contains('is-full')) setFull(false); }
  document.addEventListener('fullscreenchange', onFsChange);
  document.addEventListener('webkitfullscreenchange', onFsChange);
  document.addEventListener('keydown', function (e) {
    var t = e.target, typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
    if (e.key === 'Escape' && box.classList.contains('is-full')) setFull(false);
    else if (!typing && !e.metaKey && !e.ctrlKey && !e.altKey && (e.key === 'f' || e.key === 'F')) setFull(!box.classList.contains('is-full'));
  });
})();
