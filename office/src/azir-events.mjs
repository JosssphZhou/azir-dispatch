import { closeSync, openSync, readSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { sanitize } from './text.mjs';

// 派发判断的老虎机时长，渲染和事件时间线共用。
export const SLOT_MS = 2800;

export function eventLogPath(env = process.env, home = homedir()) {
  if (env.AZIR_DISPATCH_LOG) return env.AZIR_DISPATCH_LOG.replace(/^~(?=\/|$)/, home);
  const local = join(home, '.local/state/azir-dispatch/events.jsonl');
  const claude = join(home, '.claude/state/azir-dispatch/events.jsonl');
  try {
    statSync(claude);
    return claude;
  } catch {
    return local;
  }
}

export function parseEvents(text) {
  return String(text).split(/\r?\n/).flatMap((line) => {
    if (!line.trim()) return [];
    try {
      const event = JSON.parse(line);
      return event && typeof event === 'object' && typeof event.type === 'string' ? [event] : [];
    } catch {
      return [];
    }
  });
}

export function emptyOfficeState() {
  return { seen: new Set(), history: [], lastDecision: null, dispatch: null, advisor: null, flights: [],
    decisions: new Map(), workers: new Map(), todayModels: { sol: 0, luna: 0, opus: 0, gemini: 0, grok: 0, other: 0 },
    todayDirect: 0, todayHandback: 0, unknownCosts: 0, receipt: '',
    managerText: '等待派发记录', todayDecisions: 0, todayCost: 0, advisorCalls: 0, now: Date.now() };
}

export function modelZone(model) {
  const name = String(model || '').toLowerCase();
  return name.includes('luna') ? 'luna' : name.includes('sol') ? 'sol' : name.includes('opus') ? 'opus'
    : name.includes('gemini') ? 'gemini' : name.includes('grok') ? 'grok' : 'other';
}

export function officeWorkers(state, people = [], all = false) {
  const matched = new Set();
  const workers = [...state.workers.values()].map((record) => {
    const live = people.find((p) => p.agentName === record.targetName || p.id === record.targetName);
    if (live) matched.add(live.id);
    return { ...live, ...record, id: live?.id || `record:${record.targetName}`,
      status: record.status === 'working' ? live?.status || 'working' : record.status === 'failed' ? 'blocked' : record.status,
      command: live?.command || record.command || null, head: live?.head || null, jev: true };
  });
  if (all) for (const live of people) if (!matched.has(live.id)) workers.push({ ...live,
    targetName: live.agentName || live.name, task: live.title || '未记录任务', model: live.head?.model || live.kind,
    reason: '未经过 JEV 派发', confidence: null, jev: false });
  return workers;
}

function workerFor(state, event) {
  return [...state.workers.values()].reverse().find((w) =>
    (event.target && w.targetName === event.target) || (event.run_id && w.run_id === event.run_id) ||
    (event.task_id && w.task_id === event.task_id) ||
    (event.point === 'wrapup' && String(event.state_preview || '').includes(`Agent ${w.targetName} `)));
}

// 新飞行排在上一段飞行之后，返回这一段落地的时间。
function flightEnd(state, now, duration) {
  const previous = state.flights.at(-1);
  return Math.max(now, previous ? previous.startedAt + previous.duration : now) + duration;
}

function queueFlight(state, from, to, target, duration, now, extra = {}) {
  const previous = state.flights.at(-1);
  const startedAt = Math.max(now, previous ? previous.startedAt + previous.duration : now);
  state.flights.push({ from, to, target, duration, startedAt, ...extra });
  if (state.flights.length > 30) state.flights.shift();
}

function displayTarget(event) {
  return sanitize(event.target || event.actual?.executor || event.executor || '执行者');
}

export function ingestEvents(state, events, { now = Date.now(), initial = false } = {}) {
  for (const event of events) {
    if (!event?.id || state.seen.has(event.id)) continue;
    state.seen.add(event.id);
    if (state.seen.size > 4000) state.seen.delete(state.seen.values().next().value);
    const parsedTs = Date.parse(event.ts);
    const ts = Number.isFinite(parsedTs) ? parsedTs : now;
    const isToday = new Date(ts).toDateString() === new Date(now).toDateString();
    if (event.type === 'decision') {
      // 先看到任务卡飞到 JEV；派发判断转一次老虎机（约 2.8 秒），收尾判断显示约 0.6 秒「推理中」。
      const arrive = initial ? now : flightEnd(state, now, event.point === 'wrapup' ? 0 : event.flightMs || 2400);
      const spins = event.point === 'dispatch' && modelZone(event.jev_choice ?? event.answer) !== 'other';
      const hold = event.animationMs ?? (spins ? SLOT_MS : 600);
      const decision = { ...event, bornAt: initial ? null : now, arriveAt: initial ? null : arrive, readyAt: initial ? null : arrive + hold };
      state.lastDecision = decision;
      if (event.point === 'dispatch') state.decisions.set(event.run_id || event.id, decision);
      state.history.push({ confidence: Number(event.confidence) || 0, disposition: event.disposition || 'apply', ts });
      state.history = state.history.slice(-18);
      if (isToday) {
        state.todayDecisions += 1;
        if (event.cost != null && Number.isFinite(Number(event.cost))) state.todayCost += Number(event.cost);
        else state.unknownCosts += 1;
        if (event.disposition === 'handback') state.todayHandback += 1;
        else if (event.point === 'dispatch') state.todayDirect += 1;
      }
      state.managerText = `交给 JEV 判断：${sanitize(event.question || { dispatch: '派给哪个执行者', next_step: '下一步', wrapup: '怎么收尾', skill: '用哪个技能' }[event.point] || '判断')}`;
      if (event.point === 'wrapup') {
        const worker = workerFor(state, event);
        const result = event.disposition === 'handback' || event.answer === 'keep' ? '交回' : '直接收尾';
        state.receipt = `${worker?.task || '任务'} · ${result}`;
        state.managerText = state.receipt;
        if (worker) { worker.wrapup = decision; worker.result = result; }
        if (!initial) queueFlight(state, 'jev', 'manager', worker?.targetName || '工程经理', event.flightMs || 3200, decision.readyAt ?? now,
          { task: worker?.task || '收尾判断', result, zone: modelZone(worker?.model) });
      } else if (!initial) queueFlight(state, 'manager', 'jev', 'JEV', event.flightMs || 2400, now,
        { task: event.question || '判断任务' });
    } else if (event.type === 'dispatch') {
      const decision = state.decisions.get(event.run_id) || (!event.run_id ? [...state.decisions.values()].at(-1) : null);
      const actual = event.actual || event;
      const selected = [actual.executor, actual.model, actual.effort].filter(Boolean).join(':');
      const option = decision?.options?.[selected] ?? decision?.options?.[decision?.answer];
      state.dispatch = { ...event, targetName: displayTarget(event), status: 'working', ts,
        model: actual.model || '型号未记录', task: sanitize(event.task || decision?.question || '任务未记录'),
        reason: typeof option === 'string' ? sanitize(option) : '未记录选项说明',
        confidence: decision?.confidence ?? null, threshold: decision?.threshold ?? null, decision,
        selected, choice: decision?.answer || null };
      state.workers.set(state.dispatch.targetName, state.dispatch);
      if (isToday) state.todayModels[modelZone(state.dispatch.model)] += 1;
      state.managerText = `派发至 ${state.dispatch.targetName}`;
      // 推理结束后才从 JEV 飞出。
      if (!initial) queueFlight(state, 'jev', 'worker', state.dispatch.targetName, event.flightMs || 3200, Math.max(now, decision?.readyAt ?? now),
        { task: state.dispatch.task, zone: modelZone(state.dispatch.model) });
    } else if (event.type === 'done' || event.type === 'failed') {
      const worker = workerFor(state, event);
      if (worker) worker.status = event.type;
      state.dispatch = { ...(state.dispatch || {}), ...event, targetName: displayTarget(event), status: event.type, ts };
      state.managerText = event.type === 'done' ? `${state.dispatch.targetName} 已完成` : `${state.dispatch.targetName} 执行失败`;
      if (!initial) queueFlight(state, 'worker', 'jev', worker?.targetName || displayTarget(event), event.flightMs || 2400, now,
        { task: worker?.task || '完成任务', zone: modelZone(worker?.model) });
    } else if (event.type === 'handback') {
      state.managerText = `收回判断 · ${event.reason || '需要工程经理处理'}`;
      if (!initial) queueFlight(state, 'jev', 'manager', '工程经理', event.flightMs || 620, now);
    } else if (event.type === 'hop') {
      // 流程里的一段飞行：老板 → 主 Agent → 工程经理，执行者 → 审查 → 工程经理 → 主 Agent。
      state.says = { ...(state.says || {}), ...Object.fromEntries(Object.entries(event.say || {}).map(([k, v]) => [k, sanitize(v)])) };
      if (event.manager_text) state.managerText = sanitize(event.manager_text);
      const worker = event.target ? workerFor(state, event) : null;
      if (worker && event.status) worker.status = event.status;
      if (worker && event.result) worker.result = sanitize(event.result);
      if (event.from === 'review' && event.to === 'manager') state.proofs = (state.proofs || 0) + 1;
      if (!initial) queueFlight(state, event.from, event.to, event.to, event.flightMs || 2000, now,
        { task: sanitize(event.task || '任务'), zone: event.zone, reviewer: event.reviewer ?? 0, result: event.result ? sanitize(event.result) : undefined, tint: event.tint });
    } else if (event.type === 'advisor') {
      state.advisor = { ...event, session: sanitize(event.session || '协助'), ts, until: (initial ? ts : now) + (event.holdMs || 12000) };
      if (isToday) state.advisorCalls += 1;
    }
  }
  state.now = now;
  return state;
}

export function readNewEvents(reader, file, now = Date.now()) {
  let info;
  try { info = statSync(file); } catch { return []; }
  const rotated = info.size < reader.offset || reader.file !== file;
  if (info.size === reader.offset && !rotated) return [];
  if (rotated) reader.pending = '';
  const fd = openSync(file, 'r');
  try {
    const start = rotated ? 0 : reader.offset;
    const bytes = Buffer.alloc(Math.max(0, info.size - start));
    const read = readSync(fd, bytes, 0, bytes.length, start);
    reader.offset = start + read;
    reader.file = file;
    const text = (reader.pending || '') + bytes.subarray(0, read).toString('utf8');
    const lines = text.split(/\r?\n/);
    reader.pending = lines.pop() || '';
    return parseEvents(lines.join('\n'));
  } finally {
    closeSync(fd);
  }
}
