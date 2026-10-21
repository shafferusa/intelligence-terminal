/* Cash & financing (TERMINAL_DESIGN.md s17.1 / s17.5 #/a/{account}/cash; WP-12).  GET /api/accounts/{id}/cash: cash by
 * currency (settled / pending / available / held at lender / reserved), the settlements ladder (next business days),
 * loans, borrows & collateral, futures / option / OTC margin, sweeps today; the FX convert form (POST /api/fx/preview
 * -> confirm -> POST /api/fx/spot), the loan form (POST /api/credit/loans; repay POST /api/credit/loans/{id}/repay),
 * PB draw / repay, the per-account sweep switch and buffer (PATCH /api/accounts/{id}).  Every figure is the server's.
 *
 * Units (finsim/terminal/quotes/rates.py docstring UNITS; core.js T.fmt):
 * - a loan's index observation as published -- `value` of POST /api/credit/loans' `rate`, `index.value`, `index_pct` --
 *   is a PERCENT (3.87 = 3.87 %) and goes through T.fmt.ratePct; `spread`, `all_in`, `rate`, `segment_rate`,
 *   `fee_rate` and `rebate_rate` are FRACTIONS (0.0075 = 0.75 %) and go through T.fmt.rate.  Never the other way round
 *   (the "SOFR 387.00 %" toast of 2026-10-21).
 * - `available` is the account's own spendable cash FLOORED AT ZERO (accounting.AVAILABLE_TEXT, sentence AVAILABLE);
 *   what working orders and payables need beyond it is shown beside it as "reserved R (Treasury sweep covers N)", with
 *   UNFUNDED / overdrawn flagged (the CashView fields reserved / needs / sweep_covers / unfunded / overdrawn, formatted
 *   by T.fmt.liquidity); the definition is the hover text.
 */
