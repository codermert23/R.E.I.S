"""Orkestrasyon: (metin | ses) -> router -> göster -> TTS. Ağır işler worker thread'lerde."""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..config import secrets
from ..config.settings import Settings, models_dir
from . import briefing, documents, downloads
from .audio import Player
from .recorder import NO_SPEECH, OK, Recorder, VadParams
from .router import Router, RouterError, RouteResult, ShowItem
from .state import AssistantState, StateBus
from .tts.chunker import split_chunks
from .tts.streaming import SpeechStream, run_speech
from .tts.textprep import prepare_speech

log = logging.getLogger(__name__)

IDLE_CHECK_MS = 60_000


class Task(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.succeeded.emit(self._fn())
        except (RouterError, ValueError) as e:
            self.failed.emit(str(e))
        except Exception as e:
            log.exception("Görev hatası")
            msg = str(e) if e.__class__.__module__.startswith("reis.") else f"{type(e).__name__}: {e}"
            self.failed.emit(msg)


class Pipeline(QObject):
    show_requested = Signal(list)  # list[ShowItem]
    user_said = Signal(str)  # yazılan ya da sesle söylenen (sohbet geçmişi için)
    reis_said = Signal(str)  # Reis'in kısa konuşması
    tts_notice = Signal(str)  # yedek sese geçildi gibi tek seferlik küçük bildirimler
    briefing_notes_requested = Signal(list)  # konuşma başlarken açılacak projeler [{name, path}]
    _dispatch = Signal(object)  # worker thread -> ana thread köprüsü

    def __init__(self, settings: Settings, bus: StateBus, parent=None, *, router_factory=None,
                 tts_factory=None, player=None, stt=None, recorder_factory=None, whisper_downloader=None,
                 prewarmer=None, idle_closer=None):
        super().__init__(parent)
        self.settings = settings
        self.bus = bus
        self._gen = 0
        self._tasks: set[Task] = set()
        self._router_factory = router_factory or self._make_router
        self._tts_factory = tts_factory
        self._tts_cache: tuple | None = None  # (anahtar, sağlayıcı)
        self.player = player or Player()
        self._stt = stt
        self._recorder_factory = recorder_factory or self._make_recorder
        self._whisper_downloader = whisper_downloader or downloads.download_whisper
        self._recorder = None
        self._downloading = False
        self._speech_cancel: threading.Event | None = None  # süren konuşmanın iptal bayrağı
        # yazgı ön ısıtma / boşta kapatma (enjekte edilebilir; sahte TTS kullanılan testlerde gerçek süreç açılmaz)
        real = tts_factory is None
        self._prewarmer = prewarmer if prewarmer is not None else (self._default_prewarm if real else None)
        self._idle_closer = idle_closer if idle_closer is not None else (self._default_idle_close if real else None)
        self._idle_timer: QTimer | None = None
        self._dispatch.connect(self._run_in_main)

    def _run_in_main(self, fn) -> None:
        try:
            fn()
        except Exception:
            log.exception("Pipeline geri çağrısı hatası")
            self.bus.error("Beklenmeyen hata (reis.log)")

    def _make_router(self) -> Router:
        provider = None
        key = secrets.get_secret(secrets.GEMINI)
        if key:
            from .llm.gemini import GeminiProvider

            def _remember(model: str) -> None:
                # Eski model adı artık yoksa bulunan geçerli adı kalıcı yap.
                from ..config.settings import save_settings

                self.settings.gemini_model = model
                try:
                    save_settings(self.settings)
                except Exception:
                    log.exception("Model adı kaydedilemedi")

            provider = GeminiProvider(key, self.settings.gemini_model, on_model_changed=_remember)
        s = self.settings
        projects = {p["name"]: p["path"] for p in s.projects if p.get("name")}
        return Router(
            provider,
            project_names=lambda: list(projects),
            project_summary=lambda n: documents.project_summary(projects.get(n, ""), s.default_filename),
            address=lambda: s.address,
        )

    def _get_tts(self):
        from .tts import make_tts, tts_key

        key = tts_key(self.settings)
        if self._tts_cache is None or self._tts_cache[0] != key:
            factory = self._tts_factory or (lambda st: make_tts(st, notify=self._notify_tts))
            self._tts_cache = (key, factory(self.settings))
        return self._tts_cache[1]

    def _notify_tts(self, message: str) -> None:
        self._dispatch.emit(lambda: self.tts_notice.emit(message))  # worker thread -> ana thread

    def _run(self, fn, ok, fail):
        t = Task(fn, self)
        self._tasks.add(t)
        t.succeeded.connect(lambda r: self._dispatch.emit(lambda: ok(r)))
        t.failed.connect(lambda m: self._dispatch.emit(lambda: fail(m)))
        t.finished.connect(lambda: self._dispatch.emit(lambda: (self._tasks.discard(t), t.deleteLater())))
        t.start()

    @property
    def stt(self):
        if self._stt is None:
            from .stt import SpeechToText

            self._stt = SpeechToText(self.settings, self)
        return self._stt

    def interrupt(self) -> int:
        """Süren konuşmayı/kaydı/işi iptal eder; yeni nesil numarası döner."""
        self._gen += 1
        if self._speech_cancel is not None:
            self._speech_cancel.set()  # bekleyen parça üretimi de iptal; eski parça asla çalınmaz
            self._speech_cancel = None
        self.player.stop()
        if self._recorder is not None:
            self._recorder.cancel()
            self._recorder = None
        return self._gen

    def shutdown(self) -> None:
        self.interrupt()
        for t in list(self._tasks):
            t.wait(3000)
        try:
            from .tts import yazgi_provider

            yazgi_provider.shutdown_all()  # torch alt sürecini kapat (yetim süreç kalmasın)
        except Exception:
            log.exception("yazgı worker kapatılamadı")

    # ---- yazgı ön ısıtma / boşta kapatma
    def prewarm(self) -> None:
        """Kullanıcı etkileşime başlayınca (F1, mikrofon, brifing, metin) TTS motorunu arka planda yükler."""
        if self._prewarmer is None:
            return
        try:
            if self._prewarmer(self.settings):
                self._arm_idle_timer()
        except Exception:
            log.exception("TTS ön ısıtma başlatılamadı")

    @staticmethod
    def _default_prewarm(settings) -> bool:
        from .tts import yazgi_provider

        return yazgi_provider.prewarm_background(settings.tts_provider, settings.yazgi_voice)

    @staticmethod
    def _default_idle_close(minutes: int) -> bool:
        """True: worker hâlâ canlı (zamanlayıcı sürsün)."""
        from .tts import yazgi_provider

        return yazgi_provider.close_idle_workers(minutes * 60.0)

    def _arm_idle_timer(self) -> None:
        if self._idle_closer is None or self.settings.yazgi_idle_minutes <= 0:
            return
        if self._idle_timer is None:
            self._idle_timer = QTimer(self)
            self._idle_timer.setInterval(IDLE_CHECK_MS)
            self._idle_timer.timeout.connect(self.check_idle)
        if not self._idle_timer.isActive():
            self._idle_timer.start()

    def check_idle(self) -> None:
        """Worker `yazgi_idle_minutes` dakikadır boştaysa kapatır (~935 MB geri verilir). Canlı worker yoksa
        zamanlayıcı durur (tepsideyken hiçbir şey uyanmaz)."""
        minutes = self.settings.yazgi_idle_minutes
        alive = False
        if self._idle_closer is not None and minutes > 0:
            try:
                alive = bool(self._idle_closer(minutes))
            except Exception:
                log.exception("yazgı boşta kapatma hatası")
        if not alive and self._idle_timer is not None:
            self._idle_timer.stop()

    # ---- ses girişi (F1 / mikrofon butonu)
    def _make_recorder(self, on_result, on_error):
        s = self.settings
        params = VadParams(silence_ms=s.silence_ms, max_s=s.max_record_s, no_speech_s=s.no_speech_s)
        return Recorder(params, s.vad_aggressiveness, on_result, on_error, on_start=self.player.beep)

    def toggle_listening(self) -> None:
        """IDLE/ERROR -> dinle; LISTENING -> durdur ve işle; SPEAKING/THINKING -> kes ve dinle."""
        if self._downloading:
            return
        if self.bus.state is AssistantState.LISTENING and self._recorder is not None:
            self._recorder.stop_now()
            return
        self.start_listening()

    def start_listening(self) -> None:
        gen = self.interrupt()
        if self._downloading:
            return
        self.prewarm()  # kayıt/STT sürerken TTS motoru yüklensin: ilk cevapta soğuk başlangıç olmasın
        if not self.stt.model_ready():
            self._download_whisper_then_listen(gen)
            return
        self._begin_recording(gen)

    def _begin_recording(self, gen: int) -> None:
        def on_result(status, audio):
            self._dispatch.emit(lambda: gen == self._gen and self._on_recorded(status, audio, gen))

        def on_error(msg):
            self._dispatch.emit(lambda: gen == self._gen and self._on_record_error(msg))

        rec = self._recorder_factory(on_result, on_error)
        self._recorder = rec
        self._preload_stt()  # kayıt sürerken Whisper yüklensin
        self.bus.set_subtitle("Dinliyorum…")
        self.bus.set_state(AssistantState.LISTENING)
        rec.start()

    def _preload_stt(self) -> None:
        preload = getattr(self._stt, "preload", None)  # _stt yoksa (henüz oluşturulmadıysa) tembel property'yi tetikleme
        if preload is None and self._stt is None and self._prewarmer is not None:
            preload = getattr(self.stt, "preload", None)
        if preload is None:
            return
        threading.Thread(target=self._safe_preload, args=(preload,), daemon=True, name="stt-preload").start()

    @staticmethod
    def _safe_preload(preload) -> None:
        try:
            preload()
        except Exception:
            log.debug("Whisper ön yükleme atlandı", exc_info=True)

    def _on_record_error(self, msg: str) -> None:
        self._recorder = None
        self.bus.error(msg)

    def _on_recorded(self, status: str, audio, gen: int) -> None:
        self._recorder = None
        if status == NO_SPEECH:
            self.bus.set_subtitle("Ses algılanmadı.")
            self.bus.set_state(AssistantState.IDLE)
            return
        if status != OK:
            self.bus.set_state(AssistantState.IDLE)
            return
        self.bus.set_subtitle("Yazıya çeviriyorum…")
        self.bus.set_state(AssistantState.THINKING)
        names = self.settings.project_names()
        stt = self.stt
        self._run(
            lambda: stt.transcribe(audio, names),
            lambda text: gen == self._gen and self._on_transcript(text, gen),
            lambda msg: gen == self._gen and self.bus.error(f"Ses tanıma hatası: {msg}"),
        )

    def _on_transcript(self, text: str, gen: int) -> None:
        text = (text or "").strip()
        if not text:
            self.bus.set_subtitle("Seni anlayamadım.")
            self.bus.set_state(AssistantState.IDLE)
            return
        self._route(text, gen)  # metin kutusuyla aynı akış

    def _download_whisper_then_listen(self, gen: int) -> None:
        size = self.settings.whisper_model
        self._downloading = True
        self.bus.set_state(AssistantState.THINKING)
        self.bus.set_subtitle(f"Whisper '{size}' modeli indiriliyor… %0")

        def prog(done, total, _label):
            pct = int(done * 100 / max(total, 1))
            self._dispatch.emit(lambda: self._downloading and self.bus.set_subtitle(
                f"Whisper '{size}' modeli indiriliyor… %{pct}"))

        def finish(fn):
            def inner(arg):
                self._downloading = False
                fn(arg)
            return inner

        self._run(
            lambda: self._whisper_downloader(size, models_dir(), prog),
            finish(lambda _r: gen == self._gen and self._begin_recording(gen)),
            finish(lambda msg: self.bus.error(f"Whisper indirilemedi: {msg}")),
        )

    # ---- giriş noktaları
    def submit_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        gen = self.interrupt()
        self.prewarm()
        self._route(text, gen)

    def _route(self, text: str, gen: int) -> None:
        self.user_said.emit(text)
        self.bus.set_subtitle(f"» {text}")
        self.bus.set_state(AssistantState.THINKING)
        try:
            router = self._router_factory()
        except Exception as e:
            self.bus.error(str(e))
            return
        self._run(
            lambda: router.route(text),
            lambda res: gen == self._gen and self._after_route(res, gen),
            lambda msg: gen == self._gen and self._route_failed(msg),
        )

    def _route_failed(self, msg: str) -> None:
        self.bus.online_changed.emit(False)
        self.bus.error(msg)

    # ---- sabah brifingi (konuşma yerel şablonla üretilir: 0 LLM çağrısı)
    def start_briefing(self) -> None:
        """Dashboard/tepsi düğmesi: komut yazmadan brifing."""
        gen = self.interrupt()
        self.prewarm()
        self.user_said.emit("Sabah brifingi")
        self._begin_briefing(gen)

    def _begin_briefing(self, gen: int) -> None:
        self.bus.set_subtitle("Brifing hazırlanıyor…")
        self.bus.set_state(AssistantState.THINKING)
        s = self.settings
        self._run(
            lambda: briefing.gather(s),
            lambda b: gen == self._gen and self._after_briefing(b, gen),
            lambda msg: gen == self._gen and self.bus.error(f"Brifing hatası: {msg}"),
        )

    def _after_briefing(self, b, gen: int) -> None:
        self.show_requested.emit([ShowItem(type="daily_plan")])

        def open_notes():
            if b.open_notes:
                self.briefing_notes_requested.emit(briefing.briefing_projects(self.settings))

        self._speak(b.speech, gen, on_speaking=open_notes)

    def _after_route(self, res: RouteResult, gen: int) -> None:
        self.bus.online_changed.emit(True)
        if res.action == "briefing":
            self._begin_briefing(gen)
            return
        if res.show:
            self.show_requested.emit(res.show)
        self._speak(res.speech, gen)

    def _speak(self, speech: str, gen: int, on_speaking=None) -> None:
        speech = (speech or "").strip()
        if speech:
            self.reis_said.emit(speech)
        self.bus.set_subtitle(speech)
        if not speech:
            self.bus.set_state(AssistantState.IDLE)
            if on_speaking:
                on_speaking()
            return
        # İlk ses çıkana kadar DÜŞÜNÜYOR kalır; KONUŞUYOR ve (brifingte) notların açılması ilk ses başlarken olur.
        cancel = threading.Event()
        self._speech_cancel = cancel
        started = {"v": False}

        def first_audio():
            def apply():
                if gen != self._gen or started["v"]:
                    return
                started["v"] = True
                self.bus.set_state(AssistantState.SPEAKING)
                if on_speaking:
                    on_speaking()

            self._dispatch.emit(apply)

        def speak():
            tts = self._get_tts()
            texts = split_chunks(prepare_speech(speech))  # altyazı özgün metinle kalır
            if not texts:
                return False
            stream = SpeechStream(tts, texts, cancel).start()
            try:
                return run_speech(self.player, stream, first_audio)
            finally:
                stream.close()

        def done(_r):
            if gen == self._gen:
                self.bus.set_state(AssistantState.IDLE)

        def failed(msg):
            if gen != self._gen:
                return
            if not started["v"]:
                started["v"] = True
                if on_speaking:
                    on_speaking()  # TTS hatasında da belgeler (brifing notları) ekranda açılsın
            self.bus.error(f"Seslendirme hatası: {msg}")

        # TTS hatasında belgeler ekranda kalır; yalnızca durum ERROR olur.
        self._run(speak, done, failed)
