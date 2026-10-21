/* Watchlists (TERMINAL_DESIGN.md s17.1 #/watchlists; WP-12): lists with live prices (the quotes poller keeps the
 * visible ids fresh; every price through T.pxHtml), add by symbol (GET /api/instruments/resolve, 202 spinner) ->
 * POST /api/watchlists {account_id, name, instrument_id}; remove -> DELETE /api/watchlists.  Row Enter -> the
 * instrument workspace in the list's account; b / s -> the ticket.
 */
(function () {
  'use strict';
  var T = window.T;
  function render(el) {
    var lists = []; var grids = {};
    var name = T.h('input', { type: 'text', placeholder: 'new list name', style: 'width:160px' });
    var acctSel = T.h('select');
    function fillAccts() { T.clear(acctSel); (T.state.accounts || []).filter(function (a) { return a.kind !== 'TREASURY'; }).forEach(function (a) { acctSel.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); }); }
    fillAccts();
    var sym = T.h('input', { type: 'text', placeholder: 'symbol', style: 'width:110px', 'data-search': '1' });
    var spin = T.h('span', { class: 'spin', hidden: true });
    var add = T.h('button', { class: 'btn', onclick: function () {
      var s = sym.value.trim().toUpperCase(); var n = name.value.trim() || 'default'; if (!s) { return; }
      spin.hidden = false;
      T.api.getReady('/api/instruments/resolve', { symbol: s }).then(function (inst) {
        var id = inst.instrument_id || (inst.instrument && inst.instrument.instrument_id);
        return T.api.post('/api/watchlists', { account_id: acctSel.value, name: n, instrument_id: id }).then(function (r) { spin.hidden = true; sym.value = ''; T.ui.toast('added ' + id + ' to ' + n, 'ok'); apply(r); });
      }, function (e) { spin.hidden = true; T.api.fail(e, 'watchlist add'); });
    } }, 'Add');
    T.pageHead(el, 'Watchlists', '', [name, acctSel, sym, spin, add]);
    var body = T.h('div'); el.appendChild(body);
    var cols = [
      { key: 'symbol', label: 'symbol', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b>'; }, text: function (r) { return r.symbol || r.instrument_id; } },
      { key: 'name', label: 'name', cls: 'l', text: function (r) { return r.name || ''; } },
      { key: 'last', label: 'last', html: function (r) { return T.pxHtml(T.quote(r.instrument_id) || r.quote); }, text: function (r) { var q = T.quote(r.instrument_id) || r.quote || {}; return q.last != null ? String(q.last) : ''; } },
      { key: 'chg', label: 'chg %', html: function (r) { var q = T.quote(r.instrument_id) || r.quote || {}; var last = Number(q.last != null ? q.last : q.mid), pc = Number(q.prev_close); if (!isFinite(last) || !isFinite(pc) || !pc) { return ''; } var c = ((last / pc - 1) * 100).toFixed(2); return '<span class="' + T.signCls(c) + '" title="vs previous close (display derivation)">' + T.esc(T.fmt.signed(T.fmt.num(c, 2))) + ' %</span>'; }, text: function () { return ''; } },
      { key: 'bid', label: 'bid', html: function (r) { var q = T.quote(r.instrument_id) || r.quote || {}; return q.bid == null ? '' : T.pxHtml(q, { field: 'bid' }); }, text: function () { return ''; } },
      { key: 'ask', label: 'ask', html: function (r) { var q = T.quote(r.instrument_id) || r.quote || {}; return q.ask == null ? '' : T.pxHtml(q, { field: 'ask' }); }, text: function () { return ''; } },
      { key: 'position', label: 'position', text: function (r) { return r.position == null || r.position === 0 ? '' : T.fmt.qty(r.position); } },
      { key: 'x', label: '', cls: 'l', html: function () { return '<button class="btn small" data-act="rm" title="remove">x</button>'; }, text: function () { return ''; } }
    ];
    function apply(r) {
      lists = Array.isArray(r) ? r : (r.lists || []);
      T.clear(body); grids = {};
      if (!lists.length) { body.appendChild(T.h('div', { class: 'empty' }, 'no watchlists yet - add a symbol above')); }
      var ids = [];
      lists.forEach(function (l) {
        var p = T.h('div', { class: 'panel' }, T.h('h2', null, l.name, T.h('small', null, l.account_id || '')));
        var g = T.h('div'); p.appendChild(g); body.appendChild(p);
        grids[l.name + '|' + l.account_id] = T.components.grid.mount(g, { columns: cols, rows: l.items || [], tools: false, empty: 'empty list',
          onEnter: function (row) { T.router.go(T.router.instrument(l.account_id, row.instrument_id)); },
          onKey: function (k, row) { if (k === 'b' || k === 's') { T.ticket.open({ account_id: l.account_id, instrument_id: row.instrument_id, side: k === 'b' ? 'BUY' : 'SELL' }); return true; } return false; },
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
