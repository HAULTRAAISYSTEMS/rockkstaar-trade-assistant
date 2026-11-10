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

  /* Voice briefing (Web Speech API — free, works in Safari/Chrome). */
  var btn = document.getElementById("cm-play"), txt = document.getElementById("cm-play-text"), wave = document.getElementById("cm-wave"),
      vnote = document.getElementById("cm-voice-note"), speaking = false;
  var synth = window.speechSynthesis;
  if (!synth) { btn.disabled = true; vnote.textContent = "This browser can't speak. Try Safari or Chrome."; return; }
  function pickVoice() {
    var vs = synth.getVoices() || [], prefer = ["Samantha", "Ava", "Allison", "Google US English", "Microsoft Aria", "Alex"];
    for (var i = 0; i < prefer.length; i++) { var v = vs.find(function (x) { return x.name.indexOf(prefer[i]) > -1; }); if (v) return v; }
    return vs.find(function (x) { return /^en(-|_)US/i.test(x.lang); }) || vs.find(function (x) { return /^en/i.test(x.lang); }) || null;
  }
  function stop() { synth.cancel(); speaking = false; wrap.classList.remove("is-speaking"); txt.textContent = "Play 60-sec briefing"; btn.querySelector(".cm-play-ico").textContent = "▶"; }
  btn.addEventListener("click", function () {
    if (speaking) { stop(); return; }
    speaking = true; txt.textContent = "Preparing…";
    // iOS only allows speech started inside the tap: prime it now.
    try { synth.speak(new SpeechSynthesisUtterance("")); } catch (e) {}
    fetch("/api/briefing/voice", { cache: "no-store", headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(function (d) {
      if (!speaking) return;
      if (!d || !d.ok) { stop(); vnote.textContent = "Briefing unavailable right now."; return; }
      var sentences = d.script.match(/[^.!?]+[.!?]+/g) || [d.script], voice = pickVoice(), left = sentences.length;
      synth.cancel();
      wrap.classList.add("is-speaking"); txt.textContent = "Stop briefing"; btn.querySelector(".cm-play-ico").textContent = "■";
      vnote.textContent = "≈ " + Math.round(d.words / 2.6) + " seconds";
      sentences.forEach(function (s) {
        var u = new SpeechSynthesisUtterance(s.trim()); if (voice) u.voice = voice; u.rate = 1.04; u.pitch = 1;
        u.onend = function () { if (--left <= 0) stop(); };
        synth.speak(u);
      });
    }).catch(function () { stop(); vnote.textContent = "Briefing unavailable right now."; });
  });
  if (synth.onvoiceschanged !== undefined) synth.onvoiceschanged = function () {};
})();
