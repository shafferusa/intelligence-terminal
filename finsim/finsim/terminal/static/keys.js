/* FinSim Paper Terminal - keys.js: the keybinding registry (owner 2026-10-21 17:10: "key the stuff to the Fkeys rather
 * than alt ... like F1 opens up one exchange / let me code it in the app settings my keybinds"; docs/KEYBINDS.md).
 *
 * Loaded right after core.js and before every component (index.html).  It extends T.keys (created by core.js, whose keys
 * section dispatches the global actions) with:
 *   T.keys.ACTIONS    every action: {id, group, scope, key (its DEFAULT key or null), label, typing, files, legacy, family,
 *                     page}.  The scope comes from the id: 'ticket.*' acts in the dock ticket, 'grid.*' in a table (its selected
 *                     row), 'dialog.*' while a dialog or the keyboard help is open, everything else is 'global' (anywhere).
 *                     api.py (keybind_scope) uses the same rule.  74 actions (FINSIM1_PLAN C1).
 *   only what works   an action that opens a page names it (`page`: T.pages.<page>); while that page is not in the build
 *                     (T.pages.<page> undefined) the action is hidden from the '?' help and Settings > Keyboard
 *                     (T.keys.available(id); T.keys.groups() lists the available ones only).  The 1.0.5 default map keeps every
 *                     1.0.4 key except F12, which opens the Dashboard (the news page is gone); the actions of the pages of the new
 *                     navigation (Pending / Completed Trades, Accounts, Cash & Collateral, Derivatives) and Trading have no
 *                     default key yet (FINSIM1_PLAN 15.7 item 3).
 *   canonical keys    ONE form: Ctrl+Alt+Shift+Key, the modifiers in that order.  Key = F1..F24, A..Z, 0..9, a named key
 *                     (Enter Escape Space Tab Backspace Delete Insert Home End PageUp PageDown ArrowUp ArrowDown ArrowLeft
 *                     ArrowRight) or one ASCII punctuation character; Shift is part of a punctuation or digit character
 *                     ('?', never 'Shift+/').  The Windows / Command key is never part of a binding.
 *                     T.keys.fromEvent(e), T.keys.parse(k), T.keys.valid(k), T.keys.display(k).
 *   bindings          the defaults below + the owner's overrides (GET /api/settings .keybinds: {id: key | null}, saved by
 *                     Settings > Keyboard with PATCH /api/settings {keybinds, keybind_defaults}; a copy is kept in this
 *                     browser so a reload paints the owner's keys before the server answers).  T.keys.binding(id),
 *                     T.keys.label(id), T.keys.kbd(id), T.keys.holders(id, key), T.keys.load(overrides).
 *   one key, one action: two actions never share a key where both can act - the same scope, or either one global.
 *   when an action fires: an F-key or any Ctrl / Alt binding always (also while the cursor is in a text box: it types
 *                     nothing); a single character (letters, digits, / ? + ...) never while typing; a named key (Enter, Esc,
 *                     arrows ...) while typing only for the actions that work in boxes (ticket preview / submit / clear, close
 *                     a dialog).
 *   the contract of the page-level handlers (components/ticket.js, grid.js, bondticket.js, privateticket.js, swapticket.js,
 *                     otcticket.js, cdsticket.js, fxdesk.js): T.keys.is(e, id), T.keys.action(e, scope), T.keys.legacy(id)
 *                     and, once the file reads the registry, T.keys.uses('components/<file>.js').  Until a file that handles
 *                     an action today has called uses(), that action keeps its default key and Settings shows it as fixed
 *                     (never a key on screen that the app does not honour).
 *   a focused button  Enter (and Space) on a focused button, link or tab is the browser's own "press it": no action takes
 *                     it there (Enter on the ticket's Submit button presses Submit, never Preview).
 *   text editing      Ctrl+C / V / X / Z / Y / A, Ctrl+Backspace / Delete, Ctrl+arrows ... never act while the cursor is in a
 *                     text box (copy stays copy there); bound to an action they act everywhere else, with a note in
 *                     Settings > Keyboard.
 *   Mac               Cmd+Enter submits like Ctrl+Enter while 'Submit the order' keeps its default key (the 1.0.3 alias).
 * ASCII only; no frameworks.
 */
