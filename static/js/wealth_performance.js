(() => {
  "use strict";
  const root = document.getElementById("performance-center");
  if (!root) return;

  const get = id => document.getElementById("pc-" + id);
  const account = get("account");
  const strategy = get("strategy");
  const status = get("status");
  const chart = get("chart");
  const chartRange = get("range");
  const current = new URLSearchParams(location.search);
  if (Array.from(strategy.options).some(option => option.value === current.get("strategy"))) {
    strategy.value = current.get("strategy");
  }

  const dash = "—";
  const usd = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
  const finite = Number.isFinite;
  const number = (value, digits = 2) => finite(value) ? value.toFixed(digits) : dash;
  const money = value => finite(value) ? usd.format(value) : dash;
  const signed = (value, suffix) => finite(value) ? (value > 0 ? "+" : "") + number(value) + suffix : dash;
  const pct = value => signed(value, "%");
  const pp = value => signed(value, "");
  const time = value => {
    if (!value) return "";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
  };
  const strategyNames = {long_term: "Long-Term Investing", active: "Active Trading", unclassified: "Unclassified"};
  const allocationColors = ["#26f596", "#12aafa", "#ad82ed", "#8caabc"];
  const seriesInfo = [
    {key: "portfolio", label: "Portfolio", color: "#29f894"},
    {key: "benchmark", label: "S&P 500 TR", color: "#26aaff"},
    {key: "target", label: "Target", color: "#bc83ff", dash: [6, 4]}
  ];
  let response;
  let inflight;
  let plot;
  let selectedDay = null;
  let chartFrame = 0;
  let syncMessage = "";

  function color(element, value) {
    element.classList.toggle("pc-up", finite(value) && value > 0);
    element.classList.toggle("pc-down", finite(value) && value < 0);
  }

  function field(name, value, tone) {
    for (const element of root.querySelectorAll('[data-field="' + name + '"]')) {
      element.textContent = value;
      color(element, tone);
    }
  }

  function tableRow(parent, label, values) {
    const row = parent.insertRow();
    const heading = document.createElement("th");
    heading.scope = "row";
    heading.textContent = label;
    row.append(heading);
    for (const [content, tone] of values) {
      const cell = row.insertCell();
      cell.textContent = content;
      color(cell, tone);
    }
    return row;
  }

  function emptyTable(parent, columns, message) {
    const cell = parent.insertRow().insertCell();
    cell.colSpan = columns;
    cell.textContent = message;
  }

  function replaceList(container, textItems) {
    container.replaceChildren();
    for (const item of textItems) {
      const li = document.createElement("li");
      li.textContent = item;
      container.append(li);
    }
  }

  function notice(message, retry = false, ledger = true, accountId = account.value) {
    get("notice").hidden = !message;
    get("notice-message").textContent = message;
    get("ledger-link").hidden = !ledger;
    const ledgerUrl = new URL(get("ledger-link").href);
    ledgerUrl.search = accountId === "all" ? "" : "?p=" + accountId;
    get("ledger-link").href = ledgerUrl;
    root.querySelector('[data-action="retry"]').hidden = !retry;
    root.querySelector("[data-history-notice]").hidden = retry;
  }

  const shareQuantity = new Intl.NumberFormat("en-US", {maximumFractionDigits: 6});

  function renderOpenPositions(data) {
    const positions = get("open-positions");
    positions.replaceChildren();
    const rows = data.position.holdings;
    if (!rows.length) emptyTable(positions, 7, "No open positions available in this view.");
    for (const row of rows) {
      const position = tableRow(positions, row.ticker, [[shareQuantity.format(row.shares)],
        [money(row.market_value)], [money(row.cost_basis)], [money(row.unrealized), row.unrealized],
        [pct(row.return_pct), row.return_pct], [percentUnsigned(row.weight_pct)]]);
      const research = document.createElement("a");
      research.href = root.dataset.stock.replace("__TICKER__", encodeURIComponent(row.ticker));
      research.textContent = row.ticker;
      position.cells[0].replaceChildren(research);
    }
    const asOf = data.sync.as_of.at(-1);
    const sources = data.position.quote_sources;
    const includesLedger = sources.some(source => source.source !== "Schwab");
    const sourceLabel = asOf ? "Schwab snapshot " + time(asOf) : "";
    get("open-source").textContent = [sourceLabel, includesLedger ? "Ledger and quotes" : ""].filter(Boolean).join(" · ");
  }

  function needsHistory(check) {
    return !check.has_transactions || !check.history_confirmed || !check.live ||
      !check.cash_matches || check.positions.length || check.unsupported_positions || check.transfer_review_count;
  }

  function renderReadiness(data) {
    const panel = get("reconciliation");
    const scope = account.value + ":" + strategy.value;
    const existing = panel.dataset.scope === scope ? [...panel.querySelectorAll("details")] : [];
    const openState = new Map(existing.map(item => [item.dataset.account, item.open]));
    panel.dataset.scope = scope;
    panel.replaceChildren();
    const brokerHistory = data.reconciliation || [];
    get("readiness-status").textContent = data.history.ok ? "Verified history through " + data.history.end
      : (data.history.reason || "Select an account below to see what its ledger needs.");
    const firstBlocked = brokerHistory.find(needsHistory);
    for (const check of brokerHistory) {
      const details = document.createElement("details");
      details.dataset.account = check.portfolio_id;
      details.toggleAttribute("data-needs-review", Boolean(needsHistory(check)));
      details.open = openState.get(details.dataset.account) ?? (!data.history.ok && check === firstBlocked);
      const title = document.createElement("summary");
      const lines = [];
      if (!check.has_transactions) lines.push("Import this account’s transaction history into its ledger.");
      else {
        if (!check.cash_matches && finite(check.cash_difference)) {
          lines.push("Cash: Schwab " + money(check.broker_cash) + " · Ledger " + money(check.ledger_cash)
            + " · Schwab minus ledger " + money(check.cash_difference));
          lines.push(check.ledger_start
            ? "The ledger starts " + check.ledger_start + ". Check the prior statement for opening cash or missing activity."
            : "Check the opening cash on the statement and import any missing cash activity.");
        }
        for (const row of check.positions) {
          lines.push(row.ticker + " shares: Schwab " + shareQuantity.format(row.broker_shares)
            + " · Ledger " + shareQuantity.format(row.ledger_shares));
        }
      }
      if (!finite(check.broker_cash)) lines.push("Schwab cash is unavailable for reconciliation.");
      if (check.unsupported_positions) lines.push("The ledger cannot value one or more positions in this account.");
      if (!check.live) lines.push("Sync Schwab to check these balances against a fresh snapshot.");
      if (check.has_transactions && !check.history_confirmed) {
        lines.push("Verify full transaction coverage, then confirm it in Account settings.");
      }
      if (check.transfer_review_count) {
        lines.push("Review transfer classifications before calculating historical returns.");
      }
      title.textContent = check.account + " · " + (!check.has_transactions ? "Import history" : (lines.length ? "Review ledger" : "Balances matched"));
      details.append(title);
      if (lines.length) {
        const list = document.createElement("ul");
        replaceList(list, lines);
        details.append(list);
      }
      const ledger = document.createElement("a");
      const destination = new URL(panel.dataset.ledger, location.origin);
      destination.searchParams.set("p", check.portfolio_id);
      ledger.href = destination.href;
      ledger.textContent = "Open this account’s ledger";
      const importLink = document.createElement("a");
      const importDestination = new URL(location.href);
      importDestination.searchParams.set("p", check.portfolio_id);
      importDestination.hash = "pc-history-import";
      importLink.href = importDestination.href;
      importLink.textContent = "Import history for this account";
      importLink.addEventListener("click", () => {
        if (String(check.portfolio_id) === account.value) {
          root.dispatchEvent(new CustomEvent("performance:import-account", {detail: check.portfolio_id}));
        }
      });
      const actions = document.createElement("div");
      actions.className = "pc-account-actions";
      actions.append(importLink, ledger);
      if (check.transfer_review_count) {
        const review = document.createElement("a");
        destination.searchParams.set("review", "transfers");
        destination.hash = "review-transfers";
        review.href = destination.href;
        review.textContent = "Review transfers";
        actions.append(review);
      }
      details.append(actions);
      panel.append(details);
    }
  }

  function renderPortfolio(data) {
    renderOpenPositions(data);
    const p = data.position;
    const recorded = p.recorded_activity_ytd;
    const options = p.recorded_options_ytd;
    get("recorded-options").hidden = !options;
    field("option-count", number(options?.option_count));
    field("option-realized", money(options?.option_realized), options?.option_realized);
    field("option-fees", money(options?.option_fees));
    get("recorded-activity").hidden = !recorded || data.history.ok;
    for (const key of ["deposits", "withdrawals", "dividends", "interest", "fees"]) {
      field("recorded-" + key, money(recorded?.[key]));
    }
    const partial = {
      dividends_ytd: recorded?.dividends || null,
      fees_ytd: recorded?.fees || null,
      realized: options?.option_realized || null
    };
    for (const key of ["value", "unrealized", "realized", "dividends_ytd", "fees_ytd", "investment_profit",
      "basis", "cash", "available_cash", "deposits", "withdrawals", "dividends", "interest", "fees"]) {
      const amount = p[key] ?? partial[key];
      const value = key === "fees_ytd" && finite(amount) ? -amount : amount;
      const tone = ["unrealized", "realized", "fees_ytd", "investment_profit"].includes(key) ? value : null;
      field(key, money(value), tone);
    }
    field("unrealized-pct", pct(p.unrealized_pct), p.unrealized_pct);
    field("quote-summary", data.sync.live ? "Schwab balances" : (data.sync.stored ? "Schwab snapshot" : ""));
    field("realized-period", finite(p.realized) ? "Recorded history" : partial.realized ? "Matched options only (YTD)" : "");
    field("fee-period", finite(p.fees_ytd) ? "Trading and account fees" : recorded?.fees ? "Partial ledger total" : "");
    field("interest-sub", !finite(p.dividends_ytd) && recorded?.dividends ? "Partial ledger total"
      : finite(p.interest) ? money(p.interest) + " interest in recorded history" : "");

    const holdings = p.holdings.filter(row => finite(row.return_pct)).sort((a, b) => b.return_pct - a.return_pct);
    for (const [key, value] of [["best", holdings[0]], ["worst", holdings.at(-1)]]) {
      field(key, value ? value.ticker + " " + pct(value.return_pct) : dash, value?.return_pct);
    }
    field("largest", percentUnsigned(p.concentration?.largest_pct));
    field("top5", percentUnsigned(p.concentration?.top5_pct));
    field("effective", number(p.concentration?.effective_holdings));

    const allocation = [];
    for (const [key, label] of Object.entries(strategyNames)) {
      const s = data.strategies[key] || {};
      field(key + "-return_pct", pct(s.return_pct), s.return_pct);
      for (const fieldName of ["value", "unrealized", "realized", "dividends_ytd"]) {
        field(key + "-" + fieldName, money(s[fieldName]), fieldName === "value" ? null : s[fieldName]);
      }
      if (finite(s.value) && finite(s.cash)) allocation.push([label, s.value - s.cash, s.return_pct]);
    }
    allocation.push(["Cash & Sweep", p.cash, null]);
    const list = get("allocation");
    list.replaceChildren();
    const bands = [];
    let position = 0;
    for (const [label, value, returns] of allocation) {
      const li = document.createElement("li");
      const labelElement = document.createElement("span");
      const swatch = document.createElement("i");
      const shade = label === "Cash & Sweep" ? allocationColors[3] : allocationColors[Object.values(strategyNames).indexOf(label)];
      swatch.style.backgroundColor = shade;
      labelElement.append(swatch, label);
      const amount = document.createElement("strong");
      amount.textContent = money(value);
      const change = document.createElement("span");
      change.textContent = pct(returns);
      color(change, returns);
      li.append(labelElement, amount, change);
      list.append(li);
      if (p.value > 0 && value >= 0) {
        const next = Math.min(100, position + value / p.value * 100);
        if (next > position) bands.push(shade + " " + position + "% " + next + "%");
        position = next;
      }
    }
    const validAllocation = allocation.every(([, value]) => finite(value) && value >= 0)
      && Math.abs(allocation.reduce((sum, [, value]) => sum + value, 0) - p.value) < .05;
    get("donut").style.background = bands.length && validAllocation
      ? "conic-gradient(" + [...bands, "#1e3e56 " + position + "% 100%"].join(",") + ")" : "";
  }

  function percentUnsigned(value) {
    return finite(value) ? number(value) + "%" : dash;
  }

  function renderReturns(history) {
    const periods = get("periods");
    periods.replaceChildren();
    const periodsToShow = history.periods || response.reference?.periods;
    const data = periodsToShow?.length ? periodsToShow
      : ["Today", "1 Week", "1 Month", "3 Months", "YTD", "1 Year", "All-Time"].map(label => ({label}));
    for (const p of data) {
      const label = p.label === "Today" && p.end && p.end !== response.position.as_of ? "Latest session" : p.label;
      const row = tableRow(periods, label, [[pct(p.portfolio), p.portfolio], [pct(p.benchmark)], [pp(p.excess), p.excess]]);
      if (p.start) row.title = p.start + " to " + p.end;
    }
    const r = history.risk || {};
    for (const [key, value] of Object.entries({twr: history.twr_pct, xirr: history.mwr_annual_pct,
      annual: history.twr_annual_pct, drawdown: r.max_drawdown?.pct, volatility: r.volatility_pct, alpha: r.alpha_pct})) {
      field(key, key === "volatility" ? percentUnsigned(value) : pct(value), key === "volatility" ? null : value);
    }
    for (const key of ["sharpe", "sortino", "calmar", "beta"]) field(key, number(r[key]));
    get("risk-coverage").hidden = !history.ok || [r.sharpe, r.sortino, r.calmar, r.beta].every(finite);

    const ytd = data.find(p => p.label === "YTD");
    field("comparison-portfolio", pct(ytd?.portfolio), ytd?.portfolio);
    field("comparison-benchmark", pct(ytd?.benchmark));
    field("comparison-excess", pp(ytd?.excess), ytd?.excess);
    field("comparison-sharpe", number(r.aligned_portfolio?.sharpe) + " / " + number(r.benchmark?.sharpe));

    const heat = get("heatmap");
    heat.replaceChildren();
    const years = Object.entries(history.heatmap || {}).reverse();
    if (!years.length) emptyTable(heat, 14, history.ok ? "No monthly returns available." : "Monthly portfolio returns are pending.");
    for (const [year, monthly] of years) {
      const values = Array.from({length: 12}, (_, index) => monthly[index + 1]).concat(history.years[year]);
      const row = tableRow(heat, year, values.map(value => [finite(value) ? signed(value, "%").replace(/(\.\d)\d%$/, "$1%") : dash]));
      values.forEach((value, index) => {
        const cell = row.cells[index + 1];
        cell.dataset.tone = !finite(value) || value === 0 ? "neutral" : (value > 0 ? "up" : "down");
        if (finite(value)) {
          cell.style.setProperty("--intensity", String(Math.min(.9, .28 + Math.abs(value) / 20)));
          cell.title = pct(value);
        }
      });
    }

    const attr = get("attribution");
    attr.replaceChildren();
    let rows = history.attribution || [];
    if (!rows.length) emptyTable(attr, 4, history.ok ? "No attribution available." : "Attribution needs verified portfolio returns.");
    if (rows.length > 6) rows = [...rows.slice(0, 4), ...rows.slice(-2)];
    const scale = Math.max(1, ...rows.map(row => Math.abs(row.contribution_pct)));
    for (const row of rows) {
      const tr = tableRow(attr, row.ticker, [[pp(row.contribution_pct), row.contribution_pct],
        [money(row.profit), row.profit], [percentUnsigned(row.weight_pct)]]);
      const bar = document.createElement("span");
      bar.className = "pc-attribution-bar" + (row.contribution_pct < 0 ? " pc-negative" : "");
      bar.setAttribute("aria-hidden", "true");
      const fill = document.createElement("span");
      fill.style.width = Math.abs(row.contribution_pct) / scale * 100 + "%";
      bar.append(fill);
      tr.cells[1].prepend(bar);
    }
    chartRange.options[1].disabled = !history.ok;
    if (!history.ok) chartRange.value = "ytd";
    chartRange.disabled = !history.ok && !response.reference?.chart;
    chartRange.options[0].textContent = history.partial_ytd ? "Available this year" : "YTD";
    chartRange.options[1].textContent = history.option_history_trimmed ? "Available period" : "All time";
    get("option-coverage").hidden = !history.option_history_trimmed;
    get("option-coverage").textContent = history.option_history_trimmed
      ? "Returns start on " + history.start + ", after the last option position without daily prices closed." : "";
    const reference = response.reference?.chart;
    get("chart-empty").hidden = Boolean(history.ok || reference);
    get("chart-message").textContent = history.reason || "Historical values unavailable";
    get("chart-blocker").hidden = history.ok || !reference;
    get("chart-blocker-text").textContent = history.ok ? ""
      : (reference?.benchmark.length ? "S&P 500 and target shown. " : "Annual target shown. S&P 500 history is unavailable. ")
        + "Portfolio returns are pending. ";
    for (const link of root.querySelectorAll("[data-history-link]")) link.hidden = Boolean(history.ok);
    get("recorded-link").hidden = Boolean(history.ok) || !response.position.recorded_activity_ytd;
    setPlot();
  }

  function setProgress(progress, value) {
    progress.hidden = !finite(value);
    if (finite(value)) progress.value = Math.max(0, Math.min(100, value));
  }

  const planYear = get("plan-year");
  const planEntries = plan => Array.isArray(plan) ? plan : plan ? [plan] : [];
  const cadenceLabels = {weekly: " every week", biweekly: " every other week", monthly: " every month", once: " one time"};
  let selectedPlanYear = Number(root.dataset.planYear) || undefined;
  let selectedFundingMonth;
  const fundingSeries = [["planned", "Scheduled"], ["actual", "Verified net"], ["target", "Even goal pace"]];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const compactMoney = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD", notation: "compact"});

  function renderFundingChart(goal) {
    const data = goal.contribution_chart;
    const values = data.flatMap(row => fundingSeries.map(([key]) => row[key])).filter(finite);
    const low = Math.min(0, ...values);
    const high = Math.max(1, ...values);
    const scale = value => 100 * (value - low) / (high - low);
    const chart = get("funding-months");
    chart.replaceChildren();
    chart.style.setProperty("--funding-zero", scale(0) + "%");
    get("funding-scale").replaceChildren(...[high, (high + low) / 2, low].map(value => {
      const tick = document.createElement("span");
      tick.textContent = compactMoney.format(value);
      return tick;
    }));
    for (const [key] of fundingSeries) {
      root.querySelector('[data-funding-series="' + key + '"]').hidden = !data.some(row => finite(row[key]));
    }
    const activate = (row, item) => {
      selectedFundingMonth = row.month;
      chart.querySelector('[aria-pressed="true"]')?.setAttribute("aria-pressed", "false");
      item.setAttribute("aria-pressed", "true");
      const values = fundingSeries.filter(([key]) => finite(row[key]))
        .map(([key, label]) => label + " " + money(row[key]));
      get("funding-readout").textContent = months[row.month - 1] + " " + goal.year + " · " + values.join(" · ");
    };
    for (const row of data) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "pc-funding-month";
      item.setAttribute("aria-pressed", "false");
      item.setAttribute("aria-label", months[row.month - 1] + " " + goal.year);
      const tracks = document.createElement("span");
      tracks.className = "pc-funding-tracks";
      tracks.setAttribute("aria-hidden", "true");
      for (const [key] of fundingSeries) {
        const value = row[key];
        if (!finite(value)) continue;
        const bar = document.createElement("span");
        bar.className = "pc-funding-bar pc-funding-" + key;
        const point = scale(value);
        bar.style.bottom = (key === "target" ? point : Math.min(point, scale(0))) + "%";
        if (key !== "target") bar.style.height = Math.abs(point - scale(0)) + "%";
        tracks.append(bar);
      }
      const label = document.createElement("span");
      label.className = "pc-funding-month-label";
      label.textContent = months[row.month - 1];
      item.append(tracks, label);
      item.addEventListener("click", () => activate(row, item));
      chart.append(item);
      if (row.month === (selectedFundingMonth || 12)) activate(row, item);
    }
  }

  function renderContributionPlanner(data) {
    const options = data.goals.filter(row => row.year >= data.selected_goal.year || row.contribution_plan);
    if (!options.some(row => row.year === selectedPlanYear)) {
      selectedPlanYear = options.find(row => row.contribution_plan)?.year ?? data.selected_goal.year;
    }
    planYear.replaceChildren(...options.map(row => new Option(row.year, row.year)));
    planYear.value = selectedPlanYear;
    planYear.disabled = false;
    root.querySelector('[data-action="edit-plan"]').disabled = false;
    const goal = options.find(row => row.year === selectedPlanYear);
    const entries = planEntries(goal.contribution_plan);
    const projected = goal.projection;
    const globalView = account.value === "all" && strategy.value === "all";
    get("plan-actual").textContent = goal.planned ? "Year hasn’t started" : money(globalView ? goal.net : null);
    get("plan-remaining").textContent = money(projected?.remaining);
    get("plan-projected").textContent = money(projected?.year_end);
    get("plan-target").textContent = money(goal.contribution);
    const scheduleLabel = entries.length === 1 ? money(entries[0].amount) + cadenceLabels[entries[0].cadence]
      : entries.length + " contribution schedules";
    get("plan-summary").textContent = entries.length
      ? scheduleLabel + " · " + (projected.next_date ? "Next planned date " + projected.next_date : "Schedule complete")
      : "Add weekly, every-other-week, monthly, or one-time contributions.";
    const messages = [];
    if (finite(projected?.gap)) {
      messages.push(projected.gap === 0 ? "Projection matches the goal."
        : money(Math.abs(projected.gap)) + (projected.gap > 0 ? " above goal." : " short of goal."));
    } else if (projected) {
      messages.push(globalView ? "Year-end estimate needs verified contributions to date."
        : "Select All accounts and All strategies to include verified contributions.");
    }
    if (projected) messages.push(projected.remaining_count + " planned contributions after today.");
    if (entries.length) messages.push("Planning estimate only.");
    get("plan-context").textContent = messages.join(" ");
    renderFundingChart(goal);
    for (const link of root.querySelectorAll("[data-plan-link]")) {
      const destination = new URL(link.href);
      destination.searchParams.set("year", goal.year);
      link.href = destination.href;
    }
  }

  planYear.addEventListener("change", () => {
    selectedPlanYear = Number(planYear.value);
    selectedFundingMonth = null;
    const page = new URL(location.href);
    page.searchParams.set("year", selectedPlanYear);
    history.replaceState(null, "", page);
    renderContributionPlanner(response);
  });

  function renderGoals(data) {
    renderContributionPlanner(data);
    const goal = data.selected_goal;
    field("goal-rate", percentUnsigned(goal.return_pct));
    field("goal-amount", money(goal.contribution));
    field("net", money(goal.net));
    const contribution = finite(goal.net) ? goal.net / goal.contribution * 100 : null;
    field("contribution-sub", "of " + money(goal.contribution));
    field("progress-contribution", percentUnsigned(contribution));
    field("remaining", finite(goal.remaining) ? money(goal.remaining) + " remaining · " + data.days_left + " days left" : "Contribution history unavailable");
    setProgress(get("contribution-progress"), contribution);
    const isGoalYear = data.history.end?.slice(0, 4) === String(goal.year);
    const actual = isGoalYear ? data.history.periods?.find(period => period.label === "YTD")?.portfolio : null;
    const trajectory = isGoalYear ? data.history.growth_chart?.target.at(-1) : null;
    const delta = finite(actual) && finite(trajectory) ? actual - trajectory : null;
    field("progress-return", finite(actual) ? pct(actual) + " / " + percentUnsigned(goal.return_pct) : dash, actual);
    field("target-gap", finite(delta) ? pp(delta) + " pp vs. trajectory" : "Return history unavailable", delta);
    const returnProgress = finite(actual)
      ? (goal.return_pct > 0 ? actual / goal.return_pct * 100 : (actual >= goal.return_pct ? 100 : 0))
      : null;
    setProgress(get("return-progress"), returnProgress);
    const history = get("goal-history");
    history.replaceChildren();
    for (const year of data.goals) {
      if (year.planned && !year.saved) continue;
      tableRow(history, year.year + (year.planned ? " plan" : ""),
        [[pct(year.actual_return) + " / " + percentUnsigned(year.return_pct)],
          [money(year.net) + " / " + money(year.contribution)]]);
    }
  }

  function renderSources(data) {
    renderReadiness(data);
    const p = data.position;
    const sources = [];
    if (data.history.ok) sources.push("Ledger and Yahoo Finance daily closing prices · " + data.history.start + " to " + data.history.end);
    if (data.benchmark.as_of) sources.push(data.benchmark.label + " (" + data.benchmark.symbol + ") · " + data.benchmark.source + " · " + data.benchmark.as_of);
    else sources.push("S&P 500 total-return series unavailable.");
    for (const item of p.quote_sources) sources.push(item.source + (item.as_of ? " · " + time(item.as_of) : " · timestamp unavailable"));
    for (const asOf of data.sync.as_of) sources.push("Schwab account observation · " + time(asOf));
    replaceList(get("sources"), [...new Set(sources)]);
    replaceList(get("warnings"), [
      ...data.warnings,
      ...(p.unpriced.length ? ["Current price unavailable for " + p.unpriced.join(", ") + "."] : []),
      ...(p.stale.length ? ["Last stored price is stale for " + p.stale.join(", ") + "."] : [])
    ]);
    const coverage = data.history.ok ? "History through " + data.history.end : "History incomplete";
    const sync = data.sync.live ? "Schwab connected" : (data.sync.stored ? "Schwab snapshot" : "No Schwab balance in this view");
    status.textContent = (syncMessage || sync) + " · " + coverage;
    const blocker = data.reconciliation?.find(needsHistory);
    const ledgerAccount = account.value !== "all" ? account.value : blocker?.portfolio_id;
    notice(data.history.ok ? "" : data.history.reason, false, Boolean(ledgerAccount), ledgerAccount);
    for (const button of root.querySelectorAll("[data-assign-strategy]")) {
      const group = button.dataset.assignStrategy;
      button.hidden = !data.accounts.some(item => item.strategy === "unclassified" &&
          (account.value === "all" || String(item.id) === account.value))
        || finite(data.strategies[group]?.value) || (strategy.value !== "all" && strategy.value !== group);
    }
  }

  function refreshOptions(data) {
    const selected = account.value;
    const options = data.accounts.map(p => new Option(p.label || p.name, p.id));
    if (selected !== "all" && !options.some(option => option.value === selected)) {
      const unavailable = new Option("Account unavailable", selected);
      unavailable.disabled = true;
      options.push(unavailable);
    }
    account.replaceChildren(new Option("All accounts", "all"), ...options);
    account.value = selected;
    root.dispatchEvent(new CustomEvent("performance:accounts", {
      detail: {accounts: data.accounts, selected: account.value, today: data.position.as_of}
    }));
    root.querySelector('[data-action="goals"]').disabled = false;
    root.querySelector('[data-action="accounts"]').disabled = !data.accounts.length;
  }

  async function load() {
    inflight?.abort();
    const request = new AbortController();
    inflight = request;
    status.textContent = "Loading portfolio performance…";
    root.setAttribute("aria-busy", "true");
    const params = new URLSearchParams();
    if (account.value !== "all") params.set("p", account.value);
    if (strategy.value !== "all") params.set("strategy", strategy.value);
    const query = params.toString() ? "?" + params : "";
    for (const link of root.querySelectorAll("[data-account-link]")) {
      const destination = new URL(link.href);
      if (account.value === "all") destination.searchParams.delete("p");
      else destination.searchParams.set("p", account.value);
      link.href = destination.href;
    }
    const page = new URL(location.href);
    page.search = query;
    if (selectedPlanYear) page.searchParams.set("year", selectedPlanYear);
    history.replaceState(null, "", page);
    try {
      const r = await fetch(root.dataset.api + query, {signal: request.signal, cache: "no-store"});
      if (!r.ok) throw new Error("Performance data is temporarily unavailable.");
      response = await r.json();
      renderPortfolio(response);
      renderReturns(response.history);
      renderGoals(response);
      refreshOptions(response);
      renderSources(response);
    } catch (error) {
      if (error.name === "AbortError") return;
      status.textContent = error.message;
      notice(error.message, true, false);
    } finally {
      if (inflight === request) root.removeAttribute("aria-busy");
    }
  }

  function setPlot() {
    const history = response?.history;
    const source = history?.chart;
    const path = chartRange.value === "all" && source ? {
      days: source.days,
      portfolio: source.growth.map(value => (value - 1) * 100),
      benchmark: (source.spy || []).map(value => finite(value) ? (value - 1) * 100 : null)
    } : history?.growth_chart || response?.reference?.chart;
    const lines = path ? seriesInfo.filter(line => path[line.key]?.some(finite)) : [];
    root.querySelectorAll(".pc-legend li").forEach((item, index) =>
      item.classList.toggle("is-missing", !lines.includes(seriesInfo[index])));
    selectedDay = null;
    plot = path ? {path, lines} : null;
    drawPlot();
  }

  function drawPlot() {
    const width = chart.clientWidth;
    const height = chart.clientHeight;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    chart.width = width * ratio;
    chart.height = height * ratio;
    const ctx = chart.getContext("2d");
    if (!ctx || !plot || width < 40 || height < 40) {
      get("chart-readout").textContent = "";
      return;
    }
    const {path, lines} = plot;
    const last = path.days.length - 1;
    const values = lines.flatMap(line => path[line.key].filter(finite));
    const min = Math.min(0, ...values), max = Math.max(0, ...values);
    const span = max - min || 1;
    const lo = min - span * .12, hi = max + span * .14;
    const left = 49, right = width - 14, top = 9, bottom = height - 21;
    const stamps = path.days.map(day => Date.parse(day));
    const timeSpan = stamps[last] - stamps[0];
    const x = index => left + (right - left) * (timeSpan ? (stamps[index] - stamps[0]) / timeSpan : 0);
    const y = value => bottom - (bottom - top) * (value - lo) / (hi - lo);
    plot.x = x;

    ctx.scale(ratio, ratio);
    ctx.lineWidth = 1;
    ctx.font = "11px IBM Plex Sans, sans-serif";
    ctx.fillStyle = "#afcbe1";
    for (let i = 0; i <= 4; i++) {
      const value = lo + (hi - lo) * i / 4;
      const py = y(value);
      ctx.beginPath();
      ctx.strokeStyle = "#1f3b50";
      ctx.moveTo(left, py);
      ctx.lineTo(right, py);
      ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(number(value, span < 7 ? 1 : 0) + "%", left - 5, py + 4);
    }
    const ticks = Math.min(last, width < 420 ? 3 : 6);
    for (let i = 0; i <= ticks; i++) {
      const index = Math.round(last * i / Math.max(ticks, 1));
      const px = x(index);
      ctx.beginPath();
      ctx.moveTo(px, top);
      ctx.lineTo(px, bottom);
      ctx.stroke();
      ctx.textAlign = i === 0 ? "left" : (i === ticks ? "right" : "center");
      const day = path.days[index];
      const stamp = new Date(day + "T12:00:00Z");
      ctx.fillText(new Intl.DateTimeFormat("en-US", {month: "short",
        ...(timeSpan > 31536000000 ? {year: "2-digit"} : {day: "numeric"}), timeZone: "UTC"}).format(stamp), px, height - 6);
    }

    const portfolioValues = path.portfolio;
    if (portfolioValues?.every(finite) && last > 0) {
      const fill = ctx.createLinearGradient(0, top, 0, bottom);
      fill.addColorStop(0, "#19fa9866");
      fill.addColorStop(1, "#09a6e303");
      ctx.beginPath();
      ctx.moveTo(x(0), bottom);
      portfolioValues.forEach((value, index) => ctx.lineTo(x(index), y(value)));
      ctx.lineTo(x(last), bottom);
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
    }
    for (const line of lines) {
      ctx.lineWidth = line.dash ? 1.5 : 2;
      ctx.strokeStyle = line.color;
      ctx.setLineDash(line.dash || []);
      ctx.beginPath();
      let started = false;
      path[line.key].forEach((value, index) => {
        if (!finite(value)) {
          started = false;
          return;
        }
        if (started) ctx.lineTo(x(index), y(value));
        else ctx.moveTo(x(index), y(value));
        started = true;
      });
      ctx.stroke();
    }
    ctx.setLineDash([]);
    const i = selectedDay ?? last;
    if (selectedDay !== null) {
      ctx.strokeStyle = "#b2d7f4";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x(i), top);
      ctx.lineTo(x(i), bottom);
      ctx.stroke();
    }
    const valuesAtDate = lines.map(line => line.label + " " + pct(path[line.key][i]));
    const text = [path.days[i], ...valuesAtDate].join("  ·  ");
    if (get("chart-readout").textContent !== text) get("chart-readout").textContent = text;
  }

  function requestPlot() {
    if (chartFrame) return;
    chartFrame = requestAnimationFrame(() => {
      chartFrame = 0;
      drawPlot();
    });
  }

  chart.addEventListener("pointermove", event => {
    if (!plot?.x) return;
    const pos = event.clientX - chart.getBoundingClientRect().left;
    let lo = 0, hi = plot.path.days.length - 1;
    while (hi - lo > 1) {
      const mid = Math.floor((lo + hi) / 2);
      if (plot.x(mid) <= pos) lo = mid;
      else hi = mid;
    }
    selectedDay = Math.abs(plot.x(lo) - pos) < Math.abs(plot.x(hi) - pos) ? lo : hi;
    requestPlot();
  });
  chart.addEventListener("pointerleave", () => { selectedDay = null; requestPlot(); });
  chart.addEventListener("keydown", event => {
    if (!plot || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const last = plot.path.days.length - 1;
    const i = selectedDay ?? last;
    selectedDay = {Home: 0, End: last, ArrowLeft: Math.max(i - 1, 0), ArrowRight: Math.min(i + 1, last)}[event.key];
    requestPlot();
  });
  chartRange.addEventListener("change", setPlot);
  root.querySelectorAll('a[href="#pc-history-readiness"]').forEach(link => {
    link.addEventListener("click", () => {
      const blocker = get("reconciliation").querySelector("[data-needs-review]");
      if (blocker) blocker.open = true;
      get("history-readiness").focus({preventScroll: true});
    });
  });
  new ResizeObserver(requestPlot).observe(chart);

  const goalForm = get("goals-form");
  const accountForm = get("accounts-form");
  const goalYear = goalForm.elements.year;
  const settingsAccount = accountForm.elements.account;

  let previewTimer;
  let previewRequest;

  const scheduleInputs = get("schedule-inputs");
  const addSchedule = get("add-schedule");

  function scheduleFields(entry) {
    return Object.fromEntries([...entry.querySelectorAll("[data-plan]")].map(field => [field.dataset.plan, field]));
  }

  function addPlanEntry(plan = {}, focus = false) {
    const year = Number(goalYear.value);
    const entry = get("schedule-template").content.firstElementChild.cloneNode(true);
    const fields = scheduleFields(entry);
    fields.cadence.value = plan.cadence || "weekly";
    fields.amount.value = plan.amount ?? "";
    for (const name of ["start_date", "end_date"]) {
      fields[name].min = year + "-01-01";
      fields[name].max = year + "-12-31";
    }
    const today = response.position.as_of;
    const tomorrow = new Date(Date.parse(today + "T00:00:00Z") + 86400000).toISOString().slice(0, 10);
    fields.start_date.value = plan.start_date || (year === response.selected_goal.year
      ? (tomorrow <= fields.start_date.max ? tomorrow : today) : fields.start_date.min);
    fields.end_date.value = plan.end_date || "";
    scheduleInputs.append(entry);
    if (focus) fields.cadence.focus();
  }

  function planDraft() {
    return [...scheduleInputs.children].map(entry => {
      const fields = scheduleFields(entry);
      return {cadence: fields.cadence.value, amount: fields.amount.valueAsNumber,
        start_date: fields.start_date.value, end_date: fields.end_date.disabled ? "" : fields.end_date.value};
    });
  }

  function refreshScheduleControls() {
    addSchedule.disabled = scheduleInputs.children.length >= 30;
    [...scheduleInputs.children].forEach((entry, index) => {
      entry.querySelector("legend").textContent = "Contribution " + (index + 1);
      const {cadence, start_date, end_date} = scheduleFields(entry);
      end_date.disabled = cadence.value === "once";
      end_date.closest("label").hidden = end_date.disabled;
      end_date.min = start_date.value || start_date.min;
    });
  }

  function renderGoalPlan() {
    clearTimeout(previewTimer);
    previewRequest?.abort();
    const amount = goalForm.elements.contribution.valueAsNumber;
    const year = Number(goalYear.value);
    const note = get("goal-plan");
    refreshScheduleControls();
    const plan = planDraft();
    note.hidden = !finite(amount) || amount <= 0;
    if (note.hidden) return;
    if (!plan.length) {
      note.textContent = money(amount / 12) + " per month for an even " + year + " contribution plan.";
      return;
    }
    if ([...scheduleInputs.querySelectorAll("input")].some(input => !input.validity.valid)) {
      note.textContent = "Set a valid amount and dates for each contribution to see the estimate.";
      return;
    }
    note.textContent = "Calculating estimate…";
    previewTimer = setTimeout(async () => {
      const request = new AbortController();
      previewRequest = request;
      try {
        const r = await fetch(root.dataset.api + "/goals/" + year + "/preview", {
          method: "POST", headers: {"Content-Type": "application/json"}, signal: request.signal,
          body: JSON.stringify({contribution: amount, contribution_plan: plan})
        });
        const data = await r.json();
        if (request.signal.aborted) return;
        if (!r.ok) throw new Error(data.error || "Estimate unavailable.");
        const schedule = data.projection;
        note.textContent = schedule.count + " contributions total " + money(schedule.total)
          + " in " + year + " · " + money(schedule.remaining) + " planned after today.";
      } catch (error) {
        if (error.name !== "AbortError" && !request.signal.aborted) note.textContent = error.message;
      }
    }, 250);
  }

  function showGoal() {
    const goal = response.goals.find(row => row.year === Number(goalYear.value));
    goalForm.elements.return_pct.value = goal.return_pct;
    goalForm.elements.contribution.value = goal.contribution;
    scheduleInputs.replaceChildren();
    for (const plan of planEntries(goal.contribution_plan)) addPlanEntry(plan);
    renderGoalPlan();
  }

  const settingsAccountKey = "tradestaar.performance.settings-account";
  const saveAccountButton = accountForm.querySelector('[type="submit"]');
  let lastSettingsAccount, lastSettingsView;
  try {
    [lastSettingsAccount, lastSettingsView] = (sessionStorage.getItem(settingsAccountKey) || "").split(":");
  } catch {}
  let settingsStrategy;

  function showAccount() {
    const a = response.accounts.find(row => row.id === Number(settingsAccount.value));
    accountForm.elements.strategy.value = a ? (settingsStrategy || a.strategy) : "unclassified";
    accountForm.elements.history_confirmed.checked = Boolean(a?.history_confirmed);
    accountForm.elements.strategy.disabled = !a;
    accountForm.elements.history_confirmed.disabled = !a;
    saveAccountButton.disabled = !a;
  }

  goalYear.addEventListener("change", showGoal);
  goalForm.elements.contribution.addEventListener("input", renderGoalPlan);
  scheduleInputs.addEventListener("input", renderGoalPlan);
  addSchedule.addEventListener("click", () => {
    addPlanEntry({}, true);
    renderGoalPlan();
  });
  scheduleInputs.addEventListener("click", event => {
    if (!event.target.closest("[data-remove-schedule]")) return;
    event.target.closest(".pc-schedule-entry").remove();
    renderGoalPlan();
    addSchedule.focus();
  });
  get("goals-dialog").addEventListener("close", () => {
    clearTimeout(previewTimer);
    previewRequest?.abort();
  });
  settingsAccount.addEventListener("change", showAccount);
  root.querySelectorAll("[data-close]").forEach(button =>
    button.addEventListener("click", () => button.closest("dialog").close()));

  function openGoalDialog(year) {
    goalYear.replaceChildren(...response.goals.map(goal => new Option(goal.year + (goal.planned ? " · Plan ahead" : ""), goal.year)));
    goalYear.value = year;
    showGoal();
    goalForm.querySelector('[role="status"]').textContent = "";
    get("goals-dialog").showModal();
  }
  root.querySelector('[data-action="goals"]').addEventListener("click", () => openGoalDialog(response.selected_goal.year));
  root.querySelector('[data-action="edit-plan"]').addEventListener("click", () => openGoalDialog(selectedPlanYear));
  function openAccountSettings(preferredStrategy) {
    settingsStrategy = preferredStrategy;
    const placeholder = new Option("Select an account", "");
    placeholder.disabled = true;
    settingsAccount.replaceChildren(placeholder, ...response.accounts.map(a => new Option(a.label || a.name, a.id)));
    const preferSaved = account.value === "all" || account.value === lastSettingsView;
    const selected = (preferSaved ? [lastSettingsAccount, account.value] : [account.value]).find(id =>
      response.accounts.some(a => String(a.id) === id));
    settingsAccount.value = selected ?? (response.accounts.length === 1 ? response.accounts[0].id : "");
    showAccount();
    accountForm.querySelector('[role="status"]').textContent = "";
    get("accounts-dialog").showModal();
  }
  root.querySelector('[data-action="accounts"]').addEventListener("click", () => openAccountSettings());
  for (const button of root.querySelectorAll("[data-assign-strategy]")) {
    button.addEventListener("click", () => openAccountSettings(button.dataset.assignStrategy));
  }

  async function save(form, path, body, onSaved) {
    const message = form.querySelector('[role="status"]');
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    message.textContent = "Saving…";
    try {
      const r = await fetch(root.dataset.api + path, {method: "PUT",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      if (!r.ok) {
        const result = await r.json().catch(() => ({}));
        throw new Error(result.error || "Settings could not be saved.");
      }
      onSaved?.();
      form.closest("dialog").close();
      syncMessage = "";
      await load();
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  goalForm.addEventListener("submit", event => {
    event.preventDefault();
    selectedPlanYear = Number(goalYear.value);
    save(goalForm, "/goals/" + goalYear.value, {
      return_pct: goalForm.elements.return_pct.valueAsNumber,
      contribution: goalForm.elements.contribution.valueAsNumber,
      contribution_plan: planDraft()
    });
  });
  accountForm.addEventListener("submit", event => {
    event.preventDefault();
    const savedId = settingsAccount.value;
    const savedView = account.value;
    if (!savedId) return;
    save(accountForm, "/accounts/" + savedId, {
      strategy: accountForm.elements.strategy.value,
      history_confirmed: accountForm.elements.history_confirmed.checked
    }, () => {
      lastSettingsAccount = savedId;
      lastSettingsView = savedView;
      try { sessionStorage.setItem(settingsAccountKey, savedId + ":" + savedView); } catch {}
    });
  });
  root.querySelector('[data-action="sync"]').addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    status.textContent = "Synchronizing Schwab balances…";
    try {
      const r = await fetch(root.dataset.sync, {method: "POST", cache: "no-store"});
      const data = await r.json();
      if (!r.ok || !data.ok) throw new Error("Schwab sync unavailable. Check your connection.");
      syncMessage = data.synced
        ? "Synced " + data.synced + " Schwab " + (data.synced === 1 ? "account" : "accounts")
        : "No Schwab accounts included in Wealth";
      await load();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  for (const filter of [account, strategy]) filter.addEventListener("change", () => { syncMessage = ""; load(); });
  let balancePending = false;
  async function refreshBalance() {
    if (document.hidden || !response || balancePending || root.hasAttribute("aria-busy")) return;
    const selection = response;
    const query = new URLSearchParams(location.search);
    query.set("view", "balance");
    balancePending = true;
    try {
      const refreshed = await fetch(root.dataset.api + "?" + query, {cache: "no-store"});
      if (!refreshed.ok) return;
      const update = await refreshed.json();
      if (response !== selection || root.hasAttribute("aria-busy")) return;
      Object.assign(response.position, update.position);
      const p = response.position;
      p.investment_profit = finite(p.value) && finite(p.capital_flows_to_date)
        ? p.value - p.capital_flows_to_date : null;
      response.sync = update.sync;
      response.reconciliation = update.reconciliation;
      for (const [name, balances] of Object.entries(update.strategies)) {
        Object.assign(response.strategies[name] || {}, balances);
      }
      renderPortfolio(response);
      renderSources(response);
    } catch {
      status.textContent += " · Latest balance refresh unavailable";
    } finally {
      balancePending = false;
    }
  }

  root.querySelector('[data-action="retry"]').addEventListener("click", load);
  get("recorded-link").addEventListener("click", () => { get("recorded-activity").open = true; });
  root.addEventListener("performance:ledger-updated", () => {
    syncMessage = "";
    load();
  });
  setInterval(refreshBalance, 60000);
  document.addEventListener("visibilitychange", refreshBalance);
  load();
})();
