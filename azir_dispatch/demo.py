"""Replay the shared video recording without credentials or live dispatch."""
import sys

from .setup_config import ROOT

DEMO_FILE = ROOT / 'azir_dispatch/demo/video-run.jsonl'


def add_parser(commands):
    parser = commands.add_parser('demo', help='replay the bundled demonstration')
    parser.add_argument('--config')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--speed', type=float, default=1)


def run(args):
    if not DEMO_FILE.is_file():
        print('演示事件文件缺失', file=sys.stderr)
        return 1
    from .dashboard import main
    argv = ['--replay', str(DEMO_FILE), '--no-advisor-scan', '--speed', str(args.speed), '--seconds', str(args.seconds)]
    if args.config:
        argv += ['--config', args.config]
    if args.check:
        argv.append('--check')
    if args.once:
        argv.append('--once')
    result = main(argv)
    if result == 0:
        from .doctor import load_config, snapshot
        snapshot(load_config(args.config), steps={'board': 'ok'}, probe_versions=False)
    return result
