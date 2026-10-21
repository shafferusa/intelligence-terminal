/* Private Markets account page (TERMINAL_DESIGN.md s13.13, s17.1 pages/private_markets.js, s16 /api/private/*; WP-19).
 * pages/markets.js hands the body of #/a/{lifetime}:PRIVATE/markets to T.pages.private_markets.render(el, route).
 *
 * - GET /api/private/companies?account_id=... answers {companies: [the private names], listed: [the companies that are no
 *   longer private]}; a bare list (an older server, the fixtures) is split here on c.listing.effective, so a LISTED or
 *   ACQUIRED company never sits among the private names.
 * - "no longer private" panel above the grid (owner 2026-10-21: "spacex is in private markets for some reason"): one line
 *   per listed / acquired company - the server's sentence ("Listed 2026-06-12: SPCX on Nasdaq - trade it in US Exchange")
 *   as a link to that symbol's page in the lifetime's US Exchange account (#/a/{lifetime}:US_EXCHANGE/i/{ticker}; the click
 *   resolves the symbol first through GET /api/instruments/resolve), the mark (the listed price from the listing date), the
 *   source, and this account's pre-listing position with an s button (sell, closing only: the server refuses any BUY).
 * - companies grid: name, sector, country, status (+ ticker), last round (date . type), post-money, price per share, the
 *   mark (T.pxHtml: q-private "LAST ROUND <date>", never dimmed as stale), the SOURCE and AS-OF next to it, this account's
 *   position and lock-up; b / s / Enter -> the negotiated-price ticket (components/privateticket.js) docked on the right.
 * - "Record a reported round" / "Add a private company": one small modal each (date, round type, post-money, price per
 *   share, lead investors, SOURCE URL - mandatory for any number) -> POST /api/private/companies/{id}/rounds /
 *   POST /api/private/companies; 400 SOURCE_REQUIRED is shown inline.
 * - Nothing is computed here: every figure and every sentence is the API's (the mark, its source, the position, the
 *   listing line); the page only formats and routes.
 */
