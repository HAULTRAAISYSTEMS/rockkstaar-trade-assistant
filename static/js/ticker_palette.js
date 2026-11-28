/* Ticker search on the chart.
   Opens over the chart (works in full screen): start typing a ticker or a
   company name anywhere on the Terminal, press Ctrl/⌘+K, or tap the search
   button. Your watchlist names come first, then any US stock or ETF.
   ↑↓ to move, Enter to open, Esc to close. Opening a ticker keeps the
   timeframe, drawings and full-screen state. */
(function () {
  var RECENT_KEY = 'tradestaar.recentTickers', RECENT_MAX = 8;
  var el = null, input = null, list = null, recentBox = null, items = [], cur = 0, timer = null, ctrl = null, lastQ = '';

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]; }); }
  function recent() { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]').filter(function (t) { return /^[A-Z][A-Z0-9.]{0,7}$/.test(t); }); } catch (e) { return []; } }
  function remember(t) {
    t = String(t || '').toUpperCase(); if (!t) return;
    try { localStorage.setItem(RECENT_KEY, JSON.stringify([t].concat(recent().filter(function (x) { return x !== t; })).slice(0, RECENT_MAX))); } catch (e) {}
  }
  window.twRememberTicker = remember;

  /* Watchlist rows already on the page: ticker, name, price, change. */
  function watchRows() {
    var out = {}, order = [];
    document.querySelectorAll('.tw-wl-row[data-ticker]').forEach(function (r) {
      var d = r.dataset, t = String(d.ticker || '').toUpperCase();
      if (!t || out[t]) return;
      out[t] = {symbol: t, name: d.name || '', price: d.price || '', chg: d.chg || '', dir: d.chgdir || '', row: r, watch: true};
      order.push(t);
    });
    return {map: out, order: order};
  }

  function build() {
    if (el) return;
    el = document.createElement('div');
    el.className = 'tp-overlay'; el.hidden = true;
    el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true'); el.setAttribute('aria-label', 'Search a ticker');
    el.innerHTML =
      '<div class="tp-card">' +
        '<div class="tp-input-row"><span class="tp-glyph" aria-hidden="true">⌕</span>' +
        '<input class="tp-input" type="text" autocomplete="off" autocorrect="off" autocapitalize="characters" spellcheck="false" enterkeyhint="go" placeholder="Ticker or company — NVDA, apple, s&amp;p 500" aria-label="Ticker or company" role="combobox" aria-expanded="true" aria-controls="tp-list">' +
        '<button type="button" class="tp-clear" aria-label="Clear" hidden>×</button>' +
        '<kbd class="tp-esc">Esc</kbd><button type="button" class="tp-cancel">Cancel</button></div>' +
        '<div class="tp-recent" aria-label="Recent tickers"></div>' +
        '<ul class="tp-list" id="tp-list" role="listbox"></ul>' +
        '<div class="tp-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span>Start typing on the chart to search</span></div>' +
      '</div>';
    input = el.querySelector('.tp-input'); list = el.querySelector('.tp-list'); recentBox = el.querySelector('.tp-recent');
    el.addEventListener('mousedown', function (e) { if (e.target === el) close(); });
    el.querySelector('.tp-cancel').addEventListener('click', close);
    var clr = el.querySelector('.tp-clear');
    clr.addEventListener('click', function () { input.value = ''; clr.hidden = true; schedule(); input.focus(); });
    input.addEventListener('input', function () { clr.hidden = !input.value; schedule(); });
    // Phone: scrolling the results puts the keyboard away so you can see them all.
    list.addEventListener('touchstart', function () { if (phone()) input.blur(); }, {passive: true});
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter') { e.preventDefault(); pick(items[cur] || {symbol: input.value.trim().toUpperCase()}); }
      else if (e.key === 'Escape') { e.preventDefault(); close(); }
    });
    list.addEventListener('mousemove', function (e) { var li = e.target.closest('[data-i]'); if (li) setCur(Number(li.dataset.i)); });
    list.addEventListener('click', function (e) { var li = e.target.closest('[data-i]'); if (li) pick(items[Number(li.dataset.i)]); });
    recentBox.addEventListener('click', function (e) { var b = e.target.closest('[data-t]'); if (b) pick({symbol: b.dataset.t}); });
  }

  /* Phones get their own layout: a full-screen sheet with the search box on
     top, one-line rows, and the keyboard up straight away. */
  function phone() {
    try { return window.matchMedia('(max-width: 760px), (pointer: coarse) and (max-width: 1099px)').matches; } catch (e) { return false; }
  }
  function panel() { return (typeof twChartPanel === 'function' && twChartPanel()) || null; }
  function host() {
    var p = panel();
    var fs = document.fullscreenElement || document.webkitFullscreenElement || (p && p.classList.contains('tw-is-fullscreen') ? p : null);
    if (fs) return fs;                       // full screen: only what's inside it is drawn
    if (phone()) return document.body;       // phone: cover the whole screen
    // Desktop: inside the chart panel.
    return p || document.getElementById('tw-chart-box') || document.body;
  }
  function fitViewport() {
    // iOS keeps the layout viewport full height when the keyboard opens; size the
    // sheet to what's actually visible so the results aren't under the keyboard.
    if (!el || el.hidden || !el.classList.contains('tp-phone')) return;
    var vv = window.visualViewport;
    if (!vv) return;
    el.style.height = Math.round(vv.height) + 'px';
    el.style.top = Math.round(vv.offsetTop) + 'px';
  }
  if (window.visualViewport) { window.visualViewport.addEventListener('resize', fitViewport); window.visualViewport.addEventListener('scroll', fitViewport); }

  function open(prefill) {
    build();
    var h = host(), ph = phone();
    el.classList.toggle('tp-phone', ph);
    input.placeholder = ph ? 'Search NVDA, Apple, S&P 500' : 'Ticker or company — NVDA, apple, s&p 500';
    try { if (h !== document.body && getComputedStyle(h).position === 'static') h.style.position = 'relative'; } catch (e) {}
    if (el.parentNode !== h) h.appendChild(el);
    el.hidden = false;
    input.value = prefill || '';
    el.querySelector('.tp-clear').hidden = !input.value;
    // Focus now, inside the tap: iPhone only raises the keyboard for a focus
    // that happens in the same gesture (a setTimeout loses it).
    try { input.focus({preventScroll: true}); } catch (e) { input.focus(); }
    var n = input.value.length; try { input.setSelectionRange(n, n); } catch (e) {}
    if (ph) document.documentElement.classList.add('tp-locked');
    renderRecent();
    render(localMatches(input.value), true);
    if (input.value) schedule();
    fitViewport();
    if (!ph) setTimeout(function () { if (document.activeElement !== input) input.focus(); }, 0);
  }
  function close() {
    if (!el) return;
    el.hidden = true;
    el.style.height = ''; el.style.top = '';
    document.documentElement.classList.remove('tp-locked');
    try { input.blur(); } catch (e) {}
    if (ctrl) { try { ctrl.abort(); } catch (e) {} }
  }
  window.twOpenTickerSearch = open;

  function renderRecent() {
    var r = recent(), w = watchRows().map;
    if (!r.length) { recentBox.hidden = true; return; }
    recentBox.hidden = false;
    recentBox.innerHTML = '<span class="tp-label">Recent</span>' + r.map(function (t) {
      var x = w[t], chg = x && x.chg ? '<i class="' + (x.dir === 'pos' ? 'is-up' : x.dir === 'neg' ? 'is-down' : '') + '">' + esc(x.chg) + '</i>' : '';
      return '<button type="button" class="tp-chip" data-t="' + esc(t) + '"><b>' + esc(t) + '</b>' + chg + '<a class="vz-badge" data-zone-badge="' + esc(t) + '" hidden></a></button>';
    }).join('');
    if (window.vzLoadBadges) window.vzLoadBadges();
  }

  function localMatches(q) {
    var w = watchRows(), Q = String(q || '').trim().toUpperCase(), out = [];
    if (!Q) return w.order.slice(0, 8).map(function (t) { return w.map[t]; });
    w.order.forEach(function (t) {
      var x = w.map[t], name = String(x.name || '').toUpperCase(), score = -1;
      if (t === Q) score = 0; else if (t.indexOf(Q) === 0) score = 1; else if (name.indexOf(Q) === 0) score = 2;
      else if (name.indexOf(Q) >= 0 || t.indexOf(Q) >= 0) score = 3;
      if (score >= 0) out.push({s: score, x: x});
    });
    return out.sort(function (a, b) { return a.s - b.s || a.x.symbol.localeCompare(b.x.symbol); }).slice(0, 8).map(function (o) { return o.x; });
  }

  function schedule() {
    clearTimeout(timer);
    var q = input.value.trim();
    render(localMatches(q), !q);
    if (!q) return;
    timer = setTimeout(function () { remote(q); }, 160);
  }
  function remote(q) {
    if (ctrl) { try { ctrl.abort(); } catch (e) {} }
    ctrl = window.AbortController ? new AbortController() : null;
    lastQ = q;
    list.classList.add('is-loading');
    fetch('/api/symbols/search?q=' + encodeURIComponent(q), {credentials: 'same-origin', signal: ctrl ? ctrl.signal : undefined})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.ok || q !== input.value.trim()) return;
        var local = localMatches(q), seen = {};
        local.forEach(function (x) { seen[x.symbol] = 1; });
        var w = watchRows().map;
        var more = (d.results || []).filter(function (r) { return !seen[r.symbol]; }).map(function (r) {
          var x = w[r.symbol];
          return x ? Object.assign({}, x, {name: x.name || r.name, type: r.type}) : {symbol: r.symbol, name: r.name, type: r.type};
        });
        // An exact ticker hit goes to the top even when it isn't on a watchlist.
        var all = local.concat(more), Q = q.toUpperCase().replace(/\s+/g, '');
        all.sort(function (a, b) { return (b.symbol === Q) - (a.symbol === Q); });
        render(all.slice(0, 12), false);
      }).catch(function () {}).then(function () { list.classList.remove('is-loading'); });
  }

  function render(arr, isDefault) {
    items = arr || []; cur = 0;
    if (!items.length) {
      var q = input.value.trim().toUpperCase();
      list.innerHTML = q ? '<li class="tp-empty">No match yet — press <kbd>Enter</kbd> to open <b>' + esc(q) + '</b> anyway.</li>' : '';
      return;
    }
    list.innerHTML = (isDefault ? '<li class="tp-label tp-label--list">Your watchlist</li>' : '') + items.map(function (x, i) {
      var price = x.price ? '<span class="tp-px">' + esc(x.price) + '</span>' : '';
      var chg = x.chg ? '<span class="tp-chg ' + (x.dir === 'pos' ? 'is-up' : x.dir === 'neg' ? 'is-down' : '') + '">' + esc(x.chg) + '</span>' : '';
      var tag = x.type ? '<span class="tp-type">' + esc(x.type) + '</span>' : '';
      var watch = x.watch ? '<span class="tp-watch" title="On your watchlist">★</span>' : '';
      return '<li class="tp-item' + (x.watch ? ' is-watch' : '') + '" role="option" data-i="' + i + '" id="tp-opt-' + i + '">' +
        '<span class="tp-sym">' + esc(x.symbol) + '</span>' +
        '<span class="tp-name">' + esc(x.name || '') + tag + '</span>' +
        '<span class="tp-meta">' + watch + '<a class="vz-badge" data-zone-badge="' + esc(x.symbol) + '" hidden></a>' + price + chg + '</span></li>';
    }).join('');
    setCur(0);
    if (window.vzLoadBadges) window.vzLoadBadges();
  }
  function setCur(i) {
    cur = Math.max(0, Math.min(items.length - 1, i));
    list.querySelectorAll('.tp-item').forEach(function (li) { li.classList.toggle('is-cur', Number(li.dataset.i) === cur); });
    var c = list.querySelector('.tp-item.is-cur');
    if (c) { input.setAttribute('aria-activedescendant', c.id); if (c.scrollIntoView) c.scrollIntoView({block: 'nearest'}); }
  }
  function move(d) { if (items.length) setCur(cur + d); }

  /* Open a ticker on the chart: its watchlist row when it has one, else a
     stand-in row so any symbol works. */
  function pick(x) {
    var t = String((x && x.symbol) || '').toUpperCase().replace(/\s+/g, '');
    if (!/^[A-Z][A-Z0-9.]{0,7}$/.test(t)) return;
    close();
    remember(t);
    var row = document.querySelector('.tw-wl-row[data-ticker="' + t + '"]');
    if (!row) {
      row = document.createElement('div');
      row.className = 'tw-wl-row';
      row.dataset.ticker = t; row.dataset.name = (x && x.name) || ''; row.dataset.price = ''; row.dataset.chg = '';
    }
    if (typeof twSelect === 'function') twSelect(row);
    if (typeof twShowChartTab === 'function') twShowChartTab();
    if (row.isConnected && row.scrollIntoView) { try { row.scrollIntoView({block: 'nearest'}); } catch (e) {} }
    try { history.replaceState(null, '', location.pathname + '?ticker=' + encodeURIComponent(t)); } catch (e) {}
  }
  window.twOpenTicker = pick;

  /* Start typing on the Terminal (not in a field) to search; Ctrl/⌘+K too. */
  document.addEventListener('keydown', function (e) {
    if (!document.getElementById('tw-chart-box')) return;
    var tag = (e.target && e.target.tagName || '').toLowerCase();
    var inField = tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target && e.target.isContentEditable);
    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); open(''); return; }
    if (inField || e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
    if (el && !el.hidden) return;
    if (/^[a-zA-Z]$/.test(e.key)) { e.preventDefault(); open(e.key.toUpperCase()); }
  });
})();
