/* Terminal chart tools: drawings, price-scale labels and overlay studies.
 *
 * Works on top of the Terminal chart in templates/terminal.html and uses its
 * globals (twChartState, twCanvasContext, twEasternTimeLabel, ...). Drawings are
 * kept per ticker, so a line drawn on the 15m shows on the 1H, 4H and 1D too.
 * They are saved on this device right away and synced to the account, so the
 * phone and the desktop show the same markups.
 */

/* ═══ Tool catalogue ═══ */
var CT_TOOLS = {
  trend:    {n: 2, label: 'Trend line'},
  ray:      {n: 2, label: 'Ray', defaults: {extendRight: true}},
  extended: {n: 2, label: 'Extended line', defaults: {extendLeft: true, extendRight: true}},
  arrow:    {n: 2, label: 'Arrow'},
  hline:    {n: 1, label: 'Horizontal line', defaults: {color: '#38d6c5', showPrice: true}},
  hray:     {n: 1, label: 'Horizontal ray', defaults: {color: '#38d6c5', showPrice: true}},
  vline:    {n: 1, label: 'Vertical line', defaults: {color: '#8d9bb0'}},
  channel:  {n: 3, label: 'Parallel channel', defaults: {color: '#6fa8ff', fillOpacity: 0.1}},
  fib:      {n: 2, label: 'Fib retracement', defaults: {color: '#f0b53f', width: 1, fillOpacity: 0.08}},
  fibext:   {n: 3, label: 'Trend-based fib extension', defaults: {color: '#f0b53f', width: 1, fillOpacity: 0.06}},
  rect:     {n: 2, label: 'Rectangle', defaults: {color: '#6fa8ff', fillOpacity: 0.14}},
  text:     {n: 1, label: 'Text', defaults: {color: '#e8edf5', text: 'Note', fontSize: 13}},
  long:     {n: 2, label: 'Long position', defaults: {showPrice: true}},
  short:    {n: 2, label: 'Short position', defaults: {showPrice: true}},
  measure:  {n: 2, label: 'Measure % (price range)'},
  pctlevels: {n: 1, label: '% drop levels', defaults: {color: '#9aa7b8', width: 1, dash: 'dashed', showPrice: true, fillOpacity: 0, extendLeft: true}},
  avwap:    {n: 1, label: 'Anchored VWAP', defaults: {color: '#ff9f43', showPrice: true}}
};
var CT_GROUPS = [
  {id: 'lines', tools: ['trend', 'ray', 'extended', 'arrow', 'hline', 'hray', 'vline', 'channel']},
  {id: 'fib', tools: ['fib', 'fibext']},
  {id: 'shapes', tools: ['rect', 'text']},
  {id: 'forecast', tools: ['long', 'short']},
  {id: 'measure', tools: ['measure', 'pctlevels']},
  {id: 'avwap', tools: ['avwap']}
];
var CT_ICONS = {
  select: '<path d="M5 3.5l13 7.2-5.6 1.4-2.6 5.4z"/>',
  trend: '<path d="M5 19 19 5"/><circle cx="5" cy="19" r="1.8"/><circle cx="19" cy="5" r="1.8"/>',
  ray: '<path d="M5 19 21 3"/><circle cx="5" cy="19" r="1.8"/><circle cx="13" cy="11" r="1.8"/>',
  extended: '<path d="M2 22 22 2"/><circle cx="8" cy="16" r="1.8"/><circle cx="16" cy="8" r="1.8"/>',
  arrow: '<path d="M5 19 19 5M11 5h8v8"/>',
  hline: '<path d="M2 12h20"/><circle cx="12" cy="12" r="1.8"/>',
  hray: '<path d="M7 12h15"/><circle cx="6" cy="12" r="1.8"/>',
  vline: '<path d="M12 2v20"/><circle cx="12" cy="12" r="1.8"/>',
  channel: '<path d="M3 14 14 3M10 21 21 10"/><path d="M6.5 17.5 17.5 6.5" stroke-dasharray="2 2.5"/>',
  fib: '<path d="M3 5h18M3 9.5h18M3 14h18M3 19h18"/><path d="M6 19 18 5" stroke-dasharray="2 2.5"/>',
  fibext: '<path d="M10 5h11M10 10h11M10 15h11"/><path d="M3 20l4-9 3 5" stroke-dasharray="2 2"/>',
  rect: '<rect x="4" y="6" width="16" height="12" rx="1.5"/>',
  text: '<path d="M5 6h14M12 6v13M9 19h6"/>',
  long: '<path d="M4 4h16v8H4z" fill="rgba(34,197,139,.25)"/><path d="M4 12h16v7H4z" fill="rgba(255,77,69,.22)"/>',
  short: '<path d="M4 5h16v7H4z" fill="rgba(255,77,69,.22)"/><path d="M4 12h16v8H4z" fill="rgba(34,197,139,.25)"/>',
  measure: '<path d="M12 3v18M8 7l4-4 4 4M8 17l4 4 4-4"/><path d="M17 15.5l4-4.5M17.3 11.4h.01M20.7 15.2h.01" stroke-width="1.6"/>',
  avwap: '<path d="M3 18c4-1 5-8 9-8s5 4 9 2"/><circle cx="3.5" cy="18" r="1.8"/>',
  pctlevels: '<path d="M3 4h18"/><path d="M9 10h12M9 15h12M9 20h12" stroke-dasharray="2.5 2"/><circle cx="5" cy="4" r="1.6"/><path d="M4 11.5l2.5-3M4.2 8.6h.01M6.3 11.4h.01" stroke-width="1.4"/>',
  magnet: '<path d="M6 4v8a6 6 0 0 0 12 0V4M6 8h4M14 8h4"/>',
  star: '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9 6.8 19.6l1-5.8-4.3-4.1 5.9-.9z"/>',
  eye: '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.6"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
  redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9a5 5 0 0 0 0 10h3"/>',
  trash: '<path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13"/>',
  clear: '<path d="M6 6l12 12M18 6 6 18"/>',
  reset: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4.5h4.5"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  clone: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
  price: '<path d="M4 12h9"/><path d="M13 8h7v8h-7z"/>',
  bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>'
};
function ctIcon(name, size) {
  return '<svg viewBox="0 0 24 24" width="' + (size || 18) + '" height="' + (size || 18) + '" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + (CT_ICONS[name] || '') + '</svg>';
}

var CT_FIB_LEVELS = [
  {v: 0, on: true, c: '#787b86'}, {v: 0.236, on: true, c: '#f23645'}, {v: 0.382, on: true, c: '#ff9800'},
  {v: 0.5, on: true, c: '#4caf50'}, {v: 0.618, on: true, c: '#089981'}, {v: 0.786, on: true, c: '#00bcd4'},
  {v: 1, on: true, c: '#787b86'}, {v: 1.272, on: false, c: '#2962ff'}, {v: 1.618, on: true, c: '#f23645'},
  {v: 2.618, on: false, c: '#9c27b0'}, {v: -0.272, on: false, c: '#e91e63'}
];
var CT_FIBEXT_LEVELS = [
  {v: 0, on: true, c: '#787b86'}, {v: 0.382, on: false, c: '#ff9800'}, {v: 0.618, on: true, c: '#089981'},
  {v: 1, on: true, c: '#2962ff'}, {v: 1.272, on: true, c: '#00bcd4'}, {v: 1.618, on: true, c: '#f23645'},
  {v: 2, on: false, c: '#9c27b0'}, {v: 2.618, on: true, c: '#e91e63'}
];
// % drop levels: lines this far below the high you click (or above a low with ⇅).
var CT_PCT_LEVELS = [
  {v: 10, on: false, c: '#f3c95a'}, {v: 20, on: true, c: '#ff9f43'}, {v: 30, on: true, c: '#ff6b6b'},
  {v: 40, on: true, c: '#ff4d8d'}, {v: 50, on: true, c: '#c084fc'}, {v: 60, on: true, c: '#38bdf8'},
  {v: 70, on: false, c: '#2ee6a6'}, {v: 80, on: false, c: '#8be36b'}
];
var CT_BASE_STYLE = {color: '#f0b53f', width: 2, dash: 'solid', fillOpacity: 0.12, extendLeft: false, extendRight: false, showPrice: false, text: '', fontSize: 13, labels: true, reverse: false};

/* ═══ Settings (overlays, labels, candle colours) ═══ */
var CT_PREF_DEFAULTS = {
  vwap: {on: true, color: '#ff9f43', bands: false},
  bb: {on: false, len: 20, mult: 2, color: '#8b93ff'},
  sma: {on: false, len: 50, color: '#d9dee7'},
  levels: {on: true, pdh: true, pdl: true, pdc: true, pmh: true, pml: true},
  labels: {bid: true, ask: true, indicators: true, levels: true, drawings: true},
  candles: {up: '#22C58B', down: '#FF4D45', session: false, shading: true, style: 'candles', wicks: true},
  scale: {mode: 'normal', invert: false, lastLine: true, lastLabel: true},
  view: {grid: 'none', crosshair: 'free', watermark: false, hilo: true, rightOffset: 5, countdown: true,
         ohlc: true, legend: true, tooltip: true, hint: true, volume: true, sessions: true, events: true, zones: true},
  draw: {stay: false, hidden: false},
  // Favorite drawing tools: starred in the tool menus, shown on a floating bar
  // you can drag anywhere on the chart (like TradingView's favorites toolbar).
  favtools: {list: ['trend', 'hline', 'hray', 'fib', 'rect', 'measure', 'pctlevels', 'text'], on: true, x: null, y: null},
  // Value zones and events on the chart: each piece on/off with its colour.
  overlays: {rail: true, lines: true, tags: false, axisTags: true, alertLines: false,
             pricey: true, fair: true, accum: true, load: true,
             priceyColor: '#ff6b6b', fairColor: '#f3c95a', accumColor: '#8be36b', loadColor: '#2ee6a6',
             nextEarnings: true, earningsColor: '#f0b53f', earnChip: true, zoneChip: true,
             pastEarnings: true, news: true, newsColor: '#6fa8ff'},
  magnet: false
};
var ctPrefs = JSON.parse(JSON.stringify(CT_PREF_DEFAULTS));
var ctToolDefaults = {};
(function () {
  try {
    var saved = JSON.parse(localStorage.getItem('tradestaar.chartOverlays') || 'null');
    if (saved && typeof saved === 'object') ctMergePrefs(saved);
    var td = JSON.parse(localStorage.getItem('tradestaar.drawDefaults') || 'null');
    if (td && typeof td === 'object') ctToolDefaults = td;
  } catch (e) {}
})();
/* Pre-market and after-hours candles: green/red like the regular session.
   The blue/purple session colors were on for some accounts; switched off
   once (synced, so it wins over the older account copy). The checkbox in
   Chart settings still turns them back on. */
var ctSessFixNow = false;
(function () {
  try {
    if (localStorage.getItem('tradestaar.sessColorsOff.v1')) return;
    localStorage.setItem('tradestaar.sessColorsOff.v1', '1');
    ctSessFixNow = true;
    if (ctPrefs.candles.session) {
      ctPrefs.candles.session = false;
      localStorage.setItem('tradestaar.chartOverlays', JSON.stringify(ctPrefs));
      localStorage.setItem('tradestaar.chartOverlaysAt', String(Date.now()));
      ctSavePrefs();
    }
  } catch (e) {}
})();
/* Zone and alert labels moved onto the price axis; the in-chart tags are
   switched off once for accounts that saved the older default (the
   checkbox in Zones & events turns them back on). */
var ctAxisTagFixNow = false;
(function () {
  try {
    if (localStorage.getItem('tradestaar.axisTags.v1')) return;
    localStorage.setItem('tradestaar.axisTags.v1', '1');
    ctAxisTagFixNow = true;
    if (ctPrefs.overlays) {
      ctPrefs.overlays.tags = false; ctPrefs.overlays.axisTags = true;
      localStorage.setItem('tradestaar.chartOverlays', JSON.stringify(ctPrefs));
      localStorage.setItem('tradestaar.chartOverlaysAt', String(Date.now()));
      ctSavePrefs();
    }
  } catch (e) {}
})();
function ctMergePrefs(saved) {
  // The single Bid/Ask switch became two.
  if (saved.labels && saved.labels.bidask === false && saved.labels.bid == null) { saved.labels.bid = false; saved.labels.ask = false; }
  Object.keys(CT_PREF_DEFAULTS).forEach(function (k) {
    if (saved[k] == null) return;
    if (typeof CT_PREF_DEFAULTS[k] === 'object') ctPrefs[k] = Object.assign({}, CT_PREF_DEFAULTS[k], ctPrefs[k], saved[k]);
    else ctPrefs[k] = saved[k];
  });
}
var ctPrefsTimer = null;
function ctSavePrefs() {
  try {
    localStorage.setItem('tradestaar.chartOverlays', JSON.stringify(ctPrefs));
    localStorage.setItem('tradestaar.drawDefaults', JSON.stringify(ctToolDefaults));
  } catch (e) {}
  clearTimeout(ctPrefsTimer);
  ctPrefsTimer = setTimeout(function () {
    fetch('/api/chart/prefs', {method: 'PUT', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prefs: {overlays: ctPrefs, tools: ctToolDefaults, chart: ctChartBundle(), updated: Date.now()}})})
      .then(function (r) { ctSaveStatus(r.ok ? 'saved' : 'error'); }).catch(function () { ctSaveStatus('error'); });
  }, 900);
}
function ctLoadServerPrefs() {
  fetch('/api/chart/prefs', {credentials: 'same-origin', cache: 'no-store'})
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.prefs) return;
      var localAt = 0;
      try { localAt = Number(localStorage.getItem('tradestaar.chartOverlaysAt')) || 0; } catch (e) {}
      if ((Number(d.prefs.updated) || 0) <= localAt) return;
      if (d.prefs.overlays) ctMergePrefs(d.prefs.overlays);
      // Indicator styles, chart type, volume and extended hours ride along too.
      if (d.prefs.chart) ctApplyChartBundle(d.prefs.chart, false);
      // First load on this device after the switch-off: the account copy may
      // still have session colors on — apply the switch-off to it too.
      if (ctSessFixNow && ctPrefs.candles.session) { ctPrefs.candles.session = false; ctSessFixNow = false; ctPrefChanged(); }
      // Same for the move of zone/alert tags onto the price axis.
      if (ctAxisTagFixNow && ctPrefs.overlays && (ctPrefs.overlays.tags || ctPrefs.overlays.axisTags === false)) {
        ctPrefs.overlays.tags = false; ctPrefs.overlays.axisTags = true; ctAxisTagFixNow = false; ctPrefChanged();
      }
      if (d.prefs.tools && typeof d.prefs.tools === 'object') ctToolDefaults = d.prefs.tools;
      try {
        localStorage.setItem('tradestaar.chartOverlays', JSON.stringify(ctPrefs));
        localStorage.setItem('tradestaar.drawDefaults', JSON.stringify(ctToolDefaults));
        localStorage.setItem('tradestaar.chartOverlaysAt', String(d.prefs.updated));
      } catch (e) {}
      ctApplyCandleColors(); ctRenderStudies(); ctSyncSettingsUI(); twRenderDrawings();
    }).catch(function () {});
}
function ctPrefChanged() {
  try { localStorage.setItem('tradestaar.chartOverlaysAt', String(Date.now())); } catch (e) {}
  ctSavePrefs();
}
/* ═══ The Terminal's own chart setup (Indicators tab, chart type, volume,
   extended hours, timeframe) — synced with the account like the rest, and
   part of "Save as my default". ═══ */
function ctChartBundle(withTimeframe) {
  if (typeof twChartState === 'undefined') return null;
  var b = {type: twChartState.type, volume: !!twChartState.showVolume,
    extended: typeof twExtendedPref !== 'undefined' ? !!twExtendedPref : !!twChartState.extended,
    indicators: twChartState.indicators, styles: twChartState.styles, studies: twChartState.studies};
  if (withTimeframe) { b.interval = twChartState.interval; b.range = twChartState.range; }
  return JSON.parse(JSON.stringify(b));
}
function ctChartChanged() { ctPrefChanged(); }
function ctApplyChartBundle(b, withTimeframe) {
  if (!b || typeof twChartState === 'undefined') return;
  try {
    if (b.styles && typeof b.styles === 'object') {
      Object.keys(b.styles).forEach(function (k) { if (k !== 'emas') twChartState.styles[k] = b.styles[k]; });
      Object.keys(b.styles.emas || {}).forEach(function (p) { twChartState.styles.emas[p] = Object.assign({}, twChartState.styles.emas[p] || {}, b.styles.emas[p]); });
      localStorage.setItem('tradestaar.chartStyles', JSON.stringify(twChartState.styles));
      if (typeof twApplyStudyStyles === 'function') twApplyStudyStyles();
    }
    if (b.studies && typeof b.studies === 'object') {
      Object.keys(b.studies).forEach(function (k) { var v = Number(b.studies[k]); if (isFinite(v)) twChartState.studies[k] = Math.round(v); });
      localStorage.setItem('tradestaar.chartStudies', JSON.stringify(twChartState.studies));
    }
    if (b.indicators && typeof b.indicators === 'object') {
      twChartState.indicators.rsi = b.indicators.rsi !== false; twChartState.indicators.macd = b.indicators.macd !== false;
      localStorage.setItem('tradestaar.chartIndicators', JSON.stringify(twChartState.indicators));
      if (typeof twSyncIndicatorMenu === 'function') twSyncIndicatorMenu();
      if (typeof twApplyIndicatorVisibility === 'function') twApplyIndicatorVisibility();
    }
    if (b.type === 'candles' || b.type === 'area') {
      twChartState.type = b.type; localStorage.setItem('tradestaar.chartType', b.type);
      document.querySelectorAll('.tw-type-btn[data-type]').forEach(function (x) { x.classList.toggle('active', x.dataset.type === b.type); });
      if (typeof twApplyType === 'function') twApplyType();
    }
    if (typeof b.volume === 'boolean' && b.volume !== !!twChartState.showVolume) {
      twChartState.showVolume = b.volume; localStorage.setItem('tradestaar.chartVolume', b.volume ? '1' : '0');
      var vb = document.getElementById('tw-volume-btn'); if (vb) vb.classList.toggle('active', b.volume);
      if (twChartState.vol) twChartState.vol.applyOptions({visible: b.volume});
    }
    if (typeof b.extended === 'boolean') {
      localStorage.setItem('tradestaar.chartExtendedPref', b.extended ? '1' : '0');
      if (typeof twExtendedPref !== 'undefined') { twExtendedPref = b.extended; }
    }
    if (typeof twSyncStudySettings === 'function') twSyncStudySettings();
    if (typeof twRenderIndicators === 'function') twRenderIndicators();
    if (withTimeframe && b.interval) {
      var tf = document.querySelector('.tw-tf-btn[data-interval="' + b.interval + '"]');
      if (b.range) { twChartState.range = b.range; try { localStorage.setItem('tradestaar.chartRange', b.range); } catch (e) {} }
      if (tf && typeof twInterval === 'function') twInterval(tf);
    }
  } catch (e) {}
}
/* "Saved ✓" in the settings footer after each sync. */
var ctSaveFlashT = null;
function ctSaveStatus(state) {
  var el = document.getElementById('ct-save-text'), note = document.getElementById('ct-save-note');
  if (!el || !note) return;
  note.classList.remove('is-saved', 'is-error');
  if (state === 'saved') {
    note.classList.add('is-saved'); el.textContent = 'Saved to your account ✓';
  } else {
    note.classList.add('is-error'); el.textContent = 'Couldn’t reach the server — saved on this device, will sync next change.';
  }
  clearTimeout(ctSaveFlashT);
  ctSaveFlashT = setTimeout(function () { note.classList.remove('is-saved'); el.textContent = 'Changes save to your account automatically and sync to your devices.'; }, 2600);
}
/* Save as my default / Reset to my default. */
var ctMyDefault = null;
function ctLoadMyDefault() {
  fetch('/api/chart/default', {credentials: 'same-origin', cache: 'no-store'})
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { ctMyDefault = d && d.default && d.default.overlays ? d.default : null; ctSyncDefaultBtn(); })
    .catch(function () {});
}
function ctSyncDefaultBtn() {
  var b = document.getElementById('ct-mydefault-btn'); if (!b) return;
  b.disabled = !ctMyDefault;
  if (ctMyDefault && ctMyDefault.saved) {
    var d = new Date(ctMyDefault.saved);
    b.textContent = 'Reset to my default · ' + d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'});
  } else b.textContent = 'Reset to my default';
}
function ctLbl(key) { return !!(ctPrefs.labels && ctPrefs.labels[key]); }

/* ═══ Time ⇄ logical index ⇄ pixels ═══ */
var CT_STEP = {'1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400, '1w': 604800, '1mo': 2629800};
function ctSec(t) {
  if (t && typeof t === 'object' && t.year) return Date.UTC(t.year, (t.month || 1) - 1, t.day || 1) / 1000;
  var n = Number(t);
  if (isFinite(n)) return n;
  var parsed = Date.parse(t);
  return isFinite(parsed) ? parsed / 1000 : null;
}
var ctSecCache = {ref: null, len: -1, arr: []};
function ctBarSecs() {
  var bars = twChartState.bars || [];
  if (ctSecCache.ref !== bars || ctSecCache.len !== bars.length) {
    ctSecCache = {ref: bars, len: bars.length, arr: bars.map(function (b) { return ctSec(b.time); })};
  }
  return ctSecCache.arr;
}
function ctStep() { return CT_STEP[twChartState.interval] || 300; }
function ctTimeToLogical(t) {
  var arr = ctBarSecs(), n = arr.length, step = ctStep();
  if (!n || t == null || !isFinite(t)) return null;
  if (t <= arr[0]) return (t - arr[0]) / step;
  if (t >= arr[n - 1]) return n - 1 + (t - arr[n - 1]) / step;
  var lo = 0, hi = n - 1;
  while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (arr[mid] <= t) lo = mid; else hi = mid; }
  var span = arr[hi] - arr[lo];
  return lo + (span > 0 ? (t - arr[lo]) / span : 0);
}
function ctLogicalToTime(l) {
  var arr = ctBarSecs(), n = arr.length, step = ctStep();
  if (!n || l == null || !isFinite(l)) return null;
  if (l <= 0) return arr[0] + l * step;
  if (l >= n - 1) return arr[n - 1] + (l - (n - 1)) * step;
  var i = Math.floor(l), f = l - i;
  return arr[i] + f * (arr[i + 1] - arr[i]);
}
function ctSeries() {
  return twChartState.type === 'candles' ? twChartState.candle : twChartState.area;
}
/* lightweight-charts 4.1 answers 0 for a fractional logical index, which threw
   any point that does not sit exactly on a bar (a 15m point on the 1H, a
   dragged or resized drawing) to the left edge. Interpolate between the two
   neighbouring whole bars instead. */
function ctLogX(l) {
  var ts = twChartState.chart && twChartState.chart.timeScale();
  if (!ts || l == null || !isFinite(l)) return null;
  var i = Math.floor(l), f = l - i, x0 = ts.logicalToCoordinate(i);
  if (x0 == null) return null;
  if (f < 1e-9) return x0;
  var x1 = ts.logicalToCoordinate(i + 1);
  return x1 == null ? x0 : x0 + (x1 - x0) * f;
}
function ctX(t) {
  return ctLogX(ctTimeToLogical(t));
}
function ctY(p) {
  var s = ctSeries();
  if (!s || p == null || !isFinite(p)) return null;
  var y = s.priceToCoordinate(p);
  return y == null ? null : y;
}
function ctXY(pt) {
  if (!pt) return null;
  var x = ctX(pt.t), y = ctY(pt.p);
  return x == null || y == null ? null : {x: x, y: y};
}
function ctPriceAt(y) { var s = ctSeries(); var p = s ? s.coordinateToPrice(y) : null; return p == null || !isFinite(p) ? null : Number(p); }
function ctLogicalAt(x) { var l = twChartState.chart ? twChartState.chart.timeScale().coordinateToLogical(x) : null; return l == null || !isFinite(l) ? null : Number(l); }
function ctPointAt(x, y, snap) {
  var l = ctLogicalAt(x), p = ctPriceAt(y);
  if (l == null || p == null) return null;
  if (snap && ctPrefs.magnet) {
    var bars = twChartState.bars, idx = Math.max(0, Math.min(bars.length - 1, Math.round(l))), bar = bars[idx];
    if (bar) {
      var best = null;
      [bar.open, bar.high, bar.low, bar.close].forEach(function (v) {
        var vy = ctY(Number(v)); if (vy == null) return;
        var d = Math.abs(vy - y); if (!best || d < best.d) best = {d: d, v: Number(v)};
      });
      l = idx;
      if (best && best.d < 40) p = best.v;
    }
  }
  return {t: ctLogicalToTime(l), p: p};
}
function ctDims() {
  var box = document.getElementById('tw-chart-box');
  var w = box ? box.clientWidth : 0, h = box ? box.clientHeight : 0, axisW = 64, axisH = 0;
  try { axisW = twChartState.chart.priceScale('right').width() || 64; } catch (e) {}
  try { var ts = twChartState.chart.timeScale(); axisH = ts.height ? ts.height() : 0; } catch (e) {}
  return {W: Math.max(0, w - axisW), H: Math.max(0, h - axisH), fullW: w, fullH: h, axisW: axisW};
}
function ctFmt(p) {
  p = Number(p);
  if (!isFinite(p)) return '—';
  var a = Math.abs(p);
  return p.toFixed(a < 1 ? 4 : 2);
}
function ctHexA(hex, a) {
  var m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ''));
  if (!m) return hex;
  var n = parseInt(m[1], 16);
  return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
}
function ctDashArr(d, w) { return d === 'dashed' ? [6, 4] : (d === 'dotted' ? [Math.max(1, w), 3] : []); }

