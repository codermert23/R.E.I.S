from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QSpinBox, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from ..config import secrets
from ..config.settings import Settings, models_dir
from ..core import downloads
from ..core.tts import yazgi_setup, yazgi_provider
from ..core.tts.piper_tts import is_installed as piper_installed
from .download_row import DownloadRow
from .project_dialog import ProjectDialog
from .voice_test import VoiceTester, fill_edge_voices, fill_gemini_voices, fill_provider_combo, fill_yazgi_voices


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        # Sidebar always-on-top olduğu için diyalog arkada kalmasın.
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.settings = settings
        self.setWindowTitle("Reis Ayarları")
        self.setMinimumWidth(540)

        self.gemini_key = QLineEdit(secrets.get_secret(secrets.GEMINI))
        self.gemini_key.setEchoMode(QLineEdit.Password)
        self.gemini_key.setPlaceholderText("aistudio.google.com adresinden alınır")
        self.model = QLineEdit(settings.gemini_model)
        self.address = QLineEdit(settings.address)
        self.address.setPlaceholderText("Örn: patron, Mert, reis (boş = hitapsız)")
        self.filename = QLineEdit(settings.default_filename)
        self.daily = self._path_row(settings.daily_path, folder_ok=True)
        self.schedule = self._path_row(settings.schedule_path, folder_ok=False)
        self.passwords = self._path_row(settings.passwords_path, folder_ok=False, filt="Metin (*.txt);;Tümü (*)")
        self.city = QLineEdit(settings.city)
        self.city.setPlaceholderText("Hava durumu için şehir (yalnızca şehir adı gönderilir)")
        self._projects = [dict(p) for p in settings.projects]
        self.proj_list = QListWidget()
        self.proj_list.setMaximumHeight(90)
        self._reload_projects()
        pbtn = QHBoxLayout()
        for text, fn in (("Ekle", self._proj_add), ("Düzenle", self._proj_edit), ("Sil", self._proj_del)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            pbtn.addWidget(b)
        pbtn.addStretch(1)
        self.proj_box = QWidget()
        pl = QVBoxLayout(self.proj_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addLayout(pbtn)
        pl.addWidget(self.proj_list)
        self.brief_list = QListWidget()
        self.brief_list.setMaximumHeight(90)
        self.brief_list.setToolTip("Hiçbiri işaretli değilse brifingte tüm projeler açılır")
        self._chosen = {n.casefold() for n in (settings.briefing_projects or [])}
        self._fill_brief()
        self.autostart = QCheckBox("Windows açılışında başlat")
        self.autostart.setChecked(settings.autostart)
        self.collapsed = QCheckBox("Daraltılmış başlat")
        self.collapsed.setChecked(settings.collapsed)

        self.tts_provider = QComboBox()
        fill_provider_combo(self.tts_provider)
        self.tts_provider.setCurrentIndex(max(0, self.tts_provider.findData(settings.tts_provider)))
        self.yazgi_voice = QComboBox()
        fill_yazgi_voices(self.yazgi_voice)
        if self.yazgi_voice.findData(settings.yazgi_voice) < 0:
            self.yazgi_voice.addItem(settings.yazgi_voice, settings.yazgi_voice)
        self.yazgi_voice.setCurrentIndex(max(0, self.yazgi_voice.findData(settings.yazgi_voice)))
        self.yazgi_dl = yazgi_row(self)
        self.yazgi_prewarm = QCheckBox("Uygulama açılırken yazgı motorunu yükle (~935 MB RAM)")
        self.yazgi_prewarm.setChecked(settings.yazgi_prewarm_on_start)
        self.yazgi_idle = QSpinBox()
        self.yazgi_idle.setRange(0, 240)
        self.yazgi_idle.setSuffix(" dk (0 = hiç kapanmasın)")
        self.yazgi_idle.setValue(settings.yazgi_idle_minutes)
        self.edge_voice = QComboBox()
        fill_edge_voices(self.edge_voice)
        self.edge_voice.setCurrentIndex(max(0, self.edge_voice.findData(settings.edge_voice)))
        self.gemini_voice = QComboBox()
        fill_gemini_voices(self.gemini_voice)
        if self.gemini_voice.findData(settings.gemini_tts_voice) < 0:
            self.gemini_voice.addItem(settings.gemini_tts_voice, settings.gemini_tts_voice)
        self.gemini_voice.setCurrentIndex(max(0, self.gemini_voice.findData(settings.gemini_tts_voice)))
        self.gemini_tts_model = QLineEdit(settings.gemini_tts_model)
        self.eleven_key = QLineEdit(secrets.get_secret(secrets.ELEVENLABS))
        self.eleven_key.setEchoMode(QLineEdit.Password)
        self.eleven_key.setPlaceholderText("elevenlabs.io > API anahtarı")
        self.eleven_voice = QLineEdit(settings.elevenlabs_voice_id)
        self.eleven_voice.setPlaceholderText("Voice ID (ses kimliği)")
        self.eleven_model = QComboBox()
        from ..core.tts.elevenlabs_api import MODELS

        for mid, (label, _m) in MODELS.items():
            self.eleven_model.addItem(label, mid)
        if self.eleven_model.findData(settings.elevenlabs_model) < 0:
            self.eleven_model.addItem(settings.elevenlabs_model, settings.elevenlabs_model)
        self.eleven_model.setCurrentIndex(max(0, self.eleven_model.findData(settings.elevenlabs_model)))
        self.test_btn = QPushButton("Sesi dene")
        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        self.test_status.setObjectName("PathLabel")
        self.voice_tester = VoiceTester(self.test_btn, self._tts_snapshot, self.test_status,
                                        tts_factory=self._test_factory)
        self.piper_voice = QComboBox()
        for v in downloads.PIPER_VOICES:
            self.piper_voice.addItem(v, v)
        if settings.piper_voice not in downloads.PIPER_VOICES:
            self.piper_voice.addItem(settings.piper_voice, settings.piper_voice)
        self.piper_voice.setCurrentIndex(max(0, self.piper_voice.findData(settings.piper_voice)))
        self.piper_dl = DownloadRow(self._make_piper_download, lambda: piper_installed(self._voice(), models_dir()))
        self.piper_voice.currentIndexChanged.connect(lambda _i: self.piper_dl.refresh())
        self.tts_cache = QCheckBox("Gemini seslendirmelerini diskte önbelleğe al")
        self.tts_cache.setChecked(settings.tts_cache_enabled)
        self.tts_provider.currentIndexChanged.connect(self._tts_visibility)

        self.hotkey = QLineEdit(settings.hotkey)
        self.hotkey.setPlaceholderText("F1, Ctrl+Alt+R …")
        self.brief_key = QLineEdit(settings.briefing_hotkey)
        self.brief_key.setPlaceholderText("F2 (boş = kapalı)")
        self.whisper = QComboBox()
        for sz in downloads.WHISPER_SIZES:
            self.whisper.addItem(sz, sz)
        self.whisper.setCurrentIndex(max(0, self.whisper.findData(settings.whisper_model)))
        self.whisper_dl = DownloadRow(self._make_whisper_download,
                                      lambda: downloads.whisper_ready(models_dir(), self.whisper.currentData()))
        self.whisper.currentIndexChanged.connect(lambda _i: self.whisper_dl.refresh())

        general = QFormLayout()
        general.addRow("Gemini API anahtarı", self.gemini_key)
        general.addRow("Gemini modeli", self.model)
        general.addRow("Hitap (sana nasıl seslensin)", self.address)
        general.addRow("Varsayılan rapor dosyası", self.filename)
        general.addRow("Günlük plan yolu", self.daily[0])
        general.addRow("Program dosyası", self.schedule[0])
        general.addRow("Şifre dosyası (.txt)", self.passwords[0])
        general.addRow("Dinleme kısayolu", self.hotkey)
        general.addRow("Brifing kısayolu", self.brief_key)
        general.addRow("Whisper modeli", self.whisper)
        general.addRow("", self.whisper_dl)
        general.addRow("", self.collapsed)
        general.addRow("", self.autostart)
        pano = QFormLayout()
        pano.addRow("Şehir (hava durumu)", self.city)
        pano.addRow("Projeler", self.proj_box)
        pano.addRow("Brifingte açılacak projeler", self.brief_list)

        # Ses sekmesi: sağlayıcıya özgü alanlar yığında; yalnızca seçilen sağlayıcının sayfası görünür.
        self.tts_stack = QStackedWidget()
        self._tts_pages = {}

        def page(name: str, build) -> None:
            w = QWidget()
            f = QFormLayout(w)
            f.setContentsMargins(0, 0, 0, 0)
            build(f)
            self._tts_pages[name] = self.tts_stack.addWidget(w)

        page("yazgi", lambda f: (f.addRow("Yazgı sesi", self.yazgi_voice), f.addRow("Ses motoru", self.yazgi_dl),
                                 f.addRow("", self.yazgi_prewarm), f.addRow("Boşta kapanma", self.yazgi_idle)))
        page("edge", lambda f: f.addRow("Edge sesi", self.edge_voice))
        page("gemini", lambda f: (f.addRow("Gemini ses adı", self.gemini_voice),
                                  f.addRow("Gemini TTS modeli", self.gemini_tts_model)))
        page("elevenlabs", lambda f: (f.addRow("API anahtarı", self.eleven_key), f.addRow("Ses kimliği", self.eleven_voice),
                                      f.addRow("Model", self.eleven_model)))
        page("piper", lambda f: (f.addRow("Piper sesi", self.piper_voice), f.addRow("", self.piper_dl)))
        ses = QVBoxLayout()
        sf = QFormLayout()
        sf.addRow("Ses sağlayıcısı", self.tts_provider)
        ses.addLayout(sf)
        ses.addWidget(self.tts_stack)
        ses.addWidget(self.tts_cache)
        row = QHBoxLayout()
        row.addWidget(self.test_btn)
        row.addStretch(1)
        ses.addLayout(row)
        ses.addWidget(self.test_status)
        ses.addStretch(1)
        self._tts_visibility()

        self.tabs = QTabWidget()
        for title, lay in (("Genel", general), ("Notlar", pano), ("Ses", ses)):
            w = QWidget()
            w.setLayout(lay)
            self.tabs.addTab(w, title)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addWidget(bb)

    def _voice(self) -> str:
        return self.piper_voice.currentData() or self.settings.piper_voice

    def _make_piper_download(self):
        voice = self._voice()
        root = models_dir()
        return lambda prog, canc: downloads.download_piper_voice(voice, root, prog, canc)

    def _make_whisper_download(self):
        size = self.whisper.currentData()
        root = models_dir()
        return lambda prog, canc: downloads.download_whisper(size, root, prog, canc)

    def _tts_visibility(self):
        self.tts_stack.setCurrentIndex(self._tts_pages.get(self.tts_provider.currentData(), 0))

    def _tts_snapshot(self) -> Settings:
        """Henüz kaydedilmemiş seçimlerle geçici ayarlar (Sesi dene için)."""
        from dataclasses import replace

        return replace(
            self.settings, tts_provider=self.tts_provider.currentData(), yazgi_voice=self.yazgi_voice.currentData(),
            edge_voice=self.edge_voice.currentData(),
            gemini_tts_voice=self.gemini_voice.currentData(),
            gemini_tts_model=self.gemini_tts_model.text().strip() or self.settings.gemini_tts_model,
            piper_voice=self._voice(), elevenlabs_voice_id=self.eleven_voice.text().strip(),
            elevenlabs_model=self.eleven_model.currentData())

    def _test_factory(self, s: Settings):
        """Yazılmış ama kaydedilmemiş anahtarlarla sağlayıcı kur (yedek Piper'a düşmeden)."""
        if s.tts_provider == "gemini":
            from ..core.tts.gemini_tts import GeminiTTS

            return GeminiTTS(self.gemini_key.text().strip(), s.gemini_tts_model, s.gemini_tts_voice)
        if s.tts_provider == "elevenlabs":  # kaydedilmemiş anahtarla dene; hata sessizce yedeğe düşmesin
            from ..core.tts.elevenlabs_tts import ElevenLabsTTS

            return ElevenLabsTTS(self.eleven_key.text().strip(), s.elevenlabs_voice_id, s.elevenlabs_model)
        from ..core.tts import make_tts

        return make_tts(s)
    def done(self, r):
        self.yazgi_dl.cancel_and_wait()
        self.piper_dl.cancel_and_wait()
        self.whisper_dl.cancel_and_wait()
        super().done(r)

    # ---- proje listesi
    def _reload_projects(self):
        self.proj_list.clear()
        for p in self._projects:
            self.proj_list.addItem(p["name"])

    def _fill_brief(self):
        self.brief_list.clear()
        for p in self._projects:
            it = QListWidgetItem(p["name"])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if p["name"].casefold() in self._chosen else Qt.Unchecked)
            self.brief_list.addItem(it)

    def _proj_changed(self):
        self._chosen = {self.brief_list.item(i).text().casefold() for i in range(self.brief_list.count())
                        if self.brief_list.item(i).checkState() == Qt.Checked}
        self._reload_projects()
        self._fill_brief()

    def _proj_add(self):
        dlg = ProjectDialog(parent=self)
        if dlg.exec() == QDialog.Accepted:
            name, path = dlg.values()
            if not name or not path:
                return
            if any(p["name"].casefold() == name.casefold() for p in self._projects):
                QMessageBox.warning(self, "Reis", "Bu adda bir proje zaten var.")
                return
            self._projects.append({"name": name, "path": path})
            self._proj_changed()

    def _proj_edit(self):
        i = self.proj_list.currentRow()
        if i < 0:
            return
        p = self._projects[i]
        dlg = ProjectDialog(p["name"], p["path"], self)
        if dlg.exec() == QDialog.Accepted:
            name, path = dlg.values()
            if name and path:
                p["name"], p["path"] = name, path
                self._proj_changed()

    def _proj_del(self):
        i = self.proj_list.currentRow()
        if i >= 0:
            del self._projects[i]
            self._proj_changed()

    def _path_row(self, value: str, folder_ok: bool, filt: str = ""):
        edit = QLineEdit(value)
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        b = QPushButton("Dosya")
        b.clicked.connect(lambda: self._pick(edit, False, filt))
        row.addWidget(b)
        if folder_ok:
            b2 = QPushButton("Klasör")
            b2.clicked.connect(lambda: self._pick(edit, True))
            row.addWidget(b2)
        w = QWidget()
        row.setContentsMargins(0, 0, 0, 0)
        w.setLayout(row)
        return w, edit

    def _pick(self, edit: QLineEdit, folder: bool, filt: str = ""):
        if folder:
            p = QFileDialog.getExistingDirectory(self, "Klasör seç", edit.text())
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Dosya seç", edit.text(),
                                               filt or "Markdown (*.md *.markdown *.txt);;Tümü (*)")
        if p:
            edit.setText(p)

    def _save(self):
        s = self.settings
        s.gemini_model = self.model.text().strip() or "gemini-2.5-flash-lite"
        s.default_filename = self.filename.text().strip() or "asistannote.md"
        s.address = self.address.text().strip()
        s.daily_path = self.daily[1].text().strip()
        s.schedule_path = self.schedule[1].text().strip()
        s.passwords_path = self.passwords[1].text().strip()
        s.city = self.city.text().strip() or "Elazığ"
        s.projects = [dict(p) for p in self._projects]
        s.briefing_projects = [self.brief_list.item(i).text() for i in range(self.brief_list.count())
                               if self.brief_list.item(i).checkState() == Qt.Checked]
        s.collapsed = self.collapsed.isChecked()
        s.autostart = self.autostart.isChecked()
        from ..core.hotkey import HotkeyError, parse_hotkey

        try:
            parse_hotkey(self.hotkey.text())
        except HotkeyError as e:
            QMessageBox.warning(self, "Reis", f"Kısayol geçersiz: {e}")
            return
        s.hotkey = self.hotkey.text().strip()
        bk = self.brief_key.text().strip()
        if bk:
            try:
                parse_hotkey(bk)
            except HotkeyError as e:
                QMessageBox.warning(self, "Reis", f"Brifing kısayolu geçersiz: {e}")
                return
        s.briefing_hotkey = bk
        s.whisper_model = self.whisper.currentData()
        s.tts_provider = self.tts_provider.currentData()
        s.piper_voice = self._voice()
        s.yazgi_voice = self.yazgi_voice.currentData()
        s.yazgi_prewarm_on_start = self.yazgi_prewarm.isChecked()
        s.yazgi_idle_minutes = self.yazgi_idle.value()
        s.edge_voice = self.edge_voice.currentData()
        s.gemini_tts_voice = self.gemini_voice.currentData()
        s.gemini_tts_model = self.gemini_tts_model.text().strip() or "gemini-3.8-flash-tts"
        s.elevenlabs_voice_id = self.eleven_voice.text().strip()
        s.elevenlabs_model = self.eleven_model.currentData()
        s.tts_cache_enabled = self.tts_cache.isChecked()
        try:
            secrets.set_secret(secrets.GEMINI, self.gemini_key.text())
            secrets.set_secret(secrets.ELEVENLABS, self.eleven_key.text())
        except Exception as e:
            QMessageBox.warning(self, "Reis", f"Anahtar kaydedilemedi: {e}")
            return
        self.accept()


def yazgi_row(parent) -> DownloadRow:
    """'Ses motorunu kur' satırı (ayarlar ve ilk kurulum sihirbazı ortak). Kullanıcı tıklayınca, boyut
    uyarısı onaylanırsa çalışır; paketlenmiş exe'de kurulum yerine açıklama gösterilir."""
    def make():
        msg = yazgi_setup.blocker()
        if msg:
            raise RuntimeError(msg)
        return lambda prog, canc: yazgi_setup.run_setup(prog, canc)

    def confirm() -> bool:
        if yazgi_setup.blocker():
            return True  # make() açıklamayı gösterir
        r = QMessageBox.question(parent, "Ses motorunu kur", yazgi_setup.SIZE_WARNING + "\n\nKurulum başlasın mı?")
        return r == QMessageBox.Yes

    return DownloadRow(make, yazgi_provider.is_installed, parent, install_label="Ses motorunu kur", confirm=confirm)
