/* Hedge panel (TERMINAL_DESIGN.md s10.9, s13.16, s17.6; WP-13): two tabs inside the ticket.
 *
 *   var h = T.components.hedge.mount(el, {account_id, instrument_id, side, qty, inst, onPick(hedge|null), onLegs(ids),
 *                                        onSubmitPackage(body, spec), tif()});
 *   h.update({account_id, instrument_id, side, qty, inst});   the ticket's fields changed (debounced by the ticket)
 *   h.seed(previewHedgesBlock);   the preview's `hedges` block: ready -> rendered without another fetch (single shared fetch)
 *   h.load();                     GET /api/hedges (202 -> spinner, polled; one in-flight promise per key, shared)
 *   h.pick(n) / h.none();         keys 1..5 / 0 from the ticket; h.picked() -> {candidate_id, ratio, edits} | null
 *   h.tab('hedges' | 'strategies'); h.legIds(); h.destroy();
 *
 * Hedges tab: the s10.9 candidates for the primary (PUT_7PCT, COLLAR_7, PUT_SPREAD, INDEX_OVERLAY, CALL_* for shorts,
 * DELTA_HEDGE for options, CALENDAR for futures); every leg price is the chain's ask (buys) / bid (sells) with the
 * chain's freshness (T.px on the leg's own record); cost, protection level, net cash, greeks and the chain quality are
 * the server's; a candidate the server marks `available: false` shows its reason and cannot be picked; FX forwards and
 * options on futures show NOT IN THIS BUILD.  Picking a candidate hands {candidate_id, ratio, edits: {strike, expiry}}
 * to the ticket, which re-previews and submits the primary and the legs as ONE order.submit (sequential: the legs wait
 * for the primary and are resized on partial fills - stated on the panel).
 *
 * Strategies tab: the 60 templates of strategies.py (GET /api/strategies: groups, availability and the reason), the
 * group filter, units / tenor / per-leg strike overrides (a small number is a percent, |value| >= 60 an absolute
 * strike), POST /api/strategies/preview (202 while the chain loads) -> per-leg prices with freshness, totals (net
 * premium, fees, net cash, delta / vega / theta, incremental option margin ONCE for the package, futures IM), the
 * expiry payoff grid (+/-35 %, intrinsic at the near expiry, drawn here from the server's points - never computed) and
 * ONE Submit: an option-only package fills all-or-none (the AON sentence is shown), the others sequentially.
 *
 * Build 2 (P10):
 * - POSITION MODE (USX-12 / ORC-4; mount opts.mode 'position'): GET /api/hedges?mode=position lists the hedges of the
 *   position ALREADY HELD, sized to it - never a new primary order.  A one-leg hedge is traded as its own order (opts.
 *   onTradeLeg hands the leg to the ticket, which previews and submits it like any order); a hedge of several option legs is
 *   previewed and submitted as ONE all-or-none package (POST /api/strategies/preview key POSITION_HEDGE, then the ticket's
 *   package submit); nothing is ever bought or sold in the underlying.
 * - The TRS overlay opens the OTC tab's TRS form prefilled (T.components.swapticket.prefill, C21; PO-10) and the FX forward
 *   the FX desk prefilled (OA-09): neither is ever an order leg.  A DV01 futures hedge of a bond trades in the lifetime's US
 *   Exchange account (its instrument page opens with the ticket prefilled).
 * - The ratio reads like a trader's ('1 contract per 100 shares'; the server's ratio_text), candidates that size to nothing
 *   (fewer than 100 shares, less than one futures contract) are not listed - one line says which and why - and the
 *   candidates load by themselves whenever the panel is open with an instrument, a side and a quantity (USX-36).
 * - A strategy leg's distance from spot is already a percent: T.fmt.ratePct ('-7.22 % from spot'; APIUI-09).
 *
 * Conventions kept here: nothing is computed (the grid points, totals and strikes come from the server); every price
 * carries freshness; a 202 shows a spinner, never a number; failures surface as text with a Retry, never silently;
 * memory stays small (one candidate list and one strategy spec at a time; the catalogue cache holds 8 entries).
 * ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  var MAX_POLLS = 20, AUTOLOAD_MS = 350;
  var catalogue = {}; var catalogueKeys = [];
  var inflight = {};                                   /* key -> promise (single shared fetch per account/instrument/side/qty) */

  function badge(txt, cls, title) { return T.h('span', { class: 'badge ' + (cls || ''), title: title || '' }, txt); }
  function money(x) { return x == null || x === '' ? 'n/a' : T.fmt.money(x); }
  function sideTxt(s) { return String(s || '').replace(/_/g, ' '); }
  function pollReady(path, params, gen, isLive) {
    var tries = 0;
    function once() {
      return T.api.get(path, params).then(function (r) {
        if (!isLive(gen)) { throw new T.ApiError(0, 'SUPERSEDED', 'superseded'); }
        if (r && r.pending) {
          if (++tries >= MAX_POLLS) { throw new T.ApiError(202, 'PENDING', 'still fetching the chain after ' + tries + ' polls - Retry'); }
          return new Promise(function (ok) { setTimeout(ok, r.retry_ms || 1500); }).then(once);
        }
        return r;
      });
    }
    return once();
  }
  function postReady(path, body, gen, isLive) {
    var tries = 0;
    function once() {
      return T.api.post(path, body, { retries: 1 }).then(function (r) {
        if (!isLive(gen)) { throw new T.ApiError(0, 'SUPERSEDED', 'superseded'); }
        if (r && r.pending) {
          if (++tries >= MAX_POLLS) { throw new T.ApiError(202, 'PENDING', 'still fetching the chain after ' + tries + ' polls - Retry'); }
          return new Promise(function (ok) { setTimeout(ok, r.retry_ms || 1500); }).then(once);
        }
        return r;
      });
    }
    return once();
  }
  /* the account a hedge on another desk is dealt in (the candidate's trade_account), else the panel's own */
  function tradeAccount(c, fallback) { return (c && c.trade_account) || fallback; }
  /* open another page's ticket for a hedge that is not an order leg here: the account's page first when it is not the one shown */
  function openIn(acct, fields, sub) {
    var cur = T.router.current;
    if (cur && cur.account === acct) { T.ticket.open(Object.assign({ account_id: acct }, fields)); return; }
    var done = false;
    var off = T.state.on('route', function (r) {
      if (done || !r || r.account !== acct) { return; }
      done = true; off();
      setTimeout(function () { T.ticket.open(Object.assign({ account_id: acct }, fields)); }, 0);
    });
    setTimeout(function () { if (!done) { done = true; off(); } }, 8000);
    T.router.go(T.router.account(acct, sub || 'markets'));
  }
  function legLine(l) {
    var row = T.h('div', { class: 'hg-leg row' });
    row.appendChild(T.h('span', { class: 'side-' + T.esc(l.side || '') }, sideTxt(l.side)));
    row.appendChild(T.h('span', { class: 'mono' }, (l.kind === 'FX_FORWARD' ? '' : T.fmt.qty(l.qty) + ' ') + (l.kind === 'FX_FORWARD' ? (l.label || l.symbol || '') : (l.symbol || l.instrument_id || ''))));
    if (l.quote && l.price_basis && l.quote[l.price_basis] != null) { row.appendChild(T.px(l.quote, { field: l.price_basis, size: 'inline' })); }
    else if (l.quote && l.est_price != null) { row.appendChild(T.px(Object.assign({}, l.quote, { value: l.est_price }), { field: 'value', size: 'inline' })); }
    else if (l.est_price != null) { row.appendChild(T.h('span', { class: 'mono q-stale' }, T.fmt.px(l.est_price) + ' (no quote record)')); }
    else { row.appendChild(T.px(null)); }
    row.appendChild(T.h('span', { class: 'muted' }, (l.price_basis ? l.price_basis : '') + (l.fee != null ? ' . fee ' + money(l.fee) : '') + (l.margin && l.margin.initial ? ' . IM ' + money(l.margin.initial) : '')));
    return row;
  }

  function mount(el, opts) {
    opts = opts || {};
    var st = { account_id: opts.account_id, instrument_id: opts.instrument_id, side: opts.side, qty: opts.qty, inst: opts.inst || null,
               mode: opts.mode === 'position' ? 'position' : 'order', position: opts.position || null,
               tab: 'hedges', cands: null, candsKey: null, status: 'idle', note: '', meta: {}, picked: null, edits: {}, gen: 0, autoTimer: null,
               pos: { spec: null, cand: null, status: 'idle', note: '', gen: 0 },
               strat: { list: null, group: '', key: null, params: {}, spec: null, status: 'idle', note: '', gen: 0, timer: null } };
    var root = T.h('div', { class: 'hedge-panel' });
    var tabs = T.h('div'); var body = T.h('div', { class: 'hedge-body' });
    root.appendChild(tabs); root.appendChild(body);
    T.clear(el).appendChild(root);
    function key() { return st.mode === 'position' ? [st.account_id, st.instrument_id, 'position'].join('|') : [st.account_id, st.instrument_id, st.side, st.qty].join('|'); }
    /* the candidates can be asked for: an instrument (position mode), plus an opening side and a quantity (a new order) */
    function complete() { return !!(st.account_id && st.instrument_id && (st.mode === 'position' || (st.side && Number(st.qty) > 0))); }
    function isLive(g) { return g === st.gen && root.isConnected; }
    function isOpen() { return typeof opts.isOpen === 'function' ? !!opts.isOpen() : true; }
    /* USX-36: the candidates load by themselves while the panel is open and the order (or position) is complete */
    function autoLoad() {
      clearTimeout(st.autoTimer);
      if (!complete() || !isOpen() || st.status === 'fetching' || (st.candsKey === key() && st.status !== 'idle')) { return; }
      st.autoTimer = setTimeout(function () { if (complete() && isOpen() && st.status !== 'fetching' && !(st.candsKey === key() && st.status !== 'idle')) { load(); } }, AUTOLOAD_MS);
    }
    function paintTabs() {
      T.clear(tabs);
      var shown = (st.cands || []).filter(function (c) { return !c.zero_size; }).length;
      tabs.appendChild(T.tabs([{ key: 'hedges', label: (st.mode === 'position' ? 'Hedge this position' : 'Hedges') + (st.cands ? ' (' + shown + ')' : ''),
                                 title: st.mode === 'position' ? 'hedges of the position already held: only the hedge legs trade' : 'candidates for this order' },
                               { key: 'strategies', label: 'Strategies', title: 'the 60 templates (s13.16)' }], st.tab, function (k) { st.tab = k; paint(); if (k === 'strategies') { loadCatalogue(); } }));
    }
    function paint() { paintTabs(); T.clear(body); if (st.tab === 'hedges') { paintHedges(); } else { paintStrategies(); } }

    /* ---------------------------------------------------------- hedges ----------------------------------------- */
    function legIds() {
      var ids = {};
      (st.cands || []).forEach(function (c) { (c.legs || []).forEach(function (l) { if (l.instrument_id) { ids[l.instrument_id] = 1; } }); });
      ((st.strat.spec && st.strat.spec.legs) || []).forEach(function (l) { if (l.instrument_id) { ids[l.instrument_id] = 1; } });
      ((st.pos.spec && st.pos.spec.legs) || []).forEach(function (l) { if (l.instrument_id) { ids[l.instrument_id] = 1; } });
      return Object.keys(ids);
    }
    function setCands(list, meta) {
      st.cands = list || []; st.candsKey = key(); st.status = 'ready'; st.meta = meta || {};
      /* a futures leg whose quote was just requested: the candidates are asked for once more a few seconds later */
      var k0 = st.candsKey;
      if (st.requery_key !== k0) { st.requery_key = k0; st.requery_n = 0; }
      if (st.cands.some(function (c) { return /no quote yet - requested/.test(c.reason || ''); }) && st.requery_n < 5) {
        st.requery_n++;
        setTimeout(function () { if (root.isConnected && st.candsKey === k0 && st.status === 'ready' && isOpen()) { delete inflight[k0]; load(); } }, 6000);
      }
      if (st.picked && !st.cands.some(function (c) { return c.id === st.picked.candidate_id; })) { st.picked = null; if (opts.onPick) { opts.onPick(null); } }
      if (opts.onLegs) { opts.onLegs(legIds()); }
      paint();
    }
    function load() {
      clearTimeout(st.autoTimer);
      if (!complete()) { st.status = 'idle'; paint(); return Promise.resolve(null); }
      var k = key(); var gen = ++st.gen;
      st.status = 'fetching'; st.note = ''; st.candsKey = k; paint();
      var p = inflight[k];
      if (!p) {
        var q = st.mode === 'position' ? { account_id: st.account_id, instrument_id: st.instrument_id, mode: 'position' }
                                       : { account_id: st.account_id, instrument_id: st.instrument_id, side: st.side, qty: st.qty };
        p = pollReady('/api/hedges', q, gen, function () { return root.isConnected; });
        inflight[k] = p;
        p.then(function () { delete inflight[k]; }, function () { delete inflight[k]; });
      }
      return p.then(function (r) {
        if (!isLive(gen)) { return null; }
        if (r && !Array.isArray(r) && r.position) { st.position = r.position; }
        if (r && !Array.isArray(r) && r.status && r.status !== 'ready' && !(r.candidates || []).length) {
          st.cands = null; st.candsKey = k; st.status = r.status === 'flat' ? 'flat' : (r.status || 'unavailable'); st.note = r.note || ''; st.meta = r; paint(); return [];
        }
        var list = Array.isArray(r) ? r : (r && (r.candidates || r.rows)) || [];
        setCands(list, Array.isArray(r) ? {} : r);
        return list;
      }, function (e) {
        if (!isLive(gen) || e.code === 'SUPERSEDED') { return null; }
        st.status = 'error'; st.note = (e.code || 'ERROR') + ': ' + (e.message || e); paint();
        return null;
      });
    }
    function seed(block) {
      if (!block) { return; }
      if (block.status === 'ready' && Array.isArray(block.candidates)) { st.gen++; setCands(block.candidates, block); return; }
      if (block.status === 'fetching') { if (st.candsKey !== key()) { load(); } return; }
      st.status = block.status || 'unavailable'; st.note = block.note || ''; st.cands = null; st.candsKey = key(); paint();
    }
    /* the TRS overlay -> the OTC tab's TRS form; the FX forward -> the FX desk; both prefilled from the candidate (PO-10, OA-09) */
    function openDesk(c) {
      var pre = c.prefill || {};
      var acct = tradeAccount(c, st.account_id);
      if (c.submit_via === 'otc') {
        var sw = T.components.swapticket;
        if (sw && typeof sw.prefill === 'function') { sw.prefill(pre.kind || 'TRS', pre.terms || {}, { account_id: acct }); }
        else { T.router.go(T.router.account(acct, 'markets/otc')); T.ui.toast('the swap ticket is on the OTC tab: book the TRS there', '', 4000); }
        return;
      }
      if (c.submit_via === 'fx') {
        var go = function () {
          openIn(acct, { kind: 'FX', instrument_id: pre.instrument_id, mode: pre.mode || 'FORWARD', tenor: pre.tenor, buy_ccy: pre.buy_ccy, sell_ccy: pre.sell_ccy,
                         amount: pre.amount, amount_ccy: pre.amount_ccy }, 'markets');
        };
        /* the FX desk takes the dock: an order being written there is not submitted - said before it closes */
        if (st.mode !== 'position' && typeof opts.orderInProgress === 'function' && opts.orderInProgress()) {
          T.ui.confirm('The FX desk opens in the dock with this forward prefilled; the order you are writing on the ticket is NOT submitted (submit it first if you want both).',
                       { title: 'Open the FX desk', ok: 'Open the FX desk', cancel: 'Stay on the order' }).then(function (yes) { if (yes) { go(); } });
          return;
        }
        go();
      }
    }
    /* position mode: a one-leg hedge becomes an ordinary order on the ticket (opts.onTradeLeg); a leg that trades in another
       account (DV01 futures in US Exchange) opens there with the ticket prefilled */
    function tradeLeg(c, l) {
      var acct = tradeAccount(c, st.account_id);
      var fields = { instrument_id: l.instrument_id, kind: l.kind === 'OPTION' ? 'OPTION' : (l.kind === 'FUTURE' ? 'FUTURE' : undefined), side: l.side, qty: l.qty, order_type: 'MARKET',
                     hedge_for: { candidate_id: c.id, label: c.label, position: st.position } };
      if (l.kind === 'OPTION') { fields.leg = { instrument_id: l.instrument_id, strike: l.strike, expiry: l.expiry, underlying_id: st.instrument_id, bid: (l.quote || {}).bid, ask: (l.quote || {}).ask, quality: (l.quote || {}).quality, quote: l.quote }; delete fields.order_type; }
      if (acct !== st.account_id) {
        T.state.ticket_prefill = Object.assign({ account_id: acct }, fields);
        T.ui.toast(c.label + ': the futures trade in ' + T.accountName(acct) + ' - opening it there', '', 4000);
        T.router.go(T.router.instrument(acct, l.instrument_id));
        return;
      }
      if (opts.onTradeLeg) { opts.onTradeLeg(fields, c); }
    }
    /* position mode: a hedge of several option legs as ONE all-or-none package (strategies POSITION_HEDGE) */
    function previewPosPackage(c) {
      var gen = ++st.pos.gen;
      st.pos.cand = c.id; st.pos.spec = null; st.pos.status = 'previewing'; st.pos.note = ''; paint();
      var params = { underlying: st.instrument_id, candidate_id: c.id, edits: st.picked && st.picked.candidate_id === c.id ? (st.picked.edits || {}) : {} };
      postReady('/api/strategies/preview', { account_id: st.account_id, key: 'POSITION_HEDGE', params: params }, gen, function (g) { return g === st.pos.gen && root.isConnected; }).then(function (spec) {
        if (gen !== st.pos.gen) { return; }
        st.pos.spec = spec; st.pos.status = 'ready';
        if (opts.onLegs) { opts.onLegs(legIds()); }
        paint();
      }, function (e) {
        if (gen !== st.pos.gen || e.code === 'SUPERSEDED') { return; }
        st.pos.spec = null; st.pos.status = 'error'; st.pos.note = (e.code || 'ERROR') + ': ' + (e.message || e); paint();
      });
    }
    function mainAction(c) {
      if (!c || c.available === false || c.not_in_this_build) { return; }
      if (c.submit_via === 'otc' || c.submit_via === 'fx') { openDesk(c); return; }
      if (st.mode !== 'position') { st.edits = {}; pickCand(c); return; }
      var legs = (c.legs || []).filter(function (l) { return Number(l.qty) > 0; });
      if (c.submit_via === 'package') { previewPosPackage(c); return; }
      if (legs.length) { tradeLeg(c, legs[0]); }
    }
    function actionLabel(c, n) {
      var picked = st.picked && st.picked.candidate_id === c.id;
      var legs = (c.legs || []).filter(function (l) { return Number(l.qty) > 0; });
      if (c.submit_via === 'otc') { return 'Open the OTC tab: TRS form prefilled' + (n <= 9 ? ' (' + n + ')' : ''); }
      if (c.submit_via === 'fx') { return 'Open the FX desk: forward prefilled' + (n <= 9 ? ' (' + n + ')' : ''); }
      if (st.mode === 'position') {
        if (c.submit_via === 'package') { return 'Preview the ' + legs.length + '-leg package (all-or-none)' + (n <= 9 ? ' (' + n + ')' : ''); }
        var l = legs[0];
        return l ? 'Trade this leg: ' + sideTxt(l.side) + ' ' + T.fmt.qty(l.qty) + ' ' + (l.symbol || l.instrument_id) + (c.trade_account && c.trade_account !== st.account_id ? ' in ' + T.accountName(c.trade_account) : '') + (n <= 9 ? ' (' + n + ')' : '') : 'no leg';
      }
      return picked ? 'picked' : 'pick (' + n + ')';
    }
    function pickCand(c) {
      if (!c || c.available === false || c.not_in_this_build) { return; }
      if (c.submit_via === 'otc' || c.submit_via === 'fx') { openDesk(c); return; }
      st.picked = { candidate_id: c.id, ratio: (st.edits.ratio || c.ratio || null), edits: {} };
      ['strike', 'expiry'].forEach(function (k) { if (st.edits[k]) { st.picked.edits[k] = st.edits[k]; } });
      if (opts.onPick) { opts.onPick(st.picked, c); }
      paint();
    }
    function none() { st.picked = null; st.edits = {}; if (opts.onPick) { opts.onPick(null); } paint(); }
    function editBox(c) {
      var box = T.h('div', { class: 'hg-edit' });
      var ed = c.editable || {};
      var form = [];
      var inStrike = T.h('input', { type: 'text', value: st.edits.strike || '', placeholder: (c.legs && c.legs[0] && c.legs[0].strike) || 'listed strike', 'aria-label': 'strike' });
      var inExp = T.h('input', { type: 'text', value: st.edits.expiry || '', placeholder: (c.legs && c.legs[0] && c.legs[0].expiry) || 'YYYY-MM-DD', 'aria-label': 'expiry' });
      var inRatio = T.h('input', { type: 'text', value: st.edits.ratio || c.ratio || '', placeholder: c.ratio || 'legs per primary unit', 'aria-label': 'ratio' });
      if (ed.strike) { form.push(T.field('strike', inStrike, 'snaps to a listed strike on the server')); }
      if (ed.expiry) { form.push(T.field('expiry', inExp, 'a listed expiry')); }
      if (ed.ratio && st.mode !== 'position') { form.push(T.field('ratio', inRatio, (c.ratio_text ? c.ratio_text + ' = ' : '') + 'leg quantity per unit of the primary (' + (c.ratio || '?') + '); the legs are sized to the primary\'s filled quantity x ratio')); }
      if (form.length) { box.appendChild(T.form(form)); }
      box.appendChild(T.h('div', { class: 'row' },
        T.h('button', { class: 'btn small primary', onclick: function () {
          st.edits = { strike: inStrike.value.trim(), expiry: inExp.value.trim(), ratio: st.mode === 'position' ? '' : inRatio.value.trim() };
          if (st.mode === 'position') { st.picked = { candidate_id: c.id, ratio: null, edits: {} }; ['strike', 'expiry'].forEach(function (k) { if (st.edits[k]) { st.picked.edits[k] = st.edits[k]; } }); loadEdited(); return; }
          pickCand(c);
        } }, st.mode === 'position' ? 'Apply edits (re-price)' : 'Apply edits & re-preview'),
        T.h('button', { class: 'btn small', onclick: none }, 'None (0)')));
      return box;
    }
    /* position mode: the edits (strike / expiry) re-price the candidates on the server */
    function loadEdited() {
      var e = (st.picked && st.picked.edits) || {};
      var gen = ++st.gen; var k = key();
      st.status = 'fetching'; paint();
      var q = { account_id: st.account_id, instrument_id: st.instrument_id, mode: 'position' };
      if (e.strike) { q.strike = e.strike; } if (e.expiry) { q.expiry = e.expiry; }
      pollReady('/api/hedges', q, gen, function () { return root.isConnected; }).then(function (r) {
        if (!isLive(gen)) { return; }
        setCands((r && r.candidates) || [], r || {}); st.candsKey = k;
      }, function (err) { if (isLive(gen) && err.code !== 'SUPERSEDED') { st.status = 'error'; st.note = (err.code || 'ERROR') + ': ' + (err.message || err); paint(); } });
    }
    function positionLine() {
      var p = st.position || {};
      var q = Number(p.qty);
      var what = isFinite(q) && q ? (q > 0 ? 'long ' : 'short ') + T.fmt.qty(Math.abs(q)) + ' ' + (p.symbol || st.instrument_id) : (p.symbol || st.instrument_id);
      return T.h('div', { class: 'blue-box hg-pos' }, T.h('b', null, 'Hedge this position: '), what + ' in ' + T.accountName(st.account_id),
        T.h('div', { class: 'hint' }, 'only the hedge legs trade - nothing is bought or sold in ' + (p.symbol || st.instrument_id) + '; a one-leg hedge is its own order, several option legs one all-or-none package'));
    }
    function paintHedges() {
      var head = T.h('div', { class: 'row hg-head' });
      if (!st.instrument_id) { body.appendChild(T.h('div', { class: 'empty' }, 'enter an instrument: candidates appear here as soon as the chain is cached')); return; }
      if (st.mode === 'position') { body.appendChild(positionLine()); }
      if (st.status === 'fetching') { head.appendChild(T.h('span', { class: 'spin' })); head.appendChild(T.h('span', { class: 'muted' }, ' pricing the hedges of ' + st.instrument_id + ' (Cboe chain, delayed) ...')); body.appendChild(head); return; }
      if (st.status === 'flat') { body.appendChild(T.h('div', { class: 'empty' }, st.note || 'no position to hedge')); return; }
      if (st.status === 'error' || st.status === 'unavailable' || st.status === 'not_built') {
        head.appendChild(badge(st.status.toUpperCase(), 'amber')); head.appendChild(T.h('span', { class: 'muted' }, ' ' + (st.note || 'no candidates')));
        head.appendChild(T.h('button', { class: 'btn small', onclick: function () { st.candsKey = null; load(); } }, 'Retry'));
        body.appendChild(head); return;
      }
      if (st.status === 'idle' || !st.cands) {
        if (!complete()) { body.appendChild(T.h('div', { class: 'empty' }, !st.side ? 'choose a side: the candidates are built for it' : 'enter a quantity: the candidates are sized to it')); return; }
        body.appendChild(T.h('div', { class: 'row' }, T.h('span', { class: 'spin' }), T.h('span', { class: 'muted' }, ' loading the candidates ...')));
        autoLoad(); return;
      }
      var meta = st.meta || {};
      head.appendChild(T.h('span', { class: 'muted' }, st.mode === 'position' ? 'sized to the position held ' : 'for ' + sideTxt(st.side) + ' ' + T.fmt.qty(st.qty) + ' ' + st.instrument_id + ' '));
      head.appendChild(T.qchip(meta.chain_quality || (st.cands[0] && st.cands[0].chain_quality) || 'unavailable'));
      var asof = meta.as_of || (st.cands[0] && st.cands[0].as_of); if (asof) { head.appendChild(T.h('span', { class: 'muted' }, ' chain as of ' + T.fmt.time(asof))); }
      head.appendChild(T.h('span', { class: 'spacer' }));
      head.appendChild(T.h('button', { class: 'btn small', title: 'refetch', onclick: function () { st.candsKey = null; load(); } }, 'refresh'));
      body.appendChild(head);
      if (meta.note) { body.appendChild(T.h('div', { class: 'hint' }, meta.note)); }
      var visible = st.cands.filter(function (c) { return !c.zero_size; });
      var hidden = st.cands.filter(function (c) { return c.zero_size; });
      if (!visible.length) { body.appendChild(T.h('div', { class: 'empty' }, 'no candidates for this ' + (st.mode === 'position' ? 'position' : 'primary') + (meta.note ? '' : ' (nothing to hedge, or the chain has no usable strikes)'))); }
      var list = T.h('div', { class: 'hg-list' });
      visible.forEach(function (c, i) {
        var n = i + 1;
        var off = c.available === false;
        var nib = c.not_in_this_build || c.support === 'NOT_IN_THIS_BUILD';
        var row = T.h('div', { class: 'hg-cand' + (off || nib ? ' dim' : '') + (st.picked && st.picked.candidate_id === c.id ? ' picked' : '') + (st.pos.cand === c.id ? ' picked' : ''), dataset: { id: c.id || '' } });
        var title = T.h('div', { class: 'row' }, T.h('kbd', null, String(n <= 9 ? n : '')), T.h('b', null, c.label || c.id || ''), T.h('span', { class: 'muted mono' }, c.id || ''));
        var how = c.submit_via === 'otc' ? 'OTC TAB' : (c.submit_via === 'fx' ? 'FX DESK' : (c.submit_via === 'package' ? 'AON PACKAGE' : (st.mode === 'position' ? 'OWN ORDER' : c.fill_mode)));
        if (how) { title.appendChild(badge(how, how === 'AON' || how === 'AON PACKAGE' ? 'purple' : (c.submit_via === 'otc' || c.submit_via === 'fx' ? 'blue' : ''), c.disclosure || '')); }
        row.appendChild(title);
        if (nib) { row.appendChild(T.nib(c.label || c.id, c.reason || 'not in this build')); list.appendChild(row); return; }
        if (off) { row.appendChild(T.h('div', { class: 'hint' }, 'not available: ' + (c.reason || 'no reason given'))); list.appendChild(row); return; }
        (c.legs || []).forEach(function (l) { row.appendChild(legLine(l)); });
        var kv = [];
        if (c.cost != null) { kv.push(['cost', money(c.cost) + (c.fees != null ? ' + fees ' + money(c.fees) : '')]); }
        if (c.net_cash != null) { kv.push(['net cash', T.h('span', { class: T.signCls(c.net_cash) }, money(c.net_cash))]); }
        if (c.protection) { kv.push(['protection', c.protection]); } else if (c.protection_level != null) { kv.push(['protection level', T.fmt.pct(c.protection_level, 2)]); }
        if (c.greeks && c.greeks.delta_shares != null) { kv.push(['delta (shares)', T.fmt.num(c.greeks.delta_shares, 0)]); }
        if (c.greeks && c.greeks.dv01_position != null) { kv.push(['DV01', T.fmt.money(c.greeks.dv01_position) + ' per bp held; ' + T.fmt.money(c.greeks.dv01_contract) + ' per contract']); }
        if (c.ratio_text) { kv.push(['size', c.ratio_text, c.ratio != null ? 'ratio ' + c.ratio + ' (leg units per unit of the ' + (st.mode === 'position' ? 'position' : 'primary') + ')' : '']); }
        else if (c.ratio != null) { kv.push(['ratio', String(c.ratio)]); }
        if (kv.length) { row.appendChild(T.kv(kv)); }
        if (c.greeks && c.greeks.note) { row.appendChild(T.h('div', { class: 'hint' }, c.greeks.note)); }
        if (c.rule) { row.appendChild(T.h('div', { class: 'hint' }, c.rule)); }
        if (c.disclosure) { row.appendChild(T.h('div', { class: 'hint' }, c.disclosure)); }
        var isPicked = st.mode !== 'position' && st.picked && st.picked.candidate_id === c.id;
        var act = T.h('button', { class: 'btn small' + (isPicked || st.pos.cand === c.id ? ' primary' : '') + ' hg-act', dataset: { cand: c.id || '' }, onclick: function () { mainAction(c); } }, actionLabel(c, n));
        var actRow = T.h('div', { class: 'row' }, act);
        if (st.mode === 'position' && c.editable && (c.editable.strike || c.editable.expiry) && c.submit_via !== 'otc' && c.submit_via !== 'fx') {
          actRow.appendChild(T.h('button', { class: 'btn small', title: 'move the protective strike / the expiry (re-priced on the server)', onclick: function () { st.picked = { candidate_id: c.id, ratio: null, edits: {} }; st.edits = {}; paint(); } }, 'edit strike / expiry'));
        }
        row.appendChild(actRow);
        if (st.picked && st.picked.candidate_id === c.id && c.submit_via !== 'otc' && c.submit_via !== 'fx') { row.appendChild(editBox(c)); }
        if (st.mode === 'position' && st.pos.cand === c.id) {
          if (st.pos.status === 'previewing') { row.appendChild(T.h('div', { class: 'row' }, T.h('span', { class: 'spin' }), T.h('span', { class: 'muted' }, ' pricing the package ...'))); }
          if (st.pos.status === 'error') { row.appendChild(T.h('div', { class: 'error-box' }, st.pos.note, ' ', T.h('button', { class: 'btn small', onclick: function () { previewPosPackage(c); } }, 'Retry'))); }
          if (st.pos.spec) { row.appendChild(paintSpec(st.pos.spec)); }
        }
        list.appendChild(row);
      });
      body.appendChild(list);
      if (hidden.length) {
        body.appendChild(T.h('div', { class: 'hint hg-hidden' }, 'not listed (sizes to nothing): ' + hidden.map(function (c) { return (c.label || c.id).replace(/:.*$/, '') + ' - ' + (c.reason || 'too small'); }).join('; ')));
      }
      if (st.mode === 'position') {
        body.appendChild(T.h('div', { class: 'hint' }, 'no new order in ' + st.instrument_id + ': a one-leg hedge goes to the ticket as its own order (preview, then submit); a hedge of several option legs is one all-or-none package; the TRS overlay and FX forwards open their own desks'));
        return;
      }
      body.appendChild(T.h('div', { class: 'row' }, T.h('button', { class: 'btn small', onclick: none }, 'no hedge (0)')));
      body.appendChild(T.h('div', { class: 'hint' }, 'a chosen hedge is submitted with the primary as one package: the legs wait for the primary (WAITING_PARENT), sized to its filled quantity x ratio, and match on their own quotes afterwards (sequential; a leg can rest); the TRS overlay and FX forwards open their own desks instead'));
    }

    /* ---------------------------------------------------------- strategies ------------------------------------- */
    function loadCatalogue() {
      var k = st.account_id + '|' + st.instrument_id;
      if (catalogue[k]) { st.strat.list = catalogue[k]; paint(); return; }
      st.strat.status = 'loading'; paint();
      T.api.get('/api/strategies', { account_id: st.account_id, instrument_id: st.instrument_id }).then(function (r) {
        if (r && r.pending) { throw new T.ApiError(202, 'PENDING', 'catalogue pending'); }
        catalogue[k] = r; catalogueKeys.push(k); while (catalogueKeys.length > 8) { delete catalogue[catalogueKeys.shift()]; }
        st.strat.list = r; st.strat.status = 'idle'; paint();
      }, function (e) { st.strat.status = 'error'; st.strat.note = (e.code || 'ERROR') + ': ' + (e.message || e); paint(); });
    }
    function stratByKey(k) { var l = (st.strat.list && st.strat.list.strategies) || []; for (var i = 0; i < l.length; i++) { if (l[i].key === k) { return l[i]; } } return null; }
    function defaultParams(s) {
      var p = { underlying: st.instrument_id, units: 1000, tenor_months: 3, strikes: {} };
      if ((s.legs || []).some(function (l) { return l.other; })) { p.other = ''; }
      return p;
    }
    function previewStrategy() {
      var s = stratByKey(st.strat.key); if (!s) { return; }
      var p = st.strat.params; var gen = ++st.strat.gen;
      var params = { underlying: p.underlying, units: Number(p.units), tenor_months: Number(p.tenor_months) };
      if (p.other) { params.other = p.other; }
      var strikes = {}; Object.keys(p.strikes || {}).forEach(function (i) { if (String(p.strikes[i]).trim() !== '') { strikes[i] = String(p.strikes[i]).trim(); } });
      if (Object.keys(strikes).length) { params.strikes = strikes; }
      st.strat.status = 'previewing'; st.strat.note = ''; paint();
      postReady('/api/strategies/preview', { account_id: st.account_id, key: s.key, params: params }, gen, function (g) { return g === st.strat.gen && root.isConnected; }).then(function (spec) {
        if (gen !== st.strat.gen) { return; }
        st.strat.spec = spec; st.strat.status = 'ready';
        if (opts.onLegs) { opts.onLegs(legIds()); }
        paint();
      }, function (e) {
        if (gen !== st.strat.gen || e.code === 'SUPERSEDED') { return; }
        st.strat.spec = null; st.strat.status = 'error'; st.strat.note = (e.code || 'ERROR') + ': ' + (e.message || e); paint();
      });
    }
    function schedulePreview() { clearTimeout(st.strat.timer); st.strat.timer = setTimeout(previewStrategy, 400); }
    function drawPayoff(canvas, payoff, spot) {
      /* the server's points: payoff.payoff ([[price, P&L], ...]); payoff.grid is its description ({points, low, high}) */
      var pts = payoff ? (Array.isArray(payoff.payoff) ? payoff.payoff : (Array.isArray(payoff.grid) ? payoff.grid : [])) : [];
      var ctx = canvas.getContext('2d'); if (!ctx || !pts.length) { return; }
      var W = canvas.width, H = canvas.height; var cs = getComputedStyle(document.documentElement);
      var col = function (v, d) { var c = cs.getPropertyValue(v).trim(); return c || d; };
      ctx.clearRect(0, 0, W, H);
      var xs = pts.map(function (p) { return Number(p[0]); }), ys = pts.map(function (p) { return Number(p[1]); });
      var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
      if (y0 === y1) { y0 -= 1; y1 += 1; }
      var pad = 6; var X = function (x) { return pad + (x - x0) / (x1 - x0) * (W - 2 * pad); }; var Y = function (y) { return H - pad - (y - y0) / (y1 - y0) * (H - 2 * pad); };
      ctx.strokeStyle = col('--line', '#232a37'); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(pad, Y(0)); ctx.lineTo(W - pad, Y(0)); ctx.stroke();
      if (spot != null) { var sx = X(Number(spot)); ctx.strokeStyle = col('--fg3', '#5d6474'); ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(sx, pad); ctx.lineTo(sx, H - pad); ctx.stroke(); ctx.setLineDash([]); }
      ctx.lineWidth = 1.5;
      for (var i = 1; i < pts.length; i++) {
        ctx.strokeStyle = (ys[i] >= 0 && ys[i - 1] >= 0) ? col('--up', '#3ecf8e') : col('--down', '#ff5c5c');
        ctx.beginPath(); ctx.moveTo(X(xs[i - 1]), Y(ys[i - 1])); ctx.lineTo(X(xs[i]), Y(ys[i])); ctx.stroke();
      }
      ctx.fillStyle = col('--fg3', '#5d6474'); ctx.font = '9px monospace';
      ctx.fillText(T.fmt.px(x0), pad, H - 1); ctx.textAlign = 'right'; ctx.fillText(T.fmt.px(x1), W - pad, H - 1); ctx.textAlign = 'left';
      ctx.fillText(T.fmt.money(y1), pad, 9); ctx.fillText(T.fmt.money(y0), pad, H - 10);
    }
    function paintSpec(spec) {
      var box = T.h('div', { class: 'hg-spec' });
      if (spec.available === false) { box.appendChild(T.h('div', { class: 'error-box' }, badge('NOT AVAILABLE', 'red'), ' ' + (spec.reason || ''))); return box; }
      var fm = spec.fill_mode || 'SEQUENTIAL';
      var head = T.h('div', { class: 'row' }, badge(fm, fm === 'AON' ? 'purple' : 'blue', fm === 'AON' ? 'option-only package: all-or-none' : 'stock / futures primary with hedge legs'),
        T.h('span', { class: 'muted' }, spec.strategy_key === 'POSITION_HEDGE' ? (spec.title || 'hedge of the position held') : ((spec.strategy && spec.strategy.title) || spec.key || '')), spec.expiry ? T.h('span', { class: 'mono' }, ' expiry ' + spec.expiry) : null);
      if (spec.chain) { head.appendChild(T.qchip(spec.chain.quality)); if (spec.chain.as_of) { head.appendChild(T.h('span', { class: 'muted' }, ' ' + T.fmt.time(spec.chain.as_of))); } }
      else if (spec.chain_quality) { head.appendChild(T.qchip(spec.chain_quality)); if (spec.as_of) { head.appendChild(T.h('span', { class: 'muted' }, ' ' + T.fmt.time(spec.as_of))); } }
      box.appendChild(head);
      if (spec.underlying) { box.appendChild(T.h('div', { class: 'row' }, T.h('span', { class: 'muted' }, 'spot '), T.px(spec.underlying, { size: 'inline' }))); }
      else if (spec.spot != null) { box.appendChild(T.h('div', { class: 'row' }, T.h('span', { class: 'muted' }, 'spot ' + (spec.underlying_id || '') + ' '), T.h('span', { class: 'mono' }, T.fmt.px(spec.spot)))); }
      if (spec.expiry_rule) { box.appendChild(T.h('div', { class: 'hint' }, spec.expiry_rule)); }
      var t = T.h('table', { class: 'hg-legs' });
      t.appendChild(T.h('thead', null, T.h('tr', null, T.h('th', { class: 'l' }, '#'), T.h('th', { class: 'l' }, 'leg'), T.h('th', null, 'qty'), T.h('th', null, 'price'), T.h('th', null, 'cash'))));
      var tb = T.h('tbody');
      (spec.legs || []).forEach(function (l) {
        var name = l.kind === 'OPTION' && l.expiry ? (l.symbol || '') + ' ' + l.expiry + ' ' + (l.right || '') + T.fmt.px(l.strike).replace(/\.00$/, '')
                                                   : (l.symbol || l.instrument_id || '') + (l.strike && !l.symbol ? ' ' + l.strike : '');
        var priceCell = l.quote && l.price_basis && l.quote[l.price_basis] != null ? T.px(l.quote, { field: l.price_basis, size: 'inline' })
          : (l.quote && l.est_price != null ? T.px(Object.assign({}, l.quote, { value: l.est_price }), { field: 'value', size: 'inline' }) : (l.est_price != null ? T.h('span', { class: 'mono' }, T.fmt.px(l.est_price)) : T.px(null)));
        tb.appendChild(T.h('tr', { class: l.available === false ? 'nib' : '' }, T.h('td', { class: 'l' }, String(l.index != null ? l.index : '')),
          T.h('td', { class: 'l' }, T.h('span', { class: 'side-' + T.esc(l.order_side || l.side || '') }, sideTxt(l.order_side || l.side)), ' ', T.h('span', { class: 'mono' }, name)),
          T.h('td', { class: 'num' }, T.fmt.qty(l.qty)), T.h('td', { class: 'num' }, priceCell, T.h('span', { class: 'muted' }, ' ' + (l.price_basis || ''))), T.h('td', { class: 'num ' + T.signCls(l.cash) }, money(l.cash))));
        var hints = [];
        if (l.rule) { hints.push('rule ' + l.rule + (l.override ? ' -> override ' + l.override : '') + (l.target ? ' -> target ' + l.target : '') + (l.strike ? ' -> listed ' + l.strike : '')); }
        if (l.pct_from_spot != null) { hints.push(T.fmt.ratePct(l.pct_from_spot) + ' from spot'); }
        if (l.size_rule) { hints.push(l.size_rule); }
        if (l.available === false) { hints.push('NOT AVAILABLE: ' + (l.reason || '')); }
        if (hints.length) { tb.appendChild(T.h('tr', null, T.h('td', { class: 'l hint', colspan: '5' }, hints.join(' . ')))); }
      });
      t.appendChild(tb); box.appendChild(t);
      var tot = spec.totals || {}; var mg = spec.margin || {};
      var prem = tot.net_premium != null ? tot.net_premium : tot.net_option_premium;
      var optMargin = tot.incremental_option_margin != null ? tot.incremental_option_margin : (mg.incremental != null ? mg.incremental : (mg.requirement != null ? mg.requirement : tot.option_margin));
      var kv = [['net premium', T.h('span', { class: T.signCls(prem) }, money(prem))], ['fees', money(tot.fees)], ['net cash today', T.h('span', { class: T.signCls(tot.net_cash) }, money(tot.net_cash != null ? tot.net_cash : tot.net_cash_today))],
                tot.net_cash_after_fees != null ? ['after fees', T.h('span', { class: T.signCls(tot.net_cash_after_fees) }, money(tot.net_cash_after_fees))] : null,
                ['delta (shares)', tot.delta_shares != null ? T.fmt.num(tot.delta_shares, 0) : 'unavailable'], ['dollar delta', tot.dollar_delta != null ? money(tot.dollar_delta) : 'unavailable'],
                ['vega', tot.vega != null ? T.fmt.num(tot.vega, 2) : 'unavailable'], ['theta', tot.theta != null ? T.fmt.num(tot.theta, 2) : 'unavailable'],
                ['option margin (once)', optMargin != null ? money(optMargin) + (mg.incremental != null ? ' (incremental, against the account)' : '') : 'n/a', mg.method || ''],
                ['futures IM', tot.futures_margin != null ? money(tot.futures_margin) : 'n/a']];
      box.appendChild(T.h('h2', null, 'Totals')); box.appendChild(T.kv(kv));
      if (tot.margin_detail) { box.appendChild(T.h('div', { class: 'hint' }, tot.margin_detail)); }
      if (tot.greeks_note) { box.appendChild(T.h('div', { class: 'hint' }, tot.greeks_note)); }
      var f = spec.funding;
      if (f) {
        box.appendChild(T.h('h2', null, 'Funding'));
        box.appendChild(T.kv([['need', money(f.need)], ['available', money(f.available)], ['shortfall', money(f.shortfall)], ['sweep-in from Treasury', money(f.sweep_in)], f.margin_once != null ? ['margin reserved once', money(f.margin_once)] : null,
          ['fundable', f.ok === false ? 'NO' : 'yes'], f.confirm_required ? ['confirm', 'REQUIRED'] : null]));
        if (f.need_formula) { box.appendChild(T.h('div', { class: 'hint' }, f.need_formula)); }
      }
      var po = spec.payoff;
      if (po && (po.grid || po.payoff)) {
        box.appendChild(T.h('h2', null, 'Payoff at expiry'));
        var cv = T.h('canvas', { width: '340', height: '110', class: 'hg-payoff', 'aria-label': 'payoff grid' }); box.appendChild(cv);
        drawPayoff(cv, po, spec.spot);
        box.appendChild(T.kv([['max gain', po.max_gain == null ? 'unlimited' : money(po.max_gain)], ['max loss', po.max_loss == null ? 'unlimited' : money(po.max_loss)],
          ['breakevens', (po.breakevens || []).map(function (b) { return T.fmt.px(b); }).join(' / ') || 'none on the grid']]));
        if (po.grid_note) { box.appendChild(T.h('div', { class: 'hint' }, po.grid_note)); }
      }
      if (spec.warnings && spec.warnings.length) { box.appendChild(T.components.preview ? T.h('div', null, spec.warnings.map(function (w) { return T.h('div', { class: 'warn-box' }, typeof w === 'string' ? w : (w.message || '')); })) : null); }
      if (spec.blockers && spec.blockers.length && T.components.preview) { box.appendChild(T.components.preview.blockers(spec.blockers)); }
      /* the spec's conventions: a list of keys, or the server's {key: sentence} map (strategies.PackageSpec) */
      var cv = spec.conventions || [];
      var convBox = T.h('div', { class: 'conv' });
      var keys = Array.isArray(cv) ? cv.slice() : Object.keys(cv);
      if (fm === 'AON' && keys.indexOf('AON') < 0) { keys.unshift('AON'); }
      keys.forEach(function (k) { var sent = (!Array.isArray(cv) && cv[k]) || T.conv(k); if (sent) { convBox.appendChild(T.h('div', null, T.h('b', { title: k }, T.convTitle(k)), sent)); } });
      if (convBox.childNodes.length) { box.appendChild(convBox); }
      if (fm !== 'AON') { box.appendChild(T.h('div', { class: 'hint' }, 'sequential: the stock / futures leg is the primary; the option legs wait for it (WAITING_PARENT), sized to its filled quantity x ratio, and can rest')); }
      if (spec.strategy_key === 'POSITION_HEDGE') { box.appendChild(T.h('div', { class: 'hint' }, 'hedges the position already held: only these option legs trade, together or not at all; the payoff includes the position (not traded)')); }
      var canSubmit = !(spec.blockers && spec.blockers.length) && (spec.legs || []).length && !(spec.legs || []).some(function (l) { return l.available === false; });
      var btn = T.h('button', { class: 'btn primary', id: 'hg-submit', disabled: !canSubmit, title: fm === 'AON' ? 'one submit: every leg fills together or nothing fills' : 'one submit: primary + hedge legs' },
        fm === 'AON' ? 'Submit package (all-or-none)' : 'Submit package (sequential)');
      btn.addEventListener('click', function () {
        if (!opts.onSubmitPackage) { return; }
        var pk;
        if (spec.strategy_key === 'POSITION_HEDGE') {
          /* the hedge of a position held: the same candidate and edits the preview priced (no order in the underlying) */
          var sp = spec.params || {};
          pk = { strategy_key: 'POSITION_HEDGE', underlying: sp.underlying || st.instrument_id, candidate_id: sp.candidate_id, edits: sp.edits || {} };
        } else {
          var p = st.strat.params;
          pk = { strategy_key: spec.strategy_key || spec.key || st.strat.key, units: Number(p.units), tenor_months: Number(p.tenor_months) };
          var strikes = {}; Object.keys(p.strikes || {}).forEach(function (i) { if (String(p.strikes[i]).trim() !== '') { strikes[i] = String(p.strikes[i]).trim(); } });
          if (Object.keys(strikes).length) { pk.strikes = strikes; }
          if (p.other) { pk.other = p.other; }
          if (p.underlying) { pk.underlying = p.underlying; }
        }
        opts.onSubmitPackage({ account_id: st.account_id, package: pk, tif: opts.tif ? opts.tif() : 'DAY', preview_id: spec.preview_id || null }, spec);
      });
      box.appendChild(T.h('div', { class: 'row' }, btn, T.h('span', { class: 'hint' }, spec.preview_id ? 'preview ' + spec.preview_id : '')));
      return box;
    }
    function paintStrategies() {
      var s = st.strat;
      if (!st.instrument_id) { body.appendChild(T.h('div', { class: 'empty' }, 'enter an instrument first: templates resolve against its chain')); return; }
      if (s.status === 'loading' || !s.list) {
        if (s.status === 'error') { body.appendChild(T.h('div', { class: 'error-box' }, s.note)); body.appendChild(T.h('button', { class: 'btn small', onclick: loadCatalogue }, 'Retry')); return; }
        body.appendChild(T.h('div', { class: 'row' }, T.h('span', { class: 'spin' }), T.h('span', { class: 'muted' }, ' loading the templates ...'))); return;
      }
      var groups = s.list.groups || [];
      var sel = T.h('select', { 'aria-label': 'group' }); sel.appendChild(T.h('option', { value: '' }, 'all groups'));
      groups.forEach(function (g) { var gk = Array.isArray(g) ? g[0] : g.key, gl = Array.isArray(g) ? g[1] : g.label; sel.appendChild(T.h('option', { value: gk, selected: gk === s.group }, gl)); });
      sel.addEventListener('change', function () { s.group = sel.value; paint(); });
      var pick = T.h('select', { 'aria-label': 'strategy', id: 'hg-strategy' }); pick.appendChild(T.h('option', { value: '' }, 'pick a template ...'));
      (s.list.strategies || []).forEach(function (x) { if (s.group && x.group !== s.group) { return; } pick.appendChild(T.h('option', { value: x.key, selected: x.key === s.key }, (x.available === false ? '[n/a] ' : '') + x.title)); });
      pick.addEventListener('change', function () { s.key = pick.value || null; var x = stratByKey(s.key); s.spec = null; s.status = 'idle'; s.params = x ? defaultParams(x) : {}; if (x && x.available !== false) { schedulePreview(); } paint(); });
      body.appendChild(T.h('div', { class: 'row' }, sel, pick));
      var x = stratByKey(s.key);
      if (!x) { body.appendChild(T.h('div', { class: 'hint' }, (s.list.strategies || []).length + ' templates; option-only templates fill all-or-none')); return; }
      body.appendChild(T.h('div', { class: 'hg-strat' }, T.h('b', null, x.title), T.h('div', { class: 'hint' }, x.simple || ''), x.thesis ? T.h('div', { class: 'hint' }, 'thesis: ' + x.thesis) : null, x.best_when ? T.h('div', { class: 'hint' }, 'best when: ' + x.best_when) : null, x.risk ? T.h('div', { class: 'hint' }, 'risk: ' + x.risk) : null));
      if (x.available === false) { body.appendChild(T.h('div', { class: 'error-box' }, badge('NOT AVAILABLE', 'red'), ' ' + (x.reason || ''))); return; }
      var p = s.params;
      var inUnits = T.h('input', { type: 'number', min: '1', step: '1', value: p.units, 'aria-label': 'units' });
      var inTenor = T.h('input', { type: 'number', min: '1', step: '1', value: p.tenor_months, 'aria-label': 'tenor months' });
      inUnits.addEventListener('input', function () { p.units = inUnits.value; schedulePreview(); });
      inTenor.addEventListener('input', function () { p.tenor_months = inTenor.value; schedulePreview(); });
      var rows = [T.field('underlying', T.h('input', { type: 'text', value: p.underlying || st.instrument_id, disabled: true })), T.field('units', inUnits, 'base units: stock qty = units x size; options = max(1, round(units x size / 100)) contracts; futures notional-matched'),
                  T.field('tenor (months)', inTenor, 'the nearest listed expiry on or after today + months')];
      if (p.other !== undefined) { var inOther = T.h('input', { type: 'text', value: p.other, placeholder: 'the other name (symbol)' }); inOther.addEventListener('input', function () { p.other = inOther.value.trim().toUpperCase(); schedulePreview(); }); rows.push(T.field('other name', inOther)); }
      (x.legs || []).forEach(function (l, i) {
        if (l.kind !== 'OPTION') { return; }
        var idx = String(i + 1);
        var inK = T.h('input', { type: 'text', value: (p.strikes && p.strikes[idx]) || '', placeholder: l.strike || 'ATM' });
        inK.addEventListener('input', function () { p.strikes = p.strikes || {}; p.strikes[idx] = inK.value; schedulePreview(); });
        rows.push(T.field('leg ' + idx + ' ' + l.side + ' ' + l.type + (l.tenor ? ' ' + l.tenor + 'm' : ''), inK, 'strike rule ' + (l.strike || 'ATM') + '; override: a small number is a percent from spot, |value| >= 60 an absolute strike'));
      });
      body.appendChild(T.form(rows));
      body.appendChild(T.h('div', { class: 'row' }, T.h('button', { class: 'btn small', onclick: previewStrategy }, 'Preview package'), s.status === 'previewing' ? T.h('span', { class: 'spin' }) : null, s.status === 'previewing' ? T.h('span', { class: 'muted' }, ' pricing every leg from the cached chain ...') : null));
      if (s.status === 'error') { body.appendChild(T.h('div', { class: 'error-box' }, s.note)); }
      if (s.spec) { body.appendChild(paintSpec(s.spec)); }
    }

    /* ---------------------------------------------------------- handle ----------------------------------------- */
    function update(f) {
      f = f || {};
      var changedInst = f.instrument_id !== undefined && f.instrument_id !== st.instrument_id;
      var changedAcct = f.account_id !== undefined && f.account_id !== st.account_id;
      var changedMode = f.mode !== undefined && (f.mode === 'position' ? 'position' : 'order') !== st.mode;
      ['account_id', 'instrument_id', 'side', 'qty', 'inst', 'position'].forEach(function (k) { if (f[k] !== undefined) { st[k] = f[k]; } });
      if (f.mode !== undefined) { st.mode = f.mode === 'position' ? 'position' : 'order'; }
      if (changedInst || changedAcct || changedMode) {
        st.gen++; st.cands = null; st.candsKey = null; st.status = 'idle'; st.picked = null; st.edits = {}; st.pos = { spec: null, cand: null, status: 'idle', note: '', gen: st.pos.gen + 1 };
        st.strat.list = null; st.strat.key = null; st.strat.spec = null; st.strat.status = 'idle'; st.strat.gen++;
        if (st.mode === 'position') { st.tab = 'hedges'; }
        if (st.tab === 'strategies' && st.instrument_id) { loadCatalogue(); }
      } else if (st.candsKey && st.candsKey !== key()) { st.status = 'idle'; }
      paint();
      autoLoad();
    }
    paint();
    if (st.tab === 'strategies') { loadCatalogue(); }
    autoLoad();
    return { el: root, update: update, seed: seed, load: load, autoLoad: autoLoad,
             pick: function (n) { var c = (st.cands || []).filter(function (x) { return !x.zero_size; })[n - 1]; if (c) { st.edits = {}; mainAction(c); } }, none: none,
             picked: function () { return st.picked; }, tab: function (k) { if (k) { st.tab = k; paint(); if (k === 'strategies') { loadCatalogue(); } } return st.tab; },
             legIds: legIds, state: function () { return st; }, destroy: function () { st.gen++; st.strat.gen++; clearTimeout(st.strat.timer); T.clear(el); } };
  }
  T.components.hedge = { mount: mount };
})();
