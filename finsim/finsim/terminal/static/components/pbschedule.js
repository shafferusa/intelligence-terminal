/* Settings -> Prime brokers: one prime broker's editable schedule and the relationship (TERMINAL_DESIGN.md s12.4, s17.1; WP-07;
 * Build 2 P12: PO-16, PO-19, SHELL-28, SR-10; FINSIM1 P6 task 6: the CSA collateral terms - securities accepted, the Treasury haircuts).
 *
 * Mounted by pages/settings.js once per PB account:
 *
 *     var h = T.components.pbschedule.mount(el, {account_id: 'F1:PB_GS', broker: 'GS'});   // fetches at once
 *     h.refresh();                                                                        // re-reads the server
 *     h.changes();                                                                        // {patch, before, rows, errors}
 *     h.unmount();
 *
 * Reads  GET   /api/accounts/{id}/schedule   -> {schedule: {field: value}, seed: {field: value}, fields: [{field, label, unit, kind, min,
 *                                                max, hint, choices}], updated_ts}  (the stored values; a field an older row lacks takes the seed)
 *        GET   /api/accounts/{id}/close      -> the relationship: the agreements signed with the dealer, the opening, the schedule edits
 *                                                (count, the last one's before / after), flat + the reasons it is not
 * Writes PATCH /api/accounts/{id}/schedule   {field: value, ...}  ONLY the fields the owner changed (request_id per click via T.api)
 *        POST  /api/accounts/{id}/close      {}  Close relationship (a flat account only; 409 NOT_FLAT names what is left)
 *
 * Conventions shown on the form
 * - Each field is shown by its label in the unit a trader types: rates and initial-margin percentages in % (stored as fractions:
 *   1.25 % is sent as 0.0125), dealing half-widths in basis points or vol points (1.00 vol point is sent as 0.0100), commissions in
 *   USD; "1.25 %", "1.25" and "125 bp" all read as 1.25 % - the conversion is decimal string arithmetic, never floating point.
 * - Every field shows the broker's seed beside it and its allowed range; a value out of range or unreadable is refused in the
 *   form, before the confirmation, naming the field.
 * - Only the fields the owner changed are sent: a field whose text was not edited is never in the PATCH, and a field the server did
 *   not serve is shown empty ("not served") and is sent only when the owner types a value.  Nothing is ever sent because it could not
 *   be read (the 2026-10-21 defect: the form read equity_min_c, which the API no longer serves, showed 0.00, and saving any other
 *   field erased the minimum commission).  A field an older stored row lacks shows the seed it runs on, marked "seed".
 * - Save shows a before / after confirmation listing exactly the fields that change; nothing is sent until confirmed.  Past postings
 *   are never restated.  The header says "opened <date>" until the first edit, then the last change with its before / after.
 * - The relationship: the ISDA Master / CSA / PBA recorded with the dealer and their dates; "Close relationship" for a flat account
 *   (otherwise disabled, with what is still open).
 * - No dependencies beyond core.js (T.h, T.api, T.ui, T.fmt, T.esc); the file is ASCII.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};

  /* [key, label, kind, hint, unit, group] -- the fields in display order; kind: decimal | money | enum (how the value is STORED);
   * unit: how it is typed (pct: percent of a stored fraction, bp, vol: vol points of a stored fraction, usd, usd_per_unit, mult).
   * key is the name the API serves; a money field is decimal USD there and integer cents in the server's store (key + '_c').
   * The server's field specs (labels, ranges) take precedence when they are served. */
  var FIELDS = [
    ['equity_per_share', 'equities: commission per share', 'decimal', 'charged per share per fill, floored by the minimum below', 'usd_per_unit', 'Commissions'],
    ['equity_min', 'equities: minimum per fill (USD)', 'money', 'the floor of an equity fill\'s commission', 'usd', 'Commissions'],
    ['option_per_contract', 'listed options: per contract', 'decimal', 'exercise / assignment / expiry are free', 'usd_per_unit', 'Commissions'],
    ['futures_per_side', 'futures: per contract per side', 'decimal', 'charged in cash the same day', 'usd_per_unit', 'Commissions'],
    ['monthly_minimum', 'monthly minimum commission (USD)', 'money', '0.00 = none; the shortfall against the month\'s commissions is charged on the 1st', 'usd', 'Commissions'],
    ['financing_spread', 'financing: spread over SOFR', 'decimal', 'on debit balances (PB financing drawn), ACT/360, accrued nightly, paid monthly', 'pct', 'Financing and stock borrow'],
    ['credit_interest_spread', 'credit interest: SOFR minus', 'decimal', 'interest on positive settled USD = max(0, SOFR - this spread), ACT/360', 'pct', 'Financing and stock borrow'],
    ['gc_rate', 'stock borrow: general-collateral fee', 'decimal', 'hard-to-borrow tiers add to it', 'pct', 'Financing and stock borrow'],
    ['htb_add', 'stock borrow: hard-to-borrow add-on', 'decimal', '', 'pct', 'Financing and stock borrow'],
    ['otc_spread_mult', 'OTC spread multiplier', 'decimal', 'multiplies every OTC dealing half-width and the TRS spread (1.0 = as below)', 'mult', 'OTC dealing'],
    ['swap_margin_mode', 'variation margin', 'enum', 'CTM = collateralised-to-market (VM is collateral); STM = settled-to-market (VM settles as P&L each day)', 'enum', 'OTC dealing'],
    ['irs_half_width_bp', 'IRS dealing half-width', 'decimal', 'the dealt rate = par +/- this, against you', 'bp', 'OTC dealing'],
    ['trs_spread', 'TRS financing spread over SOFR', 'decimal', '', 'pct', 'OTC dealing'],
    ['eq_option_hw_vol', 'equity options: dealing half-width', 'decimal', 'priced at the implied vol +/- this, against you', 'vol', 'OTC dealing'],
    ['fx_option_hw_vol', 'FX options: dealing half-width', 'decimal', 'priced at the implied vol +/- this, against you', 'vol', 'OTC dealing'],
    ['forward_hw_bp', 'forwards: dealing half-width', 'decimal', 'the dealt forward = the model forward +/- this, against you', 'bp', 'OTC dealing'],
    ['irs_im_bp', 'IRS initial margin', 'decimal', 'IM = |DV01| x this many bp of rate move', 'bp', 'Initial margin'],
    ['trs_im_pct', 'TRS initial margin', 'decimal', 'of the TRS notional', 'pct', 'Initial margin'],
    ['xccy_im_pct', 'cross-currency swap initial margin', 'decimal', 'of the USD notional', 'pct', 'Initial margin'],
    ['option_im_pct', 'sold OTC options: initial margin', 'decimal', 'of the underlying notional of an option you sell (a bought option posts none)', 'pct', 'Initial margin'],
    ['forward_im_pct', 'OTC forwards: initial margin', 'decimal', 'of the forward\'s USD notional, either side', 'pct', 'Initial margin'],
    /* FINSIM1 P6 task 6 (1.0.6): the CSA's securities collateral terms (Cash & Collateral: pledge / release) */
    ['csa_securities', 'CSA collateral: securities accepted', 'enum', 'TREASURIES = US Treasury bills, notes and bonds may be pledged besides cash; CASH_ONLY = cash only', 'enum', 'CSA collateral'],
    ['csa_bill_haircut', 'CSA collateral: Treasury bill haircut', 'decimal', 'a pledged bill counts at its market value x (1 - this)', 'pct', 'CSA collateral'],
    ['csa_bond_haircut', 'CSA collateral: Treasury note / bond haircut', 'decimal', 'a pledged note or bond counts at its market value x (1 - this)', 'pct', 'CSA collateral']
  ];
  var ENUMS = { swap_margin_mode: ['CTM', 'STM'], csa_securities: ['TREASURIES', 'CASH_ONLY'] };
  var UNIT = { pct: '%', bp: 'bp', vol: 'vol pts', usd: 'USD', usd_per_unit: 'USD', mult: 'x', enum: '' };
  var DECIMAL_RE = /^\d+(\.\d{1,8})?$/;
  var MONEY_RE = /^\d+(\.\d{0,2})?$/;
  var TYPED_RE = /^(\d*\.?\d+|\d+\.)\s*(%|bps?|vol(\s*pts?)?|vp|usd|\$|x)?$/i;
  var NOT_SERVED = 'not served';

  /* "1,234.5" -> 123450 (integer cents) by string arithmetic; null when not a non-negative money string */
  function toCents(s) {
    s = String(s == null ? '' : s).trim().replace(/,/g, '').replace(/^\$\s*/, '').replace(/\s*usd$/i, '');
    if (!MONEY_RE.test(s)) { return null; }
    var p = s.split('.');
    var frac = ((p[1] || '') + '00').slice(0, 2);
    return parseInt(p[0], 10) * 100 + parseInt(frac, 10);
  }
  /* 123450 -> "1234.50"; null for anything but an integer (never "0.00" for a value that is not there) */
  function fromCents(c) {
    if (typeof c === 'string' && /^-?\d+$/.test(c.trim())) { c = parseInt(c, 10); }
    if (typeof c !== 'number' || !isFinite(c) || Math.floor(c) !== c) { return null; }
    var n = Math.abs(c);
    var whole = Math.floor(n / 100), frac = n % 100;
    return (c < 0 ? '-' : '') + String(whole) + '.' + (frac < 10 ? '0' : '') + frac;
  }
  /* The value of one field in a schedule / seed object as a string, or null when the object does not carry it.
   * money: obj[key] (decimal USD, what the API serves) or, failing that, obj[key + '_c'] (integer cents, a payload in
   * the stored shape), always returned as "1234.50"; anything unreadable is null, never 0.00. */
  function valueOf(obj, key, kind) {
    if (!obj) { return null; }
    var v = obj[key];
    if (kind === 'money') {
      if (v != null && v !== '') { var c = toCents(v); return c == null ? null : fromCents(c); }
      return fromCents(obj[key + '_c']);
    }
    if (v == null || v === '') { return null; }
    return kind === 'enum' ? String(v).toUpperCase() : String(v);
  }
  /* multiply a non-negative decimal string by 10^n by moving the point (string arithmetic: "1.25", -2 -> "0.0125"; "0.0125", 2 -> "1.25") */
  function shift(s, n) {
    s = String(s).trim();
    var neg = s.charAt(0) === '-';
    if (neg) { s = s.slice(1); }
    var p = s.split('.');
    var ip = p[0] || '0', fp = p[1] || '';
    var digits = ip + fp, point = ip.length + n;
    while (point <= 0) { digits = '0' + digits; point += 1; }
    while (point > digits.length) { digits += '0'; }
    var a = digits.slice(0, point).replace(/^0+(?=\d)/, ''), b = digits.slice(point).replace(/0+$/, '');
    return (neg ? '-' : '') + (a || '0') + (b ? '.' + b : '');
  }
  function unitOf(key) { var f = fieldDef(key); return f ? f[4] : ''; }
  function fieldDef(key) { for (var i = 0; i < FIELDS.length; i++) { if (FIELDS[i][0] === key) { return FIELDS[i]; } } return null; }
  /* a stored value -> the text a trader reads / types (0.0125 -> "1.25" for %, 0.0100 -> "1.00" for vol points) */
  function display(unit, stored) {
    if (stored == null) { return null; }
    if (unit === 'pct' || unit === 'vol') {
      var s = shift(stored, 2); var dot = s.indexOf('.');
      return dot < 0 ? s + '.00' : (s.length - dot - 1 < 2 ? s + '0' : s);
    }
    return String(stored);
  }
  /* the stored form of a typed value, or {error}: "1.25 %" / "1.25" / "125 bp" for a % field -> "0.0125"; "1 vol" -> "0.01";
     "2.5 bp" -> "2.5"; money -> "1234.50"; an enum upper-cased */
  function normalise(kind, raw, unit, key) {
    var s = String(raw == null ? '' : raw).trim();
    if (kind === 'enum') { var ch = ENUMS[key] || ENUMS.swap_margin_mode; s = s.toUpperCase(); return ch.indexOf(s) >= 0 ? { value: s } : { error: 'one of ' + ch.join(', ') }; }
    if (kind === 'money') { var c = toCents(s); return c == null ? { error: 'a USD amount at or above 0.00 with at most 2 decimals' } : { value: fromCents(c) }; }
    s = s.replace(/,/g, '').replace(/^\$\s*/, '');
    var m = TYPED_RE.exec(s);
    if (!m) { return { error: 'a non-negative number' + (unit === 'pct' ? ' in % (e.g. 1.25) or bp (e.g. 125 bp)' : unit === 'vol' ? ' of vol points (e.g. 1.00)' : unit === 'bp' ? ' of basis points' : '') }; }
    var num = m[1].charAt(0) === '.' ? '0' + m[1] : m[1].replace(/\.$/, '');
    var sfx = (m[2] || '').toLowerCase().replace(/\s+/g, '');
    var out;
    if (unit === 'pct') { out = /^bps?$/.test(sfx) ? shift(num, -4) : (sfx === '' || sfx === '%' ? shift(num, -2) : null); }
    else if (unit === 'vol') { out = (sfx === '' || sfx === '%' || /^vol/.test(sfx) || sfx === 'vp') ? shift(num, -2) : null; }
    else if (unit === 'bp') { out = sfx === '%' ? shift(num, 2) : ((sfx === '' || /^bps?$/.test(sfx)) ? num : null); }
    else { out = (sfx === '' || sfx === 'usd' || sfx === '$' || sfx === 'x') ? num : null; }
    if (out == null) { return { error: 'the unit ' + m[2] + ' does not fit this field (' + (UNIT[unit] || 'a number') + ')' }; }
    if (!DECIMAL_RE.test(out)) { return { error: 'too many decimals (the schedule keeps 8 decimal places of the stored value)' }; }
    return { value: out };
  }
  /* equal as numbers (the server normalises "0.0030" == "0.003", "1.5" == "1.50"); a missing side is never equal */
  function same(kind, a, b) {
    if (a == null || b == null) { return false; }
    if (kind === 'money') { var ca = toCents(a), cb = toCents(b); return ca != null && ca === cb; }
    if (kind === 'enum') { return String(a).toUpperCase() === String(b).toUpperCase(); }
    var pa = String(a).split('.'), pb = String(b).split('.');
    var fa = (pa[1] || '').replace(/0+$/, ''), fb = (pb[1] || '').replace(/0+$/, '');
    return String(parseInt(pa[0], 10)) === String(parseInt(pb[0], 10)) && fa === fb;
  }
  function shown(v, unit) { return v == null ? NOT_SERVED : (display(unit, v) + (UNIT[unit] ? ' ' + UNIT[unit] : '')); }
  /* the allowed range of a field in its typed unit, from the served spec (stored units), else empty */
  function rangeOf(spec, unit) {
    if (!spec) { return null; }
    var lo = spec.min_usd != null ? spec.min_usd : spec.min, hi = spec.max_usd != null ? spec.max_usd : spec.max;
    if (lo == null && hi == null) { return null; }
    var d = function (x) { return x == null ? null : (unit === 'pct' || unit === 'vol' ? shift(String(x), 2) : String(x)); };
    return { lo: d(lo), hi: d(hi), text: (lo != null ? d(lo) : '0') + ' to ' + (hi != null ? d(hi) : 'no maximum') + (UNIT[unit] ? ' ' + UNIT[unit] : '') };
  }
  function within(range, typedStored, unit) {
    if (!range) { return true; }
    var v = Number(unit === 'pct' || unit === 'vol' ? shift(typedStored, 2) : typedStored);
    if (!isFinite(v)) { return false; }
    if (range.lo != null && v < Number(range.lo)) { return false; }
    if (range.hi != null && v > Number(range.hi)) { return false; }
    return true;
  }
  function dateOf(ts) { return ts ? (T.fmt && T.fmt.date ? T.fmt.date(ts) : String(ts)) : ''; }
  function dtOf(ts) { return ts ? (T.fmt && T.fmt.dt ? T.fmt.dt(ts) + ' ET' : String(ts)) : ''; }

  var cssDone = false;
  function ensureCss() {
    if (cssDone || document.getElementById('pbschedule-css')) { cssDone = true; return; }
    cssDone = true;
    var s = document.createElement('style');
    s.id = 'pbschedule-css';
    s.textContent = '.pbschedule table.grid { max-width: 100%; }' +
      '.pbschedule table.grid td { vertical-align: middle; }' +
      '.pbschedule input[type=text], .pbschedule select { width: 120px; box-sizing: border-box; }' +
      '.pbschedule .pbs-wrap { overflow-x: auto; max-width: 100%; }' +
      '.pbschedule .pbs-actions { display: flex; gap: 8px; align-items: center; flex-wrap: nowrap; margin: 8px 0; }' +
      '.pbschedule .pbs-group td { font-weight: 600; padding-top: 8px; }' +
      '.pbschedule input.bad, .pbschedule select.bad { border-color: var(--down, #c55); outline: 1px solid var(--down, #c55); }' +
      '.pbschedule .pbs-rel { margin: 6px 0; }' +
      '.pbschedule .pbs-head { margin: 4px 0; }';
    document.head.appendChild(s);
  }

  function mount(el, opts) {
    opts = opts || {};
    ensureCss();
    var account = opts.account_id || '';
    var path = '/api/accounts/' + encodeURIComponent(account) + '/schedule';
    var relPath = '/api/accounts/' + encodeURIComponent(account) + '/close';
    var state = { data: null, rel: null, inputs: {}, last: null, dead: false };
    var root = T.h('div', { class: 'pbschedule' });
    T.clear(el).appendChild(root);

    function specOf(key) {
      var list = (state.data && state.data.fields) || [];
      for (var i = 0; i < list.length; i++) { var f = list[i]; if (f && (f.field === key || f.field_usd === key)) { return f; } }
      return null;
    }
    function labelOf(key) { var s = specOf(key); var d = fieldDef(key); return (s && s.label) || (d ? d[1] : key); }
    function headText() {
      var d = state.data || {};
      var rel = state.rel;
      var parts = [(d.name || (d.schedule && d.schedule.name) || opts.broker || d.broker || '') + ' . ' + account];
      if (rel && rel.schedule_edits === 0) { parts.push('opened ' + dateOf(rel.opened_ts) + ' on the dealer\'s seed terms (not edited since)'); }
      else if (rel && rel.last_edit) { parts.push('last change ' + dtOf(rel.last_edit.ts) + ' (' + rel.schedule_edits + ' edit' + (rel.schedule_edits === 1 ? '' : 's') + ')'); }
      else if (d.updated_ts) { parts.push('last change ' + dtOf(d.updated_ts)); }
      parts.push('modelled assumptions; real PB schedules are negotiated');
      return parts.join(' . ');
    }

    function render() {
      T.clear(root);
      if (!account) { root.appendChild(T.h('div', { class: 'error' }, 'pbschedule: no account_id given')); return; }
      var d = state.data;
      if (!d) { root.appendChild(T.h('div', { class: 'empty' }, 'loading schedule ' + account + ' ...')); return; }
      var sched = d.schedule || {};
      var seed = d.seed || {};
      root.appendChild(T.h('div', { class: 'hint pbs-head' }, headText()));
      relBlock();
      var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'term'), T.h('th', null, 'value'), T.h('th', null, 'unit'),
        T.h('th', null, 'seed'), T.h('th', null, 'allowed'))));
      var body = T.h('tbody');
      state.inputs = {};
      var group = null;
      FIELDS.forEach(function (f) {
        var key = f[0], kind = f[2], hint = f[3], unit = f[4];
        var spec = specOf(key);
        var label = labelOf(key);
        if (f[5] !== group) { group = f[5]; body.appendChild(T.h('tr', { class: 'pbs-group' }, T.h('td', { colspan: '5' }, group))); }
        var served = valueOf(sched, key, kind);
        var seedV = valueOf(seed, key, kind);
        var fromSeed = served == null && seedV != null;               /* an older stored row lacks this term: it runs on the seed */
        var cur = fromSeed ? seedV : served;                          /* null: neither served nor seeded */
        var input;
        if (kind === 'enum') {
          input = T.h('select', { 'data-field': key });
          if (cur == null) { input.appendChild(T.h('option', { value: '', selected: true }, NOT_SERVED)); }
          ENUMS[key].forEach(function (o) { input.appendChild(T.h('option', { value: o, selected: cur === o }, o)); });
        } else {
          var txt = cur == null ? '' : display(unit, cur);
          input = T.h('input', { type: 'text', value: txt, placeholder: cur == null ? NOT_SERVED : null, spellcheck: 'false', 'data-field': key, autocomplete: 'off' });
        }
        /* initial = the text the field was rendered with: an unedited field is never sent */
        state.inputs[key] = { input: input, kind: kind, unit: unit, current: cur, initial: String(input.value), label: label, range: rangeOf(spec, unit) };
        var r = state.inputs[key].range;
        var title = (spec && spec.hint) || hint || '';
        body.appendChild(T.h('tr', { title: title },
          T.h('td', null, label), T.h('td', null, input),
          T.h('td', { class: 'muted' }, UNIT[unit] || ''),
          T.h('td', { class: 'muted' }, seedV == null ? '' : display(unit, seedV) + (fromSeed ? ' (in use)' : '')),
          T.h('td', { class: 'muted' }, r ? r.text : '')));
      });
      tbl.appendChild(body);
      root.appendChild(T.h('div', { class: 'pbs-wrap' }, tbl));
      var save = T.h('button', { class: 'btn primary', onclick: onSave }, 'Save (before / after confirmation)');
      var reset = T.h('button', { class: 'btn small', onclick: function () { render(); }, title: 'discard typed values' }, 'Discard changes');
      root.appendChild(T.h('div', { class: 'pbs-actions' }, save, reset));
      root.appendChild(T.h('div', { class: 'hint' }, 'Rates and margins are typed in % (1.25 or 125 bp), half-widths in bp or vol points; the server stores fractions. Past postings are never restated: a fee already in the ledger keeps the schedule it was charged on; later fills, accruals and OTC quotes use the new numbers. Only the fields you change are sent.'));
      if (state.last) {
        var kv = [];
        Object.keys(state.last.after || {}).forEach(function (k) {
          var u = unitOf(k);
          kv.push([labelOf(k), shown(state.last.before[k], u) + ' -> ' + shown(state.last.after[k], u)]);
        });
        if (kv.length) { root.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'last change', T.h('small', null, 'just saved')), T.kv(kv))); }
      } else if (state.rel && state.rel.last_edit) {
        var le = state.rel.last_edit, kv2 = [];
        Object.keys(le.after || {}).forEach(function (k) {
          var key = k.replace(/_c$/, ''); var kind = (fieldDef(key) || [])[2];
          var before = kind === 'money' ? fromCents(le.before[k]) : le.before[k], after = kind === 'money' ? fromCents(le.after[k]) : le.after[k];
          kv2.push([(le.labels && le.labels[k]) || labelOf(key), shown(before, unitOf(key)) + ' -> ' + shown(after, unitOf(key))]);
        });
        if (kv2.length) { root.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'last change', T.h('small', null, dtOf(le.ts))), T.kv(kv2))); }
      }
      if (T.convNote) { root.appendChild(T.convNote(['ELIGIBILITY', 'SWAP_VM'])); }
    }

    /* PO-16: the agreements signed with the dealer and "Close relationship" for a flat account */
    function relBlock() {
      var rel = state.rel;
      var box = T.h('div', { class: 'section pbs-rel' });
      if (!rel) { box.appendChild(T.h('div', { class: 'muted' }, 'loading the relationship ...')); root.appendChild(box); return; }
      /* the one-line "ISDA Master / CSA / PBA with <dealer>, signed <date>" is the section's heading in Settings; here each agreement */
      if (!(rel.agreements || []).length) { box.appendChild(T.h('div', { class: 'hint' }, rel.agreements_line || '')); }
      (rel.agreements || []).forEach(function (a) { box.appendChild(T.h('div', { class: 'hint' }, a.label + ' (' + a.meaning + '), signed ' + a.signed_date)); });
      var btn = T.h('button', { class: 'btn small', disabled: !rel.flat, onclick: onClose,
        title: rel.flat ? 'close this prime-broker account: nothing is open in it' : 'not flat: ' + (rel.reasons || []).join('; ') }, 'Close relationship');
      box.appendChild(T.h('div', { class: 'pbs-actions' }, btn, T.h('span', { class: 'muted' }, rel.status !== 'OPEN' ? 'closed ' + dtOf(rel.closed_ts)
        : (rel.flat ? 'the account is flat: the relationship can be closed' : 'still open in it: ' + (rel.reasons || []).join('; ')))));
      root.appendChild(box);
    }
    function onClose() {
      var rel = state.rel;
      if (!rel || !rel.flat) { return; }
      T.ui.modal({
        title: 'Close the relationship with ' + rel.name, sticky: true,
        body: T.h('div', null, T.h('div', { class: 'hint' }, rel.close_rule || ''), T.kv([['account', account], ['agreements', rel.agreements_line || '']])),
        actions: [{ label: 'Cancel' }, { label: 'Close relationship', cls: 'primary', onClick: function (m, btn) {
          btn.disabled = true;
          T.api.post(relPath, {}).then(function () {
            m.close();
            T.ui.toast(rel.name + ' relationship closed (' + account + '); its history stays readable', 'ok', 6000);
            ['accounts', 'books', 'account_detail'].forEach(function (n) { if (T.poll && T.poll.kick) { T.poll.kick(n); } });
            load();
          }, function (e) { btn.disabled = false; T.api.fail(e, 'close ' + account); });
        } }]
      });
    }

    /* {patch, before, rows, errors}: only fields whose text the owner edited AND whose value differs from the server's */
    function changes() {
      var out = { patch: {}, before: {}, rows: [], errors: [] };
      Object.keys(state.inputs).forEach(function (key) {
        var inp = state.inputs[key];
        var raw = String(inp.input.value == null ? '' : inp.input.value);
        inp.input.classList.remove('bad');
        if (raw === inp.initial) { return; }                                  /* not edited: never sent */
        if (inp.current == null && raw.trim() === '') { return; }             /* not served and still empty: nothing to send */
        var n = normalise(inp.kind, raw, inp.unit, key);
        if (!n.error && inp.kind !== 'enum' && inp.kind !== 'money' && !within(inp.range, n.value, inp.unit)) { n = { error: 'outside the allowed ' + inp.range.text }; }
        if (n.error) { out.errors.push(inp.label + ': ' + n.error); inp.input.classList.add('bad'); return; }
        if (same(inp.kind, n.value, inp.current)) { return; }                 /* edited back to the served value */
        out.patch[key] = n.value;
        out.before[key] = inp.current;
        out.rows.push([inp.label, shown(inp.current, inp.unit) + ' -> ' + shown(n.value, inp.unit), key]);
      });
      return out;
    }

    function onSave() {
      var c = changes();
      if (c.errors.length) { T.ui.toast(c.errors.join('; '), 'err', 6000); return; }
      if (!c.rows.length) { T.ui.toast('no change to save', 'ok'); return; }
      var body = T.h('div', null,
        T.h('div', { class: 'hint' }, 'Only these terms of ' + account + ' change now; every other term keeps its value. Past postings are never restated; later fills and OTC quotes use the new numbers.'),
        T.kv(c.rows));
      T.ui.modal({
        title: 'Confirm schedule change . ' + (opts.broker || state.data.broker || ''), body: body, sticky: true,
        actions: [
          { label: 'Cancel' },
          { label: 'Apply', cls: 'primary', onClick: function (m, btn) {
            btn.disabled = true;
            T.api.patch(path, c.patch).then(function (r) {
              m.close();
              if (state.dead) { return; }
              var after = {};
              Object.keys(c.patch).forEach(function (k) {                    /* the server's value when it says so */
                var inp = state.inputs[k];
                var v = r && r.schedule ? valueOf(r.schedule, k, inp ? inp.kind : 'decimal') : null;
                after[k] = v != null ? v : c.patch[k];
              });
              state.last = { before: c.before, after: after };
              if (r && r.schedule) { state.data = mergeData(state.data, r); render(); loadRel(); } else { load(); }
              T.ui.toast('schedule saved (' + Object.keys(c.patch).length + ' term' + (Object.keys(c.patch).length === 1 ? '' : 's') + ')', 'ok');
              if (T.poll && T.poll.kick) { T.poll.kick('accounts'); }
            }, function (e) {
              btn.disabled = false;
              T.api.fail(e, 'schedule ' + account);
            });
          } }
        ]
      });
    }

    function mergeData(old, r) {
      var d = {};
      Object.keys(old || {}).forEach(function (k) { d[k] = old[k]; });
      d.schedule = r.schedule;
      if (r.updated_ts != null) { d.updated_ts = r.updated_ts; }
      if (r.broker) { d.broker = r.broker; }
      if (r.fields) { d.fields = r.fields; }
      if (r.seed) { d.seed = r.seed; }
      d.source = 'pb_schedules';
      return d;
    }

    function loadRel() {
      return T.api.get(relPath).then(function (r) {
        if (state.dead) { return; }
        state.rel = r;
        if (state.data) { render(); }
      }, function (e) {
        if (state.dead) { return; }
        state.rel = { agreements_line: 'the relationship could not be read: ' + (T.api.describe ? T.api.describe(e) : e), agreements: [], flat: false, reasons: [] };
        if (state.data) { render(); }
      });
    }

    function load() {
      loadRel();
      return T.api.get(path).then(function (r) {
        if (state.dead) { return; }
        state.data = r;
        render();
      }, function (e) {
        if (state.dead) { return; }
        T.clear(root);
        if (T.errorBox) { T.errorBox(root, e, 'schedule ' + account); } else { root.appendChild(T.h('div', { class: 'error' }, String(e && e.message || e))); }
        root.appendChild(T.h('button', { class: 'btn small', onclick: load }, 'Retry'));
      });
    }

    render();
    load();
    return {
      el: root,
      refresh: function () { return load(); },
      unmount: function () { state.dead = true; T.clear(root); },
      changes: changes
    };
  }

  T.components.pbschedule = { mount: mount, FIELDS: FIELDS, toCents: toCents, fromCents: fromCents, valueOf: valueOf, normalise: normalise, same: same, shift: shift,
                              display: display };
})();
