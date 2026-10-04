// azir 办公室的 JEV 视图：执行协调在上，JEV 判断在中间，执行工位按型号分五区。
// 派发判断先转一次老虎机，停在 JEV 选中的型号上，再显示概率条。
// 任务卡沿固定连线飞行：执行协调 → JEV → 型号区，完成后沿原路飞回。
// 画布按终端格子排版，中文占两格；不输出任何从记录里读到的控制字符。
import { width, truncate, stripAnsi } from './text.mjs';
import { modelZone, officeWorkers, SLOT_MS } from './azir-events.mjs';
import { pose, runningScreen } from './sprites.mjs';
import { identity } from './theme.mjs';

const P = {
  bg: '#12161e', panel: '#1a202b', ink: '#e6ebf5', soft: '#aab4c6', faint: '#6c7890', line: '#2e3848',
  manager: '#f0a66f', jevA: '#a77bff', jevB: '#ff6fb5', sol: '#6cb6ff', luna: '#5fe3a1', opus: '#ff9acf',
  gemini: '#8a9bff', grok: '#e3e8f2', other: '#8b97aa', working: '#5ce08a', blocked: '#ffc14d', done: '#4fd6e8', unknown: '#c48bff', idle: '#8b97aa',
  failed: '#ffc14d', desk: '#8a6a45', screen: '#0a0f18', handback: '#ffc14d', close: '#5ce08a',
};
const ZONES = {
  sol: { name: '6.1 Sol 区', purpose: '复杂开发和审查', price: '每百万 $2.00 / $10.00', short: '6.1 Sol' },
  luna: { name: 'Luna 区', purpose: '简单只读和改文档', price: '每百万 $0.10 / $0.50', short: 'Luna' },
  opus: { name: 'Opus 区', purpose: '规划和前端', price: 'Claude 订阅', short: 'Opus' },
  gemini: { name: 'Gemini 区', purpose: '中文写作和润色', price: 'Google 订阅', short: 'Gemini' },
  grok: { name: 'Grok 区', purpose: '联网搜索和 X 帖子', price: 'SuperGrok 订阅', short: 'Grok' },
  other: { name: '其他窗格', purpose: '未经 JEV', price: '', short: '其他' },
};
const ZONE_ORDER = ['sol', 'luna', 'opus', 'gemini', 'grok'];

// 执行者图标：行内一个字符宽的品牌色小图标，卡片里 3 行 × 7 列的半格方块像素标志。
const ICON = {
  sol: { ch: '❂', from: '#10a37f', to: '#10a37f' },
  luna: { ch: '❂', from: '#8eeaf2', to: '#8eeaf2' },
  opus: { ch: '✻', from: '#d97757', to: '#d97757' },
  gemini: { ch: '✦', from: '#4285f4', to: '#a142f4' },
  grok: { ch: '⊘', from: '#ffffff', to: '#ffffff' },
  other: { ch: '·', from: '#8b97aa', to: '#8b97aa' },
};
const LOGO = {
  // OpenAI 系：六边形环，中间一点。
  openai: ['..###..', '.#...#.', '#..#..#', '#..#..#', '.#...#.', '..###..'],
  // Claude：放射状星芒。
  opus: ['#..#..#', '.#.#.#.', '..###..', '#######', '..###..', '.#.#.#.'],
  // Gemini：四角星。
  gemini: ['...#...', '...#...', '..###..', '#######', '..###..', '...#...'],
  // Grok：圆环加斜杠。
  grok: ['..###.#', '.#...#.', '#...#.#', '#.#...#', '.#...#.', '#.###..'],
};
const iconColor = (zone, t = 0.5) => mix(ICON[zone]?.from || P.other, ICON[zone]?.to || P.other, t);
function putIcon(c, x, y, zone, opts = {}) {
  const icon = ICON[zone] || ICON.other;
  return c.put(x, y, icon.ch, iconColor(zone), { ...opts, bold: true });
}
function drawLogo(c, x, y, zone, { bg = P.panel, glow = 0, dim = 0 } = {}) {
  const art = LOGO[zone === 'sol' || zone === 'luna' ? 'openai' : zone];
  if (!art) return;
  for (let r = 0; r < 3; r++) {
    for (let k = 0; k < 7; k++) {
      const top = art[r * 2][k] === '#', bottom = art[r * 2 + 1][k] === '#';
      if (!top && !bottom) continue;
      const tint = mix(mix(iconColor(zone, k / 6), '#ffffff', glow), bg, dim);
      c.put(x + k, y + r, top && bottom ? '█' : top ? '▀' : '▄', tint, { bg });
    }
  }
}
const STATUS_NAME = { blocked: '需要你', working: '工作中', done: '完成', unknown: '拿不准', idle: '空闲', failed: '失败' };
const WRAP_LABEL = { close_keep: '直接收尾', close_and_clean: '收尾并清理', keep: '交回' };
// 省钱估算的假设：每件执行任务约 20 万输入、2 万输出 token。
const EST_IN = 0.2, EST_OUT = 0.02;
const SOL_SAVING = EST_IN * (2.0 - 0.1) + EST_OUT * (10.0 - 0.5);

const pct = (n) => (n == null || !Number.isFinite(Number(n)) ? '—' : `${Math.round(Number(n) * 100)}%`);
const money = (n) => `$${Number(n || 0).toFixed(n >= 1 ? 2 : 4)}`;
const hex = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
const mix = (a, b, t) => '#' + hex(a).map((v, i) => Math.round(v + (hex(b)[i] - v) * Math.max(0, Math.min(1, t)))
  .toString(16).padStart(2, '0')).join('');
const clean = (v) => stripAnsi(String(v ?? '')).replace(/[\x00-\x1f\x7f]/g, ' ');

