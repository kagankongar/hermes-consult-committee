# consult-committee vs alternatives under Hermes Agent

Date: 2026-10-29. Scope: Hermes Agent multi-model help patterns.
Evidence: live logs + config on profile `seven`, web docs, committee cross-check (2 advisors, 2026-10-29).

## TL;DR

| Solution | Invocation | Who synthesizes | Cost per use | When it fires |
|---|---|---|---|---|
| **consult-committee** (`consult_committee` tool) | On-demand tool | Acting model itself | N (parallel refs, no aggregator call) | Only when model calls it |
| **Built-in MoA** (virtual `moa` provider) | Model selection | Configured aggregator (has tools) | N+1 **per turn** | Every turn |
| **hermes-council** (karpathy llm-council port) | Tool `council_deliberate` + `/council` + Council provider | Chairman model | ~5 lite, ~9+ full peer review | On-demand as tool; per-turn as provider |
| **advisor** plugin (ljubomirj) | `post_llm_call` hook, background worker | None — NIT/CONCERN/BLOCKER text, read-only | 1 reviewer call / turn | Every turn, automatic |
| **delegate_task** subagents | On-demand delegation | Parent agent merges | Variable, multi-step per subagent | Only when delegated |

Cost ladder per use: `advisor` (1) < `consult-committee` (N) < MoA (N+1/turn) < council (~9+) < `delegate_task` (unbounded).
Frequency split: on-demand (committee / council-tool / delegate) vs per-turn tax (MoA-provider / advisor / council-as-model).

## 1. consult-committee (this repo, fork `0.1.1-fork.6`)

How it works: the acting model calls `consult_committee(question[, preset])` mid-run.
The plugin fans out to the MoA preset's `reference_models` in parallel, truncates
each answer at 6000 chars, marks failures as `[FAILED]`, and returns labelled
answers as the tool result. **The caller is the aggregator** — it weighs the
advisors against its own analysis with full conversation context and tool access.

- Budget: max 5 consultations per gateway process, then `budget exhausted`.
- Preset resolution: `args.preset or moa.default_preset`, fallback `default`.
- Failure model: partial OK; all-failed → error. Never raises; log I/O never breaks a call.
- Logging: opt-in, off by default. `<profile-home>/logs/consult-committee.log`,
  header `=== ts | seq | profile | model | preset ===`, `Q:` + `S:` + per-advisor sections.
- Upstream gap it fills: Hermes keeps `/moa` out of the model's tool list (user-turn
  marking only, NousResearch/hermes-agent#38952). Long agent runs could never ask for help.

Verified on `seven` (2026-10-29):

- `cheap` preset: `openrouter:openai/gpt-6-luna` + `openrouter:deepseek/deepseek-v4.1-flash`.
- Log: 2 consultations, both `answered: 2 failed: 0`, no truncation hit.
- `cheap-3` preset created: adds `openrouter:z-ai/glm-5.3-flash` (already the cheap
  aggregator, proven in logs). Live test: `answered: 3 failed: 0` (3× STEADY).

Strengths: cheapest diversity; synthesis keeps context + tools; pay-only-when-used;
composes with everything below. Weaknesses: quality depends on the caller actually
weighing dissent (measured 0-for-4 self-escalation without persona rule — fixed by the
system-prompt rule in README); no cross-review, no enforced diversity, no ranking.

## 2. Built-in Mixture-of-Agents (MoA)

Virtual model provider. Each named preset appears under `moa` in every model picker
(` /model <preset> --provider moa`, `hermes model`, Dashboard, desktop dropdown).
Per turn: resolve preset → run reference models **without tool schemas** (user/assistant
text only, no system prompt or tool transcript — cheap, strict-provider-safe) → append
as private context → call aggregator **with** the normal tool schema → aggregator
response is the turn response; its tool calls execute normally.

Strengths: automatic, normal chat UX, aggregator can act. Weaknesses: N+1 on **every**
turn including trivial ones; latency stacks; no on-demand granularity; synthesis model
may not share actor state.

Choose when you want always-on multi-model quality and accept the per-turn cost.

