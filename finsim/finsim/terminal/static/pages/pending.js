/* Pending Trades (FINSIM1_PLAN P2 task 2; C7; directive section 3A; UI-07): #/pending?account=&status=&asset_class=&instrument=&all=1.
 * Rows: GET /api/blotter/pending (the server derives every field and stage; this page formats them and computes nothing).
 * - Status filter: open (default: working, partly filled, held, queued, waiting parent, and the open OTC quote requests), filled,
 *   cancelled, expired, rejected (every ticket's refusals) and all (the order history).  Account, asset class, instrument and
 *   "all books" narrow it; paging is "load more" (200 rows a page).
 * - Columns (the directive's list): actions first (they stay on screen with the ticket dock open), stage, submitted, order id, instrument
 *   (with its package), class, side, type with its limit / stop / trail, quantity / filled / open, average fill, the market from the quote
 *   cache with its freshness and the distance to the limit / stop, expiration, account, counterparty, reserved cash, reason.
 * - Row actions: cancel (x), resume a held order, modify (the ticket in amend mode), replace (the ticket in replace mode), review (the
 *   order drawer's Review tab: "would fill N at P now" or why not - nothing is written).  Enter / a double click opens the order
 *   drawer (T.orderDrawer, shared with the account Orders page and Completed Trades): timeline, fills with the quote used and their
 *   settlement, journal entries, sweeps, borrow, package legs and the stored preview.
 * - "New order" opens the docked ticket for the filtered account, or a venue picker for the book.
 * - The page polls every 5 s while anything is working, else every 30 s.
 * Trader wording only; ASCII; the C4 classes (.filterbar, .stage-chip, .badge-asof, .drawer, .drawer-tabs, .kv-grid).
 * 1.0.6.1 (walk defect P8R2-01): a stored code (SESSION_CLOSED, CALL_7PCT, SEQUENTIAL, AON ...) is shown in words - the server's
 * strategy_words / fill_mode_words / decision_words first, else words() on the text (T.codeWords per UPPER_SNAKE token); display only.
 */
