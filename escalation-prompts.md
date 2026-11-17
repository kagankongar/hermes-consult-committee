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
| Irreversible, or touches money, safety, production data, someone else's system | ✅ | ✅ | ✅ |
| Same step already failed | after 1, before retrying or giving up | after 2, before a third attempt or giving up | after 2, before a third attempt or giving up |
| Greetings, chat, simple lookups, tool-confirmable facts | never | never | never |
| Advisors framed as | advisors | advisors | a review, possibly weaker |
| Failed advisors | ignored | ignored | ignored |
| After the result | adopt the answer of two or more agreeing advisors unless a tool result disproves it; one advisor is one opinion | resolve every disagreement explicitly | change only for a concrete, verifiable error |
| Suggested budget (Consultations per conversation) | 8 | 5 (default) | 3 |

Notes:

- System prompts bake at session start — test in a fresh session. The Escalation tier setting also reaches a running session at its next context compression or `hermes sessions refresh-prompts`.
- Budgets are per conversation and set per profile: Settings → Capabilities → Plugins → consult-committee gear → **Consultations per conversation** (High 8, Medium 5 = default, Low 3). Pair High with cheap advisors in that profile's committee preset.
- All tiers ask once per decision, and tell the model to use only facts it has and to write "unknown" for the rest, since advisors cannot ask back.
- Probe status: one short probe run on one acting model (deepseek-flash, one run per probe, 2026-11-17). See [Probe results](#probe-results). Run the [probe protocol](#probe-protocol) on your own model before trusting the firing rate.

## High — weak local models, safety/money/production

```
## Committee Escalation (high)
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to advisor models. They cannot see this conversation, run tools, or check facts.
Call it before you answer or act when any of these is true:
1. The question is expert-level (science, math, engineering, law, medicine, architecture) and needs reasoning, not a lookup.
2. The task needs a multi-step derivation, proof, or design trade-off.
3. The action is irreversible or touches money, safety, production data, or someone else's system.
4. A step (a command, test, or calculation) has failed once: consult before retrying it or before reporting it cannot be fixed.
Never call it for greetings, chat, simple lookups, or facts a search or tool can confirm; use that tool instead. Ask once per decision; do not repeat a question.
Question format: goal, every constraint and relevant data point, what was tried and how it failed. Use only facts from this conversation or tool results. If you lack a fact the advisors need, write "unknown" in the question; never fill it in. Do not include your own proposed answer, or premises or hints that point to it.
After the result: ignore advisors marked failed. State where the others agree and disagree. If two or more advisors agree with each other but not with you, adopt their answer unless a tool result proves otherwise, and say so. A single advisor's answer is one opinion: weigh it, do not defer to it. If the budget is exhausted, proceed solo and state it.
```

## Medium — recommended starting point

```
## Committee Escalation (medium)
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to advisor models. They cannot see this conversation, run tools, or check facts.
Call it before you answer or act when any of these is true:
1. The task needs a multi-step derivation, proof, or design trade-off.
2. The action is irreversible or touches money, safety, production data, or someone else's system.
3. A step (a command, test, or calculation) has failed twice: consult before a third attempt or before reporting it cannot be fixed.
Never call it for chat, simple lookups, or facts a search or tool can confirm. Ask once per decision; do not repeat a question.
Question format: goal, every constraint and relevant data point, what was tried and how it failed. Use only facts from this conversation or tool results. If you lack a fact the advisors need, write "unknown" in the question; never fill it in. Do not include your own proposed answer, or premises or hints that point to it.
After the result: ignore advisors marked failed. Resolve every disagreement between the others and yourself explicitly, naming which argument wins and why. If the budget is exhausted, proceed solo and state it.
```

## Low — strong models, tight quota

```
## Committee Escalation (low)
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to advisor models that may be weaker than you. They cannot see this conversation, run tools, or check facts. Treat their answers as a review, not an authority.
Call it only when:
1. The action is irreversible or touches money, safety, production data, or someone else's system.
2. A step (a command, test, or calculation) has failed twice: consult before a third attempt or before reporting it cannot be fixed.
Ask once per decision; do not repeat a question.
Question format: goal, every constraint and relevant data point, what was tried and how it failed. Use only facts from this conversation or tool results. If you lack a fact the advisors need, write "unknown" in the question; never fill it in. Do not include your own proposed answer, or premises or hints that point to it.
After the result: ignore advisors marked failed. Change your answer only if an advisor names a concrete error you can verify. List any objections you rejected, with the reason. If the budget is exhausted, proceed solo and state it.
```

## Probe results

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
