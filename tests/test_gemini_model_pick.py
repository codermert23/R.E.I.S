from reis.core.llm.gemini import GeminiProvider, pick_model


def test_prefers_flash_lite_highest_version():
    names = ["models/gemini-2.5-flash", "models/gemini-3.0-flash-lite", "models/gemini-2.5-flash-lite",
             "models/gemini-3.0-pro", "models/gemini-3.0-flash-image", "models/gemini-3.1-flash-lite-preview"]
    assert pick_model(names) == "gemini-3.0-flash-lite"


def test_falls_back_to_flash_then_alias():
    assert pick_model(["models/gemini-2.5-flash", "models/gemini-2.5-pro"]) == "gemini-2.5-flash"
    assert pick_model(["models/gemini-flash-lite-latest", "models/gemini-pro"]) == "gemini-flash-lite-latest"
    assert pick_model(["models/gemini-pro"]) is None


def test_thinking_style_by_model():
    from reis.core.llm.gemini import uses_thinking_level

    assert uses_thinking_level("gemini-3.5-flash-lite")
    assert uses_thinking_level("gemini-flash-lite-latest")
    assert not uses_thinking_level("gemini-2.5-flash-lite")


class _BadArg(Exception):
    code = 400


def test_400_falls_back_to_other_thinking_style_and_remembers():
    calls = []

    class Resp:
        text = "{}"
        usage_metadata = None

    p = GeminiProvider("k", "gemini-2.5-flash-lite")  # 2.x: önce budget=0 denenir

    def fake_generate(user, config):
        tc = config.thinking_config
        kind = "none" if tc is None else ("level" if tc.thinking_level else "budget")
        calls.append(kind)
        if kind == "budget":
            raise _BadArg("INVALID_ARGUMENT")
        return Resp()

    p._generate = fake_generate
    p.complete_json("s", "u", {"type": "object"})
    assert calls == ["budget", "level"]
    calls.clear()
    p.complete_json("s", "u", {"type": "object"})
    assert calls == ["level"]  # çalışan biçim hatırlandı


class _Busy(Exception):
    code = 503


class _Ok:
    text = "{}"
    usage_metadata = None


def _provider(model="gemini-3.5-flash-lite"):
    p = GeminiProvider("k", model)
    p._sleep = lambda s: None
    p._available_models = lambda: ["models/gemini-3.5-flash-lite", "models/gemini-2.5-flash-lite"]
    return p


def test_503_retries_then_succeeds_without_switching_model():
    p = _provider()
    n = {"c": 0}

    def gen(user, config):
        n["c"] += 1
        if n["c"] < 3:
            raise _Busy("UNAVAILABLE")
        return _Ok()

    p._generate = gen
    assert p.complete_json("s", "u", {}).text == "{}"
    assert n["c"] == 3 and p.model == "gemini-3.5-flash-lite"


def test_503_persistent_switches_model_for_this_request_only():
    p = _provider()
    used = []

    def gen(user, config):
        used.append(p.model)
        if p.model == "gemini-3.5-flash-lite":
            raise _Busy("UNAVAILABLE")
        return _Ok()

    p._generate = gen
    assert p.complete_json("s", "u", {}).text == "{}"
    assert used[-1] == "gemini-2.5-flash-lite"
    assert p.model == "gemini-3.5-flash-lite"  # kalıcı değişmez


def test_503_everywhere_gives_friendly_error():
    import pytest

    p = _provider()

    def gen(user, config):
        raise _Busy("UNAVAILABLE")

    p._generate = gen
    with pytest.raises(RuntimeError, match="çok yoğun"):
        p.complete_json("s", "u", {})
    assert p.model == "gemini-3.5-flash-lite"


class _NotFound(Exception):
    code = 404


def test_404_triggers_discovery_and_retry(monkeypatch):
    seen, changed = [], []

    class Resp:
        text = "{}"
        usage_metadata = None

    p = GeminiProvider("k", "eski-model", on_model_changed=changed.append)
    monkeypatch.setattr(p, "_available_models", lambda: ["models/gemini-2.5-flash-lite"])

    def fake_generate(user, config):
        seen.append(p.model)
        if p.model == "eski-model":
            raise _NotFound("404 NOT_FOUND")
        return Resp()

    monkeypatch.setattr(p, "_generate", fake_generate)
    out = p.complete_json("s", "u", {"type": "object"})
    assert out.text == "{}"
    assert seen == ["eski-model", "gemini-2.5-flash-lite"]
    assert changed == ["gemini-2.5-flash-lite"]
