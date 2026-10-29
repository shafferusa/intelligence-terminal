/* Accounts (owner directive 2026-10-27 section 16; FINSIM1_PLAN P6 task 8: UI-09, PORTFOLIO-18).  #/accounts (Shift+F4).
 * Every account of the selected book - Treasury, US Exchange, Debt, Credit, Foreign & FX, Private, each prime broker - with NAV,
 * settled cash, available, reserved, restricted (collateral received), margin (required vs posted / eligible, the Reg T excess),
 * working orders, settling, attention and custodian; a row opens the account's own pages (Treasury opens #/treasury).  The header's
 * "Open prime broker account" opens the existing onboarding flow (T.components.sidebar.openPbModal); nothing is ever created by
 * itself.  Below: the open margin calls and the "positions by custodian" statement.
 *
 * Reads: GET /api/collateral/accounts?book= (the /api/accounts row of each account plus the collateral module's figures and the
 * custody statement) - only when the collateral module is bound (T.hasModule('collateral'); rule 5: no request otherwise).  Without
 * it the page lists the sidebar's account rows (NAV, available, reserved, working, settling, attention) and says that restricted,
 * margin and custodian are unavailable in this build.  Every figure is the server's; this file formats and routes.  Polled every
 * 15 s.  ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  var CSS = '.acctp .sub{display:block;font-size:10px;line-height:1.25;color:var(--fg3);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
            '.acctp .line{font-family:var(--mono);font-size:11.5px;color:var(--fg2);margin:4px 0;line-height:1.5}.acctp .line b{color:var(--fg)}' +
            '.acctp .cust h3{font-size:11px;margin:10px 0 4px;color:var(--fg2);font-weight:600}.acctp .acct-grid th{white-space:normal;max-width:96px}' +
            '.acctp .cmain{display:block;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}';
  function ensureCss() { if (document.getElementById('accounts-css')) { return; } var s = document.createElement('style'); s.id = 'accounts-css'; s.textContent = CSS; document.head.appendChild(s); }
  function roleOf(a) { var s = String((a && a.account_id) || ''); var i = s.indexOf(':'); return a && a.kind === 'PB' ? 'PB' : (i >= 0 ? s.slice(i + 1) : s); }
  function hrefOf(a) { return roleOf(a) === 'TREASURY' || (a && a.kind === 'TREASURY') ? '#/treasury' : T.router.account(a.account_id, 'markets'); }
  function kindText(a) { return a.kind_label || (a.kind === 'TREASURY' ? 'Treasury' : (a.kind === 'PB' ? 'prime broker' : String(a.role || '').replace(/_/g, ' ').toLowerCase())); }
  function cell(main, sub, title) { return T.esc(main) + (sub ? '<span class="sub" title="' + T.esc(title || sub) + '">' + T.esc(sub) + '</span>' : ''); }
  function moneyOr(row, name, why) { return row && (row[name + '_c'] != null || row[name] != null) ? T.money(row, name) : (why || 'n/a'); }

  function render(el) {
    ensureCss();
    var dead = false, data = null, S = {};
    var book = T.book.current();
    var pbBtn = T.h('button', { class: 'btn primary', id: 'accounts-open-pb', title: 'record the ISDA Master + CSA + Prime Brokerage Agreement with a dealer and open the account (the onboarding flow)' },
      'Open prime broker account');
    pbBtn.addEventListener('click', function () {
      var b = T.book.current(); var e = (b && b.eligibility) || {};
      var sb = T.components && T.components.sidebar;
      if (!sb || typeof sb.openPbModal !== 'function') { T.ui.toast('the onboarding flow is not available in this build', '', 5000); return; }
      if (!e.can_onboard) { T.ui.toast((e.pb_otc_reason || 'a new prime-broker relationship needs ECP status and the PB / OTC threshold'), '', 6000); return; }
      sb.openPbModal(b);
    });
    T.pageHead(el, 'Accounts', book ? (book.name || book.book_code) : '', [pbBtn,
      T.h('a', { class: 'btn', href: '#/settings/accounts', title: 'sweep switch, buffer, PB financing and limit per account' }, 'Settings: accounts'),
      T.h('a', { class: 'btn', href: '#/settings/pb', title: 'the prime brokers: agreements and editable fee / margin schedules' }, 'Prime brokers')]);
    var root = T.h('div', { class: 'acctp' }); el.appendChild(root);
    var bound = T.hasModule && T.hasModule('collateral');

    function gridIn(parent, spec) { var g = T.h('div'); parent.appendChild(g); return T.components.grid.mount(g, Object.assign({ tools: false, rows: [], empty: 'loading ...' }, spec)); }
    function build() {
      var p = T.h('div', { class: 'panel' });
      S.head = T.h('small'); p.appendChild(T.h('h2', null, 'accounts of the book', S.head));
      var cols = [
        { key: 'name', label: 'account', cls: 'l', html: function (r) { return cell(r.name || r.account_id, kindText(r) + (r.broker && r.kind === 'PB' ? '' : ''), r.account_id); }, text: function (r) { return r.name || r.account_id; } },
        { key: 'nav', label: 'NAV', html: function (r) { return T.esc(moneyOr(r, 'nav')); }, text: function (r) { return moneyOr(r, 'nav'); } },
        { key: 'settled', label: 'settled cash', text: function (r) { return bound ? moneyOr(r, 'settled_usd', 'n/a') : 'n/a'; }, title: function () { return 'settled USD cash (GL 1010)'; } },
        { key: 'available', label: 'available', html: function (r) { var l = T.fmt.liquidity ? T.fmt.liquidity(r, '_usd') : { available: moneyOr(r, 'available_usd'), extra: '' }; return cell(l.available, l.extra, l.title); }, text: function (r) { return moneyOr(r, 'available_usd'); } },
        { key: 'reserved', label: 'reserved', text: function (r) { return moneyOr(r, 'reserved_usd'); }, title: function () { return 'held for working orders (and the short-option margin)'; } },
        { key: 'restricted', label: 'restricted', text: function (r) { return bound ? moneyOr(r, 'restricted') : 'unavailable'; }, title: function () { return 'the counterparties\' collateral inside settled cash (net CSA variation margin received + cash collateral for shares lent): not available, never swept'; } },
        { key: 'margin', label: 'margin req / excess', html: function (r) {
          if (!bound) { return 'unavailable'; }
          var req = T.num(r, 'margin_required'); var exc = T.num(r, 'margin_excess');
          if (!req && !exc) { return '<span class="muted">none</span>'; }
          return cell(moneyOr(r, 'margin_required'), (exc != null && exc < 0 ? 'deficit ' : 'excess ') + moneyOr(r, 'margin_excess') + (T.num(r, 'margin_reserved') ? ' . reserved ' + T.money(r, 'margin_reserved') : ''),
                      'required vs posted / eligible: futures initial margin, the short-option reservation, OTC initial margin, Reg T (the longs\' loan value + free cash)');
        }, text: function (r) { return bound ? moneyOr(r, 'margin_required') : 'unavailable'; } },
        { key: 'working_orders', label: 'working', text: function (r) { return r.working_orders == null ? 'n/a' : String(r.working_orders); } },
        { key: 'pending_settlements', label: 'settling', text: function (r) { return r.pending_settlements == null ? 'n/a' : String(r.pending_settlements); } },
        { key: 'attention', label: 'attention', html: function (r) { var n = Number(r.attention_count || 0) + Number(r.open_calls || 0); return n ? '<span class="neg">' + T.esc(String(r.attention_count || 0)) + (r.open_calls ? ' . ' + r.open_calls + ' call' + (r.open_calls === 1 ? '' : 's') : '') + '</span>' : '0'; },
          text: function (r) { return String(r.attention_count || 0); } },
        { key: 'custodian', label: 'custodian', cls: 'l', html: function (r) {
          var c = r.custodian || null;
          if (!c) { return bound ? 'n/a' : 'unavailable'; }
          return '<span class="cmain" title="' + T.esc(c.label || c.custodian) + '">' + T.esc(c.custodian) + '</span><span class="sub" title="' + T.esc(c.basis || '') + '">' + T.esc(c.basis || '') + '</span>';
        }, text: function (r) { return r.custodian ? r.custodian.custodian : ''; } }
      ];
      S.grid = gridIn(p, { columns: cols, rowKey: function (r) { return r.account_id; }, empty: 'no accounts', onEnter: function (r) { T.router.go(hrefOf(r)); },
        onClick: function (r, e) { if (e && e.detail >= 1) { T.router.go(hrefOf(r)); } } });
      S.note = T.h('div', { class: 'line' }); p.appendChild(S.note);
      root.appendChild(p);
      var cp = T.h('div', { class: 'panel', id: 'accounts-calls' }, T.h('h2', null, 'margin calls to act on', T.h('small', null, 'open, or unmet at their due time and not covered yet; positions are never sold for you')));
      S.calls = gridIn(cp, { rowKey: function (r) { return r.call_id; }, empty: 'no margin call to act on', columns: [
        { key: 'call_id', label: 'call', cls: 'l', html: function (r) { return cell(r.kind_label || '', r.account_name || r.account_id); }, text: function (r) { return r.kind_label || ''; } },
        { key: 'counterparty', label: 'counterparty', cls: 'l' },
        { key: 'amount', label: 'amount', text: function (r) { return T.money(r, 'amount'); } },
        { key: 'due', label: 'due', cls: 'l', text: function (r) { return r.due || ''; } },
        { key: 'status', label: 'status', cls: 'l', html: function (r) { return T.stageChip(r.status === 'MET' ? 'filled' : (r.status === 'OPEN' ? 'held' : 'failed'), String(r.status || '').toLowerCase(), r.status_label).outerHTML; }, text: function (r) { return r.status || ''; } }] });
      cp.appendChild(T.h('div', { class: 'line' }, 'every call, met or not, with its funding transfers: ', T.h('a', { href: '#/cash' }, 'Cash & Collateral'), '.'));
      root.appendChild(cp);
      var sp = T.h('div', { class: 'panel cust', id: 'accounts-custody' }, T.h('h2', null, 'positions by custodian', T.h('small', null, 'who holds what (the simulator\'s custody map, assumed)')));
      S.cust = T.h('div'); sp.appendChild(S.cust);
      root.appendChild(sp);
    }
    function custody(c) {
      T.clear(S.cust);
      if (!c) { S.cust.appendChild(T.h('div', { class: 'empty' }, bound ? 'loading ...' : 'unavailable: the custody statement comes with the collateral module, which is not in this build')); return; }
      var groups = c.groups || [];
      if (!groups.length) { S.cust.appendChild(T.h('div', { class: 'empty' }, 'no positions and no cash')); return; }
      groups.forEach(function (g) {
        S.cust.appendChild(T.h('h3', null, g.custodian + ' . ' + (g.rows || []).length + ' line' + ((g.rows || []).length === 1 ? '' : 's') + ' . ' + T.money(g, 'mv') + (g.mv_missing ? ' (' + g.mv_missing + ' without a price)' : '')));
        var holder = T.h('div'); S.cust.appendChild(holder);
        T.components.grid.mount(holder, { tools: false, rows: g.rows || [], empty: 'none', rowKey: function (r) { return r.account_id + ':' + r.instrument_id; }, columns: [
          { key: 'account_name', label: 'account', cls: 'l' },
          { key: 'symbol', label: 'position', cls: 'l', html: function (r) { return cell(r.symbol || r.instrument_id, r.kind === 'CASH' ? 'cash' : String(r.kind || '').toLowerCase(), r.name || ''); }, text: function (r) { return r.symbol || r.instrument_id; } },
          { key: 'qty', label: 'qty / notional', text: function (r) { return r.qty != null ? T.fmt.qty(r.qty) : (r.notional_c != null || r.notional != null ? T.money(r, 'notional') : ''); } },
          { key: 'mv', label: 'market value', html: function (r) { return cell(moneyOr(r, 'mv'), r.price_basis || ''); }, text: function (r) { return moneyOr(r, 'mv'); } }] });
      });
      if (c.note) { S.cust.appendChild(T.h('div', { class: 'hint' }, c.note)); }
    }
    function paint() {
      var rows = data ? (data.rows || []) : (T.state.accounts || []).filter(function (a) { var b = T.book.current(); return !b || !b.accounts || b.accounts.indexOf(a.account_id) >= 0; });
      S.grid.update(rows, { empty: 'no accounts in this book' });
      S.head.textContent = data && data.as_of ? 'as of ' + T.fmt.time(data.as_of) : (bound ? '' : 'the sidebar\'s figures');
      T.clear(S.note);
      if (!bound) {
        S.note.appendChild(T.h('span', null, 'restricted cash, margin and custodians are unavailable: they come with the collateral module, which is not in this build'));
      } else if (data && data.note) {
        S.note.appendChild(T.h('span', null, data.note));
      }
      S.calls.update(data ? (data.open_calls || []) : [], { empty: bound ? 'no margin call to act on' : 'unavailable in this build' });
      custody(data ? data.custody : null);
    }
    function load() {
      if (dead) { return Promise.resolve(); }
      if (!bound) { paint(); return Promise.resolve(); }
      return T.api.get('/api/collateral/accounts').then(function (d) { if (dead) { return; } data = d || null; paint(); }, function (e) {
        if (dead) { return; }
        if (!data) { paint(); T.clear(S.note); S.note.appendChild(T.h('span', { class: 'neg' }, T.api.fail(e, 'accounts'))); }
        throw e;
      });
    }
    build();
    paint();
    T.poll.page('accounts', 15000, load);
    T.pages.accounts.unmount = function () { dead = true; T.pages.accounts.unmount = null; };
  }
  T.pages.accounts = { render: render, unmount: null };
})();
