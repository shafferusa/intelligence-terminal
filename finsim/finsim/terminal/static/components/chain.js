/* Option chain (TERMINAL_DESIGN.md s17.1 components/chain.js; WP-12): calls | strike | puts with bid / ask / size / iv /
 * delta / oi, freshness per leg (T.pxHtml on the leg record), click bid or ask -> onPick(leg) which the instrument page
 * hands to the ticket (components/ticket.js, WP-13) as an option leg.  Renders the API's /api/instruments/{id}/chain
 * answer: {underlying: QuoteRecord, expiries: [...], rows: [{strike, call: ChainRow + instrument_id + quality, put: ...}],
 * as_of, delay_label, quality} or the 202 {status: "fetching", retry_ms} shape (a spinner, no numbers).
 *
 *   var c = T.components.chain.mount(el, {data, expiry, onExpiry(exp), onPick({instrument_id, kind, strike, expiry, side, price})});
 *   c.update(data);
 */
(function () {
  'use strict';
  var T = window.T;
  function legCell(leg, field, kind) {
    if (!leg) { return '<td class="optq"></td>'; }
    var rec = Object.assign({}, leg, { as_of: undefined });
    return '<td class="optq ' + field + '" data-kind="' + kind + '" data-field="' + field + '" data-id="' + T.esc(leg.instrument_id || '') + '">' + T.pxHtml(rec, { field: field }) + '</td>';
  }
  function num(v, dp) { return v == null || v === '' ? '' : T.fmt.num(v, dp == null ? 2 : dp); }
  function mount(el, opts) {
    opts = opts || {};
    var head = T.h('div', { class: 'chain-head' });
    var table = T.h('table', { class: 'chain' });
    var wrap = T.h('div', { class: 'grid' }, head, T.h('div', { class: 'scroll', style: 'max-height:48vh' }, table));
    T.clear(el).appendChild(wrap);
    var data = opts.data || null; var expiry = opts.expiry || null;
    function render() {
      T.clear(head);
      if (!data) { head.appendChild(T.h('span', { class: 'muted' }, 'no chain loaded')); table.innerHTML = ''; return; }
      if (data.pending || data.status === 'fetching') {
        head.appendChild(T.h('span', { class: 'spin' })); head.appendChild(T.h('span', { class: 'muted' }, ' fetching the chain from Cboe (delayed) ...'));
        table.innerHTML = ''; return;
      }
      var sel = T.h('select', { 'aria-label': 'expiry' });
      (data.expiries || []).forEach(function (x) { sel.appendChild(T.h('option', { value: x, selected: x === (expiry || data.expiry) }, x)); });
      sel.addEventListener('change', function () { expiry = sel.value; if (opts.onExpiry) { opts.onExpiry(expiry); } });
      head.appendChild(T.h('span', { class: 'muted' }, 'expiry ')); head.appendChild(sel);
      head.appendChild(T.h('span', { class: 'muted' }, (data.delay_label || 'Cboe delayed') + (data.as_of ? ' . as of ' + T.fmt.time(data.as_of) : '')));
      head.appendChild(T.qchip(data.quality));
      var u = data.underlying;
      if (u) { head.appendChild(T.h('span', { class: 'muted' }, ' underlying ')); head.appendChild(T.px(u, { size: 'inline' })); }
      var spot = u ? Number(u.last != null ? u.last : u.mid) : null;
      var rows = data.rows || [];
      var atmIdx = -1, best = Infinity;
      rows.forEach(function (r, i) { if (spot != null) { var d = Math.abs(Number(r.strike) - spot); if (d < best) { best = d; atmIdx = i; } } });
      var html = '<thead><tr><th>bid</th><th>ask</th><th>size</th><th>iv</th><th>delta</th><th>oi</th><th class="strike">strike</th><th>bid</th><th>ask</th><th>size</th><th>iv</th><th>delta</th><th>oi</th></tr></thead><tbody>';
      if (!rows.length) { html += '<tr><td colspan="13" class="empty l">no contracts for this expiry</td></tr>'; }
      rows.forEach(function (r, i) {
        var c = r.call, p = r.put;
        html += '<tr' + (i === atmIdx ? ' class="atm"' : '') + '>';
        html += legCell(c, 'bid', 'call') + legCell(c, 'ask', 'call');
        html += '<td>' + (c ? T.esc(num(c.bid_size, 0) + 'x' + num(c.ask_size, 0)) : '') + '</td><td>' + (c ? T.esc(c.iv == null ? '' : T.fmt.pct(c.iv, 1)) : '') + '</td><td>' + (c ? T.esc(num(c.delta, 2)) : '') + '</td><td>' + (c ? T.esc(num(c.oi, 0)) : '') + '</td>';
        html += '<td class="strike" data-strike="' + T.esc(r.strike) + '">' + T.esc(T.fmt.px(r.strike)) + '</td>';
        html += legCell(p, 'bid', 'put') + legCell(p, 'ask', 'put');
        html += '<td>' + (p ? T.esc(num(p.bid_size, 0) + 'x' + num(p.ask_size, 0)) : '') + '</td><td>' + (p ? T.esc(p.iv == null ? '' : T.fmt.pct(p.iv, 1)) : '') + '</td><td>' + (p ? T.esc(num(p.delta, 2)) : '') + '</td><td>' + (p ? T.esc(num(p.oi, 0)) : '') + '</td>';
        html += '</tr>';
      });
      html += '</tbody>';
      table.innerHTML = html;
    }
    table.addEventListener('click', function (e) {
      var td = e.target.closest('td.optq'); if (!td || !td.dataset.id || !opts.onPick) { return; }
      var tr = td.parentNode; var strike = tr.querySelector('td.strike'); var leg = null;
      (data.rows || []).forEach(function (r) { if (String(r.strike) === (strike && strike.dataset.strike)) { leg = td.dataset.kind === 'call' ? r.call : r.put; } });
      if (!leg) { return; }
      opts.onPick({ instrument_id: td.dataset.id, kind: td.dataset.kind, strike: strike.dataset.strike, expiry: expiry || data.expiry, side: td.dataset.field, price: leg[td.dataset.field], leg: leg });
    });
    render();
    return { el: wrap, update: function (d) { data = d; render(); }, expiry: function () { return expiry; } };
  }
  T.components.chain = { mount: mount };
})();
