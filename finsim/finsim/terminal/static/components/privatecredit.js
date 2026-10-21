/* Private credit section (TERMINAL_DESIGN.md s13.14, s17.5 Credit page "Private credit", s15 /api/credit/private; WP-21).
 * Mounted by pages/markets.js on the Credit Instruments account page:
 *
 *   var h = T.components.privatecredit.mount(el, {account_id: 'F1:CREDIT', book: 'F'});   // fetches at once
 *   h.refresh(); h.unmount();
 *
 * Commit form -> POST /api/credit/private (one request id per click; the server funds the cash out from settled cash,
 * else one Treasury sweep-in, else 409 INSUFFICIENT_FUNDS); loans table from GET /api/credit/private?scope=<account>;
 * row actions: Record fee -> POST /api/credit/private/{id}/fee; Repay -> POST /api/credit/private/{id}/repay (confirm).
 * Nothing is computed here: every figure (cash out, amortised cost, accrued, OID accreted, mark + inputs, unrealised,
 * the projected schedule) comes from the API.  The only local arithmetic is the repay prefill (principal + accrued, in
 * integer cents), which the server recomputes.  This build commits USD loans off SOFR (the form says so).
 * No dependencies beyond core.js (T.h, T.api, T.ui, T.fmt, T.money, T.form, T.field, T.kv, T.conv, T.components.grid); ASCII.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};
  var PATH = '/api/credit/private';
  var PAYMENT_MONTHS = ['1', '3', '6', '12'];

  /* "4,900,000.00" / "4900000" / "4,900,000.5" -> integer cents (string arithmetic, no floats); null when not a 2-dp amount */
  function toCents(s) {
    s = String(s == null ? '' : s).replace(/,/g, '').trim();
    var m = /^(-?)(\d+)(?:\.(\d{0,2}))?$/.exec(s);
    if (!m) { return null; }
    var frac = (m[3] || '') + '00';
    var c = parseInt(m[2], 10) * 100 + parseInt(frac.slice(0, 2), 10);
    return m[1] === '-' ? -c : c;
  }
  function fromCents(c) { return T.fmt.cents(c); }
  function moneyOf(row, name) {                                  /* the API shape (decimal string) or the view shape (cents) -> cents */
    if (!row) { return null; }
    if (row[name + '_c'] != null) { return Number(row[name + '_c']); }
    return toCents(row[name]);
  }
  function pctToFraction(p) {                                    /* "6.00" -> "0.06" (string arithmetic) */
    var s = String(p == null ? '' : p).replace(/%/g, '').trim();
    var m = /^(\d+)(?:\.(\d+))?$/.exec(s);
    if (!m) { return null; }
    var whole = m[1], frac = m[2] || '';
    var digits = (whole + frac);
    var dp = frac.length + 2;
    while (digits.length <= dp) { digits = '0' + digits; }
    var out = digits.slice(0, digits.length - dp) + '.' + digits.slice(digits.length - dp);
    return out.replace(/^0+(\d)/, '$1');
  }
  function fractionToPct(fr) {                                   /* "0.06" -> "6.00" for display */
    var n = Number(fr); if (!isFinite(n)) { return ''; }
    return (Math.round(n * 10000) / 100).toFixed(2);
  }
  function markText(row) {
    var m = row && row.mark;
    if (!m || m.value == null) { return 'n/a'; }
    return T.fmt.money(m.value) + ' ' + (m.quality || 'computed');
  }
  function markTitle(row) {
    var m = row && row.mark; var i = (m && m.inputs) || {};
    var parts = [];
    if (m && m.note) { parts.push(m.note); }
    if (i.oas_issue_pct != null) { parts.push('OAS at issue ' + i.oas_issue_pct + ' % (' + (i.oas_issue_as_of || '?') + ')'); }
    if (i.oas_now_pct != null) { parts.push('OAS now ' + i.oas_now_pct + ' % (' + (i.oas_now_as_of || '?') + ')'); }
    if (i.days_remaining != null) { parts.push(i.days_remaining + ' days remaining'); }
    if (i.formula) { parts.push(i.formula); }
    return parts.join(' . ');
  }

  function mount(el, opts) {
    opts = opts || {};
    var account = opts.account_id || '';
    var state = { loans: [], dead: false, busy: false, grid: null };
    T.clear(el);
    var root = T.h('div', { class: 'privatecredit' });
    el.appendChild(root);

    /* ---- commit form ---- */
    var borrower = T.h('input', { type: 'text', id: 'pc-borrower', placeholder: 'borrower (free text)', spellcheck: 'false' });
    var principal = T.h('input', { type: 'text', id: 'pc-principal', placeholder: 'USD principal, e.g. 5,000,000.00' });
    var issue = T.h('input', { type: 'text', id: 'pc-issue', value: '98.00' });
    var spread = T.h('input', { type: 'text', id: 'pc-spread', value: '6.00' });
    var term = T.h('input', { type: 'text', id: 'pc-term', value: '36' });
    var pay = T.h('select', { id: 'pc-pay' });
    PAYMENT_MONTHS.forEach(function (m) { pay.appendChild(T.h('option', { value: m, selected: m === '3' }, m === '1' ? 'monthly (1)' : m === '3' ? 'quarterly (3)' : m === '6' ? 'semi-annual (6)' : 'annual (12)')); });
    var commitBtn = T.h('button', { class: 'btn primary', title: 'commit the loan: cash out = principal x issue price / 100 leaves settled cash now' }, 'Commit loan');
    var result = T.h('div');
    var form = T.form([
      T.field('account', T.h('span', null, account || 'n/a'), 'loans are committed from the lifetime\'s CREDIT account'),
      T.field('borrower', borrower, 'a name; no company master lookup is needed'),
      T.field('principal (USD)', principal, 'face amount; the cash out is principal x issue price / 100'),
      T.field('issue price', issue, '98.00 = 2 % original-issue discount, accreted straight-line to maturity'),
      T.field('spread % over SOFR', spread, 'the coupon floats: SOFR (latest print, RATE_LAG) + spread, ACT/360, reset nightly'),
      T.field('term (months)', term, 'maturity = start + term months (unadjusted); payments roll modified following'),
      T.field('payments every (months)', pay, 'quarterly by default; the coupon accrued through the night before each date is paid'),
      T.field('currency / index', T.h('span', { class: 'muted' }, 'USD / SOFR (this build)'), 'other currencies answer 400 CURRENCY_UNSUPPORTED'),
      ['', T.h('div', { class: 'row' }, commitBtn)]
    ]);
    root.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'Commit a direct loan', T.h('small', null, 'POST /api/credit/private')), form, result));

    function body() {
      var fr = pctToFraction(spread.value);
      return { account_id: account, borrower: borrower.value.trim(), currency: 'USD', principal: principal.value.replace(/,/g, '').trim(), issue_price: issue.value.trim(),
               index: 'SOFR', spread: fr == null ? spread.value : fr, term_months: term.value.trim(), payment_months: pay.value };
    }
    function localCheck(b) {
      if (!b.borrower) { return 'borrower is required'; }
      if (toCents(b.principal) == null || toCents(b.principal) <= 0) { return 'principal must be a positive USD amount with at most 2 decimals'; }
      if (pctToFraction(spread.value) == null) { return 'spread must be a percent such as 6.00'; }
      if (!/^\d+$/.test(b.term_months) || Number(b.term_months) < 1) { return 'term must be a whole number of months'; }
      return null;
    }
    function onCommit() {
      if (state.busy) { return; }
      var b = body();
      var bad = localCheck(b);
      T.clear(result);
      if (bad) { result.appendChild(T.h('div', { class: 'error' }, bad)); return; }
      state.busy = true; commitBtn.disabled = true;
      T.api.post(PATH, b).then(function (r) {
        state.busy = false; commitBtn.disabled = false;
        if (state.dead) { return; }
        showCommitResult(r);
        load();
      }, function (e) {
        state.busy = false; commitBtn.disabled = false;
        if (state.dead) { return; }
        T.errorBox(result, e, 'private credit commit');
      });
    }
    commitBtn.addEventListener('click', onCommit);
    function showCommitResult(r) {
      var loan = (r && r.loan) || {};
      var kv = [['loan', loan.loan_id || r.loan_id], ['borrower', loan.borrower], ['cash out', T.money(r, 'cash_out')], ['amortised cost', T.money(loan, 'amortised_cost')],
                ['maturity', loan.maturity_date], ['next payment', loan.next_payment_date], ['mark', markText(loan), markTitle(loan)],
                ['funding', r.funding ? (r.funding.source + (r.funding.sweep_id ? ' (' + r.funding.sweep_id + ')' : '')) : 'n/a']];
      T.clear(result).appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'committed', T.h('small', null, r.replayed ? 'replayed' : 'request ' + String(r.request_id || '').slice(0, 8))), T.kv(kv)));
      var sched = (r.schedule || loan.schedule || []);
      if (sched.length) {
        var rows = sched.map(function (s) { return T.h('div', null, s.date + (s.final ? ' (maturity)' : '') + ': ' + (s.projected_coupon != null || s.projected_coupon_c != null ? T.money(s, 'projected_coupon') : 'n/a') + ' projected'); });
        result.appendChild(T.h('div', { class: 'hint' }, 'payment dates (coupon projected at the latest SOFR print; the actual coupon accrues nightly):'));
        var box = T.h('div', { class: 'kv' }); rows.forEach(function (x) { box.appendChild(T.h('div', null, '')); box.appendChild(x); });
        result.appendChild(box);
      }
    }

    /* ---- loans table ---- */
    var tableBox = T.h('div');
    var note = T.h('div', { class: 'hint' });
    root.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'Direct loans', T.h('small', null, 'GET ' + PATH + '?scope=' + account)), tableBox, note));
    function columns() {
      function num(key, label, title) { return { key: key, label: label, cls: 'num', title: function () { return title || ''; }, text: function (r) { return T.money(r, key); } }; }
      return [
        { key: 'loan_id', label: 'loan', cls: 'l', text: function (r) { return r.loan_id || ''; } },
        { key: 'borrower', label: 'borrower', cls: 'l', text: function (r) { return r.borrower || ''; } },
        num('principal', 'principal'),
        { key: 'issue_price', label: 'issue', cls: 'num', text: function (r) { return r.issue_price == null ? '' : T.fmt.px(r.issue_price); } },
        { key: 'coupon', label: 'coupon', cls: 'num', text: function (r) { return r.coupon_rate_text || (r.index + ' + ' + fractionToPct(r.spread) + ' %'); }, title: function (r) { return r.index_now ? (r.index + ' ' + r.index_now.value + ' % (' + r.index_now.as_of + ') + ' + fractionToPct(r.spread) + ' %') : 'no index print loaded'; } },
        num('amortised_cost', 'amortised cost', 'principal x issue price + OID accreted (1130)'),
        num('oid_accreted', 'OID accreted'),
        num('accrued', 'accrued', 'coupon accrued, not yet paid (1220)'),
        { key: 'mark', label: 'mark', cls: 'num', text: markText, title: markTitle },
        { key: 'unrealised', label: 'unrealised', cls: 'num', text: function (r) { return T.fmt.signed(T.money(r, 'unrealised')); } },
        num('income', 'income', 'coupons + OID (4300)'),
        num('fees', 'fees', 'amendment fees recorded (4320)'),
        { key: 'next_payment_date', label: 'next pay', cls: 'l', text: function (r) { return r.status === 'OPEN' ? (r.next_payment_date || '') : ''; } },
        { key: 'maturity_date', label: 'maturity', cls: 'l', text: function (r) { return r.maturity_date || ''; } },
        { key: 'status', label: 'status', cls: 'l', text: function (r) { return r.status || ''; } },
        { key: 'actions', label: '', cls: 'l', html: function (r) {
            if (r.status !== 'OPEN') { return ''; }
            var id = T.esc(r.loan_id);
            return '<button class="btn small" data-act="fee" data-loan="' + id + '">Record fee</button> <button class="btn small" data-act="repay" data-loan="' + id + '">Repay</button>';
          } }
      ];
    }
    function render() {
      T.clear(tableBox);
      if (!state.loans.length) { tableBox.appendChild(T.h('div', { class: 'empty' }, 'no private loans in ' + (account || 'this account'))); return; }
      var g = T.h('div'); tableBox.appendChild(g);
      state.grid = T.components.grid.mount(g, { columns: columns(), rows: state.loans, tools: false, rowKey: function (r) { return r.loan_id; },
        onEnter: function (r) { if (r && r.status === 'OPEN') { repayDialog(r); } } });
      g.addEventListener('click', function (e) {
        var b = e.target && e.target.closest ? e.target.closest('button[data-act]') : null;
        if (!b) { return; }
        var loan = null; state.loans.forEach(function (x) { if (x.loan_id === b.getAttribute('data-loan')) { loan = x; } });
        if (!loan) { return; }
        if (b.getAttribute('data-act') === 'fee') { feeDialog(loan); } else { repayDialog(loan); }
      });
    }
    function load() {
      if (state.dead) { return; }
      T.api.get(PATH, { scope: account }).then(function (rows) {
        if (state.dead) { return; }
        state.loans = Array.isArray(rows) ? rows : (rows && rows.loans) || [];
        render();
        var open = state.loans.filter(function (r) { return r.status === 'OPEN'; }).length;
        T.clear(note).appendChild(T.h('span', null, open + ' open, ' + (state.loans.length - open) + ' repaid. Marks are model prices (amortised cost adjusted for the FRED high-yield spread change), never executable; coupons accrue nightly from the latest SOFR print.'));
      }, function (e) {
        if (state.dead) { return; }
        T.clear(tableBox);
        T.errorBox(tableBox, e, 'private credit loans');
        tableBox.appendChild(T.h('button', { class: 'btn small', onclick: load }, 'Retry'));
      });
    }

    /* ---- row actions ---- */
    function feeDialog(loan) {
      var amount = T.h('input', { type: 'text', placeholder: 'USD amount received' });
      var memo = T.h('input', { type: 'text', placeholder: 'memo (amendment, waiver, arrangement ...)' });
      var err = T.h('div', { class: 'error', hidden: true });
      T.ui.modal({ title: 'Record a fee on ' + loan.loan_id, body: T.h('div', null, T.form([T.field('amount', amount), T.field('memo', memo)]), err,
                   T.h('div', { class: 'hint' }, 'Owner-recorded: Dr cash / Cr fee income (4320). Nothing is generated.')),
        actions: [{ label: 'Cancel' }, { label: 'Record', cls: 'primary', onClick: function (m, b) {
          var c = toCents(amount.value);
          if (c == null || c <= 0) { err.hidden = false; err.textContent = 'amount must be a positive USD amount with at most 2 decimals'; return; }
          b.disabled = true;
          T.api.post(PATH + '/' + encodeURIComponent(loan.loan_id) + '/fee', { amount: fromCents(c).replace(/,/g, ''), memo: memo.value.trim() }).then(function () {
            m.close(); T.ui.toast('fee ' + fromCents(c) + ' recorded on ' + loan.loan_id, 'ok'); load();
          }, function (e) { b.disabled = false; err.hidden = false; err.textContent = T.api.fail(e, 'private credit fee'); });
        } }] });
    }
    function repayDialog(loan) {
      var p = moneyOf(loan, 'principal'), a = moneyOf(loan, 'accrued');
      var prefill = p == null ? '' : fromCents(p + (a || 0)).replace(/,/g, '');
      var amount = T.h('input', { type: 'text', value: prefill });
      var err = T.h('div', { class: 'error', hidden: true });
      var info = T.kv([['accrued coupon (paid first)', T.money(loan, 'accrued')], ['amortised cost retired', T.money(loan, 'amortised_cost')], ['face', T.money(loan, 'principal')]]);
      T.ui.modal({ title: 'Repay ' + loan.loan_id + ' (' + loan.borrower + ')', body: T.h('div', null, info, T.form([T.field('amount received', amount, 'pays the accrued coupon first; the rest retires the whole amortised cost; the difference is realised')]), err,
                   T.h('div', { class: 'hint' }, 'The whole loan is retired (a partial prepayment is not in this build). Prefilled with face + accrued.')),
        actions: [{ label: 'Cancel' }, { label: 'Repay', cls: 'primary', onClick: function (m, b) {
          var c = toCents(amount.value);
          if (c == null || c <= 0) { err.hidden = false; err.textContent = 'amount must be a positive USD amount with at most 2 decimals'; return; }
          b.disabled = true;
          T.api.post(PATH + '/' + encodeURIComponent(loan.loan_id) + '/repay', { amount: fromCents(c).replace(/,/g, '') }).then(function (r) {
            m.close(); T.ui.toast(loan.loan_id + ' repaid: realised ' + (r && r.realised != null ? T.fmt.money(r.realised) : T.money(r, 'realised')), 'ok'); load();
          }, function (e) { b.disabled = false; err.hidden = false; err.textContent = T.api.fail(e, 'private credit repay'); });
        } }] });
    }

    load();
    return {
      el: root,
      refresh: load,
      loans: function () { return state.loans.slice(); },
      unmount: function () { state.dead = true; if (state.grid && state.grid.destroy) { state.grid.destroy(); } T.clear(root); }
    };
  }

  T.components.privatecredit = { mount: mount, toCents: toCents, pctToFraction: pctToFraction, fractionToPct: fractionToPct };
})();
