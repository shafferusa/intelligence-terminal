/* Portfolio (TERMINAL_DESIGN.md s17.5 #/portfolio[/tab], B16; WP-12; Build 2 P03).  Tabs Positions | Performance.
 * Positions: the consolidated GET /api/portfolio (scope ALL = the selected book's open lifetime; the "All books" toggle =
 * ALL_BOOKS) with the B16 columns - account (name; the book too in All books), instrument, class, signed quantity, average
 * cost, mark (freshness), market value, futures notional, today's P&L, unrealised, realised / income / financing to date,
 * % of the scope's NAV (the server's weight and basis), days held - grouped by class with the server's by_class subtotals
 * (a list; an older dict answer is read too), the footer (gross long / gross short / net and futures notional, server
 * figures), filters (account by name, class, text), Copy / CSV of the rows and the server's Positions CSV; tiles NAV +
 * TODAY / MTD / YTD / ITD; the 'by account' table (NAV, P&L today, % of total, positions).  The page computes nothing.
 * The layout is built once per scope and the 15 s poll only updates it (the filters and the grid's selection stay).
 * T.pages.portfolio.mountView(el, opts) is reused read-only by the History pages.
 * Performance mounts components/performance.js (WP-20).
 */
(function () {
  'use strict';
  var T = window.T;
  function money(name, label, title) { return { key: name, label: label || name.replace(/_/g, ' '), title_h: title, html: function (r) { return '<span class="' + T.sign(r, name) + '">' + T.esc(T.money(r, name)) + '</span>'; }, text: function (r) { return T.money(r, name); }, sort: function (r) { return T.num(r, name); } }; }
  var BASIS_LABEL = { "share of the book's NAV": '% book NAV', 'share of the NAV of all books': '% all-books NAV', "share of the lifetime's final NAV": '% final NAV', "share of the account's NAV": '% acct NAV' };
  function shortBook(name) { return String(name || '').replace(/ Book$/, ''); }
  function columns(o) {
    o = o || {};
    return [
      { key: 'account_id', label: 'account', cls: 'l', text: function (r) { return (r.account_name || T.accountName(r.account_id)) + (o.allBooks && r.book_name ? ' . ' + shortBook(r.book_name) : ''); }, title: function (r) { return r.account_id + (r.book_name ? ' . ' + r.book_name : ''); } },
      { key: 'symbol', label: 'instrument', cls: 'l', html: function (r) {
          var nm = String((r.extra && r.extra.label) || r.name || '');          /* OTC-R1-03: an OTC contract's short label */
          var full = String(r.name || nm);
          var h = '<b title="' + T.esc(full + ' (' + (r.klass || r.kind || '') + ')') + '">' + T.esc(r.symbol || r.instrument_id) + '</b> <span class="muted" title="' + T.esc(full) + '">' + T.esc(nm.length > 14 ? nm.slice(0, 13) + '...' : nm) + '</span>';
          /* a future carries no market value: its exposure (contracts x multiplier x mark) is said here */
          if (r.kind === 'FUTURE' && (r.notional != null || r.notional_c != null)) { h += '<div class="muted" title="futures exposure: contracts x multiplier x mark (not a market value)">notional ' + T.esc((r.currency && r.currency !== 'USD' ? r.currency + ' ' : '') + T.money(r, 'notional')) + '</div>'; }
          return h;
        }, text: function (r) { return (r.symbol || r.instrument_id) + ' ' + (r.name || ''); } },
      { key: 'qty', label: 'qty', title_h: 'signed quantity: negative = short', html: function (r) { return '<span class="' + (r.qty > 0 ? 'pos' : (r.qty < 0 ? 'neg' : '')) + '">' + T.esc(T.fmt.qty(r.qty)) + '</span>'; }, text: function (r) { return String(r.qty); }, sort: function (r) { return r.qty; } },
      { key: 'avg_px', label: 'avg cost', text: function (r) { return r.avg_px == null ? 'n/a' : T.fmt.px(r.avg_px); } },
      { key: 'mark', label: 'mark', html: function (r) { return T.pxHtml(r.mark); }, text: function (r) { return r.mark && r.mark.mark != null ? String(r.mark.mark) : ''; }, sort: function (r) { return r.mark && r.mark.mark != null ? r.mark.mark : ''; } },
      money('mv_base', 'mkt value USD', 'market value in USD at the snapshot FX; a future carries none (its P&L is open trade equity): its notional is under the instrument'),
      money('day_pnl', 'P&L today'), money('unrealised_base', 'unrealised'), money('realised_itd', 'realised', 'realised to date'), money('income_itd', 'income', 'income to date (dividends, coupons, interest)'),
      money('financing_itd', 'financing', 'financing to date (borrow fees, loan interest; a cost is negative)'),
      { key: 'weight', label: BASIS_LABEL[o.basis] || '% NAV', title_h: (o.basis || 'share of NAV') + ' (market value / NAV)', text: function (r) { return r.weight == null ? '' : T.fmt.pct(r.weight, 2); }, sort: function (r) { return r.weight == null ? '' : Number(r.weight); } },
      { key: 'days_held', label: 'days', title_h: 'days held (since the oldest open lot)', text: function (r) { return r.days_held == null ? '' : String(r.days_held); } }
    ];
  }
  /* by_class: a list [{klass, count, mv, unrealised, day_pnl, weight}] (BM-04); an older dict {klass: {n, mv_base, unrealised_base}} is read too */
  function classRows(bc) {
    if (Array.isArray(bc)) { return bc; }
    return Object.keys(bc || {}).map(function (k) {
      var c = bc[k] || {};
      return { klass: k, count: c.count != null ? c.count : c.n, mv: c.mv != null ? c.mv : c.mv_base, unrealised: c.unrealised != null ? c.unrealised : c.unrealised_base,
               day_pnl: c.day_pnl, weight: c.weight, missing: c.missing };
    });
  }
  function pnlTile(label, p, href) {
    if (!p) { return { k: label, v: 'n/a' }; }
    if (p.error) { return { k: label, v: 'n/a', s: p.error, cls: 'neg' }; }
    var b = p.baseline || {};
    var since = b.label ? (b.inception ? 'since ' + b.label : 'vs ' + b.label) : (p.degraded ? 'baseline: inception' : (b.snap_date ? 'vs ' + b.snap_date : ''));
    return { k: label, v: T.money(p, 'pnl'), s: since + (p.residual != null && T.num(p, 'residual') ? ' . residual ' + T.money(p, 'residual') : ''), cls: T.sign(p, 'pnl'), href: href || null, title: b.note || '' };
  }
  function navTile(nav, label) {
    return { k: label || 'NAV (equity)', v: T.money(nav, 'nav'), s: (nav.partial ? 'NAV carries ' + (nav.missing || []).length + ' unpriced position(s) at cost' : 'all positions priced') + (nav.quality ? ' . ' + nav.quality : ''),
             cls: nav.partial ? 'neg' : '', title: 'assets ' + T.money(nav, 'assets') + ' - liabilities ' + T.money(nav, 'liabilities') + ' . unrealised ' + T.money(nav, 'unrealised') + ' . fx result ' + T.money(nav, 'fx_result') };
  }
  function liveTiles(d) {
    var nav = d.nav || {}; var pnl = d.pnl || {};
    return [navTile(nav), pnlTile('TODAY', pnl.TODAY, '#/accounting/pnl'), pnlTile('MTD', pnl.MTD, '#/accounting/pnl'), pnlTile('YTD', pnl.YTD, '#/accounting/pnl'), pnlTile('ITD', pnl.ITD, '#/accounting/pnl')];
  }

  /* mountView(el, {tiles(d) -> [tile], allBooks, readOnly, csv, emptyText}) -> {update(data), error(e)} */
  function mountView(el, opts) {
    opts = opts || {};
    var err = T.h('div'), tiles = T.h('div'), gEl = T.h('div'), ba = T.h('div', { class: 'panel' }), notes = T.h('div');
    var acctSel = T.h('select', { 'aria-label': 'account filter' }), classSel = T.h('select', { 'aria-label': 'class filter' }), hint = T.h('span', { class: 'hint' });
    var baHead = T.h('h2', null, 'by account', T.h('small', null, opts.readOnly ? 'final NAV of each account and its share of the total' : 'NAV, P&L today and share of the total; click a row to open the account'));
    var baGrid = T.h('div'); ba.appendChild(baHead); ba.appendChild(baGrid);
    [err, tiles, T.h('div', { class: 'row', style: 'margin-bottom:6px; gap:6px; flex-wrap:wrap' }, acctSel, classSel, hint), gEl, ba, notes].forEach(function (x) { el.appendChild(x); });
    notes.appendChild(T.convNote(opts.readOnly ? ['LIFETIME', 'FX_RESULT'] : ['BOOKS', 'ACCT_DAY', 'FX_RESULT', 'STALE']));
    var grid = null, bgrid = null, data = null;
    function rowsAll() { return (data && data.positions) || []; }
    function filtered() { return rowsAll().filter(function (r) { return (!acctSel.value || r.account_id === acctSel.value) && (!classSel.value || (r.klass || r.kind || '') === classSel.value); }); }
    function emptyText() { return (acctSel.value || classSel.value) ? 'no positions match the filter' : (opts.emptyText || 'no positions'); }
    function fill(sel, items, allLabel) {
      var keep = sel.value;
      T.clear(sel); sel.appendChild(T.h('option', { value: '' }, allLabel));
      items.forEach(function (it) { sel.appendChild(T.h('option', { value: it[0] }, it[1])); });
      sel.value = keep; if (sel.value !== keep) { sel.value = ''; }
    }
    function byClass() { var m = {}; classRows(data && data.by_class).forEach(function (c) { m[c.klass || c.class || ''] = c; }); return m; }
    function mountGrid() {
      grid = T.components.grid.mount(gEl, {
        columns: columns({ allBooks: opts.allBooks, basis: data.weight_basis }), rows: [], focus: !opts.readOnly, csv: opts.csv || 'portfolio.csv', empty: 'loading ...',
        rowKey: function (r) { return r.account_id + '|' + r.instrument_id; }, groupBy: function (r) { return r.klass || r.kind || ''; },
        subtotal: function (grp, label) {
          var c = byClass()[label];
          if (!c) { return { account_id: '', symbol: T.esc('subtotal ' + label + ' (no server subtotal)') }; }
          var miss = Number(c.missing || 0);
          return { account_id: '', symbol: T.esc('subtotal ' + label + ' (' + (c.count != null ? c.count : grp.length) + (miss ? ', ' + miss + ' unpriced' : '') + ')'), mv_base: T.esc(T.money(c, 'mv')),
                   unrealised_base: T.esc(T.money(c, 'unrealised')), day_pnl: '<span class="' + T.sign(c, 'day_pnl') + '">' + T.esc(T.money(c, 'day_pnl')) + '</span>', weight: c.weight == null ? '' : T.esc(T.fmt.pct(c.weight)) };
        },
        footer: function () {
          var f = data.footer || data.totals || {}; var nav = data.nav || {}; var td = (data.pnl || {}).TODAY;
          var fut = T.num(f, 'futures_notional_long') || T.num(f, 'futures_notional_short') ? T.money(f, 'futures_notional_long') + ' / ' + T.money(f, 'futures_notional_short') : '';
          return { account_id: T.esc(acctSel.value || classSel.value ? 'totals (all rows)' : 'totals'),
                   symbol: T.esc('gross long ' + T.money(f, 'gross_long') + ' . gross short ' + T.money(f, 'gross_short') + (Number(f.unpriced || 0) ? ' . ' + f.unpriced + ' unpriced' : '')) +
                           (fut ? '<div class="muted" title="futures exposure long / short: contracts x multiplier x mark">futures notional long / short ' + T.esc(fut) + '</div>' : ''),
                   mv_base: T.esc(f.net != null || f.net_c != null ? 'net ' + T.money(f, 'net') : ''),
                   unrealised_base: '<span class="' + T.sign(nav, 'unrealised') + '">' + T.esc(T.money(nav, 'unrealised')) + '</span>',
                   day_pnl: td && !td.error ? '<span class="' + T.sign(td, 'pnl') + '" title="the scope\'s P&L today (incl. fees and cash interest)">' + T.esc(T.money(td, 'pnl')) + '</span>' : '' };
        },
        onEnter: function (r) {
          if (opts.readOnly) { return; }
          /* V104R1-01: a forward dealt on the FX desk has no instrument page (its id is the deal's): like its account's Positions row,
             Enter opens the FX desk of that account (an offsetting forward closes it) */
          if (String(r.kind || '').toUpperCase() === 'FX_FORWARD' && r.extra && r.extra.desk_forward) {
            if (r.book_code && T.book && r.book_code !== T.book.code && T.book.adopt) { T.book.adopt(r.book_code); }
            T.router.go(T.router.account(r.account_id, 'positions'));
            T.ticket.open({ account_id: r.account_id, kind: 'FX' });
            return;
          }
          T.router.go(T.router.instrument(r.account_id, r.instrument_id));
        }
      });
    }
    function openAccount(r) {
      if (opts.readOnly || !r || !r.account_id) { return; }
      if (/:TREASURY$/.test(r.account_id)) {
        if (r.book_code && r.book_code !== T.book.code) { T.book.adopt(r.book_code); }
        T.router.go('#/treasury'); return;
      }
      T.router.go(T.router.account(r.account_id, 'positions'));
    }
    function paintAccounts() {
      var rows = (data && data.by_account) || [];
      if (!bgrid) {
        var cols = [{ key: 'account_id', label: 'account', cls: 'l', text: function (r) { return r.name || r.account_name || T.accountName(r.account_id); }, title: function (r) { return r.account_id; } }];
        if (opts.allBooks) { cols.push({ key: 'book_name', label: 'book', cls: 'l', text: function (r) { return r.book_name || r.book_code || ''; } }); }
        cols.push({ key: 'nav', label: opts.readOnly ? 'final NAV' : 'NAV', text: function (r) { return T.money(r, 'nav'); }, sort: function (r) { return T.num(r, 'nav'); } });
        if (!opts.readOnly) {
          cols.push({ key: 'pnl_today', label: 'P&L today', html: function (r) { return r.pnl_today_error ? '<span class="neg" title="' + T.esc(r.pnl_today_error) + '">n/a</span>' : '<span class="' + T.sign(r, 'pnl_today') + '">' + T.esc(T.money(r, 'pnl_today')) + '</span>'; },
                      text: function (r) { return T.money(r, 'pnl_today'); }, title: function (r) { return r.pnl_today_since ? 'since ' + r.pnl_today_since : ''; }, sort: function (r) { return T.num(r, 'pnl_today'); } });
        }
        cols.push({ key: 'weight', label: '% of total', text: function (r) { return r.weight == null ? '' : T.fmt.pct(r.weight); }, sort: function (r) { return r.weight == null ? '' : Number(r.weight); } });
        cols.push({ key: 'positions', label: 'positions', text: function (r) { return r.positions == null ? '' : String(r.positions); } });
        bgrid = T.components.grid.mount(baGrid, { tools: false, rows: [], columns: cols, empty: 'loading ...', rowKey: function (r) { return r.account_id; },
          rowClass: function () { return opts.readOnly ? '' : 'click'; }, onEnter: openAccount, onClick: openAccount });
      }
      bgrid.update(rows, { empty: 'no accounts' });
    }
    acctSel.onchange = classSel.onchange = function () { if (grid) { grid.update(filtered(), { empty: emptyText() }); } };
    function update(d) {
      data = d || {};
      T.clear(err);
      T.clear(tiles); tiles.appendChild(T.tiles((opts.tiles || liveTiles)(data)));
      var accts = {}, order = [], classes = {};
      rowsAll().forEach(function (r) {
        if (!accts[r.account_id]) { accts[r.account_id] = (r.account_name || T.accountName(r.account_id)) + (opts.allBooks && r.book_name ? ' . ' + r.book_name : ''); order.push(r.account_id); }
        classes[r.klass || r.kind || ''] = 1;
      });
      fill(acctSel, order.map(function (a) { return [a, accts[a]]; }), 'all accounts');
      fill(classSel, Object.keys(classes).map(function (c) { return [c, c || '(none)']; }), 'all classes');
      hint.textContent = (data.marks_partial ? 'marks partial: ' + (data.missing_marks || []).length + ' position(s) carried at cost . ' : '') + (data.weight_basis ? '% = ' + data.weight_basis : '');
      if (!grid) { mountGrid(); }
      grid.update(filtered(), { empty: emptyText() });
      paintAccounts();
    }
    function error(e) { T.clear(err); T.errorBox(err, e, 'portfolio'); if (grid) { grid.empty('could not load the positions'); } }
    return { update: update, error: error };
  }

  function render(el, route) {
    var tab = route.tab || 'positions'; var allBooks = T.store.get('portfolio.allbooks', false);
    var view = null, perf = null, dead = false;
    function scope() { return allBooks ? 'ALL_BOOKS' : 'ALL'; }
    var toggle = T.h('label', { class: 'row', title: 'All books consolidates the open lifetimes of the three books (books never transfer to each other; the view simply sums)' },
      T.h('input', { type: 'checkbox', checked: allBooks, onchange: function (e) { allBooks = e.target.checked; T.store.set('portfolio.allbooks', allBooks); setup(false); } }), ' All books');
    var csv = T.h('a', { class: 'btn small', target: '_blank', rel: 'noopener', title: 'every position of this view as the server writes it (account, instrument, qty, cost, mark, quality, market value, unrealised)' }, 'Positions CSV');
    var head = T.pageHead(el, 'Portfolio', '', [toggle, csv]);
    var subEl = head.querySelector('.sub');
    el.appendChild(T.tabs([{ key: 'positions', label: 'Positions' }, { key: 'performance', label: 'Performance' }], tab, function (k) { T.router.go('#/portfolio/' + k); }));
    var holder = T.h('div'); el.appendChild(holder);
    function setSub() {
      var t = allBooks ? 'all books' : ((T.book.current() || {}).name || '');
      if (subEl) { subEl.textContent = t; } else { subEl = T.h('span', { class: 'sub' }, t); head.insertBefore(subEl, head.children[1] || null); }
    }
    /* the layout of one scope (built again when the All books toggle changes the scope); the first answer comes from the page poller */
    function setup(first) {
      setSub();
      csv.href = T.api.url('/api/export/positions.csv', { scope: scope() });
      T.clear(holder); view = null;
      if (perf && perf.unmount) { perf.unmount(); } perf = null;
      if (tab === 'performance') {
        var m = T.h('div', { class: 'panel' }); holder.appendChild(m);
        perf = T.mountOptional('performance', m, { scope: scope(), book: T.book.code }, 'components/performance.js (WP-20): Sharpe, Sortino, beta / alpha, drawdown, hit rate, monthly table');
        holder.appendChild(T.convNote(['PERF']));
        return;
      }
      view = mountView(holder, { allBooks: allBooks, csv: 'portfolio-' + scope() + '.csv', tiles: liveTiles });
      if (!first) { load().then(null, function () { /* said on the page (error box + page status) */ }); }
    }
    function load() {
      if (dead || tab !== 'positions' || !view) { return Promise.resolve(); }
      var sc = scope(), v = view;
      return T.api.get('/api/portfolio', { scope: sc }).then(function (d) {
        if (dead || v !== view || sc !== scope()) { return; }
        T.rememberKinds(d.positions || []);
        v.update(d);
        T.watchQuotes((d.positions || []).map(function (r) { return r.instrument_id; }));
      }, function (e) { if (v === view && !dead) { v.error(e); } throw e; });
    }
    setup(true);
    T.poll.page('portfolio', 15000, load);
    var off = T.state.on('book', function () { if (perf && perf.refresh) { perf.refresh({ scope: scope(), book: T.book.code }); } });
    T.pages.portfolio.unmount = function () { dead = true; off(); if (perf && perf.unmount) { perf.unmount(); } T.pages.portfolio.unmount = null; };
  }
  T.pages.portfolio = { render: render, unmount: null, columns: columns, mountView: mountView, classRows: classRows, pnlTile: pnlTile, navTile: navTile, COLS: columns({}),
                      tabs: ['positions', 'performance'] };
})();
