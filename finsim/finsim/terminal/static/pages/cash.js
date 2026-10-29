/* Cash & financing (TERMINAL_DESIGN.md s17.1 / s17.5 #/a/{account}/cash; WP-12; Build 2 P11: BM-11, BM-12, BM-19, BM-22, BM-34,
 * OA-19, OA-31, PO-05, PO-06, PO-17, SR-05, USX-19, APIUI-12).  Every figure is the server's; this page formats and routes.
 *
 * Reads (each poll, 15 s): GET /api/accounts/{id}/cash (cash by currency, the settlements ladder, margin, borrows, transfers,
 * OTC rows, the account row and its buying power); GET /api/credit/loans?account_id= (C26: index value and as-of, spread, all-in
 * rate, interest posted, the nights a repayment now posts first, payoff) when the account has loans; GET /api/otc/contracts?
 * account_id= when it holds OTC contracts (the VM per contract; ?detail=1 on demand for one contract's VM history).
 * The page is built ONCE and its figures are updated in place: the poll never rebuilds a form, so typed values survive (PO-06).
 *
 * - cash by currency: settled / receivable / payable / pending / held at lender / margin / OTC margin / reserved / accrued /
 *   available and "beyond own cash" (headers wrap: BM-34).  Under the grid, the "restricted (collateral received)" line (FINSIM1
 *   D9 / C21, the CashView's C9b adjustments): the counterparties' collateral inside settled cash - variation margin received
 *   under the CSA net of VM posted (floored at 0) plus cash collateral received for shares lent - which available, buying
 *   power and the sweep exclude.  With the collateral module bound (T.hasModule('collateral')) the margin panel shows its
 *   parts (GET /api/collateral/restricted); else no request goes out and the panel shows the CashView total only.  1.0.6 (FINSIM1
 *   P6 task 4): beside it, the "margin requirement (Reg T)" adjustment when the account has one - its own cash the Reg T
 *   requirement needs beyond the longs' loan value, kept out of available and the sweep - with a link to Cash & Collateral.  `available` is the account's own spendable cash FLOORED AT ZERO
 *   (accounting.AVAILABLE_TEXT); what working orders and payables need beyond it is "reserved R (Treasury sweep covers N)",
 *   UNFUNDED / overdrawn flagged (T.fmt.liquidity(r, '')); the definition is the hover text.
 * - margin & collateral: futures required / posted, short-option margin (a reservation), OTC IM, VM posted, VM received
 *   (otc_im / otc_vm_posted / otc_vm_received, BM-12 / PO-05 / SR-05), collateral at lenders; then the variation margin by
 *   contract with a "VM history" button per contract (the dated VM entries) and a link to the OTC tab.
 * - currency loans (C26): lender (assumed terms), currency, principal, the index with its value and date + spread = all-in,
 *   interest to date (posted + the nights a repayment now posts first), payoff; Repay (an amount, or "pay off in full" ->
 *   {full: true}); the toast reads paid_interest / paid_principal.  The loan form is on the Credit Instruments account only
 *   (lender from the bank directory, its currencies and tenors); elsewhere one line links to the Credit loans desk (BM-19).
 * - prime-broker financing (PB accounts, PO-17): on / off with the switch inline, headroom, limit (0 = no cap), drawn, the SOFR
 *   value + spread = all-in rate and the interest posted, next to Draw / Repay.
 * - securities borrows; sweeps: today's (accounting day) SWEEP_IN / SWEEP_OUT of this account with the fill each one funded
 *   (BM-11 / USX-19 / APIUI-12), and the sweep-out switch and buffer.
 * - currency conversion is on the FX desk (Foreign Markets & FX): a link, no form here (BM-19 / OA-19).
 *
 * Units (finsim/terminal/quotes/rates.py docstring UNITS; core.js T.fmt):
 * - a loan's index observation as published -- `value` of POST /api/credit/loans' `rate`, `index.value`, `index_pct`,
 *   `index_value_pct` -- is a PERCENT (3.87 = 3.87 %) and goes through T.fmt.ratePct; so are the C26 `spread_pct` and
 *   `all_in_pct`.  `spread`, `all_in`, `rate`, `segment_rate`, `fee_rate` and `rebate_rate` are FRACTIONS (0.0075 = 0.75 %)
 *   and go through T.fmt.rate.  Never the other way round (the "SOFR 387.00 %" toast of 2026-10-21).
 */
