import test from 'node:test';
import assert from 'node:assert/strict';
import { demoAgents, demoBackground, demoEvents, DEMO_CYCLE_MS } from '../src/demo.mjs';
import { workspaceAgents } from '../src/workspace.mjs';
import { emptyOfficeState, ingestEvents } from '../src/azir-events.mjs';
import { renderFrame } from '../src/render.mjs';
import { Roster } from '../src/roster.mjs';
import { stripAnsi, width } from '../src/text.mjs';

// 按演示的时间线读入事件，返回某一刻的办公室画面（纯文字）。
function demoFrame(time, { cols = 120, rows = 40, showAll = false } = {}) {
  const state = emptyOfficeState();
  ingestEvents(state, demoBackground(0), { now: 0, initial: true });
  for (const event of demoEvents(-1, time, 0)) {
    if (event.type === 'manager') state.managerText = event.text;
    else ingestEvents(state, [event], { now: event.at });
  }
  const roster = new Roster(() => time);
  roster.update(demoAgents(time));
  const view = { people: roster.people, size: { cols, rows }, demo: true, officeState: state, showAll, now: time, frame: Math.floor(time / 320) };
  return renderFrame(view).lines;
}

test('one demo lap follows the workflow: boss, main agent, manager, JEV four times, review, back to the main agent', () => {
  const events = demoEvents(-1, DEMO_CYCLE_MS, 0);
  const dispatched = events.filter((e) => e.type === 'decision' && e.point === 'dispatch');
  assert.deepEqual(dispatched.map((e) => [e.question, e.jev_choice.split(':')[1]]),
    [['统计文档行数', 'gpt-6-luna'], ['重构搜索索引并补测试', 'gpt-6.1-sol'], ['写发布帖', 'gemini-3.7-flash'], ['查 X 上的反馈帖子', 'grok-build']]);
  for (const d of dispatched) assert.equal(Object.keys(d.options).length, 5);
  const wraps = events.filter((e) => e.point === 'wrapup');
  assert.deepEqual(wraps.map((e) => e.disposition), ['apply']);
  const hops = events.filter((e) => e.type === 'hop').map((e) => `${e.from}>${e.to}`);
  assert.deepEqual(hops, ['boss>main', 'main>manager', 'worker>review', 'review>manager', 'worker>review', 'review>manager', 'manager>main']);
  assert.equal(new Set(demoEvents(-1, DEMO_CYCLE_MS * 2, 0).map((e) => e.id)).size, events.length * 2);
  // 演示里只允许出现 example-project 执行协调这个名字，不出现真实路径和工作区。
  const text = (JSON.stringify(events) + JSON.stringify(demoAgents(0))).replaceAll('mgr-example-project', '');
  assert.doesNotMatch(text, /\/Users\/|\/Volumes\/|example-project|wDY/);
});

test('the demo floor shows the manager, JEV and all five model zones, and hides panes JEV never dispatched', () => {
  const plain = demoFrame(41_000).map(stripAnsi).join('\n');
  for (const text of ['执行协调', 'JEV 判断', '6.1 Sol 区', 'Luna 区', 'Opus 区', 'Gemini 区', 'Grok 区', '每百万 $2.00 / $10.00',
    '统计文档行数', '写发布帖', 'JEV 93%', 'JEV 88%', 'JEV 91%', 'JEV 95%', '把握线 0.70']) {
    assert.ok(plain.includes(text), `缺少「${text}」`);
  }
  assert.doesNotMatch(plain, /和你对话|文档整理/);
  const all = demoFrame(41_000, { cols: 140, showAll: true }).map(stripAnsi).join('\n');
  assert.match(all, /其他窗格 2 个/);
  assert.match(all, /其他窗格/);
});

test('the 120x40 floor draws every layer of the workflow, and the reviewers light up in turn', () => {
  const plain = demoFrame(47_000).map(stripAnsi).join('\n');
  for (const text of [' 你 ', ' 主会话 ', '顾问 · Fable', '协助审查', 'azir 执行协调', 'example-project 执行协调',
    'JEV 判断', 'Sol 审查', 'Claude 审查', '录屏证明', '▀▀▀▀▀▀▀▀']) assert.ok(plain.includes(text), `缺少「${text}」`);
  assert.doesNotMatch(plain, /mgr-|review-/);
  assert.match(demoFrame(59_000).map(stripAnsi).join('\n'), /录屏证明[\s\S]*2 段/);
  // 窄时你一排和执行协调一排折成一行，审查排省掉。
  const narrow = demoFrame(47_000, { cols: 100, rows: 36 }).map(stripAnsi).join('\n');
  assert.match(narrow, /▌你/);
  assert.match(narrow, /azir 执行协调/);
  assert.doesNotMatch(narrow, /录屏证明/);
});

