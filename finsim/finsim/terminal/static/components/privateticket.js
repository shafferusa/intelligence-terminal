/* Negotiated-price ticket for private company shares (TERMINAL_DESIGN.md s13.13, s17.1 components/privateticket.js,
 * s16 POST /api/private/preview + /api/private/trades; WP-19; Build 2 P11: docked, OA-20 / OA-27 / OA-28).
 *
 * It lives in the DOCK (T.ticketKinds.PRIVATE, BUILD2_PLAN C11 / C23): every T.ticket.open({account_id, instrument_id, kind:
 * 'PRIVATE', side, qty}) lands here - the Private Markets page's b / s, the instrument page's Buy / Sell, Positions' Sell and
 * Orders' New order - and an empty dock on a Private Markets account mounts it too.  The handler loads the account's companies
 * (GET /api/private/companies?account_id=, kept 30 s) and prefills company / side / quantity; reopening it for the same account
 * keeps what was typed.  T.components.privateticket.mount(el, opts) is still there for a page that wants it in-page:
 *
 *   var h = T.components.privateticket.mount(el, {account_id, companies: [...], listed: [...], company_id, side, qty, onDone});
 *   h.prefill({company_id | instrument_id, side, qty}); h.refresh(companies, listed); h.unmount();
 *
 * Fields: company, side (BUY secondary shares / SELL secondary), kind (SECONDARY | ROUND = participate in a round), qty,
 * negotiated price per share (defaults to the last round's mark), discount / premium % (a convenience that fills the price
 * from the mark - the server recomputes everything), placement fee % (default settings.private_defaults.fee_pct, 3 %; 0 for
 * ROUND), settlement lag in CALENDAR days (default 30) and lock-up months (default 6).  Preview (debounced 300 ms, the
 * preview key) -> POST /api/private/preview; the panel shows gross, fee, net, "pending until <settle date>", the lock-up,
 * the mark with its source and as-of, the funding plan (sweep-in from Treasury), FIFO cost and realised for a SELL, the amber
 * LOCKUP warning (never a blocker), the risk lines (concentration and private / illiquid after the trade, names without a
 * price counted at cost), red blockers.  A preview with no price says "enter the negotiated price per share" (OA-28); the
 * PRIVATE_TRADE sentence is printed once, under the ticket.  Submit (the submit key) -> POST /api/private/trades with one
 * request id per click; enabled only with a fresh preview (< 120 s) and no blockers.  The result shows the fill, the
 * settlement, the lot and the Treasury sweep the purchase took (funding.sweep, APIUI-13).
 * A company no longer private (c.listing.effective: LISTED / ACQUIRED) is refused here before any call for a BUY, and for a
 * SELL with nothing held, with the server's sentence ("Listed 2026-06-12: SPCX on Nasdaq - trade it in US Exchange") and an
 * "open SPCX in US Exchange" link; the API refuses the same (400 COMPANY_LISTED / COMPANY_ACQUIRED).  Pre-listing shares
 * still held are offered for SELL only (closing) and the preview shows the server's LISTED warning ("sell only").
 * Keys come from the registry (keys.js): ticket.preview / ticket.submit / ticket.clear.  ASCII only; nothing is computed here
 * but the price convenience.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};
  T.ticketKinds = T.ticketKinds || {};
  var PREVIEW_TTL_MS = 120000, LIST_TTL_MS = 30000;
  var CSS = '.pt .meta{font-family:var(--mono);font-size:11px;color:var(--fg3);margin:2px 0 6px}.pt .form{grid-template-columns:118px minmax(0,1fr)}' +
            '.pt .form select,.pt .form input{width:100%;min-width:0}.pt .acts{display:flex;gap:6px;align-items:center;margin:8px 0}' +
            '.pt .rl{border-left:3px solid var(--warn);padding:3px 8px;margin:4px 0;background:var(--panel-2);font-size:11.5px}.pt .rl.info{border-left-color:var(--line)}' +
            '.pt .ask{color:var(--fg2);margin:6px 0;font-size:12px}.pt .preview .kv{margin:4px 0}';
  function ensureCss() { if (document.getElementById('privateticket-css')) { return; } var s = document.createElement('style'); s.id = 'privateticket-css'; s.textContent = CSS; document.head.appendChild(s); }
  function keyIs(e, id, legacy) { return T.keys && typeof T.keys.is === 'function' ? T.keys.is(e, id) : !!legacy(e); }
  function paren(id, dflt) { return T.keys && typeof T.keys.paren === 'function' ? T.keys.paren(id) : dflt; }

  function defaults() {
    var s = (T.state && T.state.settings) || {};
    var d = s.private_defaults || {};
    return { fee_pct: d.fee_pct != null ? String(d.fee_pct) : '0.03', settle_days: d.settle_days != null ? String(d.settle_days) : '30', lockup_months: d.lockup_months != null ? String(d.lockup_months) : '6' };
  }
  function pctFromFraction(fr) { var n = Number(fr); return isFinite(n) ? String(Math.round(n * 10000) / 100) : ''; }
  function fractionFromPct(p) { var n = Number(String(p).replace(/%/g, '').trim()); return isFinite(n) ? String(Math.round(n * 10000) / 1000000) : ''; }
  function markOf(c) { return c && c.mark && c.mark.value != null ? c.mark.value : null; }
  function listingOf(c) { var l = c && c.listing; return l && l.effective ? l : null; }
  function heldQty(c) { return c && c.position && c.position.qty ? Number(c.position.qty) : 0; }
  function usAccount(acct) { var s = String(acct || ''); var i = s.indexOf(':'); return (i >= 0 ? s.slice(0, i) : s) + ':US_EXCHANGE'; }
  /* the API answer -> {names (private names + listed / acquired names still held: sell only), listed (every one no longer private)} */
  function splitList(d) {
    var all = Array.isArray(d) ? d : ((d && d.companies) || []);
    var gone = Array.isArray(d) ? [] : ((d && d.listed) || []).slice();
    var names = [];
    all.forEach(function (c) { if (c && listingOf(c)) { gone.push(c); } else if (c) { names.push(c); } });
    return { names: names.concat(gone.filter(function (c) { return heldQty(c) > 0; })), listed: gone };
  }
  var _lists = {};                                             /* account -> {at, names, listed} (the dock's companies) */
  function loadList(acct, force) {
    var c = _lists[acct];
    if (!force && c && Date.now() - c.at < LIST_TTL_MS) { return Promise.resolve(c); }
    return T.api.get('/api/private/companies', { account_id: acct }).then(function (d) {
      var s = splitList(d); s.at = Date.now(); _lists[acct] = s; return s;
    });
  }
  /* a company no longer private refuses a BUY, and a SELL with nothing held here (the server answers the same 400) */
  function refusal(c, sideVal) {
    var l = listingOf(c);
    if (!l) { return null; }
    var held = heldQty(c);
    if (sideVal !== 'BUY' && held > 0) { return null; }
    return { code: l.code || (l.kind === 'ACQUIRED' ? 'COMPANY_ACQUIRED' : 'COMPANY_LISTED'), message: l.message || 'no longer privately traded', trade_in: l.trade_in };
  }
  /* "open SPCX in US Exchange": resolve the symbol first (registers a ticker the master does not hold yet), then route */
  function usLink(t, acct) {
    if (!t || !t.instrument_id) { return null; }
    var target = t.account_id || usAccount(acct);
    var a = T.h('a', { href: T.router.instrument(target, t.instrument_id), title: 'the listed shares trade in ' + target }, 'open ' + t.instrument_id + ' in US Exchange');
    a.addEventListener('click', function (e) {
      e.preventDefault();
      T.api.getReady('/api/instruments/resolve', { symbol: t.instrument_id }).then(function (inst) {
        var id = (inst && (inst.instrument_id || (inst.instrument && inst.instrument.instrument_id))) || t.instrument_id;
        T.router.go(T.router.instrument(target, id));
      }, function (err) { T.api.fail(err, 'open ' + t.instrument_id + ' in US Exchange'); });
    });
    return a;
  }

  function mount(el, opts) {
    opts = opts || {};
    ensureCss();
    var companies = opts.companies || [];
    var listed = opts.listed || [];
    var state = { company_id: '', side: String(opts.side || 'BUY').toUpperCase() === 'SELL' ? 'SELL' : 'BUY', preview: null, previewAt: 0, busy: false, timer: null, seq: 0, foreign: null };
    var d = defaults();
    T.clear(el);
    var root = T.h('div', { class: 'pt', tabindex: '-1' });
    var company = T.h('select', { id: 'pt-company', 'aria-label': 'company' });
    var side = T.h('select', { id: 'pt-side', 'aria-label': 'side' }, T.h('option', { value: 'BUY' }, 'BUY - buy secondary shares'), T.h('option', { value: 'SELL' }, 'SELL - sell secondary'));
    var kind = T.h('select', { id: 'pt-kind', 'aria-label': 'kind' }, T.h('option', { value: 'SECONDARY' }, 'SECONDARY (negotiated)'), T.h('option', { value: 'ROUND' }, 'ROUND (participate in a round)'));
    var qty = T.h('input', { type: 'text', id: 'pt-qty', inputmode: 'numeric', placeholder: 'number of shares', value: opts.qty != null ? String(Math.abs(Number(opts.qty)) || '') : '' });
    var price = T.h('input', { type: 'text', id: 'pt-price', inputmode: 'decimal', placeholder: 'negotiated price per share' });
    var disc = T.h('input', { type: 'text', id: 'pt-disc', placeholder: 'discount (-) / premium (+) % vs the mark' });
    var fee = T.h('input', { type: 'text', id: 'pt-fee', value: pctFromFraction(d.fee_pct) });
    var settle = T.h('input', { type: 'text', id: 'pt-settle', value: d.settle_days });
    var lock = T.h('input', { type: 'text', id: 'pt-lock', value: d.lockup_months });
    var markLine = T.h('div', { class: 'hint' });
    var panel = T.h('div', { class: 'preview' });
    var result = T.h('div');
    var submit = T.h('button', { class: 'btn primary', disabled: true, title: 'submit' + paren('ticket.submit', ' (Ctrl+Enter)') + ': enabled with a fresh preview and no blockers' }, 'Submit');
    var previewBtn = T.h('button', { class: 'btn', title: 'preview' + paren('ticket.preview', ' (Enter)') }, 'Preview');
    var clearBtn = T.h('button', { class: 'btn small', title: 'clear the preview' + paren('ticket.clear', ' (Esc)') }, 'Clear');
    /* the hints follow the owner's keys (Settings > Keyboard) while this ticket is on screen */
    var offKeys = T.state && T.state.on ? T.state.on('keybinds', function () {
      if (!submit.isConnected) { if (offKeys) { offKeys(); } return; }
      submit.title = 'submit' + paren('ticket.submit', '') + ': enabled with a fresh preview and no blockers';
      previewBtn.title = 'preview' + paren('ticket.preview', ''); clearBtn.title = 'clear the preview' + paren('ticket.clear', '');
    }) : null;
    var form = T.form([T.field('company', company), T.field('side', side), T.field('kind', kind), T.field('quantity', qty), T.field('price / share', price, 'the negotiated price: the trade executes at the price you enter'),
      T.field('discount / premium %', disc, 'fills the price from the mark (a convenience; the server recomputes everything)'), T.field('placement fee %', fee, 'default 3 % for a secondary, 0 for a round'),
      T.field('settle (calendar days)', settle, 'T+30 calendar days by default; the cash is pending until then'), T.field('lock-up (months)', lock, 'the lot carries the lock-up; the Sell ticket warns while it runs')]);
    if (opts.docked) {
      root.appendChild(T.h('h2', null, 'Private ticket'));
      root.appendChild(T.h('div', { class: 'meta' }, 'account ' + (T.accountName ? T.accountName(opts.account_id) : opts.account_id) + ' (' + opts.account_id + ') . negotiated secondaries'));
    }
    root.appendChild(form);
    root.appendChild(T.h('div', { class: 'acts' }, previewBtn, submit, clearBtn));
    root.appendChild(markLine); root.appendChild(panel); root.appendChild(result);
    root.appendChild(T.h('div', { class: 'hint' }, T.conv('PRIVATE_TRADE') || 'Private secondaries are negotiated: the trade executes at the price you enter.'));
    el.appendChild(root);

    function find(id) {
      var c = null;
      companies.concat(listed).forEach(function (x) { if (!c && x && (x.company_id === id || x.instrument_id === id)) { c = x; } });
      return c;
    }
    function current() { return state.foreign || find(company.value); }
    function fillCompanies() {
      T.clear(company);
      companies.forEach(function (c) {
        var pos = heldQty(c) ? ' - held ' + T.fmt.qty(heldQty(c)) : '';
        var l = listingOf(c);
        var tag = l ? ' - ' + (l.kind === 'ACQUIRED' ? 'acquired' : 'listed') + (l.ticker ? ' ' + l.ticker : '') + ', sell only' : '';
        company.appendChild(T.h('option', { value: c.company_id }, c.name + ' (' + c.company_id + ')' + pos + tag));
      });
      if (state.company_id && find(state.company_id) && companies.indexOf(find(state.company_id)) >= 0) { company.value = find(state.company_id).company_id; }
      if (!company.value && companies.length) { company.value = companies[0].company_id; }
      if (!state.foreign) { state.company_id = company.value; }
      paintMark();
    }
    function paintMark() {
      var c = current();
      T.clear(markLine);
      if (!c) { markLine.appendChild(T.h('span', { class: 'muted' }, companies.length ? 'pick a company' : 'no private company is offered in this account')); return; }
      var m = c.mark || null;
      var l = listingOf(c);
      var span = T.h('span'); span.innerHTML = 'mark ' + T.pxHtml(m, { basis: l && l.kind === 'LISTED' ? 'listed price' : 'last reported round' });
      markLine.appendChild(span);
      if (c.source_url) {
        var src = String(c.source_url);
        var lk = /^https?:[\/][\/]/.test(src) ? T.h('a', { href: src, target: '_blank', rel: 'noopener', title: src }, src.replace(/^https?:[\/][\/]/, '').slice(0, 36) + (src.length > 44 ? '...' : '')) : T.h('span', { title: src }, src.slice(0, 40));
        markLine.appendChild(T.h('span', { class: 'muted' }, ' . source '));
        markLine.appendChild(lk);
        if (c.as_of) { markLine.appendChild(T.h('span', { class: 'muted' }, ' (as of ' + c.as_of + ')')); }
      }
      if (l) {
        var line = T.h('div', { class: 'warn', style: 'color: var(--accent)' }, T.h('b', null, (l.kind || 'LISTED') + ' '), l.message || '');
        var link = usLink(l.trade_in, opts.account_id); if (link) { line.appendChild(T.h('span', null, ' . ')); line.appendChild(link); }
        markLine.appendChild(line);
      }
      if (m && m.quality === 'unavailable' && l) { markLine.appendChild(T.h('div', { class: 'muted' }, 'no mark: ' + (m.note || 'listed mark unavailable'))); }
      else if (m && m.quality === 'unavailable') { markLine.appendChild(T.h('div', { class: 'muted' }, 'no mark: ' + (m.note || 'valuation only') + ' - enter the negotiated price; the position is carried at cost until a round with a price per share is recorded')); }
      if (!price.value && markOf(c) != null) { price.value = String(markOf(c)); syncFromPrice(); }
    }
    function syncFromDisc() {
      var c = current(); var m = markOf(c); var p = Number(String(disc.value).replace(/%/g, '').trim());
      if (m == null || !isFinite(p) || String(disc.value).trim() === '') { return; }
      price.value = String(Math.round(Number(m) * (1 + p / 100) * 10000) / 10000);
    }
    function syncFromPrice() {
      var c = current(); var m = markOf(c); var p = Number(price.value);
      if (m == null || !isFinite(p) || !Number(m) || String(price.value).trim() === '') { disc.value = ''; return; }
      disc.value = String(Math.round((p / Number(m) - 1) * 10000) / 100);
    }
    function body() {
      var c = current();
      var b = { account_id: opts.account_id, company_id: c ? c.company_id : company.value, side: side.value, kind: kind.value, qty: qty.value.trim().replace(/,/g, ''), price: price.value.trim().replace(/,/g, '') };
      if (fee.value.trim() !== '') { b.fee_pct = fractionFromPct(fee.value); }
      if (settle.value.trim() !== '') { b.settle_days = settle.value.trim(); }
      if (lock.value.trim() !== '') { b.lockup_months = lock.value.trim(); }
      return b;
    }
    function stale() { return !state.preview || (Date.now() - state.previewAt) > PREVIEW_TTL_MS; }
    function setSubmit() { submit.disabled = state.busy || stale() || !!(state.preview && state.preview.blockers && state.preview.blockers.length); }
    function money(x) { return T.fmt.money(x); }
    function ask(text) { T.clear(panel); panel.appendChild(T.h('div', { class: 'ask' }, text)); }
    function paintRisk(lines) {
      (lines || []).forEach(function (w) {
        var info = String(w.severity || '').toUpperCase() !== 'WARN';
        panel.appendChild(T.h('div', { class: 'rl' + (info ? ' info' : ''), title: 'risk informs, it never blocks' }, T.h('b', null, (info ? 'risk ' : 'RISK ') + (w.code || '') + ' '), w.message || ''));
      });
    }
    function paintPreview(p) {
      T.clear(panel);
      if (!p) { return; }
      var kv = [['side / kind', p.side + ' ' + p.kind + ' . ' + T.fmt.qty(p.qty) + ' x ' + T.fmt.px(p.price)],
        ['gross', money(p.gross)], ['placement fee (' + T.fmt.pct(p.fee_pct) + ')', money(p.fee)], [p.side === 'BUY' ? 'net payable' : 'net receivable', money(p.net)],
        ['settlement', 'pending until ' + p.settle_date + ' (T+' + p.settle_days + ' calendar days)'],
        p.lock_until ? ['lock-up', 'until ' + p.lock_until + ' (' + p.lockup_months + ' months)'] : null,
        ['discount / premium', p.discount_pct != null ? T.fmt.signed(T.fmt.num(p.discount_pct, 2)) + ' % vs the mark' : 'n/a (no mark)'],
        p.side === 'SELL' ? ['FIFO cost relieved', money(p.cost_relieved)] : null,
        p.side === 'SELL' ? ['realised', T.fmt.signed(money(p.realised))] : null,
        ['position after', T.fmt.qty((p.position_qty || 0) + (p.side === 'BUY' ? p.qty : -p.qty))]];
      panel.appendChild(T.kv(kv));
      if (p.funding) {
        var f = p.funding;
        panel.appendChild(T.kv([['funding', f.note || f.source], ['own available at settle', money(f.own_available)], ['Treasury available', money(f.treasury_available)], ['sweep-in from Treasury', money(f.sweep_in)]]));
      }
      (p.warnings || []).forEach(function (w) { panel.appendChild(T.h('div', { class: 'warn', style: 'color: var(--accent)' }, T.h('b', null, w.code + ' '), w.message)); });
      paintRisk(p.risk);
      (p.blockers || []).forEach(function (b) { panel.appendChild(T.h('div', { class: 'error' }, b.code + ': ' + b.message)); });
    }
    function paintRefusal(r) {
      T.clear(panel);
      var box = T.h('div', { class: 'error' }, r.code + ': ' + r.message);
      var link = usLink(r.trade_in, opts.account_id); if (link) { box.appendChild(T.h('span', null, ' . ')); box.appendChild(link); }
      panel.appendChild(box);
    }
    function doPreview() {
      if (state.timer) { clearTimeout(state.timer); state.timer = null; }
      var seq = ++state.seq;
      var c = current();
      var rf = refusal(c, side.value);
      state.preview = null; submit.disabled = true;
      if (rf) { paintRefusal(rf); return Promise.resolve(); }
      if (state.foreign && state.foreign.unknown) { ask(state.foreign.message); return Promise.resolve(); }
      var b = body();
      /* OA-28: say what is missing instead of an empty panel */
      if (!b.company_id) { ask('pick a company'); return Promise.resolve(); }
      if (!b.qty) { ask('enter the number of shares'); return Promise.resolve(); }
      if (!b.price) { ask('enter the negotiated price per share' + (markOf(c) == null ? ' (this company has no price per share: its last round reported a valuation only)' : '')); return Promise.resolve(); }
      return T.api.post('/api/private/preview', b, { retries: 0 }).then(function (p) {
        if (seq !== state.seq) { return; }
        state.preview = p; state.previewAt = Date.now(); paintPreview(p); setSubmit();
      }, function (e) {
        if (seq !== state.seq) { return; }
        state.preview = null; submit.disabled = true; T.clear(panel);
        if (e && (e.code === 'COMPANY_LISTED' || e.code === 'COMPANY_ACQUIRED')) { paintRefusal({ code: e.code, message: e.message, trade_in: e.details && e.details.trade_in }); return; }
        if (e && e.code === 'PRICE_REQUIRED') { ask('enter the negotiated price per share'); return; }
        panel.appendChild(T.h('div', { class: 'error' }, (e && e.code ? e.code + ': ' : '') + (e && e.message || String(e))));
        if (e && e.code === 'NOT_ELIGIBLE_QP') { panel.appendChild(T.h('div', { class: 'hint' }, T.conv('QP'))); }
      });
    }
    function schedule() { if (state.timer) { clearTimeout(state.timer); } state.preview = null; submit.disabled = true; state.timer = setTimeout(doPreview, 300); }
    function doSubmit() {
      if (submit.disabled) { return; }
      var rf = refusal(current(), side.value);
      if (rf) { submit.disabled = true; state.preview = null; paintRefusal(rf); return; }
      state.busy = true; submit.disabled = true; T.clear(result);
      T.api.post('/api/private/trades', body()).then(function (r) {
        state.busy = false; state.preview = null; submit.disabled = true;
        var f = r.fill || {}; var s = r.settlement || {}; var l = r.lot || null;
        var fund = r.funding || f.funding || null;
        var swept = fund && T.num(fund, 'sweep') ? T.money(fund, 'sweep') : null;
        var kv = [['fill', (f.fill_id || '') + ' . ' + (r.side || f.side) + ' ' + T.fmt.qty(f.qty) + ' @ ' + T.fmt.px(f.price) + ' (' + (f.origin || 'NEGOTIATED') + ')'],
          ['fee', T.money(f, 'fee')], ['settlement', s.due_date ? (s.note || ('pending until ' + s.due_date)) : 'none'],
          l ? ['lot', 'lot ' + l.lot_id + ' . ' + T.fmt.qty(l.qty_open) + ' @ ' + T.fmt.px(l.unit_cost) + (l.lock_until ? ' . lock-up until ' + l.lock_until : '')] : null,
          r.economics && T.num(r.economics, 'realised') ? ['realised', T.fmt.signed(T.money(r.economics, 'realised'))] : null,
          swept ? ['swept from Treasury', swept + (fund.sweep_id ? ' (' + fund.sweep_id + ')' : '')] : (r.side === 'BUY' ? ['funding', 'paid from the account\'s own cash'] : null),
          ['position', T.fmt.qty(r.position_qty)]];
        result.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'done'), T.kv(kv)));
        (r.warnings || []).forEach(function (w) { result.appendChild(T.h('div', { style: 'color: var(--accent)' }, T.h('b', null, w.code + ' '), w.message)); });
        T.ui.toast('private trade done: ' + (f.fill_id || '') + (swept ? ' (swept ' + swept + ' from Treasury)' : ''), 'ok', 5000);
        T.poll.kick('positions'); T.poll.kick('account_detail'); T.poll.kick('accounts');
        if (T.state && T.state.emit) { T.state.emit('private_trade', r); }
        if (opts.onDone) { try { opts.onDone(r); } catch (e) { console.error(e); } }
      }, function (e) {
        state.busy = false; submit.disabled = true; state.preview = null;
        var box = T.h('div', { class: 'error' }, T.api.fail(e, 'private trade'));
        var link = e && e.details ? usLink(e.details.trade_in, opts.account_id) : null; if (link) { box.appendChild(T.h('span', null, ' . ')); box.appendChild(link); }
        result.appendChild(box);
      });
    }
    function clearPreview() { state.seq++; state.preview = null; T.clear(panel); T.clear(result); submit.disabled = true; }
    company.addEventListener('change', function () { state.foreign = null; state.company_id = company.value; price.value = ''; disc.value = ''; paintMark(); schedule(); });
    side.addEventListener('change', schedule);
    kind.addEventListener('change', function () { fee.value = kind.value === 'ROUND' ? '0' : pctFromFraction(d.fee_pct); schedule(); });
    [qty, fee, settle, lock].forEach(function (i) { i.addEventListener('input', schedule); });
    price.addEventListener('input', function () { syncFromPrice(); schedule(); });
    disc.addEventListener('input', function () { syncFromDisc(); schedule(); });
    previewBtn.addEventListener('click', doPreview);
    submit.addEventListener('click', doSubmit);
    clearBtn.addEventListener('click', clearPreview);
    root.addEventListener('keydown', function (e) {
      if (keyIs(e, 'ticket.submit', function (x) { return x.key === 'Enter' && x.ctrlKey; })) { e.preventDefault(); e.stopPropagation(); doSubmit(); }
      else if (keyIs(e, 'ticket.preview', function (x) { return x.key === 'Enter' && !x.ctrlKey; })) { e.preventDefault(); e.stopPropagation(); doPreview(); }
      else if (keyIs(e, 'ticket.clear', function (x) { return x.key === 'Escape'; })) { e.stopPropagation(); clearPreview(); }
    });
    var ttlTimer = setInterval(function () { if (state.preview) { setSubmit(); } }, 5000);
    function prefill(f) {
      f = f || {};
      var id = f.company_id || f.instrument_id;
      if (id) {
        var c = find(id);
        state.foreign = null;
        if (c && companies.indexOf(c) >= 0) { state.company_id = c.company_id; company.value = c.company_id; }
        else if (c) { state.foreign = c; }                     /* a company no longer private and not held here: its line, no trade */
        else { state.foreign = { company_id: id, name: id, unknown: true, message: id + ' is not a private company offered in this account' }; }
        price.value = ''; disc.value = ''; paintMark();
      }
      if (f.side) { side.value = String(f.side).toUpperCase() === 'SELL' ? 'SELL' : 'BUY'; }
      if (f.qty != null && f.qty !== '') { qty.value = String(Math.abs(Number(f.qty)) || ''); }
      T.clear(result);
      try { (qty.value ? price : qty).focus({ preventScroll: true }); } catch (e) { /* focus is a convenience */ }
      schedule();
    }
    side.value = state.side;
    fillCompanies();
    if (opts.company_id || opts.instrument_id) { prefill({ company_id: opts.company_id || opts.instrument_id, side: state.side, qty: opts.qty }); }
    var handle = {
      account_id: opts.account_id,
      prefill: prefill,
      refresh: function (list, gone) { companies = list || []; if (gone) { listed = gone; } var keep = company.value; fillCompanies(); if (keep && find(keep)) { company.value = keep; } },
      preview: doPreview,
      unmount: function () { clearInterval(ttlTimer); if (state.timer) { clearTimeout(state.timer); } T.clear(el); }
    };
    return handle;
  }

  /* ---- the dock (C11 / C23): T.ticket.open(... kind 'PRIVATE' ...) -> open(dockBody, fields) ---------------------------------- */
  var dock = null;                                             /* {el, account_id, handle, pending, loading} */
  function companyFields(fields) { return { company_id: fields.company_id || fields.instrument_id || null, side: fields.side || null, qty: fields.qty != null ? fields.qty : null }; }
  function open(body, fields) {
    fields = fields || {};
    var acct = fields.account_id;
    var same = dock && dock.el === body && dock.account_id === acct && body.firstChild;
    var want = companyFields(fields);
    if (same) {
      if (dock.handle) { if (want.company_id || want.side || want.qty != null) { dock.handle.prefill(want); } }
      else { dock.pending = want; }
      return dock.handle;
    }
    if (dock && dock.handle && dock.handle.unmount) { try { dock.handle.unmount(); } catch (e) { /* the old ticket is gone anyway */ } }
    T.clear(body);
    var me = { el: body, account_id: acct, handle: null, pending: want };
    dock = me;
    body.appendChild(T.h('div', { class: 'hint' }, 'loading the private companies of ' + (T.accountName ? T.accountName(acct) : acct) + ' ...'));
    loadList(acct, true).then(function (s) {
      if (dock !== me) { return; }
      var p = me.pending || {};
      me.handle = mount(body, { account_id: acct, companies: s.names, listed: s.listed, company_id: p.company_id, side: p.side || 'BUY', qty: p.qty, docked: true,
                                onDone: function () { loadList(acct, true).then(function (s2) { if (dock === me && me.handle) { me.handle.refresh(s2.names, s2.listed); } }, function () { /* the next open reloads */ }); } });
    }, function (e) {
      if (dock !== me) { return; }
      T.clear(body); T.errorBox(body, e, 'private companies');
    });
    return null;
  }
  T.ticketKinds.PRIVATE = { open: open };
  T.components.privateticket = { mount: mount, open: open, refusal: refusal, splitList: splitList };
  if (T.keys && typeof T.keys.uses === 'function') { T.keys.uses('components/privateticket.js'); }
})();
