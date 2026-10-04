"""Private filesystem operations and recoverable setup commits (POSIX only)."""
from contextlib import ExitStack
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from .setup_config import SetupError


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def directory_path(path):
    """Resolve ancestor aliases, but never follow a writable root symlink."""
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise SetupError('symbolic link directory target is not supported', path=str(path))
    return path.resolve()


def guarded(path):
    path = Path(path)
    for current in [path, *path.parents]:
        if current.is_symlink():
            raise SetupError('symbolic link target or parent is not supported', path=str(path))
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise SetupError('target must be a regular file owned by this user', path=str(path))
        if not os.access(path, os.W_OK):
            raise SetupError('target is not writable; elevated privileges are not supported', path=str(path))
    parent = path.parent
    while not parent.exists():
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        raise SetupError('parent directory is not writable', path=str(path))
    return path.read_bytes() if path.exists() else None


def private_directory(path):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise SetupError('unsafe state directory')
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    return path


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def stage(path, data):
    private_directory(path.parent)
    fd, name = tempfile.mkstemp(prefix='.azir-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic(path, data):
    temporary = stage(path, data)
    try:
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class Change:
    path: Path
    old: bytes | None
    new: bytes

    @property
    def changed(self):
        return self.old != self.new


def unfinished(state):
    # Reject redirection of any setup-owned writable directory before planning.
    for name in ('transactions', 'locks', 'backups'):
        if (state / name).is_symlink():
            raise SetupError('unsafe ' + name + ' directory')
    directory = state / 'transactions'
    if not directory.exists():
        return
    for path in sorted(directory.glob('*.json')):
        try:
            record = json.loads(guarded(path))
        except (OSError, ValueError, TypeError):
            raise SetupError('unreadable transaction; inspect it before retrying', transaction=str(path)) from None
        if record.get('status') != 'completed':
            raise SetupError('unfinished transaction; inspect backups before retrying', transaction=str(path))


def commit(changes, state):
    changes = [change for change in changes if change.changed]
    if not changes:
        return None
    # Each caller, regardless of config-dir, uses the same home state and path locks.
    locks = private_directory(state / 'locks')
    with ExitStack() as stack:
        for change in sorted(changes, key=lambda item: str(item.path)):
            lock = locks / (hashlib.sha256(os.fsencode(change.path)).hexdigest() + '.lock')
            guarded(lock)
            fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            stream = stack.enter_context(os.fdopen(fd, 'r+b'))
            fcntl.flock(stream, fcntl.LOCK_EX)
        unfinished(state)
        for change in changes:
            if digest(guarded(change.path)) != digest(change.old):
                raise SetupError('target changed after planning; regenerate the plan', path=str(change.path))
        transaction_id = uuid.uuid4().hex
        journal = state / 'transactions' / (transaction_id + '.json')
        backup_dir = state / 'backups' / transaction_id
        record = dict(version=1, id=transaction_id, status='preparing', files=[])
        for index, change in enumerate(changes):
            record['files'].append(dict(path=str(change.path), existed=change.old is not None,
                                        old_hash=digest(change.old), new_hash=digest(change.new),
                                        backup=str(backup_dir / str(index)) if change.old is not None else None,
                                        staged=None, replaced=False))
        def save():
            atomic(journal, (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode())
        save()
        staged = []
        try:
            for change, entry in zip(changes, record['files']):
                if change.old is not None:
                    atomic(Path(entry['backup']), change.old)
                temporary = stage(change.path, change.new)
                staged.append(temporary)
                entry['staged'] = str(temporary)
                save()
            record['status'] = 'committing'
            save()
            # Recheck all targets after staging, before the first replacement.
            for change in changes:
                if digest(guarded(change.path)) != digest(change.old):
                    raise SetupError('target changed after planning; regenerate the plan', path=str(change.path))
            for change, entry, temporary in zip(changes, record['files'], staged):
                if digest(guarded(change.path)) != digest(change.old):
                    raise SetupError('target changed during commit', path=str(change.path))
                os.replace(temporary, change.path)
                fsync_directory(change.path.parent)
                entry['replaced'] = True
                save()
            for change in changes:
                if digest(guarded(change.path)) != digest(change.new):
                    raise SetupError('post-write verification failed', path=str(change.path))
            record['status'] = 'completed'
            save()
            return str(journal)
        except BaseException as exc:
            # Restore only contents still owned by this transaction. Keep the journal
            # unfinished even after rollback so the next run explicitly reports it.
            record['status'] = 'failed'
            for change, entry in reversed(list(zip(changes, record['files']))):
                try:
                    if digest(guarded(change.path)) == digest(change.new):
                        if change.old is None:
                            change.path.unlink()
                            fsync_directory(change.path.parent)
                        else:
                            atomic(change.path, change.old)
                        entry['restored'] = True
                except (OSError, SetupError):
                    entry['restore_conflict'] = True
            try:
                save()
            except OSError:
                pass
            if isinstance(exc, KeyboardInterrupt):
                raise SetupError('commit interrupted; inspect the unfinished transaction', transaction=str(journal)) from None
            raise
        finally:
            for temporary in staged:
                temporary.unlink(missing_ok=True)
