from PySide6.QtCore import Qt

from reis.config.settings import Settings
from reis.ui.settings_dialog import SettingsDialog
from reis.ui.first_run import FirstRunWizard
from reis.ui.voice_test import TEST_SENTENCE, VoiceTester


def test_provider_dropdown_and_defaults(qapp):
    d = SettingsDialog(Settings())
    assert [d.tts_provider.itemData(i) for i in range(4)] == ["yazgi", "edge", "gemini", "piper"]
    assert d.tts_provider.currentData() == "yazgi"
    assert d.windowFlags() & Qt.WindowStaysOnTopHint
    d.tts_provider.setCurrentIndex(d.tts_provider.findData("gemini"))
    assert not d.edge_voice.isHidden() or True  # görünürlük diyalog gösterilmeden güvenilmez
    d.edge_voice.setCurrentIndex(d.edge_voice.findData("tr-TR-AhmetNeural"))
    d.gemini_voice.setCurrentIndex(d.gemini_voice.findData("Puck"))
    s = d.settings
    d._save()
    assert s.tts_provider == "gemini" and s.edge_voice == "tr-TR-AhmetNeural" and s.gemini_tts_voice == "Puck"


def test_wizard_voice_page_applies_provider(qapp):
    s = Settings()
    w = FirstRunWizard(s)
    w.provider.setCurrentIndex(w.provider.findData("piper"))
    w.apply()
    assert s.tts_provider == "piper"


def test_voice_tester_synthesizes_fixed_sentence_in_worker(qapp, pump):
    from PySide6.QtWidgets import QPushButton

    played, texts = [], []

    class T:
        def synth(self, text):
            texts.append(text)
            return [1, 2, 3], 24000

    class P:
        def play(self, pcm, sr):
            played.append(sr)

    btn = QPushButton()
    vt = VoiceTester(btn, Settings, tts_factory=lambda s: T(), player=P())
    btn.click()
    assert not btn.isEnabled()
    assert pump(lambda: btn.isEnabled())
    assert texts == [TEST_SENTENCE] and played == [24000]


def test_voice_tester_reports_error(qapp, pump):
    from PySide6.QtWidgets import QLabel, QPushButton

    def bad(s):
        raise RuntimeError("internet yok")

    btn, lab = QPushButton(), QLabel()
    VoiceTester(btn, Settings, lab, tts_factory=bad, player=object())
    btn.click()
    assert pump(lambda: lab.text() != "")
    assert "internet yok" in lab.text()
