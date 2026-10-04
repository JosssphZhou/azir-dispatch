import { sanitize, truncate, width } from './text.mjs';

const C = {
  ink: '#e5eaf4', muted: '#8994a8', dim: '#596579', line: '#394254',
  manager: '#f0a66f', jev: '#bb8cff', pink: '#ff82ce', advisor: '#79d7e8',
  green: '#65e6a8', amber: '#ffc36b', dark: '#27303d', pale: '#465165',
};
const esc = (s) => `\x1b[38;2;${parseInt(s.slice(1, 3), 16)};${parseInt(s.slice(3, 5), 16)};${parseInt(s.slice(5, 7), 16)}m`;
const color = (s, c, bold = false) => `${bold ? '\x1b[1m' : ''}${esc(c)}${s}\x1b[0m`;
const clean = (s, max) => truncate(sanitize(String(s ?? '')), max);
const dollars = (n) => Number.isFinite(Number(n)) ? `$${Number(n).toFixed(4)}` : '费用未知';
const pad = (s, n) => {
  const v = clean(s, n);
  return v + ' '.repeat(Math.max(0, n - width(v)));
};
const paintBar = (probability, n, selected) => {
  const filled = Math.max(0, Math.min(n, Math.round(probability * n)));
  let out = '';
  for (let i = 0; i < n; i += 1) {
    if (i >= filled) out += color('·', C.dark);
    else if (selected) {
      const t = filled <= 1 ? 0 : i / (filled - 1);
      const rgb = [
        Math.round(123 + (255 - 123) * t),
        Math.round(92 + (130 - 92) * t),
        Math.round(255 + (206 - 255) * t),
      ];
      out += `\x1b[38;2;${rgb.join(';')}m▰\x1b[0m`;
    } else out += color('▰', C.pale);
  }
  return out;
};
const confidenceScale = (confidence, threshold, n) => {
  const filled = Math.max(0, Math.min(n, Math.round(confidence * n)));
  const tick = Math.max(0, Math.min(n - 1, Math.round(threshold * (n - 1))));
  let out = '';
  for (let i = 0; i < n; i += 1) {
    if (i === tick) out += color('│', C.ink, true);
    else if (i < filled) out += color('▰', confidence >= threshold ? C.green : C.amber);
    else out += color('·', C.dark);
  }
  return out;
};
const line = (parts, cols) => {
  let raw = '';
  let used = 0;
  for (const [text, tint = C.ink, bold = false] of parts) {
    if (used >= cols) break;
    const value = String(text);
    const clipped = width(value) <= cols - used ? value : clean(value, cols - used);
    raw += color(clipped, tint, bold);
    used += width(clipped);
  }
  return raw + ' '.repeat(Math.max(0, cols - used));
};
const cell = (label, value, tint, n) => [pad(`${label}${value}`, n), tint, true];

function optionRows(decision, cols, ready, now) {
  if (!ready) {
    const dots = '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏';
    const frame = Math.floor(now / 120) % dots.length;
    const fill = '━'.repeat(1 + Math.floor((now % 1400) / 1400 * Math.max(1, Math.min(18, cols - 45))));
    return [line([['   ', C.ink], [dots[frame], C.jev, true], [' 推理中  ', C.jev], [fill, C.pink]], cols)];
  }
  const raw = decision?.options;
  // Older records store options as an array of names.
  const choices = Array.isArray(raw) ? raw.map((name) => [String(name), '']) : raw && typeof raw === 'object' ? Object.entries(raw) : [];
  if (!choices.length) return [];
  const answer = String(decision.answer ?? decision.jev_choice ?? '');
  const confidence = Math.max(0, Math.min(1, Number(decision.confidence) || 0));
  // Prefer the per-option probabilities JEV returned; estimate only when they are missing.
  const probs = decision?.probabilities && typeof decision.probabilities === 'object' ? decision.probabilities : null;
  const real = probs && choices.every(([key]) => Number.isFinite(Number(probs[key]))) ? (key) => Number(probs[key]) : null;
  const inline = choices.every(([, value]) => typeof value === 'number' && Number.isFinite(value)) ? (key, value) => Number(value) : null;
  const known = real || inline;
  const explicit = Boolean(known);
  const others = Math.max(1, choices.length - 1);
  return choices.map(([key, value]) => {
    const picked = key === answer || String(value) === answer;
    const p = known ? Math.max(0, Math.min(1, known(key, value))) : picked ? confidence : (1 - confidence) / others;
    const label = clean(key, 24);
    const barWidth = Math.min(24, Math.max(8, cols - 80));
    const prefix = picked ? '  ▸ ' : '    ';
    return line([
      [prefix, picked ? C.pink : C.dim], [pad(label, 24), picked ? C.ink : C.muted, picked],
      [' ', C.ink], [paintBar(p, barWidth, picked), C.ink],
      [` ${Math.round(p * 100)}%${explicit ? '' : '≈'}`, picked ? C.pink : C.muted],
    ], cols);
  });
}

