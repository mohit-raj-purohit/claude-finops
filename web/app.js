import * as C from './charts.js';
const {fmtUSD, fmtNum, fmtInt, fmtPct, seriesVar} = C;

/* ============================ state ============================ */
const S = {
  view: 'overview',
  opts: null,
  filter: {start: null, end: null, agents: [], models: [], projects: [], categories: [],
           include_sandbox: true, min_cost: null, min_tokens: null},
  range: '30d',
  metric: 'cost',
  grain: 'day',
  cache: new Map(),
  drawerStack: [],
};
const $ = (s, r = document) => r.querySelector(s);
const h = (html) => { const t = document.createElement('template');
  t.innerHTML = html.trim(); return t.content.firstElementChild; };
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

const qs = () => {
  const f = S.filter, p = new URLSearchParams();
  if (f.start) p.set('start', f.start);
  if (f.end) p.set('end', f.end);
  for (const k of ['agents', 'models', 'projects', 'categories'])
    if (f[k].length) p.set(k, f[k].join(','));
  if (!f.include_sandbox) p.set('include_sandbox', '0');
  if (f.min_cost) p.set('min_cost', f.min_cost);
  if (f.min_tokens) p.set('min_tokens', f.min_tokens);
  return p.toString();
};
async function api(route, extra = '') {
  const url = `/api/${route}?${qs()}${extra}`;
  if (S.cache.has(url)) return S.cache.get(url);
  // never cache failures: a response from a restarting server would stick until reload
  const pr = fetch(url).then(r => r.json()).then(d => {
    if (d && d.error) throw new Error(`API /${route} failed:\n${d.error}`);
    return d;
  }).catch(e => { S.cache.delete(url); throw e; });
  S.cache.set(url, pr);
  return pr;
}
const bust = () => S.cache.clear();

/* ============================ formatting helpers ============================ */
const naLabel = () => `Unavailable from connected ${agentWord()} data`;
const NA = () => `<span class="na">${esc(naLabel())}</span>`;
const BADGE = {actual: '<span class="badge">Actual</span>',
  estimated: '<span class="badge est">Estimated</span>',
  forecast: '<span class="badge fc">Forecast</span>',
  recommendation: '<span class="badge rec">Recommendation</span>'};
const statusGlyph = s => ({healthy: '🟢', high: '🟡', approaching: '🟠', critical: '🔴'}[s] || '⚪');
const statusChip = (s, txt) => `<span class="status ${s}"><span class="glyph">${statusGlyph(s)}</span>${esc(txt || s)}</span>`;
const modelColor = m => {
  const list = (S.opts?.models || []).map(x => x.model);
  const i = list.indexOf(m);
  return seriesVar(i < 0 ? 7 : i);
};
const modelName = m => S.opts?.pricing?.models?.[m]?.display_name || m;
// Categories keep one colour across every filter selection: the slot comes from the
// global category list, never from this view's ranking.
const catColor = c => {
  const list = (S.opts?.categories || []).map(x => x.category);
  const i = list.indexOf(c);
  return seriesVar(i < 0 ? 7 : i);
};
const dur = s => s == null ? '—' : s < 60 ? `${Math.round(s)}s`
  : s < 3600 ? `${Math.floor(s/60)}m ${Math.round(s%60)}s`
  : `${Math.floor(s/3600)}h ${Math.round((s%3600)/60)}m`;
const shortDay = d => (d || '').slice(5);
const shortId = s => (s || '').slice(0, 8);

function kpi(label, value, detail, opts = {}) {
  const na = value == null;
  return `<div class="kpi${na ? ' na' : ''}">
    <div class="l">${esc(label)}${opts.badge ? ' ' + opts.badge : ''}</div>
    <div class="v${opts.small ? ' sm' : ''}">${na ? esc(naLabel()) : value}</div>
    ${detail ? `<div class="d">${detail}</div>` : ''}</div>`;
}
function card(title, bodyHtml, opts = {}) {
  return `<section class="card"${opts.style ? ` style="${opts.style}"` : ''}>
    <header><h3>${esc(title)}</h3>${opts.badge || ''}
      ${opts.hint ? `<span class="hint">${esc(opts.hint)}</span>` : ''}
      <span class="spacer"></span>${opts.actions || ''}</header>
    <div class="body${opts.flush ? ' flush' : ''}">${bodyHtml}</div>
    ${opts.footer ? `<footer>${opts.footer}</footer>` : ''}</section>`;
}
function table(cols, rows, opts = {}) {
  if (!rows.length) return '<div class="empty">Nothing to show for the current filters</div>';
  const th = cols.map((c, i) => `<th class="${c.num ? 'num ' : ''}nosort">${esc(c.h)}</th>`).join('');
  const tb = rows.map((r, ri) => `<tr class="${opts.onRow ? 'clickable' : ''}" data-i="${ri}">${
    cols.map(c => `<td class="${c.num ? 'num ' : ''}${c.trunc ? 'trunc' : ''}"${
      c.title ? ` title="${esc(c.title(r))}"` : ''}>${c.f(r)}</td>`).join('')}</tr>`).join('');
  return `<div class="tbl-wrap"><table class="tbl"><thead><tr>${th}</tr></thead>
    <tbody>${tb}</tbody></table></div>`;
}
function wireTable(host, rows, onRow) {
  if (!onRow || !host) return;
  host.querySelectorAll('table.tbl tbody tr.clickable').forEach(tr =>
    tr.onclick = () => onRow(rows[+tr.dataset.i]));
}

/* ============================ chrome ============================ */
const NAV = [
  ['Command center', [
    ['overview', '◧', 'Executive overview'],
    ['advisor', '✦', 'What should I do?'],
    ['agents', '◎', 'Agents'],
    ['live', '⏻', 'Running sessions'],
    ['scorecard', '◉', 'FinOps scorecard'],
  ]],
  ['Usage', [
    ['usage', '▤', 'Usage timeline'],
    ['burn', '◑', 'Burn rate & limits', 'priced'],
    ['models', '◈', 'Model analysis'],
    ['context', '▭', 'Context & cache'],
  ]],
  ['Drill-down', [
    ['projects', '▣', 'Projects'],
    ['sessions', '▦', 'Sessions'],
    ['prompts', '☰', 'Prompt explorer'],
    ['rankings', '↕', 'Cost rankings'],
    ['categories', '◐', 'Prompt intelligence'],
    ['developer', '⌘', 'Developer activity', 'priced'],
  ]],
  ['Optimize', [
    ['diagnose', '✚', 'Why so many tokens?', 'priced'],
    ['attribution', '⧉', 'Who used the tokens', 'priced'],
    ['waste', '⚠', 'Waste detection'],
    ['modelswitch', '⇄', 'Model switch', 'priced'],
    ['freemodels', '◇', 'Free models', 'claude'],
    ['compare', '⚖', 'Compare models', 'priced'],
    ['toolkit', '✎', 'Skills & MCP', 'claude'],
    ['recommendations', '↯', 'Recommendations'],
    ['anomalies', '⚡', 'Anomalies'],
  ]],
  ['Plan', [
    ['forecast', '◭', 'Forecast', 'priced'],
    ['budgets', '⊞', 'Budgets', 'priced'],
    ['cloud', '☁', 'Billed vs local', 'cloud'],
    ['exports', '⇩', 'Export & data'],
  ]],
];

function shell() {
  document.body.innerHTML = `<div class="app">
    <aside class="sidebar">
      <div class="brand"><div class="mark"><span class="dot"></span>Claude FinOps</div>
        <div class="sub">Command Center</div></div>
      <nav class="nav">${NAV.map(([g, items], gi) => `<div class="group g${gi}">${g}</div>` +
        items.map(([id, ic, label]) =>
          `<a data-view="${id}" class="g${gi}${id === 'diagnose' ? ' start' : ''}"><span class="ic">${ic}</span>${label}<span class="nb" data-nb="${id}"></span></a>`).join('')).join('')}
      </nav>
      <div class="who" id="who"></div></aside>
    <div class="main">
      <header class="topbar">
        <div class="r1">
          <h1 id="ttl">Executive overview</h1>
          <div class="crumbs" id="crumbs"></div>
          <span class="spacer"></span>
          <div class="search"><span class="mag">⌕</span>
            <input id="gsearch" placeholder="Search prompts, sessions, models, dates…"></div>
          <button class="iconbtn upd" id="upd" hidden></button>
          <button class="iconbtn" id="tour-btn" title="Walk through this dashboard">? Tour</button>
          <button class="iconbtn" id="theme" title="Toggle theme">◐</button>
          <button class="iconbtn" id="refresh" title="Reload data">↻</button>
          <button class="act" id="sync" title="Re-read every agent's local data so the dashboard is current">⟳ Sync</button>
        </div>
        <div class="filters" id="filters"></div>
      </header>
      <div class="page" id="page"><div class="loading">Loading warehouse…</div></div>
    </div></div>`;

  document.querySelectorAll('.nav a').forEach(a =>
    a.onclick = () => go(a.dataset.view));
  $('#tour-btn').onclick = () => tourForView();
  $('#theme').onclick = () => {
    const cur = document.documentElement.getAttribute('data-theme');
    const next = cur === 'dark' ? 'light' : cur === 'light' ? 'dark'
      : (matchMedia('(prefers-color-scheme: dark)').matches ? 'light' : 'dark');
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('finops-theme', next);
    render();
  };
  $('#refresh').onclick = () => { bust(); render(); };
  updateChip();
  $('#sync').onclick = runSync;
  syncLabel();
  let t;
  $('#gsearch').oninput = e => { clearTimeout(t); const v = e.target.value;
    t = setTimeout(() => { if (v.trim().length >= 2) { S.searchTerm = v; go('search'); }
      else if (S.view === 'search') go('overview'); }, 260); };
}

/* ---------- update notice ----------
   npm cannot push a new release at anyone, so the server asks the registry once
   a day and we surface the answer here. Silent when you are current, when the
   check is switched off, and when it simply could not reach the registry. */
async function updateChip() {
  const el = $('#upd');
  if (!el) return;
  let u;
  try { u = await fetch('/api/update').then(r => r.json()); } catch { return; }
  if (!u || !u.update_available) return;
  el.hidden = false;
  el.textContent = `↑ v${u.latest} available`;
  el.title = `You are on ${u.current}. Click to copy:  ${u.command}`;
  el.onclick = async () => {
    try {
      await navigator.clipboard.writeText(u.command);
      el.textContent = '✓ command copied';
      setTimeout(() => { el.textContent = `↑ v${u.latest} available`; }, 2200);
    } catch { prompt('Run this to upgrade:', u.command); }
  };
}

/* ---------- filter bar ---------- */
const RANGES = [['today', 'Today'], ['7d', '7 days'], ['14d', '14 days'], ['30d', '30 days'],
  ['period', 'Billing period'], ['all', 'All time'], ['custom', 'Custom']];

function applyRange(r) {
  S.range = r;
  const last = S.opts.date_range.last, bp = S.opts.billing_period;
  const dayShift = n => { const d = new Date(last + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() - n + 1); return d.toISOString().slice(0, 10); };
  if (r === 'today') { S.filter.start = last; S.filter.end = last; }
  else if (r === '7d') { S.filter.start = dayShift(7); S.filter.end = last; }
  else if (r === '14d') { S.filter.start = dayShift(14); S.filter.end = last; }
  else if (r === '30d') { S.filter.start = dayShift(30); S.filter.end = last; }
  else if (r === 'period') { S.filter.start = bp.start; S.filter.end = bp.end; }
  else if (r === 'all') { S.filter.start = null; S.filter.end = null; }
}

function filterBar() {
  const f = S.filter, o = S.opts;
  const nSel = a => a.length ? `<span class="n">${a.length}</span>` : '';
  const ag = o.agents || [];
  $('#filters').innerHTML = `
    ${ag.length > 1 ? `<span class="note" style="margin-right:2px">Agent</span>
      ${ag.map(a => `<button class="chip ${f.agents.includes(a.id) ? 'on' : ''}" data-agent="${esc(a.id)}"
        title="${esc(a.note)}${a.requests ? '' : ' (no usage recorded)'}">${esc(a.name)}${a.requests ? '' : ' ·'}</button>`).join('')}
      <button class="chip ${f.agents.length === ag.length ? 'on' : ''}" data-agent="*" title="Every agent together">All</button>
      <span class="divider"></span>` : ''}
    ${RANGES.map(([k, l]) => `<button class="chip ${S.range === k ? 'on' : ''}"
      data-range="${k}">${l}</button>`).join('')}
    <span class="divider"></span>
    <div class="chipsel"><button class="chip ${f.models.length ? 'on' : ''}" data-pop="models">
      Model ${nSel(f.models)} ▾</button></div>
    <div class="chipsel"><button class="chip ${f.projects.length ? 'on' : ''}" data-pop="projects">
      Project ${nSel(f.projects)} ▾</button></div>
    <div class="chipsel"><button class="chip ${f.categories.length ? 'on' : ''}" data-pop="categories">
      Category ${nSel(f.categories)} ▾</button></div>
    <div class="chipsel"><button class="chip ${(f.min_cost || f.min_tokens) ? 'on' : ''}"
      data-pop="thresholds">Thresholds ▾</button></div>
    <button class="chip ${f.include_sandbox ? '' : 'on'}" id="sbx">
      ${f.include_sandbox ? 'Including' : 'Excluding'} sandbox agents</button>
    ${(f.models.length || f.projects.length || f.categories.length || f.min_cost || f.min_tokens)
      ? '<button class="chip" id="clr">Clear ✕</button>' : ''}
    <span class="spacer"></span>
    <span class="note">${o.date_range.first} → ${o.date_range.last} ·
      ${esc(o.meta.transcript_files)} transcripts · built ${esc((o.meta.built_at||'').slice(0,16).replace('T',' '))}</span>`;

  $('#filters').querySelectorAll('[data-range]').forEach(b =>
    b.onclick = () => { if (b.dataset.range === 'custom') return openCustom(b);
      applyRange(b.dataset.range); bust(); render(); });
  $('#filters').querySelectorAll('[data-agent]').forEach(b => b.onclick = e => {
    const id = b.dataset.agent;
    // click = only this agent; Cmd/Ctrl/Shift-click = add or remove it (multi-agent view)
    if (id === '*') f.agents = ag.map(a => a.id);
    else if (e.metaKey || e.ctrlKey || e.shiftKey)
      f.agents = f.agents.includes(id) ? f.agents.filter(x => x !== id) : [...f.agents, id];
    else f.agents = [id];
    if (!f.agents.length) f.agents = [id === '*' ? ag[0].id : id];
    try { localStorage.setItem('finops-agents', JSON.stringify(f.agents)); } catch (_) {}
    bust(); render();
  });
  $('#sbx').onclick = () => { f.include_sandbox = !f.include_sandbox; bust(); render(); };
  const clr = $('#clr'); if (clr) clr.onclick = () => {
    f.models = []; f.projects = []; f.categories = []; f.min_cost = null; f.min_tokens = null;
    bust(); render(); };
  $('#filters').querySelectorAll('[data-pop]').forEach(b => b.onclick = e => {
    e.stopPropagation(); openPop(b, b.dataset.pop); });
}

function closePops() { document.querySelectorAll('.pop').forEach(p => p.remove()); }
document.addEventListener('click', e => { if (!e.target.closest('.pop')) closePops(); });

function openPop(btn, kind) {
  const open = btn.parentElement.querySelector('.pop');
  closePops(); if (open) return;
  const f = S.filter, o = S.opts;
  let inner = '';
  if (kind === 'thresholds') {
    inner = `<div class="hd">Minimum estimated cost (USD)</div>
      <input type="number" step="0.01" id="mc" value="${f.min_cost ?? ''}" placeholder="any">
      <div class="hd">Minimum billable tokens</div>
      <input type="number" id="mt" value="${f.min_tokens ?? ''}" placeholder="any">
      <button class="chip on" id="applyth" style="width:100%;justify-content:center">Apply</button>`;
  } else {
    const key = kind, src = kind === 'models' ? o.models.map(m => [m.model, modelName(m.model), m.n])
      : kind === 'projects' ? o.projects.map(p => [String(p.project_id),
          p.name + (p.is_sandbox ? ' · sandbox' : ''), p.n])
      : o.categories.map(c => [c.category, c.category, c.n]);
    inner = src.map(([v, l, n]) => `<label><input type="checkbox" value="${esc(v)}"
      ${f[key].includes(v) ? 'checked' : ''}> ${esc(l)}
      <span class="spacer"></span><span class="n" style="color:var(--muted)">${fmtInt(n)}</span></label>`).join('');
  }
  const pop = h(`<div class="pop">${inner}</div>`);
  btn.parentElement.appendChild(pop);
  if (kind === 'thresholds') {
    pop.querySelector('#applyth').onclick = () => {
      f.min_cost = pop.querySelector('#mc').value || null;
      f.min_tokens = pop.querySelector('#mt').value || null;
      closePops(); bust(); render(); };
  } else {
    pop.querySelectorAll('input').forEach(i => i.onchange = () => {
      const set = new Set(f[kind]);
      i.checked ? set.add(i.value) : set.delete(i.value);
      f[kind] = [...set]; bust(); render(); });
  }
}
function openCustom(btn) {
  closePops();
  const pop = h(`<div class="pop"><div class="hd">From</div>
    <input type="date" id="cs" value="${S.filter.start || S.opts.date_range.first}">
    <div class="hd">To</div>
    <input type="date" id="ce" value="${S.filter.end || S.opts.date_range.last}">
    <button class="chip on" id="ca" style="width:100%;justify-content:center">Apply</button></div>`);
  btn.parentElement.insertBefore(pop, btn.nextSibling);
  pop.onclick = e => e.stopPropagation();
  pop.querySelector('#ca').onclick = () => {
    S.filter.start = pop.querySelector('#cs').value;
    S.filter.end = pop.querySelector('#ce').value;
    S.range = 'custom'; closePops(); bust(); render(); };
}

