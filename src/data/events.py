"""财报/除息日历 — Finnhub 主源(有 key 时)+ yfinance 兜底 + 本地缓存(20 小时 TTL)。

Finnhub(参考用户 PenguinAI 仓库 data/earnings/finnhub.py 的选型):
- GET /calendar/earnings?symbol=T&from=&to=&token=KEY,免费档 ~60 req/min,
  返回财报日 + bmo/amc/dmh;本系统持仓量级(≤20 票 / 20h TTL)远用不满限额
- key 从环境变量或仓库 .env 的 FINNHUB_API_KEY 读;没 key 自动降级 yfinance

yfinance 是非官方接口,可能随时抖动;因此:
- 全部 try/except,失败时保留旧缓存或返回空(EVENT_RISK 规则少触发,不误报)
- 晨报 prompt 会让 Claude 用 WebSearch 交叉验证财报日期
- 除息日 Finnhub 免费档不含,始终走 yfinance
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.config import STATE_DIR
from src.models import EventDates, parse_date

log = logging.getLogger(__name__)

CACHE_PATH = STATE_DIR / "events_cache.json"
TTL_HOURS = 20
FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings"
WINDOW_DAYS = 120


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


def _finnhub_key() -> str | None:
    key = os.environ.get("FINNHUB_API_KEY")
    if key:
        return key
    env_path = Path(__file__).resolve().parents[2] / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("FINNHUB_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    except OSError:
        pass
    return None


def _earnings_from_finnhub(ticker: str, key: str) -> tuple[date | None, str | None]:
    """返回 (未来最近一次财报日, bmo/amc/dmh)。ETF 等无财报标的返回 (None, None)。"""
    today = date.today()
    params = urllib.parse.urlencode({
        "symbol": ticker.upper(),
        "from": today.isoformat(),
        "to": (today + timedelta(days=WINDOW_DAYS)).isoformat(),
        "token": key,
    })
    with urllib.request.urlopen(f"{FINNHUB_URL}?{params}", timeout=10) as r:
        rows = (json.load(r) or {}).get("earningsCalendar") or []
    upcoming = sorted(
        (parse_date(row.get("date")), row.get("hour") or None)
        for row in rows
        if parse_date(row.get("date")) is not None
    )
    return upcoming[0] if upcoming else (None, None)


def upcoming_or_none(d: date | None, today: date | None = None) -> date | None:
    """过期的事件日期视为未知。yfinance 的 Ex-Dividend Date 常是上一次除息
    (实测 2026-07:NOK/NVDA/GOOG/AAPL 均返回过去日期),存进 events 会让
    EVENT_RISK 规则永假、并误导读 positions.json 的分析层。"""
    return d if d and d >= (today or date.today()) else None


def get_events(ticker: str) -> EventDates:
    cache = _load_cache()
    entry = cache.get(ticker.upper())
    if entry and _fresh(entry):
        return EventDates(earnings=upcoming_or_none(parse_date(entry.get("earnings"))),
                          ex_div=upcoming_or_none(parse_date(entry.get("ex_div"))))

    earnings: date | None = None
    hour: str | None = None
    source = None

    key = _finnhub_key()
    if key:
        try:
            earnings, hour = _earnings_from_finnhub(ticker, key)
            source = "finnhub"
        except Exception as e:
            log.warning("Finnhub 财报日历获取失败 %s: %s(降级 yfinance)", ticker, e)

    ex_div: date | None = None
    try:
        import yfinance as yf

        cal = yf.Ticker(ticker).calendar or {}
        if source != "finnhub":
            e = cal.get("Earnings Date")
            if isinstance(e, (list, tuple)) and e:
                earnings = parse_date(str(e[0])[:10])
            elif e:
                earnings = parse_date(str(e)[:10])
            source = source or "yfinance"
        x = cal.get("Ex-Dividend Date")
        if x:
            ex_div = parse_date(str(x)[:10])
    except Exception as e:
        log.warning("yfinance 事件日历获取失败 %s: %s", ticker, e)
        if source is None and entry:  # 两源皆失败:保留旧缓存值,好过没有
            return EventDates(earnings=upcoming_or_none(parse_date(entry.get("earnings"))),
                              ex_div=upcoming_or_none(parse_date(entry.get("ex_div"))))

    earnings = upcoming_or_none(earnings)
    ex_div = upcoming_or_none(ex_div)
    cache[ticker.upper()] = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "earnings": earnings.isoformat() if earnings else None,
        "ex_div": ex_div.isoformat() if ex_div else None,
        "hour": hour,
        "source": source,
    }
    _save_cache(cache)
    return EventDates(earnings=earnings, ex_div=ex_div)
