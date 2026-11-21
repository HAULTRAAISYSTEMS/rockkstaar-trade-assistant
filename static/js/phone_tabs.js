/* Phone page tabs.
   Long pages tag their sections with data-ptab="Label". On a phone (≤760px)
   they get a sticky tab bar and show one tab at a time, like a native app;
   on iPad and desktop nothing changes. The open tab is remembered per page
   for the session, and a #tab-<label> hash opens that tab directly.
   Optional: an element with data-ptabs-bar marks where the bar goes
   (otherwise it goes right before the first tagged section); its value, if
   any, is the tab order ("Chart,List,Trade"). */
(function () {
  'use strict';
  var tagged = Array.prototype.slice.call(document.querySelectorAll('[data-ptab]'));
  if (!tagged.length) return;
  var mq = window.matchMedia ? window.matchMedia('(max-width: 760px)') : { matches: false };
  var KEY = 'ptab:' + location.pathname;
  var labels = [];
  tagged.forEach(function (el) { var l = el.getAttribute('data-ptab'); if (labels.indexOf(l) < 0) labels.push(l); });
  if (labels.length < 2) return;
  // A page can set the order: <div data-ptabs-bar="Chart,List,Trade"></div>
  var slotEl = document.querySelector('[data-ptabs-bar]');
  var order = slotEl && slotEl.getAttribute('data-ptabs-bar');
  if (order) {
    var want = order.split(',').map(function (x) { return x.trim(); });
    labels.sort(function (a, b) {
      var ia = want.indexOf(a), ib = want.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
  }
  var slug = function (l) { return String(l).toLowerCase().replace(/[^a-z0-9]+/g, '-'); };

  var bar = document.createElement('nav');
  bar.className = 'ptabs';
  bar.setAttribute('aria-label', 'Page sections');
  var inner = document.createElement('div');
  inner.className = 'ptabs__in';
  inner.setAttribute('role', 'tablist');
  bar.appendChild(inner);
  var btns = labels.map(function (l) {
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'ptabs__btn'; b.textContent = l;
    b.setAttribute('role', 'tab'); b.setAttribute('data-ptab-btn', l);
    b.addEventListener('click', function () { show(l, true); });
    inner.appendChild(b);
    return b;
  });
  var slot = slotEl;

  var current = null, on = false;
  function initial() {
    var h = (location.hash || '').replace(/^#tab-/, '');
    for (var i = 0; i < labels.length; i++) if (h && slug(labels[i]) === h) return labels[i];
    var saved = null;
    try { saved = sessionStorage.getItem(KEY); } catch (e) {}
    return labels.indexOf(saved) >= 0 ? saved : labels[0];
  }
  function show(l, user) {
    current = l;
    tagged.forEach(function (el) { el.classList.toggle('ptab-off', on && el.getAttribute('data-ptab') !== l); });
    btns.forEach(function (b) {
      var act = b.getAttribute('data-ptab-btn') === l;
      b.classList.toggle('is-on', act); b.setAttribute('aria-selected', act ? 'true' : 'false');
      if (act && user && b.scrollIntoView) b.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
    });
    try { sessionStorage.setItem(KEY, l); } catch (e) {}
    if (user) {
      // Bring the start of the tab into view if we were scrolled past it.
      var top = bar.getBoundingClientRect().top + window.scrollY - 4;
      if (window.scrollY > top) window.scrollTo({ top: top, behavior: 'auto' });
    }
    // Charts, the globe etc. size themselves on resize once they're visible again.
    window.dispatchEvent(new Event('resize'));
    document.dispatchEvent(new CustomEvent('ptab:change', { detail: { tab: l } }));
  }
  function apply() {
    var want = !!mq.matches;
    if (want === on && (on ? bar.parentNode : true)) return;
    on = want;
    document.documentElement.classList.toggle('has-ptabs', on);
    if (on) {
      if (slot) slot.parentNode.insertBefore(bar, slot);   // a sibling, so it sticks for the whole page
      else tagged[0].parentNode.insertBefore(bar, tagged[0]);
      show(current || initial(), false);
    } else {
      if (bar.parentNode) bar.parentNode.removeChild(bar);
      tagged.forEach(function (el) { el.classList.remove('ptab-off'); });
      window.dispatchEvent(new Event('resize'));
    }
  }
  if (mq.addEventListener) mq.addEventListener('change', apply); else if (mq.addListener) mq.addListener(apply);
  apply();
  window.PhoneTabs = { show: function (l) { if (labels.indexOf(l) >= 0) show(l, true); }, labels: labels };
})();
