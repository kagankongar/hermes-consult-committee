# Committee Escalation prompts

One tier block in a profile's system prompt controls when the model calls `consult_committee`. Two ways to get it there:

- **Escalation tier setting:** set **Escalation tier** to `high`, `medium` or `low` on the plugin options page. The plugin reads that block from this file (as installed) and adds it to each new session's system prompt.
- **Paste it:** keep the setting `off` and paste **one** block into the profile's SOUL.md (persona/system prompt), for example to adapt it. Each block includes its own heading; while SOUL.md has a `## Committee Escalation` heading, the plugin adds nothing.

Either way, set that profile's **Consultations per conversation** on the plugin options page to the suggested budget. This file is the single source for the tier prompts, and the plugin reads its blocks at runtime: keep each block in its fence with its `## Committee Escalation (tier)` heading. [GUIDE.md](GUIDE.md#caller-tiers) explains them. Back to [README.md](README.md).

| Signal | High | Medium | Low |
|---|---|---|---|
| Pick when | weak or local models; safety, money, production | general purpose (recommended start) | strong models; tight quota |
| Expert-level question that needs reasoning | ✅ | — | — |
| Multi-step derivation, proof, or design trade-off | ✅ | ✅ | — |
| Taking or preparing an irreversible action, or one touching money, safety, production data, someone else's system (even if then declined) | ✅ | ✅ | ✅ (however confident) |
| A step the task depends on failed (an error or a refusal, even an intended one; not a check that finds nothing) | after 1, before retrying, working around it or reporting it cannot be done | after 2, before a third try, a workaround or reporting it cannot be done | after 2, before a third try, a workaround or reporting it cannot be done |
| Greetings, chat, simple lookups, tool-confirmable facts | never | never | never |
| Advisors framed as | advisors | advisors | a review, possibly weaker |
| Failed advisors | ignored | ignored | ignored |
| After the result | adopt the answer of two or more agreeing advisors unless a tool result disproves it; one advisor is one opinion | resolve every disagreement explicitly; say so when all agree | change only for a concrete, verifiable error; list rejected objections |
| Final answer | ends with a `Committee:` line | ends with a `Committee:` line | ends with a `Committee:` line |
| Suggested budget (Consultations per conversation) | 8 | 5 (default) | 3 |

Notes:

