/* History (TERMINAL_DESIGN.md s17.5 #/h/{lifetime}/portfolio | accounting | activity, s13.10; WP-12; Build 2 P03): the
 * read-only pages of an archived lifetime served by GET /api/history/{lifetime}/... (the same shapes as the live routes with
 * read_only: true; marks from the stored final snapshot).  A banner "read-only, frozen at <date>" with a link back to the
 * lifetimes heads every page; there are no action buttons (no Run now, no orders, no transfers).  Portfolio reuses the live
 * Portfolio view (by-account rows with names, class subtotals, weights of the final NAV); Accounting reuses the live tabs with
 * the P&L from inception (ITD); Activity reuses the live feed for this lifetime; the transactions CSV comes from
 * /api/history/{lifetime}/export/transactions.csv.  Mounted in the #history root (index.html).
 */
(function () {
  'use strict';
  var T = window.T;
  function render(el, route) {
    var lt = route.lifetime; var base = '/api/history/' + encodeURIComponent(lt) + '/';
    var sub = route.sub || 'portfolio';
    var text = T.h('span', null, 'HISTORY . lifetime ' + lt + ' . read-only, frozen at ...');
    var banner = T.h('div', { class: 'ro-banner row', style: 'gap:10px; align-items:center' }, text, T.h('span', { class: 'spacer' }),
      T.h('a', { class: 'btn small', href: '#/settings/lifetimes', title: 'every lifetime of every book, with its final figures' }, 'Back to lifetimes'));
    el.appendChild(banner);
    el.appendChild(T.tabs([{ key: 'portfolio', label: 'Portfolio' }, { key: 'accounting', label: 'Accounting' }, { key: 'activity', label: 'Activity' }], sub, function (k) { T.router.go('#/h/' + encodeURIComponent(lt) + '/' + k); }));
    var body = T.h('div'); el.appendChild(body);
    function setBanner(d) {
      var l = (d && d.lifetime) || {};
      var when = d && (d.frozen_at || l.ended);
      text.textContent = 'HISTORY . lifetime ' + lt + (l.name ? ' (' + l.name + ')' : '') + ' . read-only, frozen at ' + (when || 'the reset') + (l.reason ? ' . reason: ' + l.reason : '') +
        (l.status === 'OPEN' ? ' . this lifetime is still OPEN: the figures are live' : '');
    }
    function historyTiles(d) {
      var nav = d.nav || {}; var pnl = d.pnl || {}; var l = d.lifetime || {};
      return [T.pages.portfolio.navTile(nav, 'final NAV'), T.pages.portfolio.pnlTile('P&L since inception', pnl.ITD),
              { k: 'realised', v: T.money(nav, 'realised'), cls: T.sign(nav, 'realised') }, { k: 'capital', v: T.money(nav, 'capital'), s: 'deposits - withdrawals' },
              { k: 'lifetime', v: l.started ? l.started + ' -> ' + (l.ended || 'open') : lt, s: l.final_quality ? 'final marks ' + l.final_quality : '' }];
    }
    var accts = [];
    if (sub === 'portfolio') {
      var view = T.pages.portfolio.mountView(body, { readOnly: true, tiles: historyTiles, csv: 'history-' + lt + '-positions.csv', emptyText: 'no positions at the freeze' });
      T.api.get(base + 'portfolio').then(function (d) { setBanner(d); view.update(d); }, function (e) { view.error(e); });
    } else if (sub === 'accounting') {
      var csv = T.h('a', { class: 'btn', href: T.api.url(base + 'export/transactions.csv'), target: '_blank', rel: 'noopener' }, 'Transactions CSV (whole lifetime)');
      body.appendChild(T.h('div', { class: 'row', style: 'margin-bottom:8px' }, csv));
      var inner = T.h('div'); body.appendChild(inner);
      T.api.get(base + 'portfolio').then(function (d) {
        setBanner(d);
        accts = (d.by_account || []).map(function (a) { return { account_id: a.account_id, name: a.name || a.account_name }; });
        mountAccounting();
      }, function () { mountAccounting(); });
    } else {
      T.api.get(base + 'portfolio').then(setBanner, function () { /* the banner keeps its default */ });
      var a = T.h('div'); body.appendChild(a);
      T.pages.activity.render(a, route, { base: base + 'activity', journalBase: base + 'accounting/journal/', readOnly: true, title: 'Activity (archived)', sub: lt });
    }
    function mountAccounting() {
      T.clear(inner);
      T.pages.accounting.render(inner, { tab: route.tab || 'pnl' }, { base: base + 'accounting/', scope: lt + ':ALL', scopeLabel: 'lifetime ' + lt + ' (all its accounts)', accounts: accts,
        title: 'Accounting (archived)', sub: lt, prefix: '#/h/' + encodeURIComponent(lt) + '/accounting', readOnly: true, history: lt });
    }
    T.pages.history.unmount = function () {
      if (T.pages.accounting.unmount) { T.pages.accounting.unmount(); }
      if (T.pages.activity.unmount) { T.pages.activity.unmount(); }
      T.pages.history.unmount = null;
    };
  }
  T.pages.history = { render: render, unmount: null };
})();