function flightText(state, now) {
  const flight = state.flights.find((entry) => now >= entry.startedAt && now - entry.startedAt < entry.duration);
  if (!flight) return '工程经理  ━━━━━━━━━━━  JEV  ━━━━━━━━━━━  执行工位';
  const progress = Math.max(0, Math.min(1, (now - flight.startedAt) / flight.duration));
  const left = flight.from === 'manager' ? '工程经理' : flight.from === 'worker' ? '执行工位' : 'JEV';
  const right = flight.to === 'manager' ? '工程经理' : flight.to === 'worker' ? `执行工位 ${flight.target}` : 'JEV';
  const span = 30;
  const position = Math.round(progress * span);
  const tailStart = Math.max(0, position - 3);
  const route = color('━'.repeat(tailStart), C.line) + color('·'.repeat(position - tailStart), C.pink) +
    color('●', C.pink, true) + color('━'.repeat(span - position), C.line);
  return `${left} ${route}▶ ${right}`;
}

export function renderDispatchFloor(state, workers, { cols = 120, now = Date.now() } = {}) {
  if (cols < 56) {
    const tally = { blocked: 0, working: 0, done: 0, unknown: 0 };
    for (const worker of workers || []) tally[worker.status] = (tally[worker.status] || 0) + 1;
    return [
      line([['AZIR ', C.jev, true], [`等你${tally.blocked} 工${tally.working} 完${tally.done} 不明${tally.unknown}`, C.muted], [` JEV${state.todayDecisions} ${dollars(state.todayCost)}`, C.pink]], cols),
      line([['工程经理 · ', C.manager], [clean(state.managerText, cols - 13), C.ink]], cols),
      line([['JEV · ', C.jev], [state.lastDecision ? verdict(state.lastDecision) : '待判断', C.ink]], cols),
      line([['顾问 · ', C.advisor], [state.advisorCalls ? `${state.advisorCalls} 次调用` : '待命', C.ink]], cols),
    ];
  }
  const tally = { blocked: 0, working: 0, done: 0, unknown: 0 };
  for (const worker of workers || []) tally[worker.status] = (tally[worker.status] || 0) + 1;
  const rows = [];
  rows.push(line([
    ['  azir 办公室  ', C.jev, true],
    cell('待批准 ', tally.blocked, C.amber, 12),
    cell('工作中 ', tally.working, C.green, 12),
    cell('完成 ', tally.done, C.advisor, 10),
    cell('未知 ', tally.unknown, C.jev, 10),
    [`JEV 今日 ${state.todayDecisions} 次  ${dollars(state.todayCost)}`, C.muted],
    [`  顾问 ${state.advisorCalls} 次`, C.advisor],
  ], cols));

  const first = Math.floor((cols - 4) * 0.30);
  const second = Math.floor((cols - 4) * 0.42);
  const third = cols - 4 - first - second;
  const sizes = [first, second, third];
  const border = (left, join, right) => line([[left + sizes.map((n) => '─'.repeat(n)).join(join) + right, C.line]], cols);
  const cardRow = (values) => {
    const parts = [['│', C.line]];
    values.forEach(([value, tint, bold], i) => {
      parts.push([pad(' ' + value, sizes[i]), tint, bold], ['│', C.line]);
    });
    return line(parts, cols);
  };
  rows.push(border('┌', '┬', '┐'));
  rows.push(cardRow([['工程经理 · 主会话', C.manager, true], ['JEV 判断 · 分流', C.jev, true], ['顾问', C.advisor, true]]));
  const adv = state.advisor && state.advisor.until > now;
  const jev = state.lastDecision;
  const jevStatus = jev && jev.bornAt != null && now < jev.readyAt ? '推理中…' : jev ? verdict(jev) : '待判断';
  const advName = adv ? `${state.advisor.session || '方案顾问'} · 协助中` : '顾问待命';
  rows.push(cardRow([[clean(state.managerText, first - 1), C.ink], [jevStatus, jevStatus === '推理中…' ? C.jev : C.pink], [advName, adv ? C.advisor : C.muted]]));
  rows.push(border('└', '┴', '┘'));
  const activeFlight = state.flights.find((entry) => now >= entry.startedAt && now - entry.startedAt < entry.duration);
  rows.push(line([['  ', C.ink], [flightText(state, now), activeFlight ? C.pink : C.dim]], cols));

  if (jev) {
    const ready = jev.bornAt == null || now >= jev.readyAt;
    const actualConf = Math.max(0, Math.min(1, Number(jev.confidence) || 0));
    const conf = ready || jev.bornAt == null ? actualConf : actualConf * Math.max(0, Math.min(1, (now - jev.bornAt) / Math.max(1, jev.readyAt - jev.bornAt)));
    const threshold = Math.max(0, Math.min(1, Number(jev.threshold) || 0));
    const q = clean(jev.question || shortPoint(jev), Math.max(16, cols - 64));
    rows.push(line([
      ['  ◖ JEV 判断 · ', C.jev, true], [shortPoint(jev), C.ink, true], ['  ', C.ink], [q, C.muted],
      [`  ${ready ? conf.toFixed(2) : '0.00'} / ${threshold.toFixed(2)}`, conf >= threshold && ready ? C.green : C.amber, true],
      [`  ${ready ? verdict(jev) : '推理中'}`, ready ? (jev.disposition === 'handback' ? C.amber : C.green) : C.jev, true],
    ], cols));
    rows.push(...optionRows(jev, cols, ready, now));
    if (!ready) {
      const p = Math.max(0, Math.min(1, (now - jev.bornAt) / Math.max(1, jev.readyAt - jev.bornAt)));
      rows.push(line([['  把握线 ', C.muted], [confidenceScale(p * actualConf, threshold, Math.max(8, cols - 52)), C.muted],
        [` 线 ${threshold.toFixed(2)}  把握 ${conf.toFixed(2)}`, C.pink]], cols));
    } else {
      const scale = Math.max(8, Math.min(28, Math.floor((cols - 50) / 2)));
      rows.push(line([['  把握线 ', C.muted], [confidenceScale(conf, threshold, scale), C.muted],
        [` 线 ${threshold.toFixed(2)}   把握 ${conf.toFixed(2)}`, conf >= threshold ? C.green : C.amber, true]], cols));
    }
  } else {
    rows.push(line([['  JEV 判断：', C.jev, true], ['尚无判断记录', C.muted]], cols));
  }

  const spark = state.history.slice(-18).map((item) => {
    const glyphs = '▁▂▃▄▅▆▇█';
    const glyph = glyphs[Math.max(0, Math.min(7, Math.floor(item.confidence * 8)))];
    return color(glyph, item.disposition === 'handback' ? C.amber : C.jev);
  }).join('');
  const sparkLabel = `  把握历史 ${spark || color('等待第一次判断', C.dim)}  ·  今日顾问 ${state.advisorCalls} 次`;
  rows.push(line([[sparkLabel, C.muted]], cols));
  return rows;
}

export function verdict(event) {
  if (event.disposition === 'handback') return '交回';
  if (event.source === 'default') return '默认继续';
  return event.point === 'dispatch' ? '直接执行' : '建议';
}

function shortPoint(event) {
  return ({ dispatch: '选执行者', next_step: '下一步', wrapup: '收尾', skill: '选技能' })[event.point] || clean(event.point || '判断', 14);
}
