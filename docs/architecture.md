# azir 调度：核心接口

在线模式且判断点启用时，调用 `decide` 会把过滤和截断后的现状文字发送到 OpenRouter；现状可能包含任务说明或报错原文。请先配置额外过滤规则以处理自己的敏感内容。

## 入口

Python 3.11+ 标准库即可运行。公开函数为 `azir_dispatch.decide(point, state, *, config, log, run_id=None, actor=None, task_id=None, caused_by=None, scope=None, requested=None)`。`config` 是从 TOML 读出的字典，`log` 是判断记录路径；`state` 是现状文字。四个判断点是 `dispatch`、`next_step`、`wrapup`、`skill`。`dispatch` 的选项由 `[dispatch.executors.<执行者>.models]` 中的型号和思考等级生成，键为 `执行者:型号:思考等级`；默认答案取 `[dispatch].default`。`next_step` 的选项为 `continue`、`rethink`、`stop`，默认 `stop`。`wrapup` 的选项为 `close_and_clean`、`close_keep`、`keep`，默认 `keep`。

`skill` 要求调用方传入 `scope`。候选取 `[skill.candidates].<范围>`，默认答案取 `[skill.default].<范围>`。候选必须是非空字符串列表，默认答案必须在候选中。范围由配置定义，新增范围或候选不需要修改代码。`none` 表示不指定技能。`[points.skill].threshold` 默认 0.7。`requested` 是调用方建议的技能，可以不在候选中。提供时，核心把「调用方建议的技能：<技能>」加在现状文字前，再统一过滤和截断。

命令行与 Python 模块入口等价。调用方可额外传 `task_id` 和 `caused_by`，分别串起同一任务的多轮派发、指向导致本事件的上一条事件：

```text
bin/azir-dispatch decide <dispatch|next_step|wrapup|skill> --state-file <文件|-> [--scope <范围>] [--requested <技能>] [--config <TOML>] [--log <JSONL>] [--run-id <编号>] [--actor <发起者>] [--task-id <任务编号>] [--caused-by <事件编号>]
python3 -m azir_dispatch decide ...
bin/azir-dispatch record <dispatch|done|failed|handback|advisor> --run-id <编号> --field k=v ... [--actor <发起者>] [--task-id <任务编号>] [--caused-by <事件编号>] [--config <TOML>] [--log <JSONL>]
```

`dispatch` 可选 `--scope`，指定时默认答案取 `[dispatch.defaults].<范围>`，例如 `--scope review`；省略时仍取 `[dispatch].default`。`skill` 必须指定 `--scope`，`--requested` 仅用于 `skill`。`next_step` 和 `wrapup` 不接受范围。必需范围缺失或指定范围未配置时退出 2，不发送请求或写事件。

每个 `[points.<判断点>]` 可设置 `question` 和 `instructions`。`question` 替换内置问题，`instructions` 作为背景说明追加到问题后，两者用换行分隔，合成 HTTP 请求的 `instructions`。未配置时使用中文内置问题。`[points.<判断点>.criteria]` 按选项键覆盖选择条件，未覆盖的选项沿用内置说明。字段须为非空字符串，说明表须为 TOML 表。未知选项键视为配置错误，在发送请求和写事件之前拒绝。候选仍由 `[dispatch.executors]` 和 `[skill.candidates]` 决定，说明表不会增删候选。

`skill` 的说明表可包含所有已配置范围的候选，当前范围只发送自己的候选说明。增删执行者或技能时同步检查说明表。内置 `continue` 表示按意见修改后重新提交，`rethink` 表示重新设计，`stop` 表示需要人决定。收尾说明区分通过审查、仍需执行、待合并和已授权清理。说明只是给 JEV 的判断依据，实际动作权限由调用方核验。把握线比较逻辑保持不变，等于把握线时采用答案。

每个判断点可用 `[points.<判断点>].question` 写中文问题，用 `instructions` 写团队规则和当前判断背景，用 `[points.<判断点>.criteria]` 为选项写选择条件。参见 [配置示例](../examples/config.example.toml) 和本文上面几段的配置说明。说明表覆盖描述，候选仍由执行者或技能配置决定。派执行者不传 `--scope`；选技能才使用 `decide skill --scope development`，并配置对应范围的候选。

