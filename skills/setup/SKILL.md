---
name: azir-dispatch-setup
description: 安装、重新配置 azir-dispatch，或用户要求照仓库引导装好并跑一次派发时使用。适用于 Codex、Grok、AGy 和其他能执行普通 shell 命令的 agent。
---

先使用调用方提供的 HOME、PATH 和配置目录。普通 shell 不应加载其他账号的启动文件；每次启动新 shell 后核对 HOME 和可用 CLI。状态和配置写入该 HOME，沿用的登录目录只用于认证。

在仓库根目录按下面六步执行，每步检查成功条件后继续。Windows 使用 WSL2 Linux 环境；Python 需 3.11+。所有操作使用普通 shell 命令，不依赖 agent 专有工具或斜杠命令。

用户已授权按默认值安装时，展示角色选择和降级情况后继续本地安装。需要用户选择型号时只确认一次，再保存。已有配置先查看计划并保留自定义内容。账号、密钥和在线请求按最后一节处理。

## 1. 检测环境

尚未获取仓库时，将用户提供的仓库地址克隆到指定目录；未指定则用 `~/azir-dispatch`。存在已有仓库时直接使用，已有其他内容则换空目录。默认地址见 [README](../../README.md)。后面的命令在仓库根目录执行：

```sh
python3 --version
python3 bin/azir-dispatch doctor
python3 bin/azir-dispatch doctor --json
```

成功条件：doctor 退出 0，清单包含 claude、codex、cursor-agent、gemini、grok、agy、herdr、git、python3 和三个密钥变量。每项显示勾或叉，缺项旁有修复说明；JSON 的 `agents`、`keys` 为布尔值。状态写到 `$AZIR_DISPATCH_STATE_DIR/setup-state.json`，默认 `~/.local/state/azir-dispatch/setup-state.json`。

没有 Python 3.11+ 时先检查 `python3.11`、`python3.12` 等入口，找到后替换本引导里的 `python3`；都没有则用系统包管理器安装。没有可用 agent 时先装一个再重跑 doctor。Grok 同时检查 PATH 和 `~/.grok/bin/grok`，后者需加入当前 shell 的 PATH：

```sh
export PATH="$HOME/.grok/bin:$PATH"
```

命令存在只证明安装，账号和型号在第 6 步实际验证。缺密钥可以继续规则模式安装。

## 2. 安装 herdr

