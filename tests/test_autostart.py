import pytest

from reis.core import autostart

KEY = autostart.RUN_KEY


class FakeWinreg:
    """winreg'in bellek içi taklidi: gerçek kayıt defterine dokunmaz."""
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 1

    def __init__(self):
        self.keys: dict[str, dict] = {}

    class _K:
        def __init__(self, d):
            self.d = d

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, root, path, res=0, access=0):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self._K(self.keys[path])

    def CreateKeyEx(self, root, path, res=0, access=0):
        return self._K(self.keys.setdefault(path, {}))

    def QueryValueEx(self, k, name):
        if name not in k.d:
            raise FileNotFoundError(name)
        return k.d[name], self.REG_SZ

    def SetValueEx(self, k, name, res, typ, value):
        k.d[name] = value

    def DeleteValue(self, k, name):
        if name not in k.d:
            raise FileNotFoundError(name)
        del k.d[name]


def test_build_command_source(tmp_path):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    cmd = autostart.build_command(frozen=False, executable=str(tmp_path / "python.exe"))
    assert cmd == f'"{tmp_path / "pythonw.exe"}" -m reis'


def test_build_command_source_without_pythonw_falls_back(tmp_path):
    (tmp_path / "python.exe").write_text("")
    cmd = autostart.build_command(frozen=False, executable=str(tmp_path / "python.exe"))
    assert cmd.endswith('python.exe" -m reis')


def test_build_command_frozen():
    assert autostart.build_command(frozen=True, executable=r"C:\Apps\Reis\Reis.exe") == r'"C:\Apps\Reis\Reis.exe"'


def test_enable_query_disable_roundtrip():
    wr = FakeWinreg()
    assert not autostart.is_enabled(wr)
    autostart.enable("CMD", winreg_mod=wr)
    assert autostart.is_enabled(wr) and autostart.current_command(wr) == "CMD"
    assert wr.keys[KEY] == {"Reis": "CMD"}
    autostart.disable(wr)
    assert not autostart.is_enabled(wr) and autostart.current_command(wr) is None
    autostart.disable(wr)  # ikinci kez hata vermez


def test_disable_without_key_is_noop():
    autostart.disable(FakeWinreg())


def test_set_enabled():
    wr = FakeWinreg()
    autostart.set_enabled(True, command="X", winreg_mod=wr)
    assert autostart.is_enabled(wr)
    autostart.set_enabled(False, winreg_mod=wr)
    assert not autostart.is_enabled(wr)
