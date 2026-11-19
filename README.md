# consult-committee — a Hermes plugin

**Let your Hermes agent ask a committee of other models for a second opinion mid-run.** This plugin registers a `consult_committee` tool. When the agent hits a hard question, it sends one self-contained question to the advisors of a [Mixture-of-Agents](https://hermes-agent.nousresearch.com/docs/user-guide/features/mixture-of-agents) preset. They answer in parallel; the agent weighs their answers and decides itself, within a per-conversation budget. Only the MoA settings are used; MoA itself need not be active.

```
you ──► model ── hard question ──► consult_committee(question)
          ▲                              │  `committee` preset's advisors, in parallel
          └── weighs advisors' answers ◄─┘  (the model is the aggregator)
```

## Why

Hermes keeps `/moa` out of the model's tool list, so a model cannot ask for help during a long agent run ([#38952](https://github.com/NousResearch/hermes-agent/issues/38952) discusses the need). This plugin adds that as a plain user plugin, without touching Hermes core.

How it compares with built-in MoA, hermes-council, advisor and `delegate_task`: [COMPARISON.md](COMPARISON.md).

## Requirements

Hermes with an MoA preset that lists the advisors, by default named `committee`: create it with `hermes moa configure committee` ([setup](GUIDE.md#committee-preset)). No keys or model names live in the plugin.

## Install

```bash
hermes plugins install kagankongar/hermes-consult-committee --enable
```

```bash
hermes tools enable committee
```

```bash
hermes gateway restart
```

- One profile only: `hermes -p <profile> plugins install …` (and `hermes -p <profile> tools enable committee`).
- Verify with `HERMES_PLUGINS_DEBUG=1 hermes plugins list`.
- Update with `hermes plugins update consult-committee`.
- Pinning: `--ref` takes only a full 40-character commit SHA, not a tag. See [GUIDE.md](GUIDE.md#installation-details) for how.

## Quick start

1. Pick a caller tier (High / Medium / Low; see [escalation-prompts.md](escalation-prompts.md)). Set **Escalation tier** on the plugin options page (Settings → Capabilities → Plugins → consult-committee gear) and the plugin adds that tier's block to each new session's system prompt. Or keep it `off` and paste the block into the profile's SOUL.md yourself ([caller tiers](GUIDE.md#caller-tiers)).
2. On the same page, set **Consultations per conversation** to the suggested budget: High 8, Medium 5 (default), Low 3.
3. Start a fresh session (system prompts load at session start) and try a hard question. Saying "check with the committee" always works.

## Settings

| Option | Key | Default |
|---|---|---|
| Committee preset | `preset` | `committee` (missing → MoA's default preset) |
| Consultations per conversation | `max_consultations` | 5 (`0` disables) |
| Escalation tier | `escalation_tier` | `off` (`high`, `medium`, `low`: inject that tier's block) |
| Log committee calls | `logging` | off |
| Log file path | `log_path` | `logs/consult-committee.log` in the profile home |
| Advisor system message | `advisor_system` | bundled `advisor_system.md` |

The env vars `CONSULT_COMMITTEE_LOG` and `CONSULT_COMMITTEE_ADVISOR_SYSTEM` override the options. Details: [settings](GUIDE.md#settings-reference), [call log](GUIDE.md#committee-call-log).

## Examples

Suggested **Escalation tier** and **Consultations per conversation** per acting model, from probe runs on 2026-11-18/19 with plugin 0.4.1's tier blocks. A starting point; run the [probe protocol](escalation-prompts.md#probe-protocol) on your own setup ([probe results](escalation-prompts.md#probe-results), [method](testing/METHODOLOGY.md)).

| Acting model | Escalation tier | Consultations per conversation | Why |
|---|---|---|---|
| Claude Opus 5.5 | `low` | 3 | Strong model: ask only before irreversible or high-stakes actions, or after repeated failures. (Reference setting; not probe-tested.) |
| DeepSeek V4.1 Flash | `medium` | 5 | Low skipped an irreversible action; High adds little because Medium already consults on most expert questions. Question quality (invented facts) is its limit, not how often it asks. |
| Hy4 Flash (`tencent/hy4-flash`) | `medium` (`high` for professional or risky work) | 5 (8) | Follows the rules well; weaker judgement on edge cases (e.g. did not treat an intended refusal as a failure). |
| GLM 5.3 Flash (`z-ai/glm-5.3-flash`) | `high` | 8 | Consults less than the rules ask even on High (skipped an irreversible action and a design trade-off in 1 of 2 runs), and on Low skipped consulting after two failures. High brings it to roughly Medium behaviour of the others. |

Model names differ between providers (e.g. OpenRouter vs NEAR AI ids); the setting depends on the model, not the provider. Pair High with cheap advisors in the committee preset.

## What to expect

- The budget is per conversation. Only calls that return at least one advisor answer count; failures, bad presets and stopped calls are free ([budget](GUIDE.md#budget)).
- Without a `preset` argument the tool uses the committee preset; if it does not exist, MoA's default preset, with a note. An empty preset is an error: Hermes' built-in advisors are never called silently.
- Advisor token use is recorded in the session's usage, like the main model's.
- A failed advisor is marked as failed and the rest still answer. If all fail, the tool returns an error.
- Advisors cannot see the conversation, run tools or check facts, so the model must send a self-contained question.
- Small models rarely call a help tool on their own; a tier block in the system prompt (the **Escalation tier** setting or SOUL.md) is what makes them do it ([why](GUIDE.md#getting-the-model-to-call-it)).
- The tier prompts have had one short probe run on one model, not a full verification ([probe results](escalation-prompts.md#probe-results)).

## Documentation

- [GUIDE.md](GUIDE.md): design, budget, presets, escalation behaviour, call log, advisor system message
- [escalation-prompts.md](escalation-prompts.md): the caller tier blocks
- [COMPARISON.md](COMPARISON.md): vs built-in MoA, council, advisor, delegation
- [CHANGELOG.md](CHANGELOG.md): releases and changes

## Credits

Fork of [070freebird070-ctrl/hermes-consult-committee](https://github.com/070freebird070-ctrl/hermes-consult-committee), the original author; the core design is theirs. Maintained here; details in [GUIDE.md](GUIDE.md#why-this-fork-exists).

## License

MIT
