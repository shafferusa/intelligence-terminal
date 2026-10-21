/* Treasury (TERMINAL_DESIGN.md s17.1 / s6.7 #/treasury; WP-12): the selected book's Treasury - capital (Add funds /
 * Withdraw, one small modal each -> POST /api/treasury/deposit | withdraw), cash by currency, advances by account
 * (click -> the account), cash interest, the sweep log, a manual transfer (POST /api/transfers), the reserve
 * (PATCH /api/settings treasury_reserve).  GET /api/treasury = TreasuryView + advances + capital history + sweep log;
 * the identity NAV = capital + retained - advances is the server's `identity_ok`.  Nothing is computed here.
 * Units (2026-10-21 fix): cash_by_ccy / capital_by_ccy / advances_by_account are decimal strings in major units, like
 * nav / capital / available (they were integer cents: "USD 100,000,000.00" for a 1,000,000.00 Treasury).  The interest
 * block carries SOFR as published (index_pct, a percent -> T.fmt.ratePct) and the accrual rate, spread and floor as
 * fractions (-> T.fmt.rate): "SOFR 3.87 % - 0.50 % = 3.37 %" (it showed "387.00 %").
 */
(function () {
  'use strict';
  var T = window.T;
  var CCYS = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD'];
  /* "SOFR 3.87 % - 0.50 % = 3.37 % (as of 2026-10-19; floor 0.00 %; ACT/360) . accrued 12.50": the index as published
     (a percent), the spread and the accrual rate (fractions), from GET /api/treasury `interest`; an older payload with
     only `rate` (a fraction) shows that rate. */
  function interestText(i) {
    if (!i) { return 'n/a'; }
    var idx = i.index || 'SOFR';
    if (i.rate == null) { return idx + ': ' + (i.status || 'no print loaded (never defaulted)') + ' . accrued ' + T.money(i, 'accrued'); }
    var s = idx + ' ';
    if (i.index_pct != null) {
      var sp = String(i.spread == null ? '' : i.spread);
      var neg = sp.charAt(0) === '-';
      s += T.fmt.ratePct(i.index_pct) + (sp ? (neg ? ' - ' : ' + ') + T.fmt.rate(neg ? sp.slice(1) : sp) : '') + ' = ' + T.fmt.rate(i.rate);
    } else {
      s += T.fmt.rate(i.rate);
    }
    return s + ' (as of ' + (i.as_of || '?') + (i.floor != null ? '; floor ' + T.fmt.rate(i.floor) : '') + (i.basis ? '; ACT/' + i.basis : '') + ') . accrued ' + T.money(i, 'accrued');
  }
  function render(el) {
    var data = null;
    var body = T.h('div');
    T.pageHead(el, 'Treasury', T.book.current() ? (T.book.current().name || '') : '', [
      T.h('button', { class: 'btn primary', onclick: function () { fundsModal('deposit'); } }, 'Add funds'),
      T.h('button', { class: 'btn', onclick: function () { fundsModal('withdraw'); } }, 'Withdraw'),
      T.h('button', { class: 'btn', onclick: transferModal }, 'Transfer')]);
    el.appendChild(body);
    function load() { return T.api.get('/api/treasury').then(function (d) { data = d; paint(); }, function (e) { T.clear(body); T.errorBox(body, e, 'treasury'); }); }
    function paint() {
      T.clear(body);
      var t = data.treasury || data;
      body.appendChild(T.tiles([
        { k: 'Treasury NAV', v: T.money(t, 'nav'), s: t.identity_ok === false ? 'IDENTITY BROKEN (L8)' : 'NAV = capital + retained - advances' + (t.identity_ok ? ' (ties)' : ''), cls: t.identity_ok === false ? 'neg' : '', title: (t.formulas && t.formulas.nav) || '' },
        { k: 'contributed capital', v: T.money(t, 'capital'), s: 'posted once + deposits - withdrawals (never P&L)' },
        { k: 'advances to accounts', v: T.money(t, 'advances'), s: 'internal transfers; net to zero across the lifetime' },
        { k: 'retained results', v: T.money(t, 'retained'), s: 'Treasury\'s own income (cash interest)', cls: T.sign(t, 'retained') },
        { k: 'available USD', v: T.money(t, 'available'), s: 'settled USD - reserve ' + T.money(t, 'reserve'), cls: 'pos' },
        { k: 'encumbrance', v: T.money(t, 'encumbrance'), s: 'Treasury holds no positions in this build' }]));
      var two = T.h('div', { class: 'grid2' }); body.appendChild(two);
      var cash = T.h('div', { class: 'panel' }, T.h('h2', null, 'cash by currency'));
      var cb = t.cash_by_ccy || {};
      var rows = Object.keys(cb).map(function (c) { return { currency: c, settled: cb[c] }; });     /* {ccy: "1000000.00"}: major units */
      if (rows.length) { var g = T.h('div'); cash.appendChild(g); T.components.grid.mount(g, { tools: false, rows: rows, columns: [{ key: 'currency', label: 'ccy', cls: 'l' }, { key: 'settled', label: 'settled', text: function (r) { return T.money(r, 'settled'); } }] }); }
      else { cash.appendChild(T.h('div', { class: 'empty' }, 'no cash')); }
      var it = data.interest || null;
      cash.appendChild(T.kv([['accrued interest', T.money(t, 'accrued')], ['interest', interestText(it), it ? [it.formula, it.units, it.note].filter(Boolean).join('\n') : '']]));
      cash.appendChild(T.convNote(['TREASURY', 'CASH_INT']));
      two.appendChild(cash);
      var adv = T.h('div', { class: 'panel' }, T.h('h2', null, 'advances by account', T.h('small', null, 'click -> account')));
      var arows = Array.isArray(data.advances) ? data.advances : Object.keys(t.advances_by_account || {}).map(function (a) { return { account_id: a, amount: t.advances_by_account[a] }; });
      if (arows.length) { var ag = T.h('div'); adv.appendChild(ag); T.components.grid.mount(ag, { tools: false, rows: arows, columns: [{ key: 'account_id', label: 'account', cls: 'l', text: function (r) { return r.name || r.account_id; } }, { key: 'amount', label: 'advanced', text: function (r) { return T.money(r, 'amount'); } }], onEnter: function (r) { T.router.go(T.router.account(r.account_id, 'cash')); }, onClick: function (r) { T.router.go(T.router.account(r.account_id, 'cash')); } }); }
      else { adv.appendChild(T.h('div', { class: 'empty' }, 'no advances')); }
      two.appendChild(adv);
      var hist = T.h('div', { class: 'panel' }, T.h('h2', null, 'capital history'));
      var hrows = data.capital_history || [];
      if (hrows.length) { var hg = T.h('div'); hist.appendChild(hg); T.components.grid.mount(hg, { tools: false, rows: hrows, columns: [{ key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); } }, { key: 'kind', label: 'kind', cls: 'l' }, { key: 'currency', label: 'ccy', cls: 'l' }, { key: 'amount', label: 'amount', html: function (r) { return '<span class="' + T.sign(r, 'amount') + '">' + T.esc(T.money(r, 'amount')) + '</span>'; } }, { key: 'memo', label: 'memo', cls: 'l', text: function (r) { return r.memo || ''; } }, { key: 'request_id', label: 'request', cls: 'l', text: function (r) { return r.request_id || ''; } }] }); }
      else { hist.appendChild(T.h('div', { class: 'empty' }, 'no capital movements')); }
      body.appendChild(hist);
      var sl = T.h('div', { class: 'panel' }, T.h('h2', null, 'sweep log'));
      var srows = data.sweep_log || [];
      if (srows.length) { var sg = T.h('div'); sl.appendChild(sg); T.components.grid.mount(sg, { tools: false, rows: srows, columns: [{ key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); } }, { key: 'account_id', label: 'account', cls: 'l' }, { key: 'direction', label: 'direction', cls: 'l', text: function (r) { return r.direction || r.kind || ''; } }, { key: 'currency', label: 'ccy', cls: 'l' }, { key: 'amount', label: 'amount', text: function (r) { return T.money(r, 'amount'); } }, { key: 'reason', label: 'reason', cls: 'l', text: function (r) { return r.reason || r.memo || ''; } }] }); }
      else { sl.appendChild(T.h('div', { class: 'empty' }, 'no sweeps yet')); }
      sl.appendChild(T.convNote(['SWEEP']));
      body.appendChild(sl);
      var res = T.h('input', { type: 'text', value: T.money(t, 'reserve') === 'n/a' ? '' : T.money(t, 'reserve').replace(/,/g, ''), style: 'width:160px' });
      var saveRes = T.h('button', { class: 'btn small', onclick: function () { T.api.patch('/api/settings', { treasury_reserve: res.value.trim() || '0' }).then(function () { T.ui.toast('reserve saved', 'ok'); load(); }, function (e) { T.api.fail(e, 'reserve'); }); } }, 'Save');
      body.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'reserve', T.h('small', null, 'USD kept out of sweeps and buying power')), T.form([T.field('reserve (USD)', res), ['', saveRes]])));
    }
    function fundsModal(kind) {
      var ccy = T.h('select'); CCYS.forEach(function (c) { ccy.appendChild(T.h('option', { value: c }, c)); });
      var amt = T.h('input', { type: 'text', placeholder: 'amount', id: 'funds-amount' }); var memo = T.h('input', { type: 'text', placeholder: 'memo (optional)' });
      T.ui.modal({ title: kind === 'deposit' ? 'Add funds . ' + (T.book.current() ? T.book.current().name : '') : 'Withdraw', body: T.h('div', null, T.form([T.field('currency', ccy), T.field('amount', amt), T.field('memo', memo)]), T.h('div', { class: 'hint' }, kind === 'deposit' ? 'A capital deposit: it is never P&L and is the only way new money enters the lifetime.' : 'A capital withdrawal from settled Treasury cash (409 when insufficient).')),
        actions: [{ label: 'Cancel' }, { label: kind === 'deposit' ? 'Add' : 'Withdraw', cls: 'primary', onClick: function (m, b) {
          b.disabled = true;
          T.api.post('/api/treasury/' + kind, { currency: ccy.value, amount: amt.value.trim(), memo: memo.value.trim() || null, book: T.book.code }).then(function (r) { m.close(); T.ui.toast(kind + ' recorded: ' + (r.transfer && r.transfer.transfer_id || ''), 'ok'); load(); T.poll.kick('books'); T.poll.kick('accounts'); }, function (e) { b.disabled = false; T.api.fail(e, kind); });
        } }] });
    }
    function transferModal() {
      var accts = (T.state.accounts || []);
      var from = T.h('select'), to = T.h('select');
      accts.forEach(function (a) { from.appendChild(T.h('option', { value: a.account_id, selected: a.kind === 'TREASURY' }, a.name || a.account_id)); to.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); });
      var ccy = T.h('select'); CCYS.forEach(function (c) { ccy.appendChild(T.h('option', { value: c }, c)); });
      var amt = T.h('input', { type: 'text', placeholder: 'amount' }); var memo = T.h('input', { type: 'text', placeholder: 'memo' });
      T.ui.modal({ title: 'Internal transfer', body: T.h('div', null, T.form([T.field('from', from), T.field('to', to), T.field('currency', ccy), T.field('amount', amt), T.field('memo', memo)]), T.h('div', { class: 'hint' }, 'Within this book only (books never transfer to each other).')),
        actions: [{ label: 'Cancel' }, { label: 'Transfer', cls: 'primary', onClick: function (m, b) {
          b.disabled = true;
          T.api.post('/api/transfers', { from_account: from.value, to_account: to.value, currency: ccy.value, amount: amt.value.trim(), memo: memo.value.trim() || null }).then(function () { m.close(); T.ui.toast('transfer done', 'ok'); load(); T.poll.kick('accounts'); T.poll.kick('account_detail'); }, function (e) { b.disabled = false; T.api.fail(e, 'transfer'); });
        } }] });
    }
    load();
    T.poll.page('treasury', 20000, function () { return data ? load() : Promise.resolve(); });
  }
  T.pages.treasury = { render: render, interestText: interestText };
})();
