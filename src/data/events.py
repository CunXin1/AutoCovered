"""财报/除息日历 — yfinance 尽力而为 + 本地缓存(20 小时 TTL)。

yfinance 是非官方接口,可能随时抖动;因此:
- 全部 try/except,失败时保留旧缓存或返回空(EVENT_RISK 规则少触发,不误报)
- 晨报 prompt 会让 Claude 用 WebSearch 交叉验证财报日期
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from src.config import STATE_DIR
from src.models import EventDates, parse_date

log = logging.getLogger(__name__)

CACHE_PATH = STATE_DIR / "events_cache.json"
TTL_HOURS = 20


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError:
        log.exception("events 缓存写入失败")


def _fresh(entry: dict) -> bool:
    try:
        fetched = datetime.fromisoformat(entry["fetched_at"])
        return (datetime.now(timezone.utc) - fetched).total_seconds() < TTL_HOURS * 3600
    except (KeyError, ValueError):
        return False


def get_events(ticker: str) -> EventDates:
    cache = _load_cache()
    entry = cache.get(ticker.upper())
    if entry and _fresh(entry):
        return EventDates(earnings=parse_date(entry.get("earnings")),
                          ex_div=parse_date(entry.get("ex_div")))

    earnings: date | None = None
    ex_div: date | None = None
    try:
        import yfinance as yf

        cal = yf.Ticker(ticker).calendar or {}
        e = cal.get("Earnings Date")
        if isinstance(e, (list, tuple)) and e:
            earnings = parse_date(str(e[0])[:10])
        elif e:
            earnings = parse_date(str(e)[:10])
        x = cal.get("Ex-Dividend Date")
        if x:
            ex_div = parse_date(str(x)[:10])
    except Exception as e:
        log.warning("yfinance 事件日历获取失败 %s: %s", ticker, e)
        if entry:  # 保留过期旧值,好过没有
            return EventDates(earnings=parse_date(entry.get("earnings")),
                              ex_div=parse_date(entry.get("ex_div")))

    cache[ticker.upper()] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "earnings": earnings.isoformat() if earnings else None,
        "ex_div": ex_div.isoformat() if ex_div else None,
    }
    _save_cache(cache)
    return EventDates(earnings=earnings, ex_div=ex_div)