(function () {
  'use strict';
  var T = window.T;
  var CCYS = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD'];
  /* The rate of a drawn loan (POST /api/credit/loans -> rate {index, value, spread, all_in, as_of, monthly_proxy}):
   * value is the index observation as published, a PERCENT -> T.fmt.ratePct; spread and all_in are FRACTIONS ->
   * T.fmt.rate.  "SOFR 3.87 % + 0.75 % = 4.62 % (as of 2026-10-19)". */
  function loanRateText(rt) {
    if (!rt) { return 'the policy rate + spread'; }
    return (rt.index || 'policy rate') + ' ' + T.fmt.ratePct(rt.value) + ' + ' + T.fmt.rate(rt.spread) + (rt.all_in != null ? ' = ' + T.fmt.rate(rt.all_in) : '') +
           (rt.as_of ? ' (as of ' + rt.as_of + (rt.monthly_proxy ? ', monthly proxy' : '') + ')' : '');
  }
  /* A loan row's rate: the index name, its observation when the row carries one (index.value / index_pct: a PERCENT),
   * the spread and the all-in rate when given (FRACTIONS). */
  function loanRowRate(r) {
    var idx = r.index && typeof r.index === 'object' ? r.index : null;
    var pct = idx && idx.value != null ? idx.value : r.index_pct;
    var allIn = r.rate != null ? r.rate : r.segment_rate;
    var asOf = r.rate_as_of || (idx && idx.obs_date) || '';
    return (r.index_name || (idx ? idx.series : r.index) || '') + (pct != null && pct !== '' ? ' ' + T.fmt.ratePct(pct) : '') +
           (r.spread != null && r.spread !== '' ? ' + ' + T.fmt.rate(r.spread) : '') + (allIn != null && allIn !== '' ? ' = ' + T.fmt.rate(allIn) : '') +
           (asOf ? ' (' + asOf + ')' : '');
  }
  /* The liquidity line of one currency row (a CashView): T.fmt.liquidity -> {available, extra, warn, title}. */
  function liquidity(r) {
    if (T.fmt && typeof T.fmt.liquidity === 'function') { return T.fmt.liquidity(r, ''); }
    return { available: T.money(r, 'available'), extra: '', warn: false, title: r.definition || '' };
  }
  function render(el, route) {
    var acct = route.account; var data = null;
    T.pageHead(el, 'Cash & financing', acct);
    var body = T.h('div'); el.appendChild(body);
    function load() {
      return T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/cash').then(function (d) { data = d; paint(); }, function (e) { T.clear(body); T.errorBox(body, e, 'cash'); });
    }
    function cashGrid(cash) {
      var rows = Object.keys(cash || {}).map(function (c) { return Object.assign({ currency: c }, cash[c]); });
      var cols = ['settled', 'receivable', 'payable', 'pending', 'held_at_lender', 'margin', 'otc_margin', 'reserved', 'accrued'].map(function (k) { return { key: k, label: k.replace(/_/g, ' '), text: function (r) { return T.money(r, k); } }; });
      cols.unshift({ key: 'currency', label: 'ccy', cls: 'l' });
      /* available = max(0, own spendable cash); what orders / payables need beyond it: "reserved R (Treasury sweep covers N)" */
      cols.push({ key: 'available', label: 'available', html: function (r) { return '<span class="pos">' + T.esc(liquidity(r).available) + '</span>'; }, text: function (r) { return liquidity(r).available; }, title: function (r) { return liquidity(r).title; } });
      cols.push({ key: 'beyond_own_cash', label: 'beyond own cash', cls: 'l', html: function (r) { var l = liquidity(r); return l.extra ? '<span class="' + (l.warn ? 'neg' : '') + '">' + T.esc(l.extra) + '</span>' : ''; }, text: function (r) { return liquidity(r).extra; }, title: function (r) { return liquidity(r).title; } });
      var g = T.h('div'); T.components.grid.mount(g, { columns: cols, rows: rows, tools: false, empty: 'no cash balances' }); return g;
    }
    function paint() {
      T.clear(body);
      var a = data.account || {};
      body.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'cash by currency', T.h('small', null, data.as_of ? 'as of ' + T.fmt.time(data.as_of) : '')), cashGrid(data.cash), T.convNote(['AVAILABLE', 'SWEEP'])));
      var two = T.h('div', { class: 'grid2' }); body.appendChild(two);
      var ladder = T.h('div', { class: 'panel' }, T.h('h2', null, 'settlements ladder', T.h('small', null, 'next 5 business days')));
      var lrows = data.ladder || data.settlements || [];
      if (lrows.length) { var lg = T.h('div'); ladder.appendChild(lg); T.components.grid.mount(lg, { tools: false, rows: lrows, columns: [{ key: 'due_date', label: 'date', cls: 'l', text: function (r) { return r.due_date || r.date || ''; } }, { key: 'currency', label: 'ccy', cls: 'l' }, { key: 'amount', label: 'amount', html: function (r) { return '<span class="' + T.sign(r, 'amount') + '">' + T.esc(T.money(r, 'amount')) + '</span>'; } }, { key: 'leg', label: 'leg', cls: 'l', text: function (r) { return r.leg || r.kind || ''; } }, { key: 'ref', label: 'ref', cls: 'l', text: function (r) { return r.fill_id || r.fx_deal_id || r.ref || ''; } }, { key: 'status', label: 'status', cls: 'l', text: function (r) { return r.status || 'PENDING'; } }] }); }
      else { ladder.appendChild(T.h('div', { class: 'empty' }, 'nothing pending')); }
      two.appendChild(ladder);
      var m = data.margin || {}; var coll = data.collateral || {};
      two.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'margin & collateral'), T.kv([
        ['futures required / posted', T.money(m, 'futures_required') + ' / ' + T.money(m, 'futures_posted')], ['options required / posted', T.money(m, 'options_required') + ' / ' + T.money(m, 'options_posted')],
        ['OTC IM / VM', T.money(m, 'otc_im') + ' / ' + T.money(m, 'otc_vm')], ['collateral at lender (102 %)', T.money(coll, 'held_at_lender')]]), T.convNote(['FUT_MARGIN', 'OPT_MARGIN', 'SHORT_COLLATERAL', 'SWAP_VM'])));
      var loans = T.h('div', { class: 'panel' }, T.h('h2', null, 'currency loans & PB financing'));
      var lrows2 = data.loans || [];                  /* the OPEN loans (accounting.obligations); PB_MARGIN is repaid with the PB form */
      if (lrows2.length) { var lg2 = T.h('div'); loans.appendChild(lg2); T.components.grid.mount(lg2, { tools: false, rows: lrows2, columns: [{ key: 'loan_id', label: 'loan', cls: 'l' }, { key: 'kind', label: 'kind', cls: 'l' }, { key: 'currency', label: 'ccy', cls: 'l' }, { key: 'principal', label: 'principal', text: function (r) { return T.money(r, 'principal'); } }, { key: 'accrued', label: 'accrued', text: function (r) { return T.money(r, 'accrued'); } }, { key: 'rate', label: 'rate', cls: 'l', text: loanRowRate }, { key: 'opened_date', label: 'opened', cls: 'l' }, { key: 'lender', label: 'lender', cls: 'l' }, { key: 'act', label: '', cls: 'l', html: function (r) { return (r.status == null || r.status === 'OPEN') && (r.kind == null || r.kind === 'CASH_LOAN') ? '<button class="btn small" data-act="repay">repay</button>' : ''; }, text: function () { return ''; } }], onClick: function (r, e) { if (e.target.closest('button[data-act="repay"]')) { repay(r); } } }); }
      else { loans.appendChild(T.h('div', { class: 'empty' }, 'no loans')); }
      loans.appendChild(loanForm());
      if (a.kind === 'PB' || data.pb) { loans.appendChild(pbForm()); }
      loans.appendChild(T.convNote(['ACCRUAL', 'RATE_LAG']));
      body.appendChild(loans);
      var borrows = T.h('div', { class: 'panel' }, T.h('h2', null, 'securities borrows & collateral'));
      var brows = data.borrows || [];
      if (brows.length) { var bg = T.h('div'); borrows.appendChild(bg); T.components.grid.mount(bg, { tools: false, rows: brows, columns: [{ key: 'borrow_id', label: 'borrow', cls: 'l' }, { key: 'instrument_id', label: 'instrument', cls: 'l' }, { key: 'qty', label: 'qty', text: function (r) { return T.fmt.qty(r.qty); } }, { key: 'fee_rate', label: 'fee', text: function (r) { return T.fmt.rate(r.fee_rate); } }, { key: 'rebate_rate', label: 'rebate', text: function (r) { return T.fmt.rate(r.rebate_rate); } }, { key: 'tier', label: 'tier', cls: 'l' }, { key: 'collateral', label: 'collateral', text: function (r) { return T.money(r, 'collateral'); } }, { key: 'last_mark_date', label: 'marked', cls: 'l', text: function (r) { return r.last_mark_date || ''; } }, { key: 'recall_notice', label: 'notice', cls: 'l', text: function (r) { return r.recall_notice || ''; } }] }); }
      else { borrows.appendChild(T.h('div', { class: 'empty' }, 'no open borrows')); }
      body.appendChild(borrows);
      var sw = T.h('div', { class: 'panel' }, T.h('h2', null, 'sweeps'));
      var srows = data.sweeps_today || [];
      if (srows.length) { var sg = T.h('div'); sw.appendChild(sg); T.components.grid.mount(sg, { tools: false, rows: srows, columns: [{ key: 'ts', label: 'time', cls: 'l', text: function (r) { return T.fmt.time(r.ts); } }, { key: 'direction', label: 'direction', cls: 'l', text: function (r) { return r.direction || r.kind || ''; } }, { key: 'currency', label: 'ccy', cls: 'l' }, { key: 'amount', label: 'amount', text: function (r) { return T.money(r, 'amount'); } }, { key: 'reason', label: 'reason', cls: 'l', text: function (r) { return r.reason || r.memo || ''; } }] }); }
      else { sw.appendChild(T.h('div', { class: 'empty' }, 'no sweeps today')); }
      var chk = T.h('input', { type: 'checkbox', id: 'sweep-out' }); chk.checked = !!(a.sweep_out == null ? true : a.sweep_out);
      var buf = T.h('input', { type: 'text', id: 'sweep-buffer', value: T.money(a, 'sweep_buffer') === 'n/a' ? '' : T.money(a, 'sweep_buffer').replace(/,/g, ''), style: 'width:140px' });
      var save = T.h('button', { class: 'btn small', onclick: function () {
        save.disabled = true;
        T.api.patch('/api/accounts/' + encodeURIComponent(acct), { sweep_out: chk.checked, sweep_buffer: buf.value.trim() || '0' }).then(function () { T.ui.toast('sweep settings saved', 'ok'); save.disabled = false; load(); }, function (e) { save.disabled = false; T.api.fail(e, 'sweep settings'); });
      } }, 'Save');
      sw.appendChild(T.form([T.field('sweep-out after 17:30 ET', chk), T.field('buffer kept (USD)', buf), ['', save]]));
      body.appendChild(sw);
      body.appendChild(fxForm());
    }
    function fxForm() {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'FX convert', T.h('small', null, 'spot at the Yahoo mid +/- 0.2 pip, T+2')));
      var buy = T.h('select', { id: 'fx-buy' }), sell = T.h('select', { id: 'fx-sell' });
      CCYS.forEach(function (c) { buy.appendChild(T.h('option', { value: c, selected: c === 'EUR' }, c)); sell.appendChild(T.h('option', { value: c, selected: c === 'USD' }, c)); });
      var amt = T.h('input', { type: 'text', id: 'fx-amount', placeholder: 'amount', style: 'width:160px' });
      var amtCcy = T.h('select', { id: 'fx-amount-ccy' }); ['buy', 'sell'].forEach(function (k) { amtCcy.appendChild(T.h('option', { value: k }, 'amount in ' + k + ' ccy')); });
      var out = T.h('div', { class: 'conv' });
      var prev = T.h('button', { class: 'btn', onclick: function () {
        var body = { account_id: acct, buy_ccy: buy.value, sell_ccy: sell.value, amount: amt.value.trim(), amount_ccy: amtCcy.value === 'buy' ? buy.value : sell.value };
        T.api.post('/api/fx/preview', body, { retries: 0 }).then(function (pv) {
          T.clear(out);
          out.appendChild(T.kv([['mid', T.px(pv.quote || pv, { size: 'inline', field: pv.quote ? undefined : 'mid' })], ['rate dealt', T.fmt.px(pv.rate)], ['buy', buy.value + ' ' + T.money(pv, 'buy')], ['sell', sell.value + ' ' + T.money(pv, 'sell')], ['spread cost', T.money(pv, 'spread_cost')], ['value date', pv.value_date]]));
          var go = T.h('button', { class: 'btn primary', onclick: function () {
            go.disabled = true;
            T.api.post('/api/fx/spot', body).then(function (r) { T.ui.toast('FX deal ' + (r.deal && r.deal.deal_id || '') + ' booked, settles ' + (r.deal && r.deal.value_date || pv.value_date), 'ok'); load(); T.poll.kick('account_detail'); }, function (e) { go.disabled = false; T.api.fail(e, 'fx spot'); });
          } }, 'Confirm & deal');
          out.appendChild(go);
        }, function (e) { T.clear(out); out.appendChild(T.h('div', { class: 'error' }, T.api.fail(e, 'fx preview'))); });
      } }, 'Preview');
      p.appendChild(T.form([T.field('buy', buy), T.field('sell', sell), T.field('amount', T.h('span', { class: 'row' }, amt, amtCcy)), ['', prev]]));
      p.appendChild(out); p.appendChild(T.convNote(['FX_SPOT', 'FX_RESULT']));
      return p;
    }
    function loanForm() {
      var f = T.h('div', { style: 'margin-top:8px' });
      var ccy = T.h('select', { id: 'loan-ccy' }); CCYS.forEach(function (c) { ccy.appendChild(T.h('option', { value: c }, c)); });
      var amt = T.h('input', { type: 'text', id: 'loan-amount', placeholder: 'principal', style: 'width:160px' });
      var term = T.h('input', { type: 'text', id: 'loan-term', placeholder: 'term days (optional)', style: 'width:160px' });
      var btn = T.h('button', { class: 'btn', onclick: function () {
        btn.disabled = true;
        T.api.post('/api/credit/loans', { account_id: acct, currency: ccy.value, amount: amt.value.trim(), term_days: term.value.trim() || null }).then(function (r) { btn.disabled = false; T.ui.toast('loan ' + (r.loan && r.loan.loan_id || '') + ' drawn at ' + loanRateText(r.rate), 'ok', 6000); load(); T.poll.kick('account_detail'); }, function (e) { btn.disabled = false; T.api.fail(e, 'loan'); });
      } }, 'Draw loan');
      f.appendChild(T.h('h2', null, 'new currency loan', T.h('small', null, 'policy rate + 0.75 %, daily accrual; JPY/CHF/CAD/AUD use monthly OECD proxies (as-of month shown)')));
      f.appendChild(T.form([T.field('currency', ccy), T.field('principal', amt), T.field('term', term), ['', btn]]));
      return f;
    }
    function pbForm() {
      var f = T.h('div', { style: 'margin-top:8px' }, T.h('h2', null, 'prime-broker financing'));
      var amt = T.h('input', { type: 'text', placeholder: 'amount USD', style: 'width:160px' });
      function go(kind) { return function () { T.api.post('/api/credit/pb/' + encodeURIComponent(acct) + '/' + kind, { amount: amt.value.trim() }).then(function () { T.ui.toast('PB ' + kind + ' done', 'ok'); load(); T.poll.kick('account_detail'); }, function (e) { T.api.fail(e, 'pb ' + kind); }); }; }
      f.appendChild(T.form([T.field('amount', amt), ['', T.h('span', { class: 'row' }, T.h('button', { class: 'btn', onclick: go('draw') }, 'Draw'), T.h('button', { class: 'btn', onclick: go('repay') }, 'Repay'))]]));
      return f;
    }
    function repay(loan) {
      var amt = T.h('input', { type: 'text', value: T.money(loan, 'principal').replace(/,/g, ''), style: 'width:160px' });
      T.ui.modal({ title: 'Repay ' + loan.loan_id, body: T.form([T.field('amount (' + loan.currency + ')', amt)]), actions: [{ label: 'Cancel' }, { label: 'Repay', cls: 'primary', onClick: function (m, b) {
        b.disabled = true;
        T.api.post('/api/credit/loans/' + encodeURIComponent(loan.loan_id) + '/repay', { amount: amt.value.trim() }).then(function (r) { m.close(); T.ui.toast('repaid: interest ' + T.money(r, 'paid_interest') + ', principal ' + T.money(r, 'paid_principal'), 'ok', 5000); load(); }, function (e) { b.disabled = false; T.api.fail(e, 'repay'); });
      } }] });
    }
    load();
    T.poll.page('cash', 15000, function () { return data ? load() : Promise.resolve(); });
  }
  T.pages.cash = { render: render };
})();
