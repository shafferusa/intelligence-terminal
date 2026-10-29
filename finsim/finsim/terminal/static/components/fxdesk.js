/* The FX desk (BUILD2_PLAN P06; T.ticketKinds.FX, C11): one docked deal ticket for FX spot and outright forwards between any two
 * quoted currencies (a USD pair or a cross such as EURJPY, dealt directly).
 *
 * Fields: Spot | Forward; BUY <currency> / SELL <currency> (every currency the FX master quotes, GET /api/fx/pairs: USD, EUR,
 * GBP, JPY ... HKD, SGD, MXN, KRW ...; '<>' swaps them); the amount in either currency (the unit buttons say which, '1m' / '500k'
 * accepted); for a forward the tenor (ON spot-next, 1W, 1M, 2M, 3M, 6M, 9M, 1Y) or a broken value date.  Opened from a pair row
 * (b buys the pair's first currency, s sells it), from a forward row (tenor / date prefilled), from the account's empty dock, or
 * from any page with T.ticket.open({account_id, kind: 'FX', buy_ccy, sell_ccy, amount, mode, tenor, value_date}).
 *
 * The preview (POST /api/fx/preview, debounced 300 ms, refreshed every 10 s while open) shows: the deal ('BUY EUR 1,000,000.00 /
 * SELL USD 1,085,020.00'), the mid with its freshness chip (a cross: its own print or the mid derived from the USD pairs, said),
 * the deal rate = mid -/+ the disclosed spread and its USD cost, for a forward the spot, the forward points, the outright and the
 * two rates used (series, value, as-of; monthly proxies LIMITED) with the arithmetic, the trade and value dates, both legs'
 * settled / pending before and after and the value date's availability, and how the sold leg is funded (own cash, Treasury
 * sweep, or SHORT with the fix - a spot sale that cannot be delivered is refused SETTLEMENT_UNFUNDABLE before Confirm).
 * Confirm (Ctrl+Enter) = POST /api/fx/deal with one request id per click (503 retries the same id; X-Replayed is 'already
 * submitted'); it is enabled only by a fresh preview without blockers matching the fields.  The last deals of the account are
 * listed underneath (forwards with their computed mark-to-market).  Every figure is the server's.  ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  var CSS = [
    '.fxd .form{grid-template-columns:70px 1fr}.fxd .modes,.fxd .units{display:flex;gap:4px}.fxd .modes .btn.on,.fxd .units .btn.on{background:var(--accent);color:#111;border-color:var(--accent)}',
    '.fxd .ccys{display:flex;gap:4px;align-items:center;flex-wrap:wrap}.fxd .ccys select{flex:1;min-width:0;font-family:var(--mono)}' +
    /* V104R1-09: BUY / SELL each keep room for 'CCY name' and wrap onto their own line in the dock instead of clipping the name */
    '.fxd .ccys .pair{display:flex;gap:4px;align-items:center;flex:1 1 15em;min-width:0}.fxd .ccys .pair select{flex:1;min-width:0}.fxd .amt{display:flex;gap:4px}.fxd .amt input{flex:1;min-width:0;font-family:var(--mono)}',
    '.fxd .meta{font-family:var(--mono);font-size:11px;color:var(--muted);margin:2px 0 6px}.fxd .pv{border-top:1px solid var(--line);margin-top:8px;padding-top:6px}',
    '.fxd .pv .kv{gap:1px 10px}.fxd .blk{border-left:3px solid var(--down);padding:3px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}',
    '.fxd .wrn{border-left:3px solid var(--warn);padding:3px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}.fxd .okb{border-left:3px solid var(--up);padding:3px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}',
    '.fxd .deal{font-family:var(--mono);font-size:12.5px;margin:6px 0}.fxd .acts{display:flex;gap:6px;align-items:center;margin:8px 0}',
    '.fxd .st{font-family:var(--mono);font-size:11px;color:var(--fg3);min-height:15px}.fxd .st.err{color:var(--down)}.fxd .st.ok{color:var(--up)}',
    '.fxd table.legs{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:10.5px;margin:4px 0}.fxd table.legs th,.fxd table.legs td{padding:1px 3px;text-align:right;border-bottom:1px solid var(--line)}',
    '.fxd table.legs th:first-child,.fxd table.legs td:first-child{text-align:left}.fxd .ar{font-family:var(--mono);font-size:10.5px;color:var(--fg3);word-break:break-word}',
    '.fxd .recent{margin-top:10px}.fxd .recent h3{font-size:11px;margin:4px 0;color:var(--muted)}'
  ].join('');
  var cssDone = false;
  function ensureCss() { if (cssDone || document.getElementById('fxdesk-css')) { cssDone = true; return; } cssDone = true; var s = T.h('style', { id: 'fxdesk-css' }); s.textContent = CSS; document.head.appendChild(s); }
  var TENORS = [['ON', 'ON (spot-next)'], ['1W', '1 week'], ['1M', '1 month'], ['2M', '2 months'], ['3M', '3 months'], ['6M', '6 months'], ['9M', '9 months'], ['1Y', '1 year'], ['BROKEN', 'broken date']];
  var DEFAULT_CCYS = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD'];
  var DEBOUNCE_MS = 300, REPREVIEW_MS = 10000, PREVIEW_TTL_MS = 60000, CCY_TTL_MS = 60000;
  /* the keys in force (keys.js registry, Settings > Keyboard; KB-R1-01): the desk previews with 'Preview the order' (Enter)
     and confirms with 'Submit the order' (Ctrl+Enter) */
  function kl(id) { return T.keys && T.keys.label ? T.keys.label(id) : ''; }
  function kp(id) { return T.keys && T.keys.paren ? T.keys.paren(id) : ''; }
  var st = null;
  var ccyCache = { at: 0, list: null, info: {} };
  function money(x) { return x == null || x === '' ? 'n/a' : T.fmt.money(x, 2); }

  /* every currency the FX master quotes (USD first), cached a minute */
  function currencies(acct) {
    if (ccyCache.list && Date.now() - ccyCache.at < CCY_TTL_MS) { return Promise.resolve(ccyCache.list); }
    return T.api.get('/api/fx/pairs', { account_id: acct }).then(function (d) {
      var info = {};
      (d.currencies || []).forEach(function (c) { info[c.ccy] = c; });
      ccyCache = { at: Date.now(), list: (d.currencies || []).map(function (c) { return c.ccy; }), info: info };
      return ccyCache.list;
    }, function (e) { console.warn('fx currencies', e); return ccyCache.list || DEFAULT_CCYS; });
  }
  function parseAmount(s) {
    var t = String(s == null ? '' : s).trim().toLowerCase().replace(/[, _]/g, '');
    if (!t) { return ''; }
    var mult = 1;
    if (/m$/.test(t)) { mult = 1000000; t = t.slice(0, -1); } else if (/k$/.test(t)) { mult = 1000; t = t.slice(0, -1); } else if (/bn$/.test(t)) { mult = 1000000000; t = t.slice(0, -2); }
    var n = Number(t);
    if (!isFinite(n) || n <= 0) { return null; }
    return (Math.round(n * mult * 100) / 100).toFixed(2);
  }

  function open(body, fields) {
    ensureCss();
    fields = fields || {};
    var acct = fields.account_id || T.state.active_account;
    if (!st || st.body !== body || st.account_id !== acct || !body.firstChild || !body.contains(st.ui.root)) { build(body, acct); }
    apply(fields);
    return st;
  }

  function build(body, acct) {
    T.clear(body);
    if (st && st.timer) { clearInterval(st.timer); }
    st = { body: body, account_id: acct, mode: 'SPOT', buy: 'EUR', sell: 'USD', unit: 'EUR', seq: 0, preview: null, previewAt: 0, previewFields: null, deb: null, busy: false, ui: {}, timer: null, holdUntil: 0 };
    var root = T.h('div', { class: 'fxd', tabindex: '-1' });
    var meta = T.h('div', { class: 'meta', title: acct }, 'account ' + (T.accountName ? T.accountName(acct) : acct));
    var spotB = T.h('button', { class: 'btn on', title: 'spot: value T+2 (USD/CAD T+1)' }, 'Spot');
    var fwdB = T.h('button', { class: 'btn', title: 'outright forward: covered interest parity, value at the tenor or a broken date' }, 'Forward');
    var modes = T.h('div', { class: 'modes' }, spotB, fwdB);
    var buySel = T.h('select', { 'aria-label': 'currency you buy' });
    var sellSel = T.h('select', { 'aria-label': 'currency you sell' });
    var swap = T.h('button', { class: 'btn small', title: 'swap the two currencies' }, '<>');
    var ccys = T.h('div', { class: 'ccys' }, T.h('span', { class: 'pair' }, T.h('span', null, 'BUY'), buySel, swap), T.h('span', { class: 'pair' }, T.h('span', null, 'SELL'), sellSel));
    var amount = T.h('input', { type: 'text', inputmode: 'decimal', placeholder: 'amount (1m, 500k)', 'aria-label': 'amount' });
    var unitBuy = T.h('button', { class: 'btn small on', title: 'the amount is in the currency you buy' }, 'EUR');
    var unitSell = T.h('button', { class: 'btn small', title: 'the amount is in the currency you sell' }, 'USD');
    var amt = T.h('div', { class: 'amt' }, amount, T.h('div', { class: 'units' }, unitBuy, unitSell));
    var tenor = T.h('select', { 'aria-label': 'tenor' });
    TENORS.forEach(function (t) { tenor.appendChild(T.h('option', { value: t[0] }, t[1])); });
    tenor.value = '3M';
    var vdate = T.h('input', { type: 'date', 'aria-label': 'value date', hidden: true, title: 'a broken value date: a business day after spot, up to the 1-year date' });
    var tenorRow = T.h('div', { class: 'ccys' }, tenor, vdate);
    var tenorField = T.field('value', tenorRow, 'the forward value date');
    var form = T.form([T.field('deal', modes), T.field('currencies', ccys), T.field('amount', amt, 'the amount in the currency of the highlighted button'), tenorField]);
    var unitNote = T.h('div', { class: 'hint' }, '');
    var pv = T.h('div', { class: 'pv' }, T.h('div', { class: 'hint' }, 'the preview appears here: rate, spread, value date, both legs and how the sold currency is funded'));
    var status = T.h('div', { class: 'st', role: 'status' });
    var previewBtn = T.h('button', { class: 'btn' }, 'Preview');
    var confirm = T.h('button', { class: 'btn primary', disabled: true, title: 'book the deal' + kp('ticket.submit') }, 'Confirm');
    var clear = T.h('button', { class: 'btn small', title: 'clear the amount and the preview' }, 'Clear');
    var acts = T.h('div', { class: 'acts' }, previewBtn, confirm, clear);
    var result = T.h('div');
    var recent = T.h('div', { class: 'recent' });
    root.appendChild(T.h('h2', null, 'FX desk')); root.appendChild(meta); root.appendChild(form); root.appendChild(unitNote); root.appendChild(acts); root.appendChild(status);
    root.appendChild(pv); root.appendChild(result); root.appendChild(recent);
    body.appendChild(root);
    st.ui = { root: root, spotB: spotB, fwdB: fwdB, buySel: buySel, sellSel: sellSel, amount: amount, unitBuy: unitBuy, unitSell: unitSell, tenor: tenor, vdate: vdate,
              tenorField: tenorField, pv: pv, status: status, confirm: confirm, result: result, recent: recent, unitNote: unitNote };
    showTenor();
    spotB.addEventListener('click', function () { setMode('SPOT'); schedule(); });
    fwdB.addEventListener('click', function () { setMode('FORWARD'); schedule(); });
    buySel.addEventListener('change', function () { setCcys(buySel.value, st.sell === buySel.value ? st.buy : st.sell); schedule(); });
    sellSel.addEventListener('change', function () { setCcys(st.buy === sellSel.value ? st.sell : st.buy, sellSel.value); schedule(); });
    swap.addEventListener('click', function () { setCcys(st.sell, st.buy); schedule(); });
    unitBuy.addEventListener('click', function () { st.unit = st.buy; paintUnits(); invalidate(); schedule(); });
    unitSell.addEventListener('click', function () { st.unit = st.sell; paintUnits(); invalidate(); schedule(); });
    amount.addEventListener('input', function () { invalidate(); schedule(); });
    amount.addEventListener('keydown', function (e) {
      if (T.keys.is(e, 'ticket.submit')) { e.preventDefault(); e.stopPropagation(); doSubmit(); }
      else if (T.keys.is(e, 'ticket.preview')) { e.preventDefault(); e.stopPropagation(); runPreview(); }
    });
    tenor.addEventListener('change', function () { showTenor(); invalidate(); schedule(); });
    vdate.addEventListener('change', function () { invalidate(); schedule(); });
    previewBtn.addEventListener('click', function () { runPreview(); });
    confirm.addEventListener('click', function () { doSubmit(); });
    clear.addEventListener('click', function () { amount.value = ''; invalidate(); T.clear(pv).appendChild(T.h('div', { class: 'hint' }, 'enter an amount')); T.clear(result); setStatus('', ''); });
    root.addEventListener('keydown', function (e) {
      if (T.keys.is(e, 'ticket.submit')) { e.preventDefault(); e.stopPropagation(); doSubmit(); }
    });
    st.paintKeys = function () { confirm.title = 'book the deal' + kp('ticket.submit'); };
    fillCcys(DEFAULT_CCYS);
    currencies(acct).then(function (list) { if (st && st.ui.root === root) { fillCcys(list); } });
    st.timer = setInterval(function () {
      if (!document.hidden && st && document.body.contains(root) && T.dock.visible() && parseAmount(st.ui.amount.value)) { runPreview(true); }
      if (st && !document.body.contains(root)) { clearInterval(st.timer); }
    }, REPREVIEW_MS);
    loadRecent();
  }

  function fillCcys(list) {
    var ui = st.ui;
    [ui.buySel, ui.sellSel].forEach(function (sel) {
      T.clear(sel);
      (list || DEFAULT_CCYS).forEach(function (c) {
        var inf = ccyCache.info[c] || {};
        sel.appendChild(T.h('option', { value: c, title: (inf.name || c) + (inf.usd_rate ? ': USD ' + inf.usd_rate : ': no USD rate yet') }, c + (inf.name ? ' ' + inf.name : '')));
      });
    });
    [st.buy, st.sell].forEach(function (c) {
      if (!(list || []).some(function (x) { return x === c; })) { [ui.buySel, ui.sellSel].forEach(function (sel) { sel.appendChild(T.h('option', { value: c }, c)); }); }
    });
    ui.buySel.value = st.buy; ui.sellSel.value = st.sell;
  }
  function setMode(m) {
    st.mode = m === 'FORWARD' ? 'FORWARD' : 'SPOT';
    st.ui.spotB.classList.toggle('on', st.mode === 'SPOT'); st.ui.fwdB.classList.toggle('on', st.mode === 'FORWARD');
    showTenor(); invalidate();
  }
  function showTenor() {
    var fwd = st.mode === 'FORWARD';
    st.ui.tenorField.forEach(function (el) { el.hidden = !fwd; });
    st.ui.vdate.hidden = !(fwd && st.ui.tenor.value === 'BROKEN');
  }
  function setCcys(buy, sell) {
    if (!buy || !sell) { return; }
    if (buy === sell) { sell = buy === 'USD' ? 'EUR' : 'USD'; }
    var keepTyped = st.unit === st.buy ? 'buy' : 'sell';
    st.buy = buy; st.sell = sell;
    st.unit = keepTyped === 'buy' ? buy : sell;
    st.ui.buySel.value = buy; st.ui.sellSel.value = sell;
    if (st.ui.buySel.value !== buy || st.ui.sellSel.value !== sell) { fillCcys(ccyCache.list || DEFAULT_CCYS); }
    paintUnits(); invalidate();
  }
  function paintUnits() {
    st.ui.unitBuy.textContent = st.buy; st.ui.unitSell.textContent = st.sell;
    st.ui.unitBuy.classList.toggle('on', st.unit === st.buy); st.ui.unitSell.classList.toggle('on', st.unit === st.sell);
    st.ui.unitNote.textContent = 'amount in ' + st.unit + ' (' + (st.unit === st.buy ? 'what you receive' : 'what you deliver') + '); the other side follows from the deal rate';
  }
  /* fields from a row / another page: a pair + side (b buys the base, s sells it), or buy / sell currencies */
  function apply(f) {
    var pair = f.instrument_id && /^[A-Z]{6}$/.test(String(f.instrument_id)) ? String(f.instrument_id) : null;
    var buy = f.buy_ccy ? String(f.buy_ccy).toUpperCase() : null, sell = f.sell_ccy ? String(f.sell_ccy).toUpperCase() : null;
    if (pair && !buy && !sell) {
      var base = pair.slice(0, 3), quote = pair.slice(3);
      var selling = String(f.side || 'BUY').toUpperCase() === 'SELL';
      buy = selling ? quote : base; sell = selling ? base : quote;
      st.unit = base;
    }
    var mode = f.mode ? String(f.mode).toUpperCase() : ((f.tenor || f.value_date) ? 'FORWARD' : null);
    if (mode) { setMode(mode); }
    if (f.tenor) { st.ui.tenor.value = String(f.tenor).toUpperCase(); }
    if (f.value_date) { st.ui.tenor.value = 'BROKEN'; st.ui.vdate.value = String(f.value_date); }
    showTenor();
    if (buy || sell) {
      var unit = st.unit;
      setCcys(buy || st.buy, sell || st.sell);
      if (unit === st.buy || unit === st.sell) { st.unit = unit; }
    }
    if (f.amount_ccy) { st.unit = String(f.amount_ccy).toUpperCase(); }
    if (f.amount != null || f.qty != null) { st.ui.amount.value = String(f.amount != null ? f.amount : f.qty); }
    paintUnits();
    invalidate();
    schedule();
    if (st.ui.amount && !f.no_focus) { try { st.ui.amount.focus({ preventScroll: true }); } catch (e) { /* focus is a convenience */ } }
  }
  function invalidate() { st.preview = null; st.ui.confirm.disabled = true; }
  function schedule() { clearTimeout(st.deb); st.deb = setTimeout(function () { runPreview(); }, DEBOUNCE_MS); }
  function fieldsNow() {
    var f = { account_id: st.account_id, kind: st.mode, buy_ccy: st.buy, sell_ccy: st.sell, amount: parseAmount(st.ui.amount.value), amount_ccy: st.unit };
    if (st.mode === 'FORWARD') {
      if (st.ui.tenor.value === 'BROKEN') { f.value_date = st.ui.vdate.value || null; } else { f.tenor = st.ui.tenor.value; }
    }
    return f;
  }
  function same(a, b) { return !!(a && b) && JSON.stringify(a) === JSON.stringify(b); }
  function setStatus(text, cls) { st.ui.status.textContent = text; st.ui.status.className = 'st' + (cls ? ' ' + cls : ''); }

  function runPreview(quiet) {
    if (!st) { return; }
    var f = fieldsNow();
    if (!f.amount) {
      if (f.amount === null) { setStatus('the amount must be a positive number (1m, 500k or 1,250,000.00)', 'err'); }
      T.clear(st.ui.pv).appendChild(T.h('div', { class: 'hint' }, 'enter the amount in ' + st.unit));
      return;
    }
    if (f.kind === 'FORWARD' && st.ui.tenor.value === 'BROKEN' && !f.value_date) { T.clear(st.ui.pv).appendChild(T.h('div', { class: 'hint' }, 'pick the broken value date')); return; }
    var seq = ++st.seq;
    if (!quiet) { setStatus('previewing ...', ''); }
    T.api.mutate('POST', '/api/fx/preview', f, { retries: 1 }).then(function (p) {
      if (!st || seq !== st.seq) { return; }
      st.preview = p; st.previewAt = Date.now(); st.previewFields = f;
      paintPreview(p);
      var ok = p && p.ok && !(p.blockers || []).length;
      st.ui.confirm.disabled = !ok || st.busy;
      if (quiet && st.holdUntil && Date.now() < st.holdUntil) { return; }
      setStatus(ok ? 'ready: Confirm' + kp('ticket.submit') + ' books the deal' : 'not dealable: ' + (((p.blockers || [])[0] || {}).message || 'see the preview'), ok ? 'ok' : 'err');
    }, function (e) {
      if (!st || seq !== st.seq) { return; }
      st.preview = null; st.ui.confirm.disabled = true;
      T.clear(st.ui.pv).appendChild(T.h('div', { class: 'blk', title: (e && e.code) || '' }, T.api.describe(e)));   /* V104R2-BW-04: words, the code in the hover */
      setStatus('preview refused: ' + T.api.describe(e), 'err');
    });
  }

  function legsTable(p) {
    var legs = p.legs || {};
    var tbl = T.h('table', { class: 'legs' });
    tbl.appendChild(T.h('tr', null, T.h('th', null, 'leg'), T.h('th', null, 'amount'), T.h('th', { title: 'settled cash now' }, 'settled'), T.h('th', { title: 'pending before -> after' }, 'pending'),
      T.h('th', { title: 'available on the value date, before -> after this deal' }, 'avail @ value')));
    [p.buy_ccy, p.sell_ccy].forEach(function (c) {
      var l = legs[c]; if (!l) { return; }
      var sign = l.effect === 'receivable' ? '+' : '-';
      tbl.appendChild(T.h('tr', null, T.h('td', null, (l.effect === 'receivable' ? 'receive ' : 'deliver ') + c), T.h('td', null, sign + money(l.amount)), T.h('td', null, money(l.settled_before)),
        T.h('td', null, money(l.pending_before) + ' -> ' + money(l.pending_after)), T.h('td', null, money(l.available_at_value_date_before) + ' -> ' + money(l.available_at_value_date_after))));
    });
    return tbl;
  }
  function rateLine(r) {
    if (!r) { return 'n/a'; }
    if (r.value_pct == null) { return r.note || 'no rate'; }
    return r.series + ' ' + T.fmt.ratePct(r.value_pct) + ' as of ' + r.as_of + (r.limited ? ' (monthly proxy, LIMITED)' : '') + (r.source ? ' - ' + r.source : '');
  }
  function paintPreview(p) {
    var pv = T.clear(st.ui.pv);
    (p.blockers || []).forEach(function (b) { pv.appendChild(T.h('div', { class: 'blk', title: b.code || '' }, b.message || T.codeWords(b.code))); });   /* V104R2-BW-04 */
    (p.warnings || []).forEach(function (w) { pv.appendChild(T.h('div', { class: 'wrn' }, w)); });
    if (p.buy == null) {
      if (p.forward && p.forward.note) { pv.appendChild(T.h('div', { class: 'hint' }, p.forward.note)); }
      if (p.quote) { pv.appendChild(T.h('div', null, 'mid ', T.px(p.quote))); }
      return;
    }
    var fwd = p.kind === 'FORWARD';
    pv.appendChild(T.h('div', { class: 'deal' }, 'BUY ' + p.buy_ccy + ' ' + money(p.buy) + ' / SELL ' + p.sell_ccy + ' ' + money(p.sell)));
    pv.appendChild(T.h('div', { class: 'meta' }, (fwd ? 'forward ' + (p.tenor === 'BROKEN' ? 'broken date' : p.tenor) + ', value ' : 'spot, value ') + p.value_date + ' (trade date ' + p.trade_date + (fwd ? ', spot ' + p.spot_date : ', T+' + p.settle_days) + ')'));
    var q = p.quote || {};
    var side = p.buy_ccy === p.base ? '+' : '-';
    var kv = [
      ['pair', p.pair + ' (' + (p.quote_ccy || '') + ' per 1 ' + (p.base || '') + ')' + (p.cross ? ', a cross dealt directly' : ''), p.rate_basis || ''],
      ['spot mid', T.px(Object.assign({}, q, { mark: p.spot_mid || q.mid })), (q.basis || '') + (q.legs && q.legs.length > 1 ? '\n' + q.legs.map(function (l) { return l.pair + ' ' + l.quoted + ' (' + l.quality + ')'; }).join('\n') : '')]
    ];
    if (fwd) {
      var f = p.forward || {};
      kv.push(['points', (f.points || '') + ' pips (' + (f.days || '') + ' days from spot)', 'forward points = (outright - spot) / pip']);
      kv.push(['outright', p.outright_mid + ' (covered interest parity)', f.arithmetic || '']);
      kv.push([p.base + ' rate', rateLine(f.r_base), JSON.stringify(f.r_base || {})]);
      kv.push([p.quote_ccy + ' rate', rateLine(f.r_quote), JSON.stringify(f.r_quote || {})]);
    }
    kv.push(['deal rate', p.rate_quoted + ' = ' + (fwd ? 'outright ' : 'mid ') + p.mid + ' ' + side + ' ' + p.spread_pips + ' pip (against you)', fwd ? (p.spread_text || '') : 'FX spot: the mid +/- 0.2 pip against you']);
    kv.push(['spread cost', 'USD ' + money(p.spread_cost), 'what the spread costs against the mid, in USD']);
    if (p.cross && p.ledger_note) { kv.push(['booked as', p.ledger_note, JSON.stringify(p.ledger || {})]); }
    pv.appendChild(T.kv(kv));
    if (fwd && p.forward && p.forward.arithmetic) { pv.appendChild(T.h('div', { class: 'ar' }, p.forward.arithmetic)); }
    pv.appendChild(legsTable(p));
    var sl = (p.legs || {})[p.sell_ccy] || {};
    var fu = sl.funding || null;
    if (fu) {
      var cls = fu.fundable ? (Number(fu.from_treasury || 0) > 0 ? 'wrn' : 'okb') : 'blk';
      var txt = 'funding of ' + p.sell_ccy + ' on ' + p.value_date + ': ' + (fu.text || '');
      if (sl.note) { txt += ' - ' + sl.note; }
      pv.appendChild(T.h('div', { class: cls }, txt));
    }
    if (fwd) { pv.appendChild(T.h('div', { class: 'hint' }, 'A forward moves no cash before its value date; its mark-to-market (computed) is in Currency positions and is realised at the value date.')); }
  }

  function doSubmit() {
    if (!st || st.busy) { return; }
    var p = st.preview; var f = fieldsNow();
    if (!p || !p.ok || (p.blockers || []).length) { setStatus('preview first: nothing is dealt without a clean preview', 'err'); return; }
    if (!same(f, st.previewFields) || Date.now() - st.previewAt > PREVIEW_TTL_MS) { setStatus('the preview is stale - previewing again', 'err'); runPreview(); return; }
    st.busy = true; st.ui.confirm.disabled = true; setStatus('booking ...', '');
    T.api.post('/api/fx/deal', f).then(function (r) {
      st.busy = false;
      var d = (r && r.deal) || {};
      var head = (r && r.replayed ? 'already submitted: ' : 'booked ') + (d.deal_id || '') + ': BUY ' + d.buy_ccy + ' ' + money(d.buy) + ' / SELL ' + d.sell_ccy + ' ' + money(d.sell) + ' @ ' + (d.rate_quoted || '') + ', value ' + (d.value_date || '');
      setStatus(head, 'ok');
      st.holdUntil = Date.now() + 20000;
      var box = T.h('div', { class: 'okb' }, head + (d.kind === 'FORWARD' ? ' (forward ' + (d.tenor || '') + ': no cash until the value date)' : ''));
      box.appendChild(T.h('div', null, T.h('a', { href: T.router.account(st.account_id, 'markets/positions') }, 'Currency positions'), ' . ',
        T.h('a', { href: T.router.account(st.account_id, 'transactions') }, 'Transactions')));
      if (r && r.warning) { box.appendChild(T.h('div', { class: 'why' }, r.warning)); }
      T.clear(st.ui.result).appendChild(box);
      T.ui.toast('FX ' + (d.kind === 'FORWARD' ? 'forward' : 'spot') + ' ' + (d.deal_id || '') + ' booked', 'ok');
      T.poll.kickAll();
      invalidate(); runPreview(true); loadRecent();
    }, function (e) {
      st.busy = false;
      setStatus('refused: ' + T.api.describe(e), 'err');
      T.clear(st.ui.result).appendChild(T.h('div', { class: 'blk', title: (e && e.code) || '' }, T.api.describe(e)));
      st.holdUntil = Date.now() + 20000;
      runPreview(true);
    });
  }

  function loadRecent() {
    if (!st) { return; }
    var acct = st.account_id; var box = st.ui.recent;
    T.api.get('/api/fx/deals', { account_id: acct, limit: 6 }).then(function (rows) {
      if (!st || st.ui.recent !== box) { return; }
      T.clear(box);
      if (!rows || !rows.length) { box.appendChild(T.h('div', { class: 'hint' }, 'no FX deals in this account yet')); return; }
      box.appendChild(T.h('h3', null, 'Last deals'));
      var tbl = T.h('table', { class: 'legs' });
      tbl.appendChild(T.h('tr', null, T.h('th', null, 'deal'), T.h('th', null, 'buy / sell'), T.h('th', null, 'value'), T.h('th', { title: 'forwards: computed mark-to-market in USD' }, 'MTM')));
      rows.forEach(function (d) {
        var bs = T.h('td', null, '+' + d.buy_ccy + ' ' + money(d.buy)); bs.appendChild(T.h('br')); bs.appendChild(document.createTextNode('-' + d.sell_ccy + ' ' + money(d.sell)));
        tbl.appendChild(T.h('tr', { title: d.kind + ' ' + d.pair + ' @ ' + (d.rate_quoted || '') + ', ' + d.status + (d.link ? ' - ' + (d.link.text || '') : '') },
          T.h('td', null, d.deal_id + (d.kind === 'FORWARD' ? ' F' : '')), bs,
          T.h('td', null, String(d.value_date || '').slice(2) + (d.status === 'SETTLED' ? ' ok' : '')), T.h('td', null, d.mtm == null ? '' : money(d.mtm))));
      });
      box.appendChild(tbl);
    }, function (e) { console.warn('fx deals', e); });
  }

  T.ticketKinds.FX = { open: open };
  T.components.fxdesk = { open: open, mount: function (el, opts) { return open(el, opts || {}); } };
  if (T.keys && typeof T.keys.uses === 'function') { T.keys.uses('components/fxdesk.js'); }       /* its keys follow Settings > Keyboard (KB-R1-01) */
  if (T.state && T.state.on) { T.state.on('keybinds', function () { if (st && st.paintKeys && st.ui && st.ui.root && st.ui.root.isConnected) { st.paintKeys(); } }); }
})();
