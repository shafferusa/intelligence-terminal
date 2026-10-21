/* Account Markets tab (TERMINAL_DESIGN.md s17.5 #/a/{account}/markets[/{tab}]; WP-12).  Product tabs from
 * GET /api/accounts/{id}/markets?tab= (US_EXCHANGE: equities / options underlyings / futures by group; CREDIT: loans,
 * borrows, rates + the sections Cash loans . Securities lending . Private credit (mounts components/privatecredit.js) .
 * Repo (NOT IN THIS BUILD); FOREIGN_FX: FX spot pairs (+ listings NOT IN THIS BUILD); DEBT: the par curve and bonds
 * LIMITED "model price, not executable in this build"; PB: as US_EXCHANGE + the OTC tab that mounts
 * components/swapticket.js in an eligible lifetime or shows the ELIGIBILITY sentence; PRIVATE: pages/private_markets.js
 * (WP-19) or NOT YET BUILT).  Dense grid: symbol, name, last (freshness), chg %, bid / ask when known, volume, position;
 * typeahead "add symbol" = GET /api/instruments/resolve (202 spinner); row Enter -> the instrument workspace; b / s ->
 * the ticket.  Every price is T.pxHtml(quote): nothing shows a bare number.  Unsupported rows (support NOT_IN_THIS_BUILD)
 * are grey and cannot be ticketed.
 *
 * Refreshes never rebuild what the viewer is using (UI-ops fix 2026-10-21: the 30 s poll used to rebuild the whole
 * page - the filter box came back empty with all rows, and a click on a row's b button could be swallowed):
 * - the 30 s page poll and every quote tick update the grid IN PLACE (components/grid.js keeps the filter text, sort,
 *   selection, scroll and focus, and holds a render back while a pointer is pressed, so no click is dropped or delayed);
 * - the page skeleton (tabs, tools, grid, sections, the private-credit and swap-ticket mounts) is rebuilt only when the
 *   SHAPE of the answer changes (tab, tab state, account kind, sections, groups, curve, OTC eligibility); the rebuild
 *   waits for a pressed pointer to be released and carries over the grid's state, the add-symbol box (text, focus,
 *   caret) and the page scroll;
 * - a refresh that fails keeps the rows on screen (each price keeps its own freshness chip) and says so in one line;
 *   the first load that fails shows the error and the poll keeps retrying;
 * - quotes never go backwards: a row keeps whichever quote is newer (provider time, then fetch time) between the page
 *   answer and the quotes poller.
 */
