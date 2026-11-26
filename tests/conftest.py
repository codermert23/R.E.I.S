import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def pump(qapp):
    def _pump(cond, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            qapp.processEvents()
            if cond():
                return True
            time.sleep(0.01)
        qapp.processEvents()
        return cond()

    return _pump


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    """Gerçek %APPDATA%\\Reis ve keyring'e asla dokunma."""
    monkeypatch.setenv("REIS_DATA_DIR", str(tmp_path / "data"))
    from reis.config import secrets

    store = {}
    monkeypatch.setattr(secrets, "get_secret", lambda n: store.get(n, ""))
    monkeypatch.setattr(secrets, "set_secret", lambda n, v: store.__setitem__(n, (v or "").strip()))
    return store
