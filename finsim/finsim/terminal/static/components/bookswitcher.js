/* Book switcher (TERMINAL_DESIGN.md s17.2, s13.10; WP-12): the compact three-entry switcher at the top of the sidebar
 * (short name, compact NAV with its quality, eligibility dot); the book keys of Settings > Keyboard (Shift+F1 / F2 / F3 by
 * default; 'Next book' has no default key) select one, the hints name the keys in force; the selected book is remembered in
 * localStorage (T.book).  Every poller passes `book=<code>` (core.js) so every page follows the selected book's open
 * lifetime.  Rows come from GET /api/books: {book_code, name, profile, profile_label, lifetime: {lifetime_id, seq, started,
 * starting_capital}, nav (a decimal string or {value, quality}), eligibility: {pb_otc, pb_otc_reason, private_markets,
 * private_reason, ecp, threshold, qp_threshold, has_relationships, can_onboard}, accounts: [...], past_lifetimes}.
 * The dot: green = prime brokers & OTC eligible, amber = ECP only, hollow = neither.  SHELL-17: the names are short
 * ('Personal' for 'Personal Book') and the NAV compact ('1.00m', '1.00bn') so nothing is cut at 1100 px; the full name,
 * the exact NAV and the eligibility sentences are the hover text (the sidebar rows carry one short reason each - the
 * sentences are not repeated under the switcher).
 */
(function () {
  'use strict';
  var T = window.T;
  function navOf(b) {
    var nav = b.nav;
    if (nav && typeof nav === 'object') { return { text: T.money(nav, 'value'), raw: T.num(nav, 'value'), quality: nav.quality }; }
    return { text: T.money(b, 'nav'), raw: T.num(b, 'nav'), quality: b.nav_quality || null };
  }
  function shortName(b) { return String(b.name || b.book_code).replace(/\s+book$/i, ''); }
  /* the keys in force (keys.js registry): the book's own select key and 'Next book' */
  var BOOK_KEY = { P: 'book.personal', F: 'book.fund', I: 'book.institutional' };
  var PROFILE_KEY = { INDIVIDUAL: 'book.personal', FUND: 'book.fund', INSTITUTION: 'book.institutional' };
  function keyOf(id) { return id && T.keys && T.keys.label ? T.keys.label(id) : ''; }
  function bookKey(b) { return keyOf(BOOK_KEY[b.book_code] || PROFILE_KEY[b.profile]); }
  /* 'Shift+F1 / F2 / F3 books' (the modifiers the keys share are said once) . 'Alt+B next' */
  function keysHint(books) {
    var ks = books.map(bookKey).filter(Boolean), nx = keyOf('book.next');
    var parts = [];
    if (ks.length) {
      var pre = (/^((?:Ctrl\+|Alt\+|Shift\+)*)/.exec(ks[0]) || ['', ''])[1];
      parts.push(ks.map(function (k, i) { return i && pre && k.indexOf(pre) === 0 && k.length > pre.length ? k.slice(pre.length) : k; }).join(' / ') + (ks.length === 1 ? ' book' : ' books'));
    }
    if (nx) { parts.push(nx + ' next'); }
    return parts.join(' . ');
  }
  function render() {
    var root = T.q('#bookswitcher'); if (!root) { return; }
    var books = T.state.books || [];
    T.clear(root);
    if (!books.length) { root.appendChild(T.h('div', { class: 'bs-hint' }, T.state.server_ok === false ? 'books: server unreachable' : 'loading books ...')); return; }
    var list = T.h('div', { class: 'bs', role: 'tablist', 'aria-label': 'books' });
    var active = T.book.current();
    books.forEach(function (b) {
      var e = b.eligibility || {};
      var dot = e.pb_otc ? 'pb' : (e.ecp ? 'ecp' : '');
      var nav = navOf(b);
      var isActive = active && active.book_code === b.book_code;
      var item = T.h('button', { class: 'bs-item' + (isActive ? ' active' : ''), role: 'tab', 'aria-selected': isActive ? 'true' : 'false',
        title: (b.name || b.book_code) + (b.profile_label || b.profile ? ' . ' + (b.profile_label || b.profile) : '') + (b.lifetime ? ' . lifetime ' + b.lifetime.lifetime_id : '') +
               '\nNAV ' + nav.text + (nav.quality ? ' (' + nav.quality + ')' : '') + (e.pb_otc_reason ? '\nprime brokers & OTC: ' + e.pb_otc_reason : '') +
               (e.private_reason ? '\nPrivate Markets: ' + e.private_reason : '') + (bookKey(b) ? '\n' + bookKey(b) + ' selects it' : '') +
               (keyOf('book.next') ? '\n' + keyOf('book.next') + ' switches to the next book' : ''),
        onclick: function () { T.book.set(b.book_code); } },
        T.h('span', { class: 'bs-dot ' + dot, title: e.pb_otc ? 'prime brokers & OTC eligible' : (e.ecp ? 'ECP floor reached; below the PB/OTC threshold' : 'below the ECP floor') }),
        T.h('span', { class: 'bs-name' }, shortName(b)),
        T.h('span', { class: 'bs-nav', title: 'NAV of the open lifetime ' + nav.text + (nav.quality ? ' (' + nav.quality + ')' : '') }, nav.raw == null ? nav.text : T.fmt.compact(nav.raw)));
      list.appendChild(item);
    });
    root.appendChild(list);
    if (active) {
      var kh = keysHint(books);
      var tip = (active.name || active.book_code) + (kh ? ' . ' + kh + ' (Settings > Keyboard)' : '');
      if (active.lifetime) { root.appendChild(T.h('div', { class: 'bs-hint', title: tip }, 'lifetime ' + active.lifetime.lifetime_id)); }
      if (kh) { root.appendChild(T.h('div', { class: 'bs-hint', title: tip }, kh)); }      /* its own line: never cut at 1100 px */
    }
  }
  function init() {
    T.state.on('books', render); T.state.on('book', render); T.state.on('server_ok', render); T.state.on('keybinds', render);
    render();
  }
  T.components.bookswitcher = { init: init, render: render, shortName: shortName };
})();
