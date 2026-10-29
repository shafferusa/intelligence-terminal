/* Portfolio (TERMINAL_DESIGN.md s17.5 #/portfolio[/tab], B16; WP-12; Build 2 P03; FINSIM1_PLAN P3 tasks 4-7, 11, 12).
 * Tabs Positions | Closed | Performance.
 * Positions: the consolidated GET /api/portfolio (scope ALL = the selected book's open lifetime; the "All books" toggle = ALL_BOOKS):
 * - tiles: NAV and the P&L of TODAY / MTD / YTD / ITD, then the returns (PORTFOLIO-07: TODAY, MTD, YTD and ITD time-weighted, ITD
 *   simple - the server's returns block, "unavailable" with its reason when it cannot say);
 * - one row per position: account (its counterparty beneath: a short's stock lender, an OTC contract's dealer, a listed name's
 *   clearing), instrument (an OTC contract's whole label, never cut: V104R2-OTC-05; the sector beneath), signed quantity (an OTC
 *   contract in its own units with their label), cost / mark (an OTC contract: its dealt price, spread or rate and the current one -
 *   PORTFOLIO-06), market value (a future's notional beneath), today / unrealised, realised / fees, income / financing, total P&L
 *   (PORTFOLIO-04: unrealised + realised + income + financing + fees to date), margin / collateral (P6's figures, "n/a" until they are
 *   in the build), % of the scope's NAV / days held;
 * - grouped by asset class (default), class, sector (PORTFOLIO-19: the rows of the gross long / short; futures and OTC contracts in
 *   their own group) or account, each group with the server's subtotal;
 * - the footer: market value (gross long / gross short / net, OTC contracts at market) and exposure (futures notional, TRS reference
 *   units x the underlying price, CDX notional with its credit sign, IRS DV01) - the definitions Risk uses (PORTFOLIO-17);
 * - a click (or Enter) on a row opens its drawer above the grid (T.pages.positions.openDrawer: fills with orders and settlement
 *   states, entries paged with net cash, lots and reliefs, the borrow, marks, dividends; "Instrument page", "Positions of the account"
 *   and "Sector ..." beside it; PORTFOLIO-05); the "by account" table; Copy / CSV and the server's Positions CSV with every column.
 * Closed (PORTFOLIO-03): GET /api/portfolio/closed - one row per closed position (a listed round trip, a terminated / matured OTC
 * contract, a returned stock borrow) with its open / close dates, prices, realised, fees, income, financing, total, days held and how
 * it closed; date filters; the server's CSV.  T.pages.portfolio.mountClosed(el, opts) is reused by the History hub.
 * Performance mounts components/performance.js (WP-20).
 * The page computes nothing: every figure is the server's.  The layout is built once per scope and the 15 s poll only updates it.
 * T.pages.portfolio.mountView(el, opts) is reused read-only by the History pages.
 */
