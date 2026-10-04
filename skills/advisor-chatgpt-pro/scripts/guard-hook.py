#!/usr/bin/env python3
"""Claude Code PreToolUse 钩子：拦下绕过用量计数、直接在 ego-browser 脚本里让 ChatGPT 发送的命令。

只看含 `ego-browser` 的 Bash 命令；脚本从文件读入（`< 文件`、`-f 文件`）时把文件内容一起判断，读不到就只看命令文字。
1. 没有发送动作（点发送按钮、按回车、提交表单、向接口 POST）：放行，不查空间，普通命令不变慢。
2. 有发送动作，且命令里有 ChatGPT 特征词：拦。
3. 其余发送动作查数字任务空间当前页面，只有全部不含 ChatGPT 时放行。
4. 查询命令用到的数字任务空间（`taskSpace(11)`）当前打开的页面：有 chatgpt.com 就拦；
   查询失败、超时，或看不出用的是哪个空间，也拦，并说明怎么让命令通过。
正规发送走 pro-send.py，命令里不含 `ego-browser`，照常放行。ChatGPT 改版时只改下面几组规则。
"""
import json
import os
import re
import subprocess
import sys

CHATGPT_CONTEXT = [r"(?i)chatgpt", r"prompt-textarea", r"ProseMirror", r"data-composer", r"backend-api"]
SEND_ACTIONS = [
    r"send-button",                         # 旧版发送按钮
    r"type\s*=\s*[\"'\\]*submit",          # form button[type="submit"]
    r"发送",                                # aria-label="发送"、text=发送
    r"\bSend\b",                            # 英文界面的 Send prompt
    r"[\"'`](?:\w+\+)*Enter[\"'`]",         # press('Enter')、press("ControlOrMeta+Enter")
    r"requestSubmit|\.submit\(",            # 直接提交表单
    r"[\"'`]POST[\"'`]",                    # 直接向对话接口发请求
]
SPACE = re.compile(r"taskSpace\(\s*(\d+)\s*\)")
SCRIPT_FILE = re.compile(r"(?:(?<![<0-9])<(?!<)|(?:^|\s)-f)\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s;|&<>]+))")
QUERY_TIMEOUT = 3

QUERY_JS = """
const out = {}
for (const id of %s) {
  try { out[id] = (await (await taskSpace(id)).tabs()).map(t => t.url) } catch (e) { out[id] = null }
}
console.log('SPACE_URLS ' + JSON.stringify(out))
"""

REASON_SEND = ("ChatGPT Pro 发送必须走 skills/advisor-chatgpt-pro/scripts/pro-send.py（先按配置计数再发），"
               "不要在 ego-browser 脚本里点发送按钮、按回车或直接调用接口发送。等回答用 pro-wait.py。")
REASON_UNKNOWN = ("这条 ego-browser 命令带发送动作，但查不到它操作的是不是 ChatGPT 页面（{why}）。"
                  "要发 ChatGPT 请走 pro-send.py；其他网站的操作使用可查询的数字任务空间，再重新核对目标。")


def script_text(cmd):
    """命令文字，加上它从文件读入的脚本内容。"""
    parts = [cmd]
    for m in SCRIPT_FILE.finditer(cmd):
        path = os.path.expanduser(next(g for g in m.groups() if g))
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                parts.append(f.read(1_000_000))
        except OSError:
            pass
    return "\n".join(parts)


def query_spaces(ids):
    """返回 {空间编号: 页面地址列表}；任一空间查不到时抛出 RuntimeError。"""
    try:
        r = subprocess.run(["ego-browser", "nodejs", "-e", QUERY_JS % json.dumps(ids)],
                           capture_output=True, text=True, timeout=QUERY_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"查询超过 {QUERY_TIMEOUT} 秒")
    except OSError as e:
        raise RuntimeError(f"ego-browser 无法启动：{e}")
    # ego-browser nodejs -e 把 console.log 写到标准错误，两路都找
    line = next((l for l in (r.stdout + "\n" + r.stderr).splitlines() if l.startswith("SPACE_URLS ")), "")
    if not line:
        raise RuntimeError("查询没有结果")
    urls = json.loads(line[len("SPACE_URLS "):])
    missing = [i for i in ids if not isinstance(urls.get(str(i)), list)]
    if missing:
        raise RuntimeError(f"查不到空间 {', '.join(map(str, missing))}")
    return urls


def check(cmd):
    """返回拒绝理由；放行时返回空字符串。"""
    if "ego-browser" not in cmd:
        return ""
    text = script_text(cmd)
    if not any(re.search(p, text) for p in SEND_ACTIONS):
        return ""
    if any(re.search(p, text) for p in CHATGPT_CONTEXT):
        return REASON_SEND
    ids = sorted({int(x) for x in SPACE.findall(text)})
    if not ids:
        return REASON_UNKNOWN.format(why="看不出用的是哪个任务空间")
    try:
        urls = query_spaces(ids)
    except (RuntimeError, ValueError) as e:
        return REASON_UNKNOWN.format(why=e)
    if any("chatgpt.com" in u for lst in urls.values() for u in lst):
        return REASON_SEND
    return ""


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command") or ""
    reason = check(cmd) if data.get("tool_name") == "Bash" else ""
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
