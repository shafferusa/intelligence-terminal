/* Freshness on every price (TERMINAL_DESIGN.md s17.3, s8.2; WP-12).
 *
 *   T.px(rec, opts)      -> <span class="pxw q-delayed"><span class="px">339.50</span><span class="fr"><dot>&lt;=15m . 15:46:50</span></span>
 *   T.pxHtml(rec, opts)  -> the same as an HTML string (for grid cells)
 *   T.qchip(quality)     -> a small chip for a figure that is not a single price (NAV quality, chain quality)
 *
 * `rec` is any of the API's price-carrying shapes - a MarkView {mark, quality, source, method, quote_time, fetched_at,
 * delay_label, inputs, executable, note}, a QuoteRecord {last, bid, ask, mid, quality, source, provider, quote_time,
 * fetched_at, delay_s, delay_label, age_s, error}, a mark-dependent field {value, quality, as_of, source} or a chain leg
 * (opts.field = 'bid' | 'ask' picks the number).  The value shown is never computed here; the quality class and the
 * label come from the record's own `quality` and times.  A record without a quality is shown as q-stale with the label
 * NO STATUS, and null is q-unavailable with a dash: NOTHING SHOWS A BARE NUMBER.
 *
 * States (CSS in terminal.css): q-live (reserved), q-delayed (green hollow dot + delay), q-close (grey, CLOSE hh:mm),
 * q-computed (purple MODEL, italic), q-private (blue LAST ROUND yyyy-mm-dd, never dimmed), q-stale (amber STALE age,
 * struck through), q-unavailable (red dash N/A).  Tooltip: source, provider time, fetched time, delay label, the rule
 * that produced the state, the price basis and, for computed marks, the inputs with their times.
 * Times: only a NUMBER (epoch seconds) is formatted as a time; a date string (a curve input's as_of '2026-10-20') is
 * printed as it is (OA-04: it threw RangeError 'Invalid time value' and the bond page head never painted).
 * An archived mark (a closed lifetime's stored snapshot: method 'snapshot', 'frozen at reset') reads 'FROZEN hh:mm' (BM-31).
 * Units (OA-15): opts.unit / opts.scale / opts.ccy (or the record's quote_unit / price_scale / currency) show a listing
 * quoted in a minor unit as 'GBp 2,700.00' with the major-currency value on hover ('GBP 27.00 = 2,700.00 GBp x 0.01', a
 * display conversion of the quoted price); opts.showMajor adds it inline.
 */