/* ============================ drawers ============================ */
function closeDrawer() {
  document.querySelectorAll('.scrim,.drawer').forEach(e => e.remove());
  S.drawerStack = [];
}
function drawer(title, bodyHtml, sub) {
  C.hideTip();
  document.querySelectorAll('.scrim,.drawer').forEach(e => e.remove());
  const scrim = h('<div class="scrim"></div>');
  const d = h(`<aside class="drawer"><header>
      <h2>${esc(title)}</h2>${sub ? `<span class="crumbs">${sub}</span>` : ''}
      <span class="spacer"></span>
      <button class="iconbtn" data-x>Close ✕</button></header>
    <div class="content">${bodyHtml}</div></aside>`);
  scrim.onclick = closeDrawer;
  d.querySelector('[data-x]').onclick = closeDrawer;
  document.body.append(scrim, d);
  return d;
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

async function openPrompt(id) {
  const d = drawer('Prompt detail', '<div class="loading">Loading…</div>');
  const p = await fetch(`/api/prompt/${id}`).then(r => r.json());
  const adv = p.advisor || {};
  d.querySelector('.content').innerHTML = `
    <div class="grid g4">
      ${kpi('Estimated cost', fmtUSD(p.est_cost_usd), null, {badge: BADGE.estimated})}
      ${kpi('Billable tokens', fmtInt(p.billable_tokens))}
      ${kpi('Output tokens', fmtInt(p.output_tokens))}
      ${kpi('Peak context', fmtInt(p.max_context_tokens))}
    </div>
    ${card('Prompt', `<div class="prompt-text">${esc(p.text)}</div>
      <div class="mt" style="margin-top:8px;display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--muted)">
        <span>${esc(p.ts || '')}</span><span class="pill">${esc(p.category)}</span>
        <span>confidence ${(100*(p.category_confidence||0)).toFixed(0)}%</span>
        ${(p.category_evidence||[]).length ? `<span>matched: ${esc(p.category_evidence.join(', '))}</span>` : ''}
        <span>${fmtInt(p.char_len)} chars · ${fmtInt(p.word_len)} words</span>
        ${p.source ? `<span class="pill">${esc(p.source)}</span>` : ''}
      </div>`, {badge: BADGE.actual})}
    ${card('Cost drivers & optimization advice', adv.available ? `
      <div class="stack">
        <div><b style="font-size:12px">Why this was expensive</b>
          <ul style="margin:5px 0 0 18px;font-size:12px;color:var(--text-2)">
            ${adv.why_expensive.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>
        <div><b style="font-size:12px">Suggested changes</b>
          <ul style="margin:5px 0 0 18px;font-size:12px;color:var(--text-2)">
            ${adv.suggestions.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>
        <div class="grid g3">
          ${kpi('Est. token reduction', '~' + adv.estimated_token_reduction_pct + '%', null, {badge: BADGE.recommendation})}
          ${kpi('Est. cost reduction', '~' + adv.estimated_cost_reduction_pct + '%')}
          ${kpi('Est. cost avoided', '~' + fmtUSD(adv.estimated_cost_reduction_usd))}
        </div></div>`
      : `<div class="na">${esc(adv.message || 'No analysis available')}</div>`,
      {badge: BADGE.recommendation,
       footer: adv.available ? esc(adv.disclaimer) : null})}
    ${card('Requests in this turn', table([
      {h: 'Time', f: r => `<span class="mono">${esc((r.ts||'').slice(11,19))}</span>`},
      {h: 'Model', f: r => `<span class="swatch" style="background:${modelColor(r.model)}"></span>${esc(modelName(r.model))}`},
      {h: 'Effort', f: r => `<span class="pill">${esc(r.effort || '—')}</span>`},
      {h: 'Input', num: 1, f: r => fmtInt(r.input_tokens)},
      {h: 'Output', num: 1, f: r => fmtInt(r.output_tokens)},
      {h: 'Thinking', num: 1, f: r => fmtInt(r.thinking_tokens)},
      {h: 'Cache read', num: 1, f: r => fmtInt(r.cache_read_tokens)},
      {h: 'Cache write', num: 1, f: r => fmtInt(r.cache_write_tokens)},
      {h: 'Context', num: 1, f: r => fmtInt(r.context_tokens)},
      {h: 'Latency', num: 1, f: r => r.latency_ms ? (r.latency_ms/1000).toFixed(1)+'s' : '—'},
      {h: 'Est. cost', num: 1, f: r => fmtUSD(r.est_cost_usd)},
    ], p.requests), {badge: BADGE.actual, hint: 'cost estimated'})}
    <div class="grid g2">
      ${card('Tool calls', p.tool_summary.length ? table([
        {h: 'Tool', f: r => esc(r.name)}, {h: 'Calls', num: 1, f: r => fmtInt(r.n)}],
        p.tool_summary) : '<div class="na">No tool calls recorded</div>', {badge: BADGE.actual})}
      ${card('Files touched', p.files.length ? table([
        {h: 'File', trunc: 1, title: r => r.path, f: r => `<span class="mono">${esc(r.path)}</span>`},
        {h: 'Op', f: r => `<span class="pill">${esc(r.op)}</span>`},
        {h: '#', num: 1, f: r => fmtInt(r.n)}], p.files)
        : '<div class="na">No files touched</div>', {badge: BADGE.actual})}
    </div>
    ${card('Context', `<dl class="kv">
      <dt>Session</dt><dd><a data-sess="${esc(p.session_id)}">${esc(p.session_title || shortId(p.session_id))}</a></dd>
      <dt>Project</dt><dd>${esc(p.project)}</dd>
      <dt>Git branch</dt><dd>${p.git_branch ? esc(p.git_branch) : NA()}</dd>
      <dt>Models</dt><dd>${esc(p.models || '—')}</dd>
      <dt>Tool calls</dt><dd>${fmtInt(p.tool_calls)}</dd>
      <dt>Avg latency</dt><dd>${p.latency_ms ? (p.latency_ms/1000).toFixed(1)+'s' : NA()}</dd>
      <dt>Response text</dt><dd>${NA()} — transcripts are parsed for usage metadata, not stored replies</dd>
    </dl>`)}`;
  const link = d.querySelector('[data-sess]');
  if (link) link.onclick = () => openSession(link.dataset.sess);
}

async function openSession(id) {
  const d = drawer('Session detail', '<div class="loading">Loading…</div>', esc(shortId(id)));
  const s = await fetch(`/api/session/${encodeURIComponent(id)}`).then(r => r.json());
  const cont = d.querySelector('.content');
  cont.innerHTML = `
    <div class="grid g4">
      ${kpi('Estimated cost', fmtUSD(s.est_cost_usd), null, {badge: BADGE.estimated})}
      ${kpi('Billable tokens', fmtInt(s.billable_tokens))}
      ${kpi('Prompts / requests', `${fmtInt(s.prompt_count)} / ${fmtInt(s.request_count)}`)}
      ${kpi('Duration', dur(s.duration_s))}
      ${kpi('Peak context', fmtInt(s.max_context_tokens))}
      ${kpi('Avg context', fmtInt(s.avg_context_tokens))}
      ${kpi('Cost / prompt', s.prompt_count ? fmtUSD(s.est_cost_usd / s.prompt_count) : null)}
      ${kpi('Files touched', fmtInt(s.files_touched))}
    </div>
    ${card('Context & cost per request', '<div class="chart" id="sesschart"></div>' +
      '<div class="legend" id="sessleg"></div>', {badge: BADGE.actual,
      hint: 'context tokens actual, cost estimated'})}
    ${card('Prompts in this session', table([
      {h: 'Time', f: r => `<span class="mono">${esc((r.ts||'').slice(11,16))}</span>`},
      {h: 'Prompt', trunc: 1, title: r => r.preview, f: r => esc(r.preview)},
      {h: 'Category', f: r => `<span class="pill">${esc(r.category)}</span>`},
      {h: 'Tools', num: 1, f: r => fmtInt(r.tool_calls)},
      {h: 'Peak ctx', num: 1, f: r => fmtInt(r.max_context)},
      {h: 'Tokens', num: 1, f: r => fmtNum(r.ptokens)},
      {h: 'Est. cost', num: 1, f: r => fmtUSD(r.pcost)},
    ], s.prompts, {onRow: 1}), {badge: BADGE.estimated, flush: 0})}
    <div class="grid g2">
      ${card('Tools used', s.tools.length ? table([{h: 'Tool', f: r => esc(r.name)},
        {h: 'Calls', num: 1, f: r => fmtInt(r.n)}], s.tools) : '<div class="na">None</div>')}
      ${card('Files touched', s.files.length ? table([
        {h: 'File', trunc: 1, title: r => r.path, f: r => `<span class="mono">${esc(r.path)}</span>`},
        {h: 'Ops', f: r => esc(r.ops)}, {h: '#', num: 1, f: r => fmtInt(r.n)}], s.files)
        : '<div class="na">None</div>')}
    </div>
    ${card('Session metadata', `<dl class="kv">
      <dt>Session ID</dt><dd class="mono">${esc(s.id)}</dd>
      <dt>Title</dt><dd>${s.title ? esc(s.title) : NA()}</dd>
      <dt>Project</dt><dd>${esc(s.project)}</dd>
      <dt>Git branch</dt><dd>${s.git_branch ? esc(s.git_branch) : NA()}</dd>
      <dt>CLI version</dt><dd>${s.cli_version ? esc(s.cli_version) : NA()}</dd>
      <dt>Started / ended</dt><dd>${esc(s.started_at||'—')} → ${esc(s.ended_at||'—')}</dd>
      <dt>Models</dt><dd>${esc(s.models || '—')}</dd>
      <dt>Lines changed</dt><dd>${NA()}</dd>
      <dt>Commits / PRs</dt><dd>${NA()}</dd>
    </dl>`)}`;
  wireTable(cont, s.prompts, r => openPrompt(r.prompt_id));
  const tl = s.timeline.map((r, i) => ({i, ...r}));
  C.timeSeries($('#sesschart', cont), {rows: tl, x: 'i', type: 'area',
    series: [{key: 'context_tokens', label: 'Context tokens', color: seriesVar(0)}],
    fmt: fmtNum, height: 190, xLabel: v => '#' + (v + 1)});
  C.legend($('#sessleg', cont), [{label: 'Context tokens per request', color: seriesVar(0)}]);
}


/* ---------- charts added to table-first pages ---------- */
// Inserts a chart card after the page's KPI row (or at the top) and draws into it.
function addChart(page, title, draw, opts = {}) {
  // .chart carries every chart style (text fill, gridlines, axes); .cchart is
  // just the hook this helper looks up. Missing .chart left SVG labels at the
  // browser default fill — black text, invisible on the dark theme.
  const c = h(card(title, '<div class="chart cchart"></div>', opts));
  const anchor = opts.after ? page.querySelector(opts.after) : page.querySelector(':scope > .grid');
  if (anchor) anchor.after(c); else page.prepend(c);
  draw(c.querySelector('.cchart'));
}
const clip = (t, n = 48) => { t = String(t || ''); return t.length > n ? t.slice(0, n - 1) + '…' : t; };

/* ============================ views ============================ */
const VIEWS = {};

/* ---------- overview ---------- */
VIEWS.overview = async (page) => {
  const b = await api('bundle');
  const {overview: o, burn, timeline, models, projects, categories, waste, scorecard,
         advisor, forecast, leaderboards} = b;
  const bp = o.billing_period;
  const alloc = burn.allowances.cost;
  const st = alloc.configured ? alloc.status : null;
  const fcExp = forecast.available ? forecast.scenarios.expected.end_of_period_cost : null;

  page.innerHTML = `
    <div class="grid g5">
      ${kpi('Estimated spend', fmtUSD(o.est_cost_usd),
        `${fmtUSD(o.cost_today.c)} today · ${fmtUSD(o.cost_week.c)} last 7d`, {badge: BADGE.estimated})}
      ${kpi('Billable tokens', fmtNum(o.billable_tokens),
        `${fmtNum(o.output_tokens)} output · ${fmtNum(o.cache_read_tokens)} cache read`,
        {badge: BADGE.actual})}
      ${alloc.configured
        ? kpi('Plan usage', fmtPct(alloc.used_pct), statusChip(alloc.status), {badge: BADGE.estimated})
        : kpi('Plan usage', null, 'Set monthly_cost_allowance_usd in config/settings.json')}
      ${alloc.configured
        ? kpi('Remaining', fmtUSD(alloc.remaining),
            `${alloc.days_until_limit ?? '—'} days at current burn`, {badge: BADGE.estimated})
        : kpi('Remaining allowance', null, `No plan limit exposed by ${agentWord()} data`)}
      ${kpi('Period forecast', fcExp == null ? null : fmtUSD(fcExp),
        `${bp.remaining_days} days left in ${bp.start} → ${bp.end}`, {badge: BADGE.forecast})}
    </div>

    <div class="grid g32">
      ${card('Usage burn rate', `<div class="grid g4" style="gap:8px">
          ${kpi('Period to date', fmtUSD(burn.used.cost_usd), null, {small: 1})}
          ${kpi('Daily average', fmtUSD(burn.daily_avg_cost), null, {small: 1})}
          ${kpi('7-day burn rate', fmtUSD(burn.avg7_cost), 'per day', {small: 1})}
          ${kpi('Projected period', fmtUSD(burn.projected_period_cost),
            `at ${fmtUSD(burn.burn_rate_cost_per_day)}/day`, {small: 1})}
        </div>
        <div style="display:flex;gap:18px;align-items:center;margin-top:10px;flex-wrap:wrap">
          <div class="chart" id="gauge" style="width:180px;flex:none"></div>
          <div style="flex:1;min-width:220px" id="burnnotes"></div>
        </div>`, {badge: BADGE.estimated,
          footer: esc(burn.forecast_note || '')})}
      ${card('Cost trend', `<div class="legend" id="trendleg"></div>
        <div class="chart" id="trend"></div>`, {badge: BADGE.estimated,
        actions: `<span class="note">click a day to filter</span>`})}
    </div>

    <div class="grid g2">
      ${card('Model cost', `<div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
        <div class="chart" id="mdonut"></div><div style="flex:1;min-width:230px" id="mtable"></div></div>`,
        {badge: BADGE.estimated})}
      ${card('Project cost', '<div class="chart" id="pbars"></div>', {badge: BADGE.estimated,
        hint: 'top 10 · click to drill in'})}
    </div>

    <div class="grid g2">
      ${card('Top 10 most expensive prompts', '<div id="topprompts"></div>',
        {badge: BADGE.estimated, hint: 'click a row for full prompt + advice'})}
      ${card('Most expensive sessions', '<div id="topsessions"></div>', {badge: BADGE.estimated})}
    </div>

    <div class="grid g2">
      ${card('Waste detection', `<div class="stack">${waste.findings.slice(0, 5).map(f =>
        `<div class="item sev-${f.severity}"><div class="hd">${statusGlyph(
          f.severity === 'high' ? 'critical' : f.severity === 'medium' ? 'high' : 'healthy')}
          ${esc(f.title)}<span class="spacer"></span>
          <span style="font-variant-numeric:tabular-nums">${fmtUSD(f.est_excess_usd)} excess</span></div>
          <div class="dt">${esc(f.detail)}</div></div>`).join('')}</div>`,
        {badge: BADGE.estimated,
         hint: `${fmtPct(waste.high_severity_pct)} high-severity exposure`,
         footer: esc(waste.note)})}
      ${card('Optimization opportunities', `<div class="stack">${
        b.recommendations.recommendations.length
        ? b.recommendations.recommendations.slice(0, 5).map(r => `<div class="item">
            <div class="hd">${esc(r.title)}<span class="spacer"></span>
              <span class="badge rec">${r.confidence} confidence</span></div>
            <div class="mt"><span>Actual ${fmtUSD(r.actual_cost_usd)}</span>
              <span>Est. alternative ${fmtUSD(r.estimated_alternative_cost_usd)}</span>
              <span style="color:var(--good-ink);font-weight:600">
                Est. saving ${fmtUSD(r.estimated_savings_usd)} (${r.estimated_savings_pct}%)</span></div>
            <div class="note">${esc(r.caveat)}</div></div>`).join('')
        : '<div class="empty">No recommendation met the evidence threshold</div>'}</div>`,
        {badge: BADGE.recommendation})}
    </div>

    <div class="grid g2">
      ${card('Forecast', '<div class="chart" id="fan"></div><div class="legend" id="fanleg"></div>',
        {badge: BADGE.forecast, hint: forecast.available ? forecast.method : ''})}
      ${card('FinOps score', `<div class="scorewrap">
        <div><div class="scorenum">${scorecard.score}</div>
          <div class="scoregrade">out of 100 · grade ${scorecard.grade}</div></div>
        <div style="flex:1;min-width:230px" class="stack">${scorecard.dimensions.map(d => `
          <div><div style="display:flex;justify-content:space-between;font-size:11.5px">
            <span>${esc(d.name)}</span><span style="font-variant-numeric:tabular-nums">${d.score}</span></div>
          <div class="meter ${d.score >= 75 ? 'healthy' : d.score >= 50 ? 'high'
            : d.score >= 30 ? 'approaching' : 'critical'}"><i style="width:${d.score}%"></i></div></div>`).join('')}
        </div></div>`, {badge: BADGE.estimated})}
    </div>`;

  // burn gauge + notes
  const gEl = $('#gauge', page);
  if (alloc.configured) {
    C.gauge(gEl, {pct: alloc.used_pct, status: alloc.status, label: 'of plan allowance'});
    $('#burnnotes', page).innerHTML = `<div class="stack">
      <div class="item"><div class="hd">${statusGlyph(alloc.status)} You are likely to reach your
        current limit in <b>${alloc.days_until_limit}</b> days${alloc.limit_date
        ? ` (around ${esc(alloc.limit_date)})` : ''}.</div></div>
      <div class="item"><div class="hd">At the current burn rate you will
        ${alloc.projected_overage_pct > 0 ? `exceed your allowance by
          <b>${fmtPct(alloc.projected_overage_pct)}</b>` : `finish the period at
          <b>${fmtPct(100 + alloc.projected_overage_pct)}</b> of allowance`}.</div></div></div>`;
  } else {
    const pctOfPeriod = bp.pct_elapsed;
    C.gauge(gEl, {pct: pctOfPeriod, status: 'healthy', label: 'of billing period elapsed'});
    $('#burnnotes', page).innerHTML = `<div class="stack">
      <div class="item"><div class="hd">No plan allowance available</div>
        <div class="dt">${agentWord()} data does not expose plan limits, remaining credits,
        or message allowances. Set <span class="mono">limits.monthly_cost_allowance_usd</span>
        in <span class="mono">config/settings.json</span> to unlock usage-vs-limit,
        days-until-limit and limit-date projections.</div></div>
      <div class="item"><div class="hd">Days until limit</div><div class="dt">${
        S.opts.unavailable_label} — requires a configured allowance.</div></div></div>`;
  }

  // trend
  const tSeries = [{key: 'cost', label: 'Estimated cost', color: seriesVar(0), fmt: fmtUSD}];
  C.legend($('#trendleg', page), tSeries.map(s => ({label: s.label, color: s.color})));
  C.timeSeries($('#trend', page), {rows: timeline, x: 'bucket', series: tSeries, type: 'bar',
    fmt: fmtUSD, height: 218, xLabel: shortDay,
    onClick: r => { S.filter.start = r.bucket; S.filter.end = r.bucket; S.range = 'custom';
      bust(); render(); }});

  // models
  const mr = models.rows.filter(r => r.cost > 0);
  C.donut($('#mdonut', page), {rows: mr, label: r => r.display_name, value: r => r.cost,
    size: 176, color: r => modelColor(r.model), centerValue: fmtUSD(o.est_cost_usd), centerLabel: 'estimated',
    onClick: r => { S.filter.models = [r.model]; bust(); render(); }});
  $('#mtable', page).innerHTML = table([
    {h: 'Model', f: r => `<span class="swatch" style="background:${modelColor(r.model)}"></span>${esc(r.display_name)}`},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
    {h: '% cost', num: 1, f: r => fmtPct(r.cost_pct)},
  ], mr);

  C.barsH($('#pbars', page), {rows: projects.slice(0, 10), label: r => r.name,
    value: r => r.cost, color: seriesVar(2),
    sub: r => `<div class="row"><span class="k">Sessions</span><span class="v">${fmtInt(r.sessions)}</span></div>
      <div class="row"><span class="k">Tokens</span><span class="v">${fmtNum(r.tokens)}</span></div>`,
    onClick: r => { S.filter.projects = [String(r.project_id)]; bust(); go('sessions'); }});

  const lp = leaderboards.most_expensive.slice(0, 10);
  $('#topprompts', page).innerHTML = table([
    {h: '#', num: 1, f: (r) => lp.indexOf(r) + 1},
    {h: 'Prompt', trunc: 1, title: r => r.preview, f: r => esc(r.preview)},
    {h: 'Category', f: r => `<span class="pill">${esc(r.category)}</span>`},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.ptokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.pcost)},
  ], lp, {onRow: 1});
  wireTable($('#topprompts', page), lp, r => openPrompt(r.prompt_id));

  const ls = await api('sessions', '&limit=10&order=cost');
  $('#topsessions', page).innerHTML = table([
    {h: 'Session', trunc: 1, title: r => r.session_id,
     f: r => esc(r.title || shortId(r.session_id))},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Prompts', num: 1, f: r => fmtInt(r.prompts)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
  ], ls, {onRow: 1});
  wireTable($('#topsessions', page), ls, r => openSession(r.session_id));

  if (forecast.available) {
    C.forecastFan($('#fan', page), {history: burn.series, scenarios: forecast.scenarios,
      remainingDays: forecast.remaining_days});
    $('#fanleg', page).innerHTML = `<span class="it"><span class="swatch"
      style="background:var(--s1)"></span>Cumulative actual (estimated cost)</span>
      <span class="it"><span class="swatch" style="background:var(--s1);opacity:.35"></span>
      Forecast band: conservative → high</span>`;
  } else $('#fan', page).innerHTML = '<div class="empty">Not enough history to forecast</div>';

  renderAdvisorHero(page, advisor);
  // focus strip loads after the page so a slow diagnosis never delays the overview
  api('diagnose').then(d => {
    if (!page.isConnected) return;
    const el = h(focusStrip(d) || '<div></div>');
    page.insertBefore(el, page.firstChild);
    wireFocus(page);
  }).catch(() => {});
};

function renderAdvisorHero(page, advisor) {
  if (!page || !advisor || !Array.isArray(advisor.actions)) return;
  const hero = h(`<div class="hero"><h3>✦ AI FinOps Advisor — ${esc(advisor.question)}
    <span class="spacer"></span>
    <span class="note" style="font-weight:400">${esc(advisor.generated_from)}</span></h3>
    <div class="advisor-list">${advisor.actions.length ? advisor.actions.map((a, i) =>
      `<div class="advisor-item"><div class="num p${Math.min(a.priority, 3)}"></div>
        <div class="tx"><b>${esc(a.text)} <span class="badge ${a.basis === 'forecast' ? 'fc'
          : a.basis === 'recommendation' ? 'rec' : 'est'}">${esc(a.basis.split(':')[0])}</span></b>
        <span>${esc(a.detail)}</span></div></div>`).join('')
      : '<div class="empty">Nothing needs your attention in this range</div>'}</div>
    ${advisor.estimated_savings_range_usd[1] > 0 ? `<div class="note" style="margin-top:9px">
      Estimated savings opportunity in range: <b>${fmtUSD(advisor.estimated_savings_range_usd[0])}
      – ${fmtUSD(advisor.estimated_savings_range_usd[1])}</b> — modelled, not booked.</div>` : ''}
  </div>`);
  page.insertBefore(hero, page.firstChild);
}

/* ---------- advisor view ---------- */
VIEWS.advisor = async (page) => {
  const [advisor, waste, recs, anos, sc] = await Promise.all([api('advisor'), api('waste'),
    api('recommendations'), api('anomalies'), api('scorecard')]);
  page.innerHTML = `
    <div class="grid g2">
      ${card('Biggest optimization opportunity', sc.biggest_opportunity ? `
        <div class="item sev-high"><div class="hd">${esc(sc.biggest_opportunity.title)}</div>
          <div class="dt">${esc(sc.biggest_opportunity.detail)}</div>
          <div class="mt"><span>Estimated excess
            <b>${fmtUSD(sc.biggest_opportunity.estimated_excess_usd)}</b></span>
            <span>of ${fmtUSD(sc.biggest_opportunity.exposed_cost_usd)} exposed</span></div>
          <div class="dt"><b>Action:</b> ${esc(sc.biggest_opportunity.action || '')}</div></div>`
        : '<div class="empty">Nothing flagged</div>', {badge: BADGE.estimated})}
      ${card('What is going well', `<ul style="margin:0 0 0 18px;font-size:12.5px;color:var(--text-2)">
        ${sc.what_is_good.map(x => `<li>${esc(x)}</li>`).join('') || '<li class="na">—</li>'}</ul>
        <h3 style="font-size:12px;margin:12px 0 5px">Needs attention</h3>
        <ul style="margin:0 0 0 18px;font-size:12.5px;color:var(--text-2)">
        ${sc.needs_attention.map(x => `<li>${esc(x)}</li>`).join('') || '<li class="na">—</li>'}</ul>`)}
    </div>
    ${card('All recommendations', recs.recommendations.length ? `<div class="stack">
      ${recs.recommendations.map(r => `<div class="item"><div class="hd">${esc(r.title)}
        <span class="spacer"></span><span class="badge rec">${esc(r.confidence)} confidence</span></div>
        ${r.current_model ? `<div class="dt"><b>Currently on:</b> ${esc(r.current_model)}${
          r.scope ? ` · ${esc(r.scope)}` : ''}</div>` : ''}
        <div class="grid g3" style="gap:8px;margin:4px 0">
          ${kpi('Actual cost', fmtUSD(r.actual_cost_usd), null, {small: 1, badge: BADGE.estimated})}
          ${kpi('Est. alternative', fmtUSD(r.estimated_alternative_cost_usd), null, {small: 1})}
          ${kpi('Est. potential saving', fmtUSD(r.estimated_savings_usd),
            `${r.estimated_savings_pct}%`, {small: 1, badge: BADGE.recommendation})}
        </div>
        ${r.alternatives && r.alternatives.length ? `<div class="dt" style="margin-top:6px">
          <b>Your options${r.agent ? ` within ${esc(r.agent)}` : ''}</b> — pick the trade-off you want:</div>
          ${table([
            {h: 'Model', f: a => esc(a.name) + (a.suggested
              ? ' <span class="badge rec">suggested</span>' : '')},
            {h: 'Tier', f: a => esc(a.tier)},
            {h: 'Est. cost', num: 1, f: a => fmtUSD(a.estimated_cost_usd)},
            {h: 'Est. saving', num: 1, f: a =>
              `${fmtUSD(a.estimated_savings_usd)} <span class="note">(${a.estimated_savings_pct}%)</span>`},
          ], r.alternatives)}` : ''}
        <div class="note">${esc(r.caveat)}</div></div>`).join('')}</div>`
      : '<div class="empty">No recommendation met the evidence threshold for this range</div>',
      {badge: BADGE.recommendation,
       footer: 'Estimated potential savings are modelled from token counts and configured pricing. They are opportunities to evaluate, never booked savings, and do not account for output quality.'})}
    ${card('Anomalies to inspect', `<div class="stack" id="anolist">${anos.anomalies.map((a, i) =>
      `<div class="item sev-${a.severity} clickable" data-i="${i}"><div class="hd">
        ${a.severity === 'high' ? '🚨' : '⚠️'} ${esc(a.title)}</div>
        <div class="dt">${esc(a.detail)}</div>
        <div class="mt"><span>ratio ${a.ratio}×</span><span>click to inspect</span></div></div>`).join('')
      || '<div class="empty">No anomalies detected</div>'}</div>`, {badge: BADGE.estimated})}`;
  renderAdvisorHero(page, advisor);
  wireAnomalies(page, anos.anomalies);
};

function wireAnomalies(page, list) {
  page.querySelectorAll('#anolist .item[data-i]').forEach(el => el.onclick = () => {
    const a = list[+el.dataset.i];
    if (a.drilldown?.session_id) return openSession(a.drilldown.session_id);
    if (a.drilldown?.filter) { Object.assign(S.filter, a.drilldown.filter);
      S.range = 'custom'; bust(); go('prompts'); }
  });
}

