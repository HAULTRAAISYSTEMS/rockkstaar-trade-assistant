/* Attach the CSRF token to every state-changing request the app makes.
 *
 * Seventeen endpoints were decorated @csrf.exempt, eleven of them
 * state-changing: adding tickers to a watchlist, clearing scanner alerts,
 * disconnecting Schwab, importing broker trades into the journal, writing and
 * deleting study-log entries, and two endpoints that spend money on model
 * calls. Any page on the internet could fire those at a logged-in user's
 * browser and the session cookie would ride along.
 *
 * They were exempt because the calls are scattered through page templates as
 * bare fetch() with no token. Rather than edit dozens of call sites - and miss
 * one - the token is attached here, once, for every same-origin unsafe
 * request. Loaded blocking in <head> so it is installed before any page script
 * runs.
 */
(function () {
  "use strict";

  var meta = document.querySelector('meta[name="csrf-token"]');
  var token = meta ? meta.getAttribute("content") : "";
  if (!token) { return; }

  // GET, HEAD, OPTIONS and TRACE are safe by definition and Flask-WTF does
  // not check them.
  var SAFE = { GET: 1, HEAD: 1, OPTIONS: 1, TRACE: 1 };

  function unsafe(method) {
    return !SAFE[String(method || "GET").toUpperCase()];
  }

  // Never send the token off-site. A relative URL is always same-origin; an
  // absolute one has to match the page's origin exactly.
  function sameOrigin(url) {
    try {
      return new URL(String(url), window.location.href).origin ===
             window.location.origin;
    } catch (e) {
      return false;
    }
  }

  var nativeFetch = window.fetch;
  if (typeof nativeFetch === "function") {
    window.fetch = function (input, init) {
      init = init || {};
      var isRequest = (typeof Request !== "undefined") && (input instanceof Request);
      var url = isRequest ? input.url : input;
      var method = init.method || (isRequest ? input.method : "GET");

      if (!unsafe(method) || !sameOrigin(url)) {
        return nativeFetch.call(this, input, init);
      }

      // A Request carries its own headers, so it has to be rebuilt rather
      // than have init.headers bolted on beside it.
      if (isRequest && init.method === undefined && init.headers === undefined) {
        var rebuilt = new Request(input, {});
        rebuilt.headers.set("X-CSRFToken", token);
        return nativeFetch.call(this, rebuilt, init);
      }

      var headers = new Headers(init.headers ||
                                (isRequest ? input.headers : undefined));
      if (!headers.has("X-CSRFToken")) { headers.set("X-CSRFToken", token); }
      var next = {};
      for (var key in init) {
        if (Object.prototype.hasOwnProperty.call(init, key)) { next[key] = init[key]; }
      }
      next.headers = headers;
      return nativeFetch.call(this, input, next);
    };
  }

  // Older call sites still use XMLHttpRequest.
  var open = XMLHttpRequest.prototype.open;
  var send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__csrfNeeded = unsafe(method) && sameOrigin(url);
    return open.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    if (this.__csrfNeeded) {
      try { this.setRequestHeader("X-CSRFToken", token); } catch (e) { /* already sent */ }
    }
    return send.apply(this, arguments);
  };
})();
