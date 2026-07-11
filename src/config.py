"""配置加载。settings.yaml(gitignore)优先,缺失时退回 settings.example.yaml。

yaml 的 import 放在函数内,保证 engine/models 的测试在最小环境下可跑。
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = REPO_ROOT / "state"
ANALYSIS_DIR = STATE_DIR / "analysis"
PROMPTS_DIR = REPO_ROOT / "prompts"
CONFIG_DIR = REPO_ROOT / "config"


def load_config(path: Path | None = None) -> dict:
    import yaml

    p = path or (CONFIG_DIR / "settings.yaml")
    if not p.exists():
        example = CONFIG_DIR / "settings.example.yaml"
        log.warning("未找到 %s,使用 %s(请复制并填入真实配置)", p, example)
        p = example
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_lots(path: Path | None = None) -> dict[str, date]:
    """config/lots.yaml:每只股票的建仓日期(broker API 不返回,税务倒计时需要)。"""
    import yaml

    p = path or (CONFIG_DIR / "lots.yaml")
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    out: dict[str, date] = {}
    for ticker, d in raw.items():
        if isinstance(d, date):
            out[str(ticker).upper()] = d
        elif d:
            out[str(ticker).upper()] = date.fromisoformat(str(d))
    return out


def ticker_qcc(cfg: dict, ticker: str, style: str | None = None):
    """三层合并开仓配置:qcc ← styles.<style> ← tickers.<TICKER>。

    合并顺序即风险语义:style(激进/保守)只是默认档的选择,
    per-ticker 覆盖(NVDA/TSLA 低 delta + 部分覆盖)是硬上限,
    永远在最后生效 — aggressive 不能击穿高波动股的风险约束。
    """
    from src.models import QccConfig

    base = dict(cfg.get("qcc") or {})
    if style:
        styles = cfg.get("styles") or {}
        if style not in styles:
            raise ValueError(f"未知风格 {style!r},可用: {sorted(styles)}")
        base.update(styles[style] or {})
    override = (cfg.get("tickers") or {}).get(ticker.upper()) or {}
    base.update(override)
    return QccConfig.from_dict(base)
