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
 *   reg T      FINSIM1_PLAN P3 task 13: per account (US Exchange, the prime brokers) the Reg T requirement, maintenance, eligible
 *              collateral, excess or deficit, open calls and the assumed terms - P6's figures (collateral.summary) carried in the risk
 *              answer, "unavailable" with the reason while they are not in the build (no collateral route is called from here)
 *   sector     PORTFOLIO-19: the headline exposure per sector (a TRS counts in its underlying's sector; unclassified names said so)
 *   fx         FX_CCS_PLAN K7 (1.0.6.1): per currency the nominal and the PV delta, each the ledger part (balances, receivables / payables,
 *              positions) plus the OTC FX contracts' off-ledger part, with the USD effect of a 1 % and a 5 % move; the pair scenarios (each
 *              USD pair with the other held, each traded cross with either leg held - they replace the single +/-5 % USD row); rate01 per
 *              currency, basis01 per pair, the EUR OIS spread and points01; the prime brokers' counterparty lines (IM posted, VM, the
 *              exposure to a default); the liquidity ladder (scheduled flows, settlement-funding purchases and gaps); the notional limits
 *              ("none configured") - every figure MODEL, with its labels (PROXY / ASSUMED) and what could not be valued now
 *   warnings and compliance notices.
 * V104R2-BW-08: the exposure table marks the buckets that add up to the gross tile ("in gross"); the others are shown for their size.
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
        { key: 'name', label: 'book / account', cls: 'l', html: function (r) { return (r.level === 'book' ? '<b>' + T.esc(r.name) + '</b> <span class="muted">book</span>' : T.esc(r.name || nameOf(r, names))) + '<span class="sub2">' + T.esc(r.level === 'book' && r.lifetime_id ? 'lifetime ' + r.lifetime_id : '') + '</span>'; }, text: function (r) { return r.name || ''; } },
        money('nav', 'NAV'), money('long', 'long'), money('short', 'short'), money('gross', 'gross'), money('gross_notional', 'gross notional'),
        xCol('gross_leverage', 'gross / NAV', 'gross exposure / NAV'), xCol('gross_notional_leverage', 'notional / NAV', 'gross notional / NAV'),
        pctCol('long_pct_nav', 'long %'), pctCol('short_pct_nav', 'short %'), pctCol('net_pct_nav', 'net %'), pctCol('trs_pct_nav', 'TRS / NAV', 'TRS underlying notional / NAV')] });
      S.levNote = T.h('div', { class: 'pnote' }); pl.appendChild(S.levNote);
      body.appendChild(pl);
      /* exposure */
      var pe = panel('exposure', 'Exposure by bucket', 'the buckets marked "in gross" add up to the gross exposure tile; the others are shown for their size');
      S.exp = gridIn(pe, { rowKey: function (r) { return r.bucket; }, empty: 'no exposure', columns: [{ key: 'bucket', label: 'bucket', cls: 'l' }, money('long'), money('short'), money('net'), money('gross'), pctCol('pct_nav', '% NAV'),
        { key: 'headline', label: 'in gross', cls: 'l', html: function (r) { return r.headline ? '<span class="pos">yes</span>' : '<span class="muted" title="IRS / cross-currency swaps and CDX at notional, FX forwards and the currency rows: their measures are DV01, CS01 and the currency exposure">no</span>'; },
          text: function (r) { return r.headline ? 'yes' : 'no'; } }] });
      S.expNote = T.h('div', { class: 'pnote' }); pe.appendChild(S.expNote);
      body.appendChild(pe);
      /* PORTFOLIO-19: the headline exposure per sector */
      var psec = panel('sector', 'Exposure by sector', 'headline exposure; a TRS counts in its underlying\'s sector; classify a name from its Portfolio row');
      S.sec = gridIn(psec, { rowKey: function (r) { return r.sector; }, empty: 'no headline exposure', columns: [
        { key: 'sector', label: 'sector', cls: 'l', html: function (r) { return T.esc(r.sector) + '<span class="sub2" title="' + T.esc((r.instruments || []).join(', ')) + '">' + T.esc((r.instruments || []).slice(0, 6).join(', ') + ((r.instruments || []).length > 6 ? ' ...' : '')) + '</span>'; }, text: function (r) { return r.sector; } },
        money('long'), money('short'), money('net'), money('gross'), pctCol('pct_nav', '% NAV')] });
      body.appendChild(psec);
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
        { key: 'label', label: 'instrument', cls: 'l', html: function (r) { return T.esc(r.label || r.instrument_id) + '<span class="sub2">' + T.esc(r.account_id ? nameOf(r, names) : '') + '</span>'; }, text: function (r) { return r.label || r.instrument_id; } },
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
      /* FX_CCS_PLAN K7: the FX block */
      var pfx = panel('fx', 'Currency risk (FX)', 'the ledger part once, plus the OTC FX forwards\' and cross-currency swaps\' off-ledger part; MODEL numbers');
      S.fxNote = T.h('div', { class: 'pnote' }); pfx.appendChild(S.fxNote);
      pfx.appendChild(T.h('h3', null, 'by currency'));
      S.fxCcy = gridIn(pfx, { rowKey: function (r) { return r.ccy; }, empty: 'USD only', columns: [
        { key: 'ccy', label: 'ccy', cls: 'l' },
        { key: 'nominal', label: 'nominal', html: function (r) { return '<span class="' + T.sign(r, 'nominal') + '">' + T.esc(T.money(r, 'nominal')) + '</span><span class="sub2">ledger ' + T.esc(T.money(r, 'nominal_ledger')) + ' . OTC ' + T.esc(T.money(r, 'nominal_hooks')) + '</span>'; },
          text: function (r) { return T.money(r, 'nominal'); }, sort: function (r) { return T.num(r, 'nominal') || 0; } },
        { key: 'pv_delta', label: 'PV delta', html: function (r) { return r.pv_delta == null ? '<span class="muted" title="' + T.esc((r.partial || []).join(', ')) + '">unavailable now</span>'
          : '<span class="' + T.sign(r, 'pv_delta') + '">' + T.esc(T.money(r, 'pv_delta')) + '</span><span class="sub2">ledger ' + T.esc(T.money(r, 'pv_delta_ledger')) + ' . OTC ' + T.esc(r.pv_delta_hooks == null ? 'n/a' : T.money(r, 'pv_delta_hooks')) + '</span>'; },
          text: function (r) { return r.pv_delta == null ? '' : T.money(r, 'pv_delta'); }, sort: function (r) { return T.num(r, 'pv_delta') || 0; } },
        { key: 'rate', label: 'USD rate', text: function (r) { return r.rate == null ? 'no rate' : T.fmt.px(r.rate); } },
        money('usd_per_1pct', 'USD per +1 %', 'the USD effect of the currency rising 1 % against USD, on the PV delta'), money('usd_per_5pct', 'USD per +5 %')] });
      pfx.appendChild(T.h('h3', null, 'pair moves'));
      S.fxPairs = gridIn(pfx, { rowKey: function (r) { return r.pair + ':' + r.held + ':' + r.shock; }, empty: 'no currency exposure', columns: [
        { key: 'pair', label: 'pair', cls: 'l', text: function (r) { return String(r.pair || '').slice(0, 3) + '/' + String(r.pair || '').slice(3); } }, { key: 'shock', label: 'move', cls: 'l' },
        { key: 'held', label: 'held', cls: 'l', text: function (r) { return String(r.held || '').replace(/\b([A-Z]{3})([A-Z]{3})\b/g, '$1/$2'); } },
        { key: 'pnl', label: 'P&L', html: function (r) { return r.pnl == null ? '<span class="muted" title="' + T.esc(r.note || '') + '">unavailable</span>' : '<span class="' + T.sign(r, 'pnl') + '">' + T.esc(T.money(r, 'pnl')) + '</span>'; },
          text: function (r) { return r.pnl == null ? '' : T.money(r, 'pnl'); }, sort: function (r) { return T.num(r, 'pnl') || 0; } }] });
      pfx.appendChild(T.h('h3', null, 'rates, basis, spread and points (per +1 bp / +1 pip)'));
      S.fxSens = gridIn(pfx, { rowKey: function (r) { return r.what + ':' + r.key; }, empty: 'no OTC FX contract', columns: [
        { key: 'what', label: 'measure', cls: 'l' }, { key: 'key', label: 'currency / pair', cls: 'l' }, money('usd', 'USD'),
        { key: 'contracts', label: 'contracts', cls: 'l', html: function (r) { return '<span class="sub2">' + T.esc(r.contracts || '') + '</span>'; }, text: function (r) { return r.contracts || ''; } }] });
      pfx.appendChild(T.h('h3', null, 'counterparty and collateral'));
      S.fxCp = gridIn(pfx, { rowKey: function (r) { return r.account_id; }, empty: 'no OTC contracts', columns: [
        { key: 'name', label: 'prime broker', cls: 'l', text: function (r) { return nameOf(r, names); } }, { key: 'contracts', label: 'contracts', text: function (r) { return String(r.contracts == null ? '' : r.contracts); } },
        money('mtm', 'contracts value'), money('csa_exposure', 'CSA value'), money('im_posted', 'IM posted'), money('vm_posted', 'VM posted'), money('vm_received', 'VM received'),
        { key: 'exposure', label: 'exposure to a default', title_h: 'IM posted (assumed not segregated, ASSUMED) + whatever the VM posted exceeds what you owe on the contracts',
          html: function (r) { return '<span title="' + T.esc(r.basis || '') + '">' + T.esc(T.money(r, 'exposure')) + '</span>'; }, text: function (r) { return T.money(r, 'exposure'); } }] });
      pfx.appendChild(T.h('h3', null, 'liquidity ladder'));
      S.fxLadder = gridIn(pfx, { rowKey: function (r) { return [r.date, r.ccy, r.kind, r.contract_id || '', r.what || ''].join(':'); }, empty: 'nothing scheduled', columns: [
        { key: 'date', label: 'date', cls: 'l' }, { key: 'kind', label: 'kind', cls: 'l', text: function (r) { return { flow: 'scheduled flow', purchase: 'settlement funding', gap: 'funding gap' }[r.kind] || r.kind || ''; } },
        { key: 'ccy', label: 'ccy', cls: 'l' }, money('amount', 'amount', '+ received / - paid'),
        { key: 'what', label: 'what', cls: 'l', html: function (r) { return '<span class="sub2" style="max-width:520px" title="' + T.esc(r.what || '') + '">' + T.esc(r.what || '') + '</span>'; }, text: function (r) { return r.what || ''; } }] });
      S.fxLimits = T.h('div', { class: 'pnote' }); pfx.appendChild(S.fxLimits);
      body.appendChild(pfx);
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
        { key: 'name', label: 'prime broker', cls: 'l', html: function (r) { return T.esc(nameOf(r, names)); }, text: function (r) { return nameOf(r, names); }, title: function (r) { return r.account_id || ''; } },
        { key: 'pb_financing', label: 'financing', cls: 'l', html: function (r) { return r.pb_financing ? '<span class="pos">on</span>' : '<span class="muted">off</span>'; }, text: function (r) { return r.pb_financing ? 'on' : 'off'; } },
        { key: 'limit', label: 'limit', cls: 'l', text: function (r) { return r.limit_text || T.money(r, 'pb_limit'); } }, money('long_mv', 'long MV'), money('financed', 'financed'),
        { key: 'headroom', label: 'headroom', title: function (r) { return (r.formula || '') + '\nafter what working orders already need: ' + T.money(r, 'headroom_after_committed'); }, html: function (r) { return T.esc(T.money(r, 'headroom')); }, text: function (r) { return T.money(r, 'headroom'); } },
        money('otc_im', 'OTC IM'), money('otc_vm_posted', 'VM posted'), money('otc_vm_received', 'VM received'), money('nav', 'PB NAV'),
        xCol('gross_leverage', 'gross / NAV'), xCol('gross_notional_leverage', 'notional / NAV'), pctCol('trs_pct_nav', 'TRS / NAV')] });
      body.appendChild(pfin);
      /* FINSIM1_PLAN P3 task 13: Reg T per account (P6's figures, carried in the risk answer) */
      var preg = panel('regt', 'Reg T margin by account', 'requirement against the eligible collateral on assumed, editable terms; a deficit is reserved on cash and raises a margin call - never a forced sale');
      S.regWhy = T.h('div', { class: 'pnote' }); preg.appendChild(S.regWhy);
      S.reg = gridIn(preg, { rowKey: function (r) { return r.account_id; }, empty: 'no account under Reg T', columns: [
        { key: 'name', label: 'account', cls: 'l', text: function (r) { return nameOf(r, names); } },
        money('requirement', 'requirement', 'initial requirement: short value beyond the proceeds held x the initial %'), money('maintenance', 'maintenance'),
        money('eligible', 'eligible collateral', 'fully paid longs at their loan value + free cash'),
        { key: 'excess', label: 'excess / deficit', html: function (r) { var d = T.num(r, 'deficit'); return d ? '<b class="neg">deficit ' + T.esc(T.money(r, 'deficit')) + '</b>' : '<span class="pos">' + T.esc(T.money(r, 'excess')) + '</span>'; },
          text: function (r) { return T.num(r, 'deficit') ? '-' + T.money(r, 'deficit') : T.money(r, 'excess'); } },
        { key: 'open_calls', label: 'open calls', text: function (r) { return r.open_calls == null ? '' : String(r.open_calls) + (r.unmet_calls ? ' (' + r.unmet_calls + ' unmet)' : ''); } },
        { key: 'terms', label: 'terms (assumed)', cls: 'l', html: function (r) { var t = r.terms || {}; return '<span class="sub2" title="' + T.esc((r.definition || '') + (t.source ? ' . ' + t.source : '')) + '">' + T.esc((t.initial_text ? 'initial ' + t.initial_text : '') + (t.maintenance_text ? ' . maintenance ' + t.maintenance_text : '') + (t.long_loan_value_text ? ' . long loan value ' + t.long_loan_value_text : '') + (t.source ? ' . ' + t.source : '')) + '</span>'; },
          text: function (r) { var t = r.terms || {}; return [t.initial_text, t.maintenance_text, t.long_loan_value_text].filter(Boolean).join(' / '); } }] });
      body.appendChild(preg);
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
      S.warn = gridIn(pw, { empty: 'no risk warnings', columns: [{ key: 'severity', label: 'severity', cls: 'l', html: sevBadge, text: function (w) { return w.severity || ''; } }, { key: 'code', label: 'kind', cls: 'l', text: function (w) { return T.codeWords ? T.codeWords(w.code || '') : (w.code || ''); }, title: function (w) { return w.code || ''; } },
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
      S.expNote.textContent = r.headline_note || '';
      S.sec.update(r.by_sector || [], { empty: 'no headline exposure' });
      var rg = r.reg_t || {};
      S.regWhy.textContent = rg.available === false ? 'unavailable: ' + (rg.reason || 'not in this build') : ((rg.note || '') + ((rg.errors || []).length ? ' Could not be measured: ' + rg.errors.join('; ') : ''));
      S.reg.update(rg.available === false ? [] : (rg.rows || []), { empty: rg.available === false ? 'unavailable: ' + (rg.reason || 'not in this build') : 'no account under Reg T' });
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
      /* FX_CCS_PLAN K7: the currency moves are the FX block's pair rows (each pair, which leg held), not one +/-5 % USD row */
      var fxb = r.fx_sensitivities || null;
      S.scen.update((r.scenarios || []).filter(function (s) { return !fxb || s.group !== 'fx'; }), { empty: 'none' });
      T.clear(S.scenNotes);
      (r.scenario_notes || []).forEach(function (n) { if (fxb && n.group === 'fx') { return; } S.scenNotes.appendChild(T.h('div', null, T.h('b', null, n.title || n.group), n.text || '')); });
      if (fxb) { S.scenNotes.appendChild(T.h('div', null, T.h('b', null, 'Currency moves'), 'by pair, on the PV deltas, in the Currency risk (FX) block below')); }
      paintFx(fxb || {});
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
    function paintFx(b) {
      if (!b || !b.limits) {                      /* a server without the block: said, never an empty "none configured" */
        ['fxCcy', 'fxPairs', 'fxSens', 'fxCp', 'fxLadder'].forEach(function (k) { S[k].update([], { empty: 'unavailable' }); });
        S.fxLimits.textContent = '';
        S.fxNote.textContent = 'the currency risk block is not available from this server';
        return;
      }
      S.fxCcy.update(b.by_currency || [], { empty: 'USD only' });
      S.fxPairs.update(b.pairs || [], { empty: 'no currency exposure' });
      var sens = [];
      function add(list, what, keyName, valName) {
        (list || []).forEach(function (x) { sens.push({ what: what, key: x[keyName], usd: x[valName], contracts: (x.rows || []).map(function (y) { return y.contract_id; }).join(', ') }); });
      }
      add(b.rates01, 'rates +1 bp', 'ccy', 'usd_per_bp');
      add(b.basis01, 'basis level +1 bp', 'pair', 'usd_per_bp');
      add(b.spread01, 'OIS spread +1 bp', 'ccy', 'usd_per_bp');
      add(b.points01, 'forward points +1 pip', 'pair', 'usd_per_pip');
      sens.forEach(function (x) { if (/^[A-Z]{6}$/.test(String(x.key || ''))) { x.key = x.key.slice(0, 3) + '/' + x.key.slice(3); } });
      S.fxSens.update(sens, { empty: 'no OTC FX contract' });
      S.fxCp.update(b.counterparty || [], { empty: 'no OTC contracts' });
      S.fxLadder.update(b.ladder || [], { empty: 'nothing scheduled' });
      var lim = b.limits || {};
      S.fxLimits.textContent = [(lim.text || 'notional limits: none configured') + (lim.headline_gross_nav != null ? ' . headline gross / NAV ' + lev(lim.headline_gross_nav) + ' (warning above ' +
        (lim.warning_limit || '2x NAV') + ') . gross notional / NAV ' + lev(lim.gross_notional_nav) : ''), lim.basis || ''].filter(Boolean).join(' . ');
      S.fxNote.textContent = [(b.labels || []).join(' . '), b.method || '', (b.notes || []).length ? 'notes: ' + b.notes.join('; ') : ''].filter(Boolean).join(' . ');
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
