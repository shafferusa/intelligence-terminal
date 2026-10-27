/* Preview panel (TERMINAL_DESIGN.md s17.6, s10.3; WP-13): pure renderers for the ticket.
 *
 *   T.components.preview.render(el, pv, opts)   the s10.3 preview: leg (price + freshness + basis), execution (fill now /
 *                                               cap / session / rests until / expires), trail block, economics (gross, fee,
 *                                               net cash, settle date), funding plan (settled / pending / reserved /
 *                                               available / need / shortfall / sweep-in from Treasury / PB financing / FX
 *                                               leg / confirm), borrow terms, margin (options, futures, package once),
 *                                               warnings (amber; LOCKUP badge), compliance (blue), risk (amber), blockers
 *                                               (red, with the code and a plain-English reason), the convention sentences.
 *   T.components.preview.blockers(list, pv)     the blocker list alone (also used for 400 / 409 submit answers).
 *   T.components.preview.diff(diff, opts)       the PREVIEW_STALE bar: "339.50 -> 341.10 (+0.47 %)" + Accept & submit / Cancel.
 *   T.components.preview.result(res, ctx)       the result after a submit: order, fills with the quote used, resting qty,
 *                                               sweeps executed, borrow, hedge package, "resting until", replayed / recovered.
 *   T.components.preview.packageResult(res)     the result of a strategy package submit (AON or sequential).
 *   T.components.preview.errorAsBlocker(e)      an ApiError -> a blocker row {code, message, details, http}.
 *
 * Build 2 (P10): the result's fill prices are the EXECUTED prices, plain (with the fill's own quote record in the tooltip when
 * it carries one) - never a struck-through 'NO STATUS' chip (USX-21); the header echoes the typed limit / stop / trail when the
 * preview carries none (a refused limit reads 'LMT 300.001 (your limit, refused)', USX-34); a non-USD need shows the
 * funding.fx_conversion plan (pair, deal rate, USD amount, value date, spread cost, executable or why not) that the ticket's
 * 'convert USD -> GBP as shown' box accepts (OA-12); NO_QUOTE reads 'no quote yet - fetching' (USX-08).
 *
 * Conventions this file keeps
 * - Nothing is computed here: every figure is a field of the server's preview / result; the panel formats and labels.
 *   Cents fields (`*_c`, integers) are shown through T.fmt.cents, decimal strings through T.fmt.money.
 * - Every market price carries its freshness (T.px on the record the server used); a limit / stop / trail price is the
 *   user's own number and is labelled "your limit" / "your stop", never dressed up as a market print.
 * - Missing blocks render as "unavailable" lines, never as zeros.
 * - A blocker's plain-English reason comes from the s1.3 convention sentence that applies (ELIGIBILITY, QP, LIFETIME,
 *   STALE, BORROW_AVAIL, AVAILABLE / SWEEP, TRAILING, AON, PRIVATE_TRADE) through T.conv.
 * - ASCII only; no frameworks.
 */
