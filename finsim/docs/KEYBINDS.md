# FinSim terminal - keyboard keys (F-keys and your own keys)

The owner's ask (2026-10-21 17:10): *"can you also key the stuff to the Fkeys rather than alt and stuff? go through this
all. like F1 opens up one exchange / let me code it in the app settings my keybinds"*.

Every key of the terminal is an **action** in one registry, `finsim/finsim/terminal/static/keys.js`: an id, a plain-English
label, a group, where it acts (its scope) and its **default** key. The defaults are on the F-keys; the Alt combinations and the
`g` sequences of 1.0.3 are gone as defaults (their actions stay, without a key, ready to be bound). Your own keys are saved in
the app (Settings > Keyboard) and every page follows them.

## The default map

### Anywhere (also while the cursor is in a box: an F-key types nothing)

| Key | Action |
| --- | --- |
| F1 | US Exchange - the selected book's US Exchange account, Markets |
| F2 | Debt Instruments, Markets |
| F3 | Credit Instruments, Markets |
| F4 | Foreign Markets & FX, Markets |
| F5 | Private Markets (a book without one: a message naming the book that has it, and its key) |
| F6 | The book's prime broker account (pressed again on one: the next; none: how to open one) |
| F7 | Portfolio |
| F8 | Orders of the account you are on (off an account page: the book's US Exchange) |
| F9 | Show / hide the order ticket (the 1.0.3 `t` rules: on an account page; elsewhere it only hides an open ticket) |
| F10 | Treasury |
| F11 | Risk |
| F12 | Daily News |
| Shift+F1 / Shift+F2 / Shift+F3 | Select the Personal / Fund / Institutional book |
| Shift+F4 | Positions of the account you are on (elsewhere: US Exchange) |
| Shift+F5 | Cash & financing of the account you are on (elsewhere: US Exchange) |
| Shift+F6 | Transactions of the account you are on (elsewhere: US Exchange) |
| Shift+F7 | Accounting |
| Shift+F8 | Activity |
| Shift+F9 | Watchlists |
| Shift+F10 | History of the book's last past lifetime (`#/h/<lifetime>/portfolio`; a book with none: a message) |
| Shift+F11 | Settings |
| Shift+F12 | Keyboard help |

### Single keys (never while you type in a box)

| Key | Action |
| --- | --- |
| / | Search box of the page (or the ticket's symbol box) |
| ? | Keyboard help |
| Esc | Close a dialog or the keyboard help (only while one is open; otherwise Esc goes to the ticket) |

### In the order ticket (their 1.0.3 keys are the defaults)

| Key | Action |
| --- | --- |
| Enter | Preview the order (in the symbol box: look the symbol up) - also in a box |
| Ctrl+Enter | Submit the order (also from outside the ticket while it is open) - also in a box |
| Esc | Close the symbol list, else clear the ticket - also in a box |
| B / S / X / C | Side: buy / sell / sell short / buy to cover (options: buy to open / sell to open / sell to close / buy to close) |
| M / L / K / J / R | Order type: market / limit / stop / stop limit / trailing stop |
| = / + / - / _ | Quantity: up one lot / up ten lots / down one lot / down ten lots (the numpad + and - step one lot) |
| H | Open / close the hedge panel |
| 1 .. 5 / 0 | Pick hedge candidate 1 .. 5 / no hedge |

### In a table (the selected row)

| Key | Action |
| --- | --- |
| Up / Down | Move the selection |
| Enter | Open the selected row (instrument page, detail) |
| B / S | Ticket for the selected row: buy / sell (options: buy to open / sell to open) |
| X | Cancel the selected order (Orders) |
| R | Record a reported funding round for the selected company (Private Markets) |

### Actions without a default key (bind them in Settings > Keyboard)

Markets of the account you are on; Account 1 .. 9 of the selected book (sidebar order: the old Alt+1 .. Alt+9); Next book
(the old Alt+B). The old `g` sequences opened pages that now have F-keys.

## How the keys behave

- **One key, one action.** Two actions may share a key only when they can never act at the same moment: the ticket and a table
  (B is "side: buy" in the ticket and "ticket for the row: buy" in a table), or a dialog and a widget (Esc closes a dialog, else
  clears the ticket). A key of an "anywhere" action belongs to nothing else.
- **First come, first stop.** The global keys are read by one listener that runs before the ticket and the tables
  (`window` capture phase, `core.js`). An F-key (or any Ctrl / Alt key) bound to a global action runs it and stops the key
  there: the ticket or a table never also acts on it. A held-down key repeats no global action.
- **Typing.** F-keys and Ctrl / Alt keys work while the cursor is in a text box (they type nothing). A single character
  (letters, digits, `/`, `?`, `+` ...) never acts while you type. Enter / Esc / arrows act in a box only for the actions made for
  boxes (ticket preview / submit / clear, close a dialog).
- **The browser.** A key bound to an action never reaches the browser's own use of it (F1 does not open the browser help, F5
  does not reload, F3 does not open find). A key bound to nothing keeps the browser's behaviour (Ctrl+R still reloads).
- **Dialogs.** While a dialog is open it takes no global key (it asks something first); its close key (Esc) closes the newest
  dialog only and counts as "no" / "cancel". While the keyboard help is open, a global key closes it and runs.

## Changing keys: Settings > Keyboard

`Settings > Keyboard` (`#/settings/keyboard`; the help overlay's **Change keys** button opens it) lists every action by group
with its key and a note:

- **Change** - press the new key. Esc cancels; Backspace or Delete leaves the action without a key. A key another action
  already uses asks **swap or cancel**: Swap gives the other action your old key (or no key when the old key would clash with a
  third action). It never puts two actions on one key.
- **Clear** - no key for this action. **Reset** - back to its default (a swap is asked if the default is in use).
  **Reset all to the F-key defaults** - after a confirmation.
- **Refused keys** (the browser or Windows never hands them to a page, or the page needs them), with the reason: Ctrl+W,
  Ctrl+Shift+W, Ctrl+T, Ctrl+Shift+T, Ctrl+N, Ctrl+Shift+N, Ctrl+Tab, Ctrl+Shift+Tab, Ctrl+L, Alt+F4, Alt+Tab, Alt+Shift+Tab,
  Tab, Shift+Tab.
- **Allowed with a note** (some browsers keep them in some windows): F11 (full screen), F12 (developer tools), Ctrl+F5,
  Shift+F10 (right-click menu), Ctrl+Shift+I / J / C, Ctrl+1 .. Ctrl+9, Ctrl+Page Up / Page Down, Alt+Space. A key the
  browser takes over (F1 help, F3 find, F5 reload, F6 address bar, F7 caret browsing, F10 menu, Ctrl+F, Ctrl+P ...) is noted
  "takes over the browser's ...".
- **Test a key** - click the box and press any key: it shows exactly what the app receives (key, code, modifiers), which
  action it runs, or "no action: the browser keeps it". Nothing runs while the box listens. A key that shows nothing never
  reached the app (pick another one). Do not try Ctrl+W, Ctrl+T, Ctrl+N or Alt+F4 there: the browser or Windows acts first.
  Laptops often need the Fn key for F1 - F12.
- An action of the ticket or of a table shows **fixed for now** until its file reads the registry (the contract below): its
  key cannot change yet, so the screen never shows a key the app would not honour.

Your keys are **saved in the app**, not in the browser: `PATCH /api/settings {"keybinds": {...}, "keybind_defaults": {...}}`
through `settings.set` (journaled with before / after like every setting, a `SETTINGS_CHANGED` event). Only your changes are
stored (`{action_id: key | null}`, null = no key; a key equal to its default is dropped); the defaults live in `keys.js`. They
apply in every browser, after a page reload, after a server restart and after a lifetime reset. The page loads them at boot from
`/api/state` (and keeps a copy in the browser so a reload paints them at once), again when the window comes back (another
window may have changed them) and after every save.

### The stored form (api.py checks it)

- action ids: `[a-z0-9_.]` (up to 64); with `keybind_defaults` an id the build does not have is refused.
- keys in ONE form: `Ctrl+Alt+Shift+Key`, the modifiers in that order. Key = `F1`-`F24`, `A`-`Z`, `0`-`9`, a named key
  (`Enter`, `Escape`, `Space`, `Tab`, `Backspace`, `Delete`, `Insert`, `Home`, `End`, `PageUp`, `PageDown`, `ArrowUp`,
  `ArrowDown`, `ArrowLeft`, `ArrowRight`) or one ASCII punctuation character. Shift is part of a punctuation or digit
  character (`?`, never `Shift+/`). The Windows key is never part of a binding.
- scope from the id: `ticket.*` the ticket, `grid.*` a table, `dialog.*` an open dialog, anything else "anywhere".
- the keys in force (the defaults sent by the page + the stored and new overrides) never put one key on two actions that can
  act together; a refused key is refused with its reason. Every refusal is `400 SETTING_VALUE` with
  `details.field = "keyboard keys"` (plus `action`, `key`, `actions` where they apply); nothing is written.

## The contract for the page-level keys (the files that are not keys.js / core.js)

Each file keeps working with its 1.0.3 keys until it is changed; once changed it follows the owner's keys. The registry calls:

- `T.keys.is(e, id)` - this key press is the action's key in force and the action may act now (single characters never while
  typing; F-keys and Ctrl / Alt always; Enter / Esc / arrows in a box only for the box actions).
- `T.keys.action(e, scope)` - the id of the `scope`'s action (`'ticket'`, `'grid'`) bound to this key press, or null.
- `T.keys.legacy(id)` - the 1.0.3 letter / digit of a ticket or table action (`'grid.buy'` -> `'b'`, `'ticket.trailing'` -> `'r'`,
  `'ticket.hedge_3'` -> `'3'`), so code that compares letters keeps working unchanged.
- `T.keys.label(id)` / `T.keys.paren(id)` - the key in force as text (`'F1'`, `'Shift+F4'`, `'Esc'`; `''` without a key) /
  `' (F1)'` - for tooltips and hints.
- `T.keys.uses('components/<file>.js')` - once at load, AFTER the file reads every key through the registry: from then on its
  actions can be changed in Settings (until then they show "fixed for now").
- `T.state.on('keybinds', fn)` - the keys changed (a save, another window): repaint hints.

### components/ticket.js (P10)

`onKey` (today ~line 603) becomes:

```js
  function onKey(e) {
    var K = T.keys;
    if (K.is(e, 'ticket.submit')) { e.preventDefault(); e.stopPropagation(); submit(); return; }
    if (K.is(e, 'ticket.clear')) { e.preventDefault(); e.stopPropagation(); if (!ui.dd.hidden) { ui.dd.hidden = true; return; } clear(); return; }
    if (K.is(e, 'ticket.preview')) { e.preventDefault(); e.stopPropagation(); if (e.target === ui.inst) { resolveTyped(); } else { runPreview(true); } return; }
    var id = K.action(e, 'ticket'); if (!id) { return; }          /* a single-character key never matches while typing */
    var k = K.legacy(id);                                          /* the letter SIDE_KEYS / TYPE_KEYS compare */
    var group = kindGroup(instKind()); var sk = SIDE_KEYS[group] || SIDE_KEYS.CASH;
    var step = { 'ticket.qty_up': 1, 'ticket.qty_up_10': 10, 'ticket.qty_down': -1, 'ticket.qty_down_10': -10 }[id];
    if (step) { stepQty(e.location === 3 ? (step > 0 ? 1 : -1) : step); }        /* the numpad + / - step one lot, as today */
    else if (sk[k] && sidesFor().indexOf(sk[k]) >= 0) { st.side = sk[k]; st.side_req = null; st.close_req = null; st.side_err = null; paintFields(); fieldChanged(); }
    else if (TYPE_KEYS[k]) { st.order_type = TYPE_KEYS[k]; paintFields(); fieldChanged(); }
    else if (id === 'ticket.hedge') { toggleHedge(); }
    else if (/^ticket\.hedge_[1-5]$/.test(id)) { if (hedge) { if (!st.hedgeOpen) { toggleHedge(); } hedge.pick(Number(k)); } }
    else if (id === 'ticket.hedge_none') { if (hedge) { hedge.none(); } }
    else { return; }
    e.preventDefault(); e.stopPropagation();
  }
```

The document listener (today ~line 186): `if (T.keys.is(e, 'ticket.submit') && !ui.root.contains(e.target)) { e.preventDefault(); submit(); }`.
Tooltips: `'submit' + T.keys.paren('ticket.submit')`, `'clear' + T.keys.paren('ticket.clear')`, the "ready" text with
`T.keys.label('ticket.submit')`, `'Hedges' + T.keys.paren('ticket.hedge')`. At the end of the file:
`T.keys.uses('components/ticket.js');`.

### components/grid.js (P03)

`handle.key` (today ~line 253) becomes - the pages' `onKey(k, row, e)` callbacks keep receiving `'b'`, `'s'`, `'x'`, `'r'`:

```js
    handle.key = function (e) {
      var id = T.keys.action(e, 'grid');
      if (id === 'grid.down') { select(state.sel + 1, true); return true; }
      if (id === 'grid.up') { select(state.sel - 1, true); return true; }
      var row = visibleRows[state.sel];
      if (id === 'grid.open') { if (row && spec.onEnter) { spec.onEnter(row); return true; } return false; }
      var k = id ? T.keys.legacy(id) : null;                       /* 'b' / 's' / 'x' / 'r' */
      if (k && spec.onKey && row) { return !!spec.onKey(k, row, e); }
      return false;
    };
```

and `T.keys.uses('components/grid.js');` at the end. (core.js's bubble listener already hands the selected table every key
no one else handled; until grid.js calls `uses` it keeps the 1.0.3 rule: never while typing, never with Ctrl / Alt.)

### components/bondticket.js (P07)

```js
    face.addEventListener('keydown', function (e) { if (T.keys.is(e, 'ticket.submit')) { e.preventDefault(); doSubmit(); } else if (T.keys.is(e, 'ticket.preview')) { e.preventDefault(); runPreview(); } });
    root.addEventListener('keydown', function (e) {
      var typing = e.target && (e.target.tagName === 'INPUT');
      if (T.keys.is(e, 'ticket.clear')) { dd.hidden = true; return; }
      if (typing) { return; }                                      /* the face box submits itself: no second submit here */
      if (T.keys.is(e, 'ticket.buy')) { setSide('BUY'); schedule(); e.preventDefault(); }
      else if (T.keys.is(e, 'ticket.sell')) { setSide('SELL'); schedule(); e.preventDefault(); }
      else if (T.keys.is(e, 'ticket.submit')) { e.preventDefault(); doSubmit(); }
    });
```

In the symbol box, with the list closed: `if (T.keys.is(e, 'ticket.preview')) { ... look the symbol up ... }`; the open list keeps
Up / Down / Enter (the standard list keys, not rebindable). Hints: `'Submit' + T.keys.paren('ticket.submit')`, "ready: " +
`T.keys.label('ticket.submit')` + " submits", "b / s on a row" -> `T.keys.label('grid.buy') + ' / ' + T.keys.label('grid.sell')`.
At the end: `T.keys.uses('components/bondticket.js');`. (Its bond list is a grid: its b / s come through grid.js.)

### components/privateticket.js (P11)

```js
    el.addEventListener('keydown', function (e) {
      if (T.keys.is(e, 'ticket.submit')) { e.preventDefault(); doSubmit(); }
      else if (T.keys.is(e, 'ticket.preview')) { e.preventDefault(); doPreview(); }
      else if (T.keys.is(e, 'ticket.clear')) { state.preview = null; T.clear(panel); submit.disabled = true; }
    });
```

Tooltip `'submit' + T.keys.paren('ticket.submit')`; at the end `T.keys.uses('components/privateticket.js');`.

### components/swapticket.js (P09) and components/otcticket.js (P12)

They have no keys today. If they add any, on the ticket's root element:

```js
    root.addEventListener('keydown', function (e) {
      if (T.keys.is(e, 'ticket.submit')) { e.preventDefault(); e.stopPropagation(); book(); }          /* the ticket's Book / Submit */
      else if (T.keys.is(e, 'ticket.preview')) { e.preventDefault(); e.stopPropagation(); preview(); }
      else if (T.keys.is(e, 'ticket.clear')) { e.preventDefault(); e.stopPropagation(); clearForm(); }
    });
    T.keys.uses('components/swapticket.js');                                            /* or components/otcticket.js */
```

Never `e.key === '...'` for an action the registry has; a new kind of key is a new action in keys.js (with its default) first.

### Hints in other files (integrator)

The 1.0.3 key hints left on screen are wrong now (those keys do nothing by default); they should read the registry:

- `components/sidebar.js`: the `key:` of each nav item -> `T.keys.label('page.news' | 'page.portfolio' | 'page.treasury' |
  'page.risk' | 'page.accounting' | 'page.activity' | 'page.watchlists' | 'page.settings')`; an account row ->
  `T.keys.label({US_EXCHANGE: 'nav.us_exchange', DEBT: 'nav.debt', CREDIT: 'nav.credit', FOREIGN_FX: 'nav.foreign_fx',
  PRIVATE: 'nav.private'}[role] || (/^PB_/.test(role) ? 'nav.prime_broker' : 'nav.account_' + (i + 1)))`; `init` adds
  `T.state.on('keybinds', render)`; the header comment "Alt+1..9 follow this order" -> "Account 1 .. 9 (Settings >
  Keyboard) follow this order".
- `components/bookswitcher.js`: the tooltip line "Alt+B cycles" -> `T.keys.label({P: 'book.personal', F: 'book.fund', I:
  'book.institutional'}[b.book_code]) + ' selects it'`; the hint "Alt+B switches" -> the three book keys
  (`Shift+F1 / Shift+F2 / Shift+F3 select a book`); `T.state.on('keybinds', render)`.
- `components/header.js`: "pick an account (Alt+1..9)" -> `'pick an account' + (T.keys.label('nav.us_exchange') ? ' (' +
  T.keys.label('nav.us_exchange') + ' opens US Exchange)' : '')`, its tooltip without Alt+1..9.
- `pages/markets_debt.js`: the dock hint "press b / s" -> the grid.buy / grid.sell labels.
