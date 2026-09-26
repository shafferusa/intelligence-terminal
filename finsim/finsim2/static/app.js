/* FinSim2 — a quantitative evidence engine for portfolio management. The user decides; the page shows the evidence. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const N = v => (v == null || v === '' || Number.isNaN(Number(v))) ? null : Number(v);
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

  // ---------------------------------------------------------------- number formats
  const fmt = {
    money(v, d = 0, ccy = 'USD') { const n = N(v); if (n == null) return '—'; const sym = { USD: '$', EUR: '€', GBP: '£', JPY: '¥' }[ccy]; const s = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); return (n < 0 ? '−' : '') + (sym ? sym + s : s + ' ' + ccy); },
    big(v, ccy = 'USD') { const n = N(v); if (n == null) return '—'; const a = Math.abs(n); const sym = ccy === 'USD' ? '$' : ''; const suf = ccy === 'USD' ? '' : ' ' + ccy; const s = a >= 1e12 ? (a / 1e12).toFixed(2) + 'T' : a >= 1e9 ? (a / 1e9).toFixed(2) + 'B' : a >= 1e6 ? (a / 1e6).toFixed(2) + 'M' : a >= 1e3 ? (a / 1e3).toFixed(1) + 'K' : a.toFixed(0); return (n < 0 ? '−' : '') + sym + s + suf; },
    signed(v, d = 0) { const n = N(v); if (n == null) return '—'; return (n > 0 ? '+' : n < 0 ? '−' : '') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); },
    pct(v, d = 2) { const n = N(v); if (n == null) return '—'; return (n * 100).toFixed(d) + '%'; },
    spct(v, d = 2) { const n = N(v); if (n == null) return '—'; return (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n * 100).toFixed(d) + '%'; },
    num(v, d = 2) { const n = N(v); if (n == null) return '—'; return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); },
    px(v) { const n = N(v); if (n == null) return '—'; const a = Math.abs(n); const d = a >= 1000 ? 2 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6; return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); },
    qty(v) { const n = N(v); if (n == null) return '—'; return n.toLocaleString('en-US', { maximumFractionDigits: Math.abs(n) < 10 ? 4 : 0 }); },
    date(s) { if (!s) return '—'; const d = new Date(String(s).slice(0, 10) + 'T00:00:00Z'); return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }); },
  };
  const sign = v => N(v) == null ? '' : N(v) > 0 ? 'pos' : N(v) < 0 ? 'neg' : '';
  // a metric's display format from the analytics catalogue ('pct' | 'num' | 'ratio' | 'days' | 'bool')
  const SHORT = { ret_1d: '1D', log_ret_1d: '1D log', ret_1m: '1M', ret_3m: '3M', ret_1y: '1Y', cum_log_1y: '1Y log', ann_return: 'Ann. return', geo_mean: 'Geo mean', mean_ret: 'Mean', vol_ann: 'Volatility', stderr_mean: 'Std err', t_mean: 't (mean)', skew: 'Skew', kurt: 'Kurtosis', sharpe: 'Sharpe', sortino: 'Sortino', beta: 'Beta', corr_mkt: 'Corr', r2_mkt: 'R²', alpha_ann: 'Alpha', capm_er: 'CAPM E[R]', ewma_vol: 'EWMA vol', garch_vol: 'GARCH vol', garch_persistence: 'α+β', ar1_phi: 'AR(1) φ', half_life: 'Half-life', adf_t: 'ADF t', adf_stationary: 'Stationary', acf1: 'ACF(1)', zscore_50: 'Z-score', mom_12_1: 'Mom 12-1', max_drawdown: 'Max DD', var_95: 'VaR 95', es_95: 'ES 95', n: 'Obs' };
  const mfmt = (v, f) => { if (v == null) return '—'; if (f === 'pct') return fmt.pct(v, 1); if (f === 'days') return fmt.num(v, 1) + 'd'; if (f === 'bool') return v ? 'yes' : 'no'; if (f === 'int') return fmt.qty(v); return fmt.num(v, 2); };

  // ---------------------------------------------------------------- the server
  const keyStore = { get() { try { return localStorage.getItem('fs2.key') || new URLSearchParams(location.search).get('key') || ''; } catch (e) { return ''; } }, set(k) { try { localStorage.setItem('fs2.key', k); } catch (e) { } } };
  const pref = { get(k, d) { try { const v = localStorage.getItem('fs2.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } }, set(k, v) { try { localStorage.setItem('fs2.' + k, JSON.stringify(v)); } catch (e) { } } };
  async function api(path, opts = {}, retried = false) {
    const headers = { 'Content-Type': 'application/json' };
    const key = keyStore.get(); if (key) headers['X-FinSim-Key'] = key;
    const r = await fetch('/api' + path, { method: opts.method || (opts.body ? 'POST' : 'GET'), headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
    if (r.status === 401 && !retried) { const k = window.prompt('Access key for this FinSim2 server (printed by `python3 -m finsim2 phone`):', ''); if (k) { keyStore.set(k.trim()); return api(path, opts, true); } }
    const j = await r.json().catch(() => ({ error: 'bad response from the server' }));
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }
  const post = (path, body) => api(path, { method: 'POST', body: body || {} });
  function toast(msg, err = false) { const t = document.createElement('div'); t.className = 'toast' + (err ? ' err' : ''); t.textContent = msg; $('#toasts').appendChild(t); setTimeout(() => t.remove(), err ? 7000 : 3800); }
  function modal(html) { const m = $('#modal'); m.innerHTML = `<div class="bg"><div class="box">${html}</div></div>`; m.firstChild.addEventListener('click', e => { if (e.target === m.firstChild) closeModal(); }); return m.firstChild; }
  const closeModal = () => { $('#modal').innerHTML = ''; };
  function drawer(html) { const d = $('#drawer'); d.innerHTML = `<div class="row" style="justify-content:flex-end"><button class="ghost" id="drawerX">✕ Close</button></div>${html}`; d.classList.add('open'); $('#drawerX').onclick = closeDrawer; return d; }
  const closeDrawer = () => $('#drawer').classList.remove('open');
  const busy = async (btn, fn) => { if (btn) btn.disabled = true; try { return await fn(); } catch (e) { toast(e.message, true); } finally { if (btn) btn.disabled = false; } };

  // ---------------------------------------------------------------- charts (inline SVG, themed by tokens)
  const PALETTE = ['--accent', '--teal', '--violet', '--orange', '--pink', '--warn', '--pos', '--neg'];
  const col = i => css(PALETTE[i % PALETTE.length]);
  const niceTicks = (lo, hi, n = 4) => { if (hi === lo) { hi = lo + 1; } const span = hi - lo; const step0 = span / n; const mag = Math.pow(10, Math.floor(Math.log10(step0))); const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= step0) || step0; const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v); return out; };
  let tipEl = null;
  const tip = (html, e) => { if (!tipEl) { tipEl = document.createElement('div'); tipEl.className = 'tip'; document.body.appendChild(tipEl); } if (!html) { tipEl.style.display = 'none'; return; } tipEl.innerHTML = html; tipEl.style.display = 'block'; const x = Math.min(e.clientX + 14, innerWidth - tipEl.offsetWidth - 8); tipEl.style.left = x + 'px'; tipEl.style.top = (e.clientY + 14) + 'px'; };
  function lineChart(el, series, o = {}) {
    // series: [{name, data:[numbers|null], color?, area?}] ; o: {labels, h, fmtY, zero}
    const w = el.clientWidth || 640, h = o.h || 220, pl = 58, pr = 12, pt = 10, pb = 24;
    const all = series.flatMap(s => s.data).filter(v => v != null && isFinite(v));
    if (!all.length) { el.innerHTML = '<div class="empty">No history yet</div>'; return; }
    let lo = Math.min(...all), hi = Math.max(...all); if (o.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); } const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.02 || 1; lo -= pad; hi += pad;
    const n = Math.max(...series.map(s => s.data.length)); const X = i => pl + (n <= 1 ? 0 : i * (w - pl - pr) / (n - 1)); const Y = v => pt + (hi - v) * (h - pt - pb) / (hi - lo);
    const f = o.fmtY || (v => fmt.num(v, 0));
    let g = niceTicks(lo, hi).map(v => `<line class="grid-line" x1="${pl}" x2="${w - pr}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${pl - 8}" y="${Y(v) + 4}" text-anchor="end">${esc(f(v))}</text>`).join('');
    if (o.zero && lo < 0 && hi > 0) g += `<line x1="${pl}" x2="${w - pr}" y1="${Y(0)}" y2="${Y(0)}" stroke="${css('--faint')}" stroke-dasharray="3 3"/>`;
    const labels = o.labels || []; const lstep = Math.max(1, Math.ceil(n / 6));
    g += labels.map((l, i) => (i % lstep === 0 || i === n - 1) && i < n ? `<text x="${X(i)}" y="${h - 6}" text-anchor="${i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}">${esc(l)}</text>` : '').join('');
    const paths = series.map((s, si) => { const c = s.color || col(si); let d = '', started = false; s.data.forEach((v, i) => { if (v == null || !isFinite(v)) { started = false; return; } d += (started ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1); started = true; });
      const area = s.area ? `<path d="${d} L${X(s.data.length - 1)} ${h - pb} L${X(0)} ${h - pb} Z" fill="${c}" opacity=".10"/>` : '';
      return `${area}<path d="${d}" fill="none" stroke="${c}" stroke-width="${s.width || 2}" stroke-linejoin="round" stroke-linecap="round"/>`; }).join('');
    el.innerHTML = `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">${g}${paths}<line class="hov" x1="0" x2="0" y1="${pt}" y2="${h - pb}" stroke="${css('--faint')}" style="display:none"/></svg>` + (series.length > 1 ? `<div class="legend">${series.map((s, i) => `<span><i style="background:${s.color || col(i)}"></i>${esc(s.name)}</span>`).join('')}</div>` : '');
    const svg = el.querySelector('svg'), hov = svg.querySelector('.hov');
    svg.addEventListener('mousemove', e => { const r = svg.getBoundingClientRect(); const x = (e.clientX - r.left) * w / r.width; const i = Math.max(0, Math.min(n - 1, Math.round((x - pl) / ((w - pl - pr) / Math.max(1, n - 1))))); hov.setAttribute('x1', X(i)); hov.setAttribute('x2', X(i)); hov.style.display = '';
      tip(`<b>${esc(labels[i] || '')}</b><br>${series.map((s, si) => `<span style="color:${s.color || col(si)}">●</span> ${esc(s.name || '')} ${s.data[i] == null ? '—' : esc((o.fmtTip || f)(s.data[i]))}`).join('<br>')}`, e); });
    svg.addEventListener('mouseleave', () => { hov.style.display = 'none'; tip(null); });
  }
  function barChart(el, items, o = {}) {
    // items: [{label, value}] horizontal bars, signed
    if (!items.length) { el.innerHTML = '<div class="empty">Nothing to show</div>'; return; }
    const max = Math.max(...items.map(i => Math.abs(i.value))) || 1; const f = o.fmt || (v => fmt.signed(v));
    el.innerHTML = `<div style="display:flex;flex-direction:column;gap:7px">${items.map(i => { const w = Math.abs(i.value) / max * 50; const neg = i.value < 0;
      return `<div style="display:grid;grid-template-columns:130px 1fr 96px;gap:10px;align-items:center;font-size:12.5px"><span class="muted" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(i.label)}</span><div style="position:relative;height:14px;background:var(--surface-2);border-radius:4px"><div style="position:absolute;top:0;bottom:0;${neg ? `right:50%` : `left:50%`};width:${w}%;background:${neg ? 'var(--neg)' : 'var(--pos)'};border-radius:3px;opacity:.85"></div><div style="position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--border-strong)"></div></div><b class="num ${neg ? 'neg' : 'pos'}" style="text-align:right">${esc(f(i.value))}</b></div>`; }).join('')}</div>`;
  }
  function donut(el, items, o = {}) {
    const tot = items.reduce((a, i) => a + Math.abs(i.value), 0); if (!tot) { el.innerHTML = '<div class="empty">All cash</div>'; return; }
    const R = 62, r = 42, cx = 75, cy = 75; let a0 = -Math.PI / 2;
    const arcs = items.map((it, i) => { const a1 = a0 + Math.abs(it.value) / tot * Math.PI * 2; const large = a1 - a0 > Math.PI ? 1 : 0; const p = (a, rad) => `${cx + rad * Math.cos(a)} ${cy + rad * Math.sin(a)}`;
      const d = Math.abs(a1 - a0) >= Math.PI * 2 - 1e-6 ? `M${cx - R} ${cy} A${R} ${R} 0 1 1 ${cx + R} ${cy} A${R} ${R} 0 1 1 ${cx - R} ${cy} M${cx - r} ${cy} A${r} ${r} 0 1 0 ${cx + r} ${cy} A${r} ${r} 0 1 0 ${cx - r} ${cy}` : `M${p(a0, R)} A${R} ${R} 0 ${large} 1 ${p(a1, R)} L${p(a1, r)} A${r} ${r} 0 ${large} 0 ${p(a0, r)} Z`; a0 = a1;
      return `<path d="${d}" fill="${col(i)}" fill-rule="evenodd"><title>${esc(it.label)} ${fmt.pct(Math.abs(it.value) / tot, 1)}</title></path>`; }).join('');
    el.innerHTML = `<div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap"><svg width="150" height="150" viewBox="0 0 150 150">${arcs}<text x="75" y="72" text-anchor="middle" style="fill:var(--text);font-weight:700;font-size:15px">${esc(o.center || fmt.big(tot))}</text><text x="75" y="90" text-anchor="middle" style="fill:var(--muted);font-size:11px">${esc(o.sub || 'gross')}</text></svg>
      <div style="display:flex;flex-direction:column;gap:6px;font-size:12.5px;flex:1;min-width:160px">${items.map((it, i) => `<div class="row" style="gap:8px;flex-wrap:nowrap"><i style="width:10px;height:10px;border-radius:3px;background:${col(i)};flex:none"></i><span style="flex:1">${esc(it.label)}</span><b class="num">${fmt.pct(Math.abs(it.value) / tot, 1)}</b><span class="muted num" style="width:72px;text-align:right">${fmt.big(it.value)}</span></div>`).join('')}</div></div>`;
  }
  const spark = (data, w = 90, h = 26) => { const d = (data || []).filter(v => v != null); if (d.length < 2) return ''; const lo = Math.min(...d), hi = Math.max(...d); const X = i => i * w / (d.length - 1), Y = v => h - 2 - (v - lo) * (h - 4) / ((hi - lo) || 1); const c = d[d.length - 1] >= d[0] ? css('--pos') : css('--neg'); return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="${d.map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1)).join('')}" fill="none" stroke="${c}" stroke-width="1.6"/></svg>`; };

  // ---------------------------------------------------------------- sortable tables
  // cols: [{k, label, l (left), f (row => html), v (row => sortable value), cls (row => class)}]
  function table(el, rows, cols, o = {}) {
    const st = { key: o.sortKey || null, dir: o.sortDir || -1, limit: o.limit || 400 };
    const render = () => {
      let rs = rows.slice();
      if (st.key) { const c = cols.find(c => c.k === st.key); const val = c && (c.v || (r => r[c.k])); rs.sort((a, b) => { const x = val(a), y = val(b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return (typeof x === 'string' ? x.localeCompare(y) : x - y) * st.dir; }); }
      const shown = rs.slice(0, st.limit);
      el.innerHTML = `<div class="tbl-wrap" style="${o.maxH ? `max-height:${o.maxH}px` : ''}"><table><thead><tr>${cols.map(c => `<th class="${c.l ? 'l' : ''} ${c.nosort ? '' : 'sort'} ${st.key === c.k ? 'sorted' : ''}" data-k="${c.k}" title="${esc(c.title || '')}">${esc(c.label)}${st.key === c.k ? (st.dir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('')}</tr></thead><tbody>${shown.map((r, i) => `<tr class="${o.onRow ? 'click' : ''} ${o.rowCls ? o.rowCls(r) : ''}" data-i="${rows.indexOf(r)}">${cols.map(c => `<td class="${c.l ? 'l' : ''} ${c.cls ? c.cls(r) : ''}">${c.f ? c.f(r) : esc(r[c.k])}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="${cols.length}" class="empty">${esc(o.empty || 'Nothing here yet')}</td></tr>`}${o.total ? `<tr class="total">${cols.map(c => `<td class="${c.l ? 'l' : ''}">${o.total[c.k] != null ? o.total[c.k] : ''}</td>`).join('')}</tr>` : ''}</tbody></table></div>${rs.length > st.limit ? `<div class="row" style="justify-content:center;padding:10px"><button class="small more">Show ${Math.min(400, rs.length - st.limit)} more of ${rs.length - st.limit}</button></div>` : ''}`;
      $$('th.sort', el).forEach(th => th.onclick = () => { const k = th.dataset.k; if (st.key === k) st.dir = -st.dir; else { st.key = k; st.dir = (cols.find(c => c.k === k).l ? 1 : -1); } render(); });
      if (o.onRow) $$('tbody tr.click', el).forEach(tr => tr.onclick = e => { if (e.target.closest('button,a,input,select')) return; o.onRow(rows[+tr.dataset.i]); });
      const more = $('.more', el); if (more) more.onclick = () => { st.limit += 400; render(); };
      if (o.after) o.after(el);
    };
    render();
  }


  // ---------------------------------------------------------------- state
  const S = { status: null, assets: null, current: pref.get('asset', 'SPY'), features: null, jobsSeen: {} };
  const HZ = ['1D', '1W', '1M', '3M', '6M', '12M', '3Y', '5Y', '10Y'];
  async function assets() { if (!S.assets) S.assets = await api('/fs2/assets'); return S.assets; }
  async function featureMeta() { if (!S.features) S.features = await api('/fs2/features'); return S.features; }
  const assetName = id => ((S.assets || []).find(a => a.id === id) || {}).name || id;
  const CLS = { EQUITY: 'Stocks', ETF: 'ETFs', INDEX: 'Indices', TREASURY: 'Treasuries', CORP_BOND: 'Corporate bonds', COMMODITY: 'Commodities', FUTURE: 'Futures', FX: 'Currencies', CRYPTO: 'Crypto', Cash: 'Cash' };
  const clsName = k => CLS[k] || String(k || 'Other').replace(/_/g, ' ');
  const kpi = (k, v, s = '') => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
  const pct0 = v => N(v) == null ? '—' : Math.round(N(v) * 100) + '%';

  // ---------------------------------------------------------------- more charts
  const divColor = (v, max = 1) => { if (v == null) return 'transparent'; const t = Math.max(-1, Math.min(1, v / max)); const c = t >= 0 ? css('--pos') : css('--neg'); return `color-mix(in srgb, ${c} ${Math.round(Math.abs(t) * 85)}%, var(--surface))`; };
  function heatmap(el, rows, cols, cell, o = {}) {
    // rows: [{key,label,sub}], cols: [{key,label}], cell(row,col) -> {v, text, title}
    el.innerHTML = `<div class="tbl-wrap" style="max-height:${o.maxH || 720}px"><table class="heat"><thead><tr><th class="l">${esc(o.corner || '')}</th>${cols.map(c => `<th style="text-align:center">${esc(c.label)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr><td class="l"><b>${esc(r.label)}</b>${r.sub ? `<span class="sub">${esc(r.sub)}</span>` : ''}</td>${cols.map(c => { const x = cell(r, c) || {}; return `<td class="hc ${x.v != null && o.onCell ? 'click' : ''}" data-r="${esc(r.key)}" data-c="${esc(c.key)}" title="${esc(x.title || '')}" style="background:${divColor(x.v, o.max || 1)};text-align:center">${x.text != null ? x.text : ''}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div>`;
    if (o.onCell) $$('td.hc.click', el).forEach(td => td.onclick = () => o.onCell(td.dataset.r, td.dataset.c));
  }
  function hbars(el, items, o = {}) {
    // items: [{label, value, sub}] horizontal bars, 0..max (or signed)
    if (!items.length) { el.innerHTML = `<div class="empty">${esc(o.empty || 'Nothing to show')}</div>`; return; }
    const max = Math.max(...items.map(i => Math.abs(i.value))) || 1; const f = o.fmt || (v => fmt.pct(v, 0));
    el.innerHTML = `<div style="display:flex;flex-direction:column;gap:7px">${items.map((i, k) => `<div style="display:grid;grid-template-columns:minmax(120px,190px) 1fr 70px;gap:10px;align-items:center;font-size:12.5px"><span title="${esc(i.sub || '')}" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(i.label)}</span><div style="height:12px;background:var(--surface-2);border-radius:4px;position:relative"><div style="position:absolute;left:0;top:0;bottom:0;width:${Math.abs(i.value) / max * 100}%;background:${i.value < 0 ? css('--neg') : (o.color || col(k))};border-radius:4px;opacity:.85"></div></div><b class="num" style="text-align:right">${esc(f(i.value))}</b></div>`).join('')}</div>`;
  }
  function scatter(el, pts, o = {}) {
    // pts: [{x, y, label, color, r, line}] ; o.line: [{x,y}] drawn as a curve
    const w = el.clientWidth || 600, h = o.h || 300, pl = 56, pr = 14, pt = 12, pb = 34;
    const all = pts.concat(o.line || []);
    if (!all.length) { el.innerHTML = '<div class="empty">Nothing to plot</div>'; return; }
    let x0 = Math.min(...all.map(p => p.x)), x1 = Math.max(...all.map(p => p.x)), y0 = Math.min(...all.map(p => p.y)), y1 = Math.max(...all.map(p => p.y));
    const px = (x1 - x0) * 0.08 || 0.01, py = (y1 - y0) * 0.1 || 0.01; x0 -= px; x1 += px; y0 -= py; y1 += py;
    const X = v => pl + (v - x0) * (w - pl - pr) / (x1 - x0), Y = v => pt + (y1 - v) * (h - pt - pb) / (y1 - y0);
    const fx = o.fmtX || (v => fmt.pct(v, 0)), fy = o.fmtY || (v => fmt.pct(v, 0));
    let g = niceTicks(y0, y1).map(v => `<line class="grid-line" x1="${pl}" x2="${w - pr}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${pl - 8}" y="${Y(v) + 4}" text-anchor="end">${esc(fy(v))}</text>`).join('');
    g += niceTicks(x0, x1, 5).map(v => `<text x="${X(v)}" y="${h - 14}" text-anchor="middle">${esc(fx(v))}</text>`).join('');
    g += `<text x="${w / 2}" y="${h - 1}" text-anchor="middle">${esc(o.xLabel || '')}</text>`;
    if (o.line && o.line.length) g += `<path d="${o.line.map((p, i) => (i ? 'L' : 'M') + X(p.x).toFixed(1) + ' ' + Y(p.y).toFixed(1)).join('')}" fill="none" stroke="${css('--accent')}" stroke-width="2"/>`;
    g += pts.map(p => `<circle cx="${X(p.x)}" cy="${Y(p.y)}" r="${p.r || 5}" fill="${p.color || css('--faint')}" opacity=".9"><title>${esc(p.label || '')}: ${esc(fx(p.x))}, ${esc(fy(p.y))}</title></circle>${p.text ? `<text x="${X(p.x) + 7}" y="${Y(p.y) - 6}" style="fill:var(--text);font-weight:600">${esc(p.text)}</text>` : ''}`).join('');
    el.innerHTML = `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" height="${h}">${g}</svg>`;
  }
  function histogram(el, bins, counts, o = {}) {
    const w = el.clientWidth || 500, h = o.h || 170, pl = 10, pr = 10, pt = 8, pb = 22;
    if (!counts || !counts.length) { el.innerHTML = '<div class="empty">No distribution</div>'; return; }
    const m = Math.max(...counts) || 1; const bw = (w - pl - pr) / counts.length;
    const marks = (o.mark != null && bins.length) ? (() => { const lo = bins[0], hi = bins[bins.length - 1]; const x = pl + (o.mark - lo) / ((hi - lo) || 1) * (w - pl - pr); return `<line x1="${x}" x2="${x}" y1="${pt}" y2="${h - pb}" stroke="${css('--warn')}" stroke-width="2"/>`; })() : '';
    el.innerHTML = `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" height="${h}">${counts.map((c, i) => `<rect x="${pl + i * bw + 1}" y="${pt + (h - pt - pb) * (1 - c / m)}" width="${Math.max(1, bw - 2)}" height="${(h - pt - pb) * c / m}" fill="${(bins[i] || 0) < 0 ? css('--neg') : css('--accent')}" opacity=".75"/>`).join('')}${marks}<text x="${pl}" y="${h - 6}">${esc((o.fmt || (v => fmt.pct(v, 1)))(bins[0]))}</text><text x="${w - pr}" y="${h - 6}" text-anchor="end">${esc((o.fmt || (v => fmt.pct(v, 1)))(bins[bins.length - 1]))}</text></svg>`;
  }
  function cone(el, band, start, o = {}) {
    const w = el.clientWidth || 600, h = o.h || 280, pl = 70, pr = 12, pt = 10, pb = 24;
    if (!band || !band.length) { el.innerHTML = ''; return; }
    const days = [0, ...band.map(b => b.day)];
    const get = k => [start, ...band.map(b => b[k])];
    const hi = Math.max(...get('p95')), lo = Math.min(...get('p5'));
    const X = d => pl + d / days[days.length - 1] * (w - pl - pr), Y = v => pt + (hi - v) / ((hi - lo) || 1) * (h - pt - pb);
    const area = (a, b, c, op) => `<path d="${days.map((d, i) => (i ? 'L' : 'M') + X(d) + ' ' + Y(get(a)[i])).join('')} ${days.slice().reverse().map((d, i) => 'L' + X(d) + ' ' + Y(get(b)[days.length - 1 - i])).join('')} Z" fill="${c}" opacity="${op}"/>`;
    const line = (k, c, wd) => `<path d="${days.map((d, i) => (i ? 'L' : 'M') + X(d) + ' ' + Y(get(k)[i])).join('')}" fill="none" stroke="${c}" stroke-width="${wd}"/>`;
    const g = niceTicks(lo, hi).map(v => `<line class="grid-line" x1="${pl}" x2="${w - pr}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${pl - 8}" y="${Y(v) + 4}" text-anchor="end">${esc(fmt.big(v))}</text>`).join('');
    el.innerHTML = `<svg class="chart" viewBox="0 0 ${w} ${h}" width="100%" height="${h}">${g}${area('p95', 'p5', css('--accent'), .12)}${area('p75', 'p25', css('--accent'), .22)}${line('p50', css('--accent'), 2.2)}<line x1="${pl}" x2="${w - pr}" y1="${Y(start)}" y2="${Y(start)}" stroke="${css('--faint')}" stroke-dasharray="4 4"/><text x="${w - pr}" y="${h - 6}" text-anchor="end">${esc(o.label || (days[days.length - 1] + ' sessions'))}</text></svg><div class="legend"><span><i style="background:${css('--accent')};opacity:.35"></i>25–75th percentile</span><span><i style="background:${css('--accent')};opacity:.15"></i>5–95th percentile</span><span><i style="background:${css('--accent')}"></i>median</span></div>`;
  }

  // ---------------------------------------------------------------- shared evidence components
  const scoreCls = s => N(s) == null ? '' : s >= 15 ? 'pos' : s <= -15 ? 'neg' : '';
  const scoreTxt = s => { if (N(s) == null) return '—'; const n = Math.round(s); return (n > 0 ? '+' : n < 0 ? '−' : '') + Math.abs(n); };
  const confPill = c => !c ? '' : `<span class="pill ${c.label === 'High' ? 'pos' : c.label === 'Medium' ? 'warn' : ''}" title="evidence ${pct0((c.parts || {}).evidence)} · sample ${pct0((c.parts || {}).sample_size)} · stability ${pct0((c.parts || {}).stability)} · out-of-sample accuracy ${pct0((c.parts || {}).accuracy)}">${pct0(c.value)} ${esc(c.label)}</span>`;
  const sigPill = s => !s ? '<span class="faint">—</span>' : `<span class="pill ${/Bullish/.test(s) ? 'pos' : /Bearish/.test(s) ? 'neg' : ''}">${esc(s)}</span>`;
  const AGR = { 'STRONG AGREEMENT': 'pos', 'MODERATE AGREEMENT': 'acc', 'MIXED': 'warn', 'STRONG DISAGREEMENT': 'neg', 'NO VERIFIED ML EDGE': '' };
  const agrPill = a => a ? `<span class="pill ${AGR[a] || ''}" style="font-size:10.5px">${esc(a === 'NO VERIFIED ML EDGE' ? 'no verified ML edge' : a.toLowerCase())}</span>` : '<span class="faint">—</span>';
  function scoreStrip(hs, o = {}) {
    // one column per horizon: the Shaffer Score (raw and calibrated), the ML score, their agreement, confidence, expected return and range, evidence
    const td = (h, f) => `<td style="text-align:center">${f(hs[h] || {})}</td>`;
    return `<div class="tbl-wrap"><table class="strip"><thead><tr><th class="l"></th>${HZ.map(h => `<th style="text-align:center">${h}</th>`).join('')}</tr></thead><tbody>
      <tr><td class="l"><b>Shaffer Score</b><span class="sub">family-weighted evidence, −100..100</span></td>${HZ.map(h => { const r = hs[h] || {}; return `<td style="text-align:center;background:${divColor(r.score, 100)}" title="${esc(r.reason || '')}"><b class="num">${scoreTxt(r.score)}</b></td>`; }).join('')}</tr>
      <tr><td class="l">Calibrated<span class="sub">what this score has meant out of sample</span></td>${HZ.map(h => td(h, r => r.calibrated == null ? '<span class="faint">—</span>' : `<span class="num ${scoreCls(r.calibrated)}">${scoreTxt(r.calibrated)}</span>`)).join('')}</tr>
      <tr><td class="l"><b>ML score</b><span class="sub">walk-forward ensemble</span></td>${HZ.map(h => { const r = hs[h] || {}; return `<td style="text-align:center;background:${divColor(r.ml_score, 100)}"><span class="num">${scoreTxt(r.ml_score)}</span></td>`; }).join('')}</tr>
      <tr><td class="l">Shaffer vs ML</td>${HZ.map(h => td(h, r => agrPill(r.agreement))).join('')}</tr>
      <tr><td class="l">Confidence</td>${HZ.map(h => td(h, r => r.score != null && r.confidence ? confPill(r.confidence) : '<span class="faint">—</span>')).join('')}</tr>
      <tr><td class="l">Expected return<span class="sub">typical historical range</span></td>${HZ.map(h => td(h, r => r.expected != null ? `<b class="${sign(r.expected)}" style="font-size:12px">${fmt.spct(r.expected, 1)}</b>${r.range ? `<span class="sub">${fmt.spct(r.range[0], 0)} … ${fmt.spct(r.range[1], 0)}</span>` : ''}` : (r.range ? `<span class="sub">${fmt.spct(r.range[0], 0)} … ${fmt.spct(r.range[1], 0)}</span>` : '<span class="faint" title="not supported by the calibration yet">—</span>'))).join('')}</tr>
      <tr><td class="l">Evidence<span class="sub">independent observations</span></td>${HZ.map(h => td(h, r => r.evidence ? `<span style="font-size:12px">${esc(r.evidence)}</span><span class="sub">${fmt.num(r.n_eff, 0)}</span>` : '<span class="faint">—</span>')).join('')}</tr>
      <tr><td class="l">Out-of-sample IC<span class="sub">of the point-in-time score</span></td>${HZ.map(h => { const x = (hs[h] || {}).oos || {}; return `<td style="text-align:center;font-size:12px" title="t = ${fmt.num(x.t, 2)}, ${fmt.num(x.n_eff, 0)} independent observations">${x.ic != null ? fmt.num(x.ic, 2) : '<span class="faint">—</span>'}</td>`; }).join('')}</tr>
    </tbody></table></div>`;
  }
  function familyBlock(r) {
    // the score split into family points (they add up to it), and the signals doing most of the work either way
    if (!r || r.score == null) return `<div class="empty">${esc((r || {}).reason || 'No score at this horizon')}</div>`;
    const fams = (r.families || []).filter(f => Math.abs(f.points) >= 0.05);
    const li = (xs) => (xs || []).map(x => `<div class="row" style="flex-wrap:nowrap;padding:3px 0"><span style="flex:1">${esc(x.label || x.signal)} <span class="faint">${esc(x.family || '')}</span></span><b class="num ${sign(x.points)}">${x.points > 0 ? '+' : ''}${fmt.num(x.points, 1)}</b></div>`).join('') || '<div class="faint">none</div>';
    return `<div class="stack" style="gap:10px"><div>${fams.map(f => `<div class="row" style="flex-wrap:nowrap;padding:3px 0"><b style="flex:1">${esc(f.family)}</b><b class="num ${sign(f.points)}">${f.points > 0 ? '+' : ''}${fmt.num(f.points, 1)}</b></div>`).join('') || '<div class="faint">no family has usable evidence</div>'}</div>
      <div><div class="muted" style="font-weight:650;margin-bottom:2px">Strongest contributors</div>${li(r.contributors)}</div><div><div class="muted" style="font-weight:650;margin-bottom:2px">Contradicting</div>${li(r.contradicting)}</div></div>`;
  }
  function matters(list) {
    return (list || []).slice(0, 6).map((m, i) => `<div class="row" style="padding:5px 0;border-top:1px solid var(--border);flex-wrap:nowrap"><span class="faint" style="width:18px">${i + 1}</span><b style="flex:1">${esc(m.family)}</b><span class="cell-bar bar" style="width:90px"><i style="width:${Math.round(m.share * 100 / ((list[0] || {}).share || 1))}%"></i></span><span class="pill ${m.importance === 'HIGH' ? 'acc' : m.importance === 'MEDIUM' ? 'warn' : ''}">${m.importance}</span></div>`).join('') || '<div class="empty">Not enough evidence</div>';
  }
  function explainBlock(ex) {
    if (!ex) return '<div class="empty">No explanation yet</div>';
    const li = (xs, c) => (xs || []).map(x => `<div class="row" style="flex-wrap:nowrap;padding:3px 0"><span style="flex:1">${esc(x.label)} <span class="faint">${esc(x.family)}</span></span><b class="num ${c}">${x.contribution > 0 ? '+' : ''}${fmt.num(x.contribution, 3)}</b></div>`).join('') || '<div class="faint">none</div>';
    return `<div class="stack" style="gap:10px"><div><div class="pos" style="font-weight:650;margin-bottom:2px">Bullish</div>${li(ex.bullish, 'pos')}</div><div><div class="neg" style="font-weight:650;margin-bottom:2px">Bearish</div>${li(ex.bearish, 'neg')}</div><div><div class="muted" style="font-weight:650;margin-bottom:2px">Neutral</div>${li(ex.neutral, '')}</div></div>`;
  }
  const varTxt = v => { if (v == null) return '—'; if (typeof v === 'number') return fmt.num(v, Math.abs(v) < 1 ? 4 : 2); if (Array.isArray(v)) return v.length > 4 ? `${v.length} values` : v.map(varTxt).join(', '); if (typeof v === 'object') { const e = Object.entries(v); return e.slice(0, 3).map(([k, x]) => `${k} ${varTxt(x)}`).join(', ') + (e.length > 3 ? ' …' : ''); } return String(v); };
  function analyticCard(r) {
    // the standardised output: value, percentile, direction, signal, usefulness, confidence, best horizon, hit rate, IC, sample
    const hist = r.history && r.history.values && r.history.values.length > 3 ? spark(r.history.values, 120, 30) : '';
    return `<div class="acard ${r.applies === false ? 'na' : ''}"><div class="row" style="justify-content:space-between;flex-wrap:nowrap;align-items:flex-start"><div><div class="faint" style="font-size:11px">${esc(r.id || '')} ${esc(r.family || '')}</div><b>${esc(r.name)}</b></div>${sigPill(r.signal)}</div>
      <div class="row" style="align-items:flex-end;justify-content:space-between;margin-top:6px"><div class="av">${esc(r.display != null ? r.display : (r.value == null ? '—' : fmt.num(r.value, 3)))}</div>${hist}</div>
      <div class="meta">${r.percentile != null ? `<span>${Math.round(r.percentile * 100)}th pct</span>` : ''}${r.direction ? `<span>${esc(r.direction)}</span>` : ''}${r.best_horizon ? `<span>best ${esc(r.best_horizon)}</span>` : ''}${r.usefulness != null ? `<span title="evidence-weighted IC">use ${fmt.num(r.usefulness, 2)}</span>` : ''}${r.hit_rate != null ? `<span>hit ${fmt.pct(r.hit_rate, 0)}</span>` : ''}${r.ic != null ? `<span>IC ${fmt.num(r.ic, 2)}</span>` : ''}${r.n_eff != null ? `<span title="independent observations">n_eff ${fmt.num(r.n_eff, 0)}</span>` : ''}</div>
      ${r.interpretation ? `<div class="interp">${esc(r.interpretation)}</div>` : ''}${r.note ? `<div class="faint" style="font-size:11.5px">${esc(r.note)}</div>` : ''}</div>`;
  }

  // ---------------------------------------------------------------- navigation
  const ICON = {
    dashboard: '<path d="M3 13h8V3H3zm10 8h8V11h-8zM3 21h8v-6H3zm10-18v6h8V3z"/>',
    portfolio: '<path d="M4 7h16v12H4zM9 7V5h6v2" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
    markets: '<path d="M3 17l5-6 4 3 6-8 3 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>',
    asset: '<circle cx="11" cy="11" r="6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M20 20l-4.5-4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    analytics: '<path d="M5 4v16h15" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 15c2-5 4-7 6-4s3 1 5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    quant: '<path d="M4 6h16M4 12h10M4 18h6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="18" cy="16" r="3" fill="none" stroke="currentColor" stroke-width="2"/>',
    ml: '<circle cx="6" cy="6" r="2.2" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="6" cy="18" r="2.2" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="18" cy="12" r="2.2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M8 7l8 4M8 17l8-4" stroke="currentColor" stroke-width="1.6"/>',
    risk: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
    hedge: '<path d="M4 12h6M14 12h6M10 7l4 5-4 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" fill="none" stroke="currentColor" stroke-width="1.2" opacity=".5"/>',
    health: '<path d="M3 12h4l2-5 4 10 2-5h6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    backtests: '<path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    watchlist: '<path d="M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    settings: '<circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  };
  const NAV = [['', 'Portfolio'], ['dashboard', 'Dashboard'], ['portfolio', 'Portfolio'], ['risk', 'Risk'], ['hedge', 'Shaffer Hedge'], ['watchlist', 'Watchlist'], ['', 'Research'], ['markets', 'Markets'], ['asset', 'Asset Research'], ['analytics', 'Analytics'], ['quant', 'Quant Lab'], ['ml', 'ML Lab'], ['health', 'Model health'], ['backtests', 'Backtests'], ['', ''], ['settings', 'Settings']];
  const TITLES = { dashboard: 'Dashboard', portfolio: 'Portfolio', risk: 'Risk', hedge: 'Shaffer Hedge', watchlist: 'Watchlist', markets: 'Markets', asset: 'Asset Research', analytics: 'Analytics', quant: 'Quant Lab', ml: 'ML Lab', health: 'Model health', backtests: 'Backtests', settings: 'Settings', setup: 'Getting started' };
  function renderNav(active) {
    $('#nav').innerHTML = NAV.map(([k, label]) => !k ? (label ? `<div class="sec">${label}</div>` : '<div style="height:8px"></div>') : `<a href="#/${k}${['asset', 'analytics', 'quant'].includes(k) ? '/' + encodeURIComponent(S.current) : ''}" class="${active === k ? 'on' : ''}"><svg viewBox="0 0 24 24" fill="currentColor">${ICON[k]}</svg>${label}</a>`).join('');
    $$('#nav a').forEach(a => a.onclick = () => $('#side').classList.remove('open'));
  }
  function renderShell() {
    const st = S.status || {};
    const jobs = st.jobs || [];
    $('#fundBox').innerHTML = `<div class="row" style="justify-content:space-between"><b>Data</b>${st.data_ready ? `<span class="pill pos"><span class="dot"></span>${esc(st.last_price_date || '')}</span>` : '<span class="pill warn">not loaded</span>'}</div>
      <div class="muted" style="margin-top:4px">${st.assets || 0} assets · ${st.spy_rows ? Math.round(st.spy_rows / 252) + ' years' : '—'}</div>
      ${jobs.length ? jobs.map(j => `<div style="margin-top:8px"><div class="row" style="justify-content:space-between;font-size:12px"><span>${esc(j.kind)} ${esc(j.key)}</span><span class="faint">${j.total ? Math.round(j.done / j.total * 100) + '%' : '…'}</span></div><div class="bar"><i style="width:${j.total ? j.done / j.total * 100 : 5}%"></i></div><div class="faint" style="font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(j.message || '')}</div></div>`).join('') : `<button class="small" id="refreshBtn" style="margin-top:8px;width:100%;justify-content:center">Refresh data</button>`}`;
    const rb = $('#refreshBtn'); if (rb) rb.onclick = () => busy(rb, async () => { await post('/fs2/refresh', {}); toast('Refreshing prices, macro and fundamentals'); await loadStatus(); });
  }
  async function loadStatus() { try { S.status = await api('/fs2/status'); } catch (e) { S.status = null; } renderShell(); }

  // ---------------------------------------------------------------- router
  const pages = {};
  let routeSeq = 0;
  async function route() {
    const seq = ++routeSeq;
    const parts = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
    let page = parts[0] || 'dashboard';
    if (S.status && !S.status.data_ready) page = 'setup';
    if (!pages[page]) page = 'dashboard';
    if (['asset', 'analytics', 'quant'].includes(page) && parts[1]) { S.current = parts[1]; pref.set('asset', S.current); }
    if (page === 'ml' && parts[1] === 'forecasts' && parts[2]) { S.current = parts[2]; pref.set('asset', S.current); }
    renderNav(page);
    $('#pageTitle').textContent = TITLES[page] || 'FinSim2';
    const main = $('#main');
    main.innerHTML = '<div class="skeleton"></div>';
    try { await pages[page](main, parts.slice(1), () => seq === routeSeq); }
    catch (e) { if (seq === routeSeq) main.innerHTML = `<div class="card"><h2>Could not load this page</h2><p class="muted">${esc(e.message)}</p></div>`; }
  }
  window.addEventListener('hashchange', route);
  const go = h => { if (location.hash === h) route(); else location.hash = h; };
  function assetPicker(id, current, onPick) {
    return `<div class="search" style="flex:0 1 320px;position:relative"><input id="${id}" value="${esc(current || '')}" placeholder="Asset (SPY, NVDA, GOLD, EURUSD…)" autocomplete="off"><div class="dd" id="${id}dd"></div></div>`;
  }
  function bindPicker(id, onPick) {
    const q = $('#' + id), dd = $('#' + id + 'dd'); if (!q) return;
    q.addEventListener('focus', () => q.select());
    q.addEventListener('input', async () => { const v = q.value.trim().toUpperCase(); const list = await assets(); const hits = list.filter(a => a.id.toUpperCase().startsWith(v) || a.name.toUpperCase().includes(v)).slice(0, 12);
      dd.innerHTML = hits.map(h => `<div class="hit" data-id="${esc(h.id)}"><b>${esc(h.id)}</b><span>${esc(h.name)} <small>${esc(clsName(h.asset_class))}</small></span><span></span></div>`).join('') || '<div class="hit"><span class="muted">No match</span></div>'; dd.style.display = 'block';
      $$('.hit[data-id]', dd).forEach(el => el.onmousedown = () => { dd.style.display = 'none'; onPick(el.dataset.id); }); });
    q.addEventListener('keydown', e => { if (e.key === 'Enter') { const f = $('.hit[data-id]', dd); if (f) { dd.style.display = 'none'; onPick(f.dataset.id); } } });
    q.addEventListener('blur', () => setTimeout(() => { dd.style.display = 'none'; }, 150));
  }
  async function pollJob(id, onDone, el) {
    for (;;) {
      const j = await api('/fs2/jobs/' + id);
      if (el) el.innerHTML = `<div class="row" style="justify-content:space-between;font-size:12.5px"><span>${esc(j.message || j.status)}</span><span class="faint">${j.elapsed}s</span></div><div class="bar"><i style="width:${j.total ? j.done / j.total * 100 : 8}%"></i></div>`;
      if (j.status === 'done' || j.status === 'failed') { await loadStatus(); if (j.status === 'failed') toast(j.error || 'the job failed', true); if (onDone) onDone(j); return j; }
      await new Promise(r => setTimeout(r, 1500));
    }
  }

  // ---------------------------------------------------------------- first run: download the history
  pages.setup = async (main) => {
    main.innerHTML = `<div class="welcome"><h1>Load the market history.</h1>
      <p class="muted" style="font-size:15px">FinSim2 works from real history: daily prices for about 130 assets (stocks, ETFs, indices, Treasuries, credit, commodities, currencies, crypto) back to the 1990s where they exist, macro series from FRED and company fundamentals from SEC filings. The first download takes two to four minutes; after that it updates itself after each close.</p>
      <div class="card"><div id="setupJob"></div><button class="primary" id="setupGo" style="margin-top:12px">Download history</button></div></div>`;
    const running = (S.status && S.status.jobs || []).find(j => j.kind === 'refresh');
    const done = () => { S.assets = null; toast('History loaded'); go('#/dashboard'); };
    if (running) { $('#setupGo').disabled = true; pollJob(running.id, done, $('#setupJob')); }
    $('#setupGo').onclick = () => busy($('#setupGo'), async () => { const j = await post('/fs2/refresh', {}); $('#setupGo').disabled = true; pollJob(j.id, done, $('#setupJob')); });
  };

  // ---------------------------------------------------------------- dashboard
  const CORE = ['SPY', 'QQQ', 'IWM', 'EFA', 'UST10Y', 'TLT', 'HYG', 'GOLD', 'WTI', 'DXY', 'EURUSD', 'BTC'];
  pages.dashboard = async (main, _, alive) => {
    const [port, mk, preds] = await Promise.all([api('/fs2/portfolio').catch(() => null), api('/fs2/markets').catch(() => []), api('/fs2/predictions').catch(() => ({}))]);
    if (!alive()) return;
    const byId = Object.fromEntries(mk.map(r => [r.id, r]));
    const held = port ? port.positions.filter(p => Math.abs(p.quantity) > 1e-12) : [];
    let spyB = null; try { spyB = await api('/fs2/asset/SPY'); } catch (e) { }
    if (!alive()) return;
    const reg = spyB ? spyB.regime : null;
    main.innerHTML = `<div class="page-head"><div><h1>Dashboard</h1><p>The evidence behind your portfolio and the market, as of ${esc(S.status && S.status.last_price_date || '')}. Scores are −100 (strongly bearish evidence) to +100 (strongly bullish); every number carries its confidence.</p></div>
      <div class="row"><button id="scanBtn">Research portfolio & core markets</button></div></div>
      <div id="scanJob"></div>
      <div class="tiles">${port ? kpi('Net asset value', fmt.money(port.nav), `P&L ${fmt.signed(port.pnl)} since funding`) + kpi('Cash', fmt.money(port.cash), fmt.pct(port.cash / (port.nav || 1), 0) + ' of NAV') + kpi('Today', `<span class="${sign((port.performance || {}).daily_pnl)}">${fmt.signed((port.performance || {}).daily_pnl)}</span>`, `${fmt.spct((port.performance || {}).daily_return, 2)} · since start ${fmt.spct((port.performance || {}).twr, 1)} (time-weighted) vs ${esc(port.benchmark || 'SPY')} ${fmt.spct(((port.performance || {}).benchmark || {}).total, 1)}`) + kpi('Annual volatility', fmt.pct((port.risk || {}).vol, 1), `1-day VaR 95% ${fmt.pct((port.risk || {}).var95, 2)} · ES ${fmt.pct((port.risk || {}).es95, 2)}`) + kpi('Beta to S&P 500', fmt.num(port.beta, 2), `duration ${fmt.num(port.duration, 1)} years`) : kpi('Portfolio', '—', '')}
        ${kpi('Market regime', `<span style="font-size:16px">${esc(reg ? reg.description : '—')}</span>`, reg ? Object.values(reg.labels).slice(3).join(' · ') : '')}
        ${kpi('Forecast record', preds.scored ? fmt.pct(preds.hit_rate, 0) + ' hit rate' : '—', `${preds.scored || 0} scored · ${preds.pending || 0} pending`)}</div>
      <div class="grid g-main"><div class="stack">
        <div class="card flush"><h2>Your holdings — evidence by horizon <a class="right" href="#/portfolio" style="font-size:12.5px;font-weight:500">Portfolio →</a></h2><div id="holdEv"></div></div>
        <div class="card flush"><h2>Core markets <small>Shaffer Score (evidence, not a forecast) by horizon · click for the full research</small></h2><div id="coreEv"></div></div>
      </div><div class="stack">
        <div class="card"><h2>Regime <small>point-in-time labels</small></h2><div id="regBox"></div></div>
        <div class="card"><h2>What matters for the S&P 500 now</h2>${spyB ? matters(spyB.what_matters_now) : '<div class="empty">Research SPY first</div>'}</div>
        <div class="card"><h2>How to read this</h2><p class="muted" style="margin:0;font-size:13px">A score combines standardised signals weighted by how well each has predicted this asset at this horizon (information coefficient, shrunk when the evidence is thin, adjusted for the current regime). Confidence falls when the history holds few independent windows, when signals were unstable, or when the score has not worked out of sample. Nothing here is a forecast you should rely on without judgement.</p></div>
      </div></div>`;
    const evRows = (ids) => ids.map(id => ({ id, name: (byId[id] || {}).name || assetName(id), r: byId[id] || {} }));
    const evCols = [{ k: 'id', label: 'Asset', l: 1, f: x => `<b>${esc(x.id)}</b><span class="sub">${esc(x.name)}</span>` }, { k: 'price', label: 'Price', v: x => x.r.price, f: x => fmt.px(x.r.price) },
      { k: 'm1', label: '1M', v: x => x.r.m1, cls: x => sign(x.r.m1), f: x => fmt.spct(x.r.m1, 1) },
      ...['1W', '1M', '3M', '12M'].map(hh => ({ k: 's' + hh, label: 'Shaffer ' + hh, v: x => (x.r.scores || {})[hh], f: x => `<span class="${scoreCls((x.r.scores || {})[hh])}"><b>${scoreTxt((x.r.scores || {})[hh])}</b></span>`, cls: () => '' })),
      { k: 'ph', label: 'Best horizon', l: 1, v: x => x.r.primary_horizon, f: x => x.r.researched ? esc(x.r.primary_horizon || '—') : '<span class="faint">not researched</span>' }];
    table($('#holdEv'), evRows(held.map(p => p.asset_id)), evCols, { onRow: x => go('#/asset/' + encodeURIComponent(x.id)), empty: 'No holdings yet: add positions on the Portfolio page' });
    table($('#coreEv'), evRows(CORE.filter(id => byId[id])), evCols, { onRow: x => go('#/asset/' + encodeURIComponent(x.id)) });
    if (reg) $('#regBox').innerHTML = Object.entries(reg.labels).map(([d, l]) => `<div class="row" style="justify-content:space-between;padding:5px 0;border-top:1px solid var(--border)"><span class="muted">${esc(d)}</span><b>${esc(l)}</b></div>`).join('');
    $('#scanBtn').onclick = () => busy($('#scanBtn'), async () => { const ids = [...new Set([...held.map(p => p.asset_id), ...CORE])]; const j = await post('/fs2/scan', { assets: ids }); pollJob(j.id, () => route(), $('#scanJob')); });
  };

  // ---------------------------------------------------------------- markets
  pages.markets = async (main, args, alive) => {
    const cls = args[0] || pref.get('mkCls', 'ALL');
    const hz = pref.get('mkH', '3M');
    const rows = await api('/fs2/markets' + (cls !== 'ALL' ? '?class=' + cls : '')); if (!alive()) return;
    const all = await assets();
    const classes = [...new Set(all.map(a => a.asset_class))];
    const asof = rows.map(r => r.date).filter(Boolean).sort().pop();
    const scored = rows.filter(r => r.researched).length;
    main.innerHTML = `<div class="page-head"><div><h1>Markets</h1><p>${all.length} products. <b>Shaffer Score</b> = what the evidence currently says: a structured quantitative evidence score, not a forecast (out of sample it has not shown statistically reliable predictive power at any horizon). −100 evidence strongly negative · 0 none · +100 strongly positive. <b>ML</b> = whether machine learning has found a <i>verified</i> predictive edge (0 when it has not). Long/short scores are net of what each side costs; an expected return appears only where its calibration is statistically supported. Click a product for its scores at every horizon, then trade it with or without its Shaffer Hedge.</p>
      <p class="faint" style="font-size:12px;margin-top:4px">Prices to ${fmt.date(asof)} · ${scored} of ${rows.length} scored (research a class to score the rest) · scores recomputed after each data refresh</p></div>
      <div class="row"><button id="mkScan">Research ${cls === 'ALL' ? 'all' : esc(clsName(cls))}</button><button id="mkAdd">Add a symbol</button></div></div><div id="mkJob"></div>
      <div class="card"><div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:10px"><div class="chips">${['ALL', ...classes].map(c => `<button class="chip ${c === cls ? 'on' : ''}" data-c="${c}">${c === 'ALL' ? 'All' : esc(clsName(c))}</button>`).join('')}</div>
      <div class="seg" id="mkH">${['1D', '1W', '1M', '3M', '6M', '12M', '3Y', '5Y'].map(h => `<button data-h="${h}" class="${h === hz ? 'on' : ''}">${h}</button>`).join('')}</div></div></div>
      <div class="card flush" style="margin-top:16px"><div id="mkT"></div></div>`;
    $$('.chips .chip', main).forEach(b => b.onclick = () => { pref.set('mkCls', b.dataset.c); go('#/markets/' + b.dataset.c); });
    $$('#mkH button', main).forEach(b => b.onclick = () => { pref.set('mkH', b.dataset.h); route(); });
    let net = {};
    const agreeCls = a => /STRONG AGREEMENT/.test(a || '') ? 'pos' : /DISAGREEMENT/.test(a || '') ? 'neg' : '';
    const draw = () => table($('#mkT'), rows, [
      { k: 'id', label: 'Product', l: 1, f: r => `<b>${esc(r.id)}</b><span class="sub">${esc(r.name)}</span>` }, { k: 'asset_class', label: 'Class', l: 1, f: r => `<span class="pill">${esc(clsName(r.asset_class))}</span>` },
      { k: 'price', label: 'Price', f: r => fmt.px(r.price) }, { k: 'd1', label: 'Move 1D', cls: r => sign(r.d1), f: r => fmt.spct(r.d1, 1) }, { k: 'm1', label: '1M', cls: r => sign(r.m1), f: r => fmt.spct(r.m1, 1) },
      { k: 'ss', label: 'Shaffer ' + hz, v: r => (r.scores || {})[hz], f: r => r.researched ? `<b class="${scoreCls((r.scores || {})[hz])}" style="font-size:15px">${scoreTxt((r.scores || {})[hz])}</b>` : '<span class="faint">—</span>' },
      { k: 'cal', label: 'Calibrated', v: r => (r.calibrated || {})[hz], f: r => scoreTxt((r.calibrated || {})[hz]) },
      { k: 'ml', label: 'ML ' + hz, v: r => (r.ml || {})[hz], f: r => `<span class="${scoreCls((r.ml || {})[hz])}">${scoreTxt((r.ml || {})[hz])}</span>` },
      { k: 'ag', label: 'Agreement', l: 1, v: r => (r.agreement || {})[hz], f: r => (r.agreement || {})[hz] ? `<span class="pill ${agreeCls((r.agreement || {})[hz])}" style="font-size:10.5px">${esc((r.agreement || {})[hz])}</span>` : '<span class="faint">—</span>' },
      { k: 'conf', label: 'Confidence', v: r => (r.confidence || {})[hz], f: r => pct0((r.confidence || {})[hz]) },
      { k: 'nl', label: 'Net long / short', v: r => (net[r.id] || {}).long, f: r => { const n = net[r.id]; return n ? `<span class="${scoreCls(n.long)}">${scoreTxt(n.long)}</span> / <span class="${scoreCls(n.short)}">${n.short == null ? 'n/a' : scoreTxt(n.short)}</span>` : '<span class="faint">…</span>'; } },
      { k: 'primary_horizon', label: 'Best horizon', l: 1, f: r => r.researched ? esc(r.primary_horizon || '—') : '<span class="faint">—</span>' },
      { k: 'trade', label: '', nosort: 1, f: r => r.analysis_only ? `<span class="pill warn" style="font-size:10px;white-space:normal" title="${esc(r.analysis_only)}: its price jumps to the next contract at every roll; it cannot be bought or shorted">Analysis only</span>` : `<button class="small" data-trade="${esc(r.id)}">Trade</button>` },
    ], { sortKey: 'id', sortDir: 1, maxH: 760, onRow: r => productDrawer(r), after: el => $$('[data-trade]', el).forEach(b => b.onclick = e => { e.stopPropagation(); openTicket(b.dataset.trade, 'BUY'); }) });
    draw();
    api('/fs2/markets/net?h=' + hz).then(n => { if (!alive()) return; net = n || {}; draw(); }).catch(() => { });
    $('#mkScan').onclick = () => busy($('#mkScan'), async () => { const j = await post('/fs2/scan', { assets: rows.map(r => r.id) }); pollJob(j.id, () => route(), $('#mkJob')); });
    $('#mkAdd').onclick = () => { const m = modal(`<h2 style="margin-top:0">Add a symbol</h2><p class="muted">Any Yahoo Finance symbol: a stock (ASML, 7203.T), an ETF, an index (^VIX), a future (HG=F), a currency pair (USDSEK=X) or a coin (ADA-USD). Its full daily history is downloaded.</p><input id="symIn" placeholder="Symbol"><div id="symErr" class="neg"></div><div class="row" style="justify-content:flex-end;margin-top:12px"><button class="primary" id="symGo">Add</button></div>`);
      $('#symGo', m).onclick = () => busy($('#symGo', m), async () => { try { const a = await post('/fs2/assets', { symbol: $('#symIn', m).value }); S.assets = null; closeModal(); toast(`${a.id} added`); go('#/asset/' + encodeURIComponent(a.id)); } catch (e) { $('#symErr', m).textContent = e.message; } }); };
  };

  // ---------------------------------------------------------------- watchlist
  pages.watchlist = async (main, _, alive) => {
    const rows = await api('/fs2/watchlist'); if (!alive()) return;
    main.innerHTML = `<div class="page-head"><div><h1>Watchlist</h1><p>Assets you follow. Add from any asset's research page, or here.</p></div><div class="row">${assetPicker('wlPick', '')}</div></div><div class="card flush"><div id="wlT"></div></div>`;
    bindPicker('wlPick', id => busy(null, async () => { await post('/fs2/watchlist', { asset_id: id }); toast(`${id} added to the watchlist`); route(); }));
    table($('#wlT'), rows, [{ k: 'asset_id', label: 'Asset', l: 1, f: r => `<b>${esc(r.asset_id)}</b><span class="sub">${esc((r.asset || {}).name || '')}</span>` }, { k: 'p', label: 'Price', v: r => r.quote.price, f: r => fmt.px(r.quote.price) },
      { k: 'd1', label: '1D', v: r => r.quote.d1, cls: r => sign(r.quote.d1), f: r => fmt.spct(r.quote.d1, 1) }, { k: 'm1', label: '1M', v: r => r.quote.m1, cls: r => sign(r.quote.m1), f: r => fmt.spct(r.quote.m1, 1) },
      ...['1W', '1M', '3M', '6M', '12M'].map(hh => ({ k: 'q' + hh, label: 'Shaffer ' + hh, v: r => ((r.light || {}).scores || {})[hh], f: r => `<b class="${scoreCls(((r.light || {}).scores || {})[hh])}">${scoreTxt(((r.light || {}).scores || {})[hh])}</b><span class="sub">cal ${scoreTxt(((r.light || {}).calibrated || {})[hh])} · conf ${pct0(((r.light || {}).confidence || {})[hh])}</span>` })),
      { k: 'trade', label: '', nosort: 1, f: r => `<button class="small buy" data-tk="${esc(r.asset_id)}">Trade</button>` },
      { k: 'x', label: '', nosort: 1, f: r => `<button class="small ghost" data-un="${esc(r.asset_id)}">Remove</button>` }], { onRow: r => go('#/asset/' + encodeURIComponent(r.asset_id)), empty: 'Nothing on the watchlist yet' });
    $$('[data-un]').forEach(b => b.onclick = () => busy(b, async () => { await api('/fs2/watchlist/' + encodeURIComponent(b.dataset.un), { method: 'DELETE' }); route(); }));
    $$('[data-tk]').forEach(b => b.onclick = e => { e.stopPropagation(); openTicket(b.dataset.tk, 'BUY'); });
  };

  // ---------------------------------------------------------------- asset research: the one-page answer for an asset
  pages.asset = async (main, args, alive) => {
    const id = args[0] || S.current;
    main.innerHTML = `<div class="page-head"><div class="row">${assetPicker('arPick', id)}</div></div><div class="loading">Researching ${esc(id)}: computing ~60 signals, testing them at nine horizons, and scoring… (a few seconds the first time)</div>`;
    bindPicker('arPick', x => go('#/asset/' + encodeURIComponent(x)));
    const b = await api('/fs2/asset/' + encodeURIComponent(id)); if (!alive()) return;
    const a = b.asset; const hs = b.horizons; const ph = b.primary_horizon; const p = hs[ph] || {};
    const pos = b.position && Math.abs(b.position.quantity) > 1e-12 ? b.position : null;
    main.innerHTML = `<div class="page-head"><div><div class="muted">${esc(clsName(a.asset_class))} · ${esc(a.currency || '')}${a.sector ? ' · ' + esc(a.sector) : ''} · history since ${esc(b.history_start)} (${Math.round(b.sessions / 252)} years)</div>
        <h1>${esc(a.id)} <span class="muted" style="font-weight:500;font-size:18px">${esc(a.name)}</span></h1></div>
        <div class="row">${assetPicker('arPick', a.id)}${['COMMODITY', 'FUTURE'].includes(a.asset_class) ? '<span class="pill warn" title="Its price jumps to the next contract at every roll: it cannot be bought or shorted">ANALYSIS ONLY — CONTINUOUS FUTURES SERIES</span>' : '<button class="buy" id="arTrade">Trade</button>'}<button id="watchBtn">${b.watched ? '★ Watching' : '☆ Watch'}</button><a class="btn" href="#/analytics/${encodeURIComponent(a.id)}">Analytics</a><a class="btn" href="#/ml/forecasts/${encodeURIComponent(a.id)}">ML forecasts</a></div></div>
      <div class="tiles">${kpi('Price', `${fmt.px(b.price)} <span class="muted" style="font-size:13px">${esc(a.currency || '')}</span>`, `1D ${fmt.spct(b.change['1D'], 1)} · 1M ${fmt.spct(b.change['1M'], 1)} · 1Y ${fmt.spct(b.change['1Y'], 1)}`)}
        ${kpi('Shaffer Score', ph ? `<span class="${scoreCls(p.score)}">${scoreTxt(p.score)}</span> <span class="muted" style="font-size:14px">at ${ph}</span>` : '—', ph ? `calibrated ${scoreTxt(p.calibrated)} · ${esc((p.confidence || {}).label || '')} confidence ${pct0((p.confidence || {}).value)}` : 'no horizon has enough evidence')}
        ${kpi('Regime', `<span style="font-size:15px">${esc(b.regime.description)}</span>`, Object.values(b.regime.labels).slice(3).join(' · '))}
        ${kpi('Your exposure', pos ? fmt.qty(pos.quantity) : 'none', pos ? `cost ${fmt.money(pos.cost)} · realised ${fmt.signed(pos.realized)}` : '<a href="#/portfolio/transactions">add a position →</a>')}
        ${kpi('ML models', b.ml ? `<span style="font-size:15px">trained</span>` : '<span style="font-size:15px">not trained</span>', b.ml ? `${esc(b.ml.trained_at || '')}` : `<a href="#/ml/forecasts/${encodeURIComponent(a.id)}">train them →</a>`)}</div>
      <div class="card flush"><h2>Evidence by horizon <small>the same asset can be bearish short term and bullish long term</small></h2>${scoreStrip(hs)}</div>
      <div class="grid g3" style="margin-top:16px">
        <div class="card"><h2>What matters right now <small>1D–1M</small></h2>${matters(b.what_matters_now)}</div>
        <div class="card"><h2>What matters long term <small>12M+</small></h2>${matters(b.what_matters_long)}</div>
        <div class="card"><h2>Why: ${esc(ph || '3M')} Shaffer Score <small>family points add up to the score · <a href="#/analytics/${encodeURIComponent(a.id)}/shaffer">full breakdown →</a></small></h2>${familyBlock(hs[ph || '3M'])}</div>
      </div>
      <div class="grid g-main" style="margin-top:16px"><div class="card"><h2>Price <small>daily close</small></h2><div id="arChart"></div></div>
        <div class="card flush"><h2>Signals today <small>standardised value · best horizon · evidence</small></h2><div id="arSig"></div></div></div>`;
    bindPicker('arPick', x => go('#/asset/' + encodeURIComponent(x)));
    if ($('#arTrade')) $('#arTrade').onclick = () => openTicket(a.id, 'BUY');
    lineChart($('#arChart'), [{ name: a.id, data: b.price_history.values, area: true }], { labels: b.price_history.dates.map(d => fmt.date(d)), fmtY: v => fmt.px(v), h: 280 });
    table($('#arSig'), b.current.filter(c => c.value != null), [
      { k: 'label', label: 'Signal', l: 1, f: c => `<b>${esc(c.label)}</b><span class="sub">${esc(c.family)}</span>` }, { k: 'z', label: 'z', f: c => fmt.num(c.z, 2) },
      { k: 'signal', label: 'Reads', l: 1, v: c => c.signal, f: c => sigPill(c.signal) }, { k: 'best_horizon', label: 'Best', l: 1 },
      { k: 'usefulness', label: 'Usefulness', v: c => c.usefulness == null ? null : Math.abs(c.usefulness), f: c => fmt.num(c.usefulness, 2) }, { k: 'hit_rate', label: 'Hit', f: c => fmt.pct(c.hit_rate, 0) }], { sortKey: 'usefulness', maxH: 360, onRow: c => go(`#/analytics/${encodeURIComponent(a.id)}/overview`) });
    $('#watchBtn').onclick = () => busy($('#watchBtn'), async () => { if (b.watched) await api('/fs2/watchlist/' + encodeURIComponent(a.id), { method: 'DELETE' }); else await post('/fs2/watchlist', { asset_id: a.id }); route(); });
  };

  // ---------------------------------------------------------------- maths rendering (KaTeX when online, readable symbols offline)
  const GREEK = { alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ε', varepsilon: 'ε', zeta: 'ζ', eta: 'η', theta: 'θ', kappa: 'κ', lambda: 'λ', mu: 'μ', nu: 'ν', xi: 'ξ', pi: 'π', rho: 'ρ', sigma: 'σ', tau: 'τ', phi: 'φ', varphi: 'φ', chi: 'χ', psi: 'ψ', omega: 'ω', Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Sigma: 'Σ', Phi: 'Φ', Omega: 'Ω', Pi: 'Π',
    sum: 'Σ', prod: '∏', int: '∫', partial: '∂', nabla: '∇', infty: '∞', le: '≤', leq: '≤', ge: '≥', geq: '≥', approx: '≈', neq: '≠', cdot: '·', times: '×', odot: '⊙', sim: '∼', mid: '|', top: 'ᵀ', to: '→', in: '∈', pm: '±', quad: ' ', qquad: '  ', ldots: '…', dots: '…', ',': ' ', ';': ' ', '!': '', max: 'max', min: 'min', ln: 'ln', exp: 'exp', log: 'log', Var: 'Var', Cov: 'Cov' };
  function texLite(t) {
    let s = String(t);
    const grp = (str, i) => { if (str[i] !== '{') return [str[i] || '', i + 1]; let d = 0, j = i; for (; j < str.length; j++) { if (str[j] === '{') d++; else if (str[j] === '}') { d--; if (!d) break; } } return [str.slice(i + 1, j), j + 1]; };
    const walk = str => { let out = '', i = 0; while (i < str.length) { const c = str[i];
      if (c === '\\') { const m = /^\\([a-zA-Z]+|.)/.exec(str.slice(i)); const cmd = m ? m[1] : ''; i += (m ? m[0].length : 1);
        if (cmd === 'frac' || cmd === 'dfrac') { const [a, i1] = grp(str, i); const [b, i2] = grp(str, i1); i = i2; out += `(${walk(a)})/(${walk(b)})`; }
        else if (cmd === 'sqrt') { const [a, i1] = grp(str, i); i = i1; out += `√(${walk(a)})`; }
        else if (['text', 'mathrm', 'operatorname', 'mathbf', 'boldsymbol', 'mathcal', 'boxed', 'mathbb'].includes(cmd)) { const [a, i1] = grp(str, i); i = i1; out += cmd === 'mathbb' && a === '1' ? '𝟙' : walk(a); }
        else if (cmd === 'hat' || cmd === 'bar' || cmd === 'tilde') { const [a, i1] = grp(str, i); i = i1; out += walk(a) + ({ hat: '\u0302', bar: '\u0304', tilde: '\u0303' })[cmd]; }
        else if (cmd === 'left' || cmd === 'right' || cmd === 'big' || cmd === 'Big' || cmd === 'bigg') { }
        else if (cmd === '|') out += '‖';
        else out += GREEK[cmd] != null ? GREEK[cmd] : cmd; }
      else if (c === '^' || c === '_') { const [a, i1] = grp(str, i + 1); i = i1; out += `<${c === '^' ? 'sup' : 'sub'}>${walk(a)}</${c === '^' ? 'sup' : 'sub'}>`; }
      else if (c === '{' || c === '}') i++;
      else if (c === '&') { i++; } else { out += esc(c); i++; } } return out; };
    try { return walk(s); } catch (e) { return esc(t); }
  }
  const texFallback = root => root && $$('.tex[data-tex]', root).forEach(el => { if (!el.dataset.lite) { el.innerHTML = `<span style="font-family:'Cambria Math','STIX Two Math','Times New Roman',serif;font-size:16px">${texLite(el.dataset.tex)}</span>`; el.dataset.lite = 1; } });
  const renderTex = root => { if (!root || !document.body.contains(root)) return true; if (!window.katex) { texFallback(root); return false; } $$('.tex[data-tex]', root).forEach(el => { try { katex.render(el.dataset.tex, el, { displayMode: true, throwOnError: false }); } catch (e) { } }); return true; };
  const texWhenReady = root => { if (!renderTex(root)) { let tries = 0; const iv = setInterval(() => { if (renderTex(root) || ++tries > 20) clearInterval(iv); }, 300); } };

  // ---------------------------------------------------------------- the signal-horizon matrix (shared by Analytics and Quant Lab)
  const FAM_ORDER = ['Returns', 'Momentum', 'Statistics', 'Risk', 'Volatility', 'Technical', 'TimeSeries', 'Valuation', 'Fundamentals', 'Rates', 'Credit', 'Macro'];
  function matrixView(el, asset, mx, o = {}) {
    const metric = o.metric || 'normalized';
    let maxU = 0; Object.values(mx.matrix).forEach(r => Object.values(r).forEach(x => { if (x.usefulness != null) maxU = Math.max(maxU, Math.abs(x.usefulness)); }));
    const rows = Object.keys(mx.matrix).map(k => ({ key: k, label: (mx.features[k] || {}).label || k, sub: (mx.features[k] || {}).family, fam: (mx.features[k] || {}).family }))
      .sort((a, b) => (FAM_ORDER.indexOf(a.fam) - FAM_ORDER.indexOf(b.fam)) || a.label.localeCompare(b.label));
    heatmap(el, rows, HZ.map(h => ({ key: h, label: h })), (r, c) => { const rec = (mx.matrix[r.key] || {})[c.key]; if (!rec) return { v: null, text: '<span class="faint">·</span>', title: 'insufficient evidence' };
      const v = metric === 'normalized' ? (rec.usefulness == null ? null : rec.usefulness / (maxU || 1)) : rec[metric]; const sig = rec.q != null && rec.q < 0.1;
      return { v, text: v == null ? '<span class="faint">n/a</span>' : `${fmt.num(v, 2)}${sig ? '<sup>*</sup>' : ''}`, title: `IC ${fmt.num(rec.ic, 3)} · hit ${fmt.pct(rec.hit_rate, 0)} · p ${fmt.num(rec.p, 3)} · q ${fmt.num(rec.q, 3)} · n_eff ${fmt.num(rec.n_eff, 0)}` }; },
      { max: metric === 'ic' ? 0.3 : metric === 'hit_rate' ? 1 : metric === 'normalized' ? 1 : 0.2, corner: 'Signal', maxH: o.maxH || 760, onCell: (sig, h) => cellDrawer(asset, sig, h) });
  }
  async function cellDrawer(asset, sig, h) {
    const d = drawer('<div class="loading">Loading the evidence…</div>');
    const c = await api(`/fs2/asset/${encodeURIComponent(asset)}/cell/${encodeURIComponent(sig)}/${h}`);
    const r = c.record; const m = c.backtest.metrics;
    d.innerHTML = `<div class="row" style="justify-content:flex-end"><button class="ghost" id="drawerX">✕ Close</button></div><div class="muted">${esc(c.family)} · ${esc(asset)} · ${esc(h)} horizon</div><h2 style="margin:2px 0 12px">${esc(c.label)}</h2>
      <div class="kv"><span>Information coefficient (rank)</span><span><b>${fmt.num(r.ic, 3)}</b> <span class="faint">recent third ${fmt.num(r.ic_recent, 3)}</span></span><span>Usefulness (IC shrunk by evidence)</span><span>${fmt.num(r.usefulness, 3)}</span>
        <span>Hit rate when the signal leans</span><span>${fmt.pct(r.hit_rate, 1)}</span><span>t-statistic · p-value</span><span>${fmt.num(r.t, 2)} · ${fmt.num(r.p, 4)}</span><span>False-discovery q-value</span><span>${fmt.num(r.q, 4)} <span class="faint">(${(r.q != null && r.q < 0.1) ? 'survives testing ~60 signals' : 'may be luck among many tests'})</span></span>
        <span>Observations · independent</span><span>${fmt.qty(r.n)} · ${fmt.num(r.n_eff, 0)} <span class="faint">(signal persistence ${fmt.num(r.persistence, 0)} sessions)</span></span><span>Direction</span><span>${r.direction > 0 ? 'higher signal → higher return' : 'higher signal → lower return'}</span>
        <span>Stability (thirds agreeing)</span><span>${fmt.pct(r.stability, 0)} · ${(r.ic_thirds || []).map(x => fmt.num(x, 2)).join(' / ')}</span></div>
      <h3>Forward ${esc(h)} return by signal quintile</h3><div id="cQ"></div>
      <h3>Rolling 3-year IC</h3><div id="cR"></div>
      <h3>By regime</h3><div id="cRg"></div>
      <h3>Threshold backtest <small class="muted">long when z ≥ 1 in the signal's direction, exit at 0, hold ≥ ${esc(h)}, 5bp costs</small></h3>
      <div class="kv"><span>CAGR vs buy & hold</span><span>${fmt.pct(m.cagr, 1)} vs ${fmt.pct(m.benchmark_cagr, 1)}</span><span>Sharpe · max drawdown</span><span>${fmt.num(m.sharpe, 2)} · ${fmt.pct(m.max_drawdown, 1)}</span><span>Trades · win rate · exposure</span><span>${m.trades} · ${fmt.pct(m.win_rate, 0)} · ${fmt.pct(m.exposure, 0)}</span></div><div id="cB"></div>`;
    $('#drawerX').onclick = closeDrawer;
    hbars($('#cQ'), (c.quintiles || []).map(q => ({ label: `Q${q.quintile} ${q.quintile === 1 ? '(lowest)' : q.quintile === 5 ? '(highest)' : ''}`, value: q.mean, sub: `hit ${fmt.pct(q.hit, 0)}` })), { fmt: v => fmt.spct(v, 2) });
    lineChart($('#cR'), [{ name: 'IC', data: c.rolling_ic.map(x => x.ic), color: css('--accent') }], { labels: c.rolling_ic.map(x => fmt.date(x.date)), zero: true, fmtY: v => fmt.num(v, 2), h: 150 });
    const by = r.by_regime || {};
    $('#cRg').innerHTML = Object.keys(by).length ? `<table><thead><tr><th class="l">Regime</th><th>IC</th><th>Usefulness</th><th>n_eff</th></tr></thead><tbody><tr><td class="l"><b>All periods</b></td><td>${fmt.num(r.ic, 3)}</td><td>${fmt.num(r.usefulness, 3)}</td><td>${fmt.num(r.n_eff, 0)}</td></tr>${Object.entries(by).sort().map(([k, v]) => `<tr><td class="l">${esc(c.regime_labels[k] || k)}</td><td style="background:${divColor(v.ic, 0.3)}">${fmt.num(v.ic, 3)}</td><td>${fmt.num(v.usefulness, 3)}</td><td>${fmt.num(v.n_eff, 0)}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">Regime splits are computed for horizons up to 12 months</div>';
    lineChart($('#cB'), [{ name: 'Strategy', data: c.backtest.curve.strategy, color: css('--accent') }, { name: 'Buy & hold', data: c.backtest.curve.buy_hold, color: css('--faint'), width: 1.4 }], { labels: c.backtest.curve.dates.map(d => fmt.date(d)), fmtY: v => fmt.num(v, 2), h: 170 });
  }

  // ---------------------------------------------------------------- analytics
  const A_TABS = [['overview', 'Overview'], ['shaffer', 'Shaffer Score'], ['Returns', 'Returns'], ['Risk', 'Risk'], ['Statistics', 'Statistics'], ['Regression', 'Regression'], ['Time Series', 'Time series'], ['Volatility', 'Volatility'], ['factors', 'Factors & regimes'], ['Valuation', 'Valuation'], ['Stochastic', 'Stochastic'], ['Fixed Income', 'Fixed income'], ['Portfolio', 'Portfolio theory'], ['hedge', 'Shaffer Hedge'], ['ml', 'Machine learning'], ['backtests', 'Backtests'], ['equations', 'Equations']];
  async function shafferTab(body, b, id, alive) {
    // Shaffer Score v2: one point-in-time function for every date; the live score is the last record of its history
    body.innerHTML = '<div class="loading">Loading the Shaffer Score record…</div>';
    const [sh, st] = await Promise.all([api(`/fs2/asset/${encodeURIComponent(id)}/shaffer`), api('/fs2/shaffer').catch(() => ({}))]); if (!alive()) return;
    const hsv = sh.horizons || {}; const labs = HZ.filter(x => hsv[x]);
    let h = pref.get('ssH', b.primary_horizon && (hsv[b.primary_horizon] || {}).raw != null ? b.primary_horizon : '3M'); if (!hsv[h] || hsv[h].raw == null) h = labs.find(x => hsv[x].raw != null) || '3M';
    const perf = sh.performance || {};
    const defs = [['s', 'standardised reading, −1..+1: δ · clip(z ÷ 2); z = the signal\'s expanding z-score (only past values), δ = the direction it is used in'],
      ['w', 'historical weight = |predictive strength| × (¼ + ¾ · stability); predictive strength = the median of three IC estimates (Pearson IC, the IC implied by the hit rate, the IC implied by the conditional-return spread)'],
      ['ω', 'w shared within the family after a correlation penalty (w ÷ Σ ρ² with the family\'s other active signals): the same idea said twice counts once'],
      ['c', 'confidence = √(n_eff ÷ 100, ≤ 1) × CI strength × (1 − ½ q) × data quality'],
      ['r', 'regime adjustment 0.5..1.5: how the signal has done in today\'s regimes, each shrunk by n ÷ (n + 50)'],
      ['d', 'decay 0.1..1: the last 3 years\' and last year\'s IC against the full history; never flips a sign'],
      ['W', 'family weight = family evidence × V, V = the family score\'s own out-of-sample record (1 until it has one)'],
      ['A · H', 'applicability of the family to this asset class and to this horizon (economic priors)'],
      ['K', `${fmt.num(((st.constants || {}).KAPPA), 2)} × Σ A·H over the families present: a fixed scale set before looking at results`]];
    const fmtIC = (x, t) => x == null ? '—' : `${fmt.num(x, 3)}${t != null ? ` <span class="faint">t ${fmt.num(t, 1)}</span>` : ''}`;
    body.innerHTML = `<div class="card"><h2>Shaffer Score <span class="pill pos">v${esc(sh.version || '')}</span> <small>a structured quantitative evidence score — what the evidence currently says, not a forecast · point in time: every score, historical or today, is the same function using only data available on its date · ${fmt.num(sh.history_years, 0)} years of price history</small></h2>
        <div class="tex" data-tex="${esc(sh.formula || '')}"></div>
        <div class="tbl-wrap" style="margin-top:8px"><table><thead><tr><th class="l">Horizon</th><th>Shaffer</th><th>Calibrated</th><th>Expected</th><th>Typical range</th><th>Confidence</th><th>Evidence</th><th>Indep. obs.</th><th>OOS IC</th><th>Hit rate</th><th>Record since</th></tr></thead><tbody>
          ${labs.map(x => { const r = hsv[x]; const pf = perf[x] || {}; return `<tr data-h="${x}" style="cursor:pointer;${x === h ? 'background:var(--surface-2)' : ''}"><td class="l"><b>${x}</b></td>
            <td style="background:${divColor(r.raw, 100)}"><b>${scoreTxt(r.raw)}</b></td><td class="${scoreCls(r.calibrated)}">${scoreTxt(r.calibrated)}</td>
            <td class="${sign(r.expected)}">${r.expected == null ? '<span class="faint" title="not supported by the calibration yet">—</span>' : fmt.spct(r.expected, 1)}</td>
            <td>${r.range ? `${fmt.spct(r.range[0], 0)} … ${fmt.spct(r.range[1], 0)}` : '—'}</td><td>${r.confidence == null ? '—' : pct0(r.confidence) + ' ' + esc(r.confidence_label || '')}</td>
            <td>${esc(r.evidence || '—')}</td><td>${fmt.num(r.n_eff, 0)}</td><td>${fmtIC(pf.ic, pf.t)}</td><td>${pf.hit == null ? '—' : fmt.pct(pf.hit, 0)}</td><td>${pf.first ? fmt.date(pf.first) : (r.reason ? `<span class="faint">${esc(r.reason)}</span>` : '—')}</td></tr>`; }).join('')}
        </tbody></table></div></div>
      <div id="ssH" style="margin-top:16px"></div>`;
    texWhenReady(body);
    $$('[data-h]', body).forEach(tr => tr.onclick = () => { pref.set('ssH', tr.dataset.h); shafferTab(body, b, id, alive); });
    const r = hsv[h] || {}; const el = $('#ssH');
    if (r.raw == null) { el.innerHTML = `<div class="card"><div class="empty">${esc(r.reason || 'No score at this horizon')}</div></div>`; return; }
    const cal = (sh.calibration || {})[h] || {}; const pf = perf[h] || {}; const hist = (sh.history || {})[h] || {}; const ev = (sh.evidence || {})[h] || {}; const val = (sh.validation || {})[h] || {};
    const regime = Object.values((b.regime || {}).labels || {}).join(' · ');
    el.innerHTML = `<div class="tiles">${kpi(`Shaffer Score · ${esc(h)}`, `<span class="${scoreCls(r.raw)}">${scoreTxt(r.raw)}</span>`, `calibrated ${scoreTxt(r.calibrated)} · ${fmt.num(r.numerator, 4)} ÷ K ${fmt.num(r.K, 3)}`)}
        ${kpi('Expected return', r.expected == null ? '—' : `<span class="${sign(r.expected)}">${fmt.spct(r.expected, 1)}</span>`, r.expected == null ? 'the calibration does not support an estimate yet' : `= typical ${fmt.spct(r.expected_typical, 1)} + evidence <span class="${sign(r.expected_edge)}">${fmt.spct(r.expected_edge, 1)}</span> (the calibrated score is the evidence part)${r.range ? ` · typical range ${fmt.spct(r.range[0], 1)} … ${fmt.spct(r.range[1], 1)}` : ''}`)}
        ${kpi('Confidence', r.confidence == null ? '—' : pct0(r.confidence), `${esc(r.confidence_label || '')} · evidence ${esc(r.evidence || '—')} · ${fmt.num(r.n_eff, 0)} independent observations`)}
        ${kpi('Current regime', `<span style="font-size:14px">${esc(regime || '—')}</span>`, 'as of ' + esc(r.date || ''))}</div>
      <div class="grid g2" style="margin-top:16px"><div class="card"><h2>Family breakdown <small>points add up to the score · click for the signals</small></h2><div id="ssFam"></div></div>
        <div class="card"><h2>Signals doing the work</h2>${familyBlock({ score: r.raw, families: [], contributors: r.contributors, contradicting: r.contradicting })}</div></div>
      <div class="card flush" style="margin-top:16px"><h2 id="ssSigH">Signals</h2><div id="ssSig"></div></div>
      <div class="card" style="margin-top:16px"><h2>Calibration <small>what each score range has been followed by, out of sample (overlapping ${esc(h)} windows) · OOS IC ${fmtIC(cal.ic, cal.t)} · monotonicity ${fmt.num(cal.monotonicity, 2)} (1 = higher score, higher return)</small></h2><div id="ssCal"></div></div>
      <div class="card" style="margin-top:16px"><h2>The record <small>the ${esc(h)} score as it read on each date (weekly), point in time</small></h2><div id="ssHist"></div></div>
      <div class="grid g2" style="margin-top:16px"><div class="card flush"><h2>By regime <small>out-of-sample IC of the score when each regime held</small></h2><div id="ssReg"></div></div>
        <div class="card flush"><h2>Family validation <small>each family score's own out-of-sample record → V</small></h2><div id="ssVal"></div></div></div>
      ${r.shadow ? `<div class="card flush" style="margin-top:16px"><h2>Candidate families <small>in shadow — computed point in time but <b>not counted</b>: a family enters the score only after it adds information out of sample (SHAFFER_AUDIT.md §27) · with all candidate families the score would read ${scoreTxt(r.shadow.all)}</small></h2><div id="ssShadow"></div></div>` : ''}
      <div class="card" style="margin-top:16px"><h2>What the symbols mean</h2><div class="stack" style="gap:6px">${defs.map(([k, v]) => `<div class="row" style="flex-wrap:nowrap;align-items:flex-start"><b style="width:44px;font-family:'Cambria Math',serif;font-size:15px">${k}</b><span class="muted" style="flex:1">${esc(v)}</span></div>`).join('')}</div></div>
      ${sh.custom ? `<div class="card" style="margin-top:16px"><h2>Custom score (live only) <small>${esc(sh.custom.source || '')}</small></h2>${sh.custom.error ? `<p class="neg">${esc(sh.custom.error)}</p>` : `<p>${HZ.map(x => `${x} <b>${scoreTxt((sh.custom.horizons || {})[x])}</b>`).join(' · ')}</p>`}</div>` : ''}`;
    if (r.shadow) table($('#ssShadow'), r.shadow.families || [], [{ k: 'family', label: 'Family', l: 1, f: x => `<b>${esc(x.family)}</b><span class="sub">${esc((x.signals || []).map(y => y.label || y.signal).join(' · '))}</span>` },
      { k: 'score', label: 'Family score', cls: x => sign(x.score), f: x => fmt.num(x.score, 3) }, { k: 'W', label: 'W', f: x => fmt.num(x.W, 2) },
      { k: 'n_active', label: 'Active signals', f: x => `${x.n_active}/${(x.signals || []).length}` },
      { k: 'with', label: 'Score if added', v: x => (r.shadow.with || {})[x.family], f: x => `${scoreTxt((r.shadow.with || {})[x.family])} <span class="faint">(now ${scoreTxt(r.raw)})</span>` }], { sortKey: null });
    const fams = r.families || [];
    hbars($('#ssFam'), fams.filter(f => Math.abs(f.points) >= 0.05).map(f => ({ label: f.family, value: f.points, sub: `W ${fmt.num(f.W, 2)} · V ${fmt.num(f.V, 2)} · A ${fmt.num(f.A, 2)} · H ${fmt.num(f.H, 2)} · ${f.n_active}/${f.n_signals} signals` })), { fmt: v => (v > 0 ? '+' : '') + fmt.num(v, 1), empty: 'No family has usable evidence' });
    const sigRows = r.signals || [];
    const showSigs = fam => { $('#ssSigH').innerHTML = `${esc(fam || 'All')} signals at ${esc(h)} <small>muted signals show why</small>`;
      table($('#ssSig'), sigRows.filter(x => !fam || x.family === fam), [{ k: 'label', label: 'Signal', l: 1, f: x => `<b>${esc(x.label || x.signal)}</b><span class="sub">${esc(x.family)}</span>` },
        { k: 'points', label: 'Points', cls: x => sign(x.points), v: x => Math.abs(x.points || 0), f: x => x.s == null ? '—' : `<b>${fmt.num(x.points, 2)}</b>` }, { k: 'z', label: 'z', f: x => fmt.num(x.z, 2) },
        { k: 's', label: 's', f: x => fmt.num(x.s, 2) }, { k: 'omega', label: 'ω', f: x => fmt.num(x.omega, 2) }, { k: 'c', label: 'c', f: x => fmt.num(x.c, 2) }, { k: 'r', label: 'r', f: x => fmt.num(x.r, 2) }, { k: 'd', label: 'd', f: x => fmt.num(x.d, 2) },
        { k: 'ic', label: 'IC', f: x => fmt.num(x.ic, 3) }, { k: 'q', label: 'q', f: x => fmt.num(x.q, 2) }, { k: 'n_eff', label: 'n_eff', f: x => fmt.num(x.n_eff, 0) },
        { k: 'decay', label: 'Decay', l: 1, f: x => x.decay ? `<span class="pill ${x.decay === 'HEALTHY' ? 'pos' : x.decay === 'DECAYING' ? 'neg' : x.decay === 'WEAKENING' ? 'warn' : ''}">${esc(x.decay.toLowerCase())}</span>` : '' },
        { k: 'status', label: 'Status', l: 1, f: x => `<span class="faint">${esc(x.status || '')}</span>` }], { sortKey: 'points', sortDir: -1, maxH: 520, onRow: x => cellDrawer(id, x.signal, h) }); };
    showSigs(fams.length ? fams[0].family : null);
    const shown = fams.filter(f => Math.abs(f.points) >= 0.05);
    [...(($('#ssFam').firstElementChild || {}).children || [])].forEach((row, k) => { row.style.cursor = 'pointer'; row.onclick = () => showSigs(shown[k].family); });
    table($('#ssCal'), cal.bins || [], [{ k: 'lo', label: 'Score range', l: 1, f: x => `${scoreTxt(x.lo)} … ${scoreTxt(x.hi)}` }, { k: 'n', label: 'Scores' }, { k: 'n_eff', label: 'Indep. obs.', f: x => fmt.num(x.n_eff, 0) },
      { k: 'mean_ret', label: 'Average return', cls: x => sign(x.mean_ret), f: x => fmt.spct(Math.exp(x.mean_ret) - 1, 1) }, { k: 'median_ret', label: 'Median', f: x => fmt.spct(Math.exp(x.median_ret) - 1, 1) },
      { k: 'hit', label: 'Up share', f: x => fmt.pct(x.hit, 0) }, { k: 'q10', label: '10% … 90%', f: x => `${fmt.spct(Math.exp(x.q10) - 1, 0)} … ${fmt.spct(Math.exp(x.q90) - 1, 0)}` },
      { k: 'edge', label: 'Calibrated edge', cls: x => sign(x.edge), f: x => fmt.num(x.edge, 3) + ' σ' }], { sortKey: null, empty: cal.usable === false ? `Not enough matured history to calibrate yet (${fmt.num(cal.n_eff, 0)} independent observations)` : 'No calibration' });
    if ((hist.chart || {}).dates) lineChart($('#ssHist'), [{ name: 'Shaffer Score', data: hist.chart.values, color: css('--accent') }], { labels: hist.chart.dates.map(d => fmt.date(d)), zero: true, fmtY: v => fmt.num(v, 0), h: 240 });
    else $('#ssHist').innerHTML = '<div class="empty">No history yet</div>';
    table($('#ssReg'), Object.entries(pf.by_regime || {}).map(([k, v]) => ({ k, ...v })), [{ k: 'k', label: 'Regime', l: 1, f: x => esc(x.k.replace(/_/g, ' ')) }, { k: 'ic', label: 'OOS IC', f: x => fmt.num(x.ic, 3) }, { k: 'hit', label: 'Hit rate', f: x => x.hit == null ? '—' : fmt.pct(x.hit, 0) }, { k: 'n_eff', label: 'Indep. obs.', f: x => fmt.num(x.n_eff, 0) }], { sortKey: null, empty: 'Not enough out-of-sample history' });
    const shadowNames = new Set(((r.shadow || {}).families || []).map(x => x.family));
    table($('#ssVal'), Object.entries(val).map(([k, v]) => ({ k, ...v })), [{ k: 'k', label: 'Family', l: 1, f: x => shadowNames.has(x.k) ? `${esc(x.k)} <span class="pill" style="font-size:10px">candidate · not counted</span>` : esc(x.k) }, { k: 'V', label: 'V', f: x => fmt.num(x.V, 2) }, { k: 'ic', label: 'OOS IC', f: x => fmt.num(x.ic, 3) }, { k: 't', label: 't', f: x => fmt.num(x.t, 1) }, { k: 'state', label: 'State', l: 1, f: x => `<span class="faint">${esc(x.state || '')}</span>` }], { sortKey: 'V', sortDir: 1 });
  }

  pages.analytics = async (main, args, alive) => {
    const id = args[0] || S.current; const tab = args[1] || 'overview';
    main.innerHTML = `<div class="loading">Loading the analytics for ${esc(id)}…</div>`;
    const b = await api('/fs2/asset/' + encodeURIComponent(id)); if (!alive()) return;
    const ph = b.primary_horizon; const p = b.horizons[ph] || {}; const ml = b.ml && b.ml.horizons ? b.ml.horizons[ph] || {} : {};
    main.innerHTML = `<div class="card" style="margin-bottom:14px"><div class="row" style="gap:18px">${assetPicker('anPick', id)}
        <div class="hdrk"><span>Date</span><b>${esc(b.as_of)}</b></div><div class="hdrk"><span>Price</span><b>${fmt.px(b.price)}</b></div>
        <div class="hdrk"><span>Shaffer Score (${esc(ph || '—')})</span><b class="${scoreCls(p.score)}">${scoreTxt(p.score)}</b></div><div class="hdrk"><span>Calibrated</span><b class="${scoreCls(p.calibrated)}">${scoreTxt(p.calibrated)}</b></div><div class="hdrk"><span>ML score</span><b class="${scoreCls(p.ml_score)}">${scoreTxt(p.ml_score)}</b></div>
        <div class="hdrk"><span>Confidence</span><b>${pct0((p.confidence || {}).value)}</b></div><div class="hdrk"><span>Regime</span><b>${esc(b.regime.description)}</b></div>
        <div class="hdrk"><span>Best model</span><b>${esc(ml.best_model || (b.ml ? 'none beats noise' : 'not trained'))}</b></div><div class="hdrk"><span>Best horizon</span><b>${esc(ph || '—')}</b></div></div></div>
      <div class="tabs">${A_TABS.map(([k, l]) => `<a class="tab ${k === tab ? 'on' : ''}" href="#/analytics/${encodeURIComponent(id)}/${encodeURIComponent(k)}">${l}</a>`).join('')}</div><div id="anBody" style="margin-top:14px"><div class="loading">Computing…</div></div>`;
    bindPicker('anPick', x => go(`#/analytics/${encodeURIComponent(x)}/${encodeURIComponent(tab)}`));
    const body = $('#anBody');
    if (tab === 'overview') {
      const mx = await api(`/fs2/asset/${encodeURIComponent(id)}/matrix`); if (!alive()) return;
      body.innerHTML = `<div class="card"><h2>Signal-horizon matrix <small>rows = signals, columns = horizons · normalised predictive usefulness (rank IC shrunk by its evidence, 1.0 = this asset's strongest) · green = higher signal, higher returns · * survives false-discovery control · click a cell</small><span class="right seg" id="mxM"><button data-m="normalized" class="on">Normalised</button><button data-m="usefulness">Usefulness</button><button data-m="ic">IC</button></span></h2><div id="mx"></div></div>
        <div class="card flush" style="margin-top:16px"><h2>Evidence by horizon</h2>${scoreStrip(b.horizons)}</div>`;
      const draw = m => matrixView($('#mx'), id, mx, { metric: m });
      draw('normalized');
      $$('#mxM button').forEach(bt => bt.onclick = () => { $$('#mxM button').forEach(x => x.classList.toggle('on', x === bt)); draw(bt.dataset.m); });
      return;
    }
    if (tab === 'factors') {
      const mx = await api(`/fs2/asset/${encodeURIComponent(id)}/matrix`); if (!alive()) return;
      const hsel = pref.get('regH', '3M');
      body.innerHTML = `<div class="card"><h2>Regime analysis <small>usefulness of each signal inside each regime at the chosen horizon · the same signal can work in bull markets and fail in bear markets</small><span class="right"><select id="regH">${HZ.slice(0, 6).map(h => `<option ${h === hsel ? 'selected' : ''}>${h}</option>`).join('')}</select></span></h2><div id="rg"></div></div>
        <div class="grid g2" style="margin-top:16px"><div class="card"><h2>What matters now</h2>${matters(b.what_matters_now)}</div><div class="card"><h2>What matters long term</h2>${matters(b.what_matters_long)}</div></div>`;
      const draw = h => { const states = Object.keys(mx.regimes); const rows = Object.keys(mx.matrix).filter(k => (mx.matrix[k][h] || {}).by_regime).map(k => ({ key: k, label: (mx.features[k] || {}).label || k, sub: (mx.features[k] || {}).family }));
        const cols = [{ key: '_all', label: 'All' }, ...states.map(s => ({ key: s, label: mx.regimes[s] }))];
        heatmap($('#rg'), rows, cols, (r, c) => { const rec = mx.matrix[r.key][h]; const v = c.key === '_all' ? rec.usefulness : ((rec.by_regime || {})[c.key] || {}).usefulness; return { v, text: v == null ? '' : fmt.num(v, 2), title: c.key === '_all' ? '' : `n_eff ${fmt.num(((rec.by_regime || {})[c.key] || {}).n_eff, 0)}` }; }, { max: 0.2, corner: 'Signal', onCell: (sig) => cellDrawer(id, sig, h) }); };
      draw(hsel); $('#regH').onchange = e => { pref.set('regH', e.target.value); draw(e.target.value); };
      return;
    }
    if (tab === 'shaffer') { shafferTab(body, b, id, alive); return; }
    if (tab === 'hedge') {
      const H = pref.get('anHH', ph && HEDGE_H.includes(ph) ? ph : '3M'), amt = pref.get('anHA', 100000), lam = pref.get('anHL', 1);
      body.innerHTML = `<div class="card"><div class="row" style="gap:12px;flex-wrap:wrap;align-items:flex-end"><div><h2 style="margin:0">What the hedge engine sees in ${esc(id)}</h2><p class="muted" style="margin:4px 0 0">A hypothetical long position — its risks, every hedge candidate and how different hedge designs trade protection against expected profit.</p></div>
        <label class="f">Position ($)<input id="anHA" type="number" value="${amt}" style="width:120px"></label><label class="f">Horizon<select id="anHH">${HEDGE_H.map(x => `<option ${x === H ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
        <div><div class="muted" style="font-size:12px;margin-bottom:4px">λ (weight on profit given up)</div><div class="seg" id="anHL">${[0.5, 1, 2, 5, 10].map(l => `<button data-l="${l}" class="${l === lam ? 'on' : ''}">${l}</button>`).join('')}</div></div></div></div>
        <div class="card" style="margin-top:14px"><h2 style="margin-top:0">Hedge designs <small>protection against profit given up</small></h2><div id="anHD"><div class="loading">Simulating every design over ten years of real market moves…</div></div></div>
        <div class="card" style="margin-top:14px"><h2 style="margin-top:0">Risks and hedge candidates <small>the engine's view for the main risk</small></h2><div id="anHP"><div class="loading">Measuring the risk…</div></div></div>`;
      $('#anHA').onchange = e => { pref.set('anHA', N(e.target.value) || 100000); route(); };
      $('#anHH').onchange = e => { pref.set('anHH', e.target.value); route(); };
      const q = amt / (b.price || 1);
      let dres = null;
      $$('#anHL button').forEach(x => x.onclick = () => { pref.set('anHL', +x.dataset.l); $$('#anHL button').forEach(y => y.classList.toggle('on', y === x)); if (dres) designsTable($('#anHD'), dres, +x.dataset.l, null, null); });
      post('/fs2/hedge/designs', { asset_id: id, quantity: q, horizon: H }).then(d => { if (!alive()) return; dres = d; designsTable($('#anHD'), d, pref.get('anHL', 1), null, null); }).catch(e => { if (alive()) $('#anHD').innerHTML = `<p class="muted">${esc(e.message)}</p>`; });
      post('/fs2/hedge/analyze', { positions: [{ id, quantity: q }], objective: 'auto', params: { horizon: H, reduction: 1 }, nav: amt }).then(a => { if (!alive()) return; hedgePanel($('#anHP'), a, { scenBefore: 'Position alone', bookLabels: { before: 'Position alone', raw: 'Position + raw hedge', final: 'Position + final hedge' } }); }).catch(e => { if (alive()) $('#anHP').innerHTML = `<p class="muted">${esc(e.message)}</p>`; });
      return;
    }
    if (tab === 'ml') {
      body.innerHTML = b.ml ? `<div class="card flush"><h2>ML score by horizon <a class="right btn small" href="#/ml/forecasts/${encodeURIComponent(id)}">Independent ML forecasts →</a></h2><div id="mlT"></div></div>` : `<div class="card"><h2>No models trained for ${esc(id)} yet</h2><p class="muted">Training runs a purged walk-forward test of seven models at each horizon (about half a minute).</p><a class="btn primary" href="#/ml/forecasts/${encodeURIComponent(id)}">Train them</a></div>`;
      if (b.ml) table($('#mlT'), HZ.map(h => ({ h, r: b.ml.horizons[h] || {} })), [{ k: 'h', label: 'Horizon', l: 1 }, { k: 's', label: 'ML score', v: x => x.r.score, f: x => `<b class="${scoreCls(x.r.score)}">${scoreTxt(x.r.score)}</b>` }, { k: 'e', label: 'Expected', v: x => x.r.expected, f: x => x.r.expected == null ? '—' : `${fmt.spct(x.r.expected, 1)} <span class="faint">± ${fmt.pct(x.r.error, 1)}</span>` }, { k: 'ic', label: 'Out-of-sample IC', v: x => (x.r.ensemble || {}).ic, f: x => fmt.num((x.r.ensemble || {}).ic, 3) }, { k: 'pu', label: 'P(rise)', v: x => x.r.prob_up, f: x => fmt.pct(x.r.prob_up, 0) }, { k: 'b', label: 'Best model', l: 1, v: x => x.r.best_model, f: x => esc(x.r.best_model || x.r.message || x.r.reason || '—') }], { sortKey: null });
      return;
    }
    if (tab === 'backtests') {
      const mx = await api(`/fs2/asset/${encodeURIComponent(id)}/leaderboard?horizon=${ph || '3M'}&top=6`); if (!alive()) return;
      body.innerHTML = `<div class="card"><h2>The ${esc(ph || '3M')} leaders, backtested <small>threshold strategy on each of the strongest signals · <a href="#/backtests">build your own →</a></small></h2><div id="btL"></div></div>`;
      const rows = [];
      for (const r of mx) { try { const c = await api(`/fs2/asset/${encodeURIComponent(id)}/cell/${encodeURIComponent(r.signal)}/${ph || '3M'}`); rows.push({ ...r, m: c.backtest.metrics }); } catch (e) { } if (!alive()) return; }
      table($('#btL'), rows, [{ k: 'signal', label: 'Signal', l: 1 }, { k: 'u', label: 'Usefulness', v: r => r.usefulness, f: r => fmt.num(r.usefulness, 3) }, { k: 'cagr', label: 'CAGR', v: r => r.m.cagr, f: r => fmt.pct(r.m.cagr, 1) }, { k: 'bh', label: 'Buy & hold (same asset)', v: r => r.m.benchmark_cagr, f: r => fmt.pct(r.m.benchmark_cagr, 1) }, { k: 'sh', label: 'Sharpe', v: r => r.m.sharpe, f: r => fmt.num(r.m.sharpe, 2) }, { k: 'dd', label: 'Max DD', v: r => r.m.max_drawdown, f: r => fmt.pct(r.m.max_drawdown, 1) }, { k: 'tr', label: 'Trades', v: r => r.m.trades, f: r => r.m.trades }, { k: 'wr', label: 'Win rate', v: r => r.m.win_rate, f: r => fmt.pct(r.m.win_rate, 0) }], { onRow: r => cellDrawer(id, r.signal, ph || '3M') });
      return;
    }
    body.innerHTML = '<div class="loading">Evaluating every equation on this asset… (up to ten seconds the first time)</div>';
    const eq = await api(`/fs2/asset/${encodeURIComponent(id)}/equations`); if (!alive()) return;
    if (tab === 'equations') {
      const fams = [...new Set(eq.catalog.map(r => r.family))];
      body.innerHTML = `<div class="card"><div class="row"><input id="eqQ" placeholder="Search equations" style="width:260px"><div class="chips" id="eqF"><button class="chip on" data-f="">All ${eq.catalog.length}</button>${fams.map(f => `<button class="chip" data-f="${esc(f)}">${esc(f)}</button>`).join('')}</div></div><p class="hint">Every equation of the library calculated on ${esc(id)}'s data. "Usefulness", "best horizon", "hit rate" and IC come from the signal the equation feeds, tested at each horizon. Computed in ${eq.computed_in}s.</p></div><div id="eqL" class="eqgrid" style="margin-top:14px"></div>`;
      let fam = '', q = '';
      const draw = () => { const rows = eq.catalog.filter(r => (!fam || r.family === fam) && (!q || (r.name + ' ' + (r.interpretation || '')).toLowerCase().includes(q)));
        $('#eqL').innerHTML = rows.map(r => `<div class="eqc"><div class="row" style="justify-content:space-between;flex-wrap:nowrap;align-items:flex-start"><div><span class="eqid">${esc(r.id)}</span> <b>${esc(r.name)}</b> <span class="faint">${esc(r.family)}</span></div>${sigPill(r.signal)}</div>
          <div class="tex" data-tex="${esc(r.formula || '')}">${esc(r.formula || '')}</div>
          <div class="row" style="justify-content:space-between;align-items:flex-end"><div class="av">${esc(r.display != null ? r.display : '—')}</div>${r.history && r.history.values && r.history.values.length > 3 ? spark(r.history.values, 140, 32) : ''}</div>
          <div class="meta">${r.percentile != null ? `<span>${Math.round(r.percentile * 100)}th pct</span>` : ''}${r.direction ? `<span>${esc(r.direction)}</span>` : ''}${r.best_horizon ? `<span>best ${esc(r.best_horizon)}</span>` : ''}${r.usefulness != null ? `<span>use ${fmt.num(r.usefulness, 2)}</span>` : ''}${r.hit_rate != null ? `<span>hit ${fmt.pct(r.hit_rate, 0)}</span>` : ''}${r.applies === false ? '<span class="warn">proxy</span>' : ''}</div>
          ${r.variables && Object.keys(r.variables).length ? `<div class="vars">${Object.entries(r.variables).slice(0, 8).map(([k, v]) => `<span><i>${esc(k)}</i> ${esc(varTxt(v))}</span>`).join('')}</div>` : ''}
          ${r.interpretation ? `<div class="interp">${esc(r.interpretation)}</div>` : ''}${r.note ? `<div class="faint" style="font-size:11.5px">${esc(r.note)}</div>` : ''}
          <button class="small ghost" data-src="${esc(r.id)}" style="margin-top:4px">view code</button></div>`).join('') || '<div class="empty">No equation matches</div>';
        texWhenReady($('#eqL'));
        $$('[data-src]').forEach(bt => bt.onclick = async () => { const r = eq.catalog.find(x => x.id === bt.dataset.src); const s = await api(`/fs2/equations/${encodeURIComponent(r.id)}/source`).catch(e => ({ source: e.message })); drawer(`<div class="muted">Equation ${esc(r.id)}</div><h2 style="margin:2px 0 8px">${esc(r.name)}</h2><div class="tex" data-tex="${esc(r.formula || '')}">${esc(r.formula || '')}</div><div class="muted" style="margin:10px 0 6px"><code>${esc(s.fn || '')}</code></div><pre class="code">${esc(s.source || '')}</pre>`); texWhenReady($('#drawer')); }); };
      draw();
      $('#eqQ').oninput = e => { q = e.target.value.toLowerCase(); draw(); };
      $$('#eqF .chip').forEach(c => c.onclick = () => { fam = c.dataset.f; $$('#eqF .chip').forEach(x => x.classList.toggle('on', x === c)); draw(); });
      return;
    }
    const items = eq.tabs[tab] || [];
    body.innerHTML = `<div class="acards">${items.map(analyticCard).join('') || '<div class="empty">Nothing in this family for this asset</div>'}</div>`;
    $$('.acard', body).forEach((el, i) => { const r = items[i]; if (r && r.history && r.history.bins) { const h = document.createElement('div'); el.appendChild(h); histogram(h, r.history.bins, r.history.counts, { mark: r.value }); } });
  };

  // ---------------------------------------------------------------- quant lab
  pages.quant = async (main, args, alive) => {
    const id = args[0] || S.current; const tab = args[1] || 'leaderboard'; const h = pref.get('qlH', '3M');
    main.innerHTML = `<div class="page-head"><div><h1>Quant Lab</h1><p>Which equations have actually predicted ${esc(id)}, at which horizon, how reliably, and whether the combined score has worked out of sample.</p></div><div class="row">${assetPicker('qlPick', id)}<select id="qlH">${HZ.map(x => `<option ${x === h ? 'selected' : ''}>${x}</option>`).join('')}</select></div></div>
      <div class="tabs">${[['leaderboard', 'Equation leaderboard'], ['matrix', 'Signal-horizon matrix'], ['history', 'Score history (point in time)'], ['shaffer', 'Shaffer Score']].map(([k, l]) => `<a class="tab ${k === tab ? 'on' : ''}" href="#/quant/${encodeURIComponent(id)}/${k}">${l}</a>`).join('')}</div><div id="qlB" style="margin-top:14px"><div class="loading">Loading…</div></div>`;
    bindPicker('qlPick', x => go(`#/quant/${encodeURIComponent(x)}/${tab}`));
    $('#qlH').onchange = e => { pref.set('qlH', e.target.value); route(); };
    const body = $('#qlB');
    if (tab === 'leaderboard') {
      const lb = await api(`/fs2/asset/${encodeURIComponent(id)}/leaderboard?horizon=${h}&top=40`); if (!alive()) return;
      body.innerHTML = `<div class="card flush"><h2>${esc(id)} — ${esc(h)} horizon <small>ranked by usefulness (rank IC × evidence); nothing is hard-coded · regime sensitivity = spread of usefulness across regimes</small></h2><div id="lbT"></div></div>`;
      const fm = await featureMeta();
      table($('#lbT'), lb.map((r, i) => ({ ...r, rank: i + 1, lab: (fm[r.signal] || {}).label || r.signal, fam: (fm[r.signal] || {}).family, sens: (() => { const v = Object.values(r.by_regime || {}).map(x => x.usefulness).filter(x => x != null); return v.length ? Math.max(...v) - Math.min(...v) : null; })() })), [
        { k: 'rank', label: '#', l: 1 }, { k: 'lab', label: 'Signal', l: 1, f: r => `<b>${esc(r.lab)}</b><span class="sub">${esc(r.fam)}</span>` }, { k: 'usefulness', label: 'Predictive score', f: r => `<b class="${sign(r.usefulness)}">${fmt.num(r.usefulness, 3)}</b>` },
        { k: 'ic', label: 'IC', f: r => fmt.num(r.ic, 3) }, { k: 'ic_recent', label: 'IC recent', f: r => fmt.num(r.ic_recent, 3) }, { k: 'hit_rate', label: 'Hit rate', f: r => fmt.pct(r.hit_rate, 1) }, { k: 'p', label: 'p-value', f: r => fmt.num(r.p, 4) }, { k: 'q', label: 'q (FDR)', f: r => `<span class="${r.q != null && r.q < 0.1 ? 'pos' : ''}">${fmt.num(r.q, 3)}</span>` },
        { k: 'n', label: 'Sample', f: r => fmt.qty(r.n) }, { k: 'n_eff', label: 'Independent', f: r => fmt.num(r.n_eff, 0) }, { k: 'stability', label: 'Stability', f: r => fmt.pct(r.stability, 0) }, { k: 'sens', label: 'Regime sensitivity', f: r => fmt.num(r.sens, 2) }],
        { sortKey: null, onRow: r => cellDrawer(id, r.signal, h) });
      return;
    }
    if (tab === 'matrix') {
      const mx = await api(`/fs2/asset/${encodeURIComponent(id)}/matrix`); if (!alive()) return;
      body.innerHTML = `<div class="card"><h2>Signal-horizon matrix <small>normalised usefulness (1.0 = strongest) · * = q &lt; 0.10 · click a cell</small></h2><div id="mx"></div></div>`;
      matrixView($('#mx'), id, mx, { metric: 'normalized', maxH: 2000 });
      return;
    }
    if (tab === 'history') {
      const sh = await api(`/fs2/asset/${encodeURIComponent(id)}/shaffer`); if (!alive()) return;
      const hist = (sh.history || {})[h] || {}; const pf = (sh.performance || {})[h] || {};
      body.innerHTML = `<div class="tiles">${kpi('Out-of-sample IC of the score', fmt.num(pf.ic, 3), `t ${fmt.num(pf.t, 1)} · ${fmt.num(pf.n_eff, 0)} independent observations`)}${kpi('Hit rate', pf.hit == null ? '—' : fmt.pct(pf.hit, 0), `when |score| ≥ 5 · ${pf.n_lean || 0} scores`)}${kpi('Record', pf.first ? 'since ' + fmt.date(pf.first) : '—', 'evidence refitted monthly from outcomes known at the time')}</div>
        <div class="card"><h2>${esc(id)} Shaffer Score, ${esc(h)} horizon, as it read on each date</h2><div id="shC"></div><p class="hint">One function scores every date using only data available then; today's score is the last point. The full breakdown and calibration are on <a href="#/analytics/${encodeURIComponent(id)}/shaffer">Analytics → Shaffer Score</a>.</p></div>`;
      if ((hist.chart || {}).dates) lineChart($('#shC'), [{ name: 'Shaffer Score', data: hist.chart.values, color: css('--accent') }], { labels: hist.chart.dates.map(d => fmt.date(d)), zero: true, fmtY: v => fmt.num(v, 0), h: 260 });
      else $('#shC').innerHTML = `<div class="empty">${esc(((sh.horizons || {})[h] || {}).reason || 'No history at this horizon')}</div>`;
      return;
    }
    if (tab === 'shaffer') {
      const st = await api('/fs2/shaffer'); if (!alive()) return;
      body.innerHTML = `<div class="card"><h2>Shaffer Score <span class="pill ${st.enabled ? 'pos' : 'warn'}">${st.enabled ? 'live · ' + esc(st.version) : 'in development'}</span></h2>
        <div class="tex" data-tex="${esc(st.formula || '')}"></div>
        <p class="muted">Running: ${esc(st.source)}. The full breakdown for ${esc(id)} (every family's weight and every signal's s · w · c · r · d) is on its <a href="#/analytics/${encodeURIComponent(id)}/shaffer">Analytics → Shaffer Score</a> tab. To replace the algorithm without a code change, put a file defining <code>VERSION</code> and <code>score_asset(inputs)</code> at <code>${esc(st.override_path)}</code>.</p></div>`;
      texWhenReady(body);
    }
  };

  // ---------------------------------------------------------------- ML lab
  async function mlForecasts(main, args, alive) {
    const id = args[0] || S.current; const h = pref.get('mlH', '1M');
    const [res, pooled, runs, preds] = await Promise.all([api(`/fs2/asset/${encodeURIComponent(id)}/ml`), api('/fs2/ml/pooled').catch(() => ({})), api(`/fs2/model-runs?key=${encodeURIComponent(id)}&limit=60`).catch(() => []), api(`/fs2/predictions?asset=${encodeURIComponent(id)}`).catch(() => ({}))]);
    if (!alive()) return;
    const r = (res.horizons || {})[h] || {};
    main.innerHTML = `<div class="page-head"><div><h1>ML Lab</h1><p>Which variables matter for ${esc(id)}, at which horizon, tested walk-forward: models are trained only on data whose outcomes were known before each test block, and ranked by stable out-of-sample performance, not in-sample fit.</p></div>
      <div class="row">${assetPicker('mlPick', id)}<select id="mlH">${HZ.map(x => `<option ${x === h ? 'selected' : ''}>${x}</option>`).join('')}</select><button class="primary" id="mlTrain">${res.horizons ? 'Retrain' : 'Train models'}</button></div></div>
      <div id="mlJob"></div>
      ${!res.horizons ? `<div class="card"><h2>No models trained for ${esc(id)} yet</h2><p class="muted">Training builds a dataset of ~60 standardised signals against forward returns for each of nine horizons, and runs a purged walk-forward test of OLS, ridge, LASSO, elastic net, random forest, gradient boosting (XGBoost-style) and logistic regression. It takes about half a minute.</p></div>` : `
      <div class="tiles">${kpi('ML score', `<span class="${scoreCls(r.score)}">${scoreTxt(r.score)}</span>`, r.message || `ensemble, ${esc(h)}`)}${kpi('Expected return', r.expected == null ? '—' : fmt.spct(r.expected, 1), r.error != null ? `historical error ± ${fmt.pct(r.error, 1)}` : '')}${kpi('Probability of a rise', fmt.pct(r.prob_up, 0), 'logistic regression')}${kpi('Out-of-sample IC', fmt.num((r.ensemble || {}).ic, 3), `${fmt.pct((r.ensemble || {}).positive_folds, 0)} of folds positive · ${fmt.num((r.ensemble || {}).n_eff, 0)} independent`)}${kpi('Confidence', pct0((r.confidence || {}).value), (r.confidence || {}).label || '')}${kpi('Decay', (r.decay || {}).flag ? '<span class="neg">detected</span>' : 'none', Object.entries((r.decay || {}).windows || {}).map(([k, v]) => `${k} ${fmt.num(v.ic, 2)}`).join(' · '))}</div>
      <div class="card" style="margin-bottom:16px"><h2>Is there a verified ML edge? <span class="pill ${r.verified ? 'pos' : ''}">${r.verified ? 'verified edge' : 'no verified ML edge'}</span> <small>the ensemble must beat every simple alternative on the same out-of-sample rows, be significant on its independent observations, survive an untouched final holdout, and beat shuffled copies of itself</small></h2>
        ${r.edge_reasons && r.edge_reasons.length ? `<p class="muted" style="margin:0 0 8px">${r.edge_reasons.map(esc).join(' · ')}</p>` : ''}<div id="mlBase"></div><div class="row muted" id="mlSplit" style="gap:18px;font-size:12.5px;margin-top:8px"></div></div>
      <div class="grid g3" style="margin-bottom:16px"><div class="card"><h2>Direction <small>P(return &gt; 0)</small></h2><div id="mlDir"></div></div><div class="card"><h2>Future volatility</h2><div id="mlVol"></div></div><div class="card"><h2>Drawdown probability</h2><div id="mlDD"></div></div></div>
      <div class="grid g2" style="margin-bottom:16px"><div class="card"><h2>Tail loss <small>worst day in the window</small></h2><div id="mlTail"></div></div><div class="card"><h2>Beta change <small>realised beta − trailing 1Y beta</small></h2><div id="mlBeta"></div></div></div>
      <div class="grid g-main"><div class="stack">
        <div class="card flush"><h2>Model leaderboard <small>${esc(id)} · ${esc(h)} · ${r.folds || 0} walk-forward blocks on the development sample · ranked by mean fold IC − ½ its dispersion</small></h2><div id="lbT"></div></div>
        <div class="card"><h2>Why today's forecast <small>feature contributions, weighted across the ensemble</small></h2>${explainBlock(r.explain)}</div>
        <div class="card"><h2>Out-of-sample forecasts vs what happened</h2><div id="oosC"></div></div>
      </div><div class="stack">
        <div class="card"><h2>Feature importance <small>permutation, held-out block</small></h2><div id="fiC"></div></div>
        <div class="card"><h2>By family</h2><div id="ffC"></div></div>
        <div class="card"><h2>All horizons</h2><div id="allH"></div></div>
      </div></div>`}
      <div class="grid g2" style="margin-top:16px"><div class="card"><h2>Global and asset-class models <small>stacked across assets, volatility-scaled returns</small><button class="small right" id="poolBtn">Train global</button></h2><div id="poolC"></div></div>
        <div class="card flush"><h2>Forecast record for ${esc(id)} <small>every stored prediction, scored when its horizon passes</small></h2><div id="prT"></div></div></div>
      <div class="card flush" style="margin-top:16px"><h2>Model versions <small>each training run is recorded with its dates, features, parameters and test results</small></h2><div id="runT"></div></div>`;
    bindPicker('mlPick', x => go('#/ml/forecasts/' + encodeURIComponent(x)));
    $('#mlH').onchange = e => { pref.set('mlH', e.target.value); route(); };
    $('#mlTrain').onclick = () => busy($('#mlTrain'), async () => { const j = await post(`/fs2/asset/${encodeURIComponent(id)}/ml`, {}); pollJob(j.id, () => route(), $('#mlJob')); });
    if (res.horizons && r.baselines) {
      const e = r.ensemble || {};
      table($('#mlBase'), [{ k: 'ensemble', v: { ic: e.ic, t: e.t, hit: (e.development || {}).hit, n_eff: e.n_eff, rmse: e.rmse } , same: e.ic, ens: true },
        ...Object.entries(r.baselines).map(([k, v]) => ({ k, v, same: v.ensemble_ic_same_rows }))], [
        { k: 'k', label: 'Forecast', l: 1, f: x => x.ens ? '<b>ML ensemble</b>' : esc(x.k.replace(/_/g, ' ')) },
        { k: 'ic', label: 'OOS rank IC', v: x => x.v.ic, f: x => x.v.ic == null ? '<span class="faint">constant</span>' : fmt.num(x.v.ic, 3) },
        { k: 't', label: 't', v: x => x.v.t, f: x => fmt.num(x.v.t, 1) }, { k: 'hit', label: 'Hit rate', v: x => x.v.hit, f: x => x.v.hit == null ? '—' : fmt.pct(x.v.hit, 0) },
        { k: 'rmse', label: 'RMSE', v: x => x.v.rmse, f: x => fmt.num(x.v.rmse, 4) },
        { k: 'same', label: 'Ensemble on the same rows', v: x => x.same, f: x => x.ens ? '' : `${fmt.num(x.same, 3)} ${x.v.ic != null && x.same != null ? (x.same > x.v.ic + 0.01 ? '<span class="pos">beats</span>' : '<span class="neg">loses</span>') : ''}` }], { sortKey: null });
      if (r.status === 'ok') $('#mlSplit').innerHTML = `<span>Development IC ${fmt.num((e.development || {}).ic, 3)} (${fmt.num((e.development || {}).n_eff, 0)} obs)</span><span>Untouched holdout IC ${fmt.num((e.holdout || {}).ic, 3)} (${fmt.num((e.holdout || {}).n_eff, 0)} obs)</span><span>Permutation p ${fmt.num(e.permutation_p, 3)}</span><span>OOS R² vs historical mean ${fmt.num(e.r2_vs_mean, 3)}</span><span>Always long: hit ${fmt.pct((r.always_long || {}).hit, 0)}, Sharpe ${fmt.num((r.always_long || {}).sharpe, 2)}</span>`;
      const d = r.direction || {};
      $('#mlDir').innerHTML = d.n ? `<div class="kv"><span>P(rise) today</span><b>${fmt.pct(r.prob_up, 0)}</b><span>Verified</span><span>${d.verified ? '<span class="pos">yes</span>' : 'no'}</span><span>AUC</span><span>${fmt.num(d.auc, 3)}</span><span>Brier · base rate</span><span>${fmt.num(d.brier, 4)} · ${fmt.num(d.baseline_brier, 4)}</span><span>Accuracy · balanced</span><span>${fmt.pct(d.accuracy, 1)} · ${fmt.pct(d.balanced_accuracy, 1)}</span><span>Precision · recall · F1</span><span>${fmt.num(d.precision, 2)} · ${fmt.num(d.recall, 2)} · ${fmt.num(d.f1, 2)}</span></div>
        <div class="muted" style="font-size:12px;margin-top:6px">Calibration: ${(d.reliability || []).map(b => `${fmt.pct(b.mean_prob, 0)}→${fmt.pct(b.freq, 0)} (${b.n})`).join(' · ')}</div>` : '<div class="empty">—</div>';
      const stab = x => x.stable ? '<span class="pos">yes — both halves</span>' : x.verified ? '<span class="warn">no — one half only</span>' : 'no';
      const v = r.volatility || {};
      $('#mlVol').innerHTML = v.model ? `<div class="kv"><span>Forecast (annual)</span><b>${fmt.pct(r.vol_forecast, 1)}</b><span>Verified</span><span>${v.verified ? '<span class="pos">yes</span>' : 'no'}</span><span>RMSE model</span><span>${fmt.num(v.model.rmse, 4)}</span><span>Current vol</span><span>${fmt.num((v.baseline_current_vol || {}).rmse, 4)}</span><span>EWMA</span><span>${fmt.num((v.baseline_ewma || {}).rmse, 4)}</span><span>Stable</span><span>${stab(v)}</span><span>IC</span><span>${fmt.num(v.model.ic, 3)}</span></div>` : '<div class="empty">Not modelled at this horizon</div>';
      const riskCard = (x, bases) => x.model ? `<div class="kv"><span>Verified</span><span>${x.verified ? '<span class="pos">yes</span>' : 'no'}</span><span>Stable</span><span>${stab(x)}</span><span>RMSE model</span><span>${fmt.num(x.model.rmse, 4)}</span>${bases.map(([k, l]) => `<span>${l}</span><span>${fmt.num((x['baseline_' + k] || {}).rmse, 4)}</span>`).join('')}<span>Improvement vs best baseline</span><span>${fmt.pct(x.rmse_improvement, 1)}</span><span>IC</span><span>${fmt.num(x.model.ic, 3)}</span><span>OOS rows</span><span>${fmt.num(x.model.n, 0)}</span></div>` : '<div class="empty">Not modelled at this horizon</div>';
      $('#mlTail').innerHTML = riskCard(r.tail_loss || {}, [['previous_window', 'Previous window'], ['vol_scaled', 'EWMA vol × E[max]']]);
      $('#mlBeta').innerHTML = riskCard(r.beta_change || {}, [['no_change', 'No change'], ['mean_change', 'Historical mean change'], ['blume', 'Blume (⅓ toward 1)']]);
      const dd = r.drawdown || {};
      $('#mlDD').innerHTML = dd.n ? `<div class="kv"><span>P(drawdown ≥ ${fmt.pct(dd.threshold, 1)})</span><b>${fmt.pct(r.dd_probability, 0)}</b><span>Verified</span><span>${dd.verified ? '<span class="pos">yes</span>' : 'no'}</span><span>Brier · base rate</span><span>${fmt.num(dd.brier, 4)} · ${fmt.num(dd.baseline_brier, 4)}</span><span>AUC</span><span>${fmt.num(dd.auc, 3)}</span><span>Stable</span><span>${stab(dd)}</span><span>Base rate</span><span>${fmt.pct(dd.base_rate, 0)}</span></div>` : '<div class="empty">Not modelled at this horizon</div>';
    }
    if (res.horizons && r.status === 'ok') {
      table($('#lbT'), r.leaderboard, [{ k: 'label', label: 'Model', l: 1 }, { k: 'ic', label: 'IC', f: x => fmt.num(x.ic, 3) }, { k: 'accuracy', label: 'Direction', f: x => fmt.pct(x.accuracy, 1) }, { k: 'sharpe', label: 'Sharpe', f: x => fmt.num(x.sharpe, 2) }, { k: 'rmse', label: 'RMSE', f: x => x.rmse == null ? '—' : fmt.pct(x.rmse, 2) }, { k: 'positive_folds', label: '+ folds', f: x => fmt.pct(x.positive_folds, 0) }, { k: 'stability', label: 'Stability', l: 1, f: x => x.stability ? `<span class="pill ${x.stability === 'High' ? 'pos' : x.stability === 'Medium' ? 'warn' : ''}">${x.stability}</span>` : '—' }, { k: 'w', label: 'Ensemble weight', v: x => ((r.ensemble || {}).weights || {})[x.model], f: x => fmt.pct(((r.ensemble || {}).weights || {})[x.model], 0) }], { sortKey: null });
      const fm = await featureMeta();
      hbars($('#fiC'), Object.entries(r.importance || {}).slice(0, 14).map(([k, v]) => ({ label: (fm[k] || {}).label || k, value: v, sub: (fm[k] || {}).family })), { empty: 'No feature helped out of sample' });
      hbars($('#ffC'), Object.entries(r.family_importance || {}).map(([k, v]) => ({ label: k, value: v })), { empty: 'No family helped out of sample' });
      const oos = r.oos || [];
      lineChart($('#oosC'), [{ name: 'Forecast', data: oos.map(x => x[1]), color: css('--accent') }, { name: 'Realised', data: oos.map(x => x[2]), color: css('--faint'), width: 1 }], { labels: oos.map((x, i) => String(i)), zero: true, fmtY: v => fmt.pct(v, 1), h: 200 });
    } else if (res.horizons) { $('#lbT') && ($('#lbT').innerHTML = `<div class="empty">${esc(r.reason || r.message || 'No results at this horizon')}</div>`); }
    if (res.horizons) table($('#allH'), HZ.map(x => ({ h: x, r: res.horizons[x] || {} })), [{ k: 'h', label: 'Horizon', l: 1 }, { k: 's', label: 'Score', v: x => x.r.score, f: x => `<b class="${scoreCls(x.r.score)}">${scoreTxt(x.r.score)}</b>` }, { k: 'ic', label: 'OOS IC', v: x => (x.r.ensemble || {}).ic, f: x => fmt.num((x.r.ensemble || {}).ic, 2) }, { k: 'b', label: 'Best', l: 1, v: x => x.r.best_model, f: x => esc(x.r.best_model || '—') }], { sortKey: null, onRow: x => { pref.set('mlH', x.h); route(); } });
    const pk = Object.keys(pooled);
    $('#poolC').innerHTML = pk.length ? pk.map(k => { const p = pooled[k]; return `<div style="margin-bottom:12px"><b>${esc(p.level)} ${esc(p.key)}</b> <span class="faint">${p.assets} assets · ${esc(p.trained_at)}</span>${Object.entries(p.horizons || {}).map(([hh, v]) => `<div class="row" style="padding:4px 0;border-top:1px solid var(--border)"><span style="width:40px">${hh}</span><span class="faint">IC ${fmt.num(v.ensemble_ic, 3)}</span><span style="flex:1;font-size:12px">${Object.entries(v.family_importance || {}).slice(0, 4).map(([f, x]) => `${esc(f)} ${fmt.pct(x, 0)}`).join(' · ')}</span></div>`).join('')}</div>`; }).join('') : '<div class="empty">Not trained yet: what works across the whole market, and within each asset class</div>';
    $('#poolBtn').onclick = () => busy($('#poolBtn'), async () => { const j = await post('/fs2/ml/pooled', { level: 'global', key: 'all' }); pollJob(j.id, () => route(), $('#mlJob')); });
    const predFmt = (x, v) => v == null ? '' : x.model === 'ml_drawdown' ? `P(drawdown ≥ ${fmt.pct((x.detail || {}).threshold, 1)}) ${fmt.pct(v, 0)}` : x.model === 'ml_vol' ? `vol ${fmt.pct(v, 1)} a year` : fmt.spct(v, 1);
    table($('#prT'), (preds.recent || []).slice().reverse(), [{ k: 'made_on', label: 'Made', l: 1 }, { k: 'horizon', label: 'H', l: 1 }, { k: 'model', label: 'Model', l: 1 }, { k: 'predicted', label: 'Forecast', f: x => predFmt(x, x.predicted) }, { k: 'realized', label: 'Actual', f: x => x.realized == null ? `<span class="faint">due ${esc(x.target_date)}</span>` : x.model === 'ml_drawdown' ? (x.realized ? 'drawdown hit' : 'no drawdown') : predFmt(x, x.realized) }, { k: 'error', label: 'Error', f: x => x.error == null ? '' : x.model === 'ml_drawdown' ? 'Brier ' + fmt.num(x.error * x.error, 3) : fmt.spct(x.error, 1) }], { sortKey: null, maxH: 320, empty: 'No forecasts stored yet' });
    table($('#runT'), runs, [{ k: 'created_at', label: 'Run', l: 1 }, { k: 'horizon', label: 'H', l: 1 }, { k: 'model', label: 'Model', l: 1 }, { k: 'version', label: 'Version', l: 1 }, { k: 'train_start', label: 'Train from', l: 1 }, { k: 'test_start', label: 'Test', l: 1, f: x => `${esc(x.test_start || '')} → ${esc(x.test_end || '')}` }, { k: 'ic', label: 'IC', v: x => (x.metrics || {}).ic, f: x => fmt.num((x.metrics || {}).ic, 3) }], { sortKey: null, maxH: 300, empty: 'No runs recorded' });
  };

  // ---------------------------------------------------------------- risk: VaR, contributions, correlations, scenarios, Monte Carlo
  // ---------------------------------------------------------------- ML Lab: the laboratory for Shaffer Score and Shaffer Hedge
  const LAB_TABS = [['production', 'Production models'], ['performance', 'Historical performance'], ['signals', 'Signal research'], ['alpha', 'Shaffer Alpha'], ['directional', 'Shaffer Directional'], ['newinfo', 'New Information'], ['sigweights', 'Signal weights'], ['weights', 'Family weights'],
    ['challengers', 'Challengers & promotion'], ['hedge', 'Hedge research'], ['live', 'Live learning'], ['versions', 'Version comparison'], ['forecasts', 'Independent ML forecasts'],
    ['alphanext', 'Alpha vNext'], ['dirnext', 'Directional vNext'], ['hedgenext', 'Hedge vNext']];
  const LAB_GROUPS = [['Shaffer Alpha', ['alpha', 'alphanext', 'sigweights', 'weights']], ['Shaffer Directional', ['directional', 'dirnext']],
    ['Shaffer Hedge', ['hedge', 'hedgenext']], ['Lab', ['production', 'performance', 'signals', 'newinfo', 'challengers', 'live', 'versions', 'forecasts']]];
  const LAB_NAME = Object.fromEntries(LAB_TABS);
  const icT = (m) => m && m.ic != null ? `${fmt.num(m.ic, 3)} <span class="faint">(t ${fmt.num(m.t, 1)})</span>` : '—';
  const stagePill = s => `<span class="pill ${s === 'production' ? 'pos' : s === 'eligible for promotion' ? 'pos' : s === 'live shadow' ? 'warn' : s === 'retired' ? '' : 'neg'}" style="font-size:10.5px">${esc(s)}</span>`;
  // Signal weights: the Shaffer equation with learned signal / family / scaling weights, validated on unseen eras
  const statusPill = s => `<span class="pill ${s === 'ELIGIBLE FOR PROMOTION' ? 'pos' : s === 'SHADOW' ? 'warn' : s === 'REJECT' ? 'neg' : ''}" style="font-size:10.5px">${esc(s || '—')}</span>`;
  const cl01 = v => v == null ? v : Math.min(1, Math.max(0, v));
  const ciPct = a => a && a.acc != null ? `${fmt.pct(a.acc, 1)} <span class="faint">[${fmt.pct(cl01(a.lo), 1)}, ${fmt.pct(cl01(a.hi), 1)}]</span>` : '—';
  const KIND_LABEL = { signal: 'signal weights (δ kept, β ≥ 0)', scaling: 'signal weights + confidence / regime / decay strengths', free: 'free signed weights on every signal', interact: 'signal weights + 5 regime interactions', family: 'family weights only' };
  const labSignalWeights = (body, WR, vers) => {
    const HS = (WR || {}).horizons || {};
    const order = ['1D', '1W', '1M', '3M', '6M', '12M'].filter(k => HS[k] && HS[k].challengers && Object.keys(HS[k].challengers).length);
    if (!order.length) { body.innerHTML = `<div class="card"><h2>No signal-weight research yet</h2><p class="muted">Rebuild the research records (they now keep every signal's point-in-time inputs), then run the research: <code>python -m finsim2 lab --build</code>, or the buttons above.</p></div>`; return; }
    const hz = order.includes(pref.get('swH', '1M')) ? pref.get('swH', '1M') : order[0];
    const hx = HS[hz], CH = hx.challengers || {};
    const ck = CH[pref.get('swC', '')] ? pref.get('swC', '') : (hx.best || Object.keys(CH)[0]);
    const c = CH[ck] || {}, wf = c.walkforward || {}, P = wf.production || {}, C = wf.challenger || {};
    const statusOf = (lab, key) => { const v = vers.find(x => x.id === `shaffer-${WR.score_version}-sig-${key.replace('@', '-')}-exp`); const st = v && v.stage; return st && st.stage === 'eligible for promotion' && (st.eligible_horizons || []).includes(lab) ? 'ELIGIBLE FOR PROMOTION' : ((HS[lab].challengers[key] || {}).gates || {}).status; };
    const main = order.map(lab => { const h = HS[lab], b = (h.challengers || {})[h.best] || {}, w = b.walkforward || {}, bl = w.baselines || {};
      const pa = (w.production || {}).acc, ca = (w.challenger || {}).acc;
      return { lab, best: h.best, pa, ca, base: bl.best, ba: bl.best_acc, ex: ca != null && bl.best_acc != null ? ca - bl.best_acc : null, exp: pa != null && bl.best_acc != null ? pa - bl.best_acc : null, pic: (w.production || {}).ic, cic: (w.challenger || {}).ic, prk: (w.production || {}).rank_ic, crk: (w.challenger || {}).rank_ic, prt: (w.production || {}).rank_t, crt: (w.challenger || {}).rank_t, st: h.best ? statusOf(lab, h.best) : 'INSUFFICIENT DATA' }; });
    body.innerHTML = `<div class="card"><h2>Can re-weighting the Shaffer equation make it more accurate? <small>walk-forward over unseen eras · the best challenger per horizon by the multi-metric research score</small></h2>
        <p class="muted" style="margin:0 0 8px;font-size:12.5px">Every challenger keeps the Shaffer equation — signal x = clip(z/2), point-in-time direction δ, confidence c, regime r, decay d — and learns some of its parameters, per horizon, with ridge shrinkage toward the parent node (global → class → sector → industry → asset) and bounded weights. Trained only on outcomes known before each test era: → 2008 test 2009–12, → 2012 test 2013–16, → 2016 test 2017–20, → 2020 test 2021–24, → 2024 test 2025–. Accuracy = the sign of the score vs the sign of the return, with a date-clustered 95% interval; baseline = the best naive rule on the same records.</p>
        <div id="swMain"></div></div>
      <div class="row" style="gap:10px;margin:14px 0 10px;flex-wrap:wrap"><span class="muted" style="font-size:12.5px">Horizon</span><div class="seg" id="swH">${order.map(x => `<button data-h="${x}" class="${x === hz ? 'on' : ''}">${x}</button>`).join('')}</div>
        <span class="muted" style="font-size:12.5px;margin-left:10px">Challenger</span><select id="swC">${Object.keys(CH).map(k => `<option value="${esc(k)}" ${k === ck ? 'selected' : ''}>${esc(k)}${k === hx.best ? ' (best)' : ''}</option>`).join('')}</select></div>
      <div class="card flush"><h2>Challengers at ${esc(hz)} <small>${fmt.num(hx.records, 0)} records · ${hx.assets} assets · ${esc(hx.first || '')} → ${esc(hx.last || '')}</small></h2><div id="swCh"></div></div>
      <div class="grid g2" style="margin-top:14px"><div class="card flush"><h2>${esc(ck)} by era <small>${esc(KIND_LABEL[c.kind] || '')} · hierarchy to ${esc(c.depth || '')}</small></h2><div id="swEr"></div>
          <p class="faint" style="font-size:12px;padding:0 12px">2018 split (train → 2017, test 2018–): Δ IC ${icT((c.split || {}).delta_ic)}, Δ accuracy ${fmt.spct(((c.split || {}).delta_acc || {}).delta, 1)}. γ (confidence, regime, decay) = ${esc((c.gamma || [1, 1, 1]).join(', '))}.</p></div>
        <div class="card flush"><h2>Where does specialisation stop helping? <small>signal weights, walk-forward, by how deep the hierarchy goes</small></h2><div id="swSp"></div><div id="swSpC" style="margin-top:8px"></div></div></div>
      <div class="grid g2" style="margin-top:14px"><div class="card flush"><h2>Score bands: production <small>walk-forward records · ${esc(hz)} returns</small></h2><div id="swBP"></div></div><div class="card flush"><h2>Score bands: ${esc(ck)}</h2><div id="swBC"></div></div></div>
      <div class="grid g2" style="margin-top:14px"><div class="card flush"><h2>Does a bigger |score| mean more often right? <small>accuracy by |score|, production vs challenger</small></h2><div id="swAB"></div></div>
        <div class="card flush"><h2>Naive baselines <small>same walk-forward records</small></h2><div id="swBl"></div></div></div>
      <div class="card flush" style="margin-top:14px"><h2>Signal weights: production vs challenger <small>${esc(hx.best || '')} · each signal's share of the score's total |effective weight| on x, averaged over the node's records · stability = the challenger's global weight in each era fit</small></h2>
        <div class="row" style="gap:8px;padding:0 12px 8px"><span class="muted" style="font-size:12.5px">Node</span><select id="swN">${Object.keys(hx.weights || {}).sort((a, b) => (a !== 'global') - (b !== 'global') || a.localeCompare(b)).map(n => `<option ${n === pref.get('swN', 'global') ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select></div><div id="swW"></div></div>`;
    table($('#swMain'), main, [{ k: 'lab', label: 'Horizon', l: 1, f: x => `<b>${x.lab}</b><span class="sub">${esc(x.best || '')}</span>` }, { k: 'pa', label: 'Prod. accuracy', f: x => fmt.pct(x.pa, 1) }, { k: 'ca', label: 'Best challenger', f: x => fmt.pct(x.ca, 1) },
      { k: 'ba', label: 'Naive baseline', f: x => `${fmt.pct(x.ba, 1)}<span class="sub">${esc((x.base || '').replace(/_/g, ' '))}</span>` }, { k: 'ex', label: 'Excess ch. / prod.', f: x => `<span class="${(x.ex || 0) > 0 ? 'pos' : 'neg'}">${fmt.spct(x.ex, 1)}</span> <span class="faint">/ ${fmt.spct(x.exp, 1)}</span>` },
      { k: 'pic', label: 'IC prod. → ch.', f: x => `${fmt.num(x.pic, 3)} → ${fmt.num(x.cic, 3)}` }, { k: 'rk', label: 'Rank IC prod. → ch.', f: x => `${fmt.num(x.prk, 3)} → ${fmt.num(x.crk, 3)}<span class="sub">t ${fmt.num(x.prt, 1)} → ${fmt.num(x.crt, 1)}</span>` }, { k: 'st', label: 'Status', l: 1, f: x => statusPill(x.st) }], { sortKey: null });
    table($('#swCh'), Object.entries(CH).map(([k, v]) => ({ k, ...v })), [{ k: 'k', label: 'Challenger', l: 1, f: x => `<b>${esc(x.k)}</b><span class="sub">${esc(KIND_LABEL[x.kind] || '')} · ${fmt.num(x.n_params, 0)} params per node</span>` },
      { k: 'acc', label: 'Accuracy prod. → ch.', v: x => ((x.walkforward || {}).challenger || {}).acc, f: x => `${fmt.pct(((x.walkforward || {}).production || {}).acc, 1)} → <b>${fmt.pct(((x.walkforward || {}).challenger || {}).acc, 1)}</b>` },
      { k: 'dacc', label: 'Δ accuracy [95%]', v: x => ((x.walkforward || {}).delta_acc || {}).delta, f: x => { const d = (x.walkforward || {}).delta_acc || {}; return d.delta != null ? `<span class="${d.delta > 0 ? 'pos' : 'neg'}">${fmt.spct(d.delta, 1)}</span> <span class="faint">[${fmt.spct(d.lo, 1)}, ${fmt.spct(d.hi, 1)}]</span>` : '—'; } },
      { k: 'dic', label: 'Δ IC (t)', v: x => ((x.walkforward || {}).delta_ic || {}).ic, f: x => icT((x.walkforward || {}).delta_ic) }, { k: 'rk', label: 'Rank IC prod. → ch.', f: x => `${fmt.num(((x.walkforward || {}).production || {}).rank_ic, 3)} → ${fmt.num(((x.walkforward || {}).challenger || {}).rank_ic, 3)}` },
      { k: 'eras', label: 'Eras won', v: x => (x.gates || {}).eras_won, f: x => `${(x.gates || {}).eras_won ?? '—'}/${(x.gates || {}).eras_complete ?? '—'}` }, { k: 'sp', label: '2018 split Δ IC', f: x => icT((x.split || {}).delta_ic) },
      { k: 'rs', label: 'Research score', v: x => (x.research_score || {}).score, f: x => fmt.num((x.research_score || {}).score, 2) },
      { k: 'g', label: 'Gates', l: 1, f: x => ['G1_walkforward', 'G2_eras'].map(g => `<span class="pill ${(x.gates || {})[g] ? 'pos' : 'neg'}" style="font-size:10px">${(x.gates || {})[g] ? '✓' : '✗'} ${g.slice(0, 2)}</span>`).join(' ') }, { k: 'st', label: 'Status', l: 1, f: x => statusPill(statusOf(hz, x.k)) }], { sortKey: 'rs' });
    table($('#swEr'), (c.eras || []).map(e => ({ ...e, era: `${e.from.slice(0, 4)}–${e.to >= '2100' ? 'now' : String(+e.to.slice(0, 4) - 1)}` })), [{ k: 'era', label: 'Test era', l: 1 }, { k: 'train', label: 'Train / test', f: e => `${fmt.num(e.train, 0)} / ${fmt.num(e.test, 0)}` },
      { k: 'acc', label: 'Accuracy prod. → ch.', f: e => e.production ? `${fmt.pct(e.production.acc, 1)} → <b>${fmt.pct(e.challenger.acc, 1)}</b>` : `<span class="faint">${esc(e.status || '')}</span>` },
      { k: 'ic', label: 'IC prod. → ch.', f: e => e.production ? `${fmt.num(e.production.ic, 3)} → ${fmt.num(e.challenger.ic, 3)}` : '—' }, { k: 'd', label: 'Δ IC (t)', f: e => icT(e.delta_ic) }, { k: 'b', label: 'Best naive', f: e => e.baselines ? `${fmt.pct(e.baselines.best_acc, 1)}<span class="sub">${esc((e.baselines.best || '').replace(/_/g, ' '))}</span>` : '—' }], { sortKey: null });
    const SP = hx.specialisation || {};
    table($('#swSp'), ['global', 'class', 'sector', 'industry', 'asset'].filter(d => SP[d]).map(d => ({ d, ...SP[d] })), [{ k: 'd', label: 'Hierarchy to', l: 1 }, { k: 'acc', label: 'Accuracy prod. → ch.', f: x => `${fmt.pct(x.production_acc, 1)} → <b>${fmt.pct(x.acc, 1)}</b>` }, { k: 'ic', label: 'IC prod. → ch.', f: x => `${fmt.num(x.production_ic, 3)} → ${fmt.num(x.ic, 3)}` }, { k: 'delta_ic', label: 'Δ IC', f: x => `<span class="${(x.delta_ic || 0) > 0 ? 'pos' : 'neg'}">${fmt.num(x.delta_ic, 3)}</span>` }], { sortKey: null });
    table($('#swSpC'), Object.entries(c.by_class || {}).map(([k, v]) => ({ k, ...v })), [{ k: 'k', label: `${ck} by class`, l: 1 }, { k: 'n', label: 'Records', f: x => fmt.num(x.n, 0) }, { k: 'acc', label: 'Accuracy prod. → ch.', f: x => `${fmt.pct(x.production_acc, 1)} → <b>${fmt.pct(x.challenger_acc, 1)}</b>` }, { k: 'ic', label: 'IC prod. → ch.', f: x => `${fmt.num(x.production_ic, 3)} → ${fmt.num(x.challenger_ic, 3)}` }], { sortKey: 'n' });
    const bandCols = [{ k: 'band', label: 'Score', l: 1 }, { k: 'n', label: 'Records', f: b => fmt.num(b.n, 0) }, { k: 'independent', label: 'Indep. obs.', f: b => fmt.num(b.independent, 0) }, { k: 'positive', label: '% positive', f: b => fmt.pct(b.positive, 0) }, { k: 'mean', label: 'Avg return', f: b => fmt.spct(b.mean, 2) }, { k: 'median', label: 'Median', f: b => fmt.spct(b.median, 2) }, { k: 'correct', label: 'Direction right [95%]', f: b => b.correct != null ? `${fmt.pct(b.correct, 0)} <span class="faint">[${fmt.pct(cl01(b.lo), 0)}, ${fmt.pct(cl01(b.hi), 0)}]</span>` : '—' }];
    table($('#swBP'), P.bands || [], bandCols, { sortKey: null }); table($('#swBC'), C.bands || [], bandCols, { sortKey: null });
    table($('#swAB'), (P.abs_bands || []).map((b, i) => ({ band: b.band, p: b, c: (C.abs_bands || [])[i] || {} })), [{ k: 'band', label: '|Score|', l: 1 }, { k: 'pn', label: 'Records prod. / ch.', f: x => `${fmt.num(x.p.n, 0)} / ${fmt.num(x.c.n, 0)}` }, { k: 'p', label: 'Production right', f: x => ciPct({ acc: x.p.correct, lo: x.p.lo, hi: x.p.hi }) }, { k: 'c', label: 'Challenger right', f: x => ciPct({ acc: x.c.correct, lo: x.c.lo, hi: x.c.hi }) }], { sortKey: null });
    table($('#swBl'), Object.entries((wf.baselines || {}).by || {}).map(([k, v]) => ({ k, ...v })).concat([{ k: 'production Shaffer', ...P }, { k: `challenger ${ck}`, ...C }]), [{ k: 'k', label: 'Rule', l: 1, f: x => esc(x.k.replace(/_/g, ' ')) }, { k: 'acc', label: 'Accuracy [95%]', v: x => x.acc, f: x => ciPct(x) }, { k: 'coverage', label: 'Coverage', f: x => fmt.pct(x.coverage, 0) }], { sortKey: 'acc' });
    const node = (hx.weights || {})[pref.get('swN', 'global')] || (hx.weights || {}).global || { signals: {} }, stab = hx.stability || {};
    table($('#swW'), Object.entries(node.signals || {}).map(([s, v]) => ({ s, ...v, d: (v.challenger || 0) - (v.production || 0), st: stab[s] })), [{ k: 's', label: 'Signal', l: 1 }, { k: 'production', label: 'Production', f: x => fmt.spct(x.production, 1) }, { k: 'challenger', label: 'Challenger', f: x => `<b class="${scoreCls(x.challenger)}">${fmt.spct(x.challenger, 1)}</b>` },
      { k: 'd', label: 'Change', f: x => `<span class="${x.d > 0 ? 'pos' : 'neg'}">${fmt.spct(x.d, 1)}</span>` }, { k: 'st', label: 'Stable across eras?', l: 1, v: x => x.st && x.st.same_sign ? 1 : 0, f: x => x.st ? `${x.st.same_sign ? '<span class="pill pos" style="font-size:10px">same sign every era</span>' : (x.st.eras.some(v => v > 1e-9) && x.st.eras.some(v => v < -1e-9)) ? '<span class="pill neg" style="font-size:10px">sign flips</span>' : '<span class="pill warn" style="font-size:10px">not used in every era</span>'} <span class="faint mono" style="font-size:11px">${x.st.eras.map(v => fmt.num(v, 2)).join(' · ')}</span>` : '<span class="faint">not used</span>' }], { sortKey: 'challenger', maxH: 520, empty: 'No weights for this node' });
    $$('#swH button').forEach(b => b.onclick = () => { pref.set('swH', b.dataset.h); route(); });
    $('#swC').onchange = e => { pref.set('swC', e.target.value); route(); };
    $('#swN').onchange = e => { pref.set('swN', e.target.value); route(); };
  };
  // Shaffer Alpha (relative opportunity) vs Shaffer Directional (absolute direction): two questions, never one number
  const DR_HZ = ['1D', '1W', '1M', '3M', '6M', '12M'];
  const drNeed = body => { body.innerHTML = `<div class="card"><h2>No Alpha / Directional research yet</h2><p class="muted">Run <code>python -m finsim2 lab --directional</code> (after the research records are built), or the <b>Run weight research</b> button above.</p></div>`; };
  const drHz = (DR, key) => { const HS = (DR || {}).horizons || {}; const order = DR_HZ.filter(k => HS[k] && HS[k].directional && Object.keys(HS[k].directional).length); const hz = order.includes(pref.get(key, '1M')) ? pref.get(key, '1M') : order[0]; return { HS, order, hz }; };
  const drSeg = (id, order, hz) => `<div class="row" style="gap:10px;margin:14px 0 10px;flex-wrap:wrap"><span class="muted" style="font-size:12.5px">Horizon</span><div class="seg" id="${id}">${order.map(x => `<button data-h="${x}" class="${x === hz ? 'on' : ''}">${x}</button>`).join('')}</div>`;
  const tt = (v, t) => `${fmt.num(v, 3)}${t != null ? ` <span class="faint">(t ${fmt.num(t, 1)})</span>` : ''}`;
  const labAlpha = (body, DR, vers) => {
    const { HS, order, hz } = drHz(DR, 'drH'); if (!order.length) return drNeed(body);
    const hx = HS[hz], A = hx.alpha || {};
    body.innerHTML = `<div class="card"><h2>Shaffer Alpha — does a higher score mean a better-than-normal outcome? <small>ranking, not absolute direction</small></h2>
        <p class="muted" style="margin:0 0 8px;font-size:12.5px">The production Shaffer Score is an evidence score centred on zero: is this setup better or worse than normal for the product? It is judged here as a ranking of relative opportunity. Target = the volatility-scaled return <b>in excess of a point-in-time base expectation</b> (${esc(((DR.prior_labels || {})[DR.main_prior]) || DR.main_prior || '')}). It does not need to beat “always bullish” — that is the Directional question.</p><div id="alMain"></div></div>
      ${drSeg('alH', order, hz)}</div>
      <div class="card flush"><h2>Alpha challengers at ${esc(hz)} <small>signal weights fitted to the excess-return target · walk-forward over the unseen eras · Δ vs production on the same records</small></h2><div id="alCh"></div></div>
      <div class="grid g2" style="margin-top:14px"><div class="card flush"><h2>Where does specialisation stop helping? <small>alpha target, walk-forward</small></h2><div id="alSp"></div></div>
        <div class="card flush"><h2>Production alpha at ${esc(hz)} <small>2009 → now</small></h2><div id="alPr"></div></div></div>
      <div class="card flush" style="margin-top:14px"><h2>Signal weights: Alpha target vs Directional target <small>class-level challengers · share of the global node's |effective weight| · stable = kept (non-zero, same direction) in every era fit (CV = era-to-era variation)</small></h2><div id="alSg"></div></div>`;
    table($('#alMain'), order.map(k => ({ k, p: HS[k].production_alpha || {}, b: HS[k].best_alpha, c: ((HS[k].alpha || {})[HS[k].best_alpha] || {}) })), [{ k: 'k', label: 'Horizon', l: 1, f: x => `<b>${x.k}</b>` },
      { k: 'ic', label: 'Production IC', f: x => tt(x.p.ic, x.p.ic_t) }, { k: 'rk', label: 'Rank IC', f: x => tt(x.p.rank_ic, x.p.rank_t) },
      { k: 'q5', label: 'Top − bottom quintile (weekly)', f: x => `${fmt.spct(x.p.quintile_spread, 2)} <span class="faint">(t ${fmt.num(x.p.quintile_t, 1)})</span>` }, { k: 'dec', label: 'Decile spread', f: x => fmt.spct(x.p.decile_spread, 1) },
      { k: 'hit', label: 'Hit vs weekly median', f: x => fmt.pct(x.p.hit_vs_median, 1) }, { k: 'st', label: 'Rank stability', f: x => fmt.num(x.p.rank_stability, 2) },
      { k: 'b', label: 'Best challenger (Δ IC, t)', l: 1, f: x => `${esc(x.b || '')}<span class="sub">${icT(((x.c.walkforward || {}).delta_ic))}</span>` }, { k: 's', label: 'Status', l: 1, f: x => statusPill((x.c.gates || {}).status) }], { sortKey: null });
    table($('#alCh'), Object.entries(A).map(([k, v]) => ({ k, ...v })), [{ k: 'k', label: 'Challenger', l: 1, f: x => `<b>${esc(x.k)}</b><span class="sub">${fmt.num(x.n_params, 0)} params per node</span>` },
      { k: 'ic', label: 'IC prod. → ch.', f: x => `${fmt.num(((x.walkforward || {}).production || {}).ic, 3)} → <b>${fmt.num(((x.walkforward || {}).challenger || {}).ic, 3)}</b>` },
      { k: 'dic', label: 'Δ IC (t)', v: x => ((x.walkforward || {}).delta_ic || {}).ic, f: x => icT((x.walkforward || {}).delta_ic) },
      { k: 'rk', label: 'Rank IC prod. → ch.', f: x => `${fmt.num(((x.walkforward || {}).production || {}).rank_ic, 3)} → ${fmt.num(((x.walkforward || {}).challenger || {}).rank_ic, 3)}` },
      { k: 'q5', label: 'Quintile spread prod. → ch.', f: x => `${fmt.spct(((x.walkforward || {}).production || {}).quintile_spread, 2)} → ${fmt.spct(((x.walkforward || {}).challenger || {}).quintile_spread, 2)}` },
      { k: 'eras', label: 'Eras won', f: x => `${(x.gates || {}).eras_won ?? '—'}/${(x.gates || {}).eras_complete ?? '—'}` }, { k: 'sp', label: '2018 split Δ IC', f: x => icT((x.split || {}).delta_ic) },
      { k: 'st', label: 'Status', l: 1, f: x => statusPill((x.gates || {}).status) }], { sortKey: 'dic' });
    const SP = ((hx.specialisation || {}).alpha) || {};
    table($('#alSp'), ['global', 'class', 'sector', 'industry', 'asset'].map(d => ({ d, ...(SP[d] || {}) })), [{ k: 'd', label: 'Hierarchy to', l: 1 }, { k: 'ic', label: 'IC', f: x => fmt.num(x.ic, 3) }, { k: 'rank_ic', label: 'Rank IC', f: x => fmt.num(x.rank_ic, 3) }, { k: 'delta_ic', label: 'Δ IC vs production', f: x => `<span class="${(x.delta_ic || 0) > 0 ? 'pos' : 'neg'}">${fmt.num(x.delta_ic, 3)}</span>` }], { sortKey: null });
    const pa = hx.production_alpha || {};
    table($('#alPr'), [['IC (per asset, excess target)', tt(pa.ic, pa.ic_t)], ['Rank IC (weekly, cross-section)', tt(pa.rank_ic, pa.rank_t)], ['Top − bottom quintile (weekly mean)', `${fmt.spct(pa.quintile_spread, 2)} (t ${fmt.num(pa.quintile_t, 1)})`], ['Top − bottom decile (pooled)', fmt.spct(pa.decile_spread, 1)], ['Hit rate vs weekly median', `${fmt.pct(pa.hit_vs_median, 1)} (t ${fmt.num(pa.hit_t, 1)})`], ['Rank stability week to week', fmt.num(pa.rank_stability, 2)], ['Records', fmt.num(pa.n, 0)]].map(([k, v]) => ({ k, v })), [{ k: 'k', label: 'Metric', l: 1 }, { k: 'v', label: 'Value', f: x => x.v }], { sortKey: null });
    const SG = hx.signals || {};
    table($('#alSg'), Object.entries(SG).map(([s, v]) => ({ s, ...v })), [{ k: 's', label: 'Signal', l: 1 }, { k: 'production', label: 'Production', f: x => fmt.spct(x.production, 1) }, { k: 'alpha', label: 'Alpha challenger', v: x => Math.abs(x.alpha || 0), f: x => `<b class="${scoreCls(x.alpha)}">${fmt.spct(x.alpha, 1)}</b>` }, { k: 'directional', label: 'Directional challenger', f: x => `<span class="${scoreCls(x.directional)}">${fmt.spct(x.directional, 1)}</span>` },
      { k: 'as', label: 'Alpha stable (CV)', f: x => `${x.alpha_same_sign ? '<span class="pill pos" style="font-size:10px">same sign</span>' : '<span class="faint">no</span>'} <span class="faint">${fmt.num(x.alpha_cv, 2)}</span>` }, { k: 'ds', label: 'Directional stable (CV)', f: x => `${x.dir_same_sign ? '<span class="pill pos" style="font-size:10px">same sign</span>' : '<span class="faint">no</span>'} <span class="faint">${fmt.num(x.dir_cv, 2)}</span>` }, { k: 'helps', label: 'Supports', l: 1 }], { sortKey: 'alpha', maxH: 520 });
    $$('#alH button').forEach(b => b.onclick = () => { pref.set('drH', b.dataset.h); route(); });
  };
  const labDirectional = (body, DR, vers) => {
    const { HS, order, hz } = drHz(DR, 'drH'); if (!order.length) return drNeed(body);
    const hx = HS[hz], DD = hx.directional || {};
    const mk = DD[pref.get('drM', '')] ? pref.get('drM', '') : (hx.best_directional || Object.keys(DD)[0]);
    const m = DD[mk] || {}, wf = m.walkforward || {}, cm = wf.confusion || {};
    const prod = (DD['production (read as p = (1 + raw/100)/2)'] || {}).walkforward || {};
    body.innerHTML = `<div class="card"><h2>Shaffer Directional — how likely is the absolute return to be positive? <small>research only · production unchanged</small></h2>
        <p class="muted" style="margin:0 0 8px;font-size:12.5px">p_up = P(return over the horizon &gt; 0 | point-in-time information), from a point-in-time <b>base-return prior</b> plus Shaffer evidence; Directional score = 100 · (2·p_up − 1). <b>Accuracy here always means absolute direction</b> and is shown next to the best naive baseline on the same records (<b>excess accuracy</b>); probabilities are judged by the Brier score against the climatology forecaster (the point-in-time positive-return frequency of the asset's group).</p><div id="drMain"></div></div>
      ${drSeg('drH', order, hz)}<span class="muted" style="font-size:12.5px;margin-left:10px">Model</span><select id="drM">${Object.keys(DD).map(k => `<option value="${esc(k)}" ${k === mk ? 'selected' : ''}>${esc(k)}${k === hx.best_directional ? ' (best Brier)' : ''}</option>`).join('')}</select></div>
      <div class="card flush" style="margin-bottom:14px"><h2>Does Shaffer evidence add to the base prior? <small>paired on the same walk-forward records, weekly-clustered — the gate G1 compares with climatology, which even a calibrated constant beats at 1D; this is the stricter question</small></h2><div id="drPair"></div></div>
      <div class="card flush"><h2>Directional models at ${esc(hz)} <small>walk-forward over the unseen eras · base rate ${fmt.pct((hx.wf_base_rate || {}).all, 1)} positive · naive baseline ${fmt.pct(prod.baseline_acc, 1)} (${esc((prod.baseline || '').replace(/_/g, ' '))})</small></h2><div id="drAll"></div></div>
      <div class="grid g2" style="margin-top:14px"><div class="card flush"><h2>${esc(mk)}: score bands <small>expected vs realized p_up</small></h2><div id="drB"></div></div>
        <div class="card flush"><h2>${esc(mk)}: calibration and calls</h2><div id="drCal"></div>
          <div class="tbl-wrap" style="padding:0 12px"><table><thead><tr><th class="l">Confusion (records)</th><th>Called up</th><th>Called down</th></tr></thead><tbody><tr><td class="l">Went up</td><td>${fmt.num(cm.up_called_up, 0)}</td><td>${fmt.num(cm.up_called_down, 0)}</td></tr><tr><td class="l">Went down</td><td>${fmt.num(cm.down_called_up, 0)}</td><td>${fmt.num(cm.down_called_down, 0)}</td></tr></tbody></table></div>
          <div id="drBear" style="margin-top:8px"></div></div></div>
      <div class="card flush" style="margin-top:14px"><h2>${esc(mk)} by era <small>frozen before each era</small></h2><div id="drEr"></div></div>`;
    table($('#drMain'), order.map(k => { const b = (HS[k].directional || {})[HS[k].best_directional] || {}, w = b.walkforward || {}; const bear = (w.bearish || []).find(x => x.threshold === -20) || {}; return { k, br: (HS[k].wf_base_rate || {}).all, best: HS[k].best_directional, w, bear, st: (b.gates || {}).status, pa: HS[k].production_alpha || {} }; }),
      [{ k: 'k', label: 'H', l: 1, f: x => `<b>${x.k}</b><span class="sub">${esc(x.best || '')}</span>` }, { k: 'br', label: 'Base rate', f: x => fmt.pct(x.br, 1) }, { k: 'aic', label: 'Alpha IC (rank)', f: x => `${fmt.num(x.pa.ic, 3)} <span class="faint">(${fmt.num(x.pa.rank_ic, 3)})</span>` },
        { k: 'acc', label: 'Accuracy', f: x => fmt.pct(x.w.accuracy, 1) }, { k: 'na', label: 'Naive', f: x => `${fmt.pct(x.w.baseline_acc, 1)}<span class="sub">${esc((x.w.baseline || '').replace(/_/g, ' '))}</span>` },
        { k: 'ex', label: 'Excess', f: x => `<span class="${(x.w.excess || 0) > 0 ? 'pos' : 'neg'}">${fmt.spct(x.w.excess, 1)}</span>` }, { k: 'bal', label: 'Balanced', f: x => fmt.pct(x.w.balanced_accuracy, 1) },
        { k: 'bs', label: 'Brier (skill)', f: x => `${fmt.num(x.w.brier, 3)}<span class="sub ${(x.w.brier_skill || 0) > 0 ? 'pos' : 'neg'}">${fmt.spct(x.w.brier_skill, 2)}</span>` }, { k: 'bp', label: 'Bear prec.', f: x => fmt.pct(x.w.bear_precision, 0) }, { k: 'st', label: 'Status', l: 1, f: x => statusPill(x.st) }], { sortKey: null });
    const PR = (hx.paired || []);
    $('#drPair').innerHTML = PR.length ? '' : '<p class="muted" style="padding:12px">Not computed for this run.</p>';
    if (PR.length) table($('#drPair'), PR, [{ k: 'a', label: 'Does …', l: 1, f: x => `<b>${esc(x.a)}</b>` }, { k: 'b', label: '… add to', l: 1, f: x => esc(x.b) }, { k: 'brier_gain', label: 'Brier gain (t)', f: x => `${fmt.num(x.brier_gain, 5)} <span class="faint">(${fmt.num(x.brier_t, 1)})</span>` },
      { k: 'delta_acc', label: 'Δ accuracy (t)', f: x => `${fmt.spct(x.delta_acc, 2)} <span class="faint">(${fmt.num(x.delta_acc_t, 1)})</span>` }, { k: 'auc', label: 'AUC', f: x => `${fmt.num(x.auc_a, 3)} vs ${fmt.num(x.auc_b, 3)}` }, { k: 'adds', label: 'Adds? (t ≥ 2)', l: 1, f: x => x.adds ? '<span class="pill pos" style="font-size:10px">yes</span>' : '<span class="pill" style="font-size:10px">no</span>' }], { sortKey: null });
    table($('#drAll'), Object.entries(DD).map(([k, v]) => ({ k, ...v })), [{ k: 'k', label: 'Model', l: 1, f: x => `<b>${esc(x.k)}</b>` }, { k: 'acc', label: 'Accuracy [95%]', v: x => (x.walkforward || {}).accuracy, f: x => ciPct({ acc: (x.walkforward || {}).accuracy, lo: (x.walkforward || {}).acc_lo, hi: (x.walkforward || {}).acc_hi }) },
      { k: 'ex', label: 'Excess', v: x => (x.walkforward || {}).excess, f: x => `<span class="${((x.walkforward || {}).excess || 0) > 0 ? 'pos' : 'neg'}">${fmt.spct((x.walkforward || {}).excess, 1)}</span>` }, { k: 'bal', label: 'Balanced', f: x => fmt.pct((x.walkforward || {}).balanced_accuracy, 1) },
      { k: 'bs', label: 'Brier skill (t)', v: x => (x.walkforward || {}).brier_skill, f: x => `${fmt.spct((x.walkforward || {}).brier_skill, 2)} <span class="faint">(${fmt.num((x.walkforward || {}).brier_gain_t, 1)})</span>` },
      { k: 'auc', label: 'AUC', f: x => fmt.num((x.walkforward || {}).auc, 3) }, { k: 'ece', label: 'ECE', f: x => fmt.num((x.walkforward || {}).ece, 3) },
      { k: 'bp', label: 'Bear precision / recall', f: x => `${fmt.pct((x.walkforward || {}).bear_precision, 0)} / ${fmt.pct((x.walkforward || {}).bear_recall, 0)}` },
      { k: 'rk', label: 'Rank IC of p_up', f: x => fmt.num((x.ranking || {}).rank_ic, 3) }, { k: 'eras', label: 'Eras won', f: x => `${(x.gates || {}).eras_won ?? '—'}/${(x.gates || {}).eras_complete ?? '—'}` }, { k: 'st', label: 'Status', l: 1, f: x => statusPill((x.gates || {}).status) }], { sortKey: 'bs' });
    table($('#drB'), wf.bands || [], [{ k: 'band', label: 'Score', l: 1 }, { k: 'n', label: 'Records', f: b => fmt.num(b.n, 0) }, { k: 'independent', label: 'Indep.', f: b => fmt.num(b.independent, 0) }, { k: 'expected_p', label: 'Expected p_up', f: b => fmt.pct(b.expected_p, 0) },
      { k: 'realized_p', label: 'Realized p_up [95%]', f: b => b.realized_p != null ? `${fmt.pct(b.realized_p, 0)} <span class="faint">[${fmt.pct(cl01(b.p_lo), 0)}, ${fmt.pct(cl01(b.p_hi), 0)}]</span>` : '—' }, { k: 'mean', label: 'Mean / median return', f: b => b.n ? `${fmt.spct(b.mean, 2)} / ${fmt.spct(b.median, 2)}` : '—' }, { k: 'correct', label: 'Direction right', f: b => fmt.pct(b.correct, 0) }], { sortKey: null });
    table($('#drCal'), wf.calibration || [], [{ k: 'p', label: 'Forecast p_up (decile)', f: c => fmt.pct(c.p, 1) }, { k: 'realized', label: 'Realized', f: c => fmt.pct(c.realized, 1) }, { k: 'n', label: 'Records', f: c => fmt.num(c.n, 0) }], { sortKey: null });
    table($('#drBear'), wf.bearish || [], [{ k: 'threshold', label: 'Bearish call', l: 1, f: b => `score &lt; ${b.threshold}` }, { k: 'n', label: 'Records', f: b => fmt.num(b.n, 0) }, { k: 'p_down', label: 'P(return < 0) [95%]', f: b => b.p_down != null ? `${fmt.pct(b.p_down, 1)} <span class="faint">[${fmt.pct(b.lo, 0)}, ${fmt.pct(b.hi, 0)}]</span>` : '—' }, { k: 'unconditional', label: 'Unconditional', f: b => fmt.pct(b.unconditional, 1) }, { k: 'lift', label: 'Lift', f: b => `<span class="${(b.lift || 0) > 0 ? 'pos' : 'neg'}">${fmt.spct(b.lift, 1)}</span>` }], { sortKey: null });
    table($('#drEr'), (m.eras || []).map(e => ({ ...e, era: `${e.from.slice(0, 4)}–${e.to >= '2100' ? 'now' : String(+e.to.slice(0, 4) - 1)}` })), [{ k: 'era', label: 'Test era', l: 1 }, { k: 'test', label: 'Records', f: e => fmt.num(e.test, 0) }, { k: 'accuracy', label: 'Accuracy vs naive', f: e => e.n ? `${fmt.pct(e.accuracy, 1)} vs ${fmt.pct(e.baseline_acc, 1)}` : `<span class="faint">${esc(e.status || '')}</span>` },
      { k: 'balanced_accuracy', label: 'Balanced', f: e => fmt.pct(e.balanced_accuracy, 1) }, { k: 'brier_skill', label: 'Brier skill (t)', f: e => `${fmt.spct(e.brier_skill, 2)} <span class="faint">(${fmt.num(e.brier_gain_t, 1)})</span>` }, { k: 'auc', label: 'AUC', f: e => fmt.num(e.auc, 3) }], { sortKey: null });
    $$('#drH button').forEach(b => b.onclick = () => { pref.set('drH', b.dataset.h); route(); });
    $('#drM').onchange = e => { pref.set('drM', e.target.value); route(); };
  };
  // New information: genuinely new PIT data, judged by what it adds to the frozen benchmark
  const niPill = s => `<span class="pill ${s === 'SHADOW' || s === 'LIVE SHADOW' || s === 'ELIGIBLE FOR PROMOTION' ? 'pos' : s === 'LIMITED HISTORY' || s === 'INSUFFICIENT DATA' ? 'warn' : s === 'BLOCKED' ? '' : 'neg'}" style="font-size:10.5px">${esc(s || '—')}</span>`;
  const labNewInfo = (body, NI, BM, LV) => {
    if (!NI || !NI.horizons) { body.innerHTML = `<div class="card"><h2>No new-information research yet</h2><p class="muted">Freeze the benchmark and run it: <code>python -m finsim2 lab --freeze-benchmark</code>, then <code>python -m finsim2 lab --newinfo</code>. Sources and their status are in NEW_DATA_SOURCES.md.</p></div>`; return; }
    const HZ = ['1D', '1W', '1M', '3M', '6M', '12M'].filter(k => NI.horizons[k]);
    const hz = HZ.includes(pref.get('niH', '1W')) ? pref.get('niH', '1W') : HZ[0];
    const S = NI.summary || {}, SP = NI.families_spec || {}, bm = NI.benchmark || {};
    const best = (fam, path) => { let b = null; HZ.forEach(k => { const fr = ((NI.horizons[k] || {}).families || {})[fam] || {}; let st = fr.walkforward || {}; path.forEach(p => st = (st || {})[p]); if (st && st.t != null && (!b || st.t > b.t)) b = { ...st, h: k }; }); return b; };
    const tt2 = st => st ? `${fmt.num(st.mean, 4)} <span class="faint">(${st.h ? st.h + ', ' : ''}t ${fmt.num(st.t, 1)})</span>` : '—';
    const hpc = h => h && h.vol_mse_gain && h.vol_mse_gain.mean != null && h.mse_base ? h.vol_mse_gain.mean / h.mse_base : null;   // share of the baseline's squared error
    const hbest = fam => { const b = best(fam, ['hedge', 'vol_mse_gain']); if (!b) return '—'; const pc = hpc((((NI.horizons[b.h] || {}).families || {})[fam] || {}).walkforward?.hedge); return pc != null ? `${fmt.spct(-pc, 1)} <span class="faint">(${b.h}, t ${fmt.num(b.t, 1)})</span>` : tt2(b); };
    const LM = (LV || {}).models || {};
    const liveOf = fam => { const m = Object.values(LM).filter(x => x.family === fam); return !m.length ? null : m.some(x => x.status === 'ELIGIBLE FOR PROMOTION') ? 'ELIGIBLE FOR PROMOTION' : 'LIVE SHADOW'; };
    body.innerHTML = `<div class="card"><h2>New information — does it add anything to the frozen benchmark? <small>research only · production unchanged</small></h2>
        <p class="muted" style="margin:0 0 8px;font-size:12.5px">Benchmark <b>${esc(bm.id || '')}</b> (frozen ${esc(bm.frozen || '')}, sha256 <span class="mono">${esc((bm.hash || '').slice(0, 12))}…</span>): Shaffer Score ${esc((bm.production || {}).score || '')}, Alpha ${esc((bm.production || {}).alpha || '')}, Hedge ${esc((bm.production || {}).hedge || '')}. Every family is tested as <b>incremental</b> information on identical records — Alpha: rank IC over production; Directional (gate v2): beat the prior-only model <i>and</i> the current formulation; Hedge: volatility forecast beyond 63d / 21d realised volatility and VIX — with weights frozen before each era and Benjamini-Hochberg control (q ${fmt.num(NI.fdr_q, 2)}; ${(NI.fdr || {}).family_rejections ?? '—'} of ${(NI.fdr || {}).family_tests ?? '—'} tests survive). Datasets starting after 2018 are on a separate LIMITED HISTORY track.</p><div id="niMain"></div></div>
      <div class="card flush" style="margin-top:14px"><h2>Live shadow <small>only the tests that passed G1, G2 and FDR · recorded daily next to the benchmark · graded at maturity · never production</small></h2><div id="niLive"></div></div>
      <div class="row" style="gap:10px;margin:14px 0 10px"><span class="muted" style="font-size:12.5px">Horizon</span><div class="seg" id="niH">${HZ.map(x => `<button data-h="${x}" class="${x === hz ? 'on' : ''}">${x}</button>`).join('')}</div></div>
      <div class="card flush"><h2>Families at ${esc(hz)} <small>walk-forward, paired on identical records</small></h2><div id="niH_T"></div></div>
      <div class="card flush" style="margin-top:14px"><h2>Individual features at ${esc(hz)} <small>Alpha Δ rank IC t on top of production · nominal p &lt; 0.05 vs surviving the FDR control — features are never admitted one by one</small></h2><div id="niF"></div></div>`;
    const fams = Object.keys(S);
    table($('#niMain'), fams.map(f => ({ f, s: S[f], sp: SP[f] || {} })), [{ k: 'f', label: 'Family', l: 1, f: x => `<b>${esc(x.s.label || x.f)}</b><span class="sub">${esc(x.sp.source || x.s.why || '')}</span>` },
      { k: 'track', label: 'Tier · track', l: 1, f: x => `${x.s.tier ?? '—'} · ${esc(x.s.track || '—')}` },
      { k: 'a', label: 'Alpha Δ rank IC', f: x => tt2(best(x.f, ['alpha', 'd_rank_ic'])) }, { k: 'd', label: 'Dir. Δ Brier', f: x => tt2(best(x.f, ['directional', 'brier_vs_prior'])) },
      { k: 'h', label: 'Vol-forecast error', f: x => hbest(x.f) }, { k: 's', label: 'Status', l: 1, f: x => `${niPill(liveOf(x.f) || x.s.status)}<span class="sub">${esc(x.s.why || '')}</span>` }], { sortKey: null });
    const LR = Object.entries(LM).map(([id, m]) => ({ id, ...m }));
    if (!LR.length) $('#niLive').innerHTML = `<p class="muted" style="padding:12px 16px;margin:0">${Object.values(S).some(x => x.status === 'SHADOW') ? 'Passing tests exist but are not fitted yet: run <code>python -m finsim2 lab --live-models</code>.' : 'No test passed every gate, so nothing is in live shadow.'}</p>`;
    else table($('#niLive'), LR, [{ k: 'id', label: 'Version', l: 1, f: x => `<b class="mono">${esc(x.id)}</b><span class="sub">${esc((SP[x.family] || {}).label || x.family)} · ${esc(x.target)} · ${esc(x.horizon)} · fitted on ${fmt.num(x.records, 0)} records</span>` },
      { k: 'against', label: 'Compared with', l: 1, f: x => esc(x.against || '—') }, { k: 'graded', label: 'Graded / needed', f: x => `${x.graded ?? 0} / ${x.required ?? '—'}` },
      { k: 'gain', label: 'Live gain', f: x => x.target === 'hedge' ? fmt.num(x.mse_gain, 4) : x.target === 'alpha' ? fmt.num(x.d_rank_ic, 3) : fmt.num(x.brier_gain, 4) },
      { k: 'status', label: 'Status', l: 1, f: x => niPill(x.status || 'LIVE SHADOW') }], { sortKey: null });
    const H = (NI.horizons[hz] || {}).families || {};
    table($('#niH_T'), Object.entries(H).map(([f, v]) => ({ f, ...v })), [{ k: 'f', label: 'Family', l: 1, f: x => `<b>${esc((SP[x.f] || {}).label || x.f)}</b><span class="sub">${fmt.num(x.records, 0)} records · ${x.assets || 0} assets · from ${esc(x.first || '—')}</span>` },
      { k: 'a', label: 'Alpha Δ rank IC (t)', f: x => { const st = ((x.walkforward || {}).alpha || {}).d_rank_ic; return st ? `${fmt.num(st.mean, 4)} <span class="faint">(${fmt.num(st.t, 1)})</span>` : '—'; } },
      { k: 'dp', label: 'Brier vs prior (t)', f: x => { const st = ((x.walkforward || {}).directional || {}).brier_vs_prior; return st ? `${fmt.num(st.mean, 5)} <span class="faint">(${fmt.num(st.t, 1)})</span>` : '—'; } },
      { k: 'dc', label: 'vs current (t)', f: x => fmt.num((((x.walkforward || {}).directional || {}).brier_vs_current || {}).t, 1) },
      { k: 'ex', label: 'Δ accuracy vs prior', f: x => fmt.spct(((x.walkforward || {}).directional || {}).excess_vs_prior, 2) },
      { k: 'bal', label: 'Balanced', f: x => fmt.pct((((x.walkforward || {}).directional || {}).new || {}).balanced_accuracy, 1) },
      { k: 'h', label: 'Hedge vol error (t)', f: x => { const hh = (x.walkforward || {}).hedge, st = (hh || {}).vol_mse_gain, pc = hpc(hh); return st ? `${pc != null ? fmt.spct(-pc, 1) : fmt.num(st.mean, 4)} <span class="faint">(${fmt.num(st.t, 1)})</span>` : '—'; } },
      { k: 'eras', label: 'Eras won A / D / H', f: x => { const g = x.gates || {}; return `${(g.alpha || {}).eras_won ?? '—'} / ${(g.directional || {}).eras_won ?? '—'} / ${(g.hedge || {}).eras_won ?? '—'} of ${g.eras_complete ?? '—'}`; } },
      { k: 'g', label: 'Passed', l: 1, f: x => ['alpha', 'directional', 'hedge'].map(t => { const g = (x.gates || {})[t]; return g ? `<span class="pill ${g.passed ? 'pos' : g.G1 ? 'warn' : ''}" style="font-size:10px">${t.slice(0, 3)} ${g.passed ? '✓' : g.G1 ? 'G1 only' : '✗'}</span>` : ''; }).join(' ') }], { sortKey: null });
    const F = []; Object.entries(H).forEach(([f, v]) => Object.entries(v.features || {}).forEach(([n, st]) => F.push({ f, n, ...st })));
    table($('#niF'), F, [{ k: 'n', label: 'Feature', l: 1, f: x => `<b>${esc(x.n)}</b><span class="sub">${esc((SP[x.f] || {}).label || x.f)}</span>` }, { k: 'coverage', label: 'Coverage', f: x => fmt.pct(x.coverage, 0) },
      { k: 't', label: 'Δ rank IC t', v: x => x.t, f: x => fmt.num(x.t, 1) }, { k: 'p', label: 'p (one-sided)', f: x => fmt.num(x.p, 3) },
      { k: 'fdr', label: 'Result', l: 1, f: x => x.fdr ? '<span class="pill pos" style="font-size:10px">survives FDR</span>' : x.nominal ? '<span class="pill warn" style="font-size:10px">nominal only</span>' : '<span class="faint">no</span>' }], { sortKey: 't', maxH: 420 });
    $$('#niH button').forEach(b => b.onclick = () => { pref.set('niH', b.dataset.h); route(); });
  };
  // Breadth volatility → Shaffer Hedge impact: the complete hedge chain, production vs the breadth-vol challenger
  const bhPill = s => `<span class="pill ${s === 'SHADOW' || s === 'LIVE SHADOW' || s === 'ELIGIBLE FOR PROMOTION' ? 'pos' : s === 'IMPROVES FORECAST ONLY' || s === 'INSUFFICIENT DATA' ? 'warn' : 'neg'}" style="font-size:10.5px">${esc(s || '—')}</span>`;
  const labBreadthHedge = (el, BH) => {
    if (!BH || !BH.horizons) { el.innerHTML = `<div class="card"><h2>Breadth volatility → hedge impact <small>research only</small></h2><p class="muted" style="margin:0">Not run yet: <code>python -m finsim2 lab --breadth-hedge</code> replays the complete Shaffer Hedge point in time with production's volatility and with the breadth-enhanced forecast, and grades the realised hedges.</p></div>`; return; }
    const HZ = Object.keys(BH.horizons);
    const hz = HZ.includes(pref.get('bhH', '1W')) ? pref.get('bhH', '1W') : HZ[0];
    const LAMS = (BH.lambdas || [0.5, 1, 2, 5, 10]).map(String);
    const lam = LAMS.includes(pref.get('bhL', '1')) ? pref.get('bhL', '1') : '1';
    const lk = Number.isInteger(Number(lam)) ? Number(lam).toFixed(1) : lam;      // Python's str(λ) keys
    const Hh = BH.horizons[hz] || {}, F = Hh.forecast || {};
    const U = (o, l) => o && o.loss_reduction != null ? o.loss_reduction - Number(l) * o.profit_sacrificed - o.cost : null;
    el.innerHTML = `<div class="card"><h2>Breadth volatility → hedge impact <small>research only · hedge-2 and its sizing unchanged · ${esc((BH.benchmark || {}).id || '')}</small></h2>
        <p class="muted" style="margin:0 0 8px;font-size:12.5px">A better volatility forecast is not a better hedge. Every case replays the complete Shaffer Hedge point in time — forecast → covariance → candidates → sizing → optimiser → package → realised P&amp;L — twice: <b>production</b> (hedge-2, 252-day covariance) and the <b>breadth-vol challenger</b> (the market factor's variance from the breadth-enhanced forecast), identical otherwise. Judged on realised utility U = risk reduction − λ·profit sacrificed − cost, paired on the same book, date, objective, products, prices and costs; clustered by date. Options are MODEL-PRICED — FLAT VOLATILITY ASSUMPTION.</p>
        <div class="row" style="gap:10px;margin:6px 0 10px"><span class="muted" style="font-size:12.5px">Horizon</span><div class="seg" id="bhH">${HZ.map(x => `<button data-h="${x}" class="${x === hz ? 'on' : ''}">${x}</button>`).join('')}</div>
          <span class="muted" style="font-size:12.5px;margin-left:12px">λ (profit weight)</span><div class="seg" id="bhL">${LAMS.map(x => `<button data-l="${x}" class="${x === lam ? 'on' : ''}">${x}</button>`).join('')}</div></div>
        <p style="margin:0 0 10px;font-size:12.5px">Forecast (market factor, ${esc(hz)}, ${F.n || 0} study dates): squared log error production ${fmt.num(F.mse_production, 4)} · regression without breadth ${fmt.num(F.mse_reference, 4)} · breadth ${fmt.num(F.mse_breadth, 4)} (${fmt.spct(F.gain_pct != null ? -F.gain_pct : null, 1)} vs the regression, t ${fmt.num(F.t, 1)}) · pooled newinfo t ${fmt.num(Hh.pooled_t, 1)} · G1 ${Hh.G1 ? '<span class="pill pos" style="font-size:10px">passes</span>' : '<span class="pill neg" style="font-size:10px">fails</span>'}</p>
        <div id="bhT"></div></div>`;
    const rows = Object.entries(Hh.cells || {}).filter(([, c]) => (c.test || {}).n).map(([obj, c]) => ({ obj, ...c }));
    table($('#bhT'), rows, [{ k: 'obj', label: 'Objective · status · forecast vol', l: 1, f: x => `<b>${esc(x.obj)}</b> <span title="${esc(x.why || '')}">${bhPill(x.status)}</span><span class="sub">${x.dates} dates · vol ${fmt.pct((x.forecast_vol || {}).production, 1)} prod / ${fmt.pct((x.forecast_vol || {}).challenger, 1)} breadth</span>` },
      { k: 'prod', label: 'Product · size · cost', l: 1, f: x => { const top = Object.entries(x.by_product || {}).sort((a, b) => b[1].cases - a[1].cases)[0]; return `${esc(top ? top[0] : '—')}<span class="sub">product changed ${fmt.pct((x.decisions || {}).product, 0)} · size ×${fmt.num(((x.extended || {}).size_ratio || x.size_ratio || {}).median, 2)} · cost ${fmt.money(x.test.a.cost)} / ${fmt.money(x.test.b.cost)}</span>`; } },
      { k: 'u', label: `Utility λ ${lam}`, f: x => `${fmt.money(U(x.test.a, lam))} · ${fmt.money((((x.extended || {}).U || {}).nobreadth || {})[lk])} · ${fmt.money(U(x.test.b, lam))}<span class="sub">prod · no-breadth · breadth</span>` },
      { k: 'd', label: 'Δ breadth − prod', v: x => (x.test.d || {})[lk], f: x => { const d = (x.test.d || {})[lk], ci = (x.test.ci || {})[lk]; return `<b class="${d > 0 ? 'pos' : d < 0 ? 'neg' : ''}">${fmt.money(d)}</b>${ci ? `<span class="sub">[${fmt.money(ci[0])}, ${fmt.money(ci[1])}]</span>` : ''}`; } },
      { k: 'dbr', label: 'Δ breadth − no-breadth', f: x => { const d = ((((x.extended || {}).d || {}).breadth_vs_nobreadth) || {})[lk]; return `<span class="${d > 0 ? 'pos' : d < 0 ? 'neg' : ''}">${fmt.money(d)}</span>`; } },
      { k: 'eras', label: 'Eras +', f: x => `${(x.gates || {}).eras_won ?? '—'}/${(x.gates || {}).eras_complete ?? '—'}` }], { sortKey: null, maxH: 900 });
    $$('#bhH button').forEach(b => b.onclick = () => { pref.set('bhH', b.dataset.h); route(); });
    $$('#bhL button').forEach(b => b.onclick = () => { pref.set('bhL', b.dataset.l); route(); });
  };
  // Shaffer vNext: the three research programs (Alpha / Directional / Hedge), each against fixed production
  const vPill = s => `<span class="pill ${s === 'LIVE SHADOW ELIGIBLE' ? 'pos' : s === 'INSUFFICIENT DATA' ? 'warn' : s === 'production' || s === 'benchmark' ? '' : 'neg'}" style="font-size:10.5px">${esc(s || '—')}</span>`;
  const vNone = (el, what, cmd) => { el.innerHTML = `<div class="card"><h2>${what} <small>research only</small></h2><p class="muted" style="margin:0">Not run yet: <code>${cmd}</code>.</p></div>`; };
  const segH = (id, HZ, hz) => `<div class="row" style="gap:10px;margin:0 0 10px"><span class="muted" style="font-size:12.5px">Horizon</span><div class="seg" id="${id}">${HZ.map(x => `<button data-h="${x}" class="${x === hz ? 'on' : ''}">${x}</button>`).join('')}</div></div>`;
  const labAlphaNext = (body, AN) => {
    if (!AN || !AN.horizons) return vNone(body, 'Shaffer Alpha vNext', 'python -m finsim2 lab --vnext alpha');
    const HZ = Object.keys(AN.horizons), hz = HZ.includes(pref.get('anH', '1M')) ? pref.get('anH', '1M') : HZ[0], H = AN.horizons[hz];
    const rows = [{ n: 'production', m: (H.production || {}).walkforward || {}, ls: (H.production || {}).ls || {}, u: (H.production || {}).useful || {}, status: 'production' }]
      .concat(Object.entries(H.challengers || {}).map(([n, c]) => ({ n, m: (c.walkforward || {}).challenger || {}, ls: (c.walkforward || {}).ls_challenger || {}, pr: (c.walkforward || {}).paired || {}, g: c.gates || {}, u: c.useful || {}, status: c.status, id: c.id })));
    body.innerHTML = `<div class="card"><h2>Shaffer Alpha vNext <small>which assets will beat or trail the others over this horizon? · new information on top of production · research only</small></h2>
      <p class="muted" style="margin:0 0 10px;font-size:12.5px">Challengers add first-reported SEC fundamentals, earnings events, sector-relative structure, macro × beta and breadth × beta to the production score, fitted per horizon on records that matured before each unseen era. Judged cross-sectionally: rank IC, spreads, net-of-cost long-short, eras and FDR. Blocked (no PIT history): ${esc((AN.blocked || []).join(', '))}.</p>
      ${segH('anH', HZ, hz)}<div id="anT"></div></div>
      <div class="card flush" style="margin-top:14px"><h2>Conviction <small>does a larger |Alpha| mean a larger relative return? production</small></h2><div id="anC"></div></div>`;
    table($('#anT'), rows, [{ k: 'n', label: 'Model', l: 1, f: x => `<b>${esc(x.n)}</b>${x.id ? `<span class="sub mono">${esc(x.id)}</span>` : ''}` },
      { k: 'ric', label: 'Rank IC (t)', f: x => `${fmt.num(x.m.rank_ic, 4)} <span class="faint">(${fmt.num(x.m.rank_t, 1)})</span>` },
      { k: 'd', label: 'Δ vs production (t)', f: x => x.pr ? `<span class="${x.pr.mean > 0 ? 'pos' : 'neg'}">${fmt.num(x.pr.mean, 4)}</span> <span class="faint">(${fmt.num(x.pr.t, 1)})</span>` : '—' },
      { k: 'dec', label: 'Decile spread', f: x => fmt.spct(x.m.decile_spread, 2) },
      { k: 'ls', label: 'Long-short net / yr', f: x => `${fmt.spct(x.ls.net_annual, 1)}<span class="sub">t ${fmt.num(x.ls.t, 1)}</span>` },
      { k: 'eras', label: 'Eras Δ > 0', f: x => x.g ? `${x.g.eras_won}/${x.g.eras_complete}` : '—' },
      { k: 'use', label: 'Horizon useful?', f: x => x.u.useful ? '<span class="pill pos" style="font-size:10px">yes</span>' : '<span class="faint">no</span>' },
      { k: 's', label: 'Status', l: 1, f: x => vPill(x.status) }], { sortKey: null });
    const cv = ((H.production || {}).conviction || {});
    table($('#anC'), cv.buckets || [], [{ k: 'bucket', label: '|Alpha|', l: 1 }, { k: 'n', label: 'Records', f: b => fmt.num(b.n, 0) }, { k: 'rr', label: 'Signed relative return', f: b => fmt.spct(b.relative_return, 3) },
      { k: 'hit', label: 'Hit', f: b => fmt.pct(b.hit, 1) }, { k: 'ric', label: 'Rank IC', f: b => fmt.num(b.rank_ic, 3) }, { k: 'dd', label: 'Drawdown', f: b => fmt.spct(b.drawdown, 1) }], { sortKey: null, empty: 'no buckets' });
    $$('#anH button').forEach(b => b.onclick = () => { pref.set('anH', b.dataset.h); route(); });
  };
  const labDirNext = (body, DX) => {
    if (!DX || !DX.horizons) return vNone(body, 'Shaffer Directional vNext', 'python -m finsim2 lab --vnext directional');
    const HZ = Object.keys(DX.horizons), hz = HZ.includes(pref.get('dnH', '1D')) ? pref.get('dnH', '1D') : HZ[0], wf = DX.horizons[hz].walkforward;
    const rows = [{ n: 'prior-only (base prior)', m: wf.prior, cal: wf.prior_calibration || {}, status: 'benchmark' }, { n: 'current (prior + production)', m: wf.current, cal: {}, status: 'benchmark' }]
      .concat(Object.entries(wf.challengers || {}).map(([n, c]) => ({ n, m: c.metrics, cal: c.calibration || {}, vp: c.vs_prior, bal: c.balanced_gain, adj: c.adjustment, g: c.gates || {}, status: c.status })));
    body.innerHTML = `<div class="card"><h2>Shaffer Directional vNext <small>P(up) beyond the PIT base prior · short horizons · research only</small></h2>
      <p class="muted" style="margin:0 0 10px;font-size:12.5px">Base prior, Shaffer adjustment and final p are kept apart. A challenger must beat the prior-only model on Brier, log loss, balanced accuracy <i>and</i> calibration on unseen history — raw accuracy never qualifies it. 1M: ${esc(DX['1M'] || '')}.</p>
      ${segH('dnH', HZ, hz)}<div id="dnT"></div></div>`;
    table($('#dnT'), rows, [{ k: 'n', label: 'Model', l: 1, f: x => `<b>${esc(x.n)}</b>` }, { k: 'b', label: 'Brier', f: x => fmt.num(x.m.brier, 4) },
      { k: 'g', label: 'Brier gain vs prior (t)', f: x => x.vp ? `<span class="${x.vp.brier_gain > 0 ? 'pos' : 'neg'}">${fmt.num(x.vp.brier_gain, 5)}</span> <span class="faint">(${fmt.num(x.vp.brier_t, 1)})</span>` : '—' },
      { k: 'ba', label: 'Balanced accuracy', f: x => `${fmt.pct(x.m.balanced_accuracy, 1)}${x.bal != null ? `<span class="sub">${fmt.spct(x.bal, 2)} vs prior</span>` : ''}` },
      { k: 'ece', label: 'ECE · slope', f: x => `${fmt.num(x.m.ece, 4)}<span class="sub">slope ${fmt.num(x.cal.slope, 2)}</span>` },
      { k: 'adj', label: 'Mean |Shaffer adjustment|', f: x => x.adj ? `${fmt.pct(x.adj.mean_abs, 2)}<span class="sub">${fmt.pct(x.adj.share_over_2pp, 0)} of calls move &gt; 2pp</span>` : '—' },
      { k: 'e', label: 'Eras +', f: x => x.g ? `${x.g.eras_won ?? '—'}/${x.g.eras_complete ?? '—'}` : '—' }, { k: 's', label: 'Status', l: 1, f: x => vPill(x.status) }], { sortKey: null });
    $$('#dnH button').forEach(b => b.onclick = () => { pref.set('dnH', b.dataset.h); route(); });
  };
  const labHedgeNext = (body, HX) => {
    if (!HX || !HX.tests) return vNone(body, 'Shaffer Hedge vNext', 'python -m finsim2 lab --vnext hedge');
    const TL = { risk: 'Risk (EWMA Σ)', sizing_obj: 'Sizing per objective', sizing_regime: 'Sizing per regime', product: 'Product choice', alpha: 'Alpha → hedge' };
    const TS = Object.keys(HX.tests).filter(t => Object.keys(HX.tests[t] || {}).length), t = TS.includes(pref.get('hnT', 'risk')) ? pref.get('hnT', 'risk') : TS[0];
    const HZ = Object.keys(HX.tests[t] || {}), hz = HZ.includes(pref.get('hnH', '1W')) ? pref.get('hnH', '1W') : HZ[0];
    const U = o => o && o.loss_reduction != null ? o.loss_reduction - o.profit_sacrificed - o.cost : null;
    const rows = Object.entries((HX.tests[t] || {})[hz] || {}).filter(([, c]) => c.cases).map(([o, c]) => ({ o, ...c }));
    body.innerHTML = `<div class="card"><h2>Shaffer Hedge vNext <small>economically better protection? realised utility against hedge-2 · research only</small></h2>
      <p class="muted" style="margin:0 0 10px;font-size:12.5px">U = risk reduction − λ·profit sacrificed − cost (λ = 1 here), $ per $1M book over the horizon, paired with hedge-2 on identical cases, clustered by date, FDR across every hedge test. Options are MODEL-PRICED — FLAT VOLATILITY ASSUMPTION.</p>
      <div class="row" style="gap:10px;margin:0 0 10px"><span class="muted" style="font-size:12.5px">Experiment</span><div class="seg" id="hnT">${TS.map(x => `<button data-t="${x}" class="${x === t ? 'on' : ''}">${TL[x] || x}</button>`).join('')}</div></div>
      ${segH('hnH', HZ, hz)}<div id="hnB"></div></div>`;
    table($('#hnB'), rows, [{ k: 'o', label: 'Objective', l: 1, f: x => `<b>${esc(x.o)}</b><span class="sub">${x.dates} dates · ${x.cases} cases</span>` },
      { k: 'u', label: 'U hedge-2 / challenger', f: x => `${fmt.money(U(x.a))} / ${fmt.money(U(x.b))}` },
      { k: 'd', label: 'ΔU (95% CI)', v: x => (x.d || {})['1.0'], f: x => { const d = (x.d || {})['1.0'], ci = (x.ci || {})['1.0']; return `<b class="${d > 0 ? 'pos' : d < 0 ? 'neg' : ''}">${fmt.money(d)}</b>${ci ? `<span class="sub">[${fmt.money(ci[0])}, ${fmt.money(ci[1])}]</span>` : ''}`; } },
      { k: 'rr', label: 'Risk reduction', f: x => `${fmt.money((x.a || {}).loss_reduction)} / ${fmt.money((x.b || {}).loss_reduction)}` },
      { k: 'ps', label: 'Profit sacrificed · cost', f: x => `${fmt.money((x.b || {}).profit_sacrificed)} · ${fmt.money((x.b || {}).cost)}<span class="sub">hedge-2 ${fmt.money((x.a || {}).profit_sacrificed)} · ${fmt.money((x.a || {}).cost)}</span>` },
      { k: 'ch', label: 'Size / product changed', f: x => `${fmt.pct(x.changed, 0)} / ${fmt.pct(x.product_changed, 0)}` },
      { k: 'e', label: 'Eras +', f: x => `${(x.gates || {}).eras_won ?? '—'}/${(x.gates || {}).eras_complete ?? '—'}` }, { k: 's', label: 'Status', l: 1, f: x => x.status === 'NO HEDGE IMPROVEMENT' ? ((x.gates || {}).fdr ? `<span class="pill warn" style="font-size:10.5px" title="survived FDR, failed ${['H2', 'H3', 'H4', 'H5'].filter(k => !(x.gates || {})[k]).join(', ')}">FDR ✓ · gate ✗</span>` : '<span class="pill neg" style="font-size:10.5px">no gain</span>') : vPill(x.status) }], { sortKey: null });
    $$('#hnT button').forEach(b => b.onclick = () => { pref.set('hnT', b.dataset.t); route(); });
    $$('#hnH button').forEach(b => b.onclick = () => { pref.set('hnH', b.dataset.h); route(); });
  };
  pages.ml = async (main, args, alive) => {
    const tab = args[0] || pref.get('labTab', 'production');
    if (tab === 'forecasts') return mlForecasts(main, args.slice(1), alive);
    pref.set('labTab', tab);
    const [L, hl] = await Promise.all([api('/fs2/lab'), api('/fs2/health').catch(() => null)]); if (!alive()) return;
    const R = L.research || {}, H = R.horizons || {}, HZL = Object.keys(H).filter(k => H[k].variants);
    const hz = HZL.includes(pref.get('labH', '1M')) ? pref.get('labH', '1M') : (HZL[0] || '1M');
    const V = pref.get('labV', 'hier');
    const rec = (L.records || []).reduce((a, r) => ({ n: a.n + (r.records || 0), last: r.created > a.last ? r.created : a.last }), { n: 0, last: '' });
    main.innerHTML = `<div class="page-head"><div><h1>ML Lab</h1><p>The laboratory that tests and refines the <b>Shaffer Score</b> and the <b>Shaffer Hedge</b>. It never trades and never changes production by itself: it proposes challengers, tests them on data they never saw, runs them in live shadow, and a challenger becomes production only after it passes every gate <i>and</i> you promote it.</p></div>
      <div class="row"><button id="labRun" class="primary">Run weight research</button><button id="labBuild">Rebuild research records</button></div></div>
      <div id="labJob"></div>
      <div class="muted" style="font-size:12.5px;margin:-6px 0 10px">Research records: ${fmt.num(rec.n, 0)} point-in-time scored dates with their outcomes${rec.last ? ` (built ${esc(rec.last.slice(0, 16))})` : ''} · last research run ${esc(R.started || 'never')} · discovery before ${esc((R.confirm_from || '2018').slice(0, 4))}, confirmation from it · shrinkage toward the parent node: ${fmt.num(R.shrink_k, 0)} effective observations</div>
      <div class="tabs grouped">${LAB_GROUPS.map(([g, ks]) => `<span class="tgroup"><span class="tabgrp">${esc(g)}</span>` + ks.map(k => `<a class="tab ${k === tab ? 'on' : ''}" href="#/ml/${k}">${LAB_NAME[k]}</a>`).join('') + '</span>').join('')}</div>
      ${['performance', 'signals', 'weights'].includes(tab) && HZL.length ? `<div class="row" style="gap:10px;margin:12px 0"><span class="muted" style="font-size:12.5px">Horizon</span><div class="seg" id="labH">${HZL.map(x => `<button data-h="${x}" class="${x === hz ? 'on' : ''}">${x}</button>`).join('')}</div>
        ${tab === 'weights' ? `<span class="muted" style="font-size:12.5px;margin-left:10px">Weighting</span><div class="seg" id="labV">${[['global', 'one set'], ['class', 'by class'], ['hier', 'class → sector → industry → asset'], ['regime', 'by volatility regime']].map(([k, l]) => `<button data-v="${k}" class="${k === V ? 'on' : ''}">${l}</button>`).join('')}</div>` : ''}</div>` : ''}
      <div id="labBody" style="margin-top:12px"></div>`;
    $('#labRun').onclick = () => busy($('#labRun'), async () => { const j = await post('/fs2/lab/run', {}); pollJob(j.id, () => route(), $('#labJob')); });
    $('#labBuild').onclick = () => busy($('#labBuild'), async () => { const j = await post('/fs2/lab/run', { build: true }); pollJob(j.id, () => route(), $('#labJob')); });
    $$('#labH button').forEach(b => b.onclick = () => { pref.set('labH', b.dataset.h); route(); });
    $$('#labV button').forEach(b => b.onclick = () => { pref.set('labV', b.dataset.v); route(); });
    const body = $('#labBody');
    const fams = R.families || [];
    const need = () => { body.innerHTML = `<div class="card"><h2>No research yet</h2><p class="muted">Rebuild the research records (a point-in-time replay of the Shaffer Score over the whole universe — about half an hour) and run the weight research, or run <code>python -m finsim2 lab --build</code>.</p></div>`; };
    const vers = L.versions || [];
    if (tab === 'production') {
      const sp = vers.find(v => v.kind === 'shaffer' && v.status === 'production') || {}, hp = vers.find(v => v.kind === 'hedge' && v.status === 'production') || {};
      const hrows = ((hl || {}).rows || []).filter(x => /^Shaffer|^Hedge ML/.test(x.engine));
      const hv = Object.values(L.hedge_ml || {}).reduce((a, g) => a + Object.values(g).filter(r => r.verified).length, 0);
      body.innerHTML = `<div class="grid g2"><div class="card"><h2>Shaffer Score in production <span class="pill pos">${esc(sp.id || '—')}</span></h2><p class="muted">${esc(sp.description || '')}</p><div class="mono" style="font-size:12px;overflow:auto">${esc(sp.formula || '')}</div>
          <p class="faint" style="font-size:12px">Families: ${Object.keys(sp.families || {}).length} · weights are set by evidence at every date (W = evidence × validation, A = applicability, H = horizon fit) — the table below is the say each family actually had.</p></div>
        <div class="card"><h2>Shaffer Hedge in production <span class="pill pos">${esc(hp.id || '—')}</span></h2><p class="muted">${esc(hp.description || '')}</p><div class="mono" style="font-size:12px">${esc(hp.formula || '')}</div>
          <p class="faint" style="font-size:12px">Objective-specific ML adjustments verified: ${hv} group × objective pair(s)${hp.multiples ? ` · promoted sizing multiples for ${Object.keys(hp.multiples).length} groups` : ''}. <a href="#/ml/hedge">Hedge research →</a></p></div></div>
        <div class="card flush" style="margin-top:14px"><h2>Each family's actual say in the production score <small>mean share of |contribution| in the scored records, before ${esc((R.confirm_from || '2018').slice(0, 4))} → from it</small></h2><div id="labPW"></div></div>
        <div class="card flush" style="margin-top:14px"><h2>Health <small>from <a href="#/health">Model health</a></small></h2><div id="labHl"></div></div>`;
      if (HZL.length) table($('#labPW'), fams.map(f => ({ f })), [{ k: 'f', label: 'Family', l: 1 }, ...HZL.map(k => ({ k, label: k, v: x => (H[k].production_weights_recent || {})[x.f], f: x => `${fmt.pct((H[k].production_weights || {})[x.f], 0)} → <b>${fmt.pct((H[k].production_weights_recent || {})[x.f], 0)}</b>` }))], { sortKey: HZL.includes('1M') ? '1M' : HZL[0] });
      else $('#labPW').innerHTML = '<p class="muted" style="padding:12px">Run the weight research to see it.</p>';
      table($('#labHl'), hrows, [{ k: 'engine', label: 'Engine', l: 1 }, { k: 'horizon', label: 'Horizon / group', l: 1 }, { k: 'status', label: 'Status', l: 1, f: x => `<span class="pill ${x.status === 'HEALTHY' ? 'pos' : x.status === 'DECAYING' ? 'neg' : x.status === 'WEAKENING' || x.status === 'INSUFFICIENT DATA' ? 'warn' : ''}" style="font-size:10.5px">${esc(x.status)}</span>` }], { sortKey: null });
      return;
    }
    if (tab === 'sigweights') return labSignalWeights(body, L.weights, vers);
    if (tab === 'newinfo') return labNewInfo(body, L.newinfo, L.benchmark, L.newinfo_live);
    if (tab === 'alpha') return labAlpha(body, L.directional, vers);
    if (tab === 'alphanext') return labAlphaNext(body, L.alphanext);
    if (tab === 'dirnext') return labDirNext(body, L.dirnext);
    if (tab === 'hedgenext') return labHedgeNext(body, L.hedgenext);
    if (tab === 'directional') return labDirectional(body, L.directional, vers);
    if (!HZL.length && ['performance', 'signals', 'weights', 'versions'].includes(tab)) return need();
    const hx = H[hz] || {};
    if (tab === 'performance') {
      const P = hx.performance || {}, a = P.all || {};
      const grp = (el, obj, label) => table(el, Object.entries(obj || {}).map(([k, v]) => ({ k, ...v })), [{ k: 'k', label, l: 1 }, { k: 'n', label: 'Records', f: x => fmt.num(x.n, 0) }, { k: 'ic', label: 'IC (date-clustered t)', v: x => x.ic, f: x => icT(x) }, { k: 'hit', label: 'Hit rate (|score| ≥ 5)', v: x => x.hit, f: x => fmt.pct(x.hit, 0) }], { sortKey: 'ic' });
      body.innerHTML = `<div class="tiles">${kpi('Out-of-sample IC', fmt.num(a.ic, 3), `date-clustered t ${fmt.num(a.t, 1)} · ${fmt.num(a.weeks, 0)} weeks`)}${kpi('Hit rate', fmt.pct(a.hit, 0), 'sign right when |score| ≥ 5')}${kpi('Monotonicity', fmt.num(a.monotonicity, 2), 'deciles of the score vs what followed (1 = perfect)')}${kpi('Top − bottom decile', fmt.spct(a.top_minus_bottom, 1), `mean ${esc(hz)} return spread`)}${kpi('Records', fmt.num(hx.records, 0), `${hx.assets} assets · ${esc(hx.first || '')} → ${esc(hx.last || '')}`)}</div>
        <p class="muted" style="font-size:12.5px">Every record is the production Shaffer Score as it was computed on its date with only earlier data, then what actually happened over ${esc(hz)}.</p>
        <div class="grid g3"><div class="card flush"><h2>By asset class</h2><div id="pfC"></div></div><div class="card flush"><h2>By sector</h2><div id="pfS"></div></div><div class="card flush"><h2>By volatility regime</h2><div id="pfR"></div></div></div>
        <div class="card flush" style="margin-top:14px"><h2>By asset <small>sorted by IC; single assets are noisy — read the groups first</small></h2><div id="pfA"></div></div>`;
      grp($('#pfC'), P.class, 'Class'); grp($('#pfS'), P.sector, 'Sector'); grp($('#pfR'), P.regime, 'Regime'); grp($('#pfA'), P.asset, 'Asset');
      return;
    }
    if (tab === 'signals') {
      const F = hx.families || {}, SG = (R.signals || {})[hz] || {};
      body.innerHTML = `<div class="card flush"><h2>Families: do they work? <small>own out-of-sample IC of each family's score, date-clustered, before ${esc((R.confirm_from || '2018').slice(0, 4))} and from it</small></h2><div id="sgF"></div></div>
        <div class="card flush" style="margin-top:14px"><h2>Signals: working, decaying or useless? <small>across every cached asset at the latest refit: how often the signal survives the multiple-testing control, and its decay status</small></h2><div id="sgS"></div></div>`;
      table($('#sgF'), Object.entries(F).map(([f, v]) => ({ f, ...v })), [{ k: 'f', label: 'Family', l: 1 }, { k: 'b', label: 'Before', v: x => (x.before || {}).ic, f: x => icT(x.before) }, { k: 'a', label: 'From', v: x => (x.from || {}).ic, f: x => icT(x.from) },
        { k: 'v', label: 'Verdict', l: 1, f: x => { const tb = (x.before || {}).t, ta = (x.from || {}).t; return tb >= 2 && ta >= 1 ? '<span class="pill pos">works, and held up</span>' : tb >= 2 ? '<span class="pill warn">worked, faded</span>' : ta >= 2 ? '<span class="pill warn">recent only</span>' : (tb <= -2 || ta <= -2) ? '<span class="pill neg">negative record</span>' : '<span class="pill">no measurable value</span>'; } }], { sortKey: 'a' });
      table($('#sgS'), Object.entries(SG).map(([s, v]) => ({ s, ...v })), [{ k: 's', label: 'Signal', l: 1, f: x => `<b>${esc(x.s)}</b><span class="sub">${esc(x.family || '')}</span>` }, { k: 'assets', label: 'Assets' }, { k: 'active', label: 'Active', v: x => x.active / (x.assets || 1), f: x => fmt.pct(x.active / (x.assets || 1), 0) },
        { k: 'HEALTHY', label: 'Healthy' }, { k: 'WEAKENING', label: 'Weakening' }, { k: 'DECAYING', label: 'Decaying' }, { k: 'mean_ic', label: 'Mean IC', f: x => fmt.num(x.mean_ic, 3) }, { k: 'verdict', label: 'Verdict', l: 1 }], { sortKey: 'DECAYING' });
      return;
    }
    if (tab === 'weights') {
      const s = (hx.variants || {})[V];
      if (!s) return need();
      const c = s.confirmation || {}, w = s.walkforward || {}, g = s.gates || {};
      const rows = [['Confirmation: weights frozen at discovery, scored from ' + (R.confirm_from || '').slice(0, 4), c], ['Walk-forward (yearly refits) before ' + (R.confirm_from || '').slice(0, 4), w.before], ['Walk-forward from ' + (R.confirm_from || '').slice(0, 4), w.from]];
      const nodes = Object.keys(s.weights_share || {}).filter(k => k !== 'global').sort();
      body.innerHTML = `<div class="card"><h2>Current production vs the ML-proposed weighting <small>${esc(V)} · ${esc(hz)}</small></h2>
          <div class="row" style="gap:8px;margin-bottom:8px">${[['G1_discovery', 'G1 discovery (walk-forward before ' + (R.confirm_from || '').slice(0, 4) + ', paired t ≥ 2)'], ['G2_confirmation', 'G2 confirmation (untouched, paired t ≥ 1, monotonicity kept)']].map(([k, l]) => `<span class="pill ${g[k] ? 'pos' : 'neg'}">${g[k] ? '✓' : '✗'} ${esc(l)}</span>`).join('')}<span class="pill warn">G3 live shadow: see Challengers</span></div>
          <div class="tbl-wrap"><table><thead><tr><th class="l">Test period</th><th>Production IC</th><th>Challenger IC</th><th>Challenger − production (paired, clustered)</th><th>Hit rate prod → ch.</th><th>Monotonicity prod → ch.</th><th>Top − bottom prod → ch.</th></tr></thead><tbody>
          ${rows.map(([l, x]) => { x = x || {}; const p = x.production || {}, ch = x.challenger || {}, d = x.paired || {}; return `<tr><td class="l">${esc(l)}</td><td>${icT(p)}</td><td>${icT(ch)}</td><td class="${(d.ic || 0) > 0 ? 'pos' : 'neg'}">${icT(d)}</td><td>${fmt.pct(p.hit, 0)} → ${fmt.pct(ch.hit, 0)}</td><td>${fmt.num(p.monotonicity, 2)} → ${fmt.num(ch.monotonicity, 2)}</td><td>${fmt.spct(p.top_minus_bottom, 1)} → ${fmt.spct(ch.top_minus_bottom, 1)}</td></tr>`; }).join('')}</tbody></table></div>
          <p class="faint" style="font-size:12px">Records: ${fmt.num(s.discovery_records, 0)} in discovery. ICs are date-clustered (each week's evidence averaged across assets). The challenger index is Σ w·x over the production families' scores, fitted by ridge regression shrunk toward the parent node.</p></div>
        <div class="card flush" style="margin-top:14px"><h2>Weights <small>production: each family's actual share of the score · challenger: signed share of Σ|w| on standardised family scores (fitted on everything matured; discovery-only in brackets)</small></h2><div id="wtT"></div></div>
        <div class="card flush" style="margin-top:14px"><h2>Where does specialisation help? <small>walk-forward IC, production → challenger</small></h2><div class="grid g3" style="padding:0 12px 12px"><div id="wgC"></div><div id="wgS"></div><div id="wgR"></div></div></div>`;
      const sh = s.weights_share || {}, ds = s.discovery_share || {};
      table($('#wtT'), fams.map(f => ({ f })), [{ k: 'f', label: 'Family', l: 1 }, { k: 'p', label: 'Production (before → from)', v: x => (hx.production_weights_recent || {})[x.f], f: x => `${fmt.pct((hx.production_weights || {})[x.f], 0)} → ${fmt.pct((hx.production_weights_recent || {})[x.f], 0)}` },
        { k: 'g', label: 'Challenger: global', v: x => ((sh.global || {})[x.f]), f: x => `<b class="${scoreCls((sh.global || {})[x.f])}">${fmt.spct((sh.global || {})[x.f], 0)}</b> <span class="faint">(${fmt.spct((ds.global || {})[x.f], 0)})</span>` },
        ...nodes.slice(0, 10).map(n => ({ k: n, label: n.replace(/^(class|regime|sector|asset):/, '').replace(/^[A-Z_]+:/, ''), title: n, v: x => (sh[n] || {})[x.f], f: x => `<span class="${scoreCls((sh[n] || {})[x.f])}">${fmt.spct((sh[n] || {})[x.f], 0)}</span>` }))], { sortKey: 'g' });
      const gt = (el, obj, label) => table(el, Object.entries(obj || {}).map(([k, v]) => ({ k, ...v })), [{ k: 'k', label, l: 1 }, { k: 'production', label: 'Prod.', f: x => fmt.num(x.production, 3) }, { k: 'challenger', label: 'Chall.', f: x => `<span class="${(x.challenger || 0) > (x.production || 0) ? 'pos' : 'neg'}">${fmt.num(x.challenger, 3)}</span>` }, { k: 'n', label: 'n' }], { sortKey: 'n' });
      gt($('#wgC'), (w.groups || {}).class, 'Class'); gt($('#wgS'), (w.groups || {}).sector, 'Sector'); gt($('#wgR'), (w.groups || {}).regime, 'Regime');
      return;
    }
    if (tab === 'challengers') {
      body.innerHTML = `<div class="card"><h2>The promotion process</h2><p class="muted" style="margin:0">Production → ML Lab proposes a challenger → historical walk-forward (G1) → untouched confirmation period (G2) → live shadow: recorded every day in the prediction ledger, graded when its horizon passes (G3, at least 60 graded forecasts, compared with production on the same ones) → <b>you</b> promote it. A promoted Shaffer weighting becomes a new score version (history and calibration recomputed with it); a promoted hedge sizing is applied to its groups.</p></div>
        <div class="card flush" style="margin-top:14px"><div id="chT"></div></div>`;
      table($('#chT'), vers, [{ k: 'id', label: 'Version', l: 1, f: v => `<b>${esc(v.id)}</b><span class="sub">${esc(v.description || '')}</span>` }, { k: 'kind', label: 'Engine', l: 1, f: v => ({ hedge: 'Shaffer Hedge', 'shaffer-alpha': 'Shaffer Alpha', 'shaffer-directional': 'Shaffer Directional (research)' })[v.kind] || 'Shaffer Score' },
        { k: 'st', label: 'Stage', l: 1, v: v => (v.stage || {}).stage, f: v => stagePill((v.stage || {}).stage || v.status) + (v.stage && v.stage.by_horizon ? `<span class="sub">${Object.entries(v.stage.by_horizon).map(([k, x]) => `${k}: ${esc(x)}`).join(' · ')}</span>` : '') + (v.stage && v.stage.confirmed_multiples != null ? `<span class="sub">${v.stage.confirmed_multiples} multiple(s) confirmed</span>` : '') },
        { k: 'live', label: 'Live shadow', l: 1, f: v => { const l = (v.stage || {}).live; return l ? `${l.graded}/${l.required} graded${l.challenger_ic != null ? ` · IC ${fmt.num(l.challenger_ic, 3)} vs ${fmt.num(l.production_ic, 3)}` : ''}${l.variance_ratio != null ? ` · variance × ${fmt.num(l.variance_ratio, 2)}` : ''}` : '—'; } },
        { k: 'introduced', label: 'Since', l: 1, f: v => esc(v.live_shadow_from || v.introduced || '') },
        { k: 'x', label: '', nosort: 1, f: v => (v.stage || {}).stage === 'eligible for promotion' ? `<button class="small primary" data-promote="${esc(v.id)}">Promote…</button>` : '' }], { sortKey: null });
      $$('[data-promote]').forEach(b => b.onclick = () => { const id = b.dataset.promote; const m = modal(`<h2 style="margin-top:0">Promote ${esc(id)}?</h2><p>It passed discovery, confirmation and the live shadow. Promotion retires the current production version of this engine. This is recorded and cannot be undone silently.</p><div class="row" style="justify-content:flex-end;gap:8px"><button class="ghost" id="prNo">Cancel</button><button class="primary" id="prYes">Promote</button></div><p id="prErr" class="neg"></p>`);
        $('#prNo', m).onclick = closeModal; $('#prYes', m).onclick = () => busy($('#prYes', m), async () => { try { await post('/fs2/lab/promote', { id, confirm: true }); closeModal(); toast(`${id} promoted`); route(); } catch (e) { $('#prErr', m).textContent = e.message; } }); });
      return;
    }
    if (tab === 'hedge') {
      const sz = (L.hedge || {}).groups || {};
      const BH = L.breadth_hedge;
      const rows = []; Object.entries(sz).forEach(([g, res]) => Object.entries(res).forEach(([mt, r]) => rows.push({ g, mt, ...r })));
      const mrows = []; Object.entries(L.hedge_ml || {}).forEach(([g, res]) => Object.entries(res).forEach(([mt, r]) => r.n && mrows.push({ g, mt, ...r })));
      body.innerHTML = `<div id="bhC"></div><div class="card" style="margin-top:14px"><h2>Is the static hedge the right size? <small>the raw hedge comes from beta, DV01, CS01, FX notional and option Greeks; the Lab asks whether, historically, it was too big or too small</small></h2>
          <p class="muted" style="margin:0">For each hedge group and objective: the constant resizing H = m · H<sub>raw</sub> that worked best on windows before ${esc((L.hedge || {}).asof ? 'the confirmation date' : '2018')} (discovery), then — frozen — tested on the windows after it (confirmation), clustered by date. Confirmed multiples form the Shaffer Hedge sizing challenger; they are applied only after the live shadow and your promotion.</p></div>
        <div class="card flush" style="margin-top:14px"><h2>Sizing study ${rows.length ? '' : '<small>run the hedge audit (python -m finsim2 hedge-audit)</small>'}</h2><div id="hsT"></div></div>
        <div class="card flush" style="margin-top:14px"><h2>Objective-specific ML adjustments <small>verified only if they beat the static rule, a constant resizing and the minimum-variance multiple out of sample</small></h2><div id="hmT"></div></div>`;
      labBreadthHedge($('#bhC'), BH);
      table($('#hsT'), rows, [{ k: 'g', label: 'Group (risk:product:horizon)', l: 1 }, { k: 'mt', label: 'Objective metric', l: 1 }, { k: 'multiple', label: 'Multiple', f: r => r.multiple != null ? `× ${fmt.num(r.multiple, 3)}` : '—' }, { k: 'discovery_gain', label: 'Gain (discovery)', f: r => fmt.pct(r.discovery_gain, 1) }, { k: 'confirmation_gain', label: 'Gain (confirmation)', v: r => r.confirmation_gain, f: r => `<span class="${(r.confirmation_gain || 0) > 0 ? 'pos' : 'neg'}">${fmt.pct(r.confirmation_gain, 1)}</span>` }, { k: 't', label: 't / p', f: r => r.t != null ? 't ' + fmt.num(r.t, 1) : r.p != null ? 'p ' + fmt.num(r.p, 3) : esc(r.reason || '—') }, { k: 'passed', label: 'Confirmed', v: r => r.passed ? 1 : 0, f: r => r.passed ? '<span class="pill pos">yes</span>' : 'no' }], { sortKey: 'passed', empty: 'No sizing study yet' });
      table($('#hmT'), mrows, [{ k: 'g', label: 'Group', l: 1 }, { k: 'mt', label: 'Metric', l: 1 }, { k: 'n_eff', label: 'n_eff (dates)', f: r => fmt.num(r.n_eff, 0) }, { k: 'gain_vs_static', label: 'Gain vs static', f: r => fmt.pct(r.gain_vs_static, 1) }, { k: 'mean_adjustment', label: 'Mean adj. (constant)', f: r => `${fmt.num(r.mean_adjustment, 2)} (${fmt.num(r.constant_adjustment, 2)})` }, { k: 'verified', label: 'Verified', v: r => r.verified ? 1 : 0, f: r => r.verified ? '<span class="pill pos">yes</span>' : `<span class="faint">${esc((r.reasons || [])[0] || 'no')}</span>` }], { sortKey: 'verified', empty: 'No hedge ML trained yet' });
      return;
    }
    if (tab === 'live') {
      const rows = []; Object.entries(L.live || {}).forEach(([m, hs]) => Object.entries(hs).forEach(([h, v]) => rows.push({ m, h, ...v })));
      body.innerHTML = `<div class="card"><h2>Live learning <small>every day FinSim2 makes genuine forecasts; they are stored the day they are made (never edited) and graded when their horizon passes</small></h2><p class="muted" style="margin:0">This is the dataset no backtest can fake: it did not exist when the forecast was made. Challengers in live shadow are recorded next to production (model <code>shaffer:&lt;version&gt;</code>) and compared on the same forecasts.</p></div>
        <div class="card flush" style="margin-top:14px"><div id="lvT"></div></div>`;
      table($('#lvT'), rows, [{ k: 'm', label: 'Model', l: 1, f: r => `<b>${esc(r.m)}</b>` }, { k: 'h', label: 'Horizon', l: 1 }, { k: 'pending', label: 'Waiting to mature' }, { k: 'graded', label: 'Graded' }, { k: 'next_due', label: 'Next due', l: 1, f: r => esc(r.next_due || '—') }, { k: 'first', label: 'First forecast', l: 1, f: r => esc(r.first || '—') }], { sortKey: 'pending', empty: 'No live forecasts yet: the daily learning run records them' });
      return;
    }
    if (tab === 'versions') {
      const sv = vers.filter(v => v.kind === 'shaffer');
      const cell = (v, k) => { const s = ((H[k] || {}).variants || {})[v.variant]; if (v.status === 'production' && !v.variant) { const any = Object.values((H[k] || {}).variants || {})[0]; return any ? [((any.confirmation || {}).production || {}), (((any.walkforward || {}).from || {}).production || {})] : [{}, {}]; } return s ? [((s.confirmation || {}).challenger || {}), (((s.walkforward || {}).from || {}).challenger || {})] : [{}, {}]; };
      body.innerHTML = `<div class="card flush"><h2>Version comparison <small>the same records for every version: IC with weights frozen at discovery (confirmation) · walk-forward IC from ${esc((R.confirm_from || '').slice(0, 4))}</small></h2><div id="vcT"></div></div>`;
      table($('#vcT'), sv, [{ k: 'id', label: 'Version', l: 1, f: v => `<b>${esc(v.id)}</b> ${stagePill((v.stage || {}).stage || v.status)}` }, ...HZL.map(k => ({ k, label: k, nosort: 1, f: v => { const [a, b] = cell(v, k); return `${icT(a)}<span class="sub">WF ${icT(b)}</span>`; } }))], { sortKey: null });
      return;
    }
  };

  pages.risk = async (main, args, alive) => {
    const tab = args[0] || 'risk';
    main.innerHTML = `<div class="page-head"><div><h1>Risk</h1><p>How much the portfolio can lose, what drives it, how its relationships are changing, and what would happen in a scenario. Simulations are ranges of outcomes, not predictions.</p></div></div>
      <div class="tabs">${[['risk', 'Risk & drivers'], ['scenario', 'Scenario analysis'], ['crisis', 'Crisis replays'], ['montecarlo', 'Monte Carlo']].map(([k, l]) => `<a class="tab ${k === tab ? 'on' : ''}" href="#/risk/${k}">${l}</a>`).join('')}</div><div id="rkB" style="margin-top:14px"><div class="loading">Computing…</div></div>`;
    const body = $('#rkB');
    if (tab === 'crisis') {
      body.innerHTML = '<div class="loading">Replaying today\'s book through four historical crises (and scoring its Shaffer Hedge)…</div>';
      let r; try { r = await api('/fs2/hedge/crisis'); } catch (e) { body.innerHTML = `<div class="card"><div class="empty">${esc(e.message)}</div></div>`; return; }
      if (!alive()) return;
      const pnlOf = p => p.direct_pnl != null ? p.direct_pnl : p.factor_pnl;
      body.innerHTML = `<p class="muted" style="margin-top:0">Today's positions, held fixed, through each episode's actual cumulative factor moves (options fully re-priced with the VIX change). "Own history" uses each held asset's realised return where it traded through the episode (it includes single-name moves the factor model cannot). VaR/ES: today's exposures × each day's factor moves during the episode, against the last three years.</p>
        <div class="card flush"><div class="tbl-wrap"><table><thead><tr><th class="l">Episode</th><th>Factor model</th><th>Own history</th><th>% of NAV</th><th>1-day VaR 95% crisis / normal</th><th>1-day ES 95% crisis / normal</th><th class="l">Largest losses</th><th class="l">Largest offsets</th>${(r.crises[0].hedges || []).map(h => `<th>With ${esc(h.label || h.objective)} hedge</th>`).join('')}</tr></thead><tbody>
        ${r.crises.map(c => { const ps = c.positions.slice(); const loss = ps.slice(0, 3).filter(p => pnlOf(p) < 0), gain = ps.slice().reverse().slice(0, 2).filter(p => pnlOf(p) > 0);
          return `<tr><td class="l"><b>${esc(c.label)}</b><span class="sub">${fmt.date(c.start)} → ${fmt.date(c.end)} · ${c.sessions} sessions${c.missing.length ? ' · unavailable: ' + esc(c.missing.join(', ')) : ''}</span></td>
          <td class="${sign(c.pnl)}">${fmt.signed(c.pnl, 0)}</td><td class="${sign(c.pnl_direct)}">${fmt.signed(c.pnl_direct, 0)}</td><td>${fmt.spct(c.pnl_pct_nav, 1)}</td>
          <td>${fmt.money(c.var95, 0)} / ${fmt.money(c.var95_normal, 0)}<span class="sub">×${fmt.num((c.var95 || 0) / (c.var95_normal || 1), 1)}</span></td><td>${fmt.money(c.es95, 0)} / ${fmt.money(c.es95_normal, 0)}</td>
          <td class="l wrap" style="font-size:12px">${loss.map(p => `${esc(p.id)} ${fmt.signed(pnlOf(p), 0)}`).join('<br>') || '—'}</td><td class="l wrap" style="font-size:12px">${gain.map(p => `${esc(p.id)} ${fmt.signed(pnlOf(p), 0)}`).join('<br>') || '—'}</td>
          ${(c.hedges || []).map(h => h.error ? `<td class="faint">${esc(h.error)}</td>` : `<td class="${sign(h.hedged_pnl)}">${fmt.signed(h.hedged_pnl, 0)}<span class="sub">hedge ${fmt.signed(h.hedge_pnl, 0)} · ${esc(h.legs.map(L => `${fmt.qty(L.quantity)} ${L.id}`).join(', '))}</span></td>`).join('')}</tr>`; }).join('')}
        </tbody></table></div></div>
        <p class="hint">Limits: exposures are today's and held fixed (no rebalancing, no path, instantaneous option re-pricing); a factor that did not exist yet contributes nothing and is listed as unavailable. Hedges are today's recommended packages (market beta 100%, crash protection 50%).</p>`;
      return;
    }
    if (tab === 'risk') {
      const [an, dr] = await Promise.all([api('/fs2/portfolio'), api('/fs2/portfolio/drivers').catch(() => null)]); if (!alive()) return;
      const rk = an.risk || {};
      const held = an.positions.filter(p => Math.abs(p.quantity) > 1e-12);
      body.innerHTML = `<div class="tiles">${kpi('Volatility (annual)', fmt.pct(rk.vol, 1), 'today\'s holdings over 3 years')}${kpi('VaR 95% · 99% (1 day)', `${fmt.pct(rk.var95, 2)} · ${fmt.pct(rk.var99, 2)}`, fmt.money((rk.var95 || 0) * an.nav) + ' at 95%')}${kpi('Expected shortfall 95%', fmt.pct(rk.es95, 2), fmt.money((rk.es95 || 0) * an.nav))}${kpi('Max drawdown', fmt.pct(rk.max_drawdown, 1), 'historical simulation')}${kpi('Sharpe · Sortino', `${fmt.num(rk.sharpe, 2)} · ${fmt.num(rk.sortino, 2)}`)}${kpi('Beta · duration', `${fmt.num(an.beta, 2)} · ${fmt.num(an.duration, 1)}`)}</div>
        <div class="grid g2"><div class="card"><h2>What drives the portfolio <small>share of explained variance, weekly factor regression over 3 years</small></h2><div id="fdC"></div><p class="hint">${an.factors && an.factors.r2 != null ? `The factors explain ${fmt.pct(an.factors.r2, 0)} of the portfolio's weekly moves.` : 'Add positions to see the factor decomposition.'}</p></div>
          <div class="card flush"><h2>Risk contribution by position</h2><div id="rcT"></div></div></div>
        <div class="grid g2" style="margin-top:16px"><div class="card"><h2>Correlation matrix <small>1-year daily, USD</small></h2><div id="cmC"></div></div>
          <div class="card flush"><h2>Correlation decay <small>5-year vs 1-year vs 3-month · flagged when the relationship has moved by 0.25 or more</small></h2><div id="cdT"></div></div></div>`;
      const f = an.factors || {};
      hbars($('#fdC'), Object.entries(f.shares || {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).map(([k, v]) => ({ label: k, value: v, sub: `beta ${fmt.num((f.betas || {})[k], 2)}, t ${fmt.num((f.t || {})[k], 1)}` })), { fmt: v => fmt.pct(v, 0), empty: 'No holdings' });
      table($('#rcT'), held, [{ k: 'asset_id', label: 'Position', l: 1, f: p => `<b>${esc(p.asset_id)}</b>` }, { k: 'weight', label: 'Weight', f: p => fmt.pct(p.weight, 1) }, { k: 'volatility', label: 'Vol', f: p => fmt.pct(p.volatility, 1) }, { k: 'risk_contribution', label: 'Risk share', f: p => `${fmt.pct(p.risk_contribution, 1)}<span class="cell-bar bar"><i style="width:${Math.min(100, Math.abs(p.risk_contribution || 0) * 100)}%"></i></span>` }, { k: 'marginal_risk', label: 'Marginal', f: p => fmt.pct(p.marginal_risk, 1) }], { sortKey: 'risk_contribution', empty: 'No holdings' });
      const cm = an.correlation || {};
      heatmap($('#cmC'), (cm.assets || []).map(a => ({ key: a, label: a })), (cm.assets || []).map(a => ({ key: a, label: a })), (r, c) => { const i = cm.assets.indexOf(r.key), j = cm.assets.indexOf(c.key); const v = (cm.matrix[i] || [])[j]; return { v, text: v == null ? '' : fmt.num(v, 2) }; }, { max: 1, corner: '' });
      table($('#cdT'), (dr && dr.pairs) || [], [{ k: 'pair', label: 'Pair', l: 1, f: x => x.pair.join(' / ') }, ...['5Y', '1Y', '3M'].map(w => ({ k: w, label: w, v: x => x.windows[w], f: x => fmt.num(x.windows[w], 2) })), { k: 'shift', label: 'Shift', f: x => `<span class="${x.flag ? 'warn' : ''}">${fmt.num(x.shift, 2)}${x.flag ? ' ⚑' : ''}</span>` }], { sortKey: 'shift', empty: 'Needs two or more holdings' });
      return;
    }
    if (tab === 'scenario') {
      body.innerHTML = `<div class="card"><h2>Define a scenario <small>leave a box blank and that factor moves with the ones you set, as it has historically</small></h2><div class="form">
        <label class="f">10-year yield (bp)<input id="s_y10" type="number" placeholder="+100"></label><label class="f">Nasdaq-100 (%)<input id="s_ndx" type="number" placeholder="-15"></label><label class="f">Oil (%)<input id="s_oil" type="number" placeholder="+25"></label>
        <label class="f">US dollar (%)<input id="s_usd" type="number" placeholder="+10"></label><label class="f">VIX to (level)<input id="s_vix" type="number" placeholder="40"></label><label class="f">Credit spreads (bp)<input id="s_cr" type="number" placeholder="+200"></label></div>
        <div class="row" style="margin-top:12px"><div class="chips">${[['Rate shock', { y10: 100 }], ['Tech sell-off', { ndx: -15, vix: 35 }], ['Oil spike', { oil: 25 }], ['Strong dollar', { usd: 10 }], ['Credit crunch', { cr: 200, vix: 40, ndx: -20 }], ['2022-style', { y10: 150, ndx: -25, usd: 8 }]].map(([l, v]) => `<button class="chip" data-p='${JSON.stringify(v)}'>${l}</button>`).join('')}</div><span class="spacer"></span><button class="primary" id="scnGo">Run scenario</button></div></div><div id="scnR" style="margin-top:16px"></div>`;
      $$('[data-p]').forEach(c => c.onclick = () => { const v = JSON.parse(c.dataset.p); ['y10', 'ndx', 'oil', 'usd', 'vix', 'cr'].forEach(k => { $('#s_' + k).value = v[k] != null ? v[k] : ''; }); });
      $('#scnGo').onclick = () => busy($('#scnGo'), async () => {
        const g = k => $('#s_' + k).value === '' ? null : +$('#s_' + k).value;
        const r = await post('/fs2/portfolio/scenario', { shock: { y10_bp: g('y10'), nasdaq_pct: g('ndx'), oil_pct: g('oil'), usd_pct: g('usd'), vix_level: g('vix'), credit_bp: g('cr') } });
        $('#scnR').innerHTML = `<div class="tiles">${kpi('Estimated portfolio impact', `<span class="${sign(r.pnl)}">${fmt.signed(r.pnl)}</span>`, fmt.spct(r.pnl_pct, 2) + ' of NAV')}${Object.entries(r.completed).map(([k, v]) => kpi(esc(r.labels[k]), fmt.num(v, 1), v == null ? 'no data: left out' : r.shock[k] != null ? 'set' : 'implied by the others')).join('')}</div>
          <div class="grid g2"><div class="card flush"><h2>By position</h2><div id="scnP"></div></div><div class="card flush"><h2>Historical analogues <small>21-session windows most like this scenario, and what today's holdings did</small></h2><div id="scnA"></div></div></div><p class="hint">${esc(r.note)}</p>`;
        table($('#scnP'), r.positions, [{ k: 'asset_id', label: 'Position', l: 1 }, { k: 'market_value', label: 'Value', f: x => fmt.money(x.market_value) }, { k: 'move', label: 'Move', cls: x => sign(x.move), f: x => fmt.spct(x.move, 1) }, { k: 'pnl', label: 'P&L', cls: x => sign(x.pnl), f: x => fmt.signed(x.pnl) }, { k: 'r2', label: 'Fit R²', f: x => fmt.num(x.r2, 2) }], { sortKey: 'pnl', sortDir: 1 });
        table($('#scnA'), r.analogues, [{ k: 'start', label: 'Window', l: 1, f: x => `${fmt.date(x.start)} → ${fmt.date(x.end)}` }, { k: 'portfolio_return', label: 'Portfolio', cls: x => sign(x.portfolio_return), f: x => fmt.spct(x.portfolio_return, 1) }, { k: 'moves', label: 'Moves then', l: 1, nosort: 1, f: x => Object.entries(x.moves).map(([k, v]) => `${esc(r.labels[k].split(' (')[0])} ${fmt.num(v, 1)}`).join(' · ') }], { sortKey: null, empty: 'Set at least one factor' });
      });
      return;
    }
    body.innerHTML = `<div class="card"><div class="form"><label class="f">Horizon<select id="mcH">${['1M', '3M', '6M', '1Y', '5Y', '10Y'].map(x => `<option ${x === '1Y' ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
      <label class="f">Method<select id="mcM"><option value="bootstrap">Block bootstrap of history</option><option value="gbm">Geometric Brownian motion</option></select></label><label class="f">Paths<input id="mcP" type="number" value="2000"></label><label class="f">Drawdown threshold (%)<input id="mcD" type="number" value="20"></label>
      <label class="f">Simulate<select id="mcW"><option value="portfolio">my portfolio</option><option value="asset">one asset…</option></select></label><div id="mcAW"></div><div><button class="primary" id="mcGo" style="width:100%;justify-content:center">Simulate</button></div></div></div><div id="mcR" style="margin-top:16px"></div>`;
    $('#mcW').onchange = () => { $('#mcAW').innerHTML = $('#mcW').value === 'asset' ? assetPicker('mcA', S.current) : ''; if ($('#mcA')) bindPicker('mcA', x => { $('#mcA').value = x; }); };
    $('#mcGo').onclick = () => busy($('#mcGo'), async () => {
      const bd = { horizon: $('#mcH').value, method: $('#mcM').value, paths: +$('#mcP').value, dd: +$('#mcD').value / 100 };
      const r = $('#mcW').value === 'asset' ? await post(`/fs2/asset/${encodeURIComponent($('#mcA').value.trim().toUpperCase())}/montecarlo`, bd) : await post('/fs2/portfolio/montecarlo', bd);
      $('#mcR').innerHTML = `<div class="tiles">${kpi('Median ending value', fmt.money(r.median), fmt.spct(r.median / r.start_value - 1, 1))}${kpi('Mean', fmt.money(r.mean))}${kpi('5th · 95th percentile', `${fmt.big(r.p5)} · ${fmt.big(r.p95)}`, `25th ${fmt.big(r.p25)} · 75th ${fmt.big(r.p75)}`)}${kpi('Probability of a loss', fmt.pct(r.prob_loss, 0))}${kpi('Probability of > +10%', fmt.pct(r.prob_gain_10, 0))}${kpi(`Drawdown beyond ${fmt.pct(r.dd_threshold, 0)}`, fmt.pct(r.prob_drawdown, 0), 'at some point on the path')}</div>
        <div class="card"><h2>Monte Carlo cone <small>${r.paths} paths · ${esc(r.method)} · from ${Math.round(r.hist_days / 252)} years of history (vol ${fmt.pct(r.hist_vol_ann, 1)})</small></h2><div id="mcC"></div><p class="hint">${esc(r.note)}</p></div>`;
      cone($('#mcC'), r.cone, r.start_value, { label: `${r.days} sessions` });
    });
  };

  // ---------------------------------------------------------------- backtests
  pages.health = async (main, _, alive) => {
    const r = await api('/fs2/health'); if (!alive()) return;
    const cls = { 'HEALTHY': 'pos', 'WEAKENING': 'warn', 'DECAYING': 'neg', 'NO VERIFIED EDGE': '', 'INSUFFICIENT DATA': 'warn' };
    const sp = s => `<span class="pill ${cls[s] || ''}" style="font-size:10px">${esc(s)}</span>`;
    const engines = [...new Set(r.rows.map(x => x.engine))];
    const wf = x => {
      const w = x.walk_forward || {};
      if (w.ic !== undefined) return `IC ${fmt.num(w.ic, 3)} (t ${fmt.num(w.t, 1)}, n<sub>eff</sub> ${fmt.num(w.n_eff, 0)})<span class="sub">last ${esc((w.recent_from || '').slice(0, 4))}→: IC ${fmt.num(w.recent_ic, 3)} (t ${fmt.num(w.recent_t, 1)})</span>`;
      if (w.median_reduction !== undefined) return `variance reduction ${fmt.pct(w.median_reduction, 0)} · tail-loss reduction ${fmt.pct(w.tail_reduction, 0)} (medians)<span class="sub">variance: early third ${fmt.pct(w.early_third, 0)} · recent third ${fmt.pct(w.recent_third, 0)}${x.judged_on ? ` · judged on ${esc(x.judged_on)}` : ''}</span>`;
      if (x.verified !== undefined) return `verified in ${x.verified}/${x.assets ?? x.groups}${x.stable != null ? ` · stable in ${x.stable}` : ''}${x.decayed ? ` · decaying in ${x.decayed}` : ''}`;
      return `<span class="faint">${esc(x.reason || '—')}</span>`;
    };
    const lv = x => {
      const l = x.live || {};
      if (!l.graded && !l.pending) return '<span class="faint">no live forecasts yet</span>';
      if (l.mean_effectiveness !== undefined) return l.graded ? `${l.graded} graded · effectiveness ${fmt.num(l.mean_effectiveness, 2)}` : '<span class="faint">no graded hedges yet</span>';
      return `${l.graded || 0} graded, ${l.pending || 0} pending${l.graded ? `<span class="sub">IC ${fmt.num(l.ic, 2)} · hit ${fmt.pct(l.hit, 0)} · expected ${fmt.pct(l.mean_expected, 2)} vs realised ${fmt.pct(l.mean_realized, 2)}${l.range_coverage != null ? ` · inside range ${fmt.pct(l.range_coverage, 0)}` : ''}${l.brier != null ? ` · Brier ${fmt.num(l.brier, 3)} vs base rate ${fmt.num(l.brier_base_rate, 3)}` : ''}</span>` : ''}`;
    };
    main.innerHTML = `<div class="page-head"><div><h1>Model health</h1><p>Each engine judged separately by its own out-of-sample record and by what happened live — forecasts are stored the day they are made and graded when their horizon passes. The engines are never combined. Data to ${esc(r.asof)}.</p></div></div>
      <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:16px">${Object.entries(r.counts).map(([k, v]) => `<div class="kpi"><div class="k">${sp(k)}</div><div class="v">${v}</div></div>`).join('')}</div>
      ${engines.map(e => `<div class="card flush" style="margin-bottom:16px"><h2>${esc(e)}</h2><div class="tbl-wrap"><table><thead><tr><th class="l">Horizon / group</th><th class="l">Status</th><th class="l">Walk-forward (out of sample)</th><th class="l">Live: expected vs realised</th><th class="l">Bar</th></tr></thead><tbody>
        ${r.rows.filter(x => x.engine === e).map(x => `<tr><td class="l"><b>${esc(x.horizon)}</b>${x.assets ? `<span class="sub">${x.assets} ${e.startsWith('Shaffer Hedge') ? 'cases' : 'assets'}</span>` : ''}</td><td class="l">${sp(x.status)}</td><td class="l wrap">${wf(x)}</td><td class="l wrap">${lv(x)}</td><td class="l wrap faint" style="max-width:260px">${esc(x.bar || x.reason || '')}</td></tr>`).join('')}</tbody></table></div></div>`).join('')}
      <div class="card"><h2>Status rules (fixed in advance)</h2><pre class="muted" style="white-space:pre-wrap;font-size:12px">${esc(r.rules)}</pre></div>`;
  };

  pages.backtests = async (main, args, alive) => {
    const fm = await featureMeta(); const hist = await api('/fs2/backtests').catch(() => []); if (!alive()) return;
    const id = S.current;
    main.innerHTML = `<div class="page-head"><div><h1>Backtests</h1><p>Test any signal, the point-in-time Shaffer Score (raw or calibrated), or the ML ensemble's out-of-sample forecasts on any asset, horizon, period and regime. The signal is read at each close; with an execution lag of 1 (the default) the trade is made at the next close, so a signal first earns the return from close t+1 to t+2. Lag 0 trades at the same close that produced the signal and is optimistic. Signals point the way their history said at each date (point in time), and trade returns are net of costs.</p></div></div>
      <div class="card"><div class="form">
        <label class="f">Asset${assetPicker('btA', id)}</label>
        <label class="f">Signal<select id="btS"><option value="shaffer">Shaffer Score (point in time; ±50 = 1)</option><option value="shaffer_calibrated">Shaffer Score, calibrated (point in time)</option><option value="ml:ensemble">ML ensemble (out of sample)</option>${Object.entries(fm).map(([k, v]) => `<option value="${k}">${esc(v.label)} — ${esc(v.family)}</option>`).join('')}</select></label>
        <label class="f">Horizon<select id="btH">${HZ.slice(0, 6).map(x => `<option ${x === '3M' ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
        <label class="f">Mode<select id="btM"><option value="long">Long / flat</option><option value="long_short">Long / short</option></select></label>
        <label class="f">Entry (z)<input id="btE" type="number" step="0.25" value="1"></label><label class="f">Exit (z)<input id="btX" type="number" step="0.25" value="0"></label>
        <label class="f">From<input id="btF" type="date"></label><label class="f">To<input id="btT" type="date"></label>
        <label class="f">Only in regime<select id="btR"><option value="">any</option>${[['market', 'bull', 'Bull market'], ['market', 'bear', 'Bear market'], ['volatility', 'high_vol', 'High volatility'], ['volatility', 'low_vol', 'Low volatility'], ['rates', 'rising_rates', 'Rising rates'], ['rates', 'falling_rates', 'Falling rates'], ['inflation', 'high_inflation', 'High inflation'], ['growth', 'recession', 'Recession'], ['growth', 'expansion', 'Expansion'], ['dollar', 'strong_dollar', 'Strong dollar'], ['dollar', 'weak_dollar', 'Weak dollar']].map(([d, s, l]) => `<option value="${d}|${s}">${l}</option>`).join('')}</select></label>
        <label class="f">Costs (bp per side)<input id="btC" type="number" value="5"></label><label class="f">Execution lag (sessions)<input id="btLag" type="number" min="0" max="21" step="1" value="1"></label><label class="f"><span><input type="checkbox" id="btI"> invert the signal</span></label>
        <div><button class="primary" id="btGo" style="width:100%;justify-content:center">Run backtest</button></div></div></div><div id="btR2" style="margin-top:16px"></div>
      <div class="card flush" style="margin-top:16px"><h2>Recent backtests</h2><div id="btHist"></div></div>`;
    bindPicker('btA', x => { $('#btA').value = x; });
    $('#btGo').onclick = () => busy($('#btGo'), async () => {
      const rg = $('#btR').value ? $('#btR').value.split('|') : null;
      const r = await post('/fs2/backtest', { asset: $('#btA').value.trim().toUpperCase(), signal: $('#btS').value, horizon: $('#btH').value, mode: $('#btM').value, entry: +$('#btE').value, exit: +$('#btX').value, start: $('#btF').value || null, end: $('#btT').value || null, regime: rg, cost_bps: +$('#btC').value, lag: +$('#btLag').value, invert: $('#btI').checked });
      const m = r.metrics;
      const bh = { r: m.buy_hold_return ?? m.benchmark_return, c: m.buy_hold_cagr ?? m.benchmark_cagr, s: m.buy_hold_sharpe ?? m.benchmark_sharpe };
      $('#btR2').innerHTML = `<div class="tiles">${kpi('CAGR', fmt.pct(m.cagr, 1), `buy & hold (same asset) ${fmt.pct(bh.c, 1)}`)}${kpi('Total return', fmt.pct(m.total_return, 0), `buy & hold (same asset) ${fmt.pct(bh.r, 0)}`)}${kpi('Volatility', fmt.pct(m.vol, 1))}${kpi('Sharpe · Sortino', `${fmt.num(m.sharpe, 2)} · ${fmt.num(m.sortino, 2)}`, `buy & hold (same asset) Sharpe ${fmt.num(bh.s, 2)}`)}${kpi('Max drawdown · Calmar', `${fmt.pct(m.max_drawdown, 1)} · ${fmt.num(m.calmar, 2)}`)}${kpi('Trades · win rate', `${m.trades} · ${fmt.pct(m.win_rate, 0)}`, `avg gain ${fmt.pct(m.avg_gain, 1)} · avg loss ${fmt.pct(m.avg_loss, 1)}`)}${kpi('Profit factor', fmt.num(m.profit_factor, 2))}${kpi('Exposure · turnover', `${fmt.pct(m.exposure, 0)} · ${fmt.num(m.turnover, 1)}/yr`)}${kpi('Alpha · beta vs SPY', `${fmt.pct(m.alpha, 1)} · ${fmt.num(m.beta, 2)}`)}</div>
        <div class="card"><h2>Equity curve <small>growth of $1 · ${m.days} sessions · ${esc(r.execution || '')}</small></h2><div id="btC2"></div></div><div class="card flush" style="margin-top:16px"><h2>Trades</h2><div id="btTr"></div></div>`;
      lineChart($('#btC2'), [{ name: 'Strategy', data: r.curve.strategy, color: css('--accent'), area: true }, { name: 'Buy & hold (same asset)', data: r.curve.buy_hold, color: css('--faint'), width: 1.4 }], { labels: r.curve.dates.map(d => fmt.date(d)), fmtY: v => fmt.num(v, 2), h: 280 });
      table($('#btTr'), r.trades.slice().reverse(), [{ k: 'entry', label: 'Entry', l: 1 }, { k: 'exit', label: 'Exit', l: 1, f: t => esc(t.exit || 'open') }, { k: 'side', label: 'Side', l: 1 }, { k: 'return', label: 'Return (net)', cls: t => sign(t.return), f: t => fmt.spct(t.return, 1) }], { sortKey: null, maxH: 360, empty: 'No trades: the signal never reached the entry level' });
    });
    table($('#btHist'), hist, [{ k: 'created_at', label: 'When', l: 1 }, { k: 'a', label: 'Asset · signal', l: 1, f: x => `${esc(x.spec.asset)} · ${esc(x.spec.signal)} · ${esc(x.spec.horizon)}` }, { k: 'c', label: 'CAGR', v: x => x.result.metrics.cagr, f: x => fmt.pct(x.result.metrics.cagr, 1) }, { k: 's', label: 'Sharpe', v: x => x.result.metrics.sharpe, f: x => fmt.num(x.result.metrics.sharpe, 2) }], { sortKey: null, empty: 'No backtests yet' });
  };

  // ---------------------------------------------------------------- portfolio
  function perfBlock(an) {
    // time-weighted (flows removed, chained daily) vs the benchmark over the same periods; money-weighted since the start
    const pf = an.performance || {}; if (!pf.periods) return '';
    const bp = (pf.benchmark || {}).periods || {}; const labs = ['1D', '1W', '1M', '3M', 'YTD', '1Y', '3Y', '5Y'].filter(k => pf.periods[k] != null);
    return `<div class="card flush" style="margin-bottom:14px"><h2>Performance <small>time-weighted: deposits and withdrawals do not count as returns · dividends included · benchmark ${esc(an.benchmark)} (total return)</small></h2>
      <div class="tbl-wrap"><table class="strip"><thead><tr><th class="l"></th>${labs.map(k => `<th style="text-align:center">${k}</th>`).join('')}<th style="text-align:center">Since start</th></tr></thead><tbody>
        <tr><td class="l"><b>Portfolio</b></td>${labs.map(k => `<td style="text-align:center" class="${sign(pf.periods[k])}">${fmt.spct(pf.periods[k], 1)}</td>`).join('')}<td style="text-align:center" class="${sign(pf.twr)}"><b>${fmt.spct(pf.twr, 1)}</b></td></tr>
        <tr><td class="l">${esc(an.benchmark)}</td>${labs.map(k => `<td style="text-align:center" class="faint">${fmt.spct(bp[k], 1)}</td>`).join('')}<td style="text-align:center" class="faint">${fmt.spct((pf.benchmark || {}).total, 1)}</td></tr>
        <tr><td class="l">Difference</td>${labs.map(k => `<td style="text-align:center" class="${sign(pf.periods[k] - bp[k])}">${bp[k] == null ? '—' : fmt.spct(pf.periods[k] - bp[k], 1)}</td>`).join('')}<td style="text-align:center">${(pf.benchmark || {}).total == null ? '—' : fmt.spct(pf.twr - pf.benchmark.total, 1)}</td></tr>
      </tbody></table></div>
      <div class="row muted" style="padding:10px 16px;gap:18px;font-size:12.5px"><span>Today ${fmt.signed(pf.daily_pnl)} (${fmt.spct(pf.daily_return, 2)})</span><span>Annualised (time-weighted) ${fmt.spct(pf.twr_annual, 1)}</span><span>Money-weighted (IRR) ${fmt.spct(pf.mwr, 1)} a year</span><span>Tracking error ${fmt.pct((pf.benchmark || {}).tracking_error, 1)} · information ratio ${fmt.num((pf.benchmark || {}).information_ratio, 2)}</span><span>Dividends received ${fmt.money(an.income)} · fees ${fmt.money(an.fees)}</span></div></div>`;
  }
  pages.portfolio = async (main, args, alive) => {
    const tab = args[0] || 'overview';
    const an = await api('/fs2/portfolio'); if (!alive()) return;
    const held = an.positions.filter(p => Math.abs(p.quantity) > 1e-12);
    const rk = an.risk || {};
    main.innerHTML = `<div class="page-head"><div><h1>Portfolio</h1><p>Your holdings, valued in US dollars at the latest close. Record deposits, buys and sells (at any past date: the close of that day is used unless you give a price). Risk figures apply today's weights to their recent history.</p></div>
      <div class="row"><button class="buy" id="pfTrade">Trade…</button><a class="btn" href="#/hedge">Shaffer Hedge</a><button class="primary" id="pfAdd">+ Transaction</button></div></div>
      <div class="tiles">${kpi('NAV', fmt.money(an.nav), `contributed ${fmt.money(an.contributed)} · P&L <span class="${sign(an.pnl)}">${fmt.signed(an.pnl)}</span>`)}${kpi('Cash', fmt.money(an.cash), fmt.pct(an.cash / (an.nav || 1), 0) + ' of NAV')}${kpi('Gross · net', `${fmt.big(an.gross)} · ${fmt.big(an.net)}`, `long ${fmt.big(an.long)} · short ${fmt.big(an.short)}`)}${kpi('Beta · duration', `${fmt.num(an.beta, 2)} · ${fmt.num(an.duration, 1)}y`)}${kpi('Volatility · Sharpe', `${fmt.pct(rk.vol, 1)} · ${fmt.num(rk.sharpe, 2)}`, `Sortino ${fmt.num(rk.sortino, 2)}`)}${kpi('Max drawdown', fmt.pct(rk.max_drawdown, 1))}${kpi('VaR · ES (95%, 1 day)', `${fmt.pct(rk.var95, 2)} · ${fmt.pct(rk.es95, 2)}`)}</div>
      ${(an.warnings || []).length ? `<div class="card" style="margin-bottom:14px;border-color:var(--warn)"><b>Check these</b><ul class="muted" style="margin:6px 0 0;padding-left:18px">${an.warnings.map(w => `<li>${esc(w)}</li>`).join('')}</ul></div>` : ''}
      ${perfBlock(an)}
      <div class="tabs">${[['overview', 'Overview'], ['positions', 'Positions'], ['attribution', 'Why did it move?'], ['transactions', 'Transactions'], ['construct', 'Construct / optimise'], ['drivers', 'Drivers']].map(([k, l]) => `<a class="tab ${k === tab ? 'on' : ''}" href="#/portfolio/${k}">${l}</a>`).join('')}</div><div id="pfB" style="margin-top:14px"></div>`;
    $('#pfAdd').onclick = () => txModal();
    $('#pfTrade').onclick = () => { const m = modal(`<h2 style="margin-top:0">Trade</h2><p class="muted">Choose a product. The ticket shows its Shaffer Score and the Shaffer Hedge for the risk the trade adds.</p>${assetPicker('tkPick', '')}`); bindPicker('tkPick', x => { closeModal(); openTicket(x, 'BUY'); }); setTimeout(() => $('#tkPick') && $('#tkPick').focus(), 50); };
    const body = $('#pfB');
    if (tab === 'attribution') {
      const draw = async d => {
        body.innerHTML = '<div class="loading">Attributing the day\'s P&L…</div>';
        let a; try { a = await api('/fs2/portfolio/attribution' + (d ? '?date=' + encodeURIComponent(d) : '')); } catch (e) { body.innerHTML = `<div class="card"><div class="empty">${esc(e.message)}</div></div>`; return; }
        if (!alive()) return;
        if (a.error) { body.innerHTML = `<div class="card"><div class="empty">${esc(a.error)}</div></div>`; return; }
        const bars = (el, o) => hbars(el, Object.entries(o).map(([k, v]) => ({ label: clsName(k), value: v, sub: fmt.signed(v, 0) })), { fmt: v => fmt.signed(v, 0) });
        body.innerHTML = `<div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px"><div><b style="font-size:18px" class="${sign(a.total)}">${fmt.signed(a.total, 0)}</b> <span class="muted">on ${fmt.date(a.date)} (from the ${fmt.date(a.previous)} close; deposits and withdrawals excluded)</span></div>
            <label class="f">Day<select id="atD">${a.dates.slice().reverse().map(x => `<option ${x === a.date ? 'selected' : ''}>${x}</option>`).join('')}</select></label></div>
          <div class="grid g2" style="margin-top:12px"><div class="card"><h2>It adds up <small>${a.reconciles ? 'reconciles to the NAV change' : 'DOES NOT reconcile'}</small></h2>
              <div class="kv">${Object.entries(a.lines).map(([k, v]) => `<span>${esc(k)}</span><span class="${sign(v)}">${fmt.signed(v, 2)}</span>`).join('')}<span><b>Total</b></span><span><b>${fmt.signed(a.total, 2)}</b></span></div>
              <p class="hint">"Trading & other" is what same-day trades earned against the close, and expiries; it is zero on a day without trades.</p></div>
            <div class="card"><h2>By risk factor <small>exposures at the previous close × today's factor moves</small></h2><div id="atF"></div>${a.factor_error ? `<p class="hint neg">Factor view unavailable: ${esc(a.factor_error)}</p>` : ''}</div></div>
          <div class="grid g2" style="margin-top:16px"><div class="card"><h2>By asset class</h2><div id="atC"></div></div><div class="card"><h2>By sector</h2><div id="atS"></div></div>
            <div class="card"><h2>By country</h2><div id="atK"></div></div><div class="card"><h2>By currency</h2><div id="atU"></div></div></div>
          <div class="card flush" style="margin-top:16px"><h2>By position <small>held at the previous close</small></h2><div id="atP"></div></div>`;
        bars($('#atF'), a.by_factor); bars($('#atC'), a.by_asset_class); bars($('#atS'), a.by_sector); bars($('#atK'), a.by_country); bars($('#atU'), a.by_currency);
        table($('#atP'), a.positions, [{ k: 'asset_id', label: 'Position', l: 1, f: x => `<b>${esc(x.asset_id)}</b><span class="sub">${esc(x.name || '')}</span>` }, { k: 'quantity', label: 'Quantity', f: x => fmt.qty(x.quantity) }, { k: 'pnl', label: 'P&L', cls: x => sign(x.pnl), f: x => fmt.signed(x.pnl, 0) }], { sortKey: 'pnl', sortDir: 1 });
        $('#atD').onchange = e => draw(e.target.value);
      };
      draw(null);
      return;
    }
    if (tab === 'overview') {
      body.innerHTML = `<div class="grid g-main"><div class="stack"><div class="card"><h2>Net asset value</h2><div id="navC"></div></div><div class="card flush"><h2>Positions</h2><div id="posT"></div></div></div>
        <div class="stack"><div class="card"><h2>Asset allocation</h2><div id="alC"></div></div><div class="card"><h2>Sector</h2><div id="seC"></div></div><div class="card"><h2>Country</h2><div id="coC"></div></div><div class="card"><h2>Currency</h2><div id="cuC"></div></div></div></div>`;
      lineChart($('#navC'), [{ name: 'NAV', data: an.history.nav, area: true }], { labels: an.history.dates.map(d => fmt.date(d)), fmtY: v => fmt.big(v), h: 240 });
      posTable($('#posT'), an, true);
      const dn = (el, g) => donut(el, Object.entries(g).filter(([, v]) => Math.abs(v) > 0).map(([k, v]) => ({ label: clsName(k), value: v })));
      dn($('#alC'), an.allocation); dn($('#seC'), an.sector); dn($('#coC'), an.country); dn($('#cuC'), an.currency);
      return;
    }
    if (tab === 'positions') { body.innerHTML = '<div class="card flush"><div id="posT"></div></div>'; posTable($('#posT'), an, false); return; }
    if (tab === 'transactions') {
      const tx = await api('/fs2/portfolio/transactions'); if (!alive()) return;
      body.innerHTML = '<div class="card flush"><div id="txT"></div></div>';
      table($('#txT'), tx, [{ k: 'date', label: 'Date', l: 1 }, { k: 'kind', label: 'Type', l: 1, f: t => `<span class="pill ${t.kind === 'BUY' || t.kind === 'DEPOSIT' ? 'pos' : 'neg'}">${esc(t.kind)}</span>` }, { k: 'asset_id', label: 'Asset', l: 1, f: t => esc(t.asset_id || '') }, { k: 'quantity', label: 'Quantity', f: t => t.quantity == null ? '' : fmt.qty(t.quantity) }, { k: 'price', label: 'Price / amount', f: t => fmt.num(t.price, 2) + ' ' + esc(t.currency || '') }, { k: 'note', label: 'Note', l: 1 }, { k: 'x', label: '', nosort: 1, f: t => `<button class="small ghost" data-del="${t.id}">Remove</button>` }], { sortKey: null, empty: 'No transactions yet: start with a deposit' });
      $$('[data-del]').forEach(b => b.onclick = () => { if (!confirm('Remove this transaction? It is kept in the audit log, and refused if later transactions depend on it.')) return; busy(b, async () => { await api('/fs2/portfolio/transactions/' + b.dataset.del, { method: 'DELETE' }); route(); }); });
      return;
    }
    if (tab === 'construct') {
      body.innerHTML = `<div class="card"><div class="form"><label class="f">Assets (comma separated)<input id="opA" value="${esc(held.map(p => p.asset_id).join(', ') || 'SPY, TLT, GOLD, EFA, HYG')}"></label>
        <label class="f">Expected returns from<select id="opS"><option value="historical">history (shrunk toward 6%)</option><option value="shaffer">the 12-month Shaffer expected return (when calibration supports it)</option><option value="ml">the 12-month ML ensemble</option></select></label>
        <label class="f">Max weight per asset<input id="opC" type="number" step="0.05" value="0.4"></label><label class="f">Years of history<input id="opY" type="number" value="5"></label><div><button class="primary" id="opGo" style="width:100%;justify-content:center">Optimise</button></div></div>
        <p class="hint">Long-only, fully invested. Mean-variance optimisation is very sensitive to expected returns; treat the output as a comparison of trade-offs, not an answer.</p></div><div id="opR" style="margin-top:16px"></div>`;
      $('#opGo').onclick = () => busy($('#opGo'), async () => {
        const r = await post('/fs2/portfolio/optimize', { assets: $('#opA').value.split(',').map(x => x.trim().toUpperCase()).filter(Boolean), returns: $('#opS').value, cap: +$('#opC').value, years: +$('#opY').value });
        const colors = { 'Current': css('--warn'), 'Minimum variance': css('--teal'), 'Maximum Sharpe': css('--pos'), 'Risk parity': css('--violet'), 'Equal weight': css('--pink') };
        $('#opR').innerHTML = `<div class="grid g-main"><div class="card"><h2>Efficient frontier <small>expected return vs volatility · dots are single assets</small></h2><div id="opF"></div><div class="legend">${r.portfolios.map(p => `<span><i style="background:${colors[p.name]}"></i>${esc(p.name)}</span>`).join('')}</div></div>
          <div class="card flush"><h2>Portfolios</h2><div id="opP"></div></div></div><div class="card flush" style="margin-top:16px"><h2>Weights and risk contributions</h2><div id="opW"></div></div>`;
        scatter($('#opF'), [...r.assets_points.map(p => ({ x: p.vol, y: p.ret, label: p.asset, text: p.asset, r: 4 })), ...r.portfolios.map(p => ({ x: p.vol, y: p.ret, label: p.name, color: colors[p.name], r: 7 }))], { line: r.frontier.map(p => ({ x: p.vol, y: p.ret })), xLabel: 'volatility', h: 320 });
        table($('#opP'), r.portfolios, [{ k: 'name', label: 'Portfolio', l: 1, f: p => `<b>${esc(p.name)}</b>` }, { k: 'ret', label: 'E[return]', f: p => fmt.pct(p.ret, 1) }, { k: 'vol', label: 'Vol', f: p => fmt.pct(p.vol, 1) }, { k: 'sharpe', label: 'Sharpe', f: p => fmt.num(p.sharpe, 2) }, { k: 'diversification_ratio', label: 'Diversification', f: p => fmt.num(p.diversification_ratio, 2) }], { sortKey: null });
        const rowsW = r.assets.map(a => ({ a, ...Object.fromEntries(r.portfolios.map(p => [p.name, p.weights[a]])), ...Object.fromEntries(r.portfolios.map(p => ['rc:' + p.name, p.risk_contributions[a]])) }));
        table($('#opW'), rowsW, [{ k: 'a', label: 'Asset', l: 1 }, ...r.portfolios.map(p => ({ k: p.name, label: p.name, f: x => `${fmt.pct(x[p.name], 0)} <span class="faint">(${fmt.pct(x['rc:' + p.name], 0)} risk)</span>` }))], { sortKey: null });
      });
      return;
    }
    if (tab === 'drivers') {
      const dr = await api('/fs2/portfolio/drivers'); if (!alive()) return;
      let mc = null; try { mc = held.length ? await post('/fs2/portfolio/montecarlo', { horizon: '1Y', paths: 1000 }) : null; } catch (e) { }
      if (!alive()) return;
      const f = dr.factors || {};
      const top = Object.values(f.shares || {}).map(Math.abs).sort((a, b) => b - a);
      const conc = top.length ? top[0] / (top.reduce((a, b) => a + b, 0) || 1) : null;
      const expRet = held.reduce((a, p) => a + (p.expected_return != null ? p.expected_return * p.weight : 0), 0);
      body.innerHTML = `<div class="tiles">${kpi('Expected 12-month return', held.some(p => p.expected_return != null) ? fmt.spct(expRet, 1) : '—', 'weighted from each holding\'s primary-horizon evidence')}${kpi('Expected volatility', fmt.pct((dr.risk || {}).vol, 1))}${kpi('Probability of a loss over 1 year', mc ? fmt.pct(mc.prob_loss, 0) : '—', mc ? `drawdown beyond 20%: ${fmt.pct(mc.prob_drawdown, 0)}` : '')}${kpi('Factor concentration', conc == null ? '—' : fmt.pct(conc, 0), 'share of explained risk in the largest factor')}${kpi('Explained by factors', fmt.pct(f.r2, 0), 'weekly regression R²')}</div>
        <div class="grid g2"><div class="card"><h2>What currently drives my portfolio</h2><div id="drF"></div></div><div class="card flush"><h2>Factor betas</h2><div id="drB"></div></div></div>`;
      hbars($('#drF'), Object.entries(f.shares || {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).map(([k, v]) => ({ label: k, value: v })), { empty: 'Add positions first' });
      table($('#drB'), Object.keys(f.betas || {}).map(k => ({ k, b: f.betas[k], t: (f.t || {})[k] })), [{ k: 'k', label: 'Factor', l: 1 }, { k: 'b', label: 'Beta', f: x => fmt.num(x.b, 3) }, { k: 't', label: 't-stat', f: x => `<span class="${Math.abs(x.t || 0) > 2 ? 'pos' : 'faint'}">${fmt.num(x.t, 1)}</span>` }], { sortKey: 't' });
    }
  };
  function posTable(el, an, compact) {
    const held = an.positions.filter(p => Math.abs(p.quantity) > 1e-12 || p.realized);
    const H = pref.get('pfH', 'primary');
    const at = p => H === 'primary' || !p.shaffer_by_h ? { score: p.shaffer_score, calibrated: p.calibrated, confidence: p.signal_confidence, expected: p.expected_return, ml: p.ml_score, h: p.primary_horizon } : { ...(p.shaffer_by_h[H] || {}), h: H };
    el.innerHTML = `<div class="row" style="gap:8px;padding:8px 12px;flex-wrap:wrap"><span class="muted" style="font-size:12.5px">Shaffer Score at</span><div class="seg" id="pfH">${['primary', '1D', '1W', '1M', '3M', '6M', '12M'].map(x => `<button data-h="${x}" class="${x === H ? 'on' : ''}">${x === 'primary' ? 'each asset\'s best horizon' : x}</button>`).join('')}</div></div><div class="pt"></div>`;
    $$('#pfH button', el).forEach(b => b.onclick = () => { pref.set('pfH', b.dataset.h); posTable(el, an, compact); });
    const tEl = $('.pt', el);
    const cols = [{ k: 'asset_id', label: 'Ticker', l: 1, f: p => `<b>${esc(p.asset_id)}</b><span class="sub">${esc(p.name || '')}</span>${p.pricing_label ? priceTag(p.pricing_label) : ''}${p.continuous_series ? '<span class="pill warn" style="font-size:10.5px" title="Continuous front-month series: P&L includes roll jumps; can be sold, not bought">continuous series</span>' : ''}` }, ...(compact ? [] : [{ k: 'asset_class', label: 'Class', l: 1, f: p => `<span class="pill">${esc(clsName(p.asset_class))}</span>` }]),
      { k: 'quantity', label: 'Quantity', f: p => `${fmt.qty(p.quantity)}${p.quantity < 0 ? ' <span class="pill neg" style="font-size:10px">short</span>' : ''}` }, { k: 'price', label: 'Price', f: p => fmt.px(p.price) }, { k: 'market_value', label: 'Value', f: p => fmt.money(p.market_value) },
      { k: 'exposure', label: 'Exposure', title: 'economic exposure: notional for futures, delta-dollars for options', f: p => fmt.money(p.exposure) },
      ...(compact ? [] : [{ k: 'cost_basis', label: 'Cost', f: p => fmt.money(p.cost_basis) }]), { k: 'unrealized', label: 'Unrealised', cls: p => sign(p.unrealized), f: p => fmt.signed(p.unrealized) },
      ...(compact ? [] : [{ k: 'realized', label: 'Realised', cls: p => sign(p.realized), f: p => fmt.signed(p.realized) }]), { k: 'weight', label: 'Weight', f: p => fmt.pct(p.weight, 1) },
      ...(compact ? [] : [{ k: 'beta', label: 'Beta', f: p => fmt.num(p.beta, 2) }, { k: 'volatility', label: 'Vol', f: p => fmt.pct(p.volatility, 1) }, { k: 'sharpe', label: 'Sharpe', f: p => fmt.num(p.sharpe, 2) }]),
      { k: 'shaffer_score', label: 'Shaffer', v: p => at(p).score, f: p => `<b class="${scoreCls(at(p).score)}">${scoreTxt(at(p).score)}</b>` }, { k: 'cal', label: 'Calibrated', v: p => at(p).calibrated, f: p => `<span class="${scoreCls(at(p).calibrated)}">${scoreTxt(at(p).calibrated)}</span>` }, { k: 'ml_score', label: 'ML', v: p => at(p).ml, f: p => `<span class="${scoreCls(at(p).ml)}">${scoreTxt(at(p).ml)}</span>` }, 
      ...(compact ? [] : [{ k: 'expected_return', label: 'E[return]', v: p => at(p).expected, f: p => fmt.spct(at(p).expected, 1) }, { k: 'risk_contribution', label: 'Risk share', f: p => fmt.pct(p.risk_contribution, 1) }, { k: 'signal_confidence', label: 'Confidence', v: p => at(p).confidence, f: p => pct0(at(p).confidence) }]),
      { k: 'primary_horizon', label: 'Horizon', l: 1, f: p => esc(at(p).h || (p.shaffer_score == null ? (p.instrument ? 'derivative' : 'not researched') : '—')) },
      { k: 'hx', label: '', nosort: 1, f: p => Math.abs(p.quantity) > 1e-12 ? `<button class="small" data-hedge="${esc(p.asset_id)}">Analyze hedge</button>` : '' }];
    table(tEl, held, cols, { sortKey: 'market_value', maxH: 640, onRow: p => { if (!p.instrument) go('#/asset/' + encodeURIComponent(p.asset_id)); }, empty: 'No positions: use + Transaction to deposit cash and buy',
      after: t => $$('[data-hedge]', t).forEach(b => b.onclick = e => { e.stopPropagation(); go('#/hedge/' + encodeURIComponent(b.dataset.hedge)); }) });
  }
  function txModal() {
    const today = (S.status && S.status.last_price_date) || new Date().toISOString().slice(0, 10);
    const m = modal(`<h2 style="margin-top:0">New transaction</h2><div class="seg" id="txK" style="margin-bottom:12px"><button class="on" data-k="BUY">Buy</button><button data-k="SELL">Sell</button><button data-k="DEPOSIT">Deposit</button><button data-k="WITHDRAW">Withdraw</button></div>
      <div class="form" style="grid-template-columns:1fr 1fr"><div id="txAW"><label class="f">Asset${assetPicker('txA', S.current)}</label></div><label class="f">Date<input id="txD" type="date" value="${today}"></label>
        <label class="f" id="txQL">Quantity<input id="txQ" type="number" min="0" placeholder="units"></label><label class="f" id="txML">…or amount (USD)<input id="txM" type="number" min="0" placeholder="e.g. 100000"></label>
        <label class="f" id="txPL">Price you paid (optional)<input id="txP" type="number" min="0" placeholder="blank: close of that date"></label><label class="f" id="txFL">Fee (USD)<input id="txF" type="number" min="0" value="0"></label><label class="f" style="grid-column:1/-1">Note<input id="txN"></label></div>
      <p class="hint">A price you type is taken as the price actually paid that day, in that day's share basis: later splits are applied automatically. Buys cannot exceed the cash available at that date.</p>
      <div class="row" style="margin-top:14px"><span class="spacer"></span><button onclick="document.getElementById('modal').innerHTML=''">Cancel</button><button class="primary" id="txGo">Record</button></div><div id="txErr" class="neg"></div>`);
    let kind = 'BUY';
    bindPicker('txA', x => { $('#txA').value = x; });
    const sync = () => { const cash = kind === 'DEPOSIT' || kind === 'WITHDRAW'; ['#txAW', '#txQL', '#txPL', '#txFL'].forEach(s => { $(s, m).style.display = cash ? 'none' : ''; }); $('#txML', m).firstChild.textContent = cash ? 'Amount (USD)' : '…or amount (USD)'; };
    $$('#txK button', m).forEach(b => b.onclick = () => { kind = b.dataset.k; $$('#txK button', m).forEach(x => x.classList.toggle('on', x === b)); sync(); });
    $('#txGo', m).onclick = () => busy($('#txGo', m), async () => {
      try {
        const d = $('#txD', m).value || null; const amt = N($('#txM', m).value); const q = N($('#txQ', m).value);
        if (kind === 'DEPOSIT' || kind === 'WITHDRAW') await post('/fs2/portfolio/' + kind.toLowerCase(), { amount: amt, date: d, note: $('#txN', m).value });
        else await post('/fs2/portfolio/trade', { asset_id: $('#txA', m).value.trim().toUpperCase(), side: kind, quantity: q, amount: q ? null : amt, price: N($('#txP', m).value), fee: N($('#txF', m).value) || 0, date: d, note: $('#txN', m).value });
        closeModal(); toast('Recorded'); route();
      } catch (e) { $('#txErr', m).textContent = e.message; }
    });
  }

  // ---------------------------------------------------------------- Shaffer Hedge: shared renderers
  // hedge objectives grouped by what the hedge is for: a product that is poor at cutting variance (an index put) can be
  // excellent tail protection, so the objective decides how every candidate is judged
  const OBJ_GROUPS = [
    ['', [['auto', 'Automatic (the largest risk)']]],
    ['Variance reduction', [['min_variance', 'Minimum variance'], ['target_vol', 'Target volatility']]],
    ['Beta reduction', [['beta', 'Reduce market beta'], ['neutral', 'Neutralize market beta']]],
    ['Tail protection', [['crash', 'Crash protection (−20% scenario)'], ['es', 'Expected Shortfall'], ['var', 'Value at Risk']]],
    ['Drawdown protection', [['drawdown', 'Drawdown']]],
    ['Factor neutralization', [['systematic', 'Systematic risk (all but single-name)'], ['sector', 'Sector / industry exposure'], ['name', 'A specific position'],
      ['duration', 'Duration (rates)'], ['curve', 'Yield curve, key rate by key rate'], ['credit', 'Credit spread (CS01)'], ['fx', 'Currency exposure'],
      ['commodity', 'Commodity exposure'], ['crypto', 'Crypto exposure'], ['volatility', 'Volatility (vega)']]]];
  const OBJECTIVES = OBJ_GROUPS.flatMap(([, xs]) => xs);
  const OBJ_GROUP_OF = Object.fromEntries(OBJ_GROUPS.flatMap(([g, xs]) => xs.map(([k]) => [k, g])));
  const objOptions = sel => OBJ_GROUPS.map(([g, xs]) => { const o = xs.map(([k, l]) => `<option value="${k}" ${k === sel ? 'selected' : ''}>${esc(l)}</option>`).join(''); return g ? `<optgroup label="${esc(g)}">${o}</optgroup>` : o; }).join('');
  const HEDGE_H = ['1D', '1W', '1M', '3M', '6M', '12M'];
  function fxv(f, v) {
    if (N(v) == null) return '—';
    if (/^(RATE|REAL):/.test(f)) return 'DV01 ' + fmt.money(-v, 0);
    if (/^CREDIT:/.test(f)) return 'CS01 ' + fmt.money(-v, 0);
    if (f === 'VOL') return fmt.signed(v, 0) + '/VIX pt';
    return fmt.big(v);
  }
  const unitHint = f => /^(RATE|REAL):/.test(f) ? '$ per 1bp' : /^CREDIT:/.test(f) ? '$ per 1bp of spread' : f === 'VOL' ? '$ per VIX point' : f === 'MKT' ? 'beta-dollars' : /^FX:/.test(f) ? 'currency dollars' : /^(SEC|IND|STY):/.test(f) ? 'beta-dollars to the spread' : /^IDIO:/.test(f) ? 'single-name dollars' : 'dollars';
  const riskRows = [['beta', 'Beta', v => fmt.num(v, 2)], ['beta_usd', 'Beta-dollars', v => fmt.big(v)], ['dv01', 'DV01 ($/bp)', v => fmt.money(v, 0)], ['cs01', 'CS01 ($/bp)', v => fmt.money(v, 0)],
    ['vol_usd_per_vix', 'Vega ($/VIX pt)', v => fmt.signed(v, 0)], ['sigma_daily', 'Volatility ($/day)', v => fmt.money(v, 0)], ['sigma_annual_pct', 'Volatility (annual, % NAV)', v => fmt.pct(v, 1)],
    ['var95', '1-day VaR 95%', v => fmt.money(v, 0)], ['var95_corr1', '1-day VaR 95% if correlations → 1', v => fmt.money(v, 0)], ['es95', '1-day Expected Shortfall 95%', v => fmt.money(v, 0)], ['expected_drawdown_1y', 'Expected drawdown (1Y, median)', v => fmt.money(v, 0)],
    ['largest_share', 'Largest factor share of risk', v => fmt.pct(v, 0)]];
  function riskCompare(books, labels) {
    const keys = Object.keys(labels).filter(k => books[k]);
    const fxKeys = [...new Set(keys.flatMap(k => Object.keys(books[k].fx || {})))].slice(0, 4);
    const rows = riskRows.map(([k, lab, f]) => `<tr><td class="l">${lab}</td>${keys.map(b => `<td>${f(books[b][k])}</td>`).join('')}</tr>`).join('')
      + fxKeys.map(c => `<tr><td class="l">${esc(c)} exposure</td>${keys.map(b => `<td>${fmt.big((books[b].fx || {})[c])}</td>`).join('')}</tr>`).join('')
      + `<tr><td class="l">Delta / gamma / vega / theta</td>${keys.map(b => { const g = books[b].greeks || {}; return `<td class="faint" style="font-size:12px">${fmt.big(g.delta_usd)} / ${fmt.num(g.gamma_usd_1pct, 0)} / ${fmt.num(g.vega_usd, 0)} / ${fmt.num(g.theta_usd, 0)}</td>`; }).join('')}</tr>`;
    return `<div class="tbl-wrap"><table><thead><tr><th class="l">Risk</th>${keys.map(k => `<th>${esc(labels[k])}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  const qPill = q => q ? `<span class="pill ${q === 'HIGH' ? 'pos' : q === 'NEGATIVE' ? 'neg' : q === 'MEDIUM' ? '' : 'warn'}" style="font-size:10px">${esc(q)}</span>` : '';
  const priceTag = lab => lab ? `<span class="pill ${/FLAT VOLATILITY/.test(lab) ? 'warn' : ''}" style="font-size:10.5px;white-space:normal" title="${/FLAT VOLATILITY/.test(lab) ? 'No option-chain or skew data: every strike uses the at-the-money implied volatility, so out-of-the-money puts are probably priced too cheaply and crash hedges look more attractive than they are.' : /SURFACE/.test(lab) ? 'Priced off the implied-volatility surface of the underlying\'s option chain (skew and term structure), not this contract\'s own quote.' : /MARKET QUOTE/.test(lab) ? 'The contract\'s own quoted bid/ask mid.' : 'Priced at fair value from the underlying, rates and dividends: not an exchange quote.'}">${esc(lab)}</span>` : '';
  function legDetail(L) {
    if (L.option) {
      const o = L.option;
      const path = (o.hedge_ratio_scenarios || []).map(p => `<td>${fmt.pct(p.hedge_ratio, 0)}</td>`).join('');
      return `<div style="margin-top:6px;font-size:12.5px;line-height:1.6">${priceTag(L.pricing_label)} <b>Option</b>: ${o.right === 'P' ? 'put' : 'call'} ${fmt.px(o.strike)} expiring ${fmt.date(o.expiry)} (${esc(o.style)}) · premium ${fmt.money(o.premium_per_contract, 0)} per contract, ${fmt.money(o.premium_total, 0)} total (${fmt.pct(o.premium_total / (L.nav || 1), 2)} of NAV) · Δ ${fmt.num(o.delta, 3)} · Γ ${fmt.num(o.gamma, 5)} · vega ${fmt.money(o.vega_usd, 0)}/vol pt · theta ${fmt.money(o.theta_usd, 0)}/day · implied vol ${fmt.pct(o.iv, 1)}<br>
        <b>Delta-adjusted notional ${fmt.money(o.delta_adjusted_notional, 0)}</b> (contracts × 100 × underlying × Δ): the hedge exposure. The premium is its cost.</div>
        <div class="tbl-wrap" style="margin-top:6px"><table><thead><tr><th class="l">Market move</th>${(o.hedge_ratio_scenarios || []).map(p => `<th>${fmt.spct(p.market_move, 0)}</th>`).join('')}</tr></thead><tbody><tr><td class="l">Hedge ratio (delta re-computed)</td>${path}</tr><tr><td class="l">Option value</td>${(o.hedge_ratio_scenarios || []).map(p => `<td>${fmt.money(p.option_value, 0)}</td>`).join('')}</tr></tbody></table></div>`;
    }
    if (L.type === 'FUTURE') {
      return `<div style="margin-top:6px;font-size:12.5px;line-height:1.6"><b>Future</b>: price ${fmt.px(L.price)} × multiplier ${fmt.num(L.multiplier, L.multiplier < 1 ? 1 : 0)} = contract notional ${fmt.money(L.unit_notional, 0)} · ${fmt.num(Math.abs(L.quantity), 0)} contracts = <b>${fmt.money(L.notional, 0)} notional</b> ·
        ${L.dv01_per_contract != null ? `DV01 ${fmt.money(L.dv01_per_contract, 2)} per contract · ` : L.beta != null ? `β ${fmt.num(L.beta, 2)} · ` : ''}margin (estimate) ${fmt.money(L.margin_estimate, 0)} — collateral, not exposure · expires ${fmt.date(L.expiry)}, roll by ${fmt.date(L.roll)}${(L.cost || {}).rolls ? ` (${L.cost.rolls} roll(s) inside the horizon)` : ''}</div>`;
    }
    if (L.type === 'FORWARD') return `<div style="margin-top:6px;font-size:12.5px;line-height:1.6"><b>FX forward</b>: ${fmt.num(Math.abs(L.quantity), 0)} units of the base currency at ${fmt.px(L.price)} value ${fmt.date(L.expiry)}; forward points ${fmt.signed(((L.cost || {}).info || {}).forward_points, 0)} over the horizon.</div>`;
    return '';
  }
  function packageTable(pkg, nav) {
    if (!pkg || !pkg.length) return '<p class="muted">No hedge: nothing eligible can move this risk, or the change is smaller than one tradeable lot.</p>';
    return `<div class="tbl-wrap"><table><thead><tr><th class="l">Hedge leg</th><th>Side</th><th>Quantity</th><th class="l">Risk unit</th><th>Notional</th><th>Est. cost (horizon)</th><th>Cost % NAV</th><th class="l">Why this leg</th></tr></thead><tbody>
      ${pkg.map(L => `<tr><td class="l"><b>${esc(L.id)}</b><span class="sub">${esc(L.name)}</span></td><td><span class="pill ${L.side === 'BUY' ? 'pos' : 'neg'}">${L.side}</span></td>
        <td>${fmt.qty(Math.abs(L.quantity))} ${esc(L.units)}${L.raw_quantity != null && L.raw_quantity !== L.quantity ? `<span class="sub">raw ${fmt.qty(Math.abs(L.raw_quantity))}</span>` : ''}</td>
        <td class="l wrap" style="font-size:12px;max-width:240px">${esc(L.risk_unit)}<span class="sub">${esc(L.sizing_rule)}</span></td><td>${fmt.money(L.notional, 0)}</td><td>${fmt.money((L.cost || {}).total, 0)}</td><td>${fmt.pct(L.cost_pct_nav, 2)}</td>
        <td class="l wrap" style="font-size:12.5px;max-width:320px;min-width:190px">${esc(L.why)}</td></tr>${legDetail({ ...L, nav }) ? `<tr><td colspan="8" class="l" style="background:var(--surface-2)">${legDetail({ ...L, nav })}</td></tr>` : ''}`).join('')}</tbody></table></div>`;
  }
  // profit-aware hedge designs (hedge/designs.py): shared by the trade ticket and Analytics → Shaffer Hedge
  function designsTable(el, d, lam, selected, onPick) {
    const U = r => r.risk_reduction - lam * r.profit_sacrificed - (r.cost || 0);
    const ok = d.designs.filter(r => !r.unavailable);
    const best = ok.reduce((a, r) => (a == null || U(r) > U(a) ? r : a), null);
    const e = d.expected || {}, rk = d.risk_measure === 'ES 95%' ? 'es95' : 'sd';
    el.innerHTML = `<div class="muted" style="font-size:12.5px;margin-bottom:6px">Expected return over ${esc(d.horizon)}: <b>${fmt.spct(e.value, 1)}</b> — ${esc(e.source || '')}${e.evidence != null ? ` (Shaffer evidence ${fmt.spct(e.evidence, 1)})` : ''}. Risk = ${esc(d.risk_measure)} of the ${esc(d.horizon)} P&L over ${d.scenarios} historical windows (demeaned: the sample's past drift is not a forecast). Utility = risk removed − λ × expected profit given up − cost.</div>
      ${d.thesis ? `<div class="pill ${/disagrees/.test(d.thesis) ? 'neg' : 'pos'}" style="white-space:normal;margin-bottom:8px">${esc(d.thesis)}</div>` : ''}
      ${e.reliability ? `<div class="faint" style="font-size:12px;margin-bottom:8px">Score reliability (ML Lab, informational — not used by the hedge math): historically a production score with |score| in ${esc(e.reliability.band)} at ${esc(d.horizon)} had the direction right ${fmt.pct(e.reliability.correct, 0)} of the time [${fmt.pct(e.reliability.lo, 0)}, ${fmt.pct(e.reliability.hi, 0)}], vs ${fmt.pct(e.reliability.baseline_acc, 0)} for the best naive rule (${esc((e.reliability.best_baseline || '').replace(/_/g, ' '))}) — ${e.reliability.validated ? '<b>better than the baseline</b>' : 'not better than the baseline'}.</div>` : ''}
      ${e.research ? `<div class="faint" style="font-size:12px;margin-bottom:8px">Research view (ML Lab, informational — not used by the hedge math): Shaffer Alpha <b>${fmt.num(e.research.alpha, 0)}</b> (setup vs normal) · Shaffer Directional ${e.research.p_up != null ? `<b>${fmt.pct(e.research.p_up, 0)} up</b>` : '—'} over ${esc(d.horizon)} · expected return ${fmt.spct(e.research.expected_return, 1)} (base prior ${fmt.spct(e.research.prior_return, 1)}, ${esc(e.research.prior || '')}) · horizon risk σ ${fmt.pct(e.research.sigma_h, 1)} · ${e.research.validated ? 'validated' : `<span class="warn">not validated (${esc(e.research.status || '')})</span>`}.</div>` : ''}
      ${(d.notes || []).map(n => `<div class="pill warn" style="white-space:normal;margin:0 6px 8px 0;font-size:11.5px">${esc(n)}</div>`).join('')}
      <div class="tbl-wrap"><table><thead><tr><th class="l">Design</th><th class="l">Legs</th><th>Expected P&L</th><th>${esc(d.risk_measure)}</th><th>Risk removed</th><th>Profit given up</th><th>Cost</th><th>Upside kept</th><th>Utility (λ ${lam})</th>${onPick ? '<th></th>' : ''}</tr></thead><tbody>
      ${d.designs.map(r => r.unavailable ? `<tr class="faint"><td class="l">${esc(r.label)}</td><td class="l" colspan="${onPick ? 9 : 8}">${esc(r.unavailable)}</td></tr>` : `<tr><td class="l"><b>${esc(r.label)}</b>${best && r.key === best.key ? ` <span class="pill pos" style="font-size:10px">best at λ ${lam}</span>` : ''}${(r.pricing_labels || []).length ? '<span class="sub">' + r.pricing_labels.map(x => esc(x)).join('; ') + '</span>' : ''}</td>
        <td class="l">${(r.legs || []).map(L => `${L.quantity > 0 ? 'buy' : 'sell'} ${fmt.qty(Math.abs(L.quantity))} ${esc(L.id)}`).join('<br>') || '—'}</td><td>${fmt.signed(r.stats.mean, 0)}</td><td>${fmt.money(r.stats[rk], 0)}</td>
        <td>${fmt.signed(r.risk_reduction, 0)}</td><td>${fmt.signed(r.profit_sacrificed, 0)}</td><td>${fmt.money(r.cost || 0, 0)}</td><td>${fmt.pct(r.upside_kept, 0)}</td><td class="${U(r) >= 0 ? 'pos' : 'neg'}">${fmt.signed(U(r), 0)}</td>
        ${onPick ? `<td>${r.key === 'none' ? '' : `<button class="small ${selected === r.key ? 'primary' : 'ghost'}" data-dz="${r.key}">${selected === r.key ? 'Selected' : 'Use this design'}</button>`}</td>` : ''}</tr>`).join('')}</tbody></table></div>
      <div class="faint" style="font-size:12px;margin-top:6px">Best by λ: ${Object.entries(d.sensitivity || {}).map(([l, k]) => `λ ${l} → ${esc((d.designs.find(x => x.key === k) || {}).label || k)}`).join(' · ')}. A larger λ values expected profit more (hedges must earn their keep); a smaller λ cares mostly about the loss.</div>`;
    if (onPick) $$('[data-dz]', el).forEach(b => b.onclick = () => onPick(b.dataset.dz));
  }

  function hedgePanel(el, ana, o = {}) {
    const S0 = ana.targeted || [];
    const warn = (ana.warnings || []).map(w => `<div class="pill warn" style="white-space:normal;margin:4px 0">${esc(w)}</div>`).join('');
    const facs = (ana.factors || []).filter(f => f.targeted || Math.abs(f.share_of_risk || 0) > 0.03).slice(0, 14);
    const best = ana.best || {};
    const badge = id => ['best_score', 'best_risk_match', 'cheapest', 'lowest_basis', 'best_crash'].filter(k => best[k] === id).map(k => `<span class="pill" style="font-size:10.5px">${{ best_score: 'best score', best_risk_match: 'best risk match', cheapest: 'cheapest', lowest_basis: 'lowest basis risk', best_crash: 'best crash hedge' }[k]}</span>`).join(' ');
    const el_ = (ana.candidates || []).filter(c => c.status === 'ELIGIBLE');
    const ne = (ana.candidates || []).filter(c => c.status !== 'ELIGIBLE');
    const ml = ana.ml || {};
    const mlLegs = Object.entries(ml.by_leg || {});
    const mlTxt = mlLegs.length ? mlLegs.map(([id, a]) => a.verified ? `${esc(id)}: ${fmt.spct(a.applied, 1)} (capped at ±${fmt.pct(ml.alpha * ml.cap, 0)})` : `${esc(id)}: 0 — ${esc((a.reasons || []).join('; ') || 'not verified')}`).join('<br>') : esc(ml.note || 'no legs');
    el.innerHTML = `${warn}
      <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px"><div><b>${esc(ana.objective_label)}</b> · primary risk <b>${esc(ana.primary_label || '—')}</b> · horizon ${esc(ana.horizon)} · data ${fmt.date(ana.asof)}${ana.auto ? ' · <span class="pill">chosen automatically</span>' : ''}</div>
        <div class="faint" style="font-size:12px">Raw Shaffer Hedge = risk mathematics. The ML adjustment is separate and capped.</div></div>
      <h3 style="margin:14px 0 6px">Risk to hedge</h3>
      <div class="tbl-wrap"><table><thead><tr><th class="l">Risk factor</th><th class="l">Unit</th><th>Current</th><th>Target</th><th>After raw hedge</th><th>Hedge % (raw)</th><th>After final</th><th>Share of risk now</th></tr></thead><tbody>
      ${facs.map(f => `<tr class="${f.targeted ? '' : 'faint'}"><td class="l">${f.targeted ? '<b>' : ''}${esc(f.label)}${f.targeted ? '</b>' : ''}</td><td class="l" style="font-size:12px">${esc(unitHint(f.factor))}</td><td>${fxv(f.factor, f.current)}</td><td>${f.targeted ? fxv(f.factor, f.target) : '—'}</td><td>${fxv(f.factor, f.after_raw)}</td><td>${fmt.pct(f.hedge_pct_raw, 0)}</td><td>${fxv(f.factor, f.after_final)}</td><td>${fmt.pct(f.share_of_risk, 0)}</td></tr>`).join('')}</tbody></table></div>
      <h3 style="margin:16px 0 6px">Recommended hedge (Raw Shaffer Hedge → final)</h3>
      ${packageTable(ana.package.final && ana.package.final.length ? ana.package.final : ana.package.raw, ana.nav)}
      <div class="muted" style="font-size:12.5px;margin-top:6px"><b>ML adjustment</b> (FinalHedge = RawHedge × (1 + α·MLAdjustment), α ${ml.alpha}, |adjustment| ≤ ${ml.cap}): ${mlTxt}</div>
      <h3 style="margin:16px 0 6px">Candidate products for this risk <span class="faint" style="font-weight:400;font-size:12px">(click one to use it alone)</span></h3>
      ${!['crash', 'es', 'var'].includes(ana.objective) && el_.some(c => /^tail hedge/.test(c.hedge_type || '')) ? `<div class="muted" style="font-size:12.5px;margin-bottom:6px">Some candidates are <b>tail hedges</b>: historically they cut the worst outcomes but not everyday variance, so they score low when judged on ${esc((el_[0] || {}).judged_on || 'variance')}. Choose <b>Tail protection</b> to judge products on the tail.</div>` : ''}
      <div class="tbl-wrap"><table class="wraph"><thead><tr><th class="l">Product</th><th>Shaffer Hedge Score<br><span style="text-transform:none;letter-spacing:0;font-weight:400">judged on ${esc(((el_[0] || {}).judged_on) || 'variance')}</span></th><th>Unit-rule size</th><th>Notional</th><th>Est. cost</th><th>Exp. variance cut</th><th>Basis ($/day)</th><th>ADV use</th><th>Variance hedge<br><span style="text-transform:none;letter-spacing:0;font-weight:400">walk-forward cut</span></th><th>Tail hedge<br><span style="text-transform:none;letter-spacing:0;font-weight:400">worst 10% cut</span></th><th>Tail convexity</th><th class="l">E·Q·L·R·B·T·M</th></tr></thead><tbody>
      ${el_.slice(0, 14).map(c => { const h = c.history || {}, k = c.components || {}; return `<tr class="click" data-cand="${esc(c.id)}"><td class="l wrap" style="max-width:250px"><b>${esc(c.id)}</b><span class="sub">${esc(c.name || '')} · ${esc(c.product || '')}</span>${badge(c.id)}${c.pricing_label && /FLAT/.test(c.pricing_label) ? ' ' + priceTag(c.pricing_label) : ''}</td>
        <td><b class="${scoreCls(c.score)}">${scoreTxt(c.score)}</b>${c.hedge_type ? `<span class="sub" style="white-space:normal">${esc(c.hedge_type)}</span>` : ''}</td><td class="wrap" style="max-width:190px">${c.side === 'BUY' ? '+' : '−'}${fmt.qty(Math.abs(c.unit_quantity))}<span class="sub">${esc(c.risk_unit || '')}</span></td><td>${fmt.money(c.notional, 0)}</td>
        <td>${fmt.money(c.cost_total, 0)}</td><td>${fmt.pct(c.expected_reduction, 0)}</td><td>${fmt.money(c.basis_risk_daily, 0)}</td><td>${fmt.pct(c.participation, 2)}</td>
        <td class="wrap" style="max-width:150px">${qPill(c.variance_quality)} ${h.n ? fmt.pct(h.realized_reduction, 0) : fmt.pct(c.expected_reduction, 0) + ' <span class="faint">exp.</span>'}${h.n ? `<span class="sub">${h.n} windows since ${esc((h.first || '').slice(0, 4))}</span>` : ''}</td>
        <td>${qPill(c.tail_quality)} ${h.n && h.tail_reduction != null ? fmt.pct(h.tail_reduction, 0) : '<span class="faint">—</span>'}</td>
        <td>${c.convexity == null ? '<span class="faint">NONE (linear)</span>' : `<b>×${fmt.num(c.convexity, 2)}</b><span class="sub">gain at −20% vs same-delta linear</span>`}</td>
        <td class="l mono wrap" style="font-size:11px;max-width:150px;min-width:120px">${[k.E, k.Q, k.L, k.R, k.B, k.T, k.M].map(x => fmt.num(x, 2)).join(' · ')}</td></tr>`; }).join('')}</tbody></table></div>
      ${ne.length ? `<details style="margin-top:8px"><summary class="muted">${ne.length} product(s) not eligible or not relevant — why</summary><div class="tbl-wrap"><table><tbody>${ne.map(c => `<tr><td class="l"><b>${esc(c.id)}</b></td><td class="l"><span class="pill ${c.status === 'NOT RELEVANT' ? '' : 'warn'}">${esc(c.status || '')}</span></td><td class="l" style="font-size:12.5px">${esc((c.reasons || []).join('; '))}</td></tr>`).join('')}</tbody></table></div></details>` : ''}
      <h3 style="margin:16px 0 6px">Before and after</h3>
      ${riskCompare(o.books || ana.risk, o.bookLabels || { before: 'Before', raw: 'After raw hedge', final: 'After final hedge' })}
      <h3 style="margin:16px 0 6px">Scenarios <span class="faint" style="font-weight:400;font-size:12px">(options fully re-priced; the VIX moves with the market as it has on down and up days)</span></h3>
      <div class="tbl-wrap"><table><thead><tr><th class="l">Scenario</th><th>${esc(o.scenBefore || 'Unhedged')}</th><th>Hedge</th><th>Hedged</th><th>Loss offset</th></tr></thead><tbody>
      ${(ana.scenarios || []).map(s => `<tr><td class="l">${esc(s.label)}</td><td class="${sign(s.before)}">${fmt.signed(s.before, 0)}</td><td class="${sign(s.hedge_final)}">${fmt.signed(s.hedge_final, 0)}</td><td class="${sign(s.after_final)}">${fmt.signed(s.after_final, 0)}</td><td>${s.before < 0 ? fmt.pct(-s.hedge_final / s.before, 0) : '—'}</td></tr>`).join('')}</tbody></table></div>`;
    $$('[data-cand]', el).forEach(tr => tr.onclick = () => o.onPick && o.onPick(tr.dataset.cand));
  }

  // ---------------------------------------------------------------- the Shaffer Hedge page
  pages.hedge = async (main, args, alive) => {
    const only = args[0];
    const st = { objective: only ? 'auto' : pref.get('hObj', 'auto'), horizon: pref.get('hH', '1M'), reduction: pref.get('hRed', 0.5), use: null };
    main.innerHTML = `<div class="page-head"><div><h1>Shaffer Hedge</h1><p>Risk first: what the ${only ? esc(only) + ' position' : 'portfolio'} is exposed to, in each risk's own unit; then the products that carry that risk, each sized in its own unit (beta-dollars, DV01, CS01, currency, delta-adjusted notional), compared on effectiveness, cost, basis, liquidity and their walk-forward record.</p></div>
      <div class="row">${only ? `<button class="ghost" id="hAll">Whole portfolio</button>` : ''}</div></div>
      <div id="hKpi"></div>
      <div class="card"><div class="row" style="gap:12px;flex-wrap:wrap;align-items:flex-end">
        <label class="f">Objective<select id="hObj">${objOptions(st.objective)}</select></label>
        <label class="f">Horizon<select id="hH">${HEDGE_H.map(h => `<option ${h === st.horizon ? 'selected' : ''}>${h}</option>`).join('')}</select></label>
        <div><div class="muted" style="font-size:12px;margin-bottom:4px">Hedge</div><div class="seg" id="hRed">${[0.25, 0.5, 0.75, 1].map(x => `<button data-r="${x}" class="${x === st.reduction ? 'on' : ''}">${x * 100}%</button>`).join('')}<input id="hCustom" type="number" min="1" max="100" placeholder="custom %" style="width:92px;min-height:30px"></div></div>
        <button class="primary" id="hGo">Analyze</button></div></div>
      <div class="card" style="margin-top:16px"><div id="hOut"><div class="loading">Measuring the risk vector and testing every eligible hedge walk-forward…</div></div></div>
      <div class="card" style="margin-top:16px"><h2>Hedge ledger</h2><p class="muted">Every executed or declined Shaffer Hedge proposal, never overwritten; graded once its horizon has passed (realised variance reduction, hedge P&L, upside given up, basis error, effectiveness = realised ÷ expected).</p><div id="hLedger"></div></div>`;
    if ($('#hAll')) $('#hAll').onclick = () => go('#/hedge');
    const run = async () => {
      $('#hOut').innerHTML = '<div class="loading">Measuring the risk vector and testing every eligible hedge walk-forward…</div>';
      let positions = null;
      if (only) { const pf = await api('/fs2/portfolio'); const p = pf.positions.find(x => x.asset_id === only); positions = p ? [{ id: only, quantity: p.quantity, entry: p.entry }] : null; if (!positions) { $('#hOut').innerHTML = `<p class="muted">${esc(only)} is not held.</p>`; return; } }
      const params = { horizon: st.horizon, reduction: st.reduction };
      if (st.use) params.use = [st.use], params.candidates = [st.use];
      if (st.objective === 'name' && only) params.asset = only;
      let ana;
      try { ana = await post('/fs2/hedge/analyze', { objective: st.objective, params, positions }); }
      catch (e) { $('#hOut').innerHTML = `<p class="muted">${esc(e.message)}</p>`; $('#hKpi').innerHTML = ''; return; }
      if (!alive()) return;
      const b = ana.risk.before;
      const hedgeLike = ana.positions.filter(p => p.quantity < 0 || p.type !== 'SPOT');
      const core = ana.positions.filter(p => !(p.quantity < 0 || p.type !== 'SPOT'));
      const sumF = (ps, f) => ps.reduce((s, p) => s + ((p.exposures || {})[f] || 0), 0);
      const cov = f => { const c = sumF(core, f); return c ? -sumF(hedgeLike, f) / c : null; };
      const top = (ana.factors || []).slice().sort((x, y) => Math.abs(y.share_of_risk || 0) - Math.abs(x.share_of_risk || 0)).slice(0, 3);
      $('#hKpi').innerHTML = `<div class="tiles">
        <div class="kpi"><div class="k">Portfolio NAV</div><div class="v">${fmt.money(ana.nav, 0)}</div><div class="s">data ${fmt.date(ana.asof)}</div></div>
        <div class="kpi"><div class="k">Current risk</div><div class="v">${fmt.money(b.sigma_daily, 0)}/day</div><div class="s">${fmt.pct(b.sigma_annual_pct, 1)} a year · VaR95 ${fmt.money(b.var95, 0)}</div></div>
        <div class="kpi"><div class="k">Largest risk factors</div><div class="v" style="font-size:15px">${top.map(f => esc(f.label)).join('<br>')}</div><div class="s">${top.map(f => fmt.pct(f.share_of_risk, 0)).join(' · ')} of variance</div></div>
        <div class="kpi"><div class="k">Current hedge coverage</div><div class="v" style="font-size:15px">beta ${fmt.pct(cov('MKT'), 0)}</div><div class="s">shorts and derivatives against long exposure</div></div>
        <div class="kpi"><div class="k">Hedge carrying cost</div><div class="v">${fmt.money(-((b.greeks || {}).theta_usd || 0), 0)}/day</div><div class="s">option time decay (theta); borrow fees are in the ledger</div></div>
        <div class="kpi"><div class="k">Market regime</div><div class="v" style="font-size:14px">${esc(Object.values(ana.regime || {}).filter(Boolean).slice(0, 3).join(' · ') || '—')}</div><div class="s">point-in-time</div></div></div>`;
      const out = $('#hOut');
      out.innerHTML = `<div id="hPanel"></div><div class="row" style="margin-top:14px;gap:10px;justify-content:flex-end"><span class="muted" style="font-size:12.5px">Executes the final hedge legs as one package (all or none).</span><button class="primary" id="hExec" ${ana.package.final.length ? '' : 'disabled'}>Execute hedge</button></div>`;
      hedgePanel($('#hPanel'), ana, { onPick: id => { st.use = id; run(); }, scenBefore: only ? only + ' position' : 'Portfolio' });
      $('#hExec').onclick = () => {
        const legs = ana.package.final;
        const m = modal(`<h2 style="margin-top:0">Execute the hedge</h2><p class="muted">These legs are validated together against cash, collateral and holdings, and recorded in one transaction.</p>${packageTable(legs, ana.nav)}<div id="hxErr" class="neg" style="margin-top:8px"></div><div class="row" style="justify-content:flex-end;gap:8px;margin-top:12px"><button class="ghost" id="hxNo">Cancel</button><button class="primary" id="hxGo">Execute</button></div>`);
        $('.box', m).style.width = 'min(980px,100%)';
        $('#hxNo', m).onclick = closeModal;
        $('#hxGo', m).onclick = () => busy($('#hxGo', m), async () => { try { const r = await post('/fs2/hedge/execute', { primary: null, legs: legs.map(L => ({ id: L.id, quantity: L.side === 'BUY' ? Math.abs(L.quantity) : -Math.abs(L.quantity) })), proposal: ana, mode: 'trade_hedge' }); closeModal(); toast(`Hedge recorded (${r.legs.length} legs, ${r.package_id})`); route(); } catch (e) { $('#hxErr', m).textContent = e.message; } });
      };
    };
    $('#hObj').onchange = e => { st.objective = e.target.value; st.use = null; if (!only) pref.set('hObj', st.objective); run(); };
    $('#hH').onchange = e => { st.horizon = e.target.value; pref.set('hH', st.horizon); run(); };
    $$('#hRed button').forEach(b => b.onclick = () => { st.reduction = +b.dataset.r; pref.set('hRed', st.reduction); $$('#hRed button').forEach(x => x.classList.toggle('on', x === b)); run(); });
    $('#hCustom').onchange = e => { const v = N(e.target.value); if (v) { st.reduction = Math.max(0.01, Math.min(1, v / 100)); $$('#hRed button').forEach(x => x.classList.remove('on')); run(); } };
    $('#hGo').onclick = () => { st.use = null; run(); };
    const led = await api('/fs2/hedge/history').catch(() => []);
    if (alive()) table($('#hLedger'), led, [{ k: 'made_on', label: 'Date', l: 1, f: r => fmt.date(r.made_on) }, { k: 'status', label: 'Status', l: 1, f: r => `<span class="pill ${r.status === 'executed' ? 'pos' : ''}">${esc(r.status)}</span>` },
      { k: 'objective', label: 'Objective', l: 1 }, { k: 'risk_factor', label: 'Risk', l: 1 }, { k: 'horizon', label: 'Horizon', l: 1 }, { k: 'sel', label: 'Hedge', l: 1, nosort: 1, f: r => esc((r.selected || []).map(s => `${s.quantity > 0 ? '+' : ''}${fmt.qty(s.quantity)} ${s.id}`).join(', ')) },
      { k: 'raw_ratio', label: 'Raw %', f: r => fmt.pct(r.raw_ratio, 0) }, { k: 'final_ratio', label: 'Final %', f: r => fmt.pct(r.final_ratio, 0) }, { k: 'expected_cost', label: 'Exp. cost', f: r => fmt.money(r.expected_cost, 0) },
      { k: 'expected_reduction', label: 'Exp. var cut', f: r => fmt.pct(r.expected_reduction, 0) }, { k: 'realized_reduction', label: 'Realised cut', f: r => r.graded_on ? fmt.pct(r.realized_reduction, 0) : `<span class="faint">due ${fmt.date(r.eval_date)}</span>` },
      { k: 'hedge_pnl', label: 'Hedge P&L', cls: r => sign(r.hedge_pnl), f: r => r.graded_on ? fmt.signed(r.hedge_pnl, 0) : '—' }, { k: 'effectiveness', label: 'Effectiveness', f: r => r.graded_on ? fmt.num(r.effectiveness, 2) : '—' }], { empty: 'No hedge proposals recorded yet: execute one here or from a trade ticket.' });
    run();
  };

  // ---------------------------------------------------------------- the trade ticket with the Shaffer Hedge
  function openTicket(id, side = 'BUY') {
    const inst = /^(FUT|OPT|FWD):/.test(id);
    const st = { side, qty: null, amount: 10000, horizon: pref.get('tH', '1M'), objective: 'auto', reduction: 1, use: null, mode: 'trade_hedge', preview: null, lam: 1, book: false, design: null, designs: null };
    const m = modal(`<div class="row" style="justify-content:space-between"><h2 style="margin:0">Trade ${esc(id)}</h2><button class="ghost" id="tkX">✕</button></div>
      <div class="row" style="gap:12px;flex-wrap:wrap;align-items:flex-end;margin-top:12px">
        <div class="seg" id="tkSide">${(inst ? ['BUY', 'SELL'] : ['BUY', 'SELL', 'SHORT', 'COVER']).map(s => `<button data-s="${s}" class="${s === side ? 'on' : ''} ${s === 'BUY' || s === 'COVER' ? 'buy' : 'sell'}">${s[0] + s.slice(1).toLowerCase()}</button>`).join('')}</div>
        <label class="f">Quantity<input id="tkQ" type="number" min="0" step="any" placeholder="units" style="width:120px"></label>
        <label class="f">or amount ($)<input id="tkA" type="number" min="0" step="any" value="10000" style="width:130px"></label>
        <label class="f">Horizon<select id="tkH">${HEDGE_H.map(h => `<option ${h === st.horizon ? 'selected' : ''}>${h}</option>`).join('')}</select></label>
        <label class="f">Hedge objective<select id="tkO">${objOptions('auto')}</select></label>
        <div><div class="muted" style="font-size:12px;margin-bottom:4px">Hedge size</div><div class="seg" id="tkR">${[0.25, 0.5, 0.75, 1].map(x => `<button data-r="${x}" class="${x === 1 ? 'on' : ''}">${x * 100}%</button>`).join('')}<input id="tkRc" type="number" min="1" max="100" placeholder="%" style="width:64px;min-height:30px"></div></div>
      </div>
      <div id="tkBody" style="margin-top:14px"><div class="loading">Pricing the trade, measuring the risk it adds and finding its hedge…</div></div>
      <div class="card" style="margin-top:14px"><div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:10px">
        <div class="seg" id="tkMode"><button data-m="trade_only">Trade only</button><button data-m="trade_hedge" class="on">Trade + Shaffer Hedge</button></div>
        <div class="row" style="gap:8px"><span id="tkErr" class="neg" style="max-width:520px"></span><button class="primary" id="tkGo" disabled>Review</button></div></div></div>`);
    const box = $('.box', m); box.style.width = 'min(1240px,100%)'; box.style.maxHeight = '94vh'; box.style.overflow = 'auto';
    $('#tkX', m).onclick = closeModal;
    let seq = 0, timer = null;
    const refresh = () => { clearTimeout(timer); timer = setTimeout(load, 350); };
    async function load() {
      const my = ++seq;
      $('#tkGo', m).disabled = true; $('#tkErr', m).textContent = '';
      $('#tkBody', m).innerHTML = '<div class="loading">Pricing the trade, measuring the risk it adds and finding its hedge…</div>';
      const body = { asset_id: id, side: st.side, objective: st.objective, params: { horizon: st.horizon, reduction: st.reduction } };
      if (st.use) { body.params.use = [st.use]; body.params.candidates = [st.use]; }
      if (st.qty) body.quantity = st.qty; else body.amount = st.amount;
      let p;
      try { p = await post('/fs2/hedge/preview', body); } catch (e) { if (my === seq) { $('#tkBody', m).innerHTML = `<p class="neg">${esc(e.message)}</p>`; } return; }
      if (my !== seq) return;
      st.preview = p;
      const t = p.trade, sh = p.shaffer || {}, ns = ((p.net_scores || {}).horizons || {})[st.horizon] || {};
      const hs = sh.horizons || {};
      const hz = st.horizon in hs ? st.horizon : sh.primary_horizon;
      const cur = hs[hz] || {};
      $('#tkBody', m).innerHTML = `<div class="grid g-ticket">
        <div class="card"><div class="row" style="justify-content:space-between"><div><b style="font-size:16px">${esc(t.name || id)}</b><div class="muted">${esc(t.side)} ${fmt.qty(t.quantity)} at ${fmt.px(t.price)} · notional ${fmt.money(t.notional, 0)} · cash ${fmt.signed(-t.cash_cost, 0)}</div></div>
          <div class="muted" style="text-align:right;font-size:12px">free cash ${fmt.money(p.free_cash, 0)}<br>NAV ${fmt.money(p.nav, 0)}</div></div>
          ${t.pricing_label ? `<div style="margin-top:8px">${priceTag(t.pricing_label)}</div>` : ''}
          ${t.blocked ? `<div class="pill neg" style="white-space:normal;margin-top:8px">Cannot be traded: ${esc(t.blocked)}</div>` : t.eligible ? '' : `<div class="pill warn" style="white-space:normal;margin-top:8px">${esc(t.reasons.join('; '))}</div>`}
          <h3 style="margin:14px 0 6px">Shaffer Score <small class="faint" style="font-weight:400">what the evidence says — not a forecast</small></h3>
          ${sh.horizons ? `<div class="row" style="gap:6px;flex-wrap:wrap">${Object.entries(hs).filter(([k]) => ['1D', '1W', '1M', '3M', '6M', '12M', '3Y', '5Y'].includes(k)).map(([k, v]) => `<span class="pill ${k === hz ? 'on' : ''}" title="calibrated ${scoreTxt(v.calibrated)}">${k} <b class="${scoreCls(v.score)}">${scoreTxt(v.score)}</b></span>`).join('')}</div>
          <div style="margin-top:8px">${hz}: Shaffer <b class="${scoreCls(cur.score)}">${scoreTxt(cur.score)}</b> · calibrated ${scoreTxt(cur.calibrated)} · ML <b class="${scoreCls(cur.ml)}">${scoreTxt(cur.ml)}</b> · <span class="pill">${esc(cur.agreement || '—')}</span> · confidence ${pct0(cur.confidence)}${cur.expected != null ? ` · expected ${fmt.spct(cur.expected, 1)} <span class="faint" style="font-size:12px">(typical ${fmt.spct(cur.expected_typical, 1)}, evidence ${fmt.spct(cur.expected_edge, 1)})</span>` : ''}</div>
          <div class="faint" style="font-size:12px;margin-top:4px">Calibrated score = the evidence relative to this asset's own average; the expected return is total (its average + that evidence), so the two can differ in sign.
            ${cur.oos && cur.oos.t != null && cur.oos.t < 2 ? `<span class="pill warn" style="font-size:10.5px">WEAK EVIDENCE: out-of-sample IC ${fmt.num(cur.oos.ic, 3)} (t ${fmt.num(cur.oos.t, 1)}, ${fmt.num(cur.n_eff, 0)} independent obs.)</span>` : ''}</div>
          <div style="margin-top:8px;font-size:12.5px"><b>Supporting at ${esc(hz)}</b>: ${(cur.contributors || sh.contributors || []).map(c => esc(c.label || c.signal || c.family || '')).join(', ') || '—'}<br><b>Opposing at ${esc(hz)}</b>: ${(cur.contradicting || sh.contradicting || []).map(c => esc(c.label || c.signal || c.family || '')).join(', ') || '—'}</div>` : '<p class="muted">No Shaffer Score for this product yet (research the underlying first).</p>'}
          <div style="margin-top:8px">Net of costs at ${esc(st.horizon)}: long <b class="${scoreCls(ns.long)}">${scoreTxt(ns.long)}</b> · short <b class="${scoreCls(ns.short)}">${ns.short == null ? 'n/a' : scoreTxt(ns.short)}</b> <span class="faint" style="font-size:12px">(${esc(ns.expected_source || '')}${ns.short_note ? '; ' + esc(ns.short_note) : ''})</span></div>
          <p class="faint" style="font-size:12px;margin-top:6px">Evidence read from the long side: −100 strongly negative, 0 none, +100 strongly positive. It summarises the evidence; it is not a return forecast, and an expected return is shown only where the calibration supports one. The short score is not simply the negative: it pays borrow and earns no interest on the proceeds.</p>
        </div>
        <div class="card"><h3 style="margin:0 0 6px">What this trade adds</h3>${riskCompare(p.portfolio, { before_trade: 'Before', after_trade: 'After trade', after_hedge: 'After trade + hedge' })}</div></div>
        <div class="card" style="margin-top:14px"><h2 style="margin-top:0">Hedge designs: protection against profit given up <small>${esc(st.horizon)} · the trade ${st.book ? 'in your book' : 'alone'}</small></h2>
          <div class="row" style="gap:10px;flex-wrap:wrap;margin-bottom:8px"><span class="muted" style="font-size:12.5px">Weight on profit given up (λ)</span><div class="seg" id="tkLam">${[0.5, 1, 2, 5, 10].map(l => `<button data-l="${l}" class="${l === st.lam ? 'on' : ''}">${l}</button>`).join('')}</div>
          <label style="font-size:12.5px"><input type="checkbox" id="tkBook" ${st.book ? 'checked' : ''}> measure on the whole book</label></div>
          <div id="tkDesigns"><div class="loading">Simulating every design over ten years of real market moves…</div></div></div>
        <div class="card" style="margin-top:14px"><h2 style="margin-top:0">Shaffer Hedge for this trade <small>the engine's package for the selected objective</small></h2><div id="tkHedge"></div></div>`;
      loadDesigns(my, t);
      hedgePanel($('#tkHedge', m), p.hedge, { onPick: cid => { st.use = cid; load(); }, scenBefore: 'Trade alone', bookLabels: { before: 'Trade alone', raw: 'Trade + raw hedge', final: 'Trade + final hedge' } });
      $('#tkGo', m).disabled = !!t.blocked;
      $('#tkErr', m).textContent = t.blocked ? 'This trade cannot be booked (see above).' : '';
    }
    async function loadDesigns(my, t) {
      st.design = null; st.designs = null;
      const el = () => $('#tkDesigns', m);
      if (t.blocked || !t.signed) { el().innerHTML = '<p class="muted">No hedge designs: the trade cannot be booked.</p>'; return; }
      let d;
      try { d = await post('/fs2/hedge/designs', { asset_id: id, quantity: t.signed, horizon: st.horizon, objective: st.objective === 'auto' ? null : st.objective, lambda: st.lam, include_book: st.book }); }
      catch (e) { if (my === seq && el()) el().innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
      if (my !== seq || !el()) return;
      st.designs = d; renderDesigns();
      $$('#tkLam button', m).forEach(b => b.onclick = () => { st.lam = +b.dataset.l; $$('#tkLam button', m).forEach(x => x.classList.toggle('on', x === b)); renderDesigns(); });
      $('#tkBook', m).onchange = e => { st.book = e.target.checked; loadDesigns(seq, t); };
    }
    function renderDesigns() {
      if (!st.designs) return;
      designsTable($('#tkDesigns', m), st.designs, st.lam, st.design, k => { st.design = st.design === k ? null : k; renderDesigns(); });
      $('#tkDesigns', m).insertAdjacentHTML('beforeend', `<div class="faint" style="font-size:12px;margin-top:4px">${st.design ? '<b>The selected design replaces the engine package below when you book Trade + Shaffer Hedge.</b>' : 'Without a selection, Trade + Shaffer Hedge books the engine package below.'}</div>`);
    }
    $$('#tkSide button', m).forEach(b => b.onclick = () => { st.side = b.dataset.s; $$('#tkSide button', m).forEach(x => x.classList.toggle('on', x === b)); refresh(); });
    $('#tkQ', m).oninput = e => { st.qty = N(e.target.value); if (st.qty) $('#tkA', m).value = ''; refresh(); };
    $('#tkA', m).oninput = e => { st.amount = N(e.target.value); st.qty = null; $('#tkQ', m).value = ''; refresh(); };
    $('#tkH', m).onchange = e => { st.horizon = e.target.value; pref.set('tH', st.horizon); refresh(); };
    $('#tkO', m).onchange = e => { st.objective = e.target.value; st.use = null; refresh(); };
    $$('#tkR button', m).forEach(b => b.onclick = () => { st.reduction = +b.dataset.r; $$('#tkR button', m).forEach(x => x.classList.toggle('on', x === b)); refresh(); });
    $('#tkRc', m).onchange = e => { const v = N(e.target.value); if (v) { st.reduction = Math.max(0.01, Math.min(1, v / 100)); $$('#tkR button', m).forEach(x => x.classList.remove('on')); refresh(); } };
    $$('#tkMode button', m).forEach(b => b.onclick = () => { st.mode = b.dataset.m; $$('#tkMode button', m).forEach(x => x.classList.toggle('on', x === b)); });
    $('#tkGo', m).onclick = () => {
      const p = st.preview; if (!p) return;
      const dz = st.design && st.designs ? st.designs.designs.find(r => r.key === st.design) : null;
      const legs = st.mode === 'trade_hedge' ? (dz ? dz.legs.map(L => ({ id: L.id, quantity: Math.abs(L.quantity), side: L.quantity > 0 ? 'BUY' : 'SELL', units: '', risk_unit: dz.label, notional: null, cost: {} })) : (p.hedge.package.final || [])) : [];
      const conf = `<h3 style="margin:0 0 6px">Confirm</h3><div class="tbl-wrap"><table><thead><tr><th class="l">Leg</th><th>Side</th><th>Quantity</th><th class="l">Risk unit</th><th>Notional</th><th>Est. cost</th></tr></thead><tbody>
        <tr><td class="l"><b>${esc(id)}</b> <span class="pill">primary</span></td><td>${esc(p.trade.side)}</td><td>${fmt.qty(p.trade.quantity)}</td><td class="l">${esc(p.trade.risk_unit)}</td><td>${fmt.money(p.trade.notional, 0)}</td><td>${fmt.money((p.trade.costs || {}).total * Math.abs(p.trade.signed), 0)}</td></tr>
        ${legs.map(L => `<tr><td class="l"><b>${esc(L.id)}</b> <span class="pill">hedge</span></td><td>${L.side}</td><td>${fmt.qty(Math.abs(L.quantity))} ${esc(L.units)}</td><td class="l">${esc(L.risk_unit)}</td><td>${fmt.money(L.notional, 0)}</td><td>${fmt.money((L.cost || {}).total, 0)}</td></tr>`).join('')}</tbody></table></div>
        <p class="muted" style="font-size:12.5px">${legs.length ? `All ${legs.length + 1} legs are validated together (cash, 150% short collateral, futures margin, holdings) and recorded in one database transaction: either every leg is booked or none is.` : 'Trade only: the hedge proposal is logged as declined.'}</p>
        <div class="row" style="justify-content:flex-end;gap:8px"><button class="ghost" id="tkBack">Back</button><button class="primary" id="tkSubmit">Submit</button></div>`;
      const box2 = document.createElement('div'); box2.className = 'card'; box2.style.marginTop = '14px'; box2.innerHTML = conf; $('#tkBody', m).prepend(box2); box2.scrollIntoView({ behavior: 'smooth' });
      $('#tkBack', m).onclick = () => box2.remove();
      $('#tkSubmit', m).onclick = () => busy($('#tkSubmit', m), async () => {
        try {
          const r = await post('/fs2/hedge/execute', { primary: { asset_id: id, side: p.trade.side, quantity: p.trade.quantity }, legs: legs.map(L => ({ id: L.id, quantity: L.side === 'BUY' ? Math.abs(L.quantity) : -Math.abs(L.quantity) })), proposal: p, mode: st.mode });
          closeModal(); toast(`Recorded ${r.legs.length} leg(s)${r.package_id ? ' as ' + r.package_id : ''}`); go('#/portfolio');
        } catch (e) { $('#tkErr', m).textContent = e.message; box2.querySelector('p').innerHTML = `<span class="neg">Not recorded: ${esc(e.message)}. No leg was booked.</span>`; }
      });
    };
    load();
  }
  window.fs2Ticket = openTicket;

  function productDrawer(r) {
    const d = drawer(`<h2 style="margin:0 0 4px">${esc(r.id)}</h2><div class="muted">${esc(r.name)} · ${esc(clsName(r.asset_class))} · ${fmt.px(r.price)} (${fmt.date(r.date)})</div><div id="pdBody" style="margin-top:12px"><div class="loading">…</div></div>
      <div class="row" style="gap:8px;margin-top:14px"><button class="buy" id="pdBuy">Buy / hedge</button><button class="sell" id="pdShort">Short / hedge</button><button class="ghost" id="pdRes">Full research</button></div>`);
    $('#pdBuy', d).onclick = () => { closeDrawer(); openTicket(r.id, 'BUY'); };
    $('#pdShort', d).onclick = () => { closeDrawer(); openTicket(r.id, 'SHORT'); };
    $('#pdRes', d).onclick = () => { closeDrawer(); go('#/asset/' + encodeURIComponent(r.id)); };
    api('/fs2/asset/' + encodeURIComponent(r.id) + '/net-scores').then(ns => {
      const hs = r.scores || {};
      $('#pdBody', d).innerHTML = `<div class="tbl-wrap"><table><thead><tr><th class="l">Horizon</th><th>Shaffer</th><th>Calibrated</th><th>ML</th><th class="l">Agreement</th><th>Confidence</th><th>Net long</th><th>Net short</th></tr></thead><tbody>
        ${['1D', '1W', '1M', '3M', '6M', '12M', '3Y', '5Y'].map(k => { const n = (ns.horizons || {})[k] || {}; return `<tr><td class="l">${k}</td><td><b class="${scoreCls(hs[k])}">${scoreTxt(hs[k])}</b></td><td>${scoreTxt((r.calibrated || {})[k])}</td><td>${scoreTxt((r.ml || {})[k])}</td><td class="l" style="font-size:12px">${esc((r.agreement || {})[k] || '—')}</td><td>${pct0((r.confidence || {})[k])}</td><td class="${scoreCls(n.long)}">${scoreTxt(n.long)}</td><td class="${scoreCls(n.short)}">${n.short == null ? 'n/a' : scoreTxt(n.short)}</td></tr>`; }).join('')}</tbody></table></div>
        <p class="faint" style="font-size:12px">Net scores subtract what each side costs to hold (spread, borrow, no interest on short proceeds, fund decay). ${esc(((ns.horizons || {})['3M'] || {}).expected_source || '')}.</p>`;
    }).catch(e => { $('#pdBody', d).innerHTML = `<p class="muted">${esc(e.message)}</p>`; });
  }

  // ---------------------------------------------------------------- settings
  pages.settings = async (main, _, alive) => {
    const st = await api('/fs2/status'); if (!alive()) return;
    main.innerHTML = `<div class="page-head"><div><h1>Settings</h1><p>Data, methods and display.</p></div></div>
      <div class="grid g2"><div class="card"><h2>Data</h2><div class="kv"><span>Latest close</span><span>${esc(st.last_price_date || '—')}</span><span>Assets</span><span>${st.assets}</span><span>S&P 500 history</span><span>${st.spy_rows} sessions</span><span>Data version</span><span>${esc(st.data_version)}</span><span>Last automatic check</span><span>${esc((st.scheduler || {}).last_check || '—')}</span></div>
        <div class="row" style="margin-top:12px"><button id="rf1">Refresh now</button><button id="rf2">Re-download everything</button></div><div id="rfJ"></div>
        <p class="hint">Prices from Yahoo Finance (daily, adjusted for splits and dividends), macro series from FRED (each used only from its publication date), fundamentals from SEC filings (each used only from its filing date). The data refreshes by itself after each US close while FinSim2 runs.</p></div>
        <div class="card"><h2>Methods</h2><ul class="muted" style="margin:0;padding-left:18px;font-size:13px;line-height:1.6"><li>Signals are standardised with expanding statistics (no future normalisation) and capped at ±3.</li><li>Evidence per signal and horizon: rank IC, hit rate, t-stat and p-value on the effective sample (overlap and signal persistence removed), Benjamini–Hochberg q-values across the ~60 signals.</li><li>The Shaffer Score aggregates 15 signal families: each signal's weight, confidence, regime and decay terms are estimated only from outcomes known at each date, and the same function scores every date (history and today). Raw scores are calibrated out of sample.</li><li>ML: purged walk-forward, models ranked by stable out-of-sample IC, ensemble weights from earlier blocks only.</li><li>Every forecast is stored and scored when its horizon passes.</li></ul></div></div>
      <div class="grid g2" style="margin-top:16px"><div class="card"><h2>Portfolio benchmark</h2><div class="row">${assetPicker('bmPick', '')}<span class="muted" id="bmNow"></span></div><p class="hint">Performance is compared with this asset's total return (default SPY).</p></div>
        <div class="card flush"><h2>Audit log</h2><div id="auT"></div></div></div>
      <div class="grid g2" style="margin-top:16px"><div class="card"><h2>Display</h2><div class="seg" id="thSeg">${['system', 'dark', 'light'].map(t => `<button data-th="${t}" class="${pref.get('theme', 'system') === t ? 'on' : ''}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}</div></div>
        <div class="card flush"><h2>Fetch log</h2><div id="flT"></div></div></div>
      <div class="card" style="margin-top:16px"><h2>Cash and short proceeds</h2>
        <div class="row" style="gap:14px;flex-wrap:wrap;align-items:flex-end">
          <label class="f">Idle cash earns<select id="csMode"><option value="none">nothing</option><option value="bill">the 3-month bill</option><option value="broker">the bill − broker spread</option><option value="custom">a custom rate</option></select></label>
          <label class="f">Broker spread<input id="csSpread" type="number" step="0.001" style="width:90px"></label>
          <label class="f">Custom rate<input id="csCustom" type="number" step="0.001" style="width:90px"></label>
          <label class="f">Short proceeds earn<select id="csShort"><option value="none">nothing (retail)</option><option value="partial">part of the bill</option><option value="institutional">the bill (institutional rebate)</option></select></label>
          <label class="f">Share (partial)<input id="csShare" type="number" step="0.05" min="0" max="1" style="width:80px"></label>
          <button class="primary" id="csSave">Save</button></div>
        <p class="hint" id="csNow"></p>
        <p class="hint">Interest accrues every session on the previous close's balances at the 3-month bill as known that day, is booked as cash, and so flows into NAV, P&L, the time- and money-weighted returns and the long/short net scores. The whole ledger is replayed with the new setting.</p></div>`;
    $$('#thSeg button').forEach(b => b.onclick = () => { pref.set('theme', b.dataset.th); applyTheme(); $$('#thSeg button').forEach(x => x.classList.toggle('on', x === b)); });
    const rf = full => busy(null, async () => { const j = await post('/fs2/refresh', { full }); pollJob(j.id, () => route(), $('#rfJ')); });
    const csFill = c => { $('#csMode').value = c.cash_mode; $('#csSpread').value = c.broker_spread; $('#csCustom').value = c.custom_rate; $('#csShort').value = c.short_mode; $('#csShare').value = c.short_share; $('#csNow').textContent = 'Now: ' + c.description + '.'; };
    api('/fs2/portfolio/cash-settings').then(csFill).catch(() => {});
    $('#csSave').onclick = async () => { try { csFill(await api('/fs2/portfolio/cash-settings', { body: { cash_mode: $('#csMode').value, broker_spread: N($('#csSpread').value) || 0, custom_rate: N($('#csCustom').value) || 0, short_mode: $('#csShort').value, short_share: N($('#csShare').value) ?? 0.5 } })); toast('Cash settings saved; the ledger is replayed with them'); } catch (e) { toast(e.message, true); } };
    $('#rf1').onclick = () => rf(false); $('#rf2').onclick = () => { if (confirm('Re-download the full history of every asset? A few minutes.')) rf(true); };
    api('/fs2/settings').then(x => { $('#bmNow').textContent = 'now: ' + x.benchmark; }).catch(() => {});
    bindPicker('bmPick', id => busy(null, async () => { await post('/fs2/settings', { benchmark: id }); toast('Benchmark: ' + id); route(); }));
    api('/fs2/audit?limit=100').then(rows => table($('#auT'), rows, [{ k: 'at', label: 'When', l: 1 }, { k: 'action', label: 'Action', l: 1 }, { k: 'entity', label: 'Item', l: 1 }], { sortKey: null, maxH: 260, empty: 'Nothing yet' })).catch(() => {});
    table($('#flT'), st.fetch_log || [], [{ k: 'fetched_at', label: 'When', l: 1 }, { k: 'source', label: 'Source', l: 1 }, { k: 'key', label: 'Item', l: 1 }, { k: 'status', label: 'Status', l: 1 }], { sortKey: null, maxH: 300 });
  };

  // ---------------------------------------------------------------- search, theme, startup
  function applyTheme() { const t = pref.get('theme', 'system'); if (t === 'system') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', t); }
  function bindSearch() {
    bindPicker('q', id => { $('#q').value = ''; go('#/asset/' + encodeURIComponent(id)); });
    document.addEventListener('keydown', e => { if (e.key === '/' && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); $('#q').focus(); } if (e.key === 'Escape') closeDrawer(); });
  }
  async function boot() {
    applyTheme();
    $('#themeBtn').onclick = () => { const cur = document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'); pref.set('theme', cur === 'light' ? 'dark' : 'light'); applyTheme(); route(); };
    $('#menuBtn').onclick = () => $('#side').classList.toggle('open');
    bindSearch();
    await loadStatus();
    if (S.status && S.status.data_ready) assets().catch(() => { });
    route();
    setInterval(async () => { if (document.hidden) return; const before = S.status && S.status.data_version; const hadJobs = S.status && (S.status.jobs || []).length; await loadStatus(); if (S.status && (S.status.data_version !== before) && !hadJobs) { S.assets = null; } }, 20000);
  }
  boot();
})();
