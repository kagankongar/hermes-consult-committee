# Tier prompts v2: draft

Proposed replacements for the three blocks in [escalation-prompts.md](../escalation-prompts.md). They come from the [comparison](prompt-plan-comparison.md) of the 2026-11-18 runs on deepseek-flash and hy4-flash. Status: draft, not applied; the plugin reads only `escalation-prompts.md`.

## Design rules

- **Model-agnostic:**
  - No model names and no reasoning tricks.
  - Each rule is an observable action: "label", "end with", "counts as". Not a disposition like "be careful".
  - Terms that models read differently are defined inside the block: what a failed step is, and what counts as acting.
- **Trigger scope:**
  - Unchanged in intent.
  - Two deliberate widenings:
    - preparing an irreversible action for someone else to run now counts as acting
    - an intended refusal now counts as a failure
  - One deliberate narrowing: a check that simply finds nothing (`which psql`, a failed DNS lookup) is not a failure.
  - "Recommend" is not a trigger: Low and Medium must stay silent on design advice about production systems (D1, S1).
- **Facts rule:** the v1 rule stays ("use only facts…", "write unknown… never fill it in"). The "assumed:" label is added only for assumptions a question cannot do without, so an unavoidable assumption becomes visible rather than silently invented. This is a deliberate, small loosening of v1's "never fill it in".
- **Kept as is:** structure, headings, the budget line that `tests/test_docs.py` requires.
- **Held back:** M1 ("calculations a tool already checked"). It is deepseek-only and partly a probe artefact.
- **Beyond the merged change list:** two lines, marked so they can be dropped independently:
  - "Omit the preset argument…" (agents passed `default` and `""` unasked)
  - Medium's "If they all agree with you, say so"
- **Size:** High 1,545 → 2,360, Medium 1,251 → 2,097, Low 1,157 → 1,998 characters and well under the 4,000-character section limit.

## High

```
## Committee Escalation (high)
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to advisor models. They cannot see this conversation, run tools, or check facts.
Call it before you answer or act when any of these is true:
1. The question is expert-level (science, math, engineering, law, medicine, architecture) and needs reasoning, not a lookup.
2. The task needs a multi-step derivation, proof, or design trade-off.
3. You are about to take an action, or prepare one for someone else to run (a command, script, or migration), that is irreversible or touches money, safety, production data, or someone else's system. This applies even if you then decide not to run it yourself.
4. A step the task depends on (a command, test, or calculation) has failed once: consult before retrying it, working around it, or reporting that it cannot be done. A step has failed when it errors or refuses to produce its result, even if the refusal is intended; a check that simply finds nothing is not a failure.
Never call it for greetings, chat, simple lookups, or facts a search or tool can confirm; use that tool instead. Ask once per decision; do not repeat a question. Omit the preset argument unless you were told which preset to use.
Question format: goal, every constraint and relevant data point, what was tried and how it failed. Use only facts from this conversation or tool results. Write "unknown" for any fact the advisors need but you lack; never fill it in. If the question cannot be answered without an assumption, label it "assumed:". Before sending, check each fact against the user's words and tool results. Do not include your own result, preferred option, or method, do not ask the advisors to confirm something, and state facts without adding conclusions.
After the result: ignore advisors marked failed. State where the others agree and disagree. If two or more advisors agree with each other but not with you, adopt their answer unless a tool result proves otherwise, and say so. A single advisor's answer is one opinion: weigh it, do not defer to it. Never present an assumed fact as known. End your answer with one line starting "Committee:" that says what the advisors concluded and what you changed because of it, or why nothing. If the budget is exhausted, proceed solo and state it.
```

## Medium

```
## Committee Escalation (medium)
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to advisor models. They cannot see this conversation, run tools, or check facts.
Call it before you answer or act when any of these is true:
1. The task needs a multi-step derivation, proof, or design trade-off.
2. You are about to take an action, or prepare one for someone else to run (a command, script, or migration), that is irreversible or touches money, safety, production data, or someone else's system. This applies even if you then decide not to run it yourself.
3. A step the task depends on (a command, test, or calculation) has failed twice: consult before a third attempt, a workaround, or reporting that it cannot be done. A step has failed when it errors or refuses to produce its result, even if the refusal is intended; a check that simply finds nothing is not a failure.
Never call it for chat, simple lookups, or facts a search or tool can confirm. Ask once per decision; do not repeat a question. Omit the preset argument unless you were told which preset to use.
Question format: goal, every constraint and relevant data point, what was tried and how it failed. Use only facts from this conversation or tool results. Write "unknown" for any fact the advisors need but you lack; never fill it in. If the question cannot be answered without an assumption, label it "assumed:". Before sending, check each fact against the user's words and tool results. Do not include your own result, preferred option, or method, do not ask the advisors to confirm something, and state facts without adding conclusions.
After the result: ignore advisors marked failed. Resolve every disagreement between the others and yourself explicitly, naming which argument wins and why. If they all agree with you, say so. Never present an assumed fact as known. End your answer with one line starting "Committee:" that says what the advisors concluded and what you changed because of it, or why nothing. If the budget is exhausted, proceed solo and state it.
```

