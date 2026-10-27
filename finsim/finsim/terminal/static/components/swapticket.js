/* PB page -> OTC tab: the swap ticket and the contracts table (TERMINAL_DESIGN.md s13.11, s13.12, s15, s17.1; WP-17; Build 2 P09).
 *
 * Mounted on the OTC tab of a prime-broker account (pages/markets.js, or the PB market plug-in of Build 2 P12):
 *
 *     var h = T.components.swapticket.mount(el, {account_id: 'F1:PB_GS', notes: true, blocked: null});
 *     h.refresh();                      // re-reads the contracts now (they also refresh by themselves every 30 s)
 *     h.prefill('TRS', {underlying_id: 'AAPL', units: 1000, receiver: false});   // fill a form (the hedge panel's TRS overlay)
 *     h.terminate('SWP-000003');        // the terminate dialog of one contract
 *     h.setBlocked('NAV is below the ECP floor ...');   // a reason shown above the forms; Preview / Book disabled (null clears it)
 *     h.unmount();
 *
 *   T.components.swapticket.prefill(kind, terms, {account_id})   -> fills the visible ticket of that account (or opens its OTC tab
 *                                                                   and fills it once mounted)
 *   T.components.swapticket.terminate(contract_id, {account_id}) -> the terminate dialog from any page (Positions)
 *   T.ticketKinds.SWAP                                           -> the dock: a swap's summary with Terminate and the OTC tab link
 *
 * Reads    GET  /api/otc/contracts?account_id=..&detail=1  -> [ContractView] (live model mark, signed DV01, IM, VM balance,
 *                                                            accrued legs, kind_label, side_text, next_events, history)
 * Previews POST /api/otc/preview   {account_id, kind: IRS|TRS|XCCY, terms}  -> the deal, the funding line, the conventions
 * Books    POST /api/otc/contracts {account_id, kind, terms}  (request_id per click via T.api) -> {contract, im_transfer, entries}
 * Closes   POST /api/otc/contracts/{id}/terminate {}          -> {termination_value, accrued_pay, accrued_receive, cash, entries}
 * Entries  GET  /api/accounting/journal/{entry_id}            -> the journal entry behind a VM, a payment or a termination
 *
 * Conventions shown on the form
 * - Rates are typed in percent (4.01 = 4.01 %) and sent as decimal fractions ("0.0401") by string arithmetic; notionals are
 *   typed in their currency and sent as integer cents; no floating-point money anywhere in this file.
 * - The terms typed on each tab are kept per kind (state.values[kind]) through every preview, error, refresh and tab switch;
 *   only the preview block and the contracts block re-render.  A refused input stays as typed, marked, for correction.  A
 *   preview whose terms were edited afterwards is marked out of date and cannot be booked until it is previewed again.
 * - The only quote is the model par rate +/- the PB's disclosed half-width (SWAP_MARK): the preview shows it, the cost of
 *   dealing (mark0), the signed DV01, the initial margin and how it is funded (the account's own cash net of what its payables
 *   and working orders already need, then one Treasury sweep, then PB financing), the schedule and the fixing BEFORE anything
 *   is booked; booking needs a confirmation that repeats those numbers.  A cross-currency preview and its confirmation show the
 *   deal FX rate with its source and time.
 * - The contracts table: status and Terminate first, then the contract, kind and side, notional, maturity, the live model mark
 *   (computed), the VM balance (+ received / - posted), IM and DV01; the dealt terms, accrued legs, next events, fixings and
 *   the VM history (each entry linked to its journal entry) are in the drawer.  It sits in its own horizontal scroller, so
 *   nothing is cut off when the page is narrow.  A failed load keeps the last answer (or none) and says it is retrying.
 * - No dependencies beyond core.js (T.h, T.api, T.ui, T.fmt, T.money, T.convNote, T.router); the file is ASCII.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};
  T.ticketKinds = T.ticketKinds || {};

  var MONEY_RE = /^\d+(\.\d{0,2})?$/;
  var PCT_RE = /^\d+(\.\d{1,3})?$/;             /* percent with at most 3 decimals = the 0.001 % dealing precision */
  var TENORS = [6, 12, 24, 36, 60, 84, 120, 240, 360];
  var KINDS = [{ key: 'IRS', label: 'Interest-rate swap' }, { key: 'TRS', label: 'Total return swap' }, { key: 'XCCY', label: 'Cross-currency swap' }];
  var FIXING_LABEL = {
    TERM: 'TERM - SOFR fixed at each reset (latest print on or before the reset date)',
    COMPOUND_ARREARS: 'SOFR compounded daily in arrears (each night at its own print)'
  };
  var DEFAULTS = {
    IRS: { notional: '10,000,000.00', side: 'PAY_FIXED', tenor_months: '24', fixed_rate: '', fixed_basis: '30/360', float_months: '3', fixing: 'TERM' },
    TRS: { underlying_id: 'AAPL', units: '10000', receiver: 'true', reset_months: '1', tenor_months: '12' },
    XCCY: { pay_ccy: 'EUR', recv_ccy: 'USD', pay_notional: '10,000,000.00', recv_notional: '', pay_rate: '', recv_rate: '', pay_basis: '30/360',
            recv_basis: '30/360', months: '24', mtm_reset: 'true' }
  };
  var PREVIEW_LABEL = { IRS: 'Preview (par rate, cost of dealing, DV01, IM)', TRS: 'Preview (reset price, notional, financing, IM)',
                        XCCY: 'Preview (deal FX, leg quotes, exchanges, IM)' };
  var NOTE_KEYS = ['SWAP_MARK', 'SWAP_VM', 'SWAP_RESET', 'ACCRUAL', 'RATE_LAG', 'ELIGIBILITY'];
  var REFRESH_MS = 30000;
  var RETRY_MS = [2000, 4000, 8000, 16000, 30000];
  var KICKS = ['accounts', 'positions', 'account_detail', 'books', 'attention'];
  var COLS = 9;
  var live = [];                                /* the mounted tickets (prefill / terminate reach the visible one) */
  var pending = null;                           /* {account_id, kind, terms}: a prefill waiting for its ticket to mount */
  var memo = {};                                /* account_id -> {kind, values}: what was typed survives a re-render of the page */

  /* ---- pure helpers ---------------------------------------------------------------------------------------------------- */
  function copy(o) { var out = {}; Object.keys(o || {}).forEach(function (k) { out[k] = o[k]; }); return out; }
  /* "10,000,000.5" -> 1000000050 (integer cents) by string arithmetic; null when not a non-negative money string */
  function toCents(s) {
    s = String(s == null ? '' : s).trim().replace(/,/g, '');
    if (!MONEY_RE.test(s)) { return null; }
    var p = s.split('.');
    var frac = ((p[1] || '') + '00').slice(0, 2);
    return parseInt(p[0], 10) * 100 + parseInt(frac, 10);
  }
  /* 1000000050 -> "10,000,000.50" (integer cents -> a typed amount; display only) */
  function centsText(c) {
    var n = parseInt(c, 10);
    if (!isFinite(n)) { return ''; }
    return T.fmt && T.fmt.cents ? T.fmt.cents(n) : String(n / 100);
  }
  /* "4.01" -> "0.0401", "4.010" -> "0.04010", "0.5" -> "0.005", "12" -> "0.12"; null when not a percent string */
  function pctToFraction(s) {
    s = String(s == null ? '' : s).trim().replace(/,/g, '');
    if (!PCT_RE.test(s)) { return null; }
    var p = s.split('.');
    var whole = p[0].replace(/^0+(?=\d)/, '');
    var frac = p[1] || '';
    var digits = whole + frac;                          /* the number scaled by 10^len(frac) */
    var scale = frac.length + 2;                        /* dividing by 100 adds two places */
    while (digits.length <= scale) { digits = '0' + digits; }
    var out = digits.slice(0, digits.length - scale) + '.' + digits.slice(digits.length - scale);
    return out.replace(/^0+(?=\d)/, '');
  }
  /* "0.0401" -> "4.01" (a fraction back to the percent a trader types; string arithmetic) */
  function fractionToPct(f) {
    if (f == null || f === '') { return ''; }
    var s = String(f).trim();
    if (!/^\d*(\.\d+)?$/.test(s)) { return ''; }
    var p = s.split('.');
    var frac = (p[1] || '') + '00';
    var whole = ((p[0] || '0') + frac.slice(0, 2)).replace(/^0+(?=\d)/, '');
    var rest = frac.slice(2).replace(/0+$/, '');
    return whole + (rest ? '.' + rest : '');
  }
  /* "0.0401" -> "4.0100 %" (string arithmetic: shift two places, pad to 4 decimals) */
  function fmtPct(fraction) {
    if (fraction == null || fraction === '') { return 'n/a'; }
    var s = String(fraction).trim();
    var neg = s.charAt(0) === '-';
    if (neg) { s = s.slice(1); }
    var p = s.split('.');
    var whole = p[0] || '0', frac = (p[1] || '');
    while (frac.length < 6) { frac += '0'; }
    var shifted = whole + frac.slice(0, 2);
    shifted = shifted.replace(/^0+(?=\d)/, '');
    return (neg ? '-' : '') + shifted + '.' + frac.slice(2, 6) + ' %';
  }
  function money(row, key) { return T.money ? T.money(row, key) : String(row ? row[key] : ''); }
  function num(row, key) { return T.num ? T.num(row, key) : null; }
  /* a decimal-string amount as the API sends it (or null) -> display text */
  function amt(v) {
    if (v == null || v === '') { return 'n/a'; }
    return T.fmt && T.fmt.money ? T.fmt.money(v) : String(v);
  }
  /* a price: its own decimals, at least 2 (339.5 -> "339.50") - PO-23 */
  function px(v) {
    if (v == null || v === '') { return 'n/a'; }
    return T.fmt && T.fmt.px ? T.fmt.px(v) : String(v);
  }
  function opt(value, label) { return T.h('option', { value: value }, label || value); }
  /* an API error as one plain line: the message (the code is in the hover text) */
  function describe(e) {
    if (!e) { return 'failed'; }
    if (e.name === 'LostResponse') { return 'no answer from the server - check the contracts table before trying again'; }
    var m = e.message || e.code || String(e);
    var inc = e.incident_id || (e.details && e.details.incident_id);
    return m + (inc ? ' (incident ' + inc + ')' : '');
  }
  function connected(node) {
    if (!node) { return false; }
    if (typeof node.isConnected === 'boolean') { return node.isConnected; }
    return document.body ? document.body.contains(node) : false;
  }
  function kick() { if (T.poll && T.poll.kick) { KICKS.forEach(function (k) { T.poll.kick(k); }); } }
  function nyTime(epoch) { return epoch == null ? '' : (T.fmt && T.fmt.time ? T.fmt.time(epoch) : String(epoch)); }

  /* next_events -> one line: "PAY 2027-04-20 (pay fixed 200,500.00, receive float 195,650.00) . MATURITY 2028-10-20" */
  function eventsText(list) {
    if (!list || !list.length) { return ''; }
    return list.map(function (e) {
      var s = (e.event || '') + ' ' + (e.date || '');
      if (e.legs && e.legs.length) {
        s += ' (' + e.legs.map(function (l) {
          var v = l.amount == null && l.amount_c == null ? (l.known === false ? 'not known yet' : 'n/a') : money(l, 'amount');
          return (l.side || '') + ' ' + (l.currency ? l.currency + ' ' : '') + (l.leg || '') + ' ' + v;
        }).join(', ') + ')';
      }
      return s;
    }).join(' . ');
  }
  /* the floating leg's state: TERM fixing or the compounding so far */
  function fixingText(f) {
    if (!f) { return 'none'; }
    if (f.fixing === 'COMPOUND_ARREARS') {
      return 'compounding ' + (f.period_start || '') + '..' + (f.period_end || '') + ': ' + (f.nights || 0) + ' nights, rate so far ' +
        (f.rate_so_far == null ? 'n/a' : fmtPct(f.rate_so_far)) + ', accrued ' + money(f, 'accrued') + ' (factor ' + (f.factor || '1') + ')';
    }
    return 'SOFR ' + (f.sofr || '') + ' % (' + (f.obs_date || '') + ') -> ' + (f.rate_pct || fmtPct(f.rate)) + ' from ' + (f.reset_date || f.period_start || '');
  }
  /* a contract's or a preview's side as a sentence (PO-23); the server's side_text wins, any other kind is humanised (C21) */
  function sideText(side, row) {
    if (row && row.side_text) { return row.side_text; }
    var s = String(side || '');
    var uid = row && ((row.detail && row.detail.underlying_id) || row.underlying_id);
    if (s === 'PAY_FIXED') { return 'pay fixed / receive SOFR'; }
    if (s === 'RECEIVE_FIXED') { return 'receive fixed / pay SOFR'; }
    if (s === 'RECEIVE_TOTAL_RETURN') { return 'receive the ' + (uid ? uid + ' ' : '') + 'total return / pay financing'; }
    if (s === 'PAY_TOTAL_RETURN') { return 'pay the ' + (uid ? uid + ' ' : '') + 'total return / receive financing'; }
    var m = /^RECEIVE_([A-Z]{3})_PAY_([A-Z]{3})$/.exec(s);
    if (m) { return 'receive ' + m[1] + ' (lent) / pay ' + m[2] + ' (borrowed)'; }
    return s.replace(/_/g, ' ').toLowerCase();
  }
  /* the short kind label of a table row: IRS 5y, TRS AAPL, XCCY EUR/USD, else the server's kind_label */
  function kindShort(c) {
    var d = c.detail || {}, t = c.terms || {};
    if (c.kind === 'IRS') { var m = parseInt(c.tenor_months || t.tenor_months, 10); return 'IRS ' + (isFinite(m) ? (m % 12 === 0 ? (m / 12) + 'y' : m + 'm') : ''); }
    if (c.kind === 'TRS') { return 'TRS ' + (d.underlying_id || t.underlying_id || ''); }
    if (c.kind === 'XCCY') { return 'XCCY ' + (d.recv_ccy || t.recv_ccy || '') + '/' + (d.pay_ccy || t.pay_ccy || ''); }
    return c.kind_label || c.kind || '';
  }
  /* the dealt terms of a contract (drawer) */
  function dealtText(c) {
    var d = c.detail || {};
    if (c.kind === 'TRS') {
      return 'reset price ' + px(d.reset_px || c.reset_px) + ' x ' + (d.units == null ? '' : T.fmt.qty(d.units)) + ' ' + (d.underlying_id || '') + ' . notional ' + money(d, 'notional') + ' USD';
    }
    if (c.kind === 'XCCY') {
      var l = d.legs || {}, r = l.receive || {}, p = l.pay || {};
      return 'receive ' + (r.currency || '') + ' ' + fmtPct(r.rate) + ' / pay ' + (p.currency || '') + ' ' + fmtPct(p.rate) + ' . deal FX ' + px(d.deal_rate);
    }
    if (c.kind === 'IRS') { return (c.fixed_rate_pct || fmtPct(c.fixed_rate)) + ' fixed' + (c.par_rate_dealt ? ' (model par ' + fmtPct(c.par_rate_dealt) + ' at dealing)' : ''); }
    return (d.terms_text || d.summary || '') || 'see the details below';
  }
  /* the accrued legs per currency (an XCCY's foreign legs accrue in their own currency) */
  function accruedText(c) {
    var by = c.balances_by_ccy || {};
    var ccys = Object.keys(by);
    if (!ccys.length) { return 'receive ' + money(c, 'accrued_receive') + ' / pay ' + money(c, 'accrued_pay'); }
    function bal(b, key) { return b[key] == null && b[key + '_c'] == null ? '0.00' : money(b, key); }
    return ccys.map(function (k) { var b = by[k] || {}; return k + ' receive ' + bal(b, 'accrued_receive') + ' / pay ' + bal(b, 'accrued_pay'); }).join(' ; ');
  }
  /* the funding line of a preview (PO-07): need, the account's own cash, what is already committed, the sweep, PB financing */
  function fundingText(f) {
    if (!f) { return null; }
    var parts = [(f.currency ? f.currency + ' ' : '') + 'need ' + money(f, 'need')];
    if (f.own != null || f.own_c != null) { parts.push('own cash ' + money(f, 'own')); }
    else if (f.available != null || f.available_c != null) { parts.push('available ' + money(f, 'available')); }
    else { parts.push('settled cash ' + money(f, 'own_settled')); }
    if ((num(f, 'committed') || 0) > 0) { parts.push(money(f, 'committed') + ' of settled cash already needed by payables / orders'); }
    parts.push('sweep from Treasury ' + money(f, 'sweep'));
    if ((num(f, 'pb_draw') || 0) > 0) { parts.push('PB financing ' + money(f, 'pb_draw')); }
    if (f.ok === false) {
      var tk = (f.treasury_available != null || f.treasury_available_c != null) ? 'treasury_available' : 'treasury_settled';
      parts.push('NOT FUNDABLE: Treasury available ' + money(f, tk));
    }
    return parts.join(' . ');
  }
  /* API terms -> the strings a form holds (prefill) */
  function formValues(kind, t) {
    var v = {};
    t = t || {};
    function has(k) { return t[k] != null && t[k] !== ''; }
    function bool(x) { return x === true || x === 'true' || x === 1 || x === '1'; }
    if (kind === 'IRS') {
      if (has('notional_c')) { v.notional = centsText(t.notional_c); } else if (has('notional')) { v.notional = String(t.notional); }
      if (has('pay_fixed')) { v.side = bool(t.pay_fixed) ? 'PAY_FIXED' : 'RECEIVE_FIXED'; } else if (has('side')) { v.side = String(t.side).toUpperCase().indexOf('RECEIVE') === 0 ? 'RECEIVE_FIXED' : 'PAY_FIXED'; }
      if (has('tenor_months')) { v.tenor_months = String(t.tenor_months); }
      if (has('fixed_rate')) { v.fixed_rate = fractionToPct(t.fixed_rate); }
      ['fixed_basis', 'float_months', 'fixing'].forEach(function (k) { if (has(k)) { v[k] = String(t[k]); } });
    } else if (kind === 'TRS') {
      if (has('underlying_id') || has('underlying')) { v.underlying_id = String(t.underlying_id || t.underlying).toUpperCase(); }
      if (has('units')) { v.units = String(t.units); }
      if (has('receiver')) { v.receiver = bool(t.receiver) ? 'true' : 'false'; } else if (has('side')) { v.receiver = String(t.side).toUpperCase().indexOf('PAY') === 0 ? 'false' : 'true'; }
      ['reset_months', 'tenor_months'].forEach(function (k) { if (has(k)) { v[k] = String(t[k]); } });
    } else if (kind === 'XCCY') {
      ['pay_ccy', 'recv_ccy', 'pay_basis', 'recv_basis'].forEach(function (k) { if (has(k)) { v[k] = String(t[k]).toUpperCase(); } });
      ['pay', 'recv'].forEach(function (s) {
        if (has(s + '_notional_c')) { v[s + '_notional'] = centsText(t[s + '_notional_c']); } else if (has(s + '_notional')) { v[s + '_notional'] = String(t[s + '_notional']); }
        if (has(s + '_rate')) { v[s + '_rate'] = fractionToPct(t[s + '_rate']); }
      });
      if (has('months') || has('tenor_months')) { v.months = String(t.months || t.tenor_months); }
      if (has('mtm_reset')) { v.mtm_reset = bool(t.mtm_reset) ? 'true' : 'false'; }
    }
    return v;
  }
  /* the terms as the API wants them, or {error, field} */
  function readTerms(kind, v) {
    if (kind === 'IRS') {
      var c = toCents(v.notional);
      if (c == null || c <= 0) { return { error: 'notional: a positive USD amount with at most 2 decimals', field: 'notional' }; }
      var t = { notional_c: c, currency: 'USD', pay_fixed: v.side === 'PAY_FIXED', tenor_months: parseInt(v.tenor_months, 10), fixed_rate: null,
        fixed_basis: v.fixed_basis, fixed_months: 6, float_index: 'SOFR', float_basis: 'ACT/360', float_months: parseInt(v.float_months, 10), fixing: v.fixing };
      var fr = String(v.fixed_rate || '').trim();
      if (fr) { var f = pctToFraction(fr); if (f == null) { return { error: 'fixed rate: a percent with at most 3 decimals (4.01 = 4.01 %), or blank for the dealer quote', field: 'fixed_rate' }; } t.fixed_rate = f; }
      return { terms: t };
    }
    if (kind === 'TRS') {
      var uid = String(v.underlying_id || '').trim().toUpperCase();
      if (!uid) { return { error: 'underlying: a US-listed stock or ETF symbol', field: 'underlying_id' }; }
      var us = String(v.units || '').replace(/,/g, '').trim();
      if (!/^\d+$/.test(us) || !(parseInt(us, 10) > 0)) { return { error: 'units: a positive whole number of shares', field: 'units' }; }
      return { terms: { underlying_id: uid, units: parseInt(us, 10), receiver: v.receiver === 'true', reset_months: parseInt(v.reset_months, 10),
        tenor_months: parseInt(v.tenor_months, 10) } };
    }
    var payCcy = String(v.pay_ccy || '').trim().toUpperCase(), recvCcy = String(v.recv_ccy || '').trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(payCcy)) { return { error: 'pay currency: a three-letter code (EUR, GBP, JPY ...)', field: 'pay_ccy' }; }
    if (!/^[A-Z]{3}$/.test(recvCcy)) { return { error: 'receive currency: a three-letter code', field: 'recv_ccy' }; }
    var pnText = String(v.pay_notional || '').trim(), rnText = String(v.recv_notional || '').trim();
    var pn = pnText ? toCents(pnText) : null, rn = rnText ? toCents(rnText) : null;
    if (pnText && (pn == null || pn <= 0)) { return { error: 'pay notional: a positive amount with at most 2 decimals', field: 'pay_notional' }; }
    if (rnText && (rn == null || rn <= 0)) { return { error: 'receive notional: a positive amount with at most 2 decimals', field: 'recv_notional' }; }
    if (pn == null && rn == null) { return { error: 'give the pay or the receive notional (the other is struck at the deal FX rate)', field: 'pay_notional' }; }
    var prText = String(v.pay_rate || '').trim(), rrText = String(v.recv_rate || '').trim();
    var pr = prText ? pctToFraction(prText) : null, rr = rrText ? pctToFraction(rrText) : null;
    if (prText && pr == null) { return { error: 'pay rate: a percent with at most 3 decimals, or blank for the dealer quote', field: 'pay_rate' }; }
    if (rrText && rr == null) { return { error: 'receive rate: a percent with at most 3 decimals, or blank for the dealer quote', field: 'recv_rate' }; }
    return { terms: { pay_ccy: payCcy, recv_ccy: recvCcy, pay_notional_c: pn, recv_notional_c: rn, pay_rate: pr, recv_rate: rr, pay_basis: v.pay_basis,
      recv_basis: v.recv_basis, months: parseInt(v.months, 10), mtm_reset: v.mtm_reset === 'true' } };
  }
  /* which form field a refusal from the server is about (the input is marked; its text is never replaced) */
  function fieldOfError(kind, e, v) {
    var d = (e && e.details) || {};
    if (d.field) { return d.field; }
    var code = e && e.code;
    if (code === 'OFF_MARKET') { return kind === 'IRS' ? 'fixed_rate' : (d.leg === 'receive' ? 'recv_rate' : 'pay_rate'); }
    if (code === 'UNKNOWN_UNDERLYING' || code === 'UNDERLYING_UNSUPPORTED' || (kind === 'TRS' && code === 'CURRENCY_UNSUPPORTED')) { return 'underlying_id'; }
    if (kind === 'XCCY' && code === 'CURRENCY_UNSUPPORTED') {
      return d.currency && v && String(v.recv_ccy || '').trim().toUpperCase() === d.currency ? 'recv_ccy' : 'pay_ccy';
    }
    return null;
  }
  function sig(o) { try { return JSON.stringify(o); } catch (e) { return String(Math.random()); } }

  /* the page's own small styles (the shared stylesheet is not this component's) */
  var cssDone = false;
  function ensureCss() {
    if (cssDone || document.getElementById('swapticket-css')) { cssDone = true; return; }
    cssDone = true;
    var s = document.createElement('style'); s.id = 'swapticket-css';
    s.textContent = '.swapticket { min-width: 0; }' +
      '.swapticket .st-wrap { overflow-x: auto; max-width: 100%; }' +
      '.swapticket .st-wrap > table { width: auto; min-width: 100%; }' +
      '.swapticket table.st-contracts th, .swapticket table.st-contracts td { padding: 0 5px; }' +
      '.swapticket table.st-contracts td.st-side { white-space: normal; min-width: 140px; max-width: 190px; line-height: 1.3; padding-top: 3px; padding-bottom: 3px; text-align: left; }' +
      '.swapticket table.st-contracts tr.detail > td { white-space: normal; text-align: left; padding: 8px 10px; background: rgba(255,255,255,.015); }' +
      '.swapticket .st-drawer { max-width: 760px; }' +
      '.swapticket .st-drawer .kv div:nth-child(even) { text-align: left; white-space: normal; }' +
      '.swapticket .st-meta { color: var(--fg3); font-family: var(--mono); font-size: 11px; margin: 0 0 6px; }' +
      '.swapticket .st-meta.warn { color: var(--warn); }' +
      '.swapticket .st-stale { color: var(--warn); font-family: var(--mono); font-size: 11.5px; margin: 6px 0; }' +
      '.swapticket .st-blocked { color: var(--warn); font-style: normal; }' +
      '.swapticket .st-result { font-family: var(--mono); font-size: 11.5px; border-left: 3px solid var(--accent); padding: 4px 8px; margin: 8px 0; }' +
      '.swapticket input.bad, .swapticket select.bad { border-color: var(--down); outline: 1px solid var(--down); }' +
      '.swapticket .preview .kv div:nth-child(even) { white-space: normal; }' +
      '.swapticket .st-dock .kv div:nth-child(even) { white-space: normal; }';
    document.head.appendChild(s);
  }

  /* ---- the journal entry behind a VM, a payment or a termination (PO-23) ------------------------------------------------- */
  function entryLink(id) {
    if (id == null || id === '') { return ''; }
    return T.h('a', { href: '#', title: 'open the journal entry', onclick: function (ev) { ev.preventDefault(); showEntry(id); } }, '#' + id);
  }
  function showEntry(id) {
    T.api.get('/api/accounting/journal/' + encodeURIComponent(id)).then(function (e) {
      var lines = (e && e.lines) || [];
      var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'account'), T.h('th', null, 'gl'), T.h('th', null, 'ccy'),
        T.h('th', { class: 'num' }, 'debit'), T.h('th', { class: 'num' }, 'credit'), T.h('th', null, 'instrument'))));
      var body = T.h('tbody');
      lines.forEach(function (l) {
        body.appendChild(T.h('tr', null, T.h('td', null, l.account_id || ''), T.h('td', { class: 'l', title: l.memo || '' }, (l.gl || '') + (l.gl_name ? ' ' + l.gl_name : '')),
          T.h('td', null, l.currency || ''), T.h('td', { class: 'num' }, l.debit == null && l.debit_c == null ? '' : money(l, 'debit')),
          T.h('td', { class: 'num' }, l.credit == null && l.credit_c == null ? '' : money(l, 'credit')), T.h('td', null, l.instrument_id || '')));
      });
      tbl.appendChild(body);
      T.ui.modal({ title: 'Journal entry #' + id + (e && e.kind ? ' . ' + e.kind : ''), wide: true,
        body: T.h('div', null, T.h('div', { class: 'hint' }, (e && e.memo) || ''), T.h('div', { class: 'st-wrap' }, tbl),
          T.h('div', { class: 'hint' }, 'business date ' + ((e && e.business_date) || '') + (e && e.ts ? ' . posted ' + (T.fmt.dt ? T.fmt.dt(e.ts) : e.ts) : ''))) });
    }, function (err) { T.ui.toast('journal entry #' + id + ': ' + describe(err), 'err', 6000); });
  }

  /* ---- the terminate dialog (any page: the ticket, the dock, Positions) -------------------------------------------------- */
  function terminateDialog(c, after) {
    var mark = c.mark || {};
    var otc = ['EQ_OPTION', 'FX_OPTION', 'EQ_FORWARD', 'FX_FORWARD'].indexOf(c.kind) >= 0;
    /* V104R1-07: the mark shown is the table's last refresh; the termination re-prices when Terminate is pressed - said so */
    var REPRICE = ' The dealer re-prices when you press Terminate: the value booked is the model value at that moment, which can differ from the mark shown here.';
    var rowsKv = [['model mark (last refresh)', amt(mark.value) + (mark.quality ? ' (' + mark.quality + (mark.as_of ? ', ' + nyTime(mark.as_of) : '') + ')' : ''),
      (mark.note ? mark.note + ' - ' : '') + 'the termination is re-priced when you press Terminate'],
      ['VM balance', money(c, 'vm_balance') + ' (' + (c.margin_mode || 'CTM') + '; + received / - posted)'], ['initial margin released', money(c, 'im_balance')]];
    if (!otc) { rowsKv.push(['accrued so far', accruedText(c), 'nights not yet accrued are added at termination']); }
    var body = T.h('div', null,
      T.h('div', { class: 'hint' }, otc
        ? 'Terminates ' + c.contract_id + ' (' + kindShort(c) + ', ' + sideText(c.side, c) + ') at the model value with the half-width of the dealer against you (an option is sold back at' +
          ' the lower vol or bought back at the higher; a forward is closed at the forward -/+ the half-width); the variation margin balance is unwound and the initial margin is released.' + REPRICE
        : 'Terminates ' + c.contract_id + ' (' + kindShort(c) + ', ' + sideText(c.side, c) + ') at the current model mark. Every night owed since the last' +
          ' accrual is posted first, then the accrued coupons settle net, the variation margin balance is unwound and the initial margin is released.' + REPRICE),
      T.kv(rowsKv));
    var err = T.h('div', { class: 'error', hidden: true });
    body.appendChild(err);
    T.ui.modal({
      title: 'Terminate ' + c.contract_id, body: body, sticky: true,
      actions: [{ label: 'Cancel' }, { label: 'Terminate', cls: 'primary', onClick: function (m, btn) {
        btn.disabled = true; err.hidden = true;
        T.api.post('/api/otc/contracts/' + encodeURIComponent(c.contract_id) + '/terminate', {}).then(function (r) {
          m.close();
          var text = 'Terminated ' + c.contract_id + ' . value ' + money(r, 'termination_value') + ' . coupons settled: pay ' + money(r, 'accrued_pay') + ' / receive ' +
            money(r, 'accrued_receive') + (r.accrued_through ? ' (accrued through the night of ' + r.accrued_through + ((r.caught_up || []).length ? '; ' + r.caught_up.length +
            ' night accrual(s) posted first' : '') + ')' : '') + ' . cash ' + money(r, 'cash') + ' . IM released ' + money(r, 'im_released');
          T.ui.toast(text, 'ok', 8000);
          kick();
          if (after) { after(r, text); }
        }, function (e) { btn.disabled = false; err.textContent = describe(e); err.title = (e && e.code) || ''; err.hidden = false; });
      } }]
    });
  }
  /* the contract row of an account (or of every account), for the dock and for Positions */
  function findContract(acct, cid) {
    return T.api.get('/api/otc/contracts', acct ? { account_id: acct, detail: 1 } : { detail: 1 }).then(function (rows) {
      var list = Array.isArray(rows) ? rows : ((rows && rows.contracts) || []);
      var c = null;
      list.forEach(function (r) { if (r && r.contract_id === cid) { c = r; } });
      if (!c) { throw new Error('contract ' + cid + ' is not in ' + (acct || 'this book')); }
      return c;
    });
  }

  /* ---- the ticket ----------------------------------------------------------------------------------------------------- */
  function mount(el, opts) {
    opts = opts || {};
    ensureCss();
    var account = opts.account_id || '';
    var showNotes = opts.notes !== false;
    var kept = account ? memo[account] : null;
    var state = { kind: kept ? kept.kind : 'IRS', values: kept ? kept.values : {}, previews: {}, error: null, errorField: null, contracts: null, loadError: null,
      loadedAt: null, retry: 0, retryAt: null, timer: null, dead: false, open: {}, loading: false, blocked: opts.blocked || null, result: null, seq: 0 };
    KINDS.forEach(function (k) { if (!state.values[k.key]) { state.values[k.key] = copy(DEFAULTS[k.key]); } });
    if (account) { memo[account] = { kind: state.kind, values: state.values }; }
    var root = T.h('div', { class: 'swapticket' });
    T.clear(el).appendChild(root);
    var ui = {};
    var handle = { el: root, account_id: account, state: state };
    /* the registry's keys (keys.js; Settings > Keyboard; docs/KEYBINDS.md): in a box of the form 'Preview the order' (Enter)
       previews; 'Submit the order' (Ctrl+Enter) opens the booking confirmation of a fresh, fundable preview (the swap is booked
       only from that dialog); 'clear' (Esc) clears the preview */
    root.addEventListener('keydown', function (ev) {
      if (state.dead || !account) { return; }
      if (T.keys.is(ev, 'ticket.submit')) {
        ev.preventDefault(); ev.stopPropagation();
        var cur = state.previews[state.kind], p = (cur && cur.p) || {}, f = p.funding || {};
        var fundable = f.ok !== false && !(p.im_funding && p.im_funding.ok === false);
        if (cur && !cur.stale && !state.blocked && fundable) { onBook(cur); }
        return;
      }
      if (ui.form && ui.form.contains(ev.target) && T.keys.is(ev, 'ticket.preview')) { ev.preventDefault(); ev.stopPropagation(); if (!state.blocked) { onPreview(); } return; }
      if (T.keys.is(ev, 'ticket.clear') && state.previews[state.kind] && ui.clearBtn) { ev.preventDefault(); ev.stopPropagation(); ui.clearBtn.click(); }
    });

    function vals() { return state.values[state.kind]; }
    function alive() {
      if (state.dead) { return false; }
      if (!connected(root)) { stop(); return false; }
      return true;
    }
    function stop() {
      state.dead = true;
      if (state.timer) { clearTimeout(state.timer); state.timer = null; }
      var i = live.indexOf(handle); if (i >= 0) { live.splice(i, 1); }
    }

    /* ---- skeleton (built once; the blocks below re-render on their own) ---- */
    function build() {
      T.clear(root);
      if (!account) { root.appendChild(T.h('div', { class: 'error' }, 'no prime-broker account given to the swap ticket')); return; }
      root.appendChild(T.h('div', { class: 'hint' }, 'OTC swaps with ' + (T.accountName ? T.accountName(account) : account) + ' . dealt at the model par rate' +
        ' +/- the dealer\'s disclosed half-width; daily variation margin; initial margin per the prime broker\'s schedule (shown in each preview)'));
      ui.blocked = T.h('div', { class: 'conv st-blocked', hidden: !state.blocked }, state.blocked || '');
      root.appendChild(ui.blocked);
      ui.tabs = T.h('div'); root.appendChild(ui.tabs);
      ui.form = T.h('div'); root.appendChild(ui.form);
      ui.previewBtn = T.h('button', { class: 'btn primary', onclick: function () { onPreview(0); } }, PREVIEW_LABEL[state.kind]);
      ui.clearBtn = T.h('button', { class: 'btn small', onclick: function () { state.previews[state.kind] = null; state.error = null; state.errorField = null; renderError(); renderPreview(); renderForm(); } }, 'Clear preview');
      ui.resetBtn = T.h('button', { class: 'btn small', title: 'put this form back to its defaults', onclick: function () {
        state.values[state.kind] = copy(DEFAULTS[state.kind]); state.previews[state.kind] = null; state.error = null; state.errorField = null;
        if (memo[account]) { memo[account].values = state.values; }
        renderForm(); renderError(); renderPreview(); } }, 'Reset terms');
      root.appendChild(T.h('div', { class: 'actions' }, ui.previewBtn, ' ', ui.clearBtn, ' ', ui.resetBtn));
      ui.error = T.h('div', { class: 'error', hidden: true }); root.appendChild(ui.error);
      ui.preview = T.h('div'); root.appendChild(ui.preview);
      ui.result = T.h('div', { class: 'st-result', hidden: true }); root.appendChild(ui.result);
      ui.contracts = T.h('div', { class: 'section contracts' }); root.appendChild(ui.contracts);
      if (showNotes && T.convNote) { root.appendChild(T.convNote(NOTE_KEYS)); }
      renderTabs(); renderForm(); renderError(); renderPreview(); renderContracts();
    }
    function renderTabs() {
      T.clear(ui.tabs).appendChild(T.tabs(KINDS, state.kind, function (k) {
        if (k === state.kind) { return; }
        state.kind = k; state.error = null; state.errorField = null;
        if (memo[account]) { memo[account].kind = k; }
        renderTabs(); renderForm(); renderError(); renderPreview();
      }));
    }
    function renderError() {
      ui.error.textContent = state.error || '';
      ui.error.hidden = !state.error;
    }
    function setBlocked(reason) {
      state.blocked = reason || null;
      if (ui.blocked) { ui.blocked.textContent = state.blocked || ''; ui.blocked.hidden = !state.blocked; }
      if (ui.previewBtn) { ui.previewBtn.disabled = !!state.blocked; }
      renderPreview();
    }

    /* ---- the term forms: built from state.values[kind]; every keystroke is kept there ---- */
    function touched(name) {
      if (state.errorField === name) {
        state.errorField = null;
        var b = ui.form.querySelector('[data-field="' + name + '"]');
        if (b) { b.classList.remove('bad'); b.removeAttribute('aria-invalid'); }
      }
      var pv = state.previews[state.kind];
      if (pv && !pv.stale) {
        var r = readTerms(state.kind, vals());
        if (!r.terms || sig(r.terms) !== pv.sig) { pv.stale = true; renderPreview(); }
      }
    }
    function bind(name, input) {
      input.setAttribute('data-field', name);
      var v = vals()[name];
      if (input.tagName === 'SELECT') {
        var known = Array.prototype.some.call(input.options, function (o) { return o.value === String(v); });
        if (!known && v != null && v !== '') { input.appendChild(opt(String(v), String(v))); }
      }
      input.value = v == null ? '' : String(v);
      function upd() { vals()[name] = input.value; touched(name); }
      input.addEventListener('input', upd);
      input.addEventListener('change', upd);
      if (state.errorField === name) { input.classList.add('bad'); input.setAttribute('aria-invalid', 'true'); }
      return input;
    }
    function text(name, width, placeholder) {
      return bind(name, T.h('input', { type: 'text', style: 'width:' + width + 'px', spellcheck: 'false', placeholder: placeholder || null, autocomplete: 'off' }));
    }
    function select(name, options) { var s = T.h('select'); options.forEach(function (o) { s.appendChild(opt(o[0], o[1])); }); return bind(name, s); }
    function basis(name) { return select(name, [['30/360', '30/360'], ['ACT/360', 'ACT/360']]); }
    function irsForm() {
      return T.form([
        T.field('notional (USD)', text('notional', 160), 'sent as integer cents; no floating point'),
        T.field('side', select('side', [['PAY_FIXED', 'pay fixed / receive SOFR'], ['RECEIVE_FIXED', 'receive fixed / pay SOFR']]), 'pay fixed = par + half-width; receive fixed = par - half-width'),
        T.field('tenor', select('tenor_months', TENORS.map(function (m) { return [String(m), m + ' months' + (m % 12 === 0 ? ' (' + (m / 12) + 'y)' : '')]; })),
          'a multiple of 6 months and of the floating period; dates roll modified following on the trading calendar'),
        T.field('fixed rate (%)', text('fixed_rate', 130, 'blank = quote'), 'blank = the model par rate +/- the half-width; 0.001 % precision; a rate better for you than that quote is refused'),
        T.field('fixed leg basis', basis('fixed_basis'), 'semi-annual fixed leg; 30/360 unless booked ACT/360'),
        T.field('floating period', select('float_months', [['3', '3 months'], ['6', '6 months'], ['1', '1 month'], ['12', '12 months']]), 'SOFR ACT/360; the net coupon is paid on each payment date'),
        /* V104R1-09: short option texts (the select clipped the long ones at 1100 px); the full sentences are in the hint and the preview */
        T.field('SOFR fixing', select('fixing', [['TERM', 'Term: fixed at each reset'], ['COMPOUND_ARREARS', 'Compounded daily in arrears']]),
          FIXING_LABEL.TERM + '; or ' + FIXING_LABEL.COMPOUND_ARREARS + ' - only known at the period end')
      ]);
    }
    function trsForm() {
      return T.form([T.field('underlying', text('underlying_id', 120), 'a US-listed stock or ETF quoted in USD'),
        T.field('units', text('units', 120), 'whole shares; notional = units x the observed reset price'),
        T.field('side', select('receiver', [['true', 'receive total return / pay SOFR + spread'], ['false', 'pay total return / receive SOFR - spread']]),
          'the financing spread is the prime broker\'s schedule, against you on both sides'),
        T.field('reset', select('reset_months', [['1', '1 month'], ['3', '3 months'], ['6', '6 months'], ['12', '12 months']]), 'return, dividends and financing settle in cash at each reset; the notional is re-struck at the close'),
        T.field('tenor', select('tenor_months', [['3', '3 months'], ['6', '6 months'], ['12', '12 months'], ['24', '24 months'], ['36', '36 months'], ['60', '60 months']]), 'a multiple of the reset period')]);
    }
    function xccyForm() {
      return T.form([T.field('pay currency', text('pay_ccy', 80), 'we BORROW this currency: its notional is received at the start and repaid at maturity; we pay its coupons'),
        T.field('receive currency', text('recv_ccy', 80), 'we LEND this currency: its notional is paid at the start and comes back at maturity; we receive its coupons'),
        T.field('pay notional', text('pay_notional', 160, 'blank = at the deal FX rate'), 'give one notional; the other is struck at the observed deal FX rate'),
        T.field('receive notional', text('recv_notional', 160, 'blank = at the deal FX rate')),
        T.field('pay rate (%)', text('pay_rate', 130, 'blank = quote'), 'blank = the dealer quote (model par + half-width); a lower rate is refused'),
        T.field('receive rate (%)', text('recv_rate', 130, 'blank = quote'), 'blank = the dealer quote (model par - half-width); a higher rate is refused'),
        T.field('pay leg basis', basis('pay_basis')), T.field('receive leg basis', basis('recv_basis')),
        T.field('tenor', select('months', [['12', '12 months'], ['24', '24 months'], ['36', '36 months'], ['60', '60 months']]), 'a multiple of 6 months: semi-annual coupons on both legs'),
        T.field('notional reset', select('mtm_reset', [['true', 'mark-to-market (USD notional reset)'], ['false', 'fixed notionals']]),
          'mark-to-market: the USD notional is re-struck at each period start at the 17:00 ET FX close, the difference settled in cash')]);
    }
    function renderForm() {
      var form = state.kind === 'IRS' ? irsForm() : (state.kind === 'TRS' ? trsForm() : xccyForm());
      T.clear(ui.form).appendChild(form);
      if (ui.previewBtn) { ui.previewBtn.textContent = PREVIEW_LABEL[state.kind]; ui.previewBtn.disabled = !!state.blocked; }
    }
    function focusField(name) {
      if (!name) { return; }
      var b = ui.form.querySelector('[data-field="' + name + '"]');
      if (b && b.focus) { try { b.focus(); } catch (e) { /* focus is a convenience */ } }
    }

    /* ---- preview ---- */
    /* V104R1-06: a preview whose price is being fetched (the first TRS on a name, a currency pair not yet quoted) answers 202 with
       retry_ms: the ticket asks again by itself (up to 2 minutes), saying so, instead of asking the trader to click again */
    var PREVIEW_WAIT_MS = 120000;                 /* a first quote can take a minute right after a start (the worker's first refresh): ask again for up to 2 min */
    function onPreview(attempt, t0) {
      attempt = typeof attempt === 'number' ? attempt : 0;
      t0 = typeof t0 === 'number' ? t0 : Date.now();
      function waiting() { return Date.now() - t0 < PREVIEW_WAIT_MS; }
      if (state.blocked) { return; }
      var kind = state.kind;
      var r = readTerms(kind, vals());
      if (r.error) {
        state.error = r.error; state.errorField = r.field || null; state.previews[kind] = null;
        renderForm(); renderError(); renderPreview(); focusField(r.field);
        return;
      }
      state.error = null; state.errorField = null; renderError();
      var seq = ++state.seq;
      ui.previewBtn.disabled = true; ui.previewBtn.textContent = 'Previewing ...';
      function done() { if (ui.previewBtn) { ui.previewBtn.disabled = !!state.blocked; ui.previewBtn.textContent = PREVIEW_LABEL[state.kind]; } }
      function again(ms, what) {
        ui.previewBtn.disabled = true; ui.previewBtn.textContent = 'Fetching the price ...';
        ui.error.textContent = what + ' - asking again in ' + Math.max(1, Math.round(Number(ms || 2000) / 1000)) + ' s'; ui.error.hidden = false;
        setTimeout(function () { if (alive() && seq === state.seq && kind === state.kind) { onPreview(attempt + 1, t0); } }, Number(ms) || 2000);
      }
      T.api.post('/api/otc/preview', { account_id: account, kind: kind, terms: r.terms }).then(function (p) {
        if (!alive() || seq !== state.seq) { return; }
        if (p && p.pending) {
          var b0 = p.body || {};
          if (waiting()) { again(p.retry_ms, (b0.message || 'the price is being fetched') + ' (' + Math.round((Date.now() - t0) / 1000) + ' s so far)'); return; }
          done(); state.previews[kind] = null;
          if (kind === state.kind) { state.error = (b0.message || 'the price is still being fetched') + ' - try Preview again in a moment'; renderError(); renderPreview(); }
          return;
        }
        done();
        state.previews[kind] = { p: p, kind: kind, terms: r.terms, sig: sig(r.terms), stale: false };
        if (kind === state.kind) { renderPreview(); }
      }, function (e) {
        if (!alive() || seq !== state.seq) { return; }
        if (e && e.details && e.details.retry_ms && waiting() && kind === state.kind) { again(e.details.retry_ms, e.message || 'the price is being fetched'); return; }
        done();
        state.previews[kind] = null;
        if (kind !== state.kind) { return; }
        state.error = describe(e);
        ui.error.title = (e && e.code) || '';
        state.errorField = fieldOfError(kind, e, vals());
        renderError(); renderPreview();
        if (state.errorField) { renderForm(); focusField(state.errorField); }
      });
    }
    function renderPreview() {
      if (!ui.preview) { return; }
      T.clear(ui.preview);
      var pv = state.previews[state.kind];
      if (!pv) { return; }
      var p = pv.p || {};
      var k = p.kind || pv.kind;
      var box = k === 'TRS' ? trsPreviewBlock(p) : (k === 'XCCY' ? xccyPreviewBlock(p) : irsPreviewBlock(p));
      if (pv.stale) { box.insertBefore(T.h('div', { class: 'st-stale' }, 'The terms were changed after this preview: preview again before booking.'), box.firstChild.nextSibling); }
      var f = p.funding || {};
      var fundable = f.ok !== false && !(p.im_funding && p.im_funding.ok === false);
      box.appendChild(T.h('div', { class: 'hint' }, 'Fees: ' + (p.fees || 'none beyond the half-width (disclosed)') + '. Nothing is booked until you confirm.'));
      var why = state.blocked ? state.blocked : (pv.stale ? 'preview again first' : (!fundable ? 'the initial margin cannot be funded' : ''));
      box.appendChild(T.h('div', { class: 'actions' }, T.h('button', { class: 'btn primary', disabled: !!why, title: why || ('opens the booking confirmation' + (T.keys.paren ? T.keys.paren('ticket.submit') : '')), onclick: function () { onBook(pv); } }, 'Book this swap ...'),
        why ? T.h('span', { class: 'hint' }, ' ' + why) : null));
      ui.preview.appendChild(box);
    }
    function vmText(p) {
      return (p.margin_mode || 'CTM') + (p.margin_mode === 'STM' ? ' - settled to market (realised daily)' : ' - collateralised to market (collateral, not P&L)');
    }
    function trsPreviewBlock(p) {
      var box = T.h('div', { class: 'section preview' }, T.h('h2', null, 'Preview . total return swap', T.h('small', null, 'observed reset price; model financing, disclosed')));
      var fin = p.financing || {};
      var q = p.quote || {};
      var resets = p.reset_dates || [];
      box.appendChild(T.kv([
        ['side', sideText(p.side, p)],
        ['underlying', (p.underlying_id || '') + (p.underlying_name ? ' . ' + p.underlying_name : '') + ' x ' + (p.units == null ? '' : T.fmt.qty(p.units))],
        ['reset price (observed)', T.h('span', null, T.px ? T.px(q) : px(p.reset_px), ' ', p.reset_px_source || ''), 'the underlying\'s fresh or closing print; the reset price is never a default'],
        ['notional', money(p, 'notional') + ' USD', 'units x the reset price'],
        ['financing', (fin.rate_pct || 'unknown: no SOFR print yet') + ' ' + (fin.direction || '') + ' (SOFR ' + (fin.sofr == null ? 'n/a' : fin.sofr + ' %') +
          (fin.sofr_obs_date ? ' ' + fin.sofr_obs_date : '') + ' ' + (p.receiver === false ? '-' : '+') + ' ' + (fin.spread_pct || p.spread_pct || '') + ')', fin.formula || ''],
        ['financing per night', fin.per_night == null && fin.per_night_c == null ? 'n/a' : money(fin, 'per_night'), fin.note || ''],
        ['resets', 'every ' + (p.reset_months || '') + ' month(s): ' + resets.slice(0, 3).join(', ') + (resets.length > 3 ? ' ... (' + resets.length + ')' : '')],
        ['dates', (p.start_date || '') + ' to ' + (p.maturity_date || '')],
        ['initial margin', money(p, 'im') + (p.im_pct ? ' (' + p.im_pct + ' of the notional)' : ''), p.im_rule || ''],
        ['IM funding', fundingText(p.funding), (p.funding && (p.funding.note + '. ' + (p.funding.rule || ''))) || ''],
        ['variation margin', vmText(p)],
        ['DV01', money(p, 'dv01'), p.dv01_note || '']
      ]));
      var rows = p.schedule || [];
      if (rows.length) {
        var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'reset date'), T.h('th', null, 'event'), T.h('th', { class: 'num' }, 'nights'),
          T.h('th', { class: 'num' }, 'financing (indicative)'))));
        var body = T.h('tbody');
        rows.forEach(function (r) {
          body.appendChild(T.h('tr', null, T.h('td', null, r.date), T.h('td', null, r.event || ''), T.h('td', { class: 'num' }, r.nights == null ? '' : String(r.nights)),
            T.h('td', { class: 'num' }, r.financing == null && r.financing_c == null ? 'n/a' : money(r, 'financing'))));
        });
        tbl.appendChild(body);
        box.appendChild(T.h('div', { class: 'st-wrap' }, tbl));
      }
      return box;
    }
    function legRow(l) {
      return T.h('tr', null, T.h('td', null, l.side || ''), T.h('td', null, l.currency || ''), T.h('td', { class: 'num' }, money(l, 'notional')),
        T.h('td', { class: 'num' }, (l.rate_pct || fmtPct(l.rate)) + (l.rate_given ? ' (given)' : ' (quote)')), T.h('td', { class: 'num' }, l.quote_pct || fmtPct(l.quote)),
        T.h('td', { class: 'num' }, l.par_rate_pct || fmtPct(l.par_rate)), T.h('td', null, l.basis || ''), T.h('td', { class: 'num' }, money(l, 'first_coupon')),
        T.h('td', { class: 'num' }, money(l, 'value')), T.h('td', { class: 'num' }, money(l, 'dv01')), T.h('td', { class: 'l', title: (l.curve && l.curve.curve) || '' }, l.role || ''));
    }
    function dealRate(p) {
      return T.h('span', { title: 'exact deal rate ' + (p.deal_rate || '') }, px(p.deal_rate) + ' USD per ' + (p.foreign_ccy || '') + ' ', T.px && p.fx_quote ? T.px(p.fx_quote) : '');
    }
    function xccyPreviewBlock(p) {
      var box = T.h('div', { class: 'section preview' }, T.h('h2', null, 'Preview . cross-currency swap', T.h('small', null, 'model quotes from the curves, disclosed')));
      var se = p.start_exchange || {};
      var wp = se.we_pay || {}, wr = se.we_receive || {};
      box.appendChild(T.kv([
        ['side', sideText(p.side, p)],
        ['deal FX rate (observed)', dealRate(p), 'the pair\'s fresh or closing print; the start exchange is booked at this rate'],
        ['deal rate source', p.deal_rate_source || ((p.inputs && p.inputs.fx_label) || 'n/a'), 'where the deal rate comes from and when it was observed'],
        ['notionals', 'USD ' + money(p, 'usd_notional') + ' . ' + (p.foreign_ccy || '') + ' ' + money(p, 'foreign_notional')],
        ['start exchange', (se.date || p.start_date || '') + ': we pay ' + (wp.currency || '') + ' ' + money(wp, 'amount') + ' (lent) and receive ' + (wr.currency || '') + ' ' +
          money(wr, 'amount') + ' (borrowed)', 'reversed at maturity at the same notionals'],
        ['notional reset', p.mtm_reset ? 'mark-to-market: USD notional re-struck on ' + (p.fx_reset_dates || []).join(', ') : 'none (fixed notionals)'],
        ['cost of dealing (mark0)', money(p, 'mark0'), 'the legs dealt at the dealer quotes vs the model par rates'],
        ['DV01 (USD curve, +1 bp)', money(p, 'dv01') + ' . foreign leg ' + money(p, 'dv01_foreign'), p.dv01_note || ''],
        ['initial margin', money(p, 'im') + (p.im_pct ? ' (' + p.im_pct + ' of the USD notional)' : ''), p.im_rule || ''],
        ['start funding', fundingText(p.funding), (p.funding && ((p.funding.note || '') + '. ' + (p.funding.rule || ''))) || ''],
        p.im_funding ? ['IM funding', fundingText(p.im_funding), p.im_funding.note || ''] : null,
        ['variation margin', vmText(p)],
        ['dates', (p.start_date || '') + ' to ' + (p.maturity_date || '') + ' . half-width ' + (p.half_width_bp || '') + ' bp per leg']
      ]));
      var legs = p.legs || {};
      var lt = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'leg'), T.h('th', null, 'ccy'), T.h('th', { class: 'num' }, 'notional'),
        T.h('th', { class: 'num' }, 'rate dealt'), T.h('th', { class: 'num' }, 'dealer quote'), T.h('th', { class: 'num' }, 'model par'), T.h('th', null, 'basis'),
        T.h('th', { class: 'num' }, 'first coupon'), T.h('th', { class: 'num' }, 'value (USD)'), T.h('th', { class: 'num' }, 'DV01 (USD)'), T.h('th', null, 'role'))));
      var lb = T.h('tbody');
      ['receive', 'pay'].forEach(function (s) { if (legs[s]) { lb.appendChild(legRow(legs[s])); } });
      lt.appendChild(lb);
      box.appendChild(T.h('div', { class: 'st-wrap' }, lt));
      var rows = p.schedule || [];
      if (rows.length) {
        var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'payment date'), T.h('th', null, 'event'), T.h('th', { class: 'num' }, 'receive'),
          T.h('th', { class: 'num' }, 'pay'), T.h('th', null, 'note'))));
        var body = T.h('tbody');
        rows.forEach(function (r) {
          body.appendChild(T.h('tr', null, T.h('td', null, r.date), T.h('td', null, r.event || ''), T.h('td', { class: 'num' }, (r.receive_ccy || '') + ' ' + money(r, 'receive')),
            T.h('td', { class: 'num' }, (r.pay_ccy || '') + ' ' + money(r, 'pay')), T.h('td', { class: 'l' }, r.indicative ? 'USD leg indicative until the FX reset of ' + (r.fx_reset || '') : '')));
        });
        tbl.appendChild(body);
        box.appendChild(T.h('div', { class: 'st-wrap' }, tbl));
      }
      return box;
    }
    function irsPreviewBlock(p) {
      var box = T.h('div', { class: 'section preview' }, T.h('h2', null, 'Preview . interest-rate swap', T.h('small', null, 'model numbers, disclosed')));
      var compound = p.fixing_method === 'COMPOUND_ARREARS';
      var kv = [
        ['side', sideText(p.side || (p.pay_fixed ? 'PAY_FIXED' : 'RECEIVE_FIXED'), p)],
        ['notional', money(p, 'notional') + ' ' + (p.currency || 'USD')],
        ['par rate (model)', p.par_rate_pct || fmtPct(p.par_rate)],
        ['dealt rate', (p.dealt_rate_pct || fmtPct(p.dealt_rate)) + '  (par ' + (p.pay_fixed === false ? '-' : '+') + ' ' + (p.half_width_bp || fmtPct(p.half_width)) + (p.half_width_bp ? ' bp' : '') + ')'],
        ['cost of dealing (mark0)', money(p, 'mark0')],
        ['DV01 (PV change for +1 bp)', money(p, 'dv01'), p.dv01_note || ''],
        ['initial margin', money(p, 'im') + ' (|DV01| x ' + (p.im_bp || 50) + ' bp)', p.im_rule || ''],
        ['IM funding', fundingText(p.funding), (p.funding && ((p.funding.note || '') + '. ' + (p.funding.rule || ''))) || ''],
        ['variation margin', vmText(p)],
        ['dates', (p.start_date || '') + ' to ' + (p.maturity_date || '')]
      ];
      if (p.fixing_method) { kv.push(['SOFR leg', FIXING_LABEL[p.fixing_method] || p.fixing_method]); }
      if (p.fixing) {
        kv.push([p.fixing.indicative ? 'SOFR now (indicative only)' : 'first fixing', 'SOFR ' + (p.fixing.sofr || '') + ' % (' + (p.fixing.obs_date || '') + ') -> ' + (p.fixing.rate_pct || fmtPct(p.fixing.rate)), p.fixing.method || '']);
      }
      if (p.inputs) { kv.push(['inputs', 'curve as of ' + (p.inputs.curve_as_of || 'n/a') + (p.inputs.curve_source ? ' (' + p.inputs.curve_source + ')' : '') + '; SOFR ' + (p.inputs.sofr || 'n/a') + (p.inputs.sofr_obs_date ? ' (' + p.inputs.sofr_obs_date + ')' : ''), p.inputs.model || '']); }
      box.appendChild(T.kv(kv));
      if (p.schedule && p.schedule.length) {
        var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'payment date'), T.h('th', { class: 'num' }, 'fixed leg'), T.h('th', { class: 'num' }, 'floating leg'), T.h('th', null, 'note'))));
        var body = T.h('tbody');
        p.schedule.forEach(function (r) {
          var note = r.indicative ? (compound ? 'indicative: compounded daily at the current SOFR' : 'indicative at the current fixing') : (r.float_rate ? 'fixed at ' + fmtPct(r.float_rate) : '');
          body.appendChild(T.h('tr', null, T.h('td', null, r.date), T.h('td', { class: 'num' }, r.fixed == null && r.fixed_c == null ? '' : money(r, 'fixed')),
            T.h('td', { class: 'num' }, r.float == null && r.float_c == null ? '' : money(r, 'float')), T.h('td', { class: 'l' }, note)));
        });
        tbl.appendChild(body);
        box.appendChild(T.h('div', { class: 'st-wrap' }, tbl));
      }
      return box;
    }

    /* ---- booking ---- */
    function onBook(pv) {
      if (!pv || pv.stale || state.blocked) { return; }
      var p = pv.p || {};
      var kind = p.kind || pv.kind;
      var rows;
      if (kind === 'TRS') {
        rows = [['side', sideText(p.side, p)], ['underlying', (p.underlying_id || '') + ' x ' + (p.units == null ? '' : T.fmt.qty(p.units)) + ' at the reset price ' + px(p.reset_px)],
          ['reset price source', p.reset_px_source || ''], ['notional', money(p, 'notional') + ' USD'],
          ['financing', ((p.financing || {}).rate_pct || 'n/a') + ' ' + ((p.financing || {}).direction || '')],
          ['initial margin', money(p, 'im') + (p.im_pct ? ' (' + p.im_pct + ')' : '')], ['IM funding', fundingText(p.funding)], ['VM', p.margin_mode || 'CTM'],
          ['dates', (p.start_date || '') + ' to ' + (p.maturity_date || '')]];
      } else if (kind === 'XCCY') {
        var lg = p.legs || {}, rl = lg.receive || {}, pl = lg.pay || {};
        rows = [['side', sideText(p.side, p)], ['deal FX rate', px(p.deal_rate) + ' USD per ' + (p.foreign_ccy || '') + ' (exact ' + (p.deal_rate || '') + ')'],
          ['deal rate source', p.deal_rate_source || 'n/a'],
          ['receive leg', (rl.currency || '') + ' ' + money(rl, 'notional') + ' at ' + (rl.rate_pct || fmtPct(rl.rate))],
          ['pay leg', (pl.currency || '') + ' ' + money(pl, 'notional') + ' at ' + (pl.rate_pct || fmtPct(pl.rate))],
          ['cost of dealing', money(p, 'mark0')], ['initial margin', money(p, 'im') + (p.im_pct ? ' (' + p.im_pct + ' of USD)' : '')],
          ['start exchange', ((p.start_exchange || {}).date || p.start_date || '')], ['start funding', fundingText(p.funding)],
          p.im_funding ? ['IM funding', fundingText(p.im_funding)] : null, ['VM', p.margin_mode || 'CTM'], ['dates', (p.start_date || '') + ' to ' + (p.maturity_date || '')]];
      } else {
        rows = [['side', sideText(p.side, p)], ['notional', money(p, 'notional')], ['dealt rate', p.dealt_rate_pct || fmtPct(p.dealt_rate)], ['cost of dealing', money(p, 'mark0')],
          ['DV01 (+1 bp)', money(p, 'dv01')], ['initial margin', money(p, 'im')], ['IM funding', fundingText(p.funding)], ['VM', p.margin_mode || 'CTM'],
          ['SOFR leg', FIXING_LABEL[p.fixing_method] || p.fixing_method || ''], ['dates', (p.start_date || '') + ' to ' + (p.maturity_date || '')]];
      }
      var err = T.h('div', { class: 'error', hidden: true });
      var body = T.h('div', null, T.h('div', { class: 'hint' }, 'Booking with ' + (p.broker || 'the prime broker') + ' in ' + (T.accountName ? T.accountName(account) : account) + '. ' +
        (kind === 'IRS' ? 'The dealt rate is a model rate; ' : (kind === 'TRS' ? 'The reset price is the observed print; ' : 'The deal FX rate is the observed print shown; ')) +
        'the initial margin leaves the account now (any Treasury sweep shown above moves first).'), T.kv(rows), err);
      T.ui.modal({
        title: 'Confirm swap booking', body: body, sticky: true,
        actions: [{ label: 'Cancel' }, { label: 'Book', cls: 'primary', onClick: function (m, btn) {
          btn.disabled = true; err.hidden = true;
          T.api.post('/api/otc/contracts', { account_id: account, kind: kind, terms: pv.terms }).then(function (r) {
            m.close();
            var c = r && r.contract ? r.contract : {};
            var cid = c.contract_id || r.contract_id || '';
            var t = r.im_transfer || {};
            var f = t.funding || {};
            var text = 'Booked ' + cid + ' (' + kindShort(c) + ', ' + sideText(c.side, c) + ') . IM ' + money(t, 'amount') +
              ((num(f, 'sweep') || 0) > 0 ? ' (Treasury sweep ' + money(f, 'sweep') + ')' : '') + (kind === 'XCCY' ? ' . deal rate ' + px(p.deal_rate) : '');
            T.ui.toast(text, 'ok', 6000);
            if (state.dead) { kick(); return; }
            state.previews[kind] = null;
            state.result = { text: text, entries: r.entries || [] };
            renderPreview(); renderResult();
            load();
            kick();
          }, function (e) { btn.disabled = false; err.textContent = describe(e); err.title = (e && e.code) || ''; err.hidden = false; });
        } }]
      });
    }
    function renderResult() {
      if (!ui.result) { return; }
      T.clear(ui.result);
      if (!state.result) { ui.result.hidden = true; return; }
      ui.result.appendChild(T.h('span', null, state.result.text));
      var ents = state.result.entries || [];
      if (ents.length) { ui.result.appendChild(T.h('span', null, ' . entries ')); ents.forEach(function (id, i) { if (i) { ui.result.appendChild(document.createTextNode(' ')); } ui.result.appendChild(entryLink(id)); }); }
      ui.result.hidden = false;
    }

    /* ---- the contracts table (PO-11): status and Terminate first, the rest in the drawer ---- */
    function metaLine() {
      if (state.loadError) {
        var secs = state.retryAt ? Math.max(1, Math.round((state.retryAt - Date.now()) / 1000)) : null;
        return T.h('div', { class: 'st-meta warn', title: state.loadError }, (state.contracts == null ? 'contracts could not be loaded' : 'the last refresh failed (showing the previous answer)') +
          ' - retrying' + (secs ? ' in ' + secs + ' s' : '') + ': ' + state.loadError);
      }
      var asOf = null;
      (state.contracts || []).forEach(function (c) { var m = c.mark || {}; if (m.as_of != null && (asOf == null || m.as_of > asOf)) { asOf = m.as_of; } });
      return T.h('div', { class: 'st-meta' }, (asOf != null ? 'model marks computed ' + nyTime(asOf) + ' ET' : (state.loadedAt ? 'loaded ' + new Date(state.loadedAt).toTimeString().slice(0, 8) : '')) +
        ' . refreshed every 30 s . VM, accruals and payments are in the ledger');
    }
    function renderContracts() {
      if (!ui.contracts) { return; }
      var oldWrap = ui.contracts.querySelector('.st-wrap');
      var scroll = oldWrap ? oldWrap.scrollLeft : 0;
      T.clear(ui.contracts);
      ui.contracts.appendChild(T.h('h2', null, 'Contracts', T.h('small', null, 'model mark = computed clean PV now; click a contract for its terms and history')));
      ui.contracts.appendChild(metaLine());
      var rows = state.contracts;
      if (rows == null) { ui.contracts.appendChild(T.h('div', { class: 'empty' }, state.loadError ? 'no contracts shown yet' : 'loading contracts ...')); return; }
      if (!rows.length) { ui.contracts.appendChild(T.h('div', { class: 'empty' }, 'no OTC contracts in ' + (T.accountName ? T.accountName(account) : account) + ' yet')); return; }
      var tbl = T.h('table', { class: 'grid st-contracts' }, T.h('thead', null, T.h('tr', null, T.h('th', { class: 'l', title: 'an open contract can be terminated' }, 'status'),
        T.h('th', { class: 'l' }, 'contract'), T.h('th', { class: 'l' }, 'kind / side'), T.h('th', { class: 'num' }, 'notional'), T.h('th', { class: 'l' }, 'maturity'),
        T.h('th', { class: 'num', title: 'computed clean PV now (hover a value for its inputs)' }, 'model mark'),
        T.h('th', { class: 'num', title: '+ received from the PB / - posted to the PB' }, 'VM balance'), T.h('th', { class: 'num' }, 'IM'),
        T.h('th', { class: 'num', title: 'PV change for +1 bp' }, 'DV01'))));
      var body = T.h('tbody');
      rows.forEach(function (c) { appendRow(body, c); });
      tbl.appendChild(body);
      var wrap = T.h('div', { class: 'st-wrap' }, tbl);
      ui.contracts.appendChild(wrap);
      wrap.scrollLeft = scroll;
    }
    function appendRow(body, c) {
      var mark = c.mark || {};
      var open = !!state.open[c.contract_id];
      var markTitle = (mark.quality || '') + (mark.as_of ? ' as of ' + nyTime(mark.as_of) + ' ET' : '') + (mark.note ? ' . ' + mark.note : '') + ' . NAV overlay (mark at the last VM) ' + money(c, 'mark');
      var notional = c.notional == null && c.notional_c == null ? 'n/a' : (T.fmt.compact ? T.fmt.compact(c.notional != null ? c.notional : (c.notional_c / 100)) : money(c, 'notional'));
      var tr = T.h('tr', { class: c.status === 'OPEN' ? '' : 'dim', 'data-contract': c.contract_id },
        T.h('td', { class: 'l' }, c.status === 'OPEN' ? T.h('button', { class: 'btn small', title: 'OPEN - terminate at the current model mark', onclick: function () { onTerminate(c); } }, 'Terminate')
          : (T.stateTag ? T.stateTag(c.status) : (c.status || ''))),
        T.h('td', { class: 'l' }, T.h('a', { href: '#', title: (open ? 'hide' : 'show') + ' the terms and history', onclick: function (e) { e.preventDefault(); state.open[c.contract_id] = !state.open[c.contract_id]; renderContracts(); } },
          (open ? '- ' : '+ ') + c.contract_id)),
        T.h('td', { class: 'st-side' }, T.h('b', null, kindShort(c)), ' ', sideText(c.side, c) + (c.fixing_method === 'COMPOUND_ARREARS' ? ' (compounded)' : '')),
        T.h('td', { class: 'num', title: money(c, 'notional') + ' ' + (c.notional_ccy || c.currency || '') }, notional + ' ' + (c.notional_ccy || c.currency || '')),
        T.h('td', { class: 'l' }, c.maturity_date || ''),
        T.h('td', { class: 'num ' + (T.signCls ? T.signCls(mark.value) : ''), title: markTitle }, amt(mark.value) + (mark.quality && mark.quality !== 'computed' ? ' ' + mark.quality : '')),
        T.h('td', { class: 'num', title: (c.margin_mode || '') + ': + received from the PB / - posted to the PB' }, money(c, 'vm_balance')),
        T.h('td', { class: 'num' }, money(c, 'im_balance')),
        T.h('td', { class: 'num' }, c.dv01 == null && c.dv01_c == null ? 'n/a' : money(c, 'dv01')));
      body.appendChild(tr);
      if (open) { body.appendChild(T.h('tr', { class: 'detail' }, T.h('td', { colspan: String(COLS) }, drawer(c)))); }
    }
    function drawer(c) {
      var h = c.history || {};
      var box = T.h('div', { class: 'history st-drawer' });
      var mark = c.mark || {};
      var d = c.detail || {};
      var kv = [['dealt', dealtText(c)], ['side', sideText(c.side, c)], ['notional', money(c, 'notional') + ' ' + (c.notional_ccy || c.currency || '')],
        ['dates', (c.start_date || '') + ' to ' + (c.maturity_date || '') + ' . ' + (c.margin_mode || 'CTM') + ' variation margin'],
        ['model mark', amt(mark.value) + ' . ' + (mark.quality || '') + (mark.as_of ? ' as of ' + nyTime(mark.as_of) + ' ET' : '') + (mark.note ? ' . ' + mark.note : '')],
        ['NAV overlay', money(c, 'mark') + ' (the mark at the last variation margin; 0 after a settled-to-market settlement)'],
        ['accrued', accruedText(c)]];
      if (c.kind === 'TRS') {
        var fn = d.financing_now || null;
        kv.push(['financing now', fn ? (fmtPct(fn.rate) + ' (SOFR ' + fn.sofr + ' % ' + fn.obs_date + ', spread ' + fmtPct(d.spread) + ')') : 'no SOFR print within 10 days (waits; never defaulted)']);
        kv.push(['accrued legs', 'receivable ' + money(d, 'accrued_receivable') + ' . payable ' + money(d, 'accrued_payable')]);
        kv.push(['reset dates', (d.reset_dates || []).slice(0, 6).join(', ') + ((d.reset_dates || []).length > 6 ? ' ...' : '')]);
      } else if (c.kind === 'XCCY') {
        var legs = d.legs || {};
        ['receive', 'pay'].forEach(function (s) {
          var l = legs[s];
          if (l) { kv.push([s + ' leg', l.currency + ' ' + money(l, 'notional') + ' at ' + fmtPct(l.rate) + ' ' + (l.basis || '') + ' . accrued ' + money(l, 'accrued') + ' . notional balance ' + money(l, 'notional_balance') + ' . ' + (l.role || '')]); }
        });
        kv.push(['deal FX', px(d.deal_rate) + (c.terms && c.terms.deal_rate_source ? ' (' + c.terms.deal_rate_source + ')' : '') + (d.mtm_reset ? ' . MTM notional resets' : ' . fixed notionals') +
          (d.started ? ' . start exchanged' : ' . start exchange pending') + (d.matured_exchange ? ' . maturity exchanged' : '')]);
      } else if (c.kind === 'IRS') {
        kv.push(['SOFR leg', fixingText(c.fixing)]);
      }
      if (c.detail_error) { kv.push(['detail', c.detail_error]); }
      kv.push(['next events', eventsText(c.next_events) || 'none']);
      kv.push(['DV01', c.dv01 == null && c.dv01_c == null ? 'n/a' : money(c, 'dv01') + ' (PV change for +1 bp)']);
      kv.push(['swap result', money(c, 'result')]);
      kv.push(['remaining periods', String(c.remaining_periods == null ? '' : c.remaining_periods)]);
      box.appendChild(T.kv(kv));
      var vm = h.vm || [];
      var vmBox = T.h('div', { class: 'section' }, T.h('h3', null, 'Variation margin (' + vm.length + ')'));
      if (!vm.length) { vmBox.appendChild(T.h('div', { class: 'empty' }, 'no variation margin yet (it runs from 17:05 ET each day)')); }
      else {
        var t1 = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'date'), T.h('th', { class: 'num' }, 'mark'), T.h('th', { class: 'num' }, 'delta'), T.h('th', null, 'mode'), T.h('th', null, 'entry'))));
        var b1 = T.h('tbody');
        vm.forEach(function (r) {
          b1.appendChild(T.h('tr', null, T.h('td', null, r.vm_date), T.h('td', { class: 'num' }, money(r, 'mark')),
            T.h('td', { class: 'num ' + (T.signCls ? T.signCls(T.num ? T.num(r, 'delta') : r.delta) : '') }, money(r, 'delta')), T.h('td', null, r.mode || ''), T.h('td', null, entryLink(r.entry_id))));
        });
        t1.appendChild(b1); vmBox.appendChild(T.h('div', { class: 'st-wrap' }, t1));
      }
      box.appendChild(vmBox);
      var rs = h.resets || [];
      var rsBox = T.h('div', { class: 'section' }, T.h('h3', null, 'Fixings, payments and exchanges (' + rs.length + ')'));
      if (!rs.length) { rsBox.appendChild(T.h('div', { class: 'empty' }, 'none yet')); }
      else {
        var t2 = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'date'), T.h('th', null, 'event'), T.h('th', { class: 'num' }, 'amount'), T.h('th', null, 'detail'), T.h('th', null, 'entry'))));
        var b2 = T.h('tbody');
        rs.forEach(function (r) {
          var dd = r.detail || {};
          b2.appendChild(T.h('tr', null, T.h('td', null, r.event_date), T.h('td', null, r.kind), T.h('td', { class: 'num' }, r.kind === 'FIX' ? '' : money(r, 'amount')),
            T.h('td', { class: 'l', style: 'white-space:normal' }, resetText(r.kind, dd)), T.h('td', null, entryLink(r.entry_id))));
        });
        t2.appendChild(b2); rsBox.appendChild(T.h('div', { class: 'st-wrap' }, t2));
      }
      box.appendChild(rsBox);
      return box;
    }
    /* one line per otc_resets row: IRS fixings and payments, TRS resets, XCCY exchanges / coupons / FX resets */
    function resetText(kind, d) {
      if (kind === 'FIX') { return 'SOFR ' + (d.sofr || '') + ' % (' + (d.obs_date || '') + ') -> ' + (d.rate_pct || fmtPct(d.rate)) + ' for ' + (d.period_start || '') + '..' + (d.period_end || ''); }
      if (kind === 'PAY' && (d.receive != null || d.receive_c != null)) { return 'receive ' + money(d, 'receive') + ' / pay ' + money(d, 'pay') + ' / net ' + money(d, 'net'); }
      if (kind === 'PAY' && d.legs) {
        return Object.keys(d.legs).map(function (s) { var l = d.legs[s] || {}; return s + ' ' + (l.currency || '') + ' ' + money(l, 'amount'); }).join(' / ') + ' . net USD ' + money(d, 'net_usd');
      }
      if (kind === 'RESET') {
        return 'return ' + money(d, 'return') + ' (' + px(d.reset_px_old) + ' -> ' + px(d.reset_px) + ') . accrued received ' + money(d, 'accrued_received') + ' / paid ' +
          money(d, 'accrued_paid') + ' . VM unwound ' + money(d, 'vm_unwound') + ' . net cash ' + money(d, 'cash') + ' . notional ' + money(d, 'notional');
      }
      if (kind === 'NOTIONAL_START' || kind === 'NOTIONAL_END') {
        var a = d.we_pay || {}, b = d.we_receive || {};
        return (a.currency ? 'we pay ' + a.currency + ' ' + money(a, 'amount') : '') + (b.currency ? ' . we receive ' + b.currency + ' ' + money(b, 'amount') : '') + (d.rate ? ' at ' + px(d.rate) : '');
      }
      if (kind === 'FX_RESET') { return 'USD notional re-struck at ' + px(d.usd_per_unit) + ' USD per ' + (d.foreign_ccy || '') + ': ' + money(d, 'old_usd_notional') + ' -> ' + money(d, 'usd_notional') + ', settled ' + money(d, 'cash') + (d.usd_leg ? ' (' + d.usd_leg + ')' : ''); }
      return Object.keys(d || {}).filter(function (k) { return typeof d[k] !== 'object'; }).map(function (k) { return k.replace(/_/g, ' ') + ' ' + d[k]; }).join(' . ');
    }

    /* ---- terminate ---- */
    function onTerminate(c) {
      terminateDialog(c, function (r, text) {
        if (state.dead) { return; }
        state.result = { text: text, entries: r.entries || [] };
        renderResult();
        load();
      });
    }
    function terminate(cid) {
      var c = null;
      (state.contracts || []).forEach(function (r) { if (r.contract_id === cid) { c = r; } });
      if (c) { onTerminate(c); return true; }
      findContract(account, cid).then(onTerminate, function (e) { T.ui.toast(describe(e), 'err', 5000); });
      return true;
    }
    function prefill(kind, terms) {
      kind = String(kind || '').toUpperCase();
      if (!DEFAULTS[kind]) { return false; }
      var v = copy(DEFAULTS[kind]);
      var given = formValues(kind, terms || {});
      Object.keys(given).forEach(function (k) { v[k] = given[k]; });
      state.values[kind] = v;
      state.kind = kind; state.previews[kind] = null; state.error = null; state.errorField = null;
      if (memo[account]) { memo[account].kind = kind; memo[account].values = state.values; }
      renderTabs(); renderForm(); renderError(); renderPreview();
      try { root.scrollIntoView({ block: 'start' }); } catch (e) { /* a convenience */ }
      return true;
    }

    /* ---- the contracts: loaded at once, every 30 s in place, with back-off after a failure (PO-03) ---- */
    function schedule(ms) {
      if (state.timer) { clearTimeout(state.timer); }
      state.timer = setTimeout(function () { state.timer = null; if (alive()) { load(); } }, ms);
    }
    function load() {
      if (state.dead) { return Promise.resolve(null); }
      if (state.timer) { clearTimeout(state.timer); state.timer = null; }
      state.loading = true;
      return T.api.get('/api/otc/contracts', { account_id: account, detail: 1 }).then(function (rows) {
        if (!alive()) { return null; }
        state.loading = false; state.loadError = null; state.retry = 0; state.retryAt = null;
        state.contracts = Array.isArray(rows) ? rows : ((rows && rows.contracts) || []);
        state.loadedAt = Date.now();
        renderContracts();
        schedule(REFRESH_MS);
        return state.contracts;
      }, function (e) {
        if (!alive()) { return null; }
        state.loading = false;
        state.loadError = describe(e);
        var wait = RETRY_MS[Math.min(state.retry, RETRY_MS.length - 1)];
        state.retry += 1;
        state.retryAt = Date.now() + wait;
        renderContracts();
        schedule(wait);
        return null;
      });
    }

    handle.refresh = function () { return load(); };
    handle.unmount = function () { stop(); T.clear(root); };
    handle.readTerms = function () { return readTerms(state.kind, vals()); };
    handle.prefill = prefill;
    handle.terminate = terminate;
    handle.setBlocked = setBlocked;
    live.push(handle);
    build();
    if (pending && pending.account_id === account) { var pf = pending; pending = null; prefill(pf.kind, pf.terms); }
    if (account) { load(); }
    return handle;
  }

  /* the visible ticket of an account (else any visible one) */
  function liveHandle(acct) {
    var found = null;
    live.slice().forEach(function (h) {
      if (h.state.dead || !connected(h.el)) { var i = live.indexOf(h); if (i >= 0) { live.splice(i, 1); } return; }
      if (!acct || h.account_id === acct) { found = h; }
    });
    return found;
  }
  /* C21: fill a swap form from anywhere (the hedge panel's TRS overlay); opens the account's OTC tab when no ticket is visible */
  function prefill(kind, terms, opts) {
    opts = opts || {};
    var h = liveHandle(opts.account_id);
    if (h) { return h.prefill(kind, terms); }
    if (opts.account_id && T.router && T.router.go) {
      pending = { account_id: opts.account_id, kind: kind, terms: terms };
      T.router.go(T.router.account(opts.account_id, 'markets/otc'));
      return true;
    }
    return false;
  }
  /* C21: the terminate dialog of one contract from anywhere (Positions) */
  function terminate(contractId, opts) {
    opts = opts || {};
    var h = liveHandle(opts.account_id);
    if (h) { return h.terminate(contractId); }
    findContract(opts.account_id, contractId).then(function (c) { terminateDialog(c, null); }, function (e) { T.ui.toast(describe(e), 'err', 5000); });
    return true;
  }

  /* the dock for a swap (T.ticket.open on a SWAP instrument: Positions' Close / Hedge on a swap row): its summary, Terminate and
     the OTC tab - a swap is never an order */
  function dockOpen(body, fields) {
    fields = fields || {};
    ensureCss();
    var acct = fields.account_id || '';
    var cid = fields.instrument_id || fields.contract_id || '';
    var box = T.h('div', { class: 'swapticket st-dock' });
    T.clear(body).appendChild(box);
    box.appendChild(T.h('h2', null, 'OTC contract', cid ? T.h('small', null, cid) : null));
    var otcLink = acct ? T.h('a', { class: 'btn small', href: T.router.account(acct, 'markets/otc') }, 'Open the OTC tab') : null;
    if (!cid) { box.appendChild(T.h('div', { class: 'hint' }, 'Swaps are booked and terminated on the OTC tab of a prime-broker account.')); if (otcLink) { box.appendChild(otcLink); } return null; }
    var slot = T.h('div', { class: 'empty' }, 'loading ' + cid + ' ...');
    box.appendChild(slot);
    findContract(acct, cid).then(function (c) {
      var mark = c.mark || {};
      var kv = T.kv([['kind / side', kindShort(c) + ' . ' + sideText(c.side, c)], ['notional', money(c, 'notional') + ' ' + (c.notional_ccy || c.currency || '')],
        ['status', c.status || ''], ['maturity', c.maturity_date || ''], ['model mark', amt(mark.value) + (mark.quality ? ' (' + mark.quality + ')' : '')],
        ['VM balance', money(c, 'vm_balance') + ' (' + (c.margin_mode || 'CTM') + ')'], ['IM', money(c, 'im_balance')], ['accrued', accruedText(c)],
        ['next', eventsText((c.next_events || []).slice(0, 2)) || 'none']]);
      var actions = T.h('div', { class: 'actions' });
      if (c.status === 'OPEN') {
        actions.appendChild(T.h('button', { class: 'btn primary', onclick: function () { terminateDialog(c, function () { dockOpen(body, fields); }); } }, 'Terminate ...'));
        actions.appendChild(document.createTextNode(' '));
      }
      if (otcLink) { actions.appendChild(otcLink); }
      box.replaceChild(T.h('div', null, kv, T.h('div', { class: 'hint' }, 'A swap is not traded with an order: it is terminated at the current model mark with the dealer.'), actions), slot);
    }, function (e) {
      box.replaceChild(T.h('div', { class: 'error' }, describe(e)), slot);
      if (otcLink) { box.appendChild(otcLink); }
    });
    return null;
  }
  T.ticketKinds.SWAP = { open: dockOpen };

  T.components.swapticket = { mount: mount, prefill: prefill, terminate: terminate, toCents: toCents, pctToFraction: pctToFraction, fractionToPct: fractionToPct,
    fmtPct: fmtPct, eventsText: eventsText, fixingText: fixingText, sideText: sideText, readTerms: readTerms, formValues: formValues };
  if (T.keys && typeof T.keys.uses === 'function') { T.keys.uses('components/swapticket.js'); }   /* its keys follow Settings > Keyboard */
})();
