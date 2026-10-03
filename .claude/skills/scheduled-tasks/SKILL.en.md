---
name: scheduled-tasks
description: Runs the covered call system's scheduled jobs: the daily briefing, the
  intraday breach/gap patrol (with a roll up & forward plan when a strike is close),
  and the weekly review. Use when a scheduled job fires, or when asked to "run the
  briefing / run the patrol / run the weekly review", or "check whether anything is
  close to breaching". (Interactive position analysis and opening research belong to
  the covered-call skill.)
---

# Scheduled task runner

You are the covered call system's duty officer. This skill owns the **three
time-triggered jobs**; when the user asks in session for "analyze my positions /
what calls should I sell / give me a report", that goes to the covered-call
skill, not here.

**Follow the covered-call skill's constraints throughout** (numbers only from
state and scripts, the three-option discipline, no self-computed figures) — this
skill defines each job's flow and output policy only; it neither repeats nor
overrides that rulebook.
(Chinese edition: `SKILL.md`; the Chinese edition is authoritative — keep both in
sync when a flow changes.)

## Job dispatch

| Job | Suggested schedule | Entry file | Section |
|---|---|---|---|
| Daily briefing | Trading days 06:15 local (= 09:15 ET, pre-open) | `routines/daily-briefing.md` | 1 |
| Intraday breach/gap patrol | Every 2 hours during the session | `routines/breach-roll-check.md` | 2 |
| Weekly review | Sundays 18:00 local | `routines/weekly-review.md` | 3 |

The scheduler only fires on time; **the gating lives here**: on a non-trading day
the briefing and patrol end silently without pushing. `routines/*.md` are the
scheduler's entry points (one line pointing here); this file is authoritative for
the details.

## Two run modes

- **Scheduled (headless)**: nobody is watching a screen, so don't be noisy —
  end silently when there is no risk; archive and push only when there is a
  conclusion
- **Interactive** (the user says "run the patrol"): show the result either way,
  **archive but do not push** (the person is right there; a push is noise)

## Common step 0: refresh the facts

Every job starts with `python -m src.watcher --once --no-trigger`.

- Success → `state/positions.json` is current
- Failure (IB Gateway offline) → carry on with existing state, but check
  `updated_at`:
  - Intraday and more than 15 minutes stale: in scheduled mode push one
    severity-3 "⚠️ monitoring data stale" and stop; in interactive mode state the
    data time and continue
  - Outside the session (weekend/after close) → the last snapshot is normal;
    just state the data time

---

## 1. Daily briefing

Execute the full requirements of `prompts/daily.md`: position overview →
opening candidates for UNCOVERED holdings → overnight news → earnings/ex-div
calendar → pending proposal reminders.

1. Non-trading day (confirm via `state/positions.json`'s updated_at or WebSearch)
   → stop, no push
2. Execute `prompts/daily.md`
3. **Archive**: write the full text to `state/analysis/YYYY-MM-DD-daily.md`
4. **Push** (scheduled mode only):
   `python .claude/skills/covered-call/scripts/notify.py --title "📊 Briefing: <first-line summary>" --body-file state/analysis/YYYY-MM-DD-daily.md --severity 2`

Keep the briefing under 800 words; the first line must be a one-sentence summary
(it becomes the push title). Opening candidates are listed as candidates and
direction only — **no 9-dimension research, no final strike conclusion** here;
that is the covered-call skill's interactive flow. Tell the user in the briefing
that saying "open a call on XX" in session runs the full research + proposal flow.

## 2. Intraday breach/gap patrol

The core question: has spot moved close to a call's strike, and should it be
rolled up & forward?

1. **All-position gap snapshot** (every run): read `state/positions.json` and
   record one row per holding that has a call leg:
   TICKER | spot | strike | gap (metrics.distance_to_strike_pct) | delta | DTE |
   state. **Append** it as a timestamped section to
   `state/analysis/YYYY-MM-DD-intraday-gaps.md`. If the file already holds an
   earlier snapshot from today, mark per holding whether the gap is **narrowing
   or widening** (quote the numbers from both snapshots; compute nothing)
2. **Filter risk holdings**: those whose state or flags include
   `BREACHED` / `ROLL_WINDOW` / `TESTED` / `OPTION_LOSS`
   (TESTED = gap ≤ tested_distance_pct or delta ≥ tested_delta; current
   thresholds in the alerts section of `config/settings.yaml`)
   - None → scheduled mode pushes nothing and ends (output "all ON_TRACK");
     interactive mode shows the snapshot table and says everything is safe
3. **Same-day dedup** (scheduled mode only): for each risk holding, skip it if
   `state/analysis/` already has today's `YYYY-MM-DD-<TICKER>-<STATE>*.md` (that
   state was analyzed today), unless the state got worse than it was earlier
   (e.g. TESTED escalated to BREACHED) or the gap is narrowing noticeably faster
