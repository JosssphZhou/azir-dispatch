import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { emptyOfficeState, eventLogPath, ingestEvents, parseEvents, readNewEvents } from '../src/azir-events.mjs';
import { renderDispatchFloor } from '../src/dispatch-floor.mjs';
import { renderFrame } from '../src/render.mjs';
import { renderJevFrame } from '../src/jev-floor.mjs';
import { stripAnsi, width } from '../src/text.mjs';

const date = '2026-10-01T10:00:00.000Z';
const fixture = [
  { id: 'e1', ts: date, type: 'decision', point: 'dispatch', options: { codex: 'use Codex', claude: 'use Claude' }, jev_choice: 'codex', answer: 'codex', confidence: 0.82, threshold: 0.7, source: 'jev', disposition: 'apply', cost: 0.0003 },
  { id: 'e2', ts: date, type: 'dispatch', target: 'task-42', actual: { executor: 'codex', model: 'gpt-6.1-sol', effort: 'high' } },
  { id: 'e3', ts: date, type: 'advisor', session: 'advisor-7', cwd: '/tmp/work' },
  { id: 'e4', ts: date, type: 'decision', point: 'dispatch', options: { codex: 'use Codex', claude: 'use Claude' }, jev_choice: 'codex', answer: null, confidence: 0.31, threshold: 0.7, source: 'jev', disposition: 'handback', cost: 0.0002 },
  { id: 'e5', ts: date, type: 'handback', reason: 'confidence below threshold' },
  { id: 'e6', ts: date, type: 'done', target: 'task-42', detail: 'complete' },
];

