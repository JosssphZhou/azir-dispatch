# azir 调度

azir-dispatch 是多 agent 调度工具：选择执行者、记录派发结果，并在终端里显示调度图。

把这句话交给你的 Claude Code、Codex、Grok 或 AGy：

> 照这个仓库的 `skills/setup/SKILL.md` 引导装好并跑一次派发：https://github.com/JosssphZhou/azir-dispatch

这条引导已经让 Codex 和 Grok 在全新的环境里从零装好并跑通一次派发。AGy 还没验证。

![调度演示](docs/dispatch-demo.gif)

视频录制时开发执行者还是 Cursor，现在默认是原生 Codex GPT-6.1 Sol。

## 先看调度图

进入仓库后运行，无须任何密钥：

```sh
python3 bin/azir-dispatch demo
```

`azir-dispatch demo` 回放视频中的一整轮事件。按 `q` 退出。装好后同一张调度图可以读取真实派发记录。

## 三步装好并跑起来

1. 获取仓库，让自己的 agent 读取 [安装引导](skills/setup/SKILL.md) 并运行 `python3 bin/azir-dispatch doctor`。需要 Python 3.11+。
2. 按引导安装 herdr、保存角色配置并打开调度图。herdr 必装，因为调度图和真实执行者需要在它的终端窗格里运行。
3. 按引导派发中性样例任务，确认真实执行者返回结果，事件记录包含 `dispatch` 和 `done`。

模型由你自己配置。主会话、开发、审查、调研和顾问五个角色按可用能力降级；只有一个 CLI 时，四个必需角色共用它。没有顾问时显示「未配置，可选」。没有 OpenRouter 密钥时采用规则模式，仍可看图和派发。

手工安装、非交互答案及配置字段见 [安装说明](INSTALL.md) 和 [配置说明](docs/setup.md)。命令未加入 PATH 时，在仓库根目录使用 `python3 bin/azir-dispatch`。

项目以 [MIT 许可证](LICENSE) 开源。

## JEV 处理哪些判断

包含四个判断点：

1. 派发任务时，选择执行者、模型和思考等级。选项来自使用者的配置。
2. 执行失败时，建议继续、换做法，还是停下交回主会话。
3. 执行完成时，建议是否关闭窗格、清理工作区。
4. 派发任务时，选技能。候选和默认答案按范围配置，`none` 表示不指定技能。

派发判断的把握达到设定值时，适配器采用合法答案，低于设定值时交回发起派发的那一层。默认设定值为 0.7，每个判断点可以单独调整。失败和收尾的答案交给主会话处理，参考适配器不自动重试、关闭窗格或清理工作区。Claude Code 还需要配置子代理路由，才能应用对应的派发组合。

遇到超时、缺少密钥或接口出错，采用该判断点的默认答案。核心默认给过滤、取密钥、HTTP 请求和判断记录共用 3 秒预算。这个预算不包含执行者完成任务的时间，也不是对进程启动和系统文件操作耗时的硬保证。

判断记录写不进去时，这次判断不应用，交回发起派发的那一层。

选技能与选执行者分别判断。核心通过 `decide skill --scope <范围>` 接收范围。两个参考适配器使用 `development` 范围，选中技能后在任务文字前加「用 <技能> 技能，」。选中 `none` 时保留任务文字，拿不准时交回发起派发的会话。配置示例见 [核心配置](examples/config.example.toml)。

## 看板显示判断记录

演示动画里的事件是写死的。看板沿用同一个画面，但每个事件都从判断记录读取：派发、完成、失败和交回。连线上的小球对应这些事件。接上适配器后，看板显示真实派发。下方快速开始使用示例记录回放。

执行者的数量随派发记录变化。空间不足时，看板显示最近三个执行者和其余数量。每个判断显示判断点、选中的答案、把握和执行去向，并标明这次判断采用 JEV 答案、交回主会话，还是因 JEV 不可用而采用默认答案。

顾问栏在读取到顾问调用记录后亮起，默认持续 20 秒再变暗，显示今日调用次数。第一版识别 Claude Code 自带的顾问调用。看板只读取调用信息，不读取回答内容，所以动画里顾问栏的两行意见在看板上没有。启动时读到的历史调用只重建次数和最近调用时间，不亮起。

## herdr 办公室

运行 `node office/office.mjs` 或 `bin/azir-dispatch office`，在 Herdr 工位上方显示工程经理、JEV 和顾问状态，并读取 azir 判断记录。默认只读。传入 `--enable-answers` 后启用原版批准和拒绝按键。传入 `--enable-actions` 后启用派发、雇用、交换工位、通知和标题修改。

## 接入已有的 agent

项目提供架构说明、通用判断核心、看板，以及 Claude Code 和 Codex 两个参考适配器。核心不绑定具体 agent，也不依赖作者的运行环境。

接入其他 agent 时，适配器需要实现两件事：派一个任务，报告任务完成或失败。执行者、可用模型和思考等级由使用者配置。

