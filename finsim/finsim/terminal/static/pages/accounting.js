/* Accounting (TERMINAL_DESIGN.md s17.1 #/accounting[/tab]; WP-12; Build 2 P03).  Tabs: P&L (explain with each component's
 * effect on P&L, periods TODAY / MTD / YTD / ITD), Balance sheet (the memo NAV = capital + advances + retained in the words of
 * the scope: 'advanced to accounts' on the Treasury, 'funded by Treasury' on an account), Transactions (every ledger entry of
 * the scope with the other side, the server's CSV), Cash & financing (cash by account and currency, the settlement ladder,
 * sweeps, loans, borrows, repos, securities lent, margin), Ledger (journal entries; an entry opens as a table: entry fields
 * and its lines with Dr / Cr), Trial balance, Integrity (one row per check the run made; Run now on the live page only).
 * Scope = ALL (the selected book's open lifetime), ALL_BOOKS or an account of the book.  Every figure is the server's.
 * Also rendered read-only by the History pages (opts.base / opts.readOnly / opts.history): no Run now, P&L from inception.
 * T.pages.accounting.entryModal(entryView | promise, opts) is the shared entry table (Ledger, Activity, Transactions).
 */
(function () {
  'use strict';
  var T = window.T;
  var TABS = [{ key: 'pnl', label: 'P&L' }, { key: 'balance-sheet', label: 'Balance sheet' }, { key: 'transactions', label: 'Transactions' },
              { key: 'cash', label: 'Cash & financing' }, { key: 'ledger', label: 'Ledger' }, { key: 'trial-balance', label: 'Trial balance' },
              { key: 'integrity', label: 'Integrity' }];
  function moneyCol(name, label, opts) {
    opts = opts || {};
    return { key: name, label: label || name.replace(/_/g, ' '), title_h: opts.title,
             html: function (r) { return '<span class="' + (opts.plain ? '' : T.sign(r, name)) + '">' + T.esc(T.money(r, name)) + '</span>'; },
             text: function (r) { return T.money(r, name); }, sort: function (r) { return T.num(r, name); } };
  }
  function acctName(id, names) { return (names && names[id]) || T.accountName(id) || id || ''; }
  /* a long text cell wraps inside a fixed width instead of pushing the figures off the screen (tables fit 1440 px) */
  function wrapHtml(text, px, title) { return '<span style="display:inline-block; max-width:' + px + 'px; white-space:normal; overflow-wrap:anywhere; line-height:1.25"' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + T.esc(text == null ? '' : text) + '</span>'; }

  /* ---- the entry table (BM-25): entry fields as key / value, the lines with Dr / Cr, the linked fill / transfer, the command ---- */
  function lineCols() {
    return [{ key: 'account_id', label: 'account', cls: 'l', text: function (l) { return l.account_name || T.accountName(l.account_id) || l.account_id || ''; }, title: function (l) { return l.account_id || ''; } },
            { key: 'gl', label: 'GL account', cls: 'l', text: function (l) { return (l.gl || '') + (l.gl_name ? ' ' + l.gl_name : ''); } },
            { key: 'currency', label: 'ccy', cls: 'l' },
            { key: 'debit', label: 'Dr', text: function (l) { return l.debit == null && l.debit_c == null ? '' : T.money(l, 'debit'); } },
            { key: 'credit', label: 'Cr', text: function (l) { return l.credit == null && l.credit_c == null ? '' : T.money(l, 'credit'); } },
            { key: 'base', label: 'USD', text: function (l) { return l.base == null && l.base_c == null ? '' : T.money(l, 'base'); } },
            { key: 'instrument_id', label: 'instrument', cls: 'l', text: function (l) { return l.instrument_id || ''; } },
            { key: 'memo', label: 'memo', cls: 'l', text: function (l) { return l.memo || ''; } }];
  }
  /* a linked record (fill / transfer) as key / value: times as New York time, money as money, ids and words as they are */
  var MONEY_KEYS = { amount: 1, gross: 1, fee: 1, shortfall: 1, principal: 1, collateral: 1, net: 1 };
  function kvRows(obj, skip) {
    var out = [];
    Object.keys(obj || {}).forEach(function (k) {
      var v = obj[k];
      if (v == null || v === '' || (skip && skip.indexOf(k) >= 0) || typeof v === 'object' || /_ny$/.test(k)) { return; }
      var text = String(v);
      if (k === 'ts' || /_ts$/.test(k)) { text = obj[k.replace(/ts$/, 'ny')] || (k === 'ts' && obj.ts_ny) || T.fmt.dt(v); }
      else if (MONEY_KEYS[k]) { text = T.fmt.money(v); }
      out.push([k === 'ts' ? 'time' : k.replace(/_/g, ' '), text]);
    });
    return out;
  }
  function entryBody(b, d, extra) {
    var e = d.entry || d;
    var pairs = [['entry', String(e.entry_id == null ? '' : e.entry_id)], ['kind', e.kind || ''], ['time', e.ts_ny || T.fmt.dt(e.ts)], ['business date', e.business_date || ''],
                 ['memo', e.memo || ''], ['reference', e.ref_type ? e.ref_type + ' ' + (e.ref_id || '') : '']];
    if (d.command) { pairs.push(['command', d.command.kind + ' #' + d.command.id + (d.command.actor ? ' by ' + d.command.actor : '')]); pairs.push(['request', d.command.request_id || '']); }
    (extra || []).forEach(function (p) { pairs.push(p); });
    b.appendChild(T.kv(pairs.filter(function (p) { return p[1] != null && p[1] !== ''; })));
    var lines = d.lines || [];
    b.appendChild(T.h('h2', { style: 'margin-top:10px' }, 'lines (Dr / Cr)'));
    var g = T.h('div'); b.appendChild(g);
    T.components.grid.mount(g, { tools: false, rows: lines, columns: lineCols(), empty: 'no lines' });
    var linked = d.linked;
    if (linked && typeof linked === 'object') {
      b.appendChild(T.h('h2', { style: 'margin-top:10px' }, 'linked ' + (d.ref_type || 'record')));
      b.appendChild(T.kv(kvRows(linked, ['quote', 'quote_json', 'command_id', 'idem_key', 'entry_id', 'fx_rate'])));
    }
  }
  /* entryModal(view | promise of a view, {title, extra: [[k, v]], before: fn(body)}) */
  function entryModal(src, opts) {
    opts = opts || {};
    var h = T.ui.modal({ title: opts.title || 'journal entry', wide: true, body: function (b) {
      if (opts.before) { opts.before(b); }
      var slot = T.h('div', null, T.h('div', { class: 'hint' }, 'loading the entry ...')); b.appendChild(slot);
      Promise.resolve(src).then(function (d) { T.clear(slot); if (d) { entryBody(slot, d, opts.extra); } else { slot.appendChild(T.h('div', { class: 'hint' }, 'no journal entry for this row')); } },
        function (e) { T.clear(slot); slot.appendChild(T.h('div', { class: 'error' }, T.api.describe(e))); });
    } });
    return h;
  }

  function render(el, route, opts) {
    opts = opts || {};
    var hist = !!opts.history;
    var readOnly = !!opts.readOnly;
    var tab = route.tab || 'pnl';
    var scope = opts.scope || T.store.get('accounting.scope', 'ALL');
    var period = hist ? 'ITD' : T.store.get('accounting.period', 'TODAY');          /* SHELL-06: a closed lifetime's P&L is read from inception */
    var base = opts.base || '/api/accounting/';
    var dead = false;
    var scopeSel = T.h('select', { 'aria-label': 'scope' });
    function fillScopes() {
      var keep = scopeSel.value || scope;
      T.clear(scopeSel);
      if (opts.scope) {
        scopeSel.appendChild(T.h('option', { value: opts.scope }, opts.scopeLabel || opts.scope));
        (opts.accounts || []).forEach(function (a) { scopeSel.appendChild(T.h('option', { value: a.account_id }, (a.name || a.account_id) + ' (' + a.account_id + ')')); });
      } else {
        scopeSel.appendChild(T.h('option', { value: 'ALL' }, 'this book (' + ((T.book.current() || {}).name || 'selected') + ')'));
        scopeSel.appendChild(T.h('option', { value: 'ALL_BOOKS' }, 'all books'));
        (T.state.accounts || []).forEach(function (a) { scopeSel.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); });
      }
      scopeSel.value = keep; if (scopeSel.value !== keep) { scopeSel.value = opts.scope || 'ALL'; }
      scope = scopeSel.value;
    }
    fillScopes();
    scopeSel.addEventListener('change', function () { scope = scopeSel.value; if (!opts.scope) { T.store.set('accounting.scope', scope); } load(); });
    T.pageHead(el, opts.title || 'Accounting', opts.sub || '', [T.h('span', { class: 'sub' }, 'scope'), scopeSel]);
    var tabList = TABS.filter(function (t) { return !opts.tabs || opts.tabs.indexOf(t.key) >= 0; });
    if (!opts.noTabs) { el.appendChild(T.tabs(tabList, tab, function (k) { T.router.go((opts.prefix || '#/accounting') + '/' + k); })); }
    var body = T.h('div'); el.appendChild(body);
    function scopeName() { return scope === 'ALL' ? ((T.book.current() || {}).name || 'this book') : (scope === 'ALL_BOOKS' ? 'all books' : (/:ALL$/.test(scope) ? 'lifetime ' + scope.slice(0, -4) : T.accountName(scope))); }
    function load() {
      if (dead) { return Promise.resolve(); }
      T.clear(body); body.appendChild(T.h('div', { class: 'spin' }));
      if (tab === 'pnl') { return pnl(); }
      if (tab === 'balance-sheet') { return balance(); }
      if (tab === 'transactions') { return transactions(); }
      if (tab === 'cash') { return cash(); }
      if (tab === 'ledger') { return ledger(); }
      if (tab === 'trial-balance') { return trial(); }
      if (tab === 'integrity') { return integrity(null); }
      T.clear(body); body.appendChild(T.h('div', { class: 'empty' }, 'unknown tab'));
      return Promise.resolve();
    }

    /* ---- P&L: the explain rows carry their effect on P&L, so they add up to 'explained' (BM-14 / APIUI-14) ---- */
    function explainRows(v) {
      if (Array.isArray(v.explain) && v.explain.length) { return v.explain; }
      var c = v.components || {}; var neg = { financing: 1, fees: 1 };          /* an older answer: costs shown with their P&L sign */
      return ['realised', 'income', 'financing', 'fees', 'unrealised', 'fx_result'].filter(function (k) { return c[k] != null || c[k + '_c'] != null; }).map(function (k) {
        var n = T.num(c, k); return { key: k, label: k.replace('_', ' '), booked: c[k], effect: n == null ? null : String(neg[k] ? -n : n), note: neg[k] ? 'a cost: it reduces P&L' : '' };
      });
    }
    function pnl() {
      var per = T.h('div', { class: 'tabs' });
      ['TODAY', 'MTD', 'YTD', 'ITD'].forEach(function (p) { per.appendChild(T.h('button', { class: 'tab' + (p === period ? ' active' : ''), onclick: function () { period = p; if (!hist) { T.store.set('accounting.period', p); } load(); } }, p)); });
      return T.api.get(base + 'pnl', { scope: scope, period: period }).then(function (v) {
        T.clear(body); body.appendChild(per);
        var b = v.baseline || {};
        /* BM-33: the tile reads 'inception (capital flows)' on ITD (the value line holds the first word, the line below the rest) */
        var bLabel = b.label || (b.inception ? (period === 'ITD' ? 'inception (capital flows)' : 'inception (no snapshot yet)') : (b.snap_date ? 'snapshot ' + b.snap_date : 'snapshot'));
        var cut = bLabel.indexOf(' ('), bMain = cut > 0 ? bLabel.slice(0, cut) : bLabel, bRest = cut > 0 ? bLabel.slice(cut + 1) : '';
        body.appendChild(T.tiles([
          { k: 'P&L ' + period, v: T.money(v, 'pnl'), s: 'NAV ' + T.money(v, 'nav') + ' - NAV0 ' + T.money(v, 'nav_0') + ' - flows ' + T.money(v, 'flows'), cls: T.sign(v, 'pnl') },
          { k: 'explained', v: T.money(v, 'explained'), s: 'the sum of the P&L effects below', cls: T.sign(v, 'explained') },
          { k: 'residual', v: T.money(v, 'residual'), s: T.num(v, 'residual') ? 'NOT ZERO - integrity M1' : '0 by construction', cls: T.num(v, 'residual') ? 'neg' : 'pos' },
          { k: 'baseline', v: bMain, s: (bRest ? bRest + ' ' : '') + (b.note || (v.degraded ? 'no snapshot yet - never invented' : '')), cls: v.degraded ? 'neg' : '', title: bLabel },
          { k: 'quality', v: v.quality || 'n/a', s: v.partial ? 'marks partial' : 'all positions priced', cls: v.partial ? 'neg' : '' }]));
        var g = T.h('div', { class: 'panel' }, T.h('h2', null, 'explain', T.h('small', null, 'each component with its effect on P&L: fees and financing are costs, so they count negative')));
        var ge = T.h('div'); g.appendChild(ge); body.appendChild(g);
        T.components.grid.mount(ge, { tools: false, rows: explainRows(v), empty: 'no components', columns: [
          { key: 'label', label: 'component', cls: 'l', text: function (r) { return r.label || r.key; } },
          { key: 'effect', label: 'P&L effect', html: function (r) { return '<span class="' + T.sign(r, 'effect') + '">' + T.esc(T.money(r, 'effect')) + '</span>'; }, text: function (r) { return T.money(r, 'effect'); } },
          { key: 'booked', label: 'as booked', html: function (r) { return '<span class="muted">' + T.esc(T.money(r, 'booked')) + '</span>'; }, text: function (r) { return T.money(r, 'booked'); } },
          { key: 'note', label: 'note', cls: 'l', text: function (r) { return r.note || ''; } }],
          footer: function () { return { label: 'explained', effect: '<b class="' + T.sign(v, 'explained') + '">' + T.esc(T.money(v, 'explained')) + '</b>', note: v.explain_ties === false ? 'the rows do not add up: integrity M1' : 'P&L ' + T.esc(T.money(v, 'pnl')) + ' - residual ' + T.esc(T.money(v, 'residual')) }; } });
        body.appendChild(T.convNote(['ACCT_DAY', 'FX_RESULT', 'COST_BASIS']));
      }, function (e) { T.clear(body); T.errorBox(body, e, 'pnl'); });
    }

    /* ---- Balance sheet: the advances in the words of the scope (BM-29) ---- */
    function balance() {
      return T.api.get(base + 'balance-sheet', { scope: scope }).then(function (b) {
        T.clear(body);
        body.appendChild(T.tiles([{ k: 'assets', v: T.money(b, 'assets') }, { k: 'liabilities', v: T.money(b, 'liabilities') }, { k: 'NAV', v: T.money(b, 'nav'), s: (b.quality || '') + (b.partial ? ' . partial' : ''), cls: b.partial ? 'neg' : '' }, { k: 'rounding', v: T.money(b, 'rounding'), s: 'base-rounding plug (one cent per foreign currency at most)' }]));
        var g = T.h('div', { class: 'panel' }); var ge = T.h('div'); g.appendChild(ge); body.appendChild(g);
        T.components.grid.mount(ge, { tools: false, rows: b.rows || [], empty: 'no balances', groupBy: function (r) { return r.section || ''; }, columns: [{ key: 'label', label: 'line', cls: 'l', html: function (r) { return (r.sub ? '<span class="muted">&nbsp;&nbsp;' : '<span>') + T.esc(r.label || r.name || '') + (r.gl ? ' <span class="muted">' + T.esc(r.gl) + '</span>' : '') + '</span>'; }, text: function (r) { return r.label || r.name || ''; } }, { key: 'currency', label: 'ccy', cls: 'l', text: function (r) { return r.currency || ''; } }, moneyCol('native'), moneyCol('usd', 'USD'), { key: 'quality', label: 'quality', cls: 'l', html: function (r) { return r.quality ? T.qchipHtml(r.quality) : ''; }, text: function (r) { return r.quality || ''; } }] });
        var m = b.memo || {};
        var advLabel = m.advances_label || 'Treasury advances';
        var adv = m.advances_shown != null || m.advances_shown_c != null ? T.money(m, 'advances_shown') : T.money(m, 'advances');
        body.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'memo: ' + (m.identity_text || 'NAV = capital + advances + retained')),
          T.kv([['NAV', T.money(b, 'nav')], ['capital contributed', T.money(m, 'capital')], [advLabel, adv, 'internal transfers between the Treasury and the accounts of a lifetime; they net to zero across the lifetime'],
                ['retained results', T.money(m, 'retained')], ['FX clearing (base)', T.money(m, 'fx_clearing')], ['ties to the cent', m.ties === false ? 'NO' : (m.ties === true ? 'yes' : 'n/a')]])));
        body.appendChild(T.convNote(['TREASURY', 'ENCUMBRANCE', 'EXCESS_LIQ']));
      }, function (e) { T.clear(body); T.errorBox(body, e, 'balance sheet'); });
    }

    /* ---- Transactions of the scope (BM-13) ---- */
    function netCash(r) { var n = r.net_cash || {}; return Object.keys(n).map(function (c) { return c + ' ' + T.money(n, c); }).join('  '); }
    var TX_COLS = [
      { key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); }, sort: function (r) { return r.ts; } },
      { key: 'entry_id', label: 'entry', cls: 'l', text: function (r) { return String(r.entry_id); }, sort: function (r) { return r.entry_id; } },
      { key: 'kind', label: 'kind', cls: 'l' },
      { key: 'accounts', label: 'account', cls: 'l', html: function (r) { var n = r.account_names || {}; return wrapHtml((r.entry_accounts || r.accounts || []).map(function (a) { return acctName(a, n); }).join(', '), 150); },
        text: function (r) { var n = r.account_names || {}; return (r.entry_accounts || r.accounts || []).map(function (a) { return acctName(a, n); }).join(', '); } },
      { key: 'memo', label: 'memo', cls: 'l', html: function (r) { return wrapHtml(r.memo || '', 300, r.memo_stored || r.memo || ''); }, text: function (r) { return r.memo || ''; } },
      { key: 'instrument_id', label: 'instrument', cls: 'l', text: function (r) { return r.instrument_id || ''; } },
      { key: 'qty', label: 'qty', text: function (r) { return r.qty == null ? '' : T.fmt.qty(r.qty); } },
      { key: 'price', label: 'price', text: function (r) { return r.price == null ? '' : T.fmt.px(r.price); } },
      { key: 'gross', label: 'gross', text: function (r) { return r.gross == null && r.gross_c == null ? '' : T.money(r, 'gross'); } },
      { key: 'fee', label: 'fee', text: function (r) { return r.fee == null && r.fee_c == null ? '' : T.money(r, 'fee'); } },
      { key: 'net_cash', label: 'net cash', html: function (r) { var n = r.net_cash || {}; return Object.keys(n).map(function (c) { return '<span class="' + T.sign(n, c) + '">' + T.esc(c + ' ' + T.money(n, c)) + '</span>'; }).join(' '); }, text: netCash },
      { key: 'other_side', label: 'other side', cls: 'l', html: function (r) { return wrapHtml(r.other_side || '', 170); }, text: function (r) { return r.other_side || ''; } }];
    function showTxn(r) {
      entryModal(r, { title: 'entry ' + r.entry_id + ' . ' + (r.kind || '') + ' . ' + (r.ts_ny || T.fmt.dt(r.ts)),
        extra: [['other side', r.other_side || ''], ['net cash', netCash(r)], ['request', r.request_id || ''], ['command', r.command_id == null ? '' : String(r.command_id)]] });
    }
    function transactions() {
      var from = T.h('input', { type: 'date', 'aria-label': 'from' }), to = T.h('input', { type: 'date', 'aria-label': 'to' });
      var kinds = T.h('input', { type: 'text', placeholder: 'kinds (comma)', style: 'width:140px' }), inst = T.h('input', { type: 'text', placeholder: 'instrument', style: 'width:100px' });
      var csv = T.h('a', { class: 'btn', target: '_blank', rel: 'noopener', title: 'every entry of this scope as the server writes it (lines expanded into net cash per currency)' }, 'Server CSV');
      var sum = T.h('span', { class: 'hint' });
      var gEl = T.h('div');
      var grid = null;
      function params(fmt) { return { scope: scope, from: from.value || null, to: to.value || null, kinds: kinds.value.trim() || null, instrument: inst.value.trim() || null, limit: 1000, format: fmt || null }; }
      function fetchRows() {
        csv.href = hist ? T.api.url(base + 'transactions', params('csv')) : T.api.url('/api/export/transactions.csv', { scope: scope, from: from.value || null, to: to.value || null });
        return T.api.get(base + 'transactions', params()).then(function (r) {
          var rows = r.rows || r.entries || [];
          if (!grid) { T.clear(gEl); grid = T.components.grid.mount(gEl, { columns: TX_COLS, rows: [], csv: 'transactions-' + scope + '.csv', empty: 'loading ...', sortKey: 'ts', sortAsc: false, focus: true, onEnter: showTxn }); }
          var filtered = from.value || to.value || kinds.value.trim() || inst.value.trim();
          grid.update(rows, { empty: filtered ? 'no transactions match the filter' : 'no transactions' });
          sum.textContent = rows.length + ' entries of ' + scopeName() + (rows.length >= 1000 ? ' (the newest 1,000; the CSV has every entry)' : '');
        }, function (e) { T.clear(gEl); grid = null; T.errorBox(gEl, e, 'transactions'); });
      }
      [from, to].forEach(function (i) { i.addEventListener('change', fetchRows); });
      [kinds, inst].forEach(function (i) { i.addEventListener('keydown', function (e) { if (e.key === 'Enter') { fetchRows(); } }); });
      T.clear(body);
      body.appendChild(T.h('div', { class: 'row', style: 'margin-bottom:6px; flex-wrap:wrap; gap:6px' }, from, to, kinds, inst, T.h('button', { class: 'btn small', onclick: fetchRows }, 'Apply'), csv, sum));
      body.appendChild(gEl);
      body.appendChild(T.h('div', { class: 'hint' }, 'Enter (or a double click) on a row opens the entry with its lines (Dr / Cr).'));
      body.appendChild(T.convNote(['COST_BASIS', 'SWEEP', 'ACCT_DAY']));
      return fetchRows();
    }

    /* ---- Cash & financing of the scope (BM-13) ---- */
    function section(title, sub, rows, cols, empty, extra) {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, title, sub ? T.h('small', null, sub) : null));
      var g = T.h('div'); p.appendChild(g);
      T.components.grid.mount(g, Object.assign({ tools: rows.length > 12, rows: rows, columns: cols, empty: empty, maxHeight: '360px' }, extra || {}));
      return p;
    }
    function acctCol(all) {
      return { key: 'account_id', label: 'account', cls: 'l', text: function (r) { return (r.account_name || T.accountName(r.account_id)) + (all && r.book_name ? ' . ' + r.book_name : ''); } };
    }
    function cash() {
      return T.api.get(base + 'cash', { scope: scope }).then(function (d) {
        T.clear(body);
        var all = scope === 'ALL_BOOKS';
        var tot = d.totals || [];
        body.appendChild(T.tiles(tot.slice(0, 4).map(function (t) { return { k: t.currency + ' settled', v: T.money(t, 'settled'), s: 'available ' + T.money(t, 'available') + ' . receivable ' + T.money(t, 'receivable') + ' . payable ' + T.money(t, 'payable') }; })
          .concat((d.treasury || []).map(function (t) { var n = T.num(t, 'available_signed'); return { k: 'Treasury' + (all || (d.treasury || []).length > 1 ? ' . ' + (t.book_name || t.lifetime_id) : '') + ' can sweep', v: T.money(t, 'available'), cls: n < 0 ? 'neg' : (n > 0 ? 'pos' : ''), s: 'reserve ' + T.money(t, 'reserve') + (n < 0 ? ' is above the sweepable cash (' + T.money(t, 'available_signed') + ')' : ''), href: all ? null : '#/treasury' }; }))));
        body.appendChild(section('cash by account and currency', 'own available is floored at zero; what working orders and payables need beyond it is reserved', d.cash || [], [acctCol(all),
          { key: 'currency', label: 'ccy', cls: 'l' }, moneyCol('settled', 'settled', { plain: true }), moneyCol('receivable', 'receivable', { plain: true }), moneyCol('payable', 'payable', { plain: true }),
          moneyCol('reserved', 'reserved', { plain: true }), moneyCol('available', 'available', { plain: true }), moneyCol('margin', 'margin', { plain: true })], 'no cash',
          { rowKey: function (r) { return r.account_id + '|' + r.currency; }, onEnter: function (r) { T.router.go(T.router.account(r.account_id, 'cash')); } }));
        body.appendChild(section('settlement ladder', 'pending settlements by due date', d.ladder || [], [{ key: 'due_date', label: 'due', cls: 'l' }, acctCol(all),
          { key: 'what', label: 'for', cls: 'l', text: function (r) { return r.what || r.leg || ''; } }, { key: 'direction', label: 'pay / receive', cls: 'l' },
          { key: 'currency', label: 'ccy', cls: 'l' }, moneyCol('amount')], 'no pending settlements', { rowKey: function (r) { return r.settle_id; } }));
        body.appendChild(section('sweeps', 'Treasury sweep-ins (what each paid for) and sweep-outs of idle cash; the newest 100', d.sweeps || [], [
          { key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); }, sort: function (r) { return r.ts; } }, acctCol(all),
          { key: 'direction', label: 'direction', cls: 'l', text: function (r) { return r.direction === 'SWEEP_IN' ? 'sweep-in' : 'sweep-out'; } }, { key: 'currency', label: 'ccy', cls: 'l' }, moneyCol('amount', 'amount', { plain: true }),
          { key: 'reason', label: 'reason (what the sweep paid for)', cls: 'l', html: function (r) { return reasonHtml(r); }, text: function (r) { return r.reason || ''; } }], 'no sweeps',
          { rowKey: function (r) { return r.transfer_id; }, onEnter: function (r) { goLink(r.link); } }));
        body.appendChild(section('loans', 'currency loans and prime-broker financing', d.loans || [], [acctCol(all), { key: 'kind', label: 'kind', cls: 'l', text: function (r) { return r.kind === 'PB_MARGIN' ? 'PB financing' : 'currency loan'; } },
          { key: 'currency', label: 'ccy', cls: 'l' }, moneyCol('principal', 'principal', { plain: true }), { key: 'lender', label: 'lender', cls: 'l', text: function (r) { return r.counterparty_name || r.lender || ''; } },
          { key: 'index_name', label: 'rate', cls: 'l', text: function (r) { return (r.index_name || '') + (r.spread != null ? ' + ' + T.fmt.rate(r.spread) : ''); } },
          { key: 'maturity_date', label: 'matures', cls: 'l', text: function (r) { return r.maturity_date || 'open'; } }], 'no open loans', { rowKey: function (r) { return r.loan_id; } }));
        body.appendChild(section('margin', 'futures initial margin, option margin reserved on cash, OTC margin; collateral held at lenders', d.margin || [], [acctCol(all),
          moneyCol('futures_required', 'futures req.', { plain: true }), moneyCol('futures_posted', 'futures posted', { plain: true }), moneyCol('options_reserved', 'options reserved', { plain: true }),
          moneyCol('otc_im', 'OTC IM', { plain: true }), moneyCol('otc_vm_posted', 'OTC VM posted', { plain: true }), moneyCol('collateral_held_at_lender', 'collateral', { plain: true })], 'no margin or collateral', { rowKey: function (r) { return r.account_id; } }));
        var fin = T.h('div', { class: 'grid2' }); body.appendChild(fin);
        fin.appendChild(section('stock borrows', 'shorts borrowed: lender, fee, cash collateral', d.borrows || [], [acctCol(all), { key: 'symbol', label: 'instrument', cls: 'l', text: function (r) { return r.symbol || r.instrument_id || ''; } },
          { key: 'qty', label: 'qty', text: function (r) { return T.fmt.qty(Number(r.qty || 0) - Number(r.qty_returned || 0)); } }, { key: 'lender', label: 'lender', cls: 'l' },
          { key: 'fee_rate', label: 'fee', text: function (r) { return r.fee_rate == null ? '' : T.fmt.rate(r.fee_rate); } }, moneyCol('collateral', 'collateral', { plain: true })], 'no open borrows', { rowKey: function (r) { return r.borrow_id; } }));
        fin.appendChild(section('repo and securities lent', 'open repos / reverse repos and shares lent out', (d.repos || []).map(function (r) { return Object.assign({ what: (r.side === 'REVERSE' ? 'reverse repo ' : 'repo ') + (r.symbol || r.instrument_id || '') + ' face ' + T.fmt.qty(r.face), party: r.counterparty_name || '', amt: r.principal, amt_c: r.principal_c }, r); })
          .concat((d.lent || []).map(function (r) { return Object.assign({ what: 'lent ' + T.fmt.qty(Number(r.qty || 0) - Number(r.qty_recalled || 0)) + ' ' + (r.symbol || r.instrument_id || ''), party: r.counterparty_name || '', amt: r.collateral, amt_c: r.collateral_c }, r); })),
          [acctCol(all), { key: 'what', label: 'what', cls: 'l' }, { key: 'party', label: 'counterparty', cls: 'l' }, moneyCol('amt', 'cash', { plain: true }), { key: 'maturity_date', label: 'matures', cls: 'l', text: function (r) { return r.maturity_date || ''; } }],
          'no repos and no securities lent', { rowKey: function (r) { return (r.repo_id || r.loan_id || ''); } }));
        body.appendChild(T.convNote(['SWEEP', 'TREASURY', 'ENCUMBRANCE']));
      }, function (e) { T.clear(body); T.errorBox(body, e, 'cash & financing'); });
    }
    function reasonHtml(r) {
      var l = r.link;
      if (l && l.account_id && (l.kind === 'fill' || l.kind === 'fx_deal' || l.kind === 'settlement')) {
        return '<a href="' + T.esc(T.router.account(l.account_id, l.kind === 'fill' ? 'orders' : (l.kind === 'settlement' ? 'cash' : 'transactions'))) + '" title="open ' + T.esc(T.accountName(l.account_id)) + '">' + T.esc(r.reason || '') + '</a>';
      }
      return T.esc(r.reason || '');
    }
    function goLink(l) { if (l && l.account_id) { T.router.go(T.router.account(l.account_id, l.kind === 'fill' ? 'orders' : (l.kind === 'settlement' ? 'cash' : 'transactions'))); } }

    /* ---- Ledger ---- */
    function ledger() {
      var gl = T.h('input', { type: 'text', placeholder: 'GL', style: 'width:70px' }), inst = T.h('input', { type: 'text', placeholder: 'instrument', style: 'width:100px' }), from = T.h('input', { type: 'date' }), to = T.h('input', { type: 'date' });
      var tools = T.h('div', { class: 'row', style: 'margin-bottom:6px; gap:6px' }, gl, inst, from, to, T.h('button', { class: 'btn small', onclick: function () { fetchEntries(); } }, 'Apply'));
      var gEl = T.h('div');
      var grid = null;
      function openEntry(r) {
        entryModal(T.api.get(base + 'journal/' + encodeURIComponent(r.entry_id)), { title: 'entry ' + r.entry_id + ' . ' + (r.kind || '') });
      }
      function fetchEntries() {
        return T.api.get(base + 'journal', { scope: scope, gl: gl.value.trim() || null, instrument: inst.value.trim() || null, from: from.value || null, to: to.value || null, limit: 300 }).then(function (j) {
          var rows = Array.isArray(j) ? j : (j.entries || j.rows || []);
          if (!grid) {
            T.clear(gEl);
            grid = T.components.grid.mount(gEl, { rows: [], csv: 'journal.csv', empty: 'loading ...', sortKey: 'ts', sortAsc: false, focus: true, columns: [
              { key: 'entry_id', label: 'entry', cls: 'l', text: function (r) { return String(r.entry_id); }, sort: function (r) { return r.entry_id; } }, { key: 'ts', label: 'time', cls: 'l', text: function (r) { return r.ts_ny || T.fmt.dt(r.ts); }, sort: function (r) { return r.ts; } }, { key: 'business_date', label: 'bdate', cls: 'l' }, { key: 'kind', label: 'kind', cls: 'l' }, { key: 'memo', label: 'memo', cls: 'l' },
              { key: 'lines', label: 'lines (Dr / Cr)', cls: 'l', html: function (r) { return (r.lines || []).map(function (l) { return '<div>' + T.esc((l.account_name || l.account_id || '') + ' ' + (l.gl || '') + ' ' + (l.currency || '') + ' ' + (l.debit != null || l.debit_c != null ? 'Dr ' + T.money(l, 'debit') : '') + (l.credit != null || l.credit_c != null ? 'Cr ' + T.money(l, 'credit') : '') + (l.instrument_id ? ' ' + l.instrument_id : '')) + '</div>'; }).join(''); }, text: function (r) { return (r.lines || []).length + ' lines'; } },
              { key: 'command_id', label: 'command', cls: 'l', text: function (r) { return r.command_id == null ? '' : String(r.command_id); } }],
              onEnter: openEntry });
          }
          grid.update(rows, { empty: (gl.value.trim() || inst.value.trim() || from.value || to.value) ? 'no entries match the filter' : 'no entries' });
        }, function (e) { T.clear(gEl); grid = null; T.errorBox(gEl, e, 'journal'); });
      }
      T.clear(body); body.appendChild(tools); body.appendChild(gEl);
      body.appendChild(T.h('div', { class: 'hint' }, 'Enter (or a double click) on an entry opens it as a table: the entry, its lines with Dr / Cr and what it links to.'));
      return fetchEntries();
    }

    /* ---- Trial balance ---- */
    function trial() {
      return T.api.get(base + 'trial-balance', { scope: scope }).then(function (tb) {
        T.clear(body);
        body.appendChild(T.tiles([{ k: 'balanced', v: tb.balanced === false ? 'NO' : 'yes', cls: tb.balanced === false ? 'neg' : 'pos', s: 'debits == credits per currency and in base' }]));
        var g = T.h('div'); body.appendChild(g);
        T.components.grid.mount(g, { rows: tb.rows || [], csv: 'trial-balance.csv', empty: 'no balances', columns: [{ key: 'gl', label: 'gl', cls: 'l' }, { key: 'name', label: 'account', cls: 'l', text: function (r) { return r.name || ''; } }, { key: 'currency', label: 'ccy', cls: 'l' }, moneyCol('debit', 'debit', { plain: true }), moneyCol('credit', 'credit', { plain: true }), moneyCol('balance')], footer: function () { var t = tb.totals || {}; return { gl: 'totals', debit: T.esc(T.money(t, 'debit')), credit: T.esc(T.money(t, 'credit')) }; } });
      }, function (e) { T.clear(body); T.errorBox(body, e, 'trial balance'); });
    }

    /* ---- Integrity: one row per check the run made (BM-26); Run now only on the live page (SHELL-23) ---- */
    function integrity(mode) {
      var p = mode ? { mode: mode, scope: scope } : { scope: scope };
      return T.api.get(base + 'integrity', p).then(function (r) {
        T.clear(body);
        if (!readOnly) {
          body.appendChild(T.h('div', { class: 'row', style: 'margin-bottom:8px; gap:6px' },
            T.h('button', { class: 'btn', onclick: function () { integrity('ledger'); } }, 'Run now (ledger)'), T.h('button', { class: 'btn', onclick: function () { integrity('full'); } }, 'Run now (full)'),
            T.h('span', { class: 'hint' }, 'checks the whole database on a reader; the ledger checks of the accounts a command touches already run inside every command')));
        } else {
          body.appendChild(T.h('div', { class: 'hint', style: 'margin-bottom:8px' }, 'read-only: the checks below were run now over the whole database and are shown for this lifetime' +
            (r.frozen ? '; at the freeze the stored verdict was ' + (r.frozen.ok ? 'OK' : 'FAILED') + (r.frozen.checked_at ? ' (' + T.fmt.dt(r.frozen.checked_at) + ')' : '') : '')));
        }
        var checks = r.checks || [];
        var failed = checks.filter(function (c) { return !c.ok; }).length;
        var warned = checks.filter(function (c) { return c.ok && Number(c.warnings || 0) > 0; }).length;
        body.appendChild(T.tiles([
          { k: 'result', v: r.ok ? 'OK' : 'FAILED', cls: r.ok ? 'pos' : 'neg', s: (r.mode || '') + (r.checked_at ? ' . ' + T.fmt.dt(r.checked_at) : '') + (r.ok_all === false && r.ok ? ' . another lifetime fails' : '') },
          { k: 'checks run', v: String(checks.length), s: failed ? failed + ' failing' : (warned ? warned + ' with warnings' : 'every check passed') , cls: failed ? 'neg' : '' },
          { k: 'hold', v: r.hold ? 'INTEGRITY_HOLD' : 'none', cls: r.hold ? 'neg' : '', s: r.hold ? 'new orders of this lifetime are refused until a clean run' : '' },
          { k: 'lifetime', v: r.lifetime_id || 'all books', s: r.scope_note || '' }]));
        var g = T.h('div'); body.appendChild(g);
        T.components.grid.mount(g, { tools: false, rows: checks, empty: 'the report lists no checks', rowKey: function (c) { return c.code; }, columns: [
          { key: 'code', label: 'check', cls: 'l', text: function (c) { return c.code || c.check || ''; } }, { key: 'name', label: 'what it proves', cls: 'l', text: function (c) { return c.name || ''; } },
          { key: 'ok', label: 'result', cls: 'l', html: function (c) { return c.ok ? (Number(c.warnings || 0) ? '<span style="color: var(--warn)">ok, ' + T.esc(String(c.warnings)) + ' warning(s)</span>' : '<span class="pos">ok</span>') : '<span class="neg">FAIL</span>'; }, text: function (c) { return c.ok ? 'ok' : 'FAIL'; } },
          { key: 'count', label: 'findings', text: function (c) { return c.count == null ? '' : String(c.count); } },
          { key: 'message', label: 'message', cls: 'l', text: function (c) { return c.message || ''; } }], rowClass: function (c) { return c.ok ? '' : 'neg'; } });
      }, function (e) { T.clear(body); T.errorBox(body, e, 'integrity'); });
    }
    load();
    var off = T.state.on('accounts', function () { if (!opts.scope) { fillScopes(); } });
    T.pages.accounting.unmount = function () { dead = true; off(); T.pages.accounting.unmount = null; };
  }
  T.pages.accounting = { render: render, unmount: null, TABS: TABS, entryModal: entryModal, entryBody: entryBody };
})();
