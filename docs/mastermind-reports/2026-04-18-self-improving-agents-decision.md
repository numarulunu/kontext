# Mastermind Report: Self-Improving Agents — Architecture Decision

**Generated:** 2026-04-18 | **Council:** 7 (3 fixed + 4 dynamic) | **Challengers:** 7 | **Spec agents:** skipped per user instruction

## The Idea
Implement "self-improving agents" (Technique 6 — skill accumulation, prompt optimization, self-debugging, safety gate) on top of the existing Kontext / Tokenomy / Tool Auditor stack. Tools that sharpen themselves: prompts learn from bad outcomes, skills reinforce on wins, failed debug cycles become SCARs that prevent their own recurrence, all without babysitting.

Decision space handed to council: **A** (inside Kontext), **B** (separate plugin), **C** (extend Tokenomy/Tool Auditor), **D** (don't build).

Must-haves: local-first, probation gate, measurable delta, one-command revert, single-user n=1.

---

## Top 3 Clustered Approaches

Seven proposals clustered into five distinct positions. The three that survived challenger round:

### Approach 1 — A-minimal (signal-first)
Ship a minimum viable closed loop inside Kontext. One new table (`prompt_scores` or `rule_outcomes`), one new dream-cycle phase that reads the existing `retrieval_queries` / `tool_event` telemetry (shipped yesterday), writes proposals to a git-tracked file that the user reviews. **Crucially: v0.1 does NOT ship a scoring engine without a defined outcome signal.** Signal design is the work; the table is the plumbing.

Supported by 5 of 7 council members (Systems Strategist, Technical Architect, Measurement Scientist, Reinforcement Loop Designer, and Critical Challenger's fallback position).

### Approach 2 — D (don't build)
Existing SCAR logging via `project_{name}_log.md`, grade/decay on entries, and daily digest deliver ~75% of the practical effect. The library compounds passively. Any build time displaces Vocality revenue work — user's own stated top priority.

Supported directly by Critical Challenger; named as honest fallback by Reliability Engineer and Maintenance Cost Analyst.

### Approach 3 — A-full (Technical Architect's 3-table version)
Three tables (`skill_events`, `prompt_scores`, `self_debug_log`), new dream phase, proposals file, migration #18. More surface but all inside Kontext.

Supported only by Technical Architect in full form; four others picked scaled-down versions.

*Cut from top 3:* **B** (separate plugin) — killed by dual-DB sync divergence risk; **C** (extend Tokenomy) — killed by read-only contract break with unscoped refactor cost.

---

## Scoring

Rubric: Leverage (system intelligence unlocked), Cost (LOC + cognitive load), Blast Radius (what breaks on misfire), Reversibility (revert path). 1-5 each; average.

| # | Approach   | Leverage | Cost | Blast Radius | Reversibility | **Avg** |
|---|------------|----------|------|--------------|---------------|---------|
| 1 | D (don't build) | 2.0 | 5.0 | 5.0 | 5.0 | **4.25** |
| 2 | A-minimal (signal-first) | 3.5 | 3.0 | 3.0 | 4.0 | **3.375** |
| 3 | A-full | 3.0 | 2.0 | 2.5 | 3.5 | **2.75** |
| 4 | B (separate plugin) | 2.5 | 1.5 | 2.0 | 3.0 | **2.25** |
| 5 | C (extend Tokenomy) | 2.0 | 2.0 | 1.5 | 2.0 | **1.875** |

**Numerical winner: D.** It dominates cost/blast/reversibility because anything × zero = zero risk. That's a tell about the rubric, not the decision.

---

## Challenger Round — Key Kills

Every council verdict came back **RISKY**. The adversarial findings converged on one root flaw: **no ground-truth outcome signal exists at n=1.** The rest are downstream.

| Target | Weakest assumption killed |
|--------|---------------------------|
| **Technical Architect (A-full)** | `_improve_proposals.md` without a forcing function = log, not loop. `sessions.learned` has no baseline semantics. Proposal-quality threshold undefined. |
| **Critical Challenger (D)** | "Library compounds passively" asserted without a decay model. Conflates "don't build now" with "never build." No re-evaluation trigger. |
| **Reliability Engineer (B)** | B's "contained blast radius" only holds if sync between Kontext DB and plugin DB stays coherent. Silent divergence is worse than loud corruption. Cited revert CLI doesn't exist yet. |
| **Measurement Scientist (A)** | Frozen eval set is the load-bearing assumption — and it rots or becomes a full-time maintenance job. Non-coder user can't curate + refresh 20-30 representative queries. `recall@3` needs relevance ground truth nobody's labeling. |
| **Maintenance Cost Analyst (C)** | Tokenomy read-only contract break has unscoped refactor cost. "Near-zero month 3 overhead" collapses if the write-back refactor is non-trivial. |
| **Reinforcement Loop Designer (A)** | `type:rule` entries share vector space with memory entries. One `kontext_query` without a tag filter pollutes retrieval. Namespace collision, not elegance. |
| **Systems Strategist (A+D minimal)** | `prompt_scores` without a defined outcome signal converges to uniform noise. "Ship the table, wire signal later" = vapor feature. 2-3 hour estimate excludes the actual unknown. |

**Universal finding:** At n=1, ~1-2 sessions/day, sparse/episodic usage, **probation-via-measured-outcomes is structurally marginal.** Measurement Scientist: ~30 paired observations for d≈0.5 at 80% power via Wilcoxon = 15-30 days per change. That math kills any proposal that iterates fast.

---

## Winner: **A-minimal, signal-first (with D as explicit fallback trigger)**

D wins the rubric but loses the intent signal — the user explicitly wants the loop closed, not left open. On the challenger evidence, **A-minimal is the only viable build path** because every other build option imports A's problems plus extra coordination cost. The honest tiebreak: if you can't define an outcome signal in v0.1, A collapses to D anyway, so starting with A-minimal is free-optionality on D.

### v0.1 Scope (one paragraph, smallest shippable slice that proves the loop works)

**Build a signal harness, not a scoring engine.** v0.1 ships two things, in this order: (1) a frozen-eval replay harness — a Python module that takes a YAML file of 20 representative query→expected-top-3-files pairs, replays them against the live Kontext retrieval pipeline, emits `recall@3` + `files_loaded_delta` to a `retrieval_evals` table, and commits the YAML to git so baselines are version-pinned. This solves the "no ground truth at n=1" problem by making the user curate exactly once and refresh on a defined cadence (quarterly, or after >10% corpus growth). (2) A single new dream-cycle step that reads the existing `project_{name}_log.md` SCAR entries from the past 7 days and auto-promotes patterns appearing in ≥2 independent entries to a `_scar_promotions.md` review file — git-tracked, one-command `git checkout` revert, shown in dream's daily log line with counts so passive accumulation can't hide. **No `prompt_scores` table. No `skill_events`. No `self_debug_log`.** Ship only the harness and the SCAR auto-promotion. Both close a full loop end-to-end on day one, both have unambiguous ground truth, both inherit existing Kontext hardening patterns.

Expected LOC: ~300-500. Expected time-to-ship: 1-2 focused sessions with Claude Code.

---

## Kill Conditions (three measurable ways to abandon mid-build)

1. **Signal feasibility fails.** After the eval harness is built but before SCAR auto-promotion ships: replay 20 queries against the current retrieval pipeline, run it again unchanged 7 days later. If `recall@3` on identical queries and identical corpus drifts by >5% (i.e., noise is already larger than the effect size we'd try to detect), the measurement story is dead. Stop. Revert to D.

2. **Curation cost exceeds 1 hour/week.** If after 4 weeks of ownership, the eval YAML + SCAR review cadence consumes >1 hour/week of user time, the system is babysitting him instead of serving him. Stop. Revert to D. (This is the Maintenance Cost Analyst's "Liniste" threshold operationalized.)

3. **Sub-50% SCAR review rate.** If the user reviews fewer than 50% of auto-promoted SCARs in the first 30 days (measured by git commits touching `_scar_promotions.md`), the passive-accumulation failure mode named by Technical Architect is live. The loop is open — it's a log, not a loop. Stop. Revert to D.

Any one of these three kills is sufficient. Do not argue past them.

---

## Coverage Gaps

| Role | Status | Impact |
|------|--------|--------|
| All 7 council | ok | Full coverage |
| All 7 challengers | ok | Every verdict RISKY — strong collective signal on the n=1 signal problem |
| Phase 4 spec pass | skipped | User explicitly requested "decision + v0.1 scope only, no implementation plan" |

## Final Note on Framing

The council converged on A-minimal over pure D not because A is safer, but because A-minimal forces the signal-design work to happen in v0.1. D defers that work indefinitely — and the user's stated goal ("tools that sharpen themselves with time") cannot be met without it. A-minimal is D-plus-one-experiment: build the smallest thing that could possibly close a loop, kill it hard if any of the three conditions fire.
