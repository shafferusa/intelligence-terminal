/* Account Markets tab (TERMINAL_DESIGN.md s17.5 #/a/{account}/markets[/{tab}]; WP-12; Build 2 plug-ins).
 *
 * PLUG-INS FIRST (BUILD2_PLAN C11): when the account's market kind (T.accountKind: US_EXCHANGE, DEBT, CREDIT, FOREIGN_FX,
 * PRIVATE, PB) has a registered T.marketPlugins[kind] with a render function, the Markets body (under the page head and
 * the shell's account tab bar) is handed to it: plugin.render(body, ctx) with ctx = {account_id, acct, kind, role, tab,
 * book, query, setTab(key)}; for PB only the tabs the plug-in lists (e.g. ['otc']) - the PB equities / options / futures
 * tabs stay the standard grid below, with one tab bar for all of them.  A plug-in that lists its tabs gets an unknown tab
 * in the address bar corrected to its first tab.  Its handle's unmount() runs when the page is left.
 *
 * Without a plug-in, product tabs come from GET /api/accounts/{id}/markets?tab= (US_EXCHANGE / PB: equities, options
 * underlyings, futures by group, PB OTC; CREDIT: loans, borrows, rates + the sections (Private credit mounts
 * components/privatecredit.js); FOREIGN_FX: FX spot pairs, foreign listings; DEBT: the par curve and bonds; PRIVATE:
 * pages/private_markets.js).  An unknown tab falls back to the first tab and the address bar is corrected (SHELL-19).
 * Dense grid: symbol, trade action, last (freshness), chg %, bid / ask (only when some row has them - the free equity
 * feed has none: USX-28), volume, position (+ "working"), ccy (not on US Exchange), name last (capped).
 * - Equities & ETFs / Futures: b / s (and the b / s keys) open the ticket with the row's kind and its own sides (an option
 *   row BTO / STO; ORC-2); a row with a stale_note ("no print since ... - possibly delisted", USX-27) offers no b / s.
 * - Options underlyings (USX-05): a single click or Enter opens the instrument page with its option chain; the action is
 *   'chain' (b / s do the same) - an underlying row is not an order for the stock.
 * - Futures: grouped under the server's readable group_label, with a month picker on rows that carry other listed months
 *   (USX-26): picking a month opens that contract.
 * - The "add symbol" box (USX-16) resolves the symbol on Yahoo and ADDS it to this account's default watchlist
 *   (POST /api/watchlists): the tab lists watched names first, so it appears at the top; a symbol this tab does not list
 *   (a future typed on the equities tab) opens its instrument page instead.
 * Every price is T.pxHtml(quote): nothing shows a bare number.  Unsupported rows (support NOT_IN_THIS_BUILD) are grey.
 *
 * Refreshes never rebuild what the viewer is using (UI-ops fix 2026-10-21: the 30 s poll used to rebuild the whole
 * page - the filter box came back empty with all rows, and a click on a row's b button could be swallowed):
 * - the 30 s page poll and every quote tick update the grid IN PLACE (components/grid.js keeps the filter text, sort,
 *   selection, scroll and focus, and holds a render back while a pointer is pressed, so no click is dropped or delayed);
 * - the page skeleton (tabs, tools, grid, sections, the private-credit and swap-ticket mounts) is rebuilt only when the
 *   SHAPE of the answer changes (tab, tab state, account kind, sections, groups, curve, OTC eligibility, bid / ask
 *   presence); the rebuild waits for a pressed pointer to be released and carries over the grid's state, the add-symbol
 *   box (text, focus, caret) and the page scroll;
 * - a refresh that fails keeps the rows on screen (each price keeps its own freshness chip) and says so in one line;
 *   the first load that fails shows the error and the poll keeps retrying;
 * - quotes never go backwards: a row keeps whichever quote is newer (provider time, then fetch time) between the page
 *   answer and the quotes poller.
 *
 * The lists are COMPLETE (owner 2026-10-21: "why is there only 60 rows of equities on market place???? it needs all"):
 * Equities & ETFs lists every active listing the account trades (~760) and Options underlyings every optionable
 * underlying.  Their prices come with the page's own 30 s reload: the server subscribes the listed rows to the slow
 * BROWSE class (owner: "it can pull 50 prices per minute and stagger it within 15/30 min") and answers a compact quote
 * per row; the page states that rule (data.note).  Only the rows with a position or a working order in this account are
 * handed to the fast quotes poller (T.watchQuotes = the WATCH class); the ticket watches its own instrument and hedge
 * legs (T.ticket.ids).  Pushing every row into WATCH would ask the provider for ~790 prices every minute.
 */
