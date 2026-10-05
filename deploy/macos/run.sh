#!/bin/bash
# launchd 统一入口:python -m <module> [args...]
# launchd 的环境只有 /usr/bin:/bin:/usr/sbin:/sbin,这里补齐:
#   - .venv/bin 在最前 → `python` 指向装好依赖的解释器
#     (.claude/settings.json 白名单写的就是 `python ...`,headless Claude 也靠它)
#   - ~/.local/bin、Homebrew → 找得到 claude CLI
#   - .env 导出到环境 → 可放 CLAUDE_CODE_OAUTH_TOKEN(headless 免钥匙串登录)
set -eo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

export PATH="$REPO/.venv/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1 LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# 只认 KEY=VALUE 行,不 eval(.env 里有注释和中文,source 不安全)
if [ -f .env ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            val="${BASH_REMATCH[2]}"
            val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
            export "${BASH_REMATCH[1]}=$val"
        fi
    done < .env
fi

[ -x "$REPO/.venv/bin/python" ] || { echo "缺 $REPO/.venv,先运行 deploy/macos/install.sh" >&2; exit 78; }

echo "$(date '+%F %T') run.sh: python -m $*" >&2
exec python -m "$@"
