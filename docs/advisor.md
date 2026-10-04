# 可选 GPT Pro 顾问

顾问通过 ego lite 操作用户自己的 ChatGPT 网页账号。适合复杂问题的书面意见、审查和二次意见，答案交回当前会话，由调用者核对后决定是否采用。

截至 2026-10-04，ego lite 的[官网](https://lite.ego.app/)提供免费公开下载，无须邀请或订阅，当前提供 macOS 版（Apple Silicon 和 Intel）。[官方下载入口](https://lite.ego.app/download?auto=1)。首次引导由用户完成，会安装 ego-browser 命令和技能。Linux、Windows 和 WSL 直接跳过，顾问显示“未配置，可选”，其他角色照常工作。

## 配置

先确认用户有可使用 Pro 档的 ChatGPT 订阅，并在 ego lite 中本人登录 ChatGPT。登录、订阅和首次引导由用户操作，脚本不读写账号密码、Cookie 或登录令牌。执行步骤见 [安装引导](../skills/setup/SKILL.md#配置-gpt-pro-顾问可选)，顾问任务入口见 [公开技能](../skills/advisor-chatgpt-pro/SKILL.md)。

非交互答案字段：

```json
{"version":1,"advisor":{"provider":"chatgpt-pro","enabled":true,"weekly_limit":50}}
```

`setup --answers-json <文件> --apply` 保存对应配置和 `roles.advisor`；尚未拿到连通测试答案时仍显示未配置。拿到答案后 doctor 会实时检查浏览器登录及 Pro 档。浏览器没装、未登录或 Pro 档不可用时恢复未配置。只配置此可选步骤不代表其余六个安装步骤已完成。

```toml
[advisor]
provider = "chatgpt-pro"
enabled = true
weekly_limit = 50

[roles.advisor.preferred]
executor = "chatgpt-pro"
model = "GPT Pro"
effort = "pro"

[roles.advisor]
fallbacks = []
```

## 会发送什么

只有显式交给顾问的问题文字和选中的附件会上传到 ChatGPT；仓库不会自动上传。使用用户自己的订阅额度，不需要 OpenAI API 密钥。ChatGPT 会保留对话，具体保留和数据使用由用户的账号设置及服务条款决定。提交前说明本次材料范围，遵守用户给予的外发授权。

顾问事件沿用 `advisor` 类型，业务字段只保存调用时间、`source=chatgpt-pro` 和 `session` 会话标识，加事件系统的版本、事件标识、运行标识等公共字段；不保存问题、附件内容、用途或答案。看板沿用该事件亮起并计数。用量记录只保存时间、来源、预留标识和会话标识。结果文件按任务要求保存答案，不会自动写入状态目录。

## 每周上限

默认本地预算为 50 次，可用 `advisor.weekly_limit` 调整为任意正整数。这是本工具的预算，不是对 ChatGPT 官方套餐额度的声明；实际额度以用户账号为准。首问、追问、分批图片和连通测试各计一次。计数按滚动 7 天计算，多个进程通过文件锁共同占用额度；记录损坏时停止发送。明确点击前失败退回名额，状态不确定时保留名额并要求只读核对。

```sh
python3 skills/advisor-chatgpt-pro/scripts/pro_usage.py status --config "$AZIR_DISPATCH_CONFIG"
```

计数文件为 `$AZIR_DISPATCH_STATE_DIR/advisor-chatgpt-pro-usage.jsonl`，连通状态为同目录的 `advisor-chatgpt-pro.json`；默认目录 `~/.local/state/azir-dispatch`，新建目录 0700、文件 0600。不要为每次调用换状态目录，否则无法共享预算。用户手动在网页提问、其他工具和其他状态目录的调用无法自动统计，预算应为这些使用留出余量。

## 关闭

```json
{"version":1,"advisor":{"enabled":false}}
```

通过 setup 保存上述答案，或将配置的 `advisor.enabled` 改为 false，再运行 doctor 更新看板。后续脚本停止发送，历史计数与记录保留；不退出用户浏览器账号，也不影响其他角色。

## 防误发钩子

仅在用户授权的 Claude Code 项目内，将下面条目合并到现有 hooks（替换命令里的仓库路径），保留其他 hooks。默认安装不自动修改 hooks。

```json
{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"python3 <仓库绝对路径>/skills/advisor-chatgpt-pro/scripts/guard-hook.py"}]}]}}
```

钩子在可识别的 ChatGPT 发送或无法确定发送目标时拒绝命令，正规 pro-send.py 不受影响。它不是通用 JavaScript 沙箱；Codex 等载体不能套用 Claude Code 的 hook 协议，须依靠技能流程和统一发送入口。
