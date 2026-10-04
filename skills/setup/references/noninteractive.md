# 非交互看图和真实派发

完成主引导第 1 至 4 步后执行本分支。用户授权安装并跑一次派发时，可以创建独立 herdr 会话并启动中性样例任务。普通命令工具可以完成，不需要桌面控制或让用户手动附着。

## 创建自己的 herdr 会话

macOS 的 Unix socket 路径有长度限制。临时 HOME 使用短路径，例如 `mktemp -d /tmp/azir.XXXXXX`；herdr 配置用当前 HOME 下的短目录。已有配置环境变量指向其他会话时，本分支改为自己新建的目录。

生成唯一英文会话名，保存为 `SESSION`。从调用方 HOME 设置配置路径，所有 herdr 控制命令都显式带同一个 `--session "$SESSION"`。保留 CLI 认证需要的现有配置目录环境变量，不复制凭据。

```sh
export HERDR_CONFIG_PATH="$HOME/h/config.toml"
SESSION="azir-setup-$(date +%s)-$$"
herdr --session "$SESSION" server > "$HOME/herdr-setup-server.log" 2>&1 &
SERVER_PID=$!
herdr --session "$SESSION" workspace
```

等待服务就绪后，使用 `workspace create --cwd "$PWD" --label "安装验收" --no-focus` 新建工作区。从响应 JSON 的 `.result.root_pane.pane_id` 读取根窗格，赋给 `ROOT_PANE`。不要使用 `--current`，非交互调用方没有 pane 上下文。所有新窗格使用返回的标识，并加中文标签。

成功条件：服务位于当前 HOME 的配置目录，workspace 创建成功。socket 未就绪时稍后重试。若报告 `sun_path` 长度超限，缩短本次配置目录和会话名后重试；不停止已有服务。新 shell 如加载了系统登录配置而扩展 PATH，使用不加载启动文件的 shell，并显式传递调用方 PATH，防止把另一个账号的 CLI 当作本次环境。

## 留下调度图画面

`demo --check` 只是预检。还要真实运行动画并保存终端画面。用本技能附带的标准库脚本分配 PTY、记录画面并在指定时长后退出：

```sh
python3 skills/setup/scripts/record-demo.py --output "$HOME/install-demo.typescript" --duration 3
```

成功条件：脚本退出 0，记录文件含真实 ANSI 动画。脚本只启动 demo，不启动模型，不修改终端或系统配置。命令工具可以没有 TTY，脚本自己分配 PTY。保留记录文件，并报告路径。

用 `herdr --session "$SESSION" pane run "$ROOT_PANE" "python3 bin/azir-dispatch demo"` 在独立会话中运行同一回放，再用 pane read 读取画面，最后用 `pane send-keys "$ROOT_PANE" q` 退出。`--seconds` 只控制 --check 时长或 --once 的取帧时间，不会停止交互动画。也可运行真实 dashboard，传入本次事件路径及 `--no-advisor-scan`。成功条件：录像有标题、主会话、分流层和执行者画面，不能以 --check 的文字代替。

## 在独立窗格派发真实执行者

用主引导第 6 步的 `decide dispatch` 得到选中组合。派执行者不传 --scope，该参数只用于 `decide skill`。

用 `pane split "$ROOT_PANE" --direction down --cwd "$PWD" --no-focus` 创建执行窗格，读取返回 pane_id，标为「首次派发」。为该轮生成唯一 RUN_ID。按主引导写真实 `record dispatch`，再在执行窗格运行所选 CLI 的非交互命令。先读取该 CLI 的 --help 确认本机参数：

- Codex：`codex exec --skip-git-repo-check -m "$MODEL" -c "model_reasoning_effort=\"$EFFORT\"" '只计算 17 加 25，回复结果，不修改文件。'`。inherit 字段省略对应参数。保留 CODEX_HOME。
- Grok：`grok --always-approve --no-subagents --leader-socket "$HOME/azir-worker-leader.sock" -p '只计算 17 加 25，回复结果，不修改文件。'`。独立 socket 避免复用其他会话的环境。
- AGy：`agy --print '只计算 17 加 25，回复结果，不修改文件。'`。只使用已有登录或调用方认证环境变量。
- Claude Code：`claude -p '只计算 17 加 25，回复结果，不修改文件。'`。

把真实 stdout 和 stderr 保存到 `$HOME/install-worker.log`，将真实退出码写到独立结果文件。用 pane run 提交完整命令，再轮询该文件，最多等待 120 秒。确认退出码 0 且真实回答为 42 后，写 `record done`。真实 CLI 失败时写 `record failed`，读取日志修复登录、型号或 PATH；不要用回显脚本替代。日志缺失或进程还在运行时不能写 done。

运行 doctor 核对 first_dispatch=ok，并检查同一个 RUN_ID 的 dispatch 与 done。向用户报告实际 CLI、组合、任务结果、事件和画面记录路径。

## 清理本次会话

先保存本次事件、demo 终端记录和执行者输出，再运行 `herdr --session "$SESSION" session stop "$SESSION" --json`。核对本次服务和执行者进程已退出。保留安装和用户配置；不关闭其他会话，不停止默认服务。
