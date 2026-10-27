/* Private credit desk (TERMINAL_DESIGN.md s13.14; WP-21; Build 2 P08: OA-17 / OA-22 / OA-29 / OA-30).
 * Mounted by components/creditdesk.js on the Credit Instruments "Private credit" tab:
 *
 *   var h = T.components.privatecredit.mount(el, {account_id: 'F1:CREDIT', book: 'F'});   // fetches at once
 *   h.refresh(); h.unmount();
 *
 * - Commit a direct loan: the borrower is picked from the private company master (company_id linked; a name outside the
 *   master is free text and says so), USD off SOFR; the form shows the qualified-purchaser rule of the book (the same as
 *   Private Markets: a book below it cannot commit), the SOFR print and the high-yield spread print the loan's mark needs.
 *   Without a spread print the server refuses with the reason; the owner may commit anyway only by ticking the explicit
 *   acknowledgement (the mark then stays the amortised cost until the first print is back-filled).
 * - Direct loans: amortised cost, accrued coupon, OID, the mark with its inputs, maturity (contractual date and the payment
 *   date it rolls to), next payment; Record fee; Repay: a partial prepayment at par or the full payoff.
 * Calls: GET /api/credit/private/borrowers, GET /api/credit/private (scope = the account), POST /api/credit/private,
 * POST /api/credit/private/{id}/fee, POST /api/credit/private/{id}/repay.  Nothing is computed here but the display.
 * ASCII only; no dependencies beyond core.js and components/grid.js.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};
  var PATH = '/api/credit/private';
  var PAYMENT_MONTHS = ['1', '3', '6', '12'];

  /* "4,900,000.00" / "4900000" -> integer cents (string arithmetic, no floats); null when not a 2-dp amount */
  function toCents(s) {
    s = String(s == null ? '' : s).replace(/,/g, '').trim();
    var m = /^(-?)(\d+)(?:\.(\d{0,2}))?$/.exec(s);
    if (!m) { return null; }
    var frac = (m[3] || '') + '00';
    var c = parseInt(m[2], 10) * 100 + parseInt(frac.slice(0, 2), 10);
    return m[1] === '-' ? -c : c;
  }
  function fromCents(c) { return T.fmt.cents(c); }
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
    if (i.oas_issue_pct != null) { parts.push('OAS at issue ' + i.oas_issue_pct + ' % (' + (i.oas_issue_as_of || '?') + (i.oas_backfilled ? ', back-filled' : '') + ')'); }
    if (i.oas_now_pct != null) { parts.push('OAS now ' + i.oas_now_pct + ' % (' + (i.oas_now_as_of || '?') + ')'); }
    if (i.days_remaining != null) { parts.push(i.days_remaining + ' days remaining'); }
    if (i.formula) { parts.push(i.formula); }
    return parts.join(' . ');
  }

  function mount(el, opts) {
    opts = opts || {};
    var account = opts.account_id || '';
    var state = { loans: [], dead: false, busy: false, grid: null, meta: null };
    T.clear(el);
    var root = T.h('div', { class: 'privatecredit' });
    el.appendChild(root);

    /* ---- commit form ---- */
    var gate = T.h('div');
    var borrowerSel = T.h('select', { id: 'pc-borrower-pick' });
    var borrower = T.h('input', { type: 'text', id: 'pc-borrower', placeholder: 'borrower name (not in the master)', spellcheck: 'false', hidden: true });
    var principal = T.h('input', { type: 'text', id: 'pc-principal', placeholder: 'USD principal, e.g. 5,000,000.00' });
    var issue = T.h('input', { type: 'text', id: 'pc-issue', value: '98.00' });
    var spread = T.h('input', { type: 'text', id: 'pc-spread', value: '6.00' });
    var term = T.h('input', { type: 'text', id: 'pc-term', value: '36' });
    var pay = T.h('select', { id: 'pc-pay' });
    PAYMENT_MONTHS.forEach(function (m) { pay.appendChild(T.h('option', { value: m, selected: m === '3' }, m === '1' ? 'monthly (1)' : m === '3' ? 'quarterly (3)' : m === '6' ? 'semi-annual (6)' : 'annual (12)')); });
    var ack = T.h('input', { type: 'checkbox', id: 'pc-ack', style: 'width:auto;margin:0 6px 0 0;vertical-align:middle' });
    /* a plain sentence beside its checkbox (not the uppercase field-label style of .form label) */
    var ackRow = T.h('label', { class: 'hint', hidden: true, style: 'text-transform:none;letter-spacing:0;font-family:inherit;font-size:11px;color:var(--fg2)' }, ack,
      ' commit without a spread print: the mark stays the amortised cost until the first print on or after today is back-filled as the issue spread');
    var ackPad = T.h('span', { hidden: true });
    var rates = T.h('div', { class: 'line' });
    var commitBtn = T.h('button', { class: 'btn primary', title: 'commit the loan: cash out = principal x issue price / 100 leaves settled cash now' }, 'Commit loan');
    var result = T.h('div');
    var form = T.form([
      T.field('borrower', T.h('div', null, borrowerSel, borrower), 'picked from the private company master; "other" for a name outside it'),
      T.field('principal (USD)', principal, 'face amount; the cash out is principal x issue price / 100'),
      T.field('issue price', issue, '98.00 = 2 % original-issue discount, accreted straight-line to maturity'),
      T.field('spread % over SOFR', spread, 'the coupon floats: SOFR (latest print) + spread, ACT/360, reset nightly'),
      T.field('term (months)', term, 'maturity = start + term months (the contractual date); the last payment rolls modified following'),
      T.field('payments every (months)', pay, 'quarterly by default; the coupon accrued through the night before each date is paid'),
      T.field('currency / index', T.h('span', { class: 'muted' }, 'USD / SOFR'), 'loans float off SOFR and are marked with the US high-yield spread'),
      [ackPad, ackRow],
      [T.h('span'), T.h('div', { class: 'row' }, commitBtn)]
    ]);
    form.style.maxWidth = '760px';
    root.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'Commit a direct loan', T.h('small', null, ' from ' + (T.accountName ? T.accountName(account) : account))), gate, rates, form, result));
    borrowerSel.addEventListener('change', function () { borrower.hidden = borrowerSel.value !== '__other__'; });

    function paintMeta(meta) {
      state.meta = meta;
      var keep = borrowerSel.value;
      T.clear(borrowerSel);
      borrowerSel.appendChild(T.h('option', { value: '' }, 'pick a company'));
      (meta.borrowers || []).forEach(function (b) {
        borrowerSel.appendChild(T.h('option', { value: b.company_id, selected: b.company_id === keep }, b.name + (b.sector ? ' (' + b.sector + ')' : '') + (b.status && b.status !== 'PRIVATE' ? ' - ' + String(b.status).replace('_', ' ').toLowerCase() : '')));
      });
      borrowerSel.appendChild(T.h('option', { value: '__other__', selected: keep === '__other__' }, 'other (not in the master)'));
      T.clear(gate);
      var e = meta.eligibility || {};
      if (e.eligible === false) {
        gate.appendChild(T.h('div', { class: 'notice' }, T.h('b', null, 'Not available in this book yet: '), e.reason || '', '. ', e.rule || ''));
        commitBtn.disabled = true; commitBtn.title = e.reason || 'below the qualified-purchaser threshold';
      } else {
        commitBtn.disabled = false;
        if (e.reason) { gate.appendChild(T.h('div', { class: 'hint' }, 'Qualified purchaser: ' + e.reason)); }
      }
      var s = meta.sofr || {}; var o = meta.oas || {};
      T.clear(rates).appendChild(T.h('span', null, 'SOFR ', T.h('b', null, s.ok ? s.value + ' % (' + s.as_of + ')' : 'unavailable'), s.ok ? '' : ' - ' + (s.reason || ''),
        ' . high-yield spread ', T.h('b', null, o.ok ? o.value + ' % (' + o.as_of + ')' : 'unavailable'), o.ok ? '' : ' - ' + (o.reason || '')));
      ackRow.hidden = !!o.ok; ackPad.hidden = !!o.ok;
      if (o.ok) { ack.checked = false; }
    }
    function loadMeta() {
      return T.api.get(PATH + '/borrowers').then(function (meta) { if (!state.dead) { paintMeta(meta); } }, function (e) { if (!state.dead) { T.clear(gate); T.errorBox(gate, e, 'private credit'); } });
    }

    function body() {
      var fr = pctToFraction(spread.value);
      var b = { account_id: account, currency: 'USD', principal: principal.value.replace(/,/g, '').trim(), issue_price: issue.value.trim(),
                index: 'SOFR', spread: fr == null ? spread.value : fr, term_months: term.value.trim(), payment_months: pay.value };
      if (borrowerSel.value === '__other__') { b.borrower = borrower.value.trim(); } else if (borrowerSel.value) { b.company_id = borrowerSel.value; }
      if (ack.checked && !ackRow.hidden) { b.acknowledge_no_spread = true; }
      return b;
    }
    function localCheck(b) {
      if (!b.company_id && !b.borrower) { return 'pick a borrower (or "other" and type its name)'; }
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
        load(); loadMeta();
      }, function (e) {
        state.busy = false; commitBtn.disabled = false;
        if (state.dead) { return; }
        T.errorBox(result, e, 'private credit commit');
        if (e && e.code === 'OAS_UNAVAILABLE') { ackRow.hidden = false; }
      });
    }
    commitBtn.addEventListener('click', onCommit);
    function showCommitResult(r) {
      var loan = (r && r.loan) || {};
      var kv = [['loan', loan.loan_id || r.loan_id], ['borrower', loan.borrower + (loan.company_linked ? '' : ' (free text)')], ['cash out', T.money(r, 'cash_out')],
                ['amortised cost', T.money(loan, 'amortised_cost')], ['maturity', loan.maturity_text || loan.maturity_date], ['next payment', loan.next_payment_date],
                ['mark', markText(loan), markTitle(loan)], ['funding', r.funding ? (r.funding.source + (r.funding.sweep_id ? ' (' + r.funding.sweep_id + ')' : '')) : 'n/a']];
      T.clear(result).appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'committed', T.h('small', null, r.replayed ? ' (already submitted)' : '')), T.kv(kv)));
      (r.warnings || []).forEach(function (w) { result.appendChild(T.h('div', { class: 'why' }, w)); });
      var sched = (r.schedule || loan.schedule || []);
      if (sched.length) {
        var rows = sched.map(function (s) {
          var when = s.date + (s.final ? (s.anchor && s.anchor !== s.date ? ' (final: maturity ' + s.anchor + ' rolls to ' + s.date + ')' : ' (final, at maturity)') : '');
          return T.h('div', null, when + ': ' + (s.projected_coupon != null || s.projected_coupon_c != null ? T.money(s, 'projected_coupon') + ' projected' : 'coupon not projected (no SOFR print)'));
        });
        result.appendChild(T.h('div', { class: 'hint' }, 'payment dates (coupon projected at the latest SOFR print; the actual coupon accrues nightly):'));
        var box = T.h('div', { class: 'kv' }); rows.forEach(function (x) { box.appendChild(T.h('div', null, '')); box.appendChild(x); });
        result.appendChild(box);
      }
    }

    /* ---- loans table ---- */
    var tableBox = T.h('div');
    var note = T.h('div', { class: 'hint' });
    root.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'Direct loans', T.h('small', null, ' committed from this account')), tableBox, note));
    function columns() {
      function num(key, label, title) { return { key: key, label: label, cls: 'num', title: function () { return title || ''; }, text: function (r) { return T.money(r, key); } }; }
      return [
        { key: 'loan_id', label: 'loan', cls: 'l', text: function (r) { return r.loan_id || ''; } },
        { key: 'borrower', label: 'borrower', cls: 'l', html: function (r) { return T.esc(r.borrower || '') + (r.company_linked ? '' : ' <small class="muted" title="not linked to the private company master">free text</small>'); }, text: function (r) { return r.borrower || ''; } },
        num('principal', 'face'),
        { key: 'issue_price', label: 'issue', cls: 'num', text: function (r) { return r.issue_price == null ? '' : T.fmt.px(r.issue_price); } },
        { key: 'coupon', label: 'coupon', cls: 'num', text: function (r) { return r.coupon_rate_text || (r.index + ' + ' + fractionToPct(r.spread) + ' %'); }, title: function (r) { return r.index_now ? (r.index + ' ' + r.index_now.value + ' % (' + r.index_now.as_of + ') + ' + fractionToPct(r.spread) + ' %') : 'no index print loaded'; } },
        num('amortised_cost', 'amortised cost', 'principal x issue price + OID accreted'),
        num('accrued', 'accrued', 'coupon accrued, not yet paid'),
        { key: 'mark', label: 'mark', cls: 'num', html: function (r) { return T.esc(markText(r)) + (r.oas_pending && r.status === 'OPEN' ? ' <small class="why" title="committed without a spread print: back-filled from the first print on or after the start">no spread yet</small>' : ''); }, title: markTitle, text: markText },
        { key: 'unrealised', label: 'unrealised', cls: 'num', text: function (r) { return T.fmt.signed(T.money(r, 'unrealised')); } },
        num('income', 'income', 'coupons + OID'),
        { key: 'next_payment_date', label: 'next pay', cls: 'l', text: function (r) { return r.status === 'OPEN' ? (r.next_payment_date || '') : ''; } },
        { key: 'maturity_date', label: 'maturity', cls: 'l', text: function (r) { return r.maturity_text || r.maturity_date || ''; } },
        { key: 'status', label: 'status', cls: 'l', text: function (r) { return r.status || ''; } },
        { key: 'actions', label: '', cls: 'l', html: function (r) {
            if (r.status !== 'OPEN') { return ''; }
            var id = T.esc(r.loan_id);
            return '<span class="acts"><button class="btn small" data-act="fee" data-loan="' + id + '">Record fee</button> <button class="btn small" data-act="repay" data-loan="' + id + '">Repay</button></span>';
          }, text: function () { return ''; } }
      ];
    }
    function render() {
      if (state.grid) { state.grid.update(state.loans); return; }
      T.clear(tableBox);
      var g = T.h('div'); tableBox.appendChild(g);
      state.grid = T.components.grid.mount(g, { columns: columns(), rows: state.loans, tools: false, rowKey: function (r) { return r.loan_id; },
        empty: 'no private loans in ' + (T.accountName ? T.accountName(account) : account),
        onEnter: function (r) { if (r && r.status === 'OPEN') { repayDialog(r); } },
        onClick: function (r, e) {
          var b = e && e.target && e.target.closest ? e.target.closest('button[data-act]') : null;
          if (!b || !r) { return; }
          if (b.getAttribute('data-act') === 'fee') { feeDialog(r); } else { repayDialog(r); }
        } });
    }
    function load() {
      if (state.dead) { return; }
      return T.api.get(PATH, { scope: account }).then(function (rows) {
        if (state.dead) { return; }
        state.loans = Array.isArray(rows) ? rows : (rows && rows.loans) || [];
        render();
        var open = state.loans.filter(function (r) { return r.status === 'OPEN'; }).length;
        T.clear(note).appendChild(T.h('span', null, open + ' open, ' + (state.loans.length - open) + ' repaid. Marks are model prices (amortised cost adjusted for the change in the high-yield spread since issue), never executable; coupons accrue nightly from the latest SOFR print.'));
      }, function (e) {
        if (state.dead) { return; }
        T.clear(tableBox); state.grid = null;
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
                   T.h('div', { class: 'hint' }, 'Owner-recorded fee income when you receive it. Nothing is generated.')),
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
      var full = T.h('input', { type: 'radio', name: 'pc-rp', checked: true }); var part = T.h('input', { type: 'radio', name: 'pc-rp' });
      var amount = T.h('input', { type: 'text', placeholder: 'USD received (accrued coupon first, the rest retires face at par)', disabled: true });
      full.addEventListener('change', function () { amount.disabled = true; }); part.addEventListener('change', function () { amount.disabled = false; amount.focus(); });
      var err = T.h('div', { class: 'error', hidden: true });
      var info = T.kv([['face', T.money(loan, 'principal')], ['accrued coupon (paid first)', T.money(loan, 'accrued')], ['amortised cost', T.money(loan, 'amortised_cost')]]);
      T.ui.modal({ title: 'Repay ' + loan.loan_id + ' (' + loan.borrower + ')', body: T.h('div', null, info,
        T.h('div', null, T.h('label', null, full, ' full payoff: accrued coupon + the whole face; the difference to the amortised cost is realised')),
        T.h('div', null, T.h('label', null, part, ' partial prepayment at par')), T.form([T.field('amount received', amount)]), err,
        T.h('div', { class: 'hint' }, 'Nights not yet accrued are accrued first. A partial prepayment pays the accrued coupon first and retires that much face at par; the loan keeps running on the smaller face.')),
        actions: [{ label: 'Cancel' }, { label: 'Repay', cls: 'primary', onClick: function (m, b) {
          var body;
          if (full.checked) { body = { full: true }; } else {
            var c = toCents(amount.value);
            if (c == null || c <= 0) { err.hidden = false; err.textContent = 'amount must be a positive USD amount with at most 2 decimals'; return; }
            body = { amount: fromCents(c).replace(/,/g, '') };
          }
          b.disabled = true;
          T.api.post(PATH + '/' + encodeURIComponent(loan.loan_id) + '/repay', body).then(function (r) {
            m.close();
            T.ui.toast(loan.loan_id + (r.partial ? ' prepaid: face now ' + T.money(r, 'face_left') : ' repaid') + '; realised ' + T.money(r, 'realised'), 'ok', 6000); load();
          }, function (e) { b.disabled = false; err.hidden = false; err.textContent = T.api.fail(e, 'private credit repay'); });
        } }] });
    }

    if (T.poll && T.poll.page) { T.poll.page('credit-private', 60000, function () { return Promise.all([load(), loadMeta()]); }); }
    else { loadMeta(); load(); }
    return {
      el: root,
      refresh: load,
      loans: function () { return state.loans.slice(); },
      unmount: function () { state.dead = true; if (state.grid && state.grid.destroy) { state.grid.destroy(); } T.clear(root); }
    };
  }

  T.components.privatecredit = { mount: mount, toCents: toCents, pctToFraction: pctToFraction, fractionToPct: fractionToPct };
})();
