/* Sidebar (TERMINAL_DESIGN.md s17.1 / s17.2, B16 order; WP-12).  Below the book switcher: Daily News -> Portfolio ->
 * Treasury -> US Exchange -> Debt Instruments -> Credit Instruments -> Foreign Markets & FX -> Private Markets (when the
 * lifetime has it; otherwise a dimmed row with the QP reason) -> each opened prime broker -> "+ Open prime broker
 * account" (dimmed with the ELIGIBILITY reason when ineligible) -> Risk -> Accounting -> Activity -> Watchlists ->
 * Settings.  No Glossary (owner amendment 18:28).  Account rows show NAV / available, working orders and attention
 * counts from GET /api/accounts?book= (the server's figures; nothing is computed here).  "avail" is the account's own
 * spendable cash floored at zero; when working orders reserve more than the account holds, a second line says
 * "reserved R (Treasury sweep covers N)" -- the sweep moves exactly that at the fill -- and UNFUNDED / overdrawn amounts
 * are red (T.fmt.liquidity; the definition is the hover text).  Alt+1..9 follow this order
 * (T.sidebarAccounts).  The onboarding modal (s13.10) posts /api/books/{code}/onboard {broker} and records the three
 * agreements; it is disabled with the reason in an ineligible lifetime.
 */
