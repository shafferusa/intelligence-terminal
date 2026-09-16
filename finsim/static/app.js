/* finsim terminal UI — vanilla JS, hash routing, canvas charts. Every number on screen comes from the API,
   which reconstructs it from the event log; nothing is computed "for display" here except formatting. */
(() => {
  const $ = (sel, el = document) => el.querySelector(sel);
  const state = { worlds: [], world: null, worldId: localStorage.getItem('finsim.world'), pfId: localStorage.getItem('finsim.pf'), route: location.hash || '#/home', period: '3M' };

  // ---------------------------------------------------------------- helpers
  const fmt = {
    money: (v, d = 2) => (v == null || isNaN(v)) ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }),
    m0: v => fmt.money(v, 0), px: v => (v == null) ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 }),
    qty: v => (v == null) ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }),
    pct: (v, d = 2) => (v == null || isNaN(v)) ? '—' : (Number(v) * 100).toFixed(d) + '%',
    bps: v => (v == null) ? '—' : Number(v).toFixed(1) + 'bp',
    signed: (v, d = 2) => { if (v == null) return '—'; const n = Number(v); const s = fmt.money(Math.abs(n), d); return `<span class="${n > 0 ? 'pos' : n < 0 ? 'neg' : 'muted'}">${n > 0 ? '+' : n < 0 ? '−' : ''}${s}</span>`; },
    big: v => { const n = Number(v); if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(2) + 'B'; if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + 'M'; if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + 'K'; return fmt.money(n); },
  };
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const statusBadge = s => { const cls = { SETTLED: 'green', FILLED: 'green', FAILED: 'red', REJECTED: 'red', CANCELLED: 'red', EXPIRED: 'red', PENDING: 'amber', MATCHED: 'blue', WORKING: 'amber', PARTIALLY_FILLED: 'amber', SETTLEMENT_PENDING: 'blue', DECLARED: 'blue', EX: 'amber', PAID: 'green' }[s] || ''; return `<span class="badge ${cls}">${esc(s)}</span>`; };

  async function api(path, opts = {}) {
    const r = await fetch('/api' + path, { headers: { 'Content-Type': 'application/json' }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
    const j = await r.json().catch(() => ({ error: 'bad response' }));
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  }
  function toast(msg, err = false) { const t = document.createElement('div'); t.className = 'toast' + (err ? ' err' : ''); t.textContent = msg; $('#toastRoot').appendChild(t); setTimeout(() => t.remove(), err ? 7000 : 3500); }
  function modal(html) { const root = $('#modalRoot'); root.innerHTML = `<div class="modal-bg"><div class="modal">${html}</div></div>`; root.firstChild.addEventListener('click', e => { if (e.target === root.firstChild) root.innerHTML = ''; }); return root.firstChild; }
  const closeModal = () => { $('#modalRoot').innerHTML = ''; };
  const W = () => `/worlds/${state.worldId}`;
  const P = () => `${W()}/portfolios/${state.pfId}`;

  // ---------------------------------------------------------------- charts (canvas)
  function setupCanvas(c, h = 220) { const dpr = window.devicePixelRatio || 1; const w = c.clientWidth || 600; c.width = w * dpr; c.height = h * dpr; c.style.height = h + 'px'; const ctx = c.getContext('2d'); ctx.scale(dpr, dpr); return { ctx, w, h }; }
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  function axes(ctx, w, h, pad, ymin, ymax, fmtY, xlabels) {
    ctx.strokeStyle = css('--line'); ctx.fillStyle = css('--fg3'); ctx.font = '10px ' + css('--mono'); ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) { const y = pad.t + (h - pad.t - pad.b) * i / 4; ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke(); ctx.textAlign = 'right'; ctx.fillText(fmtY(ymax - (ymax - ymin) * i / 4), pad.l - 4, y + 3); }
    if (xlabels) { ctx.textAlign = 'center'; const n = xlabels.length; const step = Math.max(1, Math.floor(n / 6)); for (let i = 0; i < n; i += step) { const x = pad.l + (w - pad.l - pad.r) * (n === 1 ? 0.5 : i / (n - 1)); ctx.fillText(xlabels[i], x, h - 4); } }
  }
  function lineChart(c, series, { h = 220, fmtY = fmt.big, labels = null, zero = false } = {}) {
    const { ctx, w } = setupCanvas(c, h); const pad = { l: 64, r: 28, t: 10, b: 18 };
    const all = series.flatMap(s => s.data).filter(v => v != null && isFinite(v)); if (!all.length) return;
    let ymin = Math.min(...all), ymax = Math.max(...all); if (zero) { ymin = Math.min(0, ymin); ymax = Math.max(0, ymax); } if (ymin === ymax) { ymin -= 1; ymax += 1; } const r = (ymax - ymin) * 0.05; ymin -= r; ymax += r;
    axes(ctx, w, h, pad, ymin, ymax, fmtY, labels);
    const n = Math.max(...series.map(s => s.data.length));
    const X = i => pad.l + (w - pad.l - pad.r) * (n === 1 ? 0.5 : i / (n - 1)), Y = v => pad.t + (h - pad.t - pad.b) * (1 - (v - ymin) / (ymax - ymin));
    if (zero && ymin < 0 && ymax > 0) { ctx.strokeStyle = css('--fg3'); ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(pad.l, Y(0)); ctx.lineTo(w - pad.r, Y(0)); ctx.stroke(); ctx.setLineDash([]); }
    series.forEach(s => { ctx.strokeStyle = s.color || css('--accent'); ctx.lineWidth = s.width || 1.5; ctx.beginPath(); let started = false; s.data.forEach((v, i) => { if (v == null) return; if (!started) { ctx.moveTo(X(i), Y(v)); started = true; } else ctx.lineTo(X(i), Y(v)); }); ctx.stroke(); if (s.dots) s.data.forEach((v, i) => { ctx.fillStyle = s.color; ctx.beginPath(); ctx.arc(X(i), Y(v), 2.5, 0, 7); ctx.fill(); }); });
  }
  function candleChart(c, bars, { h = 300 } = {}) {
    const { ctx, w } = setupCanvas(c, h); const pad = { l: 64, r: 28, t: 10, b: 18 }; const vh = 50;
    if (!bars.length) return;
    const highs = bars.map(b => b[2]), lows = bars.map(b => b[3]); let ymin = Math.min(...lows), ymax = Math.max(...highs); const r = (ymax - ymin) * 0.05 || 1; ymin -= r; ymax += r;
    const ph = h - vh; axes(ctx, w, ph, pad, ymin, ymax, fmt.px, bars.map(b => b[0].slice(5)));
    const n = bars.length, cw = Math.max(1, (w - pad.l - pad.r) / n * 0.7); const X = i => pad.l + (w - pad.l - pad.r) * (i + 0.5) / n, Y = v => pad.t + (ph - pad.t - pad.b) * (1 - (v - ymin) / (ymax - ymin));
    const vmax = Math.max(...bars.map(b => b[5]));
    bars.forEach((b, i) => { const up = b[4] >= b[1]; ctx.strokeStyle = ctx.fillStyle = up ? css('--green') : css('--red'); ctx.beginPath(); ctx.moveTo(X(i), Y(b[2])); ctx.lineTo(X(i), Y(b[3])); ctx.stroke(); const y1 = Y(Math.max(b[1], b[4])), y2 = Y(Math.min(b[1], b[4])); ctx.fillRect(X(i) - cw / 2, y1, cw, Math.max(1, y2 - y1)); ctx.globalAlpha = 0.5; const vhh = (b[5] / vmax) * (vh - 6); ctx.fillRect(X(i) - cw / 2, h - 4 - vhh, cw, vhh); ctx.globalAlpha = 1; });
  }
  function barChart(c, items, { h = 200 } = {}) {
    const { ctx, w } = setupCanvas(c, h); const pad = { l: 64, r: 10, t: 10, b: 40 }; const vals = items.map(i => i.value); let ymin = Math.min(0, ...vals), ymax = Math.max(0, ...vals); if (ymin === ymax) ymax = ymin + 1; const r = (ymax - ymin) * 0.05; ymin -= r; ymax += r;
    axes(ctx, w, h, pad, ymin, ymax, fmt.big); const n = items.length, bw = (w - pad.l - pad.r) / n; const Y = v => pad.t + (h - pad.t - pad.b) * (1 - (v - ymin) / (ymax - ymin));
    items.forEach((it, i) => { ctx.fillStyle = it.value >= 0 ? css('--green') : css('--red'); const x = pad.l + bw * i + bw * 0.15; ctx.fillRect(x, Math.min(Y(0), Y(it.value)), bw * 0.7, Math.abs(Y(0) - Y(it.value))); ctx.fillStyle = css('--fg2'); ctx.font = '10px ' + css('--mono'); ctx.textAlign = 'center'; ctx.fillText(it.label, x + bw * 0.35, h - 26); ctx.fillText(fmt.big(it.value), x + bw * 0.35, h - 14); });
  }

  // ---------------------------------------------------------------- shell
  const NAV = [['#/home', 'DAILY BRIEFING'], ['#/portfolio', 'PORTFOLIO'], ['#/trading', 'TRADING'], ['#/markets', 'MARKETS'], ['#/commodities', 'COMMODITIES'], ['#/fixed-income', 'FIXED INCOME'], ['#/settlements', 'SETTLEMENTS'], ['#/treasury', 'TREASURY'], ['#/news', 'NEWS'], ['#/accounting', 'ACCOUNTING'], ['#/career', 'CAREER'], ['#/audit', 'AUDIT TRAIL']];
  const TODO = ['OPTIONS & OTC (Phase 3/4)', 'SEC LENDING (Phase 2)', 'REPO (Phase 2)', 'COLLATERAL / CSA (Phase 4)', 'RISK (VaR, stress) (Phase 5)', 'COUNTERPARTIES (Phase 4)', 'FX (Phase 2)'];
  function renderNav() { $('#nav').innerHTML = `<div class="section">Modules</div>` + NAV.map(([h, l]) => `<a href="${h}" class="${state.route.startsWith(h) || (h === '#/markets' && state.route.startsWith('#/security')) || (h === '#/commodities' && state.route.startsWith('#/commodity/')) || (h === '#/portfolio' && state.route.startsWith('#/position')) ? 'active' : ''}">${l}</a>`).join('') + `<div class="section">Not yet built</div>` + TODO.map(t => `<div class="todo">${t}</div>`).join(''); }
  async function loadWorlds() {
    state.worlds = await api('/worlds');
    const sel = $('#worldSel'); sel.innerHTML = state.worlds.map(w => `<option value="${w.id}">${esc(w.name)} (${w.id})</option>`).join('') || '<option value="">no worlds</option>';
    if (!state.worlds.find(w => w.id === state.worldId)) state.worldId = state.worlds[0]?.id || null;
    sel.value = state.worldId || '';
    if (state.worldId) await loadWorld(); else { $('#main').innerHTML = `<h1>Welcome</h1><p>Create a world to begin.</p>`; openNewWorld(); }
  }
  async function loadWorld() {
    localStorage.setItem('finsim.world', state.worldId);
    state.world = await api(W());
    const pfs = state.world.portfolios; $('#pfSel').innerHTML = pfs.map(p => `<option value="${p.id}">${esc(p.name)} · ${p.type}</option>`).join('');
    if (!pfs.find(p => p.id === state.pfId)) state.pfId = pfs[0]?.id; $('#pfSel').value = state.pfId; localStorage.setItem('finsim.pf', state.pfId);
    $('#simDate').textContent = `${state.world.clock.weekday.slice(0, 3)} ${state.world.current_date}`; const rg = state.world.regime; $('#regimeBadge').textContent = rg.label; $('#regimeBadge').title = rg.description;
    const rt = state.world.clock.mode === 'REAL_TIME'; document.querySelectorAll('[data-adv]').forEach(b => b.style.display = rt ? 'none' : '');
    const nu = $('#nextUpdate'); if (rt) { const d = new Date(state.world.clock.next_update); nu.style.display = ''; nu.innerHTML = `<span class="badge blue">CAREER</span> next update ${d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: state.world.clock.timezone, timeZoneName: 'short' })}`; nu.title = `The market processes one business day at ${state.world.clock.update_time} ${state.world.clock.timezone}, whether or not you log in.`; } else { nu.style.display = ''; nu.innerHTML = '<span class="badge">SANDBOX</span>'; nu.title = 'Manual time: advance days yourself.'; }
    $('#regimeBadge').className = 'badge ' + ({ NORMAL_GROWTH: 'green', RATE_CUTTING: 'blue', RATE_HIKING: 'amber', RECESSION: 'red', LIQUIDITY_STRESS: 'red' }[rg.name] || '');
    render();
  }
  async function advance(days) {
    const btns = document.querySelectorAll('[data-adv]'); btns.forEach(b => b.disabled = true);
    try { const r = await api(W() + '/advance', { method: 'POST', body: { days } }); toast(`Closed ${r.closed_days.length} day(s) → now ${r.current_date}`); await loadWorld(); }
    catch (e) { toast(e.message, true); } finally { btns.forEach(b => b.disabled = false); }
  }
  document.querySelectorAll('[data-adv]').forEach(b => b.addEventListener('click', () => advance(+b.dataset.adv)));
  $('#worldSel').addEventListener('change', e => { state.worldId = e.target.value; state.pfId = null; loadWorld(); });
  $('#pfSel').addEventListener('change', e => { state.pfId = e.target.value; localStorage.setItem('finsim.pf', state.pfId); render(); });
  $('#newWorldBtn').addEventListener('click', openNewWorld);
  $('#newPfBtn').addEventListener('click', openNewPortfolio);
  window.addEventListener('hashchange', () => { state.route = location.hash || '#/home'; render(); });

  async function openNewWorld() {
    const jobs = await api('/jobs');
    const m = modal(`<h1>New save <a onclick="document.getElementById('modalRoot').innerHTML=''">✕</a></h1>
      <p class="hint">A save is a job. Career saves run on real time: one calendar day is one simulated business day, processed at your update time whether or not you log in. Sandbox saves advance when you say so.</p>
      <div class="form">
        <label>Job</label><select id="nwJob">${jobs.map(j => `<option value="${j.key}" ${j.status !== 'PLAYABLE' ? 'disabled' : ''} ${j.key === 'PORTFOLIO_MANAGER' ? 'selected' : ''}>${esc(j.title)}${j.status !== 'PLAYABLE' ? ' — ' + esc(j.status) : ` — ${fmt.big(j.capital)}`}</option>`).join('')}</select>
        <label></label><div id="nwJobDesc" class="hint" style="margin:0"></div>
        <label>Save name</label><input id="nwName" value="My book">
        <label>Clock</label><select id="nwClock"><option value="REAL_TIME">Career — real time, one business day per real day</option><option value="SANDBOX">Sandbox — advance manually</option></select>
        <label>Timezone</label><select id="nwTz">${['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Zurich', 'Asia/Tokyo', 'Asia/Singapore', 'Australia/Sydney'].map(t => `<option>${t}</option>`).join('')}</select>
        <label>Update time</label><input id="nwTime" type="time" value="09:00">
        <label>Seed</label><input id="nwSeed" type="number" value="${Math.floor(Math.random() * 100000)}">
        <label id="nwStartL">Start date</label><input id="nwStart" type="date" value="${new Date().toISOString().slice(0, 10)}">
        <label id="nwCapL">Capital</label><select id="nwCap"><option value="100000">$100,000</option><option value="1000000">$1 million</option><option value="10000000" selected>$10 million</option><option value="100000000">$100 million</option><option value="1000000000">$1 billion</option></select>
        <label>Realism</label><select id="nwReal"><option>PROFESSIONAL</option><option>INTERMEDIATE</option><option>BEGINNER</option></select>
        <label>Initial regime</label><select id="nwReg"><option value="NORMAL_GROWTH">Normal growth</option><option value="RATE_HIKING">Rate-hiking cycle</option><option value="RECESSION">Recession</option><option value="LIQUIDITY_STRESS">Liquidity crisis</option><option value="RATE_CUTTING">Rate-cutting cycle</option></select>
      </div>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="nwGo">Create save</button><span id="nwErr" class="error"></span></div>`);
    const sync = () => { const j = jobs.find(x => x.key === $('#nwJob', m).value); $('#nwJobDesc', m).innerHTML = j ? `${esc(j.description)}<br>Capital ${fmt.big(j.capital)} · benchmark ${j.benchmark || 'absolute return'} · max leverage ${j.max_gross_leverage}x · max position ${fmt.pct(j.max_position_pct, 0)} · max drawdown ${fmt.pct(j.max_drawdown, 0)}<br>Ladder: ${j.ladder.join(' → ')}` : '';
      const sandbox = $('#nwClock', m).value === 'SANDBOX'; $('#nwStart', m).style.display = $('#nwStartL', m).style.display = sandbox ? '' : 'none'; const sb = $('#nwJob', m).value === 'SANDBOX'; $('#nwCap', m).style.display = $('#nwCapL', m).style.display = sb ? '' : 'none'; };
    $('#nwJob', m).addEventListener('change', sync); $('#nwClock', m).addEventListener('change', sync); sync();
    $('#nwGo', m).addEventListener('click', async () => {
      try { $('#nwGo', m).disabled = true; const r = await api('/worlds', { method: 'POST', body: { name: $('#nwName', m).value, seed: +$('#nwSeed', m).value, start_date: $('#nwStart', m).value, capital: +$('#nwCap', m).value, portfolio_name: $('#nwName', m).value, job: $('#nwJob', m).value, clock_mode: $('#nwClock', m).value, timezone: $('#nwTz', m).value, update_time: $('#nwTime', m).value, realism: $('#nwReal', m).value, initial_regime: $('#nwReg', m).value } }); state.worldId = r.world_id; state.pfId = r.portfolio_id; closeModal(); location.hash = '#/home'; await loadWorlds(); }
      catch (e) { $('#nwErr', m).textContent = e.message; $('#nwGo', m).disabled = false; }
    });
  }
  function openNewPortfolio() {
    const m = modal(`<h1>New book in this world <a onclick="document.getElementById('modalRoot').innerHTML=''">✕</a></h1><div class="form">
      <label>Name</label><input id="npName" value="Book II"><label>Job</label><select id="npJob"><option value="SANDBOX">Sandbox (any capital, no limits)</option><option value="PORTFOLIO_MANAGER">Portfolio Manager</option><option value="GLOBAL_MACRO">Global Macro Trader</option><option value="COMMODITY_TRADER">Commodity Trader</option><option value="FIXED_INCOME_PM">Fixed-Income PM</option></select>
      <label>Capital (sandbox)</label><input id="npCap" type="number" value="10000000"><label>Realism</label><select id="npReal"><option>PROFESSIONAL</option><option>INTERMEDIATE</option><option>BEGINNER</option></select></div>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="npGo">Create</button><span class="error" id="npErr"></span></div>`);
    $('#npGo', m).addEventListener('click', async () => { try { const r = await api(W() + '/portfolios', { method: 'POST', body: { name: $('#npName', m).value, job: $('#npJob', m).value, capital: +$('#npCap', m).value, realism: $('#npReal', m).value } }); state.pfId = r.portfolio_id; closeModal(); await loadWorld(); } catch (e) { $('#npErr', m).textContent = e.message; } });
  }

  // ---------------------------------------------------------------- pages
  const pages = {};
  async function render() {
    renderNav(); if (!state.worldId || !state.pfId) return;
    const [_, page, arg] = state.route.split('/'); const fn = pages[page] || pages.home; $('#main').innerHTML = '<p class="muted mono">loading…</p>';
    try { await fn(arg); } catch (e) { $('#main').innerHTML = `<p class="error">${esc(e.message)}</p>`; console.error(e); }
  }
  const tile = (k, v, s = '', click = '') => `<div class="tile ${click ? 'click' : ''}" ${click ? `data-click="${click}"` : ''}><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
  const realism = () => (state.world.portfolios.find(p => p.id === state.pfId) || {}).realism || 'PROFESSIONAL';

  const SEV = { HIGH: 'red', MEDIUM: 'amber', INFO: 'blue' };
  const BUCKET_LABEL = { equities: 'Equities', commodities: 'Commodities', rates: 'Rates', credit: 'Credit', dividends: 'Dividends', bond_interest: 'Bond interest', financing: 'Financing', fees: 'Fees' };
  pages.home = async () => {
    const b = await api(P() + '/briefing');
    if (!b.available) { $('#main').innerHTML = '<h1>DAILY BRIEFING</h1><p class="muted mono">No processed day yet.</p>'; return; }
    const rt = b.clock_mode === 'REAL_TIME'; const nu = b.next_update ? new Date(b.next_update) : null;
    const mk = m => m.fmt === 'pct' ? `<span class="${m.value >= 0 ? 'pos' : 'neg'}">${m.value >= 0 ? '+' : ''}${(m.value * 100).toFixed(2)}%</span>` : `<span class="${m.value >= 0 ? 'neg' : 'pos'}">${m.value >= 0 ? '+' : ''}${m.value.toFixed(1)}bp</span>`;
    const buckets = Object.entries(b.pnl_buckets);
    $('#main').innerHTML = `<h1>DAILY BRIEFING <small>${b.weekday.toUpperCase()} — ${b.date} · ${esc(b.job)} · ${esc(b.level_title)} · regime: ${esc(b.regime)}</small></h1>
      <p class="hint">${rt ? `Markets have updated for ${b.date}. Review the day, then enter instructions: they execute at the next update, ${nu ? nu.toLocaleString(undefined, { weekday: 'long', hour: '2-digit', minute: '2-digit', timeZone: state.world.clock.timezone, timeZoneName: 'short' }) : ''}.` : 'Sandbox: instructions you enter now execute when you advance the day.'}
        ${b.dates.length > 1 ? `· earlier briefings: ${b.dates.slice(-8, -1).reverse().map(d => `<a data-brief="${d}">${d}</a>`).join(' ')}` : ''}</p>
      <div class="grid3">
        <div>
          <div class="tiles">${tile('Portfolio NAV', fmt.money(b.nav), b.live_nav !== b.nav ? `live ${fmt.money(b.live_nav)}` : `${b.positions_count} position(s)`)}${tile('Today', fmt.signed(b.day_pnl), `<span class="${b.day_return >= 0 ? 'pos' : 'neg'}">${fmt.pct(b.day_return)}</span>`)}${buckets.filter(([k, v]) => Math.abs(v) > 0.005).slice(0, 6).map(([k, v]) => tile(BUCKET_LABEL[k] || k, fmt.signed(v))).join('')}</div>
          <h2>Major P&L</h2><div class="panel"><table>${buckets.map(([k, v]) => `<tr><td class="l">${BUCKET_LABEL[k] || k}</td><td>${fmt.signed(v)}</td></tr>`).join('')}<tr><th class="l">Total</th><th>${fmt.signed(b.day_pnl)}</th></tr></table>
            ${b.movers.gainers.length || b.movers.losers.length ? `<h2>Position movers</h2><table>${b.movers.gainers.map(m => `<tr class="click" data-href="#/position/${m.security_id}"><td class="l">${m.security_id}</td><td>${fmt.signed(m.pnl)}</td></tr>`).join('')}${b.movers.losers.map(m => `<tr class="click" data-href="#/position/${m.security_id}"><td class="l">${m.security_id}</td><td>${fmt.signed(m.pnl)}</td></tr>`).join('')}</table>` : ''}</div>
          <h2>What happened today</h2><div class="panel"><table>${b.market.map(m => `<tr><td class="l">${esc(m.label)}</td><td>${mk(m)}</td><td class="muted">${m.extra || ''}</td></tr>`).join('')}</table></div>
          <h2>News</h2><div class="panel">${b.news.length ? b.news.map(n => `<div style="margin-bottom:8px"><span class="badge">${n.category}</span> <b>${esc(n.headline)}</b> ${n.refs.filter(r => !r.startsWith('DIV-')).map(r => r.length <= 4 && !state.securities?.[r] ? `<a href="#/commodity/${r}">${r}</a>` : `<a href="#/security/${r}">${r}</a>`).join(' ')}<div class="muted">${esc(n.body)}</div></div>`).join('') : '<span class="muted mono">quiet day</span>'}</div>
        </div>
        <div>
          <h2 style="margin-top:0">Requires your attention</h2><div class="panel">${b.attention.length ? b.attention.map(a => `<div style="margin-bottom:6px"><span class="badge ${SEV[a.severity]}">${a.severity}</span> ${a.link ? `<a href="${a.link}">${esc(a.text)}</a>` : esc(a.text)}</div>`).join('') : '<span class="muted mono">nothing outstanding</span>'}</div>
          <h2>Your instructions</h2><div class="panel">
            ${b.orders_summary.filled_today.length ? `<div class="hint">Filled today</div><table>${b.orders_summary.filled_today.map(f => `<tr class="click" data-trade="${f.trade_id}"><td class="l ${f.side === 'BUY' ? 'pos' : 'neg'}">${f.side}</td><td>${fmt.qty(f.quantity)}</td><td class="l">${f.security_id}</td><td>${fmt.px(f.price)}</td><td class="l muted">${f.forced ? '<span class="badge red">FORCED</span>' : esc(f.note)}</td></tr>`).join('')}</table>` : ''}
            ${b.orders_summary.working.length ? `<div class="hint">Working for the next session</div><table>${b.orders_summary.working.map(o => `<tr><td class="l ${o.side === 'BUY' ? 'pos' : 'neg'}">${o.side}</td><td>${fmt.qty(o.quantity)}</td><td class="l">${o.security_id}</td><td class="l">${o.type}</td><td class="l muted">${esc(o.note)}</td></tr>`).join('')}</table>` : ''}
            ${b.orders_summary.expired_today.length ? `<div class="hint">Expired unfilled</div><table>${b.orders_summary.expired_today.map(o => `<tr><td class="l">${o.side}</td><td>${fmt.qty(o.quantity)}</td><td class="l">${o.security_id}</td></tr>`).join('')}</table>` : ''}
            ${!b.orders_summary.filled_today.length && !b.orders_summary.working.length && !b.orders_summary.expired_today.length ? '<span class="muted mono">no instructions</span>' : ''}
            <div class="row" style="margin-top:10px"><a class="btn" href="#/trading">Enter instructions →</a><a class="btn" href="#/portfolio">Portfolio →</a><a class="btn" href="#/commodities">Commodities →</a></div></div>
        </div></div>`;
    document.querySelectorAll('[data-brief]').forEach(a => a.addEventListener('click', async () => { const x = await api(P() + '/briefing?date=' + a.dataset.brief); modal(`<h1>Briefing ${x.date} <a onclick="document.getElementById('modalRoot').innerHTML=''">✕</a></h1><div class="kv"><div>NAV</div><div>${fmt.money(x.nav)}</div><div>Day P&L</div><div>${fmt.signed(x.day_pnl)}</div></div><h2>Attention</h2>${x.attention.map(i => `<div><span class="badge ${SEV[i.severity]}">${i.severity}</span> ${esc(i.text)}</div>`).join('') || '<span class="muted">none</span>'}<h2>Market</h2><table>${x.market.map(m => `<tr><td class="l">${esc(m.label)}</td><td>${mk(m)}</td></tr>`).join('')}</table>`); }));
    bindRows();
  };
  pages.dashboard = async () => {
    const d = await api(P() + '/dashboard'); const pro = realism() !== 'BEGINNER'; const cashUSD = d.cash.USD || 0;
    const nextSettle = d.upcoming_settlements[0]; const nextCf = d.upcoming_cash_flows[0];
    $('#main').innerHTML = `<h1>${esc(d.portfolio.name)} <small>${d.portfolio.type} · ${d.portfolio.realism} · ${d.portfolio.mode} · custody ${d.portfolio.custody_account}</small></h1>
      <div class="tiles">
        ${tile('NAV', fmt.money(d.nav), 'click to explain', 'nav')}
        ${tile('Day P&L', fmt.signed(d.day_pnl), `MTD ${fmt.signed(d.mtd_pnl)} · YTD ${fmt.signed(d.ytd_pnl)}`, 'pnl')}
        ${tile('Cash (settled USD)', fmt.money(cashUSD), `projected ${fmt.money(d.projected_cash.USD)}`, 'treasury')}
        ${tile('Gross exposure', fmt.money(d.gross_exposure), `long ${fmt.big(d.long_exposure)} · short ${fmt.big(d.short_exposure)}`)}
        ${tile('Net exposure', fmt.money(d.net_exposure), `${fmt.pct(d.net_exposure / d.nav)} of NAV`)}
        ${tile('Leverage', Number(d.leverage).toFixed(2) + 'x', 'gross / NAV')}
        ${pro ? tile('Margin used', fmt.money(d.margin_used), 'initial margin at clearing member') : ''}
        ${tile('Available liquidity', fmt.money(d.available_liquidity), 'settled cash + receivables − payables − working buys')}
        ${pro ? tile('Collateral posted', fmt.money(d.collateral_posted), 'futures initial margin') : ''}
        ${pro ? tile('Collateral received', fmt.money(d.collateral_received), 'none (OTC: Phase 4)') : ''}
        ${tile('Largest risk', d.largest_risk ? d.largest_risk.security_id : '—', d.largest_risk ? `${fmt.big(d.largest_risk.market_value)} · ${fmt.pct(d.largest_risk.weight)} of NAV · β ${d.largest_risk.beta}` : 'no positions')}
        ${tile('Upcoming settlements', d.upcoming_settlements.length, nextSettle ? `${nextSettle.settlement_date} ${nextSettle.instruction_type} ${nextSettle.security_id} ${fmt.big(nextSettle.cash_amount)}` : 'none', 'settlements')}
        ${tile('Upcoming cash flows', d.upcoming_cash_flows.length, nextCf ? `${nextCf.date} ${nextCf.kind} ${fmt.signed(nextCf.amount, 0)}` : 'none', 'treasury')}
        ${tile('Margin calls', d.margin_calls.length, d.margin_calls.length ? `<span class="neg">${fmt.money(d.margin_calls[0].amount)} due</span>` : 'none outstanding')}
        ${d.benchmark ? tile('vs benchmark', fmt.pct(d.return_since_inception - d.benchmark.return), `fund ${fmt.pct(d.return_since_inception)} · ${d.benchmark.id} ${fmt.pct(d.benchmark.return)}`) : ''}
      </div>
      <div class="grid3" style="margin-top:14px">
        <div class="panel"><h2 style="margin-top:0">NAV history</h2><canvas id="navChart"></canvas></div>
        <div class="panel"><h2 style="margin-top:0">Income statement (inception to date)</h2><div class="kv">
          <div>Realized P&L</div><div>${fmt.signed(d.realized)}</div><div>Unrealized P&L</div><div>${fmt.signed(d.unrealized)}</div><div>Dividend income</div><div>${fmt.signed(d.income.dividends)}</div>
          <div>Interest income</div><div>${fmt.signed(d.income.interest)}</div><div>Commissions</div><div>${fmt.signed(-d.income.commissions)}</div><div>Interest expense</div><div>${fmt.signed(-d.income.interest_expense)}</div>
          <div>Receivables</div><div>${fmt.money(d.receivables)}</div><div>Payables</div><div>${fmt.money(d.payables)}</div><div>Ledger NAV</div><div>${fmt.money(d.ledger_nav)} ${Math.abs(d.ledger_nav - d.nav) < 0.005 ? '<span class="ok">✓ reconciles</span>' : '<span class="error">✗</span>'}</div></div></div>
      </div>
      <div class="grid2" style="margin-top:14px">
        <div class="panel"><h2 style="margin-top:0">Positions</h2>${positionsTable(d.positions, true)}</div>
        <div class="panel"><h2 style="margin-top:0">Upcoming settlements & cash flows</h2>
          <table><tr><th>Date</th><th>Kind</th><th>Ref</th><th>Amount</th></tr>${d.upcoming_cash_flows.map(c => `<tr><td>${c.date}</td><td class="l">${c.kind}</td><td class="l">${esc(c.ref)}</td><td>${fmt.signed(c.amount)}</td></tr>`).join('') || '<tr><td colspan=4 class="muted">none</td></tr>'}</table>
          ${d.working_orders.length ? `<h2>Working orders</h2>${ordersTable(d.working_orders)}` : ''}</div>
      </div>`;
    lineChart($('#navChart'), [{ data: d.nav_history.map(x => x.nav), color: css('--accent') }], { labels: d.nav_history.map(x => x.date.slice(5)), fmtY: fmt.big });
    document.querySelectorAll('[data-click]').forEach(el => el.addEventListener('click', () => { const c = el.dataset.click; if (c === 'nav') navExplain(); else if (c === 'pnl') location.hash = '#/portfolio'; else if (c === 'treasury') location.hash = '#/treasury'; else if (c === 'settlements') location.hash = '#/settlements'; }));
    bindRows();
  };
  async function navExplain() {
    const x = await api(P() + '/nav-explain');
    modal(`<h1>NAV explain <a onclick="document.getElementById('modalRoot').innerHTML=''">✕</a></h1>
      <div class="kv"><div>Current NAV</div><div>${fmt.money(x.nav)}</div><div>Previous snapshot NAV (${x.since || 'inception'})</div><div>${fmt.money(x.prev_nav)}</div><div>Capital flows since</div><div>${fmt.signed(x.capital_flows)}</div><div>Change to explain</div><div>${fmt.signed(x.nav - x.prev_nav - x.capital_flows)}</div></div>
      <h2>Composition</h2><div class="kv"><div>Cash</div><div>${Object.entries(x.composition.cash).map(([c, v]) => `${c} ${fmt.money(v)}`).join(', ')}</div><div>Securities at market</div><div>${fmt.money(x.composition.market_value)}</div><div>Receivables & accruals</div><div>${fmt.money(x.composition.receivables)}</div><div>Payables</div><div>(${fmt.money(x.composition.payables)})</div></div>
      <h2>P&L accounts moved since last snapshot</h2><table><tr><th>Account</th><th>Name</th><th>Change</th></tr>${x.components.map(r => `<tr><td>${r.account}</td><td class="l">${r.name}</td><td>${fmt.signed(r.change)}</td></tr>`).join('')}<tr><th></th><th>Total</th><th>${fmt.signed(x.components.reduce((a, r) => a + r.change, 0))}</th></tr></table>
      <h2>Journal entries behind the change</h2><div class="scroll">${entriesTable(x.entries)}</div>`);
    bindRows();
  }
  function positionsTable(rows, compact = false) {
    if (!rows.length) return '<p class="muted mono">no positions — go to TRADING to enter instructions</p>';
    const cash = rows.filter(p => !p.is_future), fut = rows.filter(p => p.is_future);
    let html = '';
    if (cash.length) html += `<div class="scroll"><table><tr><th>Security</th><th>Qty</th><th>Settled</th>${compact ? '' : '<th>Pend rcv</th><th>Pend dlv</th>'}<th>Avg cost</th><th>Mark</th><th>Mkt value</th><th>Day P&L</th><th>Unreal</th><th>Realized</th>${compact ? '' : '<th>Div/Int</th><th>Comm</th><th>Weight</th><th>β</th><th>DV01</th>'}</tr>
      ${cash.map(p => `<tr class="click" data-href="#/position/${p.security_id}"><td><b>${p.security_id}</b> <span class="muted">${esc(p.name)}</span></td><td>${fmt.qty(p.quantity)}</td><td>${fmt.qty(p.settled_quantity)}</td>${compact ? '' : `<td>${fmt.qty(p.pending_receive)}</td><td>${fmt.qty(p.pending_deliver)}</td>`}<td>${fmt.px(p.average_cost)}</td><td>${fmt.px(p.mark)}</td><td>${fmt.money(p.market_value)}</td><td>${fmt.signed(p.day_pnl)}</td><td>${fmt.signed(p.unrealized_pnl)}</td><td>${fmt.signed(p.realized_pnl)}</td>${compact ? '' : `<td>${fmt.signed(p.dividend_income + p.interest_income)}</td><td>${fmt.money(p.commissions)}</td><td>${fmt.pct(p.weight, 1)}</td><td>${p.beta}</td><td>${p.risk.position_dv01 != null ? fmt.money(p.risk.position_dv01) : '—'}</td>`}</tr>`).join('')}</table></div>`;
    if (fut.length) html += `<h2>Futures <small>cleared, margined daily; P&L is settled in cash as variation margin</small></h2><div class="scroll"><table><tr><th>Contract</th><th>Contracts</th><th>Avg entry</th><th>Settle</th><th>Notional</th><th>Day VM</th><th>Cumulative VM</th><th>Initial margin</th><th>Weight</th><th>Expiry</th><th>Days</th></tr>
      ${fut.map(p => `<tr class="click" data-href="#/position/${p.security_id}"><td><b>${p.security_id}</b> <span class="muted">${esc(p.name)}</span></td><td class="${p.quantity > 0 ? 'pos' : 'neg'}">${p.quantity > 0 ? '+' : ''}${fmt.qty(p.quantity)}</td><td>${fmt.px(p.average_cost)}</td><td>${fmt.px(p.mark)}</td><td>${fmt.money(p.notional)}</td><td>${fmt.signed(p.day_variation_margin)}</td><td>${fmt.signed(p.realized_pnl)}</td><td>${fmt.money(p.risk.initial_margin)}</td><td>${fmt.pct(p.weight, 1)}</td><td>${p.expiry}</td><td class="${p.risk.days_to_expiry <= 3 ? 'neg' : ''}">${p.risk.days_to_expiry}</td></tr>`).join('')}</table></div>`;
    return html;
  }
  function bindRows() { document.querySelectorAll('tr[data-href]').forEach(tr => tr.addEventListener('click', () => { location.hash = tr.dataset.href; })); document.querySelectorAll('[data-trade]').forEach(el => el.addEventListener('click', e => { e.stopPropagation(); tradeModal(el.dataset.trade); })); document.querySelectorAll('[data-event]').forEach(el => el.addEventListener('click', e => { e.stopPropagation(); eventModal(el.dataset.event); })); document.querySelectorAll('[data-cancel]').forEach(el => el.addEventListener('click', async e => { e.stopPropagation(); try { await api(P() + '/orders/' + el.dataset.cancel, { method: 'DELETE' }); toast('order cancelled'); render(); } catch (err) { toast(err.message, true); } })); }

  pages.markets = async () => {
    const [secs, yc] = await Promise.all([api(W() + '/securities'), api(W() + '/yield-curve')]);
    const eq = secs.filter(s => !s.coupon && !s.is_future), bd = secs.filter(s => s.coupon), fin = secs.filter(s => s.is_future && ['EQUITY_INDEX', 'RATES'].includes(s.underlying_class));
    $('#main').innerHTML = `<h1>MARKETS <small>${state.world.current_date} · regime: ${state.world.regime.label} · policy rate ${fmt.pct(yc.policy_rate)} · IG ${fmt.bps(yc.ig_spread_bps)} · HY ${fmt.bps(yc.hy_spread_bps)}</small></h1>
      <p class="hint">${esc(state.world.regime.description)} Daily-step model: quotes shown are the current session's closing quotes; orders placed now fill against them (spread + impact). Working orders fill at the next open.</p>
      <h2>Equities, ETFs, preferreds, ADRs</h2><div class="scroll" style="max-height:none"><table><tr><th>Ticker</th><th>Name</th><th>Sector</th><th>Last</th><th>Chg</th><th>%</th><th>Bid</th><th>Ask</th><th>Spread</th><th>Volume</th><th>ADV</th><th>Mkt cap</th><th>Div yld</th><th>β</th><th>Vol 20d</th><th>Tier</th></tr>
      ${eq.map(s => `<tr class="click" data-href="#/security/${s.id}"><td><b>${s.id}</b></td><td class="l">${esc(s.name)} <span class="muted">${s.asset_class}</span></td><td class="l">${s.sector}</td><td>${fmt.px(s.last)}</td><td>${fmt.signed(s.change)}</td><td class="${s.change_pct >= 0 ? 'pos' : 'neg'}">${fmt.pct(s.change_pct)}</td><td>${fmt.px(s.bid)}</td><td>${fmt.px(s.ask)}</td><td>${fmt.bps(s.spread_bps)}</td><td>${fmt.qty(s.volume)}</td><td>${fmt.qty(s.adv)}</td><td>${s.market_cap ? fmt.big(s.market_cap) : '—'}</td><td>${fmt.pct(s.dividend_yield, 1)}</td><td>${s.beta}</td><td>${fmt.pct(s.realized_vol, 1)}</td><td>${s.liquidity_tier}</td></tr>`).join('')}</table></div>
      <h2>Fixed income</h2><table><tr><th>ID</th><th>Name</th><th>Rating</th><th>Coupon</th><th>Maturity</th><th>Clean</th><th>Chg</th><th>YTM</th><th>Mod dur</th><th>DV01/100</th><th>Spread</th><th>Accrued</th><th>ADV (face)</th></tr>
      ${bd.map(s => `<tr class="click" data-href="#/security/${s.id}"><td><b>${s.id}</b></td><td class="l">${esc(s.name)}</td><td>${s.rating}</td><td>${fmt.pct(s.coupon, 3)}</td><td>${s.maturity}</td><td>${fmt.px(s.last)}</td><td>${fmt.signed(s.change, 4)}</td><td>${fmt.pct(s.ytm, 3)}</td><td>${s.modified_duration.toFixed(2)}</td><td>${s.dv01_per_100.toFixed(4)}</td><td>${fmt.bps(s.spread_to_curve_bps)}</td><td>${s.accrued.toFixed(4)}</td><td>${fmt.big(s.adv)}</td></tr>`).join('')}</table>
      <h2>Financial futures <small>equity index (ES on SPXE) and 10Y Treasury note (ZN) · commodity futures are on the COMMODITIES desk</small></h2><table><tr><th>Contract</th><th>Name</th><th>Month</th><th>Last</th><th>Chg</th><th>Volume</th><th>Multiplier</th><th>Notional</th><th>Initial margin</th><th>Expiry</th></tr>
      ${fin.map(s => `<tr class="click" data-href="#/security/${s.id}"><td><b>${s.id}</b></td><td class="l">${esc(s.name)}</td><td>${s.contract_month}</td><td>${fmt.px(s.last)}</td><td>${fmt.signed(s.change, 4)}</td><td>${fmt.qty(s.volume)}</td><td>${s.multiplier}</td><td>${fmt.big(s.notional_per_contract)}</td><td>${fmt.money(s.initial_margin)}</td><td>${s.expiry}</td></tr>`).join('')}</table>`;
    bindRows();
  };

  const GROUP_LABEL = { COMMODITY_ENERGY: 'Energy', COMMODITY_METAL: 'Metals', COMMODITY_AG: 'Agriculture', COMMODITY_LIVESTOCK: 'Livestock', EQUITY_INDEX: 'Equity index', RATES: 'Rates' };
  pages.commodities = async () => {
    const cs = await api(W() + '/commodities'); const groups = {};
    cs.filter(c => c.group.startsWith('COMMODITY')).forEach(c => (groups[c.group] = groups[c.group] || []).push(c));
    $('#main').innerHTML = `<h1>COMMODITIES DESK <small>spot, front month, curve shape and fundamentals · regime: ${esc(state.world.regime.label)}</small></h1>
      <p class="hint">Prices respond to the supply/demand balance: inventory reports, production disruptions, weather, and the macro cycle. Tight inventories raise the convenience yield and put curves in backwardation; gluts push them into contango. Click a commodity for its curve, contracts and fundamentals.</p>
      ${Object.entries(groups).map(([g, rows]) => `<h2>${GROUP_LABEL[g]}</h2><table><tr><th>Code</th><th>Commodity</th><th>Spot</th><th>Day</th><th>Week</th><th>Month</th><th>Front</th><th>Front px</th><th>Curve</th><th>Slope (back/front)</th><th>Inventory σ</th><th>Demand σ</th><th>Supply shock</th><th>Conv. yield</th><th>Report</th></tr>
        ${rows.map(c => `<tr class="click" data-href="#/commodity/${c.code}"><td><b>${c.code}</b></td><td class="l">${esc(c.name)} <span class="muted">/${c.unit}</span></td><td>${fmt.px(c.spot)}</td><td class="${c.change_pct >= 0 ? 'pos' : 'neg'}">${fmt.pct(c.change_pct)}</td><td class="${c.week_pct >= 0 ? 'pos' : 'neg'}">${fmt.pct(c.week_pct)}</td><td class="${c.month_pct >= 0 ? 'pos' : 'neg'}">${fmt.pct(c.month_pct)}</td><td>${c.front || '—'}</td><td>${fmt.px(c.front_price)}</td><td><span class="badge ${c.curve_shape === 'backwardation' ? 'red' : c.curve_shape === 'contango' ? 'green' : ''}">${c.curve_shape}</span></td><td>${fmt.pct(c.curve_slope_pct, 1)}</td><td class="${c.inventory_z < -0.7 ? 'neg' : c.inventory_z > 0.7 ? 'pos' : ''}">${c.inventory_z.toFixed(2)}</td><td>${c.demand_z.toFixed(2)}</td><td>${c.supply_shock.toFixed(2)}</td><td>${fmt.pct(c.conv_yield, 1)}</td><td class="l muted">${c.report || ''}</td></tr>`).join('')}</table>`).join('')}`;
    bindRows();
  };

  pages.commodity = async (code) => {
    const c = await api(W() + '/commodities/' + code); const f = c.fundamentals; const months = Object.keys(c.curve);
    $('#main').innerHTML = `<h1>${c.spec.code} <small>${esc(c.spec.name)} · ${GROUP_LABEL[c.spec.group]} · ${c.spec.multiplier} ${esc(c.spec.unit)} per contract · tick ${c.spec.tick} ($${c.spec.tick_value.toFixed(2)}) · margin ${fmt.pct(c.spec.margin_pct, 0)} × ${c.margin_multiplier}x</small></h1>
      <div class="tiles">${tile('Spot', fmt.px(c.spot))}${tile('Front month', c.contracts[0] ? c.contracts[0].id : '—', c.contracts[0] ? `${fmt.px(c.contracts[0].last)} · expires ${c.contracts[0].expiry}` : '')}${tile('Curve', months.length ? (c.curve[months[months.length - 1]] < c.curve[months[0]] ? 'backwardation' : 'contango') : '—', months.length ? `${fmt.pct(c.curve[months[months.length - 1]] / c.curve[months[0]] - 1, 1)} back vs front` : '')}${f ? tile('Inventories', `${f.inventory_z.toFixed(2)}σ`, f.inventory_z < -0.7 ? 'tight' : f.inventory_z > 0.7 ? 'ample' : 'near normal') + tile('Demand', `${f.demand_z.toFixed(2)}σ`, `cycle beta ${f.demand_beta} · regime growth ${f.regime_growth}`) + tile('Supply shock', f.supply_shock.toFixed(2), 'positive = supply lost; decays ~3%/day') + tile('Convenience yield', fmt.pct(f.conv_yield, 1), `storage ${fmt.pct(f.storage_cost, 1)}/yr`) : ''}</div>
      <div class="grid2" style="margin-top:14px"><div class="panel"><h2 style="margin-top:0">Futures curve <span class="legend"><i style="background:${css('--accent')}"></i>today <i style="background:${css('--fg3')}"></i>5d ago <i style="background:${css('--purple')}"></i>1m ago</span></h2><canvas id="curve"></canvas>
        <h2>Spot history</h2><canvas id="spot"></canvas></div>
        <div class="panel"><h2 style="margin-top:0">Fundamentals</h2>${f ? `<p class="mono">${esc(f.narrative)}</p><div class="kv"><div>Scheduled report</div><div>${f.report || 'none'}</div><div>Event probability</div><div>${fmt.pct(f.event_probability_daily, 1)} per day (×3 over weekends)</div><div>Seasonality</div><div>${f.seasonal_amp ? `amplitude ${fmt.pct(f.seasonal_amp, 0)}, peak month ${f.seasonal_peak_month}` : 'none'}</div><div>Market beta</div><div>${f.market_beta}</div></div>` : '<p class="muted mono">financial future: priced by cost of carry from the underlying</p>'}
        <h2>News</h2>${c.news.length ? c.news.slice().reverse().map(n => `<div class="mono" style="margin-bottom:6px"><span class="muted">${n.date}</span> ${esc(n.headline)}</div>`).join('') : '<span class="muted mono">no recent news</span>'}</div></div>
      <h2>Listed contracts <small>click a contract to trade it</small></h2><table><tr><th>Contract</th><th>Month</th><th>Last</th><th>Chg</th><th>Bid</th><th>Ask</th><th>Volume</th><th>Notional</th><th>Initial margin</th><th>Expiry</th><th>Days</th></tr>${c.contracts.map(k => `<tr class="click" data-href="#/security/${k.id}"><td><b>${k.id}</b></td><td>${k.contract_month}</td><td>${fmt.px(k.last)}</td><td>${fmt.signed(k.change, 4)}</td><td>${fmt.px(k.bid)}</td><td>${fmt.px(k.ask)}</td><td>${fmt.qty(k.volume)}</td><td>${fmt.big(k.notional)}</td><td>${fmt.money(k.initial_margin)}</td><td>${k.expiry}</td><td>${k.days_to_expiry}</td></tr>`).join('')}</table>
      <h2>Strategies <small>built from single-leg instructions on the contracts above</small></h2><p class="hint">Calendar spread: buy one month, sell another. Crack spread: long CL, short RB/HO. Producer hedge: sell deferred months against expected output. Both legs are separate instructions that fill at the same update; tag them with the same strategy tag.</p>`;
    lineChart($('#curve'), [{ data: months.map(m => c.curve_1m_ago[m] ?? null), color: css('--purple') }, { data: months.map(m => c.curve_5d_ago[m] ?? null), color: css('--fg3') }, { data: months.map(m => c.curve[m]), color: css('--accent'), dots: true, width: 2 }], { labels: months.map(m => m.slice(2)), fmtY: v => v.toFixed(2), h: 200 });
    lineChart($('#spot'), [{ data: c.spot_history.map(x => x[1]), color: css('--blue') }], { labels: c.spot_history.map(x => x[0].slice(5)), fmtY: v => v.toFixed(2), h: 160 });
    bindRows();
  };

  pages.career = async () => {
    const c = await api(P() + '/career'); const j = c.job; const m = c.metrics; const ls = c.limits_status;
    const lim = (l, f) => l.limit == null || l.limit >= 9 ? `${f(l.value)} <span class="muted">/ no limit</span>` : `<span class="${l.value > l.limit ? 'neg' : ''}">${f(l.value)}</span> / ${f(l.limit)}`;
    $('#main').innerHTML = `<h1>CAREER <small>${esc(j.title)} · ${esc(c.level_title)} (level ${c.level + 1} of ${j.ladder.length})</small></h1>
      <p class="hint">${esc(j.description)} Mandate: ${j.allowed_classes.join(', ')}. Benchmark: ${j.benchmark || 'absolute return over cash'}.</p>
      <div class="tiles">${tile('Return since inception', fmt.pct(m.return_since_inception), m.benchmark_return != null ? `benchmark ${fmt.pct(m.benchmark_return)} · alpha ${fmt.pct(m.alpha)}` : `${m.days} days`)}${tile('Sharpe', m.sharpe.toFixed(2), `vol ${fmt.pct(m.volatility, 1)}`)}${tile('Max drawdown', fmt.pct(m.max_drawdown, 1), `current ${fmt.pct(m.current_drawdown, 1)}`)}${tile('Gross leverage', lim(ls.gross_leverage, v => v.toFixed(2) + 'x'), 'limit')}${tile('Largest position', lim(ls.max_position, v => fmt.pct(v, 1)), 'of NAV / limit')}${tile('Drawdown limit', lim(ls.drawdown, v => fmt.pct(v, 1)), 'from peak / limit')}${tile('Capital allocated', fmt.money(c.contributed_capital))}</div>
      <p class="hint">Ladder: ${j.ladder.map((t, i) => i === c.level ? `<b style="color:var(--accent)">${t}</b>` : t).join(' → ')}. Promotion after an EXCEEDS quarterly review; two UNACCEPTABLE quarters demote.</p>
      <div class="grid2" style="margin-top:14px"><div class="panel"><h2 style="margin-top:0">Performance reviews</h2>${c.reviews.length ? c.reviews.map(r => `<div style="margin-bottom:12px"><div><span class="badge ${r.rating === 'EXCEEDS' ? 'green' : r.rating === 'MEETS' ? 'blue' : 'red'}">${r.rating}</span> <b>${r.period} ${r.start} → ${r.end}</b> <span class="muted">${esc(r.title)}</span></div><div class="kv" style="margin-top:4px"><div>Return</div><div>${fmt.pct(r.return)} ${r.benchmark_return != null ? `vs benchmark ${fmt.pct(r.benchmark_return)} · alpha ${fmt.pct(r.alpha)}` : ''}</div><div>P&L</div><div>${fmt.signed(r.pnl)}</div><div>Max drawdown / Sharpe</div><div>${fmt.pct(r.max_drawdown, 1)} / ${r.sharpe.toFixed(2)}</div><div>Largest contribution</div><div>${r.largest_contribution.security_id || '—'} ${fmt.signed(r.largest_contribution.pnl)}</div><div>Largest loss</div><div>${r.largest_loss.security_id || '—'} ${fmt.signed(r.largest_loss.pnl)}</div><div>By bucket</div><div>${Object.entries(r.buckets).filter(([k, v]) => v).map(([k, v]) => `${BUCKET_LABEL[k] || k} ${fmt.signed(v)}`).join(' · ')}</div><div>Breaches / fails / calls missed</div><div>${r.risk_breaches} / ${r.settlement_failures} / ${r.margin_calls_missed}</div></div><div class="muted" style="margin-top:4px">${esc(r.evaluation)}</div></div>`).join('') : '<span class="muted mono">first review at month end</span>'}</div>
        <div class="panel"><h2 style="margin-top:0">Career log</h2>${c.career_log.length ? c.career_log.map(x => `<div><span class="muted">${x.date}</span> <span class="badge ${x.kind === 'PROMOTION' ? 'green' : 'red'}">${x.kind}</span> ${esc(x.text)}</div>`).join('') : '<span class="muted mono">no events yet</span>'}
          <h2>Risk-limit breaches</h2><div class="scroll"><table><tr><th>Date</th><th>Kind</th><th>Detail</th></tr>${c.breaches.map(b => `<tr><td>${b.date}</td><td class="l">${b.kind}</td><td class="l" style="white-space:normal">${esc(b.text)}</td></tr>`).join('') || '<tr><td colspan=3 class="muted">none</td></tr>'}</table></div>
          <h2>Margin calls</h2><table><tr><th>Date</th><th>ID</th><th>Amount</th><th>Status</th><th>Reason</th></tr>${c.margin_calls.map(x => `<tr><td>${x.date}</td><td>${x.id}</td><td>${fmt.money(x.amount)}</td><td>${statusBadge(x.status)}</td><td class="l" style="white-space:normal">${esc(x.reason)}</td></tr>`).join('') || '<tr><td colspan=5 class="muted">none</td></tr>'}</table></div></div>`;
  };

  pages.security = async (id) => {
    const s = await api(W() + `/securities/${id}?period=${state.period}`); const sec = s.security; const isBond = !!sec.coupon; const isFut = sec.asset_class === 'FUTURE'; const a = s.analytics; const f = s.fundamentals; const fu = s.futures;
    $('#main').innerHTML = `<h1>${sec.id} <small>${esc(sec.name)} · ${sec.asset_class} · ${sec.sector} · ${sec.country} · ${sec.currency} · ${isFut ? `contract month ${sec.contract_month} · cleared` : `ISIN ${sec.isin} · CUSIP ${sec.cusip} · settles ${sec.market} T+${state.world.settlement_cycles[sec.market]}`}</small></h1>
      <div class="tiles">${tile('Last', fmt.px(s.last))}${tile('Bid / Ask', `${fmt.px(s.bid)} / ${fmt.px(s.ask)}`, `spread ${fmt.bps(s.spread_bps)}`)}${tile('Volume', fmt.qty(s.volume), `ADV ${fmt.qty(sec.adv)}`)}${tile('Vol (20d realized)', fmt.pct(s.realized_vol_20d, 1))}
        ${isFut ? tile('Contract', `${fu.spec.multiplier} ${esc(fu.spec.unit)}`, `tick ${fu.spec.tick} = $${fu.spec.tick_value.toFixed(2)} · notional ${fmt.big(fu.notional_per_contract)}`) + tile('Initial margin', fmt.money(fu.initial_margin), `${fmt.pct(fu.spec.margin_pct, 0)} × regime ${fu.margin_multiplier}x`) + tile('Expiry', fu.expiry, `${fu.days_to_expiry} business days · auto-closed at expiry`) + tile('Spot / curve', fmt.px(fu.spot), `<a href="#/commodity/${sec.underlying}">${sec.underlying} desk page →</a>`)
          : isBond ? tile('YTM', fmt.pct(a.ytm, 3), `benchmark ${fmt.pct(a.benchmark_yield, 3)} · spread ${fmt.bps(a.spread_to_curve_bps)}`) + tile('Mod duration', a.modified_duration.toFixed(3), `convexity ${a.convexity.toFixed(1)}`) + tile('DV01 per $1MM', fmt.money(a.position_dv01), `dirty ${a.dirty_price.toFixed(4)} · accrued ${a.accrued.toFixed(4)}`) + tile('Rating', sec.rating, `1y PD ${fmt.pct(a.default_probability_1y, 2)} · recovery ${fmt.pct(sec.recovery_rate, 0)}`)
          : tile('Market cap', f ? fmt.big(f.market_cap) : '—', `${fmt.qty(sec.shares_outstanding)} shs`) + tile('Dividend', `$${sec.dividend_per_share}/qtr`, `yield ${fmt.pct(sec.dividend_yield, 1)}`) + tile('Beta', sec.beta, `idio vol ${fmt.pct(sec.sigma_annual, 0)}`) + tile('Borrow', 'n/a', 'securities lending: Phase 2')}
      </div>
      <div class="grid3" style="margin-top:14px"><div class="panel"><div class="row" style="justify-content:space-between"><h2 style="margin:0">Chart</h2><span class="periods">${['1D', '5D', '1M', '3M', 'YTD', '1Y', 'MAX'].map(p => `<a data-period="${p}" class="${p === state.period ? 'active' : ''}">${p}</a>`).join('')}</span></div><canvas id="px"></canvas></div>
        <div class="panel"><h2 style="margin-top:0">Order ticket</h2>${ticketHtml(sec.id, isBond, isFut)}<h2>Order book <small>${esc(s.order_book.note)}</small></h2><table class="book"><tr><th>Bid size</th><th>Bid</th><th>Ask</th><th>Ask size</th></tr>${s.order_book.levels.map(l => `<tr><td>${fmt.qty(l.bid_size)}</td><td class="bidc">${fmt.px(l.bid)}</td><td class="askc">${fmt.px(l.ask)}</td><td>${fmt.qty(l.ask_size)}</td></tr>`).join('')}</table></div></div>
      <div class="grid2" style="margin-top:14px">
        <div class="panel">${isFut ? `<h2 style="margin-top:0">${sec.underlying} futures curve</h2><canvas id="fcurve"></canvas><table style="margin-top:8px"><tr><th>Month</th><th>Price</th></tr>${Object.entries(fu.curve).map(([m, p]) => `<tr class="${m === sec.contract_month ? 'pos' : ''}"><td>${m}</td><td>${fmt.px(p)}</td></tr>`).join('')}</table>` : isBond ? `<h2 style="margin-top:0">Cash flows (per 100 face)</h2><table><tr><th>Date</th><th>Amount</th></tr>${s.cash_flows.map(c => `<tr><td>${c[0]}</td><td>${c[1].toFixed(4)}</td></tr>`).join('')}</table>`
          : f ? `<h2 style="margin-top:0">Fundamentals</h2><div class="kv"><div>Revenue</div><div>${fmt.big(f.revenue)}</div><div>EBITDA</div><div>${fmt.big(f.ebitda)}</div><div>Net income</div><div>${fmt.big(f.net_income)}</div><div>EPS</div><div>${f.eps}</div><div>Free cash flow</div><div>${fmt.big(f.free_cash_flow)}</div><div>Total debt</div><div>${fmt.big(f.total_debt)}</div><div>Cash</div><div>${fmt.big(f.cash)}</div><div>Enterprise value</div><div>${fmt.big(f.enterprise_value)}</div><div>P/E</div><div>${f.pe ? f.pe.toFixed(1) : '—'}</div><div>EV/EBITDA</div><div>${f.ev_ebitda ? f.ev_ebitda.toFixed(1) : '—'}</div><div>P/B</div><div>${f.pb ? f.pb.toFixed(2) : '—'}</div><div>FCF yield</div><div>${fmt.pct(f.fcf_yield)}</div><div>Debt/EBITDA</div><div>${f.debt_ebitda ? f.debt_ebitda.toFixed(2) : '—'}</div><div>ROE</div><div>${fmt.pct(f.roe)}</div></div>` : '<p class="muted mono">no issuer fundamentals (fund/ETF)</p>'}</div>
        <div class="panel"><h2 style="margin-top:0">Corporate actions</h2><table><tr><th>ID</th><th>Type</th><th>Declared</th><th>Ex</th><th>Record</th><th>Pay</th><th>Amount</th><th>Status</th></tr>${s.corporate_actions.map(c => `<tr><td class="l">${c.id}</td><td class="l">${c.action_type}</td><td>${c.declared_date}</td><td>${c.ex_date}</td><td>${c.record_date}</td><td>${c.pay_date}</td><td>${c.amount_per_unit}</td><td>${statusBadge(c.status)}</td></tr>`).join('') || '<tr><td colspan=8 class="muted">none scheduled</td></tr>'}</table>
          <h2>News</h2>${s.news.map(n => `<div class="mono" style="margin-bottom:6px"><span class="muted">${n.date}</span> ${esc(n.headline)}</div>`).join('') || '<p class="muted mono">no news</p>'}</div></div>`;
    candleChart($('#px'), s.bars);
    if (isFut) lineChart($('#fcurve'), [{ data: Object.values(fu.curve), color: css('--accent'), dots: true }], { labels: Object.keys(fu.curve).map(m => m.slice(2)), fmtY: v => v.toFixed(2), h: 160 });
    document.querySelectorAll('[data-period]').forEach(a => a.addEventListener('click', () => { state.period = a.dataset.period; render(); }));
    bindTicket();
  };

  function ticketHtml(secId = '', isBond = false, isFuture = false) {
    return `<div class="form" id="ticket"><label>Security</label><input id="tSec" value="${secId}" placeholder="ticker, bond id or contract e.g. NVRA, UST-10Y, CLZ26" ${secId ? 'readonly' : ''}><label>Side</label><select id="tSide"><option>BUY</option><option>SELL</option></select>
      <label>Quantity</label><input id="tQty" type="number" value="${isFuture ? 10 : isBond ? 1000000 : 10000}" step="${isBond ? 1000 : 1}"><label>Type</label><select id="tType"><option value="MARKET">MARKET — at next update's open</option><option value="LIMIT">LIMIT</option><option value="STOP">STOP</option><option value="STOP_LIMIT">STOP_LIMIT</option><option value="TAKE_PROFIT">TAKE_PROFIT</option><option value="TRAILING_STOP">TRAILING_STOP</option></select>
      <label>Limit</label><input id="tLimit" type="number" step="0.0001" placeholder="limit price"><label>Stop</label><input id="tStop" type="number" step="0.0001" placeholder="stop price"><label>Trail %</label><input id="tTrail" type="number" step="0.1" placeholder="e.g. 5 = 5% below best close">
      <label>Condition</label><div class="row"><span class="muted mono">only if</span><input id="tCondRef" placeholder="NVRA · CURVE:10Y · SPOT:CL" style="width:150px"><select id="tCondOp"><option>&lt;=</option><option>&gt;=</option></select><input id="tCondVal" type="number" step="any" placeholder="value" style="width:110px"></div>
      <label>TIF</label><select id="tTif"><option value="DAY">DAY — good for the next session</option><option value="GTC">GTC — until cancelled</option></select><label>Strategy tag</label><input id="tTag" placeholder="optional, e.g. crack-spread"></div>
      <div class="row" style="margin-top:8px"><button class="btn primary" id="tGo">Submit instruction</button><span id="tMsg" class="mono"></span></div><p class="hint">Instructions execute at the next daily update against that session (open, high, low, close, volume). ${isFuture ? 'Futures: quantity is contracts; shorts allowed; initial margin is posted from cash and P&L settles daily as variation margin.' : isBond ? 'Bond quantity is face value (multiples of 1,000). Price is clean per 100; accrued interest is paid on top.' : 'Market orders cross the spread and pay impact. Orders above 20% of session volume fill partially and keep working (GTC) or expire (DAY).'} Conditions are checked at the close (yields in %, e.g. CURVE:10Y >= 5).</p>`;
  }
  function bindTicket() {
    const go = $('#tGo'); if (!go) return;
    go.addEventListener('click', async () => {
      const cond = $('#tCondRef').value.trim() ? { ref: $('#tCondRef').value.trim(), op: $('#tCondOp').value.replace('&lt;', '<').replace('&gt;', '>'), value: +$('#tCondVal').value } : null;
      const body = { security_id: $('#tSec').value.trim().toUpperCase(), side: $('#tSide').value, quantity: +$('#tQty').value, order_type: $('#tType').value, limit_price: $('#tLimit').value ? +$('#tLimit').value : null, stop_price: $('#tStop').value ? +$('#tStop').value : null, time_in_force: $('#tTif').value, strategy_tag: $('#tTag').value || null, trail_pct: $('#tTrail').value ? +$('#tTrail').value / 100 : null, condition: cond };
      go.disabled = true; $('#tMsg').textContent = '';
      try { const r = await api(P() + '/orders', { method: 'POST', body }); const o = r.order; toast(`${o.status}: ${o.side} ${fmt.qty(o.quantity)} ${o.security_id} ${o.order_type} — executes at the next update`); render(); }
      catch (e) { $('#tMsg').innerHTML = `<span class="error">${esc(e.message)}</span>`; } finally { go.disabled = false; }
    });
  }
  function ordersTable(orders) {
    return `<div class="scroll"><table><tr><th>Order</th><th>Entered</th><th>Security</th><th>Side</th><th>Type</th><th>Qty</th><th>Filled</th><th>Avg px</th><th>Limit</th><th>Stop / trail</th><th>Condition</th><th>TIF</th><th>Status</th><th>Latest note</th><th></th></tr>
      ${orders.map(o => `<tr><td>${o.id}</td><td>${o.entered_date}</td><td><a href="#/security/${o.security_id}">${o.security_id}</a></td><td class="${o.side === 'BUY' ? 'pos' : 'neg'}">${o.side}</td><td>${o.order_type}${o.strategy_tag === 'SYSTEM' ? ' <span class="badge">SYSTEM</span>' : ''}</td><td>${fmt.qty(o.quantity)}</td><td>${fmt.qty(o.filled_quantity)}</td><td>${o.filled_quantity ? fmt.px(o.avg_fill_price) : '—'}</td><td>${o.limit_price ? fmt.px(o.limit_price) : '—'}</td><td>${o.stop_price ? fmt.px(o.stop_price) : o.trail_level ? `${fmt.px(o.trail_level)} (${(o.trail_pct * 100).toFixed(1)}%)` : '—'}</td><td class="l">${o.condition ? `${o.condition.ref} ${o.condition.op} ${o.condition.value}${o.condition_met_date ? ' ✓' : ''}` : ''}</td><td>${o.time_in_force}</td><td>${statusBadge(o.status)}</td><td class="l muted" style="white-space:normal;max-width:300px">${esc(o.reason || '')}</td><td>${['WORKING', 'PARTIALLY_FILLED'].includes(o.status) ? `<button class="btn danger" data-cancel="${o.id}">cancel</button>` : ''}</td></tr>`).join('') || '<tr><td colspan=15 class="muted">no orders</td></tr>'}</table></div>`;
  }
  function tradesTable(trades) {
    return `<div class="scroll"><table><tr><th>Trade</th><th>T date</th><th>S date</th><th>Security</th><th>Side</th><th>Qty</th><th>Price</th><th>Gross</th><th>Accrued</th><th>Comm</th><th>Net</th><th>Realized</th><th>Impact</th><th>Status</th></tr>
      ${trades.map(t => `<tr class="click" data-trade="${t.id}"><td>${t.id}</td><td>${t.trade_date}</td><td>${t.settlement_date}</td><td>${t.security_id}</td><td class="${t.side === 'BUY' ? 'pos' : 'neg'}">${t.side}</td><td>${fmt.qty(t.quantity)}</td><td>${fmt.px(t.price)}</td><td>${fmt.money(t.gross_amount)}</td><td>${fmt.money(t.accrued_interest)}</td><td>${fmt.money(t.commission)}</td><td>${fmt.money(t.net_amount)}</td><td>${t.side === 'SELL' ? fmt.signed(t.realized_pnl) : '—'}</td><td>${fmt.bps(t.execution_detail.impact_bps)}</td><td>${statusBadge(t.status)}</td></tr>`).join('') || '<tr><td colspan=14 class="muted">no trades</td></tr>'}</table></div>`;
  }
  function entriesTable(entries) {
    return `<table><tr><th>Entry</th><th>Date</th><th>Memo</th><th>Account</th><th>Debit</th><th>Credit</th><th>Security</th><th>Event</th></tr>
      ${entries.map(e => e.lines.map((l, i) => `<tr>${i === 0 ? `<td rowspan="${e.lines.length}">${e.id}</td><td rowspan="${e.lines.length}">${e.date}</td><td rowspan="${e.lines.length}" class="l" style="white-space:normal;max-width:340px">${esc(e.memo)}</td>` : ''}<td class="l">${l.account} <span class="muted">${esc(l.name)}</span></td><td>${l.debit ? fmt.money(l.debit) : ''}</td><td>${l.credit ? fmt.money(l.credit) : ''}</td><td>${l.security_id || ''}</td>${i === 0 ? `<td rowspan="${e.lines.length}"><a data-event="${e.cause_id || e.event_id}">${e.cause_id || e.event_id}</a></td>` : ''}</tr>`).join('')).join('') || '<tr><td colspan=8 class="muted">no entries</td></tr>'}</table>`;
  }
  const LIFECYCLE = ['EXECUTED', 'CAPTURED', 'MATCHED', 'AFFIRMED', 'CLEARED', 'SETTLEMENT_PENDING', 'SETTLED'];
  async function tradeModal(id) {
    const x = await api(P() + '/trades/' + id); const t = x.trade, si = x.settlement, ed = t.execution_detail; const reached = new Set(t.status_history.map(h => h.status));
    modal(`<h1>Trade ${t.id} <a onclick="document.getElementById('modalRoot').innerHTML=''">✕</a></h1>
      <div class="lifecycle">${LIFECYCLE.map(s => `<span class="${t.status === 'FAILED' && s === 'SETTLED' ? 'fail' : reached.has(s) ? (s === t.status ? 'cur' : 'done') : ''}">${s}</span>`).join('')}${t.status === 'FAILED' ? '<span class="fail">FAILED (retrying)</span>' : ''}</div>
      <div class="grid2"><div><h2>Economics</h2><div class="kv"><div>Security</div><div><a href="#/security/${t.security_id}" onclick="document.getElementById('modalRoot').innerHTML=''">${t.security_id}</a></div><div>Side / qty</div><div>${t.side} ${fmt.qty(t.quantity)}</div><div>Price</div><div>${fmt.px(t.price)}</div><div>Gross</div><div>${fmt.money(t.gross_amount)}</div><div>Accrued interest</div><div>${fmt.money(t.accrued_interest)}</div><div>Commission</div><div>${fmt.money(t.commission)}</div><div>Net ${t.side === 'BUY' ? 'payable' : 'receivable'}</div><div>${fmt.money(t.net_amount)} ${t.currency}</div><div>Trade / settle</div><div>${t.trade_date} → ${t.settlement_date}</div><div>Broker</div><div>${esc(t.broker)}</div>${t.side === 'SELL' ? `<div>Realized P&L</div><div>${fmt.signed(t.realized_pnl)}</div>` : ''}</div>
        <h2>Execution quality</h2><div class="kv"><div>Session</div><div>${ed.session} (${ed.regime})</div><div>Session O/H/L/C</div><div>${fmt.px(ed.reference_open)} / ${fmt.px(ed.reference_high)} / ${fmt.px(ed.reference_low)} / ${fmt.px(ed.reference_close)}</div><div>Base price</div><div>${fmt.px(ed.base_price)}</div><div>Spread cost</div><div>${fmt.money(ed.spread_cost)}</div><div>Impact</div><div>${fmt.money(ed.impact_cost)} (${fmt.bps(ed.impact_bps)})</div><div>Participation of ADV</div><div>${fmt.pct(ed.participation_of_adv)}</div><div>Partial</div><div>${ed.partial ? 'yes — remainder works' : 'no'}</div>${ed.forced ? `<div>Forced</div><div class="neg">${esc(ed.note)}</div>` : ed.note ? `<div>Note</div><div>${esc(ed.note)}</div>` : ''}</div>
        ${t.lots_relieved && t.lots_relieved.length ? `<h2>Lots relieved (FIFO)</h2><table><tr><th>Lot</th><th>Opened</th><th>Qty</th><th>Cost/unit</th><th>Cost</th></tr>${t.lots_relieved.map(l => `<tr><td>${l.lot_id}</td><td>${l.open_date}</td><td>${fmt.qty(l.quantity)}</td><td>${fmt.px(l.cost_per_unit)}</td><td>${fmt.money(l.cost)}</td></tr>`).join('')}</table>` : ''}</div>
      <div><h2>Status history</h2><table><tr><th>Date</th><th>Status</th><th>Note</th><th>Event</th></tr>${t.status_history.map(h => `<tr><td>${h.date}</td><td class="l">${statusBadge(h.status)}</td><td class="l" style="white-space:normal">${esc(h.note)}</td><td><a data-event="${h.event_id}">${h.event_id}</a></td></tr>`).join('')}</table>
        ${!si && t.status === 'CLEARED' ? '<h2>Clearing</h2><div class="kv"><div>Type</div><div>Cleared futures — no DVP; margined daily</div></div>' : ''}
        ${si ? `<h2>Settlement instruction ${si.id}</h2><div class="kv"><div>Type</div><div>${si.instruction_type} (${si.instruction_type === 'RVP' ? 'receive vs payment' : 'deliver vs payment'})</div><div>Deliverer → receiver</div><div>${esc(si.delivering_party)} → ${esc(si.receiving_party)}</div><div>Custodian</div><div>${esc(si.custodian)}</div><div>Status</div><div>${statusBadge(si.status)} ${si.fail_count ? `<span class="error">${si.fail_count} fail(s): ${esc(si.fail_reason)}</span>` : ''}</div><div>Settled</div><div>${si.settled_date || '—'}</div></div>` : ''}</div></div>
      <h2>Ledger entries</h2>${entriesTable(x.ledger_entries)}
      <h2>Events</h2><table><tr><th>Event</th><th>Type</th><th>Date</th><th>Cause</th></tr>${x.events.map(e => `<tr><td><a data-event="${e.id}">${e.id}</a></td><td class="l">${e.type}</td><td>${e.sim_date}</td><td>${e.cause_id || ''}</td></tr>`).join('')}</table>`);
    bindRows();
  }
  async function eventModal(id) {
    const x = await api(W() + '/events/' + id); const e = x.event;
    const treeHtml = n => `<li><a data-event="${n.event.id}">${n.event.id}</a> <b>${n.event.type}</b> <span class="muted">${summ(n.event)}</span>${n.children.length ? `<ul>${n.children.map(treeHtml).join('')}</ul>` : ''}</li>`;
    modal(`<h1>Event ${e.id} <a onclick="document.getElementById('modalRoot').innerHTML=''">✕</a></h1><div class="kv"><div>Type</div><div>${e.type}</div><div>Sim date</div><div>${e.sim_date}</div><div>Sequence</div><div>${e.seq}</div><div>Portfolio</div><div>${e.portfolio_id || '—'}</div><div>Caused by</div><div>${x.ancestors.map(a => `<a data-event="${a.id}">${a.id}</a> ${a.type}`).join(' → ') || 'root command'}</div></div>
      <h2>Causal tree</h2><div class="tree"><ul>${treeHtml(x.tree)}</ul></div><h2>Payload</h2><pre>${esc(JSON.stringify(e.payload, null, 2))}</pre>`);
    bindRows();
  }
  function summ(e) { const p = e.payload || {}; return [p.trade_id, p.si_id, p.entry_id, p.order_id, p.security_id, p.status, p.memo, p.amount != null ? fmt.money(p.amount) : null, p.headline].filter(Boolean).slice(0, 3).map(esc).join(' · '); }

  pages.portfolio = async () => {
    const [d, x] = await Promise.all([api(P() + '/dashboard'), api(P() + '/pnl-explain')]); const pro = realism() !== 'BEGINNER';
    const grp = (title, g) => `<div class="panel"><h2 style="margin-top:0">${title}</h2><table>${Object.entries(g).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<tr><td>${k}</td><td>${fmt.money(v)}</td><td>${fmt.pct(v / d.nav, 1)}</td></tr>`).join('') || '<tr><td class="muted">none</td></tr>'}</table></div>`;
    $('#main').innerHTML = `<h1>PORTFOLIO <small>${esc(d.portfolio.name)}</small></h1>
      <div class="tiles">${tile('NAV', fmt.money(d.nav), 'click to explain', 'nav')}${tile('Day P&L', fmt.signed(d.day_pnl), `MTD ${fmt.signed(d.mtd_pnl)} · YTD ${fmt.signed(d.ytd_pnl)}`)}${tile('Cash (settled)', fmt.money(d.cash.USD || 0), `available liquidity ${fmt.money(d.available_liquidity)}`)}${tile('Gross / Net', `${fmt.big(d.gross_exposure)} / ${fmt.big(d.net_exposure)}`, `leverage ${Number(d.leverage).toFixed(2)}x`)}${tile('Long / Short', `${fmt.big(d.long_exposure)} / ${fmt.big(d.short_exposure)}`, `futures ${fmt.big(d.futures_long)} / ${fmt.big(d.futures_short)}`)}${tile('Realized', fmt.signed(d.realized), 'incl. futures VM')}${tile('Unrealized', fmt.signed(d.unrealized))}${tile('Income', fmt.signed(d.income.dividends + d.income.interest), 'dividends + interest')}${tile('Financing expense', fmt.signed(-d.income.interest_expense))}${tile('Fees', fmt.signed(-d.income.commissions))}${pro ? tile('Margin posted', fmt.money(d.margin_used), 'initial margin at clearing member') : ''}${tile('Margin calls', d.margin_calls.length, d.margin_calls.length ? `<span class="neg">${fmt.money(d.margin_calls[0].amount)} due</span>` : 'none outstanding')}${tile('Largest risk', d.largest_risk ? d.largest_risk.security_id : '—', d.largest_risk ? `${fmt.big(d.largest_risk.notional)} · ${fmt.pct(d.largest_risk.weight)} of NAV` : '')}${d.benchmark ? tile('vs benchmark', fmt.pct(d.return_since_inception - d.benchmark.return), `book ${fmt.pct(d.return_since_inception)} · ${d.benchmark.id} ${fmt.pct(d.benchmark.return)}`) : ''}</div>
      <div class="grid3" style="margin-top:14px"><div class="panel"><h2 style="margin-top:0">NAV history</h2><canvas id="navChart"></canvas></div><div class="panel"><h2 style="margin-top:0">Upcoming settlements & cash flows</h2><table><tr><th>Date</th><th>Kind</th><th>Ref</th><th>Amount</th></tr>${d.upcoming_cash_flows.map(c => `<tr><td>${c.date}</td><td class="l">${c.kind}</td><td class="l">${esc(c.ref)}</td><td>${fmt.signed(c.amount)}</td></tr>`).join('') || '<tr><td colspan=4 class="muted">none</td></tr>'}</table>${d.working_orders.length ? `<h2>Working instructions</h2>${ordersTable(d.working_orders)}` : ''}</div></div>
      <h2>Positions <small>click a row for the full lifecycle drill-down</small></h2>${positionsTable(d.positions)}
      <div class="grid4" style="margin-top:14px">${grp('By asset class', d.exposure_by_asset_class)}${grp('By sector', d.exposure_by_sector)}${grp('By currency', d.exposure_by_currency)}${grp('By country', d.exposure_by_country)}</div>
      <p class="hint">Exposure is signed notional (futures at contracts × price × multiplier). Ledger NAV ${fmt.money(d.ledger_nav)} ${Math.abs(d.ledger_nav - d.nav) < 0.005 ? '<span class="ok">reconciles ✓</span>' : '<span class="error">does not reconcile</span>'}.</p>
      <h2>P&L explain <small>${x.available ? `close of ${x.date} · ${x.reconciles ? '<span class="ok">components sum to NAV change ✓</span>' : '<span class="error">does not reconcile</span>'}` : 'no closed day yet'}</small></h2>
      ${x.available ? `<div class="grid2"><div class="panel"><div class="kv"><div>Previous NAV</div><div>${fmt.money(x.prev_nav)}</div><div>Capital flows</div><div>${fmt.signed(x.capital_flows)}</div>${Object.entries(x.explain).map(([k, v]) => `<div>${BUCKET_LABEL[k] || k}</div><div>${fmt.signed(v)}</div>`).join('')}<div><b>Day P&L</b></div><div><b>${fmt.signed(x.day_pnl)}</b></div><div>Closing NAV</div><div>${fmt.money(x.nav)} <a data-event="${x.event_id}">${x.event_id}</a></div></div><canvas id="explainChart" style="margin-top:10px"></canvas></div>
        <div class="panel"><h2 style="margin-top:0">By position (${x.date})</h2><table><tr><th>Security</th><th>Qty</th><th>Price P&L</th><th>Realized</th><th>Unreal Δ</th><th>Dividends</th><th>Interest</th><th>Comm</th><th>Total</th></tr>${Object.entries(x.by_position).sort((a, b) => Math.abs(b[1].total) - Math.abs(a[1].total)).map(([k, v]) => `<tr class="click" data-href="#/position/${k}"><td>${k}</td><td>${fmt.qty(v.quantity)} <span class="muted">${v.bucket || ''}</span></td><td>${fmt.signed(v.price_pnl)}</td><td>${fmt.signed(v.realized)}</td><td>${fmt.signed(v.unrealized_change)}</td><td>${fmt.signed(v.dividends)}</td><td>${fmt.signed(v.interest)}</td><td>${fmt.signed(v.commissions)}</td><td>${fmt.signed(v.total)}</td></tr>`).join('')}</table>
        <h2>Daily P&L history</h2><canvas id="pnlHist"></canvas></div></div>` : ''}`;
    lineChart($('#navChart'), [{ data: d.nav_history.map(v => v.nav), color: css('--accent') }], { labels: d.nav_history.map(v => v.date.slice(5)), fmtY: fmt.big });
    if (x.available) { barChart($('#explainChart'), Object.entries(x.explain).map(([k, v]) => ({ label: EXPLAIN_SHORT[k] || k, value: v }))); lineChart($('#pnlHist'), [{ data: x.history.map(h => h.day_pnl), color: css('--blue'), dots: true }], { labels: x.history.map(h => h.date.slice(5)), zero: true, h: 160 }); }
    document.querySelectorAll('[data-click="nav"]').forEach(el => el.addEventListener('click', navExplain));
    bindRows();
  };

  pages.position = async (id) => {
    const x = await api(P() + '/positions/' + id); const p = x.position;
    $('#main').innerHTML = `<h1>${p.security_id} <small>${esc(p.name)} · <a href="#/security/${p.security_id}">security page</a></small></h1>
      <div class="tiles">${p.is_future ? tile('Contracts', `${p.quantity > 0 ? '+' : ''}${fmt.qty(p.quantity)}`, `${p.quantity > 0 ? 'long' : 'short'} · ${p.contract_month} · expires ${p.expiry} (${p.risk.days_to_expiry}d)`) + tile('Average entry', fmt.px(p.average_cost)) + tile('Settlement price', fmt.px(p.mark)) + tile('Notional', fmt.money(p.notional), `${fmt.pct(p.weight, 1)} of NAV`) + tile('Variation margin (cum.)', fmt.signed(p.realized_pnl), `today ${fmt.signed(p.day_variation_margin)}`) + tile('Initial margin', fmt.money(p.risk.initial_margin), 'held at clearing member') : tile('Owned (trade date)', fmt.qty(p.quantity), `settled ${fmt.qty(p.settled_quantity)} · pend rcv ${fmt.qty(p.pending_receive)} · pend dlv ${fmt.qty(p.pending_deliver)}`) + tile('Average cost', fmt.px(p.average_cost), `cost basis ${fmt.money(p.cost_basis)}`) + tile('Current price', fmt.px(p.mark)) + tile('Market value', fmt.money(p.market_value), `${fmt.pct(p.weight, 1)} of NAV`)}${tile('Unrealized P&L', fmt.signed(p.unrealized_pnl))}${tile('Realized P&L', fmt.signed(p.realized_pnl))}${tile('Beta / delta', `${p.beta} / ${p.risk.delta ?? 1}`, p.risk.beta_dollar_exposure != null ? `β$ ${fmt.big(p.risk.beta_dollar_exposure)}` : '')}${tile('Dividend income', fmt.signed(p.dividend_income))}${tile('Interest income', fmt.signed(p.interest_income), p.accrued_interest ? `accrued ${fmt.money(p.accrued_interest)}` : '')}${tile('Financing cost', fmt.money(0), p.financing)}${tile('Borrow status', p.borrow_status)}${tile('Collateral status', p.collateral_status)}${tile('Commissions', fmt.money(p.commissions))}${p.risk.position_dv01 != null ? tile('DV01', fmt.money(p.risk.position_dv01), `mod dur ${p.risk.modified_duration.toFixed(2)} · ytm ${fmt.pct(p.risk.ytm, 3)}`) : ''}</div>
      <h2>Position relationships</h2><div class="panel">${x.relationships.map(r => `<div class="mono"><span class="badge blue">${r.kind}</span> ${esc(r.description)}</div>`).join('')}</div>
      <div class="grid2" style="margin-top:14px"><div class="panel"><h2 style="margin-top:0">Open lots (FIFO)</h2><table><tr><th>Lot</th><th>Trade</th><th>Opened</th><th>Remaining</th><th>Original</th><th>Cost/unit</th><th>Cost</th></tr>${x.lots.map(l => `<tr><td>${l.id}</td><td><a data-trade="${l.trade_id}">${l.trade_id}</a></td><td>${l.open_date}</td><td>${fmt.qty(l.quantity)}</td><td>${fmt.qty(l.original_quantity)}</td><td>${fmt.px(l.cost_per_unit)}</td><td>${fmt.money(l.cost_total)}</td></tr>`).join('') || '<tr><td colspan=7 class="muted">no open lots</td></tr>'}</table>
        <h2>Ledger balances for this security</h2><table>${Object.entries(x.ledger_balances).map(([a, v]) => `<tr><td>${a}</td><td class="l muted">${esc(accountName(a))}</td><td>${fmt.money(v)}</td></tr>`).join('')}</table></div>
        <div class="panel"><h2 style="margin-top:0">Dividends</h2><table><tr><th>CA</th><th>Ex</th><th>Pay</th><th>Per share</th><th>Qty</th><th>Amount</th><th>Paid</th></tr>${x.dividends.map(d => `<tr><td class="l">${d.ca_id}</td><td>${d.ex_date}</td><td>${d.pay_date}</td><td>${d.amount_per_unit}</td><td>${fmt.qty(d.quantity)}</td><td>${fmt.money(d.amount)}</td><td>${d.paid ? '<span class="pos">yes</span>' : '<span class="muted">pending</span>'}</td></tr>`).join('') || '<tr><td colspan=7 class="muted">none</td></tr>'}</table>
        <h2>Custody movements</h2><table><tr><th>Date</th><th>Kind</th><th>Qty</th><th>Balance</th><th>Ref</th></tr>${x.custody_movements.map(c => `<tr><td>${c.date}</td><td class="l">${c.kind}</td><td>${fmt.signed(c.quantity, 0)}</td><td>${fmt.qty(c.balance_after)}</td><td class="l muted">${esc(c.reference)}</td></tr>`).join('') || '<tr><td colspan=5 class="muted">none</td></tr>'}</table></div></div>
      <h2>Trades</h2>${tradesTable(x.trades)}
      <h2>Settlement instructions</h2>${settlementsTable(x.settlements)}
      <h2>Daily P&L attribution</h2><div class="scroll"><table><tr><th>Date</th><th>Qty</th><th>Mark</th><th>Price P&L</th><th>Realized</th><th>Unreal Δ</th><th>Dividends</th><th>Interest</th><th>Comm</th><th>Total</th></tr>${x.daily_pnl.slice().reverse().map(r => `<tr><td>${r.date}</td><td>${fmt.qty(r.quantity)}</td><td>${fmt.px(r.mark)}</td><td>${fmt.signed(r.price_pnl)}</td><td>${fmt.signed(r.realized)}</td><td>${fmt.signed(r.unrealized_change)}</td><td>${fmt.signed(r.dividends)}</td><td>${fmt.signed(r.interest)}</td><td>${fmt.signed(r.commissions)}</td><td>${fmt.signed(r.total)}</td></tr>`).join('')}</table></div>
      <h2>Every ledger entry touching this security</h2><div class="scroll">${entriesTable(x.ledger_entries.slice().reverse())}</div>`;
    bindRows();
  };
  const EXPLAIN_SHORT = { equities: 'equities', commodities: 'cmdty', rates: 'rates', credit: 'credit', dividends: 'divs', bond_interest: 'bond int', financing: 'financing', fees: 'fees' };
  const ACCT = { '1010': 'Cash', '1100': 'Investments - Equities (cost)', '1110': 'Investments - Fixed Income (cost)', '1150': 'Investment Valuation Adjustment', '1200': 'Receivable - Securities Sold', '1210': 'Dividends Receivable', '1220': 'Accrued Interest Receivable - Bonds', '1230': 'Accrued Interest Receivable - Cash', '2100': 'Payable - Securities Purchased', '2300': 'Accrued Interest Payable - Cash', '3000': 'Contributed Capital', '4000': 'Realized Gain/Loss', '4100': 'Unrealized Gain/Loss', '4200': 'Dividend Income', '4300': 'Interest Income', '5000': 'Commissions & Fees', '5100': 'Interest Expense' };
  const accountName = a => ACCT[a.split(':')[0]] || a;

  pages.trading = async () => {
    const [orders, trades] = await Promise.all([api(P() + '/orders'), api(P() + '/trades')]);
    $('#main').innerHTML = `<h1>TRADING <small>instructions execute at the next daily update</small></h1><div class="grid3"><div class="panel"><h2 style="margin-top:0">Instructions blotter</h2>${ordersTable(orders)}</div><div class="panel"><h2 style="margin-top:0">New instruction</h2>${ticketHtml()}</div></div>
      <h2>Trades blotter <small>click a trade for its lifecycle, settlement instruction, ledger entries and events</small></h2>${tradesTable(trades)}`;
    bindTicket(); bindRows();
  };

  function settlementsTable(rows) {
    return `<div class="scroll"><table><tr><th>Instruction</th><th>Trade</th><th>T date</th><th>S date</th><th>Type</th><th>Security</th><th>ISIN</th><th>CUSIP</th><th>Qty</th><th>Cash</th><th>Ccy</th><th>Delivering</th><th>Receiving</th><th>Custodian</th><th>Status</th><th>Fails</th></tr>
      ${rows.map(s => `<tr class="click" data-trade="${s.trade_id}"><td>${s.id}</td><td>${s.trade_id}</td><td>${s.trade_date}</td><td>${s.settlement_date}</td><td>${s.instruction_type}</td><td>${s.security_id}</td><td>${s.isin}</td><td>${s.cusip}</td><td>${fmt.qty(s.quantity)}</td><td>${fmt.money(s.cash_amount)}</td><td>${s.currency}</td><td class="l">${esc(s.delivering_party)}</td><td class="l">${esc(s.receiving_party)}</td><td class="l">${esc(s.custodian)}</td><td>${statusBadge(s.status)}</td><td class="${s.fail_count ? 'neg' : ''}" title="${esc(s.fail_reason || '')}">${s.fail_count || ''}</td></tr>`).join('') || '<tr><td colspan=16 class="muted">no instructions</td></tr>'}</table></div>`;
  }
  pages.settlements = async () => {
    const [sis, cust] = await Promise.all([api(P() + '/settlements'), api(P() + '/custody')]);
    const open = sis.filter(s => ['PENDING', 'MATCHED', 'FAILED'].includes(s.status));
    $('#main').innerHTML = `<h1>SETTLEMENTS & CUSTODY <small>${esc(cust.custodian)} · account ${cust.account} · cycles: ${Object.entries(state.world.settlement_cycles).map(([k, v]) => `${k} T+${v}`).join(', ')}</small></h1>
      <div class="tiles">${tile('Open instructions', open.length)}${tile('Failed', open.filter(s => s.status === 'FAILED').length, 'retry each business day')}${tile('Settled', sis.filter(s => s.status === 'SETTLED').length)}</div>
      <h2>Settlement blotter</h2>${settlementsTable(sis)}
      <div class="grid2" style="margin-top:14px"><div class="panel"><h2 style="margin-top:0">Custody positions</h2><table><tr><th>Security</th><th>ISIN</th><th>Settled</th><th>Trade-date</th><th>Pend rcv</th><th>Pend dlv</th></tr>${cust.holdings.map(h => `<tr class="click" data-href="#/position/${h.security_id}"><td>${h.security_id} <span class="muted">${esc(h.name)}</span></td><td>${h.isin}</td><td>${fmt.qty(h.settled_quantity)}</td><td>${fmt.qty(h.trade_date_quantity)}</td><td>${fmt.qty(h.pending_receive)}</td><td>${fmt.qty(h.pending_deliver)}</td></tr>`).join('') || '<tr><td colspan=6 class="muted">empty</td></tr>'}</table></div>
      <div class="panel"><h2 style="margin-top:0">Securities movements</h2><div class="scroll"><table><tr><th>Date</th><th>Security</th><th>Kind</th><th>Qty</th><th>Balance</th><th>Ref</th><th>Event</th></tr>${cust.movements.map(m => `<tr><td>${m.date}</td><td>${m.security_id}</td><td class="l">${m.kind}</td><td>${fmt.signed(m.quantity, 0)}</td><td>${fmt.qty(m.balance_after)}</td><td class="l muted">${esc(m.reference)}</td><td><a data-event="${m.event_id}">${m.event_id}</a></td></tr>`).join('') || '<tr><td colspan=7 class="muted">none</td></tr>'}</table></div></div></div>`;
    bindRows();
  };

  pages.treasury = async () => {
    const c = await api(P() + '/cash');
    $('#main').innerHTML = `<h1>TREASURY <small>cash ledgers by currency · policy rate ${fmt.pct(c.policy_rate)}</small></h1>
      <div class="tiles">${c.accounts.map(a => tile(`${a.currency} settled cash`, fmt.money(a.settled_balance), `projected ${fmt.money(a.projected)} · accrued int ${fmt.signed(a.accrued_interest)} · deposit ${fmt.pct(a.deposit_rate)} / overdraft ${fmt.pct(a.overdraft_rate)}`)).join('')}</div>
      <div class="grid2" style="margin-top:14px"><div class="panel"><h2 style="margin-top:0">Projected liquidity (15 business days, base currency)</h2><canvas id="liq"></canvas><table style="margin-top:8px"><tr><th>Date</th><th>Flow</th><th>Balance</th><th>Ref</th></tr>${c.projection.map(r => `<tr><td>${r.date}</td><td>${fmt.signed(r.flow)}</td><td class="${r.balance < 0 ? 'neg' : ''}">${fmt.money(r.balance)}</td><td class="l muted">${esc(r.ref)}</td></tr>`).join('')}</table></div>
        <div class="panel"><h2 style="margin-top:0">Upcoming cash flows</h2><table><tr><th>Date</th><th>Kind</th><th>Ref</th><th>Amount</th></tr>${c.upcoming.map(f => `<tr><td>${f.date}</td><td class="l">${f.kind}</td><td class="l">${esc(f.ref)}</td><td>${fmt.signed(f.amount)}</td></tr>`).join('') || '<tr><td colspan=4 class="muted">none</td></tr>'}</table>
        <p class="hint">Cash borrowing, T-bills, money-market instruments and FX conversion arrive with the financing module (Phase 2). Overdrafts today accrue at policy + 150bp.</p></div></div>
      <h2>Cash movements</h2><div class="scroll"><table><tr><th>Date</th><th>Ccy</th><th>Kind</th><th>Amount</th><th>Balance after</th><th>Reference</th><th>Event</th></tr>${c.movements.map(m => `<tr><td>${m.date}</td><td>${m.currency}</td><td class="l">${m.kind}</td><td>${fmt.signed(m.amount)}</td><td>${fmt.money(m.balance_after)}</td><td class="l muted">${esc(m.reference)}</td><td><a data-event="${m.event_id}">${m.event_id}</a></td></tr>`).join('') || '<tr><td colspan=7 class="muted">none</td></tr>'}</table></div>`;
    lineChart($('#liq'), [{ data: c.projection.map(r => r.balance), color: css('--green'), dots: true }], { labels: c.projection.map(r => r.date.slice(5)), h: 160, zero: true });
    bindRows();
  };

  pages['fixed-income'] = async () => {
    const [yc, secs] = await Promise.all([api(W() + '/yield-curve'), api(W() + '/securities')]); const bd = secs.filter(s => s.coupon);
    $('#main').innerHTML = `<h1>FIXED INCOME <small>curve as of ${yc.date}</small></h1>
      <div class="tiles">${tile('Policy rate', fmt.pct(yc.policy_rate))}${tile('2Y', fmt.pct(yc.rates[3], 3))}${tile('10Y', fmt.pct(yc.rates[7], 3))}${tile('30Y', fmt.pct(yc.rates[9], 3))}${tile('2s10s', fmt.bps((yc.rates[7] - yc.rates[3]) * 1e4))}${tile('IG spread', fmt.bps(yc.ig_spread_bps))}${tile('HY spread', fmt.bps(yc.hy_spread_bps))}</div>
      <div class="grid2" style="margin-top:14px"><div class="panel"><h2 style="margin-top:0">Yield curve <span class="legend"><i style="background:${css('--accent')}"></i>today <i style="background:${css('--fg3')}"></i>previous</span></h2><canvas id="curve"></canvas><table style="margin-top:8px"><tr>${yc.tenors.map(t => `<th>${t}Y</th>`).join('')}</tr><tr>${yc.rates.map((r, i) => `<td>${fmt.pct(r, 3)} <span class="${r - yc.prev_rates[i] >= 0 ? 'neg' : 'pos'}" style="font-size:10px">${((r - yc.prev_rates[i]) * 1e4).toFixed(1)}</span></td>`).join('')}</tr></table></div>
        <div class="panel"><h2 style="margin-top:0">Rates & spreads history <span class="legend"><i style="background:${css('--blue')}"></i>2Y <i style="background:${css('--accent')}"></i>10Y <i style="background:${css('--purple')}"></i>30Y</span></h2><canvas id="rh"></canvas><h2>Credit spreads <span class="legend"><i style="background:${css('--green')}"></i>IG <i style="background:${css('--red')}"></i>HY (bp)</span></h2><canvas id="sh"></canvas></div></div>
      <h2>Bond universe</h2><table><tr><th>ID</th><th>Name</th><th>Rating</th><th>Coupon</th><th>Maturity</th><th>Clean</th><th>Dirty</th><th>YTM</th><th>Mac dur</th><th>Mod dur</th><th>Convexity</th><th>DV01/100</th><th>Bench yld</th><th>Spread</th><th>1y PD</th></tr>${bd.map(s => `<tr class="click" data-href="#/security/${s.id}"><td><b>${s.id}</b></td><td class="l">${esc(s.name)}</td><td>${s.rating}</td><td>${fmt.pct(s.coupon, 3)}</td><td>${s.maturity}</td><td>${fmt.px(s.last)}</td><td>${s.dirty_price.toFixed(4)}</td><td>${fmt.pct(s.ytm, 3)}</td><td>${s.macaulay_duration.toFixed(2)}</td><td>${s.modified_duration.toFixed(2)}</td><td>${s.convexity.toFixed(1)}</td><td>${s.dv01_per_100.toFixed(4)}</td><td>${fmt.pct(s.benchmark_yield, 3)}</td><td>${fmt.bps(s.spread_to_curve_bps)}</td><td>${fmt.pct(s.default_probability_1y)}</td></tr>`).join('')}</table>
      <h2>Regime history</h2><div class="mono">${yc.regime_history.map(([d, r]) => `<span class="badge">${d} → ${r}</span> `).join('')}</div>`;
    lineChart($('#curve'), [{ data: yc.prev_rates.map(r => r * 100), color: css('--fg3'), dots: true }, { data: yc.rates.map(r => r * 100), color: css('--accent'), dots: true, width: 2 }], { labels: yc.tenors.map(t => t + 'Y'), fmtY: v => v.toFixed(2) + '%', h: 220 });
    lineChart($('#rh'), [{ data: yc.history.map(h => h['2y'] * 100), color: css('--blue') }, { data: yc.history.map(h => h['10y'] * 100), color: css('--accent') }, { data: yc.history.map(h => h['30y'] * 100), color: css('--purple') }], { labels: yc.history.map(h => h.date.slice(5)), fmtY: v => v.toFixed(2) + '%', h: 160 });
    lineChart($('#sh'), [{ data: yc.history.map(h => h.ig), color: css('--green') }, { data: yc.history.map(h => h.hy), color: css('--red') }], { labels: yc.history.map(h => h.date.slice(5)), fmtY: v => v.toFixed(0), h: 140 });
    bindRows();
  };

  pages.news = async () => {
    const [news, cas] = await Promise.all([api(W() + '/news'), api(W() + '/corporate-actions')]);
    $('#main').innerHTML = `<h1>NEWS & EVENTS</h1><div class="grid2"><div class="panel">${news.map(n => `<div style="margin-bottom:10px"><div class="mono"><span class="muted">${n.date}</span> <span class="badge">${n.category}</span> <b>${esc(n.headline)}</b></div><div class="muted" style="margin-top:2px">${esc(n.body)} ${n.refs.filter(r => !r.startsWith('DIV-')).map(r => `<a href="#/security/${r}">${r}</a>`).join(' ')}</div></div>`).join('') || '<p class="muted mono">no news yet</p>'}</div>
      <div class="panel"><h2 style="margin-top:0">Corporate action calendar</h2><div class="scroll"><table><tr><th>Security</th><th>Type</th><th>Ex</th><th>Pay</th><th>Amount</th><th>Status</th></tr>${cas.map(c => `<tr class="click" data-href="#/security/${c.security_id}"><td>${c.security_id}</td><td class="l">${c.action_type}</td><td>${c.ex_date}</td><td>${c.pay_date}</td><td>${c.amount_per_unit}</td><td>${statusBadge(c.status)}</td></tr>`).join('')}</table></div></div></div>`;
    bindRows();
  };

  pages.accounting = async (arg) => {
    const q = new URLSearchParams((location.hash.split('?')[1]) || ''); const acct = q.get('account') || '', sec = q.get('security') || '';
    const [bs, led] = await Promise.all([api(P() + '/balance-sheet'), api(P() + `/ledger?limit=300${acct ? '&account=' + acct : ''}${sec ? '&security_id=' + sec : ''}`)]);
    const sec_ = (title, rows) => `<h2>${title}</h2><table>${rows.map(r => `<tr class="click" data-href="#/accounting?account=${r.account}"><td>${r.account}</td><td class="l">${esc(r.name)}</td><td>${fmt.money(r.balance)}</td></tr>`).join('') || '<tr><td class="muted">—</td></tr>'}</table>`;
    $('#main').innerHTML = `<h1>ACCOUNTING <small>double-entry general ledger · ${led.count} entries · trial balance ${led.trial_balance.balanced ? '<span class="ok">balanced ✓</span>' : '<span class="error">UNBALANCED</span>'} · A − L − E − NI = ${fmt.money(bs.check)}</small></h1>
      <div class="grid3"><div class="panel"><div class="grid2"><div>${sec_('Assets', bs.sections.ASSET)}${sec_('Liabilities', bs.sections.LIABILITY)}${sec_('Capital', bs.sections.EQUITY)}</div><div>${sec_('Income', bs.sections.INCOME)}${sec_('Expenses', bs.sections.EXPENSE)}<div class="kv" style="margin-top:12px"><div>Total assets</div><div>${fmt.money(bs.totals.ASSET)}</div><div>Total liabilities</div><div>${fmt.money(bs.totals.LIABILITY)}</div><div><b>NAV</b></div><div><b>${fmt.money(bs.nav)}</b></div><div>Net income (ITD)</div><div>${fmt.signed(bs.net_income)}</div></div></div></div></div>
        <div class="panel"><h2 style="margin-top:0">Trial balance</h2><div class="scroll"><table><tr><th>Account</th><th>Debit</th><th>Credit</th></tr>${led.trial_balance.rows.map(r => `<tr><td>${r.account} <span class="muted">${esc(r.name)}</span></td><td>${r.debit ? fmt.money(r.debit) : ''}</td><td>${r.credit ? fmt.money(r.credit) : ''}</td></tr>`).join('')}<tr><th>Total</th><th>${fmt.money(led.trial_balance.total_debits)}</th><th>${fmt.money(led.trial_balance.total_credits)}</th></tr></table></div></div></div>
      <h2>Journal <small>${acct ? `filtered to account ${acct} ` : ''}${sec ? `security ${sec} ` : ''}<a href="#/accounting">clear</a> · newest first</small></h2>
      <div class="row" style="margin-bottom:8px"><select id="acctSel"><option value="">all accounts</option>${led.chart.map(c => `<option value="${c.code}" ${c.code === acct ? 'selected' : ''}>${c.code} ${esc(c.name)}</option>`).join('')}</select><input id="secFilter" placeholder="security" value="${esc(sec)}"><button class="btn" id="filterGo">filter</button></div>
      <div class="scroll" style="max-height:600px">${entriesTable(led.entries)}</div>`;
    $('#filterGo').addEventListener('click', () => { const a = $('#acctSel').value, s = $('#secFilter').value.trim().toUpperCase(); location.hash = `#/accounting?${a ? 'account=' + a : ''}${s ? '&security=' + s : ''}`; });
    bindRows();
  };

  pages.audit = async () => {
    const q = new URLSearchParams((location.hash.split('?')[1]) || ''); const type = q.get('type') || '', search = q.get('q') || '', offset = +(q.get('offset') || 0);
    const x = await api(W() + `/events?limit=100&offset=${offset}${type ? '&type=' + type : ''}${search ? '&q=' + encodeURIComponent(search) : ''}`);
    $('#main').innerHTML = `<h1>AUDIT TRAIL <small>${x.total} events · immutable, append-only · state is rebuilt by replaying them</small></h1>
      <div class="row" style="margin-bottom:8px"><select id="typeSel"><option value="">all types</option>${x.types.map(t => `<option ${t === type ? 'selected' : ''}>${t}</option>`).join('')}</select><input id="qIn" placeholder="search payload (e.g. TRD-000031, NVRA)" value="${esc(search)}" style="width:320px"><button class="btn" id="qGo">filter</button>
        <span class="spacer"></span>${offset > 0 ? `<a href="#/audit?type=${type}&q=${encodeURIComponent(search)}&offset=${Math.max(0, offset - 100)}">← newer</a>` : ''} ${offset + 100 < x.total ? `<a href="#/audit?type=${type}&q=${encodeURIComponent(search)}&offset=${offset + 100}">older →</a>` : ''}</div>
      <table><tr><th>Seq</th><th>Event</th><th>Sim date</th><th>Type</th><th>Portfolio</th><th>Caused by</th><th>Children</th><th>Summary</th></tr>${x.events.map(e => `<tr class="click" data-event="${e.id}"><td>${e.seq}</td><td>${e.id}</td><td>${e.sim_date}</td><td class="l">${e.type}</td><td>${e.portfolio_id || ''}</td><td>${e.cause_id || ''}</td><td>${e.children || ''}</td><td class="l muted">${summ(e)}</td></tr>`).join('')}</table>`;
    $('#qGo').addEventListener('click', () => { location.hash = `#/audit?type=${$('#typeSel').value}&q=${encodeURIComponent($('#qIn').value.trim())}`; });
    bindRows();
  };

  loadWorlds().catch(e => { $('#main').innerHTML = `<p class="error">${esc(e.message)}</p>`; });
})();
