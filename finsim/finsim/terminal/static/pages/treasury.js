/* Treasury (TERMINAL_DESIGN.md s17.1 / s6.7 #/treasury; WP-12; Build 2 P03): the selected book's Treasury - capital (Add
 * funds / Withdraw: one small dialog each, then a confirmation naming the amount and the book -> POST /api/treasury/deposit |
 * withdraw), cash by currency, the advances to the accounts (click -> the account), cash interest, the sweep log (each
 * sweep-in says what it paid for and links to it), a manual transfer (POST /api/transfers; From = Treasury, To = the first
 * trading account or the one open in the header), the book's reserve (PATCH /api/settings {treasury_reserve, book}).
 * GET /api/treasury = TreasuryView + advances + capital history + sweep log + the shown figures (advances_view,
 * available_view); the identity is the server's.  Nothing is computed here.
 * Units: money fields are decimal strings in major units; SOFR as published is a percent (T.fmt.ratePct(i.index_pct)), the
 * accrual rate, spread and floor are fractions (T.fmt.rate): "SOFR 3.87 % - 0.50 % = 3.37 %".
 * Amounts typed by the owner (BM-21): digits with at most two decimals - '1e6', '1,000', '100.005' and signs are refused here
 * with a sentence before anything is sent (the server refuses them too); Enter in a dialog submits it.
 */