(function () {
  'use strict';
  var T = window.T;
  var ROLE_ORDER = { US_EXCHANGE: 10, DEBT: 20, CREDIT: 30, FOREIGN_FX: 40, PRIVATE: 50 };
  var PRIME_BROKERS = { GS: 'Goldman Sachs', JPM: 'JPMorgan', MS: 'Morgan Stanley', DB: 'Deutsche Bank', BOFA: 'BofA Securities', BARC: 'Barclays', CITI: 'Citi', UBS: 'UBS', BNP: 'BNP Paribas' };
  function roleOf(a) { var i = String(a.account_id || '').indexOf(':'); return i >= 0 ? a.account_id.slice(i + 1) : (a.role || ''); }
  function tradingAccounts() {
    var accts = (T.state.accounts || []).filter(function (a) { return a.kind !== 'TREASURY' && roleOf(a) !== 'TREASURY' && a.status !== 'CLOSED'; });
    accts.sort(function (a, b) {
      var ra = roleOf(a), rb = roleOf(b);
      var oa = ROLE_ORDER[ra] != null ? ROLE_ORDER[ra] : (a.sort_order != null ? a.sort_order : 100), ob = ROLE_ORDER[rb] != null ? ROLE_ORDER[rb] : (b.sort_order != null ? b.sort_order : 100);
      return oa - ob || String(a.name).localeCompare(String(b.name));
    });
    return accts;
  }
  T.sidebarAccounts = tradingAccounts;
  function item(href, label, opts) {
    opts = opts || {};
    var r = T.router.current;
    var active = r && (opts.isActive ? opts.isActive(r) : (r.hash === href || (opts.prefix && r.hash.indexOf(opts.prefix) === 0)));
    var a = T.h('a', { href: href, class: 'nav-item' + (active ? ' active' : '') + (opts.dim ? ' dim' : ''), title: opts.title || label }, label);
    if (opts.key) { a.appendChild(T.h('span', { class: 'k' }, opts.key)); }
    if (opts.badge) { a.appendChild(T.h('span', { class: 'nav-badge', title: opts.badgeTitle || '' }, String(opts.badge))); }
    if (opts.dim) { a.addEventListener('click', function (e) { e.preventDefault(); if (opts.why) { T.ui.toast(opts.why, '', 4000); } }); }
    return a;
  }
  function render() {
    var nav = T.q('#nav'); if (!nav) { return; }
    T.clear(nav);
    var book = T.book.current(); var e = (book && book.eligibility) || {};
    var accts = tradingAccounts();
    nav.appendChild(item('#/news', 'Daily News', { key: 'g n' }));
    nav.appendChild(item('#/portfolio', 'Portfolio', { key: 'g f', prefix: '#/portfolio' }));
    nav.appendChild(item('#/treasury', 'Treasury', { key: 'g t' }));
    nav.appendChild(T.h('div', { class: 'nav-section' }, 'Accounts' + (book ? ' . ' + (book.name || book.book_code) : '')));
    accts.forEach(function (a, i) {
      var role = roleOf(a);
      var href = T.router.account(a.account_id, 'markets');
      var row = item(href, a.name || role, { key: i < 9 ? 'Alt+' + (i + 1) : null, prefix: '#/a/' + encodeURIComponent(a.account_id) + '/', badge: a.attention_count || 0, badgeTitle: 'open attention items', title: a.account_id + (a.broker ? ' . ' + a.broker : '') });
      nav.appendChild(row);
      var liq = T.fmt.liquidity(a, '_usd');
      var sub = 'NAV ' + T.money(a, 'nav') + ' . avail ' + liq.available + (a.working_orders ? ' . ' + a.working_orders + ' working' : '') + (a.pending_settlements ? ' . ' + a.pending_settlements + ' settling' : '');
      nav.appendChild(T.h('span', { class: 'nav-sub', title: 'NAV and available USD (server figures)\n' + liq.title }, sub));
      if (liq.extra) { nav.appendChild(T.h('span', { class: 'nav-sub', style: liq.warn ? 'color: var(--down)' : null, title: liq.title }, liq.extra)); }   /* inline: .nav-sub's own color outranks .neg */
    });
    var hasPrivate = accts.some(function (a) { return roleOf(a) === 'PRIVATE'; });
    if (!hasPrivate) {
      nav.appendChild(item('#', 'Private Markets', { dim: true, why: (e.private_reason || 'unlocks when the lifetime reaches the qualified-purchaser threshold') + ' - ' + T.conv('QP'), title: e.private_reason || T.conv('QP') }));
      nav.appendChild(T.h('span', { class: 'nav-why' }, e.private_reason || 'unlocks at the QP threshold'));
    }
    var canOnboard = !!(e.can_onboard || e.pb_otc);
    var pbRow = T.h('a', { href: '#', class: 'nav-item' + (canOnboard ? '' : ' dim'), title: canOnboard ? 'record the ISDA Master + CSA + Prime Brokerage Agreement with a dealer and open the account' : (e.pb_otc_reason || T.conv('ELIGIBILITY')) }, '+ Open prime broker account');
    pbRow.addEventListener('click', function (ev) { ev.preventDefault(); if (canOnboard) { openPbModal(book); } else { T.ui.toast((e.pb_otc_reason || 'not eligible') + ' - ' + T.conv('ELIGIBILITY'), '', 6000); } });
    nav.appendChild(pbRow);
    if (!canOnboard) { nav.appendChild(T.h('span', { class: 'nav-why' }, e.pb_otc_reason || 'needs ECP status and the PB/OTC threshold')); }
    nav.appendChild(T.h('div', { class: 'nav-section' }, 'Book'));
    nav.appendChild(item('#/risk', 'Risk', { key: 'g r' }));
    nav.appendChild(item('#/accounting', 'Accounting', { key: 'g a', prefix: '#/accounting' }));
    nav.appendChild(item('#/activity', 'Activity', { key: 'g l' }));
    nav.appendChild(item('#/watchlists', 'Watchlists', { key: 'g w' }));
    nav.appendChild(item('#/settings', 'Settings', { key: 'g s', prefix: '#/settings' }));
  }
  function openPbModal(book) {
    if (!book) { return; }
    var st = T.state.state || {};
    var brokers = st.prime_brokers || PRIME_BROKERS;
    var schedules = st.pb_schedules || {};
    var sel = T.h('select', { id: 'pb-broker' });
    Object.keys(brokers).forEach(function (k) { sel.appendChild(T.h('option', { value: k }, brokers[k] + ' (' + k + ')')); });
    var schedBox = T.h('div', { class: 'kv', style: 'margin-top:8px' });
    function showSchedule() {
      T.clear(schedBox);
      var s = schedules[sel.value];
      if (!s) { schedBox.appendChild(T.h('div', null, 'schedule')); schedBox.appendChild(T.h('div', null, 'shown on the account page after opening (modelled assumptions)')); return; }
      Object.keys(s).forEach(function (k) { if (k === 'name') { return; } schedBox.appendChild(T.h('div', null, scheduleLabel(k))); schedBox.appendChild(T.h('div', null, scheduleValue(k, s[k]))); });
    }
    sel.addEventListener('change', showSchedule); showSchedule();
    var body = T.h('div', null,
      T.h('div', { class: 'form' }, T.h('label', { for: 'pb-broker' }, 'dealer'), sel),
      T.h('div', { class: 'hint' }, 'Opening records three agreements with this dealer, dated today: the ISDA Master Agreement (the OTC contract framework), the Credit Support Annex (collateral and variation-margin terms) and the Prime Brokerage Agreement (financing, custody and fees). GMRA (repo) and MSLA/GMSLA (securities lending) are the real-world documents for the Build 2 products.'),
      T.h('h2', null, 'fee schedule (modelled assumptions)'), schedBox,
      T.convNote(['ELIGIBILITY']));
    T.ui.modal({
      title: '+ Open prime broker account . ' + (book.name || book.book_code), body: body,
      actions: [{ label: 'Cancel' }, { label: 'Open', cls: 'primary', onClick: function (m, btn) {
        btn.disabled = true;
        T.api.post('/api/books/' + encodeURIComponent(book.book_code) + '/onboard', { broker: sel.value }).then(function (r) {
          m.close(); T.ui.toast('prime broker account opened: ' + (r.account && (r.account.name || r.account.account_id) || sel.value), 'ok');
          T.poll.kick('accounts'); T.poll.kick('books');
          if (r.account && r.account.account_id) { T.router.go(T.router.account(r.account.account_id, 'markets')); }
        }, function (e) { btn.disabled = false; T.api.fail(e, 'onboard'); });
      } }]
    });
  }
  /* A PB schedule field with its unit (s12.4; quotes/rates.py UNITS): *_spread / *_rate / htb_add are FRACTIONS (shown as
     a percent), *_bp basis points, a *_c key integer cents (shown in USD), equity_min / monthly_minimum decimal USD,
     per-share / per-contract / per-side fees USD, anything else as given. */
  function scheduleLabel(k) { return String(k).replace(/_c$/, '').replace(/_/g, ' '); }
  function scheduleValue(k, v) {
    if (v == null || v === '') { return 'n/a'; }
    if (typeof v === 'object') { return JSON.stringify(v); }
    if (/_c$/.test(k) && isFinite(Number(v))) { return 'USD ' + T.fmt.cents(Number(v)); }
    if (/(_spread|_rate)$/.test(k) || k === 'htb_add') { return T.fmt.rate(v) + ' (' + v + ')'; }
    if (/_bp$/.test(k)) { return T.fmt.num(v, 1) + ' bp'; }
    if (k === 'equity_min' || k === 'monthly_minimum') { return 'USD ' + T.fmt.money(v); }
    if (/^(equity_per_share|option_per_contract|futures_per_side)$/.test(k)) { return 'USD ' + T.fmt.px(v); }
    return String(v);
  }
  function init() {
    ['accounts', 'books', 'book', 'route', 'state'].forEach(function (k) { T.state.on(k, render); });
    render();
  }
  T.components.sidebar = { init: init, render: render, openPbModal: openPbModal, PRIME_BROKERS: PRIME_BROKERS };
})();
