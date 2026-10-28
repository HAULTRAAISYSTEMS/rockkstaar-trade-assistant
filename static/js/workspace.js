/* Shared, keyboard-accessible navigation. No provider calls or fabricated prices. */
(function () {
  'use strict';
  const dialog = document.getElementById('workspace-search');
  const opener = document.getElementById('workspace-search-open');
  if (dialog && opener) {
    const query = document.getElementById('workspace-query');
    const ticker = document.getElementById('workspace-ticker');
    const destinations = Array.from(dialog.querySelectorAll('[data-workspace-destination]'));
    const links = () => Array.from(dialog.querySelectorAll('.workspace-results a')).filter(a => !a.hidden);
    function filter() {
      const value = query.value.trim();
      const symbol = value.replace(/^\$/, '').toUpperCase();
      const matchesTool = destinations.some(a => a.textContent.toLowerCase().includes(value.toLowerCase()));
      ticker.hidden = !/^[A-Z][A-Z0-9.-]{0,9}$/.test(symbol) || (matchesTool && !value.startsWith('$'));
      if (!ticker.hidden) {
        ticker.href = ticker.dataset.researchUrl + '?ticker=' + encodeURIComponent(symbol);
        ticker.querySelector('strong').textContent = 'Research ' + symbol;
      }
      destinations.forEach(a => { a.hidden = !a.textContent.toLowerCase().includes(value.toLowerCase()); });
      document.getElementById('workspace-empty').hidden = links().length > 0;
    }
    function open() {
      document.querySelectorAll('details[open]').forEach(el => { el.open = false; });
      query.value = ''; filter(); dialog.showModal(); query.focus();
    }
    opener.addEventListener('click', open);
    document.getElementById('workspace-search-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => opener.focus());
    dialog.addEventListener('click', event => { if (event.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
    }});
    query.addEventListener('input', filter);
    dialog.addEventListener('keydown', event => {
      const items = links();
      if (event.key === 'Enter' && event.target === query && items.length) { event.preventDefault(); items[0].click(); }
      if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && items.length) {
        event.preventDefault();
        const index = items.indexOf(document.activeElement);
        const next = index < 0 ? (event.key === 'ArrowDown' ? 0 : items.length - 1) : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
        items[next].focus();
      }
    });
    document.addEventListener('keydown', event => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || event.isComposing || dialog.open) return;
      if (event.target.closest('input,textarea,select,[contenteditable="true"],[role="textbox"]') || document.querySelector('dialog[open]')) return;
      event.preventDefault(); open();
    });
  }
  const badge = document.getElementById('nav-live-indicator');
  const label = document.getElementById('nav-live-label');
  let pending = false;
  async function updateSession() {
    if (!badge || !label || document.hidden || pending) return;
    pending = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch('/api/session-status', {signal: controller.signal, cache: 'no-store'});
      if (!response.ok) throw new Error('Unavailable');
      const status = await response.json();
      label.textContent = status.label || 'SCHEDULE UNAVAILABLE';
      badge.dataset.session = status.session || 'unknown';
      badge.title = (status.basis || '') + (status.early_close ? ' · Early close today' : '');
    } catch (_) {
      label.textContent = 'SCHEDULE UNAVAILABLE'; badge.dataset.session = 'unknown';
    } finally { clearTimeout(timeout); pending = false; }
  }
  updateSession(); setInterval(updateSession, 60000);
  document.addEventListener('visibilitychange', updateSession);
})();
