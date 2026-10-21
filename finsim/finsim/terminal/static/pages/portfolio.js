/* Portfolio (TERMINAL_DESIGN.md s17.5 #/portfolio[/tab], B16; WP-12).  Tabs Positions | Performance.  Positions: the
 * consolidated GET /api/portfolio (scope ALL = the selected book's open lifetime; "All books" toggle = ALL_BOOKS) with
 * the B16 columns - account, instrument, class, signed quantity (footer: gross long / gross short from the server),
 * average cost, mark (freshness), market value, today's P&L, unrealised, realised / income / financing to date, % of
 * NAV, days held - grouped by class with the server's by_class subtotals; filters (account, class, text); Copy and CSV;
 * tiles NAV + TODAY / MTD / YTD / ITD.  Every figure is a field of accounting.positions / nav / pnl - the page computes
 * nothing (when the server sends no by_class row for a group the subtotal cell says so instead of adding).
 * Performance mounts components/performance.js (WP-20) or shows NOT YET BUILT.
 */
(function () {
  'use strict';
  var T = window.T;
  function money(name, label) { return { key: name, label: label || name.replace(/_/g, ' '), html: function (r) { return '<span class="' + T.sign(r, name) + '">' + T.esc(T.money(r, name)) + '</span>'; }, text: function (r) { return T.money(r, name); }, sort: function (r) { return T.num(r, name); } }; }
  var COLS = [
    { key: 'account_id', label: 'account', cls: 'l', text: function (r) { return r.account_name || r.account_id; } },
    { key: 'symbol', label: 'instrument', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b> <span class="muted">' + T.esc(r.name || '') + '</span>'; }, text: function (r) { return (r.symbol || r.instrument_id) + ' ' + (r.name || ''); } },
    { key: 'klass', label: 'class', cls: 'l', text: function (r) { return r.klass || r.kind || ''; } },
    { key: 'qty', label: 'qty (signed)', html: function (r) { return '<span class="' + (r.qty > 0 ? 'pos' : 'neg') + '">' + T.esc(T.fmt.qty(r.qty)) + '</span>'; }, text: function (r) { return String(r.qty); }, sort: function (r) { return r.qty; } },
    { key: 'avg_px', label: 'avg cost', text: function (r) { return r.avg_px == null ? 'n/a' : T.fmt.px(r.avg_px); } },
    { key: 'mark', label: 'mark', html: function (r) { return T.pxHtml(r.mark); }, text: function (r) { return r.mark && r.mark.mark != null ? String(r.mark.mark) : ''; }, sort: function (r) { return r.mark && r.mark.mark != null ? r.mark.mark : ''; } },
    money('mv_base', 'market value (USD)'), money('day_pnl', 'P&L today'), money('unrealised_base', 'unrealised'), money('realised_itd', 'realised to date'), money('income_itd', 'income to date'), money('financing_itd', 'financing to date'),
    { key: 'weight', label: '% NAV', text: function (r) { return r.weight == null ? '' : T.fmt.pct(r.weight, 2); }, sort: function (r) { return r.weight == null ? '' : r.weight; } },
    { key: 'days_held', label: 'days held', text: function (r) { return r.days_held == null ? '' : String(r.days_held); } }
  ];
  function render(el, route) {
    var tab = route.tab || 'positions'; var allBooks = T.store.get('portfolio.allbooks', false); var data = null; var perf = null;
    var toggle = T.h('label', { class: 'row', title: 'ALL_BOOKS consolidates the three open lifetimes (books never transfer to each other; the view simply sums)' }, T.h('input', { type: 'checkbox', checked: allBooks, onchange: function (e) { allBooks = e.target.checked; T.store.set('portfolio.allbooks', allBooks); load(); } }), ' All books');
    T.pageHead(el, 'Portfolio', allBooks ? 'ALL_BOOKS' : (T.book.current() ? T.book.current().name : ''), [toggle]);
    el.appendChild(T.tabs([{ key: 'positions', label: 'Positions' }, { key: 'performance', label: 'Performance' }], tab, function (k) { T.router.go('#/portfolio/' + k); }));
    var tiles = T.h('div'); var body = T.h('div'); el.appendChild(tiles); el.appendChild(body);
    var acctSel = T.h('select', { 'aria-label': 'account filter' }), classSel = T.h('select', { 'aria-label': 'class filter' });
    var grid = null;
    function scope() { return allBooks ? 'ALL_BOOKS' : 'ALL'; }
    function load() {
      if (tab === 'performance') {
        T.clear(tiles); T.clear(body);
        var m = T.h('div', { class: 'panel' }); body.appendChild(m);
        perf = T.mountOptional('performance', m, { scope: scope(), book: T.book.code }, 'components/performance.js (WP-20): Sharpe, Sortino, beta / alpha, drawdown, hit rate, monthly table');
        body.appendChild(T.convNote(['PERF']));
        return Promise.resolve();
      }
      return T.api.get('/api/portfolio', { scope: scope() }).then(function (d) { data = d; paint(); }, function (e) { T.clear(body); T.errorBox(body, e, 'portfolio'); });
    }
    function tile(label, p) { return p ? { k: label, v: T.money(p, 'pnl'), s: (p.degraded ? 'baseline: inception' : (p.baseline && (p.baseline.snap_date || p.baseline.note) ? 'vs ' + (p.baseline.snap_date || p.baseline.note) : '')) + (p.residual != null && T.num(p, 'residual') ? ' . residual ' + T.money(p, 'residual') : ''), cls: T.sign(p, 'pnl'), href: '#/accounting/pnl' } : { k: label, v: 'n/a' }; }
    function paint() {
      var nav = data.nav || {}; var pnl = data.pnl || {};
      T.clear(tiles);
      tiles.appendChild(T.tiles([
        { k: 'NAV (equity)', v: T.money(nav, 'nav'), s: (nav.partial ? 'NAV excludes ' + (nav.missing || []).length + ' unpriced position(s)' : 'all positions priced') + ' . ' + (nav.quality || ''), cls: nav.partial ? 'neg' : '', title: 'assets ' + T.money(nav, 'assets') + ' - liabilities ' + T.money(nav, 'liabilities') + ' . unrealised ' + T.money(nav, 'unrealised') + ' . fx result ' + T.money(nav, 'fx_result') },
        tile('TODAY', pnl.TODAY), tile('MTD', pnl.MTD), tile('YTD', pnl.YTD), tile('ITD', pnl.ITD)]));
      T.clear(body);
      var rows = data.positions || [];
      var accts = {}; var classes = {};
      rows.forEach(function (r) { accts[r.account_id] = r.account_name || r.account_id; classes[r.klass || r.kind || ''] = 1; });
      T.clear(acctSel); acctSel.appendChild(T.h('option', { value: '' }, 'all accounts')); Object.keys(accts).forEach(function (a) { acctSel.appendChild(T.h('option', { value: a }, accts[a])); });
      T.clear(classSel); classSel.appendChild(T.h('option', { value: '' }, 'all classes')); Object.keys(classes).forEach(function (c) { classSel.appendChild(T.h('option', { value: c }, c || '(none)')); });
      body.appendChild(T.h('div', { class: 'row', style: 'margin-bottom:6px' }, acctSel, classSel, T.h('span', { class: 'hint' }, data.marks_partial ? 'marks partial: some positions have no valid mark' : '')));
      var byClass = {}; (data.by_class || []).forEach(function (c) { byClass[c.klass || c.class || ''] = c; });
      var gEl = T.h('div'); body.appendChild(gEl);
      function filtered() { return rows.filter(function (r) { return (!acctSel.value || r.account_id === acctSel.value) && (!classSel.value || (r.klass || r.kind || '') === classSel.value); }); }
      grid = T.components.grid.mount(gEl, {
        columns: COLS, rows: filtered(), focus: true, csv: 'portfolio-' + scope() + '.csv', empty: 'no positions', groupBy: function (r) { return r.klass || r.kind || ''; },
        subtotal: function (grp, label) { var c = byClass[label]; return c ? { symbol: 'subtotal ' + label + ' (' + (c.count != null ? c.count : grp.length) + ')', mv_base: T.esc(T.money(c, 'mv')), unrealised_base: T.esc(T.money(c, 'unrealised')), day_pnl: T.esc(T.money(c, 'day_pnl')), weight: c.weight == null ? '' : T.esc(T.fmt.pct(c.weight)) } : { symbol: 'subtotal ' + label + ' (server sent no by_class row)' }; },
        footer: function () { var f = data.footer || data.totals || {}; return { symbol: 'gross long ' + T.money(f, 'gross_long') + ' . gross short ' + T.money(f, 'gross_short'), mv_base: T.esc(T.money(nav, 'mv_long')) + ' / ' + T.esc(T.money(nav, 'mv_short')), unrealised_base: T.esc(T.money(nav, 'unrealised')) }; },
        onEnter: function (r) { T.router.go(T.router.instrument(r.account_id, r.instrument_id)); }
      });
      acctSel.onchange = classSel.onchange = function () { grid.update(filtered()); };
      var ba = T.h('div', { class: 'panel' }, T.h('h2', null, 'by account'));
      var brows = data.by_account || [];
      if (brows.length) { var bg = T.h('div'); ba.appendChild(bg); T.components.grid.mount(bg, { tools: false, rows: brows, columns: [{ key: 'account_id', label: 'account', cls: 'l', text: function (r) { return r.name || r.account_id; } }, { key: 'nav', label: 'NAV', text: function (r) { return T.money(r, 'nav'); } }, { key: 'pnl_today', label: 'P&L today', html: function (r) { return '<span class="' + T.sign(r, 'pnl_today') + '">' + T.esc(T.money(r, 'pnl_today')) + '</span>'; } }, { key: 'weight', label: '% of total', text: function (r) { return r.weight == null ? '' : T.fmt.pct(r.weight); } }], onEnter: function (r) { T.router.go(T.router.account(r.account_id, 'positions')); }, onClick: function (r) { if (r.account_id && r.account_id.indexOf(':TREASURY') < 0) { T.router.go(T.router.account(r.account_id, 'positions')); } } }); }
      else { ba.appendChild(T.h('div', { class: 'empty' }, 'no accounts')); }
      body.appendChild(ba);
      body.appendChild(T.convNote(['BOOKS', 'ACCT_DAY', 'FX_RESULT', 'STALE']));
      T.watchQuotes(rows.map(function (r) { return r.instrument_id; }));
    }
    load();
    T.poll.page('portfolio', 15000, function () { return tab === 'positions' && data ? load() : Promise.resolve(); });
    var off = T.state.on('book', function () { if (perf && perf.refresh) { perf.refresh({ scope: scope(), book: T.book.code }); } });
    T.pages.portfolio.unmount = function () { off(); if (perf && perf.unmount) { perf.unmount(); } T.pages.portfolio.unmount = null; };
  }
  T.pages.portfolio = { render: render, unmount: null, COLS: COLS };
})();
