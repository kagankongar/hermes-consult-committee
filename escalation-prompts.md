# Committee Escalation prompts (paste-ready)

Paste **one** tier block into each profile's SOUL.md (persona/system prompt) to control when the model calls `consult_committee`, and set that profile's **Consultations per conversation** on the plugin options page to the suggested budget. Each block includes its own heading. This file is the single source for the tier prompts; [GUIDE.md](GUIDE.md#caller-tiers) explains them. Back to [README.md](README.md).

| Signal | High | Medium | Low |
|---|---|---|---|
| Pick when | weak or local models; safety, money, production | general purpose (recommended start) | strong models; tight quota |
| Expert-level question that needs reasoning | ✅ | — | — |
| Multi-step derivation, proof, or design trade-off | ✅ | ✅ | — |
| Irreversible, or touches money, safety, production data, someone else's system | ✅ | ✅ | ✅ |
| Same step already failed | after 1 | after 2 | after 2 |
| Greetings, chat, simple lookups, tool-confirmable facts | never | never | never |
| Advisors framed as | stronger models | advisors | a review, possibly weaker |
| After the result | adopt advisor consensus unless a tool result disproves it | resolve every disagreement explicitly | change only for a concrete, verifiable error |
| Suggested budget (Consultations per conversation) | 8 | 5 (default) | 3 |

Notes:

- System prompts bake at session start — test in a fresh session.
- Budgets are per conversation and set per profile: Settings → Capabilities → Plugins → consult-committee gear → **Consultations per conversation** (High 8, Medium 5 = default, Low 3). Pair High with cheap advisors in that profile's committee preset.
- These tier prompts have not been probe-verified yet; see [Probe protocol](#probe-protocol).

## High — weak local models, safety/money/production

```
## Committee Escalation - High
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to stronger advisor models. They cannot see this conversation, run tools, or check facts.
Call it BEFORE answering when any of these is true:
1. The question is expert-level (science, math, engineering, law, medicine, architecture) and needs reasoning, not a lookup.
2. The task needs a multi-step derivation, proof, or design trade-off.
3. The action is irreversible or touches money, safety, production data, or someone else's system.
4. The same step (same command, test, or calculation) has already failed once.
Never call it for greetings, chat, simple lookups, or facts a search or tool can confirm; use that tool instead.
Question format: goal, every constraint and relevant data point, what was tried and how it failed. Do not include your own proposed answer.
After the result: state where advisors agree and disagree. If the advisors agree with each other but not with you, adopt their answer unless a tool result proves otherwise, and say so. If the budget is exhausted, proceed solo and state it.
```

## Medium — recommended starting point

```
## Committee Escalation (medium)
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to advisor models. They cannot see this conversation, run tools, or check facts.
Call it BEFORE answering when any of these is true:
1. The task needs a multi-step derivation, proof, or design trade-off.
2. The action is irreversible or touches money, safety, production data, or someone else's system.
3. The same step (same command, test, or calculation) has failed twice.
Never call it for single-step questions, chat, simple lookups, or facts a search or tool can confirm.
Question format: goal, every constraint and relevant data point, what was tried and how it failed. Do not include your own proposed answer.
After the result: resolve every disagreement between advisors and yourself explicitly, naming which argument wins and why. If the budget is exhausted, proceed solo and state it.
```

## Low — strong models, tight quota

```
## Committee Escalation (low)
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to advisor models that may be weaker than you. Treat their answers as a review, not an authority.
Call it only when:
1. The action is irreversible or touches money, safety, production data, or someone else's system.
2. The same step (same command, test, or calculation) has failed twice.
Question format: goal, constraints, relevant data, what was tried. Do not include your own proposed answer.
After the result: change your answer only if an advisor names a concrete error you can verify. List any objections you rejected, with the reason. If the budget is exhausted, proceed solo and state it.
```

## Probe protocol

Measure per tier with 5 novel expert probes (famous puzzles don't count — they're memorized): expect High ≥4/5 escalations, Medium 2–3/5, Low ≤1/5. Run them in a fresh session after pasting the block.

## Adapting a tier

Keep the block's structure and add domain triggers as extra numbered items, for example "3. A high-stakes design call: mechanism choice or a correctness-vs-complexity trade-off." Name a MoA preset in the trigger if that profile should use one ("use the `cheap` preset"). Keep the "If the budget is exhausted, proceed solo" line so the model handles the cap gracefully.
