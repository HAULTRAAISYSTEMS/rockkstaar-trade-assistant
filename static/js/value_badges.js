/* Value-zone badges for any element with data-zone-badge="TICKER".
   Used by the Terminal watchlist and the Watchlists page. Zones are built in
   the background on first request, so pending tickers are retried a few
   times. A badge links to the ticker's Thesis page. */
(function () {
  var CSS = '.vz-badge{display:inline-flex;align-items:center;gap:3px;margin-left:6px;padding:1px 6px;border-radius:99px;border:1px solid currentColor;font:800 9px ui-monospace,Menlo,monospace;letter-spacing:.05em;text-transform:uppercase;text-decoration:none;vertical-align:middle;white-space:nowrap}' +
    '.vz-badge[hidden]{display:none}' +
    '.vz-badge.tone-load{color:#2ee6a6;background:rgba(46,230,166,.12)}' +
    '.vz-badge.tone-accumulate{color:#8be36b;background:rgba(139,227,107,.1)}' +
    '.vz-badge.tone-fair{color:#f3c95a;background:rgba(243,201,90,.07)}' +
    '.vz-badge.tone-pricey{color:#ff6b6b;background:rgba(255,107,107,.07)}' +
    '.vz-badge.is-signal{box-shadow:0 0 10px currentColor}';
  var SHORT = {load: 'Load', accumulate: 'Accum', fair: 'Fair', pricey: 'Pricey'};

  function injectCss() {
    if (document.getElementById('vz-badge-css')) return;
    var s = document.createElement('style'); s.id = 'vz-badge-css'; s.textContent = CSS;
    document.head.appendChild(s);
  }
  function paint(z) {
    document.querySelectorAll('[data-zone-badge="' + z.ticker + '"]').forEach(function (el) {
      if (!z.available || !z.zone) { el.hidden = true; return; }
      var sig = z.signal || {}, hot = sig.key === 'load' || sig.key === 'buy';
      el.hidden = false;
      el.className = 'vz-badge tone-' + z.zone + (hot ? ' is-signal' : '');
      el.textContent = (hot ? '⚓ ' : '') + SHORT[z.zone];
      el.title = z.zone_label + ' · buy under $' + Number(z.accum_top).toFixed(2) +
        ', load the boat under $' + Number(z.load_top).toFixed(2) + (sig.title ? ' · ' + sig.title : '');
      if (el.tagName === 'A') el.href = '/thesis?ticker=' + encodeURIComponent(z.ticker);
    });
  }
  function load(tickers, tries) {
    if (!tickers.length) return;
    fetch('/api/thesis/zones?tickers=' + encodeURIComponent(tickers.join(',')), {credentials: 'same-origin', cache: 'no-store'})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.ok) return;
        Object.keys(d.zones || {}).forEach(function (t) { paint(d.zones[t]); });
        var pending = d.pending || [];
        if (pending.length && tries < 6) setTimeout(function () { load(pending, tries + 1); }, 4000 + tries * 3000);
      }).catch(function () {});
  }
  function run() {
    var els = document.querySelectorAll('[data-zone-badge]');
    if (!els.length) return;
    injectCss();
    var seen = {}, list = [];
    els.forEach(function (el) {
      el.addEventListener('click', function (e) { e.stopPropagation(); });
      var t = (el.getAttribute('data-zone-badge') || '').toUpperCase();
      if (t && !seen[t]) { seen[t] = 1; list.push(t); }
    });
    for (var i = 0; i < list.length; i += 40) load(list.slice(i, i + 40), 0);
  }
  window.vzLoadBadges = run;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run();
})();
