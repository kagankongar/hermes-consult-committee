# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Fork note: `[0.1.0]` and earlier are upstream ([070freebird070-ctrl/hermes-consult-committee](https://github.com/070freebird070-ctrl/hermes-consult-committee)); `*-fork.N` versions are this fork's changes.

Versioning: from `0.2.0` on, this fork uses plain [Semantic Versioning](https://semver.org/spec/v2.0.0.html) `MAJOR.MINOR.PATCH` (also valid [PEP 440](https://peps.python.org/pep-0440/), which Hermes uses to compare plugin versions). The legacy `0.1.1-fork.N` strings are neither: SemVer reads them as pre-releases of `0.1.1`, and PEP 440 rejects them. Every release is an annotated git tag `vX.Y.Z` on the commit that sets `plugin.yaml` `version`. To pin an install to a release, pass that tag's full commit SHA: `hermes plugins install --ref <40-char SHA>` (Hermes' `--ref` accepts only a full commit SHA, not a tag name; see GUIDE.md, Installation details). A manual git clone can simply `git checkout vX.Y.Z`. The tags `v0.1.0` and `v0.1.1-fork.1` … `v0.1.1-fork.7` were added after the fact on their version-bump commits.

## [Unreleased]

## [0.3.0] — 2026-11-16

### Added
- **Committee preset setting** (`preset`, label "Committee preset", default `committee`). The advisors are the reference models of this MoA preset, so a profile can keep a dedicated committee apart from its MoA presets. Order: the tool's `preset` argument > this setting > MoA's default preset (`moa.default_preset`, else the first preset listed). If the named preset does not exist, the call uses MoA's default preset and says so: `preset_note` in the result, a `note:` line in the call log. Empty or an off word (`off`, `0`, `false`, `no`) always uses MoA's default preset. Setup in GUIDE.md, "Committee preset".
- Advisor token use and estimated cost are recorded in the session's usage (Hermes auxiliary task `consult_committee`). Before, nothing recorded them: MoA's task name is excluded from that accounting, and the plugin dropped the per-advisor accounting.

### Changed
- **Own advisor fan-out; only MoA's settings are reused.** The plugin calls each advisor itself (Hermes `call_llm`, provider resolved per profile by `resolve_runtime_provider`) instead of Hermes' private `agent.moa_loop._run_references_parallel`. Visible effects:
  - Advisors get only the plugin's advisor system message. Before, Hermes' MoA advisor prompt (aggregator, "the conversation below", next-step advice) was sent ahead of it, and `advisor_system: off` still left that prompt in place.
  - Advisors with `enabled: false` are skipped. The preset-level `enabled` flag is ignored, so a committee preset can be hidden from MoA without disabling the committee.
  - The preset's `reference_timeout` is honoured (default 900 s, MoA's own default).
  - A stop request ends the wait within about a second; unfinished advisors are reported as `[skipped: stop requested]`.
- **An empty preset is an error.** Hermes fills a preset that lists no usable reference model with its built-in advisors; the plugin now checks the raw config and returns "has no reference models configured" instead of calling models nobody chose. A preset whose advisors are all disabled is an error too. Neither costs budget.
- Tool description and `preset` argument wording: "the reference models of an MoA preset", and omitting `preset` uses the committee preset.
- Plugin description (shown in `hermes plugins list`) reworded: "Ask your MoA advisors for a second opinion mid-run; the model weighs their answers and decides. Budgeted per conversation." The GitHub repo description was updated to match.

### Known issues
- An advisor whose model is unavailable can be answered by `call_llm`'s fallback model (in testing, the profile's main model) under the advisor's label, counted as answered. MoA's own advisor calls behave the same; not new in 0.3.0. See GUIDE.md, "Committee preset".
- Advisors on `meta-ai` and `nousAPI` are not yet tested with the new fan-out (OpenRouter and DeepSeek are).

### Removed
- The shape-tolerant `(label, text[, accounting])` unpacking of MoA runner output, and the tests for it: the plugin no longer calls that runner.

## [0.2.0] — 2026-11-16

