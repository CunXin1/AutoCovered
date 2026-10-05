# Mac mini 本地常驻部署

把 AutoCovered 装到一台 24×7 开着的 Mac mini 上：开机自动登录，自动拉起 IB Gateway
和 watcher，按时跑晨报、盘中巡检和周报，结果推到手机。整套方案**不需要 sudo
权限的守护进程**，全部是当前用户的 LaunchAgent。

```
开机 → 自动登录 → launchd(gui/<uid>)
  ├─ com.autocovered.ibgateway  (可选) IBC 启动 IB Gateway,每日自动重启
  ├─ com.autocovered.watcher    常驻,KeepAlive;5s 处理手机命令 / 300s 行情周期
  ├─ com.autocovered.daily      工作日 06:15 PT  晨报      (交易日门禁)
  ├─ com.autocovered.patrol     工作日 07:00/09:00/11:00/12:30 PT  盘中巡检 (盘中门禁)
  └─ com.autocovered.weekly     周日 18:00 PT   周报
        │
        └─ 全部经 deploy/macos/run.sh → .venv/bin/python -m ...
```

## 目录里的文件

| 文件 | 作用 |
|---|---|
| `install.sh` | 安装、更新、查看状态、启停、试跑、卸载的统一入口(幂等) |
| `run.sh` | launchd 的统一启动器：补齐 PATH、导出 `.env`、用 `.venv` 里的 python |
| `com.autocovered.*.plist` | launchd **模板**,`__REPO__` 由 install.sh 替换。不要手工复制 |

## 为什么不能直接 `cp *.plist`(旧方案的问题)

旧版 plist 只是占位模板，在 Mac mini 上直接用会出现以下问题：

1. **找不到 python**:launchd 的 PATH 只有 `/usr/bin:/bin:/usr/sbin:/sbin`,
   `/usr/bin/env python3` 拿到的是系统自带的桩程序，没有装依赖。
2. **找不到 `python`**:`.claude/settings.json` 的白名单写的是 `python ...`(不是
   `python3`),headless Claude 执行巡检时调用会失败。现在 run.sh 把 `.venv/bin` 放在
   PATH 最前面，`python` 就有了。
3. **找不到 claude CLI**:它装在 `~/.local/bin`,不在 launchd 的 PATH 里，所以晨报、
   周报和事件分析全部失败。
4. **没有盘中巡检**:OS 调度只有 daily/weekly,巡检原本依赖 Desktop 的 Routines,
   而 headless 的 Mac mini 上不能指望 Desktop app 一直开着。现在新增了
   `run_task patrol`。
5. 需要手动 `sed /PATH/TO`,用的还是已弃用的 `launchctl load`,而且没有任何预检
   (睡眠、时区、自动登录、端口)。

## 一次性准备(Mac mini 上)

### 1. 系统设置

```bash
# 不睡眠；停电恢复后自动开机;允许网络唤醒
sudo pmset -a sleep 0 disksleep 0 autorestart 1 womp 1
# 时区：所有 plist 的时间都按太平洋时间设计(06:15 PT = 09:15 ET,夏令时同步)
sudo systemsetup -settimezone America/Los_Angeles
```

- **自动登录**:系统设置 → 用户与群组 → 自动登录。LaunchAgent 只在用户登录后运行，
  不开自动登录的话，停电重启后整套系统都不会启动。开启自动登录需要先**关闭
  FileVault**,这是一个安全取舍，Mac mini 应放在物理安全的位置。
- 锁屏可以开(登录会话仍在),但不要设置"注销"。
- 仓库放在 `~/AutoCovered` 这类目录。**不要放在 Documents/Desktop/Downloads**:
  launchd 启动的进程受 TCC 限制，读不了这几个目录(install.sh 会拦下)。

### 2. 代码与配置

