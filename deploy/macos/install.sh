#!/bin/bash
# AutoCovered macOS(Mac mini 常驻)部署
#
#   deploy/macos/install.sh                  安装/更新(建 .venv、跑测试、注册 launchd)
#   deploy/macos/install.sh --with-ibc PATH  同时注册 IBC 自启 IB Gateway
#                                            (PATH = IBC 的 gatewaystartmacos.sh)
#   deploy/macos/install.sh status           查看各任务状态与最近日志
#   deploy/macos/install.sh restart          重启 watcher(改了 settings.yaml 后用)
#   deploy/macos/install.sh stop|start       停/起 watcher(reconcile --apply 前必须 stop)
#   deploy/macos/install.sh run TASK         前台立即跑一次 daily|weekly|patrol(跳过门禁,测试用)
#   deploy/macos/install.sh uninstall        全部注销
#
# 幂等:重复运行 = 拉最新代码后的更新流程。不需要 sudo。
set -eo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$REPO/deploy/macos"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
JOBS=(watcher daily weekly patrol)

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
die()  { red "✗ $*"; exit 1; }
warn() { ylw "⚠ $*"; WARNS=$((WARNS + 1)); }
WARNS=0

label() { echo "com.autocovered.$1"; }

unload() {
    launchctl bootout "$DOMAIN/$(label "$1")" 2>/dev/null || true
}

load() {
    local name=$1 ibc=${2:-}
    local dst="$AGENTS/$(label "$name").plist"
    sed -e "s#__REPO__#$REPO#g" -e "s#__IBC_START__#$ibc#g" \
        "$SRC/$(label "$name").plist" > "$dst"
    plutil -lint -s "$dst" || die "$dst 格式错误"
    unload "$name"
    launchctl bootstrap "$DOMAIN" "$dst"
    grn "✓ 已注册 $(label "$name")"
}

installed_jobs() {
    for f in "$AGENTS"/com.autocovered.*.plist; do
        [ -e "$f" ] && basename "$f" .plist | sed 's/^com\.autocovered\.//'
    done
}

# ------------------------------------------------------------------ 预检

preflight() {
    [ "$(uname)" = Darwin ] || die "只支持 macOS"

    case "$REPO" in
        "$HOME/Documents"*|"$HOME/Desktop"*|"$HOME/Downloads"*)
            die "仓库在 $REPO:launchd 进程受 TCC 限制读不了 Documents/Desktop/Downloads,请移到如 ~/AutoCovered";;
    esac

    [ -f "$REPO/config/settings.yaml" ] || die "缺 config/settings.yaml(cp config/settings.example.yaml 后填写)"
    [ -f "$REPO/config/lots.yaml" ] || warn "缺 config/lots.yaml(税务持有期计算会缺数据)"
    grep -q 'CHANGE-ME' "$REPO/config/settings.yaml" && die "settings.yaml 里 ntfy topic 还是 CHANGE-ME"

    local py
    py=$(PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:$PATH" command -v python3) \
        || die "找不到 python3(建议 brew install python@3.12)"
    "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "$py 版本过低,需要 ≥ 3.10"
    PY=$py

    local claude
    claude=$(PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH" command -v claude || true)
    if [ -z "$claude" ]; then
        warn "找不到 claude CLI:晨报/周报/巡检/事件分析都会失败(watcher 监控不受影响)"
    elif ! grep -q '^CLAUDE_CODE_OAUTH_TOKEN=' "$REPO/.env" 2>/dev/null; then
        warn "未在 .env 配 CLAUDE_CODE_OAUTH_TOKEN:headless 运行依赖钥匙串登录态,重启后/锁屏时可能失效。建议运行 'claude setup-token',把输出写进 .env"
    fi

    local tz
    tz=$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')
    [ "$tz" = "America/Los_Angeles" ] || \
        warn "系统时区是 $tz:plist 的时间按太平洋时间设计(06:15 PT = 09:15 ET)。要么改时区,要么改 plist 里的 Hour"

    local sleep_min
    sleep_min=$(pmset -g | awk '$1 == "sleep" {print $2}')
    [ "${sleep_min:-1}" = 0 ] || \
        warn "系统会休眠(sleep=$sleep_min):运行 sudo pmset -a sleep 0 disksleep 0 autorestart 1 womp 1"

    local port
    port=$(awk '/^ibkr:/{f=1} f && /^[[:space:]]+port:/{print $2; exit}' "$REPO/config/settings.yaml")
    case "$port" in
        4001|4002) ;;
        7496|7497) warn "ibkr.port=$port 是 TWS 端口;Mac mini 上一般跑 IB Gateway(实盘 4001 / 模拟 4002)";;
    esac

    if [ "$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null)" != "$(whoami)" ]; then
        warn "未开启自动登录:LaunchAgent 要等用户登录才跑,停电重启后会全停。系统设置 → 用户与群组 → 自动登录(需关闭 FileVault)"
    fi
}

