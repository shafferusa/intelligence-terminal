/* Foreign Markets & FX marketplace (BUILD2_PLAN P06; owner 2026-10-21: "FX Forwards needs to be in FX and why is there [a] NOT IN
 * THIS BUILD - foreign listings and foreign bonds ... FX spot deals from the Cash & financing page ... cmon, get this built").
 * Registers T.marketPlugins.FOREIGN_FX (C11): pages/markets.js hands it the Markets body of a Foreign Markets & FX account.
 *
 * Tabs:
 * - FX spot (GET /api/fx/pairs): the 25 USD pairs and the major crosses (EURJPY, EURGBP, EURCHF, GBPJPY ...): bid / mid / ask
 *   (the deal rates are the mid -/+ 0.2 pip), the mid's freshness chip (a cross shows its own print when fresh, else the mid
 *   derived from its two USD pairs, labelled), the spot value date and the account's net position in the pair's currency.
 *   b / s (buttons or keys) open the FX desk (components/fxdesk.js, T.ticketKinds.FX) to buy / sell the base currency.
 * - FX forwards (GET /api/fx/forwards?pair=): the tenor grid ON (spot-next) / 1W / 1M / 2M / 3M / 6M / 9M / 1Y and a broken
 *   date: value date, days from spot, the outright (covered interest parity, computed), forward points, the dealt bid / ask
 *   (outright -/+ the disclosed forward spread) and the two rates used with their series and as-of (monthly proxies LIMITED).
 *   b / s open the desk on that forward.  A currency without a free rate series has no forward price (said, never defaulted).
 * - Foreign listings (GET /api/fx/listings): every foreign listing this account trades, held / working / watched first: the
 *   price as Yahoo prints it with its unit ('GBp 2,700.00 (GBP 27.00)' for London), the venue session (open / next open),
 *   currency, venue, settlement, the venue tax (assumed table) and the position.  b / s open the order ticket; the preview
 *   shows the currency need and the USD conversion.  The box adds a foreign Yahoo symbol (e.g. VOD.L) to the watchlist.
 * - Foreign bonds: P07's bond list (T.components.bondlist, scope FOREIGN: Bund / JGB benchmarks, gilts / OATs / BTPs LIMITED).
 * - Currency positions (GET /api/fx/positions): per currency settled, pending FX deals, pending trade settlements, forwards by
 *   value date, net and its USD equivalent at the mid; the open deals with each forward's computed mark-to-market.
 * Every figure is the server's; nothing is computed here.  Grids fit 1100 px (secondary columns hide below 1300 px).  ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  var TABS = [{ key: 'fx', label: 'FX spot' }, { key: 'forwards', label: 'FX forwards' }, { key: 'listings', label: 'Foreign listings' },
              { key: 'bonds', label: 'Foreign bonds' }, { key: 'positions', label: 'Currency positions' }];
  var CSS = [
    '.fxm .status{font-family:var(--mono);font-size:10.5px;color:var(--fg3);margin:2px 0 6px}.fxm .status b{color:var(--muted);font-weight:600}.fxm .status .bad{color:var(--warn)}',
    '.fxm .tag{display:inline-block;font-size:9.5px;font-family:var(--mono);color:var(--fg3);border:1px solid var(--line);border-radius:3px;padding:0 3px;margin-left:4px;vertical-align:middle}',
    '.fxm .tag.lim{color:var(--warn);border-color:var(--warn)}.fxm .why{color:var(--warn);font-size:10px;font-family:var(--mono)}',
    '.fxm .nm{display:inline-block;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle}',
    '.fxm .chips{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0}.fxm .chips .chip{font-family:var(--mono);font-size:10.5px}',
    '.fxm .bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 8px}.fxm .bar select,.fxm .bar input{font-family:var(--mono)}',
    '.fxm .inputs{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px;margin:0 0 8px}@media (max-width:1250px){.fxm .inputs{grid-template-columns:minmax(0,1fr)}}',
    '.fxm .pos{color:var(--up)}.fxm .neg{color:var(--down)}.fxm .tot{font-family:var(--mono);font-size:12px;margin:6px 0}',
    '@media (max-width:1300px){.fxm td.c-name,.fxm th[data-key=name],.fxm td.c-venue,.fxm th[data-key=venue],.fxm td.c-basis,.fxm th[data-key=basis],.fxm td.c-chg,.fxm th[data-key=chg],' +
    '.fxm td.c-value,.fxm th[data-key=spot],.fxm td.c-ptr,.fxm th[data-key=pending_trades],.fxm td.c-rate,.fxm th[data-key=rate],.fxm td.c-state,.fxm th[data-key=state],' +
    '.fxm td.c-kind,.fxm th[data-key=kind],.fxm td.c-days,.fxm th[data-key=days],.fxm td.c-settle,.fxm th[data-key=settle]{display:none}}',
    '@media (max-width:1150px){.fxm td.c-pos,.fxm th[data-key=position],.fxm td.c-drate,.fxm th[data-key=drate],.fxm td.c-ccy,.fxm th[data-key=ccy],.fxm td.c-tax,.fxm th[data-key=tax],' +
    '.fxm td.c-fwd,.fxm th[data-key=forwards]{display:none}}'
  ].join('');
  var cssDone = false;
  function ensureCss() { if (cssDone || document.getElementById('fxm-css')) { cssDone = true; return; } cssDone = true; var s = T.h('style', { id: 'fxm-css' }); s.textContent = CSS; document.head.appendChild(s); }
  function money(x) { return x == null || x === '' ? '' : T.fmt.money(x, 2); }
  function signed(x) { if (x == null || x === '') { return ''; } var n = Number(x); return '<span class="' + (n > 0 ? 'pos' : (n < 0 ? 'neg' : '')) + '">' + T.esc(T.fmt.money(x, 2)) + '</span>'; }
  function chgPct(q) {
    if (!q || q.last == null || q.prev_close == null || Number(q.prev_close) === 0) { return null; }
    return (Number(q.last) / Number(q.prev_close) - 1) * 100;
  }
  function chgHtml(q) { var c = chgPct(q); if (c == null) { return ''; } return '<span class="' + (c > 0 ? 'pos' : (c < 0 ? 'neg' : '')) + '">' + (c > 0 ? '+' : '') + c.toFixed(2) + ' %</span>'; }
  function whenOpen(s) {
    if (!s) { return ''; }
    if (s.open) { return 'open' + (s.closes_at ? ' to ' + T.fmt.hm(s.closes_at) : ''); }
    return s.next_open ? 'opens ' + T.fmt.dow(s.next_open) + ' ' + T.fmt.hm(s.next_open) + ' ET' : 'closed';
  }
  function midRec(r) { return { mark: r.mid, quality: r.mid_quality || 'unavailable', quote_time: r.mid_quote_time, source: r.mid_source, executable: r.executable, note: r.mid_basis && r.mid_basis !== 'pair print' ? r.mid_basis : '' }; }
  function openDesk(acct, fields) { T.ticket.open(Object.assign({ account_id: acct, kind: 'FX' }, fields)); }

  /* ---------------------------------------------------------------- FX spot ---------------------------------------- */
  function spotTab(ctx, body, status, err) {
    var acct = ctx.account_id; var grid = null; var dead = false; var data = null;
    var chips = T.h('div', { class: 'chips' });
    var gEl = T.h('div');
    var rk = T.keys.labels(['grid.buy', 'grid.sell']);              /* the keys in force (Settings > Keyboard) */
    body.appendChild(T.h('div', { class: 'hint' }, (rk ? rk + ' on a pair opens' : 'the b / s buttons of a pair open') + ' the FX desk to buy / sell the first currency; the desk also deals any two currencies directly, spot or forward.' +
      ' A cross is quoted in its second currency per 1 of the first; its mid is its own print when fresh, else derived from the two USD pairs (labelled).'));
    body.appendChild(gEl);
    body.appendChild(T.h('h2', { style: 'margin:10px 0 2px' }, 'Currencies the desk deals'));
    body.appendChild(chips);
    body.appendChild(T.convNote(['FX_SPOT']));
    function cols() {
      return [
        { key: 'pair', label: 'pair', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol) + '</b>' + (r.cross ? '<span class="tag" title="cross: ' + T.esc(r.quote) + ' per 1 ' + T.esc(r.base) + '">cross</span>' : ''); },
          text: function (r) { return r.symbol + (r.cross ? ' cross' : '') + ' ' + r.base + ' ' + r.quote; }, sort: function (r) { return r.symbol; } },
        { key: 'act', label: 'trade', cls: 'l', html: function (r) { return '<button class="btn small act" data-act="b" title="BUY ' + T.esc(r.base) + ' (b)">b</button><button class="btn small act" data-act="s" title="SELL ' + T.esc(r.base) + ' (s)">s</button>'; }, text: function () { return ''; } },
        { key: 'bid', label: 'bid', html: function (r) { return r.bid == null ? '' : '<span title="you sell ' + T.esc(r.base) + ' at the mid - 0.2 pip">' + T.esc(T.fmt.px(r.bid)) + '</span>'; }, text: function (r) { return r.bid || ''; } },
        { key: 'mid', label: 'mid', html: function (r) { return T.pxHtml(midRec(r)); }, text: function (r) { return r.mid || ''; }, sort: function (r) { return r.mid == null ? '' : Number(r.mid); } },
        { key: 'ask', label: 'ask', html: function (r) { return r.ask == null ? '' : '<span title="you buy ' + T.esc(r.base) + ' at the mid + 0.2 pip">' + T.esc(T.fmt.px(r.ask)) + '</span>'; }, text: function (r) { return r.ask || ''; } },
        { key: 'chg', label: 'chg', cls: 'c-chg', html: function (r) { return chgHtml(r.quote_rec); }, text: function (r) { var c = chgPct(r.quote_rec); return c == null ? '' : c.toFixed(2); } },
        { key: 'basis', label: 'mid from', cls: 'l c-basis', html: function (r) { return r.mid_basis === 'pair print' ? '<span class="muted">print</span>' : (r.mid_basis ? '<span title="' + T.esc(r.mid_source || '') + '">derived</span>' : ''); }, text: function (r) { return r.mid_basis || ''; } },
        { key: 'spot', label: 'value', cls: 'l c-value', html: function (r) { return '<span title="spot value date ' + T.esc(r.spot_date || '') + ' (T+2; USD/CAD and USD/TRY T+1)">' + T.esc(String(r.spot_date || '').slice(5)) + '</span>'; }, text: function (r) { return r.spot_date || ''; } },
        { key: 'position', label: 'position', cls: 'c-pos', html: function (r) { return r.position == null || Number(r.position) === 0 ? '' : '<span title="net ' + T.esc(r.position_ccy) + ' in this account: settled + pending + forwards">' + T.esc(r.position_ccy) + ' ' + signed(r.position) + '</span>'; },
          text: function (r) { return r.position == null ? '' : String(r.position); }, sort: function (r) { return r.position == null ? 0 : Number(r.position); } },
        { key: 'name', label: 'name', cls: 'l c-name', html: function (r) { return '<span class="nm">' + T.esc(r.name || '') + '</span>'; }, text: function (r) { return r.name || ''; } }
      ];
    }
    function act(r, k) { if (!r) { return; } openDesk(acct, { instrument_id: r.instrument_id, side: k === 'b' ? 'BUY' : 'SELL', mode: 'SPOT' }); }
    function paintChips(d) {
      T.clear(chips);
      (d.currencies || []).forEach(function (c) {
        var t = c.ccy + (c.usd_rate ? ' ' + c.usd_rate : ' n/a');
        chips.appendChild(T.h('span', { class: 'chip', title: (c.name || c.ccy) + ': USD per 1 ' + c.ccy + (c.usd_rate ? ' ' + c.usd_rate + ' (' + c.quality + ')' : ' - no USD rate yet') + (c.forwards ? '; forwards priced (' + c.rate_series + (c.monthly_proxy ? ', monthly proxy' : '') + ')' : '; spot only (no free rate series for forwards)') }, t + (c.forwards ? '' : ' *')));
      });
      chips.appendChild(T.h('span', { class: 'hint' }, '* spot only: no free policy-rate series, so no forward price'));
    }
    function load() {
      return T.api.get('/api/fx/pairs', { account_id: acct }).then(function (d) {
        if (dead) { return; }
        data = d; err.hidden = true;
        var s = d.session || {};
        var sd = ((d.pairs || [])[0] || {}).spot_date;
        status.innerHTML = '<b>spot value</b> ' + T.esc(sd || 'n/a') + ' (T+2; USD/CAD, USD/TRY T+1) &middot; <b>FX session</b> ' + (s.open ? 'open (Sun 17:00 - Fri 17:00 ET)' : '<span class="bad">' + T.esc((s.phase || 'closed').toLowerCase()) + '</span>' + (s.next_open ? ' - reopens ' + T.esc(T.fmt.dow(s.next_open) + ' ' + T.fmt.hm(s.next_open)) + ' ET' : '')) +
          ' &middot; <b>trade date</b> ' + T.esc(d.trade_date || '') + ' &middot; <b>deal</b> mid -/+ ' + T.esc(d.spread_pips || '0.2') + ' pip &middot; ' + T.esc(d.note || '');
        if (T.rememberKinds) { T.rememberKinds((d.pairs || []).map(function (r) { return { instrument_id: r.instrument_id, kind: 'FX' }; })); }
        if (!grid) {
          grid = T.components.grid.mount(gEl, { columns: cols(), rows: d.pairs || [], focus: true, csv: 'fx-spot.csv', empty: 'no FX pairs in the master',
            rowKey: function (r) { return r.instrument_id; },
            groupBy: function (r) { return r.cross ? 'Crosses' : 'USD pairs'; },
            onEnter: function (r) { T.router.go(T.router.instrument(acct, r.instrument_id)); },
            onKey: function (k, r) { if (k === 'b' || k === 's') { act(r, k); return true; } return false; },
            onClick: function (r, e) { var b = e.target.closest('button[data-act]'); if (b) { act(r, b.dataset.act); } } });
        } else { grid.update(d.pairs || []); }
        paintChips(d);
      }, function (e) {
        if (dead) { return; }
        err.hidden = false; err.textContent = 'refresh failed: ' + T.api.describe(e) + (data ? ' - the rows keep their last prices, each with its own freshness' : '');
      });
    }
    T.poll.page('fxspot', 15000, load);
    return { unmount: function () { dead = true; } };
  }

  /* ---------------------------------------------------------------- FX forwards ------------------------------------ */
  function forwardsTab(ctx, body, status, err) {
    var acct = ctx.account_id; var grid = null; var dead = false; var data = null;
    var pair = T.store.get('fx.fwdpair', 'EURUSD');
    var sel = T.h('select', { 'aria-label': 'currency pair' }, T.h('option', { value: pair }, pair));
    var broken = T.h('input', { type: 'date', 'aria-label': 'broken value date', title: 'a broken value date: any business day after spot, up to the 1-year date' });
    var clear = T.h('button', { class: 'btn small', title: 'drop the broken date' }, 'x');
    var bar = T.h('div', { class: 'bar' }, T.h('label', null, 'pair'), sel, T.h('label', null, 'broken date'), broken, clear,
      T.h('span', { class: 'hint' }, (T.keys.labels(['grid.buy', 'grid.sell']) ? T.keys.labels(['grid.buy', 'grid.sell']) + ' on a row opens' : 'the b / s buttons of a row open') + ' the FX desk on that forward (buy / sell the first currency)'));
    var inputs = T.h('div', { class: 'inputs' });
    var gEl = T.h('div');
    var foot = T.h('div', { class: 'hint' });
    body.appendChild(bar); body.appendChild(inputs); body.appendChild(gEl); body.appendChild(foot);
    T.api.get('/api/fx/pairs', { account_id: acct }).then(function (d) {
      if (dead) { return; }
      T.clear(sel);
      (d.pairs || []).forEach(function (r) {
        var o = T.h('option', { value: r.instrument_id }, r.instrument_id + (r.forwards ? '' : '  (spot only)'));
        if (r.instrument_id === pair) { o.selected = true; }
        sel.appendChild(o);
      });
    }, function (e) { console.warn('fx pairs', e); });
    sel.addEventListener('change', function () { pair = sel.value; T.store.set('fx.fwdpair', pair); if (grid) { grid.destroy(); grid = null; T.clear(gEl); } load(); });
    broken.addEventListener('change', function () { load(); });
    clear.addEventListener('click', function () { broken.value = ''; load(); });
    function rateBox(label, r) {
      var box = T.h('div', { class: 'panel' }, T.h('h2', null, label));
      if (!r) { box.appendChild(T.h('div', { class: 'hint' }, 'n/a')); return box; }
      if (r.value_pct == null) { box.appendChild(T.h('div', { class: 'why' }, r.note || 'no rate')); return box; }
      box.appendChild(T.kv([['series', r.series + (r.limited ? ' ' : ''), r.source || ''], ['rate', T.fmt.ratePct(r.value_pct) + (r.limited ? '  LIMITED (monthly proxy)' : ''), r.label || ''],
        ['as of', r.as_of || 'n/a', 'the observation date of the rate'], ['source', r.source || 'n/a', ''], ['day count', 'ACT/' + r.basis, '']]));
      return box;
    }
    function cols() {
      return [
        { key: 'tenor', label: 'tenor', cls: 'l', html: function (r) { return '<b>' + T.esc(r.tenor === 'BROKEN' ? 'broken' : r.tenor) + '</b>' + (r.limited ? '<span class="tag lim" title="a monthly proxy rate is an input">LIMITED</span>' : ''); }, text: function (r) { return r.tenor; } },
        { key: 'act', label: 'trade', cls: 'l', html: function (r) { return r.ok ? '<button class="btn small act" data-act="b" title="BUY ' + T.esc(pair.slice(0, 3)) + ' forward (b)">b</button><button class="btn small act" data-act="s" title="SELL ' + T.esc(pair.slice(0, 3)) + ' forward (s)">s</button>' : '<span class="why" title="' + T.esc(r.note || '') + '">no price</span>'; }, text: function () { return ''; } },
        { key: 'value_date', label: 'value date', cls: 'l', text: function (r) { return r.value_date || ''; } },
        { key: 'days', label: 'days', cls: 'c-days', text: function (r) { return r.days == null ? '' : String(r.days); } },
        { key: 'outright', label: 'outright', html: function (r) { return r.outright == null ? '' : '<span title="' + T.esc(r.arithmetic || '') + '">' + T.esc(r.outright) + '</span>'; }, text: function (r) { return r.outright || ''; } },
        { key: 'points', label: 'points', html: function (r) { return r.points == null ? '' : signed(r.points); }, text: function (r) { return r.points || ''; } },
        { key: 'bid', label: 'bid', html: function (r) { return r.bid == null ? '' : '<span title="you sell ' + T.esc(pair.slice(0, 3)) + ' forward: outright - ' + T.esc(r.spread_pips) + ' pip">' + T.esc(r.bid) + '</span>'; }, text: function (r) { return r.bid || ''; } },
        { key: 'ask', label: 'ask', html: function (r) { return r.ask == null ? '' : '<span title="you buy ' + T.esc(pair.slice(0, 3)) + ' forward: outright + ' + T.esc(r.spread_pips) + ' pip">' + T.esc(r.ask) + '</span>'; }, text: function (r) { return r.ask || ''; } },
        { key: 'name', label: 'note', cls: 'l c-name', html: function (r) { return '<span class="nm" title="' + T.esc(r.note || '') + '">' + T.esc(r.ok ? (r.label || '') : (r.note || '')) + '</span>'; }, text: function (r) { return r.note || ''; } }
      ];
    }
    function act(r, k) {
      if (!r || !r.ok) { T.ui.toast(pair + ' ' + (r && r.tenor) + ': ' + ((r && r.note) || 'no forward price'), 'err', 4000); return; }
      var f = { instrument_id: pair, side: k === 'b' ? 'BUY' : 'SELL', mode: 'FORWARD' };
      if (r.tenor === 'BROKEN') { f.value_date = r.value_date; } else { f.tenor = r.tenor; }
      openDesk(acct, f);
    }
    function load() {
      return T.api.get('/api/fx/forwards', { pair: pair, value_date: broken.value || null }).then(function (d) {
        if (dead) { return; }
        data = d; err.hidden = true;
        var sp = d.spot || {};
        status.innerHTML = '<b>' + T.esc(d.pair) + '</b> spot ' + (sp.mid ? T.esc(sp.mid) + ' (' + T.esc(sp.quality || '') + ', ' + T.esc(sp.basis || '') + ')' : '<span class="bad">no spot rate</span>') +
          ' &middot; <b>trade date</b> ' + T.esc(d.trade_date) + ' &middot; <b>spot date</b> ' + T.esc(d.spot_date) + ' &middot; ' + T.esc(d.spread_text || '');
        T.clear(inputs);
        inputs.appendChild(rateBox(d.pair.slice(0, 3) + ' rate (base)', d.r_base));
        inputs.appendChild(rateBox(d.pair.slice(3) + ' rate (quote)', d.r_quote));
        var rows = (d.tenors || []).slice();
        if (d.broken) { rows.unshift(d.broken); }
        if (!grid) {
          grid = T.components.grid.mount(gEl, { columns: cols(), rows: rows, focus: true, csv: 'fx-forwards-' + pair + '.csv', empty: 'no tenors',
            rowKey: function (r) { return r.tenor + ':' + r.value_date; },
            onKey: function (k, r) { if (k === 'b' || k === 's') { act(r, k); return true; } return false; },
            onClick: function (r, e) { var b = e.target.closest('button[data-act]'); if (b) { act(r, b.dataset.act); } } });
        } else { grid.update(rows); }
        foot.textContent = (d.method || '') + '. Computed from observed inputs (never a dealer print); the deal rate adds the disclosed forward spread against you.';
      }, function (e) {
        if (dead) { return; }
        err.hidden = false; err.textContent = 'refresh failed: ' + T.api.describe(e);
      });
    }
    T.poll.page('fxfwd', 20000, load);
    return { unmount: function () { dead = true; } };
  }

  /* ---------------------------------------------------------------- Foreign listings -------------------------------- */
  function listingsTab(ctx, body, status, err) {
    var acct = ctx.account_id; var grid = null; var dead = false; var data = null;
    var box = T.h('input', { type: 'text', placeholder: 'add a foreign symbol (Yahoo code, e.g. VOD.L) - Enter', 'data-search': '1', style: 'width:300px', 'aria-label': 'add a foreign listing to the watchlist' });
    var spin = T.h('span', { class: 'spin', hidden: true });
    var gEl = T.h('div');
    var fees = T.h('div', { class: 'panel' });
    body.appendChild(T.h('div', { class: 'bar' }, box, spin));
    body.appendChild(gEl);
    body.appendChild(fees);
    box.addEventListener('keydown', function (e) { if (e.key === 'Enter') { addSymbol(); } });
    function addSymbol() {
      var sym = box.value.trim().toUpperCase(); if (!sym) { return; }
      spin.hidden = false;
      T.api.getReady('/api/instruments/resolve', { symbol: sym }).then(function (inst) {
        var id = inst.instrument_id || (inst.instrument && inst.instrument.instrument_id);
        if (!id) { spin.hidden = true; T.ui.toast('resolved, but no instrument id in the answer', 'err'); return null; }
        return T.api.post('/api/watchlists', { account_id: acct, name: 'default', instrument_id: id }).then(function () {
          box.value = ''; spin.hidden = true;
          T.ui.toast((inst.symbol || id) + ' added to the watchlist of ' + T.accountName(acct) + ' - listed first here', 'ok', 3000);
          return load();
        });
      }).then(null, function (e) { spin.hidden = true; T.api.fail(e, 'add ' + sym); });
    }
    function tradable(r) { return r && r.support === 'FULL'; }
    function cols() {
      return [
        { key: 'symbol', label: 'symbol', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol) + '</b>' + (r.support !== 'FULL' ? '<span class="tag lim" title="' + T.esc(r.limited_reason || '') + '">LIMITED</span>' : '') + (r.working ? '<span class="tag" title="an order is working">working</span>' : ''); },
          text: function (r) { return r.symbol + ' ' + (r.yahoo_symbol || ''); } },
        { key: 'act', label: 'trade', cls: 'l', html: function (r) { return tradable(r) ? '<button class="btn small act" data-act="b" title="BUY (b)">b</button><button class="btn small act" data-act="s" title="SELL (s)">s</button>' : '<span class="why" title="' + T.esc(r.limited_reason || '') + '">quotes only</span>'; }, text: function () { return ''; } },
        { key: 'last', label: 'last', html: function (r) {
          var q = r.quote || null;
          var rec = q ? Object.assign({}, q, { quote_unit: r.quote_unit, price_scale: r.price_scale, currency: r.currency }) : null;
          return T.pxHtml(rec, { unit: r.quote_unit || null, scale: r.quote_unit ? Number(r.price_scale) : null, ccy: r.currency, showMajor: !!r.quote_unit });
        }, text: function (r) { return r.quote && r.quote.last != null ? String(r.quote.last) : ''; }, sort: function (r) { return r.quote && r.quote.last != null ? Number(r.quote.last) : ''; } },
        { key: 'chg', label: 'chg', cls: 'c-chg', html: function (r) { return chgHtml(r.quote); }, text: function (r) { var c = chgPct(r.quote); return c == null ? '' : c.toFixed(2); } },
        { key: 'session', label: 'session', cls: 'l', html: function (r) { var s = r.session || {}; return '<span class="' + (s.open ? 'pos' : 'muted') + '" title="' + T.esc(s.note || '') + '">' + T.esc(whenOpen(s)) + '</span>'; }, text: function (r) { return (r.session || {}).phase || ''; } },
        { key: 'ccy', label: 'ccy', cls: 'l c-ccy', html: function (r) { return T.esc(r.currency) + (r.quote_unit ? ' <span class="muted" title="Yahoo quotes it in ' + T.esc(r.quote_unit) + '">(' + T.esc(r.quote_unit) + ')</span>' : ''); }, text: function (r) { return r.currency; } },
        { key: 'venue', label: 'venue', cls: 'l c-venue', html: function (r) { return '<span class="nm" style="max-width:150px">' + T.esc(r.venue || ('.' + (r.venue_suffix || ''))) + '</span>'; }, text: function (r) { return r.venue || ''; } },
        { key: 'settle', label: 'settle', cls: 'l c-settle', text: function (r) { return 'T+' + r.settle_days; } },
        { key: 'tax', label: 'venue tax', cls: 'l c-tax', html: function (r) { var t = r.venue_tax; if (!t) { return '<span class="muted">-</span>'; } if (t.exempt) { return '<span class="muted" title="' + T.esc(t.exempt) + '">exempt</span>'; } return '<span title="' + T.esc(t.label + ': ' + t.rule + ' (assumed - verify current rates)') + '">' + T.esc(t.rate_pct) + ' %</span>'; }, text: function (r) { return r.venue_tax ? r.venue_tax.rate_pct : ''; } },
        { key: 'position', label: 'position', cls: 'c-pos', html: function (r) { return r.position ? '<span class="' + (r.position > 0 ? 'pos' : 'neg') + '">' + T.esc(T.fmt.qty(r.position)) + '</span>' : ''; }, text: function (r) { return r.position ? String(r.position) : ''; } },
        { key: 'name', label: 'name', cls: 'l c-name', html: function (r) { return '<span class="nm" title="' + T.esc(r.name || '') + '">' + T.esc(r.name || '') + '</span>'; }, text: function (r) { return r.name || ''; } }
      ];
    }
    function act(r, k) {
      if (!r) { return; }
      if (!tradable(r)) { T.ui.toast(r.symbol + ': ' + (r.limited_reason || 'quotes only'), 'err', 5000); return; }
      T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, kind: r.kind || 'EQUITY', side: k === 'b' ? 'BUY' : 'SELL' });
    }
    function paintFees(d) {
      T.clear(fees).appendChild(T.h('h2', null, 'Venue taxes', T.h('small', null, ' assumed - verify current rates; shown as their own fee line in the order preview')));
      fees.appendChild(T.kv((d.venue_fees || []).map(function (f) { return [f.venue + (f.suffix && f.suffix !== '*' ? ' (.' + f.suffix + ')' : ''), f.tax + (f.rate_pct && f.rate_pct !== '0.00' ? ' ' + f.rate_pct + ' %' : ''), f.rule]; })));
    }
    function load() {
      return T.api.get('/api/fx/listings', { account_id: acct }).then(function (d) {
        if (dead) { return; }
        data = d; err.hidden = true;
        var rows = d.rows || [];
        var open = rows.filter(function (r) { return r.session && r.session.open; }).length;
        status.innerHTML = '<b>' + rows.length + '</b> foreign listings &middot; <b>' + open + '</b> venue(s) open now &middot; ' + T.esc(d.note || '');
        if (T.rememberKinds) { T.rememberKinds(rows); }
        if (!grid) {
          grid = T.components.grid.mount(gEl, { columns: cols(), rows: rows, focus: true, csv: 'foreign-listings.csv', empty: 'no foreign listings in the master',
            rowKey: function (r) { return r.instrument_id; },
            groupBy: function (r) { return r.fast ? 'Held, working or watched' : (r.venue || 'Other venues'); },
            onEnter: function (r) { T.router.go(T.router.instrument(acct, r.instrument_id)); },
            onKey: function (k, r) { if (k === 'b' || k === 's') { act(r, k); return true; } return false; },
            onClick: function (r, e) { var b = e.target.closest('button[data-act]'); if (b) { act(r, b.dataset.act); } } });
        } else { grid.update(rows); }
        paintFees(d);
      }, function (e) {
        if (dead) { return; }
        err.hidden = false; err.textContent = 'refresh failed: ' + T.api.describe(e) + (data ? ' - the rows keep their last prices' : '');
      });
    }
    T.poll.page('fxlistings', 30000, load);
    return { unmount: function () { dead = true; } };
  }

  /* ---------------------------------------------------------------- Foreign bonds (P07's list, C18 / C19) ------------- */
  function bondsTab(ctx, body, status) {
    status.textContent = 'Foreign government bonds trade in this account in their own currency (T+2), priced by the bond engine; the USD conversion is shown in the bond preview.';
    var c = T.components.bondlist;
    if (c && typeof c.render === 'function') {
      try { return c.render(body, { account_id: ctx.account_id, scope: 'FOREIGN' }) || null; } catch (e) { console.error('bondlist', e); body.appendChild(T.h('div', { class: 'error' }, 'the bond list failed to draw: ' + (e.message || e))); return null; }
    }
    body.appendChild(T.h('div', { class: 'hint' }, 'loading the bond list ...'));
    return null;
  }

  /* ---------------------------------------------------------------- Currency positions (OA-24) --------------------- */
  function positionsTab(ctx, body, status, err) {
    var acct = ctx.account_id; var dead = false; var g1 = null; var g2 = null;
    var tot = T.h('div', { class: 'tot' });
    var e1 = T.h('div'); var e2 = T.h('div');
    body.appendChild(tot); body.appendChild(e1);
    body.appendChild(T.h('h2', { style: 'margin:10px 0 2px' }, 'Open FX deals'));
    body.appendChild(e2);
    var note = T.h('div', { class: 'hint' }); body.appendChild(note);
    function fwdHtml(r) {
      if (!r.forwards || !r.forwards.length) { return ''; }
      var tip = r.forwards.map(function (f) { return f.value_date + ': ' + f.amount + ' (' + f.deal_id + ' vs ' + f.against + ' at ' + f.rate + (f.mtm != null ? '; MTM USD ' + f.mtm : '') + ')'; }).join('; ');
      return '<span title="forwards by value date (no cash until then): ' + T.esc(tip) + '">' + signed(r.forwards_net) + ' <span class="muted">' +
        T.esc(r.forwards.length === 1 ? String(r.forwards[0].value_date).slice(2) : r.forwards.length + ' dates') + '</span></span>';
    }
    function c1() {
      return [
        { key: 'currency', label: 'ccy', cls: 'l', html: function (r) { return '<b>' + T.esc(r.currency) + '</b>'; }, text: function (r) { return r.currency; } },
        { key: 'settled', label: 'settled', html: function (r) { return signed(r.settled); }, text: function (r) { return r.settled; } },
        { key: 'pending_fx', label: 'pend. FX', html: function (r) { return '<span title="spot deals not yet settled: in ' + T.esc(money(r.pending_fx_in)) + ', out ' + T.esc(money(r.pending_fx_out)) + '">' + signed(r.pending_fx) + '</span>'; }, text: function (r) { return r.pending_fx; } },
        { key: 'pending_trades', label: 'pending trades', cls: 'c-ptr', html: function (r) { return signed(r.pending_trades); }, text: function (r) { return r.pending_trades; } },
        { key: 'forwards', label: 'forwards', cls: 'l c-fwd', title: function () { return 'forwards by value date (no cash until then)'; }, html: fwdHtml, text: function (r) { return r.forwards_net; } },
        { key: 'net', label: 'net', html: function (r) { return '<b>' + signed(r.net) + '</b>'; }, text: function (r) { return r.net; } },
        { key: 'rate', label: 'USD per unit', cls: 'c-rate', html: function (r) { return r.usd_rate == null ? '<span class="why">no rate</span>' : T.pxHtml({ mark: r.usd_rate, quality: r.usd_rate_quality, quote_time: r.usd_rate_quote_time, source: r.usd_rate_pair || 'USD' }); }, text: function (r) { return r.usd_rate || ''; } },
        { key: 'usd', label: 'USD equiv.', html: function (r) { return r.usd_equiv == null ? '<span class="why">n/a</span>' : signed(r.usd_equiv); }, text: function (r) { return r.usd_equiv || ''; } }
      ];
    }
    function c2() {
      return [
        { key: 'deal', label: 'deal', cls: 'l', html: function (r) { return '<b>' + T.esc(r.deal_id) + '</b>' + (r.link ? '<span class="tag" title="' + T.esc(r.link.text || '') + '">conversion</span>' : ''); }, text: function (r) { return r.deal_id; } },
        { key: 'kind', label: 'kind', cls: 'l c-kind', text: function (r) { return r.kind === 'FORWARD' ? 'forward ' + (r.tenor || '') : 'spot'; } },
        { key: 'pair', label: 'pair', cls: 'l', text: function (r) { return r.pair; } },
        { key: 'buy', label: 'buy / sell', html: function (r) { return '+' + T.esc(r.buy_ccy + ' ' + money(r.buy)) + '<br>-' + T.esc(r.sell_ccy + ' ' + money(r.sell)); }, text: function (r) { return r.buy_ccy + ' ' + r.buy + ' / ' + r.sell_ccy + ' ' + r.sell; } },
        { key: 'drate', label: 'rate', cls: 'c-drate', text: function (r) { return r.rate_quoted || ''; } },
        { key: 'value', label: 'value date', cls: 'l', html: function (r) { return '<span title="' + T.esc(r.kind === 'FORWARD' && !r.posted ? 'forward ' + (r.tenor || '') + ': no cash until the value date' : 'settles on the value date') + '">' + T.esc(r.value_date) + (r.kind === 'FORWARD' ? ' F' : '') + '</span>'; }, text: function (r) { return r.value_date; } },
        { key: 'state', label: 'state', cls: 'l c-state', text: function (r) { return r.kind === 'FORWARD' && !r.posted ? 'no cash until value date' : 'settles on value date'; } },
        { key: 'mtm', label: 'MTM (USD)', html: function (r) { if (r.kind !== 'FORWARD' || r.posted) { return ''; } if (r.mtm == null) { return '<span class="why" title="' + T.esc(r.mtm_note || '') + '">n/a</span>'; } return signed(r.mtm) + ' ' + T.qchipHtml(r.mtm_quality || 'computed', 'mark-to-market: (F now - K) x amount x discount factor, covered interest parity from observed inputs; realised at the value date'); }, text: function (r) { return r.mtm || ''; } }
      ];
    }
    function load() {
      return T.api.get('/api/fx/positions', { account_id: acct }).then(function (d) {
        if (dead) { return; }
        err.hidden = true;
        status.textContent = 'Net currency positions of ' + T.accountName(acct) + ' at ' + T.fmt.hm(d.as_of) + ' ET';
        tot.innerHTML = 'USD equivalent of all currencies <b>' + signed(d.usd_equiv_total) + '</b>' + (d.usd_missing && d.usd_missing.length ? ' (no USD rate for ' + T.esc(d.usd_missing.join(', ')) + ')' : '') +
          ' &middot; forwards mark-to-market (computed) <b>' + signed(d.forwards_mtm) + '</b> USD';
        var rows = d.currencies || []; var deals = d.open_deals || [];
        if (!g1) { g1 = T.components.grid.mount(e1, { columns: c1(), rows: rows, csv: 'fx-positions.csv', empty: 'no currency balances, deals or forwards in this account yet', rowKey: function (r) { return r.currency; } }); } else { g1.update(rows); }
        if (!g2) { g2 = T.components.grid.mount(e2, { columns: c2(), rows: deals, csv: 'fx-open-deals.csv', empty: 'no open FX deals', rowKey: function (r) { return r.deal_id; } }); } else { g2.update(deals); }
        note.textContent = d.note || '';
      }, function (e) {
        if (dead) { return; }
        err.hidden = false; err.textContent = 'refresh failed: ' + T.api.describe(e);
      });
    }
    T.poll.page('fxpositions', 15000, load);
    return { unmount: function () { dead = true; } };
  }

  /* ---------------------------------------------------------------- the plug-in -------------------------------------- */
  var TAB_FN = { fx: spotTab, forwards: forwardsTab, listings: listingsTab, bonds: bondsTab, positions: positionsTab };
  function render(el, ctx) {
    ensureCss();
    var tab = TAB_FN[ctx.tab] ? ctx.tab : 'fx';
    var root = T.h('div', { class: 'fxm' }); el.appendChild(root);
    var tabBox = T.h('div'); var status = T.h('div', { class: 'status' }); var err = T.h('div', { class: 'error', role: 'status', hidden: true }); var body = T.h('div');
    root.appendChild(tabBox); root.appendChild(status); root.appendChild(err); root.appendChild(body);
    tabBox.appendChild(T.tabs(TABS, tab, function (k) { ctx.setTab(k); }));
    var handle = null;
    try { handle = TAB_FN[tab](ctx, body, status, err); } catch (e) { console.error('foreign markets tab', tab, e); body.appendChild(T.h('div', { class: 'error' }, 'this tab failed to draw: ' + (e.message || e))); }
    return { unmount: function () { if (handle && typeof handle.unmount === 'function') { try { handle.unmount(); } catch (e) { console.error(e); } } } };
  }

  T.marketPlugins.FOREIGN_FX = { render: render, tabs: TABS, ticketKind: 'FX',
                                 dockHint: function () {
                                   var rk = T.keys && T.keys.labels ? T.keys.labels(['grid.buy', 'grid.sell']) : '';
                                   return 'Foreign Markets & FX: the FX desk deals spot and forwards in any two currencies; ' + (rk ? rk + ' on a listing opens' : 'the b / s buttons of a listing open') + ' the order ticket';
                                 } };
  T.pages.markets_fx = { render: render, tabs: TABS };
})();
