/* Minimal dependency-free SVG chart layer.
   Every chart ships a hover/tooltip layer; every multi-series chart ships a legend. */
const NS = 'http://www.w3.org/2000/svg';
export const SERIES = ['--s1','--s2','--s3','--s4','--s5','--s6','--s7','--s8'];
export const seriesVar = i => `var(${SERIES[i % SERIES.length]})`;

let tipEl = null;
function tip() {
  if (!tipEl) { tipEl = document.createElement('div'); tipEl.className = 'tip';
    tipEl.style.display = 'none'; document.body.appendChild(tipEl); }
  return tipEl;
}
export function showTip(html, x, y) {
  const t = tip(); t.innerHTML = html; t.style.display = 'block';
  const r = t.getBoundingClientRect();
  let left = x + 14, top = y + 14;
  if (left + r.width > innerWidth - 8) left = x - r.width - 14;
  if (top + r.height > innerHeight - 8) top = innerHeight - r.height - 8;
  t.style.left = Math.max(8, left) + 'px'; t.style.top = Math.max(8, top) + 'px';
}
export function hideTip() { if (tipEl) tipEl.style.display = 'none'; }

const el = (n, a = {}, kids = []) => {
  const e = document.createElementNS(NS, n);
  for (const k in a) if (a[k] !== undefined && a[k] !== null) e.setAttribute(k, a[k]);
  for (const c of [].concat(kids)) e.appendChild(typeof c === 'string'
    ? document.createTextNode(c) : c);
  return e;
};
const nice = v => {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const r = v / p;
  return (r <= 1 ? 1 : r <= 2 ? 2 : r <= 2.5 ? 2.5 : r <= 5 ? 5 : 10) * p;
};
export const fmtUSD = v => v == null ? '—' :
  (Math.abs(v) >= 1000 ? '$' + v.toLocaleString(undefined, {maximumFractionDigits: 0})
   : Math.abs(v) >= 1 ? '$' + v.toFixed(2) : '$' + v.toFixed(v < 0.01 ? 4 : 3));
export const fmtNum = v => v == null ? '—' :
  Math.abs(v) >= 1e9 ? (v/1e9).toFixed(2) + 'B' :
  Math.abs(v) >= 1e6 ? (v/1e6).toFixed(1) + 'M' :
  Math.abs(v) >= 1e3 ? (v/1e3).toFixed(1) + 'K' : Math.round(v).toLocaleString();
export const fmtInt = v => v == null ? '—' : Math.round(v).toLocaleString();
export const fmtPct = (v, d = 1) => v == null ? '—' : v.toFixed(d) + '%';

function axisLeft(g, y, max, W, ticks = 4, fmt = fmtNum) {
  for (let i = 0; i <= ticks; i++) {
    const v = max * i / ticks, yy = y(v);
    g.appendChild(el('line', {x1: 0, x2: W, y1: yy, y2: yy, class: 'gl'}));
    g.appendChild(el('text', {x: -7, y: yy + 3.5, 'text-anchor': 'end'}, fmt(v)));
  }
}