Sources: https://hermes-agent.nousresearch.com/docs/user-guide/features/mixture-of-agents,
https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mixture-of-agents.md

## 3. hermes-council (girayk)

LLM Council semantics as a plugin. Four fixed seats answer in parallel
(First-Principles Analyst, Skeptic, Pragmatist, Researcher), then anonymized
peer review/ranking (`FINAL RANKING`, avg rank + votes; optional single Judge),
then Chairman synthesis. Seats are role-fixed; you pick which of your models sits
where (`hermes council configure`, desktop panel, `council:` block in config.yaml).
Members borrow your provider credentials — no extra keys.

Surfaces: `council_deliberate` tool, `/council <question>` slash, Council model group
(`/model default --provider council` — fan-out **per user turn**, guidance cached for
tool-loop iterations), desktop panel, `hermes council` CLI. Runs land in
`~/.hermes/council/runs/`. Hard wall-clock deadlines per stage; wedged models dropped.

Cost: `lite: true` → N+1; full peer review → ~2N+1 (≈9+ with 4 seats + chairman).
As a session model the fan-out repeats per turn — the most expensive always-on option.

Strengths: most rigorous — forced perspective coverage, anti-favoritism anonymity
(seat names/roles/model ids never reach reviewers), explicit ranking breaks anchoring,
chairman gives a decisive output. Weaknesses: slowest, costliest, fixed seats overkill
for simple questions, role scaffolding can constrain.

Choose for rare high-stakes decisions where structured debate + ranking justify ~9 calls.

Source: https://hermes-agent.nousresearch.com/docs/plugins/hermes-council

## 4. advisor plugin (ljubomirj)

Persistent second-model reviewer. `post_llm_call` hook copies the completed turn into a
one-worker background queue and returns (bounded latency, replaces stale queued turns).
One review per turn max, 90 s model timeout on the worker. Advice back into the session:
`[NIT]` immediate, `[CONCERN]`/`[BLOCKER]` held until a later review reconfirms the same
note (else dropped — no stale clutter). Read-only: never edits, calls tools, or changes state.

Config: disabled by default; `/advisor on|off|status|model|provider|...`; override needs
`plugins.entries.advisor.llm.allow_provider_override/model_override: true`;
`WATCHDOG.md` in cwd appends advisor-only guidance. State in `$HERMES_HOME/advisor/`.

Caveats: `inject_message` delivery is CLI-only (gateway platforms log only);
sees completed turns, not mid-turn thinking; 1 extra call + latency every turn;
single perspective; ignorable; possible false-positive noise.

Choose as the always-on safety/quality backstop, paired with any of the above.

Source: https://hermes-agent.nousresearch.com/docs/plugins/advisor

## 5. delegate_task subagents

The only option that does **work**, not opinions: subagents with full tools, multi-step
loops, context isolation, specialization, parallelism (model or profile routes via
advanced-delegation). Parent merges results.

Strengths: decomposable execution (files, searches, multi-step). Weaknesses: heaviest,
most variable cost, merge/reconciliation overhead, usually same-model (no diversity),
risk of divergent effort.

Choose when the answer requires execution, not just judgment.

## Pareto reading

- Cheapest diverse opinion → committee (N, no aggregator call).
- Only mid-run on-demand MoA → committee (upstream `/moa` is user-turn-only).
- Actor keeps context + tools → committee (detached aggregator/chairman lose it).
- Always-on quality, cost OK → MoA provider.
- Structured dissent + ranking → council (pays ~9+).
- Continuous backstop → advisor (1/turn, read-only).
- Real execution → delegate_task.

Proven local split: 2 refs for routine (`cheap`), 3 refs for hard calls (`cheap-3`,
tie-break + survives 1 failure at +50% cost), 4+ only ad-hoc (context bloat, ~2× cost).
They compose: advisor always on, committee for breadth, council for the rare critical
call, delegation when work must be done.

## Prior art

Amp oracle tool; zen-mcp-server consensus tools; Together AI MoA paper
(arXiv:2406.04692 — aggregator need not be strongest); 42-evey/hermes-plugins
structure reference; original plugin 070freebird070-ctrl.
