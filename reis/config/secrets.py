"""API anahtarları: Windows Credential Manager (keyring). JSON'a asla yazılmaz."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SERVICE = "Reis"
GEMINI = "gemini_api_key"
ELEVENLABS = "elevenlabs_api_key"


def get_secret(name: str) -> str:
    try:
        import keyring

        return keyring.get_password(SERVICE, name) or ""
    except Exception:
        log.exception("keyring okunamadı")
        return ""


def set_secret(name: str, value: str) -> None:
    import keyring

    value = (value or "").strip()
    if value:
        keyring.set_password(SERVICE, name, value)
    else:
        try:
            keyring.delete_password(SERVICE, name)
        except Exception:
            pass