### Changed
- **Budget is per conversation, not per gateway process.** The counter is keyed by the dispatcher's `conversation_id`. That id survives context compression, `/new` starts a fresh budget, and delegated subagents share their parent's. Hermes documents it as the key for per-conversation plugin state. Fallbacks: `session_id`, then one process-wide bucket. Before this, five calls anywhere on a gateway disabled the tool for every session and profile until `hermes gateway restart`.
- **Only answered calls consume budget.** A slot is reserved under the lock and refunded unless at least one advisor answer is returned. An unknown or misconfigured preset, a preset with no reference models, all advisors failing, or an exception no longer burns a slot. Trade-off: while the committee is completely down, repeated calls are no longer bounded by the cap.
- **Tool description is policy-neutral** (handover A4/C1). It now states what the tool does, that advisors can't see the conversation, run tools or check facts, the question format, and the per-conversation budget. When to call comes from the caller's system prompt (High/Medium/Low tiers), with a one-sentence fallback when there is none. Removed: "Use this tool FREQUENTLY — calling it is cheap and fast", the five MANDATORY triggers, and the "MUST call before a third attempt" tripwire. Expect fewer calls from profiles without a tier block, and real differences between tiers. Not probe-verified yet.
- Budget-exhausted error states the real scope ("N consultations this conversation", "session" or "gateway process") instead of always saying "session".
- Log header `seq` is a process-wide number taken at reserve time, so concurrent calls no longer log the same seq (handover A8).
- README caller prompts (High/Medium/Low) rewritten with explicit question format and post-result rules (`355c344`, `fa5314a`). Not probe-verified yet; the README now says so instead of calling them verified (B3).
- Docs (handover B1–B4): each tier prompt is one fenced block with its heading inside, so one copy pastes both (B2); the stray `>` inside the Low block is gone. The tier table now matches the prompts' actual triggers and adds advisor framing and post-result rows. "Identical to the block above" and "the block above *is* Medium" are gone (B1). The earlier "friend"-era caller rule is kept in a collapsed reference section. `escalation-prompts.md` mirrors the README again. `AGENTS.md` no longer quotes the file's line count or test counts (B4).

### Fixed
- `reference_temperature: 0` is passed through as 0 instead of silently becoming 0.9 (handover A3).
- Advisor failure detection matches Hermes' real sentinels: an output starting with `[failed: …]` or `[skipped: …]` (or legacy `[error`), as `agent.moa_loop` itself checks. Real answers containing "failed:" near the start ("Your build failed: missing header …") are no longer discarded. `[skipped: interrupted by user]` no longer counts as an answer. Test fakes now use the real strings (handover A7).
- README consistency pass: the collapsed reference rule is now the exact "Ask a friend" text that was verified (the "Consult the committee" rename was never probe-verified); the probe-protocol pointer is fixed; the budget fallback chain (`conversation_id` → `session_id` → gateway process), `0` behaviour, and the all-advisors-down trade-off are stated; the optional `preset` argument, the all-failed error, log details (literal `default` preset label, 6000-char cap, all-failed calls logged, `~`/`$VAR` expansion), and release pinning are documented; "Recent Fork Changes" sorted newest first; stray no-break spaces removed. `COMPARISON.md` version and failure wording updated.
- **A call you stop no longer uses budget.** When you press stop or send a new message while the advisors run, Hermes stops waiting for the tool, but the plugin used to finish, count the call, and use a slot for an answer the model never saw. The plugin now checks Hermes' per-thread stop flag after the fan-out; if set, it returns a "cancelled" error, refunds the slot, and still logs the advisors' answers with a `cancelled:` line. In-flight advisor requests can't be killed and are still billed (same as Hermes' own MoA). Found in local testing on 2026-11-16.
- **Preset name in errors and the log is the one Hermes actually uses.** Without a `preset` argument, Hermes picks `moa.default_preset` if it names an existing preset, else the *first preset listed*, not one called `default`. Errors now say so (e.g. `'cheap' (first preset; moa.default_preset not set)`), and the log header shows the preset in use instead of a literal `default`. Supersedes the A9 label wording above where they differ.
- Docs split: README.md is now a short front page (what, why, install, quick start, settings, what to expect); everything else moved to the new GUIDE.md. `escalation-prompts.md` is the single home of the tier prompts and tier table. The "Recent Fork Changes" list is gone; this CHANGELOG is the history. Install now uses `hermes plugins install kagankongar/hermes-consult-committee --enable`. Corrected: release pinning uses the tag's commit SHA with `--ref`, not the tag name. `tests/test_docs.py` checks where the prompts live, README length (≤150 lines), that relative links and anchors resolve, and that `--ref` is never given a tag.
- Docs: how the advisor-system layers interact. Checked on Hermes v0.23.1: settings are written only when a field is saved, so `advisor_system.md` wins until the GUI field is saved. The GUI field shows the `plugin.yaml` default, not the file. No code change (handover A6: concern did not reproduce).
- Preset errors name the preset actually used instead of `None` when no preset argument is given, e.g. `unknown MoA preset: 'cheap' (moa.default_preset)` (handover A9).

