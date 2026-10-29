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
 * Previews POST /api/otc/preview   {account_id, kind: IRS|TRS|XCCY, terms}  -> the deal, the funding line, the conventions (a refused
 *                                  preview answers 200 {refused, code, message, blockers}: its reason is shown, never a 4xx - V104R2-BW-05)
 * Quotes   POST /api/otc/rfqs {account_id, kind, terms} -> {rfq}   (C14, 1.0.6: the preview's "Request quote"; the simulated dealer answers
 *          GET  /api/otc/rfqs?account_id=&status=all            on the next lifecycle tick with a quote good for 120 s)
 *          POST /api/otc/rfqs/{id}/accept | /decline           -> books at the quoted terms (the contract starts PENDING CONFIRMATION) / closes it
 * Timeline GET  /api/otc/contracts/{id}/timeline               -> requested, quoted, accepted, booked, IM, confirmed, each VM, resets ... (drawer)
 * Closes   POST /api/otc/contracts/{id}/terminate {}          -> {termination_value, accrued_pay, accrued_receive, cash, entries}
 * Entries  GET  /api/accounting/journal/{entry_id}            -> the journal entry behind a VM, a payment or a termination
 *
 * Conventions shown on the form
 * - Rates are typed in percent (4.01 = 4.01 %) and sent as decimal fractions ("0.0401") by string arithmetic; notionals are
 *   typed in their currency and sent as integer cents; no floating-point money anywhere in this file.
 * - The terms typed on each tab are kept per kind (state.values[kind]) through every preview, error, refresh and tab switch;
 *   only the preview block and the contracts block re-render.  A refused input stays as typed, marked, for correction.  A
 *   preview whose terms were edited afterwards is marked out of date and cannot be booked until it is previewed again.
 * - The dealer's price is the model par rate +/- the PB's disclosed half-width (SWAP_MARK): the preview shows it, the cost of
 *   dealing (mark0), the signed DV01, the initial margin and how it is funded (the account's own cash net of what its payables
 *   and working orders already need, then one Treasury sweep, then PB financing), the schedule and the fixing BEFORE anything
 *   is requested.  1.0.6 (C14): "Request quote" asks the simulated dealer; its quote ("indicative model quote, simulated dealer")
 *   appears under Quote requests with a countdown and Accept / Decline; accepting books at the quoted terms after a confirmation
 *   that repeats them.  The same block serves the CDX and options / forwards tickets (T.components.swapticket.mountRfqs).  A
 *   cross-currency preview and its confirmation show the deal FX rate with its source and time.
 * - FINSIM1 (P4): a contract booked as of its trade date (the owner's Goldman contracts) carries an AS-OF badge with its source label
 *   ("As-of booking - simulated OTC trade with ..., value date ..., entered ... by the operator at the owner's instruction (batch ...)")
 *   and the chip CONFIRMED (as-of booking at the owner's instruction; no electronic confirmation in the simulator); every mark is
 *   badged MODEL (a valuation from observed inputs, never a dealer price); the drawer shows the trade / value dates, the IRS contract
 *   curve spread, what NAV carries now (the intraday model mark or the mark of the last VM), the cost basis and the P&L by kind.
 * - The contracts table: status and Terminate first, then the contract, kind and side, notional, maturity, the live model mark
 *   (computed), the VM balance (+ received / - posted), IM and DV01; the dealt terms, accrued legs, next events, fixings and
 *   the VM history (each entry linked to its journal entry) are in the drawer.  It sits in its own horizontal scroller, so
 *   nothing is cut off when the page is narrow.  A failed load keeps the last answer (or none) and says it is retrying.
 * - FX_CCS_PLAN rev 2 (1.0.6.1): a cross-currency swap's legs are each fixed or floating; floating legs are EUR ESTR / USD SOFR only
 *   (compounded in arrears, quarterly, ACT/360, 2 business-day payment delay; any other currency says why not), both legs float or both
 *   are fixed.  The float / float form takes the tenor in 3-month steps, an optional spread on the EUR leg (blank = the dealer's quote), the
 *   inception conversion ("sell the EUR received at the start for USD") and the settlement funding ("buy each EUR obligation with USD two
 *   business days before"); its preview shows the deal FX rate with its source and time, the legs, the spread lines (the basis level
 *   ASSUMED, the par spread with its two parts, the half-width, the dealt spread), the schedule with its payment dates, both exchanges, the
 *   conversion, the net start flows, the EUR obligations and how each is funded, IM, the CSA, the limits and the labels.  The drawers show a
 *   contract's legs and fixings, its true-ups, its linked deals (the conversion, the settlement-funding purchases), its confirmation and an FX
 *   forward's pricing record - all the server's words.
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
  /* P8B-12: what a cross-currency swap takes - USD on one leg, one of these on the other (the currencies with a free policy-rate series) */
  var XCCY_CCYS = [['USD', 'US dollar'], ['EUR', 'euro'], ['GBP', 'pound sterling'], ['JPY', 'yen'], ['CHF', 'Swiss franc'], ['CAD', 'Canadian dollar'],
    ['AUD', 'Australian dollar']];
  var FIXING_LABEL = {
    TERM: 'TERM - SOFR fixed at each reset (latest print on or before the reset date)',
    COMPOUND_ARREARS: 'SOFR compounded daily in arrears (each night at its own print)'
  };
  var DEFAULTS = {
    IRS: { notional: '10,000,000.00', side: 'PAY_FIXED', tenor_months: '24', fixed_rate: '', fixed_basis: '30/360', float_months: '3', fixing: 'TERM' },
    TRS: { underlying_id: 'AAPL', units: '10000', receiver: 'true', reset_months: '1', tenor_months: '12' },
    XCCY: { pay_ccy: 'EUR', recv_ccy: 'USD', pay_notional: '10,000,000.00', recv_notional: '', pay_rate: '', recv_rate: '', pay_basis: '30/360',
            recv_basis: '30/360', months: '24', mtm_reset: 'true', pay_leg: 'fixed', recv_leg: 'fixed', eur_spread: '', start_conversion: 'false', sfi: 'false' }
  };
  /* FX_CCS_PLAN D4: the floating legs offered (an overnight index compounded in arrears) and why the others are not */
  var FLOAT_INDEX = { EUR: 'ESTR', USD: 'SOFR' };
  var FLOAT_MONTHS = (function () { var out = []; for (var m = 3; m <= 120; m += 3) { out.push(m); } return out; })();
  function floatWhyNot(ccy) {
    if (ccy === 'GBP') { return 'a floating GBP leg (SONIA) is not in this build yet: the SONIA index is stored and the GBP leg is the next currency (1.0.6.2)'; }
    return 'a floating ' + ccy + ' leg is not offered: no daily overnight rate is sourced for ' + ccy + ' in this terminal (its leg is fixed only)';
  }
  function floatLegs(v) { return v.pay_leg === 'float' || v.recv_leg === 'float'; }
  /* a signed spread in bp with at most 4 decimals ("0.1", "-0.5"); null when blank, false when unreadable */
  function bpText(s) {
    s = String(s == null ? '' : s).trim().replace(/\s*bps?$/i, '');
    if (!s) { return null; }
    return /^[-+]?\d+(\.\d{1,4})?$/.test(s) ? s.replace(/^\+/, '') : false;
  }
  var PREVIEW_LABEL = { IRS: 'Preview (par rate, cost of dealing, DV01, IM)', TRS: 'Preview (reset price, notional, financing, IM)',
                        XCCY: 'Preview (deal FX, leg quotes, exchanges, IM)' };
  var NOTE_KEYS = ['SWAP_MARK', 'SWAP_VM', 'SWAP_RESET', 'ACCRUAL', 'RATE_LAG', 'ELIGIBILITY'];
  var REFRESH_MS = 30000;
  var RETRY_MS = [2000, 4000, 8000, 16000, 30000];
  var KICKS = ['accounts', 'positions', 'account_detail', 'books', 'attention'];
  var COLS = 7;                               /* the contracts table (P8R1-05: notional over maturity, VM balance over IM - it fits with the dock open) */
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

  /* P8B-10: a contract event in trader words, never its internal code (next events, the fixings / exchanges table, the schedules) */
  var EVENT_WORDS = { PAY: 'payment', RESET: 'reset', FIX: 'fixing', MATURITY: 'maturity', FX_RESET: 'FX reset', START: 'start exchange',
    NOTIONAL_START: 'start exchange', NOTIONAL_END: 'maturity exchange', END: 'maturity exchange', EXPIRY: 'expiry', DELIVERY: 'delivery',
    PREMIUM: 'premium settlement', COUPON: 'coupon', CREDIT_EVENT: 'credit event', UPFRONT: 'initial payment', LAST_TRADE: 'last trading day',
    ROLL_BY: 'roll or close by', UNAVAILABLE: 'unavailable' };
  function eventWords(code) {
    var c = String(code == null ? '' : code);
    return EVENT_WORDS[c] || (T.codeWords ? T.codeWords(c) : c.replace(/_/g, ' ').toLowerCase());
  }
  /* next_events -> one line: "payment 2027-04-20 (pay fixed 200,500.00, receive float 195,650.00) . maturity 2028-10-20" */
  function eventsText(list) {
    if (!list || !list.length) { return ''; }
    return list.map(function (e) {
      var s = eventWords(e.event) + ' ' + (e.date || '');
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
    var s = String(side || '');
    /* a CDX row's side_text is a whole sentence (side, notional, CS01) for the Portfolio; a table row shows the side only - the notional
       and the CS01 have their own columns */
    if (row && row.kind === 'CDX') { return (s || row.side) === 'SELL_PROTECTION' ? 'sell protection (long credit)' : 'buy protection (short credit)'; }
    if (row && row.side_text) { return row.side_text; }
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
    if (c.kind === 'XCCY') {                      /* P8B-12: the market pair (EUR/USD, USD/JPY) whichever way the legs go; the side column says who pays what */
      var pr = String(t.deal_pair || d.deal_pair || '');
      if (/^[A-Z]{6}$/.test(pr)) { return 'XCCY ' + pr.slice(0, 3) + '/' + pr.slice(3) + (isFloat(c) ? ' float' : ''); }
      return 'XCCY ' + (d.recv_ccy || t.recv_ccy || '') + '/' + (d.pay_ccy || t.pay_ccy || '');
    }
    if (c.kind === 'CDX' && (d.index_name || d.index || t.index)) {      /* CDX NA.IG S47 */
      return String(d.index_name || ('CDX.NA.' + (d.index || t.index))).replace(/^CDX\./, 'CDX ') + (d.series || t.series ? ' S' + (d.series || t.series) : '');
    }
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
      if (isFloat(c)) {                           /* FX_CCS_PLAN D4: the float / float legs by their indices and the dealt spread */
        return (d.side_text || ('receive ' + (r.currency || '') + ' ' + (r.index || '') + ' / pay ' + (p.currency || '') + ' ' + (p.index || ''))) + ' . deal FX ' + px(d.deal_rate);
      }
      return 'receive ' + (r.currency || '') + ' ' + fmtPct(r.rate) + ' / pay ' + (p.currency || '') + ' ' + fmtPct(p.rate) + ' . deal FX ' + px(d.deal_rate);
    }
    if (c.kind === 'IRS') { return (c.fixed_rate_pct || fmtPct(c.fixed_rate)) + ' fixed' + (c.par_rate_dealt ? ' (model par ' + fmtPct(c.par_rate_dealt) + ' at dealing)' : ''); }
    /* OTC-18: an OTC option / forward reads as the deal it is (buy EUR 1,000,000.00 vs USD at 1.120032, value ...), with the forward at the deal */
    if (d.side_text) { return d.side_text + (d.forward_at_deal ? ' . model forward at the deal ' + d.forward_at_deal : ''); }
    return (d.terms_text || d.summary || '') || 'n/a';
  }
  function isFloat(c) { var d = (c && c.detail) || {}, t = (c && c.terms) || {}; return d.variant === 'FLOAT_FLOAT' || t.variant === 'FLOAT_FLOAT'; }
  /* a confirmation block as the server words it (FX_CCS_PLAN K5 / 4.1 / 10.7): never "dealer-confirmed" - a simulated confirmation */
  function confRows(conf) {
    var rows = [];
    if (!conf || typeof conf !== 'object') { return rows; }
    if (conf.words) { rows.push(['confirmation', conf.words]); }
    var pt = conf.parties || {};
    if (pt.fund || pt.counterparty) { rows.push(['parties', [pt.fund, pt.counterparty].filter(Boolean).join(' . ')]); }
    var ds = ['trade_date', 'spot_date', 'start_date', 'value_date', 'termination_date', 'maturity_date'].filter(function (k) { return conf[k]; })
      .map(function (k) { return k.replace(/_/g, ' ') + ' ' + conf[k]; });
    if (ds.length) { rows.push(['dates', ds.join(' . ')]); }
    /* V1061-05: the notionals served as amounts ({eur: "2000000.00", usd: "2229000.00"}) -> "EUR 2,000,000.00 / USD 2,229,000.00" */
    var nt = conf.notionals;
    if (nt && typeof nt === 'object') {
      var nl = Object.keys(nt).filter(function (c) { return nt[c] != null && nt[c] !== '' && typeof nt[c] !== 'object'; })
        .map(function (c) { return c.toUpperCase() + ' ' + amt(nt[c]); });
      if (nl.length) { rows.push(['notionals', nl.join(' / ')]); }
    }
    ['deal', 'rate', 'csa', 'im', 'settlement_funding'].forEach(function (k) {
      var x = conf[k];
      if (x == null || x === '' || typeof x === 'object') { return; }
      /* V1061-06: an amount served as a bare decimal string (a swap's IM "44600.00") reads like the rest of the drawer: USD 44,600.00 */
      var v = k === 'im' && /^-?\d+(\.\d+)?$/.test(String(x).trim()) ? 'USD ' + amt(x) : String(x);
      rows.push([k === 'csa' ? 'CSA' : (k === 'im' ? 'IM' : k.replace(/_/g, ' ')), v]);
    });
    (conf.settlement_instructions || []).forEach(function (s, i) {
      rows.push([i ? '' : 'settlement instructions', typeof s === 'string' ? s : (s.words || s.text || s.what || '')]);
    });
    if ((conf.labels || []).length) { rows.push(['labels', conf.labels.join(' . ')]); }
    return rows;
  }
  /* the deals linked to a contract (the inception conversion, the settlement-funding purchases, the delivery) */
  function linkedText(list) {
    return (list || []).map(function (x) {
      if (typeof x === 'string') { return x; }
      return [x.deal_id || x.id || '', x.words || x.what || x.text || (x.kind ? String(x.kind).toLowerCase() : ''), x.value_date ? 'value ' + x.value_date : '', x.status || '']
        .filter(Boolean).join(' . ');
    }).join(' ; ');
  }
  /* FX_CCS_PLAN 3.2: an FX forward's pricing record in a few lines (the full record is the server's) */
  function recordRows(rec) {
    var rows = [];
    if (!rec || typeof rec !== 'object') { return rows; }
    var f = rec.forward || {}, sp = rec.spot || {}, dl = rec.dealer || {};
    rows.push(['priced', (rec.method || '') + (rec.model_version ? ' (' + rec.model_version + ')' : '')]);
    rows.push(['spot', (sp.mid || '') + ' ' + (sp.basis || '') + (sp.print && sp.print.mid != null ? ' . print ' + sp.print.mid + ' (' + (sp.diff_pips || '0') + ' pips)' : '') +
      ' . spot date ' + (rec.spot_date || '') + ' . value date ' + (rec.value_date || '') + ' (' + (rec.days || '') + ' days)']);
    Object.keys(rec.curves || {}).forEach(function (c) { var cv = rec.curves[c] || {}; rows.push([c + ' curve', (cv.kind || '') + ' . ' + (cv.source || '') + ' of ' + (cv.as_of || '')]); });
    if (f.arithmetic) { rows.push(['outright', f.arithmetic + ' (' + (f.points || '') + ' pips)']); }
    if (dl.rule) { rows.push(['dealt', dl.rule]); }
    if ((rec.labels || []).length) { rows.push(['labels', rec.labels.join(' . ')]); }
    return rows;
  }
  /* FINSIM1 (P4): the as-of badge of a contract booked at the owner's instruction (its source label as the title) */
  function asofBadge(c) {
    if (!c || !(c.asof || c.booking === 'AS_OF')) { return null; }
    return T.h('span', { class: 'badge-asof', title: c.source_label || 'as-of booking' }, 'AS-OF');
  }
  function modelBadge() {
    return T.h('span', { class: 'badge-model', title: 'a model valuation from observed inputs - never a dealer-confirmed price' }, 'MODEL');
  }
  /* PORTFOLIO-06: the contract against the market now, per kind (C13 position fields) */
  function nowText(c) {
    var f = c.position || {}, x = f.extra || {};
    if (f.entry == null && f.current == null) { return null; }
    if (c.kind === 'TRS') {
      return (x.underlying_id || '') + ' ' + (f.current == null ? 'not observed now' : px(f.current)) + ' vs reset ' + px(f.entry) + ' on ' +
        (f.units == null ? '' : T.fmt.qty(f.units)) + ' reference shares';
    }
    if (c.kind === 'CDX') {
      return 'spread now ' + (f.current || 'not observed') + ' vs dealt ' + (f.entry || '') + ' . coupon ' + (x.coupon_bp || '') + ' bp' +
        (x.upfront ? ' . upfront ' + x.upfront + ' ' + money(x, 'upfront').replace('-', '') : '');
    }
    if (c.kind === 'IRS') {
      return 'par now ' + (f.current == null ? 'n/a' : fmtPct(f.current)) + (x.curve_spread_bp ? ' (with the contract curve spread)' : '') + ' vs fixed ' +
        fmtPct(f.entry) + ' . DV01 ' + (x.dv01 == null && x.dv01_c == null ? 'n/a' : money(x, 'dv01'));
    }
    return x.summary || null;
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
      if (has('pay_index') && has('recv_index')) {                       /* the float / float variant (FX_CCS_PLAN D4) */
        v.pay_leg = 'float'; v.recv_leg = 'float'; v.mtm_reset = 'false';
        var es = String(t.pay_ccy || '').toUpperCase() === 'USD' ? 'recv' : 'pay';
        if (has(es + '_spread_bp')) { v.eur_spread = String(t[es + '_spread_bp']); }
        v.start_conversion = bool(t.start_conversion) ? 'true' : 'false';
        v.sfi = t.settlement_funding && t.settlement_funding.EUR === 'BUY_WITH_USD' ? 'true' : 'false';
      }
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
    if (!/^[A-Z]{3}$/.test(payCcy)) { return { error: 'pay currency: pick one (USD on one leg; EUR, GBP, JPY, CHF, CAD or AUD on the other)', field: 'pay_ccy' }; }
    if (!/^[A-Z]{3}$/.test(recvCcy)) { return { error: 'receive currency: pick one', field: 'recv_ccy' }; }
    if (payCcy === recvCcy) { return { error: 'pay and receive currency must differ (USD on one leg)', field: 'recv_ccy' }; }
    if (payCcy !== 'USD' && recvCcy !== 'USD') { return { error: 'one leg must be USD (pay or receive)', field: 'recv_ccy' }; }
    var pnText = String(v.pay_notional || '').trim(), rnText = String(v.recv_notional || '').trim();
    var pn = pnText ? toCents(pnText) : null, rn = rnText ? toCents(rnText) : null;
    if (pnText && (pn == null || pn <= 0)) { return { error: 'pay notional: a positive amount with at most 2 decimals', field: 'pay_notional' }; }
    if (rnText && (rn == null || rn <= 0)) { return { error: 'receive notional: a positive amount with at most 2 decimals', field: 'recv_notional' }; }
    if (pn == null && rn == null) { return { error: 'give the pay or the receive notional (the other is struck at the deal FX rate)', field: 'pay_notional' }; }
    if (floatLegs(v)) {                                                    /* FX_CCS_PLAN D4: float / float, EUR ESTR / USD SOFR */
      if (v.pay_leg !== 'float' || v.recv_leg !== 'float') {
        return { error: 'fixed / floating cross-currency swaps are not in this build: make both legs floating (EUR ESTR / USD SOFR) or both fixed', field: v.pay_leg === 'float' ? 'recv_leg' : 'pay_leg' };
      }
      if (!FLOAT_INDEX[payCcy]) { return { error: floatWhyNot(payCcy), field: 'pay_leg' }; }
      if (!FLOAT_INDEX[recvCcy]) { return { error: floatWhyNot(recvCcy), field: 'recv_leg' }; }
      var fm = parseInt(v.months, 10);
      if (!(fm >= 3 && fm <= 120 && fm % 3 === 0)) { return { error: 'tenor: a multiple of 3 months, 3 to 120 (quarterly periods on both legs)', field: 'months' }; }
      var eurSide = payCcy === 'EUR' ? 'pay' : 'recv';
      var sp = bpText(v.eur_spread);
      if (sp === false) { return { error: 'spread on the EUR leg: basis points with at most 4 decimals (0.1, -0.5), or blank for the dealer\'s quote', field: 'eur_spread' }; }
      var ft = { pay_ccy: payCcy, recv_ccy: recvCcy, pay_notional_c: pn, recv_notional_c: rn, pay_index: FLOAT_INDEX[payCcy], recv_index: FLOAT_INDEX[recvCcy],
        pay_basis: 'ACT/360', recv_basis: 'ACT/360', months: fm, mtm_reset: false, start_conversion: eurSide === 'pay' && v.start_conversion === 'true' };
      if (sp != null) { ft[eurSide + '_spread_bp'] = sp; }
      if (v.sfi === 'true') { ft.settlement_funding = { EUR: 'BUY_WITH_USD' }; }
      return { terms: ft };
    }
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
      '.swapticket table.st-contracts td.st-side { white-space: normal; min-width: 120px; max-width: 170px; line-height: 1.3; padding-top: 3px; padding-bottom: 3px; text-align: left; }' +
      '.swapticket table.st-contracts td.stack { padding-top: 3px; padding-bottom: 3px; }' +
      '.swapticket table.st-contracts td .cl2 { color: var(--fg3); font-size: 10.5px; line-height: 1.25; }' +
      '.swapticket table.st-contracts td .st-act { margin-top: 2px; }' +
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
      '.swapticket .st-dock .kv div:nth-child(even) { white-space: normal; }' +
      '.st-rfqs { margin: 4px 0 12px; }' +
      '.st-rfqs h3, .swapticket .section h3 { font-size: 11px; margin: 10px 0 6px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; font-family: var(--mono); }' +
      '.st-rfqs h3 small, .swapticket .section h3 small { color: var(--fg3); font-weight: 400; margin-left: 8px; text-transform: none; letter-spacing: 0; }' +
      '.st-rfqs table.st-rfq-table td.st-side { white-space: normal; min-width: 130px; max-width: 230px; line-height: 1.3; }' +
      '.st-rfqs table.st-rfq-table td[data-valid] { white-space: normal; min-width: 80px; max-width: 120px; line-height: 1.3; }' +
      '.st-rfqs table.st-rfq-table td:last-child { white-space: nowrap; }' +
      '.st-rfqs .st-why { font-size: 10.5px; white-space: normal; max-width: 260px; }' +
      '.swapticket label.st-check { display: inline-flex; gap: 6px; align-items: center; justify-content: flex-start; text-align: left; white-space: normal; max-width: 520px;' +
      ' text-transform: none; letter-spacing: normal; font-size: 12px; color: var(--fg, inherit); font-family: inherit; }' +
      '.swapticket label.st-check input.bad { outline: 1px solid var(--down); }';
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

  /* the contract timeline as a small table: date, time, step, what happened, amount, journal entry */
  var STEP_WORDS = { REQUESTED: 'quote requested', QUOTED: 'quoted', ACCEPTED: 'accepted', BOOKED: 'booked', IM: 'initial margin', CONFIRMED: 'confirmed',
    CONFIRM_PENDING: 'confirmation due', UPFRONT: 'initial payment', FIX: 'fixing', VM: 'variation margin', PAY: 'payment', RESET: 'reset', EXCHANGE: 'exchange',
    CREDIT_EVENT: 'credit event', TERMINATED: 'terminated', MATURED: 'matured' };
  /* P8B-11 / P8R1-03: every row has one shape - the date column a date, the time column a clock time - and the two never make an instant
     that did not happen (the server sets them, swaps.contract_timeline):
     - a step recorded on its own day shows its New York date and clock time; one that posts entries dated the NEXT business day (recorded
       after the 17:00 ET cut-off) says that business date in its words;
     - a step recorded on a later day than the business date it belongs to (the as-of booking and its lifecycle entered at the import, a
       variation margin caught up the next morning) shows that business date "as of", with the time it was recorded in its words. */
  function stepTime(r) {
    if (r.asof) { return 'as of'; }                 /* the as-of booking's words say when it was entered; a later record's say when it was recorded */
    if (!r.ts) { return ''; }
    return nyTime(r.ts);
  }
  function stepWords(r) {
    var w = r.words || '';
    if (r.asof && r.recorded_ts) { return w + ' (recorded ' + (T.fmt && T.fmt.date ? T.fmt.date(r.recorded_ts) + ' ' : '') + nyTime(r.recorded_ts) + ' ET)'; }
    if (r.after_cutoff && r.business_date) { return w + ' (business date ' + r.business_date + ': posted after the 17:00 ET cut-off)'; }
    return w;
  }
  function timelineTable(rows) {
    ensureCss();
    if (!rows || !rows.length) { return T.h('div', { class: 'empty' }, 'nothing recorded yet'); }
    var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'date'), T.h('th', null, 'time (ET)'), T.h('th', null, 'step'),
      T.h('th', { class: 'l' }, 'what happened'), T.h('th', { class: 'num' }, 'amount'), T.h('th', null, 'entry'))));
    var body = T.h('tbody');
    rows.forEach(function (r) {
      body.appendChild(T.h('tr', { class: r.pending ? 'dim' : '' }, T.h('td', null, r.date || ''), T.h('td', null, stepTime(r)),
        T.h('td', null, STEP_WORDS[r.step] || String(r.step || '').toLowerCase()), T.h('td', { class: 'l', style: 'white-space:normal' }, stepWords(r)),
        T.h('td', { class: 'num' }, r.amount == null && r.amount_c == null ? '' : money(r, 'amount')), T.h('td', null, entryLink(r.entry_id))));
    });
    tbl.appendChild(body);
    return T.h('div', { class: 'st-wrap' }, tbl);
  }

  /* ---- C14 (FINSIM1 P4 task 8): OTC quote requests - shared by the swap, CDX and options / forwards tickets and the Derivatives page ---- */
  var RFQ_POLL_MS = 2000;                       /* while a request is open: the dealer answers on the next lifecycle tick (about 30 s) */
  var RFQ_IDLE_MS = 30000;
  var RFQ_RECENT_S = 180;                       /* a request closed in the last 3 minutes stays listed, so its outcome is seen */
  var QUOTE_SOURCE = 'indicative model quote, simulated dealer: a model price from observed inputs, never a real dealer\'s quote';
  var RFQ_STAGE = { REQUESTED: ['queued', 'AWAITING QUOTE'], QUOTED: ['working', 'QUOTED'], ACCEPTED: ['filled', 'BOOKED'], DECLINED: ['cancelled', 'DECLINED'],
    EXPIRED: ['expired', 'EXPIRED'] };
  function rfqRequest(account, kind, terms) {
    return T.api.post('/api/otc/rfqs', { account_id: account, kind: kind, terms: terms });
  }
  function nowSec() { return T.clock && T.clock.nowInt ? T.clock.nowInt() : Math.floor(Date.now() / 1000); }
  function validText(r) {
    if (r.status !== 'QUOTED' || r.expires_ts == null) { return ''; }
    var left = Number(r.expires_ts) - nowSec();
    return left > 0 ? left + ' s left (until ' + nyTime(r.expires_ts) + ' ET)' : 'lapsed at ' + nyTime(r.expires_ts) + ' ET';
  }
  /* the acceptance: the quote and what booking it posts (the IM, its funding, the cost of dealing), then Accept and book */
  function acceptDialog(r, after) {
    T.api.get('/api/otc/rfqs/' + encodeURIComponent(r.rfq_id)).then(function (d) {
      var pv = d.quote_preview || {};
      var f = pv.funding || {};
      var rows = [['contract', (d.label || '') + ' with ' + (d.dealer || 'the dealer')], ['side', d.side_words || ''], ['quote', T.h('b', null, d.quote_words || '')],
        ['source', QUOTE_SOURCE], ['valid', validText(d) || 'lapsed'], ['initial margin', money(pv, 'im') + (pv.im_rule ? ' (' + pv.im_rule + ')' : '')],
        ['funding', f.note || 'n/a'], ['cost of dealing', pv.cost_of_dealing == null && pv.cost_of_dealing_c == null ? 'none' : money(pv, 'cost_of_dealing')],
        ['dates', (pv.start_date || pv.trade_date || '') + ' to ' + (pv.maturity_date || pv.expiry || '')]];
      if (pv.curve_note) { rows.push(['curve', pv.curve_note]); }
      var err = T.h('div', { class: 'error', hidden: true });
      T.ui.modal({
        title: 'Accept ' + d.rfq_id, sticky: true,
        body: T.h('div', null, T.h('div', { class: 'hint' }, 'Booking at the quoted terms while the quote is valid; the contract then waits for the simulated confirmation at 17:05 ET.'),
          T.kv(rows), err),
        actions: [{ label: 'Cancel' }, { label: 'Accept and book', cls: 'primary', onClick: function (m, btn) {
          btn.disabled = true; err.hidden = true;
          T.api.post('/api/otc/rfqs/' + encodeURIComponent(d.rfq_id) + '/accept', {}).then(function (res) {
            m.close();
            var c = res.contract || {};
            var t = res.im_transfer || {};
            var text = 'Booked ' + (res.contract_id || '') + ' at the quote (' + (d.quote_words || '') + ') . IM ' + money(t, 'amount') + ' . ' +
              (c.confirm_label || 'awaiting the simulated confirmation at 17:05 ET');
            T.ui.toast(text, 'ok', 8000);
            kick();
            if (after) { after(res, text); }
          }, function (e) { btn.disabled = false; err.textContent = describe(e); err.title = (e && e.code) || ''; err.hidden = false; if (after) { after(null, null); } });
        } }]
      });
    }, function (e) { T.ui.toast(d0(r) + ': ' + describe(e), 'err', 6000); });
  }
  function d0(r) { return (r && r.rfq_id) || 'the quote request'; }
  function declineRfq(r, after) {
    T.api.post('/api/otc/rfqs/' + encodeURIComponent(r.rfq_id) + '/decline', {}).then(function () { T.ui.toast(r.rfq_id + ' declined', 'ok', 3000); if (after) { after(); } },
      function (e) { T.ui.toast(r.rfq_id + ': ' + describe(e), 'err', 6000); if (after) { after(); } });
  }
  /* the quote requests of an account (opts.account_id) or of the selected book, open ones first, with Accept / Decline and a countdown;
     polled every 2 s while one is open.  opts: {account_id, showAccount, hideEmpty, onBooked} -> {refresh, unmount} */
  function mountRfqs(host, opts) {
    opts = opts || {};
    ensureCss();                                /* P8R1-05: its table wraps the quote words wherever it is mounted (the Derivatives page too) */
    var st = { rows: null, dead: false, timer: null, tick: null, error: null };
    var box = T.h('div', { class: 'st-rfqs' });
    T.clear(host).appendChild(box);
    function alive() { return !st.dead && connected(box); }
    function schedule(ms) {
      if (st.timer) { clearTimeout(st.timer); }
      st.timer = setTimeout(function () { st.timer = null; if (alive()) { load(); } else { stop(); } }, ms);
    }
    function stop() {
      st.dead = true;
      if (st.timer) { clearTimeout(st.timer); st.timer = null; }
      if (st.tick) { clearInterval(st.tick); st.tick = null; }
    }
    function load() {
      var q = { status: 'all', limit: 20 };
      if (opts.account_id) { q.account_id = opts.account_id; }
      return T.api.get('/api/otc/rfqs', q).then(function (rows) {
        if (!alive()) { return; }
        var now = nowSec();
        st.error = null;
        st.rows = (Array.isArray(rows) ? rows : []).filter(function (r) { return r.status === 'REQUESTED' || r.status === 'QUOTED' || (r.closed_ts && now - Number(r.closed_ts) < RFQ_RECENT_S); });
        render();
        var open = st.rows.some(function (r) { return r.status === 'REQUESTED' || r.status === 'QUOTED'; });
        schedule(open ? RFQ_POLL_MS : RFQ_IDLE_MS);
      }, function (e) {
        if (!alive()) { return; }
        st.error = describe(e);
        render();
        schedule(RFQ_IDLE_MS);
      });
    }
    function after(res) { load(); if (res && opts.onBooked) { try { opts.onBooked(res); } catch (x) { console.error('rfq onBooked', x); } } }
    function action(r) {
      var cell = T.h('span');
      if (r.status === 'QUOTED' && r.actions && r.actions.accept && Number(r.expires_ts) > nowSec()) {
        cell.appendChild(T.h('button', { class: 'btn small primary', title: 'book at the quoted terms', onclick: function () { acceptDialog(r, after); } }, 'Accept ...'));
        cell.appendChild(document.createTextNode(' '));
      }
      if (r.status === 'REQUESTED' || r.status === 'QUOTED') {
        cell.appendChild(T.h('button', { class: 'btn small', title: 'close this request; nothing is booked', onclick: function () { declineRfq(r, after); } }, 'Decline'));
      } else if (r.status === 'EXPIRED' && r.terms) {
        cell.appendChild(T.h('button', { class: 'btn small', title: 'ask the dealer again with the same terms', onclick: function () {
          rfqRequest(r.account_id, r.kind, r.terms).then(function (x) { T.ui.toast('Quote requested again (' + x.rfq_id + ')', 'ok', 4000); load(); },
            function (e) { T.ui.toast(describe(e), 'err', 6000); });
        } }, 'Request again'));
      } else if (r.status === 'ACCEPTED' && r.contract_id) {
        cell.appendChild(T.h('span', { class: 'muted' }, 'booked ' + r.contract_id));
      }
      return cell;
    }
    function render() {
      T.clear(box);
      var rows = st.rows || [];
      if (st.tick) { clearInterval(st.tick); st.tick = null; }
      if (!rows.length && !st.error) {
        box.hidden = !!opts.hideEmpty;
        if (!opts.hideEmpty) { box.appendChild(T.h('div', { class: 'empty' }, st.rows == null ? 'loading quote requests ...' : 'no open quote requests')); }
        return;
      }
      box.hidden = false;
      box.appendChild(T.h('h3', null, 'Quote requests', T.h('small', null, ' simulated dealer; a quote is good for 120 s')));
      if (st.error) { box.appendChild(T.h('div', { class: 'st-meta warn' }, 'quote requests could not be loaded - retrying: ' + st.error)); }
      var head = [T.h('th', { class: 'l' }, 'request'), T.h('th', { class: 'l' }, 'contract')];
      if (opts.showAccount) { head.push(T.h('th', { class: 'l' }, 'account')); }
      head.push(T.h('th', { class: 'l' }, 'side'), T.h('th', { class: 'l' }, 'status'), T.h('th', { class: 'l' }, 'quote'), T.h('th', { class: 'l' }, 'valid'), T.h('th', { class: 'l' }, ''));
      var tbl = T.h('table', { class: 'grid small st-rfq-table' }, T.h('thead', null, T.h('tr', null, head)));
      var body = T.h('tbody');
      rows.forEach(function (r) {
        var sg = RFQ_STAGE[r.status] || ['queued', r.status];
        var cells = [T.h('td', { class: 'l', title: 'requested ' + nyTime(r.requested_ts) + ' ET' }, r.rfq_id), T.h('td', { class: 'l st-side' }, r.label || r.kind_label || r.kind)];
        if (opts.showAccount) { cells.push(T.h('td', { class: 'l' }, T.accountName ? T.accountName(r.account_id) : r.account_id)); }
        var why = r.status === 'EXPIRED' || r.status === 'DECLINED' ? (r.reason || '') : (r.status_words || '');
        cells.push(T.h('td', { class: 'l' }, r.side_words || ''), T.h('td', { class: 'l', title: why }, T.stageChip(sg[0], sg[1], r.status_words || ''),
          r.status === 'EXPIRED' && r.reason ? T.h('div', { class: 'muted st-why' }, r.reason) : null),
          T.h('td', { class: 'l st-side', title: QUOTE_SOURCE }, r.quote_words || (r.status === 'REQUESTED' ? 'awaiting the dealer' : '')),
          T.h('td', { class: 'l', 'data-valid': r.rfq_id }, validText(r)), T.h('td', { class: 'l' }, action(r)));
        body.appendChild(T.h('tr', null, cells));
      });
      tbl.appendChild(body);
      box.appendChild(T.h('div', { class: 'st-wrap' }, tbl));
      if (rows.some(function (r) { return r.status === 'QUOTED'; })) {        /* the countdown, in place */
        st.tick = setInterval(function () {
          if (!alive()) { stop(); return; }
          rows.forEach(function (r) { var c = box.querySelector('[data-valid="' + r.rfq_id + '"]'); if (c) { c.textContent = validText(r); } });
        }, 1000);
      }
    }
    render();
    load();
    return { refresh: load, unmount: function () { stop(); T.clear(host); } };
  }

  /* ---- the ticket ----------------------------------------------------------------------------------------------------- */
  function mount(el, opts) {
    opts = opts || {};
    ensureCss();
    var account = opts.account_id || '';
    var showNotes = opts.notes !== false;
    var kept = account ? memo[account] : null;
    var state = { kind: kept ? kept.kind : 'IRS', values: kept ? kept.values : {}, previews: {}, error: null, errorField: null, contracts: null, loadError: null,
      loadedAt: null, retry: 0, retryAt: null, timer: null, dead: false, open: {}, loading: false, blocked: opts.blocked || null, result: null, seq: 0,
      timelines: {}, rfqH: null };
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
        if (cur && !cur.stale && !state.blocked && fundable) { onRequest(cur); }
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
      if (state.rfqH) { try { state.rfqH.unmount(); } catch (e) { console.error('rfqs unmount', e); } state.rfqH = null; }
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
      /* the quote requests and the contracts table stay visible under every sub-tab of the OTC desk (they are the .contracts block) */
      ui.contracts = T.h('div', { class: 'section contracts' }); root.appendChild(ui.contracts);
      ui.rfqBox = T.h('div'); ui.contracts.appendChild(ui.rfqBox);
      ui.contractsInner = T.h('div'); ui.contracts.appendChild(ui.contractsInner);
      if (showNotes && T.convNote) { root.appendChild(T.convNote(NOTE_KEYS)); }
      renderTabs(); renderForm(); renderError(); renderPreview(); renderContracts();
      if (account) { state.rfqH = mountRfqs(ui.rfqBox, { account_id: account, hideEmpty: true, onBooked: function () { load(); } }); }
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
    /* P8B-12: the currencies a cross-currency swap takes (one leg USD), picked from a list - never found out by refusal; a currency whose
       rate is not current (the desk's rates: CHF's free series stopped in 2024) is listed disabled with the reason */
    function ccySelect(name) {
      var s = T.h('select');
      var fl = floatLegs(vals());
      XCCY_CCYS.forEach(function (c) {
        var o;
        if (fl) {                                 /* FX_CCS_PLAN D4: a floating leg prices on its overnight index and term curve, not on a policy rate */
          var cv = (state.ccyCurves || {})[c[0]];
          o = opt(c[0], c[0] + ' - ' + c[1] + (!FLOAT_INDEX[c[0]] ? ' (fixed legs only)' : (cv && cv.ok === false ? ' (curve unavailable now)' : '')));
          if (cv && cv.ok === false) { o.title = cv.note || 'curve unavailable'; }
          s.appendChild(o);
          return;
        }
        var r = (state.ccyRates || {})[c[0]];
        var off = !!(r && r.ok === false);
        o = opt(c[0], c[0] + ' - ' + c[1] + (off ? ' (no current rate)' : ''));
        if (off) { o.disabled = true; o.title = r.note || 'no current rate'; }
        s.appendChild(o);
      });
      return bind(name, s);
    }
    function loadCcyRates() {
      if (state.ccyRates !== undefined || !account) { return; }
      if (typeof T.hasModule === 'function' && !T.hasModule('otc_options')) { state.ccyRates = {}; return; }   /* rule 5: no route of a module not bound */
      state.ccyRates = null;
      T.api.get('/api/otc/options/desk', { account_id: account }).then(function (d) {
        if (!alive()) { return; }
        state.ccyRates = (d && d.rates) || {};
        state.ccyCurves = (d && d.curves) || {};
        if (state.kind === 'XCCY') { renderForm(); }
      }, function () { state.ccyRates = {}; });
    }
    function legSelect(name) {
      var s = select(name, [['fixed', 'fixed rate'], ['float', 'floating (EUR ESTR / USD SOFR)']]);
      s.addEventListener('change', function () { renderForm(); });
      return s;
    }
    function check(name, label, title) {
      var cb = T.h('input', { type: 'checkbox', 'data-field': name });
      cb.checked = vals()[name] === 'true';
      cb.addEventListener('change', function () { vals()[name] = cb.checked ? 'true' : 'false'; touched(name); });
      if (state.errorField === name) { cb.classList.add('bad'); }
      return T.h('label', { class: 'st-check', title: title || null }, cb, ' ' + label);
    }
    function xccyFloatRows() {
      var v = vals();
      var payCcy = String(v.pay_ccy || '').toUpperCase(), recvCcy = String(v.recv_ccy || '').toUpperCase();
      var rows = [];
      var why = [];
      [[payCcy, 'pay'], [recvCcy, 'receive']].forEach(function (x) { if (!FLOAT_INDEX[x[0]]) { why.push(floatWhyNot(x[0])); } });
      if (v.pay_leg !== v.recv_leg) { why.push('fixed / floating swaps are not in this build: both legs float (EUR ESTR / USD SOFR) or both are fixed'); }
      if (why.length) { rows.push(T.field('floating legs', T.h('span', { class: 'st-blocked' }, why.join('; ')))); }
      rows.push(T.field('spread on the EUR leg (bp)', text('eur_spread', 120, 'blank = quote'),
        'blank = the dealer\'s quote: its par spread for this swap + its half-width, rounded on the 0.1 bp grid against you; a better spread is refused'));
      rows.push(T.field('tenor', select('months', FLOAT_MONTHS.map(function (m) { return [String(m), m + ' months' + (m % 12 === 0 ? ' (' + (m / 12) + 'y)' : '')]; })),
        'quarterly periods on both legs from the spot date (modified following on TARGET2 + New York); each coupon paid 2 business days after its period end'));
      rows.push(T.field('legs', T.h('span', { class: 'muted' }, 'ESTR / SOFR compounded in arrears (the published indices), ACT/360, no lookback; both exchanges at the deal FX rate, no notional reset')));
      if (payCcy === 'EUR') {
        rows.push(T.field('inception conversion', check('start_conversion', 'sell the EUR received at the start for USD',
          'an audited FX desk spot deal at the desk\'s bid, value the start date, settled with the start exchange (payment versus payment): the position is then short EUR / long USD')));
      }
      rows.push(T.field('settlement funding', check('sfi', 'buy each EUR obligation with USD two business days before',
        'coupons and the final exchange: an audited FX desk spot deal for each, in Completed Trades - nothing converts on its own; the funding gap shows in the preview and on the Risk page')));
      return rows;
    }
    function xccyForm() {
      loadCcyRates();
      var fl = floatLegs(vals());
      var head = [T.field('pay currency', ccySelect('pay_ccy'), 'we BORROW this currency: its notional is received at the start and repaid at maturity; we pay its coupons'),
        T.field('receive currency', ccySelect('recv_ccy'), 'we LEND this currency: its notional is paid at the start (the spot date) and comes back at maturity; we receive its coupons'),
        T.field('pay leg', legSelect('pay_leg'), 'fixed, or floating on the overnight index of its currency (EUR ESTR, USD SOFR)'),
        T.field('receive leg', legSelect('recv_leg')),
        T.field('pay notional', text('pay_notional', 160, 'blank = at the deal FX rate'), 'give one notional; the other is struck at the observed deal FX rate'),
        T.field('receive notional', text('recv_notional', 160, 'blank = at the deal FX rate'))];
      if (fl) { return T.form(head.concat(xccyFloatRows())); }
      return T.form(head.concat([
        T.field('pay rate (%)', text('pay_rate', 130, 'blank = quote'), 'blank = the dealer quote (model par + half-width); a lower rate is refused'),
        T.field('receive rate (%)', text('recv_rate', 130, 'blank = quote'), 'blank = the dealer quote (model par - half-width); a higher rate is refused'),
        T.field('pay leg basis', basis('pay_basis')), T.field('receive leg basis', basis('recv_basis')),
        T.field('tenor', select('months', [['12', '12 months'], ['24', '24 months'], ['36', '36 months'], ['60', '60 months']]), 'a multiple of 6 months: semi-annual coupons on both legs'),
        T.field('notional reset', select('mtm_reset', [['true', 'mark-to-market (USD notional reset)'], ['false', 'fixed notionals']]),
          'mark-to-market: the USD notional is re-struck at each period start at the 17:00 ET FX close, the difference settled in cash'),
        T.field('legs', T.h('span', { class: 'muted' }, 'fixed / fixed: both legs pay a fixed rate. This version also offers the EUR/USD float / float swap (EUR on compounded ESTR' +
          ' plus the dealer\'s spread against USD on compounded SOFR): choose floating on both legs'),
          'not offered: floating GBP legs (SONIA is the next currency), floating JPY, CHF, CAD and AUD legs (no daily overnight rate source in this terminal),' +
          ' fixed / floating swaps and resetting (mark-to-market) float / float swaps')]));
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
        if (p && p.refused) {                     /* V104R2-BW-05: a refused preview answers 200 with its reason (no 4xx, no console error) */
          done(); state.previews[kind] = null;
          if (kind !== state.kind) { return; }
          state.error = p.message || 'the dealer cannot price these terms';
          ui.error.title = p.code || '';
          state.errorField = fieldOfError(kind, { code: p.code, details: p.details || {} }, vals());
          renderError(); renderPreview();
          if (state.errorField) { renderForm(); focusField(state.errorField); }
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
      var box = k === 'TRS' ? trsPreviewBlock(p) : (k === 'XCCY' ? (p.variant === 'FLOAT_FLOAT' ? xccyFloatBlock(p) : xccyPreviewBlock(p)) : irsPreviewBlock(p));
      if (pv.stale) { box.insertBefore(T.h('div', { class: 'st-stale' }, 'The terms were changed after this preview: preview again before booking.'), box.firstChild.nextSibling); }
      var f = p.funding || {};
      var fundable = f.ok !== false && !(p.im_funding && p.im_funding.ok === false);
      box.appendChild(T.h('div', { class: 'hint' }, 'Fees: ' + (p.fees || 'none beyond the half-width (disclosed)') + '. This preview is indicative: ask the dealer for a quote,' +
        ' then accept or decline it - nothing is booked before you accept.'));
      /* P8B-12: name what cannot be funded - the start exchange of a cross-currency swap (with how to fund it) or the initial margin */
      var unfunded = '';
      if (!fundable) {
        if (k === 'XCCY' && f.ok === false && f.currency && f.currency !== 'USD') {
          unfunded = 'the start exchange cannot be funded: ' + (f.hint || (f.currency + ' ' + money(f, 'need') + ' needed by ' + (f.start_date || p.start_date || 'the start date')));
        } else if (k === 'XCCY' && f.ok === false) {
          unfunded = 'the initial margin and the USD lent at the start cannot be funded (USD ' + money(f, 'need') + ' needed)';
        } else if (p.im_funding && p.im_funding.ok === false) { unfunded = 'the initial margin cannot be funded'; }
        else { unfunded = k === 'XCCY' ? 'the initial margin and the USD lent at the start cannot be funded' : 'the initial margin cannot be funded'; }
      }
      var why = state.blocked ? state.blocked : (pv.stale ? 'preview again first' : unfunded);
      ui.requestBtn = T.h('button', { class: 'btn primary', disabled: !!why, title: why || ('asks the prime broker\'s simulated dealer for a quote (it answers within about 30 s; the quote is good for 120 s)' +
        (T.keys.paren ? T.keys.paren('ticket.submit') : '')), onclick: function () { onRequest(pv); } }, 'Request quote');
      box.appendChild(T.h('div', { class: 'actions' }, ui.requestBtn, why ? T.h('span', { class: 'hint' }, ' ' + why) : null));
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
          body.appendChild(T.h('tr', null, T.h('td', null, r.date), T.h('td', null, eventWords(r.event)), T.h('td', { class: 'num' }, r.nights == null ? '' : String(r.nights)),
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
        ['start exchange', (se.date || p.start_date || '') + ' (the spot date): we pay ' + (wp.currency || '') + ' ' + money(wp, 'amount') + ' (lent) and receive ' +
          (wr.currency || '') + ' ' + money(wr, 'amount') + ' (borrowed)', (se.when ? se.when + '; ' : '') + 'reversed at maturity at the same notionals'],
        ['notional reset', p.mtm_reset ? 'mark-to-market: USD notional re-struck on ' + (p.fx_reset_dates || []).join(', ') : 'none (fixed notionals)'],
        ['cost of dealing (mark0)', money(p, 'mark0'), 'the legs dealt at the dealer quotes vs the model par rates'],
        ['DV01 (USD curve, +1 bp)', money(p, 'dv01') + ' . foreign leg ' + money(p, 'dv01_foreign'), p.dv01_note || ''],
        ['initial margin', money(p, 'im') + (p.im_pct ? ' (' + p.im_pct + ' of the USD notional)' : ''), p.im_rule || ''],
        ['start funding', fundingText(p.funding), (p.funding && ((p.funding.note || '') + '. ' + (p.funding.rule || ''))) || ''],
        p.im_funding ? ['IM funding', fundingText(p.im_funding), p.im_funding.note || ''] : null,
        ['variation margin', vmText(p)],
        ['dates', (p.trade_date ? 'traded ' + p.trade_date + ' . ' : '') + 'start ' + (p.start_date || '') + ' to ' + (p.maturity_date || '') + ' . half-width ' +
          (p.half_width_bp || '') + ' bp per leg']
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
          body.appendChild(T.h('tr', null, T.h('td', null, r.date), T.h('td', null, eventWords(r.event)), T.h('td', { class: 'num' }, (r.receive_ccy || '') + ' ' + money(r, 'receive')),
            T.h('td', { class: 'num' }, (r.pay_ccy || '') + ' ' + money(r, 'pay')), T.h('td', { class: 'l' }, r.indicative ? 'USD leg indicative until the FX reset of ' + (r.fx_reset || '') : '')));
        });
        tbl.appendChild(body);
        box.appendChild(T.h('div', { class: 'st-wrap' }, tbl));
      }
      return box;
    }
    /* FX_CCS_PLAN 3.3: the float / float preview - every line the server's words */
    function xccyFloatBlock(p) {
      var box = T.h('div', { class: 'section preview' }, T.h('h2', null, 'Preview . cross-currency swap, float / float', T.h('small', null, 'MODEL on PROXY curves; the basis level ASSUMED')));
      var legs = p.legs || {}, lp = legs.pay || {}, lr = legs.receive || {};
      var sp = p.spread || {}, conv = p.start_conversion || {}, nsf = p.net_start_flows || {}, ex = p.exchanges || {};
      var kv = [
        ['side', p.side_text || sideText(p.side, p)],
        ['deal FX rate (observed)', (p.deal_rate_text || px(p.deal_rate)) + (p.deal_rate_source ? ' . ' + p.deal_rate_source : ''),
          'both exchanges at this rate: a fresh executable print, never the ECB reference rate or a stale print'],
        ['notionals', (lp.currency || '') + ' ' + money(lp, 'notional') + ' / ' + (lr.currency || '') + ' ' + money(lr, 'notional')],
        ['dates', 'traded ' + (p.trade_date || '') + ' . start ' + (p.start_date || '') + ' (the spot date) . termination ' + (p.termination_date || '') +
          ' . last payment and final exchange ' + (p.maturity_date || ''), (p.calendars && p.calendars.schedule ? p.calendars.schedule + ', ' + (p.calendars.roll || '') : '')],
        ['dealt spread', sp.dealt_bp == null ? 'n/a' : ((Number(sp.dealt_bp) > 0 ? '+' : '') + sp.dealt_bp + ' bp on the ' + (sp.on_leg || 'EUR') + ' leg' + (sp.client_bp != null ? ' (yours: ' + sp.client_bp + ' bp)' : ''))]];
      (sp.lines || []).forEach(function (l, i) { kv.push([i ? '' : 'how it is set', l]); });
      kv.push(['cost of dealing', money(p, 'mark0') + ' (the half-width and the rounding: never a day-one gain)', p.pv_zero_parts ? 'PV at a 0 spread ' + money(p, 'pv_zero') +
        ': final exchange timing ' + money(p.pv_zero_parts, 'final_exchange_timing') + ', coupon delays ' + money(p.pv_zero_parts, 'coupon_delays') : '']);
      var r01 = p.rate01 || {};
      /* V1061R2-03: each currency's rates +1 bp money-formatted like the basis and spread figures beside it */
      kv.push(['sensitivities (USD)', 'basis +1 bp ' + money(p, 'basis01') + Object.keys(r01).map(function (c) { return ' . ' + c + ' rates +1 bp ' + T.fmt.money(r01[c], 2); }).join('') +
        (p.eur_ois_spread01 != null ? ' . EUR OIS spread +1 bp ' + money(p, 'eur_ois_spread01') : ''), 'each by re-pricing']);
      kv.push(['initial margin', money(p, 'im') + (p.im_pct ? ' (' + p.im_pct + ' of the USD notional)' : ''), p.im_rule || '']);
      kv.push(['funding', p.funding ? (p.funding.note || fundingText(p.funding)) : 'n/a']);
      kv.push(['variation margin', p.csa_note || vmText(p)]);
      if (conv.on) { kv.push(['inception conversion', conv.words || ('sell ' + ((conv.sell || {}).currency || '') + ' at ' + (conv.rate || ''))]); }
      if (nsf.words) { kv.push(['net start flows', nsf.words]); }
      if (p.settlement_funding_words) { kv.push(['settlement funding', p.settlement_funding_words]); }
      if (p.limits) { kv.push(['notional limits', p.limits.words || p.limits.text || 'none configured']); }
      box.appendChild(T.kv(kv));
      /* one line per leg (the ticket column is narrow): notional, index and spread, day count and compounding, the print now, period 1 */
      var legRows = [];
      [lp, lr].forEach(function (l) {
        if (!l.currency) { return; }
        var cp = l.current_print || {};
        var sp = l.spread_bp == null || Number(l.spread_bp) === 0 ? ' flat' : ' ' + (Number(l.spread_bp) > 0 ? '+ ' : '- ') + String(l.spread_bp).replace(/^-/, '') + ' bp';
        legRows.push([(l.side || '') + ' leg', l.currency + ' ' + money(l, 'notional') + ' on ' + (l.index || '') + sp + ' . ' + (l.day_count || '') + ' . ' + (l.compounding || l.index_name || '') +
          ' . print now ' + (cp.value_pct ? cp.value_pct + ' % (' + (cp.obs_date || '') + (cp.source ? ', ' + cp.source : '') + ')' : 'n/a') +
          ' . period 1 (indicative) ' + l.currency + ' ' + money(l, 'period1_indicative'), l.role || '']);
      });
      box.appendChild(T.kv(legRows));
      ['initial', 'final'].forEach(function (k) { if (ex[k] && ex[k].words) { box.appendChild(T.h('div', { class: 'hint' }, (k === 'initial' ? 'Initial exchange: ' : 'Final exchange: ') + ex[k].words)); } });
      var rows = p.schedule || [];
      if (rows.length) {
        var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'period'), T.h('th', null, 'start'), T.h('th', null, 'end'), T.h('th', null, 'payment'),
          T.h('th', { class: 'num' }, 'days'), T.h('th', { class: 'num' }, 'EUR (indicative)'), T.h('th', { class: 'num' }, 'USD (indicative)'))));
        var body = T.h('tbody');
        rows.forEach(function (r) {
          body.appendChild(T.h('tr', null, T.h('td', null, String(r.period || '')), T.h('td', null, r.start || r.s || ''), T.h('td', null, r.end || r.e || ''), T.h('td', null, r.pay_date || r.p || ''),
            T.h('td', { class: 'num' }, String(r.days == null ? '' : r.days)), T.h('td', { class: 'num' }, money(r, 'eur_indicative')), T.h('td', { class: 'num' }, money(r, 'usd_indicative'))));
        });
        tbl.appendChild(body);
        box.appendChild(T.h('div', { class: 'st-wrap' }, tbl));
        box.appendChild(T.h('div', { class: 'hint' }, 'Coupons are indicative until each period ends: the published compounded indices decide the payment (never an estimate).'));
      }
      var ob = p.eur_obligations || [];
      if (ob.length) {
        box.appendChild(T.h('div', { class: 'hint' }, 'EUR obligations: ' + ob.map(function (o) { return o.date + ' EUR ' + money(o, 'amount') + ' ' + (o.what || '') + (o.funded_by ? ' - ' + o.funded_by : ''); }).join(' . ')));
      }
      if ((p.labels || []).length) { box.appendChild(T.h('div', { class: 'hint' }, 'Labels: ' + p.labels.join(' . '))); }
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
      if (p.curve_note) { kv.push(['curve', p.curve_note]); }       /* V104R2-OTC-06: the Treasury par curve stands in for the SOFR swap curve - disclosed */
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

    /* ---- the quote request (C14): the preview's terms go to the prime broker's simulated dealer; its quote is accepted or declined in
       the Quote requests block above the contracts (it answers on the next lifecycle tick, about 30 s; a quote is good for 120 s) ---- */
    function onRequest(pv) {
      if (!pv || pv.stale || state.blocked) { return; }
      var p = pv.p || {};
      var kind = p.kind || pv.kind;
      if (ui.requestBtn) { ui.requestBtn.disabled = true; ui.requestBtn.textContent = 'Requesting ...'; }
      rfqRequest(account, kind, pv.terms).then(function (r) {
        if (ui.requestBtn) { ui.requestBtn.disabled = false; ui.requestBtn.textContent = 'Request quote'; }
        var text = 'Quote requested from ' + (p.broker || 'the prime broker') + ' (' + (r.rfq_id || '') + '): the simulated dealer answers within about 30 s - accept or' +
          ' decline it under Quote requests';
        T.ui.toast(text, 'ok', 6000);
        if (state.dead) { return; }
        state.result = { text: text, entries: [] };
        renderResult();
        if (state.rfqH) { state.rfqH.refresh(); }
      }, function (e) {
        if (ui.requestBtn) { ui.requestBtn.disabled = false; ui.requestBtn.textContent = 'Request quote'; }
        if (state.dead) { return; }
        state.error = describe(e); ui.error.title = (e && e.code) || '';
        state.errorField = fieldOfError(kind, e, vals());
        renderError();
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
      if (!ui.contractsInner) { return; }
      var oldWrap = ui.contractsInner.querySelector('.st-wrap');
      var scroll = oldWrap ? oldWrap.scrollLeft : 0;
      T.clear(ui.contractsInner);
      ui.contractsInner.appendChild(T.h('h2', null, 'Contracts', T.h('small', null, 'model mark = computed clean PV now; click a contract for its terms and history')));
      ui.contractsInner.appendChild(metaLine());
      var rows = state.contracts;
      if (rows == null) { ui.contractsInner.appendChild(T.h('div', { class: 'empty' }, state.loadError ? 'no contracts shown yet' : 'loading contracts ...')); return; }
      if (!rows.length) { ui.contractsInner.appendChild(T.h('div', { class: 'empty' }, 'no OTC contracts in ' + (T.accountName ? T.accountName(account) : account) + ' yet')); return; }
      var tbl = T.h('table', { class: 'grid st-contracts' }, T.h('thead', null, T.h('tr', null, T.h('th', { class: 'l', title: 'an open contract can be terminated' }, 'status'),
        T.h('th', { class: 'l' }, 'contract'), T.h('th', { class: 'l' }, 'kind / side'), T.h('th', { class: 'num', title: 'the notional; the maturity (or value date) below' }, 'notional / maturity'),
        T.h('th', { class: 'num', title: 'computed clean PV now (hover a value for its inputs)' }, 'model mark'),
        T.h('th', { class: 'num', title: 'VM: + received from the PB / - posted to the PB; the initial margin posted below' }, 'VM balance / IM'),
        T.h('th', { class: 'num', title: 'PV change for +1 bp' }, 'DV01'))));
      var body = T.h('tbody');
      rows.forEach(function (c) { appendRow(body, c); });
      tbl.appendChild(body);
      var wrap = T.h('div', { class: 'st-wrap' }, tbl);
      ui.contractsInner.appendChild(wrap);
      wrap.scrollLeft = scroll;
    }
    /* OTC-09 (FX_CCS_PLAN D14): an OTC FX contract's rate risk is two-currency.  Its one "DV01" number is a single currency's rate01 - an FX
       forward's on its quote currency's curve, a cross-currency swap's on the USD curve - so it is said as such; every currency's rate01, the
       basis levels and the forward points are in Risk > Currency risk (FX).  null for any other kind (an IRS's DV01 stays a DV01). */
    function fxRateCcy(c) {
      if (c.kind === 'FX_FORWARD') { var pr = String(((c.detail || {}).pair) || ((c.terms || {}).pair) || ''); return pr.length === 6 ? pr.slice(3) : ''; }
      if (c.kind === 'XCCY') { return 'USD'; }
      return null;
    }
    function fxRateText(c, rc) {
      return money(c, 'dv01') + ' for +1 bp on the ' + (rc || 'quote currency') + ' curve only (every currency, the basis and the points: Risk > Currency risk (FX))';
    }
    /* P8R1-07: an FX forward's notional - the base amount, then its USD value at the base currency's USD rate WHEN DEALT (the IM's base),
       said as such: it is not what the deal delivers (the delivery line gives the amounts at the dealt forward) */
    function fxNotionalText(c, d) {
      var base = String(d.pair || '').slice(0, 3);
      var text = base + ' ' + money(d, 'notional_base');
      if (base === 'USD') { return text + ' (the IM base)'; }
      var at = d.im_base_rate ? (String(d.pair).slice(3) === 'USD' ? 'the spot rate ' + d.im_base_rate : (d.im_base_pair || base + '/USD') + ' ' + d.im_base_rate)
        : 'the spot rate';
      return text + ' (USD ' + money(c, 'notional') + ' at ' + at + ' when dealt: the IM base)';
    }
    function appendRow(body, c) {
      var mark = c.mark || {};
      var open = !!state.open[c.contract_id];
      var pf = c.position || null;
      var markTitle = (mark.quality || '') + (mark.as_of ? ' as of ' + nyTime(mark.as_of) + ' ET' : '') + (mark.note ? ' . ' + mark.note : '') +
        (pf ? ' . in NAV ' + money(pf, 'mv') + ' (' + (pf.mark_label || '') + ')' : ' . the mark at the last VM ' + money(c, 'mark'));
      var notional = c.notional == null && c.notional_c == null ? 'n/a' : (T.fmt.compact ? T.fmt.compact(c.notional != null ? c.notional : (c.notional_c / 100)) : money(c, 'notional'));
      var ntCcy = c.notional_ccy || c.currency || '';
      var fxd = c.kind === 'FX_FORWARD' ? (c.detail || {}) : null;       /* OTC-18: an FX forward's notional in its base currency (EUR 1m), the USD beside it */
      var fxBase = fxd ? T.num(fxd, 'notional_base') : null;               /* the wire carries notional_base (decimal string) or notional_base_c */
      if (fxd && fxBase != null && fxd.pair) {
        notional = T.fmt.compact ? T.fmt.compact(fxBase) : T.money(fxd, 'notional_base');
        ntCcy = String(fxd.pair).slice(0, 3);
      }
      var statusCell = T.h('td', { class: 'l stack' });
      if (c.confirm_status) {                     /* C14: CONFIRMED (as-of, or the simulated 17:05 ET confirmation) or PENDING CONFIRMATION */
        var pend = c.confirm_status === 'PENDING_CONFIRM';
        statusCell.appendChild(T.h('div', null, T.stageChip(pend ? 'queued' : 'filled', pend ? 'PENDING CONFIRMATION' : 'CONFIRMED', c.confirm_label || '')));
      }
      /* P8R1-05: the action under the confirmation (one column, not two side by side) */
      statusCell.appendChild(T.h('div', { class: 'st-act' }, c.status === 'OPEN' ? T.h('button', { class: 'btn small', title: 'OPEN - terminate at the current model mark', onclick: function () { onTerminate(c); } }, 'Terminate')
        : (T.stateTag ? T.stateTag(c.status) : T.h('span', null, c.status || ''))));
      var badge = asofBadge(c);
      var tr = T.h('tr', { class: c.status === 'OPEN' ? '' : 'dim', 'data-contract': c.contract_id },
        statusCell,
        T.h('td', { class: 'l' }, T.h('a', { href: '#', title: (open ? 'hide' : 'show') + ' the terms and history', onclick: function (e) { e.preventDefault(); state.open[c.contract_id] = !state.open[c.contract_id]; renderContracts(); } },
          (open ? '- ' : '+ ') + c.contract_id), badge ? ' ' : '', badge),
        T.h('td', { class: 'st-side' }, T.h('b', null, kindShort(c)), ' ', sideText(c.side, c) + (c.fixing_method === 'COMPOUND_ARREARS' ? ' (compounded)' : '')),
        T.h('td', { class: 'num stack', title: fxd && fxd.pair && fxBase != null ? fxNotionalText(c, fxd) : money(c, 'notional') + ' ' + (c.notional_ccy || c.currency || '') },
          T.h('div', null, notional + ' ' + ntCcy), T.h('div', { class: 'cl2', title: 'maturity' + (c.kind === 'FX_FORWARD' ? ' (the value date)' : '') }, c.maturity_date || '')),
        T.h('td', { class: 'num ' + (T.signCls ? T.signCls(mark.value) : ''), title: markTitle }, amt(mark.value) + (mark.quality && mark.quality !== 'computed' ? ' ' + mark.quality : ''),
          ' ', modelBadge()),
        T.h('td', { class: 'num stack', title: (c.margin_mode || '') + ': + received from the PB / - posted to the PB; the initial margin posted below' },
          T.h('div', null, money(c, 'vm_balance')), T.h('div', { class: 'cl2' }, 'IM ' + money(c, 'im_balance'))),
        (function () {
          var rc = fxRateCcy(c);
          if (c.dv01 == null && c.dv01_c == null) { return T.h('td', { class: 'num' }, 'n/a'); }
          if (rc == null) { return T.h('td', { class: 'num' }, money(c, 'dv01')); }
          return T.h('td', { class: 'num', title: fxRateText(c, rc) }, money(c, 'dv01') + (rc ? ' ' + rc + ' curve' : ''));
        })());
      body.appendChild(tr);
      if (open) { body.appendChild(T.h('tr', { class: 'detail' }, T.h('td', { colspan: String(COLS) }, drawer(c)))); }
    }
    function drawer(c) {
      var h = c.history || {};
      var box = T.h('div', { class: 'history st-drawer' });
      var mark = c.mark || {};
      var d = c.detail || {};
      var vmCount = (h.vm || []).length;
      var pf = c.position || null;
      var kv = [];
      if (c.source_label) { kv.push(['source', T.h('span', null, asofBadge(c), asofBadge(c) ? ' ' : '', c.source_label)]); }
      if (c.confirm_status) { kv.push(['confirmation', c.confirm_label || c.confirm_status]); }
      else if (c.confirm_note) { kv.push(['confirmation', c.confirm_note]); }
      kv.push(['dealt', dealtText(c)]);
      kv.push(['side', sideText(c.side, c)]);
      if (c.client_ref) { kv.push(['owner reference', c.client_ref, 'the reference the booking carries: a second booking with it is refused']); }
      var fwdOpt = /^(EQ|FX)_(OPTION|FORWARD)$/.test(String(c.kind || ''));
      if (c.kind === 'FX_FORWARD' && T.num(d, 'notional_base') != null && d.pair) {
        kv.push(['notional', fxNotionalText(c, d)]);
      } else {
        kv.push(['notional', money(c, 'notional') + ' ' + (c.notional_ccy || c.currency || '')]);
      }
      kv.push(['dates', (c.trade_date ? 'traded ' + c.trade_date + ' . ' : '') + (fwdOpt ? (c.kind === 'FX_FORWARD' ? 'value date ' : (c.kind === 'EQ_FORWARD' ? 'maturity ' : 'expiry ')) +
        (c.maturity_date || '') : (c.start_date || '') + ' to ' + (c.maturity_date || '')) + ' . ' + (c.margin_mode || 'CTM') +
        ' variation margin' + (c.asof && c.entered_ts ? ' . entered ' + nyTime(c.entered_ts) + ' ET' : '')]);
      if (c.curve_spread_text) { kv.push(['curve spread', c.curve_spread_text]); }
      var now = nowText(c);
      if (now) { kv.push(['now', now]); }
      kv.push(['model mark', T.h('span', null, amt(mark.value) + ' . ' + (mark.quality || '') + (mark.as_of ? ' as of ' + nyTime(mark.as_of) + ' ET' : '') +
        (mark.note ? ' . ' + mark.note : '') + ' ', modelBadge())]);
      if (pf) {
        kv.push(['in NAV', money(pf, 'mv') + ' (' + (pf.mark_label || '') + ') . unrealised ' + money(pf, 'unrealised') +
          ((T.num(pf, 'cost') || 0) !== 0 ? ' against the contract cost ' + money(pf, 'cost') : '')]);
      }
      kv.push(['last VM mark', money(c, 'mark') + (vmCount ? ' (the mark at the last variation margin; 0 after a settled-to-market settlement)'
        : ' (no variation margin yet: the mark at booking)')]);
      var optFwd = /^(EQ|FX)_(OPTION|FORWARD)$/.test(String(c.kind || ''));      /* OTC-18: no swap-only rows (accruals, periods) on an option / forward */
      if (!optFwd) { kv.push(['accrued', accruedText(c)]); }
      if (optFwd && d.delivery && d.delivery.receive) {
        kv.push(['delivery', 'receive ' + (d.delivery.receive[0] || '') + ' ' + T.fmt.cents(Number(d.delivery.receive[1]) || 0) + ' . pay ' + (d.delivery.pay[0] || '') + ' ' +
          T.fmt.cents(Number(d.delivery.pay[1]) || 0) + ' on ' + (d.expiry || c.maturity_date || '')]);
      }
      if (c.kind === 'TRS') {
        var fn = d.financing_now || null;
        kv.push(['financing now', fn ? (fmtPct(fn.rate) + ' (SOFR ' + fn.sofr + ' % ' + fn.obs_date + ', spread ' + fmtPct(d.spread) + ')') : 'no SOFR print within 10 days (waits; never defaulted)']);
        kv.push(['accrued legs', 'receivable ' + money(d, 'accrued_receivable') + ' . payable ' + money(d, 'accrued_payable')]);
        kv.push(['reset dates', (d.reset_dates || []).slice(0, 6).join(', ') + ((d.reset_dates || []).length > 6 ? ' ...' : '')]);
      } else if (c.kind === 'XCCY') {
        var legs = d.legs || {};
        ['receive', 'pay'].forEach(function (s) {
          var l = legs[s];
          if (!l) { return; }
          if (l.index) {                          /* a floating leg: its index, spread, compounding and the fixings so far */
            var fx = (l.fixings || []).map(function (x) { return typeof x === 'string' ? x : [x.period ? 'period ' + x.period : '', x.rate_pct || x.rate || '', x.index_values || x.words || ''].filter(Boolean).join(' '); });
            kv.push([s + ' leg', l.currency + ' ' + money(l, 'notional') + ' on ' + (l.index_name || l.index) + (l.spread_bp != null && Number(l.spread_bp) !== 0 ? ' ' + (Number(l.spread_bp) > 0 ? '+ ' : '') + l.spread_bp + ' bp' : ' flat') +
              ' . ' + (l.compounding || l.day_count || '') + (l.accrued != null || l.accrued_c != null ? ' . accrued ' + money(l, 'accrued') : '') + (fx.length ? ' . fixings: ' + fx.join('; ') : '') + (l.role ? ' . ' + l.role : '')]);
          } else {
            kv.push([s + ' leg', l.currency + ' ' + money(l, 'notional') + ' at ' + fmtPct(l.rate) + ' ' + (l.basis || '') + ' . accrued ' + money(l, 'accrued') + ' . notional balance ' + money(l, 'notional_balance') + ' . ' + (l.role || '')]);
          }
        });
        var tu = d.trueups || d.true_ups || [];
        if (tu.length) {
          kv.push(['true-ups', tu.map(function (x) { return typeof x === 'string' ? x : [x.date || x.period_end || '', x.ccy || x.currency || '', x.amount != null || x.amount_c != null ? money(x, 'amount') : '', x.words || x.what || ''].filter(Boolean).join(' '); }).join(' ; '),
            'each payment trues the nightly estimate up to the published index']);
        }
        kv.push(['deal FX', px(d.deal_rate) + (c.terms && c.terms.deal_rate_source ? ' (' + c.terms.deal_rate_source + ')' : '') + (d.mtm_reset ? ' . MTM notional resets' : ' . fixed notionals') +
          (d.started ? ' . start exchanged' : ' . start exchange pending') + (d.matured_exchange ? ' . maturity exchanged' : '')]);
      } else if (c.kind === 'IRS') {
        kv.push(['SOFR leg', fixingText(c.fixing)]);
      }
      var linked = c.linked_deals || d.linked_deals || null;
      if (linked && linked.length) { kv.push(['linked deals', linkedText(linked)]); }
      if (c.kind === 'FX_FORWARD') {
        if (d.settlement_funding && Object.keys(d.settlement_funding).length) {
          kv.push(['settlement funding', Object.keys(d.settlement_funding).map(function (k) { return k + ': ' + (d.settlement_funding[k] === 'BUY_WITH_USD' ? 'bought with USD two business days before the value date' : 'none'); }).join(' . ')]);
        }
        recordRows(d.pricing_record).forEach(function (r) { kv.push(r); });
        if (!d.pricing_record && (d.labels || []).length) { kv.push(['labels', d.labels.join(' . ')]); }
      }
      confRows(c.confirmation || d.confirmation).forEach(function (r) { kv.push(r); });
      if (c.detail_error) { kv.push(['detail', c.detail_error]); }
      kv.push(['next events', eventsText(c.next_events) || 'none']);
      var rcD = fxRateCcy(c);
      if (rcD != null) { kv.push(['rate risk', c.dv01 == null && c.dv01_c == null ? 'n/a' : fxRateText(c, rcD)]); }
      else { kv.push(['DV01', c.dv01 == null && c.dv01_c == null ? 'n/a' : money(c, 'dv01') + ' (PV change for +1 bp)']); }
      if (optFwd) { kv.push(['result', money(c, 'result') + ' realised (at expiry, maturity or termination)']); }
      else {
        kv.push(['swap result', money(c, 'result') + ' realised' + ((T.num(c, 'financing') || 0) !== 0 ? ' . financing ' + money(c, 'financing') : '') +
          ((T.num(c, 'coupons') || 0) !== 0 ? ' . coupons / premium ' + money(c, 'coupons') : '') + ((T.num(c, 'dividends') || 0) !== 0 ? ' . dividend equivalents ' +
          money(c, 'dividends') : '') + ' (financing and coupons: + paid / - received)']);
      }
      if (!optFwd) { kv.push(['remaining periods', String(c.remaining_periods == null ? '' : c.remaining_periods)]); }
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
          b2.appendChild(T.h('tr', null, T.h('td', null, r.event_date), T.h('td', { title: r.kind || '' }, eventWords(r.kind)), T.h('td', { class: 'num' }, r.kind === 'FIX' ? '' : money(r, 'amount')),
            T.h('td', { class: 'l', style: 'white-space:normal' }, resetText(r.kind, dd)), T.h('td', null, entryLink(r.entry_id))));
        });
        t2.appendChild(b2); rsBox.appendChild(T.h('div', { class: 'st-wrap' }, t2));
      }
      box.appendChild(rsBox);
      box.appendChild(timelineBox(c.contract_id));
      return box;
    }
    /* OMS-10: the contract's life in time order (quote requested, quoted, accepted, booked, IM, confirmation, each VM, fixings, payments,
       resets, a credit event, the termination) - loaded once per drawer opening, kept for 30 s */
    function timelineBox(cid) {
      var host = T.h('div', { class: 'section' }, T.h('h3', null, 'Timeline'));
      var slot = T.h('div', { class: 'empty' }, 'loading ...');
      host.appendChild(slot);
      var cached = state.timelines[cid];
      function paint(rows) { host.replaceChild(timelineTable(rows), slot); }
      if (cached && Date.now() - cached.at < 30000) { paint(cached.rows); return host; }
      T.api.get('/api/otc/contracts/' + encodeURIComponent(cid) + '/timeline').then(function (r) {
        var rows = (r && r.timeline) || [];
        state.timelines[cid] = { rows: rows, at: Date.now() };
        if (connected(slot)) { paint(rows); }
      }, function (e) { if (connected(slot)) { slot.textContent = 'the timeline could not be loaded: ' + describe(e); } });
      return host;
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

    handle.refresh = function () { if (state.rfqH) { state.rfqH.refresh(); } return load(); };
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
        ['status', (c.status || '') + (c.confirm_label ? ' . ' + c.confirm_label : '')], c.source_label ? ['source', c.source_label] : null,
        ['maturity', c.maturity_date || ''], ['model mark', amt(mark.value) + (mark.quality ? ' (' + mark.quality + ')' : '') + ' . MODEL'],
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
    fmtPct: fmtPct, eventsText: eventsText, fixingText: fixingText, sideText: sideText, readTerms: readTerms, formValues: formValues,
    /* C14 / UI-11: the quote requests and the contract timeline, for the CDX and options tickets and the Derivatives page */
    rfqRequest: rfqRequest, mountRfqs: mountRfqs, acceptDialog: acceptDialog, timelineTable: timelineTable, kindShort: kindShort, nowText: nowText,
    dealtText: dealtText, accruedText: accruedText, asofBadge: asofBadge, modelBadge: modelBadge, QUOTE_SOURCE: QUOTE_SOURCE,
    /* FX_CCS_PLAN 1.0.6.1: the confirmation, the linked deals and an FX forward's pricing record, for the Derivatives page's drawer */
    confRows: confRows, linkedText: linkedText, recordRows: recordRows };
  if (T.keys && typeof T.keys.uses === 'function') { T.keys.uses('components/swapticket.js'); }   /* its keys follow Settings > Keyboard */
})();
