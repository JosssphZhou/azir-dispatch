#!/usr/bin/env python3
"""向 ChatGPT Pro 发一条消息：先按每周用量放行，再上传附件、写指令、点发送，最后记账。

所有 Pro 提问（首问、追问、分批图片的每一批、补发「继续」）都从这里发，
不再在内联脚本里点发送按钮，这样每一次都会被计数。

用法：
  pro-send.py --space <SPACE_ID> --prompt-file <指令文件> \
      [--file <附件绝对路径> ...] [--marker "<指令里一段独有词>"] [--dry-run]

退出码：0 已确认发送并拿到链接；3 用量拦截；4 明确未点发送（名额已退回）；
5 发送状态不确定或已点发送但无链接（名额保留，禁止直接重发）。
--dry-run：只走计数（占名额再退回），不打开浏览器，用来验证计数。
"""
import argparse
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pro_usage  # noqa: E402
from azir_dispatch import advisor

# 页面元素的识别规则集中在这里，ChatGPT 改版时只改这几项。
# 2026-09-29 实测图片：卡片是 role=button 的 div，上传中已有卡片与移除按钮，
# 移除按钮为「移除“文件名”」；必须等 progressbar 消失后才能认定完成。旧版按钮文案继续兼容。
# 输入框不再有 #prompt-textarea，是表单里 role="textbox" 的 contenteditable；发送按钮不再有 data-testid，是表单唯一的 submit 按钮。
REMOVE_LABEL_PATTERNS = [
    r"^移除文件\d+：(.+)$",
    r"^移除 (.+)$",
    r"^移除[“\"](.+)[”\"]$",
    r"^Remove file \d+: (.+)$",
    r"^Remove (.+)$",
]
COMPOSER_SELECTORS = ['#prompt-textarea', 'form [contenteditable="true"][role="textbox"]']
SEND_SELECTORS = ['[data-testid="send-button"]', 'form button[type="submit"]']

# 同名按数量核对：卡片与移除按钮各自独立计数，不能一张卡片抵两份附件；
# 不同版本的移除文案可以混用，但同一按钮只计一次。
ATTACHED_JS = r"""(labels, names, patterns) => {
  const need = {}
  for (const n of names) need[n] = (need[n] || 0) + 1
  const regexes = patterns.map(p => new RegExp(p))
  return Object.entries(need).every(([n, k]) => {
    const cards = labels.filter(l => l === n).length
    const removes = labels.filter(l => regexes.some(re => re.exec(l)?.[1] === n)).length
    return Math.max(cards, removes) >= k
  })
}"""

JS = r"""
const page = (await taskSpace(__SPACE__)).page('p1')
const files = __FILES__
const images = __IMAGES__
const text = (await import('node:fs')).readFileSync(__PROMPT__, 'utf8')
const marker = __MARKER__
const attachedAll = __ATTACHED__
const removePatterns = __REMOVE_PATTERNS__
const receiptTag = __RECEIPT_TAG__
const report = (event, detail = '') => console.log(receiptTag + ' ' + event + (detail ? ' ' + detail : ''))
const pick = sels => page.evaluate(ss => ss.find(s => document.querySelectorAll(s).length === 1) || '', sels)
if (files.length) {
  await page.waitForTimeout(5000)
  const inputs = await page.evaluate(() => [...document.querySelectorAll('form input[type="file"]')].map(e => ({
    accept: e.getAttribute('accept') || '', selector: `form input[type="file"][id="${e.id}"]`, id: e.id
  })))
  const imageInput = inputs.filter(e => e.accept === 'image/*')
  const mediaInput = inputs.filter(e => e.accept === 'image/*,video/*')
  const generalInput = inputs.filter(e => !e.accept || e.accept === '*/*')
  const chosenImage = imageInput.length === 1 ? imageInput : mediaInput
  const groups = [[images, chosenImage], [files.filter(f => !images.includes(f)), generalInput]]
  for (const [batch, matches] of groups) {
    if (!batch.length) continue
    if (matches.length !== 1 || !matches[0].id) { report('UPLOAD_FAILED'); process.exit(0) }
    await page.setInputFiles(matches[0].selector, batch)
  }
  const names = files.map(f => f.split('/').pop())
  const attachmentState = () => page.evaluate(() => ({
    labels: [...document.querySelectorAll('form button[aria-label], form [role="button"][aria-label]')].map(b => b.getAttribute('aria-label')),
    uploading: !!document.querySelector('form [role="progressbar"]')
  }))
  let attached = false
  for (let i = 0; i < 120 && !attached; i++) {
    const state = await attachmentState()
    attached = !state.uploading && attachedAll(state.labels, names, removePatterns)
    if (!attached) await page.waitForTimeout(500)
  }
  if (!attached) { report('UPLOAD_FAILED'); process.exit(0) }
}
const composer = await pick(__COMPOSER__)
if (!composer) { report('TYPE_FAILED'); process.exit(0) }
await page.click(composer, { label: '聚焦输入框' })
await page.keyboard.insertText(text)
await page.waitForTimeout(1000)
const ok = (await page.evaluate(() => document.querySelector('form')?.innerText || '')).includes(marker)
if (!ok) { report('TYPE_FAILED'); process.exit(0) }
const send = await pick(__SEND__)
if (!send) { report('SEND_NOT_FOUND'); process.exit(0) }
await page.waitForFunction(s => { const b = document.querySelector(s); return b && !b.disabled }, send, { timeout: 60000 })
report('SEND_CLICKING')
await page.click(send, { label: '发送' })
report('SENT')
await page.waitForURL(/\/c\/(?!local-)/, { timeout: 30000 }).catch(() => {})
report('URL', await page.url())
"""


