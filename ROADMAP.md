# consult-committee — analysis and roadmap

Date: 2026-11-19. Baseline: plugin 0.4.1 (master `1e9fb8a`), Hermes v0.23.2 as installed. Sources: the code, the docs, the `testing/` probe campaign, the t-dispose call log and usage database (266 committee calls, 2026-11-18/19), the installed Hermes source, and upstream docs and issues. Line numbers refer to `__init__.py` at `1e9fb8a`.

## 1. Raison d'être: is it useful?

**What it is for.** It lets the acting model ask for a second opinion in the middle of an agent run, when the model itself decides it needs one. Hermes has no other way to do this:

- MoA runs on every turn, or only when the user invokes `/moa`.
- MoA has no toolset (upstream MoA docs).
- The `advisor` plugin reviews every turn after the fact.
- `hermes-council` costs roughly 9 calls or more per question.

The plugin fills that gap with one tool and no change to Hermes core.

**The maintainer's thesis** (2026-11-19). The plugin lets you run cheaper models with more confidence. A fleet of cheap agents with the plugin is much more effective than the same fleet without it.

The architecture fits a fleet:
- **Per-agent cost is bounded:** at most the per-conversation cap × about $0.004 per consultation.
- **Every agent gets the same safety check** before irreversible actions.
- **One committee preset serves the whole profile,** so a few good advisors back many cheap actors.
- **Advisor cost doesn't depend on how long each agent's session is.**

Evidence so far for the "more confidence" half:
- With the 0.4.1 tier blocks, cheap models consult when the rules say to.
- They also act on the advice: final answers that ignored the advisors went from 20 to 0.

Evidence still missing for the "more effective" half: no run has yet compared the fleet's results with and without the plugin. E1 is built to test exactly this thesis.

**Verdict.** It is useful, but in fewer cases than the docs suggest. Its value comes from a few things the design gets right. Whether it improves final answers has not been measured yet.

| Benefit | Evidence | Strength |
|---|---|---|
| **Pay per use.** You pay only when the model asks: N calls, no aggregator call. MoA pays N+1 on every turn. | Design. Measured cost is about **$0.004 per consultation** with two advisors (gpt-6-luna-pro + claude-haiku-5.5). That is $1.02 for 253 delivered consultations, from Hermes' estimated cost in t-dispose `state.db`. The standard pair is Luna (not Pro) + Haiku, which should cost less: Luna Pro is a high-effort thinking model. | Strong |
| **The advisors' cost does not grow with session length.** Advisors get only the question (median 2,237 characters), never the agent's whole context. A stronger acting model pays for that context on every turn. The caveat is that each *result* stays in the acting model's context (see below), which prompt caching softens. | t-dispose log: question size p50 2,237 / p90 3,045 characters. | Strong for the advisor calls (by construction) |
| **The acting model keeps its tools and context while it weighs the advice.** A detached MoA aggregator or council chairman has neither. | Design. Probe re-run: final answers that ignored the advisors went from 20 to 0 with the 0.4.1 tier blocks. | Medium |
| **A safety tripwire before irreversible actions.** This is the most defensible use, even with a strong acting model (the Low tier). | Probe I1 (drop a production table) on the Low tier: deepseek-flash consulted in 1 of 3 runs, hy4-flash in 3 of 3 (run 1, before the 0.4.1 wording). | Medium; varies by model |
| **A cheap acting model (GLM or DeepSeek) does better with a Luna + Haiku committee than alone.** This is the value claim. | **Not measured.** The probes measured *whether* the model consults and how good its question is, not whether the final answer got better. | Hypothesis |

**What it costs besides tokens:**

- **Latency.** A consultation takes 24 s at the median, 53 s at p90 and 116 s at worst, during which the user sees nothing.
- **Context growth.** Each result adds about 2 × 2.8k characters (median answer length) to the acting model's context, and that is sent again on every later turn. 7% of answers (37 of 506) hit the 6,000-character cap.
- **Share of spend.** In the probe campaign, which consulted heavily, committee calls were about 30% of total model spend ($1.06 of $3.52). One consultation cost about as much as 0.6 of a whole deepseek-flash probe session, or 2.5 hy4-flash sessions.

