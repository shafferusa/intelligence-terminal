/* Canvas 1-minute line + volume (TERMINAL_DESIGN.md s17.1 components/chart.js; WP-12; Build 2 P10 USX-06).  No library.
 * Bars are the API's `bars` ({t, o, h, l, c, v} with decimal strings) from /api/instruments/{id}/bars (the session's
 * 1-minute bars the quote worker fetched: BUILD2_PLAN C8) or a quote's bars_tail; `prev_close` draws the dashed reference
 * line.  An empty list renders the text "no bars" (or opts.empty) in the middle of the canvas - nothing is interpolated or
 * invented.  Times on the axis are New York.  Prices are drawn with `dp` decimals (the instrument's tick: 2 for stocks,
 * 4 for FX) so the last-price label never shows a raw float ("last 773.960022" was the sweep's).
 * The note (source, delay, session) sits ABOVE the canvas, never over it, so it can never overlap the "no bars" text.
 *
 *   var c = T.components.chart.mount(el, {bars: [], prev_close: '342.94', note: 'Yahoo 1-minute bars, up to 15 min delayed', dp: 2});
 *   c.update({bars: ..., prev_close: ..., note: ..., empty: 'fetching bars ...'});
 */
(function () {
  'use strict';
  var T = window.T;
  function cssVar(name, fallback) { try { var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return v || fallback; } catch (e) { return fallback; } }
  function fmt(x, dp) { var n = Number(x); return isFinite(n) ? T.fmt.num(n, dp) : 'n/a'; }
  function draw(canvas, bars, opts) {
    opts = opts || {};
    var dp = opts.dp == null ? 2 : Math.max(0, Math.min(6, Number(opts.dp) || 0));
    var dpr = window.devicePixelRatio || 1;
    var w = canvas.clientWidth || 600, h = canvas.clientHeight || 220;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    var ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    var fg3 = cssVar('--fg3', '#5d6474'), line = cssVar('--line', '#232a37'), accent = cssVar('--accent', '#f0b429'), blue = cssVar('--blue', '#58a6ff');
    ctx.font = '10px ' + cssVar('--mono', 'monospace');
    ctx.fillStyle = fg3;
    bars = (bars || []).filter(function (b) { return b && b.c != null && isFinite(Number(b.c)); });
    if (!bars.length) {
      ctx.save(); ctx.font = '12px ' + cssVar('--mono', 'monospace'); ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(opts.empty || 'no bars', w / 2, h / 2); ctx.restore();
      return;
    }
    var padL = 58, padR = 8, padT = 8, padB = 18;
    var priceH = Math.round((h - padT - padB) * 0.72), volTop = padT + priceH + 6, volH = h - padB - volTop;
    var closes = bars.map(function (b) { return Number(b.c); });
    var lo = Math.min.apply(null, closes), hi = Math.max.apply(null, closes);
    var pc = opts.prev_close != null && isFinite(Number(opts.prev_close)) ? Number(opts.prev_close) : null;
    if (pc != null) { lo = Math.min(lo, pc); hi = Math.max(hi, pc); }
    if (hi === lo) { var bump = Math.max(Math.abs(hi) * 0.001, Math.pow(10, -dp)); hi += bump; lo -= bump; }
    var pad = (hi - lo) * 0.05; hi += pad; lo -= pad;
    var n = bars.length;
    function x(i) { return padL + (n === 1 ? (w - padL - padR) / 2 : (w - padL - padR) * i / (n - 1)); }
    function y(p) { return padT + priceH - (p - lo) / (hi - lo) * priceH; }
    ctx.strokeStyle = line; ctx.lineWidth = 1;
    [lo + pad, (hi + lo) / 2, hi - pad].forEach(function (p) {
      ctx.beginPath(); ctx.moveTo(padL, y(p)); ctx.lineTo(w - padR, y(p)); ctx.stroke();
      ctx.fillStyle = fg3; ctx.fillText(fmt(p, dp), 4, y(p) + 3);
    });
    if (pc != null) {
      ctx.save(); ctx.setLineDash([3, 3]); ctx.strokeStyle = fg3; ctx.beginPath(); ctx.moveTo(padL, y(pc)); ctx.lineTo(w - padR, y(pc)); ctx.stroke(); ctx.restore();
      var pcl = 'prev close ' + fmt(pc, dp); ctx.fillStyle = fg3; ctx.fillText(pcl, w - padR - ctx.measureText(pcl).width, y(pc) - 3);
    }
    var vols = bars.map(function (b) { return b.v == null ? 0 : Number(b.v) || 0; });
    var vmax = Math.max.apply(null, vols) || 1;
    ctx.fillStyle = blue; ctx.globalAlpha = 0.35;
    var bw = Math.max(1, (w - padL - padR) / n - 1);
    bars.forEach(function (b, i) { var vh = vols[i] / vmax * volH; ctx.fillRect(x(i) - bw / 2, volTop + volH - vh, bw, vh); });
    ctx.globalAlpha = 1;
    ctx.strokeStyle = accent; ctx.lineWidth = 1.5; ctx.beginPath();
    bars.forEach(function (b, i) { var px = x(i), py = y(Number(b.c)); if (i === 0) { ctx.moveTo(px, py); } else { ctx.lineTo(px, py); } });
    ctx.stroke();
    if (n === 1) { ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(x(0), y(Number(bars[0].c)), 2.5, 0, Math.PI * 2); ctx.fill(); }
    ctx.fillStyle = fg3;
    ctx.fillText(T.fmt.hm(bars[0].t), padL, h - 5);
    var lastT = T.fmt.hm(bars[n - 1].t); ctx.fillText(lastT, w - padR - ctx.measureText(lastT).width, h - 5);
    var cnt = n + (n === 1 ? ' bar' : ' bars'); ctx.fillText(cnt, (w - ctx.measureText(cnt).width) / 2, h - 5);
    var lastC = 'last ' + fmt(bars[n - 1].c, dp); ctx.fillStyle = accent; ctx.fillText(lastC, w - padR - ctx.measureText(lastC).width, padT + 10);
  }
  function mount(el, opts) {
    opts = opts || {};
    var wrap = T.h('div', { class: 'chart-wrap' });
    var note = T.h('div', { class: 'chart-note', style: 'position:static;margin:0 0 3px 2px;min-height:13px' }, opts.note || '');
    var canvas = T.h('canvas', { class: 'chart', 'aria-label': 'one-minute price chart' });
    wrap.appendChild(note); wrap.appendChild(canvas);
    T.clear(el).appendChild(wrap);
    var cur = opts;
    var handle = {
      el: wrap,
      update: function (o) { cur = Object.assign({}, cur, o || {}); note.textContent = cur.note || ''; note.title = cur.title || cur.note || ''; draw(canvas, cur.bars, cur); },
      redraw: function () { draw(canvas, cur.bars, cur); },
      state: function () { return cur; }
    };
    handle.update(opts);
    var onResize = function () { handle.redraw(); };
    window.addEventListener('resize', onResize);
    handle.destroy = function () { window.removeEventListener('resize', onResize); };
    return handle;
  }
  T.components.chart = { mount: mount, draw: draw };
})();
