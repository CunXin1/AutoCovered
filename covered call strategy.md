# Covered Call Strategy Guide (Tech Stock Holdings)

*Research compiled: July 2026*

---

# English Version

## 1. Core Parameters: Choosing Strike Price and Expiration

The mainstream "standard playbook":

- **Sell at 0.20–0.30 delta** (out-of-the-money strikes). Delta roughly equals the probability of assignment — a 0.30 delta call has about a 30% chance of expiring in the money. Go toward 0.20 delta if you want to keep your shares; go toward 0.40 if you want more premium.
- **Choose 30–45 days to expiration (DTE)**. Around 45 DTE sits at the start of the steepest part of the theta decay curve — better time-value capture per day than weeklies, and it lets you sell strikes further out of the money.
- **Management rule: close at 50–75% of max profit, or at 21 DTE**, whichever comes first. This captures most of the theta while avoiding gamma risk near expiration, then you open the next cycle.

## 2. Expected Annualized Returns (Driven by Implied Volatility)

Premium income is directly tied to the stock's implied volatility (IV). Different tech holdings vary widely:

| Holding Type | Typical Monthly Yield | Annualized (Premium Only) |
|---|---|---|
| Low-volatility mega-cap tech (AAPL, MSFT) | 0.8%–1.2% | ~10–15% |
| Medium volatility (GOOGL, AMZN, META) | 1%–2% | ~12–24% |
| High volatility (NVDA, TSLA, AMD) | 3%–5% | 30%+ or higher |

**Reality check**: these are "nominal" premium yields, not your true total return. The CBOE BXM Index (systematic S&P 500 buy-write) returned ~11.8% annualized from 1988–2012 — roughly matching the S&P 500 with only two-thirds of the volatility. However, **BXM significantly underperformed the S&P during the post-2012 bull market**. The reason is simple: covered calls cap your upside, and tech stock returns are heavily concentrated in a few explosive rallies. If you sell calls on a stock like NVDA year-round, missing one big rally after assignment can wipe out a full year of premium income.

## 3. Common Strategy Variants

1. **Standard monthly OTM (most common)**: 30–45 DTE, 0.20–0.30 delta, roll mechanically. Best for core positions you intend to hold long-term and just want to enhance yield on.
2. **Partial covering**: sell calls on only 1/3–1/2 of your position. E.g., with 300 shares of NVDA, sell only 1 contract. You sacrifice some premium in exchange for not capping the entire position during a big rally. **A widely recommended compromise for high-growth tech stocks.**
3. **Earnings IV-crush play**: IV inflates before earnings (tech stocks average a 40%+ post-earnings IV collapse; NVDA averages ~48%). Sell calls before earnings, buy them back cheap after the IV crush. High reward but real gap risk — if the stock gaps through your strike, the earnings move overwhelms any IV-crush benefit. The conservative approach: **skip earnings weeks entirely**, or use very far OTM strikes during them.
4. **The Wheel strategy**: after shares get called away, don't chase the price back up — sell cash-secured puts to re-enter on a pullback, and repeat the cycle. Suitable if you don't mind your position rotating in and out.

## 4. Managing When the Stock Breaks Above Your Strike (Rolling)

If the stock rises above your strike and you don't want to deliver shares, you can **roll up and out**: buy back the current call and simultaneously sell a new call at a higher strike and later expiration, often for a net credit. Caution: every roll effectively realizes a loss on the front-month call and opens a new position. If the stock keeps trending up, repeated rolling compresses profits and can compound losses. Fidelity's guidance: base rolling decisions on your outlook for the stock, not on refusing to "take the loss." **Sometimes the best choice is to let the shares be called away** — that's part of the strategy's design.

## 5. Tax Considerations (US)

- Premium income is taxed as **short-term capital gains** (up to 37%), regardless of how long you hold.
- The key concept is the **Qualified Covered Call (QCC)**: a call with more than 30 days to expiration and a strike that isn't deep in-the-money. Selling an **OTM QCC does not affect the stock's long-term holding period**; but selling an **in-the-money call suspends the holding period**, and a non-qualified covered call can reset a holding period of under one year to zero — affecting whether you eventually get long-term capital gains rates (15%/20% vs. ordinary rates).
- Practical implication: for positions held less than a year, only sell OTM calls with 30+ DTE — these are almost always qualified. In an IRA or other tax-advantaged account, none of this matters.

