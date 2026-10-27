/* Risk (TERMINAL_DESIGN.md s17.1 #/risk, s14; WP-12; Build 2 P11: BM-09, BM-27, BM-28, PO-18 and the owner's leverage ask of
 * 2026-10-21 18:29 "calculate gross notional against nav too ... prob going to leverage w TRS").  "Warnings only": nothing here
 * blocks a trade (funding, borrow and recall mechanics still apply).  GET /api/risk?scope= and GET /api/compliance?scope=; every
 * figure is the server's (risk.report on ONE mark snapshot, so it ties to Portfolio); a section the server omits is left out.
 *
 * Sections (built once; the 30 s refresh updates the figures in place, so sort, selection and scroll survive):
 *   tiles      NAV; gross exposure / NAV and gross NOTIONAL / NAV (x, the definitions on hover); long / short / net % of NAV;
 *              TRS notional / NAV; the book's equity delta in USD (stocks + index futures + TRS + options, BM-27); signed DV01;
 *              excess liquidity
 *   leverage   per book (all books), per account (against the account's own NAV; a prime-broker account's leverage at the dealer)
 *   exposure   by bucket; by class / by account / by currency (FX exposure)
 *   delta      delta and greeks by underlying (stock, options, index futures, TRS)
 *   dv01       DV01 per instrument with its tenor (bonds by a full reprice, rates futures, swaps) and by tenor bucket
 *   scenarios  P&L per shock; one plain sentence per scenario group below the table (BM-28)
 *   financing  liquidity, margin, maintenance, loans; margin headroom per account / PB; the prime brokers (financing on / off,
 *              limit, long market value, financed, headroom, OTC IM / VM, leverage) - #/risk?section=financing scrolls here
 *   otc        per prime broker: contracts, notional, signed DV01, IM, VM posted / received; every contract with a readable label
 *   counterparty exposure (banks, repo dealers, agent lenders, prime brokers, private-credit borrowers; assumed terms)
 *   private    the private / illiquid bucket (names without a price per share at cost, as NAV carries them) and the lock-ups
 *   warnings and compliance notices.
 * ASCII only; no framework.
 */