问题、背景说明和选项说明与现状文字一起经过内置及 `[redact].patterns` 过滤。事件记录保存过滤后的问题和说明，过滤失败时不发送 HTTP。`max_state_chars` 仍只限制现状文字。

`decide` 标准输出只有一行 JSON：`answer`、`jev_choice`、`confidence`、`threshold`、`source`、`disposition`、`cost`、`event_id`、`error`。`source` 只表示答案来源：`jev`、`rules` 或 `default`。把握达到线时 `source=jev, disposition=apply`，采用 JEV 选项；低于线时 `source=jev, disposition=handback, answer=null`，调用方交回发起派发的那一层自行决定；缺密钥或离线运行且记录成功时 `source=rules, disposition=apply`，采用配置规则默认答案；其他 JEV 故障且记录成功时 `source=default, disposition=apply`，调用方按默认答案继续。记录失败时 `source=default, disposition=handback, answer=null`，调用方不得继续派发。若无有效回答，`jev_choice` 为 `null`。阈值默认 0.7，可用 `[points.<判断点>].threshold` 单独设置，比较使用 `>=`。合法命令遇运行故障仍退出 0，并仅输出一行含错误类别的 JSON；参数或配置错误退出 2。

`[jev].mode="offline"` 或 `[points.<判断点>].enabled=false` 时，不读取 API 密钥、不执行 `[jev].key_command`，也不发送 HTTP 请求；采用默认答案并记录 `offline` 或 `disabled`。若过滤或记录失败，仍按相应错误处理。未设置模式或开关的旧配置保留在线、启用的行为。在线且启用时从 `OPENROUTER_API_KEY` 获取密钥；为空时才执行 `[jev].key_command`，将其标准输出作为密钥。标准库 HTTP 默认请求 `POST https://openrouter.ai/api/alpha/decisions`，模型为 `typesafe/jev-1.13`，整个判断默认有 3 秒预算。HTTP 不跟随重定向，任何非 2xx 状态都走默认。请求体包含 `model`、去密钥后的 `state` 和以判断点名为键的 `questions`，其中有 `type=choice`、`instructions`、`criteria`。根据 OpenRouter 的 [JEV 指南](https://openrouter.ai/docs/guides/community/jev) 和 [Decisions API 参考](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)，这是纯 HTTP 客户端的入口；旧 `POST /api/v1/systemone` 是现有 TypeSafe SDK 换地址的入口。OpenRouter 文档给出的完整模型 ID 为 `typesafe/jev-1.13`，配置可改。响应读取 `answers.<判断点>.choice`、`confidence` 和 `usage.cost`。

发送前会替换常见 API 密钥前缀、Bearer 令牌、`password=`／`token=`／`secret=` 值、PEM 私钥块和 JWT；中文紧邻凭据也会匹配。`[redact].patterns` 可增加正则；过滤在独立子进程里按判断截止时间运行，超时杀掉子进程并返回 `filter_timeout`。`strict=true` 时必须有额外规则，过滤失败或过滤后为空都会跳过 HTTP 并采用默认答案。过滤后按 `[redact].max_state_chars` 截断，默认 4000 字；截断事件记 `truncated=true`。判断记录只保存去密钥后现状文字的前 200 字。`record` 的文本字段及输出也经过相同过滤。此规则只覆盖指定的凭据形态；使用者须用额外正则去掉环境中需要隐藏的其他内容。

## 四个判断点在使用中怎么生效

包含四个判断点：

1. 派发任务时，选择执行者、模型和思考等级。选项来自使用者的配置。
2. 执行失败时，建议继续、换做法，还是停下交回主会话。
3. 执行完成时，建议是否关闭窗格、清理工作区。
4. 派发任务时，选技能。候选和默认答案按范围配置，`none` 表示不指定技能。

派发判断的把握达到设定值时，适配器采用合法答案，低于设定值时交回发起派发的那一层。默认设定值为 0.7，每个判断点可以单独调整。失败和收尾的答案交给主会话处理，参考适配器不自动重试、关闭窗格或清理工作区。Claude Code 还需要配置子代理路由，才能应用对应的派发组合。

遇到超时、缺少密钥或接口出错，采用该判断点的默认答案。核心默认给过滤、取密钥、HTTP 请求和判断记录共用 3 秒预算。这个预算不包含执行者完成任务的时间，也不是对进程启动和系统文件操作耗时的硬保证。

判断记录写不进去时，这次判断不应用，交回发起派发的那一层。

选技能与选执行者分别判断。核心通过 `decide skill --scope <范围>` 接收范围。两个参考适配器使用 `development` 范围，选中技能后在任务文字前加「用 <技能> 技能，」。选中 `none` 时保留任务文字，拿不准时交回发起派发的会话。配置示例见 [核心配置](../examples/config.example.toml)。

## 判断记录格式

JSON Lines 文件每行一条事件，版本 `v=1`。路径优先级是 `--log`、`AZIR_DISPATCH_LOG`、`[log].path`、`~/.local/state/azir-dispatch/events.jsonl`。每个进程以 `O_APPEND` 打开文件，取得文件锁后一次写入完整一行；最多等锁 500 毫秒。本功能新建的每一级目录都是 0700，文件为 0600；若最终日志目录已存在且权限宽于 0700，会向标准错误发出警告并照常写，不改变该目录权限。已有文件若以残行结尾，会先补换行并保留残行。判断记录今天不自动轮转、不截断、不自动删除。调用方应为同一次派发传同一个 `run_id`。

所有事件包含：`v`（整数）、`id`（唯一事件编号）、`ts`（UTC、带毫秒、以 `Z` 结尾）、`type`、`run_id`（一次派发的编号）、`task_id`（跨退修稳定的任务编号，可为 `null`）、`caused_by`（上一条事件 `id`，可为 `null`）、`actor`。`decide` 未指定 `run_id` 时生成新编号；`actor` 未指定时记为 `unknown`。`record` 必须传 `--run-id`。各类型附加字段如下：

| `type` | 字段 | 含义 |
| --- | --- | --- |
| `dispatch` | `requested`, `suggested`, `actual`, `executor`, `model`, `effort`, `task`, `target`, `skill_requested`, `skill_suggested`, `skill` | 前三项是 `{executor, model, effort}` 对象，`suggested` 可为 `null`；平铺字段等于 `actual`。三个技能字段分别记录调用方建议、JEV 建议和实际技能，可为字符串或 `null` |
| `decision` | `point`, `question`, `options`, `jev_choice`, `answer`, `confidence`, `threshold`, `source`, `disposition`, `cost`, `latency_ms`, `error`, `state_preview`, `truncated`, `scope`, `requested` | 判断点、选项映射、结果和脱敏摘要；`error` 无错误时为 `null`。`skill` 事件另有 `scope` 和 `requested`，未建议技能时 `requested=null`；`dispatch` 指定范围时另有 `scope` |
| `done` | `target`, `detail` | 执行者完成 |
| `failed` | `target`, `detail` | 执行者失败 |
| `handback` | `reason` | 交回发起派发的那一层 |
| `advisor` | `session`, `cwd` | 顾问调用的会话和工作目录 |

`decision` 由 `decide` 自动写入，其余类型由 `record` 写入。`--field` 每个字段传一次，例如 `--field target=worker --field detail=complete`；`dispatch` 的三组组合以 JSON 对象文本传入，`suggested=null` 表示没有建议。三个技能字段可省略，省略时写入 `null`。显式传入时使用 JSON 字符串或 `null`，例如 `--field 'skill_requested="tdd"' --field 'skill_suggested="tdd"' --field 'skill="tdd"' `。`none` 记录为字符串 `"none"`，表示明确选择不指定技能。看板应按 `run_id` 串起派发、判断和完成事件，按 `task_id` 跨退修关联，按 `id` 区分事件；未知字段可忽略。`error` 使用不含凭据的类别值，如 `offline`、`rules`、`disabled`、`missing_api_key`、`timeout`、`filter_timeout`、`http_error`、`invalid_response`、`invalid_choice`、`network_error`、`key_command_failed`。JEV 未被请求时 `cost=0`；超时或费用未知时 `cost=null`。写 `decision` 失败时入口返回 `source=default`、`disposition=handback`、`answer=null`、`error=记录失败`、`event_id=null`，调用方交回上层处理。
