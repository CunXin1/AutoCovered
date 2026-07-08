# IBKR & Charles Schwab API 调研

> 目标:自动连接 IBKR 和 Charles Schwab,查询持仓,以及(可选)自动交易。
> 整理日期:2026-07-08

---

## 一、IBKR(官方 API,免费,只需有账户)

IBKR 的 API 生态最成熟,有三条路线:

### 1. TWS API(socket 协议)
- 功能最全、延迟最低:实时行情流、下单、持仓查询。
- 官方支持 Python / Java / C++ / C#。
- **缺点**:必须本地运行 TWS 或 IB Gateway 软件并保持登录。
- Python 社区首选封装:[ib_async](https://github.com/ib-api-reloaded/ib_async)(原 ib_insync 作者 2024 年去世后由社区接手的继任项目),几行代码就能拿到 positions 和下单。
- 文档:[TWS API Documentation](https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/)

### 2. Client Portal / Web API(REST + WebSocket)
- 不需要跑 TWS。IBKR 正在把它统一成基于 OAuth 2.0 的 [IBKR Web API](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/)。
- 个人用户可以用 OAuth 1.0a 做到完全无头(headless)认证。
- 推荐封装库:[IBind](https://github.com/Voyz/ibind) —— 不用挂任何本地网关软件,适合放服务器上定时跑。
- 文档:[Client Portal API](https://interactivebrokers.github.io/cpwebapi/)

### 3. Flex Web Service(只读报表)
- 只读接口:持仓、成交历史、现金流。
- 用一个 token 就能拉数据,是"只想每天看一眼持仓"最省事的方案。
- 正被并入统一 Web API。

---

## 二、Charles Schwab(官方 API,免费,需要 Schwab 账户)

### Schwab Trader API (Individual)
- 入口:[developer.schwab.com](https://developer.schwab.com/products/trader-api--individual)
- 注册应用需人工审核(通常几天)。
- OAuth 2.0 + REST:查余额/持仓/订单、下股票和期权单、实时行情和 K 线。

### Python 封装库
- 首选:[schwab-py](https://schwab-py.readthedocs.io/en/latest/auth.html)(前 TD Ameritrade 时代 tda-api 的续作)
- 轻量替代:[Schwabdev](https://github.com/tylerebowers/Schwabdev)

### ⚠️ 最大的坑:7 天 token 过期
- Schwab 的 [refresh token 强制 7 天过期](https://developer.schwab.com/user-guides/apis-and-apps/oauth-restart-vs-refresh-token),官方不提供延长方式。
- 意味着**每周要手动走一次浏览器登录授权**。做全自动系统时要把"每周日重新授权"纳入运维流程。
- 避开 itsjafer/schwab-api 那类靠 headless 浏览器模拟登录的库 —— 违反服务条款且随时会挂。

---

## 三、第三方聚合(一个 API 同时接两家)

### SnapTrade(详细介绍见下方第四节)
- 官网:[snaptrade.com](https://snaptrade.com/)
- 统一的券商连接 API,同时支持 [IBKR](https://snaptrade.com/brokerage-integrations/ibkr-api) 和 [Schwab](https://snaptrade.com/brokerage-integrations/schwab-api)。
- ⚠️ **IBKR 只读不能交易**(底层走 Flex Query);**Schwab 支持交易**。
- 默认只读;连接时传 `connection_type="trade"` 可开通下单(仅限支持交易的券商)。
- 用户通过它的 Connection Portal 授权,不用自己维护两套 OAuth。
- 适合做产品或不想维护两套认证的场景;免费层可起步,规模化收费。

### Plaid Investments
- 官网:[plaid.com/products/investments](https://plaid.com/products/investments/)
- **纯只读**:`/investments/holdings/get` 拉持仓和交易记录,数据每日收盘后更新,不能下单。
- 支持 [IBKR](https://plaid.com/institutions/interactive-brokers-us/) 和 [Schwab](https://www.openbankingtracker.com/plaid/charles-schwab)。
- 适合只做资产看板 / 净值追踪。

---

## 四、SnapTrade 深入介绍

> 调研日期:2026-07-08,信息来自 SnapTrade 官网定价页与开发者文档。

### 4.1 是什么
SnapTrade 是一个**券商账户连接聚合 API**(类似 Plaid,但专注投资账户且支持下单)。你的应用通过一套统一的 REST API 连接 20+ 家券商,终端用户通过 SnapTrade 托管的 **Connection Portal** 页面授权自己的券商账户,授权后你就能拉取该用户的账户、持仓、余额、交易记录,并在支持的券商上代用户下单。

### 4.2 收费情况(2026 年官网定价)

| 方案 | 价格 | 包含内容 |
|---|---|---|
| **Free** | $0 | 1 个连接用户、20 个券商连接、实时数据、含交易功能、Discord 社区支持 |
| **Pay-as-you-go(实时档)** | **$2 / 连接用户 / 月**(前 5 个用户免费) | 实时数据、含交易、不限用户数、无合同、Discord + 邮件支持 |
| **Pay-as-you-go(每日档)** | **$1 / 连接用户 / 月**(前 5 个用户免费) | 每日缓存数据、**只读不含交易**、手动刷新 $0.05/次 |
| **Custom(企业)** | **$1000/月 起**(含 1000 连接用户) | 批量折扣、更高速率限制、专属 Slack 支持、集成协助、新功能抢先体验 |

- 计费单位是"连接用户"(connected user),不是 API 调用次数——一个用户连了几家券商都算一个用户。
- 无月度最低消费(PAYG),不签合同,随时升降级。
- 个人自用场景:5 个免费用户额度内基本等于**免费**。

### 4.3 对 IBKR / Schwab 的具体支持(关键!)

| | IBKR | Schwab |
|---|---|---|
| 查持仓 / 余额 / 交易记录 | ✅ | ✅ |
| **下单交易** | ❌ **不支持** | ✅ 支持 |
| 底层机制 | Flex Query(Query ID + Token,只读) | OAuth 官方接口,长效连接 |
| 数据新鲜度 | 依赖 Flex 报表生成,非实时 | 实时档可较新 |
| 其他限制 | — | 历史数据最多回溯 4 年 |

**结论:如果你的核心需求包含"在 IBKR 上自动下单",SnapTrade 满足不了,必须直连 IBKR 官方 API。** SnapTrade 更适合把它当作"统一持仓看板 + Schwab 下单通道"。

### 4.4 可用性 / 开发体验
- **注册即用**:官网注册、验证邮箱后立刻生成 API key(`clientId` + `consumerKey`),免费档不需要审核。个别券商(Fidelity、Alpaca、Tradier、TradeStation、Questrade)需要额外申请开通,但 IBKR 和 Schwab 不在此列。
- **官方 SDK 7 种语言**:Python、TypeScript、Java、Ruby、C#、PHP、Go;也可直接调 REST。另有 CLI 工具。
- **接入流程**:注册用户(拿到 `userId` + `userSecret`)→ 生成 Connection Portal 链接让用户授权券商 → 自动同步该凭证下所有账户 → 调 API 读数据/下单。
- **下单流程**:先查询报价 → 检查订单影响(order impact / checked trade)→ 确认下单,带预检机制。
- **数据同步**:每日档保证每天至少同步一次(时间不固定);实时档按需拉取最新数据。
- **认证模型**:API 请求带签名(consumerKey 签名),用户级凭证隔离;用户可随时在券商端或 SnapTrade 端撤销授权。
- **支持渠道**:免费/PAYG 靠 Discord + 邮件,企业档有专属 Slack。

### 4.5 适不适合你

✅ 适合:
- 想要**一个 API 看所有券商持仓**(IBKR + Schwab + 未来更多)。
- 做面向多用户的产品,不想自己维护每家券商的 OAuth 和审核流程。
- 想绕开 Schwab 官方 API 的"每 7 天重新授权"问题(SnapTrade 的 Schwab 连接是长效的,由它代管 token 刷新)。

❌ 不适合:
- 需要 **IBKR 自动交易**(不支持,必须直连 TWS API / Web API)。
- 需要低延迟实时行情或高频操作(聚合层有延迟,且有速率限制)。
- 极度在意数据经过第三方(持仓数据会流经 SnapTrade 服务器)。

---

## 五、怎么选

| 场景 | 推荐方案 |
|---|---|
| 自用量化 / 自动交易(两家都要下单) | IBKR 用 `ib_async` + IB Gateway(或 IBind 走无头 OAuth);Schwab 用 `schwab-py`。各接各的官方 API —— 免费、实时、功能最全。 |
| 只看持仓不交易 | SnapTrade(5 用户内免费,IBKR + Schwab 一个 API 搞定)或 Plaid;IBKR 也可直接用 Flex Query。 |
| 统一看板 + 只在 Schwab 下单 | SnapTrade 一家即可(还顺便解决 Schwab 7 天 token 问题)。 |
| 做给别人用的产品 | SnapTrade,代价是按连接用户收费和数据经过第三方;**注意 IBKR 无法下单**。 |

---

## 参考链接汇总

- [IBKR API Solutions](https://www.interactivebrokers.com/en/trading/ib-api.php)
- [IBKR Web API Docs](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/)
- [Client Portal API](https://interactivebrokers.github.io/cpwebapi/)
- [ib_async (GitHub)](https://github.com/ib-api-reloaded/ib_async)
- [IBind (GitHub)](https://github.com/Voyz/ibind)
- [Schwab Developer Portal](https://developer.schwab.com/products/trader-api--individual)
- [schwab-py 认证文档](https://schwab-py.readthedocs.io/en/latest/auth.html)
- [Schwab OAuth 7 天限制说明](https://developer.schwab.com/user-guides/apis-and-apps/oauth-restart-vs-refresh-token)
- [SnapTrade](https://snaptrade.com/)
- [SnapTrade 定价页](https://snaptrade.com/pricing)
- [SnapTrade 开发者文档](https://docs.snaptrade.com/)
- [SnapTrade × IBKR 集成说明](https://snaptrade.com/brokerage-integrations/ibkr-api)
- [SnapTrade × Schwab 集成说明](https://snaptrade.com/brokerage-integrations/schwab-api)
- [Plaid Investments](https://plaid.com/products/investments/)
