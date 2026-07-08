"""交易日/盘中判断。用 pandas_market_calendars 而不是在调度器里写死时间,
避免夏令时和节假日的坑 — Task Scheduler/launchd 只管无脑触发,这里做门禁。
"""
from __future__ import annotations

import functools
from datetime import date


@functools.lru_cache(maxsize=4)
def _calendar(name: str):
    import pandas_market_calendars as mcal

    return mcal.get_calendar(name)


def is_trading_day(d: date | None = None, calendar: str = "NYSE") -> bool:
    import pandas as pd

    d = d or pd.Timestamp.now(tz="America/New_York").date()
    sched = _calendar(calendar).schedule(start_date=d, end_date=d)
    return not sched.empty


def is_market_open_now(calendar: str = "NYSE") -> bool:
    import pandas as pd

    now = pd.Timestamp.now(tz="America/New_York")
    sched = _calendar(calendar).schedule(start_date=now.date(), end_date=now.date())
    if sched.empty:
        return False
    row = sched.iloc[0]
    return bool(row["market_open"] <= now <= row["market_close"])