azir 调度是 azir 的一部分，以 MIT 协议开源。

## 手工配置和接入 JEV

以下命令都在克隆后的仓库根目录运行。先复制 [配置示例](examples/config.example.toml)，用公开的示例任务做一次无密钥判断：

```sh
cp examples/config.example.toml my-config.toml
printf '%s\n' 'Review this public example task.' > task.md
OPENROUTER_API_KEY= bin/azir-dispatch decide dispatch \
  --config my-config.toml --state-file task.md --log events.jsonl
```

命令输出一行 JSON：`answer="codex:gpt-6.1-sol:high"`、`source="rules"`、`disposition="apply"`、`error="missing_api_key"`，并在当前目录的 `events.jsonl` 写入判断记录。这一步只判断，不启动 Codex。示例没有启用 `[jev].key_command`，密钥为空时不发 HTTP 请求；如果自己的配置启用了取密钥命令，需先停用它才能做同样的离线核对。

实际接 JEV 时，通过自己的运行环境提供 `OPENROUTER_API_KEY`，去掉命令开头的 `OPENROUTER_API_KEY=` 后再运行 `decide`。不要把密钥写进配置文件或提交到 Git。核心配置的 `[jev]`、`[dispatch]`、`[points]` 和可选的 `[redact]` 分别设置服务、候选组合、判断依据与把握线、过滤规则。示例按难度分三档候选：复杂任务用 `gpt-6.1-sol`，简单任务用便宜的 `gpt-6-luna`，规划类用 `claude-opus-5-5`。真实派发前，把候选、默认答案和路由中的型号一起改成你安装的 CLI 支持的型号及思考等级。

配置同时包含 `[dashboard]`，控制标题、记录路径、执行者保留时间和顾问高亮时间。读取刚才的判断记录：

```sh
python3 -m azir_dispatch.dashboard --config my-config.toml \
  --log events.jsonl --no-advisor-scan
```

按 `q` 退出。这里显式指定本地记录并关闭顾问扫描。实时模式还支持 `AZIR_DISPATCH_LOG` 和 `[dashboard].log`；不指定路径时默认读 `~/.local/state/azir-dispatch/events.jsonl`。默认顾问目录为 `~/.claude/projects/`，示例配置将它改为 `sessions`。

## 手工安装参考适配器

以下步骤在仓库内新建 `demo-project`，核对接入过程。`mkdir demo-project` 如果提示目录已存在，请换一个新目录名，避免覆盖已有项目。两个适配器仍只需 Python 标准库。

```sh
cp examples/adapters.example.toml my-adapters.toml
export AZIR_DISPATCH_ROOT="$PWD"
export AZIR_DISPATCH_CONFIG="$PWD/my-adapters.toml"
export AZIR_DISPATCH_LOG="$PWD/events.jsonl"
mkdir demo-project
```

### Claude Code

在新项目中安装 hooks 和与示例路由对应的子代理：

```sh
mkdir -p demo-project/.claude/agents
cp examples/claude-code-settings.example.json demo-project/.claude/settings.json
cat > demo-project/.claude/agents/worker-high.md <<'AGENT'
---
name: worker-high
description: Implements a delegated development task
model: model-b
effort: high
background: false
---
Implement the supplied task and report the outcome.
AGENT
```

已有项目须将示例的 `hooks` 合入 `.claude/settings.json`，保留已有 hooks。真实使用前，同时替换配置及代理定义中的占位型号，并按任务需要授予代理工具权限。上面的三个环境变量须保留在启动 Claude Code 的 shell 中。

直接给 hook 送一个示例工具调用，核对默认路由；这一步不启动 Claude Code 或子代理：

```sh
printf '%s\n' '{"hook_event_name":"PreToolUse","session_id":"demo","tool_use_id":"demo-call","tool_name":"Agent","tool_input":{"subagent_type":"general-purpose","prompt":"Review this public example task."}}' | \
  OPENROUTER_API_KEY= python3 "$AZIR_DISPATCH_ROOT/adapters/claude_code/hook.py" \
    --config "$AZIR_DISPATCH_CONFIG"
```

输出的 `hookSpecificOutput.updatedInput` 应包含 `subagent_type="worker-high"` 和 `model="model-b"`；记录中派发判断为 `source=rules`。完成型号配置并安装、登录 Claude Code 后，在 `demo-project` 中用 `CLAUDE_CODE_FORK_SUBAGENT=0 CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 claude` 启动前台会话。完成或失败的 hooks 将后续建议交给主会话。

### Codex

先用一个只回显任务的临时命令核对包装器，无须安装 Codex，也不会请求模型：

```sh
cat > demo-project/offline-codex <<'PY'
#!/usr/bin/env python3
import sys
print("offline task received: " + sys.stdin.read().strip())
PY
chmod +x demo-project/offline-codex
OPENROUTER_API_KEY= bin/azir-dispatch-codex run --config my-adapters.toml \
  --task-file task.md --cwd demo-project --log events.jsonl \
  --codex-command "$PWD/demo-project/offline-codex"
```

