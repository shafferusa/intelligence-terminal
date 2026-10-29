/* Attention strip (TERMINAL_DESIGN.md s17.1 / s17.2, s16 provider errors; WP-12).  Under the header: red (ERROR), amber
 * (WARN), blue (INFO).  Rows = the open items of GET /api/attention?book=<selected book> (the poller in core.js sends the
 * book) plus the states carried by GET /api/health:
 * integrity failure / INTEGRITY_HOLD (ERROR), a provider with ok=false ("Yahoo unreachable since 14:02 - quotes are
 * stale; executions paused", WARN), a stalled / dead worker (WARN; a worker busy inside a long tick is not stalled: health
 * counts its progress, V104R2-BW-10), failing lifecycle jobs (ERROR).  Persistent items - integrity, INTEGRITY_HOLD,
 * FUNDING_SHORTFALL, provider pauses - have
 * no Resolve button (the API answers 409 PERSISTENT anyway); the others resolve through POST /api/attention/{id}/resolve.
 * Provider names are the trader-facing ones (T.providerLabel: 'Treasury.gov', not treasury).  Four rows show; '+ N more'
 * (or T.attention.expand(), e.g. the Dashboard's 'open attention items' tile) opens the whole list, scrolled into view.
 */
(function () {
  'use strict';
  var T = window.T;
  var PERSISTENT_CODES = { INTEGRITY: 1, INTEGRITY_HOLD: 1, FUNDING_SHORTFALL: 1 };
  function label(p) { return T.providerLabel ? T.providerLabel(p) : p; }
  /* an account by name; the lifetime id is added when the account is not one of the selected book's */
  function acctLabel(id) {
    var name = T.accountName ? T.accountName(id) : id;
    return T.accountRow && T.accountRow(id) ? name : name + ' (' + String(id).split(':')[0] + ')';
  }
  var expanded = false;
  function isPersistent(row) {
    return !!(row.persistent || PERSISTENT_CODES[row.code] || row.source === 'integrity' || row.source === 'provider' || String(row.code || '').indexOf('PROVIDER') === 0);
  }
  function fromHealth(h) {
    var rows = [];
    if (!h) { return rows; }
    var i = h.integrity || {};
    if (i.ok === false || i.hold) {
      var probs = (i.problems || []).map(function (p) { return typeof p === 'string' ? p : (p.check || p.code || '') + (p.message ? ': ' + p.message : '') + (p.lifetime_id ? ' [' + p.lifetime_id + ']' : ''); });
      rows.push({ severity: 'ERROR', source: 'integrity', code: i.hold ? 'INTEGRITY_HOLD' : 'INTEGRITY', message: (i.hold ? 'INTEGRITY_HOLD - writes to the held lifetime are refused until the check passes. ' : 'Integrity check failed. ') + (probs.join('; ') || 'see Accounting -> Integrity'), ts: i.checked_at, persistent: true });
    }
    var ps = h.providers || {};
    Object.keys(ps).forEach(function (p) {
      var s = ps[p] || {};
      if (s.ok === false) {
        var exec = (p === 'yahoo' || p === 'cboe') ? ' - quotes are stale; executions paused' : (p === 'treasury' || p === 'fred' ? ' - computed marks use the last observation' : '');
        rows.push({ severity: 'WARN', source: 'provider', code: 'PROVIDER_' + p.toUpperCase(), message: label(p) + ' unreachable since ' + T.fmt.hm(s.last_err_ts || s.last_ok_ts) + exec + (s.last_error ? ' (' + s.last_error + ')' : '') + (s.down_until_ts ? '; next try ' + T.fmt.hm(s.down_until_ts) : '') + (s.refused_ua ? '; the host refused our User-Agent' : ''), ts: s.last_err_ts, persistent: true });
      }
    });
    var ws = h.workers || {};
    Object.keys(ws).forEach(function (w) {
      var s = ws[w] || {};
      if (s.alive === false) { rows.push({ severity: 'ERROR', source: 'supervisor', code: 'WORKER_DEAD', message: w + ' worker died' + (s.last_error ? ': ' + s.last_error : '') + ' - the supervisor restarts it', ts: s.last_tick_ts, persistent: true }); }
      else if (s.stalled) { rows.push({ severity: 'WARN', source: 'supervisor', code: 'WORKER_STALLED', message: w + ' worker stalled (last tick ' + T.fmt.hm(s.last_tick_ts) + ')', ts: s.last_tick_ts, persistent: true }); }
    });
    var lf = h.lifecycle || {};
    (lf.failing || []).forEach(function (j) { rows.push({ severity: 'ERROR', source: 'lifecycle', code: 'JOB_FAILING', message: 'job ' + (j.name || j.job || j) + ' failing' + (j.last_error ? ': ' + j.last_error : '') + (j.failures ? ' (' + j.failures + 'x)' : ''), ts: j.ts, persistent: true }); });
    return rows;
  }
  function render() {
    var el = T.q('#attention'); if (!el) { return; }
    var rows = fromHealth(T.state.health).concat((T.state.attention || []).filter(function (r) { return !r.resolved_ts; }));
    var order = { ERROR: 0, WARN: 1, INFO: 2 };
    rows.sort(function (a, b) { return (order[a.severity] - order[b.severity]) || ((b.ts || 0) - (a.ts || 0)); });
    T.clear(el);
    el.classList.toggle('expanded', expanded);
    var shown = expanded ? rows : rows.slice(0, 4);
    shown.forEach(function (r) {
      var persistent = isPersistent(r);
      var row = T.h('div', { class: 'att ' + (r.severity || 'INFO'), title: (r.code || '') + (r.details_json ? '\n' + r.details_json : '') + (r.ref_type ? '\n' + r.ref_type + ' ' + (r.ref_id || '') : '') },
        T.h('span', { class: 'sev' }, r.severity || 'INFO'),
        T.h('span', { class: 'src' }, r.source || ''),
        T.h('span', { class: 'msg' }, r.message || r.code || ''),
        r.account_id ? T.h('a', { href: T.router.account(r.account_id, 'positions'), class: 'src', title: r.account_id + ' - opens its positions (the book that holds it is selected)' }, acctLabel(r.account_id)) : null,
        T.h('span', { class: 'when' }, r.ts ? T.fmt.time(r.ts) : ''));
      if (persistent) { row.appendChild(T.h('span', { class: 'persist', title: 'cannot be resolved while it persists' }, 'PERSISTS')); }
      else if (r.id != null) {
        row.appendChild(T.h('button', { class: 'btn small', title: 'mark as seen', onclick: function (ev) {
          ev.target.disabled = true;
          T.api.post('/api/attention/' + encodeURIComponent(r.id) + '/resolve', {}).then(function () { T.poll.kick('attention'); }, function (e) { ev.target.disabled = false; T.api.fail(e, 'resolve'); });
        } }, 'Resolve'));
      }
      el.appendChild(row);
    });
    if (rows.length > 4) {
      el.appendChild(T.h('div', { class: 'att INFO' }, T.h('a', { href: '#', onclick: function (e) { e.preventDefault(); expanded = !expanded; render(); } }, expanded ? 'show fewer' : '+ ' + (rows.length - 4) + ' more')));
    }
  }
  /* open the whole list (SHELL-27: the Dashboard's 'open attention items' tile lands here) */
  function expand() { expanded = true; render(); var el = T.q('#attention'); if (el) { try { el.scrollTop = 0; el.scrollIntoView({ block: 'nearest' }); } catch (e) { /* ignore */ } } }
  function collapse() { expanded = false; render(); }
  function init() { T.state.on('health', render); T.state.on('attention', render); render(); }
  T.components.attention = { init: init, render: render, fromHealth: fromHealth, isPersistent: isPersistent, expand: expand, collapse: collapse };
  T.attention = { expand: expand, collapse: collapse, render: render };
})();