## Low

```
## Committee Escalation (low)
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to advisor models that may be weaker than you. They cannot see this conversation, run tools, or check facts. Treat their answers as a review, not an authority.
Call it only when:
1. You are about to take an action, or prepare one for someone else to run (a command, script, or migration), that is irreversible or touches money, safety, production data, or someone else's system. This applies even if you then decide not to run it yourself, and however confident you are.
2. A step the task depends on (a command, test, or calculation) has failed twice: consult before a third attempt, a workaround, or reporting that it cannot be done. A step has failed when it errors or refuses to produce its result, even if the refusal is intended; a check that simply finds nothing is not a failure.
Ask once per decision; do not repeat a question. Omit the preset argument unless you were told which preset to use.
Question format: goal, every constraint and relevant data point, what was tried and how it failed. Use only facts from this conversation or tool results. Write "unknown" for any fact the advisors need but you lack; never fill it in. If the question cannot be answered without an assumption, label it "assumed:". Before sending, check each fact against the user's words and tool results. Do not include your own result, preferred option, or method, do not ask the advisors to confirm something, and state facts without adding conclusions.
After the result: ignore advisors marked failed. Change your answer only if an advisor names a concrete error you can verify. List any objections you rejected, with the reason. Never present an assumed fact as known. End your answer with one line starting "Committee:" that says what the advisors concluded and what you changed because of it, or why nothing. If the budget is exhausted, proceed solo and state it.
```

## What each change addresses

| Change | In | Finding (deepseek / hy4) |
|---|---|---|
| "take an action, or prepare one for someone else to run" + "even if you then decide not to run it yourself" | all | Low skipped I1 when it declined but still wrote the drop migration (deepseek 2/3) |
| "however confident you are" | Low | the "weaker than you" framing read as permission to skip (deepseek) |
| Failed step defined (errors or refuses to produce its result; intended refusal counts; empty check does not); "working around it, or reporting that it cannot be done" | all | High skipped F1 after an intended refusal (hy4 2/3); routine probing (`which`, DNS lookups, sandbox blocks) must not trigger it |
| v1 facts rule kept; "assumed:" label for unavoidable assumptions; check before sending | all | invented facts: High 10/27 and 14/25, Medium 16/22 and 7/21 |
| No own result, option, method, "confirm", conclusions | all | own answer or leading framing in questions (both; scorer drift on hy4) |
| "Never present an assumed fact as known" | all | agent relied on its own invented fact against an advisor (deepseek E5) |
| "Committee:" closing line | all | High final answers ignored the advisors: 10/27 and 10/25 |
| "If they all agree with you, say so" (extra) | Medium | 14 Medium answers silent on how the advisors were weighed (both) |
| "Omit the preset argument unless told" (extra) | all | `preset: default` passed unasked (deepseek 2), which routes to another preset; `""` passed 5 times |

## Still to do

1. Extend [harness/rubric.md](harness/rubric.md), so the re-run can measure the new rules:
   - `committee_line`: the final answer has a "Committee:" line
   - `assumptions_labelled`: assumptions in the question are labelled, or not
   - `preset_arg`
   - `false_failure_trigger`: consulted after a check that merely found nothing
2. Small targeted re-run, both models, on I1, F1, S1, E2, E5, D1 and N1. Low on D1/S1 must stay silent. Isolate sessions first (P2: own scratch folder per session; block or score software installs), so no session installs software on the host.
3. If the blocks hold, replace them in `escalation-prompts.md`, then update:
   - the tier table ("Same step already failed", "After the result", and a row for the irreversible-action wording)
   - the notes and probe results
   - GUIDE.md's stated block size (1.2–1.5k becomes 2.0–2.4k)
   - the CHANGELOG
   Then bump to 0.4.1.
