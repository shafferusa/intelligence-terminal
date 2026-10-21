/* Transactions (TERMINAL_DESIGN.md s17.5 #/a/{account}/transactions; WP-12): every ledger entry of the account
 * (GET /api/accounts/{id}/transactions = TxnRow: lines expanded, net cash by currency), filters (date range, kinds,
 * instrument), CSV (the server's format=csv).  The page shows what the ledger holds; it computes nothing.
 */
(function () {
  'use strict';
  var T = window.T;
  function netCash(r) { var n = r.net_cash || {}; return Object.keys(n).map(function (c) { return c + ' ' + T.money(n, c); }).join('  '); }
  var COLS = [
    { key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); }, sort: function (r) { return r.ts; } },
    { key: 'entry_id', label: 'entry', cls: 'l' }, { key: 'business_date', label: 'business date', cls: 'l' },
    { key: 'kind', label: 'kind', cls: 'l' }, { key: 'memo', label: 'memo', cls: 'l' },
    { key: 'instrument_id', label: 'instrument', cls: 'l', text: function (r) { return r.instrument_id || ''; } },
    { key: 'qty', label: 'qty', text: function (r) { return r.qty == null ? '' : T.fmt.qty(r.qty); } },
    { key: 'price', label: 'price', text: function (r) { return r.price == null ? '' : T.fmt.px(r.price); } },
    { key: 'gross', label: 'gross', text: function (r) { return r.gross == null && r.gross_c == null ? '' : T.money(r, 'gross'); } },
    { key: 'fee', label: 'fee', text: function (r) { return r.fee == null && r.fee_c == null ? '' : T.money(r, 'fee'); } },
    { key: 'net_cash', label: 'net cash', html: function (r) { var n = r.net_cash || {}; return Object.keys(n).map(function (c) { return '<span class="' + T.sign(n, c) + '">' + T.esc(c + ' ' + T.money(n, c)) + '</span>'; }).join(' '); }, text: netCash },
    { key: 'ref', label: 'ref', cls: 'l', text: function (r) { return r.ref_type ? r.ref_type + ' ' + (r.ref_id || '') : ''; } },
    { key: 'counterparty', label: 'counterparty / link', cls: 'l', text: function (r) { return r.counterparty || (r.accounts || []).filter(function (a) { return a !== r.account_id; }).join(' ') || ''; } },
    { key: 'request_id', label: 'request', cls: 'l', text: function (r) { return r.request_id || ''; } }
  ];
  function render(el, route) {
    var acct = route.account;
    var from = T.h('input', { type: 'date', 'aria-label': 'from' }), to = T.h('input', { type: 'date', 'aria-label': 'to' });
    var kinds = T.h('input', { type: 'text', placeholder: 'kinds (comma)', style: 'width:150px' }), inst = T.h('input', { type: 'text', placeholder: 'instrument', style: 'width:110px' });
    var csv = T.h('a', { class: 'btn', target: '_blank', rel: 'noopener', title: 'the server\'s CSV (every entry, lines expanded)' }, 'Server CSV');
    T.pageHead(el, 'Transactions', acct, [from, to, kinds, inst, T.h('button', { class: 'btn', onclick: function () { load(); } }, 'Apply'), csv]);
    var gEl = T.h('div'); el.appendChild(gEl);
    var grid = T.components.grid.mount(gEl, { columns: COLS, rows: [], focus: true, csv: 'transactions-' + acct + '.csv', empty: 'loading ...', sortKey: 'ts', sortAsc: false, onEnter: function (r) { showEntry(r); } });
    var sum = T.h('div', { class: 'hint' }); el.appendChild(sum);
    el.appendChild(T.convNote(['COST_BASIS', 'SWEEP', 'ACCT_DAY']));
    function params(fmt) { return { from: from.value || null, to: to.value || null, kinds: kinds.value.trim() || null, instrument: inst.value.trim() || null, limit: 500, format: fmt || null }; }
    function load() {
      csv.href = T.api.url('/api/accounts/' + encodeURIComponent(acct) + '/transactions', params('csv'));
      return T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/transactions', params()).then(function (r) {
        var rows = Array.isArray(r) ? r : (r.rows || r.transactions || []);
        grid.update(rows);
        var n = (!Array.isArray(r) && r.net_cash) || null;
        sum.textContent = (r.total != null ? r.total + ' entries' : rows.length + ' entries') + (n ? ' . net cash ' + Object.keys(n).map(function (c) { return c + ' ' + T.money(n, c); }).join(' ') : '');
      }, function (e) { T.clear(gEl); T.errorBox(gEl, e, 'transactions'); });
    }
    function showEntry(r) {
      var lines = r.lines || [];
      var g = T.h('div');
      T.ui.modal({ title: 'entry ' + r.entry_id + ' . ' + (r.kind || '') + ' . ' + (r.ts_ny || T.fmt.dt(r.ts)), wide: true, body: function (b) {
        b.appendChild(T.h('div', { class: 'hint' }, r.memo || ''));
        b.appendChild(g);
        T.components.grid.mount(g, { tools: false, rows: lines, empty: 'no lines in this row (open the Accounting ledger browser)', columns: [{ key: 'account_id', label: 'account', cls: 'l' }, { key: 'gl', label: 'gl', cls: 'l', text: function (l) { return (l.gl || '') + (l.gl_name ? ' ' + l.gl_name : ''); } }, { key: 'currency', label: 'ccy', cls: 'l' }, { key: 'debit', label: 'debit', text: function (l) { return l.debit == null && l.debit_c == null ? '' : T.money(l, 'debit'); } }, { key: 'credit', label: 'credit', text: function (l) { return l.credit == null && l.credit_c == null ? '' : T.money(l, 'credit'); } }, { key: 'instrument_id', label: 'instrument', cls: 'l', text: function (l) { return l.instrument_id || ''; } }, { key: 'memo', label: 'memo', cls: 'l', text: function (l) { return l.memo || ''; } }] });
        if (r.command_id != null) { b.appendChild(T.h('div', { class: 'hint' }, 'command ' + r.command_id + (r.request_id ? ' . request ' + r.request_id : '') + (r.ref_type ? ' . ' + r.ref_type + ' ' + (r.ref_id || '') : ''))); }
      } });
    }
    load();
    T.poll.page('transactions', 30000, load);
  }
  T.pages.transactions = { render: render, COLS: COLS };
})();
