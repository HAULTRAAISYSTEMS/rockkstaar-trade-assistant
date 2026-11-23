/* Price flash, app-wide (like a broker's quote board).
 *
 * When a price or a % change on the page ticks, its text glows green for a
 * moment if it went up and red if it went down. Nothing on any page has to
 * opt in: one MutationObserver watches for numbers whose text changes.
 *
 * What counts as a number: short text with a $, a % or a decimal point
 * ("$635.54", "-3.41%", "24H +1.20%", "−22.47", "1,182.00"), so ranks,
 * counts, dates and times never flash.
 *
 * Many widgets redraw by replacing their markup, so a number is followed
 * by where it sits — its ticker (data-ticker / data-symbol / data-mtape /
 * a ticker link) plus its path inside that row — not only by the element.
 * The first sighting is just remembered; only a later change flashes.
 *
 * Off with "Price flash" in the profile menu (<html data-tick-flash="0">),
 * and calmer with reduced motion. Skips inputs, the right-click menu and
 * anything marked data-no-flash.
 */
(function () {
  'use strict';
  if (window.TSTickFlash || !window.MutationObserver) return;
  var NUM = /^(?:(?:24H|AH|PRE|ON|ETH)\s+)?[+\-−–]?\s?\$?\s?[+\-−–]?\d[\d,]*(?:\.\d+)?\s?(?:%|[KMBT])?$/i;
  var byEl = new WeakMap(), byKey = new Map(), timers = new WeakMap();
  var SKIP = 'input, textarea, select, script, style, noscript, .tscm, .tscm-toast, [data-no-flash], [contenteditable="true"]';
  var HOST = '[data-ticker], [data-symbol], [data-sym], [data-mtape]';
  var DUR = 1300;

  function on() { return document.documentElement.getAttribute('data-tick-flash') !== '0'; }

  function value(text) {
    var t = String(text || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 24 || !NUM.test(t)) return null;
    if (!/[$%.]/.test(t)) return null;                 // ranks, counts: never
    t = t.replace(/^(?:24H|AH|PRE|ON|ETH)\s+/i, '');   // session label, not part of the number
    var neg = /^[\-−–]/.test(t) || /\$\s?[\-−–]/.test(t);
    var m = /(\d[\d,]*(?:\.\d+)?)/.exec(t); if (!m) return null;
    var v = parseFloat(m[1].replace(/,/g, '')); if (!isFinite(v)) return null;
    if (/[KMBT]$/i.test(t)) v *= { K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[t.slice(-1).toUpperCase()];
    return { v: neg ? -v : v, pct: /%$/.test(t) };
  }

  // A number's text: its own text, ignoring icon children that carry none.
  function leafText(el) {
    // Own text first ("$1,359.00" in a cell that also holds a "+11.85" tag),
    // else the whole text when the children are only icons.
    var own = '';
    for (var n = el.firstChild; n; n = n.nextSibling) if (n.nodeType === 3) own += n.nodeValue;
    if (own.trim()) return own;
    if (el.childElementCount) {
      for (var c = el.firstElementChild; c; c = c.nextElementSibling) if ((c.textContent || '').trim()) return null;
    }
    return el.textContent;
  }

  function hostTicker(el) {
    var h = el.closest(HOST);
    if (h) return { host: h, t: h.getAttribute('data-ticker') || h.getAttribute('data-symbol') || h.getAttribute('data-sym') || h.getAttribute('data-mtape') };
    var a = el.closest('a[href]');
    if (a) {
      var m = /\/stock\/([A-Za-z0-9.\-]{1,10})(?:[/?#]|$)/.exec(a.getAttribute('href') || '') || /[?&](?:ticker|symbol)=([A-Za-z0-9.\-]{1,10})/.exec(a.getAttribute('href') || '');
      if (m) return { host: a, t: m[1].toUpperCase() };
    }
    return null;
  }
  function keyOf(el) {
    var ht = hostTicker(el); if (!ht) return null;
    var path = [], n = el;
    while (n && n !== ht.host && path.length < 8) {
      var i = 0, s = n; while ((s = s.previousElementSibling)) i++;
      path.push(n.tagName + i + (n.className && typeof n.className === 'string' ? '.' + n.className.split(' ')[0] : ''));
      n = n.parentElement;
    }
    // Same row drawn in two places (globe chip vs. side list) stays separate.
    var scope = ht.host.closest('[id]');
    return (scope ? scope.id : '') + '|' + ht.t + '|' + path.join('/');
  }

  function flash(el, up) {
    if (!on()) return;
    var cls = up ? 'tf-up' : 'tf-dn';
    el.classList.remove('tf-up', 'tf-dn');
    void el.offsetWidth;                                // restart the animation
    el.classList.add(cls);
    clearTimeout(timers.get(el));
    timers.set(el, setTimeout(function () { el.classList.remove(cls); }, DUR));
  }

  function check(el, quiet) {
    if (!el || el.nodeType !== 1 || el.closest(SKIP)) return;
    var txt = leafText(el); if (txt == null) return;
    var val = value(txt); if (!val) return;
    var key = keyOf(el);
    var prev = byEl.get(el);
    if (prev === undefined && key) prev = byKey.get(key);
    byEl.set(el, val.v);
    if (key) byKey.set(key, val.v);
    if (quiet || prev === undefined || prev === val.v) return;
    flash(el, val.v > prev);
    flashName(el, val.v > prev);
  }

  // The ticker's name glows with its number (once per row per tick).
  var nameAt = new WeakMap();
  function flashName(el, up) {
    var ht = hostTicker(el); if (!ht || !ht.t) return;
    var now = Date.now(); if (now - (nameAt.get(ht.host) || 0) < 300) return;
    nameAt.set(ht.host, now);
    var want = String(ht.t).toUpperCase(), all = [ht.host].concat(Array.prototype.slice.call(ht.host.getElementsByTagName('*'), 0, 60));
    for (var i = 0; i < all.length; i++) {
      var n = all[i], own = '';
      for (var c = n.firstChild; c; c = c.nextSibling) if (c.nodeType === 3) own += c.nodeValue;
      if (own.trim().toUpperCase() === want) { flashTo(n, up, 'tf-name-'); return; }
    }
  }
  function flashTo(el, up, prefix) {
    if (!on()) return;
    var cls = prefix + (up ? 'up' : 'dn');
    el.classList.remove(prefix + 'up', prefix + 'dn');
    void el.offsetWidth;
    el.classList.add(cls);
    clearTimeout(timers.get(el));
    timers.set(el, setTimeout(function () { el.classList.remove(cls); }, DUR));
  }

  function scan(root, quiet) {
    if (!root || root.nodeType !== 1) return;
    if (root.closest && root.closest(SKIP)) return;
    check(root, quiet);
    var all = root.getElementsByTagName('*');
    if (all.length > 4000) return;                      // never stall on a huge subtree
    for (var i = 0; i < all.length; i++) check(all[i], quiet);
  }

  var queue = new Set(), raf = 0;
  function flush() {
    raf = 0;
    queue.forEach(function (el) { if (el.isConnected) scan(el, false); });
    queue.clear();
    if (byKey.size > 6000) byKey.clear();
  }
  var mo = new MutationObserver(function (list) {
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (m.type === 'characterData') { if (m.target.parentElement) queue.add(m.target.parentElement); continue; }
      if (m.target && m.target.nodeType === 1 && m.addedNodes.length) {
        for (var j = 0; j < m.addedNodes.length; j++) {
          var n = m.addedNodes[j];
          if (n.nodeType === 1) queue.add(n);
          else if (n.nodeType === 3 && m.target.nodeType === 1) queue.add(m.target);
        }
      }
    }
    if (queue.size && !raf) raf = requestAnimationFrame(flush);
  });

  function start() {
    scan(document.body, true);                          // remember, don't flash, what's already there
    mo.observe(document.body, { subtree: true, childList: true, characterData: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();

  window.TSTickFlash = { value: value, flash: flash, scan: scan };
})();
