/* History (TERMINAL_DESIGN.md s17.5 #/h/{lifetime}/portfolio | accounting | activity, s13.10; WP-12; Build 2 P03): the
 * read-only pages of an archived lifetime served by GET /api/history/{lifetime}/... (the same shapes as the live routes with
 * read_only: true; marks from the stored final snapshot).  A banner "read-only, frozen at <date>" with a link back to the
 * lifetimes heads every page; there are no action buttons (no Run now, no orders, no transfers).  Portfolio reuses the live
 * Portfolio view (by-account rows with names, class subtotals, weights of the final NAV); Accounting reuses the live tabs with
 * the P&L from inception (ITD); Activity reuses the live feed for this lifetime; the transactions CSV comes from
 * /api/history/{lifetime}/export/transactions.csv.  Mounted in the #history root (index.html).
 *
 * The History hub (FINSIM1_PLAN C1 / P3 task 9, UI-12; #/history(/tab); T.pages.history.hub) - the selected book's history:
 *   NAV history       GET /api/history/nav: one row per daily snapshot (latest restated version per date, labelled "restated on
 *                     <date> (batch X)"; the original kept beside it - the restated / original toggle), daily and cumulative
 *                     return, quality, late / added flags; a chart of the series; a click on a date opens its split (original NAV,
 *                     the dating line, the mark policy by position, interest, the import effect by line, residual) and the
 *                     portfolio as of that date (GET /api/portfolio?as_of=: the snapshot's own positions and marks, read-only)
 *   Transactions      the Accounting transactions view of the book
 *   Account activity  the Activity feed (#/activity keeps working)
 *   Performance       components/performance.js
 *   Closed positions  T.pages.portfolio.mountClosed
 *   Imports           components/imports.js (C19) when the import tool is in the build (T.hasModule('importer'))
 *   Past lifetimes    the book's closed lifetimes, each opening its read-only pages (#/h/<lifetime>/portfolio)
 * Every figure is the server's; the chart only draws the server's NAVs.
 */