(function () {
  'use strict';
  var T = window.T;
  function roleOf(id) { var i = String(id).indexOf(':'); return i >= 0 ? id.slice(i + 1) : id; }
  function chg(row) {
    var q = row.quote || {};
    if (q.chg_pct != null) { return q.chg_pct; }
    if (row.chg_pct != null) { return row.chg_pct; }
    var last = Number(q.last != null ? q.last : q.mid), pc = Number(q.prev_close);
    if (isFinite(last) && isFinite(pc) && pc) { return ((last / pc - 1) * 100).toFixed(2); }
    return null;
  }
  function columns(acct) {
    return [
      { key: 'symbol', label: 'symbol', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b>' + (r.support && r.support !== 'FULL' ? ' <span class="state-tag ' + T.esc(r.support) + '">' + T.esc(String(r.support).replace(/_/g, ' ')) + '</span>' : ''); }, text: function (r) { return r.symbol || r.instrument_id || ''; } },
      { key: 'name', label: 'name', cls: 'l', text: function (r) { return r.name || ''; } },
      { key: 'last', label: 'last', html: function (r) { return r.support === 'NOT_IN_THIS_BUILD' ? '<span class="nib">NOT IN THIS BUILD</span>' : T.pxHtml(r.quote || r.mark, { basis: r.price_basis }); }, text: function (r) { var q = r.quote || r.mark || {}; return q.last != null ? q.last : (q.mark != null ? q.mark : (q.mid != null ? q.mid : '')); } },
      { key: 'chg', label: 'chg %', html: function (r) { var c = chg(r); return c == null ? '' : '<span class="' + T.signCls(c) + '" title="vs previous close (display derivation from two observed prices)">' + T.esc(T.fmt.signed(T.fmt.num(c, 2))) + ' %</span>'; }, text: function (r) { var c = chg(r); return c == null ? '' : String(c); } },
      { key: 'bid', label: 'bid', html: function (r) { var q = r.quote || {}; return q.bid == null ? '' : T.pxHtml(q, { field: 'bid' }); }, text: function (r) { var q = r.quote || {}; return q.bid == null ? '' : String(q.bid); } },
      { key: 'ask', label: 'ask', html: function (r) { var q = r.quote || {}; return q.ask == null ? '' : T.pxHtml(q, { field: 'ask' }); }, text: function (r) { var q = r.quote || {}; return q.ask == null ? '' : String(q.ask); } },
      { key: 'volume', label: 'volume', text: function (r) { var q = r.quote || {}; return q.day_volume == null ? '' : T.fmt.qty(q.day_volume); }, sort: function (r) { var q = r.quote || {}; return q.day_volume == null ? '' : q.day_volume; } },
      { key: 'position', label: 'position', html: function (r) { var p = r.position_qty != null ? r.position_qty : (r.position && r.position.qty); return p == null || p === 0 ? '' : '<span class="' + (p > 0 ? 'pos' : 'neg') + '">' + T.esc(T.fmt.qty(p)) + '</span>'; }, text: function (r) { var p = r.position_qty != null ? r.position_qty : (r.position && r.position.qty); return p == null ? '' : String(p); } },
      { key: 'ccy', label: 'ccy', cls: 'l', text: function (r) { return r.currency || ''; } },
      { key: 'act', label: '', cls: 'l', html: function (r) {
        if (r.support === 'NOT_IN_THIS_BUILD' || r.support === 'LIMITED') { return ''; }
        return '<button class="btn small act" data-act="b" title="BUY (b)">b</button><button class="btn small act" data-act="s" title="SELL (s)">s</button>';
      }, text: function () { return ''; } }
    ].filter(function (c) { return !(acct.hideCcy && c.key === 'ccy'); });
  }
  /* provider time, then fetch time (epoch seconds; -1 when absent) */
  function qtime(q) { var a = q ? Number(q.quote_time) : NaN, b = q ? Number(q.fetched_at) : NaN; return [isFinite(a) ? a : -1, isFinite(b) ? b : -1]; }
  /* true when quote a is at least as new as quote b */
  function newer(a, b) { if (!a) { return false; } if (!b) { return true; } var ta = qtime(a), tb = qtime(b); return ta[0] !== tb[0] ? ta[0] > tb[0] : ta[1] >= tb[1]; }
  function mergeQuote(row, q) { if (q && newer(q, row.quote)) { row.quote = Object.assign({}, row.quote || {}, q); return true; } return false; }
  function render(el, route) {
    var acct = route.account;
    var role = roleOf(acct);
    var data = null; var grid = null; var tab = route.tab; var dead = false;
    var ui = null;                                             /* the painted skeleton; null until the first answer */
    T.pageHead(el, 'Markets', acct);
    var tabBox = T.h('div'); var body = T.h('div'); el.appendChild(tabBox); el.appendChild(body);
    if (role === 'PRIVATE') {
      if (T.pages.private_markets && T.pages.private_markets.render) { T.pages.private_markets.render(body, route); return; }
      T.notBuilt(body, 'pages/private_markets.js (WP-19): the Private Markets account page (companies grid, reported rounds, negotiated secondaries)');
      body.appendChild(T.convNote(['QP', 'PRIVATE_MARK', 'PRIVATE_TRADE']));
      return;
    }
    function load() {
      return T.api.get('/api/accounts/' + encodeURIComponent(acct) + '/markets', { tab: tab }).then(function (d) {
        if (dead) { return; }
        var known = T.state.quotes || {};
        (d.rows || []).forEach(function (r) { mergeQuote(r, known[r.instrument_id]); });
        data = d; if (!tab) { tab = d.tab || (d.tabs && d.tabs[0] && d.tabs[0].key) || null; }
        if (ui) { ui.err.hidden = true; ui.err.textContent = ''; }
        show();
      }, function (e) {
        if (dead) { return; }
        if (!ui) { T.clear(body); T.errorBox(body, e, 'markets'); return; }
        console.error('markets refresh', e);
        ui.err.textContent = 'refresh failed: ' + (e && e.code ? e.code + ' ' : '') + (e && e.message || e) + ' - the rows below keep their last prices, each with its own freshness';
        ui.err.hidden = false;
      });
    }
    /* what decides the page layout; anything else in the answer is updated in place */
    function shape(d) {
      var cur = (d.tabs || []).filter(function (t) { return t.key === tab; })[0] || {};
      var e = tab === 'otc' ? (((T.book.current() || {}).eligibility) || {}) : {};
      return JSON.stringify([tab, cur.state || null, d.kind || null, !!((d.rows || []).length || !(d.sections || []).length), !!d.groups, d.empty || null,
        (d.sections || []).map(function (s) { return [s.key, s.label || null, s.state || null, !!(s.rows && s.rows.length), s.columns || null]; }),
        !!(role === 'DEBT' || d.curve), e.pb_otc === false]);
    }
    function show() {
      if (dead || !data) { return; }
      if (ui && ui.shape === shape(data)) { refresh(); return; }
      if (T.components.grid.busy && T.components.grid.busy()) { setTimeout(show, 50); return; }   /* rebuild after the click */
      paintAll();
    }
    function keep() {
      var a = document.activeElement; var main = T.q('#main'); var box = ui.box;
      var k = { grid: grid ? grid.state() : null, box: null, mainTop: main ? main.scrollTop : null };
      if (box) { k.box = { value: box.value, focus: a === box, caret: null }; if (a === box) { try { k.box.caret = [box.selectionStart, box.selectionEnd]; } catch (e) { k.box.caret = null; } } }
      return k;
    }
    function carryOver(k) {
      if (!k) { return; }
      if (ui.box && k.box) {
        ui.box.value = k.box.value;
        if (k.box.focus) { try { ui.box.focus({ preventScroll: true }); if (k.box.caret) { ui.box.setSelectionRange(k.box.caret[0], k.box.caret[1]); } } catch (e) { /* focus is a convenience */ } }
      }
      var main = T.q('#main'); if (main && k.mainTop != null) { main.scrollTop = k.mainTop; }
    }
    function watch() {
      var ids = (data.rows || []).map(function (r) { return r.instrument_id; }).filter(Boolean);
      var sig = ids.join(',');
      if (sig !== ui.ids) { ui.ids = sig; T.watchQuotes(ids); }
    }
    function paintTabs() {
      var tabs = (data.tabs || []).map(function (t) { return { key: t.key, label: t.label || t.key, nib: t.state === 'NOT_IN_THIS_BUILD', title: t.note || '' }; });
      var sig = JSON.stringify([tab, tabs]);
      if (ui.tabs === sig) { return; }
      ui.tabs = sig;
      T.clear(tabBox);
      if (tabs.length) { tabBox.appendChild(T.tabs(tabs, tab, function (k) { tab = k; T.router.go(T.router.account(acct, 'markets/' + k)); })); }
    }
    function paintAll() {
      var carry = ui ? keep() : null;
      T.clear(tabBox); T.clear(body);
      ui = { shape: shape(data), tabs: null, ids: null, box: null, note: null, curve: null, curveSig: null, sections: [],
             err: T.h('div', { class: 'error', role: 'status', hidden: true }) };
      body.appendChild(ui.err);
      grid = null;
      paintTabs();
      var cur = (data.tabs || []).filter(function (t) { return t.key === tab; })[0] || {};
      if (cur.state === 'NOT_IN_THIS_BUILD') { body.appendChild(T.nib(cur.label || tab, cur.note)); carryOver(carry); return; }
      var tools = T.h('div', { class: 'row', style: 'margin-bottom:8px' });
      var box = ui.box = T.h('input', { type: 'text', placeholder: 'add symbol (Enter resolves on Yahoo)', 'data-search': '1', 'aria-label': 'add symbol', style: 'width:260px' });
      var spin = T.h('span', { class: 'spin', hidden: true });
      box.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') { return; }
        var sym = box.value.trim().toUpperCase(); if (!sym) { return; }
        spin.hidden = false;
        T.api.getReady('/api/instruments/resolve', { symbol: sym }).then(function (inst) {
          spin.hidden = true; var id = inst.instrument_id || (inst.instrument && inst.instrument.instrument_id);
          if (!id) { T.ui.toast('resolved, but no instrument id in the answer', 'err'); return; }
          T.router.go(T.router.instrument(acct, id));
        }, function (e) { spin.hidden = true; T.api.fail(e, 'resolve ' + sym); });
      });
      ui.note = T.h('span', { class: 'hint', hidden: !data.note }, data.note || '');
      tools.appendChild(box); tools.appendChild(spin); tools.appendChild(ui.note);
      body.appendChild(tools);
      if (role === 'DEBT' || data.curve) { ui.curve = curvePanel(data.curve); ui.curveSig = JSON.stringify(data.curve || null); body.appendChild(ui.curve); }
      var rows = data.rows || [];
      if (rows.length || !(data.sections || []).length) {
        var gEl = T.h('div'); body.appendChild(gEl);
        grid = T.components.grid.mount(gEl, {
          columns: columns({ hideCcy: role === 'US_EXCHANGE' }), rows: rows, focus: true, csv: 'markets-' + role + '-' + (tab || 'all') + '.csv', empty: data.empty || 'no instruments on this tab',
          rowKey: function (r) { return r.instrument_id; },
          groupBy: data.groups ? function (r) { return r.group || ''; } : null,
          rowClass: function (r) { return r.support === 'NOT_IN_THIS_BUILD' ? 'nib' : ''; },
          onEnter: function (r) { if (r.support !== 'NOT_IN_THIS_BUILD') { T.router.go(T.router.instrument(acct, r.instrument_id)); } },
          onKey: function (k, r) { if ((k === 'b' || k === 's') && r.support === 'FULL') { T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, side: k === 'b' ? 'BUY' : 'SELL' }); return true; } return false; },
          onClick: function (r, e) { var b = e.target.closest('button[data-act]'); if (b) { T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, side: b.dataset.act === 'b' ? 'BUY' : 'SELL' }); } },
          restore: carry && carry.grid
        });
        watch();
      }
      (data.sections || []).forEach(function (s) { var sec = section(s); ui.sections.push(sec); body.appendChild(sec.el); });
      if (role === 'FOREIGN_FX') { body.appendChild(T.nib('foreign listings and foreign bonds', 'resolve refuses non-US exchanges in this build; FX spot deals from the Cash & financing page')); body.appendChild(T.convNote(['FX_SPOT'])); }
      if (role === 'CREDIT') { body.appendChild(creditSections()); }
      if (tab === 'otc' && (role.indexOf('PB_') === 0 || data.kind === 'PB')) { body.appendChild(otcTab()); }
      if (role === 'DEBT') { body.appendChild(T.h('div', { class: 'hint' }, 'Debt Instruments are LIMITED in this build: model prices from the par curve (and FRED OAS for corporates), not executable.')); }
      if (role === 'US_EXCHANGE' || data.kind === 'PB') { body.appendChild(T.convNote(['FILL_PRICE', 'PARTICIPATION', 'SESSION', 'STALE'])); }
      carryOver(carry);
    }
    /* same shape: rows, prices, note, curve and section rows change in place; nothing the viewer types into is rebuilt */
    function refresh() {
      paintTabs();
      if (ui.note) { ui.note.textContent = data.note || ''; ui.note.hidden = !data.note; }
      if (ui.curve) {
        var cs = JSON.stringify(data.curve || null);
        if (cs !== ui.curveSig) { var nc = curvePanel(data.curve); ui.curve.parentNode.replaceChild(nc, ui.curve); ui.curve = nc; ui.curveSig = cs; }
      }
      if (grid) { grid.update(data.rows || []); watch(); }
      (data.sections || []).forEach(function (s, k) { var sec = ui.sections[k]; if (sec) { sec.update(s); } });
    }
    function section(s) {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, s.label || s.key));
      var out = { el: p, update: function () {} };
      if (s.state === 'NOT_IN_THIS_BUILD') { p.appendChild(T.nib(s.label || s.key, s.note)); return out; }
      var g = null; var empty = null;
      if (s.rows && s.rows.length) {
        var cols = (s.columns || Object.keys(s.rows[0])).map(function (c) { var key = typeof c === 'string' ? c : c.key; return { key: key, label: typeof c === 'string' ? c.replace(/_/g, ' ') : (c.label || key), cls: /id|name|ccy|currency|index|status|kind|lender|tier/.test(key) ? 'l' : 'num', text: function (r) { var v = r[key]; return v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v)); } }; });
        var gEl = T.h('div'); p.appendChild(gEl);
        g = T.components.grid.mount(gEl, { columns: cols, rows: s.rows, tools: false });
      } else { empty = T.h('div', { class: 'empty' }, s.empty || 'nothing here'); p.appendChild(empty); }
      var note = T.h('div', { class: 'hint', hidden: !s.note }, s.note || ''); p.appendChild(note);
      out.update = function (s2) {
        if (g && s2.rows && s2.rows.length) { g.update(s2.rows); }
        if (empty) { empty.textContent = s2.empty || 'nothing here'; }
        note.textContent = s2.note || ''; note.hidden = !s2.note;
      };
      return out;
    }
    function creditSections() {
      var box = T.h('div');
      var pc = T.h('div', { class: 'panel' }, T.h('h2', null, 'Private credit'));
      var mount = T.h('div'); pc.appendChild(mount);
      T.mountOptional('privatecredit', mount, { account_id: acct, book: T.book.code }, 'components/privatecredit.js (WP-21): private-credit commit form and loans table');
      pc.appendChild(T.convNote(['PRIVATE_CREDIT']));
      box.appendChild(pc);
      box.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'Repo'), T.nib('repo / reverse repo', 'GMRA-documented financing is a Build 2 product')));
      box.appendChild(T.convNote(['SHORT_COLLATERAL', 'BORROW_AVAIL', 'ACCRUAL', 'RATE_LAG']));
      return box;
    }
    function otcTab() {
      var box = T.h('div', { class: 'panel' }, T.h('h2', null, 'OTC - swaps with this prime broker'));
      var book = T.book.current(); var e = (book && book.eligibility) || {};
      var mount = T.h('div'); box.appendChild(mount);
      if (e.pb_otc === false) { mount.appendChild(T.h('div', { class: 'conv' }, e.pb_otc_reason || 'not eligible', ' - ', T.conv('ELIGIBILITY'))); }
      else { T.mountOptional('swapticket', mount, { account_id: acct, book: T.book.code }, 'components/swapticket.js (WP-17): IRS / TRS / XCCY booking and the contracts table'); }
      box.appendChild(T.nib('PB OTC options and forwards (FX / equity options, FRAs, caps)', 'swaps are live; these answer 400 NOT_IN_THIS_BUILD'));
      box.appendChild(T.convNote(['SWAP_MARK', 'SWAP_VM', 'SWAP_RESET']));
      return box;
    }
    function curvePanel(curve) {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'Par yield curve', curve && curve.as_of ? T.h('small', null, 'as of ' + curve.as_of + ' . ' + (curve.source || 'Treasury.gov')) : null));
      if (!curve) { p.appendChild(T.h('div', { class: 'empty' }, 'curve unavailable')); return p; }
      var row = T.h('div', { class: 'row' });
      (curve.par || []).forEach(function (pt) { row.appendChild(T.h('span', { class: 'chip', title: 'par yield ' + pt.tenor }, pt.tenor + ' ' + T.fmt.num(pt.yield, 3) + ' %')); });
      row.appendChild(T.qchip(curve.quality, 'curve quality: ' + (curve.quality || 'unknown')));
      p.appendChild(row);
      return p;
    }
    /* the page poller loads at once and every 30 s (also after a failed first load) */
    T.poll.page('markets', 30000, load);
    var off = T.state.on('quotes', function (q) {
      if (dead || !grid || !data) { return; }
      var changed = false;
      (data.rows || []).forEach(function (r) { if (mergeQuote(r, q[r.instrument_id])) { changed = true; } });
      if (changed) { grid.update(data.rows || []); }
    });
    T.pages.markets.unmount = function () { dead = true; off(); T.pages.markets.unmount = null; };
  }
  T.pages.markets = { render: render, unmount: null, columns: columns, newer: newer };
})();
