"""Isolate collection and every test, including inherited subprocess paths."""
from pathlib import Path
import tempfile

import pytest

from state_isolation import StateGuard, isolated_environment


def pytest_sessionstart(session):
    session.config._azir_state_guard = StateGuard()
    temporary = tempfile.TemporaryDirectory(prefix='azir-test-session-')
    patch = pytest.MonkeyPatch()
    session.config._azir_test_environment = (temporary, patch)
    for key, value in isolated_environment(Path(temporary.name)).items():
        patch.setenv(key, value)


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path, monkeypatch):
    for key, value in isolated_environment(tmp_path / 'environment').items():
        monkeypatch.setenv(key, value)


def pytest_sessionfinish(session, exitstatus):
    try:
        changes = session.config._azir_state_guard.changes()
    finally:
        temporary, patch = session.config._azir_test_environment
        patch.undo()
        temporary.cleanup()
    if changes:
        reporter = session.config.pluginmanager.get_plugin('terminalreporter')
        if reporter:
            reporter.write_sep('=', 'azir-dispatch real-home state leak')
            for change in changes:
                reporter.write_line(change)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