function canvas(cols, rows) {
  const blank = () => ({ ch: ' ', fg: P.ink, bg: P.bg, bold: false });
  const grid = Array.from({ length: rows }, () => Array.from({ length: cols }, blank));
  function cell(x, y, ch, fg, bg, bold) {
    const row = grid[y];
    if (row[x].ch === '' && x > 0) row[x - 1] = { ...row[x - 1], ch: ' ' };
    if (width(row[x].ch) === 2 && x + 1 < cols) row[x + 1] = { ...row[x + 1], ch: ' ' };
    row[x] = { ch, fg, bg: bg ?? row[x].bg, bold };
  }
  // fg 可以是函数：按列返回颜色，用来画渐变。
  function put(x, y, value, fg = P.ink, { limit = cols, bg, bold = false } = {}) {
    if (y < 0 || y >= rows) return 0;
    let at = Math.round(x);
    const text = truncate(clean(value), Math.max(0, Math.min(limit, cols - Math.max(0, at))));
    for (const ch of text) {
      const w = width(ch);
      if (!w) continue;
      if (at >= 0 && at + w <= cols) {
        const color = typeof fg === 'function' ? fg(at) : fg;
        cell(at, y, ch, color, bg, bold);
        if (w === 2) {
          // 右半格原来若是另一个宽字的左半，先把那个宽字的右半清掉，否则这一行会多出一格。
          if (width(grid[y][at + 1].ch) === 2 && at + 2 < cols) grid[y][at + 2] = { ...grid[y][at + 2], ch: ' ' };
          grid[y][at + 1] = { ch: '', fg: color, bg: bg ?? grid[y][at + 1].bg, bold };
        }
      }
      at += w;
    }
    return width(text);
  }
  function fillRect(x, y, w, h, bg) {
    for (let r = y; r < y + h && r < rows; r++) for (let c = x; c < x + w && c < cols; c++) if (r >= 0 && c >= 0) grid[r][c] = { ch: ' ', fg: P.ink, bg, bold: false };
  }
  function box(x, y, w, h, tint, { bg, heavy = false } = {}) {
    if (bg) fillRect(x, y, w, h, bg);
    const [tl, tr, bl, br, hz, vt] = heavy ? ['┏', '┓', '┗', '┛', '━', '┃'] : ['╭', '╮', '╰', '╯', '─', '│'];
    put(x, y, tl + hz.repeat(w - 2) + tr, tint, { bg });
    for (let r = 1; r < h - 1; r++) { put(x, y + r, vt, tint, { bg }); put(x + w - 1, y + r, vt, tint, { bg }); }
    put(x, y + h - 1, bl + hz.repeat(w - 2) + br, tint, { bg });
  }
  function get(x, y) { return grid[y]?.[x]; }
  function lines() {
    const sgr = (c) => `\x1b[0;${c.bold ? '1;' : ''}38;2;${hex(c.fg).join(';')};48;2;${hex(c.bg).join(';')}m`;
    return grid.map((row) => {
      let out = '', last = '';
      for (const c of row) {
        if (c.ch === '') continue;
        const style = sgr(c);
        if (style !== last) { out += style; last = style; }
        out += c.ch;
      }
      return out + '\x1b[0m';
    });
  }
  return { put, box, fillRect, get, lines, cols, rows };
}

export function optionProbabilities(decision) {
  const options = Array.isArray(decision?.options) ? decision.options.map((key) => [key, '']) : Object.entries(decision?.options || {});
  const choice = decision?.jev_choice ?? decision?.answer;
  const explicit = options.length > 0 && options.every(([key]) => Number.isFinite(decision?.probabilities?.[key]));
  const numeric = options.length > 0 && options.every(([, v]) => typeof v === 'number' && Number.isFinite(v));
  const conf = Number(decision?.confidence) || 0;
  return options.map(([key, description]) => ({
    key, description: typeof description === 'string' ? description : '', selected: key === choice,
    estimated: !explicit && !numeric,
    probability: Math.max(0, Math.min(1, explicit ? decision.probabilities[key] : numeric ? description
      : key === choice ? conf : (1 - conf) / Math.max(1, options.length - 1))),
  }));
}

const optionLabel = (key) => {
  const zone = modelZone(key);
  return zone === 'other' ? WRAP_LABEL[key] || key : ZONES[zone].name;
};

/* ---------------------------------------------------------------- 顶栏 */

function topBar(c, view, workers, state) {
  const { cols } = c;
  c.fillRect(0, 0, cols, 2, P.panel);
  let x = 1;
  x += c.put(x, 0, ' AZIR OFFICE ', P.bg, { bg: P.sol, bold: true }) + 1;
  x += c.put(x, 0, ' JEV 判断 ', P.bg, { bg: mix(P.jevA, P.jevB, 0.5), bold: true }) + 2;
  const tally = {};
  for (const w of workers) tally[w.status] = (tally[w.status] || 0) + 1;
  for (const key of ['blocked', 'working', 'done', 'unknown']) {
    const text = ` ${tally[key] || 0} ${STATUS_NAME[key]} `;
    if (x + width(text) > cols - 12) break;
    x += c.put(x, 0, text, P.bg, { bg: P[key], bold: true }) + 1;
  }
  const scope = view.showAll ? '显示全部窗格' : `${workers.length} 个 JEV 工位`;
  if (x + width(scope) + 2 < cols - 10) c.put(x + 1, 0, scope, P.soft, { bg: P.panel });
  const clock = new Date(view.now ?? Date.now()).toTimeString().slice(0, 8);
  c.put(cols - 9, 0, clock, P.faint, { bg: P.panel });

  const m = state.todayModels || {};
  const saved = (m.luna || 0) * SOL_SAVING;
  const parts = [
    [`今日 JEV 判断 ${state.todayDecisions} 次`, P.jevA],
    [`Sol ${m.sol || 0} · Luna ${m.luna || 0} · Opus ${m.opus || 0} · Gemini ${m.gemini || 0} · Grok ${m.grok || 0} 件`, P.ink],
    [`直接执行 ${state.todayDirect} 件`, P.close],
    [`交回 ${state.todayHandback} 件`, P.handback],
    [`花费 ${money(state.todayCost)}`, P.soft],
    [`比全用 Sol 省 $${saved.toFixed(2)}`, P.luna],
  ];
  x = 1;
  for (const [text, tint] of parts) {
    if (x + width(text) > cols - 1) break;
    x += c.put(x, 1, text, tint, { bg: P.panel }) + 2;
  }
}

/* ---------------------------------------------------------------- 你、主会话、执行协调、审查 */

const isManager = (p) => /^mgr-/i.test(p.agentName || '');
const isReviewer = (p) => /review/i.test(p.agentName || '');
const managerName = (p) => `${clean(p.agentName).replace(/^mgr-/i, '') || '项目'} 执行协调`;
const reviewerZone = (p) => (/claude/i.test(`${p.kind} ${p.agentName}`) ? 'opus' : 'sol');
const reviewerName = (p) => (reviewerZone(p) === 'opus' ? 'Claude 审查' : 'Sol 审查');

// 一个方框：完整模式画边框和一行内容，折叠模式只画一行「名字  内容」。
function nodeBox(c, box, { title, line, tint, icon, lit = 0, dim = false }) {
  const { x, y, w, h } = box;
  const edge = lit ? mix(tint, '#ffffff', 0.35 * lit) : dim ? P.line : tint;
  if (h >= 3) {
    c.box(x, y, w, h, edge, { bg: P.panel, heavy: lit > 0 });
    let tx = x + 2;
    if (icon) { putIcon(c, tx, y, icon, { bg: P.panel }); tx += 2; }
    c.put(tx, y, ` ${title} `, lit ? P.bg : dim ? P.soft : P.bg, { bg: lit ? mix(tint, '#ffffff', 0.25) : dim ? P.panel : tint, bold: true, limit: w - (tx - x) - 2 });
    c.put(x + 2, y + 1, line, dim ? P.faint : P.ink, { limit: w - 4, bg: P.panel });
    return;
  }
  c.fillRect(x, y, w, 1, P.panel);
  let tx = x;
  tx += c.put(tx, y, '▌', edge, { bg: P.panel });
  if (icon) tx += putIcon(c, tx, y, icon, { bg: P.panel }) + 1;
  tx += c.put(tx, y, title, lit ? mix(tint, '#ffffff', 0.3) : dim ? P.soft : tint, { bg: P.panel, bold: true, limit: x + w - tx }) + 1;
  c.put(tx, y, line, dim ? P.faint : P.soft, { bg: P.panel, limit: x + w - tx });
}

