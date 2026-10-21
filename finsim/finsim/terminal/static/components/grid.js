/* Dense table (TERMINAL_DESIGN.md s17.1 components/grid.js; WP-12): sticky header, sort, filter, keyboard row
 * navigation, Copy (tab-separated) and CSV.  Rows are the API's rows; every cell is produced by the column's `html(row)`
 * (already escaped, e.g. T.pxHtml for prices) or `text(row)` (escaped here).  The grid computes nothing but subtotals
 * the page hands it through `subtotal(rows, group)`; sort keys come from `sort(row)` or the cell text.
 *
 *   var g = T.components.grid.mount(el, {
 *     columns: [{key, label, cls: 'num'|'l', html(row), text(row), sort(row), title(row), csv(row)}],
 *     rows: [], rowKey(row), rowClass(row), onEnter(row), onKey(key, row) -> bool, onClick(row, event), filter: true,
 *     tools: true, csv: 'file.csv', groupBy(row) -> label, subtotal(rows, label) -> {colKey: html},
 *     footer(rows) -> {colKey: html}, empty: 'no rows', focus: true, restore: otherGrid.state() });
 *   g.update(rows); g.selected(); g.select(i); g.rows(); g.state(); g.restore(state); g.destroy();
 *   T.components.grid.busy() -> true while a pointer is pressed inside any grid's table.
 * Keys while the grid is the key target (T.keys.grid; set on focus / click / mount with focus:true):
 * Up / Down move, Enter -> onEnter(row), any other key -> onKey(key, row) (pages map b / s / x).
 *
 * Re-renders cost the viewer nothing (UI-ops fix 2026-10-21: a poll re-render swallowed a click on a row's b button,
 * and a page rebuild cleared the filter box):
 * - update(rows) keeps the filter text, the sort, the selection and the scroll position.  The selection follows its
 *   row KEY (rowKey(row); else the row's id / *_id / key fields; else the first column's text; a key repeated on
 *   screen gets the first column's text, then an occurrence number): when the selected row is gone the selection is
 *   cleared, never moved to whichever row took its index (b / s / x act on the selection).
 * - when the visible rows keep their keys and order (a quote tick), only the cells whose HTML changed are rewritten,
 *   so the buttons and rows under the pointer stay the same elements;
 * - while the primary pointer is pressed inside the table a render is held back, and it runs as soon as the pointer
 *   is released and the click has been dispatched: a re-render can never swallow or retarget a click, and the click
 *   itself is never delayed (a press held longer than PRESS_MAX_MS stops holding renders back);
 * - state() / restore(state) carry the filter, sort, selection, scroll and focus to a NEW grid when a page must
 *   rebuild its layout.
 */
