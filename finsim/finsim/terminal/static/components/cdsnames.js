/* PB page -> OTC tab -> Single-name CDS (CDS_SINGLE_NAME_PLAN rev 2, release 1.0.7, package B4): the desk of the single-name credit default
 * swaps dealt with the prime broker - buy or sell protection on one US-listed company's senior unsecured debt, 1 to 10 years (5Y by
 * default), the standard 100 / 500 bp coupon with an upfront and the accrued rebate (the owner 2026-10-28: "CDS needs added to prime
 * brokers, i see CDX, but I want to CDS on everything you can").  Mounted by pages/markets_otc.js under the Single-name CDS sub-tab (hook
 * H4-7); the contracts table below the sub-tabs is the swap ticket's, which renders every OTC kind (a CDS row through the helpers below,
 * hooks H4-8).  The Derivatives, Positions, Portfolio, Risk and Cash pages call the helpers too (hooks H4-9..H4-13).
 *
 *     var h = T.components.cdsnames.mount(el, {account_id: 'I1:PB_GS', blocked: null | reason, note: false, onBooked: fn(result)});
 *     h.setBlocked(reason);   // the eligibility reason: Preview / Request quote / the saves disabled (null clears it)
 *     h.refresh();            // reloads the desk (ladder, universe, tier terms, this account's dealing levels) and the list
 *     h.unmount();
 *
 * Reads    GET  /api/otc/cds/desk?account_id=                 the MODEL label, the FRED ladder with its date, the universe count and its
 *                                                             refresh time, the PB's tier terms, this account's dealing levels
 *          GET  /api/otc/cds/entities?q=&filter=&limit=&offset=&account_id=    search and browse, 50 rows a page
 *          GET  /api/otc/cds/model?cik=&tenor_years=5         a name's 5Y MODEL spread on request (202 while its inputs are fetched)
 *          GET  /api/otc/cds/basis/effect?account_id=&cik=&negotiated_mid_bp= | deal_at_model=1 | clear=1    what a dealing level does
 *                                                             to the open contracts of the name (computed, nothing written)
 * Previews POST /api/otc/cds/preview {account_id, terms: {cik, tenor_years, side, notional, coupon_bp?}}    (202 while the name's inputs
 *          are fetched: retried by itself, at most 20 times)
 * Quotes   POST /api/otc/rfqs {account_id, kind: 'CDS', terms}    (T.components.swapticket.rfqRequest: the simulated dealer quotes on the next
 *          lifecycle tick; the quote is accepted or declined under Quote requests, above the contracts table)
 * Edits    POST /api/otc/cds/terms {account_id, tier, im_pct?, half_spread_bp?}    (assumed, editable; recorded with before / after)
 *          POST /api/otc/cds/basis {account_id, cik, negotiated_mid_bp | deal_at_model: true | clear: true}    (the dealing level)
 * Events   POST /api/otc/cds/contracts/{id}/credit-event {event_type, recovery_pct, event_date}    (the Derivatives page; owner-triggered)
 *
 * Every number is the server's; nothing is priced here.  Every spread is a MODEL, shown with its inputs, their dates and sources.  No new
 * deal runs on the bare model: until the owner sets a negotiated mid or confirms dealing at the MODEL for a name in this account, the
 * preview shows every number but Request quote stays disabled and the blocker says why in words, with the two level buttons beside it;
 * a new level is shown with its effect on the open contracts before it is saved.  A refusal is words first (its code only in the hover).
 * Every call is gated by T.hasModule (a module that is not running reads "unavailable", and no request is made).  ASCII; core.js only.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};

  var MOD = 'swaps_cds_names';                     /* the engine (B1): desk, preview, terms, basis, credit event */
  var ENT = 'credit_entities';                     /* the reference entities (B2): search and browse */
  var MDL = 'cds_model';                           /* the spread model (B3): a name's MODEL spread on request */
  var R_DESK = '/api/otc/cds/desk', R_PREVIEW = '/api/otc/cds/preview', R_TERMS = '/api/otc/cds/terms', R_BASIS = '/api/otc/cds/basis',
      R_EFFECT = '/api/otc/cds/basis/effect', R_ENTITIES = '/api/otc/cds/entities', R_MODEL = '/api/otc/cds/model', R_CONTRACTS = '/api/otc/cds/contracts/';
  var TENORS = [1, 2, 3, 4, 5, 7, 10];
  var LADDER = ['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC'];
  var TIERS = ['A_OR_BETTER', 'BBB', 'BB', 'B', 'CCC'];
  var LEVEL_WORDS = { NEGOTIATED: 'your negotiated mid', MODEL: 'MODEL (confirmed)', MIXED: 'mixed levels', NONE: 'none' };
  var TIER_WORDS = { A_OR_BETTER: 'A-like or better', BBB: 'BBB-like', BB: 'BB-like', B: 'B-like', CCC: 'CCC-like' };
  function tierWords(t) { return has(t) ? (TIER_WORDS[t] || s(t)) : 'n/a'; }
  /* the browse filter: the key sent as filter= and its words (4.2) */
  var FILTERS = [{ key: 'all', label: 'all' }, { key: 'nonfinancial', label: 'non-financial' }, { key: 'financial', label: 'financial' },
                 { key: 'reit', label: 'REIT' }, { key: 'mine', label: 'my contracts' }];
  var DEFAULTS = { tenor: '5', side: 'BUY_PROTECTION', notional: '5,000,000.00', coupon: '' };
  var DESK_MS = 60000, SEARCH_MS = 250, EFFECT_MS = 400, PAGE = 50, RETRY_MAX = 20;
  var MODEL_LINE = 'MODEL - equity-implied CreditGrades spread plus the ICE BofA AAA OAS floor; not a dealer quote';
  var OBLIGATION = 'senior unsecured; credit events XR14 (bankruptcy and failure to pay, no restructuring); no reference-obligation ISIN (the ISDA' +
                   ' standard reference obligation list is not available here)';
  var CREDIT_LABEL = 'simulated credit event - recovery entered by the owner; no ISDA Determinations Committee ruling or auction in the simulator';
  var CREDIT_RULES = 'the contract closes: protection = notional x (1 - recovery), less the accrued premium, settles the next business day; the IM is' +
                     ' released and the upfront cost is realised';
  var NOT_VALIDATED = 'Its spread is a MODEL and has not been validated against traded levels; design-time checks missed traded names by about 2 times' +
                      ' in both directions.';
  var FIN_CAVEAT = 'financial firm: CreditGrades was calibrated for industrial firms (its 2002 document excludes banks and finance firms); deposits,' +
                   ' repos and policy liabilities are left out of debt - treat this spread with care';
  /* a refused entity's reason in words, when the server sends only its code (3.5); the server's own words win */
  var REASON_WORDS = {
    BELOW_MIN_DEBT: function (r) { return 'not offered: reported debt ' + (has(r.debt) || has(r.debt_c) ? 'USD ' + T.money(r, 'debt') + ' ' : '') + 'is below the USD 100,000,000.00 minimum (assumed)'; },
    NO_DEBT: function () { return 'not offered: no debt reported in the latest 10-Q / 10-K XBRL (or it uses the company\'s own tags)'; },
    NO_SHARES: function () { return 'not offered: share count not reported (multi-class issuer?)'; },
    STALE_FUNDAMENTALS: function (r) { return 'not offered: balance sheet ' + (r.debt_end || '') + (num(r.age_days) != null ? ' is ' + Math.floor(num(r.age_days) / 30.4) + ' months old' : ' is too old'); },
    FOREIGN_FILER: function () { return 'not offered: foreign private issuer (ADR ratios and IFRS reports are not modelled in this release)'; },
    DEBT_GT_LIABILITIES: function () { return 'not offered: the reported debt is above the total liabilities of the same balance sheet (a filing-tag problem)'; }
  };
  var memo = {};                                   /* account -> {sel, values, q, filter, page, sort}: kept across re-renders */
  var models = {};                                 /* cik -> the 5Y MODEL result seen in this session (the browse column) */

  /* ---- small helpers ------------------------------------------------------------------------------------------------------- */
  function s(x) { return x == null ? '' : String(x); }
  function has(x) { return x != null && x !== ''; }
  function pick() { for (var i = 0; i < arguments.length; i++) { if (has(arguments[i])) { return arguments[i]; } } return null; }
  function num(x) { if (!has(x) || typeof x === 'object') { return null; } var n = Number(String(x).replace(/,/g, '')); return isFinite(n) ? n : null; }
  function obj(x) { return x && typeof x === 'object' && !Array.isArray(x) ? x : {}; }
  function clean(x) { return s(x).trim().replace(/,/g, ''); }
  function bp(x) { return num(x) == null ? 'n/a' : T.fmt.num(x, 2) + ' bp'; }
  function bpSigned(x) { var n = num(x); return n == null ? 'n/a' : (n > 0 ? '+' : '') + T.fmt.num(x, 2) + ' bp'; }
  function bpShort(x) { var n = num(x); return n == null ? 'n/a' : T.fmt.num(x, Math.round(Math.abs(n) * 100) % 100 === 0 ? 0 : 2); }
  function pct(fraction) { return num(fraction) == null ? 'n/a' : T.fmt.pct(fraction); }
  function money(row, key) { return T.money(row, key); }
  function hasMoney(row, key) { return !!row && (has(row[key]) || has(row[key + '_c'])); }
  function usd(row, key) { return hasMoney(row, key) ? 'USD ' + money(row, key) : 'n/a'; }
  function absUsd(row, key) { return hasMoney(row, key) ? 'USD ' + money(row, key).replace(/^-/, '') : 'n/a'; }
  function signedMoney(row, key) { if (!hasMoney(row, key)) { return 'n/a'; } var n = T.num(row, key); return (n != null && n > 0 ? '+' : '') + money(row, key); }
  function describe(e) { return T.api && T.api.describe ? T.api.describe(e) : s(e && e.message || e); }
  function words(code) { return T.codeWords ? T.codeWords(code) : s(code).replace(/_/g, ' ').toLowerCase(); }
  function whenOf(row, key) {                       /* an epoch field (with its _ny twin) as "2026-10-29 10:14 ET" */
    if (!row) { return ''; }
    var ts = row[key + '_ts'] != null ? row[key + '_ts'] : row[key];
    if (num(ts) != null && num(ts) > 0) { return T.fmt.dt(ts) + ' ET'; }
    return s(row[key + '_ny'] || '');
  }
  function modOk(name) { return !!(T.hasModule && T.hasModule(name)); }
  /* the dates of a valuation's inputs ({underlying: {instrument_id, quote_time | close_date}, floor, curve, valuation_date}) in words */
  function asOfWords(a) {
    if (!has(a)) { return ''; }
    if (typeof a !== 'object') { return s(a); }
    var u = obj(a.underlying), parts = [];
    if (a.valuation_date) { parts.push('valuation ' + s(a.valuation_date)); }
    if (u.close_date) { parts.push('share price: the close of ' + s(u.close_date)); }
    else if (num(u.quote_time) != null && num(u.quote_time) > 0) { parts.push('share price ' + T.fmt.dt(u.quote_time) + ' ET'); }
    if (a.floor) { parts.push('AAA floor of ' + s(a.floor)); }
    if (a.curve) { parts.push('curve of ' + s(a.curve)); }
    return parts.join(' . ');
  }
  function absMoneyText(x) { return s(x).replace(/^-/, ''); }
  /* percent typed ("4", "12.5") <-> the fraction the server keeps ("0.04", "0.125"), by string arithmetic (no float) */
  function pctToFrac(p) {
    var t = clean(p).replace(/%$/, '').trim();
    if (!/^\d+(\.\d+)?$/.test(t)) { return null; }
    var parts = t.split('.'); var ip = parts[0].replace(/^0+(?=\d)/, ''), fp = parts[1] || '';
    while (ip.length < 3) { ip = '0' + ip; }
    var out = ip.slice(0, ip.length - 2).replace(/^0+(?=\d)/, '') + '.' + ip.slice(ip.length - 2) + fp;
    return out.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') || '0';
  }
  function fracToPct(f) {
    var t = clean(f);
    if (!/^\d+(\.\d+)?$/.test(t)) { return ''; }
    var parts = t.split('.'); var ip = parts[0], fp = (parts[1] || '');
    while (fp.length < 2) { fp += '0'; }
    var out = (ip + fp.slice(0, 2)).replace(/^0+(?=\d)/, '') + (fp.length > 2 ? '.' + fp.slice(2) : '');
    return out.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
  }

  var cssDone = false;
  function ensureCss() {
    if (cssDone || document.getElementById('cdsnames-css')) { cssDone = true; return; }
    cssDone = true;
    var st = document.createElement('style');
    st.id = 'cdsnames-css';
    st.textContent = '.cdsnames { min-width: 0; }' +
      '.cdsnames .cn-head { margin: 4px 0 8px; line-height: 1.45; }' +
      '.cdsnames .cn-head .cn-model { font-weight: 600; }' +
      '.cdsnames .lim { display: inline-block; padding: 0 5px; margin-left: 4px; border: 1px solid var(--accent, #c90); border-radius: 3px; font-size: 10px; color: var(--accent, #c90); }' +
      '.cdsnames .cn-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }' +
      '.cdsnames .cn-spacer { flex: 1 1 auto; }' +
      '.cdsnames .cn-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; }' +
      '.cdsnames .cn-blocked { border-left: 3px solid var(--down, #c55); }' +
      '.cdsnames .cn-warn { color: var(--warn, #c90); }' +
      '.cdsnames .cn-bad { color: var(--down, #c55); }' +
      '.cdsnames .cn-amber { color: var(--warn, #c90); }' +
      '.cdsnames .cn-title { font-weight: 600; margin: 6px 0 4px; }' +
      '.cdsnames .cn-plain { margin: 4px 0 8px; white-space: normal; line-height: 1.4; max-width: 820px; }' +
      '.cdsnames .cn-ticket { margin: 6px 0 12px; padding: 6px 8px; border: 1px solid var(--line, #444); max-width: 980px; }' +
      '.cdsnames .cn-entity { margin: 4px 0; line-height: 1.45; white-space: normal; }' +
      '.cdsnames .cn-entity .cn-parts { color: var(--fg3, #888); font-size: 11px; }' +
      '.cdsnames .cn-ticket .form { max-width: 760px; }' +
      '.cdsnames .cn-ticket input[type=text] { width: 150px; }' +
      '.cdsnames .cn-guard { margin: 6px 0; padding: 6px 8px; border-left: 3px solid var(--down, #c55); white-space: normal; max-width: 820px; }' +
      '.cdsnames .cn-guard .cn-actions { margin: 6px 0 0; }' +
      '.cdsnames .cn-assume { margin: 8px 0; padding: 6px 8px; border: 1px dashed var(--line, #444); max-width: 980px; }' +
      '.cdsnames .cn-assume input[type=text] { width: 70px; }' +
      '.cdsnames .cn-wrap { overflow-x: auto; max-width: 100%; }' +
      '.cdsnames .cn-wrap > table { width: auto; min-width: 100%; }' +
      '.cdsnames table.grid td, .cdsnames table.grid th { white-space: nowrap; }' +
      '.cdsnames table.grid td.cn-wide { white-space: normal; min-width: 160px; max-width: 320px; line-height: 1.3; }' +
      '.cdsnames table.cn-browse tbody tr { cursor: pointer; }' +
      '.cdsnames table.cn-browse tbody tr.cn-sel { outline: 1px solid var(--accent, #c90); }' +
      '.cdsnames table.cn-browse th.cn-sort { cursor: pointer; text-decoration: underline dotted; }' +
      '.cdsnames .cn-search { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0 4px; }' +
      '.cdsnames .cn-search input[type=text] { width: 260px; }' +
      '.cdsnames .cn-pager { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 4px 0; }' +
      '.cdsnames .kv div:nth-child(even) { white-space: normal; }' +
      '.cdsnames .cn-model-box { margin: 4px 0 8px; }' +
      '.cdsnames input.bad, .cdsnames select.bad { border-color: var(--down, #c55); outline: 1px solid var(--down, #c55); }' +
      '.cn-dialog .kv div:nth-child(even) { white-space: normal; text-align: left; }' +
      '.cn-dialog .cn-wrap { overflow-x: auto; max-width: 100%; }' +
      '.cn-dialog table.grid td, .cn-dialog table.grid th { white-space: nowrap; }' +
      '.cn-dialog .cn-words { margin: 4px 0 8px; white-space: normal; line-height: 1.4; max-width: 640px; }' +
      '.cn-dialog .cn-warn { color: var(--warn, #c90); }' +
      '.cn-dialog .cn-bad { color: var(--down, #c55); }' +
      '.cn-dialog input[type=text] { width: 110px; }' +
      '.cdsn-drawer .kv div:nth-child(even) { white-space: normal; text-align: left; }';
    document.head.appendChild(st);
  }

  /* ---- the words of a contract row (shared contracts table, Derivatives, Positions) ------------------------------------------- */
  function cterms(c) { return obj(c && c.terms); }
  function cdetail(c) { return obj(c && c.detail); }
  function cpos(c) { return obj(c && c.position); }
  function cextra(c) { return obj(cpos(c).extra); }
  function tickerOf(c) { var d = cdetail(c); return s(pick(d.ticker, obj(d.entity).ticker, cterms(c).ticker, cextra(c).ticker)); }
  function nameOfContract(c) { var d = cdetail(c); return s(pick(d.entity_name, obj(d.entity).name, cterms(c).entity_name, cextra(c).entity_name)); }
  function tenorOf(c) { var d = cdetail(c); return s(pick(d.tenor_years, cterms(c).tenor_years, cextra(c).tenor_years)); }
  function sideOf(side, row) {
    var sd = s(side).toUpperCase();
    if (sd === 'BUY_PROTECTION' || sd === 'SELL_PROTECTION') { return sd; }
    var r = row || {};
    sd = s(pick(r.side, cterms(r).side, cdetail(r).side, cextra(r).side)).toUpperCase();
    if (sd === 'BUY_PROTECTION' || sd === 'SELL_PROTECTION') { return sd; }
    if (cterms(r).receiver === true) { return 'SELL_PROTECTION'; }
    if (cterms(r).receiver === false) { return 'BUY_PROTECTION'; }
    return null;
  }
  /* 'buy protection (short credit)' / 'sell protection (long credit)' */
  function sideText(side, row) {
    var sd = sideOf(side, row);
    if (sd === 'SELL_PROTECTION') { return 'sell protection (long credit)'; }
    if (sd === 'BUY_PROTECTION') { return 'buy protection (short credit)'; }
    return s((row && row.side_text) || 'single-name CDS');
  }
  /* 'ORCL 5Y CDS' */
  function kindShort(c) {
    var tk = tickerOf(c) || nameOfContract(c) || 'single-name';
    var tn = tenorOf(c);
    return tk + (tn ? ' ' + tn + 'Y' : '') + ' CDS';
  }
  /* the shared table's "now": 'spread now 248.10 bp (MODEL) vs dealt 250.10 bp . coupon 100 bp . upfront paid 1,234.56' */
  function nowText(c) {
    var f = cpos(c), x = obj(f.extra);
    if (!has(f.entry) && !has(f.current)) { return null; }
    var up = '';
    if (has(x.upfront) || has(x.upfront_c)) {
      var amt = typeof x.upfront_c === 'number' ? T.fmt.cents(Math.abs(x.upfront_c)) : (num(x.upfront) != null ? absMoneyText(T.fmt.money(x.upfront)) : '');
      var dir = num(x.upfront) == null && has(x.upfront) ? s(x.upfront) : (typeof x.upfront_c === 'number' ? (x.upfront_c < 0 ? 'received' : 'paid') : '');
      up = amt ? ' . upfront ' + (dir ? dir + ' ' : '') + amt : '';
    }
    return 'spread now ' + (has(f.current) ? s(f.current) + ' (MODEL)' : 'not observed') + ' vs dealt ' + (has(f.entry) ? s(f.entry) : 'n/a') +
      (has(x.coupon_bp) ? ' . coupon ' + s(x.coupon_bp) + ' bp' : '') + up;
  }
  /* Derivatives: 'dealt 250.10 bp / now 248.10 bp (MODEL)' */
  function dealtNow(c) {
    var f = cpos(c);
    if (!has(f.entry) && !has(f.current)) { return ''; }
    return 'dealt ' + (has(f.entry) ? s(f.entry) : 'n/a') + ' / now ' + (has(f.current) ? s(f.current) + ' (MODEL)' : 'not observed');
  }
  /* Derivatives: the CS01 cell */
  function sensitivity(c) {
    var d = cdetail(c);
    if (hasMoney(d, 'cs01')) { return 'CS01 ' + signedMoney(d, 'cs01'); }
    var lv = obj(d.live);
    return hasMoney(lv, 'cs01') ? 'CS01 ' + signedMoney(lv, 'cs01') : 'CS01 n/a';
  }
  /* Positions: a CDS position's side in words (negative quantity = protection bought = short credit) */
  function swapSide(r) {
    var ex = obj(r && r.extra); var pfx = obj(obj(ex.position_fields).extra);
    var sd = sideOf(pfx.side, null) || sideOf(ex.side, null);
    if (sd) { return sd === 'SELL_PROTECTION' ? 'sell protection' : 'buy protection'; }
    var q = Number(r && r.qty);
    return isFinite(q) && q < 0 ? 'buy protection' : 'sell protection';
  }
  /* Portfolio footer: 'single-name CDS credit long / short L / S' (HTML, escaped), or null when the book holds none */
  function footer(x, def) {
    if (!x) { return null; }
    var lo = T.num(x, 'cds_long_credit'), sh = T.num(x, 'cds_short_credit');
    if (!lo && !sh) { return null; }
    var title = (def && def.cds) || 'single-name CDS at notional: protection bought = short credit, protection sold = long credit';
    return '<span title="' + T.esc(title) + '">single-name CDS credit long / short ' + T.esc(T.money(x, 'cds_long_credit') + ' / ' + T.money(x, 'cds_short_credit')) + '</span>';
  }

  /* ---- the credit event (Derivatives page): button, dialog, drawer section ------------------------------------------------------ */
  function creditButton(st, c, onDone) {
    return T.h('button', { class: 'btn small', title: 'Simulate credit event: ' + CREDIT_LABEL + ' - ' + CREDIT_RULES, onclick: function () { creditDialog(st, c, onDone); } },
      'Simulate credit event ...');
  }
  function creditDialog(st, c, onDone) {
    ensureCss();
    if (!modOk(MOD)) { T.ui.toast('unavailable: the single-name CDS module is not running in this build', 'err', 5000); return; }
    var t = cterms(c);
    var who = nameOfContract(c) || tickerOf(c) || 'the reference entity';
    var today = T.fmt.date(T.clock && T.clock.nowInt ? T.clock.nowInt() : Math.floor(Date.now() / 1000));
    var typ = T.h('select');
    [['BANKRUPTCY', 'bankruptcy'], ['FAILURE_TO_PAY', 'failure to pay']].forEach(function (o) { typ.appendChild(T.h('option', { value: o[0] }, o[1])); });
    var rec = T.h('input', { type: 'text', spellcheck: 'false', autocomplete: 'off', value: '40', style: 'width:70px' });
    var dt = T.h('input', { type: 'date', value: today, max: today });
    var minDate = s(pick(t.trade_date, c.trade_date, c.start_date));
    if (/^\d{4}-\d{2}-\d{2}$/.test(minDate)) { dt.setAttribute('min', minDate); }
    var err = T.h('div', { class: 'error', hidden: true });
    var n = hasMoney(c, 'notional') ? 'USD ' + money(c, 'notional') : 'the notional';
    var sd = sideOf(c.side, c);
    var body = T.h('div', { class: 'cn-dialog' },
      T.h('div', { class: 'hint' }, CREDIT_LABEL + '. Real credit events are not tracked; this simulates a credit event of ' + who + (tickerOf(c) ? ' (' + tickerOf(c) + ')' : '') + '.'),
      T.form([T.field('event', typ, 'bankruptcy or failure to pay (XR14: no restructuring)'), T.field('recovery (%)', rec, 'the recovery you enter, 0 to 100 (40 = 40 %)'),
              T.field('event date', dt, 'on or before today, on or after the trade date, in a coupon period not yet paid')]),
      T.kv([['the contract', kindShort(c) + ' . ' + sideText(c.side, c) + ' . ' + n],
            ['settlement', 'protection = ' + n + ' x (1 - recovery), ' + (sd === 'SELL_PROTECTION' ? 'paid by you (protection seller)' : 'received by you (protection buyer)') +
             ', less the accrued premium through the event date; it settles the next business day'],
            ['afterwards', CREDIT_RULES]]),
      err);
    T.ui.modal({
      title: 'Simulate a credit event on ' + s(c.contract_id), sticky: true, body: body,
      actions: [{ label: 'Cancel' }, { label: 'Simulate credit event', cls: 'primary', onClick: function (m, btn) {
        btn.disabled = true; err.hidden = true;
        T.api.post(R_CONTRACTS + encodeURIComponent(c.contract_id) + '/credit-event', { event_type: typ.value, recovery_pct: clean(rec.value), event_date: dt.value })
          .then(function (r) {
            m.close();
            r = r || {};
            var netN = T.num(r, 'net');
            var text = s(c.contract_id) + ' closed by the simulated credit event of ' + s(pick(r.reference_entity, r.entity_name, who)) + ' (' + s(pick(r.event_words, words(typ.value))) +
              ', ' + s(r.event_date || dt.value) + ', recovery ' + s(pick(r.recovery_pct, clean(rec.value))) + ' %): net ' + (netN != null && netN < 0 ? 'paid ' : 'received ') +
              absMoneyText(money(r, 'net')) + ' settling ' + s(r.settle_date || 'the next business day') + ' = protection ' + money(r, 'protection') + ' less accrued premium ' +
              money(r, 'accrued') + (hasMoney(r, 'im_released') ? '; IM released ' + money(r, 'im_released') : '');
            T.ui.toast(text, 'ok', 10000);
            if (st) { st.timelines = {}; }
            if (T.poll && T.poll.kick) { ['accounts', 'positions', 'account_detail', 'books', 'attention'].forEach(function (k) { T.poll.kick(k); }); }
            if (onDone) { try { onDone(r); } catch (x) { console.error('cdsnames credit onDone', x); } }
          }, function (e) { btn.disabled = false; err.textContent = describe(e); err.title = (e && e.code) || ''; err.hidden = false; });
      } }]
    });
  }
  function eventsOf(c, st) {
    var d = cdetail(c);
    var list = [];
    if (Array.isArray(d.credit_events)) { list = d.credit_events; }
    else if (d.credit_event && typeof d.credit_event === 'object') { list = [d.credit_event]; }
    var fromPage = st && st.data && st.data.credit_events && st.data.credit_events[c.contract_id];
    if (!list.length && Array.isArray(fromPage)) { list = fromPage; }
    return list;
  }
  /* the Derivatives drawer: reference entity, the MODEL now with its inputs, CS01, jump to default, the credit event record */
  function creditSection(c, st) {
    ensureCss();
    var d = cdetail(c), lv = obj(d.live), t = cterms(c);
    var box = T.h('div', { class: 'section cdsn-drawer' }, T.h('h3', null, 'Single-name CDS', T.h('small', null, ' MODEL spread (not a dealer quote); jump to default at 40 % recovery')));
    var kv = [];
    var ent = obj(d.entity);
    var who = nameOfContract(c);
    kv.push(['reference entity', (who || 'n/a') + (tickerOf(c) ? ' (' + tickerOf(c) + ')' : '') + (has(pick(d.cik, ent.cik, t.cik)) ? ' . CIK ' + s(pick(d.cik, ent.cik, t.cik)) : '') + ' . ' + OBLIGATION]);
    kv.push(['contract', kindShort(c) + ' . ' + sideText(c.side, c) + ' . coupon ' + (has(pick(d.coupon_bp, t.coupon_bp)) ? s(pick(d.coupon_bp, t.coupon_bp)) + ' bp' : 'n/a') +
      ' . recovery 40 % for pricing . matures ' + s(c.maturity_date || t.maturity_date || 'n/a')]);
    if (lv.error) { kv.push(['MODEL now', T.h('span', { class: 'cn-warn', title: s(lv.code) }, 'unavailable now: ' + s(lv.error))]); }
    else if (Object.keys(lv).length) {
      kv.push(['MODEL now', 'mid ' + bp(lv.mid_spread_bp) + ' = model ' + bp(lv.model_spread_bp) + ' (raw CreditGrades ' + bp(lv.raw_bp) + ' + AAA floor ' + bp(lv.floor_bp) + ')' +
        (num(lv.basis_bp) ? ' + your basis ' + bpSigned(lv.basis_bp) : '') + (lv.bucket ? ' . ' + s(lv.bucket) + '-like (model spread bucket - not a credit rating)' : '') +
        (lv.quality === 'distressed' ? ' . distressed - outside the model\'s calibrated range' : '')]);
      if (has(lv.level_words)) { kv.push(['dealing level', s(lv.level_words)]); }
      var asof = asOfWords(lv.inputs_as_of);
      if (asof) { kv.push(['inputs', asof]); }
      var mi = obj(lv.model_inputs);
      if (Object.keys(mi).length) {
        kv.push(['model inputs', 'share price ' + (has(mi.S) ? s(mi.S) : 'n/a') + ' . volatility ' + (has(mi.sigma) ? pct(mi.sigma) : 'n/a') + ' (1000-day realised) . debt ' +
          usd(mi, 'debt') + (has(mi.debt_end) ? ' (balance sheet ' + s(mi.debt_end) + ')' : '') + ' . debt / share ' + (has(mi.dps) ? T.fmt.num(mi.dps, 2) : 'n/a')]);
      }
      kv.push(['value now', signedMoney(lv, 'value') + (has(lv.upfront_pct) ? ' . upfront ' + s(lv.upfront_pct) + ' % (price ' + s(lv.price) + ')' : '') +
        (has(lv.rpv01) ? ' . RPV01 ' + s(lv.rpv01) : '')]);
    }
    if (c.status && c.status !== 'OPEN') {
      kv.push(['state', s(c.status).toLowerCase() + (eventsOf(c, st).length ? ' by the simulated credit event below' : '') + ': no CS01 or jump to default any more']);
    } else {
      kv.push(['CS01', sensitivity(c).replace(/^CS01 /, '') + ' per +1 bp of the spread (a protection buyer gains when it widens)']);
      kv.push(['jump to default', hasMoney(d, 'jtd') ? signedMoney(d, 'jtd') + ' (' + s(d.jtd_rule || '+/- (1 - 40 %) x notional less the value now; the accrued premium settles at par') + ')' : 'unavailable now']);
    }
    if (has(d.terms_text)) { kv.push(['terms', s(d.terms_text)]); }
    box.appendChild(T.kv(kv));
    var ev = eventsOf(c, st);
    var eb = T.h('div', { class: 'section' }, T.h('h3', null, 'Credit event'));
    if (!ev.length) { eb.appendChild(T.h('div', { class: 'empty' }, 'none recorded: real credit events are not tracked; you can simulate one from this row')); }
    else {
      var th = function (x, cls) { return T.h('th', { class: cls || 'l' }, x); };
      var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, th('event date'), th('event'), th('recovery', 'num'), th('protection', 'num'),
        th('accrued premium', 'num'), th('net', 'num'), th('settles'))));
      var b = T.h('tbody');
      ev.forEach(function (x) {
        b.appendChild(T.h('tr', null, T.h('td', null, s(x.event_date)), T.h('td', { class: 'l' }, s(pick(x.event_words, x.event_type && words(x.event_type), ''))),
          T.h('td', { class: 'num' }, has(x.recovery_pct) ? s(x.recovery_pct) + ' %' : 'n/a'), T.h('td', { class: 'num' }, money(x, 'protection')),
          T.h('td', { class: 'num' }, money(x, 'accrued')), T.h('td', { class: 'num' }, money(x, 'net')), T.h('td', null, s(x.settle_date))));
      });
      tbl.appendChild(b);
      eb.appendChild(T.h('div', { class: 'cn-wrap' }, tbl));
      eb.appendChild(T.h('div', { class: 'hint' }, s(pick((ev[0] || {}).label, CREDIT_LABEL)) + '; ' + CREDIT_RULES));
    }
    box.appendChild(eb);
    return box;
  }

  /* ---- the Risk page's Single-name CDS panel (risk.report single_name_cds = swaps_cds_names.risk_block) ------------------------- */
  function riskPanel(container, h) {
    ensureCss();
    h = h || {};
    var nameOf = h.nameOf || function (r) { return s(r.account_id); };
    function mcol(key, label, title) {                /* a money column; a missing figure says unavailable, never 0 */
      return { key: key, label: label, title_h: title,
        html: function (r) { return hasMoney(r, key) ? '<span class="' + T.sign(r, key) + '">' + T.esc(money(r, key)) + '</span>' : '<span class="muted">unavailable</span>'; },
        text: function (r) { return hasMoney(r, key) ? money(r, key) : 'unavailable'; }, sort: function (r) { var n = T.num(r, key); return n == null ? '' : n; } };
    }
    var p = h.panel ? h.panel('cds-single', 'Single-name CDS', 'CS01 = the P&L for +1 bp of each name\'s spread; jump to default at 40 % recovery; spreads are a MODEL')
      : T.h('div', { class: 'panel', id: 'risk-cds-single' }, T.h('h2', null, 'Single-name CDS'));
    p.hidden = true;
    var line = T.h('div', { class: 'pnote' }); p.appendChild(line);
    var g = h.gridIn || function (parent, spec) { var el = T.h('div'); parent.appendChild(el); return T.components.grid.mount(el, Object.assign({ tools: false, rows: [], empty: 'loading ...' }, spec)); };
    var rowsG = g(p, { rowKey: function (r) { return r.contract_id; }, empty: 'no single-name CDS contracts', columns: [
      { key: 'label', label: 'contract', cls: 'l', html: function (r) { return '<b>' + T.esc(s(r.label || r.contract_id)) + '</b><span class="sub2">' + T.esc(s(r.contract_id) + ' . ' + nameOf(r)) + '</span>'; },
        text: function (r) { return s(r.label || r.contract_id); } },
      { key: 'entity', label: 'reference entity', cls: 'l', text: function (r) { return s(r.entity_name || '') + (r.ticker ? ' (' + r.ticker + ')' : ''); } },
      mcol('notional', 'notional'), mcol('exposure', 'credit exposure', 'protection bought = short credit (negative)'),
      { key: 'mid_spread_bp', label: 'mid (bp)', text: function (r) { return num(r.mid_spread_bp) == null ? 'unavailable' : T.fmt.num(r.mid_spread_bp, 2); },
        sort: function (r) { var n = num(r.mid_spread_bp); return n == null ? '' : n; } },
      { key: 'bucket', label: 'bucket', cls: 'l', text: function (r) { return r.bucket ? s(r.bucket) + '-like' : ''; } },
      mcol('cs01', 'CS01'), mcol('jtd', 'jump to default', 'what the contract gains (+) or loses (-) if the name defaults now at 40 % recovery'), mcol('value', 'value'),
      { key: 'level_kind', label: 'level / note', cls: 'l', html: function (r) {                  /* the dealing level with its basis; a note under it */
          var note = s(r.note || (r.quality && r.quality !== 'computed' ? r.quality : ''));
          return T.esc((LEVEL_WORDS[r.level_kind] || '') + (num(r.basis_bp) ? ' (' + bpSigned(r.basis_bp) + ')' : '')) + (note ? '<span class="sub2">' + T.esc(note) + '</span>' : '');
        }, text: function (r) { return (LEVEL_WORDS[r.level_kind] || '') + (r.note ? ' . ' + r.note : ''); } }] });
    p.appendChild(T.h('h3', null, 'by reference entity (all accounts in scope)'));
    var namesG = g(p, { rowKey: function (r) { return s(r.cik || r.ticker); }, empty: 'none', columns: [
      { key: 'name', label: 'reference entity', cls: 'l', text: function (r) { return s(r.entity_name || '') + (r.ticker ? ' (' + r.ticker + ')' : ''); } },
      { key: 'contracts', label: 'contracts', text: function (r) { return s(r.contracts); } },
      mcol('gross_notional', 'gross notional'), mcol('net_exposure', 'net exposure'), mcol('jtd', 'jump to default'),
      { key: 'jtd_pct_nav', label: 'JTD % NAV', text: function (r) { return num(r.jtd_pct_nav) == null ? 'n/a' : pct(r.jtd_pct_nav); }, sort: function (r) { var n = num(r.jtd_pct_nav); return n == null ? '' : n; } },
      mcol('cs01', 'CS01'), { key: 'level_kind', label: 'level', cls: 'l', text: function (r) { return LEVEL_WORDS[r.level_kind] || 'none'; } },
      mcol('eq_dn30_pnl', 'equity -30 %', 'this name\'s share price -30 % alone, spreads through the model'), mcol('eq_up30_pnl', 'equity +30 %'), mcol('eq_up50_pnl', 'equity +50 %')] });
    p.appendChild(T.h('h3', null, 'scenarios (full revaluation)'));
    var scenG = g(p, { rowKey: function (r) { return s(r.name); }, empty: 'none', columns: [
      { key: 'name', label: 'scenario', cls: 'l' }, mcol('pnl', 'P&L'), { key: 'note', label: 'note', cls: 'l', text: function (r) { return s(r.note || ''); } }] });
    var warnBox = T.h('div', { class: 'pnote' }); p.appendChild(warnBox);
    var note = T.h('div', { class: 'pnote' }); p.appendChild(note);
    if (container) { container.appendChild(p); }
    return {
      el: p,
      update: function (block) {
        p.hidden = !block;
        if (!block) { return; }
        if (block.error) { line.textContent = 'could not be measured: ' + s(block.error); }
        else {
          line.textContent = 'CS01 ' + signedMoney(block, 'cs01') + ' . jump to default: gain ' + money(block, 'jtd_gain') + ', loss ' + money(block, 'jtd_loss') + ', net ' + signedMoney(block, 'net_jtd') +
            ' . protection bought (short credit) ' + money(block, 'short_credit') + ' . sold (long credit) ' + money(block, 'long_credit') +
            ' . gross ' + (num(block.gross_pct_nav) != null ? pct(block.gross_pct_nav) : 'n/a') + ' of NAV' + ((block.missing || []).length ? ' . not valued now: ' + block.missing.join(', ') : '');
        }
        rowsG.update(block.rows || [], { empty: 'no single-name CDS contracts' });
        namesG.update(block.by_name || [], { empty: 'none' });
        scenG.update(block.scenarios || [], { empty: 'none' });
        T.clear(warnBox);
        (block.warnings || []).forEach(function (w) { warnBox.appendChild(T.h('div', { class: 'cn-warn', title: s(w.code) }, s(w.message || words(w.code)))); });
        note.textContent = s(block.note || '');
      }
    };
  }

  /* ---- entity rows (browse) -------------------------------------------------------------------------------------------------- */
  function isOffered(r) {
    if (r && (r.offered === true || r.offered === false)) { return r.offered; }
    var stt = s(r && r.status).toUpperCase(); return stt === 'COVERED' || stt === 'OFFERED' || stt === 'OK';
  }
  function isFinancial(r) { return !!r && (r.limited === true || s(r.flag).toUpperCase() === 'FINANCIAL'); }
  function flagWords(r) {
    if (r && has(r.flag_note)) { return s(r.flag_note); }          /* the server's words: 'financial (from filing tags: ...; SIC not checked yet)' */
    var f = s(r && r.flag).toUpperCase();
    if (!f) { return ''; }
    var what = f === 'FINANCIAL' ? 'financial' : (f === 'REIT' ? 'REIT' : f.toLowerCase().replace(/_/g, ' '));
    if (s(r.flag_source).toLowerCase() === 'sic') { return what + ' (SIC' + (r.sic ? ' ' + s(r.sic) : '') + (r.sic_desc ? ' ' + s(r.sic_desc).toLowerCase() : '') + ')'; }
    return what + ' (from filing tags; SIC not checked yet)';
  }
  function statusWords(r) {
    if (!r) { return ''; }
    var w = pick(r.status_words, r.status_text, r.reason_words, r.status_message);
    if (isOffered(r)) { return s(w || 'offered') + (isFinancial(r) ? ' (LIMITED)' : ''); }
    if (w) { return s(w); }
    var code = s(pick(r.status_reason, r.status));
    var f = REASON_WORDS[code];
    return f ? f(r) : (code ? 'not offered: ' + words(code) : 'not checked yet');
  }
  function ageClass(r) { var a = num(r && r.age_days); return (r && r.amber) || (a != null && a >= 183) ? 'cn-amber' : ''; }
  function ageText(r) { if (r && has(r.age_note)) { return s(r.age_note); } var a = num(r && r.age_days); return a == null ? '' : (a >= 183 ? Math.floor(a / 30.4) + ' months old' : ''); }
  function modelCell(r) {
    var m = models[r.cik] || r.model_5y || null;
    if (m && typeof m === 'object' && m.ok !== false && num(m.spread_bp) != null) { return bp(m.spread_bp) + (m.bucket ? ' ' + s(m.bucket) + '-like' : '') + (m.quality === 'distressed' ? ' (distressed)' : ''); }
    return null;
  }

  /* ---- the desk ---------------------------------------------------------------------------------------------------------------- */
  function mount(el, opts) {
    opts = opts || {};
    ensureCss();
    var account = opts.account_id || '';
    var kept = account ? memo[account] : null;
    var state = { sel: kept ? kept.sel : null, values: kept ? kept.values : null, q: kept ? kept.q : '', filter: kept ? kept.filter : 'all', page: kept ? kept.page : 0,
      sort: kept ? kept.sort : null, desk: null, deskError: null, list: null, listError: null, listSeq: 0, preview: null, previewSeq: 0, busy: false, retry: null,
      blocked: opts.blocked || null, dead: false, timer: null, searchTimer: null, error: null, errorField: null, assumeOpen: false, showModel: false, showAll: false, waitHealth: null,
      edits: {}, offeredOnly: kept && kept.offeredOnly === false ? false : true };                                   /* tier|term -> what is typed in the Assumptions panel, kept across the desk's refresh until saved */
    if (!state.values) { state.values = { tenor: DEFAULTS.tenor, side: DEFAULTS.side, notional: DEFAULTS.notional, coupon: DEFAULTS.coupon }; }
    function keep() { if (account) { memo[account] = { sel: state.sel, values: state.values, q: state.q, filter: state.filter, page: state.page, sort: state.sort, offeredOnly: state.offeredOnly }; } }
    keep();
    var root = T.h('div', { class: 'cdsnames' });
    T.clear(el).appendChild(root);
    var ui = {};
    root.addEventListener('keydown', function (ev) {
      if (T.keys && T.keys.is && T.keys.is(ev, 'ticket.submit')) { ev.preventDefault(); ev.stopPropagation(); if (ui.quoteBtn && !ui.quoteBtn.disabled) { onRequest(); } }
    });

    function alive() { return !state.dead && document.body.contains(root); }
    function visible() { return alive() && root.offsetParent !== null; }

    function build() {
      T.clear(root);
      if (!account) { root.appendChild(T.h('div', { class: 'error' }, 'no prime-broker account given to the single-name CDS desk')); return; }
      if (T.modulesKnown && !T.modulesKnown()) {
        root.appendChild(T.h('div', { class: 'muted' }, 'loading ...'));
        if (!state.waitHealth && T.state && T.state.on) { state.waitHealth = T.state.on('health', function () { if (state.waitHealth) { state.waitHealth(); state.waitHealth = null; } if (!state.dead) { build(); start(); } }); }
        return;
      }
      if (!modOk(MOD)) {
        root.appendChild(T.h('div', { class: 'empty' }, 'unavailable: the single-name CDS desk is not running in this build (no request is made)'));
        return;
      }
      ui.blocked = T.h('div', { class: 'conv cn-blocked', hidden: !state.blocked || opts.note === false }, state.blocked || '');
      root.appendChild(ui.blocked);
      ui.head = T.h('div', { class: 'cn-head' }); root.appendChild(ui.head);
      ui.assumeBtn = T.h('button', { class: 'btn small', title: 'the initial margin and the dealer half-spread per spread tier (assumed, editable)', onclick: function () {
        state.assumeOpen = !state.assumeOpen; renderAssume(); } }, 'Assumptions');
      ui.assume = T.h('div', { class: 'cn-assume', hidden: true }); root.appendChild(ui.assume);    /* under the header that holds its button */
      ui.ticket = T.h('div', { class: 'cn-ticket', hidden: true }); root.appendChild(ui.ticket);
      ui.search = T.h('div'); root.appendChild(ui.search);
      ui.list = T.h('div'); root.appendChild(ui.list);
      renderHead(); renderAssume(); renderTicket(); renderSearch(); renderList();
    }

    /* ---- header: MODEL line, the FRED ladder with its date, the universe, the PB's tier terms ---- */
    function renderHead() {
      if (!ui.head) { return; }
      T.clear(ui.head);
      ui.head.appendChild(T.h('div', null, T.h('span', { class: 'cn-model', title: s((state.desk || {}).model || '') }, MODEL_LINE),
        T.h('span', { class: 'lim', title: 'support LIMITED: no free single-name CDS quote exists; the spread is a MODEL, not validated against traded levels' }, 'LIMITED')));
      if (state.deskError) { ui.head.appendChild(T.h('div', { class: 'error' }, 'the single-name CDS desk could not be loaded: ' + state.deskError + ' - retrying')); return; }
      var d = state.desk;
      if (!d) { ui.head.appendChild(T.h('div', { class: 'muted' }, 'loading the FRED ladder, the reference entities and the dealer\'s terms ...')); return; }
      var lad = obj(d.ladder), rows = obj(lad.rows);
      if (lad.ok === false) {
        var lr = s(lad.reason || words(lad.code) || 'the FRED ladder is unavailable');   /* integration: the model's reason may already say 'no single-name CDS price' */
        ui.head.appendChild(T.h('div', { class: 'cn-bad', title: s(lad.code) }, /^no (single-name )?CDS price/i.test(lr) ? lr : 'no CDS price now: ' + lr));
      } else {
        var parts = LADDER.filter(function (b) { return rows[b] && num(rows[b].spread_bp) != null; }).map(function (b) { return b + ' ' + bpShort(rows[b].spread_bp); });
        var fl = obj(pick(d.floor, lad.floor));
        ui.head.appendChild(T.h('div', { title: 'ICE BofA US corporate OAS by rating (FRED), the rating-like buckets of the model spread; the AAA OAS is the floor' },
          'FRED ladder ' + (parts.length ? parts.join(' / ') + ' bp' : 'n/a') + (lad.as_of ? ' as of ' + s(lad.as_of) : '') +
          (fl.series_id ? ' . floor ' + s(fl.series_id) + ' ' + (has(fl.value_pct) ? s(fl.value_pct) + ' % = ' : '') + bp(fl.spread_bp) : '') +
          (num(fl.age_bd) != null ? ' (' + s(fl.age_bd) + ' business day' + (num(fl.age_bd) === 1 ? '' : 's') + ' old)' : '')));
      }
      ui.head.appendChild(universeLine(obj(pick(obj(state.list).universe, d.universe))));
      var tiers = obj(d.tiers), mult = num(d.otc_spread_mult);
      var tl = TIERS.filter(function (k) { return tiers[k]; }).map(function (k) {
        var t = tiers[k]; return s(t.words || tierWords(k)) + ' ' + pct(t.im_pct) + ' / ' + bp(pick(t.half_spread_eff_bp, t.half_spread_bp));
      });
      ui.head.appendChild(T.h('div', { class: 'cn-row' }, T.h('span', { class: 'muted', title: 'initial margin % of the notional and the dealer half-spread, by the rating-like bucket of the mid when the dealer quotes (assumed, editable)' },
        (d.broker ? s(d.broker) + ' ' : '') + 'IM / half-spread by tier: ' + (tl.length ? tl.join(' . ') : 'n/a') + (mult != null && mult !== 1 ? ' (half-spreads x ' + s(d.otc_spread_mult) + ')' : '') +
        ' . VM ' + (s(d.margin_mode).toUpperCase() === 'STM' ? 'settled to market' : 'collateralised to market') + ' (CSA)'), ui.assumeBtn));
      var bases = Array.isArray(d.bases) ? d.bases : [];
      if (bases.length) {
        ui.head.appendChild(T.h('div', { class: 'muted' }, 'your dealing levels in this account: ' + bases.map(function (b) {
          return s(b.ticker || b.name || b.cik) + ' ' + (b.level_kind === 'NEGOTIATED' ? 'negotiated mid ' + bp(b.negotiated_mid_bp) + ' (basis ' + bpSigned(b.basis_bp) + ')' : 'MODEL (confirmed)') +
            (whenOf(b, 'entered') ? ', set ' + whenOf(b, 'entered') : '');
        }).join(' . ')));
      }
    }

    /* the universe line: the server's words first ("1,7xx reference entities offered of ... listed, from SEC EDGAR, refreshed ..."), the counts
       by status in the hover; while the first run loads, its step */
    function universeLine(u) {
      var byStatus = obj(u.by_status);
      var title = Object.keys(byStatus).map(function (k) { return words(k) + ': ' + s(byStatus[k]); }).join('\n') + (u.source ? '\n' + s(u.source) : '');
      var st = s(u.state).toLowerCase();
      var loaded = st ? st === 'ready' || st === 'refreshing' : (num(u.offered) != null && (num(u.offered) > 0 || has(u.refreshed_ts)));
      var text = has(u.message) ? s(u.message) : (loaded ? T.fmt.qty(u.offered) + ' reference entities offered' + (num(u.listed) != null ? ' of ' + T.fmt.qty(u.listed) + ' listed' : '') +
        ' from SEC EDGAR' + (has(u.refreshed_ts) ? ', refreshed ' + T.fmt.dt(u.refreshed_ts) + ' ET' : '') : 'loading the reference-entity list from SEC EDGAR (first run, about 2-3 minutes)');
      if (u.error) { text += ' . the list could not be read: ' + s(u.error); }
      var p = obj(u.progress);
      if ((st === 'loading' || st === 'refreshing') && num(p.total)) { text += ' (' + s(p.step || 'step') + ' ' + s(p.done) + ' of ' + s(p.total) + ')'; }
      return T.h('div', { class: st === 'error' ? 'cn-bad' : (loaded ? '' : 'cn-warn'), title: title }, text);
    }
    function universeLoading() {
      var u = obj(pick(obj(state.list).universe, obj(state.desk).universe));
      var st = s(u.state).toLowerCase();
      return st ? st === 'loading' || st === 'empty' : (!num(u.offered) && !has(u.refreshed_ts));
    }

    /* ---- the Assumptions panel: five tiers x (IM %, half-spread bp), saved per tier ---- */
    function renderAssume() {
      if (!ui.assume) { return; }
      ui.assume.hidden = !state.assumeOpen;
      if (!state.assumeOpen) { return; }
      T.clear(ui.assume);
      var d = state.desk;
      if (!d) { ui.assume.appendChild(T.h('div', { class: 'muted' }, 'loading ...')); return; }
      var tiers = obj(d.tiers);
      ui.assume.appendChild(T.h('div', { class: 'cn-title' }, 'Single-name CDS assumptions of ' + s(d.broker || account) + ' (assumed, editable; every later quote and preview uses them; past postings are never restated)'));
      var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', { class: 'l' }, 'tier'), T.h('th', { class: 'l' }, 'buckets of the mid'),
        T.h('th', { class: 'num' }, 'IM % of notional'), T.h('th', { class: 'num' }, 'half-spread (bp)'), T.h('th', { class: 'num' }, 'effective half-spread'),
        T.h('th', { class: 'l' }, 'seed'), T.h('th', { class: 'l' }, 'source'), T.h('th', { class: 'l' }, ''))));
      var body = T.h('tbody');
      TIERS.forEach(function (k) {
        var t = obj(tiers[k]);
        var ek = k + '|im_pct', eh = k + '|half_spread_bp';
        var im = T.h('input', { type: 'text', spellcheck: 'false', value: ek in state.edits ? state.edits[ek] : fracToPct(t.im_pct), 'data-term': 'im_pct', 'data-tier': k });
        var hs = T.h('input', { type: 'text', spellcheck: 'false', value: eh in state.edits ? state.edits[eh] : s(t.half_spread_bp), 'data-term': 'half_spread_bp', 'data-tier': k });
        im.addEventListener('input', function () { state.edits[ek] = im.value; });
        hs.addEventListener('input', function () { state.edits[eh] = hs.value; });
        var err = T.h('span', { class: 'cn-bad' });
        var save = T.h('button', { class: 'btn small', disabled: !!state.blocked, onclick: function () {
          var body2 = { account_id: account, tier: k };
          var f = pctToFrac(im.value);
          if (f == null) { err.textContent = 'IM %: a percent such as 5 or 7.5'; return; }
          if (Number(f) !== Number(t.im_pct)) { body2.im_pct = f; }
          if (!/^\d+(\.\d+)?$/.test(clean(hs.value))) { err.textContent = 'half-spread: basis points such as 3 or 7.5'; return; }
          if (Number(clean(hs.value)) !== Number(t.half_spread_bp)) { body2.half_spread_bp = clean(hs.value); }
          if (!('im_pct' in body2) && !('half_spread_bp' in body2)) { T.ui.toast('nothing changed for ' + tierWords(k), 'ok', 3000); return; }
          save.disabled = true; err.textContent = '';
          T.api.post(R_TERMS, body2).then(function (r) {
            save.disabled = false;
            var ch = Object.keys(obj(r && r.after)).map(function (x) {                /* "IM 5.00 % -> 5.50 %", "half-spread 3.00 bp -> 4.00 bp" */
              var f = x === 'im_pct' ? pct : bp;
              return (x === 'im_pct' ? 'IM' : (x === 'half_spread_bp' ? 'half-spread' : x.replace(/_/g, ' '))) + ' ' + f(obj(r.before)[x]) + ' -> ' + f(r.after[x]);
            }).join(', ');
            T.ui.toast('Single-name CDS ' + tierWords(k) + ' assumptions saved: ' + (ch || 'no change'), 'ok', 6000);
            delete state.edits[ek]; delete state.edits[eh];
            state.preview = null; renderPreview(); loadDesk();
          }, function (e) {
            save.disabled = false;
            if (e && e.code === 'NOTHING_TO_UPDATE') { T.ui.toast('nothing changed', 'ok', 3000); return; }
            err.textContent = describe(e); err.title = (e && e.code) || '';
          });
        } }, 'Save');
        var seed = obj(t.seed);
        body.appendChild(T.h('tr', null, T.h('td', { class: 'l' }, s(t.words || tierWords(k))), T.h('td', { class: 'l' }, Array.isArray(t.buckets) ? t.buckets.join(', ') : s(t.buckets)),
          T.h('td', { class: 'num' }, im), T.h('td', { class: 'num' }, hs), T.h('td', { class: 'num' }, bp(t.half_spread_eff_bp)),
          T.h('td', { class: 'l' }, (has(seed.im_pct) ? pct(seed.im_pct) : 'n/a') + ' / ' + bp(seed.half_spread_bp)), T.h('td', { class: 'l' }, s(t.source || 'seed') + (whenOf(t, 'updated') ? ' ' + whenOf(t, 'updated') : '')),
          T.h('td', { class: 'l' }, save, ' ', err)));
      });
      tbl.appendChild(body);
      ui.assume.appendChild(T.h('div', { class: 'cn-wrap' }, tbl));
      ui.assume.appendChild(T.h('div', { class: 'hint' }, 'The tier is the rating-like bucket of the mid (the model plus your negotiated basis) when the dealer quotes; the default coupon follows the' +
        ' bucket of the MODEL spread before any basis. Reference points only: the CDX IM is 2 % (IG) / 6 % (HY); the BCBS-IOSCO schedule is 2 / 5 / 10 % by maturity.'));
    }

    /* ---- search and browse ---- */
    function renderSearch() {
      if (!ui.search) { return; }
      T.clear(ui.search);
      var entOk = modOk(ENT);
      var box = T.h('input', { type: 'text', spellcheck: 'false', autocomplete: 'off', placeholder: entOk ? 'ticker or company name' : 'a ticker, e.g. ORCL', value: state.q || '' });
      box.addEventListener('input', function () {
        state.q = box.value; state.page = 0; keep();
        if (state.searchTimer) { clearTimeout(state.searchTimer); }
        state.searchTimer = setTimeout(function () { state.searchTimer = null; loadList(); }, SEARCH_MS);
      });
      box.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' && !entOk && clean(box.value)) { ev.preventDefault(); openTicket({ ticker: clean(box.value).toUpperCase(), name: clean(box.value).toUpperCase() }); }
      });
      ui.searchBox = box;
      var fsel = T.h('select', { title: 'financial and REIT flags are known so far: from filing tags until a name\'s SIC is checked (on its first preview); a name with no flag counts as non-financial so far' });
      FILTERS.forEach(function (f) { fsel.appendChild(T.h('option', { value: f.key }, f.label)); });
      fsel.value = state.filter || 'all';
      fsel.addEventListener('change', function () { state.filter = fsel.value; state.page = 0; keep(); loadList(); });
      var off = T.h('input', { type: 'checkbox' });
      off.checked = state.offeredOnly !== false;
      off.addEventListener('change', function () { state.offeredOnly = off.checked; state.page = 0; keep(); loadList(); });
      ui.search.appendChild(T.h('div', { class: 'cn-search' }, T.h('b', null, 'Reference entities'), box, T.h('span', { class: 'muted' }, 'show'), fsel,
        T.h('label', { class: 'cn-row', title: 'browse the names a CDS is offered on; a search lists every matching listed name with its status (why one is not offered)' }, off,
          T.h('span', null, 'offered only (a search shows every name)')),
        T.h('span', { class: 'muted' }, 'flags known so far (filing tags until the SIC is checked)')));
      if (!entOk) { ui.search.appendChild(T.h('div', { class: 'hint' }, 'unavailable: the reference-entity list (SEC EDGAR) is not running in this build - type a ticker and press Enter to open its ticket')); }
    }
    function sortRows(rows) {
      var so = state.sort;
      if (!so || !so.key) { return rows; }
      var k = so.key, dir = so.dir === 'desc' ? -1 : 1;
      return rows.slice().sort(function (a, b) {
        var x = k === 'debt' ? T.num(a, 'debt') : s(a[k]).toUpperCase(), y = k === 'debt' ? T.num(b, 'debt') : s(b[k]).toUpperCase();
        if (x == null && y == null) { return 0; } if (x == null) { return 1; } if (y == null) { return -1; }
        return (x < y ? -1 : (x > y ? 1 : 0)) * dir;
      });
    }
    function renderList() {
      if (!ui.list) { return; }
      T.clear(ui.list);
      if (!modOk(ENT)) { return; }
      if (state.listError) { ui.list.appendChild(T.h('div', { class: 'error' }, 'the reference entities could not be loaded: ' + state.listError + ' - retrying with the next refresh')); }
      var L = state.list;
      if (!L) { ui.list.appendChild(T.h('div', { class: 'muted' }, 'loading the reference entities ...')); return; }
      var rows = Array.isArray(L.rows) ? L.rows : [];
      var total = num(L.total) == null ? rows.length : num(L.total);
      var serverSorted = !!(L.sort && state.sort && L.sort === state.sort.key && !!L.desc === (state.sort.dir === 'desc'));
      var shown = serverSorted ? rows : sortRows(rows);
      var from = (num(L.offset) || 0);
      var pages = Math.max(1, Math.ceil(total / PAGE));
      var prev = T.h('button', { class: 'btn small', disabled: from <= 0, onclick: function () { state.page = Math.max(0, state.page - 1); keep(); loadList(); } }, 'Previous');
      var next = T.h('button', { class: 'btn small', disabled: from + rows.length >= total, onclick: function () { state.page += 1; keep(); loadList(); } }, 'Next');
      ui.list.appendChild(T.h('div', { class: 'cn-pager' }, T.h('span', { class: 'muted' }, total ? (from + 1) + '-' + (from + rows.length) + ' of ' + T.fmt.qty(total) +
        (state.q ? ' matching "' + state.q + '"' : (L.offered_only || (state.offeredOnly !== false && !state.q) ? ' offered' : ' listed')) + ' . page ' + (Math.floor(from / PAGE) + 1) + ' of ' + pages :
        'no reference entity' + (state.q ? ' matches "' + state.q + '"' : ' yet')),
        prev, next, state.sort && !serverSorted ? T.h('span', { class: 'muted' }, '(sorted within this page)') : null));
      if (L.caption && state.filter && state.filter !== 'all') { ui.list.appendChild(T.h('div', { class: 'hint' }, s(L.caption))); }
      if (!rows.length) {
        if (universeLoading()) { ui.list.appendChild(universeLine(obj(pick(L.universe, obj(state.desk).universe)))); }
        return;
      }
      function sh(label, key, cls, title) {
        var on = state.sort && state.sort.key === key;
        return T.h('th', { class: (cls || 'l') + ' cn-sort', title: (title ? title + '; ' : '') + 'sort by ' + label, onclick: function () {
          state.sort = { key: key, dir: on && state.sort.dir === 'asc' ? 'desc' : 'asc' }; keep(); loadList(); } },
          label + (on ? (state.sort.dir === 'asc' ? ' (up)' : ' (down)') : ''));
      }
      var tbl = T.h('table', { class: 'grid small cn-browse' }, T.h('thead', null, T.h('tr', null, sh('ticker', 'ticker'), sh('company', 'name', 'l', 'the SEC conformed name'),
        sh('debt', 'debt', 'num', 'reported debt by the debt recipe (USD); the exact amount in the hover'), T.h('th', { class: 'l', title: 'the balance sheet the debt comes from (amber when 6-15 months old)' }, 'balance sheet'),
        T.h('th', { class: 'l' }, 'flags'), T.h('th', { class: 'l', title: 'the 5-year MODEL spread and its rating-like bucket, when computed in this session' }, '5Y model'),
        T.h('th', { class: 'l' }, 'status'))));
      var body = T.h('tbody');
      shown.forEach(function (r) {
        var sel = state.sel && ((state.sel.cik && state.sel.cik === r.cik) || (!state.sel.cik && state.sel.ticker === r.ticker));
        var mc = modelCell(r);
        var mcell = mc ? T.h('span', null, mc) : (modOk(MDL) && isOffered(r) ? T.h('a', { href: '#', title: 'compute this name\'s 5-year MODEL spread now (fetches its inputs the first time)', onclick: function (ev) {
          ev.preventDefault(); ev.stopPropagation(); requestModel(r, ev.target); } }, 'on request') : T.h('span', { class: 'muted' }, 'on request'));
        var tr = T.h('tr', { class: sel ? 'cn-sel' : '', title: 'open the ticket of ' + s(r.name), onclick: function () { openTicket(r); } },
          T.h('td', { class: 'l' }, T.h('b', null, s(r.ticker)), r.held ? T.h('span', { class: 'lim', title: 'this account holds a contract on this name' }, 'held') : null),
          T.h('td', { class: 'l cn-wide' }, s(r.name)),
          T.h('td', { class: 'num', title: hasMoney(r, 'debt') ? 'USD ' + money(r, 'debt') + (r.debt_recipe ? ' by ' + s(r.debt_recipe) : '') + (r.debt_text ? ': ' + s(r.debt_text) : '') : '' },
            has(r.debt_short) ? s(r.debt_short).replace(/^USD /, '') : (hasMoney(r, 'debt') ? T.fmt.compact(T.num(r, 'debt')) : 'n/a')),
          T.h('td', { class: 'l ' + ageClass(r), title: ageText(r) }, s(r.debt_end || '')),
          T.h('td', { class: 'l cn-wide' }, flagWords(r)),
          T.h('td', { class: 'l' }, mcell),
          T.h('td', { class: 'l cn-wide' + (isOffered(r) ? '' : ' muted'), title: s(r.status_reason || r.status) }, statusWords(r)));
        body.appendChild(tr);
      });
      tbl.appendChild(body);
      ui.list.appendChild(T.h('div', { class: 'cn-wrap' }, tbl));
    }
    function requestModel(r, link, tries) {
      if (!modOk(MDL)) { return; }
      tries = tries || 0;
      if (link) { link.textContent = tries ? 'fetching ' + s(r.ticker) + '\'s inputs (' + tries + ') ...' : 'computing ...'; }
      T.api.get(R_MODEL, { cik: r.cik, tenor_years: 5 }).then(function (m) {
        if (!alive()) { return; }
        if (m && m.pending) {
          if (tries + 1 >= RETRY_MAX) { if (link) { link.textContent = 'still fetching - try again'; } return; }
          setTimeout(function () { if (alive()) { requestModel(r, link, tries + 1); } }, m.retry_ms || 1500);
          return;
        }
        if (m && m.ok === false) { if (link) { link.textContent = 'unavailable'; link.title = s(m.message || m.reason || words(m.code)); } return; }
        if (m && num(m.spread_bp) != null) { models[r.cik] = m; }
        renderList();
      }, function (e) { if (link) { link.textContent = 'unavailable'; link.title = describe(e); } });
    }

    /* ---- the ticket ---- */
    function openTicket(r) {
      state.sel = { cik: r.cik == null ? null : r.cik, ticker: s(r.ticker), name: s(r.name || r.ticker), row: r };
      state.preview = null; state.error = null; state.errorField = null;
      keep();
      renderTicket(); renderList();
      if (ui.ticket && ui.ticket.scrollIntoView) { try { ui.ticket.scrollIntoView({ block: 'nearest' }); } catch (e) { /* a convenience */ } }
    }
    function selLevel() {
      var d = state.desk, sel = state.sel;
      if (!d || !sel || !Array.isArray(d.bases)) { return null; }
      for (var i = 0; i < d.bases.length; i++) {
        var b = d.bases[i];
        if ((sel.cik != null && String(b.cik) === String(sel.cik)) || (sel.cik == null && sel.ticker && b.ticker === sel.ticker)) { return b; }
      }
      return null;
    }
    function maturityOf(n) { var m = obj(obj((state.desk || {}).roll).maturities); return s(m[String(n)] || ''); }
    function readTerms() {
      var v = state.values, sel = state.sel;
      if (!sel) { return { error: 'pick a reference entity first (search or browse below)' }; }
      var n = clean(v.notional);
      if (!/^\d+(\.\d{1,2})?$/.test(n)) { return { error: 'notional: a USD amount with at most 2 decimals, e.g. 5,000,000.00', field: 'notional' }; }
      if (Number(n) < 100000) { return { error: 'notional: at least 100,000.00', field: 'notional' }; }
      if (Number(n) > 1000000000) { return { error: 'notional: at most 1,000,000,000.00', field: 'notional' }; }
      var terms = { tenor_years: Number(v.tenor) || 5, side: v.side, notional: n };
      if (sel.cik != null) { terms.cik = sel.cik; }
      if (sel.ticker) { terms.ticker = sel.ticker; }     /* B1-R2: cik AND ticker, so a REQUESTED quote reads 'ORCL 5Y CDS' before the dealer prices it */
      if (v.coupon === '100' || v.coupon === '500') { terms.coupon_bp = Number(v.coupon); }
      return { terms: terms };
    }
    function setStatus(text) { if (ui.status) { ui.status.textContent = text || ''; ui.status.hidden = !text; } }
    function setError(text, field) {
      state.error = text || null; state.errorField = field || null;
      if (ui.error) { ui.error.textContent = state.error || ''; ui.error.hidden = !state.error; }
      if (ui.form) {
        T.qa('.bad', ui.form).forEach(function (x) { x.classList.remove('bad'); });
        if (field) { var b = ui.form.querySelector('[data-field="' + field + '"]'); if (b) { b.classList.add('bad'); } }
      }
    }
    function bindField(name, input) {
      input.setAttribute('data-field', name);
      input.value = s(state.values[name]);
      function upd() {
        state.values[name] = input.value; keep();
        if (state.errorField === name) { setError(null, null); }
        var p = state.preview;
        if (p && !p.stale) { var r = readTerms(); if (!r.terms || JSON.stringify(r.terms) !== p.sig) { p.stale = true; renderPreview(); } }
        if (name === 'tenor') { renderMaturity(); }
      }
      input.addEventListener('input', upd);
      input.addEventListener('change', upd);
      input.addEventListener('keydown', function (ev) { if (T.keys && T.keys.is && T.keys.is(ev, 'ticket.preview')) { ev.preventDefault(); ev.stopPropagation(); onPreview(); } });
      if (state.errorField === name) { input.classList.add('bad'); }
      return input;
    }
    function renderMaturity() { if (ui.maturity) { var m = maturityOf(state.values.tenor); ui.maturity.textContent = m ? 'matures ' + m + ' (20 Jun / 20 Dec, the semi-annual roll)' : ''; } }
    function entityBlock() {
      var sel = state.sel; var pv = state.preview && state.preview.pv;
      var e = obj(pv && pv.entity), r = obj(sel && sel.row);
      var f = obj(e.fundamentals);
      var mi = obj(obj(pv && pv.model).inputs);
      var ent = Object.keys(e).length ? e : r;          /* the preview's entity once previewed, else the browse row */
      var name = s(pick(e.name, r.name, sel && sel.name));
      var tk = s(pick(e.ticker, r.ticker, sel && sel.ticker));
      var cik = pick(e.cik, r.cik, sel && sel.cik);
      var fw = flagWords(e.flag || e.flag_note ? e : r);
      var line1 = T.h('div', null, T.h('b', null, name + (tk ? ' (' + tk + ')' : '')), (has(cik) ? ' . CIK ' + s(cik) : '') + (pick(e.exchange, r.exchange) ? ' . ' + s(pick(e.exchange, r.exchange)) : '') +
        (fw ? ' . ' + fw : '') + ' . ' + statusWords(e.status ? e : r));
      var src = Object.keys(f).length ? f : r;          /* fundamentals: the entity's (C-ENT fundamentals_now) or the browse row's copy */
      var debtRow = hasMoney(src, 'debt') ? src : (hasMoney(mi, 'debt') ? mi : r);
      var end = s(pick(src.end_date, src.debt_end, mi.debt_end, r.debt_end));
      var recipe = s(pick(src.debt_recipe, mi.debt_recipe, r.debt_recipe));
      var form = s(pick(src.form, mi.debt_form));
      var shares = pick(src.shares, mi.shares, r.shares);
      var line2 = T.h('div', null, 'debt ' + usd(debtRow, 'debt') + (recipe ? ' by recipe ' + recipe : '') +
        (has(src.debt_text) ? ': ' + s(src.debt_text) : (form || end ? ' (' + [form, end ? 'balance sheet ' + end : ''].filter(Boolean).join(', ') + ')' : '')) +
        (ageText(src) ? ' . ' + ageText(src) : ''));
      if (ageClass(src)) { line2.className = 'cn-amber'; }
      var line3 = T.h('div', null, (has(shares) ? 'shares ' + T.fmt.qty(shares) + (pick(src.shares_label, src.shares_tag) ? ' (' + s(pick(src.shares_label, src.shares_tag)) +
        (src.shares_end ? ' ' + s(src.shares_end) : '') + ')' : '') : 'shares n/a') + (has(mi.dps) ? ' . debt / share ' + T.fmt.num(mi.dps, 2) : '') +
        (has(mi.shares_adj_note) ? ' . ' + s(mi.shares_adj_note) : ''));
      var box = T.h('div', { class: 'cn-entity' }, line1, line2, line3);
      if (!has(src.debt_text)) {                         /* the parts, when the server gave no sentence for them */
        var partsSrc = obj(pick(src.debt_parts, mi.debt_parts));
        var parts = Object.keys(partsSrc).map(function (k) { var v = partsSrc[k]; return k + ' ' + (typeof v === 'number' ? T.fmt.cents(v) : T.fmt.money(v)); });
        (Array.isArray(r.debt_parts_list) && !parts.length ? r.debt_parts_list : []).forEach(function (x) { parts.push(s(x.tag) + ' ' + money(x, 'value')); });
        if (parts.length) { box.appendChild(T.h('div', { class: 'cn-parts' }, parts.join(' + '))); }
      }
      if (has(src.debt_note)) { box.appendChild(T.h('div', { class: 'cn-parts' }, s(src.debt_note))); }
      if (isFinancial(ent)) { box.appendChild(T.h('div', { class: 'cn-warn' }, s(ent.limited_reason || FIN_CAVEAT))); }
      if (has(e.check_note)) { box.appendChild(T.h('div', { class: 'cn-parts' }, s(e.check_note))); }
      return box;
    }
    function renderTicket() {
      if (!ui.ticket) { return; }
      T.clear(ui.ticket);
      var sel = state.sel;
      ui.ticket.hidden = !sel;
      if (!sel) { return; }
      ui.ticket.appendChild(T.h('div', { class: 'cn-row' }, T.h('span', { class: 'cn-title' }, 'Single-name CDS ticket'), T.h('span', { class: 'cn-spacer' }),
        T.h('a', { href: '#', title: 'close the ticket (the list stays)', onclick: function (ev) { ev.preventDefault(); state.sel = null; state.preview = null; keep(); renderTicket(); renderList(); } }, 'close')));
      ui.ticket.appendChild(T.h('div', { class: 'muted' }, 'reference: ' + OBLIGATION));
      ui.entity = T.h('div'); ui.ticket.appendChild(ui.entity);
      ui.entity.appendChild(entityBlock());
      ui.form = T.h('div'); ui.ticket.appendChild(ui.form);
      var tenor = T.h('select');
      var tenors = Array.isArray((state.desk || {}).tenors) && state.desk.tenors.length ? state.desk.tenors : TENORS;
      tenors.forEach(function (n) { var m = maturityOf(n); tenor.appendChild(T.h('option', { value: String(n) }, n + 'Y' + (m ? ' (' + m + ')' : ''))); });
      var side = T.h('select');
      [['BUY_PROTECTION', 'buy protection (pay the coupon; short credit)'], ['SELL_PROTECTION', 'sell protection (receive the coupon; long credit)']].forEach(function (o) {
        side.appendChild(T.h('option', { value: o[0] }, o[1]));
      });
      var cpn = T.h('select');
      var pv = state.preview && state.preview.pv;
      var dflt = pv && has(pv.coupon_default_bp) ? s(pv.coupon_default_bp) : (pv && /default/i.test(s(pv.coupon_rule)) && has(pv.coupon_bp) ? s(pv.coupon_bp) : '');
      cpn.appendChild(T.h('option', { value: '' }, 'default' + (dflt ? ': ' + dflt + ' bp' : '') + ' (from the MODEL bucket)'));
      cpn.appendChild(T.h('option', { value: '100' }, '100 bp'));
      cpn.appendChild(T.h('option', { value: '500' }, '500 bp'));
      var notional = T.h('input', { type: 'text', spellcheck: 'false', autocomplete: 'off', placeholder: '5,000,000.00' });
      var rows = [T.field('tenor', bindField('tenor', tenor), 'standard maturities: the 20 June / 20 December after the semi-annual roll'),
        T.field('side', bindField('side', side), 'a protection buyer pays the fixed coupon and is paid (1 - recovery) x the notional on a credit event: it gains when the spread widens'),
        T.field('notional (USD)', bindField('notional', notional), '100,000.00 to 1,000,000,000.00'),
        T.field('coupon', bindField('coupon', cpn), 'the standard 100 or 500 bp running coupon; by default 100 bp unless the MODEL spread\'s bucket (before any negotiated basis) is BB-like or worse')];
      T.clear(ui.form).appendChild(T.form(rows));
      ui.maturity = T.h('div', { class: 'hint' }); ui.form.appendChild(ui.maturity); renderMaturity();
      var lvl = selLevel();
      if (lvl && lvl.level_kind === 'NEGOTIATED') {
        ui.form.appendChild(T.h('div', { class: 'hint' }, 'With your negotiated mid the coupon is still the standard contract\'s: the default follows the MODEL bucket before your basis' +
          (dflt ? ' (' + dflt + ' bp)' : '') + '; choose 100 or 500 bp to override it.'));
      }
      ui.previewBtn = T.h('button', { class: 'btn primary', onclick: function () { onPreview(); } }, 'Preview');
      ui.quoteBtn = T.h('button', { class: 'btn', disabled: true, onclick: function () { onRequest(); } }, 'Request quote');
      ui.midBtn = T.h('button', { class: 'btn small', title: 'set your negotiated mid for this name in this account (stored as a basis to the model)', onclick: function () { midDialog(); } }, 'Negotiated mid ...');
      ui.modelBtn = T.h('button', { class: 'btn small', title: 'confirm dealing at the MODEL for this name in this account - no market level known', onclick: function () { modelDialog(); } }, 'Deal at the MODEL ...');
      ui.resetBtn = T.h('button', { class: 'btn small', title: 'put this form back to its defaults', onclick: function () {
        state.values = { tenor: DEFAULTS.tenor, side: DEFAULTS.side, notional: DEFAULTS.notional, coupon: DEFAULTS.coupon }; state.preview = null; keep(); setError(null, null); renderTicket(); } }, 'Reset terms');
      ui.ticket.appendChild(T.h('div', { class: 'cn-actions' }, ui.previewBtn, ui.quoteBtn, ui.midBtn, ui.modelBtn, ui.resetBtn));
      ui.levelLine = T.h('div', { class: 'muted' }); ui.ticket.appendChild(ui.levelLine);
      ui.status = T.h('div', { class: 'hint', hidden: true }); ui.ticket.appendChild(ui.status);
      ui.error = T.h('div', { class: 'error', hidden: true }); ui.ticket.appendChild(ui.error);
      ui.preview = T.h('div'); ui.ticket.appendChild(ui.preview);
      ui.result = T.h('div', { class: 'conv', hidden: true }); ui.ticket.appendChild(ui.result);
      if (state.error) { setError(state.error, state.errorField); }
      renderLevelLine(); renderPreview(); paintButtons();
    }
    function renderLevelLine() {
      if (!ui.levelLine) { return; }
      var b = selLevel();
      ui.levelLine.textContent = !b ? 'dealing level for this name in this account: not set - a new deal needs your negotiated mid or your confirmation to deal at the MODEL' :
        (b.level_kind === 'NEGOTIATED' ? 'dealing level: your negotiated mid ' + bp(b.negotiated_mid_bp) + ' (basis ' + bpSigned(b.basis_bp) + ' to the model ' + bp(b.model_mid_bp) + ' when set' +
          (whenOf(b, 'entered') ? ', ' + whenOf(b, 'entered') : '') + '); it moves with the model from then on' :
          'dealing level: MODEL (you confirmed dealing at the model' + (whenOf(b, 'entered') ? ' on ' + whenOf(b, 'entered') : '') + ')');
    }
    function paintButtons() {
      var p = state.preview, pv = p && p.pv;
      var blockers = (pv && pv.blockers) || [];
      if (ui.previewBtn) { ui.previewBtn.disabled = !!state.blocked || state.busy || !state.sel; }
      if (ui.midBtn) { ui.midBtn.disabled = !!state.blocked || !state.sel; }
      if (ui.modelBtn) { ui.modelBtn.disabled = !!state.blocked || !state.sel; }
      if (ui.quoteBtn) {
        ui.quoteBtn.disabled = !!state.blocked || !pv || p.stale || pv.bookable === false || !!blockers.length || state.busy;
        ui.quoteBtn.title = ui.quoteBtn.disabled ? (state.blocked || (!pv ? 'preview first' : (p.stale ? 'the terms changed: preview again' : s(blockers[0] ? (blockers[0].message || words(blockers[0].code)) : 'not bookable')))) :
          'asks the simulated dealer of the prime broker for a firm quote (about 30 s; good for 120 s)' + (T.keys && T.keys.paren ? T.keys.paren('ticket.submit') : '');
      }
    }

    /* ---- preview (202 while the name's inputs are fetched: retried, at most 20 times) ---- */
    function onPreview(tries) {
      if (state.blocked) { setError(state.blocked, null); return; }
      var r = readTerms();
      if (r.error) { setError(r.error, r.field); return; }
      setError(null, null);
      tries = tries || 0;
      if (!tries && state.retry) { clearTimeout(state.retry); state.retry = null; }
      state.busy = true; paintButtons();
      var seq = tries ? state.previewSeq : ++state.previewSeq;
      if (!tries) { setStatus('pricing ' + s(state.sel.name) + ' from the MODEL spread and the USD curve ...'); }
      T.api.post(R_PREVIEW, { account_id: account, terms: r.terms }).then(function (pv) {
        if (state.dead || seq !== state.previewSeq) { return; }
        if (pv && pv.pending) {
          var n = tries + 1;
          var body = obj(pv.body);
          if (n >= RETRY_MAX) { state.busy = false; setStatus(''); setError('still fetching - try again', null); paintButtons(); return; }
          setStatus(s(body.message || ('fetching ' + s(state.sel.name) + '\'s SEC filings and 1,000 daily closes (first time today only)')) + ' - try ' + n + ' of ' + RETRY_MAX);
          state.retry = setTimeout(function () { state.retry = null; if (alive()) { onPreview(n); } }, num(pv.retry_ms) || 1500);
          return;
        }
        state.busy = false; setStatus('');
        if (pv && pv.refused) {                    /* V104R2-BW-05: a refused preview answers 200 with its reason - said in words, never a code */
          state.preview = { pv: pv, sig: JSON.stringify(r.terms), terms: r.terms, stale: false, refused: true };
          renderPreview(); paintButtons();
          return;
        }
        state.preview = { pv: pv || {}, sig: JSON.stringify(r.terms), terms: r.terms, stale: false };
        var m = obj(pv && pv.model);
        if (state.sel && state.sel.cik != null && Number(r.terms.tenor_years) === 5 && num(m.spread_bp) != null) { models[state.sel.cik] = m; }
        if (ui.entity) { T.clear(ui.entity).appendChild(entityBlock()); }
        if (ui.form) {                             /* the default coupon is known now: re-label the select in place */
          var sel = ui.form.querySelector('[data-field="coupon"]');
          var dflt = has(pv.coupon_default_bp) ? s(pv.coupon_default_bp) : (/default/i.test(s(pv.coupon_rule)) && has(pv.coupon_bp) ? s(pv.coupon_bp) : '');
          if (sel && sel.options && sel.options[0]) { sel.options[0].textContent = 'default' + (dflt ? ': ' + dflt + ' bp' : '') + ' (from the MODEL bucket)'; }
        }
        renderPreview(); paintButtons(); renderList();
      }, function (e) {
        if (state.dead || seq !== state.previewSeq) { return; }
        state.busy = false; setStatus('');
        setError(describe(e), e && e.details && e.details.field);
        state.preview = null;
        renderPreview(); paintButtons();
      });
    }
    function blockerBox(blockers) {
      var box = T.h('div', { class: 'cn-guard' });
      var guard = false, distressed = false;
      blockers.forEach(function (b) {
        if (b.code === 'DEALING_LEVEL_UNCONFIRMED') { guard = true; }
        if (b.code === 'DISTRESSED_NEEDS_NEGOTIATED_MID') { distressed = true; }
        box.appendChild(T.h('div', { class: 'cn-bad', title: s(b.code) }, s(b.message || words(b.code))));   /* V104R2-BW-04: words; the code in the hover */
      });
      if (distressed) {                               /* a distressed name: a MODEL confirmation is not enough (plan 3.5, Q4) */
        box.appendChild(T.h('div', { class: 'cn-actions' },
          T.h('button', { class: 'btn small primary', disabled: !!state.blocked, onclick: function () { midDialog(); } }, 'Negotiated mid ...'),
          T.h('span', { class: 'hint' }, 'not bookable until you set your negotiated mid for this name in this account; dealing at the MODEL is not offered for a distressed name')));
      } else if (guard) {
        box.appendChild(T.h('div', { class: 'cn-actions' },
          T.h('button', { class: 'btn small primary', disabled: !!state.blocked, onclick: function () { midDialog(); } }, 'Negotiated mid ...'),
          T.h('button', { class: 'btn small', disabled: !!state.blocked, onclick: function () { modelDialog(); } }, 'Deal at the MODEL ...'),
          T.h('span', { class: 'hint' }, 'not bookable until you set your negotiated mid, or confirm dealing at the MODEL, for this name in this account')));
      }
      return box;
    }
    function modelRows(m) {
      m = obj(m);
      var i = obj(m.inputs), fl = obj(m.floor);
      var out = [];
      out.push(['MODEL spread', T.h('span', null, T.h('b', null, bp(m.spread_bp)), ' = raw CreditGrades ' + bp(m.raw_bp) + ' + AAA floor ' + bp(pick(m.floor_bp, fl.spread_bp)) +
        (m.bucket_label ? ' . ' + s(m.bucket_label) : (m.bucket ? ' . ' + s(m.bucket) + '-like (model spread bucket - not a credit rating)' : '')) +
        (m.quality === 'distressed' ? ' . distressed - outside the model\'s calibrated range' : '') + ' (' + s(m.label || 'MODEL') + ')'), s(m.model || '')]);
      out.push(['share price', has(i.S) ? s(i.S) + ' (' + [i.S_source, i.S_quality].filter(has).join(', ') + ')' : 'n/a']);
      out.push(['volatility', has(i.sigma) ? pct(i.sigma) + ' 1000-day realised' + (has(i.sigma_window) ? ' (' + s(i.sigma_window) + ' daily returns' + (has(i.sigma_through) ? ' through ' + s(i.sigma_through) : '') + ')' : '') +
        (has(i.sigma_source) ? ' . ' + s(i.sigma_source) : '') : 'n/a']);
      out.push(['debt per share', (has(i.dps) ? T.fmt.num(i.dps, 4) : 'n/a') + ' = debt ' + usd(i, 'debt') + ' / ' + (has(i.shares) ? T.fmt.qty(i.shares) + ' shares' : 'n/a') +
        (has(i.debt_end) ? ' (balance sheet ' + s(i.debt_end) + (has(i.debt_form) ? ', ' + s(i.debt_form) : '') + (has(i.debt_known_from) ? ', known from ' + s(i.debt_known_from) : '') + ')' : '') +
        (has(i.shares_adj_note) ? ' . ' + s(i.shares_adj_note) : '')]);
      out.push(['risk-free rate', has(i.r) ? (num(i.r) != null && Math.abs(num(i.r)) < 1 ? pct(i.r) : s(i.r)) + ' continuous, to the maturity' + (has(i.curve_as_of) ? ' (USD Treasury curve as of ' + s(i.curve_as_of) + ')' : '') : 'n/a']);
      out.push(['floor', fl.series_id ? s(fl.series_id) + ' ' + (has(fl.value_pct) ? s(fl.value_pct) + ' % = ' : '') + bp(fl.spread_bp) + (fl.as_of ? ' as of ' + s(fl.as_of) : '') +
        (num(fl.age_bd) != null ? ' (' + s(fl.age_bd) + ' business day' + (num(fl.age_bd) === 1 ? '' : 's') + ' old' + (fl.source ? ', ' + s(fl.source) : ', FRED') + ')' : '') :
        'ICE BofA AAA US corporate OAS (FRED) ' + bp(m.floor_bp) + (m.floor_as_of ? ' as of ' + s(m.floor_as_of) : '')]);
      out.push(['parameters', 'L-bar ' + s(pick(i.Lbar, '0.5')) + ', lambda ' + s(pick(i.lambda, '0.3')) + ', recovery ' + s(pick(i.R, '0.40')) + ' . valuation ' + s(m.valuation_date || '') +
        ' . maturity ' + s(m.maturity_date || '') + (num(m.T) != null ? ' (T ' + T.fmt.num(m.T, 4) + ' years)' : '') + ' . ' + s(m.method || '')]);
      if (has(i.flag_note)) { out.push(['flag', s(i.flag_note)]); }
      return out;
    }
    function intermediateRows(m) {
      var x = obj(obj(m).intermediate);
      var keys = [['LD', 'LD = L-bar x D (the default barrier per share)'], ['sigma_A', 'sigma (the asset volatility)'], ['d', 'd (the distance factor)'],
        ['A_T', 'A(T)'], ['P0', 'P(0): survival now'], ['PT', 'P(T): survival to the maturity'], ['xi', 'xi'], ['z', 'z'], ['G_xi', 'G(xi)'],
        ['G_T_xi', 'G(T + xi)'], ['H', 'H'], ['I', 'I: the integral of e^(-rs) P(s) ds (quadrature)'], ['c', 'c*: the par spread (continuous premium)']];
      return keys.filter(function (k) { return has(x[k[0]]); }).map(function (k) { return [k[1], s(x[k[0]])]; });
    }
    function levelWords(pv) {
      var lv = pv.level, kind = typeof lv === 'string' ? lv : obj(lv).level_kind || obj(lv).kind;
      var b = obj(pv.basis), L = obj(lv);
      if (L.rule) { return s(L.rule); }                /* the server's sentence: "mid 84.00 bp = model 65.47 bp + your negotiated basis ..." */
      if (L.text) { return s(L.text); }
      var model = pick(obj(pv.model).spread_bp, pv.model_spread_bp, L.model_mid_bp);
      var basis = pick(pv.basis_bp, b.basis_bp, L.basis_bp);
      var when = whenOf(L, 'entered') || whenOf(b, 'entered');
      if (kind === 'NEGOTIATED') { return 'mid ' + bp(pv.mid_spread_bp) + ' = model ' + bp(model) + ' + your negotiated basis ' + bpSigned(basis) + (when ? ' (set ' + when + ')' : ''); }
      if (kind === 'MODEL') { return 'mid ' + bp(pv.mid_spread_bp) + ' = MODEL (you confirmed dealing at the model' + (when ? ' on ' + when : '') + ')'; }
      return 'not set - not bookable: set your negotiated mid, or confirm dealing at the MODEL';
    }
    function scheduleTable(pv) {
      var rows = pv.schedule || [];
      if (!rows.length) { return null; }
      var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'pay date'), T.h('th', null, 'accrual period'), T.h('th', { class: 'num' }, 'days'),
        T.h('th', { class: 'num' }, 'coupon (USD)'), T.h('th', null, ''))));
      var body = T.h('tbody');
      var show = state.showAll ? rows : rows.slice(0, 4).concat(rows.length > 5 ? [null] : []).concat(rows.length > 4 ? [rows[rows.length - 1]] : []);
      show.forEach(function (x) {
        if (x === null) {
          body.appendChild(T.h('tr', null, T.h('td', { colspan: '5', class: 'muted' }, T.h('a', { href: '#', onclick: function (ev) { ev.preventDefault(); state.showAll = true; renderPreview(); } },
            '... ' + (rows.length - 5) + ' more coupons (show all)'))));
          return;
        }
        body.appendChild(T.h('tr', null, T.h('td', null, s(x.pay_date)), T.h('td', null, s(x.start) + ' .. ' + s(x.end)), T.h('td', { class: 'num' }, s(x.days)), T.h('td', { class: 'num' }, money(x, 'amount')),
          T.h('td', { class: 'muted' }, s(x.direction || ''))));
      });
      tbl.appendChild(body);
      return T.h('div', { class: 'cn-wrap' }, tbl);
    }
    function headline(pv) {
      var reb = obj(pv.accrued_rebate), ip = obj(pv.initial_payment), cp = obj(pv.coupon), m = obj(pv.model), fund = obj(pv.funding);
      var buy = pv.side === 'BUY_PROTECTION';
      var out = [['deal', s(pv.side_text || sideText(pv.side)) + ' . USD ' + money(pv, 'notional') + ' . ' + (has(pv.tenor_years) ? s(pv.tenor_years) + 'Y' : '') +
        (pv.maturity_date ? ' to ' + s(pv.maturity_date) : '') + (pv.roll_date ? ' (rolled ' + s(pv.roll_date) + ')' : '')],
        ['counterparty', s(pv.broker || '') + ' (prime broker) . ' + s(pv.relationship || 'ISDA Master / CSA / PBA')],
        ['reference entity', s(pick(pv.entity_label, obj(pv.entity).name, state.sel && state.sel.name)) + ' . ' + s(pv.obligation || OBLIGATION)]];
      modelRows(m).forEach(function (r) { out.push(r); });
      out.push(['dealing level', levelWords(pv)]);
      if (num(pv.basis_bp) != null || num(obj(pv.basis).basis_bp) != null) { out.push(['negotiated basis', bpSigned(pick(pv.basis_bp, obj(pv.basis).basis_bp)) + ' (your negotiated mid less the model; held while the model moves)']); }
      out.push(['mid', bp(pv.mid_spread_bp) + (pv.tier ? ' . tier ' + s(pv.tier_words || tierWords(pv.tier)) : '') + (pv.mid_bucket ? ' (' + s(pv.mid_bucket) + '-like mid)' : '')]);
      out.push(['dealt spread', bp(pv.deal_spread_bp) + ' = mid ' + (buy ? '+' : '-') + ' half-spread ' + bp(pick(pv.half_spread_eff_bp, pv.half_spread_bp)) +
        (num(pv.otc_spread_mult) != null && num(pv.otc_spread_mult) !== 1 ? ' (' + bp(pv.half_spread_bp) + ' x ' + s(pv.otc_spread_mult) + ')' : '') + ' (assumed, against you)', s(pv.spread_rule || '')]);
      var upN = T.num(pv, 'upfront');
      out.push(['upfront', T.h('b', null, has(pv.upfront_direction) ? s(pv.upfront_direction) : ('you ' + (upN != null && upN > 0 ? 'pay' : 'receive') + ' ' + absUsd(pv, 'upfront')),
        has(pv.upfront_pct) ? ' (' + s(pv.upfront_pct) + ' % of notional, protection buyer\'s view)' : ''), s(pv.upfront_rule || '')]);
      if (has(pv.price)) { out.push(['price', s(pv.price) + ' (100 - upfront %)' + (has(pv.mid_price) ? ' . mid ' + s(pv.mid_price) : '')]); }
      if (Object.keys(reb).length) { out.push(['accrued rebate', money(reb, 'amount') + ' for ' + s(reb.days) + ' days (' + s(reb.from) + ' to ' + s(reb.through) + ' inclusive) ' + s(reb.direction || ''), s(reb.rule || '')]); }
      if (Object.keys(ip).length) { out.push(['initial payment', T.h('b', null, absUsd(ip, 'amount') + ' ' + s(ip.direction || '') + (ip.settle_date ? ' on ' + s(ip.settle_date) : '')), s(ip.rule || '')]); }
      out.push(['coupon', bp(pv.coupon_bp) + ' running' + (pv.coupon_rule ? ' (' + s(pv.coupon_rule) + ')' : '') + ', ' + s(cp.basis || 'ACT/360') + ', ' + s(cp.dates || '20 Mar / Jun / Sep / Dec, next business day') +
        (cp.first_date ? ' . first ' + s(cp.first_date) + ': USD ' + money(cp, 'first_amount') + ' (full first coupon)' : '') + (has(cp.count) ? ' . ' + s(cp.count) + ' coupons' : ''), s(cp.first_rule || '')]);
      out.push(['value at mid', signedMoney(pv, 'mark0') + ' (the contract\'s mark; daily variation margin moves its changes in cash)']);
      out.push(['cost of dealing', money(pv, 'cost_of_dealing') + ' (the day-one result: the dealt spread against the mid)']);
      out.push(['CS01', signedMoney(pv, 'cs01') + ' per +1 bp of the spread', s(pv.cs01_note || '')]);
      out.push(['rates DV01', signedMoney(pv, 'dv01') + ' per +1 bp of the USD curve', s(pv.dv01_note || '')]);
      out.push(['jump to default', signedMoney(pv, 'jtd') + ' (' + s(pv.jtd_rule || '+/- (1 - 40 %) x notional less the value at mid') + ')']);
      out.push(['credit exposure', money(pv, 'exposure') + (pv.exposure_note ? ' (' + s(pv.exposure_note) + ')' : '')]);
      out.push(['initial margin', usd(pv, 'im') + (has(pv.im_pct) ? ' (' + pct(pv.im_pct) + ' of the notional, tier ' + s(pv.tier_words || tierWords(pv.tier)) + ')' : '') + (pv.im_rule ? ' . ' + s(pv.im_rule) : '')]);
      if (Object.keys(fund).length) { out.push(['funding', s(fund.note || '') + (fund.payment_note ? ' . ' + s(fund.payment_note) : '')]); }
      out.push(['variation margin', (pv.margin_mode || 'CTM') + ': ' + (s(pv.margin_mode).toUpperCase() === 'STM' ? 'settled to market each day (P&L)' : 'collateral exchanged each day on the model value (CSA)')]);
      out.push(['assumed', (pv.assumed || []).join(', ')]);
      return T.kv(out);
    }
    function renderPreview() {
      if (!ui.preview) { return; }
      T.clear(ui.preview);
      var p = state.preview;
      if (!p) {
        ui.preview.appendChild(T.h('div', { class: 'hint' }, 'Preview prices the deal from the MODEL spread (with your negotiated basis when set) and the USD curve; Request quote then asks the' +
          ' dealer for a firm quote - nothing is booked before you accept it.'));
        paintButtons();
        return;
      }
      var pv = p.pv || {};
      if (p.stale) { ui.preview.appendChild(T.h('div', { class: 'conv cn-warn' }, 'the terms changed since this preview: preview again before requesting a quote')); }
      var blockers = (pv.blockers || []).slice();
      if (p.refused && !blockers.length) { blockers.push({ code: pv.code, message: pv.message }); }
      if (blockers.length) { ui.preview.appendChild(blockerBox(blockers)); }
      if (p.refused) { paintButtons(); return; }
      ui.preview.appendChild(T.h('div', { class: 'cn-title' }, s(pv.kind_label || 'single-name CDS') + ' . ' + s(pv.side_text || sideText(pv.side)) +
        (pv.bookable === false ? ' . not bookable' : '')));
      if (pv.plain) { ui.preview.appendChild(T.h('div', { class: 'cn-plain' }, s(pv.plain))); }
      ui.preview.appendChild(headline(pv));
      var inter = intermediateRows(pv.model);
      if (inter.length) {
        var tog = T.h('a', { href: '#', onclick: function (ev) { ev.preventDefault(); state.showModel = !state.showModel; renderPreview(); } }, state.showModel ? 'hide the model' : 'show the model');
        var mb = T.h('div', { class: 'cn-model-box' }, tog);
        if (state.showModel) { mb.appendChild(T.h('div', { class: 'hint' }, 'CreditGrades (RiskMetrics 2002) intermediates, ' + s(obj(pv.model).method || '') + ':')); mb.appendChild(T.kv(inter)); }
        ui.preview.appendChild(mb);
      }
      var sch = scheduleTable(pv);
      if (sch) { ui.preview.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'coupon schedule', T.h('small', null, 'ACT/360 on the notional; the last period includes the maturity date')), sch)); }
      paintButtons();
    }

    /* ---- the quote request: the previewed terms go to the simulated dealer; its firm quote is accepted under Quote requests ---- */
    function onRequest() {
      var p = state.preview;
      if (!p || p.stale || state.blocked || !p.pv || p.pv.bookable === false) { return; }
      var sw = T.components.swapticket;
      if (!sw || typeof sw.rfqRequest !== 'function') { setError('the quote requests are not available in this build', null); return; }
      ui.quoteBtn.disabled = true;
      sw.rfqRequest(account, 'CDS', p.terms).then(function (r) {
        paintButtons();
        var text = 'Quote requested (' + s(r && r.rfq_id) + '): ' + s(p.pv.side_text || sideText(p.pv.side)) + ' USD ' + money(p.pv, 'notional') + ' - the simulated dealer answers within' +
          ' about 30 s with a firm spread, coupon, maturity and tier; accept or decline it under Quote requests';
        ui.result.textContent = text; ui.result.hidden = false;
        T.ui.toast(text, 'ok', 6000);
        if (opts.onBooked) { try { opts.onBooked(r); } catch (e) { console.error('cdsnames onBooked', e); } }
      }, function (e) { paintButtons(); setError(describe(e), e && e.details && e.details.field); });
    }

    /* ---- the dealing-level dialogs: your negotiated mid / deal at the MODEL, each with its effect before the save ---- */
    function effectView(box, eff) {
      T.clear(box);
      eff = obj(eff);
      var b = obj(eff.before), a = obj(eff.after);
      function lvl(x) { return x.level_kind === 'NEGOTIATED' ? 'your negotiated mid' : (x.level_kind === 'MODEL' ? 'MODEL (confirmed)' : 'not set'); }
      function terms(x) {
        var hs = num(x.half_spread_eff_bp) != null && num(x.half_spread_eff_bp) !== num(x.half_spread_bp) ? bp(x.half_spread_eff_bp) + ' (' + bp(x.half_spread_bp) + ' x the PB multiplier)' : bp(x.half_spread_bp);
        return 'mid ' + bp(x.mid_bp) + (x.bucket ? ' (' + s(x.bucket) + '-like)' : '') + ' . tier ' + tierWords(x.tier) + ' . IM ' + pct(x.im_pct) + ' . half-spread ' + hs;
      }
      box.appendChild(T.kv([['model mid now', bp(eff.model_mid_bp) + (eff.model_bucket ? ' (' + s(eff.model_bucket) + '-like)' : '') + (eff.model_maturity ? ', 5Y to ' + s(eff.model_maturity) : '') +
          (eff.as_of ? ' (as of ' + (num(eff.as_of) ? T.fmt.dt(eff.as_of) + ' ET' : s(eff.as_of)) + ')' : '')],
        ['before', lvl(b) + (num(b.basis_bp) ? ' (basis ' + bpSigned(b.basis_bp) + ')' : '') + ' . ' + terms(b)],
        ['after', T.h('b', null, lvl(a) + (num(a.basis_bp) ? ' (basis ' + bpSigned(a.basis_bp) + ')' : '') + ' . ' + terms(a))],
        ['default coupon', (has(eff.coupon_default_bp) ? s(eff.coupon_default_bp) + ' bp' : 'n/a') + ' - ' + s(eff.coupon_note || 'a dealing level never changes it (it follows the MODEL bucket before any basis)')]]));
      var cs = Array.isArray(eff.contracts) ? eff.contracts : [];
      if (!cs.length) { box.appendChild(T.h('div', { class: 'hint' }, 'no open contract on this name in this account: nothing is re-marked')); return; }
      var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', { class: 'l' }, 'contract'), T.h('th', { class: 'l' }, 'side'), T.h('th', { class: 'num' }, 'notional'),
        T.h('th', { class: 'num' }, 'value now'), T.h('th', { class: 'num' }, 'value at the new mid'), T.h('th', { class: 'num' }, 'change'), T.h('th', { class: 'l' }, ''))));
      var body = T.h('tbody');
      cs.forEach(function (c) {
        body.appendChild(T.h('tr', null, T.h('td', { class: 'l' }, s(c.contract_id)), T.h('td', { class: 'l' }, sideText(c.side, c)), T.h('td', { class: 'num' }, money(c, 'notional')),
          T.h('td', { class: 'num' }, hasMoney(c, 'value_before') ? signedMoney(c, 'value_before') : 'unavailable'),
          T.h('td', { class: 'num' }, hasMoney(c, 'value_after') ? signedMoney(c, 'value_after') : 'unavailable'),
          T.h('td', { class: 'num ' + T.sign(c, 'effect') }, hasMoney(c, 'effect') ? signedMoney(c, 'effect') : 'unavailable'), T.h('td', { class: 'l cn-warn' }, s(c.note || ''))));
      });
      var miss = Array.isArray(eff.missing) ? eff.missing : [];
      body.appendChild(T.h('tr', null, T.h('td', { class: 'l', colspan: '5' }, T.h('b', null, 'total change (full revaluation; one name has one mark per account)')),
        T.h('td', { class: 'num ' + T.sign(eff, 'effect_total') }, T.h('b', null, hasMoney(eff, 'effect_total') ? signedMoney(eff, 'effect_total') :
          'unavailable' + (hasMoney(eff, 'effect_total_known') ? ' (' + signedMoney(eff, 'effect_total_known') + ' without ' + miss.join(', ') + ')' : ''))), T.h('td', null, '')));
      tbl.appendChild(body);
      box.appendChild(T.h('div', { class: 'cn-wrap' }, tbl));
      box.appendChild(T.h('div', { class: 'hint' }, s(eff.note || 'The marks of these contracts move at once; the next variation margin settles the change. Past postings are never restated.')));
    }
    /* GET the effect of a dealing level: a 202 (the name's inputs are being fetched) is asked again after retry_ms, at most RETRY_MAX times; a
       refusal (200 {refused} / {ok: false}, or an error) is said in words; only a real effect reaches onOk.  live() false stops it. */
    function getEffect(q, onOk, onWords, live, tries) {
      tries = tries || 0;
      T.api.get(R_EFFECT, q).then(function (eff) {
        if (!live() || state.dead) { return; }
        if (eff && eff.pending) {
          if (tries + 1 >= RETRY_MAX) { onWords('still fetching - try again', 'INPUTS_PENDING'); return; }
          onWords(s(obj(eff.body).message || 'fetching the inputs of this name') + ' - try ' + (tries + 1) + ' of ' + RETRY_MAX, null, true);
          setTimeout(function () { if (live() && !state.dead) { getEffect(q, onOk, onWords, live, tries + 1); } }, num(eff.retry_ms) || 1500);
          return;
        }
        if (!eff || eff.refused || eff.ok === false) { onWords(s((eff && (eff.message || eff.reason)) || 'the effect cannot be computed now'), eff && eff.code); return; }
        onOk(eff);
      }, function (e) { if (live() && !state.dead) { onWords(describe(e), e && e.code); } });
    }
    function lastModel() {
      var p = state.preview, pv = p && p.pv, sel = state.sel;
      if (!pv || !sel) { return null; }
      var m = obj(pv.model);
      return num(m.spread_bp) != null ? m : null;
    }
    /* the server's words of a saved level, with the re-mark of the open contracts when there are any */
    function savedWords(r, fallback) {
      r = obj(r); var e = obj(r.effect); var n = Array.isArray(e.contracts) ? e.contracts.length : 0;
      return s(r.words || fallback) + (n ? ' . ' + n + ' open contract' + (n === 1 ? '' : 's') + ' re-marked: ' + (hasMoney(e, 'effect_total') ? signedMoney(e, 'effect_total') : 'change unavailable') : '');
    }
    function afterLevelSaved(text) {
      T.ui.toast(text, 'ok', 7000);
      loadDesk().then(function () { renderTicket(); if (state.preview) { onPreview(); } });
    }
    function midDialog() {
      if (!state.sel || state.blocked || !modOk(MOD)) { return; }
      var sel = state.sel, cur = selLevel(), m = lastModel();
      var inp = T.h('input', { type: 'text', spellcheck: 'false', autocomplete: 'off', placeholder: 'e.g. 300.00', value: cur && cur.level_kind === 'NEGOTIATED' ? s(cur.negotiated_mid_bp) : '' });
      var effBox = T.h('div', null, T.h('div', { class: 'hint' }, 'type your mid: its effect is shown here before you save'));
      var modelBox = T.h('div');
      function paintModel(mm) {
        T.clear(modelBox).appendChild(T.kv([['model mid now', mm ? bp(mm.spread_bp) + ' (' + (mm.bucket ? s(mm.bucket) + '-like, ' : '') + 'raw ' + bp(mm.raw_bp) + ' + floor ' + bp(mm.floor_bp) +
          (mm.valuation_date ? '; valuation ' + s(mm.valuation_date) : '') + (mm.maturity_date ? ', to ' + s(mm.maturity_date) : '') + ')' : 'shown with the effect below'],
          ['its inputs', mm ? modelRows(mm).slice(1, 5).map(function (r) { return r[0] + ' ' + (typeof r[1] === 'string' ? r[1] : ''); }).join(' . ') : 'with the effect below']]));
      }
      paintModel(m);
      var err = T.h('div', { class: 'error', hidden: true });
      var st = { action: 'set', value: null, eff: null, seq: 0, timer: null };
      var body = T.h('div', { class: 'cn-dialog' },
        T.h('div', { class: 'cn-words' }, 'Your negotiated mid for ' + s(sel.name) + (sel.ticker ? ' (' + sel.ticker + ')' : '') + ' in ' + (T.accountName ? T.accountName(account) : account) +
          ': stored as a basis to the MODEL spread, so the marks keep moving with its inputs (share price, volatility, debt, the AAA floor). It sets the mid of every' +
          ' preview, booking, mark, variation margin and termination of this name in this account.'),
        modelBox,
        T.kv([['current level', cur ? (cur.level_kind === 'NEGOTIATED' ? 'your negotiated mid ' + bp(cur.negotiated_mid_bp) + ' (basis ' + bpSigned(cur.basis_bp) + ')' : 'MODEL (confirmed)') + (whenOf(cur, 'entered') ? ', set ' + whenOf(cur, 'entered') : '') : 'not set']]),
        T.form([T.field('your mid (bp)', inp, 'the running spread you negotiated, in basis points (e.g. 300.00)')]),
        effBox, err);
      var h = null;
      function want() {
        if (st.action === 'clear') { return { clear: 1 }; }
        var v = clean(inp.value);
        return /^\d+(\.\d{1,4})?$/.test(v) && Number(v) > 0 ? { negotiated_mid_bp: v } : null;
      }
      function loadEffect() {
        var w = want();
        st.eff = null; paintActs();
        if (!w) { T.clear(effBox).appendChild(T.h('div', { class: 'hint' }, st.action === 'clear' ? '' : 'type your mid in bp (above 0, at most 4 decimals): its effect is shown here before you save')); return; }
        var seq = ++st.seq;
        T.clear(effBox).appendChild(T.h('div', { class: 'muted' }, 'computing the effect ...'));
        var q = Object.assign({ account_id: account, cik: sel.cik == null ? '' : sel.cik }, w);
        if (sel.cik == null) { q.ticker = sel.ticker; }
        getEffect(q, function (eff) {
          st.eff = { w: JSON.stringify(w), body: eff };
          effectView(effBox, eff); paintActs();
          if (num(obj(eff && eff.model).spread_bp) != null) { paintModel(eff.model); }
        }, function (text, code, pending) {
          T.clear(effBox).appendChild(T.h('div', { class: pending ? 'muted' : 'error', title: s(code) }, text));
          paintActs();
        }, function () { return seq === st.seq && h && !h.closed; });
      }
      function paintActs() {
        if (!h) { return; }
        var w = want(), ok = !!(st.eff && w && st.eff.w === JSON.stringify(w));
        var save = h.el.querySelector('#cn-mid-save'), clr = h.el.querySelector('#cn-mid-clear');
        if (save) { save.disabled = st.action !== 'set' || !ok || !!state.blocked; }
        if (clr) { clr.disabled = !cur || !!state.blocked || (st.action === 'clear' && !ok); clr.textContent = st.action === 'clear' ? 'Confirm clear' : 'Clear the level ...'; }
      }
      inp.addEventListener('input', function () {
        st.action = 'set';
        if (st.eff || effBox.firstChild) {              /* the effect on screen is of another value: said at once, never left as if current */
          st.eff = null; st.seq++;
          T.clear(effBox).appendChild(T.h('div', { class: 'muted' }, clean(inp.value) ? 'computing the effect of ' + clean(inp.value) + ' bp ...' : 'type your mid: its effect is shown here before you save'));
        }
        if (st.timer) { clearTimeout(st.timer); }
        st.timer = setTimeout(function () { st.timer = null; loadEffect(); }, EFFECT_MS);
        paintActs();
      });
      function post(payload, words2) {
        err.hidden = true;
        return T.api.post(R_BASIS, Object.assign({ account_id: account, cik: sel.cik }, sel.cik == null ? { ticker: sel.ticker } : {}, payload)).then(function (r) {
          h.close();
          afterLevelSaved(savedWords(r, words2));
        }, function (e) { err.textContent = describe(e); err.title = (e && e.code) || ''; err.hidden = false; paintActs(); });
      }
      h = T.ui.modal({
        title: 'Negotiated mid: ' + s(sel.name) + (sel.ticker ? ' (' + sel.ticker + ')' : ''), sticky: true, wide: true, body: body,
        actions: [{ label: 'Cancel' },
          { label: 'Clear the level ...', id: 'cn-mid-clear', onClick: function (mm, btn) {
            if (st.action !== 'clear') { st.action = 'clear'; loadEffect(); return; }
            btn.disabled = true; post({ clear: true }, 'Dealing level cleared for ' + s(sel.ticker || sel.name) + ': a new deal needs a level again');
          } },
          { label: 'Save mid', id: 'cn-mid-save', cls: 'primary', onClick: function (mm, btn) {
            var w = want();
            if (!w || !w.negotiated_mid_bp) { return; }
            btn.disabled = true; post({ negotiated_mid_bp: w.negotiated_mid_bp }, 'Negotiated mid ' + bp(w.negotiated_mid_bp) + ' saved for ' + s(sel.ticker || sel.name));
          } }],
        onClose: function () { if (st.timer) { clearTimeout(st.timer); } st.seq++; }
      });
      h.el.classList.add('cn-dialog');
      paintActs();
      if (inp.value) { loadEffect(); }
    }
    function modelDialog() {
      if (!state.sel || state.blocked || !modOk(MOD)) { return; }
      var sel = state.sel, m = lastModel();
      var effBox = T.h('div', null, T.h('div', { class: 'muted' }, 'computing the effect ...'));
      var inBox = T.h('div');
      var err = T.h('div', { class: 'error', hidden: true });
      var st = { eff: null };
      var body = T.h('div', { class: 'cn-dialog' },
        T.h('div', { class: 'cn-words' }, T.h('b', null, 'deal at the MODEL - no market level known. '), 'No market level is known for ' + s(sel.name) + (sel.ticker ? ' (' + sel.ticker + ')' : '') + '. ' + NOT_VALIDATED),
        inBox, effBox, err);
      function paintInputs(model) {
        T.clear(inBox);
        if (model) { inBox.appendChild(T.kv(modelRows(model))); }
        else { inBox.appendChild(T.h('div', { class: 'hint' }, 'preview the name to see the MODEL\'s inputs and their dates here')); }
      }
      paintInputs(m);
      var h = T.ui.modal({
        title: 'Deal at the MODEL: ' + s(sel.name) + (sel.ticker ? ' (' + sel.ticker + ')' : ''), sticky: true, wide: true, body: body,
        actions: [{ label: 'Cancel' }, { label: 'Confirm: deal at the MODEL', id: 'cn-model-ok', cls: 'primary', onClick: function (mm, btn) {
          if (!st.eff) { return; }
          btn.disabled = true; err.hidden = true;
          T.api.post(R_BASIS, Object.assign({ account_id: account, cik: sel.cik, deal_at_model: true }, sel.cik == null ? { ticker: sel.ticker } : {})).then(function (r) {
            mm.close();
            afterLevelSaved(savedWords(r, 'Dealing at the MODEL confirmed for ' + s(sel.ticker || sel.name)));
          }, function (e) { btn.disabled = false; err.textContent = describe(e); err.title = (e && e.code) || ''; err.hidden = false; });
        } }]
      });
      h.el.classList.add('cn-dialog');
      var ok = h.el.querySelector('#cn-model-ok'); if (ok) { ok.disabled = true; }
      var q = { account_id: account, cik: sel.cik == null ? '' : sel.cik, deal_at_model: 1 };
      if (sel.cik == null) { q.ticker = sel.ticker; }
      getEffect(q, function (eff) {
        st.eff = eff; effectView(effBox, eff);
        if (!m && eff && num(obj(eff.model).spread_bp) != null) { paintInputs(eff.model); }
        if (ok) { ok.disabled = !!state.blocked; }
      }, function (text, code, pending) {
        T.clear(effBox).appendChild(T.h('div', { class: pending ? 'muted' : 'error', title: s(code) }, text));
      }, function () { return !h.closed; });
      if (!m && modOk(MDL) && sel.cik != null) {
        T.api.get(R_MODEL, { cik: sel.cik, tenor_years: Number(state.values.tenor) || 5 }).then(function (mm) {
          if (mm && !mm.pending && mm.ok !== false && num(mm.spread_bp) != null) { paintInputs(mm); }
        }, function () { /* the inputs stay "preview the name" */ });
      }
    }

    /* ---- loading: the desk at once and every 60 s in place (while visible); the list on search / filter / page ---- */
    function loadDesk() {
      if (!account || !modOk(MOD)) { return Promise.resolve(); }
      return T.api.get(R_DESK, { account_id: account }).then(function (d) {
        if (state.dead) { return; }
        var hadUniverse = obj((state.desk || {}).universe).offered;
        state.desk = d || {}; state.deskError = null;
        if (d && d.blocked && !state.blocked) { setBlocked(d.blocked); }
        renderHead(); renderLevelLine();
        if (!(ui.assume && ui.assume.contains(document.activeElement))) { renderAssume(); }   /* never while a tier box is being typed in */
        if (state.sel && ui.form && !ui.form.querySelector('option[value="' + s(state.values.tenor) + '"]')) { renderTicket(); }
        if (state.sel && ui.maturity) { renderMaturity(); }
        if (!state.list || obj(state.desk.universe).offered !== hadUniverse) { loadList(); }
      }, function (e) {
        if (state.dead) { return; }
        state.deskError = describe(e);
        renderHead();
      });
    }
    function loadList() {
      if (!account || !modOk(ENT)) { renderList(); return Promise.resolve(); }
      var seq = ++state.listSeq;
      var q = { q: clean(state.q) || null, filter: state.filter || 'all', limit: PAGE, offset: state.page * PAGE, account_id: account };
      if (state.sort && state.sort.key) { q.sort = state.sort.key; if (state.sort.dir === 'desc') { q.desc = 1; } }
      if (state.offeredOnly !== false && !clean(state.q)) { q.offered = 1; }    /* browsing: the offered names; a search: every match with its status */
      return T.api.get(R_ENTITIES, q).then(function (L) {
        if (state.dead || seq !== state.listSeq) { return; }
        state.list = L && L.pending ? state.list : (L || {}); state.listError = null;
        renderList();
        if (L && L.universe) { renderHead(); }
        if (universeLoading() && !state.listPoll) {          /* the first run of the reference-entity list: look again in 15 s */
          state.listPoll = setTimeout(function () { state.listPoll = null; if (visible()) { loadList(); } }, 15000);
        }
      }, function (e) {
        if (state.dead || seq !== state.listSeq) { return; }
        state.listError = describe(e);
        renderList();
      });
    }
    function setBlocked(reason) {
      state.blocked = reason || null;
      if (ui.blocked) { ui.blocked.textContent = state.blocked || ''; ui.blocked.hidden = !state.blocked || opts.note === false; }
      renderAssume(); renderPreview(); paintButtons();
    }
    function start() {
      if (!modOk(MOD) || state.timer) { return; }
      loadDesk();
      state.timer = setInterval(function () { if (visible()) { loadDesk(); } else if (!alive()) { stop(); } }, DESK_MS);
    }
    function stop() {
      state.dead = true;
      if (state.timer) { clearInterval(state.timer); state.timer = null; }
      if (state.searchTimer) { clearTimeout(state.searchTimer); state.searchTimer = null; }
      if (state.retry) { clearTimeout(state.retry); state.retry = null; }
      if (state.listPoll) { clearTimeout(state.listPoll); state.listPoll = null; }
      if (state.waitHealth) { state.waitHealth(); state.waitHealth = null; }
    }

    build();
    start();
    return {
      el: root, account_id: account,
      setBlocked: setBlocked,
      refresh: function () { if (!modOk(MOD)) { return Promise.resolve(); } return loadDesk(); },
      unmount: function () { stop(); T.clear(root); }
    };
  }

  T.components.cdsnames = { mount: mount, kindShort: kindShort, sideText: sideText, nowText: nowText, dealtNow: dealtNow, sensitivity: sensitivity,
    swapSide: swapSide, creditDialog: creditDialog, creditButton: creditButton, creditSection: creditSection, riskPanel: riskPanel, footer: footer,
    pctToFrac: pctToFrac, fracToPct: fracToPct, FILTERS: FILTERS };
  if (T.keys && typeof T.keys.uses === 'function') { T.keys.uses('components/cdsnames.js'); }    /* its keys follow Settings > Keyboard */
})();
