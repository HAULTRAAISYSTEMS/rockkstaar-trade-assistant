/* =================================================================
   Tradestaar Elite — 2027 Command Center 3D layer
   -----------------------------------------------------------------
   Three pieces, all presentation only, no dependencies, no network of
   their own:

   1. Backdrop  — a fixed perspective grid + particle field behind the app.
   2. Market globe — on any page with [data-cc3d-orb]. Every node is a real
      symbol from the market tape; colour = direction, spike = size of the
      move. It reads the tape the server rendered, then listens for the
      tape's own 45s poll (event "tradestaar:tape"), so it never makes an
      extra quote request and never shows a number the tape did not have.
   3. Tilt — a light 3D tilt on cards under a fine pointer.

   Honours prefers-reduced-motion (one still frame, no tilt) and pauses
   whenever the tab is hidden.
   ================================================================= */
(function () {
  'use strict';

  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var finePointer = window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches;
  var DPR = Math.min(window.devicePixelRatio || 1, 1.5);

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }
  function hexToRgb(hex) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec((hex || '').trim());
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
  }
  function rgba(rgb, a) { return 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',' + a + ')'; }

  var CYAN = [79, 227, 255];
  // The page accent (each section of the app has its own colour).
  var GOLD = (function () {
    var v = cssVar('--cc-accent-rgb', '241, 199, 91').split(',').map(function (x) { return parseInt(x, 10); });
    return v.length === 3 && v.every(isFinite) ? v : [241, 199, 91];
  })();
  var VIOLET = [139, 124, 255];
  var POS = hexToRgb(cssVar('--pos-500', '#22C58B')) || [34, 197, 139];
  var NEG = hexToRgb(cssVar('--neg-500', '#FF4D45')) || [255, 77, 69];
  var FLAT = [174, 183, 194];

  // Shared pointer for parallax
  var pointer = { x: 0, y: 0, tx: 0, ty: 0 };
  window.addEventListener('pointermove', function (e) {
    pointer.tx = (e.clientX / window.innerWidth) * 2 - 1;
    pointer.ty = (e.clientY / window.innerHeight) * 2 - 1;
  }, { passive: true });

  // One clock for everything, ~40fps cap, paused when hidden.
  var tasks = [];
  var last = 0;
  function loop(t) {
    if (!document.hidden) {
      if (t - last > 24) {
        var dt = Math.min((t - last) / 1000, 0.05);
        last = t;
        pointer.x += (pointer.tx - pointer.x) * 0.05;
        pointer.y += (pointer.ty - pointer.y) * 0.05;
        for (var i = 0; i < tasks.length; i++) tasks[i](t / 1000, dt);
      }
    }
    if (!reduceMotion) requestAnimationFrame(loop);
  }

  function fitCanvas(canvas, w, h) {
    var cw = Math.round(w * DPR), ch = Math.round(h * DPR);
    if (canvas.width !== cw || canvas.height !== ch) { canvas.width = cw; canvas.height = ch; }
    var ctx = canvas.getContext('2d');
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    return ctx;
  }

  /* ───────────────────────── 1. Backdrop ───────────────────────── */
  function initBackdrop() {
    var canvas = document.getElementById('cc3d-backdrop');
    if (!canvas || !canvas.getContext) return;
    var W = 0, H = 0, ctx;
    var small = window.innerWidth < 640;
    var N = small ? 60 : 140;
    var pts = [];
    for (var i = 0; i < N; i++) {
      pts.push({ x: (Math.random() * 2 - 1) * 1600, y: (Math.random() * 2 - 1) * 900, z: Math.random() * 2000 + 100, c: Math.random() < 0.18 ? GOLD : (Math.random() < 0.3 ? VIOLET : CYAN) });
    }
    function resize() { W = window.innerWidth; H = window.innerHeight; ctx = fitCanvas(canvas, W, H); }
    window.addEventListener('resize', resize, { passive: true });
    resize();

    var gridOffset = 0;
    function draw(time, dt) {
      ctx.clearRect(0, 0, W, H);
      var f = Math.min(W, H) * 0.9;            // focal length
      var cx = W / 2 - pointer.x * 30;
      var cy = H * 0.42 - pointer.y * 20;
      var horizon = H * 0.66 - pointer.y * 12;
      var aur = ctx.createRadialGradient(cx, horizon, 10, cx, horizon, Math.max(W, H) * 0.55);
       aur.addColorStop(0, rgba(CYAN, 0.28));
aur.addColorStop(0.35, rgba(POS, 0.16));
aur.addColorStop(0.7, rgba(CYAN, 0.05));
aur.addColorStop(1, 'rgba(0,0,0,0)');
ctx.fillStyle = aur;
ctx.fillRect(0, horizon - H * 0.42, W, H * 0.56);
var hBand = ctx.createLinearGradient(0, horizon - 22, 0, horizon + 18);
hBand.addColorStop(0, 'rgba(0,0,0,0)');
hBand.addColorStop(0.5, rgba(CYAN, 0.34));
hBand.addColorStop(1, 'rgba(0,0,0,0)');
ctx.fillStyle = hBand;
ctx.fillRect(0, horizon - 22, W, 40);
       // Glowing 3D neon mountain ridges on left and right horizon
var ridgePeaks = [[0, 0.24], [0.07, 0.74], [0.15, 0.46], [0.23, 0.95], [0.31, 0.54], [0.39, 0.28], [0.46, 0.0]];
for (var layer = 0; layer < 2; layer++) {
var maxH = H * (layer === 0 ? 0.17 : 0.115);
var pxShift = -pointer.x * (layer === 0 ? 14 : 26);
var baseY = horizon - pointer.y * (layer === 0 ? 4 : 8);
var mCol = layer === 0 ? CYAN : POS;
for (var side = -1; side <= 1; side += 2) {
var ptsM = [];
for (var k = 0; k < ridgePeaks.length; k++) {
var xf = ridgePeaks[k][0] * (layer === 0 ? 1 : 0.92);
var hf = ridgePeaks[k][1] * (0.85 + 0.15 * Math.sin(k * 2.1 + layer + side));
var mx = (side < 0 ? W * xf : W * (1 - xf)) + pxShift;
var my = baseY - maxH * hf;
ptsM.push([mx, my]);
}
ctx.beginPath();
ctx.moveTo(side < 0 ? -20 : W + 20, baseY);
for (var k = 0; k < ptsM.length; k++) ctx.lineTo(ptsM[k][0], ptsM[k][1]);
ctx.lineTo(ptsM[ptsM.length - 1][0], baseY);
ctx.closePath();
ctx.fillStyle = 'rgba(4, 10, 22, 0.84)';
ctx.fill();
ctx.save();
ctx.beginPath();
for (var k = 0; k < ptsM.length; k++) {
if (k === 0) ctx.moveTo(ptsM[k][0], ptsM[k][1]);
else ctx.lineTo(ptsM[k][0], ptsM[k][1]);
}
ctx.shadowColor = rgba(mCol, 0.9);
ctx.shadowBlur = layer === 0 ? 14 : 18;
ctx.strokeStyle = rgba(mCol, layer === 0 ? 0.72 : 0.92);
ctx.lineWidth = layer === 0 ? 1.4 : 1.8;
ctx.stroke();
ctx.shadowBlur = 0;
ctx.strokeStyle = rgba(mCol, 0.24);
ctx.lineWidth = 0.8;
ctx.beginPath();
for (var k = 1; k < ptsM.length - 1; k++) {
ctx.moveTo(ptsM[k][0], ptsM[k][1]);
ctx.lineTo(ptsM[k][0] + side * 24, baseY);
}
ctx.stroke();
ctx.restore();
}
}
       
      // Perspective floor grid
      gridOffset = (gridOffset + dt * 60) % 80;
      var camH = 160;
      ctx.lineWidth = 1;
      for (var z = 80 - gridOffset; z < 2600; z += 80) {
        var y = horizon + (camH * f) / (z + 1) * 0.35;
        if (y > H + 2) continue;
        var a = Math.max(0, 0.16 - z / 2600 * 0.16);
        ctx.strokeStyle = rgba(Math.round((z + gridOffset) / 80) % 2 === 0 ? CYAN : POS, a * 1.35);
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
      }
      var vg = ctx.createLinearGradient(0, horizon, 0, H);
      vg.addColorStop(0, rgba(CYAN, 0));
      vg.addColorStop(1, rgba(CYAN, 0.13));
var vg2 = ctx.createLinearGradient(0, horizon, 0, H);
vg2.addColorStop(0, rgba(POS, 0));
vg2.addColorStop(1, rgba(POS, 0.16));

      ctx.strokeStyle = vg;
      for (var gx = -12; gx <= 12; gx++) {
ctx.strokeStyle = (gx % 2 === 0) ? vg : vg2;
        var spread = gx * W * 0.16;
        ctx.beginPath(); ctx.moveTo(cx + spread * 0.04, horizon); ctx.lineTo(cx + spread, H + 40); ctx.stroke();
      }
      // Horizon glow
      var hg = ctx.createLinearGradient(0, horizon - 60, 0, horizon + 30);
      hg.addColorStop(0, rgba(CYAN, 0));
      hg.addColorStop(0.7, rgba(CYAN, 0.07));
      hg.addColorStop(1, rgba(GOLD, 0));
      ctx.fillStyle = hg;
      ctx.fillRect(0, horizon - 60, W, 90);

      // Particle field drifting toward the camera, with near-neighbour links
      var proj = [];
      for (var i = 0; i < pts.length; i++) {
        var p = pts[i];
        p.z -= dt * 90;
        if (p.z < 60) { p.z = 2100; p.x = (Math.random() * 2 - 1) * 1600; p.y = (Math.random() * 2 - 1) * 900; }
        var s = f / p.z;
        var px = cx + (p.x - pointer.x * 120) * s;
        var py = cy + (p.y - pointer.y * 80) * s;
        if (px < -20 || px > W + 20 || py < -20 || py > H + 20) continue;
        var alpha = Math.min(1, (2100 - p.z) / 900) * 0.75;
        proj.push([px, py, s, alpha, p.c]);
      }
      if (!small) {
        ctx.lineWidth = 0.6;
        for (var a1 = 0; a1 < proj.length; a1++) {
          for (var b1 = a1 + 1; b1 < proj.length; b1++) {
            var dx = proj[a1][0] - proj[b1][0], dy = proj[a1][1] - proj[b1][1];
            var d2 = dx * dx + dy * dy;
            if (d2 < 9000) {
              ctx.strokeStyle = rgba(CYAN, (1 - d2 / 9000) * 0.12 * Math.min(proj[a1][3], proj[b1][3]));
              ctx.beginPath(); ctx.moveTo(proj[a1][0], proj[a1][1]); ctx.lineTo(proj[b1][0], proj[b1][1]); ctx.stroke();
            }
          }
        }
      }
      for (var k = 0; k < proj.length; k++) {
        var q = proj[k];
        var r = Math.max(0.6, q[2] * 2.2);
        ctx.fillStyle = rgba(q[4], q[3]);
        ctx.beginPath(); ctx.arc(q[0], q[1], r, 0, 6.2832); ctx.fill();
      }
    }
    tasks.push(draw);
    draw(0, 0);
  }

  /* ─────────────────────── 2. Market globe ─────────────────────── */
  function readTapeFromDom() {
    var seen = {}, items = [];
    document.querySelectorAll('.market-tape [data-mtape]').forEach(function (el) {
      var sym = el.getAttribute('data-mtape');
      if (!sym || seen[sym]) return;
      seen[sym] = 1;
      var pxEl = el.querySelector('[data-mtape-px]');
      var chEl = el.querySelector('[data-mtape-chg]');
      var px = pxEl ? parseFloat(pxEl.textContent) : NaN;
      var ch = chEl ? parseFloat((chEl.textContent || '').replace('%', '')) : NaN;
      items.push({ symbol: sym, price: isFinite(px) ? px : null, change_pct: isFinite(ch) ? ch : null });
    });
    return items;
  }

  function initOrb(host) {
    var canvas = host.querySelector('canvas');
    if (!canvas || !canvas.getContext) return;
    var hud = host.querySelector('[data-cc3d-hud]');
    var decor = host.getAttribute('data-cc3d-orb') === 'decor';
    var ctx, W = 0, H = 0;
    var nodes = [];
    var lastSync = 0;
    var rotY = 0.6, rotX = -0.32, velY = reduceMotion ? 0 : 0.18, dragging = false, lx = 0, ly = 0;

    function resize() {
      var r = host.getBoundingClientRect();
      W = Math.max(200, r.width); H = Math.max(200, r.height);
      ctx = fitCanvas(canvas, W, H);
    }
    if (window.ResizeObserver) new ResizeObserver(resize).observe(host);
    else window.addEventListener('resize', resize, { passive: true });
    resize();

    function setData(items) {
      var list = (items || []).filter(function (t) { return t && t.symbol; });
      var seen = {}, uniq = [];
      list.forEach(function (t) { if (!seen[t.symbol]) { seen[t.symbol] = 1; uniq.push(t); } });
      var n = uniq.length, golden = Math.PI * (3 - Math.sqrt(5));
      nodes = uniq.map(function (t, i) {
        var y = n === 1 ? 0 : 1 - (i / (n - 1)) * 2;
        var rad = Math.sqrt(1 - y * y);
        var th = golden * i;
        var prev = null;
        for (var j = 0; j < nodes.length; j++) if (nodes[j].symbol === t.symbol) prev = nodes[j];
        return {
          symbol: t.symbol,
          price: t.price,
          chg: t.change_pct,
          x: Math.cos(th) * rad, y: y * 0.92, z: Math.sin(th) * rad,
          flash: prev && prev.price !== t.price ? 1 : (prev ? prev.flash : 0.6)
        };
      });
      var known = uniq.filter(function (t) { return t.change_pct != null; });
      host.classList.toggle('is-waiting', !known.length);
      if (known.length) lastSync = Date.now();
      renderHud(known);
      if (selected) { if (nodeFor(selected)) renderCard(); else closeCard(); }
      if (reduceMotion && ctx) draw(0, 0);
    }

    function renderHud(known) {
      if (!hud) return;
      hud.replaceChildren();
      function cell(label, value, cls) {
        var s = document.createElement('span');
        if (cls) s.className = cls;
        s.appendChild(document.createTextNode(label + ' '));
        var b = document.createElement('b'); b.textContent = value; s.appendChild(b);
        hud.appendChild(s);
      }
      if (!known.length) { cell('Status', 'Awaiting live quotes'); return; }
      var up = 0, down = 0, sum = 0;
      known.forEach(function (t) { var v = Number(t.change_pct); sum += v; if (v > 0) up++; else if (v < 0) down++; });
      var avg = sum / known.length;
      cell('Nodes', String(known.length));
      cell('Adv', String(up), 'is-up');
      cell('Dec', String(down), 'is-down');
      cell('Avg', (avg >= 0 ? '+' : '') + avg.toFixed(2) + '%', avg > 0 ? 'is-up' : (avg < 0 ? 'is-down' : ''));
      var syncCell = document.createElement('span');
      syncCell.setAttribute('data-cc3d-sync', '');
      hud.appendChild(syncCell);
      updateSync();
    }
    function updateSync() {
      var el = hud && hud.querySelector('[data-cc3d-sync]');
      if (!el || !lastSync) return;
      var s = Math.round((Date.now() - lastSync) / 1000);
      el.textContent = 'Sync ';
      var b = document.createElement('b');
      b.textContent = s < 5 ? 'live' : (s < 60 ? s + 's' : Math.floor(s / 60) + 'm');
      el.appendChild(b);
    }
    setInterval(updateSync, 5000);

    if (!decor) {
      setData(readTapeFromDom());
      document.addEventListener('tradestaar:tape', function (e) { setData(e.detail); });
    }

    // ── Interaction: drag spins; a tap (barely moves) picks a ticker ──
    var hits = [];            // screen positions of near-side nodes, rebuilt every frame
    var hovered = null, selected = null;
    var downX = 0, downY = 0, downT = 0, moved = 0;
    var card = decor ? null : buildCard();

    function nearest(clientX, clientY, radius) {
      var r = canvas.getBoundingClientRect();
      var x = clientX - r.left, y = clientY - r.top, best = null, bestD = radius * radius;
      for (var i = 0; i < hits.length; i++) {
        var dx = hits[i].x - x, dy = hits[i].y - y, d2 = dx * dx + dy * dy;
        if (d2 < bestD) { bestD = d2; best = hits[i]; }
      }
      return best;
    }

    if (!decor) {
      host.addEventListener('pointerdown', function (e) {
        if (e.target.closest('.cc3d-card')) return;
        dragging = true; lx = downX = e.clientX; ly = downY = e.clientY; downT = Date.now(); moved = 0;
        host.setPointerCapture && host.setPointerCapture(e.pointerId);
      });
      host.addEventListener('pointermove', function (e) {
        if (!dragging) {
          if (e.pointerType === 'mouse') {
            var h = nearest(e.clientX, e.clientY, 16);
            hovered = h ? h.symbol : null;
            canvas.style.cursor = h ? 'pointer' : '';
            if (reduceMotion) draw(0, 0);
          }
          return;
        }
        var dx = e.clientX - lx, dy = e.clientY - ly; lx = e.clientX; ly = e.clientY;
        moved += Math.abs(dx) + Math.abs(dy);
        if (moved < 6) return;              // still a tap, not a drag
        if (selected) closeCard();
        rotY += dx * 0.008; rotX = Math.max(-1.1, Math.min(1.1, rotX + dy * 0.006));
        velY = dx * 0.4;
        if (reduceMotion) draw(0, 0);
      });
      host.addEventListener('pointerup', function (e) {
        if (e.target.closest('.cc3d-card')) return;
        var wasTap = dragging && moved < 6 && Date.now() - downT < 600;
        dragging = false;
        if (wasTap) {
          var h = nearest(e.clientX, e.clientY, e.pointerType === 'mouse' ? 16 : 26);
          if (h) openCard(h.symbol); else closeCard();
        } else if (!selected && !reduceMotion && Math.abs(velY) < 0.12) velY = 0.18;
      });
      host.addEventListener('pointercancel', function () { dragging = false; });
      host.addEventListener('pointerleave', function () { hovered = null; canvas.style.cursor = ''; });
      document.addEventListener('pointerdown', function (e) { if (selected && !host.contains(e.target)) closeCard(); });
      document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && selected) closeCard(); });
    }

    // ── The ticker card ──
    function buildCard() {
      var el = document.createElement('div');
      el.className = 'cc3d-card';
      el.hidden = true;
      el.setAttribute('role', 'dialog');
      el.setAttribute('aria-label', 'Ticker actions');
      host.appendChild(el);
      return el;
    }
    function nodeFor(sym) { for (var i = 0; i < nodes.length; i++) if (nodes[i].symbol === sym) return nodes[i]; return null; }
    function urlFor(tpl, sym) { return (tpl || '').replace('__T__', encodeURIComponent(sym)); }
    function renderCard() {
      var nd = selected && nodeFor(selected);
      if (!card || !nd) return;
      card.replaceChildren();
      var head = document.createElement('div'); head.className = 'cc3d-card__head';
      var sym = document.createElement('strong'); sym.textContent = nd.symbol;
      var close = document.createElement('button'); close.type = 'button'; close.className = 'cc3d-card__close';
      close.setAttribute('aria-label', 'Close'); close.textContent = '×';
      close.addEventListener('click', closeCard);
      head.append(sym, close);
      var quote = document.createElement('div'); quote.className = 'cc3d-card__quote';
      var px = document.createElement('span'); px.className = 'cc3d-card__px';
      px.textContent = nd.price != null ? Number(nd.price).toFixed(2) : '—';
      var ch = document.createElement('span');
      ch.className = 'cc3d-card__chg ' + (nd.chg == null ? 'is-unknown' : (nd.chg > 0 ? 'is-up' : (nd.chg < 0 ? 'is-down' : '')));
      ch.textContent = nd.chg != null ? (nd.chg >= 0 ? '+' : '') + Number(nd.chg).toFixed(2) + '%' : '—';
      quote.append(px, ch);
      var src = document.createElement('small'); src.className = 'cc3d-card__src';
      src.textContent = 'From the live tape' + (lastSync ? ' · synced ' + new Date(lastSync).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '');
      var acts = document.createElement('div'); acts.className = 'cc3d-card__actions';
      function link(label, href, primary) {
        var a = document.createElement('a'); a.href = href; a.textContent = label;
        a.className = 'cc3d-card__btn' + (primary ? ' is-primary' : '');
        acts.appendChild(a);
      }
      link('Chart', urlFor(host.dataset.chartUrl, nd.symbol), true);
      link('Fundamentals', urlFor(host.dataset.researchUrl, nd.symbol), false);
      if (host.dataset.watchUrl) {
        var f = document.createElement('form'); f.method = 'post'; f.action = host.dataset.watchUrl; f.className = 'cc3d-card__form';
        function hidden(n, v) { var i = document.createElement('input'); i.type = 'hidden'; i.name = n; i.value = v; f.appendChild(i); }
        var tok = document.querySelector('meta[name="csrf-token"]');
        hidden('csrf_token', tok ? tok.content : '');
        hidden('tickers', nd.symbol);
        hidden('next', location.pathname + location.search);
        var b = document.createElement('button'); b.type = 'submit'; b.className = 'cc3d-card__btn'; b.textContent = '+ Watchlist';
        b.title = 'Add ' + nd.symbol + ' to your active watchlist';
        f.appendChild(b); acts.appendChild(f);
      }
      card.append(head, quote, src, acts);
    }
    function placeCard() {
      if (!card || card.hidden || !selected) return;
      if (window.matchMedia('(max-width: 640px)').matches) { card.style.left = ''; card.style.top = ''; card.classList.add('is-sheet'); return; }
      card.classList.remove('is-sheet');
      var h = null; for (var i = 0; i < hits.length; i++) if (hits[i].symbol === selected) h = hits[i];
      if (!h) return;
      var cw = card.offsetWidth || 220, chh = card.offsetHeight || 150;
      var x = h.x + 18, y = h.y - chh / 2;
      if (x + cw > W) x = h.x - 18 - cw;
      card.style.left = Math.max(0, Math.min(W - cw, x)) + 'px';
      card.style.top = Math.max(0, Math.min(H - chh, y)) + 'px';
    }
    function openCard(sym) {
      selected = sym; velY = 0;
      host.classList.add('has-selection');
      renderCard(); card.hidden = false; placeCard();
      var first = card.querySelector('.cc3d-card__btn'); if (first && document.activeElement !== first && finePointer) first.focus({ preventScroll: true });
      if (reduceMotion) draw(0, 0);
    }
    function closeCard() {
      if (!selected) return;
      selected = null; if (card) card.hidden = true;
      host.classList.remove('has-selection');
      if (!reduceMotion) velY = 0.18; else draw(0, 0);
    }

    function project(x, y, z, R, cx, cy) {
      // rotate Y then X
      var cY = Math.cos(rotY), sY = Math.sin(rotY), cX = Math.cos(rotX), sX = Math.sin(rotX);
      var x1 = x * cY + z * sY, z1 = -x * sY + z * cY;
      var y1 = y * cX - z1 * sX, z2 = y * sX + z1 * cX;
      var persp = 3.2 / (3.2 - z2);
      return [cx + x1 * R * persp, cy + y1 * R * persp, z2, persp];
    }

    function draw(time, dt) {
      if (!dragging && !reduceMotion) { rotY += velY * dt; velY += ((selected ? 0 : 0.18) - velY) * (selected ? 0.2 : 0.02); }
      ctx.clearRect(0, 0, W, H);
      // Bigger canvas, bigger globe: fill more of it once there is room.
      var R = Math.min(W, H) * (Math.min(W, H) > 420 ? 0.34 : 0.29);
      var cx = W / 2 + pointer.x * 6, cy = H * 0.47 + pointer.y * 6;

      // Core glow
      var cg = ctx.createRadialGradient(cx - R * 0.3, cy - R * 0.35, R * 0.1, cx, cy, R * 1.25);
      cg.addColorStop(0, rgba(CYAN, 0.16));
      cg.addColorStop(0.6, rgba(VIOLET, 0.07));
      cg.addColorStop(1, rgba(CYAN, 0));
      ctx.fillStyle = cg;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.25, 0, 6.2832); ctx.fill();
      // Luminous 3D Earth sphere & bright cyan atmospheric rim halo
      var earthBg = ctx.createRadialGradient(cx - R * 0.28, cy - R * 0.3, R * 0.1, cx, cy, R);
      earthBg.addColorStop(0, 'rgba(10, 48, 72, 0.88)');
      earthBg.addColorStop(0.65, 'rgba(4, 16, 34, 0.92)');
      earthBg.addColorStop(0.92, rgba(CYAN, 0.24));
      earthBg.addColorStop(1, rgba(CYAN, 0.58));
      ctx.fillStyle = earthBg;
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, 6.2832); ctx.fill();
      ctx.save();
      ctx.strokeStyle = rgba(CYAN, 0.8);
      ctx.lineWidth = 2.2;
      ctx.shadowColor = rgba(CYAN, 0.95);
      ctx.shadowBlur = 20;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.004, 0, 6.2832); ctx.stroke();
      ctx.restore();

      // Glowing emerald/cyan continent silhouettes
      var CONTINENTS = [
        [[60,-165],[70,-140],[68,-95],[60,-65],[45,-62],[30,-81],[25,-80],[20,-90],[15,-84],[9,-78],[18,-104],[32,-117],[48,-125],[58,-136]],
        [[11,-74],[5,-52],[-5,-35],[-23,-42],[-38,-57],[-54,-68],[-45,-75],[-20,-71],[-5,-81]],
        [[36,-9],[43,-9],[50,2],[58,8],[69,18],[70,30],[60,30],[50,25],[43,15],[38,24],[36,10]],
        [[35,-6],[32,12],[31,32],[12,44],[2,45],[-10,40],[-26,33],[-34,18],[-20,12],[4,9],[6,-10],[20,-17]],
        [[70,32],[73,80],[72,135],[66,170],[54,140],[40,128],[31,122],[22,114],[10,104],[1,103],[8,98],[22,90],[8,77],[25,62],[30,48],[41,29],[55,28]],
        [[-12,130],[-14,142],[-26,153],[-38,145],[-34,115],[-22,114]]
      ];
      ctx.save();
      ctx.beginPath(); ctx.arc(cx, cy, R * 0.998, 0, 6.2832); ctx.clip();
      for (var ci = 0; ci < CONTINENTS.length; ci++) {
        var poly = CONTINENTS[ci], ptsC = [], front = 0;
        for (var vi = 0; vi < poly.length; vi++) {
          var laR = poly[vi][0] * 0.0174533, loR = poly[vi][1] * 0.0174533;
          var cp = project(Math.cos(laR) * Math.cos(loR), -Math.sin(laR), Math.cos(laR) * Math.sin(loR), R * 1.001, cx, cy);
          ptsC.push(cp);
          if (cp[2] > -0.15 * R) front++;
        }
        if (front < 2) continue;
        ctx.beginPath();
        for (var vi = 0; vi < ptsC.length; vi++) {
          if (vi === 0) ctx.moveTo(ptsC[vi][0], ptsC[vi][1]);
          else ctx.lineTo(ptsC[vi][0], ptsC[vi][1]);
        }
        ctx.closePath();
        ctx.fillStyle = rgba(POS, 0.24);
        ctx.fill();
        ctx.shadowColor = rgba(CYAN, 0.75);
        ctx.shadowBlur = 8;
        ctx.strokeStyle = rgba(POS, 0.76);
        ctx.lineWidth = 1.25;
        ctx.stroke();
        ctx.shadowBlur = 0;
      }
      // City-light clusters
      var CITIES = [
        [40.7,-74],[41.8,-87.6],[37.7,-122.4],[43.6,-79.3],[51.5,-0.1],[50.1,8.6],[48.8,2.3],[47.3,8.5],
        [35.6,139.7],[22.3,114.2],[1.3,103.8],[-33.8,151.2],[25.2,55.3],[-23.5,-46.6],[19.0,72.8],[37.5,127.0],[31.2,121.5],[-26.2,28.0]
      ];
      for (var ki = 0; ki < CITIES.length; ki++) {
        var cLat = CITIES[ki][0], cLon = CITIES[ki][1];
        for (var sub = 0; sub < 3; sub++) {
          var sLat = (cLat + (sub === 0 ? 0 : (sub === 1 ? 1.8 : -1.5))) * 0.0174533;
          var sLon = (cLon + (sub === 0 ? 0 : (sub === 1 ? 2.2 : -1.9))) * 0.0174533;
          var cP = project(Math.cos(sLat) * Math.cos(sLon), -Math.sin(sLat), Math.cos(sLat) * Math.sin(sLon), R * 1.003, cx, cy);
          if (cP[2] <= 0.05 * R) continue;
          var pulse = reduceMotion ? 0.85 : (0.55 + 0.45 * Math.sin(time * 2.4 + ki * 1.3 + sub));
          ctx.fillStyle = rgba(sub === 0 ? GOLD : CYAN, pulse * 0.9);
          ctx.shadowColor = rgba(sub === 0 ? GOLD : CYAN, 0.85);
          ctx.shadowBlur = sub === 0 ? 8 : 5;
          ctx.beginPath(); ctx.arc(cP[0], cP[1], sub === 0 ? 2.0 : 1.15, 0, 6.2832); ctx.fill();
        }
      }
      ctx.shadowBlur = 0;
      ctx.restore();
       
      // Wireframe: latitudes + meridians
      ctx.lineWidth = 0.8;
      var seg = 48;
      function strokePath(fn) {
        var prev = null;
        for (var i = 0; i <= seg; i++) {
          var p = fn(i / seg * 6.2832);
          var P = project(p[0], p[1], p[2], R, cx, cy);
          if (prev) {
            var depth = (prev[2] + P[2]) / 2;
            ctx.strokeStyle = rgba(CYAN, depth > 0 ? 0.32 : 0.08);
            ctx.beginPath(); ctx.moveTo(prev[0], prev[1]); ctx.lineTo(P[0], P[1]); ctx.stroke();
          }
          prev = P;
        }
      }
      for (var la = -3; la <= 3; la++) {
        var phi = la * Math.PI / 8, yy = Math.sin(phi), rr = Math.cos(phi);
        strokePath(function (t) { return [Math.cos(t) * rr, yy, Math.sin(t) * rr]; });
      }
      for (var mo = 0; mo < 8; mo++) {
        var lon = mo * Math.PI / 8;
        strokePath(function (t) { return [Math.cos(t) * Math.cos(lon), Math.sin(t), Math.cos(t) * Math.sin(lon)]; });
      }

      // Tilted orbit ring with a travelling beacon
      var ringTilt = 0.42, beacon = (time * 0.6) % 6.2832, prevR = null;
      ctx.lineWidth = 1.2;
      for (var s = 0; s <= 72; s++) {
        var t = s / 72 * 6.2832;
        var rx = Math.cos(t) * 1.32, rz = Math.sin(t) * 1.32;
        var ry = rz * Math.sin(ringTilt); rz = rz * Math.cos(ringTilt);
        var P = project(rx, ry, rz, R, cx, cy);
        if (prevR) {
          ctx.strokeStyle = rgba(GOLD, P[2] > 0 ? 0.45 : 0.12);
          ctx.beginPath(); ctx.moveTo(prevR[0], prevR[1]); ctx.lineTo(P[0], P[1]); ctx.stroke();
        }
        prevR = P;
      }
      var bz = Math.sin(beacon) * 1.32;
      var B = project(Math.cos(beacon) * 1.32, bz * Math.sin(ringTilt), bz * Math.cos(ringTilt), R, cx, cy);
      ctx.fillStyle = rgba(GOLD, 0.95);
      ctx.shadowColor = rgba(GOLD, 0.9); ctx.shadowBlur = 14;
      ctx.beginPath(); ctx.arc(B[0], B[1], 2.6 * B[3], 0, 6.2832); ctx.fill();
      ctx.shadowBlur = 0;
      // Second glowing cyan/emerald orbital ring
      var ringTilt2 = -0.52, beacon2 = (-time * 0.45 + 2.1) % 6.2832, prevR2 = null;
      ctx.lineWidth = 1.4;
      for (var s2 = 0; s2 <= 72; s2++) {
        var t2 = s2 / 72 * 6.2832;
        var rx2 = Math.cos(t2) * 1.44, rz2 = Math.sin(t2) * 1.44;
        var ry2 = rz2 * Math.sin(ringTilt2); rz2 = rz2 * Math.cos(ringTilt2);
        var P2 = project(rx2, ry2, rz2, R, cx, cy);
        if (prevR2) {
          ctx.strokeStyle = rgba(P2[2] > 0 ? CYAN : POS, P2[2] > 0 ? 0.62 : 0.16);
          ctx.shadowColor = rgba(CYAN, 0.8);
          ctx.shadowBlur = P2[2] > 0 ? 10 : 0;
          ctx.beginPath(); ctx.moveTo(prevR2[0], prevR2[1]); ctx.lineTo(P2[0], P2[1]); ctx.stroke();
        }
        prevR2 = P2;
      }
      ctx.shadowBlur = 0;
      var bz2 = Math.sin(beacon2) * 1.44;
      var B2 = project(Math.cos(beacon2) * 1.44, bz2 * Math.sin(ringTilt2), bz2 * Math.cos(ringTilt2), R, cx, cy);
      ctx.fillStyle = rgba(POS, 0.95);
      ctx.shadowColor = rgba(CYAN, 0.95); ctx.shadowBlur = 16;
      ctx.beginPath(); ctx.arc(B2[0], B2[1], 2.8 * B2[3], 0, 6.2832); ctx.fill();
      ctx.shadowBlur = 0;
       
      // Data nodes: spike length = size of the move, colour = direction
      var drawn = [];
      for (var n = 0; n < nodes.length; n++) {
        var nd = nodes[n];
        var mag = nd.chg == null ? 0 : Math.min(Math.abs(nd.chg), 6) / 6;
        var col = nd.chg == null ? FLAT : (nd.chg > 0 ? POS : (nd.chg < 0 ? NEG : FLAT));
        var base = project(nd.x, nd.y, nd.z, R, cx, cy);
        var k = 1 + 0.06 + mag * 0.30;
        var tip = project(nd.x * k, nd.y * k, nd.z * k, R, cx, cy);
        drawn.push([base, tip, nd, col, mag]);
      }
      drawn.sort(function (a, b) { return a[1][2] - b[1][2]; });
      // A crowded globe labels only its biggest movers on the near side,
      // so names never pile on top of each other. Every node still draws.
      var labelSet = null;
      if (drawn.length > 16) {
        labelSet = {};
        drawn.filter(function (it) { return it[1][2] > 0.15 && it[2].chg != null; })
          .sort(function (a, b) { return Math.abs(b[2].chg) - Math.abs(a[2].chg); })
          .slice(0, W > 520 ? 18 : 12)
          .forEach(function (it) { labelSet[it[2].symbol] = 1; });
      }
      ctx.font = '600 ' + (W > 520 ? 12 : 10) + 'px "IBM Plex Mono", ui-monospace, monospace';
      ctx.textBaseline = 'middle';
      hits = [];
      for (var d = 0; d < drawn.length; d++) {
        var it = drawn[d], front = it[1][2] > -0.1, A = front ? 1 : 0.25;
        var isSel = selected === it[2].symbol, isHov = hovered === it[2].symbol;
        if (selected && !isSel) A *= 0.45;
        if (front && it[1][2] > 0) hits.push({ symbol: it[2].symbol, x: it[1][0], y: it[1][1] });
        it[2].flash = Math.max(0, it[2].flash - dt * 0.6);
        ctx.strokeStyle = rgba(it[3], 0.75 * A);
        ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(it[0][0], it[0][1]); ctx.lineTo(it[1][0], it[1][1]); ctx.stroke();
        var rad = (2 + it[4] * 2.5) * it[1][3];
        ctx.fillStyle = rgba(it[3], A);
        ctx.shadowColor = rgba(it[3], 0.9 * A); ctx.shadowBlur = front ? 10 + it[2].flash * 20 : 0;
        ctx.beginPath(); ctx.arc(it[1][0], it[1][1], rad * (isSel || isHov ? 1.8 : 1), 0, 6.2832); ctx.fill();
        ctx.shadowBlur = 0;
        if (isSel) {
          var pr = rad * 2.6 + 4 + Math.sin(time * 4) * 2;
          ctx.strokeStyle = rgba(GOLD, 0.95); ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(it[1][0], it[1][1], pr, 0, 6.2832); ctx.stroke();
        }
        if (front && it[1][2] > 0.15 && (isSel || isHov || (!selected && (!labelSet || labelSet[it[2].symbol])))) {
          var label = it[2].symbol + (it[2].chg != null ? ' ' + (it[2].chg >= 0 ? '+' : '') + Number(it[2].chg).toFixed(2) + '%' : '');
          var lx2 = it[1][0] + 7, ly2 = it[1][1];
          var tw = ctx.measureText(label).width;
          if (lx2 + tw > W - 4) lx2 = it[1][0] - 7 - tw;
          ctx.fillStyle = 'rgba(3,7,15,0.72)';
          ctx.fillRect(lx2 - 3, ly2 - (W > 520 ? 8 : 7), tw + 6, W > 520 ? 16 : 14);
          ctx.fillStyle = rgba(it[3], Math.min(1, 0.55 + it[1][2]));
          ctx.fillText(label, lx2, ly2);
        }
      }
    }
    tasks.push(function () { placeCard(); });
    tasks.push(draw);
    draw(0, 0);
  }

  /* ─────────────────────────── 3. Tilt ─────────────────────────── */
  function initTilt() {
    if (!finePointer || reduceMotion) return;
    var SEL = '.liq-page .cc-card, .workspace-paths a, .hx .stat, .risk-stat-card, .schwab-bal-card, .learn-card';
    var active = null, timer = 0;
    function release(el) {
      el.style.removeProperty('--cc-rx'); el.style.removeProperty('--cc-ry');
      clearTimeout(timer);
      timer = setTimeout(function () { if (el !== active) el.classList.remove('cc27-tilt'); }, 320);
    }
    document.addEventListener('pointerover', function (e) {
      var el = e.target.closest && e.target.closest(SEL);
      if (el === active) return;
      if (active) release(active);
      active = el;
      if (el) el.classList.add('cc27-tilt');
    }, { passive: true });
    document.addEventListener('pointermove', function (e) {
      if (!active) return;
      var r = active.getBoundingClientRect();
      if (r.width > 900) return; // full-width panels stay flat
      var px = (e.clientX - r.left) / r.width - 0.5, py = (e.clientY - r.top) / r.height - 0.5;
      var max = r.width > 500 ? 2 : 4;
      active.style.setProperty('--cc-ry', (px * max).toFixed(2) + 'deg');
      active.style.setProperty('--cc-rx', (-py * max).toFixed(2) + 'deg');
    }, { passive: true });
    document.addEventListener('pointerleave', function () { if (active) { release(active); active = null; } });
  }

  function start() {
    initBackdrop();
    document.querySelectorAll('[data-cc3d-orb]').forEach(initOrb);
    initTilt();
    if (!reduceMotion) requestAnimationFrame(loop);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