herdr 必装，因为调度图和所有 agent 窗格都跑在 herdr 里。已安装时直接验证；未安装时按 [官方安装说明](https://herdr.dev/agent-guide.md) 在 macOS、Linux 或 WSL 中执行：

```sh
curl -fsSL https://herdr.dev/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
herdr --version
python3 bin/azir-dispatch doctor
```

成功条件：版本命令成功、清单 herdr 行打勾，`steps.herdr=ok`。失败时检查下载连通性和 PATH，按官方说明选择包管理器或手动安装。

交互会话已在 herdr 内时继续第 3 步。交互会话尚未进入时，让用户运行 `herdr` 后继续。非交互 agent 必须完成全部安装和真实派发，不能停在“请用户进入终端”：第 3、4 步完成后按 [非交互看图和真实派发](references/noninteractive.md) 执行第 5、6 步。该分支创建自己的命名会话，不附着或修改已有会话。

## 3. 安装本仓库

克隆后已经可用，无须 pip 安装。当前 shell 使用本仓库命令及事件目录：

```sh
export PATH="$PWD/bin:$PATH"
export AZIR_DISPATCH_STATE_DIR="${AZIR_DISPATCH_STATE_DIR:-$HOME/.local/state/azir-dispatch}"
python3 bin/azir-dispatch --help
python3 bin/azir-dispatch doctor --json
```

成功条件：帮助列出 setup、doctor、demo、decide、record，`steps.repo=ok`。命令找不到时使用 `python3 bin/azir-dispatch`。需要长期保留环境变量时按用户的 shell 配置习惯保存，保留已有启动文件内容。

## 4. 选择角色和型号

读取 doctor JSON 的 `roles`，展示 main 主会话、dev 开发、review 审查、research 调研、advisor 顾问的执行者、型号和降级情况。默认开发和审查首选 Codex GPT-6.1 Sol，主会话和前端首选 Claude Opus 5.5，顾问首选 GPT Pro。只有一个 CLI 时四个必需角色都退到它，Grok 和 AGy 默认沿用自身型号 `inherit`。缺 GPT Pro 时顾问为 `optional_unconfigured`，不阻止安装。

角色格式见 [角色示例](../../examples/roles.example.toml)，可以通过答案中的 `roles` 覆盖；完整格式见 [配置说明](../../docs/setup.md)。用户已授权使用默认值时，用下面的非交互命令查看计划、保存，再验证映射：

```sh
printf '%s\n' '{"version":1,"jev":{"mode":"rules"}}' | python3 bin/azir-dispatch setup --answers-json -
printf '%s\n' '{"version":1,"jev":{"mode":"rules"}}' | python3 bin/azir-dispatch setup --answers-json - --apply
export AZIR_DISPATCH_CONFIG="$HOME/.config/azir-dispatch/config.toml"
python3 bin/azir-dispatch doctor --config "$AZIR_DISPATCH_CONFIG" --json
```

已有配置保留原候选和规则；重配置角色时把新 `roles` 明确写进答案。保存的角色表优先于 `dispatch.defaults`。型号不可用时将首选 model 和 effort 都改为 `inherit`，或填写用户已有型号，重新生成计划并保存。角色表的 `claude` 对应派发组合的适配器名 `claude-code`。

成功条件：`status=applied`、离线验证通过、`steps.models=ok`、角色映射符合用户选择。无 CLI 时 `steps.models=missing`，先安装登录一个 CLI，再执行。

## 5. 打开调度图

先用无密钥的视频事件回放做非交互预检：

```sh
python3 bin/azir-dispatch demo --check
```

成功条件：退出 0，输出“检查通过”，`steps.board=ok`。若输出“演示事件文件缺失”，检查 `azir_dispatch/demo/video-run.jsonl`，更新到包含演示素材的版本再继续，其他样例不能替代该回放。

在 herdr 内运行 `herdr pane` 核对本机语法，用普通 shell 打开一个看板窗格：

```sh
herdr pane split --current --direction right --cwd "$PWD" --no-focus
```

从返回 JSON 的 `.result.pane.pane_id` 读取新窗格标识，赋给 `BOARD_PANE`。运行回放：

```sh
herdr pane run "$BOARD_PANE" "python3 bin/azir-dispatch demo"
```

用户应看到视频事件逐步播放。终端较窄时将 split 的 direction 改为 down。看完后向该窗格发送 q，再在同一窗格运行真实看板，安装反馈中的 herdr 项应亮起：

```sh
herdr pane send-keys "$BOARD_PANE" q
herdr pane run "$BOARD_PANE" "python3 -m azir_dispatch.dashboard --config \"$AZIR_DISPATCH_CONFIG\""
```

画面异常时检查终端尺寸和真彩色支持，看板建议至少 90×46，预检最小 80×36。报告时分别写清预检通过和用户实际看图，前者不证明后者。

## 6. 跑第一次真实派发

decide 只选择执行者，必须随后启动真实 agent、提交任务并记录结果，才算完成：

```sh
printf '%s\n' '只计算 17 加 25，回复结果，不修改文件。' | python3 bin/azir-dispatch decide dispatch --state-file - --config "$AZIR_DISPATCH_CONFIG"
```

读取 JSON，`disposition=apply` 且 answer 非空时按 `executor:model:effort` 提取组合；`source=rules` 允许继续。handback 或 answer 为空时停下本次派发并报告原因。

运行 `herdr agent` 确认本机支持的 kinds。用 `herdr pane split --current --direction down --cwd "$PWD" --no-focus` 新建执行窗格，从 JSON 读取 pane_id，赋给 `WORKER_PANE`。生成唯一英文 agent 名 `WORKER` 和唯一 `RUN_ID`，启动：

```sh
herdr agent start "$WORKER" --kind "$KIND" --pane "$WORKER_PANE"
```

KIND 映射为 codex→codex、claude-code→claude、cursor-agent→cursor、gemini→gemini、grok→grok、agy→agy。型号为 inherit 时不传型号参数；Codex 明确型号在命令末尾加 `-- -m "$MODEL" -c "model_reasoning_effort=\"$EFFORT\""`（effort 为 inherit 时省略 -c），Claude 明确型号加 `-- --model "$MODEL"`。其他 CLI 的自定义型号先检查其 `--help`。启动失败先修复登录、PATH 或型号，再重试。

将选中的组合编码为 JSON 对象 `COMBINATION`，仅包含 executor、model、effort，例如 `{"executor":"grok","model":"inherit","effort":"inherit"}`。启动成功后记录并提交任务：

```sh
python3 bin/azir-dispatch record dispatch --run-id "$RUN_ID" --config "$AZIR_DISPATCH_CONFIG" --field "requested=$COMBINATION" --field suggested=null --field "actual=$COMBINATION" --field 'task=首次安装样例：计算 17 加 25' --field "target=$WORKER"
herdr agent prompt "$WORKER" '只计算 17 加 25，回复结果，不修改文件。' --wait --timeout 120000
herdr agent get "$WORKER"
herdr agent read "$WORKER" --source recent-unwrapped --lines 80
```

成功条件：真实 agent 回答 42、状态为 idle 或 done、真实看板显示派发连线和执行者。确认后记录完成并检查状态：

```sh
python3 bin/azir-dispatch record done --run-id "$RUN_ID" --config "$AZIR_DISPATCH_CONFIG" --field "target=$WORKER" --field 'detail=首次安装样例完成，结果 42'
python3 bin/azir-dispatch doctor --config "$AZIR_DISPATCH_CONFIG" --json
```

阻塞或失败时用 `record failed` 的同样字段记录实际原因，报告未完成，不能用假 CLI 或样例事件冒充真实派发。最后报告配置路径、选中角色、规则或 JEV 模式、看图及真实派发结果、仍缺少的可选项。

## 必须由用户完成的步骤

- CLI 账号登录、浏览器确认和订阅开通：由用户在对应 CLI 提示中完成，然后重试。
- 密钥准备：仅检查环境变量名是否存在。用户在自己的 shell 设置密钥并重启需要它的 agent，值不进入对话、答案文件、配置、日志或报告。
- 可选在线 JEV：先说明过滤后的任务会发往 OpenRouter；已有该动作授权后使用 `{"version":1,"jev":{"mode":"online"}}` 作为 setup 答案。没有 OPENROUTER_API_KEY 时自动采用规则答案。在线测试另需明确授权，使用 `--allow-online-test`。
- Claude Code 项目 hooks：默认跳过；需要时按 [配置说明](../../docs/setup.md) 指定单一项目，已有写入授权后使用 `--allow-hooks <同一项目>`。

setup 支持答案文件和标准输入，不等待终端问答。已有登录可沿用；账号确实未登录时才交给用户。终端附着、登录与可选密钥配置之外，其余步骤由任意能执行 shell 的 agent 完成。非交互分支也要交付调度图的真实终端记录和真实执行结果。

## 配置 GPT Pro 顾问（可选）

先确认用户有没有可使用 Pro 档的 ChatGPT 订阅。没有就跳过，顾问显示“未配置，可选”。Linux、Windows 和 WSL 也直接跳过；当前 ego lite 仅支持 macOS，不影响前面六步。

macOS 用户有订阅时，打开 [ego lite 官网](https://lite.ego.app/) 的[免费下载](https://lite.ego.app/download?auto=1)。已有应用时直接使用，用户在应用中本人完成首次引导并登录 `https://chatgpt.com/`。引导会注册 ego-browser 命令（通常在 `~/.local/bin`）及技能；必要时将该目录加入 PATH。不读取账号密码、Cookie 或登录令牌。登录和系统权限提示由用户处理，完成前暂停相关步骤。

用户已授权配置顾问时，使用非交互答案字段保存可选配置。在线提问前说明连通测试会向 ChatGPT 发送一句短问题并消耗一次订阅额度；已有测试授权时直接继续，否则取得该动作授权后再发。

```sh
printf '%s\n' '{"version":1,"advisor":{"provider":"chatgpt-pro","enabled":true,"weekly_limit":50}}' | python3 bin/azir-dispatch setup --answers-json - --apply
export AZIR_DISPATCH_CONFIG="$HOME/.config/azir-dispatch/config.toml"
command -v ego-browser
python3 bin/azir-dispatch advisor open --config "$AZIR_DISPATCH_CONFIG"
```

保存输出 `space` 为 `SPACE_ID`。按 [GPT Pro 顾问技能](../advisor-chatgpt-pro/SKILL.md) 核对登录和选择 Pro 档，再执行：

```sh
python3 bin/azir-dispatch advisor check --space "$SPACE_ID" --config "$AZIR_DISPATCH_CONFIG"
PROMPT_FILE=$(mktemp)
printf '%s\n' '用一句话回答：1+1 等于几。直接给出答案。' > "$PROMPT_FILE"
python3 skills/advisor-chatgpt-pro/scripts/pro-send.py --space "$SPACE_ID" --config "$AZIR_DISPATCH_CONFIG" --prompt-file "$PROMPT_FILE"
python3 skills/advisor-chatgpt-pro/scripts/pro-wait.py --space "$SPACE_ID" --config "$AZIR_DISPATCH_CONFIG"
python3 bin/azir-dispatch doctor --config "$AZIR_DISPATCH_CONFIG" --json
python3 skills/advisor-chatgpt-pro/scripts/pro_usage.py status --config "$AZIR_DISPATCH_CONFIG"
```

成功条件：拿到实际答案 2、顾问角色 `status=ok`、用量增加一次、事件包含 `type=advisor` 与 `source=chatgpt-pro`，真实看板顾问栏亮起并计数。完成后按顾问技能关闭本次任务空间，处理本次临时提示词文件。缺浏览器、未登录或测试失败时报告“未配置，可选”并继续其他安装步骤；发送状态不确定时先只读核对，不能自动重发。

非交互模式使用上述 `advisor` 答案字段，`enabled=false` 表示跳过。保存配置本身不触发网页外发，agent 需在获授权后显式执行连通测试。每周上限、数据范围及关闭方法见 [顾问说明](../../docs/advisor.md)。
