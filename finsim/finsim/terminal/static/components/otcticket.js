/* PB page -> OTC tab -> Options / Forwards: the ticket of the OTC equity / FX options and equity / FX forwards dealt with the prime
 * broker (Build 2 P12; BUILD2_PLAN C11 / C21; the owner 2026-10-21: "cmon, OTC with prime broker isnt built either").
 *
 * Mounted by pages/markets_otc.js under the Options and Forwards sub-tabs (the contracts table below is the swap ticket's, which
 * renders every OTC kind):
 *
 *     var h = T.components.otcticket.mount(el, {account_id: 'F1:PB_GS', mode: 'options' | 'forwards', blocked: null | reason,
 *                                               desk: <GET /api/otc/options/desk>, onBooked: fn(result), note: true});
 *     (note: false - the page states the blocked reason itself above every ticket; Preview / Book stay disabled either way)
 *     h.setMode('forwards');            // the sub-tab
 *     h.setBlocked('NAV ... below the ECP floor ...');   // the reason above the forms; Preview / Book disabled (null clears it)
 *     h.setDesk(desk);                  // the pairs / tenors / dealing terms when the page reloads them
 *     h.unmount();
 *
 * Previews POST /api/otc/preview   {account_id, kind: EQ_OPTION | FX_OPTION | EQ_FORWARD | FX_FORWARD, terms}
 * Quotes   POST /api/otc/rfqs {account_id, kind, terms}  (C14, 1.0.6: "Request quote"; the simulated dealer answers on the next lifecycle tick and
 *          the quote is accepted or declined under Quote requests, above the contracts table; an option or forward is re-priced at the
 *          acceptance and refused there - QUOTE_MOVED - when its price moved since the quote)
 *
 * Conventions shown on the form
 * - Every number is the server's: the price, the vol and where it came from (the Cboe chain, a CurrencyShares ETF proxy or the pair's
 *   realised vol), SOFR, the dividend yield, the greeks, the dealer's half-width, the initial margin, the premium and its settlement
 *   date and the funding line - all labelled computed with their inputs and times.  Nothing is priced in this file.
 * - A preview the server answers "fetching" (the Cboe chain or the Yahoo daily closes are being fetched, a quote is being fetched)
 *   is asked again by itself after the server's retry_ms, a few times, saying so.
 * - The terms typed are kept per product through previews, errors, sub-tab switches and page re-renders; a preview whose terms were
 *   edited afterwards is marked out of date and cannot be booked until it is previewed again.  A refused input stays marked.
 * - Booking needs a confirmation that repeats the deal, the premium, the IM and the funding.
 * - No dependencies beyond core.js; the file is ASCII.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};

  var PRODUCTS = {
    options: [{ key: 'EQ_OPTION', label: 'Equity option' }, { key: 'FX_OPTION', label: 'FX option' }],
    forwards: [{ key: 'EQ_FORWARD', label: 'Equity forward' }, { key: 'FX_FORWARD', label: 'FX forward' }]
  };
  var TENORS = ['1W', '2W', '1M', '2M', '3M', '6M', '9M', '1Y'];
  /* P8B-03: an FX forward's tenors are the FX desk's (value dates from the spot date: ON = spot-next) - the server's list
     (desk.fx_forward_tenors), so every tenor offered is one the dealer prices */
  var FX_FWD_TENORS = ['ON', '1W', '2W', '1M', '2M', '3M', '6M', '9M', '1Y'];
  var DEFAULTS = {
    EQ_OPTION: { underlying_id: 'AAPL', side: 'BUY', right: 'PUT', style: 'EUROPEAN', strike_mode: 'pct', strike: '95', tenor: '3M', expiry: '', units: '1,000' },
    FX_OPTION: { pair: 'EURUSD', side: 'BUY', right: 'CALL', strike_mode: 'atm', strike: '', tenor: '1M', expiry: '', notional: '1,000,000.00' },
    EQ_FORWARD: { underlying_id: 'AAPL', side: 'BUY', tenor: '3M', expiry: '', units: '1,000' },
    FX_FORWARD: { pair: 'EURUSD', side: 'BUY', tenor: '3M', expiry: '', notional: '1,000,000.00' }
  };
  var RETRY_CODES = { CHAIN_FETCHING: 1, VOL_FETCHING: 1, NO_QUOTE: 1, QUOTE_STALE: 1 };
  var MAX_RETRIES = 6;
  var memo = {};                                  /* account -> {mode, product: {options, forwards}, values}: kept across re-renders */

  function copy(o) { var out = {}; Object.keys(o || {}).forEach(function (k) { out[k] = o[k]; }); return out; }
  function clean(s) { return String(s == null ? '' : s).trim().replace(/,/g, ''); }
  function sig(o) { return JSON.stringify(o); }
  function money(row, key) { return T.money(row, key); }
  function pctText(x) { if (x == null || x === '') { return 'n/a'; } var n = Number(x); return isFinite(n) ? (n * 100).toFixed(2) + ' %' : String(x); }
  function nyTime(epoch) { return epoch ? (T.fmt.dt ? T.fmt.dt(epoch) : String(epoch)) + ' ET' : ''; }
  function describe(e) { return T.api && T.api.describe ? T.api.describe(e) : String(e && e.message || e); }

  var cssDone = false;
  function ensureCss() {
    if (cssDone || document.getElementById('otcticket-css')) { cssDone = true; return; }
    cssDone = true;
    var s = document.createElement('style');
    s.id = 'otcticket-css';
    s.textContent = '.otcticket .ot-form .form { max-width: 760px; }' +
      '.otcticket input[type=text] { width: 140px; }' +
      '.otcticket select { width: auto; min-width: 120px; max-width: 260px; }' +
      '.otcticket .ot-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }' +
      '.otcticket .ot-actions { display: flex; gap: 8px; align-items: center; flex-wrap: nowrap; margin: 8px 0; }' +
      '.otcticket .ot-blocked { border-left: 3px solid var(--down, #c55); }' +
      '.otcticket .ot-preview { margin-top: 8px; }' +
      '.otcticket .ot-preview .kv div:nth-child(even) { white-space: normal; }' +
      '.otcticket .ot-grid { overflow-x: auto; max-width: 100%; }' +
      '.otcticket table.grid td, .otcticket table.grid th { white-space: nowrap; }' +
      '.otcticket .ot-stale { color: var(--accent, #c90); }' +
      '.otcticket .ot-head { font-weight: 600; margin: 4px 0; }' +
      '.otcticket input.bad, .otcticket select.bad { border-color: var(--down, #c55); outline: 1px solid var(--down, #c55); }' +
      '.otcticket .ot-status { margin: 4px 0; }';
    document.head.appendChild(s);
  }

  /* the terms the server takes, from what was typed; {terms} or {error, field} (nothing is computed here) */
  function readTerms(kind, v) {
    var t = { side: v.side };
    if (kind === 'EQ_OPTION' || kind === 'EQ_FORWARD') {
      if (!clean(v.underlying_id)) { return { error: 'type the underlying (a US stock, ETF or index)', field: 'underlying_id' }; }
      t.underlying_id = clean(v.underlying_id).toUpperCase();
      if (!/^\d+$/.test(clean(v.units)) || Number(clean(v.units)) < 1) { return { error: 'quantity: whole shares, at least 1', field: 'units' }; }
      t.units = clean(v.units);
    } else {
      t.pair = String(v.pair || '').toUpperCase();
      if (!/^\d+(\.\d{1,2})?$/.test(clean(v.notional))) { return { error: 'notional: an amount in the base currency with at most 2 decimals', field: 'notional' }; }
      t.notional = clean(v.notional);
    }
    if (kind === 'EQ_OPTION' || kind === 'FX_OPTION') {
      t.right = v.right;
      if (kind === 'EQ_OPTION') { t.style = v.style; }
      if (kind === 'FX_OPTION' && v.strike_mode === 'atm') { t.strike = 'ATM'; }
      else if (kind === 'EQ_OPTION' && v.strike_mode === 'pct') {
        if (!/^\d+(\.\d+)?$/.test(clean(v.strike))) { return { error: 'strike: a percentage of the underlying price, e.g. 95', field: 'strike' }; }
        t.strike_pct = clean(v.strike);
      } else {
        if (!/^\d+(\.\d+)?$/.test(clean(v.strike))) { return { error: 'strike: a price / rate', field: 'strike' }; }
        t.strike = clean(v.strike);
      }
    }
    if (clean(v.expiry)) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(clean(v.expiry))) { return { error: 'date: YYYY-MM-DD (or leave it empty and pick a tenor)', field: 'expiry' }; }
      t.expiry = clean(v.expiry);
    } else { t.tenor = v.tenor; }
    return { terms: t };
  }

  function mount(el, opts) {
    opts = opts || {};
    ensureCss();
    var account = opts.account_id || '';
    var kept = memo[account];
    var state = {
      mode: opts.mode === 'forwards' ? 'forwards' : 'options', blocked: opts.blocked || null, desk: opts.desk || null, dead: false,
      product: kept ? kept.product : { options: 'EQ_OPTION', forwards: 'EQ_FORWARD' }, values: kept ? kept.values : {}, previews: {},
      error: null, errorField: null, busy: false, seq: 0, status: '', result: null
    };
    Object.keys(DEFAULTS).forEach(function (k) { if (!state.values[k]) { state.values[k] = copy(DEFAULTS[k]); } });
    if (account) { memo[account] = { product: state.product, values: state.values }; }
    var root = T.h('div', { class: 'otcticket' });
    T.clear(el).appendChild(root);
    var ui = {};
    /* the registry's keys (keys.js; Settings > Keyboard): 'Submit the order' (Ctrl+Enter) opens the booking confirmation when
       Book is enabled (a fresh preview without blockers) - the deal is booked only from that dialog; the preview key is on
       every box of the form (bind below) */
    root.addEventListener('keydown', function (ev) {
      if (T.keys.is(ev, 'ticket.submit')) { ev.preventDefault(); ev.stopPropagation(); if (ui.bookBtn && !ui.bookBtn.disabled) { onRequest(); } }
    });

    function kind() { return state.product[state.mode]; }
    function vals() { return state.values[kind()]; }

    function build() {
      T.clear(root);
      if (!account) { root.appendChild(T.h('div', { class: 'error' }, 'no prime-broker account given to the OTC ticket')); return; }
      ui.blocked = T.h('div', { class: 'conv ot-blocked', hidden: !state.blocked || opts.note === false }, state.blocked || '');
      root.appendChild(ui.blocked);
      ui.products = T.h('div'); root.appendChild(ui.products);
      ui.form = T.h('div', { class: 'ot-form' }); root.appendChild(ui.form);
      ui.previewBtn = T.h('button', { class: 'btn primary', onclick: function () { onPreview(0); } }, 'Preview');
      ui.bookBtn = T.h('button', { class: 'btn', onclick: function () { onRequest(); }, disabled: true }, 'Request quote');
      ui.resetBtn = T.h('button', { class: 'btn small', title: 'put this form back to its defaults', onclick: function () {
        state.values[kind()] = copy(DEFAULTS[kind()]); state.previews[kind()] = null; state.error = null; state.errorField = null;
        renderForm(); renderPreview(); } }, 'Reset terms');
      root.appendChild(T.h('div', { class: 'ot-actions' }, ui.previewBtn, ui.bookBtn, ui.resetBtn));
      ui.status = T.h('div', { class: 'hint ot-status', hidden: true }); root.appendChild(ui.status);
      ui.error = T.h('div', { class: 'error', hidden: true }); root.appendChild(ui.error);
      ui.preview = T.h('div', { class: 'ot-preview' }); root.appendChild(ui.preview);
      ui.result = T.h('div', { class: 'conv', hidden: true }); root.appendChild(ui.result);
      renderProducts(); renderForm(); renderPreview();
    }
    function renderProducts() {
      T.clear(ui.products).appendChild(T.tabs(PRODUCTS[state.mode], kind(), function (k) {
        if (k === kind()) { return; }
        state.product[state.mode] = k; state.error = null; state.errorField = null;
        renderProducts(); renderForm(); renderPreview();
      }));
    }
    function setStatus(text) { state.status = text || ''; if (ui.status) { ui.status.textContent = state.status; ui.status.hidden = !state.status; } }
    function setError(text, field) {
      state.error = text || null; state.errorField = field || null;
      if (ui.error) { ui.error.textContent = state.error || ''; ui.error.hidden = !state.error; }
      if (ui.form) {
        T.qa('.bad', ui.form).forEach(function (x) { x.classList.remove('bad'); });
        if (field) { var b = ui.form.querySelector('[data-field="' + field + '"]'); if (b) { b.classList.add('bad'); } }
      }
    }
    function touched(name) {
      if (state.errorField === name) { setError(null, null); }
      var pv = state.previews[kind()];
      if (pv && !pv.stale) {
        var r = readTerms(kind(), vals());
        if (!r.terms || sig(r.terms) !== pv.sig) { pv.stale = true; renderPreview(); }
      }
    }
    function bind(name, input) {
      input.setAttribute('data-field', name);
      var v = vals()[name];
      if (input.tagName === 'SELECT') {
        var known = Array.prototype.some.call(input.options, function (o) { return o.value === String(v); });
        if (!known && v != null && v !== '') { input.appendChild(T.h('option', { value: String(v) }, String(v))); }
      }
      input.value = v == null ? '' : String(v);
      function upd() { vals()[name] = input.value; touched(name); }
      input.addEventListener('input', upd);
      input.addEventListener('change', function () { upd(); if (name === 'strike_mode' || name === 'pair' || name === 'side') { renderForm(); } });
      input.addEventListener('keydown', function (ev) { if (T.keys.is(ev, 'ticket.preview')) { ev.preventDefault(); ev.stopPropagation(); onPreview(0); } });
      if (state.errorField === name) { input.classList.add('bad'); }
      return input;
    }
    function text(name, placeholder, width) {
      return bind(name, T.h('input', { type: 'text', spellcheck: 'false', autocomplete: 'off', placeholder: placeholder || null, style: width ? 'width:' + width + 'px' : null }));
    }
    function select(name, options) {
      var s = T.h('select');
      options.forEach(function (o) {
        var op = T.h('option', { value: o[0] }, o[1]);
        if (o[2]) { op.disabled = true; op.title = o[3] || ''; }               /* P8B-04: listed, not dealable, with the reason */
        s.appendChild(op);
      });
      return bind(name, s);
    }
    function tenorsFor(k) {
      var d = state.desk || {};
      return k === 'FX_FORWARD' ? (d.fx_forward_tenors || FX_FWD_TENORS) : (d.tenors || TENORS);
    }
    function pairs() {
      var list = (state.desk && state.desk.pairs) || ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'USDCAD', 'AUDUSD', 'NZDUSD', 'EURJPY', 'EURGBP', 'EURCHF', 'GBPJPY'];
      var src = (state.desk && state.desk.vol_source) || {};
      var off = (state.desk && state.desk.spot_only) || {};
      /* P8B-04: a pair one of whose currencies has no current rate is spot only - listed disabled with the reason, never offered as dealable */
      return list.map(function (p) {
        if (off[p]) { return [p, p + '  (spot only - no forward price)', true, off[p]]; }
        return [p, p + (state.mode === 'options' && src[p] ? '  (' + (String(src[p]).indexOf('proxy') === 0 ? 'proxy vol' : 'realised vol') + ')' : '')];
      });
    }
    function spotOnlyNote() {
      /* one line per reason (the four CHF pairs share one), the reason cut to its first clause; the full text is each option's hover */
      var off = (state.desk && state.desk.spot_only) || {};
      var groups = {}, order = [];
      Object.keys(off).forEach(function (p) {
        var why = String(off[p]).replace(/^spot only: /, '').split(';')[0].replace(/: no forward price.*$/, '');
        if (!groups[why]) { groups[why] = []; order.push(why); }
        groups[why].push(p);
      });
      if (!order.length) { return null; }
      return T.h('div', { class: 'hint' }, 'Spot only - no forward price, so no forward or FX option: ' +
        order.map(function (w) { return groups[w].join(', ') + ' (' + w + ')'; }).join(' . '));
    }
    function expiryField(label) {
      var fwd = kind() === 'FX_FORWARD';
      return T.field(label, T.h('span', { class: 'ot-row' }, select('tenor', tenorsFor(kind()).map(function (t) { return [t, t]; })), T.h('span', { class: 'muted' }, 'or a date'),
        text('expiry', 'YYYY-MM-DD', 110)), fwd ? 'a tenor from the spot date (ON = spot-next; weeks following, months modified following); a date overrides it'
        : 'a tenor from today (months modified following on the trading calendar); a date overrides it');
    }
    function renderForm() {
      var k = kind();
      var v = vals();
      var rows = [];
      var terms = (state.desk && state.desk.terms) || {};
      if (k === 'EQ_OPTION' || k === 'EQ_FORWARD') {
        rows.push(T.field('underlying', text('underlying_id', 'AAPL', 110), 'a US-listed stock, ETF or index with a Cboe option chain (options) / a Yahoo price (forwards)'));
      } else {
        rows.push(T.field('currency pair', select('pair', pairs()), 'options: the vol of a CurrencyShares ETF on Cboe as a proxy (EUR, GBP, JPY, CHF, CAD, AUD), else the pair\'s 30-day realised vol'));
      }
      if (k === 'EQ_OPTION' || k === 'FX_OPTION') {
        rows.push(T.field('side', select('side', [['BUY', 'buy (pay the premium)'], ['SELL', 'sell (receive the premium)']]),
          'a sold option posts initial margin per the PB schedule' + (terms.option_im_pct ? ' (' + pctText(terms.option_im_pct) + ' of the underlying notional)' : '')));
        var base = String(v.pair || 'EURUSD').slice(0, 3), quote = String(v.pair || 'EURUSD').slice(3);
        rows.push(T.field('right', select('right', k === 'FX_OPTION' ? [['CALL', 'call on ' + base + ' (' + base + ' call / ' + quote + ' put)'], ['PUT', 'put on ' + base + ' (' + base + ' put / ' + quote + ' call)']]
          : [['PUT', 'put'], ['CALL', 'call']])));
        if (k === 'EQ_OPTION') {
          rows.push(T.field('style', select('style', [['EUROPEAN', 'European (exercise at expiry)'], ['AMERICAN', 'American (CRR tree)']]), 'cash-settled at the observed close on the expiry date'));
          rows.push(T.field('strike', T.h('span', { class: 'ot-row' }, select('strike_mode', [['pct', '% of the price'], ['price', 'price (USD)']]),
            text('strike', v.strike_mode === 'pct' ? '95' : '320.00', 100)), '% of the underlying\'s observed price, rounded to the cent, or a price'));
        } else {
          rows.push(T.field('strike', T.h('span', { class: 'ot-row' }, select('strike_mode', [['atm', 'at the money (the forward)'], ['rate', 'a rate']]),
            v.strike_mode === 'atm' ? T.h('span', { class: 'muted' }, 'K = the CIP forward to the delivery date') : text('strike', '1.1650', 100)), 'Garman-Kohlhagen, European'));
        }
        rows.push(expiryField('expiry'));
      } else {
        rows.push(T.field('side', select('side', k === 'FX_FORWARD' ? [['BUY', 'buy ' + String(v.pair || 'EURUSD').slice(0, 3) + ' / sell ' + String(v.pair || 'EURUSD').slice(3)],
          ['SELL', 'sell ' + String(v.pair || 'EURUSD').slice(0, 3) + ' / buy ' + String(v.pair || 'EURUSD').slice(3)]] : [['BUY', 'buy forward (long)'], ['SELL', 'sell forward (short)']]),
          'dealt at the model forward +/- the dealer\'s half-width against you' + (terms.forward_hw_bp ? ' (' + terms.forward_hw_bp + ' bp x ' + (terms.otc_spread_mult || '1') + ')' : '')));
        rows.push(expiryField(k === 'FX_FORWARD' ? 'value date' : 'maturity'));
      }
      if (k === 'EQ_OPTION' || k === 'EQ_FORWARD') {
        rows.push(T.field('quantity (shares)', text('units', '1,000', 110), 'whole shares; notional = shares x the observed price'));
      } else {
        rows.push(T.field('notional (' + String(v.pair || 'EURUSD').slice(0, 3) + ')', text('notional', '1,000,000.00', 140), 'in the base currency of the pair; at least 1,000.00'));
      }
      T.clear(ui.form).appendChild(T.form(rows));
      if (k === 'FX_FORWARD' || k === 'FX_OPTION') { var so = spotOnlyNote(); if (so) { ui.form.appendChild(so); } }
      ui.form.appendChild(T.h('div', { class: 'hint' }, k === 'FX_FORWARD' ? 'Delivered on the value date: you receive one currency and pay the other at the dealt rate (an FX deal settled in both currencies; the sold currency must be in this account or Treasury by then).'
        : (k === 'EQ_FORWARD' ? 'Cash-settled: shares x (the observed close on the maturity date - the dealt forward).' : 'The premium settles ' + (k === 'EQ_OPTION' ? 'T+1' : 'T+2 (the pair\'s spot date)') + '; daily variation margin on the model value under the CSA; cash-settled at expiry.')));
      ui.previewBtn.disabled = !!state.blocked || state.busy;
    }

    /* ---- preview ---- */
    function onPreview(attempt) {
      if (state.blocked) { setError(state.blocked, null); return; }
      var k = kind();
      var r = readTerms(k, vals());
      if (r.error) { setError(r.error, r.field); return; }
      setError(null, null);
      state.busy = true; ui.previewBtn.disabled = true;
      var seq = ++state.seq;
      setStatus(attempt ? 'still fetching the inputs ... (try ' + (attempt + 1) + ')' : 'pricing from the observed inputs ...');
      T.api.post('/api/otc/preview', { account_id: account, kind: k, terms: r.terms }).then(function (pv) {
        if (state.dead || seq !== state.seq) { return; }
        state.busy = false; ui.previewBtn.disabled = !!state.blocked;
        if (pv && pv.pending) {                                         /* 202: an input is being fetched (the chain, the daily closes, a quote) */
          var b0 = pv.body || {};
          if (attempt < MAX_RETRIES) {
            setStatus((b0.message || 'fetching the inputs') + ' (asking again in ' + Math.round(Number(pv.retry_ms || 2000) / 1000) + ' s)');
            setTimeout(function () { if (!state.dead && kind() === k) { onPreview(attempt + 1); } }, Number(pv.retry_ms) || 2000);
          } else {
            setStatus('');
            setError((b0.message || 'the inputs are still being fetched') + ' - try Preview again in a moment', null);
          }
          return;
        }
        if (pv && pv.refused) {                   /* V104R2-BW-05: a refused preview answers 200 with its reason; said in words, never a code */
          setStatus('');
          setError(pv.message || 'the dealer cannot price these terms', pv.details && pv.details.field);
          state.previews[k] = null;
          renderPreview();
          return;
        }
        state.previews[k] = { pv: pv, sig: sig(r.terms), terms: r.terms, stale: false };
        setStatus('');
        renderPreview();
        var b = (pv.blockers || [])[0];
        if (b && b.details && b.details.retry_ms && attempt < MAX_RETRIES) {     /* a stale chain: the refresh is queued - ask again */
          setStatus(b.message + ' - asking again shortly');
          setTimeout(function () { if (!state.dead && kind() === k) { onPreview(attempt + 1); } }, Number(b.details.retry_ms) || 2000);
        }
      }, function (e) {
        if (state.dead || seq !== state.seq) { return; }
        state.busy = false; ui.previewBtn.disabled = !!state.blocked;
        var retry = e && e.details && e.details.retry_ms;
        if (e && RETRY_CODES[e.code] && retry && attempt < MAX_RETRIES) {
          setStatus(e.message + ' (asking again in ' + Math.round(Number(retry) / 1000) + ' s)');
          setTimeout(function () { if (!state.dead && kind() === k) { onPreview(attempt + 1); } }, Number(retry) || 2000);
          return;
        }
        setStatus('');
        var field = e && e.details && e.details.field;
        setError(describe(e), field);
        state.previews[k] = null;
        renderPreview();
      });
    }

    function row(label, value, title) { return [label, value, title]; }
    function inputsBlock(pv) {
      var i = pv.inputs || {};
      var out = [];
      var k = pv.kind;
      if (k === 'EQ_OPTION' || k === 'EQ_FORWARD') {
        var u = i.underlying || {};
        out.push(row('underlying ' + (pv.underlying_id || ''), T.fmt.px(i.S) + ' (' + (u.label || u.source || '') + ')', 'the observed price the deal is priced from'));
      } else {
        var f = i.fx || {};
        out.push(row(pv.pair + ' spot', T.fmt.px(i.S) + ' (' + (f.source || '') + ', ' + (f.quality || '') + (f.quote_time ? ', ' + nyTime(f.quote_time) : '') + ')'));
        if (i.forward && i.forward.arithmetic) { out.push(row('forward (CIP)', i.forward.arithmetic, 'covered interest parity: the FX desk\'s forward (computed)')); }
      }
      if (k === 'EQ_OPTION' || k === 'FX_OPTION') {
        out.push(row('volatility', (Number(i.sigma) * 100).toFixed(2) + ' %', ''));
        out.push(row('vol source', T.h('span', { class: (i.sigma_quality === 'stale' || (i.vol && i.vol.quality === 'stale')) ? 'ot-stale' : '' }, i.sigma_method || i.sigma_kind || 'n/a')));
        if (i.chain) { out.push(row('chain observed', (i.chain.quote_time_ny || '') + ' (' + (i.chain.quality || '') + (i.chain.reason ? ': ' + i.chain.reason : '') + ')')); }
      }
      if (i.rate) { out.push(row('USD rate', (i.rate.label || i.rate.value_pct + ' %') + (i.r ? ', continuous ' + (Number(i.r) * 100).toFixed(4) + ' %' : ''), 'SOFR (NY Fed / FRED), else the Treasury bill yield')); }
      if (i.r_base && i.r_quote) { out.push(row('rates', (i.r_base.label || i.r_base.ccy + ' ' + i.r_base.value_pct + ' %') + ' / ' + (i.r_quote.label || i.r_quote.ccy + ' ' + i.r_quote.value_pct + ' %'))); }
      if (i.dividend) { out.push(row('dividend yield', (i.dividend.pct || '') + ' (' + (i.dividend.method || '') + ')')); }
      if (i.carry_side) {                         /* P8B-02: the mark carries at SOFR; the dealer deals on its carry for your side */
        out.push(row('mid carry', 'SOFR ' + i.sofr_pct + ' % (continuous ' + (Number(i.r_mid) * 100).toFixed(4) + ' %): the mid forward and the mark', ''));
        out.push(row('dealer\'s carry', i.carry_pct + ' % - ' + i.carry_side + ' ' + pctText(i.financing_spread) + ' (continuous ' + (Number(i.r_financing) * 100).toFixed(4) + ' %)',
          'the PB financing spread is charged once, against you, on the side you deal'));
        if (i.deal_formula) { out.push(row('dealt', i.deal_formula)); }
      } else if (i.financing_spread) { out.push(row('financing', 'SOFR ' + i.sofr_pct + ' % + PB spread ' + pctText(i.financing_spread) + ' (continuous ' + (Number(i.r_financing) * 100).toFixed(4) + ' %)')); }
      if (i.days != null) { out.push(row('time', i.days + ' days (T = ' + i.T + ')')); }
      if (i.days_to_expiry != null) { out.push(row('time', i.days_to_expiry + ' days to expiry, ' + i.days_to_delivery + ' days to delivery (T = ' + i.T + ')')); }
      out.push(row('model', pv.model || i.model || ''));
      return T.kv(out);
    }
    function greeksBlock(pv) {
      var g = pv.greeks || {}, p = pv.position_greeks || {};
      if (!pv.greeks) { return null; }
      var fx = pv.kind === 'FX_OPTION';
      var rows = [['delta', g.delta, fx ? T.fmt.money(p.delta_base, 0) + ' ' + pv.base_ccy + ' (USD ' + money(p, 'delta_usd') + ')' : T.fmt.money(p.delta_shares, 1) + ' shares (USD ' + money(p, 'delta_usd') + ')'],
                  ['gamma', g.gamma, fx ? T.fmt.money(p.gamma_base_per_pip, 0) + ' ' + pv.base_ccy + ' per pip' : T.fmt.money(p.gamma_shares_per_usd, 2) + ' shares per USD 1'],
                  ['vega (1 vol point)', g.vega, 'USD ' + money(p, 'vega_usd')], ['theta (1 day)', g.theta, 'USD ' + money(p, 'theta_usd')], ['rho (1 bp)', g.rho, 'USD ' + money(p, 'rho_usd')]];
      var tbl = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, T.h('th', null, 'greek'), T.h('th', { class: 'num' }, 'per unit'), T.h('th', { class: 'num' }, 'this position'))));
      var body = T.h('tbody');
      rows.forEach(function (r) { body.appendChild(T.h('tr', null, T.h('td', null, r[0]), T.h('td', { class: 'num' }, r[1] == null ? 'n/a' : String(r[1])), T.h('td', { class: 'num' }, r[2]))); });
      tbl.appendChild(body);
      return T.h('div', { class: 'ot-grid' }, tbl);
    }
    function headline(pv) {
      var out = [['deal', pv.side_text], ['counterparty', (pv.broker || '') + ' (prime broker) . ' + (pv.relationship || 'ISDA Master / CSA / PBA')]];
      if (pv.kind === 'EQ_OPTION' || pv.kind === 'FX_OPTION') {
        out.push(['price per unit', 'mid ' + pv.mid_per_unit + ' . dealt ' + pv.premium_per_unit + (pv.kind === 'FX_OPTION' ? ' ' + pv.quote_ccy + ' per ' + pv.base_ccy : ' USD per share'), pv.price_rule]);
        out.push(['volatility', pv.sigma_pct + ' mid, ' + pv.deal_sigma_pct + ' dealt (half-width ' + pv.half_width + ', against you)', pv.half_width_rule]);
        out.push(['premium', 'USD ' + money(pv, 'premium') + (pv.premium_quote && pv.quote_ccy !== 'USD' ? ' (' + pv.quote_ccy + ' ' + money(pv, 'premium_quote') + ')' : '') + ' ' + (pv.side === 'BUY' ? 'paid' : 'received') + ' on ' + pv.premium_date, pv.premium_rule]);
        if (pv.kind === 'FX_OPTION') { out.push(['forward / strike', 'F ' + pv.F + ' . K ' + pv.strike + ' . delivery ' + pv.delivery_date]); }
        else { out.push(['strike', 'K ' + pv.strike + ' (' + (pv.terms && pv.terms.strike_pct ? pv.terms.strike_pct + ' % of ' + pv.S : 'as typed') + ')']); }
      } else {
        if (pv.kind === 'EQ_FORWARD' && pv.forward_deal) {
          out.push(['forward', 'mid ' + pv.forward + ' (SOFR carry) . dealer\'s ' + pv.forward_deal + ' (SOFR ' + (pv.side === 'BUY' ? '+' : '-') + ' PB spread ' +
            pctText(pv.financing_spread) + ') . dealt ' + pv.strike + ' (' + pv.half_width + ' = ' + pv.half_width_px + ' against you)', pv.price_rule]);
        } else {
          out.push(['forward', 'model ' + pv.forward + ' . dealt ' + pv.strike + ' (' + pv.half_width + ' = ' + pv.half_width_px + ' against you)', pv.price_rule]);
        }
        if (pv.delivery) { out.push(['delivery', 'receive ' + pv.delivery.receive.ccy + ' ' + T.fmt.money(pv.delivery.receive.amount) + ' . pay ' + pv.delivery.pay.ccy + ' ' + T.fmt.money(pv.delivery.pay.amount) + ' on ' + pv.value_date]); }
        if (pv.delivery_check && pv.delivery_check.fundable === false) { out.push(['warning', T.h('span', { class: 'ot-stale' }, pv.delivery_check.note || 'the sold currency is not there for the value date yet')]); }
      }
      out.push(['expiry', pv.expiry + ' (' + pv.days + ' days' + (pv.tenor && pv.tenor !== 'date' ? ', ' + pv.tenor : '') + ') . ' + (pv.settlement || '')]);
      out.push(['notional', 'USD ' + money(pv, 'notional')]);
      out.push(['cost of dealing', 'USD ' + money(pv, 'cost_of_dealing') + ' (the day-one result: the dealt price against the mid model value)']);
      out.push(['value at mid', 'USD ' + money(pv, 'mark0') + ' (the contract\'s mark; daily variation margin moves its changes in cash)']);
      out.push(['initial margin', 'USD ' + money(pv, 'im'), pv.im_rule]);
      out.push(['IM rule', pv.im_rule || '']);
      if (pv.funding) { out.push(['funding', pv.funding.note + (pv.funding.premium_note ? ' . ' + pv.funding.premium_note : '')]); }
      out.push(['variation margin', (pv.margin_mode || 'CTM') + ': ' + (pv.margin_mode === 'STM' ? 'settled to market each day (P&L)' : 'collateral exchanged each day on the model value (a bought option receives it, a sold one posts it)')]);
      return T.kv(out);
    }
    function renderPreview() {
      if (!ui.preview) { return; }
      T.clear(ui.preview);
      var p = state.previews[kind()];
      ui.bookBtn.disabled = true;
      if (!p) { ui.preview.appendChild(T.h('div', { class: 'hint' }, 'Preview prices the deal from the observed inputs; Request quote then asks the dealer - nothing is' +
        ' booked before you accept its quote.')); return; }
      var pv = p.pv;
      var blockers = pv.blockers || [];
      if (p.stale) { ui.preview.appendChild(T.h('div', { class: 'conv ot-stale' }, 'the terms changed since this preview: preview again before requesting a quote')); }
      blockers.forEach(function (b) { ui.preview.appendChild(T.h('div', { class: 'error', title: b.code || '' }, b.message || T.codeWords(b.code))); });
      ui.preview.appendChild(T.h('div', { class: 'ot-head' }, (pv.kind_label || pv.kind) + ' . ' + pv.side_text));
      ui.preview.appendChild(headline(pv));
      var g = greeksBlock(pv);
      if (g) { ui.preview.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'greeks', T.h('small', null, 'computed at the mid vol')), g)); }
      ui.preview.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'inputs', T.h('small', null, 'every number is computed from these')), inputsBlock(pv)));
      ui.bookBtn.disabled = !!state.blocked || p.stale || !pv.bookable || !!blockers.length;
      ui.bookBtn.title = ui.bookBtn.disabled ? (state.blocked || (p.stale ? 'preview again' : (blockers[0] ? blockers[0].message : ''))) :
        'asks the simulated dealer of the prime broker for a quote (about 30 s; good for 120 s; re-priced at acceptance)' + (T.keys.paren ? T.keys.paren('ticket.submit') : '');
    }

    /* ---- the quote request (C14): the previewed terms go to the simulated dealer; its quote is accepted under Quote requests (an option or
       forward is re-priced at the acceptance: when the price moved it is refused and a new quote is asked) ---- */
    function onRequest() {
      var k = kind();
      var p = state.previews[k];
      if (!p || p.stale || state.blocked) { return; }
      var sw = T.components.swapticket;
      if (!sw || typeof sw.rfqRequest !== 'function') { setError('the quote requests are not available in this build (components/swapticket.js)', null); return; }
      ui.bookBtn.disabled = true;
      sw.rfqRequest(account, k, p.terms).then(function (r) {
        ui.bookBtn.disabled = false;
        var text = 'Quote requested (' + (r.rfq_id || '') + '): ' + p.pv.side_text + ' - the simulated dealer answers within about 30 s; accept or decline it under' +
          ' Quote requests';
        ui.result.textContent = text; ui.result.hidden = false;
        T.ui.toast(text, 'ok', 6000);
        if (opts.onBooked) { try { opts.onBooked(r); } catch (e) { console.error('otcticket onBooked', e); } }
      }, function (e) { ui.bookBtn.disabled = false; setError(describe(e), e && e.details && e.details.field); });
    }

    function setBlocked(reason) {
      state.blocked = reason || null;
      if (ui.blocked) { ui.blocked.textContent = state.blocked || ''; ui.blocked.hidden = !state.blocked || opts.note === false; }
      if (ui.previewBtn) { ui.previewBtn.disabled = !!state.blocked || state.busy; }
      renderPreview();
    }

    build();
    var handle = {
      el: root, account_id: account,
      setMode: function (mode) { mode = mode === 'forwards' ? 'forwards' : 'options'; if (mode === state.mode) { return; } state.mode = mode; setError(null, null); setStatus(''); renderProducts(); renderForm(); renderPreview(); },
      setBlocked: setBlocked,
      setDesk: function (d) { state.desk = d || null; renderForm(); },
      unmount: function () { state.dead = true; T.clear(root); }
    };
    return handle;
  }

  T.components.otcticket = { mount: mount, readTerms: readTerms, DEFAULTS: DEFAULTS };
  if (T.keys && typeof T.keys.uses === 'function') { T.keys.uses('components/otcticket.js'); }    /* its keys follow Settings > Keyboard */
})();
