/* Negotiated-price ticket for private company shares (TERMINAL_DESIGN.md s13.13, s17.1 components/privateticket.js,
 * s16 POST /api/private/preview + /api/private/trades; WP-19).  Mounted by pages/private_markets.js:
 *
 *   var h = T.components.privateticket.mount(el, {account_id, companies: [...], company_id, side, onDone});
 *   h.prefill({company_id, side, qty}); h.refresh(companies); h.unmount();
 *
 * Fields: company, side (BUY secondary shares / SELL secondary), kind (SECONDARY | ROUND = participate in a round), qty,
 * negotiated price per share (defaults to the last round's mark), discount / premium % (a convenience that fills the
 * price from the mark - the server recomputes everything), placement fee % (default settings.private_defaults.fee_pct,
 * 3 %; 0 for ROUND), settlement lag in CALENDAR days (default 30) and lock-up months (default 6).  Preview (debounced
 * 300 ms, Enter) -> POST /api/private/preview; the panel shows gross, fee, net, "pending until <settle date>", the lock-up,
 * the mark with its source and as-of, the funding plan (sweep-in from Treasury), FIFO cost and realised for a SELL, the
 * amber LOCKUP warning ("lock-up until <date>; transfer restrictions apply (Rule 144)" - never a blocker), red blockers,
 * and the PRIVATE_TRADE sentence.  Submit (Ctrl+Enter) -> POST /api/private/trades with one request id per click;
 * enabled only with a fresh preview (< 120 s) and no blockers.  Nothing is computed here but the price convenience.
 * A company no longer private (c.listing.effective: LISTED / ACQUIRED) is refused here before any call for a BUY, and for
 * a SELL with nothing held, with the server's sentence ("Listed 2026-06-12: SPCX on Nasdaq - trade it in US Exchange") and
 * an "open SPCX in US Exchange" link; the API refuses the same (400 COMPANY_LISTED / COMPANY_ACQUIRED).  Pre-listing
 * shares still held are offered for SELL only (closing) and the preview shows the server's LISTED warning.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};
  var PREVIEW_TTL_MS = 120000;

  function defaults() {
    var s = (T.state && T.state.settings) || {};
    var d = s.private_defaults || {};
    return { fee_pct: d.fee_pct != null ? String(d.fee_pct) : '0.03', settle_days: d.settle_days != null ? String(d.settle_days) : '30', lockup_months: d.lockup_months != null ? String(d.lockup_months) : '6' };
  }
  function pctFromFraction(fr) { var n = Number(fr); return isFinite(n) ? String(Math.round(n * 10000) / 100) : ''; }
  function fractionFromPct(p) { var n = Number(String(p).replace(/%/g, '').trim()); return isFinite(n) ? String(Math.round(n * 10000) / 1000000) : ''; }
  function markOf(c) { return c && c.mark && c.mark.value != null ? c.mark.value : null; }
  function listingOf(c) { var l = c && c.listing; return l && l.effective ? l : null; }
  function usAccount(acct) { var s = String(acct || ''); var i = s.indexOf(':'); return (i >= 0 ? s.slice(0, i) : s) + ':US_EXCHANGE'; }
  /* a company no longer private refuses a BUY, and a SELL with nothing held here (the server answers the same 400) */
  function refusal(c, sideVal) {
    var l = listingOf(c);
    if (!l) { return null; }
    var held = c.position && c.position.qty ? Number(c.position.qty) : 0;
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
    var companies = opts.companies || [];
    var state = { company_id: opts.company_id || (companies[0] && companies[0].company_id) || '', side: opts.side || 'BUY', kind: 'SECONDARY', preview: null, previewAt: 0, busy: false, timer: null };
    var d = defaults();
    T.clear(el);
    var company = T.h('select', { id: 'pt-company' });
    var side = T.h('select', { id: 'pt-side' }, T.h('option', { value: 'BUY' }, 'BUY - buy secondary shares'), T.h('option', { value: 'SELL' }, 'SELL - sell secondary'));
    var kind = T.h('select', { id: 'pt-kind' }, T.h('option', { value: 'SECONDARY' }, 'SECONDARY (negotiated)'), T.h('option', { value: 'ROUND' }, 'ROUND (participate in a round)'));
    var qty = T.h('input', { type: 'text', id: 'pt-qty', placeholder: 'shares', value: opts.qty != null ? String(opts.qty) : '' });
    var price = T.h('input', { type: 'text', id: 'pt-price', placeholder: 'negotiated price per share' });
    var disc = T.h('input', { type: 'text', id: 'pt-disc', placeholder: 'discount (-) / premium (+) % vs the mark' });
    var fee = T.h('input', { type: 'text', id: 'pt-fee', value: pctFromFraction(d.fee_pct) });
    var settle = T.h('input', { type: 'text', id: 'pt-settle', value: d.settle_days });
    var lock = T.h('input', { type: 'text', id: 'pt-lock', value: d.lockup_months });
    var markLine = T.h('div', { class: 'hint' });
    var panel = T.h('div', { class: 'preview' });
    var result = T.h('div');
    var submit = T.h('button', { class: 'btn primary', disabled: true, title: 'submit (Ctrl+Enter): enabled with a fresh preview and no blockers' }, 'Submit');
    var previewBtn = T.h('button', { class: 'btn', title: 'preview (Enter)' }, 'Preview');
    var form = T.form([T.field('company', company), T.field('side', side), T.field('kind', kind), T.field('quantity', qty), T.field('price / share', price, 'the negotiated price: the trade executes at the price you enter'),
      T.field('discount / premium %', disc, 'fills the price from the mark (a convenience; the server recomputes everything)'), T.field('placement fee %', fee, 'default 3 % for a secondary, 0 for a round'),
      T.field('settle (calendar days)', settle, 'T+30 calendar days by default; the cash is pending until then'), T.field('lock-up (months)', lock, 'the lot carries the lock-up; the Sell ticket warns while it runs'),
      ['', T.h('div', { class: 'row' }, previewBtn, submit)]]);
    el.appendChild(form); el.appendChild(markLine); el.appendChild(panel); el.appendChild(result);
    el.appendChild(T.h('div', { class: 'hint' }, T.conv('PRIVATE_TRADE') || 'Private secondaries are negotiated: the trade executes at the price you enter.'));

    function current() { var c = null; companies.forEach(function (x) { if (x.company_id === company.value) { c = x; } }); return c; }
    function fillCompanies() {
      T.clear(company);
      companies.forEach(function (c) {
        var pos = c.position && c.position.qty ? ' - held ' + T.fmt.qty(c.position.qty) : '';
        var l = listingOf(c);
        var tag = l ? ' - ' + (l.kind === 'ACQUIRED' ? 'acquired' : 'listed') + (l.ticker ? ' ' + l.ticker : '') + ', sell only' : '';
        company.appendChild(T.h('option', { value: c.company_id, selected: c.company_id === state.company_id }, c.name + ' (' + c.company_id + ')' + pos + tag));
      });
      if (!company.value && companies.length) { company.value = companies[0].company_id; }
      state.company_id = company.value;
      paintMark();
    }
    function paintMark() {
      var c = current();
      T.clear(markLine);
      if (!c) { markLine.appendChild(T.h('span', { class: 'muted' }, 'no company selected')); return; }
      var m = c.mark || null;
      var l = listingOf(c);
      var span = T.h('span'); span.innerHTML = 'mark ' + T.pxHtml(m, { basis: l && l.kind === 'LISTED' ? 'listed price' : 'last reported round' });
      markLine.appendChild(span);
      if (c.source_url) { markLine.appendChild(T.h('span', { class: 'muted' }, ' . source: ' + c.source_url + (c.as_of ? ' (as of ' + c.as_of + ')' : ''))); }
      if (l) {
        var line = T.h('div', { class: 'warn', style: 'color: var(--accent)' }, T.h('b', null, (l.kind || 'LISTED') + ' '), l.message || '');
        var link = usLink(l.trade_in, opts.account_id); if (link) { line.appendChild(T.h('span', null, ' . ')); line.appendChild(link); }
        markLine.appendChild(line);
      }
      if (m && m.quality === 'unavailable' && l) { markLine.appendChild(T.h('div', { class: 'muted' }, 'no mark: ' + (m.note || 'listed mark unavailable'))); }
      else if (m && m.quality === 'unavailable') { markLine.appendChild(T.h('div', { class: 'muted' }, 'no mark: ' + (m.note || 'valuation only') + ' - enter the negotiated price; the position will show an unavailable mark until a round with a price per share is recorded')); }
      if (!price.value && markOf(c) != null) { price.value = String(markOf(c)); }
    }
    function syncFromDisc() {
      var c = current(); var m = markOf(c); var p = Number(String(disc.value).replace(/%/g, '').trim());
      if (m == null || !isFinite(p)) { return; }
      price.value = String(Math.round(Number(m) * (1 + p / 100) * 10000) / 10000);
    }
    function syncFromPrice() {
      var c = current(); var m = markOf(c); var p = Number(price.value);
      if (m == null || !isFinite(p) || !Number(m)) { disc.value = ''; return; }
      disc.value = String(Math.round((p / Number(m) - 1) * 10000) / 100);
    }
    function body() {
      var b = { account_id: opts.account_id, company_id: company.value, side: side.value, kind: kind.value, qty: qty.value.trim(), price: price.value.trim() };
      if (fee.value.trim() !== '') { b.fee_pct = fractionFromPct(fee.value); }
      if (settle.value.trim() !== '') { b.settle_days = settle.value.trim(); }
      if (lock.value.trim() !== '') { b.lockup_months = lock.value.trim(); }
      return b;
    }
    function stale() { return !state.preview || (Date.now() - state.previewAt) > PREVIEW_TTL_MS; }
    function setSubmit() { submit.disabled = state.busy || stale() || !!(state.preview.blockers && state.preview.blockers.length); }
    function money(x) { return T.fmt.money(x); }
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
      var mk = T.h('div', { class: 'hint' }); mk.innerHTML = 'mark ' + T.pxHtml(p.mark, { basis: 'last reported round' }) + (p.mark && p.mark.inputs && p.mark.inputs.source_url ? ' <span class="muted">source ' + T.esc(p.mark.inputs.source_url) + ' (as of ' + T.esc(p.mark.inputs.as_of || '') + ')</span>' : '');
      panel.appendChild(mk);
      if (p.funding) {
        var f = p.funding;
        panel.appendChild(T.kv([['funding', f.note || f.source], ['own available at settle', money(f.own_available)], ['Treasury available', money(f.treasury_available)], ['sweep-in from Treasury', money(f.sweep_in)]]));
      }
      (p.warnings || []).forEach(function (w) { panel.appendChild(T.h('div', { class: 'warn', style: 'color: var(--accent)' }, T.h('b', null, w.code + ' '), w.message)); });
      (p.blockers || []).forEach(function (b) { panel.appendChild(T.h('div', { class: 'error' }, b.code + ': ' + b.message)); });
      panel.appendChild(T.h('div', { class: 'hint' }, p.sentence || ''));
    }
    function paintRefusal(r) {
      var box = T.h('div', { class: 'error' }, r.code + ': ' + r.message);
      var link = usLink(r.trade_in, opts.account_id); if (link) { box.appendChild(T.h('span', null, ' . ')); box.appendChild(link); }
      panel.appendChild(box);
    }
    function doPreview() {
      if (state.timer) { clearTimeout(state.timer); state.timer = null; }
      var rf = refusal(current(), side.value);
      if (rf) { state.preview = null; submit.disabled = true; T.clear(panel); paintRefusal(rf); return Promise.resolve(); }
      var b = body();
      if (!b.company_id || !b.qty || !b.price) { state.preview = null; T.clear(panel); submit.disabled = true; return Promise.resolve(); }
      return T.api.post('/api/private/preview', b).then(function (p) {
        state.preview = p; state.previewAt = Date.now(); paintPreview(p); setSubmit();
      }, function (e) {
        state.preview = null; submit.disabled = true; T.clear(panel);
        if (e && (e.code === 'COMPANY_LISTED' || e.code === 'COMPANY_ACQUIRED')) { paintRefusal({ code: e.code, message: e.message, trade_in: e.details && e.details.trade_in }); return; }
        panel.appendChild(T.h('div', { class: 'error' }, (e && e.code ? e.code + ': ' : '') + (e && e.message || String(e))));
        if (e && e.code === 'NOT_ELIGIBLE_QP') { panel.appendChild(T.h('div', { class: 'hint' }, T.conv('QP'))); }
      });
    }
    function schedule() { if (state.timer) { clearTimeout(state.timer); } state.timer = setTimeout(doPreview, 300); }
    function doSubmit() {
      if (submit.disabled) { return; }
      var rf = refusal(current(), side.value);
      if (rf) { submit.disabled = true; state.preview = null; T.clear(panel); paintRefusal(rf); return; }
      state.busy = true; submit.disabled = true; T.clear(result);
      T.api.post('/api/private/trades', body()).then(function (r) {
        state.busy = false; state.preview = null; submit.disabled = true;
        var f = r.fill || {}; var s = r.settlement || {}; var l = r.lot || null;
        var kv = [['fill', (f.fill_id || '') + ' . ' + (r.side || f.side) + ' ' + T.fmt.qty(f.qty) + ' @ ' + T.fmt.px(f.price) + ' (' + (f.origin || 'NEGOTIATED') + ')'],
          ['fee', money(f.fee_c != null ? f.fee_c / 100 : (r.economics && r.economics.fee))], ['settlement', s.due_date ? (s.note || ('pending until ' + s.due_date)) : 'none'],
          l ? ['lot', 'lot ' + l.lot_id + ' . ' + T.fmt.qty(l.qty_open) + ' @ ' + T.fmt.px(l.unit_cost) + (l.lock_until ? ' . lock-up until ' + l.lock_until : '')] : null,
          r.economics && r.economics.realised_c ? ['realised', T.fmt.signed(money(r.economics.realised))] : null,
          f.funding && f.funding.sweep_c ? ['sweep-in from Treasury', T.fmt.cents(f.funding.sweep_c)] : null,
          ['position', T.fmt.qty(r.position_qty)]];
        result.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'done'), T.kv(kv)));
        (r.warnings || []).forEach(function (w) { result.appendChild(T.h('div', { style: 'color: var(--accent)' }, T.h('b', null, w.code + ' '), w.message)); });
        T.ui.toast('private trade done: ' + (f.fill_id || ''), 'ok');
        T.poll.kick('positions'); T.poll.kick('account_detail'); T.poll.kick('accounts');
        if (opts.onDone) { try { opts.onDone(r); } catch (e) { console.error(e); } }
      }, function (e) {
        state.busy = false; setSubmitAfterError();
        var box = T.h('div', { class: 'error' }, T.api.fail(e, 'private trade'));
        var link = e && e.details ? usLink(e.details.trade_in, opts.account_id) : null; if (link) { box.appendChild(T.h('span', null, ' . ')); box.appendChild(link); }
        result.appendChild(box);
      });
    }
    function setSubmitAfterError() { submit.disabled = true; state.preview = null; }
    company.addEventListener('change', function () { state.company_id = company.value; price.value = ''; disc.value = ''; paintMark(); schedule(); });
    side.addEventListener('change', schedule);
    kind.addEventListener('change', function () { fee.value = kind.value === 'ROUND' ? '0' : pctFromFraction(d.fee_pct); schedule(); });
    [qty, fee, settle, lock].forEach(function (i) { i.addEventListener('input', schedule); });
    price.addEventListener('input', function () { syncFromPrice(); schedule(); });
    disc.addEventListener('input', function () { syncFromDisc(); schedule(); });
    previewBtn.addEventListener('click', doPreview);
    submit.addEventListener('click', doSubmit);
    el.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); doSubmit(); }
      else if (e.key === 'Enter') { e.preventDefault(); doPreview(); }
      else if (e.key === 'Escape') { state.preview = null; T.clear(panel); submit.disabled = true; }
    });
    var ttlTimer = setInterval(function () { if (state.preview) { setSubmit(); } }, 5000);
    side.value = state.side; fillCompanies();
    var handle = {
      prefill: function (f) {
        f = f || {};
        if (f.company_id) { state.company_id = f.company_id; company.value = f.company_id; price.value = ''; disc.value = ''; paintMark(); }
        if (f.side) { side.value = f.side; }
        if (f.qty != null) { qty.value = String(f.qty); }
        try { qty.focus(); } catch (e) { /* ignore */ }
        schedule();
      },
      refresh: function (list) { companies = list || []; fillCompanies(); },
      preview: doPreview,
      unmount: function () { clearInterval(ttlTimer); if (state.timer) { clearTimeout(state.timer); } T.clear(el); }
    };
    return handle;
  }
  T.components.privateticket = { mount: mount };
})();
