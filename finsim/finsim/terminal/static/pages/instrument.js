/* Instrument workspace (TERMINAL_DESIGN.md s17.5 #/a/{account}/i/{instrument}; WP-12; Build 2 P10): quote header (last,
 * bid / ask, day range, prev close, session, freshness, support / limitation text, a curated and labelled spec), the session's
 * 1-minute chart (components/chart.js from GET /api/instruments/{id}/bars - a 202 'fetching' is asked again until the bars
 * arrive; refreshed every 2 min while the session is open), position & lots in this account (the positions poller; an OTC
 * contract's quantity in its own units with their label and a TRS's notional beside it, P8L-02), working
 * orders on it (the orders poller), the option chain when the instrument is optionable (components/chain.js; opens on the
 * nearest expiry 7-30 days out with the at-the-money row in view; click bid / ask -> ticket leg), and the ticket docked right.
 *
 * - Kinds without an exchange quote (BOND, PRIVATE, swaps and OTC contracts; BUILD2_PLAN C5) show their MARK as the headline
 *   (the module's instrument_extra headline, else the mark: a private company's last reported round with its date and
 *   source; a bond's FedInvest / model price) and no Yahoo source, chart, session chip or option-chain box (OA-21).
 * - The ticket opens on load without forcing a side (an option opens BTO; ORC-2), through T.ticket.open with the instrument's
 *   kind, so a BOND / FX / PRIVATE / SWAP instrument reaches its own ticket (C11).  A ticket prefill left by another page for
 *   this instrument (T.state.ticket_prefill: a hedge leg that trades here) is used instead.
 * - Buttons per kind: Buy / Sell (options BTO / STO) / Hedge (a position held: the hedge panel in 'hedge this position'
 *   mode, USX-12; nothing held: the panel for a new order); a bond's Buy / Sell open the bond ticket; FX opens the FX desk;
 *   a private company the negotiated private ticket; a swap Terminate on the OTC tab.
 * - A quote that has not arrived yet reads 'fetching quote ...' (never N/A); a listing quoted in a minor unit shows it
 *   (GBp 2,700.00 with the GBP value on hover; OA-15).  Nothing is computed here: every figure is the server's.
 */
