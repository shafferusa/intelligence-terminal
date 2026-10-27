/* Credit Instruments marketplace (BUILD2_PLAN P08; owner 2026-10-21: "why isnt there counterparties for credit instruments?").
 * Registers T.marketPlugins.CREDIT (C11): pages/markets.js hands it the Markets body of a Credit Instruments account.
 *
 * Tabs (each one a desk of components/creditdesk.js; the private-credit tab is components/privatecredit.js):
 * - Currency loans: draw from a bank of the counterparty directory (assumed terms), the book's open loans with the index
 *   and its date, the spread, the all-in rate, the interest to date and the payoff; repay part or pay off in full.
 * - Repo & reverse repo: borrow cash against the Treasuries held in Debt Instruments (the face is pledged) or lend cash
 *   against a dealer's Treasury, with a primary dealer; roll and close; the daily collateral mark both ways.
 * - Securities lending: lend long US equities through an agent lender; recall.
 * - Stock borrows: every open borrow of the book in any account, with lender, fee, rebate, collateral, recall notice and
 *   Return; locate and borrow ahead of a short.
 * - Private credit: direct loans to companies of the private company master.
 * - Rates: SOFR, the policy indices and the high-yield spreads with their dates; the all-in rates the assumed terms give.
 * - Counterparties: the directory with its assumed, editable terms and the book's exposure per counterparty.
 * Every figure comes from the server; nothing is computed here.  ASCII only, no framework.
 */
(function () {
  'use strict';
  var T = window.T;
  var TABS = [{ key: 'loans', label: 'Currency loans' }, { key: 'repo', label: 'Repo & reverse repo' }, { key: 'lending', label: 'Securities lending' },
              { key: 'borrows', label: 'Stock borrows' }, { key: 'private', label: 'Private credit' }, { key: 'rates', label: 'Rates' },
              { key: 'counterparties', label: 'Counterparties' }];
  function render(el, ctx) {
    var tab = TABS.some(function (t) { return t.key === ctx.tab; }) ? ctx.tab : 'loans';
    var root = T.h('div', { class: 'credit' }); el.appendChild(root);
    root.appendChild(T.tabs(TABS, tab, function (k) { ctx.setTab(k); }));
    var body = T.h('div', { class: 'credit-body' }); root.appendChild(body);
    var desk = T.components.creditdesk;
    if (!desk || typeof desk.mount !== 'function') {
      T.notBuilt(body, 'the credit desk (loans, repo, lending, borrows, rates, counterparties)');
      return null;
    }
    var handle = null;
    try { handle = desk.mount(body, { tab: tab, account_id: ctx.account_id, acct: ctx.acct, book: ctx.book, setTab: ctx.setTab }); }
    catch (e) { console.error('credit desk failed', e); body.appendChild(T.h('div', { class: 'error' }, 'this tab failed to draw: ' + (e.message || e))); }
    return { unmount: function () { if (handle && typeof handle.unmount === 'function') { handle.unmount(); } } };
  }
  T.marketPlugins.CREDIT = { render: render, tabs: TABS,
                             dockHint: 'Credit Instruments: each tab carries its own ticket - draw a loan, book a repo, lend or borrow shares, commit a private loan' };
  T.pages.markets_credit = { render: render, tabs: TABS };
})();
