/* Account positions (TERMINAL_DESIGN.md s17.5 #/a/{account}/positions; WP-12; Build 2 P10).  The positions poller's rows
 * (GET /api/accounts/{id}/positions = PositionView: every figure from accounting.positions(); the page computes nothing but
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
 * Columns: '% acct NAV' is the share of THIS account's NAV (the ticket's concentration warning uses the book's); 'exposure'
 * is a future's notional (contracts x multiplier x mark) and a swap's notional (USX-30).  Keys b / s prefill the kind's buy /
 * sell side (b on a short = the closing buy, s on a long = the closing sell; options BTO / BTC / STO / STC).
 * Detail drawer (Enter / click): GET /api/accounts/{id}/positions/{instrument} = fills (the quote used shows the price the
 * fill took: bid, ask or last; USX-22), settlement, cash flows (journal entries), mark history, linked hedge and borrow.
 * A Foreign Markets & FX account also shows its currency positions (GET /api/fx/positions: settled, pending FX and trades,
 * forwards, net and the USD equivalent at the mid; OA-24).
 */
(function () {
  'use strict';
  var T = window.T;
  var OTC = { SWAP: 1, IRS: 1, TRS: 1, XCCY: 1, EQ_OPTION: 1, FX_OPTION: 1, EQ_FORWARD: 1, FX_FORWARD: 1 };
  function money(name, label, title) { return { key: name, label: label || name.replace(/_/g, ' '), title: title ? function () { return title; } : undefined, html: function (r) { return '<span class="' + T.sign(r, name) + '">' + T.esc(T.money(r, name)) + '</span>'; }, text: function (r) { return T.money(r, name); }, sort: function (r) { return T.num(r, name); } }; }
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
    if (k === 'IRS') { return q < 0 ? 'pay fixed' : 'receive fixed'; }
    if (k === 'TRS') { return q < 0 ? 'pay total return' : 'receive total return'; }
    if (k === 'CDX') { return q < 0 ? 'buy protection' : 'sell protection'; }
    if (k === 'XCCY') { return 'cross-currency'; }
    return q < 0 ? 'pay' : 'receive';
  }
  /* a swap's notional in its currency: the row's extra.notional (a decimal string on the wire) or notional_c (cents) */
  function swapNotional(r) {
    var ex = r.extra || {}; var n = T.money(ex, 'notional');
    if (n === 'n/a') { n = T.fmt.qty(Math.abs(Number(r.qty) || 0)); }
    return (ex.qty_unit || r.currency || '') + ' ' + String(n).replace(/^-/, '');
  }
  function actionsHtml(r) {
    var k = kindOf(r); var b = '';
    function btn(act, label, title) { b += '<button class="btn small act" data-act="' + act + '" title="' + T.esc(title) + '">' + label + '</button>'; }
    if (isDeskFwd(r)) { btn('fxdesk', 'FX desk', 'the forward settles at its value date; deal an offsetting forward on the FX desk to close it'); return b; }
    if (isSwap(r)) { btn('terminate', 'Terminate ...', 'terminate the swap with the dealer at its current model mark (the OTC tab)'); btn('otc', 'OTC tab', 'the contracts table and the swap ticket'); return b; }
    if (k === 'PRIVATE_LOAN') { btn('repay', 'Repay ...', 'repay the private loan: full payoff or a partial prepayment at par'); return b; }
    if (k === 'PRIVATE') { btn('psell', 'Sell ...', 'sell through the negotiated private ticket'); return b; }
    btn('close', 'Close', 'close: the ticket with the closing side for the kind and the full quantity');
    if (k !== 'FX') { btn('hedge', 'Hedge', 'hedge this position: candidates sized to the quantity held; only the hedge legs trade'); }
    if (isOption(r) && Number(r.qty) > 0) { btn('exercise', 'Exercise', 'exercise now (long options)'); }
    if (r.borrow && r.borrow.borrow_id) { btn('return', 'Return borrow', 'return the borrow'); }
    return b;
  }
  var COLS = [
    { key: 'act', label: 'actions', cls: 'l', html: actionsHtml, text: function () { return ''; } },
    { key: 'symbol', label: 'instrument', cls: 'l', html: function (r) {
      /* OTC-R1-03: an OTC contract shows its short label whole ('CDX.NA.IG S47 5Y buy protection'), wrapped under the id, never cut */
      var lbl = r.extra && r.extra.label;
      if (isSwap(r) && lbl) { return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b> <span class="muted pos-nm" title="' + T.esc(r.name || '') + '" style="display:inline-block;max-width:200px;white-space:normal;vertical-align:top">' + T.esc(lbl) + '</span>'; }
      return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b> <span class="muted pos-nm" title="' + T.esc(r.name || '') + '">' + T.esc(String(r.name || '').slice(0, 28)) + '</span>'; }, text: function (r) { return (r.symbol || r.instrument_id) + ' ' + (r.name || ''); } },
    { key: 'qty', label: 'qty', html: function (r) {
      if (isDeskFwd(r)) { return '<span title="an FX forward dealt on the FX desk: its market value is the unrealised MTM until the value date">' + T.esc(fwdText(r)) + '</span>'; }
      if (isSwap(r)) { return '<span title="the swap\'s notional in its currency (a swap has no quantity)">' + T.esc(swapSide(r) + ' ' + swapNotional(r)) + '</span>'; }
      var unit = kindOf(r) === 'PRIVATE_LOAN' ? ' ' + T.esc((r.extra && r.extra.qty_unit) || r.currency || '') : '';
      return '<span class="' + (r.qty > 0 ? 'pos' : 'neg') + '">' + T.esc(T.fmt.qty(r.qty)) + unit + '</span>';
    }, text: function (r) { return isSwap(r) ? swapSide(r) + ' ' + swapNotional(r) : String(r.qty); }, sort: function (r) { return r.qty; } },
    { key: 'avg_px', label: 'avg cost', text: function (r) { return r.avg_px == null || isSwap(r) ? '' : T.fmt.px(r.avg_px); } },
    { key: 'mark', label: 'mark', html: function (r) { return T.pxHtml(r.mark); }, text: function (r) { return r.mark && r.mark.mark != null ? String(r.mark.mark) : ''; }, sort: function (r) { return r.mark && r.mark.mark != null ? r.mark.mark : ''; } },
    money('mv', 'market value'),
    { key: 'notional', label: 'exposure', title: function () { return 'futures: contracts x multiplier x mark (the market value of a future is 0: it is margined); swaps: the notional'; },
      html: function (r) { var k = kindOf(r); if (k === 'FUTURE') { return '<span title="notional exposure: contracts x multiplier x mark">' + T.esc(T.money(r, 'notional')) + '</span>'; } if (isSwap(r)) { return T.esc(swapNotional(r)); } return ''; },
      text: function (r) { return kindOf(r) === 'FUTURE' ? T.money(r, 'notional') : (isSwap(r) ? swapNotional(r) : ''); }, sort: function (r) { return kindOf(r) === 'FUTURE' ? T.num(r, 'notional') : ''; } },
    money('day_pnl', 'P&L today'), money('unrealised'), money('realised_itd', 'realised'),
    { key: 'carry', label: 'income / fin. / fees', title: function () { return 'income (dividends, coupons), financing and fees to date, as booked'; },
      html: function (r) { return '<span class="' + T.sign(r, 'income_itd') + '">' + T.esc(T.money(r, 'income_itd')) + '</span> / <span class="' + T.sign(r, 'financing_itd') + '">' + T.esc(T.money(r, 'financing_itd')) + '</span> / <span class="' + T.sign(r, 'fees_itd') + '">' + T.esc(T.money(r, 'fees_itd')) + '</span>'; },
      text: function (r) { return T.money(r, 'income_itd') + ' / ' + T.money(r, 'financing_itd') + ' / ' + T.money(r, 'fees_itd'); } },
    { key: 'weight', label: '% acct NAV', title: function () { return 'share of THIS account\'s NAV (the ticket\'s concentration warning uses the book\'s NAV)'; }, text: function (r) { return r.weight == null ? '' : T.fmt.pct(r.weight, 2); }, sort: function (r) { return r.weight == null ? '' : r.weight; } },
    { key: 'days_held', label: 'days', text: function (r) { return r.days_held == null ? '' : String(r.days_held); } }
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
  function render(el, route) {
    var acct = route.account;
    var akind = T.accountKind ? T.accountKind(acct) : null;
    T.pageHead(el, 'Positions', T.accountName(acct), [T.h('span', { class: 'sub', id: 'pos-asof' })]);
    var gEl = T.h('div'); var drawer = T.h('div', { class: 'panel', hidden: true }); var fxEl = T.h('div', { class: 'panel', hidden: true });
    el.appendChild(gEl); el.appendChild(drawer); el.appendChild(fxEl);
    el.appendChild(T.convNote(['COST_BASIS', 'FIFO', 'STALE']));
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
      drawer.hidden = false; T.clear(drawer);
      drawer.appendChild(T.h('h2', null, r.instrument_id + ' detail', T.h('button', { class: 'btn small', style: 'float:right', onclick: function () { drawer.hidden = true; } }, 'x')));
      drawer.appendChild(T.h('div', { class: 'spin' }));
      T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/positions/' + encodeURIComponent(r.instrument_id)).then(function (d) {
        T.clear(drawer);
        drawer.appendChild(T.h('h2', null, r.instrument_id + ' detail', T.h('button', { class: 'btn small', style: 'float:right', onclick: function () { drawer.hidden = true; } }, 'x')));
        var p = d.position || r;
        drawer.appendChild(T.kv([['mark', T.px(p.mark, { size: 'inline' })], ['cost', T.money(p, 'cost')], ['market value', T.money(p, 'mv')], kindOf(p) === 'FUTURE' ? ['exposure (notional)', T.money(p, 'notional')] : null,
          ['unrealised', T.money(p, 'unrealised')], ['income to date', T.money(p, 'income_itd')], ['income pending', T.money(p, 'income_pending')],
          kindOf(p) === 'BOND' && p.extra && p.extra.accrued != null ? ['accrued interest', (p.currency || '') + ' ' + T.money(p.extra, 'accrued')] : null,
          ['settle px (futures)', p.settle_px != null ? T.fmt.px(p.settle_px) : 'n/a']]));
        function block(title, rows, cols) {
          var b = T.h('div', { class: 'section' }, T.h('h2', null, title));
          if (!rows || !rows.length) { b.appendChild(T.h('div', { class: 'empty' }, 'none')); drawer.appendChild(b); return; }
          var g = T.h('div'); b.appendChild(g); drawer.appendChild(b);
          T.components.grid.mount(g, { tools: false, rows: rows, columns: cols });
        }
        block('fills (with the quote used)', d.fills, [
          { key: 'ts', label: 'time', cls: 'l', text: function (f) { return T.fmt.dt(f.ts); } }, { key: 'side', label: 'side', cls: 'l' }, { key: 'qty', label: 'qty', text: function (f) { return T.fmt.qty(f.qty); } },
          { key: 'price', label: 'price', text: function (f) { return T.fmt.px(f.price); } }, { key: 'fee', label: 'fee', text: function (f) { return T.money(f, 'fee'); } }, { key: 'origin', label: 'origin', cls: 'l' },
          { key: 'quote', label: 'quote used', html: quoteUsedHtml, text: function (f) { return f.price_basis || ''; } }, { key: 'settle_date', label: 'settles', cls: 'l' }]);
        block('lots', d.lots || p.lots, [{ key: 'lot_id', label: 'lot', cls: 'l' }, { key: 'side', label: 'side', cls: 'l' }, { key: 'open_date', label: 'opened', cls: 'l' }, { key: 'qty_remaining', label: 'qty', text: function (l) { return T.fmt.qty(l.qty_remaining); } }, { key: 'unit_cost', label: 'unit cost', text: function (l) { return T.fmt.px(l.unit_cost); } }, { key: 'cost', label: 'cost', text: function (l) { return T.money(l, 'cost'); } }, { key: 'lock_until', label: 'lock', cls: 'l', text: function (l) { return l.lock_until || ''; } }]);
        block('cash flows (journal entries)', d.entries, [{ key: 'ts', label: 'time', cls: 'l', text: function (e) { return T.fmt.dt(e.ts); } }, { key: 'kind', label: 'kind', cls: 'l', text: function (e) { return e.kind_label || T.codeWords(e.kind); } }, { key: 'memo', label: 'memo', cls: 'l' }, { key: 'net', label: 'net cash', text: function (e) { var n = e.net_cash || {}; return Object.keys(n).map(function (c) { return c + ' ' + T.money(n, c); }).join(' '); } }]);
        block('mark history', d.marks_history, [{ key: 'mark_date', label: 'date', cls: 'l' }, { key: 'kind', label: 'kind', cls: 'l' }, { key: 'price', label: 'price', html: function (m) { return T.pxHtml(m); }, text: function (m) { return String(m.price); } }, { key: 'source', label: 'source', cls: 'l' }]);
        if (d.borrow) { drawer.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'linked borrow'), T.kv([['id', d.borrow.borrow_id], ['qty', T.fmt.qty(d.borrow.qty)], ['fee', T.fmt.rate(d.borrow.fee_rate)], ['rebate', T.fmt.rate(d.borrow.rebate_rate)], ['tier', d.borrow.tier], ['collateral', T.money(d.borrow, 'collateral')], ['lender', d.borrow.lender]]))); }
        if (d.hedge) { drawer.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'linked hedge'), T.kv([['package', d.hedge.package_id], ['legs', (d.hedge.legs || []).map(function (l) { return l.instrument_id + ' x' + l.qty; }).join(', ')]]))); }
        /* an OTC swap: the row's mark is the overlay NAV carries (the mark at the last variation margin); the live model mark is shown here */
        var ex = p.extra || {};
        if (isSwap(p) && ex.model_mark) {
          drawer.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'swap marks'), T.kv([['side / notional', swapSide(p) + ' ' + swapNotional(p)], ['NAV overlay (last VM)', T.money(p, 'mv'), 'the mark at the last variation margin; 0 after an STM settlement'],
            ['live model mark', T.px(ex.model_mark, { size: 'inline' }), 'computed now from the curve / underlying; the next VM moves the difference'], ['VM balance', T.money(ex, 'vm_balance')], ['IM', T.money(ex, 'im')]])));
        }
        if (d.dividend_notes && d.dividend_notes.length) {
          drawer.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'dividends'), T.h('ul', { class: 'notes' }, d.dividend_notes.map(function (n) { return T.h('li', null, n); })), T.convNote ? T.convNote(['DIVIDEND']) : null));
        }
      }, function (e) { T.clear(drawer); T.errorBox(drawer, e, 'position detail'); });
    }
    var spec = {
      columns: COLS, rows: T.state.positions || [], focus: true, csv: 'positions-' + acct + '.csv', empty: T.state.positions ? 'no positions in ' + T.accountName(acct) : 'loading ...', groupBy: function (r) { return r.klass || r.kind || ''; },
      subtotal: function (rows, g) { var mv = 0, u = 0, d = 0, any = false; rows.forEach(function (r) { var a = T.num(r, 'mv'), b = T.num(r, 'unrealised'), c = T.num(r, 'day_pnl'); if (a != null) { mv += a; any = true; } if (b != null) { u += b; } if (c != null) { d += c; } }); return any ? { symbol: 'subtotal ' + g, mv: T.esc(T.fmt.money(mv.toFixed(2))), unrealised: T.esc(T.fmt.money(u.toFixed(2))), day_pnl: T.esc(T.fmt.money(d.toFixed(2))) } : { symbol: 'subtotal ' + g }; },
      onEnter: function (r) { if (isDeskFwd(r)) { act('fxdesk', r); return; } detail(r); }, onClick: function (r, e) { var b = e.target.closest('button[data-act]'); if (b) { act(b.dataset.act, r); } },
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
      if (asof) { asof.textContent = d && d.as_of ? 'as of ' + T.fmt.time(d.as_of) + (d.marks_partial ? ' . NAV excludes unpriced positions' : '') : ''; }
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
    T.pages.positions.unmount = function () { off(); off2(); T.pages.positions.unmount = null; };
  }
  T.pages.positions = { render: render, unmount: null, COLS: COLS, closeSide: closeSide, keySide: keySide, basisField: basisField };
})();
