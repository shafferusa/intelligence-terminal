/* Private Markets account page (TERMINAL_DESIGN.md s13.13, s17.1 pages/private_markets.js, s16 /api/private/*; WP-19; Build 2 P11).
 * Registers T.marketPlugins.PRIVATE (BUILD2_PLAN C11): pages/markets.js hands it the Markets body of #/a/{lifetime}:PRIVATE; the
 * account's dock holds the negotiated ticket (components/privateticket.js, T.ticketKinds.PRIVATE).  T.pages.private_markets.render
 * stays for an older shell.
 *
 * - GET /api/private/companies?account_id=... answers {companies: [the private names], listed: [the companies that are no
 *   longer private], venues}; a bare list (an older server, the fixtures) is split here on c.listing.effective, so a LISTED or
 *   ACQUIRED company never sits among the private names.
 * - "no longer private" panel above the grid (owner 2026-10-21: "spacex is in private markets for some reason"): one line
 *   per listed / acquired company - the server's sentence ("Listed 2026-06-12: SPCX on Nasdaq - trade it in US Exchange")
 *   as a link to that symbol's page in the lifetime's US Exchange account (#/a/{lifetime}:US_EXCHANGE/i/{ticker}; the click
 *   resolves the symbol first through GET /api/instruments/resolve), the mark (the listed price from the listing date), the
 *   source, and this account's pre-listing position with an s button (sell, closing only: the server refuses any BUY).
 * - companies grid, FULL WIDTH (OA-27): company (+ sector), b / s, the mark (T.pxHtml: q-private "LAST ROUND <date>", never
 *   dimmed as stale), last round, post-money, price per share, this account's position and lock-up, status, source + as-of,
 *   lead investors, country; the less important columns give way first on a narrow page.  b / s / Enter open the negotiated
 *   ticket in the DOCK (T.ticket.open kind PRIVATE); r records a round.  The grid updates in place on the 30 s poll.
 * - "Record a reported round" / "Add a private company" / "Mark as listed": one small modal each (a source URL is mandatory
 *   for any number and any status) -> POST /api/private/companies/{id}/rounds / POST /api/private/companies /
 *   POST /api/private/companies/{id}/status (SR-06: ticker, listing date, venue, source; or "filed for IPO" with its filing).
 * - Nothing is computed here: every figure and every sentence is the API's (the mark, its source, the position, the
 *   listing line); the page only formats and routes.
 */