CONVERSATION_URL = re.compile(
    r"https://chatgpt\.com/c/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
)


def is_conversation_url(url):
    """只接受网页实际分配的完整 UUID 链接，不认本地占位和残段。"""
    return bool(CONVERSATION_URL.fullmatch(url))


def build_js(space, files, prompt, marker, receipt_tag):
    dump = lambda v: json.dumps(v, ensure_ascii=False)
    return (JS.replace("__SPACE__", str(int(space)))
              .replace("__FILES__", dump(files))
              .replace("__IMAGES__", dump([f for f in files if (mimetypes.guess_type(f)[0] or '').startswith('image/')]))
              .replace("__PROMPT__", dump(prompt))
              .replace("__MARKER__", dump(marker))
              .replace("__ATTACHED__", ATTACHED_JS)
              .replace("__REMOVE_PATTERNS__", dump(REMOVE_LABEL_PATTERNS))
              .replace("__RECEIPT_TAG__", dump(receipt_tag))
              .replace("__COMPOSER__", dump(COMPOSER_SELECTORS))
              .replace("__SEND__", dump(SEND_SELECTORS)))


def receipt_events(stdout, stderr, tag):
    """只认完整换行的本轮回执；跨通道无法确定先后顺序，交给只读核对。"""
    prefix = tag + " "
    channels = []
    for stream in (stdout, stderr):
        if stream and not stream.endswith("\n"):
            return None
        lines = (line.removesuffix("\r") for line in stream.split("\n")[:-1])
        channels.append([line[len(prefix):] for line in lines if line.startswith(prefix)])
    return None if all(channels) else channels[0] or channels[1]


def default_marker(text):
    for line in text.splitlines():
        s = line.strip()
        if len(s) >= 6:
            return s[:12]
    return text.strip()[:12]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--space", required=True, help="第 1 步打印的 SPACE_ID")
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--config", help="azir-dispatch 配置文件")
    p.add_argument("--file", action="append", default=[], help="附件绝对路径，可重复")
    p.add_argument("--marker", help="用来核对输入成功的独有词，默认取指令第一行前 12 个字")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    prompt = os.path.abspath(os.path.expanduser(a.prompt_file))
    with open(prompt, encoding="utf-8") as source:
        text = source.read()
    for f in a.file:
        if not os.path.isabs(f) or not os.path.exists(f):
            sys.exit(f"附件不存在或不是绝对路径：{f}")
    marker = a.marker or default_marker(text)

    pro_usage.configure(a.config)
    cfg = pro_usage.config()
    if not advisor.settings(cfg)['enabled']:
        print('顾问未配置，可选。')
        return 4
    if not a.dry_run:
        if not advisor.browser_installed():
            print('顾问未配置，可选：需要 macOS 和 ego-browser。')
            return 4
        state = advisor.check_space(a.space)
        if not state.get('logged_in') or not state.get('pro'):
            print('顾问未配置，可选：请本人登录 ChatGPT 并选择 Pro 档。')
            return 4
    rid, msg = pro_usage.reserve(a.space)
    if msg:
        print(msg)
    if rid is None:
        sys.exit(pro_usage.EXIT_BLOCKED)

    if a.dry_run:
        pro_usage.cancel(rid, "dry-run")
        print(f"DRY_RUN 放行（记录 {rid} 已作废，不计数）")
        return

    # 只有 CLI 启动前失败或正常退出且返回明确的点击前信号，才可断言未发送。
    # 超时、非零退出和输出丢失无法排除点击已生效，必须保留名额待只读核对。
    status, reason, out, url, stderr = "unknown", "", "", "", ""
    receipt_tag = "PRO_SEND_" + secrets.token_hex(16)
    try:
        js = build_js(a.space, a.file, prompt, marker, receipt_tag)
    except Exception as e:
        status, reason = "not_sent", "CLI 启动前脚本出错"
    else:
        try:
            res = subprocess.run(["ego-browser", "nodejs"], input=js, capture_output=True, text=True, timeout=300)
        except FileNotFoundError as e:
            status, reason = "not_sent", "CLI 未启动"
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode('utf-8', errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or '')
        except Exception as e:
            print("浏览器命令结果不确定。")
        else:
            out, stderr = res.stdout or '', res.stderr or ''
            # 真实 ego-browser nodejs 的 console.log 走 stderr；两通道都只认本轮私有标识。
            events = receipt_events(out, stderr, receipt_tag)
            pre_send = ("UPLOAD_FAILED", "TYPE_FAILED", "SEND_NOT_FOUND")
            if res.returncode == 0 and events is not None:
                if len(events) == 1 and events[0] in pre_send:
                    status, reason = "not_sent", events[0]
                elif len(events) == 3 and events[:2] == ["SEND_CLICKING", "SENT"]:
                    url = events[2][4:] if events[2].startswith("URL ") else ""
                    if is_conversation_url(url):
                        status = "sent"

    if status == "not_sent":
        pro_usage.cancel(rid, reason)
        print(f"明确未点发送（{reason}），名额已退回。")
        sys.exit(4)

    pro_usage.confirm(rid, url if status == "sent" else "链接未知")
    used, s = pro_usage.summary(pro_usage.load(pro_usage.usage_file()))
    print("已计数。" + s)
    if status == "sent":
        print('已确认发送。')
    if status == "unknown":
        print("发送状态待只读核对（可能已发送，也可能未发送）；名额已保留、链接未知。"
              "请只读核对页面与用量记录，不要直接重发。")
        sys.exit(5)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print('配置、计数或浏览器连接失败；停止发送。', file=sys.stderr)
        raise SystemExit(2)
