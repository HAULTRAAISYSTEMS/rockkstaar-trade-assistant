/* Right-click menus, app-wide (press and hold on a phone).
 *
 * One menu, many places: a page or script registers a provider with
 *   TSMenu.register({ priority, match(target, ev) -> ctx|null, build(ctx, ev) -> spec })
 * (or pushes it onto window.TSMenuQueue before this file loads). The first
 * provider whose match() answers wins; when none does, the browser's own
 * menu shows as usual. Shift + right-click always gives the browser menu,
 * and so do text boxes and selected text (copy / paste keep working).
 *
 * spec = { title, sub?, items: [...] } where an item is one of
 *   { label, icon?, hint?, danger?, disabled?, checked?, run(api) }
 *   { label, icon?, sub: items | () => items | Promise<items> }   drill-in
 *   { input: { label, value, prefix?, button, chips?, submit(value, api) } }
 *   { swatches: [colors], current?, pick(color, api) }
 *   { sep: true }   { note: 'text' }
 * `label` may be a function(ctx) so it can change once data arrives;
 * call api.refresh() to redraw the current level.
 *
 * Ticker menus come built in: anything with data-ticker / data-symbol, a
 * /stock/NVDA link or a ?ticker=NVDA link gets chart, M.C.F.F., Thesis,
 * insider trades, price alert, watchlists, globe orbit and copy.
 */
