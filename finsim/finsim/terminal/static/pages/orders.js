/* Orders (TERMINAL_DESIGN.md s17.5 #/a/{account}/orders; WP-12; Build 2 P10; FINSIM1_PLAN P2 task 11): the account's working orders,
 * its order history (every ended order with its reason), its rejections (every ticket's refusals) and its fills with the quote used -
 * each section reads the trade blotters filtered to this account (GET /api/blotter/pending?account=&status=open | ended | rejected,
 * GET /api/blotter/executions?account=&record=fill) and pages with "load more" (200 rows at a time: the account's whole history is
 * reachable).  Rows are the server's; the page computes nothing.
 * - Working: actions first (they stay on screen with the ticket dock open): cancel (x), resume a held order, modify (the ticket in
 *   amend mode), replace (the ticket in replace mode), review ("would it fill now?").  Polled every 5 s while anything works.
 * - Enter / a double click on an order opens the order drawer (timeline, fills, entries, sweeps, borrow, package, preview, review);
 *   on a fill, the execution drawer (Completed Trades').
 * - FINSIM1 15.7 item 7: an as-of booking shows the AS-OF badge with its entered time and batch, a historical model line the MODEL
 *   badge; the full label is the tooltip and is listed once under its table.  A live fill reads "Simulated fill".
 * - New order opens the account's own ticket: the order ticket, the bond ticket (Debt), the FX desk (Foreign Markets & FX) or the
 *   negotiated private ticket (Private Markets; OA-20).
 */