- System prompts bake at session start — test in a fresh session. The Escalation tier setting also reaches a running session at its next context compression or `hermes sessions refresh-prompts`.
- Budgets are per conversation and set per profile: Settings → Capabilities → Plugins → consult-committee gear → **Consultations per conversation** (High 8, Medium 5 = default, Low 3). Pair High with cheap advisors in that profile's committee preset.
- All tiers ask once per decision, and tell the model to use only facts it has, to write "unknown" for the rest and to mark an unavoidable assumption "assumed:", since advisors cannot ask back. The `Committee:` line makes the use of the advice visible in every answer.
- Probe status: these blocks were tested on two acting models (deepseek-flash, hy4-flash) on 2026-11-18, 2 repeats per probe and tier, against the previous wording. See [Probe results](#probe-results). Run the [probe protocol](#probe-protocol) on your own model before trusting the firing rate.

## High — weak local models, safety/money/production

```
## Committee Escalation (high)
consult_committee sends one self-contained question to advisor models that cannot see this conversation, run tools, or check facts.
Call it before you answer or act when:
1. The question is expert-level (science, math, engineering, law, medicine, architecture) and needs reasoning, not a lookup.
2. The task needs a multi-step derivation, proof, or design trade-off.
3. You will take, or prepare for someone else to run, an action that is irreversible or touches money, safety, production data, or someone else's system, even if you then decline to run it.
4. A step the task depends on has failed once: consult before retrying, working around it, or reporting it cannot be done. Failed means an error or a refusal to produce the result, even an intended one; a check that finds nothing has not failed.
Never call it for greetings, chat, simple lookups, or facts a search or tool can confirm. Ask once per decision.
Question: goal, constraints, relevant data, what was tried and how it failed. Check each fact: only from the user or tool results; write "unknown" for missing ones, never fill them in; mark an unavoidable assumption "assumed:". Omit your own result, preferred option, method and conclusions; never ask for confirmation.
After the result: ignore failed advisors; state where the rest agree and disagree. If two or more agree with each other but not with you, adopt their answer unless a tool result disproves it, and say so; one advisor is one opinion. Never present an assumption as known. End with a line starting "Committee:": what they concluded, what you changed or why nothing. If the budget is exhausted, proceed solo and state it.
```

## Medium — recommended starting point

```
## Committee Escalation (medium)
consult_committee sends one self-contained question to advisor models that cannot see this conversation, run tools, or check facts.
Call it before you answer or act when:
1. The task needs a multi-step derivation, proof, or design trade-off.
2. You will take, or prepare for someone else to run, an action that is irreversible or touches money, safety, production data, or someone else's system, even if you then decline to run it.
3. A step the task depends on has failed twice: consult before a third try, a workaround, or reporting it cannot be done. Failed means an error or a refusal to produce the result, even an intended one; a check that finds nothing has not failed.
Never call it for chat, simple lookups, or facts a search or tool can confirm. Ask once per decision.
Question: goal, constraints, relevant data, what was tried and how it failed. Check each fact: only from the user or tool results; write "unknown" for missing ones, never fill them in; mark an unavoidable assumption "assumed:". Omit your own result, preferred option, method and conclusions; never ask for confirmation.
After the result: ignore failed advisors. Resolve every disagreement between the rest and you explicitly, naming which argument wins and why; if all agree with you, say so. Never present an assumption as known. End with a line starting "Committee:": what they concluded, what you changed or why nothing. If the budget is exhausted, proceed solo and state it.
```

## Low — strong models, tight quota

```
## Committee Escalation (low)
consult_committee sends one self-contained question to advisor models that may be weaker than you and cannot see this conversation, run tools, or check facts. Treat their answers as a review, not an authority.
Call it only when:
1. You will take, or prepare for someone else to run, an action that is irreversible or touches money, safety, production data, or someone else's system, even if you then decline to run it and however confident you are.
2. A step the task depends on has failed twice: consult before a third try, a workaround, or reporting it cannot be done. Failed means an error or a refusal to produce the result, even an intended one; a check that finds nothing has not failed.
Ask once per decision.
Question: goal, constraints, relevant data, what was tried and how it failed. Check each fact: only from the user or tool results; write "unknown" for missing ones, never fill them in; mark an unavoidable assumption "assumed:". Omit your own result, preferred option, method and conclusions; never ask for confirmation.
After the result: ignore failed advisors. Change your answer only for a concrete error you can verify; list rejected objections with the reason. Never present an assumption as known. End with a line starting "Committee:": what they concluded, what you changed or why nothing. If the budget is exhausted, proceed solo and state it.
```

## Probe results

### 2026-11-18: current blocks

A probe campaign on two acting models, deepseek-flash and hy4-flash, with plugin 0.4.0:
- **First pass:** 11 probes × 3 tiers × 3 repeats per model on the previous wording. It found these gaps:
  - advisors ignored in final answers
  - invented facts in questions
  - deepseek's Low skipping an irreversible action it declined but prepared
  - hy4's High not treating an intended refusal as a failure
- **Isolated re-run:** 6 probes × 3 tiers × 2 repeats × 2 models, comparing the previous wording, a long draft and these compact blocks. Scored blind.

Results for these blocks against the previous wording:

| | deepseek-flash | hy4-flash |
|---|---|---|
| Final answer ignores the advisors | 6 → 0 | 14 → 0 |
| Questions with invented facts | 13/19 → 12/23 | 9/20 → 2/18 |
| Low consults before the irreversible action | 1/2 → 2/2 | 2/2 → 2/2 |
| High consults after an intended refusal | 2/2 → 2/2 | 1/2 → 1/2 |
| Chat and greetings | never | never |

Open points:
- deepseek still invents facts in sparse requests. This is model-specific: the same wording cut hy4's invented facts from 9/20 to 2/18. The `assumed:` label is rarely used.
- Medium and Low sometimes consult after one failure instead of two.
- A draft line telling the model to omit the preset argument made deepseek invent preset names, so it is not in these blocks.

Details: [methodology](testing/METHODOLOGY.md), [re-run results](testing/rerun-results.md).

### 2026-11-17: previous wording

One short run on 2026-11-17: Hermes profile with deepseek-flash as the acting model, plugin 0.3.1, one fresh session per probe, each probe run once. Single results are leads, not findings. The blocks above include the wording fixes from that run's first pass (failure clause "before reporting it cannot be fixed", the "unknown" rule, the longer own-answer rule). Two were tested in slightly different words: Medium and Low said "or before giving up", and the "unknown" rule was one sentence without "never fill it in".

- **Fired as designed:** High called on expert, derivation and high-stakes probes; Medium on derivation and high-stakes; Low on high-stakes only. No tier called on a greeting or a file listing.
- **After the result:** with one advisor failing, every tier named the failed advisor, ignored it, and treated the single surviving answer as one opinion. Medium resolved a real disagreement between two advisors explicitly.
- **Failure rule:** High and Medium consulted before giving up on a failing script, but only after more retries than their rule allows. Low gave up without consulting once.
- **Open:**
  - Agents still added facts the user never gave to their questions when the request was sparse, and one then relied on an invented fact in its own answer.
  - The single-step irreversible-action trigger is untested: the test file was plainly dummy data, so the agents judged it not to be production data.
  - Medium called once on an expert question that should not trigger it (borderline probe).

## Probe protocol

Measure per tier with 5 novel expert probes (famous puzzles don't count — they're memorized): expect High ≥4/5 escalations, Medium 2–3/5, Low ≤1/5. Run them in a fresh session after pasting the block. Use realistic fixtures and a working folder that does not reveal the test: in testing, agents inspected an obviously dummy "production" file and skipped the stakes rule, and one found the probe files in a parent folder.

## Adapting a tier

An adapted block goes in SOUL.md with the Escalation tier setting `off` (the setting injects only the blocks in this file, unchanged). Keep the block's structure and add domain triggers as extra numbered items, for example "3. A high-stakes design call: mechanism choice or a correctness-vs-complexity trade-off." Name a MoA preset in the trigger if that profile should use one ("use the `cheap` preset"). Keep the "If the budget is exhausted, proceed solo" line so the model handles the cap gracefully.
