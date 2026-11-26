"""yazgı-tts-1 çalışan süreci. Reis'in kendi ortamında DEĞİL, ayrı runtime venv'inde (torch ile) çalışır.

Bu dosya `reis` paketini içe aktarmaz (runtime venv'de yok). Protokol (stdin/stdout, ikili):
  istek : tek satır JSON  {"id": 1, "text": "...", "voice": "male-01"}\n   (stdin kapanırsa süreç çıkar)
  yanıt : tek satır JSON  {"id": 1, "ok": true, "sr": 24000, "n": <bayt>}\n  ardından n bayt int16 LE PCM
          hata: {"id": 1, "ok": false, "error": "..."}\n
  ilk satır (yüklenince): {"ready": true, "sr": 24000, "voices": [...]}\n
torch/kütüphane çıktıları stdout'a karışmasın diye sys.stdout, stderr'e yönlendirilir.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # yabancı print'ler protokolü bozmasın
    model_dir = sys.argv[1]

    def send(obj: dict, payload: bytes = b"") -> None:
        out.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n" + payload)
        out.flush()

    try:
        import numpy as np
        import torch

        torch.set_num_threads(int(os.environ.get("REIS_YAZGI_THREADS") or max(2, min(6, (os.cpu_count() or 4) // 2))))
        from yazgi_tts import RATE, YazgiTTS

        tts = YazgiTTS(model_dir)
        tts.say("Merhaba.")  # ısınma
    except BaseException as e:  # noqa: BLE001
        send({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return 2
    send({"ready": True, "sr": int(RATE), "voices": list(tts.voices)})

    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline()
        if not line:
            return 0  # ana süreç kapandı
        rid = None
        try:
            req = json.loads(line.decode("utf-8"))
            rid = req.get("id")
            text = str(req.get("text", "")).strip()
            if not text:
                raise ValueError("boş metin")
            audio = tts.say(text, voice=req.get("voice") or "male-01")
            pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype("<i2").tobytes()
            send({"id": rid, "ok": True, "sr": int(RATE), "n": len(pcm)}, pcm)
        except Exception as e:  # noqa: BLE001
            send({"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    sys.exit(main())
