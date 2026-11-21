/* Floating glass cards (see static/css/glass_2028.css).
   - Mouse: a soft light follows the cursor across the card under it, and the
     card tilts toward the cursor (≤3°). Big panes (charts, the globe) only
     get the light, never the tilt.
   - Everywhere: each card glints once as it first scrolls into view.
   Honors reduced motion. */
(function () {
  'use strict';
  var SEL = '.cc-card, .card, .intel-card, .intel-kpi, .liq-card, .wa-card, .lab-panel, .stat, .cmd27-m, .tt-card, ' +
            '.learn-card, .learn-fold, .tw-stat, .tw-panel, .tw-context-card, .dp, .nd-heat, .sm-top';
  var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var finePointer = window.matchMedia && matchMedia('(hover: hover) and (pointer: fine)').matches;

  if (finePointer) {
    var cur = null, raf = 0, lastE = null;
    function canTilt(el) {
      if (reduce) return false;
      var r = el.getBoundingClientRect();
      if (r.width * r.height > 520000) return false;            // big panes: light only
      return !el.querySelector('canvas, .tw-chart, [data-cc3d-orb], iframe');
    }
    function paint() {
      raf = 0; if (!cur || !lastE) return;
      var r = cur.getBoundingClientRect(), x = lastE.clientX - r.left, y = lastE.clientY - r.top;
      cur.style.setProperty('--gx', x.toFixed(0) + 'px');
      cur.style.setProperty('--gy', y.toFixed(0) + 'px');
      if (cur._tilt) {
        var px = x / r.width - 0.5, py = y / r.height - 0.5;
        cur.style.setProperty('--ry', (px * 5).toFixed(2) + 'deg');
        cur.style.setProperty('--rx', (-py * 5).toFixed(2) + 'deg');
      }
    }
    function leave(el) {
      if (!el) return;
      el.classList.remove('glass-tilt');
      el.style.removeProperty('--gx'); el.style.removeProperty('--gy');
      el.style.removeProperty('--rx'); el.style.removeProperty('--ry');
    }
    document.addEventListener('pointermove', function (e) {
      if (e.pointerType !== 'mouse') return;
      var t = e.target && e.target.closest ? e.target.closest(SEL) : null;
      // Prefer the innermost small card; fall back to the pane it sits in.
      if (t !== cur) {
        leave(cur); cur = t;
        if (cur) { cur._tilt = canTilt(cur); if (cur._tilt) cur.classList.add('glass-tilt'); }
      }
      lastE = e;
      if (cur && !raf) raf = requestAnimationFrame(paint);
    }, { passive: true });
    document.addEventListener('pointerleave', function () { leave(cur); cur = null; });
  }

  // One glint per card as it scrolls into view.
  if (!reduce && 'IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        var el = en.target; io.unobserve(el);
        el.classList.add('glass-glint');
        setTimeout(function () { el.classList.remove('glass-glint'); }, 1500);
      });
    }, { threshold: 0.35 });
    function watch() {
      document.querySelectorAll('.cc-card, .card, .intel-card, .liq-card, .wa-card, .lab-panel, .tw-panel, .learn-fold, .stat, .cmd27-m, .intel-kpi')
        .forEach(function (el) { if (!el._glassSeen) { el._glassSeen = 1; io.observe(el); } });
    }
    watch();
    document.addEventListener('ptab:change', function () { setTimeout(watch, 50); });
  }
})();
