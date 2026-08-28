(function () {
  "use strict";

  function selectPanel(name) {
    document.querySelectorAll(".sm-tab").forEach(function (tab) {
      var active = tab.dataset.panel === name;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    document.querySelectorAll(".sm-panel").forEach(function (panel) {
      panel.hidden = panel.id !== "sm-" + name;
    });
  }

  function selectSummary(days) {
    document.querySelectorAll("[data-summary-days]").forEach(function (grid) {
      grid.classList.toggle("is-active", grid.dataset.summaryDays === days);
    });
    document.querySelectorAll("[data-summary-target]").forEach(function (button) {
      button.classList.toggle("active", button.dataset.summaryTarget === days);
    });
  }

  document.addEventListener("click", function (event) {
    var tab = event.target.closest(".sm-tab[data-panel]");
    if (tab) {
      selectPanel(tab.dataset.panel);
      return;
    }
    var range = event.target.closest("[data-summary-target]");
    if (range) selectSummary(range.dataset.summaryTarget);
  });

  selectSummary("30");
})();
