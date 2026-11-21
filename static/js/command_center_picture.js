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

    // Orbit: the biggest movers that are not the index funds.
    var orbit = $('#cmd27-orbit'); if (!orbit) return;
    var movers = items.filter(function (q) { return q && q.symbol && !INDEX[String(q.symbol).toUpperCase()] && num(q.change_pct); })
      .sort(function (a, b) { return Math.abs(b.change_pct) - Math.abs(a.change_pct); })
      .slice(0, window.innerWidth < 760 ? 4 : 10);
    orbit.innerHTML = '';
    var n = movers.length;
    movers.forEach(function (q, i) {
      var a = (-Math.PI / 2) + (i / Math.max(n, 1)) * Math.PI * 2 + 0.3;
      var narrow = window.innerWidth < 760;
      var x = 50 + Math.cos(a) * (narrow ? 38 : 46), y = 50 + Math.sin(a) * (narrow ? 44 : 45);
      var el = document.createElement(chartUrl ? 'a' : 'span');
      el.className = 'cmd27-chip ' + (q.change_pct < 0 ? 'is-down' : 'is-up');
      if (chartUrl) el.href = chartUrl.replace('__T__', encodeURIComponent(q.symbol));
      el.style.left = x + '%'; el.style.top = y + '%';
      el.innerHTML = '<span></span><small></small>';
      el.firstChild.textContent = q.symbol;
      el.lastChild.textContent = pct(q.change_pct);
      el.title = q.symbol + ' ' + pct(q.change_pct) + (num(q.price) ? ' · ' + px(q.price) : '');
      orbit.appendChild(el);
    });
  }

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