/* ═══ Drawing store (per ticker, synced to the account) ═══ */
var ctStore = {ticker: null, drawings: [], updated: 0, fetchedAt: 0, undo: [], redo: [], hidden: false};
var ctQuick = null;  // Shift+drag measure: shown until the next click, never saved
var ctDraft = null, ctAction = null, ctBoxes = {}, ctPaths = {}, ctLabelHits = {}, ctCurId = null;
var ctSaveTimer = null;
function ctLocalKey(t) { return 'tradestaar.draw.v2.' + t; }
function ctUid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }
function ctConvertLegacy(d) {
  if (!d || !Array.isArray(d.points) || d.points.length < 2) return null;
  var pts = d.points.map(function (p) { return {t: ctSec(p.time), p: Number(p.price)}; });
  if (pts.some(function (p) { return p.t == null || !isFinite(p.p); })) return null;
  var type = d.type === 'fib' ? 'fib' : 'trend';
  var nd = ctNewDrawing(type, pts);
  nd.id = d.id || nd.id;
  if (d.color) nd.style.color = d.color;
  if (d.width) nd.style.width = Number(d.width) || 2;
  if (type === 'fib' && d.color) nd.style.levels = nd.style.levels.map(function (l) { return Object.assign({}, l, {c: d.color}); });
  return nd;
}
function ctReadLocal(ticker) {
  var out = {updated: 0, drawings: []};
  try {
    var raw = JSON.parse(localStorage.getItem(ctLocalKey(ticker)) || 'null');
    if (raw && Array.isArray(raw.drawings)) out = {updated: Number(raw.updated) || 0, drawings: raw.drawings};
    // Drawings saved before they were shared across timeframes lived under one
    // key per interval/range. Fold them in once.
    var prefix = 'tradestaar.drawings.' + ticker + '.', legacy = [], seen = {};
    out.drawings.forEach(function (d) { seen[d.id] = 1; });
    for (var i = localStorage.length - 1; i >= 0; i--) {
      var key = localStorage.key(i);
      if (!key || key.indexOf(prefix) !== 0) continue;
      try {
        (JSON.parse(localStorage.getItem(key) || '[]') || []).forEach(function (d) {
          var nd = ctConvertLegacy(d); if (nd && !seen[nd.id]) { seen[nd.id] = 1; legacy.push(nd); }
        });
      } catch (e) {}
      localStorage.removeItem(key);
    }
    if (legacy.length) { out.drawings = out.drawings.concat(legacy); out.updated = Date.now(); localStorage.setItem(ctLocalKey(ticker), JSON.stringify(out)); }
  } catch (e) {}
  out.drawings = out.drawings.filter(function (d) { return d && CT_TOOLS[d.type] && Array.isArray(d.points); });
  return out;
}
function twLoadDrawings() {
  var ticker = twChartState.ticker;
  if (!ticker) return;
  ctDraft = null; ctAction = null;
  if (ctStore.ticker !== ticker) {
    var local = ctReadLocal(ticker);
    ctStore = {ticker: ticker, drawings: local.drawings, updated: local.updated, fetchedAt: 0, undo: [], redo: []};
    ctSyncUndoBtns();
    twChartState.selectedDrawing = null;
  }
  twRenderDrawings();
  ctLoadAlerts(ticker);
  if (Date.now() - ctStore.fetchedAt < 30000) return;
  ctStore.fetchedAt = Date.now();
  fetch('/api/chart/drawings/' + encodeURIComponent(ticker), {credentials: 'same-origin', cache: 'no-store'})
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.ok || ctStore.ticker !== ticker || ctAction || ctDraft) return;
      var serverAt = Number(d.updated) || 0;
      if (serverAt > ctStore.updated && Array.isArray(d.drawings)) {
        ctStore.drawings = d.drawings.filter(function (x) { return x && CT_TOOLS[x.type] && Array.isArray(x.points); });
        ctStore.updated = serverAt;
        try { localStorage.setItem(ctLocalKey(ticker), JSON.stringify({updated: serverAt, drawings: ctStore.drawings})); } catch (e) {}
        twChartState.selectedDrawing = null;
        twRenderDrawings();
      } else if (ctStore.updated > serverAt) {
        ctPushServer(ticker);
      }
    }).catch(function () {});
}
function twSaveDrawings() {
  var ticker = ctStore.ticker;
  if (!ticker) return;
  ctStore.updated = Date.now();
  try { localStorage.setItem(ctLocalKey(ticker), JSON.stringify({updated: ctStore.updated, drawings: ctStore.drawings})); } catch (e) {}
  clearTimeout(ctSaveTimer);
  ctSaveTimer = setTimeout(function () { ctPushServer(ticker); }, 700);
}
function ctPushServer(ticker) {
  if (ticker !== ctStore.ticker) return;
  fetch('/api/chart/drawings/' + encodeURIComponent(ticker), {
    method: 'PUT', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({updated: ctStore.updated, drawings: ctStore.drawings})
  }).catch(function () {});
}
function ctSnapshot() { return JSON.stringify(ctStore.drawings); }
/* Undo / redo for drawings: every add, move, edit, delete or clear is a step.
   A new change after undoing clears the redo steps (like any editor). */
function ctPushUndo(snap) {
  ctStore.undo.push(snap == null ? ctSnapshot() : snap);
  if (ctStore.undo.length > 60) ctStore.undo.shift();
  ctStore.redo = [];
  ctSyncUndoBtns();
}
function ctUndo() {
  if (!ctStore.undo.length) return;
  var cur = ctSnapshot();
  try { ctStore.drawings = JSON.parse(ctStore.undo.pop()); } catch (e) { return; }
  (ctStore.redo = ctStore.redo || []).push(cur);
  if (ctStore.redo.length > 60) ctStore.redo.shift();
  twChartState.selectedDrawing = null;
  twSaveDrawings(); twRenderDrawings(); ctSyncUndoBtns();
  ctNotice('↶ Undone' + (ctStore.undo.length ? '' : ' — nothing more to undo'));
}
function ctRedo() {
  if (!ctStore.redo || !ctStore.redo.length) return;
  var cur = ctSnapshot();
  try { ctStore.drawings = JSON.parse(ctStore.redo.pop()); } catch (e) { return; }
  ctStore.undo.push(cur);
  twChartState.selectedDrawing = null;
  twSaveDrawings(); twRenderDrawings(); ctSyncUndoBtns();
  ctNotice('↷ Redone');
}
function ctSyncUndoBtns() {
  var u = !!(ctStore.undo && ctStore.undo.length), r = !!(ctStore.redo && ctStore.redo.length);
  document.querySelectorAll('[data-ct-act="undo"], [data-ct-fb="_undo"]').forEach(function (b) {
    b.disabled = !u; b.title = u ? 'Undo (Ctrl/⌘ Z) — ' + ctStore.undo.length + ' step' + (ctStore.undo.length === 1 ? '' : 's') : 'Nothing to undo';
  });
  document.querySelectorAll('[data-ct-act="redo"], [data-ct-fb="_redo"]').forEach(function (b) {
    b.disabled = !r; b.title = r ? 'Redo (Ctrl/⌘ Shift Z) — ' + ctStore.redo.length + ' step' + (ctStore.redo.length === 1 ? '' : 's') : 'Nothing to redo';
  });
}
function ctSelected() {
  var i = twChartState.selectedDrawing;
  return i == null ? null : ctStore.drawings[i] || null;
}

function ctNewDrawing(type, points) {
  var tool = CT_TOOLS[type], style = Object.assign({}, CT_BASE_STYLE, tool.defaults || {}, ctToolDefaults[type] || {});
  if (type === 'fib' || type === 'fibext' || type === 'pctlevels') {
    var base = type === 'fib' ? CT_FIB_LEVELS : (type === 'fibext' ? CT_FIBEXT_LEVELS : CT_PCT_LEVELS);
    style.levels = JSON.parse(JSON.stringify((ctToolDefaults[type] && ctToolDefaults[type].levels) || base));
  } else delete style.levels;
  var d = {id: ctUid(), type: type, points: points.map(function (p) { return {t: p.t, p: p.p}; }), style: style, locked: false};
  if (type === 'channel') {
    if (points[2]) d.off = ctChannelOffset(d.points[0], d.points[1], points[2]);
    else d.off = 0;
    d.points = d.points.slice(0, 2);
  }
  if (type === 'long' || type === 'short') {
    var entry = d.points[0].p, target = d.points[1] ? d.points[1].p : entry;
    d.stop = entry - (target - entry);
  }
  return d;
}
function ctChannelOffset(a, b, c) {
  var la = ctTimeToLogical(a.t), lb = ctTimeToLogical(b.t), lc = ctTimeToLogical(c.t);
  if (la == null || lb == null || lc == null) return 0;
  var base = la === lb ? a.p : a.p + (b.p - a.p) * (lc - la) / (lb - la);
  return c.p - base;
}

/* ═══ Rendering ═══ */
function twPointCoords(point) {
  // Legacy signature ({time, price}) kept for older callers.
  if (!point) return null;
  return ctXY({t: point.t != null ? point.t : ctSec(point.time), p: point.p != null ? point.p : point.price});
}
function twRenderDrawings() {
  var canvas = document.getElementById('tw-chart-drawing');
  var fit = twCanvasContext(canvas);
  if (!fit) return;
  if (!twChartState.chart || !(twChartState.bars || []).length || ctStore.ticker !== twChartState.ticker) { ctUpdateProps(); return; }
  var ctx = fit.ctx, dim = ctDims(), axisLabels = [], timeLabels = [];
  ctBoxes = {}; ctPaths = {}; ctLabelHits = {};
  ctx.save();
  ctx.beginPath(); ctx.rect(0, 0, dim.W, dim.H); ctx.clip();
  var list = ctPrefs.draw.hidden ? [] : ctStore.drawings.slice();
  list.forEach(function (d, i) {
    if (d.hidden) return;
    var sel = i === twChartState.selectedDrawing;
    try { ctDrawOne(ctx, d, dim, sel, axisLabels, timeLabels); } catch (e) {}
  });
  if (ctDraft && ctDraft.points.length) {
    var draft = ctNewDrawing(ctDraft.type, ctDraft.points);
    draft.id = '_draft';
    try { ctDrawOne(ctx, draft, dim, true, axisLabels, timeLabels, true); } catch (e) {}
  }
  if (ctQuick && ctQuick.points.length === 2) {
    var qm = ctNewDrawing('measure', ctQuick.points);
    qm.id = '_quick';
    try { ctDrawOne(ctx, qm, dim, true, axisLabels, timeLabels, true); } catch (e) {}
  }
  ctExtraAxis = []; ctTimeAxisMarks = [];
  try { ctDrawExtras(ctx, dim); } catch (e) {}
  try { ctDrawAlerts(ctx, dim, axisLabels); } catch (e) {}
  ctx.restore();
  ctCurId = null;
  try { ctDrawAxisChrome(ctx, dim); ctDrawTimeAxisMarks(ctx, dim); } catch (e) {}
  // The shared time axis under RSI/MACD carries the same marks.
  var sharedAx = document.getElementById('tw-time-axis');
  if (sharedAx && sharedAx.style.display !== 'none' && typeof twDrawTimeAxis === 'function') { try { twDrawTimeAxis(); } catch (e) {} }
  ctLayoutAxisLabels(ctx, dim, axisLabels.concat(ctExtraAxis));
  timeLabels.forEach(function (l) { ctTimeLabel(ctx, dim, l.t, l.c); });
  ctUpdateProps();
  ctSyncAutoBtn();
}
/* Axis tags: zones, alerts and drawing prices sit on the price axis with
   their name attached. Tags that would overlap are pushed apart (each
   keeps a short leader line to its real price). */
