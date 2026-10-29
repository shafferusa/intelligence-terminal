/* Dashboard (owner directive 2026-10-27 sections 13 and 16; FINSIM1_PLAN P1 task 4: UI-05, PORTFOLIO-07, OMS-16).
 * The landing page: #/dashboard (the default route and the unknown-route fallback; the removed news page's old address lands here
 * too), for the selected book, from ONE call - GET /api/dashboard?book= (the server gathers what the other pages serve on one set
 * of marks; this file computes nothing: every figure is printed as the API sends it).  Refreshed every 15 s while shown; a failed
 * refresh keeps the last answer and says so in the page-status line.
 *   1. "Requires your attention": the book's tiles, each leading to what it counts - working orders -> Pending Trades, pending /
 *      failed settlements -> Completed Trades (settling / failed), collateral calls and recall notices -> Cash & Collateral,
 *      compliance notices -> Risk compliance, risk warnings -> Risk, integrity and its hold -> Accounting > Integrity, provider pauses
 *      -> Settings > Market data, open attention items -> the whole attention list under the header.
 *   2. Fund: NAV (the Portfolio's NAV tile), starting and contributed capital, the TODAY / MTD / YTD / ITD P&L tiles (the Portfolio's)
 *      each with its return % (time-weighted over the daily snapshots; ITD also the simple return on contributed capital), then the
 *      ITD P&L split (realised, unrealised, income, financing, fees, FX result).
 *   3. Exposure: gross / net / long / short with their % of NAV, leverage, gross notional / NAV and the TRS notional (the Risk page's
 *      figures and definitions).
 *   4. Cash & margin: settled, available, reserved, restricted, receivables, payables; margin required / posted / excess and open
 *      calls; collateral posted / received / encumbered.
 *   5. Accounts: one row per account (NAV, share of the book, available, reserved, restricted, working orders, settling, attention);
 *      a row opens the account (Treasury: its page).
 *   6. Orders & settlements: working, partially filled, held, queued for the next session, waiting for a parent order, dealer quotes
 *      open, rejected today, settling, failed.
 *   The risk warnings and compliance notices read the whole book: the server counts them in the background (sources.attention.counts)
 *   and their tiles say when they were counted; while the first count of the book runs they say 'counting' and the page asks again
 *   two seconds later (then every 15 s again).
 *   A section whose figures are not in this build (a provider not loaded) says "unavailable" with the reason, never a number.  The
 *   last line names each section's source, as-of time and quality.  While the dashboard module is not bound (T.hasModule) the page
 *   shows the attention summary alone (GET /api/attention/summary) with the reason.  ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  function n(v) { return v == null ? 'n/a' : String(v); }
  function plabel(p) { return T.providerLabel ? T.providerLabel(p) : String(p || '').replace(/_/g, ' '); }
  function pctOf(x) { return x == null || x === '' ? null : T.fmt.pct(x, 2); }
  function times(x) { return x == null || x === '' || !isFinite(Number(x)) ? 'n/a' : T.fmt.num(x, 2) + 'x'; }
  function integrityOf(att) {
    var i = att.integrity;
    if (i == null) { return { ok: null, failing: [] }; }
    if (typeof i === 'string') { return { ok: i === 'ok', failing: i === 'ok' ? [] : [i] }; }
    if (typeof i === 'boolean') { return { ok: i, failing: [] }; }
    return i;
  }
  function holdOf(att) {
    var h = att.hold;
    if (h == null) { return { held: false, lifetimes: [] }; }
    if (typeof h === 'boolean') { return { held: h, lifetimes: [] }; }
    return h;
  }
  function expandStrip() {
    if (T.attention && typeof T.attention.expand === 'function') { T.attention.expand(); return; }
    var el = T.q('#attention'); if (el && el.scrollIntoView) { el.scrollIntoView({ block: 'start' }); }
  }
  function unavailable(reason) { return 'unavailable' + (reason ? ': ' + reason : ''); }
  /* the risk warnings and compliance notices, counted for the book in the background (sources.attention.counts): 'counting' while
     the first count runs, else when they were counted (never a guess) */
  function countsOf(d) { var s = (d && d.sources) || {}; return (s.attention && s.attention.counts) || null; }
  function counting(d) { var c = countsOf(d); return !!(c && c.how === 'counting'); }
  function countedLine(d) {
    var c = countsOf(d);
    if (!c) { return ''; }
    if (c.how === 'counting') { return 'first count running - shown in a moment'; }
    if (c.how === 'failed') { return 'not counted: ' + (c.error || 'the count failed'); }
    return c.counted_at != null ? 'counted ' + T.fmt.time(c.counted_at) + (c.how === 'recounting' ? ' (recounting)' : '') : '';
  }
  /* the attention tiles: label; text(att, d) = what is shown; sub; cls = tone; href / act = where it leads; title = the hover */
  var STRIP = [
    { key: 'working_orders', label: 'working orders', href: '#/pending', title: 'orders of this book still working, held or waiting - opens Pending Trades',
      text: function (att) { return n(att.working_orders); },
      sub: function (att, d) { var c = d.counters || {}; return c.present ? 'partially filled ' + n(c.partial) + ' . held ' + n(c.held) : ''; } },
    { key: 'pending_settlements', label: 'pending settlements', href: '#/completed?status=settling', title: 'executions not settled yet - opens Completed Trades (settling)',
      text: function (att) { return n(att.pending_settlements); } },
    { key: 'failed', label: 'failed settlements', href: '#/completed?status=failed', title: 'settlements that failed - opens Completed Trades (failed)',
      text: function (att, d) { var v = failedOf(d); return v == null ? 'n/a' : String(v); },
      sub: function (att, d) { return failedOf(d) == null ? 'not in this build' : ''; },
      cls: function (att, d) { return Number(failedOf(d)) > 0 ? 'neg' : ''; } },
    { key: 'collateral_calls_today', label: 'collateral calls today', href: '#/cash', title: 'collateral and margin calls of the accounting day - opens Cash & Collateral',
      text: function (att) { return n(att.collateral_calls_today); },
      sub: function (att) { return att.swap_vm_payments_today != null ? 'incl. swap VM payments ' + n(att.swap_vm_payments_today) : ''; } },
    { key: 'recalls', label: 'recall notices', href: '#/cash', title: 'borrowed shares the lender is calling back - opens Cash & Collateral', text: function (att) { return n(att.recalls); } },
    { key: 'compliance', label: 'compliance notices', href: '#/risk?section=compliance', title: 'opens the compliance section of Risk',
      text: function (att, d) { return counting(d) ? 'counting' : n(att.compliance); },
      sub: function (att, d) { var w = countedLine(d); return 'info / warning / breach only' + (w ? ' . ' + w : ''); } },
    { key: 'warnings', label: 'risk warnings', href: '#/risk', title: 'opens Risk',
      text: function (att, d) { return counting(d) ? 'counting' : n(att.warnings); }, sub: function (att, d) { return countedLine(d); } },
    { key: 'integrity', label: 'integrity', href: '#/accounting/integrity', title: 'the last integrity check of this book - opens Accounting > Integrity',
      text: function (att) { var i = integrityOf(att); return i.ok === true ? 'ok' : (i.ok === false ? 'FAILED ' + (i.failing || []).join(' ') : 'not checked'); },
      sub: function (att) { var i = integrityOf(att); var h = holdOf(att); return h.held ? 'HELD ' + (h.lifetimes || []).join(' ') + ': writes refused' : (i.checked_at ? 'checked ' + T.fmt.hm(i.checked_at) : (i.ok == null ? 'no report recorded yet' : '')); },
      cls: function (att) { var i = integrityOf(att); return (i.ok === false || holdOf(att).held) ? 'neg' : (i.ok === true ? 'pos' : ''); } },
    { key: 'providers', label: 'provider pauses', href: '#/settings/market-data', title: 'market-data providers that are down - opens Settings > Market data',
      text: function (att) { var p = att.providers || {}; return p.paused != null ? n(p.paused) : n((p.down || []).length); },
      sub: function (att) { var p = att.providers || {}; return (p.down || []).map(plabel).join(', ') + ((p.refused_ua || []).length ? ' . user-agent refused: ' + p.refused_ua.map(plabel).join(', ') : ''); },
      cls: function (att) { var p = att.providers || {}; return (p.down || []).length ? 'neg' : ''; } },
    { key: 'open', label: 'open attention items', act: expandStrip, title: 'opens the whole attention list under the header',
      text: function (att) { var o = att.open || {}; return 'E ' + n(o.ERROR) + ' . W ' + n(o.WARN) + ' . I ' + n(o.INFO); },
      sub: function (att) { return att.attention_open != null ? n(att.attention_open) + ' open - click to list them' : ''; },
      cls: function (att) { var o = att.open || {}; return Number(o.ERROR) > 0 ? 'neg' : ''; } }
  ];
  /* failed settlements: the trade blotters' counter, else the collateral summary's; null when neither is in this build */
  function failedOf(d) {
    var c = d.counters || {}; if (c.present && c.failed != null) { return c.failed; }
    var k = d.collateral || {}; if (k.present && k.failed_settlements != null) { return k.failed_settlements; }
    return null;
  }
  function panel(title, small) { return T.h('div', { class: 'panel' }, T.h('h2', null, title, small ? T.h('small', null, small) : null)); }
  function strip(parent, att, d) {
    var p = panel('Requires your attention', 'this book plus the data feeds; each tile opens what it counts');
    var tiles = T.tiles(STRIP.map(function (t) {
      return { k: t.label, v: t.text(att, d), s: t.sub ? t.sub(att, d) : null, cls: t.cls ? t.cls(att, d) : '', href: t.href || null, title: t.title || '' };
    }));
    STRIP.forEach(function (t, i) {
      if (!t.act) { return; }
      var el = tiles.children[i];
      if (el) { el.classList.add('click'); el.addEventListener('click', t.act); }
    });
    p.appendChild(tiles);
    var i = integrityOf(att);
    if (i.ok === false && (i.problems || []).length) {
      var probs = T.h('div', { class: 'hint' });
      (i.problems || []).forEach(function (pr) { probs.appendChild(T.h('div', null, (pr.id || '') + ' ' + (pr.scope || '') + ': ' + (pr.detail || pr.message || ''))); });
      p.appendChild(probs);
    }
    parent.appendChild(p);
  }
  /* a P&L tile (the Portfolio's pnlTile) with its return % on the second line */
  function pnlTile(label, pnl) {
    var portfolio = T.pages.portfolio;
    var t = portfolio && portfolio.pnlTile ? portfolio.pnlTile(label, pnl, '#/accounting/pnl') : { k: label, v: T.money(pnl || {}, 'pnl') };
    var r = (pnl && pnl['return']) || {};
    var line = r.pct != null ? 'return ' + T.fmt.pct(r.pct, 2) : 'return % ' + unavailable(r.reason);
    if (label === 'ITD' && r.simple_pct != null) { line += ' (simple ' + T.fmt.pct(r.simple_pct, 2) + ')'; }
    t.s = line + (t.s ? ' . ' + t.s : '');
    t.title = (t.title ? t.title + '\n' : '') + (r.pct != null ? 'time-weighted: the daily NAV snapshots chained day by day with capital flows removed' : (r.reason || ''));
    return t;
  }
  function fund(parent, d) {
    var nav = d.nav || {}; var pnl = d.pnl || {}; var cap = d.capital || {};
    var p = panel('Fund', (d.book_name || '') + (cap.lifetime_id ? ' . lifetime ' + cap.lifetime_id : ''));
    var portfolio = T.pages.portfolio;
    var navTile = portfolio && portfolio.navTile ? portfolio.navTile(nav) : { k: 'NAV (equity)', v: T.money(nav, 'nav') };
    navTile.href = '#/portfolio';
    var tiles = [navTile,
      { k: 'starting capital', v: T.money(cap, 'starting'), s: 'contributed ' + T.money(cap, 'contributed'), title: cap.note || '' },
      pnlTile('TODAY', pnl.TODAY), pnlTile('MTD', pnl.MTD), pnlTile('YTD', pnl.YTD), pnlTile('ITD', pnl.ITD)];
    p.appendChild(T.tiles(tiles));
    var ex = d.explain || [];
    if (ex.length) {
      var t = T.h('table');
      t.appendChild(T.h('thead', null, T.h('tr', null, T.h('th', { class: 'l' }, 'ITD P&L by source'), T.h('th', null, 'booked'), T.h('th', null, 'effect on P&L'), T.h('th', { class: 'l' }, ''))));
      var tb = T.h('tbody');
      ex.forEach(function (r) {
        tb.appendChild(T.h('tr', null, T.h('td', { class: 'l' }, r.label || r.key), T.h('td', null, T.money(r, 'booked')),
          T.h('td', { class: T.sign(r, 'effect') }, T.money(r, 'effect')), T.h('td', { class: 'l muted' }, r.note || '')));
      });
      var itd = pnl.ITD || {};
      tb.appendChild(T.h('tr', { class: 'total' }, T.h('td', { class: 'l' }, 'ITD P&L'), T.h('td'), T.h('td', { class: T.sign(itd, 'pnl') }, T.money(itd, 'pnl')),
        T.h('td', { class: 'l muted' }, itd.residual != null && T.num(itd, 'residual') ? 'residual ' + T.money(itd, 'residual') : 'the rows add up to the P&L')));
      t.appendChild(tb);
      p.appendChild(T.h('div', { style: 'overflow-x:auto' }, t));
    }
    parent.appendChild(p);
  }
  function exposure(parent, d) {
    var x = d.exposure || {};
    var p = panel('Exposure', 'the Risk page\'s figures and definitions');
    if (!x.present) { p.appendChild(T.h('div', { class: 'empty' }, unavailable(x.reason))); parent.appendChild(p); return; }
    var def = x.definitions || {};
    p.appendChild(T.tiles([
      { k: 'gross', v: T.money(x, 'gross'), s: pctOf(x.gross_pct_nav) ? pctOf(x.gross_pct_nav) + ' of NAV' : '', title: def.gross_exposure || '', href: '#/risk' },
      { k: 'net', v: T.money(x, 'net'), s: pctOf(x.net_pct_nav) ? pctOf(x.net_pct_nav) + ' of NAV' : '', cls: T.sign(x, 'net'), title: def.net || '', href: '#/risk' },
      { k: 'long', v: T.money(x, 'long'), s: pctOf(x.long_pct_nav) ? pctOf(x.long_pct_nav) + ' of NAV' : '', title: def.long || '', href: '#/risk' },
      { k: 'short', v: T.money(x, 'short'), s: pctOf(x.short_pct_nav) ? pctOf(x.short_pct_nav) + ' of NAV' : '', title: def.short || '', href: '#/risk' },
      { k: 'leverage (gross / NAV)', v: times(x.leverage), s: 'gross notional / NAV ' + times(x.gross_notional_leverage), title: (def.gross_exposure || '') + '\n' + (def.gross_notional || ''), href: '#/risk' },
      { k: 'TRS notional', v: T.money(x, 'trs_notional'), s: pctOf(x.trs_pct_nav) ? pctOf(x.trs_pct_nav) + ' of NAV (signed)' : '', title: def.trs || '', href: '#/risk' }]));
    var notes = [];
    if (x.partial) { notes.push('some positions are unpriced: they are at cost in these figures'); }
    if ((x.notional_missing || []).length) { notes.push('no notional for ' + x.notional_missing.join(', ')); }
    if (notes.length) { p.appendChild(T.h('div', { class: 'hint' }, notes.join(' . '))); }
    parent.appendChild(p);
  }
  function cashMargin(parent, d) {
    var c = d.cash || {}; var m = d.margin || {}; var k = d.collateral || {};
    var p = panel('Cash & margin', 'in USD; other currencies at this answer\'s rates');
    if (c.present) {
      var tiles = [
        { k: 'settled cash', v: T.money(c, 'settled'), href: '#/cash', title: 'cash settled in the book\'s accounts' },
        { k: 'available', v: T.money(c, 'available'), href: '#/cash', title: c.note || '' },
        { k: 'reserved', v: T.money(c, 'reserved'), href: '#/cash', title: 'cash reserved by working orders' },
        { k: 'restricted', v: T.money(c, 'restricted'), href: '#/cash', title: 'collateral received that may not be spent (net per CSA, plus securities-lending cash collateral)' },
        { k: 'receivables', v: T.money(c, 'receivable'), href: '#/cash', title: 'settlements to receive' },
        { k: 'payables', v: T.money(c, 'payable'), href: '#/cash', title: 'settlements to pay' }];
      p.appendChild(T.tiles(tiles));
      var other = (c.other_adjustments || []).map(function (a) { return (a.label || a.key) + ' ' + a.currency + ' ' + T.money(a, 'amount'); });
      var ccys = (c.by_currency || []).filter(function (x) { return x.currency !== 'USD'; }).map(function (x) { return x.currency + ': settled ' + T.money(x, 'settled') + ', available ' + T.money(x, 'available'); });
      var bits = other.concat(ccys).concat((c.fx_missing || []).length ? ['no USD rate for ' + c.fx_missing.join(', ') + ' (left out of the USD figures)'] : []);
      if (bits.length) { p.appendChild(T.h('div', { class: 'hint' }, bits.join(' . '))); }
    } else {
      p.appendChild(T.h('div', { class: 'empty' }, 'cash ' + unavailable(c.reason)));
    }
    if (m.present) {
      p.appendChild(T.tiles([
        { k: 'margin required', v: T.money(m, 'required'), href: '#/cash' }, { k: 'margin posted', v: T.money(m, 'posted'), href: '#/cash' },
        { k: 'margin excess', v: T.money(m, 'excess'), cls: T.sign(m, 'excess'), href: '#/cash' },
        { k: 'open margin calls', v: n(m.open_calls), cls: Number(m.open_calls) > 0 ? 'neg' : '', href: '#/cash' }]));
    } else {
      p.appendChild(T.h('div', { class: 'empty' }, 'margin ' + unavailable(m.reason)));
    }
    if (k.present) {
      p.appendChild(T.tiles([
        { k: 'collateral posted', v: T.money(k, 'posted'), href: '#/cash' }, { k: 'collateral received', v: T.money(k, 'received'), href: '#/cash' },
        { k: 'encumbered', v: T.money(k, 'encumbered'), href: '#/cash', title: 'collateral and margin the book has posted and cannot spend' }]));
    } else if (!m.present || m.reason !== k.reason) {
      p.appendChild(T.h('div', { class: 'empty' }, 'collateral ' + unavailable(k.reason)));
    }
    parent.appendChild(p);
  }
  function accounts(parent, d) {
    var rows = d.accounts || [];
    var p = panel('Accounts', 'NAV and cash of each account; click a row to open it');
    if (!rows.length) { p.appendChild(T.h('div', { class: 'empty' }, (d.cash || {}).present === false ? unavailable((d.cash || {}).reason) : 'no accounts')); parent.appendChild(p); return; }
    var t = T.h('table');
    var cols = [['account', 'l'], ['NAV', ''], ['% of book', ''], ['available', ''], ['reserved', ''], ['restricted', ''], ['working', ''], ['settling', ''], ['attention', '']];
    t.appendChild(T.h('thead', null, T.h('tr', null, cols.map(function (c) { return T.h('th', { class: c[1] }, c[0]); }))));
    var tb = T.h('tbody');
    rows.forEach(function (r) {
      var href = r.kind === 'TREASURY' ? '#/treasury' : T.router.account(r.account_id, 'markets');
      var tr = T.h('tr', { class: 'click', title: r.account_id + (r.broker ? ' . ' + r.broker : '') + ' - opens ' + (r.kind === 'TREASURY' ? 'the Treasury page' : 'the account') },
        T.h('td', { class: 'l' }, r.name || r.account_id, r.status && r.status !== 'OPEN' ? T.stateTag(r.status) : null),
        T.h('td', null, T.money(r, 'nav')), T.h('td', null, r.weight == null ? 'n/a' : T.fmt.pct(r.weight, 2)),
        T.h('td', null, T.money(r, 'available')), T.h('td', null, T.money(r, 'reserved')), T.h('td', null, T.money(r, 'restricted')),
        T.h('td', null, n(r.working_orders)), T.h('td', null, n(r.pending_settlements)),
        T.h('td', { class: Number(r.attention_count) > 0 ? 'neg' : '' }, n(r.attention_count)));
      tr.addEventListener('click', function () { T.router.go(href); });
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    p.appendChild(T.h('div', { style: 'overflow-x:auto' }, t));
    parent.appendChild(p);
  }
  var COUNTERS = [['working', 'working', '#/pending'], ['partial', 'partially filled', '#/pending'], ['held', 'held', '#/pending'],
                  ['queued', 'queued for the next session', '#/pending'], ['waiting_parent', 'waiting for a parent order', '#/pending'],
                  ['rfq_open', 'dealer quotes open', '#/pending'], ['rejected_today', 'rejected today', '#/pending?status=rejected'],
                  ['settling', 'settling', '#/completed?status=settling'], ['failed', 'failed settlements', '#/completed?status=failed']];
  function counters(parent, d) {
    var c = d.counters || {};
    var p = panel('Orders & settlements', 'the trade blotters\' counters; each opens its blotter');
    if (!c.present) { p.appendChild(T.h('div', { class: 'empty' }, unavailable(c.reason))); parent.appendChild(p); return; }
    p.appendChild(T.tiles(COUNTERS.map(function (k) {
      return { k: k[1], v: n(c[k[0]]), href: k[2], cls: (k[0] === 'failed' || k[0] === 'rejected_today') && Number(c[k[0]]) > 0 ? 'neg' : '' };
    })));
    parent.appendChild(p);
  }
  var SOURCE_ORDER = [['nav', 'NAV'], ['pnl', 'P&L'], ['returns', 'returns'], ['exposure', 'exposure'], ['cash', 'cash'], ['margin', 'margin'],
                      ['collateral', 'collateral'], ['counters', 'counters'], ['attention', 'attention']];
  function sources(parent, d) {
    var s = d.sources || {}; var tm = d.timing || {};
    var box = T.h('details', { class: 'hint' }, T.h('summary', null, 'where the figures come from' + (d.as_of ? ' . marks as of ' + T.fmt.time(d.as_of) + ' ET' : '')));
    SOURCE_ORDER.forEach(function (k) {
      var x = s[k[0]]; if (!x) { return; }
      box.appendChild(T.h('div', null, T.h('b', null, k[1] + ': '), (x.source || '') + (x.quality ? ' . quality ' + x.quality : '') + (x.as_of ? ' . as of ' + T.fmt.time(x.as_of) : '')));
    });
    var c = countsOf(d);
    if (c) {
      box.appendChild(T.h('div', null, T.h('b', null, 'risk warnings and compliance notices: '), countedLine(d) + '; kept up to ' + n(c.max_age_s) +
        ' s and recounted in the background after each booking (the Risk page counts them live)'));
    }
    if (tm.own_ms != null) { box.appendChild(T.h('div', { class: 'muted' }, 'computed in ' + Math.round(Number(tm.own_ms)) + ' ms' + (tm.total_ms != null ? ' (' + Math.round(Number(tm.total_ms)) + ' ms with the optional figures)' : ''))); }
    parent.appendChild(box);
  }
  /* the whole page from one answer */
  function paint(body, d) {
    T.clear(body);
    var att = d.attention && typeof d.attention === 'object' ? d.attention : {};
    if (att.present === false) { body.appendChild(T.h('div', { class: 'error' }, 'attention ' + unavailable(att.reason))); }
    else { strip(body, att, d); }
    fund(body, d);
    var two = T.h('div', { class: 'grid2' });
    exposure(two, d);
    cashMargin(two, d);
    body.appendChild(two);
    accounts(body, d);
    counters(body, d);
    sources(body, d);
  }
  /* the dashboard module is not bound in this build: the attention summary alone, said so */
  function paintAttentionOnly(body, att) {
    T.clear(body);
    strip(body, att && typeof att === 'object' ? att : {}, {});
    body.appendChild(T.h('div', { class: 'empty' }, unavailable('the fund figures (NAV, P&L, exposure, cash, margin, accounts) are not in this build')));
  }
  function render(el) {
    var book = T.book.current();
    T.pageHead(el, 'Dashboard', book ? (book.name || book.book_code) : '');
    var body = T.h('div'); el.appendChild(body);
    body.appendChild(T.h('div', { class: 'empty' }, 'loading ...'));
    var loaded = false;
    var pending = false;                                     /* the first count of the risk warnings / compliance notices is running */
    function load() {
      var full = T.hasModule('dashboard');
      var call = full ? T.api.get('/api/dashboard') : T.api.get('/api/attention/summary');
      return call.then(function (d) {
        loaded = true;
        pending = full && counting(d);
        if (full) { paint(body, d && typeof d === 'object' ? d : {}); } else { paintAttentionOnly(body, d); }
      }, function (e) {
        if (!loaded) {                                       /* nothing to keep: say why here; later failures keep the last answer */
          T.clear(body);
          body.appendChild(T.h('div', { class: 'error' }, 'the dashboard could not be loaded: ' + T.api.describe(e)));
        }
        throw e;                                             /* the page-status line says it and offers Retry */
      });
    }
    T.poll.page('dashboard', function () { return pending ? 2000 : 15000; }, load);
    T.pages.dashboard.unmount = function () { T.pages.dashboard.unmount = null; };
  }
  T.pages.dashboard = { render: render, unmount: null, STRIP: STRIP, COUNTERS: COUNTERS };
})();
