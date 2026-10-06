<!-- English edition of strike-research.md (Chinese). Keep both in sync. -->

# Covered Call Strike Research Framework (9-Dimension Method)

The execution manual for step 3 of the opening flow; equally applicable when
picking a roll's new leg — see "Applicability to rolls" at the end. Delta only
answers "how likely am I to be called away"; it does not answer whether this
stock is worth selling calls on **right now**, or **above what price** you won't
regret it. Research the 9 dimensions below, then synthesize.

**Division of labor (iron rule)**: the mechanical layer (delta band, OTM,
DTE > 30, coverage, net credit) is already enforced by the
engine and the propose guardrail — you neither need to nor may recompute it.
The research layer's job is to **pick the contract to sell from within the
deterministic candidate set**. Research cannot invent contracts outside the
set — the guardrail will reject them.

**From 2026-10-02 the research no longer outputs "sell / don't sell."** Running
covered-call means selling calls, so every uncovered holding gets a recommended
contract. The nine votes answer two questions only: **how far out the strike
goes, and how many contracts**. (The "skip this round" exit is gone — it used to
cover "the premium doesn't pay for the risk", and that case is now absorbed by
pushing the strike out and cutting size, with the cost stated in the conclusion.)

Each dimension: what to check → where → how it moves the decision
(↑strike = farther/safer, ↓DTE = shorter, fewer contracts = partial coverage).
No dimension votes "don't sell".

---

## 1. IV level (are you selling it rich?) — the most important dimension

- **Check**: where current implied volatility sits in its own history
  (IV Rank / IV Percentile) and the IV-to-realized ratio (IV/HV). Selling
  covered calls is selling volatility; selling at low IV = selling cheap.
- **Source**: `python -m src.data.market_context TICKER`, computed from IBKR's
  own IV series. **Never take IV Rank from WebSearch**: sites differ in method
  and window — measured 2026-10-02, a third-party site put IBM at 11 (→ SKIP)
  while the same day computed from IBKR's series gives 55 (→ sell eagerly), and
  three sites gave AAPL 6 / 37 / 43. That is a dice roll, not a threshold. The
  candidate table's annualized column is the final landed number.
- **When Rank and percentile disagree**: Rank only reads the range endpoints, the
  percentile reads the whole distribution. A few spikes lift the upper endpoint
  and make Rank look mid-range, so **go with the percentile** — it is the one
  that answers "is today expensive". The tool warns past a 15-point divergence.
- **Rule**: IVR < 20 → you are selling cheap vol, so **↑strike to the farthest
  candidate + cut contracts**, and state the opportunity cost in the conclusion
  ("IV is near its yearly low; the same distance pays more once IV recovers");
  IVR 20–50 → proceed normally; IVR > 50 (common pre-event) → sell eagerly; the
  same premium buys a farther strike (↑strike).

## 2. Earnings and the event calendar (where are the landmines?)

- **Check**: next earnings date (always re-verify — dates change and sources
  disagree), product launches / investor days / guidance updates; for high-beta
  names also FOMC/CPI dates.
