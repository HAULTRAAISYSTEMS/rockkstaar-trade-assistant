/**
 * main.js — Rockkstaar Trade Assistant
 * Lightweight client-side enhancements. No dependencies.
 */

// ---- Auto-dismiss flash messages after 4 seconds ----
document.addEventListener("DOMContentLoaded", function () {
  const flashes = document.querySelectorAll(".flash");
  flashes.forEach(function (el) {
    setTimeout(function () {
      el.style.transition = "opacity 0.4s";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 400);
    }, 4000);
  });

  // ---- Uppercase ticker input as user types ----
  const tickerInput = document.querySelector(".ticker-input");
  if (tickerInput) {
    tickerInput.addEventListener("input", function () {
      const pos = this.selectionStart;
      this.value = this.value.toUpperCase();
      this.setSelectionRange(pos, pos);
    });
  }

  // ---- Keyboard shortcut: press "/" to focus the ticker input ----
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") {
      e.preventDefault();
      if (tickerInput) {
        tickerInput.focus();
        tickerInput.select();
      }
    }
  });

  // ---- Unlock audio on first user gesture ----
  document.addEventListener("click", _unlockAudio, { once: true });

  // ---- Inject toast container ----
  const tc = document.createElement("div");
  tc.id = "trigger-toast-container";
  tc.className = "trigger-toast-container";
  document.body.appendChild(tc);

  // ---- Kick off auto-refresh ----
  scheduleRefresh();
});


// ===========================================================================
// Auto-Refresh System
// ===========================================================================

window._arEnabled   = true;
window._arTimer     = null;
window._arInterval  = 8000;    // 8 seconds between live patches during market hours
window._arRefreshFn = null;    // Set by each page that supports live updates

function isMarketHours() {
  try {
    const etStr = new Date().toLocaleString("en-US", { timeZone: "America/New_York" });
    const et    = new Date(etStr);
    const day   = et.getDay();
    if (day === 0 || day === 6) return false;
    const mins  = et.getHours() * 60 + et.getMinutes();
    return mins >= 240 && mins < 1200;   // 4:00 AM – 8:00 PM ET
  } catch (e) {
    return true;
  }
}

function toggleAutoRefresh() {
  window._arEnabled = !window._arEnabled;
  const on = window._arEnabled;
  // Navbar button
  const btn = document.getElementById("ar-toggle");
  if (btn) {
    btn.textContent = on ? "⟳ Auto ON" : "⟳ Auto OFF";
    btn.className   = "btn-ar-toggle " + (on ? "ar-on" : "ar-off");
  }
  // Mobile bar button
  const mob = document.getElementById("mobile-ar-toggle");
  if (mob) {
    mob.textContent = on ? "⟳ Auto" : "⟳ Off";
    mob.className   = "mobile-ctrl-btn mobile-ctrl-ar " + (on ? "mcb-on" : "mcb-off");
  }
  if (on) {
    scheduleRefresh();
  } else {
    clearTimeout(window._arTimer);
    const el = document.getElementById("ar-last-updated");
    if (el) el.textContent = "Auto-refresh OFF";
  }
}

function scheduleRefresh() {
  clearTimeout(window._arTimer);
  if (!window._arEnabled) return;
  if (!window._arRefreshFn) return;

  if (!isMarketHours()) {
    window._arTimer = setTimeout(scheduleRefresh, 60000);
    return;
  }

  window._arTimer = setTimeout(async function () {
    try { await window._arRefreshFn(); } catch (e) { /* silent */ }
    scheduleRefresh();
  }, window._arInterval);
}

window._arSetLastUpdated = function (timeStr) {
  const el = document.getElementById("ar-last-updated");
  if (el) el.textContent = "Updated " + timeStr;
  // Also sync the mobile bar timestamp
  const mob = document.getElementById("mobile-last-updated");
  if (mob) mob.textContent = timeStr;
};


// ===========================================================================
// Alert Sound Engine  (Web Audio API — no files needed)
// ===========================================================================

