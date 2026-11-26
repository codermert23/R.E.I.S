from PySide6.QtCore import Qt

from reis.config import secrets
from reis.config.settings import Settings, load_settings, save_settings, settings_path
from reis.ui.first_run import FirstRunWizard
from reis.ui.settings_dialog import SettingsDialog


def test_wizard_is_always_on_top_and_has_five_pages(qapp):
    w = FirstRunWizard(Settings())
    assert w.windowFlags() & Qt.WindowStaysOnTopHint
    assert len(w.pageIds()) == 5
    assert w.autostart.isChecked()  # ilk kurulumdan sonra varsayılan açık


def test_wizard_apply_everything(qapp, isolated_data):
    s = Settings()
    w = FirstRunWizard(s)
    w.gemini.setText("  KEY123 ")
    w.proj_name.setText("BallClash")
    w.proj_path.setText(r"C:\notes\ballclash")
    w.daily_path.setText(r"C:\notes\daily")
    w.schedule_path.setText(r"C:\notes\program.md")
    w.voice.setCurrentIndex(w.voice.findData("tr_TR-fettah-medium"))
    w.autostart.setChecked(False)
    w.apply()
    assert isolated_data[secrets.GEMINI] == "KEY123"
    assert s.projects == [{"name": "BallClash", "path": r"C:\notes\ballclash"}]
    assert s.daily_path.endswith("daily") and s.schedule_path.endswith("program.md")
    assert s.piper_voice == "tr_TR-fettah-medium" and s.autostart is False and s.first_run_done


def test_wizard_skip_everything_keeps_defaults(qapp, isolated_data):
    s = Settings()
    w = FirstRunWizard(s)
    w.apply()  # hiçbir şey girilmeden "atla"
    assert s.projects == [] and s.daily_path == "" and s.first_run_done
    assert secrets.GEMINI not in isolated_data


def test_settings_dialog_flags_and_save(qapp, isolated_data):
    s = Settings()
    d = SettingsDialog(s)
    assert d.windowFlags() & Qt.WindowStaysOnTopHint
    d.autostart.setChecked(True)
    d.tts_provider.setCurrentIndex(d.tts_provider.findData("piper"))
    d.yazgi_voice.setCurrentIndex(d.yazgi_voice.findData("female-02"))
    d.hotkey.setText("Ctrl+Alt+R")
    d.whisper.setCurrentIndex(d.whisper.findData("base"))
    d._save()
    assert s.autostart and s.tts_provider == "piper" and s.yazgi_voice == "female-02"
    assert s.hotkey == "Ctrl+Alt+R" and s.whisper_model == "base"


def test_settings_dialog_rejects_bad_hotkey(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    s = Settings()
    d = SettingsDialog(s)
    d.hotkey.setText("Ctrl")
    d._save()
    assert s.hotkey == "F1"


def test_first_run_detected_by_missing_settings_file(qapp):
    assert not settings_path().exists()
    save_settings(Settings(first_run_done=True))
    assert settings_path().exists() and load_settings().first_run_done
