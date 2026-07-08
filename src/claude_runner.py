"""claude -p headless 调用封装。

- 在仓库根目录执行,使 .claude/settings.json 权限白名单与 skills 生效
- --output-format json,解析 result 字段
- 超时强杀(防失控三保险之一)
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess

from src.config import REPO_ROOT

log = logging.getLogger(__name__)

# 事件分析/晨报/周报共用的权限集(与 .claude/settings.json 白名单一致)
DEFAULT_TOOLS = (
    "Read,Glob,Grep,WebSearch,"
    "Bash(python .claude/skills/covered-call/scripts/roll_candidates.py:*)"
)


def run_claude(
    prompt: str,
    allowed_tools: str = DEFAULT_TOOLS,
    timeout: int = 300,
    binary: str = "claude",
) -> str:
    exe = shutil.which(binary)
    if not exe:
        raise RuntimeError(f"找不到 {binary} CLI,请确认 Claude Code 已安装并在 PATH 中")

    cmd = [exe, "-p", prompt, "--allowedTools", allowed_tools, "--output-format", "json"]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude 退出码 {proc.returncode}: {(proc.stderr or '')[:500]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude 输出不是 JSON: {proc.stdout[:300]}")
    result = data.get("result") if isinstance(data, dict) else None
    if not result:
        raise RuntimeError("claude 输出中没有 result 字段")
    return result
