"""Detect changes to the user's state without reading it into test output."""
import hashlib
import os
from pathlib import Path
import pwd
import stat


def isolated_environment(root):
    home = root / 'home'
    home.mkdir(parents=True)
    state = home / '.local/state/azir-dispatch'
    config = home / '.config/azir-dispatch/config.toml'
    config.parent.mkdir(parents=True)
    config.write_text('')
    return {
        'HOME': str(home),
        'XDG_CONFIG_HOME': str(home / '.config'),
        'XDG_STATE_HOME': str(home / '.local/state'),
        'XDG_CACHE_HOME': str(home / '.cache'),
        'AZIR_DISPATCH_STATE_DIR': str(state),
        'AZIR_DISPATCH_LOG': str(state / 'events.jsonl'),
        'AZIR_DISPATCH_CONFIG': str(config),
    }


def state_snapshot(root):
    result = {}

    def visit(path):
        info = path.lstat()
        relative = str(path.relative_to(root))
        if stat.S_ISLNK(info.st_mode):
            result[relative] = ('symlink', os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            result[relative] = ('directory', stat.S_IMODE(info.st_mode))
            for child in sorted(path.iterdir()):
                visit(child)
        elif stat.S_ISREG(info.st_mode):
            result[relative] = ('file', info.st_mode, info.st_mtime_ns,
                                hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            result[relative] = ('other', info.st_mode, info.st_mtime_ns)

    if root.exists() or root.is_symlink():
        visit(root)
    return result


class StateGuard:
    def __init__(self):
        # Also watch the account's home when a caller supplies a different HOME.
        homes = {Path.home(), Path(pwd.getpwuid(os.getuid()).pw_dir)}
        roots = {home / suffix for home in homes for suffix in (
            '.local/state/azir-dispatch', '.claude/state/azir-dispatch')}
        for name in ('AZIR_DISPATCH_STATE_DIR', 'AZIR_DISPATCH_LOG'):
            if os.environ.get(name):
                roots.add(Path(os.environ[name]).expanduser().absolute())
        roots |= {root.resolve() for root in roots}
        self.before = {root: state_snapshot(root) for root in sorted(roots)}

    def changes(self):
        changes = []
        for root, before in self.before.items():
            after = state_snapshot(root)
            for name in sorted(before.keys() | after.keys()):
                if before.get(name) != after.get(name):
                    action = 'created' if name not in before else 'deleted' if name not in after else 'modified'
                    changes.append(f'{action}: {root / name}')
        return changes