(function () {
  'use strict';
  var T = window.T;
  var REASON_KEYS = {
    NOT_ELIGIBLE: 'ELIGIBILITY', NOT_ELIGIBLE_QP: 'QP', LIFETIME_CLOSED: 'LIFETIME', QUOTE_STALE: 'STALE', NO_BORROW: 'BORROW_AVAIL',
    INSUFFICIENT_FUNDS: 'AVAILABLE', INSUFFICIENT_FUNDS_CCY: 'AVAILABLE', CONFIRM_REQUIRED: 'SWEEP', TRAIL_INVALID: 'TRAILING', AON_LEGS: 'AON', NO_QUOTE: 'STALE',
    AON_SIZE: 'AON', AON_STALE: 'AON', LOCKUP: 'PRIVATE_TRADE', STOP_SIDE: 'STOP_RULE', SESSION: 'SESSION', LAST_SESSION: 'SESSION',
    PRICE_REQUIRED: 'PRIVATE_TRADE', PRICE_TICK: 'LIMIT_RULE', FUNDING_SHORTFALL: 'SWEEP', PREVIEW_STALE: 'FILL_PRICE'
  };
  var PLAIN = {
    QUOTE_STALE: 'the last observed print is too old to execute against; the order can rest as a LIMIT, or wait for a fresh quote',
    NO_QUOTE: 'no quote yet - the first print is being fetched (up to 15 min delayed); the preview refreshes by itself when it arrives',
    USE_OWN_TICKET: 'this instrument is dealt on its own ticket (the bond ticket, the FX desk, the private ticket or the OTC tab), not with an order',
    INSUFFICIENT_FUNDS_CCY_CONVERT: 'tick "convert USD as shown" to buy the currency at the fill (the conversion plan is below), or deal it on the FX desk first',
    NO_BORROW: 'a short sale needs a borrow first; none is available for this name right now',
    INSUFFICIENT_FUNDS: 'the worst-case cash need exceeds own available + Treasury available + PB headroom; add funds, transfer, or size down',
    INSUFFICIENT_FUNDS_CCY: 'the foreign-currency need exceeds the cash held in that currency; deal the FX leg first (never automatic)',
    NOT_ELIGIBLE: 'an eligibility mechanic, not a risk warning: this lifetime does not meet the OTC / prime-broker threshold',
    NOT_ELIGIBLE_QP: 'an eligibility mechanic: this lifetime is below its qualified-purchaser threshold for Private Markets',
    LIFETIME_CLOSED: 'this account belongs to a closed lifetime; History is read-only - trade in the open lifetime',
    CONFIRM_REQUIRED: 'a fat-finger guard: confirm the sweep-in / notional once and the same request is resubmitted',
    PREVIEW_STALE: 'the price moved against you (or the preview expired) since you previewed; review the fresh numbers',
    INTEGRITY_HOLD: 'the whole-ledger check failed and is unresolved; nothing can be written until it is fixed (see Requires your attention)',
    FUNDING_SHORTFALL: 'this account has an open funding shortfall in this currency; fund it before placing new orders',
    OVERSELL: 'a SELL cannot flip the position through zero: SELL the long quantity and SELL SHORT the rest with a borrow',
    CLOSE_ONLY_MISMATCH: 'a closing side larger than the position it closes',
    STOP_SIDE: 'this stop would trigger at once against the last print; use a limit instead',
    TRAIL_INVALID: 'a trailing stop takes exactly one of trail amount or trail %, above 0 (and below 100 %)',
    AON_LEGS: 'an all-or-none package must be options on one underlying; packages with a stock or futures leg are sequential',
    LOCKUP: 'this sale falls inside the lot\'s lock-up: a warning, never a block',
    UNSUPPORTED: 'this instrument is unsupported or computed-only in this build: model marks never execute',
    NOT_TRADEABLE_HERE: 'this instrument does not trade in this kind of account',
    QTY_INVALID: 'quantity must be a positive multiple of the lot',
    PRICE_REQUIRED: 'this order type needs a price', PRICE_TICK: 'the price is not on the instrument\'s tick grid',
    SESSION: 'no session for this instrument within 16 days', LAST_SESSION: 'no new opening trades after 15:30 ET on the expiry day',
    INSTRUMENT_EXPIRED: 'the contract is past its last trade date', NOT_IN_THIS_BUILD: 'this needs a package that is not in this build',
    NOT_BUILT: 'this needs a package that is not built yet', NETWORK: 'the server did not answer', PENDING: 'the server is still fetching'
  };
  function money(x, dp) { return x == null || x === '' ? 'unavailable' : T.fmt.money(x, dp); }
  function sec(title, body, cls) { var s = T.h('div', { class: 'section pv-sec ' + (cls || '') }); s.appendChild(T.h('h2', null, title)); T.append(s, body); return s; }
  function badge(txt, cls, title) { return T.h('span', { class: 'badge ' + (cls || ''), title: title || '' }, txt); }
  function signedMoney(x) { var s = money(x); var cls = T.signCls(x); return T.h('span', { class: cls }, s); }
  /* the QuoteRecord-like record behind the preview's quote block (T.px wants quality / quote_time / age_s / source) */
  function quoteRec(pv, live) {
    var q = pv && pv.quote; if (!q) { return live || null; }
    return Object.assign({}, q);
  }
  function sideLabel(s) { return String(s || '').replace(/_/g, ' '); }
  /* USX-34: a price the server did not echo (a refused limit) is the user's own number from the request, labelled as such */
  function own(pv, req, k) {
    if (pv[k] != null && pv[k] !== '') { return T.fmt.px(pv[k]); }
    var v = req && req[k];
    return v != null && v !== '' ? String(v) + ' (yours' + ((pv.blockers || []).length ? ', refused' : '') + ')' : 'n/a';
  }
  function typeLabel(pv, req) {
    var t = pv.order_type || (req && req.order_type) || '';
    if (t === 'LIMIT') { return 'LMT ' + own(pv, req, 'limit_px'); }
    if (t === 'STOP') { return 'STP ' + own(pv, req, 'stop_px'); }
    if (t === 'STOP_LIMIT') { return 'STP ' + own(pv, req, 'stop_px') + ' LMT ' + own(pv, req, 'limit_px'); }
    if (t === 'TRAILING_STOP') {
      var tr = pv.trail || {};
      var amt = tr.trail_amt != null ? tr.trail_amt : (req && req.trail_amt); var pct = tr.trail_pct != null ? tr.trail_pct : (req && req.trail_pct);
      return 'TRAIL ' + (amt != null && amt !== '' ? T.fmt.px(amt) : (pct != null && pct !== '' ? T.fmt.ratePct(pct) : ''));
    }
    return t === 'MARKET' ? 'MKT' : t;
  }
  /* OA-12: the USD -> currency conversion that would fund a non-USD need (fx.conversion_plan, C17) */
  function conversionBox(fc) {
    if (!fc || typeof fc !== 'object') { return null; }
    function c(name) { return typeof fc[name] === 'number' ? T.fmt.cents(fc[name]) : (fc[name.replace(/_c$/, '')] != null ? T.fmt.money(fc[name.replace(/_c$/, '')]) : null); }
    var rows = [['deal', 'BUY ' + (fc.buy_ccy || '') + ' ' + (c('buy_c') || 'n/a') + ' / SELL ' + (fc.sell_ccy || 'USD') + ' ' + (c('usd_c') || 'n/a')],
                ['rate', (fc.rate_quoted || fc.rate || 'n/a') + (fc.pair ? ' (' + fc.pair + (fc.mid ? ' mid ' + fc.mid : '') + ', the desk spread against you)' : '')],
                ['spread cost', c('spread_cost_c') ? 'USD ' + c('spread_cost_c') : 'n/a'], ['value date', (fc.value_date || 'n/a') + (fc.settle_date ? ' (the trade settles ' + fc.settle_date + ')' : '')]];
    if (fc.usd_available != null) { rows.push(['USD available', T.fmt.money(fc.usd_available) + (fc.usd_fundable === false ? ' - NOT enough for the USD side' : '')]); }
    rows.push(['executable', fc.executable ? (fc.requested ? 'yes - converts at the fill (asked for)' : 'yes - tick "convert USD as shown" to use it') : 'NO: ' + (fc.reason || 'not executable now')]);
    var box = T.h('div', { class: fc.executable ? 'blue-box pv-fxconv' : 'warn-box pv-fxconv' }, T.h('b', null, 'FX conversion ' + (fc.requested ? '(asked for)' : '(offered)')), T.kv(rows));
    if (fc.text) { box.appendChild(T.h('div', { class: 'hint' }, fc.text)); }
    return box;
  }
  function plain(code) { return PLAIN[code] || ''; }
  function reasonFor(code) { var k = REASON_KEYS[code]; return k ? T.conv(k) : ''; }
  function detailsOf(e) {
    if (!e) { return {}; }
    if (e.details && typeof e.details === 'object') { return e.details; }
    var b = e.body && e.body.error; return (b && b.details && typeof b.details === 'object') ? b.details : {};
  }
  /* the three amounts of INSUFFICIENT_FUNDS (cents ints from the core; decimal strings tolerated) */
  function amounts(d) {
    d = d || {};
    function c(name) { if (typeof d[name + '_c'] === 'number') { return T.fmt.cents(d[name + '_c']); } if (d[name] != null) { return T.fmt.money(d[name]); } return null; }
    var rows = [];
    var need = c('need'); var own = c('own_available'); var tre = c('treasury_available'); var pb = c('pb_headroom');
    if (need != null) { rows.push(['need', need]); }
    if (own != null) { rows.push(['own available', own]); }
    if (tre != null) { rows.push(['Treasury available', tre]); }
    if (pb != null) { rows.push(['PB headroom', pb]); }
    if (d.sweep_in != null) { rows.push(['sweep-in', T.fmt.money(d.sweep_in)]); }
    if (d.threshold != null) { rows.push(['confirm threshold', T.fmt.money(d.threshold)]); }
    if (d.notional != null) { rows.push(['notional', T.fmt.money(d.notional)]); }
    if (d.nav != null) { rows.push(['lifetime NAV', T.fmt.money(d.nav)]); }
    if (d.fx_leg && typeof d.fx_leg === 'object') { rows.push(['FX leg', 'buy ' + d.fx_leg.buy_ccy + ' ' + T.fmt.money(d.fx_leg.amount) + ' / sell ' + d.fx_leg.sell_ccy + ' first']); }
    if (d.fx_conversion && typeof d.fx_conversion === 'object') { rows.push(['FX conversion', conversionBox(d.fx_conversion)]); }
    if (d.locate && typeof d.locate === 'object') { rows.push(['locate', 'available ' + T.fmt.qty(d.locate.qty_available) + ' of ' + T.fmt.qty(d.locate.qty) + ' . tier ' + (d.locate.tier || '?')]); if (d.locate.basis) { rows.push(['basis', d.locate.basis]); } }
    if (d.lifetime_id) { rows.push(['lifetime', d.lifetime_id]); }
    if (d.qp_threshold != null) { rows.push(['QP threshold', T.fmt.money(d.qp_threshold)]); }
    if (d.reason && typeof d.reason === 'string' && !d.locate) { rows.push(['reason', d.reason]); }
    if (d.quality) { rows.push(['quote quality', d.quality + (d.quote_time ? ' . ' + T.fmt.time(d.quote_time) : '')]); }
    if (d.last != null && d.stop_px != null) { rows.push(['last / stop', T.fmt.px(d.last) + ' / ' + T.fmt.px(d.stop_px)]); }
    if (d.position != null) { rows.push(['position', T.fmt.qty(d.position) + (d.working_sells != null ? ' (working sells ' + T.fmt.qty(d.working_sells) + ')' : '') + (d.working_covers != null ? ' (working covers ' + T.fmt.qty(d.working_covers) + ')' : '')]); }
    return rows.length ? T.kv(rows) : null;
  }
  function blockers(list, pv) {
    var box = T.h('div', { class: 'pv-blockers' });
    (list || []).forEach(function (b) {
      var code = b.code || 'ERROR';
      var row = T.h('div', { class: 'pv-blocker error-box', dataset: { code: code } });
      row.appendChild(T.h('div', { class: 'row' }, badge(code, 'red', 'http ' + (b.http || '')), T.h('span', { class: 'pv-msg' }, b.message || '')));
      var p = plain(code);
      if (code === 'INSUFFICIENT_FUNDS_CCY' && b.details && b.details.fx_conversion && b.details.fx_conversion.executable) { p = plain('INSUFFICIENT_FUNDS_CCY_CONVERT'); }
      if (p) { row.appendChild(T.h('div', { class: 'hint' }, p)); }
      var am = amounts(b.details); if (am) { row.appendChild(am); }
      if (b.details && typeof b.details.note === 'string' && b.details.note) { row.appendChild(T.h('div', { class: 'hint' }, b.details.note)); }
      var r = reasonFor(code); if (r) { row.appendChild(T.h('div', { class: 'conv' }, T.h('b', { title: REASON_KEYS[code] }, T.convTitle(REASON_KEYS[code])), r)); }
      box.appendChild(row);
    });
    void pv;
    return box;
  }
  function errorAsBlocker(e) {
    if (!e) { return { code: 'ERROR', message: 'unknown error', details: {}, http: 0 }; }
    if (e.name === 'LostResponse') { return { code: 'LOST_RESPONSE', message: e.message, details: { request_id: e.request_id }, http: 0 }; }
    var msg = e.message || String(e); if (e.body && e.body.incident_id) { msg += ' (incident ' + e.body.incident_id + ')'; }
    return { code: e.code || ('HTTP_' + (e.status || 0)), message: msg, details: detailsOf(e), http: e.status || 0 };
  }
  function warnings(list) {
    var box = T.h('div', { class: 'pv-warnings' });
    (list || []).forEach(function (w) {
      var txt = typeof w === 'string' ? w : (w.message || JSON.stringify(w));
      var code = typeof w === 'object' && w.code ? w.code : (/lock-?up/i.test(txt) ? 'LOCKUP' : null);
      var row = T.h('div', { class: 'pv-warn warn-box' }, code ? badge(code, 'amber') : null, ' ', txt);
      if (code === 'LOCKUP') { row.appendChild(T.h('div', { class: 'hint' }, plain('LOCKUP'))); }
      box.appendChild(row);
    });
    return box;
  }
  function notices(list, cls, kind) {
    var box = T.h('div', { class: 'pv-notices' });
    (list || []).forEach(function (n) {
      box.appendChild(T.h('div', { class: 'pv-notice ' + cls + '-box' }, badge(n.rule || n.code || kind, cls === 'blue' ? 'blue' : 'amber', n.status ? 'status: ' + n.status : ''),
        n.status ? T.h('span', { class: 'muted' }, ' ' + n.status + ' ') : ' ', n.message || n.text || ''));
    });
    return box;
  }
  function sessionLine(ex) {
    var s = ex.session || {};
    var chip = T.h('span', { class: 'chip ' + (s.phase || (s.open ? 'OPEN' : 'CLOSED')) }, (s.group || '') + ' . ' + (s.phase || (s.open ? 'OPEN' : 'CLOSED')) + (s.open && s.closes_at ? ' . closes ' + T.fmt.hm(s.closes_at) : ''));
    return chip;
  }
  function whenText(epoch) { if (!epoch) { return ''; } return T.fmt.dow(epoch) + ' ' + T.fmt.date(epoch) + ' ' + T.fmt.hm(epoch) + ' ET'; }

  /* ---------------------------------------------------------------- the preview ---------------------------------- */
  function render(el, pv, opts) {
    opts = opts || {};
    T.clear(el);
    if (!pv) { el.appendChild(T.h('div', { class: 'empty' }, 'no preview yet')); return el; }
    var inst = pv.instrument || {};
    var req = opts.request || null;
    var head = T.h('div', { class: 'pv-head' });
    head.appendChild(T.h('span', { class: 'side-' + T.esc(pv.side || (req && req.side) || '') + ' pv-side' }, sideLabel(pv.side || (req && req.side))));
    head.appendChild(T.h('span', { class: 'mono' }, ' ' + T.fmt.qty(pv.qty != null ? pv.qty : (req && req.qty)) + ' '));
    head.appendChild(T.h('b', { class: 'mono' }, inst.symbol || inst.id || (req && req.instrument_id) || '?'));
    head.appendChild(T.h('span', { class: 'muted' }, ' ' + typeLabel(pv, req) + ' ' + (pv.tif || (req && req.tif) || '')));
    if (inst.support && inst.support !== 'FULL') { head.appendChild(T.stateTag(inst.support)); }
    el.appendChild(head);
    if (inst.name) { el.appendChild(T.h('div', { class: 'hint' }, (inst.name || '') + ' . ' + (inst.kind || '') + ' . ' + (inst.currency || '') + (inst.multiplier && inst.multiplier !== '1' ? ' . x' + inst.multiplier : '') + (inst.lot && inst.lot !== 1 ? ' . lot ' + T.fmt.qty(inst.lot) : '') + (inst.expiry ? ' . expires ' + inst.expiry : ''))); }
    if (pv.blockers && pv.blockers.length) { el.appendChild(sec('Blockers', blockers(pv.blockers, pv), 'pv-blocked')); }
    if (pv.warnings && pv.warnings.length) { el.appendChild(sec('Warnings', warnings(pv.warnings))); }
    /* leg: expected price with the freshness of the quote it comes from */
    var ex = pv.execution; var q = quoteRec(pv, opts.quote);
    var leg = T.h('div', { class: 'pv-leg' });
    if (q) {
      leg.appendChild(T.h('div', { class: 'row' }, T.h('span', { class: 'muted' }, 'quote '), T.px(q, { size: 'inline' }),
        q.bid != null || q.ask != null ? T.h('span', { class: 'muted' }, ' bid / ask ', T.px(q, { field: 'bid', size: 'inline' }), ' / ', T.px(q, { field: 'ask', size: 'inline' })) : null,
        q.executable === false ? badge('NOT EXECUTABLE' + (q.reason ? ' ' + q.reason : ''), 'red') : null));
    } else { leg.appendChild(T.h('div', { class: 'row' }, T.h('span', { class: 'muted' }, 'quote '), T.px(null))); }
    var qn = (pv.quote && pv.quote.note) || (q && q.note) || '';
    if (qn) { leg.appendChild(T.h('div', { class: 'hint' }, qn)); }
    if (ex) {
      var basis = ex.price_basis || '';
      var own = basis === 'limit' || basis === 'stop' || basis === 'last_mark' || basis === 'unavailable';
      var priceEl;
      if (ex.expected_price == null) { priceEl = T.h('span', { class: 'q-unavailable' }, 'unavailable'); }
      else if (own && q) { priceEl = T.h('span', null, T.h('span', { class: 'mono' }, T.fmt.px(ex.expected_price)), T.h('span', { class: 'muted' }, basis === 'limit' ? ' your limit' : (basis === 'stop' ? ' your stop (triggers on the observed last)' : ' ' + basis))); }
      else { priceEl = T.px(Object.assign({}, q || {}, { value: ex.expected_price }), { field: 'value', size: 'inline', basis: basis }); }
      leg.appendChild(T.h('div', { class: 'row' }, T.h('span', { class: 'muted' }, 'expected '), priceEl, T.h('span', { class: 'muted' }, ' basis ' + basis)));
      var fillTxt = ex.fill_now ? ('fills now: ' + T.fmt.qty(ex.fill_qty_now) + ' of ' + T.fmt.qty(pv.qty)) : ('no fill now (' + (ex.decision || 'rests') + ')');
      var exKv = [['fill estimate', fillTxt], ['cap', ex.cap_note || 'n/a'], ['session', sessionLine(ex)]];
      if (ex.rests_until) { exKv.push(['rests until', whenText(ex.rests_until)]); }
      if (ex.expires_at) { exKv.push(['expires', whenText(ex.expires_at)]); }
      if (ex.participation != null) { exKv.push(['participation', T.fmt.pct(ex.participation, 0)]); }
      leg.appendChild(T.kv(exKv));
      if (ex.assumption) { leg.appendChild(T.h('div', { class: 'hint' }, ex.assumption)); }
    } else if (!(pv.blockers && pv.blockers.length)) { leg.appendChild(T.h('div', { class: 'hint' }, 'execution block unavailable')); }
    el.appendChild(sec('Leg', leg));
    if (opts.hedgeLegs) { el.appendChild(sec('Hedge legs', opts.hedgeLegs)); }
    if (pv.trail) {
      var tr = pv.trail;
      var tkv = [['trail', tr.trail_amt != null ? T.fmt.px(tr.trail_amt) + ' (price units)' : T.fmt.num(tr.trail_pct, 2) + ' %'],
                 ['reference last', tr.trail_ref_px != null ? T.fmt.px(tr.trail_ref_px) : 'unavailable'], ['initial stop', tr.stop_px != null ? T.fmt.px(tr.stop_px) : 'unavailable'],
                 ['ratchet', /SELL/.test(pv.side || '') ? 'sell: stop = highest observed last - trail; moves up only' : 'buy: stop = lowest observed last + trail; moves down only']];
      var trailBox = T.h('div', null, T.kv(tkv), T.h('div', { class: 'conv' }, T.h('b', { title: 'TRAILING' }, T.convTitle('TRAILING')), T.conv('TRAILING') || 'A trailing stop ratchets with each observed print in your favour and never moves back; it triggers on the observed last and fills at that print.'));
      el.appendChild(sec('Trailing stop', trailBox));
    }
    var ec = pv.economics;
    if (ec) {
      var ekv = [['gross', money(ec.gross)], ['fee', money(ec.fee) + (ec.fee_basis ? '  (' + ec.fee_basis + ')' : '')]];
      if (Array.isArray(ec.fee_lines) && ec.fee_lines.length > 1) {
        ec.fee_lines.forEach(function (fl) { ekv.push(['  ' + (fl.label || 'fee'), money(fl.amount != null ? fl.amount : (typeof fl.amount_c === 'number' ? (fl.amount_c / 100).toFixed(2) : null)) + (fl.basis && fl.basis !== fl.label ? '  (' + fl.basis + ')' : '')]); });
      }
      ekv.push(['net cash', signedMoney(ec.net_cash)], ['currency', ec.currency || ''], ['trade date', ec.trade_date || 'n/a'], ['settle date', ec.settle_date || 'n/a']);
      if (ec.accrued != null) { ekv.push(['accrued', money(ec.accrued)]); }
      el.appendChild(sec('Economics', T.kv(ekv)));
    }
    var f = pv.funding;
    if (f) {
      var fkv = [['settled', money(f.settled)], ['pending', signedMoney(f.pending)], ['reserved', money(f.reserved)], ['available', money(f.available)],
                 ['need', money(f.need) + (f.need_formula ? '  (' + f.need_formula + ')' : '')], ['shortfall', money(f.shortfall)],
                 ['sweep-in from Treasury', T.h('span', { class: Number(f.sweep_in) > 0 ? 'pv-sweep' : '' }, money(f.sweep_in) + (f.sweep_source && f.sweep_source !== 'NONE' ? ' (' + f.sweep_source + ')' : ''))],
                 ['Treasury available', money(f.treasury_available)], ['PB financing', money(f.pb_financing)]];
      if (f.fx_leg) { fkv.push(['FX leg first', 'buy ' + f.fx_leg.buy_ccy + ' ' + money(f.fx_leg.amount) + ' / sell ' + f.fx_leg.sell_ccy + (f.fx_leg.rate ? ' @ ' + f.fx_leg.rate : '') + (f.fx_leg.settle_date ? ' settles ' + f.fx_leg.settle_date : '')]); }
      if (f.fx_convert) { fkv.push(['FX conversion', 'USD converted at the fill (asked for)']); }
      var hl = f.hedge_legs;
      if (hl && typeof hl === 'object') {
        fkv.push(['hedge legs', hl.need == null ? (hl.note || 'not priced yet') : 'need ' + money(hl.need) + ' = premium ' + money(hl.premium) + ' + fees ' + money(hl.fees) +
          (hl.initial_margin != null ? ' + initial margin ' + money(hl.initial_margin) : '') + (hl.unpriced && hl.unpriced.length ? ' (unpriced: ' + hl.unpriced.join(', ') + ')' : '')]);
      }
      fkv.push(['fundable', f.ok ? 'yes' : (f.queued ? 'not now - the order QUEUES (waiting for funds) and executes automatically when cash frees up' : 'NO')]);
      if (f.confirm_required) {
        var rules = Array.isArray(f.confirm_rules) ? f.confirm_rules.filter(function (x) { return x && x.text; }).map(function (x) { return x.text; }) : [];
        fkv.push(['confirm', 'REQUIRED: ' + (rules.length ? rules.join('; ') : (f.confirm_reason || '')) + (f.nav ? ' (NAV ' + money(f.nav) + ')' : '')]);
      }
      var fbox = T.h('div', null, T.kv(fkv));
      if (f.fx_leg && f.fx_leg.note) { fbox.appendChild(T.h('div', { class: 'hint' }, f.fx_leg.note)); }
      var fcb = conversionBox(f.fx_conversion); if (fcb) { fbox.appendChild(fcb); }
      /* available is the own spendable cash floored at zero (accounting.AVAILABLE_TEXT): max(0, the server's formula) */
      if (f.formula) { fbox.appendChild(T.h('div', { class: 'hint' }, 'available = ' + (/^max\(0,/.test(f.formula) ? f.formula : 'max(0, ' + f.formula + ')'))); }
      el.appendChild(sec('Funding plan', fbox, f.ok === false && !f.queued ? 'pv-blocked' : ''));
    }
    var b = pv.borrow;
    if (b && (b.needed || b.qty != null)) {
      el.appendChild(sec('Borrow (short sale)', T.h('div', null, T.kv([['availability', T.fmt.qty(b.qty_available) + ' of ' + T.fmt.qty(b.qty) + (b.available === false ? ' (NOT available)' : '')], ['tier', b.tier || 'n/a'], ['fee', b.fee_rate != null ? T.fmt.rate(b.fee_rate) : 'n/a'],
        ['rebate', b.rebate_rate != null ? T.fmt.rate(b.rebate_rate) : 'n/a'], ['collateral (102 %)', money(b.collateral) + ' . posted at each fill: the sale proceeds first, the 2 % excess + fee from cash'], ['lender', b.lender || 'n/a'], ['valid until', b.valid_until ? T.fmt.time(b.valid_until) : 'n/a']]),
        b.basis ? T.h('div', { class: 'hint' }, b.basis) : null)));
    }
    var mg = pv.margin;
    if (mg) {
      var mkv = [];
      if (mg.requirement !== undefined) { mkv.push(['requirement', mg.requirement == null ? 'recomputed after the fill' : money(mg.requirement)]); }
      if (mg.initial != null) { mkv.push(['initial', money(mg.initial)]); }
      if (mg.maintenance != null) { mkv.push(['maintenance', money(mg.maintenance)]); }
      if (mg.per_contract != null) { mkv.push(['per contract', money(mg.per_contract)]); }
      if (mg.package_once) { mkv.push(['package', 'margin computed once for the whole package (AON)']); }
      var mbox = T.h('div', null, T.kv(mkv));
      if (mg.formula) { mbox.appendChild(T.h('div', { class: 'hint' }, mg.formula)); }
      if (mg.detail) { mbox.appendChild(T.h('div', { class: 'hint' }, mg.detail)); }
      if (mg.note) { mbox.appendChild(T.h('div', { class: 'hint' }, mg.note)); }
      el.appendChild(sec('Margin', mbox));
    }
    if (pv.risk && pv.risk.length) { el.appendChild(sec('Risk warnings', notices(pv.risk, 'amber', 'RISK'))); }
    if (pv.compliance && pv.compliance.length) { el.appendChild(sec('Compliance notices', notices(pv.compliance, 'blue', 'RULE'))); }
    var conv = (pv.conventions || []).slice();
    (opts.extraConventions || []).forEach(function (k) { if (conv.indexOf(k) < 0) { conv.push(k); } });
    var note = T.convNote(conv); if (note && note.nodeType === 1) { el.appendChild(sec('Conventions', note)); }
    var foot = T.h('div', { class: 'hint pv-foot' }, 'preview ' + (pv.preview_id || '(no id)') + (pv.as_of ? ' . as of ' + T.fmt.time(pv.as_of) : '') + (pv.expires_at ? ' . valid until ' + T.fmt.time(pv.expires_at) : ''));
    el.appendChild(foot);
    return el;
  }

  /* ---------------------------------------------------------------- PREVIEW_STALE diff ---------------------------- */
  function diff(d, opts) {
    opts = opts || {}; d = d || {};
    var box = T.h('div', { class: 'pv-diff warn-box' });
    var txt;
    if (d.expected_price_old != null && d.expected_price_new != null) {
      var pct = d.moved_pct != null ? T.fmt.num(d.moved_pct, 2) : null;
      txt = T.fmt.px(d.expected_price_old) + ' ' + String.fromCharCode(8594) + ' ' + T.fmt.px(d.expected_price_new) + (pct != null ? ' (' + (Number(d.moved_pct) > 0 ? '+' : '') + pct + ' %)' : '');
    } else if (d.reason === 'unknown_or_expired') { txt = 'the preview ' + (d.preview_id || '') + ' is unknown or expired (120 s)'; }
    else { txt = JSON.stringify(d); }
    box.appendChild(T.h('div', { class: 'row' }, badge('PREVIEW_STALE', 'amber'), T.h('b', { class: 'mono' }, txt)));
    box.appendChild(T.h('div', { class: 'hint' }, 'the fresh preview is shown below; Accept & submit sends the same request with the new preview id'));
    box.appendChild(T.h('div', { class: 'row' }, T.h('button', { class: 'btn primary', id: 'pv-accept', onclick: function () { if (opts.onAccept) { opts.onAccept(); } } }, 'Accept & submit'),
      T.h('button', { class: 'btn', id: 'pv-cancel', onclick: function () { if (opts.onCancel) { opts.onCancel(); } } }, 'Cancel')));
    return box;
  }

  /* ---------------------------------------------------------------- results --------------------------------------- */
  /* USX-21 / APIUI-13: a fill price is the EXECUTED price - shown plain; the quote it used (when the fill carries its record)
     is in the tooltip, never a struck-through 'NO STATUS' chip */
  function fillPrice(f) {
    var q = f.quote && typeof f.quote === 'object' ? f.quote : null;
    var lines = ['executed price of ' + (f.fill_id || 'the fill') + (f.price_basis ? ' (basis: ' + f.price_basis + ')' : '')];
    if (q) {
      var b = f.price_basis && q[f.price_basis] != null ? f.price_basis : null;
      lines.push('quote used: ' + (b ? b + ' ' + T.fmt.px(q[b]) : (q.last != null ? 'last ' + T.fmt.px(q.last) : '')) + (q.source ? ' . ' + q.source : '') + (q.quote_time ? ' . ' + T.fmt.time(q.quote_time) + ' ET' : ''));
    }
    return T.h('span', { class: 'mono pv-fillpx', title: lines.join('\n') }, T.fmt.px(f.price));
  }
  function fillsTable(fills) {
    var t = T.h('table', { class: 'pv-fills' });
    t.appendChild(T.h('thead', null, T.h('tr', null, T.h('th', { class: 'l' }, 'fill'), T.h('th', null, 'qty'), T.h('th', null, 'price'), T.h('th', { class: 'l' }, 'basis'), T.h('th', null, 'fee'))));
    var tb = T.h('tbody');
    (fills || []).forEach(function (f) {
      tb.appendChild(T.h('tr', null, T.h('td', { class: 'l mono' }, f.fill_id || ''), T.h('td', { class: 'num' }, T.fmt.qty(f.qty)), T.h('td', { class: 'num' }, fillPrice(f)),
        T.h('td', { class: 'l' }, f.price_basis || ''), T.h('td', { class: 'num' }, money(f.fee))));
      if (f.cap_note) { tb.appendChild(T.h('tr', null, T.h('td', { class: 'l hint', colspan: '5' }, f.cap_note + (f.settle_date ? ' . settles ' + f.settle_date : '')))); }
    });
    t.appendChild(tb);
    return t;
  }
  function sweepsList(sweeps) {
    var box = T.h('div');
    (sweeps || []).forEach(function (s) {
      box.appendChild(T.h('div', { class: 'pv-sweep-row' }, badge(s.kind || 'SWEEP_IN', 'amber'), ' ' + money(s.amount) + ' ' + (s.currency || '') + ' from ' + (s.from_account || 'Treasury') + (s.linked_id ? ' (linked to ' + s.linked_id + ')' : '') + (s.transfer_id ? ' . ' + s.transfer_id : '')));
    });
    return box;
  }
  function orderLine(o) {
    return T.h('div', { class: 'row' }, T.h('b', { class: 'mono' }, o.order_id || ''), T.h('span', { class: 'st-' + T.esc(o.status || '') }, o.status || ''),
      T.h('span', { class: 'muted' }, sideLabel(o.side) + ' ' + T.fmt.qty(o.qty) + ' ' + (o.symbol || o.instrument_id || '') + ' ' + (o.order_type || '') + (o.limit_px ? ' ' + T.fmt.px(o.limit_px) : '') + (o.stop_px ? ' stop ' + T.fmt.px(o.stop_px) : '') + (o.trail_amt ? ' trail ' + T.fmt.px(o.trail_amt) : '') + (o.trail_pct ? ' trail ' + T.fmt.ratePct(o.trail_pct) : '')));
  }
  function result(res, ctx) {
    ctx = ctx || {};
    var box = T.h('div', { class: 'pv-result' });
    if (!res) { return box; }
    if (res.lost) {
      box.appendChild(T.h('div', { class: 'error-box' }, badge('LOST RESPONSE', 'red'), ' ' + (res.message || ''), T.h('div', { class: 'hint' }, 'request ' + (res.request_id || '') + ': the orders list was re-read before the button was enabled again (no duplicate order); check Orders before resubmitting')));
      return box;
    }
    var o = res.order || {};
    var tags = T.h('div', { class: 'row' });
    if (res.replayed) { tags.appendChild(badge('ALREADY SUBMITTED (replayed)', 'blue', 'the server had this request id already: the stored result is shown')); }
    if (res.recovered) { tags.appendChild(badge('RECOVERED', 'blue', 'the submit reached the server; the order was found under this request id after a lost response')); }
    if (tags.childNodes.length) { box.appendChild(tags); }
    box.appendChild(orderLine(o));
    var filled = Number(o.filled_qty || 0); var qty = Number(o.qty || 0);
    var kv = [['filled', T.fmt.qty(filled) + ' of ' + T.fmt.qty(qty)], ['resting', T.fmt.qty(Math.max(0, qty - filled))]];
    if (o.avg_px != null) { kv.push(['avg price', T.fmt.px(o.avg_px)]); }
    if (o.fees != null) { kv.push(['fees', money(o.fees)]); }
    var dec = res.decision || {};
    var ex = (res.preview && res.preview.execution) || (ctx.preview && ctx.preview.execution) || {};
    if ((o.status === 'WORKING' || o.status === 'PARTIAL') && (dec.reason === 'AWAITING_SESSION' || ex.rests_until)) { kv.push(['resting until', ex.rests_until ? whenText(ex.rests_until) : 'the session opens']); }
    else if (o.status === 'WORKING' && dec.reason) { kv.push(['working', dec.reason + (dec.note ? ' . ' + dec.note : '')]); }
    if (o.status === 'SUSPENDED' && o.reason) { var fw = /^waiting for funds/.test(o.reason); kv.push([fw ? 'queued' : 'suspended', o.reason + (fw ? ' (executes automatically when cash frees up; cancel to drop it)' : '')]); }
    if (o.expires_ts) { kv.push(['expires', whenText(o.expires_ts)]); }
    if (o.last_event && o.last_event.note) { kv.push(['event', o.last_event.note]); }
    if (res.hedge_package) { kv.push(['hedge package', res.hedge_package + ' (legs WAITING_PARENT; released after the primary fills, resized on partial fills)']); }
    if (res.borrow) { kv.push(['borrow', (res.borrow.borrow_id || '') + (res.borrow.locate && res.borrow.locate.collateral ? ' . collateral ' + money(res.borrow.locate.collateral) : '')]); }
    box.appendChild(T.kv(kv));
    if (res.fills && res.fills.length) { box.appendChild(fillsTable(res.fills)); }
    if (res.sweeps && res.sweeps.length) { box.appendChild(sweepsList(res.sweeps)); }
    if (res.warnings && res.warnings.length) { box.appendChild(warnings(res.warnings)); }
    return box;
  }
  function packageResult(res) {
    var box = T.h('div', { class: 'pv-result' });
    if (!res) { return box; }
    if (res.lost) { return result(res); }
    var p = res.package || {};
    var tags = T.h('div', { class: 'row' });
    if (res.replayed) { tags.appendChild(badge('ALREADY SUBMITTED (replayed)', 'blue')); }
    tags.appendChild(badge(p.fill_mode || 'PACKAGE', p.fill_mode === 'AON' ? 'purple' : 'blue'));
    tags.appendChild(T.h('b', { class: 'mono' }, p.package_id || res.hedge_package || ''));
    tags.appendChild(T.h('span', { class: 'st-' + T.esc(p.status || '') }, p.status || ''));
    if (p.strategy_key) { tags.appendChild(T.h('span', { class: 'muted' }, p.strategy_key)); }
    box.appendChild(tags);
    if (res.order) { box.appendChild(orderLine(res.order)); }
    (res.orders || []).forEach(function (o) { box.appendChild(orderLine(o)); });
    var dec = res.decision || {};
    if (dec.reason) { box.appendChild(T.h('div', { class: 'hint' }, 'decision ' + (dec.action || '') + ' ' + dec.reason + (dec.note ? ' . ' + dec.note : ''))); }
    if (res.fills && res.fills.length) { box.appendChild(fillsTable(res.fills)); }
    if (res.margin) { box.appendChild(T.kv([['option margin (package, once)', money(res.margin.requirement)], res.margin.detail ? ['detail', res.margin.detail] : null])); }
    if (res.sweeps && res.sweeps.length) { box.appendChild(sweepsList(res.sweeps)); }
    if (res.warnings && res.warnings.length) { box.appendChild(warnings(res.warnings)); }
    if (p.fill_mode === 'AON') { box.appendChild(T.h('div', { class: 'conv' }, T.h('b', { title: 'AON' }, T.convTitle('AON')), T.conv('AON'))); }
    return box;
  }

  /* the FX conversion plan of a preview (its funding block, else a blocker's details): the ticket offers its checkbox from it */
  function fxConversionOf(pv) {
    if (!pv) { return null; }
    if (pv.funding && pv.funding.fx_conversion) { return pv.funding.fx_conversion; }
    var hit = null;
    (pv.blockers || []).forEach(function (b) { if (!hit && b && b.details && b.details.fx_conversion) { hit = b.details.fx_conversion; } });
    return hit;
  }

  T.components.preview = { render: render, blockers: blockers, diff: diff, result: result, packageResult: packageResult, amounts: amounts,
                           errorAsBlocker: errorAsBlocker, reason: reasonFor, plain: plain, quoteRec: quoteRec, detailsOf: detailsOf, whenText: whenText, fillsTable: fillsTable,
                           conversionBox: conversionBox, fxConversionOf: fxConversionOf, fillPrice: fillPrice };
})();
