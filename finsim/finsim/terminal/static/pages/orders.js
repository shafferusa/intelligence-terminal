/* Orders (TERMINAL_DESIGN.md s17.5 #/a/{account}/orders; WP-12; Build 2 P10): working orders (cancel with x -> DELETE
 * /api/orders/{id}, resume a SUSPENDED one -> POST /api/orders/{id}/resume), history with reasons (GET ?status=all),
 * rejections (from order_rejections) and fills with the quote used (GET /api/accounts/{id}/fills; every fill carries its
 * quote provenance rendered with T.pxHtml on the side of the quote the fill took: bid, ask or last - USX-22).  Rows are the
 * API's OrderView rows; the page computes nothing.
 * - The cancel / resume buttons are the FIRST column so they stay on screen with the ticket dock open (BM-34 / USX-29); the
 *   long 'reason / last event' text is cut to one line with the whole text on hover.
 * - A trailing stop's percent is shown as the percent it is ('trail 2.00 %', never 200.00 %; USX-14 / APIUI-06).
 * - An empty list says so once the answer arrived (never 'loading ...' forever; BM-17).
 * - New order opens the account's own ticket: the order ticket, the bond ticket (Debt), the FX desk (Foreign Markets & FX) or
 *   the negotiated private ticket (Private Markets; OA-20).
 */
