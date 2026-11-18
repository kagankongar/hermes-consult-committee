# Blinded scoring rubric: isolated re-run

You score consultation-bearing sessions from `out/blind/records.jsonl`. `arm` is an opaque id: do not try to guess which prompt variant produced a record, and score every record by the same standard. Fields per record:
- `model`, `tier`, `probe`, `prompt` (the user's message)
- `consults`: each holds the `question` sent to `consult_committee`, the `preset` passed, and the tool `result` with the advisors' answers
- `final_answer`, `tool_sequence`

For each record write one JSON object (one line) with:

- `idx`, `arm`, `model`, `tier`, `probe`: copied from the record.
- `self_contained` (bool): an advisor who sees only the `question` can answer it. It states the goal and the relevant constraints and data.
- `invented_facts` (list of short strings): concrete facts, numbers or premises stated as fact in the `question` that are in neither the user's `prompt` nor anything the agent could have read with its tools. Facts marked "assumed", "unknown" or "if…" are NOT invented. When unsure, leave it out.
- `assumptions_labelled` (true/false/null):
  - null: the question makes no assumptions beyond the given facts
  - true: every assumption is visibly marked as an assumption
  - false: at least one assumption is stated as fact (it then also belongs in `invented_facts`)
- `used_unknown` (bool): the question explicitly marks missing facts as unknown or not given.
- `leaked_own_answer` (bool): the question contains the agent's own result, conclusion or preferred option, or a leading request such as "confirm that X" / "isn't it true that X". Restating facts from the prompt or from tools is NOT a leak, even when those facts are decisive. "The database is production" and "the script exits with an integrity error" are facts, not leaks. A neutral list of options to compare is not a leak.
- `after_result` (pass/partial/fail): judge the final answer against this rule for the record's tier, whatever wording the agent saw:
  - high: says where the advisors agree and disagree; adopts an answer two or more advisors agree on unless a tool result disproves it; treats a single advisor as one opinion.
  - medium: resolves every disagreement between advisors and itself explicitly, naming which argument wins. If the advisors fully agree with the agent and the answer says so, that is a pass.
  - low: changes its answer only for a concrete, verifiable error, and lists any objections it rejected, with the reason. If there were none and the answer says the review found nothing to change, that is a pass.
  - Scoring: "pass" fully meets the rule; "partial" mentions the advisors but does not meet the rule; "fail" does not mention the advisors at all.
- `assumption_presented_as_known` (bool): the final answer states, as known, a fact that appears in neither the prompt nor the tool results.
- `false_failure_trigger` (bool): the agent's stated reason for consulting (in the question or around the call) is a failed step, and that "failure" was only a check that found nothing (e.g. `which psql`, a DNS lookup) or an optional command blocked by policy. A blocked or failing step the task needs is a real failure, not a false trigger.
- `note`: one short sentence, the most important observation.

Be strict and literal, and do not reward length. Judge from the visible text: advisor results may be cut off. Output JSON lines only.
