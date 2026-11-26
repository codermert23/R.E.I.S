from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .config import settings as cfg
from .core import autostart, winauth
from .core.hotkey import HotkeyManager
from .core.pipeline import Pipeline
from .core.state import StateBus
from .ui.appicon import app_icon
from .ui.docs_window import DocsWindow
from .ui.passwords_window import PasswordsWindow
from .ui.first_run import FirstRunWizard
from .ui.frameless import set_app_user_model_id
from .ui.settings_dialog import SettingsDialog
from .ui.sidebar import Sidebar
from .ui.workers import run_async

log = logging.getLogger("reis")

BRIEFING_MESSAGE = "Reis günaydın, brifing ver"


def _setup_logging():
    handlers = [logging.FileHandler(cfg.config_dir() / "reis.log", encoding="utf-8")]
    if sys.stderr:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _tray_icon() -> QIcon:
    return app_icon()


class ReisApp:
    def __init__(self, app: QApplication, start_tray: bool):
        self.app = app
        self.first_run = not cfg.settings_path().exists()
        self.settings = cfg.load_settings()
        self.bus = StateBus()
        self.pipeline = Pipeline(self.settings, self.bus)
        self.sidebar = Sidebar(self.settings, self.bus)
        self.docs_win = None
        self.pw_win = None
        self.pipeline.briefing_notes_requested.connect(self.open_briefing_notes)
        qss = Path(__file__).parent / "ui" / "theme.qss"
        app.setStyleSheet(qss.read_text(encoding="utf-8"))

        self.sidebar.input_bar.text_submitted.connect(self.pipeline.submit_text)
        self.sidebar.input_bar.settings_clicked.connect(lambda: self.open_settings())
        self.sidebar.collapsed_changed.connect(lambda _c: self.save())
        self.sidebar.input_bar.mic_clicked.connect(self.pipeline.toggle_listening)
        self.sidebar.input_bar.briefing_clicked.connect(self.ask_briefing)
        self.sidebar.docs_requested.connect(self.open_docs)
        self.sidebar.passwords_requested.connect(self.open_passwords)
        self.pipeline.show_requested.connect(lambda items: [self.sidebar.show_item(i) for i in items])

        self.tray = QSystemTrayIcon(_tray_icon(), app)
        menu = QMenu()
        a_toggle = QAction("Göster / Gizle", menu)
        a_toggle.triggered.connect(self.toggle_visible)
        a_brief = QAction("Sabah brifingi", menu)
        a_brief.triggered.connect(self.ask_briefing)
        a_quit = QAction("Çıkış", menu)
        a_quit.triggered.connect(self.quit)
        menu.addAction(a_toggle)
        menu.addAction(a_brief)
        menu.addAction(a_quit)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Reis")
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.toggle_visible())
        self.tray.show()
        self.hotkey = HotkeyManager(app)
        self.hotkey.install(app)
        self.hotkey.pressed.connect(self.pipeline.toggle_listening)
        self.hotkey.warning.connect(self._hotkey_warning)
        self.hotkey.register(self.settings.hotkey)
        self.brief_key = HotkeyManager(app, hotkey_id=0x5E16)
        self.brief_key.install(app)
        self.brief_key.pressed.connect(self.ask_briefing)
        self.brief_key.warning.connect(self._hotkey_warning)
        if self.settings.briefing_hotkey:
            self.brief_key.register(self.settings.briefing_hotkey)
        app.aboutToQuit.connect(self.hotkey.unregister)
        app.aboutToQuit.connect(self.brief_key.unregister)
        app.aboutToQuit.connect(self.save)
        app.aboutToQuit.connect(self.pipeline.shutdown)
        app.aboutToQuit.connect(self._close_wizard)

        if not start_tray:
            self.sidebar.show()
        if self.settings.yazgi_prewarm_on_start:
            QTimer.singleShot(2000, self.pipeline.prewarm)  # açılışı geciktirmesin; model arka planda yüklenir
        self.wizard = None
        if self.first_run:
            QTimer.singleShot(0, self.run_first_run)
        else:
            self.apply_autostart(refresh_only=True)

    def run_first_run(self):
        self.wizard = FirstRunWizard(self.settings)
        self.wizard.finished.connect(self._first_run_done)
        self.wizard.show()
        self.wizard.raise_()
        self.wizard.activateWindow()

    def _first_run_done(self, _r):
        self.settings.first_run_done = True
        self.save()
        self.apply_autostart()
        self.wizard = None

    def apply_autostart(self, refresh_only: bool = False):
        """Ayarı kayıt defterine yansıtır. REIS_DATA_DIR (test/duman) varken kayıt defterine dokunulmaz.
        Açılışta yalnızca açık ayar için komutu tazeler; asla kendiliğinden kapatmaz."""
        if os.environ.get("REIS_DATA_DIR"):
            return
        try:
            if self.settings.autostart:
                cmd = autostart.build_command()
                if autostart.current_command() != cmd:
                    autostart.enable(cmd)
            elif not refresh_only:
                autostart.disable()
        except Exception:
            log.exception("Otomatik başlatma ayarı uygulanamadı")

    def quit(self):
        """app.quit() pencereleri kapatmayı dener; sidebar/sihirbaz bunu engelleyebilir. exit() güvenlidir."""
        self.app.exit(0)

    def _close_wizard(self):
        if self.wizard is not None:
            self.wizard.done(0)

    def _hotkey_warning(self, msg: str):
        log.warning(msg)
        self.sidebar.bus.set_subtitle("⚠ " + msg)
        self.tray.showMessage("Reis", msg, QSystemTrayIcon.Warning, 6000)

    def _set(self, key, value):
        setattr(self.settings, key, value)
        self.save()

    def save(self):
        try:
            cfg.save_settings(self.settings)
        except Exception:
            log.exception("Ayarlar kaydedilemedi")

    def toggle_visible(self):
        if self.sidebar.isVisible():
            self.sidebar.hide()
        else:
            self.sidebar.show()
            self.sidebar.raise_()

    def ask_briefing(self):
        """F2 / ☀: asistana sabah mesajı gider; brifing yanıtını yapay zekâ yönlendirir."""
        self.pipeline.submit_text(BRIEFING_MESSAGE)

    def open_docs(self, focus: str = "", projects: list | None = None):
        if self.docs_win is None:
            self.docs_win = DocsWindow()
        self.docs_win.set_sources(self.settings, projects)
        self.docs_win.focus_tab(focus)
        self.docs_win.open_centered()

    def open_passwords(self):
        """Pencere açılmadan önce Windows PIN/parmak izi/parola doğrulaması istenir (Windows Hello)."""
        if getattr(self, "_pw_busy", False):
            return
        self._pw_busy = True
        self.sidebar.bus.set_subtitle("Windows doğrulaması bekleniyor…")
        run_async(lambda: winauth.authenticate("Reis şifrelerini açmak için kimliğini doğrula"),
                  self._pw_verified, lambda _m: self._pw_verified("unavailable"))

    def _pw_verified(self, result: str):
        self._pw_busy = False
        if result == "denied":
            self.sidebar.bus.set_subtitle("Doğrulama başarısız ya da iptal edildi.")
            return
        if result == "unavailable":
            self.sidebar.bus.set_subtitle("Windows PIN/Hello kurulu değil: yalnızca dosya şifrelemesi koruyor.")
        else:
            self.sidebar.bus.set_subtitle("")
        self._show_passwords()

    def _show_passwords(self):
        if self.pw_win is None:
            self.pw_win = PasswordsWindow(self.settings.passwords_path)
            self.pw_win.path_changed.connect(lambda p: self._set("passwords_path", p))
        self.pw_win.set_path(self.settings.passwords_path)
        self.pw_win.open_centered()

    def open_briefing_notes(self, projects: list) -> None:
        """Brifing konuşması başlarken: projeler tek büyük pencerede, her biri bir sekme."""
        if projects:
            self.open_docs("", projects)

    def open_settings(self, parent=None):
        dlg = SettingsDialog(self.settings, parent or self.sidebar)
        if dlg.exec():
            self.save()
            self.apply_autostart()
            if self.hotkey.current != self.settings.hotkey:
                self.hotkey.register(self.settings.hotkey)
            if self.brief_key.current != self.settings.briefing_hotkey:
                self.brief_key.register(self.settings.briefing_hotkey)
            self.sidebar.refresh_weather()