window._arSoundEnabled = true;
let   _audioCtx        = null;

function _unlockAudio() {
  try {
    _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (_audioCtx.state === "suspended") _audioCtx.resume();
  } catch (e) { /* browser doesn't support Web Audio */ }
}

function toggleAlertSound() {
  window._arSoundEnabled = !window._arSoundEnabled;
  const on = window._arSoundEnabled;
  // Navbar button
  const btn = document.getElementById("alert-sound-toggle");
  if (btn) {
    btn.textContent = on ? "🔔 Sound ON" : "🔕 Sound OFF";
    btn.className   = "btn-ar-toggle " + (on ? "ar-on" : "ar-off");
  }
  // Mobile bar button
  const mob = document.getElementById("mobile-sound-toggle");
  if (mob) {
    mob.textContent = on ? "🔔" : "🔕";
    mob.className   = "mobile-ctrl-btn mobile-ctrl-sound " + (on ? "mcb-on" : "mcb-off");
  }
}

/** Play a single oscillator tone. */
function _tone(ctx, freq, startTime, duration, type = "sine", peakGain = 0.28) {
  const osc  = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.type = type;
  osc.frequency.setValueAtTime(freq, startTime);
  gain.gain.setValueAtTime(0, startTime);
  gain.gain.linearRampToValueAtTime(peakGain, startTime + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.001, startTime + duration);
  osc.start(startTime);
  osc.stop(startTime + duration + 0.05);
}

/**
 * Play the appropriate alert sound for a trigger type.
 *
 * ORB Breakout    — ascending 3-note chime (C5 E5 G5)
 * VWAP Reclaim    — double pulse (A4 A4)
 * Momentum Runner — rapid 4-note rise (G4 B4 D5 G5)
 * Breakdown       — descending warning (C5 → G4), sawtooth
 * Momentum Breakout / Generic — bright single chime
 */
function playAlertSound(alertType) {
  if (!window._arSoundEnabled) return;
  try {
    if (!_audioCtx) _unlockAudio();
    if (!_audioCtx) return;
    if (_audioCtx.state === "suspended") _audioCtx.resume();
    const now = _audioCtx.currentTime;

    switch (alertType) {
      case "ORB Breakout":
        _tone(_audioCtx, 523, now,        0.18, "sine");
        _tone(_audioCtx, 659, now + 0.16, 0.18, "sine");
        _tone(_audioCtx, 784, now + 0.32, 0.28, "sine", 0.32);
        break;
      case "VWAP Reclaim":
        _tone(_audioCtx, 440, now,        0.12, "sine");
        _tone(_audioCtx, 440, now + 0.19, 0.14, "sine", 0.32);
        break;
      case "Momentum Runner":
        [392, 494, 587, 784].forEach((f, i) =>
          _tone(_audioCtx, f, now + i * 0.075, 0.11, "triangle", 0.22));
        break;
      case "Breakdown":
        _tone(_audioCtx, 523, now,        0.22, "sawtooth", 0.18);
        _tone(_audioCtx, 392, now + 0.22, 0.35, "sawtooth", 0.14);
        break;
      case "Momentum Breakout":
        _tone(_audioCtx, 587, now,        0.16, "sine");
        _tone(_audioCtx, 784, now + 0.17, 0.25, "sine", 0.32);
        break;
      default:
        _tone(_audioCtx, 600, now, 0.22, "sine");
    }
  } catch (e) {
    console.warn("Alert sound failed:", e);
  }
}


// ===========================================================================
// Trigger State Detection
// ===========================================================================

const _prevExecStates   = new Map();   // ticker → last known final_action
let   _statesInitialized = false;      // first call just seeds state, no alerts

/**
 * Compare ranked list against remembered states.
 * Returns stocks that just transitioned to TRIGGERED this cycle.
 * Uses final_action (session-aware, score-reconciled) so alerts only fire
 * during regular market hours when all conditions are truly confirmed.
 * On the very first call (page load), seeds the map silently.
 */
