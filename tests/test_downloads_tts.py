import httpx
import numpy as np
import pytest

from reis.core import downloads
from reis.core.downloads import DownloadCancelled, download_files, piper_voice_urls, whisper_urls
from reis.core.tts.base import TTSError


def test_piper_urls():
    onnx, cfg = piper_voice_urls("tr_TR-dfki-medium")
    base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/tr/tr_TR/dfki/medium/tr_TR-dfki-medium"
    assert onnx == base + ".onnx"
    assert cfg == base + ".onnx.json"
    assert piper_voice_urls("tr_TR-fettah-medium")[0].endswith("/tr/tr_TR/fettah/medium/tr_TR-fettah-medium.onnx")
    with pytest.raises(ValueError):
        piper_voice_urls("bozuk")


def test_whisper_urls():
    urls = dict(whisper_urls("small"))
    assert urls["model.bin"] == "https://huggingface.co/Systran/faster-whisper-small/resolve/main/model.bin"


def _client(files: dict[str, bytes]):
    def handler(req: httpx.Request):
        name = req.url.path.rsplit("/", 1)[-1]
        if name not in files:
            return httpx.Response(404)
        if req.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(files[name]))})
        return httpx.Response(200, content=files[name])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_files_progress_and_skip(tmp_path):
    c = _client({"a.bin": b"x" * 100000, "b.json": b"{}"})
    seen = []
    targets = [(tmp_path / "a.bin", "http://h/a.bin"), (tmp_path / "b.json", "http://h/b.json")]
    download_files(targets, lambda d, t, l: seen.append((d, t)), client=c)
    assert (tmp_path / "a.bin").read_bytes() == b"x" * 100000
    assert not list(tmp_path.glob("*.part"))
    assert seen[-1][0] == seen[-1][1]
    assert all(d <= t for d, t in seen)
    # ikinci çağrı: var olanlar atlanır, ağa gitmez
    download_files(targets, client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))


def test_download_optional_404_and_error(tmp_path):
    c = _client({"config.json": b"{}"})
    download_files([(tmp_path / "config.json", "http://h/config.json"),
                    (tmp_path / "vocabulary.txt", "http://h/vocabulary.txt")], client=c, optional={"vocabulary.txt"})
    assert (tmp_path / "config.json").exists() and not (tmp_path / "vocabulary.txt").exists()
    with pytest.raises(httpx.HTTPStatusError):
        download_files([(tmp_path / "x.bin", "http://h/x.bin")], client=c)


def test_download_cancel(tmp_path):
    c = _client({"a.bin": b"x" * 200000})
    with pytest.raises(DownloadCancelled):
        download_files([(tmp_path / "a.bin", "http://h/a.bin")], cancelled=lambda: True, client=c)
    assert not (tmp_path / "a.bin").exists() and not list(tmp_path.glob("*.part"))


def test_download_piper_voice_writes_both(tmp_path):
    c = _client({"tr_TR-dfki-medium.onnx": b"m" * 10, "tr_TR-dfki-medium.onnx.json": b"{}"})
    downloads.download_piper_voice("tr_TR-dfki-medium", tmp_path, client=c)
    from reis.core.tts.piper_tts import is_installed

    assert is_installed("tr_TR-dfki-medium", tmp_path)


def test_whisper_ready(tmp_path):
    assert not downloads.whisper_ready(tmp_path, "small")
    d = downloads.whisper_dir(tmp_path, "small")
    d.mkdir(parents=True)
    for f in ("config.json", "model.bin", "tokenizer.json"):
        (d / f).write_bytes(b"1")
    assert downloads.whisper_ready(tmp_path, "small")
