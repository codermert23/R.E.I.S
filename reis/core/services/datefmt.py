"""Türkçe tarih/saat biçimleri."""
from __future__ import annotations

import datetime as dt

from ..documents import TR_DAYS, TR_MONTHS


def format_date_tr(d: dt.date) -> str:
    """25 Kasım 2026 Çarşamba"""
    return f"{d.day} {TR_MONTHS[d.month - 1]} {d.year} {TR_DAYS[d.weekday()]}"


def format_clock(t: dt.datetime) -> str:
    return t.strftime("%H:%M:%S")


def format_hhmm(t: dt.datetime) -> str:
    return t.strftime("%H:%M")


def seconds_until_midnight(now: dt.datetime) -> float:
    nxt = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=1, microsecond=0)
    return (nxt - now).total_seconds()


def format_day_month_tr(d: dt.date) -> str:
    """12 Aralık"""
    return f"{d.day} {TR_MONTHS[d.month - 1]}"
