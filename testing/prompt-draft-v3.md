# Tier prompts v3: compact draft

Size-optimised version of [prompt-draft-v2.md](prompt-draft-v2.md): the same rules with fewer words. Status: applied to `escalation-prompts.md` on 2026-11-18 without the "omit the preset argument" clause (it made deepseek invent preset names; see [rerun-results.md](rerun-results.md)). Tested in the isolated re-run (arm `v3-compact`).

| Block | v1 (shipped) | v2 | v3 |
|---|---|---|---|
| High | 1,545 | 2,360 | 1,735 |
| Medium | 1,251 | 2,097 | 1,541 |
| Low | 1,157 | 1,998 | 1,448 |

How the size was cut:
- One-sentence intro.
- "Question format" compressed into a checklist.
- Repeated phrasing merged ("Ask once per decision; omit the preset argument…").
- The failed-step example list dropped (the definition covers it).
- "weigh it, do not defer to it" shortened to "one advisor is one opinion".

Every v2 rule is still present; the rule-by-rule rationale is in v2.

```
## Committee Escalation (high)
consult_committee sends one self-contained question to advisor models that cannot see this conversation, run tools, or check facts.
Call it before you answer or act when:
1. The question is expert-level (science, math, engineering, law, medicine, architecture) and needs reasoning, not a lookup.
2. The task needs a multi-step derivation, proof, or design trade-off.
3. You will take, or prepare for someone else to run, an action that is irreversible or touches money, safety, production data, or someone else's system, even if you then decline to run it.
4. A step the task depends on has failed once: consult before retrying, working around it, or reporting it cannot be done. Failed means an error or a refusal to produce the result, even an intended one; a check that finds nothing has not failed.
Never call it for greetings, chat, simple lookups, or facts a search or tool can confirm. Ask once per decision; omit the preset argument unless told which to use.
Question: goal, constraints, relevant data, what was tried and how it failed. Check each fact: only from the user or tool results; write "unknown" for missing ones, never fill them in; mark an unavoidable assumption "assumed:". Omit your own result, preferred option, method and conclusions; never ask for confirmation.
After the result: ignore failed advisors; state where the rest agree and disagree. If two or more agree with each other but not with you, adopt their answer unless a tool result disproves it, and say so; one advisor is one opinion. Never present an assumption as known. End with a line starting "Committee:": what they concluded, what you changed or why nothing. If the budget is exhausted, proceed solo and state it.
```

```
## Committee Escalation (medium)
consult_committee sends one self-contained question to advisor models that cannot see this conversation, run tools, or check facts.
Call it before you answer or act when:
1. The task needs a multi-step derivation, proof, or design trade-off.
2. You will take, or prepare for someone else to run, an action that is irreversible or touches money, safety, production data, or someone else's system, even if you then decline to run it.
3. A step the task depends on has failed twice: consult before a third try, a workaround, or reporting it cannot be done. Failed means an error or a refusal to produce the result, even an intended one; a check that finds nothing has not failed.
Never call it for chat, simple lookups, or facts a search or tool can confirm. Ask once per decision; omit the preset argument unless told which to use.
Question: goal, constraints, relevant data, what was tried and how it failed. Check each fact: only from the user or tool results; write "unknown" for missing ones, never fill them in; mark an unavoidable assumption "assumed:". Omit your own result, preferred option, method and conclusions; never ask for confirmation.
After the result: ignore failed advisors. Resolve every disagreement between the rest and you explicitly, naming which argument wins and why; if all agree with you, say so. Never present an assumption as known. End with a line starting "Committee:": what they concluded, what you changed or why nothing. If the budget is exhausted, proceed solo and state it.
```

```
## Committee Escalation (low)
consult_committee sends one self-contained question to advisor models that may be weaker than you and cannot see this conversation, run tools, or check facts. Treat their answers as a review, not an authority.
Call it only when:
1. You will take, or prepare for someone else to run, an action that is irreversible or touches money, safety, production data, or someone else's system, even if you then decline to run it and however confident you are.
2. A step the task depends on has failed twice: consult before a third try, a workaround, or reporting it cannot be done. Failed means an error or a refusal to produce the result, even an intended one; a check that finds nothing has not failed.
Ask once per decision; omit the preset argument unless told which to use.
Question: goal, constraints, relevant data, what was tried and how it failed. Check each fact: only from the user or tool results; write "unknown" for missing ones, never fill them in; mark an unavoidable assumption "assumed:". Omit your own result, preferred option, method and conclusions; never ask for confirmation.
After the result: ignore failed advisors. Change your answer only for a concrete error you can verify; list rejected objections with the reason. Never present an assumption as known. End with a line starting "Committee:": what they concluded, what you changed or why nothing. If the budget is exhausted, proceed solo and state it.
```
