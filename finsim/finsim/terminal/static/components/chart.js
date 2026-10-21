/* Canvas 1-minute line + volume (TERMINAL_DESIGN.md s17.1 components/chart.js; WP-12).  No library.
 * Bars are the API's `bars` ({t, o, h, l, c, v} with decimal strings) from /api/instruments/{id}/bars or a quote's
 * bars_tail; `prev_close` draws the dashed reference line.  An empty list renders the note "no bars" - nothing is
 * interpolated or invented.  Times on the axis are New York.
 *
 *   var c = T.components.chart.mount(el, {bars: [], prev_close: '342.94', note: 'Yahoo 1-minute bars, up to 15 min delayed'});
 *   c.update({bars: ..., prev_close: ...});
 */
(function () {
  'use strict';
  var T = window.T;
  function cssVar(name, fallback) { try { var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); return v || fallback; } catch (e) { return fallback; } }
  function draw(canvas, bars, opts) {
    opts = opts || {};
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
    if (!bars.length) { ctx.fillText(opts.empty || 'no bars', 10, 20); return; }
    var padL = 54, padR = 8, padT = 8, padB = 18;
    var priceH = Math.round((h - padT - padB) * 0.72), volTop = padT + priceH + 6, volH = h - padB - volTop;
    var closes = bars.map(function (b) { return Number(b.c); });
    var lo = Math.min.apply(null, closes), hi = Math.max.apply(null, closes);
    var pc = opts.prev_close != null && isFinite(Number(opts.prev_close)) ? Number(opts.prev_close) : null;
    if (pc != null) { lo = Math.min(lo, pc); hi = Math.max(hi, pc); }
    if (hi === lo) { hi += 0.5; lo -= 0.5; }
    var pad = (hi - lo) * 0.05; hi += pad; lo -= pad;
    var n = bars.length;
    function x(i) { return padL + (n === 1 ? 0 : (w - padL - padR) * i / (n - 1)); }
    function y(p) { return padT + priceH - (p - lo) / (hi - lo) * priceH; }
    ctx.strokeStyle = line; ctx.lineWidth = 1;
    [lo + pad, (hi + lo) / 2, hi - pad].forEach(function (p) {
      ctx.beginPath(); ctx.moveTo(padL, y(p)); ctx.lineTo(w - padR, y(p)); ctx.stroke();
      ctx.fillStyle = fg3; ctx.fillText(T.fmt.px(p.toFixed(2)), 4, y(p) + 3);
    });
    if (pc != null) {
      ctx.save(); ctx.setLineDash([3, 3]); ctx.strokeStyle = fg3; ctx.beginPath(); ctx.moveTo(padL, y(pc)); ctx.lineTo(w - padR, y(pc)); ctx.stroke(); ctx.restore();
      ctx.fillStyle = fg3; ctx.fillText('prev ' + T.fmt.px(String(pc)), w - padR - 90, y(pc) - 3);
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
    ctx.fillStyle = fg3;
    ctx.fillText(T.fmt.hm(bars[0].t), padL, h - 5);
    var lastT = T.fmt.hm(bars[n - 1].t); ctx.fillText(lastT, w - padR - ctx.measureText(lastT).width, h - 5);
    var lastC = 'last ' + T.fmt.px(bars[n - 1].c); ctx.fillStyle = accent; ctx.fillText(lastC, w - padR - ctx.measureText(lastC).width, padT + 10);
  }
  function mount(el, opts) {
    opts = opts || {};
    var wrap = T.h('div', { class: 'chart-wrap' });
    var canvas = T.h('canvas', { class: 'chart', 'aria-label': 'one-minute price chart' });
    var note = T.h('div', { class: 'chart-note' }, opts.note || '');
    wrap.appendChild(canvas); wrap.appendChild(note);
    T.clear(el).appendChild(wrap);
    var cur = opts;
    var handle = {
      el: wrap,
      update: function (o) { cur = Object.assign({}, cur, o || {}); note.textContent = cur.note || ''; draw(canvas, cur.bars, cur); },
      redraw: function () { draw(canvas, cur.bars, cur); }
    };
    handle.update(opts);
    var onResize = function () { handle.redraw(); };
    window.addEventListener('resize', onResize);
    handle.destroy = function () { window.removeEventListener('resize', onResize); };
    return handle;
  }
  T.components.chart = { mount: mount, draw: draw };
})();
