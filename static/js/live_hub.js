/* One live poll per page.
 *
 * The banner tape, the Today pulse, the Terminal rows and the chart's live
 * price each ran their own 3-second poll, so one open Terminal sent three or
 * four requests every three seconds and the server's workers recycled every
 * few minutes. Now each of those registers here for its part, and this asks
 * the server once:
 *
 *     /api/live/bundle?want=tape,quotes,live&ticker=SPY
 *
 * then hands every part to its subscribers. Parts nobody on the page wants
 * are never requested. Pauses while the tab is hidden, backs off to 30 s
 * after repeated failures, and keeps going 24/5 like the markets do.
 *
 *   TSLive.on('tape', function (data) { ... })     // data = that endpoint's JSON
 *   TSLive.setLiveTicker(function () { return 'SPY'; })
 *   TSLive.lastOk('quotes')                       // ms timestamp of last good part
 *   TSLive.now()                                  // poll immediately
 */
(function () {
  "use strict";
  if (window.TSLive) { return; }

  var POLL_MS = 3000;
  var subs = {};            // part -> [fn]
  var lastOk = {};          // part -> Date.now() of last delivered part
  var tickerFn = null;
  var timer = null, inFlight = false, fails = 0, started = false;

  function wanted() { return Object.keys(subs).filter(function (k) { return subs[k].length; }); }

  function schedule(delay) {
    clearTimeout(timer);
    if (document.hidden || !wanted().length) { return; }
    timer = setTimeout(poll, delay != null ? delay : (fails >= 5 ? 30000 : POLL_MS));
  }

  function deliver(part, data) {
    if (data == null) { return; }
    lastOk[part] = Date.now();
    (subs[part] || []).forEach(function (fn) {
      try { fn(data); } catch (e) { if (window.console) { console.warn("live hub " + part + ":", e); } }
    });
  }

  function poll() {
    if (inFlight) { schedule(); return; }
    var want = wanted();
    if (!want.length) { return; }
    var url = "/api/live/bundle?want=" + encodeURIComponent(want.join(","));
    if (want.indexOf("live") >= 0) {
      var t = "";
      try { t = tickerFn ? (tickerFn() || "") : ""; } catch (e) { t = ""; }
      if (t) { url += "&ticker=" + encodeURIComponent(t); }
    }
    inFlight = true;
    var ctrl = ("AbortController" in window) ? new AbortController() : null;
    var kill = ctrl ? setTimeout(function () { ctrl.abort(); }, 8000) : null;
    fetch(url, { cache: "no-store", credentials: "same-origin",
                 headers: { "Accept": "application/json" }, signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) {
        var type = (r.headers.get("Content-Type") || "").toLowerCase();
        if (!r.ok || type.indexOf("application/json") < 0) { throw new Error("HTTP " + r.status); }
        return r.json();
      })
      .then(function (d) {
        var parts = (d && d.parts) || {};
        Object.keys(parts).forEach(function (k) { deliver(k, parts[k]); });
        fails = 0;
      })
      .catch(function () { fails += 1; })
      .then(function () {
        if (kill) { clearTimeout(kill); }
        inFlight = false;
        schedule();
      });
  }

  function start() {
    if (started) { return; }
    started = true;
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { clearTimeout(timer); } else { schedule(0); }
    });
    schedule(0);
  }

  window.TSLive = {
    on: function (part, fn) {
      (subs[part] = subs[part] || []).push(fn);
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
      } else {
        start();
        schedule(0);
      }
    },
    setLiveTicker: function (fn) { tickerFn = fn; },
    lastOk: function (part) { return lastOk[part] || 0; },
    failures: function () { return fails; },
    now: function () { schedule(0); }
  };
})();
