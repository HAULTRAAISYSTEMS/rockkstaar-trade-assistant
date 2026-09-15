(function () {
  'use strict';
  const shell = document.querySelector('.push-shell');
  if (!shell) return;
  const status = document.getElementById('push-status');
  const state = document.getElementById('push-state');
  const enable = document.getElementById('push-enable');
  const disable = document.getElementById('push-disable');
  const test = document.getElementById('push-test');

  function supported() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  }
  function isIOS() { return /iPad|iPhone|iPod/.test(navigator.userAgent); }
  function isStandalone() { return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true; }
  function keyBytes(value) {
    const padded = value + '='.repeat((4 - value.length % 4) % 4);
    const raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(Array.from(raw), (char) => char.charCodeAt(0));
  }
  function setStatus(message, good) {
    status.textContent = message;
    status.classList.toggle('good', Boolean(good));
  }
  async function subscription() {
    const registration = await navigator.serviceWorker.ready;
    return registration.pushManager.getSubscription();
  }
  async function refresh() {
    if (!supported()) {
      setStatus('This browser does not support web push. Try current Safari, Chrome, Edge, or Firefox.', false);
      enable.disabled = disable.disabled = test.disabled = true;
      return;
    }
    if (isIOS() && !isStandalone()) {
      setStatus('On iPhone, first add Tradestaar to your Home Screen and open it from the new icon.', false);
      disable.disabled = test.disabled = true;
      return;
    }
    const current = await subscription();
    const active = Boolean(current && Notification.permission === 'granted');
    setStatus(active ? 'Free alerts are enabled on this device.' : 'Alerts are not enabled on this device.', active);
    state.textContent = active ? 'Active' : 'Not enabled here';
    state.classList.toggle('on', active);
    enable.disabled = active;
    disable.disabled = !active;
    test.disabled = !active;
  }
  enable.addEventListener('click', async function () {
    enable.disabled = true;
    try {
      if (isIOS() && !isStandalone()) throw new Error('Add Tradestaar to your iPhone Home Screen first, then open it from the icon.');
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') throw new Error('Notification permission was not granted. Change it in your browser settings and try again.');
      const registration = await navigator.serviceWorker.ready;
      const current = await registration.pushManager.getSubscription();
      const created = current || await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: keyBytes(shell.dataset.vapidKey)
      });
      const response = await fetch('/api/browser-push/subscriptions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subscription: created.toJSON() })
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || 'The subscription could not be saved.');
      await refresh();
    } catch (error) {
      setStatus(error.message || 'Notifications could not be enabled.', false);
      enable.disabled = false;
    }
  });
  disable.addEventListener('click', async function () {
    disable.disabled = true;
    try {
      const current = await subscription();
      if (current) {
        await fetch('/api/browser-push/subscriptions', {
          method: 'DELETE', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: current.endpoint })
        });
        await current.unsubscribe();
      }
      await refresh();
    } catch (error) {
      setStatus(error.message || 'Notifications could not be disabled.', false);
      disable.disabled = false;
    }
  });
  test.addEventListener('click', async function () {
    test.disabled = true;
    try {
      const response = await fetch('/api/browser-push/test', { method: 'POST' });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error || 'The test alert could not be sent.');
      setStatus('Test alert sent. It should appear in a moment.', true);
    } catch (error) {
      setStatus(error.message || 'The test alert could not be sent.', false);
    } finally {
      test.disabled = false;
    }
  });
  refresh().catch(function () { setStatus('Notification status could not be checked.', false); });
})();
