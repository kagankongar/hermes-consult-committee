# Comparison: deepseek-flash vs hy4-flash prompt plans

The input is two model-specific plans from the 2026-11-18 campaign: [deepseek-flash](prompt-plan-deepseek-flash.md) and [hy4-flash](prompt-plan-hy4-flash.md). The output is **one shared wording** for the three tier blocks in `escalation-prompts.md`. The plugin adds the same block whatever the acting model is, so per-model versions are not an option.

## Firing side by side (deepseek → hy4, per 3 repeats)

| Probe | High | Medium | Low |
|---|---|---|---|
| E1–E5 expert | 15 → 15 /15 | 13 → 11 /15 | 0 → 0 |
| D1 design | 3 → 3 | 3 → 3 | 0 → 0 |
| I1 irreversible | 3 → 3 | 3 → 3 | **1 → 3** |
| F1 failure | **3 → 1** | 0 → 1 | 0 → 0 |
| S1 sparse | 3 → 3 | 3 → 3 | 0 → 0 |
| N1–N2 chat | 0 → 0 | 0 → 0 | 0 → 0 |

The other numbers that differ:

| Measure | deepseek | hy4 |
|---|---|---|
| Invented facts, High / Medium | 10/27, 16/22 | 14/25, 7/21 |
| High final answers ignoring the advisors | 10/27 | 10/25 |
| Expert consultations that came after computing with code, High / Medium | 10, 10 | 6, 3 |
| `preset: default` passed | 2 | 0 |
| Software installed without asking | Homebrew Postgres | pip psycopg, pypdf |

## Each change, classified

| Change | deepseek | hy4 | Class | Decision |
|---|---|---|---|---|
| Fact check plus "assumed:" label (M3 / H2) | fails | fails | **shared** | apply |
| "Committee:" closing line (M5 / H3) | fails | fails | **shared** | apply |
| Name the forbidden content: own result, option, method, "confirm that…", conclusions like "no evidence of X" (M4 / H4) | fails | likely fails* | **shared** | apply |
| "take, recommend, or prepare" an irreversible action (M2) | fails (Low 1/3) | passes (3/3) | deepseek-specific | apply: fixes deepseek, harmless for hy4 |
| "Irreversible actions always qualify" in Low (M6) | fails | passes | deepseek-specific | apply with M2, same reasoning |
| A refusal counts as a failure (H1) | passes (3/3) | fails (High 1/3) | hy4-specific | apply: fixes hy4, harmless for deepseek |
| Calculations a tool has already checked (M1) | strong signal | weak signal | deepseek-specific, partly a probe artifact | **hold** until P1 probes show whether Medium over-fires on judgement questions too |
| Medium rate on expert questions | 13/15 | 11/15 | probe design (E1–E5 are derivations) | no wording change; P1 first |

\* The two scorers judged "own answer" differently; re-score both runs with one scorer before relying on this row.

**Conflicts:** none. Every proposed change adds to the blocks; none removes or reverses what the other model needs.

## What two models taught us that one would not

- **Low on I1 is a model trait, not a wording failure on its own.** hy4 caught every I1 under the same Low wording, so M2/M6 make deepseek more robust rather than fixing a broken rule.
- **High's failure rule depends on what counts as a failure.** deepseek treated the integrity refusal as a failure and consulted; hy4 called it "by design" and did not. Only the second model revealed the gap.
- **Invented facts and advisors missing from the answer are prompt problems.** Both models do it at similar rates under identical wording.
- **The "already checked with code" explanation for Medium's high rate is deepseek's habit.** With one model it would have led to the wrong fix.

## Merged change list for the re-run

1. Failure triggers (all tiers): define failure as "exits with an error or refuses to run, even when the refusal is intended" (H1).
2. Irreversible trigger (all tiers): "take, recommend, or prepare" (M2). In Low add "always qualifies, however confident you are" (M6).
3. Question format (all tiers): fact check, label everything else "assumed:" or "unknown", and never treat an assumption as fact in the final answer (M3/H2).
4. Question format (all tiers): no own result, option, method, "confirm that…", or conclusions (M4/H4).
5. After the result (all tiers): end with a "Committee:" line (M5/H3).
6. Held: M1.

Each block must stay within the 4,000-character section limit (currently High is 1,545 characters, Medium 1,251).

## Before the re-run

- Re-score "own answer in question" for both runs with one scorer, so the M4/H4 baseline is comparable.
- Add the P1 judgement probes, isolate sessions (P2), and score "installs software without asking".
- Re-run both models on E1–E5, P1, I1, F1, S1, D1 and N1, 3 tiers × 3 repeats, then compare against runs 1 and 2.
