<!-- English edition of strike-research.md (Chinese). Keep both in sync. -->

# Covered Call Strike Research Framework (9-Dimension Method)

The execution manual for step 3 of the opening flow; equally applicable when
picking a roll's new leg — see "Applicability to rolls" at the end. Delta only
answers "how likely am I to be called away"; it does not answer whether this
stock is worth selling calls on **right now**, or **above what price** you won't
regret it. Research the 9 dimensions below, then synthesize.

**Division of labor (iron rule)**: the mechanical layer (delta band, OTM,
DTE > 30, no earnings crossing, coverage, net credit) is already enforced by the
engine and the propose guardrail — you neither need to nor may recompute it.
The research layer's job is to **choose within the deterministic candidate set,
or conclude "skip this round"**. Research cannot invent contracts outside the
set — the guardrail will reject them.

Each dimension: what to check → where → how it moves the decision
(↑strike = farther/safer, ↓DTE = shorter, fewer contracts = partial coverage,
SKIP = don't sell this round).

---

## 1. IV level (are you selling it rich?) — the most important dimension

- **Check**: where current implied volatility sits in its own history
  (IV Rank / IV Percentile). Selling covered calls is selling volatility;
  selling at low IV = selling cheap.
- **Source**: WebSearch "TICKER implied volatility rank" (Barchart/Market
  Chameleon publish it); the candidate table's annualized column is the final
  landed number.
- **Rule**: IVR < 20 → the money isn't worth capping your upside, **SKIP or cut
  contracts**; IVR 20–50 → proceed normally; IVR > 50 (common pre-event) → sell
  eagerly; the same premium buys a farther strike (↑strike).

## 2. Earnings and the event calendar (where are the landmines?)

- **Check**: next earnings date (always re-verify — dates change and sources
  disagree), product launches / investor days / guidance updates; for high-beta
  names also FOMC/CPI dates.
- **Source**: positions.json events field + WebSearch cross-check.
- **Rule**: earnings-crossing expiries are **excluded by default**; the engine's
  exception channel (delta ≤ earnings_max_delta and ≥ earnings_min_otm_pct from
  spot, the ⚠️ column in the candidate table) is only considered when "no
  non-crossing candidate exists and the premium clears dimension 9's floor",
  and the conclusion must flag the earnings risk explicitly (an earnings gap can
  eat 20% of the distance overnight; ultra-low delta ≠ zero probability).
  Earnings date not officially announced (sources conflict) → treat the whole
  window as mined, lean SKIP.

## 3. Ex-dividend date (the hidden door to early assignment)

- **Check**: any ex-dividend date before expiry; dividend amount vs the
  candidate's remaining time value.
- **Source**: WebSearch "TICKER ex-dividend date"; positions.json events.ex_div.
- **Rule**: ex-div before expiry and dividend > projected time value at that
  point → early-assignment risk is real; choose an expiry **before ex-div** or
  ↑strike to widen the distance (mandatory check for dividend payers like MSFT).

## 4. Technical resistance and 52-week high (put the strike behind a wall)

- **Check**: nearest strong resistance above spot: 52-week high, prior-high
  plateau, big round numbers.
- **Source**: WebSearch "TICKER 52 week high / resistance" — only verifiable
  public levels (the 52-week high is a fact); never draw your own lines or
  invent levels.
- **Rule**: place the strike **above strong resistance** (let the wall defend
  you); a candidate strike sitting just below a prior high → move up one notch
  (that's a magnet level, the easiest to punch through); stock just made an
  all-time high with nothing above → no wall to borrow, ↑strike and cut
  contracts.

## 5. Trend state (don't sell tickets in front of the locomotive)

- **Check**: last 1–3 months — breakout uptrend, range-bound, or basing after a
  pullback; any gap-up / post-beat momentum continuation.
- **Source**: WebSearch recent price-action coverage + distance to 52-week high
  (the candidate table's distance-to-spot column is the factual anchor).
- **Rule**: **a strong uptrend is the worst time for covered calls** (see the
  math of rolls never catching a runaway stock) → SKIP, or cut contracts +
  ↑strike; sideways chop = the best time, pick normally within the band;
  grinding downtrend → check dimension 7's cost-basis constraint first.

## 6. Analyst targets and known catalysts (where does the market think it can go?)

- **Check**: sell-side consensus target (median), any cluster of upgrades in the
  last 2 weeks, known catalysts in the next 1–2 months (products, conferences,
  industry data).
- **Source**: WebSearch "TICKER analyst price target consensus" — quote published
  numbers with attribution; never estimate your own.
- **Rule**: consensus target > 20% above spot and the candidate strike sits
  below it → ↑strike or cut contracts; targets being cut / catalyst vacuum → a
  nearer strike within the band is fine, collect more premium.

## 7. Cost basis and taxes (your own ledger — nobody else's)

- **Check**: strike vs cost basis; days to long-term capital gains; the lot's
  tax status.
- **Source**: positions.json stock.avg_cost and metrics.days_to_long_term —
  the only source.
- **Rule (disclosure; relaxed from a hard ban on 2026-07-13)**:
  strike ≤ avg_cost is **allowed** (underwater recovery mode), at the price of
  showing the **locked-in-loss math** in the conclusion: if called away,
  per-share locked loss = avg_cost − strike − cumulative premiums collected;
  state the net result. For equal premium prefer the highest strike that still
  clears dimension 9's floor; on underwater names weight dimension 5's vote
  (rebound risk) heavily — a rebound through a low strike turns a paper loss
  into a realized one; if planning to re-buy after assignment, flag the 30-day
  wash-sale window. days_to_long_term < DTE with a sizable unrealized gain →
  assignment converts long-term rates to short-term, ↑strike or SKIP until past
  the line; QCC (OTM + DTE > 30) is engine-enforced.

## 8. Liquidity (you need to get out, not just in)

- **Check**: the candidate contract's bid/ask spread and open interest. Every
  future roll/buyback pays the spread again.
- **Source**: the candidate table's bid/ask and spread% columns (deterministic);
  OI can be supplemented via WebSearch.
- **Rule**: spread > 10% → switch to an adjacent strike or a standard monthly
  expiry (best liquidity); spread > 20% → don't touch the contract. Limit orders
  near mid, always — never market orders.

## 9. Annualized-yield floor (is this round worth doing at all?)

- **Check**: after adjusting through dimensions 1–8, the final candidate's
  annualized yield (the candidate table's annualized column).
- **Source**: the candidate table, sole source.
- **Rule**: set a floor — under an ultra-low-delta policy, **annualized < 4% →
  SKIP** (capping your upside isn't worth that little; wait for IV to recover).
  **"Skip this round" is a legitimate and frequently correct conclusion** —
  sitting on your hands is not failure.

---

## Synthesis (fixed procedure)

1. Run the candidate script for the deterministic set (both --style variants
   if useful).
2. Research each dimension per the table above; each yields: finding (one line)
   + source + vote (↑strike / ↓DTE / fewer contracts / SKIP / neutral).
3. Synthesize: **any dimension tripping a hard rule (spread > 20%,
   annualized < floor) → SKIP outright or switch candidates**; a
   strike ≤ cost candidate is only eligible with dimension 7's locked-in-loss
   math attached, and on such a candidate dimension 5's rebound risk carries
   veto weight; ≥ 2 dimensions voting SKIP → don't sell this round; otherwise pick the
   candidate satisfying the most ↑strike votes, defaulting DTE to 31–45
   (steepest theta decay) unless dimensions 2/3 force shorter.
4. Output the **decision table** (template below) + final conclusion:
   (strike, expiry, contracts, limit price) or SKIP + re-check conditions.
   Include the counterargument (which dimension is most likely to make you
   regret this).
5. Only after the user confirms does the propose CLI run; you stop at the
   decision table.

### Decision table template

```
| # | Dimension | Finding | Source | Vote |
|---|-----------|---------|--------|------|
| 1 | IV level | IVR 34, mid-range | Barchart | neutral |
| 2 | Earnings | 10/28, avoided | engine + re-check | neutral |
| ... |
Conclusion: sell <expiry> $<strike>C ×<n>, limit near <mid> (annualized x.x%)
Counter: if dimension 5's breakout continues, the strike is only +x% away —
could go TESTED within two weeks
```

## Applicability to rolls

A roll's new leg is also contract selection, but not all 9 dimensions run:

- **Fully applicable, vote as usual**: 1 (IV — high IV is exactly when rolling
  collects real credit), 2 (earnings — don't roll the position into an earnings
  window), 3 (ex-dividend), 7 (cost basis & taxes — a new strike ≤ avg_cost
  likewise requires the locked-in-loss math), 8 (liquidity — buying back the old
  leg and selling the new one crosses the spread twice), 9 (annualized floor —
  hold net-credit annualized to the same floor)
- **Reference only, no veto power**: 4 (resistance), 5 (trend), 6 (targets) —
  a tested/breached position means the trend is already against you; these three
  mainly inform "how far to roll"
- **Hard vetoes in synthesis**: spread > 20%, annualized < floor → that
  candidate is disqualified; a new strike ≤ cost requires dimension 7's
  locked-in-loss math plus an honest answer to "is this roll just refusing to
  take the loss". If the whole candidate set fails, the conclusion is buy back
  or let the shares be called away — never lower the bar just to "roll out of it"

## Interfaces with the rest of the system

- All price/delta/annualized numbers come only from the candidate table;
  WebSearch provides **events and verifiable public facts** (earnings dates,
  ex-div dates, 52-week highs, price targets), never price computation.
- Research conclusions must land inside the candidate set; wanting an outside
  contract = go change --style or config, not bypass the guardrail.
- Archive the full decision table to
  `state/analysis/YYYY-MM-DD-<TICKER>-open-research.md`; the phone push body is
  the table's summary.
