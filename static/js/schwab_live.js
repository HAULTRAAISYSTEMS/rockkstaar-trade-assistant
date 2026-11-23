/* Schwab account page, live: the balances and every position tick with the
   market between Schwab refreshes.

   Schwab's numbers (value, today, unrealized, each position's market value)
   load with the page. Every 3 s the shared live-quote feed (the same prices
   as the tape and the Command Center) gives each holding's latest price; the
   move since the page's price, times your shares, is added to the position,
   and the sum to the totals. The price flash glows each change. */
(function () {
  'use strict';
  var box = document.getElementById('sa-live');
  if (!box) return;
  var rows = [].slice.call(document.querySelectorAll('.sa-pos[data-sym][data-qty]'));
  if (!rows.length) return;

  function num(v) { var n = parseFloat(v); return isFinite(n) ? n : null; }
  function sgn(v) {
    if (v == null || !isFinite(v)) return '—';
    if (Math.abs(v) < 0.005) return '$0.00';
    return (v > 0 ? '+' : '−') + '$' + Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function usd(v) { return v == null ? '—' : '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function pctTxt(v, d) { return v == null || !isFinite(v) ? '' : (v >= 0 ? '+' : '') + v.toFixed(d) + '%'; }
  function tone(el, v, up, dn) {
    if (!el) return;
    el.classList.toggle(up, v > 0.005); el.classList.toggle(dn, v < -0.005);
  }
  function set(el, txt) { if (el && el.textContent !== txt) el.textContent = txt; }

  var base = rows.map(function (r) {
    var qty = num(r.getAttribute('data-qty')), mv = num(r.getAttribute('data-mv'));
    return { el: r, sym: r.getAttribute('data-sym'), qty: qty, mv: mv,
             px0: qty ? mv / qty : null, day: num(r.getAttribute('data-day')) || 0,
             unr: num(r.getAttribute('data-unr')) || 0, cost: num(r.getAttribute('data-cost')) };
  }).filter(function (b) { return b.qty && b.px0 > 0; });
  var T = { total: num(box.getAttribute('data-total')), day: num(box.getAttribute('data-day')),
            unr: num(box.getAttribute('data-unr')) };
  var unrCard = box.querySelector('[data-sl="unr"]'), unrCost = unrCard ? num(unrCard.getAttribute('data-cost')) : null;
  var syms = base.map(function (b) { return b.sym; }).filter(function (s, i, a) { return a.indexOf(s) === i; });

  function render(quotes) {
    var moved = 0;
    base.forEach(function (b) {
      var q = quotes[b.sym]; if (!q || !(q.price > 0)) return;
      var d = (q.price - b.px0) * b.qty;
      moved += d;
      var r = b.el, unr = b.unr + d, day = b.day + d;
      set(r.querySelector('[data-f="px"]'), usd(q.price));
      set(r.querySelector('[data-f="mv"]'), usd(b.mv + d));
      set(r.querySelector('[data-f="day"]'), sgn(day));
      set(r.querySelector('[data-f="unr"]'), sgn(unr));
      if (b.cost > 0) set(r.querySelector('[data-f="ret"]'), pctTxt(unr / b.cost * 100, 1));
      tone(r.querySelector('.sa-pos-day'), day, 'schwab-pnl-pos', 'schwab-pnl-neg');
      tone(r.querySelector('.sa-pos-un'), unr, 'schwab-pnl-pos', 'schwab-pnl-neg');
      r.classList.toggle('is-up', unr > 0.005); r.classList.toggle('is-dn', unr < -0.005);
    });
    function card(key, v, pct, d) {
      var c = box.querySelector('[data-sl="' + key + '"]'); if (!c || v == null) return;
      set(c.querySelector('[data-slv]'), key === 'total' ? usd(v) : sgn(v));
      if (pct != null) set(c.querySelector('[data-slp]'), pctTxt(pct, d));
      if (key !== 'total') tone(c, v, 'schwab-pnl-pos', 'schwab-pnl-neg');
    }
    var total = T.total != null ? T.total + moved : null, day = T.day != null ? T.day + moved : null;
    card('total', total);
    card('day', day, total != null && day != null && total - day > 0 ? day / (total - day) * 100 : null, 2);
    var unr = T.unr != null ? T.unr + moved : null;
    card('unr', unr, unrCost > 0 && unr != null ? unr / unrCost * 100 : null, 1);
    var dot = box.querySelector('.sa-live-dot'); if (dot) dot.hidden = false;
  }

  var busy = false;
  function poll() {
    if (busy || document.hidden) return;
    busy = true;
    fetch('/api/wealth/today/live?symbols=' + encodeURIComponent(syms.join(',')), { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { busy = false; if (d && d.ok) render(d.quotes || {}); })
      .catch(function () { busy = false; });
  }
  poll(); setInterval(poll, 3000);
})();
