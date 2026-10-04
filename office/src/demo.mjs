// 录屏演示用的虚构内容。这里不读路径、判断记录或任何接口。
//
// 一轮 61 秒，按示例协作流程走：老板一句话 → 主 Agent 写任务书交给 azir 工程经理 →
// 工程经理派发前问 JEV，JEV 每次转一次老虎机：统计文档给 Luna，重构代码给 6.1 Sol，
// 写发布帖给 Gemini，查 X 帖子给 Grok → 执行者干活 → 统计那件由 JEV 直接收尾；重构那件由 Sol 审查，
// 开场前就在做的看板界面由 Claude 看截图 → 回到工程经理 → 主 Agent 合并、推送。
export const DEMO_CYCLE_MS = 61_000;

const LUNA = 'codex-cli:gpt-6-luna:medium';
const SOL = 'codex-cli:gpt-6.1-sol:high';
const OPUS = 'claude:claude-opus-5-5:high';
const GEMINI = 'pi:gemini-3.7-flash:default';
const GROK = 'grok:grok-build:default';
const OPTIONS = {
  [SOL]: '复杂开发和审查',
  [LUNA]: '简单只读和改文档',
  [OPUS]: '规划和前端',
  [GEMINI]: '中文写作和润色',
  [GROK]: '联网搜索和 X 帖子',
};
const WRAPUP = { close_keep: '关闭窗格、保留结果', keep: '交回工程经理' };

// 选中项拿 confidence，其余按固定比例分剩下的概率。
const SHARE = { [SOL]: 0.35, [LUNA]: 0.25, [OPUS]: 0.2, [GEMINI]: 0.12, [GROK]: 0.08 };
const probabilities = (pick, confidence) => {
  const rest = Object.keys(OPTIONS).filter((key) => key !== pick);
  const total = rest.reduce((sum, key) => sum + SHARE[key], 0);
  return Object.fromEntries(Object.keys(OPTIONS).map((key) => [key, key === pick ? confidence : (1 - confidence) * SHARE[key] / total]));
};

// 交给 JEV（飞行 2.2 秒）→ 老虎机 2.8 秒 → 概率条停留 1 秒 → 飞往型号区。
function dispatchJob(at, { run, task, pick, model, confidence, target, command, cost }) {
  const [executor, , effort] = pick.split(':');
  return [
    { at, type: 'manager', text: `交给 JEV：${task}` },
    { at: at + 0.3, type: 'decision', run_id: run, point: 'dispatch', question: task, options: OPTIONS,
      probabilities: probabilities(pick, confidence), answer: pick, jev_choice: pick, confidence, threshold: 0.7,
      source: 'jev', disposition: 'apply', cost, flightMs: 2200 },
    { at: at + 6.3, type: 'dispatch', run_id: run, task, target, command,
      actual: { executor, model, effort }, flightMs: 2400 },
  ];
}

function wrapJob(at, { run, target, task, keep, confidence }) {
  return [
    { at, type: 'done', run_id: run, target, flightMs: 2000 },
    { at: at + 2.2, type: 'decision', run_id: run, target, point: 'wrapup', question: `${task}：怎么收尾`,
      options: WRAPUP, probabilities: { close_keep: keep ? 1 - confidence : confidence, keep: keep ? confidence : 1 - confidence },
      answer: keep ? null : 'close_keep', jev_choice: keep ? 'keep' : 'close_keep', confidence, threshold: 0.7,
      source: 'jev', disposition: keep ? 'handback' : 'apply', cost: 0.0002, flightMs: 2800, animationMs: 600 },
  ];
}

const EASY = { run: 'demo-easy', task: '统计文档行数', pick: LUNA, model: 'gpt-6-luna', confidence: 0.93,
  target: 'luna-统计', command: 'wc -l docs/*.md', cost: 0.0003 };
const HARD = { run: 'demo-hard', task: '重构搜索索引并补测试', pick: SOL, model: 'gpt-6.1-sol', confidence: 0.88,
  target: 'sol-搜索索引', command: 'cargo test', cost: 0.0004 };
const WRITE = { run: 'demo-write', task: '写发布帖', pick: GEMINI, model: 'gemini-3.7-flash', confidence: 0.91,
  target: 'gemini-发布帖', command: '润色第二段', cost: 0.0003 };
const UI = { run: 'demo-ui', task: '做数据看板界面', target: 'opus-看板界面' };
const SEARCH = { run: 'demo-search', task: '查 X 上的反馈帖子', pick: GROK, model: 'grok-build', confidence: 0.95,
  target: 'grok-查帖子', command: 'x search', cost: 0.0003 };

// 各段在一轮里的时间点（秒），demoAgents 也按这里推算窗格状态。
const AT = { boss: 0.3, brief: 2.0, easy: 4.0, hard: 13.0, write: 22.0, search: 31.0, easyDone: 40,
  solReview: 46, solBack: 48.3, uiReview: 51.2, uiBack: 53.4, merge: 56.3 };
const LANDED = 6.3 + 2.4;

const hop = (at, from, to, extra) => ({ at, type: 'hop', from, to, ...extra });