```bash
git clone <repo> ~/AutoCovered && cd ~/AutoCovered
cp config/settings.example.yaml config/settings.yaml   # 填 ntfy topic、IBKR 端口/账户
cp config/lots.example.yaml config/lots.yaml
# 从旧机器拷过来(都不进 git):
#   .env              SnapTrade / Finnhub / Flex token
#   state/ledger.db   账本(拷之前先停旧机器的 watcher,别两台同时写)
#   state/*           其余缓存可以不拷，会重新生成
```

`settings.yaml` 在 Mac mini 上要特别检查的项：

| 键 | 建议 |
|---|---|
| `ibkr.port` | IB Gateway 实盘用 `4001`,模拟用 `4002`(7496/7497 是 TWS 的端口) |
| `claude.binary` | 保持 `claude`,run.sh 已经把 `~/.local/bin` 加进 PATH |
| `execution.enabled / dry_run` | 换机器时**不要动**,沿用原值 |

### 3. Python 与 Claude CLI

```bash
brew install python@3.12            # 或 python.org 安装包，需 ≥ 3.10
curl -fsSL https://claude.ai/install.sh | bash   # Claude Code CLI → ~/.local/bin/claude
claude                              # 交互登录一次，确认能用
claude setup-token                  # 生成长期 token
echo 'CLAUDE_CODE_OAUTH_TOKEN=<上一步输出>' >> .env
```

`CLAUDE_CODE_OAUTH_TOKEN` 不是必需的，但强烈建议配置。不配的话,headless 的
`claude -p` 依赖钥匙串里的登录态，重启或钥匙串锁住后可能静默失败。run.sh 会把
`.env` 里的 `KEY=VALUE` 行导出成环境变量(不执行 eval,注释和中文都安全)。

### 4. IB Gateway(+ IBC 自动登录，可选但推荐)

watcher 的实时 Greeks 和下单都依赖 IB Gateway,而 Gateway 有两个特点：
**每天强制重启**,**每周要重新做一次 2FA**。人工维护的话迟早会断。

