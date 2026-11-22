/* Verify → Analyze: pictures for the numbers.
 *
 * Each check row already spells out its arithmetic ("$257B -> $282B -> …"
 * or "Current Ratio > 1.5" with a value of 2.79). This draws that same
 * arithmetic: a five-year sparkline with the change first → last, or a
 * gauge showing the value against the rule's line. Nothing is fetched and
 * nothing is recomputed beyond what the row prints.
 */
(function () {
  'use strict';
  var MULT = { K: 1e3, M: 1e6, B: 1e9, T: 1e12 };
  var NUM = /([-−–])?\s*\$?\s*([-−–])?(\d[\d,]*(?:\.\d+)?)\s*([KMBT](?![a-z])|%)?/;

  function parse(tok) {
    var m = NUM.exec(String(tok || '')); if (!m) return null;
    var v = parseFloat(m[3].replace(/,/g, '')); if (!isFinite(v)) return null;
    if (m[1] || m[2]) v = -v;
    if (m[4] && MULT[m[4]]) v *= MULT[m[4]];
    return { v: v, pct: m[4] === '%', money: /\$/.test(tok) };
  }
  function money(v) {
    var a = Math.abs(v), s = v < 0 ? '−' : '';
    if (a >= 1e12) return s + '$' + (a / 1e12).toFixed(2) + 'T';
    if (a >= 1e9) return s + '$' + (a / 1e9).toFixed(1) + 'B';
    if (a >= 1e6) return s + '$' + (a / 1e6).toFixed(1) + 'M';
    return s + '$' + a.toLocaleString('en-US', { maximumFractionDigits: 2 });
  }
  function fmt(v, unit) { return unit === '%' ? v.toFixed(1) + '%' : unit === '$' ? money(v) : (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)); }

  function series(text) {
    if (!/(->|→)/.test(text)) return null;
    var parts = text.split(/\s*(?:->|→)\s*/);
    if (parts.length < 3) return null;
    var out = [], unit = '';
    for (var i = 0; i < parts.length; i++) {
      var p = parse(parts[i]); if (!p) return null;
      out.push(p.v);
      if (p.pct) unit = '%'; else if (p.money && unit !== '%') unit = '$';
    }
    return { vals: out, unit: unit };
  }

  function spark(s, tone) {
    var v = s.vals, n = v.length, W = 92, H = 28, gap = 3, bw = (W - gap * (n - 1)) / n;
    var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v), neg = lo < 0;
    var base = neg ? Math.min(0, lo) : Math.max(0, lo - (hi - lo) * 0.9);
    var top = neg ? Math.max(0, hi) : hi;
    if (top === base) { top = base + 1; }
    var zeroY = neg ? H - (0 - base) / (top - base) * (H - 2) : H;
    var bars = v.map(function (x, i) {
      var y = H - (x - base) / (top - base) * (H - 2), y0 = Math.min(y, zeroY), h = Math.max(2, Math.abs(zeroY - y));
      if (!neg) { y0 = Math.min(y, H - 3); h = Math.max(3, H - y0); }
      return '<rect x="' + (i * (bw + gap)).toFixed(1) + '" y="' + y0.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="1.5"' +
        (i === n - 1 ? ' class="last"' : (x < 0 ? ' class="neg"' : '')) + '/>';
    }).join('');
    var first = v[0], last = v[n - 1], d;
    if (s.unit === '%') d = (last - first >= 0 ? '+' : '−') + Math.abs(last - first).toFixed(1) + ' pts';
    else if (first > 0 && last > 0) d = (last >= first ? '+' : '−') + Math.abs((last / first - 1) * 100).toFixed(0) + '%';
    else d = '';
    var title = v.map(function (x) { return fmt(x, s.unit); }).join(' → ');
    return '<span class="fa-viz fa-spark tone-' + tone + '" title="' + title + '"><svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '" aria-hidden="true">' + bars + '</svg>' +
      (d ? '<em class="' + (last >= first ? 'up' : 'dn') + '">' + d + '</em>' : '') + '<span class="sr-only">' + title + '</span></span>';
  }

  function gauge(label, valueText, tone) {
    var m = /([<>]=?|≥|≤)\s*\$?(\d+(?:\.\d+)?)\s*(%|x)?/.exec(label); if (!m) return '';
    var thr = parseFloat(m[2]), val = parse(valueText); if (!val || !isFinite(thr)) return '';
    var v = val.v, unit = m[3] === '%' || val.pct ? '%' : '';
    if (val.money) return '';
    var max = Math.max(thr * 2, v * 1.15, 0.0001), less = m[1].charAt(0) === '<' || m[1] === '≤';
    var tp = Math.min(100, thr / max * 100), vp = Math.max(0, Math.min(100, v / max * 100));
    return '<span class="fa-viz fa-gauge tone-' + tone + (less ? ' is-less' : ' is-more') + '" title="' + (less ? 'Needs under ' : 'Needs over ') + thr + unit + '">' +
      '<span class="fa-g-track"><i class="fa-g-ok" style="' + (less ? 'left:0;width:' + tp.toFixed(1) + '%' : 'left:' + tp.toFixed(1) + '%;right:0') + '"></i>' +
      '<i class="fa-g-fill" style="width:' + vp.toFixed(1) + '%"></i><i class="fa-g-thr" style="left:' + tp.toFixed(1) + '%"></i>' +
      '<i class="fa-g-dot" style="left:' + vp.toFixed(1) + '%"></i></span>' +
      '<small>' + (less ? '&lt; ' : '&gt; ') + thr + unit + '</small></span>';
  }

  function enhance(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll('.fundamentals-shell .metric-row'), function (row) {
      if (row.__fa) return; row.__fa = true;
      var data = row.querySelector('.metric-data'), valEl = row.querySelector('.metric-value'), lab = row.querySelector('.metric-label');
      if (!data || !valEl || !lab) return;
      var tone = row.classList.contains('is-pass') ? 'pass' : row.classList.contains('is-fail') ? 'fail' : row.classList.contains('is-partial') ? 'partial' : 'none';
      var work = row.querySelector('.metric-working'), html = '';
      var s = work && series(work.textContent);
      if (s) html = spark(s, tone);
      else {
        var labelText = (lab.firstChild && lab.firstChild.nodeType === 3 ? lab.firstChild.textContent : lab.textContent) || '';
        html = gauge(labelText, valEl.textContent, tone);
      }
      if (html) valEl.insertAdjacentHTML('beforebegin', html);
    });
  }

  // Dials: open the section and glide to it.
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('.fa-dial[data-open]'); if (!a) return;
    var card = document.getElementById(a.getAttribute('data-open')); if (!card) return;
    e.preventDefault();
    card.classList.add('open');
    card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    card.classList.remove('fa-flash'); void card.offsetWidth; card.classList.add('fa-flash');
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { enhance(); });
  else enhance();
  window.TSFaMetrics = { enhance: enhance, series: series, parse: parse };
})();
