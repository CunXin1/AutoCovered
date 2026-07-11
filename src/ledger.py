"""SQLite 交易账本 — 每一笔 covered call 交易的持久化事实层(模块 1/3 的地基)。

分层:state/positions.json 仍是 Claude 层唯一的"当前状态"接口;
ledger.db 是其下的历史事实,Claude 只能通过 `python -m src.stats` 读取。

写入纪律:
- 只有 daemon watcher 与 executor 写账本;`watcher --once` 只读
- 幂等:trades.exec_id UNIQUE(IBKR execId 或 synthetic:… 确定性键),
  crash 后重放同一批事件 = no-op
- rounds(一条 call 从开到终的生命周期)由 apply() 机械维护:
  开仓事件挂到同合约 open round(无则新建),平仓事件扣减剩余张数,
  归零即关 round;realized_pnl 不落库,由 stats 派生(单一事实源)
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.engine.lifecycle import (
    ASSIGN,
    BUY_TO_CLOSE,
    DEFAULT_OUTCOME,
    EXPIRE,
    OUT_OPEN,
    SELL_TO_OPEN,
    TradeEvent,
)

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  exec_id       TEXT UNIQUE NOT NULL,
  ts            TEXT NOT NULL,
  ticker        TEXT NOT NULL,
  action        TEXT NOT NULL,            -- SELL_TO_OPEN|BUY_TO_CLOSE|EXPIRE|ASSIGN
  strike        REAL NOT NULL,
  expiry        TEXT NOT NULL,            -- YYYY-MM-DD
  contracts     INTEGER NOT NULL,
  price         REAL NOT NULL DEFAULT 0,  -- 每股;EXPIRE/ASSIGN=0
  fees          REAL NOT NULL DEFAULT 0,  -- 总额
  source        TEXT NOT NULL,            -- executor|manual_tws|inferred|backfill
  price_quality TEXT NOT NULL,            -- exact|inferred|user_confirmed|backfill
  proposal_id   TEXT DEFAULT '',
  round_id      INTEGER,
  aux_price     REAL,                     -- ASSIGN: 到期收盘价(upside_forgone)
  note          TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rounds (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker               TEXT NOT NULL,
  strike               REAL NOT NULL,
  expiry               TEXT NOT NULL,
  opened_ts            TEXT NOT NULL,
  closed_ts            TEXT,
  outcome              TEXT NOT NULL DEFAULT 'open',
  rolled_from_round_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_trades_round ON trades(round_id);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_rounds_key ON rounds(ticker, strike, expiry);
"""


@dataclass
class Applied:
    """apply() 实际新插入的一行(重放被幂等跳过的不在其中)。"""

    trade_id: int
    round_id: int
    event: TradeEvent


