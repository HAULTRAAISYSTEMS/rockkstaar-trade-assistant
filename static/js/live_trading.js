/* Live trading on the Terminal's order ticket (Schwab).
   Paper is the default. Live mode needs live trading turned on (⚙, type LIVE).
   Preview → server checks + signed token → Confirm sends the exact previewed
   order. Changing anything on the ticket after the preview invalidates it. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  if (!$('lt-mode')) return;

  var state = { status: null, mode: 'paper', token: null, ticket: null, timer: null, ordersTimer: null };
  try { if (localStorage.getItem('ltMode') === 'live') state.mode = 'live'; } catch (e) {}

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function money(v) { return v == null ? '—' : '$' + Number(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}); }
  function val(id) { var el = $(id); return el && !el.disabled ? (el.value || '').trim() : ''; }

  async function api(path, body) {
    var opt = {credentials: 'same-origin', cache: 'no-store'};
    if (body !== undefined) { opt.method = 'POST'; opt.headers = {'Content-Type': 'application/json'}; opt.body = JSON.stringify(body); }
    var r = await fetch(path, opt), d = {};
    try { d = await r.json(); } catch (e) { d = {ok: false, error: 'Server error (' + r.status + ')'}; }
    if (!r.ok && d.ok !== false) d.ok = false;
    d._status = r.status;
    return d;
  }

  function ticket() {
    var bracket = $('tw-ot-bracket') && $('tw-ot-bracket').checked;
    return {
      symbol: ($('tw-ot-symbol').value || '').trim().toUpperCase(),
      side: window._twSide || 'BUY',
      qty: $('tw-ot-qty').value,
      order_type: $('tw-ot-type').value,
      limit: val('tw-ot-limit'),
      stop: val('tw-ot-stop'),
      tif: $('tw-ot-tif').value,
      extended: !!($('tw-ot-ext') && $('tw-ot-ext').checked),
      bracket: !!bracket,
      target: bracket ? val('tw-ot-target') : '',
      stop_loss: bracket ? val('tw-ot-sl') : ''
    };
  }

  // ── Mode ────────────────────────────────────────────────────────────────
  function isLive() { return state.mode === 'live' && state.status && state.status.enabled; }

  function paint() {
    var enabled = !!(state.status && state.status.enabled);
    if (!enabled && state.mode === 'live') state.mode = 'paper';
    var live = isLive();
    document.querySelectorAll('#lt-mode [data-mode]').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-mode') === state.mode);
      if (b.getAttribute('data-mode') === 'live') {
        b.disabled = !enabled;
        b.title = enabled ? 'Send real orders to Schwab' : 'Turn on live trading in ⚙ first';
      }
    });
    var tag = $('tw-ot-mode-tag');
    tag.textContent = live ? 'LIVE' : 'PAPER';
    tag.classList.toggle('is-live', live);
    $('tw-ot-note').textContent = live
      ? 'LIVE: orders go to your Schwab account ' + (state.status.account_last4 ? '…' + state.status.account_last4 + ' ' : '') + 'after you preview and confirm.'
      : 'Paper mode: preview here, then use the paper portfolio for simulated fills. No live order is transmitted.';
    $('tw-ot-preview-btn').textContent = live ? 'Preview Live Order' : 'Preview Order';
    $('lt-orders').hidden = !(state.status && state.status.connected);
    try { localStorage.setItem('ltMode', state.mode); } catch (e) {}
    clearConfirm();
  }

  function paintSettings() {
    var s = state.status || {};
    var st = $('lt-set-status');
    if (s.kill_switch) st.innerHTML = 'Live trading is switched off on the server.';
    else if (s.server_error) st.innerHTML = 'Couldn’t reach the server (' + esc(s.connect_error || 'error') + '). <a href="#" id="lt-retry">Try again</a>';
    else if (!s.connected) st.innerHTML = 'Schwab: <b class="off">not connected</b> ' + esc(s.connect_error || '') + ' — <a href="/brokers">Connect</a>';
    else st.innerHTML = 'Live trading: <b class="' + (s.enabled ? 'on">ON' : 'off">OFF') + '</b>';
    var sel = $('lt-account');
    sel.innerHTML = (s.accounts || []).map(function (a) {
      return '<option value="' + esc(a.hash) + '"' + (a.hash === s.account ? ' selected' : '') + '>Account …' + esc(a.last4) + '</option>';
    }).join('');
    $('lt-max').value = s.max_order_value || '';
    $('lt-enable').hidden = !!s.enabled || !s.connected || !!s.kill_switch;
    $('lt-disable').hidden = !s.enabled;
    document.querySelector('.lt-enable-row').hidden = !!s.enabled || !s.connected;
  }

  async function loadStatus(attempt) {
    attempt = attempt || 0;
    var d;
    try { d = await api('/api/trade/status'); } catch (e) { d = {ok: false, _status: 0, error: 'No connection'}; }
    // A 5xx (deploy restart, worker busy) or a dropped connection is not an
    // answer about Schwab: try again a few times before showing it.
    if ((d._status >= 500 || d._status === 0) && attempt < 4) {
      $('lt-set-status').innerHTML = 'Checking Schwab… (server busy, retrying)';
      setTimeout(function () { loadStatus(attempt + 1); }, [2000, 4000, 8000, 15000][attempt]);
      return;
    }
    if (d.ok === false && !d.accounts) d = {enabled: false, connected: false, connect_error: d.error, server_error: d._status >= 500 || d._status === 0};
    (d.accounts || []).forEach(function (a) { if (a.hash === d.account) d.account_last4 = a.last4; });
    state.status = d;
    paint(); paintSettings();
    if (d.connected) loadOrders();
  }

  async function saveSettings(extra) {
    var body = Object.assign({account: $('lt-account').value || undefined, max_order_value: $('lt-max').value || undefined}, extra || {});
    var d = await api('/api/trade/settings', body);
    if (d.ok === false) { $('lt-set-status').innerHTML = '<span style="color:#ff8a83">' + esc(d.error) + '</span>'; return; }
    $('lt-confirm-word').value = '';
    await loadStatus();
  }

  // ── Preview / confirm ───────────────────────────────────────────────────
  function clearConfirm() {
    state.token = null; state.ticket = null;
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    var box = $('lt-confirm'); box.hidden = true; box.innerHTML = '';
  }

  async function preview() {
    clearConfirm();
    var box = $('lt-confirm'), t = ticket();
    $('tw-preview').style.display = 'none';
    box.hidden = false; box.innerHTML = '<h4>Checking with Schwab…</h4>';
    var d = await api('/api/trade/preview', t);
    if (d.ok === false) { box.innerHTML = '<h4>Can\'t send this order</h4><ul><li class="block">' + esc(d.error) + '</li></ul>'; return; }
    var est = d.est || {};
    var items = (d.blocks || []).map(function (b) { return '<li class="block">' + esc(b) + '</li>'; })
      .concat((d.warnings || []).map(function (w) { return '<li class="warn">' + esc(w) + '</li>'; })).join('');
    box.innerHTML =
      '<h4>' + (d.token ? 'Confirm live order' : 'Blocked') + ' · Schwab …' + esc(d.account_last4) + '</h4>' +
      '<div class="lt-sum">' + esc(d.summary) + '</div>' +
      '<div>Est. value <b>' + money(est.value) + '</b>' + (est.last ? ' · last ' + Number(est.last).toFixed(2) : '') +
      (d.commission != null ? ' · commission ' + money(d.commission) : '') + '</div>' +
      (items ? '<ul>' + items + '</ul>' : '') +
      '<div class="lt-btns"><button type="button" class="tw-submit lt-send" id="lt-send"' + (d.token ? '' : ' disabled') + '>' +
      (d.token ? 'Send to Schwab' : 'Fix the above to send') + '</button>' +
      '<button type="button" class="tw-submit-ghost" id="lt-cancel-preview">Cancel</button></div>' +
      (d.token ? '<div class="lt-exp" id="lt-exp"></div>' : '');
    $('lt-cancel-preview').onclick = clearConfirm;
    if (!d.token) return;
    state.token = d.token; state.ticket = t;
    var left = d.ttl || 90;
    state.timer = setInterval(function () {
      left -= 1;
      var e = $('lt-exp'); if (e) e.textContent = 'Preview valid for ' + left + 's';
      if (left <= 0) { clearConfirm(); }
    }, 1000);
    $('lt-send').onclick = send;
  }

  async function send() {
    var btn = $('lt-send');
    if (!state.token || !btn) return;
    if (JSON.stringify(ticket()) !== JSON.stringify(state.ticket)) {
      $('lt-confirm').innerHTML = '<h4>Ticket changed</h4><ul><li class="block">Preview the order again.</li></ul>';
      state.token = null; return;
    }
    btn.disabled = true; btn.textContent = 'Sending…';
    var d = await api('/api/trade/place', {ticket: state.ticket, token: state.token});
    if (state.timer) { clearInterval(state.timer); state.timer = null; }
    state.token = null;
    var box = $('lt-confirm');
    if (d.ok) {
      box.innerHTML = '<h4>Sent</h4><div class="lt-sum ok">✓ ' + esc(d.summary) + '</div>' +
        '<div>Schwab order ' + esc(d.order_id || '(id pending)') + '. Status shows below.</div>';
      loadOrders();
    } else {
      box.innerHTML = '<h4>Not sent</h4><ul>' + (d.blocks || [d.error]).map(function (b) { return '<li class="block">' + esc(b) + '</li>'; }).join('') + '</ul>';
    }
  }

  // ── Orders ──────────────────────────────────────────────────────────────
  async function loadOrders() {
    var d = await api('/api/trade/orders');
    var list = $('lt-orders-list');
    if (d.ok === false && !(d.orders || []).length) { list.innerHTML = '<div class="lt-ord"><span>' + esc(d.error || 'Couldn\'t load orders') + '</span></div>'; return; }
    var rows = (d.orders || []).slice(0, 15);
    list.innerHTML = rows.length ? rows.map(function (o) {
      var px = o.price ? ' @ ' + o.price : (o.stop_price ? ' stop ' + o.stop_price : '');
      return '<div class="lt-ord"><span>' + esc(o.side + ' ' + o.quantity + ' ' + o.symbol + ' ' + o.order_type + px) +
        ' · <span class="st ' + (o.open ? 'open' : esc(o.status)) + '">' + esc(o.status) + (o.filled ? ' ' + o.filled + ' filled' : '') + '</span></span>' +
        (o.open ? '<button type="button" data-lt-cancel="' + esc(o.order_id) + '">Cancel</button>' : '<span></span>') + '</div>';
    }).join('') : '<div class="lt-ord"><span>No orders today</span></div>';
    var anyOpen = rows.some(function (o) { return o.open; });
    if (state.ordersTimer) clearTimeout(state.ordersTimer);
    if (anyOpen) state.ordersTimer = setTimeout(function () { if (!document.hidden) loadOrders(); }, 15000);
  }

  document.addEventListener('click', async function (e) {
    var c = e.target.closest('[data-lt-cancel]');
    if (!c) return;
    if (!window.confirm('Cancel this Schwab order?')) return;
    c.disabled = true; c.textContent = '…';
    var d = await api('/api/trade/cancel/' + encodeURIComponent(c.getAttribute('data-lt-cancel')), {});
    if (!d.ok) { c.disabled = false; c.textContent = 'Cancel'; window.alert(d.error || 'Cancel failed'); }
    setTimeout(loadOrders, 800);
  });

  // ── Wiring ──────────────────────────────────────────────────────────────
  document.querySelectorAll('#lt-mode [data-mode]').forEach(function (b) {
    b.addEventListener('click', function () { if (b.disabled) return; state.mode = b.getAttribute('data-mode'); paint(); });
  });
  document.addEventListener('click', function (e) {
    if (e.target && e.target.id === 'lt-retry') { e.preventDefault(); $('lt-set-status').textContent = 'Checking Schwab…'; loadStatus(); }
  });
  $('lt-gear').addEventListener('click', function () { var s = $('lt-settings'); s.hidden = !s.hidden; if (!s.hidden) loadStatus(); });
  $('lt-enable').addEventListener('click', function () { saveSettings({enabled: true, confirm: $('lt-confirm-word').value}); });
  $('lt-disable').addEventListener('click', function () { state.mode = 'paper'; saveSettings({enabled: false}); });
  $('lt-save').addEventListener('click', function () { saveSettings(); });
  $('lt-orders-refresh').addEventListener('click', loadOrders);
  // Any edit to the ticket voids a pending confirmation.
  ['tw-ot-symbol', 'tw-ot-qty', 'tw-ot-type', 'tw-ot-limit', 'tw-ot-stop', 'tw-ot-tif', 'tw-ot-ext', 'tw-ot-bracket', 'tw-ot-target', 'tw-ot-sl'].forEach(function (id) {
    var el = $(id); if (!el) return;
    el.addEventListener('input', function () { if (state.token) clearConfirm(); });
    el.addEventListener('change', function () { if (state.token) clearConfirm(); });
  });
  document.querySelectorAll('.tw-side-btn').forEach(function (b) { b.addEventListener('click', function () { if (state.token) clearConfirm(); }); });

  window.LiveTrading = {isLive: isLive, preview: preview, reload: loadStatus};
  loadStatus();
})();
