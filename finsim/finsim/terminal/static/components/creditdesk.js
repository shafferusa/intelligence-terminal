/* The Credit Instruments desk (BUILD2_PLAN P08): one desk per tab of pages/markets_credit.js.
 *
 *   var h = T.components.creditdesk.mount(el, {tab: 'loans', account_id: 'P1:CREDIT', book: 'P'});  h.unmount();
 *
 * Desks and what they call (every GET carries the selected book):
 * - loans:          GET /api/credit/counterparties?type=BANK, /api/credit/rates, /api/credit/loans; POST /api/credit/loans
 *                   {account_id, currency, amount, counterparty_id, tenor}; POST /api/credit/loans/{id}/repay {amount | full}.
 * - repo:           GET /api/credit/repos (repos + the Treasuries free to pledge), /api/credit/counterparties?type=REPO_DEALER,
 *                   /api/debt/curve (benchmark Treasuries for a reverse repo); POST /api/credit/repos/preview, /api/credit/repos,
 *                   /api/credit/repos/{id}/(roll|close).
 * - lending:        GET /api/credit/lending; POST /api/credit/lending/preview, /api/credit/lending, /api/credit/lending/{id}/recall.
 * - borrows:        GET /api/credit/borrows, /api/credit/locate; POST /api/credit/borrows, /api/credit/borrows/{id}/return.
 * - private:        components/privatecredit.js.
 * - rates:          GET /api/credit/rates.
 * - counterparties: GET /api/credit/counterparties?exposure=1; PATCH /api/credit/counterparties/{id} {terms | reset}.
 * Every institution is named with "(assumed terms)": the terms are model inputs the owner can edit, never quotes.  Nothing
 * is computed here except the display of what the server answers.  ASCII only, no framework.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  T.components = T.components || {};
  var CSS = [
    '.cdesk{container-type:inline-size}',
    '.cdesk .cols2{display:grid;grid-template-columns:minmax(0,1fr);gap:10px;align-items:start}.cdesk .cols2>.panel:first-child{max-width:660px}',
    '@container (min-width:1360px){.cdesk .cols2{grid-template-columns:minmax(0,380px) minmax(0,1fr)}.cdesk .cols2>.panel:first-child{max-width:none}}',
    '@container (min-width:900px){.cdesk .cols2.even{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.cdesk .cols2.even>.panel:first-child{max-width:none}}',
    '.cdesk .sub{display:block;font-size:10px;line-height:1.3;color:var(--fg3);max-width:210px;overflow:hidden;text-overflow:ellipsis}.cdesk td{padding-top:3px;padding-bottom:3px}',
    '@container (max-width:1099px){.cdesk .c-x{display:none}.cdesk .acts.v .btn{display:block;margin:2px 0;width:100%}}',
    '.cdesk .panel{margin-bottom:10px}.cdesk .panel h2 small{font-weight:400;color:var(--fg3);margin-left:6px}',
    '.cdesk .form select,.cdesk .form input{max-width:100%;min-width:0}.cdesk .form{grid-template-columns:minmax(90px,120px) minmax(0,1fr)}',
    '.cdesk .line{font-family:var(--mono);font-size:11px;color:var(--fg2);margin:4px 0}.cdesk .line b{color:var(--fg)}',
    '.cdesk .assumed{font-size:10px;color:var(--accent);border:1px solid var(--line);border-radius:3px;padding:0 4px;margin-left:4px;white-space:nowrap}',
    '.cdesk .why{color:var(--warn);font-size:10.5px}.cdesk .nm{display:inline-block;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle}',
    '.cdesk .pv{border:1px solid var(--line);border-radius:4px;padding:6px 8px;margin-top:6px}.cdesk .pv .kv{margin:0}',
    '.cdesk .acts{white-space:nowrap}.cdesk .acts .btn{margin-right:3px}.cdesk .notice{border-left:3px solid var(--accent);padding:4px 8px;margin:6px 0;color:var(--fg2)}',
    '.cdesk .grp{margin:8px 0 2px;font-weight:600;color:var(--fg2)}',
    '.cdesk .acts.v{display:inline-block}'
  ].join('');
  var cssDone = false;
  function ensureCss() { if (cssDone || document.getElementById('cdesk-css')) { cssDone = true; return; } cssDone = true; var s = T.h('style', { id: 'cdesk-css' }); s.textContent = CSS; document.head.appendChild(s); }

  function m(x) { return x == null || x === '' ? 'n/a' : T.fmt.money(x); }
  function pctOf(x) { return x == null || x === '' ? 'n/a' : T.fmt.ratePct(x); }
  function rate(x) { return x == null || x === '' ? 'n/a' : T.fmt.rate(x); }
  function opt(value, label, selected) { return T.h('option', { value: value, selected: !!selected }, label); }
  function select(list, value) { var s = T.h('select'); list.forEach(function (o) { s.appendChild(opt(o[0], o[1], String(o[0]) === String(value))); }); return s; }
  function assumed() { return T.h('span', { class: 'assumed', title: 'model input you can edit on the Counterparties tab; never a quote from the institution' }, 'assumed terms'); }
  function panel(title, sub) { var p = T.h('div', { class: 'panel' }); p.appendChild(T.h('h2', null, title, sub ? T.h('small', null, sub) : null)); return p; }
  function amountText(input) { return String(input.value || '').replace(/,/g, '').trim(); }
  function err(box, e, where) { T.clear(box); T.errorBox(box, e, where); }
  function gridIn(el, spec) { var g = T.h('div'); el.appendChild(g); return T.components.grid.mount(g, spec); }
  function actionsHtml(list, vertical) { return '<span class="acts' + (vertical ? ' v' : '') + '">' + list.map(function (a) { return '<button class="btn small" data-act="' + a[0] + '"' + (a[2] ? ' disabled title="' + T.esc(a[2]) + '"' : '') + '>' + T.esc(a[1]) + '</button>'; }).join('') + '</span>'; }
  /* the institution's name without the "(assumed terms)" / "(..., modelled)" tail: the full label stays in the tooltip and the
     panel says the terms are assumed */
  function nmShort(s) { return String(s || '').replace(/\s*\([^()]*\b(assumed terms|modelled)\)\s*$/, ''); }
  /* a cell's second line (small, muted) */
  function sub(text, title) { return '<span class="sub"' + (title ? ' title="' + T.esc(title) + '"' : '') + '>' + T.esc(text == null ? '' : text) + '</span>'; }
  var TYPE_SHORT = { BANK: 'bank', REPO_DEALER: 'primary dealer', AGENT_LENDER: 'agent lender', PRIME_BROKER: 'prime broker', PRIVATE_BORROWER: 'private-credit borrower' };
  /* a grid's onClick: the row's action button -> fn(action, row) (the grid hands the row it drew; a disabled button never fires) */
  function clicker(fn) {
    return function (row, e) {
      var b = e && e.target && e.target.closest ? e.target.closest('button[data-act]') : null;
      if (b && !b.disabled && row) { fn(b.dataset.act, row); }
    };
  }

  /* =========================================================== Currency loans =========================================== */
  function loansDesk(el, ctx) {
    var st = { dead: false, banks: [], rates: null, rows: [] };
    var cols = T.h('div', { class: 'cols2' }); el.appendChild(cols);
    var left = panel('Draw a loan', 'from a bank'); var right = panel('Open loans', 'interest to date and payoff . lender terms are assumed');
    cols.appendChild(left); cols.appendChild(right);
    var bankSel = T.h('select', { id: 'cd-loan-bank' }); var ccySel = T.h('select', { id: 'cd-loan-ccy' }); var amount = T.h('input', { type: 'text', id: 'cd-loan-amount', placeholder: 'e.g. 1,000,000.00' });
    var tenorSel = T.h('select', { id: 'cd-loan-tenor' }); var line = T.h('div', { class: 'line' }); var res = T.h('div');
    var btn = T.h('button', { class: 'btn primary', id: 'cd-loan-draw' }, 'Draw');
    left.appendChild(T.form([T.field('lender', bankSel, 'a bank of the counterparty directory; its spread, currencies, tenors and maximum are assumed terms'),
      T.field('currency', ccySel), T.field('amount', amount, 'the drawn currency is settled cash in this account at once'), T.field('tenor', tenorSel, 'OPEN = until you repay'),
      [T.h('span'), T.h('div', { class: 'row' }, btn)]]));
    left.appendChild(line); left.appendChild(res);
    left.appendChild(T.h('div', { class: 'hint' }, 'Floating: the currency\'s policy index (latest print, its date shown) + the lender\'s assumed spread, ACT/360 (GBP ACT/365), accrued nightly, paid with the principal. Repaying needs that currency in this account (buy it with Convert).'));
    function bank() { var id = bankSel.value; return st.banks.filter(function (b) { return b.counterparty_id === id; })[0] || null; }
    function fillBank() {
      var b = bank(); T.clear(ccySel); T.clear(tenorSel);
      if (!b) { return; }
      var lt = (b.terms || {}).loan || {};
      Object.keys(lt.spreads || {}).sort().forEach(function (c) { ccySel.appendChild(opt(c, c, c === (T.store.get('credit.ccy', 'EUR')))); });
      (lt.tenors || []).forEach(function (t) { tenorSel.appendChild(opt(t, t === 'OPEN' ? 'open (until repaid)' : t, t === '1M')); });
      quoteLine();
    }
    function quoteLine() {
      var b = bank(); var c = ccySel.value; T.clear(line);
      if (!b || !st.rates) { return; }
      var q = (st.rates.loans || []).filter(function (x) { return x.counterparty_id === b.counterparty_id && x.currency === c; })[0];
      if (!q) { line.textContent = 'no indicative rate'; return; }
      line.appendChild(T.h('span', null, 'indicative: ', T.h('b', null, q.index || ''), ' ', q.index_pct != null ? pctOf(q.index_pct) + ' (' + q.index_as_of + ')' : 'no print loaded', ' + ', pctOf(q.spread_pct),
        ' = ', T.h('b', null, q.all_in_pct != null ? pctOf(q.all_in_pct) : 'n/a'), q.monthly_proxy ? ' (monthly proxy)' : ''));
      line.appendChild(assumed());
      var lt = (b.terms || {}).loan || {};
      if (lt.max_usd) { line.appendChild(T.h('div', null, 'facility maximum USD ' + T.fmt.money(lt.max_usd, 0) + ' (all open loans from this bank in this book)')); }
    }
    bankSel.addEventListener('change', fillBank); ccySel.addEventListener('change', function () { T.store.set('credit.ccy', ccySel.value); quoteLine(); });
    btn.addEventListener('click', function () {
      var b = bank(); if (!b) { return; }
      var a = amountText(amount); if (!/^\d+(\.\d{1,2})?$/.test(a) || Number(a) <= 0) { T.clear(res).appendChild(T.h('div', { class: 'error' }, 'amount must be a positive number with at most 2 decimals')); return; }
      btn.disabled = true; T.clear(res);
      T.api.post('/api/credit/loans', { account_id: ctx.account_id, currency: ccySel.value, amount: a, counterparty_id: b.counterparty_id, tenor: tenorSel.value }).then(function (r) {
        btn.disabled = false; var ln = r.loan || {}; var rt = r.rate || {};
        T.ui.toast('loan ' + (ln.loan_id || '') + ' drawn: ' + ln.currency + ' ' + m(ln.principal) + ' from ' + (ln.lender_label || ln.lender) + ' at ' + rate(rt.all_in), 'ok', 6000);
        res.appendChild(T.h('div', { class: 'pv' }, T.kv([['loan', ln.loan_id], ['lender', ln.lender_label || ln.lender], ['principal', ln.currency + ' ' + m(ln.principal)],
          ['rate', (rt.index || '') + ' ' + pctOf(rt.value) + ' (' + (rt.as_of || '') + ') + ' + rate(rt.spread) + ' = ' + rate(rt.all_in)], ['maturity', ln.maturity_date || 'open']])));
        load(); T.poll.kick('account_detail');
      }, function (e) { btn.disabled = false; err(res, e, 'loan draw'); });
    });
    var gEl = T.h('div'); right.appendChild(gEl);
    var foot = T.h('div', { class: 'hint' }); right.appendChild(foot);
    var grid = T.components.grid.mount(gEl, { tools: false, rows: [], rowKey: function (r) { return r.loan_id; }, empty: 'no open loans in this book',
      onClick: clicker(function (act, row) { repayDialog(row, act === 'payoff'); }),
      columns: [
        { key: 'loan_id', label: 'loan / lender', cls: 'l', html: function (r) { var who = r.lender_label || r.lender || ''; return '<b>' + T.esc(r.loan_id) + '</b>' + sub(nmShort(who), who); }, text: function (r) { return r.loan_id + ' ' + (r.lender_label || r.lender || ''); } },
        { key: 'act', label: ' ', cls: 'l', html: function (r) { return r.repayable ? actionsHtml([['repay', 'Repay'], ['payoff', 'Pay off']], true) : '<small class="muted">' + T.esc(r.kind === 'PB_MARGIN' ? 'PB financing' : '') + '</small>'; }, text: function () { return ''; } },
        { key: 'principal', label: 'principal', html: function (r) { return T.esc(r.currency + ' ' + m(r.principal)) + sub(r.maturity_date ? 'to ' + r.maturity_date : 'open, until repaid', 'opened ' + (r.opened_date || '')); }, text: function (r) { return r.currency + ' ' + m(r.principal); } },
        { key: 'index', label: 'index', html: function (r) { var why = (r.index_name || '') + ' as published on ' + (r.index_as_of || '') + (r.monthly_proxy ? ' (monthly proxy)' : ''); return r.index_value_pct != null ? T.esc(pctOf(r.index_value_pct)) + sub((r.index_name || '') + ' ' + (r.index_as_of || ''), why) : '<span class="why">no print</span>' + sub(r.index_name || ''); }, text: function (r) { return r.index_value_pct != null ? pctOf(r.index_value_pct) + ' ' + (r.index_as_of || '') : 'no print'; } },
        { key: 'allin', label: 'all-in', html: function (r) { return '<b>' + T.esc(r.all_in_pct != null ? pctOf(r.all_in_pct) : 'n/a') + '</b>' + sub('spread ' + pctOf(r.spread_pct), 'index + the lender\'s assumed spread, ACT/' + (r.day_basis || 360)); }, text: function (r) { return r.all_in_pct != null ? pctOf(r.all_in_pct) : 'n/a'; } },
        { key: 'accrued', label: 'interest', title_h: 'interest to date (posted + the nights not yet posted)', html: function (r) { var p = (r.pending_nights || []).length; return T.esc(m(r.accrued)) + (p ? sub(p + ' pending', p + ' night(s) not yet posted by the 00:30 run: a repayment now posts them first') : ''); }, text: function (r) { return m(r.accrued); } },
        { key: 'payoff', label: 'payoff', html: function (r) { return r.payoff != null ? '<b>' + T.esc(m(r.payoff)) + '</b>' + sub(r.currency + ' now') : '<span class="why" title="' + T.esc(r.payoff_note || '') + '">n/a</span>'; }, text: function (r) { return r.payoff != null ? m(r.payoff) : 'n/a'; } }
      ] });
    function repayDialog(row, full) {
      var amt = T.h('input', { type: 'text', placeholder: row.currency + ' amount' }); var box = T.h('div', { class: 'error', hidden: true });
      var info = T.kv([['principal', row.currency + ' ' + m(row.principal)], ['interest to date', row.currency + ' ' + m(row.accrued)], ['payoff now', row.currency + ' ' + m(row.payoff)]]);
      T.ui.modal({ title: (full ? 'Pay off ' : 'Repay ') + row.loan_id + ' (' + (row.lender_label || row.lender) + ')',
        body: T.h('div', null, info, full ? T.h('div', { class: 'hint' }, 'Pays principal + every night owed (the nights the 00:30 run has not posted are posted first). Needs ' + row.currency + ' settled in ' + row.account_id + '.') :
          T.h('div', null, T.form([T.field('amount', amt, 'interest first, then principal')]), T.h('div', { class: 'hint' }, 'Interest is paid first, then principal.')), box),
        actions: [{ label: 'Cancel' }, { label: full ? 'Pay off' : 'Repay', cls: 'primary', onClick: function (mh, b) {
          var body = full ? { full: true } : { amount: amountText(amt) };
          if (!full && !/^\d+(\.\d{1,2})?$/.test(body.amount)) { box.hidden = false; box.textContent = 'amount must be a positive number with at most 2 decimals'; return; }
          b.disabled = true;
          T.api.post('/api/credit/loans/' + encodeURIComponent(row.loan_id) + '/repay', body).then(function (r) {
            mh.close(); T.ui.toast(row.loan_id + ': paid interest ' + T.money(r, 'paid_interest') + ', principal ' + T.money(r, 'paid_principal') + (r.closed ? ' - loan closed' : ''), 'ok', 6000); load(); T.poll.kick('account_detail');
          }, function (e) { b.disabled = false; box.hidden = false; box.textContent = T.api.fail(e, 'loan repay'); });
        } }] });
    }
    function load() {
      return T.api.get('/api/credit/loans').then(function (d) {
        if (st.dead) { return; }
        st.rows = d.rows || []; grid.update(st.rows);
        foot.textContent = (d.note || '') + (st.rows.length ? '' : '');
      }, function (e) { if (!st.dead) { err(foot, e, 'loans'); } });
    }
    Promise.all([T.api.get('/api/credit/counterparties', { type: 'BANK' }), T.api.get('/api/credit/rates')]).then(function (x) {
      if (st.dead) { return; }
      st.banks = (x[0].counterparties || []).filter(function (b) { return b.type === 'BANK'; }); st.rates = x[1];
      var pick = T.store.get('credit.bank', 'BANK_DB');
      st.banks.forEach(function (b) { bankSel.appendChild(opt(b.counterparty_id, b.label, b.counterparty_id === pick)); });
      bankSel.addEventListener('change', function () { T.store.set('credit.bank', bankSel.value); });
      fillBank();
    }, function (e) { if (!st.dead) { err(res, e, 'lenders'); } });
    T.poll.page('credit-loans', 30000, load);
    return { unmount: function () { st.dead = true; } };
  }

  /* =========================================================== Repo & reverse repo ====================================== */
  function repoDesk(el, ctx) {
    var st = { dead: false, dealers: [], data: null, bench: [] };
    var cols = T.h('div', { class: 'cols2' }); el.appendChild(cols);
    var left = panel('Book a repo', 'with a primary dealer'); var right = panel('Repos', 'cash, interest owed, collateral marked daily . dealer terms are assumed');
    cols.appendChild(left); cols.appendChild(right);
    var sideSel = select([['REPO', 'Repo - borrow cash against your Treasuries'], ['REVERSE', 'Reverse repo - lend cash against the dealer\'s Treasury']], T.store.get('credit.repo.side', 'REPO'));
    var dealerSel = T.h('select', { id: 'cd-repo-dealer' }); var collSel = T.h('select', { id: 'cd-repo-coll' }); var cusip = T.h('input', { type: 'text', id: 'cd-repo-cusip', placeholder: 'or a CUSIP / UST id', spellcheck: 'false' });
    var face = T.h('input', { type: 'text', id: 'cd-repo-face', placeholder: 'face, e.g. 10,000,000' });
    var termSel = select([['ON', 'overnight'], ['1W', '1 week'], ['2W', '2 weeks'], ['1M', '1 month'], ['3M', '3 months'], ['OPEN', 'open (floats daily)']], '1W');
    sideSel.id = 'cd-repo-side'; termSel.id = 'cd-repo-term';
    var pvBtn = T.h('button', { class: 'btn', id: 'cd-repo-preview' }, 'Preview'); var bookBtn = T.h('button', { class: 'btn primary', id: 'cd-repo-book', disabled: true }, 'Book');
    var pvBox = T.h('div'); var sess = T.h('div', { class: 'line' });
    left.appendChild(T.form([T.field('side', sideSel), T.field('dealer', dealerSel, 'New York Fed primary dealers; spreads, haircuts and maxima are assumed terms'),
      T.field('collateral', T.h('div', null, collSel, cusip)), T.field('face', face), T.field('term', termSel), [T.h('span'), T.h('div', { class: 'row' }, pvBtn, bookBtn)]]));
    left.appendChild(sess); left.appendChild(pvBox);
    left.appendChild(T.h('div', { class: 'hint' }, 'Cash = collateral value (clean + accrued) x (1 - haircut). Rate = SOFR + the dealer\'s spread (you borrow) or SOFR - its spread (you lend), fixed for ON / term, floating for open, ACT/360. The collateral is marked at 17:15 ET each bond trading day and the cash re-sized both ways.'));
    var last = null;
    function fillColl() {
      T.clear(collSel);
      var side = sideSel.value;
      if (side === 'REPO') {
        var cand = ((st.data || {}).collateral || []).filter(function (c) { return c.eligible; });
        if (!cand.length) { collSel.appendChild(opt('', 'no Treasury held in Debt Instruments')); }
        cand.forEach(function (c) { collSel.appendChild(opt(c.instrument_id, c.symbol + ' ' + (c.name || '') + ' - free ' + T.fmt.qty(c.free) + ' of ' + T.fmt.qty(c.held) + (c.pending ? ' (purchases of ' + T.fmt.qty(c.pending) + ' settle ' + c.pending_due + ')' : ''))); });
        cusip.hidden = true;
      } else {
        collSel.appendChild(opt('', 'pick a benchmark Treasury'));
        st.bench.forEach(function (b) { collSel.appendChild(opt(b.instrument_id, b.tenor + ' ' + b.symbol + ' ' + (b.name || ''))); });
        cusip.hidden = false;
      }
      bookBtn.disabled = true;
    }
    sideSel.addEventListener('change', function () { T.store.set('credit.repo.side', sideSel.value); fillColl(); T.clear(pvBox); });
    [dealerSel, collSel, face, termSel, cusip].forEach(function (x) { x.addEventListener('change', function () { bookBtn.disabled = true; }); x.addEventListener('input', function () { bookBtn.disabled = true; }); });
    function body() {
      var iid = sideSel.value === 'REVERSE' && cusip.value.trim() ? cusip.value.trim().toUpperCase() : collSel.value;
      if (iid && /^[0-9A-Z]{9}$/.test(iid) && iid.indexOf('UST-') !== 0) { iid = 'UST-' + iid; }
      return { account_id: ctx.account_id, side: sideSel.value, counterparty_id: dealerSel.value, instrument_id: iid, face: amountText(face), term: termSel.value };
    }
    pvBtn.addEventListener('click', function () {
      var b = body(); T.clear(pvBox); bookBtn.disabled = true;
      if (!b.instrument_id) { pvBox.appendChild(T.h('div', { class: 'error' }, 'pick the collateral Treasury')); return; }
      if (!/^\d+$/.test(b.face)) { pvBox.appendChild(T.h('div', { class: 'error' }, 'face must be a whole amount (multiples of 1,000)')); return; }
      pvBtn.disabled = true;
      T.api.post('/api/credit/repos/preview', Object.assign({ soft: true }, b)).then(function (p) {
        pvBtn.disabled = false;
        if (p && p.blocked) {   /* V104R2-BW-05: a refused preview answers 200 {blocked, blockers} (repo.py soft mode): no failed request, no console error */
          (p.blockers || []).forEach(function (x) { pvBox.appendChild(T.h('div', { class: 'error', title: x.code || '' }, x.message || T.codeWords(x.code))); });
          return;   /* Book stays disabled */
        }
        last = b; var c = p.collateral || {};
        pvBox.appendChild(T.h('div', { class: 'pv' }, T.kv([['side', p.side_text], ['dealer', p.dealer], ['collateral', c.symbol + ' ' + T.fmt.qty(c.face) + ' face at ' + c.clean + ' + ' + c.accrued + ' accrued = ' + c.dirty, (c.source || '') + (c.note ? ' . ' + c.note : '')],
          ['collateral value', m(c.value)], ['haircut', p.haircut_pct], [p.side === 'REPO' ? 'cash you receive' : 'cash you lend', T.h('b', null, m(p.cash))], ['rate', p.rate_text],
          ['term', p.term + (p.maturity_date ? ' to ' + p.maturity_date + ' (' + p.nights + ' night' + (p.nights === 1 ? '' : 's') + ')' : ' (open)')],
          ['interest', p.interest != null ? m(p.interest) + ' (' + p.interest_note + ')' : p.interest_note], p.repay != null ? [p.side === 'REPO' ? 'you repay' : 'you receive back', m(p.repay)] : null]),
          (p.warnings || []).map(function (w) { return T.h('div', { class: 'why' }, w); })));
        bookBtn.disabled = false;
      }, function (e) { pvBtn.disabled = false; err(pvBox, e, 'repo preview'); });
    });
    bookBtn.addEventListener('click', function () {
      if (!last) { return; }
      bookBtn.disabled = true;
      T.api.post('/api/credit/repos', last).then(function (r) {
        T.ui.toast((r.repo && r.repo.side === 'REVERSE' ? 'reverse repo ' : 'repo ') + r.repo_id + ' booked: ' + m((r.repo || {}).principal) + ' at ' + ((r.preview || {}).rate_text || ''), 'ok', 7000);
        T.clear(pvBox); load(); T.poll.kick('account_detail');
      }, function (e) { bookBtn.disabled = false; err(pvBox, e, 'repo'); });
    });
    var gEl = T.h('div'); right.appendChild(gEl); var foot = T.h('div', { class: 'hint' }); right.appendChild(foot);
    var grid = T.components.grid.mount(gEl, { tools: false, rows: [], rowKey: function (r) { return r.repo_id; }, empty: 'no open repos in this book',
      onClick: clicker(repoAct),
      columns: [
        { key: 'repo_id', label: 'repo / dealer', cls: 'l', html: function (r) { return '<b>' + T.esc(r.repo_id) + '</b> <small title="' + T.esc(r.side_text || '') + '">' + (r.side === 'REPO' ? 'repo' : 'reverse') + '</small>' + sub(nmShort(r.dealer), (r.dealer || '') + ' . ' + (r.agreement || '')); }, text: function (r) { return r.repo_id + ' ' + (r.side === 'REPO' ? 'repo' : 'reverse') + ' ' + (r.dealer || ''); } },
        { key: 'act', label: ' ', cls: 'l', html: function (r) { return r.status !== 'OPEN' ? '<small class="muted">' + T.esc(String(r.status || '').toLowerCase()) + '</small>' : actionsHtml((r.rate_type === 'FLOATING' ? [] : [['roll', 'Roll']]).concat([['close', 'Close']]), true); }, text: function () { return ''; } },
        { key: 'coll', label: 'collateral', cls: 'l', html: function (r) { var c = r.collateral_now || {}; var how = r.side === 'REPO' ? 'pledged' : 'held'; return T.esc(r.symbol + ' ' + T.fmt.qty(r.face)) + sub(c.error ? how + ', no mark now' : how + ', worth ' + m(c.value), (r.side === 'REPO' ? 'pledged: not sellable while the repo is open' : 'held: the dealer\'s Treasury, returned at close') + (c.error ? ' . ' + c.error : (c.source ? ' . ' + c.source : ''))); }, text: function (r) { return r.symbol + ' ' + T.fmt.qty(r.face); } },
        { key: 'cash', label: 'cash', html: function (r) { var mg = r.margin_now || {}; var call = mg.delta != null && Number(mg.delta) !== 0; return T.esc(m(r.principal)) + (call ? sub('mark ' + T.fmt.signed(m(mg.delta)), (mg.text || '') + ' at the next 17:15 mark') : ''); }, text: function (r) { return m(r.principal); } },
        { key: 'rate', label: 'rate', html: function (r) { var s = r.sofr || {}; return T.esc(r.rate_pct) + sub(r.maturity_date ? (r.term || '') + ' to ' + r.maturity_date : 'open', s.value_pct ? 'SOFR ' + s.value_pct + ' % of ' + s.as_of + ' (' + (s.source || '') + ')' + (r.rate_type === 'FLOATING' ? ', floating' : ', fixed for the term') : ''); }, text: function (r) { return r.rate_pct; } },
        { key: 'interest', label: 'interest', title_h: 'interest owed to date (posted + the nights not yet posted)', html: function (r) { var p = (r.pending_nights || []).length; return T.esc(m(r.interest_owed)) + (p ? sub(p + ' pending', 'nights not yet posted by the 00:30 run') : ''); }, text: function (r) { return m(r.interest_owed); } },
        { key: 'payoff', label: 'payoff', html: function (r) { return r.payoff != null ? '<b>' + T.esc(m(r.payoff)) + '</b>' + sub(r.side === 'REPO' ? 'you repay' : 'you receive') : '<span class="why" title="' + T.esc(r.payoff_note || '') + '">n/a</span>'; }, text: function (r) { return r.payoff != null ? m(r.payoff) : 'n/a'; } }
      ] });
    function repoAct(act, row) {
      var what = act === 'roll' ? 'Roll ' + row.repo_id + ' into the same term at today\'s SOFR + the dealer spread? The interest owed (' + m(row.interest_owed) + ') is paid now.'
        : 'Close ' + row.repo_id + ' now? ' + (row.side === 'REPO' ? 'You repay ' : 'You receive ') + m(row.payoff) + ' (cash + interest owed).';
      T.ui.confirm(what).then(function (ok) {
        if (!ok) { return; }
        T.api.post('/api/credit/repos/' + encodeURIComponent(row.repo_id) + '/' + act, {}).then(function (r) {
          T.ui.toast(row.repo_id + (act === 'roll' ? ' rolled to ' + r.maturity_date + ' at ' + r.rate_pct : ' closed: principal ' + m(r.principal) + ', interest ' + m(r.interest)), 'ok', 6000); load();
        }, function (e) { T.api.fail(e, 'repo ' + act); });
      });
    }
    function load() {
      return T.api.get('/api/credit/repos').then(function (d) {
        if (st.dead) { return; }
        var first = !st.data; st.data = d; grid.update(d.rows || []);
        var s = d.session || {};
        sess.textContent = s.open ? 'US bond market open (08:00 - 17:00 ET): repos are booked, rolled and closed now' : 'US bond market closed: repo dealing reopens 08:00 ET' + (s.next_open ? ' (' + T.fmt.dow(s.next_open) + ' ' + T.fmt.date(s.next_open) + ')' : '');
        foot.textContent = d.note || '';
        if (first) { fillColl(); }
      }, function (e) { if (!st.dead) { err(foot, e, 'repos'); } });
    }
    Promise.all([T.api.get('/api/credit/counterparties', { type: 'REPO_DEALER' }), T.api.get('/api/debt/curve').catch(function () { return {}; })]).then(function (x) {
      if (st.dead) { return; }
      st.dealers = (x[0].counterparties || []).filter(function (d) { return d.type === 'REPO_DEALER'; });
      var pick = T.store.get('credit.dealer', 'PD_GS');
      st.dealers.forEach(function (d) { var t = (d.terms || {}).repo || {}; dealerSel.appendChild(opt(d.counterparty_id, d.label + ' . SOFR +' + T.fmt.rate(t.repo_spread) + ' / -' + T.fmt.rate(t.reverse_spread), d.counterparty_id === pick)); });
      dealerSel.addEventListener('change', function () { T.store.set('credit.dealer', dealerSel.value); });
      st.bench = ((x[1] || {}).benchmarks || []).filter(function (b) { return b.instrument_id; });
      fillColl();
    }, function (e) { if (!st.dead) { err(pvBox, e, 'dealers'); } });
    T.poll.page('credit-repo', 30000, load);
    return { unmount: function () { st.dead = true; } };
  }

  /* =========================================================== Securities lending ======================================= */
  function lendingDesk(el, ctx) {
    var st = { dead: false, data: null };
    var notice = T.h('div'); el.appendChild(notice);
    var top = panel('Lend shares', 'long US equities held in US Exchange or a prime-broker account'); el.appendChild(top);
    var lgEl = T.h('div'); top.appendChild(lgEl);
    var pvBox = T.h('div'); top.appendChild(pvBox);
    var loansP = panel('Shares on loan', 'fee you earn, rebate you pay, collateral marked daily'); el.appendChild(loansP);
    var gEl = T.h('div'); loansP.appendChild(gEl); var foot = T.h('div', { class: 'hint' }); loansP.appendChild(foot);
    var lendGrid = T.components.grid.mount(lgEl, { tools: false, rows: [], rowKey: function (r) { return r.account_id + '|' + r.instrument_id; }, empty: 'no long US equity positions in this book to lend',
      onClick: clicker(function (act, row) { lendForm(row); }),
      columns: [
        { key: 'symbol', label: 'symbol', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol) + '</b>'; }, text: function (r) { return r.symbol; } },
        { key: 'act', label: ' ', cls: 'l', html: function (r) { var why = !(st.data || {}).ledger_ready ? (st.data || {}).ledger_note : (Number(r.free) <= 0 ? 'nothing free to lend' : ''); return actionsHtml([['lend', 'Lend', why]]); }, text: function () { return ''; } },
        { key: 'account', label: 'account', cls: 'l', text: function (r) { return r.account_name || r.account_id; } },
        { key: 'position', label: 'position', text: function (r) { return T.fmt.qty(r.position); } },
        { key: 'lent', label: 'on loan', text: function (r) { return T.fmt.qty(r.lent); } },
        { key: 'ws', label: 'working sells', text: function (r) { return T.fmt.qty(r.working_sells); } },
        { key: 'res', label: 'covered calls', text: function (r) { return T.fmt.qty(r.reserved); } },
        { key: 'uns', label: 'unsettled', html: function (r) { return Number(r.unsettled) ? '<span title="bought, settles ' + T.esc(r.settles || '') + '">' + T.esc(T.fmt.qty(r.unsettled)) + '</span>' : '0'; } },
        { key: 'free', label: 'free to lend', html: function (r) { return '<b>' + T.esc(T.fmt.qty(r.free)) + '</b>'; } }
      ] });
    function lendForm(row) {
      T.clear(pvBox);
      var qty = T.h('input', { type: 'text', id: 'cd-lend-qty', value: String(row.free) }); var agentSel = T.h('select', { id: 'cd-lend-agent' });
      ((st.data || {}).agents || []).forEach(function (a) { var t = (a.terms || {}).lending || {}; agentSel.appendChild(opt(a.counterparty_id, a.label + ' . your share ' + T.fmt.pct(t.owner_split, 0), a.counterparty_id === T.store.get('credit.agent', 'AGENT_BNY'))); });
      var pvBtn = T.h('button', { class: 'btn', id: 'cd-lend-preview' }, 'Preview'); var go = T.h('button', { class: 'btn primary', id: 'cd-lend-go', disabled: true }, 'Lend'); var out = T.h('div');
      pvBox.appendChild(T.h('div', { class: 'pv' }, T.h('div', { class: 'grp' }, 'Lend ' + row.symbol + ' from ' + (row.account_name || row.account_id)),
        T.form([T.field('quantity', qty, 'at most the free quantity'), T.field('agent lender', agentSel), [T.h('span'), T.h('div', { class: 'row' }, pvBtn, go)]]), out));
      var body = function () { return { account_id: row.account_id, instrument_id: row.instrument_id, qty: String(qty.value).replace(/,/g, '').trim(), counterparty_id: agentSel.value }; };
      [qty, agentSel].forEach(function (x) { x.addEventListener('input', function () { go.disabled = true; }); x.addEventListener('change', function () { T.store.set('credit.agent', agentSel.value); go.disabled = true; }); });
      pvBtn.addEventListener('click', function () {
        T.clear(out); pvBtn.disabled = true;
        T.api.post('/api/credit/lending/preview', body()).then(function (p) {
          pvBtn.disabled = false;
          out.appendChild(T.kv([['agent', p.agent], ['tier', p.tier + ' (' + p.tier_basis + ')'], ['borrower fee', rate(p.fee_rate) + ' a year on qty x close'], ['your share', rate(p.own_fee_rate) + ' (' + T.fmt.pct(p.owner_split, 0) + ' of the fee) = about ' + m(p.fee_per_day) + ' a day'],
            ['cash collateral in', m(p.collateral) + ' (102 % of ' + T.fmt.qty(p.qty) + ' x ' + p.mark + ', ' + p.mark_quality + ')'], ['rebate you pay', (p.rebate_rate != null ? rate(p.rebate_rate) + ' on the collateral = about ' + m(p.rebate_per_day) + ' a day' : 'n/a until SOFR loads')]]));
          if (!p.ledger_ready) { out.appendChild(T.h('div', { class: 'why' }, p.ledger_note)); } else { go.disabled = false; }
        }, function (e) { pvBtn.disabled = false; err(out, e, 'lend preview'); });
      });
      go.addEventListener('click', function () {
        go.disabled = true;
        T.api.post('/api/credit/lending', body()).then(function (r) { T.ui.toast('lent ' + T.fmt.qty((r.loan || {}).qty) + ' ' + (r.loan || {}).symbol + ' through ' + (r.loan || {}).agent + ' (' + r.loan_id + ')', 'ok', 6000); T.clear(pvBox); load(); },
          function (e) { go.disabled = false; err(out, e, 'lend'); });
      });
    }
    var grid = T.components.grid.mount(gEl, { tools: false, rows: [], rowKey: function (r) { return r.loan_id; }, empty: 'no shares on loan',
      onClick: clicker(recallAct),
      columns: [
        { key: 'loan_id', label: 'loan / agent', cls: 'l', html: function (r) { return '<b>' + T.esc(r.loan_id) + '</b>' + sub(nmShort(r.agent), (r.agent || '') + ' (agent lender, assumed terms)'); }, text: function (r) { return r.loan_id + ' ' + (r.agent || ''); } },
        { key: 'act', label: ' ', cls: 'l', html: function (r) { return r.status === 'OPEN' ? actionsHtml([['recall', 'Recall']]) : '<small>' + T.esc(r.status) + '</small>'; }, text: function () { return ''; } },
        { key: 'symbol', label: 'on loan', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol) + '</b> ' + T.esc(T.fmt.qty(r.outstanding)) + sub('since ' + (r.opened_date || '')); }, text: function (r) { return r.symbol + ' ' + T.fmt.qty(r.outstanding); } },
        { key: 'fee', label: 'your fee', html: function (r) { return T.esc(rate(r.own_fee_rate)) + sub('tier ' + (r.tier || ''), 'the borrower pays ' + rate(r.fee_rate) + ' a year on qty x close; your share ' + T.fmt.pct(r.owner_split, 0)); }, text: function (r) { return rate(r.own_fee_rate); } },
        { key: 'coll', label: 'collateral in', html: function (r) { return T.esc(m(r.collateral)) + sub(r.market_value != null ? 'shares worth ' + m(r.market_value) : 'shares: no mark now', r.mark != null ? 'mark ' + r.mark + ' (' + (r.mark_quality || '') + '); the collateral is re-marked to 102 % at 17:10 ET' : ''); }, text: function (r) { return m(r.collateral); } },
        { key: 'afee', label: 'fee earned', title_h: 'your share of the fee accrued, received at recall', text: function (r) { return m(r.accrued_fee); } },
        { key: 'areb', label: 'rebate owed', title_h: 'rebate accrued on the cash collateral, paid at recall', text: function (r) { return m(r.accrued_rebate); } }
      ] });
    function recallAct(act, row) {
      T.ui.confirm('Recall ' + T.fmt.qty(row.outstanding) + ' ' + row.symbol + ' from ' + row.agent + '? The collateral ' + m(row.collateral) + ' goes back to the borrower; the fee and rebate accrued are settled.').then(function (ok) {
        if (!ok) { return; }
        T.api.post('/api/credit/lending/' + encodeURIComponent(row.loan_id) + '/recall', {}).then(function (r) {
          T.ui.toast('recalled ' + T.fmt.qty(r.qty) + ' ' + row.symbol + ': collateral returned ' + m(r.collateral_returned) + ', fee received ' + m(r.fee_received) + ', rebate paid ' + m(r.rebate_paid), 'ok', 7000); load();
        }, function (e) { T.api.fail(e, 'recall'); });
      });
    }
    function load() {
      return T.api.get('/api/credit/lending').then(function (d) {
        if (st.dead) { return; }
        st.data = d; lendGrid.update(d.lendable || []); grid.update(d.loans || []);
        T.clear(notice); if (!d.ledger_ready) { notice.appendChild(T.h('div', { class: 'notice' }, 'Lending out is not available in this version yet: ' + (d.ledger_note || ''))); }
        foot.textContent = d.note || '';
      }, function (e) { if (!st.dead) { err(foot, e, 'securities lending'); } });
    }
    T.poll.page('credit-lending', 30000, load);
    return { unmount: function () { st.dead = true; } };
  }

  /* =========================================================== Stock borrows ============================================ */
  function borrowsDesk(el, ctx) {
    var st = { dead: false, rows: [], agents: [] };
    var cols = T.h('div', { class: 'cols2' }); el.appendChild(cols);
    var left = panel('Locate and borrow', 'ahead of a short sale'); var right = panel('Stock borrows', 'every open borrow of this book, any account . lender terms are assumed');
    cols.appendChild(left); cols.appendChild(right);
    var acctSel = T.h('select', { id: 'cd-bor-acct' }); var sym = T.h('input', { type: 'text', id: 'cd-bor-sym', placeholder: 'symbol, e.g. AAPL', spellcheck: 'false' }); var qty = T.h('input', { type: 'text', id: 'cd-bor-qty', placeholder: 'shares' });
    var agentSel = T.h('select', { id: 'cd-bor-agent' }); var locBtn = T.h('button', { class: 'btn', id: 'cd-bor-locate' }, 'Locate'); var bBtn = T.h('button', { class: 'btn primary', id: 'cd-bor-go', disabled: true }, 'Borrow'); var out = T.h('div');
    left.appendChild(T.form([T.field('account', acctSel, 'shares are borrowed into the account that will sell short'), T.field('symbol', sym), T.field('quantity', qty),
      T.field('lender', agentSel, 'outside a prime broker the supply is sourced from an agent lender (assumed terms); a prime-broker account borrows from its PB'), [T.h('span'), T.h('div', { class: 'row' }, locBtn, bBtn)]]));
    left.appendChild(out);
    left.appendChild(T.h('div', { class: 'hint' }, 'A short sale on US Exchange borrows by itself; borrowing here first reserves the shares. Fee tiers come from FINRA short volume and average volume (a heuristic, not a quote). Collateral 102 % in cash, marked daily both ways.'));
    (T.state.accounts || []).forEach(function (a) { var k = T.accountKind ? T.accountKind(a.account_id) : ''; if ((k === 'US_EXCHANGE' || k === 'PB') && a.status === 'OPEN' && T.accountOwner(a.account_id)) { acctSel.appendChild(opt(a.account_id, a.name || a.account_id)); } });
    var loc = null;
    function req() { return { account_id: acctSel.value, instrument_id: String(sym.value || '').trim().toUpperCase(), qty: String(qty.value || '').replace(/,/g, '').trim(), counterparty_id: agentSel.value }; }
    [acctSel, sym, qty, agentSel].forEach(function (x) { x.addEventListener('input', function () { bBtn.disabled = true; }); x.addEventListener('change', function () { bBtn.disabled = true; }); });
    locBtn.addEventListener('click', function () {
      var r = req(); T.clear(out); bBtn.disabled = true;
      if (!r.instrument_id || !/^\d+$/.test(r.qty)) { out.appendChild(T.h('div', { class: 'error' }, 'give a symbol and a whole number of shares')); return; }
      locBtn.disabled = true;
      T.api.get('/api/credit/locate', { account_id: r.account_id, instrument_id: r.instrument_id, qty: r.qty }).then(function (q) {
        locBtn.disabled = false; loc = r;
        out.appendChild(T.h('div', { class: 'pv' }, T.kv([['available', q.available ? T.fmt.qty(q.qty_available) + ' of ' + T.fmt.qty(q.qty) : 'no borrow'], ['tier', q.tier], ['fee', rate(q.fee_rate)], ['rebate', q.rebate_rate != null ? rate(q.rebate_rate) : 'n/a'],
          ['collateral', m(q.collateral)], ['lender', q.lender]]), T.h('div', { class: 'hint' }, q.basis || '')));
        bBtn.disabled = !q.available;
      }, function (e) { locBtn.disabled = false; err(out, e, 'locate'); });
    });
    bBtn.addEventListener('click', function () {
      if (!loc) { return; }
      bBtn.disabled = true;
      T.api.post('/api/credit/borrows', loc).then(function (r) { var b = r.borrow || {}; T.ui.toast('borrowed ' + T.fmt.qty(b.qty) + ' ' + b.instrument_id + ' from ' + b.lender + ' (' + b.borrow_id + ')', 'ok', 6000); T.clear(out); load(); },
        function (e) { bBtn.disabled = false; err(out, e, 'borrow'); });
    });
    var gEl = T.h('div'); right.appendChild(gEl); var foot = T.h('div', { class: 'hint' }); right.appendChild(foot);
    var grid = T.components.grid.mount(gEl, { tools: false, rows: [], rowKey: function (r) { return r.borrow_id; }, empty: 'no open stock borrows in this book',
      onClick: clicker(returnAct),
      columns: [
        { key: 'borrow_id', label: 'borrow / lender', cls: 'l', html: function (r) { return '<b>' + T.esc(r.borrow_id) + '</b>' + sub(nmShort(r.lender), r.lender); }, text: function (r) { return r.borrow_id + ' ' + (r.lender || ''); } },
        { key: 'act', label: ' ', cls: 'l', html: function (r) { return r.status !== 'OPEN' ? '' : actionsHtml([['return', 'Return' + (Number(r.returnable_qty) > 0 && Number(r.returnable_qty) < Number(r.outstanding) ? ' ' + T.fmt.qty(r.returnable_qty) : ''), Number(r.returnable_qty) > 0 ? '' : (r.return_note || 'nothing to return')]]); }, text: function () { return ''; } },
        { key: 'symbol', label: 'borrowed', cls: 'l', html: function (r) { return '<b>' + T.esc(r.symbol) + '</b> ' + T.esc(T.fmt.qty(r.outstanding)) + sub(r.account_name || r.account_id); }, text: function (r) { return r.symbol + ' ' + T.fmt.qty(r.outstanding); } },
        { key: 'fee', label: 'fee', html: function (r) { return T.esc(rate(r.fee_rate)) + sub('rebate ' + (r.rebate_rate === 'unavailable' || r.rebate_rate == null ? 'n/a' : rate(r.rebate_rate)), 'tier ' + (r.tier || '')); }, text: function (r) { return rate(r.fee_rate); } },
        { key: 'coll', label: 'collateral', html: function (r) { return T.esc(m(r.collateral)) + sub(r.market_value != null ? 'shares worth ' + m(r.market_value) : 'shares: no mark now', r.mark != null ? 'mark ' + r.mark + ' (' + (r.mark_quality || '') + ')' : ''); }, text: function (r) { return m(r.collateral); } },
        { key: 'fees', label: 'fees accrued', text: function (r) { return m(r.accrued_fees); } },
        { key: 'notice', label: 'recall', cls: 'l', html: function (r) { return r.recall_notice ? '<span class="why" title="' + T.esc(r.recall_notice) + '">notice</span>' : '<small class="muted">none</small>'; }, text: function (r) { return r.recall_notice ? 'notice' : 'none'; } }
      ] });
    function returnAct(act, row) {
      T.ui.confirm('Return ' + T.fmt.qty(row.returnable_qty) + ' ' + row.symbol + ' to ' + row.lender + '? The collateral comes back and the accrued fee and rebate are settled.').then(function (ok) {
        if (!ok) { return; }
        T.api.post('/api/credit/borrows/' + encodeURIComponent(row.borrow_id) + '/return', { qty: row.returnable_qty }).then(function () { T.ui.toast('borrow ' + row.borrow_id + ' returned', 'ok'); load(); }, function (e) { T.api.fail(e, 'return borrow'); });
      });
    }
    function load() {
      return T.api.get('/api/credit/borrows').then(function (d) { if (st.dead) { return; } st.rows = d.rows || []; grid.update(st.rows); foot.textContent = d.note || ''; },
        function (e) { if (!st.dead) { err(foot, e, 'borrows'); } });
    }
    T.api.get('/api/credit/counterparties', { type: 'AGENT_LENDER' }).then(function (d) {
      if (st.dead) { return; }
      (d.counterparties || []).filter(function (a) { return a.type === 'AGENT_LENDER'; }).forEach(function (a) { agentSel.appendChild(opt(a.counterparty_id, a.label, a.counterparty_id === ((d.defaults || {}).borrow_lender))); });
    });
    T.poll.page('credit-borrows', 30000, load);
    return { unmount: function () { st.dead = true; } };
  }

  /* =========================================================== Rates ===================================================== */
  function ratesDesk(el, ctx) {
    var st = { dead: false };
    var box = T.h('div'); el.appendChild(box);
    function paint(d) {
      T.clear(box);
      var r = d.rates || {}; var f = r.fred || {};
      var top = panel('Reference rates', 'as published, each with its date');
      var s = r.SOFR || {};
      top.appendChild(T.kv([['SOFR', s.value != null ? pctOf(s.value) + ' (' + s.as_of + ', ' + (s.source || 'FRED') + ')' : 'no print loaded'], ['FRED', f.maintenance ? 'unavailable (maintenance): series keep their last print with its true date' : 'available']]));
      var pol = Object.keys(r.policy || {}).map(function (c) { var x = r.policy[c]; return { ccy: c, series: x.series, value: x.value, as_of: x.as_of, proxy: x.monthly_proxy, source: x.source }; });
      var pg = T.h('div'); top.appendChild(T.h('div', { class: 'grp' }, 'Policy indices of the currency loans')); top.appendChild(pg);
      T.components.grid.mount(pg, { tools: false, rows: pol, rowKey: function (x) { return x.ccy; }, columns: [
        { key: 'ccy', label: 'currency', cls: 'l', text: function (x) { return x.ccy; } }, { key: 'series', label: 'series', cls: 'l', text: function (x) { return x.series; } },
        { key: 'value', label: 'rate', text: function (x) { return x.value != null ? pctOf(x.value) : 'not loaded'; } }, { key: 'asof', label: 'as of', cls: 'l', text: function (x) { return (x.as_of || '') + (x.proxy ? ' (monthly proxy)' : ''); } },
        { key: 'src', label: 'source', cls: 'l', text: function (x) { return x.source || ''; } }] });
      var oas = Object.keys(r.oas || {}).map(function (b) { var x = r.oas[b]; return { bucket: b, series: x.series, value: x.value, as_of: x.as_of }; });
      var og = T.h('div'); top.appendChild(T.h('div', { class: 'grp' }, 'ICE BofA option-adjusted spreads (private-credit marks use the HY index)')); top.appendChild(og);
      T.components.grid.mount(og, { tools: false, rows: oas, rowKey: function (x) { return x.bucket; }, columns: [
        { key: 'bucket', label: 'bucket', cls: 'l', text: function (x) { return x.bucket; } }, { key: 'series', label: 'series', cls: 'l', text: function (x) { return x.series; } },
        { key: 'value', label: 'spread', text: function (x) { return x.value != null ? pctOf(x.value) : 'not loaded'; } }, { key: 'asof', label: 'as of', cls: 'l', text: function (x) { return x.as_of || (f.maintenance ? 'FRED in maintenance' : ''); } }] });
      box.appendChild(top);
      var two = T.h('div', { class: 'cols2 even' }); box.appendChild(two);
      var lp = panel('Currency loans: all-in by lender', 'index + assumed spread'); two.appendChild(lp);
      var lg = T.h('div'); lp.appendChild(lg);
      T.components.grid.mount(lg, { tools: false, rows: d.loans || [], rowKey: function (x) { return x.counterparty_id + x.currency; }, columns: [
        { key: 'lender', label: 'lender', cls: 'l', html: function (x) { return '<span class="nm" title="' + T.esc(x.lender) + '">' + T.esc(nmShort(x.lender)) + '</span>'; } }, { key: 'ccy', label: 'ccy', cls: 'l', text: function (x) { return x.currency; } },
        { key: 'spread', label: 'spread', text: function (x) { return pctOf(x.spread_pct); } }, { key: 'allin', label: 'all-in', text: function (x) { return x.all_in_pct != null ? pctOf(x.all_in_pct) : 'no index'; } }] });
      var rp = panel('Repo with the primary dealers', 'SOFR +/- assumed spread'); two.appendChild(rp);
      var rg = T.h('div'); rp.appendChild(rg);
      T.components.grid.mount(rg, { tools: false, rows: d.repo || [], rowKey: function (x) { return x.counterparty_id; }, columns: [
        { key: 'dealer', label: 'dealer', cls: 'l', html: function (x) { return '<span class="nm" title="' + T.esc(x.dealer) + '">' + T.esc(nmShort(x.dealer)) + '</span>'; } },
        { key: 'repo', label: 'you borrow', text: function (x) { return x.repo_pct != null ? pctOf(x.repo_pct) : 'n/a'; } }, { key: 'rev', label: 'you lend', text: function (x) { return x.reverse_pct != null ? pctOf(x.reverse_pct) : 'n/a'; } }] });
      box.appendChild(T.h('div', { class: 'hint' }, d.note || ''));
    }
    T.poll.page('credit-rates', 60000, function () { return T.api.get('/api/credit/rates').then(function (d) { if (!st.dead) { paint(d); } }, function (e) { if (!st.dead) { err(box, e, 'rates'); } }); });
    return { unmount: function () { st.dead = true; } };
  }

  /* =========================================================== Counterparties ============================================ */
  function termsText(c) {
    var t = c.terms || {};
    if (t.loan) { return Object.keys(t.loan.spreads || {}).sort().map(function (k) { return k + ' +' + T.fmt.rate(t.loan.spreads[k]); }).join(', ') + (t.loan.max_usd ? ' . max USD ' + T.fmt.money(t.loan.max_usd, 0) : ''); }
    if (t.repo) { return 'repo SOFR +' + T.fmt.rate(t.repo.repo_spread) + ', reverse SOFR -' + T.fmt.rate(t.repo.reverse_spread) + (t.repo.haircuts ? ' . haircuts ' + Object.keys(t.repo.haircuts).map(function (k) { return k.toLowerCase() + ' ' + T.fmt.rate(t.repo.haircuts[k]); }).join(', ') : ' . haircuts bills 1 %, notes / bonds 2 %, TIPS 3 %') + (t.repo.max_usd ? ' . max USD ' + T.fmt.money(t.repo.max_usd, 0) : ''); }
    if (t.lending) { return 'your share of the fee ' + T.fmt.pct(t.lending.owner_split, 0) + ' . rebate SOFR ' + T.fmt.rate(t.lending.rebate_spread); }
    if (t.pb) { return 'financing SOFR +' + T.fmt.rate(t.pb.financing_spread) + ' . GC borrow ' + T.fmt.rate(t.pb.gc_rate); }
    return '';
  }
  function counterpartiesDesk(el, ctx) {
    var st = { dead: false, data: null };
    var exP = panel('Exposure by counterparty', 'this book, in USD'); el.appendChild(exP);
    var exEl = T.h('div'); exP.appendChild(exEl); var exNote = T.h('div', { class: 'hint' }); exP.appendChild(exNote);
    var dirP = panel('Directory', 'real institutions, assumed terms you can edit'); el.appendChild(dirP);
    var dirNote = T.h('div', { class: 'hint' }); dirP.appendChild(dirNote);
    var dirEl = T.h('div'); dirP.appendChild(dirEl);
    var exGrid = T.components.grid.mount(exEl, { tools: false, rows: [], rowKey: function (r) { return r.counterparty_id || r.name; }, empty: 'no exposure: no loans, repos, borrows, lent shares or private loans in this book',
      columns: [
        { key: 'name', label: 'counterparty', cls: 'l', html: function (r) { return '<span class="nm" title="' + T.esc(r.label || r.name) + '">' + T.esc(r.name) + '</span>' + sub(TYPE_SHORT[r.type] || r.type_label || r.type, r.type_label); }, text: function (r) { return r.name; } },
        { key: 'products', label: 'products', cls: 'l', html: function (r) { return (r.products || []).map(function (p) { return '<span title="' + T.esc((p.items || []).join('; ')) + '">' + T.esc(p.product + (p.count > 1 ? ' x' + p.count : '')) + '</span>'; }).join(', '); } },
        { key: 'exp', label: 'exposure', text: function (r) { return m(r.exposure); } },
        { key: 'coll', label: 'collateral held', text: function (r) { return m(r.collateral); } },
        { key: 'net', label: 'net', html: function (r) { return '<span class="' + T.signCls(r.net) + '" title="positive: your unsecured exposure to them; negative: they are exposed to you">' + T.esc(m(r.net)) + '</span>' + ((r.missing || []).length ? ' <span class="why" title="' + T.esc(r.missing.join('; ')) + '">!</span>' : ''); } }
      ] });
    var dirGrid = T.components.grid.mount(dirEl, { tools: true, search: true, csv: 'counterparties.csv', rows: [], rowKey: function (r) { return r.counterparty_id; },
      onClick: clicker(function (act, row) { editDialog(row); }),
      groupBy: function (r) { return r.type_label || r.type; }, empty: 'no counterparties',
      columns: [
        { key: 'name', label: 'counterparty', cls: 'l', html: function (r) { return '<span class="nm" title="' + T.esc(r.name + (r.country ? ' (' + r.country + ')' : '')) + '">' + T.esc(r.name) + '</span>' + (r.assumed ? ' <span class="assumed">assumed</span>' : '') + (r.edited ? ' <small title="edited by you">edited</small>' : ''); }, text: function (r) { return r.name; } },
        { key: 'act', label: ' ', cls: 'l', html: function (r) { return r.type === 'BANK' || r.type === 'REPO_DEALER' || r.type === 'AGENT_LENDER' ? actionsHtml([['edit', 'Edit terms']]) : (r.edit_where ? '<small class="muted" title="' + T.esc(r.edit_where) + '">PB schedule</small>' : ''); }, text: function () { return ''; } },
        { key: 'terms', label: 'terms', cls: 'l', html: function (r) { var s = termsText(r); return '<span class="nm" style="max-width:360px" title="' + T.esc(s) + '">' + T.esc(s) + '</span>'; }, text: termsText },
        { key: 'agreement', label: 'agreement', cls: 'l c-x', html: function (r) { return '<span class="nm" style="max-width:200px" title="' + T.esc(r.agreement || '') + '">' + T.esc(r.agreement || '') + '</span>'; }, text: function (r) { return r.agreement || ''; } }
      ] });
    function editDialog(row) {
      var t = row.terms || {}; var inputs = []; var rows = [];
      function add(section, field, sub, label, value) { var i = T.h('input', { type: 'text', value: value == null ? '' : String(value) }); inputs.push({ section: section, field: field, sub: sub, el: i }); rows.push(T.field(label, i)); }
      if (t.loan) { Object.keys(t.loan.spreads || {}).sort().forEach(function (c) { add('loan', 'spreads', c, c + ' spread (fraction)', t.loan.spreads[c]); }); add('loan', 'max_usd', null, 'maximum USD', t.loan.max_usd); }
      if (t.repo) { add('repo', 'repo_spread', null, 'repo spread (fraction)', t.repo.repo_spread); add('repo', 'reverse_spread', null, 'reverse spread (fraction)', t.repo.reverse_spread);
        var hc = t.repo.haircuts || ((st.data || {}).defaults || {}).haircuts || {}; Object.keys(hc).forEach(function (k) { add('repo', 'haircuts', k, k.toLowerCase() + ' haircut (fraction)', hc[k]); }); add('repo', 'max_usd', null, 'maximum USD', t.repo.max_usd); }
      if (t.lending) { add('lending', 'owner_split', null, 'your share of the fee (0-1)', t.lending.owner_split); add('lending', 'rebate_spread', null, 'rebate spread to SOFR', t.lending.rebate_spread); }
      var box = T.h('div', { class: 'error', hidden: true });
      T.ui.modal({ title: 'Terms of ' + row.name, body: T.h('div', null, T.h('div', { class: 'hint' }, 'Assumed model inputs (fractions: 0.0060 = 0.60 %). A change applies to new trades; open ones keep the terms they were booked at.'), T.form(rows), box),
        actions: [{ label: 'Cancel' }, { label: 'Reset to the seed', onClick: function (mh, b) { b.disabled = true; T.api.patch('/api/credit/counterparties/' + encodeURIComponent(row.counterparty_id), { reset: true }).then(function () { mh.close(); T.ui.toast(row.name + ': terms reset', 'ok'); load(); }, function (e) { b.disabled = false; box.hidden = false; box.textContent = T.api.fail(e, 'terms'); }); } },
          { label: 'Save', cls: 'primary', onClick: function (mh, b) {
            var terms = {};
            inputs.forEach(function (x) { var v = String(x.el.value || '').trim(); if (v === '') { return; } terms[x.section] = terms[x.section] || {}; if (x.sub) { terms[x.section][x.field] = terms[x.section][x.field] || {}; terms[x.section][x.field][x.sub] = v; } else { terms[x.section][x.field] = v; } });
            b.disabled = true;
            T.api.patch('/api/credit/counterparties/' + encodeURIComponent(row.counterparty_id), { terms: terms }).then(function (r) { mh.close(); T.ui.toast(row.name + ': terms saved (' + (r.note || '') + ')', 'ok', 6000); load(); },
              function (e) { b.disabled = false; box.hidden = false; box.textContent = T.api.fail(e, 'terms'); });
          } }] });
    }
    function load() {
      return T.api.get('/api/credit/counterparties', { exposure: 1 }).then(function (d) {
        if (st.dead) { return; }
        st.data = d; dirGrid.update(d.counterparties || []); var ex = d.exposure || {}; exGrid.update(ex.rows || []);
        var tot = ex.totals || {}; exNote.textContent = (ex.rows || []).length ? 'totals: exposure ' + m(tot.exposure) + ', collateral held ' + m(tot.collateral) + ', net ' + m(tot.net) + '. ' + (ex.note || '') : (ex.note || '');
        dirNote.textContent = (d.note || '') + ' Sources: primary dealers - ' + ((d.sources || {}).REPO_DEALER || '') + '.';
      }, function (e) { if (!st.dead) { err(exNote, e, 'counterparties'); } });
    }
    T.poll.page('credit-cps', 30000, load);
    return { unmount: function () { st.dead = true; } };
  }

  /* =========================================================== mount ===================================================== */
  function mount(el, ctx) {
    ensureCss();
    var root = T.h('div', { class: 'cdesk' }); el.appendChild(root);
    var tab = ctx.tab || 'loans';
    if (tab === 'private') {
      var pc = T.components.privatecredit;
      if (!pc || typeof pc.mount !== 'function') { T.notBuilt(root, 'the private-credit desk'); return { unmount: function () {} }; }
      var h = pc.mount(root, { account_id: ctx.account_id, book: ctx.book });
      return { unmount: function () { if (h && h.unmount) { h.unmount(); } } };
    }
    var desk = { loans: loansDesk, repo: repoDesk, lending: lendingDesk, borrows: borrowsDesk, rates: ratesDesk, counterparties: counterpartiesDesk }[tab] || loansDesk;
    return desk(root, ctx) || { unmount: function () {} };
  }

  T.components.creditdesk = { mount: mount, termsText: termsText };
})();
