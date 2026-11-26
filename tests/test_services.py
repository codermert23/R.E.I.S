import datetime as dt
import json

import pytest

from reis.config import settings as cfg
from reis.core.services import datefmt, weather
from reis.core.services.cache import TTLCache

GEO = {"results": [{"id": 1, "name": "İstanbul", "latitude": 41.01, "longitude": 28.95, "country_code": "TR"}]}
FORECAST = {"current": {"time": "2026-11-25T12:00", "temperature_2m": 14.2, "relative_humidity_2m": 71,
                        "apparent_temperature": 12.8, "weather_code": 61, "wind_speed_10m": 11.5}}




def test_wmo_mapping():
    assert weather.describe_wmo(0) == ("Açık", "sun")
    assert weather.describe_wmo(61)[1] == "rain"
    assert weather.describe_wmo(95)[1] == "storm"
    assert weather.describe_wmo(999) == ("Bilinmiyor", "cloud")
    assert weather.describe_wmo(None)[0] == "Bilinmiyor"


def test_parse_geocode_and_forecast():
    lat, lon, name = weather.parse_geocode(GEO)
    assert (lat, lon, name) == (41.01, 28.95, "İstanbul")
    w = weather.parse_forecast(FORECAST, name)
    assert w.temp == 14.2 and w.humidity == 71 and w.feels_like == 12.8 and w.wind_kmh == 11.5
    assert w.description == "Hafif yağmur" and w.icon == "rain" and w.city == "İstanbul"


def test_parse_errors():
    with pytest.raises(weather.WeatherError):
        weather.parse_geocode({"generationtime_ms": 0.1})
    with pytest.raises(weather.WeatherError):
        weather.parse_forecast({"current": {}})


class _Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


class _Client:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        return _Resp(GEO if "geocoding" in url else FORECAST)


def test_fetch_weather_sends_only_city_name():
    c = _Client()
    w = weather.fetch_weather("İstanbul", client=c)
    assert w.temp == 14.2
    geo_params = c.calls[0][1]
    assert geo_params["name"] == "İstanbul" and geo_params["count"] == 1 and geo_params["language"] == "tr"
    assert set(c.calls[1][1]) == {"latitude", "longitude", "timezone", "current"}
    with pytest.raises(weather.WeatherError):
        weather.fetch_weather("  ", client=c)


def test_ttl_cache_expiry():
    now = [100.0]
    c = TTLCache(900, clock=lambda: now[0])
    c.set("k", 1)
    assert c.get("k") == 1
    now[0] += 899
    assert c.get("k") == 1
    now[0] += 2
    assert c.get("k") is None


def test_turkish_date_and_midnight():
    assert datefmt.format_date_tr(dt.date(2026, 11, 25)) == "25 Kasım 2026 Çarşamba"
    assert datefmt.format_date_tr(dt.date(2026, 1, 1)) == "1 Ocak 2026 Perşembe"
    assert datefmt.format_clock(dt.datetime(2026, 1, 1, 9, 5, 3)) == "09:05:03"
    assert 0 < datefmt.seconds_until_midnight(dt.datetime(2026, 1, 1, 23, 59, 0)) <= 61


def test_settings_backward_compatible(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hotkey": "F3", "unknown_future_key": 1, "news_feed_url": "x"}), encoding="utf-8")
    s = cfg.load_settings(p)
    assert s.hotkey == "F3" and s.city == "Elazığ" and s.briefing_hotkey == "F2" and s.autostart is True
    s.city = "Ankara"
    cfg.save_settings(s, p)
    assert cfg.load_settings(p).city == "Ankara"
