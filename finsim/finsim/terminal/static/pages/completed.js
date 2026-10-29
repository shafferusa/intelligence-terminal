/* Completed Trades (FINSIM1_PLAN P2 task 3; C7; directive section 3B; UI-08):
 * #/completed?status=&account=&asset_class=&instrument=&counterparty=&from=&to=&source=&batch=&all=1.
 * Rows: GET /api/blotter/executions - ONE row per execution, never merged: every fill (all origins), every FX deal, every OTC
 * contract booking / termination / maturity and the financing trades (stock borrows, securities lent, repos, cash loans, private
 * credit; class Financing).  The server derives every field; this page formats them and computes nothing.
 * - Columns: execution id (AS-OF / MODEL badge), parent (the order id, or T-<command> of the ticket that booked it), execution time,
 *   trade date, instrument, class, side, quantity or notional, price / rate / spread, fees, settle date, status, account,
 *   counterparty, source (Simulated fill; the as-of labels with the entered time and batch; System (...); Historical model ...).
 * - Filters: date range (trade date), instrument, account, class, counterparty, status, source and import batch (the batch list
 *   only when the import tool is in the build: GET /api/import/batches); "load more" pages 200 rows; "CSV (every column)" downloads
 *   the server's CSV of the filtered rows.
 * - Enter / a double click opens the execution drawer (GET /api/executions/{id}), tabs Trade / Cash movements / Accounting /
 *   Position / Audit.  A row with a settlement offers, when the settlement module is in the build, "Simulate counterparty fail"
 *   (the owner's simulated fail - off unless pressed) and on a failed settlement "Resolve" (retry, cash compensation, buy-in).
 * Trader wording only; ASCII; the C4 classes.
 */