(function () {
  'use strict';
  var T = window.T;
  var STATUSES = [['open', 'open'], ['filled', 'filled'], ['cancelled', 'cancelled'], ['expired', 'expired'], ['rejected', 'rejected'], ['all', 'all (order history)']];
  var CLASSES = ['Equity', 'ETF', 'Option', 'Future', 'Bond', 'FX', 'OTC', 'Private'];
  var PAGE = 200;

  /* ---------------------------------------------------------------- shared cells ---------------------------------- */
  /* every UPPER_SNAKE code inside a stored text in words (T.codeWords): "order rested; SESSION_CLOSED" -> "... session closed" */
  function words(text) {
    if (text == null) { return ''; }
    return String(text).replace(/\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/g, function (c) { return T.codeWords(c); });
  }
  function stratWords(o) { return o && o.strategy_key ? (o.strategy_words || T.codeWords(o.strategy_key)) : ''; }
  function modeWords(o) { return o && o.fill_mode ? (o.fill_mode_words || T.codeWords(o.fill_mode)) : ''; }
  function chipHtml(r) {
    var label = r.stage_label || r.stage_words || '';
    return '<span class="stage-chip stage-' + T.esc(r.stage_chip || 'queued') + '" title="' + T.esc(label) + '">' + T.esc(r.stage_words || String(r.stage || '').toLowerCase()) + '</span>';
  }
  function wrap(text, px, title) {
    /* a long cell wraps inside its own width (min and max), so the table scrolls instead of squeezing it into one word per line */
    return '<span style="display:inline-block; min-width:' + Math.min(px, 130) + 'px; max-width:' + px + 'px; white-space:normal; overflow-wrap:anywhere; line-height:1.25"' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + T.esc(text == null ? '' : text) + '</span>';
  }
  function badgeHtml(r) {
    if (!r || !r.badge) { return ''; }
    return ' <span class="' + (r.badge === 'MODEL' ? 'badge-model' : 'badge-asof') + '" title="' + T.esc(r.source_label || '') + '">' + T.esc(r.badge) + '</span>';
  }
  function marketHtml(r) {
    var m = r.market;
    if (!m) { return r.row_kind === 'order' && r.open_qty != null ? '<span class="muted" title="no quote observed yet (nothing is fetched for this table)">no quote yet</span>' : ''; }
    var opt = r.kind === 'OPTION';
    var main = opt ? (r.side === 'BTO' || r.side === 'BTC' ? 'ask' : 'bid') : 'last';
    var h = T.pxHtml(m, { field: m[main] != null ? main : 'last' }) + (opt ? ' <span class="muted">' + main + '</span>' : '');
    if (!opt && (m.bid != null || m.ask != null)) { h += ' <span class="muted" title="bid / ask">' + T.esc(T.fmt.px(m.bid) + ' / ' + T.fmt.px(m.ask)) + '</span>'; }
    return h;
  }
  function distHtml(r) {
    var d = r.distance; if (!d) { return ''; }
    var n = Number(d.pct);
    return '<span title="' + T.esc(d.words || '') + '">' + T.esc((n > 0 ? '+' : '') + T.fmt.num(n, 2) + ' %') + '</span>';
  }
  function instHtml(r) {
    var pkg = r.package_id ? '<div class="muted" title="' + T.esc(r.package_words || '') + '">' + T.esc(r.package_id + (r.strategy_key ? ' ' + stratWords(r) : '') + (r.package_words ? ' . ' + r.package_words : '')) + '</div>' : '';
    var nm = r.symbol || r.instrument_id || '';
    return T.esc(nm) + (r.instrument_name && r.instrument_name !== nm ? ' <span class="muted" title="' + T.esc(r.instrument_name) + '">' + T.esc(String(r.instrument_name).slice(0, 18)) + '</span>' : '') + pkg;
  }
  function qtyHtml(r) {
    if (r.qty == null && r.notional != null) { return T.esc(T.fmt.money(r.notional)) + ' <span class="muted">notional</span>'; }
    return T.esc(T.fmt.qty(r.qty));
  }
  function actHtml(r) {
    var a = r.actions || {}; var h = '';
    if (a.cancel) { h += '<button class="btn small act" data-act="x" title="cancel this order (x)">x</button>'; }
    if (a.resume) { h += '<button class="btn small act" data-act="resume" title="resume this held order">resume</button>'; }
    if (a.modify) { h += '<button class="btn small act" data-act="modify" title="change its price, quantity or time in force (the ticket in amend mode)">mod</button>'; }
    if (a.replace) { h += '<button class="btn small act" data-act="replace" title="cancel it and place a new order in its place, in one step">repl</button>'; }
    if (a.review) { h += '<button class="btn small act" data-act="review" title="would it fill now? (nothing is written)">rev</button>'; }
    if (r.row_kind === 'rfq') { h += '<button class="btn small act" data-act="desk" title="accept or decline the quote on the prime broker\'s OTC desk">desk</button>'; }
    return h;
  }
  /* every field in its own column: what Copy and CSV export (P8R1-04: the table on screen stacks them, below) */
  var CSV_COLS = [
    { key: 'act', label: '', cls: 'l', html: actHtml, text: function () { return ''; } },
    { key: 'stage', label: 'stage', cls: 'l', html: function (r) { return chipHtml(r) + '<div class="muted" style="min-width:130px; max-width:190px; white-space:normal; overflow-wrap:anywhere">' + T.esc(r.stage_label || '') + '</div>'; },
      text: function (r) { return (r.stage_words || '') + (r.stage_label ? ': ' + r.stage_label : ''); }, sort: function (r) { return r.stage || ''; } },
    { key: 'submitted_ts', label: 'submitted', cls: 'l', text: function (r) { return r.submitted_ts ? T.fmt.dt(r.submitted_ts) : ''; }, sort: function (r) { return r.submitted_ts || 0; } },
    { key: 'order_id', label: 'order', cls: 'l', html: function (r) { return T.esc(r.order_id || '') + badgeHtml(r); }, text: function (r) { return r.order_id; } },
    { key: 'symbol', label: 'instrument', cls: 'l', html: instHtml, text: function (r) { return (r.symbol || r.instrument_id || '') + (r.package_id ? ' ' + r.package_id : ''); } },
    { key: 'asset_class', label: 'class', cls: 'l' },
    { key: 'side_words', label: 'side', cls: 'l', html: function (r) { return '<span class="side-' + T.esc(r.side || '') + '">' + T.esc(r.side_words || '') + '</span>'; }, text: function (r) { return r.side_words || ''; } },
    { key: 'type_words', label: 'type', cls: 'l', text: function (r) { return r.type_words || ''; } },
    { key: 'qty', label: 'qty', html: qtyHtml, text: function (r) { return r.qty == null ? '' : T.fmt.qty(r.qty); }, sort: function (r) { return Number(r.qty) || 0; } },
    { key: 'filled_qty', label: 'filled', text: function (r) { return r.row_kind === 'order' ? T.fmt.qty(r.filled_qty || 0) : ''; }, sort: function (r) { return Number(r.filled_qty) || 0; } },
    { key: 'open_qty', label: 'open', text: function (r) { return r.open_qty == null ? '' : T.fmt.qty(r.open_qty); }, sort: function (r) { return Number(r.open_qty) || 0; } },
    { key: 'avg_px', label: 'avg fill', text: function (r) { return r.avg_px ? T.fmt.px(r.avg_px) : ''; } },
    { key: 'market', label: 'market', cls: 'l', html: marketHtml, text: function (r) { var m = r.market; return m ? T.fmt.px(m.last) : ''; } },
    { key: 'distance', label: 'to limit / stop', html: distHtml, text: function (r) { return r.distance ? r.distance.pct + ' %' : ''; }, sort: function (r) { return r.distance ? Number(r.distance.pct) : 0; } },
    { key: 'expiration_words', label: 'expires', cls: 'l', html: function (r) { return wrap(r.expiration_words || '', 150); }, text: function (r) { return r.expiration_words || ''; } },
    { key: 'account_name', label: 'account', cls: 'l', text: function (r) { return r.account_name || ''; }, title: function (r) { return r.account_id || ''; } },
    { key: 'counterparty', label: 'counterparty', cls: 'l', html: function (r) { return wrap(r.counterparty || '', 160); }, text: function (r) { return r.counterparty || ''; } },
    { key: 'reserved', label: 'reserved', text: function (r) { return r.reserved != null ? T.fmt.money(r.reserved) : ''; }, sort: function (r) { return Number(r.reserved) || 0; } },
    { key: 'reason', label: 'reason', cls: 'l', html: function (r) { return wrap(words(r.reason), 260, words(r.reason)); }, text: function (r) { return words(r.reason); } }
  ].filter(function (c) { return c.key !== 'act'; });

  /* P8R1-04 (rule 5): the table fits 1440 px with the ticket dock open - related fields stacked in one cell, actions first.  While the box
     is narrow (the dock open: NARROW_BELOW) the stage, account / counterparty and reservation columns fold into the cells beside them
     (the stage chip under the order, its words in the order's tooltip; the account under the instrument; the reservation under the
     quantity) - nothing behind a scroll, nothing clipped.  The export keeps one column per field (CSV_COLS). */
  var NARROW_BELOW = 1130;
  function l2(html, title) { return '<div class="cl2"' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + html + '</div>'; }
  function wrapped(html, px, cls) { return '<div class="clw' + (cls ? ' ' + cls : '') + '" style="max-width:' + px + 'px">' + html + '</div>'; }
  function narrowLine(html) { return '<div class="cl2 narrow-only">' + html + '</div>'; }
  function ownReason(r) { return r.reason && String(r.stage_label || '').indexOf(r.reason) < 0 ? words(r.reason) : ''; }      /* not when the label already says it */
  function stageWords(r) { return words(r.stage_label || r.stage_words || '') + (ownReason(r) ? ' . ' + ownReason(r) : ''); }
  function clip(text, n) { text = String(text || ''); return text.length > n ? text.slice(0, n - 3).replace(/\s+\S*$/, '') + ' ...' : text; }
  function distWords(r) {
    var d = r.distance; if (!d) { return ''; }
    var n = Number(d.pct);
    return '<span title="' + T.esc(d.words || '') + '">to ' + T.esc(d.ref_kind || 'limit') + ' ' + T.esc((n > 0 ? '+' : '') + T.fmt.num(n, 2) + ' %') + '</span>';
  }
  function marketMain(r) {
    var m = r.market;
    if (!m) { return r.row_kind === 'order' && r.open_qty != null ? '<div class="muted" title="no quote observed yet (nothing is fetched for this table)">no quote yet</div>' : ''; }
    var opt = r.kind === 'OPTION';
    var main = opt ? (r.side === 'BTO' || r.side === 'BTC' ? 'ask' : 'bid') : 'last';
    return '<div>' + T.pxHtml(m, { field: m[main] != null ? main : 'last' }) + (opt ? ' <span class="muted">' + main + '</span>' : '') + '</div>';
  }
  var COLS = [
    { key: 'act', label: '', cls: 'l stack', html: function (r) { var h = actHtml(r); return h ? '<div class="btns" style="min-width:80px; max-width:92px">' + h + '</div>' : ''; },
      text: function () { return ''; } },
    { key: 'order_id', label: 'order / submitted', cls: 'l stack', title_h: 'the order and when it was submitted; while the ticket dock is open, its stage below',
      html: function (r) { return '<div>' + T.esc(r.order_id || '') + badgeHtml(r) + '</div>' + l2(T.esc(r.submitted_ts ? T.fmt.dt(r.submitted_ts) : '')) + narrowLine(chipHtml(r)); },
      text: function (r) { return (r.order_id || '') + ' ' + (r.submitted_ts ? T.fmt.dt(r.submitted_ts) : ''); }, sort: function (r) { return r.submitted_ts || 0; },
      title: function (r) { return stageWords(r); } },
    { key: 'stage', label: 'stage', cls: 'l stack wide-only', html: function (r) {
        /* a long stage text (a refusal's whole message) shows its first words; all of it is in the tooltip and the order drawer */
        return chipHtml(r) + (r.stage_label ? wrapped(T.esc(clip(words(r.stage_label), 80)), 170, 'muted') : '') + (ownReason(r) ? wrapped(T.esc(clip(ownReason(r), 80)), 170, 'cl2') : '');
      }, text: function (r) { return stageWords(r); }, sort: function (r) { return r.stage || ''; }, title: function (r) { return stageWords(r); } },
    { key: 'symbol', label: 'instrument / class', cls: 'l stack', html: function (r) {
        var nm = r.symbol || r.instrument_id || '';
        var name = r.instrument_name && r.instrument_name !== nm ? ' <span class="muted" title="' + T.esc(r.instrument_name) + '">' + T.esc(String(r.instrument_name).slice(0, 18)) + '</span>' : '';
        var cls = T.esc(r.asset_class || '') + (r.package_id ? ' . ' + T.esc(r.package_id + (r.strategy_key ? ' ' + stratWords(r) : '')) : '');
        return wrapped(T.esc(nm) + name, 150) + wrapped(cls, 150, 'cl2') + '<div class="cl2 clw narrow-only" style="max-width:150px">' + T.esc(r.account_name || '') + '</div>';
      }, text: function (r) { return (r.symbol || r.instrument_id || '') + ' ' + (r.asset_class || '') + (r.package_id ? ' ' + r.package_id : ''); } },
    { key: 'side_words', label: 'side / type / expires', cls: 'l stack', html: function (r) {
        return '<div class="side-' + T.esc(r.side || '') + '">' + T.esc(r.side_words || '') + '</div>' + l2(T.esc(r.type_words || '')) +
          (r.expiration_words ? '<div class="cl2 clw" style="max-width:140px">' + T.esc(r.expiration_words) + '</div>' : '');
      }, text: function (r) { return [r.side_words, r.type_words, r.expiration_words].filter(Boolean).join(' / '); } },
    { key: 'qty', label: 'qty / filled / open', cls: 'num stack', html: function (r) {
        var h = '<div>' + qtyHtml(r) + '</div>';
        if (r.row_kind === 'order') {
          h += l2('filled ' + T.esc(T.fmt.qty(r.filled_qty || 0)) + (r.open_qty != null ? ' . open ' + T.esc(T.fmt.qty(r.open_qty)) : ''));
          if (r.avg_px) { h += l2('avg ' + T.esc(T.fmt.px(r.avg_px)), 'the average fill price'); }
        }
        if (r.reserved != null) { h += narrowLine('reserved ' + T.esc(T.fmt.money(r.reserved))); }
        return h;
      }, text: function (r) { return (r.qty == null ? '' : T.fmt.qty(r.qty)) + (r.row_kind === 'order' ? ' / ' + T.fmt.qty(r.filled_qty || 0) + ' / ' + (r.open_qty == null ? '' : T.fmt.qty(r.open_qty)) : ''); },
      sort: function (r) { return Number(r.qty) || 0; } },
    { key: 'market', label: 'market / to limit', cls: 'num stack', html: function (r) {
        var m = r.market;
        var h = marketMain(r);
        if (m && r.kind !== 'OPTION' && (m.bid != null || m.ask != null)) { h += l2(T.esc(T.fmt.px(m.bid) + ' / ' + T.fmt.px(m.ask)), 'bid / ask'); }
        var dw = distWords(r);
        return h + (dw ? l2(dw) : '');
      }, text: function (r) { var m = r.market; return (m ? T.fmt.px(m.last) : '') + (r.distance ? ' ' + r.distance.pct + ' %' : ''); },
      sort: function (r) { return r.distance ? Number(r.distance.pct) : 0; } },
    { key: 'account_name', label: 'account / counterparty', cls: 'l stack wide-only', html: function (r) {
        return '<div title="' + T.esc(r.account_id || '') + '">' + T.esc(r.account_name || '') + '</div>' + (r.counterparty ? wrapped(T.esc(r.counterparty), 150, 'cl2') : '');
      }, text: function (r) { return (r.account_name || '') + ' / ' + (r.counterparty || ''); } },
    { key: 'reserved', label: 'reserved', cls: 'num wide-only', text: function (r) { return r.reserved != null ? T.fmt.money(r.reserved) : ''; }, sort: function (r) { return Number(r.reserved) || 0; } }
  ];

  /* ---------------------------------------------------------------- order actions ---------------------------------- */
  function cancelOrder(r, after) {
    T.ui.confirm('Cancel ' + r.order_id + ' (' + (r.side_words || '') + ' ' + T.fmt.qty(r.qty) + ' ' + (r.symbol || r.instrument_id) + ')?', { ok: 'Cancel order', cancel: 'Keep' }).then(function (yes) {
      if (!yes) { return; }
      T.api.del('/api/orders/' + encodeURIComponent(r.order_id), {}).then(function () {
        T.ui.toast('cancelled ' + r.order_id, 'ok'); ['orders', 'account_detail', 'accounts'].forEach(function (p) { T.poll.kick(p); }); if (after) { after(); }
      }, function (e) { T.api.fail(e, 'cancel'); });
    });
  }
  function resumeOrder(r, after) {
    T.api.post('/api/orders/' + encodeURIComponent(r.order_id) + '/resume', {}).then(function () { T.ui.toast('resumed ' + r.order_id, 'ok'); T.poll.kick('orders'); if (after) { after(); } },
      function (e) { T.api.fail(e, 'resume'); });
  }
  /* the dock ticket in amend / replace mode, prefilled from the order (C7; components/ticket.js) */
  function editOrder(r, mode) {
    T.ticket.open({ account_id: r.account_id, instrument_id: r.instrument_id, kind: r.kind || undefined, order_mode: mode, order: r });
  }
  function openDesk(r) { T.router.go(T.router.account(r.account_id, 'markets')); }

  /* ---------------------------------------------------------------- the order drawer (P2 task 6) -------------------- */
  /* T.orderDrawer(host, orderId, {tab, onChange}) mounts the drawer into `host` (replacing what it held) and returns {close}. */
  function orderDrawer(host, orderId, opts) {
    opts = opts || {};
    var dead = false;
    var tab = opts.tab || 'timeline';
    T.clear(host);
    var box = T.h('div', { class: 'drawer', role: 'region', 'aria-label': 'order ' + orderId });
    host.appendChild(box);
    var head = T.h('div', { class: 'drawer-head' }, T.h('h2', null, 'order ' + orderId), T.h('span', { class: 'muted' }, 'loading ...'));
    box.appendChild(head);
    var tabsEl = T.h('div'); box.appendChild(tabsEl);
    var body = T.h('div'); box.appendChild(body);
    var d = null;
    function close() { dead = true; T.clear(host); if (opts.onClose) { opts.onClose(); } }
    function load(review) {
      return T.api.get('/api/orders/' + encodeURIComponent(orderId), review ? { review: 1 } : null).then(function (x) { if (dead) { return; } d = x; paint(); },
        function (e) { if (dead) { return; } T.clear(body); T.errorBox(body, e, 'order ' + orderId); });
    }
    var TABS = [{ key: 'timeline', label: 'Timeline' }, { key: 'fills', label: 'Fills' }, { key: 'accounting', label: 'Accounting' }, { key: 'links', label: 'Funding & links' },
                { key: 'preview', label: 'Preview' }, { key: 'review', label: 'Review' }];
    function paintHead() {
      T.clear(head);
      head.appendChild(T.h('h2', null, (d.order_id || orderId)));
      head.appendChild(T.stageChip(d.stage_chip || 'queued', d.stage_words || '', d.stage_label || ''));
      head.appendChild(T.h('span', null, (d.side_words || '') + ' ' + T.fmt.qty(d.qty) + ' ' + (d.symbol || d.instrument_id || '') + ' ' + (d.type_words || '') + ' . ' + (d.expiration_words || '')));
      if (d.badge) { head.appendChild(T.h('span', { class: d.badge === 'MODEL' ? 'badge-model' : 'badge-asof', title: d.source_label || '' }, d.badge)); }
      head.appendChild(T.h('span', { class: 'spacer' }));
      var a = d.actions || {}; var row = Object.assign({}, d, { account_id: d.account_id });
      if (a.modify) { head.appendChild(T.h('button', { class: 'btn small', onclick: function () { editOrder(row, 'amend'); } }, 'Modify')); }
      if (a.replace) { head.appendChild(T.h('button', { class: 'btn small', onclick: function () { editOrder(row, 'replace'); } }, 'Replace')); }
      if (a.resume) { head.appendChild(T.h('button', { class: 'btn small', onclick: function () { resumeOrder(row, refresh); } }, 'Resume')); }
      if (a.cancel) { head.appendChild(T.h('button', { class: 'btn small', onclick: function () { cancelOrder(row, refresh); } }, 'Cancel order')); }
      head.appendChild(T.h('button', { class: 'btn small', title: 'close the drawer', onclick: close }, 'Close'));
    }
    function refresh() { load(tab === 'review'); if (opts.onChange) { opts.onChange(); } }
    function paint() {
      paintHead();
      T.clear(tabsEl);
      tabsEl.appendChild(T.h('div', { class: 'drawer-tabs' }, T.tabs(TABS, tab, function (k) { tab = k; if (k === 'review') { load(true); } else { paint(); } })));
      T.clear(body);
      ({ timeline: paintTimeline, fills: paintFills, accounting: paintAccounting, links: paintLinks, preview: paintPreview, review: paintReview }[tab] || paintTimeline)();
    }
    function paintTimeline() {
      var kv = [['account', (d.account_name || '') + ' (' + d.account_id + ')'], ['counterparty', d.counterparty || ''], ['class', d.asset_class || ''],
                ['quantity', T.fmt.qty(d.qty) + ' . filled ' + T.fmt.qty(d.filled_qty || 0) + (d.avg_px ? ' @ ' + T.fmt.px(d.avg_px) : '') + (d.remaining != null && d.actions && d.actions.cancel ? ' . open ' + T.fmt.qty(d.remaining) : '')],
                ['reserved now', d.reserved != null ? T.fmt.money(d.reserved) : 'nothing (not working)'], ['reason', words(d.reason)],
                ['replaces', d.replaces_order_id || ''], ['replaced by', d.replaced_by_order_id || ''], ['amended', d.amend_count ? d.amend_count + ' time(s)' : '']];
      if (d.badge) { kv.push(['source', d.source_label || '']); }
      body.appendChild(T.kvGrid(kv.filter(function (p) { return p[1] != null && p[1] !== ''; })));
      var steps = d.timeline || [];
      var g = T.h('div', { style: 'margin-top:8px' }); body.appendChild(g);
      T.components.grid.mount(g, { tools: false, rows: steps, empty: 'no events', columns: [
        { key: 'ts', label: 'time', cls: 'l', text: function (s) { return T.fmt.dt(s.ts); } },
        { key: 'stage', label: 'step', cls: 'l', text: function (s) { return s.words || s.stage; } },
        { key: 'note', label: 'what happened', cls: 'l', html: function (s) { return wrap(words(s.note), 760); }, text: function (s) { return words(s.note); } }] });
    }
    function settleOf(fid) { var hit = null; (d.settlements || []).forEach(function (s) { if (s.fill_id === fid) { hit = s; } }); return hit; }
    function paintFills() {
      var g = T.h('div'); body.appendChild(g);
      var P = T.components.preview;
      T.components.grid.mount(g, { tools: false, rows: d.fills || [], empty: 'no fills', columns: [
        { key: 'ts', label: 'time', cls: 'l', text: function (f) { return T.fmt.dt(f.ts); } },
        { key: 'fill_id', label: 'fill', cls: 'l', html: function (f) { return T.esc(f.fill_id) + badgeHtml(f); }, text: function (f) { return f.fill_id; } },
        { key: 'qty', label: 'qty', text: function (f) { return T.fmt.qty(f.qty); } },
        { key: 'price', label: 'price', html: function (f) { return P && P.fillPrice ? P.fillPrice(f).outerHTML : T.esc(T.fmt.px(f.price)); }, text: function (f) { return T.fmt.px(f.price); } },
        { key: 'price_basis', label: 'basis', cls: 'l' },
        { key: 'fee', label: 'fee', text: function (f) { return T.money(f, 'fee'); } },
        { key: 'cap_note', label: 'cap', cls: 'l', html: function (f) { return wrap(f.cap_note || '', 300); }, text: function (f) { return f.cap_note || ''; } },
        { key: 'settle', label: 'settlement', cls: 'l', html: function (f) { var s = settleOf(f.fill_id); return s ? T.stageChip(s.chip || 'settling', String(s.stage || '').toLowerCase(), s.label || '').outerHTML + ' ' + wrap(s.label || '', 220) : T.esc(f.settle_date ? 'due ' + f.settle_date : 'no cash leg'); },
          text: function (f) { var s = settleOf(f.fill_id); return s ? s.label : ''; } }] });
    }
    function paintAccounting() {
      var g = T.h('div'); body.appendChild(g);
      T.components.grid.mount(g, { tools: false, rows: d.entries || [], empty: 'no journal entries (nothing has filled)', onEnter: openEntry, onClick: function (e) { openEntry(e); }, columns: [
        { key: 'entry_id', label: 'entry', cls: 'l' }, { key: 'business_date', label: 'business date', cls: 'l' },
        { key: 'kind', label: 'kind', cls: 'l', text: function (e) { return T.codeWords(e.kind); } },
        { key: 'memo', label: 'memo', cls: 'l', html: function (e) { return wrap(e.memo || '', 640); }, text: function (e) { return e.memo || ''; } }] });
      body.appendChild(T.h('div', { class: 'hint' }, 'Enter or a click opens the entry with its Dr / Cr lines.'));
    }
    function openEntry(e) {
      if (!e) { return; }
      if (T.pages.accounting && T.pages.accounting.entryModal) { T.pages.accounting.entryModal(T.api.get('/api/accounting/journal/' + encodeURIComponent(e.entry_id)), { title: 'entry ' + e.entry_id }); }
    }
    function paintLinks() {
      var sw = d.sweeps || [];
      body.appendChild(T.h('h2', null, 'sweeps from Treasury for its fills'));
      body.appendChild(sw.length ? T.kvGrid(sw.map(function (s) { return [s.transfer_id, T.money(s, 'amount') + ' ' + (s.currency || '') + ' for ' + (s.linked_id || '')]; })) : T.h('div', { class: 'muted' }, 'none'));
      body.appendChild(T.h('h2', null, 'borrow'));
      var b = d.borrow;
      body.appendChild(b ? T.kvGrid([['borrow', b.borrow_id], ['lender', b.lender || ''], ['quantity', T.fmt.qty(b.qty) + (b.qty_returned ? ' (returned ' + T.fmt.qty(b.qty_returned) + ')' : '')],
                                       ['fee', b.fee_rate != null ? T.fmt.rate(b.fee_rate) : 'n/a'], ['status', String(b.status || '').toLowerCase()]]) : T.h('div', { class: 'muted' }, 'no borrow (not a short sale)'));
      body.appendChild(T.h('h2', null, 'package'));
      var p = d.package;
      if (!p) { body.appendChild(T.h('div', { class: 'muted' }, 'not part of a package')); return; }
      body.appendChild(T.h('div', null, p.package_id + (p.strategy_key ? ' . ' + stratWords(p) : '') + ' . ' + modeWords(p) + ' . ' + (p.state_words || '')));
      var g = T.h('div'); body.appendChild(g);
      T.components.grid.mount(g, { tools: false, rows: p.legs || [], columns: [{ key: 'order_id', label: 'leg', cls: 'l' }, { key: 'instrument_id', label: 'instrument', cls: 'l' },
        { key: 'side', label: 'side', cls: 'l', text: function (l) { return String(l.side || '').replace(/_/g, ' '); } }, { key: 'qty', label: 'qty', text: function (l) { return T.fmt.qty(l.qty) + ' (' + T.fmt.qty(l.filled_qty) + ' filled)'; } },
        { key: 'type_words', label: 'type', cls: 'l' }, { key: 'stage', label: 'stage', cls: 'l', html: function (l) { return chipHtml(l); }, text: function (l) { return l.stage_words; } }] });
    }
    function paintPreview() {
      var p = d.preview_summary;
      if (!p) { body.appendChild(T.h('div', { class: 'muted' }, 'no stored preview')); return; }
      if (p.kind === 'as_of') { body.appendChild(T.h('div', null, p.note || '')); return; }
      body.appendChild(T.kvGrid([['previewed', p.as_of ? T.fmt.dt(p.as_of) : 'n/a'], ['expected price', p.expected_price ? T.fmt.px(p.expected_price) + ' (' + (p.price_basis || '') + ')' : 'n/a'],
                                 ['fill now', p.fill_now ? 'yes' : 'no (' + (p.decision_words || T.codeWords(p.decision)) + ')'], ['gross', T.money(p, 'gross')], ['fee', T.money(p, 'fee')],
                                 ['cash need', T.money(p, 'need')], ['available then', T.money(p, 'available')], ['sweep-in', T.money(p, 'sweep_in')],
                                 ['trade date', p.trade_date || ''], ['settles', p.settle_date || ''], ['session', p.session_words || T.codeWords(String(p.session_phase || '').toUpperCase())]]));
      (p.warnings || []).forEach(function (w) { body.appendChild(T.h('div', { class: 'warn-box' }, words(typeof w === 'string' ? w : (w.message || '')))); });
    }
    function paintReview() {
      var r = d.review;
      if (!r) { body.appendChild(T.h('div', { class: 'muted' }, 'reviewing ...')); return; }
      body.appendChild(T.h('div', { class: r.action === 'FILL' ? 'blue-box' : 'amber-box' }, T.h('b', null, words(r.text))));
      var m = r.market;
      body.appendChild(T.kvGrid([['observed', m ? T.h('span', null, T.px(m, { size: 'inline' })) : 'no quote observed'], ['distance', r.distance ? r.distance.words : 'n/a'],
                                 ['participation cap', words(r.cap_note) || 'n/a'], ['note', words(r.note)], ['checked at', T.fmt.dt(r.evaluated_at)]]));
      body.appendChild(T.h('div', { class: 'hint' }, 'A review runs the matching rules on the cached quote and the session now; it places nothing and writes nothing.'));
      body.appendChild(T.h('button', { class: 'btn small', onclick: function () { load(true); } }, 'Review again'));
    }
    load(tab === 'review');
    return { close: close, refresh: refresh };
  }
  T.orderDrawer = orderDrawer;

  /* ---------------------------------------------------------------- the page --------------------------------------- */
  function venuePicker() {
    var accts = (T.state.accounts || []).filter(function (a) { return a && a.status !== 'CLOSED' && T.accountKind(a) !== 'TREASURY'; });
    T.ui.modal({ title: 'New order: choose the account', body: function (b, h) {
      if (!accts.length) { b.appendChild(T.h('div', { class: 'muted' }, 'no trading account in this book')); return; }
      accts.forEach(function (a) {
        b.appendChild(T.h('div', { class: 'row', style: 'margin:4px 0' }, T.h('button', { class: 'btn', onclick: function () { h.close(); T.ticket.open({ account_id: a.account_id }); } }, a.name || a.account_id)));
      });
    } });
  }
  function render(el, route) {
    var q = route.query || {};
    var st = { status: q.status && STATUSES.some(function (s) { return s[0] === q.status; }) ? q.status : 'open', account: q.account || '', cls: q.asset_class || '', inst: q.instrument || '',
               all: q.all === '1', rows: [], total: 0, next: null, anyLive: false, drawer: null };
    var dead = false;
    var newBtn = T.h('button', { class: 'btn', title: 'open the order ticket for the chosen account', onclick: function () { if (st.account) { T.ticket.open({ account_id: st.account }); } else { venuePicker(); } } }, 'New order');
    var countsEl = T.h('span', { class: 'sub' });
    T.pageHead(el, 'Pending Trades', '', [countsEl, newBtn]);
    var statusSel = T.h('select', { 'aria-label': 'status' });
    STATUSES.forEach(function (s) { statusSel.appendChild(T.h('option', { value: s[0] }, s[1])); });
    statusSel.value = st.status;
    var acctSel = T.h('select', { 'aria-label': 'account' });
    function fillAccounts() {
      var keep = st.account; T.clear(acctSel);
      acctSel.appendChild(T.h('option', { value: '' }, 'all accounts'));
      (T.state.accounts || []).forEach(function (a) { if (a && T.accountKind(a) !== 'TREASURY') { acctSel.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); } });
      if (keep && !Array.prototype.some.call(acctSel.options, function (o) { return o.value === keep; })) { acctSel.appendChild(T.h('option', { value: keep }, T.accountName(keep))); }
      acctSel.value = keep;
    }
    fillAccounts();
    var clsSel = T.h('select', { 'aria-label': 'asset class' }, T.h('option', { value: '' }, 'every class'));
    CLASSES.forEach(function (c) { clsSel.appendChild(T.h('option', { value: c }, c)); });
    clsSel.value = st.cls;
    var instIn = T.h('input', { type: 'search', placeholder: 'symbol', 'aria-label': 'instrument', value: st.inst });
    var allBox = T.h('input', { type: 'checkbox', 'aria-label': 'all books' }); allBox.checked = st.all;
    var countEl = T.h('span', { class: 'count' });
    el.appendChild(T.h('div', { class: 'filterbar' }, T.h('label', null, 'status', statusSel), T.h('label', null, 'account', acctSel), T.h('label', null, 'class', clsSel),
      T.h('label', null, 'instrument', instIn), T.h('label', { title: 'every open book, not only the selected one' }, allBox, 'all books'), T.h('span', { class: 'spacer' }), countEl));
    var drawerHost = T.h('div'); el.appendChild(drawerHost);
    var gridEl = T.h('div'); el.appendChild(gridEl);
    var more = T.h('button', { class: 'btn small', hidden: true, onclick: function () { load(true); } }, 'load more');
    el.appendChild(T.h('div', { class: 'row', style: 'margin:6px 0' }, more));
    var notesEl = T.h('div', { class: 'hint' }); el.appendChild(notesEl);
    el.appendChild(T.convNote(['ORDER_STAGES', 'LIMIT_RULE', 'STOP_RULE', 'SESSION']));
    function openDrawer(r, tab) {
      if (!r || r.row_kind !== 'order') { if (r && r.row_kind === 'rfq') { openDesk(r); } return; }
      st.drawer = orderDrawer(drawerHost, r.order_id, { tab: tab, onChange: function () { load(false); }, onClose: function () { st.drawer = null; } });
      try { drawerHost.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ }
    }
    var grid = T.components.grid.mount(gridEl, { columns: COLS, csvColumns: CSV_COLS, narrowBelow: NARROW_BELOW, rows: [], csv: 'pending-trades.csv', empty: 'loading ...', focus: true,
      rowKey: function (r) { return r.order_id; }, onEnter: function (r) { openDrawer(r, 'timeline'); },
      onClick: function (r, e) {
        var b = e.target.closest('button[data-act]'); if (!b) { return; }
        var act = b.dataset.act;
        if (act === 'x') { cancelOrder(r, function () { load(false); }); } else if (act === 'resume') { resumeOrder(r, function () { load(false); }); }
        else if (act === 'modify') { editOrder(r, 'amend'); } else if (act === 'replace') { editOrder(r, 'replace'); }
        else if (act === 'review') { openDrawer(r, 'review'); } else if (act === 'desk') { openDesk(r); }
      },
      onKey: function (k, r) { if (k === 'x' && r && r.actions && r.actions.cancel) { cancelOrder(r, function () { load(false); }); return true; } return false; } });
    function params(offset) {
      var p = { status: st.status, limit: PAGE, offset: offset || 0 };
      if (st.account) { p.account = st.account; }
      if (st.cls) { p.asset_class = st.cls; }
      if (st.inst) { p.instrument = st.inst.trim(); }
      if (st.all) { p.scope = 'ALL_BOOKS'; }
      return p;
    }
    function paintCounts(c) {
      if (!c) { countsEl.textContent = ''; return; }
      countsEl.textContent = 'working ' + c.working + ' . partly filled ' + c.partial + ' . held ' + c.held + ' . queued ' + c.queued + ' . waiting parent ' + c.waiting_parent +
        ' . quote requests ' + c.rfq_open + ' . rejected today ' + c.rejected_today;
    }
    function load(append) {
      return T.api.get('/api/blotter/pending', params(append ? st.rows.length : 0)).then(function (r) {
        if (dead) { return; }
        st.rows = append ? st.rows.concat(r.rows || []) : (r.rows || []);
        st.total = r.total || 0; st.next = r.next_offset;
        st.anyLive = st.rows.some(function (x) { return x.row_kind === 'rfq' || (x.actions && x.actions.cancel); });
        grid.update(st.rows, { empty: st.status === 'open' ? 'nothing pending: no working, held or queued order' : 'no ' + st.status + ' orders' });
        countEl.textContent = st.rows.length + ' of ' + st.total;
        more.hidden = st.next == null;
        paintCounts(r.counts);
        T.clear(notesEl); (r.notes || []).forEach(function (n) { notesEl.appendChild(T.h('div', null, n)); });
      }, function (e) { if (!dead) { T.clear(gridEl); T.errorBox(gridEl, e, 'pending trades'); } });
    }
    function changed() {
      st.status = statusSel.value; st.account = acctSel.value; st.cls = clsSel.value; st.inst = instIn.value; st.all = allBox.checked;
      var qs = ['status=' + encodeURIComponent(st.status)];
      if (st.account) { qs.push('account=' + encodeURIComponent(st.account)); }
      if (st.cls) { qs.push('asset_class=' + encodeURIComponent(st.cls)); }
      if (st.inst) { qs.push('instrument=' + encodeURIComponent(st.inst)); }
      if (st.all) { qs.push('all=1'); }
      try { history.replaceState(null, '', '#/pending?' + qs.join('&')); } catch (e) { /* the filters still apply */ }
      load(false);
    }
    [statusSel, acctSel, clsSel, allBox].forEach(function (x) { x.addEventListener('change', changed); });
    var tmr = null; instIn.addEventListener('input', function () { clearTimeout(tmr); tmr = setTimeout(changed, 350); });
    T.poll.page('pending', function () { return st.anyLive ? 5000 : 30000; }, function () { return load(false); });
    var offA = T.state.on('accounts', function () { if (!dead) { fillAccounts(); } });
    var lastLoad = 0;
    var offO = T.state.on('orders', function () { var t = Date.now(); if (!dead && t - lastLoad > 2000) { lastLoad = t; load(false); } });   /* a ticket submit / amend elsewhere */
    T.pages.pending.unmount = function () { dead = true; clearTimeout(tmr); offA(); offO(); T.pages.pending.unmount = null; };
  }
  T.pages.pending = { render: render, unmount: null, COLS: COLS, CSV_COLS: CSV_COLS, orderDrawer: orderDrawer, editOrder: editOrder, cancelOrder: cancelOrder };
})();
