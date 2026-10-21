/* The ticket (TERMINAL_DESIGN.md s17.6, s10.1-10.3, s17.4; WP-13): one component, docked on every account page.
 *
 *   T.components.ticket.open(dockBody, {account_id, instrument_id, side, qty, order_type, hedge, close, price, leg})
 *   (called by T.ticket.open in core.js; a second open on the same dock applies the fields to the live ticket)
 *
 * Fields: account (fixed to the page's account), instrument (typeahead from GET /api/instruments?q=; Enter resolves
 * through GET /api/instruments/resolve, 202 polled), side (cash BUY / SELL / SELL SHORT / BUY TO COVER; options BTO /
 * STO / BTC / STC; futures BUY / SELL - by the instrument's kind), qty with a lot stepper, type (MKT / LMT / STP /
 * STP LMT / TRAIL with a trail amount OR a trail percent - exactly one), limit, stop, TIF (DAY / GTC), borrow (implied
 * for SELL SHORT), the hedge panel (components/hedge.js: tabs Hedges / Strategies), Preview, Submit, Clear.
 *
 * Preview: POST /api/orders/preview, debounced 300 ms after the last change (Enter previews at once), re-run every 5 s
 * while a preview is open and the dock is visible (paused when the tab is hidden); any field change invalidates the
 * preview immediately (Submit disabled until the fresh one answers).  The panel is components/preview.js.
 * Submit (Ctrl+Enter) is enabled only when the preview is fresh (< 120 s by the server's expires_at), has no blockers,
 * matches the fields, and the order can execute or rest.  Every click gets one request id (crypto.randomUUID) kept in
 * T.api.pending; 503 retries the same id; a network error after the submit re-GETs the account's orders (status=all)
 * through T.api.mutate's reconcile before the button is enabled again, so a lost response never produces a duplicate
 * (the recovered order, or "lost response" with the id, is shown); X-Replayed: 1 is shown as "already submitted".
 *   409 PREVIEW_STALE  -> the diff (339.50 -> 341.10 (+0.47 %)) with Accept & submit / Cancel; Accept resubmits the
 *                         same request id with the fresh preview id (the fresh preview from the 409 body when it carries
 *                         an id, else a new preview is taken first - the core's 409 body carries an unstored preview).
 *   409 CONFIRM_REQUIRED -> "This order sweeps X from Treasury (threshold Y) - Confirm"; resubmits with confirm_large.
 *   409 QUOTE_STALE / NO_BORROW / INSUFFICIENT_FUNDS (with the three amounts) / INTEGRITY_HOLD / FUNDING_SHORTFALL and
 *   400 NOT_ELIGIBLE / NOT_ELIGIBLE_QP / LIFETIME_CLOSED / TRAIL_INVALID / AON_LEGS ... -> blockers (red, code + plain
 *   English + the convention sentence); LOCKUP is a warning, never a block.
 * A TRAILING_STOP shows the ratchet rule (sentence TRAILING) and the server's reference last / initial stop.
 * A chosen hedge candidate is submitted with the primary as ONE order (`hedge: {candidate_id, ratio, edits}`); the
 * Strategies tab submits `package: {strategy_key, units, tenor_months, strikes}` with ONE Submit (AON for option-only).
 * After a submit the result panel shows the fills with the quote used, the resting quantity, sweeps executed, the hedge
 * package, "resting until <session open>", and the orders / positions / account pollers are kicked.
 *
 * Keys inside the ticket (focus inside the dock; typing fields take only Enter / Ctrl+Enter / Esc): b s x c sides,
 * m l k j types (r = TRAIL, an addition: the design's map has no key for it), + - qty by lot (Shift x10), h hedge
 * panel, 1..5 pick a candidate / 0 none, Enter preview, Ctrl+Enter submit (also global while the dock is visible),
 * Esc clear.  Nothing here computes a price or an amount: the panel shows the server's preview; the only client-side
 * checks are "a required field is empty" (so no 400 noise).  ASCII only; styles for the ticket are injected once
 * (tokens from terminal.css; to be moved into terminal.css by the integrator).
 */