(function () {
  'use strict';
  var T = window.T;
  var CCYS = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD'];
  var AMOUNT_RE = /^[0-9]+(\.[0-9]{1,2})?$/;
  /* "SOFR 3.87 % - 0.50 % = 3.37 % (as of 2026-10-19; floor 0.00 %; ACT/360) . accrued 12.50" */
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
  /* a typed amount: null when it is a plain amount, else the sentence that says why not (BM-21) */
  function amountProblem(s) {
    s = String(s == null ? '' : s).trim();
    if (!s) { return 'type an amount, e.g. 1000000.00'; }
    if (/e/i.test(s)) { return s + ': write the amount in full digits (no exponent such as 1e6)'; }
    if (/[,\s_]/.test(s)) { return s + ': digits and one decimal point only (no thousands separators)'; }
    if (/^[+-]/.test(s)) { return s + ': a positive amount without a sign'; }
    if (/\.[0-9]{3,}$/.test(s)) { return s + ': at most 2 decimals (cents)'; }
    if (!AMOUNT_RE.test(s)) { return s + ' is not an amount: digits with at most 2 decimals, e.g. 1000000.00'; }
    if (Number(s) <= 0) { return 'the amount must be more than 0.00'; }
    return null;
  }
  /* submit a dialog with Enter from any of its inputs (BM-21) */
  function enterSubmits(inputs, fn) { inputs.forEach(function (i) { i.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); fn(); } }); }); }
  function bookName() { return (T.book.current() || {}).name || 'this book'; }
  function render(el) {
    var data = null, dead = false;
    var body = T.h('div');
    T.pageHead(el, 'Treasury', bookName(), [
      T.h('button', { class: 'btn primary', onclick: function () { fundsModal('deposit'); } }, 'Add funds'),
      T.h('button', { class: 'btn', onclick: function () { fundsModal('withdraw'); } }, 'Withdraw'),
      T.h('button', { class: 'btn', onclick: transferModal }, 'Transfer')]);
    el.appendChild(body);
    function load() {
      if (dead) { return Promise.resolve(); }
      return T.api.get('/api/treasury').then(function (d) { if (!dead) { data = d; paint(); } }, function (e) { if (!data) { T.clear(body); T.errorBox(body, e, 'treasury'); } throw e; });
    }
    function reasonHtml(r) {
      var l = r.link;
      var txt = r.reason || r.memo || '';
      if (l && l.account_id && l.kind === 'fill') {
        return '<a href="' + T.esc(T.router.account(l.account_id, 'orders')) + '" title="the fill is in the Orders page of ' + T.esc(T.accountName(l.account_id)) + (l.order_id ? ' (order ' + T.esc(l.order_id) + ')' : '') + '">' + T.esc(txt) + '</a>';
      }
      if (l && l.account_id) { return '<a href="' + T.esc(T.router.account(l.account_id, l.kind === 'settlement' ? 'cash' : 'transactions')) + '">' + T.esc(txt) + '</a>'; }
      return T.esc(txt);
    }
    function paint() {
      T.clear(body);
      var t = data.treasury || data;
      var av = data.available_view || {};
      var avN = av.signed != null || av.signed_c != null ? T.num(av, 'signed') : T.num(t, 'available');
      var avShown = av.shown != null || av.shown_c != null ? T.money(av, 'shown') : (avN != null && avN < 0 ? '0.00' : T.money(t, 'available'));
      var adv = data.advances_view || {};
      var identity = adv.identity_text || 'NAV = capital + retained - advanced to accounts';
      body.appendChild(T.tiles([
        { k: 'Treasury NAV', v: T.money(t, 'nav'), s: t.identity_ok === false ? 'IDENTITY BROKEN (L8)' : identity + (t.identity_ok ? ' (ties)' : ''), cls: t.identity_ok === false ? 'neg' : '', title: (t.formulas && (t.formulas.identity || t.formulas.nav)) || '' },
        { k: 'contributed capital', v: T.money(t, 'capital'), s: 'posted once + deposits - withdrawals (never P&L)' },
        { k: adv.advances_label || 'advanced to accounts', v: adv.advances_shown != null || adv.advances_shown_c != null ? T.money(adv, 'advances_shown') : T.money(t, 'advances'), s: 'internal transfers to the accounts; they net to zero across the lifetime' },
        { k: 'retained results', v: T.money(t, 'retained'), s: 'Treasury\'s own income (cash interest)', cls: T.sign(t, 'retained') },
        { k: 'available USD', v: avShown, s: av.note || ('settled USD - reserve ' + T.money(t, 'reserve')), cls: avN == null || avN === 0 ? '' : (avN < 0 ? 'neg' : 'pos'),
          title: 'what a sweep may move now: Treasury immediate USD less the book\'s reserve ' + T.money(t, 'reserve') + (avN != null && avN < 0 ? ' (signed: ' + T.money(av, 'signed') + ')' : '') },
        { k: 'encumbrance', v: T.money(t, 'encumbrance'), s: 'Treasury holds no positions in this build' }]));
      var two = T.h('div', { class: 'grid2' }); body.appendChild(two);
      var cash = T.h('div', { class: 'panel' }, T.h('h2', null, 'cash by currency'));
      var cb = t.cash_by_ccy || {};
      var rows = Object.keys(cb).map(function (c) { return { currency: c, settled: cb[c] }; });     /* {ccy: "1000000.00"}: major units */
      var g = T.h('div'); cash.appendChild(g);
      T.components.grid.mount(g, { tools: false, rows: rows, empty: 'no cash', columns: [{ key: 'currency', label: 'ccy', cls: 'l' }, { key: 'settled', label: 'settled', text: function (r) { return T.money(r, 'settled'); } }] });
      var it = data.interest || null;
      cash.appendChild(T.kv([['accrued interest', T.money(t, 'accrued')], ['interest', interestText(it), it ? [it.formula, it.units, it.note].filter(Boolean).join('\n') : '']]));
      cash.appendChild(T.convNote(['TREASURY', 'CASH_INT']));
      two.appendChild(cash);
      var advP = T.h('div', { class: 'panel' }, T.h('h2', null, 'advanced to accounts', T.h('small', null, 'click -> the account\'s cash & financing')));
      var arows = Array.isArray(data.advances) ? data.advances : Object.keys(t.advances_by_account || {}).map(function (a) { return { account_id: a, amount: t.advances_by_account[a] }; });
      var ag = T.h('div'); advP.appendChild(ag);
      T.components.grid.mount(ag, { tools: false, rows: arows, empty: 'nothing advanced', rowKey: function (r) { return r.account_id; }, columns: [{ key: 'account_id', label: 'account', cls: 'l', text: function (r) { return r.name || T.accountName(r.account_id); } }, { key: 'amount', label: 'advanced', text: function (r) { return T.money(r, 'amount'); } }], onEnter: function (r) { T.router.go(T.router.account(r.account_id, 'cash')); }, onClick: function (r) { T.router.go(T.router.account(r.account_id, 'cash')); } });
      two.appendChild(advP);
      var hist = T.h('div', { class: 'panel' }, T.h('h2', null, 'capital history', T.h('small', null, 'initial capital, deposits and withdrawals with their memo')));
      var hg = T.h('div'); hist.appendChild(hg);
      T.components.grid.mount(hg, { tools: false, rows: data.capital_history || [], empty: 'no capital movements', rowKey: function (r) { return r.transfer_id; }, columns: [
        { key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); } }, { key: 'kind', label: 'kind', cls: 'l', text: function (r) { return r.kind_label || r.kind || ''; } },
        { key: 'currency', label: 'ccy', cls: 'l' },
        { key: 'amount', label: 'amount', html: function (r) { var k = r.signed_amount != null || r.signed_amount_c != null ? 'signed_amount' : 'amount'; return '<span class="' + T.sign(r, k) + '">' + T.esc(T.money(r, k)) + '</span>'; }, text: function (r) { return T.money(r, r.signed_amount != null || r.signed_amount_c != null ? 'signed_amount' : 'amount'); } },
        { key: 'memo', label: 'memo', cls: 'l', html: function (r) { return '<span title="' + T.esc(r.request_id ? 'request ' + r.request_id : '') + '">' + T.esc(r.memo || '') + '</span>'; }, text: function (r) { return r.memo || ''; } },
        { key: 'transfer_id', label: 'ref', cls: 'l' }] });
      body.appendChild(hist);
      var sl = T.h('div', { class: 'panel' }, T.h('h2', null, 'sweep log', T.h('small', null, 'sweep-ins say what they paid for (click the reason to open it); sweep-outs return idle cash')));
      var sg = T.h('div'); sl.appendChild(sg);
      T.components.grid.mount(sg, { tools: (data.sweep_log || []).length > 12, rows: data.sweep_log || [], empty: 'no sweeps yet', maxHeight: '420px', rowKey: function (r) { return r.transfer_id; }, columns: [
        { key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); }, sort: function (r) { return r.ts; } },
        { key: 'account_id', label: 'account', cls: 'l', text: function (r) { return r.account_name || T.accountName(r.account_id); } },
        { key: 'direction', label: 'direction', cls: 'l', text: function (r) { var d = r.direction || r.kind || ''; return d === 'SWEEP_IN' ? 'sweep-in' : (d === 'SWEEP_OUT' ? 'sweep-out' : d); } },
        { key: 'currency', label: 'ccy', cls: 'l' }, { key: 'amount', label: 'amount', text: function (r) { return T.money(r, 'amount'); } },
        { key: 'reason', label: 'reason', cls: 'l', html: reasonHtml, text: function (r) { return r.reason || r.memo || ''; } }],
        onEnter: function (r) { var l = r.link; if (l && l.account_id) { T.router.go(T.router.account(l.account_id, l.kind === 'fill' ? 'orders' : 'transactions')); } } });
      sl.appendChild(T.convNote(['SWEEP']));
      body.appendChild(sl);
      setReserve(t);
    }
    /* the reserve panel is built once: the 20 s repaint never wipes what the owner is typing (it only refreshes an untouched box) */
    var res = T.h('input', { type: 'text', style: 'width:160px', 'aria-label': 'reserve (USD)', autocomplete: 'off' });
    res.addEventListener('input', function () { res.dataset.dirty = '1'; });
    var saveRes = T.h('button', { class: 'btn small', onclick: saveReserve }, 'Save');
    var resPanel = T.h('div', { class: 'panel' }, T.h('h2', null, 'reserve', T.h('small', null, 'USD of ' + bookName() + ' kept out of sweeps and buying power (this book only)')), T.form([T.field('reserve (USD)', res), ['', saveRes]]));
    el.appendChild(resPanel);
    function setReserve(t) {
      if (document.activeElement === res || res.dataset.dirty === '1') { return; }
      var r = T.money(t, 'reserve');
      res.value = r === 'n/a' ? '' : r.replace(/,/g, '');
    }
    function saveReserve() {
      var v = res.value.trim() || '0';
      var bad = /^0+(\.0{1,2})?$/.test(v) ? null : amountProblem(v);
      if (bad) { T.ui.toast('reserve: ' + bad, 'err', 6000); try { res.focus(); } catch (e) { /* ignore */ } return; }
      saveRes.disabled = true;
      T.api.patch('/api/settings', { treasury_reserve: v, book: T.book.code }).then(function () {
        saveRes.disabled = false; res.dataset.dirty = ''; T.ui.toast('reserve of ' + bookName() + ' saved: USD ' + T.fmt.money(v, 2), 'ok'); load(); T.poll.kick('accounts');
      }, function (e) { saveRes.disabled = false; T.api.fail(e, 'reserve'); });
    }
    enterSubmits([res], saveReserve);
    function fundsModal(kind) {
      var dep = kind === 'deposit';
      var amt = T.h('input', { type: 'text', placeholder: 'amount, e.g. 1000000.00', id: 'funds-amount', inputmode: 'decimal', autocomplete: 'off' });
      var ccy = T.h('select', { 'aria-label': 'currency' }); CCYS.forEach(function (c) { ccy.appendChild(T.h('option', { value: c }, c)); });
      var memo = T.h('input', { type: 'text', placeholder: 'memo (optional)' });
      var msg = T.h('div', { class: 'hint', style: 'min-height:1.2em' });
      var handle = null, busy = false;
      function submit() {
        if (busy) { return; }
        var v = amt.value.trim();
        var bad = amountProblem(v);
        if (bad) { msg.textContent = bad; msg.style.color = 'var(--down)'; try { amt.focus(); } catch (e) { /* ignore */ } return; }
        var shown = ccy.value + ' ' + T.fmt.money(v, 2);
        var question = dep ? 'Add ' + shown + ' to ' + bookName() + '?' : 'Withdraw ' + shown + ' from ' + bookName() + '?';
        busy = true;
        T.ui.confirm(question, { title: dep ? 'Add funds' : 'Withdraw', ok: dep ? 'Add ' + shown : 'Withdraw ' + shown }).then(function (yes) {
          if (!yes) { busy = false; try { amt.focus(); } catch (e) { /* ignore */ } return; }
          T.api.post('/api/treasury/' + kind, { currency: ccy.value, amount: v, memo: memo.value.trim() || null, book: T.book.code }).then(function (r) {
            busy = false; if (handle) { handle.close(); }
            T.ui.toast((dep ? 'deposit' : 'withdrawal') + ' recorded: ' + shown + ((r && r.transfer && r.transfer.transfer_id) ? ' (' + r.transfer.transfer_id + ')' : ''), 'ok');
            load(); T.poll.kick('books'); T.poll.kick('accounts');
          }, function (e) { busy = false; msg.textContent = T.api.describe(e); msg.style.color = 'var(--down)'; T.api.fail(e, kind); });
        });
      }
      enterSubmits([amt, memo], submit);
      handle = T.ui.modal({ title: (dep ? 'Add funds . ' : 'Withdraw . ') + bookName(),
        body: T.h('div', null, T.form([T.field('amount', amt), T.field('currency', ccy), T.field('memo', memo)]), msg,
          T.h('div', { class: 'hint' }, dep ? 'A capital deposit: it is never P&L and is the only way new money enters the lifetime. Digits with at most two decimals; you confirm the amount next.'
                                            : 'A capital withdrawal from settled Treasury cash (refused when the Treasury does not hold it). You confirm the amount next.')),
        actions: [{ label: 'Cancel' }, { label: dep ? 'Add' : 'Withdraw', cls: 'primary', onClick: function () { submit(); } }] });
      try { amt.focus(); } catch (e) { /* focus is a convenience */ }
    }
    function transferModal() {
      var accts = (T.state.accounts || []).filter(function (a) { return !a.status || a.status === 'OPEN'; });
      if (accts.length < 2) { T.ui.toast('this book has no second open account to transfer to', 'err'); return; }
      var tre = accts.filter(function (a) { return a.kind === 'TREASURY'; })[0] || accts[0];
      var trading = accts.filter(function (a) { return a.kind !== 'TREASURY'; });
      var active = T.state.active_account && trading.some(function (a) { return a.account_id === T.state.active_account; }) ? T.state.active_account : null;
      var from = T.h('select', { 'aria-label': 'from' }), to = T.h('select', { 'aria-label': 'to' });
      accts.forEach(function (a) { from.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); });
      from.value = tre.account_id;
      /* BM-18: To never offers the From account; it starts on the account open in the header, else the first trading account */
      function fillTo(prefer) {
        var keep = prefer || to.value;
        T.clear(to);
        accts.forEach(function (a) { if (a.account_id !== from.value) { to.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); } });
        to.value = keep; if (to.value !== keep || !to.value) { to.selectedIndex = 0; }
      }
      fillTo(active || (trading[0] || {}).account_id);
      from.addEventListener('change', function () { fillTo(); });
      var ccy = T.h('select', { 'aria-label': 'currency' }); CCYS.forEach(function (c) { ccy.appendChild(T.h('option', { value: c }, c)); });
      var amt = T.h('input', { type: 'text', placeholder: 'amount, e.g. 100000.00', inputmode: 'decimal', autocomplete: 'off' }); var memo = T.h('input', { type: 'text', placeholder: 'memo (optional)' });
      var msg = T.h('div', { class: 'hint', style: 'min-height:1.2em' });
      var handle = null, busy = false;
      function fail(text) { msg.textContent = text; msg.style.color = 'var(--down)'; }
      function submit() {
        if (busy) { return; }
        if (!from.value || !to.value || from.value === to.value) { fail('pick two different accounts'); return; }
        var v = amt.value.trim(); var bad = amountProblem(v);
        if (bad) { fail(bad); try { amt.focus(); } catch (e) { /* ignore */ } return; }
        busy = true;
        T.api.post('/api/transfers', { from_account: from.value, to_account: to.value, currency: ccy.value, amount: v, memo: memo.value.trim() || null }).then(function () {
          busy = false; if (handle) { handle.close(); }
          T.ui.toast('transfer done: ' + ccy.value + ' ' + T.fmt.money(v, 2) + ' ' + T.accountName(from.value) + ' -> ' + T.accountName(to.value), 'ok');
          load(); T.poll.kick('accounts'); T.poll.kick('account_detail');
        }, function (e) { busy = false; fail(e && e.code === 'SAME_ACCOUNT' ? 'pick two different accounts' : T.api.describe(e)); T.api.fail(e, 'transfer'); });
      }
      enterSubmits([amt, memo], submit);
      handle = T.ui.modal({ title: 'Internal transfer . ' + bookName(), body: T.h('div', null, T.form([T.field('from', from), T.field('to', to), T.field('amount', amt), T.field('currency', ccy), T.field('memo', memo)]), msg,
        T.h('div', { class: 'hint' }, 'Within this book only (books never transfer to each other). Settled cash moves at once.')),
        actions: [{ label: 'Cancel' }, { label: 'Transfer', cls: 'primary', onClick: function () { submit(); } }] });
      try { amt.focus(); } catch (e) { /* focus is a convenience */ }
    }
    T.poll.page('treasury', 20000, load);
    T.pages.treasury.unmount = function () { dead = true; T.pages.treasury.unmount = null; };
  }
  T.pages.treasury = { render: render, unmount: null, interestText: interestText, amountProblem: amountProblem };
})();