(function () {
  'use strict';
  var T = window.T;
  function trailText(o) { return o.trail_amt ? ' trail ' + T.fmt.px(o.trail_amt) : (o.trail_pct ? ' trail ' + T.fmt.ratePct(o.trail_pct) : ''); }
  function typeText(o) { return o.order_type + (o.limit_px ? ' ' + T.fmt.px(o.limit_px) : '') + (o.stop_px ? ' stop ' + T.fmt.px(o.stop_px) : '') + trailText(o) + (o.fill_mode === 'AON' ? ' AON' : ''); }
  function reasonText(o) { return o.reason || (o.last_event && o.last_event.note) || ''; }
  var OPEN_RE = /WORKING|PARTIAL|SUSPENDED|WAITING_PARENT/;
  var ORDER_COLS = [
    { key: 'act', label: '', cls: 'l', html: function (o) {
      if (o.status === 'WORKING' || o.status === 'PARTIAL' || o.status === 'WAITING_PARENT') { return '<button class="btn small act" data-act="x" title="cancel (x)">x</button>'; }
      if (o.status === 'SUSPENDED') { return '<button class="btn small act" data-act="resume" title="resume">resume</button><button class="btn small act" data-act="x" title="cancel (x)">x</button>'; }
      return '';
    }, text: function () { return ''; } },
    { key: 'created_ts', label: 'time', cls: 'l', text: function (o) { return T.fmt.dt(o.created_ts); }, sort: function (o) { return o.created_ts; } },
    { key: 'order_id', label: 'id', cls: 'l', text: function (o) { return o.order_id; } },
    { key: 'symbol', label: 'instrument', cls: 'l', text: function (o) { return o.symbol || o.instrument_id; } },
    { key: 'side', label: 'side', cls: 'l', html: function (o) { return '<span class="side-' + T.esc(o.side) + '">' + T.esc(String(o.side || '').replace(/_/g, ' ')) + '</span>'; }, text: function (o) { return o.side; } },
    { key: 'qty', label: 'qty', text: function (o) { return T.fmt.qty(o.qty); } },
    { key: 'filled_qty', label: 'filled', text: function (o) { return T.fmt.qty(o.filled_qty || 0) + (o.avg_px ? ' @ ' + T.fmt.px(o.avg_px) : ''); } },
    { key: 'type', label: 'type', cls: 'l', text: typeText },
    { key: 'tif', label: 'tif', cls: 'l' },
    { key: 'status', label: 'status', cls: 'l', html: function (o) { return '<span class="st-' + T.esc(o.status) + '">' + T.esc(o.status) + '</span>'; }, text: function (o) { return o.status; } },
    { key: 'reason', label: 'reason / last event', cls: 'l', html: function (o) { var t = reasonText(o); return t.length > 64 ? '<span title="' + T.esc(t) + '">' + T.esc(t.slice(0, 62)) + ' ...</span>' : T.esc(t); }, text: reasonText },
    { key: 'expires_ts', label: 'expires', cls: 'l', text: function (o) { return o.expires_ts ? T.fmt.dt(o.expires_ts) : ''; } }
  ];
  /* USX-22: the quote field the fill's price came from (bid / ask / last / mid), shown on that side of the record */
  function basisField(f) {
    var b = String(f.price_basis || '').toLowerCase(); var q = f.quote || {};
    var m = /(bid|ask|last|mid)/.exec(b);
    return m && q[m[1]] != null ? m[1] : null;
  }
  function quoteUsedHtml(f) {
    if (!f.quote) { return '<span class="muted">' + T.esc(f.price_basis || '') + '</span>'; }
    var fld = basisField(f);
    return T.pxHtml(f.quote, { field: fld || undefined, basis: f.price_basis }) + (fld ? ' <span class="muted">' + T.esc(fld) + '</span>' : '');
  }
  function render(el, route) {
    var acct = route.account;
    var akind = T.accountKind ? T.accountKind(acct) : null;
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
    var working = T.h('div', { class: 'panel' }, T.h('h2', null, 'working')); var wEl = T.h('div'); working.appendChild(wEl);
    var hist = T.h('div', { class: 'panel' }, T.h('h2', null, 'history')); var hEl = T.h('div'); hist.appendChild(hEl);
    var rej = T.h('div', { class: 'panel' }, T.h('h2', null, 'rejections', T.h('small', null, 'orders the server refused; nothing was recorded for them'))); var rEl = T.h('div'); rej.appendChild(rEl);
    var fills = T.h('div', { class: 'panel' }, T.h('h2', null, 'fills', T.h('small', null, 'with the quote used'))); var fEl = T.h('div'); fills.appendChild(fEl);
    el.appendChild(working); el.appendChild(hist); el.appendChild(rej); el.appendChild(fills);
    el.appendChild(T.convNote(['LIMIT_RULE', 'STOP_RULE', 'TRAILING', 'SESSION', 'PARTICIPATION', 'NO_VOLUME']));
    function cancel(o) {
      T.ui.confirm('Cancel ' + o.order_id + ' (' + o.side + ' ' + T.fmt.qty(o.qty) + ' ' + (o.symbol || o.instrument_id) + ')?', { ok: 'Cancel order', cancel: 'Keep' }).then(function (yes) {
        if (!yes) { return; }
        T.api.del('/api/orders/' + encodeURIComponent(o.order_id), {}).then(function () { T.ui.toast('cancelled ' + o.order_id, 'ok'); T.poll.kick('orders'); T.poll.kick('account_detail'); loadAll(); }, function (e) { T.api.fail(e, 'cancel'); });
      });
    }
    function resume(o) {
      T.api.post('/api/orders/' + encodeURIComponent(o.order_id) + '/resume', {}).then(function () { T.ui.toast('resumed ' + o.order_id, 'ok'); T.poll.kick('orders'); loadAll(); }, function (e) { T.api.fail(e, 'resume'); });
    }
    var actions = { onClick: function (o, e) { var b = e.target.closest('button[data-act]'); if (!b) { return; } if (b.dataset.act === 'x') { cancel(o); } else { resume(o); } }, onKey: function (k, o) { if (k === 'x' && OPEN_RE.test(o.status)) { cancel(o); return true; } return false; } };
    var wSpec = Object.assign({ columns: ORDER_COLS, rows: T.state.orders || [], focus: true, csv: 'orders-working.csv', empty: T.state.orders ? 'no working orders' : 'loading ...', sortKey: 'created_ts', sortAsc: false }, actions);
    var hSpec = Object.assign({ columns: ORDER_COLS, rows: [], csv: 'orders-history.csv', empty: 'loading ...', sortKey: 'created_ts', sortAsc: false }, actions);
    var rSpec = { tools: false, rows: [], empty: 'loading ...', columns: [
      { key: 'ts', label: 'time', cls: 'l', text: function (r) { return T.fmt.dt(r.ts); } }, { key: 'instrument_id', label: 'instrument', cls: 'l' }, { key: 'side', label: 'side', cls: 'l' }, { key: 'qty', label: 'qty', text: function (r) { return T.fmt.qty(r.qty); } }, { key: 'order_type', label: 'type', cls: 'l' }, { key: 'code', label: 'code', cls: 'l' },
      { key: 'message', label: 'reason', cls: 'l', html: function (r) { var t = r.message || ''; return t.length > 80 ? '<span title="' + T.esc(t) + '">' + T.esc(t.slice(0, 78)) + ' ...</span>' : T.esc(t); }, text: function (r) { return r.message || ''; } }] };
    var fSpec = { rows: [], csv: 'fills.csv', empty: 'loading ...', sortKey: 'ts', sortAsc: false, columns: [
      { key: 'ts', label: 'time', cls: 'l', text: function (f) { return T.fmt.dt(f.ts); }, sort: function (f) { return f.ts; } }, { key: 'fill_id', label: 'fill', cls: 'l' }, { key: 'order_id', label: 'order', cls: 'l', text: function (f) { return f.order_id || ''; } },
      { key: 'instrument_id', label: 'instrument', cls: 'l' }, { key: 'side', label: 'side', cls: 'l', html: function (f) { return '<span class="side-' + T.esc(f.side) + '">' + T.esc(String(f.side || '').replace(/_/g, ' ')) + '</span>'; } },
      { key: 'qty', label: 'qty', text: function (f) { return T.fmt.qty(f.qty); } }, { key: 'price', label: 'price', text: function (f) { return T.fmt.px(f.price); } },
      { key: 'gross', label: 'gross', text: function (f) { return T.money(f, 'gross'); } }, { key: 'fee', label: 'fee', text: function (f) { return T.money(f, 'fee'); } },
      { key: 'quote', label: 'quote used', html: quoteUsedHtml, text: function (f) { return f.price_basis || ''; } },
      { key: 'cap_note', label: 'cap', cls: 'l', text: function (f) { return f.cap_note || ''; } }, { key: 'origin', label: 'origin', cls: 'l' }, { key: 'settle_date', label: 'settles', cls: 'l' }] };
    var wGrid = T.components.grid.mount(wEl, wSpec);
    var hGrid = T.components.grid.mount(hEl, hSpec);
    var rGrid = T.components.grid.mount(rEl, rSpec);
    var fGrid = T.components.grid.mount(fEl, fSpec);
    function loadAll() {
      T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/orders', { status: 'all', limit: 200 }).then(function (r) {
        var list = Array.isArray(r) ? r : (r.orders || []);
        hSpec.empty = 'no orders yet'; rSpec.empty = 'none';
        hGrid.update(list.filter(function (o) { return !OPEN_RE.test(o.status); }), { empty: hSpec.empty });
        rGrid.update(Array.isArray(r) ? [] : (r.rejections || []), { empty: rSpec.empty });
      }, function (e) { T.clear(hEl); T.errorBox(hEl, e, 'orders history'); });
      T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/fills', { limit: 200 }).then(function (r) {
        fSpec.empty = 'no fills yet';
        fGrid.update(Array.isArray(r) ? r : (r.fills || []), { empty: fSpec.empty });
      }, function (e) { T.clear(fEl); T.errorBox(fEl, e, 'fills'); });
    }
    T.poll.page('orders-history', 30000, loadAll);            /* runs at once, then every 30 s */
    var off = T.state.on('orders', function (rows) { if (rows) { wSpec.empty = 'no working orders'; } wGrid.update(rows || [], { empty: wSpec.empty }); });
    T.pages.orders.unmount = function () { off(); T.pages.orders.unmount = null; };
  }
  T.pages.orders = { render: render, unmount: null, ORDER_COLS: ORDER_COLS, typeText: typeText, basisField: basisField };
})();