/* ---------- time series: line / area / stacked bars ---------- */
export function timeSeries(host, opts) {
  const {rows, x: xk, series, fmt = fmtNum, type = 'area', height = 230,
         onClick, xLabel = d => d} = opts;
  host.innerHTML = '';
  if (!rows.length) { host.innerHTML = '<div class="empty">No data in range</div>'; return; }
  const off = new Set(opts.hidden || []);
  const active = series.filter(s => !off.has(s.key));
  const W = Math.max(host.clientWidth || 640, 320), H = height;
  const m = {t: 12, r: 14, b: 26, l: 52};
  const iw = W - m.l - m.r, ih = H - m.t - m.b;

  const stacked = type === 'bar' && active.length > 1;
  const totals = rows.map(r => stacked
    ? active.reduce((a, s) => a + (+r[s.key] || 0), 0)
    : Math.max(...active.map(s => +r[s.key] || 0), 0));
  const max = nice(Math.max(...totals, 0) || 1);
  const y = v => m.t + ih - (v / max) * ih;
  const n = rows.length;
  const bw = Math.max(2, Math.min(34, iw / n * 0.72));
  const cx = i => m.l + (n === 1 ? iw / 2 : (i + 0.5) * (iw / n));
  const px = i => m.l + (n === 1 ? iw / 2 : i * (iw / (n - 1)));

  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, height: H, role: 'img'});
  const g = el('g', {transform: `translate(0,0)`});
  const gi = el('g', {transform: `translate(${m.l},0)`});
  axisLeft(gi, y, max, iw, 4, fmt);
  svg.appendChild(gi);
  svg.appendChild(el('line', {x1: m.l, x2: m.l + iw, y1: y(0), y2: y(0), class: 'ax'}));

  if (type === 'bar') {
    rows.forEach((r, i) => {
      let base = 0;
      active.forEach((s, si) => {
        const v = +r[s.key] || 0; if (!v) return;
        const y0 = y(base + v), y1 = y(base);
        const h = Math.max(1, y1 - y0 - (stacked ? 2 : 0)); // 2px surface gap between segments
        const isTop = stacked ? (base + v >= totals[i] - 1e-9) : true;
        const rect = el('rect', {
          x: cx(i) - bw / 2, y: y0, width: bw, height: h,
          rx: isTop ? Math.min(4, bw / 2) : 0,
          fill: s.color || seriesVar(series.indexOf(s))});
        g.appendChild(rect); base += v;
      });
    });
  } else {
    active.forEach((s) => {
      const idx = series.indexOf(s), col = s.color || seriesVar(idx);
      const pts = rows.map((r, i) => [px(i), y(+r[s.key] || 0)]);
      const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ');
      if (type === 'area' && active.length === 1) {
        g.appendChild(el('path', {d: `${d} L${pts[pts.length-1][0]},${y(0)} L${pts[0][0]},${y(0)} Z`,
          fill: col, 'fill-opacity': .13}));
      }
      g.appendChild(el('path', {d, fill: 'none', stroke: col, 'stroke-width': 2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
      if (n <= 40) pts.forEach(p => g.appendChild(el('circle', {
        cx: p[0], cy: p[1], r: 3.2, fill: col, stroke: 'var(--surface)', 'stroke-width': 2})));
    });
  }
  svg.appendChild(g);

  // x labels — thinned to avoid collisions
  const step = Math.max(1, Math.ceil(n / Math.max(3, Math.floor(iw / 74))));
  rows.forEach((r, i) => {
    if (i % step && i !== n - 1) return;
    svg.appendChild(el('text', {x: (type === 'bar' ? cx(i) : px(i)), y: H - 8,
      'text-anchor': 'middle'}, xLabel(r[xk])));
  });

  // hover layer
  const cross = el('line', {y1: m.t, y2: m.t + ih, class: 'crosshair', opacity: 0});
  svg.appendChild(cross);
  const hit = el('rect', {x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent',
    style: onClick ? 'cursor:pointer' : ''});
  svg.appendChild(hit);
  const idxAt = ev => {
    const b = svg.getBoundingClientRect();
    const rx = (ev.clientX - b.left) * (W / b.width);
    return Math.max(0, Math.min(n - 1, Math.round((rx - m.l) / (iw / (type === 'bar' ? n : Math.max(n - 1, 1)))
      - (type === 'bar' ? 0.5 : 0))));
  };
  hit.addEventListener('mousemove', ev => {
    const i = idxAt(ev), r = rows[i];
    cross.setAttribute('opacity', 1);
    cross.setAttribute('x1', type === 'bar' ? cx(i) : px(i));
    cross.setAttribute('x2', type === 'bar' ? cx(i) : px(i));
    const body = active.map(s => `<div class="row"><span class="k">
      <span class="swatch" style="background:${s.color || seriesVar(series.indexOf(s))}"></span>
      ${s.label}</span><span class="v">${(s.fmt || fmt)(+r[s.key] || 0)}</span></div>`).join('');
    showTip(`<div class="t">${xLabel(r[xk])}</div>${body}`, ev.clientX, ev.clientY);
  });
  hit.addEventListener('mouseleave', () => { hideTip(); cross.setAttribute('opacity', 0); });
  if (onClick) hit.addEventListener('click', ev => onClick(rows[idxAt(ev)]));
  host.appendChild(svg);
}

/* ---------- horizontal ranked bars ---------- */
export function barsH(host, opts) {
  const {rows, label, value, fmt = fmtUSD, height, color, onClick, sub, max: fixedMax} = opts;
  host.innerHTML = '';
  if (!rows.length) { host.innerHTML = '<div class="empty">No data in range</div>'; return; }
  const rh = 26, W = Math.max(host.clientWidth || 520, 300);
  const lw = Math.min(210, Math.max(110, W * 0.3)), vw = 82;
  const H = height || rows.length * rh + 6;
  const iw = Math.max(40, W - lw - vw - 10);
  const max = fixedMax || Math.max(...rows.map(value), 1e-9);
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, height: H});
  rows.forEach((r, i) => {
    const y0 = i * rh + 4, v = value(r), w = Math.max(2, (v / max) * iw);
    const col = typeof color === 'function' ? color(r, i) : (color || seriesVar(0));
    const grp = el('g', {style: onClick ? 'cursor:pointer' : ''});
    grp.appendChild(el('rect', {x: 0, y: y0 - 4, width: W, height: rh, fill: 'transparent'}));
    const t = el('text', {x: 0, y: y0 + 13, class: 'val'}, label(r).length > 30
      ? label(r).slice(0, 29) + '…' : label(r));
    grp.appendChild(t);
    grp.appendChild(el('rect', {x: lw, y: y0 + 4, width: w, height: 11, rx: 4, fill: col}));
    grp.appendChild(el('text', {x: W - 2, y: y0 + 13, 'text-anchor': 'end', class: 'val'}, fmt(v)));
    grp.addEventListener('mousemove', ev => showTip(
      `<div class="t">${label(r)}</div><div class="row"><span class="k">
       <span class="swatch" style="background:${col}"></span>Value</span>
       <span class="v">${fmt(v)}</span></div>${sub ? sub(r) : ''}`, ev.clientX, ev.clientY));
    grp.addEventListener('mouseleave', hideTip);
    if (onClick) grp.addEventListener('click', () => onClick(r));
    svg.appendChild(grp);
  });
  host.appendChild(svg);
}

