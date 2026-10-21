/* Header (TERMINAL_DESIGN.md s17.2, s17.7; WP-12): NY clock, session chips, the active-account card, the PAPER tag
 * (static in index.html), the integrity dot, the server dot and the full-width banner (offline mode = "FAKE DATA - not
 * market prices"; server unreachable; every provider down = "offline since hh:mm, last valid marks shown").
 * The card shows what the user must always see (handoff s10): account name, NAV, buying power (USD, with the
 * "incl. Treasury" sub-line and the formula on hover), settled / pending cash, positions, working orders, pending
 * settlements, borrows & collateral at the lender, loans, realised / unrealised P&L today.  Every figure is a field of
 * GET /api/accounts/{id}; with no active account the card shows the selected book (GET /api/books).
 * Available USD is the account's own spendable cash floored at zero; the reservation of working orders and what the
 * Treasury sweep covers beyond own cash sit under it (T.fmt.liquidity; accounting.AVAILABLE_TEXT on hover).
 * Buying power is what a NEW order can draw: available + the Treasury available (and PB headroom) left after what this
 * account's working orders already need beyond its own cash; the sub-line names that commitment (T.fmt.buyingPower;
 * accounting.BUYING_POWER_TEXT on hover) -- the same figure the ticket's 409 INSUFFICIENT_FUNDS check uses.
 * The clock renders the browser's time the moment this file loads (no '--:--:--' first paint); the server time replaces
 * it as soon as a poll syncs T.clock (2026-10-21 fix).
 */