(function () {
  'use strict';
  var T = window.T;
  var PAGE = 200;
  function wrap(text, px, title) {
    /* a long cell wraps inside its own width (min and max), so the table scrolls instead of squeezing it into one word per line */
    return '<span style="display:inline-block; min-width:' + Math.min(px, 130) + 'px; max-width:' + px + 'px; white-space:normal; overflow-wrap:anywhere; line-height:1.25"' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + T.esc(text == null ? '' : text) + '</span>';
  }
  function badge(r) {
    return r && r.badge ? ' <span class="' + (r.badge === 'MODEL' ? 'badge-model' : 'badge-asof') + '" title="' + T.esc(r.source_label || '') + '">' + T.esc(r.badge) + '</span>' : '';
  }
  function chip(r) {
    return '<span class="stage-chip stage-' + T.esc(r.stage_chip || 'queued') + '" title="' + T.esc(r.stage_label || '') + '">' + T.esc(r.stage_words || '') + '</span>';
  }
  /* the source of a row (15.7 item 7): the badge and a short form (entered time, batch); the full label is the tooltip */
  function sourceHtml(r, isFill) {
    if (!r.badge) { return isFill || (r.source_label && r.source_label !== 'Simulated fill') ? '<span class="muted">' + T.esc(r.source_label || 'Simulated fill') + '</span>' : ''; }
    var short = r.badge === 'MODEL' ? 'historical model' : 'entered ' + (r.entered_ts ? T.fmt.dt(r.entered_ts) : 'n/a') + (r.batch_id ? ', batch ' + r.batch_id : '');
    return '<span class="' + (r.badge === 'MODEL' ? 'badge-model' : 'badge-asof') + '" title="' + T.esc(r.source_label || '') + '">' + T.esc(r.badge) + '</span> <span title="' + T.esc(r.source_label || '') + '">' + T.esc(short) + '</span>';
  }
  /* one line per distinct as-of / model label under a table, with its row count */
  function sourceNotes(el, rows) {
    T.clear(el);
    var seen = {}, order = [];
    (rows || []).forEach(function (r) { if (!r.badge) { return; } var k = r.badge + '|' + r.source_label; if (!seen[k]) { seen[k] = { r: r, n: 0 }; order.push(k); } seen[k].n += 1; });
    order.forEach(function (k) {
      var it = seen[k];
      el.appendChild(T.h('div', null, T.h('span', { class: it.r.badge === 'MODEL' ? 'badge-model' : 'badge-asof' }, it.r.badge), ' ', it.r.source_label || '', ' (' + it.n + (it.n === 1 ? ' row)' : ' rows)')));
    });
  }
  function actHtml(r) {
    var a = r.actions || {}; var h = '';
    if (a.cancel) { h += '<button class="btn small act" data-act="x" title="cancel (x)">x</button>'; }
    if (a.resume) { h += '<button class="btn small act" data-act="resume" title="resume this held order">resume</button>'; }
    if (a.modify) { h += '<button class="btn small act" data-act="modify" title="change its price, quantity or time in force">mod</button>'; }
    if (a.replace) { h += '<button class="btn small act" data-act="replace" title="cancel it and place a new order in its place, in one step">repl</button>'; }
    if (a.review) { h += '<button class="btn small act" data-act="review" title="would it fill now? (nothing is written)">rev</button>'; }
    return h;
  }
  var ORDER_COLS = [
    { key: 'act', label: '', cls: 'l', html: actHtml, text: function () { return ''; } },
    { key: 'submitted_ts', label: 'time', cls: 'l', text: function (o) { return o.submitted_ts ? T.fmt.dt(o.submitted_ts) : ''; }, sort: function (o) { return o.submitted_ts || 0; } },
    { key: 'order_id', label: 'id', cls: 'l', html: function (o) { return T.esc(o.order_id || '') + badge(o); }, text: function (o) { return o.order_id; } },
    { key: 'symbol', label: 'instrument', cls: 'l', html: function (o) { return T.esc(o.symbol || o.instrument_id || '') + (o.package_id ? ' <span class="muted" title="' + T.esc(o.package_words || '') + '">' + T.esc(o.package_id) + '</span>' : ''); },
      text: function (o) { return o.symbol || o.instrument_id || ''; } },
    { key: 'side', label: 'side', cls: 'l', html: function (o) { return '<span class="side-' + T.esc(o.side || '') + '">' + T.esc(o.side_words || '') + '</span>'; }, text: function (o) { return o.side_words || ''; } },
    { key: 'qty', label: 'qty', text: function (o) { return T.fmt.qty(o.qty); } },
    { key: 'filled_qty', label: 'filled', text: function (o) { return T.fmt.qty(o.filled_qty || 0) + (o.avg_px ? ' @ ' + T.fmt.px(o.avg_px) : ''); } },
    { key: 'type_words', label: 'type', cls: 'l' },
    { key: 'expiration_words', label: 'time in force', cls: 'l', html: function (o) { return wrap(o.expiration_words || '', 160); }, text: function (o) { return o.expiration_words || ''; } },
    { key: 'stage', label: 'stage', cls: 'l', html: chip, text: function (o) { return o.stage_words || ''; } },
    { key: 'reason', label: 'reason / last event', cls: 'l', html: function (o) { var t = o.stage_label && o.stage_label !== o.stage_words ? o.stage_label : (o.reason || ''); return wrap(t, 300, t); },
      text: function (o) { return o.stage_label || o.reason || ''; } }
  ];
  var HIST_COLS = ORDER_COLS.filter(function (c) { return c.key !== 'act'; }).concat([{ key: 'source', label: 'source', cls: 'l', html: function (o) { return sourceHtml(o, false); },
    text: function (o) { return o.badge ? o.source_label : ''; } }]);
  var REJ_COLS = [
    { key: 'submitted_ts', label: 'time', cls: 'l', text: function (r) { return T.fmt.dt(r.submitted_ts); }, sort: function (r) { return r.submitted_ts || 0; } },
    { key: 'ticket', label: 'ticket', cls: 'l' },
    { key: 'symbol', label: 'instrument', cls: 'l', text: function (r) { return r.symbol || r.instrument_id || ''; } },
    { key: 'side_words', label: 'side', cls: 'l' },
    { key: 'qty', label: 'qty', text: function (r) { return r.qty == null ? '' : T.fmt.qty(r.qty); } },
    { key: 'type_words', label: 'type', cls: 'l' },
    { key: 'reason', label: 'reason', cls: 'l', html: function (r) { return wrap(r.reason || '', 520, r.reason || ''); }, text: function (r) { return r.reason || ''; } }
  ];
  /* USX-22: the quote field the fill's price came from (bid / ask / last / mid), shown on that side of the record */
  function basisField(f) {
    var b = String(f.price_basis || '').toLowerCase(); var q = f.quote || {};
    var m = /(bid|ask|last|mid)/.exec(b);
    return m && q[m[1]] != null ? m[1] : null;
  }
  function quoteUsedHtml(f) {
    if (String(f.price_basis || '') === 'as_of') {             /* an as-of booking: the owner's dealt price, not an observed quote */
      var n = (f.quote && f.quote.note) || 'the owner\'s dealt price (as-of booking)';
      return '<span class="muted" title="' + T.esc(n) + '">owner\'s price (as-of)</span>';
    }
    if (!f.quote || !f.quote.quality) { return '<span class="muted">' + T.esc(f.price_basis || '') + '</span>'; }
    var fld = basisField(f);
    return T.pxHtml(f.quote, { field: fld || undefined, basis: f.price_basis }) + (fld ? ' <span class="muted">' + T.esc(fld) + '</span>' : '');
  }
  var FILL_COLS = [
    { key: 'exec_ts', label: 'time', cls: 'l', text: function (f) { return T.fmt.dt(f.exec_ts); }, sort: function (f) { return f.exec_ts || 0; } },
    { key: 'execution_id', label: 'fill', cls: 'l', html: function (f) { return T.esc(f.execution_id) + badge(f); }, text: function (f) { return f.execution_id; } },
    { key: 'parent_id', label: 'order', cls: 'l' },
    { key: 'symbol', label: 'instrument', cls: 'l' },
    { key: 'side_words', label: 'side', cls: 'l', html: function (f) { return '<span class="side-' + T.esc(f.side || '') + '">' + T.esc(f.side_words || '') + '</span>'; }, text: function (f) { return f.side_words || ''; } },
    { key: 'qty', label: 'qty', text: function (f) { return T.fmt.qty(f.qty); } },
    { key: 'price', label: 'price', text: function (f) { return T.fmt.px(f.price); } },
    { key: 'gross', label: 'gross', text: function (f) { return T.money(f, 'gross'); } },
    { key: 'fee', label: 'fee', text: function (f) { return T.money(f, 'fee'); } },
    { key: 'quote', label: 'quote used', html: quoteUsedHtml, text: function (f) { return f.price_basis || ''; } },
    { key: 'cap_note', label: 'cap', cls: 'l', html: function (f) { return wrap(f.cap_note || '', 220, f.cap_note || ''); }, text: function (f) { return f.cap_note || ''; } },
    { key: 'status_label', label: 'status', cls: 'l' },
    { key: 'settle_date', label: 'settles', cls: 'l' },
    { key: 'source', label: 'source', cls: 'l', html: function (f) { return sourceHtml(f, true); }, text: function (f) { return f.source_label || 'Simulated fill'; } }
  ];

  function render(el, route) {
    var acct = route.account;
    var akind = T.accountKind ? T.accountKind(acct) : null;
    var dead = false;
    function newOrder() {
      var plug = (T.marketPlugins || {})[akind] || {};
      if (akind === 'PRIVATE') {
        if (T.ticketKinds && T.ticketKinds.PRIVATE) { T.ticket.open({ account_id: acct, kind: 'PRIVATE' }); return; }
        T.ui.toast('Private Markets: the negotiated ticket is on the Markets page', '', 4000); T.router.go(T.router.account(acct, 'markets')); return;
      }
      if (akind === 'CREDIT') { T.ui.toast('Credit Instruments: each desk on the Markets page carries its own ticket', '', 4000); T.router.go(T.router.account(acct, 'markets')); return; }
      if (plug.ticketKind) { T.ticket.open({ account_id: acct, kind: plug.ticketKind }); return; }
      T.ticket.open({ account_id: acct });
    }
    T.pageHead(el, 'Orders', T.accountName(acct), [T.h('button', { class: 'btn', onclick: newOrder }, 'New order')]);
    var drawerHost = T.h('div'); el.appendChild(drawerHost);
    function section(title, sub) {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, title, sub ? T.h('small', null, sub) : null));
      var g = T.h('div'); var notes = T.h('div', { class: 'conv' }); var more = T.h('button', { class: 'btn small', hidden: true }, 'load more');
      p.appendChild(g); p.appendChild(T.h('div', { class: 'row', style: 'margin:4px 0' }, more)); p.appendChild(notes);
      el.appendChild(p);
      return { g: g, notes: notes, more: more };
    }
    var sW = section('working', 'working, partly filled, held and queued orders');
    var sH = section('history', 'every ended order with its reason');
    var sR = section('rejections', 'orders and tickets the server refused; nothing was booked for them');
    var sF = section('fills', 'with the quote used');
    el.appendChild(T.convNote(['ORDER_STAGES', 'LIMIT_RULE', 'STOP_RULE', 'TRAILING', 'SESSION', 'PARTICIPATION', 'NO_VOLUME']));
    var P = function () { return T.pages.pending || {}; };
    function openOrder(o, tab) { if (o && o.row_kind === 'order' && T.orderDrawer) { T.orderDrawer(drawerHost, o.order_id, { tab: tab, onChange: reloadAll }); try { drawerHost.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ } } }
    function openFill(f) { if (f && T.executionDrawer) { T.executionDrawer(drawerHost, f.execution_id, { onChange: reloadAll }); try { drawerHost.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ } } }
    function act(o, e) {
      var b = e.target.closest('button[data-act]'); if (!b) { return; }
      var a = b.dataset.act; var pp = P();
      if (a === 'x') { if (pp.cancelOrder) { pp.cancelOrder(o, reloadAll); } }
      else if (a === 'resume') { T.api.post('/api/orders/' + encodeURIComponent(o.order_id) + '/resume', {}).then(function () { T.ui.toast('resumed ' + o.order_id, 'ok'); T.poll.kick('orders'); reloadAll(); }, function (er) { T.api.fail(er, 'resume'); }); }
      else if (a === 'modify' || a === 'replace') { if (pp.editOrder) { pp.editOrder(o, a === 'modify' ? 'amend' : 'replace'); } }
      else if (a === 'review') { openOrder(o, 'review'); }
    }
    var wGrid = T.components.grid.mount(sW.g, { columns: ORDER_COLS, rows: [], focus: true, csv: 'orders-working.csv', empty: 'loading ...', sortKey: 'submitted_ts', sortAsc: false,
      rowKey: function (o) { return o.order_id; }, onClick: act, onEnter: function (o) { openOrder(o); },
      onKey: function (k, o) { if (k === 'x' && o && o.actions && o.actions.cancel && P().cancelOrder) { P().cancelOrder(o, reloadAll); return true; } return false; } });
    var hGrid = T.components.grid.mount(sH.g, { columns: HIST_COLS, rows: [], csv: 'orders-history.csv', empty: 'loading ...', sortKey: 'submitted_ts', sortAsc: false,
      rowKey: function (o) { return o.order_id; }, onEnter: function (o) { openOrder(o); } });
    var rGrid = T.components.grid.mount(sR.g, { columns: REJ_COLS, rows: [], tools: false, empty: 'loading ...', rowKey: function (r) { return r.order_id; } });
    var fGrid = T.components.grid.mount(sF.g, { columns: FILL_COLS, rows: [], csv: 'fills.csv', empty: 'loading ...', sortKey: 'exec_ts', sortAsc: false,
      rowKey: function (f) { return f.execution_id; }, onEnter: openFill });
    var data = { w: [], h: [], r: [], f: [] };
    var anyLive = false;
    function pull(path, extra, key, grid, sec, empty, append) {
      /* a refresh re-reads every row already loaded (load more stays loaded); load more asks for the next page */
      var p = Object.assign({ account: acct, limit: append ? PAGE : Math.max(PAGE, data[key].length), offset: append ? data[key].length : 0 }, extra);
      return T.api.get(path, p).then(function (r) {
        if (dead) { return; }
        data[key] = append ? data[key].concat(r.rows || []) : (r.rows || []);
        grid.update(data[key], { empty: empty });
        sec.more.hidden = r.next_offset == null;
        sec.more.textContent = 'load more (' + data[key].length + ' of ' + (r.total || 0) + ')';
        if (key === 'h' || key === 'f') { sourceNotes(sec.notes, data[key]); }
        if (key === 'w') { anyLive = data.w.length > 0; }
      }, function (e) { if (!dead) { T.clear(sec.g); T.errorBox(sec.g, e, key === 'f' ? 'fills' : 'orders'); } });
    }
    function loadWorking(append) { return pull('/api/blotter/pending', { status: 'open' }, 'w', wGrid, sW, 'no working orders', append); }
    function loadHistory(append) { return pull('/api/blotter/pending', { status: 'ended' }, 'h', hGrid, sH, 'no ended orders yet', append); }
    function loadRejections(append) { return pull('/api/blotter/pending', { status: 'rejected' }, 'r', rGrid, sR, 'none', append); }
    function loadFills(append) { return pull('/api/blotter/executions', { record: 'fill' }, 'f', fGrid, sF, 'no fills yet', append); }
    sW.more.addEventListener('click', function () { loadWorking(true); });
    sH.more.addEventListener('click', function () { loadHistory(true); });
    sR.more.addEventListener('click', function () { loadRejections(true); });
    sF.more.addEventListener('click', function () { loadFills(true); });
    function reloadAll() { loadWorking(false); loadHistory(false); loadRejections(false); loadFills(false); }
    T.poll.page('orders-working', function () { return anyLive ? 5000 : 30000; }, function () { return loadWorking(false); });
    T.poll.page('orders-history', 30000, function () { return Promise.all([loadHistory(false), loadRejections(false), loadFills(false)]); });
    var last = 0;
    var off = T.state.on('orders', function () { var t = Date.now(); if (!dead && t - last > 2000) { last = t; loadWorking(false); } });   /* a submit / amend from the dock */
    T.pages.orders.unmount = function () { dead = true; off(); T.pages.orders.unmount = null; };
  }
  T.pages.orders = { render: render, unmount: null, ORDER_COLS: ORDER_COLS, basisField: basisField, sourceHtml: sourceHtml };
})();
