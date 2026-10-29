/* Sidebar (owner directive 2026-10-27 section 16; FINSIM1_PLAN P1 task 3, UI-06; TERMINAL_DESIGN.md s17.1 / s17.2).  Below the book
 * switcher, the directive's navigation in its order, each row with the key in force of its action (keys.js registry; nothing when the
 * action has no key):
 *   Dashboard (F12) -> Portfolio (F7) -> TRADING - the group title opens #/trading (the last venue used, else US Exchange) - with the
 *   selected book's venue accounts: US Exchange (F1) -> Debt Instruments (F2) -> Credit Instruments (F3) -> Foreign Markets & FX (F4)
 *   -> Private Markets (F5; when the lifetime has it, otherwise a dimmed row with ONE short reason, the full sentence on hover) -> each
 *   prime broker (F6), each with its NAV / avail / working / settling line, then Watchlists (Shift+F9) -> Pending Trades (F8) ->
 *   Completed Trades (Shift+F8) -> Accounts (Shift+F4), with Treasury (F10) inside and its NAV and cash -> Cash & Collateral
 *   (Shift+F5) -> Derivatives (Shift+F6) -> History (Shift+F10; lit on the hub #/history and on an archived lifetime's pages #/h/...;
 *   Settings no longer lights there, reversing SHELL-23) -> Settings (Shift+F11) -> Book views: Risk (F11), Accounting (Shift+F7).
 * Badges: Pending Trades = the book's working + partially filled + held orders, Completed Trades = its settling executions, from GET
 *   /api/dashboard/counts?book= (the trade blotters' counters), asked only while the dashboard module is bound (T.hasModule); without
 *   those counters there is no badge (never a guessed count).  An account row's red badge = its open attention items.
 * Transitions while a page of the new navigation is not in the build: "+ Open prime broker account" moved to the Accounts page header
 *   (FINSIM1_PLAN P6; T.components.sidebar.openPbModal stays exported for it) - the sidebar shows it only while the Accounts page is
 *   not in the build; Activity is a tab of the History hub - it has its own row (Book views) only while the hub is not in the build.
 * Account rows show NAV / available, working orders and attention counts from GET /api/accounts?book= (the server's figures; nothing
 * is computed here).  "avail" is the account's own spendable cash floored at zero; an account that holds other currencies shows each
 * one ('avail USD 0.00 . EUR 40,000.00', available_by_ccy; BM-30); when working orders reserve more than the account holds, a second
 * line says "reserved R (Treasury sweep covers N)" -- the sweep moves exactly that at the fill -- and UNFUNDED / overdrawn amounts
 * are red (T.fmt.liquidity; the definition is the hover text).  Only the selected book's accounts are listed, even while an answer for
 * the previous book is still in flight.  Account 1 .. 9 (Settings > Keyboard) follow the venue order (T.sidebarAccounts).
 * The onboarding modal (s13.10; SHELL-16 / PO-15) lists every dealer of /api/state.prime_brokers with its seed fee schedule side by
 * side (modelled assumptions, editable after opening in Settings > Prime brokers), marks the dealers already open, posts
 * /api/books/{code}/onboard {broker} and says 'already open' when the server answers created: false.
 */