## 6. Combined Recommendations for Tech Stock Holders

1. **For stable positions like AAPL/MSFT**: standard 30–45 DTE, 0.20–0.30 delta, full coverage. ~10–15% annualized enhancement at low risk.
2. **For high-volatility growth positions like NVDA/TSLA**: either cover only part of the position, or use lower delta (0.15–0.20). These stocks earn their long-term returns from tail rallies — fully covering them with calls will likely underperform simple buy-and-hold over time.
3. **Avoid earnings weeks**, unless you deliberately want to play the IV crush and accept assignment risk.
4. **Treat the strike price as "the price I'm willing to sell at"**, not "a price it won't reach." The right question when choosing a strike: "Would I be content selling at this level?"
5. The essence of a covered call is **trading upside potential for cash flow and reduced volatility** — optimal in sideways and mildly rising markets, worst in explosive rallies. If you are strongly bullish long-term on your tech holdings, selling fewer calls (or none) is more rational than maximizing premium.

---

# 中文版本

## 一、核心参数:怎么选行权价和到期日

主流的"标准打法"是这样的:

- **Delta 选 0.20–0.30**(即行权价在虚值 OTM 区域)。Delta 大致等于被行权的概率——0.30 delta 的 call 约有 30% 概率到期时价内。想保住股票就往 0.20 靠,想多收权利金就往 0.40 靠。
- **到期日选 30–45 天(DTE)**。45 天左右正好处于时间价值衰减(theta decay)加速的起点,比周度期权(weekly)的单位时间收益更好,且可以卖更远的行权价。
- **管理规则:到 50%–75% 最大利润就平仓,或者剩 21 天时平仓**,以先到者为准。这样能拿到大部分 theta,同时避开临近到期的 gamma 风险,然后开下一轮。

## 二、年化收益预期(关键看波动率)

权利金收入和股票的隐含波动率(IV)直接挂钩,不同科技股差别会很大:

| 持仓类型 | 典型月度收益 | 年化(仅权利金) |
|---|---|---|
| 低波动大盘科技(AAPL、MSFT) | 0.8%–1.2% | 约 10–15% |
| 中等波动(GOOGL、AMZN、META) | 1%–2% | 约 12–24% |
| 高波动(NVDA、TSLA、AMD) | 3%–5% | 30%+ 甚至更高 |

**但要泼一盆冷水**:这是"名义"权利金年化,不是真实总回报。CBOE 的 BXM 指数(S&P 500 系统性 buy-write)1988–2012 年年化约 11.8%,和标普基本持平但波动率只有 2/3——听起来不错,可是 **2012 年之后的长牛市中 BXM 大幅跑输标普**。原因很简单:covered call 封顶了上涨,而科技股的回报恰恰高度集中在少数暴涨区间。如果在 NVDA 这种股票上常年卖 call,被叫走一次错过的涨幅可能抵消一年的权利金。

## 三、常见策略变体

1. **标准月度 OTM(最常用)**:30–45 DTE、0.20–0.30 delta,机械滚动。适合打算长期持有、只想增强收益的核心仓位。
2. **部分覆盖(Partial covering)**:只对持仓的 1/3–1/2 卖 call。比如有 300 股 NVDA 只卖 1 张。牺牲一部分权利金,换取暴涨时不至于全部被封顶。**对高成长科技股这是很多人推荐的折中方案。**
3. **财报 IV Crush 打法**:财报前 IV 会被炒高(科技股平均财报后 IV 崩跌约 40%+,NVDA 接近 48%),财报前卖 call、财报后 IV 崩跌低价买回。收益高但有 gap 风险——股价跳空冲过行权价,IV crush 的好处会被完全吞掉。稳健做法是**避开财报周**,或者财报周只用很远的虚值行权价。
4. **Wheel(车轮)策略**:股票被叫走后不追高买回,改卖 cash-secured put 等回落接回,循环往复。适合不介意持仓被动进出的情况。

## 四、被突破时怎么办(Roll 滚仓)