test('reads azir event JSONL, ignores partial rows, and tails appended records', () => {
  const dir = mkdtempSync(join(tmpdir(), 'azir-office-'));
  try {
    const file = join(dir, 'events.jsonl');
    const reader = { file: '', offset: 0, pending: '' };
    writeFileSync(file, JSON.stringify(fixture[0]) + '\n' + '{partial');
    assert.deepEqual(readNewEvents(reader, file).map((event) => event.id), ['e1']);
    appendFileSync(file, '}\n' + JSON.stringify(fixture[1]) + '\n');
    assert.deepEqual(readNewEvents(reader, file).map((event) => event.id), ['e2']);
    assert.deepEqual(readNewEvents(reader, file), []);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('tracks direct dispatch, handback, completion, advisor calls, and today metrics', () => {
  const state = ingestEvents(emptyOfficeState(), fixture, { now: Date.parse(date), initial: false });
  assert.equal(state.todayDecisions, 2);
  assert.equal(state.todayCost, 0.0005);
  assert.equal(state.advisorCalls, 1);
  assert.equal(state.advisor.session, 'advisor-7');
  assert.equal(state.dispatch.status, 'done');
  assert.equal(state.managerText, 'task-42 已完成');
  assert.equal(state.lastDecision.disposition, 'handback');
  assert.deepEqual(state.flights.map((flight) => `${flight.from}:${flight.to}`), [
    'manager:jev', 'jev:worker', 'manager:jev', 'jev:manager', 'worker:jev',
  ]);
  assert.equal(parseEvents(`${JSON.stringify(fixture[0])}\nnot-json\n`).length, 1);
});

test('a new JEV decision waits for the task card to land, thinks for 0.6 seconds, then settles', () => {
  const state = ingestEvents(emptyOfficeState(), [fixture[0]], { now: 1000 });
  const at = (now) => renderJevFrame({ size: { cols: 120, rows: 40 }, officeState: state, people: [], now }).lines.map(stripAnsi).join('\n');
  // 默认飞行 2.4 秒，落地后推理 0.6 秒。
  assert.match(at(2000), /任务正送往 JEV/);
  assert.match(at(3600), /推理中/);
  const settled = at(4100);
  assert.match(settled, /把握 82%/);
  assert.match(settled, /把握线 0\.70/);
  assert.match(settled, /直接执行/);
});

test('draws 24-bit counters, special desks, JEV options, confidence, and spark history', () => {
  const state = ingestEvents(emptyOfficeState(), fixture.slice(0, 3), { now: Date.parse(date), initial: true });
  const lines = renderDispatchFloor(state, [
    { status: 'blocked' }, { status: 'working' }, { status: 'done' }, { status: 'unknown' },
  ], { cols: 120, now: Date.parse(date) });
  const plain = lines.map(stripAnsi);
  assert.ok(plain.some((row) => row.includes('待批准 1')));
  assert.ok(plain.some((row) => row.includes('工作中 1')));
  assert.ok(plain.some((row) => row.includes('完成 1')));
  assert.ok(plain.some((row) => row.includes('未知 1')));
  assert.ok(plain.some((row) => row.includes('执行协调')));
  assert.ok(plain.some((row) => row.includes('JEV')));
  assert.ok(plain.some((row) => row.includes('顾问')));
  assert.ok(plain.some((row) => row.includes('直接执行')));
  assert.ok(plain.some((row) => row.includes('82%≈')));
  assert.ok(plain.some((row) => row.includes('把握历史')));
  assert.ok(lines.some((row) => row.includes('\x1b[38;2;')));
  assert.ok(lines.every((row) => width(row) <= 120));
});

test('shows handback and advisor activity with a compact terminal width', () => {
  const state = ingestEvents(emptyOfficeState(), fixture.slice(0, 5), { now: Date.parse(date), initial: true });
  const lines = renderDispatchFloor(state, [{ status: 'working' }], { cols: 80, now: Date.parse(date) });
  const plain = lines.map(stripAnsi).join('\n');
  assert.match(plain, /交回/);
  assert.match(plain, /顾问/);
  assert.ok(lines.every((row) => width(row) <= 80));
});

test('keeps the three special desks visible and reduces the worker area on short screens', () => {
  const state = ingestEvents(emptyOfficeState(), fixture.slice(0, 3), { now: Date.parse(date), initial: true });
  const officeLines = renderDispatchFloor(state, [{ status: 'working' }], { cols: 80, now: Date.parse(date) });
  const rendered = renderFrame({
    people: [{ id: 'w1:p1', name: 'Ada', kind: 'codex', status: 'working', since: 0, cwd: '/tmp', title: 'task', tabName: 'tab' }],
    rooms: [], stats: { worked: 0, waiting: 0, answers: 0 }, counts: { working: 1 }, total: 1,
    filter: '', filtering: false, following: false, zoom: 'auto', selectedId: 'w1:p1', frame: 0,
    now: Date.now(), size: { cols: 80, rows: 24 }, message: '', drag: null, busy: null, hire: null, compose: null, trust: null,
    answerEnabled: false, officeLines,
  });
  assert.equal(rendered.lines.length, 24);
  assert.ok(rendered.lines.map(stripAnsi).join('\n').includes('顾问'));
  assert.ok(rendered.lines.every((row) => width(row) <= 80));
});

test('uses the configured log path, then the local default and boss-machine log', () => {
  assert.equal(eventLogPath({ AZIR_DISPATCH_LOG: '/tmp/custom.jsonl' }, '/example-home/test'), '/tmp/custom.jsonl');
  assert.equal(eventLogPath({ AZIR_DISPATCH_LOG: '~/.claude/state/azir-dispatch/events.jsonl' }, '/example-home/test'), '/example-home/test/.claude/state/azir-dispatch/events.jsonl');
  assert.equal(eventLogPath({}, '/example-home/empty'), '/example-home/empty/.local/state/azir-dispatch/events.jsonl');
});

test('hides approve and reject controls unless explicitly enabled', () => {
  const base = {
    people: [{ id: 'w1:p1', name: 'Ada', kind: 'codex', status: 'blocked', since: 0, cwd: '/tmp', title: 'approval needed', tabName: 'test', choice: { approve: ['y'], deny: ['n'] } }],
    rooms: [], stats: { worked: 0, waiting: 0, answers: 0 }, counts: { blocked: 1 }, total: 1,
    filter: '', filtering: false, following: false, zoom: 'auto', selectedId: 'w1:p1', frame: 0,
    now: Date.now(), size: { cols: 120, rows: 40 }, message: '', drag: null, busy: null, hire: null, compose: null, trust: null,
  };
  const safe = renderFrame({ ...base, answerEnabled: false });
  const safeText = safe.lines.map(stripAnsi).join('\n');
  assert.doesNotMatch(safeText, /\[y\]|\[n\]|y approve|n deny/);
  assert.deepEqual(safe.hitboxes.filter((box) => box.action === 'approve' || box.action === 'deny'), []);
  const enabled = renderFrame({ ...base, answerEnabled: true });
  assert.match(enabled.lines.map(stripAnsi).join('\n'), /\[y\]/);
  assert.ok(enabled.hitboxes.some((box) => box.action === 'approve'));
});

test('a real dispatch to one of the five models spins the slot machine before the bars settle', () => {
  const event = { ...fixture[0], id: 'slot', options: { 'codex-cli:gpt-6-luna:medium': '简单', 'grok:grok-build:default': '搜索' },
    jev_choice: 'grok:grok-build:default', answer: 'grok:grok-build:default', confidence: 1 };
  const state = ingestEvents(emptyOfficeState(), [event], { now: 1000 });
  const at = (now) => renderJevFrame({ size: { cols: 120, rows: 40 }, officeState: state, people: [], now }).lines.map(stripAnsi).join('\n');
  assert.match(at(4000), /●●●●●●●●/);
  assert.ok(at(5700).includes('JEV 选中 Grok · 把握 1.00'));
  assert.match(at(6300), /把握线 0\.70/);
  assert.doesNotMatch(at(6300), /JEV 选中/);
});
