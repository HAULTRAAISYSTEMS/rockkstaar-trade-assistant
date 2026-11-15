/* Favorites — the tickers you like most, one tap away on the chart.
 *
 *   ☆ next to the chart's ticker (or press *) stars it; ☆ on any watchlist row too.
 *   The ★ Favorites strip above the chart shows each one live: price, day move,
 *   a heat bar for how big the move is, and its value zone. Tap to open,
 *   drag to reorder, Alt+1…9 jumps to the first nine. Sort by your order,
 *   top gainers, biggest drop, or buy zone first. Works in full screen.
 *
 * Saved to the account (/api/favorites) so every device has the same list,
 * and cached locally so the strip paints before the network answers.
 */
(function () {
  'use strict';
  if (window.TSFav) return;
  var KEY = 'tradestaar.favs', SORT_KEY = 'tradestaar.favSort', OPEN_KEY = 'tradestaar.favOpen', MAX = 40;
  var RE = /^[A-Z][A-Z0-9]{0,5}(\.[A-Z])?$/;
  var favs = [], quotes = {}, subs = [], saveTimer = null, strip = null, dragT = null;
  var ZONE_RANK = {load: 0, accumulate: 1, fair: 2, pricey: 3};

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function clean(arr) {
    var out = [];
    (Array.isArray(arr) ? arr : []).forEach(function (t) { t = String(t || '').toUpperCase().trim(); if (RE.test(t) && out.indexOf(t) < 0) out.push(t); });
    return out.slice(0, MAX);
  }
  function cur() { var h = document.getElementById('tw-c-ticker'); return h ? h.textContent.trim().toUpperCase() : ''; }
  function sortMode() { try { return localStorage.getItem(SORT_KEY) || 'mine'; } catch (e) { return 'mine'; } }
  function isOpen() { try { return localStorage.getItem(OPEN_KEY) !== '0'; } catch (e) { return true; } }

  /* ── list + persistence ── */
  function init() {
    var server = window.TW_FAVS, local = null;
    try { local = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) {}
    favs = clean(Array.isArray(server) ? server : local);
    cache();
  }
  function cache() { try { localStorage.setItem(KEY, JSON.stringify(favs)); } catch (e) {} }
  function save() {
    cache();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      fetch('/api/favorites', {method: 'PUT', credentials: 'same-origin', keepalive: true, headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({tickers: favs})}).catch(function () {});
    }, 350);
    changed();
  }
  function changed() { paintAll(); subs.forEach(function (fn) { try { fn(favs.slice()); } catch (e) {} }); }
  function has(t) { return favs.indexOf(String(t || '').toUpperCase()) >= 0; }
  function toggle(t) {
    t = String(t || '').toUpperCase();
    if (!RE.test(t)) return false;
    var i = favs.indexOf(t);
    if (i >= 0) favs.splice(i, 1);
    else {
      if (favs.length >= MAX) { toast('Favorites hold up to ' + MAX + ' tickers'); return false; }
      favs.push(t); seedQuote(t);
    }
    save();
    toast(i >= 0 ? t + ' removed from favorites' : '★ ' + t + ' added to favorites');
    return i < 0;
  }

  /* ── prices: one shared live price, same as the rest of the page ── */
  function seedQuote(t) {
    if (quotes[t] && quotes[t].price) return;
    var row = document.querySelector('.tw-wl-row[data-ticker="' + t + '"]');
    if (row && row.dataset.price) {
      var pct = parseFloat(String(row.dataset.chg || '').replace(/[^0-9.+-]/g, ''));
      quotes[t] = {price: Number(row.dataset.price), pct: isFinite(pct) ? pct : null, chg: row.dataset.chg || ''};
    }
  }
  function onQuotes(d) {
    var any = false;
    ((d && d.quotes) || []).forEach(function (q) {
      if (!q || !q.ticker || !has(q.ticker) || q.price == null) return;
      var pct = q.ext_pct != null ? Number(q.ext_pct) : (q.gap_pct != null ? Number(q.gap_pct) : null);
      var tag = {post: 'AH ', pre: 'PRE ', overnight: 'O/N '}[q.ext] || '';
      var chg = pct == null ? '' : tag + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
      var prev = quotes[q.ticker];
      quotes[q.ticker] = {price: Number(q.price), pct: pct, chg: chg, moved: prev && prev.price !== Number(q.price) ? (Number(q.price) > prev.price ? 'up' : 'down') : ''};
      any = true;
    });
    if (any) paintStrip(true);
  }

  // The chart's own live tick (same 3 s) keeps the open ticker's chip exact.
  function onLive(d) {
    var q = d && d.quote, t = String((d && d.ticker) || '').toUpperCase();
    if (!q || !t || !has(t) || !(Number(q.price) > 0)) return;
    var px = Number(q.price), pct = null, tag = '';
    if ((q.ext === 'post' || q.ext === 'overnight') && Number(q.day_close) > 0) { pct = (px / Number(q.day_close) - 1) * 100; tag = q.ext === 'post' ? 'AH ' : 'O/N '; }
    else if (q.change_pct != null && isFinite(q.change_pct)) { pct = Number(q.change_pct); tag = q.ext === 'pre' ? 'PRE ' : ''; }
    else if (Number(q.prev_close) > 0) { pct = (px / Number(q.prev_close) - 1) * 100; tag = q.ext === 'pre' ? 'PRE ' : ''; }
    var prev = quotes[t];
    quotes[t] = {price: px, pct: pct, chg: pct == null ? (prev && prev.chg) || '' : tag + (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%',
      moved: prev && prev.price && prev.price !== px ? (px > prev.price ? 'up' : 'down') : ''};
    paintStrip(true);
  }

  /* ── stars on the header and on watchlist rows ── */
  function paintHeaderStar() {
    var b = document.getElementById('tw-fav-btn'); if (!b) return;
    var on = has(cur());
    b.classList.toggle('is-on', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.textContent = on ? '★' : '☆';
    b.title = on ? 'Remove ' + cur() + ' from favorites (*)' : 'Add ' + cur() + ' to favorites (*)';
  }
  function paintRowStars() {
    document.querySelectorAll('.tw-wl-row[data-ticker]').forEach(function (r) {
      var sym = r.querySelector('.tw-wl-sym'); if (!sym) return;
      var t = r.dataset.ticker, b = sym.querySelector('.fv-star');
      if (!b) {
        b = document.createElement('button');
        b.type = 'button'; b.className = 'fv-star'; b.setAttribute('data-fav-star', t);
        sym.insertBefore(b, sym.firstChild);
      }
      var on = has(t);
      b.classList.toggle('is-on', on); b.textContent = on ? '★' : '☆';
      b.title = on ? 'Remove from favorites' : 'Add to favorites';
      b.setAttribute('aria-label', (on ? 'Remove ' : 'Add ') + t + (on ? ' from' : ' to') + ' favorites');
    });
  }

  /* ── the strip ── */
  function buildStrip() {
    strip = document.getElementById('fv-strip');
    if (!strip || strip._built) return;
    strip._built = true;
    strip.innerHTML =
      '<button type="button" class="fv-head" data-fv="toggle" aria-expanded="true" title="Show or hide favorites"><span class="fv-head-star">★</span><span class="fv-head-txt">Favorites</span><b class="fv-count"></b><span class="fv-caret">▾</span></button>' +
      '<div class="fv-chips" role="list"></div>' +
      '<label class="fv-sort" title="Sort favorites"><select aria-label="Sort favorites">' +
        '<option value="mine">My order</option><option value="gain">Top gainers</option><option value="drop">Biggest drop</option><option value="zone">Buy zone first</option>' +
      '</select></label>' +
      '<button type="button" class="fv-add" data-fv="add" title="Star the ticker on the chart">＋</button>';
    var sel = strip.querySelector('select');
    sel.value = sortMode();
    sel.addEventListener('change', function () { try { localStorage.setItem(SORT_KEY, sel.value); } catch (e) {} paintStrip(); });
    strip.addEventListener('click', function (e) {
      var x = e.target.closest('[data-fv-x]');
      if (x) { e.stopPropagation(); toggle(x.getAttribute('data-fv-x')); return; }
      var act = e.target.closest('[data-fv]');
      if (act && act.getAttribute('data-fv') === 'toggle') {
        var open = !strip.classList.contains('is-open');
        try { localStorage.setItem(OPEN_KEY, open ? '1' : '0'); } catch (er) {}
        paintStrip(); return;
      }
      if (act && act.getAttribute('data-fv') === 'add') { var t = cur(); if (t && !has(t)) toggle(t); else if (window.twOpenTickerSearch) window.twOpenTickerSearch(''); return; }
      var chip = e.target.closest('[data-fv-t]');
      if (chip && !e.target.closest('.vz-badge')) openTicker(chip.getAttribute('data-fv-t'));
    });
    // Drag to reorder (My order only).
    strip.addEventListener('dragstart', function (e) {
      var c = e.target.closest('[data-fv-t]'); if (!c || sortMode() !== 'mine') { e.preventDefault(); return; }
      dragT = c.getAttribute('data-fv-t'); c.classList.add('is-drag');
      try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragT); } catch (er) {}
    });
    strip.addEventListener('dragover', function (e) {
      if (!dragT) return;
      var c = e.target.closest('[data-fv-t]'); if (!c) return;
      e.preventDefault();
      var t = c.getAttribute('data-fv-t'); if (t === dragT) return;
      var from = favs.indexOf(dragT), to = favs.indexOf(t);
      if (from < 0 || to < 0) return;
      favs.splice(from, 1); favs.splice(to, 0, dragT);
      paintStrip();
    });
    strip.addEventListener('dragend', function () { if (dragT) { dragT = null; save(); } });
  }
  function ordered() {
    var mode = sortMode(), list = favs.slice();
    if (mode === 'mine') return list;
    function pct(t) { var q = quotes[t]; return q && q.pct != null ? q.pct : null; }
    function zone(t) {
      var b = document.querySelector('[data-zone-badge="' + t + '"].vz-badge:not([hidden])');
      var m = b && /tone-(\w+)/.exec(b.className); return m && ZONE_RANK[m[1]] != null ? ZONE_RANK[m[1]] : 9;
    }
    return list.sort(function (a, b) {
      if (mode === 'zone') return zone(a) - zone(b) || favs.indexOf(a) - favs.indexOf(b);
      var pa = pct(a), pb = pct(b);
      if (pa == null && pb == null) return favs.indexOf(a) - favs.indexOf(b);
      if (pa == null) return 1; if (pb == null) return -1;
      return mode === 'gain' ? pb - pa : pa - pb;
    });
  }
  function chipHtml(t, i) {
    var q = quotes[t] || {}, pct = q.pct, dir = pct == null ? '' : (pct > 0 ? 'up' : (pct < 0 ? 'down' : ''));
    var heat = pct == null ? 0 : Math.min(1, Math.abs(pct) / 5);
    var on = t === cur();
    return '<div class="fv-chip' + (on ? ' is-cur' : '') + (dir ? ' is-' + dir : '') + '" role="listitem" data-fv-t="' + esc(t) + '" draggable="' + (sortMode() === 'mine') + '" tabindex="0" title="Open ' + esc(t) + (i < 9 ? ' (Alt+' + (i + 1) + ')' : '') + '">' +
      (i < 9 ? '<i class="fv-n">' + (i + 1) + '</i>' : '') +
      '<b class="fv-sym">' + esc(t) + '</b>' +
      '<span class="fv-px">' + (q.price ? Number(q.price).toFixed(2) : '—') + '</span>' +
      '<span class="fv-pct">' + esc(q.chg || '') + '</span>' +
      '<a class="vz-badge" data-zone-badge="' + esc(t) + '" hidden></a>' +
      '<span class="fv-heat" style="--heat:' + heat.toFixed(3) + '"></span>' +
      '<button type="button" class="fv-x" data-fv-x="' + esc(t) + '" aria-label="Remove ' + esc(t) + ' from favorites">×</button>' +
      '</div>';
  }
  function paintStrip(pricesOnly) {
    if (!strip) return;
    var open = isOpen();
    strip.classList.toggle('is-open', open);
    strip.classList.toggle('is-empty', !favs.length);
    strip.querySelector('.fv-head').setAttribute('aria-expanded', open ? 'true' : 'false');
    strip.querySelector('.fv-count').textContent = favs.length ? favs.length : '';
    var box = strip.querySelector('.fv-chips');
    if (!favs.length) {
      box.innerHTML = '<span class="fv-empty">Tap <b>☆</b> next to a ticker (or press <kbd>*</kbd>) to pin it here — live price, day move and value zone, one tap away.</span>';
      return;
    }
    if (pricesOnly && sortMode() === 'mine') {
      // Update in place so a chip being dragged or hovered isn't rebuilt.
      favs.forEach(function (t) {
        var c = box.querySelector('[data-fv-t="' + t + '"]'), q = quotes[t]; if (!c || !q) return;
        var px = c.querySelector('.fv-px'), txt = q.price ? Number(q.price).toFixed(2) : '—';
        if (px.textContent !== txt) { px.textContent = txt; if (q.moved) { c.classList.remove('flash-up', 'flash-down'); void c.offsetWidth; c.classList.add('flash-' + q.moved); } }
        c.querySelector('.fv-pct').textContent = q.chg || '';
        c.classList.toggle('is-up', q.pct > 0); c.classList.toggle('is-down', q.pct < 0);
        c.querySelector('.fv-heat').style.setProperty('--heat', (q.pct == null ? 0 : Math.min(1, Math.abs(q.pct) / 5)).toFixed(3));
      });
      return;
    }
    // Badges already painted keep their state across rebuilds.
    var keep = {};
    box.querySelectorAll('.vz-badge[data-zone-badge]').forEach(function (b) { if (!b.hidden) keep[b.getAttribute('data-zone-badge')] = [b.className, b.textContent, b.title, b.href]; });
    box.innerHTML = ordered().map(chipHtml).join('');
    var need = false;
    box.querySelectorAll('.vz-badge[data-zone-badge]').forEach(function (b) {
      var k = keep[b.getAttribute('data-zone-badge')];
      if (k) { b.className = k[0]; b.textContent = k[1]; b.title = k[2]; if (k[3]) b.href = k[3]; b.hidden = false; } else need = true;
    });
    if (need && window.vzLoadBadges) window.vzLoadBadges();
  }
  function paintAll() { paintHeaderStar(); paintRowStars(); paintStrip(); }

  function openTicker(t) {
    if (t === cur()) return;
    if (window.twOpenTicker) window.twOpenTicker({symbol: t});
    else location.href = '/terminal?ticker=' + encodeURIComponent(t);
  }

  var toastEl = null, toastTimer = null;
  function toast(msg) {
    var host = document.fullscreenElement || document.webkitFullscreenElement || document.body;
    if (!toastEl) { toastEl = document.createElement('div'); toastEl.className = 'fv-toast'; toastEl.setAttribute('role', 'status'); }
    if (toastEl.parentNode !== host) host.appendChild(toastEl);
    toastEl.textContent = msg; toastEl.classList.add('is-on');
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { toastEl.classList.remove('is-on'); }, 1600);
  }

  function boot() {
    init();
    buildStrip();
    favs.forEach(seedQuote);
    var b = document.getElementById('tw-fav-btn');
    if (b) b.addEventListener('click', function (e) { e.stopPropagation(); toggle(cur()); });
    document.addEventListener('click', function (e) {
      var s = e.target.closest('[data-fav-star]');
      if (s) { e.preventDefault(); e.stopPropagation(); toggle(s.getAttribute('data-fav-star')); }
    }, true);
    document.addEventListener('keydown', function (e) {
      var tag = (e.target && e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target && e.target.isContentEditable)) return;
      if (e.key === '*' && !e.ctrlKey && !e.metaKey && !e.altKey) { var t = cur(); if (t) { e.preventDefault(); toggle(t); } return; }
      if (e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && /^Digit[1-9]$/.test(e.code || '')) {
        var list = ordered(), t2 = list[Number(e.code.slice(5)) - 1];
        if (t2) { e.preventDefault(); e.stopPropagation(); openTicker(t2); }
      }
      if ((e.key === 'Enter' || e.key === ' ') && e.target.closest && e.target.closest('[data-fv-t]') && !e.target.closest('button')) {
        e.preventDefault(); openTicker(e.target.closest('[data-fv-t]').getAttribute('data-fv-t'));
      }
    }, true);
    var head = document.getElementById('tw-c-ticker');
    if (head && 'MutationObserver' in window) new MutationObserver(function () { paintHeaderStar(); paintStrip(); }).observe(head, {childList: true, characterData: true, subtree: true});
    if (window.TSLive) window.TSLive.on('quotes', onQuotes);
    if (window.TSLive) window.TSLive.on('live', onLive);
    // The account's list wins over a stale local cache (another device may have changed it).
    if (!Array.isArray(window.TW_FAVS)) {
      fetch('/api/favorites', {credentials: 'same-origin', cache: 'no-store'}).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && d.ok && JSON.stringify(clean(d.tickers)) !== JSON.stringify(favs)) { favs = clean(d.tickers); cache(); favs.forEach(seedQuote); changed(); } }).catch(function () {});
    }
    paintAll();
  }

  window.TSFav = {
    list: function () { return favs.slice(); }, has: has, toggle: toggle,
    on: function (fn) { subs.push(fn); }, quote: function (t) { return quotes[t] || null; }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