/* ---------- donut ---------- */
export function donut(host, opts) {
  const {rows, label, value, fmt = fmtUSD, size = 190, centerLabel, centerValue, onClick,
         color} = opts;
  host.innerHTML = '';
  const total = rows.reduce((a, r) => a + value(r), 0);
  if (!total) { host.innerHTML = '<div class="empty">No data in range</div>'; return; }
  const R = size / 2, r0 = R * 0.60, cx = R, cy = R;
  const svg = el('svg', {viewBox: `0 0 ${size} ${size}`, height: size, width: size,
    style: 'width:' + size + 'px;flex:none'});
  let a0 = -Math.PI / 2;
  rows.forEach((row, i) => {
    const v = value(row); if (v <= 0) return;
    const a1 = a0 + (v / total) * Math.PI * 2;
    const gap = 0.012; // 2px-equivalent surface gap between segments
    const s = a0 + gap, e = Math.max(s, a1 - gap);
    const P = (ang, rad) => [cx + Math.cos(ang) * rad, cy + Math.sin(ang) * rad];
    const large = (e - s) > Math.PI ? 1 : 0;
    const [x1, y1] = P(s, R), [x2, y2] = P(e, R), [x3, y3] = P(e, r0), [x4, y4] = P(s, r0);
    // colour follows the entity, never its rank in this particular slice order
    const col = typeof color === 'function' ? color(row, i) : seriesVar(i);
    const p = el('path', {d: `M${x1},${y1} A${R},${R} 0 ${large} 1 ${x2},${y2}
      L${x3},${y3} A${r0},${r0} 0 ${large} 0 ${x4},${y4} Z`, fill: col,
      style: onClick ? 'cursor:pointer' : ''});
    p.addEventListener('mousemove', ev => showTip(
      `<div class="t">${label(row)}</div><div class="row"><span class="k">
       <span class="swatch" style="background:${col}"></span>Estimated</span>
       <span class="v">${fmt(v)}</span></div><div class="row"><span class="k">Share</span>
       <span class="v">${(100 * v / total).toFixed(1)}%</span></div>`, ev.clientX, ev.clientY));
    p.addEventListener('mouseleave', hideTip);
    if (onClick) p.addEventListener('click', () => onClick(row));
    svg.appendChild(p);
    a0 = a1;
  });
  if (centerValue) {
    svg.appendChild(el('text', {x: cx, y: cy - 2, 'text-anchor': 'middle', class: 'val',
      style: 'font-size:19px;font-weight:660;fill:var(--text)'}, centerValue));
    svg.appendChild(el('text', {x: cx, y: cy + 14, 'text-anchor': 'middle',
      style: 'font-size:10px'}, centerLabel || ''));
  }
  host.appendChild(svg);
}