(function () {
  'use strict';
  var T = window.T = window.T || {};
  var K = T.keys = T.keys || {};

  /* ---------------------------------------------------------------- canonical key names ------------------------- */
  var NAMED = ['Enter', 'Escape', 'Space', 'Tab', 'Backspace', 'Delete', 'Insert', 'Home', 'End', 'PageUp', 'PageDown',
               'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
  var ALIASES = { Esc: 'Escape', Up: 'ArrowUp', Down: 'ArrowDown', Left: 'ArrowLeft', Right: 'ArrowRight', Del: 'Delete', ' ': 'Space', Spacebar: 'Space' };
  var PUNCT = '!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~';
  var FKEY = /^F([1-9]|1[0-9]|2[0-4])$/;
  var MODIFIER_KEYS = { Shift: 1, Control: 1, Alt: 1, AltGraph: 1, Meta: 1, OS: 1, Super: 1, Hyper: 1, CapsLock: 1, NumLock: 1, ScrollLock: 1, Fn: 1, FnLock: 1, Symbol: 1, SymbolLock: 1 };
  var DISPLAY = { Escape: 'Esc', ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right', PageUp: 'Page Up', PageDown: 'Page Down', Delete: 'Del', Insert: 'Ins' };

  /* the key part of a binding from KeyboardEvent.key (null: not a key the app binds) */
  function keyName(k) {
    if (typeof k !== 'string' || !k) { return null; }
    if (Object.prototype.hasOwnProperty.call(ALIASES, k)) { k = ALIASES[k]; }
    if (FKEY.test(k) || NAMED.indexOf(k) >= 0) { return k; }
    if (k.length === 1) {
      var c = k.charCodeAt(0);
      if (c >= 97 && c <= 122) { return k.toUpperCase(); }
      if ((c >= 65 && c <= 90) || (c >= 48 && c <= 57)) { return k; }
      if (PUNCT.indexOf(k) >= 0) { return k; }
    }
    return null;
  }
  /* "Ctrl+Shift+F1" -> {ctrl, alt, shift, key}; null unless the text is in the ONE canonical form */
  function parse(combo) {
    if (typeof combo !== 'string' || !combo) { return null; }
    var s = combo, ctrl = false, alt = false, shift = false;
    if (s.indexOf('Ctrl+') === 0 && s.length > 5) { ctrl = true; s = s.slice(5); }
    if (s.indexOf('Alt+') === 0 && s.length > 4) { alt = true; s = s.slice(4); }
    if (s.indexOf('Shift+') === 0 && s.length > 6) { shift = true; s = s.slice(6); }
    var ok = FKEY.test(s) || NAMED.indexOf(s) >= 0 || /^[A-Z0-9]$/.test(s) || (s.length === 1 && PUNCT.indexOf(s) >= 0);
    if (!ok) { return null; }
    if (shift && s.length === 1 && !/^[A-Z]$/.test(s)) { return null; }      /* '?' carries its Shift */
    return { ctrl: ctrl, alt: alt, shift: shift, key: s };
  }
  function valid(combo) { return !!parse(combo); }
  /* types a character in a text box: a letter / digit / punctuation / Space without Ctrl or Alt (Shift+B types 'B') */
  function printable(combo) { var p = parse(combo); return !!p && !p.ctrl && !p.alt && (p.key.length === 1 || p.key === 'Space'); }
  /* acts while typing whatever the action: an F-key or a Ctrl / Alt binding - except the text-editing keys (TEXT_EDIT below:
     Ctrl+C stays copy in a box) */
  function freeWhileTyping(combo) { var p = parse(combo); return !!p && !TEXT_EDIT[combo] && (p.ctrl || p.alt || FKEY.test(p.key)); }
  function display(combo) {
    var p = parse(combo);
    if (!p) { return combo == null ? '' : String(combo); }
    return (p.ctrl ? 'Ctrl+' : '') + (p.alt ? 'Alt+' : '') + (p.shift ? 'Shift+' : '') + (DISPLAY[p.key] || p.key);
  }
  /* What the app receives for one keydown: {combo (canonical) | null, modifier (a modifier alone), why (not bindable),
     raw (event.key), code (event.code), ctrl, alt, shift, meta} */
  function fromEvent(e) {
    var raw = e && e.key;
    var out = { raw: raw == null ? '' : String(raw), code: (e && e.code) || '', ctrl: !!(e && e.ctrlKey), alt: !!(e && e.altKey), shift: !!(e && e.shiftKey),
                meta: !!(e && e.metaKey), combo: null, modifier: false, why: null };
    if (!e || raw == null || raw === '' || raw === 'Unidentified' || raw === 'Dead' || raw === 'Process' || e.isComposing) {
      out.why = 'the browser did not say which key this is (a dead key or an input method)'; return out;
    }
    if (MODIFIER_KEYS[raw]) { out.modifier = true; return out; }
    if (out.meta) { out.why = 'the Windows key belongs to the system: the app never binds it'; return out; }
    var name = keyName(raw);
    if (!name) { out.why = 'the app does not bind this key (F1-F24, letters, digits, punctuation, Enter, Esc, Space, arrows, Home / End, Page Up / Down, Insert, Delete, Backspace)'; return out; }
    var ctrl = out.ctrl, alt = out.alt;
    var altgr = false;
    try { altgr = !!(e.getModifierState && e.getModifierState('AltGraph')); } catch (x) { altgr = false; }
    if (altgr && name.length === 1) { ctrl = false; alt = false; }          /* AltGr+key types a character */
    var shift = out.shift && !(name.length === 1 && !/^[A-Z]$/.test(name));
    out.combo = (ctrl ? 'Ctrl+' : '') + (alt ? 'Alt+' : '') + (shift ? 'Shift+' : '') + name;
    return out;
  }

  /* ---------------------------------------------------------------- keys a browser keeps --------------------------- */
  /* never bindable (refused with the reason; api.py KEYBIND_REFUSED holds the same list) */
  var REFUSED = {
    'Ctrl+W': 'the browser closes the tab or window with it - the app never sees it',
    'Ctrl+Shift+W': 'the browser closes the window with it - the app never sees it',
    'Ctrl+T': 'the browser opens a new tab with it - the app never sees it',
    'Ctrl+Shift+T': 'the browser reopens the last closed tab with it - the app never sees it',
    'Ctrl+N': 'the browser opens a new window with it - the app never sees it',
    'Ctrl+Shift+N': 'the browser opens a private window with it - the app never sees it',
    'Ctrl+Tab': 'the browser switches tabs with it - the app never sees it',
    'Ctrl+Shift+Tab': 'the browser switches tabs with it - the app never sees it',
    'Ctrl+L': 'the browser puts the cursor in its address bar with it',
    'Alt+F4': 'Windows closes the window with it - the app never sees it',
    'Alt+Tab': 'Windows switches windows with it - the app never sees it',
    'Alt+Shift+Tab': 'Windows switches windows with it - the app never sees it',
    'Ctrl+F4': 'the browser closes the tab with it - the app never sees it',
    'Ctrl+PageUp': 'the browser switches tabs with it - the app never sees it',
    'Ctrl+PageDown': 'the browser switches tabs with it - the app never sees it',
    'Ctrl+Escape': 'Windows opens the Start menu with it - the app never sees it',
    'Ctrl+Shift+Escape': 'Windows opens the Task Manager with it - the app never sees it',
    'Alt+Escape': 'Windows switches windows with it - the app never sees it',
    'Tab': 'it moves the cursor between boxes and buttons',
    'Shift+Tab': 'it moves the cursor between boxes and buttons'
  };
  /* bindable, with a visible note: some browsers keep them in some windows ("Test a key" shows what arrives) */
  var MAY_KEEP = {
    'F11': 'some browsers switch to full screen with F11 and never pass it on',
    'F12': 'some browsers open their developer tools with F12 and never pass it on',
    'Ctrl+F5': 'some browsers reload without the cache before the app sees it',
    'Shift+F10': 'some browsers open the right-click menu with Shift+F10',
    'Ctrl+Shift+I': 'some browsers open their developer tools with it',
    'Ctrl+Shift+J': 'some browsers open their developer tools with it',
    'Ctrl+Shift+C': 'some browsers open their developer tools with it',
    'Alt+Space': 'Windows may open the window menu with it'
  };
  for (var tn = 1; tn <= 9; tn++) { MAY_KEEP['Ctrl+' + tn] = 'some browsers switch to tab ' + tn + ' with it'; }
  /* what the browser does with a key the app takes over once it is bound (shown so a bound F5 is no surprise) */
  var BROWSER_USE = { 'F1': 'help', 'F3': 'find next', 'F5': 'reload', 'F6': 'address bar', 'F7': 'caret browsing', 'F10': 'menu',
    'Ctrl+F': 'find', 'Ctrl+G': 'find next', 'Ctrl+P': 'print', 'Ctrl+S': 'save page', 'Ctrl+R': 'reload', 'Ctrl+D': 'bookmark', 'Ctrl+H': 'history',
    'Ctrl+J': 'downloads', 'Ctrl+U': 'page source', 'Ctrl+O': 'open file', 'Ctrl+E': 'search', 'Ctrl+K': 'search', 'Alt+ArrowLeft': 'back',
    'Alt+ArrowRight': 'forward', 'Alt+Home': 'home page', 'Ctrl+=': 'zoom in', 'Ctrl+-': 'zoom out', 'Ctrl+0': 'zoom reset' };
  /* the text-editing keys of every box: bound to an action they never act while the cursor is in a text box (the box keeps
     them), only elsewhere; Settings > Keyboard says so next to the key */
  var TEXT_EDIT = { 'Ctrl+A': 'select all', 'Ctrl+C': 'copy', 'Ctrl+V': 'paste', 'Ctrl+X': 'cut', 'Ctrl+Z': 'undo', 'Ctrl+Y': 'redo',
    'Ctrl+Shift+Z': 'redo', 'Ctrl+Shift+V': 'paste as plain text', 'Ctrl+Backspace': 'delete the word before the cursor', 'Ctrl+Delete': 'delete the word after the cursor',
    'Ctrl+ArrowLeft': 'move a word left', 'Ctrl+ArrowRight': 'move a word right', 'Ctrl+Shift+ArrowLeft': 'select a word left',
    'Ctrl+Shift+ArrowRight': 'select a word right', 'Ctrl+Home': 'go to the start of the box', 'Ctrl+End': 'go to the end of the box',
    'Ctrl+Shift+Home': 'select to the start of the box', 'Ctrl+Shift+End': 'select to the end of the box' };
  function refused(combo) { return REFUSED[combo] || null; }
  function mayKeep(combo) { return MAY_KEEP[combo] || null; }
  function browserUse(combo) { return BROWSER_USE[combo] || null; }
  function textEdit(combo) { return TEXT_EDIT[combo] || null; }

  /* ---------------------------------------------------------------- the actions --------------------------------- */
  var GROUPS = ['Navigation', 'Books', 'Pages', 'Order ticket', 'Grids', 'Other'];
  var GROUP_WHERE = { Navigation: 'anywhere', Books: 'anywhere', Pages: 'anywhere', 'Order ticket': 'in the ticket on the right, the FX desk and the swap / OTC / CDX tickets (its show / hide key works anywhere)',
                      Grids: 'in a table: the selected row', Other: 'anywhere' };
  var SCOPE_WORDS = { global: 'anywhere', dialog: 'while a dialog or the keyboard help is open', ticket: 'in the order ticket', grid: 'in a table, on the selected row' };
  /* the files that handle a page-level action today, with the component each one registers (a file that is not loaded
     never holds an action back) and the plain name shown in Settings */
  var FILES = {
    'components/ticket.js': { component: 'ticket', name: 'the order ticket' },
    'components/bondticket.js': { component: 'bondticket', name: 'the bond ticket' },
    'components/privateticket.js': { component: 'privateticket', name: 'the private markets ticket' },
    'components/swapticket.js': { component: 'swapticket', name: 'the swap ticket' },
    'components/otcticket.js': { component: 'otcticket', name: 'the OTC options and forwards ticket' },
    'components/cdsticket.js': { component: 'cdsticket', name: 'the CDX ticket' },
    'components/fxdesk.js': { component: 'fxdesk', name: 'the FX desk' },
    'components/grid.js': { component: 'grid', name: 'tables' }
  };
  var TICKETS3 = ['components/ticket.js', 'components/bondticket.js', 'components/privateticket.js'];
  /* preview / submit: every ticket and desk (the swap, OTC and CDX tickets book through their confirmation dialog) */
  var DESKS = TICKETS3.concat(['components/fxdesk.js', 'components/swapticket.js', 'components/otcticket.js', 'components/cdsticket.js']);
  var CLEARS = TICKETS3.concat(['components/swapticket.js']);
  var TICKETS2 = ['components/ticket.js', 'components/bondticket.js'];
  var TICKET1 = ['components/ticket.js'];
  var GRID = ['components/grid.js'];
  var FAMILY = {
    'nav.accounts': 'Account 1 .. 9 of the selected book (sidebar order)',
    'book.select': 'Select the book: Personal / Fund / Institutional',
    'ticket.sides': 'Side: buy / sell / sell short / buy to cover (options: buy to open / sell to open / sell to close / buy to close)',
    'ticket.types': 'Order type: market / limit / stop / stop limit / trailing stop',
    'ticket.qty': 'Quantity: up one lot / up ten lots / down one lot / down ten lots (the numpad + and - step one lot)',
    'ticket.hedges': 'Pick hedge candidate 1 .. 5',
    'grid.move': 'Move the selection up / down',
    'grid.trade': 'Ticket for the selected row: buy / sell (options: buy to open / sell to open)'
  };
  function scopeOf(id) { var p = String(id).split('.')[0]; return (p === 'ticket' || p === 'grid' || p === 'dialog') ? p : 'global'; }
  var ACTIONS = [];
  var BY_ID = Object.create(null);
  function act(id, group, key, label, o) {
    o = o || {};
    var a = { id: id, group: group, scope: scopeOf(id), key: key || null, label: label, typing: !!o.typing, files: o.files || null,
              legacy: o.legacy || null, family: o.family || null, note: o.note || null, page: o.page || null };
    ACTIONS.push(a); BY_ID[id] = a;
  }
  /* Navigation: the selected book's accounts (F1 - F6) and the pages of the account you are on */
  act('nav.us_exchange', 'Navigation', 'F1', 'US Exchange (Markets)');
  act('nav.debt', 'Navigation', 'F2', 'Debt Instruments (Markets)');
  act('nav.credit', 'Navigation', 'F3', 'Credit Instruments (Markets)');
  act('nav.foreign_fx', 'Navigation', 'F4', 'Foreign Markets & FX (Markets)');
  act('nav.private', 'Navigation', 'F5', 'Private Markets (a book without it: which book has it)');
  act('nav.prime_broker', 'Navigation', 'F6', 'Prime broker account of the book (again: the next one)');
  act('nav.orders', 'Navigation', 'F8', 'Orders of the account you are on (elsewhere: US Exchange)');
  act('nav.positions', 'Navigation', 'Shift+F4', 'Positions of the account you are on (elsewhere: US Exchange)');
  act('nav.cash', 'Navigation', 'Shift+F5', 'Cash & financing of the account you are on (elsewhere: US Exchange)');
  act('nav.transactions', 'Navigation', 'Shift+F6', 'Transactions of the account you are on (elsewhere: US Exchange)');
  act('nav.markets', 'Navigation', null, 'Markets of the account you are on (elsewhere: US Exchange)');
  for (var n = 1; n <= 9; n++) { act('nav.account_' + n, 'Navigation', null, 'Account ' + n + ' of the selected book (sidebar order)', { family: 'nav.accounts' }); }
  /* Books */
  act('book.personal', 'Books', 'Shift+F1', 'Select the Personal book', { family: 'book.select' });
  act('book.fund', 'Books', 'Shift+F2', 'Select the Fund book', { family: 'book.select' });
  act('book.institutional', 'Books', 'Shift+F3', 'Select the Institutional book', { family: 'book.select' });
  act('book.next', 'Books', null, 'Next book (Personal, Fund, Institutional in turn)');
  /* Pages (an action naming a `page` is shown only while that page is in the build: T.keys.available) */
  act('page.dashboard', 'Pages', 'F12', 'Dashboard', { page: 'dashboard' });
  act('page.portfolio', 'Pages', 'F7', 'Portfolio');
  act('page.treasury', 'Pages', 'F10', 'Treasury');
  act('page.risk', 'Pages', 'F11', 'Risk');
  act('page.accounting', 'Pages', 'Shift+F7', 'Accounting');
  act('page.activity', 'Pages', 'Shift+F8', 'Activity');
  act('page.watchlists', 'Pages', 'Shift+F9', 'Watchlists');
  act('page.history', 'Pages', 'Shift+F10', 'History of the book\'s last past lifetime');
  act('page.settings', 'Pages', 'Shift+F11', 'Settings');
  act('page.trading', 'Pages', null, 'Trading: the last venue you used (else US Exchange)');
  act('page.pending', 'Pages', null, 'Pending Trades (on an account page: that account\'s)', { page: 'pending' });
  act('page.completed', 'Pages', null, 'Completed Trades (on an account page: that account\'s)', { page: 'completed' });
  act('page.accounts', 'Pages', null, 'Accounts', { page: 'accounts' });
  act('page.cash_collateral', 'Pages', null, 'Cash & Collateral (on an account page: its Cash & financing)', { page: 'cash_collateral' });
  act('page.derivatives', 'Pages', null, 'Derivatives', { page: 'derivatives' });
  /* Order ticket: the dock (anywhere) and the keys inside the ticket (their current keys are the defaults) */
  act('dock.toggle', 'Order ticket', 'F9', 'Show / hide the order ticket (on an account page)');
  act('ticket.preview', 'Order ticket', 'Enter', 'Preview the order (in the symbol box: look the symbol up)', { typing: true, files: DESKS });
  act('ticket.submit', 'Order ticket', 'Ctrl+Enter', 'Submit the order or book the deal (also from outside the ticket while it is open)', { typing: true, files: DESKS });
  act('ticket.clear', 'Order ticket', 'Escape', 'Close the symbol list, else clear the ticket', { typing: true, files: CLEARS });
  act('ticket.buy', 'Order ticket', 'B', 'Side: buy (options: buy to open)', { files: TICKETS2, legacy: 'b', family: 'ticket.sides' });
  act('ticket.sell', 'Order ticket', 'S', 'Side: sell (options: sell to open)', { files: TICKETS2, legacy: 's', family: 'ticket.sides' });
  act('ticket.short', 'Order ticket', 'X', 'Side: sell short (options: sell to close)', { files: TICKET1, legacy: 'x', family: 'ticket.sides' });
  act('ticket.cover', 'Order ticket', 'C', 'Side: buy to cover (options: buy to close)', { files: TICKET1, legacy: 'c', family: 'ticket.sides' });
  act('ticket.market', 'Order ticket', 'M', 'Order type: market', { files: TICKET1, legacy: 'm', family: 'ticket.types' });
  act('ticket.limit', 'Order ticket', 'L', 'Order type: limit', { files: TICKET1, legacy: 'l', family: 'ticket.types' });
  act('ticket.stop', 'Order ticket', 'K', 'Order type: stop', { files: TICKET1, legacy: 'k', family: 'ticket.types' });
  act('ticket.stop_limit', 'Order ticket', 'J', 'Order type: stop limit', { files: TICKET1, legacy: 'j', family: 'ticket.types' });
  act('ticket.trailing', 'Order ticket', 'R', 'Order type: trailing stop', { files: TICKET1, legacy: 'r', family: 'ticket.types' });
  act('ticket.qty_up', 'Order ticket', '=', 'Quantity up one lot', { files: TICKET1, legacy: '=', family: 'ticket.qty' });
  act('ticket.qty_up_10', 'Order ticket', '+', 'Quantity up ten lots', { files: TICKET1, legacy: '+', family: 'ticket.qty' });
  act('ticket.qty_down', 'Order ticket', '-', 'Quantity down one lot', { files: TICKET1, legacy: '-', family: 'ticket.qty' });
  act('ticket.qty_down_10', 'Order ticket', '_', 'Quantity down ten lots', { files: TICKET1, legacy: '_', family: 'ticket.qty' });
  act('ticket.hedge', 'Order ticket', 'H', 'Open / close the hedge panel', { files: TICKET1, legacy: 'h' });
  for (var h = 1; h <= 5; h++) { act('ticket.hedge_' + h, 'Order ticket', String(h), 'Pick hedge candidate ' + h, { files: TICKET1, legacy: String(h), family: 'ticket.hedges' }); }
  act('ticket.hedge_none', 'Order ticket', '0', 'No hedge', { files: TICKET1, legacy: '0' });
  /* Grids: the selected row of a table (the pages' onKey callbacks receive the legacy letter) */
  act('grid.up', 'Grids', 'ArrowUp', 'Move the selection up', { files: GRID, family: 'grid.move' });
  act('grid.down', 'Grids', 'ArrowDown', 'Move the selection down', { files: GRID, family: 'grid.move' });
  act('grid.open', 'Grids', 'Enter', 'Open the selected row (instrument page, detail)', { files: GRID });
  act('grid.buy', 'Grids', 'B', 'Ticket for the selected row: buy (options: buy to open)', { files: GRID, legacy: 'b', family: 'grid.trade' });
  act('grid.sell', 'Grids', 'S', 'Ticket for the selected row: sell (options: sell to open)', { files: GRID, legacy: 's', family: 'grid.trade' });
  act('grid.cancel', 'Grids', 'X', 'Cancel the selected order (Orders)', { files: GRID, legacy: 'x' });
  act('grid.round', 'Grids', 'R', 'Record a reported funding round for the selected company (Private Markets)', { files: GRID, legacy: 'r' });
  /* Other */
  act('app.search', 'Other', '/', 'Search box of the page (or the ticket\'s symbol box)');
  act('app.help', 'Other', '?', 'Keyboard help');
  act('app.help_2', 'Other', 'Shift+F12', 'Keyboard help (second key)');
  act('dialog.close', 'Other', 'Escape', 'Close a dialog or the keyboard help', { typing: true });

  /* ---------------------------------------------------------------- bindings ------------------------------------ */
  var overrides = Object.create(null);                         /* the owner's overrides: id -> key | null */
  var adopted = Object.create(null);                           /* files that read the registry (T.keys.uses) */
  var eff = Object.create(null);                               /* id -> the key in force | null */
  var lost = Object.create(null);                              /* id -> the action whose override took its default key */
  var byKey = Object.create(null);                             /* key -> [ids] */
  function clash(a, b) { return !!a && !!b && a.id !== b.id && (a.scope === b.scope || a.scope === 'global' || b.scope === 'global'); }
  function present(f) { var d = FILES[f]; return !!(d && T.components && T.components[d.component]); }
  /* the files that still hold the action at its default key (handle it today and have not called T.keys.uses) */
  function holdingFiles(a) { return (a && a.files || []).filter(function (f) { return present(f) && !adopted[f]; }); }
  function rebindable(a) { return !!a && !holdingFiles(a).length; }
  function overridden(id) {
    var a = BY_ID[id];
    if (!a || !rebindable(a) || !Object.prototype.hasOwnProperty.call(overrides, id)) { return false; }
    var v = overrides[id];
    return v === null || (valid(v) && !REFUSED[v]);
  }
  function rebuild() {
    eff = Object.create(null); lost = Object.create(null); byKey = Object.create(null);
    ACTIONS.forEach(function (a) { eff[a.id] = overridden(a.id) ? overrides[a.id] : a.key; });
    /* a default key an override has taken (a newer build's defaults against older overrides) is given up, never shared */
    ACTIONS.forEach(function (a) {
      if (!eff[a.id] || overridden(a.id)) { return; }
      for (var i = 0; i < ACTIONS.length; i++) {
        var b = ACTIONS[i];
        if (overridden(b.id) && eff[b.id] === eff[a.id] && clash(a, b)) { lost[a.id] = b.id; eff[a.id] = null; return; }
      }
    });
    ACTIONS.forEach(function (a) { var k = eff[a.id]; if (k) { (byKey[k] = byKey[k] || []).push(a.id); } });
  }
  function clean(map) {
    var out = Object.create(null);
    if (map && typeof map === 'object') {
      Object.keys(map).forEach(function (id) { var v = map[id]; if (/^[a-z0-9_.]{1,64}$/.test(id) && (v === null || valid(v))) { out[id] = v; } });
    }
    return out;
  }
  function plain(map) { var o = {}; Object.keys(map).forEach(function (k) { o[k] = map[k]; }); return o; }
  var CACHE = 'finsim.keybinds';
  function cacheGet() { try { var v = localStorage.getItem(CACHE); return v ? JSON.parse(v) : null; } catch (e) { return null; } }
  function cacheSet(map) { try { localStorage.setItem(CACHE, JSON.stringify(map)); } catch (e) { /* private window: the server copy still applies at boot */ } }
  var loadedFromServer = false;
  function emit() { if (T.state && T.state.emit) { T.state.emit('keybinds', eff); } }
  /* the owner's overrides from the server (GET /api/state or /api/settings .keybinds); remembered in this browser */
  function load(map, fromServer) {
    var next = clean(map);
    var same = JSON.stringify(plain(next)) === JSON.stringify(plain(overrides));
    overrides = next;
    if (fromServer !== false) { loadedFromServer = true; cacheSet(plain(next)); }
    rebuild();
    if (!same) { emit(); }
    return eff;
  }
  /* a file that reads the registry: its actions follow the owner's keys from now on */
  function uses(file) {
    if (!file || adopted[file]) { return; }
    adopted[file] = true;
    rebuild(); emit();
  }

  /* ---------------------------------------------------------------- matching key presses ------------------------- */
  function typingTarget(t) {
    var tag = t && t.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || !!(t && t.isContentEditable);
  }
  /* may the key in force act for this action now: F-keys and Ctrl / Alt always (a text-editing key never in a box), a
     single character never while typing, a named key while typing only for the actions that work in boxes */
  function fires(combo, a, typing) {
    if (typing && TEXT_EDIT[combo]) { return false; }
    if (!typing || freeWhileTyping(combo)) { return true; }
    if (printable(combo)) { return false; }
    return !!(a && a.typing);
  }
  /* Enter / Space on a focused button, link, tab or check box is the browser's own "press it": no action takes it there */
  var PRESSABLE = /^(button|link|tab|menuitem|option|checkbox|radio|switch)$/;
  function presses(e, combo) {
    if (combo !== 'Enter' && combo !== 'Space') { return false; }
    var t = e && e.target;
    if (!t || !t.tagName) { return false; }
    var tag = t.tagName, type = String(t.type || '').toLowerCase(), role = String((t.getAttribute && t.getAttribute('role')) || '').toLowerCase();
    if (tag === 'BUTTON' || tag === 'SUMMARY' || (tag === 'A' && t.hasAttribute && t.hasAttribute('href'))) { return true; }
    if (tag === 'INPUT' && /^(button|submit|reset|image|file|checkbox|radio)$/.test(type)) { return true; }
    return PRESSABLE.test(role);
  }
  function infoOf(e) {
    if (e && e.__fsKey && e.__fsKey.ev === e) { return e.__fsKey.info; }
    var info = fromEvent(e);
    try { e.__fsKey = { ev: e, info: info }; } catch (x) { /* a frozen event: computed again next time */ }
    return info;
  }
  /* the action of `scope` (global / dialog / ticket / grid) bound to this key press and allowed to act now, else null */
  function action(e, scope) {
    var info = infoOf(e); if (!info.combo) { return null; }
    if (presses(e, info.combo)) { return null; }
    var list = byKey[info.combo] || [];
    var typing = typingTarget(e.target);
    for (var i = 0; i < list.length; i++) {
      var a = BY_ID[list[i]];
      if (a.scope === scope && fires(info.combo, a, typing)) { return a.id; }
    }
    return null;
  }
  /* true when this key press is the action's key in force and the action may act now (Cmd+Enter on a Mac is 'Submit the
     order' while that action keeps its default Ctrl+Enter - the alias 1.0.3 had) */
  function is(e, id) {
    var a = BY_ID[id]; var k = eff[id];
    if (!a || !k) { return false; }
    var info = infoOf(e);
    if (!info.combo && info.meta && !info.ctrl && !info.alt && a.key === 'Ctrl+Enter' && k === 'Ctrl+Enter' && info.raw === 'Enter') { return true; }
    return info.combo === k && !presses(e, k) && fires(k, a, typingTarget(e.target));
  }
  /* the actions holding `combo` that would clash with action `id` (two actions never share a key where both act) */
  function holders(id, combo, map) {
    var a = BY_ID[id]; if (!a || !combo) { return []; }
    var m = map || eff;
    return ACTIONS.filter(function (b) { return b.id !== id && m[b.id] === combo && clash(a, b); }).map(function (b) { return b.id; });
  }
  /* every pair of clashing actions of a complete map (empty when the map is sound) */
  function clashes(map) {
    var out = []; var by = Object.create(null);
    ACTIONS.forEach(function (a) { var k = map[a.id]; if (k) { (by[k] = by[k] || []).push(a); } });
    Object.keys(by).forEach(function (k) {
      var l = by[k];
      for (var i = 0; i < l.length; i++) { for (var j = i + 1; j < l.length; j++) { if (clash(l[i], l[j])) { out.push([k, l[i].id, l[j].id]); } } }
    });
    return out;
  }

  /* ---------------------------------------------------------------- display ------------------------------------- */
  function esc(s) { return T.esc ? T.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function kbdHtml(combo) { return combo ? '<kbd>' + esc(display(combo)) + '</kbd>' : '<span class="kb-none">no key</span>'; }
  function binding(id) { return BY_ID[id] ? (eff[id] || null) : null; }
  function label(id) { var k = binding(id); return k ? display(k) : ''; }
  /* "F1" -> "F1 " prefix helper for hints: '(F1)' or '' when the action has no key */
  function paren(id) { var l = label(id); return l ? ' (' + l + ')' : ''; }
  /* the keys in force of several actions for one hint: labels(['grid.buy', 'grid.sell']) -> 'B / S' ('' when none has a key) */
  function labels(ids, sep) { return (ids || []).map(label).filter(Boolean).join(sep || ' / '); }
  /* an action works in this build: it names no page, or its page is loaded (T.pages.<page>) - else the help and Settings >
     Keyboard leave it out (FINSIM1_PLAN 15.7 item 3: never a key on screen for a page that is not built) */
  function available(id) {
    var a = BY_ID[id]; if (!a) { return false; }
    return !a.page || !!(T.pages && T.pages[a.page]);
  }
  function groups() {
    return GROUPS.map(function (g) { return { name: g, where: GROUP_WHERE[g], actions: ACTIONS.filter(function (a) { return a.group === g && available(a.id); }) }; });
  }
  function defaults() { var o = {}; ACTIONS.forEach(function (a) { o[a.id] = a.key; }); return o; }
  function fileNames(list) {
    var names = (list || []).map(function (f) { return (FILES[f] && FILES[f].name) || f; });
    if (names.length < 2) { return names.join(''); }
    return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
  }

  /* ---------------------------------------------------------------- export -------------------------------------- */
  K.ACTIONS = ACTIONS; K.GROUPS = GROUPS; K.GROUP_WHERE = GROUP_WHERE; K.SCOPE_WORDS = SCOPE_WORDS; K.FAMILY = FAMILY; K.FILES = FILES;
  K.REFUSED = REFUSED; K.MAY_KEEP = MAY_KEEP; K.BROWSER_USE = BROWSER_USE; K.TEXT_EDIT = TEXT_EDIT;
  K.get = function (id) { return BY_ID[id] || null; };
  K.scopeOf = scopeOf; K.parse = parse; K.valid = valid; K.printable = printable; K.freeWhileTyping = freeWhileTyping; K.display = display;
  K.fromEvent = fromEvent; K.refused = refused; K.mayKeep = mayKeep; K.browserUse = browserUse; K.textEdit = textEdit; K.typingTarget = typingTarget;
  K.presses = presses;
  K.fires = fires; K.action = action; K.is = is; K.holders = holders; K.clashes = clashes; K.clash = function (a, b) { return clash(BY_ID[a], BY_ID[b]); };
  K.kbd = function (idOrCombo) { return kbdHtml(BY_ID[idOrCombo] ? binding(idOrCombo) : idOrCombo); };
  K.binding = binding; K.label = label; K.paren = paren; K.labels = labels; K.groups = groups; K.defaults = defaults; K.available = available;
  K.overrides = function () { return plain(overrides); };
  K.overridden = overridden; K.lost = function (id) { return lost[id] || null; }; K.lostAll = function () { return plain(lost); };
  K.bindings = function () { return plain(eff); };
  /* the actions (any scope) whose key in force is `combo` */
  K.boundTo = function (combo) { return (byKey[combo] || []).slice(); };
  /* recompute after the components loaded (core.js boot): a file that is not loaded never holds an action at its default */
  K.refresh = function () { rebuild(); emit(); };
  K.load = load; K.loaded = function () { return loadedFromServer; };
  /* the owner's keys are known: from the server, or the copy this browser kept (a new browser profile has neither until
     /api/state answers - core.js then holds the global keys back instead of running a default the owner moved) */
  K.ready = function () { return loadedFromServer || hadCache; };
  K.uses = uses; K.adopted = function (f) { return !!adopted[f]; }; K.rebindable = function (id) { return rebindable(BY_ID[id]); };
  K.holdingFiles = function (id) { return holdingFiles(BY_ID[id]); }; K.fileNames = fileNames;
  /* the letter a page's grid onKey callback compares ('b', 's', 'x', 'r') and the ticket's legacy key of an action */
  K.legacy = function (id) { var a = BY_ID[id]; return a ? a.legacy : null; };
  /* the key in force for an action of `scope` (other packages' hints): e.g. T.keys.keyOf('grid.buy') -> 'B' */
  K.keyOf = binding;

  /* the owner's keys as last received from the server, before anything paints (no flash of the defaults on a reload) */
  var cached = cacheGet();
  var hadCache = cached !== null && typeof cached === 'object';
  load(cached || {}, false);
  /* another window of this browser saved keys (or read them from the server): follow at once */
  try {
    window.addEventListener('storage', function (e) {
      if (!e || e.key !== CACHE) { return; }
      var m = null;
      try { m = e.newValue ? JSON.parse(e.newValue) : {}; } catch (x) { m = null; }
      if (m && typeof m === 'object') { hadCache = true; load(m, false); }
    });
  } catch (x) { /* no storage events: the focus re-read in core.js still follows other windows */ }
})();
