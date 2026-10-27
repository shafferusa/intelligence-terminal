/* Transactions (TERMINAL_DESIGN.md s17.5 #/a/{account}/transactions; WP-12; Build 2 P03): every ledger entry of the account
 * (GET /api/accounts/{id}/transactions = TxnRow: lines expanded, net cash by currency) with the OTHER side of each entry
 * (the transfer's other account, a loan's lender, a repo's counterparty ...; never this account itself), filters (date
 * range, kinds, instrument), the server's CSV.  A fill's quantity / price / gross / fee are on the fill's own entry only (a
 * futures initial-margin move of the same fill says 'initial margin +17,500.00').  Enter / a double click opens the entry
 * with its lines (Dr / Cr), the command and the request id.  The page shows what the ledger holds; it computes nothing.
 */
(function () {
  'use strict';
  var T = window.T;
  function netCash(r) { var n = r.net_cash || {}; return Object.keys(n).map(function (c) { return c + ' ' + T.money(n, c); }).join('  '); }
  /* the other side in words: the server's (other_side); else the entry's other accounts by name - never the page's own account */
  function otherSide(r, acct) {
    if (r.other_side) { return r.other_side; }
    var names = r.account_names || {};
    var others = (r.entry_accounts || r.accounts || []).filter(function (a) { return a !== acct; });
    return others.map(function (a) { return names[a] || T.accountName(a); }).join(', ') || r.counterparty || '';
  }
  /* a long text cell wraps inside a fixed width instead of pushing the figures off the screen (tables fit 1440 px) */
  function wrapHtml(text, px, title) { return '<span style="display:inline-block; max-width:' + px + 'px; white-space:normal; overflow-wrap:anywhere; line-height:1.25"' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + T.esc(text == null ? '' : text) + '</span>'; }
  /* the columns a trader scans beside the ticket dock (it fits 1440 px with the dock open): a fill's quantity, price and fee are
     in its memo, and every field is in the entry table (Enter) and the server CSV */
  function columns(acct) {
    return [
      { key: 'ts', label: 'time', cls: 'l', text: function (r) { return String(r.ts_ny || T.fmt.dt(r.ts)).slice(5, 16); },
        title: function (r) { return (r.ts_ny || '') + ' . entry ' + r.entry_id + (r.business_date ? ' . business date ' + r.business_date : '') + (r.ref_type ? ' . ' + r.ref_type + ' ' + (r.ref_id || '') : ''); },
        sort: function (r) { return r.ts; }, csv: function (r) { return r.ts_ny || ''; } },
      { key: 'kind', label: 'kind', cls: 'l', text: function (r) { return r.kind_label || T.codeWords(r.kind); }, title: function (r) { return r.kind || ''; } },
      { key: 'memo', label: 'memo', cls: 'l', html: function (r) { return wrapHtml(r.memo || '', 280, r.memo_stored || r.memo || ''); }, text: function (r) { return r.memo || ''; } },
      { key: 'instrument_id', label: 'instrument', cls: 'l', text: function (r) { return r.instrument_id || ''; } },
      { key: 'gross', label: 'gross', title_h: 'a fill\'s gross amount (on the fill\'s own entry only)', text: function (r) { return r.gross == null && r.gross_c == null ? '' : T.money(r, 'gross'); } },
      { key: 'net_cash', label: 'net cash', html: function (r) { var n = r.net_cash || {}; return Object.keys(n).map(function (c) { return '<span class="' + T.sign(n, c) + '">' + T.esc(c + ' ' + T.money(n, c)) + '</span>'; }).join(' '); }, text: netCash },
      { key: 'other_side', label: 'other side', cls: 'l', title_h: 'who is on the other side of the entry: the other account of a transfer, a lender, a counterparty', html: function (r) { return wrapHtml(otherSide(r, acct), 130); }, text: function (r) { return otherSide(r, acct); } }
    ];
  }
  function render(el, route) {
    var acct = route.account;
    var dead = false;
    var from = T.h('input', { type: 'date', 'aria-label': 'from' }), to = T.h('input', { type: 'date', 'aria-label': 'to' });
    var kinds = T.h('input', { type: 'text', placeholder: 'kinds (comma)', style: 'width:150px' }), inst = T.h('input', { type: 'text', placeholder: 'instrument', style: 'width:110px' });
    var csv = T.h('a', { class: 'btn', target: '_blank', rel: 'noopener', title: 'the server\'s CSV (every entry, net cash per currency)' }, 'Server CSV');
    T.pageHead(el, 'Transactions', T.accountName(acct), [from, to, kinds, inst, T.h('button', { class: 'btn', onclick: function () { load(); } }, 'Apply'), csv]);
    var gEl = T.h('div'); el.appendChild(gEl);
    var grid = T.components.grid.mount(gEl, { columns: columns(acct), rows: [], focus: true, csv: 'transactions-' + acct + '.csv', empty: 'loading ...', sortKey: 'ts', sortAsc: false,
      rowKey: function (r) { return String(r.entry_id); }, onEnter: function (r) { showEntry(r); } });          /* the grid maps a double click to onEnter */
    var sum = T.h('div', { class: 'hint' }); el.appendChild(sum);
    el.appendChild(T.convNote(['COST_BASIS', 'SWEEP', 'ACCT_DAY']));
    [kinds, inst].forEach(function (i) { i.addEventListener('keydown', function (e) { if (e.key === 'Enter') { load(); } }); });
    [from, to].forEach(function (i) { i.addEventListener('change', function () { load(); }); });
    function filtered() { return !!(from.value || to.value || kinds.value.trim() || inst.value.trim()); }
    function params(fmt) { return { from: from.value || null, to: to.value || null, kinds: kinds.value.trim() || null, instrument: inst.value.trim() || null, limit: 500, format: fmt || null }; }
    function load() {
      if (dead) { return Promise.resolve(); }
      csv.href = T.api.url('/api/accounts/' + encodeURIComponent(acct) + '/transactions', params('csv'));
      return T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/transactions', params()).then(function (r) {
        if (dead) { return; }
        var rows = Array.isArray(r) ? r : (r.rows || r.transactions || []);
        grid.update(rows, { empty: filtered() ? 'no transactions match the filter' : 'no transactions yet' });
        var n = (!Array.isArray(r) && r.net_cash) || null;
        sum.textContent = (r.total != null ? r.total + ' entries' : rows.length + ' entries') + (rows.length >= 500 ? ' (the newest 500; the CSV has every entry)' : '') +
          (n ? ' . net cash ' + Object.keys(n).map(function (c) { return c + ' ' + T.money(n, c); }).join(' ') : '') + ' . Enter or a double click opens an entry with its lines';
      }, function (e) { if (!dead) { grid.empty('could not load the transactions: ' + T.api.describe(e)); } throw e; });
    }
    function showEntry(r) {
      var title = 'entry ' + r.entry_id + ' . ' + (r.kind_label || T.codeWords(r.kind || '')) + ' . ' + (r.ts_ny || T.fmt.dt(r.ts));
      var extra = [['other side', otherSide(r, acct)], ['net cash', netCash(r)], ['instrument', r.instrument_id || ''], ['business date', r.business_date || ''], ['reference', r.ref_type ? r.ref_type + ' ' + (r.ref_id || '') : ''], ['quantity', r.qty == null ? '' : T.fmt.qty(r.qty)], ['price', r.price == null ? '' : T.fmt.px(r.price)],
                   ['gross', r.gross == null && r.gross_c == null ? '' : T.money(r, 'gross')], ['fee', r.fee == null && r.fee_c == null ? '' : T.money(r, 'fee')],
                   ['command', r.command_id == null ? '' : String(r.command_id)], ['request', r.request_id || '']];
      if (T.pages.accounting && T.pages.accounting.entryModal) { T.pages.accounting.entryModal(r, { title: title, extra: extra }); return; }
      T.ui.modal({ title: title, wide: true, body: function (b) { b.appendChild(T.kv(extra.filter(function (p) { return p[1]; }))); } });
    }
    T.poll.page('transactions', 30000, load);
    T.pages.transactions.unmount = function () { dead = true; T.pages.transactions.unmount = null; };
  }
  T.pages.transactions = { render: render, unmount: null, columns: columns, COLS: columns(null) };
})();
