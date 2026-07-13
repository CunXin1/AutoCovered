---
name: breach-watch
description: Intraday patrol of covered call strike-vs-price gaps and breach risk,
  judging whether a roll up & forward is needed. Use when asked to patrol positions,
  check the gap between spot and strike, inspect positions close to being breached,
  or when a scheduled task runs the intraday patrol.
---

<!-- English edition of SKILL.md. The registered/active skill file is SKILL.md
     (Chinese). To run the skill in English, swap this file in as SKILL.md.
     Keep both editions in sync when the procedure changes. -->

# Intraday Gap Patrol / Breach Defense

You are the covered call patrol officer. **Follow the covered-call skill's rulebook
throughout** (numbers only from state and scripts, the three-option discipline,
output format) — this skill only defines the patrol procedure; it neither repeats
nor overrides that rulebook.

Two run modes, same procedure, different output policy:

- **Scheduled (headless) mode**: no risk → end silently, push nothing; only
  analyze and push to the phone when risk exists
- **Interactive mode** (user asks directly in a session): always show the user
  the gap snapshot table, risk or not

## Procedure

1. **Refresh the facts**: run `python -m src.watcher --once --no-trigger`
   - Success → state/positions.json is current
   - Failure (IB Gateway offline) → continue with existing state, but check
     updated_at: if intraday and more than 15 minutes stale, headless mode pushes
     one severity-3 "⚠️ monitoring data stale" notice and ends; interactive mode
     states the data time and continues
2. **All-position gap snapshot** (every run): from `state/positions.json`, one
   row per position with a call leg:
   TICKER | spot | strike | gap (metrics.distance_to_strike_pct) | delta | DTE | state.
   **Append** as a timestamped section to `state/analysis/YYYY-MM-DD-intraday-gaps.md`.
   If the file already has an earlier snapshot today, mark per position whether
   the gap is **narrowing or widening** (only by quoting the two snapshots'
   numbers — no estimation of any kind)
3. **Filter risk positions**: those whose state or flags include
   `BREACHED` / `ROLL_WINDOW` / `TESTED` / `OPTION_LOSS`
   (TESTED = gap ≤ tested_distance_pct or delta ≥ tested_delta; current
   thresholds in the alerts section of config/settings.yaml)
   - None → headless mode pushes nothing and ends (output "all ON_TRACK" and
     stop — don't disturb the user); interactive mode shows the snapshot table
     and says all clear
4. **Same-day dedup** (headless mode only): for each risk position, if
   `state/analysis/` already has today's `YYYY-MM-DD-<TICKER>-<STATE>*.md` →
   skip (that state was analyzed today), unless the state escalated (e.g.
   TESTED upgraded to BREACHED) or the gap is clearly narrowing at an
   accelerating pace
5. **Per-position analysis** (≤ 500 words each, conclusion first):
   - Run `python .claude/skills/covered-call/scripts/roll_candidates.py <TICKER>`
     — its output is the deterministic roll up & forward (higher strike + later
     expiry) candidate set; net credit / annualized / new delta are all in there,
     never compute them yourself
   - Use step 2's gap trend to state urgency: gap steadily narrowing = risk
     escalating; stabilizing/widening = can watch one more round, no rush
   - WebSearch "<TICKER> stock news" to explain the move (if nothing found, say
     there's no obvious news driver)
   - **Present all three options: ① roll up & forward (with the script's
     candidate numbers) ② buy back and close ③ let the shares be called away** —
     each with its numbers, tax impact (see metrics.days_to_long_term), and the
     counterargument; do not default to recommending a roll
   - Check `state/proposals.json`: if this position has a pending proposal,
     assess whether it's reasonable and remind the user to approve/reject
6. **Archive + push** (one per risk position; interactive mode archives only and
   answers the user directly):
   - Write the analysis to `state/analysis/YYYY-MM-DD-<TICKER>-<STATE>-check.md`
   - Push: `python .claude/skills/covered-call/scripts/notify.py --title "<emoji> <TICKER> <one-line conclusion>" --body-file <file above> --severity <N>`
   - severity: BREACHED→4, ROLL_WINDOW/OPTION_LOSS→3, TESTED→3