test('a dispatch decision spins the slot machine and lands on the model JEV chose', () => {
  const spinning = demoFrame(7_100).map(stripAnsi).join('\n');
  assert.match(spinning, /●●●●●●●●/);
  assert.match(spinning, /推理中/);
  const win = demoFrame(8_800).map(stripAnsi);
  assert.ok(win.join('\n').includes('JEV 选中 Luna · 把握 0.93'));
  const payline = win.find((row) => row.includes('▶') && row.includes('◀'));
  assert.equal(payline.match(/Luna/g).length >= 3, true, payline);
  assert.match(win.join('\n'), /[✦✧⋆]/);
  // 老虎机转完回到概率条。
  const after = demoFrame(9_500).map(stripAnsi).join('\n');
  assert.doesNotMatch(after, /●●●●●●●●|JEV 选中/);
  assert.match(after, /把握线 0\.70/);
  assert.ok(demoFrame(26_800).map(stripAnsi).join('\n').includes('JEV 选中 Gemini · 把握 0.91'));
});

test('wrap-up decisions never spin the slot machine', () => {
  for (let time = 40_000; time <= DEMO_CYCLE_MS; time += 100) {
    const plain = demoFrame(time).map(stripAnsi).join('\n');
    assert.doesNotMatch(plain, /●●●●●●●●|JEV 选中/, `time=${time}`);
  }
});

test('cards fly along the wires and come back marked as wrapped up or handed back', () => {
  assert.match(demoFrame(1_000).map(stripAnsi).join('\n'), /▣ 一句话/);
  assert.match(demoFrame(2_900).map(stripAnsi).join('\n'), /▣ 任务书 · 4 件/);
  assert.match(demoFrame(11_500).map(stripAnsi).join('\n'), /▣ 统计文档行数/);
  assert.match(demoFrame(44_000).map(stripAnsi).join('\n'), /▣ 统计文档… · 直接收尾/);
  assert.match(demoFrame(47_000).map(stripAnsi).join('\n'), /▣ 重构搜索索引/);
  assert.match(demoFrame(49_600).map(stripAnsi).join('\n'), /· 审查通过/);
  assert.match(demoFrame(54_600).map(stripAnsi).join('\n'), /· 截图通过/);
  const merge = demoFrame(57_200).map(stripAnsi).join('\n');
  assert.match(merge, /▣ 4 件完成/);
  assert.match(merge, /合并、推送/);
});

test('narrow panes fold the zones without desks into labels and still draw the slot machine', () => {
  const plain = demoFrame(8_800, { cols: 100, rows: 36 }).map(stripAnsi).join('\n');
  assert.ok(plain.includes('JEV 选中 Luna · 把握 0.93'));
  for (const name of ['Gemini 区', 'Grok 区', '6.1 Sol 区']) assert.ok(plain.includes(name), name);
});

test('every frame of a lap fits the terminal exactly, from 120x40 down to a tiny pane', () => {
  const times = [];
  for (let time = 0; time <= DEMO_CYCLE_MS; time += 700) times.push(time);
  for (let time = 6_400; time <= 9_500; time += 80) times.push(time);
  for (const [cols, rows] of [[120, 40], [100, 36], [80, 28], [60, 20]]) {
    for (const time of times) {
      const lines = demoFrame(time, { cols, rows });
      assert.equal(lines.length, rows);
      lines.forEach((row) => assert.equal(width(row), cols, `${cols}x${rows} time=${time}`));
    }
  }
});

test('workspace scope uses launch identity, supports all workspaces and does not follow focus', () => {
  const agents = [{ pane_id: 'own', workspace_id: 'wDY' }, { pane_id: 'other', workspace_id: 'private', focused: true }];
  assert.deepEqual(workspaceAgents(agents, null, { HERDR_WORKSPACE_ID: 'wDY' }), [agents[0]]);
  assert.deepEqual(workspaceAgents(agents, null, { HERDR_PANE_ID: 'own' }), [agents[0]]);
  assert.deepEqual(workspaceAgents(agents, { layouts: [{ workspace_id: 'wDY', panes: [{ pane_id: 'launcher' }] }] }, { HERDR_PANE_ID: 'launcher' }), [agents[0]]);
  assert.deepEqual(workspaceAgents(agents, null, {}), []);
  assert.deepEqual(workspaceAgents(agents, null, {}, true), agents);
});
