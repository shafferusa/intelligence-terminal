/* Settings -> Lifetimes: the Reset lifetime dialog (typed "RESET", starting capital) and the past-lifetimes table with
 * History links (TERMINAL_DESIGN.md s13.10, s17.1, s17.5; WP-23).
 *
 * Mounted by pages/settings.js (WP-12):
 *
 *     var h = T.components.lifetimes.mount(el, {book: 'P', book_name: 'Personal Book'});   // fetches at once
 *     h.refresh();                                                                        // re-reads the server
 *     h.unmount();
 *
 * Reads  GET  /api/books                      -> the sidebar rows (the current lifetime, NAV, eligibility, past_lifetimes)
 *        GET  /api/books/{code}/lifetimes     -> {book_code, lifetimes: [...]} newest first with the stored final figures
 *        GET  /api/lifetimes/{lifetime}       -> the current lifetime's read model: the agreements signed with each dealer
 *                                                ("ISDA Master / CSA / PBA with Goldman Sachs, signed 2026-10-21", PO-16)
 * Writes POST /api/books/{code}/reset         {confirm: "RESET", starting_capital: "1000000.00", name?}
 *                                            -> {closed: LifetimeView, opened: {lifetime_id, accounts, ...}, integrity_ok, ...}
 *
 * Conventions shown on the dialog
 * - The confirmation is the typed word RESET, upper case, exactly (the server refuses anything else with 400 CONFIRM);
 *   the Reset button stays disabled until it matches and the starting capital is a positive USD amount.
 * - Nothing is deleted: the lifetime is closed read-only with its final NAV / P&L / integrity verdict and stays under
 *   History; the new lifetime starts with the typed capital, the catalogue accounts, no prime brokers, sweeps on.
 * - Money travels as decimal strings; the typed capital is validated as text (no floating-point money).
 * - One request_id per click (T.api); X-Replayed answers are shown as "already reset" and never repeated.
 * - A failed request renders its error inside the dialog and keeps the typed values.
 * - No dependencies beyond core.js (T.h, T.api, T.ui, T.fmt, T.money, T.esc, T.convNote); the file is ASCII.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};

  var MONEY_RE = /^\d{1,15}(\.\d{0,2})?$/;

  function esc(s) { return T.esc ? T.esc(s) : String(s == null ? '' : s); }
  function fmtDate(row, key) {
    if (row[key]) { return row[key]; }
    var ts = row[key + '_ts'];
    return ts && T.fmt && T.fmt.date ? T.fmt.date(ts) : (ts ? String(ts) : '');
  }
  /* "1,000,000" -> "1000000.00"; null when not a positive USD amount with at most 2 decimals */
  function normaliseMoney(raw) {
    var s = String(raw == null ? '' : raw).trim().replace(/,/g, '').replace(/^\$/, '');
    if (!MONEY_RE.test(s)) { return null; }
    var p = s.split('.');
    var frac = ((p[1] || '') + '00').slice(0, 2);
    var whole = p[0].replace(/^0+(?=\d)/, '');
    if (/^0*$/.test(whole) && /^0*$/.test(frac)) { return null; }
    return whole + '.' + frac;
  }
  function defaultCapital(code, bookRow) {
    var s = T.state && T.state.settings;
    var bd = s && s.book_defaults && s.book_defaults[code];
    if (bd && bd.starting_capital) { return String(bd.starting_capital); }
    if (bd && typeof bd.starting_capital_c === 'number') { return T.fmt && T.fmt.cents ? T.fmt.cents(bd.starting_capital_c).replace(/,/g, '') : String(bd.starting_capital_c / 100); }
    if (bookRow && bookRow.starting_capital) { return String(bookRow.starting_capital); }
    if (bookRow && typeof bookRow.starting_capital_c === 'number') { return String(bookRow.starting_capital_c / 100); }
    if (bookRow && bookRow.lifetime && bookRow.lifetime.starting_capital) { return String(bookRow.lifetime.starting_capital); }
    return '';
  }
  function historyLinks(lid) {
    var id = encodeURIComponent(lid);
    return [['#/h/' + id + '/portfolio', 'Portfolio'], ['#/h/' + id + '/accounting', 'Accounting'], ['#/h/' + id + '/activity', 'Activity'],
            ['/api/history/' + id + '/export/transactions.csv', 'CSV']];
  }
  function linksHtml(lid) {
    return historyLinks(lid).map(function (l) { return '<a href="' + esc(l[0]) + '"' + (l[0].charAt(0) === '/' ? ' target="_blank" rel="noopener"' : '') + '>' + esc(l[1]) + '</a>'; }).join(' ');
  }

  /* PO-16: "ISDA Master / CSA / PBA with Goldman Sachs, signed 2026-10-21" per dealer; "none" without a relationship */
  function agreementsText(rows) {
    var by = {}, order = [];
    (rows || []).forEach(function (r) {
      if (!by[r.broker]) { by[r.broker] = { name: r.broker_name || r.broker, kinds: [], signed: r.signed_date }; order.push(r.broker); }
      by[r.broker].kinds.push(r.label || (r.kind === 'ISDA_MASTER' ? 'ISDA Master' : r.kind));
    });
    if (!order.length) { return 'none (no prime-broker relationship in this lifetime)'; }
    return order.map(function (k) { var g = by[k]; return g.kinds.join(' / ') + ' with ' + g.name + ', signed ' + (g.signed || ''); }).join('; ');
  }

  var COLUMNS = [
    { key: 'lifetime_id', label: 'lifetime', cls: 'l' },
    { key: 'name', label: 'name', cls: 'l', text: function (x) { return x.name || ''; } },
    { key: 'status', label: 'status', cls: 'l', text: function (x) { return x.status === 'OPEN' ? 'OPEN (current)' : (x.status || ''); } },
    { key: 'started', label: 'started', cls: 'l', text: function (x) { return fmtDate(x, 'started'); } },
    { key: 'ended', label: 'ended', cls: 'l', text: function (x) { return fmtDate(x, 'ended'); } },
    { key: 'starting_capital', label: 'starting capital', text: function (x) { return T.money(x, 'starting_capital'); } },
    { key: 'final_nav', label: 'final NAV', text: function (x) { return x.status === 'OPEN' ? '' : T.money(x, 'final_nav'); } },
    { key: 'final_pnl', label: 'P&L', html: function (x) { return x.status === 'OPEN' ? '' : '<span class="' + T.sign(x, 'final_pnl') + '">' + esc(T.money(x, 'final_pnl')) + '</span>'; },
      text: function (x) { return x.status === 'OPEN' ? '' : T.money(x, 'final_pnl'); } },
    { key: 'reason', label: 'reason', cls: 'l', text: function (x) { return x.reason || ''; } },
    { key: 'integrity_ok', label: 'integrity', cls: 'l', html: function (x) { return x.integrity_ok == null ? '' : (x.integrity_ok ? '<span class="pos">ok</span>' : '<span class="neg">FAILED</span>'); },
      text: function (x) { return x.integrity_ok == null ? '' : (x.integrity_ok ? 'ok' : 'FAILED'); } },
    { key: 'history', label: 'history', cls: 'l', html: function (x) { return x.status === 'CLOSED' ? linksHtml(x.lifetime_id) : ''; }, text: function () { return ''; } }
  ];

  function plainTable(rows) {
    var table = T.h('table', { class: 'grid lifetimes-table' });
    var thead = T.h('tr');
    COLUMNS.forEach(function (c) { thead.appendChild(T.h('th', { class: c.cls || '' }, c.label)); });
    table.appendChild(T.h('thead', null, thead));
    var tbody = T.h('tbody');
    rows.forEach(function (x) {
      var tr = T.h('tr');
      COLUMNS.forEach(function (c) {
        var td = T.h('td', { class: c.cls || '' });
        if (c.html) { td.innerHTML = c.html(x); } else { td.textContent = c.text ? c.text(x) : (x[c.key] == null ? '' : String(x[c.key])); }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function mount(el, opts) {
    opts = opts || {};
    var code = String(opts.book || (T.book && T.book.code) || '').toUpperCase();
    var state = { book: null, rows: null, view: null, last: null, error: null, dead: false };
    var root = T.h('div', { class: 'lifetimes' });
    T.clear(el).appendChild(root);

    function current() {
      var box = T.h('div', { class: 'section' });
      if (!code) { box.appendChild(T.h('div', { class: 'error' }, 'lifetimes: no book code given')); return box; }
      var b = state.book;
      var lt = b && b.lifetime;
      var title = (b && b.name) || opts.book_name || code;
      box.appendChild(T.h('h2', null, title + ' . current lifetime', T.h('small', null, lt ? lt.lifetime_id : 'no open lifetime')));
      if (lt) {
        box.appendChild(T.kv([
          ['lifetime', lt.lifetime_id + (lt.name ? ' . ' + lt.name : '')],
          ['started', fmtDate(lt, 'started')],
          ['starting capital', T.money(lt, 'starting_capital')],
          ['NAV now', b.nav ? (T.money(b, 'nav') + (b.nav.quality ? ' (' + b.nav.quality + (b.nav.partial ? ', partial' : '') + ')' : '')) : 'n/a'],
          ['past lifetimes', String(b.past_lifetimes == null ? 0 : b.past_lifetimes)],
          ['agreements', agreementsText((state.view && state.view.agreements) || [])]
        ]));
      }
      var btn = T.h('button', { class: 'btn danger', onclick: openDialog, disabled: !lt }, 'Reset lifetime...');
      box.appendChild(T.h('div', { class: 'row' }, btn, T.h('small', { class: 'muted', style: 'margin-left:8px' }, 'closes ' + (lt ? lt.lifetime_id : 'the lifetime') + ' read-only and opens ' + (lt ? code + (Number(lt.seq || 0) + 1) : 'the next one') + ' with the capital you type')));
      if (state.last) {
        var c = state.last.closed || {}, o = state.last.opened || {};
        box.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'last reset', T.h('small', null, state.last.replayed ? 'replayed by the server (already reset)' : 'done')),
          T.kv([
            ['closed', (c.lifetime_id || '') + ' . final NAV ' + T.money(c, 'final_nav') + ' . P&L ' + T.money(c, 'final_pnl') + ' . integrity ' + (state.last.integrity_ok === false ? 'FAILED (recorded, see Attention)' : 'ok')],
            ['orders cancelled', String((state.last.cancelled_orders || []).length)],
            ['opened', (o.lifetime_id || '') + ' with ' + T.money(o, 'starting_capital') + ' . accounts ' + (o.accounts || []).join(', ')],
            ['history', T.h('span', { html: c.lifetime_id ? linksHtml(c.lifetime_id) : '' })]
          ])));
      }
      return box;
    }

    function openDialog() {
      var b = state.book, lt = b && b.lifetime;
      if (!lt) { T.ui.toast('no open lifetime to reset', 'err'); return; }
      var cap = T.h('input', { type: 'text', value: defaultCapital(code, b), style: 'width:180px', spellcheck: 'false', 'data-field': 'starting_capital' });
      var name = T.h('input', { type: 'text', value: '', placeholder: (b.name || code) + ' #' + (Number(lt.seq || 0) + 1), style: 'width:260px', spellcheck: 'false', 'data-field': 'name' });
      var conf = T.h('input', { type: 'text', value: '', placeholder: 'type RESET', style: 'width:180px', autocomplete: 'off', spellcheck: 'false', 'data-field': 'confirm' });
      var err = T.h('div', { class: 'error', hidden: true });
      var body = T.h('div', null,
        T.h('div', { class: 'hint' }, 'Closing ' + lt.lifetime_id + ' (' + (b.name || code) + '). Every open order is cancelled "lifetime reset", the final NAV, realised P&L and integrity verdict are stored, every account is closed read-only, and ' + code + (Number(lt.seq || 0) + 1) + ' opens with the capital below (catalogue accounts, no prime brokers, sweeps on). Nothing is deleted: the closed lifetime stays under History.'),
        T.form([
          T.field('starting capital (USD) for ' + code + (Number(lt.seq || 0) + 1), cap, 'posted once as initial capital; the default comes from Settings > Books'),
          T.field('name (optional)', name, 'the new lifetime\'s display name'),
          T.field('confirmation', T.h('span', null, conf, T.h('small', { class: 'muted', style: 'margin-left:8px' }, 'type RESET in upper case')), 'the server refuses anything but RESET')
        ]),
        err,
        T.convNote ? T.convNote(['LIFETIME']) : null);
      var handle = T.ui.modal({
        title: 'Reset lifetime ' + lt.lifetime_id, body: body, sticky: true,
        actions: [
          { label: 'Cancel' },
          { id: 'lifetimes-reset-go', label: 'Reset lifetime', cls: 'danger', onClick: function (m, btn) { submit(m, btn); } }
        ]
      });
      var go = handle.el.querySelector('#lifetimes-reset-go');
      function check() {
        var ok = conf.value === 'RESET' && normaliseMoney(cap.value) != null;
        if (go) { go.disabled = !ok; }
      }
      conf.addEventListener('input', check); cap.addEventListener('input', check); check();
      function submit(m, btn) {
        var amount = normaliseMoney(cap.value);
        if (conf.value !== 'RESET') { err.hidden = false; err.textContent = 'type RESET (upper case) to confirm'; return; }
        if (amount == null) { err.hidden = false; err.textContent = 'starting capital must be a positive USD amount with at most 2 decimals'; return; }
        btn.disabled = true; err.hidden = true;
        var payload = { confirm: 'RESET', starting_capital: amount };
        if (name.value.trim()) { payload.name = name.value.trim(); }
        T.api.post('/api/books/' + encodeURIComponent(code) + '/reset', payload, { nobook: true }).then(function (r) {
          m.close();
          if (state.dead) { return; }
          state.last = r || {};
          T.ui.toast(r && r.replayed ? 'already reset (replayed)' : ('lifetime ' + ((r && r.closed && r.closed.lifetime_id) || lt.lifetime_id) + ' closed; ' + ((r && r.opened && r.opened.lifetime_id) || 'the next lifetime') + ' opened'), 'ok', 5000);
          if (r && r.integrity_ok === false) { T.ui.toast('the final integrity check of ' + lt.lifetime_id + ' FAILED: recorded on the lifetime and in Attention (nothing deleted)', 'err', 8000); }
          if (T.poll && T.poll.kickAll) { T.poll.kickAll(); }
          if (T.state) { T.state.active_account = null; T.state.account_detail = null; }
          if (T.store) { T.store.set('active_account', null); }      /* the closed lifetime's account is never remembered (P01) */
          load();
          if (T.router && T.router.rerender) { T.router.rerender(); }
        }, function (e) {
          btn.disabled = false;
          err.hidden = false;
          err.textContent = T.api.describe(e);                 /* V104R2-BW-04: words (with the incident id of a 500), the code in the hover */
          err.title = (e && e.code) || '';
          console.error('reset lifetime', e);
        });
      }
    }

    function past() {
      var bname = (state.book && state.book.name) || opts.book_name || code;
      var box = T.h('div', { class: 'section' }, T.h('h2', null, 'lifetimes of ' + bname, T.h('small', null, 'newest first; History opens the archived, read-only pages')));
      if (state.error) { box.appendChild(T.h('div', { class: 'error' }, state.error)); box.appendChild(T.h('button', { class: 'btn small', onclick: load }, 'Retry')); return box; }
      var rows = state.rows;
      if (!rows) { box.appendChild(T.h('div', { class: 'empty' }, 'loading lifetimes ...')); return box; }
      if (!rows.length) { box.appendChild(T.h('div', { class: 'empty' }, 'no lifetimes listed')); return box; }
      var g = T.h('div');
      if (T.components.grid && typeof T.components.grid.mount === 'function') {
        try { T.components.grid.mount(g, { tools: false, rows: rows, empty: 'no lifetimes listed', columns: COLUMNS }); } catch (e) { T.clear(g).appendChild(plainTable(rows)); }
      } else {
        g.appendChild(plainTable(rows));
      }
      box.appendChild(g);
      return box;
    }

    function render() {
      T.clear(root);
      root.appendChild(current());
      root.appendChild(past());
      if (T.convNote) { root.appendChild(T.convNote(['LIFETIME', 'BOOKS'])); }
    }

    function load() {
      if (!code) { render(); return Promise.resolve(); }
      state.error = null;
      var a = T.api.get('/api/books', null, { nobook: true }).then(function (r) {
        var list = Array.isArray(r) ? r : (r && r.books) || [];
        state.book = null;
        list.forEach(function (b) { if (b && b.book_code === code) { state.book = b; } });
        var lid = state.book && state.book.lifetime && state.book.lifetime.lifetime_id;
        return lid ? T.api.get('/api/lifetimes/' + encodeURIComponent(lid), null, { nobook: true }).then(function (v) { state.view = v; }, function () { state.view = null; }) : null;
      }, function (e) { state.error = T.api.fail(e, 'books'); });
      var b = T.api.get('/api/books/' + encodeURIComponent(code) + '/lifetimes', null, { nobook: true }).then(function (r) {
        state.rows = Array.isArray(r) ? r : (r && r.lifetimes) || [];
      }, function (e) { state.error = T.api.fail(e, 'lifetimes ' + code); });
      return Promise.all([a, b]).then(function () { if (!state.dead) { render(); } });
    }

    render();
    load();
    return {
      el: root,
      refresh: function () { return load(); },
      unmount: function () { state.dead = true; T.clear(root); },
      normaliseMoney: normaliseMoney
    };
  }

  T.components.lifetimes = { mount: mount, COLUMNS: COLUMNS, normaliseMoney: normaliseMoney, historyLinks: historyLinks };
})();
