# 参考适配器

调用判断点时，经过过滤的任务说明和报错原文会发往 OpenRouter。事件文件保存在本机。顾问扫描只导出调用编号、时间、会话和工作目录，不导出会话正文。使用前请按自己的环境设置型号、思考等级和过滤规则。

## 适配器的两项职责

适配器派一个任务，并报告任务做完或失败。派发前调用核心的 `decide("dispatch", ...)`，采用 `disposition=apply` 的答案。`source=rules` 执行配置规则答案，`source=default` 同样执行配置默认答案。`disposition=handback` 时交回发起派发的会话。选执行者后单独调用 `decide("skill", ..., scope="development")`。技能判断低于把握线时也交回，不运行执行工具。失败后问 `next_step`，完成后问 `wrapup`。这些后续答案返回给主会话，由主会话决定下一步。适配器不会重试、关闭窗格或删除工作区。

适配器只调用公开的 `azir_dispatch.decide` 和 `azir_dispatch.events.append_event`，不读取核心的私有函数。字段和来源约定见 [核心接口](architecture.md)。所有入口只需 Python 3.11 或更新版本的标准库，在支持文件锁的 macOS 或 Linux 上运行。

## 配置

以 `examples/adapters.example.toml` 为起点，另存一份属于自己的配置。所有型号都是占位符，须改成自己的 CLI 可用型号。核心的基础配置见 `examples/config.example.toml`。

两个适配器分别限制 JEV 只能在 `claude-code` 或 `codex` 执行者的组合中选。默认答案优先取 `[adapters.claude_code].default` 或 `[adapters.codex].default`，其次取 `[dispatch].default`。公共默认属于另一个执行者时，取当前执行者配置中的第一项。显式的适配器默认答案不合法则报错。

配置路径依次取 `--config`、`AZIR_DISPATCH_CONFIG`。事件路径依次取 `--log`、`AZIR_DISPATCH_LOG`、`[log].path`、`~/.local/state/azir-dispatch/events.jsonl`。两个适配器要求配置 `[skill.default].development` 和 `[skill.candidates].development`。基础和适配器配置示例均包含技能配置。技能判断使用 `[points.skill].threshold`，默认 0.7。

安装前务必设置自己需要的 `[redact].patterns`。

## Claude Code

在 shell 中将 `AZIR_DISPATCH_ROOT` 设置为仓库的绝对路径，将 `AZIR_DISPATCH_CONFIG` 设置为自己的 TOML 配置路径。将 `examples/claude-code-settings.example.json` 的 `hooks` 合入目标项目的 `.claude/settings.json`，保留项目已有 hooks。仓库只提供示例，不自动安装。

配置里的路由把一个合法组合对应到一个已安装的子代理类型。例如 `claude-code:model-b:high` 对应 `worker-high`。在目标项目的 `.claude/agents/worker-high.md` 定义该代理，设置相同的型号和思考等级，并按任务需要授予工具：

```yaml
---
name: worker-high
description: Implements a delegated development task
model: model-b
effort: high
background: false
---
Implement the supplied task and report the outcome.
```

型号仍是占位符。每条路由的代理职责和工具权限须适合它可能接收的任务。只想保持原代理类型时，可以不设路由，JEV 的组合会作为建议记录。