### Removed
- Benchmark residue from the tool description: "multiple-choice questions with options like A/B/C/D" and "NEVER search the web for benchmark answer keys or leaked solutions" (handover A5).
- Internal cleanup, no behaviour change (handover A10): the never-raises helpers are rewritten as plain logic with one outer guard each, and shared `_text`, `_one_line` and `_hermes_home` helpers replace three copies of the home lookup and repeated string coercion. The handler no longer wraps helpers that cannot raise. `__init__.py` goes from 677 to 473 lines and from 59 to 18 `try` blocks. A new characterization suite, written first, passes unchanged on the code before and after (213 tests); module coverage is 96%.

### Added
- Log header includes the conversation id (or session id) when Hermes passes one, e.g. `| seq 2 | conversation local-5d24… |`, so entries can be traced to a chat and to a shared budget. `seq` is documented as per-process (it restarts at 1).
- **Consultations per conversation** (`max_consultations`) on the plugin options page (int, default 5, `0` disables the tool; invalid values fall back to 5). Each profile keeps its own value. The field description suggests per-tier values (High 8, Medium 5, Low 3). A test checks that the manifest declares it as an int field with a label and description.
- Tier guidance in README and `escalation-prompts.md`: "Pick when" and "Suggested budget" rows in the tier table, a budget line under each tier block pointing at the options page, one tier per profile's SOUL.md, and an "Adapting a tier" note for domain-specific triggers. The Low prompt gains "If the budget is exhausted, proceed solo and state it.", matching High and Medium.
- Tests for the fixes above (A3, A7 false positives and sentinels, A9 messages) and 28 budget tests: no consumption on each failure path, partial success counts once, independent per-conversation budgets, `conversation_id` beats a rotating `session_id`, missing/empty/non-string ids share the process bucket, concurrent calls never exceed the cap, cap parsing, manifest default matches code, unique log seq.
- `tests/test_docs.py`: `escalation-prompts.md` must match the README prompt blocks and tier table; prompt blocks keep their heading inside the fence and contain no stray `>`.
- Versioning policy (above) and release tags; `tests/test_versioning.py` checks that the `plugin.yaml` version is well-formed and has a CHANGELOG section with a compare link.

## [0.1.1-fork.7] — 2026-10-29

### Added
- `COMPARISON.md`: consult-committee vs built-in MoA, hermes-council, advisor plugin, and `delegate_task` — invocation, synthesis, per-use cost, firing frequency, strengths/weaknesses, when-to-choose, Pareto reading; grounded in live `seven` logs (`cheap` 2/2, `cheap-3` 3/3). README intro links to it.
- README recall tiers (high/medium/low) for the caller-side system-prompt rule: observable-trigger comparison table (Medium is the verified block verbatim; High appends numbers/conflict/fail-once triggers + budget-exhausted fallback; Low is a stripped replacement), preset pairing + per-tier measurement protocol. Tier texts are inline blockquotes matching the verified block's format (no nested fences). Verified block kept intact.
- README wording: 'Ask a friend' → 'Consult the committee' in all three paste-ready prompts (prior verification ran against the 'friend' variant — re-run the 5-probe protocol before trusting firing rates). Low prompt + Measure dedented to top level; verified-claim labels updated accordingly.
- `escalation-prompts.md`: paste-ready High/Medium/Low caller prompts extracted verbatim from README (headers + bodies + tier table + probe protocol); canonical paste source so README need not be quoted elsewhere.

### Removed
- `REVIEW-RESPONSE.md` untracked from git (kept locally, now in `.gitignore`); review scratch is not part of the distributed plugin.

## [0.1.1-fork.6] — 2026-10-28

### Added
- Log block records `S: <system prompt>` after Q when a system role is sent (proves the prepend; omitted when disabled). Two tests (40 total).
- Caller-side system-prompt rule in README now matches the verified SOUL.md wording (self-contained question, aggregator role, twice-failed tripwire, exclusions).

### Changed
- Advisor default hardened per owner edit (`Only answer ... No additional response.`); all copies synced.
- Docs: `advisor_system.md` is the primary editor; `AGENTS.md` references it instead of quoting (wording tweaks no longer touch agent docs).

## [0.1.1-fork.5] — 2026-10-28