/* ---------- gauge ---------- */
export function gauge(host, {pct, status = 'healthy', label = '', size = 176}) {
  host.innerHTML = '';
  const W = size, H = size * 0.62, R = size * 0.42, cx = W / 2, cy = H - 6, sw = 13;
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, height: H});
  const arc = (frac, color, op) => {
    const a = Math.PI * Math.min(Math.max(frac, 0), 1);
    const x1 = cx - R, y1 = cy, x2 = cx - Math.cos(a) * R, y2 = cy - Math.sin(a) * R;
    return el('path', {d: `M${x1},${y1} A${R},${R} 0 ${a > Math.PI ? 1 : 0} 1 ${x2},${y2}`,
      fill: 'none', stroke: color, 'stroke-width': sw, 'stroke-linecap': 'round',
      'stroke-opacity': op});
  };
  svg.appendChild(arc(1, 'var(--surface-3)', 1));
  svg.appendChild(arc(Math.min(pct, 100) / 100, `var(--${status === 'healthy' ? 'good'
    : status === 'high' ? 'warning' : status === 'approaching' ? 'serious' : 'critical'})`, 1));
  svg.appendChild(el('text', {x: cx, y: cy - 12, 'text-anchor': 'middle',
    style: 'font-size:25px;font-weight:680;fill:var(--text)'}, pct.toFixed(0) + '%'));
  svg.appendChild(el('text', {x: cx, y: cy + 4, 'text-anchor': 'middle',
    style: 'font-size:10px'}, label));
  host.appendChild(svg);
}