终端应回显任务并给出 `wrapup: keep (source=rules)` 建议，记录中包含派发和完成事件。这验证包装器的接入过程。真实使用时，先安装、登录 Codex 并替换占位型号，然后省略 `--codex-command`，让包装器运行 `[adapters.codex].command` 指定的 Codex。

适配器采用合法的默认派发答案时也会启动执行工具，因此仅清空 JEV 密钥不会阻止真实 Codex 执行。以上离线核对同时使用假命令。完整接入、后台结果报告和顾问记录导出见 [参考适配器](docs/adapters.md)。

## 在 herdr 里打开

用 [herdr](https://herdr.dev) 的话，可以把本仓库当作 herdr 插件安装，然后从 herdr 的插件窗格里打开看板（`board`）、JEV 判断视图（`jev`）和办公室（`office`，需要 Node 18 或更新版本）：

```sh
herdr plugin install JosssphZhou/azir-dispatch
```

插件读取 `~/.config/azir-dispatch/config.toml`（安装引导写的位置），也可以用环境变量 `AZIR_DISPATCH_CONFIG` 指定。

## 文档目录

- [核心接口与判断记录格式](docs/architecture.md)：判断点、默认答案、来源和记录字段。
- [终端看板](docs/dashboard.md)：回放、实时模式、画面和顾问调用的读取方式。
- [参考适配器](docs/adapters.md)：Claude Code、Codex，以及给其他 agent 写适配器的方法。

## 隐私：发什么、去掉什么

调用核心的 `decide` 时，现状文字会发送到 OpenRouter，由配置的 JEV 模型处理。核心不会主动读取任务书、报错文件或完整会话。Claude Code 适配器提供任务提示或完成、失败信息。Codex 适配器在派发时提供任务文字，后续判断还包含退出码、最近 stderr 和任务文字。过滤后默认只发送前 4000 字符，因此全文不一定发送。

内置过滤替换常见 API 密钥前缀、Bearer 令牌、`password=`、`token=`、`secret=` 的值、PEM 私钥块和 JWT。过滤不是对所有敏感信息的识别保证。姓名、地址、项目资料和未识别的凭据仍可能发送。

在你自己的核心配置中增加正则表达式，处理环境中需要隐藏的其他内容。例如：

```toml
[redact]
strict = true
max_state_chars = 4000
patterns = ['internal-project-[A-Za-z0-9_-]+', 'customer-id:[0-9]+']
```

额外规则的匹配内容会替换成 `[REDACTED]`。`strict = true` 要求提供额外规则，过滤失败或过滤后为空时跳过 HTTP，返回默认答案。规则须由你按实际数据调整。不要发送无法可靠过滤的敏感内容。

核心判断记录保存在本机，保存过滤后现状的前 200 字符和判断结果。适配器还会写入派发和完成、失败的信息。看板只读这些文件，实时模式可另读顾问调用的编号、时间、会话和目录，不显示会话正文或顾问回答。请自行管理本机记录的访问权限和保留时间。

## 发布前扫描

安装 Python 3.11+ 和 `gitleaks` 后，在仓库根目录运行：

```sh
python3 scripts/privacy-scan.py .
```

脚本检查当前已跟踪文件和所有本地引用可达提交中的文件、提交说明、作者与提交者信息。支持 UTF-8 和带 BOM 的 UTF-16、UTF-32 文本。不能可靠解码的内容逐项列为「未检查」，有未检查项或命中时退出 1。仓库或工具错误导致扫描无法完成时退出 2，无命中且无未检查项时退出 0。额外调用 `gitleaks git --redact` 和 `gitleaks dir --redact` 检查密钥。输出只列位置和规则，不打印疑似凭据。历史中的问题需要维护者在发布前处理，扫描脚本不修改文件或历史。

每个判断点可用 `[points.<判断点>].question` 写中文问题，用 `instructions` 写团队规则和当前判断背景，用 `[points.<判断点>.criteria]` 为选项写选择条件。参见 [配置示例](examples/config.example.toml) 和 [配置参考](docs/architecture.md)。说明表覆盖描述，候选仍由执行者或技能配置决定。派执行者不传 `--scope`；选技能才使用 `decide skill --scope development`，并配置对应范围的候选。

## 相关项目和致谢

[herdr-jev](https://github.com/flaviomartil/herdr-jev) 也用 JEV 给多 agent 的任务分流和选模型，是 herdr 终端复用器的插件，带一个按 agent 显示状态并能批准操作的终端界面。两者的区别：azir 调度的核心不绑定 herdr 或任何一个 agent，判断点和判断记录格式是通用的，自带 Claude Code、Codex 两个参考适配器和读判断记录的看板；herdr-jev 直接接在 herdr 里工作。

[herdr-office](https://github.com/michaellandi/herdr-office) 提供 agent 工位可视化。`office/` 保留上游 MIT 许可，并在此基础上展示 azir-dispatch 的 JEV 判断、派发记录和顾问调用。