var ctExtraAxis = [];
var CT_TAG_H = 16;
function ctLayoutAxisLabels(ctx, dim, labels) {
  var GAP = 2, items = [];
  labels.forEach(function (l) {
    var y = ctY(l.p);
    if (y == null || y < 0 || y > dim.H) return;
    items.push({l: l, y0: y, y: y});
  });
  items.sort(function (a, b) { return a.y0 - b.y0; });
  // Spacing depends on each tag's height (named tags are shorter).
  var hOf = function (it) { return it.l.text ? CT_TAG_H : 18; };
  for (var i = 1; i < items.length; i++) {
    var need = (hOf(items[i - 1]) + hOf(items[i])) / 2 + GAP;
    if (items[i].y < items[i - 1].y + need) items[i].y = items[i - 1].y + need;
  }
  // Pushed past the bottom: settle upward instead.
  for (var j = items.length - 1; j >= 0; j--) {
    var maxY = j === items.length - 1 ? dim.H - hOf(items[j]) / 2
      : items[j + 1].y - (hOf(items[j]) + hOf(items[j + 1])) / 2 - GAP;
    if (items[j].y > maxY) items[j].y = maxY;
  }
  items.forEach(function (it) {
    var l = it.l;
    if (Math.abs(it.y - it.y0) > 1) {
      // Moved to make room: a soft elbow leads back to the real price.
      ctx.save(); ctx.strokeStyle = l.c; ctx.globalAlpha = 0.6; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(dim.W - 10, it.y0); ctx.lineTo(dim.W - 5, it.y0); ctx.lineTo(dim.W, it.y); ctx.stroke();
      ctx.beginPath(); ctx.arc(dim.W - 10, it.y0, 1.6, 0, Math.PI * 2); ctx.fillStyle = l.c; ctx.fill(); ctx.restore();
    }
    var r = ctAxisLabel(ctx, dim, l.p, l.c, l.text, it.y, l);
    if (r && l.alertId != null && r.close) ctAlertHits.push({id: l.alertId, x1: r.close.x1, x2: r.close.x2, y1: r.y1, y2: r.y2});
  });
}
function ctRoundRect(ctx, x, y, w, h, r) {
  r = Math.min(r, h / 2, w / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath();
}
/* Price pill on the axis: rounded, with a small arrow nosing into the chart. */
function ctPricePill(ctx, dim, y, h, color, label) {
  var x = dim.W + 1, w = dim.axisW - 3, r = 4;
  ctx.beginPath();
  ctx.moveTo(x - 5, y);
  ctx.lineTo(x, y - h / 2 + 2); ctx.quadraticCurveTo(x, y - h / 2, x + 2, y - h / 2);
  ctx.lineTo(x + w - r, y - h / 2); ctx.quadraticCurveTo(x + w, y - h / 2, x + w, y - h / 2 + r);
  ctx.lineTo(x + w, y + h / 2 - r); ctx.quadraticCurveTo(x + w, y + h / 2, x + w - r, y + h / 2);
  ctx.lineTo(x + 2, y + h / 2); ctx.quadraticCurveTo(x, y + h / 2, x, y + h / 2 - 2);
  ctx.closePath();
  ctx.fillStyle = color; ctx.fill();
  ctx.fillStyle = 'rgba(255,255,255,0.18)'; ctx.fillRect(x + 1, y - h / 2 + 1, w - 2, 1);   // top sheen
  ctx.fillStyle = ctTextOn(color); ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
  ctx.fillText(label, x + 5, y + 0.5);
}
function ctBellIcon(ctx, x, y, c) {
  ctx.save(); ctx.fillStyle = c;
  ctx.beginPath(); ctx.moveTo(x - 3.6, y + 2.4);
  ctx.quadraticCurveTo(x - 3.4, y - 3.8, x, y - 4); ctx.quadraticCurveTo(x + 3.4, y - 3.8, x + 3.6, y + 2.4);
  ctx.closePath(); ctx.fill();
  ctx.fillRect(x - 4.6, y + 2, 9.2, 1.3);
  ctx.beginPath(); ctx.arc(x, y + 4.4, 1.2, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}
/* What an alert is called on its tag: Buy plan 2 → "② BUY", Load the boat → "LOAD THE BOAT". */
function ctAlertTagParts(text) {
  var t = String(text || '').replace(/^🔔[↑↓]?\s*/, '').replace(/\s*×\s*$/, '').trim();
  var m = /buy\s*plan\s*(\d+)/i.exec(t);
  if (m) return {num: m[1], name: 'BUY'};
  return {num: '', name: t.toUpperCase()};
}
function ctAxisLabel(ctx, dim, price, color, title, yAt, meta) {
  var y = yAt != null ? yAt : ctY(price);
  if (y == null || y < -9 || y > dim.H + 9) return null;
  var label = ctFmt(price);
  ctx.save();
  var h = title ? CT_TAG_H : 18;
  ctx.font = title ? '700 9.5px "IBM Plex Mono", ui-monospace, monospace' : '700 11px "IBM Plex Mono", ui-monospace, monospace';
  ctPricePill(ctx, dim, y, h, color, label);
  if (!title) { ctx.restore(); return null; }
  // The name: a dark glass flag in the tag's colour, left of the axis.
  var isAlert = meta && meta.alertId != null;
  var parts = isAlert ? ctAlertTagParts(title) : {num: '', name: String(title)};
  ctx.font = '800 8.5px "IBM Plex Mono", ui-monospace, monospace';
  var nameW = ctx.measureText(parts.name).width;
  var pad = 6, icon = isAlert ? 12 : 0, badge = parts.num ? 13 : 0, close = isAlert ? 14 : 0, arrow = isAlert && meta.dir ? 9 : 0;
  var w = pad + close + icon + badge + nameW + arrow + pad + 4;
  var x = dim.W - 6 - w, top = y - h / 2;
  ctRoundRect(ctx, x, top, w + 6, h, 4);
  ctx.fillStyle = 'rgba(9,12,18,0.94)'; ctx.fill();
  ctx.strokeStyle = ctHexA(color, 0.85); ctx.lineWidth = 1; ctx.stroke();
  ctx.fillStyle = color; ctx.fillRect(x + 0.5, top + 3, 2, h - 6);                       // accent bar
  var cx = x + pad, out = {x1: x, x2: dim.W, y1: top, y2: top + h};
  ctx.textBaseline = 'middle'; ctx.textAlign = 'center';
  if (close) {
    ctx.fillStyle = 'rgba(255,255,255,0.08)'; ctx.beginPath(); ctx.arc(cx + 5, y, 5, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = 'rgba(214,224,236,0.75)'; ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(cx + 3, y - 2); ctx.lineTo(cx + 7, y + 2); ctx.moveTo(cx + 7, y - 2); ctx.lineTo(cx + 3, y + 2); ctx.stroke();
    out.close = {x1: cx - 2, x2: cx + 12};
    cx += close;
  }
  if (icon) { ctBellIcon(ctx, cx + 4, y, color); cx += icon; }
  if (badge) {
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(cx + 5, y, 5.5, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = ctTextOn(color); ctx.font = '800 8px "IBM Plex Mono", ui-monospace, monospace';
    ctx.fillText(parts.num, cx + 5, y + 0.5); cx += badge;
    ctx.font = '800 8.5px "IBM Plex Mono", ui-monospace, monospace';
  }
  ctx.textAlign = 'left'; ctx.fillStyle = color; ctx.fillText(parts.name, cx, y + 0.5); cx += nameW;
  if (arrow) {
    var up = meta.dir === 'above';
    ctx.beginPath();
    if (up) { ctx.moveTo(cx + 3, y + 2.5); ctx.lineTo(cx + 6, y - 2.5); ctx.lineTo(cx + 9, y + 2.5); }
    else { ctx.moveTo(cx + 3, y - 2.5); ctx.lineTo(cx + 6, y + 2.5); ctx.lineTo(cx + 9, y - 2.5); }
    ctx.closePath(); ctx.fillStyle = up ? '#22C58B' : '#FF6B6B'; ctx.fill();
  }
  ctx.restore();
  return out;
}
function ctTimeLabel(ctx, dim, t, color) {
  var x = ctX(t);
  if (x == null || x < 0 || x > dim.W || dim.fullH - dim.H < 10) return;
  var label = typeof twEasternTimeLabel === 'function' ? twEasternTimeLabel(t) : String(t);
  ctx.save();
  ctx.font = '700 10px "IBM Plex Mono", ui-monospace, monospace';
  var w = ctx.measureText(label).width + 12, h = Math.min(18, dim.fullH - dim.H - 2);
  var left = Math.max(0, Math.min(dim.W - w, x - w / 2));
  ctx.fillStyle = color; ctx.fillRect(left, dim.H + 1, w, h);
  ctx.fillStyle = ctTextOn(color); ctx.textBaseline = 'middle';
  ctx.fillText(label, left + 6, dim.H + 1 + h / 2);
  ctx.restore();
}
function ctTextOn(color) {
  var m = /^#?([0-9a-f]{6})$/i.exec(String(color || ''));
  if (!m) return '#0b0e11';
  var n = parseInt(m[1], 16), lum = (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255));
  return lum > 150 ? '#0b0e11' : '#ffffff';
}
function ctStroke(ctx, s, alpha) {
  ctx.strokeStyle = s.color; ctx.lineWidth = Number(s.width) || 1;
  ctx.setLineDash(ctDashArr(s.dash, Number(s.width) || 1));
  ctx.globalAlpha = alpha == null ? 1 : alpha;
}
function ctLineThrough(a, b, s, dim) {
  // Segment a→b, extended to the plot edges where the style asks for it.
  var p1 = {x: a.x, y: a.y}, p2 = {x: b.x, y: b.y}, dx = b.x - a.x, dy = b.y - a.y;
  if (Math.abs(dx) < 1e-6) {
    if (s.extendLeft || s.extendRight) { if (s.extendLeft) p1.y = a.y < b.y ? -10 : dim.H + 10; if (s.extendRight) p2.y = b.y < a.y ? -10 : dim.H + 10; }
    return [p1, p2];
  }
  var slope = dy / dx, big = dim.W * 4 + 2000;
  if (s.extendRight) { var xr = dx >= 0 ? big : -big; p2 = {x: xr, y: a.y + slope * (xr - a.x)}; }
  if (s.extendLeft) { var xl = dx >= 0 ? -big : big; p1 = {x: xl, y: a.y + slope * (xl - a.x)}; }
  return [p1, p2];
}
function ctHandles(ctx, pts, color) {
  ctx.save(); ctx.setLineDash([]); ctx.globalAlpha = 1;
  pts.forEach(function (p) {
    if (!p) return;
    ctx.beginPath(); ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#0b0f12'; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = color; ctx.stroke();
  });
  ctx.restore();
}
function ctLabelText(ctx, text, x, y, color, opts) {
  opts = opts || {};
  ctx.save(); ctx.setLineDash([]); ctx.globalAlpha = 1;
  ctx.font = (opts.weight || '600') + ' ' + (opts.size || 11) + 'px "IBM Plex Sans", system-ui, sans-serif';
  ctx.textBaseline = opts.baseline || 'bottom'; ctx.textAlign = opts.align || 'left';
  if (opts.bg) {
    var w = ctx.measureText(text).width, h = (opts.size || 11) + 8;
    var bx = opts.align === 'center' ? x - w / 2 - 6 : (opts.align === 'right' ? x - w - 12 : x - 6);
    var by = opts.baseline === 'middle' ? y - h / 2 : (opts.baseline === 'top' ? y - 4 : y - h + 4);
    ctx.fillStyle = opts.bg; ctx.fillRect(bx, by, w + 12, h);
  }
  ctx.fillStyle = color; ctx.fillText(text, x, y);
  // Labels are part of the drawing: clicking one selects it.
  if (ctCurId) {
    var lw = ctx.measureText(text).width, lh = (opts.size || 11) + 4;
    var lx = ctx.textAlign === 'center' ? x - lw / 2 : (ctx.textAlign === 'right' ? x - lw : x);
    var ly = ctx.textBaseline === 'middle' ? y - lh / 2 : (ctx.textBaseline === 'top' ? y : y - lh);
    (ctLabelHits[ctCurId] = ctLabelHits[ctCurId] || []).push({x: lx, y: ly, w: lw, h: lh});
  }
  ctx.restore();
}
function ctPriceLabels(d, sel) {
  return sel || (d.style.showPrice && ctLbl('drawings'));
}
function ctDrawOne(ctx, d, dim, sel, axisLabels, timeLabels, isDraft) {
  var s = d.style, pts = d.points.map(ctXY), type = d.type;
  ctCurId = d.id;
  if (pts.some(function (p) { return !p; })) return;
  var a = pts[0], b = pts[1], color = s.color;
  ctx.save();
  if (type === 'trend' || type === 'ray' || type === 'extended' || type === 'arrow') {
    var seg = ctLineThrough(a, b, s, dim);
    ctStroke(ctx, s); ctx.beginPath(); ctx.moveTo(seg[0].x, seg[0].y); ctx.lineTo(seg[1].x, seg[1].y); ctx.stroke();
    ctPaths[d.id] = {kind: 'seg', a: seg[0], b: seg[1]};
    if (type === 'arrow') {
      var ang = Math.atan2(b.y - a.y, b.x - a.x), len = 10 + (Number(s.width) || 1) * 2;
      ctx.setLineDash([]); ctx.fillStyle = color; ctx.beginPath();
      ctx.moveTo(b.x, b.y);
      ctx.lineTo(b.x - len * Math.cos(ang - 0.42), b.y - len * Math.sin(ang - 0.42));
      ctx.lineTo(b.x - len * Math.cos(ang + 0.42), b.y - len * Math.sin(ang + 0.42));
      ctx.closePath(); ctx.fill();
    }
    if (s.text) ctLabelText(ctx, s.text, (a.x + b.x) / 2, (a.y + b.y) / 2 - 6, color, {align: 'center'});
    if (ctPriceLabels(d, sel)) { axisLabels.push({p: d.points[0].p, c: color}); axisLabels.push({p: d.points[1].p, c: color}); }
    if (sel) ctHandles(ctx, [a, b], color);
  } else if (type === 'hline' || type === 'hray') {
    var x0 = type === 'hray' ? a.x : 0;
    ctStroke(ctx, s); ctx.beginPath(); ctx.moveTo(x0, a.y); ctx.lineTo(dim.W, a.y); ctx.stroke();
    ctPaths[d.id] = {kind: 'h', y: a.y, x0: x0};
    if (s.text) ctLabelText(ctx, s.text, type === 'hray' ? a.x + 6 : 8, a.y - 4, color);
    if (s.showPrice !== false || sel) axisLabels.push({p: d.points[0].p, c: color});
    if (sel) ctHandles(ctx, [a], color);
  } else if (type === 'vline') {
    ctStroke(ctx, s); ctx.beginPath(); ctx.moveTo(a.x, 0); ctx.lineTo(a.x, dim.H); ctx.stroke();
    ctPaths[d.id] = {kind: 'v', x: a.x};
    if (s.text) ctLabelText(ctx, s.text, a.x + 5, 16, color);
    timeLabels.push({t: d.points[0].t, c: color});
    if (sel) ctHandles(ctx, [{x: a.x, y: a.y}], color);
  } else if (type === 'rect') {
    var rx = Math.min(a.x, b.x), ry = Math.min(a.y, b.y), rw = Math.abs(b.x - a.x), rh = Math.abs(b.y - a.y);
    ctx.fillStyle = ctHexA(color, s.fillOpacity); ctx.fillRect(rx, ry, rw, rh);
    ctStroke(ctx, s); ctx.strokeRect(rx, ry, rw, rh);
    if (s.text) ctLabelText(ctx, s.text, rx + 6, ry + 5, color, {baseline: 'top'});
    ctBoxes[d.id] = {x: rx, y: ry, w: rw, h: rh};
    if (ctPriceLabels(d, sel)) { axisLabels.push({p: d.points[0].p, c: color}); axisLabels.push({p: d.points[1].p, c: color}); }
    if (sel) ctHandles(ctx, [a, b, {x: a.x, y: b.y}, {x: b.x, y: a.y}], color);
  } else if (type === 'channel') {
    var off = d.off || 0;
    if (isDraft && ctDraft && ctDraft.points[2]) off = ctChannelOffset(d.points[0], d.points[1], ctDraft.points[2]);
    var a2 = ctXY({t: d.points[0].t, p: d.points[0].p + off}), b2 = ctXY({t: d.points[1].t, p: d.points[1].p + off});
    if (!a2 || !b2) { ctx.restore(); return; }
    var s1 = ctLineThrough(a, b, s, dim), s2 = ctLineThrough(a2, b2, s, dim);
    ctx.fillStyle = ctHexA(color, s.fillOpacity);
    ctx.beginPath(); ctx.moveTo(s1[0].x, s1[0].y); ctx.lineTo(s1[1].x, s1[1].y); ctx.lineTo(s2[1].x, s2[1].y); ctx.lineTo(s2[0].x, s2[0].y); ctx.closePath(); ctx.fill();
    ctStroke(ctx, s);
    ctx.beginPath(); ctx.moveTo(s1[0].x, s1[0].y); ctx.lineTo(s1[1].x, s1[1].y); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(s2[0].x, s2[0].y); ctx.lineTo(s2[1].x, s2[1].y); ctx.stroke();
    ctx.setLineDash([4, 4]); ctx.globalAlpha = 0.6; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo((s1[0].x + s2[0].x) / 2, (s1[0].y + s2[0].y) / 2); ctx.lineTo((s1[1].x + s2[1].x) / 2, (s1[1].y + s2[1].y) / 2); ctx.stroke();
    ctPaths[d.id] = {kind: 'poly', pts: [s1[0], s1[1], s2[1], s2[0]]};
    if (ctPriceLabels(d, sel)) { axisLabels.push({p: d.points[0].p, c: color}); axisLabels.push({p: d.points[1].p, c: color}); }
    if (sel) ctHandles(ctx, [a, b, {x: (a2.x + b2.x) / 2, y: (a2.y + b2.y) / 2}], color);
  } else if (type === 'fib' || type === 'fibext') {
    ctDrawFibs(ctx, d, dim, sel, pts, axisLabels, isDraft);
  } else if (type === 'text') {
    var fs = Number(s.fontSize) || 13, txt = s.text || 'Text';
    ctx.font = '600 ' + fs + 'px "IBM Plex Sans", system-ui, sans-serif';
    var lines = String(txt).split('\n'), tw = 0;
    lines.forEach(function (ln) { tw = Math.max(tw, ctx.measureText(ln).width); });
    var th = lines.length * (fs + 4) + 6;
    if (s.fillOpacity > 0.01) { ctx.fillStyle = 'rgba(8,12,20,' + Math.min(0.92, 0.4 + s.fillOpacity * 3) + ')'; ctx.fillRect(a.x - 5, a.y - 3, tw + 10, th); }
    ctx.fillStyle = color; ctx.textBaseline = 'top';
    lines.forEach(function (ln, i) { ctx.fillText(ln, a.x, a.y + i * (fs + 4)); });
    ctBoxes[d.id] = {x: a.x - 5, y: a.y - 3, w: tw + 10, h: th};
    if (sel) { ctx.setLineDash([3, 3]); ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.strokeRect(a.x - 5, a.y - 3, tw + 10, th); }
  } else if (type === 'measure') {
    var up = d.points[1].p >= d.points[0].p, mc = up ? '#2f8cff' : '#ff4d6a';
    var mx = Math.min(a.x, b.x), my = Math.min(a.y, b.y), mw = Math.abs(b.x - a.x), mh = Math.abs(b.y - a.y);
    ctx.fillStyle = ctHexA(mc, 0.16); ctx.fillRect(mx, my, mw, mh);
    ctx.strokeStyle = mc; ctx.lineWidth = 1; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo((a.x + b.x) / 2, a.y); ctx.lineTo((a.x + b.x) / 2, b.y); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(a.x, (a.y + b.y) / 2); ctx.lineTo(b.x, (a.y + b.y) / 2); ctx.stroke();
    var dp = d.points[1].p - d.points[0].p, pct = d.points[0].p ? dp / d.points[0].p * 100 : 0;
    var nb = Math.round((ctTimeToLogical(d.points[1].t) || 0) - (ctTimeToLogical(d.points[0].t) || 0));
    var secs = Math.abs(d.points[1].t - d.points[0].t), span = secs >= 86400 ? (secs / 86400).toFixed(1) + 'd' : (secs >= 3600 ? (secs / 3600).toFixed(1) + 'h' : Math.round(secs / 60) + 'm');
    var line1 = (pct >= 0 ? '▲ +' : '▼ ') + pct.toFixed(2) + '%  (' + (dp >= 0 ? '+' : '') + dp.toFixed(2) + ')', line2 = Math.abs(nb) + ' bars, ' + span;
    var ly = up ? my - 6 : my + mh + 6;
    ctLabelText(ctx, line1 + '   ' + line2, mx + mw / 2, ly, '#ffffff', {align: 'center', baseline: up ? 'bottom' : 'top', bg: ctHexA(mc, 0.92)});
    ctBoxes[d.id] = {x: mx, y: my, w: mw, h: mh};
    if (sel) { axisLabels.push({p: d.points[0].p, c: mc}); axisLabels.push({p: d.points[1].p, c: mc}); ctHandles(ctx, [a, b], mc); }
  } else if (type === 'long' || type === 'short') {
    ctDrawPosition(ctx, d, dim, sel, pts, axisLabels, isDraft);
  } else if (type === 'avwap') {
    ctDrawAvwap(ctx, d, dim, sel, a, axisLabels);
  } else if (type === 'pctlevels') {
    ctDrawPctLevels(ctx, d, dim, sel, a, axisLabels);
  }
  ctx.restore();
}
function ctPctLevelPrices(d) {
  var A = d.points[0].p, up = !!d.style.reverse;
  return (d.style.levels || []).filter(function (l) { return l.on && isFinite(l.v); }).map(function (l) {
    return {v: Number(l.v), c: l.c || d.style.color, p: up ? A * (1 + l.v / 100) : A * (1 - l.v / 100)};
  }).filter(function (l) { return l.p > 0; });
}
function ctDrawPctLevels(ctx, d, dim, sel, a, axisLabels) {
  var s = d.style, up = !!s.reverse, x1 = s.extendLeft ? 0 : a.x, x2 = dim.W, ys = [a.y];
  var lbl = s.labels !== false, priceTags = s.showPrice !== false || sel;
  // The anchor: the high (or low) everything is measured from.
  ctx.save(); ctx.globalAlpha = 0.9; ctx.strokeStyle = s.color; ctx.lineWidth = 1; ctx.setLineDash([]);
  ctx.beginPath(); ctx.moveTo(x1, a.y); ctx.lineTo(x2, a.y); ctx.stroke(); ctx.restore();
  if (lbl) ctLabelText(ctx, '0%  ' + ctFmt(d.points[0].p) + (up ? '  · from low' : '  · from high'), Math.max(8, x1 + 8), a.y - 4, s.color, {size: 10});
  var levels = ctPctLevelPrices(d), last = ctLastPrice();
  levels.forEach(function (l) {
    var y = ctY(l.p); if (y == null) return;
    ys.push(y);
    ctStroke(ctx, {color: l.c, width: s.width, dash: s.dash});
    ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
    if (s.fillOpacity > 0.01) { ctx.globalAlpha = 1; ctx.fillStyle = ctHexA(l.c, s.fillOpacity / 3); ctx.fillRect(x1, Math.min(y, a.y), x2 - x1, Math.abs(y - a.y)); }
    ctx.globalAlpha = 1;
    var name = (up ? '+' : '−') + (Math.round(l.v * 100) / 100) + '%';
    var hit = last != null && (up ? last >= l.p : last <= l.p);
    // Labels sit at the right, next to the price scale, like TradingView.
    if (lbl) ctLabelText(ctx, name + '  ' + ctFmt(l.p) + (hit ? '  ✓ hit' : ''), dim.W - 34, y - 4, l.c, {weight: '700', align: 'right'});
    if (priceTags) axisLabels.push({p: l.p, c: l.c, text: (Math.round(l.v * 100) / 100) + '%'});
  });
  ctPaths[d.id] = {kind: 'fib', x1: x1, x2: x2, ys: ys, diag: []};
  if (priceTags) axisLabels.push({p: d.points[0].p, c: s.color});
  if (sel) ctHandles(ctx, [a], s.color);
}
function ctDrawFibs(ctx, d, dim, sel, pts, axisLabels, isDraft) {
  var s = d.style, ext = d.type === 'fibext', A = d.points[0], B = d.points[1], C = d.points[2];
  if (ext && !C) {
    if (isDraft && ctDraft && ctDraft.points[2]) C = ctDraft.points[2]; else C = B;
  }
  var a = pts[0], b = pts[1], c = C ? ctXY(C) : null;
  var x1, x2;
  if (ext) { if (!c) return; x1 = c.x; x2 = s.extendRight ? dim.W : c.x + Math.max(60, Math.abs(b.x - a.x)); }
  else { x1 = Math.min(a.x, b.x); x2 = s.extendRight ? dim.W : Math.max(a.x, b.x); }
  if (s.extendLeft) x1 = 0;
  var levels = (s.levels || []).filter(function (l) { return l.on; }).slice().sort(function (p, q) { return p.v - q.v; });
  var priceOf = function (v) {
    if (ext) return C.p + (B.p - A.p) * v;
    return s.reverse ? A.p + (B.p - A.p) * v : B.p + (A.p - B.p) * v;
  };
  var rows = levels.map(function (l) { var p = priceOf(l.v); return {l: l, p: p, y: ctY(p)}; }).filter(function (r) { return r.y != null; });
  if (s.fillOpacity > 0) {
    for (var i = 0; i < rows.length - 1; i++) {
      ctx.fillStyle = ctHexA(rows[i].l.c || s.color, s.fillOpacity);
      ctx.fillRect(x1, Math.min(rows[i].y, rows[i + 1].y), x2 - x1, Math.abs(rows[i + 1].y - rows[i].y));
    }
  }
  rows.forEach(function (r) {
    var col = r.l.c || s.color;
    ctx.strokeStyle = col; ctx.lineWidth = Number(s.width) || 1; ctx.setLineDash(ctDashArr(s.dash, ctx.lineWidth)); ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.moveTo(x1, r.y); ctx.lineTo(x2, r.y); ctx.stroke();
    if (s.labels !== false) {
      var lv = (Math.round(r.l.v * 1000) / 1000).toString();
      ctLabelText(ctx, lv + ' (' + ctFmt(r.p) + ')', x1 + 4, r.y - 2, col, {size: 10});
    }
    if (s.showPrice && ctLbl('drawings')) axisLabels.push({p: r.p, c: col});
  });
  // The dashed swing line between the anchors can be switched off (the
  // "╱" button in the properties bar). While it's selected it still shows
  // faintly, so you can see what the levels are measured from.
  var swing = s.swingLine !== false;
  if (swing || sel) {
    ctx.setLineDash([4, 4]); ctx.strokeStyle = ctHexA(s.color, swing ? 0.7 : 0.25); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); if (ext && c) ctx.lineTo(c.x, c.y); ctx.stroke();
  }
  // Selectable on its level lines and the dashed swing line, not the whole
  // box, so lines and drawings inside a fib can still be picked.
  ctPaths[d.id] = {kind: 'fib', ys: rows.map(function (r) { return r.y; }), x1: x1, x2: x2, diag: swing ? (ext && c ? [a, b, c] : [a, b]) : []};
  if (sel) {
    if (!s.showPrice) { axisLabels.push({p: A.p, c: s.color}); axisLabels.push({p: B.p, c: s.color}); }
    ctHandles(ctx, ext && c ? [a, b, c] : [a, b], s.color);
  }
}
function ctPositionGeom(d, dim) {
  var A = d.points[0], B = d.points[1];
  var a = ctXY(A), xb = ctX(B.t), yt = ctY(B.p), ys = ctY(d.stop);
  if (!a || xb == null || yt == null || ys == null) return null;
  var x1 = Math.min(a.x, xb), x2 = Math.max(a.x, xb);
  if (x2 - x1 < 30) x2 = x1 + 30;
  return {a: a, x1: x1, x2: x2, yt: yt, ys: ys, xb: xb};
}
function ctDrawPosition(ctx, d, dim, sel, pts, axisLabels) {
  var g = ctPositionGeom(d, dim);
  if (!g) return;
  var entry = d.points[0].p, target = d.points[1].p, stop = d.stop, long = d.type === 'long';
  ctx.fillStyle = 'rgba(34,197,139,0.20)'; ctx.fillRect(g.x1, Math.min(g.a.y, g.yt), g.x2 - g.x1, Math.abs(g.yt - g.a.y));
  ctx.fillStyle = 'rgba(255,77,69,0.20)'; ctx.fillRect(g.x1, Math.min(g.a.y, g.ys), g.x2 - g.x1, Math.abs(g.ys - g.a.y));
  ctx.setLineDash([]); ctx.lineWidth = 1;
  ctx.strokeStyle = '#8d9bb0'; ctx.beginPath(); ctx.moveTo(g.x1, g.a.y); ctx.lineTo(g.x2, g.a.y); ctx.stroke();
  var reward = Math.abs(target - entry), risk = Math.abs(entry - stop), rr = risk > 0 ? reward / risk : 0;
  var tPct = entry ? (target - entry) / entry * 100 : 0, sPct = entry ? (stop - entry) / entry * 100 : 0;
  var qty = '';
  try {
    var g2 = JSON.parse(localStorage.getItem('ts_gate') || '{}'), acct = Number(g2.acct) || 0, rp = Number(g2.risk) || 1;
    if (acct > 0 && risk > 0) {
      var q = Math.floor(acct * rp / 100 / risk);
      qty = ' · ' + q + ' sh · risk $' + Math.round(q * risk).toLocaleString();
    }
  } catch (e) {}
  var cx = (g.x1 + g.x2) / 2;
  ctLabelText(ctx, 'Target ' + ctFmt(target) + ' (' + (tPct >= 0 ? '+' : '') + tPct.toFixed(2) + '%)', cx, g.yt + (g.yt < g.a.y ? -4 : 4), '#ffffff', {align: 'center', baseline: g.yt < g.a.y ? 'bottom' : 'top', bg: 'rgba(22,150,105,.92)', size: 10});
  ctLabelText(ctx, 'Stop ' + ctFmt(stop) + ' (' + (sPct >= 0 ? '+' : '') + sPct.toFixed(2) + '%)', cx, g.ys + (g.ys > g.a.y ? 4 : -4), '#ffffff', {align: 'center', baseline: g.ys > g.a.y ? 'top' : 'bottom', bg: 'rgba(200,52,52,.92)', size: 10});
  var midText = (long ? 'Long' : 'Short') + ' ' + ctFmt(entry) + ' · R:R ' + rr.toFixed(2) + qty;
  ctx.font = '600 10px "IBM Plex Mono", ui-monospace, monospace';
  var half = ctx.measureText(midText).width / 2 + 8;
  cx = Math.max(half, Math.min(dim.W - half, cx));            // keep it off the price axis
  ctLabelText(ctx, midText, cx, g.a.y, '#ffffff', {align: 'center', baseline: 'middle', bg: 'rgba(30,38,52,.94)', size: 10});
  ctBoxes[d.id] = {x: g.x1, y: Math.min(g.yt, g.ys), w: g.x2 - g.x1, h: Math.abs(g.yt - g.ys)};
  if (ctPriceLabels(d, sel)) {
    axisLabels.push({p: target, c: '#169669'}); axisLabels.push({p: entry, c: '#5b6b82'}); axisLabels.push({p: stop, c: '#c83434'});
  }
  if (sel) ctHandles(ctx, [g.a, {x: g.xb, y: g.yt}, {x: g.xb, y: g.ys}], '#d6e0ec');
}
function ctDrawAvwap(ctx, d, dim, sel, a, axisLabels) {
  var bars = twChartState.bars || [], arr = ctBarSecs(), t0 = d.points[0].t, start = 0;
  while (start < arr.length && arr[start] < t0 - 1) start++;
  if (start >= bars.length) return;
  var pv = 0, vv = 0, path = [], last = null;
  ctStroke(ctx, d.style); ctx.beginPath();
  for (var i = start; i < bars.length; i++) {
    var b = bars[i], v = Number(b.volume) || 0, tp = (Number(b.high) + Number(b.low) + Number(b.close)) / 3;
    if (v > 0 && isFinite(tp)) { pv += tp * v; vv += v; }
    if (!vv) continue;
    last = pv / vv;
    var x = twChartState.chart.timeScale().logicalToCoordinate(i), y = ctY(last);
    if (x == null || y == null) continue;
    if (!path.length) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    path.push({x: x, y: y});
  }
  ctx.stroke();
  ctPaths[d.id] = {kind: 'path', pts: path};
  if (last != null && path.length) {
    var end = path[path.length - 1];
    ctLabelText(ctx, 'AVWAP ' + ctFmt(last), Math.min(end.x + 6, dim.W - 90), end.y - 4, d.style.color, {size: 10});
    if (d.style.showPrice !== false || sel) axisLabels.push({p: last, c: d.style.color});
  }
  if (sel && path.length) ctHandles(ctx, [path[0]], d.style.color);
}

/* ═══ Hit testing ═══ */
function ctDistSeg(p, a, b) {
  var dx = b.x - a.x, dy = b.y - a.y, len = dx * dx + dy * dy;
  if (!len) return Math.hypot(p.x - a.x, p.y - a.y);
  var t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}
function ctInPoly(p, poly) {
  var inside = false;
  for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    var xi = poly[i].x, yi = poly[i].y, xj = poly[j].x, yj = poly[j].y;
    if (((yi > p.y) !== (yj > p.y)) && (p.x < (xj - xi) * (p.y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}
function ctHandlePoints(d) {
  var a = ctXY(d.points[0]), b = d.points[1] ? ctXY(d.points[1]) : null;
  switch (d.type) {
    case 'rect': return a && b ? [a, b, {x: a.x, y: b.y}, {x: b.x, y: a.y}] : [];
    case 'channel': {
      var a2 = ctXY({t: d.points[0].t, p: d.points[0].p + (d.off || 0)}), b2 = ctXY({t: d.points[1].t, p: d.points[1].p + (d.off || 0)});
      return [a, b, a2 && b2 ? {x: (a2.x + b2.x) / 2, y: (a2.y + b2.y) / 2} : null];
    }
    case 'long': case 'short': {
      var xb = ctX(d.points[1].t), yt = ctY(d.points[1].p), ys = ctY(d.stop);
      return [a, xb != null && yt != null ? {x: xb, y: yt} : null, xb != null && ys != null ? {x: xb, y: ys} : null];
    }
    case 'vline': return a ? [{x: a.x, y: a.y}] : [];
    case 'text': return [];
    case 'avwap': { var pth = ctPaths[d.id]; return pth && pth.pts && pth.pts.length ? [pth.pts[0]] : []; }
    default: return d.points.map(ctXY);
  }
}
function ctHit(x, y) {
  if (ctPrefs.draw.hidden) return null;
  var p = {x: x, y: y}, list = ctStore.drawings, order = [], best = null;
  // Selected drawing first so its handles win over anything underneath.
  if (twChartState.selectedDrawing != null) order.push(twChartState.selectedDrawing);
  for (var k = list.length - 1; k >= 0; k--) if (k !== twChartState.selectedDrawing) order.push(k);
  for (var n = 0; n < order.length; n++) {
    var d = list[order[n]];
    if (!d || d.hidden) continue;
    var hs = ctHandlePoints(d);
    for (var h = 0; h < hs.length; h++) if (hs[h] && Math.hypot(hs[h].x - x, hs[h].y - y) <= 9) return {index: order[n], part: h};
  }
  // Otherwise the nearest drawing under the pointer (ties go to the top one).
  for (var m = 0; m < order.length; m++) {
    var i = order[m], dd = list[i];
    if (!dd || dd.hidden) continue;
    var dist = ctBodyDist(dd, p);
    if (dist != null && dist <= 6 && (!best || dist < best.dist)) best = {index: i, part: 'body', dist: dist};
  }
  return best ? {index: best.index, part: 'body'} : null;
}
function ctBodyDist(d, p) {
  var path = ctPaths[d.id], box = ctBoxes[d.id], x = p.x, y = p.y, out = null;
  var take = function (v) { if (v != null && (out == null || v < out)) out = v; };
  if (path) {
    if (path.kind === 'seg') take(ctDistSeg(p, path.a, path.b));
    if (path.kind === 'h' && x >= path.x0 - 6) take(Math.abs(y - path.y));
    if (path.kind === 'v') take(Math.abs(x - path.x));
    if (path.kind === 'poly' && ctInPoly(p, path.pts)) take(4);
    if (path.kind === 'fib') {
      if (x >= Math.min(path.x1, path.x2) - 4 && x <= Math.max(path.x1, path.x2) + 4) path.ys.forEach(function (ly) { take(Math.abs(y - ly)); });
      for (var q = 1; q < path.diag.length; q++) take(ctDistSeg(p, path.diag[q - 1], path.diag[q]));
    }
    if (path.kind === 'path') for (var s = 1; s < path.pts.length; s++) take(ctDistSeg(p, path.pts[s - 1], path.pts[s]));
  }
  if (box && x >= box.x - 4 && x <= box.x + box.w + 4 && y >= box.y - 4 && y <= box.y + box.h + 4) take(4);
  (ctLabelHits[d.id] || []).forEach(function (b) {
    if (x >= b.x - 3 && x <= b.x + b.w + 3 && y >= b.y - 3 && y <= b.y + b.h + 3) take(3);
  });
  return out;
}

/* ═══ Pointer handling ═══ */
var ctMode = null;  // active tool, or null for the cursor
function ctCanvas() { return document.getElementById('tw-chart-drawing'); }
function ctLocal(ev) {
  var r = ctCanvas().getBoundingClientRect();
  return {x: ev.clientX - r.left, y: ev.clientY - r.top};
}
function twStartDrawing(mode) {
  if (mode === 'select' || !CT_TOOLS[mode]) { twStopDrawing(); return; }
  if (ctMode === mode) { twStopDrawing(); return; }
  ctMode = mode; twChartState.drawMode = mode; ctDraft = null;
  var cv = ctCanvas(); if (cv) cv.classList.add('active');
  ctSyncRail();
  var tool = CT_TOOLS[mode], st = document.getElementById('tw-draw-status');
  if (st) {
    st.hidden = false;
    st.textContent = mode === 'pctlevels' ? '% drop levels: click the high to measure from (a low measures % up). Esc cancels.' : tool.label + ': ' + (tool.n === 1 ? 'click the chart to place it.' : (tool.n === 3 ? 'click (or drag) the first two points, then click the third.' : 'click two points, or press and drag.')) + ' Esc cancels.';
  }
  twRenderDrawings();
}
function twStopDrawing() {
  ctMode = null; twChartState.drawMode = null; ctDraft = null;
  var cv = ctCanvas(); if (cv) { cv.classList.remove('active'); cv.classList.toggle('ct-armed', twChartState.selectedDrawing != null && ctTouchSeen); }
  var st = document.getElementById('tw-draw-status'); if (st) st.hidden = true;
  ctSyncRail();
  twRenderDrawings();
}
var ctClearArmed = 0;
function twClearDrawings() {
  if (!ctStore.drawings.length) return;
  // Two taps instead of a browser confirm(): some app windows block dialogs,
  // which made Clear all silently do nothing.
  var btn = document.querySelector('#ct-rail [data-ct-act="clear"]'), now = Date.now();
  if (now - ctClearArmed > 3000) {
    ctClearArmed = now;
    if (btn) { btn.classList.add('ct-armed-clear'); btn.title = 'Tap again to remove all ' + ctStore.drawings.length + ' drawings on ' + (ctStore.ticker || 'this chart'); }
    var st = document.getElementById('tw-draw-status');
    if (st) { st.hidden = false; st.textContent = 'Tap ✕ again to remove all ' + ctStore.drawings.length + ' drawings on ' + (ctStore.ticker || 'this chart') + ' (undo brings them back).'; }
    setTimeout(function () { if (Date.now() - ctClearArmed >= 2900) { ctClearArmed = 0; if (btn) { btn.classList.remove('ct-armed-clear'); btn.title = 'Remove all drawings on this symbol'; } if (st && !ctMode) st.hidden = true; } }, 3000);
    return;
  }
  ctClearArmed = 0;
  if (btn) { btn.classList.remove('ct-armed-clear'); btn.title = 'Remove all drawings on this symbol'; }
  ctPushUndo(); ctStore.drawings = []; twChartState.selectedDrawing = null;
  twSaveDrawings(); twStopDrawing();
}
function twDeleteSelected() {
  var i = twChartState.selectedDrawing;
  if (i == null || !ctStore.drawings[i]) return;
  ctPushUndo(); ctStore.drawings.splice(i, 1); twChartState.selectedDrawing = null;
  ctArm(false); twSaveDrawings(); twRenderDrawings();
}
function ctSelect(i) {
  twChartState.selectedDrawing = i;
  ctArm(i != null && ctTouchSeen);
  ctLevelsOpen = false;
  twRenderDrawings();
}
function ctArm(on) { var cv = ctCanvas(); if (cv) cv.classList.toggle('ct-armed', !!on); }
var ctTouchSeen = false;

/* % drop levels measure from a real price: a click near a candle's high
   lands on that high (near its low, on the low — and flips to "% up"). */
function ctSnapExtreme(pt) {
  var bars = twChartState.bars || [], l = ctTimeToLogical(pt.t);
  if (l == null) return pt;
  var i = Math.max(0, Math.min(bars.length - 1, Math.round(l))), b = bars[i];
  if (!b) return pt;
  var hi = Number(b.high), lo = Number(b.low), yh = ctY(hi), yl = ctY(lo), y = ctY(pt.p);
  if (y == null || yh == null || yl == null) return pt;
  if (Math.abs(y - yh) <= 30 && Math.abs(y - yh) <= Math.abs(y - yl)) return {t: pt.t, p: hi};
  if (Math.abs(y - yl) <= 30) return {t: pt.t, p: lo, low: true};
  return pt;
}
function ctFinishDraft() {
  var d = ctNewDrawing(ctDraft.type, ctDraft.points);
  if (d.type === 'pctlevels' && ctDraft.points[0].low) d.style.reverse = true;
  ctPushUndo();
  ctStore.drawings.push(d);
  if (ctStore.drawings.length > 300) ctStore.drawings.shift();
  ctDraft = null;
  twSaveDrawings();
  if (ctPrefs.draw.stay && d.type !== 'text') { twRenderDrawings(); return; }
  twStopDrawing();
  ctSelect(ctStore.drawings.length - 1);
  if (d.type === 'text') setTimeout(function () { var inp = document.getElementById('ct-p-text'); if (inp) { inp.focus(); inp.select(); } }, 30);
}
function ctMoveBy(orig, dl, dp) {
  var l = ctTimeToLogical(orig.t);
  return {t: l == null ? orig.t : ctLogicalToTime(l + dl), p: orig.p + dp};
}
function ctApplyDrag(d, act, cur) {
  var pt = ctPointAt(cur.x, cur.y, true);
  if (!pt) return;
  var o = act.orig;
  if (act.part === 'body') {
    var l0 = act.startL, p0 = act.startP, l1 = ctLogicalAt(cur.x), p1 = ctPriceAt(cur.y);
    if (l0 == null || l1 == null || p1 == null) return;
    var dl = l1 - l0, dp = p1 - p0;
    if (d.type === 'hline') { d.points = [{t: o.points[0].t, p: o.points[0].p + dp}]; return; }
    if (d.type === 'vline') { d.points = [ctMoveBy(o.points[0], dl, 0)]; return; }
    d.points = o.points.map(function (q) { return ctMoveBy(q, dl, dp); });
    if (d.type === 'long' || d.type === 'short') d.stop = o.stop + dp;
    return;
  }
  var h = act.part;
  switch (d.type) {
    case 'hline': d.points[0] = {t: o.points[0].t, p: pt.p}; break;
    case 'vline': d.points[0] = {t: pt.t, p: o.points[0].p}; break;
    case 'rect':
      if (h === 0) d.points[0] = pt; else if (h === 1) d.points[1] = pt;
      else if (h === 2) { d.points[0] = {t: pt.t, p: o.points[0].p}; d.points[1] = {t: o.points[1].t, p: pt.p}; }
      else { d.points[1] = {t: pt.t, p: o.points[1].p}; d.points[0] = {t: o.points[0].t, p: pt.p}; }
      break;
    case 'channel':
      if (h === 2) d.off = ctChannelOffset(d.points[0], d.points[1], pt); else d.points[h] = pt;
      break;
    case 'long': case 'short':
      if (h === 0) d.points[0] = pt;
      else if (h === 1) d.points[1] = pt;
      else d.stop = pt.p;
      break;
    default: d.points[h] = pt;
  }
}

function ctInitPointer() {
  var cv = ctCanvas(), box = document.getElementById('tw-chart-box');
  if (!cv || !box || cv._ctBound) return;
  cv._ctBound = true;
  // Drag the chart up or down (like TradingView). lightweight-charts only
  // pans vertically while the price axis is in manual mode, so a press on
  // the chart switches it off auto; if the drag turns out mostly sideways
  // (or was just a click) auto comes straight back on release.
  box.addEventListener('pointerdown', function (ev) {
    if (ctMode || ev.shiftKey || ev.button > 0 || ev.target === cv || !twChartState.chart) return;
    var c = ctLocal(ev), dim = ctDims();
    if (c.x < 0 || c.y < 0 || c.x > dim.W || c.y > dim.H) return;     // the plot only, not the axes
    var ps; try { ps = twChartState.chart.priceScale('right'); } catch (e) { return; }
    if (!ps.options().autoScale) return;                              // already manual: the library pans it
    ps.applyOptions({autoScale: false});
    var x0 = ev.clientX, y0 = ev.clientY, maxDy = 0, maxDx = 0;
    function mv(e) { maxDy = Math.max(maxDy, Math.abs(e.clientY - y0)); maxDx = Math.max(maxDx, Math.abs(e.clientX - x0)); }
    function up() {
      window.removeEventListener('pointermove', mv, true); window.removeEventListener('pointerup', up, true); window.removeEventListener('pointercancel', up, true);
      if (maxDy < 14 || maxDy < maxDx * 0.35) ps.applyOptions({autoScale: true});
      ctSyncAutoBtn();
    }
    window.addEventListener('pointermove', mv, true);
    window.addEventListener('pointerup', up, true);
    window.addEventListener('pointercancel', up, true);
  }, true);
  // Mouse wheel / pinch on the price axis also leaves auto: keep the A button honest.
  box.addEventListener('wheel', function () { setTimeout(ctSyncAutoBtn, 60); }, {passive: true});
  box.addEventListener('dblclick', function () { setTimeout(ctSyncAutoBtn, 60); });
  ctSyncAutoBtn();
  // Shift + drag anywhere on the chart: a quick % measure (like TradingView).
  // It stays up until the next click or Esc and is never saved.
  box.addEventListener('pointerdown', function (ev) {
    if (ctQuick && !ev.shiftKey) { ctQuick = null; twRenderDrawings(); }
    if (!ev.shiftKey || ctMode || ev.button > 0 || !twChartState.chart || !(twChartState.bars || []).length) return;
    var c = ctLocal(ev), dim = ctDims();
    if (c.x < 0 || c.y < 0 || c.x > dim.W || c.y > dim.H) return;
    var pt = ctPointAt(c.x, c.y, true); if (!pt) return;
    ev.preventDefault(); ev.stopPropagation();
    ctQuick = {points: [pt, {t: pt.t, p: pt.p}], live: true};
    function mv(e) { if (!ctQuick || !ctQuick.live) return; var q = ctPointAt(ctLocal(e).x, ctLocal(e).y, true); if (q) { ctQuick.points[1] = q; twRenderDrawings(); } }
    function done() { if (ctQuick) ctQuick.live = false; window.removeEventListener('pointermove', mv, true); window.removeEventListener('pointerup', done, true); window.removeEventListener('pointercancel', done, true); }
    window.addEventListener('pointermove', mv, true);
    window.addEventListener('pointerup', done, true);
    window.addEventListener('pointercancel', done, true);
    twRenderDrawings();
  }, true);
  cv.addEventListener('pointerdown', function (ev) {
    if (ev.pointerType === 'touch' || ev.pointerType === 'pen') ctTouchSeen = true;
    if (!twChartState.chart || !(twChartState.bars || []).length) return;
    if (!ctMode && ctAlertClick(ev)) { ev.preventDefault(); return; }
    var c = ctLocal(ev);
    if (!ctMode) {
      var evHit = ctEventAt(c.x, c.y);
      if (evHit) {
        ev.preventDefault();
        var withUrl = (evHit.items || []).filter(function (e) { return e.url; })[0];
        if (withUrl && (ev.pointerType === 'mouse' || document.getElementById('ct-evtip') && !document.getElementById('ct-evtip').hidden)) window.open(withUrl.url, '_blank', 'noopener');
        else ctEventTip(evHit, c.x, c.y);
        return;
      }
    }
    if (ctMode) {
      ev.preventDefault();
      var pt = ctPointAt(c.x, c.y, true); if (!pt) return;
      var n = CT_TOOLS[ctMode].n;
      if (!ctDraft) {
        if (ctMode === 'pctlevels') pt = ctSnapExtreme(pt);
        if (n === 1) { ctDraft = {type: ctMode, points: [pt]}; ctFinishDraft(); return; }
        ctDraft = {type: ctMode, points: [pt, {t: pt.t, p: pt.p}], stage: 'drag', down: c};
        try { cv.setPointerCapture(ev.pointerId); } catch (e) {}
      } else {
        // A second click on (almost) the same spot would make a drawing with
        // no size that is nearly impossible to grab again — wait for a real one.
        var prev = ctXY(ctDraft.points[ctDraft.points.length - 2]), here = ctXY(pt);
        if (prev && here && Math.hypot(prev.x - here.x, prev.y - here.y) < 8) return;
        ctDraft.points[ctDraft.points.length - 1] = pt;
        if (ctDraft.points.length >= n) { ctFinishDraft(); return; }
        ctDraft.points.push({t: pt.t, p: pt.p});
      }
      twRenderDrawings();
      return;
    }
    var hit = ctHit(c.x, c.y);
    if (!hit) { ctSelect(null); ctArm(false); cv.classList.remove('ct-hover'); return; }
    ev.preventDefault();
    if (hit.index !== twChartState.selectedDrawing) ctSelect(hit.index);
    var d = ctStore.drawings[hit.index];
    if (!d || d.locked) return;
    ctAction = {index: hit.index, part: hit.part, orig: JSON.parse(JSON.stringify(d)), snap: ctSnapshot(),
      startL: ctLogicalAt(c.x), startP: ctPriceAt(c.y), down: c, moved: false};
    try { cv.setPointerCapture(ev.pointerId); } catch (e) {}
  });
  cv.addEventListener('pointermove', function (ev) {
    var c = ctLocal(ev);
    if (ctDraft) {
      var pt = ctPointAt(c.x, c.y, true); if (!pt) return;
      ctDraft.points[ctDraft.points.length - 1] = pt;
      twRenderDrawings(); return;
    }
    if (ctAction) {
      if (!ctAction.moved && Math.hypot(c.x - ctAction.down.x, c.y - ctAction.down.y) < 3) return;
      ctAction.moved = true;
      var d = ctStore.drawings[ctAction.index]; if (!d) return;
      ctApplyDrag(d, ctAction, c);
      twRenderDrawings();
    }
  });
  function up(ev) {
    var c = ctLocal(ev);
    if (ctDraft && ctDraft.stage === 'drag') {
      var moved = Math.hypot(c.x - ctDraft.down.x, c.y - ctDraft.down.y) > 6;
      ctDraft.stage = 'place';
      if (moved) {
        if (ctDraft.points.length >= CT_TOOLS[ctDraft.type].n) ctFinishDraft();
        else ctDraft.points.push(Object.assign({}, ctDraft.points[ctDraft.points.length - 1]));
      }
    }
    if (ctAction) {
      if (ctAction.moved && ctAction.snap !== ctSnapshot()) { ctPushUndo(ctAction.snap); twSaveDrawings(); }
      ctAction = null;
    }
    try { cv.releasePointerCapture(ev.pointerId); } catch (e) {}
    twRenderDrawings();
  }
  cv.addEventListener('pointerup', up);
  cv.addEventListener('pointercancel', up);
  cv.addEventListener('dblclick', function (ev) {
    var c = ctLocal(ev), hit = ctHit(c.x, c.y);
    if (hit && ctStore.drawings[hit.index] && ctStore.drawings[hit.index].type === 'text') {
      var inp = document.getElementById('ct-p-text'); if (inp) { inp.focus(); inp.select(); }
    }
  });
  // Mouse: a drawing under the pointer takes the click; anywhere else the
  // chart keeps its own pan and zoom.
  box.addEventListener('pointermove', function (ev) {
    if (ev.pointerType !== 'mouse' || ctMode || ctAction || ctDraft) return;
    var c = ctLocal(ev), hit = ctHit(c.x, c.y), onAlert = ctOverAlert(c.x, c.y), onEvent = ctEventAt(c.x, c.y);
    ctEventTip(onEvent, c.x, c.y);
    cv.classList.toggle('ct-hover', !!hit || onAlert || !!onEvent);
    cv.style.cursor = (onAlert || onEvent) ? 'pointer' : hit ? (hit.part === 'body' ? 'move' : 'pointer') : '';
  });
  box.addEventListener('pointerleave', function () { if (!ctAction) cv.classList.remove('ct-hover'); ctEventTip(null); });
  document.addEventListener('keydown', function (ev) {
    var tag = (ev.target && ev.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || (ev.target && ev.target.isContentEditable)) return;
    if (!document.getElementById('tw-chart-box')) return;
    if ((ev.key === 'Delete' || ev.key === 'Backspace') && twChartState.selectedDrawing != null) { ev.preventDefault(); twDeleteSelected(); }
    else if (ev.key === 'Escape' && ctQuick) { ctQuick = null; twRenderDrawings(); }
    else if (ev.key === 'Escape') { if (ctMode || ctDraft) twStopDrawing(); else if (twChartState.selectedDrawing != null) ctSelect(null); }
    else if ((ev.metaKey || ev.ctrlKey) && (ev.key === 'z' || ev.key === 'Z') && !ev.shiftKey) { if (ctStore.undo.length) { ev.preventDefault(); ctUndo(); } }
    else if ((ev.metaKey || ev.ctrlKey) && (((ev.key === 'z' || ev.key === 'Z') && ev.shiftKey) || ev.key === 'y' || ev.key === 'Y')) { if (ctStore.redo && ctStore.redo.length) { ev.preventDefault(); ctRedo(); } }
    else if (ctShortcut(ev)) ev.preventDefault();
  });
}
/* Keep drawings glued to the chart. The chart reports time-axis moves but not
   price-axis moves (auto-scale while panning, vertical drag, dragging the
   price scale), so each frame compares where two reference prices and the
   visible bars sit on screen and redraws when anything moved. */
var ctViewSig = '';
function ctViewSignature() {
  var s = ctSeries(), ch = twChartState.chart, bars = twChartState.bars || [];
  if (!s || !ch || !bars.length) return '';
  var ref = Number(bars[bars.length - 1].close) || 1, r = null;
  try { r = ch.timeScale().getVisibleLogicalRange(); } catch (e) {}
  var y1 = s.priceToCoordinate(ref), y2 = s.priceToCoordinate(ref * 1.01), d = ctDims();
  return [r ? r.from.toFixed(3) : '', r ? r.to.toFixed(3) : '', y1 == null ? '' : y1.toFixed(2), y2 == null ? '' : y2.toFixed(2),
    d.W, d.H, twChartState.type, bars.length].join('|');
}
function ctCheckVolumeDensity() {
  var ch = twChartState.chart, bars = twChartState.bars || [];
  if (!ch || !bars.length || !twChartState.vol) return;
  var r = null;
  try { r = ch.timeScale().getVisibleLogicalRange(); } catch (e) {}
  if (!r) return;
  var spacing = ctDims().W / Math.max(1, r.to - r.from);
  // Hysteresis so a zoom right at the edge doesn't flicker between the two.
  var dense = ctVolDense ? spacing < 2.2 : spacing < 1.6;
  if (dense === ctVolDense) return;
  ctVolDense = dense;
  try { twChartState.vol.setData(bars.map(function (b) { return {time: b.time, value: b.volume || 0, color: ctVolumeColor(b)}; })); } catch (e) {}
}
function ctWatchView() {
  if (!document.hidden && ctStore.ticker && ctStore.ticker === twChartState.ticker) {
    var sig = ctViewSignature();
    if (sig !== ctViewSig) { ctViewSig = sig; twRenderDrawings(); ctCheckVolumeDensity(); }
  }
  requestAnimationFrame(ctWatchView);
}

function twInitDrawingLayer() {
  ctInitPointer();
  ctBuildRail();
  requestAnimationFrame(ctWatchView);
  var box = document.getElementById('tw-chart-box');
  if (box && window.ResizeObserver) new ResizeObserver(function () { twRenderDrawings(); if (typeof twRenderIndicators === 'function') twRenderIndicators(); }).observe(box);
  else window.addEventListener('resize', function () { twRenderDrawings(); if (typeof twRenderIndicators === 'function') twRenderIndicators(); });
  if (twChartState.chart) {
    twChartState.chart.subscribeClick(function (param) {
      // Touch has no hover, so a tap on a drawing selects it here.
      if (ctMode || !param || !param.point) return;
      var hit = ctHit(param.point.x, param.point.y);
      if (hit) { ctSelect(hit.index); ctArm(true); }
      else if (twChartState.selectedDrawing != null) ctSelect(null);
    });
  }
  ctLoadServerPrefs();
  ctLoadMyDefault();
}

/* ═══ Drawing rail ═══ */
var ctGroupLast = {};
try { ctGroupLast = JSON.parse(localStorage.getItem('tradestaar.drawGroupLast') || '{}') || {}; } catch (e) {}
function ctBuildRail() {
  var host = document.getElementById('ct-rail');
  if (!host || host._built) return;
  host._built = true;
  var html = '<button type="button" class="tw-type-btn ct-tool" data-ct-tool="select" title="Cursor: select, move and edit drawings">' + ctIcon('select') + '<span class="tw-draw-name">Cursor</span></button>';
  CT_GROUPS.forEach(function (g) {
    var cur = g.tools.indexOf(ctGroupLast[g.id]) >= 0 ? ctGroupLast[g.id] : g.tools[0];
    html += '<div class="ct-group" data-ct-group="' + g.id + '">' +
      '<button type="button" class="tw-type-btn ct-tool" data-ct-tool="' + cur + '" title="' + CT_TOOLS[cur].label + '">' + ctIcon(cur) + '<span class="tw-draw-name">' + CT_TOOLS[cur].label + '</span></button>' +
      (g.tools.length > 1 ? '<button type="button" class="ct-more" data-ct-more="' + g.id + '" title="More tools" aria-label="More ' + g.id + ' tools"><svg viewBox="0 0 8 8" width="7" height="7" aria-hidden="true"><path d="M1 7 7 7 7 1z" fill="currentColor"/></svg></button>' : '') +
      '</div>';
  });
  html += '<span class="ct-sep"></span>' +
    '<button type="button" class="tw-type-btn tw-icon-btn ct-toggle" data-ct-act="favbar" title="Favorite tools bar">' + ctIcon('star') + '</button>' +
    '<button type="button" class="tw-type-btn tw-icon-btn ct-toggle" data-ct-act="magnet" title="Magnet: snap to open, high, low and close">' + ctIcon('magnet') + '</button>' +
    '<button type="button" class="tw-type-btn tw-icon-btn ct-toggle" data-ct-act="hide" title="Hide all drawings">' + ctIcon('eye') + '</button>' +
    '<button type="button" class="tw-type-btn tw-icon-btn" data-ct-act="undo" title="Undo (Ctrl/⌘ Z)">' + ctIcon('undo') + '</button>' +
    '<button type="button" class="tw-type-btn tw-icon-btn" data-ct-act="redo" title="Redo (Ctrl/⌘ Shift Z)">' + ctIcon('redo') + '</button>' +
    '<button type="button" class="tw-type-btn tw-icon-btn" data-ct-act="delete" title="Delete selected drawing">' + ctIcon('trash') + '</button>' +
    '<button type="button" class="tw-type-btn tw-icon-btn" data-ct-act="clear" title="Remove all drawings on this symbol">' + ctIcon('clear') + '</button>' +
    '<button type="button" class="tw-type-btn tw-icon-btn" data-ct-act="reset" title="Reset chart view">' + ctIcon('reset') + '</button>';
  host.innerHTML = html;
  host.addEventListener('click', function (ev) {
    var more = ev.target.closest('[data-ct-more]');
    if (more) { ev.stopPropagation(); ctOpenFlyout(more.getAttribute('data-ct-more'), more); return; }
    var tool = ev.target.closest('[data-ct-tool]');
    if (tool) { twStartDrawing(tool.getAttribute('data-ct-tool')); return; }
    var act = ev.target.closest('[data-ct-act]');
    if (!act) return;
    var a = act.getAttribute('data-ct-act');
    if (a === 'favbar') { ctPrefs.favtools.on = !ctPrefs.favtools.on; ctPrefChanged(); ctSyncRail(); }
    else if (a === 'magnet') { ctPrefs.magnet = !ctPrefs.magnet; ctPrefChanged(); ctSyncRail(); }
    else if (a === 'hide') { ctPrefs.draw.hidden = !ctPrefs.draw.hidden; ctPrefChanged(); ctSyncSettingsUI(); ctSyncRail(); twRenderDrawings(); }
    else if (a === 'undo') ctUndo();
    else if (a === 'redo') ctRedo();
    else if (a === 'delete') twDeleteSelected();
    else if (a === 'clear') twClearDrawings();
    else if (a === 'reset' && typeof twResetView === 'function') twResetView();
  });
  // Long-press a group button (touch) opens its menu too.
  host.addEventListener('contextmenu', function (ev) {
    var grp = ev.target.closest('[data-ct-group]');
    if (grp) { ev.preventDefault(); ctOpenFlyout(grp.getAttribute('data-ct-group'), grp); }
  });
  ctSyncRail();
}
/* Popups have to live inside the full-screen element, or the browser
   won't draw them while the chart is in full screen. */
function ctPopupHost() {
  return document.fullscreenElement || document.webkitFullscreenElement || document.body;
}
function ctOpenFlyout(groupId, anchor) {
  var old = document.getElementById('ct-flyout');
  if (old) { var same = old.getAttribute('data-group') === groupId; old.remove(); if (same) return; }
  var g = CT_GROUPS.filter(function (x) { return x.id === groupId; })[0];
  if (!g) return;
  var fly = document.createElement('div');
  fly.id = 'ct-flyout'; fly.className = 'ct-flyout'; fly.setAttribute('data-group', groupId); fly.setAttribute('role', 'menu');
  fly.innerHTML = g.tools.map(function (t) {
    var fav = ctFavList().indexOf(t) >= 0;
    return '<button type="button" role="menuitem" data-ct-pick="' + t + '"' + (ctMode === t ? ' class="on"' : '') + '>' + ctIcon(t, 16) + '<span>' + CT_TOOLS[t].label + '</span>' +
      '<i class="ct-fav-star' + (fav ? ' is-on' : '') + '" data-ct-favstar="' + t + '" role="button" title="' + (fav ? 'Remove from' : 'Add to') + ' favorites bar">' + (fav ? '★' : '☆') + '</i></button>';
  }).join('');
  ctPopupHost().appendChild(fly);
  var r = anchor.getBoundingClientRect(), fw = fly.offsetWidth, fh = fly.offsetHeight;
  var left = r.right + 6, top = r.top - 4;
  if (left + fw > window.innerWidth - 6) left = Math.max(6, r.left);
  if (left === r.left || window.innerWidth < 720) { top = r.bottom + 6; }
  if (top + fh > window.innerHeight - 6) top = Math.max(6, window.innerHeight - fh - 6);
  fly.style.left = left + 'px'; fly.style.top = top + 'px';
  fly.addEventListener('click', function (ev) {
    var star = ev.target.closest('[data-ct-favstar]');
    if (star) {
      ev.stopPropagation();
      var on = ctToggleFavTool(star.getAttribute('data-ct-favstar'));
      star.classList.toggle('is-on', on); star.textContent = on ? '★' : '☆';
      star.title = (on ? 'Remove from' : 'Add to') + ' favorites bar';
      return;
    }
    var b = ev.target.closest('[data-ct-pick]'); if (!b) return;
    var t = b.getAttribute('data-ct-pick');
    ctGroupLast[groupId] = t;
    try { localStorage.setItem('tradestaar.drawGroupLast', JSON.stringify(ctGroupLast)); } catch (e) {}
    fly.remove();
    var btn = document.querySelector('[data-ct-group="' + groupId + '"] .ct-tool');
    if (btn) { btn.setAttribute('data-ct-tool', t); btn.title = CT_TOOLS[t].label; btn.innerHTML = ctIcon(t) + '<span class="tw-draw-name">' + CT_TOOLS[t].label + '</span>'; }
    if (ctMode !== t) twStartDrawing(t);
  });
  setTimeout(function () {
    document.addEventListener('click', function close(e) {
      if (!fly.contains(e.target)) { fly.remove(); document.removeEventListener('click', close); }
    });
  }, 0);
}
function ctSyncRail() {
  document.querySelectorAll('#ct-rail [data-ct-tool]').forEach(function (b) {
    var t = b.getAttribute('data-ct-tool');
    b.classList.toggle('active', t === 'select' ? !ctMode : t === ctMode);
  });
  var mag = document.querySelector('#ct-rail [data-ct-act="magnet"]');
  if (mag) { mag.classList.toggle('active', !!ctPrefs.magnet); mag.setAttribute('aria-pressed', ctPrefs.magnet ? 'true' : 'false'); }
  var fb = document.querySelector('#ct-rail [data-ct-act="favbar"]');
  if (fb) { var fon = !!(ctPrefs.favtools && ctPrefs.favtools.on); fb.classList.toggle('active', fon); fb.setAttribute('aria-pressed', fon ? 'true' : 'false'); fb.title = fon ? 'Hide the favorite tools bar' : 'Show the favorite tools bar'; }
  ctRenderFavBar();
  ctSyncUndoBtns();
  var hide = document.querySelector('#ct-rail [data-ct-act="hide"]');
  if (hide) { hide.classList.toggle('active', !!ctPrefs.draw.hidden); hide.title = ctPrefs.draw.hidden ? 'Show drawings' : 'Hide all drawings'; }
}

/* ═══ Favorite tools bar ═══
   Star a tool in any drawing menu (☆) and it shows on a floating bar on the
   chart. Drag the bar by its handle; it remembers where you left it (as a
   share of the chart, so it stays put across window sizes and full screen).
   The ★ on the rail shows or hides it. Synced with the rest of the chart
   settings. */
function ctFavList() {
  var l = (ctPrefs.favtools && ctPrefs.favtools.list) || [];
  return l.filter(function (t, i) { return CT_TOOLS[t] && l.indexOf(t) === i; });
}
function ctToggleFavTool(t) {
  if (!CT_TOOLS[t]) return false;
  var l = ctFavList(), i = l.indexOf(t);
  if (i >= 0) l.splice(i, 1); else l.push(t);
  ctPrefs.favtools = Object.assign({}, ctPrefs.favtools, {list: l});
  if (i < 0) ctPrefs.favtools.on = true;
  ctPrefChanged(); ctSyncRail();
  return i < 0;
}
function ctRenderFavBar() {
  var box = document.getElementById('tw-chart-box');
  if (!box) return;
  var bar = document.getElementById('ct-favbar'), ft = ctPrefs.favtools || {};
  if (!ft.on) { if (bar) bar.hidden = true; return; }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'ct-favbar'; bar.className = 'ct-favbar'; bar.setAttribute('role', 'toolbar'); bar.setAttribute('aria-label', 'Favorite drawing tools');
    bar.addEventListener('pointerdown', function (e) { e.stopPropagation(); if (e.target.closest('.ct-fb-grip')) ctFavDrag(e, bar, box); });
    bar.addEventListener('click', function (e) {
      e.stopPropagation();
      var b = e.target.closest('[data-ct-fb]'); if (!b) return;
      var t = b.getAttribute('data-ct-fb');
      if (t === '_hide') { ctPrefs.favtools.on = false; ctPrefChanged(); ctSyncRail(); return; }
      if (t === '_undo') { ctUndo(); return; }
      if (t === '_redo') { ctRedo(); return; }
      twStartDrawing(t);
    });
    bar.addEventListener('contextmenu', function (e) {
      var b = e.target.closest('[data-ct-fb]');
      if (b && b.getAttribute('data-ct-fb').charAt(0) !== '_') { e.preventDefault(); ctToggleFavTool(b.getAttribute('data-ct-fb')); }
    });
    box.appendChild(bar);
    bar._key = '';
    if ('ResizeObserver' in window) new ResizeObserver(function () { if (!bar.hidden) ctPlaceFavBar(bar, box); }).observe(box);
  }
  bar.hidden = false;
  var list = ctFavList(), key = list.join(',') + '|' + (ctMode || '');
  if (bar._key !== key) {
    bar._key = key;
    bar.innerHTML = '<span class="ct-fb-grip" title="Drag to move" aria-hidden="true"><svg viewBox="0 0 8 14" width="7" height="13"><g fill="currentColor"><circle cx="2" cy="2" r="1.2"/><circle cx="6" cy="2" r="1.2"/><circle cx="2" cy="7" r="1.2"/><circle cx="6" cy="7" r="1.2"/><circle cx="2" cy="12" r="1.2"/><circle cx="6" cy="12" r="1.2"/></g></svg></span>' +
      (list.length ? list.map(function (t) {
        return '<button type="button" class="ct-fb-tool' + (ctMode === t ? ' active' : '') + '" data-ct-fb="' + t + '" title="' + CT_TOOLS[t].label + ' · right-click to remove" aria-label="' + CT_TOOLS[t].label + '">' + ctIcon(t, 17) + '</button>';
      }).join('') : '<span class="ct-fb-empty">Tap ☆ next to a tool in the drawing menus to add it here</span>') +
      '<span class="ct-fb-sep"></span>' +
      '<button type="button" class="ct-fb-tool" data-ct-fb="_undo" aria-label="Undo">' + ctIcon('undo', 16) + '</button>' +
      '<button type="button" class="ct-fb-tool" data-ct-fb="_redo" aria-label="Redo">' + ctIcon('redo', 16) + '</button>' +
      '<button type="button" class="ct-fb-tool ct-fb-hide" data-ct-fb="_hide" title="Hide this bar (★ on the left brings it back)" aria-label="Hide favorites bar">×</button>';
  }
  ctPlaceFavBar(bar, box);
  ctSyncUndoBtns();
}
function ctPlaceFavBar(bar, box) {
  var ft = ctPrefs.favtools || {}, bw = box.clientWidth, bh = box.clientHeight, w = bar.offsetWidth, h = bar.offsetHeight;
  if (!bw || !bh) return;
  // Default: bottom-left, above the earnings pins and clear of the axis tags.
  var plotH = ctDims().H || bh;
  var x = ft.x == null ? 10 : ft.x * bw, y = ft.y == null ? plotH - h - 30 : ft.y * bh;
  x = Math.max(4, Math.min(bw - w - 4, x)); y = Math.max(4, Math.min(bh - h - 4, y));
  bar.style.left = Math.round(x) + 'px'; bar.style.top = Math.round(y) + 'px';
}
function ctFavDrag(e, bar, box) {
  e.preventDefault();
  var r = bar.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
  bar.classList.add('is-drag');
  function mv(ev) {
    var b = box.getBoundingClientRect();
    ctPrefs.favtools.x = (ev.clientX - b.left - dx) / b.width;
    ctPrefs.favtools.y = (ev.clientY - b.top - dy) / b.height;
    ctPlaceFavBar(bar, box);
  }
  function up() {
    bar.classList.remove('is-drag');
    var b = box.getBoundingClientRect(), br = bar.getBoundingClientRect();
    ctPrefs.favtools.x = (br.left - b.left) / b.width; ctPrefs.favtools.y = (br.top - b.top) / b.height;
    ctPrefChanged();
    window.removeEventListener('pointermove', mv); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up);
  }
  window.addEventListener('pointermove', mv); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up);
}
if (window.addEventListener) window.addEventListener('resize', function () { var bar = document.getElementById('ct-favbar'), box = document.getElementById('tw-chart-box'); if (bar && box && !bar.hidden) ctPlaceFavBar(bar, box); });
document.addEventListener('fullscreenchange', function () { setTimeout(function () { var bar = document.getElementById('ct-favbar'), box = document.getElementById('tw-chart-box'); if (bar && box && !bar.hidden) ctPlaceFavBar(bar, box); }, 120); });

/* ═══ Properties bar for the selected drawing ═══ */
var ctLevelsOpen = false, ctPropsFor = null;
function ctUpdateProps() {
  var box = document.getElementById('tw-chart-box');
  if (!box) return;
  var bar = document.getElementById('ct-props'), d = ctSelected();
  if (!d || ctMode) { if (bar) bar.hidden = true; ctPropsFor = null; return; }
  if (!bar) {
    bar = document.createElement('div'); bar.id = 'ct-props'; bar.className = 'ct-props';
    bar.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
    bar.addEventListener('input', ctPropInput);
    bar.addEventListener('change', ctPropInput);
    bar.addEventListener('click', ctPropClick);
    box.appendChild(bar);
  }
  bar.hidden = false;
  var key = d.id + '|' + ctLevelsOpen;
  if (ctPropsFor === key) return;  // keep focus while typing
  ctPropsFor = key;
  var s = d.style, t = d.type, html = '<span class="ct-p-name">' + CT_TOOLS[t].label + '</span>';
  var hasLine = t !== 'text' && t !== 'measure' && t !== 'long' && t !== 'short';
  if (t !== 'measure' && t !== 'long' && t !== 'short') html += '<label class="ct-p-color" title="' + (t === 'fib' || t === 'fibext' ? 'Color of every level' : 'Color') + '"><input type="color" data-ct-p="color" value="' + ctHex(s.color) + '"></label>';
  if (hasLine) {
    html += '<select data-ct-p="width" title="Line width">' + [1, 2, 3, 4].map(function (w) { return '<option value="' + w + '"' + (Number(s.width) === w ? ' selected' : '') + '>' + w + 'px</option>'; }).join('') + '</select>';
    html += '<select data-ct-p="dash" title="Line style">' + [['solid', '━ Solid'], ['dashed', '╍ Dashed'], ['dotted', '┅ Dotted']].map(function (o) { return '<option value="' + o[0] + '"' + (s.dash === o[0] ? ' selected' : '') + '>' + o[1] + '</option>'; }).join('') + '</select>';
  }
  if (t === 'rect' || t === 'channel' || t === 'fib' || t === 'fibext' || t === 'text') {
    html += '<label class="ct-p-range" title="' + (t === 'text' ? 'Background' : 'Fill opacity') + '"><span>' + (t === 'text' ? 'Bg' : 'Fill') + '</span><input type="range" min="0" max="0.5" step="0.02" data-ct-p="fillOpacity" value="' + (Number(s.fillOpacity) || 0) + '"></label>';
  }
  if (t === 'pctlevels') html += '<button type="button" class="ct-p-btn' + (s.extendLeft ? ' on' : '') + '" data-ct-flag="extendLeft" title="Extend the lines left across the chart">⟵</button>';
  if (t === 'trend' || t === 'ray' || t === 'extended' || t === 'channel' || t === 'fib' || t === 'fibext') {
    html += '<button type="button" class="ct-p-btn' + (s.extendLeft ? ' on' : '') + '" data-ct-flag="extendLeft" title="Extend left">⟵</button>';
    html += '<button type="button" class="ct-p-btn' + (s.extendRight ? ' on' : '') + '" data-ct-flag="extendRight" title="Extend right">⟶</button>';
  }
  if (t === 'text') {
    html += '<input type="text" id="ct-p-text" class="ct-p-text" data-ct-p="text" maxlength="200" value="' + ctEsc(s.text) + '" placeholder="Text">';
    html += '<select data-ct-p="fontSize" title="Text size">' + [11, 13, 16, 20, 26].map(function (f) { return '<option value="' + f + '"' + (Number(s.fontSize) === f ? ' selected' : '') + '>' + f + '</option>'; }).join('') + '</select>';
  } else if (t === 'trend' || t === 'ray' || t === 'extended' || t === 'arrow' || t === 'hline' || t === 'hray' || t === 'vline' || t === 'rect') {
    html += '<input type="text" class="ct-p-text" data-ct-p="text" maxlength="80" value="' + ctEsc(s.text) + '" placeholder="Label">';
  }
  if (t === 'pctlevels') {
    html += '<button type="button" class="ct-p-btn' + (ctLevelsOpen ? ' on' : '') + '" data-ct-cmd="levels" title="Pick the % levels (30%, 40%, 50%…) and their colors">Levels %</button>';
    html += '<button type="button" class="ct-p-btn' + (s.reverse ? ' on' : '') + '" data-ct-flag="reverse" title="' + (s.reverse ? 'Measuring % up from a low — tap for % down from a high' : 'Measuring % down from a high — tap for % up from a low') + '">' + (s.reverse ? '▲ From low' : '▼ From high') + '</button>';
    html += '<button type="button" class="ct-p-btn' + (s.labels !== false ? ' on' : '') + '" data-ct-flag="labels" title="Level labels">Aa</button>';
  }
  if (t === 'fib' || t === 'fibext') {
    html += '<button type="button" class="ct-p-btn' + (ctLevelsOpen ? ' on' : '') + '" data-ct-cmd="levels" title="Edit levels and level colors">Levels</button>';
    if (t === 'fib') html += '<button type="button" class="ct-p-btn' + (s.reverse ? ' on' : '') + '" data-ct-flag="reverse" title="Reverse">⇅</button>';
    html += '<button type="button" class="ct-p-btn' + (s.labels !== false ? ' on' : '') + '" data-ct-flag="labels" title="Level labels">Aa</button>';
    html += '<button type="button" class="ct-p-btn' + (s.swingLine !== false ? ' on' : '') + '" data-ct-flag="swingLine" title="Dashed line between the anchor points">╱</button>';
  }
  if (t !== 'text' && t !== 'vline' && t !== 'measure') {
    var priceOn = (t === 'hline' || t === 'hray' || t === 'avwap') ? s.showPrice !== false : !!s.showPrice;
    html += '<button type="button" class="ct-p-btn ct-p-ico' + (priceOn ? ' on' : '') + '" data-ct-flag="showPrice" title="Show price on the price scale">' + ctIcon('price', 15) + '</button>';
  }
  html += '<button type="button" class="ct-p-btn ct-p-ico' + (d.locked ? ' on' : '') + '" data-ct-cmd="lock" title="' + (d.locked ? 'Unlock' : 'Lock in place') + '">' + ctIcon('lock', 15) + '</button>';
  if (t === 'long' || t === 'short') html += '<button type="button" class="ct-p-btn ct-p-ticket" data-ct-cmd="ticket" title="Fill the order ticket from this position: entry, bracket target and stop, size from your risk %">→ Ticket</button>';
  if (ctAlertPrices(d).length) html += '<button type="button" class="ct-p-btn ct-p-ico" data-ct-cmd="alert" title="Alert me when price reaches this (Alt+W)">' + ctIcon('bell', 15) + '</button>';
  html += '<button type="button" class="ct-p-btn ct-p-ico" data-ct-cmd="clone" title="Clone">' + ctIcon('clone', 15) + '</button>';
  html += '<button type="button" class="ct-p-btn ct-p-ico ct-p-del" data-ct-cmd="delete" title="Delete">' + ctIcon('trash', 15) + '</button>';
  html += '<button type="button" class="ct-p-btn ct-p-ico" data-ct-cmd="close" title="Done" aria-label="Close">×</button>';
  if ((t === 'fib' || t === 'fibext' || t === 'pctlevels') && ctLevelsOpen) {
    var isPct = t === 'pctlevels';
    html += '<div class="ct-levels">' + (s.levels || []).map(function (l, i) {
      return '<div class="ct-level"><input type="checkbox" data-ct-lv="on" data-i="' + i + '"' + (l.on ? ' checked' : '') + '>' +
        '<input type="number" step="' + (isPct ? '1' : '0.001') + '"' + (isPct ? ' min="0.5" max="99" title="% from the anchor"' : '') + ' data-ct-lv="v" data-i="' + i + '" value="' + l.v + '">' + (isPct ? '<span class="ct-level-pct">%</span>' : '') +
        '<input type="color" data-ct-lv="c" data-i="' + i + '" value="' + ctHex(l.c || s.color) + '">' +
        '<button type="button" data-ct-lvdel="' + i + '" title="Remove level">×</button></div>';
    }).join('') + '<div class="ct-level-actions"><button type="button" data-ct-cmd="addlevel">+ Level</button><button type="button" data-ct-cmd="resetlevels">Reset</button><button type="button" data-ct-cmd="defaultlevels" title="Use these levels for new fibs">Save as default</button></div></div>';
  }
  bar.innerHTML = html;
}
function ctHex(c) {
  if (/^#[0-9a-f]{6}$/i.test(c || '')) return c;
  var m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(c || '');
  if (m) return '#' + [m[1], m[2], m[3]].map(function (x) { return ('0' + Number(x).toString(16)).slice(-2); }).join('');
  return '#f0b53f';
}
function ctEsc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;'); }
var ctEditSnap = null, ctEditTimer = null;
function ctBeginEdit() {
  if (ctEditSnap == null) ctEditSnap = ctSnapshot();
  clearTimeout(ctEditTimer);
  ctEditTimer = setTimeout(function () { if (ctEditSnap != null && ctEditSnap !== ctSnapshot()) ctPushUndo(ctEditSnap); ctEditSnap = null; }, 800);
}
function ctRemember(d, keys) {
  var td = ctToolDefaults[d.type] = ctToolDefaults[d.type] || {};
  keys.forEach(function (k) { if (k !== 'text' && d.style[k] !== undefined) td[k] = JSON.parse(JSON.stringify(d.style[k])); });
  ctPrefChanged();
}
function ctPropInput(ev) {
  var d = ctSelected(); if (!d) return;
  var el = ev.target, key = el.getAttribute('data-ct-p'), lv = el.getAttribute('data-ct-lv');
  if (key) {
    ctBeginEdit();
    var val = el.value;
    if (key === 'width' || key === 'fontSize' || key === 'fillOpacity') val = Number(val);
    d.style[key] = val;
    if (key === 'color' && d.style.levels && d.type !== 'pctlevels') {
      d.style.levels.forEach(function (l) { l.c = val; });
      if (ctPropsFor) { var bar = document.getElementById('ct-props'); if (bar) bar.querySelectorAll('[data-ct-lv="c"]').forEach(function (x) { x.value = val; }); }
    }
    if (key !== 'text') ctRemember(d, key === 'color' && d.style.levels && d.type !== 'pctlevels' ? ['color', 'levels'] : [key]);
    twSaveDrawings(); twRenderDrawings();
  } else if (lv) {
    ctBeginEdit();
    var i = Number(el.getAttribute('data-i')), lvl = d.style.levels && d.style.levels[i];
    if (!lvl) return;
    if (lv === 'on') lvl.on = el.checked;
    else if (lv === 'v') { var n = Number(el.value); if (isFinite(n)) lvl.v = n; }
    else if (lv === 'c') lvl.c = el.value;
    twSaveDrawings(); twRenderDrawings();
  }
}
function ctPropClick(ev) {
  var d = ctSelected(); if (!d) return;
  var flag = ev.target.closest('[data-ct-flag]'), cmd = ev.target.closest('[data-ct-cmd]'), del = ev.target.closest('[data-ct-lvdel]');
  if (flag) {
    ctPushUndo();
    var f = flag.getAttribute('data-ct-flag');
    if (f === 'showPrice' && (d.type === 'hline' || d.type === 'hray' || d.type === 'avwap')) d.style.showPrice = d.style.showPrice === false;
    else if (f === 'labels' || f === 'swingLine') d.style[f] = d.style[f] === false;
    else d.style[f] = !d.style[f];
    if (f !== 'reverse') ctRemember(d, [f]);
    ctPropsFor = null; twSaveDrawings(); twRenderDrawings();
    return;
  }
  if (del) {
    ctPushUndo(); d.style.levels.splice(Number(del.getAttribute('data-ct-lvdel')), 1);
    ctPropsFor = null; twSaveDrawings(); twRenderDrawings(); return;
  }
  if (!cmd) return;
  var c = cmd.getAttribute('data-ct-cmd');
  if (c === 'delete') twDeleteSelected();
  else if (c === 'close') ctSelect(null);
  else if (c === 'lock') { ctPushUndo(); d.locked = !d.locked; ctPropsFor = null; twSaveDrawings(); twRenderDrawings(); }
  else if (c === 'alert') ctAlertFromSelected();
  else if (c === 'ticket') ctSendToTicket(d);
  else if (c === 'clone') {
    ctPushUndo();
    var copy = JSON.parse(JSON.stringify(d)), shift = 4;
    copy.id = ctUid(); copy.locked = false;
    copy.points = copy.points.map(function (p) { return ctMoveBy(p, shift, 0); });
    ctStore.drawings.push(copy); twSaveDrawings(); ctSelect(ctStore.drawings.length - 1);
  }
  else if (c === 'levels') { ctLevelsOpen = !ctLevelsOpen; ctPropsFor = null; twRenderDrawings(); }
  else if (c === 'addlevel') { ctPushUndo(); d.style.levels.push(d.type === 'pctlevels' ? {v: 25, on: true, c: '#ffd166'} : {v: 1.414, on: true, c: d.style.color}); ctPropsFor = null; twSaveDrawings(); twRenderDrawings(); }
  else if (c === 'resetlevels') { ctPushUndo(); d.style.levels = JSON.parse(JSON.stringify(d.type === 'fib' ? CT_FIB_LEVELS : (d.type === 'fibext' ? CT_FIBEXT_LEVELS : CT_PCT_LEVELS))); ctPropsFor = null; twSaveDrawings(); twRenderDrawings(); }
  else if (c === 'defaultlevels') { ctRemember(d, ['levels']); cmd.textContent = 'Saved ✓'; }
}

/* ═══ Overlay studies, key levels, bid/ask on the price scale ═══ */
var ctStudy = {series: {}, levelLines: [], nbbo: null, nbboLines: [], linesOn: null};
function ctInitStudies(chart) {
  var mk = function (opts) {
    return chart.addLineSeries(Object.assign({lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, visible: false, title: ''}, opts));
  };
  ctStudy.series = {
    vwap: mk({lineWidth: 2}), vu1: mk({lineStyle: 2}), vl1: mk({lineStyle: 2}), vu2: mk({lineStyle: 2}), vl2: mk({lineStyle: 2}),
    bbm: mk({}), bbu: mk({}), bbl: mk({}), sma: mk({lineWidth: 2})
  };
  ctApplyCandleColors();
  ctApplyView();
  setInterval(ctRenderNbbo, 5000);
  // The bar-close countdown ticks once a second.
  setInterval(function () { if (ctPrefs.view.countdown && !document.hidden) twRenderDrawings(); }, 1000);
}

/* ═══ Scale, canvas and status-line settings ═══ */
var ctViewKey = '', ctTsOffset = null;
var CT_INTERVAL_NAMES = {'1m': '1m', '5m': '5m', '15m': '15m', '1h': '1H', '4h': '4H', '1d': '1D', '1w': '1W', '1mo': '1M'};
function ctApplyView(force) {
  var ch = twChartState.chart, LC = window.LightweightCharts;
  if (!ch || !LC) return;
  var v = ctPrefs.view, sc = ctPrefs.scale;
  var key = JSON.stringify([v, sc, twChartState.ticker, twChartState.interval, twChartState.type]);
  if (key === ctViewKey && !force) return;
  ctViewKey = key;
  try {
    ch.applyOptions({
      grid: {vertLines: {visible: v.grid === 'both', color: 'rgba(255,255,255,0.05)'},
             horzLines: {visible: v.grid !== 'none', color: 'rgba(255,255,255,0.05)'}},
      crosshair: {mode: v.crosshair === 'magnet' ? LC.CrosshairMode.Magnet : LC.CrosshairMode.Normal},
      watermark: {visible: !!(v.watermark && twChartState.ticker), text: (twChartState.ticker || '') + '  ' + (CT_INTERVAL_NAMES[twChartState.interval] || ''),
                  color: 'rgba(255,255,255,0.05)', fontSize: 72, horzAlign: 'center', vertAlign: 'center'}
    });
    // Let decades of daily candles fit on one screen when zoomed all the way out.
    // Only when the value changes: setting time-scale options cancels a
    // pending fitContent(), which left a new symbol squeezed to the right edge.
    var ro = Math.max(0, Math.min(60, Number(v.rightOffset) || 0));
    if (ctTsOffset !== ro) { ctTsOffset = ro; ch.timeScale().applyOptions({rightOffset: ro, minBarSpacing: 0.02}); }
    ch.priceScale('right').applyOptions({mode: {normal: 0, log: 1, percent: 2, indexed: 3}[sc.mode] || 0, invertScale: !!sc.invert});
    [twChartState.candle, twChartState.area].forEach(function (ser) {
      if (ser) ser.applyOptions({priceLineVisible: sc.lastLine !== false, lastValueVisible: sc.lastLabel !== false});
    });
  } catch (e) {}
  var stack = document.getElementById('tw-chart-stack');
  if (stack) {
    stack.classList.toggle('ct-no-ohlc', v.ohlc === false);
    stack.classList.toggle('ct-no-legend', v.legend === false);
    stack.classList.toggle('ct-no-tooltip', v.tooltip === false);
    stack.classList.toggle('ct-no-hint', v.hint === false);
  }
}
function ctPctFrom(level, now) {
  var pct = (now - level) / level * 100;
  if (!isFinite(pct) || Math.abs(pct) < 0.05) return '';
  return (pct < 0 ? '▼' : '▲') + Math.abs(pct).toFixed(Math.abs(pct) >= 100 ? 0 : 1) + '% now';
}
/* Overlay extras drawn on the drawing canvas: high/low of the visible range
   and the time left in the current candle. */
function ctDrawExtras(ctx, dim) {
  var bars = twChartState.bars || [], ch = twChartState.chart;
  if (!bars.length || !ch) return;
  try { ctDrawValueZones(ctx, dim); } catch (e) {}
  try { ctDrawSessionMarks(ctx, dim); } catch (e) {}
  try { ctDrawEvents(ctx, dim); } catch (e) {}
  try { ctDrawNextEarnings(ctx, dim); } catch (e) {}
  if (ctPrefs.view.hilo) {
    var r = null;
    try { r = ch.timeScale().getVisibleLogicalRange(); } catch (e) {}
    if (r) {
      var from = Math.max(0, Math.ceil(r.from)), to = Math.min(bars.length - 1, Math.floor(r.to)), hi = -1, lo = -1;
      for (var i = from; i <= to; i++) {
        if (hi < 0 || Number(bars[i].high) > Number(bars[hi].high)) hi = i;
        if (lo < 0 || Number(bars[i].low) < Number(bars[lo].low)) lo = i;
      }
      ctx.save();
      ctx.font = '600 10px "IBM Plex Mono", ui-monospace, monospace'; ctx.fillStyle = 'rgba(214,224,236,0.75)'; ctx.textAlign = 'center';
      [[hi, 'high', 'bottom', -5, 'H '], [lo, 'low', 'top', 5, 'L ']].forEach(function (m) {
        if (m[0] < 0) return;
        var x = ctLogX(m[0]), y = ctY(Number(bars[m[0]][m[1]]));
        if (x == null || y == null || y < 10 || y > dim.H - 10) return;
        ctx.textBaseline = m[2];
        var tx = Math.max(30, Math.min(dim.W - 30, x));
        var lvl = Number(bars[m[0]][m[1]]), now = Number(bars[bars.length - 1].close);
        var pctTxt = lvl && now ? ctPctFrom(lvl, now) : '';
        if (!pctTxt) { ctx.fillText(m[4] + ctFmt(lvl), tx, y + m[3]); return; }
        // Price now vs. this high/low: how far it has dropped from the top,
        // or how far it has come off the bottom.
        var base = m[4] + ctFmt(lvl) + '  ', bw = ctx.measureText(base).width, pw = ctx.measureText(pctTxt).width;
        var sx = Math.max(4, Math.min(dim.W - bw - pw - 4, tx - (bw + pw) / 2));
        ctx.textAlign = 'left';
        ctx.fillStyle = 'rgba(214,224,236,0.75)'; ctx.fillText(base, sx, y + m[3]);
        ctx.fillStyle = now < lvl ? 'rgba(255,92,108,0.95)' : 'rgba(52,211,153,0.95)';
        ctx.fillText(pctTxt, sx + bw, y + m[3]);
        ctx.fillStyle = 'rgba(214,224,236,0.75)'; ctx.textAlign = 'center';
      });
      ctx.restore();
    }
  }
  var step = CT_STEP[twChartState.interval];
  if (ctPrefs.view.countdown && step && step < 86400 && ctPrefs.scale.lastLabel !== false) {
    var last = bars[bars.length - 1], left = ctSec(last.time) + step - Date.now() / 1000;
    if (left > 0 && left <= step) {
      var y2 = ctY(Number(last.close));
      if (y2 != null && y2 > 0 && y2 < dim.H - 30) {
        left = Math.floor(left);
        var hh = Math.floor(left / 3600), mm = Math.floor(left % 3600 / 60), ss = left % 60;
        var txt = (hh ? hh + ':' + ('0' + mm).slice(-2) : mm) + ':' + ('0' + ss).slice(-2);
        ctx.save();
        ctx.font = '600 10px "IBM Plex Mono", ui-monospace, monospace';
        var up = Number(last.close) >= Number(last.open);
        ctx.fillStyle = ctHexA(up ? ctPrefs.candles.up : ctPrefs.candles.down, 0.85);
        ctx.fillRect(dim.W, y2 + 9, dim.axisW, 15);
        ctx.fillStyle = '#ffffff'; ctx.textBaseline = 'middle'; ctx.fillText(txt, dim.W + 6, y2 + 16.5);
        ctx.restore();
      }
    }
  }
}
/* Opening bell, closing bell and new-day lines on intraday charts. With
   extended hours on, the line sits where the session flips (9:30 / 4:00 ET);
   with it off, consecutive bars jump from one day's close to the next open,
   so a faint line marks each new day instead. */
/* ── Value zones (from the Thesis page) on Daily / Weekly / Monthly ──
   Shaded price bands: Accumulate (under base value) and Load the boat
   (25%+ under), plus a thin line where Pricey starts. Value says where;
   the candles say when. */
function ctDrawValueZones(ctx, dim) {
  var z = twChartState.zones, o = ctPrefs.overlays || CT_PREF_DEFAULTS.overlays;
  if (!z || !z.available || ctPrefs.view.zones === false) return;
  if ((CT_STEP[twChartState.interval] || 0) < 86400) return;
  /* The value rail: a slim colour strip down the right edge of the chart
     (Pricey / Fair / Accumulate / Load) with a marker at the current price.
     The chart background stays untouched; only hairlines cross it at the
     zone edges. Every piece and colour comes from Chart settings. */
  var yBull = ctY(z.bull), yAcc = ctY(z.accum_top), yLoad = ctY(z.load_top);
  if (yBull == null && yAcc == null && yLoad == null) return;
  function clampY(y, dflt) { return y == null ? dflt : Math.max(0, Math.min(dim.H, y)); }
  var railX = dim.W - 10, railW = 7, lineEnd = o.rail ? railX - 2 : dim.W;
  ctx.save();
  if (o.rail) {
    [[0, clampY(yBull, 0), o.pricey, o.priceyColor],
     [clampY(yBull, 0), clampY(yAcc, 0), o.fair, o.fairColor],
     [clampY(yAcc, 0), clampY(yLoad, dim.H), o.accum, o.accumColor],
     [clampY(yLoad, dim.H), dim.H, o.load, o.loadColor]].forEach(function (sg) {
      var y0 = Math.min(sg[0], sg[1]), y1 = Math.max(sg[0], sg[1]);
      if (!sg[2] || y1 - y0 < 1) return;
      ctx.fillStyle = sg[3]; ctx.globalAlpha = 0.85; ctx.fillRect(railX, y0, railW, y1 - y0);
    });
    ctx.globalAlpha = 1;
  }
  function edge(y, color, dash) {
    if (y == null || y < 0 || y > dim.H) return;
    ctx.strokeStyle = ctHexA(color, 0.4); ctx.lineWidth = 1; ctx.setLineDash(dash);
    ctx.beginPath(); ctx.moveTo(0, Math.round(y) + 0.5); ctx.lineTo(lineEnd, Math.round(y) + 0.5); ctx.stroke();
  }
  if (o.lines) {
    if (o.pricey) edge(yBull, o.priceyColor, [2, 6]);
    if (o.accum) edge(yAcc, o.accumColor, [6, 6]);
    if (o.load) edge(yLoad, o.loadColor, [6, 6]);
  }
  ctx.setLineDash([]);
  if (o.axisTags !== false) {
    if (o.pricey && z.bull) ctExtraAxis.push({p: z.bull, c: o.priceyColor, text: 'PRICEY'});
    if (o.accum && z.accum_top) ctExtraAxis.push({p: z.accum_top, c: o.accumColor, text: 'ACCUM'});
    if (o.load && z.load_top) ctExtraAxis.push({p: z.load_top, c: o.loadColor, text: 'LOAD'});
  }
  if (o.tags) {
    ctx.font = '700 9.5px ui-monospace, Menlo, monospace'; ctx.textBaseline = 'middle';
    var tagRight = o.rail ? railX - 5 : dim.W - 6;
    var tag = function (y, text, color) {
      if (y == null || y < 9 || y > dim.H - 9) return;
      var w = ctx.measureText(text).width + 10, x = tagRight - w;
      ctx.fillStyle = 'rgba(8,10,12,0.85)'; ctx.fillRect(x, y - 8, w, 16);
      ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.strokeRect(x + 0.5, y - 7.5, w - 1, 15);
      ctx.fillStyle = color; ctx.textAlign = 'left'; ctx.fillText(text, x + 5, y + 0.5);
    };
    if (o.pricey) tag(yBull != null ? yBull - 10 : null, 'PRICEY ≥ ' + ctFmt(z.bull), o.priceyColor);
    if (o.accum) tag(yAcc != null ? yAcc + 10 : null, 'ACCUM ≤ ' + ctFmt(z.accum_top), o.accumColor);
    if (o.load) tag(yLoad != null ? yLoad + 10 : null, 'LOAD ≤ ' + ctFmt(z.load_top), o.loadColor);
  }
  if (o.rail) {
    var bars = twChartState.bars || [], last = bars.length ? bars[bars.length - 1].close : null;
    var yNow = last != null ? ctY(last) : null;
    if (yNow != null && yNow >= 0 && yNow <= dim.H) {
      ctx.fillStyle = '#f6f4ee';
      ctx.beginPath(); ctx.moveTo(railX - 7, yNow - 5); ctx.lineTo(railX - 1, yNow); ctx.lineTo(railX - 7, yNow + 5); ctx.closePath(); ctx.fill();
    }
  }
  ctx.restore();
}

/* Next earnings: from the chart's event feed (upcoming) or the verified
   date the Terminal's context card shows. {date, time, ts, days} or null. */
function ctNextEarnings() {
  var now = Date.now() / 1000, best = null;
  (twChartState.events || []).forEach(function (e) {
    if (e.type !== 'earnings') return;
    var t = ctEventTime(e);
    if (t != null && t >= now - 86400 && (!best || t < best.ts)) best = {date: e.date, time: e.time || '', ts: t, est: !!e.estimated, src: e.source || ''};
  });
  var ne = twChartState.nextEarnings;
  if (!best && ne && ne.date) {
    var t2 = Date.parse(String(ne.date).slice(0, 10) + 'T12:00:00Z') / 1000;
    if (isFinite(t2) && t2 >= now - 86400) best = {date: String(ne.date).slice(0, 10), time: ne.time || '', ts: t2};
  }
  if (!best) return null;
  var todayET = new Date(new Date().toLocaleString('en-US', {timeZone: 'America/New_York'}));
  todayET.setHours(12, 0, 0, 0);
  var d = new Date(String(best.date).slice(0, 10) + 'T12:00:00');
  best.days = Math.max(0, Math.round((d - todayET) / 86400000));
  return best;
}

/* The next earnings date on every timeframe: a gold dashed line at the day
   with a countdown pill at the top; past the right edge, the pill sits at
   the edge pointing right. */
function ctDrawNextEarnings(ctx, dim) {
  var o = ctPrefs.overlays || CT_PREF_DEFAULTS.overlays;
  if (!ctPrefs.view.events || !o.nextEarnings) return;
  var gold = o.earningsColor || '#f0b53f';
  var n = ctNextEarnings(); if (!n) return;
  var step = CT_STEP[twChartState.interval] || 86400;
  var t = step >= 86400 ? Math.floor(n.ts / 86400) * 86400 + 12 * 3600 : n.ts;
  var x = ctEventX(t, step);
  var when = n.days === 0 ? 'TODAY' : (n.days === 1 ? '1D' : n.days + 'D');
  var date = (n.est ? '~' : '') + ctShortDate(n.date).toUpperCase();
  var main = 'EARNINGS ' + date + (n.time ? ' · ' + String(n.time).toUpperCase() : '') + (n.est ? ' · EST' : '');
  var mv = ctEarningsMove();
  if (mv) main += '  ·  AVG MOVE ±' + mv.avg.toFixed(1) + '%';
  var tip = (n.est ? 'Estimated next earnings ~' : 'Next earnings ') + n.date + (n.time ? ' · ' + n.time : '') +
    (n.est ? ' — projected from past reports, not yet announced' : (n.src ? ' (' + n.src + ')' : ''));
  ctx.save();
  ctx.font = '800 9.5px "IBM Plex Mono", ui-monospace, monospace'; ctx.textBaseline = 'middle';
  var mw = ctx.measureText(main).width, ww = ctx.measureText(when).width;
  var h = 20, y = 4, icon = 16, w = 8 + icon + mw + 8 + ww + 12;
  function capsule(px) {
    // Glass capsule: calendar glyph, the date, then the countdown in gold.
    ctx.shadowColor = ctHexA(gold, 0.45); ctx.shadowBlur = 10;
    ctRoundRect(ctx, px, y, w, h, h / 2);
    ctx.fillStyle = 'rgba(14,11,5,0.95)'; ctx.fill();
    ctx.shadowBlur = 0;
    ctx.lineWidth = 1; ctx.strokeStyle = gold; if (n.est) ctx.setLineDash([3, 2]); ctx.stroke(); ctx.setLineDash([]);
    var cx = px + 8;
    ctx.strokeStyle = gold; ctx.lineWidth = 1.2;
    ctRoundRect(ctx, cx, y + 5, 11, 10, 2); ctx.stroke();
    ctx.fillStyle = gold; ctx.fillRect(cx, y + 5, 11, 3); ctx.fillRect(cx + 2.5, y + 3.5, 1.2, 3); ctx.fillRect(cx + 7.5, y + 3.5, 1.2, 3);
    cx += icon;
    ctx.fillStyle = gold; ctx.textAlign = 'left'; ctx.fillText(main, cx, y + h / 2 + 0.5);
    cx += mw + 8;
    ctRoundRect(ctx, cx - 4, y + 3, ww + 10, h - 6, (h - 6) / 2);
    ctx.fillStyle = gold; ctx.fill();
    ctx.fillStyle = '#1a1206'; ctx.fillText(when, cx + 1, y + h / 2 + 0.5);
  }
  if (x != null && x >= 0 && x <= dim.W - 16) {
    x = Math.round(x) + 0.5;
    // A soft gold glow behind a dashed line down to the time axis.
    var gl = ctx.createLinearGradient(x - 6, 0, x + 6, 0);
    gl.addColorStop(0, ctHexA(gold, 0)); gl.addColorStop(0.5, ctHexA(gold, n.est ? 0.08 : 0.16)); gl.addColorStop(1, ctHexA(gold, 0));
    ctx.fillStyle = gl; ctx.fillRect(x - 6, y + h, 12, dim.H - y - h);
    ctx.strokeStyle = ctHexA(gold, n.est ? 0.55 : 0.85); ctx.lineWidth = 1.2; ctx.setLineDash(n.est ? [2, 4] : [6, 4]);
    ctx.beginPath(); ctx.moveTo(x, y + h); ctx.lineTo(x, dim.H); ctx.stroke(); ctx.setLineDash([]);
    ctDrawEarningsRunway(ctx, dim, x, n, gold);
    if (mv) ctDrawEarningsRange(ctx, dim, x, mv, gold);
    var px = Math.min(Math.max(4, x - w / 2), dim.W - w - 18);
    capsule(px);
    ctTimeAxisMarks.push({x: x, c: gold, est: n.est, label: date + ' · ' + when});
    ctEventHits.push({x: px + w / 2, y: y + h / 2, r: Math.max(12, w / 2), text: tip});
  } else if (x != null && x > dim.W - 16) {
    // Past the right edge: the capsule waits at the edge, pointing right.
    var ex = dim.W - w - 30;
    capsule(ex);
    ctx.fillStyle = gold; ctx.beginPath(); ctx.moveTo(ex + w + 6, y + 5); ctx.lineTo(ex + w + 13, y + h / 2); ctx.lineTo(ex + w + 6, y + h - 5); ctx.closePath(); ctx.fill();
    ctEventHits.push({x: ex + w / 2, y: y + h / 2, r: Math.max(12, w / 2), text: tip + ' — scroll right to see it on the time axis'});
  }
  ctx.restore();
}
function ctDrawSessionMarks(ctx, dim) {
  var bars = twChartState.bars || [], ch = twChartState.chart, step = CT_STEP[twChartState.interval];
  if (!ctPrefs.view.sessions || !ch || !step || step >= 14400 || bars.length < 2) return;
  var r = null;
  try { r = ch.timeScale().getVisibleLogicalRange(); } catch (e) {}
  if (!r) return;
  var from = Math.max(1, Math.floor(r.from)), to = Math.min(bars.length - 1, Math.ceil(r.to));
  if (to - from > 4000) return;
  var marks = [];
  for (var i = from; i <= to; i++) {
    var a = bars[i - 1], b = bars[i], ra = ctIsRegular(a), rb = ctIsRegular(b);
    if (!ra && rb) marks.push([i, 'OPEN', 'rgba(34,197,139,0.55)']);
    else if (ra && !rb) marks.push([i, 'CLOSE', 'rgba(255,77,69,0.45)']);
    else if (ctEtDay(ctSec(a.time)) !== ctEtDay(ctSec(b.time))) marks.push([i, '', 'rgba(255,255,255,0.10)']);
  }
  if (marks.length > 60) marks = marks.filter(function (m) { return m[1]; });   // zoomed far out: bells only
  if (marks.length > 60) return;
  ctx.save();
  ctx.lineWidth = 1; ctx.font = '700 9px "IBM Plex Mono", ui-monospace, monospace'; ctx.textBaseline = 'top';
  marks.forEach(function (m) {
    var x = ctLogX(m[0] - 0.5);
    if (x == null || x < 0 || x > dim.W) return;
    x = Math.round(x) + 0.5;
    ctx.strokeStyle = m[2]; ctx.setLineDash(m[1] ? [3, 4] : [2, 6]);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, dim.H); ctx.stroke();
    // Below the OHLC legend in the top-left corner.
    if (m[1]) { ctx.fillStyle = m[2].replace(/[\d.]+\)$/, '0.9)'); ctx.textAlign = 'left'; ctx.fillText(m[1], x + 4, 60); }
  });
  ctx.restore();
}
function ctEtDay(sec) { try { return twEasternSessionDate.format(new Date(sec * 1000)); } catch (e) { return ''; } }
function ctIsRegular(b) { return !b.session || b.session === 'regular'; }
function ctLine(series, data, opts) {
  try { series.setData(data); series.applyOptions(opts); } catch (e) {}
}
function ctRenderStudies() {
  var S = ctStudy.series, bars = twChartState.bars || [];
  if (!S.vwap || !twChartState.chart) return;
  var labels = ctLbl('indicators'), intraday = ['1m', '5m', '15m', '1h'].indexOf(twChartState.interval) >= 0;
  // VWAP: regular session, reset each trading day. Pre/post bars get
  // whitespace so the line breaks instead of joining yesterday to today.
  var vOn = ctPrefs.vwap.on && intraday && bars.length > 0, vw = [], b1u = [], b1l = [], b2u = [], b2l = [];
  if (vOn) {
    var day = '', pv = 0, vv = 0, p2 = 0;
    bars.forEach(function (b) {
      var t = b.time;
      if (!ctIsRegular(b)) { vw.push({time: t}); b1u.push({time: t}); b1l.push({time: t}); b2u.push({time: t}); b2l.push({time: t}); return; }
      var dk = ctEtDay(ctSec(t));
      if (dk !== day) { day = dk; pv = 0; vv = 0; p2 = 0; }
      var v = Number(b.volume) || 0, tp = (Number(b.high) + Number(b.low) + Number(b.close)) / 3;
      if (v > 0 && isFinite(tp)) { pv += tp * v; vv += v; p2 += tp * tp * v; }
      if (!vv) { vw.push({time: t}); b1u.push({time: t}); b1l.push({time: t}); b2u.push({time: t}); b2l.push({time: t}); return; }
      var m = pv / vv, sd = Math.sqrt(Math.max(0, p2 / vv - m * m));
      vw.push({time: t, value: m}); b1u.push({time: t, value: m + sd}); b1l.push({time: t, value: m - sd}); b2u.push({time: t, value: m + 2 * sd}); b2l.push({time: t, value: m - 2 * sd});
    });
  }
  var vc = ctPrefs.vwap.color, bandsOn = vOn && ctPrefs.vwap.bands;
  ctLine(S.vwap, vOn ? vw : [], {visible: vOn, color: vc, lastValueVisible: labels});
  [['vu1', b1u, 0.55], ['vl1', b1l, 0.55], ['vu2', b2u, 0.35], ['vl2', b2l, 0.35]].forEach(function (x) {
    ctLine(S[x[0]], bandsOn ? x[1] : [], {visible: bandsOn, color: ctHexA(vc, x[2]), lastValueVisible: false});
  });
  var closes = bars.map(function (b) { return Number(b.close); });
  // Bollinger Bands
  var bb = ctPrefs.bb, bbOn = bb.on && bars.length > bb.len, mid = [], hi = [], lo = [];
  if (bbOn) {
    var sum = 0, sq = 0, L = Math.max(2, Number(bb.len) || 20), k = Number(bb.mult) || 2;
    for (var i = 0; i < closes.length; i++) {
      sum += closes[i]; sq += closes[i] * closes[i];
      if (i >= L) { sum -= closes[i - L]; sq -= closes[i - L] * closes[i - L]; }
      if (i >= L - 1) {
        var mean = sum / L, sdv = Math.sqrt(Math.max(0, sq / L - mean * mean));
        mid.push({time: bars[i].time, value: mean}); hi.push({time: bars[i].time, value: mean + k * sdv}); lo.push({time: bars[i].time, value: mean - k * sdv});
      }
    }
  }
  ctLine(S.bbm, mid, {visible: bbOn, color: ctHexA(bb.color, 0.75), lineStyle: 2, lastValueVisible: false});
  ctLine(S.bbu, hi, {visible: bbOn, color: bb.color, lastValueVisible: labels});
  ctLine(S.bbl, lo, {visible: bbOn, color: bb.color, lastValueVisible: labels});
  // SMA
  var sm = ctPrefs.sma, smOn = sm.on && bars.length > sm.len, sd2 = [];
  if (smOn) {
    var acc = 0, N = Math.max(2, Number(sm.len) || 50);
    for (var j = 0; j < closes.length; j++) {
      acc += closes[j]; if (j >= N) acc -= closes[j - N];
      if (j >= N - 1) sd2.push({time: bars[j].time, value: acc / N});
    }
  }
  ctLine(S.sma, sd2, {visible: smOn, color: sm.color, lastValueVisible: labels});
  ctRenderLevels();
  ctRenderNbbo();
  ctRenderStudyLegend();
  ctApplyView();
}
function ctKeyLevels() {
  var bars = twChartState.bars || [], out = [];
  if (!bars.length || twChartState.interval === '1w' || twChartState.interval === '1mo') return out;
  if (twChartState.interval === '1d') {
    var pb = bars[bars.length - 2];
    if (pb) out.push({p: Number(pb.high), t: 'PDH', c: '#7fd1ff'}, {p: Number(pb.low), t: 'PDL', c: '#ffb36b'}, {p: Number(pb.close), t: 'PDC', c: '#b0b8c4'});
    return out;
  }
  var lastDay = ctEtDay(ctSec(bars[bars.length - 1].time)), prev = null, pre = [];
  var byDay = {};
  bars.forEach(function (b) {
    var dk = ctEtDay(ctSec(b.time));
    if (dk === lastDay && b.session === 'premarket') pre.push(b);
    if (!ctIsRegular(b) || dk >= lastDay) return;
    (byDay[dk] = byDay[dk] || []).push(b);
  });
  var days = Object.keys(byDay).sort();
  if (days.length) prev = byDay[days[days.length - 1]];
  if (prev && prev.length) {
    var h = Math.max.apply(null, prev.map(function (b) { return Number(b.high); })), l = Math.min.apply(null, prev.map(function (b) { return Number(b.low); }));
    out.push({p: h, t: 'PDH', c: '#7fd1ff'}, {p: l, t: 'PDL', c: '#ffb36b'}, {p: Number(prev[prev.length - 1].close), t: 'PDC', c: '#b0b8c4'});
  }
  if (pre.length) {
    out.push({p: Math.max.apply(null, pre.map(function (b) { return Number(b.high); })), t: 'PMH', c: '#4aa3ff'});
    out.push({p: Math.min.apply(null, pre.map(function (b) { return Number(b.low); })), t: 'PML', c: '#4aa3ff'});
  }
  return out.filter(function (x) { return isFinite(x.p) && x.p > 0; });
}
function ctRenderLevels() {
  var s = ctSeries();
  ctStudy.levelLines.forEach(function (l) { try { l.s.removePriceLine(l.line); } catch (e) {} });
  ctStudy.levelLines = [];
  if (!s || !ctPrefs.levels.on) return;
  ctKeyLevels().filter(function (lv) { return ctPrefs.levels[lv.t.toLowerCase()] !== false; }).forEach(function (lv) {
    try {
      ctStudy.levelLines.push({s: s, line: s.createPriceLine({price: lv.p, color: lv.c, lineWidth: 1, lineStyle: 2, axisLabelVisible: ctLbl('levels'), title: lv.t})});
    } catch (e) {}
  });
}
function ctRenderNbbo() {
  var s = ctSeries(), q = ctStudy.nbbo;
  var live = s && q && q.symbol === twChartState.ticker && Date.now() - q.at < 20000 && q.bid > 0 && q.ask > 0 && q.ask >= q.bid;
  var want = live ? [ctLbl('ask') ? 'ask' : null, ctLbl('bid') ? 'bid' : null].filter(Boolean).join(',') : '';
  if (ctStudy.nbboLines.length && (ctStudy.nbboWant !== want || ctStudy.nbboLines[0].s !== s)) {
    ctStudy.nbboLines.forEach(function (l) { try { l.s.removePriceLine(l.line); } catch (e) {} });
    ctStudy.nbboLines = [];
  }
  ctStudy.nbboWant = want;
  if (!want) return;
  if (!ctStudy.nbboLines.length) {
    try {
      if (ctLbl('ask')) ctStudy.nbboLines.push({s: s, k: 'ask', line: s.createPriceLine({price: q.ask, color: '#2f7bff', lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: 'Ask'})});
      if (ctLbl('bid')) ctStudy.nbboLines.push({s: s, k: 'bid', line: s.createPriceLine({price: q.bid, color: '#b05cff', lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: 'Bid'})});
    } catch (e) {}
  } else {
    ctStudy.nbboLines.forEach(function (l) { try { l.line.applyOptions({price: q[l.k]}); } catch (e) {} });
  }
}
document.addEventListener('dp:quote', function (e) {
  var d = e.detail || {};
  var bid = Number(d.bid), ask = Number(d.ask);
  if (!d.symbol || !(bid > 0) || !(ask > 0)) return;
  ctStudy.nbbo = {symbol: d.symbol, bid: bid, ask: ask, at: Date.now(), src: 'stream'};
  ctRenderNbbo();
});
/* Bid / Ask from the 3-second quote poll (Schwab REST / Alpaca) when the
   Level 2 streamer isn't delivering — not connected, between symbols, or
   quiet after hours. The streamer wins whenever it's fresh. */
function ctNbboFallback(sym, q) {
  var bid = Number(q && q.bid), ask = Number(q && q.ask);
  if (!sym || !(bid > 0) || !(ask >= bid)) return false;
  var cur = ctStudy.nbbo;
  if (cur && cur.src === 'stream' && cur.symbol === sym && Date.now() - cur.at < 8000) return false;
  ctStudy.nbbo = {symbol: sym, bid: bid, ask: ask, at: Date.now(), src: 'poll'};
  ctRenderNbbo();
  return true;
}
function ctOnSeriesChange() {
  ctRenderLevels();
  if (ctStudy.nbboLines.length) { ctStudy.nbboLines.forEach(function (l) { try { l.s.removePriceLine(l.line); } catch (e) {} }); ctStudy.nbboLines = []; }
  ctRenderNbbo();
  twRenderDrawings();
}
function ctRenderStudyLegend() {
  var host = document.getElementById('tw-ema-legend');
  if (!host) return;
  host.querySelectorAll('.ct-leg').forEach(function (x) { x.remove(); });
  var items = [];
  var bars = twChartState.bars || [], intraday = ['1m', '5m', '15m', '1h'].indexOf(twChartState.interval) >= 0;
  var lastOf = function (series) { try { var d = series.data ? series.data() : null; for (var i = d ? d.length - 1 : -1; i >= 0; i--) if (d[i].value != null) return d[i].value; } catch (e) {} return null; };
  if (ctPrefs.vwap.on && intraday && bars.length) items.push(['VWAP', ctPrefs.vwap.color, lastOf(ctStudy.series.vwap)]);
  if (ctPrefs.bb.on) items.push(['BB ' + ctPrefs.bb.len + ' ' + ctPrefs.bb.mult, ctPrefs.bb.color, lastOf(ctStudy.series.bbm)]);
  if (ctPrefs.sma.on) items.push(['SMA ' + ctPrefs.sma.len, ctPrefs.sma.color, lastOf(ctStudy.series.sma)]);
  items.forEach(function (it) {
    var chip = document.createElement('span'), dot = document.createElement('i'), label = document.createElement('b');
    chip.className = 'ct-leg'; dot.style.backgroundColor = it[1];
    label.textContent = it[0] + (it[2] != null ? '  ' + Number(it[2]).toFixed(2) : '');
    chip.appendChild(dot); chip.appendChild(label); host.appendChild(chip);
  });
  if (items.length) host.hidden = false;
}

/* ═══ Candle colours ═══ */
function ctApplyCandleColors() {
  var c = ctPrefs.candles;
  if (twChartState.candle) {
    var hollow = c.style === 'hollow';
    try {
      twChartState.candle.applyOptions({upColor: hollow ? 'rgba(0,0,0,0)' : c.up, downColor: c.down, wickUpColor: c.up, wickDownColor: c.down,
        borderVisible: hollow, borderUpColor: c.up, borderDownColor: c.down, wickVisible: c.wicks !== false});
    } catch (e) {}
  }
  if (twChartState.shade) { try { twChartState.shade.applyOptions({visible: !!c.shading}); } catch (e) {} }
}
/* Per-bar colours. Extended-hours candles use the same green/red as the
   regular session (TradingView-style); the old blue/purple/orange session
   palette is an option in chart settings. */
var ctHA = [];
function ctHeikin(bar) {
  // Bars arrive in order on a full load and one at a time on a live update of
  // the last bar, so the previous Heikin Ashi candle is the one before it.
  var t = ctSec(bar.time);
  while (ctHA.length && ctHA[ctHA.length - 1].t >= t) ctHA.pop();
  var o = Number(bar.open), h = Number(bar.high), l = Number(bar.low), c = Number(bar.close);
  var prev = ctHA[ctHA.length - 1], hc = (o + h + l + c) / 4, ho = prev ? (prev.o + prev.c) / 2 : (o + c) / 2;
  var ha = {t: t, o: ho, c: hc};
  ctHA.push(ha);
  return {time: bar.time, open: ho, high: Math.max(h, ho, hc), low: Math.min(l, ho, hc), close: hc};
}
function ctCandleDatum(bar) {
  var datum = ctPrefs.candles.style === 'heikin' ? ctHeikin(bar)
    : {time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close};
  if (!ctPrefs.candles.session) return datum;
  var up = datum.close >= datum.open, col = null;
  if (bar.session === 'overnight') col = up ? '#f0b53f' : '#ff8c42';
  else if (bar.session === 'premarket') col = up ? '#4aa3ff' : '#7569e8';
  else if (bar.session === 'after_hours') col = up ? '#c77dff' : '#ff6aa9';
  if (col) { datum.color = col; datum.wickColor = col; datum.borderColor = col; }
  return datum;
}
var ctVolDense = false;
function ctVolumeColor(bar) {
  // Zoomed far out each volume bar is narrower than a pixel and red/green
  // blurs into stripes; use one soft color until there is room for color again.
  if (ctVolDense) return 'rgba(120,150,190,0.42)';
  var up = bar.close >= bar.open, ext = bar.session && bar.session !== 'regular';
  if (ctPrefs.candles.session && ext) {
    if (bar.session === 'overnight') return up ? 'rgba(240,181,63,.5)' : 'rgba(255,140,66,.5)';
    if (bar.session === 'premarket') return up ? 'rgba(74,163,255,.48)' : 'rgba(117,105,232,.48)';
    return up ? 'rgba(199,125,255,.48)' : 'rgba(255,106,169,.48)';
  }
  return ctHexA(up ? ctPrefs.candles.up : ctPrefs.candles.down, ext ? 0.32 : 0.45);
}
function ctRepaintCandles() {
  var bars = twChartState.bars || [];
  if (!bars.length || !twChartState.candle) return;
  try {
    ctHA = [];
    twChartState.candle.setData(bars.map(ctCandleDatum));
    twChartState.vol.setData(bars.map(function (b) { return {time: b.time, value: b.volume || 0, color: ctVolumeColor(b)}; }));
  } catch (e) {}
}

/* ═══ Settings panel + indicator menu wiring ═══ */
function ctSet(path, value) {
  var parts = path.split('.'), obj = ctPrefs;
  for (var i = 0; i < parts.length - 1; i++) obj = obj[parts[i]] = obj[parts[i]] || {};
  var last = parts[parts.length - 1], cur = obj[last];
  if (typeof cur === 'number') { var n = Number(value); if (!isFinite(n)) return; value = n; }
  obj[last] = value;
  if (path === 'bb.len') obj.len = Math.max(2, Math.min(200, Math.round(obj.len)));
  if (path === 'bb.mult') obj.mult = Math.max(0.5, Math.min(5, obj.mult));
  if (path === 'sma.len') obj.len = Math.max(2, Math.min(400, Math.round(obj.len)));
  ctPrefChanged();
  if (path.indexOf('candles.') === 0) { ctApplyCandleColors(); ctRepaintCandles(); }
  if (path === 'view.volume' && typeof twToggleVolume === 'function' && !!value !== !!twChartState.showVolume) twToggleVolume(document.getElementById('tw-volume-btn') || document.createElement('button'));
  if (path.indexOf('view.') === 0 || path.indexOf('scale.') === 0) ctApplyView(true);
  if (path === 'view.zones' && typeof twSyncZonesBtn === 'function') twSyncZonesBtn();
  if (path.indexOf('overlays.') === 0) {
    if (typeof twRenderEarningsChip === 'function') twRenderEarningsChip();
    if (typeof twRenderZoneChip === 'function') twRenderZoneChip(twChartState.zones);
    if (typeof twRenderDrawings === 'function') twRenderDrawings();
  }
  if (path.indexOf('labels.') === 0 || path.indexOf('levels.') === 0) { ctStudy.nbboWant = null; ctRenderLevels(); }
  if (path === 'magnet' || path === 'draw.hidden') ctSyncRail();
  if (path === 'labels.indicators' && typeof twApplyStudyStyles === 'function') twApplyStudyStyles();
  ctRenderStudies(); ctSyncSettingsUI(); twRenderDrawings();
}
/* Zones & events tab: back to the default look. */
function ctResetOverlays() {
  ctPrefs.overlays = JSON.parse(JSON.stringify(CT_PREF_DEFAULTS.overlays));
  ctPrefs.view.zones = true; ctPrefs.view.events = true;
  ctPrefChanged(); ctSyncSettingsUI();
  if (typeof twSyncZonesBtn === 'function') twSyncZonesBtn();
  if (typeof twRenderEarningsChip === 'function') twRenderEarningsChip();
  if (typeof twRenderZoneChip === 'function') twRenderZoneChip(twChartState.zones);
  twRenderDrawings();
}
function ctSyncSettingsUI() {
  if (typeof twChartState !== 'undefined') ctPrefs.view.volume = !!twChartState.showVolume;
  document.querySelectorAll('[data-ct-set]').forEach(function (el) {
    var path = el.getAttribute('data-ct-set').split('.'), v = ctPrefs;
    path.forEach(function (k) { v = v == null ? null : v[k]; });
    if (el.type === 'checkbox') el.checked = !!v; else if (v != null) el.value = String(v);
  });
  var cnt = document.getElementById('tw-ind-count');
  if (cnt && typeof twChartState !== 'undefined') {
    var n = (twChartState.indicators.rsi ? 1 : 0) + (twChartState.indicators.macd ? 1 : 0) + (ctPrefs.vwap.on ? 1 : 0) + (ctPrefs.bb.on ? 1 : 0) + (ctPrefs.sma.on ? 1 : 0) + (ctPrefs.levels.on ? 1 : 0);
    cnt.textContent = n;
  }
}
document.addEventListener('change', function (ev) {
  var el = ev.target && ev.target.closest && ev.target.closest('[data-ct-set]');
  if (!el) return;
  ctSet(el.getAttribute('data-ct-set'), el.type === 'checkbox' ? el.checked : el.value);
});
document.addEventListener('input', function (ev) {
  var el = ev.target;
  if (!el || el.type !== 'color' || !el.hasAttribute('data-ct-set')) return;
  ctSet(el.getAttribute('data-ct-set'), el.value);
});
document.addEventListener('DOMContentLoaded', ctSyncSettingsUI);

/* Settings panel: tabs and the reset buttons. */
document.addEventListener('click', function (ev) {
  var tab = ev.target.closest && ev.target.closest('[data-ct-tab]');
  if (tab) {
    var grid = document.querySelector('#tw-study-settings .tw-study-grid');
    if (grid) grid.setAttribute('data-ct-active', tab.getAttribute('data-ct-tab'));
    document.querySelectorAll('[data-ct-tab]').forEach(function (b) { b.classList.toggle('on', b === tab); b.setAttribute('aria-selected', b === tab ? 'true' : 'false'); });
    return;
  }
  var cmd = ev.target.closest && ev.target.closest('[data-ct-cmdset]');
  if (!cmd) return;
  var c = cmd.getAttribute('data-ct-cmdset');
  if (c === 'savedefault') {
    var snap = {overlays: JSON.parse(JSON.stringify(ctPrefs)), tools: JSON.parse(JSON.stringify(ctToolDefaults)),
      chart: ctChartBundle(true), saved: Date.now()};
    cmd.textContent = 'Saving…';
    fetch('/api/chart/default', {method: 'PUT', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({default: snap})})
      .then(function (r) {
        if (!r.ok) throw new Error('save failed');
        ctMyDefault = snap; ctSyncDefaultBtn();
        cmd.textContent = '★ Saved as your default ✓';
      }).catch(function () { cmd.textContent = 'Couldn’t save — try again'; })
      .then(function () { setTimeout(function () { cmd.textContent = '★ Save as my default'; }, 2200); });
    return;
  }
  if (c === 'mydefault') {
    if (!ctMyDefault) return;
    ctPrefs = JSON.parse(JSON.stringify(CT_PREF_DEFAULTS));
    ctMergePrefs(JSON.parse(JSON.stringify(ctMyDefault.overlays)));
    ctToolDefaults = JSON.parse(JSON.stringify(ctMyDefault.tools || {}));
    ctApplyChartBundle(ctMyDefault.chart, true);
    ctPrefChanged();
    ctApplyCandleColors(); ctRepaintCandles(); ctApplyView(true); ctStudy.nbboWant = null;
    ctRenderStudies(); ctSyncSettingsUI(); ctSyncRail(); twRenderDrawings();
    cmd.textContent = 'Back to your default ✓';
    setTimeout(ctSyncDefaultBtn, 2000);
    return;
  }
  if (c === 'resetall') {
    ctPrefs = JSON.parse(JSON.stringify(CT_PREF_DEFAULTS));
    // Indicator styles and lengths back to factory too.
    if (typeof twDefaultStyles !== 'undefined') ctApplyChartBundle({styles: JSON.parse(JSON.stringify(twDefaultStyles)),
      studies: JSON.parse(JSON.stringify(twDefaultStudies)), indicators: {rsi: true, macd: true}}, false);
    ctPrefChanged();
    if (!twChartState.showVolume && typeof twToggleVolume === 'function') twToggleVolume(document.getElementById('tw-volume-btn') || document.createElement('button'));
    ctApplyCandleColors(); ctRepaintCandles(); ctApplyView(true); ctStudy.nbboWant = null;
    if (typeof twApplyStudyStyles === 'function') twApplyStudyStyles();
    ctRenderStudies(); ctSyncSettingsUI(); ctSyncRail(); twRenderDrawings();
    cmd.textContent = 'Factory settings back ✓';
    setTimeout(function () { cmd.textContent = 'Factory reset'; }, 2000);
  } else if (c === 'cleartool') {
    ctToolDefaults = {}; ctPrefChanged();
    cmd.textContent = 'Drawing styles reset ✓';
    setTimeout(function () { cmd.textContent = 'Reset drawing styles to defaults'; }, 2000);
  } else if (c === 'clearsym') {
    if (!ctStore.drawings.length) { cmd.textContent = 'No drawings on ' + (ctStore.ticker || 'this symbol'); setTimeout(function () { cmd.textContent = 'Remove all drawings on this symbol'; }, 2000); return; }
    var armed = cmd.getAttribute('data-armed') === '1';
    if (!armed) {
      cmd.setAttribute('data-armed', '1'); cmd.textContent = 'Tap again to remove ' + ctStore.drawings.length + ' drawings on ' + ctStore.ticker;
      setTimeout(function () { cmd.removeAttribute('data-armed'); cmd.textContent = 'Remove all drawings on this symbol'; }, 3000);
      return;
    }
    cmd.removeAttribute('data-armed');
    ctPushUndo(); ctStore.drawings = []; twChartState.selectedDrawing = null; twSaveDrawings(); twRenderDrawings();
    cmd.textContent = 'Removed ✓ (Undo on the rail brings them back)';
    setTimeout(function () { cmd.textContent = 'Remove all drawings on this symbol'; }, 2500);
  }
});


/* ── Keyboard shortcuts ─────────────────────────────────────────────
   1–8 timeframes · Alt+letter drawing tools (ev.code, because Alt changes
   ev.key on a Mac) · Alt+R reset view · Shift+F full screen · ? help. */
var CT_KEY_TOOLS = {KeyT: 'trend', KeyH: 'hline', KeyV: 'vline', KeyF: 'fib', KeyB: 'rect', KeyC: 'channel',
                    KeyM: 'measure', KeyL: 'long', KeyS: 'short', KeyX: 'text', KeyA: 'avwap', KeyY: 'ray', KeyD: 'pctlevels'};
var CT_SHORTCUTS = [
  ['1 – 8', 'Timeframe: 1m 5m 15m 1H 4H 1D 1W 1M'],
  ['Alt + T / Y', 'Trend line / ray'], ['Alt + H / V', 'Horizontal / vertical line'],
  ['Alt + F', 'Fib retracement'], ['Alt + D', '% drop levels (click a high)'], ['Alt + B / C', 'Rectangle / channel'],
  ['Alt + L / S', 'Long / short position'], ['Alt + M', 'Measure % (or Shift + drag for a quick one)'], ['Alt + A', 'Anchored VWAP'],
  ['Alt + X', 'Text'], ['Alt + R', 'Fit chart history'], ['Alt + 0', 'Latest candles'], ['Shift + F', 'Full screen'],
  ['Alt + ↑ / ↓', 'Previous / next watchlist symbol'],
  ['Alt + W', 'Alert at the selected line'], ['Ctrl/⌘ + Z', 'Undo'], ['Ctrl/⌘ + Shift + Z  or  Ctrl + Y', 'Redo'],
  ['Delete', 'Remove selected drawing'], ['Esc', 'Cancel / deselect'], ['?', 'This list']
];
function ctShortcut(ev) {
  if (ev.metaKey || ev.ctrlKey) return false;
  if (!ev.altKey && !ev.shiftKey && /^[1-8]$/.test(ev.key)) {
    var btn = document.querySelectorAll('.tw-chart-toolbar .tw-tf-btn')[Number(ev.key) - 1];
    if (btn && !btn.disabled) { btn.click(); return true; }
    return false;
  }
  if (ev.key === '?') { ctToggleShortcutHelp(); return true; }
  if (ev.shiftKey && !ev.altKey && ev.code === 'KeyF' && typeof twToggleFullscreen === 'function') { twToggleFullscreen(); return true; }
  if (!ev.altKey || ev.shiftKey) return false;
  if (ev.code === 'Digit0' && typeof twLatestView === 'function') { twLatestView(); return true; }
  if ((ev.code === 'ArrowUp' || ev.code === 'ArrowDown') && typeof twStepWatchlist === 'function') {
    return twStepWatchlist(ev.code === 'ArrowDown' ? 1 : -1);
  }
  if (ev.code === 'KeyR' && typeof twResetView === 'function') { twResetView(); return true; }
  if (ev.code === 'KeyW' && typeof ctAlertFromSelected === 'function') { ctAlertFromSelected(); return true; }
  var tool = CT_KEY_TOOLS[ev.code];
  if (tool && CT_TOOLS[tool]) { twStartDrawing(tool); return true; }
  return false;
}
function ctToggleShortcutHelp() {
  var el = document.getElementById('ct-keys');
  if (el) { el.remove(); return; }
  el = document.createElement('div');
  el.id = 'ct-keys'; el.className = 'ct-keys'; el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', 'Keyboard shortcuts');
  el.innerHTML = '<div class="ct-keys-card"><div class="ct-keys-hd"><b>Keyboard shortcuts</b><button type="button" aria-label="Close">×</button></div>' +
    CT_SHORTCUTS.map(function (r) { return '<div class="ct-keys-row"><kbd>' + ctEsc(r[0]) + '</kbd><span>' + ctEsc(r[1]) + '</span></div>'; }).join('') + '</div>';
  el.addEventListener('click', function (e) { if (e.target === el || e.target.closest('button')) el.remove(); });
  ctPopupHost().appendChild(el);
}


/* ── Price alerts from drawings ─────────────────────────────────────
   The bell in a drawing's properties bar (or Alt+W) sets an alert at the
   drawing's price(s): a horizontal line or ray, both edges of a box, the
   fib levels on either side of price, a position's stop and target. The
   server checks every 10 s against the live quote and notifies once. */
var ctAlerts = {ticker: null, list: [], at: 0};
function ctLoadAlerts(ticker, force) {
  if (!ticker) return;
  if (!force && ctAlerts.ticker === ticker && Date.now() - ctAlerts.at < 30000) return;
  ctAlerts.at = Date.now();
  fetch('/api/alerts/price?ticker=' + encodeURIComponent(ticker), {credentials: 'same-origin', cache: 'no-store'})
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.ok || twChartState.ticker !== ticker) return;
      ctAlerts = {ticker: ticker, list: d.alerts || [], at: Date.now()};
      twRenderDrawings();
    }).catch(function () {});
}
function ctLastPrice() {
  var b = twChartState.bars || [];
  return b.length ? Number(b[b.length - 1].close) : null;
}
function ctAlertPrices(d) {
  if (!d || !d.points || !d.points.length) return [];
  var t = d.type, out = [];
  if (t === 'hline' || t === 'hray') out.push({p: d.points[0].p, note: 'Line'});
  else if (t === 'rect' && d.points[1]) {
    out.push({p: Math.max(d.points[0].p, d.points[1].p), note: 'Box top'});
    out.push({p: Math.min(d.points[0].p, d.points[1].p), note: 'Box bottom'});
  } else if ((t === 'long' || t === 'short') && d.points[1]) {
    out.push({p: d.points[1].p, note: (t === 'long' ? 'Long' : 'Short') + ' target'});
    if (d.stop != null) out.push({p: d.stop, note: (t === 'long' ? 'Long' : 'Short') + ' stop'});
  } else if (t === 'pctlevels') {
    var lastP = ctLastPrice(), pl = ctPctLevelPrices(d).map(function (l) { return {p: l.p, note: (d.style.reverse ? '+' : '−') + l.v + '% level'}; });
    if (lastP == null) return pl.slice(0, 2);
    var ab = pl.filter(function (x) { return x.p > lastP; }).sort(function (a, b) { return a.p - b.p; })[0];
    var be = pl.filter(function (x) { return x.p < lastP; }).sort(function (a, b) { return b.p - a.p; })[0];
    if (ab) out.push(ab); if (be) out.push(be);
  } else if ((t === 'fib' || t === 'fibext') && d.points[1]) {
    var A = d.points[0], B = d.points[1], C = d.points[2], s = d.style, last = ctLastPrice();
    var lv = (s.levels || []).filter(function (l) { return l.on; }).map(function (l) {
      var p = t === 'fibext' ? (C || B).p + (B.p - A.p) * l.v : (s.reverse ? A.p + (B.p - A.p) * l.v : B.p + (A.p - B.p) * l.v);
      return {p: p, note: 'Fib ' + (Math.round(l.v * 1000) / 1000)};
    });
    if (last == null) return lv.slice(0, 2);
    var above = lv.filter(function (x) { return x.p > last; }).sort(function (a, b) { return a.p - b.p; })[0];
    var below = lv.filter(function (x) { return x.p < last; }).sort(function (a, b) { return b.p - a.p; })[0];
    if (above) out.push(above); if (below) out.push(below);
  }
  return out.filter(function (x) { return isFinite(x.p) && x.p > 0; });
}
function ctAlertFromSelected() {
  var d = ctSelected(), ticker = twChartState.ticker;
  var prices = ctAlertPrices(d);
  if (!ticker || !prices.length) { ctNotice('Select a horizontal line, box, fib or position first, then tap the bell.'); return; }
  var last = ctLastPrice(), done = 0, made = [];
  prices.forEach(function (x) {
    fetch('/api/alerts/price', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ticker: ticker, price: Math.round(x.p * 10000) / 10000, current: last, note: x.note, drawing_id: d.id})})
      .then(function (r) { return r.json(); })
      .then(function (res) { if (res && res.ok) made.push(res.alert); else ctNotice((res && res.error) || 'Could not set the alert'); })
      .catch(function () { ctNotice('Could not set the alert — check your connection'); })
      .then(function () {
        if (++done < prices.length) return;
        if (made.length) ctNotice('🔔 Alert set: ' + made.map(function (a) { return (a.direction === 'above' ? '↑ ' : '↓ ') + ctFmt(a.price); }).join(' · ') + ' — you’ll be notified once.');
        ctLoadAlerts(ticker, true);
      });
  });
}
function ctDeleteAlert(id) {
  fetch('/api/alerts/price/' + id, {method: 'DELETE', credentials: 'same-origin'})
    .then(function () { ctLoadAlerts(twChartState.ticker, true); }).catch(function () {});
}
var ctAlertHits = [];
function ctDrawAlerts(ctx, dim, axisLabels) {
  ctAlertHits = [];
  if (ctAlerts.ticker !== twChartState.ticker) return;
  ctAlerts.list.forEach(function (a) {
    if (!a.enabled || a.triggered_at) return;
    var y = ctY(a.price);
    if (y == null || y < 0 || y > dim.H) return;
    // By default only a short stub next to the price axis marks the level
    // (the tag on the axis carries the name); full-width lines are a setting.
    var full = (ctPrefs.overlays || {}).alertLines === true;
    ctx.save();
    ctx.strokeStyle = 'rgba(245,197,66,0.75)'; ctx.lineWidth = 1; ctx.setLineDash(full ? [6, 4] : []);
    ctx.beginPath(); ctx.moveTo(full ? 0 : Math.max(0, dim.W - 36), Math.round(y) + 0.5); ctx.lineTo(dim.W, Math.round(y) + 0.5); ctx.stroke();
    ctx.restore();
    // The name rides on the price axis (× at its end cancels the alert).
    var note = String(a.note || 'Alert').replace(new RegExp('^' + (twChartState.ticker || '') + '\\s+'), '');
    note = note.replace(/\bstep\s+/i, '').replace(/\s+zone$/i, '');
    if (note.length > 14) note = note.slice(0, 13) + '…';
    axisLabels.push({p: a.price, c: '#f5c542', text: note, alertId: a.id, dir: a.direction});
  });
}
/* Tap the × on an alert tag to cancel it. Capture phase, before drawing. */
function ctOverAlert(x, y) {
  return ctAlertHits.some(function (h) { return x >= h.x1 - 4 && x <= h.x2 + 2 && y >= h.y1 && y <= h.y2; });
}
function ctAlertClick(ev) {
  if (!ctAlertHits.length) return false;
  var cv = ctCanvas(); if (!cv) return false;
  var r = cv.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
  for (var i = 0; i < ctAlertHits.length; i++) {
    var h = ctAlertHits[i];
    if (x >= h.x1 - 4 && x <= h.x2 + 2 && y >= h.y1 && y <= h.y2) { ctDeleteAlert(h.id); ctNotice('Alert cancelled.'); return true; }
  }
  return false;
}
function ctNotice(msg) {
  var el = document.getElementById('ct-notice');
  if (!el) {
    el = document.createElement('div'); el.id = 'ct-notice'; el.className = 'ct-notice'; el.setAttribute('role', 'status'); el.setAttribute('aria-live', 'polite');
    var box = document.getElementById('tw-chart-box'); (box || document.body).appendChild(el);
  }
  el.textContent = msg; el.classList.add('on');
  clearTimeout(el._t); el._t = setTimeout(function () { el.classList.remove('on'); }, 4200);
}
// A fired alert drops off the chart within a minute.
var ctAlertPoll = setInterval(function () {
  if (typeof document === 'undefined' || document.hidden) return;
  if (ctAlerts.list.some(function (a) { return a.enabled && !a.triggered_at; })) ctLoadAlerts(twChartState.ticker, true);
}, 60000);
if (ctAlertPoll && ctAlertPoll.unref) ctAlertPoll.unref();   // never keeps a test runner (node) alive