推荐使用 [IBC](https://github.com/IbcAlpha/IBC):

1. 安装 IB Gateway(stable 版)。
2. 按 IBC 的 macOS 说明安装到 `~/ibc`,在 `config.ini` 里填账号和
   `TradingMode=live|paper`,开启 `AutoRestartTime` 实现每日自动重启。
3. 在 Gateway 设置中启用 API、端口与 `ibkr.port` 一致、Trusted IP 填 `127.0.0.1`、
   取消勾选 Read-Only API(只有要下单时才需要)。
4. 安装时加 `--with-ibc ~/ibc/gatewaystartmacos.sh`(见下)。

每周日的 2FA 仍要在 IBKR Mobile 上点一次确认。没确认时，watcher 会连续失败 3 轮，
然后推送"系统掉线"告警，不会静默。

## 安装

```bash
cd ~/AutoCovered
deploy/macos/install.sh                                          # 不带 IBC
deploy/macos/install.sh --with-ibc ~/ibc/gatewaystartmacos.sh    # 带 IBC
```

安装脚本依次执行：

1. **预检**:macOS、仓库位置、`settings.yaml` 是否存在、ntfy topic 是否还是
   `CHANGE-ME`(是则拒绝安装)、python ≥ 3.10。下列情况只给警告不中止:claude CLI 与
   token、时区、睡眠、端口、自动登录。
2. **`.venv`**:不存在就创建，然后 `pip install -r requirements.txt`。如果 settings
   里用到 snaptrade,会顺带安装它的 SDK。
3. **跑 `pytest`**:测试不过就不部署(状态机和账本不许出错)。
4. **渲染 plist** 到 `~/Library/LaunchAgents/`,并用
   `launchctl bootout` + `bootstrap gui/<uid>` 注册(新式 API,可以重复执行)。

更新代码以后重新跑一遍 `install.sh` 即可(内部会 pip 同步、跑测试、重载 launchd)。

### 安装后验证

```bash
deploy/macos/install.sh status        # 每个 job 的 PID / 上次退出码 + 各日志的最后 3 行
tail -f state/watcher.log             # 盘中应每 5 分钟看到一次"周期完成"
deploy/macos/install.sh run patrol    # 前台立即跑一次巡检(--force 跳过盘中门禁)
deploy/macos/install.sh run daily     # 同上，跑晨报，手机应收到推送
```

在手机 ntfy 上发 `STATUS`,几秒内应该收到持仓状态。这能同时验证 watcher 在运行、
命令通道和推送通道都正常。

## 日常运维

| 场景 | 命令 |
|---|---|
| 改了 `settings.yaml` | `install.sh restart`(kickstart -k 重启 watcher) |
| 拉了新代码 | `git pull && deploy/macos/install.sh` |
| 补录历史成交 | `install.sh stop` → `.venv/bin/python -m src.reconcile --flex --apply` → `install.sh start`(账本只能有一个写者) |
| 临时试跑某个任务 | `install.sh run daily\|weekly\|patrol` |
| 全部移除 | `install.sh uninstall`(不碰 `state/`、`.venv`) |

在 Mac mini 上手工跑仓库命令时，用 `.venv/bin/python` 或先
`source .venv/bin/activate`,和 launchd 用同一套依赖。

### 日志在哪

| 文件 | 内容 |
|---|---|
| `state/watcher.log` | watcher 业务日志(Python 写的，主要看这个) |
| `state/logs/watcher.launchd.log` | watcher 的 stdout/stderr,包含启动期崩溃的 traceback(和上面有重复) |
| `state/logs/{daily,weekly,patrol}.log` | 各定时任务每次运行的输出 |
| `state/logs/ibgateway.log` | IBC/Gateway 启动输出 |
| `state/analysis/` | 晨报、周报、巡检、事件分析全文 |

日志量很小(watcher 每 5 分钟一行),不配轮转也能跑很久。需要轮转的话，用
`/etc/newsyslog.d/` 加规则即可。

## 行为细节(出问题时对照)

- **定时任务错过了**:Mac 在触发时刻睡着(没关睡眠)或关机时,launchd 会在唤醒后
  补跑**一次**(多次错过合并成一次)。补跑的晨报可能已经过了开盘;巡检有盘中门禁，
  收盘后补跑会直接跳过。
- **门禁**:`daily` 在非交易日跳过;`patrol` 不在盘中(包括节假日、半日市收盘后)
  跳过;`weekly` 不设门禁。所以 plist 的时间可以无脑写满工作日。
- **watcher 崩溃**:`KeepAlive` 会立即拉起,`ThrottleInterval=30` 保证最多每 30 秒
  一次。Gateway 断线由 broker 层自己重连，不会让进程退出。
- **`.venv` 缺失**:run.sh 以 78 退出并在日志写明原因,launchd 会按 30 秒节流反复
  重试。补跑 install.sh 后会自动恢复。
- **推送重复**:如果同时在 Claude Desktop Routines 里注册了晨报/巡检/周报，会推送
  两遍。Mac mini 上只用这里的 launchd,Desktop routines 删掉或改成手动触发。
- **Claude 用量**:巡检每个交易日最多 4 次，晨报 1 次。watcher 的事件触发分析受
  `claude.daily_call_limit` 限额,run_task 的任务不占这个额度。
- **巡检和 watcher 并发**:巡检里的 `watcher --once` 用 `client_id_once`(14),而且
  不写账本，和常驻 watcher(11)互不冲突。

## 从开发机迁移的检查清单

- [ ] 旧机器:`install.sh uninstall`(或注销 Windows 计划任务/Desktop routines),
      停掉 watcher
- [ ] 拷贝 `config/settings.yaml`、`config/lots.yaml`、`.env`、`state/ledger.db`
- [ ] Mac mini:pmset、时区、自动登录、关闭 FileVault
- [ ] IB Gateway + IBC,端口改成 4001/4002
- [ ] `claude setup-token` → `.env`
- [ ] `deploy/macos/install.sh [--with-ibc ...]`,没有红色错误
- [ ] `install.sh run patrol`、手机发 `STATUS` 均正常
- [ ] 断电重启一次，确认全部自动恢复(`install.sh status`)
