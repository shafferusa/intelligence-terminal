/* Daily News (TERMINAL_DESIGN.md s17.9, s17.1 #/news; directive s3.1; iron rule 2; WP-22; Build 2 P01 2026-10-21).
 * Top strip "Requires your attention" = GET /api/news .attention (news.attention_summary, the selected book's items plus the
 * global ones - the same set as the attention strip): every number is printed as the API sends it - this file computes
 * nothing (the STRIP table below is display only: label, text, link).  Each tile leads to what it counts (SHELL-27): working
 * orders / pending settlements -> Activity filtered by type, collateral calls / recalls -> Risk financing, compliance notices
 * -> Risk compliance, open attention items -> the expanded strip.
 * Then three columns of real headlines (Yahoo news search for the market and the names held / working, the Federal Reserve
 * press feed, the newspaper's editions from its published index), each item with publisher and time, newest first, links
 * open in a new tab (target _blank, rel noopener noreferrer); nothing is interpreted, scored or summarised.  When the newest
 * edition is older than yesterday the column lists the latest editions with their dates and says "newest edition <date>".
 * Footer: per source "last fetched hh:mm", "never fetched" or the last error, the injection-flag count (shown verbatim,
 * ignored) and the sentence NEWS.  ASCII only.
 */
(function () {
  'use strict';
  var T = window.T;
  var SOURCES = [
    { src: 'yahoo', provider: 'yahoo_news', title: 'Markets & your names', sub: 'Yahoo Finance news search' },
    { src: 'fed', provider: 'fed', title: 'Federal Reserve', sub: 'press releases feed' },
    { src: 'newspaper', provider: 'newspaper', title: "Logan's Daily Newspaper", sub: "today's editions" }
  ];
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
  /* the strip: key = the attention field printed; text = how it is shown; cls = tone; href / act = where it leads */
  var STRIP = [
    { key: 'integrity', label: 'integrity', href: '#/accounting/integrity',
      text: function (att) { var i = integrityOf(att); return i.ok === true ? 'ok' : (i.ok === false ? 'FAILED ' + (i.failing || []).join(' ') : 'not checked'); },
      sub: function (att) { var i = integrityOf(att); return i.checked_at ? 'checked ' + T.fmt.hm(i.checked_at) : (i.ok == null ? 'no report recorded yet' : ''); },
      cls: function (att) { var i = integrityOf(att); return i.ok === true ? 'pos' : (i.ok === false ? 'neg' : ''); } },
    { key: 'hold', label: 'integrity hold', href: '#/accounting/integrity',
      text: function (att) { var h = holdOf(att); return h.held ? 'HELD ' + (h.lifetimes || []).join(' ') : 'none'; },
      cls: function (att) { return holdOf(att).held ? 'neg' : ''; } },
    { key: 'open', label: 'open attention items', act: expandStrip,
      text: function (att) { var o = att.open || {}; return 'E ' + n(o.ERROR) + ' . W ' + n(o.WARN) + ' . I ' + n(o.INFO); },
      sub: function (att) { return att.attention_open != null ? n(att.attention_open) + ' open - click to list them' : ''; },
      cls: function (att) { var o = att.open || {}; return Number(o.ERROR) > 0 ? 'neg' : ''; } },
    { key: 'warnings', label: 'risk warnings', href: '#/risk', text: function (att) { return n(att.warnings); } },
    { key: 'compliance', label: 'compliance notices', href: '#/risk?section=compliance', text: function (att) { return n(att.compliance); },
      sub: function () { return 'info / warning / breach only'; } },
    { key: 'collateral_calls_today', label: 'collateral calls today', href: '#/risk?section=financing', text: function (att) { return n(att.collateral_calls_today); },
      sub: function (att) { return att.swap_vm_payments_today != null ? 'incl. swap VM payments ' + n(att.swap_vm_payments_today) : ''; } },
    { key: 'recalls', label: 'recall notices', href: '#/risk?section=financing', text: function (att) { return n(att.recalls); } },
    { key: 'working_orders', label: 'working orders', href: '#/activity?type=orders', text: function (att) { return n(att.working_orders); } },
    { key: 'pending_settlements', label: 'pending settlements', href: '#/activity?type=settlements', text: function (att) { return n(att.pending_settlements); } },
    { key: 'providers', label: 'provider pauses', href: '#/settings/data',
      text: function (att) { var p = att.providers || {}; return p.paused != null ? n(p.paused) : n((p.down || []).length); },
      sub: function (att) { var p = att.providers || {}; return (p.down || []).map(plabel).join(', ') + ((p.refused_ua || []).length ? ' . user-agent refused: ' + p.refused_ua.map(plabel).join(', ') : ''); },
      cls: function (att) { var p = att.providers || {}; return (p.down || []).length ? 'neg' : ''; } }
  ];
  function strip(parent, att) {
    var p = T.h('div', { class: 'panel' }, T.h('h2', null, 'Requires your attention', T.h('small', null, 'this book plus data feeds; every number from accounting / attention / risk / compliance')));
    var tiles = T.tiles(STRIP.map(function (t) {
      return { k: t.label, v: t.text(att), s: t.sub ? t.sub(att) : null, cls: t.cls ? t.cls(att) : '', href: t.href || null, title: 'attention.' + t.key };
    }));
    STRIP.forEach(function (t) {
      if (!t.act) { return; }
      var el = tiles.querySelector('[title="attention.' + t.key + '"]');
      if (el) { el.classList.add('click'); el.addEventListener('click', t.act); }
    });
    p.appendChild(tiles);
    var items = att.items || [];
    if (items.length) {
      var list = T.h('div');
      items.forEach(function (r) {
        list.appendChild(T.h('div', { class: 'att ' + (r.severity || 'INFO'), title: r.code || '' },
          T.h('span', { class: 'sev' }, r.severity || 'INFO'), T.h('span', { class: 'src' }, plabel(r.source || '')), T.h('span', { class: 'msg' }, r.message || r.code || ''),
          r.account_id ? T.h('a', { href: T.router.account(r.account_id, 'positions'), class: 'src', title: r.account_id }, T.accountName(r.account_id)) : null,
          T.h('span', { class: 'when' }, r.ts ? T.fmt.time(r.ts) : '')));
      });
      p.appendChild(list);
    }
    var i = integrityOf(att);
    if (i.ok === false && (i.problems || []).length) {
      var probs = T.h('div', { class: 'hint' });
      (i.problems || []).forEach(function (pr) { probs.appendChild(T.h('div', null, (pr.id || '') + ' ' + (pr.scope || '') + ': ' + (pr.detail || pr.message || ''))); });
      p.appendChild(probs);
    }
    (att.notes || []).forEach(function (note) { p.appendChild(T.h('div', { class: 'hint' }, note)); });
    parent.appendChild(p);
  }
  function itemRow(it) {
    var row = T.h('div', { class: 'row', style: 'padding:5px 0;border-bottom:1px solid var(--line)' });
    row.appendChild(T.h('div', null, T.h('a', { href: it.link, target: '_blank', rel: 'noopener noreferrer' }, it.title || ''),
      it.flagged ? T.h('span', { class: 'badge amber', title: 'instruction-like text in a fetched headline; shown verbatim, ignored (iron rule 2)', style: 'margin-left:6px' }, 'flagged') : null));
    var meta = (it.publisher || '') + (it.time ? ' . ' + T.fmt.dt(it.time) : '');
    var chips = (it.tickers || []).map(function (s) { return T.h('span', { class: 'chip', style: 'margin-left:4px' }, s); });
    row.appendChild(T.h('div', { class: 'muted', style: 'font-size:11px' }, meta, chips.length ? chips : null));
    return row;
  }
  function sourceLine(src, st, fetched) {
    st = st || {};
    if (st.ok === false && st.last_error) { return plabel(src.provider) + ': ' + st.last_error + (fetched ? ' (last good ' + T.fmt.hm(fetched) + ')' : ''); }
    if (fetched) { return 'last fetched ' + T.fmt.hm(fetched); }
    return 'never fetched';
  }
  function column(src, items, editions, fetched, st, paper) {
    var sub = src.sub;
    var stale = src.src === 'newspaper' && paper && paper.stale;
    if (src.src === 'newspaper') { sub = stale ? 'newest edition ' + paper.newest : "today's editions" + (paper && paper.origin === 'local' ? ' (local copy)' : ''); }
    var p = T.h('div', { class: 'panel' }, T.h('h2', null, src.title, T.h('small', null, sub)));
    if (src.src === 'newspaper' && editions && editions.length) {
      if (stale) { p.appendChild(T.h('div', { class: 'hint' }, 'No edition today or yesterday: the newest edition is ' + paper.newest + '. The latest editions are listed with their dates.')); }
      editions.forEach(function (e) {
        p.appendChild(T.h('div', { class: 'row', style: 'padding:4px 0' },
          T.h('b', null, (e.label || e.slot || '') + ' '), T.h('span', { class: 'muted' }, (e.date || '') + ' '), T.h('span', null, e.title || ''), ' ',
          T.h('a', { href: e.link || (T.state.newsPaperUrl || '') + (e.path || ''), target: '_blank', rel: 'noopener noreferrer' }, 'read the edition'),
          e.reading_minutes != null ? T.h('span', { class: 'muted' }, ' (' + e.reading_minutes + ' min)') : null,
          e.summary ? T.h('div', { class: 'muted', style: 'font-size:11px' }, e.summary) : null));
      });
    }
    var shown = src.src === 'newspaper' && editions && editions.length;      /* the editions are listed above: their own items would repeat them */
    var mine = items.filter(function (it) { return it.source === src.src && !(shown && it.category === 'edition'); });
    if (!mine.length && !shown) {
      p.appendChild(T.h('div', { class: 'empty' }, (st && st.ok === false) ? 'no items: ' + sourceLine(src, st, fetched) : (fetched ? 'no items in the last fetch' : 'not fetched yet')));
    }
    mine.forEach(function (it) { p.appendChild(itemRow(it)); });
    p.appendChild(T.h('div', { class: 'hint' }, sourceLine(src, st, fetched)));
    return p;
  }
  function render(el) {
    T.pageHead(el, 'Daily News', 'real headlines; nothing interpreted');
    var body = T.h('div'); el.appendChild(body);
    function load() {
      return T.api.get('/api/news', { limit: 60 }).then(function (r) {
        T.clear(body);
        var att = r.attention || {};
        strip(body, att);
        var fetched = r.fetched || {};
        var states = (r.health && r.health.sources) || {};
        var cols = T.h('div', { class: 'grid3' });
        SOURCES.forEach(function (s) { cols.appendChild(column(s, r.items || [], r.editions || [], fetched[s.src], states[s.src], r.paper || (r.health && r.health.paper))); });
        body.appendChild(cols);
        var flags = (r.health && r.health.injection_flags != null) ? r.health.injection_flags : (att.news && att.news.injection_flags != null ? att.news.injection_flags : null);
        var foot = T.h('div', { class: 'hint' }, SOURCES.map(function (s) { return plabel(s.provider) + ': ' + sourceLine(s, states[s.src], fetched[s.src]); }).join(' . ') +
          (flags != null ? ' . instruction-like text flagged in ' + n(flags) + ' headline(s), shown verbatim and ignored' : ''));
        body.appendChild(foot);
        body.appendChild(T.convNote(['NEWS']));
      }, function (e) { T.clear(body); T.errorBox(body, e, 'news'); });
    }
    T.poll.page('news', 60000, load);
    T.pages.news.unmount = function () { T.pages.news.unmount = null; };
  }
  T.pages.news = { render: render, unmount: null, STRIP: STRIP };
})();