(function () {
  'use strict';
  var T = window.T;
  var CSS = '.cashp .cash-grid th{white-space:normal;height:auto;min-height:28px;line-height:1.15;padding-top:4px;padding-bottom:4px;vertical-align:bottom;max-width:96px}' +
            '.cashp .sub2{display:block;font-size:10px;line-height:1.25;color:var(--fg3);max-width:260px;overflow:hidden;text-overflow:ellipsis}' +
            '.cashp td{padding-top:2px;padding-bottom:2px}.cashp .line{font-family:var(--mono);font-size:11.5px;color:var(--fg2);margin:4px 0;line-height:1.5}' +
            '.cashp .line b{color:var(--fg)}.cashp .acts{white-space:nowrap}.cashp .acts .btn{margin-right:3px}.cashp .form{max-width:760px}' +
            '.cashp .pbstate{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;margin:4px 0 8px;font-family:var(--mono);font-size:11.5px}' +
            '.cashp .pbstate .on{color:var(--up)}.cashp .pbstate .off{color:var(--fg3)}.cashp .msg{font-family:var(--mono);font-size:11px;min-height:15px;margin:4px 0}';
  function ensureCss() { if (document.getElementById('cash-css')) { return; } var s = document.createElement('style'); s.id = 'cash-css'; s.textContent = CSS; document.head.appendChild(s); }
  /* The rate of a drawn loan (POST /api/credit/loans -> rate {index, value, spread, all_in, as_of, monthly_proxy}):
   * value is the index observation as published, a PERCENT -> T.fmt.ratePct; spread and all_in are FRACTIONS ->
   * T.fmt.rate.  "SOFR 3.87 % + 0.75 % = 4.62 % (as of 2026-10-19)". */
  function loanRateText(rt) {
    if (!rt) { return 'the policy rate + spread'; }
    return (rt.index || 'policy rate') + ' ' + T.fmt.ratePct(rt.value) + ' + ' + T.fmt.rate(rt.spread) + (rt.all_in != null ? ' = ' + T.fmt.rate(rt.all_in) : '') +
           (rt.as_of ? ' (as of ' + rt.as_of + (rt.monthly_proxy ? ', monthly proxy' : '') + ')' : '');
  }
  /* A loan row's rate.  A C26 row (GET /api/credit/loans): index_value_pct, spread_pct and all_in_pct are all PERCENTS.  An
   * obligations row (the fallback): the index observation (index.value / index_pct) is a PERCENT, spread / rate are FRACTIONS. */
  function loanRowRate(r) {
    if (r.index_value_pct !== undefined || r.spread_pct !== undefined) {
      return (r.index_name || '') + (r.index_value_pct != null ? ' ' + T.fmt.ratePct(r.index_value_pct) : ' (no print yet)') + ' + ' + T.fmt.ratePct(r.spread_pct) +
             (r.all_in_pct != null ? ' = ' + T.fmt.ratePct(r.all_in_pct) : '') + (r.index_as_of ? ' (' + r.index_as_of + (r.monthly_proxy ? ', monthly proxy' : '') + ')' : '');
    }
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
  function lid(acct) { var s = String(acct || ''); var i = s.indexOf(':'); return i >= 0 ? s.slice(0, i) : s; }
  function roleOf(acct) { var s = String(acct || ''); var i = s.indexOf(':'); return i >= 0 ? s.slice(i + 1) : s; }
  /* a refusal in trader words: the server's message, never a field name (PO-17 / BM-19) */
  function failText(e, where) {
    var code = e && e.code;
    if (code === 'PB_FINANCING_OFF') { return 'PB financing is switched off for this account: switch it on above, then draw'; }
    if (code === 'PB_HEADROOM') { return (e.message || 'the draw exceeds the PB financing headroom') + ' - headroom grows with the long market value held at the prime broker'; }
    if (code === 'NOT_CREDIT_ACCOUNT') { return 'currency loans are drawn in the Credit Instruments account'; }
    return T.api.fail(e, where);
  }

  function render(el, route) {
    ensureCss();
    var acct = route.account; var role = roleOf(acct);
    var data = null, loans = null, contracts = null, S = null, dead = false, banks = null, restricted = null;
    T.pageHead(el, 'Cash & financing', T.accountName ? T.accountName(acct) : acct);
    var root = T.h('div', { class: 'cashp' }); el.appendChild(root);
    var spin = T.h('div', { class: 'spin' }); root.appendChild(spin);
    var err = T.h('div', { class: 'error', hidden: true }); root.appendChild(err);

    function isKind(k) { var a = (data && data.account) || {}; return a.kind === k || (k === 'PB' && /^PB_/.test(role)); }
    function load() {
      return T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/cash').then(function (d) {
        if (dead) { return; }
        data = d;
        var extras = [];
        var hasLoans = (d.loans || []).length > 0 || role === 'CREDIT';
        extras.push(hasLoans ? T.api.get('/api/credit/loans', { account_id: acct }).then(function (x) { loans = (x && x.rows) || []; }, function (e) { loans = null; console.warn('credit loans', e); }) : Promise.resolve(loans = []));
        extras.push((d.otc || []).length ? T.api.get('/api/otc/contracts', { account_id: acct }).then(function (x) { contracts = Array.isArray(x) ? x : []; }, function (e) { contracts = null; console.warn('otc contracts', e); }) : Promise.resolve(contracts = []));
        /* FINSIM1 C21: the parts of the restricted figure, only from a build whose collateral module is bound (rule 5: no request otherwise) */
        extras.push(T.hasModule && T.hasModule('collateral') ? T.api.get('/api/collateral/restricted', { account_id: acct }).then(function (x) { restricted = x || null; }, function (e) { restricted = null; console.warn('restricted', e); }) : Promise.resolve(restricted = null));
        return Promise.all(extras).then(function () {
          if (dead) { return; }
          if (spin.parentNode) { spin.parentNode.removeChild(spin); }
          err.hidden = true;
          if (!S) { build(); }
          update();
        });
      }, function (e) { if (dead) { return; } if (spin.parentNode) { spin.parentNode.removeChild(spin); } err.hidden = false; err.textContent = T.api.fail(e, 'cash'); });
    }
    function gridIn(parent, spec, cls) { var g = T.h('div', { class: cls || '' }); parent.appendChild(g); return T.components.grid.mount(g, Object.assign({ tools: false, rows: [], empty: 'loading ...' }, spec)); }

    function build() {
      S = {};
      /* ---- cash by currency */
      var pc = T.h('div', { class: 'panel' });
      S.asOf = T.h('small'); pc.appendChild(T.h('h2', null, 'cash by currency', S.asOf));
      var cols = ['settled', 'receivable', 'payable', 'pending', 'held_at_lender', 'margin', 'otc_margin', 'reserved', 'accrued'].map(function (k) { return { key: k, label: k.replace(/_/g, ' '), text: function (r) { return T.money(r, k); } }; });
      cols.unshift({ key: 'currency', label: 'ccy', cls: 'l' });
      /* available = max(0, own spendable cash); what orders / payables need beyond it: "reserved R (Treasury sweep covers N)" */
      cols.push({ key: 'available', label: 'available', html: function (r) { return '<span class="pos">' + T.esc(liquidity(r).available) + '</span>'; }, text: function (r) { return liquidity(r).available; }, title: function (r) { return liquidity(r).title; } });
      cols.push({ key: 'beyond_own_cash', label: 'beyond own cash', cls: 'l', html: function (r) { var l = liquidity(r); return l.extra ? '<span class="' + (l.warn ? 'neg' : '') + '">' + T.esc(l.extra) + '</span>' : ''; }, text: function (r) { return liquidity(r).extra; }, title: function (r) { return liquidity(r).title; } });
      S.cash = gridIn(pc, { columns: cols, rowKey: function (r) { return r.currency; }, empty: 'no cash balances' }, 'cash-grid');
      S.restricted = T.h('div', { class: 'line', id: 'cash-restricted', title: RESTRICTED_TEXT }); pc.appendChild(S.restricted);
      S.marginLine = T.h('div', { class: 'line', id: 'cash-regt', hidden: true }); pc.appendChild(S.marginLine);
      pc.appendChild(T.convNote(['AVAILABLE', 'SWEEP']));
      root.appendChild(pc);
      /* ---- settlements ladder + margin & collateral */
      var two = T.h('div', { class: 'grid2' }); root.appendChild(two);
      var ladder = T.h('div', { class: 'panel' }, T.h('h2', null, 'settlements ladder', T.h('small', null, 'next 5 business days')));
      S.ladder = gridIn(ladder, { rowKey: function (r) { return r.settle_id || ((r.fill_id || r.fx_deal_id || '') + ':' + r.leg); }, empty: 'nothing pending', columns: [
        { key: 'due_date', label: 'date', cls: 'l', text: function (r) { return r.due_date || r.date || ''; } }, { key: 'currency', label: 'ccy', cls: 'l' },
        { key: 'amount', label: 'amount', html: function (r) { return '<span class="' + T.sign(r, 'amount') + '">' + T.esc(T.money(r, 'amount')) + '</span>'; }, text: function (r) { return T.money(r, 'amount'); } },
        { key: 'leg', label: 'leg', cls: 'l', text: function (r) { return T.codeWords(r.leg || r.kind || ''); }, title: function (r) { return r.leg || ''; } }, { key: 'ref', label: 'ref', cls: 'l', text: function (r) { return r.fill_id || r.fx_deal_id || r.ref || ''; } },
        { key: 'status', label: 'status', cls: 'l', text: function (r) { return ladderStatus(r); }, title: function (r) { return r.fail_reason || ''; } }] });
      two.appendChild(ladder);
      var mp = T.h('div', { class: 'panel', id: 'cash-margin' }, T.h('h2', null, 'margin & collateral'));
      S.marginKv = T.h('div'); mp.appendChild(S.marginKv);
      S.vmBox = T.h('div', { class: 'panel', id: 'cash-vm', hidden: true });
      S.vmBox.appendChild(T.h('h2', null, 'variation margin by contract', T.h('small', null, 'VM is collateral, not P&L; posted when the mark falls, received when it rises')));
      S.vm = gridIn(S.vmBox, { rowKey: function (r) { return r.contract_id; }, empty: 'no OTC contracts', columns: [
        { key: 'contract', label: 'contract', cls: 'l', html: function (r) { return '<b>' + T.esc(r.kind_label || r.kind || '') + '</b> ' + T.esc(r.contract_id) + '<span class="sub2" title="' + T.esc(r.side_text || '') + '">' + T.esc(r.side_text || '') + '</span>'; }, text: function (r) { return r.contract_id; } },
        { key: 'im', label: 'IM', text: function (r) { return T.money(r, r.im_balance != null || r.im_balance_c != null ? 'im_balance' : 'im'); } },
        { key: 'vm_posted', label: 'VM posted', text: function (r) { return T.money(r, 'vm_posted'); } }, { key: 'vm_received', label: 'VM received', text: function (r) { return T.money(r, 'vm_received'); } },
        { key: 'vm_balance', label: 'VM balance', html: function (r) { return '<span class="' + T.sign(r, 'vm_balance') + '">' + T.esc(T.money(r, 'vm_balance')) + '</span>'; }, text: function (r) { return T.money(r, 'vm_balance'); } },
        { key: 'margin_mode', label: 'mode', cls: 'l' },
        { key: 'act', label: ' ', cls: 'l acts', html: function () { return '<button class="btn small" data-act="vm">VM history</button>'; }, text: function () { return ''; } }],
        onClick: function (r, e) { if (e.target.closest && e.target.closest('button[data-act="vm"]')) { vmHistory(r); } } });
      S.vmLink = T.h('div', { class: 'line' }); S.vmBox.appendChild(S.vmLink);
      mp.appendChild(T.convNote(['FUT_MARGIN', 'OPT_MARGIN', 'SHORT_COLLATERAL', 'SWAP_VM']));
      two.appendChild(mp);
      root.appendChild(S.vmBox);
      /* ---- loans and PB financing */
      var lp = T.h('div', { class: 'panel', id: 'cash-loans' }, T.h('h2', null, isKind('PB') ? 'prime-broker financing & loans' : 'currency loans', T.h('small', null, 'interest accrues nightly; paid at repayment')));
      S.loans = gridIn(lp, { rowKey: function (r) { return r.loan_id; }, empty: 'no loans', columns: [
        { key: 'loan_id', label: 'loan', cls: 'l', html: function (r) { return '<b>' + T.esc(r.loan_id) + '</b><span class="sub2" style="max-width:150px">' + T.esc((r.kind === 'PB_MARGIN' ? 'PB financing' : 'currency loan') + (r.opened_date ? ' . since ' + r.opened_date : '') + (r.maturity_date ? ' . matures ' + r.maturity_date : ' . open')) + '</span>'; }, text: function (r) { return r.loan_id; } },
        { key: 'act', label: ' ', cls: 'l acts', html: function (r) { return (r.repayable || ((r.status == null || r.status === 'OPEN') && (r.kind == null || r.kind === 'CASH_LOAN'))) ? '<button class="btn small" data-act="repay">Repay ...</button>' : ''; }, text: function () { return ''; } },
        { key: 'principal', label: 'principal', html: function (r) { return T.esc(r.currency || '') + ' ' + T.esc(T.money(r, 'principal')) + '<span class="sub2" style="max-width:140px;margin-left:auto" title="' + T.esc(r.lender_label || r.lender || '') + '">' + T.esc(r.lender_label || r.lender || '') + '</span>'; }, text: function (r) { return (r.currency || '') + ' ' + T.money(r, 'principal'); } },
        { key: 'rate', label: 'all-in rate', html: function (r) { var allIn = r.all_in_pct != null ? T.fmt.ratePct(r.all_in_pct) : (r.rate != null ? T.fmt.rate(r.rate) : 'n/a'); return '<b>' + T.esc(allIn) + '</b><span class="sub2" style="max-width:160px;margin-left:auto" title="' + T.esc(loanRowRate(r)) + '">' + T.esc(loanRowRate(r)) + '</span>'; }, text: loanRowRate },
        { key: 'accrued', label: 'interest / payoff', title: function (r) { return (r.pending_nights && r.pending_nights.length ? 'posted ' + T.money(r, 'accrued_posted') + ' + ' + r.pending_nights.length + ' night(s) a repayment now posts first ' + T.money(r, 'pending') : 'interest posted so far') + (r.payoff_note ? ' . payoff: ' + r.payoff_note : ''); },
          html: function (r) { return T.esc(T.money(r, 'accrued')) + (r.kind === 'PB_MARGIN' ? '<span class="sub2">paid monthly</span>' : '<span class="sub2">payoff ' + T.esc(T.money(r, 'payoff')) + '</span>'); }, text: function (r) { return T.money(r, 'accrued'); } }],
        onClick: function (r, e) { if (e.target.closest && e.target.closest('button[data-act="repay"]')) { repay(r); } } });
      S.loanNote = T.h('div', { class: 'line' }); lp.appendChild(S.loanNote);
      if (role === 'CREDIT') { lp.appendChild(loanForm()); }
      else {
        var link = T.h('a', { href: T.router.account(lid(acct) + ':CREDIT', 'markets/loans') }, 'the Credit Instruments loans desk');
        lp.appendChild(T.h('div', { class: 'line' }, 'Currency loans are drawn and repaid in Credit Instruments (lender, tenor and assumed terms there): ', link, '.'));
      }
      if (isKind('PB')) { lp.appendChild(pbForm()); }
      lp.appendChild(T.convNote(['ACCRUAL', 'RATE_LAG']));
      root.appendChild(lp);
      /* ---- borrows */
      var bp = T.h('div', { class: 'panel' }, T.h('h2', null, 'securities borrows & collateral'));
      S.borrows = gridIn(bp, { rowKey: function (r) { return r.borrow_id; }, empty: 'no open borrows', columns: [{ key: 'borrow_id', label: 'borrow', cls: 'l' }, { key: 'instrument_id', label: 'instrument', cls: 'l' }, { key: 'qty', label: 'qty', text: function (r) { return T.fmt.qty(r.qty); } },
        { key: 'fee_rate', label: 'fee', text: function (r) { return T.fmt.rate(r.fee_rate); } }, { key: 'rebate_rate', label: 'rebate', text: function (r) { return T.fmt.rate(r.rebate_rate); } }, { key: 'tier', label: 'tier', cls: 'l' },
        { key: 'lender', label: 'lender', cls: 'l', text: function (r) { return r.lender || ''; } }, { key: 'collateral', label: 'collateral', text: function (r) { return T.money(r, 'collateral'); } },
        { key: 'last_mark_date', label: 'marked', cls: 'l', text: function (r) { return r.last_mark_date || ''; } }, { key: 'recall_notice', label: 'notice', cls: 'l', text: function (r) { return r.recall_notice || ''; } }] });
      root.appendChild(bp);
      /* ---- sweeps */
      var sp = T.h('div', { class: 'panel', id: 'cash-sweeps' });
      S.sweepHead = T.h('small'); sp.appendChild(T.h('h2', null, 'sweeps', S.sweepHead));
      S.sweeps = gridIn(sp, { rowKey: function (r) { return r.transfer_id; }, empty: 'no sweeps today', columns: [
        { key: 'ts', label: 'time', cls: 'l', text: function (r) { return T.fmt.time(r.ts); } }, { key: 'business_date', label: 'day', cls: 'l', text: function (r) { return r.business_date || ''; } },
        { key: 'kind', label: 'direction', cls: 'l', text: function (r) { return r.kind === 'SWEEP_IN' ? 'in from Treasury' : (r.kind === 'SWEEP_OUT' ? 'out to Treasury' : (r.kind || '')); } },
        { key: 'currency', label: 'ccy', cls: 'l' }, { key: 'amount', label: 'amount', text: function (r) { return T.money(r, 'amount'); } },
        { key: 'linked', label: 'for', cls: 'l', html: function (r) { return linkedHtml(r); }, text: function (r) { return (r.linked_type || '') + ' ' + (r.linked_id || ''); } },
        { key: 'shortfall', label: 'shortfall covered', text: function (r) { return r.shortfall != null || r.shortfall_c != null ? T.money(r, 'shortfall') : ''; } }] });
      S.sweepNote = T.h('div', { class: 'line' }); sp.appendChild(S.sweepNote);
      sp.appendChild(sweepForm());
      root.appendChild(sp);
      /* ---- FX conversion lives on the FX desk */
      var fx = T.h('div', { class: 'panel' }, T.h('h2', null, 'currency conversion'));
      fx.appendChild(T.h('div', { class: 'line' }, 'Spot conversions and forwards are dealt on the FX desk: ', T.h('a', { href: T.router.account(lid(acct) + ':FOREIGN_FX', 'markets/fx') }, 'Foreign Markets & FX - FX spot'),
        '. A purchase that needs a currency this account lacks converts it there, or in its own ticket when the ticket offers it.'));
      fx.appendChild(T.convNote(['FX_SPOT', 'FX_RESULT']));
      root.appendChild(fx);
    }

    var LINK_WORDS = { fill: 'fill', otc_im: 'OTC initial margin', otc: 'OTC margin', otc_vm: 'OTC variation margin', swap_vm: 'swap variation margin', fx_deal: 'FX deal',
                       settlement: 'settlement', loan: 'loan', pb: 'PB financing', futures_vm: 'futures variation margin', vm: 'variation margin', borrow: 'stock borrow',
                       repo: 'repo', private: 'private trade', swapvm: 'swap variation margin', cdxcpn: 'CDX coupon', asof_sweep: 'as-of sweep, batch',
                       asof_funding: 'as-of funding, line' };
    function linkedHtml(r) {
      if (!r.linked_id) { return '<span class="muted">' + T.esc(r.kind === 'SWEEP_OUT' ? 'idle cash after the close' : '') + '</span>'; }
      var t = String(r.linked_type || '');
      var parts = String(r.linked_id).split(':');
      var word = LINK_WORDS[t] || t.replace(/_/g, ' ');
      if (t === 'otc' && (parts[0] === 'im' || parts[0] === 'vm') && parts.length > 1) { word = parts[0] === 'im' ? 'OTC initial margin' : 'OTC variation margin'; parts.shift(); }
      if (parts.length > 1 && parts[0] === t) { parts.shift(); }               /* POSTCLOSE-05: an idem-style link repeats its type ("swapvm:SWP-000003:2026-10-27") */
      var core = parts.shift();
      var tail = parts.map(function (x) { return x === 'open' ? 'at booking' : x; }).join(' ');
      var what = word + ' ' + core + (tail ? ' (' + tail + ')' : '');
      var href = /^(SWP|OTC|CDX)-/.test(core) ? T.router.account(acct, 'markets/otc') : T.router.account(acct, 'transactions');
      return '<a href="' + T.esc(href) + '" title="what this sweep funded">' + T.esc(what) + '</a>';
    }
    /* FINSIM1 C21 / C9b: the restricted part of a currency row = its "restricted" adjustment (0.00 when none; n/a from a server
     * without the adjustments) */
    var RESTRICTED_TEXT = 'the counterparties\' collateral inside settled cash: variation margin received under the CSA net of variation margin posted ' +
                          '(floored at 0; one CSA per prime broker) + cash collateral received for shares lent; excluded from available, buying power ' +
                          'and the sweep, it earns no credit interest; a variation-margin payment to the same counterparty may use it';
    function restrictedAdj(r) {
      if (!r || !Array.isArray(r.adjustments)) { return null; }
      return r.adjustments.filter(function (a) { return a && a.key === 'restricted'; })[0] || { key: 'restricted', amount: '0.00' };
    }
    function restrictedText(r) { var a = restrictedAdj(r); return a ? T.money(a, 'amount') : 'n/a'; }
    /* FINSIM1 C15: a pending settlement that failed reads "failed" (the owner's simulated counterparty fail, or not funded on its
     * due date and retried automatically); the reason is the hover */
    function ladderStatus(r) {
      if (r.fail_state === 'FAILED') { return r.fail_kind === 'SIMULATED' ? 'failed (simulated)' : 'failed - retried'; }
      return T.codeWords(r.status || 'PENDING');
    }
    /* the line under the cash grid: per currency, the restricted part of settled cash and what is held back with it */
    function paintRestricted(cash) {
      var rows = Object.keys(cash || {}).map(function (c) { return Object.assign({ currency: c }, cash[c]); });
      var known = rows.filter(function (r) { return Array.isArray(r.adjustments); });
      T.clear(S.restricted);
      if (!rows.length) { return; }
      if (!known.length) { S.restricted.appendChild(T.h('span', null, 'restricted (collateral received): not reported by this server')); return; }
      var held = known.filter(function (r) { return T.num(restrictedAdj(r), 'amount'); });
      S.restricted.appendChild(T.h('b', null, 'restricted (collateral received): '));
      if (!held.length) { S.restricted.appendChild(T.h('span', null, '0.00 - no counterparty collateral sits in this account\'s cash')); return; }
      S.restricted.appendChild(T.h('span', null, held.map(function (r) { return r.currency + ' ' + restrictedText(r) + ' of settled ' + T.money(r, 'settled'); }).join(' . ') +
        ' - the counterparties\' collateral: not available, never swept, earns no credit interest'));
    }
    /* 1.0.6: the Reg T margin cash an account keeps (the C9b adjustment "margin"): shown only when it holds some */
    function paintMargin(cash) {
      var usd = (cash || {}).USD;
      var a = usd && Array.isArray(usd.adjustments) ? usd.adjustments.filter(function (x) { return x && x.key === 'margin'; })[0] : null;
      T.clear(S.marginLine);
      S.marginLine.hidden = !(a && T.num(a, 'amount'));
      if (S.marginLine.hidden) { return; }
      S.marginLine.appendChild(T.h('b', null, 'Reg T margin cash kept: '));
      S.marginLine.appendChild(T.h('span', { title: 'the account\'s own cash the Reg T requirement needs beyond the loan value of its longs: not available, never swept' },
        'USD ' + T.money(a, 'amount') + ' - needed for the Reg T requirement of the physical shorts; '));
      S.marginLine.appendChild(T.h('a', { href: '#/cash?account=' + encodeURIComponent(acct) }, 'requirement and terms on Cash & Collateral'));
    }
    /* the margin panel's rows: the total, and its parts from the collateral module when it is bound */
    function restrictedRows(cashUsd) {
      var p = restricted;
      if (p && p.restricted != null) {
        return [['restricted (collateral received)', T.money(p, 'restricted'), p.definition || RESTRICTED_TEXT],
                ['of which net VM received under the CSA', T.money(p, 'csa_restricted') + (p.reuse_allowed ? ' (reuse allowed)' : ''),
                 'VM received ' + T.money(p, 'vm_received') + ' - VM posted ' + T.money(p, 'vm_posted') + ', floored at 0; one CSA per prime broker'],
                ['of which cash collateral for shares lent', T.money(p, 'seclend_cash'), 'cash the borrowers of your lent shares posted (never netted)']];
      }
      return [['restricted (collateral received)', cashUsd ? restrictedText(cashUsd) : 'n/a', RESTRICTED_TEXT]];
    }
    function today() { var h = T.state && T.state.health; return (h && h.clock && h.clock.accounting_day) || null; }
    function update() {
      var d = data || {};
      S.asOf.textContent = d.as_of ? 'as of ' + T.fmt.time(d.as_of) : '';
      var cash = d.cash || {};
      S.cash.update(Object.keys(cash).map(function (c) { return Object.assign({ currency: c }, cash[c]); }), { empty: 'no cash balances' });
      paintRestricted(cash);
      paintMargin(cash);
      S.ladder.update(d.ladder || d.settlements || [], { empty: 'nothing pending' });
      /* margin & collateral (BM-12): VM posted and received from the served names */
      var m = d.margin || {}; var coll = d.collateral || {};
      T.clear(S.marginKv);
      S.marginKv.appendChild(T.kv([
        ['futures initial margin required / posted', T.money(m, 'futures_required') + ' / ' + T.money(m, 'futures_posted')],
        ['short-option margin (reserved, no cash moves)', T.money(m, m.options_reserved != null || m.options_reserved_c != null ? 'options_reserved' : 'options_required')],
        ['OTC initial margin (IM)', T.money(m, 'otc_im')], ['OTC VM posted', T.money(m, 'otc_vm_posted'), 'variation margin paid to the dealer (the mark fell): collateral, not P&L'],
        ['OTC VM received', T.money(m, 'otc_vm_received'), 'variation margin received from the dealer (the mark rose): collateral, not P&L'],
        ['collateral at lenders (102 %)', T.money(coll, 'held_at_lender')]].concat(restrictedRows((d.cash || {}).USD))));
      var crows = contracts && contracts.length ? contracts : (d.otc || []);
      S.vmBox.hidden = !crows.length;
      S.vm.update(crows.filter(function (c) { return !c.status || c.status === 'OPEN'; }), { empty: 'no OTC contracts' });
      T.clear(S.vmLink);
      if (crows.length) { S.vmLink.appendChild(T.h('span', null, 'terms, marks and resets of every contract: ')); S.vmLink.appendChild(T.h('a', { href: T.router.account(acct, 'markets/otc') }, 'the OTC tab')); }
      /* loans (C26 rows; the obligations rows if that read failed) */
      var lrows = loans != null ? loans : (d.loans || []);
      S.loans.update(lrows, { empty: 'no loans' });
      S.loanNote.textContent = loans == null && (d.loans || []).length ? 'the loan detail (index value, all-in rate, interest to date) could not be loaded; showing the ledger rows' : '';
      if (S.pb) { paintPb(); }
      S.borrows.update(d.borrows || [], { empty: 'no open borrows' });
      /* sweeps of the accounting day (BM-11) */
      var day = today();
      var sw = (d.transfers || []).filter(function (t) { return t.kind === 'SWEEP_IN' || t.kind === 'SWEEP_OUT'; });
      var todays = day ? sw.filter(function (t) { return t.business_date === day; }) : sw.slice(0, 10);
      S.sweeps.update(todays, { empty: 'no sweeps on ' + (day || 'the accounting day') });
      S.sweepHead.textContent = day ? 'accounting day ' + day : '';
      var earlier = sw.filter(function (t) { return todays.indexOf(t) < 0; });
      S.sweepNote.textContent = earlier.length ? earlier.length + ' earlier sweep(s) of this account are in Transactions (latest ' + (earlier[0].business_date || '') + ': ' + (earlier[0].kind === 'SWEEP_IN' ? 'in ' : 'out ') + T.money(earlier[0], 'amount') + ')' : '';
      refreshSweepForm();
    }

    /* ---- VM history of one contract (GET /api/otc/contracts?account_id=&detail=1) */
    function vmHistory(c) {
      var box = T.h('div', null, T.h('div', { class: 'spin' }));
      T.ui.modal({ title: 'VM history ' + c.contract_id, wide: true, body: box, actions: [{ label: 'Close' }] });
      T.api.get('/api/otc/contracts', { account_id: acct, detail: 1 }).then(function (list) {
        var v = (Array.isArray(list) ? list : []).filter(function (x) { return x.contract_id === c.contract_id; })[0];
        T.clear(box);
        if (!v) { box.appendChild(T.h('div', { class: 'empty' }, 'contract not found')); return; }
        box.appendChild(T.h('div', { class: 'line' }, (v.kind_label || v.kind) + ' . ' + (v.side_text || '') + ' . IM ' + T.money(v, 'im_balance') + ' . VM posted ' + T.money(v, 'vm_posted') + ' . received ' + T.money(v, 'vm_received')));
        var hist = ((v.history || {}).vm || []).slice().reverse();
        var g = T.h('div'); box.appendChild(g);
        T.components.grid.mount(g, { tools: false, rows: hist, empty: 'no variation margin exchanged yet (the first VM runs at 17:05 ET after the trade date)', columns: [
          { key: 'vm_date', label: 'date', cls: 'l' }, { key: 'mark', label: 'mark', text: function (r) { return T.money(r, 'mark'); } },
          { key: 'delta', label: 'VM (+ received / - paid)', html: function (r) { return '<span class="' + T.sign(r, 'delta') + '">' + T.esc(T.money(r, 'delta')) + '</span>'; }, text: function (r) { return T.money(r, 'delta'); } },
          { key: 'mode', label: 'mode', cls: 'l' }, { key: 'entry_id', label: 'journal entry', cls: 'l', text: function (r) { return r.entry_id == null ? '' : String(r.entry_id); } }] });
        box.appendChild(T.h('div', { class: 'line' }, T.h('a', { href: T.router.account(acct, 'markets/otc') }, 'open the OTC tab'), ' for the terms, resets and payments.'));
      }, function (e) { T.clear(box); box.appendChild(T.h('div', { class: 'error' }, T.api.fail(e, 'VM history'))); });
    }

    /* ---- the loan form (Credit Instruments only; BM-19 / OA-19): lender from the bank directory, its currencies and tenors */
    function loanForm() {
      var f = T.h('div', { style: 'margin-top:8px' });
      f.appendChild(T.h('h2', null, 'new currency loan', T.h('small', null, 'index + the lender\'s assumed spread; JPY / CHF / CAD / AUD indices are monthly proxies (as-of month shown)')));
      var lender = T.h('select', { id: 'loan-lender', 'aria-label': 'lender' }, T.h('option', { value: '' }, 'loading the banks ...'));
      var ccy = T.h('select', { id: 'loan-ccy', 'aria-label': 'currency' });
      var tenor = T.h('select', { id: 'loan-tenor', 'aria-label': 'tenor' });
      var amt = T.h('input', { type: 'text', id: 'loan-amount', inputmode: 'decimal', placeholder: 'principal', style: 'width:160px' });
      var terms = T.h('div', { class: 'line' });
      var msg = T.h('div', { class: 'msg' });
      function bank() { return (banks || []).filter(function (b) { return b.counterparty_id === lender.value; })[0] || null; }
      function fillBank() {
        var b = bank(); var lt = (b && b.terms && b.terms.loan) || {};
        var keepC = ccy.value, keepT = tenor.value;
        T.clear(ccy); Object.keys(lt.spreads || {}).sort().forEach(function (c) { ccy.appendChild(T.h('option', { value: c }, c)); });
        if (keepC) { ccy.value = keepC; } if (!ccy.value && ccy.options.length) { ccy.value = (lt.spreads || {}).USD != null ? 'USD' : ccy.options[0].value; }
        T.clear(tenor); (lt.tenors || ['OPEN']).forEach(function (t) { tenor.appendChild(T.h('option', { value: t }, t === 'OPEN' ? 'open (no maturity)' : t)); });
        if (keepT) { tenor.value = keepT; } if (!tenor.value && tenor.options.length) { tenor.value = tenor.options[0].value; }
        var sp = (lt.spreads || {})[ccy.value];
        terms.textContent = b ? (b.label || b.name) + ': spread ' + (sp != null ? T.fmt.rate(sp) : 'n/a') + ' over the ' + ccy.value + ' index' + (lt.max_usd != null ? ' . facility up to USD ' + T.fmt.money(lt.max_usd, 0) : '') + ' . assumed terms (editable on the Credit Counterparties tab)' : '';
      }
      lender.addEventListener('change', fillBank); ccy.addEventListener('change', fillBank);
      T.api.get('/api/credit/counterparties', { type: 'BANK' }).then(function (x) {
        banks = (Array.isArray(x) ? x : ((x && (x.rows || x.counterparties)) || [])).filter(function (b) { return b && b.terms && b.terms.loan; });
        T.clear(lender);
        banks.forEach(function (b) { lender.appendChild(T.h('option', { value: b.counterparty_id }, b.label || b.name)); });
        if (!banks.length) { lender.appendChild(T.h('option', { value: '' }, 'no bank in the directory')); }
        fillBank();
      }, function (e) { T.clear(lender); lender.appendChild(T.h('option', { value: '' }, 'banks unavailable')); msg.textContent = T.api.fail(e, 'bank directory'); });
      var btn = T.h('button', { class: 'btn', onclick: function () {
        btn.disabled = true; msg.textContent = '';
        var body = { account_id: acct, currency: ccy.value, amount: amt.value.trim().replace(/,/g, ''), counterparty_id: lender.value || null, tenor: tenor.value || null };
        T.api.post('/api/credit/loans', body).then(function (r) {
          btn.disabled = false; amt.value = '';
          T.ui.toast('loan ' + (r.loan && r.loan.loan_id || '') + ' drawn at ' + loanRateText(r.rate), 'ok', 6000); load(); T.poll.kick('account_detail');
        }, function (e) { btn.disabled = false; msg.textContent = failText(e, 'loan'); });
      } }, 'Draw loan');
      f.appendChild(T.form([T.field('lender', lender), T.field('currency', ccy), T.field('tenor', tenor), T.field('principal', amt), ['', btn]]));
      f.appendChild(terms); f.appendChild(msg);
      return f;
    }

    /* ---- prime-broker financing (PO-17): state + switch inline next to Draw / Repay */
    function pbForm() {
      var f = T.h('div', { style: 'margin-top:8px' });
      f.appendChild(T.h('h2', null, 'prime-broker financing', T.h('small', null, 'SOFR + the schedule\'s spread, nightly; headroom = 50 % of the long market value here - drawn - OTC margin (capped by the limit)')));
      var state = T.h('div', { class: 'pbstate' });
      var sw = T.h('input', { type: 'checkbox', id: 'pb-financing', 'aria-label': 'PB financing on', style: 'width:auto;margin:0' });
      var swLab = T.h('span', { class: 'row', style: 'gap:6px;align-items:center' }, sw, T.h('span', { style: 'cursor:pointer', onclick: function () { if (!sw.disabled) { sw.click(); } } }, 'financing on (draw up to the headroom)'));
      var amt = T.h('input', { type: 'text', id: 'pb-amount', inputmode: 'decimal', placeholder: 'amount USD', style: 'width:160px' });
      var msg = T.h('div', { class: 'msg' });
      sw.addEventListener('change', function () {
        var want = sw.checked; sw.disabled = true; msg.textContent = '';
        T.api.patch('/api/accounts/' + encodeURIComponent(acct), { pb_financing: want }).then(function () {
          sw.disabled = false; T.ui.toast('PB financing ' + (want ? 'switched on' : 'switched off'), 'ok'); T.poll.kick('accounts'); load();
        }, function (e) { sw.disabled = false; sw.checked = !want; msg.textContent = failText(e, 'PB financing switch'); });
      });
      function go(kind) {
        return function () {
          msg.textContent = '';
          T.api.post('/api/credit/pb/' + encodeURIComponent(acct) + '/' + kind, { amount: amt.value.trim().replace(/,/g, '') }).then(function (r) {
            amt.value = '';
            var res = (r && r.result) || r || {};
            T.ui.toast('PB financing ' + (kind === 'draw' ? 'drawn ' : 'repaid ') + T.money(res, 'amount') + ' . financed now ' + T.money(res, res.financed != null || res.financed_c != null ? 'financed' : 'financed_left'), 'ok', 5000); load(); T.poll.kick('account_detail'); T.poll.kick('accounts');
          }, function (e) { msg.textContent = failText(e, 'PB ' + kind); });
        };
      }
      f.appendChild(state);
      f.appendChild(T.form([T.field('switch', swLab), T.field('amount', amt), ['', T.h('span', { class: 'row' }, T.h('button', { class: 'btn', onclick: go('draw') }, 'Draw'), T.h('button', { class: 'btn', onclick: go('repay') }, 'Repay'))]]));
      f.appendChild(msg);
      S.pb = { state: state, sw: sw };
      return f;
    }
    function paintPb() {
      var a = (data && data.account) || {};
      var bp = ((data && data.buying_power) || {}).USD || {};
      var on = !!Number(a.pb_financing);
      if (document.activeElement !== S.pb.sw && !S.pb.sw.disabled) { S.pb.sw.checked = on; }
      var row = (loans || []).filter(function (l) { return l.kind === 'PB_MARGIN'; })[0] || null;
      var limit = T.num(a, 'pb_limit');
      T.clear(S.pb.state);
      function item(k, v, title, cls) { S.pb.state.appendChild(T.h('span', { title: title || '' }, k + ' ', T.h('b', { class: cls || '' }, v))); }
      item('financing', on ? 'ON' : 'OFF', on ? 'the account may draw up to its headroom' : 'switch it on to draw; it starts off on every new prime-broker account', on ? 'on' : 'off');
      item('headroom', T.money(bp, bp.pb_headroom_gross != null || bp.pb_headroom_gross_c != null ? 'pb_headroom_gross' : 'pb_headroom'), 'available to draw now; after what working orders already need: ' + T.money(bp, 'pb_headroom'));
      item('limit', limit ? T.money(a, 'pb_limit') : 'no cap', '0 = no cap (Settings > Accounts)');
      item('drawn', row ? T.money(row, 'principal') : '0.00');
      if (row) {
        item('rate', loanRowRate(row), 'SOFR as published (its date) + the prime broker\'s spread = the all-in rate');
        item('interest posted', T.money(row, 'accrued'), 'accrued nightly, paid monthly');
      }
    }

    /* ---- repay (C26): an amount, or the full payoff ({full: true}: the nights owed are posted first) */
    function repay(loan) {
      var full = T.h('input', { type: 'checkbox', id: 'repay-full', checked: true, style: 'width:auto;justify-self:start;margin:0' });
      var amt = T.h('input', { type: 'text', id: 'repay-amount', inputmode: 'decimal', value: '', placeholder: 'amount (' + loan.currency + ')', style: 'width:160px', disabled: true });
      var msg = T.h('div', { class: 'msg' });
      full.addEventListener('change', function () { amt.disabled = full.checked; if (!full.checked && !amt.value) { amt.value = T.money(loan, 'principal').replace(/,/g, ''); } });
      var info = T.kv([['principal', loan.currency + ' ' + T.money(loan, 'principal')], ['interest to date', T.money(loan, 'accrued') + (loan.pending_nights && loan.pending_nights.length ? ' (incl. ' + loan.pending_nights.length + ' night(s) posted first)' : '')],
        ['payoff now', loan.payoff != null || loan.payoff_c != null ? loan.currency + ' ' + T.money(loan, 'payoff') : 'n/a - ' + (loan.payoff_note || 'unknown'), loan.payoff_note || ''], ['rate', loanRowRate(loan)]]);
      T.ui.modal({ title: 'Repay ' + loan.loan_id, body: T.h('div', null, info, T.form([T.field('pay off in full', full, 'principal + interest as it stands at the repayment'), T.field('or an amount (' + loan.currency + ')', amt)]), msg,
        T.h('div', { class: 'hint' }, 'Interest is paid first, then principal; the cash must be settled in ' + loan.currency + ' in this account (a USD shortfall is swept from Treasury; another currency is bought on the FX desk first).')),
        actions: [{ label: 'Cancel' }, { label: 'Repay', cls: 'primary', onClick: function (m, b) {
          b.disabled = true; msg.textContent = '';
          var body = full.checked ? { full: true } : { amount: amt.value.trim().replace(/,/g, '') };
          T.api.post('/api/credit/loans/' + encodeURIComponent(loan.loan_id) + '/repay', body).then(function (r) {
            m.close();
            T.ui.toast('repaid ' + loan.loan_id + ': interest ' + T.money(r, 'paid_interest') + ', principal ' + T.money(r, 'paid_principal') + (r.closed ? ' - loan closed' : ''), 'ok', 6000);
            load(); T.poll.kick('account_detail');
          }, function (e) {
            b.disabled = false;
            var short = e && e.details && (e.details.shortfall || e.details.shortfall_c != null) ? ' (short by ' + T.money(e.details, 'shortfall') + ')' : '';
            msg.textContent = failText(e, 'repay') + short;
          });
        } }] });
    }

    /* ---- sweep-out switch and buffer: built once; the poll refreshes them only while untouched (PO-06) */
    function sweepForm() {
      var a = (data && data.account) || {};
      var chk = T.h('input', { type: 'checkbox', id: 'sweep-out', style: 'width:auto;justify-self:start;margin:0' }); chk.checked = !!(a.sweep_out == null ? true : a.sweep_out);
      var buf = T.h('input', { type: 'text', id: 'sweep-buffer', inputmode: 'decimal', value: T.money(a, 'sweep_buffer') === 'n/a' ? '' : T.money(a, 'sweep_buffer').replace(/,/g, ''), style: 'width:140px' });
      var dirty = false;
      [chk, buf].forEach(function (x) { x.addEventListener('input', function () { dirty = true; }); x.addEventListener('change', function () { dirty = true; }); });
      var save = T.h('button', { class: 'btn small', onclick: function () {
        save.disabled = true;
        T.api.patch('/api/accounts/' + encodeURIComponent(acct), { sweep_out: chk.checked, sweep_buffer: buf.value.trim().replace(/,/g, '') || '0' }).then(function () { T.ui.toast('sweep settings saved', 'ok'); save.disabled = false; dirty = false; load(); }, function (e) { save.disabled = false; T.api.fail(e, 'sweep settings'); });
      } }, 'Save');
      S.sweepForm = { chk: chk, buf: buf, isDirty: function () { return dirty; } };
      return T.form([T.field('sweep-out after 17:30 ET', chk, 'idle cash goes back to Treasury after the close'), T.field('buffer kept (USD)', buf), ['', save]]);
    }
    function refreshSweepForm() {
      var f = S.sweepForm; var a = (data && data.account) || {};
      if (!f || f.isDirty() || document.activeElement === f.chk || document.activeElement === f.buf) { return; }
      f.chk.checked = !!(a.sweep_out == null ? true : a.sweep_out);
      var v = T.money(a, 'sweep_buffer'); f.buf.value = v === 'n/a' ? '' : v.replace(/,/g, '');
    }

    load();
    T.poll.page('cash', 15000, function () { return data ? load() : Promise.resolve(); });
    T.pages.cash.unmount = function () { dead = true; T.pages.cash.unmount = null; };
  }
  T.pages.cash = { render: render, unmount: null };
})();
