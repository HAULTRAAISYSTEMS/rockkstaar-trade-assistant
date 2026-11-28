/* Profile photo: tap the photo circle in the account menu, pick a photo, done.
   The server centre-crops it to a square and strips hidden data. */
(function () {
  'use strict';
  const input = document.getElementById('profile-photo-file');
  const picks = document.querySelectorAll('[data-photo-pick]');
  if (!input || !picks.length) return;
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const status = (t, bad) => document.querySelectorAll('[data-photo-status]').forEach(s => {
    s.textContent = t; s.classList.toggle('is-bad', !!bad);
  });

  picks.forEach(b => b.addEventListener('click', () => input.click()));

  // Shrink big phone photos before sending; fall back to the original file.
  function shrink(file) {
    return new Promise(resolve => {
      const url = URL.createObjectURL(file), img = new Image();
      img.onload = () => {
        const k = Math.min(1, 1024 / Math.max(img.width, img.height));
        const c = document.createElement('canvas');
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        c.toBlob(b => resolve(b || file), 'image/jpeg', 0.9);
      };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
      img.src = url;
    });
  }

  function show(url) {
    document.querySelectorAll('.nav-avatar__btn').forEach(b => {
      b.innerHTML = '<img class="nav-avatar__img" src="' + url + '" alt="" width="44" height="44">';
    });
    document.querySelectorAll('.nav-profile__pic').forEach(p => {
      const cam = p.querySelector('.nav-profile__cam');
      p.innerHTML = '<img src="' + url + '" alt="">';
      if (cam) p.appendChild(cam);
    });
    const mob = document.querySelector(".nav-phone-avatar");
    if (mob) mob.innerHTML = '<img class="mobile-app-nav__photo" src="' + url + '" alt="" width="26" height="26">';
  }

  input.addEventListener('change', async () => {
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    if (file.size > 25 * 1024 * 1024) { status('That photo is over 25 MB. Pick a smaller one.', true); return; }
    status('Uploading…');
    try {
      const fd = new FormData();
      fd.append('photo', await shrink(file), 'photo.jpg');
      const r = await fetch('/api/account/photo', {method: 'POST', body: fd, credentials: 'same-origin',
                                                    headers: {'X-CSRFToken': csrf()}});
      const d = await r.json().catch(() => ({ok: false}));
      if (!d.ok) throw new Error(d.error || 'Could not save that photo.');
      show(d.url);
      status('Saved · tap to change');
    } catch (e) {
      const net = e instanceof TypeError;   // fetch() itself failed: connection dropped or offline
      status(net ? 'Upload didn\'t go through. Check your connection and try again.' : (e.message || 'Could not save that photo.'), true);
    }
  });
})();