(function () {
  'use strict';
  var T = window.T;
  function roleOf(id) { var i = String(id).indexOf(':'); return i >= 0 ? id.slice(i + 1) : id; }
  function kindOf(acct) { if (T.accountKind) { return T.accountKind(acct); } var r = roleOf(acct); return /^PB_/.test(r) ? 'PB' : r; }
  /* the standard PB tabs (the server's list) - the tab bar above a PB plug-in tab */
  var PB_TABS = [{ key: 'equities', label: 'Equities & ETFs' }, { key: 'options', label: 'Options underlyings' }, { key: 'futures', label: 'Futures' }];
  function tabList(list) { return Array.isArray(list) ? list.map(function (t) { return typeof t === 'string' ? { key: t, label: t === 'otc' ? 'OTC' : t } : { key: t.key, label: t.label || t.key, nib: t.state === 'NOT_IN_THIS_BUILD', title: t.note || '' }; }).filter(function (t) { return t.key; }) : []; }
  /* the name column is capped (full name on hover) so the prices, the position and b / s stay on screen with the dock open */
  var cssDone = false;
  function ensureCss() {
    if (cssDone || document.getElementById('markets-css')) { cssDone = true; return; }
    cssDone = true;
    var s = document.createElement('style'); s.id = 'markets-css';
    s.textContent = '.mk-name { display: inline-block; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle; }' +
      '.mk-months { margin-left: 6px; min-height: 18px; padding: 0 2px; font-size: 10.5px; }';
    document.head.appendChild(s);
  }
  /* the row's position in this account: the API's `position` (the signed quantity, null when flat), the fixtures'
   * `position_qty`, or an object's `qty`; null when flat */
  function posQty(r) {
    var p = r.position_qty != null ? r.position_qty : r.position;
    if (p != null && typeof p === 'object') { p = p.qty; }
    if (p == null || p === '') { return null; }
    p = Number(p);
    return isFinite(p) && p !== 0 ? p : null;
  }
  /* a row the fast quotes poller keeps fresh: a position or a working order in this account (`fast`: the server also counts
   * an underlying whose options are held or working) */
  function isFast(r) { return !!(r && r.instrument_id && (r.fast || posQty(r) != null || r.working)); }
  /* the ticket side of b / s for the row's kind (ORC-2): options open with BTO / STO, everything else BUY / SELL */
  function sideFor(r, k) { var kind = String(r && r.kind || '').toUpperCase(); if (kind === 'OPTION') { return k === 'b' ? 'BTO' : 'STO'; } return k === 'b' ? 'BUY' : 'SELL'; }
  /* a row that can take an order from this list: FULL support and no stale_note (USX-27) */
  function tradable(r) { return !!(r && r.support === 'FULL' && !r.stale_note); }
  function hasBidAsk(rows) { return (rows || []).some(function (r) { var q = r.quote || {}; return q.bid != null || q.ask != null; }); }
  /* the unit of a price (OA-15): a listing quoted in a minor unit (GBp, ZAc, ILA) carries quote_unit / price_scale */
  function unitOpts(r, extra) {
    var o = Object.assign({}, extra || {});
    var unit = r.quote_unit || (r.spec && r.spec.quote_unit) || null; var scale = r.price_scale;
    if (unit && scale != null && String(scale) !== '1') { o.unit = unit; o.scale = scale; o.ccy = r.currency; }
    return o;
  }
  function chg(row) {
    var q = row.quote || {};
    if (q.chg_pct != null) { return q.chg_pct; }
    if (row.chg_pct != null) { return row.chg_pct; }
    var last = Number(q.last != null ? q.last : q.mid), pc = Number(q.prev_close);
    if (isFinite(last) && isFinite(pc) && pc) { return ((last / pc - 1) * 100).toFixed(2); }
    return null;
  }
  /* the futures month picker of a row (USX-26): the row's own contract selected, the other listed months to pick */
  function monthsHtml(r) {
    var ms = Array.isArray(r.months) ? r.months : [];
    var opts = []; var seen = {};
    ms.forEach(function (m) {
      var id = typeof m === 'string' ? m : (m && (m.instrument_id || m.id)); if (!id || seen[id]) { return; } seen[id] = 1;
      opts.push({ id: id, label: (m && m.label ? m.label : (m && m.symbol) || id) + (m && m.active ? ' (active)' : ''), title: m && m.active && r.active_why ? 'the active month: ' + r.active_why : '' });
    });
    if (!seen[r.instrument_id]) { opts.unshift({ id: r.instrument_id, label: r.symbol || r.instrument_id, title: '' }); }
    if (opts.length < 2) { return ''; }
    var why = r.active_why ? ' . the active month: ' + r.active_why : '';
    return ' <select class="mk-months" data-act="month" aria-label="contract month" title="' + T.esc('the listed months of this contract: pick one to open it' + why) + '">' +
      opts.map(function (o) { return '<option value="' + T.esc(o.id) + '"' + (o.title ? ' title="' + T.esc(o.title) + '"' : '') + (o.id === r.instrument_id ? ' selected' : '') + '>' + T.esc(o.label) + '</option>'; }).join('') + '</select>';
  }
  function columns(acct) {
    return [
      { key: 'symbol', label: 'symbol', cls: 'l', html: function (r) {
        return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b>' + (r.support && r.support !== 'FULL' ? ' <span class="state-tag ' + T.esc(r.support) + '">' + T.esc(String(r.support).replace(/_/g, ' ')) + '</span>' : '') +
          (r.stale_note ? ' <span class="state-tag" title="' + T.esc(r.stale_note) + ' - not offered for trading here; it stays searchable">NO PRINT</span>' : '') + (acct.months ? monthsHtml(r) : '');
      }, text: function (r) { return r.symbol || r.instrument_id || ''; } },
      { key: 'act', label: acct.chain ? 'chain' : 'trade', cls: 'l', html: function (r) {
        if (r.support === 'NOT_IN_THIS_BUILD') { return ''; }
        if (acct.chain) { return '<button class="btn small act" data-act="chain" title="open the instrument page with its option chain (click the row, Enter, b or s)">chain</button>'; }
        if (!tradable(r)) { return r.stale_note ? '<span class="muted" title="' + T.esc(r.stale_note) + '">-</span>' : ''; }
        var o = String(r.kind || '').toUpperCase() === 'OPTION';
        return '<button class="btn small act" data-act="b" title="' + (o ? 'BUY TO OPEN' : 'BUY') + ' (b)">b</button><button class="btn small act" data-act="s" title="' + (o ? 'SELL TO OPEN' : 'SELL') + ' (s)">s</button>';
      }, text: function () { return ''; } },
      { key: 'last', label: 'last', html: function (r) {
        if (r.support === 'NOT_IN_THIS_BUILD') { return '<span class="nib">NOT IN THIS BUILD</span>'; }
        if (!r.quote && r.queued) { return '<span class="muted queued" title="' + T.esc(acct.queuedTitle ? acct.queuedTitle() : 'no price fetched yet: queued for the background refresh') + '">queued</span>'; }
        return T.pxHtml(r.quote || r.mark, unitOpts(r, { basis: r.price_basis }));
      }, text: function (r) { var q = r.quote || r.mark || {}; return q.last != null ? q.last : (q.mark != null ? q.mark : (q.mid != null ? q.mid : '')); } },
      { key: 'chg', label: 'chg %', html: function (r) { var c = chg(r); return c == null ? '' : '<span class="' + T.signCls(c) + '" title="vs previous close (display derivation from two observed prices)">' + T.esc(T.fmt.signed(T.fmt.num(c, 2))) + ' %</span>'; }, text: function (r) { var c = chg(r); return c == null ? '' : String(c); } },
      { key: 'bid', label: 'bid', html: function (r) { var q = r.quote || {}; return q.bid == null ? '' : T.pxHtml(q, unitOpts(r, { field: 'bid' })); }, text: function (r) { var q = r.quote || {}; return q.bid == null ? '' : String(q.bid); } },
      { key: 'ask', label: 'ask', html: function (r) { var q = r.quote || {}; return q.ask == null ? '' : T.pxHtml(q, unitOpts(r, { field: 'ask' })); }, text: function (r) { var q = r.quote || {}; return q.ask == null ? '' : String(q.ask); } },
      { key: 'volume', label: 'volume', text: function (r) { var q = r.quote || {}; return q.day_volume == null ? '' : T.fmt.qty(q.day_volume); }, sort: function (r) { var q = r.quote || {}; return q.day_volume == null ? '' : q.day_volume; } },
      { key: 'position', label: 'position', html: function (r) {
        var p = posQty(r); var h = p == null ? '' : '<span class="' + (p > 0 ? 'pos' : 'neg') + '">' + T.esc(T.fmt.qty(p)) + '</span>';
        if (r.working) { h += (h ? ' ' : '') + '<span class="muted" title="an open order in this account on this instrument (on Options underlyings: on it or on one of its options); see Orders (g o); its price is kept fresh">working</span>'; }
        return h;
      }, text: function (r) { var p = posQty(r); return p == null ? '' : String(p); } },
      { key: 'ccy', label: 'ccy', cls: 'l', text: function (r) { return r.currency || ''; } },
      { key: 'name', label: 'name', cls: 'l', html: function (r) { return r.name ? '<span class="mk-name" title="' + T.esc(r.name) + '">' + T.esc(r.name) + '</span>' : ''; }, text: function (r) { return r.name || ''; } }
    ].filter(function (c) { return !(acct.hideCcy && c.key === 'ccy') && !(acct.hideBidAsk && (c.key === 'bid' || c.key === 'ask')); });
  }
  /* provider time, then fetch time (epoch seconds; -1 when absent) */
  function qtime(q) { var a = q ? Number(q.quote_time) : NaN, b = q ? Number(q.fetched_at) : NaN; return [isFinite(a) ? a : -1, isFinite(b) ? b : -1]; }
  /* true when quote a is at least as new as quote b */
  function newer(a, b) { if (!a) { return false; } if (!b) { return true; } var ta = qtime(a), tb = qtime(b); return ta[0] !== tb[0] ? ta[0] > tb[0] : ta[1] >= tb[1]; }
  function mergeQuote(row, q) { if (q && newer(q, row.quote)) { row.quote = Object.assign({}, row.quote || {}, q); return true; } return false; }
  function render(el, route) {
    var acct = route.account;
    var role = roleOf(acct);
    var mkind = kindOf(acct);
    var data = null; var grid = null; var tab = route.tab; var dead = false;
    var ui = null;                                             /* the painted skeleton; null until the first answer */
    T.pageHead(el, 'Markets', T.accountName(acct));          /* V104R1-04: the account's name (its id is in the address bar) */
    ensureCss();
    var tabBox = T.h('div'); var body = T.h('div'); el.appendChild(tabBox); el.appendChild(body);
    function goTab(k) { tab = k; T.router.go(T.router.account(acct, 'markets/' + k)); }
    /* SHELL-19: an unknown tab in the address bar falls back to the first tab, said once, the address bar corrected */
    function badTab(first) {
      T.ui.toast('no tab ' + tab + ' on this Markets page - showing ' + first, '', 3000);
      tab = first;
      var h = T.router.account(acct, 'markets/' + first);
      if (T.router.replace) { T.router.replace(h); }
      if (T.router.current) { T.router.current.tab = first; T.router.current.hash = h; }
    }
    /* ---- a registered product plug-in takes the body (C11) */
    var plug = T.marketPlugins && T.marketPlugins[mkind];
    var plugTabs = plug ? tabList(plug.tabs) : [];
    var plugKeys = plugTabs.map(function (t) { return t.key; });
    if (plug && typeof plug.render === 'function' && (mkind !== 'PB' || plugKeys.indexOf(tab) >= 0)) {
      if (mkind !== 'PB' && plugKeys.length && tab && plugKeys.indexOf(tab) < 0) { badTab(plugKeys[0]); }
      if (mkind === 'PB') { tabBox.appendChild(T.tabs(PB_TABS.concat(plugTabs), tab, goTab)); }
      var handle = null;
      var ctx = { account_id: acct, acct: T.accountRow ? T.accountRow(acct) : null, kind: mkind, role: role, tab: tab, book: T.book.code, query: route.query || {}, setTab: goTab };
      try { handle = plug.render(body, ctx) || null; } catch (e) { console.error('market plug-in', mkind, 'failed', e); body.appendChild(T.h('div', { class: 'error' }, 'this page failed to draw: ' + (e.message || e))); }
      T.pages.markets.unmount = function () { dead = true; if (handle && typeof handle.unmount === 'function') { try { handle.unmount(); } catch (e) { console.error('market plug-in unmount', e); } } T.pages.markets.unmount = null; };
      return;
    }
    if (role === 'PRIVATE') {
      if (T.pages.private_markets && T.pages.private_markets.render) { T.pages.private_markets.render(body, route); return; }
      T.notBuilt(body, 'the Private Markets account page (companies grid, reported rounds, negotiated secondaries)');
      body.appendChild(T.convNote(['QP', 'PRIVATE_MARK', 'PRIVATE_TRADE']));
      return;
    }
    function load() {
      return T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/markets', { tab: tab }).then(function (d) {
        if (dead) { return; }
        var keys = (d.tabs || []).map(function (t) { return t.key; });
        if (tab && keys.length && keys.indexOf(tab) < 0) { badTab(d.tab && keys.indexOf(d.tab) >= 0 ? d.tab : keys[0]); }
        var known = T.state.quotes || {};
        (d.rows || []).forEach(function (r) { mergeQuote(r, known[r.instrument_id]); });
        if (T.rememberKinds) { T.rememberKinds(d.rows); }
        data = d; if (!tab) { tab = d.tab || (d.tabs && d.tabs[0] && d.tabs[0].key) || null; }
        if (ui) { ui.err.hidden = true; ui.err.textContent = ''; }
        show();
      }, function (e) {
        if (dead) { return; }
        var listed = e && e.details && Array.isArray(e.details.tabs) ? e.details.tabs : null;   /* 400 "unknown tab" names the tabs */
        if (e && e.code === 'BAD_REQUEST' && listed && listed.length && tab && listed.indexOf(tab) < 0) { badTab(listed[0]); return load(); }
        if (!ui) { T.clear(body); T.errorBox(body, e, 'markets'); return; }
        console.error('markets refresh', e);
        ui.err.textContent = 'refresh failed: ' + T.api.describe(e) + ' - the rows below keep their last prices, each with its own freshness';
        ui.err.hidden = false;
      });
    }
    /* the instruments grid: shown when the tab has rows, else as an empty table saying so - except on a tab with sections
     * and on the PB OTC desk (the swap ticket below it is the tab; an empty instruments table above it said nothing) */
    function hasGrid(d) { return !!((d.rows || []).length || (!(d.sections || []).length && tab !== 'otc')); }
    /* what decides the page layout; anything else in the answer is updated in place */
    function shape(d) {
      var cur = (d.tabs || []).filter(function (t) { return t.key === tab; })[0] || {};
      var e = tab === 'otc' ? (((T.book.current() || {}).eligibility) || {}) : {};
      return JSON.stringify([tab, cur.state || null, d.kind || null, hasGrid(d), !!d.groups, d.empty || null,
        (d.sections || []).map(function (s) { return [s.key, s.label || null, s.state || null, !!(s.rows && s.rows.length), s.columns || null]; }),
        !!(role === 'DEBT' || d.curve), e.pb_otc === false, hasBidAsk(d.rows), (d.rows || []).some(function (r) { return Array.isArray(r.months) && r.months.length; })]);
    }
    function show() {
      if (dead || !data) { return; }
      if (ui && ui.shape === shape(data)) { refresh(); return; }
      if (T.components.grid.busy && T.components.grid.busy()) { setTimeout(show, 50); return; }   /* rebuild after the click */
      paintAll();
    }
    function keep() {
      var a = document.activeElement; var main = T.q('#main'); var box = ui.box;
      var k = { grid: grid ? grid.state() : null, box: null, mainTop: main ? main.scrollTop : null };
      if (box) { k.box = { value: box.value, focus: a === box, caret: null }; if (a === box) { try { k.box.caret = [box.selectionStart, box.selectionEnd]; } catch (e) { k.box.caret = null; } } }
      return k;
    }
    function carryOver(k) {
      if (!k) { return; }
      if (ui.box && k.box) {
        ui.box.value = k.box.value;
        if (k.box.focus) { try { ui.box.focus({ preventScroll: true }); if (k.box.caret) { ui.box.setSelectionRange(k.box.caret[0], k.box.caret[1]); } } catch (e) { /* focus is a convenience */ } }
      }
      var main = T.q('#main'); if (main && k.mainTop != null) { main.scrollTop = k.mainTop; }
    }
    /* the tooltip of a queued row (subscribed, never fetched): the server's refresh rule (data.refresh) */
    function queuedTitle() {
      var f = (data && data.refresh) || {};
      return 'no price fetched yet: this row is queued for the background refresh' + (f.per_min ? ' (' + f.per_min + ' prices a minute, oldest first' +
        (f.eta_min ? '; the queue clears in about ' + f.eta_min + ' min' : '') + ')' : '') + '. The price appears with the next page reload (every 30 s) ' +
        'and then carries its own freshness chip; open the row (Enter) for a quote now.';
    }
    /* A BROWSE list (the answer carries `refresh`: equities, option underlyings) hands the fast quotes poller only the rows
     * with a position or a working order; every other row's price comes with the 30 s page reload.  A short list (futures,
     * FX pairs, foreign listings, bonds) is watched whole, as before: the page load subscribes those rows PAGE (30 s) at
     * the server anyway, so watching them costs the provider nothing more. */
    function watch() {
      var rows = data.rows || [];
      var ids = (data.refresh ? rows.filter(isFast) : rows).map(function (r) { return r.instrument_id; }).filter(Boolean);
      var sig = ids.join(',');
      if (sig !== ui.ids) { ui.ids = sig; T.watchQuotes(ids); }
    }
    function paintTabs() {
      var tabs = (data.tabs || []).map(function (t) { return { key: t.key, label: t.label || t.key, nib: t.state === 'NOT_IN_THIS_BUILD', title: t.note || '' }; });
      if (mkind === 'PB') { plugTabs.forEach(function (t) { if (!tabs.some(function (x) { return x.key === t.key; })) { tabs.push(t); } }); }
      var sig = JSON.stringify([tab, tabs]);
      if (ui.tabs === sig) { return; }
      ui.tabs = sig;
      T.clear(tabBox);
      if (tabs.length) { tabBox.appendChild(T.tabs(tabs, tab, goTab)); }
    }
    /* the options-underlyings tab: a row opens its instrument page, where the option chain is (USX-05) */
    function openChain(r) { if (r && r.support !== 'NOT_IN_THIS_BUILD') { T.state.open_chain = r.instrument_id; T.router.go(T.router.instrument(acct, r.instrument_id)); } }
    function ticketFor(r, k) {
      if (!tradable(r)) { T.ui.toast(r.stale_note ? (r.symbol || r.instrument_id) + ': ' + r.stale_note + ' - not offered for trading' : (r.symbol || r.instrument_id) + ' is not tradable from this list', '', 3000); return; }
      T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, kind: r.kind, side: sideFor(r, k) });
    }
    /* USX-16: resolve on Yahoo, then add to this account's default watchlist so the tab lists it first */
    function addSymbol(box, spin) {
      var sym = box.value.trim().toUpperCase(); if (!sym) { return; }
      spin.hidden = false;
      T.api.getReady('/api/instruments/resolve', { symbol: sym }).then(function (inst) {
        var id = inst.instrument_id || (inst.instrument && inst.instrument.instrument_id);
        if (!id) { spin.hidden = true; T.ui.toast('resolved, but no instrument id in the answer', 'err'); return; }
        if (T.rememberKind) { T.rememberKind(id, inst.kind || (inst.instrument && inst.instrument.kind)); }
        return T.api.post('/api/watchlists', { account_id: acct, name: 'default', instrument_id: id }).then(function () {
          return load().then(function () {
            spin.hidden = true;
            var listed = data && (data.rows || []).some(function (r) { return r.instrument_id === id; });
            if (listed) {
              box.value = '';
              T.ui.toast((inst.symbol || id) + ' added to the watchlist of ' + T.accountName(acct) + ' - listed first on this tab', 'ok', 3000);
              if (grid && grid.rows && grid.select) { var vis = grid.rows(); for (var i = 0; i < vis.length; i++) { if (vis[i].instrument_id === id) { grid.select(i, true); break; } } }
            } else {
              T.ui.toast((inst.symbol || id) + ' added to the watchlist of ' + T.accountName(acct) + '; this tab does not list ' + (inst.kind || 'it').toString().toLowerCase() + 's - its page opens', '', 3500);
              T.router.go(T.router.instrument(acct, id));
            }
          });
        });
      }).then(null, function (e) { spin.hidden = true; T.api.fail(e, 'add ' + sym); });
    }
    function paintAll() {
      var carry = ui ? keep() : null;
      T.clear(tabBox); T.clear(body);
      ui = { shape: shape(data), tabs: null, ids: null, box: null, note: null, curve: null, curveSig: null, sections: [],
             err: T.h('div', { class: 'error', role: 'status', hidden: true }) };
      body.appendChild(ui.err);
      grid = null;
      paintTabs();
      var cur = (data.tabs || []).filter(function (t) { return t.key === tab; })[0] || {};
      if (cur.state === 'NOT_IN_THIS_BUILD') { body.appendChild(T.nib(cur.label || tab, cur.note)); carryOver(carry); return; }
      var isChain = tab === 'options' && (mkind === 'US_EXCHANGE' || mkind === 'PB');
      var tools = T.h('div', { class: 'row', style: 'margin-bottom:8px' });
      var spin = T.h('span', { class: 'spin', hidden: true });
      if (mkind === 'US_EXCHANGE' || mkind === 'PB' || (mkind === 'FOREIGN_FX' && tab === 'listings')) {
        var box = ui.box = T.h('input', { type: 'text', placeholder: 'add symbol to the watchlist (Enter)', 'data-search': '1', 'aria-label': 'add a symbol to this account\'s watchlist',
          title: 'Enter looks the symbol up on Yahoo and adds it to this account\'s watchlist: the tab lists watched names first', style: 'width:260px' });
        box.addEventListener('keydown', function (e) { if (e.key === 'Enter') { addSymbol(box, spin); } });
        tools.appendChild(box); tools.appendChild(spin);
      }
      ui.note = T.h('span', { class: 'hint', hidden: !data.note }, data.note || '');
      tools.appendChild(ui.note);
      body.appendChild(tools);
      if (role === 'DEBT' || data.curve) { ui.curve = curvePanel(data.curve); ui.curveSig = JSON.stringify(data.curve || null); body.appendChild(ui.curve); }
      var rows = data.rows || [];
      if (hasGrid(data)) {
        var gEl = T.h('div'); body.appendChild(gEl);
        var spec = { hideCcy: role === 'US_EXCHANGE', hideBidAsk: !hasBidAsk(rows), queuedTitle: queuedTitle, chain: isChain,
                     months: rows.some(function (r) { return Array.isArray(r.months) && r.months.length; }) };
        grid = T.components.grid.mount(gEl, {
          columns: columns(spec), rows: rows, focus: true, csv: 'markets-' + role + '-' + (tab || 'all') + '.csv', empty: data.empty || 'no instruments on this tab',
          rowKey: function (r) { return r.instrument_id; },
          groupBy: data.groups ? function (r) { return r.group_label || r.group || ''; } : null,
          rowClass: function (r) { return r.support === 'NOT_IN_THIS_BUILD' ? 'nib' : ''; },
          onEnter: function (r) { if (isChain) { openChain(r); return; } if (r.support !== 'NOT_IN_THIS_BUILD') { T.router.go(T.router.instrument(acct, r.instrument_id)); } },
          onKey: function (k, r) {
            if (k !== 'b' && k !== 's') { return false; }
            if (isChain) { openChain(r); return true; }
            if (r.support === 'FULL') { ticketFor(r, k); return true; }
            return false;
          },
          onClick: function (r, e) {
            if (e.target.closest('select')) { return; }                      /* the month picker */
            if (isChain) { openChain(r); return; }                            /* one click opens the chain (USX-05) */
            var b = e.target.closest('button[data-act]'); if (b) { ticketFor(r, b.dataset.act); }
          },
          restore: carry && carry.grid
        });
        gEl.addEventListener('change', function (e) {
          var s = e.target.closest ? e.target.closest('select[data-act="month"]') : null;
          if (s && s.value) { T.router.go(T.router.instrument(acct, s.value)); }
        });
        watch();
      }
      (data.sections || []).forEach(function (s) { var sec = section(s); ui.sections.push(sec); body.appendChild(sec.el); });
      if (role === 'FOREIGN_FX') {
        body.appendChild(notLoaded('the FX desk, foreign listings and foreign bonds'));
        body.appendChild(T.convNote(['FX_SPOT']));
      }
      if (role === 'CREDIT') { body.appendChild(creditSections()); }
      if (tab === 'otc' && (role.indexOf('PB_') === 0 || data.kind === 'PB')) { body.appendChild(otcTab()); }
      if (role === 'DEBT') { body.appendChild(notLoaded('the Debt Instruments page (Treasuries, corporates, the bond ticket)')); }
      if (role === 'US_EXCHANGE' || data.kind === 'PB') { body.appendChild(T.convNote(['FILL_PRICE', 'PARTICIPATION', 'SESSION', 'STALE'])); }
      carryOver(carry);
    }
    /* same shape: rows, prices, note, curve and section rows change in place; nothing the viewer types into is rebuilt */
    function refresh() {
      paintTabs();
      if (ui.note) { ui.note.textContent = data.note || ''; ui.note.hidden = !data.note; }
      if (ui.curve) {
        var cs = JSON.stringify(data.curve || null);
        if (cs !== ui.curveSig) { var nc = curvePanel(data.curve); ui.curve.parentNode.replaceChild(nc, ui.curve); ui.curve = nc; ui.curveSig = cs; }
      }
      if (grid) { grid.update(data.rows || []); watch(); }
      (data.sections || []).forEach(function (s, k) { var sec = ui.sections[k]; if (sec) { sec.update(s); } });
    }
    function section(s) {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, s.label || s.key));
      var out = { el: p, update: function () {} };
      if (s.state === 'NOT_IN_THIS_BUILD') { p.appendChild(T.nib(s.label || s.key, s.note)); return out; }
      var g = null; var empty = null;
      if (s.rows && s.rows.length) {
        var cols = (s.columns || Object.keys(s.rows[0])).map(function (c) { var key = typeof c === 'string' ? c : c.key; return { key: key, label: typeof c === 'string' ? c.replace(/_/g, ' ') : (c.label || key), cls: /id|name|ccy|currency|index|status|kind|lender|tier/.test(key) ? 'l' : 'num', text: function (r) { var v = r[key]; return v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v)); } }; });
        var gEl = T.h('div'); p.appendChild(gEl);
        g = T.components.grid.mount(gEl, { columns: cols, rows: s.rows, tools: false });
      } else { empty = T.h('div', { class: 'empty' }, s.empty || 'nothing here'); p.appendChild(empty); }
      var note = T.h('div', { class: 'hint', hidden: !s.note }, s.note || ''); p.appendChild(note);
      out.update = function (s2) {
        if (g && s2.rows && s2.rows.length) { g.update(s2.rows); }
        if (empty) { empty.textContent = s2.empty || 'nothing here'; }
        note.textContent = s2.note || ''; note.hidden = !s2.note;
      };
      return out;
    }
    function notLoaded(what) { return T.h('div', { class: 'error' }, what + ' did not load - reload the page (the rest of this page works)'); }
    function creditSections() {
      var box = T.h('div');
      var pc = T.h('div', { class: 'panel' }, T.h('h2', null, 'Private credit'));
      var mount = T.h('div'); pc.appendChild(mount);
      T.mountOptional('privatecredit', mount, { account_id: acct, book: T.book.code }, 'the private-credit commit form and loans table');
      pc.appendChild(T.convNote(['PRIVATE_CREDIT']));
      box.appendChild(pc);
      box.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'Repo, securities lending, counterparties'), notLoaded('the Credit Instruments desk')));
      box.appendChild(T.convNote(['SHORT_COLLATERAL', 'BORROW_AVAIL', 'ACCRUAL', 'RATE_LAG']));
      return box;
    }
    function otcTab() {
      var box = T.h('div', { class: 'panel' }, T.h('h2', null, 'OTC - swaps with this prime broker'));
      var book = T.book.current(); var e = (book && book.eligibility) || {};
      var mount = T.h('div'); box.appendChild(mount);
      if (e.pb_otc === false) { mount.appendChild(T.h('div', { class: 'conv' }, e.pb_otc_reason || 'not eligible', ' - ', T.conv('ELIGIBILITY'))); }
      else { T.mountOptional('swapticket', mount, { account_id: acct, book: T.book.code, notes: false }, 'the swap ticket (IRS / TRS / XCCY booking and the contracts table)'); }
      box.appendChild(notLoaded('the OTC options and forwards desk'));
      box.appendChild(T.convNote(['SWAP_MARK', 'SWAP_VM', 'SWAP_RESET']));
      return box;
    }
    function curvePanel(curve) {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'Par yield curve', curve && curve.as_of ? T.h('small', null, 'as of ' + curve.as_of + ' . ' + (curve.source || 'Treasury.gov')) : null));
      if (!curve) { p.appendChild(T.h('div', { class: 'empty' }, 'curve unavailable')); return p; }
      var row = T.h('div', { class: 'row' });
      (curve.par || []).forEach(function (pt) { row.appendChild(T.h('span', { class: 'chip', title: 'par yield ' + pt.tenor }, pt.tenor + ' ' + T.fmt.num(pt.yield, 3) + ' %')); });
      row.appendChild(T.qchip(curve.quality, 'curve quality: ' + (curve.quality || 'unknown')));
      p.appendChild(row);
      return p;
    }
    /* the page poller loads at once and every 30 s (also after a failed first load) */
    T.poll.page('markets', 30000, load);
    var off = T.state.on('quotes', function (q) {
      if (dead || !grid || !data) { return; }
      var changed = false;
      (data.rows || []).forEach(function (r) { if (mergeQuote(r, q[r.instrument_id])) { changed = true; } });
      if (changed) { grid.update(data.rows || []); }
    });
    T.pages.markets.unmount = function () { dead = true; off(); T.pages.markets.unmount = null; };
  }
  T.pages.markets = { render: render, unmount: null, columns: columns, newer: newer, posQty: posQty, isFast: isFast, sideFor: sideFor, tradable: tradable };
})();
