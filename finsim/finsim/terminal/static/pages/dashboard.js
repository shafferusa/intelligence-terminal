/* Dashboard (owner directive 2026-10-27 section 16; FINSIM1_PLAN 15.7 item 1 - the 1.0.5 Dashboard).
 * The landing page: #/dashboard (the default route and the unknown-route fallback; the removed news page's old address lands
 * here too).  It shows the selected book's "Requires your attention" tiles from GET /api/attention/summary?book= (the book's
 * items plus the global ones - the same set as the attention strip under the header): every number is printed as the API
 * sends it - this file computes nothing (the STRIP table below is display only: label, text, link).  Each tile leads to what
 * it counts: working orders / pending settlements -> Activity filtered by type, collateral calls / recalls -> Risk financing,
 * compliance notices -> Risk compliance, integrity -> Accounting > Integrity, provider pauses -> Settings > Data Status, open
 * attention items -> the expanded strip.  Below the tiles, ONE line naming what the next release adds; then the newest open
 * attention items themselves.
 * It calls no /api/dashboard: that route arrives with the full Dashboard (NAV, P&L, exposure, cash, margin, accounts).
 * Refreshed every 15 s while shown; a failed refresh keeps the last answer and says so in the page-status line.  ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  function n(v) { return v == null ? 'n/a' : String(v); }
  function plabel(p) { return T.providerLabel ? T.providerLabel(p) : String(p || '').replace(/_/g, ' '); }
  function integrityOf(att) {
    var i = att.integrity;
    if (i == null) { return { ok: null, failing: [] }; }
    if (typeof i === 'string') { return { ok: i === 'ok', failing: i === 'ok' ? [] : [i] }; }
    if (typeof i === 'boolean') { return { ok: i, failing: [] }; }
    return i;
  }
  function holdOf(att) {
    var h = att.hold;
    if (h == null) { return { held: false, lifetimes: [] }; }
    if (typeof h === 'boolean') { return { held: h, lifetimes: [] }; }
    return h;
  }
  function expandStrip() {
    if (T.attention && typeof T.attention.expand === 'function') { T.attention.expand(); return; }
    var el = T.q('#attention'); if (el && el.scrollIntoView) { el.scrollIntoView({ block: 'start' }); }
  }
  /* the tiles: key = the summary field printed; text = how it is shown; cls = tone; href / act = where it leads; title = the hover */
  var STRIP = [
    { key: 'integrity', label: 'integrity', href: '#/accounting/integrity', title: 'the last integrity check of this book - opens Accounting > Integrity',
      text: function (att) { var i = integrityOf(att); return i.ok === true ? 'ok' : (i.ok === false ? 'FAILED ' + (i.failing || []).join(' ') : 'not checked'); },
      sub: function (att) { var i = integrityOf(att); return i.checked_at ? 'checked ' + T.fmt.hm(i.checked_at) : (i.ok == null ? 'no report recorded yet' : ''); },
      cls: function (att) { var i = integrityOf(att); return i.ok === true ? 'pos' : (i.ok === false ? 'neg' : ''); } },
    { key: 'hold', label: 'integrity hold', href: '#/accounting/integrity', title: 'writes to a held lifetime are refused until the check passes - opens Accounting > Integrity',
      text: function (att) { var h = holdOf(att); return h.held ? 'HELD ' + (h.lifetimes || []).join(' ') : 'none'; },
      cls: function (att) { return holdOf(att).held ? 'neg' : ''; } },
    { key: 'open', label: 'open attention items', act: expandStrip, title: 'opens the whole attention list under the header',
      text: function (att) { var o = att.open || {}; return 'E ' + n(o.ERROR) + ' . W ' + n(o.WARN) + ' . I ' + n(o.INFO); },
      sub: function (att) { return att.attention_open != null ? n(att.attention_open) + ' open - click to list them' : ''; },
      cls: function (att) { var o = att.open || {}; return Number(o.ERROR) > 0 ? 'neg' : ''; } },
    { key: 'warnings', label: 'risk warnings', href: '#/risk', title: 'opens Risk', text: function (att) { return n(att.warnings); } },
    { key: 'compliance', label: 'compliance notices', href: '#/risk?section=compliance', title: 'opens the compliance section of Risk',
      text: function (att) { return n(att.compliance); }, sub: function () { return 'info / warning / breach only'; } },
    { key: 'collateral_calls_today', label: 'collateral calls today', href: '#/risk?section=financing', title: 'opens the financing section of Risk',
      text: function (att) { return n(att.collateral_calls_today); },
      sub: function (att) { return att.swap_vm_payments_today != null ? 'incl. swap VM payments ' + n(att.swap_vm_payments_today) : ''; } },
    { key: 'recalls', label: 'recall notices', href: '#/risk?section=financing', title: 'opens the financing section of Risk', text: function (att) { return n(att.recalls); } },
    { key: 'working_orders', label: 'working orders', href: '#/activity?type=orders', title: 'opens Activity filtered to orders', text: function (att) { return n(att.working_orders); } },
    { key: 'pending_settlements', label: 'pending settlements', href: '#/activity?type=settlements', title: 'opens Activity filtered to settlements',
      text: function (att) { return n(att.pending_settlements); } },
    { key: 'providers', label: 'provider pauses', href: '#/settings/data', title: 'market-data providers that are down - opens Settings > Data Status',
      text: function (att) { var p = att.providers || {}; return p.paused != null ? n(p.paused) : n((p.down || []).length); },
      sub: function (att) { var p = att.providers || {}; return (p.down || []).map(plabel).join(', ') + ((p.refused_ua || []).length ? ' . user-agent refused: ' + p.refused_ua.map(plabel).join(', ') : ''); },
      cls: function (att) { var p = att.providers || {}; return (p.down || []).length ? 'neg' : ''; } }
  ];
  function strip(parent, att) {
    var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'Requires your attention', T.h('small', null, 'this book plus data feeds; every number from accounting / attention / risk / compliance')));
    var tiles = T.tiles(STRIP.map(function (t) {
      return { k: t.label, v: t.text(att), s: t.sub ? t.sub(att) : null, cls: t.cls ? t.cls(att) : '', href: t.href || null, title: t.title || '' };
    }));
    STRIP.forEach(function (t, i) {
      if (!t.act) { return; }
      var el = tiles.children[i];
      if (el) { el.classList.add('click'); el.addEventListener('click', t.act); }
    });
    p.appendChild(tiles);
    var i = integrityOf(att);
    if (i.ok === false && (i.problems || []).length) {
      var probs = T.h('div', { class: 'hint' });
      (i.problems || []).forEach(function (pr) { probs.appendChild(T.h('div', null, (pr.id || '') + ' ' + (pr.scope || '') + ': ' + (pr.detail || pr.message || ''))); });
      p.appendChild(probs);
    }
    (att.notes || []).forEach(function (note) { p.appendChild(T.h('div', { class: 'hint' }, note)); });
    parent.appendChild(p);
  }
  /* the newest open attention items of the book (the summary's items; the whole list is the strip under the header) */
  function openItems(parent, att) {
    var items = att.items || [];
    var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'Open attention items', T.h('small', null, items.length ? 'newest first' : '')));
    if (items.length) {
      var list = T.h('div');
      items.forEach(function (r) {
        list.appendChild(T.h('div', { class: 'att ' + (r.severity || 'INFO') },
          T.h('span', { class: 'sev' }, r.severity || 'INFO'), T.h('span', { class: 'src' }, plabel(r.source || '')), T.h('span', { class: 'msg', title: r.message || '' }, r.message || T.codeWords(r.code || '')),
          r.account_id ? T.h('a', { href: T.router.account(r.account_id, 'positions'), class: 'src', title: 'opens its positions' }, T.accountName(r.account_id)) : null,
          T.h('span', { class: 'when' }, r.ts ? T.fmt.time(r.ts) : '')));
      });
      p.appendChild(list);
      if (att.attention_open != null && Number(att.attention_open) > items.length) {
        var more = T.h('a', { href: '#', onclick: function (e) { e.preventDefault(); expandStrip(); } }, 'all ' + n(att.attention_open) + ' open items: the attention list under the header');
        p.appendChild(T.h('div', { class: 'hint' }, 'the newest ' + items.length + ' are listed - ', more));
      }
    } else {
      p.appendChild(T.h('div', { class: 'empty' }, 'no open attention items for this book'));
    }
    parent.appendChild(p);
  }
  /* the one line naming what the next release adds (15.7 item 1) */
  function nextRelease() {
    return T.nib('NAV, P&L, exposure, cash, margin and account tiles',
      'the next release adds them here, with the Pending Trades, Completed Trades, Accounts, Cash & Collateral, Derivatives and History pages and the new sidebar');
  }
  function render(el) {
    var book = T.book.current();
    T.pageHead(el, 'Dashboard', book ? (book.name || book.book_code) : '');
    var body = T.h('div'); el.appendChild(body);
    body.appendChild(T.h('div', { class: 'empty' }, 'loading ...'));
    var loaded = false;
    function load() {
      return T.api.get('/api/attention/summary').then(function (att) {
        loaded = true;
        T.clear(body);
        var a = att && typeof att === 'object' ? att : {};
        strip(body, a);
        body.appendChild(nextRelease());
        openItems(body, a);
      }, function (e) {
        if (!loaded) {                                       /* nothing to keep: say why here; later failures keep the last answer */
          T.clear(body);
          body.appendChild(T.h('div', { class: 'error' }, 'the attention summary could not be loaded: ' + T.api.describe(e)));
          body.appendChild(nextRelease());
        }
        throw e;                                             /* the page-status line says it and offers Retry */
      });
    }
    T.poll.page('dashboard', 15000, load);
    T.pages.dashboard.unmount = function () { T.pages.dashboard.unmount = null; };
  }
  T.pages.dashboard = { render: render, unmount: null, STRIP: STRIP };
})();
