from __future__ import annotations

from typing import Protocol

import numpy as np


class TTSError(Exception):
    """Kullanıcıya gösterilebilir Türkçe mesaj taşır."""


class TTSProvider(Protocol):
    def synth(self, text: str) -> tuple[np.ndarray, int]:
        """Metni sese çevirir: (int16 mono PCM, örnekleme hızı)."""
        ...
