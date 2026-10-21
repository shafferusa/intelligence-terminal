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
 *   T.keys    the keyboard map (s17.6).  T.ui toast / modal / confirm / help.  T.conv convention sentences
 *             (/api/state.conventions) shown as inline one-sentence notes (no glossary - owner amendment 18:28).
 *   T.book    the selected book (localStorage); every poller passes `book=`; Alt+B cycles.
 *   T.ticket  the dock slot: delegates to components/ticket.js (WP-13) when it is loaded, else "ticket not built".
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
  T.VERSION = '1.0.0';
  T.components = T.components || {};
  T.pages = T.pages || {};

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
                   'is refused (directive amendment -8).',
    /* The buying-power cell of one account / currency (accounting.BUYING_POWER_TEXT; 2026-10-21, second pass): `total` =
     * available + Treasury left + PB left, where what the account's working orders, payables and any overdraft already
     * need beyond its own cash (`committed`; their fills sweep it) has come off Treasury available first and PB headroom
     * second.  It is the figure the ticket's fundability uses (an order within it is accepted, one beyond it is refused
     * with 409 INSUFFICIENT_FUNDS), so it never promises cash an earlier order will sweep.  bp = a BuyingPower row of
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
                      'refused (directive amendment -8).',
    time: function (epoch) { if (!epoch) { return '--:--:--'; } return fix24(NY_TIME.format(new Date(epoch * 1000))); },
    hm: function (epoch) { if (!epoch) { return '--:--'; } return fix24(NY_HM.format(new Date(epoch * 1000))); },
    date: function (epoch) { if (!epoch) { return ''; } return NY_DATE.format(new Date(epoch * 1000)); },
    dt: function (epoch) { if (!epoch) { return ''; } return T.fmt.date(epoch) + ' ' + T.fmt.hm(epoch); },
    dow: function (epoch) { if (!epoch) { return ''; } return NY_DOW.format(new Date(epoch * 1000)); },
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
    set: function (code) {
      if (code === T.book.code) { return; }
      T.book.code = code; T.store.set('book', code);
      T.state.active_account = null; T.state.account_detail = null; T.state.positions = null; T.state.orders = null;
      T.state.emit('book', code);
      if (T.router.current && T.router.current.account) { T.router.go('#/portfolio'); }
      T.poll.kickAll();
      T.router.rerender();
    },
    cycle: function () {
      var books = T.state.books || []; if (!books.length) { return; }
      var i = -1;
      for (var k = 0; k < books.length; k++) { if (books[k].book_code === T.book.code) { i = k; } }
      T.book.set(books[(i + 1) % books.length].book_code);
    }
  };

  /* ---------------------------------------------------------------- api ------------------------------------------- */
  function ApiError(status, code, message, details, body, headers) {
    this.name = 'ApiError'; this.status = status; this.code = code || 'ERROR'; this.message = message || code || ('HTTP ' + status);
    this.details = details; this.body = body; this.headers = headers || {}; this.request_id = body && body.request_id;
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
      var msg = e && e.name === 'LostResponse' ? ('lost response: ' + e.message) : (e && e.code ? (e.code + ': ' + e.message) : String(e && e.message || e));
      if (e && e.body && e.body.incident_id) { msg += ' (incident ' + e.body.incident_id + ')'; }
      console.error(where || 'api', e);
      T.ui.toast(msg, 'err', 6000);
      return msg;
    }
  };

  /* ---------------------------------------------------------------- conventions ----------------------------------- */
  T.conv = function (key) { var c = T.state.conventions || {}; return c[key] || ''; };
  /* an inline one-sentence note block for the given keys (only the ones the server knows) */
  T.convNote = function (keys) {
    var el = T.h('div', { class: 'conv' });
    (keys || []).forEach(function (k) { var s = T.conv(k); if (s) { el.appendChild(T.h('div', null, T.h('b', null, k), s)); } });
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
  T.ui = {
    toast: function (msg, kind, ms) {
      var root = T.q('#toast-root'); if (!root) { return; }
      var el = T.h('div', { class: 'toast ' + (kind || ''), role: 'status' }, msg);
      root.appendChild(el);
      setTimeout(function () { if (el.parentNode) { el.parentNode.removeChild(el); } }, ms || 3500);
    },
    modal: function (spec) {
      var root = T.q('#modal-root');
      var box = T.h('div', { class: 'modal' + (spec.wide ? ' wide' : ''), role: 'dialog', 'aria-label': spec.title || 'dialog' });
      var bg = T.h('div', { class: 'modal-bg' }, box);
      var handle = { el: box, bg: bg, close: function () { if (bg.parentNode) { bg.parentNode.removeChild(bg); } document.removeEventListener('keydown', onKey); } };
      function onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); handle.close(); } }
      box.appendChild(T.h('h1', null, spec.title || '', T.h('button', { class: 'btn small', onclick: handle.close, title: 'close (Esc)' }, 'x')));
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
      bg.addEventListener('mousedown', function (e) { if (e.target === bg && !spec.sticky) { handle.close(); } });
      document.addEventListener('keydown', onKey);
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
          actions: [{ label: opts.cancel || 'Cancel', onClick: function (m) { m.close(); resolve(false); } },
                    { label: opts.ok || 'Confirm', cls: 'primary', onClick: function (m) { m.close(); resolve(true); } }]
        });
        h.bg.addEventListener('mousedown', function (e) { if (e.target === h.bg) { h.close(); resolve(false); } });
      });
    },
    help: function (toggle) {
      var el = T.q('#help'); if (!el) { return; }
      if (toggle === false || (toggle !== true && !el.hidden)) { el.hidden = true; return; }
      var rows = T.keys.map();
      var kv = T.h('div', { class: 'kv' });
      rows.forEach(function (r) { kv.appendChild(T.h('div', { html: r[0].split(' ').map(function (k) { return '<kbd>' + T.esc(k) + '</kbd>'; }).join(' ') })); kv.appendChild(T.h('div', null, r[1])); });
      T.clear(el).appendChild(T.h('div', { class: 'keys' }, T.h('h1', null, 'Keyboard', T.h('button', { class: 'btn small', onclick: function () { el.hidden = true; } }, 'x')), kv,
        T.h('div', { class: 'hint' }, 'Global unless noted; inside inputs only Esc and Ctrl+Enter act. The ticket keys (b s x c, m l k j, + -, h, 1-5, Enter, Ctrl+Enter, Esc) belong to components/ticket.js.')));
      el.hidden = false;
      el.onclick = function (e) { if (e.target === el) { el.hidden = true; } };
    }
  };

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

  /* ---------------------------------------------------------------- dock / ticket slot ---------------------------- */
  T.dock = {
    visible: function () { return !T.q('#app').classList.contains('nodock'); },
    /* show(on): route-driven visibility; show(on, true) also remembers the choice (the user's `t` / x) */
    show: function (on, remember) { T.q('#app').classList.toggle('nodock', !on); if (remember) { T.store.set('dock', !!on); } },
    toggle: function () { T.dock.show(!T.dock.visible(), true); },
    body: function () { return T.q('#dock-body'); }
  };
  T.ticket = {
    /* open / prefill the single ticket (s17.6) for the active account: fields {account_id, instrument_id, side, qty, order_type, hedge: bool} */
    open: function (fields) {
      fields = fields || {};
      fields.account_id = fields.account_id || T.state.active_account;
      var body = T.dock.body();
      T.q('#dock-account').textContent = fields.account_id || '';
      var c = T.components.ticket;
      T.dock.show(true);
      if (c && typeof c.open === 'function') { try { return c.open(body, fields); } catch (e) { console.error('ticket failed', e); T.clear(body).appendChild(T.h('div', { class: 'error' }, 'ticket failed: ' + (e.message || e))); return null; } }
      T.notBuilt(body, 'components/ticket.js (WP-13): the order ticket', fields.instrument_id ? ('wanted: ' + (fields.side || '') + ' ' + (fields.qty || '') + ' ' + fields.instrument_id + ' in ' + fields.account_id) : 'no ticket; nothing can be submitted from this page yet');
      return null;
    },
    /* ids the quotes poller must keep fresh for the ticket (the ticket instrument + hedge legs); set by components/ticket.js */
    ids: function (list) { T.state.ticket_ids = list || []; T.poll.kick('quotes'); },
    /* true while a MARKET order of the visible account is working (drives the 2 s quote interval) */
    marketWorking: function () {
      return (T.state.orders || []).some(function (o) { return o.order_type === 'MARKET' && (o.status === 'WORKING' || o.status === 'PARTIAL'); });
    }
  };

  /* ---------------------------------------------------------------- router ---------------------------------------- */
  var ROUTES = [
    [/^#\/news$/, function () { return { page: 'news' }; }],
    [/^#\/treasury$/, function () { return { page: 'treasury' }; }],
    [/^#\/portfolio(?:\/([a-z]+))?$/, function (m) { return { page: 'portfolio', tab: m[1] || 'positions' }; }],
    [/^#\/accounting(?:\/([a-z-]+))?$/, function (m) { return { page: 'accounting', tab: m[1] || 'pnl' }; }],
    [/^#\/risk$/, function () { return { page: 'risk' }; }],
    [/^#\/activity$/, function () { return { page: 'activity' }; }],
    [/^#\/watchlists$/, function () { return { page: 'watchlists' }; }],
    [/^#\/settings(?:\/([a-z_-]+))?$/, function (m) { return { page: 'settings', tab: m[1] || 'general' }; }],
    [/^#\/h\/([^\/]+)\/(portfolio|accounting|activity)(?:\/([a-z-]+))?$/, function (m) { return { page: 'history', lifetime: dec(m[1]), sub: m[2], tab: m[3] || null }; }],
    [/^#\/a\/([^\/]+)\/markets(?:\/([A-Za-z0-9_-]+))?$/, function (m) { return { page: 'markets', account: dec(m[1]), tab: m[2] || null }; }],
    [/^#\/a\/([^\/]+)\/i\/(.+)$/, function (m) { return { page: 'instrument', account: dec(m[1]), instrument: dec(m[2]) }; }],
    [/^#\/a\/([^\/]+)\/(positions|orders|cash|transactions)$/, function (m) { return { page: m[2], account: dec(m[1]) }; }]
  ];
  function dec(s) { try { return decodeURIComponent(s); } catch (e) { return s; } }
  var _currentPage = null;
  T.router = {
    current: null,
    parse: function (hash) {
      hash = hash || '#/news';
      for (var i = 0; i < ROUTES.length; i++) { var m = ROUTES[i][0].exec(hash); if (m) { var r = ROUTES[i][1](m); r.hash = hash; return r; } }
      return { page: 'news', hash: '#/news', unknown: hash };
    },
    go: function (hash) { if (location.hash === hash) { T.router.render(); } else { location.hash = hash; } },
    link: function (parts) { return '#/' + parts.map(function (p) { return encodeURIComponent(p); }).join('/'); },
    account: function (id, sub) { return '#/a/' + encodeURIComponent(id) + '/' + (sub || 'markets'); },
    instrument: function (acct, inst) { return '#/a/' + encodeURIComponent(acct) + '/i/' + encodeURIComponent(inst); },
    rerender: function () { T.router.render(true); },
    render: function (force) {
      var r = T.router.parse(location.hash);
      var prev = T.router.current;
      T.router.current = r;
      if (r.unknown) { console.warn('unknown route', r.unknown); }
      if (r.account && r.account !== T.state.active_account) {
        T.state.active_account = r.account; T.store.set('active_account', r.account);
        T.state.account_detail = null; T.state.positions = null; T.state.orders = null;
        T.state.emit('active_account', r.account);
        T.poll.kick('account_detail'); T.poll.kick('positions'); T.poll.kick('orders');
      }
      T.state.quote_ids = []; T.state.chain_target = null;
      if (_currentPage && _currentPage.unmount) { try { _currentPage.unmount(); } catch (e) { console.error(e); } }
      T.poll.clearPage();
      var page = T.q('#page'), news = T.q('#news'), hist = T.q('#history');
      page.hidden = r.page === 'news' || r.page === 'history'; news.hidden = r.page !== 'news'; hist.hidden = r.page !== 'history';
      var container = r.page === 'news' ? news : (r.page === 'history' ? hist : page);
      var mod = T.pages[r.page];
      T.clear(container);
      if (r.page === 'news' && !mod) {
        T.notBuilt(container, 'pages/news.js (WP-22): Daily News', 'the attention strip above carries the same health, integrity and provider state; headlines arrive with that package');
        _currentPage = null;
      } else if (!mod) {
        T.notBuilt(container, 'pages/' + r.page + '.js');
        _currentPage = null;
      } else {
        _currentPage = mod;
        try { mod.render(container, r); } catch (e) { console.error('page', r.page, 'failed', e); container.appendChild(T.h('div', { class: 'error' }, 'page failed: ' + (e.message || e))); }
      }
      T.dock.show(!!r.account && T.store.get('dock', true));
      if (r.account) { T.q('#dock-account').textContent = r.account; }
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
    Promise.resolve(out).then(function () { p.errors = 0; }, function (e) {
      p.errors = (p.errors || 0) + 1; p.last_error = e;
      if (!(e && e.code === 'NETWORK')) { console.error('poller', p.name, e); }
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
    clearPage: function () { Object.keys(pagePollers).forEach(function (n) { var p = pagePollers[n]; p.active = false; if (p.timer) { clearTimeout(p.timer); } }); pagePollers = {}; },
    status: function () { var s = {}; Object.keys(pollers).forEach(function (n) { var p = pollers[n]; s[n] = { active: p.active, errors: p.errors, last: p.last }; }); return s; },
    paused: function () { return paused; }
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
      });
    });
    T.poll.define('accounts', 10000, function () { return T.api.get('/api/accounts').then(function (a) { T.state.set('accounts', Array.isArray(a) ? a : (a && a.accounts) || []); }); });
    T.poll.define('attention', 30000, function () { return T.api.get('/api/attention').then(function (a) { T.state.set('attention', Array.isArray(a) ? a : (a && a.rows) || []); }); });
    T.poll.define('account_detail', 10000, function () {
      var id = T.state.active_account; if (!id) { return Promise.resolve(); }
      return T.api.get('/api/accounts/' + encodeURIComponent(id)).then(function (d) { if (T.state.active_account === id) { T.state.set('account_detail', d); } });
    });
    T.poll.define('positions', 10000, function () {
      var id = T.state.active_account; if (!id) { return Promise.resolve(); }
      return T.api.get('/api/accounts/' + encodeURIComponent(id) + '/positions').then(function (p) { if (T.state.active_account === id) { T.state.set('positions', Array.isArray(p) ? p : (p && p.positions) || []); } });
    });
    T.poll.define('orders', function () { return anyOrderWorking() ? 5000 : 30000; }, function () {
      var id = T.state.active_account; if (!id) { return Promise.resolve(); }
      return T.api.get('/api/accounts/' + encodeURIComponent(id) + '/orders', { status: 'open' }).then(function (o) { if (T.state.active_account === id) { T.state.set('orders', Array.isArray(o) ? o : (o && o.orders) || []); } });
    });
    T.poll.define('quotes', function () { return T.ticket.marketWorking() ? 2000 : (anyVisibleSessionOpen() ? 5000 : 15000); }, function () {
      var ids = quoteIds(); if (!ids.length) { return Promise.resolve(); }
      return T.api.get('/api/quotes', { ids: ids.join(',') }).then(function (r) {
        if (r.server_time) { T.clock.sync(r.server_time); }
        var q = T.state.quotes || {}; var got = r.quotes || {};
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
  var KEYMAP = [
    ['/', 'symbol search (focus the page search box or the ticket instrument)'], ['?', 'this help'], ['Esc', 'close dialog / help'],
    ['g m', 'Markets of the active account'], ['g p', 'Positions'], ['g o', 'Orders'], ['g c', 'Cash & financing'], ['g x', 'Transactions'],
    ['g t', 'Treasury'], ['g f', 'Portfolio'], ['g a', 'Accounting'], ['g r', 'Risk'], ['g l', 'Activity'], ['g n', 'Daily News'], ['g w', 'Watchlists'], ['g s', 'Settings'],
    ['Alt+1 .. Alt+9', 'switch account (sidebar order)'], ['Alt+B', 'cycle the book (Personal / Fund / Institutional)'], ['t', 'toggle the ticket dock'],
    ['Up Down', 'move in a grid'], ['Enter', 'open the row'], ['b / s', 'prefill the ticket BUY / SELL from a row'], ['x', 'cancel the selected order'],
    ['Ctrl+Enter', 'submit the ticket (ticket component)']
  ];
  var pendingG = null; var pendingTimer = null;
  function isTyping(e) { var t = e.target; var tag = t && t.tagName; return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (t && t.isContentEditable); }
  T.keys = {
    map: function () { return KEYMAP; },
    grid: null,                                                /* the focused grid handle registers itself for Up/Down/Enter/b/s/x */
    handler: function (e) {
      if (e.key === 'Escape') { T.ui.help(false); T.state.emit('escape', e); return; }
      if (e.altKey && !e.ctrlKey && !e.metaKey) {
        if (e.key === 'b' || e.key === 'B') { e.preventDefault(); T.book.cycle(); return; }
        if (/^[1-9]$/.test(e.key)) {
          e.preventDefault();
          var accts = T.sidebarAccounts ? T.sidebarAccounts() : (T.state.accounts || []);
          var a = accts[Number(e.key) - 1];
          if (a) { var cur = T.router.current; T.router.go(T.router.account(a.account_id, cur && cur.account && cur.page !== 'instrument' ? cur.page : 'markets')); }
          return;
        }
      }
      if (isTyping(e) || e.ctrlKey || e.metaKey || e.altKey) { return; }
      if (e.key === '?') { e.preventDefault(); T.ui.help(); return; }
      if (e.key === '/') { e.preventDefault(); var box = T.q('[data-search]') || T.q('#ticket-instrument'); if (box) { box.focus(); box.select && box.select(); } else { T.ui.toast('no search box on this page', '', 1500); } return; }
      if (pendingG) {
        clearTimeout(pendingTimer); var g = pendingG; pendingG = null;
        var acct = T.state.active_account || T.store.get('active_account', null);
        var map = { m: 'markets', p: 'positions', o: 'orders', c: 'cash', x: 'transactions' };
        if (map[e.key]) { if (acct) { T.router.go(T.router.account(acct, map[e.key])); } else { T.ui.toast('pick an account first (Alt+1..9)', '', 1500); } return; }
        var glob = { t: '#/treasury', f: '#/portfolio', a: '#/accounting', r: '#/risk', l: '#/activity', n: '#/news', w: '#/watchlists', s: '#/settings' };
        if (glob[e.key]) { T.router.go(glob[e.key]); return; }
        void g; return;
      }
      if (e.key === 'g') { pendingG = true; pendingTimer = setTimeout(function () { pendingG = null; }, 1500); return; }
      if (e.key === 't') { T.dock.toggle(); return; }
      if (T.keys.grid && T.keys.grid.key) { if (T.keys.grid.key(e)) { e.preventDefault(); } }
    }
  };

  /* ---------------------------------------------------------------- boot ------------------------------------------ */
  T.boot = function () {
    if (T._booted) { return; } T._booted = true;
    T.theme.apply();
    document.addEventListener('keydown', T.keys.handler);
    var closeBtn = T.q('#dock-close'); if (closeBtn) { closeBtn.addEventListener('click', T.dock.toggle); }
    window.addEventListener('hashchange', function () { T.router.render(); });
    definePollers();
    ['header', 'attention', 'sidebar', 'bookswitcher'].forEach(function (n) {
      var c = T.components[n];
      if (c && c.init) { try { c.init(); } catch (e) { console.error('component', n, 'init failed', e); } }
      else { console.warn('component missing at boot:', n); }
    });
    T.api.get('/api/state', null, { nobook: true }).then(function (s) {
      T.state.conventions = s.conventions || {};
      T.state.settings = s.settings || null;
      if (s.clock && s.clock.utc) { T.clock.sync(s.clock.utc); }
      if (s.sessions) { T.state.set('sessions', s.sessions); }
      if (s.health) { T.state.set('health', s.health); }
      T.state.set('state', s);
    }, function (e) { T.api.fail(e, 'bootstrap /api/state'); }).then(function () {
      Object.keys(pollers).forEach(function (n) { T.poll.start(n); });
      if (!location.hash) { location.hash = '#/news'; }
      T.router.render();
    });
  };
})();
