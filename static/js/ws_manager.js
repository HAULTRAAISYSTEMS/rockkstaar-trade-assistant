/**
 * ws_manager.js — WebSocket real-time update layer for Rockkstaar
 *
 * Connects to the server's WS endpoint for instant push updates.
 * Falls back to the existing 15s polling system if:
 *   - WebSocket is not supported by the browser
 *   - The connection drops and can't be re-established after MAX_RECONNECTS
 *
 * Status states (shown in the navbar pill):
 *   LIVE        — WebSocket connected, server is pushing data in real time
 *   RECONNECTING — Connection dropped, retrying with backoff; polling as bridge
 *   POLLING     — WebSocket unavailable; using 15s REST polling permanently
 *
 * Public API:
 *   window._wsConnect(path, onMessage)  — call once per page on load
 *   window._wsDisconnect()              — called automatically on page unload
 *   window._wsGetStatus()               — returns current status string
 */

(function () {
  'use strict';

  // ── Configuration ──────────────────────────────────────────────────────────
  var MAX_RECONNECT_ATTEMPTS = 5;
  var RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 15000]; // ms, exponential-ish

  // ── State ──────────────────────────────────────────────────────────────────
  var _ws             = null;
  var _wsPath         = null;
  var _onMessage      = null;
  var _reconnectCount = 0;
  var _reconnectTimer = null;
  var _gaveUp         = false;    // true after MAX_RECONNECT_ATTEMPTS
  var _status         = 'polling';

  // ── Status Pill ────────────────────────────────────────────────────────────
  function _setStatus(s) {
    if (_status === s) return;
    _status = s;
    var pill = document.getElementById('ws-status-pill');
    if (!pill) return;
    pill.className = 'ws-status-pill ws-' + s;
    var labels = { live: 'LIVE', reconnecting: 'RECONNECTING', polling: 'POLLING' };
    pill.textContent = labels[s] || s.toUpperCase();
  }

  // ── WebSocket Lifecycle ────────────────────────────────────────────────────
  function _openSocket() {
    if (_ws) {
      _ws.onopen = _ws.onclose = _ws.onerror = _ws.onmessage = null;
      try { _ws.close(); } catch (e) {}
      _ws = null;
    }

    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url   = proto + '//' + location.host + _wsPath;

    try {
      _ws = new WebSocket(url);
    } catch (e) {
      console.warn('[WS] Cannot create WebSocket:', e);
      _fallbackForever();
      return;
    }

    _ws.onopen = function () {
      console.log('[WS] Connected to', _wsPath);
      _reconnectCount = 0;
      _gaveUp         = false;
      _setStatus('live');
      // WS is now driving updates — pause the polling timer
      window._arEnabled = false;
      clearTimeout(window._arTimer);
    };

    _ws.onmessage = function (evt) {
      try {
        var data = JSON.parse(evt.data);
        if (data && _onMessage) _onMessage(data);
        // Keep the "last updated" timestamp in sync with WS pushes
        if (data && data.server_time) {
          window._arSetLastUpdated(data.server_time);
        }
      } catch (e) {
        console.warn('[WS] Unparseable message:', e);
      }
    };

    _ws.onclose = function (evt) {
      if (_gaveUp) return;
      if (evt.wasClean && evt.code === 1000) {
        // Intentional close (page navigation etc.) — stay in polling mode
        _setStatus('polling');
        _enablePolling();
        return;
      }
      console.warn('[WS] Unexpected close — code:', evt.code, 'reason:', evt.reason);
      _scheduleReconnect();
    };

    _ws.onerror = function () {
      // onclose always fires after onerror — reconnect logic lives there
    };
  }

  function _scheduleReconnect() {
    if (_reconnectCount >= MAX_RECONNECT_ATTEMPTS) {
      console.warn('[WS] Giving up after', MAX_RECONNECT_ATTEMPTS, 'attempts — switching to polling');
      _fallbackForever();
      return;
    }

    _setStatus('reconnecting');
    _enablePolling(); // polling as bridge while reconnecting

    var delay = RECONNECT_DELAYS[Math.min(_reconnectCount, RECONNECT_DELAYS.length - 1)];
    _reconnectCount++;
    console.log('[WS] Reconnecting in', delay, 'ms (attempt', _reconnectCount + '/' + MAX_RECONNECT_ATTEMPTS + ')');

    clearTimeout(_reconnectTimer);
    _reconnectTimer = setTimeout(_openSocket, delay);
  }

  function _fallbackForever() {
    _gaveUp = true;
    _ws     = null;
    _setStatus('polling');
    _enablePolling();
  }

  function _enablePolling() {
    if (!window._arEnabled) {
      window._arEnabled = true;
      if (typeof scheduleRefresh === 'function') scheduleRefresh();
    }
  }

  // ── Clean disconnect on page leave ────────────────────────────────────────
  function _disconnect() {
    _gaveUp = true;
    clearTimeout(_reconnectTimer);
    if (_ws) {
      try { _ws.close(1000, 'page unload'); } catch (e) {}
      _ws = null;
    }
  }

  window.addEventListener('beforeunload', _disconnect);

  // ── Public API ─────────────────────────────────────────────────────────────
  /**
   * Connect this page to a WebSocket endpoint.
   * @param {string}   path       - e.g. '/ws/dashboard'
   * @param {function} onMessage  - called with parsed JSON payload on each push
   */
  window._wsConnect = function (path, onMessage) {
    _wsPath    = path;
    _onMessage = onMessage;

    // Guard: only connect if browser supports WebSocket
    if (!window.WebSocket) {
      console.warn('[WS] Browser does not support WebSocket — using polling');
      _setStatus('polling');
      return;
    }

    _openSocket();
  };

  window._wsDisconnect = _disconnect;
  window._wsGetStatus  = function () { return _status; };

}());