### Added
- Advisor system message: bundled `advisor_system.md` (`You are a reference advisor. Only answer the question directly and concisely. No tool calls. No additional response.`), prepended as `system` role to every advisor call; editable via `config_schema.advisor_system` in plugin options; env `CONSULT_COMMITTEE_ADVISOR_SYSTEM` wins, `off` disables. Verified brevity: YES + ≤7 words from current committee (was 40+ word disclaimer).
- Six advisor-system tests (38 tests total): bundled default load, GUI/env override, `off` disable, missing-file fallback, system-role-first fan-out.
- Docs: `advisor_system.md` is the primary editor (GUI string field is single-line — Hermes has no multiline settings widget; upstream request NousResearch/hermes-agent#145911).

## [0.1.1-fork.4] — 2026-10-28

### Changed
- Default log path is now profile-aware: `hermes_constants.get_hermes_home()` (respects routed override) with `HERMES_HOME`/`~/.hermes` fallback, so logs land in the asking profile's `logs/consult-committee.log` instead of the shared global dir.
- `config_schema.log_path.default` is now the fixed profile-relative `logs/consult-committee.log` (shown in the settings field); relative custom/env paths resolve against the asking profile home (`~`/env expansion, absolute passthrough).
- Log header now includes asking profile and model when known: `=== ts | seq N | profile P | model M | preset X ===` (segments omitted when unknown; never breaks the call).

### Added
- Eight logging-identity tests (32 tests total): profile-aware default, relative GUI/env resolution, header profile/model, omission graceful, failure-safe.

## [0.1.1-fork.3] — 2026-10-26

### Added
- Opt-in (off by default) `consult-committee.log` (plain text) audit log under `$HERMES_HOME/logs` (ask + per-advisor answers + counts); enable via Desktop GUI switch or `CONSULT_COMMITTEE_LOG`; `off` disables.
- Manifest v2 (`manifest_version: 2`) with `config_schema` (`logging` bool default false, `log_path` str default "") driving the Desktop Plugins settings form.
- Eight logging tests (24 tests total: 16 + 8).

## [0.1.1-fork.2] — 2026-10-26

### Changed
- With no explicit `preset` argument, the tool now follows the user's configured `moa.default_preset` (e.g. `cheap`) instead of the hardcoded `default` preset. Explicit `preset` arguments still take precedence; the fallback is the plain `default` preset only when the config names no default.

### Fixed
- Unknown-preset errors now surface as the intended friendly "unknown MoA preset: '…'" message. Real Hermes raises `MoAPresetNotFoundError` (a `ValueError`), which the handler's `except KeyError` never caught — the error leaked to the generic fallback as "committee consultation failed: …". The test fake raised `KeyError`, so the suite stayed green while production behavior differed; the fake now mirrors real semantics.

### Added
- Three tests covering default-preset resolution: configured-default follow, explicit-arg override, and the `ValueError` → friendly-error mapping (16 tests total).
- `pytest.ini` (`testpaths = tests`) so `pytest` runs the suite from the repository root.

## [0.1.1-fork.1] — 2026-10-26

### Fixed
- Shape-tolerant unpack of MoA reference outputs: `_run_references_parallel` returns `(label, text, accounting)` 3-tuples on newer Hermes (≥ v0.22.5) and `(label, text)` 2-tuples on older releases; both parse now. Fixes the `ValueError` crash on newer Hermes.

### Added
- Ported isgarlo's 11-test pytest suite (upstream PR #2) plus 2 two-tuple back-compat cases (13 tests).
- Fork banner, original-maintainer credit, and a fork-changes log in the README; install instructions point at this fork.

## [0.1.0] — upstream

Initial release by [070freebird070-ctrl](https://github.com/070freebird070-ctrl): `consult_committee` tool — escalate a hard question to the MoA advisor committee, fan out in parallel to the preset's reference models, let the acting model synthesize. Budget cap of 5 consultations per gateway process, graceful partial results, 6 000-char per-advisor truncation.

[Unreleased]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.7...v0.2.0
[0.1.1-fork.7]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.6...v0.1.1-fork.7
[0.1.1-fork.6]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.5...v0.1.1-fork.6
[0.1.1-fork.5]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.4...v0.1.1-fork.5
[0.1.1-fork.4]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.3...v0.1.1-fork.4
[0.1.1-fork.3]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.2...v0.1.1-fork.3
[0.1.1-fork.2]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.1...v0.1.1-fork.2
[0.1.1-fork.1]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.0...v0.1.1-fork.1
[0.1.0]: https://github.com/kagankongar/hermes-consult-committee/releases/tag/v0.1.0