function drawPeople(c, L, state, crew, lit, now) {
  const says = state.says || {};
  const advisorOn = state.advisor && now < state.advisor.until;
  nodeBox(c, L.boss, { title: '你', line: says.boss || '—', tint: GOLD, lit: lit.has('boss') ? 1 : 0 });
  nodeBox(c, L.main, { title: '主会话', line: says.main || '空闲', tint: ICON.opus.from, icon: 'opus', lit: lit.has('main') ? 1 : 0 });
  nodeBox(c, L.advisor, { title: '顾问 · Fable', line: advisorOn ? state.advisor.session : '空闲', tint: '#7ad7ff', lit: advisorOn ? 1 : 0, dim: !advisorOn });
  L.mgrs.forEach((box, i) => {
    const p = crew.managers[i];
    const line = i === 0 ? state.managerText : p?.title || STATUS_NAME[p?.status] || '空闲';
    nodeBox(c, box, { title: p ? managerName(p) : '执行协调', line, tint: P.manager, lit: i === 0 && lit.has('manager') ? 1 : 0, dim: i > 0 });
  });
  if (!L.review) return;
  L.reviews.forEach((box, i) => {
    const p = crew.reviewers[i];
    if (!p) {
      nodeBox(c, box, { title: '录屏证明', line: `${state.proofs || 0} 段`, tint: P.done, lit: lit.has('proof') ? 1 : 0, dim: !state.proofs });
      return;
    }
    const zone = reviewerZone(p);
    const on = lit.has(`review${i}`);
    nodeBox(c, box, { title: reviewerName(p), icon: zone, line: says[`review${i}`] || p.title || STATUS_NAME[p.status] || '空闲',
      tint: P[zone], lit: on ? 1 : 0, dim: !on && p.status !== 'working' });
  });
}

/* ---------------------------------------------------------------- JEV 判断 */

function jevBox(c, L, state, now) {
  const { x, y, w, h } = L.jev;
  const grad = (col) => mix(P.jevA, P.jevB, (col - x) / Math.max(1, w));
  const d = state.lastDecision;
  const slot = slotState(d, now);
  const edge = slot?.flash ? (col) => mix(grad(col), '#ffffff', 0.65) : slot?.win ? (col) => mix(grad(col), '#ffffff', 0.2) : grad;
  c.box(x, y, w, h, edge, { bg: P.panel, heavy: true });
  c.put(x + 2, y, ' JEV 判断 ', P.bg, { bg: slot?.flash ? P.jevB : P.jevA, bold: true });
  const incoming = state.flights.find((f) => f.to === 'jev' && now >= f.startedAt && now < f.startedAt + f.duration);
  const thinking = d?.readyAt != null && now < d.readyAt;
  const inner = w - 4;
  if (!d) {
    c.put(x + 2, y + 2, '等待任务', P.faint, { bg: P.panel });
    return;
  }
  const point = d.point === 'wrapup' ? '收尾判断' : d.point === 'dispatch' ? '派给哪个型号' : '判断';
  const head = `${point} · ${d.question || '未记录问题'}`;
  c.put(x + 13, y, ` ${head} `, P.ink, { limit: w - 16, bg: P.panel, bold: true });
  if (slot) { drawSlot(c, L, d, slot, now, grad); return; }
  if (thinking) {
    const dots = '.'.repeat(1 + (Math.floor(now / 200) % 3));
    const msg = incoming ? '任务正送往 JEV' : `推理中${dots}`;
    c.put(x + 3, y + 2, msg, grad, { bg: P.panel, bold: true });
    const shimmer = Array.from({ length: Math.min(30, inner - 4) }, (_, i) => ((i + Math.floor(now / 80)) % 6 < 2 ? '▰' : '▱')).join('');
    c.put(x + 3, y + 3, shimmer, grad, { bg: P.panel });
    return;
  }
  const opts = optionProbabilities(d).sort((a, b) => b.selected - a.selected || b.probability - a.probability).slice(0, Math.max(1, h - 4));
  const labelW = 14;
  const barW = Math.max(8, Math.min(32, inner - labelW - 8 - 34));
  opts.forEach((o, i) => {
    const row = y + 1 + i;
    const zone = modelZone(o.key);
    const tint = o.selected ? (zone === 'other' ? grad(x + w / 2) : P[zone]) : P.faint;
    let cx = x + 2;
    cx += c.put(cx, row, o.selected ? '▸ ' : '  ', tint, { bg: P.panel, bold: true });
    if (zone !== 'other') { putIcon(c, cx, row, zone, { bg: P.panel }); cx += 2; }
    c.put(cx, row, optionLabel(o.key), tint, { limit: labelW, bg: P.panel, bold: o.selected });
    cx += labelW + 1;
    const fill = Math.round(o.probability * barW);
    for (let k = 0; k < barW; k++) {
      c.put(cx + k, row, k < fill ? '█' : '·', k < fill ? (o.selected ? mix(P.jevA, P.jevB, k / barW) : P.line) : P.line, { bg: P.panel });
    }
    cx += barW + 1;
    cx += c.put(cx, row, `${pct(o.probability)}${o.estimated ? '≈' : ''}`.padStart(5), o.selected ? P.ink : P.faint, { bg: P.panel, bold: o.selected }) + 2;
    if (o.description) c.put(cx, row, o.description, o.selected ? P.soft : P.faint, { limit: x + w - 2 - cx, bg: P.panel });
  });
  // 把握条和 0.70 把握线。
  const conf = Number(d.confidence), threshold = Number(d.threshold ?? 0.7);
  const row = y + h - 2;
  let cx = x + 2;
  cx += c.put(cx, row, `把握 ${pct(conf)}`, P.ink, { bg: P.panel, bold: true }) + 1;
  const scaleW = Math.max(10, Math.min(30, inner - 60));
  const tick = Math.round(threshold * scaleW);
  const pass = conf >= threshold;
  for (let k = 0; k < scaleW; k++) {
    const on = k < Math.round(conf * scaleW);
    c.put(cx + k, row, k === tick ? '┃' : on ? '▰' : '▱', k === tick ? P.ink : on ? (pass ? P.close : P.handback) : P.line, { bg: P.panel });
  }
  cx += scaleW + 1;
  cx += c.put(cx, row, `把握线 ${threshold.toFixed(2)}`, P.soft, { bg: P.panel }) + 3;
  const chosen = d.jev_choice ?? d.answer;
  const verdict = d.point === 'wrapup'
    ? (d.disposition === 'handback' || chosen === 'keep' ? '→ 交回执行协调' : '→ 直接收尾')
    : d.disposition === 'handback' ? '→ 把握不足，交回执行协调' : `→ 派给 ${optionLabel(chosen)} · 直接执行`;
  const vt = d.disposition === 'handback' || chosen === 'keep' ? P.handback : modelZone(chosen) === 'other' ? P.close : P[modelZone(chosen)];
  c.put(cx, row, verdict, vt, { limit: x + w - 2 - cx, bg: P.panel, bold: true });
}


