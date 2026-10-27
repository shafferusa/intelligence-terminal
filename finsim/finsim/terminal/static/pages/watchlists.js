/* Watchlists (TERMINAL_DESIGN.md s17.1 #/watchlists; WP-12; Build 2 P10 SHELL-25): lists with live prices (the quotes poller
 * keeps the visible ids fresh; every price through T.pxHtml), add by symbol (GET /api/instruments/resolve, 202 spinner) ->
 * POST /api/watchlists {account_id, name, instrument_id}; remove -> DELETE /api/watchlists.
 * - A name is added only to an account that trades it (the instrument's account_kinds against the account picked): 'TLT
 *   trades in US Exchange' is said and the account box moves to the first account of the book that does; Enter on a row
 *   opens the instrument in an account that trades it (the list's own when it can, else the first one that can, said).
 * - b / s on a row open the ticket with the KIND's sides (an option BTO / STO, a held long's s = SELL, a short's b = BUY TO
 *   COVER; ORC-2), in an account that trades it; a bond, an FX pair or a private name opens its own ticket.
 * - Every input has a visible label; each list shows its account by name.
 */
(function () {
  'use strict';
  var T = window.T;
  var kindsCache = {};                                          /* instrument_id -> account_kinds (upper case), per page */
  function lookupKinds(id) {
    if (kindsCache[id]) { return Promise.resolve(kindsCache[id]); }
    return T.api.get('/api/instruments/' + encodeURIComponent(id)).then(function (d) {
      var i = (d && (d.instrument || d)) || {};
      if (i.kind) { T.rememberKind(id, i.kind); }
      kindsCache[id] = (i.account_kinds || []).map(function (k) { return String(k).toUpperCase(); });
      return kindsCache[id];
    });
  }
  function accounts() { return (T.state.accounts || []).filter(function (a) { return a && a.kind !== 'TREASURY' && a.status !== 'CLOSED'; }); }
  /* the account that trades it, in the instrument's own order of preference (US Exchange before a prime broker) */
  function accountFor(kinds) {
    var l = accounts();
    for (var k = 0; k < kinds.length; k++) { for (var i = 0; i < l.length; i++) { if (T.accountKind(l[i]) === kinds[k]) { return l[i].account_id; } } }
    return null;
  }
  function kindsText(kinds) {
    var names = { US_EXCHANGE: 'US Exchange', DEBT: 'Debt Instruments', CREDIT: 'Credit Instruments', FOREIGN_FX: 'Foreign Markets & FX', PRIVATE: 'Private Markets', PB: 'a prime-broker account' };
    return kinds.length ? kinds.map(function (k) { return names[k] || k; }).join(' or ') : 'no account (reference only)';
  }
  function render(el) {
    var lists = []; var grids = {};
    var name = T.h('input', { type: 'text', id: 'wl-name', placeholder: 'default', style: 'width:140px' });
    var acctSel = T.h('select', { id: 'wl-account' });
    function fillAccts() {
      var keep = acctSel.value;
      T.clear(acctSel);
      accounts().forEach(function (a) { acctSel.appendChild(T.h('option', { value: a.account_id }, a.name || T.accountName(a.account_id))); });
      if (keep && accounts().some(function (a) { return a.account_id === keep; })) { acctSel.value = keep; }
    }
    fillAccts();
    var sym = T.h('input', { type: 'text', id: 'wl-symbol', placeholder: 'e.g. AAPL', style: 'width:110px', 'data-search': '1' });
    var spin = T.h('span', { class: 'spin', hidden: true });
    var msg = T.h('span', { class: 'muted', id: 'wl-msg' });
    function say(t, cls) { msg.textContent = t || ''; msg.className = cls === 'err' ? 'error-text' : 'muted'; if (cls === 'err') { msg.style.color = 'var(--down)'; } else { msg.style.color = ''; } }
    function addSym() {
      var s = sym.value.trim().toUpperCase(); var n = name.value.trim() || 'default'; if (!s) { say('type a symbol first', 'err'); return; }
      spin.hidden = false; say('');
      T.api.getReady('/api/instruments/resolve', { symbol: s }, 10).then(function (inst) {
        var i = inst && (inst.instrument || inst) || {};
        var id = i.instrument_id || i.id;
        var kinds = (i.account_kinds || []).map(function (k) { return String(k).toUpperCase(); });
        kindsCache[id] = kinds;
        var here = T.accountKind(acctSel.value);
        if (kinds.indexOf(here) < 0) {
          /* SHELL-25: never a name in an account that cannot trade it */
          spin.hidden = true;
          var to = accountFor(kinds);
          var text = (i.symbol || id) + ' trades in ' + kindsText(kinds) + ', not in ' + T.accountName(acctSel.value) + (to ? ': the account is now ' + T.accountName(to) + ' - press Add again' : '');
          if (to) { acctSel.value = to; }
          say(text, 'err'); T.ui.toast(text, 'err', 5000);
          return null;
        }
        return T.api.post('/api/watchlists', { account_id: acctSel.value, name: n, instrument_id: id }).then(function (r) { spin.hidden = true; sym.value = ''; say(''); T.ui.toast('added ' + id + ' to ' + n + ' (' + T.accountName(acctSel.value) + ')', 'ok'); apply(r); });
      }).then(null, function (e) { spin.hidden = true; say(T.api.describe(e), 'err'); T.api.fail(e, 'watchlist add'); });
    }
    sym.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); addSym(); } });
    var add = T.h('button', { class: 'btn', id: 'wl-add', onclick: addSym }, 'Add');
    var form = T.h('span', { class: 'row wl-form', style: 'gap:6px;flex-wrap:wrap;align-items:center' },
      T.h('label', { for: 'wl-name', class: 'muted' }, 'list'), name, T.h('label', { for: 'wl-account', class: 'muted' }, 'account'), acctSel,
      T.h('label', { for: 'wl-symbol', class: 'muted' }, 'symbol'), sym, spin, add, msg);
    T.pageHead(el, 'Watchlists', ((T.book.current() || {}).name || ''), [form]);
    var body = T.h('div'); el.appendChild(body);
    var cols = [
      { key: 'symbol', label: 'symbol', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b>'; }, text: function (r) { return r.symbol || r.instrument_id; } },
      { key: 'name', label: 'name', cls: 'l', text: function (r) { return r.name || ''; } },
      { key: 'last', label: 'last', html: function (r) { return T.pxHtml(T.quote(r.instrument_id) || r.quote); }, text: function (r) { var q = T.quote(r.instrument_id) || r.quote || {}; return q.last != null ? String(q.last) : ''; } },
      { key: 'chg', label: 'chg %', html: function (r) { var q = T.quote(r.instrument_id) || r.quote || {}; var last = Number(q.last != null ? q.last : q.mid), pc = Number(q.prev_close); if (!isFinite(last) || !isFinite(pc) || !pc) { return ''; } var c = ((last / pc - 1) * 100).toFixed(2); return '<span class="' + T.signCls(c) + '" title="vs previous close (display derivation)">' + T.esc(T.fmt.signed(T.fmt.num(c, 2))) + ' %</span>'; }, text: function () { return ''; } },
      { key: 'bid', label: 'bid', html: function (r) { var q = T.quote(r.instrument_id) || r.quote || {}; return q.bid == null ? '' : T.pxHtml(q, { field: 'bid' }); }, text: function () { return ''; } },
      { key: 'ask', label: 'ask', html: function (r) { var q = T.quote(r.instrument_id) || r.quote || {}; return q.ask == null ? '' : T.pxHtml(q, { field: 'ask' }); }, text: function () { return ''; } },
      { key: 'position', label: 'position', text: function (r) { return r.position == null || r.position === 0 ? '' : T.fmt.qty(r.position); } },
      { key: 'x', label: '', cls: 'l', html: function () { return '<button class="btn small" data-act="rm" title="remove from the list">x</button>'; }, text: function () { return ''; } }
    ];
    /* the account a row opens in: the list's own when it trades the name, else the first of the book that does (said) */
    function tradeAccount(l, row) {
      return lookupKinds(row.instrument_id).then(function (kinds) {
        if (!kinds.length) { return { account: null, kinds: kinds }; }
        if (kinds.indexOf(T.accountKind(l.account_id)) >= 0) { return { account: l.account_id, kinds: kinds }; }
        var to = accountFor(kinds);
        if (to) { T.ui.toast((row.symbol || row.instrument_id) + ' trades in ' + T.accountName(to) + ', not in ' + T.accountName(l.account_id) + ': opened there', '', 4000); }
        return { account: to, kinds: kinds };
      });
    }
    function openRow(l, row) {
      tradeAccount(l, row).then(function (t) {
        T.router.go(T.router.instrument(t.account || l.account_id, row.instrument_id));
      }, function (e) { T.api.fail(e, 'watchlist open'); });
    }
    function trade(l, row, k) {
      tradeAccount(l, row).then(function (t) {
        if (!t.account) { T.ui.toast((row.symbol || row.instrument_id) + ' does not trade in any account of this book', 'err', 4000); return; }
        var kind = String(row.kind || T.instKind(row.instrument_id) || '').toUpperCase();
        var held = t.account === l.account_id ? Number(row.position) || 0 : 0;
        var side;
        if (kind === 'OPTION') { side = k === 'b' ? (held < 0 ? 'BTC' : 'BTO') : (held > 0 ? 'STC' : 'STO'); }
        else if (kind === 'BOND' || kind === 'FX' || kind === 'PRIVATE' || kind === 'FUTURE') { side = k === 'b' ? 'BUY' : 'SELL'; }
        else if (T.pages.positions && T.pages.positions.keySide) { side = T.pages.positions.keySide({ kind: kind, qty: held }, k); }
        else { side = k === 'b' ? 'BUY' : 'SELL'; }
        T.ticket.open({ account_id: t.account, instrument_id: row.instrument_id, kind: kind || undefined, side: side });
      }, function (e) { T.api.fail(e, 'watchlist ticket'); });
    }
    function apply(r) {
      lists = Array.isArray(r) ? r : (r.lists || []);
      T.clear(body); grids = {};
      if (!lists.length) { body.appendChild(T.h('div', { class: 'empty' }, 'no watchlists yet - add a symbol above')); }
      var ids = [];
      lists.forEach(function (l) {
        T.rememberKinds(l.items || []);
        var p = T.h('div', { class: 'panel' }, T.h('h2', null, l.name, T.h('small', { title: l.account_id || '' }, T.accountName(l.account_id) || l.account_id || '')));
        var g = T.h('div'); p.appendChild(g); body.appendChild(p);
        grids[l.name + '|' + l.account_id] = T.components.grid.mount(g, { columns: cols, rows: l.items || [], tools: false, empty: 'empty list',
          onEnter: function (row) { openRow(l, row); },
          onKey: function (k, row) { if (k === 'b' || k === 's') { trade(l, row, k); return true; } return false; },
          onClick: function (row, e) { if (e.target.closest('button[data-act="rm"]')) { T.api.del('/api/watchlists', { account_id: l.account_id, name: l.name, instrument_id: row.instrument_id }).then(apply, function (err) { T.api.fail(err, 'watchlist remove'); }); } } });
        (l.items || []).forEach(function (i) { ids.push(i.instrument_id); });
      });
      T.watchQuotes(ids);
    }
    function load() { return T.api.get('/api/watchlists').then(apply, function (e) { T.clear(body); T.errorBox(body, e, 'watchlists'); }); }
    load();
    var off = T.state.on('quotes', function () { lists.forEach(function (l) { var g = grids[l.name + '|' + l.account_id]; if (g) { g.update(l.items || []); } }); });
    var off2 = T.state.on('accounts', fillAccts);
    T.pages.watchlists.unmount = function () { off(); off2(); T.pages.watchlists.unmount = null; };
  }
  T.pages.watchlists = { render: render, unmount: null };
})();
