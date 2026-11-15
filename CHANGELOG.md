# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Fork note: `[0.1.0]` and earlier are upstream ([070freebird070-ctrl/hermes-consult-committee](https://github.com/070freebird070-ctrl/hermes-consult-committee)); `*-fork.N` versions are this fork's changes.

Versioning: from `0.2.0` on, this fork uses plain [Semantic Versioning](https://semver.org/spec/v2.0.0.html) `MAJOR.MINOR.PATCH` (also valid [PEP 440](https://peps.python.org/pep-0440/), which Hermes uses to compare plugin versions). The legacy `0.1.1-fork.N` strings are neither: SemVer reads them as pre-releases of `0.1.1`, and PEP 440 rejects them. Every release is an annotated git tag `vX.Y.Z` on the commit that sets `plugin.yaml` `version`. Pin an install with `hermes plugins install --ref vX.Y.Z`. The tags `v0.1.0` and `v0.1.1-fork.1` … `v0.1.1-fork.7` were added after the fact on their version-bump commits.

## [Unreleased]

### Changed
- README caller prompts (High/Medium/Low) rewritten as fenced, paste-safe blocks with explicit question format and post-result rules (`355c344`, `fa5314a`). Known gap: `escalation-prompts.md` and the README tier table still describe the previous wording, and the new prompts have not been probe-verified.

### Added
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

[Unreleased]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.7...HEAD
[0.1.1-fork.7]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.6...v0.1.1-fork.7
[0.1.1-fork.6]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.5...v0.1.1-fork.6
[0.1.1-fork.5]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.4...v0.1.1-fork.5
[0.1.1-fork.4]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.3...v0.1.1-fork.4
[0.1.1-fork.3]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.2...v0.1.1-fork.3
[0.1.1-fork.2]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.1-fork.1...v0.1.1-fork.2
[0.1.1-fork.1]: https://github.com/kagankongar/hermes-consult-committee/compare/v0.1.0...v0.1.1-fork.1
[0.1.0]: https://github.com/kagankongar/hermes-consult-committee/releases/tag/v0.1.0