/* ── Trade from the chart ───────────────────────────────────────────
   A long/short position drawing hands entry, target and stop to the order
   ticket (terminal.html listens for ct:ticket). Drag the handles, then
   "→ Ticket" again to update it. Nothing is sent: the ticket still needs
   Preview and a second tap. */
function ctSendToTicket(d) {
  if (!d || (d.type !== 'long' && d.type !== 'short') || !d.points[1]) return;
  var detail = {symbol: twChartState.ticker, side: d.type === 'long' ? 'BUY' : 'SELL',
    entry: Math.round(d.points[0].p * 100) / 100, target: Math.round(d.points[1].p * 100) / 100,
    stop: Math.round(d.stop * 100) / 100, last: ctLastPrice()};
  document.dispatchEvent(new CustomEvent('ct:ticket', {detail: detail}));
}


/* ── Saved layouts ──────────────────────────────────────────────────
   A layout is the chart's own saved state (timeframe, range, chart type,
   volume, extended hours, indicators and every Settings-panel option).
   Applying one writes that state back and reloads the Terminal, so it
   comes up exactly as saved. Synced to the account (/api/chart/layouts). */
var CT_LAYOUT_KEYS = ['tradestaar.chartInterval', 'tradestaar.chartRange', 'tradestaar.chartType', 'tradestaar.chartVolume',
  'tradestaar.chartExtendedPref', 'tradestaar.chartIndicators', 'tradestaar.chartStudies', 'tradestaar.chartStyles',
  'tradestaar.chartOverlays'];
