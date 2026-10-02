import * as C from './charts.js';
const {fmtUSD, fmtNum, fmtInt, fmtPct, seriesVar} = C;

/* ============================ state ============================ */
const S = {
  view: 'hygiene',
  opts: null,
  filter: {start: null, end: null, agents: [], models: [], projects: [], categories: [],
           include_sandbox: true, min_cost: null, min_tokens: null},
  range: '30d',
  metric: 'cost',
  grain: 'day',
  cache: new Map(),
  drawerStack: [],
  sessPage: {offset: 0, limit: 200},
  promptPage: {offset: 0, limit: 200},
};
const $ = (s, r = document) => r.querySelector(s);
const h = (html) => { const t = document.createElement('template');
  t.innerHTML = html.trim(); return t.content.firstElementChild; };
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ---------- privacy mask ----------
   For screen-sharing: prompt text and session titles (written from your prompts)
   render as dots. Only the page changes; the warehouse and exports are untouched. */
const MASK_KEY = 'finops-mask-prompts';
let MASKED = false;
try { MASKED = localStorage.getItem(MASK_KEY) === '1'; } catch (_) {}
const pt = t => MASKED && t ? String(t).replace(/\S/g, '•') : t;
const stitle = x => (!MASKED && x.title) || shortId(x.session_id);

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
// Shown where a projection depends on recent days: days after the last sync are
// unread, so they are projected rather than counted as zero.
const staleNote = x => x && x.unread_days > 0 ? `<div class="note">Your data runs to <b>${esc(x.synced_through)}</b>.
  The ${x.unread_days} day${x.unread_days === 1 ? '' : 's'} since then ${x.unread_days === 1 ? 'is' : 'are'} projected, not counted as $0.
  Press <b>Sync</b> to read them.</div>` : '';
// A rule that claims no excess says so, instead of a "$0.0000 excess" that reads like a measurement.
const excessText = f => f.est_excess_usd > 0 ? `${fmtUSD(f.est_excess_usd)} excess` : 'no excess claimed';
const statusGlyph = s => dot({healthy: 'green', high: 'yellow', approaching: 'orange', critical: 'red'}[s] || 'grey');
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

// "(up 12% vs yesterday)", with a small up/down triangle icon; nothing when yesterday had no spend to compare against.
const vsYesterday = o => {
  const y = o.cost_yesterday?.c, t = o.cost_today?.c;
  if (!y || t == null) return '';
  const d = (t - y) / y * 100;
  return ` (${I(d >= 0 ? 'triUp' : 'triDown')}${Math.abs(d).toFixed(0)}% vs yesterday)`;
};
function kpi(label, value, detail, opts = {}) {
  const na = value == null;
  return `<div class="kpi${na ? ' na' : ''}${opts.tone && !na ? ' tone-' + opts.tone : ''}"${opts.title ? ` title="${esc(opts.title)}"` : ''}>
    <div class="l">${esc(label)}${opts.badge ? ' ' + opts.badge : ''}</div>
    <div class="v${opts.small ? ' sm' : ''}">${na ? esc(naLabel()) : value}</div>
    ${detail ? `<div class="d">${detail}</div>` : ''}</div>`;
}
function card(title, bodyHtml, opts = {}) {
  return `<section class="card"${opts.style ? ` style="${opts.style}"` : ''}>
    <header><h3>${opts.icon || ''}${esc(title)}</h3>${opts.badge || ''}
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
    ['hygiene', 'gauge', 'Context hygiene'],
    ['overview', 'dashboard', 'Executive overview'],
    ['advisor', 'bulb', 'What should I do?'],
    ['agents', 'bot', 'Agents'],
    ['live', 'activity', 'Running sessions'],
    ['scorecard', 'award', 'FinOps scorecard'],
  ]],
  ['Usage', [
    ['usage', 'barChart', 'Usage timeline'],
    ['burn', 'flame', 'Burn rate & limits', 'priced'],
    ['models', 'cpu', 'Model analysis'],
    ['context', 'layers', 'Context & cache'],
  ]],
  ['Drill-down', [
    ['projects', 'folder', 'Projects'],
    ['sessions', 'terminal', 'Sessions'],
    ['prompts', 'message', 'Prompt explorer'],
    ['rankings', 'sortDesc', 'Cost rankings'],
    ['categories', 'tag', 'Prompt intelligence'],
    ['developer', 'code', 'Developer activity', 'priced'],
  ]],
  ['Optimize', [
    ['diagnose', 'scan', 'Why so many tokens?', 'priced'],
    ['attribution', 'users', 'Who used the tokens', 'priced'],
    ['subagents', 'bot', 'Subagent models', 'claude'],
    ['waste', 'trash', 'Waste detection'],
    ['freemodels', 'gift', 'Free models', 'claude'],
    ['jev', 'sparkles', 'Jev (fast decisions)'],
    ['compare', 'scale', 'Compare models', 'priced'],
    ['toolkit', 'wrench', 'Skills & MCP', 'claude'],
    ['recommendations', 'listChecks', 'Recommendations'],
    ['anomalies', 'zap', 'Anomalies'],
  ]],
  ['Plan', [
    ['forecast', 'trendUp', 'Forecast', 'priced'],
    ['budgets', 'wallet', 'Budgets', 'priced'],
    ['cloud', 'cloud', 'Billed vs local', 'cloud'],
    ['exports', 'download', 'Export & data'],
  ]],
  ['Setup', [
    ['settings', 'cog', 'Settings'],
  ]],
];

// Toolbar icons: 24px line icons drawn in currentColor, so they follow the theme.
const toolIcon = d => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const ICON = {
  tour: toolIcon('<circle cx="12" cy="12" r="10"/><path d="m16.2 7.8-2.1 6.3-6.3 2.1 2.1-6.3z"/>'),
  recent: toolIcon('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>'),
  eye: toolIcon('<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
  eyeOff: toolIcon('<path d="M10.7 5.1A10.4 10.4 0 0 1 12 5c6.4 0 10 7 10 7a18 18 0 0 1-2.2 3.2"/><path d="M6.6 6.6A17.6 17.6 0 0 0 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="m2 2 20 20"/>'),
  moon: toolIcon('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
  sun: toolIcon('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  reload: toolIcon('<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>'),
  // Sync pulls transcripts into the warehouse: a database with an arrow going in.
  sync: toolIcon('<ellipse cx="11" cy="5" rx="7" ry="3"/><path d="M4 5v6c0 1.66 3.13 3 7 3"/><path d="M4 11v6c0 1.66 3.13 3 7 3"/><path d="M18 5v5"/><path d="M19 13v8"/><path d="m16 18 3 3 3-3"/>'),
};

// Every other icon in the app: flat 24px line icons in the same style, drawn in
// currentColor so they follow the theme and whatever tone they sit in.
const IP = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  chevDown: '<path d="m6 9 6 6 6-6"/>',
  chevRight: '<path d="m9 6 6 6-6 6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  checkCircle: '<circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/>',
  xCircle: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
  circle: '<circle cx="12" cy="12" r="9"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  siren: '<path d="M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9z"/><path d="M12 8v4M12 16h.01"/>',
  target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  sparkles: '<path d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 3v4M17 5h4M5 17v4M3 19h4"/>',
  compact: '<path d="m15 15 6 6M15 21v-6h6M9 9 3 3M9 3v6H3M15 9l6-6M15 3v6h6M9 15l-6 6M9 21v-6H3"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
  handover: '<path d="m15 17 5-5-5-5"/><path d="M4 18v-2a4 4 0 0 1 4-4h12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14"/><path d="M12 17h.01"/>',
  pause: '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
  stop: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  play: '<path d="M7 4v16l13-8z"/>',
  cloudDown: '<path d="M20 16.6A5 5 0 0 0 18 7h-1.3A8 8 0 1 0 4 15.2"/><path d="M12 12v9M8 17l4 4 4-4"/>',
  arrowUp: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  triUp: '<path d="M12 6l7 11H5z"/>',
  triDown: '<path d="M12 18 5 7h14z"/>',
  external: '<path d="M15 3h6v6M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  star: '<path d="m12 2.5 2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.4l-5.9 3.1 1.2-6.5-4.8-4.6 6.6-.9z"/>',
  starHalf: '<path d="M12 2.5v14.9l-5.9 3.1 1.2-6.5-4.8-4.6 6.6-.9z"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  note: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  cog: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9 7 7M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/>',
  panel: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 15h18"/>',
  zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
  // sidebar
  gauge: '<path d="m12 14 4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0"/>',
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
  bulb: '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/><path d="M9 18h6M10 22h4"/>',
  bot: '<rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 8V4H8M2 14h2M20 14h2M9 13v2M15 13v2"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  award: '<circle cx="12" cy="8" r="6"/><path d="M15.5 12.9 17 22l-5-3-5 3 1.5-9.1"/>',
  barChart: '<path d="M3 3v18h18"/><path d="M8 17v-5M13 17V7M18 17v-8"/>',
  flame: '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.4-.5-2-1-3-1.1-2.1-.2-4 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.2.4-2.3 1-3.3.2 1.7 1.2 2.8 2.5 2.8z"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 12 10 5 10-5M2 17l10 5 10-5"/>',
  folder: '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z"/>',
  terminal: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3M12 15h6"/>',
  message: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  sortDesc: '<path d="M11 5h10M11 9h7M11 13h4M3 17l3 3 3-3M6 18V4"/>',
  tag: '<path d="M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  code: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="11.5" cy="11.5" r="3.5"/><path d="m17 17-3-3"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
  trash: '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C10 3 12 8 12 8s2-5 4.5-5a2.5 2.5 0 0 1 0 5"/>',
  scale: '<path d="M12 3v18M7 21h10M3 7h18"/><path d="m6 7-3 7a3 3 0 0 0 6 0zM18 7l-3 7a3 3 0 0 0 6 0z"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z"/>',
  listChecks: '<path d="m3 7 2 2 4-4M3 17l2 2 4-4M13 6h8M13 12h8M13 18h8"/>',
  trendUp: '<path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>',
  wallet: '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
  cloud: '<path d="M17.5 19H9a7 7 0 1 1 6.7-9h1.8a4.5 4.5 0 1 1 0 9z"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',
};
const FILLED = new Set(['play', 'triUp', 'triDown', 'star', 'starHalf']);
// I('name'): the icon as inline SVG. Toolbar icons are shared from ICON above.
const I = n => ICON[n] ? ICON[n].replace('<svg ', '<svg class="i" ')
  : `<svg class="i${FILLED.has(n) ? ' fill' : ''}" viewBox="0 0 24 24" aria-hidden="true">${IP[n]}</svg>`;
// Status and severity: a flat filled dot in the matching tone.
const dot = tone => `<svg class="i dot ${tone}" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="6"/></svg>`;

// The theme button shows the theme it switches to: a moon in light mode, a sun in dark.
function themeLabel() {
  const b = $('#theme'); if (!b) return;
  const cur = document.documentElement.getAttribute('data-theme')
    || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const to = cur === 'dark' ? 'light' : 'dark';
  b.innerHTML = to === 'dark' ? ICON.moon : ICON.sun;
  b.title = `Switch to ${to} theme`;
  b.setAttribute('aria-label', b.title);
}

function shell() {
  document.body.innerHTML = `<div class="app">
    <aside class="sidebar">
      <div class="brand"><div class="mark"><span class="dot"></span>Claude FinOps
          <span class="ver" id="ver"></span></div>
        <div class="sub">Command Center</div></div>
      <nav class="nav">${NAV.map(([g, items], gi) => `<div class="group g${gi}">${g}</div>` +
        items.map(([id, ic, label]) =>
          `<a data-view="${id}" class="g${gi}${id === 'diagnose' ? ' start' : ''}"><span class="ic">${I(ic)}</span>${label}<span class="nb" data-nb="${id}"></span></a>`).join('')).join('')}
      </nav>
      <div class="who" id="who"></div></aside>
    <div class="main">
      <header class="topbar">
        <div class="r1">
          <div class="ttl-block"><h1 id="ttl">Executive overview</h1>
            <div class="crumbs" id="crumbs"></div></div>
          <div class="scope" id="scope" aria-label="Totals for the current filters"></div>
          <span class="spacer"></span>
          <div class="search"><span class="mag">${I('search')}</span>
            <input id="gsearch" placeholder="Search prompts, sessions, models…"></div>
          <button class="iconbtn upd" id="upd" hidden></button>
          <div class="tools">
            <button class="iconbtn" id="tour-btn" title="Walk through this dashboard">${ICON.tour}Tour</button>
            <button class="iconbtn sq" id="recent-btn" title="Recent sessions and prompts, from any page (R)" aria-label="Recent">${ICON.recent}</button>
            <button class="iconbtn sq" id="mask"></button>
            <button class="iconbtn sq" id="theme"></button>
            <button class="iconbtn sq" id="refresh" title="Reload this page's numbers from the warehouse. To pick up new sessions, use Sync" aria-label="Reload view">${ICON.reload}</button>
          </div>
          <button class="act sync" id="sync" title="Re-read every agent's transcripts from disk into the warehouse">${ICON.sync}Sync</button>
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
    themeLabel();
    render();
  };
  themeLabel();
  $('#refresh').onclick = () => { bust(); render(); };
  maskLabel();
  $('#mask').onclick = () => {
    MASKED = !MASKED;
    try { localStorage.setItem(MASK_KEY, MASKED ? '1' : '0'); } catch (_) {}
    maskLabel(); closeDrawer(); render();
    if (RECENT.open) { RECENT.offset = 0; renderRecent(); }
  };
  $('#recent-btn').onclick = () => toggleRecent();
  updateChip();
  $('#sync').onclick = runSync;
  syncLabel();
  let t;
  $('#gsearch').oninput = e => { clearTimeout(t); const v = e.target.value;
    t = setTimeout(() => { if (v.trim().length >= 2) { S.searchTerm = v; go('search'); }
      else if (S.view === 'search') go('overview'); }, 260); };
}

// The page loader: a skeleton in the shape of a dashboard (KPI row, two charts, a table)
// so the layout doesn't jump when data lands, plus a status line that says what is
// happening and, on a slow query, how long it has taken.
const LOADER_STEPS = ['Reading the warehouse', 'Pricing every request', 'Adding up tokens and cache',
  'Grouping by session and project', 'Drawing the charts'];
const LOADER_TIPS = ['Press R anywhere to open Recent sessions.',
  'Click any row in a table to open its details.',
  'The eye button hides prompt text before you share your screen.',
  '"Actual" numbers come from transcripts; "Estimated" ones use list prices.',
  'Tour walks you through whichever dashboard you are on.'];
function pageLoader() {
  const bone = (w, h = 10) => `<i class="bone" style="width:${w};height:${h}px"></i>`;
  const bars = [38, 62, 45, 80, 56, 90, 70, 48, 66, 84, 58, 74]
    .map((v, i) => `<i style="height:${v}%;animation-delay:${i * 70}ms"></i>`).join('');
  return `<div class="pl" role="status" aria-live="polite">
    <div class="pl-status"><span class="pl-mark"><i></i><i></i><i></i></span>
      <span class="pl-msg">${LOADER_STEPS[0]}…</span><span class="pl-time"></span></div>
    <div class="grid g4">${Array.from({length: 4}, () =>
      `<div class="kpi skel">${bone('55%', 8)}${bone('45%', 22)}${bone('70%', 8)}</div>`).join('')}</div>
    <div class="grid g2">
      <div class="card skel"><div class="body">${bone('35%', 10)}<div class="pl-bars">${bars}</div></div></div>
      <div class="card skel"><div class="body">${bone('30%', 10)}<div class="pl-bars alt">${bars}</div></div></div>
    </div>
    <div class="card skel"><div class="body">${bone('25%', 10)}
      ${Array.from({length: 4}, (_, i) => `<div class="pl-row">${bone(`${60 - i * 8}%`)}${bone('12%')}${bone('9%')}</div>`).join('')}
    </div></div>
    <div class="pl-tip">Tip: ${esc(LOADER_TIPS[Math.floor(Math.random() * LOADER_TIPS.length)])}</div>
  </div>`;
}
function loaderTicker(page) {
  const t0 = Date.now();
  let i = 0;
  const id = setInterval(() => {
    const msg = page.querySelector('.pl-msg');
    if (!msg || !page.isConnected) return clearInterval(id);   // the view has rendered
    i = Math.min(i + 1, LOADER_STEPS.length - 1);
    msg.textContent = LOADER_STEPS[i] + '…';
    const s = Math.round((Date.now() - t0) / 1000);
    if (s >= 3) page.querySelector('.pl-time').textContent = `${s}s · large ranges take a little longer`;
  }, 1100);
}

// Headline totals for whatever the filters select, on every page, so a number
// on the page can always be read against the whole it came from.
async function scopeStrip() {
  const el = $('#scope'); if (!el) return;
  const key = qs();
  el.dataset.key = key;
  let o;
  try { o = await api('overview'); } catch { el.innerHTML = ''; return; }
  if (el.dataset.key !== key) return;   // the filters moved on while this loaded
  const cell = (l, v, t) => `<div class="sc"${t ? ` title="${esc(t)}"` : ''}><span>${l}</span><b>${v}</b></div>`;
  el.innerHTML =
    cell('Est. spend', fmtUSD(o.est_cost_usd), 'Estimated at list prices for the current filters') +
    cell('Sessions', fmtInt(o.sessions)) +
    cell('Prompts', fmtInt(o.prompts)) +
    cell('Requests', fmtNum(o.requests), fmtInt(o.requests) + ' model requests'
      + (o.unpriced_requests ? `, of which ${fmtInt(o.unpriced_requests)} have no price data and count as $0` : '')) +
    cell('Active days', fmtInt(o.active_days));
}

function maskLabel() {
  const b = $('#mask');
  // The icon shows the current state: an open eye while prompts are visible.
  b.innerHTML = MASKED ? ICON.eyeOff : ICON.eye;
  b.title = MASKED ? 'Prompt text is hidden. Click to show it'
    : 'Hide prompt text and session titles, e.g. before sharing your screen';
  b.setAttribute('aria-label', MASKED ? 'Show prompts' : 'Hide prompts');
  b.setAttribute('aria-pressed', MASKED);
  b.classList.toggle('on', MASKED);
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
  el.innerHTML = `${I('arrowUp')} v${u.latest} available`;
  el.title = `You are on ${u.current}. Click to copy:  ${u.command}`;
  el.onclick = async () => {
    try {
      await navigator.clipboard.writeText(u.command);
      el.innerHTML = `${I('check')} command copied`;
      setTimeout(() => { el.innerHTML = `${I('arrowUp')} v${u.latest} available`; }, 2200);
    } catch { prompt('Run this to upgrade:', u.command); }
  };
}

/* ---------- filter bar ---------- */
const RANGES = [['today', 'Today'], ['7d', '7 days'], ['14d', '14 days'], ['30d', '30 days'],
  ['period', 'Billing period'], ['all', 'All time'], ['custom', 'Custom']];

function applyRange(r) {
  S.range = r;
  // Ranges end today (your local date), not on the last day that happens to have data:
  // "Today" with no usage yet should read as empty, not as some earlier day.
  const last = S.opts.date_range.today || S.opts.date_range.last, bp = S.opts.billing_period;
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
    ${ag.length > 1 ? `<div class="seg" role="group" aria-label="Agent"><span class="seg-l">Agent</span>
      ${ag.map(a => `<button class="${f.agents.includes(a.id) ? 'on' : ''}" data-agent="${esc(a.id)}"
        title="${esc(a.note)}${a.requests ? '' : ' (no usage recorded)'}. Cmd/Shift-click to combine">${esc(a.name)}${a.requests ? '' : ' ·'}</button>`).join('')}
      <button class="${f.agents.length === ag.length ? 'on' : ''}" data-agent="*" title="Every agent together">All</button></div>` : ''}
    <div class="seg" role="group" aria-label="Date range"><span class="seg-l">Range</span>
    ${RANGES.map(([k, l]) => `<button class="${S.range === k ? 'on' : ''}"
      data-range="${k}">${l}</button>`).join('')}</div>
    <div class="chipsel"><button class="chip ${f.models.length ? 'on' : ''}" data-pop="models">
      Model ${nSel(f.models)} ${I('chevDown')}</button></div>
    <div class="chipsel"><button class="chip ${f.projects.length ? 'on' : ''}" data-pop="projects">
      Project ${nSel(f.projects)} ${I('chevDown')}</button></div>
    <div class="chipsel"><button class="chip ${f.categories.length ? 'on' : ''}" data-pop="categories">
      Category ${nSel(f.categories)} ${I('chevDown')}</button></div>
    <div class="chipsel"><button class="chip ${(f.min_cost || f.min_tokens) ? 'on' : ''}"
      data-pop="thresholds">Thresholds ${I('chevDown')}</button></div>
    <button class="chip toggle ${f.include_sandbox ? '' : 'on'}" id="sbx" role="switch" aria-checked="${!f.include_sandbox}"
      title="Sandbox agents are throwaway projects. ${f.include_sandbox ? 'They are included; click to leave them out' : 'They are left out; click to include them'}">
      <i></i>Hide sandbox</button>
    ${(f.models.length || f.projects.length || f.categories.length || f.min_cost || f.min_tokens)
      ? `<button class="chip clr" id="clr">Clear filters ${I('x')}</button>` : ''}
    <span class="spacer"></span>
    <span class="coverage" title="Data in the warehouse · last rebuilt ${esc((o.meta.built_at||'').slice(0,16).replace('T',' '))}">
      ${esc(o.meta.transcript_files)} transcripts · since ${esc(o.date_range.first)}</span>`;

  $('#filters').querySelectorAll('[data-range]').forEach(b =>
    b.onclick = e => { if (b.dataset.range === 'custom') { e.stopPropagation(); return openCustom(b); }
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
      <div class="note" style="margin:8px 0 6px">These apply per <strong>request</strong>, not per
        session or prompt. A session's totals will count only its requests above the
        threshold, so filtered totals read lower than the session's real cost.</div>
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
  const open = btn.parentElement.querySelector('.pop');
  closePops(); if (open) return;
  const pop = h(`<div class="pop" style="left:auto;right:0"><div class="hd">From</div>
    <input type="date" id="cs" value="${S.filter.start || S.opts.date_range.first}">
    <div class="hd">To</div>
    <input type="date" id="ce" value="${S.filter.end || S.opts.date_range.last}">
    <button class="chip on" id="ca" style="width:100%;justify-content:center">Apply</button></div>`);
  btn.parentElement.style.position = 'relative';
  btn.parentElement.appendChild(pop);
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
      <button class="iconbtn" data-x>Close ${I('x')}</button></header>
    <div class="content">${bodyHtml}</div></aside>`);
  scrim.onclick = closeDrawer;
  d.querySelector('[data-x]').onclick = closeDrawer;
  document.body.append(scrim, d);
  return d;
}
document.addEventListener('keydown', e => {
  // Esc closes the top-most layer: a detail drawer first, then the Recent panel.
  if (e.key === 'Escape') { if ($('.drawer')) closeDrawer(); else if (RECENT.open) toggleRecent(false); return; }
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
  if (!typing && !e.metaKey && !e.ctrlKey && !e.altKey && e.key.toLowerCase() === 'r' && !$('.drawer'))
    toggleRecent();
});