def _close_first(ev: TradeEvent) -> tuple:
    # 同时间戳时平仓先于开仓,保证"平旧开新"顺序正确
    return (ev.ts, 0 if ev.action != SELL_TO_OPEN else 1)


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=5)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ 查询

    def is_empty(self) -> bool:
        return self.conn.execute("SELECT NOT EXISTS(SELECT 1 FROM trades)").fetchone()[0] == 1

    def get_trade(self, trade_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()

    def filled_contracts(self, proposal_id: str, action: str = SELL_TO_OPEN) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(contracts),0) FROM trades "
            "WHERE proposal_id=? AND action=? AND source='executor'",
            (proposal_id, action)).fetchone()
        return int(row[0])

    def real_orders_today(self) -> int:
        """备用口径(限额主口径仍是 orders.jsonl 的提交时点计数)。"""
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT proposal_id) FROM trades "
            "WHERE source='executor' AND ts LIKE date('now') || '%'").fetchone()
        return int(row[0])

    def _remaining(self, round_id: int) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN action=? THEN contracts ELSE -contracts END),0) "
            "FROM trades WHERE round_id=?", (SELL_TO_OPEN, round_id)).fetchone()
        return int(row[0])

    def _open_round(self, ticker: str, strike: float, expiry: str) -> Optional[int]:
        row = self.conn.execute(
            "SELECT id FROM rounds WHERE ticker=? AND strike=? AND expiry=? "
            "AND outcome=? ORDER BY id DESC LIMIT 1",
            (ticker, strike, expiry, OUT_OPEN)).fetchone()
        return row["id"] if row else None

    def _latest_round(self, ticker: str, strike: float, expiry: str) -> Optional[int]:
        row = self.conn.execute(
            "SELECT id FROM rounds WHERE ticker=? AND strike=? AND expiry=? "
            "ORDER BY id DESC LIMIT 1", (ticker, strike, expiry)).fetchone()
        return row["id"] if row else None

    # ------------------------------------------------------------ 写入

    def apply(self, events: list[TradeEvent]) -> list[Applied]:
        """幂等写入一批事件并维护 rounds。返回实际新插入的行。"""
        applied: list[Applied] = []
        with self.conn:   # 单事务:crash 全回滚,重放幂等
            for ev in sorted(events, key=_close_first):
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO trades "
                    "(exec_id, ts, ticker, action, strike, expiry, contracts, price, "
                    " fees, source, price_quality, proposal_id, aux_price, note) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ev.exec_id, ev.ts, ev.ticker, ev.action, ev.strike,
                     ev.expiry.isoformat(), ev.contracts, round(ev.price, 4),
                     round(ev.fees, 4), ev.source, ev.price_quality,
                     ev.proposal_id, ev.aux_price, ev.note))
                if cur.rowcount == 0:
                    continue   # exec_id 已存在(重放),round 状态早已一致
                trade_id = cur.lastrowid
                round_id = self._attach_round(trade_id, ev)
                applied.append(Applied(trade_id=trade_id, round_id=round_id, event=ev))
        return applied

    def _attach_round(self, trade_id: int, ev: TradeEvent) -> int:
        expiry = ev.expiry.isoformat()
        if ev.action == SELL_TO_OPEN:
            rid = self._open_round(ev.ticker, ev.strike, expiry)
            if rid is None:
                rid = self.conn.execute(
                    "INSERT INTO rounds (ticker, strike, expiry, opened_ts) "
                    "VALUES (?,?,?,?)",
                    (ev.ticker, ev.strike, expiry, ev.ts)).lastrowid
            if ev.rolled_from is not None:
                src = self._latest_round(ev.rolled_from.ticker, ev.rolled_from.strike,
                                         ev.rolled_from.expiry.isoformat())
                if src is not None and src != rid:
                    self.conn.execute(
                        "UPDATE rounds SET rolled_from_round_id=? "
                        "WHERE id=? AND rolled_from_round_id IS NULL", (src, rid))
        else:   # BUY_TO_CLOSE / EXPIRE / ASSIGN
            rid = self._open_round(ev.ticker, ev.strike, expiry)
            if rid is None:
                # 无对应开仓记录(账本启用前的历史腿):建即关的孤儿 round
                rid = self.conn.execute(
                    "INSERT INTO rounds (ticker, strike, expiry, opened_ts) "
                    "VALUES (?,?,?,?)",
                    (ev.ticker, ev.strike, expiry, ev.ts)).lastrowid
                log.warning("平仓事件无对应开仓 round,建孤儿 round #%s(%s)", rid, ev.exec_id)
        self.conn.execute("UPDATE trades SET round_id=? WHERE id=?", (rid, trade_id))

        if ev.action != SELL_TO_OPEN and self._remaining(rid) <= 0:
            outcome = ev.outcome or DEFAULT_OUTCOME.get(ev.action, "unknown")
            self.conn.execute(
                "UPDATE rounds SET outcome=?, closed_ts=? WHERE id=?",
                (outcome, ev.ts, rid))
        return rid

    def confirm_trade_price(self, trade_id: int, price: float) -> Optional[sqlite3.Row]:
        """手机 CONFIRM 命令:人工修正推断价格。返回修正后的行(不存在返回 None)。"""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE trades SET price=?, price_quality='user_confirmed' WHERE id=?",
                (round(price, 4), trade_id))
            if cur.rowcount == 0:
                return None
        return self.get_trade(trade_id)