function detectNewTriggers(ranked) {
  const fresh = [];
  for (const s of ranked) {
    const prev = _prevExecStates.get(s.ticker);
    const cur  = s.final_action || "WAIT";
    if (_statesInitialized && prev !== undefined && prev !== "TRIGGERED" && cur === "TRIGGERED") {
      fresh.push(s);
    }
    _prevExecStates.set(s.ticker, cur);
  }
  if (!_statesInitialized) _statesInitialized = true;
  return fresh;
}


// ===========================================================================
// Alert Type Classifier
// ===========================================================================

/**
 * Map a triggered stock to a named alert type used for sounds and labels.
 * Priority: explicit setup_type > ORB status > bias fallback.
 */
function getAlertType(s) {
  const st  = s.setup_type  || "";
  const orb = s.orb_status  || "";
  const bias = s.trade_bias || "";

  if (st === "Momentum Runner")                              return "Momentum Runner";
  if (st === "VWAP Reclaim")                                 return "VWAP Reclaim";
  if (st === "Breakdown" || (orb === "BELOW" && bias === "Short Bias"))
                                                             return "Breakdown";
  if (st === "ORB" || (s.orb_ready === "YES" && orb === "ABOVE"))
                                                             return "ORB Breakout";
  if (st === "Momentum Breakout" || st === "Gap and Go" || st === "Range Break")
                                                             return "Momentum Breakout";
  return "Alert";
}

const ALERT_TYPE_COLOR = {
  "ORB Breakout":      "#22c55e",
  "VWAP Reclaim":      "#3b82f6",
  "Momentum Runner":   "#f97316",
  "Breakdown":         "#ef4444",
  "Momentum Breakout": "#22c55e",
  "Alert":             "#6366f1",
};


// ===========================================================================
// Trigger Toast Notifications
// ===========================================================================

let _toastIdSeq = 0;

/**
 * Show a pop-up toast for a newly triggered stock.
 * Stacks in the top-right corner; auto-dismisses after 12 s.
 */
function showTriggerToast(s, alertType) {
  const container = document.getElementById("trigger-toast-container");
  if (!container) return;

  const id    = "toast-" + (++_toastIdSeq);
  const color = ALERT_TYPE_COLOR[alertType] || "#6366f1";
  const price = s.current_price != null ? "$" + s.current_price.toFixed(2) : "—";
  const gap   = s.gap_display || "—";
  const mom   = s.momentum_score != null ? s.momentum_score + "/10" : "—";

  const toast = document.createElement("div");
  toast.id        = id;
  toast.className = "trigger-toast";
  toast.style.setProperty("--alert-color", color);
  toast.innerHTML = `
    <div class="toast-header">
      <span class="toast-flash">⚡</span>
      <span class="toast-ticker">${s.ticker}</span>
      <span class="toast-alert-type">${alertType}</span>
      <button class="toast-close" onclick="dismissToast('${id}')" title="Dismiss">✕</button>
    </div>
    <div class="toast-body">
      <span class="toast-price">${price}</span>
      <span class="toast-gap ${s.gap_class}">${gap}</span>
      <span class="toast-mom">Mom ${mom}</span>
    </div>
    <a href="/stock/${s.ticker}" class="toast-action">View details →</a>
    <div class="toast-progress"><div class="toast-progress-bar" id="${id}-bar"></div></div>
  `;
  container.prepend(toast);

  // Animate in
  requestAnimationFrame(() => toast.classList.add("toast-visible"));

  // Progress bar shrink
  const bar = document.getElementById(id + "-bar");
  if (bar) {
    bar.style.transition = "width 12s linear";
    requestAnimationFrame(() => { bar.style.width = "0%"; });
  }

  // Auto-dismiss after 12 s
  const timer = setTimeout(() => dismissToast(id), 12000);
  toast.dataset.timer = timer;
}

function dismissToast(id) {
  const toast = document.getElementById(id);
  if (!toast) return;
  clearTimeout(Number(toast.dataset.timer));
  toast.classList.remove("toast-visible");
  setTimeout(() => toast.remove(), 350);
}
