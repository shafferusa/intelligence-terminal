/* Activity (TERMINAL_DESIGN.md s17.1 #/activity; WP-12; Build 2 P03): the selected book's feed newest first - order events,
 * fills, transfers (one row per transfer: the event that describes it is folded in), settlements, financing, OTC, attention -
 * from GET /api/activity (the server keeps it to the book's open lifetime; "All books" = every open lifetime; an account
 * narrows it).  The type filter reads #/activity?type=orders|settlements|transfers|... (the Dashboard's attention tiles link there);
 * 'orders' and 'settlements' also list what is open now.  Rows no book owns (job runs, housekeeping, data feeds) are
 * 'system' rows, shown only when that filter is picked.  Enter / a double click opens a row as a table (its fields, and the
 * journal entry with Dr / Cr lines when it has one).  Rows are shown as the server sends them.  Also rendered read-only by
 * the History pages (opts.base / opts.readOnly / opts.journalBase).  A fill booked as of an earlier date (the owner's import)
 * carries the AS-OF badge with its label - the server's source fields (15.7 item 7).
 */
(function () {
  'use strict';
  var T = window.T;
  var CATS = [['', 'all activity'], ['orders', 'orders and fills'], ['settlements', 'settlements'], ['transfers', 'transfers and sweeps'], ['financing', 'financing'],
              ['otc', 'OTC contracts'], ['attention', 'attention'], ['other', 'other'], ['system', 'system (jobs, feeds)']];
  var BADGE = { orders: 'blue', settlements: 'purple', transfers: 'green', financing: 'amber', otc: 'purple', attention: 'amber' };
  function shortBook(n) { return String(n || '').replace(/ Book$/, ''); }
  /* V104R1-04: a reference in words ('otc SWP-000001' -> 'contract SWP-000001', 'fx_deal D-000001' -> 'FX deal D-000001') */
  var REF_WORDS = { otc: 'contract', fx_deal: 'FX deal', sec_loan: 'securities loan', private_loan: 'private loan', borrow: 'borrow', attention: 'item' };
  function refText(r) { return r.ref_type ? (REF_WORDS[r.ref_type] || String(r.ref_type).replace(/_/g, ' ')) + ' ' + (r.ref_id || '') : (r.ref || ''); }
  /* 15.7 item 7: an as-of (or historical-model) fill shows its badge; the label (entered time, batch) is its title */
  function sourceBadge(r) {
    var b = r && r.badge;
    if (!b) { return ''; }
    return '<span class="' + (b === 'MODEL' ? 'badge-model' : 'badge-asof') + '" title="' + T.esc(r.source_label || '') + '">' + T.esc(b) + '</span> ';
  }
  /* a long text cell wraps inside a fixed width instead of pushing the figures off the screen (tables fit 1440 px) */
  function wrapHtml(text, px, title) { return '<span style="display:inline-block; max-width:' + px + 'px; white-space:normal; overflow-wrap:anywhere; line-height:1.25"' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + T.esc(text == null ? '' : text) + '</span>'; }
  function columns(allBooks) {
    var cols = [
      { key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); }, sort: function (r) { return r.ts; } },
      { key: 'category', label: 'type', cls: 'l', html: function (r) {
        var cls = r.severity === 'ERROR' ? 'red' : (r.severity === 'WARN' ? 'amber' : (BADGE[r.category] || ''));
        return '<span class="badge ' + cls + '" title="' + T.esc((r.type || '') + (r.event ? ' ' + T.codeWords(r.event) : '')) + '">' + T.esc(r.category || r.type || '') + '</span>';
      }, text: function (r) { return r.category || r.type || ''; } }];
    if (allBooks) { cols.push({ key: 'book_name', label: 'book', cls: 'l', text: function (r) { return shortBook(r.book_name) || (r.system ? 'system' : ''); } }); }
    cols.push({ key: 'account_id', label: 'account', cls: 'l', text: function (r) { return r.account_name || (r.account_id ? T.accountName(r.account_id) : (r.system ? 'system' : (r.lifetime_id ? 'lifetime ' + r.lifetime_id : ''))); }, title: function (r) { return r.account_id || ''; } });
    cols.push({ key: 'symbol', label: 'instrument', cls: 'l', text: function (r) { return r.symbol || r.instrument_id || ''; } });
    cols.push({ key: 'summary', label: 'what happened', cls: 'l', html: function (r) { return sourceBadge(r) + wrapHtml(r.summary || r.message || '', 560, r.badge ? r.source_label : ''); },
                text: function (r) { return (r.badge ? r.badge + ' ' : '') + (r.summary || r.message || ''); } });
    cols.push({ key: 'ref', label: 'ref', cls: 'l', text: refText });
    return cols;
  }
  /* the row as a table (BM-25): its fields, the server's detail (money already as text), the journal entry when it has one */
  function showRow(r, opts) {
    opts = opts || {};
    var pairs = [['time', r.ts_ny || T.fmt.dt(r.ts)], ['type', (r.category || '') + ' . ' + (r.type || '') + (r.event ? ' ' + T.codeWords(r.event) : '')],
                 ['book', r.book_name || ''], ['account', r.account_id ? (r.account_name || T.accountName(r.account_id)) + ' (' + r.account_id + ')' : (r.system ? 'system (no book owns it)' : '')],
                 ['instrument', r.symbol || r.instrument_id || ''], ['what happened', r.summary || ''], ['reference', r.ref_type ? refText(r) : ''],
                 ['folded in', (r.events || []).map(T.codeWords).join(', ')]];
    Object.keys(r.detail || {}).forEach(function (k) { var v = r.detail[k]; if (v != null && v !== '') { pairs.push([k, String(v)]); } });
    pairs = pairs.filter(function (p) { return p[1] != null && p[1] !== ''; });
    function before(b) {
      b.appendChild(T.kv(pairs));
      if (!opts.readOnly && r.account_id) {
        var acts = T.h('div', { class: 'row', style: 'margin:8px 0; gap:6px' });
        if (r.type === 'fill' || r.ref_type === 'order') { acts.appendChild(T.h('a', { class: 'btn small', href: T.router.account(r.account_id, 'orders') }, 'Orders of ' + (r.account_name || T.accountName(r.account_id)))); }
        if (r.instrument_id) { acts.appendChild(T.h('a', { class: 'btn small', href: T.router.instrument(r.account_id, r.instrument_id) }, 'Open ' + (r.symbol || r.instrument_id))); }
        acts.appendChild(T.h('a', { class: 'btn small', href: T.router.account(r.account_id, r.type === 'transfer' ? 'cash' : 'transactions') }, r.type === 'transfer' ? 'Cash & financing' : 'Transactions'));
        b.appendChild(acts);
      }
    }
    var title = (r.category || r.type || 'activity') + ' . ' + (r.ts_ny || T.fmt.dt(r.ts));
    if (r.entry_id != null && T.pages.accounting && T.pages.accounting.entryModal) {
      return T.pages.accounting.entryModal(T.api.get((opts.journalBase || '/api/accounting/journal/') + encodeURIComponent(r.entry_id)), { title: title, before: before });
    }
    return T.ui.modal({ title: title, wide: true, body: function (b) { before(b); } });
  }
  function render(el, route, opts) {
    opts = opts || {};
    var q = route.query || {};
    var readOnly = !!opts.readOnly;
    var allBooks = !readOnly && !!T.store.get('activity.allbooks', false);
    var dead = false;
    var catSel = T.h('select', { 'aria-label': 'activity type' });
    CATS.forEach(function (c) { catSel.appendChild(T.h('option', { value: c[0] }, c[1])); });
    catSel.value = q.type || ''; if (catSel.value !== (q.type || '')) { catSel.value = ''; }
    var acctSel = T.h('select', { 'aria-label': 'account' });
    function fillAccounts() {
      var keep = acctSel.value; T.clear(acctSel); acctSel.appendChild(T.h('option', { value: '' }, 'every account'));
      (T.state.accounts || []).forEach(function (a) { acctSel.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); });
      acctSel.value = keep; if (acctSel.value !== keep) { acctSel.value = ''; }
    }
    if (!readOnly) { fillAccounts(); }
    var toggle = T.h('label', { class: 'row', title: 'every open lifetime of the three books' }, T.h('input', { type: 'checkbox', checked: allBooks, onchange: function (e) { allBooks = e.target.checked; T.store.set('activity.allbooks', allBooks); remount(); load(); } }), ' All books');
    var actions = readOnly ? [catSel] : [catSel, acctSel, toggle, T.h('button', { class: 'btn small', title: 'ask the server again now', onclick: function () { load(); } }, 'Refresh')];
    var head = T.pageHead(el, opts.title || 'Activity', opts.sub || (readOnly ? '' : ((T.book.current() || {}).name || '')), actions);
    var subEl = head.querySelector('.sub');
    var extras = T.h('div'); var gEl = T.h('div'); var note = T.h('div', { class: 'hint' });
    el.appendChild(extras); el.appendChild(gEl); el.appendChild(note);
    var grid = null;
    function remount() {
      if (subEl && !readOnly) { subEl.textContent = allBooks ? 'all books' : ((T.book.current() || {}).name || ''); }
      grid = T.components.grid.mount(gEl, { columns: columns(allBooks), rows: [], focus: true, csv: 'activity.csv', empty: 'loading ...', sortKey: 'ts', sortAsc: false,
        rowKey: function (r) { return (r.type || '') + ':' + (r.ref_type || '') + ':' + (r.ref_id || '') + ':' + (r.id || '') + ':' + r.ts; },
        onEnter: function (r) { showRow(r, { readOnly: readOnly, journalBase: opts.journalBase }); } });          /* the grid maps a double click to onEnter */
    }
    remount();
    function emptyText() {
      var c = catSel.value;
      if (c === 'system') { return 'no system rows'; }
      return c ? 'no ' + (CATS.filter(function (x) { return x[0] === c; })[0] || ['', c])[1] + ' yet' : 'no activity yet';
    }
    function paintExtras(r) {
      T.clear(extras);
      if (Array.isArray(r.working_orders)) {
        var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'working orders now (' + r.working_orders.length + ')', T.h('small', null, readOnly ? 'open when the lifetime was frozen' : 'click a row to open the account\'s orders')));
        var g = T.h('div'); p.appendChild(g); extras.appendChild(p);
        T.components.grid.mount(g, { tools: false, rows: r.working_orders, empty: 'no working orders', maxHeight: '260px', rowKey: function (o) { return o.order_id; },
          onEnter: function (o) { if (!readOnly) { T.router.go(T.router.account(o.account_id, 'orders')); } }, onClick: function (o) { if (!readOnly) { T.router.go(T.router.account(o.account_id, 'orders')); } },
          columns: [{ key: 'created_ts', label: 'placed', cls: 'l', text: function (o) { return o.created_ny || T.fmt.dt(o.created_ts); } }, { key: 'account_id', label: 'account', cls: 'l', text: function (o) { return o.account_name || T.accountName(o.account_id); } },
                    { key: 'order_id', label: 'order', cls: 'l' }, { key: 'side', label: 'side', cls: 'l' }, { key: 'symbol', label: 'instrument', cls: 'l' },
                    { key: 'qty', label: 'qty', text: function (o) { return T.fmt.qty(o.qty); } }, { key: 'remaining', label: 'open', text: function (o) { return T.fmt.qty(o.remaining); } },
                    { key: 'order_type', label: 'type', cls: 'l', text: function (o) { return o.order_type + (o.limit_px ? ' ' + o.limit_px : '') + (o.stop_px ? ' stop ' + o.stop_px : ''); } },
                    { key: 'tif', label: 'TIF', cls: 'l' }, { key: 'status', label: 'status', cls: 'l' }] });
      }
      if (Array.isArray(r.pending_settlements)) {
        var s = T.h('div', { class: 'panel' }, T.h('h2', null, 'pending settlements (' + r.pending_settlements.length + ')', T.h('small', null, 'by due date')));
        var sg = T.h('div'); s.appendChild(sg); extras.appendChild(s);
        T.components.grid.mount(sg, { tools: false, rows: r.pending_settlements, empty: 'no pending settlements', maxHeight: '260px', rowKey: function (x) { return x.settle_id; },
          onEnter: function (x) { if (!readOnly) { T.router.go(T.router.account(x.account_id, 'cash')); } }, onClick: function (x) { if (!readOnly) { T.router.go(T.router.account(x.account_id, 'cash')); } },
          columns: [{ key: 'due_date', label: 'due', cls: 'l' }, { key: 'account_id', label: 'account', cls: 'l', text: function (x) { return x.account_name || T.accountName(x.account_id); } },
                    { key: 'what', label: 'for', cls: 'l' }, { key: 'direction', label: 'pay / receive', cls: 'l' }, { key: 'currency', label: 'ccy', cls: 'l' },
                    { key: 'amount', label: 'amount', html: function (x) { return '<span class="' + T.sign(x, 'amount') + '">' + T.esc(T.money(x, 'amount')) + '</span>'; }, text: function (x) { return T.money(x, 'amount'); } }] });
      }
    }
    function params() {
      var p = { limit: 300, type: catSel.value || null };
      if (!readOnly) { if (acctSel.value) { p.account = acctSel.value; } else if (allBooks) { p.scope = 'ALL_BOOKS'; } }
      return p;
    }
    function load() {
      if (dead) { return Promise.resolve(); }
      var g = grid;
      return T.api.get(opts.base || '/api/activity', params()).then(function (r) {
        if (dead || g !== grid) { return; }
        var rows = Array.isArray(r) ? r : (r.rows || r.items || []);
        grid.update(rows, { empty: emptyText() });
        paintExtras(r || {});
        note.textContent = rows.length + ' rows' + (rows.length >= 300 ? ' (the newest 300)' : '') + (catSel.value === 'system' ? '' : ' . system rows (job runs, feeds) are under the "system" type');
      }, function (e) { if (g === grid && !dead) { grid.empty('could not load the activity: ' + T.api.describe(e)); } throw e; });
    }
    catSel.addEventListener('change', function () {
      if (!readOnly) { T.router.replace('#/activity' + (catSel.value ? '?type=' + encodeURIComponent(catSel.value) : '')); }
      load().then(null, function () { /* said in the table */ });
    });
    acctSel.addEventListener('change', function () { load().then(null, function () { /* said in the table */ }); });
    var off = readOnly ? function () {} : T.state.on('accounts', fillAccounts);
    if (!readOnly) { T.poll.page('activity', 15000, load); } else { load().then(null, function () { /* said in the table */ }); }
    T.pages.activity.unmount = function () { dead = true; off(); T.pages.activity.unmount = null; };
  }
  T.pages.activity = { render: render, unmount: null, columns: columns, showRow: showRow, CATS: CATS, COLS: columns(false) };
})();
