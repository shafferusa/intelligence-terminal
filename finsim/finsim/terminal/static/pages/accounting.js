/* Accounting (TERMINAL_DESIGN.md s17.1 #/accounting[/tab]; WP-12).  Tabs: P&L (explain + residual, periods TODAY /
 * MTD / YTD / ITD), Balance sheet (scope incl. ALL and ALL_BOOKS, the memo tie NAV = capital + advances + retained),
 * Ledger browser (journal entries with lines; entry detail), Trial balance (debits == credits per currency), Integrity
 * (the last report; "run now" = GET /api/accounting/integrity?mode=ledger|full, synchronous on a reader).  Scope = an
 * account of the selected book, ALL (its open lifetime) or ALL_BOOKS.  Every figure is the server's.
 */
(function () {
  'use strict';
  var T = window.T;
  var TABS = [{ key: 'pnl', label: 'P&L' }, { key: 'balance-sheet', label: 'Balance sheet' }, { key: 'ledger', label: 'Ledger' }, { key: 'trial-balance', label: 'Trial balance' }, { key: 'integrity', label: 'Integrity' }];
  function render(el, route, opts) {
    opts = opts || {};
    var tab = route.tab || 'pnl'; var scope = T.store.get('accounting.scope', 'ALL'); var period = T.store.get('accounting.period', 'TODAY');
    var base = opts.base || '/api/accounting/';
    var scopeSel = T.h('select', { 'aria-label': 'scope' });
    function fillScopes() {
      T.clear(scopeSel);
      scopeSel.appendChild(T.h('option', { value: 'ALL' }, 'ALL (this book)')); scopeSel.appendChild(T.h('option', { value: 'ALL_BOOKS' }, 'ALL_BOOKS'));
      (T.state.accounts || []).forEach(function (a) { scopeSel.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); });
      if (opts.scope) { T.clear(scopeSel); scopeSel.appendChild(T.h('option', { value: opts.scope }, opts.scope)); scope = opts.scope; }
      scopeSel.value = scope; if (scopeSel.value !== scope) { scope = 'ALL'; scopeSel.value = scope; }
    }
    fillScopes();
    scopeSel.addEventListener('change', function () { scope = scopeSel.value; T.store.set('accounting.scope', scope); load(); });
    T.pageHead(el, opts.title || 'Accounting', opts.sub || '', [T.h('span', { class: 'sub' }, 'scope'), scopeSel]);
    if (!opts.noTabs) { el.appendChild(T.tabs(TABS, tab, function (k) { T.router.go((opts.prefix || '#/accounting') + '/' + k); })); }
    var body = T.h('div'); el.appendChild(body);
    function money(name, label) { return { key: name, label: label || name.replace(/_/g, ' '), html: function (r) { return '<span class="' + T.sign(r, name) + '">' + T.esc(T.money(r, name)) + '</span>'; }, text: function (r) { return T.money(r, name); } }; }
    function load() {
      T.clear(body); body.appendChild(T.h('div', { class: 'spin' }));
      if (tab === 'pnl') { return pnl(); }
      if (tab === 'balance-sheet') { return balance(); }
      if (tab === 'ledger') { return ledger(); }
      if (tab === 'trial-balance') { return trial(); }
      if (tab === 'integrity') { return integrity(null); }
      T.clear(body); body.appendChild(T.h('div', { class: 'empty' }, 'unknown tab'));
      return Promise.resolve();
    }
    function pnl() {
      var per = T.h('div', { class: 'tabs' });
      ['TODAY', 'MTD', 'YTD', 'ITD'].forEach(function (p) { per.appendChild(T.h('button', { class: 'tab' + (p === period ? ' active' : ''), onclick: function () { period = p; T.store.set('accounting.period', p); load(); } }, p)); });
      return T.api.get(base + 'pnl', { scope: scope, period: period }).then(function (v) {
        T.clear(body); body.appendChild(per);
        var c = v.components || {};
        body.appendChild(T.tiles([
          { k: 'P&L ' + period, v: T.money(v, 'pnl'), s: 'NAV ' + T.money(v, 'nav') + ' - NAV0 ' + T.money(v, 'nav_0') + ' - flows ' + T.money(v, 'flows'), cls: T.sign(v, 'pnl') },
          { k: 'explained', v: T.money(v, 'explained'), s: 'sum of the components below' },
          { k: 'residual', v: T.money(v, 'residual'), s: T.num(v, 'residual') ? 'NOT ZERO - integrity M1' : '0 by construction', cls: T.num(v, 'residual') ? 'neg' : 'pos' },
          { k: 'baseline', v: v.degraded ? 'inception' : ((v.baseline && (v.baseline.snap_date || v.baseline.date)) || 'snapshot'), s: v.degraded ? 'no snapshot yet - never invented' : (v.baseline && v.baseline.note) || '', cls: v.degraded ? 'neg' : '' },
          { k: 'quality', v: v.quality || 'n/a', s: v.partial ? 'marks partial' : 'all positions priced', cls: v.partial ? 'neg' : '' }]));
        var rows = ['realised', 'income', 'financing', 'fees', 'unrealised', 'fx_result'].filter(function (k) { return c[k] != null || c[k + '_c'] != null; }).map(function (k) { return { component: k.replace('_', ' '), value: T.money(c, k), sign: T.sign(c, k) }; });
        var g = T.h('div', { class: 'panel' }, T.h('h2', null, 'explain'));
        var ge = T.h('div'); g.appendChild(ge); body.appendChild(g);
        T.components.grid.mount(ge, { tools: false, rows: rows, columns: [{ key: 'component', label: 'component', cls: 'l' }, { key: 'value', label: 'delta vs baseline', html: function (r) { return '<span class="' + r.sign + '">' + T.esc(r.value) + '</span>'; } }] });
        body.appendChild(T.convNote(['ACCT_DAY', 'FX_RESULT', 'COST_BASIS']));
      }, function (e) { T.clear(body); T.errorBox(body, e, 'pnl'); });
    }
    function balance() {
      return T.api.get(base + 'balance-sheet', { scope: scope }).then(function (b) {
        T.clear(body);
        body.appendChild(T.tiles([{ k: 'assets', v: T.money(b, 'assets') }, { k: 'liabilities', v: T.money(b, 'liabilities') }, { k: 'NAV', v: T.money(b, 'nav'), s: (b.quality || '') + (b.partial ? ' . partial' : ''), cls: b.partial ? 'neg' : '' }, { k: 'rounding', v: T.money(b, 'rounding'), s: 'base-rounding plug (one cent per foreign currency at most)' }]));
        var g = T.h('div', { class: 'panel' }); var ge = T.h('div'); g.appendChild(ge); body.appendChild(g);
        T.components.grid.mount(ge, { tools: false, rows: b.rows || [], groupBy: function (r) { return r.section || ''; }, columns: [{ key: 'label', label: 'line', cls: 'l', html: function (r) { return (r.sub ? '<span class="muted">&nbsp;&nbsp;' : '<span>') + T.esc(r.label || r.name || '') + (r.gl ? ' <span class="muted">' + T.esc(r.gl) + '</span>' : '') + '</span>'; }, text: function (r) { return r.label || r.name || ''; } }, { key: 'currency', label: 'ccy', cls: 'l', text: function (r) { return r.currency || ''; } }, money('native'), money('usd', 'USD'), { key: 'quality', label: 'quality', cls: 'l', html: function (r) { return r.quality ? T.qchipHtml(r.quality) : ''; }, text: function (r) { return r.quality || ''; } }] });
        var m = b.memo || {};
        body.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'memo: NAV = capital + advances + retained'), T.kv([['capital contributed', T.money(m, 'capital')], ['Treasury advances', T.money(m, 'advances')], ['FX clearing (base)', T.money(m, 'fx_clearing')], ['retained results', T.money(m, 'retained')], ['ties to the cent', m.ties === false ? 'NO' : (m.ties === true ? 'yes' : 'n/a')]])));
        body.appendChild(T.convNote(['TREASURY', 'ENCUMBRANCE', 'EXCESS_LIQ']));
      }, function (e) { T.clear(body); T.errorBox(body, e, 'balance sheet'); });
    }
    function ledger() {
      var gl = T.h('input', { type: 'text', placeholder: 'gl', style: 'width:80px' }), inst = T.h('input', { type: 'text', placeholder: 'instrument', style: 'width:110px' }), from = T.h('input', { type: 'date' }), to = T.h('input', { type: 'date' });
      var tools = T.h('div', { class: 'row', style: 'margin-bottom:6px' }, gl, inst, from, to, T.h('button', { class: 'btn small', onclick: function () { fetchEntries(); } }, 'Apply'));
      var gEl = T.h('div');
      function fetchEntries() {
        return T.api.get(base + 'journal', { scope: scope, gl: gl.value.trim() || null, instrument: inst.value.trim() || null, from: from.value || null, to: to.value || null, limit: 300 }).then(function (j) {
          var rows = Array.isArray(j) ? j : (j.entries || j.rows || []);
          T.components.grid.mount(gEl, { rows: rows, csv: 'journal.csv', empty: 'no entries', sortKey: 'ts', sortAsc: false, focus: true, columns: [
            { key: 'entry_id', label: 'entry', cls: 'l' }, { key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); }, sort: function (r) { return r.ts; } }, { key: 'business_date', label: 'bdate', cls: 'l' }, { key: 'kind', label: 'kind', cls: 'l' }, { key: 'memo', label: 'memo', cls: 'l' },
            { key: 'lines', label: 'lines', cls: 'l', html: function (r) { return (r.lines || []).map(function (l) { return '<div>' + T.esc((l.account_id || '') + ' ' + (l.gl || '') + ' ' + (l.currency || '') + ' ' + (l.debit != null || l.debit_c != null ? 'Dr ' + T.money(l, 'debit') : '') + (l.credit != null || l.credit_c != null ? 'Cr ' + T.money(l, 'credit') : '') + (l.instrument_id ? ' ' + l.instrument_id : '')) + '</div>'; }).join(''); }, text: function (r) { return (r.lines || []).length + ' lines'; } },
            { key: 'command_id', label: 'command', cls: 'l', text: function (r) { return r.command_id == null ? '' : String(r.command_id); } }],
            onEnter: function (r) { T.api.get(base + 'journal/' + encodeURIComponent(r.entry_id)).then(function (d) { T.ui.modal({ title: 'entry ' + r.entry_id, wide: true, body: '<pre>' + T.esc(JSON.stringify(d, null, 1)) + '</pre>' }); }, function (e) { T.api.fail(e, 'entry'); }); } });
        }, function (e) { T.clear(gEl); T.errorBox(gEl, e, 'journal'); });
      }
      T.clear(body); body.appendChild(tools); body.appendChild(gEl);
      return fetchEntries();
    }
    function trial() {
      return T.api.get(base + 'trial-balance', { scope: scope }).then(function (tb) {
        T.clear(body);
        body.appendChild(T.tiles([{ k: 'balanced', v: tb.balanced === false ? 'NO' : 'yes', cls: tb.balanced === false ? 'neg' : 'pos', s: 'debits == credits per currency and in base' }]));
        var g = T.h('div'); body.appendChild(g);
        T.components.grid.mount(g, { rows: tb.rows || [], csv: 'trial-balance.csv', columns: [{ key: 'gl', label: 'gl', cls: 'l' }, { key: 'name', label: 'account', cls: 'l', text: function (r) { return r.name || ''; } }, { key: 'currency', label: 'ccy', cls: 'l' }, money('debit'), money('credit'), money('balance')], footer: function () { var t = tb.totals || {}; return { gl: 'totals', debit: T.esc(T.money(t, 'debit')), credit: T.esc(T.money(t, 'credit')) }; } });
      }, function (e) { T.clear(body); T.errorBox(body, e, 'trial balance'); });
    }
    function integrity(mode) {
      var p = mode ? { mode: mode, scope: scope } : { scope: scope };
      return T.api.get(base + 'integrity', p).then(function (r) {
        T.clear(body);
        var row = T.h('div', { class: 'row', style: 'margin-bottom:8px' },
          T.h('button', { class: 'btn', onclick: function () { integrity('ledger'); } }, 'Run now (ledger)'), T.h('button', { class: 'btn', onclick: function () { integrity('full'); } }, 'Run now (full)'),
          T.h('span', { class: 'hint' }, 'runs synchronously on a reader; the scoped check already runs inside every command'));
        body.appendChild(row);
        body.appendChild(T.tiles([{ k: 'result', v: r.ok ? 'OK' : 'FAILED', cls: r.ok ? 'pos' : 'neg', s: (r.mode || '') + (r.checked_at ? ' . ' + T.fmt.dt(r.checked_at) : '') }, { k: 'hold', v: r.hold ? 'INTEGRITY_HOLD' : 'none', cls: r.hold ? 'neg' : '', s: r.hold ? 'writes to the held lifetime are refused' : '' }, { k: 'lifetime', v: r.lifetime_id || scope }]));
        var checks = r.checks || (r.problems || []).map(function (x) { return Object.assign({ ok: false }, typeof x === 'string' ? { code: x } : x); });
        var g = T.h('div'); body.appendChild(g);
        T.components.grid.mount(g, { tools: false, rows: checks, empty: 'no check rows in the report', columns: [{ key: 'code', label: 'check', cls: 'l', text: function (c) { return c.code || c.check || ''; } }, { key: 'name', label: 'name', cls: 'l', text: function (c) { return c.name || ''; } }, { key: 'severity', label: 'severity', cls: 'l', text: function (c) { return c.severity || ''; } }, { key: 'ok', label: 'ok', cls: 'l', html: function (c) { return c.ok ? '<span class="pos">ok</span>' : '<span class="neg">FAIL</span>'; }, text: function (c) { return c.ok ? 'ok' : 'FAIL'; } }, { key: 'count', label: 'count', text: function (c) { return c.count == null ? '' : String(c.count); } }, { key: 'message', label: 'message', cls: 'l', text: function (c) { return c.message || ''; } }], rowClass: function (c) { return c.ok ? '' : 'neg'; } });
      }, function (e) { T.clear(body); T.errorBox(body, e, 'integrity'); });
    }
    load();
    var off = T.state.on('accounts', fillScopes);
    T.pages.accounting.unmount = function () { off(); T.pages.accounting.unmount = null; };
  }
  T.pages.accounting = { render: render, unmount: null, TABS: TABS };
})();
