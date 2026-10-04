"""Optional browser advisor configuration and presence-only connection checks."""
import json
import shutil
import subprocess
import sys
import uuid

DEFAULTS = dict(provider='chatgpt-pro', enabled=False, weekly_limit=50)


def settings(config):
    value = config.get('advisor', {})
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('invalid advisor settings')
    result = {**DEFAULTS, **value}
    if (result['provider'] != 'chatgpt-pro' or type(result['enabled']) is not bool
            or type(result['weekly_limit']) is not int or result['weekly_limit'] < 1):
        raise ValueError('advisor requires chatgpt-pro, boolean enabled and positive weekly_limit')
    return result


def browser_installed():
    return sys.platform == 'darwin' and bool(shutil.which('ego-browser'))


def browser_run(script, timeout=30):
    # Browser login stays inside ego lite; no API credential values enter probes.
    from .setup_config import clean_environment
    result = subprocess.run(['ego-browser', 'nodejs'], input=script, text=True,
                            capture_output=True, timeout=timeout, env=clean_environment())
    if result.returncode:
        raise ValueError('ego-browser connection failed')
    for line in (result.stdout + '\n' + result.stderr).splitlines():
        if line.startswith('ADVISOR_STATE '):
            value = json.loads(line.removeprefix('ADVISOR_STATE '))
            if isinstance(value, dict):
                return value
    raise ValueError('ego-browser connection result missing')


PAGE_STATE = r"""() => {
  const composer = document.querySelector('#prompt-textarea, form [contenteditable="true"][role="textbox"]');
  const login = [...document.querySelectorAll('button,a')].some(e => /^(Log in|Sign in|登录|登入)$/i.test((e.textContent || '').trim()));
  const model = [...document.querySelectorAll('form button[aria-haspopup="menu"]')].find(e => /Pro$/.test((e.textContent || '').trim()));
  return {logged_in: !!composer && !login, pro: !!model};
}"""


def check_space(space):
    return browser_run(fr"""
const task = await taskSpace({int(space)});
const page = task.page('p1');
if (!/^https:\/\/chatgpt\.com\//.test(await page.url())) throw new Error('Not ChatGPT');
console.log('ADVISOR_STATE ' + JSON.stringify(await page.evaluate({PAGE_STATE})));
""")


def probe_ready():
    if not browser_installed():
        return False
    try:
        value = browser_run(f"""
const task = await taskSpace({json.dumps('advisor-check-' + uuid.uuid4().hex)});
try {{
  const page = task.page('p1');
  await page.goto('https://chatgpt.com/');
  await page.waitForFunction(() => !!document.querySelector('#prompt-textarea, form [contenteditable="true"][role="textbox"]') || [...document.querySelectorAll('button,a')].some(e => /^(Log in|Sign in|登录|登入)$/i.test((e.textContent || '').trim())), undefined, {{timeout: 15000}});
  console.log('ADVISOR_STATE ' + JSON.stringify(await page.evaluate({PAGE_STATE})));
}} finally {{ await task.finish({{keep: []}}); }}
""")
        return value.get('logged_in') is True and value.get('pro') is True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def available(config, *, probe=False):
    if not settings(config)['enabled'] or not browser_installed():
        return False
    from .setup_io import guarded
    from .setup_state import state_directory
    try:
        ready = json.loads(guarded(state_directory() / 'advisor-chatgpt-pro.json') or b'{}')
        verified = ready.get('verified') is True
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return verified and (not probe or probe_ready())


def mark_verified():
    from .setup_io import atomic, guarded
    from .setup_state import state_directory
    path = state_directory() / 'advisor-chatgpt-pro.json'
    guarded(path)
    atomic(path, b'{"verified":true,"source":"chatgpt-pro"}\n')


def add_parser(commands):
    parser = commands.add_parser('advisor', help='open or check the optional GPT Pro advisor')
    parser.add_argument('action', choices=['open', 'check'])
    parser.add_argument('--config')
    parser.add_argument('--space', type=int)


def run(args):
    from .doctor import load_config
    try:
        config = load_config(args.config)
        if not settings(config)['enabled'] or not browser_installed():
            print(json.dumps(dict(status='optional_unconfigured', hint='macOS：安装 ego lite 并完成引导；其他系统跳过。'), ensure_ascii=False))
            return 4
        if args.action == 'open':
            value = browser_run(f"""
const task = await taskSpace({json.dumps('advisor-chatgpt-pro-' + uuid.uuid4().hex)});
const page = task.page('p1');
await page.goto('https://chatgpt.com/');
console.log('ADVISOR_STATE ' + JSON.stringify({{space: task.spaceId, ...(await page.evaluate({PAGE_STATE}))}}));
""")
        else:
            if args.space is None:
                raise ValueError('advisor check requires --space')
            value = check_space(args.space)
        value['status'] = 'ready' if value.get('logged_in') and value.get('pro') else 'optional_unconfigured'
        print(json.dumps(value, ensure_ascii=False))
        return 0 if args.action == 'open' or value['status'] == 'ready' else 4
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        print(json.dumps(dict(status='optional_unconfigured', hint='检查浏览器连接、ChatGPT 登录和 Pro 档。'), ensure_ascii=False))
        return 4
