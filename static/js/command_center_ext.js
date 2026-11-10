/* Market mood dial + spoken briefing on the Today page. */
(function () {
  "use strict";
  var wrap = document.getElementById("cm-wrap"); if (!wrap) return;
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  var needle = document.getElementById("cm-needle"), label = document.getElementById("cm-label"), score = document.getElementById("cm-score"),
      note = document.getElementById("cm-note"), parts = document.getElementById("cm-parts");

  function setMood(d) {
    if (!d || !d.known) { label.textContent = "—"; note.textContent = "Mood needs live market data — it will appear once the feed answers."; return; }
    needle.style.transform = "rotate(" + (-90 + d.score * 1.8) + "deg)";
    label.textContent = d.label; score.textContent = d.score + " / 100";
    wrap.className = wrap.className.replace(/\bmood-\S+/g, "") + " mood-" + d.label.toLowerCase();
    note.textContent = d.note;
    parts.innerHTML = d.parts.map(function (p) {
      return '<div class="cm-part"><span>' + esc(p.name) + "</span><i><b class=\"" + (p.score >= 62 ? "hi" : p.score <= 38 ? "lo" : "") + "\" style=\"width:" + p.score + '%"></b></i><small>' + esc(p.detail) + "</small></div>";
    }).join("");
  }
  function loadMood() {
    fetch("/api/mood", { cache: "no-store", headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(setMood).catch(function () {});
  }
  loadMood(); setInterval(loadMood, 30000);

  /* Voice briefing (Web Speech API — free, works in Safari/Chrome).

     iPhone rules this follows:
     - Speech only starts if speak() is called inside the tap itself, so the
       script is fetched ahead of time and spoken synchronously on tap.
     - The ring/silent switch mutes Web Speech. Playing a short silent clip
       from an <audio> element in the same tap moves the page to the media
       audio channel, so the briefing plays like a video would.
     - cancel() right before speak() can leave iOS silent; only cancel when
       something is actually queued. */
  var btn = document.getElementById("cm-play"), txt = document.getElementById("cm-play-text"), wave = document.getElementById("cm-wave"),
      vnote = document.getElementById("cm-voice-note"), speaking = false, script = null, scriptAt = 0, watchdog = null;
  var synth = window.speechSynthesis;
  if (!synth || typeof SpeechSynthesisUtterance === "undefined") { btn.disabled = true; vnote.textContent = "This browser can't speak. Try Safari or Chrome."; return; }
  var SILENT = "data:audio/wav;base64,UklGRmQGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YUAGAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICA";
  var unlockEl = null;
  function unlockAudio() {
    try {
      if (!unlockEl) { unlockEl = new Audio(SILENT); unlockEl.setAttribute("playsinline", ""); unlockEl.preload = "auto"; }
      var pr = unlockEl.play(); if (pr && pr.catch) pr.catch(function () {});
    } catch (e) {}
  }
  function pickVoice() {
    var vs = synth.getVoices() || [], prefer = ["Samantha", "Ava", "Allison", "Google US English", "Microsoft Aria", "Alex"];
    for (var i = 0; i < prefer.length; i++) { var v = vs.find(function (x) { return x.name.indexOf(prefer[i]) > -1; }); if (v) return v; }
    return vs.find(function (x) { return /^en(-|_)US/i.test(x.lang); }) || vs.find(function (x) { return /^en/i.test(x.lang); }) || null;
  }
  function loadScript() {
    return fetch("/api/briefing/voice", { cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d && d.ok && d.script) { script = d; scriptAt = Date.now(); } return script; })
      .catch(function () { return script; });
  }
  loadScript(); setInterval(function () { if (!speaking) loadScript(); }, 60000);

  function stop() {
    if (watchdog) { clearTimeout(watchdog); watchdog = null; }
    if (synth.speaking || synth.pending) synth.cancel();
    speaking = false; wrap.classList.remove("is-speaking"); txt.textContent = "Play 60-sec briefing"; btn.querySelector(".cm-play-ico").textContent = "▶";
  }
  function speak(d) {
    var sentences = d.script.match(/[^.!?]+[.!?]+/g) || [d.script], voice = pickVoice(), left = sentences.length, started = false;
    if (synth.speaking || synth.pending) synth.cancel();
    wrap.classList.add("is-speaking"); txt.textContent = "Stop briefing"; btn.querySelector(".cm-play-ico").textContent = "■";
    vnote.textContent = "≈ " + Math.round(d.words / 2.6) + " seconds";
    sentences.forEach(function (s) {
      var u = new SpeechSynthesisUtterance(s.trim()); if (voice) u.voice = voice; u.lang = (voice && voice.lang) || "en-US";
      u.rate = 1.04; u.pitch = 1; u.volume = 1;
      u.onstart = function () { started = true; };
      u.onend = u.onerror = function () { if (--left <= 0 && speaking) stop(); };
      synth.speak(u);
    });
    if (synth.paused) synth.resume();
    // If nothing has started after 2.5 s the phone is blocking speech — say so
    // instead of showing a wave over silence.
    watchdog = setTimeout(function () {
      if (speaking && !started && !synth.speaking) {
        stop();
        vnote.textContent = "Your phone blocked the audio — turn the volume up, then tap Play again.";
      }
    }, 2500);
  }
  btn.addEventListener("click", function () {
    if (speaking) { stop(); return; }
    speaking = true;
    unlockAudio();
    if (script && Date.now() - scriptAt < 5 * 60000) { speak(script); loadScript(); return; }
    // No script yet: wake speech inside the tap with a real (quiet) word, then read when it arrives.
    txt.textContent = "Preparing…";
    try { var w = new SpeechSynthesisUtterance("Briefing"); w.volume = 0.01; synth.speak(w); } catch (e) {}
    loadScript().then(function (d) {
      if (!speaking) return;
      if (!d) { stop(); vnote.textContent = "Briefing unavailable right now."; return; }
      speak(d);
    });
  });
  if (synth.onvoiceschanged !== undefined) synth.onvoiceschanged = function () { pickVoice(); };
})();