(function () {
  'use strict';
  var T = window.T;
  var STATUSES = ['PRIVATE', 'FILED_IPO', 'PUBLIC'];

  function lastRound(c) { return c.last_round || null; }
  function roundText(c) { var r = lastRound(c); return r ? (r.round_date + ' . ' + (r.round_type || '')) : 'no reported round'; }
  function linkHtml(url) {
    if (!url) { return '<span class="muted">no source</span>'; }
    var isLink = /^https?:\/\//.test(url);
    var label = T.esc(url.replace(/^https?:\/\//, '').slice(0, 40)) + (url.length > 48 ? '...' : '');
    return isLink ? '<a href="' + T.esc(url) + '" target="_blank" rel="noopener" title="' + T.esc(url) + '">' + label + '</a>' : '<span title="' + T.esc(url) + '">' + label + '</span>';
  }
  function sourceCell(c) { var r = lastRound(c); return linkHtml(c.source_url || (r && r.source_url) || ''); }
  function statusCell(c) {
    var s = String(c.status || 'PRIVATE');
    var tag = '<span class="state-tag ' + T.esc(s) + '">' + T.esc(s.replace(/_/g, ' ')) + '</span>';
    if (c.ticker) { tag += ' <b>' + T.esc(c.ticker) + '</b>' + (c.listed_on ? ' <span class="muted">from ' + T.esc(c.listed_on) + '</span>' : ''); }
    return tag;
  }
  function positionCell(c) {
    var p = c.position;
    if (!p || !p.qty) { return ''; }
    return '<span class="' + (p.qty > 0 ? 'pos' : 'neg') + '">' + T.esc(T.fmt.qty(p.qty)) + '</span>' + (p.lock_until ? ' <span class="muted" title="lock-up until ' + T.esc(p.lock_until) + '">lock ' + T.esc(p.lock_until) + '</span>' : '');
  }
  function columns() {
    return [
      { key: 'name', label: 'company', cls: 'l', html: function (c) { return '<b>' + T.esc(c.name) + '</b> <span class="muted mono">' + T.esc(c.company_id) + '</span>'; }, text: function (c) { return c.name + ' ' + c.company_id; } },
      { key: 'sector', label: 'sector', cls: 'l', text: function (c) { return c.sector || ''; } },
      { key: 'country', label: 'country', cls: 'l', text: function (c) { return c.country || ''; } },
      { key: 'status', label: 'status', cls: 'l', html: statusCell, text: function (c) { return (c.status || '') + (c.ticker ? ' ' + c.ticker : ''); } },
      { key: 'round', label: 'last round', cls: 'l', text: roundText, sort: function (c) { var r = lastRound(c); return r ? r.round_date : ''; } },
      { key: 'post_money', label: 'post-money (USD)', text: function (c) { var r = lastRound(c); return r && r.post_money != null ? T.fmt.money(r.post_money, 0) : ''; }, sort: function (c) { var r = lastRound(c); return r && r.post_money != null ? r.post_money : ''; } },
      { key: 'pps', label: 'price / share', text: function (c) { var r = lastRound(c); return r && r.price_per_share != null ? T.fmt.px(r.price_per_share) : ''; } },
      { key: 'mark', label: 'mark', html: function (c) { return T.pxHtml(c.mark, { basis: 'last reported round' }); }, text: function (c) { return c.mark && c.mark.value != null ? c.mark.value : ''; } },
      { key: 'source', label: 'source', cls: 'l', html: sourceCell, text: function (c) { return c.source_url || ''; } },
      { key: 'as_of', label: 'as of', cls: 'l', text: function (c) { return c.as_of || ''; } },
      { key: 'leads', label: 'lead investors', cls: 'l', text: function (c) { var r = lastRound(c); return r && r.lead_investors ? r.lead_investors : ''; } },
      { key: 'position', label: 'position', html: positionCell, text: function (c) { return c.position && c.position.qty ? String(c.position.qty) : ''; } },
      { key: 'act', label: '', cls: 'l', html: function () { return '<button class="btn small act" data-act="b" title="Buy secondary shares (b)">b</button><button class="btn small act" data-act="s" title="Sell secondary (s)">s</button>'; }, text: function () { return ''; } }
    ];
  }

  function roundFields(prefix) {
    var f = {
      round_date: T.h('input', { type: 'text', placeholder: 'YYYY-MM-DD', id: prefix + '-date' }),
      round_type: T.h('input', { type: 'text', placeholder: 'Series X / tender offer / secondary / SAFE / secondary indication', id: prefix + '-type' }),
      post_money: T.h('input', { type: 'text', placeholder: 'post-money valuation in USD (optional)', id: prefix + '-post' }),
      price_per_share: T.h('input', { type: 'text', placeholder: 'implied price per share in USD (optional)', id: prefix + '-pps' }),
      lead_investors: T.h('input', { type: 'text', placeholder: 'lead investors (optional)', id: prefix + '-leads' }),
      source_url: T.h('input', { type: 'text', placeholder: 'source URL - required for any number', id: prefix + '-src' }),
      as_of: T.h('input', { type: 'text', placeholder: 'as-of date (defaults to the round date)', id: prefix + '-asof' })
    };
    f.rows = [T.field('round date', f.round_date), T.field('round type', f.round_type), T.field('post-money (USD)', f.post_money), T.field('price per share', f.price_per_share),
      T.field('lead investors', f.lead_investors), T.field('source URL', f.source_url, 'mandatory: nothing is recorded without a source'), T.field('as of', f.as_of)];
    f.read = function () {
      var o = {};
      ['round_date', 'round_type', 'post_money', 'price_per_share', 'lead_investors', 'source_url', 'as_of'].forEach(function (k) { var v = f[k].value.trim(); if (v) { o[k] = v; } });
      return o;
    };
    f.empty = function () { return !Object.keys(f.read()).length; };
    return f;
  }

  /* the API answer -> {names, listed}: a company no longer private (listing.effective) never sits among the private names */
  function splitCompanies(d) {
    var all = Array.isArray(d) ? d : ((d && d.companies) || []);
    var gone = Array.isArray(d) ? [] : ((d && d.listed) || []).slice();
    var names = [];
    all.forEach(function (c) { if (c && c.listing && c.listing.effective) { gone.push(c); } else if (c) { names.push(c); } });
    return { names: names, listed: gone };
  }
  function heldQty(c) { return c && c.position && c.position.qty ? Number(c.position.qty) : 0; }
  function usAccount(acct) { var s = String(acct || ''); var i = s.indexOf(':'); return (i >= 0 ? s.slice(0, i) : s) + ':US_EXCHANGE'; }
  /* where the listed shares trade: the server's trade_in, else this lifetime's US Exchange account and the ticker */
  function tradeIn(acct, c) {
    var l = (c && c.listing) || {};
    if (l.trade_in && l.trade_in.instrument_id) { return { account_id: l.trade_in.account_id || usAccount(acct), instrument_id: l.trade_in.instrument_id }; }
    if (l.us === false || !l.ticker) { return null; }
    return { account_id: usAccount(acct), instrument_id: l.ticker };
  }
  /* open the symbol's page in US Exchange; resolve first so a ticker the master does not hold yet is registered (202 -> wait) */
  function openListed(t) {
    T.api.getReady('/api/instruments/resolve', { symbol: t.instrument_id }).then(function (inst) {
      var id = (inst && (inst.instrument_id || (inst.instrument && inst.instrument.instrument_id))) || t.instrument_id;
      T.router.go(T.router.instrument(t.account_id, id));
    }, function (e) { T.api.fail(e, 'open ' + t.instrument_id + ' in US Exchange'); });
  }
  function listedLink(acct, c) {
    var l = (c && c.listing) || {};
    var t = tradeIn(acct, c);
    var text = l.message || ((l.kind === 'ACQUIRED' ? 'Acquired' : 'Listed') + (l.on ? ' ' + l.on : '') + (l.ticker ? ': ' + l.ticker : ''));
    if (!t) { return T.h('span', { class: 'listed-line' }, text); }
    var a = T.h('a', { href: T.router.instrument(t.account_id, t.instrument_id), class: 'listed-line', title: 'open ' + t.instrument_id + ' in ' + t.account_id }, text);
    a.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); openListed(t); });
    return a;
  }
  function listedRow(acct, c, onSell) {
    var l = c.listing || {};
    var row = T.h('div', { class: 'row', style: 'gap:10px;margin:4px 0', 'data-company': c.company_id });
    row.appendChild(T.stateTag(l.kind === 'ACQUIRED' ? 'ACQUIRED' : 'LISTED'));
    row.appendChild(T.h('b', null, c.name || c.company_id));
    row.appendChild(listedLink(acct, c));
    if (c.mark && c.mark.value != null) {
      var m = T.h('span', { class: 'muted' }); m.innerHTML = 'mark ' + T.pxHtml(c.mark, { basis: l.kind === 'ACQUIRED' ? 'last reported round' : 'listed price' }); row.appendChild(m);
    }
    if (l.source_url) { var s = T.h('span', { class: 'muted' }); s.innerHTML = 'source ' + linkHtml(l.source_url); row.appendChild(s); }
    var q = heldQty(c);
    if (q > 0) {
      row.appendChild(T.h('span', null, 'held here: ' + T.fmt.qty(q) + ' pre-listing shares' + (c.position.lock_until ? ' (lock-up until ' + c.position.lock_until + ')' : '')));
      row.appendChild(T.h('button', { class: 'btn small act', title: 'sell the pre-listing shares held here (closing only)', onclick: function () { onSell(c); } }, 's'));
    }
    return row;
  }
  function matches(c, f) { return (c.name + ' ' + c.company_id + ' ' + (c.sector || '') + ' ' + (c.ticker || '')).toUpperCase().indexOf(f.toUpperCase()) >= 0; }

  function render(el, route) {
    var acct = route.account;
    var data = null; var listed = []; var grid = null; var ticket = null; var filter = '';
    var head = T.h('div', { class: 'row' }, T.h('span', { class: 'sub' }, 'Private Markets - negotiated secondaries, last-round marks'), T.h('span', { class: 'spacer' }),
      T.h('button', { class: 'btn', onclick: function () { roundModal(null); } }, 'Record a reported round'),
      T.h('button', { class: 'btn', onclick: addModal }, 'Add a private company'));
    var outPanel = T.h('div', { class: 'panel', hidden: true });
    var two = T.h('div', { class: 'grid2' });
    var left = T.h('div', { class: 'panel' }, T.h('h2', null, 'companies', T.h('small', null, 'mark = last reported round (never dimmed as stale); source and as-of beside it')));
    var gridEl = T.h('div'); left.appendChild(gridEl);
    var right = T.h('div', { class: 'panel' }, T.h('h2', null, 'negotiated ticket', T.h('small', null, acct)));
    var ticketEl = T.h('div'); right.appendChild(ticketEl);
    two.appendChild(left); two.appendChild(right);
    el.appendChild(head); el.appendChild(outPanel); el.appendChild(two);
    el.appendChild(T.convNote(['QP', 'PRIVATE_MARK', 'PRIVATE_TRADE', 'FIFO']));

    function rows() {
      var list = data || [];
      if (!filter) { return list; }
      return list.filter(function (c) { return matches(c, filter); });
    }
    /* the ticket's companies: the private names plus listed / acquired ones still held here (those can only be sold) */
    function tradable() { return (data || []).concat(listed.filter(function (c) { return heldQty(c) > 0; })); }
    function openTicket(c, side) {
      if (ticket && ticket.prefill) { ticket.prefill({ company_id: c.company_id, side: side || 'BUY' }); return; }
      mountTicket(c ? c.company_id : null, side);
    }
    function mountTicket(companyId, side) {
      ticket = T.mountOptional('privateticket', ticketEl, { account_id: acct, companies: tradable(), company_id: companyId, side: side || 'BUY', onDone: load },
        'components/privateticket.js (WP-19): the negotiated-price ticket');
    }
    function paintListed() {
      T.clear(outPanel);
      outPanel.hidden = !listed.length;
      if (!listed.length) { return; }
      outPanel.appendChild(T.h('h2', null, 'no longer private', T.h('small', null, 'listed or acquired: not traded here - the line opens the listed shares in US Exchange')));
      var shown = listed.filter(function (c) { return !filter || matches(c, filter); });
      if (!shown.length) { outPanel.appendChild(T.h('div', { class: 'empty' }, 'no listed company matches the filter')); }
      shown.forEach(function (c) { outPanel.appendChild(listedRow(acct, c, function (x) { openTicket(x, 'SELL'); })); });
    }
    function paint() {
      paintListed();
      T.clear(gridEl);
      var list = rows();
      if (!list.length) { gridEl.appendChild(T.h('div', { class: 'empty' }, data && data.length ? 'no company matches the filter' : 'no private companies in the master')); }
      else {
        grid = T.components.grid.mount(gridEl, {
          columns: columns(), rows: list, rowKey: function (c) { return c.company_id; }, filter: true, tools: true, csv: 'private-companies.csv', empty: 'no companies',
          onEnter: function (c) { openTicket(c, 'BUY'); },
          onKey: function (key, c) { if (key === 'b') { openTicket(c, 'BUY'); return true; } if (key === 's') { openTicket(c, 'SELL'); return true; } if (key === 'r') { roundModal(c); return true; } return false; }
        });
      }
      if (ticket && ticket.refresh) { ticket.refresh(tradable()); } else if (!ticket) { mountTicket(null, 'BUY'); }
    }
    /* one delegated listener for the grid's b / s buttons (registered once: the grid re-renders on every poll) */
    gridEl.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button.act') : null; if (!b) { return; }
      var tr = b.closest('tr[data-i]');
      var c = tr && grid && grid.rows ? (grid.rows()[Number(tr.dataset.i)] || null) : null;
      if (!c && grid && grid.selected) { c = grid.selected(); }
      if (c) { e.stopPropagation(); openTicket(c, b.dataset.act === 's' ? 'SELL' : 'BUY'); }
    });
    function load() {
      return T.api.get('/api/private/companies', { account_id: acct }).then(function (d) {
        var s = splitCompanies(d);
        data = s.names; listed = s.listed;
        paint();
      }, function (e) { T.clear(gridEl); T.errorBox(gridEl, e, 'private companies'); });
    }
    function roundModal(c) {
      var sel = T.h('select');
      (data || []).forEach(function (x) { sel.appendChild(T.h('option', { value: x.company_id, selected: c && x.company_id === c.company_id }, x.name + ' (' + x.company_id + ')')); });
      var f = roundFields('rr');
      var err = T.h('div', { class: 'error' });
      T.ui.modal({ title: 'Record a reported round', sticky: true,
        body: T.h('div', null, T.form([T.field('company', sel)].concat(f.rows)), err,
          T.h('div', { class: 'hint' }, 'The mark becomes this round\'s price per share from its date (sentence PRIVATE_MARK). A valuation without a price per share leaves the mark unavailable (valuation only). The source URL is mandatory: a number without a source is refused (SOURCE_REQUIRED).')),
        actions: [{ label: 'Cancel' }, { label: 'Record', cls: 'primary', onClick: function (m, b) {
          b.disabled = true; T.clear(err);
          T.api.post('/api/private/companies/' + encodeURIComponent(sel.value) + '/rounds', f.read()).then(function (r) {
            m.close(); T.ui.toast('round recorded: ' + sel.value + (r && r.mark && r.mark.value != null ? ' mark ' + T.fmt.px(r.mark.value) : ''), 'ok'); load(); T.poll.kick('positions'); T.poll.kick('account_detail');
          }, function (e) { b.disabled = false; err.textContent = T.api.fail(e, 'record round'); });
        } }] });
    }
    function addModal() {
      var name = T.h('input', { type: 'text', placeholder: 'company name', id: 'pc-name' }), sector = T.h('input', { type: 'text', placeholder: 'sector' }), country = T.h('input', { type: 'text', placeholder: 'country' });
      var status = T.h('select'); STATUSES.forEach(function (s) { status.appendChild(T.h('option', { value: s }, s.replace(/_/g, ' '))); });
      var ticker = T.h('input', { type: 'text', placeholder: 'listed ticker (PUBLIC only)' }); var notes = T.h('input', { type: 'text', placeholder: 'notes (optional)' });
      var f = roundFields('ac');
      var err = T.h('div', { class: 'error' });
      T.ui.modal({ title: 'Add a private company', sticky: true, wide: true,
        body: T.h('div', null, T.form([T.field('name', name), T.field('sector', sector), T.field('country', country), T.field('status', status), T.field('ticker', ticker), T.field('notes', notes)]),
          T.h('h2', null, 'last reported round', T.h('small', null, 'optional; a source URL is required for any number')), T.form(f.rows), err,
          T.h('div', { class: 'hint' }, 'The company gets a PRIVATE instrument (PRV-<slug>) that trades only in this account at negotiated prices (sentence PRIVATE_TRADE).')),
        actions: [{ label: 'Cancel' }, { label: 'Add', cls: 'primary', onClick: function (m, b) {
          b.disabled = true; T.clear(err);
          var body = { name: name.value.trim(), sector: sector.value.trim() || null, country: country.value.trim() || null, status: status.value, ticker: ticker.value.trim() || null, notes: notes.value.trim() || null };
          if (!f.empty()) { body.last_round = f.read(); }
          T.api.post('/api/private/companies', body).then(function (r) {
            m.close(); T.ui.toast('company added: ' + (r && r.company_id || body.name), 'ok'); load();
          }, function (e) { b.disabled = false; err.textContent = T.api.fail(e, 'add company'); });
        } }] });
    }
    var fi = T.h('input', { type: 'text', placeholder: 'filter companies', style: 'width:180px' });
    fi.addEventListener('input', function () { filter = fi.value.trim(); paint(); });
    head.insertBefore(fi, head.querySelector('button'));
    load();
    T.poll.page('private_markets', 30000, function () { return data ? load() : Promise.resolve(); });
    return { unmount: function () { if (ticket && ticket.unmount) { ticket.unmount(); } } };
  }
  T.pages.private_markets = { render: render };
})();