(function () {
  'use strict';
  var T = window.T;
  function money(name, label, title) { return { key: name, label: label || name.replace(/_/g, ' '), title_h: title, html: function (r) { return '<span class="' + T.sign(r, name) + '">' + T.esc(T.money(r, name)) + '</span>'; }, text: function (r) { return T.money(r, name); }, sort: function (r) { return T.num(r, name); } }; }
  function span(r, name) { return '<span class="' + T.sign(r, name) + '">' + T.esc(T.money(r, name)) + '</span>'; }
  function two(a, b, title) { return '<div' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + a + '</div><div class="muted" style="font-size:10.5px">' + b + '</div>'; }
  var BASIS_LABEL = { "share of the book's NAV": '% book NAV', 'share of the NAV of all books': '% all-books NAV', "share of the lifetime's final NAV": '% final NAV', "share of the account's NAV": '% acct NAV' };
  /* a subtotal / group label in the instrument column, wrapped inside the column's width (fit 1440 px) */
  function wrapLabel(text) { return '<div style="white-space:normal;max-width:165px">' + T.esc(text) + '</div>'; }
  function shortBook(name) { return String(name || '').replace(/ Book$/, ''); }
  function isOtc(r) { var k = String(r.kind || '').toUpperCase(); return (k === 'SWAP' || r.klass === 'OTC derivatives') && !(r.extra && r.extra.desk_forward); }
  function otcTerms(r) { var f = T.pages.positions && T.pages.positions.otcTerms; return f ? f(r) : { entry: 'n/a', entryLabel: '', current: 'n/a', currentLabel: '', markLabel: '' }; }
  function columns(o) {
    o = o || {};
    return [
      { key: 'account_id', label: 'account', cls: 'l', html: function (r) {
          var a = (r.account_name || T.accountName(r.account_id)) + (o.allBooks && r.book_name ? ' . ' + shortBook(r.book_name) : '');
          return '<div>' + T.esc(a) + '</div>' + (r.counterparty ? '<div class="muted" style="font-size:10.5px;white-space:normal;max-width:130px" title="counterparty">' + T.esc(r.counterparty) + '</div>' : '');
        }, text: function (r) { return (r.account_name || T.accountName(r.account_id)) + (o.allBooks && r.book_name ? ' . ' + shortBook(r.book_name) : ''); },
        title: function (r) { return r.account_id + (r.book_name ? ' . ' + r.book_name : '') + (r.counterparty ? ' . counterparty ' + r.counterparty : ''); } },
      { key: 'symbol', label: 'instrument', cls: 'l', html: function (r) {
          /* OTC-R1-03 / V104R2-OTC-05: an OTC contract's whole short label ('CDX.NA.IG S47 5Y buy protection'), wrapped, never cut */
          var nm = String((r.extra && r.extra.label) || r.name || '');
          var full = String(r.name || nm);
          var h = '<b title="' + T.esc(full + ' (' + (r.asset_class || r.klass || r.kind || '') + ')') + '">' + T.esc(r.symbol || r.instrument_id) + '</b>' +
                  '<div class="muted" style="white-space:normal;min-width:' + (isOtc(r) ? 140 : 0) + 'px;max-width:165px;line-height:1.25" title="' + T.esc(full) + '">' + T.esc(isOtc(r) ? nm : nm.slice(0, 40)) + '</div>';
          if (o.groupBy !== 'sector' && r.sector_label && !isOtc(r) && r.kind !== 'FUTURE') { h += '<div class="muted" style="font-size:10px" title="sector' + (r.sector_source ? ' (' + T.esc(r.sector_source) + ')' : '') + '">' + T.esc(r.sector_label) + '</div>'; }
          return h;
        }, text: function (r) { return (r.symbol || r.instrument_id) + ' ' + (r.name || ''); } },
      { key: 'qty', label: 'qty', title_h: 'signed quantity: negative = short; an OTC contract in its own units (a TRS its reference shares, a CDX / IRS its notional), labelled',
        html: function (r) {
          /* P8L-02: an OTC contract's quantity is its units with their label (never its notional read as a quantity) */
          var c = T.contractQty(r);
          if (c) {
            var tip = c.isNotional ? 'the contract\'s notional ' + c.ccy + ' ' + c.notional + ' (its face, never its market value); negative = the short side'
                                   : c.label + '; notional ' + c.ccy + ' ' + c.notional + ' (the face, never the market value); negative = the short side';
            return '<span class="' + (c.n > 0 ? 'pos' : (c.n < 0 ? 'neg' : '')) + '" title="' + T.esc(tip) + '">' + T.esc(T.fmt.qty(c.n)) + '</span>' +
                   '<div class="muted" style="font-size:10px;white-space:normal;max-width:90px;margin-left:auto" title="' + T.esc(tip) + '">' + T.esc(c.label) + '</div>';
          }
          return '<span class="' + (r.qty > 0 ? 'pos' : (r.qty < 0 ? 'neg' : '')) + '">' + T.esc(T.fmt.qty(r.qty)) + '</span>';
        },
        text: function (r) { var c = T.contractQty(r); return c ? String(c.n) + ' ' + c.label : String(r.qty); },
        sort: function (r) { var c = T.contractQty(r); return c ? c.n : r.qty; } },
      { key: 'mark', label: 'cost / mark', title_h: 'average cost above, the mark below; an OTC contract: its dealt price, spread or rate above, the current one below (PORTFOLIO-06)',
        html: function (r) {
          if (isOtc(r)) { var t = otcTerms(r); return two('<span title="' + T.esc(t.entryLabel) + '">' + T.esc(t.entry) + '</span>', '<span title="' + T.esc(t.currentLabel) + '">' + T.esc(t.current) + '</span>'); }
          return two(T.esc(r.avg_px == null ? 'n/a' : T.fmt.px(r.avg_px)), T.pxHtml(r.mark));
        },
        text: function (r) { return (r.avg_px == null ? '' : String(r.avg_px)) + ' / ' + (r.mark && r.mark.mark != null ? String(r.mark.mark) : ''); },
        sort: function (r) { return r.mark && r.mark.mark != null ? r.mark.mark : ''; } },
      { key: 'mv_base', label: 'mkt value USD', title_h: 'market value in USD at the snapshot FX (an OTC contract: its contract value); a future carries none (its P&L is open trade equity): its notional exposure is beneath',
        html: function (r) {
          if (r.kind === 'FUTURE' && (r.notional != null || r.notional_c != null)) { return two(span(r, 'mv_base'), '<span style="white-space:normal" title="futures exposure: contracts x multiplier x mark (not a market value)">notional ' + T.esc((r.currency && r.currency !== 'USD' ? r.currency + ' ' : '') + T.money(r, 'notional')) + '</span>'); }
          if (isOtc(r)) { var t = otcTerms(r); return t.markLabel ? two(span(r, 'mv_base'), T.esc(t.markLabel)) : span(r, 'mv_base'); }
          return span(r, 'mv_base');
        }, text: function (r) { return T.money(r, 'mv_base'); }, sort: function (r) { return T.num(r, 'mv_base'); } },
      { key: 'day_pnl', label: 'today / unrealised', title_h: 'P&L today above; unrealised (market value - cost) below',
        html: function (r) { return two(span(r, 'day_pnl'), span(r, 'unrealised_base')); }, text: function (r) { return T.money(r, 'day_pnl') + ' / ' + T.money(r, 'unrealised_base'); },
        sort: function (r) { return T.num(r, 'day_pnl'); } },
      { key: 'realised_itd', label: 'realised / fees', title_h: 'realised to date above; fees to date below (commissions: a cost is negative)',
        html: function (r) { return two(span(r, 'realised_itd'), span(r, 'fees_itd')); }, text: function (r) { return T.money(r, 'realised_itd') + ' / ' + T.money(r, 'fees_itd'); },
        sort: function (r) { return T.num(r, 'realised_itd'); } },
      { key: 'income_itd', label: 'income / financing', title_h: 'income to date (dividends, coupons, interest) above; financing to date (borrow fees, swap financing, loan interest; a cost is negative) below',
        html: function (r) { return two(span(r, 'income_itd'), span(r, 'financing_itd')); }, text: function (r) { return T.money(r, 'income_itd') + ' / ' + T.money(r, 'financing_itd'); },
        sort: function (r) { return T.num(r, 'income_itd'); } },
      { key: 'total_pnl', label: 'total P&L', title_h: 'unrealised + realised + income + financing + fees to date (n/a while the row has no price)',
        html: function (r) { return r.total_pnl == null && r.total_pnl_c == null ? '<span class="muted" title="no price: carried at cost">n/a</span>' : '<b class="' + T.sign(r, 'total_pnl') + '">' + T.esc(T.money(r, 'total_pnl')) + '</b>'; },
        text: function (r) { return T.money(r, 'total_pnl'); }, sort: function (r) { return T.num(r, 'total_pnl'); } },
      { key: 'margin', label: 'margin / coll.', title_h: 'the margin the position requires and the collateral it has posted (P6: Reg T for shorts, futures and OTC initial margin, a short\'s cash at the lender)',
        html: function (r) {
          if (r.margin == null && r.margin_c == null) { return '<span class="muted" title="' + T.esc(r.margin_note || 'not in this build') + '">n/a</span>'; }
          return two(T.esc(T.money(r, 'margin')), T.esc(T.money(r, 'collateral')), r.margin_basis || '');
        }, text: function (r) { return r.margin == null && r.margin_c == null ? 'n/a' : T.money(r, 'margin') + ' / ' + T.money(r, 'collateral'); }, sort: function (r) { return T.num(r, 'margin'); } },
      { key: 'weight', label: (BASIS_LABEL[o.basis] || '% NAV') + ' / days', title_h: (o.basis || 'share of NAV') + ' (market value / NAV) above; days held below',
        html: function (r) { return two(T.esc(r.weight == null ? '' : T.fmt.pct(r.weight, 2)), T.esc(r.days_held == null ? '' : r.days_held + 'd')); },
        text: function (r) { return (r.weight == null ? '' : T.fmt.pct(r.weight, 2)) + ' / ' + (r.days_held == null ? '' : String(r.days_held)); },
        sort: function (r) { return r.weight == null ? '' : Number(r.weight); } }
    ];
  }
  /* by_class / by_asset_class: a list [{klass, count, mv, unrealised, day_pnl, weight}] (BM-04); an older dict {klass: {n, mv_base, unrealised_base}} is read too */
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
  /* PORTFOLIO-07: the return tiles from the server's returns block (fractions; T.fmt.pct shows them); a missing figure says why */
  function pct(v) { return v == null || v === '' ? null : T.fmt.pct(v, 2); }
  function returnTiles(rt) {
    rt = rt || {};
    if (!rt.available) { return [{ k: 'returns', v: 'unavailable', s: rt.reason || 'not in this build', cls: 'muted' }]; }
    var why = rt.reasons || {}; var b = rt.baselines || {};
    function tile(k, key, sub) { var v = pct(rt[key]); return { k: k, v: v == null ? 'n/a' : v, s: v == null ? (why[key] || 'not computable') : sub, cls: v == null ? 'muted' : T.signCls(rt[key]), title: rt.basis || '' }; }
    return [tile('return TODAY', 'today_pct', b.today ? 'vs the snapshot of ' + b.today : ''), tile('return MTD (TWR)', 'mtd_pct', b.mtd ? 'from ' + b.mtd : ''),
            tile('return YTD (TWR)', 'ytd_pct', b.ytd ? 'from ' + b.ytd : ''), tile('return ITD (TWR)', 'itd_twr_pct', 'time-weighted, trading days chained'),
            tile('return ITD (simple)', 'itd_simple_pct', '(NAV - contributed) / contributed' + ((rt.restated || []).length ? ' . restated snapshots used' : ''))];
  }
  var GROUPS = [['asset', 'asset class'], ['class', 'class'], ['sector', 'sector'], ['account', 'account']];
  var NOT_IN_SECTOR = 'futures and OTC (exposure, not in the sector view)';

  /* mountView(el, {tiles(d) -> [tile], allBooks, readOnly, csv, emptyText}) -> {update(data), error(e)} */
  function mountView(el, opts) {
    opts = opts || {};
    if (T.pages.positions && T.pages.positions.ensureCompactStyle) { T.pages.positions.ensureCompactStyle(); }   /* fit 1440 px: wrapped headers, 6 px padding */
    var err = T.h('div'), tiles = T.h('div'), rtiles = T.h('div'), drawer = T.h('div', { hidden: true }), gEl = T.h('div', { class: 'p3-compact' }), ba = T.h('div', { class: 'panel' }), notes = T.h('div');
    var acctSel = T.h('select', { 'aria-label': 'account filter' }), classSel = T.h('select', { 'aria-label': 'asset class filter' }), hint = T.h('span', { class: 'hint' });
    var groupSel = T.h('select', { 'aria-label': 'group by' });
    GROUPS.forEach(function (g) { groupSel.appendChild(T.h('option', { value: g[0] }, 'group by ' + g[1])); });
    groupSel.value = T.store.get('portfolio.group', 'asset'); if (!groupSel.value) { groupSel.value = 'asset'; }
    var baHead = T.h('h2', null, 'by account', T.h('small', null, opts.readOnly ? 'final NAV of each account and its share of the total' : 'NAV, P&L today and share of the total; click a row to open the account'));
    var baGrid = T.h('div'); ba.appendChild(baHead); ba.appendChild(baGrid);
    [err, tiles, rtiles, T.h('div', { class: 'filterbar' }, groupSel, acctSel, classSel, hint), drawer, gEl, ba, notes].forEach(function (x) { el.appendChild(x); });
    notes.appendChild(T.convNote(opts.readOnly ? ['LIFETIME', 'FX_RESULT'] : ['BOOKS', 'ACCT_DAY', 'FX_RESULT', 'STALE']));
    var grid = null, bgrid = null, data = null, open = null;
    function rowsAll() { return (data && data.positions) || []; }
    function assetOf(r) { return r.asset_class || r.klass || r.kind || ''; }
    function filtered() { return rowsAll().filter(function (r) { return (!acctSel.value || r.account_id === acctSel.value) && (!classSel.value || assetOf(r) === classSel.value); }); }
    function emptyText() { return (acctSel.value || classSel.value) ? 'no positions match the filter' : (opts.emptyText || 'no positions'); }
    function fill(sel, items, allLabel) {
      var keep = sel.value;
      T.clear(sel); sel.appendChild(T.h('option', { value: '' }, allLabel));
      items.forEach(function (it) { sel.appendChild(T.h('option', { value: it[0] }, it[1])); });
      sel.value = keep; if (sel.value !== keep) { sel.value = ''; }
    }
    function inGross(r) { return r.kind !== 'FUTURE' && !isOtc(r) && String(r.kind || '').toUpperCase() !== 'FX_FORWARD' && (r.mv_base != null || r.mv_base_c != null); }
    function groupOf(r) {
      var g = groupSel.value;
      if (g === 'class') { return r.klass || r.kind || ''; }
      if (g === 'sector') { return inGross(r) ? (r.sector_label || 'unclassified') : NOT_IN_SECTOR; }
      if (g === 'account') { return (r.account_name || T.accountName(r.account_id)) + (opts.allBooks && r.book_name ? ' . ' + shortBook(r.book_name) : ''); }
      return assetOf(r);
    }
    function subtotalOf(label, grp) {
      var g = groupSel.value;
      if (g === 'sector') {
        var s = ((data && data.by_sector) || []).filter(function (x) { return x.sector === label; })[0];
        if (!s) { return { account_id: '', symbol: '<div style="white-space:normal;max-width:165px">' + T.esc(label + ' (' + grp.length + ')') + '</div>' }; }
        return { account_id: '', symbol: '<div style="white-space:normal;max-width:165px">' + T.esc(label + ' (' + s.count + ')') + '</div><div class="muted" style="font-size:10.5px;white-space:normal;max-width:165px">long ' + T.esc(T.money(s, 'long')) + ' . short ' + T.esc(T.money(s, 'short')) + '</div>',
                 mv_base: '<b>' + T.esc(T.money(s, 'net')) + '</b><div class="muted" style="font-size:10.5px">net market value</div>', weight: T.esc(s.pct_nav == null ? '' : T.fmt.pct(s.pct_nav) + ' gross') };
      }
      if (g === 'account') {
        var a = ((data && data.by_account) || []).filter(function (x) { return (x.name || x.account_name) === label || ((x.name || x.account_name) + (x.book_name ? ' . ' + shortBook(x.book_name) : '')) === label; })[0];
        return { account_id: '', symbol: wrapLabel('subtotal ' + label + ' (' + grp.length + ')'), mv_base: a ? '<span title="the account\'s NAV">NAV ' + T.esc(T.money(a, 'nav')) + '</span>' : '',
                 day_pnl: a ? two('<span class="' + T.sign(a, 'pnl_today') + '">' + T.esc(T.money(a, 'pnl_today')) + '</span>', '') : '', weight: a && a.weight != null ? T.esc(T.fmt.pct(a.weight)) : '' };
      }
      var src = g === 'class' ? classRows(data && data.by_class) : classRows(data && data.by_asset_class);
      var c = src.filter(function (x) { return (x.klass || x.class || '') === label; })[0];
      if (!c) { return { account_id: '', symbol: wrapLabel('subtotal ' + label + ' (no server subtotal)') }; }
      var miss = Number(c.missing || 0);
      return { account_id: '', symbol: wrapLabel('subtotal ' + label + ' (' + (c.count != null ? c.count : grp.length) + (miss ? ', ' + miss + ' unpriced' : '') + ')'), mv_base: T.esc(T.money(c, 'mv')),
               day_pnl: two('<span class="' + T.sign(c, 'day_pnl') + '">' + T.esc(T.money(c, 'day_pnl')) + '</span>', '<span class="' + T.sign(c, 'unrealised') + '">' + T.esc(T.money(c, 'unrealised')) + '</span>'),
               total_pnl: c.total_pnl != null || c.total_pnl_c != null ? '<b class="' + T.sign(c, 'total_pnl') + '">' + T.esc(T.money(c, 'total_pnl')) + '</b>' : '',
               weight: c.weight == null ? '' : T.esc(T.fmt.pct(c.weight)) };
    }
    function footer() {
      var f = data.footer || data.totals || {}; var nav = data.nav || {}; var td = (data.pnl || {}).TODAY; var x = f.otc_exposure || {}; var def = f.definitions || {};
      var fut = T.num(f, 'futures_notional_long') || T.num(f, 'futures_notional_short') ? T.money(f, 'futures_notional_long') + ' / ' + T.money(f, 'futures_notional_short') : '';
      /* POSTCLOSE-07: gross long / short are the securities and options at market (the NAV block's definition); OTC contracts and
         FX forwards are beside them at their market value (the contract value NAV carries, never a notional) */
      var otc = T.num(f, 'otc_mv_positive') || T.num(f, 'otc_mv_negative') ? T.money(f, 'otc_mv_positive') + ' / ' + T.money(f, 'otc_mv_negative') : '';
      /* PORTFOLIO-17: exposure (never a market value), the definitions Risk uses */
      var ex = [];
      if (fut) { ex.push('<span title="' + T.esc(def.futures || 'contracts x multiplier x mark') + '">futures notional long / short ' + T.esc(fut) + '</span>'); }
      if (T.num(x, 'trs_long') || T.num(x, 'trs_short')) { ex.push('<span title="' + T.esc(def.trs_exposure || def.trs || '') + '">TRS long / short ' + T.esc(T.money(x, 'trs_long') + ' / ' + T.money(x, 'trs_short')) + '</span>'); }
      if (T.num(x, 'cdx_long_credit') || T.num(x, 'cdx_short_credit')) { ex.push('<span title="' + T.esc(def.cdx || '') + '">CDX credit long / short ' + T.esc(T.money(x, 'cdx_long_credit') + ' / ' + T.money(x, 'cdx_short_credit')) + '</span>'); }
      if (T.num(x, 'irs_dv01')) { ex.push('<span title="' + T.esc(def.irs || '') + '">IRS DV01 ' + T.esc(T.money(x, 'irs_dv01')) + ' per +1 bp</span>'); }
      return { account_id: T.esc(acctSel.value || classSel.value ? 'totals (all rows)' : 'totals'),
               symbol: '<div style="white-space:normal;min-width:200px;max-width:300px;line-height:1.3">' +
                       '<div title="' + T.esc(def.market_value || '') + '"><b>market value</b> gross long ' + T.esc(T.money(f, 'gross_long')) + ' . gross short ' + T.esc(T.money(f, 'gross_short')) + (Number(f.unpriced || 0) ? ' . ' + f.unpriced + ' unpriced' : '') + '</div>' +
                       (otc ? '<div class="muted" title="OTC contracts and FX forwards at market value, positive / negative (not their notional)">OTC at market + / - ' + T.esc(otc) + '</div>' : '') +
                       (ex.length ? '<div class="muted" title="' + T.esc(def.exposure || '') + '"><b>exposure</b> ' + ex.join(' . ') + '</div>' : '') + '</div>',
               mv_base: T.esc(f.net != null || f.net_c != null ? 'net ' + T.money(f, 'net') : ''),
               day_pnl: two(td && !td.error ? '<span class="' + T.sign(td, 'pnl') + '" title="the scope\'s P&L today (incl. fees and cash interest)">' + T.esc(T.money(td, 'pnl')) + '</span>' : '', '<span class="' + T.sign(nav, 'unrealised') + '">' + T.esc(T.money(nav, 'unrealised')) + '</span>') };
    }
    function openRow(r) {
      if (opts.readOnly || !r) { return; }
      var k = String(r.kind || '').toUpperCase();
      if (k === 'FX_FORWARD' && r.extra && r.extra.desk_forward) {
        /* V104R1-01: a forward dealt on the FX desk has no instrument page (its id is the deal's): its account's FX desk */
        if (r.book_code && T.book && r.book_code !== T.book.code && T.book.adopt) { T.book.adopt(r.book_code); }
        T.router.go(T.router.account(r.account_id, 'positions'));
        T.ticket.open({ account_id: r.account_id, kind: 'FX' });
        return;
      }
      if (!T.pages.positions || !T.pages.positions.openDrawer) { T.router.go(T.router.instrument(r.account_id, r.instrument_id)); return; }
      if (open === r.account_id + '|' + r.instrument_id && !drawer.hidden) { return; }
      open = r.account_id + '|' + r.instrument_id;
      var extra = [T.h('a', { class: 'btn small', href: T.router.instrument(r.account_id, r.instrument_id), title: 'the instrument page in ' + (r.account_name || r.account_id) }, 'Instrument page'),
                   T.h('a', { class: 'btn small', href: T.router.account(r.account_id, 'positions'), title: 'the account\'s Positions page (actions: close, hedge ...)' }, 'Positions of ' + (r.account_name || T.accountName(r.account_id)))];
      T.pages.positions.openDrawer(drawer, { account_id: r.account_id, instrument_id: r.instrument_id, row: r, extra: extra, onClose: function () { open = null; },
        onChange: function () { if (opts.reload) { opts.reload(); } } });
    }
    function mountGrid() {
      grid = T.components.grid.mount(gEl, {
        columns: columns({ allBooks: opts.allBooks, basis: data.weight_basis, groupBy: groupSel.value }), rows: [], focus: !opts.readOnly, csv: opts.csv || 'portfolio.csv', empty: 'loading ...',
        rowKey: function (r) { return r.account_id + '|' + r.instrument_id; }, groupBy: groupOf, rowClass: function () { return opts.readOnly ? '' : 'click'; },
        subtotal: function (grp, label) { return subtotalOf(label, grp); },
        footer: footer,
        onEnter: openRow,
        onClick: function (r, e) { if (e && e.target && e.target.closest('a')) { return; } openRow(r); }
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
    groupSel.onchange = function () {
      T.store.set('portfolio.group', groupSel.value);
      if (grid && grid.destroy) { grid.destroy(); }
      T.clear(gEl); grid = null;
      if (data) { mountGrid(); grid.update(filtered(), { empty: emptyText() }); }
    };
    function update(d) {
      data = d || {};
      T.clear(err);
      T.clear(tiles); tiles.appendChild(T.tiles((opts.tiles || liveTiles)(data)));
      T.clear(rtiles);
      if (!opts.readOnly && data.returns) { rtiles.appendChild(T.tiles(returnTiles(data.returns))); }
      var accts = {}, order = [], classes = {};
      rowsAll().forEach(function (r) {
        if (!accts[r.account_id]) { accts[r.account_id] = (r.account_name || T.accountName(r.account_id)) + (opts.allBooks && r.book_name ? ' . ' + r.book_name : ''); order.push(r.account_id); }
        classes[assetOf(r)] = 1;
      });
      fill(acctSel, order.map(function (a) { return [a, accts[a]]; }), 'all accounts');
      fill(classSel, Object.keys(classes).map(function (c) { return [c, c || '(none)']; }), 'all asset classes');
      hint.textContent = (data.marks_partial ? 'marks partial: ' + (data.missing_marks || []).length + ' position(s) carried at cost . ' : '') + (data.weight_basis ? '% = ' + data.weight_basis : '') +
                         (opts.readOnly ? '' : ' . click a row for its detail');
      if (!grid) { mountGrid(); }
      grid.update(filtered(), { empty: emptyText() });
      paintAccounts();
    }
    function error(e) { T.clear(err); T.errorBox(err, e, 'portfolio'); if (grid) { grid.empty('could not load the positions'); } }
    return { update: update, error: error, closeDrawer: function () { if (!drawer.hidden) { T.clear(drawer); drawer.hidden = true; open = null; } } };
  }

  /* ---- PORTFOLIO-03: the closed positions (also mounted by the History hub) ------------------------------------------------------- */
  function mountClosed(el, opts) {
    opts = opts || {};
    var dead = false;
    var from = T.h('input', { type: 'date', 'aria-label': 'closed from' }), to = T.h('input', { type: 'date', 'aria-label': 'closed to' });
    var csv = T.h('a', { class: 'btn small', target: '_blank', rel: 'noopener', title: 'every closed position of this view as the server writes it' }, 'Server CSV');
    var count = T.h('span', { class: 'count' });
    var bar = T.h('div', { class: 'filterbar' }, T.h('label', null, 'closed from', from), T.h('label', null, 'to', to), T.h('button', { class: 'btn small', onclick: function () { load(); } }, 'Apply'), csv, count);
    var gEl = T.h('div'), note = T.h('div', { class: 'hint' });
    el.appendChild(bar); el.appendChild(gEl); el.appendChild(note);
    var totals = {};
    var grid = T.components.grid.mount(gEl, { rows: [], csv: 'closed-positions.csv', empty: 'loading ...', sortKey: 'close_date', sortAsc: false,
      rowKey: function (r) { return r.account_id + '|' + r.instrument_id + '|' + r.open_ts + '|' + r.close_ts + '|' + r.source; },
      columns: [
        { key: 'account_id', label: 'account', cls: 'l', text: function (r) { return r.account_name || T.accountName(r.account_id); }, title: function (r) { return r.account_id; } },
        { key: 'symbol', label: 'instrument', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b><div class="muted" style="white-space:normal;max-width:220px">' + T.esc((r.asset_class || '') + (r.name && r.name !== r.symbol ? ' . ' + r.name : '')) + '</div>'; },
          text: function (r) { return (r.symbol || r.instrument_id) + ' ' + (r.asset_class || ''); } },
        { key: 'side', label: 'side / qty', html: function (r) { return two(T.esc(String(r.side || '').toLowerCase()), T.esc(T.fmt.qty(r.qty))); }, text: function (r) { return r.side + ' ' + r.qty; } },
        { key: 'open_date', label: 'opened / closed', cls: 'l', html: function (r) { return two(T.esc(r.open_date || ''), T.esc(r.close_date || '') + ' (' + T.esc(r.how || '') + ')'); }, text: function (r) { return r.open_date + ' / ' + r.close_date; }, sort: function (r) { return r.close_date || ''; } },
        { key: 'days_held', label: 'days', text: function (r) { return r.days_held == null ? '' : String(r.days_held); } },
        { key: 'avg_open_px', label: 'avg open / close', html: function (r) { return two(T.esc(r.avg_open_px == null ? 'n/a' : T.fmt.px(r.avg_open_px)), T.esc(r.avg_close_px == null ? 'n/a' : T.fmt.px(r.avg_close_px))); }, text: function (r) { return (r.avg_open_px || '') + ' / ' + (r.avg_close_px || ''); } },
        money('realised', 'realised'), money('fees', 'fees', 'commissions and fees of every fill of the position (a cost)'),
        { key: 'income', label: 'income / financing', html: function (r) { return two(span(r, 'income'), '<span class="' + (T.num(r, 'financing') ? 'neg' : '') + '">' + T.esc(T.money(r, 'financing')) + '</span>'); }, text: function (r) { return T.money(r, 'income') + ' / ' + T.money(r, 'financing'); } },
        { key: 'total_pnl', label: 'total P&L', html: function (r) { return r.source === 'borrow' ? '<span class="muted" title="' + T.esc(r.note || '') + '">' + T.esc(T.money(r, 'total_pnl')) + '</span>' : '<b class="' + T.sign(r, 'total_pnl') + '">' + T.esc(T.money(r, 'total_pnl')) + '</b>'; },
          text: function (r) { return T.money(r, 'total_pnl'); }, sort: function (r) { return T.num(r, 'total_pnl'); } },
        { key: 'counterparty', label: 'counterparty', cls: 'l', html: function (r) { return '<span style="white-space:normal;display:inline-block;max-width:180px">' + T.esc(r.counterparty || '') + '</span>'; }, text: function (r) { return r.counterparty || ''; } }],
      footer: function () { return { account_id: 'totals', realised: T.esc(T.money(totals, 'realised')), fees: T.esc(T.money(totals, 'fees')),
        income: two(T.esc(T.money(totals, 'income')), T.esc(T.money(totals, 'financing'))), total_pnl: '<b class="' + T.sign(totals, 'total_pnl') + '">' + T.esc(T.money(totals, 'total_pnl')) + '</b>' }; },
      onEnter: function (r) { if (!opts.readOnly && r && r.source === 'fills') { T.router.go(T.router.account(r.account_id, 'orders')); } } });
    function params(fmt) { return { scope: opts.scope || 'ALL', from: from.value || null, to: to.value || null, format: fmt || null }; }
    function load() {
      if (dead) { return Promise.resolve(); }
      csv.href = T.api.url('/api/portfolio/closed', params('csv'));
      return T.api.get('/api/portfolio/closed', params()).then(function (d) {
        if (dead) { return; }
        totals = (d && d.totals) || {};
        var rows = (d && d.rows) || [];
        grid.update(rows, { empty: from.value || to.value ? 'no position closed in these dates' : 'no closed position yet: a position closes when its quantity returns to zero, an OTC contract when it is terminated or matures' });
        count.textContent = rows.length + ' closed';
        note.textContent = (d && d.note) || '';
      }, function (e) { if (!dead) { grid.empty('could not load the closed positions: ' + T.api.describe(e)); } });
    }
    [from, to].forEach(function (i) { i.addEventListener('change', function () { load(); }); });
    load();
    return { refresh: load, unmount: function () { dead = true; } };
  }

  function render(el, route) {
    var tab = route.tab || 'positions'; var allBooks = T.store.get('portfolio.allbooks', false);
    var view = null, perf = null, closed = null, dead = false;
    function scope() { return allBooks ? 'ALL_BOOKS' : 'ALL'; }
    var toggle = T.h('label', { class: 'row', title: 'All books consolidates the open lifetimes of the three books (books never transfer to each other; the view simply sums)' },
      T.h('input', { type: 'checkbox', checked: allBooks, onchange: function (e) { allBooks = e.target.checked; T.store.set('portfolio.allbooks', allBooks); setup(false); } }), ' All books');
    var csv = T.h('a', { class: 'btn small', target: '_blank', rel: 'noopener', title: 'every position of this view as the server writes it: account, instrument, qty, cost, mark, quality, market value, unrealised, asset class, sector, counterparty, P&L to date, total P&L, margin, collateral' }, 'Positions CSV');
    var head = T.pageHead(el, 'Portfolio', '', [toggle, csv]);
    var subEl = head.querySelector('.sub');
    el.appendChild(T.tabs([{ key: 'positions', label: 'Positions' }, { key: 'closed', label: 'Closed' }, { key: 'performance', label: 'Performance' }], tab, function (k) { T.router.go('#/portfolio/' + k); }));
    var holder = T.h('div'); el.appendChild(holder);
    function setSub() {
      var t = allBooks ? 'all books' : ((T.book.current() || {}).name || '');
      if (subEl) { subEl.textContent = t; } else { subEl = T.h('span', { class: 'sub' }, t); head.insertBefore(subEl, head.children[1] || null); }
    }
    /* the layout of one scope (built again when the All books toggle changes the scope); the first answer comes from the page poller */
    function setup(first) {
      setSub();
      csv.href = T.api.url('/api/export/positions.csv', { scope: scope(), columns: 'all' });
      T.clear(holder); view = null;
      if (perf && perf.unmount) { perf.unmount(); } perf = null;
      if (closed && closed.unmount) { closed.unmount(); } closed = null;
      if (tab === 'performance') {
        var m = T.h('div', { class: 'panel' }); holder.appendChild(m);
        if (allBooks) { m.appendChild(T.h('div', { class: 'hint' }, 'performance statistics are per book: showing the selected book')); }
        perf = T.mountOptional('performance', m, { scope: 'ALL', book: T.book.code }, 'components/performance.js (WP-20): Sharpe, Sortino, beta / alpha, drawdown, hit rate, monthly table');
        holder.appendChild(T.convNote(['PERF']));
        return;
      }
      if (tab === 'closed') {
        var c = T.h('div', { class: 'panel' }); holder.appendChild(c);
        closed = mountClosed(c, { scope: scope() });
        return;
      }
      view = mountView(holder, { allBooks: allBooks, csv: 'portfolio-' + scope() + '.csv', tiles: liveTiles, reload: function () { load(); } });
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
    var off = T.state.on('book', function () { if (perf && perf.refresh) { perf.refresh({ scope: 'ALL', book: T.book.code }); } if (closed && closed.refresh) { closed.refresh(); } });
    T.pages.portfolio.unmount = function () { dead = true; off(); if (perf && perf.unmount) { perf.unmount(); } if (closed && closed.unmount) { closed.unmount(); } T.pages.portfolio.unmount = null; };
  }
  T.pages.portfolio = { render: render, unmount: null, columns: columns, mountView: mountView, mountClosed: mountClosed, classRows: classRows, pnlTile: pnlTile, navTile: navTile,
                        returnTiles: returnTiles, COLS: columns({}), tabs: ['positions', 'closed', 'performance'] };
})();
