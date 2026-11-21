/* 3D scene behind every page: parallax depth layers that follow the pointer
   and the scroll, and a live neon floor (grid flowing toward you, light pulses
   on the trails, twinkling stars). Scrolling pushes the floor forward, as if
   flying over it. Pauses when hidden; one still frame for reduced motion. */
(function () {
  'use strict';
  var stage = document.getElementById('ts-scene-stage');
  var canvas = document.getElementById('ts-scene-floor');
  if (!stage || !canvas || !canvas.getContext) return;
  var root = document.documentElement;
  var layers = Array.prototype.slice.call(stage.querySelectorAll('[data-depth]'));
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var calm = true;  // 30 fps everywhere: the floor moves slowly, the eye can't tell, the battery can
  var deep = root.classList.contains('cc-scene-deep');          // terminal: still frame
  var ctx = canvas.getContext('2d'), W = 0, H = 0;
  var px = 0, py = 0, tx = 0, ty = 0, last = 0, drawnAt = 0, travel = 0, lastScroll = window.scrollY || 0;
  var HZ = 0.64; // horizon, as a fraction of the scene height (matches the layer art)

  var stars = [];
  for (var i = 0; i < 140; i++) stars.push({ x: Math.random(), y: Math.random() * 0.6, r: Math.random() * 1.3 + 0.3, p: Math.random() * 6.28, s: 0.6 + Math.random() * 1.8 });

  // The art stage is wider than the screen (to keep its aspect); the floor
  // canvas only covers what is visible, at a soft resolution (glow content).
  var OX = 0;
  function size() {
    var r = stage.getBoundingClientRect(), vw = document.documentElement.clientWidth || window.innerWidth;
    var scale = Math.min(window.devicePixelRatio || 1, 1) * (vw > 1400 ? 0.75 : 0.85);
    W = r.width; H = r.height; OX = Math.max(0, (W - vw) / 2);
    canvas.style.left = OX + 'px'; canvas.style.width = (W - 2 * OX) + 'px';
    canvas.width = Math.round((W - 2 * OX) * scale); canvas.height = Math.round(H * scale);
    ctx.setTransform(scale, 0, 0, scale, -OX * scale, 0);
    if (reduce || deep) draw(0);
  }
  window.addEventListener('resize', size, { passive: true });
  window.addEventListener('pointermove', function (e) {
    tx = (e.clientX / window.innerWidth) * 2 - 1; ty = (e.clientY / window.innerHeight) * 2 - 1;
  }, { passive: true });
  window.addEventListener('scroll', function () {
    var y = window.scrollY || 0; travel += (y - lastScroll) * 0.004; lastScroll = y;
  }, { passive: true });

  var TRAILS = [[-150, 1020, 0.55], [250, 1120, 0.8], [2550, 1380, 0.55], [2150, 1280, 0.8]];
  function trailPoint(tr, s) {
    var k = W / 2400, hz = H * HZ;
    return [(tr[0] + (tr[1] - tr[0]) * (1 - Math.pow(1 - s, 1 + tr[2]))) * k, H + 30 - (H + 30 - hz - 6) * Math.pow(s, 0.75)];
  }

  function frame(t) {
    requestAnimationFrame(frame);
    if (document.hidden) return;
    if (calm && t - drawnAt < 33) return;   // ~30 fps on data pages
    drawnAt = t;
    var time = t / 1000, dt = Math.min(0.05, (t - last) / 1000 || 0); last = t;
    var sway = Math.sin(time * 0.18) * 0.35;
    px += ((tx || sway) - px) * Math.min(1, dt * 3); py += (ty * 0.6 - py) * Math.min(1, dt * 3);
    var sc = Math.min(window.scrollY || 0, 1600);
    for (var l = 0; l < layers.length; l++) {
      var d = parseFloat(layers[l].getAttribute('data-depth')) || 0;
      // Scrolling down lifts you: near ranges sink faster than far ones.
      var tf = 'translate3d(' + (-px * d * 34).toFixed(1) + 'px,' + (-py * d * 10 + sc * d * 0.06).toFixed(1) + 'px,0)';
      if (layers[l]._tf !== tf) { layers[l].style.transform = tf; layers[l]._tf = tf; }
    }
    draw(time);
  }

  function draw(time) {
    if (!W || !H) return;
    ctx.clearRect(OX, 0, W - 2 * OX, H);
    var hz = H * HZ, k = W / 2400, vx = W / 2 - px * 18;
    for (var i = 0; i < stars.length; i++) {
      var s = stars[i], a = 0.35 + 0.65 * Math.abs(Math.sin(time * s.s + s.p));
      ctx.fillStyle = 'rgba(200,225,255,' + (a * 0.8).toFixed(3) + ')';
      ctx.beginPath(); ctx.arc(s.x * W - px * 4, s.y * H, s.r, 0, 6.2832); ctx.fill();
    }
    ctx.save();
    ctx.beginPath(); ctx.rect(0, hz, W, H - hz); ctx.clip();
    for (var g = -24; g <= 24; g++) {
      var x0 = vx + g * 12 * k, x1 = vx + g * 380 * k - px * 60;
      var grd = ctx.createLinearGradient(0, hz, 0, H);
      grd.addColorStop(0, 'rgba(40,170,255,0.05)'); grd.addColorStop(1, 'rgba(40,170,255,0.42)');
      ctx.strokeStyle = grd; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(x0, hz); ctx.lineTo(x1, H + 400 * k); ctx.stroke();
    }
    var off = ((time * 0.45 + travel) % 1 + 1) % 1;
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
    for (var r = 0; r < TRAILS.length; r++) {
      var tr = TRAILS[r];
      for (var pass = 0; pass < 2; pass++) {
        ctx.strokeStyle = pass ? 'rgba(120,255,200,0.95)' : 'rgba(60,255,170,0.18)';
        ctx.lineWidth = pass ? 2.2 : 10;
        ctx.beginPath();
        for (var q = 0; q <= 60; q++) { var P = trailPoint(tr, q / 60); P[0] -= px * 50 * (1 - q / 60); if (q) ctx.lineTo(P[0], P[1]); else ctx.moveTo(P[0], P[1]); }
        ctx.stroke();
      }
      var ph = (((time * 0.35) + travel * 0.6 + r * 0.27) % 1 + 1) % 1;
      var B = trailPoint(tr, ph); B[0] -= px * 50 * (1 - ph);
      var glow = ctx.createRadialGradient(B[0], B[1], 0, B[0], B[1], 26 * (1 - ph * 0.7));
      glow.addColorStop(0, 'rgba(220,255,240,0.95)'); glow.addColorStop(0.3, 'rgba(60,255,170,0.5)'); glow.addColorStop(1, 'rgba(60,255,170,0)');
      ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(B[0], B[1], 26, 0, 6.2832); ctx.fill();
    }
    ctx.restore();
    var hg = ctx.createLinearGradient(0, 0, W, 0);
    hg.addColorStop(0, 'rgba(120,230,255,0)'); hg.addColorStop(0.5, 'rgba(120,230,255,0.9)'); hg.addColorStop(1, 'rgba(120,230,255,0)');
    ctx.fillStyle = hg; ctx.fillRect(0, hz - 1, W, 2);
  }
  size();
  if (reduce || deep) draw(0.5); else requestAnimationFrame(frame);
})();