股价涨过行权价、不想交股票时,可以**向上+向后滚仓(roll up and out)**:买回当前 call,同时卖出更高行权价、更远到期日的新 call,通常还能收净权利金。但注意:每次滚仓本质上是在平掉一笔浮亏、开一笔新仓,如果股票持续单边上涨,反复滚仓会不断压缩利润甚至累积亏损。Fidelity 的建议是:滚仓决定应基于对股票的后市判断,而不是单纯"不想认输"。**有时候最好的选择就是让股票被叫走**——这本来就是策略设计的一部分。

## 五、税务(美国)

- 权利金收益按**短期资本利得**计税(最高 37%),无论持有多久。
- 关键概念是 **Qualified Covered Call(QCC)**:到期 >30 天、且行权价不深度价内的 call 才算 qualified。卖 **OTM 的 QCC 不影响股票的长期持有期计算**;但卖**价内(ITM)call 会暂停持有期**,不合格的 covered call 甚至会把不满一年的持有期直接清零——这会影响股票以后能否享受长期资本利得税率(15%/20% vs 普通税率)。
- 实操含义:对还没满一年的持仓,只卖 30 天以上到期、虚值的 call,基本都是 qualified 的。在 IRA 等免税账户里做则完全没这个顾虑。

## 六、针对科技股持仓的综合建议

1. **对 AAPL/MSFT 这类稳健仓**:标准 30–45 DTE、0.20–0.30 delta 全覆盖,年化增强 10–15%,风险小。
2. **对 NVDA/TSLA 这类高波动高成长仓**:要么只做部分覆盖,要么用更低的 delta(0.15–0.20),因为这些股票的长期回报靠的就是尾部暴涨,全覆盖卖 call 长期大概率跑输单纯持有。
3. **避开财报周**,除非明确想赌 IV crush 且接受被叫走。
4. **心态上把行权价当成"愿意卖出的价格"**,而不是"不会到的价格"。选行权价的正确问法是:"这个价位卖掉我甘心吗?"
5. covered call 的本质是**用上涨空间换现金流和降低波动**——在横盘和温和上涨市里最优,在暴涨市里最差。如果长期强烈看多手里的科技股,少卖或不卖比多收权利金更理性。

---

# Sources / 参考来源

- [QuantWheel — Covered Call Strike Selection Guide](https://quantwheel.com/learn/covered-call-strike-selection/)
- [ApexVol — Selling Covered Calls Complete Income Guide](https://apexvol.com/learn/selling-covered-calls-guide)
- [ThetaScout — Best Stocks for Covered Calls in 2026](https://www.thetascout.com/blog/best-covered-call-stocks-2026)
- [Option Samurai — NVDA Covered Calls](https://optionsamurai.com/covered-calls/nvda/)
- [OptionsPilot — AAPL Covered Calls Guide](https://optionspilot.app/stocks/aapl-covered-calls-cash-secured-puts)
- [Fidelity — Rolling Covered Calls](https://www.fidelity.com/learning-center/investment-products/options/rolling-covered-calls)
- [Options Playbook — Rolling a Covered Call](https://www.optionsplaybook.com/managing-positions/rolling-covered-calls)
- [Fidelity — Tax Implications of Covered Calls](https://www.fidelity.com/learning-center/investment-products/options/tax-implications-covered-calls)
- [Option Samurai — Qualified Covered Calls](https://optionsamurai.com/blog/qualified-covered-calls/)
- [iPresage — Earnings IV Crush Research](https://www.ipresage.com/research/earnings-iv-crush)
- [Snider Advisors — Covered Calls Around Earnings](https://www.snideradvisors.com/blog/trade-covered-calls-around-earnings/)
- [CBOE — BXM BuyWrite Index Factsheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf)
- [Hewitt EnnisKnupp — BXM Performance Study (2012)](https://cdn.cboe.com/resources/education/research_publications/HewittEnnisKnupp-BXM-(2012).pdf)
- [R-bloggers — BXM Post-2012 Underperformance Analysis](https://www.r-bloggers.com/2019/10/calling-covered-data-2/)
- [Evolve ETFs — Covered Calls on Volatile Tech Stocks](https://evolveetfs.com/2024/10/how-covered-calls-can-help-you-navigate-volatile-tech-stocks/)
