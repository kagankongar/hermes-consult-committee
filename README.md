# consult-committee — a Hermes plugin

**Give your local model a "phone a friend" button.** This plugin registers a `consult_committee` tool. The acting model can call it mid-run to escalate a hard question to an advisor committee: the reference models of a [Mixture-of-Agents](https://hermes-agent.nousresearch.com/docs/user-guide/features/mixture-of-agents) preset. The advisors answer in parallel, and the model writes the final answer itself. Only the MoA settings are used; MoA itself need not be active.

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

1. Pick a caller tier (High / Medium / Low) and paste its block from [escalation-prompts.md](escalation-prompts.md) into the profile's SOUL.md.
2. Set **Consultations per conversation** on the plugin options page (Settings → Capabilities → Plugins → consult-committee gear) to the suggested budget: High 8, Medium 5 (default), Low 3.
3. Start a fresh session (system prompts load at session start) and try a hard question. Saying "check with the committee" always works.

## Settings

| Option | Key | Default |
|---|---|---|
| Committee preset | `preset` | `committee` (missing → MoA's default preset) |
| Consultations per conversation | `max_consultations` | 5 (`0` disables) |
| Log committee calls | `logging` | off |
| Log file path | `log_path` | `logs/consult-committee.log` in the profile home |
| Advisor system message | `advisor_system` | bundled `advisor_system.md` |

The env vars `CONSULT_COMMITTEE_LOG` and `CONSULT_COMMITTEE_ADVISOR_SYSTEM` override the options. Details: [settings](GUIDE.md#settings-reference), [call log](GUIDE.md#committee-call-log).

## What to expect

- The budget is per conversation. Only calls that return at least one advisor answer count; failures, bad presets and stopped calls are free ([budget](GUIDE.md#budget)).
- Without a `preset` argument the tool uses the committee preset; if it does not exist, MoA's default preset, with a note. An empty preset is an error: Hermes' built-in advisors are never called silently.
- Advisor token use is recorded in the session's usage, like the main model's.
- A failed advisor is marked as failed and the rest still answer. If all fail, the tool returns an error.
- Advisors cannot see the conversation, run tools or check facts, so the model must send a self-contained question.
- Small models rarely call a help tool on their own; a tier block in SOUL.md is what makes them do it ([why](GUIDE.md#getting-the-model-to-call-it)).
- The tier prompts are not probe-verified yet ([probe protocol](escalation-prompts.md#probe-protocol)).

## Documentation

- [GUIDE.md](GUIDE.md): design, budget, presets, escalation behaviour, call log, advisor system message
- [escalation-prompts.md](escalation-prompts.md): the caller tier blocks
- [COMPARISON.md](COMPARISON.md): vs built-in MoA, council, advisor, delegation
- [CHANGELOG.md](CHANGELOG.md): releases and changes

## Credits

Fork of [070freebird070-ctrl/hermes-consult-committee](https://github.com/070freebird070-ctrl/hermes-consult-committee), the original author; the core design is theirs. Maintained here; details in [GUIDE.md](GUIDE.md#why-this-fork-exists).

## License

MIT
