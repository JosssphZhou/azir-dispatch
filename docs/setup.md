# 安装引导的答案和结果

在仓库根目录运行 `bin/azir-dispatch setup --help` 查看参数。没有 TTY，或提供 `--answers` 时只输出 JSON；TTY 下默认逐步问答。问答与答案文件使用同一套配置、计划和提交逻辑。

答案文件使用 TOML，必须包含 `version = 1`。只填写想改变的选项，其余沿用已有值；首次使用默认离线。未知字段、重复键、无效类型和矛盾选项会被拒绝。以下各节均可省略：

```toml
version = 1

[defaults]
development = "codex:inherit:inherit"
review = "claude-code:inherit:inherit"

[executors.codex.models]
inherit = ["inherit"]

[executors.claude-code.models]
inherit = ["inherit"]

[jev]
mode = "offline" # online 需明确填写；只支持环境变量来源

[points.dispatch]
enabled = true
threshold = 0.7

[points.next_step]
enabled = true
threshold = 0.7

[points.wrapup]
enabled = true
threshold = 0.8

[points.skill]
enabled = false
threshold = 0.7

[skills]
development = []
review = []

[privacy]
home = true
name = true
email = true
strict = false
max_state_chars = 4000

[hooks]
project = "."
scope = "local" # shared 才使用项目共享设置

[dashboard]
title = "AZIR WORKFLOW"
main_title = "主会话"
main_subtitle = ""
```

执行者今天只支持 `codex` 和 `claude-code`。默认答案格式为 `执行者:型号:思考等级`，必须属于候选组合。`inherit` 是沿用执行者自身设置的占位，不会作为真实型号或思考等级传给 Codex。可把 `inherit` 改成自己的实际候选。

开发默认也作为 `[dispatch].default`。审查默认可通过 `azir-dispatch decide dispatch --scope review --config <配置> --state-file <任务> --log <记录>` 使用；不指定范围时使用开发默认。两个参考适配器继续按自己的执行者过滤候选，并使用 `development` 技能范围，不能跨执行者切换。

技能只从 `~/.claude/skills` 和 `~/.agents/skills` 读取 `SKILL.md` 的 name，不执行文件中的指示。候选用名称表示，例如 `development = ["tdd"]`；同名时用计划列出的来源路径明确选择，例如 `development = [{ name = "tdd", source = "~/.agents/skills/tdd/SKILL.md" }]`。未选择技能时关闭技能判断点。选择候选时自动启用；显式启用却没有候选会报错。离线技能答案始终是 `none`。

隐私建议来自 Git 姓名、邮箱和家目录，生成转义的字面匹配。计划显示来源和替换结果，允许逐条停用；这里不接受任意正则。手工配置仍可设置 `redact.patterns`。姓名规则不能代替秘密扫描；`strict` 仍遵循核心的过滤失败与规则要求。

`--apply` 不授予 hooks 或网络权限。答案中的 hooks 项目也不授予写入权限；加 `--allow-hooks <同一个项目>` 才写。授权路径不一致会报错。共享设置必须由答案明确选择，或在交互中单独确认。不会修改用户级 Claude Code 设置、shell 或 Git 配置。安装计划报告个人设置是否已被 Git 忽略，未忽略时应自行确认是否会被提交。

每个文件在提交前重新核对哈希。内容变了就停止，需要重跑计划。只修改安装记录能证明属于 azir 且未被用户改动的 hooks，保留其他内容和顺序。无法读取严格 JSON 时保留文件，并输出待人工合并片段。目标不属于当前用户或不可写时停止，不使用提权。

配置目录、状态目录和 hooks 项目路径先解析为真实路径。允许这些目录上级的路径别名，包括 macOS 的 `/var` 和 `/tmp`。传入的目录本身是符号链接时停止，包括已失效的链接。解析之后，拒绝目标文件及写入目录内部的符号链接，包括项目的 `.claude`、状态目录的 `locks`、`transactions` 和 `backups`。授权路径也按真实路径比较，因此同一项目的上级路径别名不会导致授权不匹配。写入和提交前的复核均使用解析后的路径。

项目设置的展示副本只显示与本次生成内容完全一致的 azir hook 条目。其他 hook 的 command 显示为 `<已有 hook，内容不显示>`；其他字符串值（含 env、matcher、任意字段和嵌套列表）均隐藏，仅保留结构，同时报告原有非 azir 条目的保留数量。这项保护不依赖用户预先配置过滤规则。展示不改变实际写入、条目指纹或私有原始备份。

