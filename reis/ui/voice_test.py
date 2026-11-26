"""'Sesi dene' düğmesi: sabit Türkçe cümleyi worker thread'de sentezler ve çalar."""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QPushButton

from ..config.settings import Settings

log = logging.getLogger(__name__)

TEST_SENTENCE = "Merhaba, ben Reis. Bugün sana nasıl yardımcı olabilirim?"

YAZGI_LABEL = "Yazgı (çevrimdışı, ücretsiz, önerilen)"
EDGE_LABEL = "Edge TTS (çevrimiçi, ücretsiz)"
GEMINI_LABEL = "Gemini TTS (ücretsiz, Gemini anahtarıyla)"
PIPER_LABEL = "Piper (çevrimdışı)"
ELEVEN_LABEL = "ElevenLabs (çevrimiçi, kendi anahtarınla)"


def fill_provider_combo(combo, eleven: bool = True) -> None:
    items = [(YAZGI_LABEL, "yazgi"), (EDGE_LABEL, "edge"), (GEMINI_LABEL, "gemini"), (PIPER_LABEL, "piper")]
    if eleven:  # anahtar alanı yalnızca Ayarlar penceresinde var
        items.append((ELEVEN_LABEL, "elevenlabs"))
    for label, key in items:
        combo.addItem(label, key)


def fill_yazgi_voices(combo) -> None:
    from ..core.tts.yazgi_provider import VOICES

    for k, v in VOICES.items():
        combo.addItem(v, k)


def fill_edge_voices(combo) -> None:
    from ..core.tts.edge import VOICES

    for k, v in VOICES.items():
        combo.addItem(v, k)


def fill_gemini_voices(combo) -> None:
    from ..core.tts.gemini_tts import VOICES

    for v in VOICES:
        combo.addItem(v, v)


class VoiceTester(QObject):
    done = Signal(str)  # boş = başarılı, aksi halde hata mesajı

    def __init__(self, button: QPushButton, get_settings, status=None, tts_factory=None, player=None, parent=None):
        super().__init__(parent or button)
        self.button = button
        self._get_settings = get_settings
        self._tts_factory = tts_factory
        self._player = player
        self.status = status  # isteğe bağlı QLabel
        self._busy = False
        self.done.connect(self._finished)
        button.clicked.connect(self.start)

    def start(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.button.setEnabled(False)
        self.button.setText("Üretiliyor…")
        if self.status is not None:
            self.status.setText("")
        s: Settings = self._get_settings()
        threading.Thread(target=self._work, args=(s,), daemon=True).start()

    def _work(self, s: Settings) -> None:
        try:
            if self._tts_factory is not None:
                tts = self._tts_factory(s)
            else:
                from ..core.tts import make_tts

                tts = make_tts(s)
            pcm, sr = tts.synth(TEST_SENTENCE)
            player = self._player
            if player is None:
                from ..core.audio import Player

                player = Player()
            player.play(pcm, sr)
            self.done.emit("")
        except Exception as e:
            log.exception("Ses denemesi başarısız")
            self.done.emit(str(e) or type(e).__name__)

    def _finished(self, err: str) -> None:
        self._busy = False
        self.button.setEnabled(True)
        self.button.setText("Sesi dene")
        if self.status is not None:
            self.status.setText(err)
