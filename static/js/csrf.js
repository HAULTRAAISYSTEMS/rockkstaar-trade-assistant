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
  // Pages without the base layout (sign in, sign up) carry the token only in
  // their form, so the refresh below still has something to work with.
  if (!token) {
    var field = document.querySelector('input[name="csrf_token"]');
    token = field ? field.value : "";
  }
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

  /* ── Keep the token current ─────────────────────────────────────────
     A page left open on a phone outlives the session it was rendered for:
     iOS drops the session cookie when Safari or the home-screen app is
     suspended, then restores the page from memory with its old token, and
     the next form post failed the security check. Before a form that has
     sat open is posted, and whenever a page comes back from the back/forward
     cache, fetch the session's current token and write it everywhere the
     page carries one. */
  var syncedAt = Date.now();
  var STALE_MS = 2 * 60 * 1000;

  function applyToken(next) {
    if (!next) { return; }
    token = next;
    if (meta) { meta.setAttribute("content", next); }
    var fields = document.querySelectorAll('input[name="csrf_token"]');
    for (var i = 0; i < fields.length; i++) { fields[i].value = next; }
  }

  function syncToken() {
    if (typeof nativeFetch !== "function") { return Promise.resolve(); }
    return nativeFetch.call(window, "/api/csrf-token", {
      credentials: "same-origin", cache: "no-store",
      headers: { "Accept": "application/json" }
    }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.token) { applyToken(d.token); syncedAt = Date.now(); } })
      .catch(function () { /* post with what we have; the server explains */ });
  }

  window.addEventListener("pageshow", function (e) {
    if (e.persisted) { syncToken(); }
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && Date.now() - syncedAt > STALE_MS) { syncToken(); }
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (e.defaultPrevented || !form || form.tagName !== "FORM") { return; }
    if (String(form.getAttribute("method") || "GET").toUpperCase() !== "POST") { return; }
    if (!sameOrigin(form.getAttribute("action") || window.location.href)) { return; }
    if (!form.querySelector('input[name="csrf_token"]')) { return; }
    if (form.__csrfFresh) { form.__csrfFresh = false; return; }
    if (Date.now() - syncedAt < STALE_MS) { return; }
    e.preventDefault();
    var submitter = e.submitter || null;
    syncToken().then(function () {
      form.__csrfFresh = true;
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
      } else {
        if (submitter && submitter.name) {
          var carry = document.createElement("input");
          carry.type = "hidden"; carry.name = submitter.name; carry.value = submitter.value;
          form.appendChild(carry);
        }
        form.__csrfFresh = false;
        HTMLFormElement.prototype.submit.call(form);
      }
    });
  });

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
