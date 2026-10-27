/* The ticket (TERMINAL_DESIGN.md s17.6, s10.1-10.3, s17.4; WP-13): one component, docked on every account page.
 *
 *   T.components.ticket.open(dockBody, {account_id, instrument_id, side, qty, order_type, hedge, close, position_qty, kind, price, leg})
 *   (called by T.ticket.open in core.js; a second open on the same dock applies the fields to the live ticket)
 *
 * Safety rules (hotfix 2026-10-21, after the US Exchange UI sweep):
 * - A NEW instrument never inherits the previous order: qty is EMPTY and must be entered (no default size: 100 AAPL ->
 *   ESZ26 once became BUY 100 ESZ26, about 39M notional), type MKT, no limit / stop / trail, TIF DAY, no hedge pick, no
 *   hedge candidates, no preview; an in-flight preview of the previous instrument is dropped (preview sequence bumped).
 *   Fields passed in the same open() (Close: side + qty; a chain leg: side + limit) are applied after that reset.
 * - Close (`close: true` + `position_qty` signed, from Positions) takes the CLOSING side of the instrument's kind: long ->
 *   SELL (cash) / STC (option) / SELL (future, FX); short -> BUY TO COVER / BTC / BUY; qty = |position|; nothing else of
 *   the previous ticket carries over (MKT, DAY, no prices, no hedge).  The side is recomputed when the instrument's kind
 *   arrives from the server.
 * - A side the instrument's kind does not accept (SELL for an option, ...) NEVER falls back to an opening side: the side
 *   is left unset, the status says why, and nothing previews or submits until a side is chosen.
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
 * Build 2 (P10):
 * - HEDGE THIS POSITION (USX-12 / ORC-4): open({hedge: 'position'}) (Positions > Hedge, the instrument page's Hedge on a held
 *   name) hides the order fields and shows the hedge panel in position mode: candidates sized to the quantity held, and
 *   NOTHING is ordered in the instrument itself.  A one-leg hedge becomes an ordinary order on this ticket (the leg as the
 *   instrument, its side and size: preview, then submit; the line 'hedge leg for ...' says what it hedges); a hedge of several
 *   option legs is one all-or-none package submitted from the panel.  'New order instead' leaves the mode.
 * - A chain leg (open({leg})) carries its meta, so no GET /api/instruments/<OCC> is made for it (USX-35).
 * - The symbol box searches every instrument; one that is dealt on another ticket (a bond's CUSIP, an FX pair such as EURUSD,
 *   a private company, a swap) or that does not trade in this account (a foreign listing typed in US Exchange) is handed to
 *   its own ticket - in the account that trades it, opening that account's page when it is not the one shown (C11).  A failed
 *   search says so in the status line (APIUI-10).
 * - The qty label names the unit (shares, contracts, face, the base currency; OA-26); a non-USD need with an FX conversion plan
 *   offers 'convert USD -> GBP as shown' (sent as fx_convert: true; OA-12); the preview header echoes the typed limit (USX-34);
 *   the hedge candidates load by themselves while the panel is open and the order has a side and a quantity (USX-36).
 *
 * Keys inside the ticket (focus inside the dock; typing fields take only the box actions preview / submit / clear) are the
 * registry's ticket actions (keys.js, Settings > Keyboard; defaults = the 1.0.3 keys): b s x c sides, m l k j r types,
 * = + - _ qty by lot (the numpad + / - one lot, Shift ten), h hedge panel, 1..5 pick a candidate / 0 none, Enter preview,
 * Ctrl+Enter submit (also from outside the ticket while the dock is visible, never under a dialog; Cmd+Enter on a Mac),
 * Esc clear; the hints name the keys in force.  Nothing here computes a price or an amount: the panel shows the server's preview; the only client-side
 * checks are "a required field is empty" (so no 400 noise).  ASCII only; styles for the ticket are injected once
 * (tokens from terminal.css; to be moved into terminal.css by the integrator).
 */
