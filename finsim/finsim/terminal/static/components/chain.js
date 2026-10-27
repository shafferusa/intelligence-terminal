/* Option chain (TERMINAL_DESIGN.md s17.1 components/chain.js; WP-12; Build 2 P10 USX-25): calls | strike | puts with bid /
 * ask / size / iv / delta / oi, freshness per leg (T.pxHtml on the leg record), click bid or ask -> onPick(leg) which the
 * instrument page hands to the ticket (components/ticket.js) as an option leg.  Renders the API's
 * /api/instruments/{id}/chain answer: {underlying: QuoteRecord, expiries: [...], expiry, rows: [{strike, call: ChainRow +
 * instrument_id + quality, put: ...}], as_of, delay_label, quality} or the 202 {status: "fetching", retry_ms} shape (a
 * spinner, no numbers).
 *
 * - The expiry list shows the days to expiry; T.components.chain.defaultExpiry(expiries, today) is the one a page opens on:
 *   the nearest expiry 7 to 30 days out (else the nearest one at least 7 days out, else the farthest listed) - never today's
 *   0DTE unless nothing else is listed.
 * - Each new expiry scrolls its at-the-money row (the strike nearest the underlying) to the middle of the chain's own scroll
 *   box (the page itself never jumps); a refresh of the same expiry keeps where the viewer scrolled.
 * - A one-sided quote reads 'no bid' / 'no ask' (Cboe shows no price on that side: a bid of 0.00 is no bid) instead of a red
 *   N/A chip, and that side cannot be clicked (there is no price to deal at).
 *
 *   var c = T.components.chain.mount(el, {data, expiry, onExpiry(exp), onPick({instrument_id, kind, strike, expiry, side, price, leg})});
 *   c.update(data);  c.centerAtm();
 */