// Daily/weekly presets leave the Extended choice alone: it only applies to
// intraday charts, and switching it off there hid after-hours candles later.
var CT_BUILTIN_LAYOUTS = [
  {name: 'Scalp · 1m', builtin: true, state: {'tradestaar.chartInterval': '1m', 'tradestaar.chartRange': '1d', 'tradestaar.chartType': 'candles',
    'tradestaar.chartExtendedPref': '1', 'tradestaar.chartVolume': '1'}},
  {name: 'Day trade · 5m', builtin: true, state: {'tradestaar.chartInterval': '5m', 'tradestaar.chartRange': '5d', 'tradestaar.chartType': 'candles',
    'tradestaar.chartExtendedPref': '1', 'tradestaar.chartVolume': '1'}},
  {name: 'Swing · 1D', builtin: true, state: {'tradestaar.chartInterval': '1d', 'tradestaar.chartRange': '1y', 'tradestaar.chartType': 'candles',
    'tradestaar.chartVolume': '1'}},
  {name: 'Big picture · 1W', builtin: true, state: {'tradestaar.chartInterval': '1w', 'tradestaar.chartRange': 'max', 'tradestaar.chartType': 'candles',
    'tradestaar.chartVolume': '1'}}
];
var ctLayouts = [];
try { ctLayouts = JSON.parse(localStorage.getItem('tradestaar.layouts') || '[]') || []; } catch (e) { ctLayouts = []; }
function ctLayoutState() {
  var st = {};
  CT_LAYOUT_KEYS.forEach(function (k) { try { var v = localStorage.getItem(k); if (v != null) st[k] = v; } catch (e) {} });
  st['tradestaar.chartOverlays'] = JSON.stringify(ctPrefs);
  if (twChartState.interval) st['tradestaar.chartInterval'] = twChartState.interval;
  if (twChartState.range) st['tradestaar.chartRange'] = twChartState.range;
  return st;
}
function ctPushLayouts() {
  try { localStorage.setItem('tradestaar.layouts', JSON.stringify(ctLayouts)); } catch (e) {}
  return fetch('/api/chart/layouts', {method: 'PUT', credentials: 'same-origin', keepalive: true, headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({layouts: ctLayouts})}).catch(function () {});
}
function ctLoadLayouts() {
  fetch('/api/chart/layouts', {credentials: 'same-origin', cache: 'no-store'})
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.ok || !Array.isArray(d.layouts)) return;
      if (!d.layouts.length && ctLayouts.length) { ctPushLayouts(); return; }   // first sync from this device
      ctLayouts = d.layouts;
      try { localStorage.setItem('tradestaar.layouts', JSON.stringify(ctLayouts)); } catch (e) {}
      ctRenderLayoutMenu();
    }).catch(function () {});
}
function ctSaveLayout(name) {
  name = String(name || '').trim().slice(0, 40);
  if (!name) return;
  var st = ctLayoutState(), at = -1;
  ctLayouts.forEach(function (l, i) { if (l.name.toLowerCase() === name.toLowerCase()) at = i; });
  if (at >= 0) ctLayouts[at] = {name: name, state: st}; else ctLayouts.unshift({name: name, state: st});
  ctLayouts = ctLayouts.slice(0, 20);
  ctPushLayouts(); ctRenderLayoutMenu();
  if (typeof ctNotice === 'function') ctNotice('Layout "' + name + '" saved.');
}
function ctApplyLayout(layout) {
  if (!layout || !layout.state) return;
  var st = layout.state;
  Object.keys(st).forEach(function (k) { if (CT_LAYOUT_KEYS.indexOf(k) >= 0) { try { localStorage.setItem(k, st[k]); } catch (e) {} } });
  var now = Date.now();
  try { localStorage.setItem('tradestaar.chartOverlaysAt', String(now)); } catch (e) {}
  var overlays = null;
  try { overlays = st['tradestaar.chartOverlays'] ? JSON.parse(st['tradestaar.chartOverlays']) : null; } catch (e) {}
  var done = function () { location.reload(); };
  if (overlays) {
    fetch('/api/chart/prefs', {method: 'PUT', credentials: 'same-origin', keepalive: true, headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prefs: {overlays: overlays, tools: ctToolDefaults, updated: now}})}).then(done, done);
  } else done();
}
function ctDeleteLayout(i) { ctLayouts.splice(i, 1); ctPushLayouts(); ctRenderLayoutMenu(); }
function ctRenderLayoutMenu() {
  var m = document.getElementById('ct-layouts'); if (!m) return;
  var row = function (l, i, builtin) {
    return '<div class="ct-lay-row"><button type="button" class="ct-lay-apply" data-lay="' + (builtin ? 'b' : 'u') + i + '">' + ctEsc(l.name) +
      '<small>' + ctEsc(((l.state['tradestaar.chartInterval'] || '') + ' · ' + (l.state['tradestaar.chartRange'] || '')).toUpperCase()) + '</small></button>' +
      (builtin ? '' : '<button type="button" class="ct-lay-del" data-lay-del="' + i + '" title="Delete layout" aria-label="Delete ' + ctEsc(l.name) + '">×</button>') + '</div>';
  };
  m.innerHTML = '<div class="ct-lay-hd">Layouts <button type="button" class="ct-lay-close" data-layout-close>Done</button></div>' +
    '<form class="ct-lay-save"><input name="n" maxlength="40" placeholder="Name this layout…" aria-label="Layout name"><button type="submit">Save current</button></form>' +
    (ctLayouts.length ? '<div class="ct-lay-sec">Yours</div>' + ctLayouts.map(function (l, i) { return row(l, i, false); }).join('') : '') +
    '<div class="ct-lay-sec">Presets</div>' + CT_BUILTIN_LAYOUTS.map(function (l, i) { return row(l, i, true); }).join('') +
    '<a class="ct-lay-grid" href="/terminal/grid?ticker=' + encodeURIComponent(twChartState.ticker || '') + '">▦ Multi-chart grid (2 or 4 charts) ↗</a>';
}
function twToggleLayouts(ev) {
  if (ev) ev.stopPropagation();
  var m = document.getElementById('ct-layouts'), btn = document.getElementById('tw-layout-btn');
  if (!m) {
    m = document.createElement('div'); m.id = 'ct-layouts'; m.className = 'ct-layouts'; m.hidden = true;
    (btn && btn.parentElement || document.body).appendChild(m);
    m.addEventListener('click', function (e) {
      e.stopPropagation();
      if (e.target.closest('[data-layout-close]')) { twToggleLayouts(); return; }
      var a = e.target.closest('[data-lay]'), d = e.target.closest('[data-lay-del]');
      if (d) { ctDeleteLayout(Number(d.getAttribute('data-lay-del'))); return; }
      if (a) { var k = a.getAttribute('data-lay'); ctApplyLayout(k[0] === 'b' ? CT_BUILTIN_LAYOUTS[+k.slice(1)] : ctLayouts[+k.slice(1)]); }
    });
    m.addEventListener('submit', function (e) { e.preventDefault(); var inp = m.querySelector('input[name=n]'); ctSaveLayout(inp.value); inp.value = ''; });
    document.addEventListener('click', function (e) { if (!m.hidden && !m.contains(e.target) && e.target !== btn) { m.hidden = true; if (btn) btn.setAttribute('aria-expanded', 'false'); } });
    ctLoadLayouts();
  }
  ctRenderLayoutMenu();
  m.hidden = !m.hidden;
  if (btn) btn.setAttribute('aria-expanded', m.hidden ? 'false' : 'true');
  if (!m.hidden) { var inp = m.querySelector('input[name=n]'); if (inp && !('ontouchstart' in window)) inp.focus(); }
}


