/* Profile photo: pick, position in a circle, upload. The server re-encodes it. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const file = $('pp-file'), dlg = $('pp-dialog'), cv = $('pp-canvas'), zoom = $('pp-zoom'), msg = $('pp-msg');
  if (!file || !dlg || !cv) return;
  const ctx = cv.getContext('2d');
  const S = cv.width;                       // canvas is square
  let img = null, base = 1, z = 1, ox = 0, oy = 0;

  const say = (t, bad) => { msg.textContent = t || ''; msg.classList.toggle('is-bad', !!bad); };
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

  function clamp() {
    const w = img.width * base * z, h = img.height * base * z;
    const mx = Math.max(0, (w - S) / 2), my = Math.max(0, (h - S) / 2);
    ox = Math.min(mx, Math.max(-mx, ox));
    oy = Math.min(my, Math.max(-my, oy));
  }
  function draw(c, size) {
    const k = size / S, s = base * z * k;
    c.fillStyle = '#0c101a'; c.fillRect(0, 0, size, size);
    c.imageSmoothingQuality = 'high';
    c.drawImage(img, size / 2 + ox * k - img.width * s / 2, size / 2 + oy * k - img.height * s / 2, img.width * s, img.height * s);
  }
  const paint = () => { clamp(); draw(ctx, S); };

  function open(f) {
    if (!f) return;
    if (f.size > 25 * 1024 * 1024) { say('That photo is too large. Pick one under 25 MB.', true); return; }
    const url = URL.createObjectURL(f);
    const im = new Image();
    im.onload = () => {
      img = im; base = Math.max(S / im.width, S / im.height); z = 1; ox = oy = 0; zoom.value = '1';
      paint(); say('');
      if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    };
    im.onerror = () => { URL.revokeObjectURL(url); say('This browser can\'t open that file. Try a JPEG or PNG.', true); };
    im.src = url;
  }
  const close = () => { if (dlg.open) dlg.close(); file.value = ''; };

  ['pp-pick', 'pp-pick2'].forEach(id => $(id) && $(id).addEventListener('click', () => file.click()));
  file.addEventListener('change', () => open(file.files && file.files[0]));
  $('pp-cancel').addEventListener('click', close);
  dlg.addEventListener('cancel', () => { file.value = ''; });

  // Drag, pinch and wheel.
  const pts = new Map(); let pinch = null;
  cv.addEventListener('pointerdown', e => { cv.setPointerCapture(e.pointerId); pts.set(e.pointerId, {x: e.clientX, y: e.clientY}); });
  cv.addEventListener('pointermove', e => {
    if (!pts.has(e.pointerId) || !img) return;
    const prev = pts.get(e.pointerId), k = S / cv.getBoundingClientRect().width;
    pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
    if (pts.size === 2) {
      const [a, b] = [...pts.values()], d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch) { setZoom(z * d / pinch); }
      pinch = d; return;
    }
    ox += (e.clientX - prev.x) * k; oy += (e.clientY - prev.y) * k; paint();
  });
  const up = e => { pts.delete(e.pointerId); if (pts.size < 2) pinch = null; };
  cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
  cv.addEventListener('wheel', e => { e.preventDefault(); setZoom(z * (e.deltaY < 0 ? 1.08 : 1 / 1.08)); }, {passive: false});
  function setZoom(v) {
    const nz = Math.min(4, Math.max(1, v)), r = nz / z;
    ox *= r; oy *= r; z = nz; zoom.value = String(z); paint();
  }
  zoom.addEventListener('input', () => setZoom(+zoom.value));

  function swapPictures(url) {
    const html = '<img src="' + url + '" alt="" decoding="async">';
    document.querySelectorAll('.nav-avatar__btn').forEach(b => { b.innerHTML = '<img class="nav-avatar__img" src="' + url + '" alt="" width="44" height="44">'; });
    document.querySelectorAll('.nav-profile__pic').forEach(p => { p.innerHTML = html; });
    document.querySelectorAll('.nav-profile__txt small').forEach(s => { s.textContent = 'Change profile photo'; });
    const mob = document.querySelector('[aria-controls="mobile-account-menu"] .mobile-app-nav__icon');
    if (mob) { mob.classList.add('has-photo'); mob.innerHTML = '<img class="mobile-app-nav__photo" src="' + url + '" alt="" width="26" height="26">'; }
    const face = document.querySelector('.pp-face');
    if (face) face.innerHTML = '<img id="pp-img" src="' + url + '" alt="Your profile photo" width="96" height="96">';
    $('pp-pick2').textContent = 'Change photo';
    $('pp-remove').hidden = false;
  }

  $('pp-save').addEventListener('click', () => {
    if (!img) return;
    const out = document.createElement('canvas'); out.width = out.height = 512;
    draw(out.getContext('2d'), 512);
    const btn = $('pp-save'); btn.disabled = true; btn.textContent = 'Saving…';
    out.toBlob(async blob => {
      try {
        const fd = new FormData(); fd.append('photo', blob, 'photo.jpg');
        const r = await fetch('/api/account/photo', {method: 'POST', body: fd, credentials: 'same-origin', headers: {'X-CSRFToken': csrf()}});
        const d = await r.json().catch(() => ({ok: false}));
        if (!d.ok) throw new Error(d.error || 'Could not save the photo.');
        swapPictures(d.url); close(); say('Saved. It now shows in your menu on every page.');
      } catch (err) { say(err.message || 'Could not save the photo.', true); }
      finally { btn.disabled = false; btn.textContent = 'Save photo'; }
    }, 'image/jpeg', 0.92);
  });

  // Remove: tap twice, so a slip doesn't delete it.
  let armed = null;
  $('pp-remove').addEventListener('click', async (e) => {
    const b = e.currentTarget;
    if (!armed) { b.textContent = 'Tap again to remove'; armed = setTimeout(() => { b.textContent = 'Remove'; armed = null; }, 3500); return; }
    clearTimeout(armed); armed = null;
    const r = await fetch('/api/account/photo', {method: 'DELETE', credentials: 'same-origin', headers: {'X-CSRFToken': csrf()}});
    if (r.ok) location.reload(); else { b.textContent = 'Remove'; say('Could not remove it. Try again.', true); }
  });
})();