(function () {
  'use strict';
  var T = window.T;
  var RULES = {
    live: 'a real-time print (reserved; no provider in this build produces it)',
    delayed: 'within the provider\'s stated delay during an open session',
    close: 'session closed - the last valid mark with its time; executions are blocked by the session, valuation is fine',
    computed: 'a model from observed inputs (listed) - never executable',
    private: 'the last reported round - valuation only; never aged into stale, never executable',
    stale: 'older than its stated delay + 5 min, or nothing fetched for 5 min, or a failing provider - executions blocked; valuation keeps this mark',
    unavailable: 'no value from any provider - the position\'s market value is reported as unavailable (NAV partial)',
    unknown: 'the record carries no quality field - treated as not executable'
  };
  function num(x) { if (x == null || x === '') { return null; } var n = Number(x); return isFinite(n) ? x : null; }
  /* epoch seconds of a time field, or null (a date string is not a time: printed as it is) */
  function epoch(x) { return T.epochOf ? T.epochOf(x) : (typeof x === 'number' && isFinite(x) && x ? x : null); }
  function when(x) { var e = epoch(x); return e != null ? T.fmt.dt(e) : String(x); }
  function isFrozen(rec) { return !!(rec && (rec.method === 'snapshot' || /^frozen/i.test(String(rec.delay_label || '')))); }
  function pick(rec, field) {
    if (!rec || typeof rec !== 'object') { return null; }
    if (field) { return num(rec[field]); }
    var keys = ['mark', 'value', 'last', 'mid', 'price', 'close'];
    for (var i = 0; i < keys.length; i++) { var v = num(rec[keys[i]]); if (v != null) { return v; } }
    return null;
  }
  function delayShort(rec) {
    var s = rec.delay_s;
    if (typeof s === 'number') { return s <= 0 ? 'RT' : '<=' + Math.round(s / 60) + 'm'; }
    var lbl = String(rec.delay_label || '');
    var m = /(\d+)\s*min/.exec(lbl);
    if (m) { return '<=' + m[1] + 'm'; }
    if (/real/i.test(lbl)) { return 'RT'; }
    return lbl ? lbl.slice(0, 12) : '';
  }
  function roundDate(rec) {
    var m = /(\d{4}-\d{2}-\d{2})/.exec(String(rec.note || rec.as_of_date || rec.round_date || ''));
    if (m) { return m[1]; }
    return rec.quote_time ? T.fmt.date(rec.quote_time) : '';
  }
  /* the unit of a quoted price: {unit, scale, ccy} when the listing is quoted in a minor unit (price_scale != 1) */
  function unitOf(rec, opts) {
    var unit = opts.unit || (rec && rec.quote_unit) || null;
    var scale = opts.scale != null ? opts.scale : (rec && rec.price_scale != null ? rec.price_scale : null);
    if (!unit || scale == null || !isFinite(Number(scale)) || Number(scale) === 1) { return null; }
    return { unit: String(unit), scale: Number(scale), ccy: opts.ccy || (rec && rec.currency) || '' };
  }
  /* normalise any shape into {value, quality, label, title, cls} */
  T.freshness = function (rec, opts) {
    opts = opts || {};
    var bf = !opts.field && (opts.basis === 'ask' || opts.basis === 'bid') && rec && num(rec[opts.basis]) != null ? opts.basis : null;
    var value = pick(rec, opts.field || bf);
    var q = rec && rec.quality ? String(rec.quality) : null;
    if (value == null) { q = 'unavailable'; }
    else if (!q) { q = 'unknown'; }
    var qt = rec ? (epoch(rec.quote_time) != null ? epoch(rec.quote_time) : epoch(rec.as_of)) : null;
    var age = rec && typeof rec.age_s === 'number' ? rec.age_s : (qt ? T.clock.now() - qt : null);
    var label;
    switch (q) {
      case 'live': label = 'LIVE ' + T.fmt.time(qt); break;
      case 'delayed': label = delayShort(rec) + (qt ? ' . ' + T.fmt.time(qt) : ''); break;
      case 'close': label = 'CLOSE ' + (qt ? T.fmt.hm(qt) : ''); break;
      case 'computed': label = 'MODEL' + (qt ? ' ' + T.fmt.hm(qt) : ''); break;
      case 'private': label = 'LAST ROUND ' + roundDate(rec); break;
      case 'stale': label = 'STALE ' + (age != null ? T.fmt.age(age) : ''); break;
      case 'unavailable': label = 'N/A'; break;
      default: label = 'NO STATUS';
    }
    var frozen = q !== 'unavailable' && isFrozen(rec);
    if (frozen) { label = 'FROZEN' + (qt ? ' ' + T.fmt.hm(qt) : (rec.as_of ? ' ' + String(rec.as_of).slice(0, 10) : '')); }
    var lines = [];
    if (rec) {
      lines.push((rec.source || rec.provider || 'source unknown') + (rec.method ? ' . ' + rec.method : ''));
      if (qt) { lines.push('provider time ' + T.fmt.dt(qt) + ':' + T.fmt.time(qt).slice(6) + ' ET' + (age != null ? ' (' + T.fmt.age(age) + ' ago)' : '')); }
      if (epoch(rec.fetched_at) != null) { lines.push('fetched ' + T.fmt.dt(rec.fetched_at) + ' ET'); }
      if (rec.delay_label) { lines.push(rec.delay_label); }
      if (rec.error) { lines.push('provider error: ' + rec.error); }
    }
    lines.push('rule: ' + (RULES[q] || RULES.unknown));
    var basis = opts.basis || (rec && rec.price_basis);
    if (basis) { lines.push('basis: ' + basis); }
    if (rec && rec.inputs && typeof rec.inputs === 'object') {
      Object.keys(rec.inputs).forEach(function (k) {
        var v = rec.inputs[k];
        if (v && typeof v === 'object') { lines.push('input ' + k + ' = ' + (v.value != null ? v.value : JSON.stringify(v)) + (v.as_of ? ' (as of ' + when(v.as_of) + ')' : '') + (v.quality ? ' ' + v.quality : '')); }
        else { lines.push('input ' + k + ' = ' + v); }
      });
    }
    if (rec && rec.note) { lines.push(rec.note); }
    if (rec && rec.executable === false && q !== 'unavailable') { lines.push('not executable'); }
    if (frozen) { lines.push('frozen: the last mark of a closed lifetime, kept read-only'); }
    var u = value != null ? unitOf(rec, opts) : null;
    var major = null;
    if (u) {
      major = (u.ccy ? u.ccy + ' ' : '') + T.fmt.money((Number(value) * u.scale).toFixed(4), 2);
      lines.unshift(major + ' = ' + T.fmt.px(value) + ' ' + u.unit + ' x ' + u.scale + ' (the listing is quoted in ' + u.unit + ')');
    }
    var cls = frozen ? 'q-close' : 'q-' + (q === 'unknown' ? 'stale' : q);
    return { value: value, quality: q, label: label, title: lines.join('\n'), cls: cls, age: age, unit: u ? u.unit : null, major: major };
  };
  T.pxHtml = function (rec, opts) {
    opts = opts || {};
    var f = T.freshness(rec, opts);
    var shown = f.value == null ? '-' : (opts.money ? T.fmt.money(f.value, opts.dp) : T.fmt.px(f.value));
    if (f.unit && f.value != null) { shown = f.unit + ' ' + shown + (opts.showMajor && f.major ? ' (' + f.major + ')' : ''); }
    var size = opts.size === 'big' ? ' big' : (opts.size === 'inline' ? ' inline' : '');
    return '<span class="pxw ' + f.cls + size + '" title="' + T.esc(f.title) + '" data-q="' + T.esc(f.quality) + '">' +
      '<span class="px">' + T.esc(shown) + '</span>' +
      '<span class="fr"><span class="dot"></span>' + T.esc(f.label) + '</span></span>';
  };
  T.px = function (rec, opts) {
    var wrap = T.h('span');
    wrap.innerHTML = T.pxHtml(rec, opts);
    return wrap.firstChild;
  };
  T.qchip = function (quality, title) {
    var q = quality || 'unavailable';
    return T.h('span', { class: 'qchip q-' + q, title: title || ('quality: ' + q + ' - ' + (RULES[q] || RULES.unknown)) }, q.toUpperCase());
  };
  T.qchipHtml = function (quality, title) {
    var q = quality || 'unavailable';
    return '<span class="qchip q-' + T.esc(q) + '" title="' + T.esc(title || ('quality: ' + q + ' - ' + (RULES[q] || RULES.unknown))) + '">' + T.esc(q.toUpperCase()) + '</span>';
  };
  T.components.freshness = { rules: RULES, normalise: T.freshness };
})();