/* ---------- usage timeline ---------- */
const METRICS = [
  ['cost', 'Estimated cost', fmtUSD], ['tokens', 'Billable tokens', fmtNum],
  ['requests', 'Requests', fmtInt], ['sessions', 'Sessions', fmtInt],
  ['prompts', 'Prompts', fmtInt],
  ['input_tokens', 'Input tokens', fmtNum], ['output_tokens', 'Output tokens', fmtNum],
  ['cache_read_tokens', 'Cache read', fmtNum], ['cache_write_tokens', 'Cache write', fmtNum],
  ['avg_context', 'Avg context', fmtNum],
];
VIEWS.usage = async (page) => {
  const [tl, models, ov] = await Promise.all([api('timeline', `&grain=${S.grain}`),
    api('models'), api('overview')]);
  const [, mlabel, mfmt] = METRICS.find(m => m[0] === S.metric) || METRICS[0];
  page.innerHTML = `
    <div class="grid g5">
      ${kpi('Total tokens', fmtNum(ov.billable_tokens), null, {badge: BADGE.actual})}
      ${kpi('Input', fmtNum(ov.input_tokens), 'uncached input only')}
      ${kpi('Output', fmtNum(ov.output_tokens), `${fmtNum(ov.thinking_tokens)} thinking`)}
      ${kpi('Cache read', fmtNum(ov.cache_read_tokens))}
      ${kpi('Cache write', fmtNum(ov.cache_write_tokens))}
      ${kpi('Requests', fmtInt(ov.requests))}
      ${kpi('Sessions', fmtInt(ov.sessions))}
      ${kpi('Prompts', fmtInt(ov.prompts))}
      ${kpi('Active days', fmtInt(ov.active_days))}
      ${kpi('Est. spend', fmtUSD(ov.est_cost_usd), null, {badge: BADGE.estimated})}
    </div>
    ${card(mlabel + ' over time', `
      <div class="filters" style="margin:0 0 8px">
        ${METRICS.map(([k, l]) => `<button class="chip ${S.metric === k ? 'on' : ''}"
          data-metric="${k}">${l}</button>`).join('')}
        <span class="divider"></span>
        <button class="chip ${S.grain === 'day' ? 'on' : ''}" data-grain="day">Daily</button>
        <button class="chip ${S.grain === 'hour' ? 'on' : ''}" data-grain="hour">Hourly</button>
      </div>
      <div class="chart" id="tl"></div>`,
      {badge: S.metric === 'cost' ? BADGE.estimated : BADGE.actual,
       hint: 'click a bucket to drill into that day', flush: 0})}
    ${card('Model mix over time', '<div class="legend" id="mixleg"></div>' +
      '<div class="chart" id="mix"></div>', {badge: BADGE.estimated,
      hint: 'stacked estimated cost per model'})}
    ${card('Daily detail', '<div id="dtl"></div>', {badge: BADGE.estimated,
      hint: 'click a row to filter the whole dashboard to that day'})}`;

  page.querySelectorAll('[data-metric]').forEach(b => b.onclick = () => {
    S.metric = b.dataset.metric; render(); });
  page.querySelectorAll('[data-grain]').forEach(b => b.onclick = () => {
    S.grain = b.dataset.grain; bust(); render(); });

  C.timeSeries($('#tl', page), {rows: tl, x: 'bucket',
    series: [{key: S.metric, label: mlabel, color: seriesVar(0), fmt: mfmt}],
    fmt: mfmt, type: S.metric === 'avg_context' ? 'area' : 'bar', height: 260,
    xLabel: v => S.grain === 'day' ? shortDay(v) : v.slice(8).replace('T', ' ') + ':00',
    onClick: r => { if (S.grain === 'day') { S.filter.start = r.bucket; S.filter.end = r.bucket;
      S.range = 'custom'; bust(); render(); } }});

  // per-model stacked mix
  const byDay = new Map();
  const mlist = models.rows.filter(r => r.cost > 0).map(r => r.model);
  const perModel = await Promise.all(mlist.map(async m => {
    const r = await fetch(`/api/timeline?${qs()}&models=${encodeURIComponent(m)}`)
      .then(x => x.json()).catch(() => []);
    return [m, Array.isArray(r) ? r : []];
  }));
  for (const [m, rows] of perModel) for (const r of rows) {
    if (!byDay.has(r.bucket)) byDay.set(r.bucket, {bucket: r.bucket});
    byDay.get(r.bucket)[m] = r.cost;
  }
  const mixRows = [...byDay.values()].sort((a, b) => a.bucket < b.bucket ? -1 : 1);
  const mixSeries = mlist.map(m => ({key: m, label: modelName(m), color: modelColor(m), fmt: fmtUSD}));
  C.legend($('#mixleg', page), mixSeries.map(s => ({label: s.label, color: s.color})));
  C.timeSeries($('#mix', page), {rows: mixRows, x: 'bucket', series: mixSeries, type: 'bar',
    fmt: fmtUSD, height: 220, xLabel: shortDay});

  const detail = [...tl].reverse();
  $('#dtl', page).innerHTML = table([
    {h: S.grain === 'day' ? 'Day' : 'Hour', f: r => esc(r.bucket)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
    {h: 'Prompts', num: 1, f: r => fmtInt(r.prompts)},
    {h: 'Input', num: 1, f: r => fmtNum(r.input_tokens)},
    {h: 'Output', num: 1, f: r => fmtNum(r.output_tokens)},
    {h: 'Cache read', num: 1, f: r => fmtNum(r.cache_read_tokens)},
    {h: 'Cache write', num: 1, f: r => fmtNum(r.cache_write_tokens)},
    {h: 'Avg context', num: 1, f: r => fmtNum(r.avg_context)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
  ], detail, {onRow: 1});
  wireTable($('#dtl', page), detail, r => { if (S.grain === 'day') {
    S.filter.start = r.bucket; S.filter.end = r.bucket; S.range = 'custom'; bust(); render(); }});
};

/* ---------- burn & limits ---------- */
VIEWS.burn = async (page) => {
  const burn = await api('burn');
  const bp = burn.period;
  const rows = Object.entries(burn.allowances);
  page.innerHTML = `
    <div class="grid g4">
      ${kpi('Period', `${bp.start} → ${bp.end}`, `${bp.elapsed_days} of ${bp.total_days} days elapsed`,
        {small: 1, badge: BADGE.actual})}
      ${kpi('Days remaining', fmtInt(bp.remaining_days), fmtPct(bp.pct_elapsed) + ' elapsed')}
      ${kpi('Used this period', fmtUSD(burn.used.cost_usd),
        `${fmtNum(burn.used.tokens)} tokens · ${fmtInt(burn.used.requests)} requests`,
        {badge: BADGE.estimated})}
      ${kpi('Projected period total', fmtUSD(burn.projected_period_cost),
        `${fmtNum(burn.projected_period_tokens)} tokens`, {badge: BADGE.forecast})}
      ${kpi('Daily average', fmtUSD(burn.daily_avg_cost), fmtNum(burn.daily_avg_tokens) + ' tokens/day')}
      ${kpi('7-day average', fmtUSD(burn.avg7_cost), fmtNum(burn.avg7_tokens) + ' tokens/day')}
      ${kpi('Projection rate', fmtUSD(burn.burn_rate_cost_per_day),
        `${burn.burn_rate_window_days}-day mean · per day`, {badge: BADGE.estimated})}
      ${kpi('Remaining credits', burn.remaining_credits_usd === S.opts.unavailable_label
        ? null : fmtUSD(burn.remaining_credits_usd))}
    </div>
    <div class="grid g3">${rows.map(([k, a]) => card(
      `Allowance: ${k}`,
      a.configured ? `
        <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
          <div class="chart" id="g-${k}" style="width:170px;flex:none"></div>
          <div style="flex:1;min-width:170px"><dl class="kv">
            <dt>Used</dt><dd>${k === 'cost' ? fmtUSD(a.used) : fmtNum(a.used)} (${fmtPct(a.used_pct)})</dd>
            <dt>Remaining</dt><dd>${k === 'cost' ? fmtUSD(a.remaining) : fmtNum(a.remaining)}
              (${fmtPct(a.remaining_pct)})</dd>
            <dt>Allowance</dt><dd>${k === 'cost' ? fmtUSD(a.allowance) : fmtNum(a.allowance)}</dd>
            <dt>Days to limit</dt><dd>${a.days_until_limit ?? '—'}</dd>
            <dt>Limit date</dt><dd>${a.limit_date ? esc(a.limit_date) : '—'}</dd>
            <dt>Projected</dt><dd>${k === 'cost' ? fmtUSD(a.projected_end_of_period)
              : fmtNum(a.projected_end_of_period)}</dd>
            <dt>Overage</dt><dd>${fmtPct(a.projected_overage_pct)}</dd>
          </dl></div></div>
        <div class="item" style="margin-top:9px">
          <div class="hd">${statusGlyph(a.status)} You are likely to reach this limit in
            <b>${a.days_until_limit}</b> days.</div>
          <div class="dt">At the current burn rate you will
            ${a.projected_overage_pct > 0
              ? `exceed the allowance by <b>${fmtPct(a.projected_overage_pct)}</b>.`
              : `finish at <b>${fmtPct(100 + a.projected_overage_pct)}</b> of allowance.`}</div></div>`
        : `<div class="empty" style="text-align:left">
            <div class="na">${esc(a.message)}</div>
            <p style="font-size:11.5px;color:var(--muted);margin-top:8px">Claude Code transcripts
            record token usage only — they carry no plan tier, message allowance, credit balance
            or billed amount. Configure this in
            <span class="mono">config/settings.json → limits</span> to enable usage-vs-limit,
            days-until-limit and limit-date projections.</p></div>`,
      {badge: a.configured ? BADGE.estimated : ''})).join('')}</div>
    ${card('Daily consumption within the billing period', '<div class="chart" id="bs"></div>',
      {badge: BADGE.estimated})}`;
  for (const [k, a] of rows) if (a.configured)
    C.gauge($(`#g-${k}`, page), {pct: a.used_pct, status: a.status, label: 'of allowance'});
  C.timeSeries($('#bs', page), {rows: burn.series, x: 'day', type: 'bar',
    series: [{key: 'cost', label: 'Estimated cost', color: seriesVar(0), fmt: fmtUSD}],
    fmt: fmtUSD, height: 220, xLabel: shortDay});
};

/* ---------- models ---------- */
VIEWS.models = async (page) => {
  const m = await api('models');
  const sup = m.superlatives, rows = m.rows;
  const find = k => rows.find(r => r.model === sup[k]);
  page.innerHTML = `
    <div class="grid g4">
      ${kpi('Most expensive', sup.most_expensive ? esc(modelName(sup.most_expensive)) : null,
        find('most_expensive') ? fmtUSD(find('most_expensive').cost) + ' estimated' : '', {small: 1})}
      ${kpi('Most frequently used', sup.most_used ? esc(modelName(sup.most_used)) : null,
        find('most_used') ? fmtInt(find('most_used').requests) + ' requests' : '', {small: 1})}
      ${kpi('Most token-efficient', sup.most_token_efficient ? esc(modelName(sup.most_token_efficient)) : null,
        find('most_token_efficient')
          ? fmtPct(find('most_token_efficient').output_per_input * 100, 2) + ' output per prompt token' : '',
        {small: 1})}
      ${kpi('Best cost per output', sup.best_cost_per_output ? esc(modelName(sup.best_cost_per_output)) : null,
        find('best_cost_per_output')
          ? '$' + find('best_cost_per_output').cost_per_1k_output.toFixed(3) + ' / 1K output' : '',
        {small: 1, badge: BADGE.estimated})}
    </div>
    <div class="grid g2">
      ${card('Cost share', `<div style="display:flex;justify-content:center">
        <div class="chart" id="md"></div></div><div class="legend" id="mdl"></div>`,
        {badge: BADGE.estimated})}
      ${card('Token share', '<div class="chart" id="mb"></div>', {badge: BADGE.actual})}
    </div>
    ${card('Model FinOps table', '<div id="mt"></div>', {badge: BADGE.estimated,
      hint: 'models detected from your data — nothing hardcoded',
      footer: 'Pricing from config/pricing.json (updated ' + esc(S.opts.pricing.updated)
        + '). Models with no price entry fall back to default pricing and are marked.'})}
    ${card('Price table in effect', table([
      {h: 'Model', f: r => `<span class="swatch" style="background:${modelColor(r[0])}"></span>${esc(r[1].display_name || r[0])}`},
      {h: 'Tier', f: r => `<span class="pill">${esc(r[1].tier)}</span>`},
      {h: 'Input /M', num: 1, f: r => '$' + r[1].input},
      {h: 'Output /M', num: 1, f: r => '$' + r[1].output},
      {h: 'Cache write 5m /M', num: 1, f: r => '$' + r[1].cache_write_5m},
      {h: 'Cache write 1h /M', num: 1, f: r => '$' + r[1].cache_write_1h},
      {h: 'Cache read /M', num: 1, f: r => '$' + r[1].cache_read},
      {h: 'Context window', num: 1, f: r => r[1].context_window ? fmtNum(r[1].context_window) : '—'},
    ], Object.entries(S.opts.pricing.models)), {hint: 'edit config/pricing.json to update — no code changes needed'})}`;

  const priced = rows.filter(r => r.cost > 0);
  C.donut($('#md', page), {rows: priced, label: r => r.display_name, value: r => r.cost, size: 200,
    color: r => modelColor(r.model),
    centerValue: fmtUSD(priced.reduce((a, r) => a + r.cost, 0)), centerLabel: 'estimated'});
  C.legend($('#mdl', page), priced.map(r => ({label: `${r.display_name} — ${fmtUSD(r.cost)} (${r.cost_pct}%)`,
    color: modelColor(r.model)})));
  C.barsH($('#mb', page), {rows: rows.filter(r => r.tokens > 0), label: r => r.display_name,
    value: r => r.tokens, fmt: fmtNum, color: r => modelColor(r.model),
    sub: r => `<div class="row"><span class="k">Share</span><span class="v">${r.token_pct}%</span></div>`});
  $('#mt', page).innerHTML = table([
    {h: 'Model', f: r => `<span class="swatch" style="background:${modelColor(r.model)}"></span>${esc(r.display_name)}
      ${r.pricing_known ? '' : '<span class="badge na" title="no price entry — default pricing used">default price</span>'}`},
    {h: 'Tier', f: r => `<span class="pill">${esc(r.tier)}</span>`},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
    {h: 'Input', num: 1, f: r => fmtNum(r.input_tokens)},
    {h: 'Output', num: 1, f: r => fmtNum(r.output_tokens)},
    {h: 'Cache read', num: 1, f: r => fmtNum(r.cache_read_tokens)},
    {h: 'Cache write', num: 1, f: r => fmtNum(r.cache_write_tokens)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Avg context', num: 1, f: r => fmtNum(r.avg_context)},
    {h: 'Ctx util', num: 1, f: r => r.utilization_pct == null ? '—' : fmtPct(r.utilization_pct)},
    {h: 'Avg latency', num: 1, f: r => r.avg_latency_ms ? (r.avg_latency_ms/1000).toFixed(1)+'s' : '—'},
    {h: '$/1K out', num: 1, f: r => r.cost_per_1k_output ? '$' + r.cost_per_1k_output.toFixed(3) : '—'},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
    {h: '% cost', num: 1, f: r => fmtPct(r.cost_pct)},
  ], rows, {onRow: 1});
  wireTable($('#mt', page), rows, r => { S.filter.models = [r.model]; bust(); go('prompts'); });
};

/* ---------- projects ---------- */
VIEWS.projects = async (page) => {
  const rows = await api('projects');
  page.innerHTML = `
    ${card('Project cost ranking', '<div class="chart" id="pb"></div>', {badge: BADGE.estimated,
      hint: 'click a bar to drill into its sessions'})}
    ${card('Project FinOps table', '<div id="pt"></div>', {badge: BADGE.estimated,
      hint: 'Project → Session → Prompt drill-down',
      footer: 'Project identity comes from the working directory recorded in each transcript. '
        + `Repository/remote metadata is not present in ${agentWord()} data.`})}`;
  C.barsH($('#pb', page), {rows: rows.slice(0, 18), label: r => r.name, value: r => r.cost,
    color: seriesVar(2), onClick: r => { S.filter.projects = [String(r.project_id)];
      bust(); go('sessions'); },
    sub: r => `<div class="row"><span class="k">Sessions</span><span class="v">${fmtInt(r.sessions)}</span></div>
      <div class="row"><span class="k">Avg / session</span><span class="v">${fmtUSD(r.avg_cost_per_session)}</span></div>`});
  $('#pt', page).innerHTML = table([
    {h: 'Project', f: r => esc(r.name) + (r.is_sandbox
      ? ' <span class="pill">sandbox agent</span>' : '')},
    {h: 'Path', trunc: 1, title: r => r.path || '', f: r => `<span class="mono sub">${esc(r.path || '—')}</span>`},
    {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
    {h: 'Prompts', num: 1, f: r => fmtInt(r.prompts)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Files', num: 1, f: r => fmtInt(r.files_touched)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
    {h: 'Avg / session', num: 1, f: r => fmtUSD(r.avg_cost_per_session)},
    {h: 'Avg / prompt', num: 1, f: r => r.avg_cost_per_prompt == null ? '—' : fmtUSD(r.avg_cost_per_prompt)},
    {h: 'Budget', num: 1, f: r => r.budget_usd ? fmtPct(r.budget_used_pct) + ' of ' + fmtUSD(r.budget_usd) : '—'},
  ], rows, {onRow: 1});
  wireTable($('#pt', page), rows, r => { S.filter.projects = [String(r.project_id)];
    bust(); go('sessions'); });
};

/* ---------- sessions ---------- */
VIEWS.sessions = async (page) => {
  const order = S.sessOrder || 'cost';
  const rows = await api('sessions', `&limit=400&order=${order}`);
  const eff = await api('efficiency');
  const avgTok = rows.length ? rows.reduce((a, r) => a + r.tokens, 0) / rows.length : 0;
  page.innerHTML = `
    <div class="grid g4">
      ${kpi('Sessions in range', fmtInt(rows.length), null, {badge: BADGE.actual})}
      ${kpi('Avg cost / session', fmtUSD(rows.reduce((a, r) => a + r.cost, 0) / (rows.length || 1)),
        null, {badge: BADGE.estimated})}
      ${kpi('Avg tokens / session', fmtNum(avgTok))}
      ${kpi('Avg tokens / prompt', fmtNum(rows.reduce((a, r) => a + (r.tokens_per_prompt || 0), 0)
        / (rows.filter(r => r.tokens_per_prompt).length || 1)))}
    </div>
    ${card('Session explorer', `<div class="filters" style="margin:0 0 8px">
        ${[['cost', 'Most expensive'], ['tokens', 'Most tokens'], ['duration', 'Longest'],
           ['prompts', 'Most prompts'], ['recent', 'Most recent']].map(([k, l]) =>
          `<button class="chip ${order === k ? 'on' : ''}" data-so="${k}">${l}</button>`).join('')}
        <span class="spacer"></span>
        <span class="note">rows above ${fmtNum(avgTok * 3)} tokens are unusually expensive</span>
      </div><div id="st"></div>`, {badge: BADGE.estimated, hint: 'click a row to open the session'})}
    <div class="grid g2">
      ${card('Lowest output yield', table([
        {h: 'Session', trunc: 1, f: r => esc(r.title || shortId(r.session_id))},
        {h: 'Output share', num: 1, f: r => fmtPct(r.output_ratio * 100, 2)},
        {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
        {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
      ], eff.low_efficiency_sessions, {onRow: 1}), {badge: BADGE.estimated, hint: 'low efficiency'})}
      ${card('Highest output yield', table([
        {h: 'Session', trunc: 1, f: r => esc(r.title || shortId(r.session_id))},
        {h: 'Output share', num: 1, f: r => fmtPct(r.output_ratio * 100, 2)},
        {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
        {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
      ], eff.high_efficiency_sessions, {onRow: 1}), {badge: BADGE.estimated, hint: 'high efficiency'})}
    </div>`;
  page.querySelectorAll('[data-so]').forEach(b => b.onclick = () => {
    S.sessOrder = b.dataset.so; bust(); render(); });
  $('#st', page).innerHTML = table([
    {h: 'Session', trunc: 1, title: r => r.session_id,
     f: r => `${r.tokens > avgTok * 3 ? '🔴 ' : ''}${esc(r.title || shortId(r.session_id))}
       <div class="sub mono">${esc(shortId(r.session_id))}</div>`},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Branch', f: r => r.git_branch ? `<span class="mono">${esc(r.git_branch)}</span>` : '—'},
    {h: 'Start', f: r => `<span class="mono">${esc((r.started_at || '').slice(0, 16).replace('T', ' '))}</span>`},
    {h: 'Duration', num: 1, f: r => dur(r.duration_s)},
    {h: 'Prompts', num: 1, f: r => fmtInt(r.prompts)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Tools', num: 1, f: r => fmtInt(r.tool_calls)},
    {h: 'Files', num: 1, f: r => fmtInt(r.files_touched)},
    {h: 'Models', f: r => (r.models || '').split(',').map(m =>
      `<span class="swatch" title="${esc(modelName(m))}" style="background:${modelColor(m)}"></span>`).join('')},
    {h: 'Input', num: 1, f: r => fmtNum(r.input_tokens)},
    {h: 'Output', num: 1, f: r => fmtNum(r.output_tokens)},
    {h: 'Cache read', num: 1, f: r => fmtNum(r.cache_read_tokens)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Peak ctx', num: 1, f: r => fmtNum(r.max_context)},
    {h: 'Tok/prompt', num: 1, f: r => fmtNum(r.tokens_per_prompt)},
    {h: '$/prompt', num: 1, f: r => r.cost_per_prompt == null ? '—' : fmtUSD(r.cost_per_prompt)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
  ], rows, {onRow: 1});
  wireTable($('#st', page), rows, r => openSession(r.session_id));
  const effCards = [...page.querySelectorAll('.card')].filter(c =>
    /output yield/.test(c.querySelector('h3')?.textContent || ''));
  wireTable(effCards[0], eff.low_efficiency_sessions, r => openSession(r.session_id));
  wireTable(effCards[1], eff.high_efficiency_sessions, r => openSession(r.session_id));
  addChart(page, 'Top sessions by estimated cost', el => C.barsH(el, {
    rows: [...rows].sort((a, b) => b.cost - a.cost).slice(0, 12),
    label: r => clip(r.title || shortId(r.session_id), 42), value: r => r.cost,
    sub: r => `<div class="row"><span class="k">Project</span><span class="v">${esc(r.project)}</span></div>
      <div class="row"><span class="k">Tokens</span><span class="v">${fmtNum(r.tokens)}</span></div>
      <div class="row"><span class="k">Prompts</span><span class="v">${fmtInt(r.prompts)}</span></div>`,
    onClick: r => openSession(r.session_id)}), {badge: BADGE.estimated, hint: 'Click a bar to open the session'});
};

/* ---------- prompt explorer ---------- */
VIEWS.prompts = async (page) => {
  const order = S.promptOrder || 'cost';
  const q = S.promptQ || '';
  const rows = await api('prompts',
    `&limit=400&order=${order}${q ? '&q=' + encodeURIComponent(q) : ''}`);
  page.innerHTML = `
    ${card('Prompt explorer', `
      <div class="filters" style="margin:0 0 8px">
        <div class="search"><span class="mag">⌕</span>
          <input id="pq" value="${esc(q)}" placeholder="Search prompt text…"></div>
        <span class="divider"></span>
        ${[['cost', 'Most expensive'], ['tokens', 'Most tokens'], ['length', 'Longest'],
           ['efficiency', 'Most efficient'], ['cheapest', 'Cheapest'], ['recent', 'Most recent']]
          .map(([k, l]) => `<button class="chip ${order === k ? 'on' : ''}" data-po="${k}">${l}</button>`).join('')}
        <span class="spacer"></span>
        <span class="note">${fmtInt(rows.length)} prompts · global filters apply</span>
      </div><div id="pt"></div>`,
      {badge: BADGE.estimated, hint: 'click any row for the full prompt, usage and advice',
       footer: 'Prompt text is read from your local transcripts and never leaves this machine.'})}`;
  const inp = $('#pq', page);
  let t; inp.oninput = e => { clearTimeout(t); const v = e.target.value;
    t = setTimeout(() => { S.promptQ = v; bust(); render().then(() => {
      const i = $('#pq'); if (i) { i.focus(); i.setSelectionRange(v.length, v.length); } }); }, 300); };
  page.querySelectorAll('[data-po]').forEach(b => b.onclick = () => {
    S.promptOrder = b.dataset.po; bust(); render(); });
  $('#pt', page).innerHTML = table([
    {h: 'When', f: r => `<span class="mono">${esc((r.ts || '').slice(0, 16).replace('T', ' '))}</span>`},
    {h: 'Prompt', trunc: 1, title: r => r.preview, f: r => esc(r.preview)},
    {h: 'Category', f: r => `<span class="pill" title="confidence ${(100*(r.category_confidence||0)).toFixed(0)}%${
      r.category_evidence?.length ? ' · matched: ' + esc(r.category_evidence.join(', ')) : ''}">${esc(r.category)}</span>`},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Session', trunc: 1, title: r => r.session_id,
     f: r => `<span class="mono sub">${esc(r.session_title || shortId(r.session_id))}</span>`},
    {h: 'Models', f: r => (r.models || '').split(',').map(m =>
      `<span class="swatch" title="${esc(modelName(m))}" style="background:${modelColor(m)}"></span>`).join('')},
    {h: 'Chars', num: 1, f: r => fmtInt(r.char_len)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Input', num: 1, f: r => fmtNum(r.input_tokens)},
    {h: 'Output', num: 1, f: r => fmtNum(r.output_tokens)},
    {h: 'Cache read', num: 1, f: r => fmtNum(r.cache_read_tokens)},
    {h: 'Cache write', num: 1, f: r => fmtNum(r.cache_write_tokens)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.ptokens)},
    {h: 'Peak ctx', num: 1, f: r => fmtNum(r.max_context)},
    {h: 'Tools', num: 1, f: r => fmtInt(r.tool_calls)},
    {h: 'Files', num: 1, f: r => fmtInt(r.files_touched)},
    {h: 'Latency', num: 1, f: r => r.latency_ms ? (r.latency_ms / 1000).toFixed(1) + 's' : '—'},
    {h: 'Tok eff', num: 1, f: r => fmtPct(r.efficiency * 100, 2)},
    {h: '$/1K out', num: 1, f: r => r.cost_per_1k_output ? '$' + r.cost_per_1k_output.toFixed(3) : '—'},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.pcost)},
  ], rows, {onRow: 1});
  wireTable($('#pt', page), rows, r => openPrompt(r.prompt_id));
};

/* ---------- rankings ---------- */
const RANK_TABS = [
  ['most_expensive', 'Top 20 most expensive'], ['most_token_heavy', 'Top 20 most token-heavy'],
  ['cheapest', 'Top 20 cheapest'], ['most_efficient', 'Top 20 most efficient'],
  ['longest_sessions', 'Top 20 longest sessions']];
VIEWS.rankings = async (page) => {
  const lb = await api('leaderboards', '&n=20');
  const tab = S.rankTab || 'most_expensive';
  const isSess = tab === 'longest_sessions';
  const rows = lb[tab];
  page.innerHTML = card('Cost & efficiency leaderboards', `
    <div class="tabs">${RANK_TABS.map(([k, l]) =>
      `<button class="${tab === k ? 'on' : ''}" data-rt="${k}">${l}</button>`).join('')}</div>
    <div id="rt" style="margin-top:8px"></div>`,
    {badge: BADGE.estimated, hint: 'every row is clickable'});
  page.querySelectorAll('[data-rt]').forEach(b => b.onclick = () => {
    S.rankTab = b.dataset.rt; render(); });
  const cols = isSess ? [
    {h: '#', num: 1, f: r => rows.indexOf(r) + 1},
    {h: 'Session', trunc: 1, f: r => esc(r.title || shortId(r.session_id))},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Duration', num: 1, f: r => dur(r.duration_s)},
    {h: 'Prompts', num: 1, f: r => fmtInt(r.prompts)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
    {h: 'Start', f: r => `<span class="mono">${esc((r.started_at || '').slice(0, 16).replace('T', ' '))}</span>`},
  ] : [
    {h: '#', num: 1, f: r => rows.indexOf(r) + 1},
    {h: 'Prompt', trunc: 1, title: r => r.preview, f: r => esc(r.preview)},
    {h: 'Category', f: r => `<span class="pill">${esc(r.category)}</span>`},
    {h: 'Models', f: r => (r.models || '').split(',').map(m =>
      `<span class="swatch" title="${esc(modelName(m))}" style="background:${modelColor(m)}"></span>`).join('')},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.ptokens)},
    {h: 'Output', num: 1, f: r => fmtNum(r.output_tokens)},
    {h: 'Tok eff', num: 1, f: r => fmtPct(r.efficiency * 100, 2)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.pcost)},
    {h: 'Session', trunc: 1, f: r => esc(r.session_title || shortId(r.session_id))},
    {h: 'Date', f: r => esc(r.day)},
  ];
  $('#rt', page).innerHTML = table(cols, rows, {onRow: 1});
  wireTable($('#rt', page), rows, r => isSess ? openSession(r.session_id) : openPrompt(r.prompt_id));
  addChart(page, 'Top 12 prompts by estimated cost', el => C.barsH(el, {
    rows: (lb.most_expensive || []).slice(0, 12), label: r => clip(r.preview, 46), value: r => r.pcost,
    sub: r => `<div class="row"><span class="k">Project</span><span class="v">${esc(r.project)}</span></div>
      <div class="row"><span class="k">Tokens</span><span class="v">${fmtNum(r.ptokens)}</span></div>`}),
    {badge: BADGE.estimated, after: '.nothing'});
};

/* ---------- categories / prompt intelligence ---------- */
VIEWS.categories = async (page) => {
  const c = await api('categories');
  const rows = c.rows;
  page.innerHTML = `
    <div class="grid g2">
      ${card('Cost by activity', `<div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">
        <div class="chart" id="cd"></div><div style="flex:1;min-width:220px" class="legend"
        id="cl" style="flex-direction:column;align-items:flex-start"></div></div>`,
        {badge: BADGE.estimated, hint: `where your ${agentWord()} budget goes`})}
      ${card('Prompts by activity', '<div class="chart" id="cb"></div>', {badge: BADGE.actual})}
    </div>
    ${card('Activity breakdown', '<div id="ct"></div>', {badge: BADGE.estimated,
      hint: 'click a row to filter the dashboard to that activity',
      footer: esc(c.note) + ' Classification is keyword-based and transparent — open any prompt to see the matched terms and confidence.'})}`;
  C.donut($('#cd', page), {rows, label: r => r.category, value: r => r.cost, size: 190,
    color: r => catColor(r.category),
    centerValue: fmtUSD(rows.reduce((a, r) => a + r.cost, 0)), centerLabel: 'estimated',
    onClick: r => { S.filter.categories = [r.category]; bust(); go('prompts'); }});
  $('#cl', page).innerHTML = rows.map((r, i) => `<span class="it">
    <span class="swatch" style="background:${catColor(r.category)}"></span>
    ${esc(r.category)} — <b>${fmtPct(r.cost_pct)}</b> · ${fmtUSD(r.cost)}</span>`).join('');
  C.barsH($('#cb', page), {rows, label: r => r.category, value: r => r.prompts, fmt: fmtInt,
    color: r => catColor(r.category),
    onClick: r => { S.filter.categories = [r.category]; bust(); go('prompts'); }});
  $('#ct', page).innerHTML = table([
    {h: 'Activity', f: (r) => `<span class="swatch" style="background:${catColor(r.category)}"></span>${esc(r.category)}`},
    {h: 'Prompts', num: 1, f: r => fmtInt(r.prompts)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Avg prompt chars', num: 1, f: r => fmtInt(r.avg_prompt_chars)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Output', num: 1, f: r => fmtNum(r.output_tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
    {h: '$/prompt', num: 1, f: r => fmtUSD(r.cost_per_prompt)},
    {h: '% of spend', num: 1, f: r => `<div style="display:flex;align-items:center;gap:7px;justify-content:flex-end">
      <span class="minibar" style="width:${Math.max(2, r.cost_pct)}px;background:${catColor(r.category)}"></span>
      ${fmtPct(r.cost_pct)}</div>`},
    {h: 'Avg confidence', num: 1, f: r => fmtPct((r.confidence || 0) * 100, 0)},
  ], rows, {onRow: 1});
  wireTable($('#ct', page), rows, r => { S.filter.categories = [r.category]; bust(); go('prompts'); });
};

/* ---------- context & cache ---------- */
VIEWS.context = async (page) => {
  const [ctx, eff] = await Promise.all([api('context'), api('efficiency')]);
  const ca = eff.cache;
  page.innerHTML = `
    <div class="grid g5">
      ${kpi('Avg context / request', fmtNum(ctx.avg_context), null, {badge: BADGE.actual})}
      ${kpi('Max context seen', fmtNum(ctx.max_context),
        ctx.typical_context_window ? `window ${fmtNum(ctx.typical_context_window)}` : '')}
      ${kpi('Context utilization', ctx.typical_context_window
        ? fmtPct(100 * ctx.avg_context / ctx.typical_context_window) : null,
        'average vs largest configured window')}
      ${kpi('Tokens / request', fmtNum(eff.tokens_per_request))}
      ${kpi('Cache hit ratio', ca.reads ? fmtPct(eff.cache_hit_ratio * 100) : null,
        'reads ÷ (reads + writes)', {badge: BADGE.actual})}
    </div>
    ${ctx.large_context_cost_pct > 0 ? `<div class="hero">
      <h3>⚠️ Context warnings</h3>
      <div class="stack">
        <div class="item sev-${ctx.large_context_cost_pct > 30 ? 'high' : 'medium'}">
          <div class="hd">${fmtPct(ctx.large_context_cost_pct)} of your estimated spend came from
            requests with more than ${fmtNum(ctx.threshold)} context tokens.</div>
          <div class="dt">${fmtInt(ctx.large_context_requests)} requests, ${fmtUSD(ctx.large_context_cost)} estimated.</div></div>
        <div class="item sev-medium"><div class="hd">These are expensive because the same context is
          re-sent on every turn.</div>
          <div class="dt">Caching discounts the repeat, but a large prefix still dominates the bill.
            /compact or a fresh session resets it.</div></div>
      </div></div>` : ''}
    <div class="grid g2">
      ${card('Cost by context size', '<div class="chart" id="cxb"></div>', {badge: BADGE.estimated,
        hint: 'context = input + cache read + cache write per request'})}
      ${card('Caching: with vs without', `
        <div class="grid g3" style="gap:8px">
          ${kpi('Est. cost with caching', fmtUSD(ca.cost_with_cache), null, {small: 1, badge: BADGE.estimated})}
          ${kpi('Est. cost without caching', fmtUSD(ca.cost_without_cache), 'same tokens at input rates', {small: 1})}
          ${kpi('Est. savings', fmtUSD(ca.estimated_savings_usd), fmtPct(ca.savings_pct),
            {small: 1, badge: BADGE.estimated})}
        </div>
        <div class="chart" id="cachebar" style="margin-top:10px"></div>
        <dl class="kv" style="margin-top:10px">
          <dt>Cache reads</dt><dd>${fmtNum(ca.reads)} tokens</dd>
          <dt>Cache writes</dt><dd>${fmtNum(ca.writes)} tokens</dd>
          <dt>Tokens served from cache</dt><dd>${fmtNum(ca.reads)}</dd>
        </dl>`, {badge: BADGE.estimated,
        footer: 'The no-cache baseline prices every cached token at the plain input rate. It is a modelled counterfactual, not a bill you avoided.'})}
    </div>
    ${card('Sessions with the largest context', '<div id="hs"></div>', {badge: BADGE.estimated,
      hint: `above ${fmtNum(ctx.threshold)} peak context tokens`})}
    ${card('Context distribution', table([
      {h: 'Context bucket', f: r => esc(r.bucket)},
      {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
      {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
      {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
      {h: '% of spend', num: 1, f: r => fmtPct(r.cost_pct)},
    ], ctx.buckets), {badge: BADGE.estimated})}`;
  C.barsH($('#cxb', page), {rows: ctx.buckets, label: r => r.bucket, value: r => r.cost,
    color: seriesVar(0),
    sub: r => `<div class="row"><span class="k">Requests</span><span class="v">${fmtInt(r.requests)}</span></div>
      <div class="row"><span class="k">Share</span><span class="v">${r.cost_pct}%</span></div>`});
  C.barsH($('#cachebar', page), {rows: [
    {k: 'Without caching (modelled)', v: ca.cost_without_cache},
    {k: 'With caching (your actual usage)', v: ca.cost_with_cache}],
    label: r => r.k, value: r => r.v, color: (r, i) => i ? seriesVar(2) : seriesVar(1), height: 60});
  $('#hs', page).innerHTML = table([
    {h: 'Session', trunc: 1, f: r => esc(r.title || shortId(r.session_id))},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Avg context', num: 1, f: r => fmtNum(r.avg_context)},
    {h: 'Peak context', num: 1, f: r => fmtNum(r.max_context)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
  ], ctx.heavy_sessions, {onRow: 1});
  wireTable($('#hs', page), ctx.heavy_sessions, r => openSession(r.session_id));
};

/* ---------- model switch ---------- */
// Verdicts from the back-test. Wording matters here: "supported" means your own
// history backs the switch, not that we modelled it.
const VERDICT = {
  supported: ['🟢', 'Backed by your data', 'healthy'],
  caution:   ['🟠', 'Trial first', 'approaching'],
  risky:     ['🔴', 'Cost more work', 'critical'],
  marginal:  ['⚪', 'Too close to call', 'high'],
};

function evidenceBody(ev) {
  if (!ev || !ev.categories?.length) {
    return `<div class="empty">No category yet has ${ev?.min_prompts || 8}+ prompts on two
      different models of the same agent, so there is nothing to compare. Run a cheaper model on
      a handful of real tasks and this fills in.</div>`;
  }
  const rows = [];
  ev.categories.forEach(c => c.candidates.forEach((x, i) => rows.push({c, x, first: i === 0})));
  return table([
    {h: 'Work', f: r => r.first ? `<b>${esc(r.c.category.replace('_', ' '))}</b>` : ''},
    {h: 'You use now', f: r => r.first
      ? `${esc(r.c.current.name)} <span class="note">${fmtUSD(r.c.current.cost_per_prompt)}/prompt ·
         ${Math.round(r.c.current.turns)} turns</span>` : ''},
    {h: 'Instead of', f: r => `<b>${esc(r.x.name)}</b>`},
    {h: '$ / prompt', num: 1, f: r => fmtUSD(r.x.cost_per_prompt)},
    {h: 'Turns', num: 1, f: r => `${Math.round(r.x.turns)} <span class="note">(${r.x.turn_ratio}×)</span>`},
    {h: 'Re-asked', num: 1, f: r => `${fmtPct(r.x.repeat_pct)}<span class="note">${
      r.x.repeat_delta > 0 ? ' +' + r.x.repeat_delta : ''}</span>`},
    {h: 'On', num: 1, f: r => `${fmtInt(r.x.prompts)} prompts`},
    {h: 'Verdict', f: r => `<span title="${esc(r.x.why)}">${
      statusChip(VERDICT[r.x.verdict][2], VERDICT[r.x.verdict][1])}</span>`},
    {h: 'Would save', num: 1, f: r => r.x.verdict === 'supported'
      ? `<b>${fmtUSD(r.x.estimated_savings_usd)}</b>` : `<span class="note">${fmtUSD(r.x.estimated_savings_usd)}</span>`},
  ], rows) + `<div class="note" style="padding:10px 14px">${esc(ev.method)}</div>`;
}

VIEWS.modelswitch = async (page) => {
  const [m, ev] = await Promise.all([
    api('model_switch'),
    api('model_evidence').catch(() => null),
  ]);
  const sc = m.savings_by_confidence || {};
  const conf = c => `<span class="badge rec">${esc(c)} confidence</span>`;
  page.innerHTML = `
    <div class="grid g4">
      ${kpi('Backed by your own runs', fmtUSD(ev?.estimated_savings_usd || 0),
        `${ev?.categories?.filter(c => c.recommended).length || 0} categories where a cheaper model
         already did the same work for less`, {badge: BADGE.actual})}
      ${kpi('Potential savings', fmtUSD(m.estimated_savings_usd),
        `${fmtPct(m.total_cost_usd ? 100 * m.estimated_savings_usd / m.total_cost_usd : 0)} of ${fmtUSD(m.total_cost_usd)} in range`,
        {badge: BADGE.recommendation})}
      ${kpi('Safe to switch', fmtUSD(m.safe_savings_usd), 'High + medium confidence only', {badge: BADGE.recommendation})}
      ${kpi('Try on a sample first', fmtUSD(sc.low || 0), 'Low confidence: coding and refactoring', {badge: BADGE.recommendation})}
      ${kpi('Stays on current model', fmtUSD(m.blocked_by_context_usd),
        `${fmtInt(m.blocked_by_context_requests)} requests too big for the cheaper model's context`, {badge: BADGE.estimated})}
    </div>
    ${card('What actually happened when you used a cheaper model',
      `<div id="ms-ev">${evidenceBody(ev)}</div>`, {badge: BADGE.actual, flush: 1,
      hint: 'Measured from your own prompts — no repricing, no assumptions about tokens',
      footer: ev ? esc(ev.caveat) : ''})}
    <div class="note">The table above is history; the one below is a model. Where they disagree,
      believe the history: repricing assumes the cheaper model would finish in the same number of
      turns, and your data shows that is often where the saving goes.</div>
    ${card('Switch these (repriced, not measured)', `<div id="ms-sw"></div>`,
      {badge: BADGE.recommendation, flush: 1,
      hint: 'Each request repriced on the model its work needs, same tokens',
      footer: esc(m.caveat)})}
    ${card('Default model per project', `<div id="ms-pj"></div>`, {badge: BADGE.recommendation, flush: 1,
      hint: 'Based on how much frontier-model spend is reasoning-heavy work'})}
    ${card('How to switch', `<div class="stack">
      ${(m.providers?.length ? m.providers : ['anthropic']).map(pv => { const h = (m.how_by_agent || {})[pv] || m.how; return `
      <div class="dt"><b>${esc(h.agent || 'Claude Code')}</b></div>
      <div class="dt"><b>This session:</b> <code>${esc(h.session)}</code></div>
      <div class="dt"><b>Whole project:</b> <code>${esc(h.project)}</code></div>
      ${pv === 'anthropic' ? `<div class="dt"><b>Subagents:</b> <code>${esc(m.how.subagent)}</code></div>` : ''}`; }).join('')}
      <div class="dt note">Kept on the top model: ${esc(Object.entries(m.rules).filter(([, v]) => v === 'keep').map(([k]) => k.replace('_', ' ')).join(', '))}.</div>
    </div>`)}`;
  $('#ms-sw', page).innerHTML = table([
    {h: 'Work', f: r => esc(r.scope)},
    {h: 'Now', f: r => esc(r.current_name)},
    {h: 'Switch to', f: r => `<b>${esc(r.recommended_name)}</b>`},
    {h: 'Confidence', f: r => conf(r.confidence)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Est. cost now', num: 1, f: r => fmtUSD(r.cost)},
    {h: 'After switch', num: 1, f: r => fmtUSD(r.alt)},
    {h: 'Saves', num: 1, f: r => `<b>${fmtUSD(r.estimated_savings_usd)}</b> (${fmtPct(r.estimated_savings_pct)})`},
  ], m.switches);
  $('#ms-pj', page).innerHTML = table([
    {h: 'Project', trunc: 1, f: r => esc(r.project)},
    {h: 'Frontier spend', num: 1, f: r => fmtUSD(r.frontier_cost)},
    {h: 'Reasoning-heavy', num: 1, f: r => fmtPct(r.keep_pct)},
    {h: 'Suggested default', f: r => `<b>${esc(r.suggested_default)}</b>`},
    {h: 'Could save', num: 1, f: r => fmtUSD(r.estimated_savings_usd)},
    {h: 'Why', f: r => esc(r.why)},
  ], m.projects);
  addChart(page, 'Savings by switch', el => C.barsH(el, {
    rows: m.switches.slice(0, 10), label: r => clip(`${r.scope} → ${r.recommended_name}`, 48),
    value: r => r.estimated_savings_usd,
    color: r => r.confidence === 'high' ? seriesVar(2) : r.confidence === 'medium' ? seriesVar(0) : 'var(--warning)',
    sub: r => `<div class="row"><span class="k">Now</span><span class="v">${fmtUSD(r.cost)}</span></div>
      <div class="row"><span class="k">After</span><span class="v">${fmtUSD(r.alt)}</span></div>
      <div class="row"><span class="k">Confidence</span><span class="v">${esc(r.confidence)}</span></div>`}),
    {badge: BADGE.recommendation, hint: 'Colour = confidence (green high, blue medium, amber low)'});
};

/* ---------- waste ---------- */
VIEWS.waste = async (page) => {
  const w = await api('waste');
  page.innerHTML = `
    <div class="grid g4">
      ${kpi('Estimated excess', fmtUSD(w.estimated_excess_usd),
        `${fmtPct(w.excess_pct)} of ${fmtUSD(w.total_cost_usd)} — the actual waste figure`,
        {badge: BADGE.estimated})}
      ${kpi('Exposed spend', fmtUSD(w.exposed_cost_usd),
        `${fmtPct(w.exposed_pct)} sits in items a rule touched`, {badge: BADGE.estimated})}
      ${kpi('Prompts flagged', fmtInt(w.affected_prompts))}
      ${kpi('Sessions flagged', fmtInt(w.affected_sessions))}
      ${kpi('Rules triggered', fmtInt(w.findings.length),
        `${w.findings.filter(f => f.severity === 'high').length} high severity`)}
    </div>
    <div class="note"><b>Exposed spend</b> is what the flagged items cost in total — money worth
      reviewing, not money wasted. <b>Estimated excess</b> is how much more that work cost than a
      reasonable baseline, de-duplicated across rules, and is what the FinOps score grades. Each
      rule below states the baseline it measures against.</div>
    ${['high', 'medium', 'low'].map(sev => {
      const list = w.findings.filter(f => f.severity === sev);
      if (!list.length) return '';
      const title = sev === 'high' ? '🔴 High waste'
        : sev === 'medium' ? '🟡 Optimization opportunities' : '⚪ Low-priority observations';
      return card(title, `<div class="stack">${list.map((f, i) => `
        <div class="item sev-${sev}">
          <div class="hd">${esc(f.title)}<span class="spacer"></span>
            <span style="font-variant-numeric:tabular-nums">${fmtUSD(f.est_excess_usd)} excess</span>
            <span class="note" style="font-variant-numeric:tabular-nums">of ${fmtUSD(f.est_cost_usd)} exposed</span></div>
          <div class="dt">${esc(f.detail)}</div>
          <div class="dt"><b>Excess measured as:</b> ${esc(f.excess_basis)}</div>
          <div class="dt"><b>Recommended:</b> ${esc(f.recommended_action)}</div>
          <details style="margin-top:5px"><summary style="cursor:pointer;font-size:11.5px;color:var(--s1)">
            Show ${f.evidence.length} flagged items</summary>
            <div class="ev" data-kind="${esc(f.kind)}" style="margin-top:6px"></div></details>
        </div>`).join('')}</div>`, {badge: BADGE.estimated});
    }).join('')}
    <div class="note">${esc(w.note)}</div>`;
  // fill evidence tables
  const all = w.findings;
  page.querySelectorAll('.ev').forEach(host => {
    const f = all.find(x => x.kind === host.dataset.kind);
    const isSess = !!f.evidence[0]?.session_id && !f.evidence[0]?.prompt_id;
    const cols = isSess ? [
      {h: 'Session', trunc: 1, f: r => esc(r.title || shortId(r.session_id))},
      {h: 'Project', f: r => esc(r.project || '—')},
      {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
      {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens ?? r.reads)},
      {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
      {h: 'Est. excess', num: 1, f: r => fmtUSD(r.excess)},
    ] : [
      {h: 'Prompt', trunc: 1, title: r => r.preview, f: r => esc(r.preview)},
      {h: 'Detail', f: r => r.n ? `repeated ${r.n}×` : r.char_len ? fmtInt(r.char_len) + ' chars'
        : r.tools ? fmtInt(r.tools) + ' tool calls'
        : r.out_tokens != null ? fmtInt(r.out_tokens) + ' output tokens' : '—'},
      {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
      {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
      {h: 'Est. excess', num: 1, f: r => fmtUSD(r.excess)},
    ];
    host.innerHTML = table(cols, f.evidence, {onRow: 1});
    wireTable(host, f.evidence, r => r.prompt_id ? openPrompt(r.prompt_id)
      : openSession(r.session_id));
  });
  addChart(page, 'Excess cost by rule', el => C.barsH(el, {
    rows: w.findings.filter(f => f.est_excess_usd > 0).sort((a, b) => b.est_excess_usd - a.est_excess_usd),
    label: f => clip(f.title, 46), value: f => f.est_excess_usd,
    color: f => f.severity === 'high' ? 'var(--critical)' : f.severity === 'medium' ? 'var(--warning)' : seriesVar(0),
    sub: f => `<div class="row"><span class="k">Exposed</span><span class="v">${fmtUSD(f.est_cost_usd)}</span></div>`}),
    {badge: BADGE.estimated, hint: 'What each rule says you overspent, against its own baseline'});
};

/* ---------- attribution: session / subagent / skill / MCP / connector ---------- */
VIEWS.attribution = async (page) => {
  const b = await api('breakdown');
  const srv = x => esc((x || '').replace(/^claude_ai_/, '').replace(/_/g, ' '));
  const sum = (rows, k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
  const subTok = sum(b.subagents.types, 'tokens');
  const extCols = label => [
    {h: label, f: r => `<b>${srv(r.server)}</b>`},
    {h: 'Calls', num: 1, f: r => fmtInt(r.calls)},
    {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
    {h: 'Injected', num: 1, f: r => fmtNum(r.injected)},
    {h: 'Largest', num: 1, f: r => fmtNum(r.largest)},
    {h: 'Carried (re-read)', num: 1, f: r => fmtNum(r.carried)},
    {h: 'Est. carry cost', num: 1, f: r => fmtUSD(r.carried_cost)},
  ];
  const toolDetail = rows => rows.map(r => `<details style="margin:4px 0"><summary style="cursor:pointer;font-size:12px">
      ${srv(r.server)} — per tool</summary>${table([
        {h: 'Tool', f: t => esc(t.name.split('__').pop())},
        {h: 'Calls', num: 1, f: t => fmtInt(t.calls)},
        {h: 'Injected', num: 1, f: t => fmtNum(t.injected)},
        {h: 'Carried', num: 1, f: t => fmtNum(t.carried)}], r.tools)}</details>`).join('');
  page.innerHTML = `
    <div class="grid g4">
      ${kpi('Subagent tokens', fmtNum(subTok), `${fmtInt(sum(b.subagents.types, 'runs'))} runs`, {badge: BADGE.actual})}
      ${kpi('Skills (Claude-invoked)', fmtInt(b.skills.length), `${fmtNum(sum(b.skills, 'injected'))} tokens injected`, {badge: BADGE.actual})}
      ${kpi('MCP servers used', fmtInt(b.mcp.length), `${fmtUSD(sum(b.mcp, 'carried_cost'))} est. carry cost`, {badge: BADGE.estimated})}
      ${kpi('Connectors used', fmtInt(b.connectors.length), `${fmtUSD(sum(b.connectors, 'carried_cost'))} est. carry cost`, {badge: BADGE.estimated})}
    </div>
    <div class="note">${esc(b.note)}</div>
    ${card('Sessions: main agent vs subagents', `<div id="at-sess"></div>`, {badge: BADGE.actual, flush: 1, hint: 'Top 30 by tokens; click to drill in'})}
    ${card('Subagents by type', `<div id="at-types"></div>`, {badge: BADGE.actual, flush: 1})}
    ${card('Most expensive subagent runs', `<div id="at-runs"></div>`, {badge: BADGE.actual, flush: 1})}
    ${card('Skills', `<div id="at-skills"></div>`, {badge: BADGE.estimated, flush: 1, hint: 'Invoked by Claude via the Skill tool'})}
    ${card('Slash commands & user-invoked skills', `<div id="at-slash"></div>`, {badge: BADGE.actual, flush: 1, hint: 'Whole turn attributed'})}
    ${card('MCP servers', `<div id="at-mcp"></div>${toolDetail(b.mcp)}`, {badge: BADGE.estimated, flush: 1})}
    ${card('Connectors (claude.ai)', `<div id="at-conn"></div>${toolDetail(b.connectors)}`, {badge: BADGE.estimated, flush: 1})}
    ${b.configured_unused_mcp.length ? `<div class="note">Configured but never called: <b>${esc(b.configured_unused_mcp.join(', '))}</b>. Their tool definitions still load into every session. Remove them with <code>claude mcp remove &lt;name&gt;</code>.</div>` : ''}`;
  const put = (id, cols, rows, onRow) => { const el = $(id, page); el.innerHTML = table(cols, rows, {onRow: !!onRow}); wireTable(el, rows, onRow); };
  put('#at-sess', [
    {h: 'Session', trunc: 1, title: r => r.session_id, f: r => esc(r.title || shortId(r.session_id))},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Main agent', num: 1, f: r => fmtNum(r.main_tokens)},
    {h: 'Subagents', num: 1, f: r => r.sub_tokens ? `${fmtNum(r.sub_tokens)} (${r.subagents})` : '—'},
    {h: 'Peak ctx', num: 1, f: r => fmtNum(r.max_ctx)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
    {h: 'Skills / MCP', trunc: 1, f: r => esc([...r.skills, ...r.mcp.map(m => m.replace(/^claude_ai_/, ''))].join(', ') || '—')},
  ], b.sessions, r => openSession(r.session_id));
  put('#at-types', [
    {h: 'Agent type', f: r => `<b>${esc(r.agent_type)}</b>`},
    {h: 'Runs', num: 1, f: r => fmtInt(r.runs)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Tokens / run', num: 1, f: r => fmtNum(r.tokens_per_run)},
    {h: 'Returned to main', num: 1, f: r => fmtNum(r.returned_tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
  ], b.subagents.types);
  put('#at-runs', [
    {h: 'Task', trunc: 1, title: r => r.agent_desc, f: r => esc(r.agent_desc || r.agent_id)},
    {h: 'Type', f: r => esc(r.agent_type)},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
  ], b.subagents.runs, r => openSession(r.session_id));
  put('#at-skills', extCols('Skill'), b.skills);
  put('#at-slash', [
    {h: 'Command', f: r => `<b>/${esc(r.server)}</b>${r.builtin ? ' <span class="na">built-in</span>' : ''}`},
    {h: 'Uses', num: 1, f: r => fmtInt(r.calls)},
    {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
    {h: 'Turn tokens', num: 1, f: r => fmtNum(r.turn_tokens)},
    {h: 'Est. turn cost', num: 1, f: r => fmtUSD(r.turn_cost)},
  ], b.slash_commands);
  put('#at-mcp', extCols('Server'), b.mcp);
  put('#at-conn', extCols('Connector'), b.connectors);
  addChart(page, 'Where subagent and MCP tokens went', el => {
    el.innerHTML = '<div class="grid g2"><div><div class="note">Subagent tokens by type</div><div id="at-c1"></div></div>' +
      '<div><div class="note">MCP & connector carry cost</div><div id="at-c2"></div></div></div>';
    C.barsH($('#at-c1', el), {rows: [...b.subagents.types].sort((x, y) => y.tokens - x.tokens).slice(0, 8),
      label: r => r.agent_type, value: r => r.tokens, fmt: fmtNum});
    C.barsH($('#at-c2', el), {rows: [...b.mcp, ...b.connectors].sort((x, y) => y.carried_cost - x.carried_cost).slice(0, 8),
      label: r => r.server.replace(/^claude_ai_/, ''), value: r => r.carried_cost});
  }, {badge: BADGE.estimated});
};

/* ---------- running sessions: stop / close from the browser ---------- */
async function sessionAction(pid, action, body = {}) {
  const r = await fetch(`/api/live/${pid}/${action}`, {method: 'POST',
    headers: {'X-FinOps-Action': '1', 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  return r.json();
}
// Plan limits as `/usage` reports them: asked of Claude Code itself, server-side.
const usageSev = p => p >= 90 ? 'critical' : p >= 75 ? 'approaching' : p >= 50 ? 'high' : 'healthy';
const usageBody = u => {
  if (!u || !u.ok) return `<div class="empty">${esc((u && u.error) || 'Usage is unavailable')}</div>`;
  const meters = (u.limits || []).map(l => `
    <div class="item">
      <div class="hd"><b>${esc(l.label)}</b><span class="spacer"></span>
        <span style="font-variant-numeric:tabular-nums">${fmtPct(l.pct)} used</span></div>
      <div class="meter ${usageSev(l.pct)}"><i style="width:${Math.min(100, l.pct)}%"></i></div>
      ${l.resets ? `<div class="dt note">Resets ${esc(l.resets)}</div>` : ''}</div>`).join('')
    || `<pre class="note">${esc(u.text || '')}</pre>`;
  const windows = (u.windows || []).map(w => `
    <div class="item"><div class="hd">Last ${esc(w.window)}<span class="spacer"></span>
      <span class="note">${esc(w.detail)}</span></div>
      ${(w.notes || []).map(n => `<div class="dt">• ${esc(n)}</div>`).join('')}</div>`).join('');
  return `<div class="stack">${meters}${windows}</div>
    <div class="note">${esc(u.note || '')}${u.cached ? ` · read ${dur(u.age_s)} ago` : ''}${u.stale
      ? ` · <b>last good read</b> (refresh failed: ${esc(u.error || '')})` : ''}</div>`;
};
VIEWS.live = async (page) => {
  const [res, usage0] = await Promise.all([
    fetch('/api/live?agents=' + encodeURIComponent(S.filter.agents.join(','))).then(r => r.json()),   // never cached: always live
    fetch('/api/usage').then(r => r.json()).catch(e => ({ok: false, error: e.message}))]);
  const list = res.sessions || [];
  const agentName = id => ((S.opts?.agents || []).find(a => a.id === id) || {}).name || id;
  const busy = list.filter(x => x.status === 'busy');
  const sevOf = x => x.severity === 'high' ? 'high' : x.severity === 'medium' ? 'medium' : 'low';
  page.innerHTML = `
    ${card('Plan usage right now', `<div id="live-usage">${usageBody(usage0)}</div>`,
      {badge: BADGE.actual, hint: 'Claude Code\'s own /usage: plan limits, not cost. Costs no tokens.',
       actions: '<button class="btn pb-copy" id="usage-refresh">↻ Refresh usage</button>'})}
    <div class="grid g4">
      ${kpi('Running sessions', fmtInt(list.length), `${busy.length} working right now`, {badge: BADGE.actual})}
      ${kpi('Working (spending now)', fmtInt(busy.length), 'Only these consume tokens right now')}
      ${kpi('Largest live context', fmtNum(Math.max(0, ...list.map(x => x.context || 0))), 'Re-read on every step')}
      ${kpi('Spent by live sessions', fmtUSD(list.reduce((a, x) => a + (x.est_cost_usd || 0), 0)), 'Since each started (priced agents only)', {badge: BADGE.estimated})}
    </div>
    <div class="note"><b>Idle sessions don't use tokens</b>; they only cost again when you send the next message,
      which re-reads their whole context. <b>Interrupt</b> stops the current turn (like Esc).
      <b>Compact</b> types <code>/compact</code> into that session's terminal (tmux, Terminal.app, iTerm2 or
      Windows console; anywhere else it lands on your clipboard to paste).
      <b>Close</b> exits the session cleanly; copy its resume command to bring it back.
      <b>Force kill</b> is for a session that won't close. Each button asks you to click twice.
      Codex, Gemini and Cursor keep no session registry, so they count as running when their transcript was
      written in the last 20 minutes. Cursor sessions live inside the IDE and can't be stopped from here.
      <span class="spacer"></span><button class="btn pb-copy" id="live-refresh">↻ Refresh</button></div>
    <div class="stack">${list.map((x, i) => `
      <div class="item sev-${sevOf(x)}" data-i="${i}">
        <div class="hd">${x.status === 'busy' ? '<span class="live-dot"></span>' : '<span class="idle-dot"></span>'}
          <span class="pill">${esc(agentName(x.agent))}</span>
          ${esc(x.name || shortId(x.session_id))} <span class="note">· ${esc(x.project)}${x.pid ? ` · pid ${x.pid}` : ''}${x.model ? ` · ${esc(x.model)}` : ''}</span>
          ${x.hosts_dashboard ? '<span class="status high">runs this dashboard</span>' : ''}
          <span class="spacer"></span>
          <span style="font-variant-numeric:tabular-nums">${x.context == null ? 'context not recorded' : fmtNum(x.context) + ' context'} · ${fmtInt(x.steps)} steps${x.est_cost_usd == null ? '' : ' · ' + fmtUSD(x.est_cost_usd)}</span></div>
        <div class="dt">${x.status === 'busy' ? '<b>Working now</b>' : 'Idle'}${x.uptime ? ` · up ${esc(x.uptime)}` : ''} ·
          last activity ${x.last_write_s == null ? '—' : dur(x.last_write_s)} ago${x.memory_mb == null ? '' : ` · ${fmtInt(x.memory_mb)} MB`} ·
          <span class="note">${esc(x.cwd)}</span></div>
        ${x.severity !== 'ok' ? `<div class="dt"><b>Advice:</b> ${x.severity === 'high'
          ? 'Very large context. Use <b>Hand over</b> to continue in a fresh session, or split the remaining work into sub-sessions.'
          : 'Getting heavy. Hit <b>Compact</b> at the next break, or close it if the task is done.'}</div>` : ''}
        <div class="live-actions">
          ${x.signalable ? `<button class="act" data-a="interrupt" ${x.status !== 'busy' ? 'disabled title="Nothing running"' : ''}>⏸ Interrupt</button>
          ${x.agent === 'claude' ? '<button class="act" data-a="compact" title="Types /compact into that session\'s terminal">🗜 Compact</button>' : ''}
          <button class="act warn" data-a="close">⏹ Close session</button>
          <button class="act danger" data-a="kill">✖ Force kill</button>` : `<span class="note">${x.agent === 'cursor' ? 'Runs inside the Cursor IDE: stop it there.' : 'No matching process found: stop it in its terminal.'}</span>`}
          ${x.resume ? `<button class="act ghost" data-copy="${esc(x.resume)}">Copy resume command</button>` : ''}
          ${x.agent === 'claude' ? '<button class="act ghost" data-ho="1">⇢ Hand over / split</button>' : ''}
          <span class="live-msg"></span>
        </div>
        ${x.agent !== 'claude' ? '' : `<div class="handover" hidden>
          <div class="dt">Starts fresh session(s) in <code>${esc(x.cwd)}</code>, seeded with a brief built from this
            transcript (goal, recent requests, task list, files changed, last status). No tokens are spent building it.</div>
          <textarea rows="3" placeholder="Leave empty to continue the same work in one new session.&#10;Or split it: one sub-task per line, one new session each (max 8)."></textarea>
          <div class="live-actions">
            <label class="note"><input type="checkbox" class="ho-close" checked> Close this session afterwards (resumable)</label>
            <span class="spacer"></span>
            <button class="act ghost ho-brief">Write brief only</button>
            <button class="act warn ho-go">Start new session(s)</button>
          </div>
          <div class="ho-out note"></div>
        </div>`}
      </div>`).join('') || `<div class="empty">No ${esc(agentWord())} sessions are running</div>`}</div>`;
  $('#live-refresh', page).onclick = () => render();
  const ub = $('#usage-refresh', page);
  ub.onclick = async () => {
    ub.disabled = true; ub.textContent = 'Reading…';
    try {
      const u = await fetch('/api/usage?refresh=1').then(r => r.json());
      $('#live-usage', page).innerHTML = usageBody(u);
    } catch (e) { $('#live-usage', page).innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
    ub.disabled = false; ub.textContent = '↻ Refresh usage';
  };
  page.querySelectorAll('.item[data-i]').forEach(row => {
    const x = list[+row.dataset.i], pid = x.pid, msg = row.querySelector('.live-msg');
    const ho = row.querySelector('.handover'), ta = ho?.querySelector('textarea'), hoOut = ho?.querySelector('.ho-out');
    const refreshGo = () => { const n = ta.value.split('\n').filter(l => l.trim()).length;
      ho.querySelector('.ho-go').textContent = n > 1 ? `Start ${Math.min(n, 8)} sub-sessions` : 'Start new session'; };
    if (ho) { ta.oninput = refreshGo; refreshGo(); }
    const runHandover = async (launch, btn) => {
      const label = btn.textContent; btn.disabled = true; btn.textContent = 'Working…';
      try {
        const r = await sessionAction(pid, 'handover', {tasks: ta.value.split('\n'), launch,
          close: launch && ho.querySelector('.ho-close').checked});
        hoOut.innerHTML = r.ok ? `<div class="live-msg ok">${esc(r.message)}</div>` + r.sessions.map(s =>
          `<div>${s.launched ? '▶' : '•'} ${esc(s.task || 'Continue the same work')} — <code>${esc(s.brief)}</code>${s.error ? ` <span class="live-msg err">${esc(s.error)}</span>` : ''}</div>`).join('')
          : `<div class="live-msg err">Failed: ${esc(r.error)}</div>`;
        if (r.ok && r.closed && r.closed.exited) { row.classList.add('gone'); setTimeout(() => render(), 2500); }
      } catch (e) { hoOut.innerHTML = `<div class="live-msg err">Failed: ${esc(e.message)}</div>`; }
      btn.disabled = false; btn.textContent = label; refreshGo();
    };
    if (ho) {
      ho.querySelector('.ho-go').onclick = e => runHandover(true, e.currentTarget);
      ho.querySelector('.ho-brief').onclick = e => runHandover(false, e.currentTarget);
    }
    row.querySelectorAll('button.act:not(.ho-go):not(.ho-brief)').forEach(b => {
      if (b.dataset.ho) { b.onclick = () => { ho.hidden = !ho.hidden; if (!ho.hidden) ta.focus(); }; return; }
      if (b.dataset.copy) { b.onclick = async () => { try { await navigator.clipboard.writeText(b.dataset.copy); msg.textContent = 'Copied'; } catch { msg.textContent = b.dataset.copy; } }; return; }
      const label = b.textContent;
      b.onclick = async () => {
        // two-click confirm, inline (no browser dialogs)
        if (!b.classList.contains('armed')) {
          row.querySelectorAll('button.armed').forEach(o => { o.classList.remove('armed'); o.textContent = o.dataset.label; });
          b.dataset.label = label; b.classList.add('armed');
          b.textContent = x.hosts_dashboard && !['interrupt', 'compact'].includes(b.dataset.a)
            ? 'Sure? This is the session that launched the dashboard (the dashboard keeps running)' : `Click again to ${b.dataset.a}`;
          setTimeout(() => { if (b.classList.contains('armed')) { b.classList.remove('armed'); b.textContent = label; } }, 4000);
          return;
        }
        b.classList.remove('armed'); b.disabled = true; b.textContent = 'Working…';
        try {
          const r = await sessionAction(pid, b.dataset.a, {agent: x.agent});
          if (!r.ok && r.copy) { try { await navigator.clipboard.writeText(r.copy); } catch {} }
          msg.textContent = r.ok ? r.message : `Failed: ${r.error}`;
          msg.className = 'live-msg ' + (r.ok ? 'ok' : 'err');
          if (r.ok && r.exited) { row.classList.add('gone'); setTimeout(() => render(), 1500); }
        } catch (e) { msg.textContent = 'Failed: ' + e.message; msg.className = 'live-msg err'; }
        b.disabled = false; b.textContent = label;
      };
    });
  });
};

/* ---------- token diagnosis ---------- */
// Steps + a copy-paste prompt for Claude, attached server-side to each finding.
const PB = [];
const pb = x => {
  if (!x) return '';
  let promptHtml = '';
  if (x.prompt) {
    PB.push(x.prompt);
    promptHtml = `<div class="pb-prompt"><div class="pb-hd">Prompt for Claude${x.where ? ` <span class="note">· run in ${esc(x.where)}</span>` : ''}
        <span class="spacer"></span><button class="btn pb-copy" data-pb="${PB.length - 1}">Copy</button></div>
      <pre>${esc(x.prompt)}</pre></div>`;
  }
  return `<details class="pb" open><summary>How to fix</summary>
    <ol>${x.steps.map(t => `<li>${esc(t)}</li>`).join('')}</ol>${promptHtml}</details>`;
};
const wirePB = page => page.querySelectorAll('.pb-copy').forEach(b => b.onclick = async () => {
  try { await navigator.clipboard.writeText(PB[+b.dataset.pb]); b.textContent = 'Copied ✓'; }
  catch { b.textContent = 'Copy failed'; }
  setTimeout(() => b.textContent = 'Copy', 1500);
});
const HARNESS = {needed: ['🔴 Needed', 'high'], partial: ['🟡 Partial', 'medium'],
  in_place: ['✅ In place', 'low'], not_needed: ['⚪ Not needed', 'low'], unknown: ['— Unknown', 'low']};
const harnessChip = h => h ? `<b>${HARNESS[h.verdict][0]}</b>` : '—';

VIEWS.diagnose = async (page) => {
  const d = await api('diagnose');
  const isCl = (d.agent || 'claude') === 'claude', md = d.vocab?.md || 'CLAUDE.md';
  const gdir = {claude: '~/.claude', codex: '~/.codex', gemini: '~/.gemini'}[d.agent || 'claude'];
  PB.length = 0;
  const t = d.total || {}, tr = d.trend || {};
  const sevChip = s => s === 'high' ? '🔴' : s === 'medium' ? '🟡' : '⚪';
  page.innerHTML = `${focusStrip(d)}
    <div class="note" style="font-size:14px"><b>${esc(d.headline)}</b>
      ${tr.explanation ? `<br>${esc(tr.explanation)}` : ''}</div>
    <div class="grid g4">
      ${kpi('Tokens in range', fmtNum(t.b), `${fmtInt(t.n)} requests · ${fmtInt(t.sessions)} sessions`, {badge: BADGE.actual})}
      ${kpi('Avg context / request', fmtNum(t.ctx), 'Re-sent on every step', {badge: BADGE.actual})}
      ${kpi('Cache-read share', fmtPct(t.b ? 100 * t.cr / t.b : 0), 'History re-read', {badge: BADGE.actual})}
      ${kpi('Last 7d vs prior 7d', tr.change_pct == null ? null : `${tr.change_pct > 0 ? '+' : ''}${tr.change_pct}%`,
        tr.volume_factor ? `${tr.volume_factor}× requests · ${tr.size_factor}× size each` : '', {badge: BADGE.actual})}
    </div>
    <div id="dx-live"></div>${card('Running now', d.live_sessions.length ? `<div class="stack">${d.live_sessions.map(x => `
      <div class="item sev-${x.severity === 'ok' ? 'low' : x.severity}">
        <div class="hd">${x.severity === 'high' ? '🔴' : x.severity === 'medium' ? '🟡' : '🟢'}
          ${esc(x.title || shortId(x.session_id))} <span class="note">· ${esc(x.project)}</span><span class="spacer"></span>
          <span style="font-variant-numeric:tabular-nums">${fmtNum(x.context)} context · ${fmtInt(x.steps)} steps</span></div>
        <div class="dt">${esc(x.advice)} <span class="note">Last write ${x.idle_min} min ago; context grew
          ${fmtNum(x.start_context)} → ${fmtNum(x.context)}.</span></div>${pb(x.playbook)}</div>`).join('')}</div>`
      : '<div class="empty">No session written in the last 20 minutes</div>',
      {badge: BADGE.actual, hint: 'Read live from transcripts on each load'})}
    <div id="dx-past"></div>${card('Past sessions that carried too much context', `<div class="stack">${d.session_health.map(x => `
      <div class="item sev-${x.peak >= 300000 ? 'high' : 'medium'}">
        <div class="hd"><a href="#" class="sess-link" data-sess="${esc(x.session_id)}">${esc(x.title || shortId(x.session_id))}</a>
          <span class="note">· ${esc(x.project)}</span><span class="spacer"></span>
          <span style="font-variant-numeric:tabular-nums">peak ${fmtNum(x.peak)} · ${fmtUSD(x.cost)} ·
            ~${fmtUSD(x.avoidable_cost)} above a 100K baseline</span></div>
        ${x.fixes.map(t => `<div class="dt">• ${esc(t)}</div>`).join('')}${pb(x.playbook)}</div>`).join('')
      || '<div class="empty">No session passed 150K context</div>'}</div>`,
      {badge: BADGE.recommendation, hint: '"Above baseline" = context re-read beyond 100K, at cache-read price (est.)'})}
    <div id="dx-mem"></div>${card(`Add to ${md}${isCl ? ' / memory' : ''} (from your past prompts)`, `<div class="stack">${d.memory_suggestions.map(x => `
      <div class="item sev-${x.kind === 'security' ? 'high' : x.already_saved ? 'low' : 'medium'}">
        <div class="hd">${x.kind === 'security' ? '🔐' : x.kind === 'template' ? '⚙' : x.kind === 'reference' ? '🔗' : '📝'}
          ${esc(x.text.slice(0, 140))}${x.already_saved ? ' <span class="na">already saved</span>' : ''}
          <span class="spacer"></span><span class="note">${fmtInt(x.sessions)} sessions</span></div>
        <div class="dt">${esc(x.why)}</div>
        <div class="dt"><b>Put it in:</b> ${esc(x.target)}</div>
        ${(x.examples || []).length ? `<details><summary style="cursor:pointer;font-size:11.5px">Examples</summary>
          ${x.examples.map(e => `<div class="dt note">“${esc(e)}”</div>`).join('')}</details>` : ''}${pb(x.playbook)}</div>`).join('')
      || '<div class="empty">No repeated instructions found</div>'}</div>`,
      {badge: BADGE.recommendation, hint: 'Mined from non-sandbox prompts; secrets masked'})}
    ${card('Why consumption is high', `<div class="stack">${d.drivers.map(x => `
      <div class="item"><div class="hd">${esc(x.title)}<span class="spacer"></span>
        <span style="font-variant-numeric:tabular-nums">${fmtPct(x.share_pct)} of tokens</span></div>
        <div class="dt">${esc(x.detail)}</div></div>`).join('')}</div>`,
      {badge: BADGE.actual, hint: 'Drivers overlap, so shares do not add up to 100%'})}
    <div id="dx-recs"></div>${card('What to change', `<div class="stack">${d.recommendations.map(r => `
      <div class="item sev-${r.priority === 1 ? 'high' : r.priority === 2 ? 'medium' : 'low'}">
        <div class="hd">${esc(r.title)}<span class="spacer"></span>
          ${r.est_savings_usd ? `<span>~${fmtUSD(r.est_savings_usd)} est.</span>` : ''}</div>
        <div class="dt"><b>Why:</b> ${esc(r.why)}</div>
        <div class="dt"><b>How:</b> ${esc(r.how)}</div>
        ${r.savings_basis ? `<div class="dt note">${esc(r.savings_basis)}</div>` : ''}${pb(r.playbook)}</div>`).join('')
      || '<div class="empty">Nothing stands out</div>'}</div>`, {badge: BADGE.recommendation})}
    ${card(`Projects: ${esc(d.vocab?.name || 'Claude')} config health`, `<div id="dx-proj"></div>`,
      {badge: BADGE.recommendation, hint: isCl ? 'CLAUDE.md, memory, .claude/settings.json and MCP checked on disk now' : `${md} checked on disk now`, flush: 1})}
    ${isCl ? card('Projects: does it need a harness?', `<div id="dx-harness" class="stack"></div>`,
      {badge: BADGE.recommendation, hint: 'Harness = CLAUDE.md, permissions, hooks, skills, subagents. Need is judged from your usage in each project'}) : ''}
    <div id="dx-issues"></div>
    ${card(`Global config (${gdir})`, `<div class="stack">
      <div class="dt">Default model: <b>${esc(d.global.default_model || 'not set')}</b> ·
        MCP servers: ${esc(d.global.mcp_servers.join(', ') || 'none')} ·
        ${isCl ? `Custom agents: ${esc(d.global.custom_agents.join(', ') || 'none')}` : ''}</div>
      ${d.global.files.map(x => `<div class="dt">${esc(x.path)} — ~${fmtInt(x.tokens)} tokens</div>`).join('')}
      ${d.global.issues.map(i => `<div class="item sev-${i.severity}"><div class="hd">${sevChip(i.severity)} ${esc(i.title)}</div>
        <div class="dt">${esc(i.fix)}</div>${pb(i.playbook)}</div>`).join('')}</div>`)}
    <div class="note">${esc(d.note)}</div>`;
  const cols = [
    {h: 'Project', trunc: 1, title: r => r.path, f: r => esc(r.name) + (r.exists ? '' : ' <span class="na">(path missing)</span>')},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
    {h: 'Avg ctx', num: 1, f: r => fmtNum(r.avg_context)},
    {h: md, f: r => r.claude_md.paths.length ? `~${fmtInt(r.claude_md.tokens)} tok` : '<span class="na">missing</span>'},
    ...(isCl ? [{h: 'Memory', num: 1, f: r => r.memory_tokens == null ? '—' : `~${fmtInt(r.memory_tokens)}`}] : []),
    {h: 'Explore %', num: 1, f: r => fmtPct(r.explore_pct)},
    ...(isCl ? [{h: 'Harness', f: r => harnessChip(r.harness)}] : []),
    {h: 'Needs update', f: r => r.issues.length ? r.issues.map(i => sevChip(i.severity) + ' ' + esc(i.title)).join('<br>') : '✓'},
  ];
  page.querySelectorAll('.sess-link').forEach(l => l.onclick = e => { e.preventDefault(); openSession(l.dataset.sess); });
  $('#dx-proj', page).innerHTML = table(cols, d.projects);
  const order = {needed: 0, partial: 1, in_place: 2, not_needed: 3, unknown: 4};
  if ($('#dx-harness', page)) $('#dx-harness', page).innerHTML = [...d.projects].filter(p => p.harness)
    .sort((a, b) => order[a.harness.verdict] - order[b.harness.verdict] || b.tokens - a.tokens)
    .map(p => { const h = p.harness; return `<div class="item sev-${HARNESS[h.verdict][1]}">
      <div class="hd">${harnessChip(h)} ${esc(p.name)}<span class="spacer"></span>
        <span class="note">${h.have.length ? 'Has: ' + esc(h.have.join(', ')) : 'Nothing set up'}</span></div>
      ${h.reasons.length ? `<div class="dt"><b>Why:</b> ${esc(h.reasons.join(' · '))}</div>` : ''}
      ${h.missing.map(m => `<div class="dt"><b>Add ${esc(m.piece)}:</b> ${esc(m.fix)}</div>`).join('')}</div>`; })
    .join('') || '<div class="empty">No projects in range</div>';
  const withIssues = d.projects.filter(p => p.issues.length);
  $('#dx-issues', page).innerHTML = withIssues.map(p => card(`Fix: ${p.name}`, `<div class="stack">
    <div class="dt note">${esc(p.path)}</div>
    ${p.issues.map(i => `<div class="item sev-${i.severity}"><div class="hd">${sevChip(i.severity)} ${esc(i.title)}</div>
      <div class="dt">${esc(i.fix)}</div>${pb(i.playbook)}</div>`).join('')}</div>`, {badge: BADGE.recommendation})).join('');
  wirePB(page);
  wireFocus(page);
  addChart(page, 'What drives your token use', el => C.barsH(el, {
    rows: d.drivers.filter(x => x.share_pct != null).sort((a, b) => b.share_pct - a.share_pct),
    label: x => clip(x.title, 44), value: x => x.share_pct, fmt: v => fmtPct(v), max: 100,
    color: x => x.share_pct >= 50 ? 'var(--critical)' : x.share_pct >= 25 ? 'var(--warning)' : seriesVar(0)}),
    {badge: BADGE.actual, hint: 'Share of tokens or cost each driver accounts for'});
};

/* ---------- recommendations ---------- */
VIEWS.recommendations = VIEWS.advisor;

/* ---------- anomalies ---------- */
VIEWS.anomalies = async (page) => {
  const [a, tl] = await Promise.all([api('anomalies'), api('timeline')]);
  const dates = new Set(a.anomalies.filter(x => x.date).map(x => x.date));
  page.innerHTML = `
    ${card('Daily spend with anomalies highlighted', '<div class="chart" id="an"></div>' +
      '<div class="legend" id="anl"></div>', {badge: BADGE.estimated})}
    ${card('Detected anomalies', `<div class="stack" id="anolist">${a.anomalies.length
      ? a.anomalies.map((x, i) => `<div class="item sev-${x.severity} clickable" data-i="${i}">
        <div class="hd">${x.severity === 'high' ? '🚨' : '⚠️'} ${esc(x.title)}
          <span class="spacer"></span><span class="pill">${esc(x.type)}</span></div>
        <div class="dt">${esc(x.detail)}</div>
        <div class="mt"><span>observed ${fmtNum(x.metric_value)}</span>
          <span>baseline ${fmtNum(x.baseline)}</span><span>ratio ${x.ratio}×</span>
          <span style="color:var(--s1)">click to inspect →</span></div></div>`).join('')
      : '<div class="empty">No anomalies detected in this range</div>'}</div>`,
      {badge: BADGE.estimated,
       footer: 'Thresholds are configurable in config/settings.json → anomaly.'})}`;
  C.timeSeries($('#an', page), {rows: tl, x: 'bucket', type: 'bar', fmt: fmtUSD, height: 220,
    xLabel: shortDay,
    series: [{key: 'cost', label: 'Estimated cost', fmt: fmtUSD,
      color: seriesVar(0)}]});
  // repaint anomaly days in the critical color
  const bars = $('#an', page).querySelectorAll('rect');
  tl.forEach((r, i) => { if (dates.has(r.bucket) && bars[i]) bars[i].setAttribute('fill', 'var(--critical)'); });
  $('#anl', page).innerHTML = `<span class="it"><span class="swatch" style="background:${seriesVar(0)}"></span>Normal day</span>
    <span class="it"><span class="swatch" style="background:var(--critical)"></span>🚨 Anomalous day</span>`;
  wireAnomalies(page, a.anomalies);
};

/* ---------- forecast ---------- */
VIEWS.forecast = async (page) => {
  const [f, burn] = await Promise.all([api('forecast'), api('burn')]);
  if (!f.available) { page.innerHTML = card('Forecast', `<div class="empty">${esc(f.message)}</div>`);
    return; }
  page.innerHTML = `
    <div class="grid g4">
      ${kpi('End of day', fmtUSD(f.end_of_day_cost), null, {badge: BADGE.forecast})}
      ${kpi('End of week', fmtUSD(f.end_of_week_cost), null, {badge: BADGE.forecast})}
      ${kpi('End of billing period', fmtUSD(f.scenarios.expected.end_of_period_cost),
        `${f.remaining_days} days remaining`, {badge: BADGE.forecast})}
      ${kpi('Estimated monthly cost', fmtUSD(f.estimated_monthly_cost), null, {badge: BADGE.forecast})}
      ${kpi('Period to date', fmtUSD(f.period_used), fmtNum(f.period_used_tokens) + ' tokens',
        {badge: BADGE.estimated})}
      ${kpi('Projected tokens', fmtNum(f.end_of_period_tokens), null, {badge: BADGE.forecast})}
      ${kpi('Daily mean ± σ', `${fmtUSD(f.daily_mean)} ± ${fmtUSD(f.daily_stdev)}`,
        `over ${f.sample_days} days`, {small: 1})}
      ${f.limit_exhaustion_date === S.opts.unavailable_label
        ? kpi('Limit exhaustion date', null, 'Requires a configured allowance')
        : kpi('Limit exhaustion date', esc(f.limit_exhaustion_date),
            f.will_exceed ? '🔴 forecast exceeds allowance' : '🟢 within allowance', {badge: BADGE.forecast})}
    </div>
    ${card('Cumulative spend and forecast fan', '<div class="chart" id="fan2"></div>' +
      '<div class="legend" id="fl2"></div>', {badge: BADGE.forecast,
      hint: f.method,
      footer: 'Scenarios are the 14-day mean daily spend minus, at, and plus one standard deviation, projected across the remaining days of the billing period. They assume your recent pattern continues.'})}
    ${card('Scenarios', table([
      {h: 'Scenario', f: r => `<b>${esc(r[0])}</b>`},
      {h: 'Daily rate', num: 1, f: r => fmtUSD(r[1].daily_rate)},
      {h: 'Projected end of period', num: 1, f: r => fmtUSD(r[1].end_of_period_cost)},
      {h: 'vs today', num: 1, f: r => '+' + fmtUSD(r[1].end_of_period_cost - f.period_used)},
    ], Object.entries(f.scenarios)), {badge: BADGE.forecast})}`;
  C.forecastFan($('#fan2', page), {history: burn.series, scenarios: f.scenarios,
    remainingDays: f.remaining_days, height: 300});
  $('#fl2', page).innerHTML = `<span class="it"><span class="swatch" style="background:var(--s1)"></span>
    Cumulative actual (estimated cost)</span>
    <span class="it"><span class="swatch" style="background:var(--s1);opacity:.35"></span>
    Forecast band (conservative → high)</span>`;
};

/* ---------- budgets ---------- */
VIEWS.budgets = async (page) => {
  const b = await api('budgets');
  const st = S.opts.settings;
  page.innerHTML = `
    ${card('Budget vs actual vs forecast', `<div class="stack">${b.lines.map(l => l.configured ? `
      <div class="item">
        <div class="hd">${esc(l.name)}<span class="spacer"></span>${statusChip(l.status)}</div>
        <div class="grid g4" style="gap:8px;margin:4px 0">
          ${kpi('Budget', l.unit === 'tokens' ? fmtNum(l.budget) : fmtUSD(l.budget), null, {small: 1})}
          ${kpi('Actual', l.unit === 'tokens' ? fmtNum(l.actual) : fmtUSD(l.actual),
            fmtPct(l.used_pct) + ' used', {small: 1, badge: BADGE.estimated})}
          ${kpi('Forecast', l.forecast == null ? null
            : (l.unit === 'tokens' ? fmtNum(l.forecast) : fmtUSD(l.forecast)),
            l.forecast_pct == null ? '' : fmtPct(l.forecast_pct) + ' of budget',
            {small: 1, badge: BADGE.forecast})}
          ${kpi('Variance', l.variance == null ? null
            : (l.variance >= 0 ? '+' : '') + (l.unit === 'tokens' ? fmtNum(l.variance) : fmtUSD(l.variance)),
            l.variance == null ? '' : (l.variance > 0 ? 'over budget' : 'under budget'), {small: 1})}
        </div>
        <div class="meter ${l.status}"><i style="width:${Math.min(l.used_pct, 100)}%"></i></div>
        <div class="mt">
          ${l.thresholds_breached.length ? `<span>⚠️ breached ${l.thresholds_breached.join('%, ')}%</span>` : '<span>No threshold breached</span>'}
          ${l.thresholds_forecast_breach.length
            ? `<span style="color:var(--serious-ink)">forecast to breach ${l.thresholds_forecast_breach.join('%, ')}%</span>` : ''}
        </div></div>`
      : `<div class="item"><div class="hd">${esc(l.name)}<span class="spacer"></span>
          <span class="badge na">not configured</span></div>
          <div class="dt">Actual to date: ${l.unit === 'tokens' ? fmtNum(l.actual) : fmtUSD(l.actual)}
            (estimated). Set a budget below to track variance and get threshold warnings.</div></div>`
      ).join('')}</div>`, {badge: BADGE.estimated,
      hint: `alert thresholds: ${b.thresholds_pct.join('%, ')}%`})}
    ${card('Configure budgets, limits and thresholds', `
      <div class="grid g3">
        <div><div class="hd" style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;font-weight:620;margin-bottom:6px">Budgets</div>
          <label class="pop" style="position:static;display:block;border:none;box-shadow:none;padding:0">
            <div class="hd">Monthly budget (USD)</div>
            <input type="number" step="1" id="b-monthly" value="${st.budgets.monthly_usd ?? ''}" placeholder="not set">
            <div class="hd">Daily budget (USD)</div>
            <input type="number" step="0.5" id="b-daily" value="${st.budgets.daily_usd ?? ''}" placeholder="not set">
            <div class="hd">Monthly token budget</div>
            <input type="number" id="b-tokens" value="${st.budgets.monthly_tokens ?? ''}" placeholder="not set">
          </label></div>
        <div><div class="hd" style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;font-weight:620;margin-bottom:6px">Plan limits</div>
          <label class="pop" style="position:static;display:block;border:none;box-shadow:none;padding:0">
            <div class="hd">Monthly cost allowance (USD)</div>
            <input type="number" step="1" id="l-cost" value="${st.limits.monthly_cost_allowance_usd ?? ''}" placeholder="not exposed by ${agentWord()} data">
            <div class="hd">Monthly token allowance</div>
            <input type="number" id="l-tok" value="${st.limits.monthly_token_allowance ?? ''}" placeholder="not exposed by ${agentWord()} data">
            <div class="hd">Monthly request allowance</div>
            <input type="number" id="l-req" value="${st.limits.monthly_request_allowance ?? ''}" placeholder="not exposed by ${agentWord()} data">
            <div class="hd">Remaining credits (USD)</div>
            <input type="number" step="0.01" id="l-cred" value="${st.limits.remaining_credits_usd ?? ''}" placeholder="not exposed by ${agentWord()} data">
          </label></div>
        <div><div class="hd" style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;font-weight:620;margin-bottom:6px">Alert thresholds</div>
          <label class="pop" style="position:static;display:block;border:none;box-shadow:none;padding:0">
            <div class="hd">Comma-separated %</div>
            <input type="text" id="t-thr" value="${st.alert_thresholds_pct.join(',')}"
              style="width:100%;padding:5px 7px;border-radius:6px;border:1px solid var(--border);background:var(--surface-2)">
          </label>
          <button class="chip on" id="savecfg" style="margin-top:10px;width:100%;justify-content:center">Save configuration</button>
          <div class="note" style="margin-top:8px">Writes to <span class="mono">config/settings.json</span>.
          Leave a field blank to keep it unconfigured — the dashboard will report it as unavailable
          rather than inventing a value.</div>
        </div>
      </div>`, {footer: `Plan allowances are NOT available from ${agentWord()} data. Anything you enter here is your own declared figure, used only to compute usage-vs-limit and days-until-limit.`})}`;
  $('#savecfg', page).onclick = async () => {
    const v = id => { const x = $('#' + id, page).value.trim(); return x === '' ? null : +x; };
    await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        budgets: {monthly_usd: v('b-monthly'), daily_usd: v('b-daily'), monthly_tokens: v('b-tokens')},
        limits: {monthly_cost_allowance_usd: v('l-cost'), monthly_token_allowance: v('l-tok'),
                 monthly_request_allowance: v('l-req'), remaining_credits_usd: v('l-cred')},
        alert_thresholds_pct: $('#t-thr', page).value.split(',').map(x => +x.trim()).filter(Boolean),
      })}).then(r => r.json());
    S.opts = await fetch('/api/options').then(r => r.json());
    bust(); render();
  };
  const bl = b.lines.filter(l => l.configured && l.budget);
  if (bl.length) addChart(page, 'Budget used', el => C.barsH(el, {
    rows: bl, label: l => l.name, value: l => 100 * l.actual / l.budget, fmt: v => fmtPct(v), max: 100,
    color: l => l.actual >= l.budget ? 'var(--critical)' : l.actual >= 0.75 * l.budget ? 'var(--warning)' : seriesVar(2)}),
    {badge: BADGE.estimated, after: '.nothing'});
};

/* ---------- scorecard ---------- */
VIEWS.scorecard = async (page) => {
  const [sc, advisor] = await Promise.all([api('scorecard'), api('advisor')]);
  page.innerHTML = `
    ${card('AI FinOps Score', `<div class="scorewrap">
      <div style="text-align:center">
        <div class="scorenum">${sc.score}</div>
        <div class="scoregrade">out of 100 · grade <b>${sc.grade}</b></div>
        <div class="chart" id="sg" style="width:190px;margin-top:6px"></div></div>
      <div style="flex:1;min-width:280px" class="stack">${sc.dimensions.map(d => `
        <div><div style="display:flex;justify-content:space-between;font-size:12px">
          <span><b>${esc(d.name)}</b> <span class="note">weight ${d.weight}</span></span>
          <span style="font-variant-numeric:tabular-nums;font-weight:640">${d.score}</span></div>
        <div class="meter ${d.score >= 75 ? 'healthy' : d.score >= 50 ? 'high'
          : d.score >= 30 ? 'approaching' : 'critical'}"><i style="width:${d.score}%"></i></div>
        <div class="note">${esc(d.detail)}</div></div>`).join('')}
      </div></div>`, {badge: BADGE.estimated,
      footer: 'The score is a weighted mean of the dimensions above, each graded against a reference in config/settings.json → scorecard. It is a self-consistency measure of your own usage, not a benchmark against other users.'})}
    <div class="grid g3">
      ${card('✅ What is good', `<ul style="margin:0 0 0 18px;font-size:12.5px">${
        sc.what_is_good.map(x => `<li style="margin-bottom:6px">${esc(x)}</li>`).join('')
        || '<li class="na">Nothing scored above 60 in this range</li>'}</ul>`)}
      ${card('⚠️ Needs attention', `<ul style="margin:0 0 0 18px;font-size:12.5px">${
        sc.needs_attention.map(x => `<li style="margin-bottom:6px">${esc(x)}</li>`).join('')
        || '<li class="na">Nothing scored below 70</li>'}</ul>`)}
      ${card('🎯 Biggest opportunity', sc.biggest_opportunity ? `
        <div class="item sev-high"><div class="hd">${esc(sc.biggest_opportunity.title)}</div>
          <div class="dt">${esc(sc.biggest_opportunity.detail)}</div>
          <div class="mt"><span>Estimated excess ${fmtUSD(sc.biggest_opportunity.estimated_excess_usd)}</span>
            <span>of ${fmtUSD(sc.biggest_opportunity.exposed_cost_usd)} exposed</span></div>
          <div class="dt"><b>Action:</b> ${esc(sc.biggest_opportunity.action || '')}</div></div>`
        : '<div class="empty">Nothing flagged</div>', {badge: BADGE.estimated})}
    </div>`;
  C.gauge($('#sg', page), {pct: sc.score,
    status: sc.score >= 75 ? 'healthy' : sc.score >= 55 ? 'high'
      : sc.score >= 40 ? 'approaching' : 'critical', label: 'FinOps score', size: 190});
  renderAdvisorHero(page, advisor);
  addChart(page, 'Score by dimension', el => C.barsH(el, {
    rows: [...sc.dimensions].sort((a, b) => a.score - b.score), label: x => x.name, value: x => x.score,
    fmt: v => Math.round(v) + '/100', max: 100,
    color: x => x.score < 50 ? 'var(--critical)' : x.score < 75 ? 'var(--warning)' : 'var(--good)'}),
    {hint: 'Weakest first', after: ':scope > .card'});
};

/* ---------- developer / Claude Code ---------- */
VIEWS.developer = async (page) => {
  const d = await api('developer');
  const u = d.unavailable;
  page.innerHTML = `
    <div class="grid g2">
      ${card('Cost per repository', '<div class="chart" id="rb"></div>', {badge: BADGE.estimated,
        hint: 'sandbox agent workspaces excluded'})}
      ${card('Tool usage', '<div class="chart" id="tb"></div>', {badge: BADGE.actual,
        hint: 'top tools by call count'})}
    </div>
    ${card('Repository FinOps', table([
      {h: 'Repository', f: r => esc(r.repository)},
      {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
      {h: 'Prompts', num: 1, f: r => fmtInt(r.prompts)},
      {h: 'Files touched', num: 1, f: r => fmtInt(r.files_touched)},
      {h: 'Cost / session', num: 1, f: r => fmtUSD(r.cost_per_session)},
      {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
    ], d.cost_per_repository), {badge: BADGE.estimated})}
    <div class="grid g2">
      ${card('Cost by git branch', table([
        {h: 'Branch', f: r => `<span class="mono">${esc(r.branch)}</span>`},
        {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
        {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
        {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
      ], d.branches), {badge: BADGE.estimated,
        hint: 'branch recorded per session in the transcript'})}
      ${card('Most-touched files', table([
        {h: 'File', trunc: 1, title: r => r.path, f: r => `<span class="mono">${esc(r.path)}</span>`},
        {h: 'Ops', f: r => esc(r.ops)},
        {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
        {h: 'Touches', num: 1, f: r => fmtInt(r.touches)},
      ], d.files), {badge: BADGE.actual})}
    </div>
    ${card(`Not available from ${esc(agentWord())} data`, `<dl class="kv">
      ${Object.entries(u).map(([k, v]) => `<dt>${esc(k.replace(/_/g, ' '))}</dt>
        <dd class="na">${esc(v)}</dd>`).join('')}
    </dl>`, {badge: '<span class="badge na">Unavailable</span>',
      footer: 'Agent transcripts record which files a tool call targeted, but not diff sizes, commits, PRs or issue linkage. Correlating spend with commits or PRs would require a separate git/GitHub data source, which this dashboard deliberately does not fabricate.'})}`;
  C.barsH($('#rb', page), {rows: d.cost_per_repository.slice(0, 12), label: r => r.repository,
    value: r => r.cost, color: seriesVar(2)});
  C.barsH($('#tb', page), {rows: d.tools.slice(0, 14), label: r => r.name, value: r => r.calls,
    fmt: fmtInt, color: seriesVar(0)});
};

/* ---------- search ---------- */
VIEWS.search = async (page) => {
  const term = S.searchTerm || '';
  if (term.trim().length < 2) { page.innerHTML = '<div class="empty">Type at least 2 characters</div>';
    return; }
  const r = await fetch(`/api/search?q=${encodeURIComponent(term)}`).then(x => x.json());
  const sec = (title, html, n) => card(`${title} (${n})`, n ? html
    : '<div class="empty">No matches</div>');
  page.innerHTML = `
    <div class="note">Global search for "<b>${esc(term)}</b>" — searches prompt text, session IDs
      and titles, git branches, project names and paths, model IDs, tool names and targets,
      and dates. Independent of the global filters.</div>
    ${sec('Prompts', '<div id="sp"></div>', r.prompts.length)}
    ${sec('Sessions', '<div id="ss"></div>', r.sessions.length)}
    <div class="grid g3">
      ${sec('Projects', table([{h: 'Project', f: x => esc(x.name)},
        {h: 'Path', trunc: 1, f: x => `<span class="mono sub">${esc(x.path || '—')}</span>`}],
        r.projects), r.projects.length)}
      ${sec('Models', table([{h: 'Model', f: x => esc(modelName(x.model))},
        {h: 'Requests', num: 1, f: x => fmtInt(x.requests)},
        {h: 'Est. cost', num: 1, f: x => fmtUSD(x.cost)}], r.models), r.models.length)}
      ${sec('Days', table([{h: 'Day', f: x => esc(x.day)},
        {h: 'Requests', num: 1, f: x => fmtInt(x.requests)},
        {h: 'Est. cost', num: 1, f: x => fmtUSD(x.cost)}], r.days), r.days.length)}
    </div>
    ${sec('Tools & targets', table([{h: 'Tool', f: x => esc(x.name)},
      {h: 'Target', trunc: 1, title: x => x.target || '', f: x => `<span class="mono">${esc(x.target || '—')}</span>`},
      {h: 'Calls', num: 1, f: x => fmtInt(x.n)}], r.tools), r.tools.length)}`;
  if (r.prompts.length) {
    $('#sp', page).innerHTML = table([
      {h: 'When', f: x => `<span class="mono">${esc((x.ts || '').slice(0, 16).replace('T', ' '))}</span>`},
      {h: 'Prompt', trunc: 1, title: x => x.preview, f: x => esc(x.preview)},
      {h: 'Category', f: x => `<span class="pill">${esc(x.category)}</span>`},
      {h: 'Tokens', num: 1, f: x => fmtNum(x.billable_tokens)},
      {h: 'Est. cost', num: 1, f: x => fmtUSD(x.est_cost_usd)},
    ], r.prompts, {onRow: 1});
    wireTable($('#sp', page), r.prompts, x => openPrompt(x.prompt_id));
  }
  if (r.sessions.length) {
    $('#ss', page).innerHTML = table([
      {h: 'Session', trunc: 1, f: x => esc(x.title || shortId(x.session_id))},
      {h: 'Project', f: x => esc(x.project)},
      {h: 'Branch', f: x => x.git_branch ? `<span class="mono">${esc(x.git_branch)}</span>` : '—'},
      {h: 'Tokens', num: 1, f: x => fmtNum(x.billable_tokens)},
      {h: 'Est. cost', num: 1, f: x => fmtUSD(x.est_cost_usd)},
    ], r.sessions, {onRow: 1});
    wireTable($('#ss', page), r.sessions, x => openSession(x.session_id));
  }
};

/* ---------- exports & data provenance ---------- */
const EXPORTS = [['usage', 'Usage timeline'], ['prompts', 'Prompts'], ['sessions', 'Sessions'],
  ['models', 'Model breakdown'], ['projects', 'Projects'], ['costs', 'Costs'],
  ['waste', 'Waste findings'], ['recommendations', 'FinOps recommendations'],
  ['forecast', 'Forecast scenarios']];
VIEWS.exports = async (page) => {
  const m = S.opts.meta, p = S.opts.pricing;
  page.innerHTML = `
    ${card('Export', `<div class="tbl-wrap"><table class="tbl">
      <thead><tr><th class="nosort">Dataset</th><th class="nosort">CSV</th>
      <th class="nosort">JSON</th></tr></thead><tbody>
      ${EXPORTS.map(([k, l]) => `<tr><td>${l}</td>
        <td><a href="/api/export/${k}?${qs()}&format=csv">Download CSV</a></td>
        <td><a href="/api/export/${k}?${qs()}&format=json">Download JSON</a></td></tr>`).join('')}
      <tr><td><b>Full PDF report</b></td><td colspan="2">
        <a href="/api/export/report?${qs()}" target="_blank">Open printable report</a>
        <span class="note"> — then Cmd/Ctrl+P → Save as PDF</span></td></tr>
      </tbody></table></div>`,
      {hint: 'exports honour the current global filters',
       footer: 'Prompt exports include your full prompt text. Handle the files accordingly.'})}
    ${card('Data provenance', `<dl class="kv">
      <dt>Source</dt><dd class="mono">${esc(m.source_dir)}</dd>
      <dt>Transcript files parsed</dt><dd>${esc(m.transcript_files)}</dd>
      <dt>Warehouse built</dt><dd>${esc(m.built_at)}</dd>
      <dt>Data range</dt><dd>${esc(S.opts.date_range.first)} → ${esc(S.opts.date_range.last)}</dd>
      <dt>Cost basis</dt><dd><span class="badge est">Estimated</span> token counts × configured pricing</dd>
      <dt>Price table updated</dt><dd>${esc(p.updated)} · ${esc(p.source)}</dd>
      <dt>Rebuild command</dt><dd class="mono">python3 -m finops.etl</dd>
    </dl>`, {badge: BADGE.actual})}
    ${card('Accuracy contract', `
      <div class="grid g4">
        <div class="item"><div class="hd">${BADGE.actual} Actual</div>
          <div class="dt">Read directly from your transcripts: token counts, timestamps, models,
          tool calls, file paths, session and project identity.</div></div>
        <div class="item"><div class="hd">${BADGE.estimated} Estimated</div>
          <div class="dt">Derived: every dollar figure. ${agentWord()} data contains no billed
          amounts, so cost = tokens × <span class="mono">config/pricing.json</span>.</div></div>
        <div class="item"><div class="hd">${BADGE.forecast} Forecast</div>
          <div class="dt">Projected from history. Assumes recent patterns continue; it is not a
          commitment or a quote.</div></div>
        <div class="item"><div class="hd">${BADGE.recommendation} Recommendation</div>
          <div class="dt">Modelled opportunity. Savings estimates hold token usage constant on the
          alternative and do not model output quality.</div></div>
      </div>
      <h3 style="font-size:12.5px;margin:14px 0 6px">Deliberately not fabricated</h3>
      <dl class="kv">
        <dt>Plan tier &amp; allowance</dt><dd class="na">${esc(naLabel())} — configure it yourself if you know it</dd>
        <dt>Remaining credits</dt><dd class="na">${esc(naLabel())}</dd>
        <dt>Message / request allowance</dt><dd class="na">${esc(naLabel())}</dd>
        <dt>Billed invoice amounts</dt><dd class="na">${esc(naLabel())}</dd>
        <dt>Assistant response text</dt><dd class="na">Not stored — only usage metadata is extracted</dd>
        <dt>Lines changed / commits / PRs</dt><dd class="na">${esc(naLabel())}</dd>
      </dl>`)}`;
};

/* ============================ focus cues ============================ */
// "What needs me first": one ranked list built from the diagnosis, used for the nav
// badges and the Focus strip at the top of the overview and diagnosis pages.
function focusItems(d) {
  const out = [];
  for (const x of d.live_sessions || []) if (x.severity !== 'ok')
    out.push({lvl: x.severity === 'high' ? 1 : 2, text: `Running session at ${fmtNum(x.context)} context: ${x.title || shortId(x.session_id)}`, view: 'diagnose', anchor: 'dx-live'});
  for (const m of d.memory_suggestions || []) if (m.kind === 'security')
    out.push({lvl: 1, text: `Credentials pasted in prompts (${m.sessions} sessions): rotate them`, view: 'diagnose', anchor: 'dx-mem'});
  for (const r of d.recommendations || []) if (r.priority === 1)
    out.push({lvl: 1, text: r.title + (r.est_savings_usd ? ` (~${fmtUSD(r.est_savings_usd)})` : ''), view: 'diagnose', anchor: 'dx-recs'});
  for (const p of d.projects || []) for (const i of p.issues) if (i.severity === 'high')
    out.push({lvl: 1, text: `${p.name}: ${i.title}`, view: 'diagnose', anchor: 'dx-issues'});
  const s0 = (d.session_health || [])[0];
  if (s0) out.push({lvl: 2, text: `Heaviest session: ${s0.title || shortId(s0.session_id)} (peak ${fmtNum(s0.peak)})`, view: 'diagnose', anchor: 'dx-past'});
  for (const p of d.projects || []) for (const i of p.issues) if (i.severity === 'medium')
    out.push({lvl: 2, text: `${p.name}: ${i.title}`, view: 'diagnose', anchor: 'dx-issues'});
  return out.sort((a, b) => a.lvl - b.lvl);
}
function focusStrip(d, n = 5) {
  const items = focusItems(d);
  if (!items.length) return '';
  return `<section class="focus"><div class="focus-hd">🎯 Focus on these first
      <span class="note">${items.filter(i => i.lvl === 1).length} urgent · ${items.filter(i => i.lvl === 2).length} next</span></div>
    ${items.slice(0, n).map((it, k) => `<a class="focus-it l${it.lvl}" data-go="${it.view}" data-anchor="${it.anchor}">
      <span class="fn">${k + 1}</span><span class="ft">${esc(it.text)}</span>
      <span class="fl">${it.lvl === 1 ? 'Fix first' : 'Next'}</span></a>`).join('')}</section>`;
}
function wireFocus(root) {
  root.querySelectorAll('.focus-it').forEach(a => a.onclick = async () => {
    if (S.view !== a.dataset.go) { go(a.dataset.go); await new Promise(r => setTimeout(r, 400)); }
    const el = document.getElementById(a.dataset.anchor);
    if (el) { el.scrollIntoView({behavior: 'smooth', block: 'start'}); el.classList.add('flash');
      setTimeout(() => el.classList.remove('flash'), 1600); }
  });
}
async function updateNavBadges() {
  const set = (id, n, lvl) => { const el = document.querySelector(`[data-nb="${id}"]`);
    if (el) { el.textContent = n ? n : ''; el.className = 'nb' + (n ? ' l' + lvl : ''); } };
  try {
    const [d, w, a] = await Promise.all([hasPriced() ? api('diagnose') : null, api('waste'), api('anomalies')]);
    if (d) { const f = focusItems(d), urgent = f.filter(i => i.lvl === 1).length;
      set('diagnose', urgent || f.length, urgent ? 1 : 2); }
    const hw = w.findings.filter(x => x.severity === 'high').length;
    set('waste', hw || w.findings.length, hw ? 1 : 2);
    set('anomalies', (a.anomalies || []).length, 2);
    const lv = await fetch('/api/live?agents=' + encodeURIComponent(S.filter.agents.join(','))).then(r => r.json());
    const heavy = (lv.sessions || []).filter(x => x.severity !== 'ok').length;
    set('live', (lv.sessions || []).length, heavy ? 1 : 2);
  } catch { /* badges are a hint; never block the page */ }
}

/* ---------- agent-aware chrome ---------- */
// What the current agent selection can show: Claude-only pages need Claude Code in the
// selection; cost pages need at least one agent whose usage is priced.
const selAgents = () => (S.opts?.agents || []).filter(a => S.filter.agents.includes(a.id));
const hasClaude = () => S.filter.agents.includes('claude');
const hasPriced = () => selAgents().some(a => a.data !== 'activity');
const hasCloudKey = () => Object.values(S.opts?.cloud || {}).some(Boolean);
const navAllowed = scope => !scope || (scope === 'claude' ? hasClaude()
  : scope === 'cloud' ? hasCloudKey() : hasPriced());
// "Claude Code", "Codex", or "agent" for a mixed selection: used in data-availability text
const agentWord = () => { const a = selAgents(); return a.length === 1 ? a[0].name : 'agent'; };
// Whose usage this is. Read from ~/.claude.json by the server, so a screenshot
// or a shared dashboard always says which account the numbers belong to.
function whoBlock() {
  const el = $('#who'), a = S.opts?.settings?.account || {};
  if (!el) return;
  if (!a.name && !a.email) { el.innerHTML = ''; return; }
  const initials = (a.name || a.email || '?').split(/[\s@.]+/).filter(Boolean)
    .slice(0, 2).map(w => w[0].toUpperCase()).join('');
  el.innerHTML = `<div class="av">${esc(initials)}</div>
    <div class="id">
      <div class="nm">${esc(a.name || a.email)}</div>
      <div class="em" title="${esc(a.email || '')}">${esc(a.email || '')}</div>
      ${a.plan || a.org ? `<div class="pl">${esc([a.plan, a.org].filter(Boolean).join(' · '))}</div>` : ''}
    </div>`;
  el.title = `Claude Code is signed in as ${a.name || ''} <${a.email || ''}>`.trim();
}

function applyAgentChrome() {
  const a = selAgents();
  const label = a.length === 1 ? a[0].name.replace(/ (Code|CLI)$/, '') : a.length ? 'Multi-agent' : 'AI';
  whoBlock();
  const mark = $('.brand .mark');
  if (mark) mark.innerHTML = `<span class="dot"></span>${esc(label)} FinOps`;
  const sub = $('.brand .sub');
  if (sub) sub.textContent = a.length > 1 ? a.map(x => x.name).join(' + ') : 'Command Center';
  document.title = `${label} FinOps Command Center`;
  const scopes = Object.fromEntries(NAV.flatMap(([, items]) => items.map(([id, , , sc]) => [id, sc])));
  document.querySelectorAll('.nav a').forEach(el => el.style.display = navAllowed(scopes[el.dataset.view]) ? '' : 'none');
  // hide a group header when every item under it is hidden
  document.querySelectorAll('.nav .group').forEach(g => {
    let el = g.nextElementSibling, any = false;
    while (el && !el.classList.contains('group')) { if (el.style.display !== 'none') any = true; el = el.nextElementSibling; }
    g.style.display = any ? '' : 'none';
  });
  // leaving a page the new selection can't show: fall back to the overview
  if (scopes[S.view] && !navAllowed(scopes[S.view])) S.view = 'overview';
}

/* ============================ router ============================ */
const TITLES = Object.fromEntries(NAV.flatMap(([, items]) => items.map(([id, , l]) => [id, l])));
TITLES.search = 'Global search';

function go(view) { S.view = view; closeDrawer(); render(); }

async function render() {
  C.hideTip();   // a tooltip whose chart is about to be replaced must not linger
  // A fresh container per render: if a slower, superseded view resolves later it
  // writes into its own detached node and can never clobber the current view.
  const stale = $('#page');
  const page = document.createElement('div');
  page.className = 'page';
  page.id = 'page';
  stale.replaceWith(page);
  applyAgentChrome();
  document.querySelectorAll('.nav a').forEach(a =>
    a.classList.toggle('on', a.dataset.view === S.view));
  $('#ttl').textContent = TITLES[S.view] || S.view;
  const f = S.filter;
  const bits = [];
  if (f.start || f.end) bits.push(`<b>${esc(f.start || '…')} → ${esc(f.end || '…')}</b>`);
  else bits.push('<b>all time</b>');
  if (f.models.length) bits.push(f.models.map(m => esc(modelName(m))).join(', '));
  if (f.projects.length) bits.push(f.projects.map(p =>
    esc((S.opts.projects.find(x => String(x.project_id) === p) || {}).name || p)).join(', '));
  if (f.categories.length) bits.push(f.categories.join(', '));
  if (!f.include_sandbox) bits.push('excl. sandbox');
  $('#crumbs').innerHTML = bits.join(' <span style="opacity:.4">·</span> ');
  filterBar();
  page.innerHTML = '<div class="loading">Computing…</div>';
  updateNavBadges();
  try {
    await (VIEWS[S.view] || VIEWS.overview)(page);
  } catch (e) {
    page.innerHTML = `<div class="card"><div class="body">
      <b>Something went wrong rendering this view.</b>
      <pre class="prompt-text" style="margin-top:8px">${esc(e.stack || e)}</pre></div></div>`;
  }
}

/* ============================ boot ============================ */
(async function boot() {
  const saved = localStorage.getItem('finops-theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
  shell();
  S.opts = await fetch('/api/options').then(r => r.json());
  S.filter.agents = defaultAgents();
  applyRange('30d');
  // ?view=<name> opens straight to one screen, so a link (or a screenshot run)
  // can point at a specific report rather than always landing on the overview.
  const want = new URLSearchParams(location.search).get('view');
  if (want && NAV.some(([, items]) => items.some(([id]) => id === want))) S.view = want;
  await render();
  if (!want) maybeFirstTour();   // arriving on a deep link is not a first visit
  let t; addEventListener('resize', () => { clearTimeout(t); t = setTimeout(render, 220); });
})();

/* ============================ agents ============================ */
// Last choice if still valid, else the agent with the most usage (Claude on most machines).
function defaultAgents() {
  const ag = S.opts?.agents || [];
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem('finops-agents') || '[]'); } catch (_) {}
  saved = saved.filter(id => ag.some(a => a.id === id));
  if (saved.length) return saved;
  const top = [...ag].sort((a, b) => b.requests - a.requests)[0];
  return top ? [top.id] : [];
}

VIEWS.agents = async (page) => {
  const d = await api('by_agent');
  const all = S.opts.agents || [];
  const colorOf = id => seriesVar(Math.max(all.findIndex(a => a.id === id), 0));
  const dataBadge = k => ({full: '<span class="badge">Full data</span>',
    tokens: '<span class="badge">Tokens + model</span>',
    activity: '<span class="badge rec">Activity only</span>'}[k] || '');
  // pivot daily rows into one row per day with a column per agent
  const byDay = new Map();
  for (const r of d.daily) {
    const row = byDay.get(r.day) || {day: r.day};
    row[r.agent] = S.metric === 'cost' ? r.cost : r.tokens;
    byDay.set(r.day, row);
  }
  const shown = d.agents.map(a => a.agent);
  page.innerHTML = `
    <div class="note">Showing: <b>${esc(shown.map(id => all.find(a => a.id === id)?.name || id).join(' + ') || 'nothing in range')}</b>.
      Click an agent chip above to see only it; <b>Cmd/Ctrl-click</b> to add more, or <b>All</b> for every agent together.
      Every other page follows the same selection.</div>
    <div class="grid g4">${d.agents.map(a => kpi(a.name,
      a.cost ? fmtUSD(a.cost) : fmtNum(a.tokens) + ' tok',
      `${fmtInt(a.sessions)} sessions · ${fmtInt(a.prompts)} prompts · ${fmtInt(a.active_days)} active days`,
      {badge: a.cost ? BADGE.estimated : ''})).join('')}</div>
    ${card('Side by side', `<div id="ag-tbl"></div>`, {flush: 1, badge: BADGE.estimated,
      hint: 'Costs are estimated at each provider\'s API list price'})}
    ${card(`Daily ${S.metric === 'cost' ? 'estimated cost' : 'tokens'} by agent`, `<div class="chart" id="ag-trend"></div><div id="ag-leg"></div>`,
      {actions: `<button class="chip ${S.metric === 'cost' ? 'on' : ''}" data-m="cost">Cost</button>
                 <button class="chip ${S.metric !== 'cost' ? 'on' : ''}" data-m="tokens">Tokens</button>`})}
    ${card('What each agent records on this machine', `<div class="stack">${all.map(a => `
      <div class="item"><div class="hd">${esc(a.name)} ${dataBadge(a.data)}<span class="spacer"></span>
        <span class="note">${a.requests ? `${esc(a.first)} → ${esc(a.last)} · ${fmtInt(a.sessions)} sessions` : 'installed, no usage recorded'}</span></div>
        <div class="dt">${esc(a.note)}</div></div>`).join('')}</div>`)}`;
  $('#ag-tbl', page).innerHTML = table([
    {h: 'Agent', f: r => `<b>${esc(r.name)}</b> ${dataBadge(r.data)}`},
    {h: 'Est. cost', num: 1, f: r => r.data === 'activity' ? '<span class="na">not priced</span>' : fmtUSD(r.cost)},
    {h: 'Tokens', num: 1, f: r => r.tokens ? fmtNum(r.tokens) : '<span class="na">not recorded</span>'},
    {h: 'Output', num: 1, f: r => r.output_tokens ? fmtNum(r.output_tokens) : '—'},
    {h: 'Cache reads', num: 1, f: r => r.cache_read_tokens ? fmtNum(r.cache_read_tokens) : '—'},
    {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
    {h: 'Prompts', num: 1, f: r => fmtInt(r.prompts)},
    {h: 'Tool calls', num: 1, f: r => fmtInt(r.tool_calls)},
    {h: 'Cost / prompt', num: 1, f: r => r.cost && r.prompts ? fmtUSD(r.cost / r.prompts) : '—'},
    {h: 'Models', trunc: 1, title: r => r.models, f: r => esc((r.models || '').split(',').map(modelName).join(', '))},
  ], d.agents);
  const series = shown.map(id => ({key: id, label: all.find(a => a.id === id)?.name || id, color: colorOf(id)}));
  C.timeSeries($('#ag-trend', page), {rows: [...byDay.values()], x: 'day', series, type: 'bar',
    fmt: S.metric === 'cost' ? fmtUSD : fmtNum, height: 230, xLabel: shortDay});
  C.legend($('#ag-leg', page), series.map(s => ({label: s.label, color: s.color})));
  page.querySelectorAll('[data-m]').forEach(b => b.onclick = () => { S.metric = b.dataset.m; render(); });
  addChart(page, 'Share by agent', el => {
    el.innerHTML = '<div class="grid g2"><div><div class="note">Estimated cost</div><div id="ag-d1"></div></div>' +
      '<div><div class="note">Tokens</div><div id="ag-d2"></div></div></div>';
    C.donut($('#ag-d1', el), {rows: d.agents.filter(a => a.cost > 0), label: a => a.name, value: a => a.cost,
      color: a => colorOf(a.agent), size: 170, centerValue: fmtUSD(d.agents.reduce((x, a) => x + a.cost, 0)), centerLabel: 'estimated'});
    C.donut($('#ag-d2', el), {rows: d.agents.filter(a => a.tokens > 0), label: a => a.name, value: a => a.tokens, fmt: fmtNum,
      color: a => colorOf(a.agent), size: 170, centerValue: fmtNum(d.agents.reduce((x, a) => x + (a.tokens || 0), 0)), centerLabel: 'tokens'});
    const lg = document.createElement('div'); el.appendChild(lg);
    C.legend(lg, d.agents.map(a => ({label: `${a.name} · ${a.cost ? fmtUSD(a.cost) + ' · ' : ''}${fmtNum(a.tokens)} tok`, color: colorOf(a.agent)})));
  }, {badge: BADGE.estimated});
};

/* ============================ machine actions ============================ */
async function doAction(path, body = {}) {
  const r = await fetch(`/api/do/${path}`, {method: 'POST',
    headers: {'X-FinOps-Action': '1', 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const d = await r.json();
  if (d.error) throw new Error(d.error.split('\n').filter(Boolean).pop());
  return d;
}
// Poll a background job, streaming its log into `host`; resolves with the final status.
async function followJob(id, host) {
  for (;;) {
    const j = await fetch(`/api/job/${id}`).then(r => r.json());
    if (host) {
      let box = host.querySelector('.prompt-text');
      if (!box) { host.innerHTML = '<div class="prompt-text"></div>'; box = host.firstChild; }
      // follow the newest line unless the user scrolled up to read
      const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
      box.textContent = j.log.join('\n') || 'Starting…';
      if (atEnd) box.scrollTop = box.scrollHeight;
    }
    if (j.state !== 'running') return j;
    await new Promise(r => setTimeout(r, 1200));
  }
}

/* ---------- sync ---------- */
const ago = iso => { if (!iso) return 'never';
  const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
  return m < 1 ? 'just now' : m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`; };
async function syncLabel() {
  const b = $('#sync'); if (!b) return;
  const d = await fetch('/api/sync').then(r => r.json()).catch(() => ({}));
  if (d.job && d.job.state === 'running') return runSync();
  b.title = `Last synced ${ago(d.built_at)}. Re-read every agent's local data so the dashboard is current`;
  b.textContent = `⟳ Sync · ${ago(d.built_at)}`;
}
async function runSync() {
  const b = $('#sync');
  b.disabled = true; b.textContent = '⟳ Syncing…';
  try {
    const {job} = await doAction('sync');
    const j = await followJob(job);
    if (j.state !== 'done') throw new Error(j.log.pop());
    S.opts = await fetch('/api/options').then(r => r.json()); bust(); render();
  } catch (e) { b.title = 'Sync failed: ' + e.message; }
  b.disabled = false; syncLabel();
}

/* ---------- free models ---------- */
VIEWS.freemodels = async (page) => {
  const d = await fetch('/api/free_models').then(r => r.json());
  page.innerHTML = `
    <div class="note">${esc(d.how_it_works)} Free models are weaker than Claude: use them for routine
      work (see <a data-go="modelswitch">Model switch</a> for which work that is) and keep Claude for hard problems.</div>
    ${!d.bin_on_path ? `<div class="item sev-medium"><div class="dt">${esc(d.bin_dir)} is not on your PATH, so the new commands
      won't run by name. Add <code>export PATH="$HOME/.local/bin:$PATH"</code> to ~/.zshrc.</div></div>` : ''}
    ${card('How to use and test a free model', `<div class="stack">
      <div class="dt"><b>1. Test:</b> click <b>▶ Test it</b> on an added model. It asks the model directly, then runs a real one-line Claude Code session through the new command.</div>
      <div class="dt"><b>2. Use:</b> open a terminal in any project and run the command, e.g. <code>claude-qwen-coder</code>. It's normal Claude Code, running on the free model.</div>
      <div class="dt"><b>3. Check which model is on:</b> type <code>/model</code> or <code>/status</code> inside that session. It shows the Qwen model.</div>
      <div class="dt"><b>Will it appear in /model in my normal <code>claude</code>?</b> No. Normal <code>claude</code> talks only to Anthropic, which doesn't serve Qwen. Each free model lives behind its own command, so your Claude sessions stay unchanged. Inside a free-model session, <code>/model</code> can't switch to Opus/Sonnet either; exit and run <code>claude</code> for that.</div>
      <div class="dt note">Quick manual check from a terminal: <code>claude-qwen-coder -p "say hi"</code></div>
    </div>`)}
    <div class="grid g3">${d.models.map(m => card(m.name, `<div class="stack">
      <div class="dt"><b>Good for:</b> ${esc(m.good_for)}</div>
      <div class="dt"><b>Limits:</b> ${esc(m.limits)}</div>
      <div class="dt note">${m.provider === 'ollama'
        ? `Runs on this machine · ~${m.download_gb} GB download · wants ${m.min_ram_gb} GB RAM (you have ${m.ram_gb} GB)`
        : 'Runs in the cloud via OpenRouter · no download'}</div>
      ${m.installed
        ? `<div class="dt">✅ Added. Run <code>${esc(m.command)}</code> in any project.</div>
           <div><button class="act" data-test="${esc(m.id)}">▶ Test it</button>
             <button class="act ghost" data-rm="${esc(m.id)}">Remove</button></div>
           <div data-testlog="${esc(m.id)}"></div>`
        : `<div><button class="act" data-add="${esc(m.id)}" ${m.fits_ram ? '' : 'title="Less RAM than recommended"'}>＋ Add ${esc(m.command)}</button></div>`}
    </div>`, {badge: m.installed ? '<span class="badge">Added</span>' : ''})).join('')}</div>`;
  page.querySelectorAll('[data-go]').forEach(a => a.onclick = () => go(a.dataset.go));
  page.querySelectorAll('[data-add]').forEach(b => b.onclick = () => addFreeModel(b.dataset.add));
  page.querySelectorAll('[data-test]').forEach(b => b.onclick = () => testFreeModel(b.dataset.test, page.querySelector(`[data-testlog="${b.dataset.test}"]`), b));
  page.querySelectorAll('[data-rm]').forEach(b => b.onclick = async () => {
    if (b.dataset.armed) { await doAction(`free_model/${b.dataset.rm}/remove`); render(); }
    else { b.dataset.armed = 1; b.classList.add('armed'); b.textContent = 'Click again to remove'; }
  });
};

async function testFreeModel(id, host, btn) {
  btn.disabled = true;
  try {
    const {job} = await doAction(`free_model/${id}/test`);
    const j = await followJob(job, host);
    host.insertAdjacentHTML('beforeend', j.state === 'done'
      ? '<div class="item sev-low"><div class="dt">✅ Working end to end.</div></div>'
      : '<div class="item sev-high"><div class="dt">Test failed. See the log above.</div></div>');
  } catch (e) { host.innerHTML = `<div class="item sev-high"><div class="dt">${esc(e.message)}</div></div>`; }
  btn.disabled = false;
}

// Show exactly what will happen, collect anything needed, run only after the user confirms.
async function addFreeModel(id) {
  const d = drawer('Add free model', '<div class="loading">Checking prerequisites…</div>');
  const c = d.querySelector('.content');
  const plan = await fetch(`/api/free_models/${id}/plan`).then(r => r.json());
  if (plan.blocked) { c.innerHTML = `<div class="item sev-high"><div class="dt" style="white-space:pre-wrap">${esc(plan.blocked)}</div></div>`; return; }
  c.innerHTML = `
    <h3>${esc(plan.model.name)}</h3>
    <div class="dt">This will:</div>
    <div class="stack">${plan.steps.map((s, i) => `<div class="item ${s.warn ? 'sev-medium' : ''}"><div class="dt">
      ${s.warn ? '⚠' : `${i + 1}.`} ${esc(s.do)}${s.consent ? ' <span class="pill">needs your OK</span>' : ''}</div></div>`).join('')}</div>
    ${plan.needs.map(n => `${n.steps ? `<div class="item"><div class="hd">How to get your ${esc(n.label)}</div>
      <ol class="dt" style="margin:6px 0 8px 18px;padding:0;line-height:1.7">${n.steps.map(x => `<li>${esc(x)}</li>`).join('')}</ol>
      ${n.url ? `<a class="act" href="${esc(n.url)}" target="_blank" rel="noopener" style="text-decoration:none;display:inline-block">Open ${esc(new URL(n.url).host)} ↗</a>` : ''}</div>` : ''}
      <label class="dt"><b>${esc(n.label)}</b><br>
      <input type="password" data-need="${esc(n.field)}" autocomplete="off" style="width:100%;margin-top:6px;padding:6px 8px;border:1px solid var(--border);border-radius:6px;background:var(--surface-2)">
      <span class="note">${esc(n.help)}</span></label>`).join('')}
    ${plan.consent_required ? `<label class="dt"><input type="checkbox" id="fm-ok"> I'm OK with the steps marked "needs your OK"</label>` : ''}
    <div><button class="act" id="fm-go">Add ${esc(plan.model.command)}</button></div>
    <div id="fm-log"></div>`;
  $('#fm-go', c).onclick = async () => {
    const body = {consent: plan.consent_required ? $('#fm-ok', c).checked : true};
    if (!body.consent) { $('#fm-log', c).innerHTML = '<div class="item sev-medium"><div class="dt">Tick the box to confirm first.</div></div>'; return; }
    for (const n of c.querySelectorAll('[data-need]')) {
      if (!n.value.trim()) { $('#fm-log', c).innerHTML = `<div class="item sev-medium"><div class="dt">Enter the ${esc(n.closest('label').querySelector('b').textContent)}.</div></div>`; return; }
      const need = plan.needs.find(x => x.field === n.dataset.need);
      if (need?.pattern && !new RegExp(need.pattern).test(n.value.trim())) {
        $('#fm-log', c).innerHTML = `<div class="item sev-medium"><div class="dt">That doesn't look like an ${esc(need.label)}. It should start with sk-or-.</div></div>`; return; }
      body[n.dataset.need] = n.value.trim();
    }
    $('#fm-go', c).disabled = true;
    try {
      const {job} = await doAction(`free_model/${id}/install`, body);
      const j = await followJob(job, $('#fm-log', c));
      if (j.state === 'done') { $('#fm-log', c).insertAdjacentHTML('beforeend',
        `<div class="item sev-low"><div class="dt">✅ Added. Open a terminal in any project and run <code>${esc(j.result.command)}</code>. Inside it, <code>/model</code> shows the Qwen model; your normal <code>claude</code> is unchanged.</div>
         <div><button class="act" id="fm-test">▶ Test it now</button></div><div id="fm-testlog"></div></div>`);
        $('#fm-test', c).onclick = e => testFreeModel(id, $('#fm-testlog', c), e.target);
        if (S.view === 'freemodels') render(); }
      else $('#fm-go', c).disabled = false;
    } catch (e) { $('#fm-log', c).innerHTML = `<div class="item sev-high"><div class="dt">${esc(e.message)}</div></div>`; $('#fm-go', c).disabled = false; }
  };
}

/* ---------- compare Claude vs free models ---------- */
VIEWS.compare = async (page) => {
  const d = await fetch('/api/compare?agents=' + encodeURIComponent(S.filter.agents.join(','))).then(r => r.json());
  const hasFree = d.rows.some(r => r.kind === 'free');
  const stars = n => n == null ? '<span class="na">—</span>' : '★'.repeat(Math.floor(n)) + (n % 1 ? '½' : '') +
    `<span style="opacity:.25">${'★'.repeat(5 - Math.ceil(n))}</span>`;
  const price = r => r.kind === 'free' ? '<b>$0</b>' : `${fmtUSD(r.price_in)} / ${fmtUSD(r.price_out)}`;
  const status = r => r.kind !== 'free'
    ? (r.requests ? `${fmtInt(r.requests)} req · ${fmtUSD(r.cost)}` : '<span class="na">not used</span>')
    : !r.fits ? `❌ needs ${r.min_ram_gb} GB RAM` : r.installed ? `✅ <code>${esc(r.command)}</code>` : `<a data-go="freemodels">＋ Add</a>`;
  page.innerHTML = `
    ${card(hasFree ? 'Claude vs free models' : `${esc(agentWord())} models side by side`, `<div id="cmp"></div>`, {flush: 1, badge: BADGE.recommendation,
      hint: hasFree ? `This machine: ${esc(d.os)}, ${d.ram_gb} GB RAM` : 'List prices, your usage in range', footer: esc(d.note)})}
    ${!hasFree ? '' : `<div class="grid g3">
      ${card('Use Claude for', `<div class="dt">Multi-file changes, debugging, anything agentic or long-running.
        Sonnet is the value pick; keep Opus for the hardest problems (see <a data-go="modelswitch">Model switch</a>).</div>`)}
      ${card('Use a free model for', `<div class="dt">Offline or private work, throwaway snippets, explanations,
        single-file edits. Cloud Qwen is the strongest free option; locally, pick the biggest one that fits your RAM.</div>`)}
      ${card('Try it on your own work', `<div class="dt">Give the same small task to a free model (e.g. <code>claude-qwen</code>)
        and to Sonnet (<code>/model sonnet</code>), then compare the diff and how many turns each took.</div>`)}
    </div>`}`;
  $('#cmp', page).innerHTML = table([
    {h: 'Model', f: r => `<b>${esc(r.name)}</b>${r.kind === 'free' ? ' <span class="pill">free</span>' : ''}`},
    {h: 'Runs', f: r => esc(r.where)},
    {h: '$ / 1M in · out', num: 1, f: price},
    {h: 'Tool use', f: r => stars(r.tools)},
    {h: 'Reasoning', f: r => stars(r.reasoning)},
    {h: 'Multi-file', f: r => stars(r.multifile)},
    {h: 'Speed', f: r => esc(r.speed)},
    {h: 'Best for', f: r => esc(r.best_for)},
    {h: 'You', f: status},
  ], d.rows);
  page.querySelectorAll('[data-go]').forEach(a => a.onclick = e => { e.preventDefault(); go(a.dataset.go); });
  addChart(page, 'Output price per 1M tokens', el => C.barsH(el, {
    rows: d.rows, label: r => r.name.replace(/ \((local|free).*\)$/, ''), value: r => r.price_out || 0,
    color: r => r.kind === 'free' ? seriesVar(2) : seriesVar(0)}),
    {hint: hasFree ? 'Free models cost $0; Claude prices are list prices' : 'List prices', after: ':scope > .card'});
};

/* ---------- skills & MCP from recurring work ---------- */
VIEWS.toolkit = async (page) => {
  const d = await fetch('/api/suggestions').then(r => r.json());
  const ev = x => (x || []).map(e => `<code>${esc(e)}</code>`).join(' ');
  page.innerHTML = `
    <div class="note">${esc(d.note)}</div>
    ${card('MCP servers for work you repeat', `<div class="stack">${d.mcp.map(m => `
      <div class="item ${m.installed ? '' : 'sev-medium'}"><div class="hd">${esc(m.name)}
        <span class="spacer"></span><span class="note">${fmtInt(m.sessions)} sessions · ${esc(m.projects.join(', '))}</span></div>
        <div class="dt">${esc(m.what)}</div>
        ${m.evidence.length ? `<div class="dt note">Seen: ${ev(m.evidence)}</div>` : ''}
        ${m.installed ? '<div class="dt">✅ Already connected</div>' : `
        <div class="dt"><code>${esc(m.command)}</code></div>
        <div><button class="act" data-mcp="${esc(m.id)}">＋ Add to Claude</button><span class="mcp-out"></span></div>`}
      </div>`).join('') || '<div class="empty">No recurring pattern points to an MCP server</div>'}</div>`,
      {badge: BADGE.recommendation})}
    ${card('Skills from what you repeat', `<div class="stack">${d.skills.map((s, i) => `
      <div class="item ${s.installed ? '' : 'sev-medium'}"><div class="hd">/${esc(s.name)}
        <span class="spacer"></span><span class="note">${fmtInt(s.sessions)} sessions · ${fmtInt(s.runs)} runs · ${esc(s.projects.join(', '))}</span></div>
        <div class="dt">${esc(s.what)}</div>
        <div class="dt note">Examples: ${ev(s.examples.slice(0, 2))}</div>
        ${s.installed ? '<div class="dt">✅ Skill exists</div>' : `<div><button class="act" data-skill="${i}">＋ Create skill</button><span class="sk-out"></span></div>`}
      </div>`).join('') || '<div class="empty">Nothing repeats often enough yet</div>'}</div>`,
      {badge: BADGE.recommendation, hint: `Created in ${d.skills_dir}; edit the SKILL.md afterwards`})}`;
  page.querySelectorAll('[data-skill]').forEach(b => b.onclick = async () => {
    const out = b.nextElementSibling; b.disabled = true;
    try { const r = await doAction('skill', d.skills[+b.dataset.skill]);
      out.innerHTML = ` ✅ Created <code>${esc(r.path)}</code>. Use it with <code>${esc(r.use)}</code>.`; }
    catch (e) { out.textContent = ' ' + e.message; b.disabled = false; }
  });
  page.querySelectorAll('[data-mcp]').forEach(b => b.onclick = async () => {
    const m = d.mcp.find(x => x.id === b.dataset.mcp); const out = b.nextElementSibling;
    const body = {};
    for (const n of m.needs) {
      if (!b.dataset.asked) {
        out.innerHTML = ` <input data-need="${esc(n.field)}" placeholder="${esc(n.label)}" style="width:320px;padding:4px 8px;border:1px solid var(--border);border-radius:6px;background:var(--surface-2)"> <span class="note">${esc(n.help)}</span>`;
        b.dataset.asked = 1; b.textContent = 'Confirm add'; return;
      }
      body[n.field] = out.querySelector(`[data-need="${n.field}"]`).value;
    }
    b.disabled = true;
    try { const r = await doAction(`mcp/${m.id}`, body); out.innerHTML = ` ✅ Added. ${esc(r.next)}`; }
    catch (e) { out.textContent = ' ' + e.message; b.disabled = false; }
  });
};

/* ---------- billed vs local (vendor APIs) ---------- */
VIEWS.cloud = async (page) => {
  const d = await fetch('/api/cloud?days=30').then(r => r.json());
  const t = d.totals, cfg = d.configured, pv = d.providers;
  const none = !Object.values(cfg).some(Boolean);
  const gap = (t.billed_claude_cost || 0) - (t.local_claude_cost || 0);
  const setup = Object.entries(pv).map(([k, p]) => `
    <div class="item sev-${cfg[k] ? 'low' : 'medium'}">
      <div class="hd">${cfg[k] ? '✓' : '○'} ${esc(p.name)}<span class="spacer"></span>
        <span class="note">${cfg[k] ? 'key found' : 'no key'}</span></div>
      <div class="dt"><b>Gives you:</b> ${esc(p.covers)}</div>
      <div class="dt"><b>Get a key:</b> ${esc(p.how)}</div>
      <div class="dt">Then set <code>${esc(p.env)}</code> in your environment, or add
        <code>"${esc(p.field)}"</code> to <code>~/.claude-finops/secrets.local.json</code>
        (gitignored, never packaged). Restart the dashboard afterwards.</div>
    </div>`).join('');
  page.innerHTML = `
    <div class="note"><b>The only page that talks to the internet.</b> ${esc(d.note)}
      Nothing is fetched until you click Refresh.
      <span class="spacer"></span>
      <span class="note">Last fetched: ${esc(d.fetched_at || 'never')}</span>
      <button class="act" id="cl-sync" ${none ? 'disabled title="Add a key first"' : ''}>↯ Refresh from APIs</button></div>
    <div id="cl-log"></div>
    ${Object.keys(d.errors).length ? `<div class="note sev-high">${Object.entries(d.errors)
      .map(([k, v]) => `<div><b>${esc(k)}</b>: ${esc(v)}</div>`).join('')}</div>` : ''}
    <div class="grid g4">
      ${kpi('Billed (Claude Code, org)', t.billed_claude_cost ? fmtUSD(t.billed_claude_cost) : null,
        `${fmtInt(t.org_users)} users · ${fmtNum(t.billed_claude_tokens)} tokens`, {badge: BADGE.actual})}
      ${kpi('This machine (estimated)', fmtUSD(t.local_claude_cost),
        `${fmtNum(t.local_claude_tokens)} tokens from local transcripts`, {badge: BADGE.estimated})}
      ${kpi('Gap', t.billed_claude_cost ? fmtUSD(gap) : null,
        'Other machines, other members, or work off this machine')}
      ${kpi('Cursor team spend', t.cursor_spend == null ? null : fmtUSD(t.cursor_spend),
        `${fmtInt(t.cursor_members)} members`, {badge: BADGE.actual})}
    </div>
    ${card('Set up the APIs', `<div class="stack">${setup}</div>`,
      {hint: 'Keys are read from your environment or a local file; the dashboard never stores them'})}
    ${card('Billed vs local, per day', `<div id="cl-day"></div>`, {flush: 1, badge: BADGE.actual,
      hint: 'Claude Code: what the vendor billed against what this machine recorded'})}
    ${card('Claude Code users (org-wide)', `<div id="cl-users"></div>`, {flush: 1, badge: BADGE.actual,
      hint: 'Per user per day, aggregated over the range — includes Pro/Max subscription users'})}
    ${card('Cursor members', `<div id="cl-cur"></div>`, {flush: 1, badge: BADGE.actual})}
    ${card('Billed API cost by line item', `<div id="cl-api"></div>`, {flush: 1, badge: BADGE.actual,
      hint: 'From the Anthropic cost report: API spend only, not subscription plans'})}`;
  $('#cl-day', page).innerHTML = table([
    {h: 'Day', f: r => esc(r.day)},
    {h: 'Billed $', num: 1, f: r => fmtUSD(r.billed_cost)},
    {h: 'Local $ (est.)', num: 1, f: r => r.local_cost == null ? '—' : fmtUSD(r.local_cost)},
    {h: 'Billed tokens', num: 1, f: r => fmtNum(r.billed_tokens)},
    {h: 'Local tokens', num: 1, f: r => r.local_tokens == null ? '—' : fmtNum(r.local_tokens)},
    {h: 'Sessions billed / local', num: 1, f: r => `${fmtInt(r.billed_sessions)} / ${r.local_sessions == null ? '—' : fmtInt(r.local_sessions)}`},
  ], d.by_day);
  $('#cl-users', page).innerHTML = table([
    {h: 'User', f: r => esc(r.actor)},
    {h: 'Plan', f: r => esc(r.customer_type || '—')},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
    {h: 'Lines +/-', num: 1, f: r => `${fmtInt(r.lines_added)} / ${fmtInt(r.lines_removed)}`},
    {h: 'Commits', num: 1, f: r => fmtInt(r.commits)},
    {h: 'PRs', num: 1, f: r => fmtInt(r.prs)},
    {h: 'Where', trunc: 1, f: r => esc(r.terminals.join(', ') || '—')},
  ], d.users);
  $('#cl-cur', page).innerHTML = table([
    {h: 'Member', f: r => esc(r.member)},
    {h: 'Spend', num: 1, f: r => r.spend_usd == null ? '—' : fmtUSD(r.spend_usd)},
    {h: 'Active days', num: 1, f: r => fmtInt(r.days)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Lines +/-', num: 1, f: r => `${fmtInt(r.lines_added)} / ${fmtInt(r.lines_removed)}`},
  ], d.cursor_members);
  $('#cl-api', page).innerHTML = table([
    {h: 'Line item', f: r => esc(r.description)},
    {h: 'Billed $', num: 1, f: r => fmtUSD(r.cost_usd)},
  ], d.api_cost_by_description);
  const btn = $('#cl-sync', page);
  if (btn) btn.onclick = async () => {
    btn.disabled = true; btn.textContent = 'Fetching…';
    try {
      const {job} = await doAction('cloud_sync', {days: 30});
      const j = await followJob(job, $('#cl-log', page));
      if (j.state === 'error') throw new Error(j.error || 'failed');
      render();
    } catch (e) {
      $('#cl-log', page).innerHTML = `<div class="note sev-high">Failed: ${esc(e.message)}</div>`;
      btn.disabled = false; btn.textContent = '↯ Refresh from APIs';
    }
  };
};

/* ============================ walkthrough ============================ */
// A guided tour: a welcome tour on first visit, and a per-dashboard tour any time via
// the "? Tour" button. Each step says what you see, what you get, and one thing to try.
// Targets: a CSS selector, 'kpis' (the first KPI row), or 'cardN' (Nth card on the page).
const TOUR_DONE = 'finops-tour-done';
const TOUR_WELCOME = [
  {el: '.brand', t: 'Welcome to FinOps', see: 'One dashboard for what your coding agents (Claude Code, Codex, Gemini CLI, Cursor) spend in tokens and money.',
    get: 'Where the tokens go, why, and what to change to spend less. Everything is read from files on this machine.',
    act: 'Use Next and Previous to move through the tour, or Skip to close it.'},
  {el: '.nav', t: 'Dashboards', see: 'Every dashboard, grouped: Command center, Usage, Drill-down, Optimize, Plan.',
    get: 'Start with <b>Why so many tokens?</b> or <b>What should I do?</b> for a ranked to-do list.',
    act: 'Click any item to open that dashboard. A number next to an item means something needs attention.'},
  {el: '[data-agent]', t: 'Pick the agent', see: 'Agent chips: one agent, several, or All.',
    get: 'Every dashboard is filtered to the agents you pick. Dashboards that need data an agent doesn\'t record are hidden.',
    act: 'Click <b>Codex</b> or <b>All</b> to switch.'},
  {el: '[data-range]', t: 'Date range & filters', see: 'Range chips, plus Model, Project, Category and Threshold filters.',
    get: 'Narrow any dashboard to a period, a model or a project.',
    act: 'Try <b>7 days</b>, then open <b>Project ▾</b> to focus on one repo.'},
  {el: '#gsearch', t: 'Search', see: 'Search across prompts, sessions, models and dates.',
    get: 'Jump straight to the prompt or session you remember.', act: 'Type a word from a recent prompt and press Enter.'},
  {el: '#sync', t: 'Keep it current', see: 'Sync re-reads every agent\'s local data.',
    get: 'Fresh numbers after you\'ve been working.', act: 'Click <b>⟳ Sync</b> whenever the numbers look stale.'},
  {el: '#tour-btn', t: 'Tour any dashboard', see: 'This button starts a tour of the dashboard you\'re on.',
    get: 'A short explanation of each section: what it shows, what you get, what you can do.',
    act: 'Open any dashboard and click <b>? Tour</b> to replay it. Next: this dashboard.'},
];
// Per-dashboard tours. One step per section the dashboard renders, in the order you
// scroll past them; steps whose section is absent for the current filters are dropped
// at start (see startTour), so a short dashboard simply gets a short tour.
const TOURS = {
  overview: [
    {el: 'kpis', t: 'Headline numbers', see: 'Estimated spend, billable tokens, plan usage, remaining allowance and the period forecast.', get: 'A quick read on how much you use and what it costs, for the filters above.', act: 'Change the date range and watch every number follow it.'},
    {el: 'card:Usage burn rate', t: 'Burn rate', see: 'Period to date, daily average, 7-day rate and the projected period total, with a gauge against your allowance.', get: 'Whether today\'s pace lands you inside or outside your plan.', act: 'Set monthly_cost_allowance_usd in config/settings.json to make the gauge exact.'},
    {el: 'card:Cost trend', t: 'Cost trend', see: 'Spend per day across the range.', get: 'The days that drove the bill.', act: 'Hover a point for the exact day, then narrow the range to that week.'},
    {el: 'card:Model cost', t: 'Model cost', see: 'How spend splits across the models you used.', get: 'Whether an expensive model is doing routine work.', act: 'A big share on a top-tier model? Check <b>Model switch</b>.'},
    {el: 'card:Project cost', t: 'Project cost', see: 'Spend per repository.', get: 'Which codebase costs the most to work in.', act: 'Click a project to filter every dashboard to it.'},
    {el: 'card:Top 10 most expensive prompts', t: 'Most expensive prompts', see: 'The ten single prompts that cost the most.', get: 'The requests worth rewriting or splitting.', act: 'Click a prompt to see every step it triggered.'},
    {el: 'card:Most expensive sessions', t: 'Most expensive sessions', see: 'The longest-running, highest-cost sessions.', get: 'The marathon sessions where context kept being re-read.', act: 'Click one to see where it grew.'},
    {el: 'card:Waste detection', t: 'Waste at a glance', see: 'A summary of the patterns that burn tokens for nothing.', get: 'An estimate of what you could avoid.', act: 'Open <b>Waste detection</b> for the full findings and evidence.'},
    {el: 'card:Optimization opportunities', t: 'Opportunities', see: 'The top ranked changes with estimated savings.', get: 'What to fix first.', act: 'Open <b>What should I do?</b> for the full list with ready-made prompts.'},
    {el: 'card:Forecast', t: 'Forecast', see: 'Projected spend to the end of the billing period.', get: 'An early read on whether you\'ll go over.', act: 'Open <b>Forecast</b> for the optimistic and pessimistic scenarios.'},
    {el: 'card:FinOps score', t: 'FinOps score', see: 'Your overall score for cache use, model mix, waste and budget.', get: 'One number for how efficiently you work.', act: 'Open <b>FinOps scorecard</b> to see what each component grades.'}],
  advisor: [
    {el: 'card:Biggest optimization opportunity', t: 'Start here', see: 'The single change with the largest estimated saving, with the evidence behind it.', get: 'The best use of your next ten minutes.', act: 'Read the estimate, then apply the change.'},
    {el: 'card:What is going well', t: 'Strengths & gaps', see: 'What your habits already do well, and the items that need attention.', get: 'Confirmation of what to keep doing, and where you lose money.', act: 'Work down the <b>Needs attention</b> list.'},
    {el: 'card:All recommendations', t: 'All recommendations', see: 'Every recommendation, ranked by estimated saving, each with its reasoning.', get: 'A prioritised to-do list built from your own usage.', act: 'Copy a recommendation\'s prompt straight into your agent.'},
    {el: 'card:Anomalies to inspect', t: 'Anomalies', see: 'Days and sessions that spent far more than your normal.', get: 'Surprises caught before they repeat.', act: 'Click one to see what happened that day.'}],
  agents: [
    {el: 'kpis', t: 'Agent totals', see: 'One tile per agent with its usage for the current filters.', get: 'How spend splits across Claude Code, Codex, Gemini CLI and Cursor.', act: 'Use the agent chips in the header to focus on one.'},
    {el: 'card:Side by side', t: 'Side by side', see: 'Every agent in one table: cost, tokens, requests, sessions.', get: 'A like-for-like comparison of what each agent costs you.', act: 'Compare tokens per request: a high number means large contexts.'},
    {el: 'card:Daily', t: 'Daily by agent', see: 'Cost or tokens per day, split by agent.', get: 'When you switched agents and what that did to spend.', act: 'Use the metric toggle to swap cost for tokens.'},
    {el: 'card:What each agent records', t: 'What each agent records', see: 'Which fields each agent writes to disk on this machine.', get: 'Why some dashboards are hidden for some agents: the data simply isn\'t recorded.', act: 'Check here first when a number reads "not recorded".'}],
  live: [
    {el: 'kpis', t: 'Running now', see: 'Sessions running right now, which are working (spending) at this moment, the largest live context and what live sessions have spent.', get: 'What is costing you money this second.', act: 'Only <b>working</b> sessions consume tokens; idle ones cost again on your next message.'},
    {el: '#live-refresh', t: 'Refresh & the rules', see: 'The note explains what Interrupt, Close and Force kill do, and how non-Claude agents are detected.', get: 'Confidence before you stop something.', act: 'Click <b>↻ Refresh</b> for the latest state.'},
    {el: '.item[data-i]', t: 'A session', see: 'Agent, project, context size, steps, cost and last activity, plus advice when a session gets heavy.', get: 'Control over sessions that are quietly growing expensive.', act: 'Interrupt, Close or Force kill it (each needs two clicks), copy its resume command, or <b>Hand over</b> a Claude session to a fresh one.'}],
  scorecard: [
    {el: 'card:AI FinOps Score', t: 'Your score', see: 'The overall score with a component breakdown: cache use, model mix, waste and budget.', get: 'Where your habits are strong and where they cost money.', act: 'Hover a component to see how it is calculated.'},
    {el: 'card:✅ What is good', t: 'What is good', see: 'The components you already score well on.', get: 'The habits worth keeping.', act: 'Keep these when you change your setup.'},
    {el: 'card:⚠️ Needs attention', t: 'Needs attention', see: 'The components dragging the score down.', get: 'A short list of what to fix.', act: 'Start at the top: it carries the most weight.'},
    {el: 'card:🎯 Biggest opportunity', t: 'Biggest opportunity', see: 'The single change that would move the score most.', get: 'One concrete action with an estimated saving.', act: 'Apply it, then Sync and re-check the score.'}],
  usage: [
    {el: 'kpis', t: 'Token breakdown', see: 'Total, input, output, thinking, cache read and cache write tokens, plus requests, sessions, prompts and active days.', get: 'Exactly which kind of token you spend on. Cache read is cheap; uncached input is not.', act: 'Compare cache read against input: a low ratio means context is being re-sent, not reused.'},
    {el: 'card0', t: 'Usage over time', see: 'Tokens or cost per day (or per hour on short ranges).', get: 'Spikes and quiet periods at a glance.', act: 'Hover a bar for the day\'s numbers; narrow the range to zoom in.'},
    {el: 'card:Model mix over time', t: 'Model mix over time', see: 'Which models carried the work on each day.', get: 'Whether you drifted to an expensive model over time.', act: 'Click a legend entry to isolate one model.'},
    {el: 'card:Daily detail', t: 'Daily detail', see: 'A row per day with tokens, requests and estimated cost.', get: 'The exact numbers behind the charts.', act: 'Sort by cost to find the outlier days.'}],
  burn: [
    {el: 'kpis', t: 'Burn rate & limits', see: 'Billing period, days remaining, used to date, projected total, daily and 7-day averages, and remaining credits.', get: 'Early warning before you hit a cap.', act: 'Compare the projected period total with your allowance.'},
    {el: 'card0', t: 'Allowance gauges', see: 'One gauge per allowance you configured: cost, tokens or requests.', get: 'How much of each limit is gone, and how many days it lasts at this rate.', act: 'Set your limits in config/settings.json to make these exact.'},
    {el: 'card:Daily consumption within the billing period', t: 'Daily consumption', see: 'Each day of the billing period against the pace you\'d need to stay inside the limit.', get: 'Which days pushed you off pace.', act: 'Hover a bar to see that day against the target line.'}],
  models: [
    {el: 'kpis', t: 'Model headlines', see: 'How many models you used and what the mix costs.', get: 'A first read on whether the mix is right.', act: 'Filter to one model with <b>Model ▾</b> above.'},
    {el: 'card:Cost share', t: 'Cost share', see: 'Spend split across models.', get: 'The model that owns your bill.', act: 'Check whether that model is doing work a cheaper one could.'},
    {el: 'card:Token share', t: 'Token share', see: 'The same split by tokens instead of money.', get: 'The gap between the two charts is the price difference at work.', act: 'A model with a small token share but a big cost share is your expensive one.'},
    {el: 'card:Model FinOps table', t: 'Model FinOps table', see: 'Per model: cost, tokens, context, output and cost per 1K output.', get: 'What each model really costs for the work you give it.', act: 'Sort by cost per 1K output, then open <b>Model switch</b>.'},
    {el: 'card:Price table in effect', t: 'Prices in effect', see: 'The per-model prices used for every estimate in this dashboard.', get: 'Transparency: you can check the maths.', act: 'Edit the price file if your rates differ.'}],
  context: [
    {el: 'kpis', t: 'Context & cache', see: 'Average and peak context per request, and how much is served from cache.', get: 'How much re-reading history costs you, and what caching saves.', act: 'Large average context? Clear or compact sessions more often.'},
    {el: 'card:Cost by context size', t: 'Cost by context size', see: 'Spend grouped by how large the context was.', get: 'Proof of how quickly cost climbs with context.', act: 'See how much sits in the largest buckets.'},
    {el: 'card:Caching: with vs without', t: 'What caching saves', see: 'What you paid against what the same work would cost with no cache.', get: 'The value of cache hits in money.', act: 'A small gap means sessions are restarted too often to build a cache.'},
    {el: 'card:Sessions with the largest context', t: 'Largest contexts', see: 'The sessions that carried the most history.', get: 'The sessions to split or hand over next time.', act: 'Click one to see where it grew.'},
    {el: 'card:Context distribution', t: 'Context distribution', see: 'How your requests spread across context sizes.', get: 'Whether large contexts are the exception or the norm.', act: 'A long right tail means /compact earlier.'}],
  projects: [
    {el: 'card:Project cost ranking', t: 'Cost ranking', see: 'Projects ordered by spend.', get: 'Which repos cost the most.', act: 'Click a bar to filter every dashboard to that project.'},
    {el: 'card:Project FinOps table', t: 'Project FinOps table', see: 'Per project: cost, tokens, sessions, prompts and efficiency.', get: 'Whether one repo needs better memory or a harness.', act: 'High tokens per prompt? Check that repo in <b>Why so many tokens?</b>.'}],
  sessions: [
    {el: 'kpis', t: 'Session headlines', see: 'Sessions in range, average cost per session, and average tokens per session and per prompt.', get: 'Whether your sessions run long and heavy or short and cheap.', act: 'Compare tokens per prompt against other ranges.'},
    {el: 'card:Session explorer', t: 'Session explorer', see: 'Every session with cost, steps, peak context and project, with its own filters.', get: 'The marathon sessions that cost the most.', act: 'Sort by cost, then click a session to see its prompts.'},
    {el: 'card:Lowest output yield', t: 'Lowest output yield', see: 'Sessions that consumed a lot of context for very little output.', get: 'Where tokens went in and almost nothing came out.', act: 'These are the best candidates for a fresh session.'},
    {el: 'card:Highest output yield', t: 'Highest output yield', see: 'Sessions that produced the most output per token consumed.', get: 'What a well-scoped session looks like for your work.', act: 'Compare their size and prompts with the low-yield ones.'}],
  prompts: [
    {el: 'card:Prompt explorer', t: 'Prompt explorer', see: 'Each prompt you typed and what it cost end to end, including every step it triggered.', get: 'Which requests are expensive, and why.', act: 'Sort by cost and click a prompt to see its requests, tool calls and files touched.'},
    {el: '#pq', t: 'Search your prompts', see: 'A search box over the prompt text itself, on top of the global filters above.', get: 'A way to find the prompts about one feature, file or bug.', act: 'Type a word you use often and see what that kind of request costs you.'},
    {el: '.chip[data-po]', t: 'Sort the list', see: 'Sort by most expensive, most tokens, longest, most efficient, cheapest or most recent.', get: 'The same prompts read as a cost list or as an efficiency list.', act: 'Compare <b>Most expensive</b> with <b>Most efficient</b>: the difference is how you phrased them.'},
    {el: '#pt', t: 'A prompt row', see: 'One row per prompt with its tokens, requests and estimated cost.', get: 'The full chain of work a single sentence set off.', act: 'Click a row for the whole prompt, its requests, tool calls and the files it touched.'}],
  rankings: [
    {el: 'card:Cost & efficiency leaderboards', t: 'Leaderboards', see: 'Top prompts, sessions, days, models and projects by cost, plus the efficiency boards.', get: 'Your most expensive work in one place.', act: 'Switch board with the tabs, then click any row to drill in.'},
    {el: '.tabs', t: 'Pick a board', see: 'One tab per leaderboard: most expensive, longest sessions, and the efficiency boards.', get: 'The same range read from several angles without changing any filter.', act: 'Open <b>Longest sessions</b>: duration and cost usually rise together.'},
    {el: '#rt', t: 'The rows', see: 'The top 20 rows of the board you picked, ranked with their tokens and estimated cost.', get: 'The specific work behind the headline numbers elsewhere.', act: 'Click any row to open the prompt or session behind it.'}],
  categories: [
    {el: 'card:Cost by activity', t: 'Cost by activity', see: 'Spend per kind of work: coding, debugging, docs, review and so on.', get: 'Which kinds of work cost most.', act: 'Cheap, repetitive categories are the ones to move to a smaller model.'},
    {el: 'card:Prompts by activity', t: 'Prompts by activity', see: 'How many prompts fall into each category.', get: 'Volume next to cost: a category can be frequent but cheap.', act: 'Compare this with the cost chart to find the expensive outliers.'},
    {el: 'card:Activity breakdown', t: 'Activity breakdown', see: 'Per category: prompts, tokens, cost and the model used.', get: 'Concrete evidence for routing work to a cheaper model.', act: 'Then open <b>Model switch</b> to see the saving.'}],
  developer: [
    {el: 'card:Cost per repository', t: 'Cost per repository', see: 'Spend per repo checked out on this machine.', get: 'Which codebase is expensive to work in.', act: 'Click a repo to filter the dashboards.'},
    {el: 'card:Tool usage', t: 'Tool usage', see: 'Each tool the agent called and how often.', get: 'Tools with huge call counts: they fill context and cost money.', act: 'Big Read or Grep counts? Add project memory so the agent re-reads less.'},
    {el: 'card:Repository FinOps', t: 'Repository FinOps', see: 'Per repo: cost, tokens, sessions and prompts.', get: 'A per-repo efficiency comparison.', act: 'Fix the worst repo\'s config in <b>Why so many tokens?</b>.'},
    {el: 'card:Cost by git branch', t: 'Cost by branch', see: 'Spend attributed to the branch that was checked out.', get: 'What a feature branch actually cost.', act: 'Use it for per-feature cost reporting.'},
    {el: 'card:Most-touched files', t: 'Most-touched files', see: 'The files the agent read or edited most.', get: 'Files re-read over and over are pure context cost.', act: 'Summarise a hot file in your memory file so it isn\'t re-read every session.'}],
  diagnose: [
    {el: '.focus', t: 'Focus on these first', see: 'The few fixes with the biggest effect, pulled out of everything below.', get: 'A short path through a long dashboard.', act: 'Click a focus item to jump to its section.'},
    {el: 'card:Running now', t: 'Running now', see: 'Live sessions and how much context each is carrying.', get: 'A heavy session you can fix right now.', act: 'Hand over or compact anything flagged.'},
    {el: 'card:Past sessions that carried too much context', t: 'Past heavy sessions', see: 'Finished sessions that dragged an oversized context along.', get: 'The pattern to avoid repeating.', act: 'Look at how long they ran before you started a fresh one.'},
    {el: 'card:Add to', t: 'Memory suggestions', see: 'Facts your prompts repeat, ready to move into your memory file.', get: 'Shorter prompts and less re-explaining.', act: 'Copy a suggestion into the project memory file.'},
    {el: 'card:Why consumption is high', t: 'The drivers', see: 'The main drivers of your token use, each with its share.', get: 'A plain explanation of where tokens go.', act: 'Read the top driver first.'},
    {el: 'card:What to change', t: 'What to change', see: 'Each finding with a ready-made prompt that fixes it.', get: 'A fix your agent can apply for you.', act: 'Click <b>Copy</b> and paste it into your agent.'},
    {el: 'card:Projects:', t: 'Project config health', see: 'Per project: whether it has memory, an agents file, hooks and the rest.', get: 'The config gaps that make every session start from zero.', act: 'Fix the projects flagged red.'},
    {el: '#dx-harness', t: 'Does it need a harness?', see: 'Per project, whether the work you do there would be better served by a harness: hooks, subagents or a scripted workflow.', get: 'A read on when a repo has outgrown ad-hoc prompting.', act: 'Start with the project that repeats the same multi-step task most often.'},
    {el: 'card:Global config', t: 'Global config', see: 'Your machine-wide agent configuration and what\'s missing from it.', get: 'Settings that pay off across every project at once.', act: 'Apply the global fixes before the per-project ones.'},
    {el: '#dx-issues', t: 'Per-project fixes', see: 'One card per project that has open issues, each with the exact fix for that repo.', get: 'The work split by repo, so you can fix one and stop.', act: 'Fix your most expensive repo first; <b>Project cost</b> on the overview says which that is.'}],
  attribution: [
    {el: 'kpis', t: 'Who used the tokens', see: 'Tokens split by session, subagent, skill, MCP server and connector.', get: 'Which add-ons quietly cost the most context.', act: 'Compare main-agent tokens with subagent tokens.'},
    {el: 'card:Sessions: main agent vs subagents', t: 'Main agent vs subagents', see: 'How much work the main agent did versus the subagents it spawned.', get: 'Whether fan-out is paying for itself.', act: 'Heavy subagent spend on simple tasks? Spawn fewer.'},
    {el: 'card:Subagents by type', t: 'Subagents by type', see: 'Each subagent type and what it consumed.', get: 'The agent type worth narrowing or dropping.', act: 'Tighten the prompt of the most expensive type.'},
    {el: 'card:Most expensive subagent runs', t: 'Most expensive runs', see: 'Individual subagent runs ordered by cost.', get: 'The single runs that got out of hand.', act: 'Click one to see what it was asked to do.'},
    {el: 'card:Skills', t: 'Skills', see: 'Which skills loaded and what each added to context.', get: 'Skills that load on every turn but rarely help.', act: 'Trim the description of anything that loads too eagerly.'},
    {el: 'card:Slash commands', t: 'Slash commands', see: 'The commands and user-invoked skills you actually use.', get: 'Which shortcuts earn their keep.', act: 'Delete the ones you never call.'},
    {el: 'card:MCP servers', t: 'MCP servers', see: 'Each configured server, whether it was ever called, and its context cost.', get: 'Tool definitions loaded on every request for nothing.', act: 'Remove servers that are configured but never called.'},
    {el: 'card:Connectors', t: 'Connectors', see: 'claude.ai connectors and their share of context.', get: 'The same check for connectors as for MCP servers.', act: 'Disconnect what you don\'t use from this machine.'}],
  waste: [
    {el: 'kpis', t: 'Waste headlines', see: 'Estimated excess, exposed spend, and how many prompts, sessions and rules are involved.', get: 'An honest estimate of avoidable spend.', act: '<b>Exposed</b> is what flagged work cost in total; <b>excess</b> is how much more than a fair baseline.'},
    {el: 'card:🔴 High waste', t: 'High waste', see: 'The rules that fired hardest: repeated reads, retries, stale sessions.', get: 'The costly patterns, each with the baseline it is measured against.', act: 'Open <b>Show flagged items</b> to see the evidence, then fix these first.'},
    {el: 'card:🟡 Optimization opportunities', t: 'Medium findings', see: 'Patterns worth changing but not urgent.', get: 'The next tier of savings.', act: 'Batch these into one config change.'},
    {el: 'card:⚪ Low-priority observations', t: 'Low-priority observations', see: 'Small findings, kept for completeness.', get: 'Context for the numbers above.', act: 'Skim them; act only if one matches a habit you want to change.'}],
  modelswitch: [
    {el: 'kpis', t: 'Model switch', see: 'What you\'d save running routine work on a cheaper model from the same vendor, split by confidence.', get: 'Savings you can trust, separated from savings that need a judgement call.', act: 'Start with the high-confidence figure.'},
    {el: 'card:Switch these', t: 'Switch these', see: 'The specific prompts or categories that a smaller model could have handled.', get: 'Evidence per item, not a blanket recommendation.', act: 'Check a few prompts yourself before you trust the pattern.'},
    {el: 'card:Default model per project', t: 'Default per project', see: 'A suggested default model for each project, from the work you do there.', get: 'A setting you change once instead of choosing per prompt.', act: 'Apply it to your cheapest, most repetitive repo first.'},
    {el: 'card:How to switch', t: 'How to switch', see: 'The exact command or setting for each agent.', get: 'No guessing at flag names.', act: 'Copy the command for your agent and try it on a small task.'}],
  freemodels: [
    {el: 'card:How to use and test a free model', t: 'How it works', see: 'How to plug a local or free cloud model into Claude Code, and how to test it.', get: 'Zero-cost options for simple or private work.', act: 'Read the RAM guidance before you pick a model.'},
    {el: 'card1', t: 'A model', see: 'Each model with its size, RAM needs, strengths and limits.', get: 'A realistic idea of what runs on your machine.', act: 'Click <b>＋ Add</b> on a model that fits your RAM.'}],
  compare: [
    {el: 'card0', t: 'Models side by side', see: 'Price, context window, ratings and your own usage per model.', get: 'A clear pick for each kind of work.', act: 'Sort by output price, the one that usually dominates the bill.'},
    {el: 'card:Use Claude for', t: 'Use the big model for', see: 'The work that genuinely needs a top model.', get: 'Where paying more actually pays off.', act: 'Keep multi-file and agentic work here.'},
    {el: 'card:Use a free model for', t: 'Use a free model for', see: 'The work a free or local model handles fine.', get: 'The safe places to spend nothing.', act: 'Move throwaway snippets and explanations here.'},
    {el: 'card:Try it on your own work', t: 'Try it yourself', see: 'A short recipe for comparing models on a task of your own.', get: 'Your own evidence instead of someone\'s benchmark.', act: 'Run the same small task on both and compare the result.'}],
  toolkit: [
    {el: 'card:MCP servers for work you repeat', t: 'MCP suggestions', see: 'Servers suggested from the work your prompts repeat.', get: 'Fewer manual steps and smaller prompts.', act: 'Add one, then check its context cost in <b>Who used the tokens</b>.'},
    {el: 'card:Skills from what you repeat', t: 'Skill suggestions', see: 'Skills drafted from the instructions you keep retyping.', get: 'Repetition moved out of your prompts.', act: 'Create a suggested skill in one click.'}],
  recommendations: [
    {el: 'card:Biggest optimization opportunity', t: 'Start here', see: 'The change with the largest estimated saving.', get: 'The best single thing to do next.', act: 'Apply it, then Sync and re-check.'},
    {el: 'card:What is going well', t: 'Strengths & gaps', see: 'What already works, and what needs attention.', get: 'What to keep and what to change.', act: 'Work down the <b>Needs attention</b> list.'},
    {el: 'card:All recommendations', t: 'All recommendations', see: 'Every recommendation with its estimated saving and reasoning.', get: 'A prioritised list of what to change.', act: 'Start with the highest saving.'},
    {el: 'card:Anomalies to inspect', t: 'Anomalies', see: 'Unusual days and sessions worth a look.', get: 'Problems caught before they become habits.', act: 'Click one to see what happened.'}],
  anomalies: [
    {el: 'card:Daily spend with anomalies highlighted', t: 'Spend with anomalies', see: 'Daily spend with the outlier days marked against your normal band.', get: 'How far outside normal each day was.', act: 'Hover a highlighted day for its numbers.'},
    {el: 'card:Detected anomalies', t: 'Detected anomalies', see: 'Each anomaly with what triggered it.', get: 'The explanation, not just the spike.', act: 'Click one to see the sessions behind it.'}],
  forecast: [
    {el: 'kpis', t: 'Forecast', see: 'Projected spend for the rest of the billing period.', get: 'Whether you\'ll land over or under budget.', act: 'Set a budget under <b>Budgets</b> to compare against.'},
    {el: 'card:Cumulative spend and forecast fan', t: 'The forecast fan', see: 'Spend to date, then a fan of where the period could end.', get: 'A range, not a false-precision single number.', act: 'Read the width of the fan as the uncertainty.'},
    {el: 'card:Scenarios', t: 'Scenarios', see: 'Optimistic, expected and pessimistic end-of-period totals.', get: 'A best and worst case to plan against.', act: 'Budget against the pessimistic number.'}],
  budgets: [
    {el: 'card:Budget vs actual vs forecast', t: 'Budget vs actual', see: 'Each budget line with its budget, actual, forecast and variance.', get: 'A warning before you overspend, not after.', act: 'Watch the variance column: a positive forecast variance means trouble.'},
    {el: 'card:Configure budgets', t: 'Configure budgets', see: 'Your budget lines, limits and alert thresholds.', get: 'Numbers that make the forecast and burn dashboards meaningful.', act: 'Edit a budget and save; every dashboard picks it up.'}],
  cloud: [
    {el: 'kpis', t: 'Billed vs local', see: 'What the vendor billed the whole organisation next to what this machine recorded.', get: 'The gap: usage from other machines, other members, or work off this machine.', act: 'Click <b>↯ Refresh from APIs</b> to fetch — this is the only page that goes online.'},
    {el: 'card:Set up the APIs', t: 'Set up the APIs', see: 'Which provider keys were found, and how to get each one.', get: 'Org-wide Claude Code usage per user, and Cursor team spend.', act: 'Run claude-finops --set-key, or set the environment variable, then restart.'},
    {el: 'card:Billed vs local', t: 'The comparison', see: 'Billed totals against local totals for the same period.', get: 'Proof of how much of the bill this machine explains.', act: 'A large gap means most spend happens elsewhere: check the user tables.'},
    {el: 'card:Claude Code users', t: 'Users org-wide', see: 'Each Claude Code user in the organisation and their usage.', get: 'Who drives the bill across the team.', act: 'Compare your own row with the team average.'},
    {el: 'card:Cursor members', t: 'Cursor members', see: 'Cursor team members and their spend.', get: 'The same picture for Cursor seats.', act: 'Look for seats with no usage at all.'},
    {el: 'card:Billed API cost by line item', t: 'Billed line items', see: 'The vendor\'s own line items for the period.', get: 'The invoice view, straight from the API.', act: 'Reconcile this with your finance numbers.'}],
  search: [
    {el: 'card:Prompts', t: 'Matching prompts', see: 'Every prompt whose text matches your search, with what it cost.', get: 'One term, and everything you ever asked about it.', act: 'Click a prompt to open it with its requests and files.'},
    {el: 'card:Sessions', t: 'Matching sessions', see: 'Sessions whose title or prompts match, with cost and size.', get: 'The whole working session around a match, not just the one line.', act: 'Click a session to see how it grew.'},
    {el: 'card:Projects', t: 'Matching projects', see: 'Projects whose name matches, with their spend.', get: 'A quick jump into one repo\'s numbers.', act: 'Click a project to filter every dashboard to it.'},
    {el: 'card:Models', t: 'Matching models', see: 'Models whose name matches, with cost and tokens.', get: 'A direct route to one model\'s usage.', act: 'Search a model name to see what it cost you this range.'},
    {el: 'card:Days', t: 'Matching days', see: 'Days matching the search, with their totals.', get: 'A date lookup: type 2026-09 to pull a month.', act: 'Click a day to narrow every dashboard to it.'},
    {el: 'card:Tools & targets', t: 'Tools & targets', see: 'Tool calls and the files or targets they touched that match your search.', get: 'What the agent actually did with a given file.', act: 'Search a filename to see how often it was re-read.'}],
  exports: [
    {el: 'card:Export', t: 'Export', see: 'Every dataset behind these dashboards, as CSV or JSON, for the current filters.', get: 'Numbers you can share or analyse elsewhere.', act: 'Download the CSV for the current filters.'},
    {el: 'card:Data provenance', t: 'Data provenance', see: 'Where each number comes from: which files on this machine, read when.', get: 'Confidence that nothing is invented.', act: 'Check the last-read time if numbers look stale, then Sync.'},
    {el: 'card:Accuracy contract', t: 'Accuracy contract', see: 'What is measured, what is estimated, and what is deliberately not fabricated.', get: 'Clear limits on what these dashboards can claim.', act: 'Read this before you quote a number to someone else.'}],
};

let TOUR = null;
function tourTarget(el) {
  const page = $('#page');
  if (!el) return null;
  if (el === 'kpis') return page?.querySelector('.kpi')?.closest('.grid') || null;
  const m = /^card(\d+)$/.exec(el);
  if (m) return page?.querySelectorAll('.card')[+m[1]] || null;
  // 'card:Some title' — match a card by the start of its heading, so a step keeps
  // pointing at the right section even when cards above it are conditional.
  if (el.startsWith('card:')) {
    const want = el.slice(5).trim().toLowerCase();
    for (const c of page?.querySelectorAll('.card') || [])
      if ((c.querySelector('header h3')?.textContent || '').trim().toLowerCase().startsWith(want)) return c;
    return null;
  }
  return document.querySelector(el);
}
function startTour(steps, name) {
  endTour(false);
  // A dashboard only renders the sections its data supports, so drop the steps whose
  // section isn't on the page rather than pointing the ring at nothing.
  steps = (steps || []).filter(s => !s.el || tourTarget(s.el));
  if (!steps.length) steps = [{t: TITLES[S.view] || 'This dashboard', see: 'This dashboard has no guided tour yet.', get: '', act: 'Use the filters above to explore it.'}];
  TOUR = {steps, i: 0, name};
  const layer = document.createElement('div');
  layer.id = 'tour';
  layer.innerHTML = `<div class="tour-ring"></div><div class="tour-pop" role="dialog" aria-live="polite"></div>`;
  document.body.appendChild(layer);
  document.addEventListener('keydown', tourKeys);
  showStep();
}
function tourKeys(e) {
  if (!TOUR) return;
  if (e.key === 'Escape') endTour(true);
  else if (e.key === 'ArrowRight') tourMove(1);
  else if (e.key === 'ArrowLeft') tourMove(-1);
}
function tourMove(d) {
  if (!TOUR) return;
  const n = TOUR.i + d;
  if (n < 0) return;
  if (n >= TOUR.steps.length) return endTour(true);
  TOUR.i = n; showStep();
}
function endTour(done) {
  document.removeEventListener('keydown', tourKeys);
  $('#tour')?.remove();
  if (done) { try { localStorage.setItem(TOUR_DONE, '1'); } catch (_) {} }
  TOUR = null;
}
function showStep() {
  const {steps, i, name} = TOUR, s = steps[i];
  const ring = $('#tour .tour-ring'), pop = $('#tour .tour-pop');
  const target = tourTarget(s.el);
  const last = i === steps.length - 1;
  pop.innerHTML = `
    <div class="tour-hd"><span class="tour-n">${esc(name)} · ${i + 1} / ${steps.length}</span>
      <button class="tour-x" data-t="skip" title="Close (Esc)">✕</button></div>
    <h4>${esc(s.t)}</h4>
    ${s.see ? `<div class="tour-row"><b>What you see</b><span>${s.see}</span></div>` : ''}
    ${s.get ? `<div class="tour-row"><b>What you get</b><span>${s.get}</span></div>` : ''}
    ${s.act ? `<div class="tour-row"><b>Try this</b><span>${s.act}</span></div>` : ''}
    <div class="tour-ft">
      <button class="act ghost" data-t="skip">Skip tour</button><span class="spacer"></span>
      <button class="act" data-t="prev" ${i ? '' : 'disabled'}>← Previous</button>
      <button class="act primary" data-t="next">${last ? 'Finish' : 'Next →'}</button>
    </div>`;
  pop.querySelectorAll('[data-t]').forEach(b => b.onclick = () =>
    b.dataset.t === 'skip' ? endTour(true) : tourMove(b.dataset.t === 'next' ? 1 : -1));
  const place = () => {
    if (!TOUR) return;
    const vw = innerWidth, vh = innerHeight, pw = Math.min(360, vw - 32);
    pop.style.width = pw + 'px';
    const ph = pop.offsetHeight;
    if (!target || !target.offsetParent) {
      ring.style.display = 'none';
      pop.style.left = (vw - pw) / 2 + 'px'; pop.style.top = Math.max(16, (vh - ph) / 2) + 'px';
      return;
    }
    const r = target.getBoundingClientRect(), pad = 6;
    Object.assign(ring.style, {display: 'block', left: r.left - pad + 'px', top: r.top - pad + 'px',
      width: r.width + pad * 2 + 'px', height: Math.min(r.height, vh - 24) + pad * 2 + 'px'});
    let top = r.bottom + 14, left = Math.min(Math.max(16, r.left), vw - pw - 16);
    if (top + ph > vh - 16) top = r.top - ph - 14;                 // above
    if (top < 16) {                                               // beside, else centered
      top = Math.max(16, Math.min(r.top, vh - ph - 16));
      left = r.right + 14 + pw < vw ? r.right + 14 : r.left - pw - 14 > 0 ? r.left - pw - 14 : (vw - pw) / 2;
    }
    pop.style.left = left + 'px'; pop.style.top = top + 'px';
  };
  if (target) target.scrollIntoView({block: 'nearest', behavior: 'instant'});
  place();
  requestAnimationFrame(place);
  pop.querySelector('[data-t="next"]').focus();
}
function tourForView() { startTour(TOURS[S.view], TITLES[S.view] || 'Tour'); }
function maybeFirstTour() {
  let done = false;
  try { done = localStorage.getItem(TOUR_DONE) === '1'; } catch (_) {}
  if (done) return;
  // Per-dashboard tours are long now, so the first-visit tour only previews the first
  // few sections; the "? Tour" button plays the full one.
  const view = (TOURS[S.view] || []).slice(0, 3);
  const steps = [...TOUR_WELCOME, ...view.map(s => ({...s, t: `${TITLES[S.view]}: ${s.t}`}))];
  startTour(steps, 'Welcome');
}
addEventListener('resize', () => { if (TOUR) showStep(); });

