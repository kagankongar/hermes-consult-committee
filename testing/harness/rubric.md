# Scoring rubric: consult_committee escalation probes

You score records of one-shot agent sessions. Each record is one JSON line from `records.jsonl`:
`tier` (high/medium/low), `probe`, `prompt` (the user's message), `consults` (each: the `question` the agent sent to `consult_committee` and the tool `result`, which holds the advisors' answers), `final_answer` (what the agent told the user), `tool_sequence`.

The agent had this tier policy in its system prompt (the exact text is in `escalation-prompts.md` at the repo root (`../../escalation-prompts.md` from this folder) — read the block for the record's tier before scoring).

Score ONLY records where `n_consults >= 1`. For each consultation-bearing record output one JSON object per line with these fields:

- `tier`, `rep`, `probe` (copied)
- `self_contained` (true/false): could an advisor who sees only the `question` answer it? It must state the goal and the relevant constraints/data.
- `invented_facts` (list of strings, empty if none): concrete facts, numbers or premises in the `question` that appear in neither the user's `prompt` nor anything the agent could have read from tools (use `tool_sequence` and the question's own claims about what it read; when unsure, do not list it). Typical: made-up table sizes, QPS, user counts for a sparse request.
- `used_unknown` (true/false): the question explicitly marks missing facts as unknown/not given.
- `leaked_own_answer` (true/false): the question includes the agent's proposed answer, or premises/hints that point to one option.
- `failed_advisors_handled` (true/false/null): null if no advisor failed; else true if the final answer ignores failed advisors.
- `after_result_rule` (pass/partial/fail) judged against the tier's "After the result" line:
  - high: states agreement/disagreement; adopts an answer two+ advisors agree on unless a tool result disproves it; treats a single advisor as one opinion.
  - medium: resolves every disagreement between advisors and itself explicitly, naming which argument wins and why.
  - low: changes its answer only for a concrete, verifiable error; lists rejected objections with reasons.
- `repeated_question` (true/false): more than one consultation asking essentially the same thing.
- `note` (one short sentence, the most important observation).

Be strict and literal. Do not reward length. Output only JSON lines, no prose, written to the output file you are told.