(function () {
  'use strict';
  var T = window.T;
  var DAY = 86400000;
  function dte(expiry, today) {
    var a = Date.parse(String(expiry).slice(0, 10) + 'T00:00:00Z'), b = Date.parse(String(today).slice(0, 10) + 'T00:00:00Z');
    return isFinite(a) && isFinite(b) ? Math.round((a - b) / DAY) : null;
  }
  function todayNy() { return T.fmt.date(T.clock.nowInt()); }
  /* the expiry a page opens on: the nearest 7..30 days out, else the nearest >= 7 days, else the farthest listed */
  function defaultExpiry(expiries, today) {
    var list = (expiries || []).slice().sort();
    if (!list.length) { return null; }
    today = today || todayNy();
    var inRange = list.filter(function (x) { var d = dte(x, today); return d != null && d >= 7 && d <= 30; });
    if (inRange.length) { return inRange[0]; }
    var later = list.filter(function (x) { var d = dte(x, today); return d != null && d >= 7; });
    return later.length ? later[0] : list[list.length - 1];
  }
  function oneSided(v) { return v == null || v === '' || !isFinite(Number(v)) || Number(v) <= 0; }
  function legCell(leg, field, kind, chainQuality) {
    if (!leg) { return '<td class="optq"></td>'; }
    if (oneSided(leg[field])) {
      return '<td class="optq none ' + field + '" title="Cboe shows no ' + field + ' for ' + T.esc(leg.instrument_id || '') + ': no price to ' + (field === 'bid' ? 'sell' : 'buy') + ' at">' +
        '<span class="muted">no ' + field + '</span></td>';
    }
    var rec = Object.assign({}, leg, { as_of: undefined });
    var other = field === 'bid' ? 'ask' : 'bid';
    if (oneSided(leg[other]) && (!rec.quality || rec.quality === 'unavailable')) {
      /* a one-sided quote: this side has a real price; it keeps the chain's own freshness (never a red N/A) and says it is one-sided */
      rec.quality = chainQuality || 'delayed';
      rec.note = 'one-sided quote: Cboe shows no ' + other + ' for this contract';
    }
    return '<td class="optq ' + field + '" data-kind="' + kind + '" data-field="' + field + '" data-id="' + T.esc(leg.instrument_id || '') + '">' + T.pxHtml(rec, { field: field }) + '</td>';
  }
  function num(v, dp) { return v == null || v === '' ? '' : T.fmt.num(v, dp == null ? 2 : dp); }
  function mount(el, opts) {
    opts = opts || {};
    var head = T.h('div', { class: 'chain-head' });
    var table = T.h('table', { class: 'chain' });
    var scroller = T.h('div', { class: 'scroll', style: 'max-height:48vh' }, table);
    var wrap = T.h('div', { class: 'grid' }, head, scroller);
    T.clear(el).appendChild(wrap);
    var data = opts.data || null; var expiry = opts.expiry || null; var centeredFor = null;
    function centerAtm() {
      var atm = T.q('tr.atm', table);
      if (!atm) { return false; }
      scroller.scrollTop = Math.max(0, atm.offsetTop - scroller.clientHeight / 2 + atm.offsetHeight / 2);
      return true;
    }
    function render() {
      T.clear(head);
      if (!data) { head.appendChild(T.h('span', { class: 'muted' }, 'no chain loaded')); table.innerHTML = ''; return; }
      if (data.pending || data.status === 'fetching') {
        head.appendChild(T.h('span', { class: 'spin' })); head.appendChild(T.h('span', { class: 'muted' }, ' fetching the chain from Cboe (delayed) ...'));
        table.innerHTML = ''; return;
      }
      if (data.status === 'unavailable') { head.appendChild(T.h('span', { class: 'muted' }, 'chain unavailable' + (data.note ? ': ' + data.note : ''))); table.innerHTML = ''; return; }
      var today = todayNy(); var shown = expiry || data.expiry;
      var sel = T.h('select', { 'aria-label': 'expiry' });
      (data.expiries || []).forEach(function (x) { var d = dte(x, today); sel.appendChild(T.h('option', { value: x, selected: x === shown }, x + (d != null ? ' (' + d + ' d)' : ''))); });
      sel.addEventListener('change', function () { expiry = sel.value; if (opts.onExpiry) { opts.onExpiry(expiry); } });
      head.appendChild(T.h('span', { class: 'muted' }, 'expiry ')); head.appendChild(sel);
      head.appendChild(T.h('span', { class: 'muted' }, 'calls left . puts right .'));
      head.appendChild(T.h('span', { class: 'muted' }, (data.delay_label || 'Cboe delayed') + (data.as_of ? ' . as of ' + T.fmt.time(data.as_of) : '')));
      head.appendChild(T.qchip(data.quality));
      var u = data.underlying;
      if (u) { head.appendChild(T.h('span', { class: 'muted' }, ' underlying ')); head.appendChild(T.px(u, { size: 'inline' })); }
      var spot = u ? Number(u.last != null ? u.last : u.mid) : null;
      var rows = data.rows || [];
      var atmIdx = -1, best = Infinity;
      rows.forEach(function (r, i) { if (spot != null && isFinite(spot)) { var d = Math.abs(Number(r.strike) - spot); if (d < best) { best = d; atmIdx = i; } } });
      var html = '<thead><tr><th title="calls">bid</th><th title="calls">ask</th><th title="calls">size</th><th title="calls">iv</th><th title="calls">delta</th><th title="calls">oi</th><th class="strike">strike</th>' +
        '<th title="puts">bid</th><th title="puts">ask</th><th title="puts">size</th><th title="puts">iv</th><th title="puts">delta</th><th title="puts">oi</th></tr></thead><tbody>';
      if (!rows.length) { html += '<tr><td colspan="13" class="empty l">no contracts for this expiry</td></tr>'; }
      rows.forEach(function (r, i) {
        var c = r.call, p = r.put;
        html += '<tr' + (i === atmIdx ? ' class="atm" title="at the money: the strike nearest the underlying"' : '') + '>';
        html += legCell(c, 'bid', 'call', data.quality) + legCell(c, 'ask', 'call', data.quality);
        html += '<td>' + (c ? T.esc(num(c.bid_size, 0) + 'x' + num(c.ask_size, 0)) : '') + '</td><td>' + (c ? T.esc(c.iv == null ? '' : T.fmt.pct(c.iv, 1)) : '') + '</td><td>' + (c ? T.esc(num(c.delta, 2)) : '') + '</td><td>' + (c ? T.esc(num(c.oi, 0)) : '') + '</td>';
        html += '<td class="strike" data-strike="' + T.esc(r.strike) + '">' + T.esc(T.fmt.px(r.strike)) + '</td>';
        html += legCell(p, 'bid', 'put', data.quality) + legCell(p, 'ask', 'put', data.quality);
        html += '<td>' + (p ? T.esc(num(p.bid_size, 0) + 'x' + num(p.ask_size, 0)) : '') + '</td><td>' + (p ? T.esc(p.iv == null ? '' : T.fmt.pct(p.iv, 1)) : '') + '</td><td>' + (p ? T.esc(num(p.delta, 2)) : '') + '</td><td>' + (p ? T.esc(num(p.oi, 0)) : '') + '</td>';
        html += '</tr>';
      });
      html += '</tbody>';
      table.innerHTML = html;
      if (rows.length && centeredFor !== (data.expiry || shown)) {
        /* a new expiry: its ATM row to the middle of the chain box (after layout) */
        var ex = data.expiry || shown;
        setTimeout(function () { if (centerAtm()) { centeredFor = ex; } }, 0);
      }
    }
    table.addEventListener('click', function (e) {
      var td = e.target.closest('td.optq'); if (!td || !td.dataset.id || !opts.onPick) { return; }
      var tr = td.parentNode; var strike = tr.querySelector('td.strike'); var leg = null;
      (data.rows || []).forEach(function (r) { if (String(r.strike) === (strike && strike.dataset.strike)) { leg = td.dataset.kind === 'call' ? r.call : r.put; } });
      if (!leg || oneSided(leg[td.dataset.field])) { return; }
      opts.onPick({ instrument_id: td.dataset.id, kind: td.dataset.kind, strike: strike.dataset.strike, expiry: expiry || data.expiry, side: td.dataset.field, price: leg[td.dataset.field], leg: leg,
                    underlying_id: (data.underlying && data.underlying.instrument_id) || data.underlying_id || null });
    });
    render();
    return { el: wrap, update: function (d) { data = d; render(); }, expiry: function (x) { if (x !== undefined) { expiry = x; } return expiry; },
             centerAtm: centerAtm };
  }
  T.components.chain = { mount: mount, defaultExpiry: defaultExpiry, dte: dte };
})();