(function () {
  'use strict';
  var T = window.T;
  var ROLE_ORDER = { US_EXCHANGE: 10, DEBT: 20, CREDIT: 30, FOREIGN_FX: 40, PRIVATE: 50 };
  var PRIME_BROKERS = { GS: 'Goldman Sachs', JPM: 'JPMorgan', MS: 'Morgan Stanley', DB: 'Deutsche Bank', BOFA: 'BofA Securities', BARC: 'Barclays', CITI: 'Citi', UBS: 'UBS', BNP: 'BNP Paribas' };
  function roleOf(a) { var i = String(a.account_id || '').indexOf(':'); return i >= 0 ? a.account_id.slice(i + 1) : (a.role || ''); }
  /* the selected book's open-lifetime account ids (GET /api/books .accounts), or null when unknown */
  function bookIds() { var b = T.book.current(); return b && Array.isArray(b.accounts) ? b.accounts : null; }
  function inBook(a) { var ids = bookIds(); return !ids || ids.indexOf(a.account_id) >= 0; }
  function tradingAccounts() {
    var accts = (T.state.accounts || []).filter(function (a) { return a.kind !== 'TREASURY' && roleOf(a) !== 'TREASURY' && a.status !== 'CLOSED' && inBook(a); });
    accts.sort(function (a, b) {
      var ra = roleOf(a), rb = roleOf(b);
      var oa = ROLE_ORDER[ra] != null ? ROLE_ORDER[ra] : (a.sort_order != null ? a.sort_order : 100), ob = ROLE_ORDER[rb] != null ? ROLE_ORDER[rb] : (b.sort_order != null ? b.sort_order : 100);
      return oa - ob || String(a.name).localeCompare(String(b.name));
    });
    return accts;
  }
  T.sidebarAccounts = tradingAccounts;
  function treasuryRow() { return (T.state.accounts || []).filter(function (a) { return (a.kind === 'TREASURY' || roleOf(a) === 'TREASURY') && inBook(a); })[0] || null; }
  /* the key in force of a sidebar action (keys.js registry; '' without a key) */
  function keyOf(id) { return T.keys && T.keys.label ? T.keys.label(id) : ''; }
  var ROLE_KEY = { US_EXCHANGE: 'nav.us_exchange', DEBT: 'nav.debt', CREDIT: 'nav.credit', FOREIGN_FX: 'nav.foreign_fx', PRIVATE: 'nav.private' };
  /* an account row: its role's key (F1 .. F6 by default), else 'Account N' of the venue order when the owner gave it one */
  function accountKey(role, i) { var id = ROLE_KEY[role] || (/^PB_/.test(role) ? 'nav.prime_broker' : null); var k = id ? keyOf(id) : ''; return k || (i < 9 ? keyOf('nav.account_' + (i + 1)) : ''); }
  function item(href, label, opts) {
    opts = opts || {};
    var r = T.router.current;
    var active = r && (opts.isActive ? opts.isActive(r) : (r.hash === href || (opts.prefix && r.hash.indexOf(opts.prefix) === 0)));
    var a = T.h('a', { href: href, class: 'nav-item' + (active ? ' active' : '') + (opts.dim ? ' dim' : '') + (opts.sub ? ' sub' : ''), title: opts.title || label }, label);
    if (opts.key) { a.appendChild(T.h('span', { class: 'k' }, opts.key)); }
    if (opts.badge) { a.appendChild(T.h('span', { class: 'nav-badge' + (opts.badgeCls ? ' ' + opts.badgeCls : ''), title: opts.badgeTitle || '' }, String(opts.badge))); }
    if (opts.dim) { a.addEventListener('click', function (e) { e.preventDefault(); if (opts.why) { T.ui.toast(opts.why, '', 5000); } }); }
    return a;
  }
  /* "avail": USD alone, or each currency the account can spend (BM-30; C24 available_by_ccy, major units) */
  function availText(a, liq) {
    var by = a.available_by_ccy;
    if (by && typeof by === 'object') {
      var other = Object.keys(by).filter(function (c) { return c !== 'USD' && Number(by[c]) !== 0 && isFinite(Number(by[c])); }).sort();
      if (other.length) { return 'USD ' + T.fmt.money(by.USD != null ? by.USD : '0.00') + ' . ' + other.map(function (c) { return c + ' ' + T.fmt.money(by[c]); }).join(' . '); }
    }
    return liq.available;
  }
  /* a figures line under a row: the parts wrap between each other, never inside a figure (no figure is cut off) */
  function subLine(parts, title, cls) {
    var el = T.h('span', { class: 'nav-sub wrap' + (cls ? ' ' + cls : ''), title: title });
    parts.forEach(function (p, i) { if (i) { el.appendChild(document.createTextNode(' . ')); } el.appendChild(T.h('span', { class: 'nsp' }, p)); });
    return el;
  }
  /* the short reason of a dimmed row (the full sentence is the hover and the click toast) */
  function shortPb(e) {
    var thr = e.threshold != null ? T.fmt.compact(e.threshold) : null;
    if (e.has_relationships) { return 'new relationships need NAV >= ' + (thr || 'the PB/OTC threshold'); }
    return 'unlocks at ' + (thr ? thr + ' NAV' : 'the PB/OTC threshold');
  }
  function shortPrivate(e) { return 'unlocks at ' + (e.qp_threshold != null ? T.fmt.compact(e.qp_threshold) + ' NAV' : 'the qualified-purchaser threshold'); }
  /* the TRADING group title: a section heading that opens #/trading (the last venue used) */
  function tradingTitle() {
    var r = T.router.current;
    var k = keyOf('page.trading');
    var a = T.h('a', { href: '#/trading', class: 'nav-section nav-group-link' + (r && r.page === 'trading' ? ' active' : ''), title: 'Trading: opens the last venue you used (else US Exchange)' + (k ? ' (' + k + ')' : '') }, 'Trading');
    if (k) { a.appendChild(T.h('span', { class: 'k' }, k)); }
    return a;
  }
  /* the trade blotters' counters for the badges (GET /api/dashboard/counts): {pending_badge, completed_badge, counts} or null */
  var navCounts = null;
  function badges() {
    var c = navCounts;
    if (!c || !c.present) { return { pending: 0, completed: 0, pendingTitle: '', completedTitle: '' }; }
    var n = c.counts || {};
    function v(k) { return Number(n[k]) || 0; }
    return { pending: Number(c.pending_badge) || 0, completed: Number(c.completed_badge) || 0,
             pendingTitle: 'working ' + v('working') + ' . partially filled ' + v('partial') + ' . held ' + v('held') + (v('queued') ? ' . queued for the next session ' + v('queued') : '') + (v('rfq_open') ? ' . dealer quotes open ' + v('rfq_open') : ''),
             completedTitle: 'settling ' + v('settling') + (v('failed') ? ' . failed ' + v('failed') : '') };
  }
  function historyHubBuilt() { var h = T.pages.history && T.pages.history.hub; return !!(h && (typeof h === 'function' || typeof h.render === 'function')); }
  function render() {
    var nav = T.q('#nav'); if (!nav) { return; }
    T.clear(nav);
    var book = T.book.current(); var e = (book && book.eligibility) || {};
    var accts = tradingAccounts();
    var b = badges();
    nav.appendChild(item('#/dashboard', 'Dashboard', { key: keyOf('page.dashboard') }));
    nav.appendChild(item('#/portfolio', 'Portfolio', { key: keyOf('page.portfolio'), prefix: '#/portfolio' }));
    /* TRADING: the venues (each opens its Markets page with the docked ticket) and Watchlists */
    nav.appendChild(tradingTitle());
    accts.forEach(function (a, i) {
      var role = roleOf(a);
      var href = T.router.account(a.account_id, 'markets');
      var row = item(href, a.name || role, { key: accountKey(role, i) || null, prefix: '#/a/' + encodeURIComponent(a.account_id) + '/', badge: a.attention_count || 0, badgeTitle: 'open attention items', title: a.account_id + (a.broker ? ' . ' + a.broker : '') });
      nav.appendChild(row);
      var liq = T.fmt.liquidity(a, '_usd');
      var avail = availText(a, liq);
      var parts = ['NAV ' + T.money(a, 'nav')].concat(('avail ' + avail).split(' . '));
      if (a.working_orders) { parts.push(a.working_orders + ' working'); }
      if (a.pending_settlements) { parts.push(a.pending_settlements + ' settling'); }
      var title = 'NAV and available cash (server figures)' + (avail !== liq.available ? ' by currency' + (a.available_base != null ? '; all currencies in USD at the current FX: ' + T.fmt.money(a.available_base) : '') : '') + '\n' + liq.title;
      nav.appendChild(subLine(parts, title));
      if (liq.extra) { nav.appendChild(T.h('span', { class: 'nav-sub', style: liq.warn ? 'color: var(--down)' : null, title: liq.title }, liq.extra)); }   /* inline: .nav-sub's own color outranks .neg */
    });
    if (!accts.length) { nav.appendChild(T.h('span', { class: 'nav-why' }, T.state.server_ok === false ? 'server unreachable' : 'loading accounts ...')); }
    var hasPrivate = accts.some(function (a) { return roleOf(a) === 'PRIVATE'; });
    if (!hasPrivate) {
      var pfull = (e.private_reason || 'Private Markets unlock when the lifetime reaches the qualified-purchaser threshold') + (T.conv('QP') ? ' - ' + T.conv('QP') : '');
      nav.appendChild(item('#', 'Private Markets', { dim: true, why: pfull, title: pfull }));
      nav.appendChild(T.h('span', { class: 'nav-why', title: pfull }, shortPrivate(e)));
    }
    /* PO-13: the server's can_onboard alone (relationships that exist stay usable; new ones need the threshold); the button lives on
       the Accounts page header in 1.0.6 - here only while that page is not in the build */
    if (!T.pages.accounts) {
      var canOnboard = !!e.can_onboard;
      var pbFull = (e.pb_otc_reason || 'needs ECP status and the PB/OTC threshold') + (T.conv('ELIGIBILITY') ? ' - ' + T.conv('ELIGIBILITY') : '');
      var pbRow = T.h('a', { href: '#', class: 'nav-item' + (canOnboard ? '' : ' dim'), title: canOnboard ? 'record the ISDA Master + CSA + Prime Brokerage Agreement with a dealer and open the account' : pbFull }, '+ Open prime broker account');
      pbRow.addEventListener('click', function (ev) { ev.preventDefault(); if (canOnboard) { openPbModal(book); } else { T.ui.toast(pbFull, '', 6000); } });
      nav.appendChild(pbRow);
      if (!canOnboard) { nav.appendChild(T.h('span', { class: 'nav-why', title: pbFull }, shortPb(e))); }
    }
    nav.appendChild(item('#/watchlists', 'Watchlists', { key: keyOf('page.watchlists') }));
    /* the trade blotters, the accounts, cash & collateral, derivatives, history, settings */
    nav.appendChild(T.h('div', { class: 'nav-section' }, 'Fund'));
    nav.appendChild(item('#/pending', 'Pending Trades', { key: keyOf('page.pending'), prefix: '#/pending', badge: b.pending, badgeCls: 'count', badgeTitle: b.pendingTitle,
      title: 'Pending Trades: working, queued, partially filled and held orders of the book' }));
    nav.appendChild(item('#/completed', 'Completed Trades', { key: keyOf('page.completed'), prefix: '#/completed', badge: b.completed, badgeCls: 'count', badgeTitle: b.completedTitle,
      title: 'Completed Trades: every execution of the book with its settlement status' }));
    nav.appendChild(item('#/accounts', 'Accounts', { key: keyOf('page.accounts'), title: 'Accounts: every account of the book (Treasury, the venues, each prime broker)' }));
    nav.appendChild(item('#/treasury', 'Treasury', { key: keyOf('page.treasury'), sub: true }));
    var tre = treasuryRow();
    if (tre) {
      var tliq = T.fmt.liquidity(tre, '_usd');
      nav.appendChild(subLine(['NAV ' + T.money(tre, 'nav')].concat(('cash ' + availText(tre, tliq)).split(' . ')),
        'Treasury NAV and its own spendable cash (server figures); the sweep draws on it down to the Treasury reserve shown on the Treasury page\n' + tliq.title, 'sub'));
    }
    nav.appendChild(item('#/cash', 'Cash & Collateral', { key: keyOf('page.cash_collateral'), prefix: '#/cash' }));
    nav.appendChild(item('#/derivatives', 'Derivatives', { key: keyOf('page.derivatives'), prefix: '#/derivatives' }));
    nav.appendChild(item('#/history', 'History', { key: keyOf('page.history'), isActive: function (r) { return r.page === 'history'; },
      title: 'History: NAV history, transactions, account activity and past lifetimes' }));
    nav.appendChild(item('#/settings', 'Settings', { key: keyOf('page.settings'), isActive: function (r) { return r.page === 'settings'; } }));
    nav.appendChild(T.h('div', { class: 'nav-section' }, 'Book views'));
    nav.appendChild(item('#/risk', 'Risk', { key: keyOf('page.risk'), prefix: '#/risk' }));
    nav.appendChild(item('#/accounting', 'Accounting', { key: keyOf('page.accounting'), prefix: '#/accounting' }));
    if (!historyHubBuilt()) { nav.appendChild(item('#/activity', 'Activity', { key: keyOf('page.activity'), prefix: '#/activity' })); }
  }
  /* PO-15: the dealers (C10: [{broker, name, schedule, agreements}]); the static names when the server sends none */
  function dealers() {
    var st = T.state.state || {};
    var list = st.prime_brokers;
    if (Array.isArray(list) && list.length) { return list.map(function (d) { return { broker: d.broker, name: d.name || PRIME_BROKERS[d.broker] || d.broker, schedule: d.schedule || null, agreements: d.agreements || null }; }); }
    var sch = st.pb_schedules || {};
    if (list && typeof list === 'object' && !Array.isArray(list)) { return Object.keys(list).map(function (k) { var v = list[k]; return typeof v === 'string' ? { broker: k, name: v, schedule: sch[k] || null } : { broker: k, name: v.name || k, schedule: v.schedule || sch[k] || null }; }); }
    return Object.keys(PRIME_BROKERS).map(function (k) { return { broker: k, name: PRIME_BROKERS[k], schedule: sch[k] || null }; });
  }
  /* the dealers this book already has an account with: broker -> account row */
  function openDealers() {
    var out = {};
    (T.state.accounts || []).forEach(function (a) { if (a.kind === 'PB' && a.status !== 'CLOSED' && inBook(a)) { out[a.broker || roleOf(a).replace(/^PB_/, '')] = a; } });
    return out;
  }
  /* one schedule cell (s12.4 units: *_spread / *_rate / htb_add fractions shown as a percent, *_bp basis points, money in USD) */
  var SCHED_COLS = [
    ['equity', 'equities: USD per share (minimum per fill)', function (s) { return T.fmt.px(s.equity_per_share) + ' (min ' + T.money(s, 'equity_min') + ')'; }],
    ['options', 'USD per option contract', function (s) { return T.fmt.px(s.option_per_contract); }],
    ['futures', 'USD per futures contract per side', function (s) { return T.fmt.px(s.futures_per_side); }],
    ['financing', 'debit balances: SOFR + this spread', function (s) { return 'SOFR + ' + T.fmt.rate(s.financing_spread); }],
    ['GC / HTB', 'stock borrow: general-collateral fee / hard-to-borrow add-on', function (s) { return T.fmt.rate(s.gc_rate) + ' / +' + T.fmt.rate(s.htb_add); }],
    ['OTC', 'IRS dealing half-width / TRS spread over SOFR', function (s) { return T.fmt.num(s.irs_half_width_bp, 1) + ' bp / ' + T.fmt.rate(s.trs_spread); }],
    ['monthly min', 'monthly minimum commission', function (s) { var v = T.num(s, 'monthly_minimum'); return v ? 'USD ' + T.money(s, 'monthly_minimum') : 'none'; }]
  ];
  function openPbModal(book) {
    if (!book) { return; }
    var list = dealers(); var open = openDealers();
    var hasSched = list.some(function (d) { return d.schedule; });
    var first = list.filter(function (d) { return !open[d.broker]; })[0] || list[0];
    var picked = first ? first.broker : null;
    var sel = T.h('select', { id: 'pb-broker', 'aria-label': 'dealer' });
    list.forEach(function (d) { sel.appendChild(T.h('option', { value: d.broker, selected: d.broker === picked }, d.name + (open[d.broker] ? ' - already open' : ''))); });
    var table = T.h('table', { class: 'pb-sched' });
    var status = T.h('div', { class: 'hint', id: 'pb-status' });
    var openBtn = null;
    function paint() {
      T.clear(table);
      var head = T.h('tr', null, T.h('th', { class: 'l' }, 'dealer'));
      SCHED_COLS.forEach(function (c) { head.appendChild(T.h('th', { title: c[1] }, c[0])); });
      table.appendChild(T.h('thead', null, head));
      var tb = T.h('tbody');
      list.forEach(function (d) {
        var tr = T.h('tr', { class: 'click' + (d.broker === picked ? ' sel' : '') + (open[d.broker] ? ' is-open' : ''), title: open[d.broker] ? d.name + ': already open (' + (open[d.broker].name || open[d.broker].account_id) + ')' : 'pick ' + d.name },
          T.h('td', { class: 'l' }, d.name, open[d.broker] ? T.h('span', { class: 'state-tag', style: 'margin-left:6px' }, 'OPEN') : null));
        SCHED_COLS.forEach(function (c) { tr.appendChild(T.h('td', null, d.schedule ? c[2](d.schedule) : 'n/a')); });
        tr.addEventListener('click', function () { picked = d.broker; sel.value = d.broker; paint(); });
        tb.appendChild(tr);
      });
      table.appendChild(tb);
      var o = open[picked];
      T.clear(status);
      if (o) { status.appendChild(T.h('span', null, (o.name || o.account_id) + ' is already open in this lifetime: ')); status.appendChild(T.h('a', { href: T.router.account(o.account_id, 'markets') }, 'go to it')); }
      if (openBtn) { openBtn.title = o ? 'already open - nothing new would be signed' : 'record the three agreements and open the account'; }
    }
    sel.addEventListener('change', function () { picked = sel.value; paint(); });
    var body = T.h('div', null,
      T.h('div', { class: 'form' }, T.h('label', { for: 'pb-broker' }, 'dealer'), sel),
      T.h('div', { class: 'hint' }, 'Opening records three agreements with this dealer, dated today: the ISDA Master Agreement (the OTC contract framework), the Credit Support Annex (collateral and variation-margin terms) and the Prime Brokerage Agreement (financing, custody and fees). Repo is documented under a GMRA and securities lending under an MSLA / GMSLA.' +
        ' PB financing starts switched off: switch it on in the Cash & financing page of the account (or Settings > Accounts) before drawing.'),
      T.h('h2', null, 'fee schedules', T.h('small', null, 'modelled assumptions, not the dealers\' real terms - editable after opening in Settings > Prime brokers')),
      hasSched ? T.h('div', { class: 'pb-sched-wrap' }, table) : T.h('div', { class: 'empty' }, 'the server sent no fee schedules; each dealer\'s schedule shows in Settings > Prime brokers once its account is open'),
      status,
      T.convNote(['ELIGIBILITY']));
    var m = T.ui.modal({
      title: 'Open a prime broker account . ' + (book.name || book.book_code), body: body, wide: true,
      actions: [{ label: 'Cancel' }, { label: 'Open', cls: 'primary', id: 'pb-open', onClick: function (h, btn) {
        if (open[picked]) { T.ui.toast('already open: ' + (open[picked].name || picked), '', 3000); return; }
        btn.disabled = true;
        T.api.post('/api/books/' + encodeURIComponent(book.book_code) + '/onboard', { broker: picked }, { nobook: true }).then(function (r) {
          h.close();
          var acc = r && r.account; var name = acc && (acc.name || acc.account_id) || picked;
          if (r && r.created === false) { T.ui.toast('already open: ' + name + ' (nothing new was signed)', '', 4000); }
          else { T.ui.toast('prime broker account opened: ' + name + ' - ISDA Master, CSA and PBA recorded', 'ok', 4000); }
          var aid = (acc && acc.account_id) || (r && r.account_id);
          /* the server confirmed the account: list it under the book now, so its page opens as an account of the open
             lifetime before the next books answer arrives */
          var cb = (T.state.books || []).filter(function (x) { return x.book_code === book.book_code; })[0];
          if (aid && cb && Array.isArray(cb.accounts) && cb.accounts.indexOf(aid) < 0) { cb.accounts.push(aid); }
          T.poll.kick('accounts'); T.poll.kick('books');
          if (aid) { T.router.go(T.router.account(aid, 'markets')); }
        }, function (e) { btn.disabled = false; T.api.fail(e, 'open prime broker account'); });
      } }]
    });
    openBtn = m.el.querySelector('#pb-open');
    paint();
    return m;
  }
  /* the badges' counters: asked every 15 s, only while the dashboard module is bound (C2: no request to a route that is not built) */
  var knownModule = null;
  function pollCounts() {
    if (!T.hasModule('dashboard')) {
      if (navCounts) { navCounts = null; render(); }
      return Promise.resolve();
    }
    var code = T.book.code;
    return T.api.get('/api/dashboard/counts').then(function (c) {
      if (code !== T.book.code) { return; }                     /* an answer for the previous book: dropped */
      var before = JSON.stringify(navCounts);
      navCounts = c && typeof c === 'object' ? c : null;
      if (JSON.stringify(navCounts) !== before) { render(); }
    });
  }
  function init() {
    ['accounts', 'books', 'book', 'route', 'state', 'server_ok', 'keybinds'].forEach(function (k) { T.state.on(k, render); });   /* keybinds: the keys shown follow Settings > Keyboard */
    T.state.on('book', function () { navCounts = null; });
    /* the module list arrives with /api/health: ask for the counters as soon as the dashboard module is known to be bound */
    T.state.on('health', function () { var m = T.hasModule('dashboard'); if (m !== knownModule) { knownModule = m; T.poll.kick('navcounts'); } });
    if (T.poll && T.poll.define) { T.poll.define('navcounts', 15000, pollCounts); }
    render();
  }
  T.components.sidebar = { init: init, render: render, openPbModal: openPbModal, PRIME_BROKERS: PRIME_BROKERS, tradingAccounts: tradingAccounts, dealers: dealers,
                           counts: function () { return navCounts; } };
})();
