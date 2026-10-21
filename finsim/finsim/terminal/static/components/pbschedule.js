/* Settings -> Prime brokers: the per-PB editable fee schedule (TERMINAL_DESIGN.md s12.4, s17.1; WP-07).
 *
 * Mounted by pages/settings.js (WP-12) once per PB account:
 *
 *     var h = T.components.pbschedule.mount(el, {account_id: 'F1:PB_GS', broker: 'GS'});   // fetches at once
 *     h.refresh();                                                                        // re-reads the server
 *     h.changes();                                                                        // {patch, before, rows, errors}
 *     h.unmount();
 *
 * Reads  GET   /api/accounts/{id}/schedule   -> {account_id, broker, schedule: {field: value}, seed?, updated_ts, source?, fields_note?}
 * Writes PATCH /api/accounts/{id}/schedule   {field: value, ...}  ONLY the fields the owner changed (request_id per click via T.api)
 *                                            -> {schedule, updated_ts, ...}
 *
 * Conventions shown on the form
 * - Every value travels as a decimal string: fractions (0.0125 = 1.25 %), USD per unit, and the two money fields in
 *   MAJOR units under the names the API serves them -- equity_min "1.00", monthly_minimum "0.00" (the codec's names;
 *   the server converts them to its integer cents with units.to_c).  A payload that still carries only the stored
 *   cents (equity_min_c: 100) is read as cents and shown in USD; the form always sends the USD name.
 * - Only the fields the owner changed are sent: a field whose text was not edited is never in the PATCH, and a field
 *   the server did not serve is shown empty ("not served") and is sent only when the owner types a value.  Nothing is
 *   ever sent because it could not be read (the 2026-10-21 defect: the form read equity_min_c, which the API no longer
 *   serves, showed 0.00, and saving any other field erased the minimum commission).
 * - Save shows a before / after confirmation listing exactly the fields that change; nothing is sent until confirmed.
 * - Past postings are never restated: a fee already in the ledger keeps the schedule it was charged on.
 * - The seed (the broker's modelled default) is shown next to each field; "modelled assumptions; real PB schedules vary".
 * - A failed request renders its error text in the panel and keeps the typed values (never a blank, never a stale form).
 * - No dependencies beyond core.js (T.h, T.api, T.ui, T.fmt, T.esc); the file is ASCII.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};

  /* [key, label, kind, hint] -- the s12.4 fields in display order; kind: decimal | money | enum.  key is the name the API
   * serves; a money field is decimal USD there and integer cents in the server's store (key + '_c'). */
  var FIELDS = [
    ['equity_per_share', 'equities: USD per share', 'decimal', 'charged per share per fill, floored by the minimum below'],
    ['equity_min', 'equities: minimum per fill (USD)', 'money', ''],
    ['option_per_contract', 'listed options: USD per contract', 'decimal', 'exercise / assignment / expiry are free'],
    ['futures_per_side', 'futures: USD per contract per side', 'decimal', 'charged in cash the same day'],
    ['financing_spread', 'financing spread over SOFR (fraction)', 'decimal', '0.0125 = 1.25 % on debit balances (2700), ACT/360, accrued nightly, paid monthly'],
    ['credit_interest_spread', 'credit interest spread under SOFR (fraction)', 'decimal', 'interest on positive settled USD = max(0, SOFR - spread), ACT/360'],
    ['gc_rate', 'GC stock-borrow fee (fraction)', 'decimal', 'general collateral; tiers T1-T3 add to it'],
    ['htb_add', 'hard-to-borrow add-on (fraction)', 'decimal', ''],
    ['otc_spread_mult', 'OTC spread multiplier', 'decimal', 'multiplies the IRS dealing half-width and the TRS spread (1.0 = the seed terms)'],
    ['swap_margin_mode', 'swap variation margin', 'enum', 'CTM = collateralised-to-market (1320 / 2330); STM = settled-to-market (daily to 4450)'],
    ['monthly_minimum', 'monthly minimum commission (USD)', 'money', '0.00 = none; the shortfall vs the month\'s commissions is charged on the 1st'],
    ['irs_half_width_bp', 'IRS dealing half-width (bp)', 'decimal', 'par rate +/- half-width against the user'],
    ['trs_spread', 'TRS financing spread over SOFR (fraction)', 'decimal', '']
  ];
  var ENUMS = { swap_margin_mode: ['CTM', 'STM'] };
  var DECIMAL_RE = /^\d+(\.\d{1,8})?$/;
  var MONEY_RE = /^\d+(\.\d{0,2})?$/;
  var NOT_SERVED = 'not served';

  /* "1,234.5" -> 123450 (integer cents) by string arithmetic; null when not a non-negative money string */
  function toCents(s) {
    s = String(s == null ? '' : s).trim().replace(/,/g, '');
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
  /* the stored form of a typed value, or {error} */
  function normalise(kind, raw) {
    var s = String(raw == null ? '' : raw).trim();
    if (kind === 'enum') { s = s.toUpperCase(); return ENUMS.swap_margin_mode.indexOf(s) >= 0 ? { value: s } : { error: 'one of ' + ENUMS.swap_margin_mode.join(', ') }; }
    if (kind === 'money') { var c = toCents(s); return c == null ? { error: 'a USD amount at or above 0.00 with at most 2 decimals' } : { value: fromCents(c) }; }
    s = s.replace(/,/g, '');
    if (!DECIMAL_RE.test(s)) { return { error: 'a non-negative decimal with at most 8 decimals' }; }
    return { value: s };
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
  function shown(v) { return v == null ? NOT_SERVED : String(v); }
  /* the percent of a FRACTION field (*_spread, *_rate, htb_add; quotes/rates.py UNITS): 0.0125 -> " (= 1.25 %)"; the
   * USD-per-unit fees, the multiplier and the bp field are not fractions and get no hint */
  var FRACTION_RE = /(_spread|_rate)$|^htb_add$/;
  function pctHint(key, v) {
    if (!FRACTION_RE.test(key)) { return ''; }
    var s = String(v == null ? '' : v);
    if (!DECIMAL_RE.test(s)) { return ''; }
    return ' (= ' + (T.fmt && T.fmt.rate ? T.fmt.rate(s) : (Number(s) * 100).toFixed(4).replace(/0+$/, '').replace(/\.$/, '') + ' %') + ')';
  }

  function mount(el, opts) {
    opts = opts || {};
    var account = opts.account_id || '';
    var path = '/api/accounts/' + encodeURIComponent(account) + '/schedule';
    var state = { data: null, inputs: {}, last: null, dead: false };
    var root = T.h('div', { class: 'pbschedule' });
    T.clear(el).appendChild(root);

    function render() {
      T.clear(root);
      if (!account) { root.appendChild(T.h('div', { class: 'error' }, 'pbschedule: no account_id given')); return; }
      var d = state.data;
      if (!d) { root.appendChild(T.h('div', { class: 'empty' }, 'loading schedule ' + account + ' ...')); return; }
      var sched = d.schedule || {};
      var seed = d.seed || {};
      var head = T.h('div', { class: 'hint' },
        (d.name || sched.name || opts.broker || d.broker || '') + ' . ' + account +
        (d.updated_ts ? ' . last change ' + (T.fmt && T.fmt.dt ? T.fmt.dt(d.updated_ts) : d.updated_ts) : '') +
        (d.source === 'seed' ? ' . seed values (no edited row yet)' : '') +
        ' . ' + (sched.note || 'modelled assumptions; real PB schedules vary'));
      root.appendChild(head);
      var rows = [];
      state.inputs = {};
      FIELDS.forEach(function (f) {
        var key = f[0], label = f[1], kind = f[2], hint = f[3];
        var cur = valueOf(sched, key, kind);                    /* null: the server did not serve this field */
        var input;
        if (kind === 'enum') {
          input = T.h('select', { 'data-field': key });
          if (cur == null) { input.appendChild(T.h('option', { value: '', selected: true }, NOT_SERVED)); }
          ENUMS[key].forEach(function (o) { input.appendChild(T.h('option', { value: o, selected: cur === o }, o)); });
        } else {
          input = T.h('input', { type: 'text', value: cur == null ? '' : cur, placeholder: cur == null ? NOT_SERVED : null, style: 'width:160px', spellcheck: 'false', 'data-field': key });
        }
        /* initial = the text the field was rendered with: an unedited field is never sent */
        state.inputs[key] = { input: input, kind: kind, current: cur, initial: String(input.value), label: label };
        var sv = valueOf(seed, key, kind);
        var seedText = sv == null ? '' : 'seed ' + sv;
        var title = (hint ? hint + '. ' : '') + seedText;
        rows.push([T.h('label', { title: title }, label), T.h('span', null, input, T.h('small', { class: 'muted', style: 'margin-left:8px' }, seedText + pctHint(key, cur)))]);
      });
      var save = T.h('button', { class: 'btn primary', onclick: onSave }, 'Save (before / after confirmation)');
      var reset = T.h('button', { class: 'btn small', onclick: function () { render(); }, title: 'discard typed values' }, 'Discard changes');
      rows.push(['', T.h('span', null, save, ' ', reset)]);
      root.appendChild(T.form(rows));
      if (d.fields_note) { root.appendChild(T.h('div', { class: 'hint' }, d.fields_note)); }
      root.appendChild(T.h('div', { class: 'hint' }, 'Past postings are never restated: a fee already in the ledger keeps the schedule it was charged on; later fills, accruals and OTC quotes use the new numbers. Only the fields you change are sent.'));
      if (state.last) {
        var kv = [];
        Object.keys(state.last.after || {}).forEach(function (k) {
          kv.push([k, shown(state.last.before[k]) + ' -> ' + shown(state.last.after[k])]);
        });
        if (kv.length) { root.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'last change', T.h('small', null, 'event SETTINGS_CHANGED')), T.kv(kv))); }
      }
      if (T.convNote) { root.appendChild(T.convNote(['SWEEP', 'ACCRUAL', 'STALE'])); }
    }

    /* {patch, before, rows, errors}: only fields whose text the owner edited AND whose value differs from the server's */
    function changes() {
      var out = { patch: {}, before: {}, rows: [], errors: [] };
      Object.keys(state.inputs).forEach(function (key) {
        var inp = state.inputs[key];
        var raw = String(inp.input.value == null ? '' : inp.input.value);
        if (raw === inp.initial) { return; }                                  /* not edited: never sent */
        if (inp.current == null && raw.trim() === '') { return; }             /* not served and still empty: nothing to send */
        var n = normalise(inp.kind, raw);
        if (n.error) { out.errors.push(inp.label + ': ' + n.error); return; }
        if (same(inp.kind, n.value, inp.current)) { return; }                 /* edited back to the served value */
        out.patch[key] = n.value;
        out.before[key] = inp.current;
        out.rows.push([inp.label, shown(inp.current) + ' -> ' + n.value, key]);
      });
      return out;
    }

    function onSave() {
      var c = changes();
      if (c.errors.length) { T.ui.toast(c.errors.join('; '), 'err', 6000); return; }
      if (!c.rows.length) { T.ui.toast('no change to save', 'ok'); return; }
      var body = T.h('div', null,
        T.h('div', { class: 'hint' }, 'Only these fields of ' + account + ' change now; every other field keeps its value. Past postings are never restated; later fills use the new numbers.'),
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
              if (r && r.schedule) { state.data = mergeData(state.data, r); render(); } else { load(); }
              T.ui.toast('schedule saved (' + Object.keys(c.patch).length + ' field' + (Object.keys(c.patch).length === 1 ? '' : 's') + ')', 'ok');
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
      d.source = 'pb_schedules';
      return d;
    }

    function load() {
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

  T.components.pbschedule = { mount: mount, FIELDS: FIELDS, toCents: toCents, fromCents: fromCents, valueOf: valueOf, normalise: normalise, same: same };
})();
