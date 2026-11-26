"""Ayarlar: %APPDATA%\\Reis\\settings.json (platformdirs)."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

log = logging.getLogger(__name__)

APP_NAME = "Reis"


def config_dir() -> Path:
    override = os.environ.get("REIS_DATA_DIR")
    if override:
        p = Path(override) / "config"
    else:
        p = Path(user_config_dir(APP_NAME, appauthor=False, roaming=True))
    p.mkdir(parents=True, exist_ok=True)
    return p


def models_dir() -> Path:
    """Model klasörü. Öncelik: REIS_MODELS_DIR, REIS_DATA_DIR/models, kullanıcı veri klasörü/models."""
    models_override = os.environ.get("REIS_MODELS_DIR")
    override = os.environ.get("REIS_DATA_DIR")
    if models_override:
        p = Path(models_override)
    elif override:
        p = Path(override) / "models"
    else:
        p = Path(user_data_dir(APP_NAME, appauthor=False, roaming=False)) / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def runtime_dir() -> Path:
    """Ayrı Python çalışma ortamları (yazgı: torch). REIS_RUNTIME_DIR ile geçersiz kılınır. Oluşturulmaz."""
    override = os.environ.get("REIS_RUNTIME_DIR")
    if override:
        return Path(override)
    return Path(user_data_dir(APP_NAME, appauthor=False, roaming=False)) / "runtime"


def cache_dir() -> Path:
    override = os.environ.get("REIS_DATA_DIR")
    if override:
        p = Path(override) / "cache" / "tts"
    else:
        p = Path(user_data_dir(APP_NAME, appauthor=False, roaming=False)) / "cache" / "tts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings_path() -> Path:
    return config_dir() / "settings.json"


@dataclass
class Settings:
    # Belgeler
    projects: list = field(default_factory=list)  # [{"name": str, "path": str}]
    default_filename: str = "asistannote.md"
    daily_path: str = ""
    passwords_path: str = ""  # düz metin: her satır "Ad: şifre"
    briefing_projects: list = field(default_factory=list)  # brifingte açılacak proje adları; boş = hepsi
    schedule_path: str = ""
    # LLM
    gemini_model: str = "gemini-2.5-flash-lite"
    # STT
    whisper_model: str = "small"
    whisper_idle_minutes: int = 10
    stt_language: str = "tr"
    # Dinleme
    hotkey: str = "F1"
    briefing_hotkey: str = "F2"  # basınca asistana "günaydın, brifing ver" mesajı gider
    silence_ms: int = 1200
    vad_aggressiveness: int = 2  # 0-3
    max_record_s: int = 30
    no_speech_s: int = 6
    # TTS
    tts_provider: str = "yazgi"  # yazgi | edge | gemini | piper | elevenlabs (hata olursa Edge/Piper'a düşer)
    yazgi_voice: str = "male-01"
    yazgi_prewarm_on_start: bool = False  # uygulama açılırken yazgı motorunu arka planda yükle (~935 MB RAM)
    yazgi_idle_minutes: int = 10  # motor bu kadar dk boşta kalırsa kapanır (RAM geri verilir); 0 = hiç kapanmasın
    yazgi_notice_shown: bool = False  # "yazgı kurulu değil" bildirimi bir kez gösterildi
    edge_voice: str = "tr-TR-EmelNeural"
    gemini_tts_model: str = "gemini-3.8-flash-tts"
    gemini_tts_voice: str = "Kore"
    piper_voice: str = "tr_TR-dfki-medium"
    elevenlabs_voice_id: str = ""  # anahtar Credential Manager'da; burada yalnızca ses kimliği
    elevenlabs_model: str = "eleven_flash_v2_5"  # ucuz (yarı kredi); Türkçe destekler
    tts_cache_enabled: bool = True  # Gemini için disk önbelliği
    # Uygulama
    autostart: bool = True
    first_run_done: bool = False
    collapsed: bool = False
    address: str = ""  # Reis sana nasıl hitap etsin (ör. "patron", "Mert"); boş = hitapsız
    city: str = "Elazığ"  # kenar çubuğundaki hava durumu (servise yalnızca şehir adı gider)

    def project_names(self) -> list[str]:
        return [p["name"] for p in self.projects if p.get("name")]

    def to_dict(self) -> dict:
        return asdict(self)


def load_settings(path: Path | None = None) -> Settings:
    path = path or settings_path()
    s = Settings()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            known = {f.name for f in fields(Settings)}
            for k, v in data.items():
                if k in known:
                    setattr(s, k, v)
    except Exception:
        log.exception("Ayarlar okunamadı, varsayılanlar kullanılıyor")
    return s


def save_settings(s: Settings, path: Path | None = None) -> None:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(s.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