/* ---------------------------------------------------------------- 老虎机 */

// 三个转轮依次停下（毫秒，从任务卡落到 JEV 算起），最后一个停下后开始中奖闪光。
const REEL_STOPS = [1300, 1650, 2000];
const WIN_AT = REEL_STOPS.at(-1) + 100;
const WIN_MS = 800;
const REEL_W = 12;
const MACHINE_W = 1 + 3 * REEL_W + 2 + 1;
const GOLD = '#ffd36b';

function slotSymbols(d) {
  const keys = Array.isArray(d.options) ? d.options : Object.keys(d.options || {});
  const zones = ZONE_ORDER.filter((z) => keys.some((k) => modelZone(k) === z));
  return zones.length >= 2 ? zones : ZONE_ORDER;
}

export function slotState(d, now) {
  if (!d || d.point !== 'dispatch' || d.arriveAt == null || d.readyAt == null) return null;
  const t = now - d.arriveAt;
  if (t < 0 || now >= d.readyAt) return null;
  const syms = slotSymbols(d);
  const sel = syms.indexOf(modelZone(d.jev_choice ?? d.answer));
  if (sel < 0) return null;
  const win = t >= WIN_AT;
  const wt = t - WIN_AT;
  return { t, syms, sel, win, wt, flash: win && wt < WIN_MS && Math.floor(wt / 130) % 2 === 0 };
}

// 转轮位置：先快后慢（三次缓出），略微越过目标一格，停下时弹回。返回格位和模糊程度。
function reel(i, t, n, sel) {
  const stop = REEL_STOPS[i], start = i * 2, base = 18 + 5 * i;
  const D = base + ((((sel - start - base) % n) + n) % n);
  const over = 0.6;
  let p, blur;
  if (t < stop) {
    const u = Math.max(0, t / stop);
    p = (D + over) * (1 - (1 - u) ** 3);
    blur = Math.min(1, (3 * (D + over) * (1 - u) ** 2 / stop) * 1000 / 45);
  } else {
    const v = Math.min(1, (t - stop) / 140);
    p = D + over * (1 - v) ** 2;
    blur = 0;
  }
  const k = Math.round(p);
  const at = (off) => ((((start + k + off) % n) + n) % n);
  return { top: at(1), mid: at(0), bottom: at(-1), blur, stopped: t >= stop + 60, bounce: t >= stop - 200 && t < stop + 140 };
}

function machineX(L) {
  const { x, w } = L.jev;
  const center = Math.floor((w - MACHINE_W) / 2);
  return x + Math.max(11, Math.min(center, w - MACHINE_W - 36));
}

function drawSlot(c, L, d, s, now, grad) {
  const { x, y, w } = L.jev;
  const mx = machineX(L);
  const top = y + 1, pay = y + 3;
  const chase = Math.floor(now / (s.win ? 60 : 95));
  // 灯珠：上下两排交替亮灭，中奖时全亮闪烁。
  for (let k = 0; k < MACHINE_W; k++) {
    const col = mx + k;
    const lit = s.flash || (k + chase) % 2 === 0;
    const tint = lit ? mix(grad(col), '#ffffff', s.flash ? 0.7 : 0.35) : mix(grad(col), P.panel, 0.62);
    c.put(col, top, '●', tint, { bg: P.panel });
    c.put(col, y + 5, '●', tint, { bg: P.panel });
  }
  for (let r = 0; r < 3; r++) {
    const row = y + 2 + r;
    const edge = row === pay ? (s.flash ? '#ffffff' : GOLD) : grad(mx);
    c.put(mx, row, row === pay ? '▶' : '┃', edge, { bg: P.panel, bold: true });
    c.put(mx + MACHINE_W - 1, row, row === pay ? '◀' : '┃', row === pay ? edge : grad(mx + MACHINE_W - 1), { bg: P.panel, bold: true });
  }
  const n = s.syms.length;
  for (let i = 0; i < 3; i++) {
    const rx = mx + 1 + i * (REEL_W + 1);
    if (i > 0) for (let r = 0; r < 3; r++) c.put(rx - 1, y + 2 + r, '┃', mix(grad(rx), P.panel, 0.45), { bg: P.panel });
    const st = reel(i, s.t, n, s.sel);
    const band = s.win ? mix('#2a1f45', P.jevB, s.flash ? 0.45 : 0.18) : st.bounce ? '#3a2a5e' : '#241b38';
    [[st.top, y + 2], [st.mid, pay], [st.bottom, y + 4]].forEach(([idx, row]) => {
      const center = row === pay;
      const bg = center ? band : P.screen;
      c.fillRect(rx, row, REEL_W, 1, bg);
      const zone = s.syms[idx];
      const label = ZONES[zone].short;
      const lw = 2 + width(label);
      const lx = rx + Math.floor((REEL_W - lw) / 2);
      if (!center) {
        const dim = 0.5 + 0.35 * st.blur;
        c.put(lx, row, ICON[zone].ch, mix(iconColor(zone), P.screen, dim), { bg });
        c.put(lx + 2, row, label, mix(P.soft, P.screen, dim), { bg });
        return;
      }
      putIcon(c, lx, row, zone, { bg });
      const tint = s.win ? (col) => mix(P.jevA, P.jevB, (col - lx) / Math.max(1, lw)) : st.stopped ? P[zone] : mix(P.ink, P.faint, st.blur * 0.6);
      c.put(lx + 2, row, label, s.win && s.flash ? '#ffffff' : tint, { bg, bold: true });
    });
  }
  // 拉杆：开始时拉下再弹回。
  const lx = mx + MACHINE_W + 1;
  const pull = s.t < 160 ? 1 : s.t < 420 ? 2 : s.t < 600 ? 1 : 0;
  c.put(mx + MACHINE_W, pay, '━', P.soft, { bg: P.panel });
  for (let row = top + pull + 1; row < pay; row++) c.put(lx, row, '┃', P.soft, { bg: P.panel });
  c.put(lx, pay, pull >= 2 ? '●' : '┛', pull >= 2 ? P.jevB : P.soft, { bg: P.panel, bold: true });
  if (pull < 2) c.put(lx, top + pull, '●', P.jevB, { bg: P.panel, bold: true });
  // 左边：大图标，随第一个转轮变化，中奖时发亮。
  const left = reel(0, s.t, n, s.sel);
  const logoZone = s.win ? s.syms[s.sel] : s.syms[left.mid];
  drawLogo(c, x + Math.max(2, Math.floor((mx - x - 7) / 2)), y + 2, logoZone, { glow: s.flash ? 0.6 : s.win ? 0.1 : 0.35 * left.blur });
  // 右边：转动时「推理中」，停下后中奖行。
  const tx = lx + 3, room = x + w - 2 - tx;
  if (!s.win) {
    const dots = '.'.repeat(1 + (Math.floor(now / 160) % 3));
    c.put(tx, pay, `推理中${dots}`, grad, { bg: P.panel, bold: true, limit: room });
    const shimmer = Array.from({ length: Math.min(18, room) }, (_, i) => ((i + Math.floor(now / 50)) % 6 < 2 ? '▰' : '▱')).join('');
    c.put(tx, pay + 1, shimmer, grad, { bg: P.panel });
  } else {
    const zone = s.syms[s.sel];
    const line = `JEV 选中 ${ZONES[zone].short} · 把握 ${Number(d.confidence || 0).toFixed(2)}`;
    const lw = width(line);
    c.put(tx, pay, line, s.flash ? '#ffffff' : (col) => mix(P.jevA, P.jevB, (col - tx) / Math.max(1, lw)), { bg: P.panel, bold: true, limit: room });
    const stars = '✦ ✧ ⋆ ✦ ✧ ⋆ ✦';
    c.put(tx, pay - 1, stars, (col) => mix(GOLD, P.jevB, ((col - tx + Math.floor(now / 70)) % 8) / 8), { bg: P.panel, limit: room });
    c.put(tx, pay + 1, stars, (col) => mix(P.jevA, GOLD, ((col - tx + Math.floor(now / 70)) % 8) / 8), { bg: P.panel, limit: room });
  }
}