(function () {
  'use strict';
  var T = window.T;
  function render(el, route) {
    var lt = route.lifetime; var base = '/api/history/' + encodeURIComponent(lt) + '/';
    var sub = route.sub || 'portfolio';
    var text = T.h('span', null, 'HISTORY . lifetime ' + lt + ' . read-only, frozen at ...');
    var banner = T.h('div', { class: 'ro-banner row', style: 'gap:10px; align-items:center' }, text, T.h('span', { class: 'spacer' }),
      T.h('a', { class: 'btn small', href: '#/history/lifetimes', title: 'every past lifetime of the book' }, 'Back to History'),
      T.h('a', { class: 'btn small', href: '#/settings/lifetimes', title: 'every lifetime of every book, with its final figures' }, 'Lifetimes'));
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

  /* ---- the History hub (UI-12) ------------------------------------------------------------------------------------------------- */
  var HUB_TABS = [{ key: 'nav', label: 'NAV history' }, { key: 'transactions', label: 'Transactions' }, { key: 'activity', label: 'Account activity' },
                  { key: 'performance', label: 'Performance' }, { key: 'closed', label: 'Closed positions' }, { key: 'imports', label: 'Imports' },
                  { key: 'lifetimes', label: 'Past lifetimes' }];
  function pct6(x) { return x == null || x === '' ? '' : T.fmt.pct(x, 3); }

  /* the NAV chart: the restated series (solid) and, when a date was restated, the original one (dashed) - one axis, a crosshair
     and a tooltip on hover, a legend with both series named; the table below is the chart's table view */
  function navChart(rows, mode) {
    var W = 860, H = 190, L = 92, R = 14, TOP = 12, B = 26;
    var pts = rows.map(function (r) { return { d: r.snap_date, a: T.num(r, 'nav'), o: r.original ? T.num(r.original, 'nav') : null }; }).filter(function (p) { return p.a != null; });
    var box = T.h('div', { class: 'navchart', style: 'position:relative' });
    if (pts.length < 2) { box.appendChild(T.h('div', { class: 'hint' }, pts.length ? 'one snapshot so far: the chart starts with the second' : 'no daily snapshot yet')); return box; }
    var hasOrig = pts.some(function (p) { return p.o != null && p.o !== p.a; });
    var vals = [];
    pts.forEach(function (p) { vals.push(p.a); if (hasOrig && p.o != null) { vals.push(p.o); } });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    if (hi === lo) { hi = lo + 1; }
    var pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
    function x(i) { return L + (W - L - R) * (pts.length === 1 ? 0 : i / (pts.length - 1)); }
    function y(v) { return TOP + (H - TOP - B) * (1 - (v - lo) / (hi - lo)); }
    function line(key) { return pts.map(function (p, i) { return p[key] == null ? null : x(i).toFixed(1) + ',' + y(p[key]).toFixed(1); }).filter(Boolean).join(' '); }
    var main = mode === 'original' && hasOrig ? 'o' : 'a';
    var other = main === 'a' ? 'o' : 'a';
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" role="img" aria-label="NAV by date" style="display:block">';
    [0, 0.5, 1].forEach(function (f) {
      var v = lo + (hi - lo) * f; var yy = y(v).toFixed(1);
      svg += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + yy + '" y2="' + yy + '" stroke="var(--line)" stroke-width="1"/>';
      svg += '<text x="' + (L - 6) + '" y="' + (Number(yy) + 3) + '" text-anchor="end" font-size="10" fill="var(--fg3)" font-family="var(--mono)">' + T.esc(T.fmt.compact(v)) + '</text>';
    });
    svg += '<text x="' + L + '" y="' + (H - 8) + '" font-size="10" fill="var(--fg3)" font-family="var(--mono)">' + T.esc(pts[0].d) + '</text>';
    svg += '<text x="' + (W - R) + '" y="' + (H - 8) + '" text-anchor="end" font-size="10" fill="var(--fg3)" font-family="var(--mono)">' + T.esc(pts[pts.length - 1].d) + '</text>';
    if (hasOrig) { svg += '<polyline fill="none" stroke="var(--fg3)" stroke-width="1.5" stroke-dasharray="5 4" points="' + line(other) + '"/>'; }
    svg += '<polyline fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" points="' + line(main) + '"/>';
    pts.forEach(function (p, i) { if (p[main] != null) { svg += '<circle cx="' + x(i).toFixed(1) + '" cy="' + y(p[main]).toFixed(1) + '" r="2.5" fill="var(--accent)"/>'; } });
    svg += '<line class="nc-x" x1="0" x2="0" y1="' + TOP + '" y2="' + (H - B) + '" stroke="var(--fg3)" stroke-width="1" visibility="hidden"/>';
    svg += '<rect class="nc-hit" x="' + L + '" y="' + TOP + '" width="' + (W - L - R) + '" height="' + (H - TOP - B) + '" fill="transparent"/></svg>';
    var legend = T.h('div', { class: 'muted', style: 'font-size:10.5px;margin:2px 0 4px;display:flex;gap:14px' },
      T.h('span', null, T.h('span', { style: 'display:inline-block;width:18px;border-top:2px solid var(--accent);vertical-align:middle;margin-right:4px' }), main === 'a' ? 'NAV (restated where a restatement exists)' : 'NAV as originally recorded'),
      hasOrig ? T.h('span', null, T.h('span', { style: 'display:inline-block;width:18px;border-top:1.5px dashed var(--fg3);vertical-align:middle;margin-right:4px' }), main === 'a' ? 'as originally recorded' : 'restated') : null);
    box.appendChild(legend);
    var holder = T.h('div', { html: svg }); box.appendChild(holder);
    var tip = T.h('div', { class: 'tile', style: 'position:absolute;pointer-events:none;display:none;padding:4px 8px;font-size:11px;z-index:3;min-width:0' });
    box.appendChild(tip);
    var s = holder.querySelector('svg'), cross = holder.querySelector('.nc-x'), hit = holder.querySelector('.nc-hit');
    function at(ev) {
      var rect = s.getBoundingClientRect(); var fx = (ev.clientX - rect.left) / rect.width * W;
      var i = Math.round((fx - L) / ((W - L - R) / (pts.length - 1)));
      return Math.max(0, Math.min(pts.length - 1, i));
    }
    hit.addEventListener('mousemove', function (ev) {
      var i = at(ev); var p = pts[i]; var xx = x(i);
      cross.setAttribute('x1', xx); cross.setAttribute('x2', xx); cross.setAttribute('visibility', 'visible');
      tip.style.display = 'block';
      T.clear(tip);
      tip.appendChild(T.h('div', null, T.h('b', null, p.d)));
      tip.appendChild(T.h('div', null, 'NAV ' + T.fmt.money(String(p.a.toFixed(2)))));
      if (hasOrig && p.o != null && p.o !== p.a) { tip.appendChild(T.h('div', { class: 'muted' }, 'original ' + T.fmt.money(String(p.o.toFixed(2))))); }
      var rect = s.getBoundingClientRect();
      var left = (xx / W) * rect.width; tip.style.left = Math.min(rect.width - 170, left + 10) + 'px'; tip.style.top = '24px';
    });
    hit.addEventListener('mouseleave', function () { cross.setAttribute('visibility', 'hidden'); tip.style.display = 'none'; });
    return box;
  }

  function navTab(el) {
    var mode = T.store.get('history.navmode', 'restated');
    var dead = false;
    var data = null;
    var head = T.h('div', { class: 'filterbar' });
    var chartEl = T.h('div', { class: 'panel' }), gEl = T.h('div'), split = T.h('div', { hidden: true }), asof = T.h('div', { hidden: true }), note = T.h('div', { class: 'hint' });
    el.appendChild(head); el.appendChild(chartEl); el.appendChild(split); el.appendChild(asof); el.appendChild(gEl); el.appendChild(note);
    function paintHead() {
      T.clear(head);
      [['restated', 'restated (latest version)'], ['original', 'original snapshots']].forEach(function (m) {
        head.appendChild(T.h('button', { class: 'btn small' + (mode === m[0] ? ' primary' : ''), title: m[0] === 'restated' ? 'per date the latest restated version where one exists, else the original' : 'the snapshots as they were recorded (never altered)',
          onclick: function () { mode = m[0]; T.store.set('history.navmode', mode); paint(); } }, m[1]));
      });
      var n = (data && data.restated_dates) || [];
      head.appendChild(T.h('span', { class: 'count' }, ((data && data.count) || 0) + ' daily snapshots' + (n.length ? ' . ' + n.length + ' restated' : '') + ' . click a date for its split and its portfolio'));
    }
    function val(r, k) { return mode === 'original' && r.original ? r.original[k] : r[k]; }
    var grid = T.components.grid.mount(gEl, { rows: [], csv: 'nav-history.csv', empty: 'loading ...', sortKey: 'snap_date', sortAsc: false, rowKey: function (r) { return r.snap_date; },
      rowClass: function () { return 'click'; },
      columns: [
        { key: 'snap_date', label: 'date', cls: 'l', html: function (r) { return T.esc(r.snap_date) + (r.trading_day ? '' : ' <span class="muted" title="no trading: the previous trading day\'s marks are carried">(no trading)</span>'); }, text: function (r) { return r.snap_date; } },
        { key: 'nav', label: 'NAV', html: function (r) { var o = mode === 'original'; if (o && !r.original) { return '<span class="muted" title="no original snapshot: the restatement added this date">n/a</span>'; } return T.esc(T.fmt.money(String(val(r, 'nav')))); },
          text: function (r) { return String(val(r, 'nav') == null ? '' : val(r, 'nav')); }, sort: function (r) { return Number(val(r, 'nav') || 0); } },
        { key: 'pnl_day', label: 'P&L day', html: function (r) { var v = val(r, 'pnl_day'); return v == null ? '' : '<span class="' + T.signCls(v) + '">' + T.esc(T.fmt.money(String(v))) + '</span>'; }, text: function (r) { return String(val(r, 'pnl_day') || ''); } },
        { key: 'flows_day', label: 'flows', text: function (r) { var v = val(r, 'flows_day'); return v == null ? '' : T.fmt.money(String(v)); } },
        { key: 'ret', label: 'daily return', html: function (r) { var v = mode === 'original' ? (r.original || {}).ret : r.ret; return v == null ? '' : '<span class="' + T.signCls(v) + '">' + T.esc(pct6(v)) + '</span>'; }, text: function (r) { return String((mode === 'original' ? (r.original || {}).ret : r.ret) || ''); } },
        { key: 'cum', label: 'cumulative', html: function (r) { var v = mode === 'original' ? (r.original || {}).cum : r.cum; return v == null ? '' : '<span class="' + T.signCls(v) + '">' + T.esc(pct6(v)) + '</span>'; }, text: function (r) { return String((mode === 'original' ? (r.original || {}).cum : r.cum) || ''); } },
        { key: 'quality', label: 'quality', cls: 'l', html: function (r) { var q = mode === 'original' && r.original ? r.original.quality : r.quality; return T.qchipHtml ? T.qchipHtml(q) : T.esc(q || ''); }, text: function (r) { return r.quality || ''; } },
        { key: 'restated', label: 'status', cls: 'l', html: function (r) {
            var b = [];
            if (r.restated) { b.push('<span class="badge-asof" title="' + T.esc((r.restated_text || '') + (r.reason ? ' . ' + r.reason : '') + ' . the original is kept unchanged') + '">' + T.esc(r.added ? 'added by restatement' : (r.restated_text || 'restated')) + '</span>'); }
            if (r.late) { b.push('<span class="badge" title="the original snapshot was computed after its date\'s evening; its restated version takes every figure as of the business date">late original</span>'); }
            return b.join(' ') || '<span class="muted">original</span>';
          }, text: function (r) { return (r.restated ? (r.restated_text || 'restated') : 'original') + (r.late ? ' . late original' : ''); } }],
      onEnter: function (r) { pick(r); }, onClick: function (r) { pick(r); } });
    function amountRows(list, label) {
      return (list || []).map(function (x) { return [x.label || x.key || x.instrument_id || label, T.money(x, 'amount')]; });
    }
    function pick(r) {
      T.clear(split); split.hidden = false; split.className = 'drawer';
      split.appendChild(T.h('div', { class: 'drawer-head' }, T.h('h2', null, r.snap_date + (r.restated ? ' . ' + (r.restated_text || 'restated') : ' . original snapshot')), T.h('span', { class: 'spacer' }),
        T.h('button', { class: 'btn small', onclick: function () { showAsOf(r.snap_date); } }, 'Portfolio as of ' + r.snap_date),
        T.h('button', { class: 'btn small', title: 'close', onclick: function () { split.hidden = true; asof.hidden = true; } }, 'x')));
      if (!r.restated || !r.split) {
        split.appendChild(T.kvGrid([['NAV', T.money(r, 'nav')], ['P&L day', T.money(r, 'pnl_day')], ['flows', T.money(r, 'flows_day')], ['quality', r.quality || ''],
          ['taken', r.original && r.original.ts ? T.fmt.dt(r.original.ts) : 'n/a'], ['late', r.late ? 'yes: computed after the evening of its date' : 'no']]));
        split.appendChild(T.h('div', { class: 'hint' }, 'an original snapshot: no restatement touched this date'));
        return;
      }
      var s = r.split || {};
      /* the waterfall at exact cents: original + dating + mark policy + interest + import effects = restated (residual 0) */
      split.appendChild(T.kvGrid([['original NAV', s.original_nav_c != null || s.original_nav != null ? T.money(s, 'original_nav') : 'n/a', r.added ? 'no original row: the restatement added this date' : 'the stored snapshot, unchanged'],
        ['dating', T.money(s, 'dating'), 'entries the original counted that belong to another business date, or dated this day but posted after it was taken'],
        ['mark policy', T.money(s, 'mark_policy'), 'dated closes / settles / OTC VM marks against the marks the original used, per position below'],
        ['interest restatement', T.money(s, 'interest_effect'), 'the re-accrual corrections dated on or before this date'],
        ['import effect', T.fmt.money(String((s.import_effect || []).reduce(function (a, x) { return a + (T.num(x, 'amount') || 0); }, 0).toFixed(2))), 'by line below'],
        ['restated NAV', T.money(r, 'nav')], ['residual', T.money(s, 'residual'), 'restated - (original + dating + mark policy + interest + import): always 0']]));
      if ((s.import_effect || []).length) {
        split.appendChild(T.h('h2', null, 'import effect by line'));
        var g1 = T.h('div'); split.appendChild(g1);
        T.components.grid.mount(g1, { tools: false, rows: s.import_effect, maxHeight: '260px', columns: [{ key: 'label', label: 'line', cls: 'l', text: function (x) { return x.label || x.key; } },
          { key: 'amount', label: 'effect on NAV', html: function (x) { return '<span class="' + T.sign(x, 'amount') + '">' + T.esc(T.money(x, 'amount')) + '</span>'; }, text: function (x) { return T.money(x, 'amount'); } }] });
      }
      if ((s.mark_policy || []).length) {
        split.appendChild(T.h('h2', null, 'mark policy by position'));
        var g2 = T.h('div'); split.appendChild(g2);
        T.components.grid.mount(g2, { tools: false, rows: s.mark_policy, maxHeight: '260px', columns: [{ key: 'instrument_id', label: 'position', cls: 'l', text: function (x) { return x.instrument_id + (x.account_id ? ' . ' + T.accountName(x.account_id) : ''); } },
          { key: 'qty', label: 'qty', text: function (x) { return T.fmt.qty(x.qty); } }, { key: 'original_mark', label: 'original mark', text: function (x) { return x.original_mark == null ? 'n/a' : T.fmt.px(x.original_mark); } },
          { key: 'dated_mark', label: 'dated mark', text: function (x) { return x.dated_mark == null ? 'n/a' : T.fmt.px(x.dated_mark) + (x.mark_kind ? ' (' + String(x.mark_kind).toLowerCase() + (x.mark_date ? ' of ' + x.mark_date : '') + ')' : ''); } },
          { key: 'amount', label: 'effect', html: function (x) { return '<span class="' + T.sign(x, 'amount') + '">' + T.esc(T.money(x, 'amount')) + '</span>'; }, text: function (x) { return T.money(x, 'amount'); } }] });
      }
      if ((s.dating || []).length) {
        split.appendChild(T.h('h2', null, 'the dating line: entries of another business date'));
        var g3 = T.h('div'); split.appendChild(g3);
        T.components.grid.mount(g3, { tools: false, rows: s.dating, maxHeight: '220px', columns: [{ key: 'entry_id', label: 'entry', cls: 'l', text: function (x) { return String(x.entry_id); } },
          { key: 'business_date', label: 'business date', cls: 'l' }, { key: 'posted', label: 'posted', cls: 'l' }, { key: 'kind', label: 'kind', cls: 'l', text: function (x) { return T.codeWords(x.kind); } },
          { key: 'why', label: 'why', cls: 'l', html: function (x) { return '<span style="white-space:normal;display:inline-block;max-width:420px">' + T.esc(x.why || '') + '</span>'; }, text: function (x) { return x.why || ''; } }] });
      }
      try { split.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ }
    }
    function showAsOf(d) {
      asof.hidden = false; asof.className = 'panel'; T.clear(asof);
      asof.appendChild(T.h('h2', null, 'portfolio as of ' + d, T.h('small', null, 'the stored snapshot: positions and marks as recorded that day, read-only')));
      var slot = T.h('div', { class: 'spin' }); asof.appendChild(slot);
      T.api.get('/api/portfolio', { as_of: d }).then(function (p) {
        T.clear(asof);
        asof.appendChild(T.h('h2', null, 'portfolio as of ' + d, T.h('small', null, (p.restated && p.restated.text ? p.restated.text + ' . ' : '') + 'the stored snapshot, nothing re-marked')));
        var nav = p.nav || {};
        asof.appendChild(T.kvGrid([['NAV', T.money(nav, 'nav')], ['original NAV', p.original_nav != null || p.original_nav_c != null ? T.money(p, 'original_nav') : 'the snapshot is original'],
          ['settled cash', T.money(nav, 'settled')], ['long / short market value', T.money(nav, 'mv_long') + ' / ' + T.money(nav, 'mv_short')], ['unrealised', T.money(nav, 'unrealised')],
          ['realised to date', T.money(nav, 'realised_cum')], ['quality', nav.quality || ''], ['positions', String(p.count || 0)]]));
        var g = T.h('div'); asof.appendChild(g);
        T.components.grid.mount(g, { rows: p.positions || [], csv: 'portfolio-' + d + '.csv', empty: 'no positions that day', maxHeight: '420px', rowKey: function (x) { return x.account_id + '|' + x.instrument_id; },
          columns: [{ key: 'account_id', label: 'account', cls: 'l', text: function (x) { return x.account_name || T.accountName(x.account_id); } },
            { key: 'symbol', label: 'instrument', cls: 'l', html: function (x) { return '<b>' + T.esc(x.symbol || x.instrument_id) + '</b> <span class="muted">' + T.esc(x.asset_class || '') + '</span>'; }, text: function (x) { return x.symbol || x.instrument_id; } },
            { key: 'qty', label: 'qty', text: function (x) { return T.fmt.qty(x.qty); } }, { key: 'mark', label: 'mark', html: function (x) { return x.mark == null ? '<span class="muted">n/a</span>' : T.esc(T.fmt.px(x.mark)); }, text: function (x) { return x.mark == null ? '' : String(x.mark); } },
            { key: 'mark_kind', label: 'mark of', cls: 'l', text: function (x) { return (x.mark_kind ? String(x.mark_kind).toLowerCase() : '') + (x.mark_date ? ' ' + x.mark_date : '') + (x.quality ? ' . ' + x.quality : ''); } },
            { key: 'mv', label: 'market value', text: function (x) { return x.mv_base == null && x.mv_base_c == null ? T.money(x, 'mv') : T.money(x, 'mv_base'); } },
            { key: 'unrealised', label: 'unrealised', html: function (x) { return '<span class="' + T.sign(x, 'unrealised') + '">' + T.esc(T.money(x, 'unrealised')) + '</span>'; }, text: function (x) { return T.money(x, 'unrealised'); } }] });
      }, function (e) { T.clear(asof); T.errorBox(asof, e, 'portfolio as of ' + d); });
    }
    function paint() {
      paintHead();
      T.clear(chartEl);
      chartEl.appendChild(T.h('h2', null, 'NAV by date', T.h('small', null, mode === 'original' ? 'the snapshots as they were recorded' : 'the latest version of every date (restated where a restatement exists)')));
      chartEl.appendChild(navChart((data && data.rows) || [], mode));
      grid.update((data && data.rows) || [], { empty: 'no daily snapshot yet: the first is taken at 17:45 ET' });
      note.textContent = (data && data.note) || '';
    }
    function load() {
      if (dead) { return Promise.resolve(); }
      return T.api.get('/api/history/nav', { scope: 'ALL' }).then(function (d) { if (dead) { return; } data = d || {}; paint(); },
        function (e) { if (!dead) { grid.empty('could not load the NAV history: ' + T.api.describe(e)); } });
    }
    load();
    return { unmount: function () { dead = true; }, refresh: load };
  }

  function lifetimesTab(el) {
    var book = T.book.current() || {};
    var gEl = T.h('div'); el.appendChild(T.h('div', { class: 'hint' }, 'every past lifetime of ' + (book.name || 'the book') + ': its read-only Portfolio, Accounting and Activity, frozen at its reset')); el.appendChild(gEl);
    var grid = T.components.grid.mount(gEl, { tools: false, rows: [], empty: 'loading ...', rowKey: function (r) { return r.lifetime_id; }, rowClass: function (r) { return r.status === 'CLOSED' ? 'click' : ''; },
      columns: [{ key: 'lifetime_id', label: 'lifetime', cls: 'l', text: function (r) { return r.lifetime_id + (r.name ? ' . ' + r.name : ''); } }, { key: 'status', label: 'status', cls: 'l' },
        { key: 'started', label: 'started', cls: 'l', text: function (r) { return r.started || (r.started_ts ? T.fmt.date(r.started_ts) : ''); } },
        { key: 'ended', label: 'ended', cls: 'l', text: function (r) { return r.ended || (r.ended_ts ? T.fmt.date(r.ended_ts) : (r.status === 'OPEN' ? 'open (this is the live one)' : '')); } },
        { key: 'final_nav', label: 'final NAV', text: function (r) { return r.final_nav == null && r.final_nav_c == null ? '' : T.money(r, 'final_nav'); } }],
      onEnter: go, onClick: go });
    function go(r) { if (r && r.status === 'CLOSED') { T.router.go('#/h/' + encodeURIComponent(r.lifetime_id) + '/portfolio'); } }
    var code = T.book.code;
    T.api.get('/api/books/' + encodeURIComponent(code) + '/lifetimes', null, { nobook: true }).then(function (r) {
      var rows = (r && r.lifetimes) || [];
      grid.update(rows, { empty: 'no lifetime yet' });
      if (!rows.some(function (x) { return x.status === 'CLOSED'; })) { el.appendChild(T.h('div', { class: 'hint' }, 'no past lifetime yet: a lifetime becomes history after a reset (Settings > Lifetimes)')); }
    }, function (e) { grid.empty('could not load the lifetimes: ' + T.api.describe(e)); });
  }

  function hubRender(el, route) {
    var tab = route.tab || 'nav';
    var book = T.book.current() || {};
    T.pageHead(el, 'History', book.name || '');
    el.appendChild(T.tabs(HUB_TABS, tab, function (k) { T.router.go('#/history/' + k); }));
    var body = T.h('div'); el.appendChild(body);
    var subs = [];
    if (tab === 'nav') {
      subs.push(navTab(body));
    } else if (tab === 'transactions') {
      T.pages.accounting.render(body, { tab: 'transactions' }, { noTabs: true, title: 'Transactions', sub: book.name || '' });
      subs.push({ unmount: function () { if (T.pages.accounting.unmount) { T.pages.accounting.unmount(); } } });
    } else if (tab === 'activity') {
      T.pages.activity.render(body, { query: route.query || {} }, { title: 'Account activity', sub: book.name || '' });
      subs.push({ unmount: function () { if (T.pages.activity.unmount) { T.pages.activity.unmount(); } } });
    } else if (tab === 'performance') {
      var pm = T.h('div', { class: 'panel' }); body.appendChild(pm);
      var perf = T.mountOptional('performance', pm, { scope: 'ALL', book: T.book.code }, 'components/performance.js: the performance statistics');
      body.appendChild(T.convNote(['PERF']));
      if (perf) { subs.push(perf); }
    } else if (tab === 'closed') {
      var cp = T.h('div', { class: 'panel' }); body.appendChild(cp);
      subs.push(T.pages.portfolio.mountClosed(cp, { scope: 'ALL' }));
    } else if (tab === 'imports') {
      var ip = T.h('div', { class: 'panel' }); body.appendChild(ip);
      var comp = T.components.imports;
      /* C19 / C2: the import routes are called only when the importer module is bound and its panel is in the build */
      if (!T.hasModule('importer')) {
        ip.appendChild(T.h('div', { class: 'empty' }, 'no import batches (the import tool is not in this build)'));
      } else if (!comp || typeof comp.render !== 'function') {
        ip.appendChild(T.h('div', { class: 'empty' }, 'the import panel is not in this build yet: the batches, their lines and reports are on the importer\'s report (python -m finsim.terminal.importer --mode report)'));
      } else {
        try { var h = comp.render(ip, { book: T.book.code }); if (h && h.unmount) { subs.push(h); } } catch (e) { console.error('imports panel failed', e); ip.appendChild(T.h('div', { class: 'error' }, 'the import panel failed: ' + (e.message || e))); }
      }
    } else if (tab === 'lifetimes') {
      lifetimesTab(body);
    }
    hub.unmount = function () { subs.forEach(function (s) { try { if (s && s.unmount) { s.unmount(); } } catch (e) { console.error(e); } }); subs = []; hub.unmount = null; };
  }
  var hub = { render: hubRender, unmount: null, tabs: HUB_TABS.map(function (t) { return t.key; }) };
  T.pages.history = { render: render, unmount: null, hub: hub };
})();
