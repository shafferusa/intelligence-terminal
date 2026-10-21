/* History (TERMINAL_DESIGN.md s17.5 #/h/{lifetime}/portfolio | accounting | activity, s13.10; WP-12): the read-only
 * pages of an archived lifetime served by GET /api/history/{lifetime}/... (the same shapes as the live routes with
 * read_only: true; marks from the stored final snapshot).  A banner "read-only, frozen at <date>" heads every page;
 * no action buttons; the transactions CSV comes from /api/history/{lifetime}/export/transactions.csv.  Mounted in the
 * #history root (index.html).
 */
(function () {
  'use strict';
  var T = window.T;
  function render(el, route) {
    var lt = route.lifetime; var base = '/api/history/' + encodeURIComponent(lt) + '/';
    var sub = route.sub || 'portfolio';
    var banner = T.h('div', { class: 'ro-banner' }, 'HISTORY . lifetime ' + lt + ' . read-only, frozen at ...');
    el.appendChild(banner);
    el.appendChild(T.tabs([{ key: 'portfolio', label: 'Portfolio' }, { key: 'accounting', label: 'Accounting' }, { key: 'activity', label: 'Activity' }], sub, function (k) { T.router.go('#/h/' + encodeURIComponent(lt) + '/' + k); }));
    var body = T.h('div'); el.appendChild(body);
    function setBanner(d) { var when = d && (d.frozen_at || (d.lifetime && (d.lifetime.ended || d.lifetime.ended_ts && T.fmt.date(d.lifetime.ended_ts)))); banner.textContent = 'HISTORY . lifetime ' + lt + (d && d.lifetime && d.lifetime.name ? ' (' + d.lifetime.name + ')' : '') + ' . read-only, frozen at ' + (when || 'the reset') + (d && d.lifetime && d.lifetime.reason ? ' . reason: ' + d.lifetime.reason : ''); }
    if (sub === 'portfolio') {
      T.api.get(base + 'portfolio').then(function (d) {
        setBanner(d);
        var nav = d.nav || {}; var pnl = d.pnl || {};
        body.appendChild(T.tiles([{ k: 'final NAV', v: T.money(nav, 'nav'), s: (nav.quality || '') + (nav.partial ? ' . partial' : '') }, { k: 'ITD P&L', v: pnl.ITD ? T.money(pnl.ITD, 'pnl') : 'n/a', cls: pnl.ITD ? T.sign(pnl.ITD, 'pnl') : '' }, { k: 'realised', v: T.money(nav, 'realised') }, { k: 'capital', v: T.money(nav, 'capital') }]));
        var g = T.h('div'); body.appendChild(g);
        T.components.grid.mount(g, { columns: T.pages.portfolio.COLS, rows: d.positions || [], csv: 'history-' + lt + '-positions.csv', empty: 'no positions at the freeze', groupBy: function (r) { return r.klass || r.kind || ''; } });
        var ba = d.by_account || [];
        if (ba.length) { var bg = T.h('div', { class: 'panel' }, T.h('h2', null, 'by account')); var bgi = T.h('div'); bg.appendChild(bgi); body.appendChild(bg); T.components.grid.mount(bgi, { tools: false, rows: ba, columns: [{ key: 'account_id', label: 'account', cls: 'l', text: function (r) { return r.name || r.account_id; } }, { key: 'nav', label: 'final NAV', text: function (r) { return T.money(r, 'nav'); } }] }); }
        body.appendChild(T.convNote(['LIFETIME']));
      }, function (e) { T.errorBox(body, e, 'history portfolio'); });
    } else if (sub === 'accounting') {
      T.api.get(base + 'portfolio').then(setBanner, function () { /* banner keeps its default */ });
      var csv = T.h('a', { class: 'btn', href: T.api.url(base + 'export/transactions.csv'), target: '_blank', rel: 'noopener' }, 'Transactions CSV');
      body.appendChild(T.h('div', { class: 'row', style: 'margin-bottom:8px' }, csv));
      var inner = T.h('div'); body.appendChild(inner);
      T.pages.accounting.render(inner, { tab: route.tab || 'pnl' }, { base: base + 'accounting/', scope: lt + ':ALL', title: 'Accounting (archived)', sub: lt, prefix: '#/h/' + encodeURIComponent(lt) + '/accounting' });
    } else {
      T.api.get(base + 'portfolio').then(setBanner, function () { /* banner keeps its default */ });
      var a = T.h('div'); body.appendChild(a);
      T.pages.activity.render(a, route, { base: base + 'activity', readOnly: true, title: 'Activity (archived)', sub: lt });
    }
  }
  T.pages.history = { render: render };
})();
