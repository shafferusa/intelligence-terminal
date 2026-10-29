/* The historical-import panel (FINSIM1_PLAN C19 / P5 task 8; directive section 17): the import batches of the selected book, the
 * decision and outcome of every line, the business-date cash table, the funding moves, the restated NAV with its split and the
 * bridge to the owner's workbook.  Mounted by the History hub's Imports tab (pages/history.js), which calls it only when the import
 * tool is in the build (T.hasModule('importer'), C2):
 *
 *     var h = T.components.imports.render(el, {book: 'F'});   // fetches at once
 *     h.unmount();
 *
 * Routes (read-only): GET /api/import/batches (the batches with their book and state), GET /api/import/batches/{id} (its stored
 * record and `view`: every label, word and figure ready to show), POST /api/import/report {batch_id} (a read-only check of the batch
 * against the book as it stands now; nothing is written).  The panel computes nothing: it shows the server's figures (decimal
 * strings, the API shape of the view's cents) and words.  No dependencies; ASCII.
 */
(function () {
  'use strict';
  var T = window.T;
  T.components = T.components || {};

  var TABS = [{ key: 'lines', label: 'Lines', title: 'every line of the batch: its decision, what the book held and what the import did' },
              { key: 'cash', label: 'Business-date cash', title: 'each account\'s settled cash at the close of each business date' },
              { key: 'funding', label: 'Funding moves', title: 'the sweeps and transfers the import made, each on its business date' },
              { key: 'nav', label: 'Restated NAV', title: 'the restated NAV of each date with its split' },
              { key: 'bridge', label: 'Bridge to the workbook', title: 'from the workbook\'s reconstructed NAV to the restated NAV, line by line' }];

  /* a money field of a server row (T.money: the API's decimal string, or cents in the view's own shape); signed adds the + */
  function money(row, name) { return T.money(row, name); }
  function signed(row, name) { var n = T.num(row, name); var s = T.money(row, name); return n != null && n > 0 ? '+' + s : s; }
  function cls(row, name) { var n = T.num(row, name); return n == null || n === 0 ? 'num' : (n > 0 ? 'num pos' : 'num neg'); }
  function plain(row, name) { var n = T.num(row, name); return n == null ? '' : n.toFixed(2); }
  function moneyCell(key, label, opts) {
    opts = opts || {};
    return { key: key, label: label, cls: 'num', title: function () { return opts.title || ''; },
             html: function (r) { return '<span class="' + (opts.signed ? cls(r, key) : 'num') + '">' + T.esc(opts.signed ? signed(r, key) : money(r, key)) + '</span>'; },
             sort: function (r) { var n = T.num(r, key); return n == null ? '' : n; }, csv: function (r) { return plain(r, key); } };
  }
  function txt(key, label, opts) {
    opts = opts || {};
    return { key: key, label: label, cls: opts.cls || 'l', text: function (r) { return r[key] == null ? '' : String(r[key]); },
             title: function (r) { return opts.titleKey ? String(r[opts.titleKey] || '') : ''; } };
  }

  function render(el, opts) {
    opts = opts || {};
    var dead = false;
    var grids = [];
    var st = { batches: [], id: null, view: null, tab: T.store.get('imports_tab', 'lines'), now: null, navDate: null, lineNo: null };
    var root = T.h('div', { class: 'imports' });
    el.appendChild(root);

    function drop() { grids.forEach(function (g) { try { g.destroy(); } catch (e) { /* gone */ } }); grids = []; }
    function grid(host, spec) { var g = T.components.grid.mount(host, spec); grids.push(g); return g; }

    function draw() {
      if (dead) { return; }
      drop(); T.clear(root);
      if (!T.hasModule('importer')) {
        root.appendChild(T.h('div', { class: 'empty' }, 'no import batches (the import tool is not in this build)'));
        return;
      }
      var mine = st.batches.filter(function (b) { return !opts.book || !b.book_code || b.book_code === opts.book; });
      var others = st.batches.length - mine.length;
      var head = T.h('div', { class: 'row', style: 'gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:6px' }, T.h('h2', { style: 'margin:0' }, 'Historical imports'));
      root.appendChild(head);
      if (!mine.length) {
        root.appendChild(T.h('div', { class: 'empty' }, 'no import batches for this book' + (others ? ' (' + others + ' batch' + (others === 1 ? '' : 'es') + ' of other books)' : '')));
        return;
      }
      if (!st.id || !mine.some(function (b) { return b.batch_id === st.id; })) {
        var applied = mine.filter(function (b) { return b.applied_ts; });
        st.id = (applied.length ? applied[applied.length - 1] : mine[mine.length - 1]).batch_id;
      }
      var sel = T.h('select', { 'aria-label': 'import batch' });
      mine.forEach(function (b) { sel.appendChild(T.h('option', { value: b.batch_id }, b.batch_id + ' - ' + (b.state || ''))); });
      sel.value = st.id;
      sel.addEventListener('change', function () { st.id = sel.value; st.view = null; st.now = null; load(); });
      head.appendChild(T.h('label', null, 'batch ', sel));
      head.appendChild(T.h('span', { class: 'spacer' }));
      var chk = T.h('button', { class: 'btn small', title: 'compare every line with the book as it stands now (read-only: nothing is written)' }, 'Check against the book now');
      chk.addEventListener('click', function () { checkNow(chk); });
      head.appendChild(chk);
      if (!st.view) { root.appendChild(T.h('div', { class: 'empty' }, 'loading ...')); return; }
      var v = st.view;
      root.appendChild(T.kvGrid((v.summary || []).map(function (p) { return [p[0], p[1] == null ? 'n/a' : String(p[1]), p[2] || '']; })));
      if (v.verify_only) {
        root.appendChild(T.h('div', { class: 'hint' }, 'The last run found every import line already in the book: it checked and reported and wrote nothing.'));
      }
      if (st.now) {
        root.appendChild(T.h('div', { class: 'hint' }, 'Checked against the book ' + st.now.at + ': ' + st.now.counts + ' (the "Found -> now" column of the lines).'));
      }
      root.appendChild(T.tabs(TABS.map(function (t) { return { key: t.key, label: t.label, title: t.title }; }), st.tab, function (k) {
        st.tab = k; T.store.set('imports_tab', k); draw();
      }));
      var body = T.h('div'); root.appendChild(body);
      if (st.tab === 'cash') { cashTab(body, v); }
      else if (st.tab === 'funding') { fundingTab(body, v); }
      else if (st.tab === 'nav') { navTab(body, v); }
      else if (st.tab === 'bridge') { bridgeTab(body, v); }
      else { linesTab(body, v); }
      if ((v.notes || []).length) {
        root.appendChild(T.h('h2', null, 'Notes of the last run'));
        var ul = T.h('ul', { class: 'hint' });
        v.notes.forEach(function (n) { ul.appendChild(T.h('li', null, n)); });
        root.appendChild(ul);
      }
    }

    /* ---- lines ---- */
    function linesTab(body, v) {
      var drawer = T.h('div'); body.appendChild(drawer);
      var host = T.h('div'); body.appendChild(host);
      var now = st.now ? st.now.byLine : null;
      var cols = [{ key: 'line_no', label: '#', cls: 'num', text: function (r) { return String(r.line_no); }, sort: function (r) { return r.line_no; } },
                  txt('product_short', 'Product', { titleKey: 'product' }),
                  { key: 'instrument', label: 'Instrument', cls: 'l', title: function (r) { return r.instrument_full || ''; },
                    html: function (r) { return T.esc(r.instrument) + (r.asof ? ' <span class="badge-asof" title="' + T.esc(r.source) + '">AS-OF</span>' : '') +
                                                (r.historical_model ? ' <span class="badge-model" title="' + T.esc(r.source) + '">MODEL</span>' : ''); },
                    text: function (r) { return r.instrument; } },
                  txt('side_short', 'Side', { titleKey: 'side' }), txt('qty_short', 'Qty', { cls: 'num', titleKey: 'qty' }),
                  txt('price_short', 'Price', { cls: 'num', titleKey: 'price' }), txt('date_short', 'Date', { titleKey: 'trade_date' }), txt('account', 'Account'),
                  txt('decision_short', 'Decision', { titleKey: 'decision' }),
                  /* what the run found; after "Check against the book now" also what the book holds now ("missing -> imported") */
                  { key: 'found', label: now ? 'Found -> now' : 'Found', cls: 'l',
                    text: function (r) { var x = now && now[r.line_no]; return x && x.status_short !== r.status_short ? r.status_short + ' -> ' + x.status_short : r.status_short; },
                    title: function (r) { var x = now && now[r.line_no]; return r.status + (x ? '; now: ' + x.status_text + (x.differences.length ? ': ' + x.differences.join('; ') : '') : ''); } },
                  txt('action_short', 'Result', { titleKey: 'action' })];
      var g = grid(host, { columns: cols, rows: v.lines || [], csv: 'import-' + v.batch_id + '-lines.csv', empty: 'no lines', focus: false,
                           rowKey: function (r) { return 'L' + r.line_no; }, onEnter: function (r) { st.lineNo = r.line_no; lineDrawer(drawer, r); },
                           onClick: function (r) { st.lineNo = r.line_no; lineDrawer(drawer, r); } });
      if (st.lineNo != null) {
        var r0 = (v.lines || []).filter(function (r) { return r.line_no === st.lineNo; })[0];
        if (r0) { lineDrawer(drawer, r0); }
      }
      body.appendChild(T.h('div', { class: 'hint' }, 'Enter or a click on a line shows its detail. Preserved lines are never altered: a difference from the workbook is reported, never corrected.'));
      return g;
    }
    function lineDrawer(host, r) {
      T.clear(host);
      var now = st.now && st.now.byLine[r.line_no];
      var close = T.h('button', { class: 'btn small', title: 'close the detail' }, 'Close');
      close.addEventListener('click', function () { st.lineNo = null; T.clear(host); });
      var d = T.h('div', { class: 'drawer' },
        T.h('div', { class: 'drawer-head' }, T.h('h2', null, 'Line ' + r.line_no + ': ' + r.product), r.asof ? T.h('span', { class: 'badge-asof' }, 'AS-OF') : null,
            r.historical_model ? T.h('span', { class: 'badge-model' }, 'MODEL') : null, T.h('span', { class: 'spacer' }), close),
        T.kvGrid([['Instrument', r.instrument_full || r.instrument], ['Side', r.side], ['Quantity', r.qty], ['Price / terms', r.price || 'n/a'],
                  ['Trade date', r.trade_date || 'n/a'], ['Account', r.account || 'n/a'], ['Counterparty', r.counterparty || 'n/a'],
                  ['Decision', r.decision], ['Found at the run', r.status], now ? ['Found now', now.status_text] : null, ['Result', r.action],
                  r.applied_et ? ['Booked', r.applied_et] : null, ['Rows created', r.created || 'none']]),
        r.source ? T.h('div', { class: 'hint' }, 'Source: ' + r.source) : null);
      var diffs = (now && now.differences.length ? now.differences : r.differences) || [];
      if (diffs.length) {
        d.appendChild(T.h('div', { class: 'hint' }, (now ? 'Differences now: ' : 'Differences: ') + diffs.join('; ')));
      }
      if (r.note) { d.appendChild(T.h('div', { class: 'hint' }, r.note)); }
      host.appendChild(d);
    }

    /* ---- business-date cash ---- */
    function cashTab(body, v) {
      var c = v.cash || { dates: [], rows: [] };
      if (!c.rows.length) { body.appendChild(T.h('div', { class: 'empty' }, 'no business-date cash table in this run\'s report')); return; }
      var cols = [txt('account', 'Account'), txt('stage', 'When')];
      c.dates.forEach(function (d) {
        cols.push({ key: d, label: d.slice(5), cls: 'num', title: function () { return 'settled cash at the close of ' + d; },
                    html: function (r) { var o = { v: (r.values || {})[d] }; var n = T.num(o, 'v'); return '<span class="' + (n != null && n < 0 ? 'num neg' : 'num') + '">' + T.esc(money(o, 'v')) + '</span>'; },
                    sort: function (r) { var n = T.num({ v: (r.values || {})[d] }, 'v'); return n == null ? '' : n; }, csv: function (r) { return plain({ v: (r.values || {})[d] }, 'v'); } });
      });
      var host = T.h('div'); body.appendChild(host);
      grid(host, { columns: cols, rows: c.rows, csv: 'import-' + v.batch_id + '-business-date-cash.csv', empty: 'no rows',
                   rowKey: function (r) { return r.account + '|' + r.stage; }, rowClass: function (r) { return r.below_zero ? 'neg' : ''; } });
      body.appendChild(T.h('div', { class: 'hint' }, 'Settled cash of each account at the close of each business date. Every account must close every date at or above zero; the as-of sweeps and transfers are dated to the business date they belong to.'));
    }

    /* ---- funding moves ---- */
    function fundingTab(body, v) {
      var host = T.h('div'); body.appendChild(host);
      grid(host, { columns: [txt('transfer_id', 'Transfer'), txt('kind', 'Kind'), txt('from', 'From'), txt('to', 'To'), moneyCell('amount', 'Amount'),
                             txt('business_date', 'Business date'), txt('for', 'For')],
                   rows: v.funding || [], csv: 'import-' + v.batch_id + '-funding.csv', empty: 'the import made no funding move',
                   rowKey: function (r) { return r.transfer_id; } });
      body.appendChild(T.h('div', { class: 'hint' }, 'Internal sweeps and transfers move cash between the fund\'s accounts: they change no NAV.'));
    }

    /* ---- restated NAV ---- */
    function navTab(body, v) {
      var rs = v.restated || { book: [], accounts: [] };
      if (!rs.book.length) { body.appendChild(T.h('div', { class: 'empty' }, 'no restated NAV in this run\'s report')); return; }
      var drawer = T.h('div'); body.appendChild(drawer);
      var host = T.h('div'); body.appendChild(host);
      grid(host, { columns: [txt('date', 'Date'), moneyCell('original', 'Original NAV', { title: 'the snapshot as it was taken (0 where none existed)' }),
                             moneyCell('import', 'Import', { signed: true, title: 'the import\'s effect, by line in the detail' }),
                             moneyCell('interest', 'Interest', { signed: true, title: 'interest restated on business-date balances' }),
                             moneyCell('mark_policy', 'Mark policy', { signed: true, title: 'the preserved positions at the day\'s official closes / settles' }),
                             moneyCell('dating', 'Dating', { signed: true, title: 'entries the original snapshot counted on another business date' }),
                             moneyCell('restated', 'Restated NAV'), moneyCell('residual', 'Residual', { title: 'must be 0' })],
                   rows: rs.book, csv: 'import-' + v.batch_id + '-restated-nav.csv', empty: 'no rows', rowKey: function (r) { return r.date; },
                   onEnter: function (r) { st.navDate = r.date; navDrawer(drawer, r); }, onClick: function (r) { st.navDate = r.date; navDrawer(drawer, r); } });
      if (st.navDate) {
        var r0 = rs.book.filter(function (r) { return r.date === st.navDate; })[0];
        if (r0) { navDrawer(drawer, r0); }
      }
      body.appendChild(T.h('div', { class: 'hint' }, 'The whole book. Original + import + interest + mark policy + dating = restated, with a residual of 0. The originals are kept beside the restated versions; Enter or a click on a date shows its split.'));
      if (rs.accounts.length) {
        body.appendChild(T.h('h2', null, 'By account (original / restated)'));
        var dates = rs.book.map(function (r) { return r.date; });
        var cols = [txt('account', 'Account')];
        dates.forEach(function (d) {
          cols.push({ key: d, label: d.slice(5), cls: 'num', title: function (r) { var x = r.dates[d]; return x && x.added ? 'added by the restatement (no original snapshot)' : 'original / restated'; },
                      html: function (r) { var x = r.dates[d]; return x ? T.esc(money(x, 'original')) + '<br>' + T.esc(money(x, 'restated')) + (x.added ? ' *' : '') : 'n/a'; },
                      sort: function (r) { var x = r.dates[d]; var n = x ? T.num(x, 'restated') : null; return n == null ? '' : n; },
                      csv: function (r) { var x = r.dates[d]; return x ? plain(x, 'original') + ' / ' + plain(x, 'restated') : ''; } });
        });
        var ah = T.h('div'); body.appendChild(ah);
        grid(ah, { columns: cols, rows: rs.accounts, csv: 'import-' + v.batch_id + '-restated-by-account.csv', empty: 'no rows', rowKey: function (r) { return r.account; } });
        body.appendChild(T.h('div', { class: 'hint' }, '* added by the restatement: the account had no snapshot that day (it opened after the day\'s snapshot).'));
      }
    }
    function navDrawer(host, r) {
      T.clear(host);
      var close = T.h('button', { class: 'btn small' }, 'Close');
      close.addEventListener('click', function () { st.navDate = null; T.clear(host); });
      var d = T.h('div', { class: 'drawer' }, T.h('div', { class: 'drawer-head' }, T.h('h2', null, 'Restated NAV of ' + r.date), T.h('span', { class: 'spacer' }), close),
        T.kvGrid([['Original NAV', money(r, 'original')], ['Import', signed(r, 'import')], ['Interest', signed(r, 'interest')], ['Mark policy', signed(r, 'mark_policy')],
                  ['Dating', signed(r, 'dating')], ['Restated NAV', money(r, 'restated')], ['Residual', money(r, 'residual')]]));
      if ((r.by_line || []).length) {
        d.appendChild(T.h('h2', null, 'Import effect by line'));
        var t1 = T.h('div'); d.appendChild(t1);
        grid(t1, { columns: [{ key: 'line_no', label: '#', cls: 'num', text: function (x) { return x.line_no == null ? '' : String(x.line_no); } }, txt('what', 'Line'),
                             moneyCell('amount', 'Effect', { signed: true })], rows: r.by_line, empty: 'none', rowKey: function (x) { return String(x.line_no) + x.what; } });
      }
      if ((r.mark_policy || []).length) {
        d.appendChild(T.h('h2', null, 'Mark policy by position'));
        var t2 = T.h('div'); d.appendChild(t2);
        grid(t2, { columns: [txt('instrument', 'Instrument'), txt('original_mark', 'Snapshot mark', { cls: 'num' }), txt('dated_mark', 'Dated mark', { cls: 'num' }),
                             moneyCell('amount', 'Effect', { signed: true })], rows: r.mark_policy, empty: 'none', rowKey: function (x) { return x.instrument; } });
      }
      if ((r.dating || []).length) {
        d.appendChild(T.h('h2', null, 'Dating (' + r.dating.length + ' entr' + (r.dating.length === 1 ? 'y' : 'ies') + ')'));
        var ul = T.h('ul', { class: 'hint' });
        r.dating.slice(0, 40).forEach(function (x) { ul.appendChild(T.h('li', null, x)); });
        if (r.dating.length > 40) { ul.appendChild(T.h('li', null, 'and ' + (r.dating.length - 40) + ' more')); }
        d.appendChild(ul);
      }
      host.appendChild(d);
    }

    /* ---- bridge ---- */
    function bridgeTab(body, v) {
      var b = v.bridge;
      if (!b) { body.appendChild(T.h('div', { class: 'empty' }, 'no bridge to the workbook in this run\'s report')); return; }
      var rows = [{ what: 'the workbook (reconstructed on assumed prices)', amount: b.workbook, source: '', source_short: '', total: true }]
        .concat((b.lines || []).map(function (x) { return { what: x.what, amount: x.amount, source: x.source, source_short: x.source_short || x.source, step: true }; }))
        .concat([{ what: 'not explained by the estimates (' + (b.unexplained_what || '') + ')', amount: b.unexplained, source: 'this run', source_short: 'this run', step: true },
                 { what: 'the restated NAV', amount: b.restated, source: '', source_short: '', total: true },
                 { what: 'difference to the workbook', amount: b.difference, source: 'never forced', source_short: 'never forced', step: true }]);
      var host = T.h('div'); body.appendChild(host);
      grid(host, { columns: [txt('what', b.date ? 'Bridge of ' + b.date : 'Bridge'),
                             { key: 'amount', label: 'Amount', cls: 'num', html: function (r) { return '<span class="' + (r.step ? cls(r, 'amount') : 'num') + '">' + T.esc(r.step ? signed(r, 'amount') : money(r, 'amount')) + '</span>'; },
                               csv: function (r) { return plain(r, 'amount'); } },
                             txt('source_short', 'Source', { titleKey: 'source' })],
                   rows: rows, csv: 'import-' + v.batch_id + '-bridge.csv', empty: 'no rows', rowKey: function (r) { return r.what; } });
      if (b.note) { body.appendChild(T.h('div', { class: 'hint' }, b.note)); }
    }

    /* ---- data ---- */
    function checkNow(btn) {
      if (!st.id) { return; }
      btn.disabled = true;
      T.api.post('/api/import/report', { batch_id: st.id }).then(function (rep) {
        if (dead) { return; }
        var by = {}; var counts = {};
        (rep.lines || []).forEach(function (l) { by[l.line_no] = { status_short: l.status_short || l.status, status_text: l.status_text || l.status, differences: l.differences || [] };
                                                 counts[l.status_short || l.status] = (counts[l.status_short || l.status] || 0) + 1; });
        st.now = { byLine: by, at: rep.checked_et || 'just now', counts: Object.keys(counts).sort().map(function (k) { return counts[k] + ' ' + k; }).join(', ') };
        btn.disabled = false; draw();
      }, function (e) { if (!dead) { btn.disabled = false; T.api.fail(e, 'import check'); } });
    }
    function load() {
      if (!st.id) { draw(); return; }
      var id = st.id;
      draw();
      T.api.get('/api/import/batches/' + encodeURIComponent(id)).then(function (r) {
        if (dead || id !== st.id) { return; }
        st.view = r.view || null;
        if (!st.view) { T.clear(root); root.appendChild(T.h('div', { class: 'error' }, 'the batch ' + id + ' has no view in this build')); return; }
        draw();
      }, function (e) { if (!dead) { T.clear(root); T.errorBox(root, e, 'import batch'); } });
    }
    function loadBatches() {
      if (!T.hasModule('importer')) { draw(); return; }
      T.api.get('/api/import/batches').then(function (r) {
        if (dead) { return; }
        st.batches = r.batches || [];
        draw();
        if (st.id) { load(); }
      }, function (e) { if (!dead) { T.clear(root); T.errorBox(root, e, 'import batches'); } });
    }
    loadBatches();
    return { unmount: function () { dead = true; drop(); } };
  }

  T.components.imports = { render: render };
})();
