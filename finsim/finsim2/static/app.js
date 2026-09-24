/* FinSim2 — the portfolio-manager edition. One book, one amount, a clean interface on FinSim's engine. */
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

  // ---------------------------------------------------------------- state
  const S = { worlds: [], wid: pref.get('wid', null), world: null, pid: null, dash: null, secs: null, secsFor: null };
  const W = () => `/worlds/${S.wid}`;
  const P = () => `/worlds/${S.wid}/portfolios/${S.pid}`;
  const isLive = () => S.world && S.world.clock && S.world.clock.mode === 'REAL_TIME';
  async function loadWorlds() { S.worlds = await api('/worlds'); if (!S.worlds.find(w => w.id === S.wid)) S.wid = S.worlds.length ? S.worlds[S.worlds.length - 1].id : null; pref.set('wid', S.wid); }
  async function loadWorld() {
    if (!S.wid) { S.world = null; S.pid = null; S.dash = null; return; }
    const world = await api(W());
    const pid = (world.portfolios.find(p => p.job === 'PORTFOLIO_MANAGER') || world.portfolios[0] || {}).id;
    const dash = await api(`/worlds/${S.wid}/portfolios/${pid}/dashboard`);
    S.world = world; S.pid = pid; S.dash = dash;
  }
  async function securities(force = false) {
    const key = S.wid + '|' + (S.world && S.world.current_date);
    if (!S.secs || S.secsFor !== key || force) { S.secs = await api(W() + '/securities'); S.secsFor = key; }
    return S.secs;
  }

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

  // ---------------------------------------------------------------- navigation
  const ICON = {
    overview: '<path d="M3 13h8V3H3zm10 8h8V11h-8zM3 21h8v-6H3zm10-18v6h8V3z"/>',
    trade: '<path d="M7 17l10-10M17 7h-7M17 7v7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    positions: '<path d="M4 6h16M4 12h16M4 18h10" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    markets: '<path d="M3 17l5-6 4 3 6-8 3 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>',
    fx: '<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18" fill="none" stroke="currentColor" stroke-width="1.6"/>',
    risk: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>',
    performance: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    activity: '<path d="M3 12h4l3 8 4-16 3 8h4" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>',
    analytics: '<path d="M5 4v16h15" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 15c2-5 4-7 6-4s3 1 5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    shaffer: '<path d="M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    settings: '<circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  };
  const NAV = [['', 'Fund'], ['overview', 'Overview'], ['trade', 'Trade'], ['positions', 'Positions'], ['fx', 'Cash & FX'], ['', 'Markets & research'], ['markets', 'Markets'], ['analytics', 'Analytics'], ['shaffer', 'Shaffer Score'], ['', 'Oversight'], ['risk', 'Risk'], ['performance', 'Performance'], ['activity', 'Activity'], ['settings', 'Settings']];
  const TITLES = { overview: 'Overview', trade: 'Trade', positions: 'Positions', fx: 'Cash & FX', markets: 'Markets', analytics: 'Analytics', shaffer: 'Shaffer Score', risk: 'Risk', performance: 'Performance', activity: 'Activity', settings: 'Settings', asset: 'Asset', welcome: 'Start a fund' };
  function renderNav(active) {
    $('#nav').innerHTML = NAV.map(([k, label]) => !k ? `<div class="sec">${label}</div>` : `<a href="#/${k}" class="${active === k ? 'on' : ''}"><svg viewBox="0 0 24 24" fill="currentColor">${ICON[k]}</svg>${label}${k === 'shaffer' ? '<span class="pill warn">soon</span>' : ''}</a>`).join('');
    $$('#nav a').forEach(a => a.onclick = () => $('#side').classList.remove('open'));
  }

  function renderShell() {
    const fb = $('#fundBox');
    if (!S.world || !S.dash) { fb.innerHTML = '<span class="muted">No fund yet</span>'; $('#topKpis').innerHTML = ''; $('#clockBox').innerHTML = ''; return; }
    const d = S.dash;
    fb.innerHTML = `<div class="row" style="justify-content:space-between"><b style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(S.world.name)}</b><span class="pill ${isLive() ? 'pos' : 'acc'}"><span class="dot"></span>${isLive() ? 'Live' : 'Practice'}</span></div>
      <div class="muted" style="margin-top:4px">${esc(d.level_title || 'Portfolio manager')} · ${fmt.date(S.world.current_date)}</div>
      ${S.worlds.length > 1 ? `<select id="fundSel">${S.worlds.map(w => `<option value="${w.id}" ${w.id === S.wid ? 'selected' : ''}>${esc(w.name)}</option>`).join('')}</select>` : ''}`;
    const fs = $('#fundSel'); if (fs) fs.onchange = async () => { S.wid = fs.value; pref.set('wid', S.wid); S.secs = null; await loadWorld(); renderShell(); route(); };
    const cashTot = (d.cash_accounts || []).reduce((a, c) => a + Number(c.base_value || 0), 0);
    $('#topKpis').innerHTML = `<div class="k"><span>NAV</span><b>${fmt.money(d.nav)}</b></div><div class="k"><span>Today</span><b class="${sign(d.day_pnl)}">${fmt.signed(d.day_pnl)}</b></div>
      <div class="k hide-sm"><span>Since start</span><b class="${sign(d.return_since_inception)}">${fmt.spct(d.return_since_inception)}</b></div><div class="k hide-md"><span>Cash</span><b>${fmt.big(cashTot)}</b></div>`;
    const tw = S.world.trading_window || {};
    $('#clockBox').innerHTML = isLive()
      ? `<span class="pill ${tw.open ? 'pos' : 'warn'}" title="${esc(tw.reason || '')}"><span class="dot"></span>${tw.session_running ? 'Market open' : 'After hours'} · next update ${S.world.clock.next_update ? new Date(S.world.clock.next_update).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '—'}</span>`
      : `<button class="primary small" id="advBtn" title="process the next trading day">Next day ▸</button>`;
    const ab = $('#advBtn'); if (ab) ab.onclick = () => busy(ab, async () => { const r = await post(W() + '/advance', { days: 1 }); await loadWorld(); renderShell(); route(); toast(`Moved to ${fmt.date(S.world.current_date)}`); });
  }

  // ---------------------------------------------------------------- router
  const pages = {};
  let routeSeq = 0;
  async function route() {
    const seq = ++routeSeq;
    const parts = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
    let page = parts[0] || 'overview';
    if (!S.wid) page = 'welcome';
    if (!pages[page]) page = 'overview';
    renderNav(page === 'asset' ? 'markets' : page);
    $('#pageTitle').textContent = TITLES[page] || 'FinSim2';
    const main = $('#main');
    main.innerHTML = '<div class="skeleton"></div>';
    try { await pages[page](main, parts.slice(1), () => seq === routeSeq); }
    catch (e) { if (seq === routeSeq) main.innerHTML = `<div class="card"><h2>Something went wrong</h2><p class="muted">${esc(e.message)}</p></div>`; }
  }
  window.addEventListener('hashchange', route);

  // ---------------------------------------------------------------- start a fund
  pages.welcome = async (main) => {
    let amount = 100e6, mode = 'LIVE';
    const presets = [[1e6, '$1M', 'a small book'], [10e6, '$10M', 'a sleeve'], [100e6, '$100M', 'a typical fund'], [1e9, '$1B', 'a flagship fund']];
    main.innerHTML = `<div class="welcome">
      <h1>Run a fund.</h1><p class="muted" style="font-size:15px;margin:0 0 26px">You are the portfolio manager. Pick how much you run; everything else is the market: stocks worldwide, bonds, futures, options, currencies, crypto and OTC, with real accounting and risk.</p>
      <div class="card stack">
        <label class="f">Fund name<input id="wName" value="My Fund" maxlength="60"></label>
        <div><div class="muted" style="font-size:12px;font-weight:500;margin-bottom:6px">Amount</div>
          <div class="amounts">${presets.map(([v, l, s]) => `<button data-v="${v}" class="${v === amount ? 'on' : ''}"><b>${l}</b><small>${s}</small></button>`).join('')}</div>
          <label class="f" style="margin-top:10px">Or any amount (USD, up to $1 trillion)<input id="wAmt" type="number" min="1" step="1000000" placeholder="e.g. 250000000"></label></div>
        <div><div class="muted" style="font-size:12px;font-weight:500;margin-bottom:6px">Market</div>
          <div class="modes"><button data-m="LIVE" class="on"><b>Live market</b><span class="muted">Real prices. The fund moves one real trading day at a time, updated after each close, with delayed live quotes in the session.</span></button>
          <button data-m="PRACTICE"><b>Practice</b><span class="muted">A simulated market you advance yourself, a day at a time. Works offline.</span></button></div></div>
        <div class="row"><span class="muted" id="wSum"></span><span class="spacer"></span><button class="primary" id="wGo">Start fund</button></div>
        <div id="wErr" class="neg"></div>
      </div>
      ${S.worlds.length ? `<div class="card" style="margin-top:16px"><h2>Your funds</h2>${S.worlds.map(w => `<div class="row" style="padding:6px 0;border-top:1px solid var(--border)"><b>${esc(w.name)}</b><span class="muted">${esc(w.id)}</span><span class="spacer"></span><button class="small" data-open="${w.id}">Open</button></div>`).join('')}</div>` : ''}
    </div>`;
    const sum = () => { $('#wSum').textContent = `${fmt.money(amount)} · ${mode === 'LIVE' ? 'live market' : 'practice market'}`; };
    $$('.amounts button').forEach(b => b.onclick = () => { amount = +b.dataset.v; $('#wAmt').value = ''; $$('.amounts button').forEach(x => x.classList.toggle('on', x === b)); sum(); });
    $('#wAmt').oninput = e => { const v = +e.target.value; if (v > 0) { amount = v; $$('.amounts button').forEach(x => x.classList.remove('on')); } sum(); };
    $$('.modes button').forEach(b => b.onclick = () => { mode = b.dataset.m; $$('.modes button').forEach(x => x.classList.toggle('on', x === b)); sum(); });
    $$('[data-open]').forEach(b => b.onclick = async () => { S.wid = b.dataset.open; pref.set('wid', S.wid); await loadWorld(); renderShell(); location.hash = '#/overview'; route(); });
    sum();
    $('#wGo').onclick = () => busy($('#wGo'), async () => {
      $('#wGo').textContent = mode === 'LIVE' ? 'Loading the real market…' : 'Building the market…'; $('#wErr').textContent = '';
      try { const r = await post('/fs2/worlds', { name: $('#wName').value, amount, mode }); S.wid = r.world_id; pref.set('wid', S.wid); await loadWorlds(); await loadWorld(); renderShell(); toast(`${r.name} is open with ${fmt.money(r.amount)}`); location.hash = '#/overview'; route(); }
      catch (e) { $('#wErr').textContent = e.message + (mode === 'LIVE' ? ' · try Practice if you are offline' : ''); $('#wGo').textContent = 'Start fund'; }
    });
  };

  // ---------------------------------------------------------------- overview
  const CLASS = { EQUITY: 'Stocks', ETF: 'ETFs', REIT: 'REITs', ADR: 'ADRs', GOVT_BOND: 'Government bonds', CORP_BOND: 'Corporate bonds', MBS_TBA: 'Agency MBS', STRUCTURED: 'Structured credit', CRYPTO: 'Crypto', OPTION: 'Options', FX: 'Currencies', CASH: 'Cash' };
  const clsName = k => CLASS[k] || String(k || 'Other').replace(/_/g, ' ').toLowerCase().replace(/^./, c => c.toUpperCase());
  const kpi = (k, v, s = '') => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
  pages.overview = async (main, _, alive) => {
    await loadWorld(); renderShell(); if (!alive()) return;
    const d = S.dash; const [career, news] = await Promise.all([api(P() + '/career').catch(() => null), api(W() + '/news').catch(() => [])]); if (!alive()) return;
    const cashTot = (d.cash_accounts || []).reduce((a, c) => a + Number(c.base_value || 0), 0);
    const m = (career && career.metrics) || {};
    main.innerHTML = `<div class="page-head"><div><h1>${esc(S.world.name)}</h1><p>${esc(d.level_title || '')} · benchmark ${esc(d.portfolio.benchmark || 'SPY')} · ${S.world.market_source === 'REAL' ? 'real market' : 'simulated market'} · ${fmt.date(S.world.current_date)}</p></div><div class="row"><a class="btn" href="#/trade">Trade</a><a class="btn" href="#/analytics">Analytics</a></div></div>
      <div class="tiles">
        ${kpi('Net asset value', fmt.money(d.nav), `started with ${fmt.money(career ? career.contributed_capital : null)}`)}
        ${kpi('Today', `<span class="${sign(d.day_pnl)}">${fmt.signed(d.day_pnl)}</span>`, `month ${fmt.signed(d.mtd_pnl)} · year ${fmt.signed(d.ytd_pnl)}`)}
        ${kpi('Return since start', `<span class="${sign(d.return_since_inception)}">${fmt.spct(d.return_since_inception)}</span>`, d.benchmark ? `${esc(d.benchmark.id)} ${fmt.spct(d.benchmark.return)} · alpha <span class="${sign(d.return_since_inception - d.benchmark.return)}">${fmt.spct(d.return_since_inception - d.benchmark.return)}</span>` : '')}
        ${kpi('Cash, all currencies', fmt.money(cashTot), (d.cash_accounts || []).filter(c => Number(c.settled)).map(c => c.currency).join(' · ') || 'USD')}
        ${kpi('Gross / net exposure', `${fmt.big(d.gross_exposure)} <span class="muted" style="font-size:15px">/ ${fmt.big(d.net_exposure)}</span>`, `leverage ${fmt.num(d.leverage, 2)}x`)}
        ${kpi('Sharpe · max drawdown', `${m.sharpe != null && (m.days || 0) >= 20 ? fmt.num(m.sharpe, 2) : '—'} <span class="muted" style="font-size:15px">· ${fmt.pct(m.max_drawdown, 1)}</span>`, `volatility ${fmt.pct(m.volatility, 1)} · ${m.days || 0} days`)}
      </div>
      <div class="grid g-main">
        <div class="stack">
          <div class="card"><h2>Fund vs ${esc(d.portfolio.benchmark || 'SPY')} <small>growth of the NAV and the benchmark since the start</small></h2><div id="navChart"></div></div>
          <div class="card flush"><h2>Largest positions <a class="right" href="#/positions" style="font-size:12.5px;font-weight:500">All positions →</a></h2><div id="topPos"></div></div>
        </div>
        <div class="stack">
          <div class="card"><h2>Allocation <small>by asset class</small></h2><div id="alloc"></div></div>
          <div class="card flush"><h2>Cash by currency <a class="right" href="#/fx" style="font-size:12.5px;font-weight:500">Cash & FX →</a></h2><div id="ccy"></div></div>
          ${career ? `<div class="card"><h2>Mandate limits</h2>${Object.entries(career.limits_status || {}).map(([k, v]) => { const u = v.limit ? Math.abs(v.value) / v.limit : 0; return `<div style="margin-bottom:10px"><div class="row" style="justify-content:space-between;font-size:12.5px"><span>${esc(k.replace(/_/g, ' '))}</span><span class="num">${k === 'gross_leverage' ? fmt.num(v.value, 2) + 'x / ' + fmt.num(v.limit, 2) + 'x' : fmt.pct(v.value, 1) + ' / ' + fmt.pct(v.limit, 0)}</span></div><div class="bar ${u > 0.9 ? 'bad' : u > 0.7 ? 'warn' : 'good'}"><i style="width:${Math.min(100, u * 100)}%"></i></div></div>`; }).join('')}</div>` : ''}
          <div class="card"><h2>Headlines</h2>${(news || []).slice(0, 6).map(n => `<div style="padding:7px 0;border-top:1px solid var(--border)"><div style="font-weight:550">${n.link ? `<a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.headline)}</a>` : esc(n.headline)}</div><div class="muted" style="font-size:12px">${esc(n.publisher || n.category || '')} · ${fmt.date(n.date)}</div></div>`).join('') || '<div class="empty">No news yet</div>'}</div>
        </div>
      </div>`;
    const hist = d.nav_history || [];
    let bench = null;
    if (d.portfolio.benchmark && hist.length > 1) { try { const b = await api(W() + `/securities/${encodeURIComponent(d.portfolio.benchmark)}?period=MAX`); const map = {}; (b.bars || []).forEach(x => { map[x[0]] = x[4]; }); let last = null; const ser = hist.map(h => { if (map[h.date] != null) last = map[h.date]; return last; }); const first = ser.find(v => v != null); if (first) bench = ser.map(v => v == null ? null : v / first * 100); } catch (e) { } }
    if (!alive()) return;
    const nav0 = hist.length ? hist[0].nav : 1;
    lineChart($('#navChart'), [{ name: 'Fund', data: hist.map(h => h.nav / nav0 * 100), area: true }, ...(bench ? [{ name: d.portfolio.benchmark, data: bench, color: css('--faint'), width: 1.5 }] : [])], { labels: hist.map(h => fmt.date(h.date).replace(/, \d{4}$/, '')), fmtY: v => v.toFixed(1), h: 240 });
    positionsTable($('#topPos'), d.positions.slice().sort((a, b) => Math.abs(b.market_value) - Math.abs(a.market_value)).slice(0, 8), d.nav, { compact: true });
    donut($('#alloc'), Object.entries(d.exposure_by_asset_class || {}).filter(([, v]) => Number(v)).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).map(([k, v]) => ({ label: clsName(k), value: Number(v) })));
    ccyTable($('#ccy'), d.cash_accounts || []);
  };
  function ccyTable(el, rows) {
    const tot = rows.reduce((a, r) => a + Number(r.base_value || 0), 0);
    table(el, rows, [
      { k: 'currency', label: 'Currency', l: 1, f: r => `<b>${esc(r.currency)}</b>${r.is_base ? ' <span class="pill acc">base</span>' : ''}` },
      { k: 'settled', label: 'Balance', f: r => `<span class="${Number(r.settled) < 0 ? 'neg' : ''}">${fmt.num(r.settled, 0)}</span>` },
      { k: 'base_value', label: 'In USD', f: r => fmt.money(r.base_value) },
      { k: 'share', label: 'Share', v: r => Number(r.base_value) / (tot || 1), f: r => tot ? fmt.pct(Number(r.base_value) / tot, 1) : '—' },
    ], { sortKey: 'base_value', empty: 'No cash', total: { currency: '<b>Total</b>', base_value: fmt.money(tot) } });
  }
  function positionsTable(el, rows, nav, o = {}) {
    const cols = [
      { k: 'security_id', label: 'Position', l: 1, f: r => `<b>${esc(r.security_id)}</b><span class="sub">${esc(r.name || '')}</span>` },
      ...(o.compact ? [] : [{ k: 'asset_class', label: 'Class', l: 1, f: r => `<span class="pill">${esc(clsName(r.asset_class))}</span>` }, { k: 'currency', label: 'Ccy', l: 1 }]),
      { k: 'quantity', label: 'Quantity', f: r => fmt.qty(r.quantity) },
      ...(o.compact ? [] : [{ k: 'average_cost', label: 'Avg cost', f: r => fmt.px(r.average_cost) }, { k: 'mark', label: 'Price', f: r => fmt.px(r.mark) }]),
      { k: 'market_value', label: 'Value (USD)', f: r => fmt.money(r.market_value) },
      { k: 'unrealized_pnl', label: 'Unrealized', cls: r => sign(r.unrealized_pnl), f: r => fmt.signed(r.unrealized_pnl) },
      { k: 'day_pnl', label: 'Today', cls: r => sign(r.day_pnl), f: r => fmt.signed(r.day_pnl) },
      { k: 'weight', label: 'Weight', v: r => Math.abs(Number(r.market_value)) / (nav || 1), f: r => `${fmt.pct(Number(r.market_value) / (nav || 1), 1)}<span class="cell-bar bar"><i style="width:${Math.min(100, Math.abs(Number(r.market_value)) / (nav || 1) * 400)}%"></i></span>` },
    ];
    table(el, rows, cols, { sortKey: o.sortKey || 'weight', onRow: r => { location.hash = '#/asset/' + encodeURIComponent(r.security_id); }, empty: 'No positions yet · open Trade to buy something', maxH: o.maxH });
  }

  // ---------------------------------------------------------------- trade: one ticket for everything listed, plus option chains
  const unitOf = s => !s ? 'units' : s.is_bond ? 'face' : s.is_future ? 'contracts' : s.asset_class === 'CRYPTO' ? 'coins' : s.asset_class === 'OPTION' ? 'contracts' : 'shares';
  const unitPrice = s => { const p = N(s.last) || 0; if (s.is_bond) return p / 100; return p * (N(s.multiplier) || 1); };
  pages.trade = async (main, args, alive) => {
    const secs = await securities(); if (!alive()) return;
    const tab = args[0] === 'options' ? 'options' : 'ticket';
    main.innerHTML = `<div class="page-head"><div><h1>Trade</h1><p>Search anything listed: stocks worldwide, ETFs, bonds, futures, crypto. Option chains are on the second tab. Currencies convert on <a href="#/fx">Cash & FX</a>.</p></div>
      <div class="seg"><button data-t="ticket" class="${tab === 'ticket' ? 'on' : ''}">Order ticket</button><button data-t="options" class="${tab === 'options' ? 'on' : ''}">Options</button></div></div><div id="tradeBody"></div>`;
    $$('.page-head .seg button').forEach(b => b.onclick = () => { location.hash = b.dataset.t === 'options' ? '#/trade/options' : '#/trade'; });
    if (tab === 'options') return optionsPage($('#tradeBody'), args.slice(1), alive);
    const sid = args[0] || pref.get('lastTicket', 'SPY');
    const listed = secs.find(s => s.id === sid);
    if (!listed && args[0]) {                     // an option contract (chains are not in the listing): load it on its own
      const c = await api(W() + `/securities/${encodeURIComponent(sid)}?period=1M`).catch(() => null); if (!alive()) return;
      if (c && c.security) { const sec = { ...c.security, last: c.last, bid: c.bid, ask: c.ask, change_pct: null, is_bond: false, is_future: !!c.security.is_future }; return ticketPage($('#tradeBody'), sec, alive, sec); }
    }
    ticketPage($('#tradeBody'), listed || secs.find(s => s.id === 'SPY') || secs[0], alive);
  };
  async function ticketPage(el, sec, alive, optionSec) {
    el.innerHTML = `<div class="grid g-ticket"><div class="stack"><div class="card" id="qCard"></div><div class="card flush"><h2>Working orders</h2><div id="working"></div></div></div><div class="card" id="ticket"></div></div>`;
    let s = optionSec || sec; pref.set('lastTicket', sec.id);
    const detail = optionSec ? null : await api(W() + `/securities/${encodeURIComponent(sec.id)}?period=6M`).catch(() => null); if (!alive()) return;
    const held = (S.dash.positions || []).find(p => p.security_id === s.id);
    $('#qCard').innerHTML = `<div class="row" style="justify-content:space-between"><div><div class="muted" style="font-size:12.5px">${esc(clsName(s.asset_class))} · ${esc(s.currency)}${s.sector ? ' · ' + esc(s.sector) : ''}${s.country ? ' · ' + esc(s.country) : ''}</div><h2 style="font-size:19px;margin:2px 0 0">${esc(s.id)} <small>${esc(s.name)}</small></h2></div><a class="btn small" href="#/asset/${encodeURIComponent(s.id)}">Research →</a></div>
      <div class="quote-head" style="margin-top:8px"><span class="px">${fmt.px(s.last)}</span>${s.change_pct != null ? `<span class="pill ${sign(s.change_pct)}">${fmt.spct(s.change_pct)}</span>` : ''}<span class="muted">bid ${fmt.px(s.bid)} · ask ${fmt.px(s.ask)}</span></div>
      <div id="qChart" style="margin-top:8px"></div>
      <div class="kv" style="margin-top:10px">${s.is_bond ? `<span>Coupon</span><span>${s.coupon ? fmt.pct(s.coupon, 3) : 'none (bill)'}</span><span>Maturity</span><span>${fmt.date(s.maturity)}</span><span>Rating</span><span>${esc(s.rating || '—')}</span>` : ''}${s.is_future ? `<span>Contract size</span><span>${fmt.qty(s.multiplier)} ${esc(s.unit || '')}</span><span>Expiry</span><span>${fmt.date(s.expiry)}</span>` : ''}${s.market_cap ? `<span>Market cap</span><span>${fmt.big(s.market_cap, s.currency)}</span>` : ''}${s.beta != null && !s.is_bond ? `<span>Beta</span><span>${fmt.num(s.beta, 2)}</span>` : ''}${s.dividend_yield ? `<span>Dividend yield</span><span>${fmt.pct(s.dividend_yield, 2)}</span>` : ''}<span>Average daily volume</span><span>${fmt.big(s.adv, '')}</span>${held ? `<span>You hold</span><span><b>${fmt.qty(held.quantity)}</b> · ${fmt.money(held.market_value)} · <span class="${sign(held.unrealized_pnl)}">${fmt.signed(held.unrealized_pnl)}</span></span>` : ''}</div>`;
    if (detail && detail.bars && detail.bars.length) lineChart($('#qChart'), [{ name: s.id, data: detail.bars.map(b => b[4]), area: true }], { labels: detail.bars.map(b => fmt.date(b[0]).replace(/, \d{4}$/, '')), fmtY: v => fmt.px(v), h: 170 });
    else $('#qChart').innerHTML = '';
    const heldCcys = (S.dash.cash_accounts || []).filter(c => Number(c.settled) > 0).map(c => c.currency);
    const payWith = [...new Set([s.currency, ...heldCcys, 'USD'])];
    const payDefault = heldCcys.includes(s.currency) ? s.currency : 'USD';
    const amountMode = !s.is_future && s.asset_class !== 'OPTION';
    $('#ticket').innerHTML = `<h2>Order</h2>
      <div class="seg" style="width:100%;margin-bottom:12px"><button class="on buy" data-side="BUY" style="flex:1">Buy</button><button data-side="SELL" style="flex:1">Sell</button></div>
      <div class="form" style="grid-template-columns:1fr 1fr">
        <label class="f">Size in<select id="tUnit"><option value="qty">${unitOf(s)}</option>${amountMode ? '<option value="amt">amount ($)</option>' : ''}</select></label>
        <label class="f">${'Quantity'}<input id="tQty" type="number" min="0" step="${s.is_bond ? 1000 : 1}" value="${s.is_bond ? 100000 : s.is_future || s.asset_class === 'OPTION' ? 1 : 100}"></label>
        <label class="f">Order type<select id="tType"><option value="MARKET">Market</option><option value="LIMIT">Limit</option><option value="STOP">Stop</option></select></label>
        <label class="f" id="tLimitL" style="visibility:hidden">Price<input id="tLimit" type="number" step="0.01" value="${N(s.last) ? N(s.last).toFixed(2) : ''}"></label>
        <label class="f">Good for<select id="tTif"><option value="DAY">Today</option><option value="GTC">Until cancelled</option></select></label>
        <label class="f">Pay / receive in<select id="tCcy">${payWith.map(c => `<option ${c === payDefault ? 'selected' : ''}>${c}</option>`).join('')}</select></label>
      </div>
      <div class="preview" id="tPrev">Enter a size to see the cost.</div>
      <button class="primary" id="tGo" style="width:100%;margin-top:12px;justify-content:center">Place buy order</button>
      <p class="hint">${isLive() ? 'Live fund: market orders fill at the next price update (every 15 minutes in the session, or at the open).' : 'Practice fund: orders fill when you move to the next day.'} ${s.currency !== 'USD' ? `This trades in ${esc(s.currency)}; paying in another currency converts at the dealer's rate in the same deal.` : ''}</p>`;
    let side = 'BUY';
    const qtyOf = () => { const v = N($('#tQty').value) || 0; if ($('#tUnit').value === 'amt') { const up = unitPrice(s); return up > 0 ? Math.floor(v / up) : 0; } return v; };
    let pt = null;
    const preview = () => { clearTimeout(pt); pt = setTimeout(async () => {
      const q = qtyOf(); if (!(q > 0)) { $('#tPrev').innerHTML = 'Enter a size to see the cost.'; return; }
      try { const type = $('#tType').value; const r = await post(P() + '/orders/preview', { security_id: s.id, side, quantity: q, order_type: type, limit_price: type === 'MARKET' ? null : N($('#tLimit').value), settle_ccy: $('#tCcy').value });
        const cash = N(r.cash_needed);
        $('#tPrev').innerHTML = `<div class="kv"><span>Quantity</span><span>${fmt.qty(r.quantity)} ${unitOf(s)}</span><span>Price (${esc(r.price_source)})</span><span>${fmt.px(r.price)} ${esc(r.currency)}</span><span>Gross</span><span>${fmt.num(r.gross, 2)} ${esc(r.currency)}</span>${N(r.accrued_interest) ? `<span>Accrued interest</span><span>${fmt.num(r.accrued_interest, 2)}</span>` : ''}${N(r.initial_margin) ? `<span>Initial margin</span><span>${fmt.num(r.initial_margin, 2)}</span>` : ''}<span>Commission</span><span>${fmt.num(r.commission, 2)}</span>
          <span><b>${cash > 0 ? 'You pay' : 'You receive'}</b></span><span><b class="${cash > 0 ? 'neg' : 'pos'}">${fmt.num(Math.abs(cash), 2)} ${esc(r.currency)}</b></span>${r.fx ? `<span>${r.fx.direction === 'pay' ? 'Paid with' : 'Received in'}</span><span>${esc(r.settle_ccy)} ${fmt.num(r.fx.settle_amount, 2)} at ${fmt.num(r.fx.rate, 4)}</span><span>${esc(r.settle_ccy)} after</span><span>${fmt.num(N(r.fx.settle_balance) + (r.fx.direction === 'pay' ? -1 : 1) * N(r.fx.settle_amount), 0)}</span>` : `<span>${esc(r.currency)} after</span><span>${fmt.num(N(r.cash_projected) - cash, 0)}</span>`}</div>`;
      } catch (e) { $('#tPrev').innerHTML = `<span class="neg">${esc(e.message)}</span>`; } }, 250); };
    $$('#ticket .seg button').forEach(b => b.onclick = () => { side = b.dataset.side; $$('#ticket .seg button').forEach(x => { x.classList.toggle('on', x === b); x.classList.toggle('buy', x === b && side === 'BUY'); x.classList.toggle('sell', x === b && side === 'SELL'); }); const g = $('#tGo'); g.textContent = `Place ${side === 'BUY' ? 'buy' : 'sell'} order`; g.className = side === 'BUY' ? 'primary' : 'sell'; g.style.cssText = 'width:100%;margin-top:12px;justify-content:center'; preview(); });
    $('#tType').onchange = () => { $('#tLimitL').style.visibility = $('#tType').value === 'MARKET' ? 'hidden' : 'visible'; preview(); };
    ['tQty', 'tUnit', 'tLimit', 'tCcy'].forEach(id => $('#' + id).addEventListener('input', preview));
    $('#tUnit').onchange = () => { $('#tQty').value = $('#tUnit').value === 'amt' ? 100000 : (s.is_bond ? 100000 : 100); preview(); };
    $('#tGo').onclick = () => busy($('#tGo'), async () => {
      const q = qtyOf(); if (!(q > 0)) throw new Error('enter a size');
      const type = $('#tType').value;
      const o = await post(P() + '/orders', { security_id: s.id, side, quantity: q, order_type: type, limit_price: type === 'LIMIT' ? N($('#tLimit').value) : null, stop_price: type === 'STOP' ? N($('#tLimit').value) : null, time_in_force: $('#tTif').value, settle_ccy: $('#tCcy').value });
      const st = (o.order && o.order.status) || o.status || 'sent';
      toast(`${side === 'BUY' ? 'Buy' : 'Sell'} ${fmt.qty(q)} ${s.id}: ${String(st).toLowerCase()}${o.fx ? ` · ${o.fx.buy_ccy} ${fmt.num(o.fx.buy_amount, 0)} bought with ${o.fx.sell_ccy}` : ''}`, st === 'REJECTED');
      if (o.fx_note) toast(o.fx_note, true); await loadWorld(); renderShell(); working();
    });
    const working = async () => { const os = await api(P() + '/orders'); const w = os.filter(o => ['NEW', 'WORKING', 'PARTIALLY_FILLED', 'PENDING', 'ACCEPTED'].includes(o.status));
      table($('#working'), w, [{ k: 'security_id', label: 'Order', l: 1, f: o => `<b>${esc(o.side)} ${esc(o.security_id)}</b><span class="sub">${esc(o.order_type)}${o.limit_price ? ' @ ' + fmt.px(o.limit_price) : ''} · ${esc(o.time_in_force)}</span>` }, { k: 'quantity', label: 'Qty', f: o => fmt.qty(o.quantity) }, { k: 'filled_quantity', label: 'Filled', f: o => fmt.qty(o.filled_quantity) }, { k: 'status', label: 'Status', f: o => `<span class="pill acc">${esc(o.status)}</span>` }, { k: 'x', label: '', nosort: 1, f: o => `<button class="small" data-cancel="${o.id}">Cancel</button>` }], { empty: 'No working orders' });
      $$('[data-cancel]').forEach(b => b.onclick = () => busy(b, async () => { await api(P() + '/orders/' + b.dataset.cancel, { method: 'DELETE' }); toast('Order cancelled'); working(); })); };
    working(); preview();
  }
  async function optionsPage(el, args, alive) {
    const unders = await api(W() + '/options'); if (!alive()) return;
    const under = args[0] && unders.find(u => u.underlying === args[0]) ? args[0] : (unders.find(u => u.underlying === pref.get('optUnder', 'SPX')) || unders[0] || {}).underlying;
    if (!under) { el.innerHTML = '<div class="card empty">No listed options today</div>'; return; }
    pref.set('optUnder', under);
    const ch = await api(W() + `/options/${encodeURIComponent(under)}/chain${args[1] ? '?expiry=' + args[1] : ''}`); if (!alive()) return;
    const u = unders.find(x => x.underlying === under) || {};
    const groups = {}; unders.forEach(x => { const g = x.is_index ? 'Indices' : x.underlying.includes('-') && /\d{2}$/.test(x.underlying.split('-').pop() || '') && x.contract_size === 1 ? 'Futures' : x.currency && x.currency !== 'USD' ? 'International stocks' : x.contract_size === 1 ? 'Futures' : 'US stocks & ETFs'; (groups[g] = groups[g] || []).push(x); });
    el.innerHTML = `<div class="card"><div class="row"><label class="f" style="min-width:320px;flex:1">Underlying<select id="oUnder">${Object.entries(groups).map(([g, xs]) => `<optgroup label="${g}">${xs.map(x => `<option value="${esc(x.underlying)}" ${x.underlying === under ? 'selected' : ''}>${esc(x.underlying)} — ${esc(x.name)}${x.currency && x.currency !== 'USD' ? ' (' + x.currency + ')' : ''}</option>`).join('')}</optgroup>`).join('')}</select></label>
      <div class="kv" style="min-width:260px"><span>Level</span><span>${fmt.px(ch.level)} ${esc(u.currency || '')}</span><span>ATM implied vol</span><span>${fmt.pct(u.atm_iv, 1)}</span><span>Contract</span><span>${fmt.qty(u.contract_size || 100)} per contract · ${esc(u.exchange || '')}</span><span>Style</span><span>${esc(ch.style)}</span></div></div>
      <div class="chips" style="margin-top:12px">${ch.expiries.map(e => `<button class="chip ${e === ch.expiry ? 'on' : ''}" data-exp="${e}">${fmt.date(e).replace(/, \d{4}$/, '')}</button>`).join('')}</div></div>
      <div class="card flush" style="margin-top:16px"><h2>Chain <small>${fmt.date(ch.expiry)} · ${ch.days_to_expiry} days · click a bid or ask to trade it</small></h2><div class="tbl-wrap chain"><table><thead><tr><th colspan="4" style="text-align:center;color:var(--pos)">Calls</th><th></th><th colspan="4" style="text-align:center;color:var(--neg)">Puts</th></tr><tr><th>Δ</th><th>IV</th><th>Bid</th><th>Ask</th><th style="text-align:center">Strike</th><th>Bid</th><th>Ask</th><th>IV</th><th>Δ</th></tr></thead><tbody>${ch.rows.map(r => { const atm = Math.abs(r.strike - ch.level) === Math.min(...ch.rows.map(x => Math.abs(x.strike - ch.level)));
        const c = r.C || {}, p = r.P || {}; return `<tr class="${atm ? 'atm' : ''}"><td class="muted">${fmt.num(c.delta, 2)}</td><td class="muted">${fmt.pct(c.iv, 1)}</td><td class="c" data-o="${esc(c.id || '')}" data-s="SELL">${fmt.num(c.bid, 2)}</td><td class="c" data-o="${esc(c.id || '')}" data-s="BUY">${fmt.num(c.ask, 2)}</td><td class="k">${fmt.num(r.strike, r.strike % 1 ? 2 : 0)}</td><td class="c" data-o="${esc(p.id || '')}" data-s="SELL">${fmt.num(p.bid, 2)}</td><td class="c" data-o="${esc(p.id || '')}" data-s="BUY">${fmt.num(p.ask, 2)}</td><td class="muted">${fmt.pct(p.iv, 1)}</td><td class="muted">${fmt.num(p.delta, 2)}</td></tr>`; }).join('')}</tbody></table></div></div>
      <div id="optTicket" style="margin-top:16px"></div>`;
    $('#oUnder').onchange = () => { location.hash = '#/trade/options/' + encodeURIComponent($('#oUnder').value); };
    $$('[data-exp]').forEach(b => b.onclick = () => { location.hash = `#/trade/options/${encodeURIComponent(under)}/${b.dataset.exp}`; });
    $$('td.c[data-o]').forEach(td => td.onclick = async () => { if (!td.dataset.o) return; const c = await api(W() + `/securities/${encodeURIComponent(td.dataset.o)}?period=1M`).catch(() => null);
      const sec = c ? { ...c.security, last: c.last, bid: c.bid, ask: c.ask, change_pct: null, is_bond: false, is_future: false } : null; if (!sec) return;
      await ticketPage($('#optTicket'), sec, alive, sec); if (td.dataset.s === 'SELL') { const b = $$('#ticket .seg button')[1]; if (b) b.click(); } $('#optTicket').scrollIntoView({ behavior: 'smooth' }); });
  }

  // ---------------------------------------------------------------- positions
  pages.positions = async (main, _, alive) => {
    await loadWorld(); renderShell(); if (!alive()) return; const d = S.dash;
    const byCls = {}; d.positions.forEach(p => { const k = clsName(p.asset_class); byCls[k] = (byCls[k] || 0) + 1; });
    const unreal = d.positions.reduce((a, p) => a + Number(p.unrealized_pnl || 0), 0);
    main.innerHTML = `<div class="page-head"><div><h1>Positions</h1><p>${d.positions.length} open · values in US dollars at today's marks · click a row for research and to trade it.</p></div></div>
      <div class="tiles">${kpi('Long exposure', fmt.money(d.long_exposure))}${kpi('Short exposure', fmt.money(d.short_exposure))}${kpi('Unrealized P&L', `<span class="${sign(unreal)}">${fmt.signed(unreal)}</span>`)}${kpi('Realized P&L', `<span class="${sign(d.realized)}">${fmt.signed(d.realized)}</span>`, 'since the start')}</div>
      <div class="card"><div class="chips" id="pf"><button class="chip on" data-c="">All ${d.positions.length}</button>${Object.entries(byCls).map(([k, n]) => `<button class="chip" data-c="${esc(k)}">${esc(k)} ${n}</button>`).join('')}</div></div>
      <div class="card flush" style="margin-top:16px"><div id="posT"></div></div>`;
    const show = c => positionsTable($('#posT'), d.positions.filter(p => !c || clsName(p.asset_class) === c), d.nav, { maxH: 720 });
    $$('#pf .chip').forEach(b => b.onclick = () => { $$('#pf .chip').forEach(x => x.classList.toggle('on', x === b)); show(b.dataset.c); });
    show('');
  };

  // ---------------------------------------------------------------- markets
  const MARKET_TABS = [['stocks', 'Stocks', s => ['EQUITY', 'ADR', 'REIT'].includes(s.asset_class)], ['etfs', 'ETFs', s => s.asset_class === 'ETF'], ['bonds', 'Bonds', s => s.is_bond],
    ['futures', 'Futures', s => s.is_future], ['crypto', 'Crypto', s => s.asset_class === 'CRYPTO'], ['world', 'International', s => s.currency !== 'USD' && !s.is_future && !s.is_bond]];
  pages.markets = async (main, args, alive) => {
    const secs = await securities(); if (!alive()) return;
    const tab = MARKET_TABS.find(t => t[0] === args[0]) ? args[0] : 'stocks';
    const [, , pred] = MARKET_TABS.find(t => t[0] === tab);
    const rows = secs.filter(pred);
    main.innerHTML = `<div class="page-head"><div><h1>Markets</h1><p>${secs.length.toLocaleString()} listed instruments · ${fmt.date(S.world.current_date)} close${S.world.market_source === 'REAL' ? ' (real market)' : ''}. Currencies are on <a href="#/fx">Cash & FX</a>, option chains on <a href="#/trade/options">Trade</a>.</p></div></div>
      <div class="card"><div class="row"><div class="seg">${MARKET_TABS.map(([k, l, p]) => `<button data-t="${k}" class="${k === tab ? 'on' : ''}">${l} <span class="muted">${secs.filter(p).length}</span></button>`).join('')}</div><span class="spacer"></span><input id="mq" placeholder="Filter" style="width:220px"></div></div>
      <div class="card flush" style="margin-top:16px"><div id="mt"></div></div>`;
    $$('.seg button', main).forEach(b => b.onclick = () => { location.hash = '#/markets/' + b.dataset.t; });
    const cols = tab === 'bonds' ? [
      { k: 'id', label: 'Bond', l: 1, f: s => `<b>${esc(s.id)}</b><span class="sub">${esc(s.name)}</span>` }, { k: 'rating', label: 'Rating', l: 1, f: s => esc(s.rating || '—') }, { k: 'currency', label: 'Ccy', l: 1 },
      { k: 'coupon', label: 'Coupon', f: s => s.coupon ? fmt.pct(s.coupon, 3) : '<span class="muted">bill</span>' }, { k: 'maturity', label: 'Maturity', f: s => fmt.date(s.maturity) }, { k: 'last', label: 'Price', f: s => fmt.px(s.last) }, { k: 'change_pct', label: 'Day', cls: s => sign(s.change_pct), f: s => fmt.spct(s.change_pct) }]
      : tab === 'futures' ? [
      { k: 'id', label: 'Contract', l: 1, f: s => `<b>${esc(s.id)}</b><span class="sub">${esc(s.name)}</span>` }, { k: 'currency', label: 'Ccy', l: 1 }, { k: 'expiry', label: 'Expiry', f: s => fmt.date(s.expiry) }, { k: 'multiplier', label: 'Size', f: s => fmt.qty(s.multiplier) + ' ' + esc(s.unit || '') },
      { k: 'last', label: 'Price', f: s => fmt.px(s.last) }, { k: 'change_pct', label: 'Day', cls: s => sign(s.change_pct), f: s => fmt.spct(s.change_pct) }, { k: 'volume', label: 'Volume', f: s => fmt.big(s.volume, '') }]
      : [{ k: 'id', label: 'Name', l: 1, f: s => `<b>${esc(s.id)}</b><span class="sub">${esc(s.name)}</span>` }, { k: 'sector', label: 'Sector', l: 1, f: s => `<span class="muted">${esc(s.sector || '')}</span>` }, { k: 'currency', label: 'Ccy', l: 1 },
      { k: 'last', label: 'Price', f: s => fmt.px(s.last) }, { k: 'change_pct', label: 'Day', cls: s => sign(s.change_pct), f: s => fmt.spct(s.change_pct) }, { k: 'market_cap', label: 'Market cap', f: s => s.market_cap ? fmt.big(s.market_cap, s.currency) : '—' },
      { k: 'realized_vol', label: 'Volatility', f: s => fmt.pct(s.realized_vol, 1) }, { k: 'dividend_yield', label: 'Yield', f: s => s.dividend_yield ? fmt.pct(s.dividend_yield, 2) : '—' }, { k: 'volume', label: 'Volume', f: s => fmt.big(s.volume, '') }];
    const show = q => { const qq = (q || '').trim().toUpperCase(); table($('#mt'), qq ? rows.filter(s => s.id.toUpperCase().includes(qq) || String(s.name).toUpperCase().includes(qq)) : rows, cols, { sortKey: tab === 'bonds' ? 'maturity' : tab === 'futures' ? 'expiry' : 'market_cap', sortDir: tab === 'bonds' || tab === 'futures' ? 1 : -1, maxH: 720, onRow: s => { location.hash = '#/asset/' + encodeURIComponent(s.id); } }); };
    $('#mq').oninput = e => show(e.target.value); show('');
  };

  // ---------------------------------------------------------------- one asset: price, statistics from the equation library, trade
  pages.asset = async (main, args, alive) => {
    const sid = args[0]; if (!sid) { location.hash = '#/markets'; return; }
    const [a, detail] = await Promise.all([api(`/fs2/worlds/${S.wid}/analytics/${encodeURIComponent(sid)}`), sid.startsWith('FX:') ? null : api(W() + `/securities/${encodeURIComponent(sid)}?period=1Y`).catch(() => null)]); if (!alive()) return;
    const x = a.asset, m = a.metrics, info = a.metric_info;
    const held = (S.dash.positions || []).find(p => p.security_id === sid);
    const group = (title, keys) => `<div class="card"><h2>${title}</h2><div class="kv">${keys.filter(k => info[k]).map(k => `<span title="equation ${esc((info[k].equations || []).join(', '))}">${esc(info[k].label)}</span><span class="${['ret_1d', 'ret_1m', 'ret_3m', 'ret_1y', 'ann_return', 'alpha_ann', 'mom_12_1', 'sharpe', 'sortino'].includes(k) ? sign(m[k]) : ''}">${mfmt(m[k], info[k].fmt)}</span>`).join('')}</div></div>`;
    main.innerHTML = `<div class="page-head"><div><div class="muted">${esc(x.class_label)} · ${esc(x.currency)}${x.sector ? ' · ' + esc(x.sector) : ''}${x.country ? ' · ' + esc(x.country) : ''}</div><h1>${esc(x.id)} <span class="muted" style="font-weight:500;font-size:18px">${esc(x.name)}</span></h1></div>
      <div class="row">${sid.startsWith('FX:') ? `<a class="btn primary" href="#/fx">Convert currency</a>` : `<a class="btn primary" href="#/trade/${encodeURIComponent(sid)}">Trade</a>`}<a class="btn" href="#/analytics/equations/${encodeURIComponent(sid)}">Equations on this asset</a></div></div>
      <div class="tiles">${kpi('Last', fmt.px(x.last) + ' <span class="muted" style="font-size:14px">' + esc(x.currency) + '</span>', `${fmt.spct(m.ret_1d)} today`)}${kpi('1 year', `<span class="${sign(m.ret_1y)}">${fmt.spct(m.ret_1y)}</span>`, `annualised ${fmt.spct(m.ann_return)}`)}${kpi('Volatility', fmt.pct(m.vol_ann, 1), `GARCH ${fmt.pct(m.garch_vol, 1)} · EWMA ${fmt.pct(m.ewma_vol, 1)}`)}${kpi('Sharpe', fmt.num(m.sharpe, 2), `Sortino ${fmt.num(m.sortino, 2)}`)}${kpi('Beta to ' + esc(a.market), fmt.num(m.beta, 2), `alpha ${fmt.spct(m.alpha_ann)} a year`)}${kpi('Shaffer Score', x.shaffer != null ? fmt.num(x.shaffer, 1) : '<span class="muted" style="font-size:16px">in development</span>', a.shaffer.enabled ? 'version ' + esc(a.shaffer.version) : '<a href="#/shaffer">what it will be →</a>')}</div>
      ${held ? `<div class="card" style="margin-bottom:16px"><div class="row"><b>You hold ${fmt.qty(held.quantity)}</b><span class="muted">worth ${fmt.money(held.market_value)} · cost ${fmt.px(held.average_cost)}</span><span class="${sign(held.unrealized_pnl)}">${fmt.signed(held.unrealized_pnl)} unrealized</span><span class="muted">${fmt.pct(held.weight, 1)} of NAV</span></div></div>` : ''}
      <div class="card"><h2>Price <small>${a.series.closes.length} sessions</small></h2><div id="aChart"></div></div>
      <div class="grid g3" style="margin-top:16px">
        ${group('Returns', ['ret_1d', 'ret_1m', 'ret_3m', 'ret_1y', 'ann_return', 'geo_mean', 'mom_12_1', 'cum_log_1y', 't_mean'])}
        ${group('Risk', ['vol_ann', 'ewma_vol', 'garch_vol', 'garch_persistence', 'max_drawdown', 'var_95', 'es_95', 'skew', 'kurt'])}
        ${group('Market & behaviour', ['beta', 'corr_mkt', 'r2_mkt', 'alpha_ann', 'capm_er', 'sharpe', 'sortino', 'ar1_phi', 'half_life', 'adf_t', 'adf_stationary', 'acf1', 'zscore_50', 'n'])}
      </div>
      ${detail && detail.security && detail.security.fundamentals ? `<div class="card" style="margin-top:16px"><h2>Fundamentals <small>${esc(detail.security.fundamentals.source || '')} ${esc(detail.security.fundamentals.as_of || '')}</small></h2><div class="kv" style="max-width:520px">${['revenue', 'net_income', 'ebitda', 'free_cash_flow', 'total_debt', 'cash'].map(k => `<span>${k.replace(/_/g, ' ')}</span><span>${fmt.big(detail.security.fundamentals[k], x.currency)}</span>`).join('')}<span>EPS</span><span>${fmt.num(detail.security.fundamentals.eps, 2)}</span></div></div>` : ''}`;
    lineChart($('#aChart'), [{ name: x.id, data: a.series.closes, area: true }], { labels: a.series.dates.map(d => fmt.date(d)), fmtY: v => fmt.px(v), h: 260 });
  };

  // ---------------------------------------------------------------- cash & FX
  pages.fx = async (main, _, alive) => {
    await loadWorld(); renderShell(); if (!alive()) return;
    const [fx, loans, cash] = await Promise.all([api(W() + '/fx'), api(P() + '/ccy-loans').catch(() => ({ rates: [], loans: [], capacity: 0, outstanding_base: 0 })), api(P() + '/cash')]); if (!alive()) return;
    const d = S.dash; const accts = (d.cash_accounts || []).filter(c => c.is_base || Number(c.settled) || Number(c.projected)); const tot = accts.reduce((a, c) => a + Number(c.base_value || 0), 0);
    const ccys = ['USD', ...fx.rows.map(r => r.ccy).filter(c => c !== 'USD')];
    main.innerHTML = `<div class="page-head"><div><h1>Cash & FX</h1><p>Every currency the fund holds, valued in dollars at today's rates. Convert in one deal (crosses are priced through the dollar, as dealers do), or borrow a currency at its own interest rate.</p></div></div>
      <div class="tiles">${kpi('Cash, all currencies', fmt.money(tot), `${accts.filter(c => Number(c.settled)).length} currencies held`)}${accts.filter(c => Number(c.settled)).slice(0, 4).map(c => kpi(`${esc(c.currency)} balance`, fmt.num(c.settled, 0), `${fmt.money(c.base_value)} · rate ${fmt.pct(c.policy_rate, 2)}`)).join('')}${kpi('Currency loans', fmt.money(loans.outstanding_base), `room ${fmt.big(loans.capacity)}`)}</div>
      <div class="grid g-main">
        <div class="stack">
          <div class="card flush"><h2>Balances by currency</h2><div id="bal"></div></div>
          <div class="card flush"><h2>Exchange rates <small>${fmt.date(fx.date)} · per US dollar unless marked · policy rates drive the carry</small></h2><div id="rates"></div></div>
        </div>
        <div class="stack">
          <div class="card"><h2>Convert</h2><div class="form" style="grid-template-columns:1fr 1fr">
            <label class="f">Buy<select id="cBuy">${ccys.map(c => `<option ${c === 'EUR' ? 'selected' : ''}>${c}</option>`).join('')}</select></label>
            <label class="f">Sell<select id="cSell">${ccys.map(c => `<option ${c === 'USD' ? 'selected' : ''}>${c}</option>`).join('')}</select></label>
            <label class="f">Amount<input id="cAmt" type="number" min="0" value="1000000"></label>
            <label class="f">Amount is in<select id="cIn"><option value="BUY">the currency I buy</option><option value="SELL">the currency I sell</option></select></label></div>
            <div class="preview" id="cPrev"></div><button class="primary" id="cGo" style="width:100%;margin-top:12px;justify-content:center">Convert</button>
            <p class="hint">Spot settles in two business days; the balance shows as projected until then.</p></div>
          <div class="card"><h2>Borrow a currency <small>at its policy rate plus a spread</small></h2><div class="form" style="grid-template-columns:1fr 1fr">
            <label class="f">Currency<select id="lCcy">${(loans.rates || []).map(r => `<option value="${r.currency}">${r.currency} · ${fmt.pct(r.borrow_rate, 2)}</option>`).join('')}</select></label>
            <label class="f">Amount<input id="lAmt" type="number" min="0" value="1000000"></label>
            <label class="f">Term<select id="lTerm"><option value="0">Open (rate resets daily)</option><option value="30">1 month fixed</option><option value="90">3 months fixed</option><option value="180">6 months fixed</option><option value="365">1 year fixed</option></select></label>
            <div><button id="lGo" style="width:100%;justify-content:center">Borrow</button></div></div>
            <p class="hint">Borrow a low-rate currency (yen, Swiss franc) and hold a high-rate one to earn the carry; the risk is the exchange rate.</p>
            <div id="loans" style="margin-top:10px"></div></div>
        </div>
      </div>
      <div class="card flush" style="margin-top:16px"><h2>Cash movements</h2><div id="moves"></div></div>`;
    table($('#bal'), accts, [
      { k: 'currency', label: 'Currency', l: 1, f: r => `<b>${esc(r.currency)}</b>${r.is_base ? ' <span class="pill acc">base</span>' : ''}` },
      { k: 'settled', label: 'Settled', cls: r => Number(r.settled) < 0 ? 'neg' : '', f: r => fmt.num(r.settled, 2) },
      { k: 'projected', label: 'After pending', f: r => fmt.num(r.projected, 2) },
      { k: 'rate', label: 'USD per unit', f: r => Number(r.rate) >= 0.1 ? fmt.num(r.rate, 4) : Number(r.rate).toPrecision(4) },
      { k: 'base_value', label: 'In USD', f: r => fmt.money(r.base_value) },
      { k: 'share', label: 'Share', v: r => Number(r.base_value) / (tot || 1), f: r => `${fmt.pct(Number(r.base_value) / (tot || 1), 1)}<span class="cell-bar bar"><i style="width:${Math.min(100, Math.abs(Number(r.base_value)) / (tot || 1) * 100)}%"></i></span>` },
    ], { sortKey: 'base_value', total: { currency: '<b>Total</b>', base_value: fmt.money(tot) } });
    table($('#rates'), fx.rows, [
      { k: 'pair', label: 'Pair', l: 1, f: r => `<b>${esc(r.pair)}</b>` }, { k: 'quote', label: 'Rate', f: r => fmt.num(r.quote, r.quote >= 20 ? 2 : 4) },
      { k: 'day_pct', label: 'Day', cls: r => sign(r.day_pct), f: r => fmt.spct(r.day_pct) }, { k: 'month_pct', label: 'Month', cls: r => sign(r.month_pct), f: r => fmt.spct(r.month_pct) },
      { k: 'rate', label: 'Policy rate', f: r => fmt.pct(r.rate, 2) }, { k: 'carry', label: 'Carry vs USD', cls: r => sign(r.carry), f: r => fmt.spct(r.carry) },
      { k: 'realized_vol', label: 'Volatility', f: r => fmt.pct(r.realized_vol, 1) },
      { k: 'h', label: '3 months', nosort: 1, f: r => spark((r.history || []).slice(-63).map(h => Array.isArray(h) ? h[1] : (h.spot != null ? h.spot : h.quote))) },
    ], { sortKey: 'pair', sortDir: 1, onRow: r => { location.hash = '#/asset/FX:' + r.ccy; } });
    const moves = (cash.movements || []).slice(0, 300);
    table($('#moves'), moves, [{ k: 'date', label: 'Date', l: 1, f: m => fmt.date(m.date) }, { k: 'currency', label: 'Ccy', l: 1 }, { k: 'kind', label: 'Kind', l: 1, f: m => `<span class="pill">${esc(String(m.kind).replace(/_/g, ' ').toLowerCase())}</span>` }, { k: 'amount', label: 'Amount', cls: m => sign(m.amount), f: m => fmt.num(m.amount, 2) }, { k: 'balance_after', label: 'Balance', f: m => fmt.num(m.balance_after, 2) }, { k: 'reference', label: 'Reference', l: 1, f: m => `<span class="muted">${esc(m.reference)}</span>` }], { empty: 'No cash movements yet', maxH: 420 });
    const renderLoans = () => { const open = (loans.loans || []).filter(l => l.status === 'OPEN');
      $('#loans').innerHTML = open.length ? open.map(l => `<div class="row" style="padding:8px 0;border-top:1px solid var(--border)"><b>${esc(l.currency)} ${fmt.num(l.principal, 0)}</b><span class="muted">${fmt.pct(l.rate, 2)} · ${l.maturity ? 'due ' + fmt.date(l.maturity) : 'open'}</span><span class="spacer"></span><button class="small" data-repay="${l.id}">Repay</button></div>`).join('') : '<div class="muted">No currency loans.</div>';
      $$('[data-repay]').forEach(b => b.onclick = () => busy(b, async () => { await post(P() + `/ccy-loans/${b.dataset.repay}/repay`, {}); toast('Loan repaid'); route(); })); };
    renderLoans();
    let t = null; const prev = () => { clearTimeout(t); t = setTimeout(() => { const b = $('#cBuy').value, s = $('#cSell').value, a = N($('#cAmt').value) || 0; if (b === s) { $('#cPrev').textContent = 'Pick two different currencies.'; return; }
      const cross = fx.cross && fx.cross[b] && fx.cross[b][s]; const inBuy = $('#cIn').value === 'BUY';
      $('#cPrev').innerHTML = cross ? `<div class="kv"><span>Mid rate</span><span>1 ${b} = ${fmt.num(cross, cross >= 20 ? 2 : 5)} ${s}</span><span>${inBuy ? 'You pay about' : 'You get about'}</span><span><b>${inBuy ? fmt.num(a * cross, 2) + ' ' + s : fmt.num(a / cross, 2) + ' ' + b}</b></span><span>Route</span><span>${b !== 'USD' && s !== 'USD' && !([b, s].sort().join() === 'EUR,GBP') ? `one deal, priced through USD` : 'direct'}</span></div>` : ''; }, 150); };
    ['cBuy', 'cSell', 'cAmt', 'cIn'].forEach(id => $('#' + id).addEventListener('input', prev)); prev();
    $('#cGo').onclick = () => busy($('#cGo'), async () => { const r = await post(P() + '/fx/spot', { buy_ccy: $('#cBuy').value, sell_ccy: $('#cSell').value, amount: N($('#cAmt').value), amount_ccy: $('#cIn').value }); toast(`Bought ${$('#cBuy').value}, settles ${fmt.date(r.settlement_date || r.value_date)}`); route(); });
    $('#lGo').onclick = () => busy($('#lGo'), async () => { await post(P() + '/ccy-loans', { currency: $('#lCcy').value, amount: N($('#lAmt').value), term_days: +$('#lTerm').value }); toast(`Borrowed ${$('#lCcy').value}`); route(); });
  };

  // ---------------------------------------------------------------- risk
  pages.risk = async (main, _, alive) => {
    const r = await api(P() + '/risk'); if (!alive()) return; const d = S.dash; const v = r.var || {}; const f = r.factors || {};
    main.innerHTML = `<div class="page-head"><div><h1>Risk</h1><p>One-day value at risk from ${v.days || 0} days of history, stress scenarios, sensitivities and limits.</p></div></div>
      <div class="tiles">${kpi('VaR 95%, one day', fmt.money(v.var95), fmt.pct(N(v.var95) / (d.nav || 1), 2) + ' of NAV')}${kpi('VaR 99%, one day', fmt.money(v.var99), `ten days ${fmt.money(v.var99_10d)}`)}${kpi('Expected shortfall 97.5%', fmt.money(v.es975))}${kpi('Rate sensitivity (DV01)', fmt.money(f.dv01), 'per basis point')}${kpi('Credit sensitivity (CS01)', fmt.money(f.cs01))}${kpi('Vega', fmt.money(f.vega), 'per vol point')}</div>
      <div class="grid g2"><div class="card"><h2>Stress scenarios <small>P&L if it happened today</small></h2><div id="stress"></div></div>
      <div class="card"><h2>Limits</h2>${(r.limits || []).map(l => `<div style="margin-bottom:11px"><div class="row" style="justify-content:space-between;font-size:12.5px"><span>${esc(l.name)}</span><span class="num">${fmt.num(l.value, 2)}${esc(l.unit || '')} <span class="muted">/ ${fmt.num(l.hard, 2)}</span></span></div><div class="bar ${l.status === 'BREACH' || l.utilization > 1 ? 'bad' : l.utilization > 0.8 ? 'warn' : 'good'}"><i style="width:${Math.min(100, (l.utilization || 0) * 100)}%"></i></div></div>`).join('') || '<div class="empty">No limits</div>'}</div></div>
      <div class="grid g2" style="margin-top:16px"><div class="card"><h2>Daily P&L history <small>the series VaR is drawn from</small></h2><div id="varS"></div></div>
      <div class="card flush"><h2>Liquidity <small>days to sell at a fifth of daily volume</small></h2><div id="liq"></div></div></div>`;
    barChart($('#stress'), Object.values(r.stress || {}).map(s => ({ label: s.label, value: Number(s.pnl) })).sort((a, b) => a.value - b.value));
    lineChart($('#varS'), [{ name: 'P&L', data: (v.series || []).map(s => s.pnl), color: css('--accent') }], { labels: (v.series || []).map(s => fmt.date(s.date)), zero: true, fmtY: x => fmt.big(x), h: 200 });
    table($('#liq'), (r.liquidity && r.liquidity.positions) || [], [{ k: 'id', label: 'Position', l: 1, f: p => `<b>${esc(p.id)}</b>` }, { k: 'market_value', label: 'Value', f: p => fmt.money(p.market_value) }, { k: 'days_to_liquidate', label: 'Days', f: p => fmt.num(p.days_to_liquidate, 1) }, { k: 'bucket', label: 'Bucket', f: p => `<span class="pill">${esc(p.bucket)}</span>` }], { sortKey: 'days_to_liquidate', maxH: 320, empty: 'No positions' });
  };

  // ---------------------------------------------------------------- performance
  pages.performance = async (main, _, alive) => {
    const [x, career] = await Promise.all([api(P() + '/pnl-explain').catch(() => ({ available: false })), api(P() + '/career').catch(() => null)]); if (!alive()) return;
    const d = S.dash; const m = (career && career.metrics) || {}; const hist = d.nav_history || [];
    const inc = d.income || {};
    main.innerHTML = `<div class="page-head"><div><h1>Performance</h1><p>How the fund has done, where today's P&L came from, and the income statement.</p></div></div>
      <div class="tiles">${kpi('Return since start', `<span class="${sign(m.return_since_inception)}">${fmt.spct(m.return_since_inception)}</span>`, `benchmark ${fmt.spct(m.benchmark_return)}`)}${kpi('Alpha', `<span class="${sign(m.alpha)}">${fmt.spct(m.alpha)}</span>`, 'return above the benchmark')}${kpi('Sharpe ratio', m.sharpe != null && (m.days || 0) >= 20 ? fmt.num(m.sharpe, 2) : '—', `volatility ${fmt.pct(m.volatility, 1)}`)}${kpi('Max drawdown', fmt.pct(m.max_drawdown, 1), `now ${fmt.pct(m.current_drawdown, 1)}`)}</div>
      <div class="grid g-main"><div class="card"><h2>Net asset value</h2><div id="navH"></div></div><div class="card"><h2>Today by source <small>${x.available ? fmt.date(x.date) : 'no closed day yet'}</small></h2><div id="expl"></div></div></div>
      <div class="grid g2" style="margin-top:16px"><div class="card"><h2>Daily P&L</h2><div id="dpl"></div></div>
      <div class="card"><h2>Income statement <small>since the start</small></h2><div class="kv"><span>Realized P&L</span><span class="${sign(d.realized)}">${fmt.signed(d.realized)}</span><span>Unrealized P&L</span><span class="${sign(d.unrealized)}">${fmt.signed(d.unrealized)}</span><span>Dividends</span><span>${fmt.signed(Math.abs(inc.dividends || 0))}</span><span>Interest earned</span><span>${fmt.signed(Math.abs(inc.interest || 0))}</span><span>Interest paid</span><span class="neg">${fmt.signed(-Math.abs(inc.interest_expense || 0))}</span><span>Commissions</span><span class="neg">${fmt.signed(-Math.abs(inc.commissions || 0))}</span><span><b>Total P&L</b></span><span><b class="${sign(d.since_inception_pnl)}">${fmt.signed(d.since_inception_pnl)}</b></span></div></div></div>`;
    lineChart($('#navH'), [{ name: 'NAV', data: hist.map(h => h.nav), area: true }], { labels: hist.map(h => fmt.date(h.date)), fmtY: v => fmt.big(v), h: 240 });
    barChart($('#expl'), x.available ? Object.entries(x.explain).filter(([, v]) => Number(v)).map(([k, v]) => ({ label: k.replace(/_/g, ' '), value: Number(v) })).sort((a, b) => Math.abs(b.value) - Math.abs(a.value)) : []);
    lineChart($('#dpl'), [{ name: 'Day P&L', data: hist.map(h => h.day_pnl), color: css('--violet') }], { labels: hist.map(h => fmt.date(h.date)), zero: true, fmtY: v => fmt.big(v), h: 200 });
  };

  // ---------------------------------------------------------------- activity
  pages.activity = async (main, args, alive) => {
    const tab = args[0] || 'trades';
    const [trades, orders, news] = await Promise.all([api(P() + '/trades'), api(P() + '/orders'), api(W() + '/news').catch(() => [])]); if (!alive()) return;
    main.innerHTML = `<div class="page-head"><div><h1>Activity</h1><p>Every fill, every order and the news tape.</p></div><div class="seg">${[['trades', `Trades ${trades.length}`], ['orders', `Orders ${orders.length}`], ['news', 'News']].map(([k, l]) => `<button data-t="${k}" class="${k === tab ? 'on' : ''}">${l}</button>`).join('')}</div></div><div class="card flush"><div id="act"></div></div>`;
    $$('.seg button', main).forEach(b => b.onclick = () => { location.hash = '#/activity/' + b.dataset.t; });
    if (tab === 'trades') table($('#act'), trades.slice().reverse(), [{ k: 'trade_date', label: 'Date', l: 1, f: t => fmt.date(t.trade_date) }, { k: 'security_id', label: 'Trade', l: 1, f: t => `<b class="${t.side === 'BUY' ? 'pos' : 'neg'}">${esc(t.side)}</b> <b>${esc(t.security_id)}</b>` }, { k: 'quantity', label: 'Qty', f: t => fmt.qty(t.quantity) }, { k: 'price', label: 'Price', f: t => fmt.px(t.price) }, { k: 'currency', label: 'Ccy', l: 1 }, { k: 'net_amount', label: 'Net amount', f: t => fmt.num(t.net_amount, 2) }, { k: 'realized_pnl', label: 'Realized', cls: t => sign(t.realized_pnl), f: t => Number(t.realized_pnl) ? fmt.signed(t.realized_pnl) : '' }, { k: 'status', label: 'Settlement', f: t => `<span class="pill ${t.status === 'SETTLED' ? 'pos' : 'acc'}">${esc(t.status.toLowerCase())}</span><span class="sub">${fmt.date(t.settlement_date)}</span>` }], { maxH: 760, empty: 'No trades yet' });
    else if (tab === 'orders') table($('#act'), orders.slice().reverse(), [{ k: 'entered_date', label: 'Entered', l: 1, f: o => fmt.date(o.entered_date) }, { k: 'security_id', label: 'Order', l: 1, f: o => `<b>${esc(o.side)} ${esc(o.security_id)}</b><span class="sub">${esc(o.order_type)}${o.limit_price ? ' @ ' + fmt.px(o.limit_price) : ''}</span>` }, { k: 'quantity', label: 'Qty', f: o => fmt.qty(o.quantity) }, { k: 'filled_quantity', label: 'Filled', f: o => fmt.qty(o.filled_quantity) }, { k: 'avg_fill_price', label: 'Avg price', f: o => fmt.px(o.avg_fill_price) }, { k: 'status', label: 'Status', f: o => `<span class="pill ${o.status === 'FILLED' ? 'pos' : o.status === 'REJECTED' ? 'neg' : 'acc'}">${esc(o.status.toLowerCase())}</span>${o.reason ? `<span class="sub">${esc(o.reason)}</span>` : ''}` }], { maxH: 760, empty: 'No orders yet' });
    else $('#act').innerHTML = `<div style="padding:6px 18px">${news.slice(0, 120).map(n => `<div style="padding:10px 0;border-bottom:1px solid var(--border)"><div style="font-weight:600">${n.link ? `<a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.headline)}</a>` : esc(n.headline)}</div><div class="muted" style="font-size:12.5px">${esc(n.publisher || String(n.category || '').replace(/_/g, ' ').toLowerCase())} · ${fmt.date(n.date)}${(n.refs || []).length ? ' · ' + n.refs.slice(0, 4).map(r => `<a href="#/asset/${encodeURIComponent(r)}">${esc(r)}</a>`).join(' ') : ''}</div>${n.body ? `<div style="margin-top:4px;font-size:13px">${esc(String(n.body).slice(0, 280))}</div>` : ''}</div>`).join('') || '<div class="empty">No news yet</div>'}</div>`;
  };

  // ---------------------------------------------------------------- analytics: scoreboard · equation library
  const SCORE_SETS = {
    overview: ['ret_1m', 'ret_1y', 'vol_ann', 'sharpe', 'beta', 'alpha_ann', 'max_drawdown'],
    risk: ['vol_ann', 'ewma_vol', 'garch_vol', 'garch_persistence', 'var_95', 'es_95', 'skew', 'kurt', 'max_drawdown'],
    behaviour: ['ar1_phi', 'half_life', 'adf_t', 'adf_stationary', 'acf1', 'zscore_50', 'mom_12_1', 't_mean'],
    market: ['beta', 'corr_mkt', 'r2_mkt', 'alpha_ann', 'capm_er', 'sharpe', 'sortino'],
  };
  const SCORE_CLASSES = [['all', 'All', () => true], ['stocks', 'Stocks', r => ['EQUITY', 'ADR', 'REIT'].includes(r.asset_class)], ['etfs', 'ETFs', r => r.asset_class === 'ETF'],
    ['bonds', 'Bonds', r => /BOND|MBS|STRUCTURED/.test(r.asset_class)], ['futures', 'Futures', r => r.asset_class === 'FUTURE'], ['crypto', 'Crypto', r => r.asset_class === 'CRYPTO'], ['fx', 'Currencies', r => r.asset_class === 'FX']];
  pages.analytics = async (main, args, alive) => {
    const tab = args[0] === 'equations' ? 'equations' : 'scores';
    main.innerHTML = `<div class="page-head"><div><h1>Analytics</h1><p>${tab === 'scores' ? 'Every tradeable asset scored with the equation library: returns, risk, market sensitivity and time-series behaviour from its price history, against the S&P 500 and the dollar policy rate.' : 'The equation library: every formula, the code that implements it, and its value on any asset.'}</p></div>
      <div class="seg"><button data-t="scores" class="${tab === 'scores' ? 'on' : ''}">Scoreboard</button><button data-t="equations" class="${tab === 'equations' ? 'on' : ''}">Equation library</button></div></div><div id="an"><div class="loading">Scoring every asset…</div></div>`;
    $$('.page-head .seg button').forEach(b => b.onclick = () => { location.hash = '#/analytics/' + b.dataset.t; });
    return tab === 'scores' ? scoreboard($('#an'), alive) : equations($('#an'), args[1], alive);
  };
  async function scoreboard(el, alive) {
    const sb = await api(`/fs2/worlds/${S.wid}/scores`); if (!alive()) return;
    const info = sb.metric_info; let cls = pref.get('scoreCls', 'all'), set = pref.get('scoreSet', 'overview'), q = '';
    el.innerHTML = `<div class="card"><div class="row"><div class="chips" id="scC">${SCORE_CLASSES.map(([k, l, p]) => `<button class="chip ${k === cls ? 'on' : ''}" data-c="${k}">${l} <span class="muted">${sb.rows.filter(p).length}</span></button>`).join('')}</div><span class="spacer"></span>
      <div class="seg" id="scS">${Object.keys(SCORE_SETS).map(k => `<button data-s="${k}" class="${k === set ? 'on' : ''}">${k[0].toUpperCase() + k.slice(1)}</button>`).join('')}</div><input id="scQ" placeholder="Filter" style="width:180px"></div>
      <p class="hint">${sb.count.toLocaleString()} assets · ${fmt.date(sb.date)} · market ${esc(sb.market)} · risk-free ${fmt.pct(sb.rf, 2)} · Shaffer Score ${sb.shaffer.enabled ? `live (version ${esc(sb.shaffer.version)}, ${sb.shaffer.scored} scored)` : '<b>in development</b> (<a href="#/shaffer">about</a>)'} · hover a column for its equations</p></div>
      <div class="card flush" style="margin-top:16px"><div id="scT"></div></div>`;
    const show = () => { const pred = SCORE_CLASSES.find(c => c[0] === cls)[2]; const qq = q.trim().toUpperCase();
      const rows = sb.rows.filter(pred).filter(r => !qq || r.id.toUpperCase().includes(qq) || String(r.name).toUpperCase().includes(qq));
      const cols = [{ k: 'id', label: 'Asset', l: 1, f: r => `<b>${esc(r.id)}</b><span class="sub">${esc(r.name)}</span>` }, { k: 'class_label', label: 'Class', l: 1, f: r => `<span class="pill">${esc(r.class_label)}</span>` }, { k: 'currency', label: 'Ccy', l: 1 }, { k: 'last', label: 'Last', f: r => fmt.px(r.last) },
        ...SCORE_SETS[set].filter(k => info[k]).map(k => ({ k, label: SHORT[k] || info[k].label, title: `${info[k].label}: equation ${(info[k].equations || []).join(', ') || 'n/a'}`, cls: r => ['ret_1m', 'ret_1y', 'alpha_ann', 'mom_12_1', 'sharpe', 'sortino', 'zscore_50'].includes(k) ? sign(r[k]) : k === 'max_drawdown' && r[k] < -0.25 ? 'neg' : '', f: r => mfmt(r[k], info[k].fmt), v: r => typeof r[k] === 'boolean' ? (r[k] ? 1 : 0) : r[k] })),
        { k: 'shaffer', label: 'Shaffer', title: 'Shaffer Score', f: r => r.shaffer == null ? '<span class="faint">—</span>' : `<b>${fmt.num(r.shaffer, 1)}</b>` }];
      table($('#scT'), rows, cols, { sortKey: sb.shaffer.enabled ? 'shaffer' : (set === 'risk' ? 'vol_ann' : set === 'behaviour' ? 'half_life' : 'sharpe'), sortDir: set === 'behaviour' ? 1 : -1, maxH: 760, onRow: r => { location.hash = '#/asset/' + encodeURIComponent(r.id); } }); };
    $$('#scC .chip').forEach(b => b.onclick = () => { cls = b.dataset.c; pref.set('scoreCls', cls); $$('#scC .chip').forEach(x => x.classList.toggle('on', x === b)); show(); });
    $$('#scS button').forEach(b => b.onclick = () => { set = b.dataset.s; pref.set('scoreSet', set); $$('#scS button').forEach(x => x.classList.toggle('on', x === b)); show(); });
    $('#scQ').oninput = e => { q = e.target.value; show(); };
    show();
  }
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
  async function equations(el, sid, alive) {
    const [cat, a] = await Promise.all([api('/fs2/equations'), sid ? api(`/fs2/worlds/${S.wid}/analytics/${encodeURIComponent(sid)}`).catch(() => null) : null]); if (!alive()) return;
    const vals = {}; if (a) (a.applied || []).forEach(x => { vals[x.id] = x; });
    const info = a ? a.metric_info : {};
    el.innerHTML = `<div class="card"><div class="row"><label class="f" style="flex:1;min-width:240px">Apply to an asset<input id="eqAsset" list="eqAssets" placeholder="ticker, e.g. SPY, AAPL, SAP-DE, FX:EUR" value="${esc(sid || '')}"><datalist id="eqAssets">${(S.secs || []).slice(0, 3000).map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}</datalist></label>
      <label class="f" style="flex:1;min-width:200px">Search the library<input id="eqQ" placeholder="e.g. GARCH, duration, Sharpe"></label></div>
      ${a ? `<p class="hint">Showing values for <b>${esc(a.asset.id)}</b> (${esc(a.asset.name)}) from ${a.series.closes.length} closes. Equations without a value describe instruments or models rather than a single price series.</p>` : '<p class="hint">Pick an asset to see each equation evaluated on its price history.</p>'}
      <div class="toc" style="margin-top:10px">${cat.sections.map(s => `<a class="chip" href="javascript:void 0" data-sec="${s.key}">${esc(s.title)}</a>`).join('')}</div></div>
      <div id="eqList"></div>`;
    const draw = q => { const qq = (q || '').toLowerCase();
      $('#eqList').innerHTML = cat.sections.map(s => { const eqs = cat.equations.filter(e => e.section === s.key && (!qq || (e.name + ' ' + e.note + ' ' + e.id).toLowerCase().includes(qq))); if (!eqs.length) return '';
        return `<div class="sec-title" id="sec-${s.key}">${esc(s.title)}</div><div class="eq-list">${eqs.map(e => { const v = vals[e.id]; const mi = e.metric && info[e.metric];
          return `<div class="eq"><div class="id">${esc(e.id)}</div><div class="nm">${esc(e.name)}</div><div class="val">${v && v.value != null ? `<span title="${esc(mi ? mi.label : e.metric)}">${esc(mi ? mi.label : e.metric)}: ${mfmt(v.value, mi ? mi.fmt : 'num')}</span>` : e.metric ? `<span class="pill">${esc(e.metric)}</span>` : ''}</div>
            <div class="tex" data-tex="${esc(e.latex)}">${esc(e.latex)}</div><div class="note">${esc(e.note)}</div><div class="fn"><button class="small ghost" data-src="${esc(e.id)}"><code>${esc(e.fn)}</code> · view code</button></div></div>`; }).join('')}</div>`; }).join('') || '<div class="card empty" style="margin-top:16px">No equation matches</div>';
      if (!renderTex($('#eqList'))) { let tries = 0; const iv = setInterval(() => { if (renderTex($('#eqList')) || ++tries > 20) clearInterval(iv); }, 300); }
      $$('[data-src]').forEach(b => b.onclick = async () => { const e = cat.equations.find(x => x.id === b.dataset.src); const r = await api(`/fs2/equations/${encodeURIComponent(e.id)}/source`).catch(err => ({ source: err.message }));
        drawer(`<div class="muted">Equation ${esc(e.id)}</div><h2 style="margin:2px 0 8px">${esc(e.name)}</h2><div class="tex" data-tex="${esc(e.latex)}" style="overflow-x:auto">${esc(e.latex)}</div><p class="muted">${esc(e.note)}</p><div class="muted" style="margin:10px 0 6px"><code>${esc(e.fn)}</code></div><pre style="background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:12px;overflow:auto;font-size:12px;line-height:1.5"><code style="background:none;padding:0">${esc(r.source || '')}</code></pre>`); renderTex($('#drawer')); }); };
    draw('');
    $('#eqQ').oninput = e => draw(e.target.value);
    $('#eqAsset').onchange = e => { const v = e.target.value.trim().toUpperCase(); location.hash = '#/analytics/equations' + (v ? '/' + encodeURIComponent(v) : ''); };
    $$('[data-sec]').forEach(c => c.onclick = () => { const t = $('#sec-' + c.dataset.sec); if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  }

  // ---------------------------------------------------------------- the Shaffer Score
  pages.shaffer = async (main, _, alive) => {
    const st = await api('/fs2/shaffer'); let sb = null; if (st.enabled) sb = await api(`/fs2/worlds/${S.wid}/scores`).catch(() => null); if (!alive()) return;
    const cat = await api('/fs2/equations').catch(() => null); if (!alive()) return;
    const ranked = sb ? sb.rows.filter(r => r.shaffer != null).sort((a, b) => b.shaffer - a.shaffer) : [];
    const [lo, hi] = st.range || [0, 100];
    main.innerHTML = `<div class="page-head"><div><h1>Shaffer Score</h1><p>A single quantitative score for every tradeable asset, from an algorithm in development. This is where it will live: ranked across the whole market, on every asset page and as a column on the Analytics scoreboard.</p></div><span class="pill ${st.enabled ? 'pos' : 'warn'}" style="font-size:13px;padding:5px 12px"><span class="dot"></span>${st.enabled ? 'Live · version ' + esc(st.version) : 'In development'}</span></div>
      ${st.enabled ? `<div class="grid g-main"><div class="card flush"><h2>Ranking <small>${ranked.length} assets scored · ${fmt.date(sb.date)}</small></h2><div id="shT"></div></div><div class="card"><h2>Distribution</h2><div id="shD"></div></div></div>` : `
      <div class="grid g2">
        <div class="card"><h2>What will appear here</h2><div class="stack" style="gap:10px">
          <div class="row" style="align-items:flex-start;flex-wrap:nowrap"><span class="pill acc">1</span><div><b>A ranking of every asset</b><div class="muted">Stocks worldwide, ETFs, bonds, futures, crypto and currencies, sorted by score, filterable by class.</div></div></div>
          <div class="row" style="align-items:flex-start;flex-wrap:nowrap"><span class="pill acc">2</span><div><b>The score on each asset page</b><div class="muted">Next to the statistics it is built from, so a score can always be traced back to its inputs.</div></div></div>
          <div class="row" style="align-items:flex-start;flex-wrap:nowrap"><span class="pill acc">3</span><div><b>A column on the scoreboard</b><div class="muted">Sort the whole market by it alongside Sharpe, volatility, beta and the rest.</div></div></div>
          <div class="row" style="align-items:flex-start;flex-wrap:nowrap"><span class="pill acc">4</span><div><b>Your holdings, scored</b><div class="muted">How the fund's positions rank, once the score is live.</div></div></div></div></div>
        <div class="card"><h2>How it plugs in</h2><p class="muted" style="margin-top:0">The score is one Python function. FinSim2 calls it for every asset with that asset's statistics and details, and shows what it returns (higher is better; ${lo} to ${hi} is the scale the bars use).</p>
          <pre style="background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:12px;overflow:auto;font-size:12px;line-height:1.5"><code style="background:none;padding:0">VERSION = "1.0"

def score(metrics, asset):
    # metrics: the scoreboard statistics (sharpe, vol_ann,
    #   garch_vol, beta, alpha_ann, half_life, adf_t, ...)
    # asset: id, name, asset_class, currency, last, ...
    return 50.0   # a float, or None to leave it unscored</code></pre>
          <p class="muted">Put it in <code>finsim2/shaffer_score.py</code>, or drop a file at <code>${esc(st.override_path)}</code> and it is picked up without touching the code. Now reading: <code>${esc(st.source)}</code>.</p></div>
      </div>
      <div class="card" style="margin-top:16px"><h2>Inputs it will receive <small>every statistic on the scoreboard, per asset</small></h2><div id="shIn"></div></div>`}`;
    if (st.enabled && sb) {
      table($('#shT'), ranked, [{ k: 'rank', label: '#', v: r => ranked.indexOf(r), f: r => ranked.indexOf(r) + 1, l: 1 }, { k: 'id', label: 'Asset', l: 1, f: r => `<b>${esc(r.id)}</b><span class="sub">${esc(r.name)}</span>` }, { k: 'class_label', label: 'Class', l: 1, f: r => `<span class="pill">${esc(r.class_label)}</span>` }, { k: 'shaffer', label: 'Score', f: r => `<b>${fmt.num(r.shaffer, 1)}</b><span class="cell-bar bar"><i style="width:${Math.max(0, Math.min(100, (r.shaffer - lo) / ((hi - lo) || 1) * 100))}%"></i></span>` }, { k: 'held', label: 'Held', v: r => (S.dash.positions || []).some(p => p.security_id === r.id) ? 1 : 0, f: r => (S.dash.positions || []).some(p => p.security_id === r.id) ? '<span class="pill pos">held</span>' : '' }], { sortKey: 'shaffer', maxH: 760, onRow: r => { location.hash = '#/asset/' + encodeURIComponent(r.id); } });
      const bins = new Array(10).fill(0); ranked.forEach(r => { bins[Math.max(0, Math.min(9, Math.floor((r.shaffer - lo) / ((hi - lo) || 1) * 10)))]++; });
      $('#shD').innerHTML = `<div style="display:flex;align-items:flex-end;gap:4px;height:180px">${bins.map((b, i) => `<div title="${b}" style="flex:1;background:var(--accent);opacity:.8;border-radius:4px 4px 0 0;height:${b / (Math.max(...bins) || 1) * 100}%"></div>`).join('')}</div><div class="row" style="justify-content:space-between" class="muted"><span class="muted">${lo}</span><span class="muted">${hi}</span></div>`;
    } else {
      const mi = (sb && sb.metric_info) || (await api(`/fs2/worlds/${S.wid}/analytics/SPY`).catch(() => ({ metric_info: {} }))).metric_info || {};
      if (!alive()) return;
      $('#shIn').innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:6px 18px;font-size:13px">${Object.entries(mi).map(([k, v]) => `<div class="row" style="justify-content:space-between;flex-wrap:nowrap;border-bottom:1px solid var(--border);padding:5px 0"><span>${esc(v.label)}</span><code>${esc(k)}</code></div>`).join('')}</div>`;
    }
  };

  // ---------------------------------------------------------------- settings
  pages.settings = async (main, _, alive) => {
    const w = S.world;
    main.innerHTML = `<div class="page-head"><div><h1>Settings</h1><p>Your funds, the clock and the display.</p></div><button class="primary" id="newFund">New fund</button></div>
      <div class="grid g2"><div class="card"><h2>This fund</h2><div class="kv"><span>Name</span><span>${esc(w.name)}</span><span>Save</span><span>${esc(w.id)}</span><span>Market</span><span>${w.market_source === 'REAL' ? 'real market' : 'simulated'}</span><span>Clock</span><span>${isLive() ? `live, updates ${esc(w.clock.update_time)} ${esc(w.clock.timezone)}` : 'practice: you advance it'}</span><span>Date</span><span>${fmt.date(w.current_date)} (${esc(w.clock.weekday)})</span><span>Engine</span><span>${esc(w.engine_version)} · save format ${esc(w.save_version)}</span><span>Integrity</span><span>${w.integrity && w.integrity.ok === false ? '<span class="neg">problems found</span>' : '<span class="pos">ok</span>'}</span></div>
        ${!isLive() ? `<div class="row" style="margin-top:14px"><button id="adv5">Advance 5 days</button><button id="adv21">Advance a month</button></div>` : ''}</div>
      <div class="card"><h2>All funds</h2>${S.worlds.map(x => `<div class="row" style="padding:8px 0;border-top:1px solid var(--border)"><b>${esc(x.name)}</b><span class="muted">${esc(x.id)}</span><span class="spacer"></span>${x.id === S.wid ? '<span class="pill acc">open</span>' : `<button class="small" data-open="${x.id}">Open</button>`}<button class="small ghost" data-del="${x.id}" data-name="${esc(x.name)}">Delete</button></div>`).join('')}</div></div>
      <div class="card" style="margin-top:16px"><h2>Display</h2><div class="seg" id="thSeg">${['system', 'dark', 'light'].map(t => `<button data-th="${t}" class="${pref.get('theme', 'system') === t ? 'on' : ''}">${t[0].toUpperCase() + t.slice(1)}</button>`).join('')}</div></div>`;
    $('#newFund').onclick = () => { location.hash = '#/welcome'; S.wid = null; route(); };
    $$('[data-open]').forEach(b => b.onclick = async () => { S.wid = b.dataset.open; pref.set('wid', S.wid); S.secs = null; await loadWorld(); renderShell(); location.hash = '#/overview'; });
    $$('[data-del]').forEach(b => b.onclick = () => { if (!confirm(`Delete the fund "${b.dataset.name}" and its whole history? This cannot be undone.`)) return; busy(b, async () => { await api('/worlds/' + b.dataset.del, { method: 'DELETE' }); await loadWorlds(); await loadWorld(); renderShell(); toast('Fund deleted'); route(); }); });
    [['adv5', 5], ['adv21', 21]].forEach(([id, n]) => { const b = $('#' + id); if (b) b.onclick = () => busy(b, async () => { await post(W() + '/advance', { days: n }); await loadWorld(); renderShell(); toast(`Now ${fmt.date(S.world.current_date)}`); route(); }); });
    $$('#thSeg button').forEach(b => b.onclick = () => { pref.set('theme', b.dataset.th); applyTheme(); $$('#thSeg button').forEach(x => x.classList.toggle('on', x === b)); });
  };

  // ---------------------------------------------------------------- search, theme, startup
  function applyTheme() { const t = pref.get('theme', 'system'); if (t === 'system') document.documentElement.removeAttribute('data-theme'); else document.documentElement.setAttribute('data-theme', t); }
  function bindSearch() {
    const q = $('#q'), dd = $('#qdd'); let hits = [], sel = 0;
    const close = () => { dd.style.display = 'none'; };
    const go = h => { q.value = ''; close(); q.blur(); location.hash = '#/asset/' + encodeURIComponent(h.id); };
    q.addEventListener('input', async () => { const v = q.value.trim().toUpperCase(); if (!v || !S.wid) { close(); return; } const secs = await securities();
      hits = secs.filter(s => s.id.toUpperCase().startsWith(v)).concat(secs.filter(s => !s.id.toUpperCase().startsWith(v) && String(s.name).toUpperCase().includes(v))).slice(0, 12); sel = 0;
      dd.innerHTML = hits.map((h, i) => `<div class="hit ${i === sel ? 'sel' : ''}" data-i="${i}"><b>${esc(h.id)}</b><span>${esc(h.name)} <small>${esc(clsName(h.asset_class))} · ${esc(h.currency)}</small></span><span class="num ${sign(h.change_pct)}">${fmt.px(h.last)}</span></div>`).join('') || '<div class="hit"><span class="muted">No match</span></div>';
      dd.style.display = 'block'; $$('.hit[data-i]', dd).forEach(el => el.onmousedown = () => go(hits[+el.dataset.i])); });
    q.addEventListener('keydown', e => { if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { sel = Math.max(0, Math.min(hits.length - 1, sel + (e.key === 'ArrowDown' ? 1 : -1))); $$('.hit', dd).forEach((el, i) => el.classList.toggle('sel', i === sel)); e.preventDefault(); } else if (e.key === 'Enter' && hits[sel]) go(hits[sel]); else if (e.key === 'Escape') { close(); q.blur(); } });
    q.addEventListener('blur', () => setTimeout(close, 150));
    document.addEventListener('keydown', e => { if (e.key === '/' && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); q.focus(); } if (e.key === 'Escape') closeDrawer(); });
  }
  async function boot() {
    applyTheme();
    $('#themeBtn').onclick = () => { const cur = document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'); pref.set('theme', cur === 'light' ? 'dark' : 'light'); applyTheme(); route(); };
    $('#menuBtn').onclick = () => $('#side').classList.toggle('open');
    bindSearch();
    try { await loadWorlds(); await loadWorld(); } catch (e) { toast(e.message, true); S.wid = null; }
    renderShell(); route();
    // live funds: refresh the header every minute (the server processes the day by itself after the close)
    setInterval(async () => { if (!S.wid || document.hidden) return; try { const before = S.world && S.world.current_date; await loadWorld(); renderShell(); if (S.world.current_date !== before) route(); } catch (e) { } }, 60000);
  }
  boot();
})();
