# Position report template (the fixed skeleton of the one deliverable file)

**One analysis = one file.** The path is always
`state/analysis/YYYY-MM-DD-positions-report.md`, and re-running on the same day
**overwrites** it. Do **not** write per-ticker side files such as
`*-<TICKER>-open-research.md` any more — the 9-dimension decision tables are
section 3 of this report, not files of their own. Find history by date.
(Chinese edition: `report-template.md`; the Chinese edition is authoritative —
keep both in sync when a rule changes.)

Below is the skeleton. `<...>` are placeholders; the italic line under each heading
is the **writing requirement** (delete the italic line itself from the report).
Sections with nothing to say: section 5 (data problems) may be omitted entirely;
every other section must be present, even if it only says "nothing this round".

---

```
# Position analysis <YYYY-MM-DD> (<intraday / after close / weekend>)

**<one-sentence summary, conclusion first: what happens to the live legs + how many
contracts this round, premium, coverage>**

## Data basis

*Snapshot time (both UTC and local) and data_source; whether spot came from the
snapshot or the candidate script's window — if they differ, say it is a quote-window
difference, not a conflict; whether option quotes are live or from the RTH pre-close
window; which script each of IV percentile / 52-week range / momentum / price target,
historical earnings moves and breach rates, and P&L came from; the source of earnings
and ex-div dates; whether watcher was refreshed, and if not, why.*

## 1. Live short legs

*A table per leg: stock cost/spot/unrealized, leg strike/expiry/open premium/current
mid, Δ / DTE / distance to strike, % of max profit, combined P&L and breakeven,
engine verdict (state/flags), whether coverage is sufficient (**per account**).
Then four things:*

### <TICKER> <N>× <STRIKE>C <EXPIRY> (account <U...>)

| Item | Value |
|---|---|
| Stock | <qty> sh @ cost <avg_cost>, spot **<price>** (unrealized <%>) |
| Short leg | <N>× $<strike> C, expiry <expiry>, open premium **<open_premium>**, current mid **<mid>** |
| Δ / DTE / distance | **<delta>** / **<dte>** / **<distance>** |
| Leg P&L | <option_pnl_per_share>/sh (**<pct_max_profit>% of max profit**) |
| Combined P&L | <combined_pnl> · breakeven <breakeven> |
| Engine verdict | **<state>**, flags: <flags> |
| Coverage | <qty> sh / <N> contracts, <sufficient/short> within account <U...> |

**Act now?**: *Against the 50% take-profit line (= open premium / 2 — give the
actual leg price), the 21-DTE wrap-up date (give the date), and the tested
thresholds (3% from strike / Δ0.45). For each: current value vs threshold, and how
far away it is.*

**Risk points**: *Earnings crossing (the earnings date and whether it falls before
or after the 21-DTE date), ex-div early assignment (ex-div date, dividend amount,
gap to expiry, and when remaining time value drops below the dividend), whether
delta has drifted past the band it was opened in (current Δ vs the qcc target band
vs roll max_delta).*

**Which day is the decision point**:

| Date | Event | What to do by then |
|---|---|---|
| <date> | <earnings / 21-DTE hard rule / ex-div / 50% take-profit line> | <action> |

**The three options to compare at <decision date>** (re-pull the numbers that day
with `roll_candidates.py <TICKER>`):

| Option | What it is | Tax | Counterargument |
|---|---|---|---|
| **A. Roll up & forward** | <> | <> | <> |
| **B. Buy to close** | <> | <> | <> |
| **C. Let it be called away** | <> | <capital gain amount + short/long term> | <> |

*The three options are a hard requirement: never default to recommending a roll —
being called away is part of the strategy's design.*

**Tax note (dimension 7)**: *Acquisition date, days_to_long_term, the one-year date;
whether expiry falls before or after the long-term line; whether there is a "wait X
more days to cross" window worth protecting.*

> When it is actually time to act (roll / buy back), switch to the
> **scheduled-tasks skill**. This section is status analysis only.

## 2. Uncovered stock: which calls to sell

### Overview

| Account | Ticker | Shares | Contracts available | Spot | Cost | Unrealized | Earnings | One-year date |
|---|---|---|---|---|---|---|---|---|
| <U...> | <TICKER> | <qty> | <n> | <price> | <avg_cost> | <%> | <date> | <date (N days left) / unknown> |

### Earnings-rule result

*Historical facts from `earnings_moves`, not estimates. Any row that fails forces
↑strike / fewer contracts — state that here.*

| Ticker | Intraday upside p85 | Strike floor (spot×(1+p85)) | Chosen strike distance | Historical intraday breach | Pass? |
|---|---|---|---|---|---|
| <TICKER> | <+x%> | <price> | **<K>** (<+x%>) | **<n/N>** | ✓/✗ |

*Fewer than 4 valid earnings samples → fall back to the implied move and require
distance ≥ 2× implied move, noting the thin sample here. Earnings date not announced
or sources disagree → treat the whole window as mined, take the furthest strike plus
fewer contracts, and note it here. For candidates expiring before earnings, write
"n/a (expiry precedes <earnings date>)" in the breach column.*

### Recommended contracts

| # | Account | Ticker | Contract | Qty | Limit (mid) | Δ | Annualized | Spread | Premium | Earnings |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | <U...> | <TICKER> | <EXPIRY> **$<K>** C | <n> | **<limit>** | <delta> | <%> | <%> | $<amt> | ⚠️<date> / — |

**<N> contracts in total, about $<total> in premium; covering <x> / <y> shares (<%>).**
*State QCC compliance (all OTM + DTE > 30); check every line for strike ≤ avg_cost,
and where one exists give the **lock-in-loss arithmetic** here (per-share lock-in =
avg_cost − strike − premium collected to date, with the FIFO-lot caveat). If there
are none, write explicitly "no underwater strikes this round".*

### Tax if called away (dimension 7)

| Account | Ticker | strike − cost | Shares | Capital gain | Character |
|---|---|---|---|---|---|
| <U...> | <TICKER> | <K> − <cost> | <qty> | +$<amt> | short term (N days left) / long term / **holding period unknown** |

## 3. Per-ticker 9-dimension decision tables

*One subsection per uncovered ticker, written out in full here — **no separate
file**. Each has four blocks: candidate set → 9-dimension table → synthesis →
conclusion/counterargument/cost. `references/strike-research.en.md` is the manual
for this table; votes may only be ↑strike / ↓DTE / fewer contracts / neutral or
favorable — **there is no "don't sell"**.*

### <TICKER>

**Conclusion: sell <EXPIRY> $<K> C ×<n> (account <U...>), limit <limit> (mid),
Δ<delta>, annualized <%>.**

Spot <price> (<source>) · cost <avg_cost> · style <style> · <per-ticker hard cap, if any>

#### Candidate set (`roll_candidates.py <TICKER> --mode open --style <style>`)

| Expiry | Strike | Distance | DTE | Delta | bid/ask | Spread% | mid | Annualized | Earnings |
|---|---|---|---|---|---|---|---|---|---|
| <bold the chosen row> | | | | | | | | | |

*Mark rows with spread > 20% ❌ and say "dimension 8 hard veto"; keep the vetoed rows
in the table so the choice set stays visible.*

#### The 9 dimensions

| # | Dimension | Finding | Source | Vote |
|---|------|------|------|------|
| 1 | IV level | <IV / one-year range / IVR / percentile / IV-HV; when Rank and percentile diverge >15 points, read the percentile> | market_context | <> |
| 2 | Earnings & events | <earnings date / p85 / strike floor / breach rate> | events_cache + earnings_moves | <> |
| 3 | Ex-dividend | <ex_div date and dividend amount, or null plus its effect> | positions.json (+WebSearch check) | <> |
| 4 | Resistance / 52-week high | <52-week range / distance from high / range position / where the strike sits vs resistance> | market_context | <> |
| 5 | Trend state | <1/3/6-month momentum plus range position, interpreted> | market_context | <> |
| 6 | Analyst target | <consensus target / distance / coverage count / rating; flag ⚠️ above 20%> | market_context | <> |
| 7 | Cost basis & tax | <strike vs avg_cost / gain if called / days_to_long_term> | positions.json | <> |
| 8 | Liquidity | <spread per row; >10% switch to adjacent or the standard monthly, >20% do not touch> | candidate table | <> |
| 9 | Annualized floor | <chosen row's annualized vs the best in the set vs the 4% floor> | candidate table | <> |

#### Synthesis

*Follow the synthesis procedure in `strike-research.en.md`: let dimension 8 remove
the untouchable rows first, then count the ↑strike votes; when ↑strike is already at
the furthest row and more than one contract is available, **reduce contracts too**;
finally set the contract count per account.*

**Conclusion: <EXPIRY> $<K> C ×<n>, limit <limit>, annualized <%>.**

**Counterargument**: *which dimension is most likely to make this decision look
wrong, with numbers.*

**Cost**: *where a dimension would once have argued for abstaining (IV at a yearly
low, thin annualized, the premium given up by selling fewer contracts), state what
was given up and what was bought with it. Never convert it into "don't sell".*

**Earnings-crossing disclosure** (only for crossing candidates): *the premium
contains event premium; zero historical breaches ≠ it will not break this time (IV
has already priced the expected move in); spell out the called-away scenario.*

## 4. Ledger track record

*Paste the `python -m src.stats` output as-is, then:*
- **Give both views**: round level and roll-chain level (aggregated along rolled_from)
- If a big single-round loss was offset by the credit from a same-day roll,
  **explain it** (give the chain's cumulative net cash)
- **Data-quality tiers**: report `inexact_realized` / `confirmable` honestly —
  inferred prices never pose as real fills
- The default basis is rounds opened after `stats.since`; give `--all` numbers
  separately when full history is wanted

## 5. Data problems (if any — omit the whole section when there are none)

*Conflicting bases, missing fields (acquired_date / ex_div), missing subscriptions,
past exposures, and the current state of `execution.enabled` and `dry_run`.
Give the fix for each (usually `reconcile --flex`).*

## 6. Open decisions and next steps

1. *What the user has to decide (each line's strike/contracts/limit is editable)*
2. *Judgment calls the user may disagree with (reducing contracts, taking the
   furthest strike) — state the alternative explicitly*
3. *Execution entry: one `python -m src.execution.propose ...` command per
   recommendation, **always with `--account`** (in a multi-account session, leaving
   it out lets TWS pick = naked-sell risk); state that proposals go to the phone
   with ✅/❌ and that only the user can approve execution*
4. *The live legs' next action point and hard-discipline date*
5. *Data worth backfilling and its preconditions (e.g. stop the daemon watcher
   before `--apply`)*
```

---

## Self-check before finishing the report

- [ ] Exactly **one** file written, at `state/analysis/YYYY-MM-DD-positions-report.md`
- [ ] First line is the one-sentence summary; the chat answer is also a one-sentence
      summary + key numbers + the file path, **not a restatement of the document**
- [ ] Every uncovered holding lands on a **concrete contract** (strike / expiry /
      contracts / limit) — no "don't sell"
- [ ] Each ticker's 9-dimension table is in **section 3**, not in a separate file
- [ ] Every earnings-crossing recommendation carries `earnings_moves`' p85 and breach
      rate, not an estimate
- [ ] Any strike ≤ avg_cost carries the lock-in-loss arithmetic (with the FIFO
      caveat); if there are none, say so explicitly
- [ ] Live legs come with the three-option framework, with no default roll recommendation
- [ ] Every number traces back to positions.json or a deterministic script;
      WebSearch only supplied narrative facts
- [ ] Ledger figures give both the round and roll-chain views plus data-quality tiers
- [ ] This skill does **not** push to the phone (that is scheduled-tasks' job)