Cost figures are Hermes' estimates, and approximate: the usage database covers a slightly wider window than the call log. It has 269 haiku calls against 253 delivered consultations, plus 42 `deepseek-flash` calls under task `consult_committee` that the log does not explain. The per-consultation figure uses the two advisor models only; the 30% share includes all three.

**Where it does not pay off:**

- A strong acting model on routine work: use it only as the irreversible-action tripwire.
- Facts a tool can check.
- Latency-sensitive chat.
- Questions that need execution rather than judgement: use `delegate_task` there.

**Risks to the value claim:**

1. **Garbage in.** The question is only as good as the acting model's summary. deepseek-flash still invented facts in 12 of 23 questions after the 0.4.1 rewrite (hy4-flash: 2 of 18).
2. **The weakest model aggregates.** The cheap acting model weighs answers from stronger advisors.
3. **Self-MoA** ([arXiv:2502.00674](https://arxiv.org/abs/2502.00674)) found that mixing models of unequal strength lowers quality compared with sampling the best model several times. A cheap third advisor can drag the committee down.

**Bottom line.** It is cheap and well built, and fills a real gap. The headline benefit (cheap agents working better with a committee than without) is still a claim. The most important item on this roadmap is the experiment that tests it: **E1**.

## 2. Architecture and inner workings

```
register(ctx) ──► tool consult_committee (toolset "committee")
              └─► system prompt section consult-committee.escalation-policy  (tier block from escalation-prompts.md)

handler: validate → reserve budget (in-memory, per conversation_id) → resolve preset (arg > setting > MoA default)
         → refuse empty/disabled presets → fan-out: one call_llm per advisor, thread pool, contextvars copied
         → poll stop request + Hermes tool deadline (−20 s) → drop fallback answers → truncate 6,000 chars
         → payload {advisors, answered, failed, note} → refund unless ≥1 answer → one log block
```

**What is good:**

- **The handler never raises.** Every exit returns a structured result.
- **Budget accounting is exact.** A call is charged only when at least one advisor answered, and the refund runs in `finally`.
- **Fallback answers are caught.** An answer Hermes produced from another model is detected and discarded (`_fallback_failure`, `:527`).
- **Deadline handling.** The fan-out returns partial results 20 s before Hermes would abandon the call.
- **Zero import-time Hermes dependencies.**
- **Fast tests.** 415 tests pass in 1.7 s.

**Weaknesses:**

| # | Weakness | Where |
|---|---|---|
| A1 | **Coupled to Hermes internals, and every test uses fakes.** The private seams are `agent.tool_executor._resolve_sequential_tool_timeout`, `agent.transports`, `tools.daemon_pool`, `tools.interrupt`, `hermes_cli.model_normalize`, `read_raw_config` and `call_llm(route_info=…)`. Since N1 the list also includes `agent.auxiliary_client._read_main_model` and `_read_main_provider`, which are private partials; N3 now guards every one of these. Each helper swallows its errors, so a rename upstream degrades the plugin *silently*: the deadline drops back to 400 s, fallback detection turns off, and so on. | `:510–602` |
| A2 | **The call log records the config's default model, not the session's model.** It is wrong under `-m` or after `/model`. The probe authors noticed this with hy4. (Fixed by N1.) | `_current_model_name`, `:163` |
| A3 | **The budget lives in a module dict.** It resets on a gateway restart and on plugin hot-reload (`plugins update`), and it is never pruned. | `:44` |
| A4 | **Settings are read by hand through `load_config()`.** Hermes now has the public `ctx.get_config` (installed `hermes_cli/plugins_context.py:75`). | `:188` |
| A5 | **The `preset` parameter lets the model invent preset names.** 13 of 266 t-dispose calls were refused for names such as `escalation-policy`, `high`, `medium`, `consult-committee` and `reference-models`, taken from the prompt section id and the tier names. All 13 happened on 2026-11-18, in runs whose prompts mentioned the argument. There have been none since. | `:108`, `:892–898` |
| A6 | **Maintenance load.** About 1,100 lines of code carry about 3,060 lines of tests and about 770 lines of docs, plus a 209-line changelog. AGENTS.md is a dense contract that each change must update. That is the price of the precision, but it slows every change. | — |

## 3. UI/UX

There are three audiences.

**The operator (installing and configuring):**

- **Install takes three commands, plus a preset made in MoA's editor.** That editor asks for an aggregator, which the committee ignores (GUIDE, Committee preset).
- **The default preset name is not in use.** None of the 5 production profiles (seven, kirk, geordi-la-forge, jean-luc-picard, k-builder) has a `committee` preset; checked in their `config.yaml` on 2026-11-19. Every call there uses MoA's default preset instead, which the result and log mention only in a note. This may be deliberate, but nothing in the UI shows it.
- **Nothing shows the configuration as a whole.** Nothing answers "which advisors would answer right now, and how much budget is left?" without reading YAML or turning on the log.
- **The settings page has warts:**
  - `advisor_system` is a single-line field. Hermes v0.23.2 has no multiline field type (`_FIELD_TYPES` in installed `hermes_cli/plugins_settings.py:24`).
  - Its displayed default is not the text actually sent; its own description has to explain that.
  - The `log_path` label reads "fixed profile-relative default".

**The acting model (schema, results, errors):**

- **Good:**
  - The schema description is neutral about when to call.
  - Error strings tell the model what to do next, for example "answer with your own best judgment".
  - Failed advisors are flagged.
- **Missing:**
  - The success payload does not say how much budget is left (`:954`), so the model cannot ration consultations.
  - Advisors are labelled by brand (`openrouter:anthropic/…`), which invites deferring to a famous name. hermes-council anonymises its seats for this reason.
  - The optional `preset` argument is a trap for small models (A5).

**The human in the chat:**

- During a 24–53 s fan-out nothing is shown.
- Afterwards, the only trace is the `Committee:` line that the tier blocks ask for. Without a tier block there is none.
- The log is a file, off by default.
- **Fix available:** Hermes now offers `ctx.post_message` (edit-in-place status bound to the calling session; installed `plugins_context.py:416`) and `ctx.register_command` (`:458`). Both fit here.

## 4. Roadmap

Each item lists what to do, the evidence for it, the effort (S/M/L) and how to verify it. Items marked **E** are experiments: ship them only if the metric moves.

### Now: 0.4.2 (fixes and hygiene, no new features)

| ID | What | Evidence | Effort | Verify |
|---|---|---|---|---|
| N1 | **Log the session's real model.** *Done (0.4.2).* Plugin tools get no agent handle (`model_tools.py` passes only task, session and conversation ids). The plugin now reads Hermes' per-turn main-runtime binding the way Hermes' own tools do (`agent.auxiliary_client._read_main_model` / `_read_main_provider`), with config as the fallback. Verified live on t-dispose: a `-m tencent/hy4-flash` session is logged as `openrouter:tencent/hy4-flash`, where 0.4.1 logged the config's glm. | A2 | S | Log-header test in `test_consult_committee.py`; a t-dispose run with `-m` |
| N2 | **Make the docs consistent.** *Done (Unreleased).* Details below the table. | Read-through, probe report | S | `pytest` (`test_docs.py`); read-through |
| N3 | **Seam smoke test.** *Done (0.4.2):* `scripts/check_hermes_seams.py` passes 23 of 23 checks on v0.23.2, and `tests/test_seams.py` keeps its list in step with the plugin's imports. A script, run with the Hermes interpreter (`hermes --print-runtime-command`), asserts that every lazily imported seam from A1 exists with the expected signature. Add it to the release checklist in AGENTS.md. | A1 | S–M | Passes on v0.23.2; fails when a seam is renamed |
| N4 | **Live-test `meta-ai` and `nousAPI` advisors.** *Done 2026-11-19 (0.4.2, Hermes v0.23.2):* t-dispose with a temporary preset mirroring production's `default` (built-in `nous` `openai/gpt-6-luna-pro` + meta-ai `muse-spark-1.3-contributor`). Both answered (556 and 459 characters, 8.8 s), and `state.db` shows each was served by its own model, with no fallback. Gap left: production's custom `nousAPI` entry (an API key for the same endpoint) takes a different resolution path, and was not tested. Use a t-dispose committee preset. *Amended 2026-11-19:* this blocks N5. None of the five production profiles has a `committee` preset, so their calls go to MoA's `default` preset, which is exactly nousAPI `openai/gpt-6-luna-pro` + meta-ai `muse-spark-1.3-contributor`. Do it in two halves. **meta-ai:** t-dispose already has a credential. **nousAPI:** t-dispose has no provider entry or key; you add it, or approve one test session on seven. | GUIDE "Known limits"; production `moa.presets.default` | S | Log: `status: answered` per advisor; no `fallback discarded` |
| N5 | *Done 2026-11-19: all five run 0.4.2 (`2ab1e7b`, git installs); configs unchanged. SOUL.md: pasted blocks removed and the Escalation tier set (high on seven, geordi-la-forge, jean-luc-picard; medium on k-builder).* **Roll 0.4.2 out to the five profiles** (they ran 0.4.0). Operational; needs your go-ahead. *Amended:* updating the plugin does not change the policy text on seven, geordi-la-forge, jean-luc-picard or k-builder. They keep their pasted pre-0.4.1 blocks in SOUL.md, and the plugin skips injection while that heading exists. To get the probe-tested 0.4.1 wording, set **Escalation tier** and delete the block, or re-paste it. That edits profiles, so it is your call. | profile state | S | `hermes -p <p> plugins list`; Hermes' log shows the inject or SOUL-skip line |

N2 covers:

- README ("one short probe run on one model", twice) vs escalation-prompts.md (two models).
- README's GLM 5.3 Flash row has no committed or scored data: the 36 records sit in git-ignored `testing/harness/out/`, with consult counts only. Either score and commit them, or mark the row "unscored" (done: marked).
- COMPARISON.md is dated 2026-10-29 and describes 0.3.1.
- [#38952](https://github.com/NousResearch/hermes-agent/issues/38952) is titled "Make MoA fully configurable via config.yaml" and was closed as completed on 2026-06-27. It is not a "model-callable MoA" request, so reword the README and COMPARISON references.
- `testing/METHODOLOGY.md` §9 and the plan files say "nothing applied", but v3 was applied.
- State the A3 budget reset in GUIDE, Budget.
- `ONBOARDING.md` (a personal Muse routing order) confuses outside users: move it, or mark it maintainer-only.

### Next: 0.5 (visible and easy to set up)

| ID | What | Evidence | Effort | Verify |
|---|---|---|---|---|
| X1 | *Done (0.5.0); live-checked on t-dispose through `hermes chat`: status, doctor, and the budget line after a resume. Desktop app not yet checked.* **`/committee` slash command.** Details below the table. | 0 of 5 profiles have the default preset; nothing shows the configuration as a whole. `register_command` exists in v0.23.2. | M | Unit tests with a fake `ctx`; live on t-dispose (CLI, desktop, one gateway platform) |
| X2 | *Done (0.5.0); live-checked: the model received `budget: {used: 1, cap: 3, scope: conversation}`.* **Budget in the result.** Add `budget: {used, cap, scope}` to the success payload, and end the note with the consultations left; the error strings already say what was used. | `:954` | S | Payload tests |
| X3 | *Spike done 2026-11-19: on t-dispose through the gateway, `post_message` from the tool's wait loop was delivered (`ok=True`), and its edits showed live ("asking 2 advisors…", "1 of 2 answered…", "2 of 2 answered…"; `chat -q` prints each edit as a line). Not shipped: a desktop check and your on-by-default decision come first.* **Progress message during the fan-out.** One `ctx.post_message`, edited as advisors finish ("Committee: 1 of 2 answered…"), then a final summary line. Store `ctx` at register; post from the handler thread, not the workers. | 24–53 s of silence; API at `plugins_context.py:416` | M | Spike on t-dispose first: does it bind from a tool turn on desktop and on a gateway platform? Then tests with a fake handle |
| X4 | *Done (0.5.0).* **Settings page cleanup.** Make `advisor_system` default empty, labelled "Override (empty = advisor_system.md)", so what the page shows is true. Simplify the `log_path` label. Also README: `hermes tools enable committee` is needed only on profiles that saved a toolset list. Plugin toolsets are on by default otherwise (installed `hermes_cli/tools_config.py`, `_enabled_plugin_toolsets`). | §3 | S | Advisor-system tests; look at the options page |
| X5 | *Lower priority (amended): no invented-preset refusals since the prompt fix on 2026-11-18.* **Remove the `preset` tool parameter.** The setting stays. This finishes the "one required parameter" design and removes the trap. The alternative is that an unknown explicit preset falls back with a note instead of an error. | A5 | S | `TestSchemaDescription`; handler tests updated; CHANGELOG notes the change |

X1 covers:

- `status`: version, resolved preset and why it was chosen, advisors (enabled or disabled), tier, budget used out of the cap for this conversation, and whether logging is on, with the log path.
- `doctor`: flags a missing `committee` preset, no usable or all-disabled advisors, an advisor equal to the acting model, a tier set while SOUL.md overrides it, a tier without a block, and a changed Hermes seam (N3).
- No `ask` subcommand: "check with the committee" already works.
- *Amended after N4/N5:* the doctor checks only what a finding backs. It warns on:
  - a missing committee preset, so calls fall back (0 of 5 profiles have one);
  - an advisor that is the same model as the acting model (2 of 5);
  - a tier set while SOUL.md still has a pasted block (4 of 5 before 2026-11-19);
  - a missing seam (N3).

  It compares model ids only, since profiles write providers as `meta-ai:` or `nousAPI:` and the runtime may lowercase them. Outside an agent turn the live model is unbound and the config's model is used. `/committee` does not collide with a built-in command (`resolve_command`, v0.23.2).
- *Amended:* slash-command handlers get `session_id`, not `conversation_id` (installed `hermes_cli/plugin_command_dispatch.py`). The tool handler has to record which budget key each `session_id` uses, so that `status` can show the right count.

### Then: 0.6 (prove the value, then improve answers)

Run these on `testing/harness/run_iso.py`, on t-dispose.

| ID | What | Metric | Effort |
|---|---|---|---|
| E0 | **Make the harness reusable.** Paths become arguments (profile, work directory); commit the scorer prompt; give scorers a read-only copy. *Amended:* `hermes chat -q` runs inside the shared gateway, so an edited plugin file loads only after `hermes -p <p> plugins disable` + `enable` (no restart). That toggle reorders `config.yaml`'s toolset list, so back the file up and restore it. Record this dev loop in AGENTS.md too. | One command reruns a wave | M |
| E1 | **Outcome evaluation.** This is the experiment the raison d'être depends on. Details below the table. | Correctness per dollar and per second, per arm | M–L |
| E2 | **Concise, structured advisor answers** (`advisor_system.md` only): answer first, the key reasons, the assumptions the advisor had to make, and what missing fact would change the answer, in about 250 words. Hypotheses: advisors flag facts the question invented; answers stop hitting the cap; less context growth. | Invented-fact propagation into the final answer, answer length, truncation rate (7% today), E1 correctness | S to change, M to measure |
| E3 | **Anonymised labels** in the payload (Advisor A, B…). Real names stay in the log. | Same as E2; ship only if it helps | S |
| E4 | **Preset guidance per Self-MoA:** two comparably strong advisors, or one strong model twice, vs a mixed preset. Config only; write the result into GUIDE. *Amended:* `gpt-6-luna-pro` is billed 7–17× the input tokens of the other advisors for identical messages (t-dispose `state.db`: 5,881 vs haiku's 787 per call on OpenRouter; 2,221 vs muse-spark's 129 on Nous). Explained: Luna Pro is a high-effort thinking model. It made up about 63% of advisor spend. The standard committee is Luna + Haiku (latest), so compare presets on cost per useful answer. | E1 metric on 1–2 presets | S |
| F1 | **Real-use data** (passive). `state.db` records advisor usage per session even with logging off. Once the production profiles have sessions, read the firing rate and cost per session. The log adds failures and discarded fallbacks where logging is on (seven, kirk, k-builder). Watch muse-spark first if it stays the main model on seven and jean-luc-picard: it runs on High there and was never probe-tested. | Consultations per session by tier and model; advisor failures | S |

E1 setup:

- Ten probes with checkable answers.
- Two arms per acting model: alone (tier `off`) and with a committee (`medium`). There is no "strong model alone" arm, since a committee is two or more advisors by definition.
- Acting models GLM 5.3 Flash and DeepSeek Flash; committee Luna + Haiku.
- 2 repeats: 10 probes × 2 arms × 2 models × 2 = 80 sessions. A pilot first: 5 probes × 2 arms × 2 models × 1 = 20.
- Cost from `state.db` `session_model_usage`, latency from the log.
- Report it the fleet way: correct answers per dollar across the whole probe set, plus how many irreversible-action probes went through without a consultation. That tests the thesis in §1 directly.

### Later, or only if a condition is met

- **`ctx.get_config`** in place of the hand-rolled settings reader (A4). The characterization tests guard the refactor.
- **`ctx.llm.complete`** in place of the private `call_llm` import, only if it supports per-call provider and model, reasoning settings, and session usage accounting. `agent/plugin_llm.py` exists; this has not been verified.
- **Upstream asks:**
  - Pass a session's toolsets to prompt sections, which removes the "tier block without the tool" limit.
  - A public tool-deadline accessor.
- **If Hermes ships a native model-callable MoA,** re-scope the plugin to what stays unique: the budget, discarding fallback answers, and the tier prompts.

### Won't do

- **Debate or multiple rounds:** no reliable gain over ensembling, at higher cost ([arXiv:2311.17371](https://arxiv.org/abs/2311.17371)).
- **Peer ranking or a chairman:** that is hermes-council, and it breaks "the acting model aggregates".
- **Numeric confidence from advisors:** verbalised confidence is miscalibrated.
- **Answer cache or question dedupe:** no evidence of repeats within a conversation.
- **A cost-based budget:** the count cap plus Hermes' own usage accounting is enough, and `post_auxiliary_call` carries no cost.
- **A budget that survives restarts:** the gain is small and the state handling is not.
- **Advisors with tools or conversation context:** that is `delegate_task` or MoA.
- **Moving advisors out of `moa.presets`:** declined on 2026-11-16.

## 5. Decisions for you

1. **X5:** remove `preset` from the schema, or keep it with a fallback?
2. **X3:** progress message on by default, or behind a setting?
3. **E1 budget:** a 20-session pilot, then about 80 sessions. Committee arms cost about $0.004 or less per consultation.
4. **Same model acting and advising** (seven, jean-luc-picard; profile config). muse-spark is the acting model and one of the two advisors in their `default` preset. The advisor sees only the question, in a fresh context, so it is an independent sample, but it shares the acting model's blind spots, and High's "two agree" rule then leans on it. The fix is a `committee` preset on those profiles with a different second advisor. *Done 2026-11-19: both have a `committee` preset (Luna + Haiku). Open: their main model, GLM or DeepSeek, instead of muse-spark.*
5. **kirk** runs GLM 5.3 Flash with the tier `off`. README suggests `high` (budget 8) for GLM, which under-consults even on High.
