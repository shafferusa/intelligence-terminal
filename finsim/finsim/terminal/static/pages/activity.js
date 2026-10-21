/* Activity (TERMINAL_DESIGN.md s17.1 #/activity; WP-12): the merged feed newest first - order events, fills,
 * transfers, job results, attention - from GET /api/activity?limit&account&type&since, with filters.  Rows are shown
 * as the server sends them.  Also used read-only by the History pages (opts.base).
 */
(function () {
  'use strict';
  var T = window.T;
  var COLS = [
    { key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); }, sort: function (r) { return r.ts; } },
    { key: 'type', label: 'type', cls: 'l', html: function (r) { var cls = r.severity === 'ERROR' ? 'red' : (r.severity === 'WARN' ? 'amber' : ''); return '<span class="badge ' + cls + '">' + T.esc(r.type || '') + '</span>'; }, text: function (r) { return r.type || ''; } },
    { key: 'account_id', label: 'account', cls: 'l', text: function (r) { return r.account_id || ''; } },
    { key: 'instrument_id', label: 'instrument', cls: 'l', text: function (r) { return r.instrument_id || ''; } },
    { key: 'summary', label: 'summary', cls: 'l', text: function (r) { return r.summary || r.message || ''; } },
    { key: 'ref', label: 'ref', cls: 'l', text: function (r) { return r.ref_type ? r.ref_type + ' ' + (r.ref_id || '') : (r.ref || ''); } }
  ];
  function render(el, route, opts) {
    opts = opts || {};
    var type = T.h('input', { type: 'text', placeholder: 'type', style: 'width:120px' }), acct = T.h('select');
    T.clear(acct); acct.appendChild(T.h('option', { value: '' }, 'all accounts')); (T.state.accounts || []).forEach(function (a) { acct.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); });
    T.pageHead(el, opts.title || 'Activity', opts.sub || '', opts.readOnly ? [] : [type, acct, T.h('button', { class: 'btn small', onclick: function () { load(); } }, 'Apply')]);
    var gEl = T.h('div'); el.appendChild(gEl);
    var grid = T.components.grid.mount(gEl, { columns: COLS, rows: [], focus: true, csv: 'activity.csv', empty: 'loading ...', sortKey: 'ts', sortAsc: false, onEnter: function (r) {
      if (r.account_id && r.ref_type === 'order') { T.router.go(T.router.account(r.account_id, 'orders')); }
      else if (r.account_id && r.instrument_id) { T.router.go(T.router.instrument(r.account_id, r.instrument_id)); }
      else { T.ui.modal({ title: r.type || 'event', body: '<pre>' + T.esc(JSON.stringify(r, null, 1)) + '</pre>' }); }
    } });
    function load() {
      return T.api.get(opts.base || '/api/activity', { limit: 300, type: type.value.trim() || null, account: acct.value || null }).then(function (r) { grid.update(Array.isArray(r) ? r : (r.rows || r.items || [])); }, function (e) { T.clear(gEl); T.errorBox(gEl, e, 'activity'); });
    }
    load();
    if (!opts.readOnly) { T.poll.page('activity', 15000, load); }
  }
  T.pages.activity = { render: render, COLS: COLS };
})();
