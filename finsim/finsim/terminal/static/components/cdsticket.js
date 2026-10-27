/* PB page -> OTC tab -> Credit (CDX): the ticket of the CDS index swaps dealt with the prime broker - CDX.NA.IG and CDX.NA.HY 5Y,
 * the on-the-run series (the owner 2026-10-21: "Buy CDX.NA.IG 5Y protection $3.000M notional", "Buy CDX.NA.HY 5Y protection $4.000M
 * notional").  Mounted by pages/markets_otc.js under the Credit (CDX) sub-tab; the contracts table below the sub-tabs is the swap
 * ticket's, which renders every OTC kind (a CDX row says its side, notional and CS01).
 *
 *     var h = T.components.cdsticket.mount(el, {account_id: 'F1:PB_GS', blocked: null | reason, note: true, onBooked: fn(result)});
 *     h.setBlocked(reason);   // the eligibility reason: Preview / Book / Save disabled (null clears it)
 *     h.refresh();            // reloads the desk (proxy spreads, series, the editable assumptions)
 *     h.unmount();
 *
 * Reads    GET  /api/otc/cdx/desk?account_id=     (series, maturity, coupon, recovery, the FRED proxy with its date, the assumptions)
 * Previews POST /api/otc/cdx/preview {account_id, terms: {index, side, notional}}
 * Books    POST /api/otc/contracts {account_id, kind: 'CDX', terms}            (one request_id per click through T.api)
 * Edits    POST /api/otc/cdx/terms {account_id, index, basis_bp, half_spread_bp, im_pct}   (assumed, editable; recorded with before / after)
 *
 * Every number is the server's: the proxy spread (ICE BofA OAS via FRED - not a CDX dealer quote - with its date), the basis, the
 * dealer's half-spread, the upfront in USD and %, the price, the accrued rebate, the initial payment and its date, the coupon schedule,
 * CS01, the IM and the funding.  Nothing is priced here.  The terms typed are kept per index across re-renders; a preview whose terms
 * changed is marked out of date and cannot be booked.  Booking needs a confirmation that repeats the deal.  ASCII; core.js only.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};

  var INDICES = [{ key: 'IG', label: 'CDX.NA.IG 5Y' }, { key: 'HY', label: 'CDX.NA.HY 5Y' }];
  var DEFAULTS = { IG: { side: 'BUY_PROTECTION', notional: '3,000,000.00' }, HY: { side: 'BUY_PROTECTION', notional: '4,000,000.00' } };
  var DESK_MS = 60000;
  var memo = {};                                   /* account -> {index, values}: kept across re-renders */

  function copy(o) { var out = {}; Object.keys(o || {}).forEach(function (k) { out[k] = o[k]; }); return out; }
  function clean(s) { return String(s == null ? '' : s).trim().replace(/,/g, ''); }
  function money(row, key) { return T.money(row, key); }
  function describe(e) { return T.api && T.api.describe ? T.api.describe(e) : String(e && e.message || e); }
  function bp(x) { return x == null || x === '' ? 'n/a' : T.fmt.num(x, 2) + ' bp'; }
  function pct(fraction) { return fraction == null || fraction === '' ? 'n/a' : T.fmt.pct(fraction); }
  function signed(row, key) { var n = T.num(row, key); return (n != null && n > 0 ? '+' : '') + money(row, key); }
  /* a money field without its sign (the direction is said in words next to it) */
  function absMoney(row, key) {
    if (!row) { return 'n/a'; }
    var c = row[key + '_c'];
    if (typeof c === 'number' && isFinite(c)) { return T.fmt.cents(Math.abs(c)); }
    var s = row[key];
    return s == null || s === '' ? 'n/a' : T.fmt.money(String(s).replace(/^-/, ''));
  }

  var cssDone = false;
  function ensureCss() {
    if (cssDone || document.getElementById('cdsticket-css')) { cssDone = true; return; }
    cssDone = true;
    var s = document.createElement('style');
    s.id = 'cdsticket-css';
    s.textContent = '.cdsticket .form { max-width: 760px; }' +
      '.cdsticket input[type=text] { width: 150px; }' +
      '.cdsticket .cd-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }' +
      '.cdsticket .cd-actions { display: flex; gap: 8px; align-items: center; flex-wrap: nowrap; margin: 8px 0; }' +
      '.cdsticket .cd-blocked { border-left: 3px solid var(--down, #c55); }' +
      '.cdsticket .cd-proxy { margin: 6px 0; line-height: 1.45; }' +
      '.cdsticket .cd-proxy .lim { display: inline-block; padding: 0 5px; margin-left: 4px; border: 1px solid var(--accent, #c90); border-radius: 3px; font-size: 10px; color: var(--accent, #c90); }' +
      '.cdsticket .cd-warn { color: var(--accent, #c90); }' +
      '.cdsticket .cd-bad { color: var(--down, #c55); }' +
      '.cdsticket .cd-head { font-weight: 600; margin: 4px 0; }' +
      '.cdsticket .cd-plain { margin: 4px 0 8px; white-space: normal; line-height: 1.4; max-width: 760px; }' +
      '.cdsticket .cd-assume { margin: 8px 0; padding: 6px 8px; border: 1px dashed var(--line, #444); max-width: 760px; }' +
      '.cdsticket .cd-assume input[type=text] { width: 90px; }' +
      '.cdsticket .cd-grid { overflow-x: auto; max-width: 100%; }' +
      '.cdsticket table.grid td, .cdsticket table.grid th { white-space: nowrap; }' +
      '.cdsticket .kv div:nth-child(even) { white-space: normal; }' +
      '.cdsticket input.bad, .cdsticket select.bad { border-color: var(--down, #c55); outline: 1px solid var(--down, #c55); }';
    document.head.appendChild(s);
  }

  /* the terms the server takes, from what was typed: {terms} or {error, field} */
  function readTerms(index, v) {
    var n = clean(v.notional);
    if (!/^\d+(\.\d{1,2})?$/.test(n)) { return { error: 'notional: a USD amount with at most 2 decimals, e.g. 3,000,000.00', field: 'notional' }; }
    if (Number(n) < 100000) { return { error: 'notional: at least 100,000.00', field: 'notional' }; }
    return { terms: { index: index, side: v.side, notional: n } };
  }

  function mount(el, opts) {
    opts = opts || {};
    ensureCss();
    var account = opts.account_id || '';
    var kept = memo[account];
    var state = { index: kept ? kept.index : 'IG', values: kept ? kept.values : {}, previews: {}, desk: null, deskError: null, blocked: opts.blocked || null,
      busy: false, seq: 0, dead: false, timer: null, error: null, errorField: null, assumeOpen: false, showAll: false };
    INDICES.forEach(function (i) { if (!state.values[i.key]) { state.values[i.key] = copy(DEFAULTS[i.key]); } });
    if (account) { memo[account] = { index: state.index, values: state.values }; }
    var root = T.h('div', { class: 'cdsticket' });
    T.clear(el).appendChild(root);
    var ui = {};
    /* the registry's keys (keys.js; Settings > Keyboard): 'Submit the order' (Ctrl+Enter) opens the booking confirmation when
       Book is enabled - the trade is booked only from that dialog; the preview key is on every box of the form */
    root.addEventListener('keydown', function (ev) {
      if (T.keys.is(ev, 'ticket.submit')) { ev.preventDefault(); ev.stopPropagation(); if (ui.bookBtn && !ui.bookBtn.disabled) { onBook(); } }
    });

    function vals() { return state.values[state.index]; }
    function ind() { return ((state.desk || {}).indices || {})[state.index] || null; }

    function build() {
      T.clear(root);
      if (!account) { root.appendChild(T.h('div', { class: 'error' }, 'no prime-broker account given to the CDX ticket')); return; }
      ui.blocked = T.h('div', { class: 'conv cd-blocked', hidden: !state.blocked || opts.note === false }, state.blocked || '');
      root.appendChild(ui.blocked);
      ui.tabs = T.h('div'); root.appendChild(ui.tabs);
      ui.index = T.h('div', { class: 'cd-proxy' }); root.appendChild(ui.index);
      ui.form = T.h('div'); root.appendChild(ui.form);
      ui.previewBtn = T.h('button', { class: 'btn primary', onclick: onPreview }, 'Preview');
      ui.bookBtn = T.h('button', { class: 'btn', onclick: onBook, disabled: true }, 'Book');
      ui.assumeBtn = T.h('button', { class: 'btn small', title: 'the basis, the dealer half-spread and the IM used for this index (assumed, editable)', onclick: function () {
        state.assumeOpen = !state.assumeOpen; renderAssume(); } }, 'Assumptions');
      ui.resetBtn = T.h('button', { class: 'btn small', title: 'put this form back to its defaults', onclick: function () {
        state.values[state.index] = copy(DEFAULTS[state.index]); state.previews[state.index] = null; setError(null, null); renderForm(); renderPreview(); } }, 'Reset terms');
      root.appendChild(T.h('div', { class: 'cd-actions' }, ui.previewBtn, ui.bookBtn, ui.assumeBtn, ui.resetBtn));
      ui.assume = T.h('div', { class: 'cd-assume', hidden: true }); root.appendChild(ui.assume);
      ui.status = T.h('div', { class: 'hint', hidden: true }); root.appendChild(ui.status);
      ui.error = T.h('div', { class: 'error', hidden: true }); root.appendChild(ui.error);
      ui.preview = T.h('div'); root.appendChild(ui.preview);
      ui.result = T.h('div', { class: 'conv', hidden: true }); root.appendChild(ui.result);
      renderTabs(); renderIndex(); renderForm(); renderAssume(); renderPreview();
    }
    function renderTabs() {
      T.clear(ui.tabs).appendChild(T.tabs(INDICES, state.index, function (k) {
        if (k === state.index) { return; }
        state.index = k; if (memo[account]) { memo[account].index = k; }
        setError(null, null); renderTabs(); renderIndex(); renderForm(); renderAssume(); renderPreview();
      }));
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
    /* the index line and the proxy spread with its date (support LIMITED) */
    function renderIndex() {
      if (!ui.index) { return; }
      T.clear(ui.index);
      if (state.deskError) { ui.index.appendChild(T.h('div', { class: 'error' }, 'the CDX desk could not be loaded: ' + state.deskError + ' - retrying')); return; }
      var d = ind();
      if (!d) { ui.index.appendChild(T.h('div', { class: 'muted' }, 'loading the index terms and the proxy spread ...')); return; }
      ui.index.appendChild(T.h('div', null, T.h('b', null, d.name + ' S' + d.series + ' ' + d.tenor), ' . ' + d.long_name + ' (' + d.names + ' names) . matures ' + d.maturity_date +
        ' . coupon ' + bp(d.coupon_bp) + ' running . recovery ' + pct(d.recovery) + ' . quoted in ' + d.quoted + ' . rolled ' + d.roll_date + ', next roll ' + d.next_roll));
      var p = d.proxy || {};
      var line = T.h('div');
      if (p.ok) {
        line.appendChild(T.h('span', null, (p.label || 'proxy spread') + ': ' + p.series_id + ' ' + p.value_pct + ' % = ' + bp(p.spread_bp) + ' as of ' + p.as_of +
          ' (' + p.age_bd + ' business day' + (Number(p.age_bd) === 1 ? '' : 's') + ' old, ' + (p.source || 'FRED') + ')'));
        line.appendChild(T.h('span', { class: 'lim', title: 'support LIMITED: no free CDX quote exists; the spread is an index OAS proxy' }, 'LIMITED'));
        line.appendChild(T.h('div', null, 'basis adjustment ' + bp(d.basis_bp) + ' (assumed) -> mid ' + bp(d.mid_spread_bp) + ' . dealer half-spread ' + bp(d.half_spread_bp) +
          (state.desk && Number(state.desk.otc_spread_mult) !== 1 ? ' x ' + state.desk.otc_spread_mult + ' = ' + bp(d.half_spread_eff_bp) : '') + ' (assumed, against you) . IM ' + pct(d.im_pct) + ' of the notional (assumed)'));
      } else {
        /* V104R1-05: the server's reason already starts 'no CDX price: ' - said once */
        var why = String(p.reason || p.code || 'the proxy spread is unavailable');
        line.appendChild(T.h('span', { class: 'cd-bad' }, /^no CDX price/i.test(why) ? why : 'no CDX price: ' + why));
        line.appendChild(T.h('span', { class: 'lim' }, 'LIMITED'));
      }
      if (p.fred_maintenance) { line.appendChild(T.h('div', { class: 'cd-warn' }, p.fred_note || 'FRED unavailable (maintenance): the last observation is shown with its true date')); }
      ui.index.appendChild(line);
    }
    function bind(name, input) {
      input.setAttribute('data-field', name);
      var v = vals()[name];
      input.value = v == null ? '' : String(v);
      function upd() {
        vals()[name] = input.value;
        if (state.errorField === name) { setError(null, null); }
        var pv = state.previews[state.index];
        if (pv && !pv.stale) { var r = readTerms(state.index, vals()); if (!r.terms || JSON.stringify(r.terms) !== pv.sig) { pv.stale = true; renderPreview(); } }
      }
      input.addEventListener('input', upd);
      input.addEventListener('change', upd);
      input.addEventListener('keydown', function (ev) { if (T.keys.is(ev, 'ticket.preview')) { ev.preventDefault(); ev.stopPropagation(); onPreview(); } });
      if (state.errorField === name) { input.classList.add('bad'); }
      return input;
    }
    function renderForm() {
      if (!ui.form) { return; }
      var d = ind() || {};
      var rows = [];
      var side = T.h('select');
      [['BUY_PROTECTION', 'buy protection (pay the coupon; short credit)'], ['SELL_PROTECTION', 'sell protection (receive the coupon; long credit)']].forEach(function (o) {
        side.appendChild(T.h('option', { value: o[0] }, o[1]));
      });
      rows.push(T.field('side', bind('side', side), 'a protection buyer pays the fixed coupon and is paid on defaults: it gains when credit spreads widen'));
      rows.push(T.field('notional (USD)', bind('notional', T.h('input', { type: 'text', spellcheck: 'false', autocomplete: 'off', placeholder: '3,000,000.00' })),
        'the index notional (version 1, factor 1.00); at least 100,000.00'));
      T.clear(ui.form).appendChild(T.form(rows));
      ui.form.appendChild(T.h('div', { class: 'hint' }, 'The upfront and the accrued rebate settle net the next business day; the coupon (' + (d.coupon_bp ? bp(d.coupon_bp) : '100 / 500 bp') +
        ') is paid quarterly on the 20th of March, June, September and December, ACT/360, full first coupon; daily variation margin on the model value under the CSA.'));
      ui.previewBtn.disabled = !!state.blocked || state.busy;
    }
    /* the editable assumptions of this index (basis, half-spread, IM): saved per account, recorded with before / after */
    function renderAssume() {
      if (!ui.assume) { return; }
      ui.assume.hidden = !state.assumeOpen;
      if (!state.assumeOpen) { return; }
      T.clear(ui.assume);
      var d = ind();
      if (!d) { ui.assume.appendChild(T.h('div', { class: 'muted' }, 'loading ...')); return; }
      var specs = (state.desk && state.desk.term_specs) || {};
      var inputs = {};
      function inp(name, val, unit) { var i = T.h('input', { type: 'text', spellcheck: 'false', value: val, 'data-term': name }); inputs[name] = i; return T.h('span', { class: 'cd-row' }, i, T.h('span', { class: 'muted' }, unit)); }
      var imPct = d.im_pct != null && d.im_pct !== '' ? String(Number(d.im_pct) * 100) : '';
      var rows = [T.field((specs.basis_bp || {}).label || 'basis adjustment', inp('basis_bp', d.basis_bp, 'bp (seed ' + ((d.seed || {}).basis_bp || '0') + ')'), (specs.basis_bp || {}).hint || ''),
        T.field((specs.half_spread_bp || {}).label || 'dealer half-spread', inp('half_spread_bp', d.half_spread_bp, 'bp (seed ' + ((d.seed || {}).half_spread_bp || '') + ')'), (specs.half_spread_bp || {}).hint || ''),
        T.field((specs.im_pct || {}).label || 'initial margin', inp('im_pct', imPct, '% of the notional (seed ' + ((d.seed || {}).im_pct ? Number(d.seed.im_pct) * 100 : '') + ' %)'), (specs.im_pct || {}).hint || '')];
      ui.assume.appendChild(T.h('div', { class: 'cd-head' }, d.name + ' assumptions (assumed, editable; ' + (d.terms_source || 'seed') + ')'));
      ui.assume.appendChild(T.form(rows));
      var err = T.h('div', { class: 'error', hidden: true });
      var save = T.h('button', { class: 'btn small', disabled: !!state.blocked, onclick: function () {
        var body = { account_id: account, index: state.index, basis_bp: clean(inputs.basis_bp.value), half_spread_bp: clean(inputs.half_spread_bp.value), im_pct: clean(inputs.im_pct.value) + ' %' };
        save.disabled = true; err.hidden = true;
        T.api.post('/api/otc/cdx/terms', body).then(function (r) {
          save.disabled = false;
          var ch = Object.keys(r.after || {}).map(function (k) { return k + ' ' + (r.before || {})[k] + ' -> ' + r.after[k]; }).join(', ');
          T.ui.toast('CDX assumptions saved: ' + (ch || 'no change'), 'ok', 6000);
          state.previews[state.index] = null; renderPreview(); load();
        }, function (e) {
          save.disabled = false;
          if (e && e.code === 'NOTHING_TO_UPDATE') { T.ui.toast('nothing changed', 'ok', 3000); return; }
          err.textContent = describe(e); err.hidden = false;
        });
      } }, 'Save assumptions');
      ui.assume.appendChild(T.h('div', { class: 'cd-actions' }, save, T.h('span', { class: 'hint' }, 'used by later previews, bookings, marks and VM; past postings are never restated')));
      ui.assume.appendChild(err);
    }

    /* ---- preview ---- */
    function onPreview() {
      if (state.blocked) { setError(state.blocked, null); return; }
      var k = state.index;
      var r = readTerms(k, vals());
      if (r.error) { setError(r.error, r.field); return; }
      setError(null, null);
      state.busy = true; ui.previewBtn.disabled = true;
      var seq = ++state.seq;
      setStatus('pricing from the proxy spread and the USD curve ...');
      T.api.post('/api/otc/cdx/preview', { account_id: account, terms: r.terms }).then(function (pv) {
        if (state.dead || seq !== state.seq) { return; }
        state.busy = false; ui.previewBtn.disabled = !!state.blocked;
        setStatus('');
        state.previews[k] = { pv: pv, sig: JSON.stringify(r.terms), terms: r.terms, stale: false };
        renderPreview();
      }, function (e) {
        if (state.dead || seq !== state.seq) { return; }
        state.busy = false; ui.previewBtn.disabled = !!state.blocked;
        setStatus('');
        setError(describe(e), e && e.details && e.details.field);
        state.previews[k] = null;
        renderPreview();
      });
    }
    function scheduleTable(pv) {
      var rows = pv.schedule || [];
      if (!rows.length) { return null; }
      var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'pay date'), T.h('th', null, 'accrual period'), T.h('th', { class: 'num' }, 'days'),
        T.h('th', { class: 'num' }, 'coupon (USD)'), T.h('th', null, ''))));
      var body = T.h('tbody');
      var show = state.showAll ? rows : rows.slice(0, 4).concat(rows.length > 5 ? [null] : []).concat(rows.length > 4 ? [rows[rows.length - 1]] : []);
      show.forEach(function (s) {
        if (s === null) {
          body.appendChild(T.h('tr', null, T.h('td', { colspan: '5', class: 'muted' }, T.h('a', { href: '#', onclick: function (ev) { ev.preventDefault(); state.showAll = true; renderPreview(); } },
            '... ' + (rows.length - 5) + ' more coupons (show all)'))));
          return;
        }
        body.appendChild(T.h('tr', null, T.h('td', null, s.pay_date), T.h('td', null, s.start + ' .. ' + s.end), T.h('td', { class: 'num' }, String(s.days)), T.h('td', { class: 'num' }, money(s, 'amount')),
          T.h('td', { class: 'muted' }, s.direction || '')));
      });
      tbl.appendChild(body);
      return T.h('div', { class: 'cd-grid' }, tbl);
    }
    function headline(pv) {
      var px = pv.proxy || {};
      var reb = pv.accrued_rebate || {}, ip = pv.initial_payment || {}, cp = pv.coupon || {};
      var out = [['deal', pv.side_text + ' . USD ' + money(pv, 'notional')], ['counterparty', (pv.broker || '') + ' (prime broker) . ' + (pv.relationship || 'ISDA Master / CSA / PBA')],
        ['index', pv.index_name + ' S' + pv.series + ' ' + pv.tenor + ' (version ' + pv.version + ') . maturity ' + pv.maturity_date + ' . last coupon ' + pv.last_payment_date],
        ['proxy spread', px.series_id + ' ' + px.value_pct + ' % = ' + bp(px.spread_bp) + ' as of ' + px.as_of + ' - ' + (px.label || 'proxy') + ' (LIMITED)', px.series_name],
        ['mid spread', bp(pv.mid_spread_bp) + ' = proxy + basis ' + bp(pv.basis_bp) + ' (' + (pv.basis_note || 'assumed') + ')'],
        ['dealt spread', bp(pv.deal_spread_bp) + ' = mid ' + (pv.side === 'BUY_PROTECTION' ? '+' : '-') + ' half-spread ' + bp(pv.half_spread_eff_bp) + ' (assumed, against you)', pv.spread_rule],
        ['upfront', T.h('b', null, 'you ' + (T.num(pv, 'upfront') > 0 ? 'pay' : 'receive') + ' USD ' + absMoney(pv, 'upfront') + ' (' + pv.upfront_pct + ' % of notional, protection buyer\'s view)'), pv.upfront_rule],
        ['price', pv.price + (pv.quoted === 'price' ? ' (HY is quoted in price: 100 - upfront %)' : ' (100 - upfront %; IG is quoted in spread)') + ' . mid ' + pv.mid_price],
        ['accrued rebate', money(reb, 'amount') + ' for ' + reb.days + ' days (' + reb.from + ' to ' + reb.through + ' inclusive) ' + (reb.direction || ''), reb.rule],
        ['initial payment', T.h('b', null, 'USD ' + absMoney(ip, 'amount') + ' ' + (ip.direction || '') + ' on ' + ip.settle_date), ip.rule],
        ['coupon', bp(pv.coupon_bp) + ' running, ' + (cp.basis || 'ACT/360') + ', ' + (cp.dates || '') + ' . first ' + cp.first_date + ': USD ' + money(cp, 'first_amount') + ' (full first coupon) . ' + cp.count + ' coupons', cp.first_rule],
        ['value at mid', signed(pv, 'mark0') + ' (the contract\'s mark; daily variation margin moves its changes in cash)'],
        ['cost of dealing', money(pv, 'cost_of_dealing') + ' (the day-one result: the dealt spread against the mid)'],
        ['CS01', signed(pv, 'cs01') + ' per +1 bp of the index spread', pv.cs01_note],
        ['rates DV01', signed(pv, 'dv01') + ' per +1 bp of the USD curve', pv.dv01_note],
        ['credit exposure', money(pv, 'exposure') + ' (' + (pv.exposure_note || '') + ')'],
        ['initial margin', 'USD ' + money(pv, 'im') + ' . ' + (pv.im_rule || '')]];
      if (pv.funding) { out.push(['funding', pv.funding.note + (pv.funding.payment_note ? ' . ' + pv.funding.payment_note : '')]); }
      out.push(['variation margin', (pv.margin_mode || 'CTM') + ': ' + (pv.margin_mode === 'STM' ? 'settled to market each day (P&L)' : 'collateral exchanged each day on the model value (CSA)')]);
      out.push(['model', 'hazard ' + pv.hazard_mid + ' (= S / (1 - R)) . RPV01 ' + pv.rpv01_mid + ' (dealt ' + pv.rpv01_deal + ') . survival to maturity ' + pv.survival_to_maturity +
        ' . curve as of ' + ((pv.inputs || {}).curve_as_of || 'n/a'), pv.model]);
      out.push(['assumed', (pv.assumed || []).join(', ')]);
      return T.kv(out);
    }
    function renderPreview() {
      if (!ui.preview) { return; }
      T.clear(ui.preview);
      var p = state.previews[state.index];
      ui.bookBtn.disabled = true;
      if (!p) { ui.preview.appendChild(T.h('div', { class: 'hint' }, 'Preview prices the deal from the proxy spread and the USD curve - nothing is booked until you confirm.')); return; }
      var pv = p.pv;
      var blockers = pv.blockers || [];
      if (p.stale) { ui.preview.appendChild(T.h('div', { class: 'conv cd-warn' }, 'the terms changed since this preview: preview again before booking')); }
      blockers.forEach(function (b) { ui.preview.appendChild(T.h('div', { class: 'error' }, b.code + ': ' + b.message)); });
      ui.preview.appendChild(T.h('div', { class: 'cd-head' }, (pv.kind_label || 'CDS index') + ' . ' + pv.side_text));
      ui.preview.appendChild(T.h('div', { class: 'cd-plain' }, pv.plain || ''));
      ui.preview.appendChild(headline(pv));
      var sch = scheduleTable(pv);
      if (sch) { ui.preview.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'coupon schedule', T.h('small', null, 'ACT/360 on the notional; the last period includes the maturity date')), sch)); }
      ui.bookBtn.disabled = !!state.blocked || p.stale || !pv.bookable || !!blockers.length;
      ui.bookBtn.title = ui.bookBtn.disabled ? (state.blocked || (p.stale ? 'preview again' : (blockers[0] ? blockers[0].message : ''))) : 'book with a confirmation' + (T.keys.paren ? T.keys.paren('ticket.submit') : '');
    }

    /* ---- book ---- */
    function onBook() {
      var k = state.index;
      var p = state.previews[k];
      if (!p || p.stale || state.blocked) { return; }
      var pv = p.pv;
      var ip = pv.initial_payment || {};
      var lines = [['deal', pv.side_text + ' . USD ' + money(pv, 'notional')], ['counterparty', pv.broker + ' (ISDA Master / CSA / PBA)'],
        ['dealt spread', bp(pv.deal_spread_bp) + ' (mid ' + bp(pv.mid_spread_bp) + ', proxy as of ' + (pv.proxy || {}).as_of + ')'],
        ['upfront', pv.upfront_direction + ' (' + pv.upfront_pct + ' %, price ' + pv.price + ')'],
        ['initial payment', 'USD ' + absMoney(ip, 'amount') + ' ' + (ip.direction || '') + ' on ' + ip.settle_date + ' (incl. the accrued rebate ' + money(pv.accrued_rebate || {}, 'amount') + ')'],
        ['initial margin', 'USD ' + money(pv, 'im')], ['cost of dealing', 'USD ' + money(pv, 'cost_of_dealing')], ['CS01', signed(pv, 'cs01') + ' per bp']];
      if (pv.funding) { lines.push(['funding', pv.funding.note]); }
      var err = T.h('div', { class: 'error', hidden: true });
      T.ui.modal({
        title: 'Book ' + pv.index_name + ' S' + pv.series + ' 5Y', sticky: true,
        body: T.h('div', null, T.h('div', { class: 'hint' }, 'The prime broker re-prices on the inputs available at booking; if they moved, the numbers booked are the new ones (shown in the result).'),
          T.kv(lines), err),
        actions: [{ label: 'Cancel' }, { label: 'Book', cls: 'primary', onClick: function (m, btn) {
          btn.disabled = true; err.hidden = true;
          T.api.post('/api/otc/contracts', { account_id: account, kind: 'CDX', terms: p.terms }).then(function (r) {
            m.close();
            var ipr = r.initial_payment || {};
            var text = 'Booked ' + r.contract_id + ' . ' + pv.side_text + ' USD ' + money(pv, 'notional') + ' . initial payment USD ' + absMoney(ipr, 'amount') +
              ' ' + (ipr.direction || '') + ' on ' + ipr.settle_date + ' . IM USD ' + money(r.im_transfer || {}, 'amount') + ' posted';
            state.previews[k] = null; renderPreview();
            ui.result.textContent = text; ui.result.hidden = false;
            T.ui.toast(text, 'ok', 8000);
            ['accounts', 'positions', 'account_detail', 'books', 'attention'].forEach(function (n) { if (T.poll && T.poll.kick) { T.poll.kick(n); } });
            if (opts.onBooked) { try { opts.onBooked(r); } catch (e) { console.error('cdsticket onBooked', e); } }
          }, function (e) { btn.disabled = false; err.textContent = describe(e); err.hidden = false; });
        } }]
      });
    }

    /* ---- the desk: at once and every 60 s in place ---- */
    function load() {
      if (!account) { return Promise.resolve(); }
      return T.api.get('/api/otc/cdx/desk', { account_id: account }).then(function (d) {
        if (state.dead) { return; }
        state.desk = d; state.deskError = null;
        if (d && d.blocked && !state.blocked) { setBlocked(d.blocked); }
        renderIndex(); renderForm(); renderAssume();
      }, function (e) {
        if (state.dead) { return; }
        state.deskError = describe(e);
        renderIndex();
      });
    }
    function setBlocked(reason) {
      state.blocked = reason || null;
      if (ui.blocked) { ui.blocked.textContent = state.blocked || ''; ui.blocked.hidden = !state.blocked || opts.note === false; }
      if (ui.previewBtn) { ui.previewBtn.disabled = !!state.blocked || state.busy; }
      renderAssume(); renderPreview();
    }

    build();
    load();
    state.timer = setInterval(function () { if (!state.dead && document.body.contains(root)) { load(); } }, DESK_MS);
    return {
      el: root, account_id: account,
      setBlocked: setBlocked,
      refresh: load,
      unmount: function () { state.dead = true; if (state.timer) { clearInterval(state.timer); state.timer = null; } T.clear(root); }
    };
  }

  T.components.cdsticket = { mount: mount, readTerms: readTerms, DEFAULTS: DEFAULTS };
  if (T.keys && typeof T.keys.uses === 'function') { T.keys.uses('components/cdsticket.js'); }    /* its keys follow Settings > Keyboard */
})();