- **Source**: positions.json events field + WebSearch cross-check.
- **Rule** (revised 2026-10-02: **earnings season is not something to skip —
  raise the strike by the name's own historical earnings move instead**).
  Earnings-crossing expiries are allowed and the engine only stamps the ⚠️
  column; avoiding earnings is no longer a default preference either — in a
  dense window like late October, any compliant new leg (DTE > 31) necessarily
  crosses an earnings date, so it cannot be dodged, only priced. Order of work:

  1. **Get the historical facts first** (mandatory — never eyeball "how much
     higher is appropriate"):
     `python -m src.data.earnings_moves TICKER --strike K1 --strike K2 [--price P]`
     reports what each of the last N earnings reactions actually did (close-to-
     close and intraday-high against the prior close), the upside percentiles,
     and the **historical breach rate for each candidate strike** — how many of
     those N earnings would have pushed through it.
  2. **Strike floor**: the strike's distance from spot must be ≥ the **p85 of
     historical intraday upside moves**. Intraday rather than close is the
     conservative choice: a strike touched intraday was ITM at that moment.
  3. **Re-check the breach rate**: an earnings-crossing opening needs
     **≤1 historical breach and ≤15% of the sample** on the intraday basis.
     Above that, ↑strike until it clears, and cut contracts.
  4. **Thin sample** (fewer than 4 usable earnings, e.g. a recent listing) →
     the distribution is not dependable: fall back to the implied move and
     require a strike distance ≥ 2× it; if no candidate reaches that far, take
     the farthest one and cut contracts, saying plainly that the sample is thin.
  5. **Earnings date not officially announced** (sources conflict) → treat the
     whole window as mined: farthest candidate, fewer contracts.

  Disclosure duties that still hold: (a) an earnings-crossing candidate's
  premium contains event premium — compare it against a same-delta pre-earnings
  expiry; the extra you collect is the fee for carrying gap risk, and the
  conclusion must say whether that price is worth it (the two are now compared
  **on equal footing**, with no built-in preference for the pre-earnings
  expiry); (b) a historical distribution is not a probability guarantee — the
  sample is a handful of events and IV has already priced the market's expected
  move into the premium, so zero historical breaches ≠ it will not break this
  time; (c) the called-away scenario must appear in the conclusion, because low
  delta ≠ zero probability.

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
- **Source**: `python -m src.data.market_context TICKER` gives the 52-week high
  and low, distance from the high and position in the range, all computed from
  IBKR daily bars. WebSearch is only for narrative colour (a prior-high shelf, a
  round number) — never for a number you can compute, and never draw your own
  lines or invent levels.
- **Rule**: place the strike **above strong resistance** (let the wall defend
  you); a candidate strike sitting just below a prior high → move up one notch
  (that's a magnet level, the easiest to punch through); stock just made an
  all-time high with nothing above → no wall to borrow, ↑strike and cut
  contracts.

## 5. Trend state (don't sell tickets in front of the locomotive)

- **Check**: last 1–3 months — breakout uptrend, range-bound, or basing after a
  pullback; any gap-up / post-beat momentum continuation.
- **Source**: `python -m src.data.market_context TICKER` gives 1/3/6-month
  returns and the position in the 52-week range (deterministic); WebSearch only
  explains *why* it moved (earnings, ratings, product, macro).
- **Rule**: **a strong uptrend is the worst time for covered calls** (see the
  math of rolls never catching a runaway stock) → **↑strike to the farthest
  candidate + cut contracts**, with the called-away scenario in the conclusion;
  sideways chop = the best time, pick normally within the band;
  grinding downtrend → check dimension 7's cost-basis constraint first (premium
  does not cushion the stock falling further).

## 6. Analyst targets and known catalysts (where does the market think it can go?)

- **Check**: sell-side consensus target (median), any cluster of upgrades in the
  last 2 weeks, known catalysts in the next 1–2 months (products, conferences,
  industry data).
- **Source**: `python -m src.data.market_context TICKER` gives the consensus
  target, its range, the analyst count and the rating (via the yfinance API —
  IBKR's free tier does not carry it). WebSearch only adds narrative on recent
  clusters of upgrades/downgrades and catalysts; never estimate your own.
- **Rule**: consensus target > 20% above spot and the candidate strike sits
  below it → ↑strike or cut contracts; targets being cut / catalyst vacuum → a
  nearer strike within the band is fine, collect more premium.

## 7. Cost basis (tax is disclosed, it does not vote)

**Revised 2026-10-05: tax drops from a voting dimension to a disclosure one.** Why:
**every assignment produces a capital gain**, and that is equally true of every strike
in the candidate set, so it cannot tell you which one to pick. Meanwhile "avoid being
called away" is already what dimensions 1/2/4/5/6 (assignment probability) vote on —
letting tax vote again counts the same worry twice and turns the report into a tax
ledger. **This dimension now carries only two things:**

- **Still a hard rule: the cost line.** strike ≤ avg_cost is **allowed** (underwater
  recovery mode), at the price of showing the **locked-in-loss math** in the
  conclusion: per-share locked loss = avg_cost − strike − cumulative premiums
  collected; state the net result. That is P&L, not tax, and **is not relaxed here**.
  For equal premium prefer the highest strike that still clears dimension 9's floor;
  on underwater names weight dimension 5's vote (rebound risk) heavily — a rebound
  through a low strike turns a paper loss into a realized one. **Mind FIFO**: IBKR
  delivers the oldest lot first, so a strike above the weighted-average cost can still
  lock a loss on the earliest lot (lots come from the Flex report's LOT-level holdings).
- **Demoted to one line of disclosure: tax.** Gain if called away ((strike − cost) ×
  shares) and its short/long-term character get **one column in the report's overview
  table plus one line in the summary** — no section of their own, no paragraphs, and no
  repetition under each ticker. If a re-buy is planned after assignment, flag the
  30-day wash-sale window in one clause.
- **Vote**: this dimension votes **"disclosure"** (equivalent to neutral) by default,
  never ↑strike. **The one exception**: `days_to_long_term < DTE`, i.e. the long-term
  line falls inside this leg's life — then the tax rate genuinely differs by outcome
  ("called away = short term, survive past the line = long term") and it may cast one
  ↑strike vote. That case is rare (the line usually sits far beyond a 46-day DTE);
  do not manufacture it. QCC (OTM + DTE > 30) is engine-enforced.

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
- **Rule**: annualized yield selects the candidate; it is no longer a veto. Under
  an ultra-low-delta policy a candidate under 4% means the premium does not pay
  for the capped upside → switch within the candidate set (usually a nearer
  strike or a longer DTE) to bring it back above 4%. If every candidate on that
  name sits below 4% (common on low-vol names), take the highest and **say
  explicitly in the conclusion that the premium is thin**, so the user can see
  how much upside they are selling for how little.

---

## Synthesis (fixed procedure)

1. Run the candidate script for the deterministic set (both --style variants
   if useful).
2. Research each dimension per the table above; each yields: finding (one line)
   + source + vote (↑strike / ↓DTE / fewer contracts / neutral or favourable).
3. Synthesize: **never touch a contract with a spread > 20%** (switch to an
   adjacent strike or the standard monthly expiry); a strike ≤ cost candidate
   needs dimension 7's locked-in-loss math attached and should preferably be
   swapped for one above the cost line (remember FIFO uses the earliest lot, not
   the weighted average); **pick the candidate satisfying the most ↑strike
   votes**, defaulting DTE to 31–45 (steepest theta decay) unless dimensions 2/3
   force shorter; when ↑strike votes pile up and the name allows several
   contracts, cut size at the same time.
4. Output the **decision table** (template below) + final conclusion:
   (strike, expiry, contracts, limit price) + the counterargument (which
   dimension is most likely to make you regret this). **Every holding gets a
   conclusion; "don't sell" is not an option** — risk is expressed through strike
   distance and contract count, not through abstaining.
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
Cost: (where a dimension would once have voted "don't sell", state what is being
given up — e.g. "IV sits at its yearly low; the same distance pays X% more once
IV recovers")
```

## Applicability to rolls

A roll's new leg is also contract selection, but not all 9 dimensions run:

- **Fully applicable, vote as usual**: 1 (IV — high IV is exactly when rolling
  collects real credit), 2 (earnings — rolling into an earnings window requires
  this dimension's explicit gap-risk pricing), 3 (ex-dividend), 7 (cost basis & taxes — a new strike ≤ avg_cost
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
  or let the shares be called away — never lower the bar just to "roll out of it".
  (Note: "sell by default" applies to **openings** only. A roll may and should
  still conclude "don't roll — buy back or let it be called away"; that is not
  abstaining, it is the three-option discipline.)

## Interfaces with the rest of the system

- **Numbers come from deterministic scripts; WebSearch supplies narrative only**:
  price/delta/annualized/spread ← the candidate table (roll_candidates.py);
  historical earnings moves and breach rates ← `src.data.earnings_moves`;
  IV percentiles / 52-week high-low / period returns / analyst targets ←
  `src.data.market_context`; historical P&L ← `src.stats`.
  WebSearch's only job is **narrative fact**: why it moved, what the catalyst is,
  verifying an earnings date, the rough size of an implied move. No number that
  feeds a decision threshold may come from WebSearch — third-party sites can
  differ by 5x on the same metric (see dimension 1).
- Research conclusions must land inside the candidate set; wanting an outside
  contract = go change --style or config, not bypass the guardrail.
- Write the full decision table into **section 3 of that run's position report**
  (`state/analysis/YYYY-MM-DD-positions-report.md`; skeleton in
  `report-template.en.md`). **No more per-ticker
  `*-<TICKER>-open-research.md` files** — one analysis produces one file. When
  scheduled-tasks pushes to the phone, the body is that section's table summary.