(function () {
  'use strict';
  var T = window.T;
  var ID_FIELD = /(^id$|_id$|^key$)/;
  var PRESS_MAX_MS = 3000;
  var pressed = null;                                          /* the grid whose table holds the pressed pointer */
  var hooked = false;
  function cmp(a, b) {
    var na = Number(String(a).replace(/[,%\s]/g, '')), nb = Number(String(b).replace(/[,%\s]/g, ''));
    var an = a !== '' && a != null && isFinite(na), bn = b !== '' && b != null && isFinite(nb);
    if (an && bn) { return na - nb; }
    if (an) { return -1; } if (bn) { return 1; }
    return String(a == null ? '' : a).localeCompare(String(b == null ? '' : b));
  }
  /* one set of document listeners for every grid: the release of the press ends the hold (capture phase); the held
   * render itself runs in a later task, i.e. after the browser has dispatched the click of that press */
  function hook() {
    if (hooked) { return; }
    hooked = true;
    function up(e) { var g = pressed; pressed = null; if (g) { g.release(e && e.pointerType === 'touch' ? 350 : 0); } }
    ['pointerup', 'pointercancel', 'mouseup', 'dragend'].forEach(function (n) { document.addEventListener(n, up, true); });
    window.addEventListener('blur', up);
  }
  function mount(el, spec) {
    spec = spec || {};
    var cols = spec.columns || [];
    var allRows = spec.rows || [];
    var state = { sortKey: spec.sortKey || null, sortAsc: spec.sortAsc !== false, filter: '', sel: -1, selKey: null };
    var root = T.h('div', { class: 'grid' });
    var tools = T.h('div', { class: 'grid-tools' });
    var input = T.h('input', { type: 'search', placeholder: 'filter', 'aria-label': 'filter rows', 'data-search': spec.search ? '1' : null });
    var count = T.h('span', { class: 'count' });
    if (spec.tools !== false) {
      if (spec.filter !== false) { tools.appendChild(input); }
      tools.appendChild(count);
      tools.appendChild(T.h('span', { class: 'spacer' }));
      tools.appendChild(T.h('button', { class: 'btn small', title: 'copy the visible rows (tab-separated)', onclick: function () { T.copy(tsv()); } }, 'Copy'));
      tools.appendChild(T.h('button', { class: 'btn small', title: 'download the visible rows as CSV', onclick: function () { T.csv(spec.csv || 'grid.csv', cols, visible()); } }, 'CSV'));
      root.appendChild(tools);
    }
    var scroll = T.h('div', { class: 'scroll', tabindex: '0', role: 'grid' });
    if (spec.maxHeight) { scroll.style.maxHeight = spec.maxHeight; }
    var table = T.h('table');
    scroll.appendChild(table); root.appendChild(scroll);
    T.clear(el).appendChild(root);
    var handle = { el: root, table: table };
    var visibleRows = [];                                      /* the rows ON SCREEN: tr[data-i] indexes this list */
    var drawn = null;                                          /* the model on screen (see build) */
    var pending = false, dead = false, pressTimer = null;
    var self = {
      release: function (delay) {
        clearTimeout(pressTimer); pressTimer = null;
        if (pending) { setTimeout(function () { if (pending && pressed !== self && !dead) { render(); } }, delay || 0); }
      }
    };

    function cellText(c, r) { if (c.text) { return c.text(r); } var v = r[c.key]; return v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v)); }
    function sortVal(c, r) { return c.sort ? c.sort(r) : cellText(c, r); }
    function keyOf(r, i) {
      var k = null;
      if (spec.rowKey) { try { k = spec.rowKey(r); } catch (e) { k = null; } }
      if ((k == null || k === '') && r && typeof r === 'object') {
        var parts = [];
        Object.keys(r).sort().forEach(function (f) { var v = r[f]; if (ID_FIELD.test(f) && (typeof v === 'string' || typeof v === 'number')) { parts.push(f + '=' + v); } });
        if (parts.length) { k = parts.join('&'); }
      }
      if (k == null || k === '') { k = cols.length ? '#' + cellText(cols[0], r) : '@' + i; }
      return String(k);
    }
    /* keys unique on screen: a repeated key gets the first column's text, then an occurrence number */
    function keysOf(vis) {
      var base = vis.map(function (r, i) { return keyOf(r, i); });
      var n = Object.create(null); var seen = Object.create(null);
      base.forEach(function (k) { n[k] = (n[k] || 0) + 1; });
      return base.map(function (k, i) {
        if (n[k] > 1 && cols.length) { k += '|#' + cellText(cols[0], vis[i]); }
        seen[k] = (seen[k] || 0) + 1;
        return seen[k] > 1 ? k + '~' + seen[k] : k;
      });
    }
    function visible() {
      var rows = allRows.slice();
      if (state.filter) {
        var f = state.filter.toLowerCase();
        rows = rows.filter(function (r) { return cols.some(function (c) { return String(cellText(c, r)).toLowerCase().indexOf(f) >= 0; }); });
      }
      if (state.sortKey) {
        var c = cols.filter(function (x) { return x.key === state.sortKey; })[0];
        if (c) { rows.sort(function (a, b) { var d = cmp(sortVal(c, a), sortVal(c, b)); return state.sortAsc ? d : -d; }); }
      }
      if (spec.groupBy) {
        var groups = {}; var order = [];
        rows.forEach(function (r) { var g = spec.groupBy(r) || ''; if (!groups[g]) { groups[g] = []; order.push(g); } groups[g].push(r); });
        rows = []; order.forEach(function (g) { rows = rows.concat(groups[g]); });
      }
      return rows;
    }
    function tsv() {
      var lines = [cols.map(function (c) { return c.label || c.key; }).join('\t')];
      visibleRows.forEach(function (r) { lines.push(cols.map(function (c) { return cellText(c, r); }).join('\t')); });
      return lines.join('\n');
    }
    function head() {
      var html = '<thead><tr>';
      cols.forEach(function (c) {
        var cls = (c.cls || 'num') + ' sortable' + (state.sortKey === c.key ? ' sorted' + (state.sortAsc ? ' asc' : '') : '');
        html += '<th class="' + cls + '" data-key="' + T.esc(c.key) + '" title="' + T.esc(c.title_h || ('sort by ' + (c.label || c.key))) + '">' + T.esc(c.label || c.key) + '</th>';
      });
      return html + '</tr></thead>';
    }
    /* the model of the table: one entry per <tr> of the tbody, {kind: row | group | subtotal | empty, key, cls, and
     * cells [{cls, title, html}] or html}; a data row carries i = its index in `vis` (= its data-i) */
    function build() {
      var vis = visible();
      var keys = keysOf(vis);
      var n = cols.length;
      var rows = [];
      if (!vis.length) { rows.push({ kind: 'empty', key: '', cls: '', html: '<td class="l empty" colspan="' + n + '">' + T.esc(spec.empty || 'no rows') + '</td>' }); }
      var lastGroup = null; var groupRows = [];
      function flushGroup() {
        if (spec.groupBy && spec.subtotal && lastGroup != null && groupRows.length) {
          var st = spec.subtotal(groupRows, lastGroup) || {};
          rows.push({ kind: 'subtotal', key: lastGroup, cls: 'subtotal', cells: cols.map(function (c, i) {
            return { cls: c.cls || 'num', title: null, html: String(i === 0 && st[c.key] == null ? T.esc('subtotal ' + lastGroup) : (st[c.key] == null ? '' : st[c.key])) };
          }) });
        }
        groupRows = [];
      }
      vis.forEach(function (r, i) {
        if (spec.groupBy) {
          var g = spec.groupBy(r) || '';
          if (g !== lastGroup) { flushGroup(); lastGroup = g; rows.push({ kind: 'group', key: g, cls: 'group', html: '<td class="l" colspan="' + n + '"><b>' + T.esc(g) + '</b></td>' }); }
          groupRows.push(r);
        }
        rows.push({ kind: 'row', key: keys[i], i: i, cls: (spec.onEnter ? 'click ' : '') + (spec.rowClass ? (spec.rowClass(r) || '') : ''), cells: cols.map(function (c) {
          var inner = c.html ? c.html(r) : T.esc(cellText(c, r));
          var t = c.title ? c.title(r) : null;
          return { cls: c.cls || 'num', title: c.title ? (t == null ? '' : String(t)) : null, html: inner == null ? '' : String(inner) };
        }) });
      });
      flushGroup();
      var foot = null;
      if (spec.footer && vis.length) {
        var ft = spec.footer(vis) || {};
        foot = '<tr>' + cols.map(function (c, i) { return '<td class="' + (c.cls || 'num') + '">' + (ft[c.key] == null ? (i === 0 ? 'total' : '') : ft[c.key]) + '</td>'; }).join('') + '</tr>';
      }
      return { head: head(), rows: rows, foot: foot, vis: vis, keys: keys };
    }
    function sameShape(a, b) {
      if (!a || a.head !== b.head || (a.foot == null) !== (b.foot == null) || a.rows.length !== b.rows.length) { return false; }
      for (var k = 0; k < a.rows.length; k++) { if (a.rows[k].kind !== b.rows[k].kind || a.rows[k].key !== b.rows[k].key) { return false; } }
      return true;
    }
    /* rewrite only what changed; false when the DOM does not match the model on screen (the caller then repaints) */
    function patch(model) {
      var body = table.tBodies[0];
      if (!body || body.rows.length !== model.rows.length) { return false; }
      for (var k = 0; k < model.rows.length; k++) {
        var nw = model.rows[k], old = drawn.rows[k], tr = body.rows[k];
        if (nw.cls !== old.cls) { tr.className = nw.cls; }
        if (!nw.cells) { if (nw.html !== old.html) { tr.innerHTML = nw.html; } continue; }
        if (tr.cells.length !== nw.cells.length) { return false; }
        for (var j = 0; j < nw.cells.length; j++) {
          var c = nw.cells[j], o = old.cells[j], td = tr.cells[j];
          if (c.html !== o.html) { td.innerHTML = c.html; }
          if (c.title !== o.title) { if (c.title == null) { td.removeAttribute('title'); } else { td.setAttribute('title', c.title); } }
          if (c.cls !== o.cls) { td.className = c.cls; }
        }
      }
      if (model.foot !== drawn.foot) { if (!table.tFoot) { return false; } table.tFoot.innerHTML = model.foot; }
      return true;
    }
    function paint(model) {
      var top = scroll.scrollTop, left = scroll.scrollLeft;
      var html = model.head + '<tbody>';
      model.rows.forEach(function (r) {
        html += '<tr' + (r.cls ? ' class="' + T.esc(r.cls) + '"' : '') + (r.kind === 'row' ? ' data-i="' + r.i + '"' : '') + '>';
        if (!r.cells) { html += r.html; }
        else { r.cells.forEach(function (c) { html += '<td class="' + c.cls + '"' + (c.title != null ? ' title="' + T.esc(c.title) + '"' : '') + '>' + c.html + '</td>'; }); }
        html += '</tr>';
      });
      html += '</tbody>';
      if (model.foot != null) { html += '<tfoot>' + model.foot + '</tfoot>'; }
      table.innerHTML = html;
      if (scroll.scrollTop !== top) { scroll.scrollTop = top; }
      if (scroll.scrollLeft !== left) { scroll.scrollLeft = left; }
    }
    function markSel() {
      T.qa('tr.sel', table).forEach(function (tr) { tr.classList.remove('sel'); });
      var tr = state.sel >= 0 ? table.querySelector('tr[data-i="' + state.sel + '"]') : null;
      if (tr) { tr.classList.add('sel'); }
      return tr;
    }
    /* the selection follows its key; a selected row that left the screen clears the selection */
    function followSel(keys) {
      if (state.selKey == null) { state.sel = -1; return; }
      if (keys[state.sel] === state.selKey) { return; }
      state.sel = keys.indexOf(state.selKey);
      if (state.sel < 0) { state.selKey = null; }
    }
    function render() {
      if (dead) { return; }
      if (pressed === self) { pending = true; return; }
      pending = false;
      var model = build();
      if (!(sameShape(drawn, model) && patch(model))) { paint(model); }
      drawn = model; visibleRows = model.vis;
      followSel(model.keys); markSel();
      count.textContent = visibleRows.length + (allRows.length !== visibleRows.length ? ' of ' + allRows.length : '') + ' rows';
    }
    function select(i, scrollIntoView) {
      if (!visibleRows.length) { state.sel = -1; state.selKey = null; markSel(); return; }
      state.sel = Math.max(0, Math.min(visibleRows.length - 1, i));
      state.selKey = drawn ? drawn.keys[state.sel] : keyOf(visibleRows[state.sel], state.sel);
      var tr = markSel();
      if (tr && scrollIntoView && tr.scrollIntoView) { tr.scrollIntoView({ block: 'nearest' }); }
    }
    function press(e) {
      if (e.button != null && e.button !== 0) { return; }     /* only the primary button makes a click */
      hook(); pressed = self;
      clearTimeout(pressTimer);
      pressTimer = setTimeout(function () { if (pressed === self) { pressed = null; self.release(0); } }, PRESS_MAX_MS);
    }
    handle.key = function (e) {
      if (e.key === 'ArrowDown') { select(state.sel + 1, true); return true; }
      if (e.key === 'ArrowUp') { select(state.sel - 1, true); return true; }
      var row = visibleRows[state.sel];
      if (e.key === 'Enter') { if (row && spec.onEnter) { spec.onEnter(row); return true; } return false; }
      if (spec.onKey && row) { return !!spec.onKey(e.key, row, e); }
      return false;
    };
    table.addEventListener('pointerdown', press);
    table.addEventListener('mousedown', press);
    table.addEventListener('click', function (e) {
      var th = e.target.closest('th');
      if (th && th.dataset.key) {
        if (state.sortKey === th.dataset.key) { state.sortAsc = !state.sortAsc; } else { state.sortKey = th.dataset.key; state.sortAsc = true; }
        render(); return;
      }
      var tr = e.target.closest('tr[data-i]');
      if (tr) { select(Number(tr.dataset.i)); T.keys.grid = handle; if (spec.onClick) { spec.onClick(visibleRows[state.sel], e); } }
    });
    table.addEventListener('dblclick', function (e) {
      var tr = e.target.closest('tr[data-i]');
      if (tr && spec.onEnter) { select(Number(tr.dataset.i)); spec.onEnter(visibleRows[state.sel]); }
    });
    scroll.addEventListener('focus', function () { T.keys.grid = handle; if (state.sel < 0) { select(0); } });
    scroll.addEventListener('keydown', function (e) { if (handle.key(e)) { e.preventDefault(); e.stopPropagation(); } });
    input.addEventListener('input', function () { state.filter = input.value; state.sel = -1; state.selKey = null; render(); });
    handle.update = function (rows) { allRows = rows || []; render(); };
    handle.rows = function () { return visibleRows; };
    handle.selected = function () { return visibleRows[state.sel] || null; };
    handle.select = select;
    /* what a page rebuild must carry over to the next grid (plain data) */
    handle.state = function () {
      var a = document.activeElement;
      var s = { filter: state.filter, sortKey: state.sortKey, sortAsc: state.sortAsc, selKey: state.selKey,
                scrollTop: scroll.scrollTop, scrollLeft: scroll.scrollLeft, focus: a === input ? 'filter' : (a === scroll ? 'grid' : null), caret: null };
      if (a === input) { try { s.caret = [input.selectionStart, input.selectionEnd]; } catch (e) { s.caret = null; } }
      return s;
    };
    handle.restore = function (s) {
      if (!s) { return handle; }
      state.filter = s.filter || ''; input.value = state.filter;
      if (s.sortKey !== undefined) { state.sortKey = s.sortKey; state.sortAsc = s.sortAsc !== false; }
      state.selKey = s.selKey == null ? null : String(s.selKey); state.sel = -1;
      render();
      scroll.scrollTop = s.scrollTop || 0; scroll.scrollLeft = s.scrollLeft || 0;
      try {
        if (s.focus === 'filter') { input.focus({ preventScroll: true }); if (s.caret && input.setSelectionRange) { input.setSelectionRange(s.caret[0], s.caret[1]); } }
        else if (s.focus === 'grid') { scroll.focus({ preventScroll: true }); }
      } catch (e) { /* focus is a convenience */ }
      return handle;
    };
    handle.destroy = function () { dead = true; clearTimeout(pressTimer); if (pressed === self) { pressed = null; } if (T.keys.grid === handle) { T.keys.grid = null; } T.clear(el); };
    render();
    if (spec.restore) { handle.restore(spec.restore); }
    if (spec.focus) { T.keys.grid = handle; }
    return handle;
  }
  T.components.grid = { mount: mount, cmp: cmp, busy: function () { return pressed !== null; }, PRESS_MAX_MS: PRESS_MAX_MS };
})();
