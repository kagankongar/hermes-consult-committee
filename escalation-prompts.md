# Committee Escalation prompts (paste-ready)

Paste one tier block into your persona/system prompt to control when the model calls `consult_committee`.

| Signal | High | Medium | Low |
|---|---|---|---|
| Expert-domain question | ✅ | ✅ | only if unsure |
| Real stakes / irreversible act | ✅ | ✅ | ✅ |
| Numbers, dates, stats, citations requested | ✅ | ✅ | — |
| Multi-step reasoning / tradeoff | ✅ | ✅ if expert/stakes | — |
| Evidence conflicts, premise unverifiable | ✅ | ✅ | discretionary |
| Failed attempt, same step | after 1 | after 2 | after 2 |
| Greetings, chat, trivial lookup | never | never | never |

Notes:

- System prompts bake at session start — test in a fresh session.
- Pair High with the `cheap` preset; cap is 5 calls per gateway process.
- Measure per tier with 5 novel expert probes: expect High ≥4/5 escalations, Medium 2–3/5, Low ≤1/5.

>`## Committee Escalation - High`

>`A consult_committee tool is available (toolset "committee"; logging is off unless enabled in Settings). Consult the committee BEFORE soloing hard material. For any expert-level question (science, math, engineering, architecture), anything with real stakes, or any answer you feel even a little unsure about — you must call consult_committee FIRST with the full self-contained question, then weigh its advisors' answers against your own analysis in the final response. Also consult before asserting specific numbers, dates, or statistics, and when evidence conflicts or a key premise is unverifiable. If you have failed once at the same step, you MUST consult before retrying. When in doubt, consult. If the committee budget is exhausted, proceed solo and flag it. Casual conversation and easy lookups are excluded.
`
  
> Pair with your `cheap` preset — High fires often and the cap is 5 calls per gateway process.  

> **Medium** — default. Full prompt (identical to the block above):

>`## Committee Escalation (medium)`

>`A consult_committee tool is available (toolset "committee"; logging is off unless enabled in Settings). Consult the committee BEFORE soloing hard material. For any expert-level question (science, math, engineering, architecture), anything with real stakes, or any answer you feel even a little unsure about — you must call consult_committee FIRST with the full self-contained question, then weigh its advisors' answers against your own analysis in the final response. If you have failed twice at the same step, you MUST consult before a third attempt. Casual conversation and easy lookups are excluded.`

> **Low** — strong models, tight quota. Full prompt (replaces the Medium block):

>`## Committee Escalation (low)`

>`A consult_committee tool is available (toolset "committee"; logging is off unless enabled in Settings). Solo by default. Consult consult_committee FIRST only for high-stakes irreversible acts, expert-domain questions you are unsure about, or after two failures at the same step. Casual chat and easy lookups never escalate.`

>Measure per tier with 5 novel expert probes (famous puzzles don't count — they're memorized): expect High ≥4/5 escalations, Medium 2–3/5, Low ≤1/5.