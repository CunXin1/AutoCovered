"""IBKR Flex Web Service v3 客户端 —— 两步取账户报表。

为什么不直接用 ib_async.FlexReport:本模块要满足几条硬要求 ——
IBKR 的限速纪律(SendRequest ≤1 次/秒、≤10 次/分钟)、报表尚未生成时的
有上限轮询重试、把错误码原样上报、以及 token 绝不进日志(含 URL)。

流程(v=3):
1. SendRequest?t=<token>&q=<queryId>&v=3  → Status=Success 时给 ReferenceCode
2. GetStatement?t=<token>&q=<ReferenceCode>&v=3 → 报表 XML
   第二步的 q 是 ReferenceCode,不是 Query ID。
   报表还在生成时返回 ErrorCode 1019,按上限轮询。

日期:缺省用 query 自带周期(本仓库的 query 是滚动 365 天)。要指定区间用
fd/td(yyyyMMdd),单段不超过 365 天 —— 更长的跨度用 date_segments() 分段。
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# 现行文档端点在前,老的 Universal servlet 端点作为回退
ENDPOINTS = (
    "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService",
    "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService",
)
USER_AGENT = "AutoCovered/1.0 (+covered-call ledger reconciliation)"

# IBKR 限速:SendRequest 每秒 ≤1、每分钟 ≤10
MIN_INTERVAL_S = 1.0
MAX_PER_MINUTE = 10

# 报表仍在生成
ERR_NOT_READY = "1019"
MAX_SEGMENT_DAYS = 365

_send_times: deque[float] = deque(maxlen=MAX_PER_MINUTE)


class FlexError(RuntimeError):
    """Flex 侧明确报错(带 IBKR 错误码)。"""

    def __init__(self, code: str, message: str):
        super().__init__(f"Flex 错误 {code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Segment:
    fd: str   # yyyyMMdd
    td: str


def date_segments(start: date, end: date,
                  max_days: int = MAX_SEGMENT_DAYS) -> list[Segment]:
    """把区间切成每段 ≤max_days 的若干段(Flex 单次请求上限 365 天)。"""
    if end < start:
        raise ValueError("结束日期早于开始日期")
    out: list[Segment] = []
    cur = start
    while cur <= end:
        last = min(cur + timedelta(days=max_days - 1), end)
        out.append(Segment(cur.strftime("%Y%m%d"), last.strftime("%Y%m%d")))
        cur = last + timedelta(days=1)
    return out


def _redact(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def _throttle() -> None:
    """就地实现 ≤1/s 且 ≤10/min。"""
    now = time.monotonic()
    if _send_times:
        gap = now - _send_times[-1]
        if gap < MIN_INTERVAL_S:
            time.sleep(MIN_INTERVAL_S - gap)
    if len(_send_times) == MAX_PER_MINUTE:
        oldest = _send_times[0]
        wait = 60.0 - (time.monotonic() - oldest)
        if wait > 0:
            log.info("Flex 限速:每分钟上限已满,等待 %.0f 秒", wait)
            time.sleep(wait)
    _send_times.append(time.monotonic())


def _get(path: str, params: dict, token: str, timeout: int) -> ET.Element:
    """发一次请求并解析 XML。错误信息里的 token 一律脱敏。"""
    import requests

    last_err: Optional[Exception] = None
    for base in ENDPOINTS:
        url = f"{base}/{path}"
        try:
            resp = requests.get(url, params=params, timeout=timeout,
                                headers={"User-Agent": USER_AGENT})
        except requests.RequestException as e:
            last_err = RuntimeError(f"{base} 请求失败: {_redact(str(e), token)}")
            log.warning("Flex 端点 %s 不可用,尝试下一个", base)
            continue
        if resp.status_code != 200:
            last_err = RuntimeError(f"{base} 返回 HTTP {resp.status_code}")
            continue
        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as e:
            body = _redact(resp.text[:200], token)
            raise RuntimeError(f"Flex 返回的不是合法 XML: {e};开头: {body}") from None
    raise last_err or RuntimeError("Flex 所有端点均不可用")


def _status_error(root: ET.Element) -> Optional[FlexError]:
    """FlexStatementResponse 里的失败状态 → FlexError;成功/非该结构返回 None。"""
    if root.tag != "FlexStatementResponse":
        return None
    status = (root.findtext("Status") or "").strip()
    if status.lower() == "success":
        return None
    code = (root.findtext("ErrorCode") or "").strip()
    msg = (root.findtext("ErrorMessage") or "未提供错误信息").strip()
    return FlexError(code or "?", msg)


def send_request(token: str, query_id: str, *, fd: str = "", td: str = "",
                 timeout: int = 30) -> str:
    """第一步:请求生成报表,返回 ReferenceCode。"""
    params = {"t": token, "q": query_id, "v": "3"}
    if fd and td:
        params.update({"fd": fd, "td": td})
    _throttle()
    log.info("Flex SendRequest queryId=%s%s", query_id,
             f" 区间 {fd}-{td}" if fd and td else "(用 query 自带周期)")
    root = _get("SendRequest", params, token, timeout)
    err = _status_error(root)
    if err:
        raise err
    ref = (root.findtext("ReferenceCode") or "").strip()
    if not ref:
        raise RuntimeError("Flex SendRequest 成功但没返回 ReferenceCode")
    return ref


def get_statement(token: str, reference_code: str, *, timeout: int = 60,
                  max_wait_s: int = 180, poll_s: int = 5) -> ET.Element:
    """第二步:按 ReferenceCode 取报表;未就绪(1019)时有上限轮询。"""
    params = {"t": token, "q": reference_code, "v": "3"}
    deadline = time.monotonic() + max_wait_s
    attempt = 0
    while True:
        attempt += 1
        root = _get("GetStatement", params, token, timeout)
        err = _status_error(root)
        if err is None:
            return root
        if err.code != ERR_NOT_READY:
            raise err
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"报表在 {max_wait_s} 秒内未生成完毕(最后状态 {err.code}: {err.message})")
        log.info("报表仍在生成(第 %d 次),%d 秒后重试", attempt, poll_s)
        time.sleep(poll_s)


def fetch_statement(token: str, query_id: str, *, fd: str = "", td: str = "",
                    timeout: int = 60, max_wait_s: int = 180,
                    poll_s: int = 5) -> ET.Element:
    """完整两步:返回报表 XML 根节点。"""
    if not token or not query_id:
        raise ValueError("缺少 Flex token 或 query id")
    ref = send_request(token, query_id, fd=fd, td=td, timeout=timeout)
    return get_statement(token, ref, timeout=timeout,
                         max_wait_s=max_wait_s, poll_s=poll_s)


def fetch_range(token: str, query_id: str, start: date, end: date,
                **kw) -> list[ET.Element]:
    """跨度可超过 365 天:自动分段,返回每段的报表根节点。"""
    return [fetch_statement(token, query_id, fd=s.fd, td=s.td, **kw)
            for s in date_segments(start, end)]


# ---------------------------------------------------------------- 报表元信息


@dataclass
class StatementInfo:
    """报表自述的身份:哪个账户、覆盖哪段时间、什么时候生成的。"""

    account_id: str
    from_date: str
    to_date: str
    period: str
    when_generated: str

    @property
    def coverage(self) -> str:
        if self.from_date and self.to_date:
            return f"{self.from_date} → {self.to_date}"
        return self.period or "(报表未声明区间)"


def statement_infos(root: ET.Element) -> list[StatementInfo]:
    out = []
    for stmt in root.iter("FlexStatement"):
        out.append(StatementInfo(
            account_id=stmt.get("accountId", ""),
            from_date=stmt.get("fromDate", ""),
            to_date=stmt.get("toDate", ""),
            period=stmt.get("period", ""),
            when_generated=stmt.get("whenGenerated", ""),
        ))
    return out


def section_inventory(root: ET.Element) -> dict[str, int]:
    """报表里实际存在的章节 → 行数。缺失的章节不出现(不推断不存在的记录)。"""
    sections = {
        "Trades": "Trade",
        "TradeConfirms": "TradeConfirm",
        "OpenPositions": "OpenPosition",
        "CashReport": "CashReportCurrency",
        "CashTransactions": "CashTransaction",
        "EquitySummaryInBase": "EquitySummaryByReportDateInBase",
        "StmtFunds": "StatementOfFundsLine",
        "ChangeInNAV": "ChangeInNAV",
        "FxPositions": "FxPosition",
        "ClosedLots": "ClosedLot",
        "CorporateActions": "CorporateAction",
        "ConversionRates": "ConversionRate",
        "SecuritiesInfo": "SecurityInfo",
    }
    out: dict[str, int] = {}
    for name, child in sections.items():
        n = sum(1 for _ in root.iter(child))
        if n:
            out[name] = n
    return out


def level_counts(root: ET.Element, child: str = "Trade") -> dict[str, int]:
    """按 levelOfDetail 统计行数。

    同一笔成交会在 ASSET_CLASS / ORDER / EXECUTION / CLOSED_LOT 各出现一次,
    叠加统计就是成倍重复计算 —— 调用方必须按层级取其一。
    """
    out: dict[str, int] = {}
    for el in root.iter(child):
        lod = (el.get("levelOfDetail") or "(未标注)").upper()
        out[lod] = out.get(lod, 0) + 1
    return out