/* ── Earnings & news on the time axis ───────────────────────────────
   Earnings: a gold dashed line at the date (the future too — the time axis
   extrapolates past the last bar) with an "E" flag. News: small dots along
   the bottom at the headline's time; hover shows it, tap opens it. Nearby
   headlines merge into one dot with a count. */
var ctEventHits = [];
function ctEventTime(e) {
  if (e.ts) return Number(e.ts);
  if (!e.date) return null;
  // Earnings date: 9:30 ET for "BMO"/"before", 4:00 PM for "AMC"/"after", else noon ET.
  var tm = String(e.time || '').toLowerCase(), hh = /bmo|before|pre/.test(tm) ? 9.5 : /amc|after|post/.test(tm) ? 16 : 12;
  var d = new Date(e.date + 'T00:00:00Z'); if (isNaN(d.getTime())) return null;
  return Math.floor(d.getTime() / 1000) + Math.round((hh + 5) * 3600);   // ET ≈ UTC−5 (±1 h in summer is fine at this scale)
}
/* Daily, Weekly and Monthly: an event belongs to the candle that contains
   it, so it sits on that candle instead of between two. */
function ctEventX(t, step) {
  if (step < 86400) return ctX(t);
  var l = ctTimeToLogical(t), n = ctBarSecs().length;
  if (l == null) return null;
  // Inside the last (still-forming) candle counts as that candle.
  if (l >= 0 && l < n) return ctLogX(Math.min(n - 1, Math.floor(l + 1e-6)));
  return ctLogX(l);
}
var CT_MAX_EARNINGS_LONG = 8;
function ctDrawEvents(ctx, dim) {
  ctEventHits = [];
  var evs = twChartState.events || [];
  if (!ctPrefs.view.events || !evs.length) return;
  var step = CT_STEP[twChartState.interval] || 86400, bars = twChartState.bars || [];
  if (!bars.length) return;
  var first = ctSec(bars[0].time);
  ctx.save();
  ctx.font = '800 10px "IBM Plex Mono", ui-monospace, monospace'; ctx.textBaseline = 'middle'; ctx.textAlign = 'center';
  // Earnings. On Daily and longer only the last 8 past reports (plus any
  // upcoming one): an "All" chart would otherwise carry 30+ flags.
  var ov = ctPrefs.overlays || CT_PREF_DEFAULTS.overlays;
  var earn = ov.pastEarnings === false ? [] : evs.filter(function (e) { return e.type === 'earnings'; });
  if (step >= 86400) {
    var nowS = Date.now() / 1000;
    var past = earn.filter(function (e) { var t = ctEventTime(e); return t != null && t <= nowS && !e.upcoming; })
      .sort(function (a, b) { return ctEventTime(b) - ctEventTime(a); }).slice(0, CT_MAX_EARNINGS_LONG);
    earn = past;
  } else {
    earn = earn.filter(function (e) { var t = ctEventTime(e); return !e.upcoming && t != null && t <= Date.now() / 1000; });
  }
  // Room between neighbouring pins decides whether the EPS surprise fits above each.
  var pinXs = earn.map(function (e) {
    var t = ctEventTime(e); if (t == null) return null;
    if (step >= 86400) t = Math.floor(t / 86400) * 86400 + 12 * 3600;
    return ctEventX(t, step);
  }).filter(function (v) { return v != null; }).sort(function (a, b) { return a - b; });
  function pinGap(x) {
    var g = Infinity;
    pinXs.forEach(function (o) { if (Math.abs(o - x) > 0.5) g = Math.min(g, Math.abs(o - x)); });
    return g;
  }
  earn.forEach(function (e) {
    var t = ctEventTime(e); if (t == null || t < first) return;
    if (step >= 86400) t = Math.floor(t / 86400) * 86400 + 12 * 3600;
    var x = ctEventX(t, step); if (x == null || x < 0) return;
    var label = 'Earnings ' + (e.date || '') + (e.time && e.time !== 'TBD' ? ' · ' + String(e.time).toUpperCase() : '');
    if (e.eps != null) label += ' · EPS ' + Number(e.eps).toFixed(2) + (e.eps_est != null ? ' vs ' + Number(e.eps_est).toFixed(2) + ' est' : '') + (e.result ? ' · ' + e.result.toUpperCase() : '');
    else if (e.upcoming || t > Date.now() / 1000) label += ' · upcoming';
    // Beat green, miss red; a report with no EPS on file stays a quiet slate
    // (gold is kept for the next report).
    var flagColor = e.result === 'beat' ? '#22C58B' : e.result === 'miss' ? '#FF4D45' : (e.result === 'inline' ? '#C9D3E0' : '#7F8A9B');
    if (x > dim.W) {
      // Past the right edge (upcoming): a flag at the edge says it's coming.
      var tag = 'E ' + ctShortDate(e.date) + ' ›';
      ctx.font = '800 10px "IBM Plex Mono", ui-monospace, monospace';
      // Above the news dots, which collect at the last candle.
      var tw = ctx.measureText(tag).width + 12, tx = dim.W - tw - 4, ty = dim.H - 44;
      ctx.fillStyle = '#F0B53F'; ctx.fillRect(tx, ty, tw, 16);
      ctx.fillStyle = '#1a1206'; ctx.textAlign = 'left'; ctx.fillText(tag, tx + 6, ty + 8.5); ctx.textAlign = 'center';
      ctEventHits.push({x: tx + tw / 2, y: ty + 8, r: Math.max(10, tw / 2), text: label + ' — scroll right to see it on the time axis'});
      return;
    }
    x = Math.round(x) + 0.5;
    // Every timeframe gets the report line; on Daily and up it is fainter,
    // since a long chart holds many reports.
    {
      // Blends into the background: a faint dotted line that fades out toward the top.
      var lg = ctx.createLinearGradient(0, 18, 0, dim.H - 22);
      var a1 = step < 86400 ? 0.34 : 0.2;
      lg.addColorStop(0, ctHexA(flagColor, 0)); lg.addColorStop(0.55, ctHexA(flagColor, a1 * 0.6)); lg.addColorStop(1, ctHexA(flagColor, a1));
      ctx.strokeStyle = lg; ctx.lineWidth = 1; ctx.setLineDash([2, 4]);
      ctx.beginPath(); ctx.moveTo(x, 18); ctx.lineTo(x, dim.H - 24); ctx.stroke(); ctx.setLineDash([]);
    }
    ctDrawEarnPin(ctx, x, dim.H - 14, flagColor, e, pinGap(x));
    ctTimeAxisMarks.push({x: x, c: flagColor});
    ctEventHits.push({x: x, y: dim.H - 14, r: 10, text: label + (e.source ? ' (' + e.source + ')' : '')});
  });
  // News, clustered by pixel
  var dots = [];
  (ov.news === false ? [] : evs).filter(function (e) { return e.type === 'news' && e.ts; }).forEach(function (e) {
    if (e.ts < first) return;
    var x = ctEventX(Number(e.ts), step); if (x == null || x < 0 || x > dim.W) return;
    var near = dots.filter(function (d) { return Math.abs(d.x - x) < 12; })[0];
    if (near) near.items.push(e); else dots.push({x: x, items: [e]});
  });
  // A news dot on the same candle as an earnings flag stacks above it.
  var eXs = ctEventHits.filter(function (h) { return h.text && Math.abs(h.y - (dim.H - 14)) < 2; }).map(function (h) { return h.x; });
  dots.forEach(function (d) {
    var y = eXs.some(function (ex) { return Math.abs(ex - d.x) < 16; }) ? dim.H - 34 : dim.H - 14, n = d.items.length;
    ctx.fillStyle = ctHexA(ov.newsColor || '#6fa8ff', 0.92); ctx.beginPath(); ctx.arc(d.x, y, n > 1 ? 8 : 6, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#06101f'; ctx.fillText(n > 1 ? String(Math.min(n, 9)) : 'N', d.x, y + 0.5);
    ctEventHits.push({x: d.x, y: y, r: 10, items: d.items});
  });
  ctx.restore();
}
/* How much this stock has moved on its own earnings days: for each of the
   last 8 reports with a known reaction day (after-close → next session,
   before-open → that day; unknown → the bigger of the two), the close-to-
   close % move. From the chart's daily candles; null without enough. */
var ctMoveCache = {key: '', val: null};
function ctEarningsMove() {
  var bars = twChartState.bars || [];
  if (twChartState.interval !== '1d' || bars.length < 40) return ctMoveCache.key.indexOf((twChartState.ticker || '') + '|') === 0 ? ctMoveCache.val : null;
  var evs = (twChartState.events || []).filter(function (e) { return e.type === 'earnings' && !e.upcoming && e.date; });
  var key = (twChartState.ticker || '') + '|' + bars.length + '|' + evs.length;
  if (ctMoveCache.key === key) return ctMoveCache.val;
  var days = bars.map(function (b) { return ctEtDay(ctSec(b.time)); });
  var moves = [];
  evs.sort(function (a, b) { return a.date < b.date ? 1 : -1; }).slice(0, 12).forEach(function (e) {
    if (moves.length >= 8) return;
    var d = String(e.date).slice(0, 10), i = -1;
    for (var k = 0; k < days.length; k++) { if (days[k] >= d) { i = k; break; } }
    if (i < 1 || i >= bars.length) return;
    var hr = String(e.time || '').toLowerCase();
    function mvAt(r) { return r >= 1 && r < bars.length ? (bars[r].close / bars[r - 1].close - 1) * 100 : null; }
    var sameDay = days[i] === d, m;
    if (/amc|after/.test(hr)) m = mvAt(sameDay ? i + 1 : i);
    else if (/bmo|before/.test(hr)) m = mvAt(i);
    else { var m1 = mvAt(i), m2 = sameDay ? mvAt(i + 1) : null; m = (m2 != null && Math.abs(m2) > Math.abs(m1 || 0)) ? m2 : m1; }
    if (m != null && isFinite(m)) moves.push(m);
  });
  var val = null;
  if (moves.length >= 3) {
    var avg = moves.reduce(function (s, v) { return s + Math.abs(v); }, 0) / moves.length;
    val = {avg: avg, n: moves.length, up: moves.filter(function (v) { return v > 0; }).length, last: Number(bars[bars.length - 1].close)};
  }
  ctMoveCache = {key: key, val: val};
  return val;
}
/* The road to the report: a thin gold track from the last candle to the
   earnings line along the bottom, a tick each week, and the day count. */
function ctDrawEarningsRunway(ctx, dim, x, n, gold) {
  var bars = twChartState.bars || []; if (!bars.length) return;
  var x0 = ctLogX(bars.length - 1); if (x0 == null || x - x0 < 30) return;
  var y = dim.H - 30;
  ctx.save();
  var g = ctx.createLinearGradient(x0, 0, x, 0);
  g.addColorStop(0, ctHexA(gold, 0.05)); g.addColorStop(1, ctHexA(gold, 0.5));
  ctx.fillStyle = g; ctx.fillRect(x0, y - 1, x - x0, 2);
  var per = (x - x0) / Math.max(1, n.days);
  if (per * 7 >= 6) for (var d = 7; d < n.days; d += 7) { var tx = x0 + per * d; ctx.fillStyle = ctHexA(gold, 0.35); ctx.fillRect(Math.round(tx), y - 3, 1, 6); }
  ctx.beginPath(); ctx.arc(x0, y, 2.5, 0, Math.PI * 2); ctx.fillStyle = ctHexA(gold, 0.6); ctx.fill();
  var label = n.days === 0 ? 'REPORTS TODAY' : n.days + (n.days === 1 ? ' DAY' : ' DAYS') + ' TO EARNINGS';
  ctx.font = '800 9px "IBM Plex Mono", ui-monospace, monospace'; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
  var mid = (x0 + x) / 2, tw = ctx.measureText(label).width;
  if (x - x0 > tw + 20) { ctx.fillStyle = ctHexA(gold, 0.8); ctx.fillText(label, mid, y - 5); }
  ctx.restore();
}
/* The stock's usual earnings move as a bracket on the report line: last
   price ± the average move, with both prices. Historical, not a forecast. */
function ctDrawEarningsRange(ctx, dim, x, mv, gold) {
  var hiP = mv.last * (1 + mv.avg / 100), loP = mv.last * (1 - mv.avg / 100);
  var yh = ctY(hiP), yl = ctY(loP), ym = ctY(mv.last);
  if (yh == null || yl == null || ym == null) return;
  ctx.save();
  var bw = 14;
  var g = ctx.createLinearGradient(0, yh, 0, yl);
  g.addColorStop(0, 'rgba(34,197,139,0.22)'); g.addColorStop(0.5, ctHexA(gold, 0.08)); g.addColorStop(1, 'rgba(255,77,69,0.22)');
  ctRoundRect(ctx, x - bw / 2, yh, bw, yl - yh, 5); ctx.fillStyle = g; ctx.fill();
  ctx.lineWidth = 1; ctx.strokeStyle = ctHexA(gold, 0.6); ctx.stroke();
  ctx.fillStyle = '#22C58B'; ctx.fillRect(x - bw / 2 - 3, Math.round(yh), bw + 6, 1.5);
  ctx.fillStyle = '#FF4D45'; ctx.fillRect(x - bw / 2 - 3, Math.round(yl), bw + 6, 1.5);
  ctx.beginPath(); ctx.moveTo(x, ym - 4); ctx.lineTo(x + 4, ym); ctx.lineTo(x, ym + 4); ctx.lineTo(x - 4, ym); ctx.closePath();
  ctx.fillStyle = gold; ctx.fill();
  ctx.font = '800 9px "IBM Plex Mono", ui-monospace, monospace'; ctx.textBaseline = 'middle';
  var right = x + bw / 2 + 6 + 90 < dim.W;
  ctx.textAlign = right ? 'left' : 'right';
  var lx = right ? x + bw / 2 + 6 : x - bw / 2 - 6;
  if (yl - yh >= 34) {
    ctx.fillStyle = '#22C58B'; ctx.fillText('+' + mv.avg.toFixed(1) + '%  ' + ctFmt(hiP), lx, yh);
    ctx.fillStyle = '#FF6B6B'; ctx.fillText('−' + mv.avg.toFixed(1) + '%  ' + ctFmt(loP), lx, yl);
    ctx.fillStyle = ctHexA(gold, 0.75); ctx.font = '700 8px "IBM Plex Mono", ui-monospace, monospace';
    ctx.fillText('AVG EARNINGS MOVE · LAST ' + mv.n, lx, (yh + yl) / 2);
  } else {
    // Tight range: one line under the bracket so nothing overlaps.
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    var t1 = 'AVG MOVE ±' + mv.avg.toFixed(1) + '% · ' + ctFmt(loP) + '–' + ctFmt(hiP);
    var tw = ctx.measureText(t1).width, tx = Math.max(tw / 2 + 4, Math.min(dim.W - tw / 2 - 4, x));
    ctx.fillStyle = 'rgba(9,12,18,0.85)'; ctx.fillRect(tx - tw / 2 - 4, yl + 5, tw + 8, 13);
    ctx.fillStyle = ctHexA(gold, 0.9); ctx.fillText(t1, tx, yl + 7);
  }
  ctx.restore();
  ctEventHits.push({x: x, y: (yh + yl) / 2, r: Math.max(10, (yl - yh) / 2), text: 'Average move on its last ' + mv.n + ' earnings days: ±' + mv.avg.toFixed(1) + '% (' + mv.up + ' up, ' + (mv.n - mv.up) + ' down). From past prices — not a forecast.'});
}
/* "A" on the price axis: lit while the axis auto-fits; after a vertical drag
   or axis scaling it dims, and a tap fits the price back to the candles. */
function ctSyncAutoBtn() {
  var box = document.getElementById('tw-chart-box'); if (!box || !twChartState.chart) return;
  var b = document.getElementById('tw-auto-btn');
  if (!b) {
    b = document.createElement('button');
    b.type = 'button'; b.id = 'tw-auto-btn'; b.className = 'tw-auto-btn'; b.textContent = 'A';
    b.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
    b.addEventListener('click', function (e) {
      e.stopPropagation();
      if (typeof twAutoScalePrice === 'function') twAutoScalePrice(); else twChartState.chart.priceScale('right').applyOptions({autoScale: true});
      ctSyncAutoBtn(); twRenderDrawings();
    });
    box.appendChild(b);
  }
  var auto = true;
  try { auto = !!twChartState.chart.priceScale('right').options().autoScale; } catch (e) {}
  b.classList.toggle('is-manual', !auto);
  b.setAttribute('aria-pressed', auto ? 'true' : 'false');
  b.title = auto ? 'Price axis auto-fits the candles. Drag the chart up or down to move it.' : 'Manual price axis — tap to fit the price back to the candles';
  var dim = ctDims();
  // In the corner where the two axes meet (above the axis end when the time
  // axis is the shared one under RSI/MACD).
  var axH = dim.fullH - dim.H;
  b.style.left = Math.round(dim.W + (dim.axisW - 22) / 2) + 'px';
  b.style.top = Math.round(axH >= 20 ? dim.H + (axH - 20) / 2 : dim.H - 24) + 'px';
}
/* Earnings pin: a dark rounded badge ringed in the result colour (beat green,
   miss red, no estimate gold) with a pointer up to the report line, and the
   EPS surprise above it when there's room. */
function ctDrawEarnPin(ctx, x, y, c, e, gap) {
  ctx.save();
  var w = 17, h = 16, top = y - h / 2;
  ctx.shadowColor = ctHexA(c, 0.55); ctx.shadowBlur = 6;
  ctRoundRect(ctx, x - w / 2, top, w, h, 5);
  ctx.fillStyle = 'rgba(10,13,19,0.96)'; ctx.fill();
  ctx.shadowBlur = 0;
  ctx.lineWidth = 1.4; ctx.strokeStyle = c; ctx.stroke();
  ctx.beginPath(); ctx.moveTo(x - 3, top); ctx.lineTo(x, top - 3.5); ctx.lineTo(x + 3, top); ctx.closePath(); ctx.fillStyle = c; ctx.fill();
  ctx.fillStyle = c; ctx.font = '800 9.5px "IBM Plex Mono", ui-monospace, monospace'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText('E', x, y + 0.5);
  if (e.result === 'beat' || e.result === 'miss') {
    ctx.beginPath();
    if (e.result === 'beat') { ctx.moveTo(x + 4.5, top + 5); ctx.lineTo(x + 6.5, top + 2); ctx.lineTo(x + 8.5, top + 5); }
    else { ctx.moveTo(x + 4.5, top + 2); ctx.lineTo(x + 6.5, top + 5); ctx.lineTo(x + 8.5, top + 2); }
    ctx.closePath(); ctx.fill();
  }
  if (gap >= 38 && e.eps != null && e.eps_est != null && Number(e.eps_est) !== 0) {
    var sp = (Number(e.eps) - Number(e.eps_est)) / Math.abs(Number(e.eps_est)) * 100;
    if (isFinite(sp)) {
      var txt = (sp >= 0 ? '+' : '') + (Math.abs(sp) >= 100 ? sp.toFixed(0) : sp.toFixed(1)) + '%';
      ctx.font = '700 8.5px "IBM Plex Mono", ui-monospace, monospace';
      ctx.fillStyle = ctHexA(c, 0.95); ctx.fillText(txt, x, top - 9);
    }
  }
  ctx.restore();
}
var ctTimeAxisMarks = [];
/* Price and time axes: a soft glass strip with a glowing accent edge, the
   earnings notches on the time axis and the next report's date. */
function ctAccent() {
  try { var v = getComputedStyle(document.documentElement).getPropertyValue('--cc-accent').trim(); if (/^#[0-9a-f]{6}$/i.test(v)) return v; } catch (e) {}
  return '#c4f53b';
}
function ctDrawAxisChrome(ctx, dim) {
  if (!dim.W || !dim.H) return;
  var acc = ctAccent();
  ctx.save();
  var g = ctx.createLinearGradient(dim.W, 0, dim.fullW, 0);
  g.addColorStop(0, 'rgba(255,255,255,0.035)'); g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g; ctx.fillRect(dim.W, 0, dim.axisW, dim.H);
  var v = ctx.createLinearGradient(0, 0, 0, dim.H);
  v.addColorStop(0, ctHexA(acc, 0)); v.addColorStop(0.5, ctHexA(acc, 0.45)); v.addColorStop(1, ctHexA(acc, 0.08));
  ctx.fillStyle = v; ctx.fillRect(dim.W, 0, 1, dim.H);
  if (dim.fullH - dim.H > 8) {
    var hz = ctx.createLinearGradient(0, 0, dim.W, 0);
    hz.addColorStop(0, ctHexA(acc, 0.04)); hz.addColorStop(0.7, ctHexA(acc, 0.4)); hz.addColorStop(1, ctHexA(acc, 0.55));
    ctx.fillStyle = hz; ctx.fillRect(0, dim.H, dim.W + 1, 1);
    var tb = ctx.createLinearGradient(0, dim.H, 0, dim.fullH);
    tb.addColorStop(0, 'rgba(255,255,255,0.03)'); tb.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = tb; ctx.fillRect(0, dim.H + 1, dim.W, dim.fullH - dim.H - 1);
    // A small glowing corner where the two axes meet.
    ctx.fillStyle = ctHexA(acc, 0.7); ctx.beginPath(); ctx.arc(dim.W + 0.5, dim.H + 0.5, 2, 0, Math.PI * 2); ctx.fill();
  }
  ctx.restore();
}
function ctDrawTimeAxisMarks(ctx, dim) {
  if (dim.fullH - dim.H < 10) return;   // no native time axis (the shared one below draws them)
  ctDrawTimeMarksAt(ctx, dim.W, dim.H, dim.fullH - dim.H);
}
/* Earnings notches and the next report's date on a time axis whose top edge
   is at y=top (the chart's own axis, or the shared one under RSI/MACD). */
function ctDrawTimeMarksAt(ctx, W, top0, axisH) {
  var dim = {W: W, H: top0, fullH: top0 + axisH};
  ctx.save();
  ctTimeAxisMarks.forEach(function (m) {
    if (m.x < 0 || m.x > dim.W) return;
    if (m.label) {
      ctx.font = '800 9.5px "IBM Plex Mono", ui-monospace, monospace';
      var w = ctx.measureText(m.label).width + 14, h = Math.min(17, dim.fullH - dim.H - 3);
      var left = Math.max(0, Math.min(dim.W - w, m.x - w / 2)), top = dim.H + 2;
      ctx.fillStyle = '#07090d'; ctx.fillRect(left - 6, dim.H + 1, w + 12, dim.fullH - dim.H - 1);   // clear the tick text under it
      ctRoundRect(ctx, left, top, w, h, h / 2);
      ctx.fillStyle = m.est ? 'rgba(9,12,18,0.96)' : m.c; ctx.fill();
      ctx.lineWidth = 1; ctx.strokeStyle = m.c; if (m.est) ctx.setLineDash([3, 2]); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = m.est ? m.c : ctTextOn(m.c); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(m.label, left + w / 2, top + h / 2 + 0.5);
      return;
    }
    ctx.fillStyle = m.c;
    ctx.beginPath(); ctx.moveTo(m.x - 3.5, dim.H + 5); ctx.lineTo(m.x, dim.H + 1); ctx.lineTo(m.x + 3.5, dim.H + 5); ctx.closePath(); ctx.fill();
  });
  ctx.restore();
}
function ctShortDate(iso) {
  var d = new Date(String(iso) + 'T12:00:00Z');
  return isNaN(d.getTime()) ? String(iso || '') : d.toLocaleDateString('en-US', {month: 'short', day: 'numeric', timeZone: 'UTC'});
}
function ctEventAt(x, y) {
  for (var i = 0; i < ctEventHits.length; i++) { var h = ctEventHits[i]; if (Math.abs(h.x - x) <= h.r && Math.abs(h.y - y) <= h.r) return h; }
  return null;
}
function ctEventTip(h, x, y) {
  var tip = document.getElementById('ct-evtip'), box = document.getElementById('tw-chart-box');
  if (!h) { if (tip) tip.hidden = true; return; }
  if (!tip) { tip = document.createElement('div'); tip.id = 'ct-evtip'; tip.className = 'ct-evtip'; (box || document.body).appendChild(tip); }
  var html = h.text ? '<b>' + ctEsc(h.text) + '</b>' : h.items.slice().sort(function (a, b) { return b.ts - a.ts; }).slice(0, 4).map(function (e) {
    var when = '';
    try { when = new Intl.DateTimeFormat('en-US', {timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}).format(new Date(e.ts * 1000)); } catch (_) {}
    return '<div class="ct-evrow"><small>' + ctEsc(when) + (e.source ? ' · ' + ctEsc(e.source) : '') + '</small>' + ctEsc(e.label) + '</div>';
  }).join('') + (h.items.length > 4 ? '<small>+' + (h.items.length - 4) + ' more</small>' : '') + (h.items.some(function (e) { return e.url; }) ? '<small class="ct-evhint">Tap to open</small>' : '');
  tip.innerHTML = html; tip.hidden = false;
  var W = box ? box.clientWidth : 600;
  tip.style.left = Math.max(6, Math.min(W - 286, x - 140)) + 'px';
  tip.style.bottom = '40px';
}


/* ── Right-click on the chart (press and hold on a phone) ───────────
   On empty chart: an alert, a line or a trade plan at the exact price
   under the pointer, the drawing tools, indicators and view. On a
   drawing: its own menu (style, alert, colour, lock, copy, delete).
   The menu itself is static/js/context_menu.js. */
var CT_MENU_COLORS = ['#38d6c5', '#22c58b', '#f0b53f', '#ff9f43', '#ff4d45', '#ff6bcb', '#8b93ff', '#e8edf5'];
function ctMenuAddDrawing(type, points, select) {
  var d = ctNewDrawing(type, points);
  ctPushUndo(); ctStore.drawings.push(d);
  if (ctStore.drawings.length > 300) ctStore.drawings.shift();
  twSaveDrawings();
  if (select) ctSelect(ctStore.drawings.length - 1); else twRenderDrawings();
  return d;
}
function ctMenuCmd(i, cmd) {
  ctSelect(i);
  var f = document.createElement('i'); f.setAttribute('data-ct-cmd', cmd);
  ctPropClick({target: f});
}
function ctMenuAlertAt(price) {
  var t = twChartState.ticker; if (!t || !window.TSMenu) return;
  window.TSMenu.setAlert(t, Math.round(price * 10000) / 10000, ctLastPrice()).then(function (ok) { if (ok) ctLoadAlerts(t, true); });
}
function ctMenuDrawingSpec(i) {
  var d = ctStore.drawings[i], T = window.TSMenu;
  var label = (CT_TOOLS[d.type] || {}).label || 'Drawing', items = [];
  items.push({icon: 'edit', label: 'Edit style & levels', run: function () { ctSelect(i); }});
  if (ctAlertPrices(d).length) items.push({icon: 'bell', label: 'Set alert at this ' + (d.type === 'rect' ? 'box' : d.type === 'long' || d.type === 'short' ? 'stop & target' : 'level'), hint: 'Alt W', run: function () { ctMenuCmd(i, 'alert'); }});
  if ((d.type === 'long' || d.type === 'short') && document.querySelector('[data-ticket], #tw-ticket, .tw-ticket')) items.push({icon: 'ticket', label: 'Send to order ticket', run: function () { ctMenuCmd(i, 'ticket'); }});
  items.push({sep: true});
  items.push({swatches: CT_MENU_COLORS, current: d.style && d.style.color, pick: function (c) {
    ctPushUndo(); d.style.color = c; ctPropsFor = null; twSaveDrawings(); twRenderDrawings();
  }});
  items.push({icon: 'lock', label: d.locked ? 'Unlock' : 'Lock in place', run: function () { ctMenuCmd(i, 'lock'); }});
  items.push({icon: 'dup', label: 'Duplicate', run: function () { ctMenuCmd(i, 'clone'); }});
  if (i !== ctStore.drawings.length - 1) items.push({icon: 'front', label: 'Bring to front', run: function () {
    ctPushUndo(); ctStore.drawings.push(ctStore.drawings.splice(i, 1)[0]); twSaveDrawings(); ctSelect(ctStore.drawings.length - 1);
  }});
  items.push({sep: true});
  items.push({icon: 'trash', label: 'Delete', hint: 'Del', danger: true, run: function () {
    ctSelect(i); twDeleteSelected();
    if (T) T.toast(label + ' deleted', {undo: ctUndo});
  }});
  return {title: label, sub: twChartState.ticker || '', items: items, prefetch: false};
}
function ctMenuChartSpec(c) {
  var T = window.TSMenu, t = twChartState.ticker || '', p = c.price, pt = c.point, F = ctFmt(p);
  var last = ctLastPrice(), diff = last ? (p - last) / last * 100 : null;
  var items = [];
  items.push({icon: 'bell', label: 'Alert at $' + F, hint: diff == null ? '' : (diff >= 0 ? '+' : '−') + Math.abs(diff).toFixed(1) + '%', run: function () { ctMenuAlertAt(p); }});
  if (pt) {
    items.push({icon: 'hline', label: 'Horizontal line at $' + F, hint: 'Alt H', run: function () { ctMenuAddDrawing('hline', [pt]); if (T) T.toast('Line at $' + F + ' · right-click it for options'); }});
    items.push({icon: 'hray', label: 'Horizontal ray from here', run: function () { ctMenuAddDrawing('hray', [pt]); }});
    items.push({icon: 'long', label: 'Plan a long from $' + F, run: function () {
      var l = ctTimeToLogical(pt.t), t2 = ctLogicalToTime((l == null ? 0 : l) + 18);
      ctMenuAddDrawing('long', [pt, {t: t2 == null ? pt.t : t2, p: p * 1.06}], true);
    }});
    items.push({icon: 'short', label: 'Plan a short from $' + F, run: function () {
      var l = ctTimeToLogical(pt.t), t2 = ctLogicalToTime((l == null ? 0 : l) + 18);
      ctMenuAddDrawing('short', [pt, {t: t2 == null ? pt.t : t2, p: p * 0.94}], true);
    }});
    items.push({icon: 'text', label: 'Note here', run: function () {
      ctMenuAddDrawing('text', [pt], true);
      setTimeout(function () { var inp = document.getElementById('ct-p-text'); if (inp) { inp.focus(); inp.select(); } }, 30);
    }});
  }
  items.push({icon: 'pen', label: 'Draw', sub: function () {
    var out = [];
    if (pt) {
      out.push({icon: 'vline', label: 'Vertical line here', run: function () { ctMenuAddDrawing('vline', [pt]); }});
      out.push({icon: 'wave', label: 'Anchored VWAP from this bar', run: function () { ctMenuAddDrawing('avwap', [pt]); }});
      out.push({sep: true}, {note: 'Pick a tool, then click the chart:'});
    }
    [['trend', 'pen', 'Alt T'], ['fib', 'fib', 'Alt F'], ['rect', 'rect', 'Alt B'], ['channel', 'chan', 'Alt C'], ['measure', 'ruler', ''], ['pctlevels', 'fib', ''], ['ray', 'pen', ''], ['arrow', 'pen', '']].forEach(function (r) {
      if (CT_TOOLS[r[0]]) out.push({icon: r[1], label: CT_TOOLS[r[0]].label, hint: r[2], run: function () { twStartDrawing(r[0]); }});
    });
    return out;
  }});
  items.push({icon: 'wave', label: 'Indicators', sub: function () {
    var ind = twChartState.indicators || {}, out = [];
    function study(label, path) {
      var parts = path.split('.'), on = !!(ctPrefs[parts[0]] && ctPrefs[parts[0]][parts[1]]);
      out.push({label: label, checked: function () { var o = ctPrefs[parts[0]]; return !!(o && o[parts[1]]); }, keep: true, run: function () { var o = ctPrefs[parts[0]]; ctSet(path, !(o && o[parts[1]])); }});
      return on;
    }
    if (typeof twToggleIndicator === 'function') {
      ['rsi', 'macd'].forEach(function (n) {
        out.push({label: n === 'rsi' ? 'RSI (14)' : 'MACD (12, 26, 9)', keep: true, checked: function () { return !!(twChartState.indicators || {})[n]; },
          run: function () { var cb = document.getElementById('tw-ind-' + n); if (cb) { cb.checked = !ind[n]; twToggleIndicator(n, cb); } else twToggleIndicator(n); ind = twChartState.indicators; }});
      });
    }
    study('VWAP', 'vwap.on');
    study('Bollinger Bands (' + ctPrefs.bb.len + ', ' + ctPrefs.bb.mult + ')', 'bb.on');
    study('SMA ' + ctPrefs.sma.len, 'sma.on');
    study('Key levels (PDH / PDL / PDC)', 'levels.on');
    out.push({sep: true});
    study('Volume', 'view.volume');
    study('Value zones', 'view.zones');
    study('Earnings & news marks', 'view.events');
    return out;
  }});
  items.push({sep: true});
  if (ctStore.undo && ctStore.undo.length) items.push({icon: 'undo', label: 'Undo', hint: 'Ctrl Z', run: ctUndo});
  if (ctStore.drawings.length) {
    items.push({icon: 'eye', label: ctPrefs.draw.hidden ? 'Show drawings' : 'Hide drawings', run: function () { ctSet('draw.hidden', !ctPrefs.draw.hidden); }});
    items.push({icon: 'trash', danger: true, label: 'Remove all ' + ctStore.drawings.length + ' drawing' + (ctStore.drawings.length === 1 ? '' : 's'), run: function () {
      var n = ctStore.drawings.length;
      ctPushUndo(); ctStore.drawings = []; twChartState.selectedDrawing = null; twSaveDrawings(); twStopDrawing();
      if (T) T.toast(n + ' drawing' + (n === 1 ? '' : 's') + ' removed from ' + t, {undo: ctUndo});
    }});
  }
  if (typeof twResetView === 'function') items.push({icon: 'reset', label: 'Reset chart view', hint: 'Alt R', run: twResetView});
  if (typeof twToggleFullscreen === 'function') items.push({icon: 'full', label: 'Full screen chart', hint: 'Shift F', run: twToggleFullscreen});
  items.push({icon: 'copy', label: 'Copy price $' + F, run: function () { if (T) T.copy(F, '$' + F); }});
  if (t && T) {
    items.push({sep: true});
    items.push({icon: 'star', label: t + ' — more', sub: function () { return T.tickerItems({ticker: t, price: last, inChart: true}); }});
  }
  return {title: t + '  ·  $' + F, sub: diff == null ? '' : (diff >= 0 ? '+' : '−') + Math.abs(diff).toFixed(2) + '% from last', items: items, prefetch: false};
}
if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  (window.TSMenuQueue = window.TSMenuQueue || []).push({
    priority: 30,
    match: function (target, ev) {
      var box = target.closest && target.closest('#tw-chart-box');
      if (!box || !twChartState.chart || !ctCanvas()) return null;
      if (target.closest('button, input, select, textarea, a, label, [data-ct-group], [data-ct-fb], [role="dialog"], .ct-notice')) return null;
      var c = ctLocal(ev), dim = ctDims();
      if (c.y < 0 || c.y > dim.H || c.x < 0) return null;
      var hit = c.x <= dim.W ? ctHit(c.x, c.y) : null;
      if (hit) return {drawing: hit.index};
      var price = ctPriceAt(c.y);
      if (price == null || !(price > 0)) return null;
      return {price: price, point: c.x <= dim.W ? ctPointAt(c.x, c.y, true) : null};
    },
    build: function (c) {
      if (ctMode) twStopDrawing();
      return c.drawing != null ? ctMenuDrawingSpec(c.drawing) : ctMenuChartSpec(c);
    }
  });
}