(function () {
  'use strict';
  var T = window.T;
  var VALUATION = { PRIVATE: 1, BOND: 1, SWAP: 1, IRS: 1, TRS: 1, XCCY: 1, EQ_OPTION: 1, FX_OPTION: 1, EQ_FORWARD: 1, FX_FORWARD: 1 };
  var OTC = { SWAP: 1, IRS: 1, TRS: 1, XCCY: 1, EQ_OPTION: 1, FX_OPTION: 1, EQ_FORWARD: 1, FX_FORWARD: 1 };
  var EQUITY = { EQUITY: 1, ETF: 1, ADR: 1, REIT: 1, PREFERRED: 1 };
  var NOTES = { FUTURE: ['FUT_SETTLE', 'FUT_MARGIN', 'CME_CAL'], OPTION: ['OCC', 'EXERCISE', 'OPT_MARGIN'], FX: ['FX_SPOT'] };
  var BARS_REFRESH_MS = 120000, BARS_TRIES = 30;

  function decimals(x) { var s = String(x == null ? '' : x).trim(); var i = s.indexOf('.'); if (i < 0) { return 0; } return s.slice(i + 1).replace(/0+$/, '').length; }
  /* price decimals from the tick (0.01 -> 2, 0.0001 -> 4, 0.25 -> 2), at least 2, at most 6 */
  function dpOf(i) { var d = decimals(i && i.tick); if (i && i.kind === 'FX' && d < 4) { d = 4; } return Math.max(2, Math.min(6, d)); }
  /* the minor-unit options of a listing quoted in pence etc. (OA-15) */
  function unitOpts(i) {
    var sp = (i && i.spec) || {};
    var scale = i && i.price_scale != null ? Number(i.price_scale) : 1;
    return sp.quote_unit && isFinite(scale) && scale !== 1 ? { unit: sp.quote_unit, scale: scale, ccy: i.currency } : {};
  }
  function unitText(i, v) { var u = unitOpts(i); return (u.unit ? u.unit + ' ' : '') + T.fmt.px(v); }
  function present(v) { return v != null && v !== '' && v !== 'null' && !(typeof v === 'number' && !isFinite(v)); }
  /* USX-33: a curated, labelled spec - the product module's spec_view (C5) when it sends one, else the fields that matter for
     the kind with readable labels; a null / empty value is never shown */
  function specRows(i, d) {
    var ex = d && d.extra;
    if (ex && Array.isArray(ex.spec_view) && ex.spec_view.length) {
      return ex.spec_view.filter(function (r) { return r && r.label && present(r.value); }).map(function (r) { return [String(r.label), String(r.value), r.title || '']; });
    }
    var s = (i && i.spec) || {}; var out = [];
    function add(label, v, title) { if (present(v)) { out.push([label, String(v), title || '']); } }
    var kind = i.kind;
    if (EQUITY[kind]) {
      add('exchange', s.full_exchange || s.exchange);
      if (s.foreign) { add('venue', s.venue); add('quoted in', s.quote_unit, s.quote_unit_note); }
      add('sector', s.sector); add('country', s.country);
      add('shares outstanding', present(s.shares_outstanding) ? T.fmt.qty(s.shares_outstanding) + (present(s.shares_asof) ? ' (as of ' + s.shares_asof + ')' : '') : null, 'SEC filing figure in the universe file');
      add('SEC CIK', s.cik);
      add('beta', present(s.beta) ? T.fmt.num(s.beta, 2) + ' (static)' : null, 'universe file; used by the index-overlay hedge');
      add('dividend yield', present(s.dividend_yield) ? T.fmt.rate(s.dividend_yield) + ' (trailing)' : null, 'universe file, trailing 12 months');
      add('avg daily volume', present(s.adv) ? T.fmt.qty(s.adv) + ' shares' : null, 'universe file');
      add('settlement', present(i.settle_days) ? 'T+' + i.settle_days + (i.currency && i.currency !== 'USD' ? ' in ' + i.currency : '') : null);
      if (s.limited_reason) { add('limited', s.limited_reason); }
    } else if (kind === 'FUTURE') {
      add('contract', i.name); add('root', s.root);
      add('multiplier', present(i.multiplier) ? T.fmt.num(i.multiplier, 0) : null);
      add('tick', present(i.tick) ? i.tick + (present(i.multiplier) ? ' (' + T.fmt.money(String(Number(i.tick) * Number(i.multiplier)), 2) + ' per contract)' : '') : null);
      add('last trade', s.last_trade || i.expiry);
      add('daily settlement', present(s.settle_time_et) ? s.settle_time_et + ' ET (variation margin)' : null);
      add('initial margin', present(s.im_c) ? T.fmt.cents(s.im_c) + ' per contract' : null, 'static exchange-style margin table (FUT_MARGIN)');
      add('maintenance', present(s.mm_c) ? T.fmt.cents(s.mm_c) + ' per contract' : null);
      add('trade date', s.trade_date_rule === 'next_business_day_after_18:00' ? 'trades after 18:00 ET count for the next business day' : s.trade_date_rule);
      add('note', s.note);
    } else if (kind === 'OPTION') {
      add('underlying', i.underlying_id); add('expiry', i.expiry); add('strike', present(i.strike) ? T.fmt.px(i.strike) : null);
      add('right', i.right === 'C' ? 'call' : (i.right === 'P' ? 'put' : i.right));
      add('style', i.style ? String(i.style).toLowerCase() : null);
      add('exercise', i.exercise === 'CASH' ? 'cash-settled' : (i.exercise ? 'physical: ' + T.fmt.num(i.multiplier || 100, 0) + ' shares per contract' : null));
      add('multiplier', present(i.multiplier) ? T.fmt.num(i.multiplier, 0) : null);
    } else if (kind === 'INDEX') {
      add('reference', s.reference ? 'reference only (not tradeable)' : null); add('note', s.note);
    } else if (kind === 'FX') {
      add('pair', i.instrument_id); add('settlement', 'spot T+2 (USD/CAD T+1) on the FX desk');
    } else {
      Object.keys(s).forEach(function (k) { if (/^(note|issuer|sector|country|maturity|coupon)$/.test(k)) { add(k, s[k]); } });
    }
    if (present(i.lot) && Number(i.lot) !== 1) { add('lot', T.fmt.qty(i.lot)); }
    return out;
  }
  function sideFor(kind, dir) { return kind === 'OPTION' ? (dir === 'b' ? 'BTO' : 'STO') : (dir === 'b' ? 'BUY' : 'SELL'); }

  function render(el, route) {
    var acct = route.account, id = route.instrument;
    var head = T.h('div', { class: 'panel' }); var chartEl = T.h('div'); var posEl = T.h('div', { class: 'panel' }); var ordEl = T.h('div', { class: 'panel' }); var chainEl = T.h('div', { class: 'panel' });
    var actions = T.h('span', { class: 'row', style: 'gap:6px' });
    T.pageHead(el, id, T.accountName(acct), [actions]);
    el.appendChild(head); el.appendChild(chartEl); el.appendChild(T.h('div', { class: 'grid2' }, posEl, ordEl)); el.appendChild(chainEl);
    var inst = null; var chart = null; var chain = null; var expiry = null; var userExpiry = false; var barsLoaded = false; var dead = false;
    function I() { return inst ? (inst.instrument || inst) : { instrument_id: id, kind: T.instKind(id) }; }
    function kindOf() { return I().kind || T.instKind(id) || null; }
    function held() { var p = (T.state.positions || []).filter(function (r) { return r.instrument_id === id; })[0]; return p ? Number(p.qty) || 0 : 0; }
    function open(fields) { return T.ticket.open(Object.assign({ account_id: acct, instrument_id: id, kind: kindOf() || undefined }, fields || {})); }
    function openPrivate(side) {
      if (T.ticketKinds && T.ticketKinds.PRIVATE) { open({ kind: 'PRIVATE', side: side }); return; }
      T.ui.toast(I().symbol + ' trades through the negotiated private ticket on the Private Markets page', '', 4000);
      T.router.go(T.router.account(acct, 'markets'));
    }
    function hedge() {
      if (held()) { open({ kind: 'POSITION_HEDGE', inst_kind: kindOf() || undefined, hedge: 'position', position_qty: held() }); return; }
      open({ hedge: true });
    }
    function paintActions() {
      T.clear(actions);
      var i = I(); var kind = i.kind;
      function btn(label, title, fn, cls) { actions.appendChild(T.h('button', { class: 'btn' + (cls ? ' ' + cls : ''), title: title || '', onclick: fn }, label)); }
      if (!inst) { return; }
      if (i.support === 'NOT_IN_THIS_BUILD' || i.support === 'UNSUPPORTED' || kind === 'INDEX') { return; }
      if (OTC[kind]) {
        btn('Terminate ...', 'terminate this contract with the dealer at its current model mark', function () {
          if (T.components.swapticket && T.components.swapticket.terminate) { T.components.swapticket.terminate(id, { account_id: acct }); } else { T.router.go(T.router.account(acct, 'markets/otc')); }
        });
        btn('OTC tab', 'the contracts table and the swap ticket', function () { T.router.go(T.router.account(acct, 'markets/otc')); });
        return;
      }
      var exActs = inst.extra && Array.isArray(inst.extra.actions) ? inst.extra.actions : null;
      if ((kind === 'BOND' || kind === 'PRIVATE') && exActs && !exActs.length) {
        /* the product module lists no action (a LIMITED bond, a company that cannot be traded here): only a hedge of what is held */
        if (kind === 'BOND' && held()) { btn('Hedge', 'hedge the bond you hold (DV01 futures in US Exchange)', hedge); }
        return;
      }
      if (kind === 'PRIVATE') { btn('Buy', 'the negotiated private ticket (BUY)', function () { openPrivate('BUY'); }); btn('Sell', 'the negotiated private ticket (SELL)', function () { openPrivate('SELL'); }); return; }
      if (kind === 'FX') { btn('Deal on the FX desk', 'spot or forward on the FX desk', function () { open({ kind: 'FX' }); }); return; }
      if (i.support === 'COMPUTED') { return; }
      btn(kind === 'OPTION' ? 'Buy (BTO)' : 'Buy', 'ticket: ' + sideFor(kind, 'b'), function () { open({ side: sideFor(kind, 'b') }); });
      btn(kind === 'OPTION' ? 'Sell (STO)' : 'Sell', 'ticket: ' + sideFor(kind, 's'), function () { open({ side: sideFor(kind, 's') }); });
      if (kind !== 'BOND' || held()) {
        btn('Hedge', held() ? 'hedge the position you hold (no new order in ' + i.symbol + ')' : 'the ticket with the hedge panel for a new order', hedge);
      }
    }
    T.watchQuotes([id]);
    function sessionChip(s) {
      return T.h('span', { class: 'chip ' + (s.phase || (s.open ? 'OPEN' : 'CLOSED')) }, s.group + ' . ' + (s.phase || (s.open ? 'OPEN' : 'CLOSED')) + (s.closes_at && s.open ? ' . closes ' + T.fmt.hm(s.closes_at) : (s.next_open && !s.open ? ' . opens ' + T.fmt.dow(s.next_open) + ' ' + T.fmt.hm(s.next_open) : '')));
    }
    function paintHead() {
      var d = inst || {}; var i = I(); var kind = i.kind; var val = !!VALUATION[kind];
      var q = val ? null : (T.quote(id) || d.quote || null); var s = d.session || {};
      T.clear(head);
      var top = T.h('div', { class: 'row' });
      top.appendChild(T.h('span', { style: 'font-size:16px;font-weight:600;font-family:var(--mono)' }, i.symbol || id));
      top.appendChild(T.h('span', { class: 'muted' }, [i.name, kind, i.currency, i.multiplier && String(i.multiplier) !== '1' && kind !== 'BOND' ? 'x' + T.fmt.num(i.multiplier, 0) : null].filter(Boolean).join(' . ')));
      var sup = d.support || i.support;
      if (sup && sup !== 'FULL') { top.appendChild(T.stateTag(sup)); }
      if (!val && s.group) { top.appendChild(sessionChip(s)); }
      head.appendChild(top);
      var row = T.h('div', { class: 'row', style: 'margin-top:6px;gap:18px;align-items:flex-start' });
      var kv = [];
      var u = unitOpts(i);
      if (val) {
        /* OA-21: the mark is the headline: the product module's (C5) else the mark; its own source and date, never a Yahoo line */
        var ex = d.extra || {};
        var hl = ex.headline && ex.headline.value != null ? Object.assign({}, d.mark || {}, ex.headline) : (d.mark || null);
        row.appendChild(T.h('span', null, T.px(hl, Object.assign({ size: 'big' }, u))));
        if (hl) {
          var m = d.mark || {}; var inp = (hl.inputs || m.inputs || {});
          kv.push(['valuation', (hl.label || hl.method || m.method || '') ? String(hl.label || m.source || hl.method || '') + (hl.quality ? ' (' + hl.quality + ')' : '') : (hl.quality || 'n/a')]);
          if (kind === 'PRIVATE' && !(ex.spec_view && ex.spec_view.length)) {
            /* the round's facts (when the private module sends no spec_view of its own, which lists them) */
            if (inp.round_date) { kv.push(['round', inp.round_date + (inp.round_type ? ' . ' + inp.round_type : '')]); }
            if (inp.post_money != null) { kv.push(['post-money', T.fmt.money(inp.post_money)]); }
            if (inp.lead_investors) { kv.push(['lead investors', String(inp.lead_investors)]); }
            if (inp.source_url) { kv.push(['source', T.h('a', { href: inp.source_url, target: '_blank', rel: 'noopener noreferrer', title: inp.source_url }, String(inp.source_url).replace(/^https?:\/\//, '').slice(0, 48))]); }
            if (inp.recorded_by) { kv.push(['recorded by', inp.recorded_by + (inp.as_of ? ' . as of ' + inp.as_of : '')]); }
          } else if (kind !== 'PRIVATE') {
            if (hl.source) { kv.push(['source', String(hl.source)]); }
            var asof = hl.as_of_date || hl.as_of || hl.quote_time;
            if (asof) { kv.push(['as of', T.fmt.dt(asof) || String(asof)]); }
            if (hl.yield_pct != null) { kv.push(['yield', T.fmt.ratePct(hl.yield_pct)]); }
            if (hl.accrued != null) { kv.push(['accrued / 100', T.fmt.px(hl.accrued)]); }
            if (hl.dv01_per_1m != null) { kv.push(['DV01 per 1m face', T.fmt.money(hl.dv01_per_1m)]); }
          }
          if (hl.note || m.note) { kv.push(['note', String(hl.note || m.note)]); }
        } else { kv.push(['mark', 'unavailable']); }
        if (ex.error) { kv.push(['detail', 'could not be loaded: ' + ex.error]); }
      } else if (q && (q.last != null || q.mid != null || q.bid != null)) {
        row.appendChild(T.h('span', null, T.px(q, Object.assign({ size: 'big' }, u))));
        if (q.bid != null || q.ask != null) { kv.push(['bid / ask', T.h('span', null, T.px(q, Object.assign({ field: 'bid', size: 'inline' }, u)), ' / ', T.px(q, Object.assign({ field: 'ask', size: 'inline' }, u)))]); }
        kv.push(['prev close', q.prev_close != null ? unitText(i, q.prev_close) : 'n/a']);
        kv.push(['day range', q.day_low != null && q.day_high != null ? unitText(i, q.day_low) + ' - ' + T.fmt.px(q.day_high) : 'n/a']);
        kv.push(['volume', q.day_volume != null ? T.fmt.qty(q.day_volume) : 'n/a']);
        kv.push(['source', (q.source || (T.providerLabel ? T.providerLabel(q.provider) : q.provider) || 'n/a') + (q.delay_label ? ' . ' + q.delay_label : '')]);
        if (q.error) { kv.push(['provider', 'last fetch failed: ' + q.error]); }
      } else if (q && q.error) {
        row.appendChild(T.h('span', { class: 'q-unavailable' }, 'quote unavailable'));
        kv.push(['provider', String(q.error)]);
      } else {
        /* USX-08: a quote the worker has not fetched yet is said to be on its way, never 'N/A' */
        row.appendChild(T.h('span', { class: 'muted' }, 'fetching quote ...', T.h('span', { class: 'spin', style: 'margin-left:6px' })));
        kv.push(['quote', 'requested from Yahoo (up to 15 min delayed); it appears here when the first print arrives']);
      }
      if (!val && d.mark && d.mark.quality === 'computed') { kv.push(['mark', T.px(d.mark, { size: 'inline' }), 'computed mark (model) - never executable']); }
      if (kv.length) { row.appendChild(T.kv(kv)); }
      head.appendChild(row);
      var lim = i.limitation || d.limitation || (i.support === 'LIMITED' ? 'LIMITED: marks only, no execution in this build' : null) || ((i.spec || {}).limited_reason && i.support !== 'FULL' ? i.spec.limited_reason : null);
      if (lim) { head.appendChild(T.h('div', { class: 'conv' }, lim)); }
      if (i.support === 'NOT_IN_THIS_BUILD') { head.appendChild(T.nib(i.name || id)); }
      var sr = specRows(i, d);
      if (sr.length) { head.appendChild(T.h('div', { class: 'spec-view', style: 'margin-top:6px' }, T.kv(sr))); }
      if (NOTES[kind]) { head.appendChild(T.convNote(NOTES[kind])); }
    }
    function paintPositions() {
      T.clear(posEl); posEl.appendChild(T.h('h2', null, 'position in ' + T.accountName(acct)));
      var ps = (T.state.positions || []).filter(function (p) { return p.instrument_id === id; });
      if (!ps.length) { posEl.appendChild(T.h('div', { class: 'empty' }, T.state.positions ? 'no position' : 'loading ...')); return; }
      var p = ps[0];
      /* P8L-02: an OTC contract's quantity is its units with their label (a TRS its reference shares, a CDX / IRS its notional), never
         its notional read as a quantity; a TRS shows its notional on its own line */
      var c = T.contractQty(p);
      var qtyRow = c ? ['quantity', T.h('span', { class: c.n > 0 ? 'pos' : (c.n < 0 ? 'neg' : '') }, T.fmt.qty(c.n) + ' ' + c.label),
                        (c.isNotional ? 'the contract\'s notional (its face, never its market value)' : 'the contract\'s reference units') + '; negative = the short side']
                     : ['quantity', T.fmt.qty(p.qty)];
      posEl.appendChild(T.kv([qtyRow, c && !c.isNotional ? ['notional', c.ccy + ' ' + c.notional, 'the contract\'s face (never its market value)'] : null,
        ['avg cost', p.avg_px != null ? T.fmt.px(p.avg_px) : 'n/a'], ['mark', T.px(p.mark, { size: 'inline' })], ['market value', T.money(p, 'mv')],
        p.kind === 'FUTURE' ? ['exposure (notional)', T.money(p, 'notional')] : null, ['unrealised', T.money(p, 'unrealised')], ['P&L today', T.money(p, 'day_pnl')], ['realised ITD', T.money(p, 'realised_itd')]]));
      if (p.lots && p.lots.length) {
        var g = T.h('div'); posEl.appendChild(g);
        T.components.grid.mount(g, { tools: false, rows: p.lots, columns: [
          { key: 'lot_id', label: 'lot', cls: 'l' }, { key: 'side', label: 'side', cls: 'l' }, { key: 'open_date', label: 'opened', cls: 'l' }, { key: 'qty_remaining', label: 'qty', text: function (r) { return T.fmt.qty(r.qty_remaining); } },
          { key: 'unit_cost', label: 'unit cost', text: function (r) { return T.fmt.px(r.unit_cost); } }, { key: 'cost', label: 'cost', text: function (r) { return T.money(r, 'cost'); } }, { key: 'lock_until', label: 'locked until', cls: 'l', text: function (r) { return r.lock_until || ''; } }] });
      }
      if (p.borrow) { posEl.appendChild(T.h('div', { class: 'hint' }, 'borrow ' + (p.borrow.borrow_id || '') + ' . fee ' + T.fmt.rate(p.borrow.fee_rate) + ' . collateral ' + T.money(p.borrow, 'collateral'))); }
      paintActions();
    }
    function trailText(o) { return o.trail_amt ? ' trail ' + T.fmt.px(o.trail_amt) : (o.trail_pct ? ' trail ' + T.fmt.ratePct(o.trail_pct) : ''); }
    function paintOrders() {
      T.clear(ordEl); ordEl.appendChild(T.h('h2', null, 'working orders on ' + id));
      var os = (T.state.orders || []).filter(function (o) { return o.instrument_id === id; });
      if (!os.length) { ordEl.appendChild(T.h('div', { class: 'empty' }, T.state.orders ? 'none' : 'loading ...')); return; }
      var g = T.h('div'); ordEl.appendChild(g);
      T.components.grid.mount(g, { tools: false, rows: os, columns: [
        { key: 'x', label: '', cls: 'l', html: function () { return '<button class="btn small" data-act="x" title="cancel (x)">x</button>'; }, text: function () { return ''; } },
        { key: 'side', label: 'side', cls: 'l', html: function (o) { return '<span class="side-' + T.esc(o.side) + '">' + T.esc(String(o.side).replace(/_/g, ' ')) + '</span>'; }, text: function (o) { return o.side; } },
        { key: 'qty', label: 'qty', text: function (o) { return T.fmt.qty(o.qty) + (o.filled_qty ? ' (' + T.fmt.qty(o.filled_qty) + ' filled)' : ''); } },
        { key: 'order_type', label: 'type', cls: 'l', text: function (o) { return o.order_type + (o.limit_px ? ' ' + T.fmt.px(o.limit_px) : '') + (o.stop_px ? ' stop ' + T.fmt.px(o.stop_px) : '') + trailText(o); } },
        { key: 'status', label: 'status', cls: 'l', html: function (o) { return '<span class="st-' + T.esc(o.status) + '">' + T.esc(o.status) + '</span>'; }, text: function (o) { return o.status; } }],
        onClick: function (o, e) { if (e.target.closest('button[data-act="x"]')) { cancel(o); } }, onKey: function (k, o) { if (k === 'x') { cancel(o); return true; } return false; } });
    }
    function cancel(o) {
      T.ui.confirm('Cancel order ' + o.order_id + ' (' + o.side + ' ' + o.qty + ' ' + o.instrument_id + ')?', { ok: 'Cancel order', cancel: 'Keep' }).then(function (yes) {
        if (!yes) { return; }
        T.api.del('/api/orders/' + encodeURIComponent(o.order_id), {}).then(function () { T.ui.toast('order cancelled', 'ok'); T.poll.kick('orders'); T.poll.kick('account_detail'); }, function (e) { T.api.fail(e, 'cancel'); });
      });
    }
    /* ---- chart (USX-06): the session's 1-minute bars; a 202 is asked again; refreshed while the session is open ---- */
    function sessionOpen() { var s = (inst && inst.session) || {}; var live = T.state.sessions && (T.state.sessions.sessions || T.state.sessions); var g = live && s.group ? live[s.group] : null; return !!((g && g.open) || s.open); }
    function loadBars() {
      if (dead || !chart) { return Promise.resolve(); }
      if (barsLoaded && !sessionOpen()) { return Promise.resolve(); }
      var i = I();
      if (!barsLoaded) { chart.update({ empty: 'fetching the session\'s 1-minute bars ...' }); }
      return T.api.getReady('/api/instruments/' + encodeURIComponent(id) + '/bars', { range: '1d' }, BARS_TRIES).then(function (b) {
        if (dead) { return; }
        barsLoaded = true;
        var q = (inst && inst.quote) || {};
        var parts = [b.source || q.source || 'Yahoo 1-minute bars'];
        if (b.delay_label || q.delay_label) { parts.push(b.delay_label || q.delay_label); }
        if (b.session && b.session.trade_date) { parts.push('session ' + b.session.trade_date); }
        if (b.bars && b.bars.length) { parts.push(b.bars.length + ' bars ' + T.fmt.hm(b.bars[0].t) + '-' + T.fmt.hm(b.bars[b.bars.length - 1].t) + ' ET'); }
        if (b.note) { parts.push(b.note); }
        chart.update({ bars: b.bars || [], prev_close: b.prev_close != null ? b.prev_close : q.prev_close, dp: dpOf(i), note: parts.join(' . '),
                       empty: b.error ? 'no bars: ' + b.error : 'no bars for this session yet' });
      }, function (e) {
        if (dead) { return; }
        chart.update({ bars: [], note: '1-minute bars', empty: e && e.code === 'PENDING' ? 'the bars are still being fetched - retrying' : 'bars unavailable: ' + (e.message || e) });
      });
    }
    /* ---- chain (USX-25): the nearest expiry 7-30 days out, ATM centred; a pick hands the leg (with its meta) to the ticket ---- */
    function loadChain() {
      var i = I();
      if (VALUATION[i.kind] || !(i.optionable || (inst && (inst.optionable || inst.chain_available)))) {
        T.clear(chainEl); chainEl.hidden = !!VALUATION[i.kind];
        if (!VALUATION[i.kind]) { chainEl.appendChild(T.h('h2', null, 'option chain')); chainEl.appendChild(T.h('div', { class: 'empty' }, i.kind === 'OPTION' ? 'this is an option contract: its chain is on ' + (i.underlying_id || 'the underlying') + '\'s page' : 'no listed options')); }
        return;
      }
      T.state.chain_target = { instrument_id: id, expiry: expiry };
      if (!chain) {
        T.clear(chainEl); chainEl.appendChild(T.h('h2', null, 'option chain', T.h('small', null, 'Cboe delayed; click a bid (sell to open) or an ask (buy to open) for a ticket leg'))); var m = T.h('div'); chainEl.appendChild(m);
        chain = T.components.chain.mount(m, { data: { pending: true }, expiry: expiry,
          onExpiry: function (x) { userExpiry = true; setExpiry(x); },
          onPick: function (leg) {
            T.ticket.open({ account_id: acct, instrument_id: leg.instrument_id, kind: 'OPTION', side: leg.side === 'ask' ? 'BTO' : 'STO', price: leg.price,
                            leg: Object.assign({}, leg.leg || {}, { instrument_id: leg.instrument_id, strike: leg.strike, expiry: leg.expiry, right: leg.kind === 'call' ? 'C' : 'P', underlying_id: id }) });
          } });
        chainEl.appendChild(T.convNote(['OCC', 'AON']));
        if (T.state.open_chain === id) { T.state.open_chain = null; setTimeout(function () { try { chainEl.scrollIntoView({ block: 'start' }); } catch (e) { /* ignore */ } }, 0); }
      }
      T.poll.kick('chain');
    }
    function setExpiry(x) {
      expiry = x; T.state.chain = null; T.state.chain_target = { instrument_id: id, expiry: x };
      if (chain) { chain.expiry(x); chain.update({ pending: true }); }
      T.poll.kick('chain');
    }
    function onChain(c) {
      if (!chain || !c) { return; }
      if (!c.pending && c.status !== 'fetching' && !userExpiry && !expiry && Array.isArray(c.expiries) && c.expiries.length) {
        var want = T.components.chain.defaultExpiry(c.expiries);
        if (want && want !== c.expiry) { setExpiry(want); return; }
        expiry = c.expiry || want; chain.expiry(expiry);
      }
      chain.update(c);
    }
    function openTicketOnLoad() {
      var i = I(); var kind = i.kind;
      var pre = T.state.ticket_prefill;
      if (pre && pre.account_id === acct && pre.instrument_id === id) { T.state.ticket_prefill = null; T.ticket.open(Object.assign({ kind: kind || undefined }, pre)); return; }
      if (i.support === 'NOT_IN_THIS_BUILD' || i.support === 'UNSUPPORTED' || kind === 'INDEX' || OTC[kind]) { return; }
      if (kind === 'PRIVATE' && !(T.ticketKinds && T.ticketKinds.PRIVATE)) { return; }
      /* ORC-2: no forced side - an option opens BTO, everything else keeps the ticket's own side (a Close stays a Close) */
      T.ticket.open({ account_id: acct, instrument_id: id, kind: kind || undefined, side: kind === 'OPTION' ? 'BTO' : undefined });
    }
    T.api.get('/api/instruments/' + encodeURIComponent(id), { account: acct }).then(function (d) {
      if (dead) { return; }
      inst = d; var i = I();
      if (i.kind) { T.rememberKind(id, i.kind); }
      paintHead(); paintActions();
      if (VALUATION[i.kind] || i.kind === 'OPTION') {
        chartEl.hidden = !!VALUATION[i.kind];
        if (i.kind === 'OPTION') { T.clear(chartEl).appendChild(T.h('div', { class: 'hint', style: 'margin:0 0 8px' }, 'no 1-minute chart for an option contract: its quotes come from the Cboe chain (bid / ask above)')); }
      } else {
        chart = T.components.chart.mount(chartEl, { bars: [], note: '1-minute bars', empty: 'fetching the session\'s 1-minute bars ...', dp: dpOf(i) });
        T.poll.page('bars-' + id, BARS_REFRESH_MS, loadBars);
      }
      loadChain();
      openTicketOnLoad();
    }, function (e) { if (!dead) { T.clear(head); T.errorBox(head, e, 'instrument'); } });
    paintPositions(); paintOrders();
    var offs = [
      T.state.on('positions', paintPositions), T.state.on('orders', paintOrders),
      T.state.on('quotes', function (q) { if (inst && q[id] && !VALUATION[I().kind]) { inst.quote = Object.assign({}, inst.quote || {}, q[id]); paintHead(); } }),
      T.state.on('chain', onChain)
    ];
    T.pages.instrument.unmount = function () { dead = true; offs.forEach(function (f) { f(); }); if (chart && chart.destroy) { chart.destroy(); } T.state.chain_target = null; T.pages.instrument.unmount = null; };
  }
  T.pages.instrument = { render: render, unmount: null, specRows: specRows };
})();
