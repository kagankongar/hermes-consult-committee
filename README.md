# consult-committee — a Hermes plugin

> **Why this fork exists.** The upstream repo (`070freebird070-ctrl/hermes-consult-committee`)
> has an open, unmerged bugfix for the same crash this fork fixes — PR #2
> (`fix/3-tuple-unpacking`, open since 7 Aug 2026): every `consult_committee`
> call fails on current Hermes with `ValueError: too many values to unpack`.
> With the owner unresponsive to bugfix PRs, this fork maintains a working
> version: the shape-tolerant fix (old 2-tuples and new 3-tuples) plus a
> 13-test pytest suite. Upstream PR #3 carries the same fix for review.
>
> Original plugin by [070freebird070-ctrl](https://github.com/070freebird070-ctrl);
> all core design and docs are theirs — this fork maintains the fix
> and further developments.

## Fork changes (kagankongar)

- 2026-10-26 — Shape-tolerant unpack of MoA reference outputs (2- and 3-tuples); fixes the `ValueError` crash on Hermes ≥ v0.22.5.
- 2026-10-26 — Ported isgarlo's 11-test pytest suite (upstream PR #2) + 2 back-compat cases; 13/13 green.
- 2026-10-26 — Fork banner, maintainer credit, this log; version `0.1.1-fork.1`.
- 2026-10-26 — Default preset now follows the user's configured `moa.default_preset` (e.g. `cheap`) instead of the hardcoded `default`; explicit `preset` args still win. Version `0.1.1-fork.2`.
- 2026-10-26 — Fixed a latent bug found while doing it: unknown-preset errors were never caught (`MoAPresetNotFoundError` is a `ValueError`, not a `KeyError`) and leaked to the generic fallback as "committee consultation failed".
- 2026-10-26 — Test suite grown to 16 (3 new default-preset tests); `pytest.ini` added so `pytest` runs the suite from the repo root.
- 2026-10-26 — Opt-in (off by default) plain-text committee call log (`$HERMES_HOME/logs/consult-committee.log`) via manifest v2 `config_schema` + `CONSULT_COMMITTEE_LOG` override; 8 new logging tests, 24/24 green. Version `0.1.1-fork.3`.

Full history: [CHANGELOG.md](CHANGELOG.md).

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

Then expose the toolset — add `committee` to your enabled toolsets in
`~/.hermes/config.yaml`:

```yaml
toolsets:
  - hermes-cli
  - committee
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

   > Phone a friend BEFORE you solo hard stuff. For any expert-level question
   > (science, math, engineering, architecture), anything with real stakes, or
   > any answer you feel even a little unsure about — call the
   > consult_committee tool FIRST, then blend its advice into your answer. Do
   > not answer from your own head and hope.

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
completed consultation appends one plain-text block to
`$HERMES_HOME/logs/consult-committee.log` (`~/.hermes/logs/consult-committee.log`
when `HERMES_HOME` is unset): a header line with UTC timestamp, sequence
number and requested preset, the question on one line (truncated to 4000
chars, newlines collapsed to spaces), one section per advisor with its
answer verbatim (`[FAILED]` appended when it failed), and an
answered/failed counts line, followed by a blank line.

```
=== 2026-10-26T12:00:00+00:00 | seq 1 | preset default ===
Q: What is 6 x 7?
--- advisor: m1 ---
The answer is 42.
answered: 1 failed: 0

```

**Log file path** (empty = default file). `CONSULT_COMMITTEE_LOG` env
overrides the GUI (set to a file path to enable, or `off`/empty to
disable). Log-open/write failures are ignored so logging never breaks a
call. Restart the gateway after changing settings if the log location does
not take effect immediately.
Limitation: the final aggregator decision happens in the acting model
AFTER the tool returns, so it is not in the log — only the ask and the
advisors' answers are.

## Prior art / credits

- [Amp's oracle tool](https://ampcode.com/news/oracle) — consult-a-stronger-model as a tool
- [zen-mcp-server](https://github.com/BeehiveInnovations/zen-mcp-server) — multi-model consensus tools
- [Together AI MoA paper](https://arxiv.org/abs/2406.04692) — aggregator-need-not-be-strongest
- [42-evey/hermes-plugins](https://github.com/42-evey/hermes-plugins) — plugin structure reference
- [070freebird070-ctrl](https://github.com/070freebird070-ctrl) — original plugin author; core design and docs are theirs

## License

MIT
