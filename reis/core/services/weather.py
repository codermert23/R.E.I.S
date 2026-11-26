"""Open-Meteo hava durumu (anahtarsız). Servise yalnızca şehir adı gider."""
from __future__ import annotations

from dataclasses import dataclass

from .cache import TTLCache

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 6.0
CACHE_TTL_S = 900.0
# Pano kartı ve sabah brifingi aynı önbelleği paylaşır (anahtar: şehir.casefold()).
SHARED_CACHE = TTLCache(CACHE_TTL_S)

# WMO kodu -> (Türkçe açıklama, simge anahtarı)
_WMO = {
    0: ("Açık", "sun"),
    1: ("Çoğunlukla açık", "sun"),
    2: ("Parçalı bulutlu", "partly"),
    3: ("Kapalı", "cloud"),
    45: ("Sisli", "fog"),
    48: ("Kırağılı sis", "fog"),
    51: ("Hafif çisenti", "rain"),
    53: ("Çisenti", "rain"),
    55: ("Yoğun çisenti", "rain"),
    56: ("Dondurucu çisenti", "rain"),
    57: ("Yoğun dondurucu çisenti", "rain"),
    61: ("Hafif yağmur", "rain"),
    63: ("Yağmur", "rain"),
    65: ("Şiddetli yağmur", "rain"),
    66: ("Dondurucu yağmur", "rain"),
    67: ("Şiddetli dondurucu yağmur", "rain"),
    71: ("Hafif kar", "snow"),
    73: ("Kar yağışı", "snow"),
    75: ("Yoğun kar yağışı", "snow"),
    77: ("Kar taneleri", "snow"),
    80: ("Hafif sağanak", "rain"),
    81: ("Sağanak", "rain"),
    82: ("Şiddetli sağanak", "rain"),
    85: ("Hafif kar sağanağı", "snow"),
    86: ("Yoğun kar sağanağı", "snow"),
    95: ("Gök gürültülü fırtına", "storm"),
    96: ("Dolulu fırtına", "storm"),
    99: ("Şiddetli dolulu fırtına", "storm"),
}


def describe_wmo(code) -> tuple[str, str]:
    try:
        return _WMO[int(code)]
    except (KeyError, TypeError, ValueError):
        return ("Bilinmiyor", "cloud")


@dataclass
class Weather:
    city: str
    temp: float
    feels_like: float
    humidity: float
    wind_kmh: float
    code: int
    description: str
    icon: str


class WeatherError(Exception):
    pass


def parse_geocode(data: dict) -> tuple[float, float, str]:
    res = (data or {}).get("results") or []
    if not res:
        raise WeatherError("Şehir bulunamadı")
    r = res[0]
    return float(r["latitude"]), float(r["longitude"]), str(r.get("name") or "")


def parse_forecast(data: dict, city: str = "") -> Weather:
    try:
        cur = data["current"]
        code = int(cur["weather_code"])
        desc, icon = describe_wmo(code)
        return Weather(
            city=city,
            temp=float(cur["temperature_2m"]),
            feels_like=float(cur["apparent_temperature"]),
            humidity=float(cur["relative_humidity_2m"]),
            wind_kmh=float(cur["wind_speed_10m"]),
            code=code, description=desc, icon=icon,
        )
    except (KeyError, TypeError, ValueError) as e:
        raise WeatherError(f"Beklenmeyen yanıt: {e}") from e


def fetch_weather(city: str, client=None) -> Weather:
    """Bloklayan ağ çağrısı: worker thread'de çalıştırılmalı."""
    import httpx

    city = (city or "").strip()
    if not city:
        raise WeatherError("Şehir ayarlanmadı")
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        g = client.get(GEO_URL, params={"name": city, "count": 1, "language": "tr"})
        g.raise_for_status()
        lat, lon, name = parse_geocode(g.json())
        f = client.get(FORECAST_URL, params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        })
        f.raise_for_status()
        return parse_forecast(f.json(), name or city)
    finally:
        if own:
            client.close()
