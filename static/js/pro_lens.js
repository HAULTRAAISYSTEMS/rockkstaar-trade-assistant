/* Pro lens: owner cash flow, a ten-year cash-flow model, and the growth
   today's price needs. The math mirrors pro_lens.py (dcf_value,
   implied_start_growth) exactly; the dials recompute it live. */
(function () {
  var el = document.getElementById('pl-model'), root = document.getElementById('pro-lens');
  if (!el || !root) return;
  var P;
  try { P = JSON.parse(el.textContent); } catch (e) { return; }
  var SC = [['bear', -0.10], ['base', 0], ['bull', 0.05]];
  var G_MIN = -0.10, G_MAX = 0.60;
  var st = {basis: P.basis, r: P.r, g: P.g, n: P.years};

  function $(id) { return document.getElementById(id); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function money(v) { return v == null || !isFinite(v) ? '—' : '$' + v.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}); }
  function pct(v) { return v == null ? '—' : (v >= 0 ? '+' : '') + Math.round(v * 100) + '%'; }

  function path(g0, n, gt) {
    if (n <= 1) return [gt];
    var out = [];
    for (var t = 0; t < n; t++) out.push(g0 + (gt - g0) * t / (n - 1));
    return out;
  }
  function dcf(cash, shares, g0, r, n, gt, cps) {
    if (!(cash > 0) || !(shares > 0) || r <= gt) return null;
    var pv = 0, cf = cash, gs = path(g0, n, gt);
    for (var t = 1; t <= gs.length; t++) { cf *= 1 + gs[t - 1]; pv += cf / Math.pow(1 + r, t); }
    pv += cf * (1 + gt) / (r - gt) / Math.pow(1 + r, n);
    return pv / shares + (cps || 0);
  }
  function implied(price, cash, shares, r, n, gt, cps) {
    if (!price || !(cash > 0) || !shares || r <= gt) return null;
    var f = function (g) { return dcf(cash, shares, g, r, n, gt, cps); };
    var lo = -0.5, hi = 1.5;
    if (f(lo) >= price) return lo;
    if (f(hi) < price) return null;
    for (var i = 0; i < 80; i++) { var mid = (lo + hi) / 2; if (f(mid) < price) lo = mid; else hi = mid; }
    return (lo + hi) / 2;
  }
  function tone(imp, did) {
    if (imp == null) return ['pricey', 'Out of reach'];
    var gap = imp - did;
    if (gap <= 0) return ['load', 'Below its record'];
    if (gap <= 0.08) return ['fair', 'Demanding'];
    return ['pricey', 'Heroic'];
  }

  function cash() { return st.basis === 'owner' && P.owner > 0 ? P.owner : P.fcf; }

  function render() {
    var price = P.price, c = cash(), vals = {};
    SC.forEach(function (s) {
      var g = clamp(st.g + s[1], G_MIN, G_MAX), v = dcf(c, P.shares, g, st.r, st.n, P.gt, P.cash_ps);
      vals[s[0]] = v;
      var gEl = root.querySelector('[data-pl-g="' + s[0] + '"]'), vEl = root.querySelector('[data-pl-v="' + s[0] + '"]'), uEl = root.querySelector('[data-pl-up="' + s[0] + '"]');
      if (gEl) gEl.textContent = pct(g);
      if (vEl) vEl.textContent = money(v);
      if (uEl) { uEl.textContent = v ? pct(v / price - 1) : '—'; uEl.className = 'tone-' + (v && v >= price ? 'pos' : 'neg'); }
    });
    // Range bar: bear .. bull with today's price on it.
    var rg = $('pl-range');
    if (rg && vals.bear != null && vals.bull != null) {
      var lo = Math.min(vals.bear, price) * 0.9, hi = Math.max(vals.bull, price) * 1.1, sp = hi - lo || 1;
      var band = rg.querySelector('.pl-range__band'), px = rg.querySelector('.pl-range__px');
      band.style.left = ((vals.bear - lo) / sp * 100) + '%';
      band.style.width = ((vals.bull - vals.bear) / sp * 100) + '%';
      px.style.left = ((price - lo) / sp * 100) + '%';
    }
    var imp = implied(price, c, P.shares, st.r, st.n, P.gt, P.cash_ps), t = tone(imp, P.g);
    var impTxt = imp == null ? 'over +150%' : pct(imp);
    $('pl-need').textContent = impTxt; $('pl-need2').textContent = impTxt;
    var w = $('pl-word'); w.textContent = t[1];
    var card = $('pl-believe'); card.className = card.className.replace(/tone-\w+/, 'tone-' + t[0]);
    var mx = Math.max(Math.abs(imp == null ? 1.5 : imp), Math.abs(P.g), 0.05);
    $('pl-bar-need').style.width = (Math.abs(imp == null ? 1.5 : imp) / mx * 100) + '%';
    $('pl-bar-need').className = imp == null || imp - P.g > 0.08 ? 'is-hot' : 'is-ok';
    $('pl-bar-did').style.width = (Math.abs(P.g) / mx * 100) + '%';
    var rTxt = (st.r * 100).toFixed(st.r * 200 % 2 ? 1 : 0) + '%';
    $('pl-r-txt').textContent = rTxt; $('pl-r-txt2').textContent = rTxt;
    $('pl-r-out').textContent = rTxt;
    $('pl-g-out').textContent = pct(st.g);
    $('pl-n-out').textContent = st.n + ' yrs';
  }

  var rIn = $('pl-r'), gIn = $('pl-g'), nIn = $('pl-n');
  function sync() { rIn.value = st.r * 100; gIn.value = st.g * 100; nIn.value = st.n; }
  rIn.addEventListener('input', function () { st.r = parseFloat(rIn.value) / 100; render(); });
  gIn.addEventListener('input', function () { st.g = parseFloat(gIn.value) / 100; render(); });
  nIn.addEventListener('input', function () { st.n = parseInt(nIn.value, 10); render(); });
  $('pl-reset').addEventListener('click', function () { st.r = P.r; st.g = P.g; st.n = P.years; sync(); render(); });
  root.querySelectorAll('[data-pl-basis]').forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.disabled) return;
      st.basis = b.getAttribute('data-pl-basis');
      root.querySelectorAll('[data-pl-basis]').forEach(function (x) { x.classList.toggle('is-on', x === b); });
      render();
    });
  });
  // Live price: the page's live feed updates M.price via TSLive; follow it.
  if (window.TSLive && TSLive.on) TSLive.on('live', function (d) {
    var q = d && d.quote;
    if (q && q.price > 0 && (!d.ticker || !window.__plTicker || d.ticker === window.__plTicker)) { P.price = q.price; render(); }
  });
  sync(); render();
})();
