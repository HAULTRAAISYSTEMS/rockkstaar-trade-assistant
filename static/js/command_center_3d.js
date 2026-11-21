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
  var ALERT_GOLD = [245, 196, 81];   // price-alert shockwaves: always gold
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
      INDEX:  { name: 'INDEXES',       lat: 70,  lon: -42,  spread: 5, max: 9 },
      FUT:    { name: 'FUTURES · MACRO', lat: 8, lon: -32, spread: 5, max: 9 }
    };
    var ETF_REGION = { XLK: 'TECH', SMH: 'SEMIS', XLF: 'FIN', XLE: 'ENERGY', XLY: 'CONS', XLV: 'HEALTH',
                       XLC: 'COMM', XLI: 'INDUS', XLB: 'INDUS', XLU: 'OTHER', XLRE: 'OTHER', IWM: 'INDEX' };
    var SECTORS = {};
    try { SECTORS = JSON.parse(host.getAttribute('data-sectors') || '{}') || {}; } catch (e) { SECTORS = {}; }
    var useRegions = !!host.getAttribute('data-sectors');
    function regionFor(sym) {
      var u = String(sym || '').toUpperCase();
      if (/^(ES|NQ|YM|RTY|CL|GC|SI|NG|ZB|ZN|DXY|BTC.*|ETH.*)$/.test(u) || /[=\/]/.test(u)) return 'FUT';
      if (/^(SPY|QQQ|DIA|IWM|VIX|VOO|VTI|TLT|GLD|SLV|USO|UUP)$/.test(u) || /\^/.test(u)) return 'INDEX';
      return ETF_REGION[SECTORS[u]] || 'OTHER';
    }
    // Starred tickers (the orbit) always get a name tag on the globe.
    var starred = {}, chipSet = {}, chipRects = [];
    // Zoom + sector focus state.
    var zoom = 1, zoomTo = 1, ZMIN = 1, ZMAX = 3.6, focusKey = null, sectorHits = [], lastHidden = {}, zoomSent = 1;
    var shiftX = 0, shiftTo = 0;   // slide the globe right while the sector panel is open
    document.addEventListener('cmd27:sector', function (e) {
      var on = !!e.detail;
      // Measure after the panel is laid out: slide only by what it covers.
      requestAnimationFrame(function () {
        var panel = document.getElementById('cmd27-sector');
        if (!on || !panel || panel.hidden || W <= 520) { shiftTo = 0; return; }
        var pr = panel.getBoundingClientRect(), hr = canvas.getBoundingClientRect();
        var cover = Math.max(0, pr.right - hr.left);
        shiftTo = Math.min(cover * 0.5, W * 0.22);
      });
    });
    function clampZ(z) { return Math.max(ZMIN, Math.min(ZMAX, z)); }
    document.addEventListener('cmd27:zoom-cmd', function (e) {
      var d = e.detail && e.detail.dir;
      if (d === 'in') zoomTo = clampZ(zoomTo * 1.45);
      else if (d === 'out') { zoomTo = clampZ(zoomTo / 1.45); if (zoomTo <= 1.05) { zoomTo = 1; clearFocus(); } }
      else if (d === 'reset') { zoomTo = 1; clearFocus(); }
    });
    document.addEventListener('cmd27:sector-close', function () { clearFocus(); zoomTo = 1; });
    function clearFocus() {
      if (!focusKey) return;
      focusKey = null;
      document.dispatchEvent(new CustomEvent('cmd27:sector', { detail: null }));
    }
    // Spin + tilt (+ optional zoom) so a world point faces you.
    function flyToVec(v, z, done) {
      var tY = Math.atan2(-v[0], v[2]), z1 = Math.sqrt(v[0] * v[0] + v[2] * v[2]);
      var tX = Math.max(-1.1, Math.min(1.1, Math.atan2(v[1], z1)));
      var dY = Math.atan2(Math.sin(tY - rotY), Math.cos(tY - rotY));
      flyTo = { fromY: rotY, toY: rotY + dY, fromX: rotX, toX: tX, t: 0, done: done || null };
      if (z) zoomTo = clampZ(z);
      velY = 0;
    }
    function focusRegion(k) {
      var rg = REGIONS[k]; if (!rg) return;
      focusKey = k;
      var z = Math.max(1.7, Math.min(3.2, 26 / (rg.eff || rg.spread) + 0.9));
      flyToVec(geo(rg.lat, rg.lon), z);
      sendSector(k);
    }
    // The panel's rows/average — re-sent on every tape update without moving the camera.
    function sendSector(k) {
      var rg = REGIONS[k]; if (!rg) return;
      var rows = nodes.filter(function (nd) { return nd.region === k; })
        .map(function (nd) { return { symbol: nd.symbol, chg: nd.chg, price: nd.price }; })
        .sort(function (a, b) { return (b.chg == null ? -1e9 : b.chg) - (a.chg == null ? -1e9 : a.chg); });
      var known = rows.filter(function (r) { return r.chg != null && !/VIX/.test(r.symbol); });
      var avg = known.length ? known.reduce(function (t, r) { return t + Number(r.chg); }, 0) / known.length : null;
      document.dispatchEvent(new CustomEvent('cmd27:sector', { detail: { key: k, name: rg.name, avg: avg, rows: rows } }));
    }
    document.addEventListener('cmd27:sector-open', function (e) { if (e.detail && e.detail.key) focusRegion(e.detail.key); });
    // Clear space between the side panels (wide screens), from the page.
    var safeHalf = null;
    document.addEventListener('cmd27:safe', function (e) { safeHalf = (e.detail && e.detail.half) || null; });
    // My Portfolio view: pins become your holdings (sized by $ value).
    var pMode = false, lastTape = null;
    document.addEventListener('cmd27:portfolio', function (e) {
      var it = e.detail && e.detail.items;
      if (it && it.length) {
        pMode = true;
        it.forEach(function (h) { if (h.sector && !SECTORS[h.symbol]) SECTORS[h.symbol] = h.sector; });
        setData(it);
      } else if (pMode) {
        pMode = false;
        setData(lastTape || readTapeFromDom());
      }
    });
    // Shockwaves (alert hit / buy zone) and earnings this week.
    var flagged = {}, waves = [], EARN = {};
    try {
      JSON.parse(host.getAttribute('data-earnings') || '[]').forEach(function (ev) {
        var su = String(ev.s || '').toUpperCase(); if (su && (!EARN[su] || ev.d < EARN[su].d)) EARN[su] = ev;
      });
    } catch (e) { EARN = {}; }
    function earnDays(sym) {
      var ev = EARN[sym]; if (!ev) return null;
      var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(ev.d || '')); if (!m) return null;
      var et; try { et = new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' }); } catch (e2) { et = new Date().toISOString().slice(0, 10); }
      var d = Math.round((Date.UTC(+m[1], +m[2] - 1, +m[3]) - Date.UTC(+et.slice(0, 4), +et.slice(5, 7) - 1, +et.slice(8, 10))) / 864e5);
      return d >= 0 && d <= 7 ? d : null;
    }
    document.addEventListener('cmd27:flags', function (e) {
      var d = e.detail || {}; flagged = {};
      Object.keys(d.zone || {}).forEach(function (k) { flagged[k] = 'zone'; });
      Object.keys(d.alert || {}).forEach(function (k) { flagged[k] = 'alert'; });
      var t0 = performance.now() / 1000;
      (d.fresh || []).forEach(function (f, i) { waves.push({ symbol: String(f.symbol).toUpperCase(), kind: f.kind, t0: t0 + i * 0.35 }); });
      var lg = document.getElementById('cmd27-legend');
      if (lg) lg.classList.toggle('has-wave', Object.keys(flagged).length > 0);
    });
    (function () {
      var lg = document.getElementById('cmd27-legend');
      if (lg) lg.classList.toggle('has-earn', Object.keys(EARN).some(function (k) { return earnDays(k) != null; }));
    })();
    document.addEventListener('cmd27:chips', function (e) {
      chipSet = {}; ((e.detail && e.detail.tickers) || []).forEach(function (t) { chipSet[String(t).toUpperCase()] = 1; });
      chipRects = (e.detail && e.detail.rects) || [];
    });
    document.addEventListener('cmd27:orbit', function (e) {
      starred = {}; ((e.detail && e.detail.tickers) || []).forEach(function (t) { starred[String(t).toUpperCase()] = 1; });
      if (typeof selected !== 'undefined' && selected && typeof renderCard === 'function') renderCard();
    });
    function placeInRegions(list) {
      var groups = {};
      list.forEach(function (t) { var k = regionFor(t.symbol); (groups[k] = groups[k] || []).push(t); });
      var pos = {};
      Object.keys(groups).forEach(function (k) {
        // Biggest movers sit at the heart of the territory; the territory
        // grows with its pin count so pins never stack.
        var g = groups[k].slice().sort(function (a, b) { return Math.abs(b.change_pct || 0) - Math.abs(a.change_pct || 0); });
        var rg = REGIONS[k], n = g.length;
        var spread = Math.min(rg.max || 20, Math.max(rg.spread, 2.7 * Math.sqrt(n)));
        rg.eff = spread;
        g.forEach(function (t, i) {
          var rr = n === 1 ? 0 : spread * Math.sqrt((i + 0.5) / n), a = i * 2.39996;
          var la = rg.lat + rr * Math.cos(a);
          var lo = rg.lon + rr * Math.sin(a) / Math.max(0.3, Math.cos(la * 0.0174533));
          pos[t.symbol] = { v: geo(la, lo), region: k, rank: i };
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
    var eImg = null;
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

    /* Viewport renderer: one ray per screen pixel of the canvas (not per
       pixel of the disc), so zooming in stays sharp and costs the same.
       Rays and the lat/lon lookup are cached per (size, R, tilt); a frame
       is then a texture lookup plus lighting. Spinning only shifts
       longitude, so it never invalidates the cache. */
    var eW = 0, eH = 0, eS = 1, eKey = '', eRay = null, eRow = null, eCol = null, lowres = false, eBox = [0, 0, 0, 0];
    // Adaptive quality: a device that struggles gets a softer Earth, not a stutter.
    var eQuality = 1, eCost = 0, eFrames = 0;
    function buildViewport(R, cx0, cy0) {
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var budget = (window.innerWidth < 760 ? 260000 : 520000) * eQuality / (lowres ? 4 : 1);
      eS = Math.min(dpr, Math.sqrt(budget / Math.max(1, W * H)));
      eW = Math.max(1, Math.round(W * eS)); eH = Math.max(1, Math.round(H * eS));
      if (eCanvas.width !== eW || eCanvas.height !== eH) { eCanvas.width = eW; eCanvas.height = eH; }
      eImg = eCtx.createImageData(eW, eH);
      var n = eW * eH;
      eRay = new Float32Array(n * 4); eRow = new Int32Array(n); eCol = new Float32Array(n);
      var cX = Math.cos(rotX), sX = Math.sin(rotX), PI = Math.PI, aa = 1.5 / (R * eS);
      // Only the silhouette's bounding box can hold Earth pixels.
      var rS = R * EARTH_SIL * 1.02;
      eBox = [Math.max(0, Math.floor((cx0 - rS) * eS)), Math.max(0, Math.floor((cy0 - rS) * eS)),
              Math.min(eW, Math.ceil((cx0 + rS) * eS)), Math.min(eH, Math.ceil((cy0 + rS) * eS))];
      for (var j = eBox[1]; j < eBox[3]; j++) {
        for (var i = eBox[0]; i < eBox[2]; i++) {
          var p = j * eW + i, o = p * 4;
          var u = ((i + 0.5) / eS - cx0) / R, v = ((j + 0.5) / eS - cy0) / R;
          var k = (u * u + v * v) / 10.24;
          var A = k + 1, B = -6.4 * k, C = 10.24 * k - 1, disc = B * B - 4 * A * C;
          if (disc < 0) { eRay[o + 3] = 0; continue; }
          var z = (-B + Math.sqrt(disc)) / (2 * A), f = (3.2 - z) / 3.2;
          var x1 = u * f, y1 = v * f;
          eRay[o] = x1; eRay[o + 1] = y1; eRay[o + 2] = z;
          var rr = Math.sqrt(u * u + v * v) / EARTH_SIL;
          eRay[o + 3] = Math.max(0.001, Math.min(1, (1 - rr) / aa));
          var y = y1 * cX + z * sX, z1 = -y1 * sX + z * cX;
          var lat = Math.asin(y < -1 ? 1 : (y > 1 ? -1 : -y));
          var ty = ((PI / 2 - lat) / PI * TH) | 0; if (ty >= TH) ty = TH - 1;
          eRow[p] = ty * TW;
          eCol[p] = (PI - Math.atan2(z1, x1)) / (2 * PI) * TW;
        }
      }
    }

    function drawEarth(cx, cy, R) {
      if (!tex) return false;
      // Rays are built around the resting centre; pointer parallax is a shift.
      var cx0 = W / 2 + shiftX, cy0 = H * 0.47;
      var t0 = performance.now();
      var key = W + 'x' + H + ':' + R.toFixed(1) + ':' + rotX.toFixed(4) + ':' + shiftX.toFixed(1) + ':' + eQuality.toFixed(2) + (lowres ? ':lo' : '');
      if (key !== eKey) { buildViewport(R, cx0, cy0); eKey = key; }
      var data = eImg.data, ray = eRay, row = eRow, col = eCol;
      var Lx = sunCam[0], Ly = sunCam[1], Lz = sunCam[2];
      var Hx = Lx, Hy = Ly, Hz = Lz + 1, hl = Math.sqrt(Hx * Hx + Hy * Hy + Hz * Hz) || 1; Hx /= hl; Hy /= hl; Hz /= hl;
      var tw = TW, rowB = TW * TH * 4;
      var shift = (rotY / (2 * Math.PI)) * tw; shift = shift - Math.floor(shift / tw) * tw;
      for (var jj = eBox[1]; jj < eBox[3]; jj++) for (var p = jj * eW + eBox[0], pe = jj * eW + eBox[2]; p < pe; p++) {
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
      // Push only the box that holds the Earth.
      var bw0 = eBox[2] - eBox[0], bh0 = eBox[3] - eBox[1];
      if (bw0 > 0 && bh0 > 0) {
        eCtx.clearRect(0, 0, eW, eH);
        eCtx.putImageData(eImg, 0, 0, eBox[0], eBox[1], bw0, bh0);
      }
      ctx.drawImage(eCanvas, cx - cx0, cy - cy0, W, H);
      // (cx0 already includes the slide; the offset is only pointer parallax)
      if (!lowres) {
        eCost = eCost * 0.9 + (performance.now() - t0) * 0.1;
        if (++eFrames > 30) {
          eFrames = 0;
          if (eCost > 11 && eQuality > 0.4) eQuality = Math.max(0.4, eQuality * 0.8);
          else if (eCost < 4 && eQuality < 1) eQuality = Math.min(1, eQuality * 1.15);
        }
      }
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
          value: t.value, weight: t.weight, dollars: t.dollars, earn: earnDays(String(t.symbol).toUpperCase()),
          region: pp ? pp.region : null, rank: pp ? pp.rank : 0,
          x: pp ? pp.v[0] : Math.cos(th) * rad, y: pp ? pp.v[1] : y * 0.92, z: pp ? pp.v[2] : Math.sin(th) * rad,
          flash: prev && prev.price !== t.price ? 1 : (prev ? prev.flash : 0.6)
        };
      });
      if (useRegions) {
        var sum = {};
        nodes.forEach(function (nd) {
          if (!nd.region) return;
          var st = sum[nd.region] = sum[nd.region] || { key: nd.region, name: REGIONS[nd.region].name, n: 0, s: 0, k: 0 };
          st.n++; if (nd.chg != null && !/VIX/.test(nd.symbol)) { st.s += Number(nd.chg); st.k++; }
        });
        var list2 = Object.keys(sum).map(function (k) { var st = sum[k]; return { key: k, name: st.name, n: st.n, avg: st.k ? st.s / st.k : null }; })
          .sort(function (a, b) { return b.n - a.n; });
        document.dispatchEvent(new CustomEvent('cmd27:regions', { detail: list2 }));
        if (focusKey) sendSector(focusKey);
      }
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
      document.addEventListener('tradestaar:tape', function (e) { lastTape = e.detail; if (!pMode) setData(e.detail); });
    }

    // ── Interaction: drag spins; a tap (barely moves) picks a ticker ──
    var hits = [];            // screen positions of near-side nodes, rebuilt every frame
    var hovered = null, selected = null;
    var downX = 0, downY = 0, downT = 0, moved = 0, ptrs = {}, pinch0 = null, lastTap = 0;
    var _tw = {}, _twN = 0;
    function textW(key, txt) {
      var v = _tw[key]; if (v !== undefined) return v;
      if (++_twN > 2000) { _tw = {}; _twN = 0; }
      return (_tw[key] = ctx.measureText(txt).width);
    }
    function ptrsList() { return Object.keys(ptrs).map(function (k) { return ptrs[k]; }); }
    function pinching() { return !!pinch0; }
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
        ptrs[e.pointerId] = { x: e.clientX, y: e.clientY };
        if (useRegions && Object.keys(ptrs).length === 2) {
          var pp = ptrsList(); pinch0 = { d: Math.hypot(pp[0].x - pp[1].x, pp[0].y - pp[1].y) || 1, z: zoomTo };
          dragging = false; moved = 99; return;
        }
        dragging = true; lx = downX = e.clientX; ly = downY = e.clientY; downT = Date.now(); moved = 0;
        host.setPointerCapture && host.setPointerCapture(e.pointerId);
      });
      host.addEventListener('pointermove', function (e) {
        if (ptrs[e.pointerId]) { ptrs[e.pointerId].x = e.clientX; ptrs[e.pointerId].y = e.clientY; }
        if (pinch0 && Object.keys(ptrs).length >= 2) {
          var pq = ptrsList(), dd = Math.hypot(pq[0].x - pq[1].x, pq[0].y - pq[1].y);
          zoomTo = clampZ(pinch0.z * dd / pinch0.d); zoom = zoomTo;
          if (zoomTo <= 1.02) clearFocus();
          if (selected) closeCard();
          return;
        }
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
        var zf = 1 / zoom;
        rotY += dx * 0.008 * zf; rotX = Math.max(-1.1, Math.min(1.1, rotX + dy * 0.006 * zf));
        velY = dx * 0.4 * zf;
        if (reduceMotion) draw(0, 0);
      });
      host.addEventListener('pointerup', function (e) {
        delete ptrs[e.pointerId];
        if (Object.keys(ptrs).length < 2) pinch0 = null;
        if (e.target.closest('.cc3d-card')) return;
        var wasTap = dragging && moved < 6 && Date.now() - downT < 600;
        dragging = false;
        if (wasTap) {
          var r0 = canvas.getBoundingClientRect(), tx0 = e.clientX - r0.left, ty0 = e.clientY - r0.top;
          // Sector tag → fly into that sector.
          for (var si = 0; si < sectorHits.length; si++) {
            var bx = sectorHits[si].box;
            if (tx0 >= bx[0] - 4 && tx0 <= bx[2] + 4 && ty0 >= bx[1] - 4 && ty0 <= bx[3] + 4) { lastTap = 0; focusRegion(sectorHits[si].key); return; }
          }
          var h = nearest(e.clientX, e.clientY, e.pointerType === 'mouse' ? 16 : 26);
          if (h) { lastTap = 0; openCard(h.symbol); return; }
          // Double-tap on open globe: zoom in; double-tap when deep: back out.
          var now = Date.now();
          if (useRegions && now - lastTap < 320) { lastTap = 0; if (zoomTo >= 3) { zoomTo = 1; clearFocus(); } else zoomTo = clampZ(zoomTo * 1.6); return; }
          lastTap = now;
          closeCard();
        } else if (!selected && !reduceMotion && Math.abs(velY) < 0.06 && zoomTo <= 1.15 && !focusKey) velY = 0.1;
      });
      host.addEventListener('pointercancel', function (e) { delete ptrs[e.pointerId]; pinch0 = null; dragging = false; });
      // Trackpad pinch (ctrl+wheel) always zooms; a plain wheel zooms only
      // once you are already zoomed in, so the page still scrolls normally.
      host.addEventListener('wheel', function (e) {
        if (!useRegions || !(e.ctrlKey || zoomTo > 1.01)) return;
        e.preventDefault();
        zoomTo = clampZ(zoomTo * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0018)));
        if (zoomTo <= 1.02) { zoomTo = 1; clearFocus(); }
      }, { passive: false });
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
      var sector = document.createElement('small'); sector.className = 'cc3d-card__sector';
      sector.textContent = nd.region && REGIONS[nd.region] ? REGIONS[nd.region].name : '';
      head.append(sym, sector, close);
      var quote = document.createElement('div'); quote.className = 'cc3d-card__quote';
      var px = document.createElement('span'); px.className = 'cc3d-card__px';
      px.textContent = nd.price != null ? Number(nd.price).toFixed(2) : '—';
      var ch = document.createElement('span');
      ch.className = 'cc3d-card__chg ' + (nd.chg == null ? 'is-unknown' : (nd.chg > 0 ? 'is-up' : (nd.chg < 0 ? 'is-down' : '')));
      ch.textContent = nd.chg != null ? (nd.chg >= 0 ? '+' : '') + Number(nd.chg).toFixed(2) + '%' : '—';
      quote.append(px, ch);
      var src = document.createElement('small'); src.className = 'cc3d-card__src';
      src.textContent = 'From the live tape' + (lastSync ? ' · synced ' + new Date(lastSync).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '');
      if (pMode && nd.value != null) {
        var usd = function (v, sign) { var a = Math.abs(v); return (sign ? (v > 0 ? '+' : v < 0 ? '−' : '') : '') + '$' + a.toLocaleString('en-US', { maximumFractionDigits: a >= 1000 ? 0 : 2 }); };
        src.textContent = usd(nd.value) + (nd.weight != null ? ' · ' + Number(nd.weight).toFixed(1) + '% of your portfolio' : '') +
          (nd.dollars != null ? ' · ' + usd(nd.dollars, true) + ' today' : '');
      }
      if (nd.earn != null) src.textContent += ' · reports ' + (nd.earn === 0 ? 'today' : nd.earn === 1 ? 'tomorrow' : 'in ' + nd.earn + ' days');
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
      if (sceneMode()) {
        var on = !!starred[nd.symbol];
        var st = document.createElement('button'); st.type = 'button';
        st.className = 'cc3d-card__btn cc3d-card__star' + (on ? ' is-on' : '');
        st.textContent = on ? '★ In your orbit' : '☆ Add to orbit';
        st.title = on ? 'Remove ' + nd.symbol + ' from the chips around the globe' : 'Keep ' + nd.symbol + ' in the chips around the globe';
        st.addEventListener('click', function () {
          document.dispatchEvent(new CustomEvent('cmd27:orbit-toggle', { detail: { symbol: nd.symbol } }));
        });
        acts.insertBefore(st, acts.firstChild);
      }
      card.append(head, quote, src, acts);
    }
    function sceneMode() { return document.documentElement.classList.contains('cc-scene') && useRegions; }
    // Fly to a ticker: spin the globe until its pin faces you, then open its card.
    var flyTo = null;
    document.addEventListener('cmd27:focus', function (e) {
      var sym = String((e.detail && e.detail.symbol) || '').toUpperCase(), nd = nodeFor(sym);
      if (!nd) return;
      flyToVec([nd.x, nd.y, nd.z], Math.max(zoomTo, 1.8), function () { openCard(sym); });
    });
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
      if (flyTo) {
        flyTo.t = Math.min(1, flyTo.t + (reduceMotion ? 1 : dt / 1.1));
        var ez = flyTo.t < 0.5 ? 2 * flyTo.t * flyTo.t : 1 - Math.pow(-2 * flyTo.t + 2, 2) / 2;
        rotY = flyTo.fromY + (flyTo.toY - flyTo.fromY) * ez;
        rotX = flyTo.fromX + (flyTo.toX - flyTo.fromX) * ez;
        if (flyTo.t >= 1) { var fdone = flyTo.done; flyTo = null; if (fdone) fdone(); }
      } else if (!dragging && !reduceMotion) {
        // Zoomed in or focused on a sector: hold still so it can be read.
        var cruise = selected || focusKey || zoomTo > 1.15 ? 0 : 0.1;
        rotY += velY * dt; velY += (cruise - velY) * (selected ? 0.2 : 0.02);
      }
      if (zoom !== zoomTo) { zoom += (zoomTo - zoom) * Math.min(1, (reduceMotion ? 1 : dt * 6)); if (Math.abs(zoomTo - zoom) < 0.002) zoom = zoomTo; }
      if (shiftX !== shiftTo) { shiftX += (shiftTo - shiftX) * Math.min(1, (reduceMotion ? 1 : dt * 6)); if (Math.abs(shiftTo - shiftX) < 0.5) shiftX = shiftTo; }
      lowres = dragging || !!flyTo || zoom !== zoomTo || shiftX !== shiftTo || pinching();
      if (Math.abs(zoom - zoomSent) > 0.04 || (zoom === zoomTo && zoomSent !== zoom)) {
        zoomSent = zoom;
        host.classList.toggle('is-zoomed', zoom > 1.15);
        document.dispatchEvent(new CustomEvent('cmd27:zoom', { detail: { zoom: zoom } }));
      }
      ctx.clearRect(0, 0, W, H);
      // Bigger canvas, bigger globe: fill more of it once there is room.
      var R = Math.min(W, H) * (Math.min(W, H) > 420 ? 0.35 : 0.33);
      // With orbit chips around it, the globe also leaves a clear ring for
      // them sideways (narrow columns: iPad landscape, phones).
      if (useRegions) R = safeHalf ? Math.min(R, (safeHalf - 70) / 1.053) : Math.min(R, W * 0.30);
      R *= zoom;
      var cx = W / 2 + shiftX + pointer.x * 6, cy = H * 0.47 + pointer.y * 6;

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

      if (zoom < 1.25) {
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

      }
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
      // Orbit chips are obstacles: tags and names steer around them.
      var tagBoxes = useRegions && zoom < 1.2 ? chipRects.slice() : [];
      sectorHits = [];
      function boxFree(b) {
        for (var q = 0; q < tagBoxes.length; q++) { var o = tagBoxes[q]; if (b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]) return false; }
        return true;
      }
      // Sector territories: a glowing border on the land + a live tag.
      if (useRegions && nodes.length) {
        var stats = {};
        nodes.forEach(function (nd) {
          if (!nd.region) return;
          var st = stats[nd.region] = stats[nd.region] || { n: 0, sum: 0, k: 0, w: 0 };
          st.n++; if (pMode && nd.weight != null) st.w += Number(nd.weight);
          // VIX moves opposite to stocks, so it stays a pin but not part of the average.
          if (nd.chg != null && !/VIX/.test(String(nd.symbol).toUpperCase())) { st.sum += Number(nd.chg); st.k++; }
        });
        Object.keys(stats).forEach(function (k) {
          var rg = REGIONS[k], st = stats[k], avg = st.k ? st.sum / st.k : null;
          var colT = avg == null ? CYAN : (avg > 0.05 ? POS : (avg < -0.05 ? NEG : FLAT));
          var cen = geo(rg.lat, rg.lon), C = project(cen[0], cen[1], cen[2], R, cx, cy);
          if (C[2] < -0.1) return;
          // Border: small circle of radius spread+4° around the centre.
          var ang = ((rg.eff || rg.spread) + 3) * 0.0174533, ca = Math.cos(ang), sa = Math.sin(ang);
          var up = Math.abs(cen[1]) > 0.95 ? [1, 0, 0] : [0, 1, 0];
          var e1 = [cen[1] * up[2] - cen[2] * up[1], cen[2] * up[0] - cen[0] * up[2], cen[0] * up[1] - cen[1] * up[0]];
          var l1 = Math.sqrt(e1[0] * e1[0] + e1[1] * e1[1] + e1[2] * e1[2]); e1 = [e1[0] / l1, e1[1] / l1, e1[2] / l1];
          var e2 = [cen[1] * e1[2] - cen[2] * e1[1], cen[2] * e1[0] - cen[0] * e1[2], cen[0] * e1[1] - cen[1] * e1[0]];
          var dash = reduceMotion ? 0 : time * 0.6, isFocus = focusKey === k, dimT = focusKey && !isFocus ? 0.3 : 1;
          var ring = [], frontN = 0;
          for (var bs = 0; bs <= 64; bs++) {
            var th2 = bs / 64 * 6.2832, c2 = Math.cos(th2), s2 = Math.sin(th2);
            var pv = [cen[0] * ca + (e1[0] * c2 + e2[0] * s2) * sa, cen[1] * ca + (e1[1] * c2 + e2[1] * s2) * sa, cen[2] * ca + (e1[2] * c2 + e2[2] * s2) * sa];
            var PT = project(pv[0] * 1.004, pv[1] * 1.004, pv[2] * 1.004, R, cx, cy);
            ring.push(PT); if (PT[2] > 0) frontN++;
          }
          // Heat: the territory glows with its sector's average move.
          if (frontN > 52 && avg != null) {
            var heat = Math.min(Math.abs(avg) / 3, 1);
            ctx.save();
            ctx.beginPath(); ctx.arc(cx, cy, R * EARTH_SIL, 0, 6.2832); ctx.clip();
            ctx.beginPath();
            ring.forEach(function (q, qi) { if (qi) ctx.lineTo(q[0], q[1]); else ctx.moveTo(q[0], q[1]); });
            ctx.closePath();
            ctx.fillStyle = rgba(colT, (0.07 + 0.16 * heat) * dimT * (isFocus ? 1.4 : 1)); ctx.fill();
            ctx.restore();
          }
          ctx.lineWidth = isFocus ? 2.2 : 1.6;
          ctx.shadowColor = rgba(colT, 0.9); ctx.shadowBlur = isFocus ? 14 : 8;
          for (var bq = 1; bq < ring.length; bq++) {
            var PT2 = ring[bq], prevT = ring[bq - 1];
            if (PT2[2] > 0 && prevT[2] > 0 && (isFocus || (bq + Math.floor(dash * 4)) % 4 !== 0)) {
              ctx.strokeStyle = rgba(colT, (isFocus ? 0.9 : 0.5) * dimT * Math.min(1, PT2[2] * 2));
              ctx.beginPath(); ctx.moveTo(prevT[0], prevT[1]); ctx.lineTo(PT2[0], PT2[1]); ctx.stroke();
            }
          }
          ctx.shadowBlur = 0;
          // Floating tag above the territory.
          if (C[2] > 0.2) {
            var lift = project(cen[0] * 1.16, cen[1] * 1.16, cen[2] * 1.16, R, cx, cy);
            ctx.font = '800 ' + (W > 520 ? 11 : 10) + 'px "IBM Plex Mono", ui-monospace, monospace';
            ctx.textBaseline = 'middle';
            var more = lastHidden[k] || 0;
            var t1 = rg.name, t2 = (avg == null ? st.n + ' pins' : (avg >= 0 ? '+' : '') + avg.toFixed(2) + '%') + (more ? '  ·  +' + more : '');
            // My Portfolio: the territory tag leads with your allocation there.
            if (pMode) t1 = rg.name + '  ' + (st.w >= 10 ? st.w.toFixed(0) : st.w.toFixed(1)) + '%';
            var w1 = ctx.measureText(t1).width, w2 = ctx.measureText(t2).width, bw = w1 + w2 + 22;
            var bx = lift[0] - bw / 2, by = lift[1] - 11, box = [bx, by, bx + bw, by + 22];
            if (!boxFree(box)) return;
            tagBoxes.push(box);
            sectorHits.push({ key: k, box: box });
            ctx.globalAlpha = dimT;
            ctx.strokeStyle = rgba(colT, 0.55); ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(C[0], C[1]); ctx.lineTo(lift[0], by + 22); ctx.stroke();
            ctx.fillStyle = 'rgba(3,10,22,0.82)';
            ctx.shadowColor = rgba(colT, 0.7); ctx.shadowBlur = 12;
            ctx.beginPath();
            if (ctx.roundRect) ctx.roundRect(bx, by, bw, 22, 7); else ctx.rect(bx, by, bw, 22);
            ctx.fill(); ctx.shadowBlur = 0; ctx.stroke();
            ctx.fillStyle = 'rgba(232,241,255,0.95)'; ctx.fillText(t1, bx + 8, by + 11);
            ctx.fillStyle = rgba(colT, 1); ctx.fillText(t2, bx + 14 + w1, by + 11);
            ctx.globalAlpha = 1;
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
        // Closed exchanges keep their beacon; only open ones carry a tag.
        if (Pe[2] > 0.3 && ex.live) {
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

      if (waves.length) waves = waves.filter(function (wv) { return time - wv.t0 < 3.4; });
      var drawn = [], maxW = 0;
      if (pMode) nodes.forEach(function (q) { if (q.weight > maxW) maxW = q.weight; });
      for (var n = 0; n < nodes.length; n++) {
        var nd = nodes[n];
        // Market: spike = size of today's move. Portfolio: spike = $ size of the position.
        var mag = pMode ? (maxW > 0 && nd.weight > 0 ? Math.sqrt(nd.weight / maxW) : 0.1)
          : (nd.chg == null ? 0 : Math.min(Math.abs(nd.chg), 6) / 6);
        var col = nd.chg == null ? FLAT : (nd.chg > 0 ? POS : (nd.chg < 0 ? NEG : FLAT));
        var base = project(nd.x, nd.y, nd.z, R, cx, cy);
        var k = 1 + 0.06 + mag * (pMode ? 0.42 : 0.30);
        var tip = project(nd.x * k, nd.y * k, nd.z * k, R, cx, cy);
        drawn.push([base, tip, nd, col, mag]);
      }
      drawn.sort(function (a, b) { return a[1][2] - b[1][2]; });
      var labelSet = null, sceneOn = useRegions && document.documentElement.classList.contains('cc-scene');
      if (!sceneOn && drawn.length > 16) {
        labelSet = {};
        drawn.filter(function (it) { return it[1][2] > 0.15 && it[2].chg != null; })
          .sort(function (a, b) { return Math.abs(b[2].chg) - Math.abs(a[2].chg); })
          .slice(0, W > 520 ? 18 : 12)
          .forEach(function (it) { labelSet[it[2].symbol] = 1; });
      }
      hits = [];
      var cands = [];
      for (var d = 0; d < drawn.length; d++) {
        var it = drawn[d], front = it[1][2] > -0.1, A = front ? 1 : 0.25;
        var isSel = selected === it[2].symbol, isHov = hovered === it[2].symbol;
        if (selected && !isSel) A *= 0.45;
        if (focusKey && it[2].region !== focusKey) A *= 0.35;
        if (front && it[1][2] > 0) hits.push({ symbol: it[2].symbol, x: it[1][0], y: it[1][1] });
        it[2].flash = Math.max(0, it[2].flash - dt * 0.6);
        // Tower: footprint ring on the surface, glowing shaft, bright cap.
        if (sceneOn && front) {
          ctx.strokeStyle = rgba(it[3], 0.45 * A); ctx.lineWidth = 1;
          ctx.beginPath(); ctx.ellipse(it[0][0], it[0][1], 3.2 * it[0][3] * Math.min(zoom, 2.2), 1.6 * it[0][3] * Math.min(zoom, 2.2), 0, 0, 6.2832); ctx.stroke();
          ctx.strokeStyle = rgba(it[3], 0.22 * A); ctx.lineWidth = 4 + Math.min(zoom - 1, 1.5) * 2;
          ctx.beginPath(); ctx.moveTo(it[0][0], it[0][1]); ctx.lineTo(it[1][0], it[1][1]); ctx.stroke();
          ctx.strokeStyle = rgba(it[3], 0.9 * A); ctx.lineWidth = 1.3 + Math.min(zoom - 1, 1.5) * 0.6;
        } else {
          ctx.strokeStyle = rgba(it[3], 0.75 * A); ctx.lineWidth = 1.4;
        }
        ctx.beginPath(); ctx.moveTo(it[0][0], it[0][1]); ctx.lineTo(it[1][0], it[1][1]); ctx.stroke();
        var rad = (2 + it[4] * (pMode ? 4.5 : 2.5)) * it[1][3] * (sceneOn ? Math.min(1 + (zoom - 1) * 0.5, 1.8) : 1);
        ctx.fillStyle = rgba(it[3], A);
        ctx.shadowColor = rgba(it[3], 0.9 * A); ctx.shadowBlur = front ? 10 + it[2].flash * 20 : 0;
        ctx.beginPath(); ctx.arc(it[1][0], it[1][1], rad * (isSel || isHov ? 1.8 : 1), 0, 6.2832); ctx.fill();
        ctx.shadowBlur = 0;
        if (sceneOn && (starred[it[2].symbol] || chipSet[it[2].symbol]) && front) {
          ctx.strokeStyle = rgba(starred[it[2].symbol] ? GOLD : CYAN, 0.9 * A); ctx.lineWidth = 1.4;
          ctx.beginPath(); ctx.arc(it[1][0], it[1][1], rad + 4, 0, 6.2832); ctx.stroke();
        }
        if (front && it[1][2] > 0) {
          var symU = it[2].symbol, sc = Math.min(zoom, 2.2) * it[0][3];
          // Shockwave: three rings race out from the base the first time a
          // price alert fires or the stock enters its buy zone…
          for (var wi = 0; wi < waves.length; wi++) {
            var wv = waves[wi]; if (wv.symbol !== symU) continue;
            var wcol = wv.kind === 'alert' ? ALERT_GOLD : POS;
            for (var rr2 = 0; rr2 < 3; rr2++) {
              var age = time - wv.t0 - rr2 * 0.45;
              if (age < 0 || age > 2.2) continue;
              var wr = (6 + age * 62) * sc, wa = (1 - age / 2.2);
              ctx.strokeStyle = rgba(wcol, 0.9 * wa * A); ctx.lineWidth = 2.4 * wa + 0.6;
              ctx.shadowColor = rgba(wcol, 0.9 * wa); ctx.shadowBlur = 14 * wa;
              ctx.beginPath(); ctx.ellipse(it[0][0], it[0][1], wr, wr * 0.62, 0, 0, 6.2832); ctx.stroke();
            }
            ctx.shadowBlur = 0;
            if (time - wv.t0 < 1.2) {   // a flash up the tower
              ctx.strokeStyle = rgba(wcol, (1 - (time - wv.t0) / 1.2) * A); ctx.lineWidth = 3;
              ctx.beginPath(); ctx.moveTo(it[0][0], it[0][1]); ctx.lineTo(it[1][0], it[1][1]); ctx.stroke();
            }
          }
          // …then a soft echo every few seconds while it stays true.
          var fk = flagged[symU];
          if (fk && !reduceMotion) {
            var ph2 = ((time + (symU.charCodeAt(0) % 7) * 0.4) % 4.5) / 4.5;
            if (ph2 < 0.55) {
              var er = (5 + ph2 / 0.55 * 34) * sc, ea = 1 - ph2 / 0.55;
              ctx.strokeStyle = rgba(fk === 'alert' ? ALERT_GOLD : POS, 0.6 * ea * A); ctx.lineWidth = 1.6;
              ctx.beginPath(); ctx.ellipse(it[0][0], it[0][1], er, er * 0.62, 0, 0, 6.2832); ctx.stroke();
            }
          }
          // Earnings this week: a dashed violet ring that pulses on the cap.
          if (it[2].earn != null) {
            var pulse2 = reduceMotion ? 0 : Math.sin(time * 3.2) * 1.6;
            ctx.save();
            ctx.setLineDash([3, 3]); ctx.lineDashOffset = reduceMotion ? 0 : -time * 8;
            ctx.strokeStyle = rgba(VIOLET, 0.95 * A); ctx.lineWidth = 1.6;
            ctx.shadowColor = rgba(VIOLET, 0.9); ctx.shadowBlur = 10;
            ctx.beginPath(); ctx.arc(it[1][0], it[1][1], rad + 7 + pulse2, 0, 6.2832); ctx.stroke();
            ctx.restore();
          }
        }
        if (isSel) {
          var pr = rad * 2.6 + 4 + Math.sin(time * 4) * 2;
          ctx.strokeStyle = rgba(GOLD, 0.95); ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(it[1][0], it[1][1], pr, 0, 6.2832); ctx.stroke();
        }
        if (sceneOn) {
          // A stock that is an orbit chip keeps its gold ring, not a second name tag.
          if (front && it[1][2] > 0.12 && (isSel || isHov || zoom >= 1.2 || !chipSet[it[2].symbol])) {
            var pri = (isSel || isHov ? 1e4 : 0) + (starred[it[2].symbol] ? 500 : 0) + (focusKey && it[2].region === focusKey ? 300 : 0)
              + (flagged[it[2].symbol] ? 400 : 0) + (it[2].earn != null ? 250 : 0) + (pMode ? (it[2].weight || 0) * 20 : 0)
              + Math.abs(it[2].chg || 0) * 10 + it[1][2] * 5;
            cands.push({ it: it, pri: pri, sel: isSel || isHov, A: A });
          }
        } else if (front && it[1][2] > 0.15 && (isSel || isHov || (!selected && (!labelSet || labelSet[it[2].symbol])))) {
          ctx.font = '600 ' + (W > 520 ? 12 : 10) + 'px "IBM Plex Mono", ui-monospace, monospace';
          ctx.textBaseline = 'middle';
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
      // Ticker tags on the towers, most important first; when two would
      // collide the less important one waits (zoom in to reveal it).
      if (sceneOn) {
        cands.sort(function (a, b) { return b.pri - a.pri; });
        var fs = Math.round(Math.min(10.5 + (zoom - 1) * 1.6, 13.5) * (W > 520 ? 1 : 0.92));
        ctx.font = '700 ' + fs + 'px "IBM Plex Mono", ui-monospace, monospace';
        ctx.textBaseline = 'middle';
        var hiddenNow = {}, fkey = fs + '|';
        for (var c = 0; c < cands.length; c++) {
          var cd = cands[c], nd2 = cd.it[2], tip2 = cd.it[1];
          var showPct = cd.sel || zoom >= 1.6 || starred[nd2.symbol];
          var txt = nd2.symbol + (pMode && nd2.weight != null ? ' ' + Number(nd2.weight).toFixed(nd2.weight < 10 ? 1 : 0) + '%'
            : (showPct && nd2.chg != null ? ' ' + (nd2.chg >= 0 ? '+' : '') + Number(nd2.chg).toFixed(2) + '%' : ''));
          var eTxt = nd2.earn != null ? (nd2.earn === 0 ? ' TODAY' : ' ' + nd2.earn + 'd') : '';
          txt += eTxt;
          var tw3 = textW(fkey + txt, txt), ph = fs + 7, pw = tw3 + 12;
          var spots = [[tip2[0] - pw / 2, tip2[1] - ph - 6], [tip2[0] + 8, tip2[1] - ph / 2], [tip2[0] - pw - 8, tip2[1] - ph / 2], [tip2[0] - pw / 2, tip2[1] + 6]];
          var placed = null;
          for (var sp2 = 0; sp2 < spots.length; sp2++) {
            var bx2 = [spots[sp2][0], spots[sp2][1], spots[sp2][0] + pw, spots[sp2][1] + ph];
            if (bx2[0] < 2 || bx2[2] > W - 2 || bx2[1] < 2 || bx2[3] > H - 2) continue;
            if (cd.sel || boxFree(bx2)) { placed = bx2; break; }
          }
          if (!placed) { if (nd2.region) hiddenNow[nd2.region] = (hiddenNow[nd2.region] || 0) + 1; continue; }
          tagBoxes.push(placed);
          var colL = cd.it[3], aL = Math.max(0.35, cd.A);
          ctx.fillStyle = 'rgba(3,10,22,' + (0.86 * aL).toFixed(2) + ')';
          ctx.strokeStyle = rgba(colL, 0.75 * aL); ctx.lineWidth = 1;
          ctx.beginPath();
          if (ctx.roundRect) ctx.roundRect(placed[0], placed[1], pw, ph, 6); else ctx.rect(placed[0], placed[1], pw, ph);
          ctx.fill(); ctx.stroke();
          ctx.fillStyle = 'rgba(232,241,255,' + aL.toFixed(2) + ')';
          var symW = textW(fkey + nd2.symbol, nd2.symbol);
          ctx.fillText(nd2.symbol, placed[0] + 6, placed[1] + ph / 2 + 0.5);
          var mid = txt.slice(nd2.symbol.length, txt.length - eTxt.length);
          if (mid) { ctx.fillStyle = pMode ? 'rgba(255,227,161,' + aL.toFixed(2) + ')' : rgba(colL, aL); ctx.fillText(mid, placed[0] + 6 + symW, placed[1] + ph / 2 + 0.5); }
          if (eTxt) { ctx.fillStyle = rgba(VIOLET, aL); ctx.fillText(eTxt, placed[0] + 6 + symW + textW(fkey + mid, mid), placed[1] + ph / 2 + 0.5); }
        }
        lastHidden = hiddenNow;
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
