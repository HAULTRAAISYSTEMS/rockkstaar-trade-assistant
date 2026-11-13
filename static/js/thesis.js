/* Thesis page: zone alerts, buy-plan share counts, live what-if sliders.
   The what-if math mirrors thesis.py (build_targets) exactly. */
(function () {
  var el = document.getElementById('vz-model');
  if (!el) return;
  var M;
  try { M = JSON.parse(el.textContent); } catch (e) { return; }

  function $(id) { return document.getElementById(id); }
  function money(v) { return v == null || !isFinite(v) ? '—' : '$' + v.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}); }
  function pct(v, signed) { return (signed && v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function fairMultiple(g) {
    return M.sales ? clamp(2 + Math.max(g, -0.1) * 10, 1, 6) : clamp(12 + Math.max(g, -0.1) * 60, 10, 32);
  }

  /* ---- Toast ---- */
  var toastEl = null, toastT = null;
  function toast(msg) {
    if (!toastEl) { toastEl = document.createElement('div'); toastEl.className = 'vz-toast'; toastEl.setAttribute('role', 'status'); document.body.appendChild(toastEl); }
    toastEl.textContent = msg; toastEl.classList.add('is-on');
    clearTimeout(toastT); toastT = setTimeout(function () { toastEl.classList.remove('is-on'); }, 3800);
  }

  /* ---- Price alerts (same API as the chart's bell) ---- */
  function setAlert(price, note) {
    return fetch('/api/alerts/price', {
      method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ticker: M.ticker, price: Math.round(price * 100) / 100, current: M.price, note: note})
    }).then(function (r) { return r.json().catch(function () { return {ok: false}; }); });
  }
  document.querySelectorAll('.vz-bell').forEach(function (b) {
    b.addEventListener('click', function () {
      var p = parseFloat(b.getAttribute('data-alert-price'));
      if (!(p > 0)) return;
      b.disabled = true;
      setAlert(p, b.getAttribute('data-alert-note')).then(function (res) {
        if (res && res.ok) { b.classList.add('is-set'); b.querySelector('span').textContent = 'Alert set'; toast('🔔 ' + M.ticker + ' alert set at ' + money(p) + ' — you’ll be notified once.'); }
        else { b.disabled = false; toast((res && res.error) || 'Could not set the alert'); }
      }).catch(function () { b.disabled = false; toast('Could not set the alert — check your connection'); });
    });
  });
  var planBtn = $('vz-plan-alerts');
  if (planBtn) planBtn.addEventListener('click', function () {
    planBtn.disabled = true;
    var labels = ['Buy plan step 1', 'Buy plan step 2', 'Buy plan step 3'];
    Promise.all((M.plan || []).map(function (p, i) { return setAlert(p, M.ticker + ' ' + labels[i]); }))
      .then(function (all) {
        var ok = all.filter(function (r) { return r && r.ok; }).length;
        if (ok === all.length) { planBtn.textContent = '✓ 3 alerts set'; toast('🔔 ' + ok + ' buy-plan alerts set for ' + M.ticker + '.'); }
        else { planBtn.disabled = false; toast(ok + ' of ' + all.length + ' alerts set. ' + ((all.find(function (r) { return r && !r.ok; }) || {}).error || '')); }
      }).catch(function () { planBtn.disabled = false; toast('Could not set the alerts — check your connection'); });
  });

  /* ---- Buy plan: shares per step for the plan size ---- */
  var acct = $('vz-account'), KEY = 'tradestaar.thesis.planSize';
  function renderShares() {
    var total = parseFloat(acct && acct.value) || 0;
    document.querySelectorAll('.vz-shares').forEach(function (s) {
      var w = parseFloat(s.getAttribute('data-weight')), px = parseFloat(s.getAttribute('data-price'));
      if (!(total > 0) || !(px > 0)) { s.textContent = ''; return; }
      var dollars = total * w, sh = Math.floor(dollars / px);
      s.textContent = ' · ' + money(dollars) + ' ≈ ' + sh + ' share' + (sh === 1 ? '' : 's');
    });
  }
  if (acct) {
    try { var saved = localStorage.getItem(KEY); if (saved) acct.value = saved; } catch (e) {}
    acct.addEventListener('input', function () { try { localStorage.setItem(KEY, acct.value); } catch (e) {} renderShares(); });
    renderShares();
  }

  /* ---- What-if ---- */
  var gIn = $('vz-g'), mIn = $('vz-m'), sIn = $('vz-s');
  if (!gIn || !M.power || !M.shares) return;
  if (M.sales) { mIn.min = '0.5'; mIn.max = '15'; mIn.step = '0.1'; }
  var multTouched = false, growthTouched = false;
  function reset() {
    gIn.value = (Math.round(M.growth * 1000) / 10).toFixed(1);
    mIn.value = fairMultiple(M.growth).toFixed(1);
    sIn.value = Math.round(M.mos * 100);
    multTouched = false; growthTouched = false;
    update();
  }
  function update() {
    var G = growthTouched ? parseFloat(gIn.value) / 100 : M.growth;
    if (!multTouched) mIn.value = fairMultiple(G).toFixed(1);
    var baseMult = multTouched ? parseFloat(mIn.value) : fairMultiple(G), mos = parseFloat(sIn.value) / 100;
    $('vz-g-out').textContent = pct(G, true);
    $('vz-m-out').textContent = baseMult.toFixed(1) + 'x' + (M.sales ? ' sales' : '');
    $('vz-s-out').textContent = Math.round(mos * 100) + '%';
    var rows = {bear: [Math.min(G, 0) - 0.05, 0.6], base: [G, 1], bull: [G + 0.05, 1.35]}, out = {};
    Object.keys(rows).forEach(function (k) {
      var g = rows[k][0], mult = baseMult * rows[k][1], target = M.power * (1 + g) * mult / M.shares;
      out[k] = target;
      var up = target / M.price - 1;
      var tEl = document.querySelector('[data-wi-target="' + k + '"]'); if (tEl) tEl.textContent = money(target);
      var uEl = document.querySelector('[data-wi-upside="' + k + '"]');
      if (uEl) { uEl.textContent = pct(up, true); uEl.className = up >= 0 ? 'tone-pos' : 'tone-neg'; }
      var gEl = document.querySelector('[data-wi-growth="' + k + '"]'); if (gEl) gEl.textContent = pct(g, true);
      var mEl = document.querySelector('[data-wi-mult="' + k + '"]'); if (mEl) mEl.textContent = mult.toFixed(1) + 'x' + (M.sales ? ' sales' : '');
    });
    var loadTop = out.base * (1 - mos), zone;
    if (M.price <= loadTop) zone = 'Load the boat';
    else if (M.price <= out.base) zone = 'Accumulate';
    else if (M.price <= out.bull) zone = 'Fair';
    else zone = 'Pricey';
    var read = $('vz-wi-read');
    if (read) read.innerHTML = 'With these assumptions: buy under <b>' + money(out.base) + '</b>, load the boat under <b>' +
      money(loadTop) + '</b>. At ' + money(M.price) + ' today, ' + M.ticker + ' would be <b class="vz-zone-word">' + zone + '</b>.';
  }
  gIn.addEventListener('input', function () { growthTouched = true; update(); });
  mIn.addEventListener('input', function () { multTouched = true; update(); });
  sIn.addEventListener('input', update);
  var r = $('vz-reset'); if (r) r.addEventListener('click', reset);
  reset();
})();
