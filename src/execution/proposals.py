"""交易提案 — 半自动执行(B 模式)的核心对象。

生命周期:pending → approved/rejected/expired → executed/dry_run_executed/failed
每个提案:唯一 ID、TTL(默认 30 分钟)、限价 net credit、生成依据。
存储:state/proposals.json(id → dict)。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.models import parse_date

log = logging.getLogger(__name__)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"
EXECUTED = "executed"            # 兼容旧记录;新流程用 SUBMITTED→FILLED/CANCELLED
DRY_RUN_EXECUTED = "dry_run_executed"
FAILED = "failed"
SUBMITTED = "submitted"          # 订单已提交券商,等待成交(watcher 周期对账)
PARTIALLY_FILLED = "partially_filled"
FILLED = "filled"
CANCELLED = "cancelled"          # DAY 限价单收盘未成交/被撤


@dataclass
class ProposalLeg:
    action: str          # "BUY"(平旧腿) | "SELL"(开新腿)
    strike: float
    expiry: date
    contracts: int
    right: str = "C"

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "strike": self.strike,
            "expiry": self.expiry.isoformat(),
            "contracts": self.contracts,
            "right": self.right,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProposalLeg":
        return cls(
            action=d["action"],
            strike=d["strike"],
            expiry=parse_date(d["expiry"]),
            contracts=d["contracts"],
            right=d.get("right", "C"),
        )

    def summary(self) -> str:
        verb = "平" if self.action == "BUY" else "开"
        return f"{verb} {self.contracts}x {self.expiry:%m/%d} ${self.strike:g}{self.right}"


@dataclass
class Proposal:
    id: str
    kind: str            # ROLL | OPEN_CALL | CLOSE_CALL
    ticker: str
    position_id: str
    legs: list[ProposalLeg]
    limit_net_credit: float
    rationale: str
    created_at: datetime
    expires_at: datetime
    status: str = PENDING
    result: str = ""

    @classmethod
    def new(
        cls,
        kind: str,
        ticker: str,
        position_id: str,
        legs: list[ProposalLeg],
        limit_net_credit: float,
        rationale: str,
        ttl_minutes: int = 30,
    ) -> "Proposal":
        now = datetime.now(timezone.utc)
        return cls(
            id=f"{ticker}-{uuid.uuid4().hex[:6]}",
            kind=kind,
            ticker=ticker,
            position_id=position_id,
            legs=legs,
            limit_net_credit=limit_net_credit,
            rationale=rationale,
            created_at=now,
            expires_at=now + timedelta(minutes=ttl_minutes),
        )

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return now >= self.expires_at

    def summary(self) -> str:
        legs = " + ".join(l.summary() for l in self.legs)
        return f"[{self.id}] {self.ticker} {self.kind}: {legs},限价 net credit ${self.limit_net_credit:.2f}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "ticker": self.ticker,
            "position_id": self.position_id,
            "legs": [l.to_dict() for l in self.legs],
            "limit_net_credit": self.limit_net_credit,
            "rationale": self.rationale,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "status": self.status,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        return cls(
            id=d["id"],
            kind=d["kind"],
            ticker=d["ticker"],
            position_id=d.get("position_id", d["ticker"]),
            legs=[ProposalLeg.from_dict(l) for l in d["legs"]],
            limit_net_credit=d["limit_net_credit"],
            rationale=d.get("rationale", ""),
            created_at=datetime.fromisoformat(d["created_at"]),
            expires_at=datetime.fromisoformat(d["expires_at"]),
            status=d.get("status", PENDING),
            result=d.get("result", ""),
        )


class ProposalStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> dict[str, dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_all(self, data: dict[str, dict]) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def save(self, p: Proposal) -> None:
        data = self._load_all()
        data[p.id] = p.to_dict()
        self._save_all(data)

    def get(self, pid: str) -> Optional[Proposal]:
        d = self._load_all().get(pid)
        return Proposal.from_dict(d) if d else None

    def load_all(self) -> list[Proposal]:
        """全部提案(账本归因/手动 roll 配对/覆盖率校验用)。"""
        out = []
        for d in self._load_all().values():
            try:
                out.append(Proposal.from_dict(d))
            except (KeyError, ValueError, TypeError):
                log.warning("提案记录损坏,跳过: %s", d.get("id"))
        return out

    def has_pending_for(self, position_id: str) -> bool:
        now = datetime.now(timezone.utc)
        for d in self._load_all().values():
            if d.get("position_id") == position_id and d.get("status") == PENDING:
                if datetime.fromisoformat(d["expires_at"]) > now:
                    return True
        return False

    def by_status(self, status: str) -> list[Proposal]:
        return [Proposal.from_dict(d) for d in self._load_all().values()
                if d.get("status") == status]

    def pending_open_contracts(self, ticker: str) -> int:
        """该 ticker 未过期 pending OPEN_CALL 提案的总张数(覆盖率校验:
        两条各自合规的提案不许联合超卖)。"""
        now = datetime.now(timezone.utc)
        n = 0
        for d in self._load_all().values():
            if (d.get("ticker", "").upper() == ticker.upper()
                    and d.get("kind") == "OPEN_CALL"
                    and d.get("status") == PENDING
                    and datetime.fromisoformat(d["expires_at"]) > now):
                n += sum(l.get("contracts", 0) for l in d.get("legs", []))
        return n

    def expire_stale(self) -> int:
        """把过期的 pending 标记为 expired,返回处理条数。"""
        data = self._load_all()
        now = datetime.now(timezone.utc)
        n = 0
        for d in data.values():
            if d.get("status") == PENDING and datetime.fromisoformat(d["expires_at"]) <= now:
                d["status"] = EXPIRED
                n += 1
        if n:
            self._save_all(data)
        return n