(function () {
  'use strict';
  var T = window.T;
  var SIDES = { CASH: ['BUY', 'SELL', 'SELL_SHORT', 'BUY_TO_COVER'], OPTION: ['BTO', 'STO', 'BTC', 'STC'], FUTURE: ['BUY', 'SELL'], FX: ['BUY', 'SELL'], BOND: ['BUY', 'SELL'] };
  var SIDE_LABEL = { BUY: 'BUY', SELL: 'SELL', SELL_SHORT: 'SELL SHORT', BUY_TO_COVER: 'BUY TO COVER', BTO: 'BTO buy to open', STO: 'STO sell to open', BTC: 'BTC buy to close', STC: 'STC sell to close' };
  var SIDE_KEYS = { CASH: { b: 'BUY', s: 'SELL', x: 'SELL_SHORT', c: 'BUY_TO_COVER' }, OPTION: { b: 'BTO', s: 'STO', x: 'STC', c: 'BTC' }, FUTURE: { b: 'BUY', s: 'SELL' } };
  /* the side that CLOSES a position, by kind group (never an opening side) */
  var CLOSE_SIDES = { CASH: { LONG: 'SELL', SHORT: 'BUY_TO_COVER' }, OPTION: { LONG: 'STC', SHORT: 'BTC' }, FUTURE: { LONG: 'SELL', SHORT: 'BUY' }, FX: { LONG: 'SELL', SHORT: 'BUY' }, BOND: { LONG: 'SELL', SHORT: 'BUY' } };
  var SELL_LIKE = ['SELL', 'SELL_SHORT', 'STO', 'STC'];
  var TYPES = [['MARKET', 'MKT'], ['LIMIT', 'LMT'], ['STOP', 'STP'], ['STOP_LIMIT', 'STP LMT'], ['TRAILING_STOP', 'TRAIL']];
  var TYPE_KEYS = { m: 'MARKET', l: 'LIMIT', k: 'STOP', j: 'STOP_LIMIT', r: 'TRAILING_STOP' };
  var DEBOUNCE_MS = 300, REPREVIEW_MS = 5000, TA_MS = 200;
  /* kinds dealt on their own ticket (C11), and what the generic ticket can trade per account kind (a display rule: the server's
     preview still has the last word with NOT_TRADEABLE_HERE) */
  var OWN_TICKET = { BOND: 'the bond ticket', FX: 'the FX desk', PRIVATE: 'the private ticket', SWAP: 'the OTC tab', IRS: 'the OTC tab', TRS: 'the OTC tab', XCCY: 'the OTC tab',
                     EQ_OPTION: 'the OTC tab', FX_OPTION: 'the OTC tab', EQ_FORWARD: 'the OTC tab', FX_FORWARD: 'the OTC tab' };
  var EQUITY_LIKE = { EQUITY: 1, ETF: 1, ADR: 1, REIT: 1, PREFERRED: 1 };
  var ROLE_OF_KIND = { BOND: 'DEBT', FX: 'FOREIGN_FX', PRIVATE: 'PRIVATE' };
  var UNIT = { OPTION: 'contracts', FUTURE: 'contracts', BOND: 'face' };
  var CSS = [
    '.ticket .form{grid-template-columns:92px 1fr}.ticket .qty-row{display:flex;gap:4px}.ticket .qty-row input{flex:1;min-width:0}',
    '.ticket .status{font-family:var(--mono);font-size:11px;color:var(--fg3);min-height:16px;margin:4px 0}.ticket .status.err{color:var(--down)}.ticket .status.ok{color:var(--up)}',
    '.ticket h2{font-size:11px;margin:8px 0 4px;letter-spacing:.5px;text-transform:uppercase;color:var(--fg3)}.ticket .pv-head{font-size:13px}.ticket .pv-side{font-weight:600}',
    '.error-box{border-left:3px solid var(--down);padding:4px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}.warn-box,.amber-box{border-left:3px solid var(--warn);padding:4px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}',
    '.blue-box{border-left:3px solid var(--blue);padding:4px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}.pv-msg{font-family:var(--mono);font-size:11px}.pv-sweep{color:var(--warn);font-weight:600}.pv-blocked h2{color:var(--down)}',
    '.ticket .kv{gap:1px 10px}.ticket table th,.ticket table td{padding:2px 4px}.hg-cand{border:1px solid var(--line);border-radius:4px;padding:6px 8px;margin:6px 0}.hg-cand.picked{border-color:var(--accent)}',
    '.hg-cand kbd{margin-right:4px}.hg-payoff{width:100%;height:110px;display:block;background:var(--bg);border:1px solid var(--line);border-radius:4px}.hg-strat{margin:6px 0}',
    '.ticket .ta .dd{min-width:260px}.ticket .inst-meta{font-family:var(--mono);font-size:11px;color:var(--muted);margin:2px 0 4px}.ticket .hedge-wrap{border-top:1px solid var(--line);margin-top:8px;padding-top:6px}',
    '.ticket .fresh{font-family:var(--mono);font-size:10.5px;color:var(--fg3)}.ticket .fresh.stale{color:var(--warn)}.ticket .actions{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:6px 0}',
    '.ticket select.side-unset{border-color:var(--down);color:var(--down)}'
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
  /* OA-26: the unit of the quantity box */
  function unitLabel() { var k = instKind(); if (k === 'FX') { return String(st.instrument_id || '').slice(0, 3) + ' (base)'; } return UNIT[k] || 'shares'; }
  /* where a search row trades, for the account kind of this ticket: null = here */
  function elsewhere(r) {
    var here = T.accountKind ? T.accountKind(st.account_id) : null;
    var kind = String(r.kind || '').toUpperCase();
    if (OWN_TICKET[kind]) { return OWN_TICKET[kind]; }
    var foreign = EQUITY_LIKE[kind] && r.currency && String(r.currency).toUpperCase() !== 'USD';
    if (foreign && here === 'US_EXCHANGE') { return 'Foreign Markets & FX'; }
    if (!foreign && EQUITY_LIKE[kind] && here === 'FOREIGN_FX') { return 'US Exchange'; }
    return null;
  }
  /* the account of the selected book that trades an instrument with these account kinds, in the instrument's own order of
     preference (EURUSD: Foreign Markets & FX before Credit); Treasury never trades */
  function accountFor(kinds) {
    var list = (T.state.accounts || []).filter(function (a) { return a && a.status !== 'CLOSED' && T.accountKind(a) !== 'TREASURY'; });
    for (var k = 0; k < kinds.length; k++) {
      for (var i = 0; i < list.length; i++) { if (T.accountKind(list[i]) === kinds[k]) { return list[i].account_id; } }
    }
    return null;
  }
  /* C11 hand-over: an instrument dealt on another ticket (or in another account) is opened there; true when handed over */
  function handOff(id, row) {
    var kind = String((row && row.kind) || T.instKind(id) || '').toUpperCase();
    var where = elsewhere(row || { kind: kind });
    if (!where && !(T.ticketKinds && T.ticketKinds[kind])) { return false; }
    paintStatus((row && row.symbol || id) + ': ' + (where ? 'dealt on ' + where : 'its own ticket') + ' - opening it ...');
    T.api.get('/api/instruments/' + encodeURIComponent(id)).then(function (d) {
      var i = (d && (d.instrument || d)) || {};
      var kinds = (i.account_kinds || []).map(function (k) { return String(k).toUpperCase(); });
      var here = T.accountKind(st.account_id);
      var target = kinds.indexOf(here) >= 0 ? st.account_id : (accountFor(kinds) || (ROLE_OF_KIND[kind] ? accountFor([ROLE_OF_KIND[kind]]) : null));
      if (!target) {
        var bk = T.book.current();
        paintStatus((i.symbol || id) + ' (' + kind + ') does not trade in any account of ' + ((bk && bk.name) || 'this book') + (kind === 'PRIVATE' ? ' (Private Markets needs a qualified-purchaser book)' : ''), 'err');
        return;
      }
      if (i.kind) { T.rememberKind(id, i.kind); }
      var fields = { account_id: target, instrument_id: id, kind: i.kind || kind, side: st.side_req || undefined };
      if (kind === 'PRIVATE' && !(T.ticketKinds && T.ticketKinds.PRIVATE)) {
        T.ui.toast((i.symbol || id) + ' trades through the negotiated private ticket on the Private Markets page', '', 4500);
        T.router.go(T.router.account(target, 'markets'));
        return;
      }
      if (target === st.account_id || (T.router.current && T.router.current.account === target)) { T.ticket.open(fields); return; }
      T.ui.toast((i.symbol || id) + ' trades in ' + T.accountName(target) + ': opening its ticket there', '', 3500);
      var done = false;
      var off = T.state.on('route', function (r) { if (!done && r && r.account === target) { done = true; off(); setTimeout(function () { T.ticket.open(fields); }, 0); } });
      setTimeout(function () { if (!done) { done = true; off(); } }, 8000);
      T.router.go(T.router.account(target, 'markets'));
    }, function (e) { paintStatus('look-up of ' + id + ' failed: ' + T.api.describe(e), 'err'); });
    return true;
  }
  /* USX-35: the meta of a chain leg (no GET /api/instruments/<OCC> before the preview materialises the row) */
  function legMeta(leg, id) {
    leg = leg || {};
    var m = /^([A-Z.^-]+?)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/.exec(String(id || ''));
    var right = leg.right || (m ? m[5] : null);
    var strike = leg.strike != null ? String(leg.strike) : (m ? String(Number(m[6]) / 1000) : null);
    var expiry = leg.expiry || (m ? '20' + m[2] + '-' + m[3] + '-' + m[4] : null);
    var under = leg.underlying_id || (m ? m[1] : null);
    var q = leg.quote && typeof leg.quote === 'object' ? leg.quote : leg;
    var quote = (q.bid != null || q.ask != null) ? { bid: q.bid, ask: q.ask, bid_size: q.bid_size, ask_size: q.ask_size, mid: q.mid, last: q.last != null ? q.last : q.last_trade_price,
                                                     quality: q.quality, source: q.source, quote_time: q.quote_time, as_of: q.as_of, fetched_at: q.fetched_at, delay_label: q.delay_label, provider: q.provider } : null;
    return { instrument_id: id, symbol: id, kind: 'OPTION', name: (under ? under + ' ' : '') + (expiry || '') + ' ' + (right || '') + (strike || ''), currency: 'USD', multiplier: '100', lot: 1,
             expiry: expiry, strike: strike, right: right, underlying_id: under, support: 'FULL', quote: quote, from_chain: true };
  }
  function sidesFor() { return SIDES[kindGroup(instKind())] || SIDES.CASH; }
  function lot() { var l = st.inst && Number(st.inst.lot); return l && l > 0 ? l : 1; }
  function num(x) { var n = Number(String(x == null ? '' : x).replace(/,/g, '')); return isFinite(n) ? n : null; }
  function isTyping(t) { var tag = t && t.tagName; return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'; }
  /* the key in force of a ticket action as text ('Ctrl+Enter'; '' without a key) / ' (Ctrl+Enter)' - Settings > Keyboard */
  function kl(id) { return T.keys && T.keys.label ? T.keys.label(id) : ''; }
  function kp(id) { return T.keys && T.keys.paren ? T.keys.paren(id) : ''; }
  /* the hints that name a key, painted again when the owner changes his keys */
  function paintKeyHints() {
    if (!ui) { return; }
    var pk = kl('ticket.preview');
    ui.inst.placeholder = 'symbol' + (pk ? ' (' + pk + ' resolves)' : '');
    if (ui.rows.inst && ui.rows.inst[0]) { ui.rows.inst[0].title = 'type a symbol; ' + (pk ? pk + ' resolves it (Yahoo)' : 'Preview resolves it') + '; pick from the list'; }
    ui.hedgeBtn.title = 'hedge panel' + kp('ticket.hedge');
    ui.hedgeBtn.textContent = 'Hedges' + kp('ticket.hedge');
    ui.previewBtn.title = 'preview now' + kp('ticket.preview');
    ui.clearBtn.title = 'clear' + kp('ticket.clear');
    if (st) { paintSubmit(); }
  }
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
    return { account_id: account_id || T.state.active_account, instrument_id: '', inst: null, side: 'BUY', side_req: null, close_req: null, side_err: null,
             hedge_pos: null, hedge_for: null, fx_convert: false,
             qty: 0, order_type: 'MARKET', limit_px: '', stop_px: '',
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
    var pseq = st ? st.pseq : 0;
    st = fresh(acct); st.pseq = pseq + 1;           /* a response of the previous ticket can never match the new sequence */
    var root = T.h('div', { class: 'ticket', tabindex: '-1' });
    ui = { body: body, root: root };
    /* instrument typeahead */
    ui.inst = T.h('input', { type: 'text', id: 'ticket-instrument', placeholder: 'symbol (Enter resolves)', autocomplete: 'off', spellcheck: 'false', 'aria-label': 'instrument' });
    ui.dd = T.h('div', { class: 'dd', hidden: true });
    var ta = T.h('div', { class: 'ta' }, ui.inst, ui.dd);
    ui.instMeta = T.h('div', { class: 'inst-meta' });
    ui.side = T.h('select', { 'aria-label': 'side', id: 'ticket-side' });
    ui.qty = T.h('input', { type: 'number', min: '0', step: '1', value: '', placeholder: 'qty (required)', 'aria-label': 'quantity', id: 'ticket-qty' });
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
    ui.fxConv = T.h('input', { type: 'checkbox', id: 'ticket-fx-convert', 'aria-label': 'convert USD as shown' });
    ui.fxConvLabel = T.h('label', { for: 'ticket-fx-convert', class: 'muted' }, 'convert USD as shown');
    ui.rows = {};
    function row(key, label, input, title) { var pair = T.field(label, input, title); ui.rows[key] = pair; return pair; }
    var form = T.form([
      row('account', 'account', T.h('span', { id: 'ticket-account', title: st.account_id || '' }, st.account_id ? T.accountName(st.account_id) : '')),
      row('inst', 'instrument', ta, 'type a symbol; Enter resolves it (Yahoo); pick from the list'),
      row('side', 'side', ui.side),
      row('qty', 'qty', T.h('div', { class: 'qty-row' }, ui.minus, ui.qty, ui.plus), 'whole lots; + / - step by a lot; empty for every new instrument (it must be entered)'),
      row('type', 'type', ui.type),
      row('limit', 'limit', ui.limit, 'on the tick grid; a marketable limit fills at the observed price on its first look'),
      row('stop', 'stop', ui.stop, 'triggers on the observed last trade'),
      row('trail', 'trail', T.h('div', { class: 'qty-row' }, ui.trailMode, ui.trail), 'exactly one of amount (price units) or percent'),
      row('tif', 'TIF', ui.tif),
      row('borrow', 'borrow', T.h('span', null, ui.borrow, ' ', T.h('span', { class: 'muted' }, 'implied: a locate and a borrow come first')), 'SELL SHORT opens the borrow in the same transaction (102 % collateral)'),
      row('fxconv', 'FX', T.h('span', null, ui.fxConv, ' ', ui.fxConvLabel), 'the order\'s currency is short in this account and Treasury: tick to buy it with USD at the fill, at the plan shown in the preview'),
      row('hedge', 'hedge', T.h('span', { class: 'row' }, ui.hedgeBtn, ui.hedgeSum))
    ]);
    ui.modeBox = T.h('div', { class: 'blue-box', id: 'ticket-mode', hidden: true });
    root.appendChild(ui.modeBox);
    ui.form = form;
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
    hedge = T.components.hedge ? T.components.hedge.mount(ui.hedgeWrap, { account_id: st.account_id, instrument_id: '', side: st.side, qty: st.qty, inst: null, onPick: onHedgePick, onLegs: watchIds, onSubmitPackage: submitPackage,
                                                                          tif: function () { return st.tif; }, onTradeLeg: onTradeLeg,
                                                                          orderInProgress: function () { return !!(st && st.instrument_id && st.qty > 0 && !st.hedge_pos); },
                                                                          isOpen: function () { return !!(st && st.hedgeOpen && ui && ui.root.isConnected && T.dock.visible()); } }) : null;
    if (!hedge) { T.notBuilt(ui.hedgeWrap, 'components/hedge.js: the hedge panel'); }
    wire();
    paintFields(); paintQuote(); paintSubmit();
    st.tick = setInterval(tick, 1000);
    paintKeyHints();
    if (!docKeys) {
      docKeys = true;
      /* 'Submit the order' (Ctrl+Enter by default) also from outside the ticket while the dock shows it - never under an
         open dialog (the key belongs to the dialog then; KB-R1-09) */
      document.addEventListener('keydown', function (e) {
        if (!ui || !ui.root.isConnected || !T.dock.visible()) { return; }
        if (T.ui && T.ui.dialogs && T.ui.dialogs()) { return; }
        if (T.keys.is(e, 'ticket.submit') && !ui.root.contains(e.target)) { e.preventDefault(); submit(); }
      });
      T.state.on('keybinds', function () { if (ui && ui.root.isConnected) { paintKeyHints(); } });
      T.state.on('route', function (r) { if (ui && ui.root.isConnected && r && r.account && st && r.account !== st.account_id) { reset(r.account); } });
      T.state.on('quotes', function () { if (ui && ui.root.isConnected) { paintQuote(); } });
      document.addEventListener('visibilitychange', function () { if (!document.hidden && ui && ui.root.isConnected && st.preview && !st.dirty) { scheduleReprev(0); } });
    }
  }
  function reset(acct) {
    clearTimers();
    var keepOpen = st && st.hedgeOpen && !st.hedge_pos; var pseq = st ? st.pseq : 0;
    st = fresh(acct); st.hedgeOpen = !!keepOpen; st.pseq = pseq + 1;
    var ta_ = T.q('#ticket-account', ui.root); ta_.textContent = st.account_id ? T.accountName(st.account_id) : ''; ta_.title = st.account_id || '';      /* V104R1-04: the name, the id in the tooltip */
    if (hedge) { hedge.update({ account_id: st.account_id, instrument_id: '', side: st.side, qty: st.qty, inst: null, mode: 'order', position: null }); }
    ui.inst.value = ''; T.clear(ui.result); T.clear(ui.preview); T.clear(ui.instMeta);
    paintFields(); paintQuote(); paintSubmit(); paintStatus('');
    st.tick = setInterval(tick, 1000);
    stateTicket(); watchIds();
  }
  function apply(fields) {
    var f = fields || {};
    if (f.leg && f.leg.instrument_id) { f.instrument_id = f.leg.instrument_id; }
    var id = f.instrument_id ? String(f.instrument_id).trim() : '';
    var newInst = !!(id && id !== st.instrument_id);
    if (f.leg && id && !instCache[id]) { cacheInst(id, legMeta(f.leg, id)); }          /* USX-35: the chain leg's own meta, no look-up */
    /* 0. hedge this position (USX-12): no primary order at all; anything else leaves that mode */
    if (f.hedge === 'position' && id) { enterPositionMode(id, f); return; }
    if (st.hedge_pos && (id || f.side || f.close || f.qty != null)) { leavePositionMode(); }
    /* 1. a new instrument resets the whole order; a Close on the same instrument resets it too (nothing carries over) */
    if (newInst) { setInstrument(id, f.leg ? (instCache[id] || legMeta(f.leg, id)) : (f.kind && !instCache[id] ? { kind: String(f.kind) } : null), true); }
    else if (f.close) { resetOrder(); st.result = null; T.clear(ui.result); paintPreview(); }
    if (f.close) { st.hedgeOpen = false; }
    st.hedge_for = f.hedge_for || (newInst || f.close ? null : st.hedge_for);
    /* 2. the caller's fields */
    if (f.close) {
      var pq = num(f.position_qty);
      st.close_req = pq ? (pq > 0 ? 'LONG' : 'SHORT') : (f.side ? (SELL_LIKE.indexOf(String(f.side)) >= 0 ? 'LONG' : 'SHORT') : null);
      st.side_req = null;
      if (!st.close_req) { st.side = ''; }
    } else if (f.side) {
      /* a Close ticket re-sided by a later prefill (the instrument page opens every visit with BUY) is no longer that close:
         its quantity must be entered again, so a Close -> chart visit never leaves BUY <position qty> ready to submit */
      if (!newInst && st.close_req && String(f.side) !== st.side && f.qty == null) { st.qty = 0; }
      st.side_req = String(f.side); st.close_req = null;
    }
    if (f.qty != null && num(f.qty) != null) { st.qty = Math.max(0, Math.floor(Math.abs(num(f.qty)))); }
    if (f.order_type) { st.order_type = f.order_type; }
    if (f.price != null && f.leg) { st.order_type = 'LIMIT'; st.limit_px = String(f.price); }
    if (f.limit_px != null) { st.limit_px = String(f.limit_px); }
    if (f.stop_px != null) { st.stop_px = String(f.stop_px); }
    if (f.tif) { st.tif = f.tif; }
    if (f.hedge) { st.hedgeOpen = true; }
    /* 3. the side for the instrument's kind (a Close takes the closing side; an unaccepted side is never replaced) */
    settleSide();
    if (f.close && !st.close_req) { st.side_err = 'Close: the position direction is unknown; choose the side'; }
    paintFields(); stateTicket();
    if (st.instrument_id) { fieldChanged(); }
    if (!id && !st.instrument_id) { try { ui.inst.focus(); } catch (e) { /* ignore */ } }
  }
  /* the per-order fields a new instrument (or a Close) never inherits; drops the preview, the hedge pick and any in-flight
     preview (the sequence is bumped, so a late answer for the previous order is ignored) */
  function resetOrder() {
    clearTimeout(st.timer); clearTimeout(st.reprev);
    st.qty = 0; st.order_type = 'MARKET'; st.limit_px = ''; st.stop_px = ''; st.trail_mode = 'amt'; st.trail_amt = ''; st.trail_pct = ''; st.tif = 'DAY';
    st.side_req = null; st.close_req = null; st.side_err = null; st.fx_convert = false;
    st.preview = null; st.preview_body = null; st.preview_err = null; st.submit_err = null; st.stale = null; st.previewing = false; st.pseq++;
    st.hedge = null; st.hedge_cand = null; st.package_spec = null;
    if (hedge && hedge.picked && hedge.picked()) { hedge.none(); }
  }
  /* ---- hedge this position (USX-12 / ORC-4): the panel in position mode; the order fields are not used ---- */
  function enterPositionMode(id, f) {
    var ik = f.inst_kind || (f.kind && String(f.kind).toUpperCase() !== 'POSITION_HEDGE' ? f.kind : null);
    var meta = f.leg ? null : (ik && !instCache[id] ? { kind: String(ik).toUpperCase() } : null);
    if (id !== st.instrument_id) { setInstrument(id, meta, true); } else { resetOrder(); }
    resetOrder();
    st.result = null; T.clear(ui.result);
    var pq = num(f.position_qty);
    st.hedge_pos = { instrument_id: id, qty: pq, kind: ik || instKind() };
    st.hedge_for = null; st.side = ''; st.qty = 0; st.hedgeOpen = true;
    if (hedge) { hedge.update({ account_id: st.account_id, instrument_id: id, mode: 'position', position: pq != null ? { qty: pq, symbol: id, instrument_id: id } : null, side: '', qty: 0, inst: st.inst }); hedge.tab('hedges'); hedge.load(); }
    paintFields(); paintPreview(); paintSubmit(); stateTicket(); watchIds();
    paintStatus('hedge your ' + id + ' position: pick a hedge below - nothing is ordered in ' + id);
  }
  function leavePositionMode() {
    if (!st.hedge_pos) { return; }
    st.hedge_pos = null; st.side = st.side || 'BUY';
    if (hedge) { hedge.update({ mode: 'order', position: null }); }
    paintFields();
  }
  /* a one-leg hedge of the position becomes an ordinary order on this ticket (the panel's 'Trade this leg') */
  function onTradeLeg(fields, cand) {
    var pos = st.hedge_pos;
    leavePositionMode();
    st.hedgeOpen = false;
    apply(Object.assign({}, fields, { hedge_for: { label: (cand && cand.label) || '', position: pos } }));
    paintFields(); paintInstMeta();
    try { ui.qty.focus(); } catch (e) { /* focus is a convenience */ }
  }
  /* the side for the instrument's kind.  A Close takes the kind's closing side; a requested side (or the current one) is
     kept only when the kind accepts it.  Never a fallback to an opening side: an unaccepted side leaves the side UNSET
     with the reason in side_err, and validity() then stops the preview and the submit until a side is chosen. */
  function settleSide() {
    var sides = sidesFor(), kind = instKind();
    st.side_err = null;
    if (st.close_req) {
      var cs = (CLOSE_SIDES[kindGroup(kind)] || CLOSE_SIDES.CASH)[st.close_req];
      if (cs && sides.indexOf(cs) >= 0) { st.side = cs; return; }
      st.side = ''; st.side_err = 'Close: no closing side for a ' + st.close_req.toLowerCase() + ' ' + kind + ' position; choose the side';
      return;
    }
    var want = st.side_req || st.side;
    if (want && sides.indexOf(want) >= 0) { st.side = want; return; }
    st.side = '';
    if (st.side_req) { st.side_err = 'side ' + (SIDE_LABEL[st.side_req] || st.side_req) + ' does not apply to ' + kind + ' (sides: ' + sides.map(function (s) { return SIDE_LABEL[s] || s; }).join(' / ') + '): choose the side'; }
  }
  function clear() { reset(st.account_id); try { ui.inst.focus(); } catch (e) { /* ignore */ } }

  /* ---------------------------------------------------------------- instrument ------------------------------------ */
  function setInstrument(id, meta, deferPreview) {
    id = String(id || '').trim(); if (!id) { return; }
    if (meta && meta.kind) { T.rememberKind(id, meta.kind); }
    var changed = id !== st.instrument_id;
    st.instrument_id = id; st.inst = meta || instCache[id] || null; st.result = null;
    if (changed) { resetOrder(); }                 /* nothing of the previous instrument's order carries over (qty empty, MKT, DAY, no hedge) */
    else { st.preview = null; st.submit_err = null; st.stale = null; st.hedge = null; st.hedge_cand = null; st.previewing = false; st.pseq++; }
    ui.inst.value = id; ui.dd.hidden = true;
    T.clear(ui.result);
    settleSide();
    paintFields(); paintInstMeta(); paintQuote(); paintPreview(); stateTicket(); watchIds();
    if (hedge) { hedge.update({ account_id: st.account_id, instrument_id: id, side: st.side, qty: st.qty, inst: st.inst }); }
    if (!deferPreview) { fieldChanged(); }
    if (!instCache[id]) {
      T.api.get('/api/instruments/' + encodeURIComponent(id), { account: st.account_id }).then(function (d) {
        if (!d || d.pending) { return; }
        var m = d.instrument || d; if (d.quote && !m.quote) { m.quote = d.quote; } if (d.support && !m.support) { m.support = d.support; } if (d.session && !m.session) { m.session = d.session; }
        cacheInst(id, m);
        if (st.instrument_id !== id) { return; }
        var before = instKind(), sideBefore = st.side; st.inst = m;
        settleSide();                                  /* the real kind: a Close recomputes its closing side; an unaccepted side stays unset */
        paintFields(); paintInstMeta(); paintQuote(); stateTicket();
        if (hedge) { hedge.update({ inst: m, side: st.side, qty: st.qty }); }
        if (before !== instKind() || sideBefore !== st.side) { fieldChanged(); }
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
      if (handOff(id, inst)) { return; }
      setInstrument(id, inst);
      runPreview(true);
    }, function (e) {
      st.resolving = false;
      /* the resolver knows Yahoo symbols; a CUSIP, a pair or a company name is in the master: the first search hit is used */
      T.api.get('/api/instruments', { q: txt, limit: 5 }).then(function (rows) {
        var list = Array.isArray(rows) ? rows : (rows && rows.rows) || [];
        var hit = list.filter(function (r) { return String(r.symbol || '').toUpperCase() === txt || String(r.instrument_id || '').toUpperCase() === txt || String(r.instrument_id || '').toUpperCase().indexOf(txt) >= 0; })[0];
        if (hit && handOff(hit.instrument_id, hit)) { return; }
        if (hit) { cacheInst(hit.instrument_id, hit); setInstrument(hit.instrument_id, hit); runPreview(true); return; }
        paintStatus('resolve ' + txt + ': ' + (e.code || '') + ' ' + (e.message || ''), 'err');
      }, function () { paintStatus('resolve ' + txt + ': ' + (e.code || '') + ' ' + (e.message || ''), 'err'); });
    });
  }
  function typeahead() {
    var q = ui.inst.value.trim(); if (q.length < 1) { ui.dd.hidden = true; return; }
    T.api.get('/api/instruments', { q: q, limit: 10 }).then(function (rows) {
      if (ui.inst.value.trim() !== q) { return; }
      var list = Array.isArray(rows) ? rows : (rows && rows.rows) || [];
      T.rememberKinds(list);
      /* what trades here first, then what another ticket / account deals (tagged with where) */
      list = list.filter(function (r) { return !elsewhere(r); }).concat(list.filter(function (r) { return !!elsewhere(r); }));
      T.clear(ui.dd);
      if (!list.length) { ui.dd.hidden = true; paintStatus('no instrument matches ' + q + ' (Enter looks the symbol up on Yahoo)'); return; }
      list.forEach(function (r) {
        var nib = r.support === 'NOT_IN_THIS_BUILD' || r.support === 'UNSUPPORTED';
        var where = elsewhere(r);
        var d = T.h('div', { class: nib ? 'nib' : '' }, T.h('span', { class: 'sym' }, r.symbol || r.instrument_id), T.h('span', { class: 'nm' }, (r.name || '') + ' . ' + (r.kind || '') + (r.currency && r.currency !== 'USD' ? ' . ' + r.currency : '')),
          nib ? T.stateTag(r.support) : null, where ? T.h('span', { class: 'muted', title: 'opens ' + where }, ' -> ' + where) : null);
        d.addEventListener('mousedown', function (e) {
          e.preventDefault(); ui.dd.hidden = true;
          if (nib) { paintStatus((r.symbol || r.instrument_id) + ' is ' + r.support.replace(/_/g, ' '), 'err'); return; }
          cacheInst(r.instrument_id, r);
          if (handOff(r.instrument_id, r)) { return; }
          setInstrument(r.instrument_id, r); runPreview(true);
        });
        ui.dd.appendChild(d);
      });
      ui.dd.hidden = false;
    }, function (e) { ui.dd.hidden = true; paintStatus('search ' + q + ' failed: ' + T.api.describe(e), 'err'); });
  }

  /* ---------------------------------------------------------------- fields ---------------------------------------- */
  function wire() {
    ui.inst.addEventListener('input', function () { clearTimeout(st.taTimer); st.taTimer = setTimeout(typeahead, TA_MS); });
    ui.inst.addEventListener('blur', function () { setTimeout(function () { if (ui) { ui.dd.hidden = true; } }, 150); });
    ui.side.addEventListener('change', function () { st.side = ui.side.value; st.side_req = null; st.close_req = null; st.side_err = null; paintFields(); fieldChanged(); });
    ui.qty.addEventListener('input', function () { var n = num(ui.qty.value); st.qty = n == null ? 0 : Math.max(0, Math.floor(n)); fieldChanged(); });
    ui.minus.addEventListener('click', function (e) { stepQty(-(e.shiftKey ? 10 : 1)); });
    ui.plus.addEventListener('click', function (e) { stepQty(e.shiftKey ? 10 : 1); });
    ui.type.addEventListener('change', function () { st.order_type = ui.type.value; paintFields(); fieldChanged(); });
    ui.limit.addEventListener('input', function () { st.limit_px = ui.limit.value.trim(); fieldChanged(); });
    ui.stop.addEventListener('input', function () { st.stop_px = ui.stop.value.trim(); fieldChanged(); });
    ui.trailMode.addEventListener('change', function () { st.trail_mode = ui.trailMode.value; fieldChanged(); });
    ui.trail.addEventListener('input', function () { if (st.trail_mode === 'pct') { st.trail_pct = ui.trail.value.trim(); } else { st.trail_amt = ui.trail.value.trim(); } fieldChanged(); });
    ui.tif.addEventListener('change', function () { st.tif = ui.tif.value; fieldChanged(); });
    ui.fxConv.addEventListener('change', function () { st.fx_convert = !!ui.fxConv.checked; fieldChanged(); });
    ui.hedgeBtn.addEventListener('click', function () { toggleHedge(); });
    ui.previewBtn.addEventListener('click', function () { runPreview(true); });
    ui.submitBtn.addEventListener('click', function () { submit(); });
    ui.clearBtn.addEventListener('click', clear);
    ui.root.addEventListener('keydown', onKey);
  }
  function stepQty(n) { var l = lot(); st.qty = Math.max(0, (st.qty || 0) + n * l); st.qty = Math.round(st.qty / l) * l; paintFields(); fieldChanged(); }
  function paintMode() {
    var pos = st.hedge_pos;
    T.clear(ui.modeBox); ui.modeBox.hidden = !pos && !st.hedge_for;
    var hide = !!pos;
    ['side', 'qty', 'type', 'limit', 'stop', 'trail', 'tif', 'borrow', 'fxconv', 'hedge'].forEach(function (k) { if (hide && ui.rows[k]) { ui.rows[k].forEach(function (el) { el.hidden = true; }); } });
    ui.previewBtn.hidden = hide; ui.submitBtn.hidden = hide; ui.freshEl.hidden = hide;
    if (pos) {
      var q = pos.qty; var what = q != null && isFinite(q) && q !== 0 ? (q > 0 ? 'long ' : 'short ') + T.fmt.qty(Math.abs(q)) + ' ' + pos.instrument_id : 'the ' + pos.instrument_id + ' position';
      ui.modeBox.appendChild(T.h('div', null, T.h('b', null, 'Hedge this position: '), what + ' (' + T.accountName(st.account_id) + ')'));
      ui.modeBox.appendChild(T.h('div', { class: 'hint' }, 'no order in ' + pos.instrument_id + ': pick a hedge below; only its legs trade'));
      ui.modeBox.appendChild(T.h('button', { class: 'btn small', id: 'ticket-leave-hedge', onclick: function () { leavePositionMode(); st.hedgeOpen = false; paintFields(); fieldChanged(); } }, 'New order instead'));
    } else if (st.hedge_for) {
      var hp = st.hedge_for.position || {}; var hq = Number(hp.qty);
      ui.modeBox.appendChild(T.h('div', null, T.h('b', null, 'Hedge leg: '), (st.hedge_for.label || '') + (isFinite(hq) && hq ? ' - for your ' + (hq > 0 ? 'long ' : 'short ') + T.fmt.qty(Math.abs(hq)) + ' ' + (hp.instrument_id || hp.symbol || '') : '')));
      ui.modeBox.appendChild(T.h('div', { class: 'hint' }, 'its own order: preview, then submit; nothing else is ordered'));
    }
  }
  function paintFields() {
    if (st.hedge_pos) { ui.hedgeWrap.hidden = false; ui.hedgeBtn.classList.add('primary'); paintMode(); return; }
    ui.previewBtn.hidden = false; ui.submitBtn.hidden = false; ui.freshEl.hidden = false;
    ['side', 'qty', 'type', 'tif', 'hedge'].forEach(function (k) { if (ui.rows[k]) { ui.rows[k].forEach(function (el) { el.hidden = false; }); } });
    var sides = sidesFor();
    T.clear(ui.side);
    if (!st.side || sides.indexOf(st.side) < 0) { ui.side.appendChild(T.h('option', { value: '', selected: true, disabled: true }, 'choose a side')); }
    sides.forEach(function (s) { ui.side.appendChild(T.h('option', { value: s, selected: s === st.side }, SIDE_LABEL[s] || s)); });
    ui.side.value = sides.indexOf(st.side) >= 0 ? st.side : '';
    ui.side.classList.toggle('side-unset', !(st.side && sides.indexOf(st.side) >= 0));
    ui.side.title = st.side_err || '';
    if (document.activeElement !== ui.qty || num(ui.qty.value) !== st.qty) { ui.qty.value = st.qty > 0 ? String(st.qty) : ''; }
    ui.qty.step = String(lot()); ui.qty.min = '0';
    if (ui.rows.qty && ui.rows.qty[0]) { ui.rows.qty[0].textContent = 'qty (' + unitLabel() + ')'; }
    ui.qty.placeholder = unitLabel() + ' (required)';
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
    paintFx();
    paintMode();
  }
  /* OA-12: the conversion box only when the latest preview offers a USD -> currency plan */
  function paintFx() {
    var P = T.components.preview; var fc = P && P.fxConversionOf ? P.fxConversionOf(st.preview) : null;
    var on = !!(fc || st.fx_convert) && !st.hedge_pos;
    if (ui.rows.fxconv) { ui.rows.fxconv.forEach(function (el) { el.hidden = !on; }); }
    ui.fxConv.checked = !!st.fx_convert;
    ui.fxConvLabel.textContent = 'convert USD -> ' + ((fc && fc.buy_ccy) || (st.inst && st.inst.currency) || 'the currency') + ' as shown' + (fc && fc.executable === false ? ' (not executable now)' : '');
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
    if (!q || (q.last == null && q.mid == null && q.bid == null && q.ask == null && !q.error)) {
      /* USX-08: no print yet - it is being fetched (never a bare N/A) */
      ui.quoteLine.appendChild(T.h('span', { class: 'muted' }, 'fetching quote ...'));
      return;
    }
    ui.quoteLine.appendChild(T.px(q, { size: 'inline' }));
    if (q && (q.bid != null || q.ask != null)) { ui.quoteLine.appendChild(T.h('span', { class: 'muted' }, ' bid / ask ')); ui.quoteLine.appendChild(T.px(q, { field: 'bid', size: 'inline' })); ui.quoteLine.appendChild(T.h('span', { class: 'muted' }, ' / ')); ui.quoteLine.appendChild(T.px(q, { field: 'ask', size: 'inline' })); }
    if (q && q.executable === false) { ui.quoteLine.appendChild(T.h('span', { class: 'badge red' }, 'NOT EXECUTABLE' + (q.reason ? ' ' + q.reason : ''))); }
  }
  function paintStatus(msg, cls) { ui.status.textContent = msg || ''; ui.status.className = 'status ' + (cls || ''); }
  /* USX-36: opening the panel loads the candidates whenever the order names an instrument, a side and a quantity (a missing limit
     price does not hold them back); later changes reload them by themselves (hedge.js autoLoad) */
  function toggleHedge() {
    if (st.hedge_pos) { return; }
    st.hedgeOpen = !st.hedgeOpen; paintFields();
    if (st.hedgeOpen && hedge && st.instrument_id && st.side && st.qty > 0) {
      var hs = hedge.state();
      if (!hs.cands && (hs.status === 'idle' || hs.status === 'error')) { if (st.preview && st.preview.hedges && st.preview.hedges.status === 'ready') { hedge.seed(st.preview.hedges); } else { hedge.load(); } }
    } else if (st.hedgeOpen && hedge) { hedge.update({}); }
  }
  function onHedgePick(h, cand) { st.hedge = h; st.hedge_cand = cand || null; paintFields(); watchIds(); fieldChanged(); }

  /* ---------------------------------------------------------------- preview --------------------------------------- */
  function validity() {
    if (st.hedge_pos) { return 'hedge mode: pick a hedge of the position below'; }
    if (!st.instrument_id) { return 'enter an instrument'; }
    if (st.side_err) { return st.side_err; }
    if (!st.side || sidesFor().indexOf(st.side) < 0) { return 'choose the side (' + sidesFor().map(function (s) { return SIDE_LABEL[s] || s; }).join(' / ') + ')'; }
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
    if (st.fx_convert) { b.fx_convert = true; }
    return b;
  }
  function fieldChanged() {
    st.dirty = true; st.submit_err = null; st.stale = null; st.preview_err = null;
    st.pseq++; st.previewing = false;              /* an answer to a request sent before this change is for other fields: ignored */
    clearTimeout(st.timer); clearTimeout(st.reprev);
    if (hedge) { hedge.update({ side: st.side, qty: st.qty }); }
    stateTicket();
    paintSubmit();
    var why = validity();
    if (why) {                                     /* an incomplete order can never be re-previewed: the old preview no longer matches the fields */
      if (st.preview) { st.preview = null; st.preview_body = null; paintPreview(); tick(); }
      paintStatus(why, st.side_err || !st.side ? 'err' : ''); return;
    }
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
      if (body.instrument_id !== st.instrument_id) { return; }    /* defence in depth: never show (or seed hedges from) another instrument's preview */
      if (pv && pv.pending) { st.preview = null; paintStatus('preview pending (202): the server is still fetching the quote', 'err'); scheduleReprev(pv.retry_ms || 1500); return; }
      st.preview = pv; st.preview_body = json; st.dirty = false; st.preview_err = null;
      paintPreview(); paintSubmit(); paintQuote(); paintFx();
      if (hedge && pv.hedges && !st.hedge_pos) { hedge.seed(pv.hedges); }
      paintStatus(pv.ok && !(pv.blockers || []).length ? 'preview ok . ' + (pv.execution && pv.execution.queued ? 'queues: waiting for funds (executes automatically when cash frees up)' : (pv.execution && pv.execution.fill_now ? 'fills now' : 'rests / no fill now')) : 'preview: ' + (pv.blockers || []).map(function (b) { return b.code; }).join(', '), pv.ok && !(pv.blockers || []).length ? 'ok' : 'err');
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
      P.render(box, st.preview, { quote: T.quote(st.instrument_id), hedgeLegs: hedgeLegsEl(), extraConventions: extra, request: previewBody() });
    } else if (!st.submit_err && !st.preview_err && !st.hedge_pos) { ui.preview.appendChild(T.h('div', { class: 'empty' }, st.instrument_id ? (st.previewing ? 'previewing ...' : 'no preview yet' + kp('ticket.preview')) : 'enter an instrument to preview')); }
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
    var sk = kl('ticket.submit');
    var why = st.submitting ? 'submitting ...' : (st.dirty ? 'fields changed: waiting for the fresh preview' : (!st.preview ? 'no preview' : (st.stale ? 'review the diff' : (ok ? (sk || 'ready') : 'blocked or expired'))));
    ui.submitBtn.title = 'submit' + kp('ticket.submit') + ' - ' + why;
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
      if (kind === 'package' && st.hedge_pos) { paintStatus('hedge package ' + ((res.package && res.package.package_id) || '') + ' ' + ((res.package && res.package.status) || '') + ': only the hedge legs were ordered', 'ok'); }
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
        var sweep = d.sweep_in != null ? T.fmt.money(d.sweep_in) : (typeof d.sweep_in_c === 'number' && d.sweep_in_c > 0 ? T.fmt.cents(d.sweep_in_c) : null);
        var rules = Array.isArray(d.rules) ? d.rules.filter(function (x) { return x && x.text; }) : [];
        var txt = T.h('div', null,
          T.h('div', null, 'A large order: ' + (rules.length ? rules.map(function (x) { return x.text; }).join('; ') : (d.reason || e.message || 'confirmation required')) + '.'),
          sweep ? T.h('div', { class: 'hint' }, 'It sweeps ' + sweep + ' from Treasury' + (d.threshold ? ' (the confirm threshold is ' + T.fmt.money(d.threshold) + ')' : '') + '.') : null,
          d.notional ? T.h('div', { class: 'hint' }, 'notional ' + T.fmt.money(d.notional) + (d.nav ? ' . lifetime NAV ' + T.fmt.money(d.nav) : '')) : null,
          T.h('div', { class: 'hint' }, 'Confirm sends the same order again with your confirmation; nothing was written yet.'));
        paintStatus('needs your confirmation: ' + (d.reason || 'a large order'), 'err'); paintSubmit();
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
  /* Every key here is an action of the registry (keys.js; docs/KEYBINDS.md 'The contract for the page-level keys'): the owner's
     keys from Settings > Keyboard reach the ticket.  SIDE_KEYS / TYPE_KEYS keep comparing the 1.0.3 letters through
     T.keys.legacy(id).  A single-character key never acts while the cursor is in a box; Enter / Ctrl+Enter / Esc do (the
     box actions); Enter on a focused button presses that button. */
  var QTY_STEP = { 'ticket.qty_up': 1, 'ticket.qty_up_10': 10, 'ticket.qty_down': -1, 'ticket.qty_down_10': -10 };
  function onKey(e) {
    var K = T.keys;
    if (K.is(e, 'ticket.submit')) { e.preventDefault(); e.stopPropagation(); submit(); return; }
    if (K.is(e, 'ticket.clear')) { e.preventDefault(); e.stopPropagation(); if (!ui.dd.hidden) { ui.dd.hidden = true; return; } clear(); return; }
    if (K.is(e, 'ticket.preview')) { e.preventDefault(); e.stopPropagation(); if (e.target === ui.inst) { resolveTyped(); } else { runPreview(true); } return; }
    var id = K.action(e, 'ticket'); if (!id) { return; }                 /* a single-character key never matches while typing */
    if (st.hedge_pos && !/^ticket\.hedge_([1-5]|none)$/.test(id)) { return; }   /* 'hedge this position': only the candidate keys */
    var k = K.legacy(id);                                                /* the letter SIDE_KEYS / TYPE_KEYS compare */
    var group = kindGroup(instKind()); var sk = SIDE_KEYS[group] || SIDE_KEYS.CASH;
    var step = QTY_STEP[id];
    if (step) { stepQty(e.location === 3 ? (e.shiftKey ? 10 : 1) * (step > 0 ? 1 : -1) : step); }   /* the numpad + / - step one lot (Shift: ten), as in 1.0.3 */
    else if (k && sk[k] && sidesFor().indexOf(sk[k]) >= 0) { st.side = sk[k]; st.side_req = null; st.close_req = null; st.side_err = null; paintFields(); fieldChanged(); }
    else if (k && TYPE_KEYS[k]) { st.order_type = TYPE_KEYS[k]; paintFields(); fieldChanged(); }
    else if (id === 'ticket.hedge') { toggleHedge(); }
    else if (/^ticket\.hedge_[1-5]$/.test(id)) { if (hedge) { if (!st.hedgeOpen) { toggleHedge(); } hedge.pick(Number(k)); } }
    else if (id === 'ticket.hedge_none') { if (hedge) { hedge.none(); } }
    else { return; }
    e.preventDefault(); e.stopPropagation();
  }

  T.components.ticket = { open: open, mount: function (el, opts) { return open(el, opts); }, state: function () { return st; }, handle: handle, submit: function () { submit(); }, preview: function () { runPreview(true); }, clear: clear, SIDES: SIDES, TYPES: TYPES };
  if (T.keys && typeof T.keys.uses === 'function') { T.keys.uses('components/ticket.js'); }     /* its keys follow Settings > Keyboard from now on */
})();