// 中奖瞬间从老虎机中心向四周散开的一圈火花，画在连线之上。
function drawSparks(c, L, state, now) {
  const s = slotState(state.lastDecision, now);
  if (!s || !s.win || s.wt >= WIN_MS) return;
  const mx = machineX(L);
  const cx = mx + MACHINE_W / 2, cy = L.jev.y + 3;
  const u = s.wt / WIN_MS;
  const spread = 1 - (1 - u) ** 2;
  const N = 26;
  for (let i = 0; i < N; i++) {
    const a = (i / N) * Math.PI * 2 + (i % 3) * 0.11;
    const speed = 0.8 + 0.45 * (((i * 7) % 5) / 4);
    const r = (MACHINE_W / 2 + 2 + 30 * spread) * speed;
    const px = Math.round(cx + Math.cos(a) * r);
    const py = Math.round(cy + Math.sin(a) * r * 0.24);
    if (py >= L.jev.y + 1 && py <= L.jev.y + 5 && px >= mx - 1 && px <= mx + MACHINE_W + 1) continue;
    if (Math.abs(py - cy) <= 1 && px >= mx + MACHINE_W + 3 && px < L.jev.x + L.jev.w - 1) continue;
    if (py < 2 || py >= c.rows - 1 || px < 0 || px >= c.cols) continue;
    const under = c.get(px, py);
    // 只落在地面或 JEV 框里的空白格上，不盖住文字、连线和别的方框。
    const inJev = px > L.jev.x && px < L.jev.x + L.jev.w - 1 && py > L.jev.y && py < L.jev.y + L.jev.h - 1;
    if (!under || under.ch !== ' ' || (under.bg !== P.bg && !inJev)) continue;
    const ch = u < 0.55 ? '✦✧⋆'[i % 3] : u < 0.8 ? '✧⋆'[i % 2] : '⋆';
    const tint = mix(i % 4 === 0 ? GOLD : mix(P.jevA, P.jevB, i / N), under.bg, Math.max(0, u - 0.45) * 1.6);
    c.put(px, py, ch, tint, { bg: under.bg, bold: true });
  }
}

// 每个区此刻亮多少：老虎机中奖时选中的区闪亮，任务卡飞往该区时保持亮起。
function zoneGlow(state, now) {
  const glow = new Map();
  const s = slotState(state.lastDecision, now);
  if (s?.win) glow.set(s.syms[s.sel], s.flash ? 1 : 0.75);
  for (const f of state.flights) {
    if (f.to !== 'worker' || !f.zone) continue;
    if (now >= f.startedAt - 1600 && now <= f.startedAt + f.duration + 700) glow.set(f.zone, Math.max(glow.get(f.zone) || 0, 0.6));
  }
  return glow;
}

// 画面里有东西在动时返回 true，主循环据此把刷新提到每秒 25 帧。
export function jevAnimating(state, now) {
  if (!state) return false;
  const d = state.lastDecision;
  if (d?.readyAt != null && now >= (d.arriveAt ?? d.bornAt ?? now) - 50 && now < d.readyAt + 100) return true;
  return state.flights.some((f) => now >= f.startedAt - 100 && now <= f.startedAt + f.duration + 200);
}

/* ---------------------------------------------------------------- 工位卡 */

