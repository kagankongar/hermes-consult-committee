# consult-committee — Guide

Extended documentation for the `consult_committee` Hermes plugin. For the short overview, see [README.md](README.md).

- [How it works](#how-it-works) · [Design choices](#design-choices)
- [Installation details](#installation-details): [updating](#updating), [pinning a release](#pinning-a-release), [manual clone](#manual-clone)
- [Settings reference](#settings-reference) · [Committee preset](#committee-preset) · [Budget](#budget) · [Status and doctor](#status-and-doctor)
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
                                   │  fans out to the reference models
                                   │  of your `committee` MoA preset
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

- **One required parameter** (`question`) — maximizes tool-call reliability on small local models. An optional `preset` names a MoA preset; without it the tool uses the committee preset from the plugin settings (see [Committee preset](#committee-preset)).

- **MoA settings, own fan-out.** The advisors are configured as an MoA preset, so you pick them with Hermes' own MoA editor and model picker. The calls are the plugin's own: one Hermes `call_llm` per advisor, with the provider resolved for the asking profile. MoA itself does not need to be active, and none of MoA's runtime code (its advisor prompt, aggregator or fan-out policy) is involved.

- **Policy lives in your system prompt, not in the tool description.** The description is in context for every profile, so it only states what the tool does, what advisors can't do, the question format, and the budget. When to call comes from a tier block in the system prompt: set **Escalation tier** and the plugin adds it, or paste the block into the profile's SOUL.md ([Caller tiers](#caller-tiers)). Without a tier block, a one-sentence fallback applies: hard multi-step reasoning, high-stakes or irreversible decisions, or a step that failed twice. Tiers use observable triggers, not "if unsure": verbalized uncertainty is the miscalibrated signal in small models.

- **Budget cap** — 5 consultations per conversation by default, then the tool returns a budget-exhausted error; `-1` turns the cap off. Details in [Budget](#budget).

- **Graceful partial results** — a failed advisor (auth down, quota out) is reported as failed; the rest still answer. If every advisor fails, the tool returns an error instead of a result, and the model answers on its own. Each advisor answer is truncated to 6000 chars (`_TRUNCATE_CHARS`) with `[... truncated]`.

## Installation details

Requires an MoA preset that lists the committee's advisors, by default one named `committee` (see [Committee preset](#committee-preset)). No keys or model names live in the plugin itself.

Install from GitHub (Hermes v0.23.1). `owner/repo` shorthand works; a full Git URL also works:

```
hermes plugins install kagankongar/hermes-consult-committee --enable
hermes tools enable committee
hermes gateway restart
```

- The plugin lands in the current profile's `plugins/` folder; `hermes -p <profile> plugins install …` installs into one profile (and `hermes -p <profile> tools enable committee` enables the toolset there).
- Without `--enable` Hermes asks "Enable now? [y/N]"; `--no-enable` installs it disabled. Hermes also warns that the source is not from its curated catalog.
- Never hand-edit `config.yaml` to enable the toolset; use `hermes tools enable committee`. It matters only where a profile saved its own toolset list; otherwise plugin toolsets are on by default.
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
| Committee preset | `preset` | str | `committee` | MoA preset whose reference models are the advisors. Missing preset → MoA's default preset. Empty or an off word (`off`, `0`, `false`, `no`, any case) → always MoA's default preset. See [Committee preset](#committee-preset). |
| Log committee calls | `logging` | bool | `false` | Off by default. Turn on to append one block per call (outcome, question, per-advisor status and answers) to a plain-text log. See [Committee call log](#committee-call-log). |
| Log file path | `log_path` | str | `logs/consult-committee.log` | A relative path is inside the asking profile's home. |
| Advisor system message override | `advisor_system` | str | empty (the bundled `advisor_system.md` is sent) | Replaces the advisors' system message for this profile; `off` sends none. Single-line field; for longer text edit `advisor_system.md`. See [Advisor system message](#advisor-system-message). |
| Consultations per conversation | `max_consultations` | int | `5` | Committee calls allowed per conversation; only calls that return at least one advisor answer count. Suggested by caller tier: High 8, Medium 5, Low 3. `0` disables the tool, `-1` means no limit. Invalid values fall back to 5. |
| Escalation tier | `escalation_tier` | str | `off` | `high`, `medium` or `low`: the plugin adds that tier's block from [escalation-prompts.md](escalation-prompts.md) to each new session's system prompt. `off`, empty or any other value: nothing is added. Skipped when the profile's SOUL.md already has a `## Committee Escalation` heading. Applies to new sessions, and to running ones at their next context compression or `hermes sessions refresh-prompts`; does not change the budget. See [Caller tiers](#caller-tiers). |

Environment overrides:

- `CONSULT_COMMITTEE_LOG` — log file path; wins over the settings field. `off` or empty disables logging.
- `CONSULT_COMMITTEE_ADVISOR_SYSTEM` — advisor system message; wins over the settings field and the file. `off` disables it.

## Committee preset

The advisors are the reference models of one MoA preset, named by the **Committee preset** setting (`preset`, default `committee`). MoA does not have to be in use: the plugin only reads the preset.

**Create it** per profile with `hermes -p <profile> moa configure committee`, or in `config.yaml`:

```yaml
moa:
  default_preset: default          # keep your MoA default explicit (see below)
  presets:
    committee:
      enabled: false               # hide it from MoA model switching; committee ignores this flag
      reference_models:
        - provider: openrouter
          model: openai/gpt-6-luna
        - provider: openrouter
          model: anthropic/claude-haiku-5.5
          reasoning_effort: low    # optional, per advisor
        - provider: openrouter
          model: openai/gpt-6-luna-pro
          enabled: false           # skipped by committee
      reference_temperature: 0.4   # optional, default 0.9
      reference_timeout: 120       # optional, seconds per advisor, default 900
```

The standard committee is two comparably strong, cheap advisors, such as Luna and Haiku (latest), with a cheap acting model such as GLM or DeepSeek Flash. A high-effort thinking advisor (Luna Pro) costs far more per call: on the probe profile it made up about 63% of advisor spend.

What committee uses from the preset: `reference_models` (each advisor's `enabled` and `reasoning_effort` included), `reference_temperature` and `reference_timeout`. It ignores the preset's `aggregator`, `aggregator_temperature`, `fanout` and `degraded_reference_policy`, the preset-level `enabled`, and `moa.privacy_filter`. MoA's editor still asks for an aggregator; any model will do.

**Keep your MoA default where it was.** When `moa.default_preset` is not set, Hermes treats the *first* preset listed as the MoA default. `hermes moa configure committee` is safe: it saves your current default into `moa.default_preset`. When you edit `config.yaml` by hand, set `moa.default_preset` explicitly or list `committee` after your usual presets, or `committee` may become the MoA default. Check with `hermes moa list`.

**Which preset a call uses:**

1. The tool's `preset` argument, when the model passes one (an unknown name is an error).
2. The **Committee preset** setting. If that preset does not exist, the call falls back to step 3 and says so: a `preset_note` in the tool result and a `note:` line in the call log.
3. MoA's default preset, chosen the way Hermes does: `moa.default_preset` when it names an existing preset, otherwise the first preset listed. An empty setting or an off word (`off`, `0`, `false`, `no`, any case) goes straight here, without a note.

**Errors instead of surprises.** For a preset with no usable reference model, Hermes substitutes its built-in advisors. Committee refuses that: it returns "has no reference models configured" instead of calling models you never chose. A preset whose advisors are all `enabled: false` is an error too. Neither costs budget. Errors and the call log name the preset actually used.

**Costs and tuning.** Each advisor call is a Hermes auxiliary call with task `consult_committee`, so its tokens and estimated cost are recorded in the session's usage, next to the main model's. Hermes' auxiliary settings for that task apply, e.g. `auxiliary.consult_committee.max_concurrency` to cap parallel advisor calls. Unlike MoA, committee does not send a provider's user-initiated request headers; checked with OpenRouter and DeepSeek on Hermes v0.23.1. A provider that only serves premium models with those headers may refuse the call.

**Fallback answers are discarded.** When an advisor's model is unavailable, Hermes' `call_llm` can answer with a fallback model (in testing: the profile's main model) instead of failing. Committee does not present that as the advisor's answer: the advisor is reported as failed, with the reason and the model Hermes fell back to. Hermes still makes that fallback call, so its tokens appear in the session's usage. On a Hermes whose `call_llm` has no `route_info`, the fallback cannot be detected and the answer carries the advisor's label.

**Deadline.** Hermes abandons any tool call at its tool deadline (`timeouts.tools.sequential_call`, 420 s by default). Committee stops waiting 20 s before that and returns the answers it has; advisors still running are listed as `[skipped: committee deadline reached after Ns]`. A `reference_timeout` above that deadline therefore has no effect.

**Known limits.** Advisors on OpenRouter and DeepSeek are tested with 0.3.1. With 0.4.2 on Hermes v0.23.2, `meta-ai` and Hermes' built-in `nous` provider are tested too: both answered, served by the slot's own model with no fallback. A custom provider entry for the same Nous endpoint (such as `nousAPI` with an API key) resolves differently and is not tested separately.

## Budget

The tool allows 5 consultations per conversation by default, then returns a budget-exhausted error. Set it per profile with **Consultations per conversation** on the plugin options page; match it to that profile's caller tier (see [Caller tiers](#caller-tiers)). Full explanation with examples: [BUDGET.md](BUDGET.md).

- `0` keeps the tool registered but every call returns "budget exhausted".
- `-1` removes the cap. Calls are still counted (status, call log), the result has `cap: null`, and its note says the consultations left are unlimited. Nothing then limits advisor spend if the model over-calls; the tier blocks' triggers are the only brake.
- The budget is keyed on Hermes' `conversation_id`, else `session_id`, else the whole gateway process when Hermes passes neither. Per Hermes' docs, `conversation_id` survives context compression, `/new` starts a new one, and delegated subagents share their parent's.
- Only calls that return at least one advisor answer count. A bad preset, an empty preset, all advisors failing, an error, or a call you stop (stop button or a new message) costs nothing.
- A stopped call returns within about a second. Its advisor requests still finish and are billed by the provider, since in-flight calls can't be killed, but Hermes has stopped waiting, so the answer never reaches the model and the slot is given back.
- Trade-off: the cap bounds cloud-quota burn when the model over-calls; it does not bound retries while every advisor is down, since those calls cost nothing.
- Every answered call tells the model where it stands: the tool result carries `budget: {used, cap, scope}`, and its note ends with the consultations left.
- The count lives in the gateway's memory: a gateway restart or a plugin reload (`hermes plugins update`, disable and enable) starts every conversation's count from zero.

## Status and doctor

`/committee` (0.5.0+, a Hermes slash command) shows what a call would use right now, for the profile you are in:

- `/committee status` (or just `/committee`): plugin version, the preset and why it was chosen, the advisors (disabled ones listed), temperature, timeout and committee deadline, the escalation tier and whether it is injected, the budget used in this session's conversation, and the call log path.
- `/committee doctor`: checks the setup and flags only problems seen in practice. It warns when the committee preset is missing (calls fall back to MoA's default preset), when an advisor is the acting model itself (an independent answer, but with the same blind spots), and when a tier is set but a pasted SOUL.md block overrides it. It fails when the preset has no usable advisor or the tier has no block, and notes when there is no escalation policy at all (tier off and no SOUL.md block). It also checks that the Hermes internals the plugin uses still exist (`scripts/check_hermes_seams.py`, run inside the running Hermes; changed internals warn).

The budget line needs one committee call in the session first: slash commands get the session id, while the budget is counted per conversation.

## Getting the model to call it

### Small models under-call help

The 2026 escalation literature is unanimous: small models are overconfident (75–92% of tested conditions) and **won't reach for a help tool on their own** as often as they should. We reproduced this live: a 35B-class local model called the tool reliably when nudged ("use the committee"), answered hard questions correctly through it — but went 0-for-4 on self-escalation. It could even *describe* the tool and its triggers perfectly when asked. Knowing ≠ doing.

Two findings from testing worth passing on:

- **Famous puzzles are useless as escalation tests.** Well-known trap questions (Tuesday-boy probability, Alice's-sisters, etc.) are in the training data — the model solves them solo, correctly, and never feels the need to escalate. Test with genuinely expert or novel questions.

- **A system-prompt rule fixes it — verified (earlier wording).** After adding the "Ask a friend" rule (kept in [Verified earlier rule](#verified-earlier-rule) below) to the model's persona file, the same model self-escalated **unprompted** on an expert math integral (fresh session, zero hints) and again on a hard C++20 lock-free concurrency question in normal chat, weighing the advisors' answers against its own analysis in the final response. The later "Consult the committee" rename of that rule was never probe-verified. The current tier prompts were probe-tested on two acting models, 2–3 runs per probe and tier ([probe results](escalation-prompts.md#probe-results)).

### Levers

Strongest first:

1. **System-prompt rule** (recommended) — one of the tier blocks from [escalation-prompts.md](escalation-prompts.md) in the system prompt: set **Escalation tier** and the plugin adds it, or paste it into the persona file (SOUL.md) yourself ([Caller tiers](#caller-tiers)). Run the [probe protocol](escalation-prompts.md#probe-protocol) before trusting its firing rate. Most agents bake the system prompt in at **session start** — running sessions keep the old prompt (with the Escalation tier setting, until their next context compression or `hermes sessions refresh-prompts`), so test in a fresh session.

2. **Tool description** — deliberately neutral since `0.2.0` so it doesn't override your tier. Its fallback (hard multi-step reasoning, high stakes, failed twice) applies only to profiles with no tier block, which will call less often than they did under the old "MANDATORY / use FREQUENTLY" description.

3. **User nudge** — "check with the committee" in your message always works.

Measure your own model's escalation rate before trusting it: escalation behavior is model-specific and unpredictable (see [Act or Escalate, arXiv:2604.08588](https://arxiv.org/abs/2604.08588)).

### Caller tiers

Pick one tier per profile and set that profile's **Consultations per conversation** (on the plugin options page) to the suggested budget. Observable triggers beat felt-confidence thresholds, because small models miscalibrate those. Test in a fresh session.

The prompt blocks, the comparison table (who each tier is for, its triggers, how it treats advisor answers, suggested budget: High 8, Medium 5, Low 3), the [probe protocol](escalation-prompts.md#probe-protocol) and how to [adapt a tier](escalation-prompts.md#adapting-a-tier) all live in one place: [escalation-prompts.md](escalation-prompts.md). Pair High with cheap advisors in that profile's committee preset, since it fires most often.

There are two ways to get a block into the system prompt:

- **Escalation tier setting** (recommended). Set **Escalation tier** (`escalation_tier`) to `high`, `medium` or `low` on the plugin options page. The plugin reads that tier's block from the installed `escalation-prompts.md` and adds it to each new session's system prompt, after memory, under Hermes' heading `## Plugin Context: consult-committee.escalation-policy`. Updating the plugin updates the block; there is no copy to drift. `off` (the default), empty or any other value adds nothing. The tier does not change the budget.
- **Paste it yourself.** Keep the setting `off` and paste exactly one block into the profile's SOUL.md. Use this to adapt a block, or on a Hermes without plugin system prompt sections, where the setting does nothing.

If the profile's SOUL.md already has a `##` heading that starts with the words `Committee Escalation` (whatever follows, such as `(medium)` or `- High`; `## Committee Escalations` or a `###` heading does not count), the plugin adds nothing, so the policy never appears twice; it logs one INFO line per profile in Hermes' log. Only SOUL.md is checked.

**Moving from a pasted block to the setting:** set **Escalation tier**, then delete the `## Committee Escalation` block from the profile's SOUL.md (until you do, the pasted block wins). Start a new session to see the change.

Changes apply when Hermes builds a session's system prompt: at the start of every **new session**, and in a running session at its next context compression or after `hermes sessions refresh-prompts` (both rebuild the prompt, plugin sections included). Until then a running or resumed session keeps the prompt it has. So a tier change can reach a long conversation partway through, at a compression; use `refresh-prompts` to apply it on purpose. No gateway restart is needed for a setting change. To check, ask a fresh session to quote its committee policy, or look for `Session plugin prompt section: id=consult-committee.escalation-policy` in the profile's `logs/agent.log`.

Known limits:

- The plugin cannot see which toolsets a session has (Hermes does not pass that to prompt sections). If the `committee` toolset is off for a platform (only possible where the profile saved its own toolset list via `hermes tools`), its sessions still get the block and the model is told about a tool it lacks. Enable it there (`hermes tools enable committee`).
- Hermes allows plugin prompt sections 8,000 characters in total per session (a tier block is 1.4–1.7k). If other plugins use most of that, Hermes skips the section and logs a warning.

### Verified earlier rule

<details>
<summary>Verified earlier rule, "Ask a friend" wording (superseded by the tiers; kept for reference)</summary>

Exact text from the verification run (as of <code>v0.1.1-fork.6</code>):

> Ask a friend BEFORE soloing hard material. For any expert-level question (science, math, engineering, architecture), anything with real stakes, or any answer you feel even a little unsure about — you must call consult_committee FIRST with the full self-contained question, then weigh its advisors' answers against your own analysis in the final response. If you have failed twice at the same step, you MUST consult before a third attempt. Casual conversation and easy lookups are excluded.

</details>

## Committee call log

Logging is off by default. Turn it on in the Desktop app: Settings → Capabilities → Plugins → consult-committee gear → **Log committee calls** switch (or set `CONSULT_COMMITTEE_LOG` to a file path). When enabled, every tool call appends exactly one plain-text block to the asking profile's `logs/consult-committee.log` (`<profile-home>/logs/consult-committee.log`, resolved via profile-aware `get_hermes_home()`). The block is written once, when the call exits, and it is written for every exit: delivered, refused (empty question, budget exhausted, unknown preset, no reference models, every model disabled), internal error, cancelled, all advisors failed.

**Header.** `=== YYYY-MM-DD HH:MM:SS UTC | seq N | conversation ID | profile P | model M ===`. `seq` counts calls in this Hermes process and restarts at 1 after a restart. Calls refused for an empty question or an exhausted budget take no number, so `seq` is left out. The conversation id (or `session ID`) is what Hermes passes, so you can tell which chat a call came from and which calls share a budget. Profile and model are the asking ones. Every part except the timestamp is left out when unknown.

**Lines under the header.**

- `plugin:` plugin name and version.
- `preset:` the preset used, with the reason (see [Committee preset](#committee-preset)).
- `note:` only when the committee preset was missing and MoA's default was used instead.
- `budget:` consultations used after this call, e.g. `2 of 5 used in this conversation` (`2 used in this conversation (no cap)` with `-1`). A refund is already included.
- `timing:` elapsed seconds. Once the fan-out was reached it also shows the deadline, the advisor timeout and the temperature.
- `outcome:` what happened, always with the effect on the budget.

Then `Q (N chars[, first M shown]): …` (the question on one line, shown up to 4000 chars, newlines collapsed to spaces), `S: …` with the advisor system message when one is sent, and one section per advisor. Each section starts with `--- advisor i of N: <label> ---` and a `status:` line. The answer text follows only for advisors that answered, exactly as delivered to the model (6000-char cap, nothing else trimmed).

**Outcome values.**

| `outcome:` | Meaning | Budget |
|---|---|---|
| `delivered (X of N advisors answered; budget charged)` | At least one answer reached the model. | charged |
| `delivered after deadline cut (X of N advisors answered; budget charged)` | Hermes' tool deadline came first; the answers collected so far were delivered. | charged |
| `all advisors failed (0 of N answered; no budget used)` | Error returned to the model. | refunded |
| `cancelled (stop request or Hermes' tool deadline; answers not delivered; no budget used)` | Hermes abandoned the call. | refunded |
| `refused: question is required (no budget used)` | Empty question. | none |
| `refused: budget exhausted (N consultations this <conversation\|session\|gateway process>; no budget used)` | Cap reached. | none |
| `refused: unknown MoA preset … (no budget used)` | The explicit preset does not exist. | refunded |
| `refused: MoA preset … has no reference models configured (no budget used)` | Nothing usable in the raw config. | refunded |
| `refused: MoA preset … has every reference model disabled (no budget used)` | All slots have `enabled: false`. | refunded |
| `error: … (no budget used)` | Internal error. | refunded |

**Advisor status values.** `answered (N chars[, truncated from M chars])`, `failed: …` (the reason), `fallback discarded: …` (Hermes answered with another model, so the answer was dropped), `skipped: stop requested`, `skipped: committee deadline reached after Ns`, `empty reply`.

A delivered call with mixed advisors:

```
=== 2026-11-16 17:53:39 UTC | seq 3 | conversation local-8084b8 | profile t-dispose | model provider:model-main ===
plugin:   consult-committee 0.4.0
preset:   'committee' (committee preset)
budget:   2 of 5 used in this conversation
timing:   41.7 s elapsed; deadline 400 s; advisor timeout 900 s; temperature 0.9
outcome:  delivered (2 of 4 advisors answered; budget charged)

Q (127 chars): A payment API retries POST /charges automatically on timeout. Give the single most important safeguard to add, in one sentence.
S: <advisor system message>

--- advisor 1 of 4: provider:model-a[reasoning=low] ---
status:   answered (171 chars)
Add an idempotency key to each charge request (sent by the client and stored server-side) so that retries return the original result instead of creating duplicate charges.

--- advisor 2 of 4: provider:model-b ---
status:   answered (132 chars)
Add an idempotency key to each charge request so automatic retries return the original result instead of creating duplicate charges.

--- advisor 3 of 4: provider:model-c ---
status:   failed: TimeoutError('request timed out')
--- advisor 4 of 4: provider:model-d ---
status:   empty reply
```

A refused call is short:

```
=== 2026-11-16 18:02:11 UTC | conversation local-8084b8 | profile t-dispose | model provider:model-main ===
plugin:   consult-committee 0.4.0
budget:   5 of 5 used in this conversation
timing:   0.0 s elapsed
outcome:  refused: budget exhausted (5 consultations this conversation; no budget used)

Q (36 chars): Which index fits this query pattern?
```

**Log file path** (fixed default `logs/consult-committee.log`, profile-relative). `CONSULT_COMMITTEE_LOG` env overrides the GUI (set to a file path to enable, or `off`/empty to disable). `~` and `$VARS` are expanded; relative paths resolve against the asking profile home; absolute paths pass through. Log-open/write failures are ignored so logging never breaks a call. Restart the gateway after changing settings if the log location does not take effect immediately. Limitation: the final aggregator decision happens in the acting model AFTER the tool returns, so it is not in the log — only the ask and the advisors' answers are.

## Advisor system message

Every advisor call prepends a short system prompt, and it is the only system message the advisors get (before 0.3.0, Hermes' MoA advisor prompt was sent ahead of it); its text is in `advisor_system.md`. Primary editor is `advisor_system.md` in the plugin dir — use it for longer text, since the plugin options field (**Advisor system message**) is single-line (Hermes has no multiline settings widget yet; upstream request [hermes-agent#145911](https://github.com/NousResearch/hermes-agent/issues/145911)). Short edits work in the GUI field (**Advisor system message override**, empty by default) too; env `CONSULT_COMMITTEE_ADVISOR_SYSTEM` wins, `off` disables.

**How the layers interact.** Precedence is env `CONSULT_COMMITTEE_ADVISOR_SYSTEM` > the settings field > `advisor_system.md` > the built-in default. Hermes stores a settings value only once you change and save that field. Until then, edits to `advisor_system.md` take effect (checked on Hermes v0.23.1: saving only the **Log committee calls** switch writes `logging` and nothing else). Since 0.5.0 the field's default is empty, so an empty field means "use `advisor_system.md`". Once you save text in it, it overrides the file until you clear it.

## Why this fork exists

The upstream repo (`070freebird070-ctrl/hermes-consult-committee`) has an open, unmerged bugfix for the same crash this fork fixes — PR #2 (`fix/3-tuple-unpacking`; as of 2026-11 open since 7 Aug 2026): every `consult_committee` call fails on current Hermes with `ValueError: too many values to unpack`. As of 2026-11 the owner has not responded to bugfix PRs, so this fork maintains a working version: the shape-tolerant fix (old 2-tuples and new 3-tuples) plus a pytest suite. Upstream PR #3 carries the same fix for review. Since 0.3.0 the plugin no longer calls that internal MoA function at all; it makes the advisor calls itself.

Original plugin by [070freebird070-ctrl](https://github.com/070freebird070-ctrl); all core design and docs are theirs — this fork maintains the fix and further developments.

Full change history: [CHANGELOG.md](CHANGELOG.md).

## Prior art and credits

- [Amp's oracle tool](https://ampcode.com/news/oracle) — consult-a-stronger-model as a tool

- [zen-mcp-server](https://github.com/BeehiveInnovations/zen-mcp-server) — multi-model consensus tools

- [Together AI MoA paper](https://arxiv.org/abs/2406.04692) — aggregator-need-not-be-strongest

- [42-evey/hermes-plugins](https://github.com/42-evey/hermes-plugins) — plugin structure reference

## License

MIT