4. **Per-holding analysis** (≤500 words each, conclusion first):
   - Run `python .claude/skills/covered-call/scripts/roll_candidates.py <TICKER>`
     — the output is the deterministic roll up & forward candidate set (higher
     strike + later expiry) with net credit / annualized / new delta already
     computed; never compute them yourself
   - If the new leg crosses earnings: price it per the covered-call skill's
     earnings rule — run
     `python -m src.data.earnings_moves <TICKER> --strike <new strike>` for the
     historical breach rate; never judge "will it survive earnings" by feel
   - Use step 1's gap trend for urgency: steadily narrowing = risk escalating;
     stabilizing or widening = fine to watch another round, no rush
   - WebSearch "<TICKER> stock news" to explain the move (say so plainly if
     there is no clear news)
   - **Always compare three options: ① roll up & forward (with the script's
     numbers) ② buy to close ③ let the stock be called away**, each with its
     numbers, tax impact (see metrics.days_to_long_term) and the counterargument;
     never default to recommending a roll
   - Check `state/proposals.json`: if a pending proposal exists for this
     holding, assess whether it is still reasonable and prompt approve/reject
5. **Archive + push** (one per risk holding; interactive mode archives only):
   - Write the analysis to `state/analysis/YYYY-MM-DD-<TICKER>-<STATE>-check.md`
   - Push: `python .claude/skills/covered-call/scripts/notify.py --title "<emoji> <TICKER> <one-line conclusion>" --body-file <that file> --severity <N>`
   - severity: BREACHED→4, ROLL_WINDOW/OPTION_LOSS→3, TESTED→3

**Rolls have no proposal channel**: the propose CLI only supports openings. Once
the user decides to roll, tell them it must be done manually in TWS (buy back the
old leg then sell the new one, or use a combo order, limit near mid, never
market). The watcher reconciles the fills automatically; records with an inferred
price get a push asking the user to correct them with `CONFIRM <trade_id> @<price>`.

## 3. Weekly review

Execute the full requirements of `prompts/weekly.md`: week in review → next
week's roll plan → P&L accounting (including the honest "upside given up"
metric) → QCC audit → event calendar → operational checks.

1. Markets are closed on Sunday and the Gateway is probably offline — step 0
   failing is normal; use Friday's closing state and state the data time
2. Execute `prompts/weekly.md`; every P&L figure comes from
   `python -m src.stats` (note that it only counts rounds opened after
   `stats.since` by default, and mind its data-quality tiers)
3. **Archive**: write the full text to `state/analysis/YYYY-MM-DD-weekly.md`
4. **Push** (scheduled mode only):
   `python .claude/skills/covered-call/scripts/notify.py --title "📅 Weekly: <first-line summary>" --body-file state/analysis/YYYY-MM-DD-weekly.md --severity 2`

The weekly review may run longer than the briefing; its first line is likewise a
one-sentence summary.

---

## Permissions and boundaries

Depends on the `.claude/settings.json` allowlist: `watcher --once --no-trigger`,
`roll_candidates.py`, `notify.py`, `Write(state/analysis/**)`. When editing this
file or `routines/`, do not introduce commands outside the allowlist or a
headless run will stall waiting for approval.

- **No order placement, no editing proposals**: execution only happens when the
  user approves a proposal on their phone, or acts at the broker themselves
- **No final opening strike conclusions**: the 9-dimension research is the
  covered-call skill's interactive flow
- Division of labour with the Python watcher: the watcher (resident, 5-minute
  cycle) is the first line — second-level pushes on state transitions plus
  proposals with buttons. This skill is the second line, making the narrative
  judgement with news and tax context. When the watcher is not running, step 0
  here refreshes a cycle on its own.
