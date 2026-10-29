/* FinSim Paper Terminal - core.js (TERMINAL_DESIGN.md s17.4; WP-12).
 *
 * One global, window.T, that every component and page extends:
 *   T.api     fetch wrapper: JSON in/out, `book=` on every GET, a request_id per click, the same id retried up to 3 times
 *             on 503 / network error, X-Replayed aware ("already submitted"), the LOST-RESPONSE rule for order submits
 *             (a network error after a submit re-GETs /api/accounts/{id}/orders?status=all before the caller may enable
 *             its button again, so a lost response never produces a duplicate order).
 *   T.state   store + subscribe (T.state.set / get / on).
 *   T.router  hash routes (s17.5).  T.poll named pollers (pause when document.hidden, immediate fetch on resume).
 *   T.fmt     numbers from decimal strings via Intl (money is never a float in the API; the UI only formats).
 *   T.keys    the keyboard: keys.js is the registry (every action, its F-key default, the owner's keys from Settings >
 *             Keyboard); the keys section below dispatches the global ones (docs/KEYBINDS.md).
 *   T.ui      toast / modal / confirm / help (the keyboard help shows the keys in force).  T.conv convention sentences
 *             (/api/state.conventions) shown as inline one-sentence notes (no glossary - owner amendment 18:28).
 *   T.book    the selected book (localStorage); every poller passes `book=`; Shift+F1 / F2 / F3 select a book by default.
 *   T.ticket  the dock slot: T.ticket.open dispatches on the instrument's kind to a registered T.ticketKinds handler
 *             (bond ticket, FX desk, private ticket, swap / OTC tickets), else components/ticket.js; an account page
 *             with an empty dock mounts an empty ticket with the hint "type a symbol or press b / s on a row".
 *   T.marketPlugins / T.ticketKinds  the plug-in registries of the product packages (BUILD2_PLAN C11; below).
 *   T.router  also draws the account tab bar (Markets / Positions / Orders / Cash & financing / Transactions) on every
 *             account page, switches the book for an account of another book, never opens a ticket on an account of a
 *             closed lifetime, corrects unknown routes / tabs, and passes an optional ?query on #/activity and #/risk.
 *             FINSIM1_PLAN C1: #/dashboard is the default route and the unknown-route fallback ("no page X - showing the
 *             Dashboard"); the removed news page's address #/news redirects there with a one-time toast; #/trading opens the
 *             last venue used (T.store last_venue), else the book's US Exchange Markets; #/pending?query, #/completed?query,
 *             #/accounts, #/cash?query, #/derivatives(/tab) and #/history(/tab) render T.pages.pending / completed /
 *             accounts / cash_collateral / derivatives and T.pages.history.hub - a NOT YET BUILT panel (and no API call)
 *             while that file is not in the build.
 *   T.hasModule(name)  C2: true when GET /api/health .modules[name] is "bound" (the health poll's last answer, fetched once
 *             before the first route renders); a page calls an optional module's routes only when it is true.
 *   1.0.6 (FINSIM1_PLAN P1b): the keys of the new navigation are on (keys.js: F8 Pending, Shift+F8 Completed, Shift+F4 Accounts,
 *             Shift+F5 Cash & Collateral, Shift+F6 Derivatives; Shift+F10 the History hub - the last past lifetime's pages while the
 *             hub is not in the build); #/settings/data (the old Data Status address) opens Settings > Market data; an error is
 *             said in the server's words without its internal code (V104R2-BW-04: T.api.describe; the code stays in the console).
 *
 * Conventions this file enforces on screen
 * - Nothing is computed here: every figure is a field of an API response; this file formats and routes.
 * - A missing optional component (another package's file) degrades to a NOT YET BUILT block, never a fake number.
 * - API errors surface (toast + the caller's own rendering); background poll failures mark the server dot red and
 *   paint the "server unreachable" banner; they are never swallowed silently.
 * - ASCII only; no frameworks, no CDN, no build step.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.VERSION = null;                                            /* the served code version: /api/health .version (set by the poller; ORC-9) */
  T.components = T.components || {};
  T.pages = T.pages || {};
  /* ---- plug-in registries (BUILD2_PLAN C11).  A product package registers from its own file, at load:
   *  T.marketPlugins[KIND] = { render: function (el, ctx) -> optional {unmount()}, tabs: optional, ticketKind: optional,
   *                            dockHint: optional }
   *    KIND = the account's market kind (T.accountKind): US_EXCHANGE, DEBT, CREDIT, FOREIGN_FX, PRIVATE or PB.  pages/markets.js
   *    hands the Markets body (under the page head and the account tab bar) to render(el, ctx) with
   *    ctx = {account_id, acct (the GET /api/accounts row or null), kind, role, tab (the route's tab or null), book, setTab(key),
   *    query}.  For PB only the tab keys listed in `tabs` (e.g. ['otc']) go to the plug-in; the other PB tabs stay the
   *    standard instruments grid.  For every other kind the plug-in owns all tabs (setTab(key) -> #/a/<id>/markets/<key>) and
   *    may list them in `tabs` ([key] or [{key, label}]) so an unknown tab in the address bar falls back to the first one.
   *    ticketKind names the T.ticketKinds handler of the account's empty dock ticket (e.g. 'BOND'); dockHint the hint shown
   *    in the dock when the account has no order ticket.  render may return {unmount()} (called when the page is left).
   *  T.ticketKinds[INSTRUMENT_KIND] = function (dockBody, fields) | { open: function (dockBody, fields) }
   *    T.ticket.open hands the dock to it when the order's instrument has that kind (fields.kind, else the remembered kind
   *    of the instrument, else one GET /api/instruments/{id}); every other kind gets components/ticket.js.  The handler owns
   *    dockBody (the shell clears it when the handler changes); fields = {account_id, instrument_id?, side?, qty?, ...}. */
  T.marketPlugins = T.marketPlugins || {};
  T.ticketKinds = T.ticketKinds || {};

  /* ---------------------------------------------------------------- basics ---------------------------------------- */
  T.esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  T.uuid = function () {
    if (window.crypto && window.crypto.randomUUID) { return window.crypto.randomUUID(); }
    var s = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx';
    return s.replace(/[xy]/g, function (c) { var r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 3 | 8)).toString(16); });
  };
  /* DOM builder: T.h('div', {class: 'x', onclick: fn, html: '<b>..</b>'}, 'text', el, [els]) */
  T.h = function (tag, attrs) {
    var el = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) { return; }
      if (k === 'class') { el.className = v; }
      else if (k === 'html') { el.innerHTML = v; }
      else if (k === 'text') { el.textContent = v; }
      else if (k === 'style') { el.setAttribute('style', v); }
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') { el.addEventListener(k.slice(2), v); }
      else if (k === 'dataset') { Object.keys(v).forEach(function (d) { el.dataset[d] = v[d]; }); }
      else if (v === true) { el.setAttribute(k, ''); }
      else { el.setAttribute(k, v); }
    });
    for (var i = 2; i < arguments.length; i++) { T.append(el, arguments[i]); }
    return el;
  };
  T.append = function (el, child) {
    if (child == null || child === false) { return; }
    if (Array.isArray(child)) { child.forEach(function (c) { T.append(el, c); }); return; }
    if (child instanceof Node) { el.appendChild(child); return; }
    el.appendChild(document.createTextNode(String(child)));
  };
  T.clear = function (el) { while (el && el.firstChild) { el.removeChild(el.firstChild); } return el; };
  T.q = function (sel, root) { return (root || document).querySelector(sel); };
  T.qa = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  /* ---------------------------------------------------------------- formatting ------------------------------------ */
  var NF = {};
  function nf(dp) { if (!NF[dp]) { NF[dp] = new Intl.NumberFormat('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp }); } return NF[dp]; }
  var NY_TIME = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  var NY_HM = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour12: false, hour: '2-digit', minute: '2-digit' });
  var NY_DATE = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' });
  var NY_DOW = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', weekday: 'short' });
  function fix24(s) { return s.replace(/^24:/, '00:'); }
  /* epoch seconds from a number or a numeric string; null for null, '', 0, NaN and every non-numeric string (an ISO date
     such as a curve's as_of '2026-10-20' is NEVER turned into a Date here - no time-zone guess, no RangeError; the
     formatters print it as given) */
  function epochOf(x) {
    if (x == null || x === '') { return null; }
    var n = typeof x === 'number' ? x : (/^\s*-?\d+(\.\d+)?\s*$/.test(String(x)) ? Number(x) : NaN);
    return isFinite(n) && n !== 0 ? n : null;
  }
  function given(x, dflt) { return typeof x === 'string' && x.trim() && epochOf(x) == null ? x.trim() : dflt; }
  T.epochOf = epochOf;
  function decimals(x) { var s = String(x).trim(); var i = s.indexOf('.'); return i < 0 ? 0 : s.length - i - 1; }
  T.fmt = {
    /* a decimal string ("1697525.00"), a number or null -> "1,697,525.00"; dp defaults to the string's own decimals (min 2, max 6) */
    money: function (x, dp) {
      if (x == null || x === '') { return 'n/a'; }
      var s = String(x).trim();
      var n = Number(s);
      if (!isFinite(n)) { return s; }
      if (dp == null) {
        var i = s.indexOf('.');
        var d = i < 0 ? 0 : s.length - i - 1;
        dp = Math.max(2, Math.min(6, d));
      }
      return nf(dp).format(n);
    },
    cents: function (c, dp) {                                  /* integer cents -> "1,234.56" (a display conversion only) */
      if (c == null || !isFinite(Number(c))) { return 'n/a'; }
      c = Number(c);
      var neg = c < 0; var a = Math.abs(c);
      var whole = Math.floor(a / 100); var frac = a % 100;
      var s = nf(0).format(whole) + '.' + (frac < 10 ? '0' : '') + frac;
      return (neg ? '-' : '') + s;
    },
    px: function (x) {                                         /* a price: its own decimals, at least 2 */
      if (x == null || x === '') { return 'n/a'; }
      var s = String(x); var i = s.indexOf('.');
      var d = i < 0 ? 0 : s.length - i - 1;
      return T.fmt.money(s, Math.max(2, Math.min(6, d)));
    },
    qty: function (n) {
      if (n == null || n === '') { return 'n/a'; }
      var v = Number(n); if (!isFinite(v)) { return String(n); }
      return nf(0).format(v);
    },
    num: function (n, dp) { if (n == null || n === '' || !isFinite(Number(n))) { return 'n/a'; } return nf(dp == null ? 2 : dp).format(Number(n)); },
    pct: function (x, dp) {                                    /* a fraction (0.172) or a string -> "17.20 %" */
      if (x == null || x === '' || !isFinite(Number(x))) { return 'n/a'; }
      return nf(dp == null ? 2 : dp).format(Number(x) * 100) + ' %';
    },
    /* RATE UNITS -- the one place the UI decides them (server side: finsim/terminal/quotes/rates.py, docstring UNITS).
     *   T.fmt.rate(x)    x is a FRACTION: every API field named rate, *_rate, spread, floor, all_in, segment_rate, coupon
     *                    (0.0337 -> "3.37 %"; 0.00125 -> "0.125 %").  Multiplies by 100 exactly once.
     *   T.fmt.ratePct(x) x is already a PERCENT, as published: /api/rates values (unit "percent"), /api/curve yields, an
     *                    observation echoed elsewhere (index_pct, *_pct, a loan's index.value, a fixing's sofr)
     *                    (3.87 -> "3.87 %").  Never scaled.
     * Passing a percent to T.fmt.rate is the "SOFR 387.00 %" defect of 2026-10-21; there is no unit guessing here. */
    rate: function (x) {
      if (x == null || x === '' || !isFinite(Number(x))) { return 'n/a'; }
      return nf(Math.max(2, Math.min(4, decimals(x) - 2))).format(Number(x) * 100) + ' %';
    },
    ratePct: function (x) {
      if (x == null || x === '' || !isFinite(Number(x))) { return 'n/a'; }
      return nf(Math.max(2, Math.min(4, decimals(x)))).format(Number(x)) + ' %';
    },
    /* The liquidity line of one account / currency (accounting.AVAILABLE_TEXT, 2026-10-21; TERMINAL_DESIGN s6.0 and
     * directive amendment -7): `available` is the account's own spendable cash FLOORED AT ZERO; what its working orders
     * and payables need beyond its own cash is shown apart as "reserved R (Treasury sweep covers N)" -- exactly that
     * moves from Treasury when the order fills or the payable settles -- with "UNFUNDED" (Treasury cannot cover it) and
     * "overdrawn" (settled cash below zero: an open funding shortfall) flagged.  src = an /api/accounts row (sfx
     * '_usd': available_usd, reserved_usd, needs_usd, sweep_covers_usd, unfunded_usd, overdrawn_usd) or a CashView
     * (sfx '': available, reserved, needs, sweep_covers, unfunded, overdrawn, definition).  Formats only: every figure
     * is the server's.  Returns {available, extra, warn, title}. */
    liquidity: function (src, sfx) {
      src = src || {}; sfx = sfx == null ? '' : sfx;
      function n(k) { var v = T.num(src, k + sfx); return v == null ? 0 : v; }
      function m(k) { return T.money(src, k + sfx); }
      var parts = [], warn = false;
      if (n('reserved') > 0 || n('needs') > 0) {
        var head = n('reserved') > 0 ? 'reserved ' + m('reserved') : 'payables beyond own cash ' + m('needs');
        if (n('sweep_covers') > 0) { head += ' (Treasury sweep covers ' + m('sweep_covers') + ')'; }
        parts.push(head);
      }
      if (n('unfunded') > 0) { parts.push('UNFUNDED ' + m('unfunded')); warn = true; }
      if (n('overdrawn') > 0) { parts.push('overdrawn ' + m('overdrawn')); warn = true; }
      var extra = parts.join(' . ');
      return { available: m('available'), extra: extra, warn: warn,
               title: (src.definition || T.fmt.AVAILABLE_DEF) + '\navailable ' + m('available') + (extra ? ' . ' + extra : '') };
    },
    AVAILABLE_DEF: 'available = max(0, settled cash + receivables due by the settlement date - payables - cash reserved by working orders): ' +
                   'the account\'s own spendable cash, never shown below zero. What working orders and payables need beyond it is shown apart as ' +
                   '"reserved (Treasury sweep covers N)": exactly that shortfall moves from Treasury when the order fills or the payable settles ' +
                   '(directive amendment -7); a part Treasury cannot cover is "unfunded"; settled cash below zero is "overdrawn" (an open funding ' +
                   'shortfall). Buying power = available + the Treasury available and PB headroom left after what this account\'s working orders ' +
                   'and payables already need beyond its own cash: max(0, own spendable + Treasury available + PB headroom); a new order beyond it ' +
                   'is never accepted as working (directive amendment -8): it queues, waiting for funds and reserving nothing, and executes ' +
                   'automatically when cash frees up.',
    /* The buying-power cell of one account / currency (accounting.BUYING_POWER_TEXT; 2026-10-21, second pass): `total` =
     * available + Treasury left + PB left, where what the account's working orders, payables and any overdraft already
     * need beyond its own cash (`committed`; their fills sweep it) has come off Treasury available first and PB headroom
     * second.  It is the figure the ticket's fundability uses (an order within it works, one beyond it queues SUSPENDED
     * "waiting for funds" - FUNDS_WAIT), so it never promises cash an earlier order will sweep.  bp = a BuyingPower row of
     * GET /api/accounts/{id} (`buying_power.USD`: available, committed, treasury_available, treasury_gross, pb_headroom,
     * pb_headroom_gross, total, formula); a row without `committed` (an older payload) shows no commitment line.
     * Formats only: every figure is the server's.  Returns {total, sub, title}. */
    buyingPower: function (bp) {
      bp = bp || {};
      function n(k) { var v = T.num(bp, k); return v == null ? 0 : v; }
      var tre = T.money(bp, 'treasury_available');
      var committed = n('committed') > 0;
      var lines = [bp.formula || T.fmt.BUYING_POWER_DEF,
                   'own available ' + T.money(bp, 'available') + ' + Treasury ' + tre + ' + PB headroom ' + T.money(bp, 'pb_headroom') + ' = ' + T.money(bp, 'total')];
      if (committed) {
        lines.push('committed ' + T.money(bp, 'committed') + ': what this account\'s working orders and payables need beyond its own cash, taken off ' +
                   'Treasury available ' + T.money(bp, 'treasury_gross') + (n('pb_headroom_gross') > 0 ? ' and then PB headroom ' + T.money(bp, 'pb_headroom_gross') : '') +
                   ' (their fills sweep it)');
      }
      return { total: T.money(bp, 'total'), sub: 'incl. Treasury ' + tre + (committed ? ' (after ' + T.money(bp, 'committed') + ' committed)' : ''),
               title: lines.join('\n') };
    },
    BUYING_POWER_DEF: 'buying power = max(0, own spendable cash + Treasury available + PB headroom) = available + Treasury left + PB left: what this ' +
                      'account\'s working orders, payables and any overdraft need beyond its own cash ("committed") comes off Treasury available first ' +
                      'and PB headroom second, exactly as their fills will sweep it; a new order whose shortfall exceeds Treasury left + PB left is ' +
                      'never accepted as working (directive amendment -8): it queues, waiting for funds and reserving nothing, and executes ' +
                      'automatically when cash frees up.',
    /* epoch seconds -> New York time; a date string is printed as given and anything else non-finite gives the blank form
       (UI sweep OA-04: a bond mark's inputs.curve.as_of '2026-10-20' threw RangeError 'Invalid time value' here) */
    time: function (epoch) { var s = epochOf(epoch); return s == null ? given(epoch, '--:--:--') : fix24(NY_TIME.format(new Date(s * 1000))); },
    hm: function (epoch) { var s = epochOf(epoch); return s == null ? given(epoch, '--:--') : fix24(NY_HM.format(new Date(s * 1000))); },
    date: function (epoch) { var s = epochOf(epoch); if (s == null) { var g = given(epoch, ''); return /^\d{4}-\d{2}-\d{2}/.test(g) ? g.slice(0, 10) : g; } return NY_DATE.format(new Date(s * 1000)); },
    dt: function (epoch) { var s = epochOf(epoch); return s == null ? given(epoch, '') : T.fmt.date(s) + ' ' + T.fmt.hm(s); },
    dow: function (epoch) { var s = epochOf(epoch); return s == null ? '' : NY_DOW.format(new Date(s * 1000)); },
    /* a compact amount for narrow places (the book switcher): 1,000,000.00 -> "1.00m", 1,000,000,000 -> "1.00bn" */
    compact: function (x) {
      var n = Number(String(x == null ? '' : x).replace(/,/g, ''));
      if (x == null || x === '' || !isFinite(n)) { return 'n/a'; }
      var a = Math.abs(n), sg = n < 0 ? '-' : '';
      if (a >= 9995e5) { return sg + nf(2).format(a / 1e9) + 'bn'; }          /* 999,997.75 reads 1.00m, not 1,000.0k */
      if (a >= 9995e2) { return sg + nf(2).format(a / 1e6) + 'm'; }
      if (a >= 1e4) { return sg + nf(1).format(a / 1e3) + 'k'; }
      return sg + nf(2).format(a);
    },
    age: function (s) {
      if (s == null || !isFinite(s)) { return '?'; }
      s = Math.max(0, Math.round(s));
      if (s < 60) { return s + 's'; }
      if (s < 3600) { return Math.floor(s / 60) + 'm'; }
      if (s < 86400) { return Math.floor(s / 3600) + 'h ' + (Math.floor(s / 60) % 60) + 'm'; }
      return Math.floor(s / 86400) + 'd';
    },
    signed: function (s) { if (s == null || s === 'n/a') { return s; } var n = Number(String(s).replace(/,/g, '')); return n > 0 ? '+' + s : s; }
  };
  /* A money field of an API row: `row[name]` (decimal string, the API shape) or `row[name + '_c']` (cents, the view shape). */
  T.money = function (row, name, dp) {
    if (!row) { return 'n/a'; }
    var c = row[name + '_c'];
    if (typeof c === 'number' && isFinite(c)) { return T.fmt.cents(c); }
    var s = row[name];
    if (s == null || s === '') { return 'n/a'; }
    if (typeof s === 'object') { return T.money(s, 'value', dp); }
    return T.fmt.money(s, dp);
  };
  T.num = function (row, name) {                               /* the numeric value of a money field, for sign classes only */
    if (!row) { return null; }
    var c = row[name + '_c'];
    if (typeof c === 'number' && isFinite(c)) { return c / 100; }
    var s = row[name];
    if (s == null || s === '') { return null; }
    if (typeof s === 'object') { s = s.value; }
    var n = Number(s); return isFinite(n) ? n : null;
  };
  T.sign = function (row, name) { var n = T.num(row, name); return n == null || n === 0 ? '' : (n > 0 ? 'pos' : 'neg'); };
  T.signCls = function (x) { var n = Number(String(x == null ? '' : x).replace(/,/g, '')); return !isFinite(n) || n === 0 ? '' : (n > 0 ? 'pos' : 'neg'); };
  /* P8L-02: the quantity of an OTC contract's position row (kind SWAP / class 'OTC derivatives'; never an FX-desk forward) in its
     own units with their label - a TRS its reference shares ('38,700 ORCL reference shares'), a CDX / IRS its notional ('USD
     notional') - from the contract fields the server sends (extra.units / extra.unit_label), signed like the row (negative = the
     short side: pay total return, buy protection, pay fixed).  Without those fields the row's own signed notional is shown with a
     notional label, so a notional is never read as a quantity.  Display only: {n, label, ccy, notional, isNotional}, or null for
     any other row. */
  T.contractQty = function (r) {
    if (!r) { return null; }
    var ex = r.extra || {}; var k = String(r.kind || '').toUpperCase();
    if (ex.desk_forward || !(k === 'SWAP' || r.klass === 'OTC derivatives')) { return null; }
    var q = Number(r.qty) || 0; var u = Number(ex.units);
    var useUnits = ex.units != null && ex.units !== '' && isFinite(u) && u !== 0 && !!ex.unit_label;
    var ccy = ex.qty_unit || r.currency || 'USD';
    var label = useUnits ? String(ex.unit_label) : ccy + ' notional';
    var notional = T.money(ex, 'notional');
    return { n: useUnits ? (q < 0 ? -Math.abs(u) : Math.abs(u)) : q, label: label, ccy: ccy,
             notional: notional === 'n/a' ? T.fmt.qty(Math.abs(q)) : notional.replace(/^-/, ''), isNotional: / notional$/.test(label) };
  };

  /* ---------------------------------------------------------------- clock ----------------------------------------- */
  T.clock = {
    offset: 0,                                                 /* server epoch - local epoch (seconds) */
    /* server time: epoch seconds (quotes' server_time, health clock.epoch) or the ISO string of clock.utc
       ("2026-10-21T13:00:00+00:00"); anything unparseable is ignored, so the offset is never NaN (the '--:--:--' clock) */
    sync: function (server) {
      var s = typeof server === 'number' ? server : (typeof server === 'string' && server ? (/^\d+(\.\d+)?$/.test(server) ? Number(server) : Date.parse(server) / 1000) : NaN);
      if (isFinite(s) && s > 0) { T.clock.offset = s - Date.now() / 1000; }
    },
    now: function () { return Date.now() / 1000 + T.clock.offset; },
    nowInt: function () { return Math.floor(T.clock.now()); }
  };

  /* ---------------------------------------------------------------- state ----------------------------------------- */
  var _subs = {};
  T.state = {
    clock: null, health: null, sessions: null, books: [], accounts: [], active_account: null, account_detail: null,
    positions: null, orders: null, quotes: {}, quote_ids: [], chain: null, chain_target: null, watchlists: null,
    selection: { instrument: null }, ticket: null, preview: null, settings: null, conventions: {}, attention: [], route: null,
    server_ok: null, server_down_since: null, state_version: null,
    set: function (key, value) { T.state[key] = value; T.state.emit(key, value); },
    get: function (key) { return T.state[key]; },
    on: function (key, fn) { (_subs[key] = _subs[key] || []).push(fn); return function () { T.state.off(key, fn); }; },
    off: function (key, fn) { var a = _subs[key] || []; var i = a.indexOf(fn); if (i >= 0) { a.splice(i, 1); } },
    emit: function (key, value) {
      (_subs[key] || []).slice().forEach(function (fn) {
        try { fn(value, key); } catch (e) { console.error('state listener failed for', key, e); }
      });
    }
  };

  /* ---------------------------------------------------------------- storage (per-viewer conveniences only) --------- */
  T.store = {
    get: function (k, d) { try { var v = localStorage.getItem('finsim.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set: function (k, v) { try { localStorage.setItem('finsim.' + k, JSON.stringify(v)); } catch (e) { /* private window etc. */ } }
  };

  /* ---------------------------------------------------------------- book ------------------------------------------ */
  T.book = {
    code: T.store.get('book', null),
    get: function () { return T.book.code; },
    current: function () {
      var code = T.book.code; var books = T.state.books || [];
      for (var i = 0; i < books.length; i++) { if (books[i].book_code === code) { return books[i]; } }
      return books[0] || null;
    },
    /* the switcher / the book keys: select the book; an account page goes to the Portfolio and a History page to Settings >
       Lifetimes (the other book's pages are not this account's / lifetime's); every other page re-renders for the book */
    set: function (code) {
      if (code === T.book.code) { return; }
      T.book.adopt(code);
      var cur = T.router.current;
      if (cur && cur.account) { T.router.go('#/portfolio'); return; }
      if (cur && cur.page === 'history' && !cur.hub) { T.router.go('#/settings/lifetimes'); return; }
      T.router.rerender();
    },
    /* select the book without leaving the page (the router, for an account of that book): remembered; the active account -
       of the other book - forgotten in the state AND in the remembered value (SHELL-02); every poller re-asked with book= */
    adopt: function (code) {
      if (!code || code === T.book.code) { return; }
      T.book.code = code; T.store.set('book', code);
      T.forgetAccount();
      T.state.emit('book', code);
      T.poll.kickAll();
    },
    cycle: function () {
      var books = T.state.books || []; if (!books.length) { return; }
      var i = -1;
      for (var k = 0; k < books.length; k++) { if (books[k].book_code === T.book.code) { i = k; } }
      T.book.set(books[(i + 1) % books.length].book_code);
    }
  };

  /* ---------------------------------------------------------------- api ------------------------------------------- */
  /* every failed call: status, code, message, details, body, headers, request_id and incident_id (a 500 carries it in
     error.details; it is copied onto body.incident_id too, where older callers look for it) */
  function ApiError(status, code, message, details, body, headers) {
    this.name = 'ApiError'; this.status = status; this.code = code || 'ERROR'; this.message = message || code || ('HTTP ' + status);
    this.details = details; this.body = body; this.headers = headers || {}; this.request_id = body && body.request_id;
    this.incident_id = (details && details.incident_id) || (body && body.incident_id) || null;
    if (this.incident_id && body && typeof body === 'object' && body.incident_id == null) { body.incident_id = this.incident_id; }
  }
  ApiError.prototype = Object.create(Error.prototype);
  T.ApiError = ApiError;
  function LostResponse(request_id, found, note) {
    this.name = 'LostResponse'; this.request_id = request_id; this.found = found || null;
    this.message = note || (found ? 'the submit reached the server (order ' + found.order_id + ')' : 'no answer from the server and no order recorded under this request id');
  }
  LostResponse.prototype = Object.create(Error.prototype);
  T.LostResponse = LostResponse;

  function withBook(path, nobook) {
    if (nobook || path.indexOf('/api/') !== 0 || !T.book.code) { return path; }
    if (/[?&]book=/.test(path)) { return path; }
    return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'book=' + encodeURIComponent(T.book.code);
  }
  function qs(params) {
    if (!params) { return ''; }
    var parts = [];
    Object.keys(params).forEach(function (k) { var v = params[k]; if (v == null || v === '') { return; } parts.push(encodeURIComponent(k) + '=' + encodeURIComponent(v)); });
    return parts.length ? '?' + parts.join('&') : '';
  }
  function parseBody(res) {
    return res.text().then(function (txt) {
      if (!txt) { return null; }
      try { return JSON.parse(txt); } catch (e) { return { raw: txt }; }
    });
  }
  function headersOf(res) { var h = {}; try { res.headers.forEach(function (v, k) { h[k.toLowerCase()] = v; }); } catch (e) { /* ignore */ } return h; }
  function markServer(ok) {
    var was = T.state.server_ok;
    T.state.server_ok = ok;
    if (!ok && was !== false) { T.state.server_down_since = T.clock.nowInt(); }
    if (ok) { T.state.server_down_since = null; }
    if (was !== ok) { T.state.emit('server_ok', ok); }
  }
  function request(method, path, body, opts) {
    opts = opts || {};
    var init = { method: method, headers: { 'Accept': 'application/json' }, cache: 'no-store' };
    if (body != null) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    if (opts.request_id) { init.headers['X-Request-Id'] = opts.request_id; }
    return fetch(withBook(path, opts.nobook), init).then(function (res) {
      markServer(true);
      var headers = headersOf(res);
      return parseBody(res).then(function (data) {
        if (res.status === 202) { return { status: 202, pending: true, retry_ms: (data && data.retry_ms) || 1500, body: data, headers: headers }; }
        if (res.ok) {
          if (data && typeof data === 'object' && !Array.isArray(data)) {
            if (headers['x-replayed'] === '1') { data.replayed = true; }
            data.$status = res.status;
          }
          return data;
        }
        var err = (data && data.error) || {};
        throw new ApiError(res.status, err.code || ('HTTP_' + res.status), err.message || res.statusText || ('HTTP ' + res.status), err.details, data, headers);
      });
    }, function (netErr) {
      markServer(false);
      throw new ApiError(0, 'NETWORK', 'server unreachable: ' + (netErr && netErr.message || netErr), null, null, {});
    });
  }
  T.api = {
    pending: {},                                               /* request_id -> {path, ts}: the pending-submit map (s17.4) */
    url: function (path, params) { return withBook(path + qs(params)); },
    get: function (path, params, opts) { return request('GET', path + qs(params), null, opts); },
    /* GET that waits for a 202 ("resolving" / "fetching") to turn into 200; up to `tries` polls */
    getReady: function (path, params, tries) {
      tries = tries == null ? 8 : tries;
      return T.api.get(path, params).then(function (r) {
        if (r && r.pending && tries > 0) {
          return new Promise(function (ok) { setTimeout(ok, r.retry_ms || 1500); }).then(function () { return T.api.getReady(path, params, tries - 1); });
        }
        if (r && r.pending) { throw new ApiError(202, 'PENDING', 'still fetching - try again'); }
        return r;
      });
    },
    /* Mutating call.  opts: {request_id, reconcile: account_id (orders re-GET on a lost response), retries}
     * - request_id: one per click (crypto.randomUUID), kept in T.api.pending until the call settles;
     * - 503 (Retry-After) and transient network errors retry the SAME id up to 3 times;
     * - a network error on an order submit with opts.reconcile re-GETs the account's orders (status=all) and either
     *   returns the order found under this request_id (recovered) or throws LostResponse(found=null) - the caller
     *   keeps its button disabled until this settles;
     * - X-Replayed: 1 -> result.replayed = true and the toast "already submitted". */
    mutate: function (method, path, body, opts) {
      opts = opts || {};
      var rid = opts.request_id || T.uuid();
      body = Object.assign({}, body || {}); body.request_id = rid;
      var retries = opts.retries == null ? 3 : opts.retries;
      T.api.pending[rid] = { path: path, ts: Date.now() };
      function done(r) { delete T.api.pending[rid]; return r; }
      function attempt(n) {
        return request(method, path, body, { request_id: rid, nobook: opts.nobook }).then(function (r) {
          if (r && r.replayed) { T.ui.toast('already submitted (request ' + rid.slice(0, 8) + ' replayed by the server)', 'ok'); }
          if (r && typeof r === 'object') { r.request_id = r.request_id || rid; }
          return done(r);
        }, function (e) {
          if (e.status === 503 && n < retries) {
            var wait = Number(e.headers['retry-after'] || 1) * 1000;
            return new Promise(function (ok) { setTimeout(ok, wait); }).then(function () { return attempt(n + 1); });
          }
          if (e.code === 'NETWORK') {
            if (opts.reconcile) {
              return T.api.get('/api/accounts/' + encodeURIComponent(opts.reconcile) + '/orders', { status: 'all' }).then(function (rows) {
                var list = Array.isArray(rows) ? rows : (rows && rows.orders) || [];
                var found = null;
                list.forEach(function (o) { if (o && o.request_id === rid) { found = o; } });
                delete T.api.pending[rid];
                if (found) { return { order: found, recovered: true, request_id: rid }; }
                throw new LostResponse(rid, null);
              }, function () {
                if (n < retries) { return new Promise(function (ok) { setTimeout(ok, 1000); }).then(function () { return attempt(n + 1); }); }
                delete T.api.pending[rid];
                throw new LostResponse(rid, null, 'server unreachable; the orders list could not be re-read - do not resubmit until it answers');
              });
            }
            if (n < retries) { return new Promise(function (ok) { setTimeout(ok, 1000); }).then(function () { return attempt(n + 1); }); }
          }
          delete T.api.pending[rid];
          throw e;
        });
      }
      return attempt(0);
    },
    post: function (path, body, opts) { return T.api.mutate('POST', path, body, opts); },
    patch: function (path, body, opts) { return T.api.mutate('PATCH', path, body, opts); },
    del: function (path, body, opts) { return T.api.mutate('DELETE', path, body, opts); },
    /* the standard error presentation: toast + console; returns the message for inline use */
    fail: function (e, where) {
      var msg = T.api.describe(e);
      console.error(where || 'api', e);
      T.ui.toast(msg, 'err', 6000);
      return msg;
    },
    /* one line for an error in trader words: the server's message, never its internal code (V104R2-BW-04; the code is in the
       console line T.api.fail writes); a code that came without a message is said in words (T.codeWords); a 500 keeps its incident
       id (what to quote when reporting it); a lost submit response says so */
    describe: function (e) {
      var msg;
      if (e && e.name === 'LostResponse') { msg = 'lost response: ' + e.message; }
      else if (e && e.code && (!e.message || e.message === e.code)) { msg = T.codeWords(e.code); }
      else { msg = String(e && e.message || e); }
      var inc = e && (e.incident_id || (e.details && e.details.incident_id) || (e.body && e.body.incident_id));
      if (inc) { msg += ' (incident ' + inc + ')'; }
      return msg;
    }
  };

  /* ---------------------------------------------------------------- conventions ----------------------------------- */
  T.conv = function (key) { var c = T.state.conventions || {}; return c[key] || ''; };
  /* V104R1-04: trader wording for an internal code ('OTC_IM' -> 'OTC initial margin', 'BUY_CCY' -> 'currency bought', 'SWEEP_IN' ->
     'sweep-in'); the same rule as api_views.code_words; anything that is not a code comes back as given */
  var CODE_ACRONYMS = { FX: 1, OTC: 1, CDX: 1, CDS: 1, JTD: 1, /* CDS-107 */ PB: 1, IM: 1, VM: 1, TRS: 1, IRS: 1, XCCY: 1, FIFO: 1, SOFR: 1, NAV: 1, US: 1, USD: 1, ECP: 1, QP: 1, ADV: 1, DV01: 1,
                        CS01: 1, ETF: 1, GC: 1, HTB: 1, CSA: 1, ISDA: 1, PBA: 1, SHO: 1, GTC: 1, AON: 1, MKT: 1, LMT: 1, EUR: 1, GBP: 1, JPY: 1, CHF: 1, CAD: 1,
                        AUD: 1, OCC: 1, CME: 1, PDT: 1, API: 1, HTTP: 1 };
  var CODE_WORDS = { ACCT_DAY: 'accounting day', FUT_MARGIN: 'futures margin', OPT_MARGIN: 'option margin', EXCESS_LIQ: 'excess liquidity', BUY_CCY: 'currency bought',
                     SELL_CCY: 'currency sold', PB_ONBOARDED: 'prime broker onboarded', OTC_BOOKED: 'OTC contract booked', FX_DEALT: 'FX deal',
                     FX_FORWARD_DEALT: 'FX forward dealt', SWEEP_IN: 'sweep-in', SWEEP_OUT: 'sweep-out', OTC_IM: 'OTC initial margin', OTC_VM: 'OTC variation margin',
                     OTC_TERMINATE: 'OTC termination' };
  T.codeWords = function (code) {
    var c = code == null ? '' : String(code);
    if (!/^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$/.test(c)) { return c; }
    if (CODE_WORDS[c]) { return CODE_WORDS[c]; }
    return c.split('_').map(function (w) { return CODE_ACRONYMS[w] ? w : w.toLowerCase(); }).join(' ');
  };
  /* a convention key as a title: 'SWAP_MARK' -> 'Swap mark', 'FX_SPOT' -> 'FX spot' (the key stays in the tooltip) */
  T.convTitle = function (key) { var w = T.codeWords(key); return w.charAt(0).toUpperCase() + w.slice(1); };
  /* an inline one-sentence note block for the given keys (only the ones the server knows), each headed by its title in words */
  T.convNote = function (keys) {
    var el = T.h('div', { class: 'conv' });
    (keys || []).forEach(function (k) { var s = T.conv(k); if (s) { el.appendChild(T.h('div', null, T.h('b', { title: k }, T.convTitle(k)), s)); } });
    return el.childNodes.length ? el : document.createComment('no conventions');
  };

  /* ---------------------------------------------------------------- page scaffolding helpers ---------------------- */
  T.pageHead = function (el, title, sub, actions) {
    var head = T.h('div', { class: 'page-head' }, T.h('h1', null, title), sub ? T.h('span', { class: 'sub' }, sub) : null, T.h('span', { class: 'spacer' }));
    (actions || []).forEach(function (a) { head.appendChild(a); });
    el.appendChild(head); return head;
  };
  /* tiles: [{k, v, s, cls, title, href}] */
  T.tiles = function (list) {
    var box = T.h('div', { class: 'tiles' });
    list.forEach(function (t) {
      var tile = T.h('div', { class: 'tile' + (t.href ? ' click' : ''), title: t.title || '' }, T.h('div', { class: 'k' }, t.k), T.h('div', { class: 'v ' + (t.cls || '') }, t.v), t.s != null ? T.h('div', { class: 's' }, t.s) : null);
      if (t.href) { tile.addEventListener('click', function () { T.router.go(t.href); }); }
      box.appendChild(tile);
    });
    return box;
  };
  /* key-value block: [[label, value | element, title?], ...] */
  T.kv = function (pairs) {
    var box = T.h('div', { class: 'kv' });
    pairs.forEach(function (p) { if (!p) { return; } box.appendChild(T.h('div', { title: p[2] || '' }, p[0])); box.appendChild(T.h('div', { title: p[2] || '' }, p[1] == null ? 'n/a' : p[1])); });
    return box;
  };
  /* C4: label | value pairs in columns (.kv-grid): [[label, value | element, title?], ...]; a null value shows n/a */
  T.kvGrid = function (pairs) {
    var box = T.h('div', { class: 'kv-grid' });
    (pairs || []).forEach(function (p) { if (!p) { return; } box.appendChild(T.h('div', { title: p[2] || '' }, T.h('span', null, p[0]), T.h('span', null, p[1] == null ? 'n/a' : p[1]))); });
    return box;
  };
  /* C4: an order / settlement stage chip: stage = working | partial | held | queued | filled | cancelled | expired | rejected |
     settling | failed (any case); label defaults to the stage in words */
  T.stageChip = function (stage, label, title) {
    var s = String(stage == null ? '' : stage).toLowerCase();
    return T.h('span', { class: 'stage-chip stage-' + s.replace(/[^a-z_]/g, ''), title: title || '' }, label || s.replace(/_/g, ' '));
  };
  T.tabs = function (list, active, onPick) {
    var box = T.h('div', { class: 'tabs', role: 'tablist' });
    list.forEach(function (t) {
      var b = T.h('button', { class: 'tab' + (t.key === active ? ' active' : '') + (t.nib ? ' nib' : ''), role: 'tab', title: t.title || '' }, t.label);
      b.addEventListener('click', function () { onPick(t.key, t); });
      box.appendChild(b);
    });
    return box;
  };
  T.field = function (label, input, title) { return [T.h('label', { title: title || '' }, label), input]; };
  T.form = function (rows) { var f = T.h('div', { class: 'form' }); rows.forEach(function (r) { T.append(f, r); }); return f; };
  T.errorBox = function (el, e, where) { var msg = T.api.fail(e, where); el.appendChild(T.h('div', { class: 'error' }, msg)); };

  /* ---------------------------------------------------------------- not built / not in this build ----------------- */
  T.notBuilt = function (el, label, detail) {
    T.clear(el).appendChild(T.h('div', { class: 'notbuilt' }, T.h('b', null, 'NOT YET BUILT'), ' - ', label || 'this component', detail ? T.h('div', { class: 'hint' }, detail) : null));
    return el;
  };
  T.nib = function (label, note) {
    return T.h('div', { class: 'notbuilt nib' }, T.h('b', null, 'NOT IN THIS BUILD'), ' - ', label, note ? T.h('div', { class: 'hint' }, note) : null);
  };
  T.stateTag = function (state) { return T.h('span', { class: 'state-tag ' + (state || '') }, (state || '').replace(/_/g, ' ')); };
  /* mount another package's component when its file answered; else the NOT YET BUILT block.  Returns the handle or null. */
  T.mountOptional = function (name, el, opts, label) {
    var c = T.components[name];
    if (c && typeof c.mount === 'function') {
      try { return c.mount(el, opts || {}); } catch (e) { console.error('component', name, 'failed', e); T.clear(el).appendChild(T.h('div', { class: 'error' }, name + ' failed: ' + (e.message || e))); return null; }
    }
    T.notBuilt(el, label || ('components/' + name + '.js'));
    return null;
  };

  /* ---------------------------------------------------------------- ui: toast / modal / confirm / help ------------- */
  var TOAST_MAX = 220, TOAST_KEEP = 4;
  /* the open dialogs, newest last: the 'close a dialog' key (Esc by default; keys section) closes the newest one */
  var _modals = [];
  function helpOpen() { var el = T.q('#help'); return !!(el && !el.hidden); }
  function paintHelp(el) {
    var box = T.h('div', { class: 'keys' }, T.h('h1', null, 'Keyboard', T.h('span', { class: 'spacer' }),
      T.h('button', { class: 'btn small', title: 'change the keys in Settings > Keyboard', onclick: function () { el.hidden = true; T.router.go('#/settings/keyboard'); } }, 'Change keys'),
      T.h('button', { class: 'btn small', title: 'close' + (T.keys.paren ? T.keys.paren('dialog.close') : ''), onclick: function () { el.hidden = true; } }, 'x')));
    T.help.renderKeys(box);
    T.clear(el).appendChild(box);
  }
  T.ui = {
    /* a toast is short: text beyond TOAST_MAX characters is cut (the whole message is the hover text) and at most
       TOAST_KEEP toasts are on screen at once (the oldest goes first); identical text replaces itself */
    toast: function (msg, kind, ms) {
      var root = T.q('#toast-root'); if (!root) { return; }
      var full = String(msg == null ? '' : msg);
      var text = full.length > TOAST_MAX ? full.slice(0, TOAST_MAX - 3).replace(/\s+\S*$/, '') + ' ...' : full;
      T.qa('.toast', root).forEach(function (t) { if (t.title === full) { root.removeChild(t); } });
      var el = T.h('div', { class: 'toast ' + (kind || ''), role: 'status', title: full }, text);
      root.appendChild(el);
      while (root.children.length > TOAST_KEEP) { root.removeChild(root.firstChild); }
      setTimeout(function () { if (el.parentNode) { el.parentNode.removeChild(el); } }, ms || 3500);
    },
    /* spec: {title, body (node | html | fn(body, handle)), actions [{label, cls, id, onClick(handle, button)}], wide, sticky
       (no close on a click outside), onDismiss (Esc / a click outside: the dialog's own "cancel"), onClose (always, once)} */
    modal: function (spec) {
      var root = T.q('#modal-root');
      var box = T.h('div', { class: 'modal' + (spec.wide ? ' wide' : ''), role: 'dialog', 'aria-label': spec.title || 'dialog' });
      var bg = T.h('div', { class: 'modal-bg' }, box);
      var handle = { el: box, bg: bg, closed: false,
        close: function () {
          if (handle.closed) { return; }
          handle.closed = true;
          if (bg.parentNode) { bg.parentNode.removeChild(bg); }
          var i = _modals.indexOf(handle); if (i >= 0) { _modals.splice(i, 1); }
          if (spec.onClose) { try { spec.onClose(handle); } catch (e) { console.error('dialog onClose failed', e); } }
        },
        /* the 'close a dialog' key (Esc) and a click outside: the dialog's cancel, then close */
        dismiss: function () {
          if (handle.closed) { return; }
          if (spec.onDismiss) { try { spec.onDismiss(handle); } catch (e) { console.error('dialog onDismiss failed', e); } }
          handle.close();
        } };
      box.appendChild(T.h('h1', null, spec.title || '', T.h('button', { class: 'btn small', onclick: function () { handle.dismiss(); }, title: 'close' + (T.keys.paren ? T.keys.paren('dialog.close') : '') }, 'x')));
      var body = T.h('div', { class: 'modal-body' });
      if (typeof spec.body === 'function') { spec.body(body, handle); } else if (spec.body instanceof Node) { body.appendChild(spec.body); } else if (spec.body != null) { body.innerHTML = spec.body; }
      box.appendChild(body);
      if (spec.actions && spec.actions.length) {
        var acts = T.h('div', { class: 'modal-actions' });
        spec.actions.forEach(function (a) {
          var b = T.h('button', { class: 'btn ' + (a.cls || ''), onclick: function () { if (a.onClick) { a.onClick(handle, b); } else { handle.close(); } } }, a.label);
          if (a.id) { b.id = a.id; }
          acts.appendChild(b);
        });
        box.appendChild(acts);
      }
      bg.addEventListener('mousedown', function (e) { if (e.target === bg && !spec.sticky) { handle.dismiss(); } });
      _modals.push(handle);
      root.appendChild(bg);
      var first = box.querySelector('input, select, textarea, button.primary');
      if (first) { try { first.focus(); } catch (e) { /* ignore */ } }
      return handle;
    },
    confirm: function (text, opts) {
      opts = opts || {};
      return new Promise(function (resolve) {
        var h = T.ui.modal({
          title: opts.title || 'Confirm', body: T.h('div', null, text), sticky: true,
          onDismiss: function () { resolve(false); },                /* Esc / x answer "no" (the promise never hangs) */
          actions: [{ label: opts.cancel || 'Cancel', onClick: function (m) { m.close(); resolve(false); } },
                    { label: opts.ok || 'Confirm', cls: 'primary', onClick: function (m) { m.close(); resolve(true); } }]
        });
        h.bg.addEventListener('mousedown', function (e) { if (e.target === h.bg) { h.close(); resolve(false); } });
      });
    },
    /* the keyboard help ('?' / Shift+F12 by default): the CURRENT keys from the registry (T.help.renderKeys) */
    help: function (toggle) {
      var el = T.q('#help'); if (!el) { return; }
      if (toggle === false || (toggle !== true && !el.hidden)) { el.hidden = true; return; }
      paintHelp(el);
      el.hidden = false;
      el.onclick = function (e) { if (e.target === el) { el.hidden = true; } };
    },
    /* the newest dialog (else the keyboard help) closes; false when none is open */
    closeTop: function () {
      if (_modals.length) { _modals[_modals.length - 1].dismiss(); return true; }
      if (helpOpen()) { T.ui.help(false); return true; }
      return false;
    },
    dialogs: function () { return _modals.length; }
  };

  /* ---------------------------------------------------------------- keyboard map (help + Settings) ---------------- */
  /* The CURRENT keys (keys.js registry: the F-key defaults + the owner's changes from Settings > Keyboard) in a compact
     two-column table per group (key | what it does), used by the '?' / Shift+F12 overlay and by Settings > General
     (T.help.renderKeys(el)).  Related actions share one row ('B / S / X / C  Side: buy / sell / ...'); an action without a
     key is left out of the overlay and counted in its last line.  Nothing here is a hard-coded key label. */
  function keyLabelHtml(label) {                               /* a canonical key ('Shift+F1') or a list 'B / S' as <kbd> */
    var parts = String(label == null ? '' : label).split(' / ');
    return parts.map(function (k) { return T.keys.valid && T.keys.valid(k) ? T.keys.kbd(k) : '<kbd>' + T.esc(k) + '</kbd>'; }).join('<span class="ksep"> / </span>');
  }
  /* [{group, where, rows: [[keysHtml, words]]}] and the number of actions without a key */
  function keyRows() {
    var K = T.keys, out = [], none = 0;
    if (!K.ACTIONS) { return { groups: out, none: 0, missing: true }; }
    K.groups().forEach(function (g) {
      var rows = [], seen = {};
      g.actions.forEach(function (a) {
        if (a.family) {
          if (seen[a.family]) { return; }
          seen[a.family] = 1;
          var fam = g.actions.filter(function (x) { return x.family === a.family; });
          var keys = fam.map(function (x) { return K.binding(x.id); });
          if (!keys.some(Boolean)) { none += fam.length; return; }
          none += keys.filter(function (k) { return !k; }).length;
          rows.push([keys.map(function (k) { return k ? K.kbd(k) : '<span class="kb-none">-</span>'; }).join('<span class="ksep"> / </span>'), K.FAMILY[a.family] || a.label]);
          return;
        }
        var k = K.binding(a.id);
        if (!k) { none++; return; }
        rows.push([K.kbd(k), a.label]);
      });
      if (rows.length) { out.push({ group: g.name, where: g.where, rows: rows }); }
    });
    return { groups: out, none: none };
  }
  T.help = {
    keyHtml: keyLabelHtml,
    rows: keyRows,
    renderKeys: function (el) {
      var box = T.h('div', { class: 'keymap' });
      var r = keyRows();
      if (r.missing) { box.appendChild(T.h('div', { class: 'error' }, 'the keyboard registry (keys.js) did not load: no key is bound')); }
      r.groups.forEach(function (g) {
        var t = T.h('table', { class: 'keys-table' });
        t.appendChild(T.h('caption', null, g.group, T.h('span', { class: 'kw' }, ' . ' + g.where)));
        g.rows.forEach(function (row) { t.appendChild(T.h('tr', null, T.h('td', { class: 'k', html: row[0] }), T.h('td', { class: 'd' }, row[1]))); });
        box.appendChild(t);
      });
      box.appendChild(T.h('div', { class: 'hint' }, 'F-keys and Ctrl / Alt keys also work while the cursor is in a box; single keys (letters, / and ?) never act while you type.' +
        (r.none ? ' ' + r.none + ' more action' + (r.none === 1 ? ' has' : 's have') + ' no key.' : '') + ' ',
        T.h('a', { href: '#/settings/keyboard', onclick: function () { T.ui.help(false); } }, 'Change keys: Settings > Keyboard')));
      if (el) { el.appendChild(box); }
      return box;
    }
  };

  /* ---------------------------------------------------------------- provider names -------------------------------- */
  /* trader-facing names of the provider-health keys (GET /api/health .providers, worker refreshers); the market-data sources only */
  T.PROVIDER_LABEL = { yahoo: 'Yahoo', cboe: 'Cboe', treasury: 'Treasury.gov', fred: 'FRED', finra: 'FINRA',
    frankfurter: 'Frankfurter (ECB reference rates)', fiscaldata: 'Treasury fiscal data',
    treasurydirect: 'TreasuryDirect', nyfed: 'New York Fed', ecb: 'ECB', mof: 'Japan Ministry of Finance', boe: 'Bank of England', sec: 'SEC EDGAR', edgar: 'SEC EDGAR',
    debt: 'Debt market data (Treasury list, FedInvest, ECB, MOF)', otc_options: 'OTC option inputs (Yahoo closes, Cboe chains)' };
  T.providerLabel = function (name) {
    var k = String(name == null ? '' : name);
    return T.PROVIDER_LABEL[k] || T.PROVIDER_LABEL[k.toLowerCase()] || k.replace(/_/g, ' ');
  };
  /* the attention strip's handle (components/attention.js replaces expand with the real one) */
  T.attention = T.attention || { expand: function () { var c = T.components.attention; if (c && c.expand) { c.expand(); } } };
  /* C2 (FINSIM1_PLAN): is the optional server module `name` bound in this build?  From the last /api/health answer (the boot
     reads it before the first route renders; the health poller keeps it fresh).  A page calls a route of an optional module
     only when this is true; otherwise its section says "unavailable" with the reason and makes no request. */
  T.hasModule = function (name) {
    var h = T.state.health; var m = h && h.modules;
    return !!(m && typeof m === 'object' && m[name] === 'bound');
  };
  /* false until a health answer with the module list has arrived (a page may wait for the 'health' state event) */
  T.modulesKnown = function () { var h = T.state.health; return !!(h && h.modules && typeof h.modules === 'object'); };

  /* ---------------------------------------------------------------- theme ----------------------------------------- */
  T.theme = {
    get: function () { return T.store.get('theme', 'dark'); },
    set: function (t) { T.store.set('theme', t); document.documentElement.setAttribute('data-theme', t); },
    apply: function () { document.documentElement.setAttribute('data-theme', T.theme.get()); }
  };

  /* ---------------------------------------------------------------- csv / copy ------------------------------------ */
  T.csv = function (filename, columns, rows) {
    function cell(v) { v = v == null ? '' : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
    var lines = [columns.map(function (c) { return cell(c.label || c.key); }).join(',')];
    rows.forEach(function (r) { lines.push(columns.map(function (c) { return cell(c.csv ? c.csv(r) : (c.text ? c.text(r) : r[c.key])); }).join(',')); });
    T.download(filename, lines.join('\r\n'), 'text/csv');
  };
  T.download = function (filename, text, mime) {
    var blob = new Blob([text], { type: mime || 'text/plain' });
    var a = T.h('a', { href: URL.createObjectURL(blob), download: filename });
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  };
  T.copy = function (text) {
    if (navigator.clipboard && navigator.clipboard.writeText) { return navigator.clipboard.writeText(text).then(function () { T.ui.toast('copied', 'ok', 1200); }, function () { T.ui.toast('copy failed', 'err'); }); }
    T.ui.toast('clipboard unavailable', 'err'); return Promise.resolve();
  };

  /* ---------------------------------------------------------------- accounts: kind, owner, refusal ---------------- */
  function roleOfId(id) { var s = String(id || ''); var i = s.indexOf(':'); return i >= 0 ? s.slice(i + 1) : s; }
  /* the GET /api/accounts row of an account of the selected book (null when not loaded or not in this book) */
  T.accountRow = function (id) {
    var list = T.state.accounts || [];
    for (var i = 0; i < list.length; i++) { if (list[i] && list[i].account_id === id) { return list[i]; } }
    return null;
  };
  /* the account's market kind (books.account_kind_for): TREASURY, PB, else its role (US_EXCHANGE, DEBT, CREDIT, FOREIGN_FX,
     PRIVATE); takes a row or an id */
  T.accountKind = function (acct) {
    var row = acct && typeof acct === 'object' ? acct : T.accountRow(acct);
    var id = row ? row.account_id : acct;
    if (row && (row.kind === 'TREASURY' || row.kind === 'PB')) { return row.kind; }
    var role = (row && row.role) || roleOfId(id);
    return /^PB_/.test(role) ? 'PB' : (role || null);
  };
  var ROLE_NAMES = { US_EXCHANGE: 'US Exchange', DEBT: 'Debt Instruments', CREDIT: 'Credit Instruments', FOREIGN_FX: 'Foreign Markets & FX', PRIVATE: 'Private Markets', TREASURY: 'Treasury' };
  /* the account's name ("US Exchange", "Goldman Sachs (PB)"); from the id when its row is not loaded */
  T.accountName = function (id) {
    var row = T.accountRow(id); if (row && row.name) { return row.name; }
    var role = roleOfId(id);
    return ROLE_NAMES[role] || (/^PB_/.test(role) ? role.slice(3) + ' (PB)' : String(id || ''));
  };
  /* The book whose OPEN lifetime holds the account (GET /api/books rows carry `accounts`): that book row; null when no open
     lifetime holds it (an account of a lifetime closed by a reset, or an unknown id); undefined while the books are not loaded. */
  T.accountOwner = function (id) {
    var books = T.state.books || []; var known = false;
    if (!id) { return undefined; }
    for (var i = 0; i < books.length; i++) {
      var acc = books[i] && books[i].accounts;
      if (Array.isArray(acc)) { known = true; if (acc.indexOf(id) >= 0) { return books[i]; } }
    }
    return known ? null : undefined;
  };
  function closedRow(id) { var row = T.accountRow(id); return !!(row && row.status && row.status !== 'OPEN'); }
  /* why no order may be opened in this account (null = it may): none picked, an account of a closed lifetime, a CLOSED account */
  T.accountRefusal = function (id) {
    if (!id) { var usx = T.keys.label ? T.keys.label('nav.us_exchange') : ''; return 'pick an account first (in the sidebar' + (usx ? ', or ' + usx + ' for US Exchange' : '') + ')'; }
    if (T.accountOwner(id) === null) { return id + ' belongs to a closed lifetime: read-only, no new orders'; }
    if (closedRow(id)) { return T.accountName(id) + ' is ' + String(T.accountRow(id).status).toLowerCase() + ': no new orders'; }
    return null;
  };
  /* forget the active account (state and the remembered one): a book switch, a lifetime reset, an account that closed;
     the header then shows the book card (BM-16) */
  T.forgetAccount = function () {
    T.state.active_account = null; T.state.account_detail = null; T.state.positions = null; T.state.orders = null; T.state.account_error = null;
    T.store.set('active_account', null);
    T.state.emit('active_account', null);
  };
  /* SHELL-02 / BM-16: an active or remembered account that no open lifetime holds any more (a reset) or that is CLOSED is
     forgotten; the page of such an account turns into the read-only notice.  Runs on every books / accounts answer. */
  function validateActive() {
    var cur = T.router.current;
    var a = T.state.active_account;
    if (a && T.accountOwner(a) === null) { T.forgetAccount(); }
    var s = T.store.get('active_account', null);
    if (s && (T.accountOwner(s) === null || (closedRow(s) && !(cur && cur.account === s)))) { T.store.set('active_account', null); }
    /* the shown account changed side: an open account closed by a reset, or a notice for an account the books now list (a
       prime-broker account opened a moment ago, before this answer) */
    if (cur && cur.account) {
      var own = T.accountOwner(cur.account);
      if ((!cur.closed && own === null) || (cur.closed && own)) { T.router.rerender(); }
    }
  }
  T.validateActive = validateActive;

  /* ---------------------------------------------------------------- instrument kinds (ticket dispatch) ------------- */
  var _kinds = Object.create(null);
  T.rememberKind = function (id, kind) { if (id && kind) { _kinds[id] = String(kind).toUpperCase(); } };
  T.rememberKinds = function (rows) { (rows || []).forEach(function (r) { if (r && r.instrument_id && r.kind) { _kinds[r.instrument_id] = String(r.kind).toUpperCase(); } }); };
  T.instKind = function (id) { return id ? (_kinds[id] || null) : null; };
  var OCC_RE = /\d{6}[CP]\d{8}$/;                               /* an OCC option id: its kind is known without asking */

  /* ---------------------------------------------------------------- dock / ticket slot ---------------------------- */
  T.dock = {
    visible: function () { return !T.q('#app').classList.contains('nodock'); },
    /* show(on): route-driven visibility; show(on, true) also remembers the choice (the user's `t` / x) */
    show: function (on, remember) { T.q('#app').classList.toggle('nodock', !on); if (remember) { T.store.set('dock', !!on); } },
    toggle: function () { T.dock.show(!T.dock.visible(), true); },
    body: function () { return T.q('#dock-body'); }
  };
  /* the empty ticket's hint, with the CURRENT row keys of a table (Settings > Keyboard) */
  function ticketHint() {
    var b = T.keys.label ? T.keys.label('grid.buy') : '', s = T.keys.label ? T.keys.label('grid.sell') : '';
    return b && s ? 'type a symbol or press ' + b + ' / ' + s + ' on a row' : 'type a symbol or use a row\'s buy / sell buttons';
  }
  /* account kinds whose orders the order ticket does not take: their own ticket kind once a package registers it, else a hint */
  var DOCK_DEFAULT = {
    PRIVATE: { kind: 'PRIVATE', hint: 'Private Markets: the negotiated ticket is on this page' },
    DEBT: { kind: 'BOND', hint: 'Debt Instruments: no bond ticket is loaded - bonds show their model prices on this page' },
    CREDIT: { kind: null, hint: 'Credit Instruments: loans, borrows and private credit are booked from this page' }
  };
  var _mounted = null;                                         /* {account_id, handler: 'ticket' | KIND | 'hint'} in the dock */
  function hasKinds() { return Object.keys(T.ticketKinds).some(function (k) { return !!T.ticketKinds[k]; }); }
  /* what an empty dock holds for this account: {handler: 'ticket'} (the order ticket), {handler: KIND, kind} (a registered
     ticket kind: the account plug-in's ticketKind or the kind's default) or {handler: 'hint', hint} */
  function emptyPlan(acct) {
    var ak = T.accountKind(acct); var plug = T.marketPlugins[ak] || {}; var dflt = DOCK_DEFAULT[ak] || null;
    var kind = plug.ticketKind !== undefined ? plug.ticketKind : (dflt ? dflt.kind : null);
    if (kind && T.ticketKinds[kind]) { return { handler: kind, kind: kind }; }
    var hint = (typeof plug.dockHint === 'function' ? plug.dockHint() : plug.dockHint) || (dflt && dflt.hint) || null;   /* a function: the keys in force */
    if (hint) { return { handler: 'hint', hint: hint }; }
    return { handler: 'ticket', kind: null };
  }
  function dispatch(kind, fields) {
    var body = T.dock.body(); if (!body) { return null; }
    var h = kind ? T.ticketKinds[kind] : null;
    var key = h ? kind : 'ticket';
    if (_mounted && _mounted.handler !== key) { T.clear(body); }   /* another kind's ticket held the dock: its handler starts clean */
    _mounted = { account_id: fields.account_id, handler: key };
    T.q('#dock-account').textContent = fields.account_id ? T.accountName(fields.account_id) : '';
    T.dock.show(true);
    var out = null;
    try {
      if (typeof h === 'function') { out = h(body, fields); }
      else if (h && typeof h.open === 'function') { out = h.open(body, fields); }
      else {
        var c = T.components.ticket;
        if (c && typeof c.open === 'function') { out = c.open(body, fields); }
        else { T.notBuilt(body, 'the order ticket', fields.instrument_id ? ('wanted: ' + (fields.side || '') + ' ' + (fields.qty || '') + ' ' + fields.instrument_id + ' in ' + fields.account_id) : 'no ticket; nothing can be submitted from this page yet'); }
      }
    } catch (e) { console.error('ticket failed', e); T.clear(body).appendChild(T.h('div', { class: 'error' }, 'ticket failed: ' + (e.message || e))); }
    T.ticket.hint(key === 'ticket' && !fields.instrument_id && !(T.state.ticket && T.state.ticket.instrument_id));
    return out;
  }
  T.ticket = {
    /* open / prefill the dock ticket: fields {account_id, instrument_id, kind, side, qty, order_type, hedge, close, ...}.  The
       instrument's kind picks the handler (T.ticketKinds, else components/ticket.js).  Refused (toast, nothing mounted) for an
       account of a closed lifetime or a CLOSED account. */
    open: function (fields) {
      fields = Object.assign({}, fields || {});
      fields.account_id = fields.account_id || T.state.active_account;
      var why = T.accountRefusal(fields.account_id);
      if (why) { T.ui.toast(why, 'err', 4000); return null; }
      var id = fields.instrument_id ? String(fields.instrument_id) : '';
      var kind = fields.kind ? String(fields.kind).toUpperCase() : (fields.leg || OCC_RE.test(id) ? 'OPTION' : (id ? T.instKind(id) : null));
      if (id && !kind && hasKinds()) {
        /* one look-up of an instrument this page did not list, so a bond / FX / private name reaches its own ticket */
        T.api.get('/api/instruments/' + encodeURIComponent(id)).then(function (inst) {
          var k = inst && (inst.kind || (inst.instrument && inst.instrument.kind));
          if (k) { T.rememberKind(id, k); }
          dispatch(k ? String(k).toUpperCase() : null, fields);
        }, function () { dispatch(null, fields); });
        return null;
      }
      return dispatch(kind, fields);
    },
    /* the dock of an account page always holds something (SHELL-13 / USX-28): the account's ticket - empty, with the hint
       "type a symbol or press b / s on a row" - or, for an account whose orders a ticket does not take, where they are */
    ensure: function (acct) {
      var body = T.dock.body(); if (!body || !acct || T.accountRefusal(acct)) { return; }
      var plan = emptyPlan(acct); var m = _mounted;
      if (m && body.firstChild) {
        if (m.handler === 'ticket' && plan.handler === 'ticket') { return; }   /* the order ticket follows the route itself and keeps what is typed */
        if (m.account_id === acct && m.handler === plan.handler && m.handler !== 'hint') { return; }
      }
      if (plan.handler === 'hint') { T.clear(body); _mounted = { account_id: acct, handler: 'hint' }; T.ticket.hint(true, plan.hint); return; }
      /* an empty ticket mounted by the shell never takes the keyboard: the page keys (a table's Up / Down and buy / sell keys) stay live;
         '/' or a click puts the cursor in its symbol box */
      var had = document.activeElement;
      T.ticket.open({ account_id: acct, kind: plan.kind || undefined });
      var now = document.activeElement;
      if (now && now !== had && body.contains(now)) {
        try { if (had && had !== document.body && had.focus) { had.focus({ preventScroll: true }); } else { now.blur(); } } catch (e) { /* focus is a convenience */ }
      }
    },
    /* the line above the dock body: the empty-ticket hint, or where an account's orders are booked */
    hint: function (on, text) { var el = T.q('#dock-hint'); if (!el) { return; } el.hidden = !on; el.textContent = text || ticketHint(); },
    mounted: function () { return _mounted; },
    /* ids the quotes poller must keep fresh for the ticket (the ticket instrument + hedge legs); set by components/ticket.js */
    ids: function (list) {
      T.state.ticket_ids = list || []; T.poll.kick('quotes');
      if (_mounted && _mounted.handler === 'ticket') { T.ticket.hint(!(list && list.length)); }
    },
    /* true while a MARKET order of the visible account is working (drives the 2 s quote interval) */
    marketWorking: function () {
      return (T.state.orders || []).some(function (o) { return o.order_type === 'MARKET' && (o.status === 'WORKING' || o.status === 'PARTIAL'); });
    }
  };

  /* ---------------------------------------------------------------- router ---------------------------------------- */
  function dec(s) { try { return decodeURIComponent(s); } catch (e) { return s; } }
  /* "type=orders&x=1" -> {type: 'orders', x: '1'} */
  function parseQuery(s) {
    var out = {};
    String(s || '').split('&').forEach(function (kv) { if (!kv) { return; } var i = kv.indexOf('='); var k = dec(i < 0 ? kv : kv.slice(0, i)); if (k) { out[k] = i < 0 ? '' : dec(kv.slice(i + 1)); } });
    return out;
  }
  var DEFAULT_ROUTE = '#/dashboard';
  /* a page tab that moved: its old address opens the new one (the address bar is corrected, no history entry) */
  var TAB_ALIASES = { settings: { data: 'market-data' } };
  var ROUTES = [
    [/^#\/dashboard$/, function () { return { page: 'dashboard' }; }],
    /* the removed news page (1.0.4 and earlier): its old address lands on the Dashboard with a one-time toast */
    [/^#\/news(?:[\/?].*)?$/, function () { return { page: 'dashboard', removed: true }; }],
    /* the last venue used (T.store last_venue), else the book's US Exchange Markets (render below) */
    [/^#\/trading$/, function () { return { page: 'trading' }; }],
    /* the pages of the new navigation (C1): a NOT YET BUILT panel until their file is in the build */
    [/^#\/pending(?:\?(.*))?$/, function (m) { return { page: 'pending', query: parseQuery(m[1]) }; }],
    [/^#\/completed(?:\?(.*))?$/, function (m) { return { page: 'completed', query: parseQuery(m[1]) }; }],
    [/^#\/accounts$/, function () { return { page: 'accounts' }; }],
    [/^#\/cash(?:\?(.*))?$/, function (m) { return { page: 'cash_collateral', query: parseQuery(m[1]) }; }],
    [/^#\/derivatives(?:\/([a-z_-]+))?$/, function (m) { return { page: 'derivatives', tab: m[1] || null }; }],
    [/^#\/history(?:\/([a-z_-]+))?$/, function (m) { return { page: 'history', hub: true, tab: m[1] || null }; }],
    [/^#\/treasury$/, function () { return { page: 'treasury' }; }],
    [/^#\/portfolio(?:\/([a-z]+))?$/, function (m) { return { page: 'portfolio', tab: m[1] || 'positions' }; }],
    [/^#\/accounting(?:\/([a-z-]+))?$/, function (m) { return { page: 'accounting', tab: m[1] || 'pnl' }; }],
    /* #/risk?section=financing and #/activity?type=orders: the query is handed to the page as route.query (SHELL-27) */
    [/^#\/risk(?:\?(.*))?$/, function (m) { return { page: 'risk', query: parseQuery(m[1]) }; }],
    [/^#\/activity(?:\?(.*))?$/, function (m) { return { page: 'activity', query: parseQuery(m[1]) }; }],
    [/^#\/watchlists$/, function () { return { page: 'watchlists' }; }],
    [/^#\/settings(?:\/([a-z_-]+))?$/, function (m) { return { page: 'settings', tab: m[1] || 'general' }; }],
    [/^#\/h\/([^\/]+)\/(portfolio|accounting|activity)(?:\/([a-z-]+))?$/, function (m) { return { page: 'history', lifetime: dec(m[1]), sub: m[2], tab: m[3] || null }; }],
    [/^#\/a\/([^\/]+)\/markets(?:\/([A-Za-z0-9_-]+))?$/, function (m) { return { page: 'markets', account: dec(m[1]), tab: m[2] || null }; }],
    [/^#\/a\/([^\/]+)\/i\/(.+)$/, function (m) { return { page: 'instrument', account: dec(m[1]), instrument: dec(m[2]) }; }],
    [/^#\/a\/([^\/]+)\/(positions|orders|cash|transactions)$/, function (m) { return { page: m[2], account: dec(m[1]) }; }]
  ];
  /* the account pages every account shares (OA-10): the tab bar on top of each of them, plug-in pages included */
  /* [route tab, label, the keyboard action that opens it (its CURRENT key is in the tooltip)] */
  var ACCOUNT_TABS = [['markets', 'Markets', 'nav.markets'], ['positions', 'Positions', 'nav.positions'], ['orders', 'Orders', 'nav.orders'], ['cash', 'Cash & financing', 'nav.cash'], ['transactions', 'Transactions', 'nav.transactions']];
  function accountBar(r) {
    var bar = T.h('div', { class: 'acct-tabs', role: 'tablist', 'aria-label': 'account pages' });
    var row = T.accountRow(r.account);
    bar.appendChild(T.h('span', { class: 'acct-tabs-name', title: r.account + (row && row.broker ? ' . ' + row.broker : '') }, T.accountName(r.account)));
    if (row && row.status && row.status !== 'OPEN') { bar.appendChild(T.stateTag(row.status)); }
    ACCOUNT_TABS.forEach(function (t) {
      var active = r.page === t[0] || (t[0] === 'markets' && r.page === 'instrument');
      bar.appendChild(T.h('a', { class: 'tab' + (active ? ' active' : ''), role: 'tab', 'aria-selected': active ? 'true' : 'false', href: T.router.account(r.account, t[0]), title: t[1] + ' of ' + T.accountName(r.account) + (T.keys.paren ? T.keys.paren(t[2]) : '') }, t[1]));
    });
    return bar;
  }
  /* an account no open lifetime holds: a read-only notice with the way to its history (never a ticket) */
  function closedNotice(el, r) {
    var lid = String(r.account).split(':')[0];
    T.pageHead(el, T.accountName(r.account), r.account);
    el.appendChild(T.h('div', { class: 'ro-banner' }, r.account + ' is not an account of an open lifetime: a lifetime reset closed it (its records stay, read-only) or it does not exist. Nothing can be traded in it.'));
    var links = T.h('div', { class: 'row' });
    if (/^[A-Z]+\d+$/.test(lid)) { links.appendChild(T.h('a', { class: 'btn', href: '#/h/' + encodeURIComponent(lid) + '/portfolio' }, 'History of lifetime ' + lid)); }
    links.appendChild(T.h('a', { class: 'btn', href: '#/settings/lifetimes' }, 'Lifetimes'));
    var first = T.keys && T.keys.account ? T.keys.account() : null;
    if (first) { links.appendChild(T.h('a', { class: 'btn', href: T.router.account(first, 'markets') }, T.accountName(first) + ' of ' + ((T.book.current() || {}).name || 'the selected book'))); }
    el.appendChild(links);
  }
  function tabKeys(list) { return Array.isArray(list) ? list.map(function (t) { return typeof t === 'string' ? t : (t && (t.key || t[0])); }).filter(Boolean) : null; }
  /* the NOT YET BUILT panel of a route whose page file is not in this build: its name and where its content is meanwhile */
  var NOT_BUILT = {
    pending: ['Pending Trades', 'working orders are on each account\'s Orders page and under Activity'],
    completed: ['Completed Trades', 'fills are on each account\'s Orders page; settlements are under Activity'],
    accounts: ['Accounts', 'every account is in the sidebar; Treasury has its own page'],
    cash_collateral: ['Cash & Collateral', 'cash, financing and collateral are on each account\'s Cash & financing page and under Accounting'],
    derivatives: ['Derivatives', 'OTC contracts are on the prime-broker account\'s Markets page (OTC tab); listed options and futures are on the account Positions pages'],
    history: ['History', 'the pages of a past lifetime are reached from Settings > Lifetimes; account activity is under Activity']
  };
  /* the History hub (C1): T.pages.history.hub = {render(el, route), unmount?, tabs?} or a render function; null while not built */
  function historyHub() {
    var h = T.pages.history && T.pages.history.hub;
    if (typeof h === 'function') { return { render: h }; }
    return h && typeof h.render === 'function' ? h : null;
  }
  /* #/trading: the last venue used while it is an open account of the selected book, else the book's US Exchange Markets */
  function tradingHash() {
    var last = T.store.get('last_venue', null);
    var book = T.book.current(); var ids = book && Array.isArray(book.accounts) ? book.accounts : null;
    if (last && (ids ? ids.indexOf(last) >= 0 : !!T.accountOwner(last)) && !closedRow(last)) { return T.router.account(last, 'markets'); }
    var id = accountsOf('US_EXCHANGE')[0] || gAccount();
    return id ? T.router.account(id, 'markets') : null;
  }
  function replaceHash(h) { try { history.replaceState(history.state, '', h); } catch (e) { /* the address bar keeps the old hash */ } }
  var _currentPage = null;
  T.router = {
    current: null,
    DEFAULT: DEFAULT_ROUTE,
    parse: function (hash) {
      hash = hash || DEFAULT_ROUTE;
      for (var i = 0; i < ROUTES.length; i++) { var m = ROUTES[i][0].exec(hash); if (m) { var r = ROUTES[i][1](m); r.hash = hash; return r; } }
      return { page: 'dashboard', hash: DEFAULT_ROUTE, unknown: hash };
    },
    go: function (hash) { if (location.hash === hash) { T.router.render(); } else { location.hash = hash; } },
    replace: replaceHash,
    link: function (parts) { return '#/' + parts.map(function (p) { return encodeURIComponent(p); }).join('/'); },
    account: function (id, sub) { return '#/a/' + encodeURIComponent(id) + '/' + (sub || 'markets'); },
    instrument: function (acct, inst) { return '#/a/' + encodeURIComponent(acct) + '/i/' + encodeURIComponent(inst); },
    accountBar: accountBar,
    rerender: function () { T.router.render(true); },
    render: function (force) {
      var r = T.router.parse(location.hash);
      var prev = T.router.current;
      /* SHELL-19: an unknown address says so and the address bar is corrected (no history entry) */
      if (r.unknown) { T.ui.toast('no page ' + r.unknown + ' - showing the Dashboard', '', 3500); replaceHash(DEFAULT_ROUTE); }
      /* C1: the removed news page's address (an old bookmark) lands on the Dashboard, said once */
      if (r.removed) { T.ui.toast('That page was removed - showing the Dashboard', '', 4000); replaceHash(DEFAULT_ROUTE); r.hash = DEFAULT_ROUTE; }
      /* UI-20: a tab that moved (Settings > Data Status is Settings > Market data since 1.0.6) */
      var alias = r.tab && TAB_ALIASES[r.page] ? TAB_ALIASES[r.page][r.tab] : null;
      if (alias) { r.tab = alias; r.hash = r.hash.replace(/\/[^\/?]*$/, '/' + alias); replaceHash(r.hash); }
      /* C1: #/trading is the last venue used (else US Exchange Markets): the address becomes that venue's */
      if (r.page === 'trading') {
        var venue = tradingHash();
        if (!venue) { T.ui.toast('the accounts are still loading - showing the Dashboard', '', 2500); }
        replaceHash(venue || DEFAULT_ROUTE);
        r = T.router.parse(venue || DEFAULT_ROUTE);
      }
      /* SHELL-02: an account of another book selects that book (switcher, sidebar and header follow); an account no open
         lifetime holds is shown as a read-only notice and is never made the active account */
      if (r.account) {
        var owner = T.accountOwner(r.account);
        if (owner === null) { r.closed = true; T.poll.kick('books'); }       /* a fresh answer re-classifies it (a new PB account) */
        else if (owner && owner.book_code !== T.book.code) {
          T.book.adopt(owner.book_code);
          T.ui.toast('switched to ' + (owner.name || owner.book_code) + ': ' + T.accountName(r.account) + ' belongs to it', '', 3000);
        }
      }
      T.router.current = r;
      if (r.account && !r.closed && r.account !== T.state.active_account) {
        T.state.active_account = r.account; T.store.set('active_account', r.account);
        T.state.account_detail = null; T.state.positions = null; T.state.orders = null; T.state.account_error = null;
        T.state.emit('active_account', r.account);
        T.poll.kick('account_detail'); T.poll.kick('positions'); T.poll.kick('orders');
      }
      if (r.closed && (T.state.active_account === r.account || T.store.get('active_account', null) === r.account)) { T.forgetAccount(); }
      if (r.account && !r.closed && (r.page === 'markets' || r.page === 'instrument')) { T.store.set('last_venue', r.account); }   /* #/trading */
      T.state.quote_ids = []; T.state.chain_target = null;
      if (_currentPage && _currentPage.unmount) { try { _currentPage.unmount(); } catch (e) { console.error(e); } }
      T.poll.clearPage();
      /* an archived lifetime's History pages (#/h/...) have their own container; every other page (the History hub too) is #page */
      var page = T.q('#page'), hist = T.q('#history');
      var archived = r.page === 'history' && !r.hub;
      page.hidden = archived; hist.hidden = !archived;
      var container = archived ? hist : page;
      var mod = r.hub ? historyHub() : T.pages[r.page];
      T.clear(container);
      /* SHELL-19: an unknown tab of a page that lists its tabs (mod.tabs / mod.TABS / mod.SECTIONS: [key] or [{key}] or
         [[key, label]]) falls back to the first one */
      var keys = mod && !archived ? tabKeys(mod.tabs || mod.TABS || mod.SECTIONS) : null;
      if (keys && keys.length && r.tab && keys.indexOf(r.tab) < 0) {
        T.ui.toast('no tab ' + r.tab + ' here - showing ' + keys[0], '', 3000);
        r.tab = keys[0]; r.hash = r.hash.replace(/\/[^\/?]*$/, '/' + keys[0]); replaceHash(r.hash);
      }
      if (r.closed) {
        closedNotice(container, r);
        _currentPage = null;
      } else if (!mod) {
        var nb = NOT_BUILT[r.page];
        if (nb) { T.notBuilt(container, nb[0], nb[1]); } else { T.notBuilt(container, 'this page (' + r.page + ')'); }
        _currentPage = null;
      } else {
        if (r.account) { container.appendChild(accountBar(r)); }
        _currentPage = mod;
        try { mod.render(container, r); } catch (e) { console.error('page', r.page, 'failed', e); container.appendChild(T.h('div', { class: 'error' }, 'page failed: ' + (e.message || e))); }
      }
      var dockOn = !!r.account && !r.closed && !closedRow(r.account) && T.store.get('dock', true);   /* no ticket on a CLOSED account */
      T.dock.show(dockOn);
      if (r.account) { T.q('#dock-account').textContent = r.closed ? '' : T.accountName(r.account); }
      if (dockOn) { T.ticket.ensure(r.account); }
      T.state.emit('route', r);
      if (!force || !prev || prev.page !== r.page) { try { window.scrollTo(0, 0); } catch (e) { /* ignore */ } }
    }
  };

  /* ---------------------------------------------------------------- pollers --------------------------------------- */
  var pollers = {}; var pagePollers = {}; var paused = false;
  function schedule(p) {
    if (p.timer) { clearTimeout(p.timer); p.timer = null; }
    if (paused || !p.active) { return; }
    var ms = typeof p.every === 'function' ? p.every() : p.every;
    p.timer = setTimeout(function () { run(p); }, Math.max(500, ms));
  }
  function run(p) {
    if (p.timer) { clearTimeout(p.timer); p.timer = null; }
    if (p.running) { p.again = true; return; }
    p.running = true;
    var out;
    try { out = p.fn(); } catch (e) { out = Promise.reject(e); }
    Promise.resolve(out).then(function () { p.errors = 0; p.last_error = null; T.pageStatus.ok(p); }, function (e) {
      p.errors = (p.errors || 0) + 1; p.last_error = e;
      /* an HTTP error is said on the page (T.pageStatus) and logged as a warning; anything else (a TypeError in a page's
         poller code) stays a console error */
      if (e && e.name === 'ApiError') { if (e.code !== 'NETWORK') { console.warn('poller', p.name, e.status, e.code, e.message); T.pageStatus.fail(p, e); } }
      else { console.error('poller', p.name, e); }
    }).then(function () {
      p.running = false; p.last = Date.now();
      if (p.again) { p.again = false; run(p); } else { schedule(p); }
    });
  }
  T.poll = {
    define: function (name, every, fn, opts) {
      var p = pollers[name] = { name: name, every: every, fn: fn, active: !(opts && opts.inactive), timer: null, running: false, errors: 0 };
      return p;
    },
    start: function (name) { var p = pollers[name]; if (p) { p.active = true; run(p); } },
    stop: function (name) { var p = pollers[name]; if (p) { p.active = false; if (p.timer) { clearTimeout(p.timer); p.timer = null; } } },
    kick: function (name) { var p = pollers[name]; if (p && p.active && !paused) { run(p); } },
    kickAll: function () { Object.keys(pollers).forEach(function (n) { T.poll.kick(n); }); Object.keys(pagePollers).forEach(function (n) { run(pagePollers[n]); }); },
    /* a page-scoped poller: cleared by the router on navigation */
    page: function (name, every, fn) { var p = pagePollers[name] = { name: 'page:' + name, every: every, fn: fn, active: true }; run(p); return p; },
    clearPage: function () { Object.keys(pagePollers).forEach(function (n) { var p = pagePollers[n]; p.active = false; if (p.timer) { clearTimeout(p.timer); } }); pagePollers = {}; T.pageStatus.clearPage(); },
    /* run a poller now by name (a global one, or a page one as 'page:<name>'): the page-status Retry */
    retry: function (name) { var p = pollers[name] || pagePollers[String(name).replace(/^page:/, '')]; if (p && p.active && !paused) { run(p); } },
    status: function () { var s = {}; Object.keys(pollers).forEach(function (n) { var p = pollers[n]; s[n] = { active: p.active, errors: p.errors, last: p.last }; }); return s; },
    paused: function () { return paused; }
  };
  /* ---- APIUI-10: a poller whose call fails with an HTTP error says so ON THE PAGE - one line above it with the error, the
     incident id of a 500 and a Retry - instead of leaving 'loading ...' or stale figures without a word; the line goes on
     the poller's next success.  A NETWORK failure is the banner's ('Server unreachable since ...'). */
  var PSTAT_LABEL = { account_detail: 'account', positions: 'positions', orders: 'working orders', quotes: 'quotes', chain: 'option chain',
                      accounts: 'accounts', books: 'books', attention: 'attention items', clock: 'sessions', health: 'server health', navcounts: 'trade counts' };
  var ACCOUNT_POLLERS = { account_detail: 1, positions: 1, orders: 1 };
  var _pfail = {};
  function paintPageStatus() {
    var el = T.q('#page-status'); if (!el) { return; }
    var cur = T.router.current || {};
    var keys = Object.keys(_pfail).filter(function (k) { return !ACCOUNT_POLLERS[k] || (cur.account && cur.account === T.state.active_account); });
    T.clear(el);
    el.hidden = !keys.length;
    if (!keys.length) { return; }
    keys.slice(0, 3).forEach(function (k) {
      var f = _pfail[k]; var every = typeof f.p.every === 'function' ? f.p.every() : f.p.every;
      el.appendChild(T.h('div', { class: 'pstat' }, T.h('b', null, 'could not load ' + (PSTAT_LABEL[k] || k.replace(/^page:/, '')) + ': '), T.api.describe(f.e),
        T.h('span', { class: 'muted' }, ' - ' + (f.p.errors > 1 ? f.p.errors + ' tries; ' : '') + 'what is shown is the last answer received (or none yet); retrying every ' + Math.round(Math.max(500, every || 10000) / 1000) + ' s '),
        T.h('button', { class: 'btn small', onclick: function () { T.poll.retry(k); } }, 'Retry')));
    });
    if (keys.length > 3) { el.appendChild(T.h('div', { class: 'muted' }, '+ ' + (keys.length - 3) + ' more failing')); }
  }
  T.pageStatus = {
    fail: function (p, e) { _pfail[p.name] = { p: p, e: e }; paintPageStatus(); },
    ok: function (p) { if (_pfail[p.name]) { delete _pfail[p.name]; paintPageStatus(); } },
    clearPage: function () { Object.keys(_pfail).forEach(function (k) { if (k.indexOf('page:') === 0) { delete _pfail[k]; } }); paintPageStatus(); },
    failing: function () { return Object.keys(_pfail); },
    paint: paintPageStatus
  };
  document.addEventListener('visibilitychange', function () {
    paused = !!document.hidden;
    if (paused) {
      Object.keys(pollers).forEach(function (n) { var p = pollers[n]; if (p.timer) { clearTimeout(p.timer); p.timer = null; } });
      Object.keys(pagePollers).forEach(function (n) { var p = pagePollers[n]; if (p.timer) { clearTimeout(p.timer); p.timer = null; } });
    } else { T.poll.kickAll(); }
  });

  function anyVisibleSessionOpen() {
    var s = T.state.sessions && T.state.sessions.sessions || T.state.sessions || {};
    return Object.keys(s).some(function (g) { return s[g] && s[g].open; });
  }
  function anyOrderWorking() { return (T.state.orders || []).some(function (o) { return o.status === 'WORKING' || o.status === 'PARTIAL'; }); }
  /* The ids the fast quotes poller asks for (GET /api/quotes = the server's WATCH class; at most MAX_WATCH = the server's
   * MAX_QUOTE_IDS): the ticket's instrument and hedge legs FIRST (T.ticket.ids), then the page's (T.watchQuotes), so a
   * long page list can never push the ticket's own quote out.  Long list pages (Markets) hand over only the rows that
   * need speed; the rest of their prices come with the page's own reload (the server's slow BROWSE class). */
  var MAX_WATCH = 200;
  function quoteIds() {
    var seen = Object.create(null); var out = [];
    function add(i) { if (i && !seen[i]) { seen[i] = 1; out.push(i); } }
    (T.state.ticket_ids || []).forEach(add); (T.state.quote_ids || []).forEach(add);
    return out.slice(0, MAX_WATCH);
  }
  /* /api/quotes carries the session states of the requested ids' groups only: they are merged into the full set (from
   * /api/state = a {group: state} map, or /api/sessions = {sessions: map, rules, ...}) instead of replacing it, so the
   * header keeps every session chip (UI sweep 2026-10-21: the chips collapsed to US EQ wherever quotes polled) */
  function mergeSessions(cur, part) {
    if (!part || typeof part !== 'object') { return cur; }
    if (cur && typeof cur === 'object' && cur.sessions && typeof cur.sessions === 'object') { return Object.assign({}, cur, { sessions: Object.assign({}, cur.sessions, part) }); }
    return Object.assign({}, (cur && typeof cur === 'object') ? cur : {}, part);
  }
  function definePollers() {
    T.poll.define('health', 10000, function () {
      return T.api.get('/api/health', null, { nobook: true }).then(function (h) {
        if (h.clock && h.clock.utc) { T.clock.sync(h.clock.utc); }
        if (h.version) { T.VERSION = h.version; }
        var changed = T.state.state_version != null && h.state_version !== T.state.state_version;
        T.state.state_version = h.state_version;
        T.state.set('health', h);
        if (changed) { T.poll.kick('accounts'); T.poll.kick('positions'); T.poll.kick('orders'); T.poll.kick('account_detail'); T.poll.kick('books'); T.poll.kick('attention'); }
      });
    });
    T.poll.define('clock', 30000, function () { return T.api.get('/api/sessions').then(function (s) { T.state.set('sessions', s); }); });
    T.poll.define('books', 30000, function () {
      return T.api.get('/api/books', null, { nobook: true }).then(function (b) {
        var list = Array.isArray(b) ? b : (b && b.books) || [];
        T.state.set('books', list);
        if (list.length && !list.some(function (x) { return x.book_code === T.book.code; })) { T.book.code = list[0].book_code; T.store.set('book', T.book.code); T.state.emit('book', T.book.code); }
        validateActive();
      });
    });
    /* an answer for a book that is no longer selected (a switch while the call was out) is dropped: the sidebar never shows
       another book's accounts under the selected one */
    T.poll.define('accounts', 10000, function () {
      var code = T.book.code;
      return T.api.get('/api/accounts').then(function (a) {
        if (code !== T.book.code) { return; }
        T.state.set('accounts', Array.isArray(a) ? a : (a && a.accounts) || []);
        validateActive();
      });
    });
    T.poll.define('attention', 30000, function () { return T.api.get('/api/attention').then(function (a) { T.state.set('attention', Array.isArray(a) ? a : (a && a.rows) || []); }); });
    T.poll.define('account_detail', 10000, function () {
      var id = T.state.active_account; if (!id) { return Promise.resolve(); }
      return T.api.get('/api/accounts/' + encodeURIComponent(id)).then(function (d) {
        if (T.state.active_account === id) { T.state.account_error = null; T.state.set('account_detail', d); }
      }, function (e) {
        /* the header card says why there are no figures (a 404 'account does not exist', a 500 ...) instead of '(loading)' */
        if (T.state.active_account === id && !(e && e.code === 'NETWORK')) { T.state.account_error = e; T.state.emit('account_detail', T.state.account_detail); }
        throw e;
      });
    });
    T.poll.define('positions', 10000, function () {
      var id = T.state.active_account; if (!id) { return Promise.resolve(); }
      return T.api.get('/api/accounts/' + encodeURIComponent(id) + '/positions').then(function (p) {
        var list = Array.isArray(p) ? p : (p && p.positions) || [];
        T.rememberKinds(list);
        if (T.state.active_account === id) { T.state.set('positions', list); }
      });
    });
    T.poll.define('orders', function () { return anyOrderWorking() ? 5000 : 30000; }, function () {
      var id = T.state.active_account; if (!id) { return Promise.resolve(); }
      return T.api.get('/api/accounts/' + encodeURIComponent(id) + '/orders', { status: 'open' }).then(function (o) { if (T.state.active_account === id) { T.state.set('orders', Array.isArray(o) ? o : (o && o.orders) || []); } });
    });
    var quoteTries = {}; var quoteFastMs = 0; var QUOTE_FAST_TRIES = 5;
    T.poll.define('quotes', function () { return quoteFastMs || (T.ticket.marketWorking() ? 2000 : (anyVisibleSessionOpen() ? 5000 : 15000)); }, function () {
      var ids = quoteIds(); if (!ids.length) { quoteFastMs = 0; return Promise.resolve(); }
      return T.api.get('/api/quotes', { ids: ids.join(',') }).then(function (r) {
        if (r.server_time) { T.clock.sync(r.server_time); }
        var q = T.state.quotes || {}; var got = r.quotes || {};
        var unknown = {}; (r.unknown || []).forEach(function (k) { unknown[k] = 1; });
        var fast = false;
        ids.forEach(function (k) {
          var x = got[k];
          if (unknown[k] || (x && (x.last != null || x.mid != null || x.bid != null || x.ask != null))) { delete quoteTries[k]; return; }
          quoteTries[k] = (quoteTries[k] || 0) + 1;
          if (quoteTries[k] <= QUOTE_FAST_TRIES) { fast = true; }
        });
        quoteFastMs = fast ? 1000 : 0;
        Object.keys(got).forEach(function (k) { q[k] = got[k]; });
        T.state.quotes = q; T.state.emit('quotes', q);
        if (r.sessions) { T.state.set('sessions', mergeSessions(T.state.sessions, r.sessions)); }
      });
    });
    /* a 202 from the chain route (the shared Cboe fetch is still running) is asked again after its retry_ms - at most
     * CHAIN_FAST_TRIES times in a row - instead of at the next 30 / 60 s poll (UI sweep 2026-10-21: the chain appeared
     * ~60 s after a 202); a 200, an error or a new target (instrument / expiry) starts over */
    var chainFast = { ms: 0, n: 0, key: null }; var CHAIN_FAST_TRIES = 40;
    T.poll.define('chain', function () { return chainFast.ms || ((T.state.ticket && T.state.ticket.option) ? 30000 : 60000); }, function () {
      var t = T.state.chain_target; if (!t) { chainFast.ms = 0; chainFast.n = 0; return Promise.resolve(); }
      return T.api.get('/api/instruments/' + encodeURIComponent(t.instrument_id) + '/chain', { expiry: t.expiry }).then(function (c) {
        var pending = !!(c && c.pending); var key = t.instrument_id + '|' + (t.expiry || '');
        if (chainFast.key !== key) { chainFast.key = key; chainFast.n = 0; }
        chainFast.n = pending ? chainFast.n + 1 : 0;
        chainFast.ms = (pending && chainFast.n <= CHAIN_FAST_TRIES) ? Math.max(500, Math.min(5000, Number(c.retry_ms) || 1500)) : 0;
        if (T.state.chain_target && T.state.chain_target.instrument_id === t.instrument_id) { T.state.set('chain', c); }
      }, function (e) { chainFast.ms = 0; chainFast.n = 0; throw e; });
    });
  }
  /* pages declare the quote ids they show (visible rows); the quotes poller merges them with the ticket's */
  T.watchQuotes = function (ids) { T.state.quote_ids = (ids || []).slice(0, MAX_WATCH); T.poll.kick('quotes'); };
  T.quote = function (id) { return (T.state.quotes || {})[id] || null; };

  /* ---------------------------------------------------------------- keys ------------------------------------------ */
  /* The keyboard (owner 2026-10-21 17:10: the F-keys instead of Alt and g sequences, and his own keys in Settings >
   * Keyboard; docs/KEYBINDS.md).  keys.js holds the registry - every action, its default key, the owner's overrides and
   * the matching rules; this section dispatches the global actions from it (RUN below) and nothing is hard-coded here:
   * - ONE capture-phase listener on window runs BEFORE every widget of the page.  A key held by a global action (F1-F12,
   *   Shift+F1-F12, '/' and '?' by default) runs it and stops the event, so the ticket or a table never also acts on it
   *   (one key press, at most one action).  While a dialog or the keyboard help is open, its 'close' key (Esc) closes the
   *   newest one first; a dialog takes no global action under it (it asks something first).  A held-down key repeats no
   *   global action.
   * - F-keys and Ctrl / Alt keys act while the cursor is in a text box (they type nothing); a single character never does.
   * - A key bound to nothing keeps the browser's behaviour (Ctrl+R reloads); an F-key or a Ctrl / Alt key bound to any
   *   action (a ticket or table one too) never reaches the browser (F1 does not open its help, F5 does not reload, F3 does
   *   not open find).
   * - T.keys.capture(fn) hands every key press to fn and runs nothing else (Settings > Keyboard: Change, Test a key).
   * - A bubble-phase listener on document hands the selected table (T.keys.grid) what no one else handled.
   * The page widgets read the same registry: T.keys.is(e, id) / T.keys.action(e, scope) (keys.js; docs/KEYBINDS.md). */
  var K = T.keys = T.keys || {};
  /* a box the viewer can see: in the layout (offsetParent) and enabled - never one inside the hidden dock (SHELL-12) */
  function visibleOne(sel) { var list = T.qa(sel); for (var i = 0; i < list.length; i++) { if (list[i].offsetParent !== null && !list[i].disabled) { return list[i]; } } return null; }
  /* The account the account keys used before the F-keys (SHELL-02; T.keys.account, the closed-lifetime notice): the active
     or remembered one when the selected book's open lifetime holds it and it is not CLOSED; else the selected book's
     first trading account (sidebar order: US Exchange first) */
  function gAccount() {
    var book = T.book.current(); var ids = book && Array.isArray(book.accounts) ? book.accounts : null;
    var cands = [T.state.active_account, T.store.get('active_account', null)];
    for (var i = 0; i < cands.length; i++) {
      var c = cands[i];
      if (c && (ids ? ids.indexOf(c) >= 0 : T.accountOwner(c) !== null) && !closedRow(c)) { return c; }
    }
    var list = (T.sidebarAccounts ? T.sidebarAccounts() : []).filter(function (a) { return !ids || ids.indexOf(a.account_id) >= 0; });
    if (list.length) { return list[0].account_id; }
    if (ids) { return ids.filter(function (x) { return /:US_EXCHANGE$/.test(x); })[0] || ids.filter(function (x) { return !/:TREASURY$/.test(x); })[0] || null; }
    return null;
  }
  function bookName(b) { return b ? (b.name || b.book_code) : 'the selected book'; }
  /* the open accounts of a role ('PB' = every prime broker) in a book, sidebar order for the selected book */
  function accountsOf(role, book) {
    var cur = T.book.current(); book = book || cur;
    function match(id) { var r = roleOfId(id); return role === 'PB' ? /^PB_/.test(r) : r === role; }
    if (book && cur && book.book_code === cur.book_code && T.sidebarAccounts) {
      var side = T.sidebarAccounts().map(function (a) { return a.account_id; }).filter(match);
      if (side.length) { return side; }
    }
    var ids = book && Array.isArray(book.accounts) ? book.accounts : [];
    return ids.filter(function (id) { return match(id) && !(book === cur && closedRow(id)); });
  }
  /* the account page the route shows (an account of an open lifetime), else null */
  function routeAccount() { var r = T.router.current; return r && r.account && !r.closed ? r.account : null; }
  function needBook() { var b = T.book.current(); if (!b) { T.ui.toast('the books are still loading', '', 2000); } return b; }
  function goRole(role, name) {
    var book = needBook(); if (!book) { return; }
    var id = accountsOf(role, book)[0];
    if (!id) { T.ui.toast(bookName(book) + ' has no open ' + name + ' account', '', 3500); return; }
    T.router.go(T.router.account(id, 'markets'));
  }
  var BOOK_ACTION = { P: 'book.personal', F: 'book.fund', I: 'book.institutional' };
  var PROFILE_ACTION = { INDIVIDUAL: 'book.personal', FUND: 'book.fund', INSTITUTION: 'book.institutional' };
  function bookKey(b) { var id = BOOK_ACTION[b.book_code] || PROFILE_ACTION[b.profile]; return id ? K.label(id) : ''; }
  /* F5: the book's Private Markets account; a book without one is told which book has it (and its key) */
  function goPrivate() {
    var book = needBook(); if (!book) { return; }
    var id = accountsOf('PRIVATE', book)[0];
    if (id) { T.router.go(T.router.account(id, 'markets')); return; }
    var e = book.eligibility || {};
    var others = (T.state.books || []).filter(function (b) { return b.book_code !== book.book_code && accountsOf('PRIVATE', b).length; });
    var msg = bookName(book) + ' has no Private Markets account' + (e.private_reason ? ' (' + e.private_reason + ')' : '');
    if (others.length) {
      msg += '. ' + others.map(function (b) { var k = bookKey(b); return bookName(b) + (k ? ' (' + k + ' selects it)' : ''); }).join(' and ') + (others.length > 1 ? ' have one' : ' has one');
    } else { msg += '. No book has one yet'; }
    T.ui.toast(msg, '', 7000);
  }
  /* F6: the book's prime broker account (pressed again on one: the next); none: how to open one */
  function goPrime() {
    var book = needBook(); if (!book) { return; }
    var list = accountsOf('PB', book);
    if (!list.length) {
      var e = book.eligibility || {};
      /* the onboarding button lives on the Accounts page header (1.0.6); the sidebar keeps it only while that page is not in the build */
      var where = T.pages.accounts ? 'open one with "Open prime broker account" on the Accounts page' : 'open one with "+ Open prime broker account" in the sidebar';
      T.ui.toast(bookName(book) + ' has no prime broker account: ' + (e.can_onboard ? where
        : 'new relationships need the PB / OTC threshold' + (e.pb_otc_reason ? ' (' + e.pb_otc_reason + ')' : '')), '', 7000);
      return;
    }
    var i = list.indexOf(routeAccount());
    T.router.go(T.router.account(list[(i + 1) % list.length], 'markets'));
  }
  /* F8 / Shift+F4 / F5 / F6: a page of the account you are on; off an account page, the book's US Exchange account */
  function goTab(tab) {
    var id = routeAccount() || accountsOf('US_EXCHANGE')[0] || gAccount();
    if (!id) { T.ui.toast('the accounts are still loading', '', 2000); return; }
    T.router.go(T.router.account(id, tab));
  }
  /* account N of the selected book in sidebar order (the old Alt+1..9; unbound by default): the same page as now */
  function goNth(n) {
    var accts = T.sidebarAccounts ? T.sidebarAccounts() : (T.state.accounts || []);
    var a = accts[n - 1];
    if (!a) { T.ui.toast(bookName(T.book.current()) + ' has ' + accts.length + ' trading account' + (accts.length === 1 ? '' : 's'), '', 2500); return; }
    var cur = T.router.current;
    T.router.go(T.router.account(a.account_id, cur && cur.account && !cur.closed && cur.page !== 'instrument' ? cur.page : 'markets'));
  }
  function selectBook(code, profile, word) {
    var books = T.state.books || [];
    var b = books.filter(function (x) { return x.book_code === code; })[0] || books.filter(function (x) { return x.profile === profile; })[0];
    if (!b) { T.ui.toast('no ' + word + ' book' + (books.length ? '' : ' (the books are still loading)'), '', 2500); return; }
    if (b.book_code === T.book.code) { T.ui.toast(bookName(b) + ' is already selected', '', 1500); return; }
    T.book.set(b.book_code);
    T.ui.toast(bookName(b) + ' selected', 'ok', 1500);
  }
  /* Shift+F10 (1.0.6, C1): the History hub (#/history); while the hub is not in the build, the pages of the book's last past lifetime */
  function goHistoryHub() {
    if (historyHub()) { T.router.go('#/history'); return; }
    goHistory();
  }
  /* the History pages of the book's last past lifetime (#/h/<lifetime>/portfolio) */
  function goHistory() {
    var book = needBook(); if (!book) { return; }
    var none = bookName(book) + ' has no past lifetime yet: History shows a lifetime after a reset (Settings > Lifetimes)';
    if (book.past_lifetimes === 0) { T.ui.toast(none, '', 5000); return; }
    var code = book.book_code;
    T.api.get('/api/books/' + encodeURIComponent(code) + '/lifetimes', null, { nobook: true }).then(function (r) {
      if (T.book.code !== code) { return; }
      var past = ((r && r.lifetimes) || []).filter(function (x) { return x && x.status === 'CLOSED'; });
      if (!past.length) { T.ui.toast(none, '', 5000); return; }
      T.router.go('#/h/' + encodeURIComponent(past[0].lifetime_id) + '/portfolio');
      if (past.length > 1) { T.ui.toast('History of ' + (past[0].name || past[0].lifetime_id) + ', the last of ' + past.length + ' past lifetimes (Settings > Lifetimes lists them all)', '', 4000); }
    }, function (e) { T.api.fail(e, 'history'); });
  }
  /* F9 (the old 't'): the ticket trades in the account whose page it is on (SHELL-13); elsewhere it only hides an open dock */
  function toggleDock() {
    var r = T.router.current;
    if (T.dock.visible()) { T.dock.toggle(); return; }
    if (!r || !r.account || r.closed) {
      var usx = K.label('nav.us_exchange');
      T.ui.toast('open an account page first' + (usx ? ' (' + usx + ' opens US Exchange)' : '') + ': the ticket trades in the account whose page it is on', '', 3000);
      return;
    }
    var why = T.accountRefusal(r.account);
    if (why) { T.ui.toast(why, 'err', 3000); return; }
    T.dock.toggle(); T.ticket.ensure(r.account);
  }
  function focusSearch() {
    var box = visibleOne('[data-search]') || visibleOne('#ticket-instrument');
    if (box) { box.focus(); if (box.select) { box.select(); } } else { T.ui.toast('no search box on this page', '', 1500); }
  }
  function goPage(hash) { return function () { T.router.go(hash); }; }
  /* every global (and dialog) action of keys.js; a static test checks the two lists match */
  var RUN = {
    'nav.us_exchange': function () { goRole('US_EXCHANGE', 'US Exchange'); },
    'nav.debt': function () { goRole('DEBT', 'Debt Instruments'); },
    'nav.credit': function () { goRole('CREDIT', 'Credit Instruments'); },
    'nav.foreign_fx': function () { goRole('FOREIGN_FX', 'Foreign Markets & FX'); },
    'nav.private': goPrivate,
    'nav.prime_broker': goPrime,
    'nav.markets': function () { goTab('markets'); },
    'nav.positions': function () { goTab('positions'); },
    'nav.orders': function () { goTab('orders'); },
    'nav.cash': function () { goTab('cash'); },
    'nav.transactions': function () { goTab('transactions'); },
    'nav.account_1': function () { goNth(1); }, 'nav.account_2': function () { goNth(2); }, 'nav.account_3': function () { goNth(3); },
    'nav.account_4': function () { goNth(4); }, 'nav.account_5': function () { goNth(5); }, 'nav.account_6': function () { goNth(6); },
    'nav.account_7': function () { goNth(7); }, 'nav.account_8': function () { goNth(8); }, 'nav.account_9': function () { goNth(9); },
    'book.personal': function () { selectBook('P', 'INDIVIDUAL', 'Personal'); },
    'book.fund': function () { selectBook('F', 'FUND', 'Fund'); },
    'book.institutional': function () { selectBook('I', 'INSTITUTION', 'Institutional'); },
    'book.next': function () { T.book.cycle(); },
    'page.dashboard': goPage('#/dashboard'),
    'page.portfolio': goPage('#/portfolio'), 'page.treasury': goPage('#/treasury'), 'page.risk': goPage('#/risk'),
    'page.accounting': goPage('#/accounting'), 'page.activity': goPage('#/activity'), 'page.watchlists': goPage('#/watchlists'), 'page.settings': goPage('#/settings'),
    'page.history': goHistoryHub,
    'page.trading': goPage('#/trading'),
    /* C1: on an account page, Pending / Completed Trades open filtered to that account and Cash & Collateral opens its Cash & financing */
    'page.pending': function () { var id = routeAccount(); T.router.go(id ? '#/pending?account=' + encodeURIComponent(id) : '#/pending'); },
    'page.completed': function () { var id = routeAccount(); T.router.go(id ? '#/completed?account=' + encodeURIComponent(id) : '#/completed'); },
    'page.accounts': goPage('#/accounts'),
    'page.cash_collateral': function () { var id = routeAccount(); T.router.go(id ? T.router.account(id, 'cash') : '#/cash'); },
    'page.derivatives': goPage('#/derivatives'),
    'dock.toggle': toggleDock,
    'app.search': focusSearch,
    'app.help': function () { T.ui.help(); },
    'app.help_2': function () { T.ui.help(); },
    'dialog.close': function () { T.ui.closeTop(); }
  };
  function runKeyAction(id, e) {
    var fn = RUN[id];
    if (!fn) { console.warn('keyboard: no handler for the action', id); return; }
    try { fn(e); } catch (x) { console.error('keyboard action', id, 'failed', x); }
  }
  function stopKey(e) { e.preventDefault(); e.stopPropagation(); if (e.stopImmediatePropagation) { e.stopImmediatePropagation(); } }
  var _capture = null;
  var _kbWaitToast = 0;
  function onKeyCapture(e) {
    if (!K.ACTIONS) { return; }                                 /* keys.js missing: nothing is bound */
    var info = K.fromEvent(e);
    /* Settings > Keyboard listens: nothing runs; the listener may hand a key back ('pass': Tab leaves "Test a key") */
    if (_capture) { var pass = false; try { pass = _capture(e, info) === 'pass'; } catch (x) { console.error('key capture failed', x); } if (!pass) { stopKey(e); } return; }
    if (!info.combo) { return; }
    if (_modals.length || helpOpen()) {
      var d = K.action(e, 'dialog');
      if (d) { stopKey(e); if (!e.repeat) { runKeyAction(d, e); } return; }
    }
    var g = K.action(e, 'global');
    if (g) {
      stopKey(e);
      if (e.repeat || _modals.length) { return; }
      /* a browser that has neither the owner's keys from the app nor its own copy yet (a new profile, the first second of a
         boot): the defaults may be keys he moved - hold the key back rather than run the wrong action (KB-R1-03) */
      if (K.ready && !K.ready()) {
        if (Date.now() - _kbWaitToast > 1500) { _kbWaitToast = Date.now(); T.ui.toast('your keys are still loading from the app - press it again in a moment', '', 2500); }
        rereadKeys(true);
        return;
      }
      if (helpOpen() && g !== 'app.help' && g !== 'app.help_2') { T.ui.help(false); }
      runKeyAction(g, e);
      return;
    }
    /* a dialog takes no page key either: a ticket / table key pressed outside the newest dialog (focus left behind it)
       never acts on the page under it (KB-R1-09) */
    if (_modals.length && K.boundTo(info.combo).length) {
      var top = _modals[_modals.length - 1];
      if (!(top && top.el && top.el.contains(e.target))) { stopKey(e); return; }
    }
    if (K.freeWhileTyping(info.combo) && K.boundTo(info.combo).length) { e.preventDefault(); }
  }
  /* what no widget handled: the selected table's keys (until grid.js reads the registry, today's rule: never while typing
     and never with Ctrl / Alt) and the 'escape' state event.  A table never acts under an open dialog or the keyboard help. */
  function onKeyBubble(e) {
    if (e.key === 'Escape' || e.key === 'Esc') { T.state.emit('escape', e); }
    var g = K.grid;
    if (!g || !g.key) { return; }
    if (_modals.length || helpOpen()) { return; }
    if (!(K.adopted && K.adopted('components/grid.js')) && (K.typingTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey)) { return; }
    if (g.key(e)) { e.preventDefault(); }
  }
  var _installed = false;
  /* the dock button's tooltip and the empty ticket's hint follow the keys in force */
  function paintKeyHints() {
    var b = T.q('#dock-close'); if (b) { b.title = 'show / hide the ticket dock' + K.paren('dock.toggle'); }
    var h = T.q('#dock-hint'); if (h && _mounted && _mounted.handler === 'ticket') { h.textContent = ticketHint(); }
  }
  var _kbRead = 0;
  /* the owner's keys again from the server when the window comes back (another window may have changed them; the same
     browser's other windows follow at once through keys.js's storage listener).  `force`: the keys never arrived (the boot
     call failed, a key pressed before they loaded) - asked again at most every 3 s */
  function rereadKeys(force) {
    if (!K.load) { return; }
    var wait = force === true || (K.loaded && !K.loaded()) ? 3000 : 15000;
    if ((document.hidden && force !== true) || Date.now() - _kbRead < wait) { return; }
    _kbRead = Date.now();
    T.api.get('/api/settings', null, { nobook: true }).then(function (s) { if (!_capture) { K.load((s && s.keybinds) || {}); } }, function () { /* the banner reports an unreachable server */ });
  }
  K.account = gAccount;
  K.grid = K.grid || null;                                     /* the focused grid handle registers itself (components/grid.js) */
  K.handler = onKeyCapture;
  K.run = runKeyAction;
  K.RUN = RUN;
  /* every key press goes to fn(event, T.keys.fromEvent(event)) and nothing else runs; returns the release function */
  K.capture = function (fn) { _capture = fn; return function () { if (_capture === fn) { _capture = null; } }; };
  K.capturing = function () { return !!_capture; };
  /* the global and table keys in force as [key, words] (the full map is T.help.renderKeys) */
  K.map = function () {
    return (K.ACTIONS || []).filter(function (a) { return a.scope !== 'ticket' && K.binding(a.id); }).map(function (a) { return [K.label(a.id), a.label]; });
  };
  K.install = function () {
    if (_installed) { return; }
    _installed = true;
    if (!K.ACTIONS) { console.error('keyboard: keys.js (the keybinding registry) did not load - no key is bound'); return; }
    if (K.refresh) { K.refresh(); }                             /* every component is loaded: actions of files that read the registry follow the owner */
    K.ACTIONS.forEach(function (a) { if ((a.scope === 'global' || a.scope === 'dialog') && !RUN[a.id]) { console.warn('keyboard: no handler for', a.id); } });
    window.addEventListener('keydown', onKeyCapture, true);
    document.addEventListener('keydown', onKeyBubble);
    T.state.on('keybinds', function () { paintKeyHints(); if (helpOpen()) { paintHelp(T.q('#help')); } });
    window.addEventListener('focus', rereadKeys);
    document.addEventListener('visibilitychange', rereadKeys);
    paintKeyHints();
  };

  /* ---------------------------------------------------------------- boot ------------------------------------------ */
  T.boot = function () {
    if (T._booted) { return; } T._booted = true;
    T.theme.apply();
    T.keys.install();                                          /* the keyboard: keys.js registry, the owner's keys (cached until /api/state answers) */
    var closeBtn = T.q('#dock-close');
    if (closeBtn) { closeBtn.addEventListener('click', function () { T.dock.toggle(); var r = T.router.current; if (T.dock.visible() && r && r.account) { T.ticket.ensure(r.account); } }); }
    window.addEventListener('hashchange', function () { T.router.render(); });
    definePollers();
    ['header', 'attention', 'sidebar', 'bookswitcher'].forEach(function (n) {
      var c = T.components[n];
      if (c && c.init) { try { c.init(); } catch (e) { console.error('component', n, 'init failed', e); } }
      else { console.warn('component missing at boot:', n); }
    });
    /* C2: /api/health (the module list behind T.hasModule) is read with /api/state, so the first route knows what is bound */
    var bootHealth = T.api.get('/api/health', null, { nobook: true }).then(function (h) {
      if (h && typeof h === 'object' && !T.state.health) {
        if (h.clock && h.clock.utc) { T.clock.sync(h.clock.utc); }
        if (h.version) { T.VERSION = h.version; }
        T.state.state_version = h.state_version;
        T.state.set('health', h);
      }
    }, function () { /* the health poller reports an unreachable server */ });
    T.api.get('/api/state', null, { nobook: true }).then(function (s) {
      T.state.conventions = s.conventions || {};
      T.state.settings = s.settings || null;
      if (T.keys.load) { T.keys.load((s.settings && s.settings.keybinds) || {}); }   /* the owner's keys before the first route renders */
      if (s.version) { T.VERSION = s.version; }
      if (s.clock && s.clock.utc) { T.clock.sync(s.clock.utc); }
      if (s.sessions) { T.state.set('sessions', s.sessions); }
      if (s.health) { T.state.set('health', s.health); }
      /* the books (each with its open lifetime's accounts) before the first route, so an account link of another book or of a
         closed lifetime is classified on the first render */
      if (Array.isArray(s.books) && s.books.length && !(T.state.books || []).length) {
        T.state.set('books', s.books);
        if (!s.books.some(function (x) { return x.book_code === T.book.code; })) { T.book.code = s.books[0].book_code; T.store.set('book', T.book.code); }
      }
      if (Array.isArray(s.accounts) && s.book && s.book === T.book.code && !(T.state.accounts || []).length) { T.state.set('accounts', s.accounts); }
      T.state.set('state', s);
    }, function (e) {
      T.api.fail(e, 'bootstrap /api/state');
      setTimeout(function () { rereadKeys(true); }, 3000);     /* the owner's keys: asked again (KB-R1-03) */
    }).then(function () { return bootHealth; }).then(function () {
      Object.keys(pollers).forEach(function (n) { T.poll.start(n); });
      if (!location.hash) { replaceHash(DEFAULT_ROUTE); }
      T.router.render();
      validateActive();
    });
  };
})();
