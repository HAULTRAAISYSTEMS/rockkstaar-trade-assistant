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
});
