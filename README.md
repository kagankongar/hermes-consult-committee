# consult-committee — a Hermes plugin

**Give your local model a "phone a friend" button.** This plugin registers a `consult_committee` tool that lets the acting model escalate a hard question to your configured [Mixture-of-Agents](https://hermes-agent.nousresearch.com/docs/user-guide/features/mixture-of-agents) advisor committee — mid-run, on its own judgment — instead of you having to type `/moa` yourself.

Hermes upstream deliberately keeps `/moa` out of the model's tool list (the slash command marks one *user* turn as MoA-enabled). That's the right default — but it means the model can never ask for help *during* a long agent run. This plugin fills that gap ([#38952](https://github.com/NousResearch/hermes-agent/issues/38952) discusses the need) without touching Hermes core: it's a plain user plugin.

> Detailed comparison vs built-in MoA, hermes-council, advisor, and `delegate_task`: [COMPARISON.md](COMPARISON.md).

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

- **The model synthesizes, not a detached aggregator.** The tool returns the advisors' labelled answers; the calling model weighs them against its own analysis. Mirrors upstream MoA's "aggregator = acting model" design, saves an LLM call, and the caller has conversation context a detached aggregator lacks.

- **One required parameter** (`question`) — maximizes tool-call reliability on small local models. An optional `preset` names a MoA preset; without it the tool uses `moa.default_preset`.

- **Policy lives in your system prompt, not in the tool description.** The description is in context for every profile, so it only states what the tool does, what advisors can't do, the question format, and the budget. When to call comes from the tier block you paste into each profile's SOUL.md. Without a tier block, a one-sentence fallback applies: hard multi-step reasoning, high-stakes or irreversible decisions, or a step that failed twice. Tiers use observable triggers, not "if unsure": verbalized uncertainty is the miscalibrated signal in small models.

- **Hard budget cap** — 5 consultations per conversation by default, then the tool returns a budget-exhausted error. Set it on the plugin options page (Settings → Capabilities → Plugins → consult-committee gear → **Consultations per conversation**); each profile keeps its own value, so match it to that profile's caller tier (below). `0` keeps the tool registered but every call returns "budget exhausted". Only calls that return at least one advisor answer count: a bad preset, an empty preset, all advisors failing, an error, or a call you stop (stop button or a new message) costs nothing. A stopped call's advisor requests still finish and are billed by the provider, since in-flight calls can't be killed, but Hermes has stopped waiting, so the answer never reaches the model and the slot is given back. The budget is keyed on Hermes' `conversation_id`, else `session_id`, else the whole gateway process when Hermes passes neither. Per Hermes' docs, `conversation_id` survives context compression, `/new` starts a new one, and delegated subagents share their parent's. This bounds cloud-quota burn when the model over-calls; it does not bound retries while every advisor is down, since those calls cost nothing.

- **Graceful partial results** — a failed advisor (auth down, quota out) is reported as failed; the rest still answer. If every advisor fails, the tool returns an error instead of a result, and the model answers on its own. Each advisor answer is truncated to 6000 chars (`_TRUNCATE_CHARS`) with `[... truncated]`.

## Install

```
git clone https://github.com/kagankongar/hermes-consult-committee \
    ~/.hermes/plugins/consult-committee
hermes plugins enable consult-committee
```

To pin a release instead of tracking `master`, check out its tag (for example `git -C ~/.hermes/plugins/consult-committee checkout v0.2.0`) or install with `hermes plugins install --ref v0.2.0`. Releases and their changes: [CHANGELOG.md](CHANGELOG.md).

(Upstream original: `070freebird070-ctrl/hermes-consult-committee` — this fork is the maintained copy until upstream merges the fix.)

Then expose the toolset (never hand-edit `config.yaml` — use the CLI):

```
hermes tools enable committee
```

Restart your gateway (`hermes gateway restart`). Verify with `HERMES_PLUGINS_DEBUG=1 hermes plugins list`.

Requires a working MoA config — the plugin fans out to whatever advisors you configured. With no explicit `preset` argument it uses the same preset Hermes would: `moa.default_preset` when it names an existing preset, otherwise the **first preset listed** under `moa.presets` (not necessarily one called `default`). Set `moa.default_preset` explicitly if the order of your presets might change. No keys or model names live in the plugin itself.

## Known limitation: small models under-call help (measured)

The 2026 escalation literature is unanimous: small models are overconfident (75–92% of tested conditions) and **won't reach for a help tool on their own** as often as they should. We reproduced this live: a 35B-class local model called the tool reliably when nudged ("use the committee"), answered hard questions correctly through it — but went 0-for-4 on self-escalation. It could even *describe* the tool and its triggers perfectly when asked. Knowing ≠ doing.

Two findings from testing worth passing on:

- **Famous puzzles are useless as escalation tests.** Well-known trap questions (Tuesday-boy probability, Alice's-sisters, etc.) are in the training data — the model solves them solo, correctly, and never feels the need to escalate. Test with genuinely expert or novel questions.

- **A system-prompt rule fixes it — verified (earlier wording).** After adding the "Ask a friend" rule (kept after the tier blocks below) to the model's persona file, the same model self-escalated **unprompted** on an expert math integral (fresh session, zero hints) and again on a hard C++20 lock-free concurrency question in normal chat, weighing the advisors' answers against its own analysis in the final response. Neither the later "Consult the committee" rename of that rule nor the tier prompts below have been probe-verified yet.

Levers, strongest first:

1. **System-prompt rule** (recommended) — paste **one** of the tier blocks below into your persona/system prompt. Run the probe protocol (right after the tier blocks) before trusting its firing rate.

> Note: most agents bake the system prompt in at **session start** — running sessions keep the old prompt, so test in a fresh session.

> **Recall tiers — pick one per profile.** Paste exactly one block into each profile's SOUL.md and set that profile's **Consultations per conversation** to the suggested budget. Observable triggers beat felt-confidence thresholds (small models miscalibrate those — the reason this section exists). The same blocks are in [escalation-prompts.md](escalation-prompts.md).
>
> | Signal | High | Medium | Low |
> |---|---|---|---|
> | Pick when | weak or local models; safety, money, production | general purpose (recommended start) | strong models; tight quota |
> | Expert-level question that needs reasoning | ✅ | — | — |
> | Multi-step derivation, proof, or design trade-off | ✅ | ✅ | — |
> | Irreversible, or touches money, safety, production data, someone else's system | ✅ | ✅ | ✅ |
> | Same step already failed | after 1 | after 2 | after 2 |
> | Greetings, chat, simple lookups, tool-confirmable facts | never | never | never |
> | Advisors framed as | stronger models | advisors | a review, possibly weaker |
> | After the result | adopt advisor consensus unless a tool result disproves it | resolve every disagreement explicitly | change only for a concrete, verifiable error |
> | Suggested budget (Consultations per conversation) | 8 | 5 (default) | 3 |

> **High** — weak local models, safety/money/production. Full prompt:

```
## Committee Escalation - High
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to stronger advisor models. They cannot see this conversation, run tools, or check facts.
Call it BEFORE answering when any of these is true:
1. The question is expert-level (science, math, engineering, law, medicine, architecture) and needs reasoning, not a lookup.
2. The task needs a multi-step derivation, proof, or design trade-off.
3. The action is irreversible or touches money, safety, production data, or someone else's system.
4. The same step (same command, test, or calculation) has already failed once.
Never call it for greetings, chat, simple lookups, or facts a search or tool can confirm; use that tool instead.
Question format: goal, every constraint and relevant data point, what was tried and how it failed. Do not include your own proposed answer.
After the result: state where advisors agree and disagree. If the advisors agree with each other but not with you, adopt their answer unless a tool result proves otherwise, and say so. If the budget is exhausted, proceed solo and state it.
```

> Budget: set **Consultations per conversation** to **8** on this profile's plugin options page. Pair with your `cheap` preset — High fires often.

> **Medium** — recommended starting point. Full prompt:

```
## Committee Escalation (medium)
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to advisor models. They cannot see this conversation, run tools, or check facts.
Call it BEFORE answering when any of these is true:
1. The task needs a multi-step derivation, proof, or design trade-off.
2. The action is irreversible or touches money, safety, production data, or someone else's system.
3. The same step (same command, test, or calculation) has failed twice.
Never call it for single-step questions, chat, simple lookups, or facts a search or tool can confirm.
Question format: goal, every constraint and relevant data point, what was tried and how it failed. Do not include your own proposed answer.
After the result: resolve every disagreement between advisors and yourself explicitly, naming which argument wins and why. If the budget is exhausted, proceed solo and state it.
```

> Budget: the default **5** fits; nothing to change.

> **Low** — strong models, tight quota. Full prompt:

```
## Committee Escalation (low)
A consult_committee tool is available (toolset "committee"). consult_committee sends one self-contained question to advisor models that may be weaker than you. Treat their answers as a review, not an authority.
Call it only when:
1. The action is irreversible or touches money, safety, production data, or someone else's system.
2. The same step (same command, test, or calculation) has failed twice.
Question format: goal, constraints, relevant data, what was tried. Do not include your own proposed answer.
After the result: change your answer only if an advisor names a concrete error you can verify. List any objections you rejected, with the reason. If the budget is exhausted, proceed solo and state it.
```

> Budget: set **Consultations per conversation** to **3** on this profile's plugin options page.

<details>
<summary>Verified earlier rule, "Ask a friend" wording (superseded by the tiers above; kept for reference)</summary>

Exact text from the verification run (as of <code>v0.1.1-fork.6</code>):

> Ask a friend BEFORE soloing hard material. For any expert-level question (science, math, engineering, architecture), anything with real stakes, or any answer you feel even a little unsure about — you must call consult_committee FIRST with the full self-contained question, then weigh its advisors' answers against your own analysis in the final response. If you have failed twice at the same step, you MUST consult before a third attempt. Casual conversation and easy lookups are excluded.

</details>

> **Probe protocol.** Measure per tier with 5 novel expert probes (famous puzzles don't count — they're memorized): expect High ≥4/5 escalations, Medium 2–3/5, Low ≤1/5.

> **Adapting a tier.** Keep the block's structure and add domain triggers as extra numbered items, for example "3. A high-stakes design call: mechanism choice or a correctness-vs-complexity trade-off." Name a MoA preset in the trigger if that profile should use one ("use the `cheap` preset"). Keep the "If the budget is exhausted, proceed solo" line so the model handles the cap gracefully.

2. **Tool description** — deliberately neutral since `0.2.0` so it doesn't override your tier. Its fallback (hard multi-step reasoning, high stakes, failed twice) applies only to profiles with no tier block, which will call less often than they did under the old "MANDATORY / use FREQUENTLY" description.

3. **User nudge** — "check with the committee" in your message always works.

Measure your own model's escalation rate before trusting it: escalation behavior is model-specific and unpredictable (see [Act or Escalate, arXiv:2604.08588](https://arxiv.org/abs/2604.08588)).

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

Every advisor call prepends a short system prompt (default in `advisor_system.md`): `You are a reference advisor. Only answer the question directly and concisely. No tool calls. No additional response.` Primary editor is `advisor_system.md` in the plugin dir — use it for longer text, since the plugin options field (**Advisor system message**) is single-line (Hermes has no multiline settings widget yet; upstream request [hermes-agent#145911](https://github.com/NousResearch/hermes-agent/issues/145911)). Short edits work in the GUI field too; env `CONSULT_COMMITTEE_ADVISOR_SYSTEM` wins, `off` disables.

**How the layers interact.** Precedence is env `CONSULT_COMMITTEE_ADVISOR_SYSTEM` > the settings field > `advisor_system.md` > the built-in default. Hermes stores a settings value only once you change and save that field. Until then, edits to `advisor_system.md` take effect (checked on Hermes v0.23.1: saving only the **Log committee calls** switch writes `logging` and nothing else). Two caveats: the settings field always *displays* the `plugin.yaml` default, not what is in `advisor_system.md`, so it can show stale text after you edit the file; and once you save the field, it overrides the file until you clear it.

## Why this fork exists

The upstream repo (`070freebird070-ctrl/hermes-consult-committee`) has an open, unmerged bugfix for the same crash this fork fixes — PR #2 (`fix/3-tuple-unpacking`, open since 7 Aug 2026): every `consult_committee` call fails on current Hermes with `ValueError: too many values to unpack`. With the owner unresponsive to bugfix PRs, this fork maintains a working version: the shape-tolerant fix (old 2-tuples and new 3-tuples) plus a pytest suite. Upstream PR #3 carries the same fix for review.

Original plugin by [070freebird070-ctrl](https://github.com/070freebird070-ctrl); all core design and docs are theirs — this fork maintains the fix and further developments.

> **Recent Fork Changes**

> - 2026-11-15 — Budget per conversation and only answered calls count; `max_consultations` setting; `reference_temperature: 0` honoured; failure detection matches Hermes' `[failed:`/`[skipped:` sentinels; tool description made policy-neutral (when to call comes from your tier block); tier prompts and tier table brought back in sync; plain SemVer with release tags. Version `0.2.0`.

> - 2026-10-29 — `escalation-prompts.md` paste-ready High/Medium/Low caller prompts (verbatim from README); `REVIEW-RESPONSE.md` untracked, local-only. Version `0.1.1-fork.7`.

> - 2026-10-28 — Log records `S:` system line; advisor default hardened; caller rule matches SOUL.md; `AGENTS.md` never quotes the prompt. Version `0.1.1-fork.6`.

> - 2026-10-28 — Advisor system message (`advisor_system.md`, editable in plugin options, env override, `off` disables); brevity verified YES + ≤7 words; 6 new tests, 38/38 green. Version `0.1.1-fork.5`.

> - 2026-10-28 — Profile-aware log default (`<profile-home>/logs/consult-committee.log` via `get_hermes_home()`), header includes asking profile + model, fixed profile-relative `logs/consult-committee.log` in settings field with relative-path resolution; 8 new identity tests, 32/32 green. Version `0.1.1-fork.4`.

> - 2026-10-26 — Opt-in (off by default) plain-text committee call log (`$HERMES_HOME/logs/consult-committee.log`) via manifest v2 `config_schema` + `CONSULT_COMMITTEE_LOG` override; 8 new logging tests, 24/24 green. Version `0.1.1-fork.3`.

> - 2026-10-26 — Test suite grown to 16 (3 new default-preset tests); `pytest.ini` added so `pytest` runs the suite from the repo root. Version `0.1.1-fork.2`.

> Full history: [CHANGELOG.md](CHANGELOG.md).

## Prior art / credits

- [Amp's oracle tool](https://ampcode.com/news/oracle) — consult-a-stronger-model as a tool

- [zen-mcp-server](https://github.com/BeehiveInnovations/zen-mcp-server) — multi-model consensus tools

- [Together AI MoA paper](https://arxiv.org/abs/2406.04692) — aggregator-need-not-be-strongest

- [42-evey/hermes-plugins](https://github.com/42-evey/hermes-plugins) — plugin structure reference

- [070freebird070-ctrl](https://github.com/070freebird070-ctrl) — original plugin author; core design and docs are theirs

## License

MIT