setup_venv() {
    if [ ! -x "$REPO/.venv/bin/python" ]; then
        echo "→ 创建 .venv($PY)"
        "$PY" -m venv "$REPO/.venv"
    fi
    echo "→ 同步依赖"
    "$REPO/.venv/bin/python" -m pip install -q --upgrade pip
    "$REPO/.venv/bin/python" -m pip install -q -r "$REPO/requirements.txt"
    if grep -qE '^[[:space:]]*(fallback|secondary):[[:space:]]*"?snaptrade' "$REPO/config/settings.yaml"; then
        "$REPO/.venv/bin/python" -m pip install -q snaptrade-python-sdk
    fi
    echo "→ 跑引擎测试"
    (cd "$REPO" && "$REPO/.venv/bin/python" -m pytest -q) || die "测试未通过,不部署"
}

# ------------------------------------------------------------------ 命令

cmd_install() {
    local ibc=""
    if [ "${1:-}" = "--with-ibc" ]; then
        ibc=${2:-}
        [ -f "$ibc" ] || die "--with-ibc 需要 IBC 的 gatewaystartmacos.sh 路径"
    fi

    preflight
    setup_venv
    mkdir -p "$REPO/state/logs" "$AGENTS"
    chmod +x "$SRC/run.sh"

    for j in "${JOBS[@]}"; do load "$j"; done
    if [ -n "$ibc" ]; then
        load ibgateway "$ibc"
    fi

    echo
    if [ "$WARNS" -gt 0 ]; then
        ylw "部署完成,有 $WARNS 条警告(见上)"
    else
        grn "部署完成"
    fi
    echo "下一步:install.sh run patrol 试跑一次,再 install.sh status 看结果"
}

cmd_status() {
    printf '%-26s %-8s %s\n' LABEL PID LAST_EXIT
    for j in $(installed_jobs); do
        local out pid code
        out=$(launchctl print "$DOMAIN/$(label "$j")" 2>/dev/null || true)
        pid=$(awk '$1 == "pid" {print $3}' <<< "$out")
        code=$(awk '/last exit code/ {print $NF}' <<< "$out")
        printf '%-26s %-8s %s\n' "$(label "$j")" "${pid:--}" "${code:--}"
    done
    echo
    for f in "$REPO/state/watcher.log" "$REPO"/state/logs/*.log; do
        [ -f "$f" ] || continue
        echo "── $(basename "$f")"
        tail -n 3 "$f"
    done
}

cmd_uninstall() {
    for j in $(installed_jobs); do
        unload "$j"
        rm -f "$AGENTS/$(label "$j").plist"
        grn "✓ 已注销 $(label "$j")"
    done
}

case "${1:-install}" in
    install|--with-ibc)
        [ "${1:-}" = install ] && shift
        cmd_install "$@";;
    status)    cmd_status;;
    restart)   launchctl kickstart -k "$DOMAIN/$(label watcher)" && grn "✓ watcher 已重启";;
    stop)      unload watcher && grn "✓ watcher 已停(start 恢复)";;
    start)     load watcher;;
    run)
        [[ " daily weekly patrol " == *" ${2:-} "* ]] || die "用法: install.sh run daily|weekly|patrol"
        mkdir -p "$REPO/state/logs"
        # 走同一个 run.sh(同 PATH/.env),加 --force 跳过交易日/盘中门禁
        "$SRC/run.sh" src.run_task "$2" --force 2>&1 | tee -a "$REPO/state/logs/$2.log";;
    uninstall) cmd_uninstall;;
    *) sed -n '2,13p' "$0"; exit 1;;
esac
