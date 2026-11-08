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

      // Perspective floor grid
      gridOffset = (gridOffset + dt * 60) % 80;
      var camH = 160;
      ctx.lineWidth = 1;
      for (var z = 80 - gridOffset; z < 2600; z += 80) {
        var y = horizon + (camH * f) / (z + 1) * 0.35;
        if (y > H + 2) continue;
        var a = Math.max(0, 0.16 - z / 2600 * 0.16);
        ctx.strokeStyle = rgba(CYAN, a);
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
      }
      var vg = ctx.createLinearGradient(0, horizon, 0, H);
      vg.addColorStop(0, rgba(CYAN, 0));
      vg.addColorStop(1, rgba(CYAN, 0.13));
      ctx.strokeStyle = vg;
      for (var gx = -12; gx <= 12; gx++) {
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

    // Drag to spin, with inertia (data globes only)
    if (!decor) host.addEventListener('pointerdown', function (e) { dragging = true; lx = e.clientX; ly = e.clientY; velY = 0; host.setPointerCapture && host.setPointerCapture(e.pointerId); });
    host.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      var dx = e.clientX - lx, dy = e.clientY - ly; lx = e.clientX; ly = e.clientY;
      rotY += dx * 0.008; rotX = Math.max(-1.1, Math.min(1.1, rotX + dy * 0.006));
      velY = dx * 0.4;
      if (reduceMotion) draw(0, 0);
    });
    function endDrag() { dragging = false; if (!reduceMotion && Math.abs(velY) < 0.12) velY = 0.18; }
    host.addEventListener('pointerup', endDrag);
    host.addEventListener('pointercancel', endDrag);

    function project(x, y, z, R, cx, cy) {
      // rotate Y then X
      var cY = Math.cos(rotY), sY = Math.sin(rotY), cX = Math.cos(rotX), sX = Math.sin(rotX);
      var x1 = x * cY + z * sY, z1 = -x * sY + z * cY;
      var y1 = y * cX - z1 * sX, z2 = y * sX + z1 * cX;
      var persp = 3.2 / (3.2 - z2);
      return [cx + x1 * R * persp, cy + y1 * R * persp, z2, persp];
    }

    function draw(time, dt) {
      if (!dragging && !reduceMotion) { rotY += velY * dt; velY += (0.18 - velY) * 0.02; }
      ctx.clearRect(0, 0, W, H);
      var R = Math.min(W, H) * 0.29;
      var cx = W / 2 + pointer.x * 6, cy = H * 0.47 + pointer.y * 6;

      // Core glow
      var cg = ctx.createRadialGradient(cx - R * 0.3, cy - R * 0.35, R * 0.1, cx, cy, R * 1.25);
      cg.addColorStop(0, rgba(CYAN, 0.16));
      cg.addColorStop(0.6, rgba(VIOLET, 0.07));
      cg.addColorStop(1, rgba(CYAN, 0));
      ctx.fillStyle = cg;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.25, 0, 6.2832); ctx.fill();

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
          .slice(0, 12)
          .forEach(function (it) { labelSet[it[2].symbol] = 1; });
      }
      ctx.font = '600 10px "IBM Plex Mono", ui-monospace, monospace';
      ctx.textBaseline = 'middle';
      for (var d = 0; d < drawn.length; d++) {
        var it = drawn[d], front = it[1][2] > -0.1, A = front ? 1 : 0.25;
        it[2].flash = Math.max(0, it[2].flash - dt * 0.6);
        ctx.strokeStyle = rgba(it[3], 0.75 * A);
        ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(it[0][0], it[0][1]); ctx.lineTo(it[1][0], it[1][1]); ctx.stroke();
        var rad = (2 + it[4] * 2.5) * it[1][3];
        ctx.fillStyle = rgba(it[3], A);
        ctx.shadowColor = rgba(it[3], 0.9 * A); ctx.shadowBlur = front ? 10 + it[2].flash * 20 : 0;
        ctx.beginPath(); ctx.arc(it[1][0], it[1][1], rad, 0, 6.2832); ctx.fill();
        ctx.shadowBlur = 0;
        if (front && it[1][2] > 0.15 && (!labelSet || labelSet[it[2].symbol])) {
          var label = it[2].symbol + (it[2].chg != null ? ' ' + (it[2].chg >= 0 ? '+' : '') + Number(it[2].chg).toFixed(2) + '%' : '');
          var lx2 = it[1][0] + 7, ly2 = it[1][1];
          var tw = ctx.measureText(label).width;
          if (lx2 + tw > W - 4) lx2 = it[1][0] - 7 - tw;
          ctx.fillStyle = 'rgba(3,7,15,0.72)';
          ctx.fillRect(lx2 - 3, ly2 - 7, tw + 6, 14);
          ctx.fillStyle = rgba(it[3], Math.min(1, 0.55 + it[1][2]));
          ctx.fillText(label, lx2, ly2);
        }
      }
    }
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