(function () {
  'use strict';
  var T = window.T;
  /* OTC-R1-06: by class / by account / by currency wrap to the width their figures need (the owner's 24,064,993.00 of OTC notional clipped the
     GROSS and % NAV columns of 'by class' in a third of the page at 1440 px): as many per row as fit at 520 px each */
  var CSS = '.risk .grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(520px,100%),1fr));gap:10px}' +
            '.risk .basis{color:var(--fg3);font-size:11px;margin:-4px 0 8px}.risk .notes div{margin:3px 0;line-height:1.4;white-space:normal}.risk .notes b{margin-right:6px}' +
            '.risk .sub2{display:block;font-size:10px;line-height:1.25;color:var(--fg3);max-width:300px;overflow:hidden;text-overflow:ellipsis}' +
            '.risk td{padding-top:2px;padding-bottom:2px}.risk .tile .v{font-size:15px}.risk .pnote{color:var(--fg3);font-size:11px;margin:4px 0;white-space:normal}' +
            '.risk h3{font-size:11px;letter-spacing:.5px;text-transform:uppercase;color:var(--fg3);margin:8px 0 4px;font-weight:500}';
  function ensureCss() { if (document.getElementById('risk-css')) { return; } var s = document.createElement('style'); s.id = 'risk-css'; s.textContent = CSS; document.head.appendChild(s); }
  /* a leverage multiple: 2 decimals from 1x, 4 below (a 1bn book's 0.0067x must not read 0.01x) */
  function lev(x) { if (x == null || x === '') { return 'n/a'; } var n = Math.abs(Number(x)); return T.fmt.num(x, n >= 1 || n === 0 ? 2 : 4) + 'x'; }
  function pct(x) { return x == null || x === '' ? 'n/a' : T.fmt.pct(x); }
  function money(name, label, title) {
    return { key: name, label: label || name.replace(/_/g, ' '), title_h: title, html: function (r) { return '<span class="' + T.sign(r, name) + '">' + T.esc(T.money(r, name)) + '</span>'; }, text: function (r) { return T.money(r, name); }, sort: function (r) { var n = T.num(r, name); return n == null ? '' : n; } };
  }
  function pctCol(name, label, title) { return { key: name, label: label, title_h: title, text: function (r) { return r[name] == null ? '' : pct(r[name]); }, sort: function (r) { return r[name] == null ? '' : Number(r[name]); } }; }
  function xCol(name, label, title) { return { key: name, label: label, title_h: title, text: function (r) { return r[name] == null ? 'n/a' : lev(r[name]); }, sort: function (r) { return r[name] == null ? '' : Number(r[name]); } }; }
  function nameOf(r, names) { var id = r.account_id; return r.account_name || r.name || (names && names[id]) || (T.accountName ? T.accountName(id) : id) || ''; }
  function sevBadge(w) { var s = String(w.severity || 'WARN'); return '<span class="badge ' + (s === 'WARN' ? 'amber' : 'blue') + '">' + T.esc(s) + '</span>'; }

  function render(el, route) {
    ensureCss();
    var query = (route && route.query) || {};
    var scope = T.store.get('risk.scope', 'ALL');
    var sel = T.h('select', { 'aria-label': 'risk scope' });
    function fill() {
      T.clear(sel);
      sel.appendChild(T.h('option', { value: 'ALL' }, 'this book (all its accounts)'));
      sel.appendChild(T.h('option', { value: 'ALL_BOOKS' }, 'all books'));
      (T.state.accounts || []).forEach(function (a) { if (a.kind !== 'TREASURY') { sel.appendChild(T.h('option', { value: a.account_id }, a.name || a.account_id)); } });
      sel.value = scope; if (sel.value !== scope) { scope = 'ALL'; sel.value = scope; }
    }
    fill(); sel.addEventListener('change', function () { scope = sel.value; T.store.set('risk.scope', scope); load(); });
    T.pageHead(el, 'Risk', 'warnings only - informs, never blocks', [sel]);
    var body = T.h('div', { class: 'risk' }); el.appendChild(body);
    var spin = T.h('div', { class: 'spin' }); body.appendChild(spin);
    var err = T.h('div', { class: 'error', hidden: true }); body.appendChild(err);
    var S = null; var dead = false; var scrolled = false; var names = {};

    function panel(id, title, sub) {
      var p = T.h('div', { class: 'panel', id: 'risk-' + id }, T.h('h2', null, title, sub ? T.h('small', null, sub) : null));
      return p;
    }
    function gridIn(parent, spec) { var g = T.h('div'); parent.appendChild(g); return T.components.grid.mount(g, Object.assign({ tools: false, rows: [], empty: 'loading ...' }, spec)); }
    function build() {
      S = {};
      S.tiles = T.h('div'); body.appendChild(S.tiles);
      S.basis = T.h('div', { class: 'basis' }); body.appendChild(S.basis);
      /* leverage */
      var pl = panel('leverage', 'Leverage against NAV', 'gross exposure and gross notional / NAV; per book, and per account against the account\'s own NAV (a prime-broker account = its leverage at the dealer)');
      S.lev = gridIn(pl, { rowKey: function (r) { return r.level + ':' + (r.account_id || r.lifetime_id); }, empty: 'no positions', columns: [
        { key: 'name', label: 'book / account', cls: 'l', html: function (r) { return (r.level === 'book' ? '<b>' + T.esc(r.name) + '</b> <span class="muted">book</span>' : T.esc(r.name || nameOf(r, names))) + '<span class="sub2">' + T.esc(r.level === 'book' ? (r.lifetime_id || '') : (r.account_id || '')) + '</span>'; }, text: function (r) { return r.name || ''; } },
        money('nav', 'NAV'), money('long', 'long'), money('short', 'short'), money('gross', 'gross'), money('gross_notional', 'gross notional'),
        xCol('gross_leverage', 'gross / NAV', 'gross exposure / NAV'), xCol('gross_notional_leverage', 'notional / NAV', 'gross notional / NAV'),
        pctCol('long_pct_nav', 'long %'), pctCol('short_pct_nav', 'short %'), pctCol('net_pct_nav', 'net %'), pctCol('trs_pct_nav', 'TRS / NAV', 'TRS underlying notional / NAV')] });
      S.levNote = T.h('div', { class: 'pnote' }); pl.appendChild(S.levNote);
      body.appendChild(pl);
      /* exposure */
      var pe = panel('exposure', 'Exposure by bucket', 'IRS / cross-currency swaps are shown at notional but are not in the headline gross');
      S.exp = gridIn(pe, { rowKey: function (r) { return r.bucket; }, empty: 'no exposure', columns: [{ key: 'bucket', label: 'bucket', cls: 'l' }, money('long'), money('short'), money('net'), money('gross'), pctCol('pct_nav', '% NAV')] });
      body.appendChild(pe);
      var three = T.h('div', { class: 'grid3' });
      var pc = panel('by-class', 'By class');
      S.cls = gridIn(pc, { rowKey: function (r) { return r.klass; }, empty: 'none', columns: [{ key: 'klass', label: 'class', cls: 'l' }, money('long'), money('short'), money('gross'), pctCol('pct_nav', '% NAV')] });
      var pa = panel('by-account', 'By account');
      S.acc = gridIn(pa, { rowKey: function (r) { return r.account_id; }, empty: 'none', columns: [{ key: 'name', label: 'account', cls: 'l', text: function (r) { return nameOf(r, names); } }, money('gross'), pctCol('pct_nav', '% NAV'), xCol('gross_leverage', 'x acct NAV', 'gross exposure / the account\'s own NAV')] });
      var pf = panel('by-currency', 'By currency (FX exposure)');
      S.ccy = gridIn(pf, { rowKey: function (r) { return r.currency; }, empty: 'USD only', columns: [{ key: 'currency', label: 'ccy', cls: 'l' }, { key: 'native', label: 'in currency', text: function (r) { return T.money(r, 'native'); } }, { key: 'rate', label: 'USD rate', text: function (r) { return r.rate == null ? 'no rate' : T.fmt.px(r.rate); } }, money('usd', 'USD'), pctCol('pct_nav', '% NAV')] });
      three.appendChild(pc); three.appendChild(pa); three.appendChild(pf); body.appendChild(three);
      /* delta and greeks by underlying */
      var pd = panel('delta', 'Delta and greeks by underlying', 'USD; options from the Cboe chain row or the option model');
      S.delta = gridIn(pd, { rowKey: function (r) { return r.underlying; }, empty: 'no equity delta', columns: [{ key: 'underlying', label: 'underlying', cls: 'l' }, money('stock', 'stock / ETF'), money('options', 'options'), money('futures', 'index futures'), money('trs', 'TRS'), money('total', 'total delta'),
        { key: 'contracts', label: 'option contracts', text: function (r) { return r.contracts ? T.fmt.qty(r.contracts) : ''; } }, money('gamma', 'gamma / 1 %'), money('vega', 'vega / pt'), money('theta', 'theta / day')] });
      S.deltaNote = T.h('div', { class: 'pnote' }); pd.appendChild(S.deltaNote);
      body.appendChild(pd);
      /* DV01 */
      var pv = panel('dv01', 'DV01', 'P&L for +1 bp in yields (signed: a long bond loses)');
      S.dv = gridIn(pv, { rowKey: function (r) { return r.account_id + ':' + r.instrument_id; }, empty: 'no rates exposure', columns: [
        { key: 'label', label: 'instrument', cls: 'l', html: function (r) { return T.esc(r.label || r.instrument_id) + '<span class="sub2">' + T.esc(r.account_name || r.account_id || '') + '</span>'; }, text: function (r) { return r.label || r.instrument_id; } },
        { key: 'kind', label: 'kind', cls: 'l' }, { key: 'qty', label: 'qty / face', text: function (r) { return T.fmt.qty(r.qty); } }, { key: 'tenor', label: 'tenor', cls: 'l', text: function (r) { return (r.tenor_years != null ? T.fmt.num(r.tenor_years, 2) + 'y . ' : '') + (r.tenor || ''); } },
        money('dv01', 'DV01'), { key: 'basis', label: 'how', cls: 'l', html: function (r) { return '<span class="sub2" title="' + T.esc(r.basis || '') + '">' + T.esc(r.basis || '') + '</span>'; }, text: function (r) { return r.basis || ''; } }] });
      pv.appendChild(T.h('h3', null, 'by tenor'));
      S.tenor = gridIn(pv, { rowKey: function (r) { return r.tenor; }, empty: 'none', columns: [{ key: 'tenor', label: 'tenor', cls: 'l' }, { key: 'n', label: 'positions', text: function (r) { return String(r.n); } }, money('dv01', 'DV01')] });
      body.appendChild(pv);
      /* scenarios */
      var ps = panel('scenarios', 'Scenarios', 'P&L of each shock on today\'s positions; no VaR in this build');
      S.scen = gridIn(ps, { rowKey: function (r) { return r.name; }, empty: 'none', columns: [{ key: 'name', label: 'scenario', cls: 'l' }, { key: 'shock', label: 'shock', cls: 'l' }, money('pnl', 'P&L'),
        { key: 'unpriced', label: 'not re-priced', cls: 'l', html: function (r) { var u = r.unpriced || []; return u.length ? '<span title="' + T.esc(u.join(', ')) + '">' + u.length + ' (' + T.esc(u.slice(0, 3).join(', ')) + (u.length > 3 ? ' ...' : '') + ')</span>' : ''; }, text: function (r) { return (r.unpriced || []).join(' '); } }] });
      S.scenNotes = T.h('div', { class: 'notes pnote' }); ps.appendChild(S.scenNotes);
      body.appendChild(ps);
      /* concentration */
      var pk = panel('concentration', 'Concentration', 'the largest exposures against the book\'s NAV (warning above 25 %)');
      S.conc = gridIn(pk, { rowKey: function (r) { return r.account_id + ':' + r.instrument_id; }, empty: 'no positions', columns: [
        { key: 'symbol', label: 'instrument', cls: 'l', html: function (r) { return T.esc(r.symbol || r.instrument_id) + '<span class="sub2">' + T.esc((r.name && r.name !== r.symbol ? r.name + ' . ' : '') + nameOf(r, names)) + '</span>'; }, text: function (r) { return r.symbol || r.instrument_id; } },
        money('mv', 'exposure'), pctCol('pct_nav', '% NAV'), { key: 'note', label: 'basis', cls: 'l', text: function (r) { return r.note || 'market value'; } }] });
      body.appendChild(pk);
      /* financing and liquidity */
      var pfin = panel('financing', 'Financing and liquidity', 'cash, margin and financing of the scope; prime brokers below');
      S.finKv = T.h('div'); pfin.appendChild(S.finKv);
      pfin.appendChild(T.h('h3', null, 'margin headroom per account / prime broker'));
      S.margin = gridIn(pfin, { rowKey: function (r) { return r.account_id; }, empty: 'no margin in use', columns: [{ key: 'name', label: 'account', cls: 'l', text: function (r) { return nameOf(r, names); } }, money('required', 'required'), money('posted', 'posted'), money('headroom', 'headroom'), { key: 'note', label: 'note', cls: 'l', html: function (r) { return '<span class="sub2" style="max-width:420px" title="' + T.esc(r.note || '') + '">' + T.esc(r.note || '') + '</span>'; }, text: function (r) { return r.note || ''; } }] });
      pfin.appendChild(T.h('h3', null, 'prime brokers'));
      S.pb = gridIn(pfin, { rowKey: function (r) { return r.account_id; }, empty: 'no prime-broker account in this scope', columns: [
        { key: 'name', label: 'prime broker', cls: 'l', html: function (r) { return T.esc(nameOf(r, names)) + '<span class="sub2">' + T.esc(r.account_id) + '</span>'; }, text: function (r) { return nameOf(r, names); } },
        { key: 'pb_financing', label: 'financing', cls: 'l', html: function (r) { return r.pb_financing ? '<span class="pos">on</span>' : '<span class="muted">off</span>'; }, text: function (r) { return r.pb_financing ? 'on' : 'off'; } },
        { key: 'limit', label: 'limit', cls: 'l', text: function (r) { return r.limit_text || T.money(r, 'pb_limit'); } }, money('long_mv', 'long MV'), money('financed', 'financed'),
        { key: 'headroom', label: 'headroom', title: function (r) { return (r.formula || '') + '\nafter what working orders already need: ' + T.money(r, 'headroom_after_committed'); }, html: function (r) { return T.esc(T.money(r, 'headroom')); }, text: function (r) { return T.money(r, 'headroom'); } },
        money('otc_im', 'OTC IM'), money('otc_vm_posted', 'VM posted'), money('otc_vm_received', 'VM received'), money('nav', 'PB NAV'),
        xCol('gross_leverage', 'gross / NAV'), xCol('gross_notional_leverage', 'notional / NAV'), pctCol('trs_pct_nav', 'TRS / NAV')] });
      body.appendChild(pfin);
      /* OTC per prime broker */
      var po = panel('otc', 'OTC contracts per prime broker', 'VM is collateral, not P&L; DV01 and exposure from the current (post-reset) terms');
      S.otcSum = gridIn(po, { rowKey: function (r) { return r.account_id; }, empty: 'no OTC contracts', columns: [{ key: 'name', label: 'prime broker', cls: 'l', text: function (r) { return nameOf(r, names); } }, { key: 'contracts', label: 'contracts', text: function (r) { return String(r.contracts); } },
        money('notional', 'notional'), { key: 'dv01', label: 'DV01', html: function (r) { return r.dv01 == null ? '<span class="muted" title="DV01 of some contract is unavailable">partial</span>' : '<span class="' + T.sign(r, 'dv01') + '">' + T.esc(T.money(r, 'dv01')) + '</span>'; }, text: function (r) { return r.dv01 == null ? '' : T.money(r, 'dv01'); } },
        money('mark', 'mark'), money('im', 'IM'), money('vm_posted', 'VM posted'), money('vm_received', 'VM received')] });
      S.otc = gridIn(po, { rowKey: function (r) { return r.contract_id; }, empty: 'no OTC contracts', columns: [
        { key: 'label', label: 'contract', cls: 'l', html: function (r) { return '<b>' + T.esc(r.label || r.contract_id) + '</b><span class="sub2">' + T.esc(r.contract_id + ' . ' + (r.pb_name || '')) + '</span>'; }, text: function (r) { return r.label || r.contract_id; } },
        { key: 'notional', label: 'notional', text: function (r) { return T.money(r, 'notional') + ' ' + (r.currency || ''); } },
        { key: 'dv01', label: 'DV01', html: function (r) { return r.dv01 == null ? '<span class="muted">n/a</span>' : '<span class="' + T.sign(r, 'dv01') + '">' + T.esc(T.money(r, 'dv01')) + '</span>'; }, text: function (r) { return r.dv01 == null ? '' : T.money(r, 'dv01'); } },
        money('mark', 'mark'), money('vm_balance', 'VM balance'), money('im', 'IM'), { key: 'margin_mode', label: 'margin', cls: 'l' }, { key: 'maturity_date', label: 'maturity', cls: 'l' },
        { key: 'next', label: 'next event', cls: 'l', text: function (r) { var n = r.next_event; return n ? [n.date, n.kind, n.text].filter(Boolean).join(' . ') : ''; } }] });
      body.appendChild(po);
      /* credit derivatives (PB CDX protection; shown when the book holds any) */
      S.cdx = panel('credit', 'Credit derivatives (CDX)', 'CS01 = the P&L for +1 bp of the index spread; spreads are a labelled proxy');
      S.cdx.hidden = true;
      S.cdxLine = T.h('div', { class: 'pnote' }); S.cdx.appendChild(S.cdxLine);
      S.cdxRows = gridIn(S.cdx, { rowKey: function (r) { return r.contract_id; }, empty: 'no CDX contracts', columns: [
        { key: 'label', label: 'contract', cls: 'l', html: function (r) { return '<b>' + T.esc(r.label || r.contract_id) + '</b><span class="sub2">' + T.esc(r.contract_id + ' . ' + nameOf(r, names)) + '</span>'; }, text: function (r) { return r.label || r.contract_id; } },
        money('notional', 'notional'), money('exposure', 'credit exposure'), money('cs01', 'CS01'),
        { key: 'mid_spread_bp', label: 'spread (bp)', text: function (r) { return r.mid_spread_bp == null ? 'n/a' : T.fmt.num(r.mid_spread_bp, 1) + (r.proxy_as_of ? ' (' + r.proxy_as_of + ')' : ''); } },
        money('value', 'value'), { key: 'note', label: 'note', cls: 'l', text: function (r) { return r.note || ''; } }] });
      S.cdxScen = gridIn(S.cdx, { rowKey: function (r) { return r.name; }, empty: 'none', columns: [{ key: 'name', label: 'spread scenario', cls: 'l' }, money('pnl', 'P&L')] });
      S.cdxNote = T.h('div', { class: 'pnote' }); S.cdx.appendChild(S.cdxNote);
      body.appendChild(S.cdx);
      /* counterparties */
      var pcp = panel('counterparties', 'Exposure by counterparty', 'per book; institutions on assumed, editable terms');
      S.cp = gridIn(pcp, { rowKey: function (r) { return (r.lifetime_id || '') + ':' + (r.counterparty_id || r.name); }, empty: 'no counterparty exposure', columns: [
        { key: 'name', label: 'counterparty', cls: 'l', html: function (r) { return T.esc(r.label || r.name || '') + '<span class="sub2">' + T.esc(r.type_label || r.type || '') + '</span>'; }, text: function (r) { return r.label || r.name || ''; } },
        { key: 'products', label: 'products', cls: 'l', html: function (r) { var p = (r.products || []).map(function (x) { return x.product + (x.count > 1 ? ' x' + x.count : ''); }).join(', '); return '<span class="sub2" title="' + T.esc((r.products || []).map(function (x) { return (x.items || []).join('; '); }).join('; ')) + '">' + T.esc(p) + '</span>'; }, text: function (r) { return (r.products || []).map(function (x) { return x.product; }).join(', '); } },
        money('exposure', 'exposure'), money('collateral', 'collateral'), money('net', 'net'),
        { key: 'missing', label: 'not priced', cls: 'l', html: function (r) { var m = r.missing || []; return m.length ? '<span class="neg" title="' + T.esc(m.join('\n')) + '">' + m.length + '</span>' : ''; }, text: function (r) { return (r.missing || []).length ? String(r.missing.length) : ''; } }],
        footer: function () { var t = (S.cpTotals || {}); return { name: '<b>total</b>', exposure: T.esc(T.money(t, 'exposure')), collateral: T.esc(T.money(t, 'collateral')), net: T.esc(T.money(t, 'net')) }; } });
      S.cpNote = T.h('div', { class: 'pnote' }); pcp.appendChild(S.cpNote);
      body.appendChild(pcp);
      /* private / illiquid */
      var pp = panel('private', 'Private / illiquid', 'not shocked; names without a price per share at cost, as NAV carries them');
      S.priv = gridIn(pp, { rowKey: function (r) { return r.account_id + ':' + r.instrument_id; }, empty: 'no private positions', columns: [
        { key: 'name', label: 'company / loan', cls: 'l', html: function (r) { return T.esc(r.name || r.instrument_id) + '<span class="sub2">' + T.esc(r.instrument_id + ' . ' + nameOf(r, names)) + '</span>'; }, text: function (r) { return r.name || r.instrument_id; } },
        { key: 'qty', label: 'qty', text: function (r) { return T.fmt.qty(r.qty); } }, money('mv', 'value'), { key: 'basis', label: 'basis', cls: 'l', text: function (r) { return r.basis || ''; } }, pctCol('pct_nav', '% NAV'),
        { key: 'lock_until', label: 'lock-up until', cls: 'l', text: function (r) { return r.lock_until || ''; } }] });
      S.privNote = T.h('div', { class: 'pnote' }); pp.appendChild(S.privNote);
      body.appendChild(pp);
      /* warnings, compliance */
      var pw = panel('warnings', 'Risk warnings', 'amber = warning, blue = information; none blocks');
      S.warn = gridIn(pw, { empty: 'no risk warnings', columns: [{ key: 'severity', label: 'severity', cls: 'l', html: sevBadge, text: function (w) { return w.severity || ''; } }, { key: 'code', label: 'code', cls: 'l' },
        { key: 'message', label: 'message', cls: 'l', html: function (w) { return '<span style="white-space:normal">' + T.esc(w.message || '') + '</span>'; }, text: function (w) { return w.message || ''; } }, { key: 'account', label: 'account', cls: 'l', text: function (w) { return w.account_id ? nameOf(w, names) : ''; } }] });
      body.appendChild(pw);
      var pn = panel('compliance', 'Compliance notices', 'inform only; each names its rule and threshold - verify the current rule text');
      S.compErr = T.h('div', { class: 'error', hidden: true }); pn.appendChild(S.compErr);
      S.comp = gridIn(pn, { empty: 'no notices', columns: [{ key: 'rule', label: 'rule', cls: 'l' }, { key: 'status', label: 'status', cls: 'l', html: function (n) { return '<span class="badge blue">' + T.esc(n.status || '') + '</span>'; }, text: function (n) { return n.status || ''; } },
        { key: 'threshold', label: 'threshold', cls: 'l', text: function (n) { return n.threshold == null ? '' : String(n.threshold); } }, { key: 'message', label: 'message', cls: 'l', html: function (n) { return '<span style="white-space:normal">' + T.esc(n.message || '') + '</span>'; }, text: function (n) { return n.message || ''; } }] });
      body.appendChild(pn);
      body.appendChild(T.convNote(['EXCESS_LIQ', 'ENCUMBRANCE', 'FUT_MARGIN', 'OPT_MARGIN']));
    }

    function tiles(r) {
      var def = r.definitions || {};
      var d = r.delta || {};
      var scen = (r.scenarios || []).filter(function (s) { return s.name === 'rates +100 bp'; })[0];
      var list = [
        { k: 'NAV', v: T.money(r, 'nav'), s: r.book_nav != null && r.book_nav !== r.nav ? 'book NAV ' + T.money(r, 'book_nav') : (r.partial ? (r.missing || []).length + ' position(s) without a price, carried at cost' : 'marks: ' + (r.quality || 'n/a')), title: r.pct_basis || '' },
        { k: 'gross exposure / NAV', v: lev(r.gross_leverage), s: 'gross ' + T.money(r, 'gross'), title: def.gross_exposure || '' },
        { k: 'gross notional / NAV', v: lev(r.gross_notional_leverage), s: 'notional ' + T.money(r, 'gross_notional') + ((r.notional_missing || []).length ? ' (' + r.notional_missing.length + ' without a price)' : ''), title: def.gross_notional || '' },
        { k: 'long / NAV', v: pct(r.long_pct_nav), s: 'long ' + T.money(r, 'long'), title: def.long || '' },
        { k: 'short / NAV', v: pct(r.short_pct_nav), s: 'short ' + T.money(r, 'short'), title: def.short || '' },
        { k: 'net / NAV', v: pct(r.net_pct_nav), cls: T.sign(r, 'net'), s: 'net ' + T.money(r, 'net'), title: def.net || '' },
        { k: 'TRS notional / NAV', v: pct(r.trs_pct_nav), s: 'TRS ' + T.money(r, 'trs_notional'), title: def.trs || '' },
        { k: 'equity delta (USD)', v: T.money(d, 'total'), cls: T.sign(d, 'total'), s: 'stock ' + T.money(d, 'equities') + ' . options ' + T.money(d, 'options') + ' . index fut ' + T.money(d, 'index_futures') + ' . TRS ' + T.money(d, 'trs'), title: d.basis || def.delta || '' },
        { k: 'DV01 (per +1 bp)', v: T.money(r, 'dv01_signed'), cls: T.sign(r, 'dv01_signed'), s: scen ? '+100 bp: ' + T.money(scen, 'pnl') : '', title: def.dv01 || '' },
        { k: 'excess liquidity', v: T.money(r, 'excess_liquidity'), s: 'encumbrance ' + T.money(r, 'encumbrance') }];
      var box = T.tiles(list);
      S.tiles.parentNode.replaceChild(box, S.tiles); S.tiles = box;
    }
    function finKv(r) {
      var f = r.financing_liquidity || {}; var fm = f.formulas || {};
      var kv = T.kv([
        ['Treasury available', T.money(f, 'treasury_available')], ['accounts available', T.money(f, 'accounts_available')],
        ['excess liquidity', T.money(f, 'excess_liquidity'), fm.excess_liquidity || ''], ['encumbrance', T.money(f, 'encumbrance'), fm.encumbrance || ''],
        ['futures initial margin required / posted', T.money(f, 'futures_im_required') + ' / ' + T.money(f, 'futures_im_posted')],
        ['futures maintenance required / shortfall', T.money(f, 'maintenance_required') + ' / ' + T.money(f, 'maintenance_shortfall')],
        ['margin excess', T.money(f, 'margin_excess'), fm.margin_excess || ''], ['short-option margin (reserved)', T.money(f, 'options_margin_required')],
        ['collateral at lenders (shorts)', T.money(f, 'collateral_posted')], ['OTC initial margin', T.money(f, 'otc_im')],
        ['OTC VM posted / received', T.money(f, 'otc_vm_posted') + ' / ' + T.money(f, 'otc_vm_received')],
        ['currency loans / Treasury capital', T.money(f, 'loans') + ' / ' + T.money(f, 'treasury_capital'), fm.loans || '']]);
      T.clear(S.finKv); S.finKv.appendChild(kv);
    }
    function paint(r, c) {
      names = r.account_names || {};
      tiles(r);
      S.basis.textContent = [r.pct_basis ? 'percentages: ' + r.pct_basis : '', r.note || ''].filter(Boolean).join(' . ');
      var levRows = [];
      if ((r.by_book || []).length > 1 || r.scope === 'ALL_BOOKS') { (r.by_book || []).forEach(function (b) { levRows.push(Object.assign({ level: 'book' }, b)); }); }
      (r.leverage_by_account || []).forEach(function (a) { levRows.push(Object.assign({ level: 'account' }, a)); });
      S.lev.update(levRows, { empty: 'no positions in this scope' });
      S.levNote.textContent = ((r.definitions || {}).gross_notional || '') + ((r.notional_missing || []).length ? ' Without a price (left out of the notional): ' + r.notional_missing.join(', ') + '.' : '');
      S.exp.update(r.exposures || [], { empty: 'no exposure' });
      var cls = r.exposure_by_class || {};
      S.cls.update(Object.keys(cls).map(function (k) { return Object.assign({ klass: k }, cls[k]); }), { empty: 'none' });
      var acc = r.exposure_by_account || {};
      S.acc.update(Object.keys(acc).map(function (k) { return Object.assign({ account_id: k }, acc[k]); }), { empty: 'none' });
      S.ccy.update(r.fx_exposure || [], { empty: 'USD only' });
      S.delta.update(r.delta_by_underlying || [], { empty: 'no equity delta' });
      var g = r.greeks || {}; var dl = r.delta || {};
      S.deltaNote.textContent = (dl.basis || '') + ' Other futures (rates, energy, metals, grains, FX) at notional: ' + T.money(dl, 'other_futures') + '.' +
        ((g.unavailable || []).length ? ' Greeks unavailable: ' + g.unavailable.map(function (u) { return u.instrument_id + ' (' + u.reason + ')'; }).join('; ') + '.' : '');
      S.dv.update(r.dv01_rows || [], { empty: 'no rates exposure' });
      S.tenor.update(r.dv01_by_tenor || [], { empty: 'none' });
      S.scen.update(r.scenarios || [], { empty: 'none' });
      T.clear(S.scenNotes);
      (r.scenario_notes || []).forEach(function (n) { S.scenNotes.appendChild(T.h('div', null, T.h('b', null, n.title || n.group), n.text || '')); });
      S.conc.update(r.concentration || [], { empty: 'no positions' });
      finKv(r);
      S.margin.update(r.margin || [], { empty: 'no margin in use' });
      S.pb.update(((r.financing_liquidity || {}).pb) || [], { empty: 'no prime-broker account in this scope' });
      var otc = r.otc || []; var rows = [];
      otc.forEach(function (o) { (o.contracts_detail || []).forEach(function (cd) { rows.push(Object.assign({ pb_name: o.name || nameOf(o, names) }, cd)); }); });
      S.otcSum.update(otc, { empty: 'no OTC contracts' });
      S.otc.update(rows, { empty: 'no OTC contracts' });
      var cd = r.credit_derivatives || null;
      S.cdx.hidden = !cd;
      if (cd) {
        S.cdxLine.textContent = cd.error ? 'could not be measured: ' + cd.error : 'CS01 ' + T.money(cd, 'cs01') + ' . protection bought (short credit) ' + T.money(cd, 'short_credit') + ' . sold (long credit) ' + T.money(cd, 'long_credit') +
          ' . gross ' + (cd.gross_pct_nav != null ? pct(cd.gross_pct_nav) : 'n/a') + ' of NAV' + ((cd.missing || []).length ? ' . not valued: ' + cd.missing.join(', ') : '');
        S.cdxRows.update(cd.rows || [], { empty: 'no CDX contracts' });
        S.cdxScen.update(cd.scenarios || [], { empty: 'none' });
        S.cdxNote.textContent = cd.note || '';
      }
      var cp = r.counterparties || {};
      S.cpTotals = cp.totals || null;
      S.cp.update(cp.rows || [], { empty: cp.available === false ? (cp.note || 'the counterparty directory is not loaded') : 'no counterparty exposure' });
      S.cpNote.textContent = (cp.note || '') + ((cp.errors || []).length ? ' Could not be measured: ' + cp.errors.join('; ') : '');
      var pv = r.private || {};
      S.priv.update(pv.positions || [], { empty: 'no private positions' });
      S.privNote.textContent = 'bucket ' + (pv.pct_nav != null ? pct(pv.pct_nav) + ' of NAV' : 'n/a') + ' (' + T.money(pv, 'mv') + (T.num(pv, 'at_cost') ? ', of which at cost ' + T.money(pv, 'at_cost') : '') + ') . ' + (pv.note || '') +
        ((pv.lockups || []).length ? ' . lock-ups: ' + pv.lockups.map(function (l) { return l.name + ' ' + T.fmt.qty(l.qty) + ' until ' + l.lock_until; }).join('; ') : '');
      S.warn.update(r.warnings || [], { empty: 'no risk warnings' });
      if (c && c.error) { S.compErr.hidden = false; S.compErr.textContent = 'compliance unavailable: ' + (c.error.message || c.error); S.comp.update([], { empty: '' }); }
      else { S.compErr.hidden = true; S.comp.update(c ? (Array.isArray(c) ? c : (c.notices || [])) : [], { empty: 'no notices' }); }
      if (!scrolled && query.section) {
        scrolled = true;
        var tgt = document.getElementById('risk-' + String(query.section).replace(/[^a-z0-9-]/gi, ''));
        if (tgt) { try { tgt.scrollIntoView({ block: 'start' }); } catch (e) { tgt.scrollIntoView(); } }
      }
    }
    function load() {
      var want = scope;
      return Promise.all([T.api.get('/api/risk', { scope: want }), T.api.get('/api/compliance', { scope: want }).then(null, function (e) { return { error: e }; })]).then(function (res) {
        if (dead || want !== scope) { return; }
        if (spin.parentNode) { spin.parentNode.removeChild(spin); }
        err.hidden = true;
        if (!S) { build(); }
        paint(res[0] || {}, res[1]);
      }, function (e) {
        if (dead) { return; }
        if (spin.parentNode) { spin.parentNode.removeChild(spin); }
        err.hidden = false; err.textContent = T.api.fail(e, 'risk');
      });
    }
    load();
    T.poll.page('risk', 30000, function () { return S ? load() : Promise.resolve(); });
    var off = T.state.on('accounts', fill);
    T.pages.risk.unmount = function () { dead = true; off(); T.pages.risk.unmount = null; };
  }
  T.pages.risk = { render: render, unmount: null };
})();
