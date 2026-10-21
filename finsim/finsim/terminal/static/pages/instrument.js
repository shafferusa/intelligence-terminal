/* Instrument workspace (TERMINAL_DESIGN.md s17.5 #/a/{account}/i/{instrument}; WP-12): quote header (last, bid / ask,
 * day range, prev close, session, freshness, support / limitation text), the 1-minute chart (components/chart.js from
 * GET /api/instruments/{id}/bars), position & lots in this account (the positions poller), working orders on it (the
 * orders poller), the option chain when the instrument is optionable (components/chain.js; expiry selector; click bid /
 * ask -> ticket leg), and the ticket docked right (T.ticket.open -> components/ticket.js, WP-13; "ticket not built"
 * otherwise).  No invented content: an unavailable quote shows N/A; a LIMITED instrument shows its limitation text.
 */
(function () {
  'use strict';
  var T = window.T;
  function render(el, route) {
    var acct = route.account, id = route.instrument;
    var head = T.h('div', { class: 'panel' }); var chartEl = T.h('div'); var posEl = T.h('div', { class: 'panel' }); var ordEl = T.h('div', { class: 'panel' }); var chainEl = T.h('div', { class: 'panel' });
    T.pageHead(el, id, acct, [T.h('button', { class: 'btn', onclick: function () { T.ticket.open({ account_id: acct, instrument_id: id, side: 'BUY' }); } }, 'Buy'), T.h('button', { class: 'btn', onclick: function () { T.ticket.open({ account_id: acct, instrument_id: id, side: 'SELL' }); } }, 'Sell'), T.h('button', { class: 'btn', title: 'open the ticket with the hedge panel', onclick: function () { T.ticket.open({ account_id: acct, instrument_id: id, hedge: true }); } }, 'Hedge')]);
    el.appendChild(head); el.appendChild(chartEl); el.appendChild(T.h('div', { class: 'grid2' }, posEl, ordEl)); el.appendChild(chainEl);
    var inst = null; var chart = null; var chain = null; var expiry = null;
    T.watchQuotes([id]);
    function paintHead(d) {
      var i = d.instrument || d; var q = d.quote || T.quote(id) || null; var s = d.session || {};
      T.clear(head);
      var top = T.h('div', { class: 'row' });
      top.appendChild(T.h('span', { style: 'font-size:16px;font-weight:600;font-family:var(--mono)' }, i.symbol || id));
      top.appendChild(T.h('span', { class: 'muted' }, (i.name || '') + ' . ' + (i.kind || '') + ' . ' + (i.currency || '') + (i.multiplier && i.multiplier !== '1' ? ' . x' + i.multiplier : '')));
      if (d.support || i.support) { top.appendChild(T.stateTag(d.support || i.support)); }
      if (s.group) { top.appendChild(T.h('span', { class: 'chip ' + (s.phase || (s.open ? 'OPEN' : 'CLOSED')) }, s.group + ' . ' + (s.phase || (s.open ? 'OPEN' : 'CLOSED')) + (s.closes_at && s.open ? ' . closes ' + T.fmt.hm(s.closes_at) : ''))); }
      head.appendChild(top);
      var row = T.h('div', { class: 'row', style: 'margin-top:6px;gap:18px' });
      row.appendChild(T.h('span', null, T.px(q, { size: 'big' })));
      var kv = [];
      if (q) {
        if (q.bid != null || q.ask != null) { kv.push(['bid / ask', T.h('span', null, T.px(q, { field: 'bid', size: 'inline' }), ' / ', T.px(q, { field: 'ask', size: 'inline' }))]); }
        kv.push(['prev close', q.prev_close != null ? T.fmt.px(q.prev_close) : 'n/a']);
        kv.push(['day range', q.day_low != null && q.day_high != null ? T.fmt.px(q.day_low) + ' - ' + T.fmt.px(q.day_high) : 'n/a']);
        kv.push(['volume', q.day_volume != null ? T.fmt.qty(q.day_volume) : 'n/a']);
        kv.push(['source', (q.source || '') + (q.delay_label ? ' . ' + q.delay_label : '')]);
      } else { kv.push(['quote', 'unavailable']); }
      if (d.mark && d.mark.quality === 'computed') { kv.push(['mark', T.px(d.mark, { size: 'inline' }), 'computed mark (model) - never executable']); }
      row.appendChild(T.kv(kv));
      head.appendChild(row);
      var lim = i.limitation || d.limitation || (i.support === 'LIMITED' ? 'LIMITED: marks only, no execution in this build' : null);
      if (lim) { head.appendChild(T.h('div', { class: 'conv' }, lim)); }
      if (i.support === 'NOT_IN_THIS_BUILD') { head.appendChild(T.nib(i.name || id)); }
      if (i.spec) { head.appendChild(T.h('div', { class: 'hint' }, Object.keys(i.spec).map(function (k) { return k + ' ' + i.spec[k]; }).join(' . '))); }
      var notes = []; if (i.kind === 'FUTURE') { notes = ['FUT_SETTLE', 'FUT_MARGIN', 'CME_CAL']; } else if (i.kind === 'OPTION') { notes = ['OCC', 'EXERCISE', 'OPT_MARGIN']; } else if (i.kind === 'FX') { notes = ['FX_SPOT']; }
      if (notes.length) { head.appendChild(T.convNote(notes)); }
    }
    function paintPositions() {
      T.clear(posEl); posEl.appendChild(T.h('h2', null, 'position in ' + acct));
      var ps = (T.state.positions || []).filter(function (p) { return p.instrument_id === id; });
      if (!ps.length) { posEl.appendChild(T.h('div', { class: 'empty' }, 'no position')); return; }
      var p = ps[0];
      posEl.appendChild(T.kv([['quantity', T.fmt.qty(p.qty)], ['avg cost', p.avg_px != null ? T.fmt.px(p.avg_px) : 'n/a'], ['mark', T.px(p.mark, { size: 'inline' })], ['market value', T.money(p, 'mv')], ['unrealised', T.money(p, 'unrealised')], ['P&L today', T.money(p, 'day_pnl')], ['realised ITD', T.money(p, 'realised_itd')]]));
      if (p.lots && p.lots.length) {
        var g = T.h('div'); posEl.appendChild(g);
        T.components.grid.mount(g, { tools: false, rows: p.lots, columns: [
          { key: 'lot_id', label: 'lot', cls: 'l' }, { key: 'side', label: 'side', cls: 'l' }, { key: 'open_date', label: 'opened', cls: 'l' }, { key: 'qty_remaining', label: 'qty', text: function (r) { return T.fmt.qty(r.qty_remaining); } },
          { key: 'unit_cost', label: 'unit cost', text: function (r) { return T.fmt.px(r.unit_cost); } }, { key: 'cost', label: 'cost', text: function (r) { return T.money(r, 'cost'); } }, { key: 'lock_until', label: 'locked until', cls: 'l', text: function (r) { return r.lock_until || ''; } }] });
      }
      if (p.borrow) { posEl.appendChild(T.h('div', { class: 'hint' }, 'borrow ' + (p.borrow.borrow_id || '') + ' . fee ' + T.fmt.rate(p.borrow.fee_rate) + ' . collateral ' + T.money(p.borrow, 'collateral'))); }
    }
    function paintOrders() {
      T.clear(ordEl); ordEl.appendChild(T.h('h2', null, 'working orders on ' + id));
      var os = (T.state.orders || []).filter(function (o) { return o.instrument_id === id; });
      if (!os.length) { ordEl.appendChild(T.h('div', { class: 'empty' }, 'none')); return; }
      var g = T.h('div'); ordEl.appendChild(g);
      T.components.grid.mount(g, { tools: false, rows: os, columns: [
        { key: 'side', label: 'side', cls: 'l', html: function (o) { return '<span class="side-' + T.esc(o.side) + '">' + T.esc(o.side) + '</span>'; }, text: function (o) { return o.side; } },
        { key: 'qty', label: 'qty', text: function (o) { return T.fmt.qty(o.qty) + (o.filled_qty ? ' (' + T.fmt.qty(o.filled_qty) + ' filled)' : ''); } },
        { key: 'order_type', label: 'type', cls: 'l', text: function (o) { return o.order_type + (o.limit_px ? ' ' + T.fmt.px(o.limit_px) : '') + (o.stop_px ? ' stop ' + T.fmt.px(o.stop_px) : ''); } },
        { key: 'status', label: 'status', cls: 'l', html: function (o) { return '<span class="st-' + T.esc(o.status) + '">' + T.esc(o.status) + '</span>'; }, text: function (o) { return o.status; } },
        { key: 'x', label: '', cls: 'l', html: function () { return '<button class="btn small" data-act="x" title="cancel (x)">x</button>'; }, text: function () { return ''; } }],
        onClick: function (o, e) { if (e.target.closest('button[data-act="x"]')) { cancel(o); } }, onKey: function (k, o) { if (k === 'x') { cancel(o); return true; } return false; } });
    }
    function cancel(o) {
      T.ui.confirm('Cancel order ' + o.order_id + ' (' + o.side + ' ' + o.qty + ' ' + o.instrument_id + ')?', { ok: 'Cancel order', cancel: 'Keep' }).then(function (yes) {
        if (!yes) { return; }
        T.api.del('/api/orders/' + encodeURIComponent(o.order_id), {}).then(function () { T.ui.toast('order cancelled', 'ok'); T.poll.kick('orders'); T.poll.kick('account_detail'); }, function (e) { T.api.fail(e, 'cancel'); });
      });
    }
    function loadChain() {
      var i = inst && (inst.instrument || inst);
      if (!i || !(i.optionable || inst.optionable || inst.chain_available)) { T.clear(chainEl); chainEl.appendChild(T.h('h2', null, 'option chain')); chainEl.appendChild(T.h('div', { class: 'empty' }, 'not optionable')); return; }
      T.state.chain_target = { instrument_id: id, expiry: expiry };
      if (!chain) { T.clear(chainEl); chainEl.appendChild(T.h('h2', null, 'option chain', T.h('small', null, 'Cboe delayed; click bid / ask for a ticket leg'))); var m = T.h('div'); chainEl.appendChild(m);
        chain = T.components.chain.mount(m, { data: { pending: true }, expiry: expiry, onExpiry: function (x) { expiry = x; T.state.chain = null; T.state.chain_target = { instrument_id: id, expiry: x }; chain.update({ pending: true }); T.poll.kick('chain'); },
          onPick: function (leg) { T.ticket.open({ account_id: acct, instrument_id: leg.instrument_id, side: leg.side === 'ask' ? 'BTO' : 'STO', price: leg.price, leg: leg }); } });
        chainEl.appendChild(T.convNote(['OCC', 'AON'])); }
      T.poll.kick('chain');
    }
    T.api.get('/api/instruments/' + encodeURIComponent(id), { account: acct }).then(function (d) {
      inst = d; paintHead(d);
      chart = T.components.chart.mount(chartEl, { bars: [], note: 'loading bars ...' });
      T.api.get('/api/instruments/' + encodeURIComponent(id) + '/bars', { range: '1d' }).then(function (b) {
        var q = d.quote || {};
        chart.update({ bars: b.bars || [], prev_close: b.prev_close != null ? b.prev_close : q.prev_close, note: (b.source || q.source || 'Yahoo') + ' 1-minute bars' + (b.delay_label || q.delay_label ? ' . ' + (b.delay_label || q.delay_label) : '') + (b.session && b.session.trade_date ? ' . ' + b.session.trade_date : '') });
      }, function (e) { chart.update({ bars: [], note: 'bars unavailable: ' + (e.message || e) }); });
      loadChain();
      T.ticket.open({ account_id: acct, instrument_id: id, side: 'BUY' });
    }, function (e) { T.clear(head); T.errorBox(head, e, 'instrument'); });
    paintPositions(); paintOrders();
    var offs = [
      T.state.on('positions', paintPositions), T.state.on('orders', paintOrders),
      T.state.on('quotes', function (q) { if (inst && q[id]) { inst.quote = Object.assign({}, inst.quote || {}, q[id]); paintHead(inst); } }),
      T.state.on('chain', function (c) { if (chain && c) { chain.update(c); } })
    ];
    T.pages.instrument.unmount = function () { offs.forEach(function (f) { f(); }); if (chart && chart.destroy) { chart.destroy(); } T.state.chain_target = null; T.pages.instrument.unmount = null; };
  }
  T.pages.instrument = { render: render, unmount: null };
})();
