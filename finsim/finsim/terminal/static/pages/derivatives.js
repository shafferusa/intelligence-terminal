/* Derivatives (FINSIM1 P4 task 10, UI-11): #/derivatives(/tab), Shift+F6 from 1.0.6 - the book's derivatives in one place.  Dealing stays on
 * the prime broker's OTC tab (F6) and the order tickets; this page shows, services and closes.
 *
 *   OTC contracts   book-wide.  The open quote requests on top (accept / decline, a countdown: the shared block of components/swapticket.js),
 *                   then every contract: status and confirmation, the AS-OF badge of an as-of booking, kind and side, notional, maturity, the
 *                   MODEL mark (a valuation from observed inputs, never a dealer price) and what NAV carries, VM balance, IM, DV01 / CS01.
 *                   A contract opens its drawer (terms, the contract timeline, CDX credit events); Terminate; on an open CDX row "Simulate
 *                   credit event" - owner-triggered, off unless pressed, the recovery entered here.
 *   Listed options  the option positions with greeks (the risk report's rows: Cboe where published, else the model on the mark's inputs) and
 *                   the strategies grouped (the QQQ bear put spread: each leg its own row, the package named).
 *   Futures         IM posted, the last settles with their daily VM, open trade equity, and the delivery dates: GC / ZN Dec 26 are physically
 *                   delivered, first notice about 2026-11-30 - roll or close before about 11-27 (approximate, the exchange rule summarised).
 *   Servicing       what is coming: resets, payments, coupons, maturities, option expiries, futures delivery dates.
 *
 * Reads    GET  /api/derivatives?tab=otc|options|futures|servicing&book=   (every 30 s while shown; a page poller)
 *          GET  /api/otc/rfqs?status=all&book=                             (the quote requests block; every 2 s while one is open)
 *          GET  /api/otc/contracts/{id}/timeline                           (a contract's drawer)
 * Writes   POST /api/otc/rfqs/{id}/accept | decline, POST /api/otc/contracts/{id}/terminate (the swap ticket's dialogs),
 *          POST /api/otc/contracts/{id}/credit-event {reference_entity, recovery_pct, event_date}
 * Every number is the server's; nothing is priced here.  The page calls nothing while the OTC module is not bound (T.hasModule('swaps')).
 * ASCII; core.js, and components/swapticket.js for the shared pieces (it loads first).
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.pages = T.pages || {};

  var TABS = [{ key: 'otc', label: 'OTC contracts' }, { key: 'options', label: 'Listed options' }, { key: 'futures', label: 'Futures' },
              { key: 'servicing', label: 'Servicing' }];
  var REFRESH_MS = 30000;
  var CREDIT_LABEL = 'simulated credit event - recovery entered by the owner; no ISDA Determinations Committee ruling or auction in the simulator';
  var EVENT_WORDS = { PAY: 'payment', RESET: 'reset', FIX: 'fixing', MATURITY: 'maturity', FX_RESET: 'FX reset', EXPIRY: 'option expiry', ROLL_BY: 'roll or close by',
                      LAST_TRADE: 'last trading day', UNAVAILABLE: 'unavailable', START: 'start exchange', NOTIONAL_START: 'start exchange',
                      NOTIONAL_END: 'maturity exchange', DELIVERY: 'delivery', PREMIUM: 'premium settlement' };
  var cur = null;

  var cssDone = false;
  function ensureCss() {
    if (cssDone || document.getElementById('derivatives-css')) { cssDone = true; return; }
    cssDone = true;
    var s = document.createElement('style');
    s.id = 'derivatives-css';
    s.textContent = '.deriv .deriv-wrap { overflow-x: auto; max-width: 100%; }' +
      '.deriv .deriv-wrap > table { width: auto; min-width: 100%; }' +
      '.deriv table.deriv-table th, .deriv table.deriv-table td { padding: 0 6px; }' +
      '.deriv table.deriv-table td.wrap { white-space: normal; min-width: 150px; max-width: 280px; line-height: 1.3; padding-top: 2px; padding-bottom: 2px; }' +
      '.deriv table.deriv-table tr.detail > td { white-space: normal; text-align: left; padding: 6px 8px; }' +
      '.deriv table.deriv-table td.deriv-act .btn { display: block; margin: 2px 0; white-space: nowrap; }' +
      '.deriv table.deriv-table td.stack { padding-top: 3px; padding-bottom: 3px; }' +
      '.deriv table.deriv-table td .cl2 { color: var(--fg3); font-size: 10.5px; line-height: 1.25; }' +
      '.deriv table.deriv-table td .clw { white-space: normal; overflow-wrap: anywhere; line-height: 1.25; }' +
      '.deriv .deriv-note { margin: 4px 0 8px; }' +
      '.deriv .deriv-strategies { margin: 4px 0 10px; }' +
      '.deriv .warnline { color: var(--warn); }' +
      '.deriv h3 { font-size: 11px; margin: 10px 0 6px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; font-family: var(--mono); }' +
      '.deriv h3 small { color: var(--fg3); font-weight: 400; margin-left: 8px; text-transform: none; letter-spacing: 0; }';
    document.head.appendChild(s);
  }

  function sw() { return T.components.swapticket || null; }
  function money(row, key) { return T.money(row, key); }
  function num(row, key) { return T.num(row, key); }
  function describe(e) { return T.api && T.api.describe ? T.api.describe(e) : String(e && e.message || e); }
  function nyTime(epoch) { return epoch == null ? '' : T.fmt.time(epoch); }
  function acctName(id) { return T.accountName ? T.accountName(id) : id; }
  function signedCls(row, key) { return T.signCls ? T.signCls(num(row, key)) : ''; }
  function fmtNum(x, dp) { return x == null || x === '' ? 'n/a' : T.fmt.num(x, dp); }
  function wrap(tbl) { return T.h('div', { class: 'deriv-wrap' }, tbl); }
  function th(text, cls, title) { return T.h('th', { class: cls || 'l', title: title || '' }, text); }

  /* ---- OTC contracts ------------------------------------------------------------------------------------------------------- */
  function kindShort(c) { var s = sw(); return s && s.kindShort ? s.kindShort(c) : (c.kind_label || c.kind || ''); }
  function sideText(c) { var s = sw(); return s && s.sideText ? s.sideText(c.side, c) : (c.side_text || String(c.side || '').replace(/_/g, ' ').toLowerCase()); }
  /* P8R1-05 (rule 5): the contracts table fits 1440 px - its cells stack related figures (a TRS's units over its USD notional; the model mark
     over what NAV carries; the VM balance over the IM; the dealt term over the market now), and the status and account sit under the
     contract id, so every mark is on screen without a sideways scroll */
  function stack(first, second, title) {
    return T.h('div', null, T.h('div', { class: 'clw', style: 'max-width:150px; margin-left:auto', title: title || '' }, first), second ? T.h('div', { class: 'cl2' }, second) : null);
  }
  function notionalCell(c) {
    var p = c.position || {};
    if (c.kind === 'TRS' && p.units != null) { return stack(T.fmt.qty(p.units) + ' ' + (p.unit_label || 'reference shares'), money(c, 'notional') + ' USD'); }
    var fxd = c.kind === 'FX_FORWARD' ? (c.detail || {}) : null;
    if (fxd && fxd.pair && num(fxd, 'notional_base') != null) { return stack(String(fxd.pair).slice(0, 3) + ' ' + money(fxd, 'notional_base'), money(c, 'notional') + ' USD'); }
    return stack(money(c, 'notional') + ' ' + (c.notional_ccy || c.currency || 'USD'), null);
  }
  function dealtNowCell(c) {
    var t = dealtNow(c);
    var i = t.indexOf(' / ');
    return i > 0 ? stack(t.slice(0, i), t.slice(i + 3)) : stack(t, null);
  }
  /* PORTFOLIO-06: the dealt price, spread or rate against the market now (the C13 position fields; never priced here) */
  function dealtNow(c) {
    var f = c.position || null;
    if (!f || (f.entry == null && f.current == null)) { return ''; }
    var fx = sw() && sw().fmtPct ? sw().fmtPct : function (x) { return String(x); };
    if (c.kind === 'TRS') { return 'reset ' + (f.entry == null ? 'n/a' : T.fmt.px(f.entry)) + ' / now ' + (f.current == null ? 'not observed' : T.fmt.px(f.current)); }
    if (c.kind === 'IRS') { return 'fixed ' + (f.entry == null ? 'n/a' : fx(f.entry)) + ' / par now ' + (f.current == null ? 'n/a' : fx(f.current)); }
    if (c.kind === 'CDX') { return 'dealt ' + (f.entry || 'n/a') + ' / now ' + (f.current || 'not observed'); }
    return '';
  }
  function sensitivity(c) {
    if (c.kind === 'CDX') { var d = c.detail || {}; return d.cs01 == null && d.cs01_c == null ? 'CS01 n/a' : 'CS01 ' + money(d, 'cs01'); }
    return c.dv01 == null && c.dv01_c == null ? 'n/a' : 'DV01 ' + money(c, 'dv01');
  }
  function confirmChip(c) {
    if (!c.confirm_status) { return null; }
    var pend = c.confirm_status === 'PENDING_CONFIRM';
    return T.stageChip(pend ? 'queued' : 'filled', pend ? 'PENDING CONFIRMATION' : 'CONFIRMED', c.confirm_label || '');
  }
  function otcRow(st, body, c) {
    var open = !!st.open[c.contract_id];
    var mark = c.mark || {};
    var pf = c.position || null;
    var s = sw();
    var chips = T.h('div');
    var chip = confirmChip(c);
    if (chip) { chips.appendChild(chip); chips.appendChild(document.createTextNode(' ')); }
    chips.appendChild(T.stateTag ? T.stateTag(c.status) : T.h('span', null, c.status || ''));
    var badge = s && s.asofBadge ? s.asofBadge(c) : null;
    /* the actions come FIRST, one per line, so they stay on screen at 1440 px although the table is wider than the page (rule 5, the 1.0.6
       integrator's walk; as on the account Positions page) */
    var actions = T.h('td', { class: 'l deriv-act' });
    if (c.status === 'OPEN') {
      actions.appendChild(T.h('button', { class: 'btn small', title: 'terminate at the current model value with the dealer', onclick: function () {
        if (s && s.terminate) { s.terminate(c.contract_id, { account_id: c.account_id }); } } }, 'Terminate ...'));
      if (c.kind === 'CDX' && st.data && st.data.credit_event_offered) {
        actions.appendChild(T.h('button', { class: 'btn small', title: 'Simulate credit event: ' + CREDIT_LABEL, onclick: function () { creditDialog(st, c); } }, 'Credit event ...'));
      }
    }
    var markTitle = (mark.quality || '') + (mark.as_of ? ' as of ' + nyTime(mark.as_of) + ' ET' : '') + (mark.note ? ' . ' + mark.note : '');
    var navLine = pf ? 'in NAV ' + money(pf, 'mv') + ' (' + (pf.mark_label || '') + ')' : (c.status === 'OPEN' ? 'in NAV n/a' : '');
    var tr = T.h('tr', { class: c.status === 'OPEN' ? '' : 'dim' }, actions,
      T.h('td', { class: 'l stack' }, T.h('div', null, T.h('a', { href: '#', title: (open ? 'hide' : 'show') + ' the terms, the timeline and the history', onclick: function (e) {
        e.preventDefault(); st.open[c.contract_id] = !st.open[c.contract_id]; paint(st); } }, (open ? '- ' : '+ ') + c.contract_id), badge ? ' ' : '', badge), chips,
        T.h('div', { class: 'cl2' }, acctName(c.account_id))),
      T.h('td', { class: 'l stack' }, T.h('div', { class: 'clw', style: 'max-width:170px' }, T.h('b', null, kindShort(c)), ' ', sideText(c))),
      T.h('td', { class: 'num stack' }, notionalCell(c)),
      T.h('td', { class: 'l stack', title: ((c.position || {}).extra || {}).current_label || '' }, dealtNowCell(c)),
      T.h('td', { class: 'l' }, c.maturity_date || ''),
      T.h('td', { class: 'num stack ' + (T.signCls ? T.signCls(mark.value) : ''), title: markTitle + (pf ? ' . unrealised ' + money(pf, 'unrealised') : '') },
        T.h('div', null, mark.value == null ? 'n/a' : T.fmt.money(mark.value), ' ', s && s.modelBadge ? s.modelBadge() : null),
        navLine ? T.h('div', { class: 'cl2 clw', style: 'max-width:170px; margin-left:auto' }, navLine) : null),
      T.h('td', { class: 'num stack', title: (c.margin_mode || 'CTM') + ': + received from the dealer / - posted; IM = the initial margin posted' },
        stack(money(c, 'vm_balance'), 'IM ' + money(c, 'im_balance'))),
      T.h('td', { class: 'num' }, sensitivity(c)));
    body.appendChild(tr);
    if (open) { body.appendChild(T.h('tr', { class: 'detail' }, T.h('td', { colspan: '9' }, otcDrawer(st, c)))); }
  }
  function otcDrawer(st, c) {
    var s = sw();
    var box = T.h('div', { class: 'drawer' });
    var head = T.h('div', { class: 'drawer-head' }, T.h('h2', null, c.contract_id), T.h('span', { class: 'muted' }, kindShort(c) + ' . ' + sideText(c)), T.h('span', { class: 'spacer' }),
      T.h('a', { class: 'btn small', href: T.router.account(c.account_id, 'markets/otc'), title: 'the prime broker\'s OTC tab: dealing and the full history' }, 'OTC tab'));
    box.appendChild(head);
    var pf = c.position || {};
    var kv = [];
    if (c.source_label) { kv.push(['source', c.source_label]); }
    kv.push(['confirmation', c.confirm_label || c.confirm_note || 'n/a']);
    kv.push(['dealt', s && s.dealtText ? s.dealtText(c) : '']);
    kv.push(['dates', (c.trade_date ? 'traded ' + c.trade_date + ' . ' : '') + (c.start_date || '') + ' to ' + (c.maturity_date || '')]);
    var now = s && s.nowText ? s.nowText(c) : null;
    if (now) { kv.push(['now', now]); }
    if (c.curve_spread_text) { kv.push(['curve spread', c.curve_spread_text]); }
    if (c.position) { kv.push(['in NAV', money(pf, 'mv') + ' (' + (pf.mark_label || '') + ') . unrealised ' + money(pf, 'unrealised') + ' against the contract cost ' + money(pf, 'cost')]); }
    kv.push(['accrued', s && s.accruedText ? s.accruedText(c) : '']);
    kv.push(['next', s && s.eventsText ? (s.eventsText(c.next_events) || 'none') : '']);
    kv.push(['result to date', 'swap result ' + money(c, 'result') + ' . financing ' + money(c, 'financing') + ' . coupons / premium ' + money(c, 'coupons') +
      ' . dividend equivalents ' + money(c, 'dividends') + ' (financing and coupons: + paid / - received)']);
    box.appendChild(T.kvGrid(kv));
    var d = c.detail || {};
    if (c.kind === 'CDX') {
      var ev = d.credit_events || [];
      var cb = T.h('div', { class: 'section' }, T.h('h3', null, 'Credit events', T.h('small', null, ' index factor ' + (d.index_factor || '1.000000') + ' of ' +
        money(d, 'original_notional') + ' . ' + (d.names || '') + ' names, equal weights (assumed)')));
      if (!ev.length) { cb.appendChild(T.h('div', { class: 'empty' }, 'none recorded: real credit events are not tracked; the owner can simulate one from this row')); }
      else {
        var t = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, th('event date'), th('reference entity'), th('recovery', 'num'),
          th('defaulted', 'num'), th('protection', 'num'), th('accrued premium', 'num'), th('net', 'num'), th('settles'), th('notional after', 'num'))));
        var b = T.h('tbody');
        ev.forEach(function (x) {
          b.appendChild(T.h('tr', null, T.h('td', null, x.event_date), T.h('td', { class: 'l' }, x.reference_entity), T.h('td', { class: 'num' }, x.recovery_pct + ' %'),
            T.h('td', { class: 'num' }, money(x, 'defaulted')), T.h('td', { class: 'num' }, money(x, 'protection')), T.h('td', { class: 'num' }, money(x, 'accrued')),
            T.h('td', { class: 'num' }, money(x, 'net')), T.h('td', null, x.settle_date), T.h('td', { class: 'num' }, money(x, 'notional_after'))));
        });
        t.appendChild(b);
        cb.appendChild(wrap(t));
        cb.appendChild(T.h('div', { class: 'hint' }, CREDIT_LABEL));
      }
      box.appendChild(cb);
    }
    var tl = T.h('div', { class: 'section' }, T.h('h3', null, 'Timeline'));
    var slot = T.h('div', { class: 'empty' }, 'loading ...');
    tl.appendChild(slot);
    box.appendChild(tl);
    var cached = st.timelines[c.contract_id];
    function show(rows) { if (slot.parentNode) { slot.parentNode.replaceChild(s && s.timelineTable ? s.timelineTable(rows) : T.h('div', null, rows.length + ' steps'), slot); } }
    if (cached && Date.now() - cached.at < REFRESH_MS) { show(cached.rows); }
    else {
      T.api.get('/api/otc/contracts/' + encodeURIComponent(c.contract_id) + '/timeline').then(function (r) {
        var rows = (r && r.timeline) || [];
        st.timelines[c.contract_id] = { rows: rows, at: Date.now() };
        show(rows);
      }, function (e) { slot.textContent = 'the timeline could not be loaded: ' + describe(e); });
    }
    return box;
  }
  function paintOtc(st) {
    var d = st.data || {};
    var c = st.content;
    T.clear(c);
    var rows = d.contracts || [];
    var openN = rows.filter(function (x) { return x.status === 'OPEN'; }).length;
    c.appendChild(T.h('h2', null, 'OTC contracts', T.h('small', null, ' ' + openN + ' open, ' + (rows.length - openN) + ' closed . book-wide; dealing on a prime broker\'s OTC tab')));
    c.appendChild(T.h('div', { class: 'hint deriv-note' }, d.note || ''));
    if (!rows.length) { c.appendChild(T.h('div', { class: 'empty' }, 'no OTC contracts in this book yet - they are dealt on a prime-broker account\'s OTC tab (F6)')); return; }
    var tbl = T.h('table', { class: 'grid deriv-table' }, T.h('thead', null, T.h('tr', null, th('act', 'l', 'terminate the contract; on a CDX, simulate a credit event'),
      th('contract / status / account'), th('kind / side'), th('notional', 'num'), th('dealt / now', 'l', 'the dealt reset price, spread or fixed rate against the market now'),
      th('maturity'), th('model mark / in NAV', 'num', 'a valuation from observed inputs - never a dealer-confirmed price; below it, what NAV carries now: the intraday' +
        ' model value, else the mark of the last variation margin'), th('VM balance / IM', 'num', 'VM: + received / - posted; the initial margin posted below'),
      th('DV01 / CS01', 'num'))));
    var body = T.h('tbody');
    rows.forEach(function (x) { otcRow(st, body, x); });
    tbl.appendChild(body);
    c.appendChild(wrap(tbl));
    var recent = d.recent_rfqs || [];
    if (recent.length) {
      var rt = T.h('table', { class: 'grid small' }, T.h('thead', null, T.h('tr', null, th('request'), th('contract'), th('account'), th('outcome'), th('when'))));
      var rb = T.h('tbody');
      recent.slice(0, 10).forEach(function (r) {
        rb.appendChild(T.h('tr', null, T.h('td', null, r.rfq_id), T.h('td', { class: 'l' }, (r.label || '') + ' . ' + (r.side_words || '')), T.h('td', null, acctName(r.account_id)),
          T.h('td', { class: 'l', style: 'white-space:normal' }, r.status === 'ACCEPTED' ? 'booked ' + (r.contract_id || '') : (r.status_words || '') + (r.reason ? ': ' + r.reason : '')),
          T.h('td', null, r.closed_ts ? T.fmt.dt(r.closed_ts) : '')));
      });
      rt.appendChild(rb);
      c.appendChild(T.h('div', { class: 'section' }, T.h('h3', null, 'Recent quote requests', T.h('small', null, ' accepted, declined or expired')), wrap(rt)));
    }
  }
  /* the owner-triggered credit event of one name of a CDX index: the server computes every figure; the dialog states the rules */
  function creditDialog(st, c) {
    var d = c.detail || {};
    var names = d.names || (d.index === 'HY' ? 100 : 125);
    var today = T.fmt.date(T.clock && T.clock.nowInt ? T.clock.nowInt() : Math.floor(Date.now() / 1000));
    var ent = T.h('input', { type: 'text', spellcheck: 'false', autocomplete: 'off', placeholder: 'the defaulted constituent', style: 'width:220px' });
    var rec = T.h('input', { type: 'text', spellcheck: 'false', autocomplete: 'off', value: '40', style: 'width:70px' });
    var dt = T.h('input', { type: 'date', value: today, max: today });
    var err = T.h('div', { class: 'error', hidden: true });
    var body = T.h('div', null,
      T.h('div', { class: 'hint' }, CREDIT_LABEL + '. Real credit events are not tracked; this simulates one constituent of ' + (d.index_name || 'the index') + ' defaulting.'),
      T.form([T.field('reference entity', ent, 'the name of the defaulted constituent (one event per name)'), T.field('recovery (%)', rec, 'the recovery you enter, 0 to 100'),
              T.field('event date', dt, 'on or before today, in the current coupon period')]),
      T.kv([['weights', names + ' names, equal weights (assumed): the defaulted notional = the original notional ' + money(d, 'original_notional') + ' / ' + names],
            ['settlement', 'protection = the defaulted notional x (1 - recovery), ' + (c.side === 'BUY_PROTECTION' ? 'received by you (protection buyer)' : 'paid by you (protection seller)') +
             ', net of the accrued premium on the defaulted notional from the period start through the event date; it settles the next business day'],
            ['afterwards', 'the notional falls by the defaulted notional: the premium and the IM follow it from the next night (the IM released returns to cash),' +
             ' the contract cost is released pro rata to the swap result and the next variation margin values the reduced contract']]),
      err);
    T.ui.modal({
      title: 'Simulate a credit event on ' + c.contract_id, sticky: true, body: body,
      actions: [{ label: 'Cancel' }, { label: 'Simulate credit event', cls: 'primary', onClick: function (m, btn) {
        btn.disabled = true; err.hidden = true;
        T.api.post('/api/otc/contracts/' + encodeURIComponent(c.contract_id) + '/credit-event', { reference_entity: ent.value, recovery_pct: rec.value, event_date: dt.value })
          .then(function (r) {
            m.close();
            var text = 'Simulated credit event on ' + c.contract_id + ': ' + (r.reference_entity || '') + ' at ' + r.recovery_pct + ' % recovery . defaulted ' + money(r, 'defaulted') +
              ' . protection ' + money(r, 'protection') + ' . accrued premium ' + money(r, 'accrued') + ' . net ' + money(r, 'net') + ' settling ' + (r.settle_date || '') +
              ' . notional now ' + money(r, 'notional_after');
            T.ui.toast(text, 'ok', 10000);
            st.timelines = {};
            if (T.poll && T.poll.kick) { ['accounts', 'positions', 'account_detail', 'books', 'attention'].forEach(function (n) { T.poll.kick(n); }); }
            load(st);
          }, function (e) { btn.disabled = false; err.textContent = describe(e); err.title = (e && e.code) || ''; err.hidden = false; });
      } }]
    });
  }

  /* ---- listed options ------------------------------------------------------------------------------------------------------ */
  function paintOptions(st) {
    var d = st.data || {};
    var c = st.content;
    T.clear(c);
    c.appendChild(T.h('h2', null, 'Listed options', T.h('small', null, ' positions with their greeks; exercise and expiry on the expiry date')));
    if (d.available === false) { c.appendChild(T.h('div', { class: 'empty' }, 'unavailable: ' + (d.reason || 'no data'))); return; }
    var strategies = d.strategies || [];
    if (strategies.length) {
      var sb = T.h('div', { class: 'deriv-strategies' }, T.h('h3', null, 'Strategies', T.h('small', null, ' each leg stays its own position; the package is named')));
      strategies.forEach(function (s) {
        sb.appendChild(T.h('div', null, T.h('b', null, s.strategy_words || s.strategy), ' (' + s.package_id + ', ' + acctName(s.account_id) + '): ' +
          (s.legs || []).map(function (l) { return (l.side || '') + ' ' + l.qty + ' ' + l.instrument_id + (l.price ? ' @ ' + l.price : ''); }).join(' / ')));
      });
      c.appendChild(sb);
    }
    var rows = d.rows || [];
    if (!rows.length) { c.appendChild(T.h('div', { class: 'empty' }, 'no listed option positions in this book')); return; }
    var tbl = T.h('table', { class: 'grid deriv-table' }, T.h('thead', null, T.h('tr', null, th('account'), th('option'), th('underlying'), th('right'), th('strike', 'num'),
      th('expiry'), th('contracts', 'num'), th('mark', 'num'), th('market value', 'num'), th('delta', 'num', 'per contract'), th('gamma', 'num'), th('vega', 'num', 'per vol point'),
      th('theta', 'num', 'per day'), th('greeks'), th('strategy'))));
    var body = T.h('tbody');
    rows.forEach(function (r) {
      var g = r.greeks || {};
      body.appendChild(T.h('tr', null, T.h('td', { class: 'l' }, r.account_name || acctName(r.account_id)),
        T.h('td', { class: 'l' }, T.h('a', { href: T.router.instrument(r.account_id, r.instrument_id), title: 'the instrument page' }, r.symbol || r.instrument_id)),
        T.h('td', { class: 'l' }, (r.underlying_id || '') + (r.underlying_mark ? ' ' + T.fmt.px(r.underlying_mark) : '')),
        T.h('td', null, r.right === 'C' ? 'call' : (r.right === 'P' ? 'put' : (r.right || ''))), T.h('td', { class: 'num' }, r.strike == null ? 'n/a' : T.fmt.px(r.strike)),
        T.h('td', null, r.expiry || ''), T.h('td', { class: 'num' }, T.fmt.qty(r.qty)), T.h('td', { class: 'num', title: r.quality || '' }, r.mark == null ? 'n/a' : T.fmt.px(r.mark)),
        T.h('td', { class: 'num ' + signedCls(r, 'mv') }, money(r, 'mv')),
        T.h('td', { class: 'num' }, fmtNum(g.delta, 4)), T.h('td', { class: 'num' }, fmtNum(g.gamma, 4)), T.h('td', { class: 'num' }, fmtNum(g.vega, 4)),
        T.h('td', { class: 'num' }, fmtNum(g.theta, 4)), T.h('td', { class: 'l', title: r.greeks_note || '' }, r.greeks ? (r.greeks_source || '') : 'unavailable'),
        T.h('td', { class: 'l' }, r.strategy || '')));
    });
    tbl.appendChild(body);
    c.appendChild(wrap(tbl));
    c.appendChild(T.h('div', { class: 'hint deriv-note' }, d.note || ''));
  }

  /* ---- futures --------------------------------------------------------------------------------------------------------------- */
  function paintFutures(st) {
    var d = st.data || {};
    var c = st.content;
    T.clear(c);
    c.appendChild(T.h('h2', null, 'Futures', T.h('small', null, ' initial margin, daily variation margin, open trade equity, delivery')));
    if (d.available === false) { c.appendChild(T.h('div', { class: 'empty' }, 'unavailable: ' + (d.reason || 'no data'))); return; }
    var rows = d.rows || [];
    if (!rows.length) { c.appendChild(T.h('div', { class: 'empty' }, 'no futures positions in this book')); return; }
    var soon = (T.clock && T.clock.nowInt ? T.clock.nowInt() : Math.floor(Date.now() / 1000)) + 30 * 86400;
    var tbl = T.h('table', { class: 'grid deriv-table' }, T.h('thead', null, T.h('tr', null, th('account'), th('contract'), th('contracts', 'num'), th('avg entry', 'num'),
      th('mark', 'num'), th('open trade equity', 'num', '(mark - average entry) x contracts x multiplier'), th('last settle'), th('daily VM', 'num', 'the last settle\'s variation margin'),
      th('IM posted', 'num'), th('delivery'))));
    var body = T.h('tbody');
    rows.forEach(function (r) {
      var ls = r.last_settle || null;
      var dv = r.delivery || {};
      var actBy = dv.act_by ? Date.parse(dv.act_by + 'T12:00:00Z') / 1000 : null;
      body.appendChild(T.h('tr', null, T.h('td', { class: 'l' }, r.account_name || acctName(r.account_id)),
        T.h('td', { class: 'l', title: r.name || '' }, T.h('a', { href: T.router.instrument(r.account_id, r.instrument_id) }, r.symbol || r.instrument_id)),
        T.h('td', { class: 'num' }, T.fmt.qty(r.qty)), T.h('td', { class: 'num' }, r.avg_px == null ? 'n/a' : T.fmt.px(r.avg_px)),
        T.h('td', { class: 'num', title: r.quality || '' }, r.mark == null ? 'n/a' : T.fmt.px(r.mark)),
        T.h('td', { class: 'num ' + signedCls(r, 'ote') }, money(r, 'ote')),
        T.h('td', { class: 'l' }, ls ? ls.date + ' @ ' + T.fmt.px(ls.price) : 'none yet'),
        T.h('td', { class: 'num ' + (ls ? signedCls(ls, 'vm') : '') }, ls ? money(ls, 'vm') : ''),
        T.h('td', { class: 'num' }, money(r, 'im')),
        T.h('td', { class: 'l wrap' + (dv.rule === 'physical' && actBy && actBy < soon ? ' warnline' : '') }, dv.words || '')));
    });
    tbl.appendChild(body);
    c.appendChild(wrap(tbl));
    c.appendChild(T.h('div', { class: 'hint deriv-note' }, d.note || ''));
  }

  /* ---- servicing --------------------------------------------------------------------------------------------------------------- */
  function paintServicing(st) {
    var d = st.data || {};
    var c = st.content;
    T.clear(c);
    c.appendChild(T.h('h2', null, 'Servicing', T.h('small', null, ' what is coming, from ' + (d.as_of || 'today'))));
    var rows = d.rows || [];
    if (!rows.length) { c.appendChild(T.h('div', { class: 'empty' }, 'nothing scheduled: no open derivatives in this book')); return; }
    var tbl = T.h('table', { class: 'grid deriv-table' }, T.h('thead', null, T.h('tr', null, th('date'), th('event'), th('kind'), th('what'), th('account'), th('detail'),
      th('amount', 'num', '+ received / - paid; blank while not known'))));
    var body = T.h('tbody');
    rows.forEach(function (r) {
      body.appendChild(T.h('tr', null, T.h('td', null, r.date || 'n/a'), T.h('td', null, EVENT_WORDS[r.event] || String(r.event || '').toLowerCase()), T.h('td', null, r.kind || ''),
        T.h('td', { class: 'l wrap' }, r.what || ''), T.h('td', { class: 'l' }, r.account_name || acctName(r.account_id)), T.h('td', { class: 'l wrap' }, r.detail || ''),
        T.h('td', { class: 'num ' + signedCls(r, 'amount') }, r.amount == null && r.amount_c == null ? '' : money(r, 'amount'))));
    });
    tbl.appendChild(body);
    c.appendChild(wrap(tbl));
    c.appendChild(T.h('div', { class: 'hint deriv-note' }, d.note || ''));
  }

  /* ---- the page ------------------------------------------------------------------------------------------------------------------ */
  function paint(st) {
    if (st.dead) { return; }
    if (st.error && !st.data) { T.clear(st.content).appendChild(T.h('div', { class: 'error' }, 'the ' + st.tab + ' tab could not be loaded: ' + st.error + ' - retrying')); return; }
    if (!st.data) { return; }
    ({ otc: paintOtc, options: paintOptions, futures: paintFutures, servicing: paintServicing })[st.tab](st);
    if (st.error) { st.content.insertBefore(T.h('div', { class: 'st-meta warn' }, 'the last refresh failed (showing the previous answer): ' + st.error), st.content.firstChild); }
  }
  function load(st) {
    if (st.dead) { return Promise.resolve(); }
    return T.api.get('/api/derivatives', { tab: st.tab }).then(function (d) {
      if (st.dead) { return; }
      st.data = d; st.error = null;
      paint(st);
    }, function (e) {
      if (st.dead) { return; }
      st.error = describe(e);
      paint(st);
      throw e;
    });
  }
  function render(el, route) {
    unmount();
    ensureCss();
    var tab = (route && route.tab) || 'otc';
    var st = cur = { dead: false, tab: tab, data: null, error: null, open: {}, timelines: {}, rfqH: null };
    var root = T.h('div', { class: 'deriv' });
    el.appendChild(root);
    var book = (T.book && T.book.current && T.book.current()) || {};
    T.pageHead(root, 'Derivatives', 'OTC contracts, listed options and futures' + (book.name ? ' of ' + book.name : '') + ': marks, margin, servicing');
    root.appendChild(T.tabs(TABS, tab, function (k) { if (k !== st.tab) { T.router.go('#/derivatives/' + k); } }));
    if (!T.hasModule || !T.hasModule('swaps')) {
      root.appendChild(T.h('div', { class: 'empty' }, 'unavailable: the OTC and derivatives module is not running in this build'));
      return;
    }
    var s = sw();
    if (tab === 'otc') {
      var rfqBox = T.h('div');
      root.appendChild(rfqBox);
      if (s && s.mountRfqs) { st.rfqH = s.mountRfqs(rfqBox, { showAccount: true, hideEmpty: true, onBooked: function () { load(st); } }); }
    }
    st.content = T.h('div', { class: 'section' }, T.h('div', { class: 'empty' }, 'loading ...'));
    root.appendChild(st.content);
    T.poll.page('derivatives', REFRESH_MS, function () { return load(st); });
  }
  function unmount() {
    if (!cur) { return; }
    cur.dead = true;
    if (cur.rfqH && cur.rfqH.unmount) { try { cur.rfqH.unmount(); } catch (e) { console.error('derivatives rfqs', e); } }
    cur = null;
  }

  T.pages.derivatives = { render: render, unmount: unmount, tabs: TABS };
})();