(function () {
  'use strict';
  var T = window.T;
  T.marketPlugins = T.marketPlugins || {};
  var STATUSES = ['PRIVATE', 'FILED_IPO', 'PUBLIC'];
  var CSS = '.pm{container-type:inline-size}.pm .sub2{display:block;font-size:10px;line-height:1.3;color:var(--fg3);max-width:260px;overflow:hidden;text-overflow:ellipsis}' +
            '.pm td{padding-top:3px;padding-bottom:3px}.pm .acts{white-space:nowrap}' +
            '@container (max-width:1250px){.pm .c-lead,.pm .c-country{display:none}}@container (max-width:1080px){.pm .c-src{display:none}}' +
            '@container (max-width:940px){.pm .c-pps,.pm .c-status{display:none}}@container (max-width:820px){.pm .c-post{display:none}}@container (max-width:640px){.pm .c-round{display:none}}' +
            '.pm .lrow{display:grid;grid-template-columns:70px minmax(120px,210px) minmax(0,1fr) auto;gap:1px 10px;align-items:baseline;padding:3px 0;border-bottom:1px solid var(--line);font-size:12px}' +
            '.pm .lrow .lmsg{min-width:0}.pm .lrow .sub2{max-width:none;white-space:normal}' +
            '.pm .headrow{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:2px 0 8px}.pm .headrow .sub{flex:1 1 260px}';
  function ensureCss() { if (document.getElementById('private-markets-css')) { return; } var s = document.createElement('style'); s.id = 'private-markets-css'; s.textContent = CSS; document.head.appendChild(s); }

  function lastRound(c) { return c.last_round || null; }
  function roundText(c) { var r = lastRound(c); return r ? (r.round_date + ' . ' + (r.round_type || '')) : 'no reported round'; }
  function linkHtml(url, max) {
    if (!url) { return '<span class="muted">no source</span>'; }
    var isLink = /^https?:\/\//.test(url);
    var n = max || 34;
    var label = T.esc(url.replace(/^https?:\/\//, '').slice(0, n)) + (url.length > n + 8 ? '...' : '');
    return isLink ? '<a href="' + T.esc(url) + '" target="_blank" rel="noopener" title="' + T.esc(url) + '">' + label + '</a>' : '<span title="' + T.esc(url) + '">' + label + '</span>';
  }
  function sourceCell(c) { var r = lastRound(c); return linkHtml(c.source_url || (r && r.source_url) || '') + (c.as_of ? '<span class="sub2">as of ' + T.esc(c.as_of) + '</span>' : ''); }
  function statusCell(c) {
    var s = String(c.status || 'PRIVATE');
    var tag = '<span class="state-tag ' + T.esc(s) + '">' + T.esc(s.replace(/_/g, ' ')) + '</span>';
    if (c.ticker) { tag += ' <b>' + T.esc(c.ticker) + '</b>' + (c.listed_on ? ' <span class="muted">from ' + T.esc(c.listed_on) + '</span>' : ''); }
    return tag;
  }
  function positionCell(c) {
    var p = c.position;
    if (!p || !p.qty) { return ''; }
    return '<span class="' + (p.qty > 0 ? 'pos' : 'neg') + '">' + T.esc(T.fmt.qty(p.qty)) + '</span>' + (p.lock_until ? '<span class="sub2" title="lock-up until ' + T.esc(p.lock_until) + '">lock-up ' + T.esc(p.lock_until) + '</span>' : '');
  }
  function columns() {
    return [
      { key: 'name', label: 'company', cls: 'l', html: function (c) { return '<b>' + T.esc(c.name) + '</b><span class="sub2">' + T.esc([c.company_id, c.sector].filter(Boolean).join(' . ')) + '</span>'; }, text: function (c) { return c.name + ' ' + c.company_id; } },
      { key: 'act', label: ' ', cls: 'l acts', html: function () { return '<button class="btn small act" data-act="b" title="buy secondary shares (negotiated ticket in the dock)">b</button><button class="btn small act" data-act="s" title="sell secondary (negotiated ticket in the dock)">s</button>'; }, text: function () { return ''; } },
      { key: 'position', label: 'position', html: positionCell, text: function (c) { return c.position && c.position.qty ? String(c.position.qty) : ''; } },
      { key: 'mark', label: 'mark', html: function (c) { return T.pxHtml(c.mark, { basis: 'last reported round' }); }, text: function (c) { return c.mark && c.mark.value != null ? c.mark.value : ''; } },
      { key: 'round', label: 'last round', cls: 'l c-round', html: function (c) { var r = lastRound(c); return r ? T.esc(r.round_date) + '<span class="sub2" style="max-width:170px" title="' + T.esc(r.round_type || '') + '">' + T.esc(r.round_type || '') + '</span>' : '<span class="muted">no reported round</span>'; }, text: roundText, sort: function (c) { var r = lastRound(c); return r ? r.round_date : ''; } },
      { key: 'post_money', label: 'post-money (USD)', cls: 'num c-post', text: function (c) { var r = lastRound(c); return r && r.post_money != null ? T.fmt.money(r.post_money, 0) : ''; }, sort: function (c) { var r = lastRound(c); return r && r.post_money != null ? r.post_money : ''; } },
      { key: 'pps', label: 'price / share', cls: 'num c-pps', text: function (c) { var r = lastRound(c); return r && r.price_per_share != null ? T.fmt.px(r.price_per_share) : 'valuation only'; } },
      { key: 'status', label: 'status', cls: 'l c-status', html: statusCell, text: function (c) { return (c.status || '') + (c.ticker ? ' ' + c.ticker : ''); } },
      { key: 'source', label: 'source', cls: 'l c-src', html: sourceCell, text: function (c) { return c.source_url || ''; } },
      { key: 'leads', label: 'lead investors', cls: 'l c-lead', html: function (c) { var r = lastRound(c); var t = r && r.lead_investors ? r.lead_investors : ''; return '<span class="sub2" style="display:inline-block;max-width:200px" title="' + T.esc(t) + '">' + T.esc(t) + '</span>'; }, text: function (c) { var r = lastRound(c); return r && r.lead_investors ? r.lead_investors : ''; } },
      { key: 'country', label: 'country', cls: 'l c-country', text: function (c) { return c.country || ''; } }
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
    var row = T.h('div', { class: 'lrow', 'data-company': c.company_id });
    row.appendChild(T.stateTag(l.kind === 'ACQUIRED' ? 'ACQUIRED' : 'LISTED'));
    row.appendChild(T.h('b', null, c.name || c.company_id));
    var msg = T.h('div', { class: 'lmsg' }, listedLink(acct, c));
    var sub = T.h('span', { class: 'sub2' });
    var bits = [];
    if (c.mark && c.mark.value != null) { bits.push('mark ' + T.pxHtml(c.mark, { basis: l.kind === 'ACQUIRED' ? 'last reported round' : 'listed price', size: 'inline' })); }
    if (l.source_url) { bits.push('source ' + linkHtml(l.source_url)); }
    sub.innerHTML = bits.join(' . ');
    msg.appendChild(sub);
    row.appendChild(msg);
    var q = heldQty(c);
    var end = T.h('span');
    if (q > 0) {
      end.appendChild(T.h('span', { class: 'muted', title: c.position.lock_until ? 'lock-up until ' + c.position.lock_until : '' }, 'held ' + T.fmt.qty(q) + ' '));
      end.appendChild(T.h('button', { class: 'btn small act', title: 'sell the pre-listing shares held here (closing only)', onclick: function () { onSell(c); } }, 's'));
    }
    row.appendChild(end);
    return row;
  }
  function matches(c, f) { return (c.name + ' ' + c.company_id + ' ' + (c.sector || '') + ' ' + (c.ticker || '')).toUpperCase().indexOf(f.toUpperCase()) >= 0; }
  function today() { try { return T.fmt.date(T.clock.nowInt()); } catch (e) { return ''; } }

  function render(el, routeOrCtx) {
    var rc = routeOrCtx || {};
    var acct = rc.account_id || rc.account;
    var data = null; var listed = []; var venues = []; var grid = null; var filter = ''; var dead = false;
    ensureCss();
    var root = T.h('div', { class: 'pm' });
    var fi = T.h('input', { type: 'text', placeholder: 'filter companies', style: 'width:180px', 'aria-label': 'filter companies' });
    var head = T.h('div', { class: 'headrow' }, T.h('span', { class: 'sub' }, 'Private Markets - negotiated secondaries, last-round marks; the negotiated ticket is in the dock'), fi,
      T.h('button', { class: 'btn', onclick: function () { roundModal(selected()); } }, 'Record a reported round'),
      T.h('button', { class: 'btn', onclick: function () { listModal(selected()); }, title: 'a company listed or filed for its IPO: record it with its source' }, 'Mark as listed'),
      T.h('button', { class: 'btn', onclick: addModal }, 'Add a private company'));
    var outPanel = T.h('div', { class: 'panel', hidden: true });
    var rk = T.keys.labels(['grid.buy', 'grid.sell']), rr = T.keys.label('grid.round');   /* the keys in force (Settings > Keyboard) */
    var main = T.h('div', { class: 'panel' }, T.h('h2', null, 'companies', T.h('small', null, 'mark = last reported round (never dimmed as stale); a valuation-only round leaves no mark; ' +
      (rk ? rk + ' on a company open' : 'the b / s buttons open') + ' the negotiated ticket in the dock' + (rr ? '; ' + rr + ' records a reported round' : ''))));
    var gridEl = T.h('div'); main.appendChild(gridEl);
    root.appendChild(head); root.appendChild(outPanel); root.appendChild(main);
    root.appendChild(T.convNote(['QP', 'PRIVATE_MARK', 'PRIVATE_TRADE', 'FIFO']));
    el.appendChild(root);

    function rows() {
      var list = data || [];
      return filter ? list.filter(function (c) { return matches(c, filter); }) : list;
    }
    function selected() { return grid && grid.selected ? grid.selected() : null; }
    function openTicket(c, side) {
      if (!c) { return; }
      T.ticket.open({ account_id: acct, instrument_id: c.instrument_id || c.company_id, company_id: c.company_id, kind: 'PRIVATE', side: side || 'BUY' });
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
      var list = rows();
      var empty = data && data.length ? 'no company matches the filter' : 'no private companies in the master';
      if (!grid) {
        grid = T.components.grid.mount(gridEl, {
          columns: columns(), rows: list, rowKey: function (c) { return c.company_id; }, filter: false, tools: true, csv: 'private-companies.csv', empty: empty,
          onEnter: function (c) { openTicket(c, 'BUY'); },
          onKey: function (key, c) { if (key === 'b') { openTicket(c, 'BUY'); return true; } if (key === 's') { openTicket(c, 'SELL'); return true; } if (key === 'r') { roundModal(c); return true; } return false; },
          onClick: function (c, e) { var b = e && e.target && e.target.closest ? e.target.closest('button.act') : null; if (b && c) { openTicket(c, b.dataset.act === 's' ? 'SELL' : 'BUY'); } }
        });
      } else { grid.update(list, { empty: empty }); }
    }
    function load() {
      return T.api.get('/api/private/companies', { account_id: acct }).then(function (d) {
        if (dead) { return; }
        var s = splitCompanies(d);
        data = s.names; listed = s.listed; venues = (d && d.venues) || venues;
        if (T.rememberKinds) { T.rememberKinds(s.names.concat(s.listed).map(function (c) { return { instrument_id: c.instrument_id || c.company_id, kind: 'PRIVATE' }; })); }
        paint();
      }, function (e) { if (dead) { return; } if (!grid) { T.clear(gridEl); T.errorBox(gridEl, e, 'private companies'); } else { console.error('private companies refresh', e); } });
    }
    function companySelect(list, c) {
      var sel = T.h('select');
      list.forEach(function (x) { sel.appendChild(T.h('option', { value: x.company_id, selected: !!(c && x.company_id === c.company_id) }, x.name + ' (' + x.company_id + ')')); });
      return sel;
    }
    function roundModal(c) {
      var sel = companySelect(data || [], c);
      var f = roundFields('rr');
      var err = T.h('div', { class: 'error' });
      T.ui.modal({ title: 'Record a reported round', sticky: true,
        body: T.h('div', null, T.form([T.field('company', sel)].concat(f.rows)), err,
          T.h('div', { class: 'hint' }, 'The mark becomes this round\'s price per share from its date. A valuation without a price per share leaves the mark unavailable (valuation only: the position is carried at cost). The source URL is mandatory: a number without a source is refused.')),
        actions: [{ label: 'Cancel' }, { label: 'Record', cls: 'primary', onClick: function (m, b) {
          b.disabled = true; T.clear(err);
          T.api.post('/api/private/companies/' + encodeURIComponent(sel.value) + '/rounds', f.read()).then(function (r) {
            m.close(); T.ui.toast('round recorded: ' + sel.value + (r && r.mark && r.mark.value != null ? ' mark ' + T.fmt.px(r.mark.value) : ''), 'ok'); load(); T.poll.kick('positions'); T.poll.kick('account_detail');
          }, function (e) { b.disabled = false; err.textContent = T.api.fail(e, 'record round'); });
        } }] });
    }
    /* SR-06: a company that lists (or files for its IPO) is recorded with its source; from the listing date it leaves the private names
       and its mark is the listed instrument's */
    function listModal(c) {
      var sel = companySelect(data || [], c);
      var status = T.h('select', null, T.h('option', { value: 'PUBLIC' }, 'listed (its shares trade on an exchange)'), T.h('option', { value: 'FILED_IPO' }, 'filed for its IPO (still private)'));
      var ticker = T.h('input', { type: 'text', placeholder: 'ticker, e.g. SPCX', id: 'ml-ticker' });
      var on = T.h('input', { type: 'text', placeholder: 'YYYY-MM-DD (first trade date)', id: 'ml-date', value: today() });
      var ven = T.h('select', { id: 'ml-venue' });
      (venues.length ? venues : [{ name: 'Nasdaq Global Select Market' }, { name: 'New York Stock Exchange' }]).forEach(function (v) { ven.appendChild(T.h('option', { value: v.name }, v.name)); });
      ven.appendChild(T.h('option', { value: '' }, 'another venue (type it)'));
      var other = T.h('input', { type: 'text', placeholder: 'venue name (a non-US venue is kept by name, not traded here)', id: 'ml-other', hidden: true });
      var src = T.h('input', { type: 'text', placeholder: 'source URL - the exchange notice or the issuer\'s release (required)', id: 'ml-src' });
      var err = T.h('div', { class: 'error' });
      var listedRows = [T.field('ticker', ticker), T.field('first trade date', on), T.field('venue', T.h('span', { class: 'row' }, ven, other))];
      var formEl = T.form([T.field('company', sel), T.field('status', status)].concat(listedRows).concat([T.field('source URL', src, 'mandatory: nothing is recorded without a source')]));
      function sync() {
        var pub = status.value === 'PUBLIC';
        [ticker, on, ven, other].forEach(function (x) { x.disabled = !pub; });
        other.hidden = !pub || ven.value !== '';
      }
      status.addEventListener('change', sync); ven.addEventListener('change', sync); sync();
      T.ui.modal({ title: 'Mark as listed', sticky: true, wide: true,
        body: T.h('div', null, formEl, err,
          T.h('div', { class: 'hint' }, 'From the first trade date the company leaves the private names: new purchases go through US Exchange (a US venue registers the ticker there), shares held here can still be sold (closing only) and their mark becomes the listed price. Filed for IPO keeps it private until the shares trade.')),
        actions: [{ label: 'Cancel' }, { label: 'Save', cls: 'primary', onClick: function (m, b) {
          b.disabled = true; T.clear(err);
          var body = { status: status.value, source_url: src.value.trim() };
          if (status.value === 'PUBLIC') { body.ticker = ticker.value.trim().toUpperCase(); body.listed_on = on.value.trim(); body.exchange = ven.value || other.value.trim(); }
          T.api.post('/api/private/companies/' + encodeURIComponent(sel.value) + '/status', body).then(function (r) {
            m.close();
            var line = r && r.listing && r.listing.message ? r.listing.message : (sel.value + ' ' + status.value.replace(/_/g, ' ').toLowerCase());
            T.ui.toast((r && r.name ? r.name + ': ' : '') + line, 'ok', 6000); load(); T.poll.kick('positions');
          }, function (e) { b.disabled = false; err.textContent = T.api.fail(e, 'mark as listed'); });
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
          T.h('div', { class: 'hint' }, 'The company gets a private instrument (PRV-<name>) that trades only in this account at negotiated prices.')),
        actions: [{ label: 'Cancel' }, { label: 'Add', cls: 'primary', onClick: function (m, b) {
          b.disabled = true; T.clear(err);
          var body = { name: name.value.trim(), sector: sector.value.trim() || null, country: country.value.trim() || null, status: status.value, ticker: ticker.value.trim() || null, notes: notes.value.trim() || null };
          if (!f.empty()) { body.last_round = f.read(); }
          T.api.post('/api/private/companies', body).then(function (r) {
            m.close(); T.ui.toast('company added: ' + (r && r.company_id || body.name), 'ok'); load();
          }, function (e) { b.disabled = false; err.textContent = T.api.fail(e, 'add company'); });
        } }] });
    }
    fi.addEventListener('input', function () { filter = fi.value.trim(); paint(); });
    var offTrade = T.state && T.state.on ? T.state.on('private_trade', function () { if (!dead) { load(); } }) : null;
    load();
    T.poll.page('private_markets', 30000, function () { return data ? load() : Promise.resolve(); });
    return { unmount: function () { dead = true; if (offTrade) { offTrade(); } if (grid && grid.destroy) { try { grid.destroy(); } catch (e) { /* gone */ } } } };
  }
  T.pages.private_markets = { render: render };
  T.marketPlugins.PRIVATE = { render: function (el, ctx) { return render(el, ctx); }, ticketKind: 'PRIVATE',
                              dockHint: function () {
                                var rk = T.keys && T.keys.labels ? T.keys.labels(['grid.buy', 'grid.sell']) : '';
                                return 'Private Markets: the negotiated ticket - ' + (rk ? 'press ' + rk + ' on a company' : 'use the b / s buttons of a company');
                              } };
})();
