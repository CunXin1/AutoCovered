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

## Length budget (hard rule)

The report is meant to be read, not filed. **Run `wc -l` when you finish; if it is
over, cut.**

- **350 lines maximum** for the whole file (table rows included). Measured baseline:
  6 uncovered tickers + 1 live leg = 348 lines (the 2026-10-05 report). With more
  tickers, allow **+28 lines per extra ticker**, with a **hard ceiling of 450**
- **Section 3: 28 lines per ticker.** Within those 28, the 9-dimension table (11
  lines) and the candidate table (5–7) are the **floor** and do not compress; save
  space on prose instead (conclusion / synthesis / counter-case / cost / earnings
  disclosure ≤ 5 lines in total, one line each)
- Ceilings elsewhere: summary ≤ 20 · data basis ≤ 10 (at most 5 bullets) · each live
  leg in section 1 ≤ 40 · section 2 ≤ 40 · section 4 ≤ 18 · section 5 ≤ 12 ·
  section 6 ≤ 45
- Priority when it will not fit (highest first): **summary table > section 2's number
  tables > section 3's 9-dimension tables > prose**
- **State each fact once**: contracts / sizes / limits / annualized already in the
  summary table are referenced, not repeated, in sections 2 and 3; section 2 carries
  **no separate "recommended contracts" table** (the summary table at the top is it);
  in section 1 the discipline lines and the decision dates go in **one** table
  (threshold / current value / what to do that day), not two
- **Tax gets one column and one line, never a section**: gain if called away and its
  short/long-term character go in the last two columns of section 2's overview table,
  one line for the total, one line in the summary — no paragraphs and no repetition per
  ticker (it is identical for every candidate)
- **Never restate this skill's rule text** in the report ("rule: IVR<20 → ↑strike +
  reduce size" and the like). Write only what was found and decided for this ticker —
  the rules live in the skill, and the reader does not need to learn them twice
- 9-dimension table: **one sentence per cell** in the Finding column; the Vote column
  is the vote name plus a parenthetical of at most six words
- Candidate table: keep **the chosen row + 2–4 comparison rows** (keep 1–2 of the rows
  vetoed by dimension 8, marked ❌, so the reader sees how narrow the choice was) —
  not the script's whole output pasted in
- Three-option, decision-point and open-decision tables: **one sentence per cell**
- **Consecutive prose lines become `- ` bullets**, never a stacked paragraph: adjacent
  `**Synthesis**:` / `**Counter-case**:` lines without bullets render as one run-on
  paragraph and the reader cannot tell them apart

---

```
# Position analysis <YYYY-MM-DD> (<intraday / after close / weekend>)

**<one-sentence summary, conclusion first: what happens to the live legs + how many
contracts this round, premium, coverage>**

## Summary: what to sell this round

*The first table in the file, straight after the one-sentence summary and **before
anything else**. It is the list of recommended contracts — section 2 does not repeat
it. The three bullets after it are one line each.*

| Ticker | Contract | Qty (account) | Limit (mid) | Δ | Annualized |
|---|---|---|---|---|---|
| <TICKER> | <EXPIRY> **$<K>** C | <n> (<U...>) | **<limit>** | <delta> | <%> |

**<N> contracts total · about $<total> in premium · covering <x>/<y> shares (<%>)**

**Live legs**: <TICKER> <N>×<K>C <EXPIRY> — <state>, next decision point <date>
(<event>), <act / stand pat> this week.

- **Best of the round**: <ticker + annualized + one-clause reason>
- **The one to watch**: <ticker + why (with numbers) + what was done about it
  (↑strike / reduced size)>
- **Cost if everything is called away**: <total capital gain + short/long term>

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

*Tax: one sentence per option in the Tax column of the three-option table above, and
**no separate "tax note" block**. Only `days_to_long_term < DTE` (the long-term line
falling inside this leg's life) earns an extra line.*

> When it is actually time to act (roll / buy back), switch to the
> **scheduled-tasks skill**. This section is status analysis only.

## 2. Uncovered stock: which calls to sell

### Overview

| Account | Ticker | Shares | Spot | Cost | Unrealized | Earnings | One year (left) | Gain if called |
|---|---|---|---|---|---|---|---|---|
| <U...> | <TICKER> | <qty> | <price> | <avg_cost> | <%> | <date> | <date (N)> / unknown | +$<amt> (short/long) |

*Those last two columns are **all** the space dimension 7 gets: gain if called away
plus its short/long-term character. At most **one** further line after the table for
the total (capital gain if everything is called away, and its character) — **no
paragraph**: every assignment produces a capital gain, equally true of every candidate,
so it is not a selection criterion.*

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

### Compliance and underwater check

*The contract list lives in the summary table at the top; **do not repeat it here**.
This subsection carries only three things, at most 2 lines each:*
- *QCC compliance statement: all OTM + opening DTE > 30 (give the nearest strike's
  distance to spot and its DTE)*
- *If the whole book's expiry/spread choice shares one reason (e.g. all standard
  monthlies), state it **once** here; section 3 does not repeat it*
- *Check every line for strike ≤ avg_cost: where one exists give the **lock-in-loss
  arithmetic** (per-share lock-in = avg_cost − strike − premium collected to date,
  with the FIFO-lot caveat); if there are none, one line saying "no underwater
  strikes this round"*

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
| 7 | Cost basis (tax disclosed only) | <strike vs avg_cost; underwater → locked-in-loss math, otherwise one clause "+x% above cost"> | positions.json | **disclosure** (unless days_to_long_term < DTE) |
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

- [ ] **The whole file is ≤ 350 lines** (actually count it: `wc -l`; +28 per extra ticker, hard ceiling 450); if over, cut prose first
- [ ] **The summary table is at the very top** (right after the one-sentence summary),
      and sections 2 and 3 do not repeat contracts / sizes / limits
- [ ] The report does **not** restate the skill's rule text — only findings and
      conclusions for these tickers
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
- [ ] Tax appears only as one overview-table column plus one summary/total line —
      **no tax section and no tax paragraphs**
- [ ] Live legs come with the three-option framework, with no default roll recommendation
- [ ] Every number traces back to positions.json or a deterministic script;
      WebSearch only supplied narrative facts
- [ ] Ledger figures give both the round and roll-chain views plus data-quality tiers
- [ ] This skill does **not** push to the phone (that is scheduled-tasks' job)
