/* Portfolio -> Performance tab (TERMINAL_DESIGN.md s6.9, s17.1, s19.20; WP-20).
 *
 * Renders GET /api/portfolio/performance?scope=... (+ book= when given) and computes NOTHING: every figure, count,
 * date and note on this tab is a field of the PerfView JSON produced by finsim/terminal/performance.py; this file only
 * formats what it is given.  Mounted by pages/portfolio.js (WP-12):
 *
 *     var h = T.components.performance.mount(el, {scope: 'ALL', book: 'P'});   // fetches at once
 *     h.refresh({book: 'F'});                                                 // after a book switch
 *     h.unmount();
 *
 * Conventions shown on the tab
 * - Until two daily snapshots exist the tab shows "insufficient history (n of 2 snapshots)" and the sentence PERF.
 * - Ratios arrive as floats (4 dp); percentages are shown at 2 dp with the 4-dp value in the hover; NAVs and flows
 *   arrive as cents (`*_c`) or, through the API layer, as decimal strings -- both are accepted (money()).
 * - A failed request renders the error text in the tab (never a blank, never a stale table).
 * - PORTFOLIO-15: the returns are TRADING-DAY returns (a weekend or holiday snapshot is folded into the next trading day; the first
 *   return is the inception day's).  The annualised ratios (return, volatility, Sharpe, Sortino, beta, alpha) show from the 20th
 *   trading-day return (``ratios_status``, ``needed_returns``); before that their cells read "insufficient history" while the
 *   cumulative time-weighted return (``cum_twr``), drawdown, hit rate and the dated daily series (``daily``) are always shown.
 * - No dependencies: plain DOM, window.fetch, Intl.  The file is ASCII.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};

  var PATH = '/api/portfolio/performance';
  var NF2 = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var NF0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* A money field: `row[name + '_c']` (integer cents, the view's own shape) or `row[name]` (a decimal string, the
   * API-layer shape).  Formatting only: cents -> dollars is a display conversion, not a statistic. */
  function money(row, name) {
    if (!row) { return 'n/a'; }
    var c = row[name + '_c'];
    if (typeof c === 'number' && isFinite(c)) {
      var neg = c < 0; var a = Math.abs(c);
      var whole = Math.floor(a / 100); var frac = a % 100;
      return (neg ? '-' : '') + NF0.format(whole) + '.' + (frac < 10 ? '0' : '') + frac;
    }
    var s = row[name];
    if (s == null || s === '') { return 'n/a'; }
    var n = Number(s);
    return isFinite(n) ? NF2.format(n) : esc(s);
  }

  function pct(x) {               /* a ratio shown as a percentage, 2 dp, 4-dp hover */
    if (x == null || !isFinite(x)) { return '<span class="na">n/a</span>'; }
    return '<span title="' + esc(x) + '">' + NF2.format(x * 100) + ' %</span>';
  }

  function ratio(x) {             /* a pure number (Sharpe, Sortino, beta), 2 dp, 4-dp hover */
    if (x == null || !isFinite(x)) { return '<span class="na">n/a</span>'; }
    return '<span title="' + esc(x) + '">' + NF2.format(x) + '</span>';
  }

  function sofrText(v) {
    if (!v.sofr) { return 'SOFR unavailable'; }
    var n = Number(v.sofr);
    return 'SOFR ' + (isFinite(n) ? NF2.format(n * 100) + ' %' : esc(v.sofr)) + (v.sofr_as_of ? ' (' + esc(v.sofr_as_of) + ')' : '');
  }

  function windowText(v) {
    var parts = [];
    if (v.lifetime_id) { parts.push(esc(v.lifetime_id)); }
    parts.push(v.n_snapshots + ' snapshot' + (v.n_snapshots === 1 ? '' : 's'));
    if (v.first_date && v.last_date) { parts.push(esc(v.first_date) + ' \u2192 ' + esc(v.last_date)); }
    if (v.partial_snapshots) { parts.push(v.partial_snapshots + ' partial'); }
    return parts.join(' \u00b7 ');
  }

  function statsTable(v) {
    /* PORTFOLIO-15: the annualised ratios wait for 20 trading-day returns; the server says so (ratios_status / needed_returns) */
    var gated = v.ratios_status === 'insufficient history' && v.needed_returns;
    function g(html) { return gated ? '<span class="na" title="the annualised ratios show from the ' + esc(v.needed_returns) + 'th trading-day return">insufficient history (' + esc(v.n_returns) + ' of ' + esc(v.needed_returns) + ')</span>' : html; }
    var days = v.trading_days ? ' trading-day' : ' daily';
    var rows = [
      ['Cumulative return (time-weighted)', pct(v.cum_twr), 'the' + days + ' returns chained: product of (1 + r) - 1' + (v.first_date ? ', from ' + esc(v.first_date) : '')],
      ['Annualised return', g(pct(v.ann_ret)), 'mean' + days + ' return \u00d7 252'],
      ['Volatility', g(pct(v.vol)), 'stdev of' + days + ' returns \u00d7 \u221a252'],
      ['Sharpe', g(ratio(v.sharpe)), 'vs ' + sofrText(v) + ', \u00d7 \u221a252'],
      ['Sortino', g(ratio(v.sortino)), 'downside deviation over all days, vs ' + sofrText(v)],
      ['Beta (' + esc(v.benchmark) + ')', g(ratio(v.beta)), v.pairs_used + days + ' pairs on observed closes' + (v.pairs_skipped ? ', ' + v.pairs_skipped + ' skipped' : '')],
      ['Alpha (annualised)', g(pct(v.alpha)), 'intercept of the' + days + ' regression \u00d7 252'],
      ['Max drawdown', pct(v.max_dd), 'on the flow-adjusted index'],
      ['Hit rate', pct(v.hit_rate), v.n_returns + days + ' returns' + (v.returns_skipped ? ', ' + v.returns_skipped + ' skipped' : '')]
    ];
    var h = '<table class="perf-table grid"><thead><tr><th>statistic</th><th class="num">value</th><th>basis</th></tr></thead><tbody>';
    for (var i = 0; i < rows.length; i++) {
      h += '<tr><td>' + rows[i][0] + '</td><td class="num">' + rows[i][1] + '</td><td class="muted">' + rows[i][2] + '</td></tr>';
    }
    return h + '</tbody></table>';
  }

  function monthlyTable(v) {
    var m = v.monthly || [];
    var h = '<table class="perf-table grid monthly"><thead><tr><th>month</th><th class="num">NAV start</th><th class="num">flows</th>' +
            '<th class="num">NAV end</th><th class="num">return</th><th class="num">days</th></tr></thead><tbody>';
    if (!m.length) { h += '<tr><td colspan="6" class="muted">no complete month yet</td></tr>'; }
    for (var i = 0; i < m.length; i++) {
      var r = m[i];
      h += '<tr><td title="' + esc(r.start_date) + ' \u2192 ' + esc(r.end_date) + '">' + esc(r.month) + '</td>' +
           '<td class="num">' + money(r, 'nav_start') + '</td><td class="num">' + money(r, 'flows') + '</td>' +
           '<td class="num">' + money(r, 'nav_end') + '</td><td class="num">' + pct(r.ret) + '</td>' +
           '<td class="num">' + esc(r.n_snapshots) + '</td></tr>';
    }
    return h + '</tbody></table>';
  }

  /* PORTFOLIO-15: the dated daily series (the server's records: date, the folded weekend / holiday dates, NAV, flows, return, cumulative) */
  function dailyTable(v) {
    var d = v.daily || [];
    var h = '<table class="perf-table grid daily"><thead><tr><th>date</th><th>folded in</th><th class="num">NAV</th><th class="num">flows</th>' +
            '<th class="num">return</th><th class="num">cumulative</th></tr></thead><tbody>';
    if (!d.length) { h += '<tr><td colspan="6" class="muted">no return yet</td></tr>'; }
    for (var i = d.length - 1; i >= 0; i--) {
      var r = d[i];
      h += '<tr><td title="' + (r.from_date ? 'from ' + esc(r.from_date) : 'inception: NAV over the capital received') + '">' + esc(r.date) + (r.from_date ? '' : ' <span class="muted">(inception)</span>') + '</td>' +
           '<td class="muted">' + esc((r.folded || []).join(', ')) + '</td><td class="num">' + money(r, 'nav') + '</td><td class="num">' + money(r, 'flows') + '</td>' +
           '<td class="num">' + pct(r.ret) + '</td><td class="num">' + pct(r.cum) + '</td></tr>';
    }
    return h + '</tbody></table>';
  }

  function notes(v) {
    var n = v.notes || [];
    if (!n.length) { return ''; }
    var h = '<ul class="perf-notes muted">';
    for (var i = 0; i < n.length; i++) { h += '<li>' + esc(n[i]) + '</li>'; }
    return h + '</ul>';
  }

  /* C9d: the snapshots read from a restated version are labelled "restated on <date> (batch X)"; the dates are in the hover */
  function restatedTag(v) {
    var r = v.restated || [];
    if (!r.length) { return ''; }
    var dates = r.map(function (x) { return x.snap_date; }).join(', ');
    return ' <span class="badge-asof" title="' + esc(r.length + ' snapshot(s) ' + (r[0].text || 'restated') + ': ' + dates + ' (the originals are kept unchanged)') + '">restated</span>';
  }

  function render(el, v) {
    var h = '<div class="perf"><div class="perf-head"><span class="perf-title">Performance</span>' + restatedTag(v) +
            '<span class="perf-window muted">' + windowText(v) + '</span></div>';
    if (v.status !== 'ok') {
      h += '<p class="perf-empty">' + esc(v.status) + ' (' + v.n_snapshots + ' of ' + v.needed + ' snapshots)</p>';
      h += notes(v);
    } else {
      h += statsTable(v);
      h += '<h4 class="perf-sub">Monthly (NAV to NAV, flows excluded)</h4>' + monthlyTable(v);
      h += '<h4 class="perf-sub">' + (v.trading_days ? 'Trading-day returns (weekend and holiday snapshots folded into the next trading day)' : 'Daily returns') + '</h4>' + dailyTable(v);
      h += notes(v);
    }
    h += '<p class="perf-sentence conv">' + esc(v.sentence || '') + '</p></div>';
    el.innerHTML = h;
  }

  function renderError(el, msg) {
    el.innerHTML = '<div class="perf"><div class="perf-head"><span class="perf-title">Performance</span></div>' +
                   '<p class="perf-error error">performance statistics unavailable: ' + esc(msg) + '</p></div>';
  }

  function url(opts) {
    var q = [];
    if (opts.scope) { q.push('scope=' + encodeURIComponent(opts.scope)); }
    if (opts.book) { q.push('book=' + encodeURIComponent(opts.book)); }
    return PATH + (q.length ? '?' + q.join('&') : '');
  }

  /* isCurrent(): false once a newer refresh() superseded this request (its response is then dropped). */
  function load(el, opts, isCurrent) {
    var u = url(opts);
    return window.fetch(u, { cache: 'no-store', headers: { 'Accept': 'application/json' } }).then(function (res) {
      return res.text().then(function (txt) {
        var body = null;
        try { body = txt ? JSON.parse(txt) : null; } catch (e) { body = null; }
        if (!res.ok) {
          var m = body && body.error && body.error.message ? body.error.message : ('HTTP ' + res.status);
          throw new Error(m);
        }
        if (!body || typeof body.status !== 'string') { throw new Error('malformed response from ' + u); }
        return body;
      });
    }).then(function (view) {
      if (!isCurrent() || !el.isConnected) { return view; }
      render(el, view);
      return view;
    }, function (err) {
      if (!isCurrent() || !el.isConnected) { throw err; }
      renderError(el, err && err.message ? err.message : String(err));
      throw err;
    });
  }

  var STYLE_ID = 'perf-style';
  function ensureStyle() {      /* the tab's own classes; terminal.css provides grid / num / muted / conv / error */
    if (!document.head || document.getElementById(STYLE_ID)) { return; }
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = '.perf-head{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin:4px 0 8px}' +
      '.perf-title{font-weight:600}.perf-sub{margin:12px 0 6px;font-size:12px}.perf-table td,.perf-table th{padding:3px 8px}' +
      '.perf-notes{margin:6px 0 0 16px;font-size:11px}.perf-empty{font-family:var(--mono)}.na{opacity:.6}';
    document.head.appendChild(s);
  }

  function mount(el, opts) {
    if (!el) { throw new Error('performance.mount: no element'); }
    ensureStyle();
    var o = { scope: (opts && opts.scope) || 'ALL', book: opts && opts.book };
    var seq = 0;
    el.innerHTML = '<div class="perf"><div class="perf-head"><span class="perf-title">Performance</span>' +
                   '<span class="perf-window muted">loading\u2026</span></div></div>';
    function refresh(next) {
      if (next) { if (next.scope) { o.scope = next.scope; } if ('book' in next) { o.book = next.book; } }
      var mine = ++seq;
      return load(el, o, function () { return seq === mine; }).catch(function () { return null; });
    }
    var first = refresh();
    return { refresh: refresh, unmount: function () { seq = -1; el.innerHTML = ''; }, ready: first, options: o };
  }

  T.components.performance = { mount: mount, render: render, money: money, pct: pct, ratio: ratio, url: url, PATH: PATH };
})();