function card(c, worker, x, y, w, h, frame, now, selected, glow = 0) {
  const st = worker.status in STATUS_NAME ? worker.status : 'unknown';
  const tint = P[st];
  const bg = P.panel;
  const zone = modelZone(worker.model);
  c.box(x, y, w, h, selected ? P.ink : glow ? mix(P[zone] || tint, '#ffffff', glow * 0.4) : tint, { bg, heavy: glow > 0 });
  const put = (r, text, fg, opts = {}) => c.put(x + 2, y + r, text, fg, { limit: w - 4, bg, ...opts });
  // 第 1 行：状态色条 + 执行者名，右上角写执行者和型号。
  const executor = clean(worker.actual?.executor || worker.kind || '').replace(/-cli$/, '').slice(0, 8);
  // 窄卡片只留图标，把位置让给执行者名。
  const who = !worker.jev || zone === 'other' ? executor : w < 24 ? '' : ZONES[zone].short;
  const whoW = width(who) + (zone === 'other' ? 0 : 2);
  c.put(x + 1, y + 1, '▌', tint, { bg });
  const nameW = w - 5 - whoW;
  c.put(x + 2, y + 1, worker.targetName || worker.name || '执行者', P.ink, { limit: nameW, bg, bold: true });
  if (zone !== 'other') putIcon(c, x + w - 2 - whoW, y + 1, zone, { bg });
  c.put(x + w - 2 - width(who), y + 1, who, zone === 'other' ? P.faint : P[zone], { bg });
  put(2, worker.task || '未记录任务', P.ink);
  if (worker.jev) {
    const grad = (col) => mix(P.jevA, P.jevB, (col - x) / w);
    let cx = x + 2;
    cx += c.put(cx, y + 3, ` JEV ${pct(worker.confidence)} `, P.bg, { bg: P.jevA, bold: true }) + 1;
    if (w >= 30) c.put(cx, y + 3, `选 ${zone === 'other' ? clean(worker.model) : ZONES[zone].short}`, grad, { limit: x + w - 2 - cx, bg, bold: true });
    put(4, `原因：${worker.reason || '未记录选项说明'}`, P.soft);
  } else {
    put(3, '未经 JEV', P.faint);
    put(4, worker.title || '', P.faint);
  }
  // 小人坐在桌前，桌上屏幕显示命令和火花线。
  const artY = y + (h >= 15 ? 6 : 5);
  if (artY + 4 < y + h - 3) {
    const id = identity(String(worker.id || worker.targetName || 'x'));
    const figure = pose(st === 'failed' ? 'blocked' : st, frame).rows;
    const personX = w < 30 ? x + 1 : x + Math.max(2, Math.floor((w - 38) / 2));
    for (let r = 0; r < 4; r++) {
      const row = figure[r];
      [...row].forEach((ch, i) => {
        if (ch === ' ') return;
        const color = r === 0 ? (i >= 4 && i <= 8 ? id.hair : id.skin) : r === 1 ? id.skin : id.shirt;
        c.put(personX + i, artY + r, ch, color, { bg });
      });
    }
    const monX = personX + 13, monW = Math.min(20, x + w - 3 - monX);
    if (monW >= 8) {
      const command = worker.command || (st === 'done' ? '已完成' : st === 'blocked' ? '等你处理' : st === 'idle' ? '待命' : '执行中');
      const spark = st === 'working' ? runningScreen('', frame)[1] : '';
      c.put(monX, artY, '┌' + '─'.repeat(monW - 2) + '┐', P.faint, { bg });
      c.fillRect(monX + 1, artY + 1, monW - 2, 2, P.screen);
      c.put(monX, artY + 1, '│', P.faint, { bg }); c.put(monX + monW - 1, artY + 1, '│', P.faint, { bg });
      c.put(monX, artY + 2, '│', P.faint, { bg }); c.put(monX + monW - 1, artY + 2, '│', P.faint, { bg });
      const cmd = truncate(clean(command), monW - 2);
      c.put(monX + 1 + Math.floor((monW - 2 - width(cmd)) / 2), artY + 1, cmd, tint, { bg: P.screen });
      c.put(monX + 1, artY + 2, [...spark.repeat(3)].slice(0, monW - 2).join(''), tint, { bg: P.screen });
      c.put(monX, artY + 3, '└' + '─'.repeat(Math.floor((monW - 3) / 2)) + '┴' + '─'.repeat(Math.ceil((monW - 3) / 2)) + '┘', P.faint, { bg });
    }
    // 桌上放执行者的大图标：窄卡片放在小人旁边，宽卡片放在屏幕右边。
    const logoX = monW >= 8 ? monX + monW + 2 : personX + 12;
    if (zone !== 'other' && logoX + 7 <= x + w - 2) drawLogo(c, logoX, artY + 1, zone, { bg, glow: glow * 0.3 });
    c.put(x + 1, artY + 4, '▀'.repeat(w - 2), P.desk, { bg });
  }
  // 底部：状态和收尾结果。
  const since = worker.ts ? Math.max(0, Math.round((now - worker.ts) / 1000)) : null;
  const statusText = `${STATUS_NAME[st]}${since != null && since < 36000 ? ` · ${since < 60 ? `${since} 秒` : `${Math.round(since / 60)} 分钟`}` : ''}`;
  c.put(x + 1, y + h - 3, '▌', tint, { bg });
  put(h - 3, statusText, tint, { bold: true });
  if (worker.result) put(h - 2, `收尾：${worker.result}`, worker.result === '交回' ? P.handback : P.close, { bold: true });
}

function emptyDesk(c, x, y, w, h, zone, glow = 0) {
  c.box(x, y, w, h, glow ? mix(P[zone], '#ffffff', glow * 0.4) : P.line, { bg: P.bg, heavy: glow > 0 });
  c.put(x + 2, y + 1, '空闲', glow ? P[zone] : P.faint, { bold: glow > 0 });
  if (h >= 5 && w >= 11) drawLogo(c, x + w - 9, y + 1, zone, { bg: P.bg, glow: glow * 0.5, dim: glow ? 0 : 0.55 });
}

/* ---------------------------------------------------------------- 布局和连线 */

// 一排方框按给定宽度居中排开，放不下时等比缩窄。
function row(cols, y, h, widths, gap = 4) {
  const total = widths.reduce((a, b) => a + b, 0) + gap * (widths.length - 1);
  const scale = Math.min(1, (cols - 4 - gap * (widths.length - 1)) / Math.max(1, total - gap * (widths.length - 1)));
  const ws = widths.map((w) => Math.max(12, Math.floor(w * scale)));
  let x = Math.floor((cols - ws.reduce((a, b) => a + b, 0) - gap * (ws.length - 1)) / 2);
  return ws.map((w) => { const box = { x, y, w, h, cx: x + Math.floor(w / 2) }; x += w + gap; return box; });
}

function layout(cols, rows, zoneKeys, active, crew) {
  // 40 行以上画完整流程：你一排、执行协调一排、JEV、型号区、审查一排；不到 40 行时前两排折成一行，审查排省掉。
  const full = rows >= 40;
  const tight = rows < 34;
  const lineH = full ? 3 : 1;
  // 竖屏（例如 3:4 截图）时行数远多于需要：工位卡最高 16 行，多出来的行均分成各层之间的间距，不把工位卡拉长。
  const need = 2 + (lineH + 1) * 2 + (tight ? 6 : 7) + 2 + (rows >= 40 ? 3 : 2) + 16 + 1 + 4;
  const spare = full ? Math.max(0, rows - need) : 0;
  const g = Math.floor(spare / 5);
  const [boss, main, advisor] = row(cols, 2 + g, lineH, [26, 36, 26], 12);
  const mgrY = 2 + g + lineH + 1 + g;
  const mgrs = row(cols, mgrY, lineH, Array.from({ length: Math.max(1, crew.managers.length) }, () => 40));
  const jev = { w: Math.min(cols - 4, 116), h: tight ? 6 : 7 };
  jev.x = Math.floor((cols - jev.w) / 2); jev.y = mgrY + lineH + 1 + g;
  // 横线紧贴 JEV 框底边，省出一行给工位卡。
  const bus = jev.y + jev.h;
  const zoneY = bus + 2 + g;
  const gap = 2;
  const n = zoneKeys.length;
  const room = cols - 2 - gap * (n - 1);
  const even = Math.floor(room / n);
  // 每区至少 21 列才排得下工位卡；排不下时没有工位的区折叠成一个标签。
  const fold = even < 21 && active.size > 0 && active.size < n;
  const foldW = (key) => width(ZONES[key].name) + 2;
  const foldTotal = zoneKeys.filter((k) => !active.has(k)).reduce((sum, k) => sum + foldW(k), 0);
  const each = fold ? Math.floor((room - foldTotal) / active.size) : even;
  const widths = zoneKeys.map((k) => (fold && !active.has(k) ? foldW(k) : each));
  let at = Math.floor((cols - widths.reduce((a, b) => a + b, 0) - gap * (n - 1)) / 2);
  const headRows = rows >= 40 ? 3 : 2;
  const deskY = zoneY + headRows;
  const review = full ? { y: Math.min(rows - 4, deskY + 16 + 1 + g), h: 3 } : null;
  const deskH = review ? Math.min(16, review.y - 1 - deskY) : Math.min(16, rows - 2 - deskY);
  const reviews = review ? row(cols, review.y, 3, [...crew.reviewers.map(() => 30), 24]) : [];
  const zones = zoneKeys.map((key, i) => {
    const z = { key, x: at, w: widths[i], center: at + Math.floor(widths[i] / 2), folded: fold && !active.has(key) };
    at += widths[i] + gap;
    return z;
  });
  return { boss, main, advisor, mgrs, jev, bus, zoneY, deskY, deskH, zones, headRows, review, reviews };
}

