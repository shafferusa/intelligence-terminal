/* Settings (TERMINAL_DESIGN.md s17.1 #/settings[/section]; WP-12).  Sections: General (theme, hotkeys, participation
 * rate, session overrides, defaults), Accounts (per-account sweep switch / buffer, PB financing switch and limit ->
 * PATCH /api/accounts/{id}), Books (names, starting capitals for NEW lifetimes, the PB/OTC and QP thresholds ->
 * PATCH /api/settings), Lifetimes (Reset lifetime + past lifetimes: mounts components/lifetimes.js, WP-23), Prime
 * brokers (fee schedules: mounts components/pbschedule.js per PB account, WP-07), Private defaults (fee %, settlement
 * lag, lock-up), Data Status (each provider's status and delay, workers, jobs, news - from GET /api/health), Unsupported
 * (the s20 list from GET /api/unsupported), and the legacy link (port 8766).  A missing component shows NOT YET BUILT.
 */
(function () {
  'use strict';
  var T = window.T;
  var SECTIONS = [['general', 'General'], ['accounts', 'Accounts'], ['books', 'Books'], ['lifetimes', 'Lifetimes'], ['pb', 'Prime brokers'], ['private', 'Private defaults'], ['data', 'Data Status'], ['unsupported', 'Unsupported']];
  function render(el, route) {
    var section = route.tab || 'general';
    T.pageHead(el, 'Settings', '', [T.h('a', { class: 'legacy', href: 'http://127.0.0.1:8766/', target: '_blank', rel: 'noopener' }, 'legacy FinSim simulation (8766, read-only intent)')]);
    var nav = T.h('div', { class: 'settings-nav' });
    SECTIONS.forEach(function (s) { nav.appendChild(T.h('button', { class: 'tab' + (s[0] === section ? ' active' : ''), onclick: function () { T.router.go('#/settings/' + s[0]); } }, s[1])); });
    el.appendChild(nav);
    var body = T.h('div'); el.appendChild(body);
    var settings = null;
    function patch(obj, label) { return T.api.patch('/api/settings', obj).then(function (r) { settings = r.settings || r; T.ui.toast((label || 'settings') + ' saved', 'ok'); T.poll.kick('books'); }, function (e) { T.api.fail(e, label || 'settings'); }); }
    function moneyInput(v) { return T.h('input', { type: 'text', value: v == null ? '' : String(v).replace(/,/g, ''), style: 'width:180px' }); }
    function get(k, d) { var v = settings && settings[k]; return v == null ? d : v; }
    function general() {
      var theme = T.h('select'); ['dark', 'light'].forEach(function (t) { theme.appendChild(T.h('option', { value: t, selected: T.theme.get() === t }, t)); });
      theme.addEventListener('change', function () { T.theme.set(theme.value); });
      var part = moneyInput(get('participation_rate', '0.10'));
      var over = T.h('textarea', { rows: 3, style: 'width:100%', placeholder: '{"US_EQUITY": {"close": "13:00"}}' }); over.value = JSON.stringify(get('session_overrides', {}), null, 0);
      body.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'look & keys'), T.form([T.field('theme', theme)]), T.h('div', { class: 'hint' }, 'Keyboard map: press ? on any page.'), T.h('div', { class: 'kv' }, T.keys.map().slice(0, 16).reduce(function (acc, r) { acc.push(T.h('div', null, r[0])); acc.push(T.h('div', null, r[1])); return acc; }, []))));
      body.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'execution defaults'), T.form([T.field('participation rate', part, 'share of each new 1-minute bar\'s volume an order may take'), T.field('session overrides (JSON)', over, 'per-group overrides, e.g. an early close the NYSE file lacks'), ['', T.h('button', { class: 'btn', onclick: function () {
        var o; try { o = over.value.trim() ? JSON.parse(over.value) : {}; } catch (e) { T.ui.toast('session overrides: invalid JSON', 'err'); return; }
        patch({ participation_rate: part.value.trim(), session_overrides: o }, 'execution defaults');
      } }, 'Save')]]), T.convNote(['PARTICIPATION', 'SESSION', 'CME_CAL'])));
    }
    function accounts() {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'per-account sweep and financing switches'));
      var rows = (T.state.accounts || []).filter(function (a) { return a.kind !== 'TREASURY'; });
      if (!rows.length) { p.appendChild(T.h('div', { class: 'empty' }, 'no accounts loaded')); }
      rows.forEach(function (a) {
        var sw = T.h('input', { type: 'checkbox' }); sw.checked = a.sweep_out == null ? true : !!a.sweep_out;
        var buf = moneyInput(T.money(a, 'sweep_buffer') === 'n/a' ? '' : T.money(a, 'sweep_buffer'));
        var fin = T.h('input', { type: 'checkbox' }); fin.checked = !!a.pb_financing;
        var lim = moneyInput(T.money(a, 'pb_limit') === 'n/a' ? '' : T.money(a, 'pb_limit'));
        var save = T.h('button', { class: 'btn small', onclick: function () {
          var o = { sweep_out: sw.checked, sweep_buffer: buf.value.trim() || '0' };
          if (a.kind === 'PB') { o.pb_financing = fin.checked; o.pb_limit = lim.value.trim() || '0'; }
          T.api.patch('/api/accounts/' + encodeURIComponent(a.account_id), o).then(function () { T.ui.toast(a.name + ' saved', 'ok'); T.poll.kick('accounts'); }, function (e) { T.api.fail(e, 'account settings'); });
        } }, 'Save');
        var f = [T.field('sweep-out', sw), T.field('buffer (USD)', buf)];
        if (a.kind === 'PB') { f.push(T.field('PB financing', fin)); f.push(T.field('PB limit (USD)', lim)); }
        f.push(['', save]);
        p.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, a.name || a.account_id, T.h('small', null, a.account_id)), T.form(f)));
      });
      p.appendChild(T.convNote(['SWEEP']));
      body.appendChild(p);
    }
    function books() {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'books', T.h('small', null, 'names and starting capitals apply to NEW lifetimes; the live lifetime\'s history is never rewritten')));
      var defaults = get('book_defaults', {}); var inputs = {};
      (T.state.books || []).forEach(function (b) {
        var d = defaults[b.book_code] || {};
        var nm = T.h('input', { type: 'text', value: d.name || b.name || '', style: 'width:220px' });
        var cap = moneyInput(d.starting_capital != null ? d.starting_capital : (b.lifetime && b.lifetime.starting_capital) || '');
        inputs[b.book_code] = { name: nm, cap: cap };
        p.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, b.book_code + ' . ' + (b.profile || ''), T.h('small', null, b.lifetime ? 'open lifetime ' + b.lifetime.lifetime_id + ' started ' + (b.lifetime.started || '') : '')), T.form([T.field('name', nm), T.field('starting capital (USD)', cap)])));
      });
      var thr = moneyInput(get('pb_otc_threshold', '25000000.00'));
      var qp = get('qp_threshold', {}); var qpI = moneyInput(qp.INDIVIDUAL != null ? qp.INDIVIDUAL : '5000000.00'), qpF = moneyInput(qp.FUND != null ? qp.FUND : '25000000.00');
      p.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'thresholds'), T.form([T.field('PB / OTC eligibility (book NAV, USD)', thr), T.field('QP threshold - Personal (USD)', qpI), T.field('QP threshold - Fund (USD)', qpF)])));
      p.appendChild(T.h('button', { class: 'btn primary', onclick: function () {
        var bd = {}; Object.keys(inputs).forEach(function (k) { bd[k] = { name: inputs[k].name.value.trim(), starting_capital: inputs[k].cap.value.trim() }; });
        patch({ book_defaults: bd, pb_otc_threshold: thr.value.trim(), qp_threshold: { INDIVIDUAL: qpI.value.trim(), FUND: qpF.value.trim(), INSTITUTION: '0' } }, 'books');
      } }, 'Save books'));
      p.appendChild(T.convNote(['BOOKS', 'ELIGIBILITY', 'QP']));
      body.appendChild(p);
    }
    function lifetimes() {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'lifetimes', T.h('small', null, 'Reset lifetime (typed RESET) and the past lifetimes with History links')));
      var m = T.h('div'); p.appendChild(m);
      var book = T.book.current();
      T.mountOptional('lifetimes', m, { book: T.book.code, book_name: book && book.name }, 'components/lifetimes.js (WP-23): the Reset lifetime dialog and the past-lifetimes table');
      if (!T.components.lifetimes) {
        var l = T.h('div'); p.appendChild(l);
        if (book) {
          T.api.get('/api/books/' + encodeURIComponent(book.book_code) + '/lifetimes', null, { nobook: true }).then(function (r) {
            var rows = Array.isArray(r) ? r : (r.lifetimes || []);
            T.components.grid.mount(l, { tools: false, rows: rows, empty: 'no lifetimes listed', columns: [{ key: 'lifetime_id', label: 'lifetime', cls: 'l' }, { key: 'name', label: 'name', cls: 'l', text: function (x) { return x.name || ''; } }, { key: 'status', label: 'status', cls: 'l' }, { key: 'started_ts', label: 'started', cls: 'l', text: function (x) { return x.started || T.fmt.date(x.started_ts); } }, { key: 'ended_ts', label: 'ended', cls: 'l', text: function (x) { return x.ended || (x.ended_ts ? T.fmt.date(x.ended_ts) : ''); } }, { key: 'starting_capital', label: 'starting capital', text: function (x) { return T.money(x, 'starting_capital'); } }, { key: 'final_nav', label: 'final NAV', text: function (x) { return T.money(x, 'final_nav'); } }, { key: 'final_pnl', label: 'P&L', html: function (x) { return '<span class="' + T.sign(x, 'final_pnl') + '">' + T.esc(T.money(x, 'final_pnl')) + '</span>'; } }, { key: 'reason', label: 'reason', cls: 'l', text: function (x) { return x.reason || ''; } }, { key: 'integrity_ok', label: 'integrity', cls: 'l', text: function (x) { return x.integrity_ok == null ? '' : (x.integrity_ok ? 'ok' : 'FAILED'); } }, { key: 'h', label: 'history', cls: 'l', html: function (x) { return x.status === 'CLOSED' ? '<a href="#/h/' + T.esc(encodeURIComponent(x.lifetime_id)) + '/portfolio">History</a>' : ''; }, text: function () { return ''; } }] });
          }, function (e) { T.errorBox(l, e, 'lifetimes'); });
        }
      }
      p.appendChild(T.convNote(['LIFETIME']));
      body.appendChild(p);
    }
    function pb() {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'prime brokers', T.h('small', null, 'editable fee schedules (modelled assumptions); before / after confirmation')));
      var pbs = (T.state.accounts || []).filter(function (a) { return a.kind === 'PB'; });
      if (!pbs.length) { p.appendChild(T.h('div', { class: 'empty' }, 'no prime-broker account in this lifetime (sidebar: + Open prime broker account)')); }
      pbs.forEach(function (a) {
        var s = T.h('div', { class: 'section' }, T.h('h2', null, a.name || a.account_id, T.h('small', null, a.broker || '')));
        var m = T.h('div'); s.appendChild(m); p.appendChild(s);
        T.mountOptional('pbschedule', m, { account_id: a.account_id, broker: a.broker }, 'components/pbschedule.js (WP-07): the per-PB fee schedule form');
        if (!T.components.pbschedule) { T.api.get('/api/accounts/' + encodeURIComponent(a.account_id) + '/schedule').then(function (sc) { m.appendChild(T.h('pre', null, JSON.stringify(sc, null, 1))); }, function () { /* the NOT YET BUILT block stands */ }); }
      });
      body.appendChild(p);
    }
    function priv() {
      var d = get('private_defaults', {});
      var fee = moneyInput(d.fee_pct != null ? d.fee_pct : '0.03'), lag = moneyInput(d.settle_days != null ? d.settle_days : '30'), lock = moneyInput(d.lockup_months != null ? d.lockup_months : '6');
      body.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'private markets defaults', T.h('small', null, 'the ticket starts from these; each trade may override them')), T.form([T.field('placement fee (fraction)', fee), T.field('settlement lag (calendar days)', lag), T.field('lock-up (months)', lock), ['', T.h('button', { class: 'btn', onclick: function () { patch({ private_defaults: { fee_pct: fee.value.trim(), settle_days: lag.value.trim(), lockup_months: lock.value.trim() } }, 'private defaults'); } }, 'Save')]]), T.convNote(['PRIVATE_TRADE', 'PRIVATE_MARK', 'QP'])));
    }
    function data() {
      var h = T.state.health || {};
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'Data Status', T.h('small', null, 'each provider\'s status and delay; nothing is hidden')));
      var DELAY = { yahoo: 'last trade, up to 15 min delayed (equities / ETFs / futures / FX / indices; 1-minute bars)', cboe: 'option chains and NBBO, ~15 min delayed', treasury: 'daily par curve (slow; cached for the day)', fred: 'daily rates and OAS (published next morning)', finra: 'daily short volume (borrow heuristic)', frankfurter: 'ECB reference rates (fallback FX)', fed: 'Federal Reserve press feed (hourly)', newspaper: 'the repository\'s own editions (index.json)' };
      var ps = h.providers || {};
      if (!Object.keys(ps).length) { p.appendChild(T.h('div', { class: 'empty' }, 'no provider state in /api/health yet')); }
      Object.keys(ps).forEach(function (k) {
        var s = ps[k] || {};
        p.appendChild(T.h('div', { class: 'ds-row' }, T.h('span', null, k), T.h('span', { class: 'st ' + (s.ok === false ? 'bad' : (s.ok ? 'ok' : 'na')) }, s.ok === false ? 'DOWN' : (s.ok ? 'OK' : 'n/a')), T.h('span', null, (DELAY[k] || '') + (s.last_ok_ts ? ' . last ok ' + T.fmt.time(s.last_ok_ts) : '') + (s.last_error ? ' . ' + s.last_error : '') + (s.consecutive_errors ? ' . ' + s.consecutive_errors + ' consecutive errors' : '') + (s.down_until_ts ? ' . paused until ' + T.fmt.time(s.down_until_ts) : '') + (s.refused_ua ? ' . UA refused' : '')), T.h('span', { class: 'muted' }, s.requests_today != null ? s.requests_today + ' req today' : '')));
      });
      var ws = h.workers || {};
      p.appendChild(T.h('h2', null, 'workers'));
      Object.keys(ws).forEach(function (k) { var s = ws[k] || {}; p.appendChild(T.h('div', { class: 'ds-row' }, T.h('span', null, k), T.h('span', { class: 'st ' + (s.alive === false ? 'bad' : (s.stalled ? 'bad' : 'ok')) }, s.alive === false ? 'DEAD' : (s.stalled ? 'STALLED' : 'alive')), T.h('span', null, (s.last_tick_ts ? 'last tick ' + T.fmt.time(s.last_tick_ts) : '') + (s.errors_1h ? ' . ' + s.errors_1h + ' errors/h' : '') + (s.last_error ? ' . ' + s.last_error : '')), T.h('span'))); });
      var lf = h.lifecycle || {};
      p.appendChild(T.h('h2', null, 'lifecycle'));
      p.appendChild(T.h('div', { class: 'ds-row' }, T.h('span', null, 'jobs'), T.h('span', { class: 'st ' + ((lf.failing || []).length ? 'bad' : 'ok') }, (lf.failing || []).length ? 'FAILING' : 'ok'), T.h('span', null, 'due ' + (lf.due == null ? '?' : lf.due) + ((lf.failing || []).length ? ' . failing: ' + lf.failing.map(function (j) { return j.name || j.job || j; }).join(', ') : '')), T.h('span')));
      if (h.news) { p.appendChild(T.h('div', { class: 'ds-row' }, T.h('span', null, 'news'), T.h('span', { class: 'st ok' }, 'fetched'), T.h('span', null, Object.keys(h.news.fetched || {}).map(function (k) { return k + ' ' + T.fmt.time(h.news.fetched[k]); }).join(' . ') + (h.news.injection_flags ? ' . ' + h.news.injection_flags + ' injection flag(s)' : '')), T.h('span'))); }
      p.appendChild(T.h('h2', null, 'server'));
      p.appendChild(T.kv([['app', (h.app || '') + ' ' + (h.version || '')], ['schema', h.schema], ['pid', h.pid], ['uptime', T.fmt.age(h.uptime_s)], ['db', (h.db_path || '') + (h.db_size ? ' (' + T.fmt.qty(h.db_size) + ' bytes)' : '')], ['state version', h.state_version], ['offline mode', h.offline ? 'YES - fake data' : 'no'], ['calendar', h.calendar ? (h.calendar.file || '') + ' (fallback years ' + (h.calendar.fallback_years || []).join(', ') + ')' : ''], ['lock', h.lock ? (h.lock.path || '') + ' pid ' + h.lock.pid : ''], ['UI pollers', JSON.stringify(T.poll.status())]]));
      p.appendChild(T.convNote(['STALE', 'COMPUTED', 'PAPER']));
      body.appendChild(p);
    }
    function unsupported() {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'Unsupported in this build', T.h('small', null, 'shown as such everywhere; never priced with invented data')));
      var g = T.h('div'); p.appendChild(g); body.appendChild(p);
      T.api.get('/api/unsupported').then(function (r) {
        var rows = Array.isArray(r) ? r : (r.items || []);
        T.components.grid.mount(g, { tools: false, rows: rows, empty: 'the server lists nothing as unsupported', columns: [{ key: 'item', label: 'item', cls: 'l', text: function (x) { return x.item || x.name || ''; } }, { key: 'state', label: 'state', cls: 'l', html: function (x) { return '<span class="state-tag ' + T.esc(x.state || '') + '">' + T.esc(String(x.state || '').replace(/_/g, ' ')) + '</span>'; }, text: function (x) { return x.state || ''; } }, { key: 'wording', label: 'how it is shown', cls: 'l', text: function (x) { return x.wording || x.how || ''; } }, { key: 'where', label: 'where', cls: 'l', text: function (x) { return x.where || ''; } }] });
      }, function (e) { T.errorBox(g, e, 'unsupported'); });
    }
    function paint() {
      T.clear(body);
      var fn = { general: general, accounts: accounts, books: books, lifetimes: lifetimes, pb: pb, private: priv, data: data, unsupported: unsupported }[section] || general;
      fn();
    }
    T.api.get('/api/settings').then(function (s) { settings = s.settings || s; paint(); }, function (e) { settings = {}; T.api.fail(e, 'settings'); paint(); });
    var off = T.state.on('health', function () { if (section === 'data') { paint(); } });
    T.pages.settings.unmount = function () { off(); T.pages.settings.unmount = null; };
  }
  T.pages.settings = { render: render, unmount: null, SECTIONS: SECTIONS };
})();
