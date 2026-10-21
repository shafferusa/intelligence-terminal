/* Risk (TERMINAL_DESIGN.md s17.1 #/risk, s14; WP-12): exposures, greeks, DV01, scenarios, concentration, the private /
 * illiquid bucket, margin headroom per PB, risk warnings (amber) and compliance notices (blue) - "warnings only",
 * nothing here blocks a trade (funding, borrow and recall mechanics still apply).  GET /api/risk?scope= and
 * GET /api/compliance?scope= (WP-09); every figure is the server's and the sections render whatever the RiskView
 * carries (a section the server omits is left out, never invented).
 */
(function () {
  'use strict';
  var T = window.T;
  function money(name, label) { return { key: name, label: label || name.replace(/_/g, ' '), html: function (r) { return '<span class="' + T.sign(r, name) + '">' + T.esc(T.money(r, name)) + '</span>'; }, text: function (r) { return T.money(r, name); } }; }
  function table(parent, title, rows, cols, note) {
    var p = T.h('div', { class: 'panel' }, T.h('h2', null, title));
    if (rows && rows.length) { var g = T.h('div'); p.appendChild(g); T.components.grid.mount(g, { tools: false, rows: rows, columns: cols }); }
    else { p.appendChild(T.h('div', { class: 'empty' }, 'none')); }
    if (note) { p.appendChild(T.h('div', { class: 'hint' }, note)); }
    parent.appendChild(p);
  }
  function render(el) {
    var scope = T.store.get('risk.scope', 'ALL');
    var sel = T.h('select');
    function fill() { T.clear(sel); sel.appendChild(T.h('option', { value: 'ALL' }, 'ALL (this book)')); (T.state.accounts || []).forEach(function (a) { sel.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); }); sel.value = scope; if (sel.value !== scope) { scope = 'ALL'; sel.value = scope; } }
    fill(); sel.addEventListener('change', function () { scope = sel.value; T.store.set('risk.scope', scope); load(); });
    T.pageHead(el, 'Risk', 'warnings only - informs, never blocks', [sel]);
    var body = T.h('div'); el.appendChild(body);
    function load() {
      T.clear(body); body.appendChild(T.h('div', { class: 'spin' }));
      return Promise.all([T.api.get('/api/risk', { scope: scope }), T.api.get('/api/compliance', { scope: scope }).then(null, function (e) { return { error: e }; })]).then(function (res) {
        var r = res[0], c = res[1];
        T.clear(body);
        if (r.note) { body.appendChild(T.h('div', { class: 'conv' }, r.note)); }
        var g = r.greeks || {};
        body.appendChild(T.tiles([
          { k: 'gross exposure', v: T.money(r, 'gross'), s: 'long ' + T.money(r, 'long') + ' . short ' + T.money(r, 'short') },
          { k: 'net exposure', v: T.money(r, 'net'), cls: T.sign(r, 'net') },
          { k: 'DV01', v: T.money(r, 'dv01'), s: 'USD per bp (bonds + swaps)' },
          { k: 'delta (USD)', v: T.money(g, 'delta'), s: 'gamma ' + T.money(g, 'gamma') + ' . vega ' + T.money(g, 'vega') + ' . theta ' + T.money(g, 'theta') },
          { k: 'excess liquidity', v: T.money(r, 'excess_liquidity'), s: 'encumbrance ' + T.money(r, 'encumbrance') }]));
        table(body, 'exposures by bucket', r.exposures, [{ key: 'bucket', label: 'bucket', cls: 'l' }, money('long'), money('short'), money('net'), money('gross'), { key: 'pct_nav', label: '% NAV', text: function (x) { return x.pct_nav == null ? '' : T.fmt.pct(x.pct_nav); } }]);
        table(body, 'scenarios', r.scenarios, [{ key: 'name', label: 'scenario', cls: 'l' }, { key: 'shock', label: 'shock', cls: 'l', text: function (x) { return x.shock == null ? '' : String(x.shock); } }, money('pnl', 'P&L'), { key: 'note', label: 'note', cls: 'l', text: function (x) { return x.note || ''; } }], 'private marks are not shocked; no VaR in this build');
        table(body, 'concentration', r.concentration, [{ key: 'instrument_id', label: 'instrument', cls: 'l', text: function (x) { return x.symbol || x.instrument_id || x.name || ''; } }, money('mv', 'market value'), { key: 'pct_nav', label: '% NAV', text: function (x) { return x.pct_nav == null ? '' : T.fmt.pct(x.pct_nav); } }]);
        if (r.margin) { table(body, 'margin headroom per account / PB', r.margin, [{ key: 'account_id', label: 'account', cls: 'l' }, money('required'), money('posted'), money('headroom'), { key: 'note', label: 'note', cls: 'l', text: function (x) { return x.note || ''; } }]); }
        if (r.private) { var pv = r.private; table(body, 'private / illiquid', pv.positions || pv.lockups || [], [{ key: 'name', label: 'company', cls: 'l', text: function (x) { return x.name || x.company_id || x.instrument_id || ''; } }, money('mv', 'mark value'), { key: 'pct_nav', label: '% NAV', text: function (x) { return x.pct_nav == null ? '' : T.fmt.pct(x.pct_nav); } }, { key: 'lock_until', label: 'lock-up until', cls: 'l', text: function (x) { return x.lock_until || ''; } }], 'bucket ' + (pv.pct_nav != null ? T.fmt.pct(pv.pct_nav) + ' of NAV' : '') + ' . illiquid; last-round marks, not shocked'); }
        table(body, 'risk warnings', r.warnings, [{ key: 'severity', label: 'severity', cls: 'l', html: function (w) { return '<span class="badge amber">' + T.esc(w.severity || 'WARN') + '</span>'; }, text: function (w) { return w.severity || ''; } }, { key: 'code', label: 'code', cls: 'l' }, { key: 'message', label: 'message', cls: 'l' }, { key: 'account_id', label: 'account', cls: 'l', text: function (w) { return w.account_id || ''; } }]);
        var notices = c && !c.error ? (Array.isArray(c) ? c : (c.notices || [])) : [];
        var cp = T.h('div', { class: 'panel' }, T.h('h2', null, 'compliance notices', T.h('small', null, 'inform only; each names its rule and threshold - verify the current rule text')));
        if (c && c.error) { cp.appendChild(T.h('div', { class: 'error' }, 'compliance unavailable: ' + (c.error.message || c.error))); }
        else if (notices.length) { var cg = T.h('div'); cp.appendChild(cg); T.components.grid.mount(cg, { tools: false, rows: notices, columns: [{ key: 'rule', label: 'rule', cls: 'l' }, { key: 'status', label: 'status', cls: 'l', html: function (n) { return '<span class="badge blue">' + T.esc(n.status || '') + '</span>'; }, text: function (n) { return n.status || ''; } }, { key: 'threshold', label: 'threshold', cls: 'l', text: function (n) { return n.threshold == null ? '' : String(n.threshold); } }, { key: 'message', label: 'message', cls: 'l' }] }); }
        else { cp.appendChild(T.h('div', { class: 'empty' }, 'no notices')); }
        body.appendChild(cp);
        body.appendChild(T.convNote(['EXCESS_LIQ', 'ENCUMBRANCE', 'FUT_MARGIN', 'OPT_MARGIN']));
      }, function (e) { T.clear(body); T.errorBox(body, e, 'risk'); });
    }
    load();
    T.poll.page('risk', 30000, load);
    var off = T.state.on('accounts', fill);
    T.pages.risk.unmount = function () { off(); T.pages.risk.unmount = null; };
  }
  T.pages.risk = { render: render, unmount: null };
})();
