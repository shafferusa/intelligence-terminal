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
 * P&L today (BM-15 / USX-11) is the account's TOTAL P&L today - fees, financing and income included, the figure Portfolio
 * shows - with realised / unrealised / fees (and income / financing / FX when not zero) on hover.
 * Server down (SHELL-26): the server chip turns red, the integrity dot greys ('unknown - server unreachable': the check
 * cannot be made) and the banner says 'Server unreachable since ...' - next to the FAKE DATA line in offline mode, never
 * hidden by it.  The server chip reads 'server ok'; version, pid, uptime and the database are its hover text (SHELL-15).
 * With no account picked (or one that a reset closed - BM-16) the card is the selected book's: name, NAV, lifetime and
 * 'pick an account (Alt+1..9)'; an account whose figures cannot be read says why (APIUI-10).
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
    var dot = T.q('#integrity-dot'); var h = T.state.health;
    var down = T.state.server_ok === false;
    if (dot) {
      if (down) { dot.className = 'idot unknown'; dot.title = 'integrity: unknown - server unreachable (the last check was ' + (h && h.integrity && h.integrity.ok ? 'ok' : 'not ok or not made') + ')'; }
      else if (!h || !h.integrity || h.integrity.ok == null) { dot.className = 'idot unknown'; dot.title = 'integrity: not checked yet'; }
      else {
        var i = h.integrity;
        if (i.ok && !i.hold) { dot.className = 'idot'; dot.title = 'integrity ok' + (i.checked_at ? ' . checked ' + T.fmt.time(i.checked_at) : ''); }
        else { dot.className = 'idot bad'; dot.title = (i.hold ? 'INTEGRITY_HOLD: ' : 'integrity failed: ') + ((i.problems || []).map(function (p) { return p.check || p.code || p; }).join(', ') || 'see the attention strip'); }
      }
    }
    var sd = T.q('#server-dot');
    if (sd) {
      h = h || {};
      sd.className = 'chip ' + (down ? 'err' : (T.state.server_ok ? 'OPEN' : ''));
      sd.textContent = down ? 'server unreachable' : (T.state.server_ok ? 'server ok' : 'server ?');
      sd.title = down ? ('no answer from the server since ' + T.fmt.time(T.state.server_down_since) + ' ET')
        : ('FinSim terminal ' + (h.version ? 'v' + h.version : '') + (h.pid ? ' . pid ' + h.pid : '') + (h.schema ? ' . schema ' + h.schema : '') +
           (h.uptime_s != null ? ' . up ' + T.fmt.age(h.uptime_s) : '') + (h.db_path ? '\ndatabase ' + h.db_path : '') + (h.port ? '\nport ' + h.port : ''));
    }
  }
  function renderBanner() {
    var b = T.q('#banner'); if (!b) { return; }
    var h = T.state.health || {};
    var lines = [], cls = '';
    /* the server first: in offline mode the FAKE DATA line must never hide that nothing updates any more */
    if (T.state.server_ok === false) { lines.push('Server unreachable since ' + T.fmt.time(T.state.server_down_since) + ' ET - nothing on this page updates; figures are the last ones received'); cls = 'offline'; }
    if (h.offline) { lines.push('FAKE DATA - not market prices: this server runs offline and a stand-in provider replaces every market-data source'); cls = 'offline'; }
    else if (h.providers && T.state.server_ok !== false) {
      var ps = Object.keys(h.providers).filter(function (p) { return h.providers[p] && h.providers[p].ok != null; });
      var down = ps.filter(function (p) { return h.providers[p].ok === false; });
      if (ps.length && down.length === ps.length) {
        var since = Math.min.apply(null, down.map(function (p) { return h.providers[p].last_err_ts || h.providers[p].last_ok_ts || T.clock.nowInt(); }));
        lines.push('offline since ' + T.fmt.hm(since) + ' ET - every market-data provider is unreachable; last valid marks shown; executions paused'); cls = cls || 'warn';
      }
    }
    b.hidden = !lines.length; b.className = cls;
    T.clear(b);
    lines.forEach(function (l) { b.appendChild(T.h('div', null, l)); });
  }
  function card() {
    var el = T.q('#acct-card'); if (!el) { return; }
    T.clear(el);
    var d = T.state.account_detail; var id = T.state.active_account;
    function cell(k, v, title, cls) { return T.h('span', { class: 'ac', title: title || '' }, T.h('b', { class: cls || '' }, v), T.h('i', null, k)); }
    if (!id || !d || (d.account && d.account.account_id !== id)) {
      var book = T.book.current();
      if (!book) { el.appendChild(T.h('span', { class: 'muted' }, T.state.server_ok === false ? 'server unreachable' : 'loading books ...')); return; }
      var nav = book.nav && typeof book.nav === 'object' ? T.money(book.nav, 'value') : T.money(book, 'nav');
      el.appendChild(T.h('span', { class: 'ac-name', title: (book.profile_label || book.profile || '') + ' . ' + book.book_code }, book.name || book.book_code));
      el.appendChild(cell('book NAV', nav, 'NAV of the open lifetime (server figure)' + (book.nav && book.nav.quality ? ' . quality ' + book.nav.quality : '')));
      if (book.lifetime) { el.appendChild(cell('lifetime', book.lifetime.lifetime_id, 'started ' + (book.lifetime.started || ''))); }
      var err = id && T.state.account_error;
      if (err) { el.appendChild(cell('account', T.accountName ? T.accountName(id) : id, 'could not read ' + id + ': ' + T.api.describe(err), 'neg')); el.appendChild(T.h('span', { class: 'ac-err', title: T.api.describe(err) }, 'unavailable: ' + (err.code || 'error'))); return; }
      var usx = T.keys && T.keys.label ? T.keys.label('nav.us_exchange') : '';
      el.appendChild(cell('account', id ? (T.accountName ? T.accountName(id) : id) + ' ...' : 'pick an account' + (usx ? ' (' + usx + ' opens US Exchange)' : ''),
        id ? 'loading ' + id : 'pick an account of this book in the sidebar' + (usx ? ' (' + usx + ' opens US Exchange; the keys are in Settings > Keyboard)' : '') + '; the card then shows its NAV, buying power, cash and P&L'));
      return;
    }
    var a = d.account || {};
    var usdCash = (d.cash && d.cash.USD) || null;
    var bp = (d.buying_power && d.buying_power.USD) || null;
    var pnl = (d.pnl && d.pnl.TODAY) || null;
    var comps = pnl && pnl.components || {};
    el.appendChild(T.h('span', { class: 'ac-name', title: a.account_id + (a.broker ? ' . ' + a.broker : '') }, a.name || a.account_id));
    if (a.status && a.status !== 'OPEN') { el.appendChild(T.stateTag(a.status)); }
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
      /* the TOTAL P&L today (fees included): the figure Portfolio shows; its parts on hover */
      var today = T.money(pnl, 'pnl');
      var parts = ['realised ' + T.money(comps, 'realised'), 'unrealised ' + T.money(comps, 'unrealised'), 'fees ' + T.money(comps, 'fees')];
      [['income', 'income'], ['financing', 'financing'], ['fx_result', 'FX result']].forEach(function (k) { var v = T.num(comps, k[0]); if (v) { parts.push(k[1] + ' ' + T.money(comps, k[0])); } });
      var ptitle = pnl.error ? ('P&L today unavailable: ' + pnl.error) :
        ('P&L today ' + today + ' (fees included) = NAV now - NAV at the baseline - deposits / transfers (' + (pnl.baseline && pnl.baseline.note ? pnl.baseline.note : 'vs the last 17:45 ET snapshot') + ')' +
         '\n' + parts.join(' . ') + '\nrealised + income - financing - fees + unrealised + FX = explained' + (T.num(pnl, 'residual') ? ' (residual ' + T.money(pnl, 'residual') + ')' : '') +
         (pnl.degraded ? '\nbaseline: inception (no snapshot yet)' : ''));
      el.appendChild(cell('P&L today', T.fmt.signed(today), ptitle, T.sign(pnl, 'pnl')));
    }
    if (d.attention && (Array.isArray(d.attention) ? d.attention.length : d.attention)) { el.appendChild(cell('attention', String(Array.isArray(d.attention) ? d.attention.length : d.attention), 'open attention items for this account', 'neg')); }
  }
  function init() {
    startClock();
    T.state.on('sessions', renderChips);
    T.state.on('health', function () { renderIntegrity(); renderBanner(); });
    T.state.on('server_ok', function () { renderIntegrity(); renderBanner(); });
    ['account_detail', 'active_account', 'books', 'book', 'route', 'positions', 'keybinds'].forEach(function (k) { T.state.on(k, card); });
    renderChips(); renderIntegrity(); renderBanner(); card();
  }
  T.components.header = { init: init, card: card, chipText: chipText, tickClock: tickClock };
  startClock();                                                /* first paint: the browser clock at once, before the boot's /api/state */
})();