const schedule = [
  hop(AT.boss, 'boss', 'main', { task: '一句话', flightMs: 1600,
    say: { boss: '统计、重构、发布帖、查反馈，今晚做完', main: '写任务书', review0: '空闲', review1: '空闲' } }),
  hop(AT.brief, 'main', 'manager', { task: '任务书 · 4 件', flightMs: 1800, manager_text: '收到任务书：4 件' }),
  ...dispatchJob(AT.easy, EASY),
  ...dispatchJob(AT.hard, HARD),
  ...dispatchJob(AT.write, WRITE),
  ...dispatchJob(AT.search, SEARCH),
  ...wrapJob(AT.easyDone, { ...EASY, keep: false, confidence: 0.91 }),
  hop(AT.solReview, 'worker', 'review', { task: HARD.task, zone: 'sol', reviewer: 0, target: HARD.target, run_id: HARD.run,
    status: 'done', flightMs: 2000, say: { review0: '审查重构' } }),
  { at: AT.solReview + 0.4, type: 'advisor', session: '协助审查', holdMs: 4000 },
  hop(AT.solBack, 'review', 'manager', { task: HARD.task, zone: 'sol', reviewer: 0, target: HARD.target, run_id: HARD.run,
    result: '审查通过', flightMs: 2600, manager_text: '审查通过：重构搜索索引', say: { review0: '通过' } }),
  hop(AT.uiReview, 'worker', 'review', { task: UI.task, zone: 'opus', reviewer: 1, target: UI.target, run_id: UI.run,
    status: 'done', flightMs: 2000, say: { review1: '看截图' } }),
  hop(AT.uiBack, 'review', 'manager', { task: UI.task, zone: 'opus', reviewer: 1, target: UI.target, run_id: UI.run,
    result: '截图通过', flightMs: 2600, manager_text: '截图通过：数据看板界面', say: { review1: '通过' } }),
  hop(AT.merge, 'manager', 'main', { task: '4 件完成', flightMs: 1800, manager_text: '交给主 Agent 合并', say: { main: '合并、推送' } }),
].sort((a, b) => a.at - b.at);

// 开场前已经在做的前端任务，不播放派发动画，只让 Opus 区有一张工位卡。
export function demoBackground(startedAt) {
  const ts = new Date(startedAt - 60_000).toISOString();
  return [
    { id: '演示-背景-判断', ts, type: 'decision', run_id: 'demo-ui', point: 'dispatch', question: '做数据看板界面',
      options: OPTIONS, probabilities: probabilities(OPUS, 0.86), answer: OPUS, jev_choice: OPUS, confidence: 0.86,
      threshold: 0.7, source: 'jev', disposition: 'apply', cost: 0.0004 },
    { id: '演示-背景-派发', ts, type: 'dispatch', run_id: 'demo-ui', task: '做数据看板界面', target: 'opus-看板界面',
      command: 'bun run dev', actual: { executor: 'claude', model: 'claude-opus-5-5', effort: 'high' } },
  ];
}

export function demoEvents(fromMs, toMs, startedAt) {
  const events = [];
  for (let lap = Math.max(0, Math.floor(Math.max(0, fromMs) / DEMO_CYCLE_MS)); lap <= Math.floor(toMs / DEMO_CYCLE_MS); lap += 1) {
    schedule.forEach((item, index) => {
      const at = lap * DEMO_CYCLE_MS + item.at * 1000;
      if (at > fromMs && at <= toMs) events.push({ ...item, id: `演示-${lap}-${index}`, ts: new Date(startedAt + at).toISOString(), at });
    });
  }
  return events;
}

// 工作区里的窗格。前五个是 JEV 派出的执行者，后两个不经过 JEV，默认不显示，按 a 才出现。
export function demoAgents(elapsedMs) {
  const phase = (elapsedMs % DEMO_CYCLE_MS) / 1000;
  const lap = Math.floor(elapsedMs / DEMO_CYCLE_MS);
  const state = (start, end) => (phase >= end ? 'done' : phase >= start ? 'working' : lap > 0 ? 'done' : 'idle');
  const rows = [
    ['luna-统计', 'codex', state(AT.easy + LANDED, AT.easyDone), '统计文档行数'],
    ['sol-搜索索引', 'codex', state(AT.hard + LANDED, AT.solReview), '重构搜索索引并补测试'],
    ['gemini-发布帖', 'pi', state(AT.write + LANDED, Infinity), '写发布帖'],
    ['grok-查帖子', 'grok', state(AT.search + LANDED, Infinity), '查 X 上的反馈帖子'],
    ['opus-看板界面', 'claude', state(-1, AT.uiReview), '做数据看板界面'],
    ['mgr-azir', 'claude', 'working', '派发和验收'],
    ['mgr-example-project', 'claude', 'idle', '空闲'],
    ['review-sol', 'codex', state(AT.solReview, AT.solBack + 2.6), '审查代码'],
    ['review-claude', 'claude', state(AT.uiReview, AT.uiBack + 2.6), '看截图'],
    ['主对话', 'claude', 'idle', '和老板对话'],
    ['文档整理', 'claude', 'working', '整理会议纪要'],
  ];
  return rows.map(([name, agent, status, title], i) => ({
    pane_id: `demo:p${i + 1}`, workspace_id: 'demo', tab_id: `demo:t${i + 1}`, name, agent, agent_status: status,
    state_change_seq: lap * 3 + (status === 'done' ? 2 : status === 'working' ? 1 : 0),
    cwd: '', terminal_title_stripped: title, project: '演示', branch: 'main', focused: false,
  }));
}
