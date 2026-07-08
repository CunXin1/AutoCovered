"""state/ 目录读写 — Python 层与 Claude 层之间的唯一接口。

positions.json 采用原子写(临时文件 + os.replace),避免 Claude 读到半截文件。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

from src.config import STATE_DIR
from src.models import Position, PositionState, StateResult

log = logging.getLogger(__name__)


class StateStore:
    def __init__(self, state_dir: Path = STATE_DIR):
        self.dir = state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "analysis").mkdir(exist_ok=True)

    @property
    def positions_path(self) -> Path:
        return self.dir / "positions.json"

    @property
    def alerts_path(self) -> Path:
        return self.dir / "alerts.jsonl"

    @property
    def notify_log_path(self) -> Path:
        return self.dir / "notify_log.json"

    # ---------------------------------------------------------- positions

    def write_positions(
        self,
        positions: list[Position],
        results: dict[str, StateResult],
        account: str = "",
        data_source: str = "",
    ) -> None:
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "account": account,
            "data_source": data_source,
            "positions": [],
        }
        for pos in positions:
            d = pos.to_dict()
            r = results.get(pos.position_id)
            if r:
                d["state"] = r.state.value
                d["flags"] = [f.value for f in r.flags]
                d["reasons"] = r.reasons
            payload["positions"].append(d)

        tmp = self.positions_path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.positions_path)

    def load_positions_raw(self) -> dict:
        if not self.positions_path.exists():
            return {"positions": []}
        with open(self.positions_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_prev_states(self) -> dict[str, PositionState]:
        out: dict[str, PositionState] = {}
        for d in self.load_positions_raw().get("positions", []):
            try:
                out[d["id"]] = PositionState(d["state"])
            except (KeyError, ValueError):
                continue
        return out

    # ---------------------------------------------------------- alerts

    def append_alert(self, record: dict) -> None:
        record.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        with open(self.alerts_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ---------------------------------------------------------- notify log

    def load_notify_log(self) -> dict[str, date]:
        if not self.notify_log_path.exists():
            return {}
        try:
            with open(self.notify_log_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {k: date.fromisoformat(v) for k, v in raw.items()}
        except (json.JSONDecodeError, ValueError):
            log.warning("notify_log.json 损坏,重置")
            return {}

    def save_notify_log(self, log_map: dict[str, date]) -> None:
        with open(self.notify_log_path, "w", encoding="utf-8") as f:
            json.dump({k: v.isoformat() for k, v in log_map.items()}, f, ensure_ascii=False)

    # ---------------------------------------------------------- analysis

    def save_analysis(self, name: str, content: str) -> Path:
        path = self.dir / "analysis" / name
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path
