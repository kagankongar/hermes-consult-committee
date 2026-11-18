# Isolated re-run: v1 vs v2 vs v3 tier blocks (2026-11-18)

Arms:
- **v1-control:** shipped `escalation-prompts.md`
- **v2-full:** [prompt-draft-v2.md](prompt-draft-v2.md)
- **v3-compact:** [prompt-draft-v3.md](prompt-draft-v3.md)

Size: 6 probes (I1, F1, S1, E2, D1, N1) × 3 tiers × 2 repeats × 2 acting models (deepseek-flash, hy4-flash) = 72 sessions per arm, 216 in total. Runner: [harness/run_iso.py](harness/run_iso.py); extraction: [harness/extract_iso.py](harness/extract_iso.py); rubric: [harness/rubric_iso.md](harness/rubric_iso.md).

**Isolation:**
- Package installs blocked with `approvals.deny`.
- `memory`, `session_search`, `cronjob` and `kanban` toolsets off.
- Profile scratch folder emptied before every wave of 6 parallel sessions; fresh fixture copy per session.
- Injected block length checked per session.

**Health:** 216/216 sessions exited 0, 0 injected-block mismatches, nothing installed on the host (all 11 install attempts blocked).

**Scoring:**
- Firing and the `Committee:` line: counted mechanically.
- Quality: judged blind, with arms shuffled and given opaque ids, one Claude Sonnet scorer per model.
- Correction: the deepseek scorer saw final answers cut at 8,000 characters and scored 8 as "fail". 7 of them (v2: 4, v3: 3) do have the advisors and a `Committee:` line after the cut, so they are counted as not failed below. They were not re-judged pass vs partial.

## Firing (sessions that consulted, of 2; errored preset calls excluded)

| Probe, should consult on | deepseek H | M | L | hy4 H | M | L |
|---|---|---|---|---|---|---|
| I1 irreversible: H M L | 2/2/2 | 2/2/2 | **1/2/2** | 2/2/2 | 2/2/2 | 2/2/2 |
| F1 failure: H (M, L only after 2) | 2/2/2 | 0/**2/1** | 0/**1/2** | **1/2/1** | 1/1/0 | 0/0/0 |
| S1 sparse: H M | 2/2/2 | 2/2/2 | 0/0/0 | 2/2/2 | 2/2/2 | 0/0/0 |
| E2 expert: H | 2/2/2 | 2/2/2 | 0/0/0 | 2/2/2 | 2/1/1 | 0/0/0 |
| D1 design: H M | 2/2/2 | 2/2/2 | 0/**1**/0 | 2/2/2 | 2/2/2 | 0/0/0 |
| N1 greeting: none | 0 | 0 | 0 | 0 | 0 | 0 |

Cells are v1/v2/v3.

## Quality (consulting sessions)

| Measure | deepseek v1 | v2 | v3 | hy4 v1 | v2 | v3 |
|---|---|---|---|---|---|---|
| Consulting sessions | 19 | 24 | 23 | 20 | 20 | 18 |
| Final answer ends with a `Committee:` line | 0 | 24 | 23 | 0 | 20 | 18 |
| Final answer ignores the advisors | 6 | 0 | 0 | 14 | 0 | 0 |
| Question has invented facts | 13 | 10 | 12 | 9 | 3 | 2 |
| Assumptions labelled (true / false) | 2 / 13 | 1 / 10 | 3 / 12 | 0 / 9 | 0 / 3 | 4 / 2 |
| Marks missing facts "unknown" | 15 | 21 | 18 | 14 | 15 | 11 |
| Own answer in the question | 0 | 2 | 1 | 1 | 1 | 0 |
| Final answer presents an assumption as known | 3 | 2 | 2 | 0 | 1 | 0 |
| Consulted over a non-failure | 0 | 0 | 0 | 2 | 1 | 3 |
| Errored calls with an invented preset name | 1 | 4 | 8 | 0 | 0 | 0 |

## Verdict

- **v2 and v3 perform alike.** v3 does it with 26–28% fewer characters, so v3 is the candidate.
- **Fixed by both (except where noted):**
  - advisors ignored in the final answer: 20 → 0
  - deepseek Low skipping the irreversible action
  - hy4 High skipping after an intended refusal: v2 fixed it (2 of 2); v3 stays at 1 of 2, the same as v1
  - hy4's invented facts (9 → 2–3)
- **Not fixed:** deepseek still invents facts (13/19 → 12/23). This is model-specific: under the same wording hy4 went from 9/20 to 2/18. The "assumed:" label is rarely used; models either avoid the assumption or state it as fact.
- **Side effects to resolve before shipping:**
  - **The "Omit the preset argument…" line backfires.** deepseek starts inventing preset names (`high`, `medium`, `escalation-policy`): errored calls 1 → 4 → 8. Drop the line. The preset argument is a tool-schema matter.
  - **Medium and Low consult on F1 after one failure,** where the rule asks for two (deepseek). The failure definition plus "or reporting it cannot be done" seems to pull the consult forward. Harmless but costs budget; leave as is or test a tweak.
- **Caveats:** n = 2 per cell; one fixture; LLM judge. The quality counts are indicative, not statistics.