def selftest() -> int:
    """Paketlenmiş uygulamada tembel içe aktarılan ağır bağımlılıkların yüklendiğini doğrular."""
    results = []
    for mod in ("faster_whisper", "ctranslate2", "piper", "onnxruntime", "sounddevice", "webrtcvad", "keyring",
                "google.genai", "httpx", "pydantic", "numpy"):
        try:
            __import__(mod)
            results.append(f"OK   {mod}")
        except Exception as e:
            results.append(f"FAIL {mod}: {e}")
    try:
        import webrtcvad

        webrtcvad.Vad(2).is_speech(b"\0" * 960, 16000)
        import sounddevice as sd

        results.append(f"OK   portaudio ({len(sd.query_devices())} cihaz)")
        from faster_whisper import WhisperModel  # noqa: F401
        from piper import PiperVoice  # noqa: F401
    except Exception as e:
        results.append(f"FAIL native: {e}")
    text = "\n".join(results)
    (cfg.config_dir() / "selftest.txt").write_text(text, encoding="utf-8")
    print(text)
    return 1 if any(r.startswith("FAIL") for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reis")
    parser.add_argument("--tray", action="store_true", help="Tepside küçük başlat")
    parser.add_argument("--selftest", action="store_true", help="Bağımlılık kontrolü (paket doğrulama)")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()

    set_app_user_model_id("Reis.Assistant.1")  # görev çubuğunda python.exe değil Reis simgesi; doğru gruplama
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Reis")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    _setup_logging()

    lock = QLockFile(str(cfg.config_dir() / "reis.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        log.warning("Reis zaten çalışıyor, ikinci örnek kapatılıyor")
        if not os.environ.get("REIS_AUTOQUIT_MS"):  # duman testinde modal kutu takılı kalır
            QMessageBox.information(None, "Reis", "Reis zaten çalışıyor (tepsiye bak).")
        return 0

    reis = ReisApp(app, start_tray=args.tray)

    quit_ms = os.environ.get("REIS_AUTOQUIT_MS")  # duman testi
    if quit_ms:
        QTimer.singleShot(int(quit_ms), reis.quit)
    code = app.exec()
    lock.unlock()
    return code