(function () {
  'use strict';
  var T = window.T;
  var SIDES = { CASH: ['BUY', 'SELL', 'SELL_SHORT', 'BUY_TO_COVER'], OPTION: ['BTO', 'STO', 'BTC', 'STC'], FUTURE: ['BUY', 'SELL'], FX: ['BUY', 'SELL'], BOND: ['BUY', 'SELL'] };
  var SIDE_LABEL = { BUY: 'BUY', SELL: 'SELL', SELL_SHORT: 'SELL SHORT', BUY_TO_COVER: 'BUY TO COVER', BTO: 'BTO buy to open', STO: 'STO sell to open', BTC: 'BTC buy to close', STC: 'STC sell to close' };
  var SIDE_KEYS = { CASH: { b: 'BUY', s: 'SELL', x: 'SELL_SHORT', c: 'BUY_TO_COVER' }, OPTION: { b: 'BTO', s: 'STO', x: 'STC', c: 'BTC' }, FUTURE: { b: 'BUY', s: 'SELL' } };
  var TYPES = [['MARKET', 'MKT'], ['LIMIT', 'LMT'], ['STOP', 'STP'], ['STOP_LIMIT', 'STP LMT'], ['TRAILING_STOP', 'TRAIL']];
  var TYPE_KEYS = { m: 'MARKET', l: 'LIMIT', k: 'STOP', j: 'STOP_LIMIT', r: 'TRAILING_STOP' };
  var DEBOUNCE_MS = 300, REPREVIEW_MS = 5000, TA_MS = 200;
  var CSS = [
    '.ticket .form{grid-template-columns:92px 1fr}.ticket .qty-row{display:flex;gap:4px}.ticket .qty-row input{flex:1;min-width:0}',
    '.ticket .status{font-family:var(--mono);font-size:11px;color:var(--fg3);min-height:16px;margin:4px 0}.ticket .status.err{color:var(--down)}.ticket .status.ok{color:var(--up)}',
    '.ticket h2{font-size:11px;margin:8px 0 4px;letter-spacing:.5px;text-transform:uppercase;color:var(--fg3)}.ticket .pv-head{font-size:13px}.ticket .pv-side{font-weight:600}',
    '.error-box{border-left:3px solid var(--down);padding:4px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}.warn-box,.amber-box{border-left:3px solid var(--warn);padding:4px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}',
    '.blue-box{border-left:3px solid var(--blue);padding:4px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}.pv-msg{font-family:var(--mono);font-size:11px}.pv-sweep{color:var(--warn);font-weight:600}.pv-blocked h2{color:var(--down)}',
    '.ticket .kv{gap:1px 10px}.ticket table th,.ticket table td{padding:2px 4px}.hg-cand{border:1px solid var(--line);border-radius:4px;padding:6px 8px;margin:6px 0}.hg-cand.picked{border-color:var(--accent)}',
    '.hg-cand kbd{margin-right:4px}.hg-payoff{width:100%;height:110px;display:block;background:var(--bg);border:1px solid var(--line);border-radius:4px}.hg-strat{margin:6px 0}',
    '.ticket .ta .dd{min-width:260px}.ticket .inst-meta{font-family:var(--mono);font-size:11px;color:var(--muted);margin:2px 0 4px}.ticket .hedge-wrap{border-top:1px solid var(--line);margin-top:8px;padding-top:6px}',
    '.ticket .fresh{font-family:var(--mono);font-size:10.5px;color:var(--fg3)}.ticket .fresh.stale{color:var(--warn)}.ticket .actions{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:6px 0}'
  ].join('');
  var st = null, ui = null, hedge = null, cssDone = false, docKeys = false;
  var instCache = {}, instKeys = [];

  /* ---------------------------------------------------------------- helpers --------------------------------------- */
  function ensureCss() { if (cssDone) { return; } cssDone = true; var s = T.h('style', { id: 'ticket-css' }); s.textContent = CSS; document.head.appendChild(s); }
  function kindGroup(kind) { if (kind === 'OPTION') { return 'OPTION'; } if (kind === 'FUTURE') { return 'FUTURE'; } if (kind === 'FX') { return 'FX'; } if (kind === 'BOND') { return 'BOND'; } return 'CASH'; }
  function guessKind(id) {
    if (!id) { return null; }
    if (/^[A-Z.^-]+\s\d{6}[CP]\d{8}$/.test(id) || /^[A-Z.^-]+\d{6}[CP]\d{8}$/.test(id)) { return 'OPTION'; }
    if (/^[A-Z]{1,3}[FGHJKMNQUVXZ]\d{2}(\.[A-Z]{3})?$/.test(id)) { return 'FUTURE'; }
    if (/^[A-Z]{6}$/.test(id) || /=X$/.test(id)) { return 'FX'; }
    return null;
  }
  function instKind() { return (st.inst && st.inst.kind) || guessKind(st.instrument_id) || 'EQUITY'; }
  function sidesFor() { return SIDES[kindGroup(instKind())] || SIDES.CASH; }
  function lot() { var l = st.inst && Number(st.inst.lot); return l && l > 0 ? l : 1; }
  function num(x) { var n = Number(String(x == null ? '' : x).replace(/,/g, '')); return isFinite(n) ? n : null; }
  function isTyping(t) { var tag = t && t.tagName; return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'; }
  function cacheInst(id, meta) { instCache[id] = meta; instKeys.push(id); while (instKeys.length > 50) { delete instCache[instKeys.shift()]; } }
  function freshFor() { if (!st.preview || !st.preview.expires_at) { return null; } return Math.floor(st.preview.expires_at - T.clock.now()); }
  function stateTicket() { T.state.ticket = st.instrument_id ? { account_id: st.account_id, instrument_id: st.instrument_id, option: instKind() === 'OPTION', side: st.side, qty: st.qty } : null; }
  function watchIds() {
    var ids = []; if (st.instrument_id) { ids.push(st.instrument_id); }
    (hedge ? hedge.legIds() : []).forEach(function (i) { if (ids.indexOf(i) < 0) { ids.push(i); } });
    if (T.ticket && T.ticket.ids) { T.ticket.ids(ids.slice(0, 40)); }
  }

  /* ---------------------------------------------------------------- state ----------------------------------------- */
  function fresh(account_id) {
    return { account_id: account_id || T.state.active_account, instrument_id: '', inst: null, side: 'BUY', qty: 1, order_type: 'MARKET', limit_px: '', stop_px: '',
             trail_mode: 'amt', trail_amt: '', trail_pct: '', tif: 'DAY', hedge: null, hedge_cand: null, hedgeOpen: false,
             preview: null, preview_body: null, previewing: false, pseq: 0, dirty: false, preview_err: null, submit_err: null, stale: null,
             submitting: false, result: null, result_kind: null, last_rid: null, timer: null, reprev: null, taTimer: null, tick: null, resolving: false };
  }
  function clearTimers() { if (!st) { return; } clearTimeout(st.timer); clearTimeout(st.reprev); clearTimeout(st.taTimer); clearInterval(st.tick); st.timer = st.reprev = st.taTimer = st.tick = null; }

  /* ---------------------------------------------------------------- open / build ---------------------------------- */
  function open(body, fields) {
    ensureCss();
    fields = fields || {};
    var acct = fields.account_id || T.state.active_account;
    if (!ui || ui.body !== body || !ui.root.isConnected) { build(body, acct); }
    else if (acct && acct !== st.account_id) { reset(acct); }
    apply(fields);
    return handle();
  }
  function handle() { return { state: function () { return st; }, preview: function () { runPreview(true); }, submit: function () { submit(); }, clear: clear, el: ui && ui.root }; }
  function build(body, acct) {
    clearTimers();
    st = fresh(acct);
    var root = T.h('div', { class: 'ticket', tabindex: '-1' });
    ui = { body: body, root: root };
    /* instrument typeahead */
    ui.inst = T.h('input', { type: 'text', id: 'ticket-instrument', placeholder: 'symbol (Enter resolves)', autocomplete: 'off', spellcheck: 'false', 'aria-label': 'instrument' });
    ui.dd = T.h('div', { class: 'dd', hidden: true });
    var ta = T.h('div', { class: 'ta' }, ui.inst, ui.dd);
    ui.instMeta = T.h('div', { class: 'inst-meta' });
    ui.side = T.h('select', { 'aria-label': 'side', id: 'ticket-side' });
    ui.qty = T.h('input', { type: 'number', min: '0', step: '1', value: '1', 'aria-label': 'quantity', id: 'ticket-qty' });
    ui.minus = T.h('button', { class: 'btn small', title: 'minus one lot (Shift: ten)', tabindex: '-1' }, '-');
    ui.plus = T.h('button', { class: 'btn small', title: 'plus one lot (Shift: ten)', tabindex: '-1' }, '+');
    ui.type = T.h('select', { 'aria-label': 'order type', id: 'ticket-type' });
    TYPES.forEach(function (t) { ui.type.appendChild(T.h('option', { value: t[0] }, t[1])); });
    ui.limit = T.h('input', { type: 'text', inputmode: 'decimal', placeholder: 'limit price', 'aria-label': 'limit price', id: 'ticket-limit' });
    ui.stop = T.h('input', { type: 'text', inputmode: 'decimal', placeholder: 'stop price', 'aria-label': 'stop price', id: 'ticket-stop' });
    ui.trailMode = T.h('select', { 'aria-label': 'trail mode', id: 'ticket-trail-mode' }, T.h('option', { value: 'amt' }, 'amount'), T.h('option', { value: 'pct' }, '%'));
    ui.trail = T.h('input', { type: 'text', inputmode: 'decimal', placeholder: 'trail', 'aria-label': 'trail', id: 'ticket-trail' });
    ui.tif = T.h('select', { 'aria-label': 'time in force', id: 'ticket-tif' }, T.h('option', { value: 'DAY' }, 'DAY'), T.h('option', { value: 'GTC' }, 'GTC (90 d)'));
    ui.borrow = T.h('input', { type: 'checkbox', checked: true, disabled: true, 'aria-label': 'borrow' });
    ui.hedgeBtn = T.h('button', { class: 'btn small', id: 'ticket-hedge-btn', title: 'hedge panel (h)' }, 'Hedges (h)');
    ui.hedgeSum = T.h('span', { class: 'muted mono', id: 'ticket-hedge-sum' });
    ui.rows = {};
    function row(key, label, input, title) { var pair = T.field(label, input, title); ui.rows[key] = pair; return pair; }
    var form = T.form([
      row('account', 'account', T.h('span', { class: 'mono', id: 'ticket-account' }, st.account_id || '')),
      row('inst', 'instrument', ta, 'type a symbol; Enter resolves it (Yahoo); pick from the list'),
      row('side', 'side', ui.side),
      row('qty', 'qty', T.h('div', { class: 'qty-row' }, ui.minus, ui.qty, ui.plus), 'whole lots; + / - step by a lot'),
      row('type', 'type', ui.type),
      row('limit', 'limit', ui.limit, 'on the tick grid; a marketable limit fills at the observed price on its first look'),
      row('stop', 'stop', ui.stop, 'triggers on the observed last trade'),
      row('trail', 'trail', T.h('div', { class: 'qty-row' }, ui.trailMode, ui.trail), 'exactly one of amount (price units) or percent'),
      row('tif', 'TIF', ui.tif),
      row('borrow', 'borrow', T.h('span', null, ui.borrow, ' ', T.h('span', { class: 'muted' }, 'implied: a locate and a borrow come first')), 'SELL SHORT opens the borrow in the same transaction (102 % collateral)'),
      row('hedge', 'hedge', T.h('span', { class: 'row' }, ui.hedgeBtn, ui.hedgeSum))
    ]);
    root.appendChild(form);
    root.appendChild(ui.instMeta);
    ui.quoteLine = T.h('div', { class: 'row', id: 'ticket-quote' }); root.appendChild(ui.quoteLine);
    ui.previewBtn = T.h('button', { class: 'btn', id: 'ticket-preview-btn', title: 'preview now (Enter)' }, 'Preview');
    ui.submitBtn = T.h('button', { class: 'btn primary', id: 'ticket-submit-btn', disabled: true, title: 'submit (Ctrl+Enter)' }, 'Submit');
    ui.clearBtn = T.h('button', { class: 'btn small', id: 'ticket-clear-btn', title: 'clear (Esc)' }, 'Clear');
    ui.freshEl = T.h('span', { class: 'fresh', id: 'ticket-fresh' });
    root.appendChild(T.h('div', { class: 'actions' }, ui.previewBtn, ui.submitBtn, ui.clearBtn, ui.freshEl));
    ui.status = T.h('div', { class: 'status', id: 'ticket-status' }); root.appendChild(ui.status);
    ui.result = T.h('div', { id: 'ticket-result' }); root.appendChild(ui.result);
    ui.preview = T.h('div', { id: 'ticket-preview' }); root.appendChild(ui.preview);
    ui.hedgeWrap = T.h('div', { class: 'hedge-wrap', id: 'ticket-hedge', hidden: true }); root.appendChild(ui.hedgeWrap);
    T.clear(body).appendChild(root);
    hedge = T.components.hedge ? T.components.hedge.mount(ui.hedgeWrap, { account_id: st.account_id, instrument_id: '', side: st.side, qty: st.qty, inst: null, onPick: onHedgePick, onLegs: watchIds, onSubmitPackage: submitPackage, tif: function () { return st.tif; } }) : null;
    if (!hedge) { T.notBuilt(ui.hedgeWrap, 'components/hedge.js: the hedge panel'); }
    wire();
    paintFields(); paintQuote(); paintSubmit();
    st.tick = setInterval(tick, 1000);
    if (!docKeys) {
      docKeys = true;
      document.addEventListener('keydown', function (e) {
        if (!ui || !ui.root.isConnected || !T.dock.visible()) { return; }
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !ui.root.contains(e.target)) { e.preventDefault(); submit(); }
      });
      T.state.on('route', function (r) { if (ui && ui.root.isConnected && r && r.account && st && r.account !== st.account_id) { reset(r.account); } });
      T.state.on('quotes', function () { if (ui && ui.root.isConnected) { paintQuote(); } });
      document.addEventListener('visibilitychange', function () { if (!document.hidden && ui && ui.root.isConnected && st.preview && !st.dirty) { scheduleReprev(0); } });
    }
  }
  function reset(acct) {
    clearTimers();
    var keepOpen = st && st.hedgeOpen;
    st = fresh(acct); st.hedgeOpen = !!keepOpen;
    T.q('#ticket-account', ui.root).textContent = st.account_id || '';
    if (hedge) { hedge.update({ account_id: st.account_id, instrument_id: '', side: st.side, qty: st.qty, inst: null }); }
    ui.inst.value = ''; T.clear(ui.result); T.clear(ui.preview); T.clear(ui.instMeta);
    paintFields(); paintQuote(); paintSubmit(); paintStatus('');
    st.tick = setInterval(tick, 1000);
    stateTicket(); watchIds();
  }
  function apply(fields) {
    var f = fields || {};
    if (f.leg && f.leg.instrument_id) { f.instrument_id = f.leg.instrument_id; }
    if (f.side) { st.side = f.side; }
    if (f.qty != null && num(f.qty) != null) { st.qty = Math.max(0, Math.floor(num(f.qty))); }
    if (f.order_type) { st.order_type = f.order_type; }
    if (f.price != null && f.leg) { st.order_type = 'LIMIT'; st.limit_px = String(f.price); }
    if (f.limit_px != null) { st.limit_px = String(f.limit_px); }
    if (f.stop_px != null) { st.stop_px = String(f.stop_px); }
    if (f.tif) { st.tif = f.tif; }
    if (f.hedge) { st.hedgeOpen = true; }
    if (f.instrument_id && f.instrument_id !== st.instrument_id) { setInstrument(f.instrument_id, f.leg ? { kind: 'OPTION', lot: 1 } : null); }
    else { paintFields(); if (st.instrument_id) { fieldChanged(); } }
    if (!f.instrument_id && !st.instrument_id) { try { ui.inst.focus(); } catch (e) { /* ignore */ } }
  }
  function clear() { reset(st.account_id); try { ui.inst.focus(); } catch (e) { /* ignore */ } }

  /* ---------------------------------------------------------------- instrument ------------------------------------ */
  function setInstrument(id, meta) {
    id = String(id || '').trim(); if (!id) { return; }
    st.instrument_id = id; st.inst = meta || instCache[id] || null; st.preview = null; st.result = null; st.submit_err = null; st.stale = null; st.hedge = null; st.hedge_cand = null;
    ui.inst.value = id; ui.dd.hidden = true;
    T.clear(ui.result);
    var sides = sidesFor(); if (sides.indexOf(st.side) < 0) { st.side = sides[0]; }
    if (st.qty < lot()) { st.qty = lot(); }
    paintFields(); paintInstMeta(); paintQuote(); stateTicket(); watchIds();
    if (hedge) { hedge.update({ account_id: st.account_id, instrument_id: id, side: st.side, qty: st.qty, inst: st.inst }); }
    fieldChanged();
    if (!instCache[id]) {
      T.api.get('/api/instruments/' + encodeURIComponent(id), { account: st.account_id }).then(function (d) {
        if (!d || d.pending) { return; }
        var m = d.instrument || d; if (d.quote && !m.quote) { m.quote = d.quote; } if (d.support && !m.support) { m.support = d.support; } if (d.session && !m.session) { m.session = d.session; }
        cacheInst(id, m);
        if (st.instrument_id !== id) { return; }
        var before = instKind(); st.inst = m;
        var sides = sidesFor(); if (sides.indexOf(st.side) < 0) { st.side = sides[0]; }
        if (st.qty < lot()) { st.qty = lot(); }
        paintFields(); paintInstMeta(); paintQuote(); stateTicket();
        if (hedge) { hedge.update({ inst: m, side: st.side, qty: st.qty }); }
        if (before !== instKind()) { fieldChanged(); }
      }, function (e) { if (st.instrument_id === id) { paintStatus('instrument ' + id + ': ' + (e.code || '') + ' ' + (e.message || ''), 'err'); } });
    }
  }
  function resolveTyped() {
    var txt = ui.inst.value.trim().toUpperCase(); if (!txt) { return; }
    if (txt === st.instrument_id) { runPreview(true); return; }
    st.resolving = true; paintStatus('resolving ' + txt + ' ...');
    T.api.getReady('/api/instruments/resolve', { symbol: txt }, 10).then(function (r) {
      st.resolving = false;
      var inst = r && (r.instrument || r);
      var id = inst && (inst.instrument_id || inst.id);
      if (!id) { paintStatus('resolve: no instrument in the answer', 'err'); return; }
      if (inst.support === 'NOT_IN_THIS_BUILD' || inst.support === 'UNSUPPORTED') { paintStatus(id + ': ' + inst.support.replace(/_/g, ' ') + (inst.reason ? ' - ' + inst.reason : ''), 'err'); cacheInst(id, inst); return; }
      cacheInst(id, inst);
      setInstrument(id, inst);
      runPreview(true);
    }, function (e) { st.resolving = false; paintStatus('resolve ' + txt + ': ' + (e.code || '') + ' ' + (e.message || ''), 'err'); });
  }
  function typeahead() {
    var q = ui.inst.value.trim(); if (q.length < 1) { ui.dd.hidden = true; return; }
    T.api.get('/api/instruments', { q: q, account: st.account_id, limit: 8 }).then(function (rows) {
      if (ui.inst.value.trim() !== q) { return; }
      var list = Array.isArray(rows) ? rows : (rows && rows.rows) || [];
      T.clear(ui.dd);
      if (!list.length) { ui.dd.hidden = true; return; }
      list.forEach(function (r) {
        var nib = r.support === 'NOT_IN_THIS_BUILD' || r.support === 'UNSUPPORTED';
        var d = T.h('div', { class: nib ? 'nib' : '' }, T.h('span', { class: 'sym' }, r.symbol || r.instrument_id), T.h('span', { class: 'nm' }, (r.name || '') + ' . ' + (r.kind || '')), nib ? T.stateTag(r.support) : null);
        d.addEventListener('mousedown', function (e) { e.preventDefault(); if (nib) { paintStatus((r.symbol || r.instrument_id) + ' is ' + r.support.replace(/_/g, ' '), 'err'); ui.dd.hidden = true; return; } cacheInst(r.instrument_id, r); setInstrument(r.instrument_id, r); runPreview(true); });
        ui.dd.appendChild(d);
      });
      ui.dd.hidden = false;
    }, function () { ui.dd.hidden = true; });
  }

  /* ---------------------------------------------------------------- fields ---------------------------------------- */
  function wire() {
    ui.inst.addEventListener('input', function () { clearTimeout(st.taTimer); st.taTimer = setTimeout(typeahead, TA_MS); });
    ui.inst.addEventListener('blur', function () { setTimeout(function () { if (ui) { ui.dd.hidden = true; } }, 150); });
    ui.side.addEventListener('change', function () { st.side = ui.side.value; paintFields(); fieldChanged(); });
    ui.qty.addEventListener('input', function () { var n = num(ui.qty.value); st.qty = n == null ? 0 : Math.floor(n); fieldChanged(); });
    ui.minus.addEventListener('click', function (e) { stepQty(-(e.shiftKey ? 10 : 1)); });
    ui.plus.addEventListener('click', function (e) { stepQty(e.shiftKey ? 10 : 1); });
    ui.type.addEventListener('change', function () { st.order_type = ui.type.value; paintFields(); fieldChanged(); });
    ui.limit.addEventListener('input', function () { st.limit_px = ui.limit.value.trim(); fieldChanged(); });
    ui.stop.addEventListener('input', function () { st.stop_px = ui.stop.value.trim(); fieldChanged(); });
    ui.trailMode.addEventListener('change', function () { st.trail_mode = ui.trailMode.value; fieldChanged(); });
    ui.trail.addEventListener('input', function () { if (st.trail_mode === 'pct') { st.trail_pct = ui.trail.value.trim(); } else { st.trail_amt = ui.trail.value.trim(); } fieldChanged(); });
    ui.tif.addEventListener('change', function () { st.tif = ui.tif.value; fieldChanged(); });
    ui.hedgeBtn.addEventListener('click', function () { toggleHedge(); });
    ui.previewBtn.addEventListener('click', function () { runPreview(true); });
    ui.submitBtn.addEventListener('click', function () { submit(); });
    ui.clearBtn.addEventListener('click', clear);
    ui.root.addEventListener('keydown', onKey);
  }
  function stepQty(n) { var l = lot(); st.qty = Math.max(0, (st.qty || 0) + n * l); st.qty = Math.round(st.qty / l) * l; paintFields(); fieldChanged(); }
  function paintFields() {
    var sides = sidesFor();
    T.clear(ui.side); sides.forEach(function (s) { ui.side.appendChild(T.h('option', { value: s, selected: s === st.side }, SIDE_LABEL[s] || s)); });
    ui.side.value = st.side;
    ui.qty.value = String(st.qty); ui.qty.step = String(lot()); ui.qty.min = '0';
    ui.type.value = st.order_type;
    var t = st.order_type;
    function show(key, on) { ui.rows[key].forEach(function (el) { el.hidden = !on; }); }
    show('limit', t === 'LIMIT' || t === 'STOP_LIMIT'); show('stop', t === 'STOP' || t === 'STOP_LIMIT'); show('trail', t === 'TRAILING_STOP');
    show('borrow', st.side === 'SELL_SHORT');
    ui.limit.value = st.limit_px; ui.stop.value = st.stop_px; ui.trailMode.value = st.trail_mode; ui.trail.value = st.trail_mode === 'pct' ? st.trail_pct : st.trail_amt;
    ui.trail.placeholder = st.trail_mode === 'pct' ? 'percent (2 dp)' : 'price units';
    ui.tif.value = st.tif;
    ui.hedgeWrap.hidden = !st.hedgeOpen;
    ui.hedgeBtn.classList.toggle('primary', !!st.hedgeOpen);
    ui.hedgeSum.textContent = st.hedge ? (st.hedge.candidate_id + (st.hedge.ratio ? ' x' + st.hedge.ratio : '') + (st.hedge.edits && Object.keys(st.hedge.edits).length ? ' (edited)' : '')) : (st.hedgeOpen ? 'none picked' : '');
  }
  function paintInstMeta() {
    T.clear(ui.instMeta);
    var m = st.inst; if (!st.instrument_id) { return; }
    if (!m) { ui.instMeta.appendChild(T.h('span', null, st.instrument_id + ' . loading the instrument ...')); return; }
    ui.instMeta.appendChild(T.h('span', null, (m.name || '') + ' . ' + (m.kind || instKind()) + ' . ' + (m.currency || '') + (m.multiplier && m.multiplier !== '1' ? ' . x' + m.multiplier : '') + (m.lot && Number(m.lot) !== 1 ? ' . lot ' + T.fmt.qty(m.lot) : '') + (m.expiry ? ' . exp ' + m.expiry : '') + ' '));
    if (m.support && m.support !== 'FULL') { ui.instMeta.appendChild(T.stateTag(m.support)); }
    if (m.support === 'LIMITED' && (m.limitation || (m.spec && m.spec.note))) { ui.instMeta.appendChild(T.h('div', { class: 'hint' }, m.limitation || m.spec.note)); }
    if (m.support === 'NOT_IN_THIS_BUILD') { ui.instMeta.appendChild(T.nib(m.name || st.instrument_id)); }
  }
  function paintQuote() {
    T.clear(ui.quoteLine);
    if (!st.instrument_id) { return; }
    var q = T.quote(st.instrument_id) || (st.inst && st.inst.quote) || (st.preview && st.preview.quote) || null;
    ui.quoteLine.appendChild(T.h('span', { class: 'muted' }, 'quote '));
    ui.quoteLine.appendChild(T.px(q, { size: 'inline' }));
    if (q && (q.bid != null || q.ask != null)) { ui.quoteLine.appendChild(T.h('span', { class: 'muted' }, ' bid / ask ')); ui.quoteLine.appendChild(T.px(q, { field: 'bid', size: 'inline' })); ui.quoteLine.appendChild(T.h('span', { class: 'muted' }, ' / ')); ui.quoteLine.appendChild(T.px(q, { field: 'ask', size: 'inline' })); }
    if (q && q.executable === false) { ui.quoteLine.appendChild(T.h('span', { class: 'badge red' }, 'NOT EXECUTABLE' + (q.reason ? ' ' + q.reason : ''))); }
  }
  function paintStatus(msg, cls) { ui.status.textContent = msg || ''; ui.status.className = 'status ' + (cls || ''); }
  function toggleHedge() { st.hedgeOpen = !st.hedgeOpen; paintFields(); if (st.hedgeOpen && hedge && st.instrument_id) { var hs = hedge.state(); if (!hs.cands && hs.status === 'idle') { if (st.preview && st.preview.hedges) { hedge.seed(st.preview.hedges); } else { hedge.load(); } } } }
  function onHedgePick(h, cand) { st.hedge = h; st.hedge_cand = cand || null; paintFields(); watchIds(); fieldChanged(); }

  /* ---------------------------------------------------------------- preview --------------------------------------- */
  function validity() {
    if (!st.instrument_id) { return 'enter an instrument'; }
    if (!(st.qty > 0)) { return 'enter a quantity'; }
    if (st.qty % lot() !== 0) { return 'quantity must be a multiple of the lot (' + T.fmt.qty(lot()) + ')'; }
    var t = st.order_type;
    if ((t === 'LIMIT' || t === 'STOP_LIMIT') && num(st.limit_px) == null) { return 'enter a limit price'; }
    if ((t === 'STOP' || t === 'STOP_LIMIT') && num(st.stop_px) == null) { return 'enter a stop price'; }
    if (t === 'TRAILING_STOP') {
      var v = st.trail_mode === 'pct' ? st.trail_pct : st.trail_amt;
      if (num(v) == null || num(v) <= 0) { return 'enter a trail ' + (st.trail_mode === 'pct' ? 'percent' : 'amount') + ' above 0'; }
      if (st.trail_mode === 'pct' && num(v) >= 100) { return 'trail percent must be below 100'; }
    }
    return null;
  }
  function previewBody() {
    var b = { account_id: st.account_id, instrument_id: st.instrument_id, side: st.side, qty: Number(st.qty), order_type: st.order_type, tif: st.tif, confirm_large: false };
    var t = st.order_type;
    if (t === 'LIMIT' || t === 'STOP_LIMIT') { b.limit_px = st.limit_px; }
    if (t === 'STOP' || t === 'STOP_LIMIT') { b.stop_px = st.stop_px; }
    if (t === 'TRAILING_STOP') { if (st.trail_mode === 'pct') { b.trail_pct = st.trail_pct; } else { b.trail_amt = st.trail_amt; } }
    if (st.side === 'SELL_SHORT') { b.borrow = true; }
    if (st.hedge) { b.hedge = { candidate_id: st.hedge.candidate_id, ratio: st.hedge.ratio, edits: st.hedge.edits || {} }; }
    return b;
  }
  function fieldChanged() {
    st.dirty = true; st.submit_err = null; st.stale = null; st.preview_err = null;
    clearTimeout(st.timer); clearTimeout(st.reprev);
    if (hedge) { hedge.update({ side: st.side, qty: st.qty }); }
    stateTicket();
    paintSubmit();
    var why = validity();
    if (why) { paintStatus(why); return; }
    paintStatus('previewing in ' + DEBOUNCE_MS + ' ms ...');
    st.timer = setTimeout(function () { runPreview(false); }, DEBOUNCE_MS);
  }
  function runPreview(manual, then) {
    clearTimeout(st.timer); clearTimeout(st.reprev);
    var why = validity(); if (why) { paintStatus(why, manual ? 'err' : ''); return; }
    var body = previewBody(); var seq = ++st.pseq; var json = JSON.stringify(body);
    st.previewing = true; paintStatus('previewing ...');
    T.api.post('/api/orders/preview', body, { retries: 1 }).then(function (pv) {
      if (seq !== st.pseq) { return; }
      st.previewing = false;
      if (pv && pv.pending) { st.preview = null; paintStatus('preview pending (202): the server is still fetching the quote', 'err'); scheduleReprev(pv.retry_ms || 1500); return; }
      st.preview = pv; st.preview_body = json; st.dirty = false; st.preview_err = null;
      paintPreview(); paintSubmit(); paintQuote();
      if (hedge && pv.hedges) { hedge.seed(pv.hedges); }
      paintStatus(pv.ok && !(pv.blockers || []).length ? 'preview ok . ' + (pv.execution && pv.execution.fill_now ? 'fills now' : 'rests / no fill now') : 'preview: ' + (pv.blockers || []).map(function (b) { return b.code; }).join(', '), pv.ok && !(pv.blockers || []).length ? 'ok' : 'err');
      scheduleReprev();
      if (then) { then(pv); }
    }, function (e) {
      if (seq !== st.pseq) { return; }
      st.previewing = false; st.preview = null; st.preview_err = e;
      paintPreview(); paintSubmit();
      paintStatus('preview failed: ' + (e.code || '') + ' ' + (e.message || ''), 'err');
      if (e.status === 409 || e.status === 503 || e.code === 'NETWORK') { scheduleReprev(); }
    });
  }
  function scheduleReprev(ms) {
    clearTimeout(st.reprev);
    st.reprev = setTimeout(function () {
      if (!ui || !ui.root.isConnected) { return; }
      if (document.hidden || !T.dock.visible() || st.submitting || st.dirty || st.stale) { scheduleReprev(); return; }
      if (st.preview || st.preview_err) { runPreview(false); }
    }, ms == null ? REPREVIEW_MS : ms);
  }
  function hedgeLegsEl() {
    var c = st.hedge_cand; if (!st.hedge || !c) { return null; }
    var box = T.h('div');
    box.appendChild(T.h('div', { class: 'row' }, T.h('b', null, c.label || c.id), T.h('span', { class: 'muted' }, ' ratio ' + (st.hedge.ratio || c.ratio || '?') + (st.hedge.edits && Object.keys(st.hedge.edits).length ? ' . edits ' + JSON.stringify(st.hedge.edits) : ''))));
    (c.legs || []).forEach(function (l) {
      var row = T.h('div', { class: 'row' }, T.h('span', { class: 'side-' + T.esc(l.side || '') }, String(l.side || '').replace(/_/g, ' ')), T.h('span', { class: 'mono' }, T.fmt.qty(l.qty) + ' ' + (l.symbol || l.instrument_id)));
      if (l.quote && l.price_basis && l.quote[l.price_basis] != null) { row.appendChild(T.px(l.quote, { field: l.price_basis, size: 'inline' })); } else if (l.est_price != null) { row.appendChild(T.h('span', { class: 'mono' }, T.fmt.px(l.est_price))); }
      box.appendChild(row);
    });
    if (c.net_cash != null) { box.appendChild(T.kv([['hedge net cash', T.h('span', { class: T.signCls(c.net_cash) }, T.fmt.money(c.net_cash))], c.protection ? ['protection', c.protection] : null])); }
    box.appendChild(T.h('div', { class: 'hint' }, c.disclosure || 'sequential; a leg can rest: the legs are released after the primary fills, sized to its filled quantity x ratio'));
    return box;
  }
  function paintPreview() {
    var P = T.components.preview;
    T.clear(ui.preview);
    if (!P) { T.notBuilt(ui.preview, 'components/preview.js'); return; }
    if (st.stale) { ui.preview.appendChild(P.diff(st.stale.diff, { onAccept: acceptStale, onCancel: function () { st.stale = null; st.dirty = true; paintPreview(); paintSubmit(); paintStatus('cancelled; taking a fresh preview ...'); runPreview(false); } })); }
    if (st.submit_err) { var b = P.errorAsBlocker(st.submit_err); ui.preview.appendChild(T.h('div', { class: 'section pv-blocked' }, T.h('h2', null, 'Submit refused'), P.blockers([b]))); }
    if (st.preview_err) { var pb = P.errorAsBlocker(st.preview_err); ui.preview.appendChild(T.h('div', { class: 'section pv-blocked' }, T.h('h2', null, 'Preview failed'), P.blockers([pb]))); }
    if (st.preview) {
      var extra = []; if (st.order_type === 'TRAILING_STOP') { extra.push('TRAILING'); }
      var box = T.h('div', { class: 'section' }); ui.preview.appendChild(box);
      P.render(box, st.preview, { quote: T.quote(st.instrument_id), hedgeLegs: hedgeLegsEl(), extraConventions: extra });
    } else if (!st.submit_err && !st.preview_err) { ui.preview.appendChild(T.h('div', { class: 'empty' }, st.instrument_id ? (st.previewing ? 'previewing ...' : 'no preview yet (Enter)') : 'enter an instrument to preview')); }
  }

  /* ---------------------------------------------------------------- submit ---------------------------------------- */
  function canSubmit() {
    if (!st.preview || st.submitting || st.dirty || st.previewing || st.stale) { return false; }
    var pv = st.preview;
    if (pv.ok === false || (pv.blockers && pv.blockers.length)) { return false; }
    var left = freshFor(); if (left != null && left <= 0) { return false; }
    if (JSON.stringify(previewBody()) !== st.preview_body) { return false; }
    var ex = pv.execution || {};
    if (ex.fill_now || ex.rests_until || st.order_type !== 'MARKET') { return true; }
    return !(pv.quote && pv.quote.executable === false);
  }
  function paintSubmit() {
    var ok = canSubmit();
    ui.submitBtn.disabled = !ok;
    var why = st.submitting ? 'submitting ...' : (st.dirty ? 'fields changed: waiting for the fresh preview' : (!st.preview ? 'no preview' : (st.stale ? 'review the diff' : (ok ? 'Ctrl+Enter' : 'blocked or expired'))));
    ui.submitBtn.title = 'submit (Ctrl+Enter) - ' + why;
    ui.submitBtn.textContent = st.submitting ? 'Submitting ...' : 'Submit';
    tick();
  }
  function tick() {
    if (!ui || !st) { return; }
    var left = freshFor();
    if (st.preview && left != null) {
      ui.freshEl.textContent = left > 0 ? 'preview fresh ' + left + ' s' : 'preview expired';
      ui.freshEl.className = 'fresh' + (left <= 10 ? ' stale' : '');
      if (left <= 0 && !ui.submitBtn.disabled) { ui.submitBtn.disabled = true; }
    } else { ui.freshEl.textContent = ''; ui.freshEl.className = 'fresh'; }
  }
  function detailsOf(e) { return T.components.preview ? T.components.preview.detailsOf(e) : (e && e.details) || {}; }
  function submit(opts) {
    opts = opts || {};
    if (!opts.force && !canSubmit()) { paintSubmit(); return; }
    var rid = opts.rid || T.uuid();
    var body = previewBody();
    body.preview_id = opts.preview_id || (st.preview && st.preview.preview_id) || null;
    body.confirm_large = !!opts.confirm_large;
    send(body, rid, 'single');
  }
  function submitPackage(body, spec) {
    if (st.submitting) { return; }
    var rid = T.uuid();
    body = Object.assign({}, body, { confirm_large: false });
    st.package_spec = spec || null;
    send(body, rid, 'package');
  }
  function send(body, rid, kind) {
    st.submitting = true; st.last_rid = rid; st.submit_err = null; st.stale = null;
    clearTimeout(st.reprev);
    paintSubmit(); paintStatus('submitting (request ' + rid.slice(0, 8) + ') ...');
    T.api.post('/api/orders', body, { request_id: rid, reconcile: st.account_id }).then(function (res) {
      st.submitting = false; st.result = res; st.result_kind = kind; st.preview = null; st.stale = null;
      paintResult(); paintPreview(); paintSubmit();
      paintStatus((res.replayed ? 'already submitted . ' : (res.recovered ? 'recovered after a lost response . ' : '')) + 'order ' + ((res.order && res.order.order_id) || (res.package && res.package.package_id) || '') + ' ' + ((res.order && res.order.status) || (res.package && res.package.status) || ''), 'ok');
      if (kind === 'single' && st.hedge) { st.hedge = null; st.hedge_cand = null; if (hedge) { hedge.none(); } paintFields(); }
      ['orders', 'positions', 'account_detail', 'accounts', 'attention'].forEach(function (p) { T.poll.kick(p); });
      scheduleReprev();
    }, function (e) {
      st.submitting = false;
      if (e && e.name === 'LostResponse') {
        st.result = { lost: true, message: e.message, request_id: rid }; st.result_kind = kind;
        paintResult(); paintSubmit(); paintStatus('lost response: ' + e.message, 'err'); T.poll.kick('orders'); return;
      }
      var d = detailsOf(e);
      if (e.code === 'PREVIEW_STALE') {
        if (d.preview && typeof d.preview === 'object' && d.preview.execution) { st.preview = d.preview; st.preview_body = JSON.stringify(previewBody()); st.dirty = false; }
        st.stale = { diff: d.diff || {}, rid: rid, kind: kind, body: body, fresh: d.preview || null };
        paintPreview(); paintSubmit(); paintStatus('PREVIEW_STALE: ' + (e.message || ''), 'err'); return;
      }
      if (e.code === 'CONFIRM_REQUIRED') {
        if (d.preview && typeof d.preview === 'object' && d.preview.execution) { st.preview = d.preview; st.preview_body = JSON.stringify(previewBody()); st.dirty = false; paintPreview(); }
        var sweep = d.sweep_in != null ? T.fmt.money(d.sweep_in) : (typeof d.sweep_in_c === 'number' ? T.fmt.cents(d.sweep_in_c) : null);
        var txt = T.h('div', null,
          T.h('div', null, sweep ? 'This order sweeps ' + sweep + ' from Treasury' + (d.threshold ? ' (confirm threshold ' + T.fmt.money(d.threshold) + ')' : '') + '.' : (e.message || 'confirmation required')),
          d.notional ? T.h('div', { class: 'hint' }, 'notional ' + T.fmt.money(d.notional) + (d.nav ? ' of lifetime NAV ' + T.fmt.money(d.nav) : '') + (d.reason ? ' - ' + d.reason : '')) : null,
          T.h('div', { class: 'hint' }, 'Confirm resubmits the same request (' + rid.slice(0, 8) + ') with confirm_large: true; nothing was written.'));
        paintStatus('CONFIRM_REQUIRED: ' + (d.reason || ''), 'err'); paintSubmit();
        T.ui.confirm(txt, { title: 'Confirm large order', ok: 'Confirm' }).then(function (yes) {
          if (!yes) { paintStatus('not confirmed; nothing was submitted'); scheduleReprev(); return; }
          var again = Object.assign({}, body, { confirm_large: true });
          if (d.preview && d.preview.preview_id) { again.preview_id = d.preview.preview_id; }
          send(again, rid, kind);
        });
        return;
      }
      st.submit_err = e; paintPreview(); paintSubmit();
      paintStatus('refused: ' + (e.code || '') + ' ' + (e.message || ''), 'err');
      if (e.status === 409 && (e.code === 'QUOTE_STALE' || e.code === 'INSUFFICIENT_FUNDS')) { scheduleReprev(); }
    });
  }
  function acceptStale() {
    var s = st.stale; if (!s) { return; }
    st.stale = null;
    if (s.fresh && s.fresh.preview_id) { send(Object.assign({}, s.body, { preview_id: s.fresh.preview_id }), s.rid, s.kind); return; }
    if (s.kind === 'package') { send(Object.assign({}, s.body, { preview_id: null }), s.rid, s.kind); return; }
    paintStatus('taking a fresh preview before resubmitting ...');
    st.dirty = true;
    runPreview(true, function (pv) { if (pv && pv.ok !== false && !(pv.blockers || []).length) { submit({ force: true, rid: s.rid, preview_id: pv.preview_id }); } else { paintStatus('the fresh preview has blockers; not resubmitted', 'err'); } });
  }
  function paintResult() {
    var P = T.components.preview; T.clear(ui.result);
    if (!st.result || !P) { return; }
    var box = T.h('div', { class: 'section' }); box.appendChild(T.h('h2', null, st.result.lost ? 'Submit: lost response' : 'Result'));
    box.appendChild(st.result_kind === 'package' ? P.packageResult(st.result) : P.result(st.result, { preview: st.result.preview }));
    box.appendChild(T.h('div', { class: 'row' }, T.h('button', { class: 'btn small', onclick: function () { T.router.go(T.router.account(st.account_id, 'orders')); } }, 'Orders'),
      T.h('button', { class: 'btn small', onclick: function () { T.router.go(T.router.account(st.account_id, 'positions')); } }, 'Positions'),
      T.h('button', { class: 'btn small', onclick: function () { st.result = null; T.clear(ui.result); } }, 'dismiss')));
    ui.result.appendChild(box);
  }

  /* ---------------------------------------------------------------- keys ------------------------------------------ */
  function onKey(e) {
    var typing = isTyping(e.target);
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.stopPropagation(); submit(); return; }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (!ui.dd.hidden) { ui.dd.hidden = true; return; } clear(); return; }
    if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); if (e.target === ui.inst) { resolveTyped(); } else { runPreview(true); } return; }
    if (typing || e.ctrlKey || e.metaKey || e.altKey) { return; }
    var k = e.key; var group = kindGroup(instKind()); var sk = SIDE_KEYS[group] || SIDE_KEYS.CASH;
    if (sk[k] && sidesFor().indexOf(sk[k]) >= 0) { st.side = sk[k]; paintFields(); fieldChanged(); }
    else if (TYPE_KEYS[k]) { st.order_type = TYPE_KEYS[k]; paintFields(); fieldChanged(); }
    else if (k === '=' || k === '+') { stepQty((k === '+' && e.location !== 3) || e.shiftKey ? 10 : 1); }       /* Shift+= is '+' on a US keyboard: x10; numpad + : x1 */
    else if (k === '-' || k === '_') { stepQty(-(k === '_' || e.shiftKey ? 10 : 1)); }
    else if (k === 'h') { toggleHedge(); }
    else if (/^[1-5]$/.test(k)) { if (hedge) { if (!st.hedgeOpen) { toggleHedge(); } hedge.pick(Number(k)); } }
    else if (k === '0') { if (hedge) { hedge.none(); } }
    else { return; }
    e.preventDefault(); e.stopPropagation();
  }

  T.components.ticket = { open: open, mount: function (el, opts) { return open(el, opts); }, state: function () { return st; }, handle: handle, submit: function () { submit(); }, preview: function () { runPreview(true); }, clear: clear, SIDES: SIDES, TYPES: TYPES };
})();
