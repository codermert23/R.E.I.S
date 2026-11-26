"""İlk kurulum sihirbazı. Yalnızca ayar dosyası yokken gösterilir; her sayfa atlanabilir."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget, QWizard, QWizardPage)

from ..config import secrets
from ..config.settings import Settings, models_dir
from ..core import downloads
from ..core.tts.piper_tts import is_installed as piper_installed
from .download_row import DownloadRow
from .settings_dialog import yazgi_row
from .voice_test import VoiceTester, fill_edge_voices, fill_gemini_voices, fill_provider_combo, fill_yazgi_voices


def _label(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    return lb


def _path_edit(parent: QWidget, folder_ok: bool) -> tuple[QWidget, QLineEdit]:
    edit = QLineEdit()
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(edit, 1)

    def pick(folder: bool):
        if folder:
            p = QFileDialog.getExistingDirectory(parent, "Klasör seç", edit.text())
        else:
            p, _ = QFileDialog.getOpenFileName(parent, "Dosya seç", edit.text(),
                                               "Markdown (*.md *.markdown *.txt);;Tümü (*)")
        if p:
            edit.setText(p)

    b = QPushButton("Dosya")
    b.clicked.connect(lambda: pick(False))
    row.addWidget(b)
    if folder_ok:
        b2 = QPushButton("Klasör")
        b2.clicked.connect(lambda: pick(True))
        row.addWidget(b2)
    return w, edit


class FirstRunWizard(QWizard):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)  # sidebar always-on-top; sihirbaz arkada kalmasın
        self.setWindowTitle("Reis'e hoş geldin")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumWidth(520)
        self.settings = settings
        self.setButtonText(QWizard.NextButton, "İleri / Atla >")
        self.setButtonText(QWizard.BackButton, "< Geri")
        self.setButtonText(QWizard.CancelButton, "Kurulumu atla")
        self.setButtonText(QWizard.FinishButton, "Bitir")

        # 1. Gemini anahtarı
        p1 = QWizardPage()
        p1.setTitle("Gemini API anahtarı")
        p1.setSubTitle("Komutları yönlendirmek için gerekir. İstersen sonra Ayarlar'dan girebilirsin.")
        self.gemini = QLineEdit(secrets.get_secret(secrets.GEMINI))
        self.gemini.setEchoMode(QLineEdit.Password)
        self.gemini.setPlaceholderText("aistudio.google.com adresinden ücretsiz alınır")
        l1 = QVBoxLayout(p1)
        l1.addWidget(self.gemini)
        l1.addWidget(_label("Anahtar Windows Credential Manager'da saklanır, dosyaya yazılmaz."))

        # 2. Yollar
        p2 = QWizardPage()
        p2.setTitle("Notların nerede?")
        p2.setSubTitle("Hepsi isteğe bağlı; sonra uygulamadan da eklenebilir.")
        self.proj_name = QLineEdit()
        self.proj_name.setPlaceholderText("örn. BallClash")
        pw, self.proj_path = _path_edit(p2, folder_ok=True)
        dw, self.daily_path = _path_edit(p2, folder_ok=True)
        sw, self.schedule_path = _path_edit(p2, folder_ok=False)
        f2 = QFormLayout(p2)
        f2.addRow("İlk proje adı", self.proj_name)
        f2.addRow("Proje raporu / klasörü", pw)
        f2.addRow("Günlük plan (dosya ya da klasör)", dw)
        f2.addRow("Ders / iş programı dosyası", sw)

        # 3. Ses
        p3 = QWizardPage()
        p3.setTitle("Reis'in sesi")
        p3.setSubTitle("Yazgı: çevrimdışı, ücretsiz, doğal Türkçe (kaynaktan çalıştırmada; yaklaşık 1 GB indirme, "
                       "isteğe bağlı). Kurulana kadar Edge sesi (internet gerekir) kullanılır. Piper: çevrimdışı, ~60 MB.")
        self.provider = QComboBox()
        fill_provider_combo(self.provider, eleven=False)
        self.provider.setCurrentIndex(max(0, self.provider.findData(settings.tts_provider)))
        self.yazgi_voice = QComboBox()
        fill_yazgi_voices(self.yazgi_voice)
        self.yazgi_voice.setCurrentIndex(max(0, self.yazgi_voice.findData(settings.yazgi_voice)))
        self.yazgi_dl = yazgi_row(self)
        self.edge_voice = QComboBox()
        fill_edge_voices(self.edge_voice)
        self.edge_voice.setCurrentIndex(max(0, self.edge_voice.findData(settings.edge_voice)))
        self.gemini_voice = QComboBox()
        fill_gemini_voices(self.gemini_voice)
        self.gemini_voice.setCurrentIndex(max(0, self.gemini_voice.findData(settings.gemini_tts_voice)))
        self.gemini_note = _label("Gemini TTS, yukarıda girdiğin Gemini anahtarını kullanır.")
        self.voice = QComboBox()
        for v in downloads.PIPER_VOICES:
            self.voice.addItem(v, v)
        self.voice.setCurrentIndex(max(0, self.voice.findData(settings.piper_voice)))
        self.voice_dl = DownloadRow(self._make_voice_dl,
                                    lambda: piper_installed(self.voice.currentData(), models_dir()))
        self.voice.currentIndexChanged.connect(lambda _i: self.voice_dl.refresh())
        self.test_btn = QPushButton("Sesi dene")
        self.test_status = _label("")
        self.voice_tester = VoiceTester(self.test_btn, self._tts_snapshot, self.test_status,
                                        tts_factory=self._test_factory)
        l3 = QVBoxLayout(p3)
        l3.addWidget(self.provider)
        self._voice_groups = {
            "yazgi": [self.yazgi_voice, self.yazgi_dl],
            "edge": [self.edge_voice],
            "gemini": [self.gemini_voice, self.gemini_note],
            "piper": [self.voice, self.voice_dl],
        }
        for ws in self._voice_groups.values():
            for w in ws:
                l3.addWidget(w)
        l3.addWidget(self.test_btn)
        l3.addWidget(self.test_status)
        self.provider.currentIndexChanged.connect(lambda _i: self._provider_visibility())
        self._provider_visibility()

        # 4. Whisper
        p4 = QWizardPage()
        p4.setTitle("Konuşma tanıma")
        p4.setSubTitle("Whisper modeli sesli komut için gerekir. 'small' yaklaşık 480 MB. "
                       "Atlarsan ilk F1 basışında indirilir.")
        self.whisper = QComboBox()
        for sz in downloads.WHISPER_SIZES:
            self.whisper.addItem(sz, sz)
        self.whisper.setCurrentIndex(max(0, self.whisper.findData(settings.whisper_model)))
        self.whisper_dl = DownloadRow(self._make_whisper_dl,
                                      lambda: downloads.whisper_ready(models_dir(), self.whisper.currentData()))
        self.whisper.currentIndexChanged.connect(lambda _i: self.whisper_dl.refresh())
        l4 = QVBoxLayout(p4)
        l4.addWidget(self.whisper)
        l4.addWidget(self.whisper_dl)

        # 5. Otomatik başlatma
        p5 = QWizardPage()
        p5.setTitle("Otomatik başlatma")
        p5.setSubTitle("Reis tepside küçük başlar; F1 ile hemen konuşabilirsin.")
        self.autostart = QCheckBox("Windows açılışında başlat")
        self.autostart.setChecked(True)
        l5 = QVBoxLayout(p5)
        l5.addWidget(self.autostart)
        l5.addWidget(_label("Bunu istediğin zaman Ayarlar'dan kapatabilirsin."))

        for p in (p1, p2, p3, p4, p5):
            self.addPage(p)

    def _provider_visibility(self) -> None:
        cur = self.provider.currentData()
        for name, ws in self._voice_groups.items():
            for w in ws:
                w.setVisible(name == cur)

    def _tts_snapshot(self) -> Settings:
        from dataclasses import replace

        return replace(self.settings, tts_provider=self.provider.currentData(),
                       yazgi_voice=self.yazgi_voice.currentData(),
                       edge_voice=self.edge_voice.currentData(), gemini_tts_voice=self.gemini_voice.currentData(),
                       piper_voice=self.voice.currentData())

    def _test_factory(self, s: Settings):
        if s.tts_provider == "gemini":  # henüz kaydedilmemiş anahtarı kullan
            from ..core.tts.gemini_tts import GeminiTTS

            return GeminiTTS(self.gemini.text().strip() or secrets.get_secret(secrets.GEMINI),
                             s.gemini_tts_model, s.gemini_tts_voice)
        from ..core.tts import make_tts

        return make_tts(s)

    def _make_voice_dl(self):
        voice, root = self.voice.currentData(), models_dir()
        return lambda prog, canc: downloads.download_piper_voice(voice, root, prog, canc)

    def _make_whisper_dl(self):
        size, root = self.whisper.currentData(), models_dir()
        return lambda prog, canc: downloads.download_whisper(size, root, prog, canc)

    def done(self, r):
        self.yazgi_dl.cancel_and_wait()
        self.voice_dl.cancel_and_wait()
        self.whisper_dl.cancel_and_wait()
        self.apply()
        super().done(r)

    def apply(self) -> None:
        """Girilenleri ayarlara yazar (iptal/atla durumunda da, boş bırakılanlar değişmez)."""
        s = self.settings
        key = self.gemini.text().strip()
        if key:
            try:
                secrets.set_secret(secrets.GEMINI, key)
            except Exception:
                pass
        name, path = self.proj_name.text().strip(), self.proj_path.text().strip()
        if name and path and name not in s.project_names():
            s.projects.append({"name": name, "path": path})
        if self.daily_path.text().strip():
            s.daily_path = self.daily_path.text().strip()
        if self.schedule_path.text().strip():
            s.schedule_path = self.schedule_path.text().strip()
        s.piper_voice = self.voice.currentData()
        s.tts_provider = self.provider.currentData()
        s.yazgi_voice = self.yazgi_voice.currentData()
        s.edge_voice = self.edge_voice.currentData()
        s.gemini_tts_voice = self.gemini_voice.currentData()
        s.whisper_model = self.whisper.currentData()
        s.autostart = self.autostart.isChecked()
        s.first_run_done = True
