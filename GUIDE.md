# consult-committee — Guide

Extended documentation for the `consult_committee` Hermes plugin. For the short overview, see [README.md](README.md).

- [How it works](#how-it-works) · [Design choices](#design-choices)
- [Installation details](#installation-details): [updating](#updating), [pinning a release](#pinning-a-release), [manual clone](#manual-clone)
- [Settings reference](#settings-reference) · [Which preset is used](#which-preset-is-used) · [Budget](#budget)
- [Getting the model to call it](#getting-the-model-to-call-it): [caller tiers](#caller-tiers), [levers](#levers), [verified earlier rule](#verified-earlier-rule)
- [Committee call log](#committee-call-log) · [Advisor system message](#advisor-system-message)
- [Why this fork exists](#why-this-fork-exists) · [Prior art and credits](#prior-art-and-credits)

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

Why the model, not a separate aggregator, writes the final answer: see [Design choices](#design-choices). A detailed comparison with built-in MoA, hermes-council, advisor, and `delegate_task` is in [COMPARISON.md](COMPARISON.md).

## Design choices

Grounded in the MoA paper and 2026 escalation literature:

- **The model synthesizes, not a detached aggregator.** The tool returns the advisors' labelled answers; the calling model weighs them against its own analysis. Mirrors upstream MoA's "aggregator = acting model" design, saves an LLM call, and the caller has conversation context a detached aggregator lacks.

- **One required parameter** (`question`) — maximizes tool-call reliability on small local models. An optional `preset` names a MoA preset; without it the tool uses `moa.default_preset` (see [Which preset is used](#which-preset-is-used)).

- **Policy lives in your system prompt, not in the tool description.** The description is in context for every profile, so it only states what the tool does, what advisors can't do, the question format, and the budget. When to call comes from the tier block you paste into each profile's SOUL.md. Without a tier block, a one-sentence fallback applies: hard multi-step reasoning, high-stakes or irreversible decisions, or a step that failed twice. Tiers use observable triggers, not "if unsure": verbalized uncertainty is the miscalibrated signal in small models.

- **Hard budget cap** — 5 consultations per conversation by default, then the tool returns a budget-exhausted error. Details in [Budget](#budget).

- **Graceful partial results** — a failed advisor (auth down, quota out) is reported as failed; the rest still answer. If every advisor fails, the tool returns an error instead of a result, and the model answers on its own. Each advisor answer is truncated to 6000 chars (`_TRUNCATE_CHARS`) with `[... truncated]`.

## Installation details

Requires a working MoA config: the plugin fans out to whatever advisors your preset lists. No keys or model names live in the plugin itself.

Install from GitHub (Hermes v0.23.1). `owner/repo` shorthand works; a full Git URL also works:

```
hermes plugins install kagankongar/hermes-consult-committee --enable
hermes tools enable committee
hermes gateway restart
```

- The plugin lands in the current profile's `plugins/` folder; `hermes -p <profile> plugins install …` installs into one profile (and `hermes -p <profile> tools enable committee` enables the toolset there).
- Without `--enable` Hermes asks "Enable now? [y/N]"; `--no-enable` installs it disabled. Hermes also warns that the source is not from its curated catalog.
- Never hand-edit `config.yaml` to enable the toolset; use `hermes tools enable committee`.
- Verify with `HERMES_PLUGINS_DEBUG=1 hermes plugins list`.

### Updating

An unpinned install follows the repository's default branch (`master`), not release tags:

```
hermes plugins check-updates            # read-only: compares the remote HEAD with your install
hermes plugins update consult-committee
```

### Pinning a release

`--ref` accepts only a full 40-character commit SHA. Tags, branches and short SHAs are rejected (`--ref must be a full 40-character commit SHA.`). Releases are annotated tags (`v0.2.0`, …; see [CHANGELOG.md](CHANGELOG.md)), so look up the commit a tag points to:

```
git ls-remote https://github.com/kagankongar/hermes-consult-committee.git 'refs/tags/v0.2.0^{}'
hermes plugins install kagankongar/hermes-consult-committee --ref <40-character SHA> --enable
```

Use the line ending in `^{}`: that SHA is the release commit. Without `^{}`, `ls-remote` prints the tag object's own SHA, which is not a commit and won't install. The Desktop app has the same option: Skills → Plugins → Install from Git → **Pin to commit**.

- `hermes plugins list` shows the pin as `git pinned@<sha8>`. Pins are per profile.
- `check-updates` reports a pinned plugin as "pinned" and never offers an update; `hermes plugins update` refuses to move it.
- Move to a newer release: look up its SHA the same way, then `hermes plugins install kagankongar/hermes-consult-committee --force --ref <new SHA>`. Untracked and git-ignored files stay; edits to tracked files are copied to `~/.hermes/plugins-backup/<name>-<sha>/` first.
- Back to tracking `master`: `hermes plugins remove consult-committee`, then install again without `--ref`. A forced reinstall without `--ref` keeps the old pin.

### Manual clone

Clone into the profile's plugins folder (`~/.hermes/plugins/` for the default profile, `~/.hermes/profiles/<profile>/plugins/` for a named one) and enable it:

```
git clone https://github.com/kagankongar/hermes-consult-committee.git \
    ~/.hermes/plugins/consult-committee
hermes plugins enable consult-committee
hermes tools enable committee
hermes gateway restart
```

To let `check-updates` and `update` manage a clone that tracks `master`, run `hermes plugins adopt consult-committee`. `adopt` always records an unpinned install that follows the default branch, so don't adopt a clone you checked out at a release tag (`git -C ~/.hermes/plugins/consult-committee checkout v0.2.0`); update that one yourself with git, or use `--ref` above instead.

## Settings reference

Find the options page at Settings → Capabilities → Plugins → consult-committee gear. Settings are per profile.

| Label | Key | Type | Default | Meaning |
|---|---|---|---|---|
| Log committee calls | `logging` | bool | `false` | Off by default. Turn on to append every consultation (question + per-advisor answers) to a plain-text log. See [Committee call log](#committee-call-log). |
| Log file path | `log_path` | str | `logs/consult-committee.log` | Fixed profile-relative default, resolved against the asking profile home. |
| Advisor system message | `advisor_system` | str | `You are a reference advisor. Only answer the question directly and concisely. No tool calls. No additional response.` | System prompt prepended to every advisor call. Single-line field; for longer text edit `advisor_system.md`. See [Advisor system message](#advisor-system-message). |
| Consultations per conversation | `max_consultations` | int | `5` | Committee calls allowed per conversation; only calls that return at least one advisor answer count. Suggested by caller tier: High 8, Medium 5, Low 3. `0` disables the tool. Invalid values fall back to 5. |

Environment overrides:

- `CONSULT_COMMITTEE_LOG` — log file path; wins over the settings field. `off` or empty disables logging.
- `CONSULT_COMMITTEE_ADVISOR_SYSTEM` — advisor system message; wins over the settings field and the file. `off` disables it.

## Which preset is used

With no `preset` argument the tool uses the same preset Hermes would: `moa.default_preset` when it names an existing preset, otherwise the **first preset listed** under `moa.presets` (not necessarily one called `default`). Set `moa.default_preset` explicitly if the order of your presets might change.

Errors and the call log name the preset actually used.

## Budget

The tool allows 5 consultations per conversation by default, then returns a budget-exhausted error. Set it per profile with **Consultations per conversation** on the plugin options page; match it to that profile's caller tier (see [Caller tiers](#caller-tiers)).

- `0` keeps the tool registered but every call returns "budget exhausted".
- The budget is keyed on Hermes' `conversation_id`, else `session_id`, else the whole gateway process when Hermes passes neither. Per Hermes' docs, `conversation_id` survives context compression, `/new` starts a new one, and delegated subagents share their parent's.
- Only calls that return at least one advisor answer count. A bad preset, an empty preset, all advisors failing, an error, or a call you stop (stop button or a new message) costs nothing.
- A stopped call's advisor requests still finish and are billed by the provider, since in-flight calls can't be killed, but Hermes has stopped waiting, so the answer never reaches the model and the slot is given back.
- Trade-off: the cap bounds cloud-quota burn when the model over-calls; it does not bound retries while every advisor is down, since those calls cost nothing.

## Getting the model to call it

### Small models under-call help

The 2026 escalation literature is unanimous: small models are overconfident (75–92% of tested conditions) and **won't reach for a help tool on their own** as often as they should. We reproduced this live: a 35B-class local model called the tool reliably when nudged ("use the committee"), answered hard questions correctly through it — but went 0-for-4 on self-escalation. It could even *describe* the tool and its triggers perfectly when asked. Knowing ≠ doing.

Two findings from testing worth passing on:

- **Famous puzzles are useless as escalation tests.** Well-known trap questions (Tuesday-boy probability, Alice's-sisters, etc.) are in the training data — the model solves them solo, correctly, and never feels the need to escalate. Test with genuinely expert or novel questions.

- **A system-prompt rule fixes it — verified (earlier wording).** After adding the "Ask a friend" rule (kept in [Verified earlier rule](#verified-earlier-rule) below) to the model's persona file, the same model self-escalated **unprompted** on an expert math integral (fresh session, zero hints) and again on a hard C++20 lock-free concurrency question in normal chat, weighing the advisors' answers against its own analysis in the final response. Neither the later "Consult the committee" rename of that rule nor the tier prompts have been probe-verified yet.

### Levers

Strongest first:

1. **System-prompt rule** (recommended) — paste **one** of the tier blocks from [escalation-prompts.md](escalation-prompts.md) into your persona/system prompt. Run the [probe protocol](escalation-prompts.md#probe-protocol) before trusting its firing rate. Most agents bake the system prompt in at **session start** — running sessions keep the old prompt, so test in a fresh session.

2. **Tool description** — deliberately neutral since `0.2.0` so it doesn't override your tier. Its fallback (hard multi-step reasoning, high stakes, failed twice) applies only to profiles with no tier block, which will call less often than they did under the old "MANDATORY / use FREQUENTLY" description.

3. **User nudge** — "check with the committee" in your message always works.

Measure your own model's escalation rate before trusting it: escalation behavior is model-specific and unpredictable (see [Act or Escalate, arXiv:2604.08588](https://arxiv.org/abs/2604.08588)).

### Caller tiers

Pick one tier per profile. Paste exactly one block into that profile's SOUL.md and set that profile's **Consultations per conversation** (on the plugin options page) to the suggested budget. Observable triggers beat felt-confidence thresholds, because small models miscalibrate those. Test in a fresh session.

The prompt blocks, the comparison table (who each tier is for, its triggers, how it treats advisor answers, suggested budget: High 8, Medium 5, Low 3), the [probe protocol](escalation-prompts.md#probe-protocol) and how to [adapt a tier](escalation-prompts.md#adapting-a-tier) all live in one place: [escalation-prompts.md](escalation-prompts.md). Pair High with your `cheap` preset, since it fires most often.

### Verified earlier rule

<details>
<summary>Verified earlier rule, "Ask a friend" wording (superseded by the tiers; kept for reference)</summary>

Exact text from the verification run (as of <code>v0.1.1-fork.6</code>):

> Ask a friend BEFORE soloing hard material. For any expert-level question (science, math, engineering, architecture), anything with real stakes, or any answer you feel even a little unsure about — you must call consult_committee FIRST with the full self-contained question, then weigh its advisors' answers against your own analysis in the final response. If you have failed twice at the same step, you MUST consult before a third attempt. Casual conversation and easy lookups are excluded.

</details>

## Committee call log

Logging is off by default. Turn it on in the Desktop app: Settings → Capabilities → Plugins → consult-committee gear → **Log committee calls** switch (or set `CONSULT_COMMITTEE_LOG` to a file path). When enabled, every completed consultation appends one plain-text block to the asking profile's `logs/consult-committee.log` (`<profile-home>/logs/consult-committee.log`, resolved via profile-aware `get_hermes_home()`): a header line with UTC timestamp, sequence number (counts calls in this Hermes process; restarts at 1 after a restart), the conversation id (or session id) when Hermes passes one, so you can tell which chat a call came from and which calls share a budget, asking profile + model when known, and the preset actually used (the `preset` argument, else Hermes' default preset), the question on one line (truncated to 4000 chars, newlines collapsed to spaces), the advisor system prompt on one `S:` line when set, one section per advisor with its answer (after the 6000-char cap; `[FAILED]` appended when it failed), an answered/failed counts line, and a `cancelled:` line when the call was stopped, followed by a blank line. A consultation where every advisor failed is logged too, although it returns an error and costs no budget.

```
=== 2026-10-28T07:22:50.953804+00:00 | seq 1 | conversation local-5d24c1 | profile seven | model meta-ai:muse-spark-1.3-contributor | preset default ===
Q: Are you operational and ready to answer committee questions?
S: You are a reference advisor. Only answer the question directly and concisely. No tool calls. No additional response.
--- advisor: openrouter:openai/gpt-6-luna ---
Yes—I’m ready to help answer committee questions.
--- advisor: openrouter:z-ai/glm-5.3-flash ---
Yes, I'm operational and ready. I'm serving as a reference advisor in this Mixture of Agents process — I can't execute tools, run commands, or access files/URLs myself, but I can analyze the conversation context, reason through the task state, and provide concrete guidance, next steps, and risk analysis for the acting agent to use.

Ready for the first committee question whenever you are.
answered: 2 failed: 0
```

**Log file path** (fixed default `logs/consult-committee.log`, profile-relative). `CONSULT_COMMITTEE_LOG` env overrides the GUI (set to a file path to enable, or `off`/empty to disable). `~` and `$VARS` are expanded; relative paths resolve against the asking profile home; absolute paths pass through. Log-open/write failures are ignored so logging never breaks a call. Restart the gateway after changing settings if the log location does not take effect immediately. Limitation: the final aggregator decision happens in the acting model AFTER the tool returns, so it is not in the log — only the ask and the advisors' answers are.

## Advisor system message

Every advisor call prepends a short system prompt; its default text is in `advisor_system.md` (and shown in the [settings table](#settings-reference)). Primary editor is `advisor_system.md` in the plugin dir — use it for longer text, since the plugin options field (**Advisor system message**) is single-line (Hermes has no multiline settings widget yet; upstream request [hermes-agent#145911](https://github.com/NousResearch/hermes-agent/issues/145911)). Short edits work in the GUI field too; env `CONSULT_COMMITTEE_ADVISOR_SYSTEM` wins, `off` disables.

**How the layers interact.** Precedence is env `CONSULT_COMMITTEE_ADVISOR_SYSTEM` > the settings field > `advisor_system.md` > the built-in default. Hermes stores a settings value only once you change and save that field. Until then, edits to `advisor_system.md` take effect (checked on Hermes v0.23.1: saving only the **Log committee calls** switch writes `logging` and nothing else). Two caveats: the settings field always *displays* the `plugin.yaml` default, not what is in `advisor_system.md`, so it can show stale text after you edit the file; and once you save the field, it overrides the file until you clear it.

## Why this fork exists

The upstream repo (`070freebird070-ctrl/hermes-consult-committee`) has an open, unmerged bugfix for the same crash this fork fixes — PR #2 (`fix/3-tuple-unpacking`; as of 2026-11 open since 7 Aug 2026): every `consult_committee` call fails on current Hermes with `ValueError: too many values to unpack`. As of 2026-11 the owner has not responded to bugfix PRs, so this fork maintains a working version: the shape-tolerant fix (old 2-tuples and new 3-tuples) plus a pytest suite. Upstream PR #3 carries the same fix for review.

Original plugin by [070freebird070-ctrl](https://github.com/070freebird070-ctrl); all core design and docs are theirs — this fork maintains the fix and further developments.

Full change history: [CHANGELOG.md](CHANGELOG.md).

## Prior art and credits

- [Amp's oracle tool](https://ampcode.com/news/oracle) — consult-a-stronger-model as a tool

- [zen-mcp-server](https://github.com/BeehiveInnovations/zen-mcp-server) — multi-model consensus tools

- [Together AI MoA paper](https://arxiv.org/abs/2406.04692) — aggregator-need-not-be-strongest

- [42-evey/hermes-plugins](https://github.com/42-evey/hermes-plugins) — plugin structure reference

## License

MIT
