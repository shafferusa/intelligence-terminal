/* Account positions (TERMINAL_DESIGN.md s17.5 #/a/{account}/positions; WP-12; Build 2 P10; FINSIM1_PLAN P3 tasks 5-6).  The positions
 * poller's rows (GET /api/accounts/{id}/positions = PositionView: every figure from accounting.positions(); the page computes nothing but
 * the exercise dialog's labelled display arithmetic).
 *
 * Actions come FIRST in every row, so they stay on screen with the ticket dock open (BM-34 / USX-29), and they depend on
 * what the row is (APIUI-11, PO-09, OA-20):
 * - listed stocks / ETFs / options / futures: Close (the ticket with the CLOSING side of the kind - long stock SELL, short
 *   stock BUY TO COVER, long option STC, short option BTC, long future SELL, short future BUY - the full quantity, MKT DAY;
 *   the ticket re-derives the side from the instrument's kind and never falls back to an opening side), Hedge (the hedge
 *   panel in 'hedge this position' mode: candidates sized to the quantity held, only the hedge legs trade - never a new order
 *   in the instrument; USX-12), Exercise (long options: a dialog per right - a call BUYS 100 shares at K, a put SELLS 100 at
 *   K, a cash-settled index option pays the intrinsic - with the intrinsic value against the option's mark and a warning when
 *   it is out of the money; USX-32), Return borrow;
 * - a bond: Close on the bond ticket, Hedge (a DV01 futures hedge in US Exchange);
 * - a private company: Sell on the negotiated private ticket (no Close / Hedge: no order path);
 * - an OTC swap: its side and notional in its currency (never a quantity in cents) and 'Terminate ...' with a link to the
 *   OTC tab (no Close / Hedge: a swap is never an order);
 * - a private loan: 'Repay ...' (full payoff or a partial prepayment at par).
 * Columns (V104R2-BW-07: they fit 1440 px with the ticket dock open - two figures share a cell): actions, instrument (an OTC
 * contract's whole label on its own line, never cut and never over the next column: V104R2-OTC-04), quantity (an OTC contract in
 * its units with their label), cost / mark (an OTC contract: its dealt price, spread or rate and the current one, PORTFOLIO-06),
 * market value (a future's notional and a swap's exposure beneath), today / unrealised, realised / carry (income, financing and
 * fees to date in the hover and the drawer), % acct NAV / days.  Keys b / s prefill the kind's buy / sell side (b on a short = the
 * closing buy, s on a long = the closing sell; options BTO / BTC / STO / STC).
 * Drill-down (PORTFOLIO-05): a single click (or Enter) on a row opens its drawer ABOVE the grid - T.pages.positions.openDrawer,
 * shared with the Portfolio: the position's figures (total P&L, counterparty, margin / collateral, sector), its fills (all of them,
 * each with its order and its settlement state), its journal entries paged ("load more") with the account's net cash and grouped
 * by type, its open and closed lots with every relief, the borrow (lender worded), the mark history and the dividend notes of the
 * holding period.
 * A Foreign Markets & FX account also shows its currency positions (GET /api/fx/positions: settled, pending FX and trades,
 * forwards, net and the USD equivalent at the mid; OA-24).
 */