(function () {
  'use strict';
  var T = window.T;
  var PAGE = 200;
  var CLASSES = ['Equity', 'ETF', 'Option', 'Future', 'Bond', 'FX', 'OTC', 'Private', 'Financing'];
  var SOURCES = [['', 'every source'], ['simulated', 'simulated fill'], ['as_of', 'as-of booking'], ['historical_model', 'historical model'], ['system', 'system']];
  var CHIP = { executed: 'working', settling: 'settling', settled: 'filled', failed: 'failed', resolved: 'filled', daily_vm: 'settling', collateral: 'filled',
               no_cash_leg: 'expired', otc_margin: 'settling', open: 'working', rolled: 'queued', closed: 'cancelled', returned: 'cancelled', recalled: 'cancelled',
               repaid: 'cancelled', terminated: 'cancelled', matured: 'cancelled', 'credit event': 'settling' };

  function wrap(text, px, title, leadHtml) {
    /* a long cell wraps inside its own width (min and max), so the table scrolls instead of squeezing it into one word per line; a badge
       (leadHtml) leads INSIDE the block - placed before it in the no-wrap cell it pushed the block past the cell's edge (1.0.6 walk) */
    return '<span style="display:inline-block; min-width:' + Math.min(px, 130) + 'px; max-width:' + px + 'px; white-space:normal; overflow-wrap:anywhere; line-height:1.25"' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + (leadHtml || '') + T.esc(text == null ? '' : text) + '</span>';
  }
  function badgeHtml(r) {
    if (!r || !r.badge) { return ''; }
    return ' <span class="' + (r.badge === 'MODEL' ? 'badge-model' : 'badge-asof') + '" title="' + T.esc(r.source_label || '') + '">' + T.esc(r.badge) + '</span>';
  }
  function statusHtml(r) {
    return '<span class="stage-chip stage-' + T.esc(CHIP[r.status] || 'working') + '" title="' + T.esc(r.status_label || '') + '">' + T.esc(r.status_label || r.status || '') + '</span>';
  }
  function qtyText(r) {
    if (r.qty != null) { return T.fmt.qty(r.qty); }
    if (r.notional != null) { return T.fmt.money(r.notional) + ' ' + (r.currency || ''); }
    return '';
  }
  function priceText(r) { return r.price == null || r.price === '' ? '' : (r.price_label === 'price' ? T.fmt.px(r.price) : r.price + ' ' + (r.price_label || '')); }
  function sourceHtml(r, px) {
    var short = r.badge ? (r.badge === 'MODEL' ? 'historical model' : 'as-of' + (r.entered_ts ? ', entered ' + T.fmt.dt(r.entered_ts) : '') + (r.batch_id ? ', batch ' + r.batch_id : '')) : (r.source_label || '');
    return wrap(short, px || 230, r.source_label || '', r.badge ? '<span class="' + (r.badge === 'MODEL' ? 'badge-model' : 'badge-asof') + '" title="' + T.esc(r.source_label || '') + '">' + T.esc(r.badge) + '</span> ' : '');
  }
  /* every field in its own column: what Copy and the grid's CSV export (P8R1-04: the table on screen stacks them, below; "CSV (every
     column)" is the server's) */
  var CSV_COLS = [
    { key: 'execution_id', label: 'execution', cls: 'l', html: function (r) { return T.esc(r.execution_id || '') + badgeHtml(r); }, text: function (r) { return r.execution_id; } },
    { key: 'parent_id', label: 'parent', cls: 'l', text: function (r) { return r.parent_id || ''; }, title: function (r) { return r.parent_kind === 'order' ? 'the order it filled' : 'the ticket (command) that booked it'; } },
    { key: 'exec_ts', label: 'executed', cls: 'l', text: function (r) { return r.exec_ts ? T.fmt.dt(r.exec_ts) : ''; }, sort: function (r) { return r.exec_ts || 0; } },
    { key: 'trade_date', label: 'trade date', cls: 'l' },
    { key: 'symbol', label: 'instrument', cls: 'l', html: function (r) { return T.esc(r.symbol || r.instrument_id || '') + (r.instrument_name && r.kind === 'otc' ? '' : ''); }, text: function (r) { return r.symbol || r.instrument_id || ''; } },
    { key: 'asset_class', label: 'class', cls: 'l' },
    { key: 'side_words', label: 'side', cls: 'l', html: function (r) { return wrap(r.side_words || '', 150); }, text: function (r) { return r.side_words || ''; } },
    { key: 'qty', label: 'qty / notional', text: qtyText, sort: function (r) { return Number(r.qty != null ? r.qty : r.notional) || 0; } },
    { key: 'price', label: 'price', text: priceText },
    { key: 'fee', label: 'fees', text: function (r) { return r.fee != null ? T.fmt.money(r.fee) : ''; }, sort: function (r) { return Number(r.fee) || 0; } },
    { key: 'settle_date', label: 'settles', cls: 'l' },
    { key: 'status', label: 'status', cls: 'l', html: statusHtml, text: function (r) { return r.status_label || ''; } },
    { key: 'account_name', label: 'account', cls: 'l', text: function (r) { return r.account_name || ''; }, title: function (r) { return r.account_id || ''; } },
    { key: 'counterparty', label: 'counterparty', cls: 'l', html: function (r) { return wrap(r.counterparty || '', 170); }, text: function (r) { return r.counterparty || ''; } },
    { key: 'source', label: 'source', cls: 'l', html: sourceHtml, text: function (r) { return r.source_label || ''; } }
  ];

  /* P8R1-04 (rule 5): the table fits 1440 px with the ticket dock open - related fields stacked in one cell (execution / parent,
     executed / trade date, instrument / class, quantity / price / fees, status / settles).  While the box is narrow (the dock open:
     NARROW_BELOW) the account / counterparty and source columns fold into the cells beside them (the account under the instrument, the
     counterparty under the side, the source in the execution's badge and tooltip) - nothing behind a scroll, nothing clipped. */
  var NARROW_BELOW = 1120;
  function l2(html, title) { return '<div class="cl2"' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + html + '</div>'; }
  function wrapped(html, px, cls) { return '<div class="clw' + (cls ? ' ' + cls : '') + '" style="max-width:' + px + 'px">' + html + '</div>'; }
  function narrowLine(html, px) { return '<div class="cl2 narrow-only' + (px ? ' clw" style="max-width:' + px + 'px' : '') + '">' + html + '</div>'; }
  var COLS = [
    { key: 'execution_id', label: 'execution / parent', cls: 'l stack', html: function (r) {
        return '<div>' + T.esc(r.execution_id || '') + badgeHtml(r) + '</div>' +
          l2(r.parent_id ? 'parent ' + T.esc(r.parent_id) : '', r.parent_kind === 'order' ? 'the order it filled' : 'the ticket (command) that booked it');
      }, text: function (r) { return (r.execution_id || '') + ' / ' + (r.parent_id || ''); }, title: function (r) { return r.source_label || ''; } },
    { key: 'exec_ts', label: 'executed / trade date', cls: 'l stack', html: function (r) {
        return '<div>' + T.esc(r.exec_ts ? T.fmt.dt(r.exec_ts) : '') + '</div>' + l2(r.trade_date ? 'trade ' + T.esc(r.trade_date) : '');
      }, text: function (r) { return (r.exec_ts ? T.fmt.dt(r.exec_ts) : '') + ' / ' + (r.trade_date || ''); }, sort: function (r) { return r.exec_ts || 0; } },
    { key: 'symbol', label: 'instrument / class', cls: 'l stack', html: function (r) {
        return wrapped(T.esc(r.symbol || r.instrument_id || ''), 150) + l2(T.esc(r.asset_class || '')) + narrowLine(T.esc(r.account_name || ''), 150);
      }, text: function (r) { return (r.symbol || r.instrument_id || '') + ' / ' + (r.asset_class || ''); } },
    { key: 'side_words', label: 'side', cls: 'l stack', html: function (r) {
        return wrapped(T.esc(r.side_words || ''), 120) + (r.counterparty ? narrowLine(T.esc(r.counterparty), 120) : '');
      }, text: function (r) { return r.side_words || ''; } },
    { key: 'qty', label: 'qty / price / fees', cls: 'num stack', html: function (r) {
        var p = priceText(r);
        return '<div>' + T.esc(qtyText(r)) + '</div>' + (p ? l2('@ ' + T.esc(p)) : '') + (r.fee != null && Number(r.fee) !== 0 ? l2('fee ' + T.esc(T.fmt.money(r.fee))) : '');
      }, text: function (r) { return qtyText(r) + (priceText(r) ? ' @ ' + priceText(r) : '') + (r.fee != null ? ' fee ' + T.fmt.money(r.fee) : ''); },
      sort: function (r) { return Number(r.qty != null ? r.qty : r.notional) || 0; } },
    { key: 'status', label: 'status / settles', cls: 'l stack', html: function (r) {
        return statusHtml(r) + (r.settle_date ? l2('settles ' + T.esc(r.settle_date)) : '');
      }, text: function (r) { return (r.status_label || '') + (r.settle_date ? ' / ' + r.settle_date : ''); } },
    { key: 'account_name', label: 'account / counterparty', cls: 'l stack wide-only', html: function (r) {
        return '<div title="' + T.esc(r.account_id || '') + '">' + T.esc(r.account_name || '') + '</div>' + (r.counterparty ? wrapped(T.esc(r.counterparty), 150, 'cl2') : '');
      }, text: function (r) { return (r.account_name || '') + ' / ' + (r.counterparty || ''); } },
    { key: 'source', label: 'source', cls: 'l stack wide-only', html: function (r) { return sourceHtml(r, 150); }, text: function (r) { return r.source_label || ''; } }
  ];

  /* ---------------------------------------------------------------- settlement actions (C15, when bound) ----------- */
  function failSettlement(s, after) {
    var reason = T.h('input', { type: 'text', placeholder: 'optional reason (e.g. counterparty did not deliver)', 'aria-label': 'reason', style: 'width:100%' });
    T.ui.modal({ title: 'Simulate a counterparty fail of ' + s.settle_id, body: function (b) {
      b.appendChild(T.h('div', null, 'A simulated counterparty fail: the settlement stays open and no cash moves until you resolve it (retry, cash compensation or buy-in). Nothing else changes.'));
      b.appendChild(T.h('div', { style: 'margin-top:6px' }, reason));
    }, actions: [{ label: 'Keep it settling' }, { label: 'Simulate the fail', cls: 'primary', id: 'cmp-fail-go', onClick: function (h) {
      h.close();
      T.api.post('/api/settlements/' + encodeURIComponent(s.settle_id) + '/fail', { reason: reason.value || null }).then(function () { T.ui.toast('settlement ' + s.settle_id + ': simulated counterparty fail', 'ok'); after(); },
        function (e) { T.api.fail(e, 'settlement fail'); });
    } }] });
  }
  function resolveSettlement(s, after) {
    var sel = T.h('select', { 'aria-label': 'resolution' }, T.h('option', { value: 'retry' }, 'retry (settle at the next settlement run)'),
      T.h('option', { value: 'cash_compensation' }, 'cash compensation (the counterparty pays an amount)'), T.h('option', { value: 'buy_in' }, 'buy-in (buy the securities elsewhere; enter the extra cost)'));
    var amt = T.h('input', { type: 'text', inputmode: 'decimal', placeholder: 'amount', 'aria-label': 'amount', hidden: true });
    sel.addEventListener('change', function () { amt.hidden = sel.value === 'retry'; amt.placeholder = sel.value === 'buy_in' ? 'extra cost' : 'compensation received'; });
    T.ui.modal({ title: 'Resolve the failed settlement ' + s.settle_id, body: function (b) {
      b.appendChild(T.h('div', null, s.label || ''));
      b.appendChild(T.h('div', { class: 'row', style: 'margin-top:6px; gap:6px' }, sel, amt));
    }, actions: [{ label: 'Cancel' }, { label: 'Resolve', cls: 'primary', id: 'cmp-resolve-go', onClick: function (h) {
      var body = { action: sel.value };
      if (sel.value === 'cash_compensation') { body.amount = amt.value; } else if (sel.value === 'buy_in') { body.cost = amt.value; }
      h.close();
      T.api.post('/api/settlements/' + encodeURIComponent(s.settle_id) + '/resolve', body).then(function () { T.ui.toast('settlement ' + s.settle_id + ' resolved (' + sel.value.replace(/_/g, ' ') + ')', 'ok'); after(); },
        function (e) { T.api.fail(e, 'settlement resolve'); });
    } }] });
  }

  /* ---------------------------------------------------------------- the execution drawer (P2 task 4) ---------------- */
  function executionDrawer(host, eid, opts) {
    opts = opts || {};
    var dead = false; var tab = 'trade'; var d = null;
    T.clear(host);
    var box = T.h('div', { class: 'drawer', role: 'region', 'aria-label': 'execution ' + eid }); host.appendChild(box);
    var head = T.h('div', { class: 'drawer-head' }, T.h('h2', null, 'execution ' + eid), T.h('span', { class: 'muted' }, 'loading ...')); box.appendChild(head);
    var tabsEl = T.h('div'); box.appendChild(tabsEl);
    var body = T.h('div'); box.appendChild(body);
    var TABS = [{ key: 'trade', label: 'Trade' }, { key: 'cash', label: 'Cash movements' }, { key: 'accounting', label: 'Accounting' }, { key: 'position', label: 'Position' },
                { key: 'audit', label: 'Audit' }];
    function close() { dead = true; T.clear(host); if (opts.onClose) { opts.onClose(); } }
    function load() {
      T.api.get('/api/executions/' + encodeURIComponent(eid)).then(function (x) { if (!dead) { d = x; paint(); } }, function (e) { if (!dead) { T.clear(body); T.errorBox(body, e, 'execution ' + eid); } });
    }
    function after() { load(); if (opts.onChange) { opts.onChange(); } }
    function paint() {
      var x = d.execution || {};
      T.clear(head);
      head.appendChild(T.h('h2', null, x.execution_id || eid));
      head.appendChild(T.h('span', { html: statusHtml(x) }));
      head.appendChild(T.h('span', null, (x.side_words || '') + ' ' + qtyText(x) + ' ' + (x.symbol || '') + (x.price ? ' @ ' + priceText(x) : '') + ' . ' + (x.account_name || '')));
      if (x.badge) { head.appendChild(T.h('span', { class: x.badge === 'MODEL' ? 'badge-model' : 'badge-asof', title: x.source_label || '' }, x.badge)); }
      head.appendChild(T.h('span', { class: 'spacer' }));
      head.appendChild(T.h('button', { class: 'btn small', title: 'close the drawer', onclick: close }, 'Close'));
      T.clear(tabsEl);
      tabsEl.appendChild(T.h('div', { class: 'drawer-tabs' }, T.tabs(TABS, tab, function (k) { tab = k; paint(); })));
      T.clear(body);
      ({ trade: paintTrade, cash: paintCash, accounting: paintAccounting, position: paintPosition, audit: paintAudit }[tab] || paintTrade)();
    }
    function paintTrade() {
      var x = d.execution || {};
      body.appendChild(T.kvGrid([['execution', x.execution_id], ['parent', x.parent_id || ''], ['instrument', (x.symbol || '') + (x.instrument_name ? ' (' + x.instrument_name + ')' : '')],
        ['class', x.asset_class || ''], ['side', x.side_words || ''], ['quantity / notional', qtyText(x)], ['price', priceText(x) || 'n/a'],
        ['gross', x.gross != null ? T.fmt.money(x.gross) : 'n/a'], ['fees', x.fee != null ? T.fmt.money(x.fee) : 'n/a'], ['executed', x.exec_ny || (x.exec_ts ? T.fmt.dt(x.exec_ts) : '')],
        ['trade date', x.trade_date || ''], ['settle date', x.settle_date || ''], ['status', x.status_label || ''], ['account', (x.account_name || '') + ' (' + (x.account_id || '') + ')'],
        ['counterparty', x.counterparty || ''], ['source', x.source_label || ''], ['note', x.note || '']].filter(function (p) { return p[1] != null && p[1] !== ''; })));
      var p = d.parent;
      if (p && p.kind === 'order' && p.order) {
        var o = p.order;
        body.appendChild(T.h('h2', { style: 'margin-top:10px' }, 'parent order ' + o.order_id + ' . ' + (o.stage_words || '') + ' . ' + (o.type_words || '') + ' . ' + (o.expiration_words || '')));
        var g = T.h('div'); body.appendChild(g);
        T.components.grid.mount(g, { tools: false, rows: o.timeline || [], columns: [{ key: 'ts', label: 'time', cls: 'l', text: function (s) { return T.fmt.dt(s.ts); } },
          { key: 'stage', label: 'step', cls: 'l', text: function (s) { return s.words || s.stage; } },
          { key: 'note', label: 'what happened', cls: 'l', html: function (s) { return wrap(s.note || '', 700); }, text: function (s) { return s.note || ''; } }] });
        if (T.orderDrawer) { body.appendChild(T.h('button', { class: 'btn small', style: 'margin-top:6px', onclick: function () { T.orderDrawer(host, o.order_id, { onClose: opts.onClose }); } }, 'Open order ' + o.order_id)); }
      } else if (p) {
        body.appendChild(T.h('div', { class: 'hint', style: 'margin-top:8px' }, (p.ticket_id || '') + ': ' + (p.words || '')));
      }
      /* P4-R3 (OMS-10): an OTC execution shows its contract's timeline - requested, quoted, accepted, booked, IM, each VM, resets, a credit event */
      if (d.timeline && d.timeline.length && T.components.swapticket && T.components.swapticket.timelineTable) {
        body.appendChild(T.h('h2', { style: 'margin-top:10px' }, 'contract timeline'));
        body.appendChild(T.components.swapticket.timelineTable(d.timeline));
      } else if (d.timeline_note) {
        body.appendChild(T.h('div', { class: 'hint', style: 'margin-top:8px' }, d.timeline_note));
      }
    }
    function paintCash() {
      var collOn = T.hasModule('collateral') && d.settlement_actions;
      body.appendChild(T.h('h2', null, 'settlement'));
      var sg = T.h('div'); body.appendChild(sg);
      T.components.grid.mount(sg, { tools: false, rows: d.settlements || [], empty: 'no settlement row (an expiry at 0, a futures fill settled by daily margin, or an OTC contract)',
        onClick: function (s, e) { var b = e.target.closest('button[data-act]'); if (!b) { return; } if (b.dataset.act === 'fail') { failSettlement(s, after); } else { resolveSettlement(s, after); } },
        columns: [{ key: 'settle_id', label: 'settlement', cls: 'l' }, { key: 'due_date', label: 'due', cls: 'l' },
          { key: 'amount', label: 'amount', text: function (s) { return T.money(s, 'amount') + ' ' + (s.currency || ''); } },
          { key: 'stage', label: 'state', cls: 'l', html: function (s) { return '<span class="stage-chip stage-' + T.esc(s.chip || 'settling') + '">' + T.esc(String(s.stage || '').toLowerCase()) + '</span> ' + wrap(s.label || '', 360); },
            text: function (s) { return s.label || ''; } },
          { key: 'act', label: '', cls: 'l', html: function (s) {
            if (!collOn) { return ''; }
            if (s.can_fail) { return '<button class="btn small" data-act="fail" title="the owner\'s simulated counterparty fail (off unless pressed)">Simulate counterparty fail</button>'; }
            if (s.can_resolve) { return '<button class="btn small" data-act="resolve">Resolve</button>'; }
            return '';
          }, text: function () { return ''; } }] });
      if (!collOn) { body.appendChild(T.h('div', { class: 'hint' }, 'Simulated counterparty fails and their resolution are unavailable: the settlement module is not in this build.')); }
      body.appendChild(T.h('h2', { style: 'margin-top:10px' }, 'transfers linked to it'));
      var tg = T.h('div'); body.appendChild(tg);
      T.components.grid.mount(tg, { tools: false, rows: d.transfers || [], empty: 'none (no sweep or transfer was needed)', columns: [{ key: 'transfer_id', label: 'transfer', cls: 'l' },
        { key: 'kind', label: 'kind', cls: 'l', text: function (t) { return T.codeWords(t.kind); } }, { key: 'business_date', label: 'business date', cls: 'l' },
        { key: 'amount', label: 'amount', text: function (t) { return T.money(t, 'amount') + ' ' + (t.currency || ''); } },
        { key: 'from_account', label: 'from', cls: 'l', text: function (t) { return t.from_account ? T.accountName(t.from_account) : ''; } },
        { key: 'to_account', label: 'to', cls: 'l', text: function (t) { return t.to_account ? T.accountName(t.to_account) : ''; } }] });
      var cash = (d.entries || []).filter(function (e) { return e.cash != null && Number(e.cash) !== 0; });
      body.appendChild(T.h('h2', { style: 'margin-top:10px' }, 'settled cash moves (GL 1010)'));
      body.appendChild(cash.length ? T.kvGrid(cash.map(function (e) { return [e.business_date + ' . entry ' + e.entry_id, T.fmt.money(e.cash) + ' . ' + T.codeWords(e.kind)]; })) : T.h('div', { class: 'muted' }, 'no settled cash has moved yet'));
    }
    function paintAccounting() {
      var g = T.h('div'); body.appendChild(g);
      T.components.grid.mount(g, { tools: false, rows: d.entries || [], empty: 'no journal entries', onEnter: openEntry, onClick: function (e) { openEntry(e); }, columns: [
        { key: 'entry_id', label: 'entry', cls: 'l' }, { key: 'business_date', label: 'business date', cls: 'l' }, { key: 'kind', label: 'kind', cls: 'l', text: function (e) { return T.codeWords(e.kind); } },
        { key: 'memo', label: 'memo', cls: 'l', html: function (e) { return wrap(e.memo || '', 640); }, text: function (e) { return e.memo || ''; } }] });
      body.appendChild(T.h('div', { class: 'hint' }, 'Enter or a click opens the entry with its Dr / Cr lines.'));
    }
    function openEntry(e) {
      if (e && T.pages.accounting && T.pages.accounting.entryModal) { T.pages.accounting.entryModal(T.api.get('/api/accounting/journal/' + encodeURIComponent(e.entry_id)), { title: 'entry ' + e.entry_id }); }
    }
    function paintPosition() {
      var p = d.position;
      if (p && p.open) {
        body.appendChild(T.kvGrid([['position now', T.fmt.qty(p.qty)], ['average cost', p.avg_px ? T.fmt.px(p.avg_px) : 'n/a'], ['cost', T.money(p, 'cost')],
          ['market value', p.mv != null ? T.fmt.money(p.mv) : 'unavailable'], ['unrealised', p.unrealised != null ? T.fmt.money(p.unrealised) : 'unavailable'],
          ['realised to date', T.money(p, 'realised_itd')]]));
        if (p.href) { body.appendChild(T.h('a', { class: 'btn small', href: p.href, style: 'margin:6px 0; display:inline-block' }, 'Open the position in ' + ((d.execution || {}).account_name || 'its account'))); }
      } else {
        body.appendChild(T.h('div', { class: 'muted' }, (p && p.note) || 'not a listed position (an FX deal, an OTC contract or a financing trade: see its own page)'));
      }
      body.appendChild(T.h('h2', { style: 'margin-top:10px' }, 'lots opened by it'));
      var g1 = T.h('div'); body.appendChild(g1);
      T.components.grid.mount(g1, { tools: false, rows: d.lots_opened || [], empty: 'none', columns: [{ key: 'lot_id', label: 'lot', cls: 'l' }, { key: 'side', label: 'side', cls: 'l' },
        { key: 'qty_open', label: 'opened', text: function (l) { return T.fmt.qty(l.qty_open); } }, { key: 'qty_remaining', label: 'open now', text: function (l) { return T.fmt.qty(l.qty_remaining); } },
        { key: 'unit_cost', label: 'unit cost', text: function (l) { return T.fmt.px(l.unit_cost); } }] });
      body.appendChild(T.h('h2', { style: 'margin-top:10px' }, 'lots it relieved (FIFO) . realised ' + T.money(d, 'realised')));
      var g2 = T.h('div'); body.appendChild(g2);
      T.components.grid.mount(g2, { tools: false, rows: d.lots_relieved || [], empty: 'none', columns: [{ key: 'lot_id', label: 'lot', cls: 'l' }, { key: 'open_fill_id', label: 'opened by', cls: 'l' },
        { key: 'qty', label: 'qty', text: function (l) { return T.fmt.qty(l.qty); } }, { key: 'cost_relieved', label: 'cost', text: function (l) { return T.money(l, 'cost_relieved'); } },
        { key: 'proceeds', label: 'proceeds', text: function (l) { return T.money(l, 'proceeds'); } }, { key: 'realised', label: 'realised', text: function (l) { return T.money(l, 'realised'); } }] });
    }
    function paintAudit() {
      var a = d.audit || {}; var c = a.command || {};
      body.appendChild(T.kvGrid([['booked by', c.kind ? T.codeWords(String(c.kind).replace(/\./g, '_').toUpperCase()) + ' (command ' + c.id + ')' : 'n/a'], ['request', c.request_id || ''],
        ['actor', c.actor || ''], ['started', c.started_ts ? T.fmt.dt(c.started_ts) : ''], ['release', c.app_version || ''], ['payload sha256', c.payload_sha256 ? String(c.payload_sha256).slice(0, 16) + ' ...' : ''],
        ['booking', a.booking ? (a.booking === 'AS_OF' ? 'as-of booking' : 'historical model') : 'live'], ['entered', a.entered_ts ? T.fmt.dt(a.entered_ts) : ''],
        ['batch', a.batch_id || ''], ['line', a.import_key || ''], ['source', a.source_label || '']].filter(function (p) { return p[1] != null && p[1] !== ''; })));
      body.appendChild(T.h('div', { class: 'hint' }, 'Every row of the ledger was written by one command; records are never edited (a correction is a new, labelled entry).'));
    }
    load();
    return { close: close };
  }
  T.executionDrawer = executionDrawer;

  /* ---------------------------------------------------------------- the page --------------------------------------- */
  function render(el, route) {
    var q = route.query || {};
    var st = { status: q.status || '', account: q.account || '', cls: q.asset_class || '', inst: q.instrument || '', cp: q.counterparty || '', from: q.from || '', to: q.to || '',
               source: q.source || '', batch: q.batch || '', all: q.all === '1', rows: [], next: null, total: 0 };
    var dead = false;
    var csvBtn = T.h('button', { class: 'btn', title: 'the server CSV of the filtered rows, every column' }, 'CSV (every column)');
    T.pageHead(el, 'Completed Trades', 'every execution, one row each', [csvBtn]);
    function inp(type, value, label, ph) { var i = T.h('input', { type: type, 'aria-label': label, placeholder: ph || '' }); i.value = value || ''; return i; }
    var fromIn = inp('date', st.from, 'from'), toIn = inp('date', st.to, 'to'), instIn = inp('search', st.inst, 'instrument', 'symbol'), cpIn = inp('search', st.cp, 'counterparty', 'counterparty');
    var acctSel = T.h('select', { 'aria-label': 'account' });
    function fillAccounts() {
      var keep = st.account; T.clear(acctSel); acctSel.appendChild(T.h('option', { value: '' }, 'all accounts'));
      (T.state.accounts || []).forEach(function (a) { if (a) { acctSel.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); } });
      if (keep && !Array.prototype.some.call(acctSel.options, function (o) { return o.value === keep; })) { acctSel.appendChild(T.h('option', { value: keep }, T.accountName(keep))); }
      acctSel.value = keep;
    }
    fillAccounts();
    var clsSel = T.h('select', { 'aria-label': 'asset class' }, T.h('option', { value: '' }, 'every class'));
    CLASSES.forEach(function (c) { clsSel.appendChild(T.h('option', { value: c }, c)); });
    clsSel.appendChild(T.h('option', { value: '!Financing' }, 'every trade but financing'));
    clsSel.value = st.cls;
    var statusSel = T.h('select', { 'aria-label': 'status' }, T.h('option', { value: '' }, 'every status'));
    var srcSel = T.h('select', { 'aria-label': 'source' });
    SOURCES.forEach(function (s) { srcSel.appendChild(T.h('option', { value: s[0] }, s[1])); });
    srcSel.value = st.source;
    var batchSel = T.h('select', { 'aria-label': 'import batch' }, T.h('option', { value: '' }, 'every batch'));
    var allBox = T.h('input', { type: 'checkbox', 'aria-label': 'all books' }); allBox.checked = st.all;
    var countEl = T.h('span', { class: 'count' });
    el.appendChild(T.h('div', { class: 'filterbar' }, T.h('label', null, 'from', fromIn), T.h('label', null, 'to', toIn), T.h('label', null, 'instrument', instIn),
      T.h('label', null, 'account', acctSel), T.h('label', null, 'class', clsSel), T.h('label', null, 'counterparty', cpIn), T.h('label', null, 'status', statusSel),
      T.h('label', null, 'source', srcSel), T.h('label', null, 'batch', batchSel), T.h('label', { title: 'every open book, not only the selected one' }, allBox, 'all books'),
      T.h('span', { class: 'spacer' }), countEl));
    var batchNote = T.h('div', { class: 'hint' }); el.appendChild(batchNote);
    function loadBatches() {
      if (!T.hasModule('importer')) {
        batchSel.disabled = true; batchSel.title = 'no import batches (the import tool is not in this build)';
        batchNote.textContent = 'no import batches (the import tool is not in this build)';
        return;
      }
      batchSel.disabled = false; batchNote.textContent = '';
      T.api.get('/api/import/batches').then(function (r) {
        if (dead) { return; }
        var keep = st.batch; T.clear(batchSel); batchSel.appendChild(T.h('option', { value: '' }, 'every batch'));
        (r.batches || []).forEach(function (b) { batchSel.appendChild(T.h('option', { value: b.batch_id }, b.batch_id + (b.state ? ' (' + b.state + ')' : (b.status ? ' (' + String(b.status).toLowerCase().replace(/_/g, ' ') + ')' : '')))); });
        batchSel.value = keep;
      }, function (e) { if (!dead) { batchNote.textContent = 'import batches unavailable: ' + T.api.describe(e); } });
    }
    loadBatches();
    var drawerHost = T.h('div'); el.appendChild(drawerHost);
    var gridEl = T.h('div'); el.appendChild(gridEl);
    var more = T.h('button', { class: 'btn small', hidden: true, onclick: function () { load(true); } }, 'load more');
    el.appendChild(T.h('div', { class: 'row', style: 'margin:6px 0' }, more));
    el.appendChild(T.convNote(['EXECUTION_STAGES', 'FILL_PRICE', 'COST_BASIS']));
    function openDrawer(r) { if (r) { executionDrawer(drawerHost, r.execution_id, { onChange: function () { load(false); } }); try { drawerHost.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ } } }
    var grid = T.components.grid.mount(gridEl, { columns: COLS, csvColumns: CSV_COLS, narrowBelow: NARROW_BELOW, rows: [], csv: 'completed-trades-visible.csv',
      empty: 'loading ...', focus: true,
      rowKey: function (r) { return r.execution_id; }, onEnter: openDrawer });
    function params(offset) {
      var p = { limit: PAGE, offset: offset || 0 };
      if (st.status) { p.status = st.status; }
      if (st.account) { p.account = st.account; }
      if (st.cls) { p.asset_class = st.cls; }
      if (st.inst) { p.instrument = st.inst.trim(); }
      if (st.cp) { p.counterparty = st.cp.trim(); }
      if (st.from) { p.from = st.from; }
      if (st.to) { p.to = st.to; }
      if (st.source) { p.source = st.source; }
      if (st.batch) { p.batch = st.batch; }
      if (st.all) { p.scope = 'ALL_BOOKS'; }
      return p;
    }
    var statusesDone = false;
    function load(append) {
      return T.api.get('/api/blotter/executions', params(append ? st.rows.length : 0)).then(function (r) {
        if (dead) { return; }
        if (!statusesDone) {
          statusesDone = true;
          (r.statuses || []).forEach(function (s) { statusSel.appendChild(T.h('option', { value: s.key }, s.label)); });
          statusSel.value = st.status;
        }
        st.rows = append ? st.rows.concat(r.rows || []) : (r.rows || []);
        st.total = r.total || 0; st.next = r.next_offset;
        grid.update(st.rows, { empty: 'no executions match' });
        countEl.textContent = st.rows.length + ' of ' + st.total;
        more.hidden = st.next == null;
      }, function (e) { if (!dead) { T.clear(gridEl); T.errorBox(gridEl, e, 'completed trades'); } });
    }
    function changed() {
      st.status = statusSel.value; st.account = acctSel.value; st.cls = clsSel.value; st.inst = instIn.value; st.cp = cpIn.value; st.from = fromIn.value; st.to = toIn.value;
      st.source = srcSel.value; st.batch = batchSel.value; st.all = allBox.checked;
      var qs = [];
      [['status', st.status], ['account', st.account], ['asset_class', st.cls], ['instrument', st.inst], ['counterparty', st.cp], ['from', st.from], ['to', st.to], ['source', st.source],
       ['batch', st.batch]].forEach(function (p) { if (p[1]) { qs.push(p[0] + '=' + encodeURIComponent(p[1])); } });
      if (st.all) { qs.push('all=1'); }
      try { history.replaceState(null, '', '#/completed' + (qs.length ? '?' + qs.join('&') : '')); } catch (e) { /* the filters still apply */ }
      load(false);
    }
    [statusSel, acctSel, clsSel, srcSel, batchSel, allBox, fromIn, toIn].forEach(function (x) { x.addEventListener('change', changed); });
    var tmr = null;
    [instIn, cpIn].forEach(function (x) { x.addEventListener('input', function () { clearTimeout(tmr); tmr = setTimeout(changed, 350); }); });
    csvBtn.addEventListener('click', function () {
      var p = params(0); delete p.limit; delete p.offset; p.format = 'csv';
      var a = T.h('a', { href: T.api.url('/api/blotter/executions', p), download: 'completed-trades.csv' });
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    });
    T.poll.page('completed', 30000, function () { return load(false); });
    var offA = T.state.on('accounts', function () { if (!dead) { fillAccounts(); } });
    var offH = T.state.on('health', function () { if (!dead && T.hasModule('importer') === batchSel.disabled) { loadBatches(); } });
    T.pages.completed.unmount = function () { dead = true; clearTimeout(tmr); offA(); offH(); T.pages.completed.unmount = null; };
  }
  T.pages.completed = { render: render, unmount: null, COLS: COLS, CSV_COLS: CSV_COLS, executionDrawer: executionDrawer };
})();