/* 3D scene: parallax depth layers + a live neon floor with moving light trails. */
(function () {
  'use strict';
  var stage = document.getElementById('cmd27-stage');
  var canvas = document.getElementById('cmd27-floor');
  if (!stage || !canvas || !canvas.getContext) return;
  var layers = Array.prototype.slice.call(stage.querySelectorAll('[data-depth]'));
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var ctx = canvas.getContext('2d'), W = 0, H = 0, dpr = 1;
  var px = 0, py = 0, tx = 0, ty = 0, visible = true, last = 0;
  var HZ = 0.64; // horizon, as a fraction of the scene height (matches the layer art)

  var stars = [];
  for (var i = 0; i < 140; i++) stars.push({ x: Math.random(), y: Math.random() * 0.6, r: Math.random() * 1.3 + 0.3, p: Math.random() * 6.28, s: 0.6 + Math.random() * 1.8 });

  function size() {
    var r = stage.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    W = r.width; H = r.height;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  size();
  window.addEventListener('resize', size, { passive: true });
  window.addEventListener('pointermove', function (e) {
    tx = (e.clientX / window.innerWidth) * 2 - 1; ty = (e.clientY / window.innerHeight) * 2 - 1;
  }, { passive: true });
  if ('IntersectionObserver' in window) new IntersectionObserver(function (es) { visible = es[0].isIntersecting; }).observe(stage);

  // Light trails sweeping from the near corners to the horizon (same curves as the art).
  var TRAILS = [[-150, 1020, 0.55], [250, 1120, 0.8], [2550, 1380, 0.55], [2150, 1280, 0.8]];
  function trailPoint(tr, s) {
    var k = W / 2400, hz = H * HZ;
    return [(tr[0] + (tr[1] - tr[0]) * (1 - Math.pow(1 - s, 1 + tr[2]))) * k, H + 30 - (H + 30 - hz - 6) * Math.pow(s, 0.75)];
  }

  function frame(t) {
    requestAnimationFrame(frame);
    if (!visible || document.hidden) return;
    var time = t / 1000, dt = Math.min(0.05, (t - last) / 1000 || 0); last = t;
    // Ease toward the pointer; a slow idle sway keeps phones feeling 3D.
    var sway = reduce ? 0 : Math.sin(time * 0.18) * 0.35;
    px += ((tx || sway) - px) * Math.min(1, dt * 3); py += (ty * 0.6 - py) * Math.min(1, dt * 3);
    var sc = window.scrollY || 0;
    for (var l = 0; l < layers.length; l++) {
      var d = parseFloat(layers[l].getAttribute('data-depth')) || 0;
      layers[l].style.transform = 'translate3d(' + (-px * d * 34).toFixed(2) + 'px,' + (-py * d * 10 + sc * d * 0.22).toFixed(2) + 'px,0)';
    }
    draw(reduce ? 0 : time);
  }

  function draw(time) {
    ctx.clearRect(0, 0, W, H);
    var hz = H * HZ, k = W / 2400, vx = W / 2 - px * 26 * 0.7;
    // Twinkling stars (sky sits behind this canvas, mountains in front).
    for (var i = 0; i < stars.length; i++) {
      var s = stars[i], a = 0.35 + 0.65 * Math.abs(Math.sin(time * s.s + s.p));
      ctx.fillStyle = 'rgba(200,225,255,' + (a * 0.8).toFixed(3) + ')';
      ctx.beginPath(); ctx.arc(s.x * W - px * 4, s.y * H, s.r, 0, 6.2832); ctx.fill();
    }
    ctx.save();
    ctx.beginPath(); ctx.rect(0, hz, W, H - hz); ctx.clip();
    // Converging lines.
    for (var g = -24; g <= 24; g++) {
      var x0 = vx + g * 12 * k, x1 = vx + g * 380 * k - px * 60;
      var grd = ctx.createLinearGradient(0, hz, 0, H);
      grd.addColorStop(0, 'rgba(40,170,255,0.05)'); grd.addColorStop(1, 'rgba(40,170,255,0.42)');
      ctx.strokeStyle = grd; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(x0, hz); ctx.lineTo(x1, H + 400 * k); ctx.stroke();
    }
    // Cross lines flowing toward the viewer.
    var off = (time * 0.45) % 1;
    for (var n = 0; n < 34; n++) {
      var z = n + 1 - off;
      if (z <= 0.15) continue;
      var y = hz + (H - hz) * 0.9 / z;
      if (y > H + 2 || y - hz < 1.5) continue;
      var f = (y - hz) / (H - hz);
      ctx.strokeStyle = 'rgba(40,170,255,' + (0.06 + 0.4 * f).toFixed(3) + ')';
      ctx.lineWidth = 0.8 + 1.4 * f;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
    }
    // Light trails with pulses running toward the horizon.
    for (var r = 0; r < TRAILS.length; r++) {
      var tr = TRAILS[r];
      for (var pass = 0; pass < 2; pass++) {
        ctx.strokeStyle = pass ? 'rgba(120,255,200,0.95)' : 'rgba(60,255,170,0.18)';
        ctx.lineWidth = pass ? 2.2 : 10;
        ctx.beginPath();
        for (var q = 0; q <= 60; q++) { var P = trailPoint(tr, q / 60); P[0] -= px * 50 * (1 - q / 60); if (q) ctx.lineTo(P[0], P[1]); else ctx.moveTo(P[0], P[1]); }
        ctx.stroke();
      }
      var ph = ((time * 0.35) + r * 0.27) % 1;
      var B = trailPoint(tr, ph); B[0] -= px * 50 * (1 - ph);
      var glow = ctx.createRadialGradient(B[0], B[1], 0, B[0], B[1], 26 * (1 - ph * 0.7));
      glow.addColorStop(0, 'rgba(220,255,240,0.95)'); glow.addColorStop(0.3, 'rgba(60,255,170,0.5)'); glow.addColorStop(1, 'rgba(60,255,170,0)');
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(B[0], B[1], 26, 0, 6.2832); ctx.fill();
    }
    ctx.restore();
    // Horizon glow line.
    var hg = ctx.createLinearGradient(0, 0, W, 0);
    hg.addColorStop(0, 'rgba(120,230,255,0)'); hg.addColorStop(0.5, 'rgba(120,230,255,0.9)'); hg.addColorStop(1, 'rgba(120,230,255,0)');
    ctx.fillStyle = hg; ctx.fillRect(0, hz - 1, W, 2);
  }
  if (reduce) { draw(0); } else requestAnimationFrame(frame);
})();