// 连线是一串格子；prev 和 next 是两端外面的那一格，用来决定端点画成竖线还是拐角。
const run = (x0, x1, y) => { const pts = []; const step = x1 >= x0 ? 1 : -1; for (let x = x0; x !== x1 + step; x += step) pts.push([x, y]); return pts; };
const col = (x, y0, y1) => { const pts = []; const step = y1 >= y0 ? 1 : -1; for (let y = y0; y !== y1 + step; y += step) pts.push([x, y]); return pts; };
const dedupe = (pts) => pts.filter((p, i) => i === 0 || p[0] !== pts[i - 1][0] || p[1] !== pts[i - 1][1]);
const reverse = (r) => r && { ...r, pts: [...r.pts].reverse(), prev: r.next, next: r.prev };

// 从上方方框底边往下，经一条横线，进入下方方框顶边。
function drop(id, x0, yTop, x1, yBottom, wireY) {
  return { id, pts: dedupe([...col(x0, yTop, wireY), ...run(x0, x1, wireY), ...col(x1, wireY, yBottom)]), prev: [x0, yTop - 1], next: [x1, yBottom + 1] };
}

function routes(L) {
  const m = L.mgrs[0];
  const R = {
    bossMain: { id: 'bossMain', pts: run(L.boss.x + L.boss.w, L.main.x - 1, L.boss.y + Math.floor(L.boss.h / 2)),
      prev: [L.boss.x + L.boss.w - 1, L.boss.y + Math.floor(L.boss.h / 2)], next: [L.main.x, L.main.y + Math.floor(L.main.h / 2)] },
    mainMgr: L.mgrs.map((box, i) => drop(`mainMgr${i}`, L.main.cx, L.main.y + L.main.h, box.cx, box.y - 1, L.main.y + L.main.h)),
    mgrJev: drop('mgrJev', m.cx, m.y + m.h, Math.floor(L.jev.x + L.jev.w / 2), L.jev.y - 1, m.y + m.h),
    zone: Object.fromEntries(L.zones.map((z) => [z.key, drop(`zone-${z.key}`, Math.floor(L.jev.x + L.jev.w / 2), L.jev.y + L.jev.h, z.center, L.zoneY - 1, L.bus)])),
  };
  if (L.review) {
    const wireY = L.review.y - 1;
    R.review = (zoneKey, i) => {
      const z = L.zones.find((q) => q.key === zoneKey) || L.zones[0];
      const box = L.reviews[i] || L.reviews[0];
      const from = z.folded ? L.zoneY + 2 : L.deskY + L.deskH;
      return drop(`review-${zoneKey}-${i}`, z.center, from, box.cx, L.review.y - 1, wireY);
    };
    // 回程：审查方框顶边 → 审查排上方那条横线 → 最左一列向上 → 执行协调方框左边。
    R.rail = (i) => {
      const box = L.reviews[i] || L.reviews[0];
      const my = m.y + Math.floor(m.h / 2);
      return { id: 'rail', pts: dedupe([...run(box.cx, 0, wireY), ...col(0, wireY, my), ...run(0, m.x - 1, my)]), prev: [box.cx, box.y], next: [m.x, my] };
    };
  }
  return R;
}

function routeFor(R, f) {
  const key = `${f.from}>${f.to}`;
  const zone = R.zone[f.zone] || Object.values(R.zone)[0];
  switch (key) {
    case 'boss>main': return R.bossMain;
    case 'main>manager': return R.mainMgr[0];
    case 'manager>main': return reverse(R.mainMgr[0]);
    case 'manager>jev': return R.mgrJev;
    case 'jev>manager': return reverse(R.mgrJev);
    case 'jev>worker': return zone;
    case 'worker>jev': return reverse(zone);
    case 'worker>review': return R.review ? R.review(f.zone, f.reviewer) : null;
    case 'review>manager': return R.rail ? R.rail(f.reviewer) : null;
    default: return null;
  }
}

const GLYPH = { ud: '│', lr: '─', dr: '╭', dl: '╮', ur: '╰', ul: '╯', u: '│', d: '│', l: '─', r: '─',
  udl: '┤', udr: '├', ulr: '┴', dlr: '┬', udlr: '┼' };
// 几条连线共用的格子记下所有方向，交汇处画成丁字或十字。
function drawRoute(c, r, tint) {
  c.wires ??= new Map();
  const pts = r.pts;
  pts.forEach(([x, y], i) => {
    const near = [i === 0 ? r.prev : pts[i - 1], i === pts.length - 1 ? r.next : pts[i + 1]];
    const dirs = new Set(near.filter(Boolean).map(([nx, ny]) => (ny < y ? 'u' : ny > y ? 'd' : nx < x ? 'l' : 'r')));
    const seen = c.wires.get(`${x},${y}`) || new Set();
    for (const d of dirs) seen.add(d);
    c.wires.set(`${x},${y}`, seen);
    const key = ['u', 'd', 'l', 'r'].filter((d) => seen.has(d)).join('');
    const under = c.get(x, y);
    if (!under || under.ch === '') return;
    c.put(x, y, GLYPH[key] || '│', tint, { bg: under.bg });
  });
}

function flightTint(f) {
  if (f.tint && P[f.tint]) return P[f.tint];
  if (f.result === '交回') return P.handback;
  if (f.result) return P.close;
  if (f.from === 'boss') return GOLD;
  if (f.from === 'main') return ICON.opus.from;
  if (f.to === 'main') return P.close;
  if (f.to === 'review') return P[f.zone] || P.jevB;
  return f.to === 'jev' ? P.jevA : P[f.zone] || P.jevB;
}

