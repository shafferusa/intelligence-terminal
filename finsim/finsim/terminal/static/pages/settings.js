/* Settings (TERMINAL_DESIGN.md s17.1 #/settings[/section]; WP-12; Build 2 P01 2026-10-21).  Sections: General (theme,
 * the keyboard map in force, participation rate), Keyboard (the owner's keys, 2026-10-21 17:10: every action by group with
 * its key; Change = press the new key, a key in use asks to swap or cancel, Clear, Reset, Reset all, refused / noted browser
 * keys, Test a key; saved in the app with PATCH /api/settings {keybinds, keybind_defaults}; docs/KEYBINDS.md), Accounts
 * (per-account sweep switch / buffer, PB financing switch and limit ->
 * PATCH /api/accounts/{id}), Prime brokers (the eligibility sentence, the signed agreements, each PB's fee schedule via
 * components/pbschedule.js), Books (names, starting capitals for NEW lifetimes, the PB/OTC and QP thresholds -> PATCH
 * /api/settings, which renames books through book.update_settings), Lifetimes (components/lifetimes.js), Calendar (NYSE
 * holidays / early closes for this year and next, the session hours per group, the session overrides form), Margins &
 * limits (the static futures margin table and position limits with editable overrides - assumed values), Private
 * defaults, Market data (UI-20, 1.0.6; it was Data Status - #/settings/data still opens it: GET /api/health status first - each
 * market-data provider with what it supplies and its delay, the background refreshers, the workers, the next lifecycle job - then
 * the market-data and execution-simulation preferences, linked where they are edited (participation rate: General; session
 * overrides: Calendar; futures margins: Margins & limits), then the server and database file + WAL) and Unsupported (GET
 * /api/unsupported, four wrapped columns).  No news: the news feature and its sources are gone (owner directive section 2).
 *
 * Every section reads what it shows from the server BEFORE it paints (SHELL-08: correct on a direct load and after F5) and
 * repaints when the pollers bring a change (T.state.on, unsubscribed on unmount) - except while a form on it has unsaved
 * edits.  Nothing is computed here: every figure is a field of an API response.  ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  var SECTIONS = [['general', 'General'], ['keyboard', 'Keyboard'], ['accounts', 'Accounts'], ['pb', 'Prime brokers'], ['books', 'Books'], ['lifetimes', 'Lifetimes'], ['calendar', 'Calendar'],
    ['margins', 'Margins & limits'], ['private', 'Private defaults'], ['market-data', 'Market data'], ['unsupported', 'Unsupported']];
  var GROUP_LABEL = { US_EQUITY: 'US equities & ETFs', US_EQUITY_OPTIONS: 'US equity options', US_INDEX_OPTIONS: 'US index options', US_BONDS: 'US bonds',
    CME_EQUITY_INDEX: 'CME equity index futures', CME_RATES: 'CME rates futures', NYMEX_ENERGY: 'NYMEX energy futures', COMEX_METALS: 'COMEX metals futures',
    CBOT_GRAINS: 'CBOT grains futures', CME_FX: 'CME FX futures', FX_SPOT: 'FX spot', FOREIGN: 'Foreign listings', PRIVATE_MARKETS: 'Private markets', '*': 'every group' };
  var PROVIDER_WHAT = { yahoo: 'last trade, up to 15 min delayed (equities / ETFs / futures / FX / indices; 1-minute bars)', cboe: 'option chains and NBBO, about 15 min delayed',
    treasury: 'daily par yield curve (cached for the day)', fred: 'daily rates and credit spreads (published the next morning)', finra: 'daily short volume (borrow estimate)',
    frankfurter: 'ECB reference rates (fallback FX)', fiscaldata: 'Treasury auctions and outstanding securities',
    treasurydirect: 'FedInvest Treasury prices (daily)', nyfed: 'SOFR fallback and the SOFR Index (daily)', ecb: 'ECB deposit rate, ESTR and its index, the euro-area AAA curves (daily)',
    mof: 'JGB yields (daily)', boe: 'SONIA and the UK OIS curve (daily, Bank of England)',
    debt: 'debt refresher (every 30 min): the Treasury list, FedInvest prices, ECB and MOF yields', otc_options: 'OTC option inputs: daily closes and chains (every 15 s while contracts are open)' };
  function plabel(p) { return T.providerLabel ? T.providerLabel(p) : String(p || '').replace(/_/g, ' '); }
  function glabel(g) { return GROUP_LABEL[g] || String(g || '').replace(/_/g, ' '); }
  function wrapTd(text, cls) { return T.h('td', { class: cls || 'l', style: 'white-space:normal;height:auto;padding:4px 8px;vertical-align:top' }, text); }
  function th(text, cls) { return T.h('th', { class: cls || 'l' }, text); }
  function table(head, rows, opts) {
    var t = T.h('table', { style: (opts && opts.fixed) ? 'table-layout:fixed' : null });
    if (opts && opts.cols) { var cg = T.h('colgroup'); opts.cols.forEach(function (w) { cg.appendChild(T.h('col', { style: 'width:' + w })); }); t.appendChild(cg); }
    t.appendChild(T.h('thead', null, T.h('tr', null, head)));
    var tb = T.h('tbody'); rows.forEach(function (r) { tb.appendChild(r); }); t.appendChild(tb);
    return t;
  }
  function moneyInput(v, w) { return T.h('input', { type: 'text', value: v == null ? '' : String(v).replace(/,/g, ''), style: 'width:' + (w || '160px'), spellcheck: 'false' }); }
  function bookRow(books, code) { var r = null; (books || []).forEach(function (b) { if (b && b.book_code === code) { r = b; } }); return r; }
  function profileLabel(b) {
    if (!b) { return ''; }
    if (b.profile_label) { return b.profile_label; }
    var bp = (T.state.state && T.state.state.book_profiles) || {};
    return bp[b.profile] || b.profile || '';
  }

  function render(el, route) {
    var section = route.tab === 'data' ? 'market-data' : (route.tab || 'general');      /* the old Data Status address */
    if (!SECTIONS.some(function (s) { return s[0] === section; })) { section = 'general'; }
    T.pageHead(el, 'Settings', '');
    var nav = T.h('div', { class: 'settings-nav' });
    SECTIONS.forEach(function (s) { nav.appendChild(T.h('button', { class: 'tab' + (s[0] === section ? ' active' : ''), onclick: function () { T.router.go('#/settings/' + s[0]); } }, s[1])); });
    el.appendChild(nav);
    var body = T.h('div'); el.appendChild(body);
    body.appendChild(T.h('div', { class: 'empty' }, 'loading ...'));
    var D = { settings: null, accounts: null, books: null, extra: {}, error: null };
    var dirty = false, dead = false, sig = '', handles = [];
    body.addEventListener('input', function () { dirty = true; });
    body.addEventListener('change', function () { dirty = true; });

    function bookCode() { return T.book && T.book.code; }
    function signature() {
      if (section === 'accounts' || section === 'pb') { return JSON.stringify((T.state.accounts || []).map(function (a) { return [a.account_id, a.status, a.sweep_out, a.sweep_buffer, a.pb_financing, a.pb_limit]; })); }
      if (section === 'books') { return JSON.stringify((T.state.books || []).map(function (b) { return [b.book_code, b.name, b.lifetime && b.lifetime.lifetime_id, b.eligibility && b.eligibility.can_onboard]; })); }
      return '';
    }
    function fetchCore() {
      var code = bookCode();
      var jobs = [
        T.api.get('/api/settings').then(function (s) {
          D.settings = s.settings || s;
          /* the owner's keys as the server has them now (another window may have changed them) */
          if (T.keys && T.keys.load && !(T.keys.capturing && T.keys.capturing())) { T.keys.load((D.settings && D.settings.keybinds) || {}); }
        }),
        T.api.get('/api/accounts').then(function (a) { D.accounts = (Array.isArray(a) ? a : (a && a.accounts) || []).filter(function (x) { return x.status !== 'CLOSED'; }); }),
        T.api.get('/api/books', null, { nobook: true }).then(function (b) { D.books = Array.isArray(b) ? b : (b && b.books) || []; })
      ];
      if (section === 'calendar') {
        var y = new Date().getFullYear();
        try { var ny = T.state.health && T.state.health.clock && T.state.health.clock.trading_day; if (ny) { y = Number(String(ny).slice(0, 4)); } } catch (e) { /* the browser year */ }
        jobs.push(T.api.get('/api/calendar', { year: y }).then(function (c) { D.extra.cal0 = c; }));
        jobs.push(T.api.get('/api/calendar', { year: y + 1 }).then(function (c) { D.extra.cal1 = c; }));
        jobs.push(T.api.get('/api/sessions').then(function (s) { D.extra.sessions = s; }));
      }
      if (section === 'unsupported') { jobs.push(T.api.get('/api/unsupported').then(function (r) { D.extra.unsupported = Array.isArray(r) ? r : (r.items || []); })); }
      return Promise.all(jobs).then(function () {
        if (section !== 'pb') { return null; }
        /* the signed agreements of the open lifetime (PO-16) */
        var row = bookRow(D.books, code); var id = row && row.lifetime && row.lifetime.lifetime_id;
        return id ? T.api.get('/api/lifetimes/' + encodeURIComponent(id), null, { nobook: true }).then(function (v) { D.extra.lifetime = v; }) : null;
      });
    }
    function load() {
      D.error = null;
      return fetchCore().then(null, function (e) { D.error = T.api.fail(e, 'settings'); }).then(function () { if (!dead) { dirty = false; paint(); } });
    }
    function patch(obj, label, after) {
      return T.api.patch('/api/settings', obj).then(function (r) {
        D.settings = r.settings || r; dirty = false;
        T.ui.toast((label || 'settings') + ' saved', 'ok');
        if (T.poll) { T.poll.kick('books'); }
        /* the BOOKS / QP / ELIGIBILITY sentences follow the saved settings (SHELL-22) */
        T.api.get('/api/state', null, { nobook: true }).then(function (s) { T.state.conventions = s.conventions || T.state.conventions; T.state.set('state', s); if (!dead) { load(); } }, function () { if (!dead) { load(); } });
        if (after) { after(r); }
      }, function (e) { T.api.fail(e, label || 'settings'); });
    }
    function get(k, d) { var v = D.settings && D.settings[k]; return v == null ? d : v; }

    /* ---------------------------------------------------------------- General ---------------------------------- */
    function general() {
      var theme = T.h('select', { style: 'width:160px' }); ['dark', 'light'].forEach(function (t) { theme.appendChild(T.h('option', { value: t, selected: T.theme.get() === t }, t)); });
      theme.addEventListener('change', function () { T.theme.set(theme.value); dirty = false; });
      body.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'look'), T.form([T.field('theme', theme)])));
      /* the keys IN FORCE (the F-key defaults + the owner's changes): the same map as the '?' overlay */
      var keys = T.h('div', { class: 'panel' }, T.h('h2', null, 'keyboard', T.h('small', null, 'the keys in force'),
        T.h('button', { class: 'btn small', style: 'margin-left:10px', onclick: function () { T.router.go('#/settings/keyboard'); } }, 'Change keys')));
      if (T.help && typeof T.help.renderKeys === 'function') { T.help.renderKeys(keys); }
      else { keys.appendChild(table([th('key'), th('what it does')], T.keys.map().map(function (r) { return T.h('tr', null, wrapTd(r[0]), wrapTd(r[1])); }))); }
      body.appendChild(keys);
      var part = moneyInput(get('participation_rate', '0.10'), '120px');
      body.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'execution defaults'),
        T.form([T.field('participation rate', part, 'share of each new 1-minute bar\'s volume an order may take'), ['', T.h('button', { class: 'btn', onclick: function () { patch({ participation_rate: part.value.trim() }, 'execution defaults'); } }, 'Save')]]),
        T.h('div', { class: 'hint' }, 'Session overrides (an early close the NYSE file lacks) are in Settings > Calendar.'),
        T.convNote(['PARTICIPATION'])));
    }

    /* ---------------------------------------------------------------- Keyboard (owner 2026-10-21 17:10) -------- */
    /* Every action by group with the key in force (keys.js registry).  Change = press the new key (Esc cancels,
       Backspace / Delete leaves it without a key); a key another action can act on at the same time asks "swap or cancel"
       (one key never does two things); Clear; Reset (one) and Reset all (to the F-key defaults); keys a browser never hands
       to a page are refused with the reason, keys some browsers keep (F11, F12, Ctrl+F5 ...) carry a note; Test a key
       shows exactly what the app receives.  Saved in the app: PATCH /api/settings {keybinds: the changes, keybind_defaults:
       every default} (journaled; api.py checks the result), then T.keys.load(answer.keybinds) - every page follows. */
    var kbRelease = [];
    function kbStop() { var l = kbRelease; kbRelease = []; l.forEach(function (f) { try { f(); } catch (e) { /* already released */ } }); }
    function kbLabel(id) { var a = T.keys.get(id); return a ? a.group + ': ' + a.label : 'another action'; }
    function kbSaid(combo) { return combo ? T.keys.display(combo) : 'no key'; }
    function kbNotes(a) {
      var K = T.keys, k = K.binding(a.id), notes = [];
      var hold = K.holdingFiles(a.id);
      if (hold.length) { notes.push('fixed for now: ' + K.fileNames(hold) + (hold.length === 1 ? ' still uses' : ' still use') + ' the default key'); }
      else if (K.overridden(a.id)) { notes.push('changed (default ' + kbSaid(a.key) + ')'); }
      if (K.lost(a.id)) { notes.push('its default ' + kbSaid(a.key) + ' is the key of ' + kbLabel(K.lost(a.id)) + ' now'); }
      if (k && K.mayKeep(k)) { notes.push(K.mayKeep(k) + ': try it in Test a key below'); }
      else if (k && K.textEdit && K.textEdit(k)) { notes.push('in a text box it stays ' + K.textEdit(k) + '; the action runs everywhere else'); }
      else if (k && K.browserUse(k)) { notes.push('takes over the browser\'s ' + K.browserUse(k)); }
      if (a.scope === 'dialog') { notes.push('acts only while a dialog or the keyboard help is open'); }
      return notes.join(' . ');
    }
    function kbRow(a) {
      var K = T.keys, k = K.binding(a.id), fixed = !K.rebindable(a.id), changed = K.overridden(a.id) || !!K.lost(a.id);
      return T.h('tr', { class: changed ? 'kb-changed' : null, 'data-action': a.id },
        T.h('td', { class: 'l what' }, a.label),
        T.h('td', { class: 'l key', html: K.kbd(k) }),
        T.h('td', { class: 'l note' }, kbNotes(a)),
        T.h('td', { class: 'l acts' },
          T.h('button', { class: 'btn small', disabled: fixed, title: fixed ? 'fixed for now' : 'press the new key', onclick: function () { kbChange(a); } }, 'Change'),
          T.h('button', { class: 'btn small', disabled: fixed || !k, title: 'leave this action without a key', onclick: function () { kbPropose(a, null, null); } }, 'Clear'),
          T.h('button', { class: 'btn small', disabled: fixed || !changed, title: 'back to its default: ' + kbSaid(a.key), onclick: function () { kbPropose(a, a.key, null, 'reset'); } }, 'Reset')));
    }
    /* "Change": a dialog that takes the next key press (T.keys.capture: nothing else runs meanwhile) */
    function kbChange(a) {
      var K = T.keys, cur = K.binding(a.id), release = null;
      var live = T.h('div', { class: 'kb-capture', 'aria-live': 'polite' }, 'press the new key');
      var msg = T.h('div', { class: 'kb-msg' }), ask = T.h('div', { class: 'kb-ask' });
      var m = T.ui.modal({
        title: 'Key for: ' + a.label, sticky: true,
        body: T.h('div', null, T.h('div', { class: 'muted' }, a.group + ' . now ' + kbSaid(cur) + ' . default ' + kbSaid(a.key)),
          live, T.h('div', { class: 'hint' }, 'Esc cancels . Backspace or Delete leaves it without a key . F-keys and Ctrl / Alt keys also work while the cursor is in a box'), msg, ask),
        actions: [{ label: 'Cancel', onClick: function (h) { h.close(); } }],
        onClose: function () { if (release) { release(); release = null; } }
      });
      function pause() { if (release) { release(); release = null; } }
      function listen(note) {
        T.clear(ask); msg.textContent = note || '';
        if (!note) { live.textContent = 'press the new key'; }
        pause();
        release = K.capture(function (e, info) {
          if (info.modifier) { live.textContent = [info.ctrl && 'Ctrl', info.alt && 'Alt', info.shift && 'Shift'].filter(Boolean).join('+') + '+ ...'; return; }
          if (e.repeat) { return; }
          if (info.combo === 'Escape') { m.close(); return; }
          if (info.combo === 'Backspace' || info.combo === 'Delete') { pause(); kbPropose(a, null, ctx); return; }
          if (!info.combo) { live.textContent = info.raw || '?'; msg.textContent = 'not a key the app binds: ' + info.why; return; }
          live.innerHTML = K.kbd(info.combo);
          var why = K.refused(info.combo);
          if (why) { msg.textContent = K.display(info.combo) + ' cannot be used: ' + why + '. Press another key.'; return; }
          pause(); kbPropose(a, info.combo, ctx);
        });
      }
      var ctx = { modal: m, msg: msg, ask: ask, retry: listen, cancel: true };     /* the dialog has its own Cancel */
      listen();
    }
    /* a dialog for the swap question of Reset (Change asks in its own dialog) */
    function kbAsk(a) {
      var msg = T.h('div', { class: 'kb-msg' }), ask = T.h('div', { class: 'kb-ask' });
      var m = T.ui.modal({ title: 'Key for: ' + a.label, sticky: true, body: T.h('div', null, msg, ask) });
      return { modal: m, msg: msg, ask: ask, retry: null };
    }
    /* give action `a` the key `combo` (null = no key): saved at once when no other action can act on that key; else
       "swap or cancel" - the other action(s) take a's old key (or none, when it would clash with a third one) */
    function kbPropose(a, combo, ctx, verb) {
      var K = T.keys, cur = K.binding(a.id), said = kbSaid(combo);
      var changes = {}; changes[a.id] = combo;
      if (combo === cur) {
        if (ctx) { ctx.modal.close(); }
        if (verb === 'reset' && K.overridden(a.id)) { kbSave(changes, a.label + ': back to ' + said); } else { T.ui.toast(a.label + ': ' + said + ' (unchanged)', '', 2000); }
        return;
      }
      var hs = combo ? K.holders(a.id, combo) : [];
      if (!hs.length) {
        if (ctx) { ctx.modal.close(); }
        kbSave(changes, a.label + ': ' + (verb === 'reset' ? 'back to ' : '') + said + (combo && K.mayKeep(combo) ? ' (' + K.mayKeep(combo) + ')' : ''));
        return;
      }
      ctx = ctx || kbAsk(a);
      T.clear(ctx.ask);
      var names = hs.map(kbLabel).join(' and ');
      var fixedH = hs.filter(function (h) { return !K.rebindable(h); });
      if (fixedH.length) {
        var note = said + ' is the key of ' + names + ', which cannot change yet (' + K.fileNames(K.holdingFiles(fixedH[0])) + ' still use' + (K.holdingFiles(fixedH[0]).length === 1 ? 's' : '') + ' its default key).';
        if (ctx.retry) { ctx.retry(note + ' Press another key.'); } else { ctx.msg.textContent = note; ctx.ask.appendChild(T.h('div', { class: 'row' }, T.h('button', { class: 'btn', onclick: function () { ctx.modal.close(); } }, 'Close'))); }
        return;
      }
      var map = K.bindings(); map[a.id] = combo;
      hs.forEach(function (h) { map[h] = cur; changes[h] = cur; });
      var left = !!cur && K.clashes(map).some(function (c) { return hs.indexOf(c[1]) >= 0 || hs.indexOf(c[2]) >= 0; });
      if (left) { hs.forEach(function (h) { map[h] = null; changes[h] = null; }); }
      var swapWords = hs.map(function (h) { return T.keys.get(h).label + (cur && !left ? ' gets ' + kbSaid(cur) : ' is left without a key'); }).join('; ');
      ctx.msg.textContent = said + ' is already the key of ' + names + '. One key never does two things: swap them, or cancel.';
      var swap = T.h('button', { class: 'btn primary', onclick: function () { ctx.modal.close(); kbSave(changes, a.label + ': ' + said + ' (swapped: ' + swapWords + ')'); } }, 'Swap: ' + swapWords);
      ctx.ask.appendChild(T.h('div', { class: 'row' }, swap,
        ctx.retry ? T.h('button', { class: 'btn', onclick: function () { ctx.retry(); } }, 'Press another key') : null,
        ctx.cancel ? null : T.h('button', { class: 'btn', onclick: function () { ctx.modal.close(); } }, 'Cancel')));
      try { swap.focus(); } catch (e) { /* focus is a convenience */ }
    }
    function kbSave(changes, okMsg) {
      var K = T.keys, send = {};
      Object.keys(K.lostAll()).forEach(function (id) { send[id] = null; });     /* a default another key took stays "no key" */
      Object.keys(changes).forEach(function (id) { send[id] = changes[id]; });
      return T.api.patch('/api/settings', { keybinds: send, keybind_defaults: K.defaults() }).then(function (r) {
        var s = (r && r.settings) || r || {};
        D.settings = s;
        K.load(s.keybinds || {});
        T.ui.toast(okMsg, 'ok', 3500);
        if (!dead && section === 'keyboard') { paint(); }
      }, function (e) {
        var d = (e && e.details) || {};
        if (e && e.code === 'SETTING_VALUE' && d.key && Array.isArray(d.actions)) { T.ui.toast('not saved: ' + kbSaid(d.key) + ' would be the key of ' + d.actions.map(kbLabel).join(' and '), 'err', 6000); return; }
        T.api.fail(e, 'keyboard');
      });
    }
    function kbResetAll() {
      var K = T.keys, send = {};
      K.ACTIONS.forEach(function (a) { if (K.overridden(a.id) || K.lost(a.id)) { send[a.id] = a.key; } });
      var n = Object.keys(send).length;
      if (!n) { T.ui.toast('every key is at its F-key default', '', 2000); return; }
      T.ui.confirm('Put ' + n + ' key' + (n === 1 ? '' : 's') + ' back to the F-key defaults?', { title: 'Reset all keys', ok: 'Reset all', cancel: 'Keep my keys' }).then(function (yes) {
        if (yes) { kbSave(send, 'every key is back to its F-key default'); }
      });
    }
    /* "Test a key": what the app receives (key, code, modifiers), what it would do, and whether the browser kept it */
    function kbTest() {
      var K = T.keys, release = null;
      var out = T.h('div', { class: 'kb-test-out', 'aria-live': 'polite' }, T.h('span', { class: 'muted' }, 'nothing pressed yet'));
      var box = T.h('div', { class: 'kb-test', tabindex: '0', role: 'button', 'aria-label': 'test a key: click, then press a key; Tab leaves the box' }, 'click here, then press a key');
      function off() { if (release) { release(); release = null; } box.classList.remove('on'); box.textContent = 'click here, then press a key'; }
      function on() {
        if (release) { return; }
        box.classList.add('on'); box.textContent = 'listening - press any key (Tab or a click outside the box stops)';
        release = K.capture(show); kbRelease.push(off);
      }
      box.addEventListener('focus', on);
      box.addEventListener('blur', off);
      function show(e, info) {
        T.clear(out);
        /* Tab / Shift+Tab leave the box (no keyboard trap, KB-R1-04): listening stops and the key is handed back to the browser */
        if (info.combo === 'Tab' || info.combo === 'Shift+Tab') {
          out.appendChild(T.h('div', { class: 'muted' }, K.display(info.combo) + ': the box stopped listening (Tab moves the cursor on; it can never be a key of an action)'));
          off();
          return 'pass';
        }
        var held = [info.ctrl && 'Ctrl', info.alt && 'Alt', info.shift && 'Shift', info.meta && 'Windows'].filter(Boolean);
        var raw = 'key "' + info.raw + '"' + (info.code ? ', code "' + info.code + '"' : '') + (held.length ? ', held: ' + held.join(' + ') : '') + (e.repeat ? ', repeating' : '');
        if (info.modifier) { out.appendChild(T.h('div', null, 'received ' + raw + ': a modifier alone - hold it and press another key')); return; }
        if (!info.combo) { out.appendChild(T.h('div', null, 'received ' + raw + ': ' + info.why)); return; }
        out.appendChild(T.h('div', { html: 'received ' + K.kbd(info.combo) + ' <span class="muted">(' + T.esc(raw) + ')</span>' }));
        var why = K.refused(info.combo);
        if (why) { out.appendChild(T.h('div', { class: 'neg' }, 'cannot be chosen: ' + why)); return; }
        var ids = K.boundTo(info.combo);
        ids.forEach(function (id) { var a = K.get(id); out.appendChild(T.h('div', null, 'in this app: ' + a.group + ' - ' + a.label + ' (' + K.SCOPE_WORDS[a.scope] + ')')); });
        if (!ids.length) { out.appendChild(T.h('div', { class: 'muted' }, 'no action has this key' + (K.browserUse(info.combo) ? ': the browser keeps it (' + K.browserUse(info.combo) + ')' : ': the browser keeps whatever it does with it'))); }
        if (K.mayKeep(info.combo)) { out.appendChild(T.h('div', { class: 'pos' }, 'it reached the app, so this window hands ' + K.display(info.combo) + ' over (' + K.mayKeep(info.combo) + ')')); }
      }
      return T.h('div', { class: 'panel' }, T.h('h2', null, 'test a key', T.h('small', null, 'exactly what the app receives from your browser; nothing runs while the box listens')),
        T.h('div', { class: 'hint' }, 'A key that shows nothing here never reaches the app (the browser or Windows keeps it): pick another key for that action. Do not try Ctrl+W, Ctrl+F4, Ctrl+T, Ctrl+N or Alt+F4 - the browser or Windows acts on them first (Ctrl+W and Ctrl+F4 close this window). Tab leaves the box.'),
        box, out);
    }
    function kbLimits() {
      var K = T.keys, rows = [];
      Object.keys(K.REFUSED).forEach(function (k) { rows.push(T.h('tr', null, T.h('td', { class: 'l', html: K.kbd(k) }), wrapTd('refused'), wrapTd(K.REFUSED[k]))); });
      Object.keys(K.MAY_KEEP).forEach(function (k) { rows.push(T.h('tr', null, T.h('td', { class: 'l', html: K.kbd(k) }), wrapTd('allowed, with a note'), wrapTd(K.MAY_KEEP[k]))); });
      return T.h('div', { class: 'panel' }, T.h('details', null, T.h('summary', { class: 'muted' }, 'keys a browser or Windows keeps (' + rows.length + '): refused, or allowed with a note'),
        table([th('key'), th('here'), th('why')], rows, { fixed: true, cols: ['18%', '18%', '64%'] })));
    }
    function keyboard() {
      var K = T.keys;
      if (!K || !K.ACTIONS) { body.appendChild(T.h('div', { class: 'error' }, 'the keyboard registry (keys.js) did not load: no key can be changed here')); return; }
      var changed = K.ACTIONS.filter(function (a) { return K.overridden(a.id) || K.lost(a.id); }).length;
      var p = T.h('div', { class: 'panel kb' }, T.h('h2', null, 'keyboard', T.h('small', null, 'your keys are saved in the app: in every browser, kept after restarts and lifetime resets')));
      p.appendChild(T.h('div', { class: 'hint' }, 'Change: press the new key (Esc cancels; Backspace or Delete leaves the action without a key). A key another action already uses asks to swap or cancel - one key never does two things. ' +
        'F-keys and Ctrl / Alt keys also work while the cursor is in a box; single keys (letters, / and ?) never act while you type.'));
      p.appendChild(T.h('div', { class: 'row kb-bar' }, T.h('button', { class: 'btn', disabled: !changed, onclick: kbResetAll }, 'Reset all to the F-key defaults'),
        T.h('span', { class: 'muted' }, changed ? changed + ' action' + (changed === 1 ? '' : 's') + ' changed from the defaults' : 'every key is at its F-key default')));
      K.groups().forEach(function (g) {
        var t = T.h('table', { class: 'kb-table' });
        t.appendChild(T.h('colgroup', null, T.h('col', { style: 'width:33%' }), T.h('col', { style: 'width:11%' }), T.h('col', { style: 'width:35%' }), T.h('col', { style: 'width:21%' })));
        t.appendChild(T.h('thead', null, T.h('tr', null, th('what it does'), th('key'), th('note'), th(''))));
        var tb = T.h('tbody'); g.actions.forEach(function (a) { tb.appendChild(kbRow(a)); }); t.appendChild(tb);
        p.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, g.name, T.h('small', null, g.where)), t));
      });
      body.appendChild(p);
      body.appendChild(kbTest());
      body.appendChild(kbLimits());
    }

    /* ---------------------------------------------------------------- Accounts --------------------------------- */
    function accounts() {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'per-account sweep and financing switches', T.h('small', null, (bookRow(D.books, bookCode()) || {}).name || '')));
      var rows = (D.accounts || []).filter(function (a) { return a.kind !== 'TREASURY'; });
      if (!rows.length) { p.appendChild(T.h('div', { class: 'empty' }, 'this lifetime has no trading accounts')); }
      rows.forEach(function (a) {
        var sw = T.h('input', { type: 'checkbox' }); sw.checked = a.sweep_out == null ? true : !!a.sweep_out;
        var buf = moneyInput(T.money(a, 'sweep_buffer') === 'n/a' ? '' : String(a.sweep_buffer == null ? '' : a.sweep_buffer));
        var fin = T.h('input', { type: 'checkbox' }); fin.checked = !!a.pb_financing;
        var lim = moneyInput(a.pb_limit == null ? '' : String(a.pb_limit));
        var save = T.h('button', { class: 'btn small', onclick: function () {
          var o = { sweep_out: sw.checked, sweep_buffer: buf.value.trim().replace(/,/g, '') || '0' };
          if (a.kind === 'PB') { o.pb_financing = fin.checked; o.pb_limit = lim.value.trim().replace(/,/g, '') || '0'; }
          T.api.patch('/api/accounts/' + encodeURIComponent(a.account_id), o).then(function () { T.ui.toast(a.name + ' saved', 'ok'); dirty = false; if (T.poll) { T.poll.kick('accounts'); } load(); }, function (e) { T.api.fail(e, 'account settings'); });
        } }, 'Save');
        var f = [T.field('sweep-out', sw, 'idle cash above the buffer returns to Treasury after 17:30 ET'), T.field('buffer (USD)', buf)];
        if (a.kind === 'PB') { f.push(T.field('PB financing', fin, 'borrowing from the prime broker against long positions; off when the account opens')); f.push(T.field('PB limit (USD, 0 = no cap)', lim)); }
        f.push(['', save]);
        p.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, a.name || a.account_id, T.h('small', null, a.account_id)), T.form(f)));
      });
      p.appendChild(T.convNote(['SWEEP']));
      body.appendChild(p);
    }

    /* ---------------------------------------------------------------- Prime brokers ---------------------------- */
    function agreementsLine(rows) {
      var by = {};
      (rows || []).forEach(function (r) { var k = r.broker; (by[k] = by[k] || { name: r.broker_name || r.broker, kinds: [], signed: r.signed_date }).kinds.push(r.kind); });
      return by;
    }
    function pb() {
      var b = bookRow(D.books, bookCode());
      var e = (b && b.eligibility) || {};
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'prime brokers', T.h('small', null, 'editable fee schedules (modelled assumptions); before / after confirmation')));
      /* PO-14: the eligibility sentence of this book, before any schedule */
      p.appendChild(T.h('div', { class: 'section' }, T.kv([
        ['book', (b && b.name) || bookCode() || ''],
        ['prime brokers & OTC', e.pb_otc_reason || 'n/a'],
        ['new relationships', e.can_onboard == null ? 'n/a' : (e.can_onboard ? 'allowed' : 'refused until NAV meets the threshold')],
        ['OTC bookings', e.pb_otc == null ? 'n/a' : (e.pb_otc && e.ecp ? 'allowed' : 'refused (' + (e.pb_otc_reason || 'not eligible') + ')')]
      ]), T.convNote(['ELIGIBILITY'])));
      var pbs = (D.accounts || []).filter(function (a) { return a.kind === 'PB'; });
      var ag = agreementsLine(((D.extra.lifetime || {}).agreements) || []);
      if (!pbs.length) {
        var where = T.pages.accounts ? ': open one with "Open prime broker account" on the Accounts page' : ': open one with "+ Open prime broker account" in the sidebar';
        p.appendChild(T.h('div', { class: 'empty' }, 'no prime-broker account in this lifetime' + (e.can_onboard ? where : (e.pb_otc_reason ? ' (' + e.pb_otc_reason + ')' : ''))));
      }
      pbs.forEach(function (a) {
        var g = ag[a.broker] || null;
        var line = g ? (g.kinds.map(function (k) { return k === 'ISDA_MASTER' ? 'ISDA Master' : k; }).join(' / ') + ' with ' + g.name + ', signed ' + (g.signed || '')) : 'agreements: none recorded';
        var s = T.h('div', { class: 'section' }, T.h('h2', null, a.name || a.account_id, T.h('small', null, line)));
        var m = T.h('div'); s.appendChild(m); p.appendChild(s);
        var hnd = T.mountOptional('pbschedule', m, { account_id: a.account_id, broker: a.broker }, 'components/pbschedule.js: the per-PB fee schedule form');
        if (hnd) { handles.push(hnd); }
      });
      body.appendChild(p);
    }

    /* ---------------------------------------------------------------- Books ------------------------------------ */
    function books() {
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'books', T.h('small', null, 'names and starting capitals apply to NEW lifetimes; a name changes the sidebar at once; history is never rewritten')));
      var defaults = get('book_defaults', {}); var inputs = {};
      var list = D.books || [];
      if (!list.length) { p.appendChild(T.h('div', { class: 'empty' }, 'no books returned by the server')); }
      list.forEach(function (b) {
        var d = defaults[b.book_code] || {};
        var nm = T.h('input', { type: 'text', value: d.name || b.name || '', style: 'width:220px' });
        var cap0 = d.starting_capital != null ? d.starting_capital : (b.starting_capital != null ? b.starting_capital : ((b.lifetime && b.lifetime.starting_capital) || ''));
        var cap = moneyInput(cap0);
        inputs[b.book_code] = { name: nm, cap: cap, name0: nm.value, cap0: String(cap0) };
        p.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, (b.name || b.book_code) + ' . ' + profileLabel(b), T.h('small', null, b.lifetime ? 'open lifetime ' + (b.lifetime.name || b.lifetime.lifetime_id) + ' (' + b.lifetime.lifetime_id + ') since ' + (b.lifetime.started || '') + ', started with ' + T.money(b.lifetime, 'starting_capital') : 'no open lifetime')),
          T.form([T.field('name', nm), T.field('starting capital (USD) of the next lifetime', cap)])));
      });
      var thr = moneyInput(get('pb_otc_threshold', '25000000.00'));
      var qp = get('qp_threshold', {}); var qpI = moneyInput(qp.INDIVIDUAL != null ? qp.INDIVIDUAL : '5000000.00'), qpF = moneyInput(qp.FUND != null ? qp.FUND : '25000000.00');
      p.appendChild(T.h('div', { class: 'section' }, T.h('h2', null, 'thresholds'), T.form([T.field('PB / OTC eligibility (book NAV, USD)', thr, 'the Fund and Institutional books are eligible from day one'),
        T.field('Private Markets - Personal (NAV, USD)', qpI), T.field('Private Markets - Fund (NAV, USD)', qpF), T.field('Private Markets - Institutional', T.h('span', { class: 'muted' }, 'open from the start'))])));
      p.appendChild(T.h('button', { class: 'btn primary', onclick: function () {
        var bd = {};
        Object.keys(inputs).forEach(function (k) {
          var row = {}; var n = inputs[k].name.value.trim(); var c = inputs[k].cap.value.trim().replace(/,/g, '');
          if (n) { row.name = n; }
          if (c) { row.starting_capital = c; }
          if (Object.keys(row).length) { bd[k] = row; }
        });
        var o = { qp_threshold: { INDIVIDUAL: qpI.value.trim().replace(/,/g, '') || '0', FUND: qpF.value.trim().replace(/,/g, '') || '0', INSTITUTION: '0' } };
        if (Object.keys(bd).length) { o.book_defaults = bd; }
        if (thr.value.trim()) { o.pb_otc_threshold = thr.value.trim().replace(/,/g, ''); }
        patch(o, 'books');
      } }, 'Save books'));
      p.appendChild(T.convNote(['BOOKS', 'ELIGIBILITY', 'QP']));
      body.appendChild(p);
    }

    /* ---------------------------------------------------------------- Lifetimes -------------------------------- */
    function lifetimes() {
      var b = bookRow(D.books, bookCode());
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'lifetimes', T.h('small', null, 'Reset lifetime (typed RESET) and the past lifetimes with History links')));
      var m = T.h('div'); p.appendChild(m);
      var hnd = T.mountOptional('lifetimes', m, { book: bookCode(), book_name: b && b.name }, 'components/lifetimes.js: the Reset lifetime dialog and the past-lifetimes table');
      if (hnd) { handles.push(hnd); }
      body.appendChild(p);                       /* the component prints the LIFETIME sentence once (SHELL-22) */
    }

    /* ---------------------------------------------------------------- Calendar (SHELL-10) ---------------------- */
    function calendar() {
      var c0 = D.extra.cal0 || {}, c1 = D.extra.cal1 || {};
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'trading calendar', T.h('small', null, 'NYSE holidays and early closes (13:00 ET); futures follow the NYSE calendar')));
      var grid = T.h('div', { style: 'display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px' });
      [c0, c1].forEach(function (c) {
        if (!c || c.year == null) { return; }
        var rows = [];
        var days = (c.holidays || []).map(function (d) { return [d, 'closed']; }).concat((c.early_closes || []).map(function (d) { return [d, 'early close 13:00 ET']; }));
        days.sort(function (a, b) { return a[0] < b[0] ? -1 : 1; });
        days.forEach(function (x) {
          var dow = ''; try { dow = new Date(x[0] + 'T12:00:00Z').toLocaleDateString('en-US', { weekday: 'short', timeZone: 'UTC' }); } catch (e) { dow = ''; }
          rows.push(T.h('tr', null, T.h('td', { class: 'l' }, x[0]), T.h('td', { class: 'l' }, dow), T.h('td', { class: 'l ' + (x[1] === 'closed' ? 'neg' : '') }, x[1])));
        });
        grid.appendChild(T.h('div', null, T.h('h2', null, String(c.year), T.h('small', null, c.fallback ? 'computed from the holiday rules (not in the file)' : 'from the NYSE file')),
          rows.length ? table([th('date'), th('day'), th('market')], rows) : T.h('div', { class: 'empty' }, 'no holidays listed')));
      });
      p.appendChild(grid);
      p.appendChild(T.convNote(['CME_CAL']));
      body.appendChild(p);
      var ses = D.extra.sessions || {}; var rules = ses.rules || {};
      var hrs = T.h('div', { class: 'panel' }, T.h('h2', null, 'session hours (New York time)'));
      var srows = Object.keys(rules).map(function (g) {
        var r = rules[g] || {};
        var hours = r.kind === 'GRAINS' ? ('night ' + r.night_open + ' - ' + r.night_close + ', day ' + r.open + ' - ' + r.close) :
          (r.kind === 'CME' ? (r.open + ' (evening before) - ' + r.close) : (r.kind === 'FX' ? 'Sun ' + r.open + ' - Fri ' + r.close : (r.open && r.close ? r.open + ' - ' + r.close : 'no fixed session')));
        return T.h('tr', null, wrapTd(glabel(g)), wrapTd(hours), wrapTd(r.early || '-'), wrapTd(r.holidays || '-'), wrapTd(r.note || ''));
      });
      hrs.appendChild(table([th('group'), th('hours'), th('early close'), th('holidays'), th('note')], srows, { fixed: true, cols: ['18%', '22%', '9%', '20%', '31%'] }));
      hrs.appendChild(T.convNote(['SESSION']));
      body.appendChild(hrs);
      /* the session overrides: a small form instead of raw JSON */
      var cur = get('session_overrides', {}) || {};
      var ov = T.h('div', { class: 'panel' }, T.h('h2', null, 'session overrides', T.h('small', null, 'an early close or a closed day the NYSE file lacks; applied at once')));
      var orows = [];
      Object.keys(cur).sort().forEach(function (d) {
        Object.keys(cur[d] || {}).forEach(function (g) {
          orows.push(T.h('tr', null, T.h('td', { class: 'l' }, d), T.h('td', { class: 'l' }, glabel(g)), T.h('td', { class: 'l' }, cur[d][g] === 'CLOSED' ? 'closed' : 'closes ' + cur[d][g] + ' ET'),
            T.h('td', null, T.h('button', { class: 'btn small', onclick: function () {
              var next = JSON.parse(JSON.stringify(cur)); delete next[d][g]; if (!Object.keys(next[d]).length) { delete next[d]; }
              patch({ session_overrides: next }, 'session overrides');
            } }, 'Remove'))));
        });
      });
      ov.appendChild(orows.length ? table([th('date'), th('group'), th('override'), th('')], orows) : T.h('div', { class: 'empty' }, 'no overrides'));
      var gsel = T.h('select', { style: 'width:240px' });
      ['US_EQUITY', 'US_EQUITY_OPTIONS', 'US_INDEX_OPTIONS', 'US_BONDS', 'CME_EQUITY_INDEX', 'CME_RATES', 'NYMEX_ENERGY', 'COMEX_METALS', 'CBOT_GRAINS', 'CME_FX', 'FX_SPOT', '*'].forEach(function (g) { gsel.appendChild(T.h('option', { value: g }, glabel(g))); });
      var dinp = T.h('input', { type: 'date', style: 'width:160px' });
      var tinp = T.h('input', { type: 'text', placeholder: 'HH:MM', style: 'width:90px' });
      var closed = T.h('input', { type: 'checkbox' });
      ov.appendChild(T.form([T.field('group', gsel), T.field('date', dinp), T.field('close time (ET)', T.h('span', null, tinp, T.h('label', { style: 'margin-left:10px;text-transform:none' }, closed, ' closed all day'))),
        ['', T.h('button', { class: 'btn', onclick: function () {
          var d = dinp.value; var v = closed.checked ? 'CLOSED' : tinp.value.trim();
          if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) { T.ui.toast('pick a date', 'err'); return; }
          if (v !== 'CLOSED' && !/^([01]\d|2[0-3]):[0-5]\d$/.test(v)) { T.ui.toast('close time as HH:MM (24 h) or tick closed', 'err'); return; }
          var next = JSON.parse(JSON.stringify(cur)); next[d] = next[d] || {}; next[d][gsel.value] = v;
          patch({ session_overrides: next }, 'session overrides');
        } }, 'Add override')]]));
      body.appendChild(ov);
    }

    /* ---------------------------------------------------------------- Margins & limits (SR-11) ----------------- */
    function margins() {
      var tbl = get('tables', {}) || {}; var fut = tbl.futures || []; var opt = tbl.option_limit || {};
      var mo = get('futures_margin_overrides', {}) || {}; var lo = get('position_limit_overrides', {}) || {};
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'margins & limits', T.h('small', null, 'assumed - verify current exchange values')));
      p.appendChild(T.h('div', { class: 'hint' }, 'The static tables below are modelled assumptions. An override replaces the table value for NEW margin requirements and new limit checks; positions already open keep what was posted. Leave both margin fields empty to use the table again.'));
      var inputs = {};
      var rows = fut.map(function (f) {
        var o = mo[f.root] || {};
        var im = moneyInput(o.im != null ? o.im : '', '100px'), mm = moneyInput(o.mm != null ? o.mm : '', '100px'), lim = moneyInput(lo[f.root] != null ? lo[f.root] : '', '90px');
        inputs[f.root] = { im: im, mm: mm, lim: lim };
        return T.h('tr', null, T.h('td', { class: 'l' }, f.root), T.h('td', { class: 'l' }, f.name), T.h('td', { class: 'l' }, glabel(f.group)), T.h('td', null, T.fmt.money(f.im)), T.h('td', null, T.fmt.money(f.mm)),
          T.h('td', null, im), T.h('td', null, mm), T.h('td', null, f.spot_month_limit == null ? '-' : T.fmt.qty(f.spot_month_limit)), T.h('td', null, lim));
      });
      p.appendChild(fut.length ? table([th('root'), th('contract'), th('group'), th('IM (table)', 'r'), th('MM (table)', 'r'), th('IM override', 'r'), th('MM override', 'r'), th('spot-month limit', 'r'), th('limit override', 'r')], rows)
        : T.h('div', { class: 'empty' }, 'the server sent no futures table'));
      var optIn = moneyInput(lo.OPTIONS != null ? lo.OPTIONS : '', '120px');
      p.appendChild(T.h('div', { class: 'section', style: 'margin-top:10px' }, T.form([T.field('option position limit (contracts)', T.h('span', null, optIn, T.h('span', { class: 'muted', style: 'margin-left:8px' }, 'table default ' + (opt.default == null ? 'n/a' : T.fmt.qty(opt.default)) + (opt.tiers ? ' (tiers ' + opt.tiers.map(T.fmt.qty).join(' / ') + ')' : ''))))])));
      p.appendChild(T.h('button', { class: 'btn primary', onclick: function () {
        var m = {}, l = {};
        Object.keys(inputs).forEach(function (r) {
          var i = inputs[r]; var a = i.im.value.trim().replace(/,/g, ''), b = i.mm.value.trim().replace(/,/g, ''), c = i.lim.value.trim().replace(/,/g, '');
          if (a || b) { m[r] = { im: a, mm: b }; }
          if (c) { l[r] = c; }
        });
        var ov = optIn.value.trim().replace(/,/g, ''); if (ov) { l.OPTIONS = ov; }
        patch({ futures_margin_overrides: m, position_limit_overrides: l }, 'margins & limits');
      } }, 'Save margins & limits'));
      p.appendChild(T.convNote(['FUT_MARGIN']));
      body.appendChild(p);
    }

    /* ---------------------------------------------------------------- Private defaults ------------------------- */
    function priv() {
      var d = get('private_defaults', {});
      var fee = moneyInput(d.fee_pct != null ? d.fee_pct : '0.03', '100px'), lag = moneyInput(d.settle_days != null ? d.settle_days : '30', '100px'), lock = moneyInput(d.lockup_months != null ? d.lockup_months : '6', '100px');
      body.appendChild(T.h('div', { class: 'panel' }, T.h('h2', null, 'private markets defaults', T.h('small', null, 'the ticket starts from these; each trade may override them')), T.form([T.field('placement fee (fraction)', fee, '0.03 = 3 %'), T.field('settlement lag (calendar days)', lag), T.field('lock-up (months)', lock), ['', T.h('button', { class: 'btn', onclick: function () { patch({ private_defaults: { fee_pct: fee.value.trim(), settle_days: lag.value.trim(), lockup_months: lock.value.trim() } }, 'private defaults'); } }, 'Save')]]), T.convNote(['PRIVATE_TRADE', 'PRIVATE_MARK', 'QP'])));
    }

    /* ---------------------------------------------------------------- Market data (SHELL-14; UI-20) -------------- */
    function stateCell(s) {
      if (s && s.ok === false) { return ['bad', 'DOWN']; }
      if (s && s.ok) { return ['ok', 'OK']; }
      if (s && (s.last_ok_ts || s.last_err_ts)) { return ['na', '?']; }
      return ['na', 'not used yet'];
    }
    function marketData() {
      var h = T.state.health || {};
      var down = T.state.server_ok === false;
      var p = T.h('div', { class: 'panel' + (down ? ' dim' : '') }, T.h('h2', null, 'Market data', T.h('small', null, down ? 'server unreachable - as of ' + T.fmt.hm(h.clock && h.clock.epoch) + ' (the last answer)' : 'each provider\'s status and delay; nothing is hidden')));
      var ps = h.providers || {};
      if (!Object.keys(ps).length) { p.appendChild(T.h('div', { class: 'empty' }, 'no provider has reported yet')); }
      Object.keys(ps).sort().forEach(function (k) {
        var s = ps[k] || {}; var st = stateCell(s);
        var parts = [PROVIDER_WHAT[k] || ''];
        if (s.last_ok_ts) { parts.push('last ok ' + T.fmt.hm(s.last_ok_ts)); }
        if (s.ok === false && s.last_error) { parts.push(s.last_error); }
        if (s.consecutive_errors) { parts.push(s.consecutive_errors + ' errors in a row'); }
        if (s.down_until_ts) { parts.push('paused until ' + T.fmt.hm(s.down_until_ts)); }
        if (s.refused_ua) { parts.push('user-agent refused'); }
        p.appendChild(T.h('div', { class: 'ds-row', style: 'grid-template-columns:170px 90px 1fr auto' }, T.h('span', null, plabel(k)), T.h('span', { class: 'st ' + st[0] }, st[1]), T.h('span', { style: 'white-space:normal' }, parts.filter(Boolean).join(' . ')), T.h('span', { class: 'muted' }, s.requests_today != null ? s.requests_today + ' requests today' : '')));
      });
      var refs = h.refreshers || {};
      if (Object.keys(refs).length) {
        p.appendChild(T.h('h2', null, 'background refreshers'));
        Object.keys(refs).sort().forEach(function (k) {
          var r = refs[k] || {};
          var st = r.last_error && r.failures ? ['bad', 'FAILING'] : (r.last_ok_ts ? ['ok', 'OK'] : ['na', 'not run yet']);
          p.appendChild(T.h('div', { class: 'ds-row', style: 'grid-template-columns:170px 90px 1fr auto' }, T.h('span', null, plabel(k)), T.h('span', { class: 'st ' + st[0] }, st[1]),
            T.h('span', { style: 'white-space:normal' }, [r.interval_s ? 'every ' + T.fmt.age(r.interval_s) : '', r.run_after_et ? 'after ' + r.run_after_et + ' ET' : '', r.last_ok_ts ? 'last ok ' + T.fmt.hm(r.last_ok_ts) : '', r.last_error ? r.last_error : ''].filter(Boolean).join(' . ')), T.h('span')));
        });
      }
      var ws = h.workers || {};
      p.appendChild(T.h('h2', null, 'workers'));
      Object.keys(ws).forEach(function (k) { var s = ws[k] || {}; p.appendChild(T.h('div', { class: 'ds-row', style: 'grid-template-columns:170px 90px 1fr auto' }, T.h('span', null, k), T.h('span', { class: 'st ' + (s.alive === false ? 'bad' : (s.stalled ? 'bad' : 'ok')) }, s.alive === false ? 'STOPPED' : (s.stalled ? 'STALLED' : 'running')), T.h('span', { style: 'white-space:normal' }, (s.last_tick_ts ? 'last tick ' + T.fmt.time(s.last_tick_ts) : '') + (s.errors_1h ? ' . ' + s.errors_1h + ' errors in the last hour' : '') + (s.last_error ? ' . ' + s.last_error : '') + (s.note ? ' . ' + s.note : '')), T.h('span'))); });
      var lf = h.lifecycle || {}; var nd = lf.next_due || {}; var failing = lf.failing || [];
      p.appendChild(T.h('h2', null, 'scheduled jobs'));
      p.appendChild(T.h('div', { class: 'ds-row', style: 'grid-template-columns:170px 90px 1fr auto' }, T.h('span', null, 'lifecycle'), T.h('span', { class: 'st ' + (failing.length ? 'bad' : 'ok') }, failing.length ? 'FAILING' : 'ok'),
        T.h('span', { style: 'white-space:normal' }, (nd.job ? 'next: ' + nd.job + ' at ' + T.fmt.hm(nd.at_ts) + ' ET' + (nd.at_ts && T.fmt.date(nd.at_ts) !== T.fmt.date(T.clock.nowInt()) ? ' (' + T.fmt.date(nd.at_ts) + ')' : '') : (lf.note || 'next job unknown')) + (failing.length ? ' . failing: ' + failing.map(function (j) { return j.name || j.job || j; }).join(', ') : '')), T.h('span')));
      /* UI-20: the market-data and execution-simulation preferences, where each one is edited (status above, settings below) */
      p.appendChild(T.h('h2', null, 'market-data and execution preferences'));
      var prefs = [['participation rate', T.h('span', null, (get('participation_rate', null) != null ? T.fmt.pct(get('participation_rate', null), 0) + ' of each new 1-minute bar\'s volume' : 'n/a') + ' . ',
                      T.h('a', { href: '#/settings/general' }, 'change it in General')), 'the share of a bar\'s volume an order may take'],
                   ['session overrides', T.h('span', null, (Object.keys(get('session_overrides', {}) || {}).length ? Object.keys(get('session_overrides', {})).length + ' day(s) overridden' : 'none') + ' . ',
                      T.h('a', { href: '#/settings/calendar' }, 'edit them in Calendar')), 'an early close or a closed day the NYSE file lacks'],
                   ['futures margins & limits', T.h('a', { href: '#/settings/margins' }, 'Margins & limits'), 'the static margin table and position limits (assumed values) and your overrides']];
      p.appendChild(T.kv(prefs));
      p.appendChild(T.h('h2', null, 'server'));
      var dbf = h.db_files || {};
      var cal = h.calendar || {};
      var fby = (cal.fallback_years || []);
      p.appendChild(T.kv([['app', (h.app || '') + ' ' + (h.version || '')], ['schema', h.schema], ['process', h.pid], ['up for', T.fmt.age(h.uptime_s)],
        ['database', (h.db_path || '') + (h.db_size != null ? ' (' + T.fmt.qty(h.db_size) + ' bytes' + (dbf.wal != null ? ' = file ' + T.fmt.qty(dbf.file) + ' + journal ' + T.fmt.qty(dbf.wal) : '') + ')' : '')],
        ['state version', h.state_version], ['offline mode', h.offline ? 'YES - fake data' : 'no'],
        ['calendar', (cal.file || '') + (cal.years && cal.years.length ? ' (years ' + cal.years[0] + ' - ' + cal.years[cal.years.length - 1] + ')' : '') + (fby.length ? ' . computed for ' + fby.join(', ') : '')],
        ['lock', h.lock ? (h.lock.path || '') : '']]));
      var dbg = T.h('details', { style: 'margin-top:8px' }, T.h('summary', { class: 'muted' }, 'debug: the page pollers'), T.h('pre', { style: 'white-space:pre-wrap;font-size:10.5px' }, JSON.stringify(T.poll.status(), null, 1)));
      p.appendChild(dbg);
      p.appendChild(T.convNote(['STALE', 'COMPUTED', 'PAPER']));
      body.appendChild(p);
    }

    /* ---------------------------------------------------------------- Unsupported (SHELL-07) ------------------- */
    function unsupported() {
      var rows = D.extra.unsupported || [];
      var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'what this terminal does not do', T.h('small', null, 'shown as such everywhere; never priced with invented data')));
      p.appendChild(rows.length ? table([th('item'), th('state'), th('how it is shown'), th('where')], rows.map(function (x) {
        return T.h('tr', null, wrapTd(x.item || ''), T.h('td', { class: 'l', style: 'white-space:normal;height:auto;vertical-align:top;padding:4px 8px' }, T.stateTag(x.state || '')), wrapTd(x.wording || ''), wrapTd(x.where || ''));
      }), { fixed: true, cols: ['34%', '13%', '33%', '20%'] }) : T.h('div', { class: 'empty' }, 'the server lists nothing'));
      body.appendChild(p);
    }

    function paint() {
      handles.forEach(function (h) { try { if (h && h.unmount) { h.unmount(); } } catch (e) { console.error(e); } });
      handles = [];
      kbStop();                                                /* a listening "Test a key" box never outlives its page */
      T.clear(body);
      if (D.error) { body.appendChild(T.h('div', { class: 'error' }, D.error, ' ', T.h('button', { class: 'btn small', onclick: load }, 'Retry'))); }
      var fn = { general: general, keyboard: keyboard, accounts: accounts, pb: pb, books: books, lifetimes: lifetimes, calendar: calendar, margins: margins, private: priv, 'market-data': marketData, unsupported: unsupported }[section] || general;
      try { fn(); } catch (e) { console.error('settings', section, e); body.appendChild(T.h('div', { class: 'error' }, 'settings ' + section + ' failed: ' + (e.message || e))); }
      sig = signature();
    }
    load();
    var offs = [];
    /* repaint on what the pollers bring (SHELL-08), never over unsaved edits */
    ['accounts', 'books'].forEach(function (k) {
      offs.push(T.state.on(k, function () {
        if (dead || dirty || (section !== 'accounts' && section !== 'pb' && section !== 'books')) { return; }
        if (signature() !== sig) { load(); }
      }));
    });
    offs.push(T.state.on('book', function () { if (!dead) { dirty = false; load(); } }));
    offs.push(T.state.on('health', function () { if (!dead && section === 'market-data') { paint(); } }));
    offs.push(T.state.on('server_ok', function () { if (!dead && section === 'market-data') { paint(); } }));
    /* the keys in force changed (a save, another window, a file that now reads the registry): the key lists follow */
    offs.push(T.state.on('keybinds', function () { if (!dead && (section === 'keyboard' || section === 'general') && D.settings && !(T.keys.capturing && T.keys.capturing())) { paint(); } }));
    T.pages.settings.unmount = function () {
      dead = true;
      kbStop();
      offs.forEach(function (f) { try { f(); } catch (e) { /* already gone */ } });
      handles.forEach(function (h) { try { if (h && h.unmount) { h.unmount(); } } catch (e) { /* ignore */ } });
      T.pages.settings.unmount = null;
    };
  }
  T.pages.settings = { render: render, unmount: null, SECTIONS: SECTIONS };
})();
