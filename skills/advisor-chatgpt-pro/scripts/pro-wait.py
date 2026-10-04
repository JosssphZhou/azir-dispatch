#!/usr/bin/env python3
"""等 ChatGPT Pro 答完，打印最后一条回答。只读页面，不发送任何消息，不计数。

用法：
  pro-wait.py --space <SPACE_ID> [--interval 20] [--max-polls 90] [--max-minutes 30]

每轮只读页面：
「思考中」字样和停止按钮都没有，最后一条回答非空且连续两轮相同，才算答完。

输出：答完时打印 ===ANSWER===、回答全文、URL 一行，退出码 0；轮询次数用完或超过总时长打印 TIMEOUT，退出码 2。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from azir_dispatch.advisor import mark_verified

EXIT_TIMEOUT = 2

# 页面后备用的选择器，ChatGPT 改版时只改这里。2026-09-26 实测：回答正文在这个属性的元素里，
# 旧版的 data-message-author-role 已经没有了。
ANSWER_SELECTORS = ['[data-markdown-text-style="assistant-message"]', '[data-message-author-role="assistant"]']

STATE_JS = r"""
const page = (await taskSpace(__SPACE__)).page('p1')
const state = await page.evaluate(async sels => {
  const out = { url: location.href, dom: null }
  const sel = sels.find(s => document.querySelector(s))
  const msgs = sel ? document.querySelectorAll(sel) : []
  const main = document.querySelector('main')?.innerText || ''
  out.dom = { thinking: /思考中|Thinking/.test(main.slice(-2000)) ||
                !!document.querySelector('button[aria-label*="停止"], button[aria-label*="Stop"]'),
              text: msgs.length ? (msgs[msgs.length - 1].innerText || '').trim() : '' }
  return out
}, __SELECTORS__)
// 回答可能很长，控制台输出会被截断或折行，所以写进文件交给 Python 读。
;(await import('node:fs')).writeFileSync(__OUT__, JSON.stringify(state))
console.log('STATE_WRITTEN')
"""

def build_js(space, out):
    return (STATE_JS.replace("__SPACE__", str(int(space)))
                    .replace("__SELECTORS__", json.dumps(ANSWER_SELECTORS, ensure_ascii=False))
                    .replace("__OUT__", json.dumps(out, ensure_ascii=False)))


def answer_from_dom(dom, prev_text):
    """页面后备：没有在思考、回答非空、和上一轮读到的相同，才算答完。"""
    text = (dom or {}).get("text") or ""
    done = not (dom or {}).get("thinking") and bool(text) and text == prev_text
    return done, text


def read_state(space, timeout=120):
    with tempfile.TemporaryDirectory(prefix="pro-wait-") as d:
        out = os.path.join(d, "state.json")
        try:
            res = subprocess.run(["ego-browser", "nodejs"], input=build_js(space, out),
                                 capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            print("读取页面超时", file=sys.stderr)
            return None
        if not os.path.exists(out):
            print("读取页面失败。", file=sys.stderr)
            return None
        with open(out, encoding="utf-8") as f:
            return json.load(f)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--space", required=True, help="第 1 步打印的 SPACE_ID")
    p.add_argument("--config", help="azir-dispatch 配置文件")
    p.add_argument("--interval", type=float, default=20, help="每轮间隔秒数")
    p.add_argument("--max-polls", type=int, default=90)
    p.add_argument("--max-minutes", type=float, default=30, help="总时长上限，单次读取的时间也算在内")
    a = p.parse_args()

    deadline = time.monotonic() + a.max_minutes * 60
    prev = None  # 连续两轮相同且停止生成才算完成。
    for i in range(a.max_polls):
        if i:
            time.sleep(max(0, min(a.interval, deadline - time.monotonic())))
        left = deadline - time.monotonic()
        if left <= 0:
            break
        s = read_state(a.space, timeout=max(1, min(120, left)))
        if s is None:
            prev = None
            continue
        done, text = answer_from_dom(s.get("dom"), prev)
        prev = text
        if done:
            from azir_dispatch.doctor import load_config, snapshot
            cfg = load_config(a.config)
            mark_verified()
            snapshot(cfg, probe_versions=False)
            print("===ANSWER===\n" + text + "\nURL " + s.get("url", ""))
            return
    print("TIMEOUT")
    sys.exit(EXIT_TIMEOUT)

if __name__ == "__main__":
    main()
