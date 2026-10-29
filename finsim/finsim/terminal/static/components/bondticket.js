/* The bond ticket (BUILD2_PLAN P07; T.ticketKinds.BOND, C11) and the bond list component (C18: T.components.bondlist for
 * the Foreign bonds tab of the Foreign Markets & FX page).
 *
 * Ticket (docked): account (fixed to the page's account); bond (CUSIP / name search over GET /api/instruments?kinds=BOND
 * &account=, never a Yahoo look-up); side BUY / SELL (b / s); FACE amount in the bond's currency, a multiple of its lot
 * (1,000; JGBs 100,000) with -/+ steps; the preview (POST /api/bonds/preview, debounced 300 ms, refreshed every 10 s while
 * open) shows the price the trade deals at = the mark (FedInvest or computed, with its freshness chip: source, as-of, inputs)
 * +/- the disclosed ASSUMED half-spread, the yield, accrued per 100, the clean amount, accrued amount, fee (USD 0.02 per 100
 * face, min 5.00), the total paid / received, trade and settlement dates (T+1; foreign T+2 in the currency), the funding
 * (own cash, Treasury sweep, FX conversion) and the DV01 change; refusals in red with the reason ("bond market closed -
 * reopens 08:00 ET ...", LIMITED, OVERSELL, release the repo first ...).  Submit (Ctrl+Enter) = POST /api/bonds/trade with one
 * request id per click (503 retries the same id; X-Replayed is "already submitted"), enabled only with a fresh preview
 * without blockers that matches the fields.  Nothing is computed here: every figure is the server's.
 *
 * bondlist.render(el, {account_id, scope: 'FOREIGN'}) -> {unmount}: GET /api/debt/bonds; the generic government
 * benchmarks with price (freshness), yield, coupon, maturity, currency, DV01, position and b / s; the countries that cannot be
 * listed (gilts / OATs / BTPs while FRED is down) as grey rows with the reason.  ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  var CSS = [
    '.bt .form{grid-template-columns:78px 1fr}.bt .sides{display:flex;gap:4px}.bt .sides .btn.on{background:var(--accent);color:#111;border-color:var(--accent)}',
    '.bt .sides .btn.on.sell{background:var(--down);border-color:var(--down);color:#fff}.bt .face{display:flex;gap:4px}.bt .face input{flex:1;min-width:0}',
    '.bt .meta{font-family:var(--mono);font-size:11px;color:var(--muted);margin:2px 0 6px}.bt .pv{border-top:1px solid var(--line);margin-top:8px;padding-top:6px}',
    '.bt .pv .kv{gap:1px 10px}.bt .blk{border-left:3px solid var(--down);padding:3px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}',
    '.bt .wrn{border-left:3px solid var(--warn);padding:3px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}.bt .tot{font-family:var(--mono);font-size:13px;margin:6px 0}',
    '.bt .acts{display:flex;gap:6px;align-items:center;margin:8px 0}.bt .st{font-family:var(--mono);font-size:11px;color:var(--fg3);min-height:15px}.bt .st.err{color:var(--down)}.bt .st.ok{color:var(--up)}',
    '.bt .dd{position:absolute;z-index:20;background:var(--panel);border:1px solid var(--line);max-height:260px;overflow:auto;min-width:300px;font-size:11.5px}',
    '.bt .dd div{padding:3px 6px;cursor:pointer;white-space:nowrap}.bt .dd div:hover,.bt .dd div.sel{background:var(--panel-2);color:var(--accent)}.bt .ta{position:relative}',
    '.bl .why{color:var(--warn);font-size:10px;font-family:var(--mono)}.bl tr.unavail td{color:var(--fg3)}.bl .nm{display:inline-block;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle}',
    '.bl{container-type:inline-size}@container (max-width:900px){.bl .c-name,.bl .c-asof{display:none}}@container (max-width:680px){.bl .c-dv01{display:none}}'
  ].join('');
  var cssDone = false;
  function ensureCss() { if (cssDone || document.getElementById('bondticket-css')) { cssDone = true; return; } cssDone = true; var s = T.h('style', { id: 'bondticket-css' }); s.textContent = CSS; document.head.appendChild(s); }
  function money(x) { return x == null ? 'n/a' : T.fmt.money(x, 2); }
  /* the keys in force (keys.js registry, Settings > Keyboard): 'Ctrl+Enter' / ' (Ctrl+Enter)' / ' (B / S on a row)' */
  function kl(id) { return T.keys && T.keys.label ? T.keys.label(id) : ''; }
  function kp(id) { return T.keys && T.keys.paren ? T.keys.paren(id) : ''; }
  function rowKeysParen() { var b = kl('grid.buy'), s = kl('grid.sell'); return b || s ? ' (' + [b, s].filter(Boolean).join(' / ') + ' on a row)' : ''; }
  var DEBOUNCE_MS = 300, REPREVIEW_MS = 10000, PREVIEW_TTL_MS = 60000;
  var st = null;                                               /* the one ticket's state */

  function open(body, fields) {
    ensureCss();
    fields = fields || {};
    var sameAcct = st && st.account_id === fields.account_id && st.body === body && body.firstChild;
    if (!sameAcct) { build(body, fields.account_id); }
    if (fields.instrument_id && fields.instrument_id !== st.instrument_id) { setInstrument(fields.instrument_id, null); }
    if (fields.side) { setSide(String(fields.side).toUpperCase() === 'SELL' ? 'SELL' : 'BUY'); }
    if (fields.qty != null || fields.face != null) { st.ui.face.value = String(fields.face != null ? fields.face : fields.qty); }
    schedule();
    return st;
  }

  function build(body, account_id) {
    T.clear(body);
    if (st && st.timer) { clearInterval(st.timer); }
    st = { body: body, account_id: account_id, instrument_id: null, inst: null, side: 'BUY', seq: 0, preview: null, previewAt: 0, deb: null, busy: false, ui: {}, timer: null };
    var root = T.h('div', { class: 'bt', tabindex: '-1' });
    var acct = T.h('div', { class: 'meta', title: account_id }, 'account ' + (T.accountName ? T.accountName(account_id) : account_id));   /* V104R2-BW-02 (f): the name; the id in the hover */
    var inst = T.h('input', { type: 'text', id: 'ticket-instrument', placeholder: 'CUSIP, coupon or maturity (e.g. 912810 or 2035)', autocomplete: 'off', 'aria-label': 'bond' });
    var dd = T.h('div', { class: 'dd', hidden: true });
    var ta = T.h('div', { class: 'ta' }, inst, dd);
    var meta = T.h('div', { class: 'meta' }, 'pick a bond from the list' + rowKeysParen() + ' or search here');
    var buy = T.h('button', { class: 'btn on', title: 'BUY' + kp('ticket.buy') }, 'BUY');
    var sell = T.h('button', { class: 'btn sell', title: 'SELL' + kp('ticket.sell') }, 'SELL');
    var sides = T.h('div', { class: 'sides' }, buy, sell);
    var face = T.h('input', { type: 'text', inputmode: 'numeric', placeholder: 'face amount', 'aria-label': 'face amount' });
    var minus = T.h('button', { class: 'btn small', title: 'minus one lot' }, '-');
    var plus = T.h('button', { class: 'btn small', title: 'plus one lot' }, '+');
    var faceRow = T.h('div', { class: 'face' }, face, minus, plus);
    var lotNote = T.h('div', { class: 'hint' }, 'face in the bond\'s currency, a multiple of its lot');
    var form = T.form([T.field('bond', ta), T.field('side', sides), T.field('face', faceRow, 'the face (nominal) amount, not a number of bonds')]);
    var pv = T.h('div', { class: 'pv' }, T.h('div', { class: 'hint' }, 'the preview appears here: price, yield, accrued, fee, settlement'));
    var status = T.h('div', { class: 'st', role: 'status' });
    var previewBtn = T.h('button', { class: 'btn' }, 'Preview');
    var submit = T.h('button', { class: 'btn primary', disabled: true, title: 'submit' + kp('ticket.submit') }, 'Submit');
    var clear = T.h('button', { class: 'btn small', title: 'clear the ticket' }, 'Clear');
    var acts = T.h('div', { class: 'acts' }, previewBtn, submit, clear);
    var result = T.h('div');
    root.appendChild(T.h('h2', null, 'Bond ticket')); root.appendChild(acct); root.appendChild(form); root.appendChild(lotNote); root.appendChild(meta); root.appendChild(acts); root.appendChild(status); root.appendChild(pv); root.appendChild(result);
    body.appendChild(root);
    st.ui = { root: root, inst: inst, dd: dd, meta: meta, buy: buy, sell: sell, face: face, pv: pv, status: status, submit: submit, result: result, lotNote: lotNote };
    buy.addEventListener('click', function () { setSide('BUY'); schedule(); });
    sell.addEventListener('click', function () { setSide('SELL'); schedule(); });
    function step(dir) { var lot = (st.inst && st.inst.lot) || 1000; var v = Number(String(face.value).replace(/,/g, '')) || 0; v = Math.max(0, v + dir * lot); face.value = v ? String(v) : ''; schedule(); }
    minus.addEventListener('click', function () { step(-1); });
    plus.addEventListener('click', function () { step(1); });
    face.addEventListener('input', function () { invalidate(); schedule(); });
    /* the registry's ticket keys (keys.js; Settings > Keyboard): the face box submits / previews itself */
    face.addEventListener('keydown', function (e) {
      if (T.keys.is(e, 'ticket.submit')) { e.preventDefault(); e.stopPropagation(); doSubmit(); }
      else if (T.keys.is(e, 'ticket.preview')) { e.preventDefault(); e.stopPropagation(); runPreview(); }
    });
    previewBtn.addEventListener('click', function () { runPreview(); });
    submit.addEventListener('click', function () { doSubmit(); });
    clear.addEventListener('click', function () { st.instrument_id = null; st.inst = null; inst.value = ''; face.value = ''; meta.textContent = 'pick a bond'; invalidate(); T.clear(pv); T.clear(result); });
    root.addEventListener('keydown', function (e) {
      var typing = e.target && (e.target.tagName === 'INPUT');
      if (T.keys.is(e, 'ticket.clear')) { dd.hidden = true; return; }
      if (typing) { return; }                                      /* the face box submits itself: no second submit here */
      if (T.keys.is(e, 'ticket.buy')) { setSide('BUY'); schedule(); e.preventDefault(); e.stopPropagation(); }
      else if (T.keys.is(e, 'ticket.sell')) { setSide('SELL'); schedule(); e.preventDefault(); e.stopPropagation(); }
      else if (T.keys.is(e, 'ticket.submit')) { e.preventDefault(); e.stopPropagation(); doSubmit(); }
    });
    st.paintKeys = function () {
      meta.textContent = st.instrument_id ? meta.textContent : 'pick a bond from the list' + rowKeysParen() + ' or search here';
      buy.title = 'BUY' + kp('ticket.buy'); sell.title = 'SELL' + kp('ticket.sell'); submit.title = 'submit' + kp('ticket.submit');
    };
    typeahead(inst, dd);
    st.timer = setInterval(function () { if (!document.hidden && st && st.instrument_id && document.body.contains(root) && T.dock.visible()) { runPreview(true); } }, REPREVIEW_MS);
  }

  function typeahead(input, dd) {
    var t = null, sel = -1, items = [];
    function pick(it) { dd.hidden = true; setInstrument(it.instrument_id, it); schedule(); }
    input.addEventListener('input', function () {
      clearTimeout(t); var q = input.value.trim(); if (q.length < 2) { dd.hidden = true; return; }
      if (q.charAt(0) === '^') { T.clear(dd).appendChild(T.h('div', { class: 'muted' }, q.toUpperCase() + ': a Treasury YIELD index (percent), not a security - pick a note or bond of that maturity')); dd.hidden = false; return; }
      t = setTimeout(function () {
        T.api.get('/api/instruments', { q: q.toUpperCase(), kinds: 'BOND', limit: 30, account: st.account_id }).then(function (rows) {
          items = Array.isArray(rows) ? rows : []; sel = -1; T.clear(dd);
          if (!items.length) { dd.appendChild(T.h('div', { class: 'muted' }, 'no bond in this account matches ' + q)); dd.hidden = false; return; }
          items.forEach(function (it, i) { var d = T.h('div', null, (it.symbol || it.instrument_id) + '  ' + (it.name || '')); d.addEventListener('mousedown', function (e) { e.preventDefault(); pick(it); }); dd.appendChild(d); void i; });
          dd.hidden = false;
        }, function (e) { T.api.fail(e, 'bond search'); });
      }, 200);
    });
    input.addEventListener('keydown', function (e) {
      if (dd.hidden) {
        if (T.keys.is(e, 'ticket.preview')) {                    /* the list closed: the preview key looks the symbol up */
          e.preventDefault(); e.stopPropagation(); var q = input.value.trim().toUpperCase();
          if (q.charAt(0) === '^') { st.ui.meta.textContent = T.debtYieldText || (q + ' is a Treasury yield index (a yield in percent), not a security: pick a Treasury note or bond instead'); return; }
          if (q) { setInstrument(q.indexOf('-') > 0 ? q : 'UST-' + q, null); schedule(); }
        }
        return;
      }
      var kids = T.qa('div', dd);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, Math.min(kids.length - 1, sel + (e.key === 'ArrowDown' ? 1 : -1))); kids.forEach(function (k, i) { k.classList.toggle('sel', i === sel); }); }
      else if (e.key === 'Enter') { e.preventDefault(); if (items[sel >= 0 ? sel : 0]) { pick(items[sel >= 0 ? sel : 0]); } }
    });
    input.addEventListener('blur', function () { setTimeout(function () { dd.hidden = true; }, 150); });
  }

  function setSide(side) {
    st.side = side;
    st.ui.buy.classList.toggle('on', side === 'BUY'); st.ui.sell.classList.toggle('on', side === 'SELL');
    invalidate();
  }
  function setInstrument(id, row) {
    st.instrument_id = id; st.inst = null; invalidate();
    st.ui.inst.value = row ? (row.symbol || id) : id.replace(/^UST-/, '');
    st.ui.meta.textContent = 'loading ' + id + ' ...';
    if (T.ticket && T.ticket.ids) { T.ticket.ids([]); }
    T.api.get('/api/instruments/' + encodeURIComponent(id)).then(function (d) {
      if (!st || st.instrument_id !== id) { return; }
      var inst = d.instrument || d; st.inst = inst;
      if (T.rememberKind) { T.rememberKind(id, inst.kind); }
      var ex = d.extra || {}; var h = ex.headline || {};
      st.ui.inst.value = inst.symbol || id;
      st.ui.meta.textContent = (inst.name || id) + (inst.support !== 'FULL' ? ' - LIMITED: ' + (((inst.spec || {}).limited_reason) || 'quotes only') : '');
      st.ui.lotNote.textContent = 'face in ' + (inst.currency || 'USD') + ', a multiple of ' + T.fmt.qty(inst.lot || 1000) + (h.value ? ' . mark ' + h.value + ' (' + (h.source || '') + ')' : '');
      schedule();
    }, function (e) { if (st && st.instrument_id === id) { st.ui.meta.textContent = 'unknown bond ' + id + ': ' + (e.message || e.code); } });
  }
  function invalidate() { st.preview = null; st.ui.submit.disabled = true; }
  function schedule() { clearTimeout(st.deb); st.deb = setTimeout(function () { runPreview(); }, DEBOUNCE_MS); }
  function fieldsNow() { return { account_id: st.account_id, instrument_id: st.instrument_id, side: st.side, face: String(st.ui.face.value || '').replace(/[, ]/g, '') }; }
  function same(a, b) { return a && b && a.account_id === b.account_id && a.instrument_id === b.instrument_id && a.side === b.side && String(a.face) === String(b.face); }

  function runPreview(quiet) {
    if (!st || !st.instrument_id) { return; }
    var f = fieldsNow();
    if (!f.face) { if (!quiet) { setStatus('enter the face amount', ''); } T.clear(st.ui.pv).appendChild(T.h('div', { class: 'hint' }, 'enter the face amount (a multiple of the lot)')); return; }
    var seq = ++st.seq;
    if (!quiet) { setStatus('previewing ...', ''); }
    T.api.mutate('POST', '/api/bonds/preview', f, { retries: 1 }).then(function (p) {
      if (!st || seq !== st.seq) { return; }
      st.preview = p; st.previewAt = Date.now(); st.previewFields = f;
      paintPreview(p);
      var ok = p && p.ok && !(p.blockers || []).length;
      st.ui.submit.disabled = !ok || st.busy;
      if (quiet && st.holdUntil && Date.now() < st.holdUntil) { return; }        /* a refusal / fill message stays readable */
      setStatus(ok ? 'ready: ' + (kl('ticket.submit') ? kl('ticket.submit') + ' or Submit' : 'Submit') + ' submits' : 'not submittable: ' + ((p.blockers || [])[0] || {}).message, ok ? 'ok' : 'err');
    }, function (e) {
      if (!st || seq !== st.seq) { return; }
      st.preview = null; st.ui.submit.disabled = true;
      setStatus('preview failed: ' + T.api.describe(e), 'err');
    });
  }
  function setStatus(text, cls) { st.ui.status.textContent = text; st.ui.status.className = 'st' + (cls ? ' ' + cls : ''); }

  function paintPreview(p) {
    var pv = T.clear(st.ui.pv);
    (p.blockers || []).forEach(function (b) { pv.appendChild(T.h('div', { class: 'blk', title: b.code || '' }, b.message || T.codeWords(b.code))); });   /* V104R2-BW-04 */
    (p.warnings || []).forEach(function (w) { pv.appendChild(T.h('div', { class: 'wrn' }, w)); });
    if (p.price == null) { if (p.mark) { pv.appendChild(T.h('div', null, 'mark ', T.px(p.mark))); } return; }
    var ccy = p.currency || 'USD';
    var paying = p.side === 'BUY';
    var tot = T.h('div', { class: 'tot' }, (paying ? 'PAY ' : 'RECEIVE ') + ccy + ' ' + money(String(Math.abs(Number(p.net_cash || 0)).toFixed(2))) + ' on ' + (p.settle_date || '') + ' (' + (p.settlement || 'T+1') + ')');
    pv.appendChild(tot);
    var m = p.mark || {};
    var fund = p.funding || {};
    var kv = [
      ['mark', T.px(m), (m.source || '') + (m.note ? '\n' + m.note : '')],
      ['half-spread', (paying ? '+ ' : '- ') + p.half_spread + ' per 100', p.half_spread_basis || ''],
      ['dealt at', p.price + ' (clean, per 100)', p.price_basis || ''],
      ['yield', p.yield_pct != null ? T.fmt.num(p.yield_pct, 3) + ' %' : 'n/a', 'yield to maturity at the dealt price'],
      ['accrued', p.accrued + ' per 100', 'ACT/ACT accrued of the trade date'],
      ['clean', ccy + ' ' + money(p.clean), ''],
      ['accrued amt', ccy + ' ' + money(p.accrued_amount != null ? p.accrued_amount : p.accrued_c != null ? (p.accrued_c / 100).toFixed(2) : p['accrued']), ''],
      ['fee', ccy + ' ' + money(p.fee), p.fee_basis || ''],
      ['trade / settle', (p.trade_date || '') + ' / ' + (p.settle_date || ''), 'settlement ' + (p.settlement || '')],
      ['position', T.fmt.qty(p.position_before) + ' -> ' + T.fmt.qty(p.position_after), 'face held in this account'],
      ['DV01 change', p.dv01_change != null ? 'USD ' + money(p.dv01_change) + ' per +1 bp' : 'n/a', 'P&L of this trade\'s face for +1 bp (a purchase adds a negative DV01)']
    ];
    if (fund.note) { kv.push(['funding', fund.note, fundTitle(fund)]); }
    pv.appendChild(T.kv(kv));
    if (p.arithmetic) { pv.appendChild(T.h('div', { class: 'hint' }, p.arithmetic)); }
  }

  /* the funding detail for the hover, in words (never the raw JSON: amounts as money, keys as words) */
  function fundTitle(f) {
    return Object.keys(f || {}).filter(function (k) { return k !== 'note' && f[k] != null && f[k] !== '' && typeof f[k] !== 'object'; }).map(function (k) {
      var v = f[k];
      return k.replace(/_/g, ' ') + ': ' + (typeof v === 'string' && /^-?\d+\.\d{2}$/.test(v) ? money(v) : (typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v)));
    }).join('\n');
  }
  /* V104R2-BW-02 (c): the fill line without the internal conventions group the stored memo ends with ('(EXECUTION, ACCRUED, COST_BASIS)') */
  function memoWords(m) { return String(m || '').replace(/\s*\((?:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*)(?:,\s*[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*)*\)\s*$/, ''); }

  function doSubmit() {
    if (!st || st.busy) { return; }
    var p = st.preview; var f = fieldsNow();
    if (!p || !p.ok || (p.blockers || []).length) { setStatus('preview first: nothing is submitted without a clean preview', 'err'); return; }
    if (!same(f, st.previewFields) || Date.now() - st.previewAt > PREVIEW_TTL_MS) { setStatus('the preview is stale - previewing again', 'err'); runPreview(); return; }
    st.busy = true; st.ui.submit.disabled = true; setStatus('submitting ...', '');
    T.api.post('/api/bonds/trade', { account_id: f.account_id, instrument_id: f.instrument_id, side: f.side, qty: Number(f.face) }).then(function (r) {
      st.busy = false;
      var fill = (r && r.fill) || {};
      var eco = (r && r.economics) || {};
      setStatus((r && r.replayed ? 'already submitted: ' : 'filled: ') + fill.side + ' ' + T.fmt.qty(fill.qty) + ' ' + (st.ui.inst.value || '') + ' @ ' + fill.price, 'ok');
      st.holdUntil = Date.now() + 20000;
      T.clear(st.ui.result).appendChild(T.h('div', { class: 'wrn', style: 'border-left-color:var(--up)', title: fill.memo || '' }, memoWords(fill.memo) + ' . net ' + (eco.currency || '') + ' ' + money(eco.net_cash) + ' . fill ' + (fill.fill_id || '')));
      T.ui.toast('bond ' + fill.side + ' ' + T.fmt.qty(fill.qty) + ' @ ' + fill.price + ' booked', 'ok');
      ['positions', 'account_detail', 'accounts', 'orders'].forEach(function (n) { T.poll.kick(n); });
      T.poll.kickAll();
      invalidate(); runPreview(true);
    }, function (e) {
      st.busy = false;
      setStatus('refused: ' + T.api.describe(e), 'err');
      st.holdUntil = Date.now() + 20000;
      runPreview(true);
    });
  }

  /* ---------------------------------------------------------------- the bond list (C18) ----------------------------- */
  function listColumns(acct) {
    return [
      { key: 'symbol', label: 'bond', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol) + '</b>' + (r.unavailable ? ' <span class="state-tag LIMITED" title="' + T.esc(r.reason || '') + '">N/A</span>' : (r.support !== 'FULL' ? ' <span class="state-tag LIMITED" title="' + T.esc(r.limited_reason || '') + '">LIMITED</span>' : '')); }, text: function (r) { return r.symbol || ''; } },
      { key: 'act', label: 'trade', cls: 'l', html: function (r) {
        if (r.unavailable || r.support !== 'FULL' || r.price == null) { return '<span class="why" title="' + T.esc(r.reason || r.limited_reason || (r.mark && r.mark.note) || 'no price') + '">' + (r.unavailable ? 'n/a' : (r.support !== 'FULL' ? 'quotes only' : 'no price')) + '</span>'; }
        return '<button class="btn small act" data-act="b" title="BUY' + T.esc(kp('grid.buy')) + '">b</button><button class="btn small act" data-act="s" title="SELL' + T.esc(kp('grid.sell')) + '">s</button>';
      }, text: function () { return ''; } },
      { key: 'ccy', label: 'ccy', cls: 'l', text: function (r) { return r.currency || ''; } },
      { key: 'coupon', label: 'coupon', text: function (r) { return r.coupon_pct == null ? '' : T.fmt.num(r.coupon_pct, 3); } },
      { key: 'maturity', label: 'maturity', cls: 'l', text: function (r) { return r.maturity || ''; } },
      { key: 'price', label: 'price', html: function (r) { return r.unavailable ? '<span class="muted">-</span>' : T.pxHtml(r.mark); }, text: function (r) { return r.price || ''; } },
      { key: 'yield', label: 'yield', text: function (r) { return r.yield_pct == null ? '' : T.fmt.num(r.yield_pct, 3) + ' %'; } },
      { key: 'asof', label: 'as of', cls: 'l c-asof', html: function (r) { var i = (r.mark && r.mark.inputs) || {}; return T.esc(i.as_of_date || ''); }, text: function (r) { var i = (r.mark && r.mark.inputs) || {}; return i.as_of_date || ''; } },
      { key: 'dv01', label: 'DV01/1M', cls: 'num c-dv01', text: function (r) { return r.dv01_per_1m == null ? '' : T.fmt.num(r.dv01_per_1m, 0); } },
      { key: 'position', label: 'position', text: function (r) { return r.position ? T.fmt.qty(r.position) : ''; } },
      { key: 'name', label: 'name', cls: 'l c-name', html: function (r) { var lab = (r.mark && r.mark.inputs && r.mark.inputs.label) || r.reason || ''; return '<span class="nm" title="' + T.esc((r.name || '') + (lab ? ' - ' + lab : '')) + '">' + T.esc(r.name || '') + '</span>'; }, text: function (r) { return r.name || ''; } }
    ];
  }
  function bondlist(el, opts) {
    ensureCss();
    opts = opts || {};
    var acct = opts.account_id; var scope = opts.scope || 'FOREIGN';
    var root = T.h('div', { class: 'bl' }); T.clear(el).appendChild(root);
    var note = T.h('div', { class: 'hint' }); var err = T.h('div', { class: 'error', hidden: true }); var gEl = T.h('div');
    root.appendChild(err); root.appendChild(gEl); root.appendChild(note);
    var grid = null, dead = false, timer = null;
    function rows(d) {
      var out = (d.rows || []).slice();
      (d.missing || []).forEach(function (m) { out.push({ instrument_id: 'NA-' + m.country + '-' + m.tenor, symbol: m.country + ' ' + m.tenor, name: 'generic ' + m.country + ' government ' + m.tenor, unavailable: true, reason: m.reason, support: 'LIMITED' }); });
      return out;
    }
    function act(r, side) {
      if (r.unavailable || r.support !== 'FULL' || r.price == null) { T.ui.toast(r.symbol + ': ' + (r.reason || r.limited_reason || (r.mark && r.mark.note) || 'no price'), 'err', 5000); return; }
      T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, kind: 'BOND', side: side });
    }
    function load() {
      return T.api.get('/api/debt/bonds', { account_id: acct, scope: scope }).then(function (d) {
        if (dead) { return; }
        err.hidden = true;
        if (T.rememberKinds) { T.rememberKinds(d.rows); }
        note.textContent = d.note || '';
        if (!grid) {
          grid = T.components.grid.mount(gEl, { columns: listColumns(acct), rows: rows(d), csv: 'foreign-bonds.csv', empty: 'no foreign government benchmark yet (the ECB / MOF curves load in the background)',
            rowKey: function (r) { return r.instrument_id; }, rowClass: function (r) { return r.unavailable ? 'unavail' : ''; },
            groupBy: function (r) { return { DE: 'Germany (EUR)', JP: 'Japan (JPY)', GB: 'United Kingdom (GBP)', FR: 'France (EUR)', IT: 'Italy (EUR)' }[String(r.symbol).split(' ')[0]] || ''; },
            onEnter: function (r) { if (!r.unavailable) { T.router.go(T.router.instrument(acct, r.instrument_id)); } },
            onKey: function (k, r) { if (k === 'b' || k === 's') { act(r, k === 'b' ? 'BUY' : 'SELL'); return true; } return false; },
            onClick: function (r, e) { var b = e.target.closest('button[data-act]'); if (b) { act(r, b.dataset.act === 'b' ? 'BUY' : 'SELL'); } } });
        } else { grid.update(rows(d)); }
      }, function (e) { if (dead) { return; } err.hidden = false; err.textContent = 'foreign bonds: ' + T.api.describe(e); });
    }
    load();
    timer = setInterval(function () { if (!document.hidden && document.body.contains(root)) { load(); } }, 60000);
    return { unmount: function () { dead = true; clearInterval(timer); }, reload: load };
  }

  T.ticketKinds.BOND = { open: open };
  T.components.bondticket = { open: open, mount: function (el, fields) { return open(el, fields); }, state: function () { return st; } };
  T.components.bondlist = { render: bondlist, mount: bondlist };
  if (T.keys && typeof T.keys.uses === 'function') { T.keys.uses('components/bondticket.js'); }     /* its keys follow Settings > Keyboard */
  if (T.state && T.state.on) { T.state.on('keybinds', function () { if (st && st.paintKeys && st.ui && st.ui.root && st.ui.root.isConnected) { st.paintKeys(); } }); }
})();