function drawWiring(c, L, R, state, now) {
  // 飞行中最亮，落地后连线保留约 3 秒再在 2 秒内淡出。
  const active = new Map();
  for (const f of state.flights) {
    const age = now - f.startedAt;
    if (age < 0 || age > f.duration + 5000) continue;
    const r = routeFor(R, f);
    if (!r) continue;
    const t = age <= f.duration ? 0 : Math.min(1, Math.max(0, (age - f.duration - 3000) / 2000));
    const prev = active.get(r.id);
    if (prev == null || t < prev.t) active.set(r.id, { t, r, tint: flightTint(f) });
  }
  const base = P.line;
  const statics = [R.bossMain, ...R.mainMgr, R.mgrJev, ...Object.values(R.zone), ...(R.rail ? [R.rail(0)] : [])];
  for (const r of statics) if (!active.has(r.id)) drawRoute(c, r, base);
  for (const { r, t, tint } of active.values()) drawRoute(c, r, mix(tint, base, t));
  for (const f of state.flights) {
    const age = now - f.startedAt;
    if (age < 0 || age > f.duration) continue;
    const r = routeFor(R, f);
    if (!r?.pts.length) continue;
    const [px, py] = r.pts[Math.min(r.pts.length - 1, Math.floor((age / f.duration) * r.pts.length))];
    const label = f.result ? `${truncate(f.task || '任务', 10)} · ${f.result}` : truncate(f.task || '任务', 14);
    const text = ` ▣ ${label} `;
    const tx = Math.max(0, Math.min(c.cols - width(text), px - Math.floor(width(text) / 2)));
    c.put(tx, py, text, P.bg, { bg: flightTint(f), bold: true });
  }
}

// 此刻正被任务卡经过的方框，用来点亮。
function litNodes(state, now) {
  const lit = new Set();
  for (const f of state.flights) {
    const age = now - f.startedAt;
    if (age < -200 || age > f.duration + 900) continue;
    if (age >= f.duration * 0.6) lit.add(f.to === 'review' ? `review${f.reviewer ?? 0}` : f.to);
    if (age < f.duration * 0.4) lit.add(f.from === 'review' ? `review${f.reviewer ?? 0}` : f.from);
    if (f.from === 'review' && f.to === 'manager' && age >= f.duration * 0.6) lit.add('proof');
  }
  return lit;
}

/* ---------------------------------------------------------------- 整帧 */

export function renderJevFrame(view) {
  const { cols, rows } = view.size;
  const state = view.officeState;
  const now = view.now ?? Date.now();
  const frame = view.frame || 0;
  const c = canvas(cols, rows);
  const people = view.people || [];
  const crew = { managers: people.filter(isManager), reviewers: people.filter(isReviewer).slice(0, 3) };
  const workers = officeWorkers(state, people.filter((p) => !isManager(p) && !isReviewer(p)), view.showAll);
  const empty = { hitboxes: [], grid: { cols: 1, rows: 1, ids: [], menuCols: 1, menuVisible: 0 }, regions: [] };
  topBar(c, view, workers, state);
  if (cols < 72 || rows < 26) {
    // 太小时不画工位图，只列出关键信息。
    const d = state.lastDecision;
    const list = [
      `执行协调：${state.managerText}`,
      d ? `JEV：${d.question || ''} · 把握 ${pct(d.confidence)}` : 'JEV：等待任务',
      ...workers.map((w) => `${ZONES[modelZone(w.model)].short} · ${w.task || ''} · ${STATUS_NAME[w.status] || ''} · ${pct(w.confidence)}`),
    ];
    list.slice(0, rows - 3).forEach((line, i) => c.put(1, 3 + i, line, i === 1 ? P.jevA : P.ink, { limit: cols - 2 }));
    c.put(1, rows - 1, '终端太小 · 100×36', P.faint, { limit: cols - 2 });
    return { ...empty, lines: c.lines() };
  }
  const zoneKeys = [...ZONE_ORDER];
  const zoneOf = (w) => (!w.jev ? 'other' : modelZone(w.model));
  if (view.showAll && workers.some((w) => zoneOf(w) === 'other')) zoneKeys.push('other');
  const active = new Set(workers.map(zoneOf));
  const L = layout(cols, rows, zoneKeys, active, crew);
  const R = routes(L);
  drawPeople(c, L, state, crew, litNodes(state, now), now);
  jevBox(c, L, state, now);
  const glow = zoneGlow(state, now);
  const hitboxes = [], ids = [];
  for (const z of L.zones) {
    const meta = ZONES[z.key];
    const lit = glow.get(z.key) || 0;
    const entries = workers.filter((w) => zoneOf(w) === z.key).sort((a, b) => (b.ts || 0) - (a.ts || 0));
    let hx = z.x;
    if (z.key !== 'other') { putIcon(c, hx, L.zoneY, z.key); hx += 2; }
    const nameOpts = lit ? { bold: true, bg: P[z.key], limit: z.w - 2 } : { bold: true, limit: z.w - 2 };
    hx += c.put(hx, L.zoneY, meta.name, lit ? P.bg : P[z.key] || P.other, nameOpts);
    if (z.folded) { c.put(z.x, L.zoneY + 1, '空闲', lit ? P[z.key] : P.faint, { limit: z.w }); continue; }
    if (entries.length > 1) c.put(hx + 1, L.zoneY, `${entries.length} 个`, P.soft, { limit: z.x + z.w - hx - 1 });
    c.put(z.x, L.zoneY + 1, meta.purpose, P.soft, { limit: z.w });
    if (L.headRows >= 3) c.put(z.x, L.zoneY + 2, meta.price, P.faint, { limit: z.w });
    if (L.deskH < 8) continue;
    const page = Math.max(0, view.officePage || 0);
    const worker = entries.find((w) => w.id === view.selectedId) || entries[page % Math.max(1, entries.length)];
    if (worker) {
      card(c, worker, z.x, L.deskY, z.w, L.deskH, frame, now, !view.demo && view.detail?.id === worker.id, lit);
      hitboxes.push({ id: worker.id, x: z.x, y: L.deskY, w: z.w, h: L.deskH }); ids.push(worker.id);
    } else emptyDesk(c, z.x, L.deskY, z.w, Math.min(L.deskH, 5), z.key, lit);
  }
  drawWiring(c, L, R, state, now);
  drawSparks(c, L, state, now);
  const keys = `a ${view.showAll ? '只看 JEV 工位' : '显示全部窗格'}   [ ] 翻看同区工位   q 退出`;
  c.put(1, rows - 1, keys, P.faint, { limit: cols - 2 });
  return { lines: c.lines(), hitboxes, grid: { cols: L.zones.length, rows: 1, ids, menuCols: 1, menuVisible: 0 }, regions: [] };
}
