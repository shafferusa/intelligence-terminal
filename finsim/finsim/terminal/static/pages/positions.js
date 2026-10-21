/* Account positions (TERMINAL_DESIGN.md s17.5 #/a/{account}/positions; WP-12).  The positions poller's rows
 * (GET /api/accounts/{id}/positions = PositionView: every figure from accounting.positions(); the page computes
 * nothing).  Actions: Close (ticket prefilled with the opposite side and the full quantity), Hedge (ticket with the
 * hedge panel), Exercise (long options -> POST /api/options/exercise), Return borrow (POST /api/credit/borrows/{id}/return).
 * Detail drawer (Enter / click): GET /api/accounts/{id}/positions/{instrument} = fills, settlement, cash flows (journal
 * entries), mark history, linked hedge and borrow.  Marks render through T.pxHtml - nothing shows a bare number.
 */
(function () {
  'use strict';
  var T = window.T;
  function money(name, label) { return { key: name, label: label || name.replace(/_/g, ' '), html: function (r) { return '<span class="' + T.sign(r, name) + '">' + T.esc(T.money(r, name)) + '</span>'; }, text: function (r) { return T.money(r, name); }, sort: function (r) { return T.num(r, name); } }; }
  var COLS = [
    { key: 'symbol', label: 'instrument', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b> <span class="muted">' + T.esc(r.name || '') + '</span>'; }, text: function (r) { return (r.symbol || r.instrument_id) + ' ' + (r.name || ''); } },
    { key: 'klass', label: 'class', cls: 'l', text: function (r) { return r.klass || r.kind || ''; } },
    { key: 'qty', label: 'qty', html: function (r) { return '<span class="' + (r.qty > 0 ? 'pos' : 'neg') + '">' + T.esc(T.fmt.qty(r.qty)) + '</span>'; }, text: function (r) { return String(r.qty); }, sort: function (r) { return r.qty; } },
    { key: 'avg_px', label: 'avg cost', text: function (r) { return r.avg_px == null ? 'n/a' : T.fmt.px(r.avg_px); } },
    { key: 'mark', label: 'mark', html: function (r) { return T.pxHtml(r.mark); }, text: function (r) { return r.mark && r.mark.mark != null ? String(r.mark.mark) : ''; }, sort: function (r) { return r.mark && r.mark.mark != null ? r.mark.mark : ''; } },
    money('mv', 'market value'), money('day_pnl', 'P&L today'), money('unrealised'), money('realised_itd', 'realised'), money('income_itd', 'income'), money('financing_itd', 'financing'), money('fees_itd', 'fees'),
    { key: 'weight', label: '% NAV', text: function (r) { return r.weight == null ? '' : T.fmt.pct(r.weight, 2); }, sort: function (r) { return r.weight == null ? '' : r.weight; } },
    { key: 'days_held', label: 'days', text: function (r) { return r.days_held == null ? '' : String(r.days_held); } },
    { key: 'act', label: 'actions', cls: 'l', html: function (r) {
      var b = '<button class="btn small act" data-act="close" title="close: ticket with the opposite side, full qty">Close</button><button class="btn small act" data-act="hedge" title="ticket with the hedge panel">Hedge</button>';
      if ((r.kind === 'OPTION' || r.klass === 'listed options') && r.qty > 0) { b += '<button class="btn small act" data-act="exercise" title="exercise now (long options)">Exercise</button>'; }
      if (r.borrow && r.borrow.borrow_id) { b += '<button class="btn small act" data-act="return" title="return the borrow">Return borrow</button>'; }
      return b;
    }, text: function () { return ''; } }
  ];
  function render(el, route) {
    var acct = route.account;
    T.pageHead(el, 'Positions', acct, [T.h('span', { class: 'sub', id: 'pos-asof' })]);
    var gEl = T.h('div'); var drawer = T.h('div', { class: 'panel', hidden: true }); el.appendChild(gEl); el.appendChild(drawer);
    el.appendChild(T.convNote(['COST_BASIS', 'FIFO', 'STALE']));
    function act(kind, r) {
      if (kind === 'close') { T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, side: r.qty > 0 ? 'SELL' : 'BUY_TO_COVER', qty: Math.abs(r.qty), close: true }); }
      else if (kind === 'hedge') { T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, hedge: true, qty: Math.abs(r.qty) }); }
      else if (kind === 'exercise') {
        T.ui.confirm('Exercise ' + r.qty + ' x ' + r.instrument_id + ' now? (commission-free; the premium rolls into the stock basis)', { ok: 'Exercise' }).then(function (yes) {
          if (!yes) { return; }
          T.api.post('/api/options/exercise', { account_id: acct, instrument_id: r.instrument_id, qty: r.qty }).then(function () { T.ui.toast('exercised', 'ok'); T.poll.kick('positions'); T.poll.kick('account_detail'); }, function (e) { T.api.fail(e, 'exercise'); });
        });
      } else if (kind === 'return') {
        T.ui.confirm('Return borrow ' + r.borrow.borrow_id + ' (' + T.fmt.qty(r.borrow.qty || Math.abs(r.qty)) + ' ' + r.instrument_id + ')? The short must be covered first.', { ok: 'Return' }).then(function (yes) {
          if (!yes) { return; }
          T.api.post('/api/credit/borrows/' + encodeURIComponent(r.borrow.borrow_id) + '/return', { qty: r.borrow.qty || Math.abs(r.qty) }).then(function () { T.ui.toast('borrow returned', 'ok'); T.poll.kick('positions'); T.poll.kick('account_detail'); }, function (e) { T.api.fail(e, 'return borrow'); });
        });
      }
    }
    function detail(r) {
      drawer.hidden = false; T.clear(drawer);
      drawer.appendChild(T.h('h2', null, r.instrument_id + ' detail', T.h('button', { class: 'btn small', style: 'float:right', onclick: function () { drawer.hidden = true; } }, 'x')));
      drawer.appendChild(T.h('div', { class: 'spin' }));
      T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/positions/' + encodeURIComponent(r.instrument_id)).then(function (d) {
        T.clear(drawer);
        drawer.appendChild(T.h('h2', null, r.instrument_id + ' detail', T.h('button', { class: 'btn small', style: 'float:right', onclick: function () { drawer.hidden = true; } }, 'x')));
        var p = d.position || r;
        drawer.appendChild(T.kv([['mark', T.px(p.mark, { size: 'inline' })], ['cost', T.money(p, 'cost')], ['market value', T.money(p, 'mv')], ['unrealised', T.money(p, 'unrealised')], ['income pending', T.money(p, 'income_pending')], ['settle px (futures)', p.settle_px != null ? T.fmt.px(p.settle_px) : 'n/a']]));
        function block(title, rows, cols) {
          var b = T.h('div', { class: 'section' }, T.h('h2', null, title));
          if (!rows || !rows.length) { b.appendChild(T.h('div', { class: 'empty' }, 'none')); drawer.appendChild(b); return; }
          var g = T.h('div'); b.appendChild(g); drawer.appendChild(b);
          T.components.grid.mount(g, { tools: false, rows: rows, columns: cols });
        }
        block('fills (with the quote used)', d.fills, [
          { key: 'ts', label: 'time', cls: 'l', text: function (f) { return T.fmt.dt(f.ts); } }, { key: 'side', label: 'side', cls: 'l' }, { key: 'qty', label: 'qty', text: function (f) { return T.fmt.qty(f.qty); } },
          { key: 'price', label: 'price', text: function (f) { return T.fmt.px(f.price); } }, { key: 'fee', label: 'fee', text: function (f) { return T.money(f, 'fee'); } }, { key: 'origin', label: 'origin', cls: 'l' },
          { key: 'quote', label: 'quote used', html: function (f) { return f.quote ? T.pxHtml(f.quote, { basis: f.price_basis }) : ''; }, text: function (f) { return f.price_basis || ''; } }, { key: 'settle_date', label: 'settles', cls: 'l' }]);
        block('lots', d.lots || p.lots, [{ key: 'lot_id', label: 'lot', cls: 'l' }, { key: 'side', label: 'side', cls: 'l' }, { key: 'open_date', label: 'opened', cls: 'l' }, { key: 'qty_remaining', label: 'qty', text: function (l) { return T.fmt.qty(l.qty_remaining); } }, { key: 'unit_cost', label: 'unit cost', text: function (l) { return T.fmt.px(l.unit_cost); } }, { key: 'cost', label: 'cost', text: function (l) { return T.money(l, 'cost'); } }, { key: 'lock_until', label: 'lock', cls: 'l', text: function (l) { return l.lock_until || ''; } }]);
        block('cash flows (journal entries)', d.entries, [{ key: 'ts', label: 'time', cls: 'l', text: function (e) { return T.fmt.dt(e.ts); } }, { key: 'kind', label: 'kind', cls: 'l' }, { key: 'memo', label: 'memo', cls: 'l' }, { key: 'net', label: 'net cash', text: function (e) { var n = e.net_cash || {}; return Object.keys(n).map(function (c) { return c + ' ' + T.money(n, c); }).join(' '); } }]);
        block('mark history', d.marks_history, [{ key: 'mark_date', label: 'date', cls: 'l' }, { key: 'kind', label: 'kind', cls: 'l' }, { key: 'price', label: 'price', html: function (m) { return T.pxHtml(m); }, text: function (m) { return String(m.price); } }, { key: 'source', label: 'source', cls: 'l' }]);
        if (d.borrow) { drawer.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'linked borrow'), T.kv([['id', d.borrow.borrow_id], ['qty', T.fmt.qty(d.borrow.qty)], ['fee', T.fmt.rate(d.borrow.fee_rate)], ['rebate', T.fmt.rate(d.borrow.rebate_rate)], ['tier', d.borrow.tier], ['collateral', T.money(d.borrow, 'collateral')], ['lender', d.borrow.lender]]))); }
        if (d.hedge) { drawer.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'linked hedge'), T.kv([['package', d.hedge.package_id], ['legs', (d.hedge.legs || []).map(function (l) { return l.instrument_id + ' x' + l.qty; }).join(', ')]]))); }
      }, function (e) { T.clear(drawer); T.errorBox(drawer, e, 'position detail'); });
    }
    var grid = T.components.grid.mount(gEl, {
      columns: COLS, rows: T.state.positions || [], focus: true, csv: 'positions-' + acct + '.csv', empty: T.state.positions ? 'no positions' : 'loading ...', groupBy: function (r) { return r.klass || r.kind || ''; },
      subtotal: function (rows, g) { var mv = 0, u = 0, d = 0, any = false; rows.forEach(function (r) { var a = T.num(r, 'mv'), b = T.num(r, 'unrealised'), c = T.num(r, 'day_pnl'); if (a != null) { mv += a; any = true; } if (b != null) { u += b; } if (c != null) { d += c; } }); return any ? { symbol: 'subtotal ' + g, mv: T.esc(T.fmt.money(mv.toFixed(2))), unrealised: T.esc(T.fmt.money(u.toFixed(2))), day_pnl: T.esc(T.fmt.money(d.toFixed(2))) } : { symbol: 'subtotal ' + g }; },
      onEnter: detail, onClick: function (r, e) { var b = e.target.closest('button[data-act]'); if (b) { act(b.dataset.act, r); } },
      onKey: function (k, r) { if (k === 'b' || k === 's') { T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, side: k === 'b' ? 'BUY' : 'SELL' }); return true; } return false; }
    });
    function paint(rows) { grid.update(rows || []); var d = T.state.account_detail; var asof = T.q('#pos-asof'); if (asof) { asof.textContent = d && d.as_of ? 'as of ' + T.fmt.time(d.as_of) + (d.marks_partial ? ' . NAV excludes unpriced positions' : '') : ''; } }
    paint(T.state.positions);
    var off = T.state.on('positions', paint); var off2 = T.state.on('account_detail', function () { paint(T.state.positions); });
    T.pages.positions.unmount = function () { off(); off2(); T.pages.positions.unmount = null; };
  }
  T.pages.positions = { render: render, unmount: null, COLS: COLS };
})();