// Lists honour the date range; a drawer shows the whole prompt or session, plus what fell
// inside the selected dates when the two differ, so both list and drawer numbers make sense.
const wholeNote = (what, x) => {
  const r = x && x.in_range;
  if (!r || (Math.abs(r.cost - (x.est_cost_usd || 0)) < 0.005 && r.requests === (x.request_count ?? r.requests))) return '';
  return `<div class="note">In the selected dates: <b>${fmtUSD(r.cost)}</b> across ${fmtInt(r.requests)} requests
    (the list shows this). The totals below cover the whole ${what}, including other days.</div>`;
};
async function openPrompt(id) {
  const d = drawer('Prompt detail', '<div class="loading">Loading…</div>');
  const p = await fetch(`/api/prompt/${id}?${qs()}`).then(r => r.json());
  d.querySelector('.content').innerHTML = `${wholeNote('prompt', p)}
    <div class="grid g4">
      ${kpi('Estimated cost', fmtUSD(p.est_cost_usd), null, {badge: BADGE.estimated})}
      ${kpi('Billable tokens', fmtInt(p.billable_tokens))}
      ${kpi('Output tokens', fmtInt(p.output_tokens))}
      ${kpi('Peak context', fmtInt(p.max_context_tokens))}
    </div>
    ${card('Prompt', `<div class="prompt-text">${esc(pt(p.text))}</div>
      <div class="mt" style="margin-top:8px;display:flex;gap:14px;flex-wrap:wrap;font-size:11px;color:var(--muted)">
        <span>${esc(p.ts || '')}</span><span class="pill">${esc(p.category)}</span>
        <span>confidence ${(100*(p.category_confidence||0)).toFixed(0)}%</span>
        ${(p.category_evidence||[]).length ? `<span>matched: ${esc(pt(p.category_evidence.join(', ')))}</span>` : ''}
        <span>${fmtInt(p.char_len)} chars · ${fmtInt(p.word_len)} words</span>
        ${p.source ? `<span class="pill">${esc(p.source)}</span>` : ''}
      </div>`, {badge: BADGE.actual})}
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
      <dt>Session</dt><dd><a data-sess="${esc(p.session_id)}">${esc((!MASKED && p.session_title) || shortId(p.session_id))}</a></dd>
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
  const s = await fetch(`/api/session/${encodeURIComponent(id)}?${qs()}`).then(r => r.json());
  const cont = d.querySelector('.content');
  cont.innerHTML = `${wholeNote('session', s)}
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
      {h: 'Prompt', trunc: 1, title: r => pt(r.preview), f: r => esc(pt(r.preview))},
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
      ${s.resume ? `<dt>Resume</dt><dd><span class="mono">${esc(s.resume)}</span>
        <button class="btn pb-copy" id="sess-resume" data-copy="${esc(s.resume)}">Copy</button></dd>` : ''}
      <dt>Session ID</dt><dd class="mono">${esc(s.id)}</dd>
      <dt>Title</dt><dd>${s.title ? esc(pt(s.title)) : NA()}</dd>
      <dt>Project</dt><dd>${esc(s.project)}</dd>
      <dt>Git branch</dt><dd>${s.git_branch ? esc(s.git_branch) : NA()}</dd>
      <dt>CLI version</dt><dd>${s.cli_version ? esc(s.cli_version) : NA()}</dd>
      <dt>Started / ended</dt><dd>${esc(s.started_at||'—')} → ${esc(s.ended_at||'—')}</dd>
      <dt>Models</dt><dd>${esc(s.models || '—')}</dd>
      <dt>Lines changed</dt><dd>${NA()}</dd>
      <dt>Commits / PRs</dt><dd>${NA()}</dd>
    </dl>`)}`;
  wireTable(cont, s.prompts, r => openPrompt(r.prompt_id));
  const rb = cont.querySelector('#sess-resume');
  if (rb) rb.onclick = async () => {
    try { await navigator.clipboard.writeText(rb.dataset.copy); rb.textContent = 'Copied'; }
    catch { rb.textContent = 'Copy failed'; } };
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
        `${fmtUSD(o.cost_today.c)} today${vsYesterday(o)} · ${fmtUSD(o.cost_week.c)} last 7d`, {badge: BADGE.estimated})}
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
        <div class="chart" id="mdonut" style="width:auto;flex:none"></div><div style="flex:1;min-width:max-content" id="mtable"></div></div>`,
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
          <span style="font-variant-numeric:tabular-nums">${excessText(f)}</span></div>
          <div class="dt">${esc(f.detail)}</div></div>`).join('')}</div>`,
        {badge: BADGE.estimated,
         hint: `${fmtPct(waste.high_severity_pct)} high-severity exposure`,
         footer: esc(waste.note)})}
      <div style="display:flex;flex-direction:column;gap:12px;min-width:0">
      ${card('Optimization opportunities', `<div class="stack">${
        b.recommendations.recommendations.length
        ? b.recommendations.recommendations.slice(0, 5).map(r => `<div class="item">
            <div class="hd">${esc(r.title)}</div>
            <div class="mt"><span>Actual ${fmtUSD(r.actual_cost_usd)}</span></div>
            <div class="note">${esc(r.caveat)}</div></div>`).join('')
        : '<div class="empty">No recommendation met the evidence threshold</div>'}</div>`,
        {badge: BADGE.recommendation})}
      ${card('FinOps score', `<div class="scorewrap">
        <div><div class="scorenum">${scorecard.score}</div>
          <div class="scoregrade">out of 100</div></div>
        <div style="flex:1;min-width:230px" class="stack">${scorecard.dimensions.map(d => `
          <div><div style="display:flex;justify-content:space-between;font-size:11.5px">
            <span>${esc(d.name)}</span><span style="font-variant-numeric:tabular-nums">${d.score}</span></div>
          <div class="meter ${d.score >= 75 ? 'healthy' : d.score >= 50 ? 'high'
            : d.score >= 30 ? 'approaching' : 'critical'}"><i style="width:${d.score}%"></i></div></div>`).join('')}
        </div></div>`, {badge: BADGE.estimated})}
      </div>
    </div>

    <div class="grid">
      ${card('Forecast', '<div class="chart" id="fan"></div><div class="legend" id="fanleg"></div>',
        {badge: BADGE.forecast, hint: forecast.available ? forecast.method : ''})}
    </div>`;

  // burn gauge + notes
  const gEl = $('#gauge', page);
  if (alloc.configured) {
    C.gauge(gEl, {pct: alloc.used_pct, status: alloc.status, label: 'of plan allowance'});
    $('#burnnotes', page).innerHTML = `<div class="stack">
      <div class="item"><div class="hd">${statusGlyph(alloc.status)} ${alloc.days_until_limit == null
        ? `<b>Exceeded</b> your current limit.`
        : `You are likely to reach your current limit in <b>${alloc.days_until_limit}</b> days${alloc.limit_date
          ? ` (around ${esc(alloc.limit_date)})` : ''}.`}</div></div>
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
    {h: 'Prompt', trunc: 1, title: r => pt(r.preview), f: r => esc(pt(r.preview))},
    {h: 'Category', f: r => `<span class="pill">${esc(r.category)}</span>`},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.ptokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.pcost)},
  ], lp, {onRow: 1});
  wireTable($('#topprompts', page), lp, r => openPrompt(r.prompt_id));

  const ls = (await api('sessions', '&limit=10&order=cost')).rows;
  $('#topsessions', page).innerHTML = table([
    {h: 'Session', trunc: 1, title: r => r.session_id,
     f: r => esc(stitle(r))},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Prompts', num: 1, f: r => fmtInt(r.prompts)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
  ], ls, {onRow: 1});
  wireTable($('#topsessions', page), ls, r => openSession(r.session_id));

  if (forecast.available && !forecast.insufficient_history) {
    C.forecastFan($('#fan', page), {history: burn.series, scenarios: forecast.scenarios,
      remainingDays: forecast.remaining_days, nowLabel: forecast.unread_days ? 'last sync' : 'today'});
    $('#fanleg', page).innerHTML = `<span class="it"><span class="swatch"
      style="background:var(--s1)"></span>Cumulative actual (estimated cost)</span>
      <span class="it"><span class="swatch" style="background:var(--s1);opacity:.35"></span>
      Forecast band: conservative → high</span>`;
  } else if (forecast.available) {
    $('#fan', page).innerHTML = '<div class="empty">Fewer than 7 priced days in the window: '
      + 'bands not shown.</div>';
  } else $('#fan', page).innerHTML = '<div class="empty">Not enough history to forecast</div>';

  renderAdvisorHero(page, advisor);
  // focus strip loads after the page so a slow diagnosis never delays the overview
  api('diagnose').then(d => {
    if (!page.isConnected) return;
    const el = h(focusStrip(d) || '<div></div>');
    page.insertBefore(el, page.querySelector('.actnow')?.nextSibling || page.firstChild);
    page._focusData = d;
    wireFocus(page);
  }).catch(() => {});
  if (hasClaude()) fetch('/api/act_now').then(r => r.json()).then(d => {
    if (!page.isConnected || !(d.items || []).length) return;
    const el = h(actNowStrip(d.items));
    page.insertBefore(el, page.firstChild);
    wireActNow(el, d.items);
  }).catch(() => {});
};

/* ---------- act now: one-click actions, on the page everyone opens first ---------- */
const ACT_ICON = {session: 'compact', skill: 'cog', memory: 'note', statusline: 'panel'};
const codeTicks = t => esc(t).replace(/`([^`]+)`/g, '<code>$1</code>');
function actNowStrip(items) {
  const btn = it => it.kind === 'session'
    ? '<button class="act" data-do="compact" title="Types /compact into that session\'s terminal">' + I('compact') + ' Compact</button>'
      + '<button class="act ghost" data-do="handover">' + I('handover') + ' Hand over</button>'
    : it.kind === 'skill' ? '<button class="act" data-do="skill">' + I('plus') + ' Create skill</button>'
    : it.kind === 'memory' ? '<button class="act ghost" data-do="memory">Show examples</button>'
    : '<button class="act" data-do="statusline">Install statusline</button>';
  return `<section class="actnow"><div class="focus-hd">${I('zap')} Act now
      <span class="note">one click each · nothing changes until you click</span></div>
    ${items.map((it, i) => `<div class="actnow-it" data-i="${i}">
      <span class="ai">${ACT_ICON[it.kind] ? I(ACT_ICON[it.kind]) : '•'}</span>
      <div class="ft"><b>${codeTicks(MASKED && it.private ? it.title.split(it.private).join(pt(it.private)) : it.title)}</b><div class="note">${esc(it.detail)}</div></div>
      <div class="live-actions">${btn(it)}<span class="live-msg"></span></div></div>`).join('')}</section>`;
}
function wireActNow(root, items) {
  root.querySelectorAll('.actnow-it').forEach(row => {
    const it = items[+row.dataset.i], msg = row.querySelector('.live-msg');
    const say = (t, ok) => { msg.textContent = t; msg.className = 'live-msg ' + (ok ? 'ok' : 'err'); };
    row.querySelectorAll('[data-do]').forEach(b => b.onclick = async () => {
      const what = b.dataset.do;
      if (what === 'handover') return go('live');
      if (what === 'memory') return jumpTo('diagnose', 'dx-mem');
      if (what === 'compact' && !armed(b)) return;
      b.disabled = true;
      try {
        if (what === 'compact') {
          const r = await sessionAction(it.pid, 'compact', {agent: 'claude'});
          if (!r.ok && r.copy) { try { await navigator.clipboard.writeText(r.copy); } catch {} }
          say(r.ok ? r.message : `Failed: ${r.error}`, r.ok);
          b.disabled = false; return;
        }
        const r = what === 'skill' ? await doAction('skill', it.skill) : await doAction('statusline');
        say(what === 'skill' ? `Created ${r.path}. Use it with ${r.use}.` : r.message, r.ok !== false);
        if (r.ok !== false) b.remove(); else b.disabled = false;
      } catch (e) { say('Failed: ' + e.message, false); b.disabled = false; }
    });
  });
}
// Two-click confirm, inline (no browser dialogs): the first click arms, the second runs.
function armed(b) {
  if (b.classList.contains('armed')) { b.classList.remove('armed'); b.textContent = b.dataset.label; return true; }
  b.dataset.label = b.textContent; b.classList.add('armed'); b.textContent = 'Click again to confirm';
  setTimeout(() => { if (b.classList.contains('armed')) { b.classList.remove('armed'); b.textContent = b.dataset.label; } }, 4000);
  return false;
}
// Open a view and scroll to an element that appears once the (possibly slow) view renders.
async function jumpTo(view, anchor) {
  if (S.view !== view) go(view);
  for (let i = 0; i < 60; i++) {
    const el = document.getElementById(anchor);
    if (el) { el.scrollIntoView({behavior: 'smooth', block: 'start'}); el.classList.add('flash');
      setTimeout(() => el.classList.remove('flash'), 1600); return; }
    await new Promise(r => setTimeout(r, 250));
  }
}

/* ---------- row actions: Resume (copy) and, for a running session, Compact ---------- */
async function liveSessions() {
  try {
    const d = await fetch('/api/live?agents=claude').then(r => r.json());
    return new Map((d.sessions || []).filter(x => x.signalable && x.pid).map(x => [x.session_id, x.pid]));
  } catch { return new Map(); }
}
const rowActs = (r, live) => {
  const pid = live && live.get(r.session_id);
  return `<span class="row-acts">${r.resume ? `<button class="act ghost" data-resume="${esc(r.resume)}"
    title="Copy: ${esc(r.resume)}">${I('copy')} Resume</button>` : ''}${pid ? ` <button class="act" data-compact="${pid}"
    title="Running now: types /compact into its terminal">${I('compact')} Compact</button>` : ''}</span>`;
};
function wireRowActs(host) {
  if (!host) return;
  host.querySelectorAll('[data-resume]').forEach(b => b.onclick = async e => {
    e.stopPropagation();
    try { await navigator.clipboard.writeText(b.dataset.resume); b.innerHTML = `${I('check')} Copied`; }
    catch { b.textContent = b.dataset.resume; }
    setTimeout(() => { b.innerHTML = `${I('copy')} Resume`; }, 1800);
  });
  host.querySelectorAll('[data-compact]').forEach(b => b.onclick = async e => {
    e.stopPropagation();
    if (!armed(b)) return;
    b.disabled = true;
    try {
      const r = await sessionAction(+b.dataset.compact, 'compact', {agent: 'claude'});
      if (!r.ok && r.copy) { try { await navigator.clipboard.writeText(r.copy); } catch {} }
      b.innerHTML = r.ok ? `${I('check')} Sent` : 'Failed'; b.title = r.ok ? r.message : r.error;
    } catch (err) { b.textContent = 'Failed'; b.title = err.message; }
    setTimeout(() => { b.disabled = false; b.innerHTML = `${I('compact')} Compact`; }, 2500);
  });
}

function renderAdvisorHero(page, advisor) {
  if (!page || !advisor || !Array.isArray(advisor.actions)) return;
  const hero = h(`<div class="hero"><h3>${I('sparkles')} AI FinOps Advisor — ${esc(advisor.question)}
    <span class="spacer"></span>
    <span class="note" style="font-weight:400">${esc(advisor.generated_from)}</span></h3>
    <div class="advisor-list">${advisor.actions.length ? advisor.actions.map((a, i) =>
      `<div class="advisor-item"><div class="num p${Math.min(a.priority, 3)}"></div>
        <div class="tx"><b>${esc(a.text)} <span class="badge ${a.basis === 'forecast' ? 'fc'
          : a.basis === 'recommendation' ? 'rec' : 'est'}">${esc(a.basis.split(':')[0])}</span></b>
        <span>${esc(a.detail)}</span></div></div>`).join('')
      : '<div class="empty">Nothing needs your attention in this range</div>'}</div>

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
        <span class="spacer"></span><span class="badge">${esc(r.confidence)}</span></div>
        ${r.current_model ? `<div class="dt"><b>Currently on:</b> ${esc(r.current_model)}${
          r.scope ? ` · ${esc(r.scope)}` : ''}</div>` : ''}
        ${r.detail ? `<div class="dt">${esc(r.detail)}</div>` : ''}
        <div class="grid g3" style="gap:8px;margin:4px 0">
          ${kpi('Spend involved', fmtUSD(r.actual_cost_usd), null, {small: 1, badge: BADGE.estimated})}
        </div>
        ${r.alternatives && r.alternatives.length ? `<div class="dt" style="margin-top:6px">
          <b>Your options${r.agent ? ` within ${esc(r.agent)}` : ''}</b> — pick the trade-off you want:</div>
          ${table([
            {h: 'Model', f: a => esc(a.name) + (a.suggested
              ? ' <span class="badge rec">suggested</span>' : '')},
            {h: 'Tier', f: a => esc(a.tier)},
            {h: 'Est. cost', num: 1, f: a => fmtUSD(a.estimated_cost_usd)},
          ], r.alternatives)}` : ''}
        <div class="note">${esc(r.caveat)}</div></div>`).join('')}</div>`
      : '<div class="empty">No recommendation met the evidence threshold for this range</div>',
      {badge: BADGE.recommendation,
       footer: 'These are observations from your own usage. No dollar saving is attached unless the method can support one — a guessed percentage of spend is not a saving.'})}
    ${card('Anomalies to inspect', `<div class="stack" id="anolist">${anos.anomalies.map((a, i) =>
      `<div class="item sev-${a.severity} clickable" data-i="${i}"><div class="hd">
        ${I(a.severity === 'high' ? 'siren' : 'alert')} ${esc(a.title)}</div>
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
  const [tl, models, ov, hm] = await Promise.all([api('timeline', `&grain=${S.grain}`),
    api('models'), api('overview'), api('heatmap')]);
  const [, mlabel, mfmt] = METRICS.find(m => m[0] === S.metric) || METRICS[0];
  const hmMetric = ['cost', 'tokens', 'requests'].includes(S.metric) ? S.metric : 'cost';
  page.innerHTML = `
    <div class="grid g5">
      ${kpi('Total tokens', fmtNum(ov.billable_tokens), null, {badge: BADGE.actual})}
      ${kpi('Input', fmtNum(ov.input_tokens), 'uncached input only')}
      ${kpi('Output', fmtNum(ov.output_tokens), `${fmtNum(ov.thinking_tokens)} thinking`)}
      ${kpi('Cache read', fmtNum(ov.cache_read_tokens))}
      ${kpi('Cache write', fmtNum(ov.cache_write_tokens))}
      ${kpi('Requests', fmtInt(ov.requests), ov.unpriced_requests ? `${fmtInt(ov.unpriced_requests)} with no price data ($0)` : null)}
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
       hint: 'click a bucket to drill into that day',
       footer: 'Days are your local calendar days (this computer\'s time zone, or CLAUDE_FINOPS_TZ). '
             + 'Claude Code stamps its transcripts in UTC; finops converts them when it reads them.',
       flush: 0})}
    ${card('Peak hours', '<div class="chart" id="heat"></div>',
      {badge: hmMetric === 'cost' ? BADGE.estimated : BADGE.actual,
       hint: `your local time (${hm.tz || 'local'}) · ${hmMetric === 'cost' ? 'estimated cost' : hmMetric}`})}
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
  C.heatmap($('#heat', page), {cells: hm.cells, metric: hmMetric});

  // per-model stacked mix
  const byDay = new Map();
  const mlist = models.rows.filter(r => r.cost > 0).map(r => r.model);
  const perModel = await Promise.all(mlist.map(async m => {
    // replace the model filter (not add to it): each series is exactly one model
    const q = new URLSearchParams(qs()); q.set('models', m);
    const r = await fetch(`/api/timeline?${q}`)
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
  const [burn, plan] = await Promise.all([api('burn'), fetch('/api/plan_history')
    .then(r => r.json()).catch(e => ({ok: false, reason: e.message}))]);
  const bp = burn.period;
  const rows = Object.entries(burn.allowances);
  page.innerHTML = `${staleNote(burn)}
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
          <div class="hd">${statusGlyph(a.status)} ${a.days_until_limit == null
            ? `<b>Exceeded</b> this limit.`
            : `You are likely to reach this limit in <b>${a.days_until_limit}</b> days.`}</div>
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
    ${card('Plan limits over time', plan.ok ? `
      <div class="grid g3">
        ${kpi('5-hour peak', fmtPct(plan.summary.five_hour_peak ?? 0, 0), null, {badge: BADGE.actual})}
        ${kpi('Times at 90%+ (5-hour)', fmtInt(plan.summary.five_hour_ge90),
          `${fmtInt(plan.summary.five_hour_hit100)} reached 100%`)}
        ${kpi('Weekly peak', fmtPct(plan.summary.weekly_peak ?? 0, 0),
          `${fmtInt(plan.summary.weekly_ge90)} times at 90%+`)}
      </div>
      <div class="legend" id="planleg"></div><div class="chart" id="plan"></div>`
      : `<div class="empty">${esc(plan.reason || 'Unavailable')}</div>`,
      {badge: BADGE.actual, hint: '5-hour and weekly plan usage, % used',
       footer: 'Read from the Claude desktop app\'s local plan-usage-history.json. '
             + 'Its format is undocumented, so this card may go blank after an app update.'})}
    ${card('Daily consumption within the billing period', '<div class="chart" id="bs"></div>',
      {badge: BADGE.estimated})}`;
  for (const [k, a] of rows) if (a.configured)
    C.gauge($(`#g-${k}`, page), {pct: a.used_pct, status: a.status, label: 'of allowance'});
  if (plan.ok) {
    const ps = [{key: 'five_hour', label: '5-hour window', color: seriesVar(0), fmt: v => fmtPct(v, 0)},
                {key: 'weekly', label: 'Weekly window', color: seriesVar(1), fmt: v => fmtPct(v, 0)}];
    C.legend($('#planleg', page), ps.map(s => ({label: s.label, color: s.color})));
    C.timeSeries($('#plan', page), {rows: plan.series, x: 't', type: 'line', series: ps,
      fmt: v => fmtPct(v, 0), max: 100, height: 220,
      xLabel: t => new Date(t).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})});
  }
  C.timeSeries($('#bs', page), {rows: burn.series, x: 'day', type: 'bar',
    series: [{key: 'cost', label: 'Estimated cost', color: seriesVar(0), fmt: fmtUSD}],
    fmt: fmtUSD, height: 220, xLabel: shortDay});
};

