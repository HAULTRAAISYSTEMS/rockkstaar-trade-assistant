/* Earnings strip: once a stock has reported (or is in its release window),
   show how the price is reacting, from the same live tape every other price
   uses. The reaction to a report trades after hours / pre-market, so an
   extended move is shown (labelled) whenever there is one, whatever the 24H
   setting; otherwise the day's move. The price flash glows it as it ticks. */
(function () {
  'use strict';
  var strip = document.getElementById('earn-strip');
  if (!strip) return;
  function render(items) {
    if (!Array.isArray(items) || !items.length) return;
    var L = { pre: 'PRE ', post: 'AH ', overnight: '24H ' };
    var by = {};
    items.forEach(function (i) { if (i && i.symbol) by[String(i.symbol).toUpperCase()] = i; });
    strip.querySelectorAll('.es-item').forEach(function (a) {
      if (a.classList.contains('is-soon')) return;
      var it = by[a.getAttribute('data-es')], el = a.querySelector('[data-es-move]');
      if (!it || !el) return;
      var ext = L[it.ext] && isFinite(Number(it.ext_pct)) && it.ext_pct !== null;
      var p = { pct: ext ? Number(it.ext_pct) : (isFinite(Number(it.change_pct)) && it.change_pct !== null ? Number(it.change_pct) : null),
                label: ext ? L[it.ext] : '' };
      if (p.pct == null) return;
      var txt = p.label + (p.pct >= 0 ? '+' : '') + p.pct.toFixed(2) + '%';
      if (el.textContent !== txt) el.textContent = txt;
      el.hidden = false;
      el.classList.toggle('is-up', p.pct > 0); el.classList.toggle('is-down', p.pct < 0);
    });
  }
  document.addEventListener('tradestaar:tape', function (e) { render(e.detail); });
  document.addEventListener('tradestaar:tape', function (e) { window.__esLast = e.detail; });
})();
