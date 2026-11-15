# Committee Escalation prompts (paste-ready)

Paste **one** tier block into your persona/system prompt to control when the model calls `consult_committee`. Each block includes its own heading. Source of truth: the README "Recall tiers" section; this file mirrors it.

| Signal | High | Medium | Low |
|---|---|---|---|
| Expert-level question that needs reasoning | ✅ | — | — |
| Multi-step derivation, proof, or design trade-off | ✅ | ✅ | — |
| Irreversible, or touches money, safety, production data, someone else's system | ✅ | ✅ | ✅ |
| Same step already failed | after 1 | after 2 | after 2 |
| Greetings, chat, simple lookups, tool-confirmable facts | never | never | never |
| Advisors framed as | stronger models | advisors | a review, possibly weaker |
| After the result | adopt advisor consensus unless a tool result disproves it | resolve every disagreement explicitly | change only for a concrete, verifiable error |

Notes:

- System prompts bake at session start — test in a fresh session.
- Pair High with the `cheap` preset; the cap is 5 calls per conversation by default (raise it with the **Consultations per conversation** setting, `max_consultations`).
- These tier prompts have not been probe-verified yet. Measure per tier with 5 novel expert probes (famous puzzles don't count — they're memorized): expect High ≥4/5 escalations, Medium 2–3/5, Low ≤1/5.

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

## Medium — default

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
After the result: change your answer only if an advisor names a concrete error you can verify. List any objections you rejected, with the reason.
```
