/* Stock page → Levels: the Fibonacci ruler and the "you are here" row.
 *
 * The ruler draws the swing from high (top) to low (bottom) on a price
 * scale wide enough to also hold today's price, so a stock that has fallen
 * through the swing low shows how far, instead of pinning to the edge.
 * Active / Macro (fibToggle) redraws it.
 */
(function () {
  'use strict';
  var box = document.getElementById('lv-ruler'), raw = document.getElementById('lv-fib-data');
  if (!box || !raw) return;
  var D; try { D = JSON.parse(raw.textContent); } catch (e) { return; }
  var cur = Number(D.cur);
  var LV = [[0, 'high', 'Swing high'], [23.6, 'l236'], [38.2, 'l382'], [50, 'l50'], [61.8, 'l618'], [65, 'l65'], [70.5, 'l705'], [78.6, 'l786'], [100, 'low', 'Swing low']];
  var ZONES = [[0, 38.2, 'premium', 'Premium'], [38.2, 61.8, 'mid', 'Mid'], [61.8, 70.5, 'golden', 'Golden pocket'], [70.5, 100, 'discount', 'Discount']];
  function money(v) { return '$' + Number(v).toFixed(2); }

  function set(mode) {
    var s = D[mode] || D.active;
    var hi = Number(s.high), lo = Number(s.low);
    if (!(hi > lo)) { box.hidden = true; return; }
    var rng = hi - lo, at = function (p) { return hi - rng * p / 100; };
    var top = hi, bot = lo;
    if (isFinite(cur) && cur > 0) { top = Math.max(top, cur); bot = Math.min(bot, cur); }
    var pad = (top - bot) * 0.05; top += pad; bot -= pad;
    var y = function (v) { return (top - v) / (top - bot) * 100; };
    var html = '<div class="lv-r-col">';
    ZONES.forEach(function (z) {
      var a = y(at(z[0])), b = y(at(z[1]));
      html += '<div class="lv-r-zone is-' + z[2] + '" style="top:' + a.toFixed(2) + '%;height:' + (b - a).toFixed(2) + '%"><span>' + z[3] + '</span></div>';
    });
    LV.forEach(function (l) {
      var v = l[1] === 'high' ? hi : l[1] === 'low' ? lo : (s[l[1]] != null ? Number(s[l[1]]) : at(l[0]));
      html += '<div class="lv-r-tick' + (l[0] === 61.8 || l[0] === 70.5 ? ' is-gp' : '') + (l[0] === 0 || l[0] === 100 ? ' is-end' : '') + (l[0] === 65 ? ' is-quiet' : '') + '" style="top:' + y(v).toFixed(2) + '%"><b>' + (l[0] === 0 ? '0' : l[0]) + '%</b></div>';
    });
    if (isFinite(cur) && cur > 0) {
      var r = (hi - cur) / rng * 100, note;
      if (cur > hi) note = ((cur / hi - 1) * 100).toFixed(1) + '% above the swing high';
      else if (cur < lo) note = ((1 - cur / lo) * 100).toFixed(1) + '% under the swing low';
      else note = r.toFixed(1) + '% retraced';
      html += '<div class="lv-r-now' + (cur < lo || cur > hi ? ' is-out' : '') + '" style="top:' + y(cur).toFixed(2) + '%"><i></i><span><b>NOW ' + money(cur) + '</b><small>' + note + '</small></span></div>';
    }
    html += '</div>';
    box.innerHTML = html;
    box.hidden = false;
    box.setAttribute('aria-label', 'Swing ' + money(hi) + ' to ' + money(lo) + (isFinite(cur) ? ', price ' + money(cur) : ''));
    placeNowRow();
  }

  // A "you are here" row in the level list, between the two levels price sits between.
  function placeNowRow() {
    var grid = document.querySelector('.fib-grid-v2'); if (!grid || !(cur > 0)) return;
    var old = grid.querySelector('.fib-row-now'); if (old) old.remove();
    var rows = Array.prototype.slice.call(grid.querySelectorAll('.fib-row'));
    var row = document.createElement('div');
    row.className = 'fib-row fib-row-now';
    row.innerHTML = '<span class="fib-pct">◆</span><span class="fib-label">Price now</span><span class="fib-value">' + money(cur) + '</span>';
    var before = null;
    for (var i = 0; i < rows.length; i++) {
      var v = parseFloat((rows[i].querySelector('.fib-value') || {}).textContent.replace(/[$,]/g, ''));
      if (isFinite(v) && cur >= v) { before = rows[i]; break; }
    }
    if (before) before.parentNode.insertBefore(row, before);
    else if (rows.length) { var last = rows[rows.length - 1]; last.parentNode.insertBefore(row, last.nextSibling); }
  }

  var orig = window.fibToggle;
  if (typeof orig === 'function') window.fibToggle = function (mode) { orig(mode); set(mode === 'macro' && D.macro ? 'macro' : 'active'); };
  set('active');
})();
