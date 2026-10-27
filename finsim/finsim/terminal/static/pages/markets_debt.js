/* Debt Instruments marketplace (BUILD2_PLAN P07; owner 2026-10-21: "no bonds in debt instruments available??? thought this
 * was suppose to be a market place").  Registers T.marketPlugins.DEBT (C11): pages/markets.js hands it the Markets body of
 * a Debt Instruments account.
 *
 * Tabs (GET /api/debt/markets?account_id=&tab=):
 * - Treasuries: EVERY outstanding marketable US Treasury (fiscaldata MSPD + auctions; ids UST-<CUSIP>), grouped by type with
 *   the filter chips All / Bills / Notes / Bonds / TIPS / FRNs; the search box filters by CUSIP, name (coupon, maturity) or
 *   type - no Yahoo look-up.  Columns: CUSIP, trade (b / s; LIMITED rows say why), type, coupon, maturity, price (with
 *   its freshness chip: source, as-of and inputs on hover), yield, spread (bp to the par curve), rating, accrued, DV01
 *   (USD per 1,000,000 face), position, name.
 * - Corporates: the GENERIC corporates (one per rating bucket x tenor; no real issuer), priced par curve + the bucket's ICE
 *   BofA OAS from FRED; a bucket without an OAS print is a grey row "unavailable" with the reason (FRED in maintenance).
 * - Curve: the Treasury par curve (and the real TIPS curve) charted on a canvas, today's Yahoo shifts, the benchmark
 *   Treasury of each tenor with its yield against the curve, SOFR (NY Fed when FRED is down), the ECB AAA / JGB curves and
 *   the OECD monthly 10-year yields.
 * b / s (buttons or keys) on a tradable row open the bond ticket (components/bondticket.js, T.ticketKinds.BOND); on a
 * LIMITED / unavailable row they say why (OA-33).  Enter opens the instrument page.  The page reloads every 60 s and
 * updates the grid in place (filter, chips, sort, selection and scroll are kept).  Every figure is the server's; nothing is
 * computed here.  ASCII only, no framework.
 */
