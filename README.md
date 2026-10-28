# consult-committee — a Hermes plugin

**Give your local model a "phone a friend" button.** This plugin registers a
`consult_committee` tool that lets the acting model escalate a hard question to
your configured [Mixture-of-Agents](https://hermes-agent.nousresearch.com/docs/user-guide/features/mixture-of-agents)
advisor committee — mid-run, on its own judgment — instead of you having to
type `/moa` yourself.

Hermes upstream deliberately keeps `/moa` out of the model's tool list (the
slash command marks one *user* turn as MoA-enabled). That's the right default —
but it means the model can never ask for help *during* a long agent run.
This plugin fills that gap ([#38952](https://github.com/NousResearch/hermes-agent/issues/38952)
discusses the need) without touching Hermes core: it's a plain user plugin.

> Detailed comparison vs built-in MoA, hermes-council, advisor, and
> `delegate_task`: [COMPARISON.md](COMPARISON.md).

## How it works

```
you ──► local model (fast, cheap, default)
              │
              ├── easy task ──► answers directly
              │
              └── hard task ──► calls consult_committee(question)
                                   │  fans out to your MoA preset's
                                   │  reference models, in parallel
                                   ▼
                          advisors' answers returned as tool result
                                   │
              local model synthesizes ◄┘   (it IS the aggregator —
                                            with full conversation context)
```

Design choices, grounded in the MoA paper and 2026 escalation literature:

- **The model synthesizes, not a detached aggregator.** The tool returns the
  advisors' labelled answers; the calling model weighs them against its own
  analysis. Mirrors upstream MoA's "aggregator = acting model" design, saves an
  LLM call, and the caller has conversation context a detached aggregator lacks.
- **One required parameter** (`question`) — maximizes tool-call reliability on
  small local models.
- **Concrete triggers in the description, not "if unsure"** — verbalized
  uncertainty is the miscalibrated signal in small models; observable criteria
  (expert-level question, two failed attempts) work better.
- **Hard budget cap** — max 5 consultations per gateway process, then the tool
  returns a budget-exhausted error. Bounds cloud-quota burn if the model
  over-calls.
- **Graceful partial results** — a failed advisor (auth down, quota out) is
  reported as failed; the rest still answer.


## Install

```bash
git clone https://github.com/kagankongar/hermes-consult-committee \
    ~/.hermes/plugins/consult-committee
hermes plugins enable consult-committee
```
(Upstream original: `070freebird070-ctrl/hermes-consult-committee` — this fork
is the maintained copy until upstream merges the fix.)

Then expose the toolset (never hand-edit `config.yaml` — use the CLI):

```bash
hermes tools enable committee
```

Restart your gateway (`hermes gateway restart`). Verify with
`HERMES_PLUGINS_DEBUG=1 hermes plugins list`.

Requires a working MoA config — the plugin fans out to whatever advisors you
configured. With no explicit `preset` argument it follows your configured
`moa.default_preset` (falling back to the `default` preset only when the
config names no default). No keys or model names live in the plugin itself.

## Known limitation: small models under-call help (measured)

The 2026 escalation literature is unanimous: small models are overconfident
(75–92% of tested conditions) and **won't reach for a help tool on their own**
as often as they should. We reproduced this live: a 35B-class local model called
the tool reliably when nudged ("use the committee"), answered hard questions
correctly through it — but went 0-for-4 on self-escalation. It could even
*describe* the tool and its triggers perfectly when asked. Knowing ≠ doing.

Two findings from testing worth passing on:

- **Famous puzzles are useless as escalation tests.** Well-known trap questions
  (Tuesday-boy probability, Alice's-sisters, etc.) are in the training data —
  the model solves them solo, correctly, and never feels the need to escalate.
  Test with genuinely expert or novel questions.
- **A system-prompt rule fixes it — verified.** After adding the rule below to
  the model's persona file, the same model self-escalated **unprompted** on an
  expert math integral (fresh session, zero hints) and again on a hard C++20
  lock-free concurrency question in normal chat, weighing the advisors'
  answers against its own analysis in the final response.

Levers, strongest first:

1. **System-prompt rule** (recommended, verified above) — add to your
   persona/system prompt:

   > Ask a friend BEFORE soloing hard material. For any expert-level question
   > (science, math, engineering, architecture), anything with real stakes, or
   > any answer you feel even a little unsure about — you must call
   > consult_committee FIRST with the full self-contained question, then weigh
   > its advisors' answers against your own analysis in the final response. If
   > you have failed twice at the same step, you MUST consult before a third
   > attempt. Casual conversation and easy lookups are excluded.

   Note: most agents bake the system prompt in at **session start** — running
   sessions keep the old prompt, so test in a fresh session.

2. **Tool description** — already ships with concrete triggers + a mandatory
   retry tripwire ("failed twice → MUST consult").
3. **User nudge** — "check with the committee" in your message always works.

Measure your own model's escalation rate before trusting it: escalation
behavior is model-specific and unpredictable (see
[Act or Escalate, arXiv:2604.08588](https://arxiv.org/abs/2604.08588)).

## Committee call log

Logging is off by default. Turn it on in the Desktop app: Settings →
Capabilities → Plugins → consult-committee gear → **Log committee calls**
switch (or set `CONSULT_COMMITTEE_LOG` to a file path). When enabled, every
completed consultation appends one plain-text block to the asking profile's
`logs/consult-committee.log` (`<profile-home>/logs/consult-committee.log`,
resolved via profile-aware `get_hermes_home()`): a header line with UTC timestamp, sequence
number, asking profile + model when known, and requested preset, the question on one line (truncated to 4000
chars, newlines collapsed to spaces), the advisor system prompt on one `S:` line when set, one section per advisor with its
answer verbatim (`[FAILED]` appended when it failed), and an
answered/failed counts line, followed by a blank line.

```
=== 2026-10-28T07:22:50.953804+00:00 | seq 1 | profile seven | model meta-ai:muse-spark-1.3-contributor | preset default ===
Q: Are you operational and ready to answer committee questions?
S: You are a reference advisor. Only answer the question directly and concisely. No tool calls. No additional response.
--- advisor: openrouter:openai/gpt-6-luna ---
Yes—I’m ready to help answer committee questions.
--- advisor: openrouter:z-ai/glm-5.3-flash ---
Yes, I'm operational and ready. I'm serving as a reference advisor in this Mixture of Agents process — I can't execute tools, run commands, or access files/URLs myself, but I can analyze the conversation context, reason through the task state, and provide concrete guidance, next steps, and risk analysis for the acting agent to use.

Ready for the first committee question whenever you are.
answered: 2 failed: 0

```

**Log file path** (fixed default `logs/consult-committee.log`, profile-relative). `CONSULT_COMMITTEE_LOG` env
overrides the GUI (set to a file path to enable, or `off`/empty to
disable). Relative paths resolve against the asking profile home; absolute paths pass through. Log-open/write failures are ignored so logging never breaks a
call. Restart the gateway after changing settings if the log location does
not take effect immediately.
Limitation: the final aggregator decision happens in the acting model
AFTER the tool returns, so it is not in the log — only the ask and the
advisors' answers are.

## Advisor system message

Every advisor call prepends a short system prompt (default in `advisor_system.md`):
`You are a reference advisor. Only answer the question directly and concisely. No tool calls. No additional response.`
Primary editor is `advisor_system.md` in the plugin dir — use it for longer text,
since the plugin options field (**Advisor system message**) is single-line
(Hermes has no multiline settings widget yet; upstream request
[hermes-agent#145911](https://github.com/NousResearch/hermes-agent/issues/145911)).
Short edits work in the GUI field too;
env `CONSULT_COMMITTEE_ADVISOR_SYSTEM` wins, `off` disables.

## Why this fork exists

The upstream repo (`070freebird070-ctrl/hermes-consult-committee`)
has an open, unmerged bugfix for the same crash this fork fixes — PR #2
(`fix/3-tuple-unpacking`, open since 7 Aug 2026): every `consult_committee`
call fails on current Hermes with `ValueError: too many values to unpack`.
With the owner unresponsive to bugfix PRs, this fork maintains a working
version: the shape-tolerant fix (old 2-tuples and new 3-tuples) plus a
40-test pytest suite. Upstream PR #3 carries the same fix for review.

Original plugin by [070freebird070-ctrl](https://github.com/070freebird070-ctrl);
all core design and docs are theirs — this fork maintains the fix
and further developments.

> **Recent Fork Changes**
>
> - 2026-10-26 — Test suite grown to 16 (3 new default-preset tests); `pytest.ini` added so `pytest` runs the suite from the repo root.
> - 2026-10-26 — Opt-in (off by default) plain-text committee call log (`$HERMES_HOME/logs/consult-committee.log`) via manifest v2 `config_schema` + `CONSULT_COMMITTEE_LOG` override; 8 new logging tests, 24/24 green. Version `0.1.1-fork.3`.
> - 2026-10-28 — Profile-aware log default (`<profile-home>/logs/consult-committee.log` via `get_hermes_home()`), header includes asking profile + model, fixed profile-relative `logs/consult-committee.log` in settings field with relative-path resolution; 8 new identity tests, 32/32 green. Version `0.1.1-fork.4`.
> - 2026-10-28 — Advisor system message (`advisor_system.md`, editable in plugin options, env override, `off` disables); brevity verified YES + ≤7 words; 6 new tests, 38/38 green. Version `0.1.1-fork.5`.
> - 2026-10-28 — Log records `S:` system line; advisor default hardened; caller rule matches SOUL.md; `AGENTS.md` never quotes the prompt. Version `0.1.1-fork.6`.
>
> Full history: [CHANGELOG.md](CHANGELOG.md).

## Prior art / credits

- [Amp's oracle tool](https://ampcode.com/news/oracle) — consult-a-stronger-model as a tool
- [zen-mcp-server](https://github.com/BeehiveInnovations/zen-mcp-server) — multi-model consensus tools
- [Together AI MoA paper](https://arxiv.org/abs/2406.04692) — aggregator-need-not-be-strongest
- [42-evey/hermes-plugins](https://github.com/42-evey/hermes-plugins) — plugin structure reference
- [070freebird070-ctrl](https://github.com/070freebird070-ctrl) — original plugin author; core design and docs are theirs

## License

MIT
