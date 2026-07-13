"""SnapTrade 连接管理:注册用户 / 诊断连接类型 / 生成 trade 授权链接。

首次接入顺序(Personal API Key,PERS- 开头):
  1. key 放 .env(SNAPTRADE_CLIENT_ID / SNAPTRADE_CONSUMER_KEY)或 settings.yaml
  2. python scripts/snaptrade_connect.py                            # 诊断;若提示缺 user 凭证 → 下一步
  3. python scripts/snaptrade_connect.py --register <自定义userId>   # user_secret 自动写入 .env
  4. python scripts/snaptrade_connect.py --trade --broker SCHWAB    # 5 分钟内浏览器完成 OAuth
  5. python scripts/snaptrade_connect.py                            # 确认 type=trade,抄 account_id

其他用法:
  python scripts/snaptrade_connect.py --trade --reconnect <连接ID>  # 现有只读连接原地升级为 trade

背景:Connection Portal 的 connection_type 默认是 "read"(只读)。要下单必须
用 "trade" 生成链接并重新走一次券商 OAuth 授权。链接 5 分钟过期,过期重跑即可。
凭证优先级:.env > settings.yaml 的 snaptrade 段。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "config" / "settings.yaml"
ENV_PATH = ROOT / ".env"

ENV_MAP = {
    "SNAPTRADE_CLIENT_ID": "client_id",
    "SNAPTRADE_CONSUMER_KEY": "consumer_key",
    "SNAPTRADE_USER_ID": "user_id",
    "SNAPTRADE_USER_SECRET": "user_secret",
}


def _load_env_file() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_PATH.exists():
        return out
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _save_env_keys(pairs: dict[str, str]) -> None:
    lines = []
    if ENV_PATH.exists():
        lines = [l for l in ENV_PATH.read_text(encoding="utf-8").splitlines()
                 if l.split("=", 1)[0].strip() not in pairs]
    lines += [f"{k}={v}" for k, v in pairs.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(ENV_PATH, 0o600)


def load_cfg() -> dict:
    cfg: dict = {}
    if SETTINGS.exists():
        with open(SETTINGS, "r", encoding="utf-8") as f:
            cfg = dict((yaml.safe_load(f) or {}).get("snaptrade") or {})
    env = _load_env_file()
    for env_key, cfg_key in ENV_MAP.items():
        v = os.environ.get(env_key) or env.get(env_key)
        if v:
            cfg[cfg_key] = v
    missing = [k for k in ("client_id", "consumer_key") if not cfg.get(k)]
    if missing:
        sys.exit(f"缺少凭证: {', '.join(missing)}(.env 或 settings.yaml snaptrade 段)")
    return cfg


def user_kwargs(cfg: dict) -> dict:
    # Personal key(PERS- 前缀)无独立 user:服务端按 key 解析身份,但 SDK
    # 类型检查仍要求这两个字段 → 传空串即可(实测 2026-07-13 有效)
    return {"user_id": cfg.get("user_id") or "", "user_secret": cfg.get("user_secret") or ""}


def build_client(cfg: dict):
    try:
        from snaptrade_client import SnapTrade
    except ImportError:
        sys.exit("未安装 SDK:pip install snaptrade-python-sdk")
    return SnapTrade(client_id=cfg["client_id"], consumer_key=cfg["consumer_key"])


def register(st, cfg: dict, user_id: str) -> None:
    if cfg["client_id"].startswith("PERS-"):
        sys.exit("Personal key 注册时已自带 user,无需 --register(直接 --trade 即可)")
    body = st.authentication.register_snap_trade_user(user_id=user_id).body
    uid = body.get("userId") or body.get("user_id") or user_id
    secret = body.get("userSecret") or body.get("user_secret")
    if not secret:
        sys.exit(f"注册返回异常: {body}")
    _save_env_keys({"SNAPTRADE_USER_ID": str(uid), "SNAPTRADE_USER_SECRET": str(secret)})
    print(f"✅ 用户已注册: {uid}(user_secret 已写入 .env,权限 600)")
    print("下一步: python scripts/snaptrade_connect.py --trade --broker SCHWAB")


def show_status(st, cfg: dict) -> None:
    kwargs = user_kwargs(cfg)
    auths = st.connections.list_brokerage_authorizations(**kwargs).body
    if not auths:
        print("没有任何券商连接。运行:python scripts/snaptrade_connect.py --trade --broker SCHWAB")
        return
    print("券商连接(brokerage authorizations):")
    read_only_ids = []
    for a in auths:
        broker = (a.get("brokerage") or {}).get("name") or "?"
        conn_type = a.get("type") or "?"
        disabled = a.get("disabled", False)
        mark = "✅" if conn_type == "trade" and not disabled else "⚠️"
        print(f"  {mark} {a.get('id')}  {broker}  type={conn_type}"
              f"{'  [已失效,需 --reconnect 修复]' if disabled else ''}")
        if conn_type != "trade":
            read_only_ids.append(a.get("id"))

    accounts = st.account_information.list_user_accounts(**kwargs).body
    print("\n账户:")
    for acc in accounts or []:
        print(f"  {acc.get('id')}  {acc.get('institution_name')}  "
              f"{acc.get('name') or ''} ({acc.get('number') or '-'})")
    configured = cfg.get("account_ids") or []
    if configured:
        known = {acc.get("id") for acc in accounts or []}
        for aid in configured:
            if aid not in known:
                print(f"  ⚠️ settings.yaml account_ids 里的 {aid} 不在已连接账户中")

    if read_only_ids:
        print("\ntype=read 的连接不能下单。原地升级(保留账户与历史):")
        for rid in read_only_ids:
            print(f"  python scripts/snaptrade_connect.py --trade --reconnect {rid}")


def make_portal_url(st, cfg: dict, reconnect: str | None, broker: str | None) -> None:
    kwargs = dict(
        connection_type="trade",
        connection_portal_version="v4",
        **user_kwargs(cfg),
    )
    if reconnect:
        kwargs["reconnect"] = reconnect
    if broker:
        kwargs["broker"] = broker
    body = st.authentication.login_snap_trade_user(**kwargs).body
    url = body.get("redirectURI") if isinstance(body, dict) else body
    action = f"升级连接 {reconnect}" if reconnect else "新建连接"
    print(f"[{action} → trade] 5 分钟内在浏览器打开完成券商 OAuth:\n\n{url}\n")
    print("授权页出现权限确认时,务必勾选/同意 trading 权限。")
    print("完成后运行 python scripts/snaptrade_connect.py 确认 type=trade。")


def main() -> None:
    ap = argparse.ArgumentParser(description="SnapTrade 连接管理")
    ap.add_argument("--register", metavar="userId", help="注册 SnapTrade 用户,secret 写入 .env")
    ap.add_argument("--trade", action="store_true", help="生成 connection_type=trade 的授权链接")
    ap.add_argument("--reconnect", metavar="连接ID", help="原地升级/修复既有连接")
    ap.add_argument("--broker", help="券商 slug(如 SCHWAB),跳过选择页")
    args = ap.parse_args()

    cfg = load_cfg()
    st = build_client(cfg)
    if args.register:
        register(st, cfg, args.register)
    elif args.trade:
        make_portal_url(st, cfg, args.reconnect, args.broker)
    else:
        show_status(st, cfg)


if __name__ == "__main__":
    main()
