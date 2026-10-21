/* Orders (TERMINAL_DESIGN.md s17.5 #/a/{account}/orders; WP-12): working orders (cancel with x -> DELETE /api/orders/{id},
 * resume a SUSPENDED one -> POST /api/orders/{id}/resume), history with reasons (GET ?status=all), rejections (from
 * order_rejections) and fills with the quote used (GET /api/accounts/{id}/fills; every fill carries its quote
 * provenance rendered with T.pxHtml).  Rows are the API's OrderView rows; the page computes nothing.
 */
(function () {
  'use strict';
  var T = window.T;
  function typeText(o) { return o.order_type + (o.limit_px ? ' ' + T.fmt.px(o.limit_px) : '') + (o.stop_px ? ' stop ' + T.fmt.px(o.stop_px) : '') + (o.trail_amt ? ' trail ' + T.fmt.px(o.trail_amt) : '') + (o.trail_pct ? ' trail ' + T.fmt.pct(o.trail_pct) : '') + (o.fill_mode === 'AON' ? ' AON' : ''); }
  var ORDER_COLS = [
    { key: 'created_ts', label: 'time', cls: 'l', text: function (o) { return T.fmt.dt(o.created_ts); }, sort: function (o) { return o.created_ts; } },
    { key: 'order_id', label: 'id', cls: 'l', text: function (o) { return o.order_id; } },
    { key: 'symbol', label: 'instrument', cls: 'l', text: function (o) { return o.symbol || o.instrument_id; } },
    { key: 'side', label: 'side', cls: 'l', html: function (o) { return '<span class="side-' + T.esc(o.side) + '">' + T.esc(o.side) + '</span>'; }, text: function (o) { return o.side; } },
    { key: 'qty', label: 'qty', text: function (o) { return T.fmt.qty(o.qty); } },
    { key: 'filled_qty', label: 'filled', text: function (o) { return T.fmt.qty(o.filled_qty || 0) + (o.avg_px ? ' @ ' + T.fmt.px(o.avg_px) : ''); } },
    { key: 'type', label: 'type', cls: 'l', text: typeText },
    { key: 'tif', label: 'tif', cls: 'l' },
    { key: 'status', label: 'status', cls: 'l', html: function (o) { return '<span class="st-' + T.esc(o.status) + '">' + T.esc(o.status) + '</span>'; }, text: function (o) { return o.status; } },
    { key: 'reason', label: 'reason / last event', cls: 'l', text: function (o) { return o.reason || (o.last_event && o.last_event.note) || ''; } },
    { key: 'expires_ts', label: 'expires', cls: 'l', text: function (o) { return o.expires_ts ? T.fmt.dt(o.expires_ts) : ''; } },
    { key: 'act', label: '', cls: 'l', html: function (o) {
      if (o.status === 'WORKING' || o.status === 'PARTIAL' || o.status === 'WAITING_PARENT') { return '<button class="btn small act" data-act="x" title="cancel (x)">x</button>'; }
      if (o.status === 'SUSPENDED') { return '<button class="btn small act" data-act="resume" title="resume">resume</button><button class="btn small act" data-act="x" title="cancel (x)">x</button>'; }
      return '';
    }, text: function () { return ''; } }
  ];
  function render(el, route) {
    var acct = route.account;
    T.pageHead(el, 'Orders', acct, [T.h('button', { class: 'btn', onclick: function () { T.ticket.open({ account_id: acct }); } }, 'New order')]);
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
    var actions = { onClick: function (o, e) { var b = e.target.closest('button[data-act]'); if (!b) { return; } if (b.dataset.act === 'x') { cancel(o); } else { resume(o); } }, onKey: function (k, o) { if (k === 'x' && /WORKING|PARTIAL|SUSPENDED|WAITING_PARENT/.test(o.status)) { cancel(o); return true; } return false; } };
    var wGrid = T.components.grid.mount(wEl, Object.assign({ columns: ORDER_COLS, rows: T.state.orders || [], focus: true, csv: 'orders-working.csv', empty: 'no working orders', sortKey: 'created_ts', sortAsc: false }, actions));
    var hGrid = T.components.grid.mount(hEl, Object.assign({ columns: ORDER_COLS, rows: [], csv: 'orders-history.csv', empty: 'loading ...', sortKey: 'created_ts', sortAsc: false }, actions));
    var rGrid = T.components.grid.mount(rEl, { tools: false, rows: [], empty: 'none', columns: [
      { key: 'ts', label: 'time', cls: 'l', text: function (r) { return T.fmt.dt(r.ts); } }, { key: 'instrument_id', label: 'instrument', cls: 'l' }, { key: 'side', label: 'side', cls: 'l' }, { key: 'qty', label: 'qty', text: function (r) { return T.fmt.qty(r.qty); } }, { key: 'order_type', label: 'type', cls: 'l' }, { key: 'code', label: 'code', cls: 'l' }, { key: 'message', label: 'reason', cls: 'l' }] });
    var fGrid = T.components.grid.mount(fEl, { rows: [], csv: 'fills.csv', empty: 'loading ...', sortKey: 'ts', sortAsc: false, columns: [
      { key: 'ts', label: 'time', cls: 'l', text: function (f) { return T.fmt.dt(f.ts); }, sort: function (f) { return f.ts; } }, { key: 'fill_id', label: 'fill', cls: 'l' }, { key: 'order_id', label: 'order', cls: 'l', text: function (f) { return f.order_id || ''; } },
      { key: 'instrument_id', label: 'instrument', cls: 'l' }, { key: 'side', label: 'side', cls: 'l', html: function (f) { return '<span class="side-' + T.esc(f.side) + '">' + T.esc(f.side) + '</span>'; } },
      { key: 'qty', label: 'qty', text: function (f) { return T.fmt.qty(f.qty); } }, { key: 'price', label: 'price', text: function (f) { return T.fmt.px(f.price); } },
      { key: 'gross', label: 'gross', text: function (f) { return T.money(f, 'gross'); } }, { key: 'fee', label: 'fee', text: function (f) { return T.money(f, 'fee'); } },
      { key: 'quote', label: 'quote used', html: function (f) { return f.quote ? T.pxHtml(f.quote, { basis: f.price_basis }) : '<span class="muted">' + T.esc(f.price_basis || '') + '</span>'; }, text: function (f) { return f.price_basis || ''; } },
      { key: 'cap_note', label: 'cap', cls: 'l', text: function (f) { return f.cap_note || ''; } }, { key: 'origin', label: 'origin', cls: 'l' }, { key: 'settle_date', label: 'settles', cls: 'l' }] });
    function loadAll() {
      T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/orders', { status: 'all', limit: 200 }).then(function (r) {
        var list = Array.isArray(r) ? r : (r.orders || []);
        hGrid.update(list.filter(function (o) { return !/WORKING|PARTIAL|SUSPENDED|WAITING_PARENT/.test(o.status); }));
        rGrid.update(Array.isArray(r) ? [] : (r.rejections || []));
      }, function (e) { T.clear(hEl); T.errorBox(hEl, e, 'orders history'); });
      T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/fills', { limit: 200 }).then(function (r) { fGrid.update(Array.isArray(r) ? r : (r.fills || [])); }, function (e) { T.clear(fEl); T.errorBox(fEl, e, 'fills'); });
    }
    loadAll();
    T.poll.page('orders-history', 30000, loadAll);
    var off = T.state.on('orders', function (rows) { wGrid.update(rows || []); });
    T.pages.orders.unmount = function () { off(); T.pages.orders.unmount = null; };
  }
  T.pages.orders = { render: render, unmount: null, ORDER_COLS: ORDER_COLS };
})();