(function () {
  'use strict';
  if (window.TSMenu) return;

  var SYM = /^[A-Z][A-Z0-9.\-]{0,9}$/;
  var providers = [];
  var menu = null, stack = [], ctx = null, lastOpen = 0, pressAt = 0;

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function money(v) {
    v = Number(v); if (!isFinite(v)) return '';
    return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) < 1 ? 4 : 2 });
  }
  function pct(v) { v = Number(v); return isFinite(v) ? (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) + '%' : ''; }

  /* ── Icons (24-unit strokes) ─────────────────────────────────── */
  var IC = {
    chart: '<path d="M4 19h16"/><path d="M7 15V9M7 9V6M12 16v-5M12 11V8M17 13V7"/><rect x="5.5" y="9" width="3" height="5" rx=".6"/><rect x="10.5" y="11" width="3" height="4" rx=".6"/><rect x="15.5" y="7" width="3" height="5" rx=".6"/>',
    page: '<rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M8.5 8h7M8.5 12h7M8.5 16h4"/>',
    shield: '<path d="M12 3.5l7 3v5.2c0 4.3-3 7.4-7 8.8-4-1.4-7-4.5-7-8.8V6.5z"/><path d="M9 12.2l2.1 2.1L15.3 10"/>',
    target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r=".8"/>',
    people: '<circle cx="9" cy="8.5" r="3"/><path d="M3.5 19c.6-3.2 2.8-5 5.5-5s4.9 1.8 5.5 5"/><circle cx="16.5" cy="9.5" r="2.4"/><path d="M15.5 14.2c2.6-.2 4.5 1.4 5 4.3"/>',
    bell: '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    list: '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.5" cy="6.5" r="1.2"/><circle cx="4.5" cy="12" r="1.2"/><circle cx="4.5" cy="17.5" r="1.2"/>',
    orbit: '<circle cx="12" cy="12" r="4.2"/><ellipse cx="12" cy="12" rx="9.5" ry="4" transform="rotate(-24 12 12)"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8"/>',
    trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13"/><path d="M10.5 11v5.5M13.5 11v5.5"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    ext: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    compare: '<path d="M7 4v16M17 4v16"/><path d="M4 8l3-3 3 3M14 16l3 3 3-3"/>',
    spin: '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4.5v4h-4"/>',
    save: '<path d="M6 3.5h12v17l-6-4-6 4z"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    lock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
    palette: '<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.3 0 1.9-.8 1.9-1.8 0-1.4-1.2-1.6-1.2-2.7 0-.9.7-1.5 1.6-1.5H17a3.5 3.5 0 0 0 3.5-3.5c0-4.2-3.8-7.5-8.5-7.5z"/><circle cx="7.8" cy="11" r="1.1"/><circle cx="10.5" cy="7.4" r="1.1"/><circle cx="15" cy="7.6" r="1.1"/>',
    dup: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/><path d="M14 11.5v5M11.5 14h5"/>',
    front: '<rect x="4" y="4" width="10" height="10" rx="1.5" opacity=".45"/><rect x="10" y="10" width="10" height="10" rx="1.5"/>',
    hline: '<path d="M3 12h18"/><circle cx="12" cy="12" r="1.8"/>',
    hray: '<path d="M7 12h14"/><circle cx="6" cy="12" r="1.8"/>',
    vline: '<path d="M12 3v18"/><circle cx="12" cy="12" r="1.8"/>',
    text: '<path d="M5 6h14M12 6v13M9 19h6"/>',
    long: '<path d="M4 4h16v8H4z" fill="rgba(34,197,139,.28)"/><path d="M4 12h16v7H4z" fill="rgba(255,77,69,.24)"/>',
    short: '<path d="M4 5h16v7H4z" fill="rgba(255,77,69,.24)"/><path d="M4 12h16v8H4z" fill="rgba(34,197,139,.28)"/>',
    pen: '<path d="M5 19 19 5"/><circle cx="5" cy="19" r="1.8"/><circle cx="19" cy="5" r="1.8"/>',
    wave: '<path d="M3 15c3-6 5-6 7-2s4 4 6-1 4-4 5-2"/>',
    undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    eye: '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.6"/>',
    reset: '<path d="M4 12a8 8 0 1 0 2.3-5.7"/><path d="M4 4.5v4h4"/>',
    full: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    zoomin: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2M8.5 11h5M11 8.5v5"/>',
    zoomout: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2M8.5 11h5"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 3.6 5.4 3.6 8.5s-1.1 5.9-3.6 8.5c-2.5-2.6-3.6-5.4-3.6-8.5s1.1-5.9 3.6-8.5z"/>',
    wallet: '<rect x="3.5" y="6.5" width="17" height="12" rx="2"/><path d="M16 12.5h4.5M3.5 9.5h17"/>',
    ticket: '<path d="M4 7.5h16v3a2 2 0 0 0 0 3.5v3H4v-3a2 2 0 0 0 0-3.5z"/><path d="M14 7.5v9.5" stroke-dasharray="1.6 1.8"/>',
    ruler: '<path d="M12 3v18M8 7l4-4 4 4M8 17l4 4 4-4"/>',
    fib: '<path d="M3 5h18M3 9.5h18M3 14h18M3 19h18"/>',
    rect: '<rect x="4" y="6" width="16" height="12" rx="1.5"/>',
    chan: '<path d="M3 14 14 3M10 21 21 10"/>',
    star: '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9 6.8 19.6l1-5.8-4.3-4.1 5.9-.9z"/>'
  };
  function icon(n) {
    return IC[n] ? '<svg class="tscm-ic" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + IC[n] + '</svg>' : '<span class="tscm-ic"></span>';
  }

  /* ── Toast ───────────────────────────────────────────────────── */
  function toast(msg, opts) {
    opts = opts || {};
    var el = document.getElementById('tscm-toast');
    if (!el) {
      el = document.createElement('div'); el.id = 'tscm-toast'; el.className = 'tscm-toast';
      el.setAttribute('role', 'status'); el.setAttribute('aria-live', 'polite');
    }
    host().appendChild(el);
    el.innerHTML = '<span></span>' + (opts.undo ? '<button type="button">Undo</button>' : '');
    el.firstChild.textContent = msg;
    el.classList.toggle('is-bad', !!opts.bad);
    if (opts.undo) el.querySelector('button').addEventListener('click', function () { el.classList.remove('on'); opts.undo(); });
    // Reflow so a second toast in a row animates in again.
    el.classList.remove('on'); void el.offsetWidth; el.classList.add('on');
    clearTimeout(el._t); el._t = setTimeout(function () { el.classList.remove('on'); }, opts.undo ? 6500 : 3600);
  }

  function host() { return document.fullscreenElement || document.webkitFullscreenElement || document.body; }

  /* ── Shared actions ──────────────────────────────────────────── */
  function getJSON(url) {
    return fetch(url, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
      .then(function (r) { return r.json().catch(function () { return { ok: false }; }); });
  }
  function postJSON(url, body) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body || {}) })
      .then(function (r) { return r.json().catch(function () { return { ok: false, error: 'Request failed (' + r.status + ')' }; }); });
  }
  function copy(text, label) {
    var done = function () { toast('Copied ' + (label || text)); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    else { fallbackCopy(text); done(); }
  }
  function fallbackCopy(text) {
    var ta = document.createElement('textarea'); ta.value = text; ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;left:-9999px;top:0'; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    ta.remove();
  }
  function go(url, newTab) { if (newTab) window.open(url, '_blank', 'noopener'); else location.href = url; }

  function setAlert(t, price, current) {
    price = Number(String(price).replace(/[$,\s]/g, ''));
    if (!(price > 0)) { toast('Enter a price above $0', { bad: true }); return Promise.resolve(false); }
    return postJSON('/api/alerts/price', { ticker: t, price: Math.round(price * 10000) / 10000, current: current || null, note: 'Price alert' })
      .then(function (d) {
        if (d && d.ok) {
          toast('🔔 ' + t + ' alert set ' + (d.alert.direction === 'above' ? '↑ at or above ' : '↓ at or below ') + money(d.alert.price));
          document.dispatchEvent(new CustomEvent('tsmenu:alert', { detail: d.alert }));
          return true;
        }
        toast((d && d.error) || 'Could not set the alert', { bad: true }); return false;
      }).catch(function () { toast('Could not set the alert — check your connection', { bad: true }); return false; });
  }

  // The globe orbit: on the Command Center the globe owns the list (instant);
  // anywhere else read it, change it and save it back.
  function onCommandCenter() { return !!document.getElementById('cmd27-orbit'); }
  function setOrbit(t, on) {
    if (onCommandCenter()) {
      document.dispatchEvent(new CustomEvent('cmd27:orbit-set', { detail: { symbol: t, on: on } }));
      toast(on ? t + ' added to your globe orbit' : t + ' removed from your globe orbit',
        { undo: function () { document.dispatchEvent(new CustomEvent('cmd27:orbit-set', { detail: { symbol: t, on: !on } })); } });
      return Promise.resolve(true);
    }
    return getJSON('/api/command/orbit').then(function (d) {
      var list = (d && d.tickers) || [], i = list.indexOf(t), max = (d && d.max) || 12;
      if (on && i >= 0) { toast(t + ' is already in your globe orbit'); return true; }
      if (!on && i < 0) return true;
      if (on) { if (list.length >= max) { toast('Your orbit is full (' + max + '). Remove one first.', { bad: true }); return false; } list.push(t); }
      else list.splice(i, 1);
      return postJSON('/api/command/orbit', { tickers: list }).then(function (r) {
        if (r && r.ok) { toast(on ? t + ' added to your globe orbit' : t + ' removed from your globe orbit'); return true; }
        toast((r && r.error) || 'Could not save the orbit', { bad: true }); return false;
      });
    }).catch(function () { toast('Could not save the orbit', { bad: true }); return false; });
  }

  function setList(t, list, on) {
    return postJSON('/api/ticker-menu/' + encodeURIComponent(t) + '/watchlist', { watchlist_id: list.id, action: on ? 'add' : 'remove' })
      .then(function (d) {
        if (d && d.ok) { list.has = on; toast(on ? t + ' added to ' + list.name : t + ' removed from ' + list.name); return true; }
        toast((d && d.error) || 'Could not update the list', { bad: true }); return false;
      }).catch(function () { toast('Could not update the list', { bad: true }); return false; });
  }

  /* ── Finding the ticker under the pointer ────────────────────── */
  function linkTicker(a) {
    var href = a.getAttribute('href') || '';
    if (/^(mailto:|tel:|javascript:|#)/i.test(href)) return null;
    var u; try { u = new URL(href, location.href); } catch (e) { return null; }
    if (u.origin !== location.origin) return null;
    var m = /^\/stock\/([A-Za-z0-9.\-]{1,10})(?:\/|$)/.exec(u.pathname);
    var t = m ? m[1] : (u.searchParams.get('ticker') || u.searchParams.get('symbol') || '');
    t = String(t).toUpperCase();
    return SYM.test(t) ? t : null;
  }
  function attrTicker(el) {
    var t = (el.getAttribute('data-ticker') || el.getAttribute('data-symbol') || el.getAttribute('data-sym') || '').toUpperCase().trim();
    return SYM.test(t) ? t : null;
  }
  // Big page wrappers carry the page's ticker too; a right-click on their
  // empty space should still give the browser menu.
  var WRAPPERS = /^(MAIN|BODY|HTML|FORM)$/;
  function findTicker(target) {
    for (var el = target; el && el.nodeType === 1 && el !== document.body; el = el.parentElement) {
      if (el.hasAttribute('data-no-menu')) return null;
      if (el.tagName === 'A') { var lt = linkTicker(el); if (lt) return { ticker: lt, el: el, link: el.href }; continue; }
      if (WRAPPERS.test(el.tagName) || el.id === 'lab' || el.classList.contains('fa-position') || el.id === 'sd-sec-intelligence') continue;
      if (el.hasAttribute('data-ticker') || el.hasAttribute('data-symbol') || el.hasAttribute('data-sym')) {
        var t = attrTicker(el); if (t) return { ticker: t, el: el };
      }
    }
    return null;
  }

  function info(c) {
    if (!c.infoP) {
      c.infoP = getJSON('/api/ticker-menu/' + encodeURIComponent(c.ticker)).then(function (d) {
        c.info = d && d.ok ? d : { lists: [], in_orbit: false };
        if (c.price == null && c.info.price) c.price = c.info.price;
        if (c.chg == null && c.info.change_pct != null) c.chg = c.info.change_pct;
        return c.info;
      }).catch(function () { c.info = { lists: [], in_orbit: false }; return c.info; });
    }
    return c.infoP;
  }

  // The standard ticker menu. `extra` items go first (a chip's "Spin to").
  function tickerItems(c, extra) {
    var t = c.ticker, enc = encodeURIComponent(t), here = location.pathname;
    var items = (extra || []).slice();
    items.push(
      { icon: 'chart', label: 'Open chart', hint: 'Terminal', run: function () { go('/terminal?ticker=' + enc); }, hide: here === '/terminal' && c.inChart },
      { icon: 'shield', label: 'M.C.F.F. check', run: function () { go('/fundamentals?tab=mcff&ticker=' + enc); } },
      { icon: 'target', label: 'Thesis & price map', run: function () { go('/thesis?ticker=' + enc); } },
      { icon: 'people', label: 'Insider trades', run: function () { go('/smart-money?ticker=' + enc); } },
      { icon: 'page', label: 'Stock page', run: function () { go('/stock/' + enc); } },
      { sep: true },
      { icon: 'bell', label: 'Set price alert', sub: function () { return alertItems(c); } },
      { icon: 'list', label: 'Watchlists', sub: function () { return listItems(c); } }
    );
    if (c.listId) {
      items.push({ icon: 'trash', danger: true, label: function () { return 'Remove from ' + (c.listName || 'this list'); }, run: function () {
        return setList(t, { id: c.listId, name: c.listName || 'this list' }, false).then(function (ok) {
          if (!ok) return;
          var row = c.el && c.el.closest('[data-ticker]');
          if (row && c.listBox && c.listBox.contains(row)) { row.classList.add('tscm-gone'); setTimeout(function () { row.remove(); }, 260); }
        });
      } });
    }
    items.push({ icon: 'orbit', label: function () {
      if (!c.info && c.orbit == null) return 'Globe orbit';
      var inOrbit = c.orbit != null ? c.orbit : c.info.in_orbit;
      return inOrbit ? 'Remove from globe orbit' : 'Add to globe orbit';
    }, run: function () {
      return info(c).then(function (d) {
        var inOrbit = c.orbit != null ? c.orbit : d.in_orbit;
        return setOrbit(t, !inOrbit);
      });
    } });
    items.push({ icon: 'compare', label: 'Compare with…', sub: function () {
      return [{ input: { label: 'Compare ' + t + ' with', placeholder: 'AMD, MSFT', button: 'Compare', submit: function (v) {
        var others = String(v || '').toUpperCase().split(/[\s,]+/).filter(function (x) { return SYM.test(x) && x !== t; }).slice(0, 2);
        if (!others.length) { toast('Type one or two tickers', { bad: true }); return false; }
        go('/compare?symbols=' + [t].concat(others).join(','));
      } } }];
    } });
    var card = c.el && c.el.closest('article, li, .card, [class*="news"], [class*="story"]');
    var saveBtn = card && card.querySelector('[data-save]');
    if (saveBtn) items.push({ sep: true }, { icon: 'save', label: saveBtn.classList.contains('is-saved') ? 'Open in Research Memory' : 'Save story to Research Memory', run: function () { saveBtn.click(); } });
    items.push({ sep: true });
    if (c.link) items.push({ icon: 'ext', label: 'Open in new tab', run: function () { go(c.link, true); } });
    items.push({ icon: 'copy', label: 'Copy ticker', hint: t, run: function () { copy(t); } });
    if (c.link) items.push({ icon: 'link', label: 'Copy link', run: function () { copy(c.link, 'link'); } });
    return items.filter(function (i) { return !i.hide; });
  }

  function alertItems(c) {
    return info(c).then(function () {
      var p = c.price, rows = [];
      var chips = p ? [-10, -5, -2, 2, 5, 10].map(function (d) { return { label: (d > 0 ? '+' : '−') + Math.abs(d) + '%', value: (p * (1 + d / 100)).toFixed(2) }; }) : [];
      rows.push({ input: { label: 'Alert me when ' + c.ticker + ' reaches', prefix: '$', value: p ? Number(p).toFixed(2) : '', inputmode: 'decimal', button: 'Set alert', chips: chips,
        submit: function (v) { return setAlert(c.ticker, v, p).then(function (ok) { return ok; }); } } });
      rows.push({ note: p ? 'Now ' + money(p) + (c.chg != null ? ' · ' + pct(c.chg) : '') + '. Fires once, then notifies you.' : 'No live quote right now. Type the price you want.' });
      return rows;
    });
  }

  function listItems(c) {
    return info(c).then(function (d) {
      var lists = d.lists || [];
      if (!lists.length) return [{ note: 'No watchlists yet.' }, { icon: 'plus', label: 'Create one', run: function () { go('/watchlists'); } }];
      var mine = lists.filter(function (l) { return !l.auto; }), auto = lists.filter(function (l) { return l.auto; });
      function row(l) {
        return { icon: 'list', checked: function () { return !!l.has; }, label: l.name, keep: true,
          run: function (api) { return setList(c.ticker, l, !l.has).then(function () { api.refresh(); }); } };
      }
      var out = [{ note: 'Tap a list to add ' + c.ticker + ' (✓ = already in it, tap again to remove).' }];
      out = out.concat(mine.map(row));
      if (auto.length) { out.push({ sep: true }, { note: 'Automatic lists' }); out = out.concat(auto.map(row)); }
      out.push({ sep: true }, { icon: 'page', label: 'Manage watchlists', run: function () { go('/watchlists'); } });
      return out;
    });
  }

  /* ── Menu UI ─────────────────────────────────────────────────── */
  function close() {
    if (!menu) return;
    var m = menu; menu = null; stack = []; ctx = null;
    m.classList.remove('on');
    setTimeout(function () { m.remove(); }, 140);
    document.removeEventListener('keydown', onKey, true);
  }

  function labelOf(it) { return typeof it.label === 'function' ? it.label(ctx) : it.label; }

  function render() {
    if (!menu) return;
    var level = stack[stack.length - 1], body = menu.querySelector('.tscm-body');
    var hd = menu.querySelector('.tscm-hd');
    var title = level.title || '';
    hd.innerHTML = (stack.length > 1 ? '<button type="button" class="tscm-back" aria-label="Back">‹</button>' : '') +
      '<b>' + esc(title) + '</b>' + (level.sub ? '<span class="tscm-hd-sub">' + esc(typeof level.sub === 'function' ? level.sub(ctx) : level.sub) + '</span>' : '');
    var back = hd.querySelector('.tscm-back'); if (back) back.addEventListener('click', function (e) { e.stopPropagation(); pop(); });
    if (level.loading) { body.innerHTML = '<div class="tscm-note tscm-loading">Loading…</div>'; return; }
    var html = '';
    level.items.forEach(function (it, i) {
      if (it.sep) { html += '<div class="tscm-sep" role="separator"></div>'; return; }
      if (it.note) { html += '<div class="tscm-note">' + esc(it.note) + '</div>'; return; }
      if (it.input) {
        var f = it.input;
        html += '<form class="tscm-form" data-i="' + i + '"><label>' + esc(f.label) + '</label><div class="tscm-field">' +
          (f.prefix ? '<span>' + esc(f.prefix) + '</span>' : '') +
          '<input type="text" autocomplete="off" spellcheck="false"' + (f.inputmode ? ' inputmode="' + f.inputmode + '"' : '') + ' value="' + esc(f.value || '') + '" placeholder="' + esc(f.placeholder || '') + '">' +
          '<button type="submit">' + esc(f.button || 'Go') + '</button></div>' +
          (f.chips && f.chips.length ? '<div class="tscm-chips">' + f.chips.map(function (ch) { return '<button type="button" data-v="' + esc(ch.value) + '">' + esc(ch.label) + '</button>'; }).join('') + '</div>' : '') +
          '</form>';
        return;
      }
      if (it.swatches) {
        html += '<div class="tscm-sw" data-i="' + i + '">' + it.swatches.map(function (c) {
          return '<button type="button" data-c="' + esc(c) + '" style="--c:' + esc(c) + '"' + (String(it.current || '').toLowerCase() === c.toLowerCase() ? ' class="on"' : '') + ' aria-label="Colour ' + esc(c) + '"></button>';
        }).join('') + '</div>';
        return;
      }
      var chk = typeof it.checked === 'function' ? it.checked(ctx) : it.checked;
      html += '<button type="button" role="menuitem" class="tscm-it' + (it.danger ? ' is-danger' : '') + (chk ? ' is-on' : '') + '" data-i="' + i + '"' + (it.disabled ? ' disabled' : '') + '>' +
        (chk != null ? '<span class="tscm-ck">' + (chk ? '✓' : '') + '</span>' : icon(it.icon)) +
        '<span class="tscm-lbl">' + esc(labelOf(it)) + '</span>' +
        (it.hint ? '<kbd>' + esc(it.hint) + '</kbd>' : '') + (it.sub ? '<span class="tscm-more">›</span>' : '') + '</button>';
    });
    body.innerHTML = html;
    Array.prototype.forEach.call(body.querySelectorAll('.tscm-form'), function (form) {
      var it = level.items[+form.getAttribute('data-i')], inp = form.querySelector('input');
      if (it.input.valueP) it.input.valueP.then(function (v) { if (!inp.value && v) inp.value = v; });
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var r = it.input.submit(inp.value, api);
        Promise.resolve(r).then(function (ok) { if (ok !== false) close(); });
      });
      Array.prototype.forEach.call(form.querySelectorAll('.tscm-chips button'), function (b) {
        b.addEventListener('click', function () { inp.value = b.getAttribute('data-v'); inp.focus(); });
      });
    });
    Array.prototype.forEach.call(body.querySelectorAll('.tscm-sw button'), function (b) {
      b.addEventListener('click', function () { var it = level.items[+b.parentNode.getAttribute('data-i')]; it.pick(b.getAttribute('data-c'), api); close(); });
    });
    place();
  }

  var api = {
    close: close, toast: toast, copy: copy,
    refresh: function () { render(); focusFirst(); }
  };

  function choose(btn) {
    var level = stack[stack.length - 1], it = level.items[+btn.getAttribute('data-i')];
    if (!it || it.disabled) return;
    if (it.sub) { push(it); return; }
    var r = it.run ? it.run(api) : null;
    if (it.keep) { Promise.resolve(r).then(function () { render(); }); return; }
    close();
  }
  function push(it) {
    var lvl = { title: labelOf(it), items: [], loading: true };
    stack.push(lvl); render();
    Promise.resolve(typeof it.sub === 'function' ? it.sub(ctx) : it.sub).then(function (items) {
      if (stack[stack.length - 1] !== lvl) return;
      lvl.items = items || []; lvl.loading = false; render(); focusFirst();
    });
  }
  function pop() { if (stack.length > 1) { stack.pop(); render(); focusFirst(); } else close(); }

  function focusables() { return menu ? Array.prototype.slice.call(menu.querySelectorAll('.tscm-body .tscm-it:not([disabled]), .tscm-body input, .tscm-sw button')) : []; }
  function focusFirst() {
    if (!menu) return;
    var inp = menu.querySelector('.tscm-body input');
    var f = inp || focusables()[0];
    if (f) { try { f.focus({ preventScroll: true }); } catch (e) { f.focus(); } if (inp) inp.select(); }
  }
  function onKey(e) {
    if (!menu) return;
    var list = focusables(), i = list.indexOf(document.activeElement), typing = document.activeElement && document.activeElement.tagName === 'INPUT';
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (stack.length > 1) pop(); else close(); return; }
    if (e.key === 'ArrowDown' || (e.key === 'Tab' && !e.shiftKey)) { e.preventDefault(); if (list.length) list[(i + 1) % list.length].focus(); return; }
    if (e.key === 'ArrowUp' || (e.key === 'Tab' && e.shiftKey)) { e.preventDefault(); if (list.length) list[(i - 1 + list.length) % list.length].focus(); return; }
    if (!typing && (e.key === 'ArrowLeft' || e.key === 'Backspace') && stack.length > 1) { e.preventDefault(); pop(); return; }
    if (!typing && e.key === 'ArrowRight' && document.activeElement && document.activeElement.classList.contains('tscm-it')) {
      var lvl = stack[stack.length - 1], it = lvl.items[+document.activeElement.getAttribute('data-i')];
      if (it && it.sub) { e.preventDefault(); push(it); }
    }
  }

  var at = { x: 0, y: 0, sheet: false };
  function place() {
    if (!menu) return;
    if (at.sheet) return;     // the phone sheet is pinned to the bottom by CSS
    var w = menu.offsetWidth, h = menu.offsetHeight, vw = window.innerWidth, vh = window.innerHeight;
    var x = at.x + 2, y = at.y + 2;
    if (x + w > vw - 8) x = Math.max(8, at.x - w - 2);
    if (y + h > vh - 8) y = Math.max(8, vh - h - 8);
    menu.style.left = x + 'px'; menu.style.top = y + 'px';
  }

  function open(spec, c, x, y, touch) {
    close();
    ctx = c || {};
    at = { x: x, y: y, sheet: !!touch && window.innerWidth < 640 };
    menu = document.createElement('div');
    menu.className = 'tscm' + (at.sheet ? ' is-sheet' : '');
    menu.setAttribute('role', 'menu');
    menu.innerHTML = '<div class="tscm-hd"></div><div class="tscm-body"></div>' +
      (at.sheet ? '' : '<div class="tscm-ft">Shift + right-click for the browser menu</div>');
    menu.addEventListener('click', function (e) {
      var b = e.target.closest('.tscm-it'); if (b && menu.contains(b)) { e.preventDefault(); choose(b); }
    });
    menu.addEventListener('contextmenu', function (e) { e.preventDefault(); });
    host().appendChild(menu);
    stack = [{ title: spec.title, sub: spec.sub, items: spec.items || [] }];
    render();
    requestAnimationFrame(function () { if (menu) menu.classList.add('on'); });
    if (!touch) focusables()[0] && focusables()[0].focus({ preventScroll: true });
    document.addEventListener('keydown', onKey, true);
    lastOpen = Date.now();
    // Labels that depend on the server (orbit, price) fill in when it answers.
    if (ctx.ticker && spec.prefetch !== false) info(ctx).then(function () {
      if (menu && stack.length === 1) { var a = document.activeElement, idx = focusables().indexOf(a); render(); var f = focusables(); if (idx >= 0 && f[idx]) f[idx].focus({ preventScroll: true }); }
    });
  }

  /* ── Providers ───────────────────────────────────────────────── */
  function register(p) {
    if (!p || typeof p.match !== 'function') return;
    providers.push(p);
    providers.sort(function (a, b) { return (b.priority || 0) - (a.priority || 0); });
  }
  function resolve(target, ev) {
    for (var i = 0; i < providers.length; i++) {
      var p = providers[i], c = null;
      try { c = p.match(target, ev); } catch (e) { c = null; }
      if (c) return { p: p, c: c };
    }
    return null;
  }
  function headerFor(c) {
    return function () { var bits = []; if (c.price) bits.push(money(c.price)); if (c.chg != null) bits.push(pct(c.chg)); return bits.join('  '); };
  }

  // Any ticker on any page (lowest priority: specific menus win).
  register({
    priority: 0,
    match: function (target) {
      var f = findTicker(target); if (!f) return null;
      var c = { ticker: f.ticker, el: f.el, link: f.link || null };
      var p = parseFloat(f.el.getAttribute('data-price')); if (isFinite(p) && p > 0) c.price = p;
      var box = f.el.closest('[data-watchlist-id]');
      if (box) { c.listBox = box; c.listId = +box.getAttribute('data-watchlist-id') || null; c.listName = box.getAttribute('data-watchlist-name') || ''; }
      return c;
    },
    build: function (c) {
      if (c.listId && !c.listName) info(c).then(function (d) { (d.lists || []).forEach(function (l) { if (l.id === c.listId) c.listName = l.name; }); });
      return { title: c.ticker, sub: headerFor(c), items: tickerItems(c) };
    }
  });

  // Command Center globe: a ticker chip.
  register({
    priority: 25,
    match: function (target) {
      var chip = target.closest && target.closest('.cmd27-chip'); if (!chip) return null;
      var t = attrTicker(chip); if (!t) return null;
      var c = { ticker: t, el: chip, orbit: chip.getAttribute('data-orbit') === '1' };
      var p = parseFloat(chip.getAttribute('data-price')); if (isFinite(p) && p > 0) c.price = p;
      var small = chip.querySelector('small'), m = small && /([+\-−]?\d+(?:\.\d+)?)%/.exec(small.textContent.replace('−', '-'));
      if (m) c.chg = parseFloat(m[1]);
      return c;
    },
    build: function (c) {
      var extra = [];
      if (c.el.getAttribute('data-spin') === '1') extra.push({ icon: 'spin', label: 'Spin the globe to ' + c.ticker, run: function () { document.dispatchEvent(new CustomEvent('cmd27:focus', { detail: { symbol: c.ticker } })); } });
      var items = extra.concat(tickerItems(c));
      return { title: c.ticker, sub: headerFor(c), items: items };
    }
  });

  // Command Center globe: empty space around it.
  register({
    priority: 20,
    match: function (target) {
      var g = target.closest && target.closest('.cmd27-hero__globe'); if (!g) return null;
      if (target.closest('button, a, input, form, .cmd27-chip, .cmd27-sector, .cmd27-mode, .cmd27-zoom, #cmd27-orbit-bar')) return null;
      return { globe: g };
    },
    build: function (c) {
      var g = c.globe, q = function (s) { return g.querySelector(s) || document.querySelector(s); };
      function click(s) { return function () { var b = q(s); if (b) b.click(); }; }
      var portfolio = g.classList.contains('is-portfolio'), full = g.classList.contains('is-full');
      var mine = !!document.querySelector('#cmd27-orbit .cmd27-chip.is-mine');
      var items = [
        { input: { label: 'Add a ticker to your orbit', placeholder: 'NVDA', button: 'Add', submit: function (v) {
          var t = String(v || '').trim().toUpperCase();
          if (!SYM.test(t)) { toast('Type a ticker like NVDA', { bad: true }); return false; }
          setOrbit(t, true);
        } } },
        { sep: true },
        { icon: 'globe', label: 'Show the market', checked: !portfolio, run: click('#cmd27-mode [data-mode="market"]') },
        { icon: 'wallet', label: 'Show my portfolio', checked: portfolio, run: click('#cmd27-mode [data-mode="portfolio"]') },
        { sep: true }
      ];
      if (mine && !portfolio) items.push(
        { icon: 'edit', label: 'Edit orbit', run: function () { document.dispatchEvent(new CustomEvent('cmd27:orbit-edit')); } },
        { icon: 'reset', label: 'Reset orbit to top movers', danger: true, run: function () {
          var before = Array.prototype.map.call(document.querySelectorAll('#cmd27-orbit .cmd27-chip.is-mine'), function (ch) { return ch.getAttribute('data-ticker'); });
          document.dispatchEvent(new CustomEvent('cmd27:orbit-reset'));
          toast('Orbit reset to the day’s top movers', { undo: function () { before.forEach(function (t) { document.dispatchEvent(new CustomEvent('cmd27:orbit-set', { detail: { symbol: t, on: true } })); }); } });
        } },
        { sep: true });
      items.push(
        { icon: 'zoomin', label: 'Zoom in', run: click('[data-zoom="in"]') },
        { icon: 'zoomout', label: 'Zoom out', run: click('[data-zoom="out"]') },
        { icon: 'globe', label: 'Whole world', run: click('[data-zoom="reset"]') },
        { icon: 'full', label: full ? 'Exit full screen' : 'Full screen', hint: 'F', run: click('#cmd27-fs') }
      );
      return { title: 'Command Center globe', items: items, prefetch: false };
    }
  });

  (window.TSMenuQueue || []).forEach(register);
  window.TSMenuQueue = { push: register };

  /* ── Triggers ────────────────────────────────────────────────── */
  function editable(el) {
    return !!(el && el.closest && el.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]'));
  }
  function hasSelection(target) {
    var s = window.getSelection && window.getSelection();
    if (!s || s.isCollapsed || !String(s).trim()) return false;
    try { return s.containsNode(target, true); } catch (e) { return true; }
  }
  function show(found, ev, touch) {
    var spec = found.p.build(found.c, ev);
    if (!spec) return false;
    open(spec, found.c, ev.clientX, ev.clientY, touch);
    return true;
  }

  document.addEventListener('contextmenu', function (ev) {
    if (menu && menu.contains(ev.target)) { ev.preventDefault(); return; }
    if (Date.now() - pressAt < 900) { ev.preventDefault(); return; }   // the long-press already opened one
    if (ev.defaultPrevented || ev.shiftKey || editable(ev.target) || hasSelection(ev.target)) return;
    var found = resolve(ev.target, ev); if (!found) return;
    if (show(found, ev, ev.pointerType === 'touch' || (ev.sourceCapabilities && ev.sourceCapabilities.firesTouchEvents))) ev.preventDefault();
  });

  // Press and hold (touch): same menu, after half a second without moving.
  var lp = null;
  function cancelLP() { if (lp) { clearTimeout(lp.t); document.documentElement.classList.remove('tscm-pressing'); lp = null; } }
  document.addEventListener('pointerdown', function (ev) {
    if (menu && !menu.contains(ev.target)) close();
    if (ev.pointerType !== 'touch' || editable(ev.target)) return;
    var found = resolve(ev.target, ev); if (!found) return;
    cancelLP();
    var x = ev.clientX, y = ev.clientY;
    document.documentElement.classList.add('tscm-pressing');
    lp = { x: x, y: y, t: setTimeout(function () {
      lp = null; document.documentElement.classList.remove('tscm-pressing');
      if (show(found, { clientX: x, clientY: y, target: ev.target }, true)) {
        pressAt = Date.now();
        if (navigator.vibrate) try { navigator.vibrate(8); } catch (e) {}
      }
    }, 520) };
  }, true);
  document.addEventListener('pointermove', function (ev) { if (lp && Math.hypot(ev.clientX - lp.x, ev.clientY - lp.y) > 10) cancelLP(); }, true);
  document.addEventListener('pointerup', cancelLP, true);
  document.addEventListener('pointercancel', cancelLP, true);
  document.addEventListener('scroll', function (ev) { cancelLP(); if (menu && !at.sheet && !(ev.target && ev.target.nodeType === 1 && menu.contains(ev.target))) close(); }, true);
  // The tap that ends a long press must not also follow the link under it.
  document.addEventListener('click', function (ev) {
    if (Date.now() - pressAt < 700 && !(menu && menu.contains(ev.target))) { ev.preventDefault(); ev.stopPropagation(); }
  }, true);
  window.addEventListener('resize', function () { if (menu && !at.sheet) close(); });
  window.addEventListener('blur', function () { if (menu && !(document.activeElement && menu.contains(document.activeElement))) close(); });

  window.TSMenu = {
    register: register, open: open, close: close, toast: toast, copy: copy,
    tickerItems: tickerItems, setAlert: setAlert, setOrbit: setOrbit, icon: icon, money: money, findTicker: findTicker,
    _resolve: resolve
  };
})();