/* ---------- forecast fan ---------- */
export function forecastFan(host, {history, scenarios, remainingDays, height = 230}) {
  host.innerHTML = '';
  if (!history.length) { host.innerHTML = '<div class="empty">Not enough history</div>'; return; }
  const W = Math.max(host.clientWidth || 640, 320), H = height;
  const m = {t: 12, r: 14, b: 26, l: 56};
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  let cum = 0;
  const hist = history.map(d => ({day: d.day, v: (cum += d.cost)}));
  const base = cum, n = hist.length, total = n + remainingDays;
  // scenarios may only carry 'expected' (insufficient_history): iterate only the
  // keys actually present so a caller that forgets to check that flag cannot crash.
  const keys = ['conservative', 'expected', 'high'].filter(k => scenarios[k]);
  const paths = {};
  for (const k of keys) {
    const rate = scenarios[k].daily_rate;
    paths[k] = Array.from({length: remainingDays + 1}, (_, i) => base + rate * i);
  }
  const hasBand = paths.conservative && paths.high;
  const max = nice(Math.max(base, ...(paths.high || paths.expected)) || 1);
  const X = i => m.l + (total <= 1 ? 0 : i * (iw / (total - 1)));
  const Y = v => m.t + ih - (v / max) * ih;
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, height: H});
  const gi = el('g', {transform: `translate(${m.l},0)`});
  axisLeft(gi, Y, max, iw, 4, fmtUSD); svg.appendChild(gi);

  if (hasBand) {
    const band = [];
    paths.high.forEach((v, i) => band.push(`${i ? 'L' : 'M'}${X(n - 1 + i)},${Y(v)}`));
    for (let i = paths.conservative.length - 1; i >= 0; i--)
      band.push(`L${X(n - 1 + i)},${Y(paths.conservative[i])}`);
    svg.appendChild(el('path', {d: band.join(' ') + ' Z', fill: 'var(--s1)', 'fill-opacity': .13}));
  }

  const line = (pts, color, dash) => el('path', {
    d: pts.map((p, i) => (i ? 'L' : 'M') + p[0] + ',' + p[1]).join(' '), fill: 'none',
    stroke: color, 'stroke-width': 2, 'stroke-dasharray': dash || null,
    'stroke-linecap': 'round'});
  svg.appendChild(line(hist.map((d, i) => [X(i), Y(d.v)]), 'var(--s1)'));
  svg.appendChild(line(paths.expected.map((v, i) => [X(n - 1 + i), Y(v)]), 'var(--s1)', '5 4'));

  const labels = [['Actual to date', 'var(--s1)', fmtUSD(base), X(n - 1), Y(base)],
    ['Expected', 'var(--s1)', fmtUSD(paths.expected.at(-1)), X(total - 1), Y(paths.expected.at(-1))]];
  labels.forEach(([t, c, v, x, y]) => {
    svg.appendChild(el('circle', {cx: x, cy: y, r: 3.5, fill: c, stroke: 'var(--surface)',
      'stroke-width': 2}));
  });
  if (paths.high) {
    svg.appendChild(el('text', {x: X(total - 1), y: Y(paths.high.at(-1)) - 6, 'text-anchor': 'end',
      class: 'val'}, 'High ' + fmtUSD(paths.high.at(-1))));
  }
  svg.appendChild(el('text', {x: X(total - 1), y: Y(paths.expected.at(-1)) - 6, 'text-anchor': 'end',
    class: 'val'}, 'Expected ' + fmtUSD(paths.expected.at(-1))));
  svg.appendChild(el('text', {x: X(0), y: H - 8}, history[0].day));
  svg.appendChild(el('text', {x: X(n - 1), y: H - 8, 'text-anchor': 'middle'}, 'today'));
  svg.appendChild(el('text', {x: X(total - 1), y: H - 8, 'text-anchor': 'end'}, 'period end'));

  const hit = el('rect', {x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent'});
  hit.addEventListener('mousemove', ev => {
    const b = svg.getBoundingClientRect();
    const i = Math.max(0, Math.min(total - 1, Math.round(((ev.clientX - b.left) * (W / b.width) - m.l)
      / (iw / (total - 1)))));
    if (i < n) showTip(`<div class="t">${hist[i].day} · actual</div>
      <div class="row"><span class="k">Cumulative</span><span class="v">${fmtUSD(hist[i].v)}</span></div>`,
      ev.clientX, ev.clientY);
    else {
      const j = i - n + 1;
      showTip(`<div class="t">Day +${j} · forecast</div>` + keys
        .map(k => `<div class="row"><span class="k">${k}</span>
          <span class="v">${fmtUSD(paths[k][j])}</span></div>`).join(''), ev.clientX, ev.clientY);
    }
  });
  hit.addEventListener('mouseleave', hideTip);
  svg.appendChild(hit);
  host.appendChild(svg);
}

/* ---------- sparkline ---------- */
export function spark(host, values, color = 'var(--s1)', h = 30) {
  host.innerHTML = '';
  if (!values.length) return;
  const W = Math.max(host.clientWidth || 120, 60), max = Math.max(...values, 1e-9);
  const svg = el('svg', {viewBox: `0 0 ${W} ${h}`, height: h});
  const pts = values.map((v, i) => [i * (W / Math.max(values.length - 1, 1)), h - 2 - (v / max) * (h - 4)]);
  svg.appendChild(el('path', {d: pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ','
    + p[1].toFixed(1)).join(' '), fill: 'none', stroke: color, 'stroke-width': 1.6,
    'stroke-linejoin': 'round'}));
  host.appendChild(svg);
}

/* ---------- legend ---------- */
export function legend(host, items, onToggle) {
  host.innerHTML = '';
  items.forEach(it => {
    const s = document.createElement('span');
    s.className = 'it' + (it.off ? ' off' : '');
    s.innerHTML = `<span class="swatch" style="background:${it.color}"></span>${it.label}`;
    if (onToggle) { s.style.cursor = 'pointer'; s.onclick = () => onToggle(it.key); }
    host.appendChild(s);
  });
}
