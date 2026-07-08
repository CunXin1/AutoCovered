"""推送层。主通道 ntfy(双向:出站推送 + 手机 action 按钮回传命令),
可选镜像 Bark(单向)。推送失败绝不能杀死 watcher — 只记日志。

severity → ntfy priority 映射:🔴5(urgent,响铃) 🟠4(high) 🟡3(default) 🟢/⚪2(low)
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

SEVERITY_TO_PRIORITY = {4: 5, 3: 4, 2: 3, 1: 3, 0: 2}


class Notifier:
    def __init__(self, cfg: dict):
        n = cfg.get("notify") or {}
        self.provider = n.get("provider", "none")
        self.ntfy = n.get("ntfy") or {}
        self.bark = n.get("bark") or {}
        self.mirror_bark = bool(n.get("mirror_bark", False))

    # ---------------------------------------------------------------- API

    def push(
        self,
        title: str,
        body: str = "",
        severity: int = 2,
        actions: list[dict] | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        ok = False
        try:
            if self.provider == "ntfy":
                ok = self._ntfy(title, body, severity, actions, tags)
            elif self.provider == "bark":
                ok = self._bark(title, body)
            else:
                log.info("notify 未启用: %s | %s", title, body[:120])
            if self.mirror_bark and self.provider != "bark" and self.bark.get("device_key"):
                self._bark(title, body)
        except Exception:
            log.exception("推送失败: %s", title)
        return ok

    def approval_actions(self, proposal_id: str) -> list[dict]:
        """给提案推送生成 ✅批准/❌拒绝 一键按钮(POST 回 commands topic)。"""
        server = (self.ntfy.get("server") or "https://ntfy.sh").rstrip("/")
        topic = self.ntfy.get("topic_commands", "")
        if not topic:
            return []
        headers = self._auth_headers()

        def act(label: str, cmd: str) -> dict:
            a = {
                "action": "http",
                "label": label,
                "url": f"{server}/{topic}",
                "method": "POST",
                "body": f"{cmd} {proposal_id}",
                "clear": True,
            }
            if headers:
                a["headers"] = headers
            return a

        return [act("✅ 批准", "APPROVE"), act("❌ 拒绝", "REJECT")]

    # ---------------------------------------------------------------- impl

    def _auth_headers(self) -> dict:
        token = self.ntfy.get("token") or ""
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _ntfy(self, title, body, severity, actions, tags) -> bool:
        import requests

        server = (self.ntfy.get("server") or "https://ntfy.sh").rstrip("/")
        topic = self.ntfy.get("topic_alerts", "")
        if not topic:
            log.warning("ntfy topic_alerts 未配置,跳过推送")
            return False
        payload = {
            "topic": topic,
            "title": title,
            "message": body or title,
            "priority": SEVERITY_TO_PRIORITY.get(severity, 3),
        }
        if tags:
            payload["tags"] = tags
        if actions:
            payload["actions"] = actions
        r = requests.post(server, json=payload, headers=self._auth_headers(), timeout=10)
        r.raise_for_status()
        return True

    def _bark(self, title, body) -> bool:
        import requests

        server = (self.bark.get("server") or "https://api.day.app").rstrip("/")
        key = self.bark.get("device_key", "")
        if not key:
            return False
        payload = {
            "title": title,
            "body": body or title,
            "device_key": key,
            "group": self.bark.get("group", "covered-call"),
        }
        r = requests.post(f"{server}/push", json=payload, timeout=10)
        r.raise_for_status()
        return True