/* ---------- models ---------- */
VIEWS.models = async (page) => {
  const [m, lc] = await Promise.all([api('models'), api('long_context_pricing')]);
  const sup = m.superlatives, rows = m.rows;
  const find = k => rows.find(r => r.model === sup[k]);
  page.innerHTML = `
    ${lc.unpriced_requests ? `<div class="hero">
      <h3>${I('alert')} Some long-context requests are priced low</h3>
      <div class="stack">
        <div class="item sev-high">
          <div class="hd">${fmtInt(lc.unpriced_requests)} requests sent more context than their
            model's standard window, so they ran on the long-context variant — which bills at a
            premium this price table does not have.</div>
          <div class="dt">Affected: ${lc.unpriced_models.map(esc).join(', ')}.
            Their estimated cost is <strong>understated</strong>. Add a
            <code>"&lt;model&gt;[1m]"</code> entry to <code>config/pricing.json</code> and rebuild
            to correct it. Requests with a configured long-context price
            (${fmtInt(lc.repriced_requests)}) are already billed at it.</div></div>
      </div></div>` : ''}
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
        + '). A Claude model with no price entry is priced as the newest model of its family and marked; any other unlisted model counts as $0. Add it to config/pricing.json to price it exactly.'})}
    ${card('Price table in effect', table([
      {h: 'Model', f: r => `<span class="swatch" style="background:${modelColor(r[0])}"></span>${esc(r[1].display_name || r[0])}`},
      {h: 'Tier', f: r => `<span class="pill">${esc(r[1].tier)}</span>`},
      {h: 'Input /M', num: 1, f: r => '$' + r[1].input},
      {h: 'Output /M', num: 1, f: r => '$' + r[1].output},
      {h: 'Cache write 5m /M', num: 1, f: r => '$' + r[1].cache_write_5m},
      {h: 'Cache write 1h /M', num: 1, f: r => '$' + r[1].cache_write_1h},
      {h: 'Cache read /M', num: 1, f: r => '$' + r[1].cache_read},
      {h: 'Context window', num: 1, f: r => r[1].context_window ? fmtNum(r[1].context_window) : '—'},
    ], Object.entries(S.opts.pricing.models).filter(([k]) => (S.opts.pricing.used || []).includes(k))),
      {hint: `the ${(S.opts.pricing.used || []).length} models in your data, of ${Object.keys(S.opts.pricing.models).length} priced · edit config/pricing.json to update`})}`;

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
      ${r.pricing_known || r.tier === 'free' ? ''
        : r.priced_as_family ? `<span class="badge na" title="no entry of its own in config/pricing.json; priced as the newest model of its family">priced as ${esc(r.priced_as_family)}</span>`
        : '<span class="badge na" title="no price entry in config/pricing.json, so its cost counts as $0">not priced: $0</span>'}`},
    {h: 'Tier', f: r => r.tier === 'unknown' || r.tier === 'unpriced'
      ? `<span class="pill">unpriced</span>`
      : `<span class="pill">${esc(r.tier)}</span>`},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Sessions', num: 1, f: r => fmtInt(r.sessions)},
    {h: 'Input', num: 1, f: r => fmtNum(r.input_tokens)},
    {h: 'Output', num: 1, f: r => fmtNum(r.output_tokens)},
    {h: 'Cache read', num: 1, f: r => fmtNum(r.cache_read_tokens)},
    {h: 'Cache write', num: 1, f: r => fmtNum(r.cache_write_tokens)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Avg context', num: 1, f: r => fmtNum(r.avg_context)},
    {h: 'Ctx util', num: 1, f: r => (r.utilization_pct == null ? '—' : fmtPct(r.utilization_pct))
      + (r.over_window_requests ? ` <span class="note" title="requests over a window this price table cannot explain">· ${fmtInt(r.over_window_requests)} over</span>` : '')
      + (r.unknown_window_requests && r.utilization_pct == null ? ` <span class="note" title="no context window known for this model">· window unknown</span>` : '')},
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
// With a session token budget the dot follows it (whole-session tokens); without one,
// it keeps flagging rows far above the loaded average.
function sessFlag(r, avgTok) {
  if (!r.budget_tokens) return r.tokens > avgTok * 3 ? dot('red') + ' ' : '';
  const pct = 100 * (r.session_tokens || 0) / r.budget_tokens;
  if (pct < 75) return '';
  return `<span title="${fmtPct(pct)} of your ${fmtNum(r.budget_tokens)}-token session budget">${
    dot(pct >= 100 ? 'red' : 'yellow')}</span> `;
}
VIEWS.sessions = async (page) => {
  const order = S.sessOrder || 'cost';
  const sp = S.sessPage;
  const res = await api('sessions', `&limit=${sp.offset + sp.limit}&order=${order}`);
  const rows = res.rows, total = res.total;
  const [eff, live] = await Promise.all([api('efficiency'), liveSessions()]);
  const avgTok = rows.length ? rows.reduce((a, r) => a + r.tokens, 0) / rows.length : 0;
  page.innerHTML = `
    <div class="grid g4">
      ${kpi('Sessions in range', fmtInt(total), null, {badge: BADGE.actual})}
      ${kpi('Avg cost / loaded session', fmtUSD(rows.reduce((a, r) => a + r.cost, 0) / (rows.length || 1)),
        null, {badge: BADGE.estimated})}
      ${kpi('Avg tokens / loaded session', fmtNum(avgTok))}
      ${kpi('Avg tokens / loaded prompt', fmtNum(rows.reduce((a, r) => a + (r.tokens_per_prompt || 0), 0)
        / (rows.filter(r => r.tokens_per_prompt).length || 1)))}
    </div>
    ${card('Session explorer', `<div class="filters" style="margin:0 0 8px">
        ${[['cost', 'Most expensive'], ['tokens', 'Most tokens'], ['duration', 'Longest'],
           ['prompts', 'Most prompts'], ['recent', 'Most recent']].map(([k, l]) =>
          `<button class="chip ${order === k ? 'on' : ''}" data-so="${k}">${l}</button>`).join('')}
        <span class="spacer"></span>
        <span class="note">${rows.some(r => r.budget_tokens)
          ? 'dots mark sessions at 75% (amber) and 100% (red) of your session token budget'
          : `rows above ${fmtNum(avgTok * 3)} tokens are unusually expensive`}</span>
      </div><div id="st"></div>
      <div class="filters" style="margin-top:8px">
        <span class="note">Showing ${fmtInt(Math.min(rows.length, total))} of ${fmtInt(total)}</span>
        <span class="spacer"></span>
        ${rows.length < total ? '<button class="btn pb-copy" id="sess-more">Load more</button>' : ''}
      </div>`, {badge: BADGE.estimated, hint: 'click a row to open the session'})}
    <div class="grid g2">
      ${card('Lowest output yield', table([
        {h: 'Session', trunc: 1, f: r => esc(stitle(r))},
        {h: 'Output share', num: 1, f: r => fmtPct(r.output_ratio * 100, 2)},
        {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
        {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
      ], eff.low_efficiency_sessions, {onRow: 1}), {badge: BADGE.estimated, hint: 'low efficiency'})}
      ${card('Highest output yield', table([
        {h: 'Session', trunc: 1, f: r => esc(stitle(r))},
        {h: 'Output share', num: 1, f: r => fmtPct(r.output_ratio * 100, 2)},
        {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
        {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
      ], eff.high_efficiency_sessions, {onRow: 1}), {badge: BADGE.estimated, hint: 'high efficiency'})}
    </div>`;
  page.querySelectorAll('[data-so]').forEach(b => b.onclick = () => {
    S.sessOrder = b.dataset.so; S.sessPage.offset = 0; bust(); render(); });
  const sessMore = $('#sess-more', page);
  if (sessMore) sessMore.onclick = () => { sp.offset += sp.limit; bust(); render(); };
  $('#st', page).innerHTML = table([
    {h: 'Session', trunc: 1, title: r => r.session_id,
     f: r => `${sessFlag(r, avgTok)}${esc(stitle(r))}
       <div class="sub mono">${esc(shortId(r.session_id))}</div>`},
    {h: '', f: r => rowActs(r, live)},
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
  wireRowActs($('#st', page));
  const effCards = [...page.querySelectorAll('.card')].filter(c =>
    /output yield/.test(c.querySelector('h3')?.textContent || ''));
  wireTable(effCards[0], eff.low_efficiency_sessions, r => openSession(r.session_id));
  wireTable(effCards[1], eff.high_efficiency_sessions, r => openSession(r.session_id));
  addChart(page, 'Top sessions by estimated cost', el => C.barsH(el, {
    rows: [...rows].sort((a, b) => b.cost - a.cost).slice(0, 12),
    label: r => clip(stitle(r), 42), value: r => r.cost,
    sub: r => `<div class="row"><span class="k">Project</span><span class="v">${esc(r.project)}</span></div>
      <div class="row"><span class="k">Tokens</span><span class="v">${fmtNum(r.tokens)}</span></div>
      <div class="row"><span class="k">Prompts</span><span class="v">${fmtInt(r.prompts)}</span></div>`,
    onClick: r => openSession(r.session_id)}), {badge: BADGE.estimated, hint: 'Click a bar to open the session'});
};

/* ---------- prompt explorer ---------- */
VIEWS.prompts = async (page) => {
  const order = S.promptOrder || 'cost';
  const q = S.promptQ || '';
  const pp = S.promptPage;
  const res = await api('prompts',
    `&limit=${pp.offset + pp.limit}&order=${order}${q ? '&q=' + encodeURIComponent(q) : ''}`);
  const rows = res.rows, total = res.total;
  page.innerHTML = `
    ${card('Prompt explorer', `
      <div class="filters" style="margin:0 0 8px">
        <div class="search"><span class="mag">${I('search')}</span>
          <input id="pq" value="${esc(q)}" placeholder="Search prompt text…"></div>
        <span class="divider"></span>
        ${[['cost', 'Most expensive'], ['tokens', 'Most tokens'], ['length', 'Longest'],
           ['efficiency', 'Most efficient'], ['cheapest', 'Cheapest'], ['recent', 'Most recent']]
          .map(([k, l]) => `<button class="chip ${order === k ? 'on' : ''}" data-po="${k}">${l}</button>`).join('')}
        <span class="spacer"></span>
        <span class="note">Showing ${fmtInt(Math.min(rows.length, total))} of ${fmtInt(total)} prompts · global filters apply</span>
        ${rows.length < total ? '<button class="btn pb-copy" id="prompt-more">Load more</button>' : ''}
      </div><div id="pt"></div>`,
      {badge: BADGE.estimated, hint: 'click any row for the full prompt, usage and advice',
       footer: 'Prompt text is read from your local transcripts and never leaves this machine.'})}`;
  const inp = $('#pq', page);
  let t; inp.oninput = e => { clearTimeout(t); const v = e.target.value;
    t = setTimeout(() => { S.promptQ = v; S.promptPage.offset = 0; bust(); render().then(() => {
      const i = $('#pq'); if (i) { i.focus(); i.setSelectionRange(v.length, v.length); } }); }, 300); };
  page.querySelectorAll('[data-po]').forEach(b => b.onclick = () => {
    S.promptOrder = b.dataset.po; S.promptPage.offset = 0; bust(); render(); });
  const promptMore = $('#prompt-more', page);
  if (promptMore) promptMore.onclick = () => { pp.offset += pp.limit; bust(); render(); };
  $('#pt', page).innerHTML = table([
    {h: 'When', f: r => `<span class="mono">${esc((r.ts || '').slice(0, 16).replace('T', ' '))}</span>`},
    {h: 'Prompt', trunc: 1, title: r => pt(r.preview), f: r => esc(pt(r.preview))},
    {h: 'Category', f: r => `<span class="pill" title="confidence ${(100*(r.category_confidence||0)).toFixed(0)}%${
      r.category_evidence?.length ? ' · matched: ' + esc(r.category_evidence.join(', ')) : ''}">${esc(r.category)}</span>`},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Session', trunc: 1, title: r => r.session_id,
     f: r => `<span class="mono sub">${esc((!MASKED && r.session_title) || shortId(r.session_id))}</span>`},
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
  const [lb, live] = await Promise.all([api('leaderboards', '&n=20'), liveSessions()]);
  const tab = S.rankTab || 'most_expensive';
  const isSess = tab === 'longest_sessions';
  const rows = lb[tab];
  page.innerHTML = card('Cost & efficiency leaderboards', `
    <div class="tabs">${RANK_TABS.map(([k, l]) =>
      `<button class="${tab === k ? 'on' : ''}" data-rt="${k}">${l}</button>`).join('')}</div>
    <div id="rt" style="margin-top:8px"></div>`,
    {badge: BADGE.estimated, hint: isSess
      ? 'duration is first to last message of the whole session, idle time included; tokens and cost are in range'
      : tab === 'cheapest' ? 'prompts with a cost; $0 runs on free or local models are left out'
      : 'every row is clickable'});
  page.querySelectorAll('[data-rt]').forEach(b => b.onclick = () => {
    S.rankTab = b.dataset.rt; render(); });
  const cols = isSess ? [
    {h: '#', num: 1, f: r => rows.indexOf(r) + 1},
    {h: 'Session', trunc: 1, f: r => esc(stitle(r))},
    {h: '', f: r => rowActs(r, live)},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Duration (whole session)', num: 1, f: r => dur(r.duration_s)},
    {h: 'Prompts', num: 1, f: r => fmtInt(r.prompts)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
    {h: 'Start', f: r => `<span class="mono">${esc((r.started_at || '').slice(0, 16).replace('T', ' '))}</span>`},
  ] : [
    {h: '#', num: 1, f: r => rows.indexOf(r) + 1},
    {h: 'Prompt', trunc: 1, title: r => pt(r.preview), f: r => esc(pt(r.preview))},
    {h: 'Category', f: r => `<span class="pill">${esc(r.category)}</span>`},
    {h: 'Models', f: r => (r.models || '').split(',').map(m =>
      `<span class="swatch" title="${esc(modelName(m))}" style="background:${modelColor(m)}"></span>`).join('')},
    {h: 'Tokens', num: 1, f: r => fmtNum(r.ptokens)},
    {h: 'Output', num: 1, f: r => fmtNum(r.output_tokens)},
    {h: 'Tok eff', num: 1, f: r => fmtPct(r.efficiency * 100, 2)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.pcost)},
    {h: 'Session', trunc: 1, f: r => esc((!MASKED && r.session_title) || shortId(r.session_id))},
    {h: '', f: r => rowActs(r, live)},
    {h: 'Date', f: r => esc(r.day)},
  ];
  $('#rt', page).innerHTML = table(cols, rows, {onRow: 1});
  wireTable($('#rt', page), rows, r => isSess ? openSession(r.session_id) : openPrompt(r.prompt_id));
  wireRowActs($('#rt', page));
  addChart(page, 'Top 12 prompts by estimated cost', el => C.barsH(el, {
    rows: (lb.most_expensive || []).slice(0, 12), label: r => clip(pt(r.preview), 46), value: r => r.pcost,
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
/* ---------- context & session hygiene: observed shares, no savings ---------- */
VIEWS.hygiene = async (page) => {
  const [hy, fit] = await Promise.all([api('hygiene'), api('context_window_fit')]);
  const T = hy.thresholds.map(String), kT = String(hy.rank_threshold);
  const K = t => `${Math.round(+t / 1000)}K`;
  const ab = t => hy.above[t];
  // Colour grade by how much of the spend went to re-sending a large prefix. The bands are
  // a reading aid, not a verdict: long agentic work legitimately runs at high context.
  const grade = (v, bands) => v == null ? null
    : v < bands[0] ? 'good' : v < bands[1] ? 'warning' : v < bands[2] ? 'serious' : 'critical';
  const SHARE = [25, 50, 75], FIT = [5, 15, 30];
  const bandTip = b => `Green under ${b[0]}%, amber ${b[0]}–${b[1]}%, orange ${b[1]}–${b[2]}%, red ${b[2]}% and up`;
  page.innerHTML = `
    <div class="note" style="margin:0 0 10px">Everything on this page is observed from your transcripts.
      It shows where spend sat while a large prefix was being re-sent on every turn. It does
      <b>not</b> estimate what /compact or a fresh session would have saved — that depends on
      what the work still needed, which the transcript does not say. Auto-compaction is not
      recorded; it is detected as the context dropping by more than half. A typed /compact is
      recorded and also counts.</div>
    <div class="grid g4">
      ${T.map(t => kpi(`Spend in requests ≥ ${K(t)} context`, fmtPct(ab(t).share_pct),
        `${fmtUSD(ab(t).cost_usd)} · ${fmtInt(ab(t).requests)} requests`,
        {badge: BADGE.actual, tone: grade(ab(t).share_pct, SHARE), title: bandTip(SHARE)})).join('')}
      ${T.map(t => kpi(`Spend after a session first crossed ${K(t)}`, fmtPct(ab(t).share_after_first_cross_pct),
        `${fmtInt(ab(t).sessions)} of ${fmtInt(hy.sessions)} sessions crossed it`,
        {badge: BADGE.actual, tone: grade(ab(t).share_after_first_cross_pct, SHARE), title: bandTip(SHARE)})).join('')}
    </div>
    <div class="grid g3">
      ${kpi('Spend near or over the context window', fmtPct(fit.near_or_over_cost_pct),
        `≥ ${fit.threshold_pct}% of the window in use · ${fmtInt(fit.near_requests + fit.over_requests)} requests, subagent turns included (they can fill their own window)`,
        {badge: BADGE.actual, tone: grade(fit.near_or_over_cost_pct, FIT), title: bandTip(FIT)})}
      ${kpi('Sessions in range', fmtInt(hy.sessions), `${fmtInt(hy.requests)} main-thread requests`, {badge: BADGE.actual, tone: 'info'})}
      ${kpi('Spend in range', fmtUSD(hy.cost_usd), 'subagent turns excluded (own prefix)', {badge: BADGE.estimated, tone: 'info'})}
    </div>
    ${card('Context per request, most expensive session after ' + K(kT), '<div class="chart" id="hy-traj"></div>',
      {badge: BADGE.actual, hint: 'prompt-side tokens on each request, in order · click a row below to change session'})}
    ${card('Sessions ranked by spend after crossing ' + K(kT), '<div id="hy-sess"></div>',
      {badge: BADGE.actual, flush: 1,
       footer: hy.note + ' Subagent turns are excluded because they run against their own prefix.'})}`;

  const drawTraj = s => {
    const rows = s.context_trajectory.map((c, i) => ({i: (s.trajectory_index || [])[i] || i + 1, ctx: c, cum: s.cumulative_cost[i]}));
    C.timeSeries($('#hy-traj', page), {rows, x: 'i', type: 'area', height: 210, fmt: fmtNum,
      series: [{key: 'ctx', label: 'Context tokens', color: seriesVar(0)}],
      xLabel: v => `#${v}`});
    $('#hy-traj', page).insertAdjacentHTML('beforeend',
      `<div class="note" style="margin-top:6px">${esc(stitle(s))} — ${fmtInt(s.requests)} requests,
       ${fmtUSD(s.cost_usd)}. Crossed ${K(kT)} at request #${s.ever_crossed[kT] ? s.first_cross_idx[kT] + 1 : '—'};
       ${fmtPct(s.cost_after_pct[kT])} of its spend came after that.</div>`);
  };
  const rows = hy.sessions_ranked;
  $('#hy-sess', page).innerHTML = table([
    {h: 'Session', trunc: 1, f: r => esc(stitle(r))},
    {h: 'Project', trunc: 1, f: r => esc(r.project || '')},
    {h: 'Context', f: r => `<span class="spk" data-i="${rows.indexOf(r)}" style="display:inline-block;width:110px"></span>`},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Peak ctx', num: 1, f: r => fmtNum(r.max_context)},
    ...T.map(t => ({h: `After ${K(t)}`, num: 1, f: r => !r.ever_crossed[t] ? '<span class="na">never</span>'
      : `<b>${fmtUSD(r.cost_after[t])}</b> <span class="note">(${fmtPct(r.cost_after_pct[t])})</span>`})),
    {h: 'Session cost', num: 1, f: r => fmtUSD(r.cost_usd)},
  ], rows);
  page.querySelectorAll('#hy-sess .spk').forEach(el => C.spark(el, rows[+el.dataset.i].context_trajectory, seriesVar(0), 22));
  page.querySelectorAll('#hy-sess table.tbl tbody tr').forEach(tr => {
    tr.classList.add('clickable');
    tr.onclick = e => { if (e.altKey) openSession(rows[+tr.dataset.i].session_id); else drawTraj(rows[+tr.dataset.i]); };
  });
  if (rows.length) drawTraj(rows[0]);
};

VIEWS.context = async (page) => {
  const [ctx, eff, ttl] = await Promise.all([api('context'), api('efficiency'), api('ttl_replay')]);
  const ca = eff.cache;
  const ttlCard = () => {
    if (!ttl || !ttl.segments) return '';
    if (!ttl.reconciled) {
      return card('Cache TTL comparison', `
        <p class="note" style="margin:0">Not shown. Replaying your actual 1h TTL produced
          ${fmtUSD(ttl.replay_1h_usd)} against ${fmtUSD(ttl.logged_cost_usd)} of logged cost
          (${ttl.reconciliation_drift_pct}% drift). A replay that cannot reproduce the bill you
          did get is not evidence about one you did not, so the comparison is withheld.</p>`,
        {badge: BADGE.actual, hint: 'arithmetic · failed its own reconciliation check'});
    }
    const cheaper5 = ttl.cheaper_ttl === '5m';
    return card('Cache TTL: what you use vs the alternative', `
      <p class="note" style="margin:0 0 10px">Replayed over <strong>${fmtInt(ttl.segments)}</strong>
        cache segments using your real inter-turn gaps. No assumption is made about model
        behaviour — this is arithmetic on timestamps and token counts.</p>
      <div class="grid g3" style="gap:8px">
        ${kpi('Your 1h TTL', fmtUSD(ttl.replay_1h_usd), 'replayed', {small: 1, badge: BADGE.actual})}
        ${kpi('Same work on a 5m TTL', fmtUSD(ttl.replay_5m_usd), 'counterfactual', {small: 1})}
        ${kpi(cheaper5 ? 'A 5m TTL would save' : 'Your 1h TTL saves',
          fmtUSD(Math.abs(ttl.difference_usd)), cheaper5 ? 'switch to save' : 'already the cheaper choice',
          {small: 1, badge: BADGE.actual})}
      </div>
      <div class="stack" style="margin-top:10px">
        <div class="item sev-${cheaper5 ? 'medium' : 'low'}">
          <div class="hd">${cheaper5
            ? 'A shorter TTL would be cheaper for how you actually work.'
            : 'Keep the 1h TTL — a 5m TTL would cost you more, not less.'}</div>
          <div class="dt">${cheaper5
            ? 'Your turns come close enough together that the prefix rarely expires, so you are paying the 2x write premium for protection you do not use.'
            : 'The gaps where a 5m prefix would expire force a full-prefix rewrite, and that costs more than the cheaper write rate saves.'}
            This only matters if the TTL is configurable in your setup.</div></div>
      </div>`,
      {badge: BADGE.actual,
       hint: 'arithmetic · reconciled to ' + ttl.reconciliation_drift_pct + '% of logged cost',
       footer: ttl.note});
  };
  page.innerHTML = `
    ${ttlCard()}
    <div class="grid g5">
      ${kpi('Avg context / request', fmtNum(ctx.avg_context), null, {badge: BADGE.actual})}
      ${kpi('Max context seen', fmtNum(ctx.max_context),
        ctx.typical_context_window ? `most requests ran on a ${fmtNum(ctx.typical_context_window)} window` : '')}
      ${kpi('Context utilization', ctx.context_utilization_pct == null ? null : fmtPct(ctx.context_utilization_pct),
        'average, against each request\'s own model window') }
      ${kpi('Tokens / request', fmtNum(eff.tokens_per_request))}
      ${kpi('Cache hit ratio', ca.reads ? fmtPct(eff.cache_hit_ratio * 100) : null,
        'reads ÷ (reads + writes), by token', {badge: BADGE.actual})}
    </div>
    ${(() => {
      const cs = ca.cost_split;
      if (!cs || !cs.read_cost_share) return '';
      const mult = cs.write_vs_read_multiple, h1 = cs.write_1h_token_share;
      return card('Cache reads vs writes, by cost', `
        <p class="note" style="margin:0 0 10px">Writes are
          <strong>${fmtPct(100 * (1 - cs.read_token_share))}</strong> of your cache tokens but
          <strong>${fmtPct(100 * cs.write_cost_share)}</strong> of your cache cost${
            mult ? `, because a write token costs ${mult.toFixed(1)}x a read token on average across your models` : ''}.
          The token ratio above is the flattering number; this is the one that moves the bill.</p>
        <dl class="kv">
          <dt>Cache reads</dt><dd>${fmtNum(cs.read_tokens)} tokens · ${fmtUSD(cs.read_cost_usd)}
            (${fmtPct(100 * cs.read_cost_share)} of cache cost)</dd>
          <dt>Cache writes</dt><dd>${fmtNum(cs.write_tokens)} tokens · ${fmtUSD(cs.write_cost_usd)}
            (${fmtPct(100 * cs.write_cost_share)} of cache cost)</dd>
          <dt>— 5m writes</dt><dd>${fmtNum(cs.write_5m_tokens)} tokens · ${fmtUSD(cs.write_5m_cost_usd)}</dd>
          <dt>— 1h writes</dt><dd>${fmtNum(cs.write_1h_tokens)} tokens · ${fmtUSD(cs.write_1h_cost_usd)}</dd>
        </dl>
        ${h1 !== null && h1 >= 0.5 && cs.write_tokens ? `<div class="stack" style="margin-top:10px">
          <div class="item sev-low">
            <div class="hd">${fmtPct(100 * h1)} of your cache writes use the 1h TTL.</div>
            <div class="dt">A 1h write costs more per token than a 5m one, but that does not make
              it the wrong choice — whether it pays off depends on your real inter-turn gaps.
              The TTL comparison above replays them and answers it with arithmetic rather than
              a rule of thumb.</div></div>
        </div>` : ''}`, {badge: BADGE.estimated,
        footer: 'Component costs are re-derived per model from config/pricing.json, since a request stores one blended cost.'});
    })()}
    ${ctx.large_context_cost_pct > 0 ? `<div class="hero">
      <h3>${I('alert')} Context warnings</h3>
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
          ${kpi('Uncached counterfactual', fmtUSD(ca.uncached_counterfactual_delta_usd),
            `${fmtPct(ca.uncached_counterfactual_pct)} · not a saving`, {small: 1, badge: BADGE.estimated})}
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
    {h: 'Session', trunc: 1, f: r => esc(stitle(r))},
    {h: 'Project', f: r => esc(r.project)},
    {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
    {h: 'Avg context', num: 1, f: r => fmtNum(r.avg_context)},
    {h: 'Peak context', num: 1, f: r => fmtNum(r.max_context)},
    {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
  ], ctx.heavy_sessions, {onRow: 1});
  wireTable($('#hs', page), ctx.heavy_sessions, r => openSession(r.session_id));
};

/* ---------- waste ---------- */
VIEWS.waste = async (page) => {
  const w = await api('waste');
  page.innerHTML = `
    <div class="grid g4">
      ${kpi('Estimated excess', fmtUSD(w.estimated_excess_usd),
        `${fmtPct(w.excess_pct)} of ${fmtUSD(w.total_cost_usd)}, the estimated waste`,
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
      reasonable baseline, de-duplicated across rules. Each rule below states the baseline it
      measures against.</div>
    ${['high', 'medium', 'low'].map(sev => {
      const list = w.findings.filter(f => f.severity === sev);
      if (!list.length) return '';
      const title = sev === 'high' ? 'High waste'
        : sev === 'medium' ? 'Optimization opportunities' : 'Low-priority observations';
      const icon = dot(sev === 'high' ? 'red' : sev === 'medium' ? 'yellow' : 'grey');
      return card(title, `<div class="stack">${list.map((f, i) => `
        <div class="item sev-${sev}">
          <div class="hd">${esc(f.title)}<span class="spacer"></span>
            <span style="font-variant-numeric:tabular-nums">${excessText(f)}</span>
            <span class="note" style="font-variant-numeric:tabular-nums">of ${fmtUSD(f.est_cost_usd)} exposed</span></div>
          <div class="dt">${esc(f.detail)}</div>
          <div class="dt"><b>Excess measured as:</b> ${esc(f.excess_basis)}</div>
          <div class="dt"><b>Recommended:</b> ${esc(f.recommended_action)}</div>
          <details style="margin-top:5px"><summary style="cursor:pointer;font-size:11.5px;color:var(--s1)">
            ${f.count > f.evidence.length ? `Show the ${f.evidence.length} costliest of ${fmtInt(f.count)} flagged items` : `Show ${f.evidence.length} flagged items`}</summary>
            <div class="ev" data-kind="${esc(f.kind)}" style="margin-top:6px"></div></details>
        </div>`).join('')}</div>`, {badge: BADGE.estimated, icon});
    }).join('')}
    <div class="note">${esc(w.note)}</div>`;
  // fill evidence tables
  const all = w.findings;
  page.querySelectorAll('.ev').forEach(host => {
    const f = all.find(x => x.kind === host.dataset.kind);
    const isSess = !!f.evidence[0]?.session_id && !f.evidence[0]?.prompt_id;
    const cols = isSess ? [
      {h: 'Session', trunc: 1, f: r => esc(stitle(r))},
      {h: 'Project', f: r => esc(r.project || '—')},
      {h: 'Requests', num: 1, f: r => fmtInt(r.requests)},
      {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens ?? r.reads)},
      {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
      {h: 'Est. excess', num: 1, f: r => r.excess > 0 ? fmtUSD(r.excess) : '—'},
    ] : [
      {h: 'Prompt', trunc: 1, title: r => pt(r.preview), f: r => esc(pt(r.preview))},
      {h: 'Detail', f: r => r.n ? `repeated ${r.n}×` : r.char_len ? fmtInt(r.char_len) + ' chars'
        : r.tools ? fmtInt(r.tools) + ' tool calls'
        : r.out_tokens != null ? fmtInt(r.out_tokens) + ' output tokens' : '—'},
      {h: 'Tokens', num: 1, f: r => fmtNum(r.tokens)},
      {h: 'Est. cost', num: 1, f: r => fmtUSD(r.cost)},
      {h: 'Est. excess', num: 1, f: r => r.excess > 0 ? fmtUSD(r.excess) : '—'},
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
    ${card('Sessions: main agent vs subagents', `<div id="at-sess"></div>`, {badge: BADGE.estimated, flush: 1, hint: 'Top 30 by tokens; click to drill in'})}
    ${card('Subagents by type', `<div id="at-types"></div>`, {badge: BADGE.estimated, flush: 1})}
    ${card('Most expensive subagent runs', `<div id="at-runs"></div>`, {badge: BADGE.estimated, flush: 1})}
    ${card('Skills', `<div id="at-skills"></div>`, {badge: BADGE.estimated, flush: 1, hint: 'Invoked by Claude via the Skill tool'})}
    ${card('Slash commands & user-invoked skills', `<div id="at-slash"></div>`, {badge: BADGE.estimated, flush: 1, hint: 'Whole turn attributed'})}
    ${card('MCP servers', `<div id="at-mcp"></div>${toolDetail(b.mcp)}`, {badge: BADGE.estimated, flush: 1})}
    ${card('Connectors (claude.ai)', `<div id="at-conn"></div>${toolDetail(b.connectors)}`, {badge: BADGE.estimated, flush: 1})}
    ${b.configured_unused_mcp.length ? `<div class="note">Configured but never called: <b>${esc(b.configured_unused_mcp.join(', '))}</b>. Their tool definitions still load into every session. Remove them with <code>claude mcp remove &lt;name&gt;</code>.</div>` : ''}`;
  const put = (id, cols, rows, onRow) => { const el = $(id, page); el.innerHTML = table(cols, rows, {onRow: !!onRow}); wireTable(el, rows, onRow); };
  put('#at-sess', [
    {h: 'Session', trunc: 1, title: r => r.session_id, f: r => esc(stitle(r))},
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
/* ---------- one running session's own token limit ---------- */
function limitBadge(sid) {
  const o = ((S.opts?.settings?.guard || {}).sessions || {})[sid];
  if (!o) return '';
  return o.off ? '<span class="pill" title="The session guard is off for this conversation">Guard off</span>'
    : `<span class="pill" data-limit-badge="${esc(sid)}" title="This conversation's own token limit">Limit ${esc(fmtNum(o.session_tokens))}</span>`;
}
// The Set limit panel: tokens used so far (as the guard counts them), the limit box with
// suggestions from that number, and save / remove / turn off.
async function drawLimit(box, x, i) {
  const sid = x.session_id;
  box.innerHTML = '<div class="note">Counting this conversation\'s tokens…</div>';
  let d;
  try {
    const r = await fetch(`/api/guard/session/${encodeURIComponent(sid)}`);
    d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Could not read this session.');
  } catch (e) { box.innerHTML = `<div class="live-msg err">${esc(e.message)}</div>`; return; }
  const own = d.limit, id = `lim-${i}`, used = d.tokens || 0;
  const now = own?.off ? 'The guard is off for this conversation.'
    : own ? `This conversation's limit: <b>${fmtNum(own.session_tokens)}</b> (${fmtPct(100 * used / own.session_tokens, 0)} used).`
    : d.applies ? `No limit of its own yet. It uses the per-session budget from Budgets: <b>${fmtNum(d.applies)}</b>.`
    : 'No limit yet.';
  box.innerHTML = `<div class="cfg">
    <div class="fld-hint">Used so far: <b>${fmtNum(used)}</b> tokens. This counts everything the conversation has used,
      including re-reading its context at every step, so it is much bigger than the context size above.</div>
    ${d.installed ? '' : `<div class="lb-state warn" style="margin-top:6px">Live warnings are not installed, so a limit
      here won't do anything yet. <button class="act" data-lim-setup>Set up live warnings</button></div>`}
    <div class="fld-hint" style="margin-top:6px">${now}</div>
    ${amountField({id, label: 'Token limit for this conversation', kind: 'tokens', value: own?.session_tokens ?? null,
      chips: tokChips([[used * 1.1, '+10%'], [used * 1.25, '+25%'], [used * 1.5, '+50%'], [used * 2, '2×']], 3)})}
    <div class="fld-hint">When this conversation reaches its limit, Claude Code asks you "continue?" before its next
      step. Your warn percentages from Budgets apply too. The limit is forgotten after 30 days.</div>
    <div class="live-actions">
      <button class="act" data-lim-save>Save limit</button>
      ${own ? '<button class="act ghost" data-lim-rm>Remove limit</button>' : ''}
      ${own?.off ? '' : '<button class="act ghost" data-lim-off>Turn the guard off for this conversation</button>'}
      <span class="live-msg" role="status"></span>
    </div></div>`;
  const inp = $('#' + id, box), msg = box.querySelector('.live-msg');
  box.querySelectorAll('[data-fill]').forEach(c => c.onclick = () => {
    inp.value = shortAmount(+c.dataset.v); inp.dispatchEvent(new Event('input')); });
  inp.addEventListener('input', () => clearErr(box, id));
  const setup = box.querySelector('[data-lim-setup]');
  if (setup) setup.onclick = () => go('budgets');
  const send = async (body, done) => {
    try {
      await doAction(`session_limit/${encodeURIComponent(sid)}`, body);
      S.opts = await fetch('/api/options').then(r => r.json());
      const hd = box.closest('.item')?.querySelector('.hd');
      hd?.querySelectorAll('.pill[title]').forEach(p => p.remove());
      hd?.querySelector('.spacer')?.insertAdjacentHTML('beforebegin', limitBadge(sid));
      await drawLimit(box, x, i);
      const m = box.querySelector('.live-msg'); if (m) { m.classList.add('ok'); m.textContent = done; }
    } catch (e) { msg.classList.add('err'); msg.textContent = e.message; }
  };
  box.querySelector('[data-lim-save]').onclick = () => {
    const e = amountError(inp) || (inp.value.trim() ? '' : 'Type a limit, or tap a suggestion.');
    if (e) return setErr(box, id, e);
    send({session_tokens: parseAmount(inp.value, 'tokens').value}, 'Saved. It applies from the next step.');
  };
  box.querySelector('[data-lim-rm]')?.addEventListener('click', () => send({remove: true}, 'Removed.'));
  box.querySelector('[data-lim-off]')?.addEventListener('click', () => send({off: true}, 'The guard is off for this conversation.'));
}

VIEWS.live = async (page) => {
  const [res, usage0] = await Promise.all([
    fetch('/api/live?agents=' + encodeURIComponent(S.filter.agents.join(','))).then(r => r.json()),   // never cached: always live
    fetch('/api/usage').then(r => r.json()).catch(e => ({ok: false, error: e.message}))]);
  const list = res.sessions || [];
  const liveActions = res.actions || ['interrupt', 'close', 'kill'];
  const canInterrupt = liveActions.includes('interrupt');
  const agentName = id => ((S.opts?.agents || []).find(a => a.id === id) || {}).name || id;
  const busy = list.filter(x => x.status === 'busy');
  const sevOf = x => x.severity === 'high' ? 'high' : x.severity === 'medium' ? 'medium' : 'low';
  page.innerHTML = `
    ${card('Plan usage right now', `<div id="live-usage">${usageBody(usage0)}</div>`,
      {badge: BADGE.actual, hint: 'Claude Code\'s own /usage: plan limits, not cost. Costs no tokens.',
       actions: `<button class="btn pb-copy" id="usage-refresh">${I('reload')} Refresh usage</button>`})}
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
      <span class="spacer"></span><button class="btn pb-copy" id="live-refresh">${I('reload')} Refresh</button></div>
    <div class="stack">${list.map((x, i) => `
      <div class="item sev-${sevOf(x)}" data-i="${i}">
        <div class="hd">${x.status === 'busy' ? '<span class="live-dot"></span>' : '<span class="idle-dot"></span>'}
          <span class="pill">${esc(agentName(x.agent))}</span>
          ${esc((!MASKED && x.name) || shortId(x.session_id))} <span class="note">· ${esc(x.project)}${x.pid ? ` · pid ${x.pid}` : ''}${x.model ? ` · ${esc(x.model)}` : ''}</span>
          ${x.hosts_dashboard ? '<span class="status high">runs this dashboard</span>' : ''}
          ${x.agent === 'claude' ? limitBadge(x.session_id) : ''}
          <span class="spacer"></span>
          <span style="font-variant-numeric:tabular-nums">${x.context == null ? 'context not recorded' : fmtNum(x.context) + ' context'} · ${fmtInt(x.steps)} steps${x.est_cost_usd == null ? '' : ' · ' + fmtUSD(x.est_cost_usd)}</span></div>
        <div class="dt">${x.status === 'busy' ? '<b>Working now</b>' : 'Idle'}${x.uptime ? ` · up ${esc(x.uptime)}` : ''} ·
          last activity ${x.last_write_s == null ? '—' : dur(x.last_write_s)} ago${x.memory_mb == null ? '' : ` · ${fmtInt(x.memory_mb)} MB`} ·
          <span class="note">${esc(x.cwd)}</span></div>
        ${x.severity !== 'ok' ? `<div class="dt"><b>Advice:</b> ${x.severity === 'high'
          ? 'Very large context. Use <b>Hand over</b> to continue in a fresh session, or split the remaining work into sub-sessions.'
          : 'Getting heavy. Hit <b>Compact</b> at the next break, or close it if the task is done.'}</div>` : ''}
        <div class="live-actions">
          ${x.signalable ? `${canInterrupt ? `<button class="act" data-a="interrupt" ${x.status !== 'busy' ? 'disabled title="Nothing running"' : ''}>${I('pause')} Interrupt</button>` : ''}
          ${x.agent === 'claude' ? '<button class="act" data-a="compact" title="Types /compact into that session\'s terminal">' + I('compact') + ' Compact</button>' : ''}
          <button class="act warn" data-a="close">${I('stop')} Close session</button>
          <button class="act danger" data-a="kill">${I('x')} Force kill</button>` : `<span class="note">${x.agent === 'cursor' ? 'Runs inside the Cursor IDE: stop it there.' : 'No matching process found: stop it in its terminal.'}</span>`}
          ${x.resume ? `<button class="act ghost" data-copy="${esc(x.resume)}">Copy resume command</button>` : ''}
          ${x.agent === 'claude' ? '<button class="act ghost" data-ho="1">' + I('handover') + ' Hand over / split</button>' : ''}
          ${x.agent === 'claude' && x.session_id ? '<button class="act ghost" data-lim="1">' + I('gauge') + ' Set limit</button>' : ''}
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
        ${x.agent === 'claude' && x.session_id ? `<div class="handover limitbox" hidden></div>` : ''}
      </div>`).join('') || `<div class="empty">No ${esc(agentWord())} sessions are running</div>`}</div>`;
  $('#live-refresh', page).onclick = () => render();
  const ub = $('#usage-refresh', page);
  ub.onclick = async () => {
    ub.disabled = true; ub.textContent = 'Reading…';
    try {
      const u = await fetch('/api/usage?refresh=1').then(r => r.json());
      $('#live-usage', page).innerHTML = usageBody(u);
    } catch (e) { $('#live-usage', page).innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
    ub.disabled = false; ub.innerHTML = `${I('reload')} Refresh usage`;
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
          `<div>${s.launched ? I('play') : '•'} ${esc(s.task || 'Continue the same work')} — <code>${esc(s.brief)}</code>${s.error ? ` <span class="live-msg err">${esc(s.error)}</span>` : ''}</div>`).join('')
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
      if (b.dataset.lim) {
        const box = row.querySelector('.limitbox');
        b.onclick = () => { box.hidden = !box.hidden; if (!box.hidden) drawLimit(box, x, +row.dataset.i); };
        return;
      }
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
  try { await navigator.clipboard.writeText(PB[+b.dataset.pb]); b.innerHTML = `Copied ${I('check')}`; }
  catch { b.textContent = 'Copy failed'; }
  setTimeout(() => b.textContent = 'Copy', 1500);
});
const HARNESS = {needed: [dot('red') + ' Needed', 'high'], partial: [dot('yellow') + ' Partial', 'medium'],
  in_place: [I('checkCircle') + ' In place', 'low'], not_needed: [dot('grey') + ' Not needed', 'low'], unknown: ['— Unknown', 'low']};
const harnessChip = h => h ? `<b>${HARNESS[h.verdict][0]}</b>` : '—';

VIEWS.diagnose = async (page) => {
  const d = await api('diagnose');
  const isCl = (d.agent || 'claude') === 'claude', md = d.vocab?.md || 'CLAUDE.md';
  const gdir = {claude: '~/.claude', codex: '~/.codex', gemini: '~/.gemini'}[d.agent || 'claude'];
  PB.length = 0;
  const t = d.total || {}, tr = d.trend || {};
  const sevChip = s => dot(s === 'high' ? 'red' : s === 'medium' ? 'yellow' : 'grey');
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
        <div class="hd">${dot(x.severity === 'high' ? 'red' : x.severity === 'medium' ? 'yellow' : 'green')}
          ${esc(stitle(x))} <span class="note">· ${esc(x.project)}</span><span class="spacer"></span>
          <span style="font-variant-numeric:tabular-nums">${fmtNum(x.context)} context · ${fmtInt(x.steps)} steps</span></div>
        <div class="dt">${esc(x.advice)} <span class="note">Last write ${x.idle_min} min ago; context grew
          ${fmtNum(x.start_context)} → ${fmtNum(x.context)}.</span></div>${pb(x.playbook)}</div>`).join('')}</div>`
      : '<div class="empty">No session written in the last 20 minutes</div>',
      {badge: BADGE.actual, hint: 'Read live from transcripts on each load'})}
    <div id="dx-past"></div>${card('Past sessions that carried too much context', `<div class="stack">${d.session_health.map(x => `
      <div class="item sev-${x.peak >= 300000 ? 'high' : 'medium'}">
        <div class="hd"><a href="#" class="sess-link" data-sess="${esc(x.session_id)}">${esc(stitle(x))}</a>
          <span class="note">· ${esc(x.project)}</span><span class="spacer"></span>
          <span style="font-variant-numeric:tabular-nums">peak ${fmtNum(x.peak)} · ${fmtUSD(x.cost)} ·
            ~${fmtNum(x.tokens_above_100k)} tokens re-read above 100K (not a saving)</span></div>
        ${x.fixes.map(t => `<div class="dt">• ${esc(t)}</div>`).join('')}${pb(x.playbook)}</div>`).join('')
      || '<div class="empty">No session passed 150K context</div>'}</div>`,
      {badge: BADGE.recommendation, hint: '"Above baseline" = context re-read beyond 100K'})}
    <div id="dx-mem"></div>${card(`Add to ${md}${isCl ? ' / memory' : ''} (from your past prompts)`, `<div class="stack">${d.memory_suggestions.map(x => `
      <div class="item sev-${x.kind === 'security' ? 'high' : x.already_saved ? 'low' : 'medium'}">
        <div class="hd">${I(x.kind === 'security' ? 'lock' : x.kind === 'template' ? 'cog' : x.kind === 'reference' ? 'link' : 'note')}
          ${esc(pt(x.text.slice(0, 140)))}${x.already_saved ? ' <span class="na">already saved</span>' : ''}
          <span class="spacer"></span><span class="note">${fmtInt(x.sessions)} sessions</span></div>
        <div class="dt">${esc(x.why)}</div>
        <div class="dt"><b>Put it in:</b> ${esc(x.target)}</div>
        ${(x.examples || []).length ? `<details><summary style="cursor:pointer;font-size:11.5px">Examples</summary>
          ${x.examples.map(e => `<div class="dt note">“${esc(pt(e))}”</div>`).join('')}</details>` : ''}${pb(x.playbook)}</div>`).join('')
      || '<div class="empty">No repeated instructions found</div>'}</div>`,
      {badge: BADGE.recommendation, hint: 'Mined from non-sandbox prompts; secrets masked'})}
    ${card('Why consumption is high', `<div class="stack">${d.drivers.map(x => `
      <div class="item"><div class="hd">${esc(x.title)}<span class="spacer"></span>
        <span style="font-variant-numeric:tabular-nums">${fmtPct(x.share_pct)} of tokens</span></div>
        <div class="dt">${esc(x.detail)}</div></div>`).join('')}</div>`,
      {badge: BADGE.actual, hint: 'Drivers overlap, so shares do not add up to 100%'})}
    <div id="dx-recs"></div>${card('What to change', `<div class="stack">${d.recommendations.map(r => `
      <div class="item sev-${r.priority === 1 ? 'high' : r.priority === 2 ? 'medium' : 'low'}">
        <div class="hd">${esc(r.title)}</div>
        <div class="dt"><b>Why:</b> ${esc(r.why)}</div>
        <div class="dt"><b>How:</b> ${esc(r.how)}</div>
        ${pb(r.playbook)}</div>`).join('')
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
    {h: 'Needs update', f: r => r.issues.length ? r.issues.map(i => sevChip(i.severity) + ' ' + esc(i.title)).join('<br>') : I('check')},
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
        <div class="hd">${I(x.severity === 'high' ? 'siren' : 'alert')} ${esc(x.title)}
          <span class="spacer"></span><span class="pill">${esc(x.type)}</span></div>
        <div class="dt">${esc(x.detail)}</div>
        <div class="mt"><span>observed ${x.type === 'session_outlier' ? fmtNum(x.metric_value) + ' tokens' : fmtUSD(x.metric_value)}</span>
          <span>baseline ${x.type === 'session_outlier' ? fmtNum(x.baseline) + ' tokens' : fmtUSD(x.baseline)}</span><span>ratio ${x.ratio}×</span>
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
    <span class="it"><span class="swatch" style="background:var(--critical)"></span>${I('siren')} Anomalous day</span>`;
  wireAnomalies(page, a.anomalies);
};

/* ---------- forecast ---------- */
VIEWS.forecast = async (page) => {
  const [f, burn] = await Promise.all([api('forecast'), api('burn')]);
  if (!f.available) { page.innerHTML = card('Forecast', `<div class="empty">${esc(f.message)}</div>`);
    return; }
  page.innerHTML = `${staleNote(f)}
    <div class="grid g4">
      ${kpi('End of billing period', fmtUSD(f.scenarios.expected.end_of_period_cost),
        `${f.remaining_days} days remaining`, {badge: BADGE.forecast})}
      ${kpi('A typical 30 days', fmtUSD(f.estimated_monthly_cost), 'at the recent daily mean', {badge: BADGE.forecast})}
      ${kpi('Period to date', fmtUSD(f.period_used), fmtNum(f.period_used_tokens) + ' tokens',
        {badge: BADGE.estimated})}
      ${kpi('Projected tokens', fmtNum(f.end_of_period_tokens), null, {badge: BADGE.forecast})}
      ${kpi('Daily mean ± σ', `${fmtUSD(f.daily_mean)} ± ${fmtUSD(f.daily_stdev)}`,
        `last ${f.window_days} calendar days, ${f.sample_days} with spend`, {small: 1})}
      ${f.limit_exhaustion_date === S.opts.unavailable_label
        ? kpi('Limit exhaustion date', null, 'Requires a configured allowance')
        : kpi('Limit exhaustion date', esc(f.limit_exhaustion_date),
            f.will_exceed ? dot('red') + ' forecast exceeds allowance' : dot('green') + ' within allowance', {badge: BADGE.forecast})}
    </div>
    ${card('Cumulative spend and forecast fan', f.insufficient_history
        ? '<div class="empty">Fewer than 7 priced days in the window: bands not shown.</div>'
        : '<div class="chart" id="fan2"></div><div class="legend" id="fl2"></div>',
      {badge: BADGE.forecast,
      hint: f.method,
      footer: 'Expected is the 14-calendar-day mean daily spend projected across the remaining days. Conservative and high add minus and plus one standard deviation × √(days remaining), the spread of a sum of independent days; the daily rate shown is what each scenario implies. Days you did not use Claude count as zero. They assume your recent pattern continues.'})}
    ${card('Scenarios', table([
      {h: 'Scenario', f: r => `<b>${esc(r[0])}</b>`},
      {h: 'Daily rate', num: 1, f: r => fmtUSD(r[1].daily_rate)},
      {h: 'Projected end of period', num: 1, f: r => fmtUSD(r[1].end_of_period_cost)},
      {h: 'vs today', num: 1, f: r => '+' + fmtUSD(r[1].end_of_period_cost - f.period_used)},
    ], Object.entries(f.scenarios)), {badge: BADGE.forecast})}`;
  if (!f.insufficient_history) {
    C.forecastFan($('#fan2', page), {history: burn.series, scenarios: f.scenarios,
      remainingDays: f.remaining_days, height: 300, nowLabel: f.unread_days ? 'last sync' : 'today'});
    $('#fl2', page).innerHTML = `<span class="it"><span class="swatch" style="background:var(--s1)"></span>
      Cumulative actual (estimated cost)</span>
      <span class="it"><span class="swatch" style="background:var(--s1);opacity:.35"></span>
      Forecast band (conservative → high)</span>`;
  }
};

/* ---------- budgets ---------- */
/* ---------- budgets form helpers ---------- */
// "20M", "500k", "$3,000", "2.5B" -> number; blank -> null. kind: 'tokens' | 'usd' | 'count'
function parseAmount(raw, kind) {
  const s = String(raw ?? '').trim().replace(/[,\s_]/g, '').replace(/^\$/, '');
  if (s === '') return {value: null};
  const m = /^(\d+(?:\.\d+)?|\.\d+)([kmb])?$/i.exec(s);
  if (!m) return {error: kind === 'usd' ? 'Enter an amount like 3000, $3,000 or 3k.'
                        : 'Enter a number like 20M, 500k or 2,000,000.'};
  const v = parseFloat(m[1]) * ({k: 1e3, m: 1e6, b: 1e9}[(m[2] || '').toLowerCase()] || 1);
  return {value: kind === 'usd' ? Math.round(v * 100) / 100 : Math.round(v)};
}
// Round up to `sig` significant figures (two by default), so suggestions are round numbers.
const niceUp = (v, sig = 2) => {
  if (!(v > 0)) return 0;
  const p = 10 ** (Math.floor(Math.log10(v)) - sig + 1);
  return Math.ceil(v / p - 1e-9) * p;
};
// 8400000 -> "8.4M": compact, and parseAmount reads it back exactly for round numbers.
function shortAmount(v) {
  const u = [[1e9, 'B'], [1e6, 'M'], [1e3, 'k']].find(([d]) => Math.abs(v) >= d);
  return u ? +(v / u[0]).toFixed(2) + u[1] : String(Math.round(v));
}
// items: a number, or [value, label, title]; data-driven ones are rounded up, zeros dropped
function suggChips(items, fmt, sig = 2) {
  const seen = new Set();
  return items.map(it => Array.isArray(it) ? {v: niceUp(it[0], sig), l: it[1], t: it[2]} : {v: it})
    .filter(c => c.v > 0 && !seen.has(c.v) && seen.add(c.v))
    .map(c => ({...c, l: c.l ? `${c.l} · ${fmt(c.v)}` : fmt(c.v)}));
}
const usdChips = items => suggChips(items, v => '$' + shortAmount(v));
const tokChips = (items, sig) => suggChips(items, shortAmount, sig);

function amountField({id, label, value, kind, zero, chips = [], hint = ''}) {
  const shown = value == null ? '' : kind === 'usd' ? String(value) : Number(value).toLocaleString('en-US');
  return `<div class="fld">
    <div class="hd"><label for="${id}">${label}</label></div>
    <input type="text" inputmode="decimal" id="${id}" data-kind="${kind}" ${zero ? 'data-zero="1"' : ''}
      value="${esc(shown)}" placeholder="not set" autocomplete="off" spellcheck="false"
      aria-describedby="${id}-hint ${id}-err">
    ${chips.length ? `<div class="sugg" aria-label="Suggestions">${chips.map(c =>
      `<button type="button" class="chip" data-fill="${id}" data-v="${c.v}" title="${esc(c.t || 'Use this value')}">${esc(c.l)}</button>`).join('')}</div>` : ''}
    <div class="fld-hint" id="${id}-hint">${hint}</div>
    <div class="fld-err" id="${id}-err" role="alert"></div>
  </div>`;
}
// The reason a typed amount is not acceptable, or '' when it is (blank is always fine).
function amountError(el) {
  const r = parseAmount(el.value, el.dataset.kind);
  if (r.error) return r.error;
  if (r.value == null) return '';
  if (r.value < 0) return 'Cannot be negative.';
  if (r.value === 0 && !el.dataset.zero) return 'Must be more than 0, or leave it blank.';
  return '';
}
// Toggle chips for a list of percentages, plus a box to add another one.
function pctField({id, label, values, presets, max}) {
  const all = [...new Set([...presets, ...values])].sort((a, b) => a - b);
  return `<div class="fld"><div class="hd">${label}</div>
    <div class="sugg" id="${id}" data-max="${max}">${all.map(p =>
      `<button type="button" class="chip ${values.includes(p) ? 'on' : ''}" data-pct="${p}" aria-pressed="${values.includes(p)}">${p}%</button>`).join('')}
      <input type="text" inputmode="decimal" class="pct-add" placeholder="+ other %" aria-label="Add another percentage"
        style="width:92px;padding:2px 8px;border-radius:99px">
    </div>
    <div class="fld-err" id="${id}-err" role="alert"></div></div>`;
}
function wirePct(page) {
  page.querySelectorAll('.cfg [data-max]').forEach(box => {
    const toggle = b => b.onclick = () => {
      b.classList.toggle('on'); b.setAttribute('aria-pressed', b.classList.contains('on'));
      clearErr(page, box.id);
    };
    box.querySelectorAll('[data-pct]').forEach(toggle);
    const add = box.querySelector('.pct-add');
    const commit = () => {
      const raw = add.value.trim().replace(/%$/, '');
      if (!raw) return;
      const v = +raw, max = +box.dataset.max;
      if (!Number.isFinite(v) || v < 1 || v > max) return setErr(page, box.id, `Enter a percentage from 1 to ${max}.`);
      let b = box.querySelector(`[data-pct="${v}"]`);
      if (!b) {
        b = h(`<button type="button" class="chip" data-pct="${v}" aria-pressed="false">${v}%</button>`);
        const after = [...box.querySelectorAll('[data-pct]')].find(x => +x.dataset.pct > v);
        box.insertBefore(b, after || add);
        toggle(b);
      }
      if (!b.classList.contains('on')) b.click();
      add.value = '';
      clearErr(page, box.id);
    };
    add.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); commit(); } };
    add.onblur = commit;
  });
}
function setErr(page, id, text) {
  const e = $('#' + id + '-err', page); if (e) e.textContent = text;
  const el = $('#' + id, page) || $('#' + id + '-tok', page);
  if (el && el.tagName !== 'DIV') el.classList.add('bad');
}
function clearErr(page, id) {
  const e = $('#' + id + '-err', page); if (e) e.textContent = '';
  [$('#' + id, page), $('#' + id + '-tok', page), $('#' + id + '-path', page)]
    .forEach(el => el && el.classList.remove('bad'));
}

// API keys: status only ever comes back from the server, never the key itself.
async function drawKeys(page) {
  const host = $('#cfg-keys', page);
  if (!host) return;
  let ks;
  try { ks = await fetch('/api/keys').then(r => r.json()); }
  catch (e) { host.innerHTML = '<div class="note">Could not read the key status.</div>'; return; }
  host.innerHTML = Object.entries(ks).map(([pid, k]) => `
    <div class="item">
      <div class="hd">${esc(k.name)}<span class="spacer"></span>
        <span class="badge ${k.source ? '' : 'na'}">${k.source === 'env' ? 'set by $' + esc(k.env)
          : k.source === 'stored' ? 'stored' + (k.last4 ? ' · ends ' + esc(k.last4) : '') : 'not set'}</span></div>
      <div class="dt"><b>Gives you:</b> ${esc(k.covers)}</div>
      <div class="dt"><b>Get a key:</b> ${esc(k.how)}</div>
      ${k.source === 'env'
        ? `<div class="fld-hint">The ${esc(k.env)} environment variable takes priority. Unset it to manage the key here.</div>`
        : `<div style="display:flex;gap:6px;align-items:center;margin-top:6px">
            <input type="password" id="key-${pid}" autocomplete="off" spellcheck="false" style="flex:1;min-width:0"
              aria-label="${esc(k.name)} key" placeholder="${k.source ? 'paste a new key to replace it' : 'paste the key'}">
            <button class="act" data-keysave="${pid}">Save key</button>
            ${k.source === 'stored' ? `<button class="act" data-keyrm="${pid}">Remove</button>` : ''}
          </div>`}
      <div class="fld-err" id="key-${pid}-err" role="alert"></div>
      <div class="fld-hint ok" id="key-${pid}-ok" role="status"></div>
    </div>`).join('');
  const done = async (pid, text) => {
    S.opts = await fetch('/api/options').then(r => r.json());
    applyAgentChrome();                    // Billed vs local appears once a key exists
    await drawKeys(page);
    const ok = $('#key-' + pid + '-ok', page); if (ok) ok.textContent = text;
  };
  host.querySelectorAll('[data-keysave]').forEach(b => {
    const pid = b.dataset.keysave, inp = $('#key-' + pid, host), err = $('#key-' + pid + '-err', host);
    inp.oninput = () => { err.textContent = ''; inp.classList.remove('bad'); };
    inp.onkeydown = e => { if (e.key === 'Enter') b.click(); };
    b.onclick = async () => {
      if (!inp.value.trim()) { err.textContent = 'Paste a key first.'; inp.classList.add('bad'); return; }
      b.disabled = true;
      try {
        await doAction('key/' + pid, {value: inp.value});
        inp.value = '';
        await done(pid, 'Saved. Billed vs local uses it on the next Refresh.');
      } catch (e) { err.textContent = e.message; inp.classList.add('bad'); b.disabled = false; }
    };
  });
  host.querySelectorAll('[data-keyrm]').forEach(b => b.onclick = async () => {
    if (!b.dataset.armed) {            // two clicks, like the other destructive actions
      b.dataset.armed = '1'; b.textContent = 'Click again to remove';
      setTimeout(() => { if (b.isConnected) { delete b.dataset.armed; b.textContent = 'Remove'; } }, 4000);
      return;
    }
    const pid = b.dataset.keyrm;
    try { await doAction('key/' + pid, {remove: true}); await done(pid, 'Removed.'); }
    catch (e) { const err = $('#key-' + pid + '-err', host); if (err) err.textContent = e.message; }
  });
}

/* ---------- guides: picture slides that explain a page ---------- */
const GUIDES = {};
let GUIDE = null;          // {name, i, opener, el}
const guideSeenKey = name => `finops.guide.${name}.seen`;

function openGuide(name, slideId, opener) {
  const slides = GUIDES[name];
  if (!slides || !slides.length) return;
  closeGuide();
  const el = document.createElement('div');
  el.className = 'guide-layer';
  el.innerHTML = `<div class="guide" role="dialog" aria-modal="true" aria-labelledby="guide-title" tabindex="-1"></div>`;
  document.body.appendChild(el);
  el.addEventListener('click', e => { if (e.target === el) closeGuide(); });
  GUIDE = {name, i: Math.max(0, slides.findIndex(s => s.id === slideId)), opener: opener || document.activeElement, el};
  document.addEventListener('keydown', guideKeys, true);
  try { localStorage.setItem(guideSeenKey(name), '1'); } catch (_) {}
  drawGuide();
}
function closeGuide() {
  if (!GUIDE) return;
  const {el, opener} = GUIDE;
  GUIDE = null;
  document.removeEventListener('keydown', guideKeys, true);
  el.remove();
  if (opener && opener.isConnected && opener.focus) opener.focus();
}
function guideKeys(e) {
  if (!GUIDE) return;
  e.stopPropagation();
  const slides = GUIDES[GUIDE.name];
  if (e.key === 'Escape') { e.preventDefault(); closeGuide(); }
  else if (e.key === 'ArrowRight' && GUIDE.i < slides.length - 1) { e.preventDefault(); GUIDE.i++; drawGuide(); }
  else if (e.key === 'ArrowLeft' && GUIDE.i > 0) { e.preventDefault(); GUIDE.i--; drawGuide(); }
  else if (e.key === 'Tab') {                       // keep focus inside the dialog
    const f = [...GUIDE.el.querySelectorAll('button:not([disabled]), a[href]')];
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
}
function drawGuide() {
  const slides = GUIDES[GUIDE.name], s = slides[GUIDE.i], n = slides.length, last = GUIDE.i === n - 1;
  const box = GUIDE.el.querySelector('.guide');
  box.innerHTML = `
    <header><span class="g-step">${GUIDE.i + 1} of ${n}</span>
      <h2 id="guide-title">${esc(s.title)}</h2>
      <button class="act g-x" data-g="close" aria-label="Close guide">${I('x')}</button></header>
    <div class="g-pic">${s.img
      ? `<span class="g-fig"><img src="${esc(s.img)}" alt="${esc(s.alt || '')}">${(s.marks || []).map(m =>
          `<span class="g-mark" style="left:${m.x}%;top:${m.y}%">${m.n}</span>`).join('')}</span>`
      : '<div class="g-ex">Example</div><div class="g-live"></div>'}</div>
    <div class="g-text" aria-live="polite">${s.text.map(t => `<p>${t}</p>`).join('')}</div>
    <footer>
      <div class="g-dots" aria-hidden="true">${slides.map((_, i) => `<i class="${i === GUIDE.i ? 'on' : ''}"></i>`).join('')}</div>
      <span class="spacer"></span>
      ${s.target ? '<button class="act" data-g="show">Show me on the page</button>' : ''}
      <button class="act" data-g="back" ${GUIDE.i ? '' : 'disabled'}>Back</button>
      <button class="chip on" data-g="${last ? 'close' : 'next'}">${last ? 'Done' : 'Next'}</button>
    </footer>`;
  if (s.live) s.live(box.querySelector('.g-live'));
  box.querySelectorAll('[data-g]').forEach(b => b.onclick = () => {
    const a = b.dataset.g;
    if (a === 'close') closeGuide();
    else if (a === 'next') { GUIDE.i++; drawGuide(); }
    else if (a === 'back') { GUIDE.i--; drawGuide(); }
    else if (a === 'show') { const t = s.target; closeGuide(); showMe(t); }
  });
  (box.querySelector('[data-g="next"], [data-g="close"]:not(.g-x)') || box).focus();
}
function showMe(selector) {
  const t = $(selector);
  if (!t) return;
  if (t.tagName === 'DETAILS') t.open = true;
  t.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  t.classList.remove('pulse'); void t.offsetWidth; t.classList.add('pulse');
  setTimeout(() => t.classList.remove('pulse'), 2200);
}
// First visit to a page with a guide: open it once. Never on top of the welcome tour.
function maybeGuide(name) {
  let seen = true;
  try { seen = localStorage.getItem(guideSeenKey(name)) === '1'; } catch (_) {}
  if (seen || !GUIDES[name]) return;
  const tryOpen = () => {
    if (S.view !== name || GUIDE) return;
    try { if (localStorage.getItem(guideSeenKey(name)) === '1') return; } catch (_) {}
    if (TOUR) return setTimeout(tryOpen, 800);     // wait for the welcome tour to finish
    openGuide(name);
  };
  setTimeout(tryOpen, 400);
}

// A frozen copy of a form block for a guide picture: demo values, no ids that clash.
function guideCopy(host, html) {
  host.innerHTML = `<div class="cfg">${html}</div>`;
  host.querySelectorAll('[id]').forEach(el => el.id = 'gcopy-' + el.id);
  host.querySelectorAll('[for]').forEach(el => el.setAttribute('for', 'gcopy-' + el.getAttribute('for')));
  host.querySelectorAll('[aria-describedby]').forEach(el => el.setAttribute('aria-describedby',
    el.getAttribute('aria-describedby').split(/\s+/).filter(Boolean).map(t => 'gcopy-' + t).join(' ')));
  host.querySelectorAll('input, select, button, textarea').forEach(el => { el.disabled = true; el.tabIndex = -1; });
}
const DEMO = {spend: 312, day: 14, busy: 29, tokens: 7.4e8, typical: 8.5e6, large: 3.3e7};

GUIDES.budgets = [
  {id: 'overview', title: 'What is this page?', img: 'guide/budgets.png',
   alt: 'The Budgets page: your results on one side, your limits on the other',
   marks: [{n: 1, x: 30, y: 8.5}, {n: 2, x: 96, y: 26}, {n: 3, x: 94, y: 51.5}],
   text: ['This page helps you stop spending too much on AI.',
          '<b>①</b> The results show how you are doing so far. <b>②</b> Here you set your money and token limits. <b>③</b> The session limit stops one conversation from getting too big. (On the real page the results are at the top and the limits are below them.)',
          'You only need to fill in what you care about. Everything else can stay empty.'],
   target: '[data-blk="money"]'},
  {id: 'money', title: '① Money limits', target: '[data-blk="money"]',
   live: h => guideCopy(h, `<div class="grid g2">
     ${amountField({id: 'b-monthly', label: 'Monthly budget (USD)', kind: 'usd', value: 400,
        chips: usdChips([[DEMO.spend, 'Last 30 days'], 250, 500])})}
     ${amountField({id: 'b-daily', label: 'Daily budget (USD)', kind: 'usd', value: null,
        chips: usdChips([[DEMO.day, 'Average day'], [DEMO.busy, 'Busy day']])})}</div>`),
   text: ['Type the most money you want to spend in a month, or in a day.',
          'Not sure? Tap a suggestion. They come from your own recent spending.',
          'We warn you as you get close. Leave a box empty if you don\'t need it.']},
  {id: 'tokens', title: '② Token limits', target: '[data-blk="tokens"]',
   live: h => guideCopy(h, amountField({id: 'b-tokens', label: 'Monthly token budget', kind: 'tokens',
     value: 9e8, chips: tokChips([[DEMO.tokens, 'Last 30 days'], 1e9, 5e9])})),
   text: ['A token is a small piece of text, about ¾ of a word. Claude counts all its work in tokens.',
          'Set how many tokens you want to use in a month. You can type short numbers like <b>900M</b> or <b>2B</b>.']},
  {id: 'session', title: '③ Session limit', target: '[data-blk="session"]',
   live: h => guideCopy(h, `<div class="grid g2">
     ${amountField({id: 'g-tokens', label: 'Per-session token budget', kind: 'tokens', value: 8e6,
        chips: tokChips([[DEMO.typical, 'Typical'], [DEMO.large, 'Large'], 5e6, 10e6])})}
     ${pctField({id: 'g-warn', label: 'Warn me at', values: [75, 80], presets: [50, 60, 70, 75, 80, 90], max: 99})}</div>`),
   text: ['A session is one Claude Code conversation.',
          'Long conversations cost more, because Claude re-reads everything each time.',
          'Set a limit for one conversation. Then pick when you want a warning, like at 75% and 80%.']},
  {id: 'after', title: 'After you say "continue", and project limits', target: '#g-projects',
   live: h => guideCopy(h, `<div class="grid g2">
     <div class="fld"><div class="hd">After I say "continue"</div>
       <label style="display:flex;gap:6px;margin:6px 0"><input type="radio" checked> Ask again every +25%</label>
       <label style="display:flex;gap:6px;margin:6px 0"><input type="radio"> Once per session</label></div>
     <div class="fld"><div class="hd">Different limit for a project</div>
       <div style="display:flex;gap:6px;align-items:center"><select><option>shop-app</option></select>
         <input type="text" value="15M" style="width:90px"><label><input type="checkbox"> off</label></div></div></div>`),
   text: ['When a conversation reaches its limit, you can let it keep going.',
          'Choose if we ask you again a bit later, or never again for that conversation.',
          'A big project can have its own, bigger limit. Or you can turn the limit off for it.']},
  {id: 'live', title: 'Live warnings (optional)', target: '#livebox',
   live: h => { h.innerHTML = `<div class="cfg"><div class="livebox"><div class="lb-head"><b>Live warnings in Claude Code</b>
       <span class="blk-sub">(optional)</span><span class="spacer"></span><span class="lb-state">○ Not installed</span>
       <button class="act" disabled>Install</button></div></div>
       <div class="g-cc">Session guard: this session has used 8.4M tokens, 105% of its 8M budget.
         Allow this tool call? <b>❯ Yes</b> &nbsp; No</div></div>`; },
   text: ['<b>You don\'t need this for your limits to work.</b>',
          'Install it if you want Claude Code itself to warn you while you work, and to ask "continue?" when a conversation reaches its limit. The dark box shows what that looks like.',
          'You can uninstall it any time.']},
  {id: 'advanced', title: 'Advanced (you can skip this)', target: '[data-blk="advanced"]',
   live: h => guideCopy(h, `<div class="grid g2">
     ${amountField({id: 'l-cost', label: 'Monthly cost allowance (USD)', kind: 'usd', zero: 1, value: null})}
     ${pctField({id: 't-thr', label: 'Warn when a budget reaches', values: [50, 75, 90, 100], presets: [25, 50, 75, 90, 100, 110], max: 1000})}</div>`),
   text: ['<b>Plan limits:</b> only fill these in if you know your plan\'s real numbers.',
          '<b>Alert thresholds:</b> when a money or token limit reaches these percentages, it gets marked.',
          'Most people never need to change these.']},
  {id: 'save', title: 'Save, then check your results', target: '#savecfg',
   live: h => { h.innerHTML = `<div class="cfg"><div class="save-row"><button class="chip on" disabled>Save</button>
       <span class="ok">Saved.</span></div>
       <div class="meter high" style="margin:8px 0"><i style="width:82%"></i></div>
       <div class="fld-hint">Monthly spend · 82% used</div>
       <div style="margin-top:8px">${dot('yellow')} a conversation at 75% of its limit &nbsp; ${dot('red')} over its limit</div></div>`; },
   text: ['Press <b>Save</b>. Your limits are kept on this computer.',
          'The top of this page then shows how you are doing against each limit.',
          'On the <b>Sessions</b> page, amber and red dots show conversations near or over their limit.']},
];


VIEWS.budgets = async (page) => {
  const b = await api('budgets');
  const st = S.opts.settings;
  const sg = st.guard || {};
  // suggestions from your own recent usage (see Analytics.budget_suggestions)
  const SG = b.suggest || {}, sizes = SG.session_tokens || [];
  const pctile = q => sizes.length ? sizes[Math.min(sizes.length - 1, Math.floor(q * sizes.length))] : 0;
  const overShare = v => {
    let lo = 0, hi = sizes.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (sizes[m] > v) hi = m; else lo = m + 1; }
    const n = sizes.length - lo;
    return n ? `${fmtInt(n)} of your ${fmtInt(sizes.length)} past sessions (${fmtPct(100 * n / sizes.length, 0)}) went over this.`
      : `None of your ${fmtInt(sizes.length)} past sessions went over this.`;
  };
  const HINTS = {
    'b-monthly': v => v ? `At your last-30-days pace (${fmtUSD(SG.spend_30d)}) you would use ${fmtPct(100 * SG.spend_30d / v, 0)} of this.`
      : `Last 30 days: ${fmtUSD(SG.spend_30d)} (estimated).`,
    'b-daily': () => `Your average day is ${fmtUSD(SG.daily_avg)}; 1 day in 10 costs more than ${fmtUSD(SG.daily_p90)}.`,
    'b-tokens': v => v ? `The last 30 days used ${fmtNum(SG.tokens_30d)} tokens, ${fmtPct(100 * SG.tokens_30d / v, 0)} of this.`
      : `Last 30 days: ${fmtNum(SG.tokens_30d)} tokens.`,
    'g-tokens': v => !sizes.length ? '' : v ? overShare(v) : `Half your sessions stay under ${fmtNum(pctile(.5))} tokens.`,
  };
  const planHint = `Your own figure, from your plan or invoice. ${esc(agentWord())} data does not include it.`;
  const stepVal = sg.step_pct ?? 25;
  // Does any per-session budget exist (global or a project override)? The hook needs one.
  const anyBudget = !!sg.session_tokens || [sg.projects, sg.sessions].some(m => Object.values(m || {}).some(o => o.session_tokens));
  const liveBox = () => {
    const on = !!S.opts.guard_installed;
    const state = !on ? `<span class="lb-state">○ Not installed</span>`
      : anyBudget ? `<span class="lb-state on">● Installed · works in new Claude Code sessions</span>`
      : `<span class="lb-state warn">Installed, but it does nothing until you set a per-session token budget above.</span>`;
    return `<div class="livebox" id="livebox">
      <div class="lb-head"><b>Live warnings in Claude Code</b> <span class="blk-sub">(optional)</span>
        <span class="spacer"></span>${state}
        <button class="act" id="g-install">${on ? 'Uninstall' : 'Install'}</button></div>
      <p>Your limits above already work without this. Install it if you want Claude Code itself to warn
        you at your warn percentages, and ask "continue?" when a conversation reaches its limit.</p>
      <p class="blk-sub">It is a small helper that runs inside Claude Code. Needs: a per-session token budget.
        It cannot end a conversation, and if it ever fails it lets Claude carry on. Undo any time.</p>
      <div class="fld-hint" id="g-msg" role="status"></div></div>`;
  };
  page.innerHTML = `
    ${card('Budget vs actual vs forecast', `<p class="blk-intro" style="margin-top:0">How you're doing against your limits. Set your limits below.</p><div class="stack">${b.lines.map(l => l.configured ? `
      <div class="item">
        <div class="hd">${esc(l.name)}<span class="spacer"></span>${statusChip(l.status)}</div>
        <div class="grid g4" style="gap:8px;margin:4px 0">
          ${kpi('Budget', l.unit === 'tokens' ? fmtNum(l.budget) : fmtUSD(l.budget), null, {small: 1})}
          ${kpi('Actual', l.unit === 'tokens' ? fmtNum(l.actual) : fmtUSD(l.actual),
            fmtPct(l.used_pct) + ' used', {small: 1, badge: BADGE.estimated})}
          ${kpi('Forecast', l.sessions_over != null ? '—' : l.forecast == null ? null
            : (l.unit === 'tokens' ? fmtNum(l.forecast) : fmtUSD(l.forecast)),
            l.sessions_over != null ? 'not forecast per session'
              : l.forecast_pct == null ? '' : fmtPct(l.forecast_pct) + ' of budget',
            {small: 1, badge: BADGE.forecast})}
          ${kpi('Variance', l.sessions_over != null ? '—' : l.variance == null ? null
            : (l.variance >= 0 ? '+' : '') + (l.unit === 'tokens' ? fmtNum(l.variance) : fmtUSD(l.variance)),
            l.variance == null ? '' : (l.variance > 0 ? 'over budget' : 'under budget'), {small: 1})}
        </div>
        <div class="meter ${l.status}"><i style="width:${Math.min(l.used_pct, 100)}%"></i></div>
        <div class="mt">
          ${l.thresholds_breached.length ? `<span>${I('alert')} breached ${l.thresholds_breached.join('%, ')}%</span>` : '<span>No threshold breached</span>'}
          ${l.thresholds_forecast_breach.length
            ? `<span style="color:var(--serious-ink)">forecast to breach ${l.thresholds_forecast_breach.join('%, ')}%</span>` : ''}
          ${l.sessions_over == null ? '' : `<span title="Most of a long session's tokens are cache reads, priced at about a tenth of the input rate.">
            ${l.sessions_over ? `<a href="#" data-over="1">${fmtInt(l.sessions_over)} session${l.sessions_over === 1 ? '' : 's'} over budget this period</a>`
              : 'No session over budget this period'} · largest session shown</span>`}
        </div></div>`
      : `<div class="item"><div class="hd">${esc(l.name)}<span class="spacer"></span>
          <span class="badge na">not configured</span></div>
          <div class="dt">Actual to date: ${l.unit === 'tokens' ? fmtNum(l.actual) : fmtUSD(l.actual)}
            (estimated). Set a budget below to track variance and get threshold warnings.</div></div>`
      ).join('')}</div>`, {badge: BADGE.estimated,
      hint: `alert thresholds: ${b.thresholds_pct.join('%, ')}%`})}
    ${card('Set your limits', `<div class="cfg">
      <section class="blk" data-blk="money">
        <h4><span class="num">①</span> Money limits <button type="button" class="blk-help" data-guide="money" aria-label="Help: money limits">?</button></h4>
        <p class="blk-intro">Set the most money you want to spend. We warn you before you go over.</p>
        <div class="grid g2">
          ${amountField({id: 'b-monthly', label: 'Monthly budget (USD)', kind: 'usd', value: st.budgets.monthly_usd,
            chips: usdChips([[SG.spend_30d, 'Last 30 days', 'Your estimated spend over the last 30 days, rounded up'],
                             [SG.spend_30d * 1.1, 'Last 30 days +10%', 'Some headroom over your recent spend'],
                             1000, 2500, 5000])})}
          ${amountField({id: 'b-daily', label: 'Daily budget (USD)', kind: 'usd', value: st.budgets.daily_usd,
            chips: usdChips([[SG.daily_avg, 'Average day', 'Your average day over the last 30 days, rounded up'],
                             [SG.daily_p90, 'Busy day', '1 day in 10 costs more than this'], 50, 100, 250])})}
        </div>
      </section>
      <section class="blk" data-blk="tokens">
        <h4><span class="num">②</span> Token limits <button type="button" class="blk-help" data-guide="tokens" aria-label="Help: token limits">?</button></h4>
        <p class="blk-intro">Tokens are the small pieces of text Claude reads and writes (about ¾ of a word).
          Set how many you want to use in a month.</p>
        <div class="grid g2">
          ${amountField({id: 'b-tokens', label: 'Monthly token budget', kind: 'tokens', value: st.budgets.monthly_tokens,
            chips: tokChips([[SG.tokens_30d, 'Last 30 days', 'Billable tokens over the last 30 days, rounded up'],
                             1e9, 5e9, 10e9])})}
          <div></div>
        </div>
      </section>
      <section class="blk" data-blk="session">
        <h4><span class="num">③</span> Session limit <button type="button" class="blk-help" data-guide="session" aria-label="Help: session limit">?</button></h4>
        <p class="blk-intro">A session is one Claude Code conversation. Stop one conversation from getting too big.</p>
        <div class="grid g3">
        <div>
          ${amountField({id: 'g-tokens', label: 'Per-session token budget', kind: 'tokens', value: sg.session_tokens,
            chips: tokChips([[pctile(.75), 'Typical', '3 in 4 of your sessions stay under this'],
                             [pctile(.9), 'Large', '9 in 10 of your sessions stay under this'],
                             5e6, 10e6, 25e6, 50e6])})}
          ${pctField({id: 'g-warn', label: 'Warn me at', values: sg.warn_pct || [], presets: [50, 60, 70, 75, 80, 90], max: 99})}
        </div>
        <div>
          <div class="fld"><div class="hd">After I say "continue"</div>
            <label style="display:flex;gap:6px;align-items:center;margin:6px 0;flex-wrap:wrap">
              <input type="radio" name="g-after" value="step" ${sg.after_approval !== 'once' ? 'checked' : ''}>
              Ask again every
              <select id="g-step-sel" style="width:auto">
                ${[10, 25, 50, 100].map(v => `<option value="${v}" ${stepVal === v ? 'selected' : ''}>+${v}%</option>`).join('')}
                <option value="other" ${[10, 25, 50, 100].includes(stepVal) ? '' : 'selected'}>other…</option>
              </select>
              <input type="text" inputmode="decimal" id="g-step" value="${stepVal}" placeholder="%"
                style="width:64px;${[10, 25, 50, 100].includes(stepVal) ? 'display:none' : ''}"></label>
            <label style="display:flex;gap:6px;align-items:center;margin:6px 0">
              <input type="radio" name="g-after" value="once" ${sg.after_approval === 'once' ? 'checked' : ''}>
              Once per session</label>
            <div class="fld-err" id="g-step-err" role="alert"></div>
          </div>
        </div>
        <div>
          <div class="fld"><div class="hd">Different limit for a project</div>
            <div id="g-projects"></div>
            <datalist id="gtok-list">${[pctile(.75), pctile(.9), 5e6, 10e6, 25e6, 50e6].filter(v => v > 0)
              .map(v => `<option value="${shortAmount(niceUp(v))}">`).join('')}</datalist>
            <button class="act" id="g-add" style="margin-top:6px">${I('plus')} Add override</button>
          </div>
        </div>
        </div>
        ${liveBox()}
      </section>
      <details class="blk adv" data-blk="advanced">
        <summary><h4>Advanced: plan limits and alert thresholds <span class="blk-sub">(most people can skip this)</span>
          <button type="button" class="blk-help" data-guide="advanced" aria-label="Help: advanced">?</button></h4></summary>
        <div class="grid g2">
          <div><div class="sec">Plan limits</div>
          <div class="fld-hint" style="margin:-2px 2px 6px">${planHint}</div>
          ${amountField({id: 'l-cost', label: 'Monthly cost allowance (USD)', kind: 'usd', zero: 1,
            value: st.limits.monthly_cost_allowance_usd})}
          ${amountField({id: 'l-tok', label: 'Monthly token allowance', kind: 'tokens', zero: 1,
            value: st.limits.monthly_token_allowance})}
          ${amountField({id: 'l-req', label: 'Monthly request allowance', kind: 'count', zero: 1,
            value: st.limits.monthly_request_allowance})}
          ${amountField({id: 'l-cred', label: 'Remaining credits (USD)', kind: 'usd', zero: 1,
            value: st.limits.remaining_credits_usd})}
          </div>
          <div><div class="sec">Alert thresholds</div>
            <p class="blk-intro">When a money or token limit reaches these percentages, the dashboard marks it.</p>
          ${pctField({id: 't-thr', label: 'Warn when a budget reaches', values: st.alert_thresholds_pct,
            presets: [25, 50, 75, 90, 100, 110], max: 1000})}
          </div>
        </div>
      </details>
      <div class="save-row">
        <button class="chip on" id="savecfg">Save</button><span id="cfg-msg" role="status"></span>
        <span class="fld-hint">Saved on this computer only. Amounts take shorthand: 20M, 500k, $3,000.</span>
      </div>
    </div>`, {actions: `<button class="act" id="guide-open">${I('help')} How does this work?</button>`})}`;
  // amounts: chips fill the field; hints follow what is typed; errors clear as you edit
  page.querySelectorAll('.cfg [data-fill]').forEach(c => c.onclick = () => {
    const el = $('#' + c.dataset.fill, page);
    el.value = el.dataset.kind === 'usd' ? String(+c.dataset.v) : shortAmount(+c.dataset.v);
    el.dispatchEvent(new Event('input'));
  });
  page.querySelectorAll('.cfg input[data-kind]').forEach(el => {
    const upd = () => {
      clearErr(page, el.id);
      const hint = HINTS[el.id], h = $('#' + el.id + '-hint', page);
      if (hint && h) { const r = parseAmount(el.value, el.dataset.kind); h.textContent = r.error ? '' : hint(r.value); }
    };
    el.addEventListener('input', upd);
    el.addEventListener('blur', () => { const e = amountError(el); if (e) setErr(page, el.id, e); });
    upd();
  });
  wirePct(page);
  const stepSel = $('#g-step-sel', page), stepIn = $('#g-step', page);
  stepSel.onchange = () => {
    stepIn.style.display = stepSel.value === 'other' ? '' : 'none';
    if (stepSel.value !== 'other') stepIn.value = stepSel.value; else stepIn.focus();
    clearErr(page, 'g-step');
  };
  // project overrides: [{path, off, tokens}], edited in place, saved with the rest
  const gp = Object.entries(sg.projects || {}).map(([path, o]) =>
    ({path, off: !!o.off, tokens: o.session_tokens == null ? '' : o.session_tokens.toLocaleString('en-US')}));
  const known = [...new Map((S.opts.projects || [])
    .filter(p => p.path && (p.agent || 'claude') === 'claude' && !p.is_sandbox)
    .map(p => [p.path, p])).values()].sort((a, b) => a.name.localeCompare(b.name));
  const drawProjects = () => {
    $('#g-projects', page).innerHTML = gp.length ? gp.map((o, i) => `
      <div style="display:flex;gap:6px;align-items:center;margin:4px 0">
        <select data-gp="${i}" data-k="path" id="gp-${i}-path" style="flex:1;min-width:0">
          <option value="">choose a project</option>
          ${[...new Set([o.path, ...known.map(p => p.path)].filter(Boolean))].map(path => {
            const p = known.find(k => k.path === path);
            return `<option value="${esc(path)}" ${path === o.path ? 'selected' : ''} title="${esc(path)}">${esc(p ? p.name : path)}</option>`;
          }).join('')}
        </select>
        <input type="text" inputmode="decimal" list="gtok-list" data-gp="${i}" data-k="tokens" id="gp-${i}-tok"
          value="${esc(o.tokens)}" placeholder="budget, e.g. 20M" style="width:130px;flex:none" ${o.off ? 'disabled' : ''}>
        <label style="display:flex;gap:3px;align-items:center"><input type="checkbox" data-gp="${i}" data-k="off" ${o.off ? 'checked' : ''}>off</label>
        <button class="act" data-gp-rm="${i}" title="Remove override" aria-label="Remove override">${I('x')}</button>
      </div>
      <div class="fld-err" id="gp-${i}-err" role="alert"></div>`).join('')
      : '<div class="fld-hint">None: every project uses the per-session budget.</div>';
    page.querySelectorAll('[data-gp]').forEach(el => el[el.type === 'text' ? 'oninput' : 'onchange'] = () => {
      const o = gp[+el.dataset.gp], k = el.dataset.k;
      o[k] = k === 'off' ? el.checked : el.value;
      clearErr(page, `gp-${el.dataset.gp}`);
      if (k === 'off') drawProjects();
    });
    page.querySelectorAll('[data-gp-rm]').forEach(b => b.onclick = () => { gp.splice(+b.dataset.gpRm, 1); drawProjects(); });
  };
  drawProjects();
  $('#g-add', page).onclick = () => { gp.push({path: '', off: false, tokens: ''}); drawProjects(); };
  $('#g-install', page).onclick = async () => {
    const msg = $('#g-msg', page);
    try {
      const r = await doAction('guard', {remove: !!S.opts.guard_installed});
      S.opts = await fetch('/api/options').then(r => r.json());
      bust(); await render();
      const m = $('#g-msg'); if (m) m.textContent = r.message;
    } catch (e) { msg.textContent = e.message; }
  };
  page.querySelectorAll('[data-over]').forEach(a => a.onclick = e => {
    e.preventDefault(); S.sessOrder = 'tokens'; S.sessPage.offset = 0; bust(); go('sessions'); });
  $('#savecfg', page).onclick = async () => {
    const msg = $('#cfg-msg', page);
    msg.className = ''; msg.textContent = '';
    page.querySelectorAll('.cfg .fld-err').forEach(e => { if (!e.id.startsWith('key-')) e.textContent = ''; });
    page.querySelectorAll('.cfg .bad').forEach(e => e.classList.remove('bad'));
    const errs = [];
    const amt = id => { const el = $('#' + id, page), e = amountError(el); if (e) errs.push([id, e]);
      return e ? null : parseAmount(el.value, el.dataset.kind).value; };
    const pcts = id => [...page.querySelectorAll(`#${id} [data-pct].on`)].map(b => +b.dataset.pct).sort((a, b) => a - b);
    const budgets = {monthly_usd: amt('b-monthly'), daily_usd: amt('b-daily'), monthly_tokens: amt('b-tokens')};
    const limits = {monthly_cost_allowance_usd: amt('l-cost'), monthly_token_allowance: amt('l-tok'),
                    monthly_request_allowance: amt('l-req'), remaining_credits_usd: amt('l-cred')};
    const thr = pcts('t-thr');
    if (!thr.length) errs.push(['t-thr', 'Pick at least one percentage.']);
    const after = (page.querySelector('[name=g-after]:checked') || {}).value || 'step';
    let step = stepSel.value === 'other' ? parseAmount(stepIn.value, 'count').value : +stepSel.value;
    if (after === 'step' && !(step >= 1 && step <= 1000)) errs.push(['g-step', 'Enter a step between 1 and 1000%.']);
    if (!(step >= 1 && step <= 1000)) step = 25;
    const projects = {};
    gp.forEach((o, i) => {
      const id = `gp-${i}`;
      if (!o.path) return errs.push([id, 'Pick a project, or remove this row.']);
      if (projects[o.path]) return errs.push([id, 'This project already has an override above.']);
      if (o.off) return (projects[o.path] = {off: true});
      const r = parseAmount(o.tokens, 'tokens');
      if (r.error) return errs.push([id, r.error]);
      if (!(r.value > 0)) return errs.push([id, 'Enter a budget, or tick off.']);
      projects[o.path] = {session_tokens: r.value};
    });
    const guard = {session_tokens: amt('g-tokens'), warn_pct: pcts('g-warn'), after_approval: after,
                   step_pct: step, projects};
    if (errs.length) {
      errs.forEach(([id, e]) => setErr(page, id, e));
      page.querySelectorAll('details.adv').forEach(d => { if (d.querySelector('.fld-err:not(:empty)')) d.open = true; });
      msg.className = 'err';
      msg.textContent = `Fix ${errs.length} field${errs.length === 1 ? '' : 's'} before saving.`;
      const first = $('#' + errs[0][0], page) || $('#' + errs[0][0] + '-err', page);
      if (first) { first.scrollIntoView({block: 'center', behavior: 'smooth'}); first.focus && first.focus(); }
      return;
    }
    let res;
    try {
      res = await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-FinOps-Action': '1'},
        body: JSON.stringify({budgets, limits, alert_thresholds_pct: thr, guard})}).then(r => r.json());
    } catch (e) { res = {error: 'Could not reach the dashboard server. Is it still running?'}; }
    if (res.error) { msg.className = 'err'; msg.textContent = 'Not saved: ' + res.error; return; }
    S.opts = await fetch('/api/options').then(r => r.json());
    bust(); await render();
    const m = $('#cfg-msg'); if (m) { m.className = 'ok'; m.textContent = 'Saved.'; }
  };
  const bl = b.lines.filter(l => l.configured && l.budget);
  if (bl.length) addChart(page, 'Budget used', el => C.barsH(el, {
    rows: bl, label: l => l.name, value: l => 100 * l.actual / l.budget, fmt: v => fmtPct(v), max: 100,
    color: l => l.actual >= l.budget ? 'var(--critical)' : l.actual >= 0.75 * l.budget ? 'var(--warning)' : seriesVar(2)}),
    {badge: BADGE.estimated, after: '.nothing'});
  $('#guide-open', page).onclick = e => openGuide('budgets', null, e.currentTarget);
  page.querySelectorAll('[data-guide]').forEach(b => b.onclick = e => {
    e.preventDefault(); e.stopPropagation();          // inside <summary>: don't toggle Advanced
    openGuide('budgets', b.dataset.guide, b);
  });
  maybeGuide('budgets');
};

/* ---------- settings ---------- */
VIEWS.settings = async (page) => {
  page.innerHTML = card('API keys', `<div class="cfg">
      <div class="fld-hint" style="margin-bottom:8px">An API key is a password that lets this dashboard ask
        Anthropic or Cursor for your company's bill. You only need one for the <b>Billed vs local</b> page,
        which compares what was billed with what this computer recorded. That page shows up in the sidebar
        once a key is saved. Skip this if you don't use it. Keys stay on this computer, in a file only you
        can read. They are never shown again.</div>
      <div id="cfg-keys" class="stack"><div class="note">Loading…</div></div></div>`,
    {hint: 'optional'});
  drawKeys(page);
};

/* ---------- scorecard ---------- */
VIEWS.scorecard = async (page) => {
  const [sc, advisor] = await Promise.all([api('scorecard'), api('advisor')]);
  page.innerHTML = `
    ${card('AI FinOps Score', `<div class="scorewrap">
      <div style="text-align:center">
        <div class="scorenum">${sc.score}</div>
        <div class="scoregrade">out of 100</div>
        <div class="chart" id="sg" style="width:190px;margin-top:6px"></div></div>
      <div style="flex:1;min-width:280px" class="stack">${sc.dimensions.map(d => `
        <div><div style="display:flex;justify-content:space-between;font-size:12px">
          <span><b>${esc(d.name)}</b> <span class="note">weight ${d.weight}</span></span>
          <span style="font-variant-numeric:tabular-nums;font-weight:640">${d.score}</span></div>
        <div class="meter ${d.score >= 75 ? 'healthy' : d.score >= 50 ? 'high'
          : d.score >= 30 ? 'approaching' : 'critical'}"><i style="width:${d.score}%"></i></div>
        <div class="note">${esc(d.detail)}</div></div>`).join('')}
      </div></div>`, {badge: BADGE.estimated,
      footer: 'Each dimension is measured from your transcripts; budget adherence needs a budget on the Budgets page.'})}
    <div class="grid g3">
      ${card('What is good', `<ul style="margin:0 0 0 18px;font-size:12.5px">${
        sc.what_is_good.map(x => `<li style="margin-bottom:6px">${esc(x)}</li>`).join('')
        || '<li class="na">Nothing scored above 60 in this range</li>'}</ul>`, {icon: I('checkCircle')})}
      ${card('Needs attention', `<ul style="margin:0 0 0 18px;font-size:12.5px">${
        sc.needs_attention.map(x => `<li style="margin-bottom:6px">${esc(x)}</li>`).join('')
        || '<li class="na">Nothing scored below 70</li>'}</ul>`, {icon: I('alert')})}
      ${card('Biggest opportunity', sc.biggest_opportunity ? `
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
        hint: `${d.branches.length} branches · recorded per session in the transcript`})}
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
  // "40+" when the list was cut at its limit, rather than a count that looks complete
  const cnt = k => r[k].length ? r[k].length + (r[k + '_more'] ? '+' : '') : 0;
  const sec = (title, html, n) => card(`${title} (${n})`, n ? html
    : '<div class="empty">No matches</div>');
  page.innerHTML = `
    <div class="note">Global search for "<b>${esc(term)}</b>" — searches prompt text, session IDs
      and titles, git branches, project names and paths, model IDs, tool names and targets,
      and dates. Independent of the global filters.</div>
    ${sec('Prompts', '<div id="sp"></div>', cnt('prompts'))}
    ${sec('Sessions', '<div id="ss"></div>', cnt('sessions'))}
    <div class="grid g3">
      ${sec('Projects', table([{h: 'Project', f: x => esc(x.name)},
        {h: 'Path', trunc: 1, f: x => `<span class="mono sub">${esc(x.path || '—')}</span>`}],
        r.projects), cnt('projects'))}
      ${sec('Models', table([{h: 'Model', f: x => esc(modelName(x.model))},
        {h: 'Requests', num: 1, f: x => fmtInt(x.requests)},
        {h: 'Est. cost', num: 1, f: x => fmtUSD(x.cost)}], r.models), cnt('models'))}
      ${sec('Days', table([{h: 'Day', f: x => esc(x.day)},
        {h: 'Requests', num: 1, f: x => fmtInt(x.requests)},
        {h: 'Est. cost', num: 1, f: x => fmtUSD(x.cost)}], r.days), cnt('days'))}
    </div>
    ${sec('Tools & targets', table([{h: 'Tool', f: x => esc(x.name)},
      {h: 'Target', trunc: 1, title: x => x.target || '', f: x => `<span class="mono">${esc(x.target || '—')}</span>`},
      {h: 'Calls', num: 1, f: x => fmtInt(x.n)}], r.tools), cnt('tools'))}`;
  if (r.prompts.length) {
    $('#sp', page).innerHTML = table([
      {h: 'When', f: x => `<span class="mono">${esc((x.ts || '').slice(0, 16).replace('T', ' '))}</span>`},
      {h: 'Prompt', trunc: 1, title: x => pt(x.preview), f: x => esc(pt(x.preview))},
      {h: 'Category', f: x => `<span class="pill">${esc(x.category)}</span>`},
      {h: 'Tokens', num: 1, f: x => fmtNum(x.billable_tokens)},
      {h: 'Est. cost', num: 1, f: x => fmtUSD(x.est_cost_usd)},
    ], r.prompts, {onRow: 1});
    wireTable($('#sp', page), r.prompts, x => openPrompt(x.prompt_id));
  }
  if (r.sessions.length) {
    $('#ss', page).innerHTML = table([
      {h: 'Session', trunc: 1, f: x => esc(stitle(x))},
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
  ['models', 'Model breakdown'], ['projects', 'Projects'], ['costs', 'Cost by day and model'],
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
          <div class="dt">A recommendation grounded in an observed share of spend. No saving is
          estimated.</div></div>
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
// "What to change": things to fix once and habits to change, each with its reason.
// Running sessions are left to ⚡ Act now, which can act on them. Items are keyed so a
// dismissed one comes back only if its evidence changes (e.g. more sessions affected).
const firstSentence = t => { const m = String(t || '').match(/^.*?[.!?](\s|$)/); return (m ? m[0] : String(t || '')).trim(); };
// "29.7% of X." + "35.6% of X." -> "29.7–35.6% of X." plus the shared advice; null when they differ.
function sameFix(fixes) {
  const pct = /\d+(?:\.\d+)?%/, heads = fixes.map(firstSentence);
  const shape = heads.map(t => t.replace(pct, '#'));
  if (!shape.every(t => t === shape[0]) || !pct.test(heads[0])) return null;
  const nums = heads.map(t => parseFloat(t.match(pct)[0]));
  const range = `${Math.min(...nums)}–${Math.max(...nums)}%`;
  const rest = fixes[0].slice(heads[0].length).trim().replace(/\bthis repo\b/g, 'each repo');
  return `${shape[0].replace('#', range).replace(' here ', ' in these repos ')} ${rest}`.trim();
}
function focusItems(d) {
  const out = [];
  for (const m of d.memory_suggestions || []) if (m.kind === 'security')
    out.push({lvl: 1, kind: 'fix', key: `sec:${m.sessions}`, view: 'diagnose', anchor: 'dx-mem',
      text: `Rotate the credentials you pasted into prompts (${m.sessions} session${m.sessions === 1 ? '' : 's'})`,
      why: 'Closing the sessions does not help: the secrets stay in plain text in those transcripts on disk.'});
  const byIssue = (sev, lvl) => {
    const groups = new Map();
    for (const p of d.projects || []) for (const i of p.issues) if (i.severity === sev) {
      if (!groups.has(i.title)) groups.set(i.title, []);
      groups.get(i.title).push({project: p.name, fix: i.fix});
    }
    for (const [title, ps] of groups) out.push({lvl, kind: 'fix', view: 'diagnose', anchor: 'dx-issues',
      key: `proj:${title}:${ps.map(x => x.project).sort().join(',')}`,
      text: ps.length === 1 ? `${ps[0].project}: ${title}` : `${title} in ${ps.length} projects: ${ps.map(x => x.project).join(', ')}`,
      why: ps.length === 1 ? ps[0].fix : sameFix(ps.map(x => x.fix))
        || ps.slice(0, 3).map(x => `${x.project}: ${firstSentence(x.fix)}`).join(' · ')});
  };
  byIssue('high', 1);
  for (const r of d.recommendations || []) if (r.priority === 1)
    out.push({lvl: 2, kind: 'habit', key: `rec:${r.title}`, view: 'diagnose', anchor: 'dx-recs',
      text: r.title, why: `${firstSentence(r.why)} ${firstSentence(r.how)}`});
  byIssue('medium', 3);
  return out.sort((a, b) => a.lvl - b.lvl);
}
const FOCUS_KEY = 'finops.focus.hidden';
const focusHidden = () => { try { return new Set(JSON.parse(localStorage.getItem(FOCUS_KEY) || '[]')); } catch { return new Set(); } };
const setFocusHidden = set => { try { localStorage.setItem(FOCUS_KEY, JSON.stringify([...set])); } catch {} };
function focusStrip(d, n = 5) {
  const all = focusItems(d), hidden = focusHidden();
  const items = all.filter(i => !hidden.has(i.key));
  const nHidden = all.length - items.length;
  if (!all.length) return '';
  const fixes = items.filter(i => i.kind === 'fix').length, habits = items.filter(i => i.kind === 'habit').length;
  return `<section class="focus"><div class="focus-hd">${I('target')} What to change
      <span class="note">${fixes} to fix once · ${habits} habit${habits === 1 ? '' : 's'} from your past sessions</span>
      <span class="spacer"></span>${nHidden ? `<button class="act ghost" data-unhide="1">Show ${nHidden} hidden</button>` : ''}</div>
    ${items.slice(0, n).map((it, k) => `<div class="focus-it l${it.lvl === 1 ? 1 : 2}" data-go="${it.view}" data-anchor="${it.anchor}" data-key="${esc(it.key)}">
      <span class="fn">${k + 1}</span>
      <span class="ft"><span class="fx">${esc(it.text)}</span><span class="fw">${esc(it.why || '')}</span></span>
      <span class="fl">${it.kind === 'habit' ? 'Habit' : 'Fix once'}</span>
      <button class="fd" title="Hide this until its evidence changes" aria-label="Hide">${I('x')}</button></div>`).join('')
      || '<div class="note">Everything here is hidden. Nice.</div>'}</section>`;
}
function wireFocus(root) {
  const redraw = () => { const old = root.querySelector('.focus'); if (!old || !root._focusData) return;
    const el = h(focusStrip(root._focusData) || '<div></div>'); old.replaceWith(el); wireFocus(root); };
  root.querySelectorAll('.focus-it').forEach(a => {
    a.onclick = () => jumpTo(a.dataset.go, a.dataset.anchor);
    a.querySelector('.fd').onclick = e => { e.stopPropagation();
      const hid = focusHidden(); hid.add(a.dataset.key); setFocusHidden(hid); redraw(); };
  });
  const un = root.querySelector('[data-unhide]');
  if (un) un.onclick = () => { setFocusHidden(new Set()); redraw(); };
}
async function updateNavBadges() {
  const set = (id, n, lvl) => { const el = document.querySelector(`[data-nb="${id}"]`);
    if (el) { el.textContent = n ? n : ''; el.className = 'nb' + (n ? ' l' + lvl : ''); } };
  try {
    const [d, w, a] = await Promise.all([hasPriced() ? api('diagnose') : null, api('waste'), api('anomalies')]);
    if (d) { const hid = focusHidden(), f = focusItems(d).filter(i => !hid.has(i.key)), urgent = f.filter(i => i.lvl === 1).length;
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
  // Stamp the version onto the logo. Two copies of this app can be installed at
  // once (npm global, a checkout) and the browser cannot tell them apart.
  const ver = $('#ver');
  if (ver && S.opts?.version) {
    ver.textContent = 'v' + S.opts.version;
    ver.title = 'Version serving this page';
  }
  const mark = $('.brand .mark');
  if (mark) mark.innerHTML = `<span class="dot"></span>${esc(label)} FinOps` +
    (S.opts?.version ? `<span class="ver" id="ver" title="Version serving this page">v${esc(S.opts.version)}</span>` : '');
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
  scopeStrip();
  page.innerHTML = pageLoader();
  loaderTicker(page);
  updateNavBadges();
  try {
    await (VIEWS[S.view] || VIEWS.overview)(page);
  } catch (e) {
    page.innerHTML = `<div class="card"><div class="body">
      <b>Something went wrong rendering this view.</b>
      <pre class="prompt-text" style="margin-top:8px">${esc(e.stack || e)}</pre></div></div>`;
  }
}

/* ---------- Recent panel ----------
   Past sessions and their prompts from any page, without leaving it. Resume and
   stop act on sessions, so the list is grouped by session; running ones sit on
   top. Close and Force kill stay on Running sessions, one click away from here. */
const RECENT = {open: false, offset: 0, limit: 30, q: '', open_ids: new Set()};
// Recent means all time: keep agent/project filters, drop the date range and thresholds.
const recentQs = () => { const p = new URLSearchParams(qs());
  ['start', 'end', 'min_cost', 'min_tokens'].forEach(k => p.delete(k)); return p.toString(); };
const fmtWhen = iso => iso ? `${ago(iso)} · ${esc(iso.slice(0, 16).replace('T', ' '))}` : '';

function toggleRecent(force) {
  RECENT.open = force ?? !RECENT.open;
  document.querySelectorAll('.recent,.recent-scrim').forEach(e => e.remove());
  $('#recent-btn')?.classList.toggle('on', RECENT.open);
  if (!RECENT.open) return;
  const scrim = h('<div class="recent-scrim"></div>');
  const p = h(`<aside class="recent" aria-label="Recent sessions"><header>
      <h2>Recent</h2><span class="note">all time · agent &amp; project filters apply</span>
      <span class="spacer"></span><button class="iconbtn" data-x>Close ${I('x')}</button></header>
    <div class="recent-search"><span class="mag">${I('search')}</span>
      <input id="recent-q" placeholder="Search your prompts…" value="${esc(RECENT.q)}"></div>
    <div class="content"><div class="loading">Loading…</div></div></aside>`);
  scrim.onclick = () => toggleRecent(false);
  p.querySelector('[data-x]').onclick = () => toggleRecent(false);
  let t;
  p.querySelector('#recent-q').oninput = e => { clearTimeout(t);
    t = setTimeout(() => { RECENT.q = e.target.value.trim(); RECENT.offset = 0; renderRecent(); }, 260); };
  document.body.append(scrim, p);
  RECENT.offset = 0;
  renderRecent();
}

async function renderRecent() {
  const box = $('.recent .content');
  if (!box) return;
  const more = RECENT.offset > 0;
  const [live, res] = await Promise.all([
    more || RECENT.q ? null : fetch('/api/live?agents=' + encodeURIComponent(S.filter.agents.join(',')))
      .then(r => r.json()).catch(() => ({sessions: []})),
    RECENT.q
      ? fetch(`/api/prompts?${recentQs()}&order=recent&limit=${RECENT.limit}&offset=${RECENT.offset}&q=${encodeURIComponent(RECENT.q)}`).then(r => r.json())
      : fetch(`/api/sessions?${recentQs()}&order=recent&limit=${RECENT.limit}&offset=${RECENT.offset}`).then(r => r.json())]);
  if (!$('.recent')) return;   // closed while loading
  const rows = res.rows || [];
  const liveIds = new Set(((live || {}).sessions || RECENT.live || []).map(x => x.session_id));
  if (live) RECENT.live = live.sessions || [];
  const html = RECENT.q ? recentPrompts(rows) : recentSessions(rows, liveIds);
  const shown = RECENT.offset + rows.length;
  const moreBtn = shown < (res.total || 0)
    ? `<button class="btn pb-copy recent-more">Load more (${fmtInt(shown)} of ${fmtInt(res.total)})</button>` : '';
  if (more) { box.querySelector('.recent-more')?.remove(); box.insertAdjacentHTML('beforeend', html + moreBtn); }
  else box.innerHTML = (RECENT.q ? '' : recentLive(RECENT.live || [])) + html + moreBtn
    || '<div class="empty">Nothing found</div>';
  if (!rows.length && !more && RECENT.q) box.innerHTML = '<div class="empty">No prompt matches</div>';
  wireRecent(box);
}

const recentLive = list => !list.length ? '' : `<div class="recent-hd">Running now
    <span class="spacer"></span><a href="#" data-go-live>Close or kill ${I('chevRight')}</a></div>
  ${list.map((x, i) => `<div class="item recent-it sev-${x.severity === 'high' ? 'high' : x.severity === 'medium' ? 'medium' : 'low'}" data-live="${i}">
    <div class="hd"><span class="live-dot ${x.status === 'busy' ? 'busy' : ''}" title="${esc(x.status)}"></span>
      <span class="recent-title">${esc((!MASKED && x.name) || shortId(x.session_id))}</span>
      <span class="spacer"></span><span class="note">${esc(x.status || '')}</span></div>
    <div class="mt"><span>${esc(x.project || '')}</span>
      ${x.context ? `<span>${fmtNum(x.context)} context</span>` : ''}
      ${x.est_cost_usd != null ? `<span>${fmtUSD(x.est_cost_usd)}</span>` : ''}</div>
    <div class="live-actions">
      ${x.resume ? `<button class="act ghost" data-resume="${esc(x.resume)}" title="Copy: ${esc(x.resume)}">${I('copy')} Resume</button>` : ''}
      ${x.signalable && x.agent === 'claude' ? '<button class="act" data-la="compact" title="Types /compact into that session\'s terminal">' + I('compact') + ' Compact</button>' : ''}
      ${x.signalable ? `<button class="act warn" data-la="interrupt" ${x.status !== 'busy' ? 'disabled title="Nothing running"' : 'title="Stops the current turn, like pressing Esc"'}>${I('pause')} Stop</button>` : ''}
      ${x.session_id ? `<button class="act ghost" data-open-sess="${esc(x.session_id)}">Details</button>` : ''}
      <span class="live-msg"></span></div></div>`).join('')}
  <div class="recent-hd">Past sessions</div>`;

const recentSessions = (rows, liveIds) => rows.map(r => `
  <div class="item recent-it clickable${RECENT.open_ids.has(r.session_id) ? ' open' : ''}" data-sess="${esc(r.session_id)}">
    <div class="hd">${liveIds.has(r.session_id) ? '<span class="live-dot" title="Running now"></span>' : ''}
      <span class="recent-title">${esc(stitle(r))}</span><span class="spacer"></span>
      <span class="note">${fmtUSD(r.cost)}</span></div>
    <div class="mt"><span>${esc(r.project || '')}</span><span>${fmtInt(r.prompts)} prompts</span>
      <span>${fmtWhen(r.started_at)}</span></div>
    <div class="live-actions">
      ${r.resume ? `<button class="act ghost" data-resume="${esc(r.resume)}" title="Copy: ${esc(r.resume)}">${I('copy')} Resume</button>` : ''}
      <button class="act ghost" data-open-sess="${esc(r.session_id)}">Details</button>
      <span class="spacer"></span><span class="note recent-caret">${I(RECENT.open_ids.has(r.session_id) ? 'chevDown' : 'chevRight')} prompts</span></div>
    <div class="recent-prompts"${RECENT.open_ids.has(r.session_id) ? '' : ' hidden'}></div></div>`).join('')
  || '<div class="empty">No sessions for these filters</div>';

const promptLine = x => `<a href="#" class="recent-p" data-prompt="${x.prompt_id}" title="${esc(pt(x.preview))}">
  <span class="mono sub">${esc((x.ts || '').slice(5, 16).replace('T', ' '))}</span>
  <span class="recent-pv">${esc(pt(x.preview) || '—')}</span>
  <span class="note">${fmtUSD(x.pcost ?? x.est_cost_usd)}</span></a>`;

const recentPrompts = rows => rows.map(x => `<div class="item recent-it">
    ${promptLine(x)}
    <div class="mt"><span>${esc((!MASKED && x.session_title) || shortId(x.session_id))}</span>
      <span>${esc(x.project || '')}</span>
      <a href="#" data-open-sess="${esc(x.session_id)}">Session ${I('chevRight')}</a></div></div>`).join('');

async function loadSessionPrompts(host, sid) {
  host.innerHTML = '<div class="note">Loading…</div>';
  try {
    const s = await fetch(`/api/session/${encodeURIComponent(sid)}`).then(r => r.json());
    const ps = s.prompts || [];
    host.innerHTML = ps.length ? ps.map(promptLine).join('') : '<div class="note">No prompts recorded</div>';
    wireRecent(host);
  } catch (e) { host.innerHTML = `<div class="live-msg err">${esc(e.message)}</div>`; }
}

function wireRecent(root) {
  wireRowActs(root);   // Resume: copy command
  root.querySelectorAll('[data-open-sess]').forEach(a => a.onclick = e => {
    e.preventDefault(); e.stopPropagation(); openSession(a.dataset.openSess); });
  root.querySelectorAll('[data-prompt]').forEach(a => a.onclick = e => {
    e.preventDefault(); e.stopPropagation(); openPrompt(+a.dataset.prompt); });
  root.querySelectorAll('[data-go-live]').forEach(a => a.onclick = e => {
    e.preventDefault(); toggleRecent(false); go('live'); });
  root.querySelector('.recent-more')?.addEventListener('click', () => {
    RECENT.offset += RECENT.limit; renderRecent(); });
  root.querySelectorAll('.item[data-sess]').forEach(it => {
    const host = it.querySelector('.recent-prompts'), sid = it.dataset.sess;
    if (!host.hidden && !host.childElementCount) loadSessionPrompts(host, sid);
    it.onclick = e => {
      if (e.target.closest('button, a, .recent-prompts')) return;
      host.hidden = !host.hidden;
      it.classList.toggle('open', !host.hidden);
      it.querySelector('.recent-caret').innerHTML = I(host.hidden ? 'chevRight' : 'chevDown') + ' prompts';
      host.hidden ? RECENT.open_ids.delete(sid) : RECENT.open_ids.add(sid);
      if (!host.hidden && !host.childElementCount) loadSessionPrompts(host, sid);
    };
  });
  root.querySelectorAll('.item[data-live]').forEach(it => {
    const x = RECENT.live[+it.dataset.live], msg = it.querySelector('.live-msg');
    it.querySelectorAll('[data-la]').forEach(b => b.onclick = async e => {
      e.stopPropagation();
      if (!armed(b)) return;   // two clicks, like Running sessions
      b.disabled = true;
      try {
        const r = await sessionAction(x.pid, b.dataset.la, {agent: x.agent});
        if (!r.ok && r.copy) { try { await navigator.clipboard.writeText(r.copy); } catch {} }
        msg.textContent = r.ok ? r.message : `Failed: ${r.error}`;
        msg.className = 'live-msg ' + (r.ok ? 'ok' : 'err');
      } catch (err) { msg.textContent = 'Failed: ' + err.message; msg.className = 'live-msg err'; }
      setTimeout(() => { b.disabled = false; }, 2500);
    });
  });
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
  let want = new URLSearchParams(location.search).get('view');
  // The repriced Model switch view was removed; its links now open the measured comparison.
  if (want === 'modelswitch') want = 'compare';
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
      // an agent with no price data leads with what it does have, never "0 tok"
      a.cost ? fmtUSD(a.cost) : a.tokens ? fmtNum(a.tokens) + ' tokens' : fmtInt(a.requests) + ' requests',
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
  b.title = `Last synced ${ago(d.built_at)}. Re-read every agent's transcripts from disk into the warehouse`;
  b.classList.remove('busy');
  b.innerHTML = `${ICON.sync}Sync <span class="ago">· ${ago(d.built_at)}</span>`;
}
async function runSync() {
  const b = $('#sync');
  b.disabled = true; b.classList.add('busy'); b.innerHTML = `${ICON.sync}Syncing…`;
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
      work (see <a data-go="compare">Compare models</a> for what your own history shows) and keep Claude for hard problems.</div>
    ${!d.bin_on_path ? `<div class="item sev-medium"><div class="dt">${esc(d.bin_dir)} is not on your PATH, so the new commands
      won't run by name. Add <code>export PATH="$HOME/.local/bin:$PATH"</code> to ~/.zshrc.</div></div>` : ''}
    ${card('How to use and test a free model', `<div class="stack">
      <div class="dt"><b>1. Test:</b> click <b>${I('play')} Test it</b> on an added model. It asks the model directly, then runs a real one-line Claude Code session through the new command.</div>
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
        ? `<div class="dt">${I('checkCircle')} Added. Run <code>${esc(m.command)}</code> in any project.</div>
           <div><button class="act" data-test="${esc(m.id)}">${I('play')} Test it</button>
             <button class="act ghost" data-rm="${esc(m.id)}">Remove</button></div>
           <div data-testlog="${esc(m.id)}"></div>`
        : `${m.downloaded ? `<div class="dt">${I('checkCircle')} Already downloaded in Ollama. Add creates the <code>${esc(m.command)}</code> command; nothing is downloaded again.</div>` : ''}
           <div><button class="act" data-add="${esc(m.id)}" ${m.fits_ram ? '' : 'title="Less RAM than recommended"'}>${I('plus')} Add ${esc(m.command)}</button></div>`}
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
      ? '<div class="item sev-low"><div class="dt">' + I('checkCircle') + ' Working end to end.</div></div>'
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
      ${s.warn ? I('alert') : `${i + 1}.`} ${esc(s.do)}${s.consent ? ' <span class="pill">needs your OK</span>' : ''}</div></div>`).join('')}</div>
    ${plan.needs.map(n => `${n.steps ? `<div class="item"><div class="hd">How to get your ${esc(n.label)}</div>
      <ol class="dt" style="margin:6px 0 8px 18px;padding:0;line-height:1.7">${n.steps.map(x => `<li>${esc(x)}</li>`).join('')}</ol>
      ${n.url ? `<a class="act" href="${esc(n.url)}" target="_blank" rel="noopener" style="text-decoration:none;display:inline-block">Open ${esc(new URL(n.url).host)} ${I('external')}</a>` : ''}</div>` : ''}
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
        `<div class="item sev-low"><div class="dt">${I('checkCircle')} Added. Open a terminal in any project and run <code>${esc(j.result.command)}</code>. Inside it, <code>/model</code> shows the Qwen model; your normal <code>claude</code> is unchanged.</div>
         <div><button class="act" id="fm-test">${I('play')} Test it now</button></div><div id="fm-testlog"></div></div>`);
        $('#fm-test', c).onclick = e => testFreeModel(id, $('#fm-testlog', c), e.target);
        if (S.view === 'freemodels') render(); }
      else $('#fm-go', c).disabled = false;
    } catch (e) { $('#fm-log', c).innerHTML = `<div class="item sev-high"><div class="dt">${esc(e.message)}</div></div>`; $('#fm-go', c).disabled = false; }
  };
}

/* ---------- compare Claude vs free models ---------- */
VIEWS.compare = async (page) => {
  const d = await fetch('/api/compare?' + qs()).then(r => r.json());
  if (d.error) throw new Error(d.error);
  const hasFree = d.rows.some(r => r.kind === 'free');
  const stars = n => n == null ? '<span class="na">—</span>' : I('star').repeat(Math.floor(n)) + (n % 1 ? I('starHalf') : '') +
    `<span style="opacity:.25">${I('star').repeat(5 - Math.ceil(n))}</span>`;
  const price = r => r.kind === 'free' ? '<b>$0</b>' : `${fmtUSD(r.price_in)} / ${fmtUSD(r.price_out)}`;
  const status = r => r.kind !== 'free'
    ? (r.requests ? `${fmtInt(r.requests)} req · ${fmtUSD(r.cost)}` : '<span class="na">not used</span>')
    : !r.fits ? `${I('xCircle')} needs ${r.min_ram_gb} GB RAM` : r.installed ? `${I('checkCircle')} <code>${esc(r.command)}</code>` : `<a data-go="freemodels">${I('plus')} Add</a>`;
  page.innerHTML = `
    ${card(hasFree ? 'Claude vs free models' : `${esc(agentWord())} models side by side`, `<div id="cmp"></div>`, {flush: 1, badge: BADGE.recommendation,
      hint: hasFree ? `This machine: ${esc(d.os)}, ${d.ram_gb} GB RAM` : 'List prices, your usage in range', footer: esc(d.note)})}
    ${!hasFree ? '' : `<div class="grid g3">
      ${card('Use Claude for', `<div class="dt">Multi-file changes, debugging, anything agentic or long-running.
        Sonnet is the value pick; keep Opus for the hardest problems (see <a data-go="compare">Compare models</a>).</div>`)}
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

/* ---------- subagent models: measured $/run per type and model, plus a settings experiment ---------- */
let SUB_MSG = null;          // {ok, text}: shown once after the page redraws
VIEWS.subagents = async (page) => {
  const d = await api('subagents');
  const k = d.kpis, x = d.experiment;
  const say = s => {
    const t = esc(s.type);
    switch (s.kind) {
      case 'fork': return `Forks carry your whole conversation (median ${fmtNum(s.med_context)} tokens of context here).
        Their cost comes from context, not the model: run <code>/compact</code> before forking, or use a fresh subagent with a short brief.`;
      case 'unpriced': return `Can't compare: ${esc(s.models.join(', '))} isn't priced. Add it to <code>config/pricing.json</code>.`;
      case 'cheapest': return `Already on the cheapest model this type has used (${esc(s.model)}).`;
      case 'not_enough': return `Not enough runs to compare: a cheaper model needs ${s.min_runs} runs of this type, and so does
        ${esc(s.model)}. An experiment below would collect them.`;
      case 'switch': return `On ${esc(s.to)}, ${t} runs cost a median <b>${fmtUSD(s.to_med)}</b> (${s.to_runs} runs) vs
        <b>${fmtUSD(s.from_med)}</b> on ${esc(s.from)} (${s.from_runs} runs). The runs did different tasks, so this is evidence, not proof.
        ${s.fix.kind === 'file'
          ? `<br>Fix: add <code>model: ${esc(s.alias)}</code> to <code>${esc(s.fix.path)}</code>${s.fix.current ? ` (it now says <code>model: ${esc(s.fix.current)}</code>)` : ''}.`
          : `<br>Built-in subagents have no documented per-type model setting. Test it with an experiment on <b>${esc(s.alias)}</b> below.`}`;
    }
    return '';
  };
  const typeBlock = t => `<div class="item"><div class="hd">${esc(t.type)}${t.custom ? ' <span class="pill">your agent file</span>' : ''}
      <span class="spacer"></span><span style="font-variant-numeric:tabular-nums">${fmtInt(t.runs)} run${t.runs === 1 ? '' : 's'} · ${fmtUSD(t.cost)}</span></div>
    ${table([
      {h: 'Model', f: c => esc(c.name) + (c.priced ? '' : ' <span class="pill">not priced</span>')},
      {h: 'Runs', num: 1, f: c => fmtInt(c.runs)},
      {h: 'Median $/run', num: 1, f: c => c.priced ? fmtUSD(c.med_cost) : '—'},
      {h: 'Turns/run', num: 1, f: c => fmtNum(c.med_turns)},
      {h: 'Output/run', num: 1, f: c => fmtNum(c.med_output)},
      {h: 'Context/run', num: 1, f: c => fmtNum(c.med_context)},
      {h: 'Spend', num: 1, f: c => c.priced ? fmtUSD(c.cost) : '—'},
    ], t.rows)}
    <div class="dt" style="margin-top:6px">${say({...t.suggestion, type: t.type})}</div></div>`;
  const side = (s, n) => s.runs ? `${fmtUSD(s.med_cost)} <span class="sub">${fmtInt(s.runs)} run${s.runs === 1 ? '' : 's'} · ${fmtNum(s.med_turns)} turns</span>` : '<span class="na">no runs</span>';
  const results = r => r.types.length ? table([
    {h: 'Type', f: t => esc(t.type)},
    {h: 'Before (median $/run)', num: 1, f: t => side(t.before)},
    {h: `After, on ${r.model}`, num: 1, f: t => side(t.after)},
    {h: 'Change', num: 1, f: t => t.ready ? `${t.change < 0 ? '−' : t.change > 0 ? '+' : ''}${fmtUSD(Math.abs(t.change))}`
      : `<span class="na">collecting ${Math.min(t.before.runs, t.after.runs)} of ${d.min_runs}</span>`},
    {h: 'Ignored', num: 1, f: t => t.ignored ? `${fmtInt(t.ignored)}` : '—', title: () => 'Runs after the start that used another model (an agent file or the main model picked it)'},
  ], r.types) : '<div class="empty">No subagent runs in the before or after window yet</div>';
  const span = r => `${esc(r.started_at.slice(0, 10))} → ${r.stopped_at ? esc(r.stopped_at.slice(0, 10)) : 'now'}`;
  const pick = (d.types.map(t => t.suggestion).find(s => s.kind === 'switch' && s.fix.kind === 'experiment') || {}).alias || 'haiku';
  const json = m => `"env": {\n  "${x.env_var}": "${m}"\n}`;
  const exp = x.settings_error ? `<span class="lb-state warn">${esc(x.settings_error)}</span>`
    : x.active ? `<div class="lb-head"><span class="lb-state on">● Running on <b>${esc(x.active.model)}</b> since ${esc(x.active.started_at.slice(0, 10))}</span>
          <span class="spacer"></span><button class="act" id="sx-stop">Stop experiment</button></div>
        ${x.warning ? `<p class="fld-hint" style="color:var(--warning-ink)">${esc(x.warning)}</p>` : ''}
        <p class="fld-hint">Before is the same length of time just before the start (at most 30 days). These windows ignore the date range above.</p>`
    : x.current_value ? `<span class="lb-state warn"><code>${esc(x.env_var)}</code> is already set to <b>${esc(x.current_value)}</b> in
        <span class="mono">${esc(x.settings_path)}</span>. finops won't change a value it didn't set.</span>`
    : `<p class="blk-intro">Run subagents on one model for a while, then compare each type's runs before and after.
        This is the only way to see what a switch does on your own work.</p>
      <div class="lb-head"><label for="sx-model">Model</label>
        <select id="sx-model" style="width:auto">${x.model_choices.map(m => `<option${m === pick ? ' selected' : ''}>${m}</option>`).join('')}</select>
        <button class="act" id="sx-start">Start experiment</button></div>
      <div class="livebox" id="sx-confirm" hidden>
        <p><b>This changes Claude Code's settings for all your projects.</b> It adds this to
          <span class="mono">${esc(x.settings_path)}</span> (a backup is saved first):</p>
        <pre class="mono" id="sx-json" style="margin:6px 0;white-space:pre-wrap">${esc(json(pick))}</pre>
        <p class="blk-sub">New Claude Code sessions then run subagents on this model. Sessions already open keep their model,
          and so do agent files with their own <code>model:</code> line and calls where the main model picks one.
          Stop removes it again.</p>
        <div class="live-actions"><button class="chip on" id="sx-go">Confirm and start</button>
          <button class="act ghost" id="sx-cancel">Cancel</button></div></div>`;
  page.innerHTML = !d.claude_selected
    ? `<div class="empty">Subagent models covers Claude Code only. Add Claude Code to the agents above.</div>`
    : `
    <div class="grid g4">
      ${kpi('Subagent spend', fmtUSD(k.sub_cost), `${fmtPct(k.share_pct)} of Claude Code spend in range`, {badge: BADGE.estimated})}
      ${kpi('Subagent share of spend', fmtPct(k.share_pct), 'the upper bound on what routing can change, not a saving', {badge: BADGE.estimated})}
      ${kpi('Subagent runs', fmtInt(k.runs), 'one run = one subagent call', {badge: BADGE.actual})}
      ${kpi('Subagent types', fmtInt(k.types), 'built-in or your own agent files', {badge: BADGE.actual})}
    </div>
    ${card('By type and model', d.types.length ? `<div class="stack">${d.types.map(typeBlock).join('')}</div>`
        : '<div class="empty">No subagent runs in this range</div>',
      {badge: BADGE.estimated, hint: `medians per run · a cheaper model is suggested only with ${d.min_runs}+ runs on each side`,
       footer: 'Measured from runs that happened, never repriced. No savings total is shown: runs of the same type did different tasks.'})}
    ${card('Experiment', `<div class="cfg"><section class="blk">${exp}
        ${SUB_MSG ? `<div class="fld-hint ${SUB_MSG.ok ? 'ok' : ''}" style="${SUB_MSG.ok ? '' : 'color:var(--critical-ink)'}">${esc(SUB_MSG.text)}</div>` : ''}
        ${x.shell_value ? `<p class="fld-hint">Your shell also sets <code>${esc(x.env_var)}=${esc(x.shell_value)}</code>, which may take priority.</p>` : ''}
      </section>
      ${x.active && !x.settings_error ? results(x.active) : ''}
      ${x.history.length ? `<div class="sec" style="margin-top:12px">Past experiments</div>${x.history.map(h =>
        `<details style="margin:4px 0"><summary style="cursor:pointer;font-size:12px">${esc(h.model)} · ${span(h)}</summary>${results(h)}</details>`).join('')}` : ''}
      </div>`, {badge: BADGE.recommendation, hint: `sets ${x.env_var} in Claude Code's settings`})}`;
  SUB_MSG = null;
  const redraw = msg => { SUB_MSG = msg; bust(); render(); };
  const run = async (body, btn) => {
    if (btn) btn.disabled = true;
    try { const r = await doAction('subagent_experiment', body); redraw({ok: r.ok, text: r.message}); }
    catch (e) { redraw({ok: false, text: e.message}); }
  };
  const sel = $('#sx-model', page), box = $('#sx-confirm', page);
  if (sel) sel.onchange = () => { $('#sx-json', page).textContent = json(sel.value); };
  const start = $('#sx-start', page);
  if (start) start.onclick = () => { box.hidden = false; $('#sx-go', page).focus(); };
  if (box) {
    $('#sx-cancel', page).onclick = () => { box.hidden = true; };
    $('#sx-go', page).onclick = e => run({action: 'start', model: sel.value}, e.currentTarget);
  }
  const stop = $('#sx-stop', page);
  if (stop) stop.onclick = () => {
    if (!stop.dataset.armed) {
      stop.dataset.armed = '1'; stop.textContent = 'Click again to stop';
      setTimeout(() => { if (stop.isConnected) { delete stop.dataset.armed; stop.textContent = 'Stop experiment'; } }, 4000);
      return;
    }
    run({action: 'stop'}, stop);
  };
};

/* ---------- skills & MCP from recurring work ---------- */
/* ---------- jev: TypeSafe's fast decision model ---------- */
let JEV_MSG = null;          // {where: 'install'|'key', ok, text}: shown once after the page redraws
VIEWS.jev = async (page) => {
  const d = await api('jev');
  const st = d.status, k = d.key, fit = d.fit;
  const flash = where => JEV_MSG && JEV_MSG.where === where
    ? `<div class="fld-hint ${JEV_MSG.ok ? 'ok' : ''}" style="${JEV_MSG.ok ? '' : 'color:var(--critical-ink)'}">${esc(JEV_MSG.text)}</div>` : '';
  const state = !st.claude
    ? `<span class="lb-state warn">Claude Code's <code>claude</code> command was not found on this computer, so Jev can't be installed from here.</span>`
    : st.installed ? `<span class="lb-state on">● Installed${st.version ? ' · version ' + esc(st.version) : ''}${st.enabled === false ? ' (turned off in Claude Code)' : ''}</span>`
    : '<span class="lb-state">○ Not installed</span>';
  const keyState = k.source === 'shell' ? `set in your shell${k.last4 ? ' · ends ' + esc(k.last4) : ''}`
    : k.source ? `saved${k.last4 ? ' · ends ' + esc(k.last4) : ''}` : 'not set';
  const small = fit.share_pct < 1;
  const usd = v => v ? fmtUSD(v) : '$0';
  page.innerHTML = `
    ${card('What is Jev?', `<div class="cfg">
      <p class="blk-intro">Jev is a very fast, very cheap AI that makes decisions. It picks one option, answers yes or no,
        gives a score, or pulls out a value. It can't write text or code.</p>
      <p class="blk-intro">It doesn't make Claude Code itself cheaper. It makes the apps and scripts you build cheaper, by
        replacing AI calls that only make a decision, like sorting tickets or checking a pull request.</p>
      <p class="blk-intro">Installing it teaches Claude how to use Jev when you build those.
        <a href="https://docs.typesafe.ai" target="_blank" rel="noopener">Read Jev's docs</a></p></div>`,
      {hint: 'made by TypeSafe AI'})}
    ${card('Set up Jev', `<div class="cfg">
      <section class="blk">
        <h4><span class="num">①</span> Install the Jev plugin for Claude Code</h4>
        <div class="lb-head">${state}<span class="spacer"></span>
          ${st.claude && !st.installed ? '<button class="act" id="jev-install">Install</button>' : ''}
          ${st.installed ? '<button class="act" id="jev-uninstall">Uninstall</button>' : ''}</div>
        <div class="livebox" id="jev-confirm" hidden>
          <p><b>This adds TypeSafe's plugin to Claude Code for all your projects.</b> It runs these two commands:</p>
          <pre class="mono" style="margin:6px 0;white-space:pre-wrap">claude plugin marketplace add typesafe-ai/skills
claude plugin install typesafe@typesafe-ai</pre>
          <p class="blk-sub">Jev only sees what the apps you build send it. Uninstall any time.</p>
          <div class="live-actions"><button class="chip on" id="jev-go">Confirm install</button>
            <button class="act ghost" id="jev-cancel">Cancel</button></div></div>
        <div id="jev-log"></div>${flash('install')}
      </section>
      <section class="blk">
        <h4><span class="num">②</span> Add your Jev API key</h4>
        <p class="blk-intro">The key lets your apps and Claude talk to Jev. Get one at
          <a href="https://console.typesafe.ai" target="_blank" rel="noopener">console.typesafe.ai</a>.</p>
        <div class="lb-head"><span class="lb-state ${k.source ? 'on' : ''}">Key: ${keyState}</span></div>
        <div style="display:flex;gap:6px;align-items:center;margin-top:6px">
          <input type="password" id="jev-key" autocomplete="off" spellcheck="false" style="flex:1;min-width:0"
            aria-label="Jev API key" placeholder="${k.source ? 'paste a new key to replace it' : 'paste your Jev API key'}">
          <button class="act" id="jev-key-save">Save key</button>
          ${k.source === 'claude_settings' ? '<button class="act" id="jev-key-rm">Remove</button>' : ''}</div>
        <div class="fld-err" id="jev-key-err" role="alert"></div>${flash('key')}
        <p class="fld-hint">It is saved in Claude Code's settings (<span class="mono">~/.claude/settings.json</span>), so new
          Claude Code sessions can use it. Restart any open ones. This page never shows the key again.
          ${k.source === 'shell' ? ' A key set in your shell takes priority over a saved one.' : ''}</p>
      </section></div>`)}
    ${card('Where Jev fits in your work', `<div class="cfg">
      <p class="blk-intro">We looked for your Claude Code prompts that were really just a decision: short, no tools, and
        asking "which one", "yes or no", "classify" and the like.</p>
      <div class="grid g4">
        ${kpi('Decision-like prompts', fmtInt(fit.prompts), `of ${fmtInt(fit.total_prompts)} in this range`)}
        ${kpi('Cost on Claude', usd(fit.claude_cost), fmtPct(fit.share_pct, 2) + ' of your spend', {badge: BADGE.estimated})}
        ${kpi('Rough cost on Jev', usd(fit.jev_cost), 'at TypeSafe\'s published price', {badge: BADGE.estimated})}
        ${kpi('Saving', usd(Math.max(fit.claude_cost - fit.jev_cost, 0)), 'if these had gone to Jev')}
      </div>
      ${small ? `<p class="fld-hint" style="margin-top:8px">Your Claude Code work is mostly writing code, which Jev can't do.
        Jev pays off in apps and scripts that make many decisions, like sorting support tickets or reviewing pull requests.</p>` : ''}
      ${fit.examples.length ? `<div class="sec" style="margin-top:12px">Examples</div><div class="stack">${fit.examples.map(e =>
        `<div class="item"><div class="dt">${MASKED ? '<i>Prompt text hidden</i>' : esc(e.text)}
          <span class="spacer"></span> ${fmtUSD(e.cost)}</div></div>`).join('')}</div>` : ''}</div>`,
      {badge: BADGE.estimated, hint: 'read-only, from this computer\'s transcripts'})}`;
  JEV_MSG = null;
  const redraw = msg => { JEV_MSG = msg; bust(); render(); };
  const confirmBox = $('#jev-confirm', page);
  const inst = $('#jev-install', page);
  if (inst) inst.onclick = () => { confirmBox.hidden = false; $('#jev-go', page).focus(); };
  $('#jev-cancel', page).onclick = () => { confirmBox.hidden = true; };
  const runJob = async (what, btn) => {
    btn.disabled = true;
    try {
      const {job} = await doAction('jev/' + what);
      const j = await followJob(job, $('#jev-log', page));
      redraw({where: 'install', ok: j.state === 'done',
              text: j.state === 'done' ? (what === 'install' ? 'Installed. Restart any open Claude Code sessions to use it.' : 'Removed.')
                : (j.log[j.log.length - 1] || 'It did not finish.')});
    } catch (e) { redraw({where: 'install', ok: false, text: e.message}); }
  };
  $('#jev-go', page).onclick = e => runJob('install', e.currentTarget);
  const un = $('#jev-uninstall', page);
  if (un) un.onclick = () => {
    if (!un.dataset.armed) {
      un.dataset.armed = '1'; un.textContent = 'Click again to uninstall';
      setTimeout(() => { if (un.isConnected) { delete un.dataset.armed; un.textContent = 'Uninstall'; } }, 4000);
      return;
    }
    runJob('uninstall', un);
  };
  const keyIn = $('#jev-key', page), keyErr = $('#jev-key-err', page);
  keyIn.oninput = () => { keyErr.textContent = ''; keyIn.classList.remove('bad'); };
  keyIn.onkeydown = e => { if (e.key === 'Enter') $('#jev-key-save', page).click(); };
  $('#jev-key-save', page).onclick = async () => {
    if (!keyIn.value.trim()) { keyErr.textContent = 'Paste your Jev API key first.'; keyIn.classList.add('bad'); return; }
    try { await doAction('jev/key', {value: keyIn.value}); redraw({where: 'key', ok: true, text: 'Saved. New Claude Code sessions will use it.'}); }
    catch (e) { keyErr.textContent = e.message; keyIn.classList.add('bad'); }
  };
  const rm = $('#jev-key-rm', page);
  if (rm) rm.onclick = async () => {
    if (!rm.dataset.armed) {
      rm.dataset.armed = '1'; rm.textContent = 'Click again to remove';
      setTimeout(() => { if (rm.isConnected) { delete rm.dataset.armed; rm.textContent = 'Remove'; } }, 4000);
      return;
    }
    try { await doAction('jev/key', {remove: true}); redraw({where: 'key', ok: true, text: 'Removed.'}); }
    catch (e) { keyErr.textContent = e.message; }
  };
};

VIEWS.toolkit = async (page) => {
  const d = await fetch('/api/suggestions?' + qs()).then(r => r.json());
  const ev = x => (x || []).map(e => `<code>${esc(e)}</code>`).join(' ');
  page.innerHTML = `
    <div class="note">${esc(d.note)}</div>
    ${card('MCP servers for work you repeat', `<div class="stack">${d.mcp.map(m => `
      <div class="item ${m.installed ? '' : 'sev-medium'}"><div class="hd">${esc(m.name)}
        <span class="spacer"></span><span class="note">${fmtInt(m.sessions)} sessions · ${esc(m.projects.join(', '))}</span></div>
        <div class="dt">${esc(m.what)}</div>
        ${m.evidence.length ? `<div class="dt note">Seen: ${ev(m.evidence)}</div>` : ''}
        ${m.installed ? '<div class="dt">' + I('checkCircle') + ' Already connected</div>' : `
        <div class="dt"><code>${esc(m.command)}</code></div>
        <div><button class="act" data-mcp="${esc(m.id)}">${I('plus')} Add to Claude</button><span class="mcp-out"></span></div>`}
      </div>`).join('') || '<div class="empty">No recurring pattern points to an MCP server</div>'}</div>`,
      {badge: BADGE.recommendation})}
    ${card('Skills from what you repeat', `<div class="stack">${d.skills.map((s, i) => `
      <div class="item ${s.installed ? '' : 'sev-medium'}"><div class="hd">/${esc(s.name)}
        <span class="spacer"></span><span class="note">${fmtInt(s.sessions)} sessions · ${fmtInt(s.runs)} runs · ${esc(s.projects.join(', '))}</span></div>
        <div class="dt">${esc(s.what)}</div>
        <div class="dt note">Examples: ${ev(s.examples.slice(0, 2))}</div>
        ${s.installed ? '<div class="dt">' + I('checkCircle') + ' Skill exists</div>' : `<div><button class="act" data-skill="${i}">${I('plus')} Create skill</button><span class="sk-out"></span></div>`}
      </div>`).join('') || '<div class="empty">Nothing repeats often enough yet</div>'}</div>`,
      {badge: BADGE.recommendation, hint: `Created in ${d.skills_dir}; edit the SKILL.md afterwards`})}`;
  page.querySelectorAll('[data-skill]').forEach(b => b.onclick = async () => {
    const out = b.nextElementSibling; b.disabled = true;
    try { const r = await doAction('skill', d.skills[+b.dataset.skill]);
      out.innerHTML = ` ${I('checkCircle')} Created <code>${esc(r.path)}</code>. Use it with <code>${esc(r.use)}</code>.`; }
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
    try { const r = await doAction(`mcp/${m.id}`, body); out.innerHTML = ` ${I('checkCircle')} Added. ${esc(r.next)}`; }
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
      <div class="hd">${I(cfg[k] ? 'check' : 'circle')} ${esc(p.name)}<span class="spacer"></span>
        <span class="note">${cfg[k] ? 'key found' : 'no key'}</span></div>
      <div class="dt"><b>Gives you:</b> ${esc(p.covers)}</div>
      <div class="dt"><b>Get a key:</b> ${esc(p.how)}</div>
      <div class="dt">Then add it on the <a href="#" data-go-settings>Settings</a> page,
        run <code>claude-finops --set-key</code>, or set <code>${esc(p.env)}</code> in your environment.</div>
    </div>`).join('');
  page.innerHTML = `
    <div class="note"><b>The only page that talks to the internet.</b> ${esc(d.note)}
      Nothing is fetched until you click Refresh.
      <span class="spacer"></span>
      <span class="note">Last fetched: ${esc(d.fetched_at || 'never')}</span>
      <button class="act" id="cl-sync" ${none ? 'disabled title="Add a key first"' : ''}>${I('cloudDown')} Refresh from APIs</button></div>
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
      {hint: 'Stored keys stay on this machine (0600) and are never shown again'})}
    ${card('Billed vs local, per day', `<div id="cl-day"></div>`, {flush: 1, badge: BADGE.actual,
      hint: 'Claude Code: what the vendor billed against what this machine recorded'})}
    ${card('Claude Code users (org-wide)', `<div id="cl-users"></div>`, {flush: 1, badge: BADGE.actual,
      hint: 'Per user per day, aggregated over the range — includes Pro/Max subscription users'})}
    ${card('Cursor members', `<div id="cl-cur"></div>`, {flush: 1, badge: BADGE.actual})}
    ${card('Billed API cost by line item', `<div id="cl-api"></div>`, {flush: 1, badge: BADGE.actual,
      hint: 'From the Anthropic cost report: API spend only, not subscription plans'})}`;
  page.querySelectorAll('[data-go-settings]').forEach(a => a.onclick = e => { e.preventDefault(); go('settings'); });
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
      btn.disabled = false; btn.innerHTML = `${I('cloudDown')} Refresh from APIs`;
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
    act: `Try <b>7 days</b>, then open <b>Project ${I('chevDown')}</b> to focus on one repo.`},
  {el: '#gsearch', t: 'Search', see: 'Search across prompts, sessions, models and dates.',
    get: 'Jump straight to the prompt or session you remember.', act: 'Type a word from a recent prompt and press Enter.'},
  {el: '#sync', t: 'Keep it current', see: 'Sync re-reads every agent\'s local data.',
    get: 'Fresh numbers after you\'ve been working.', act: `Click <b>${I('sync')} Sync</b> whenever the numbers look stale.`},
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
    {el: 'card:Model cost', t: 'Model cost', see: 'How spend splits across the models you used.', get: 'Whether an expensive model is doing routine work.', act: 'A big share on a top-tier model? Check <b>Compare models</b>, and <b>Subagent models</b> for subagent work.'},
    {el: 'card:Project cost', t: 'Project cost', see: 'Spend per repository.', get: 'Which codebase costs the most to work in.', act: 'Click a project to filter every dashboard to it.'},
    {el: 'card:Top 10 most expensive prompts', t: 'Most expensive prompts', see: 'The ten single prompts that cost the most.', get: 'The requests worth rewriting or splitting.', act: 'Click a prompt to see every step it triggered.'},
    {el: 'card:Most expensive sessions', t: 'Most expensive sessions', see: 'The longest-running, highest-cost sessions.', get: 'The marathon sessions where context kept being re-read.', act: 'Click one to see where it grew.'},
    {el: 'card:Waste detection', t: 'Waste at a glance', see: 'A summary of the patterns that burn tokens for nothing.', get: 'An estimate of what you could avoid.', act: 'Open <b>Waste detection</b> for the full findings and evidence.'},
    {el: 'card:Optimization opportunities', t: 'Opportunities', see: 'The top ranked changes, ranked by spend involved.', get: 'What to fix first.', act: 'Open <b>What should I do?</b> for the full list with ready-made prompts.'},
    {el: 'card:Forecast', t: 'Forecast', see: 'Projected spend to the end of the billing period.', get: 'An early read on whether you\'ll go over.', act: 'Open <b>Forecast</b> for the optimistic and pessimistic scenarios.'},
    {el: 'card:FinOps score', t: 'FinOps score', see: 'Your overall score for context share, cache break-even and budget adherence.', get: 'One number for how efficiently you work.', act: 'Open <b>FinOps scorecard</b> to see what each dimension measures.'}],
  advisor: [
    {el: 'card:Biggest optimization opportunity', t: 'Start here', see: 'The change involving the most spend, with the evidence behind it.', get: 'The best use of your next ten minutes.', act: 'Read the estimate, then apply the change.'},
    {el: 'card:What is going well', t: 'Strengths & gaps', see: 'What your habits already do well, and the items that need attention.', get: 'Confirmation of what to keep doing, and where you lose money.', act: 'Work down the <b>Needs attention</b> list.'},
    {el: 'card:All recommendations', t: 'All recommendations', see: 'Every recommendation, ranked by spend involved, each with its reasoning.', get: 'A prioritised to-do list built from your own usage.', act: 'Copy a recommendation\'s prompt straight into your agent.'},
    {el: 'card:Anomalies to inspect', t: 'Anomalies', see: 'Days and sessions that spent far more than your normal.', get: 'Surprises caught before they repeat.', act: 'Click one to see what happened that day.'}],
  agents: [
    {el: 'kpis', t: 'Agent totals', see: 'One tile per agent with its usage for the current filters.', get: 'How spend splits across Claude Code, Codex, Gemini CLI and Cursor.', act: 'Use the agent chips in the header to focus on one.'},
    {el: 'card:Side by side', t: 'Side by side', see: 'Every agent in one table: cost, tokens, requests, sessions.', get: 'A like-for-like comparison of what each agent costs you.', act: 'Compare tokens per request: a high number means large contexts.'},
    {el: 'card:Daily', t: 'Daily by agent', see: 'Cost or tokens per day, split by agent.', get: 'When you switched agents and what that did to spend.', act: 'Use the metric toggle to swap cost for tokens.'},
    {el: 'card:What each agent records', t: 'What each agent records', see: 'Which fields each agent writes to disk on this machine.', get: 'Why some dashboards are hidden for some agents: the data simply isn\'t recorded.', act: 'Check here first when a number reads "not recorded".'}],
  live: [
    {el: 'kpis', t: 'Running now', see: 'Sessions running right now, which are working (spending) at this moment, the largest live context and what live sessions have spent.', get: 'What is costing you money this second.', act: 'Only <b>working</b> sessions consume tokens; idle ones cost again on your next message.'},
    {el: '#live-refresh', t: 'Refresh & the rules', see: 'The note explains what Interrupt, Close and Force kill do, and how non-Claude agents are detected.', get: 'Confidence before you stop something.', act: `Click <b>${I('reload')} Refresh</b> for the latest state.`},
    {el: '.item[data-i]', t: 'A session', see: 'Agent, project, context size, steps, cost and last activity, plus advice when a session gets heavy.', get: 'Control over sessions that are quietly growing expensive.', act: 'Interrupt, Close or Force kill it (each needs two clicks), copy its resume command, or <b>Hand over</b> a Claude session to a fresh one.'}],
  scorecard: [
    {el: 'card:AI FinOps Score', t: 'Your score', see: 'The overall score with a component breakdown: cache use, model mix, waste and budget.', get: 'Where your habits are strong and where they cost money.', act: 'Hover a component to see how it is calculated.'},
    {el: 'card:What is good', t: 'What is good', see: 'The components you already score well on.', get: 'The habits worth keeping.', act: 'Keep these when you change your setup.'},
    {el: 'card:Needs attention', t: 'Needs attention', see: 'The components dragging the score down.', get: 'A short list of what to fix.', act: 'Start at the top: it carries the most weight.'},
    {el: 'card:Biggest opportunity', t: 'Biggest opportunity', see: 'The single change that would move the score most.', get: 'One concrete action grounded in observed spend.', act: 'Apply it, then Sync and re-check the score.'}],
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
    {el: 'kpis', t: 'Model headlines', see: 'How many models you used and what the mix costs.', get: 'A first read on whether the mix is right.', act: `Filter to one model with <b>Model ${I('chevDown')}</b> above.`},
    {el: 'card:Cost share', t: 'Cost share', see: 'Spend split across models.', get: 'The model that owns your bill.', act: 'Check whether that model is doing work a cheaper one could.'},
    {el: 'card:Token share', t: 'Token share', see: 'The same split by tokens instead of money.', get: 'The gap between the two charts is the price difference at work.', act: 'A model with a small token share but a big cost share is your expensive one.'},
    {el: 'card:Model FinOps table', t: 'Model FinOps table', see: 'Per model: cost, tokens, context, output and cost per 1K output.', get: 'What each model really costs for the work you give it.', act: 'Sort by cost per 1K output, then open <b>Subagent models</b> to see which subagents could run cheaper.'},
    {el: 'card:Price table in effect', t: 'Prices in effect', see: 'The per-model prices used for every estimate in this dashboard.', get: 'Transparency: you can check the maths.', act: 'Edit the price file if your rates differ.'}],
  context: [
    {el: 'kpis', t: 'Context & cache', see: 'Average and peak context per request, and how much is served from cache.', get: 'How much re-reading history costs you, and what caching saves.', act: 'Large average context? Clear or compact sessions more often.'},
    {el: 'card:Cost by context size', t: 'Cost by context size', see: 'Spend grouped by how large the context was.', get: 'Proof of how quickly cost climbs with context.', act: 'See how much sits in the largest buckets.'},
    {el: 'card:Caching: with vs without', t: 'Caching, with vs without', see: 'What you paid against what the same tokens would cost with no cache at all.', get: 'A counterfactual, not a saving: nobody would have run it that way.', act: 'A small gap means sessions are restarted too often to build a cache.'},
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
    {el: 'card:Activity breakdown', t: 'Activity breakdown', see: 'Per category: prompts, tokens, cost and the model used.', get: 'Concrete evidence for routing work to a cheaper model.', act: 'Then open <b>Compare models</b> to see the options.'}],
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
  hygiene: [
    {el: 'kpis', t: 'Where spend sat', see: 'Headline shares of your spend, colour-graded: green is low, amber and orange are rising, red means most of the bill sat there. Hover a card for its bands.', get: 'How much of the bill was re-sending a large prefix.', act: 'Walk through each card with Next; the colour is a reading aid, not a verdict.'},
    {el: 'kpi:Spend in requests ≥ 100K', t: 'Spend in requests ≥ 100K context', see: 'The share of spend on requests whose prompt side (context re-sent to the model) was 100K tokens or more.', get: 'How much you paid while carrying a big conversation. Every turn re-sends it, so large context costs on every request.', act: 'Red here with a red "after crossing" card means sessions keep going long after they got big.'},
    {el: 'kpi:Spend in requests ≥ 150K', t: 'Spend in requests ≥ 150K context', see: 'The same share, but for requests at 150K tokens or more.', get: 'The heaviest tier: close to where auto-compaction kicks in.', act: 'Compare with the 100K card: a small gap means sessions that pass 100K usually keep growing to 150K.'},
    {el: 'kpi:Spend after a session first crossed 100K', t: 'Spend after first crossing 100K', see: 'The share of spend that came after a session first reached 100K, even if a later /compact brought it back down. The detail line counts how many sessions ever crossed it.', get: 'How much of your bill is the long tail of big sessions.', act: 'If this is much higher than the "requests ≥ 100K" card, compaction is working but sessions still run long afterwards.'},
    {el: 'kpi:Spend after a session first crossed 150K', t: 'Spend after first crossing 150K', see: 'The same, measured from the first time a session reached 150K.', get: 'Spend in sessions that went all the way to the heavy end.', act: 'Few sessions but a high share? Open them in the ranked list below.'},
    {el: 'kpi:Spend near or over the context window', t: 'Near or over the context window', see: 'The share of spend on requests using 90% or more of the model\'s context window, or going over it.', get: 'Turns that risk truncation or a forced compaction mid-task.', act: 'Anything above amber is worth a look: a /compact or fresh session before you hit the wall keeps the answer quality up.'},
    {el: 'kpi:Sessions in range', t: 'Sessions in range', see: 'How many sessions and main-thread requests the page is based on (blue: context, not graded).', get: 'The sample size behind the percentages.', act: 'Widen the date range if this is small; a handful of sessions makes the shares jumpy.'},
    {el: 'kpi:Spend in range', t: 'Spend in range', see: 'Total estimated main-thread spend the shares are taken from. Subagent turns are left out because they run on their own prefix.', get: 'The dollar base: multiply any share above by this to get dollars.', act: 'Marked Estimated because it uses list prices, not your invoice.'},
    {el: 'card:Context per request', t: 'Context per request', see: 'Prompt-side tokens on every request of one session, in order.', get: 'Where a session grew, and where compaction dropped it back.', act: 'Click a row in the ranked list below to switch session.'},
    {el: 'card:Sessions ranked', t: 'Sessions', see: 'Each session\'s context trajectory and what it spent after crossing the threshold.', get: 'The sessions where a fresh start would have mattered most.', act: 'Click a row for its trajectory; alt-click to open the session.'}],
  waste: [
    {el: 'kpis', t: 'Waste headlines', see: 'Estimated excess, exposed spend, and how many prompts, sessions and rules are involved.', get: 'An honest estimate of avoidable spend.', act: '<b>Exposed</b> is what flagged work cost in total; <b>excess</b> is how much more than a fair baseline.'},
    {el: 'card:High waste', t: 'High waste', see: 'The rules that fired hardest: repeated reads, retries, stale sessions.', get: 'The costly patterns, each with the baseline it is measured against.', act: 'Open <b>Show flagged items</b> to see the evidence, then fix these first.'},
    {el: 'card:Optimization opportunities', t: 'Medium findings', see: 'Patterns worth changing but not urgent.', get: 'The next tier of opportunities.', act: 'Batch these into one config change.'},
    {el: 'card:Low-priority observations', t: 'Low-priority observations', see: 'Small findings, kept for completeness.', get: 'Context for the numbers above.', act: 'Skim them; act only if one matches a habit you want to change.'}],
  jev: [
    {el: 'card:What is Jev?', t: 'What Jev is', see: 'A fast, cheap AI that only makes decisions: pick one, yes or no, a score, a value.', get: 'Cheaper, faster apps and scripts, wherever an AI call only decides something.', act: 'Read the three lines. Jev does not make Claude Code itself cheaper.'},
    {el: 'card:Set up Jev', t: 'Install and key', see: 'One click installs the Jev plugin for Claude Code; the key is saved in Claude Code\'s settings.', get: 'Claude knows how to use Jev when you build something.', act: 'Press Install, check the two commands, then Confirm. Paste your key and Save.'},
    {el: 'card:Where Jev fits', t: 'Where Jev fits', see: 'Your prompts that were really just a decision, and what they would cost on Jev.', get: 'An honest number before you change anything.', act: 'If it is tiny, Jev belongs in your apps, not your coding sessions.'}],
  freemodels: [
    {el: 'card:How to use and test a free model', t: 'How it works', see: 'How to plug a local or free cloud model into Claude Code, and how to test it.', get: 'Zero-cost options for simple or private work.', act: 'Read the RAM guidance before you pick a model.'},
    {el: 'card1', t: 'A model', see: 'Each model with its size, RAM needs, strengths and limits.', get: 'A realistic idea of what runs on your machine.', act: `Click <b>${I('plus')} Add</b> on a model that fits your RAM.`}],
  subagents: [
    {el: 'kpis', t: 'The ceiling', see: 'What subagents cost, and their share of your Claude Code spend.', get: 'An upper bound on what routing subagents can change at all.', act: 'If the share is small, your money is in the main conversation; see <b>Context hygiene</b>.'},
    {el: 'card:By type and model', t: 'Measured per run', see: 'Each subagent type, the models it ran on, and the median cost, turns, output and context per run.', get: 'A cheaper model suggested only when it has enough runs and really cost less per run.', act: 'Read the line under each type. Custom agents get the exact model: line to add.'},
    {el: 'card:Experiment', t: 'Experiment', see: 'Run subagents on one model for a while, then compare before and after.', get: 'Evidence from your own work instead of a guess.', act: 'Pick a model, Start, check the change it makes, then Confirm. Stop puts it back.'}],
  compare: [
    {el: 'card0', t: 'Models side by side', see: 'Price, context window, ratings and your own usage per model.', get: 'A clear pick for each kind of work.', act: 'Sort by output price, the one that usually dominates the bill.'},
    {el: 'card:Use Claude for', t: 'Use the big model for', see: 'The work that genuinely needs a top model.', get: 'Where paying more actually pays off.', act: 'Keep multi-file and agentic work here.'},
    {el: 'card:Use a free model for', t: 'Use a free model for', see: 'The work a free or local model handles fine.', get: 'The safe places to spend nothing.', act: 'Move throwaway snippets and explanations here.'},
    {el: 'card:Try it on your own work', t: 'Try it yourself', see: 'A short recipe for comparing models on a task of your own.', get: 'Your own evidence instead of someone\'s benchmark.', act: 'Run the same small task on both and compare the result.'}],
  toolkit: [
    {el: 'card:MCP servers for work you repeat', t: 'MCP suggestions', see: 'Servers suggested from the work your prompts repeat.', get: 'Fewer manual steps and smaller prompts.', act: 'Add one, then check its context cost in <b>Who used the tokens</b>.'},
    {el: 'card:Skills from what you repeat', t: 'Skill suggestions', see: 'Skills drafted from the instructions you keep retyping.', get: 'Repetition moved out of your prompts.', act: 'Create a suggested skill in one click.'}],
  recommendations: [
    {el: 'card:Biggest optimization opportunity', t: 'Start here', see: 'The change involving the most spend, with the evidence behind it.', get: 'The best single thing to do next.', act: 'Apply it, then Sync and re-check.'},
    {el: 'card:What is going well', t: 'Strengths & gaps', see: 'What already works, and what needs attention.', get: 'What to keep and what to change.', act: 'Work down the <b>Needs attention</b> list.'},
    {el: 'card:All recommendations', t: 'All recommendations', see: 'Every recommendation with its spend involved and reasoning.', get: 'A prioritised list of what to change.', act: 'Start with the highest spend involved.'},
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
    {el: 'card:Set your limits', t: 'Configure budgets', see: 'Your budget lines, limits and alert thresholds.', get: 'Numbers that make the forecast and burn dashboards meaningful.', act: 'Edit a budget and save; every dashboard picks it up.'},
    {el: 'card:Set your limits', t: 'Suggestions and shorthand', see: 'Chips under each amount suggest values from your own last 30 days and session sizes; amounts accept 20M, 500k or $3,000.', get: 'A sensible budget in one click, and a clear message when a value will not work.', act: 'Click a suggestion, adjust it, and Save.'},
    {el: 'card:Set your limits', t: 'Session guard', see: 'A token budget for each Claude Code session, warn percentages, what happens after you approve, per-project overrides, and the Live warnings box (optional).', get: 'A warning while a session grows, and a pause for your approval once it reaches its budget.', act: 'Set a budget, save, then, if you want live warnings, <b>Install</b> in the Live warnings box. It is optional. It applies to new sessions.'}],
  settings: [
    {el: 'card:API keys', t: 'API keys', see: 'Optional keys for the Anthropic Admin API and Cursor.', get: 'The Billed vs local page, without the terminal.', act: 'Paste a key and press Save key. It is never shown again, only its last four characters.'}],
  cloud: [
    {el: 'kpis', t: 'Billed vs local', see: 'What the vendor billed the whole organisation next to what this machine recorded.', get: 'The gap: usage from other machines, other members, or work off this machine.', act: `Click <b>${I('cloudDown')} Refresh from APIs</b> to fetch — this is the only page that goes online.`},
    {el: 'card:Set up the APIs', t: 'Set up the APIs', see: 'Which provider keys were found, and how to get each one.', get: 'Org-wide Claude Code usage per user, and Cursor team spend.', act: 'Add a key on the Settings page, or run claude-finops --set-key.'},
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
  // 'kpi:Label start' — one KPI card, matched by the start of its label (badge text ignored).
  if (el.startsWith('kpi:')) {
    const want = el.slice(4).trim().toLowerCase();
    for (const k of page?.querySelectorAll('.kpi') || [])
      if ((k.querySelector('.l')?.firstChild?.textContent || '').trim().toLowerCase().startsWith(want)) return k;
    return null;
  }
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
      <button class="tour-x" data-t="skip" title="Close (Esc)">${I('x')}</button></div>
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