(function () {
  'use strict';
  var T = window.T;
  var TABS = [{ key: 'treasuries', label: 'Treasuries' }, { key: 'corporates', label: 'Corporates' }, { key: 'curve', label: 'Curve' }];
  var TYPES = [['ALL', 'All'], ['BILL', 'Bills'], ['NOTE', 'Notes'], ['BOND', 'Bonds'], ['TIPS', 'TIPS'], ['FRN', 'FRNs']];
  var TYPE_SHORT = { BILL: 'bill', NOTE: 'note', BOND: 'bond', TIPS: 'TIPS', FRN: 'FRN', CORP: 'corp', GOV: 'govt', LEGACY: 'old' };
  var GROUP = { BILL: 'Bills (zero coupon, bond-equivalent yield)', NOTE: 'Notes (2-10 years at issue)', BOND: 'Bonds (20-30 years at issue)',
                TIPS: 'TIPS - LIMITED: real price, quotes only', FRN: 'Floating-rate notes - LIMITED: quotes only', LEGACY: 'Earlier rows still held' };
  var CSS = [
    '.debt .bar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:0 0 8px}.debt .bar .chip{cursor:pointer}.debt .bar .chip.on{color:var(--accent);border-color:var(--accent)}',
    '.debt .status{font-family:var(--mono);font-size:10.5px;color:var(--fg3);margin:2px 0 6px}.debt .status b{color:var(--muted);font-weight:600}.debt .status .bad{color:var(--warn)}',
    '.debt .nm{display:inline-block;max-width:210px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle}',
    '.debt .why{color:var(--warn);font-size:10px;font-family:var(--mono)}.debt tr.unavail td{color:var(--fg3)}',
    '.debt .curve-wrap{position:relative}.debt canvas.yc{width:100%;height:260px;display:block;background:var(--bg);border:1px solid var(--line);border-radius:4px}',
    '.debt .legend{display:flex;gap:12px;flex-wrap:wrap;font-family:var(--mono);font-size:10.5px;color:var(--muted);margin:4px 0}.debt .legend i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:4px}',
    '.debt .cols2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}@media (max-width:1250px){.debt .cols2{grid-template-columns:minmax(0,1fr)}}',
    '@media (max-width:1300px){.debt td.c-name,.debt th[data-key=name],.debt td.c-type,.debt th[data-key=type],.debt td.c-rating,.debt th[data-key=rating]{display:none}}'
  ].join('');
  var cssDone = false;
  function ensureCss() { if (cssDone || document.getElementById('debt-css')) { cssDone = true; return; } cssDone = true; var s = T.h('style', { id: 'debt-css' }); s.textContent = CSS; document.head.appendChild(s); }
  function num(x, dp) { return x == null || x === '' ? '' : T.fmt.num(x, dp); }
  function pct(x, dp) { return x == null || x === '' ? '' : T.fmt.num(x, dp == null ? 3 : dp) + ' %'; }
  function posQty(r) { var p = r.position; if (p == null || p === '') { return null; } p = Number(p); return isFinite(p) && p !== 0 ? p : null; }
  function tradable(r) { return r && r.support === 'FULL' && !r.unavailable && r.price != null; }
  function whyNot(r) {
    if (!r) { return 'no row'; }
    if (r.unavailable) { return r.reason || 'unavailable'; }
    if (r.support !== 'FULL') { return (r.limited_reason || ('LIMITED: ' + r.symbol)); }
    if (r.price == null) { return (r.mark && r.mark.note) || r.note || 'no price yet: nothing is dealt without one'; }
    return r.why_not || '';
  }
  function couponText(r) {
    if (r.type === 'BILL') { return 'disc.'; }
    if (r.type === 'FRN') { return 'float'; }
    return r.coupon_pct == null ? '' : T.fmt.num(r.coupon_pct, 3);
  }
  function columns(kind) {
    var cols = [
      { key: 'symbol', label: kind === 'corp' ? 'bucket' : 'CUSIP', cls: 'l', html: function (r) {
        var tag = r.unavailable ? ' <span class="state-tag LIMITED" title="' + T.esc(r.reason || '') + '">N/A</span>' : (r.support && r.support !== 'FULL' ? ' <span class="state-tag LIMITED" title="' + T.esc(r.limited_reason || '') + '">LIMITED</span>' : '');
        return '<b>' + T.esc(r.symbol || r.instrument_id) + '</b>' + tag;
      }, text: function (r) { return (r.symbol || '') + ' ' + (r.cusip || '') + ' ' + (r.type || ''); }, sort: function (r) { return r.symbol || ''; } },
      { key: 'act', label: 'trade', cls: 'l', html: function (r) {
        if (!tradable(r)) { return '<span class="why" title="' + T.esc(whyNot(r)) + '">' + (r.unavailable ? 'n/a' : (r.support !== 'FULL' ? 'quotes only' : 'no price')) + '</span>'; }
        return '<button class="btn small act" data-act="b" title="BUY (b)">b</button><button class="btn small act" data-act="s" title="SELL (s)">s</button>';
      }, text: function () { return ''; } },
      { key: 'type', label: 'type', cls: 'l c-type', text: function (r) { return TYPE_SHORT[r.type] || r.type || ''; } },
      { key: 'coupon', label: 'coupon', text: function (r) { return couponText(r); }, sort: function (r) { return r.coupon_pct == null ? '' : Number(r.coupon_pct); } },
      { key: 'maturity', label: 'maturity', cls: 'l', text: function (r) { return r.maturity || ''; } },
      { key: 'price', label: 'price', html: function (r) { return r.unavailable ? '<span class="muted">-</span>' : T.pxHtml(r.mark); },
        text: function (r) { return r.price == null ? '' : String(r.price); }, sort: function (r) { return r.price == null ? '' : Number(r.price); } },
      { key: 'yield', label: 'yield', html: function (r) {
        if (r.yield_pct == null) { return ''; }
        var t = r.type === 'BILL' ? 'bond-equivalent yield' + (r.discount_rate_pct ? '; discount rate ' + T.fmt.num(r.discount_rate_pct, 3) + ' %' : '') : 'yield to maturity (semi-annual street convention)';
        if (r.type === 'TIPS') { t = 'REAL yield (TIPS)'; }
        return '<span title="' + T.esc(t) + '">' + T.esc(pct(r.yield_pct)) + '</span>';
      }, text: function (r) { return r.yield_pct == null ? '' : String(r.yield_pct); }, sort: function (r) { return r.yield_pct == null ? '' : Number(r.yield_pct); } },
      { key: 'spread', label: kind === 'corp' ? 'OAS' : 'spread', html: function (r) { return r.spread_bp == null ? '' : '<span title="' + T.esc(r.spread_basis || '') + '">' + T.esc(T.fmt.num(r.spread_bp, 1)) + '</span>'; },
        text: function (r) { return r.spread_bp == null ? '' : String(r.spread_bp); }, sort: function (r) { return r.spread_bp == null ? '' : Number(r.spread_bp); } },
      { key: 'rating', label: 'rating', cls: 'l c-rating', text: function (r) { return r.rating || ''; } },
      { key: 'accrued', label: 'accrued', html: function (r) { return r.accrued == null ? '' : '<span title="accrued interest per 100 face today (ACT/ACT)">' + T.esc(T.fmt.num(r.accrued, 4)) + '</span>'; },
        text: function (r) { return r.accrued == null ? '' : String(r.accrued); } },
      { key: 'dv01', label: 'DV01/1M', html: function (r) { return r.dv01_per_1m == null ? '' : '<span title="value change in ' + T.esc(r.currency || 'USD') + ' of 1,000,000 face for 1 bp of yield">' + T.esc(T.fmt.num(r.dv01_per_1m, 0)) + '</span>'; },
        text: function (r) { return r.dv01_per_1m == null ? '' : String(r.dv01_per_1m); }, sort: function (r) { return r.dv01_per_1m == null ? '' : Number(r.dv01_per_1m); } },
      { key: 'position', label: 'position', html: function (r) { var p = posQty(r); return p == null ? '' : '<span class="' + (p > 0 ? 'pos' : 'neg') + '" title="face held in this account">' + T.esc(T.fmt.qty(p)) + '</span>'; },
        text: function (r) { var p = posQty(r); return p == null ? '' : String(p); } },
      { key: 'name', label: 'name', cls: 'l c-name', html: function (r) { return '<span class="nm" title="' + T.esc((r.name || '') + (r.unavailable ? ' - ' + (r.reason || '') : '')) + '">' + T.esc(r.name || '') + '</span>'; }, text: function (r) { return r.name || ''; } }
    ];
    if (kind === 'corp') { cols = cols.filter(function (c) { return c.key !== 'type'; }); }
    return cols;
  }
  /* the corporates tab: listed generic rows + one grey row per bucket / tenor that cannot be listed (its reason) */
  function corpRows(d) {
    var rows = (d.rows || []).slice();
    (d.missing || []).forEach(function (m) {
      rows.push({ instrument_id: 'NA-' + m.bucket + '-' + m.tenor, symbol: m.bucket + ' ' + m.tenor, name: 'Generic ' + m.bucket + '-rated USD corporate ' + m.tenor + ' (generic: curve + ICE BofA ' + m.bucket + ' OAS)',
                 type: 'CORP', rating: m.bucket, unavailable: true, reason: m.reason + (m.series ? ' (' + m.series + ')' : ''), support: 'LIMITED', mark: null, price: null });
    });
    var order = ['AAA', 'AA', 'A', 'BBB', 'BB', 'B', 'CCC'];
    rows.sort(function (a, b) { var ra = order.indexOf(a.rating), rb = order.indexOf(b.rating); if (ra !== rb) { return ra - rb; } return (a.years || parseFloat(String(a.symbol).split(' ')[1]) || 0) - (b.years || parseFloat(String(b.symbol).split(' ')[1]) || 0); });
    return rows;
  }
  /* OA-23: a Yahoo yield index typed where a bond is expected is explained, never opened as a tradable row */
  var YIELD_TEXT = '^IRX / ^FVX / ^TNX / ^TYX are the Yahoo Treasury YIELD indices (the 13-week, 5-, 10- and 30-year yields, in percent), not ' +
    'securities: they move these bond prices during the day (Curve tab). To trade, pick a Treasury bill, note or bond of that maturity here.';
  T.debtYieldText = YIELD_TEXT;
  function statusLine(d) {
    var st = d.status || {}; var m = st.master || {}; var parts = [];
    parts.push('<b>list</b> ' + (m.record_date ? 'fiscaldata MSPD ' + T.esc(m.record_date) + ' + auctions' : '<span class="bad">not fetched yet</span>') + (m.count ? ' (' + T.esc(m.count) + ' securities)' : ''));
    parts.push('<b>prices</b> ' + (st.fedinvest_date ? 'TreasuryDirect FedInvest ' + T.esc(st.fedinvest_date) : '<span class="bad">no FedInvest prices yet</span>') + ', moved by today\'s Yahoo yields when older');
    var f = d.fred || {};
    parts.push(f.maintenance ? '<span class="bad"><b>FRED unavailable (maintenance)</b></span> - OAS / OECD series keep their last values; SOFR from the NY Fed' : '<b>FRED</b> ok');
    var s = (d.sessions || {}).US_BONDS;
    if (s) { parts.push('<b>US bonds</b> ' + (s.open ? 'open 08:00-17:00 ET' : 'closed - reopens 08:00 ET' + (s.next_open ? ' ' + T.esc(T.fmt.dow(s.next_open) + ' ' + T.fmt.date(s.next_open)) : ''))); }
    return parts.join(' &middot; ');
  }
  function render(el, ctx) {
    ensureCss();
    var acct = ctx.account_id;
    var tab = TABS.some(function (t) { return t.key === ctx.tab; }) ? ctx.tab : 'treasuries';
    var root = T.h('div', { class: 'debt' }); el.appendChild(root);
    var tabBox = T.h('div'); var status = T.h('div', { class: 'status' }); var err = T.h('div', { class: 'error', hidden: true }); var body = T.h('div');
    root.appendChild(tabBox); root.appendChild(status); root.appendChild(err); root.appendChild(body);
    var dead = false, data = null, grid = null, ui = null, typeSel = T.store.get('debt.type', 'ALL');
    function paintTabs() {
      var counts = {}; (data && data.tabs || []).forEach(function (t) { counts[t.key] = t.count; });
      T.clear(tabBox).appendChild(T.tabs(TABS.map(function (t) { return { key: t.key, label: t.label + (counts[t.key] != null ? ' (' + counts[t.key] + ')' : '') }; }), tab, function (k) { ctx.setTab(k); }));
    }
    function rowsFor() {
      if (!data) { return []; }
      if (tab === 'corporates') { return corpRows(data); }
      var rows = data.rows || [];
      return typeSel === 'ALL' ? rows : rows.filter(function (r) { return r.type === typeSel; });
    }
    function openTicket(r, side) {
      if (!tradable(r)) { T.ui.toast((r.symbol || r.instrument_id) + ': ' + whyNot(r), 'err', 5000); return; }
      T.ticket.open({ account_id: acct, instrument_id: r.instrument_id, kind: 'BOND', side: side });
    }
    function mountGrid(kind) {
      var yh = T.h('div', { class: 'hint', hidden: true }, YIELD_TEXT); body.appendChild(yh);
      var gEl = T.h('div'); body.appendChild(gEl);
      grid = T.components.grid.mount(gEl, {
        columns: columns(kind), rows: rowsFor(), focus: true, search: true, csv: 'debt-' + tab + '.csv',
        empty: (data && data.empty) || (tab === 'corporates' ? 'no generic corporate can be priced yet' : 'no Treasuries listed yet'),
        rowKey: function (r) { return r.instrument_id; },
        rowClass: function (r) { return r.unavailable ? 'unavail' : ''; },
        groupBy: kind === 'ust' ? function (r) { return GROUP[r.type] || r.type; } : (kind === 'corp' ? function (r) { return (['AAA', 'AA', 'A', 'BBB'].indexOf(r.rating) >= 0 ? 'Investment grade' : 'High yield') + ' - generic, no real issuer'; } : null),
        onEnter: function (r) { if (!r.unavailable) { T.router.go(T.router.instrument(acct, r.instrument_id)); } },
        onKey: function (k, r) { if (k === 'b' || k === 's') { openTicket(r, k === 'b' ? 'BUY' : 'SELL'); return true; } return false; },
        onClick: function (r, e) { var b = e.target.closest('button[data-act]'); if (b) { openTicket(r, b.dataset.act === 'b' ? 'BUY' : 'SELL'); } }
      });
      var fi = gEl.querySelector('input[type=search]');
      if (fi) { fi.placeholder = 'search CUSIP, coupon, maturity'; fi.addEventListener('input', function () { yh.hidden = String(fi.value || '').trim().charAt(0) !== '^'; }); }
    }
    function typeBar() {
      var bar = T.h('div', { class: 'bar' });
      var counts = (data && data.types) || {};
      TYPES.forEach(function (t) {
        var n = t[0] === 'ALL' ? (data.rows || []).length : (counts[t[0]] || 0);
        var c = T.h('span', { class: 'chip' + (typeSel === t[0] ? ' on' : ''), role: 'button', tabindex: '0', title: 'show ' + t[1].toLowerCase() }, t[1] + ' ' + n);
        c.addEventListener('click', function () { typeSel = t[0]; T.store.set('debt.type', typeSel); T.qa('.chip', bar).forEach(function (x) { x.classList.remove('on'); }); c.classList.add('on'); if (grid) { grid.update(rowsFor()); } });
        bar.appendChild(c);
      });
      var sk = T.keys.label('app.search'), rk = T.keys.labels(['grid.buy', 'grid.sell']);   /* the keys in force (Settings > Keyboard) */
      bar.appendChild(T.h('span', { class: 'hint' }, 'search: CUSIP, coupon or maturity in the filter box' + (sk ? ' (' + sk + ')' : '') + '; ' +
        (rk ? rk + ' on a row opens' : 'the b / s buttons of a row open') + ' the bond ticket'));
      return bar;
    }
    function paint() {
      T.clear(body); grid = null;
      ui = { tab: tab };
      if (tab === 'treasuries') { body.appendChild(typeBar()); mountGrid('ust'); }
      else if (tab === 'corporates') {
        var oas = data.oas || {};
        var line = Object.keys(oas).filter(function (k) { return k !== 'HY'; }).map(function (k) { var o = oas[k] || {}; return k + ' ' + (o.value != null ? T.fmt.num(Number(o.value) * 100, 0) + ' bp (' + o.as_of + ')' : 'n/a'); }).join(' . ');
        body.appendChild(T.h('div', { class: 'hint' }, 'ICE BofA option-adjusted spreads (FRED): ' + (line || 'n/a') + '. Each generic bond: coupon = model par yield at listing rounded to 1/8 %, priced on the live par curve + its bucket OAS; no real issuer, no static spread.'));
        mountGrid('corp');
      } else { body.appendChild(curvePanel(data.curve || {})); }
      body.appendChild(T.h('div', { class: 'hint' }, data.note || ''));
    }
    function load() {
      return T.api.get('/api/debt/markets', { account_id: acct, tab: tab }).then(function (d) {
        if (dead) { return; }
        data = d; err.hidden = true;
        if (T.rememberKinds) { T.rememberKinds(d.rows); }
        status.innerHTML = statusLine(d);
        paintTabs();
        if (!ui || ui.tab !== tab || tab === 'curve') { paint(); } else if (grid) { grid.update(rowsFor()); }
      }, function (e) {
        if (dead) { return; }
        err.hidden = false; err.textContent = 'refresh failed: ' + (T.api.describe ? T.api.describe(e) : (e.code + ' ' + e.message)) + (data ? ' - the rows keep their last prices, each with its own freshness' : '');
      });
    }
    paintTabs();
    status.textContent = 'loading the Treasury list ...';
    T.poll.page('debt', 60000, load);
    return { unmount: function () { dead = true; } };
  }

  /* ---------------------------------------------------------------- the Curve tab ------------------------------------ */
  function series(block) { return ((block && block.points) || []).map(function (p) { return { x: Number(p.years), y: Number(p.yield) }; }).filter(function (p) { return isFinite(p.x) && isFinite(p.y); }); }
  function cssVar(name, fb) { try { var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return v || fb; } catch (e) { return fb; } }
  function drawCurve(canvas, c) {
    var dpr = window.devicePixelRatio || 1; var w = canvas.clientWidth || 700, h = canvas.clientHeight || 260;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    var g = canvas.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
    var fg3 = cssVar('--fg3', '#5d6474'), line = cssVar('--line', '#232a37'), accent = cssVar('--accent', '#f0b429'), blue = cssVar('--blue', '#58a6ff'), up = cssVar('--up', '#3fb950');
    g.font = '10px ' + cssVar('--mono', 'monospace');
    var par = series(c.par), real = series(c.real);
    var bench = (c.benchmarks || []).filter(function (r) { return r.yield_pct != null && r.years != null; }).map(function (r) { return { x: Number(r.years), y: Number(r.yield_pct), t: r.tenor }; });
    var all = par.concat(real, bench);
    if (!all.length) { g.fillStyle = fg3; g.fillText('no curve fetched yet', 12, 20); return; }
    var padL = 44, padR = 12, padT = 12, padB = 24;
    function X(yrs) { return padL + (w - padL - padR) * Math.sqrt(Math.max(0, yrs)) / Math.sqrt(30.5); }    /* square-root axis: the short end readable */
    var lo = Math.min.apply(null, all.map(function (p) { return p.y; })), hi = Math.max.apply(null, all.map(function (p) { return p.y; }));
    if (hi - lo < 0.5) { hi += 0.25; lo -= 0.25; }
    var pad = (hi - lo) * 0.08; hi += pad; lo -= pad;
    function Y(v) { return padT + (h - padT - padB) * (hi - v) / (hi - lo); }
    g.strokeStyle = line; g.lineWidth = 1; g.fillStyle = fg3;
    [lo + pad, (lo + hi) / 2, hi - pad].forEach(function (v) { g.beginPath(); g.moveTo(padL, Y(v)); g.lineTo(w - padR, Y(v)); g.stroke(); g.fillText(v.toFixed(2) + '%', 4, Y(v) + 3); });
    [0.25, 1, 2, 5, 10, 20, 30].forEach(function (t) { var x = X(t); g.beginPath(); g.moveTo(x, padT); g.lineTo(x, h - padB); g.stroke(); g.fillText(t < 1 ? '3M' : t + 'Y', x - 8, h - 8); });
    function path(pts, color, dash) {
      if (pts.length < 2) { return; }
      g.save(); g.strokeStyle = color; g.lineWidth = 1.8; if (dash) { g.setLineDash(dash); }
      g.beginPath(); pts.slice().sort(function (a, b) { return a.x - b.x; }).forEach(function (p, i) { if (i === 0) { g.moveTo(X(p.x), Y(p.y)); } else { g.lineTo(X(p.x), Y(p.y)); } }); g.stroke(); g.restore();
    }
    path(par, accent); path(real, blue, [5, 4]);
    g.fillStyle = up; bench.forEach(function (p) { g.beginPath(); g.arc(X(p.x), Y(p.y), 3.2, 0, Math.PI * 2); g.fill(); });
  }
  function curvePanel(c) {
    var box = T.h('div');
    var par = c.par || {};
    var head = T.h('div', { class: 'panel' }, T.h('h2', null, 'US Treasury curve', T.h('small', null, par.as_of ? ' par curve as of ' + par.as_of + ' . ' + (par.source || '') : ' par curve not fetched yet')));
    var wrap = T.h('div', { class: 'curve-wrap' }); var cv = T.h('canvas', { class: 'yc', 'aria-label': 'yield curve chart' }); wrap.appendChild(cv); head.appendChild(wrap);
    head.appendChild(T.h('div', { class: 'legend' }, T.h('span', null, T.h('i', { style: 'background:var(--accent)' }), 'par yield curve (nominal)'),
      T.h('span', null, T.h('i', { style: 'background:var(--blue)' }), 'real yield curve (TIPS)' + ((c.real || {}).as_of ? ' ' + c.real.as_of : ' - not fetched')),
      T.h('span', null, T.h('i', { style: 'background:var(--up);height:6px;width:6px;border-radius:3px' }), 'benchmark Treasuries (FedInvest / computed yield)')));
    var chips = T.h('div', { class: 'row' });
    (par.points || []).forEach(function (p) { chips.appendChild(T.h('span', { class: 'chip', title: 'par yield ' + p.tenor }, p.tenor + ' ' + T.fmt.num(p.yield, 3) + ' %')); });
    chips.appendChild(T.qchip(par.quality || 'unavailable', 'curve quality: ' + (par.quality || 'unavailable')));
    head.appendChild(chips);
    var sh = (par.shifts || []).map(function (s) { return s.symbol + ' ' + (Number(s.shift_pct) >= 0 ? '+' : '') + T.fmt.num(Number(s.shift_pct) * 100, 1) + ' bp' + (s.quality ? ' (' + s.quality + ')' : ''); }).join(' . ');
    head.appendChild(T.h('div', { class: 'hint' }, 'today\'s Yahoo shifts: ' + (sh || 'none observed yet') + '. ' + (par.shift_note || '')));
    box.appendChild(head);
    setTimeout(function () { drawCurve(cv, c); }, 0);
    var onResize = function () { if (document.body.contains(cv)) { drawCurve(cv, c); } else { window.removeEventListener('resize', onResize); } };
    window.addEventListener('resize', onResize);
    var bp = T.h('div', { class: 'panel' }, T.h('h2', null, 'Benchmark Treasuries', T.h('small', null, ' the most recently issued security of each tenor bucket')));
    var gEl = T.h('div'); bp.appendChild(gEl); box.appendChild(bp);
    T.components.grid.mount(gEl, { tools: false, rows: c.benchmarks || [], empty: 'no Treasury list yet', rowKey: function (r) { return r.instrument_id; },
      columns: [{ key: 'tenor', label: 'tenor', cls: 'l', text: function (r) { return r.tenor; } }, { key: 'symbol', label: 'CUSIP', cls: 'l', text: function (r) { return r.symbol; } },
        { key: 'coupon', label: 'coupon', text: function (r) { return couponText(r); } }, { key: 'maturity', label: 'maturity', cls: 'l', text: function (r) { return r.maturity || ''; } },
        { key: 'price', label: 'price', html: function (r) { return T.pxHtml(r.mark); }, text: function (r) { return r.price || ''; } },
        { key: 'yield', label: 'yield', text: function (r) { return pct(r.yield_pct); } }, { key: 'spread', label: 'vs curve (bp)', text: function (r) { return r.spread_bp == null ? '' : T.fmt.num(r.spread_bp, 1); } },
        { key: 'name', label: 'name', cls: 'l c-name', html: function (r) { return '<span class="nm" title="' + T.esc(r.name || '') + '">' + T.esc(r.name || '') + '</span>'; }, text: function (r) { return r.name || ''; } }],
      onEnter: function (r) { T.router.go(T.router.instrument((T.router.current || {}).account || '', r.instrument_id)); } });
    var two = T.h('div', { class: 'cols2' });
    var rates = T.h('div', { class: 'panel' }, T.h('h2', null, 'Rates and other curves'));
    var s = c.sofr || {}; var f = c.fred || {};
    var kv = [['SOFR', s.value != null ? T.fmt.ratePct(s.value) + ' (' + s.as_of + ')' : 'n/a', s.source || ''], ['FRED', f.maintenance ? 'unavailable (maintenance)' : 'ok', f.note || '']];
    [['eur_aaa', 'EUR AAA govt par (Bund proxy)'], ['jgb', 'JGB (MOF)'], ['real', 'US real (TIPS)']].forEach(function (k) {
      var b = c[k[0]] || {}; var p10 = (b.points || []).filter(function (p) { return p.tenor === '10Y'; })[0];
      kv.push([k[1], b.as_of ? (p10 ? '10Y ' + T.fmt.num(p10.yield, 3) + ' % ' : '') + '(' + b.as_of + ')' : 'not fetched yet', b.source || '']);
    });
    var o = c.oecd_10y || {};
    Object.keys(o).forEach(function (cc) { var x = o[cc] || {}; kv.push([cc + ' 10Y (OECD monthly)', x.value != null ? T.fmt.ratePct(x.value) + ' (' + x.as_of + ')' : 'n/a - ' + (f.maintenance ? 'FRED unavailable (maintenance)' : 'not fetched yet'), x.series || '']); });
    rates.appendChild(T.kv(kv));
    two.appendChild(rates);
    var fx = T.h('div', { class: 'panel' }, T.h('h2', null, 'Foreign government benchmarks'), T.h('div', { class: 'hint' }, 'Bund / JGB generic benchmarks trade in the Foreign Markets & FX account (settle T+2 in EUR / JPY); gilts, OATs and BTPs are LIMITED (monthly OECD averages only).'));
    two.appendChild(fx);
    box.appendChild(two);
    return box;
  }

  T.marketPlugins.DEBT = { render: render, tabs: TABS, ticketKind: 'BOND', dockHint: function () {
    var rk = T.keys && T.keys.labels ? T.keys.labels(['grid.buy', 'grid.sell']) : '';
    return 'Debt Instruments: pick a bond' + (rk ? ' and press ' + rk : ' with its b / s buttons') + ' (or type a CUSIP in the ticket)';
  } };
  T.pages.markets_debt = { render: render, columns: columns, curvePanel: curvePanel, drawCurve: drawCurve, tradable: tradable, whyNot: whyNot };
})();