(function () {
  'use strict';
  var T = window.T;
  var OTC = { SWAP: 1, IRS: 1, TRS: 1, XCCY: 1, EQ_OPTION: 1, FX_OPTION: 1, EQ_FORWARD: 1, FX_FORWARD: 1 };
  function kindOf(r) { return String(r.kind || '').toUpperCase(); }
  /* a forward dealt on the FX desk (the NAV overlay of an unposted forward: its MTM until the value date) - not a PB contract */
  function isDeskFwd(r) { return kindOf(r) === 'FX_FORWARD' && !!(r.extra && r.extra.desk_forward); }
  function isSwap(r) { return !isDeskFwd(r) && (!!OTC[kindOf(r)] || r.klass === 'OTC derivatives'); }
  function fwdText(r) {
    var ex = r.extra || {}; var b = ex.buy || [], s = ex.sell || [];
    return 'buy ' + (b[0] || '') + ' ' + T.fmt.cents(Number(b[1]) || 0) + ' / sell ' + (s[0] || '') + ' ' + T.fmt.cents(Number(s[1]) || 0) + (ex.value_date ? ' . value ' + ex.value_date : '');
  }
  function isOption(r) { return kindOf(r) === 'OPTION' || r.klass === 'listed options'; }
  /* a swap's side in words from its signed notional quantity (accounting: + receive fixed / receive total return) */
  function swapSide(r) {
    var ex = r.extra || {}; var k = String(ex.kind || '').toUpperCase(); var q = Number(r.qty);
    var pfx = (ex.position_fields && ex.position_fields.extra) || {};
    if (k === 'IRS') { return q < 0 ? 'pay fixed' : 'receive fixed'; }
    if (k === 'TRS') { return q < 0 ? 'pay total return' : 'receive total return'; }
    if (k === 'CDX') { return q < 0 ? 'buy protection' : 'sell protection'; }
    if (k === 'XCCY') { return 'cross-currency'; }
    /* OTC-18: a forward / option reads as what it is (buy EUR / sell USD, short AAPL forward, bought put) - never receive / pay */
    if (k === 'FX_FORWARD' || k === 'EQ_FORWARD' || k === 'EQ_OPTION' || k === 'FX_OPTION') {
      return pfx.side || (k === 'FX_FORWARD' ? (q < 0 ? 'sell the base forward' : 'buy the base forward') : (q < 0 ? 'short' : 'long'));
    }
    return q < 0 ? 'pay' : 'receive';
  }
  /* OTC-18: an FX forward's two legs and its value date (from the C13 fields), shown under its quantity */
  function otcLegs(r) {
    var pfx = ((r.extra || {}).position_fields || {}).extra || {};
    return pfx.legs || '';
  }
  /* a swap's notional in its currency: the row's extra.notional (a decimal string on the wire) or notional_c (cents) */
  function swapNotional(r) {
    var ex = r.extra || {}; var n = T.money(ex, 'notional');
    if (n === 'n/a') { n = T.fmt.qty(Math.abs(Number(r.qty) || 0)); }
    return (ex.qty_unit || r.currency || '') + ' ' + String(n).replace(/^-/, '');
  }
  /* PORTFOLIO-06: an OTC contract's dealt term and its current one, with their labels (the C13 fields: a TRS's reset price and the
     underlying price, a CDX's dealt spread and the spread now, an IRS's fixed rate and the par rate now) */
  function otcTerms(r) {
    var ex = r.extra || {}; var pf = ex.position_fields || {}; var x = pf.extra || {};
    var entry = ex.entry != null ? ex.entry : pf.entry; var cur = ex.current != null ? ex.current : pf.current;
    var k = String(ex.kind || '').toUpperCase();
    function fmtv(v) { if (v == null || v === '') { return 'n/a'; } var s = String(v); if (k === 'IRS' && /^-?0?\.\d+$/.test(s)) { return T.fmt.rate(s); } return /bp$/.test(s) ? s : T.fmt.px(s); }
    return { entry: fmtv(entry), entryLabel: x.entry_label || 'dealt', current: fmtv(cur), currentLabel: x.current_label || 'now', markLabel: pf.mark_label || (r.mark && r.mark.delay_label) || '' };
  }
  function actionsHtml(r) {
    var k = kindOf(r); var b = '';
    function btn(act, label, title) { b += '<button class="btn small act" data-act="' + act + '" title="' + T.esc(title) + '">' + label + '</button>'; }
    function wrap() { return '<div style="white-space:normal;max-width:128px;line-height:1.9">' + b + '</div>'; }
    if (isDeskFwd(r)) { btn('fxdesk', 'FX desk', 'the forward settles at its value date; deal an offsetting forward on the FX desk to close it'); return wrap(); }
    if (isSwap(r)) { btn('terminate', 'Terminate ...', 'terminate the swap with the dealer at its current model mark (the OTC tab)'); btn('otc', 'OTC tab', 'the contracts table and the swap ticket'); return wrap(); }
    if (k === 'PRIVATE_LOAN') { btn('repay', 'Repay ...', 'repay the private loan: full payoff or a partial prepayment at par'); return wrap(); }
    if (k === 'PRIVATE') { btn('psell', 'Sell ...', 'sell through the negotiated private ticket'); return wrap(); }
    btn('close', 'Close', 'close: the ticket with the closing side for the kind and the full quantity');
    if (k !== 'FX') { btn('hedge', 'Hedge', 'hedge this position: candidates sized to the quantity held; only the hedge legs trade'); }
    if (isOption(r) && Number(r.qty) > 0) { btn('exercise', 'Exercise', 'exercise now (long options)'); }
    if (r.borrow && r.borrow.borrow_id) { btn('return', 'Return borrow', 'return the borrow'); }
    return '<div style="white-space:normal;max-width:128px;line-height:1.9">' + b + '</div>';
  }
  function two(a, b, title) { return '<div' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + a + '</div><div class="muted" style="font-size:10.5px">' + b + '</div>'; }
  function moneySpan(r, name) { return '<span class="' + T.sign(r, name) + '">' + T.esc(T.money(r, name)) + '</span>'; }
  var COLS = [
    { key: 'act', label: 'actions', cls: 'l', html: actionsHtml, text: function () { return ''; } },
    { key: 'symbol', label: 'instrument', cls: 'l', html: function (r) {
      /* OTC-R1-03 / V104R2-OTC-04: an OTC contract's short label whole ('CDX.NA.IG S47 5Y buy protection') on its own wrapped line under
         the id - a block with a minimum width, so the column is wide enough and the text never runs over the quantity */
      var lbl = r.extra && r.extra.label;
      if (isSwap(r) && lbl) { return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b><div class="muted pos-nm" title="' + T.esc(r.name || '') + '" style="white-space:normal;min-width:130px;max-width:160px;line-height:1.25">' + T.esc(lbl) + '</div>'; }
      return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b><div class="muted pos-nm" title="' + T.esc(r.name || '') + '" style="white-space:normal;max-width:160px;line-height:1.25">' + T.esc(String(r.name || '').slice(0, 40)) + '</div>'; },
      text: function (r) { return (r.symbol || r.instrument_id) + ' ' + (r.name || ''); } },
    { key: 'qty', label: 'qty', html: function (r) {
      if (isDeskFwd(r)) { return '<span style="white-space:normal;display:inline-block;max-width:200px" title="an FX forward dealt on the FX desk: its market value is the unrealised MTM until the value date">' + T.esc(fwdText(r)) + '</span>'; }
      var c = T.contractQty ? T.contractQty(r) : null;
      var legs = otcLegs(r);
      if (c && legs) {             /* OTC-18: an FX forward - its legs and value date in words under the quantity */
        return two('<span class="' + (c.n > 0 ? 'pos' : 'neg') + '" title="' + T.esc(swapSide(r)) + '">' + T.esc(T.fmt.qty(c.n)) + '</span>',
          '<span style="white-space:normal;display:inline-block;max-width:150px">' + T.esc(c.label + ' . ' + legs) + '</span>');
      }
      if (c) { return two('<span class="' + (c.n > 0 ? 'pos' : 'neg') + '" title="' + T.esc(swapSide(r) + ' ' + swapNotional(r)) + '">' + T.esc(T.fmt.qty(c.n)) + '</span>', '<span style="white-space:normal;display:inline-block;max-width:90px">' + T.esc(c.label) + '</span>'); }
      if (isSwap(r)) { return '<span title="the swap\'s notional in its currency (a swap has no quantity)">' + T.esc(swapSide(r) + ' ' + swapNotional(r)) + '</span>'; }
      var unit = kindOf(r) === 'PRIVATE_LOAN' ? ' ' + T.esc((r.extra && r.extra.qty_unit) || r.currency || '') : '';
      return '<span class="' + (r.qty > 0 ? 'pos' : 'neg') + '">' + T.esc(T.fmt.qty(r.qty)) + unit + '</span>';
    }, text: function (r) { return isSwap(r) ? swapSide(r) + ' ' + swapNotional(r) : String(r.qty); }, sort: function (r) { return r.qty; } },
    { key: 'mark', label: 'cost / mark', title_h: 'average cost (an OTC contract: its dealt price, spread or rate) above; the mark (the current one) below',
      html: function (r) {
        if (isSwap(r)) { var t = otcTerms(r); return two('<span title="' + T.esc(t.entryLabel) + '">' + T.esc(t.entry) + '</span>', '<span title="' + T.esc(t.currentLabel) + '">' + T.esc(t.current) + '</span>'); }
        return two(T.esc(r.avg_px == null ? 'n/a' : T.fmt.px(r.avg_px)), T.pxHtml(r.mark));
      },
      text: function (r) { return (r.avg_px == null ? '' : String(r.avg_px)) + ' / ' + (r.mark && r.mark.mark != null ? String(r.mark.mark) : ''); }, sort: function (r) { return r.mark && r.mark.mark != null ? r.mark.mark : ''; } },
    { key: 'mv', label: 'market value', title_h: 'market value (an OTC contract: its contract value); below: a future\'s notional exposure (contracts x multiplier x mark), a swap\'s notional',
      html: function (r) {
        var k = kindOf(r);
        var sub = k === 'FUTURE' ? 'notional ' + T.money(r, 'notional') : (isSwap(r) ? swapNotional(r) : '');
        return sub ? two(moneySpan(r, 'mv'), T.esc(sub)) : moneySpan(r, 'mv');
      }, text: function (r) { return T.money(r, 'mv'); }, sort: function (r) { return T.num(r, 'mv'); } },
    { key: 'day_pnl', label: 'today / unrealised', title_h: 'P&L today above; unrealised (market value - cost) below',
      html: function (r) { return two(moneySpan(r, 'day_pnl'), moneySpan(r, 'unrealised')); }, text: function (r) { return T.money(r, 'day_pnl') + ' / ' + T.money(r, 'unrealised'); },
      sort: function (r) { return T.num(r, 'day_pnl'); } },
    { key: 'realised_itd', label: 'realised / carry', title_h: 'realised to date above; income + financing + fees to date below (each in the hover and the drawer)',
      html: function (r) {
        var carry = (T.num(r, 'income_itd') || 0) + (T.num(r, 'financing_itd') || 0) + (T.num(r, 'fees_itd') || 0);
        var tip = 'income ' + T.money(r, 'income_itd') + ' . financing ' + T.money(r, 'financing_itd') + ' . fees ' + T.money(r, 'fees_itd');
        return two(moneySpan(r, 'realised_itd'), '<span class="' + T.signCls(carry) + '" title="' + T.esc(tip) + '">' + T.esc(T.fmt.money(carry.toFixed(2))) + '</span>', tip);
      }, text: function (r) { return T.money(r, 'realised_itd') + ' / ' + T.money(r, 'income_itd') + ' / ' + T.money(r, 'financing_itd') + ' / ' + T.money(r, 'fees_itd'); },
      sort: function (r) { return T.num(r, 'realised_itd'); } },
    { key: 'weight', label: '% NAV / days', title_h: 'share of THIS account\'s NAV (the ticket\'s concentration warning uses the book\'s NAV) above; days held below',
      html: function (r) { return two(T.esc(r.weight == null ? '' : T.fmt.pct(r.weight, 2)), T.esc(r.days_held == null ? '' : r.days_held + 'd')); },
      text: function (r) { return (r.weight == null ? '' : T.fmt.pct(r.weight, 2)) + ' / ' + (r.days_held == null ? '' : String(r.days_held)); }, sort: function (r) { return r.weight == null ? '' : r.weight; } }
  ];
  /* the ticket's side vocabulary for a position row (ticket.js SIDES): options BTO / STO / BTC / STC, futures / FX / bonds
     BUY / SELL, cash instruments BUY / SELL / SELL_SHORT / BUY_TO_COVER */
  function sideGroup(r) {
    var k = r.kind || '';
    if (k === 'OPTION' || r.klass === 'listed options') { return 'OPTION'; }
    if (k === 'FUTURE' || r.klass === 'futures') { return 'FUTURE'; }
    if (k === 'FX' || k === 'BOND') { return k; }
    return 'CASH';
  }
  /* the side that closes the row's position (never an opening side) */
  function closeSide(r) {
    var g = sideGroup(r), long = r.qty > 0;
    if (g === 'OPTION') { return long ? 'STC' : 'BTC'; }
    if (g === 'CASH') { return long ? 'SELL' : 'BUY_TO_COVER'; }
    return long ? 'SELL' : 'BUY';
  }
  /* b / s on a row: the closing side when the key reduces the position, else the kind's opening side for that direction */
  function keySide(r, k) {
    var g = sideGroup(r), long = r.qty > 0, short = r.qty < 0;
    if (g === 'OPTION') { return k === 'b' ? (short ? 'BTC' : 'BTO') : (long ? 'STC' : 'STO'); }
    if (g === 'CASH') { return k === 'b' ? (short ? 'BUY_TO_COVER' : 'BUY') : 'SELL'; }
    return k === 'b' ? 'BUY' : 'SELL';
  }
  /* USX-22: the field of the fill's quote record the fill price came from (bid / ask / last / mid) */
  function basisField(f) {
    var b = String(f.price_basis || '').toLowerCase(); var q = f.quote || {};
    var m = /(bid|ask|last|mid)/.exec(b);
    if (m && q[m[1]] != null) { return m[1]; }
    return null;
  }
  function quoteUsedHtml(f) {
    if (!f.quote) { return '<span class="muted">' + T.esc(f.price_basis || '') + '</span>'; }
    var fld = basisField(f);
    return T.pxHtml(f.quote, { field: fld || undefined, basis: f.price_basis }) + (fld ? ' <span class="muted">' + T.esc(fld) + '</span>' : '');
  }
  /* OCC fields of an option id (display only): {root, expiry, right, strike} */
  function occ(id) {
    var m = /^([A-Z.^-]+?)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/.exec(String(id || ''));
    return m ? { root: m[1], expiry: '20' + m[2] + '-' + m[3] + '-' + m[4], right: m[5], strike: Number(m[6]) / 1000 } : null;
  }
  /* 15.7 item 7: a fill booked as of an earlier date carries the AS-OF badge, its entered time and batch; the full label is the title */
  function fillSourceHtml(f) {
    if (!f || !f.badge) { return '<span class="muted">' + T.esc((f && f.source_label) || 'Simulated fill') + '</span>'; }
    var short = f.badge === 'MODEL' ? 'historical model' : 'entered ' + (f.entered_ts ? T.fmt.dt(f.entered_ts) : 'n/a') + (f.batch_id ? ', batch ' + f.batch_id : '');
    return '<div style="white-space:normal;min-width:140px;max-width:190px;line-height:1.25"><span class="' + (f.badge === 'MODEL' ? 'badge-model' : 'badge-asof') + '" title="' +
           T.esc(f.source_label || '') + '">' + T.esc(f.badge) + '</span> <span title="' + T.esc(f.source_label || '') + '">' + T.esc(short) + '</span></div>';
  }
  /* 1.0.6 (fit 1440 px with the dock open): P3's grids - account Positions, the position drawer, the Portfolio - wrap their two-line
     headers ('today / unrealised') and use 6 px cell padding; one style element, added once */
  var COMPACT_ID = 'p3-compact-style';
  function ensureCompactStyle() {
    if (document.getElementById(COMPACT_ID)) { return; }
    var s = document.createElement('style');
    s.id = COMPACT_ID;
    s.textContent = '.p3-compact th{white-space:normal;line-height:1.15;vertical-align:bottom;padding:3px 6px}' +
                    '.p3-compact td{padding-left:6px;padding-right:6px}';
    document.head.appendChild(s);
  }
  /* a fill's settlement state (C15): settled / settling (due) / failed with its reason / resolved */
  function settleHtml(s) {
    if (!s) { return '<span class="muted">no settlement row</span>'; }
    var st = String(s.stage || '').toLowerCase();
    var chip = st.indexOf('fail') >= 0 ? 'failed' : (st.indexOf('settled') >= 0 || st === 'resolved' ? 'filled' : 'settling');
    return '<span class="stage-chip stage-' + chip + '" title="' + T.esc((s.settle_id || '') + (s.fail_reason ? ' . ' + s.fail_reason : '')) + '">' + T.esc(s.label || st || '') + '</span>';
  }

  /* ---- PORTFOLIO-05: the position drawer, shared with the Portfolio (T.pages.positions.openDrawer) -------------------------------------- */
  var SECTORS = ['Communication Services', 'Consumer Discretionary', 'Consumer Staples', 'Energy', 'Financials', 'Health Care', 'Industrials',
                 'Information Technology', 'Materials', 'Real Estate', 'Utilities', 'Commodity', 'Crypto', 'Fixed Income'];
  /* the owner's sector of an instrument (instrument.classify, source "owner"): a dialog with the GICS sectors offered, any text allowed;
     empty = back to the instrument master's (or unclassified) */
  function classify(iid, current, done) {
    var list = T.h('datalist', { id: 'sector-list' }); SECTORS.forEach(function (s) { list.appendChild(T.h('option', { value: s })); });
    var input = T.h('input', { type: 'text', list: 'sector-list', value: current && current !== 'unclassified' ? current : '', placeholder: 'e.g. Financials (empty = unclassified)', 'aria-label': 'sector', style: 'width:260px' });
    var err = T.h('div', { class: 'error', hidden: true });
    T.ui.modal({ title: 'Sector of ' + iid, body: T.h('div', null, T.h('div', { class: 'hint', style: 'margin-bottom:6px' }, 'Your classification overrides the instrument master\'s; it is used by the sector view of the Portfolio and Risk.'), input, list, err),
      actions: [{ label: 'Cancel' }, { label: 'Save', cls: 'primary', onClick: function (m, b) {
        b.disabled = true;
        T.api.post('/api/instruments/' + encodeURIComponent(iid) + '/sector', { sector: input.value.trim() }).then(function (res) {
          m.close(); T.ui.toast(iid + ': sector ' + ((res && res.sector) || 'unclassified'), 'ok', 3000); if (done) { done(res); }
        }, function (e) { b.disabled = false; err.hidden = false; err.textContent = T.api.fail(e, 'sector'); });
      } }] });
  }
  /* openDrawer(holder, {account_id, instrument_id, row, extra: [elements for the head], onClose}) -> {el, close(), reload()} */
  function openDrawer(holder, opts) {
    opts = opts || {};
    var acct = opts.account_id, iid = opts.instrument_id, r0 = opts.row || {};
    var dead = false;
    ensureCompactStyle();
    var box = T.h('div', { class: 'drawer p3-compact', role: 'region', 'aria-label': 'position detail ' + iid });
    T.clear(holder); holder.hidden = false; holder.appendChild(box);
    function close() { dead = true; T.clear(holder); holder.hidden = true; if (opts.onClose) { opts.onClose(); } }
    function head(p) {
      var h = T.h('div', { class: 'drawer-head' }, T.h('h2', null, (r0.symbol || iid) + ' . ' + T.accountName(acct)),
        T.h('span', { class: 'badge' }, (p && p.asset_class) || r0.asset_class || r0.klass || ''),
        T.h('span', { class: 'muted' }, (p && p.sector_label) || r0.sector_label || ''), T.h('span', { class: 'spacer' }));
      (opts.extra || []).forEach(function (x) { h.appendChild(x); });
      if (!isSwap(r0) && kindOf(r0) !== 'FX_FORWARD') {
        h.appendChild(T.h('button', { class: 'btn small', title: 'classify the sector of ' + iid + ' (yours overrides the instrument master)', onclick: function () {
          classify(iid, (p && p.sector_label) || r0.sector_label, function () { load(); if (opts.onChange) { opts.onChange(); } }); } }, 'Sector ...'));
      }
      h.appendChild(T.h('button', { class: 'btn small', title: 'close the detail', onclick: close }, 'x'));
      return h;
    }
    var entries = [], entriesTotal = 0, group = '';
    function figures(p, d) {
      var ex = p.extra || {};
      var rows = [['quantity', isSwap(p) && T.contractQty ? (function () { var c = T.contractQty(p); return c ? T.fmt.qty(c.n) + ' ' + c.label : T.fmt.qty(p.qty); })() : T.fmt.qty(p.qty)],
        isSwap(p) ? null : ['average cost', p.avg_px == null ? 'n/a' : T.fmt.px(p.avg_px)],
        ['mark', T.px(p.mark, { size: 'inline' })], ['cost', T.money(p, 'cost')], ['market value', T.money(p, 'mv')],
        kindOf(p) === 'FUTURE' ? ['exposure (notional)', T.money(p, 'notional'), 'contracts x multiplier x mark (a future has no market value)'] : null,
        ['P&L today', T.money(p, 'day_pnl')], ['unrealised', T.money(p, 'unrealised')], ['realised to date', T.money(p, 'realised_itd')],
        ['income to date', T.money(p, 'income_itd')], ['financing to date', T.money(p, 'financing_itd'), 'a cost is negative'], ['fees to date', T.money(p, 'fees_itd')],
        ['total P&L', p.total_pnl == null && p.total_pnl_c == null ? 'n/a (no price)' : T.money(p, 'total_pnl'), 'unrealised + realised + income + financing + fees to date'],
        ['income pending', T.money(p, 'income_pending')], ['counterparty', p.counterparty || 'n/a'],
        ['margin / collateral', p.margin == null && p.margin_c == null ? (p.margin_note || 'n/a') : T.money(p, 'margin') + ' / ' + T.money(p, 'collateral'), p.margin_basis || p.margin_note || ''],
        ['days held', p.days_held == null ? 'n/a' : String(p.days_held)], ['sector', p.sector_label || 'unclassified', p.sector_source ? 'source: ' + p.sector_source : ''],
        kindOf(p) === 'BOND' && ex.accrued != null ? ['accrued interest', (p.currency || '') + ' ' + T.money(ex, 'accrued')] : null,
        ['settle px (futures)', p.settle_px != null ? T.fmt.px(p.settle_px) : null]];
      box.appendChild(T.kvGrid(rows.filter(function (x) { return x && x[1] != null; })));
      if (isSwap(p)) {
        var t = otcTerms(p); var pf = ex.position_fields || {};
        box.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'contract'), T.kvGrid([
          ['side / notional', swapSide(p) + ' ' + swapNotional(p)], [t.entryLabel, t.entry], [t.currentLabel, t.current],
          ['market value', T.money(p, 'mv') + (t.markLabel ? ' (' + t.markLabel + ')' : ''), 'the contract value NAV carries: the live model mark when fresh ("model, intraday"), else the mark at the last variation margin'],
          ['move since the last VM', T.money(pf, 'move_since_vm')], ['last VM', pf.last_vm_date || 'none yet'], ['VM balance', T.money(ex, 'vm_balance')], ['initial margin', T.money(ex, 'im')],
          ['DV01', ex.dv01_c != null || ex.dv01 != null ? T.money(ex, 'dv01') : null], ['start / maturity', (ex.start_date || '') + ' -> ' + (ex.maturity_date || '')]].filter(function (x) { return x[1] != null; }))));
      }
    }
    function table(el, rows, cols, extraSpec) {
      var g = T.h('div'); el.appendChild(g);
      return T.components.grid.mount(g, Object.assign({ tools: rows.length > 15, rows: rows, columns: cols, maxHeight: '320px', empty: 'none' }, extraSpec || {}));
    }
    var tabsEl = T.h('div'), body = T.h('div');
    var tab = 'fills', data = null;
    function paintTabs() {
      T.clear(tabsEl);
      var d = data || {};
      var list = [['fills', 'fills (' + (d.fills_total != null ? d.fills_total : (d.fills || []).length) + ')'], ['entries', 'cash flows and entries (' + (d.entries_total != null ? d.entries_total : (d.entries || []).length) + ')'],
                  ['lots', 'lots (' + ((d.lots || []).length + (d.closed_lots || []).length) + ')'], ['borrow', 'borrow (' + (d.borrows || []).length + ')'],
                  ['marks', 'mark history'], ['dividends', 'dividends (' + (d.dividend_notes || []).length + ')']];
      var bar = T.h('div', { class: 'drawer-tabs', role: 'tablist' });
      list.forEach(function (t) { bar.appendChild(T.h('button', { class: 'tab' + (t[0] === tab ? ' active' : ''), role: 'tab', onclick: function () { tab = t[0]; paintTabs(); paintBody(); } }, t[1])); });
      tabsEl.appendChild(bar);
    }
    function paintEntries() {
      var d = data || {};
      var bar = T.h('div', { class: 'filterbar' });
      var sel = T.h('select', { 'aria-label': 'entry group' }); sel.appendChild(T.h('option', { value: '' }, 'every type'));
      (d.entry_groups || []).forEach(function (g) { sel.appendChild(T.h('option', { value: g.group }, g.label + ' (' + g.count + ')')); });
      sel.value = group;
      sel.addEventListener('change', function () { group = sel.value; paintBody(); });
      bar.appendChild(T.h('label', null, 'type', sel));
      var shown = entries.filter(function (e) { return !group || e.group === group; });
      bar.appendChild(T.h('span', { class: 'count' }, shown.length + ' shown of ' + entriesTotal + (entries.length < entriesTotal ? ' (' + entries.length + ' loaded)' : '')));
      body.appendChild(bar);
      table(body, shown, [{ key: 'ts', label: 'time', cls: 'l', text: function (e) { return T.fmt.dt(e.ts); }, sort: function (e) { return e.ts; } },
        { key: 'business_date', label: 'bus. date', cls: 'l' },
        { key: 'group', label: 'type', cls: 'l', text: function (e) { return e.kind_label || T.codeWords(e.kind); }, title: function (e) { return e.group || ''; } },
        { key: 'memo', label: 'memo', cls: 'l', html: function (e) { return '<span style="white-space:normal;display:inline-block;max-width:420px" title="' + T.esc(e.memo_stored || '') + '">' + T.esc(e.memo || '') + '</span>'; }, text: function (e) { return e.memo || ''; } },
        { key: 'net', label: 'net cash', html: function (e) { var n = e.net_cash || {}; var k = Object.keys(n); return k.length ? k.map(function (c) { return '<span class="' + T.sign(n, c) + '">' + T.esc(c + ' ' + T.money(n, c)) + '</span>'; }).join(' ') : '<span class="muted">0.00</span>'; },
          text: function (e) { var n = e.net_cash || {}; return Object.keys(n).map(function (c) { return c + ' ' + T.money(n, c); }).join(' ') || '0.00'; } }],
        { tools: true, rowKey: function (e) { return String(e.entry_id); }, onEnter: function (e) { if (T.pages.accounting && T.pages.accounting.entryModal) { T.pages.accounting.entryModal(T.api.get('/api/accounting/journal/' + encodeURIComponent(e.entry_id)), { title: 'entry ' + e.entry_id }); } } });
      if (entries.length < entriesTotal) {
        body.appendChild(T.h('button', { class: 'btn small', onclick: function (ev) { ev.target.disabled = true; loadEntries(entries.length).then(function () { paintTabs(); paintBody(); }); } },
          'Load ' + Math.min(100, entriesTotal - entries.length) + ' more'));
      }
      body.appendChild(T.h('div', { class: 'hint' }, 'net cash = what the entry moved in this account\'s settled cash; Enter on a row opens the entry with its lines'));
    }
    function paintBody() {
      T.clear(body);
      var d = data || {};
      if (tab === 'fills') {
        table(body, d.fills || [], [{ key: 'ts', label: 'time', cls: 'l', text: function (f) { return T.fmt.dt(f.ts); }, sort: function (f) { return f.ts; } }, { key: 'side', label: 'side', cls: 'l' },
          { key: 'qty', label: 'qty', text: function (f) { return T.fmt.qty(f.qty); } }, { key: 'price', label: 'price', text: function (f) { return T.fmt.px(f.price); } },
          { key: 'fee', label: 'fee', text: function (f) { return T.money(f, 'fee'); } },
          { key: 'order_id', label: 'order', cls: 'l', html: function (f) { return f.order_id ? '<a href="' + T.esc(T.router.account(f.account_id || acct, 'orders')) + '" title="the account\'s Orders page lists this order">' + T.esc(f.order_id) + '</a>' : '<span class="muted">' + T.esc(f.origin || '') + '</span>'; }, text: function (f) { return f.order_id || ''; } },
          { key: 'settlement', label: 'settlement', cls: 'l', html: function (f) { return settleHtml(f.settlement); }, text: function (f) { return (f.settlement && f.settlement.label) || ''; } },
          { key: 'quote', label: 'quote used', html: quoteUsedHtml, text: function (f) { return f.price_basis || ''; } },
          { key: 'source', label: 'source', cls: 'l', html: fillSourceHtml, text: function (f) { return f.source_label || 'Simulated fill'; } }], { rowKey: function (f) { return f.fill_id; } });
        if (d.fills_total != null && (d.fills || []).length < d.fills_total) { body.appendChild(T.h('div', { class: 'hint' }, (d.fills || []).length + ' of ' + d.fills_total + ' fills shown')); }
      } else if (tab === 'entries') {
        paintEntries();
      } else if (tab === 'lots') {
        body.appendChild(T.h('h2', null, 'open lots'));
        table(body, d.lots || [], [{ key: 'lot_id', label: 'lot', cls: 'l' }, { key: 'side', label: 'side', cls: 'l' }, { key: 'open_date', label: 'opened', cls: 'l' }, { key: 'open_fill_id', label: 'opening fill', cls: 'l' },
          { key: 'qty_remaining', label: 'qty', text: function (l) { return T.fmt.qty(l.qty_remaining) + ' of ' + T.fmt.qty(l.qty_open); } }, { key: 'unit_cost', label: 'unit cost', text: function (l) { return T.fmt.px(l.unit_cost); } },
          { key: 'cost', label: 'cost', text: function (l) { return T.money(l, 'cost'); } }, { key: 'lock_until', label: 'lock', cls: 'l', text: function (l) { return l.lock_until || ''; } }]);
        body.appendChild(T.h('h2', null, 'closed lots'));
        table(body, d.closed_lots || [], [{ key: 'lot_id', label: 'lot', cls: 'l' }, { key: 'side', label: 'side', cls: 'l' }, { key: 'open_date', label: 'opened', cls: 'l' },
          { key: 'qty_open', label: 'qty', text: function (l) { return T.fmt.qty(l.qty_open); } }, { key: 'unit_cost', label: 'unit cost', text: function (l) { return T.fmt.px(l.unit_cost); } },
          { key: 'cost', label: 'cost', text: function (l) { return T.money(l, 'cost'); } }]);
        body.appendChild(T.h('h2', null, 'reliefs (what closed each lot)'));
        table(body, d.reliefs || [], [{ key: 'ts', label: 'time', cls: 'l', text: function (x) { return T.fmt.dt(x.ts); } }, { key: 'close_fill_id', label: 'closing fill', cls: 'l' }, { key: 'lot_id', label: 'lot', cls: 'l' },
          { key: 'qty', label: 'qty', text: function (x) { return T.fmt.qty(x.qty); } }, { key: 'price', label: 'price', text: function (x) { return T.fmt.px(x.price); } },
          { key: 'cost_relieved', label: 'cost relieved', text: function (x) { return T.money(x, 'cost_relieved'); } }, { key: 'proceeds', label: 'proceeds', text: function (x) { return T.money(x, 'proceeds'); } },
          { key: 'realised', label: 'realised', html: function (x) { return moneySpan(x, 'realised'); }, text: function (x) { return T.money(x, 'realised'); } }]);
      } else if (tab === 'borrow') {
        if (!(d.borrows || []).length) { body.appendChild(T.h('div', { class: 'empty' }, 'no stock borrow for this position')); }
        (d.borrows || []).forEach(function (b) {
          body.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'stock borrow ' + b.borrow_id + ' (' + String(b.status || '').toLowerCase() + ')'), T.kvGrid([['lender', b.lender], ['quantity', T.fmt.qty(Number(b.qty || 0) - Number(b.qty_returned || 0))],
            ['fee', T.fmt.rate(b.fee_rate)], ['rebate', T.fmt.rate(b.rebate_rate)], ['tier', b.tier], ['cash collateral at the lender', T.money(b, 'collateral')], ['opened', b.opened_date || ''], ['last collateral mark', b.last_mark_date || 'n/a']])));
        });
      } else if (tab === 'marks') {
        table(body, d.marks_history || [], [{ key: 'mark_date', label: 'date', cls: 'l' }, { key: 'kind', label: 'kind', cls: 'l' }, { key: 'price', label: 'price', html: function (m) { return T.pxHtml(m); }, text: function (m) { return String(m.price); } }, { key: 'source', label: 'source', cls: 'l' }]);
      } else if (tab === 'dividends') {
        if (!(d.dividend_notes || []).length) { body.appendChild(T.h('div', { class: 'empty' }, 'no dividend of this name in the last 60 / next 120 days')); }
        else { body.appendChild(T.h('ul', { class: 'notes' }, d.dividend_notes.map(function (n) { return T.h('li', null, n); }))); if (T.convNote) { body.appendChild(T.convNote(['DIVIDEND'])); } }
      }
    }
    function url(offset) { return '/api/accounts/' + encodeURIComponent(acct) + '/positions/' + encodeURIComponent(iid); }
    function loadEntries(offset) {
      return T.api.get(url(), { entries_offset: offset, entries_limit: 100 }).then(function (d) { if (dead) { return; } entries = entries.concat(d.entries || []); entriesTotal = d.entries_total || entries.length; },
        function (e) { T.api.fail(e, 'position entries'); });
    }
    function load() {
      T.clear(box); box.appendChild(head(null)); box.appendChild(T.h('div', { class: 'spin' }));
      return T.api.get(url(), { entries_limit: 100 }).then(function (d) {
        if (dead) { return; }
        data = d || {}; entries = (d && d.entries) || []; entriesTotal = (d && d.entries_total) || entries.length;
        var p = d.position || r0;
        T.clear(box); box.appendChild(head(p));
        figures(p, d);
        box.appendChild(tabsEl); box.appendChild(body);
        paintTabs(); paintBody();
      }, function (e) { if (dead) { return; } T.clear(box); box.appendChild(head(null)); T.errorBox(box, e, 'position detail'); });
    }
    load();
    try { holder.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ }
    return { el: box, close: close, reload: load };
  }

  function render(el, route) {
    var acct = route.account;
    var akind = T.accountKind ? T.accountKind(acct) : null;
    T.pageHead(el, 'Positions', T.accountName(acct), [T.h('span', { class: 'sub', id: 'pos-asof' })]);
    /* PORTFOLIO-05: the drawer docked ABOVE the grid (a single click or Enter opens it) */
    ensureCompactStyle();
    var gEl = T.h('div', { class: 'p3-compact' }); var drawer = T.h('div', { hidden: true }); var fxEl = T.h('div', { class: 'panel p3-compact', hidden: true });
    el.appendChild(drawer); el.appendChild(gEl); el.appendChild(fxEl);
    el.appendChild(T.convNote(['COST_BASIS', 'FIFO', 'STALE']));
    var open = null;
    function openPrivate(r, side) {
      if (T.ticketKinds && T.ticketKinds.PRIVATE) { T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, kind: 'PRIVATE', side: side, qty: Math.abs(r.qty) }); return; }
      T.ui.toast(r.instrument_id + ': private shares are sold through the negotiated ticket on the Private Markets page', '', 4500);
      T.router.go(T.router.account(acct, 'markets'));
    }
    function act(kind, r) {
      var k = kindOf(r);
      if (kind === 'close') {
        if (!r.qty) { T.ui.toast('nothing to close: ' + r.instrument_id + ' has no open quantity', 'err'); return; }
        if (k === 'BOND') { T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, kind: 'BOND', side: r.qty > 0 ? 'SELL' : 'BUY', face: Math.abs(r.qty) }); return; }
        /* the closing side for the kind, the full quantity, MKT DAY; position_qty (signed) + kind let the ticket re-derive the side */
        T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, kind: r.kind, side: closeSide(r), qty: Math.abs(r.qty), close: true, position_qty: r.qty,
                        order_type: 'MARKET', tif: 'DAY' });
      }
      /* the hedge panel lives in the order ticket: the pseudo kind POSITION_HEDGE has no ticket of its own, so a bond (whose kind opens
         the bond ticket) reaches the panel too; the instrument's real kind travels as inst_kind */
      else if (kind === 'hedge') { T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, kind: 'POSITION_HEDGE', inst_kind: r.kind, hedge: 'position', position_qty: r.qty }); }
      else if (kind === 'exercise') { exercise(r); }
      else if (kind === 'return') {
        T.ui.confirm('Return borrow ' + r.borrow.borrow_id + ' (' + T.fmt.qty(r.borrow.qty || Math.abs(r.qty)) + ' ' + r.instrument_id + ')? The short must be covered first.', { ok: 'Return' }).then(function (yes) {
          if (!yes) { return; }
          T.api.post('/api/credit/borrows/' + encodeURIComponent(r.borrow.borrow_id) + '/return', { qty: r.borrow.qty || Math.abs(r.qty) }).then(function () { T.ui.toast('borrow returned', 'ok'); T.poll.kick('positions'); T.poll.kick('account_detail'); }, function (e) { T.api.fail(e, 'return borrow'); });
        });
      }
      else if (kind === 'terminate') {
        var sw = T.components.swapticket;
        if (sw && typeof sw.terminate === 'function') { sw.terminate(r.instrument_id, { account_id: acct }); } else { T.router.go(T.router.account(acct, 'markets/otc')); }
      }
      else if (kind === 'otc') { T.router.go(T.router.account(acct, 'markets/otc')); }
      else if (kind === 'fxdesk') { T.ticket.open({ account_id: acct, kind: 'FX' }); }
      else if (kind === 'psell') { openPrivate(r, 'SELL'); }
      else if (kind === 'repay') { repay(r); }
    }
    /* USX-32: the exercise dialog per right, with the intrinsic value (display arithmetic from the last underlying quote) against
       the option's mark, and a warning when it is out of the money */
    function exercise(r) {
      var o = occ(r.instrument_id) || {};
      T.api.get('/api/instruments/' + encodeURIComponent(r.instrument_id)).then(function (d) {
        var i = (d && (d.instrument || d)) || {};
        var und = i.underlying_id || o.root;
        return T.api.get('/api/quotes', { ids: und }).then(function (qr) { return { i: i, und: und, uq: ((qr && qr.quotes) || {})[und] || null }; }, function () { return { i: i, und: und, uq: null }; });
      }).then(function (ctx) {
        var i = ctx.i; var n = Math.abs(Number(r.qty)) || 0;
        var right = i.right || o.right; var K = Number(i.strike != null ? i.strike : o.strike); var mult = Number(i.multiplier || 100);
        var cash = String(i.exercise || '').toUpperCase() === 'CASH';
        var S = ctx.uq ? Number(ctx.uq.last != null ? ctx.uq.last : ctx.uq.mid) : NaN;
        var markPs = r.mark && r.mark.mark != null ? Number(r.mark.mark) : NaN;
        var lines = [];
        var sh = T.fmt.qty(n * mult) + ' ' + ctx.und;
        if (cash) { lines.push('Cash-settled: you receive the intrinsic value, ' + (right === 'C' ? 'max(0, S - K)' : 'max(0, K - S)') + ' x ' + T.fmt.num(mult, 0) + ' x ' + T.fmt.qty(n) + ', at the settlement price.'); }
        else if (right === 'P') { lines.push('A put exercise SELLS ' + sh + ' at ' + T.fmt.px(K) + ' (receive ' + T.fmt.money(String(K * mult * n), 2) + '): it delivers shares you hold, or opens a short sale with a borrow if you hold none.'); }
        else { lines.push('A call exercise BUYS ' + sh + ' at ' + T.fmt.px(K) + ' (pay ' + T.fmt.money(String(K * mult * n), 2) + '); the premium paid rolls into the shares\' cost basis.'); }
        var warn = null, cls = 'warn-box';
        if (isFinite(S) && isFinite(K)) {
          var intr = Math.max(0, right === 'P' ? K - S : S - K);
          lines.push('Intrinsic value now: ' + (right === 'P' ? 'max(0, ' + T.fmt.px(K) + ' - ' + T.fmt.px(S) + ')' : 'max(0, ' + T.fmt.px(S) + ' - ' + T.fmt.px(K) + ')') + ' = ' + T.fmt.num(intr, 2) + ' per share' +
                     (isFinite(markPs) ? ' against the option\'s mark ' + T.fmt.px(markPs) + ' (time value ' + T.fmt.num(Math.max(0, markPs - intr), 2) + ')' : '') +
                     ' - display arithmetic from ' + ctx.und + ' last ' + T.fmt.px(S) + (ctx.uq && ctx.uq.quote_time ? ' (' + T.fmt.time(ctx.uq.quote_time) + ' ET)' : '') + '.');
          if (intr <= 0) {
            warn = 'OUT OF THE MONEY: exercising ' + (right === 'P' ? 'sells below' : 'buys above') + ' the market and gives up the option\'s whole value' + (isFinite(markPs) ? ' (mark ' + T.fmt.money(String(markPs * mult * n), 2) + ')' : '') + '; selling it (STC) keeps that value.';
            cls = 'error-box';
          } else if (isFinite(markPs) && markPs - intr > 0.005) {
            warn = 'Exercising now gives up the time value (' + T.fmt.money(String((markPs - intr) * mult * n), 2) + ' at the mark); selling the option (STC) may keep more.';
          }
        } else { lines.push('The underlying price is not available right now: the intrinsic value cannot be shown.'); }
        var body = T.h('div', null, T.h('div', null, 'Exercise ' + T.fmt.qty(n) + ' x ' + r.instrument_id + ' now? (commission-free)'), lines.map(function (l) { return T.h('div', { class: 'hint', style: 'margin-top:4px' }, l); }),
          warn ? T.h('div', { class: cls, style: 'margin-top:6px' }, warn) : null);
        T.ui.confirm(body, { title: 'Exercise ' + (right === 'P' ? 'put' : (right === 'C' ? 'call' : 'option')), ok: warn && cls === 'error-box' ? 'Exercise anyway' : 'Exercise' }).then(function (yes) {
          if (!yes) { return; }
          T.api.post('/api/options/exercise', { account_id: acct, instrument_id: r.instrument_id, qty: r.qty }).then(function () { T.ui.toast('exercised ' + r.instrument_id, 'ok'); T.poll.kick('positions'); T.poll.kick('account_detail'); }, function (e) { T.api.fail(e, 'exercise'); });
        });
      }, function (e) { T.api.fail(e, 'exercise'); });
    }
    /* a private loan: full payoff (accrued coupon + the face) or a partial prepayment at par (POST /api/credit/private/{id}/repay) */
    function repay(r) {
      var ex = r.extra || {};
      var full = T.h('input', { type: 'radio', name: 'pos-rp', checked: true }); var part = T.h('input', { type: 'radio', name: 'pos-rp' });
      var amount = T.h('input', { type: 'text', inputmode: 'decimal', placeholder: 'USD received (accrued coupon first, the rest retires face at par)', disabled: true, 'aria-label': 'amount received' });
      full.addEventListener('change', function () { amount.disabled = true; }); part.addEventListener('change', function () { amount.disabled = false; amount.focus(); });
      var err = T.h('div', { class: 'error', hidden: true });
      T.ui.modal({ title: 'Repay ' + r.instrument_id + (ex.borrower ? ' (' + ex.borrower + ')' : ''), body: T.h('div', null,
        T.kv([['face', T.money(ex, 'principal')], ['accrued coupon (paid first)', T.money(ex, 'accrued')], ['carrying value', T.money(r, 'mv')]]),
        T.h('div', null, T.h('label', null, full, ' full payoff: accrued coupon + the whole face')), T.h('div', null, T.h('label', null, part, ' partial prepayment at par')),
        T.form([T.field('amount received', amount)]), err, T.h('div', { class: 'hint' }, 'Nights not yet accrued are accrued first.')),
        actions: [{ label: 'Cancel' }, { label: 'Repay', cls: 'primary', onClick: function (m, b) {
          var body;
          if (full.checked) { body = { full: true }; } else {
            var v = String(amount.value || '').replace(/,/g, '').trim();
            if (!/^\d+(\.\d{1,2})?$/.test(v) || Number(v) <= 0) { err.hidden = false; err.textContent = 'amount must be a positive USD amount with at most 2 decimals'; return; }
            body = { amount: v };
          }
          b.disabled = true;
          T.api.post('/api/credit/private/' + encodeURIComponent(r.instrument_id) + '/repay', body).then(function (res) {
            m.close(); T.ui.toast(r.instrument_id + (res && res.partial ? ' prepaid' : ' repaid'), 'ok', 5000); T.poll.kick('positions'); T.poll.kick('account_detail');
          }, function (e) { b.disabled = false; err.hidden = false; err.textContent = T.api.fail(e, 'private credit repay'); });
        } }] });
    }
    function detail(r) {
      if (open && open.iid === r.instrument_id && !drawer.hidden) { return; }
      var h = openDrawer(drawer, { account_id: acct, instrument_id: r.instrument_id, row: r, onClose: function () { open = null; },
        onChange: function () { T.poll.kick('positions'); } });
      open = { iid: r.instrument_id, h: h };
    }
    var spec = {
      columns: COLS, rows: T.state.positions || [], focus: true, csv: 'positions-' + acct + '.csv', empty: T.state.positions ? 'no positions in ' + T.accountName(acct) : 'loading ...', groupBy: function (r) { return r.klass || r.kind || ''; },
      rowClass: function () { return 'click'; },
      subtotal: function (rows, g) { var mv = 0, u = 0, d = 0, any = false; rows.forEach(function (r) { var a = T.num(r, 'mv'), b = T.num(r, 'unrealised'), c = T.num(r, 'day_pnl'); if (a != null) { mv += a; any = true; } if (b != null) { u += b; } if (c != null) { d += c; } }); return any ? { symbol: 'subtotal ' + T.esc(g), mv: T.esc(T.fmt.money(mv.toFixed(2))), day_pnl: two(T.esc(T.fmt.money(d.toFixed(2))), T.esc(T.fmt.money(u.toFixed(2)))) } : { symbol: 'subtotal ' + T.esc(g) }; },
      onEnter: function (r) { if (isDeskFwd(r)) { act('fxdesk', r); return; } detail(r); },
      /* PORTFOLIO-05: a single click on a row opens its drawer; a click on an action button runs that action */
      onClick: function (r, e) { var b = e.target.closest('button[data-act]'); if (b) { act(b.dataset.act, r); return; } if (e.target.closest('a')) { return; } if (!isDeskFwd(r)) { detail(r); } },
      onKey: function (k, r) {
        if (k !== 'b' && k !== 's') { return false; }
        var kk = kindOf(r);
        if (isDeskFwd(r)) { T.ui.toast('an FX forward settles at its value date: deal an offsetting forward on the FX desk', '', 3500); return true; }
        if (isSwap(r) || kk === 'PRIVATE_LOAN') { T.ui.toast(isSwap(r) ? 'a swap is terminated, never traded: Terminate ...' : 'a private loan is repaid: Repay ...', '', 3000); return true; }
        if (kk === 'PRIVATE') { openPrivate(r, k === 'b' ? 'BUY' : 'SELL'); return true; }
        if (kk === 'BOND') { T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, kind: 'BOND', side: k === 'b' ? 'BUY' : 'SELL' }); return true; }
        T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, kind: r.kind, side: keySide(r, k) }); return true;
      }
    };
    var grid = T.components.grid.mount(gEl, spec);
    function paint(rows) {
      /* BM-17: once an answer arrived, an empty list says so (never 'loading ...' forever) */
      if (rows) { spec.empty = 'no positions in ' + T.accountName(acct); }
      grid.update(rows || [], { empty: spec.empty });
      var d = T.state.account_detail; var asof = T.q('#pos-asof');
      if (asof) { asof.textContent = d && d.as_of ? 'as of ' + T.fmt.time(d.as_of) + (d.marks_partial ? ' . NAV excludes unpriced positions' : '') + ' . click a row for its detail' : ''; }
    }
    paint(T.state.positions);
    /* OA-24: the currency positions of a Foreign Markets & FX account */
    var fxGrid = null; var fxSpec = null; var fxTot = null;
    function loadFx() {
      return T.api.get('/api/fx/positions', { account_id: acct }).then(function (d) {
        var rows = (d && (d.rows || d.currencies || d.positions)) || [];
        fxEl.hidden = false;
        if (!fxGrid) {
          T.clear(fxEl); fxEl.appendChild(T.h('h2', null, 'currency positions', T.h('small', null, 'settled + pending FX + pending trades + forwards = net; USD at the mid')));
          var g = T.h('div'); fxEl.appendChild(g);
          fxSpec = { tools: false, rows: [], empty: 'no currency held or pending', columns: [
            { key: 'currency', label: 'ccy', cls: 'l', html: function (r) { return '<b>' + T.esc(r.currency) + '</b> <span class="muted">' + T.esc(r.name || '') + '</span>'; }, text: function (r) { return r.currency; } },
            { key: 'settled', label: 'settled', html: function (r) { return '<span class="' + T.sign(r, 'settled') + '">' + T.esc(T.money(r, 'settled')) + '</span>'; }, text: function (r) { return T.money(r, 'settled'); } },
            { key: 'pending_fx', label: 'pending FX', text: function (r) { return T.money(r, 'pending_fx'); } },
            { key: 'pending_trades', label: 'pending trades', text: function (r) { return T.money(r, 'pending_trades'); } },
            { key: 'forwards_net', label: 'forwards', text: function (r) { return T.money(r, 'forwards_net') + ((r.forwards || []).length ? ' (' + r.forwards.length + ')' : ''); } },
            { key: 'net', label: 'net', html: function (r) { return '<b class="' + T.sign(r, 'net') + '">' + T.esc(T.money(r, 'net')) + '</b>'; }, text: function (r) { return T.money(r, 'net'); } },
            { key: 'usd_equiv', label: 'USD equiv.', html: function (r) { return r.usd_equiv_c == null && r.usd_equiv == null ? '<span class="muted">no rate</span>' : T.esc(T.money(r, 'usd_equiv')) + (r.usd_rate ? ' <span class="muted" title="' + T.esc((r.usd_rate_pair || '') + ' ' + (r.usd_rate_quality || '')) + '">@ ' + T.esc(r.usd_rate) + '</span>' : ''); }, text: function (r) { return T.money(r, 'usd_equiv'); } }] };
          fxGrid = T.components.grid.mount(g, fxSpec);
          fxTot = T.h('div', { class: 'hint' }); fxEl.appendChild(fxTot);
          if (d && d.note) { fxEl.appendChild(T.h('div', { class: 'hint' }, d.note)); }
        }
        fxSpec.empty = 'no currency held or pending';
        fxGrid.update(rows, { empty: fxSpec.empty });
        if (fxTot) { fxTot.textContent = rows.length ? 'total USD equivalent ' + T.money(d, 'usd_equiv_total') + ((d.usd_missing || []).length ? ' (no USD rate for ' + d.usd_missing.join(', ') + ': not included)' : '') + (d.forwards_mtm_c ? ' . forwards mark-to-market ' + T.money(d, 'forwards_mtm') + ' (computed)' : '') : ''; }
      }, function (e) {
        fxEl.hidden = false;
        if (!fxGrid) { T.clear(fxEl); fxEl.appendChild(T.h('h2', null, 'currency positions')); fxEl.appendChild(T.h('div', { class: 'error' }, 'could not load the currency positions: ' + T.api.describe(e))); }
      });
    }
    if (akind === 'FOREIGN_FX') { T.poll.page('fx-positions', 30000, loadFx); }
    var off = T.state.on('positions', paint); var off2 = T.state.on('account_detail', function () { paint(T.state.positions); });
    T.pages.positions.unmount = function () { off(); off2(); if (open && open.h) { open.h.close(); } T.pages.positions.unmount = null; };
  }
  T.pages.positions = { render: render, unmount: null, COLS: COLS, closeSide: closeSide, keySide: keySide, basisField: basisField, openDrawer: openDrawer, classify: classify,
                        otcTerms: otcTerms, settleHtml: settleHtml, ensureCompactStyle: ensureCompactStyle };
})();
