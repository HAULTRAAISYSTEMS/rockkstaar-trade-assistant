/* Control Center (the avatar menu): greeting by time of day, the market
   session chip, and the Alert sounds / Auto refresh switches. Those two
   switches drive main.js's existing toggles and are remembered on this
   device. */
(function () {
  'use strict';
  var KEY = { sound: 'tradestaar.cc.sound', auto: 'tradestaar.cc.auto' };
  function state(kind) { return kind === 'sound' ? window._arSoundEnabled !== false : window._arEnabled !== false; }
  function flip(kind) {
    if (kind === 'sound' && typeof window.toggleAlertSound === 'function') window.toggleAlertSound();
    if (kind === 'auto' && typeof window.toggleAutoRefresh === 'function') window.toggleAutoRefresh();
  }
  function paint() {
    document.querySelectorAll('[data-ccx-toggle]').forEach(function (b) {
      b.setAttribute('aria-pressed', state(b.getAttribute('data-ccx-toggle')) ? 'true' : 'false');
    });
  }
  function init() {
    // Restore what this device chose last time.
    ['sound', 'auto'].forEach(function (k) {
      var want = null; try { want = localStorage.getItem(KEY[k]); } catch (e) {}
      if (want === '0' && state(k)) flip(k);
    });
    document.querySelectorAll('[data-ccx-toggle]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        e.preventDefault();
        var k = b.getAttribute('data-ccx-toggle'); flip(k);
        try { localStorage.setItem(KEY[k], state(k) ? '1' : '0'); } catch (err) {}
        paint();
      });
    });
    paint();
    // Good morning / afternoon / evening, by the device's clock.
    var h = new Date().getHours();
    var greet = h < 5 ? 'Late night session' : h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
    document.querySelectorAll('[data-ccx-greet]').forEach(function (el) { el.textContent = greet; });
    // Session chip mirrors the navbar's session label.
    var src = document.getElementById('nav-live-label');
    function session() {
      var t = (src && src.textContent || '').trim();
      if (!t || /checking/i.test(t)) return;
      document.querySelectorAll('[data-ccx-session]').forEach(function (el) {
        el.textContent = t;
        el.classList.toggle('is-open', /open|regular|live|pre|after|overnight|24/i.test(t) && !/closed/i.test(t));
      });
    }
    session();
    if (src && window.MutationObserver) new MutationObserver(session).observe(src, { childList: true, characterData: true, subtree: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
