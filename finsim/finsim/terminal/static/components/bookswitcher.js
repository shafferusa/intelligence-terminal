/* Book switcher (TERMINAL_DESIGN.md s17.2, s13.10; WP-12): the compact three-entry switcher at the top of the sidebar
 * (name, NAV with its quality, eligibility dot); Alt+B cycles; the selected book is remembered in localStorage
 * (T.book).  Every poller passes `book=<code>` (core.js) so every page follows the selected book's open lifetime.
 * Rows come from GET /api/books: {book_code, name, profile, lifetime: {lifetime_id, seq, started, starting_capital},
 * nav (a decimal string or {value, quality}), eligibility: {pb_otc, pb_otc_reason, private_markets, private_reason, ecp,
 * threshold, qp_threshold, has_relationships, can_onboard}, accounts: [...], past_lifetimes}.  The dot: green = prime
 * brokers & OTC eligible, amber = ECP only, hollow = neither; the active row shows the eligibility sentences.
 */
(function () {
  'use strict';
  var T = window.T;
  function navOf(b) {
    var nav = b.nav;
    if (nav && typeof nav === 'object') { return { text: T.money(nav, 'value'), quality: nav.quality }; }
    return { text: T.money(b, 'nav'), quality: b.nav_quality || null };
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
        title: (b.name || b.book_code) + ' . ' + (b.profile || '') + (b.lifetime ? ' . lifetime ' + b.lifetime.lifetime_id : '') + '\n' + (e.pb_otc_reason || '') + '\n' + (e.private_reason || '') + '\nAlt+B cycles',
        onclick: function () { T.book.set(b.book_code); } },
        T.h('span', { class: 'bs-dot ' + dot, title: e.pb_otc ? 'prime brokers & OTC eligible' : (e.ecp ? 'ECP floor reached; below the PB/OTC threshold' : 'below the ECP floor') }),
        T.h('span', { class: 'bs-name' }, b.name || b.book_code),
        T.h('span', { class: 'bs-nav', title: 'NAV of the open lifetime' + (nav.quality ? ' (' + nav.quality + ')' : '') }, nav.text));
      list.appendChild(item);
    });
    root.appendChild(list);
    if (active) {
      var e = active.eligibility || {};
      var hint = T.h('div', { class: 'bs-hint' });
      hint.appendChild(T.h('div', null, (active.lifetime ? 'lifetime ' + active.lifetime.lifetime_id + ' . ' : '') + 'Alt+B switches'));
      if (!e.pb_otc && e.pb_otc_reason) { hint.appendChild(T.h('div', { title: T.conv('ELIGIBILITY') }, e.pb_otc_reason)); }
      if (!e.private_markets && e.private_reason) { hint.appendChild(T.h('div', { title: T.conv('QP') }, e.private_reason)); }
      root.appendChild(hint);
    }
  }
  function init() {
    T.state.on('books', render); T.state.on('book', render); T.state.on('server_ok', render);
    render();
  }
  T.components.bookswitcher = { init: init, render: render };
})();
