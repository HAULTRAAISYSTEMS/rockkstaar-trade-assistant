/* Display preferences from the avatar menu (e.g. "24H prices").
 * Saved per account; flips <html data-show-24h> at once and tells the page
 * (tradestaar:prefs) so the globe redraws without a reload. */
(function () {
  'use strict';
  var ATTR = { show_24h: 'data-show-24h', flash_ticks: 'data-tick-flash', earnings_strip: 'data-earn-strip' };
  document.addEventListener('change', function (e) {
    var box = e.target.closest && e.target.closest('input[data-pref]'); if (!box) return;
    var name = box.getAttribute('data-pref'), on = !!box.checked, body = {};
    body[name] = on;
    function apply(v) {
      if (ATTR[name]) document.documentElement.setAttribute(ATTR[name], v ? '1' : '0');
      Array.prototype.forEach.call(document.querySelectorAll('input[data-pref="' + name + '"]'), function (b) { b.checked = v; });
      document.dispatchEvent(new CustomEvent('tradestaar:prefs', { detail: { name: name, on: v } }));
    }
    apply(on);
    fetch('/api/account/prefs', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.ok) { apply(!on); if (window.TSMenu) window.TSMenu.toast('Could not save that setting', { bad: true }); return; }
        if (window.TSMenu && name === 'flash_ticks') window.TSMenu.toast(on ? 'Price flash on' : 'Price flash off');
        if (window.TSMenu && name === 'earnings_strip') window.TSMenu.toast(on ? 'Earnings strip on' : 'Earnings strip off');
        if (window.TSMenu && name === 'show_24h') window.TSMenu.toast(on ? '24H prices on: the globe shows pre-market, after-hours and overnight moves' : '24H prices off: the globe shows the last regular session');
      })
      .catch(function () { apply(!on); });
  });
})();

/* One rule for every price display: with "24H prices" on, a stock trading
 * pre-market / after hours / overnight shows that price and move, labelled;
 * otherwise the last regular session. No regular move at all → the extended
 * one, labelled. TS24.pick(item) → { px, pct, label, ext }. */
(function () {
  'use strict';
  var L = { pre: 'PRE', post: 'AH', overnight: '24H' };
  function n(v) { return v !== null && v !== undefined && v !== '' && isFinite(Number(v)); }
  window.TS24 = {
    on: function () { return document.documentElement.getAttribute('data-show-24h') === '1'; },
    pick: function (t) {
      t = t || {};
      var lbl = L[t.ext], use = !!(lbl && n(t.ext_pct) && (window.TS24.on() || !n(t.change_pct)));
      if (!use) return { px: n(t.price) ? Number(t.price) : null, pct: n(t.change_pct) ? Number(t.change_pct) : null, label: '', ext: null };
      return { px: n(t.ext_price) ? Number(t.ext_price) : (n(t.price) ? Number(t.price) : null), pct: Number(t.ext_pct), label: lbl + ' ', ext: t.ext };
    }
  };
})();