(function () {
  'use strict';
  var T = window.T;
  var GROUP_LABEL = { US_EQUITY: 'US EQ', US_EQUITY_OPTIONS: 'EQ OPT', US_INDEX_OPTIONS: 'IDX OPT', US_BONDS: 'BONDS', CME_EQUITY_INDEX: 'CME', CME_RATES: 'CME RATES', NYMEX_ENERGY: 'NYMEX', COMEX_METALS: 'COMEX', CBOT_GRAINS: 'CBOT', CME_FX: 'CME FX', FX_SPOT: 'FX' };
  var CHIP_GROUPS = ['US_EQUITY', 'US_INDEX_OPTIONS', 'CME_EQUITY_INDEX', 'FX_SPOT', 'US_BONDS'];
  var clockTimer = null;
  function tickClock() {
    var el = T.q('#clock'); if (!el) { return; }
    var now = T.clock.now();
    var browser = !isFinite(now) || now <= 0;
    if (browser) { now = Date.now() / 1000; }                 /* no usable server offset: the browser clock, never '--:--:--' */
    el.innerHTML = T.esc(T.fmt.time(now)) + ' <small>ET ' + T.esc(T.fmt.dow(now)) + ' ' + T.esc(T.fmt.date(now)) + (!browser && T.clock.offset && Math.abs(T.clock.offset) > 5 ? ' (server clock)' : '') + '</small>';
  }
  function startClock() { tickClock(); if (!clockTimer) { clockTimer = setInterval(tickClock, 1000); } }
  function chipText(g, s) {
    var label = GROUP_LABEL[g] || g;
    var phase = s.phase || (s.open ? 'OPEN' : 'CLOSED');
    var detail = '';
    if (phase === 'OPEN' && s.closes_at) { detail = 'closes ' + T.fmt.hm(s.closes_at); }
    else if (phase === 'BREAK' && s.next_open) { detail = 'reopens ' + T.fmt.hm(s.next_open); }
    else if (s.next_open) { detail = 'opens ' + T.fmt.dow(s.next_open) + ' ' + T.fmt.hm(s.next_open); }
    return { label: label, phase: phase, text: label + ' . ' + phase + (detail ? ' . ' + detail : '') + (s.early_close ? ' (early)' : '') };
  }
  function renderChips() {
    var el = T.q('#session-chips'); if (!el) { return; }
    var src = T.state.sessions; var sessions = src && src.sessions ? src.sessions : src;
    T.clear(el);
    if (!sessions || typeof sessions !== 'object') { el.appendChild(T.h('span', { class: 'chip' }, 'sessions ?')); return; }
    CHIP_GROUPS.forEach(function (g) {
      var s = sessions[g]; if (!s) { return; }
      var c = chipText(g, s);
      el.appendChild(T.h('span', { class: 'chip ' + c.phase + ' g-' + g, title: g + (s.note ? ' . ' + s.note : '') + (s.trade_date ? ' . trade date ' + s.trade_date : '') }, c.text));
    });
  }
  function renderIntegrity() {
    var dot = T.q('#integrity-dot'); var h = T.state.health; if (!dot) { return; }
    if (!h || !h.integrity) { dot.className = 'idot unknown'; dot.title = 'integrity: not checked yet'; return; }
    var i = h.integrity;
    if (i.ok && !i.hold) { dot.className = 'idot'; dot.title = 'integrity ok' + (i.checked_at ? ' . checked ' + T.fmt.time(i.checked_at) : ''); }
    else { dot.className = 'idot bad'; dot.title = (i.hold ? 'INTEGRITY_HOLD: ' : 'integrity failed: ') + ((i.problems || []).map(function (p) { return p.check || p.code || p; }).join(', ') || 'see the attention strip'); }
    var sd = T.q('#server-dot');
    if (sd) {
      var ok = T.state.server_ok !== false;
      sd.className = 'chip ' + (ok ? 'OPEN' : 'err');
      sd.textContent = ok ? ('v' + (h.version || '?') + ' pid ' + (h.pid || '?')) : 'server unreachable';
      sd.title = ok ? ('finsim-terminal ' + (h.version || '') + ' . schema ' + (h.schema || '') + ' . uptime ' + T.fmt.age(h.uptime_s) + ' . db ' + (h.db_path || '')) : 'no answer from the server since ' + T.fmt.time(T.state.server_down_since);
    }
  }
  function renderBanner() {
    var b = T.q('#banner'); if (!b) { return; }
    var h = T.state.health || {};
    var msg = null, cls = '';
    if (h.offline) { msg = 'FAKE DATA - not market prices (serve --offline: a FakeProvider replaces every market-data source)'; cls = 'offline'; }
    else if (T.state.server_ok === false) { msg = 'Server unreachable since ' + T.fmt.time(T.state.server_down_since) + ' ET - nothing on this page updates; figures are the last ones received'; cls = 'offline'; }
    else if (h.providers) {
      var ps = Object.keys(h.providers); var down = ps.filter(function (p) { return h.providers[p] && h.providers[p].ok === false; });
      if (ps.length && down.length === ps.length) {
        var since = Math.min.apply(null, down.map(function (p) { return h.providers[p].last_err_ts || h.providers[p].last_ok_ts || T.clock.nowInt(); }));
        msg = 'offline since ' + T.fmt.hm(since) + ' ET - every market-data provider is unreachable; last valid marks shown; executions paused'; cls = 'warn';
      }
    }
    b.hidden = !msg; b.className = cls; b.textContent = msg || '';
  }
  function card() {
    var el = T.q('#acct-card'); if (!el) { return; }
    T.clear(el);
    var d = T.state.account_detail; var id = T.state.active_account;
    function cell(k, v, title, cls) { return T.h('span', { class: 'ac', title: title || '' }, T.h('b', { class: cls || '' }, v), T.h('i', null, k)); }
    if (!id || !d || (d.account && d.account.account_id !== id)) {
      var book = T.book.current();
      if (!book) { el.appendChild(T.h('span', { class: 'muted' }, 'no book loaded')); return; }
      var nav = book.nav && typeof book.nav === 'object' ? T.money(book.nav, 'value') : T.money(book, 'nav');
      el.appendChild(T.h('span', { class: 'ac-name' }, book.name || book.book_code));
      el.appendChild(cell('book NAV', nav, 'NAV of the open lifetime (server figure)'));
      if (book.lifetime) { el.appendChild(cell('lifetime', book.lifetime.lifetime_id, 'started ' + (book.lifetime.started || ''))); }
      el.appendChild(cell('account', id ? id + ' (loading)' : 'none', 'Alt+1..9 picks an account'));
      return;
    }
    var a = d.account || {};
    var usdCash = (d.cash && d.cash.USD) || null;
    var bp = (d.buying_power && d.buying_power.USD) || null;
    var pnl = (d.pnl && d.pnl.TODAY) || null;
    var comps = pnl && pnl.components || {};
    el.appendChild(T.h('span', { class: 'ac-name', title: a.account_id + (a.broker ? ' . ' + a.broker : '') }, a.name || a.account_id));
    var navTitle = 'equity (NAV) at the current marks' + (d.nav && d.nav.quality ? ' . quality ' + d.nav.quality : '') + (d.marks_partial ? ' . NAV excludes unpriced positions' : '');
    el.appendChild(cell('NAV', (d.nav && typeof d.nav === 'object') ? T.money(d.nav, 'nav') === 'n/a' ? T.money(d.nav, 'value') : T.money(d.nav, 'nav') : T.money(d, 'nav'), navTitle, d.marks_partial ? 'neg' : ''));
    if (bp) {
      var bpf = T.fmt.buyingPower(bp);
      var bpEl = cell('buying power USD', bpf.total, bpf.title);
      bpEl.appendChild(T.h('small', null, bpf.sub));
      el.appendChild(bpEl);
    }
    if (usdCash) {
      var liq = T.fmt.liquidity(usdCash, '');
      var avEl = cell('available USD', liq.available, liq.title);
      if (liq.extra) { avEl.appendChild(T.h('small', { style: liq.warn ? 'color: var(--down)' : null }, liq.extra)); }   /* inline: the card's small/b colors outrank .neg */
      el.appendChild(avEl);
      el.appendChild(cell('settled / pending', T.money(usdCash, 'settled') + ' / ' + T.money(usdCash, 'pending'), 'USD settled cash / pending (receivables - payables); available ' + liq.available + (liq.extra ? ' . ' + liq.extra : '')));
    }
    el.appendChild(cell('positions', String(d.positions_count != null ? d.positions_count : (T.state.positions || []).length), 'open positions'));
    el.appendChild(cell('working', String(d.working_orders != null ? d.working_orders : '?'), 'working orders'));
    var ps = d.pending_settlements; var psN = Array.isArray(ps) ? ps.length : (ps == null ? '?' : ps);
    el.appendChild(cell('settling', String(psN), 'pending settlements'));
    var coll = d.collateral || {};
    var borrows = Array.isArray(d.borrows) ? d.borrows.length : (d.borrows == null ? 0 : d.borrows);
    el.appendChild(cell('borrows / collateral', borrows + ' / ' + T.money(coll, 'held_at_lender'), 'open borrows / cash collateral held at the lender (102 %, not spendable)'));
    var loans = Array.isArray(d.loans) ? d.loans.length : (d.loans == null ? 0 : d.loans);
    el.appendChild(cell('loans', String(loans), 'open currency loans and PB financing'));
    if (pnl) {
      var r = T.money(comps, 'realised'), u = T.money(comps, 'unrealised');
      var today = T.money(pnl, 'pnl');
      el.appendChild(cell('P&L today', (r !== 'n/a' || u !== 'n/a') ? r + ' / ' + u : today, 'realised / unrealised today (' + (pnl.baseline && pnl.baseline.note ? pnl.baseline.note : 'vs the last 17:45 ET snapshot') + '); total ' + today + (pnl.degraded ? ' . baseline: inception (no snapshot yet)' : ''), T.sign(pnl, 'pnl')));
    }
    if (d.attention && (Array.isArray(d.attention) ? d.attention.length : d.attention)) { el.appendChild(cell('attention', String(Array.isArray(d.attention) ? d.attention.length : d.attention), 'open attention items for this account', 'neg')); }
  }
  function init() {
    startClock();
    T.state.on('sessions', renderChips);
    T.state.on('health', function () { renderIntegrity(); renderBanner(); });
    T.state.on('server_ok', function () { renderIntegrity(); renderBanner(); });
    ['account_detail', 'active_account', 'books', 'book', 'route', 'positions'].forEach(function (k) { T.state.on(k, card); });
    renderChips(); renderIntegrity(); renderBanner(); card();
  }
  T.components.header = { init: init, card: card, chipText: chipText, tickClock: tickClock };
  startClock();                                                /* first paint: the browser clock at once, before the boot's /api/state */
})();