候选配置在提交前完成离线判断、azir 自身 hook 的固定合成输入测试，以及示例看板回放预检。临时记录不会写到正式事件文件。配置保存、协议通过、hooks 已写入、真实会话生效、执行者账号和型号可用分别报告，不能互相代替。

仅配置 Codex 候选时，Claude Code 协议预检使用临时的继承设置，并报告 `claude_code_configured=false`；不会把该候选写入配置。这种配置不能安装 Claude Code hooks，需先添加对应候选。

一次真实 JEV 测试需要在线配置与 `--allow-online-test`，在本地提交后执行，不重试。只发固定的无意义测试文字；测试结果单独报告，不因失败回滚本地配置。接收方是 OpenRouter 及配置中的 JEV 模型；正常使用发送的字段见 [隐私说明](privacy.md)。

有变更时才建立私有状态目录、锁和备份。新目录权限 0700，配置、hooks、安装记录、恢复记录和备份权限 0600。原字节备份可能包含旧文件已有的秘密，应按敏感文件保存。setup 不取得或保存新的 JEV 密钥。

单个文件使用同目录暂存、同步和原子替换。状态目录由 `AZIR_DISPATCH_STATE_DIR` 指定，默认是 `~/.local/state/azir-dispatch`。多个文件的恢复记录保存在状态目录的 `transactions/` 下；异常中断后下次运行会报告未完成事务并停止，附恢复记录位置供人工核对。原字节备份在同一状态目录的 `backups/` 下。正常拒绝、EOF 或 Ctrl-C 在提交前不改变目标文件。旁路锁只能约束配合该锁的安装进程，不能保证编辑器在最后一次复核之后的写入不会竞争。

## 环境检测和安装进度

运行 `python3 bin/azir-dispatch doctor` 查看逐项清单，`--json` 输出相同的安装状态。检测 claude、codex、cursor-agent、gemini、grok、agy，Grok 同时查 PATH 与 `~/.grok/bin/grok`。herdr、git、python3 显示版本；无法识别版本时仅报告命令存在。三个 API 密钥变量仅检测名称是否存在，探测版本的子进程不接收这些密钥。

每次 doctor 都原子更新 `$AZIR_DISPATCH_STATE_DIR/setup-state.json`，默认目录为 `~/.local/state/azir-dispatch`。状态文件包含 version、updated_at、steps、agents、keys、roles、jev_mode、hints 和 tools。models 在保存配置后更新；board 在 demo 验证成功后更新；first_dispatch 在记录真实派发后更新。doctor 保留后续步骤的进度。配置和适配器默认从同一状态目录选择事件文件。

## 角色映射

新安装默认建立五个角色的首选及备选，格式见 [角色示例](../examples/roles.example.toml)。答案支持 `roles` 表：每项包含 `preferred = {executor, model, effort}` 与有序 `fallbacks = [{executor, model, effort}, ...]`，effort 可省略，默认 inherit。角色执行者是 CLI 命令名；也接受 claude-code，实际组合中 Claude 使用 claude-code。支持 codex、claude、cursor-agent、gemini、grok、agy；gpt-pro 是用户自己的可选 GPT Pro 接入命令，检测到该命令才表示已接入，不推断浏览器账号能力。

配置中有 roles 时，运行时按可用 CLI 重新选角色，开发与审查默认采用 dev/review，frontend 采用 main，缺项按有序备选降级。仅安装 Claude 或 Codex 时，四个必需角色都用该 CLI；无 GPT Pro 时 advisor 为 optional_unconfigured。命令存在不证明账号或型号可用。角色表优先于 dispatch.defaults；不使用角色表的旧配置继续按原默认组合执行。

setup 答案增加 `jev.mode = "rules"`，原 offline/online 仍支持。无 OpenRouter 密钥时采用配置默认答案，事件 source=rules；next_step 和 wrapup 可在对应 points 表里设置 default，答案必须属于该判断点候选。配置为 offline/rules 时不执行取密钥命令；在线 JEV 的其他故障仍以 source=default 走原降级路径。事件增加 jev_mode 字段，规则判断为 rules。

## 视频演示入口

`python3 bin/azir-dispatch demo` 回放仓库内 `azir_dispatch/demo/video-run.jsonl`，不派发任务、不读取真实顾问会话。`--check` 做非交互逐帧验证，`--once` 打印单帧，支持 --config、--speed、--seconds。演示素材缺失时仅输出“演示事件文件缺失”并退出 1；演示素材随仓库提供。

## 手工配置和接入 JEV

以下命令都在克隆后的仓库根目录运行。先复制 [配置示例](../examples/config.example.toml)，用公开的示例任务做一次无密钥判断：

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
