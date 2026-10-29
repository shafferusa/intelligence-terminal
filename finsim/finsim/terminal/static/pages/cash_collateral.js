/* Cash & Collateral (owner directive 2026-10-27 sections 12 / 14 / 16; FINSIM1_PLAN P6 task 7: UI-10, PORTFOLIO-16; tasks 3-6's displays).
 * #/cash (Shift+F5; #/cash?account=<id> narrows it to one account).  The book's cash, collateral, margin and funding in one place:
 * - the tiles: restricted (collateral received), Reg T margin cash reserved, margin required / posted / excess, collateral posted /
 *   received, encumbered, open calls, failed settlements;
 * - cash by account and currency (Accounting's cash tab, C9f T.pages.accounting.renderCashTab, when that page is in the build) and the
 *   available / restricted / margin / reserved split of every account;
 * - the settlement ladder with each settlement's state (settling / failed / resolved);
 * - short sale proceeds (GL 2600) vs the cash collateral the lenders hold (GL 1400), per short and in total, the fund's own cash
 *   between them;
 * - interest and financing by type: today, month to date, since inception;
 * - collateral by counterparty (per CSA: initial and variation margin, cash vs securities, net VM; the FCM; the stock lenders; the
 *   securities-lending agents) and margin per account with the Reg T terms;
 * - margin calls (met / open / unmet, with their funding transfers); CSA pledges (pledge / release); Treasury bills;
 * - the assumptions editor: Reg T terms per account (margin.terms) and each CSA's "reuse allowed" flag.
 * Reads GET /api/collateral/cash?book=[&scope=<account>]; writes POST /api/margin/terms, /api/collateral/csa, /api/collateral/pledges,
 * /api/collateral/pledges/{id}/release.  Every route is the collateral module's: none is called unless T.hasModule('collateral')
 * (rule 5); otherwise the page says the figures are unavailable in this build.  Every number is the server's; this file formats.
 * Polled every 15 s; the forms are built once and keep what is typed.  ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  var CSS = '.ccp .sub{display:block;font-size:10px;line-height:1.25;color:var(--fg3);max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
            '.ccp .line{font-family:var(--mono);font-size:11.5px;color:var(--fg2);margin:4px 0;line-height:1.5}.ccp .line b{color:var(--fg)}' +
            '.ccp .grid-th-wrap th{white-space:normal;max-width:100px;vertical-align:bottom}.ccp .msg{font-family:var(--mono);font-size:11px;min-height:15px;margin:4px 0}' +
            '.ccp .terms-row{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;margin:4px 0;font-family:var(--mono);font-size:11.5px}' +
            '.ccp .terms-row input[type=text]{width:64px}.ccp .terms-row .nm{min-width:150px;color:var(--fg)}.ccp h3{font-size:11px;margin:10px 0 4px;color:var(--fg2)}';
  function ensureCss() { if (document.getElementById('cashcoll-css')) { return; } var s = document.createElement('style'); s.id = 'cashcoll-css'; s.textContent = CSS; document.head.appendChild(s); }
  function cell(main, sub, title) { return T.esc(main == null ? '' : main) + (sub ? '<span class="sub" title="' + T.esc(title || sub) + '">' + T.esc(sub) + '</span>' : ''); }
  function m(row, name) { return row && (row[name + '_c'] != null || (row[name] != null && row[name] !== '')) ? T.money(row, name) : 'n/a'; }
  function signed(row, name) { return '<span class="' + T.sign(row, name) + '">' + T.esc(m(row, name)) + '</span>'; }
  function pct(f) { var n = Number(f); return isFinite(n) ? (Math.round(n * 10000) / 100).toFixed(2).replace(/\.?0+$/, '') + ' %' : 'n/a'; }
  var STAGE_CHIP = { SETTLING: 'settling', FAILED: 'failed', SETTLED: 'filled', RESOLVED: 'filled', OPEN: 'held', MET: 'filled', UNMET: 'failed', PLEDGED: 'held', RELEASED: 'cancelled' };
  function chip(stage, label, title) { return T.stageChip(STAGE_CHIP[stage] || 'queued', label || String(stage || '').toLowerCase(), title || '').outerHTML; }

  function render(el, route) {
    ensureCss();
    var q = (route && route.query) || {};
    var scope = q.account || q.scope || null;
    var dead = false, data = null, S = null;
    var book = T.book.current();
    T.pageHead(el, 'Cash & Collateral', (scope ? T.accountName(scope) + ' . ' : '') + (book ? (book.name || book.book_code) : ''),
      scope ? [T.h('a', { class: 'btn', href: '#/cash', title: 'the whole book' }, 'whole book'), T.h('a', { class: 'btn', href: T.router.account(scope, 'cash'), title: 'the account\'s own Cash & financing page' }, 'account cash page')] : []);
    var root = T.h('div', { class: 'ccp' }); el.appendChild(root);
    if (!(T.hasModule && T.hasModule('collateral'))) {
      root.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'cash, collateral and margin'),
        T.h('div', { class: 'empty' }, 'unavailable: these figures come with the collateral module, which is not in this build.'),
        T.h('div', { class: 'line' }, 'Each account\'s cash, financing and collateral stay on its own Cash & financing page; the book\'s cash on the ', T.h('a', { href: '#/treasury' }, 'Treasury'), ' page.')));
      T.pages.cash_collateral.unmount = function () { T.pages.cash_collateral.unmount = null; };
      return;
    }
    var spin = T.h('div', { class: 'spin' }); root.appendChild(spin);
    var err = T.h('div', { class: 'error', hidden: true }); root.appendChild(err);
    function gridIn(parent, spec, cls) { var g = T.h('div', { class: cls || '' }); parent.appendChild(g); return T.components.grid.mount(g, Object.assign({ tools: false, rows: [], empty: 'loading ...' }, spec)); }
    function panel(title, small, id) { var p = T.h('div', { class: 'panel', id: id || null }, T.h('h2', null, title, small ? T.h('small', null, small) : null)); root.appendChild(p); return p; }

    function build() {
      S = {};
      S.tiles = T.h('div'); root.appendChild(S.tiles);
      /* ---- cash by account and currency: Accounting's cash tab (C9f) when it is in the build */
      var pc = panel('cash by account and currency', 'Accounting\'s cash view', 'cc-cash');
      S.c9f = T.h('div'); pc.appendChild(S.c9f);
      var acc = T.pages.accounting;
      if (acc && typeof acc.renderCashTab === 'function') {
        try { acc.renderCashTab(S.c9f, { book: T.book.code, account: scope || null }); } catch (e) { console.error('renderCashTab', e); S.c9f.appendChild(T.h('div', { class: 'hint' }, 'Accounting\'s cash view failed to draw: ' + (e.message || e))); }
      } else {
        S.c9f.appendChild(T.h('div', { class: 'hint' }, 'Accounting\'s cash view is not in this build; the split below lists each account\'s cash by currency.'));
      }
      /* ---- the split */
      var ps = panel('available / restricted / reserved', 'per account and currency; available = settled - restricted - Reg T margin cash + receivables due - payables - reserved, floored at 0', 'cc-split');
      S.split = gridIn(ps, { rowKey: function (r) { return r.account_id + ':' + r.currency; }, empty: 'no cash', columns: [
        { key: 'account', label: 'account', cls: 'l', html: function (r) { return cell(r.account_name || r.account_id, r.currency); }, text: function (r) { return (r.account_name || r.account_id) + ' ' + r.currency; } },
        { key: 'settled', label: 'settled', text: function (r) { return m(r, 'settled'); } },
        { key: 'receivable', label: 'receivable', text: function (r) { return m(r, 'receivable'); } },
        { key: 'payable', label: 'payable', text: function (r) { return m(r, 'payable'); } },
        { key: 'reserved', label: 'reserved', text: function (r) { return m(r, 'reserved'); }, title: function () { return 'working orders and the short-option margin'; } },
        { key: 'restricted', label: 'restricted', html: function (r) { return T.num(r, 'restricted') ? '<b>' + T.esc(m(r, 'restricted')) + '</b>' : T.esc(m(r, 'restricted')); }, text: function (r) { return m(r, 'restricted'); },
          title: function () { return (data && data.definitions && data.definitions.restricted) || ''; } },
        { key: 'margin_reserved', label: 'Reg T margin cash', text: function (r) { return m(r, 'margin_reserved'); }, title: function () { return 'the cash the Reg T requirement needs beyond the longs\' loan value: kept, never swept'; } },
        { key: 'available', label: 'available', html: function (r) { return '<span class="pos">' + T.esc(m(r, 'available')) + '</span>'; }, text: function (r) { return m(r, 'available'); } },
        { key: 'beyond', label: 'beyond own cash', cls: 'l', html: function (r) {
          var parts = [];
          if (T.num(r, 'overdrawn')) { parts.push('<span class="neg">overdrawn ' + T.esc(m(r, 'overdrawn')) + '</span>'); }
          if (T.num(r, 'needs')) { parts.push('needs ' + T.esc(m(r, 'needs')) + (T.num(r, 'unfunded') ? ' (<span class="neg">unfunded ' + T.esc(m(r, 'unfunded')) + '</span>)' : ' (Treasury covers it)')); }
          return parts.join(' . ');
        }, text: function (r) { return T.num(r, 'needs') ? m(r, 'needs') : ''; } }] }, 'grid-th-wrap');
      /* ---- settlement ladder + short collateral */
      var two = T.h('div', { class: 'grid2' }); root.appendChild(two);
      S.c9fMounted = !!(acc && typeof acc.renderCashTab === 'function');
      var pl = T.h('div', { class: 'panel', id: 'cc-ladder' }, T.h('h2', null, S.c9fMounted ? 'settlement states' : 'settlement ladder',
        T.h('small', null, S.c9fMounted ? 'failed and resolved settlements (the ladder itself is in the cash view above)' : 'pending settlements by due date, with their state')));
      S.ladder = gridIn(pl, { rowKey: function (r) { return r.settle_id; }, empty: 'nothing pending', columns: [
        { key: 'due_date', label: 'due', cls: 'l' },
        { key: 'account', label: 'account', cls: 'l', text: function (r) { return T.accountName(r.account_id); } },
        { key: 'what', label: 'what', cls: 'l', html: function (r) { return cell(r.instrument_id ? (String(r.fill_side || '').replace(/_/g, ' ').toLowerCase() + ' ' + T.fmt.qty(r.fill_qty) + ' ' + r.instrument_id) : (r.fx_deal_id ? 'FX deal ' + r.fx_deal_id : (r.ref_id ? 'payment ' + r.ref_id : String(r.leg || 'cash').toLowerCase())), r.settle_id); },
          text: function (r) { return r.instrument_id || r.fx_deal_id || r.ref_id || ''; } },
        { key: 'amount', label: 'amount', html: function (r) { return signed(r, 'amount'); }, text: function (r) { return m(r, 'amount'); } },
        { key: 'stage', label: 'state', cls: 'l', html: function (r) { return chip(r.stage, null, r.label); }, text: function (r) { return r.stage || ''; } }] });
      S.ladderNote = T.h('div', { class: 'line' }); pl.appendChild(S.ladderNote);
      two.appendChild(pl);
      var pk = T.h('div', { class: 'panel', id: 'cc-shorts' }, T.h('h2', null, 'short proceeds vs collateral at the lender', T.h('small', null, 'GL 2600 vs GL 1400')));
      S.shortsKv = T.h('div'); pk.appendChild(S.shortsKv);
      S.shorts = gridIn(pk, { rowKey: function (r) { return r.account_id + ':' + r.instrument_id; }, empty: 'no physical short', columns: [
        { key: 'symbol', label: 'short', cls: 'l', html: function (r) { return cell(r.symbol + ' ' + T.fmt.qty(r.qty), (r.lenders || []).join(', '), (r.borrows || []).join(' ')); }, text: function (r) { return r.symbol; } },
        { key: 'proceeds', label: 'proceeds', text: function (r) { return m(r, 'proceeds'); } },
        { key: 'collateral', label: 'at lender', text: function (r) { return m(r, 'collateral'); } },
        { key: 'fund_cash', label: 'fund cash', text: function (r) { return m(r, 'fund_cash'); }, title: function () { return 'collateral - proceeds: the 2 % excess and the daily marks the fund paid'; } },
        { key: 'short_mv', label: 'short value', html: function (r) { return cell(m(r, 'short_mv'), r.price_basis || ''); }, text: function (r) { return m(r, 'short_mv'); } }] });
      two.appendChild(pk);
      /* ---- interest and financing */
      var pi = panel('interest and financing', 'by business date; income positive, costs negative', 'cc-income');
      S.income = gridIn(pi, { rowKey: function (r) { return r.key; }, empty: 'nothing accrued yet', columns: [
        { key: 'label', label: 'type', cls: 'l', html: function (r) { return cell(r.label, 'GL ' + (r.gls || []).join(', ')); }, text: function (r) { return r.label; } },
        { key: 'today', label: 'today', html: function (r) { return signed(r, 'today'); }, text: function (r) { return m(r, 'today'); } },
        { key: 'mtd', label: 'month to date', html: function (r) { return signed(r, 'mtd'); }, text: function (r) { return m(r, 'mtd'); } },
        { key: 'itd', label: 'since inception', html: function (r) { return signed(r, 'itd'); }, text: function (r) { return m(r, 'itd'); } }] });
      S.incomeNote = T.h('div', { class: 'line' }); pi.appendChild(S.incomeNote);
      /* ---- collateral by counterparty + margin by account */
      var pcp = panel('collateral by counterparty', 'posted / received, net per CSA, cash vs securities, initial vs variation margin, requirement vs eligible', 'cc-counterparties');
      S.cps = gridIn(pcp, { rowKey: function (r) { return r.type + ':' + r.counterparty; }, empty: 'no collateral posted or received', columns: [
        { key: 'counterparty', label: 'counterparty', cls: 'l', html: function (r) { return cell(r.counterparty, { CSA: 'CSA (OTC)', FCM: 'futures clearing', STOCK_LENDER: 'stock lender', AGENT_LENDER: 'securities-lending agent' }[r.type] || r.type); }, text: function (r) { return r.counterparty; } },
        { key: 'im', label: 'IM posted / required', text: function (r) { return T.num(r, 'im_required') || T.num(r, 'im_posted') ? m(r, 'im_posted') + ' / ' + m(r, 'im_required') : ''; } },
        { key: 'vm', label: 'VM posted / received', html: function (r) {
          if (r.type !== 'CSA') { return ''; }
          var net = T.num(r, 'net_vm'); var abs = String(m(r, 'net_vm')).replace(/^-/, '');
          return cell(m(r, 'vm_posted') + ' / ' + m(r, 'vm_received'), (net < 0 ? 'net posted ' + abs : 'net received ' + abs) + (r.reuse_allowed ? ' . reuse allowed' : ''));
        }, text: function (r) { return r.type === 'CSA' ? m(r, 'net_vm') : ''; } },
        { key: 'cash', label: 'cash posted / received', text: function (r) { return m(r, 'cash_posted') + ' / ' + m(r, 'cash_received'); } },
        { key: 'securities', label: 'securities (eligible)', text: function (r) { return T.num(r, 'securities_posted') ? m(r, 'securities_posted') : ''; } },
        { key: 'req', label: 'requirement / eligible', text: function (r) { return T.num(r, 'requirement') || T.num(r, 'eligible') ? m(r, 'requirement') + ' / ' + m(r, 'eligible') : ''; } },
        { key: 'exposure', label: 'exposure', text: function (r) { return T.num(r, 'exposure') ? m(r, 'exposure') : ''; }, title: function () { return 'the short value owed to a stock lender, or the market value of shares lent'; } }] }, 'grid-th-wrap');
      var pm = panel('margin by account', 'required vs posted / eligible; Reg T on the accounts that hold US equities (assumed terms, editable below)', 'cc-margin');
      S.margin = gridIn(pm, { rowKey: function (r) { return r.account_id + ':' + r.key; }, empty: 'no margin requirement', columns: [
        { key: 'account', label: 'account', cls: 'l', html: function (r) { return cell(r.account_name, r.counterparty); }, text: function (r) { return r.account_name; } },
        { key: 'label', label: 'requirement', cls: 'l', html: function (r) { return cell(r.label, r.basis, r.basis); }, text: function (r) { return r.label; } },
        { key: 'required', label: 'required', text: function (r) { return m(r, 'required'); } },
        { key: 'posted', label: 'posted / eligible', text: function (r) { return m(r, 'posted'); } },
        { key: 'excess', label: 'excess', html: function (r) { return signed(r, 'excess'); }, text: function (r) { return m(r, 'excess'); } }] });
      S.regt = T.h('div'); pm.appendChild(S.regt);
      /* ---- margin calls */
      var pmc = panel('margin calls', 'after the close (17:20 ET); due the next business day 10:00 ET', 'cc-calls');
      S.calls = gridIn(pmc, { rowKey: function (r) { return r.call_id; }, empty: 'no margin call yet', columns: [
        { key: 'issued', label: 'issued', cls: 'l', html: function (r) { return cell(r.business_date, r.call_id); }, text: function (r) { return r.business_date; } },
        { key: 'kind', label: 'call', cls: 'l', html: function (r) { return cell(r.kind_label, r.account_name || r.account_id); }, text: function (r) { return r.kind_label; } },
        { key: 'counterparty', label: 'counterparty', cls: 'l' },
        { key: 'amount', label: 'amount', text: function (r) { return m(r, 'amount'); } },
        { key: 'due', label: 'due', cls: 'l', text: function (r) { return r.due || ''; } },
        { key: 'status', label: 'status', cls: 'l', html: function (r) { return chip(r.status, r.status_label, r.note || ''); }, text: function (r) { return r.status; } },
        { key: 'funding', label: 'funding', cls: 'l', html: function (r) {
          var t = (r.transfers || []).map(function (x) { return (x.kind === 'SWEEP_IN' ? 'sweep ' : 'transfer ') + x.transfer_id + ' ' + T.money(x, 'amount'); });
          return cell(t.length ? t.join(', ') : (r.status === 'MET' ? 'own cash' : 'none yet'), r.basis || r.note || '', r.basis || '');
        }, text: function (r) { return (r.transfers || []).map(function (x) { return x.transfer_id; }).join(' '); } }] });
      pmc.appendChild(T.h('div', { class: 'line' }, (data && data.definitions && data.definitions.calls) || 'positions are never sold for you'));
      /* ---- pledges + bills */
      var pp = panel('CSA securities pledges', 'US Treasuries pledged to a prime broker; no GL moves; a pledged face cannot be sold', 'cc-pledges');
      S.pledges = gridIn(pp, { rowKey: function (r) { return r.pledge_id; }, empty: 'no pledge', columns: [
        { key: 'pledge_id', label: 'pledge', cls: 'l', html: function (r) { return cell(r.symbol, (r.account_name || r.account_id) + ' -> ' + (r.pledged_to_name || r.pledged_to), r.name); }, text: function (r) { return r.pledge_id; } },
        { key: 'face', label: 'face', text: function (r) { return T.fmt.qty(r.face); } },
        { key: 'mv', label: 'market value', html: function (r) { return cell(m(r, 'market_value'), r.price_basis || r.price_note || ''); }, text: function (r) { return m(r, 'market_value'); } },
        { key: 'haircut', label: 'haircut', text: function (r) { return r.haircut_text || pct(r.haircut); } },
        { key: 'eligible', label: 'eligible', text: function (r) { return m(r, 'eligible'); } },
        { key: 'status', label: 'status', cls: 'l', html: function (r) { return chip(r.status); }, text: function (r) { return r.status; } },
        { key: 'act', label: ' ', cls: 'l', html: function (r) { return r.status === 'PLEDGED' ? '<button class="btn small" data-act="release">Release</button>' : ''; }, text: function () { return ''; } }],
        onClick: function (r, e) { if (e.target.closest && e.target.closest('button[data-act="release"]')) { release(r); } } });
      pp.appendChild(pledgeForm());
      pp.appendChild(T.h('div', { class: 'hint' }, 'Releasing cash initial margin against pledged securities (collateral substitution) is not in this version.'));
      var pb = panel('Treasury securities', 'bills held in the book\'s accounts: cash equivalents', 'cc-bills');
      S.billsLine = T.h('div', { class: 'line' }); pb.appendChild(S.billsLine);
      S.bills = gridIn(pb, { rowKey: function (r) { return r.account_id + ':' + r.instrument_id; }, empty: 'no Treasury bill', columns: [
        { key: 'symbol', label: 'bill', cls: 'l', html: function (r) { return cell(r.symbol, r.account_name, r.name); }, text: function (r) { return r.symbol; } },
        { key: 'face', label: 'face', text: function (r) { return T.fmt.qty(r.face); } },
        { key: 'mv', label: 'market value', html: function (r) { return cell(m(r, 'market_value'), r.price_basis || r.price_note || ''); }, text: function (r) { return m(r, 'market_value'); } },
        { key: 'maturity', label: 'maturity', cls: 'l', html: function (r) { return cell(r.maturity || 'n/a', r.days_to_maturity != null ? r.days_to_maturity + ' days' : ''); }, text: function (r) { return r.maturity || ''; } },
        { key: 'accreted', label: 'discount accreted', text: function (r) { return m(r, 'accreted_discount'); }, title: function () { return 'the purchase discount accreted straight-line to today (display only: the ledger carries the bill at cost)'; } },
        { key: 'pledged', label: 'pledged face', text: function (r) { return r.pledged_face ? T.fmt.qty(r.pledged_face) : ''; } }] });
      /* ---- assumptions */
      var pa = panel('assumptions', 'Reg T terms per account and each CSA\'s reuse flag (assumed, editable; every change is recorded)', 'cc-terms');
      S.terms = T.h('div'); pa.appendChild(S.terms);
      S.termsMsg = T.h('div', { class: 'msg' }); pa.appendChild(S.termsMsg);
    }

    function tiles() {
      var s = (data && data.summary) || {};
      T.clear(S.tiles);
      S.tiles.appendChild(T.tiles([
        { k: 'restricted', v: m(s, 'restricted'), s: 'collateral received: not available, never swept', title: (data.definitions || {}).restricted || '' },
        { k: 'Reg T margin cash', v: m(s, 'margin_reserved'), s: 'kept for the Reg T requirement', title: (data.definitions || {}).regt || '' },
        { k: 'margin required', v: m(s, 'margin_required'), s: 'posted / eligible ' + m(s, 'margin_posted') + ' . ' + (T.num(s, 'margin_excess') < 0 ? 'deficit ' : 'excess ') + m(s, 'margin_excess'),
          cls: T.num(s, 'margin_excess') < 0 ? 'neg' : '', title: (data.definitions || {}).margin || '' },
        { k: 'collateral posted', v: m(s, 'collateral_posted'), s: 'received ' + m(s, 'collateral_received'), title: (data.definitions || {}).collateral || '' },
        { k: 'encumbered', v: m(s, 'encumbered'), s: 'pledged and lent securities at market', title: (data.definitions || {}).encumbered || '' },
        { k: 'open calls', v: String(s.open_calls == null ? 'n/a' : s.open_calls) + (T.num(s, 'open_calls') ? ' . ' + m(s, 'open_calls') : ''), s: s.unmet_calls ? s.unmet_calls + ' unmet' : 'unmet 0',
          cls: s.open_calls || s.unmet_calls ? 'neg' : '' },
        { k: 'failed settlements', v: String(s.failed_settlements == null ? 'n/a' : s.failed_settlements), cls: s.failed_settlements ? 'neg' : '',
          href: s.failed_settlements && T.pages.completed ? '#/completed?status=failed' : null },
        { k: 'futures initial margin', v: m(data, 'futures_im'), s: 'posted at the FCM (GL 1300)' }
      ]));
    }
    function marginRows() {
      var out = [];
      (((data && data.summary) || {}).by_account || []).forEach(function (a) {
        (a.components || []).forEach(function (c) { out.push(Object.assign({ account_id: a.account_id, account_name: a.name }, c)); });
      });
      return out;
    }
    function regtLines() {
      T.clear(S.regt);
      (((data && data.summary) || {}).regt || []).forEach(function (v) {
        if (!v.short_mv_c && !Number(v.short_mv)) { return; }
        var t = v.terms || {};
        S.regt.appendChild(T.h('div', { class: 'line', title: v.definition || '' },
          T.h('b', null, (v.name || v.account_id) + ' Reg T: '),
          'short ' + m(v, 'short_mv') + ' x ' + (t.initial_text || pct(t.initial_pct)) + ' = requirement ' + m(v, 'requirement') + ' (maintenance ' + (t.maintenance_text || '') + ' ' + m(v, 'maintenance') + ')'
          + '; eligible = longs ' + m(v, 'long_mv') + ' x ' + (t.long_loan_value_text || '') + ' = ' + m(v, 'long_loan_value') + ' + free cash ' + m(v, 'free_cash') + ' = ' + m(v, 'eligible')
          + (T.num(v, 'deficit') ? '; DEFICIT ' + m(v, 'deficit') : '; excess ' + m(v, 'excess')) + '; margin cash reserved ' + m(v, 'margin_cash') + ' (' + (t.source || '') + ')'));
        (v.notes || []).forEach(function (n) { S.regt.appendChild(T.h('div', { class: 'hint' }, n)); });
      });
    }
    function update() {
      var d = data || {};
      tiles();
      S.split.update(d.cash || [], { empty: 'no cash' });
      var lrows = S.c9fMounted ? (d.ladder || []).filter(function (r) { return r.stage === 'FAILED' || r.stage === 'RESOLVED' || r.fail_state; }) : (d.ladder || []);
      S.ladder.update(lrows, { empty: S.c9fMounted ? 'no failed settlement' : 'nothing pending' });
      var failed = (d.ladder || []).filter(function (r) { return r.stage === 'FAILED'; }).length;
      S.ladderNote.textContent = failed ? failed + ' failed: a simulated counterparty fail waits for your resolution in Completed Trades; an unfunded one is retried automatically' : '';
      var sh = d.shorts || {};
      T.clear(S.shortsKv);
      S.shortsKv.appendChild(T.kv([['short sale proceeds (2600)', m(sh, 'proceeds')], ['cash collateral at the lenders (1400)', m(sh, 'collateral')],
        ['fund cash between them', m(sh, 'fund_cash'), sh.note || '']]));
      S.shorts.update(sh.rows || [], { empty: 'no physical short' });
      var inc = d.income || {};
      S.income.update(inc.rows || [], { empty: 'nothing accrued yet' });
      S.incomeNote.textContent = (inc.today ? 'today = business date ' + inc.today + '; month from ' + (inc.month_start || '') + '. ' : '') + (inc.note || '');
      S.cps.update(((d.summary || {}).by_counterparty) || [], { empty: 'no collateral posted or received' });
      S.margin.update(marginRows(), { empty: 'no margin requirement' });
      regtLines();
      S.calls.update(d.calls || [], { empty: 'no margin call yet' });
      S.pledges.update(((d.pledges || {}).rows) || [], { empty: 'no pledge' });
      refreshPledgeForm();
      var b = d.treasury_securities || {};
      S.billsLine.textContent = b.line || '';
      S.bills.update(b.rows || [], { empty: 'no Treasury bill' });
      paintTerms();
    }

    /* ---- the pledge form: an eligible holding, a face, the prime broker */
    function pledgeForm() {
      var f = T.h('div', { style: 'margin-top:8px' });
      f.appendChild(T.h('h3', null, 'pledge a Treasury under a CSA'));
      S.pHold = T.h('select', { id: 'pledge-holding', 'aria-label': 'holding' });
      S.pTo = T.h('select', { id: 'pledge-to', 'aria-label': 'prime broker' });
      S.pFace = T.h('input', { type: 'text', id: 'pledge-face', inputmode: 'numeric', placeholder: 'face', style: 'width:140px' });
      S.pMsg = T.h('div', { class: 'msg' });
      var btn = T.h('button', { class: 'btn', id: 'pledge-submit' }, 'Pledge');
      btn.addEventListener('click', function () {
        var hold = S.pHold.value ? JSON.parse(S.pHold.value) : null;
        S.pMsg.textContent = '';
        if (!hold) { S.pMsg.textContent = 'pick a Treasury you hold'; return; }
        if (!S.pTo.value) { S.pMsg.textContent = 'pick the prime broker whose CSA it secures'; return; }
        var face = S.pFace.value.trim().replace(/,/g, '');
        if (!/^\d+$/.test(face) || Number(face) <= 0) { S.pMsg.textContent = 'face: a whole positive amount, e.g. 1000000'; return; }
        btn.disabled = true;
        T.api.post('/api/collateral/pledges', { account_id: hold.account_id, instrument_id: hold.instrument_id, face: face, pledged_to: S.pTo.value }).then(function (r) {
          btn.disabled = false; S.pFace.value = '';
          var p = (r && r.pledge) || {};
          T.ui.toast('pledged ' + T.fmt.qty(p.face) + ' ' + (p.symbol || '') + ': eligible ' + T.money(p, 'eligible') + ' after a ' + (p.haircut_text || '') + ' haircut', 'ok', 6000);
          load();
        }, function (e) { btn.disabled = false; S.pMsg.textContent = T.api.describe(e); });
      });
      f.appendChild(T.form([T.field('holding', S.pHold, 'US Treasuries held in the book, with the face free to pledge (settled, not pledged in a repo or a CSA)'),
        T.field('prime broker', S.pTo), T.field('face', S.pFace), ['', btn]]));
      f.appendChild(S.pMsg);
      return f;
    }
    function refreshPledgeForm() {
      var pv = (data && data.pledges) || {};
      if (document.activeElement !== S.pHold) {
        var keep = S.pHold.value;
        T.clear(S.pHold);
        var hs = (pv.eligible_holdings || []).filter(function (h) { return h.free > 0; });
        if (!hs.length) { S.pHold.appendChild(T.h('option', { value: '' }, 'no Treasury free to pledge')); }
        hs.forEach(function (h) { S.pHold.appendChild(T.h('option', { value: JSON.stringify({ account_id: h.account_id, instrument_id: h.instrument_id }) }, h.symbol + ' (' + h.type.toLowerCase() + ') . ' + (h.account_name || h.account_id) + ' . free ' + T.fmt.qty(h.free))); });
        if (keep) { S.pHold.value = keep; }
      }
      if (document.activeElement !== S.pTo) {
        var keepTo = S.pTo.value;
        T.clear(S.pTo);
        var ts = (pv.terms || []).filter(function (t) { return t.securities === 'TREASURIES'; });
        if (!ts.length) { S.pTo.appendChild(T.h('option', { value: '' }, 'no prime broker takes securities')); }
        ts.forEach(function (t) { S.pTo.appendChild(T.h('option', { value: t.account_id }, t.name + ' . bills ' + pct(t.bill_haircut) + ' / notes and bonds ' + pct(t.bond_haircut) + ' haircut')); });
        if (keepTo) { S.pTo.value = keepTo; }
      }
    }
    function release(r) {
      T.ui.modal({ title: 'Release pledge ' + r.pledge_id, body: T.h('div', null, T.h('div', { class: 'line' }, T.fmt.qty(r.face) + ' ' + r.symbol + ' pledged to ' + (r.pledged_to_name || r.pledged_to) + ' (eligible ' + m(r, 'eligible') + ') becomes free to sell or pledge again. Nothing moves in the ledger.')),
        actions: [{ label: 'Cancel' }, { label: 'Release', cls: 'primary', onClick: function (mod, b) {
          b.disabled = true;
          T.api.post('/api/collateral/pledges/' + encodeURIComponent(r.pledge_id) + '/release', {}).then(function () { mod.close(); T.ui.toast('pledge ' + r.pledge_id + ' released', 'ok'); load(); },
            function (e) { b.disabled = false; T.api.fail(e, 'release'); });
          return false;
        } }] });
    }

    /* ---- assumptions: Reg T terms (typed in %, sent as fractions) and the CSA reuse flag */
    var termInputs = {};
    function paintTerms() {
      var rows = (data && data.terms) || [];
      var sig = JSON.stringify(rows.map(function (t) { return [t.account_id, t.regt.initial_pct, t.regt.maintenance_pct, t.regt.long_loan_value_pct, t.reuse_allowed]; }));
      if (S.termsSig === sig) { return; }
      var focused = document.activeElement && S.terms.contains(document.activeElement);
      if (focused && S.termsSig) { return; }
      S.termsSig = sig;
      T.clear(S.terms); termInputs = {};
      if (!rows.length) { S.terms.appendChild(T.h('div', { class: 'empty' }, 'no account holds US equities in this view')); return; }
      rows.forEach(function (t) {
        var r = t.regt || {};
        var ins = { initial_pct: T.h('input', { type: 'text', value: pct(r.initial_pct).replace(' %', ''), 'aria-label': 'initial %' }),
                    maintenance_pct: T.h('input', { type: 'text', value: pct(r.maintenance_pct).replace(' %', ''), 'aria-label': 'maintenance %' }),
                    long_loan_value_pct: T.h('input', { type: 'text', value: pct(r.long_loan_value_pct).replace(' %', ''), 'aria-label': 'loan value %' }) };
        termInputs[t.account_id] = ins;
        var save = T.h('button', { class: 'btn small' }, 'Save');
        save.addEventListener('click', function () { saveTerms(t, ins, save); });
        function grp(label, input) { return T.h('span', { class: 'row', style: 'gap:5px;align-items:center' }, label, input, '%'); }
        var row = T.h('div', { class: 'terms-row', id: 'terms-' + t.account_id.replace(/[^A-Za-z0-9_]/g, '_') }, T.h('span', { class: 'nm' }, t.name),
          grp('Reg T initial', ins.initial_pct), grp('maintenance', ins.maintenance_pct), grp('long loan value', ins.long_loan_value_pct), save,
          T.h('span', { class: 'muted' }, r.source || ''));
        if (t.reuse_allowed != null) {
          var cb = T.h('input', { type: 'checkbox', style: 'width:auto;margin:0', 'aria-label': 'CSA reuse allowed' });
          cb.checked = !!t.reuse_allowed;
          cb.addEventListener('change', function () { saveReuse(t, cb); });
          row.appendChild(T.h('label', { class: 'row', style: 'gap:5px;align-items:center', title: 'when on, the variation margin received under this prime broker\'s CSA is no longer restricted (the fund may reuse it); off by default' },
            cb, 'CSA reuse allowed'));
        }
        S.terms.appendChild(row);
      });
    }
    function fraction(v) {
      var s = String(v || '').trim().replace(/\s*%$/, '');
      if (!/^\d+(\.\d{1,2})?$/.test(s)) { return null; }
      var n = Math.round(Number(s) * 100);
      return (n / 10000).toFixed(4);
    }
    function saveTerms(t, ins, btn) {
      var body = { account_id: t.account_id }; var bad = [];
      Object.keys(ins).forEach(function (k) {
        var f = fraction(ins[k].value);
        if (f == null) { bad.push(k.replace(/_pct$/, '').replace(/_/g, ' ')); return; }
        if (f !== Number(t.regt[k]).toFixed(4)) { body[k] = f; }
      });
      S.termsMsg.textContent = '';
      if (bad.length) { S.termsMsg.textContent = bad.join(', ') + ': type a percent with at most 2 decimals, e.g. 50 or 30.5'; return; }
      if (Object.keys(body).length === 1) { S.termsMsg.textContent = 'nothing changed'; return; }
      btn.disabled = true;
      T.api.post('/api/margin/terms', body).then(function () { btn.disabled = false; S.termsSig = null; T.ui.toast('Reg T terms of ' + t.name + ' saved', 'ok'); load(); },
        function (e) { btn.disabled = false; S.termsMsg.textContent = T.api.describe(e); });
    }
    function saveReuse(t, cb) {
      var want = cb.checked;
      cb.disabled = true;
      T.api.post('/api/collateral/csa', { account_id: t.account_id, reuse_allowed: want }).then(function () {
        cb.disabled = false; S.termsSig = null; T.ui.toast('CSA reuse ' + (want ? 'allowed' : 'off') + ' for ' + t.name, 'ok'); load();
      }, function (e) { cb.disabled = false; cb.checked = !want; S.termsMsg.textContent = T.api.describe(e); });
    }

    function load() {
      if (dead) { return Promise.resolve(); }
      return T.api.get('/api/collateral/cash', scope ? { scope: scope } : null).then(function (d) {
        if (dead) { return; }
        data = d || {};
        if (spin.parentNode) { spin.parentNode.removeChild(spin); }
        err.hidden = true;
        if (!S) { build(); }
        update();
      }, function (e) {
        if (dead) { return; }
        if (spin.parentNode) { spin.parentNode.removeChild(spin); }
        err.hidden = false; err.textContent = T.api.fail(e, 'cash and collateral');
        throw e;
      });
    }
    T.poll.page('cash_collateral', 15000, load);
    T.pages.cash_collateral.unmount = function () { dead = true; T.pages.cash_collateral.unmount = null; };
  }
  T.pages.cash_collateral = { render: render, unmount: null };
})();
