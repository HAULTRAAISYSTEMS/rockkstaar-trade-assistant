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
      // Pages with a painted scene (the Command Center) keep only the particles.
      if (!document.documentElement.classList.contains('cc-scene')) {
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

      }
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
    var rotY = -0.35, rotX = -0.3, velY = reduceMotion ? 0 : 0.1, dragging = false, lx = 0, ly = 0;

    function resize() {
      var r = host.getBoundingClientRect();
      W = Math.max(200, r.width); H = Math.max(200, r.height);
      ctx = fitCanvas(canvas, W, H);
    }
    if (window.ResizeObserver) new ResizeObserver(resize).observe(host);
    else window.addEventListener('resize', resize, { passive: true });
    resize();

    /* ── Real Earth texture ───────────────────────────────────────────
       /static/img/earth.webp is an equirectangular map built from Natural
       Earth coastlines (top half) and city lights from real city
       populations (bottom half); scripts/build_earth_texture.py makes it.
       Each frame every pixel of the disc is ray-cast through the same
       perspective as project(), rotated back to lat/lon and shaded. */
    var EARTH_SIL = Math.sqrt(10.24 * 4 / 36.96);   // silhouette radius / R for persp 3.2

    // World point for a latitude/longitude, in the same frame as project()
    // and the texture (east is +lon).
    function geo(lat, lon) {
      var a = lat * 0.0174533, o = lon * 0.0174533;
      return [Math.cos(a) * Math.cos(o), -Math.sin(a), -Math.cos(a) * Math.sin(o)];
    }
    function toCam(v) {
      var cY = Math.cos(rotY), sY = Math.sin(rotY), cX = Math.cos(rotX), sX = Math.sin(rotX);
      var x1 = v[0] * cY + v[2] * sY, z1 = -v[0] * sY + v[2] * cY;
      return [x1, v[1] * cX - z1 * sX, v[1] * sX + z1 * cX];
    }
    // Where the sun is overhead right now (declination + hour angle), so the
    // lit half of the globe is the real day side.
    function subsolar(now) {
      var start = Date.UTC(now.getUTCFullYear(), 0, 0), day = (now - start) / 864e5;
      var decl = -23.44 * Math.cos(2 * Math.PI / 365 * (day + 10));
      var hrs = now.getUTCHours() + now.getUTCMinutes() / 60;
      return [decl, -15 * (hrs - 12)];
    }
    var sunCam = [-0.52, -0.42, 0.74];

    /* Sector continents: each sector owns a landmass, pins cluster inside it. */
    var REGIONS = {
      TECH:   { name: 'TECH',          lat: 41,  lon: -100, spread: 12 },
      SEMIS:  { name: 'SEMIS',         lat: 34,  lon: 118,  spread: 9 },
      FIN:    { name: 'FINANCIALS',    lat: 49,  lon: 12,   spread: 8 },
      ENERGY: { name: 'ENERGY',        lat: 26,  lon: 47,   spread: 6 },
      CONS:   { name: 'CONSUMER',      lat: -11, lon: -57,  spread: 10 },
      HEALTH: { name: 'HEALTHCARE',    lat: 3,   lon: 22,   spread: 10 },
      COMM:   { name: 'COMMUNICATION', lat: 22,  lon: 79,   spread: 6 },
      INDUS:  { name: 'INDUSTRIALS',   lat: 60,  lon: 95,   spread: 12 },
      OTHER:  { name: 'DEFENSIVE · OTHER', lat: -25, lon: 134, spread: 9 },
      INDEX:  { name: 'INDEXES',       lat: 72,  lon: -40,  spread: 6 }
    };
    var ETF_REGION = { XLK: 'TECH', SMH: 'SEMIS', XLF: 'FIN', XLE: 'ENERGY', XLY: 'CONS', XLV: 'HEALTH',
                       XLC: 'COMM', XLI: 'INDUS', XLB: 'INDUS', XLU: 'OTHER', XLRE: 'OTHER', IWM: 'INDEX' };
    var SECTORS = {};
    try { SECTORS = JSON.parse(host.getAttribute('data-sectors') || '{}') || {}; } catch (e) { SECTORS = {}; }
    var useRegions = !!host.getAttribute('data-sectors');
    function regionFor(sym) {
      var u = String(sym || '').toUpperCase();
      if (/^(SPY|QQQ|DIA|IWM|VIX|VOO|VTI|TLT|GLD|SLV|USO|DXY|UUP|BTC.*|ETH.*|ES|NQ|YM|RTY|CL|GC)$/.test(u) || /[=^\/]/.test(u)) return 'INDEX';
      return ETF_REGION[SECTORS[u]] || 'OTHER';
    }
    function placeInRegions(list) {
      var groups = {};
      list.forEach(function (t) { var k = regionFor(t.symbol); (groups[k] = groups[k] || []).push(t); });
      var pos = {};
      Object.keys(groups).forEach(function (k) {
        var g = groups[k], rg = REGIONS[k], n = g.length;
        g.forEach(function (t, i) {
          var rr = n === 1 ? 0 : rg.spread * Math.sqrt((i + 0.5) / n), a = i * 2.39996;
          var la = rg.lat + rr * Math.cos(a);
          var lo = rg.lon + rr * Math.sin(a) / Math.max(0.3, Math.cos(la * 0.0174533));
          pos[t.symbol] = { v: geo(la, lo), region: k };
        });
      });
      return pos;
    }

    /* Exchanges: a beacon pulses green while the exchange is inside its
       regular session by its own local clock (holidays are not counted). */
    var EXCH = [
      ['NYSE', 40.71, -74.01, 'America/New_York', 930, 1600],
      ['TSX', 43.65, -79.38, 'America/Toronto', 930, 1600],
      ['B3', -23.55, -46.63, 'America/Sao_Paulo', 1000, 1700],
      ['LSE', 51.51, -0.09, 'Europe/London', 800, 1630],
      ['XETRA', 50.11, 8.68, 'Europe/Berlin', 900, 1730],
      ['NSE', 19.06, 72.87, 'Asia/Kolkata', 915, 1530],
      ['SSE', 31.23, 121.47, 'Asia/Shanghai', 930, 1500],
      ['HKEX', 22.28, 114.16, 'Asia/Hong_Kong', 930, 1600],
      ['TSE', 35.68, 139.77, 'Asia/Tokyo', 900, 1530],
      ['ASX', -33.87, 151.21, 'Australia/Sydney', 1000, 1600]
    ].map(function (e) { return { name: e[0], lat: e[1], lon: e[2], tz: e[3], open: e[4], close: e[5], v: geo(e[1], e[2]), live: false }; });
    var ARCS = [['NYSE', 'LSE'], ['LSE', 'XETRA'], ['XETRA', 'NSE'], ['NSE', 'HKEX'], ['HKEX', 'TSE'], ['TSE', 'ASX'],
                ['NYSE', 'B3'], ['NYSE', 'TSX'], ['LSE', 'HKEX'], ['NYSE', 'TSE'], ['SSE', 'HKEX'], ['LSE', 'B3']];
    function exch(n) { for (var i = 0; i < EXCH.length; i++) if (EXCH[i].name === n) return EXCH[i]; return null; }
    var sessionAt = 0;
    function refreshSessions(now) {
      EXCH.forEach(function (e) {
        try {
          var parts = new Intl.DateTimeFormat('en-US', { timeZone: e.tz, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(now);
          var get = function (t) { for (var i = 0; i < parts.length; i++) if (parts[i].type === t) return parts[i].value; return ''; };
          var wd = get('weekday'), hm = parseInt(get('hour'), 10) * 100 + parseInt(get('minute'), 10);
          e.live = wd !== 'Sat' && wd !== 'Sun' && hm >= e.open && hm < e.close;
        } catch (err) { e.live = false; }
      });
    }
    // Great-circle arc lifted off the surface.
    function arcPoint(a, b, t, lift) {
      var d = Math.acos(Math.max(-1, Math.min(1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2])));
      var sd = Math.sin(d) || 1e-6, k1 = Math.sin((1 - t) * d) / sd, k2 = Math.sin(t * d) / sd;
      var h = 1 + lift * Math.sin(Math.PI * t) * Math.min(1, d / 1.2);
      return [(a[0] * k1 + b[0] * k2) * h, (a[1] * k1 + b[1] * k2) * h, (a[2] * k1 + b[2] * k2) * h];
    }
    var tex = null, TW = 0, TH = 0;
    var eCanvas = document.createElement('canvas'), eCtx = eCanvas.getContext('2d');
    var eImg = null, eD = 0, eRay = null;
    (function loadEarth() {
      var img = new Image();
      img.decoding = 'async';
      img.onload = function () {
        try {
          var c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
          var g = c.getContext('2d'); g.drawImage(img, 0, 0);
          tex = g.getImageData(0, 0, c.width, c.height).data; TW = c.width; TH = c.height / 2;
          if (reduceMotion && ctx) draw(0, 0);
        } catch (e) { tex = null; }
      };
      var small = Math.min(window.innerWidth, window.innerHeight) < 600;
      img.src = (host.getAttribute('data-earth-src') || '/static/img/' + (small ? 'earth_1024.webp' : 'earth.webp'));
    })();

    function buildRays(D) {
      // Camera-space unit-sphere point for every pixel of a D×D square (0 = off-globe).
      eD = D; eCanvas.width = D; eCanvas.height = D;
      eImg = eCtx.createImageData(D, D);
      eRay = new Float32Array(D * D * 4);
      var half = D / 2;
      for (var j = 0; j < D; j++) {
        for (var i = 0; i < D; i++) {
          var u = ((i + 0.5) - half) / half * EARTH_SIL, v = ((j + 0.5) - half) / half * EARTH_SIL;
          var k = (u * u + v * v) / 10.24;
          var A = k + 1, B = -6.4 * k, C = 10.24 * k - 1, disc = B * B - 4 * A * C;
          var o = (j * D + i) * 4;
          if (disc < 0) { eRay[o + 3] = 0; continue; }
          var z = (-B + Math.sqrt(disc)) / (2 * A);
          var f = (3.2 - z) / 3.2;
          eRay[o] = u * f; eRay[o + 1] = v * f; eRay[o + 2] = z;
          // Edge alpha: fade the last ~1.5px for a smooth limb.
          var rr = Math.sqrt(u * u + v * v) / EARTH_SIL;
          eRay[o + 3] = Math.max(0.001, Math.min(1, (1 - rr) * half / 1.5));
        }
      }
    }

    // Spinning about the vertical axis only shifts longitude, so each pixel's
    // texture row and base column are cached per tilt (rotX); a frame is then
    // a lookup plus lighting.
    var eRow = null, eCol = null, eTilt = null;
    function buildLookup() {
      var n = eD * eD, cX = Math.cos(rotX), sX = Math.sin(rotX), PI = Math.PI;
      eRow = new Int32Array(n); eCol = new Float32Array(n);
      for (var p = 0; p < n; p++) {
        var o = p * 4;
        if (eRay[o + 3] === 0) continue;
        var y1 = eRay[o + 1], z2 = eRay[o + 2];
        var y = y1 * cX + z2 * sX, z1 = -y1 * sX + z2 * cX;
        var lat = Math.asin(y < -1 ? 1 : (y > 1 ? -1 : -y));
        var ty = ((PI / 2 - lat) / PI * TH) | 0; if (ty >= TH) ty = TH - 1;
        eRow[p] = ty * TW;
        eCol[p] = (PI - Math.atan2(z1, eRay[o])) / (2 * PI) * TW;   // lon = -atan2(z1, x1) - rotY
      }
      eTilt = rotX;
    }

    function drawEarth(cx, cy, R) {
      if (!tex) return false;
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var D = Math.min(Math.round(2 * R * EARTH_SIL * dpr), window.innerWidth < 760 ? 360 : 600);
      if (D !== eD) { buildRays(D); eTilt = null; }
      if (eTilt !== rotX) buildLookup();
      var data = eImg.data, ray = eRay, row = eRow, col = eCol;
      var Lx = sunCam[0], Ly = sunCam[1], Lz = sunCam[2];
      var Hx = Lx, Hy = Ly, Hz = Lz + 1, hl = Math.sqrt(Hx * Hx + Hy * Hy + Hz * Hz) || 1; Hx /= hl; Hy /= hl; Hz /= hl;
      var tw = TW, rowB = TW * TH * 4;
      var shift = (rotY / (2 * Math.PI)) * tw; shift = shift - Math.floor(shift / tw) * tw;
      for (var p = 0, n = D * D; p < n; p++) {
        var o = p * 4, a = ray[o + 3];
        if (a === 0) { data[o + 3] = 0; continue; }
        var x1 = ray[o], y1 = ray[o + 1], z2 = ray[o + 2];
        var tx = (col[p] - shift) | 0; if (tx < 0) tx += tw; if (tx >= tw) tx -= tw;
        var ti = (row[p] + tx) * 4;
        var r = tex[ti], g = tex[ti + 1], b = tex[ti + 2], lights = tex[rowB + ti] / 255;
        var raw = x1 * Lx + y1 * Ly + z2 * Lz, dif = raw < 0 ? 0 : raw;
        var shade = 0.34 + 0.95 * dif;
        var nightness = 0.5 - raw * 4; nightness = nightness < 0 ? 0 : (nightness > 1 ? 1 : nightness);
        var lg = lights * (0.35 + 1.35 * nightness);
        var R2 = r * shade + 210 * lg, G2 = g * shade + 255 * lg, B2 = b * shade + 72 * lg;
        // Warm twilight along the terminator.
        var tb = raw < 0 ? -raw : raw;
        if (tb < 0.07 && g > b) { tb = (1 - tb / 0.07) * 0.35; R2 += 90 * tb; G2 += 50 * tb; }
        if (b > g) {
          var sp = x1 * Hx + y1 * Hy + z2 * Hz;
          if (sp > 0.97) { sp = Math.pow(sp, 220) * 80; R2 += sp * 0.6; G2 += sp * 0.9; B2 += sp; }
        }
        var limb = 1 - z2; limb = limb * limb * limb;
        R2 += 20 * limb; G2 += 120 * limb; B2 += 170 * limb;
        data[o] = R2 > 255 ? 255 : R2; data[o + 1] = G2 > 255 ? 255 : G2; data[o + 2] = B2 > 255 ? 255 : B2;
        data[o + 3] = a * 255;
      }
      eCtx.putImageData(eImg, 0, 0);
      var RS = R * EARTH_SIL;
      ctx.drawImage(eCanvas, cx - RS, cy - RS, RS * 2, RS * 2);
      return true;
    }

    function setData(items) {
      var list = (items || []).filter(function (t) { return t && t.symbol; });
      var seen = {}, uniq = [];
      list.forEach(function (t) { if (!seen[t.symbol]) { seen[t.symbol] = 1; uniq.push(t); } });
      var n = uniq.length, golden = Math.PI * (3 - Math.sqrt(5));
      var placed = useRegions ? placeInRegions(uniq) : null;
      nodes = uniq.map(function (t, i) {
        var y = n === 1 ? 0 : 1 - (i / (n - 1)) * 2;
        var rad = Math.sqrt(1 - y * y);
        var th = golden * i;
        var prev = null;
        for (var j = 0; j < nodes.length; j++) if (nodes[j].symbol === t.symbol) prev = nodes[j];
        var pp = placed && placed[t.symbol];
        return {
          symbol: t.symbol,
          price: t.price,
          chg: t.change_pct,
          region: pp ? pp.region : null,
          x: pp ? pp.v[0] : Math.cos(th) * rad, y: pp ? pp.v[1] : y * 0.92, z: pp ? pp.v[2] : Math.sin(th) * rad,
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
        } else if (!selected && !reduceMotion && Math.abs(velY) < 0.06) velY = 0.1;
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
      if (!reduceMotion) velY = 0.1; else draw(0, 0);
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
      if (!dragging && !reduceMotion) { rotY += velY * dt; velY += ((selected ? 0 : 0.1) - velY) * (selected ? 0.2 : 0.02); }
      ctx.clearRect(0, 0, W, H);
      // Bigger canvas, bigger globe: fill more of it once there is room.
      var R = Math.min(W, H) * (Math.min(W, H) > 420 ? 0.35 : 0.33);
      var cx = W / 2 + pointer.x * 6, cy = H * 0.47 + pointer.y * 6;

      // Core glow
      var cg = ctx.createRadialGradient(cx - R * 0.3, cy - R * 0.35, R * 0.1, cx, cy, R * 1.25);
      cg.addColorStop(0, rgba(CYAN, 0.16));
      cg.addColorStop(0.6, rgba(VIOLET, 0.07));
      cg.addColorStop(1, rgba(CYAN, 0));
      ctx.fillStyle = cg;
      ctx.beginPath(); ctx.arc(cx, cy, R * 1.25, 0, 6.2832); ctx.fill();
      // Real Earth: coastlines + city lights texture, lit by the real sun.
      var nowD = new Date();
      var ss = subsolar(nowD); sunCam = toCam(geo(ss[0], ss[1]));
      if (!sessionAt || time - sessionAt > 30) { refreshSessions(nowD); sessionAt = time || 0.001; }
      if (!drawEarth(cx, cy, R)) {
        var earthBg = ctx.createRadialGradient(cx - R * 0.28, cy - R * 0.3, R * 0.1, cx, cy, R);
        earthBg.addColorStop(0, 'rgba(10, 48, 72, 0.88)');
        earthBg.addColorStop(1, 'rgba(4, 16, 34, 0.92)');
        ctx.fillStyle = earthBg;
        ctx.beginPath(); ctx.arc(cx, cy, R, 0, 6.2832); ctx.fill();
      }
      // Atmosphere: soft cyan halo just outside the limb, then a crisp rim.
      var RS = R * EARTH_SIL;
      var atm = ctx.createRadialGradient(cx, cy, RS * 0.96, cx, cy, RS * 1.16);
      atm.addColorStop(0, rgba(CYAN, 0.55));
      atm.addColorStop(0.35, rgba(CYAN, 0.18));
      atm.addColorStop(1, rgba(CYAN, 0));
      ctx.fillStyle = atm;
      ctx.beginPath(); ctx.arc(cx, cy, RS * 1.16, 0, 6.2832); ctx.arc(cx, cy, RS * 0.985, 0, 6.2832, true); ctx.fill();
      ctx.save();
      ctx.strokeStyle = rgba(CYAN, 0.7);
      ctx.lineWidth = 1.6;
      ctx.shadowColor = rgba(CYAN, 0.95);
      ctx.shadowBlur = 18;
      ctx.beginPath(); ctx.arc(cx, cy, RS, 0, 6.2832); ctx.stroke();
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
            ctx.strokeStyle = rgba(CYAN, depth > 0 ? 0.13 : 0.0);
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
       
      // Holographic HUD ring: slow-turning ticks and arc segments.
      var RSo = R * EARTH_SIL, hr = RSo * 1.13, spin = reduceMotion ? 0 : time * 0.12;
      ctx.save();
      ctx.lineWidth = 1;
      for (var tk = 0; tk < 120; tk++) {
        var ang = spin + tk / 120 * 6.2832, long = tk % 10 === 0;
        var r0 = hr - (long ? 7 : 3), r1 = hr + (long ? 2 : 0);
        ctx.strokeStyle = rgba(CYAN, long ? 0.55 : 0.22);
        ctx.beginPath(); ctx.moveTo(cx + Math.cos(ang) * r0, cy + Math.sin(ang) * r0); ctx.lineTo(cx + Math.cos(ang) * r1, cy + Math.sin(ang) * r1); ctx.stroke();
      }
      ctx.lineWidth = 2;
      ctx.shadowColor = rgba(CYAN, 0.9); ctx.shadowBlur = 10;
      [[0.1, 0.9], [1.6, 2.2], [3.3, 4.4], [5.0, 5.5]].forEach(function (seg, i) {
        ctx.strokeStyle = rgba(i % 2 ? POS : CYAN, 0.7);
        ctx.beginPath(); ctx.arc(cx, cy, hr + 6, -spin * 1.6 + seg[0], -spin * 1.6 + seg[1]); ctx.stroke();
      });
      ctx.shadowBlur = 0;
      ctx.restore();

      // Trading arcs between money centres, with light pulses flowing along them.
      ctx.save();
      for (var ai = 0; ai < ARCS.length; ai++) {
        var ea = exch(ARCS[ai][0]), eb = exch(ARCS[ai][1]);
        var liveArc = ea.live || eb.live, prevA = null, segs = 36;
        ctx.lineWidth = liveArc ? 1.6 : 1;
        for (var si = 0; si <= segs; si++) {
          var Pa = arcPoint(ea.v, eb.v, si / segs, 0.32);
          var Q = project(Pa[0], Pa[1], Pa[2], R, cx, cy);
          if (prevA) {
            var vis = (prevA[2] + Q[2]) / 2;
            // Hidden behind the globe? skip (the limb sits around z≈0.3 of the radius in this view).
            if (vis > -0.05) {
              ctx.strokeStyle = rgba(liveArc ? POS : CYAN, (liveArc ? 0.55 : 0.28) * Math.min(1, 0.4 + vis));
              ctx.beginPath(); ctx.moveTo(prevA[0], prevA[1]); ctx.lineTo(Q[0], Q[1]); ctx.stroke();
            }
          }
          prevA = Q;
        }
        // Pulses
        for (var pk = 0; pk < 2; pk++) {
          var pt = ((reduceMotion ? 0.5 : time * 0.22) + ai * 0.37 + pk * 0.5) % 1;
          var Pp = arcPoint(ea.v, eb.v, pt, 0.32), Qp = project(Pp[0], Pp[1], Pp[2], R, cx, cy);
          if (Qp[2] < 0) continue;
          ctx.fillStyle = rgba(liveArc ? POS : CYAN, 0.95);
          ctx.shadowColor = rgba(liveArc ? POS : CYAN, 1); ctx.shadowBlur = 12;
          ctx.beginPath(); ctx.arc(Qp[0], Qp[1], 2.2 * Qp[3], 0, 6.2832); ctx.fill();
        }
        ctx.shadowBlur = 0;
      }
      var tagBoxes = [];
      function boxFree(b) {
        for (var q = 0; q < tagBoxes.length; q++) { var o = tagBoxes[q]; if (b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]) return false; }
        return true;
      }
      // Sector territories: a glowing border on the land + a live tag.
      if (useRegions && nodes.length) {
        var stats = {};
        nodes.forEach(function (nd) {
          if (!nd.region) return;
          var st = stats[nd.region] = stats[nd.region] || { n: 0, sum: 0, k: 0 };
          st.n++;
          // VIX moves opposite to stocks, so it stays a pin but not part of the average.
          if (nd.chg != null && !/VIX/.test(String(nd.symbol).toUpperCase())) { st.sum += Number(nd.chg); st.k++; }
        });
        Object.keys(stats).forEach(function (k) {
          var rg = REGIONS[k], st = stats[k], avg = st.k ? st.sum / st.k : null;
          var colT = avg == null ? CYAN : (avg > 0.05 ? POS : (avg < -0.05 ? NEG : FLAT));
          var cen = geo(rg.lat, rg.lon), C = project(cen[0], cen[1], cen[2], R, cx, cy);
          if (C[2] < -0.1) return;
          // Border: small circle of radius spread+4° around the centre.
          var ang = (rg.spread + 4) * 0.0174533, ca = Math.cos(ang), sa = Math.sin(ang);
          var up = Math.abs(cen[1]) > 0.95 ? [1, 0, 0] : [0, 1, 0];
          var e1 = [cen[1] * up[2] - cen[2] * up[1], cen[2] * up[0] - cen[0] * up[2], cen[0] * up[1] - cen[1] * up[0]];
          var l1 = Math.sqrt(e1[0] * e1[0] + e1[1] * e1[1] + e1[2] * e1[2]); e1 = [e1[0] / l1, e1[1] / l1, e1[2] / l1];
          var e2 = [cen[1] * e1[2] - cen[2] * e1[1], cen[2] * e1[0] - cen[0] * e1[2], cen[0] * e1[1] - cen[1] * e1[0]];
          var dash = reduceMotion ? 0 : time * 0.6;
          ctx.lineWidth = 1.6;
          ctx.shadowColor = rgba(colT, 0.9); ctx.shadowBlur = 8;
          var prevT = null;
          for (var bs = 0; bs <= 64; bs++) {
            var th2 = bs / 64 * 6.2832, c2 = Math.cos(th2), s2 = Math.sin(th2);
            var pv = [cen[0] * ca + (e1[0] * c2 + e2[0] * s2) * sa, cen[1] * ca + (e1[1] * c2 + e2[1] * s2) * sa, cen[2] * ca + (e1[2] * c2 + e2[2] * s2) * sa];
            var PT = project(pv[0] * 1.004, pv[1] * 1.004, pv[2] * 1.004, R, cx, cy);
            if (prevT && PT[2] > 0 && prevT[2] > 0 && ((bs + Math.floor(dash * 4)) % 4 !== 0)) {
              ctx.strokeStyle = rgba(colT, 0.5 * Math.min(1, PT[2] * 2));
              ctx.beginPath(); ctx.moveTo(prevT[0], prevT[1]); ctx.lineTo(PT[0], PT[1]); ctx.stroke();
            }
            prevT = PT;
          }
          ctx.shadowBlur = 0;
          // Floating tag above the territory.
          if (C[2] > 0.2) {
            var lift = project(cen[0] * 1.16, cen[1] * 1.16, cen[2] * 1.16, R, cx, cy);
            ctx.font = '800 ' + (W > 520 ? 11 : 10) + 'px "IBM Plex Mono", ui-monospace, monospace';
            ctx.textBaseline = 'middle';
            var t1 = rg.name, t2 = avg == null ? st.n + ' pins' : (avg >= 0 ? '+' : '') + avg.toFixed(2) + '%';
            var w1 = ctx.measureText(t1).width, w2 = ctx.measureText(t2).width, bw = w1 + w2 + 22;
            var bx = lift[0] - bw / 2, by = lift[1] - 11, box = [bx, by, bx + bw, by + 22];
            if (!boxFree(box)) return;
            tagBoxes.push(box);
            ctx.strokeStyle = rgba(colT, 0.55); ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(C[0], C[1]); ctx.lineTo(lift[0], by + 22); ctx.stroke();
            ctx.fillStyle = 'rgba(3,10,22,0.82)';
            ctx.shadowColor = rgba(colT, 0.7); ctx.shadowBlur = 12;
            ctx.beginPath();
            if (ctx.roundRect) ctx.roundRect(bx, by, bw, 22, 7); else ctx.rect(bx, by, bw, 22);
            ctx.fill(); ctx.shadowBlur = 0; ctx.stroke();
            ctx.fillStyle = 'rgba(232,241,255,0.95)'; ctx.fillText(t1, bx + 8, by + 11);
            ctx.fillStyle = rgba(colT, 1); ctx.fillText(t2, bx + 14 + w1, by + 11);
          }
        });
      }

      // Exchange beacons + labels (near side only).
      ctx.font = '700 ' + (W > 520 ? 10 : 9) + 'px "IBM Plex Mono", ui-monospace, monospace';
      ctx.textBaseline = 'middle';
      for (var ei = 0; ei < EXCH.length; ei++) {
        var ex = EXCH[ei], Pe = project(ex.v[0], ex.v[1], ex.v[2], R, cx, cy);
        if (Pe[2] < 0.12) continue;
        var colE = ex.live ? POS : GOLD, ph = reduceMotion ? 0.5 : (time * 0.8 + ei * 0.13) % 1;
        ctx.strokeStyle = rgba(colE, (1 - ph) * 0.8); ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.arc(Pe[0], Pe[1], (4 + ph * 14) * Pe[3], 0, 6.2832); ctx.stroke();
        ctx.fillStyle = rgba(colE, 1); ctx.shadowColor = rgba(colE, 1); ctx.shadowBlur = 12;
        ctx.beginPath(); ctx.arc(Pe[0], Pe[1], 2.6 * Pe[3], 0, 6.2832); ctx.fill();
        ctx.shadowBlur = 0;
        if (Pe[2] > 0.3) {
          var tag = ex.name + (ex.live ? ' · OPEN' : ' · CLOSED'), tw2 = ctx.measureText(tag).width;
          var tx2 = Pe[0] + 8, ty2 = Pe[1] - 10;
          if (!boxFree([tx2 - 4, ty2 - 8, tx2 + tw2 + 4, ty2 + 8])) { ty2 = Pe[1] + 12; if (!boxFree([tx2 - 4, ty2 - 8, tx2 + tw2 + 4, ty2 + 8])) continue; }
          tagBoxes.push([tx2 - 4, ty2 - 8, tx2 + tw2 + 4, ty2 + 8]);
          ctx.fillStyle = 'rgba(3,10,22,0.78)';
          ctx.strokeStyle = rgba(colE, 0.6); ctx.lineWidth = 1;
          ctx.beginPath();
          if (ctx.roundRect) ctx.roundRect(tx2 - 4, ty2 - 8, tw2 + 8, 16, 5); else ctx.rect(tx2 - 4, ty2 - 8, tw2 + 8, 16);
          ctx.fill(); ctx.stroke();
          ctx.fillStyle = rgba(colE, 1);
          ctx.fillText(tag, tx2, ty2);
        }
      }
      ctx.restore();

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
      var labelSet = null, sceneOn = document.documentElement.classList.contains('cc-scene');
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
        if (front && it[1][2] > 0.15 && (isSel || isHov || (!selected && (!labelSet || labelSet[it[2].symbol]) && (!sceneOn || it[1][2] > 0.35)))) {
          var label = it[2].symbol + (it[2].chg != null ? ' ' + (it[2].chg >= 0 ? '+' : '') + Number(it[2].chg).toFixed(2) + '%' : '');
          if (sceneOn && !isSel && !isHov) label = it[2].symbol;
          var lx2 = it[1][0] + 7, ly2 = it[1][1];
          var tw = ctx.measureText(label).width;
          if (lx2 + tw > W - 4) lx2 = it[1][0] - 7 - tw;
          if (sceneOn && !isSel && !isHov) {
            var lb = [lx2 - 3, ly2 - 7, lx2 + tw + 3, ly2 + 7];
            if (typeof boxFree === 'function' && !boxFree(lb)) continue;
            if (typeof tagBoxes !== 'undefined') tagBoxes.push(lb);
          }
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