查证日期：2026-10-01。官方 [hooks 参考](https://code.claude.com/docs/en/hooks#pretooluse-decision-control) 支持 `PreToolUse` 的 `updatedInput`，替换时须保留完整原参数。这里改 `subagent_type` 和 `model`，思考等级由代理定义的 `effort` 提供。官方 [子代理文档](https://code.claude.com/docs/en/sub-agents) 说明这些代理定义字段，未确认 Agent 工具有独立的思考等级参数，所以这里不写该参数。

选中具体技能时，适配器保留完整工具参数，在 `updatedInput.prompt` 前加「用 <技能> 技能，」。即使没有执行者路由，也可以单独更新提示。选中 `none` 时保持原提示。技能判断低把握时拒绝待执行调用并交回，不提交更新后的参数。派发事件记录 `skill_requested=null`、JEV 的 `skill_suggested` 和实际 `skill`。

高把握且有路由时应用组合，记录 `decision`、`dispatch`。无路由时保留输入，`suggested` 记录建议，`actual` 记录请求中的型号或 `inherit`，未知思考等级为 `null`。这里的 `actual` 是提交给 CLI 的配置，不是服务器实际采用型号的证明。权限、型号回退等仍由 Claude Code 处理。

低把握时保持参数，输出一句交回主会话的提示，并拒绝这一次待执行工具调用，让主会话重新决定。它不会授权工具或绕过权限。主会话的完成与失败提示分别通过 `PostToolUse`、`PostToolUseFailure` 的 `additionalContext` 返回。嵌套子代理的工具调用忽略。

### 前台完成和后台报告

参考 hooks 自动跟踪前台 Agent 调用。使用前台模式启动：

```sh
CLAUDE_CODE_FORK_SUBAGENT=0 CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 claude
```

这两个会话环境变量关闭 fork 和后台任务，详见 [环境变量](https://code.claude.com/docs/en/env-vars)。路由代理也应保持 `background: false`，派发时不请求后台执行。后台启动回执不会写 `done`。后台完成没有稳定的父工具调用编号，`SubagentStop` 的上下文也不送给主会话，因此本参考不依靠它推断父任务完成。使用后台时，在获得真实最终结果后由调用方执行明确报告入口，传事件里原始的 `run_id` 和结果文件：

```sh
python3 "$AZIR_DISPATCH_ROOT/adapters/claude_code/hook.py" \
  --config "$AZIR_DISPATCH_CONFIG" --report done \
  --run-id session-id:tool-call-id --detail-file outcome.txt
```

失败用 `--report failed`。命令输出 JSON 中包含 `wrapup` 或 `next_step` 提示，调用方须把提示交给主会话。不存在对应派发记录时报告失败。前台 `done` 表示工具完成，结果是否满足任务要求仍由主会话验收。

### 顾问事件导出

Claude Code 目前没有单独设置顾问思考等级的选项，所以顾问只能选型号、不能设思考等级。依据 [Claude Code 顾问文档](https://code.claude.com/docs/en/advisor)，查证日期：2026-10-01。

```sh
python3 adapters/claude_code/advisor_watch.py \
  --config my-config.toml --sessions-dir ~/.claude/projects/
```

命令是一次扫描，可由使用者定期运行。默认目录就是 `~/.claude/projects/`。识别顶层或 `message.content` 中的 `server_tool_use` 且 `name=advisor`，以调用 `id` 去重。去重信息直接来自目标事件文件，多个导出进程用旁边的 `.advisor.lock` 串行处理。事件保留原调用时间，额外字段 `advisor_call_id` 用于去重。缺少调用编号的记录不导出。坏行跳过，末尾未换行记录等下一次扫描。

事件文件必须位于会话目录之外。输出包含新增数和跳过数，不修改源会话。不读顾问回答。只读取事件文件的看板可使用本命令导出记录；看板如果同时直接扫描源会话，也应按原调用编号去重，或关闭看板自身扫描，避免两次计数。

## Codex

```sh
bin/azir-dispatch-codex run --config my-config.toml \
  --task-file task.md --cwd ./project
```

`--config`、`--log` 也可放在 `run` 前面。`[adapters.codex].command` 配置 Codex 可执行文件路径，或使用 `--codex-command` 覆盖。路径可含空格，命令按参数数组运行，不经过 shell。任务全文经标准输入传给 Codex。

包装器使用 `codex exec -m <型号> -c 'model_reasoning_effort="<等级>"'`，指定工作目录时追加 `-C <目录>`。以上语法于 2026-10-01 通过本机 `codex exec --help` 核对。

技能选中后在传给 Codex 的任务文字前加「用 <技能> 技能，」。`none` 保持任务文字原样。技能判断低把握时退出 4，不启动 Codex，不加前缀。JEV 不可用时采用 `development` 范围默认技能。派发事件保存 `skill_requested=null`、`skill_suggested` 和实际 `skill`，技能判断通过 `caused_by` 指向选执行者事件。

高把握采用选项。低把握退出 4，不启动 Codex。JEV 不可用时按适配器默认组合继续。Codex 输出保留在终端，失败时先对完整 stderr 应用核心过滤器及配置中的额外规则，再取过滤结果的最后 4000 字符发送给 `next_step`，事件里的报错也经过过滤。成功后报告 `done` 并打印 `wrapup` 建议。包装器返回 Codex 退出码，不能启动返回 127，输入、配置或记录错误返回 2。信号退出转换为 `128 + 信号编号`。本包装器自身的退出码 4 表示交回，但实际 Codex 子进程也可能返回 4，须结合提示与事件辨别。

可传 `--run-id` 指定本次派发编号，`--task-id` 把跨重试的同一任务串起来。默认各生成一份编号。后续判断以 `caused_by` 指向完成或失败事件，任务内派发和完成共享 `run_id`、`task_id`。

## 给自己的 agent 写适配器

参考 `adapters/codex/runner.py` 的 `run_task` 或 `adapters/claude_code/hook.py` 的 `handle_hook`：读取任务，限制当前运行工具能接收的候选，调用 `decide`，按结果运行或交回，最后记录结果并返回下一步建议。

可以复用 `adapters/common.py` 的 `load_settings`、`executor_config`、`ask`、`record` 和 `hint`。`record` 过滤写入的文字，判断调用仍由核心完成过滤。任务摘要先对完整任务应用核心过滤器及额外规则，再取前 200 字。完整任务仍原样交给执行工具。所有长度限制都在过滤之后执行。不要把内部对象或完整会话直接写入事件。测试放在命令输入输出和事件文件处，外部模型服务用回环地址的假 HTTP 服务，执行工具用临时假命令。参考三个适配器测试文件，运行 `python3 -m pytest -q -p no:cacheprovider tests`。公共测试入口在收集阶段及每个测试开始时隔离家目录、状态、日志和配置，并在会话结束时检查真实家目录的状态是否变化。

## 加一个新判断点

下面是文档示例，当前核心不实现选机器。假设以后增加 `machine` 判断点：

| 选项 | 含义 |
| --- | --- |
| `WINDOWS` | 派到使用者配置的 Windows 执行机 |
| `STUDIO` | 派到使用者配置的另一台执行机 |
| `DEFER` | 暂不派发，交回主会话 |

默认答案为 `DEFER`。先由确定性规则根据连接、软件、能力和任务权限算出合法候选，始终保留 `DEFER`。JEV 只能在这份候选列表里选，不能把不可用或未授权机器变成可用。高把握采用合法答案，低把握交回，接口不可用取 `DEFER`。例如 Windows 不可连接时，候选只能是 `STUDIO` 和 `DEFER`。

实现时扩展核心的判断点定义和合法选项验证，再让适配器调用公开入口。机器连接和实际执行应由适配器实现，判断记录仍按同一事件格式写入。新判断点需要验证非法回答不会执行。
