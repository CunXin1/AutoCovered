"""ntfy 命令监听线程 — 双向推送的"入站"半边。

订阅 commands topic 的流式接口,把手机发来的命令放进线程安全队列,
watcher 主循环每个 tick 取出分发。命令语法:

    APPROVE <提案id>   批准执行(受 execution.enabled/dry_run 约束)
    REJECT <提案id>    拒绝提案
    STATUS             回推当前持仓状态总览
    ANALYZE <ticker>   手动触发 Claude 深度分析

连接断开自动指数退避重连(5s→60s)。守护线程,随主进程退出。
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time

log = logging.getLogger(__name__)


class CommandListener(threading.Thread):
    def __init__(self, cfg: dict, cmd_queue: "queue.Queue[str]"):
        super().__init__(daemon=True, name="ntfy-listener")
        n = (cfg.get("notify") or {}).get("ntfy") or {}
        self.server = (n.get("server") or "https://ntfy.sh").rstrip("/")
        self.topic = n.get("topic_commands", "")
        self.token = n.get("token") or ""
        self.queue = cmd_queue
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        import requests

        if not self.topic:
            log.warning("ntfy topic_commands 未配置,双向命令通道停用")
            return
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        backoff = 5
        url = f"{self.server}/{self.topic}/json"
        while not self._stop.is_set():
            try:
                with requests.get(url, headers=headers, stream=True,
                                  timeout=(10, 90)) as resp:
                    resp.raise_for_status()
                    backoff = 5
                    log.info("命令通道已连接: %s", url)
                    for line in resp.iter_lines():
                        if self._stop.is_set():
                            return
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if event.get("event") == "message" and event.get("message"):
                            msg = event["message"].strip()
                            log.info("收到手机命令: %s", msg)
                            self.queue.put(msg)
            except Exception as e:
                log.warning("命令通道断开(%s),%ds 后重连", e, backoff)
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 60)
