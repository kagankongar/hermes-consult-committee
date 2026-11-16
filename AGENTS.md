# AGENTS.md — consult-committee

Hermes plugin. Registers one tool: `consult_committee(question, preset?)`.
Acting model escalates hard questions; handler fans out to MoA preset
reference models in parallel; model synthesises (it is the aggregator).

## Layout

- `__init__.py` — entire plugin: `CONSULT_SCHEMA`, `_handle_consult_committee`, `register(ctx)`, profile/model identity + profile-aware log path + advisor system message.
- `tests/` — self-contained, no live LLM, no Hermes install: `test_consult_committee.py` (handler, budget, logging, advisor system), `test_helpers_characterization.py` (pins helper behaviour; must stay green unchanged across refactors), `test_versioning.py` (version/CHANGELOG/tag links), `test_docs.py` (prompts only in `escalation-prompts.md`, README length, links/anchors resolve, `--ref` never given a tag).
- `pytest.ini` — `testpaths = tests`. Canonical command: `pytest` from repo root.
- `plugin.yaml` — name/version/tools. `CHANGELOG.md` — fork history. `README.md` — short front page (what, why, install, quick start, settings; ≤150 lines, test-enforced). `GUIDE.md` — everything in depth (design, install/pinning, settings, presets, budget, escalation behaviour, call log, advisor system message, fork history). `escalation-prompts.md` — the ONLY copy of the tier prompts + tier table. `COMPARISON.md` — vs-alternatives (MoA, council, advisor, delegation) comparison. `ONBOARDING.md` — Muse routing standing order. `advisor_system.md` — bundled advisor system default.

## Run / verify

See `ONBOARDING.md` — all code-writing routes via Muse (`call-muse-code` skill); Hermes owns brief, verification, changelog, commit.

```bash
pytest            # from repo root
# when no pytest sits on PATH (never pip-install into Hermes venvs):
uv run --with pytest -- python -m pytest
```

Tests mock Hermes internals via `sys.modules` (`tools.registry`, `hermes_cli.config`,
`hermes_cli.moa_config`, `agent.moa_loop`, plus `hermes_constants` / `hermes_cli.profiles` fakes for log-path tests). Plugin has zero import-time Hermes deps —
keep it that way. Reset `_consultations` and `_log_seq` between tests (existing autouse fixture does this).

## Handler contract (`_handle_consult_committee`)

- Lazy imports only, inside function body (mirrors Hermes call sites, avoids import cycles).
- Never raise: all paths return `tool_result` / `tool_error` JSON strings; outer `except Exception` → `tool_error("committee consultation failed: …")`.
- Validation first: empty `question` → `tool_error("question is required")` before touching Hermes modules.
- Budget: `_consultations` dict keyed by `_budget_key(kwargs)`: the dispatcher's `conversation_id` (compression-stable, shared with delegated children; Hermes' documented key for per-conversation plugin state), else `session_id`, else one process-wide bucket. Cap = `settings.max_consultations` via `_max_consultations()` (default `_DEFAULT_MAX_CONSULTATIONS = 5`, `0` disables, invalid → default). Reserve under `_lock` before the fan-out; a `finally` refunds unless a `tool_result` with ≥1 answer was built (bad preset / no models / all failed / exception / stop request cost nothing). Stop request = `tools.interrupt.is_interrupted()` true on the tool thread after the fan-out (`_interrupted()`, lazy import, False when unavailable): Hermes has abandoned the call, so return a "cancelled" `tool_error`, still log the block with a `cancelled:` line. Past cap → `tool_error("committee budget exhausted (N consultations this <conversation|session|gateway process>) …")`. Log `seq` is the process-wide `_log_seq`, taken at reserve time.
- Preset: `args.preset or None` → `resolve_moa_preset(load_config().get("moa") or {}, preset_name)`. `None` → Hermes uses `moa.default_preset` if it names an existing preset, else the FIRST preset listed (not `default`). Catch `(KeyError, ValueError)` → friendly `unknown MoA preset: <label>`, where label comes from `_preset_in_use(moa_cfg, explicit)` → `(name, label)`: explicit `'x'`; else asks Hermes' `normalize_moa_config` for the real default → `'<name>' (moa.default_preset)` or `'<name>' (first preset; moa.default_preset not set|'<raw>' not found)`; without that function (older Hermes, test fakes) `'<raw>' (moa.default_preset)` / `'default' (no moa.default_preset set)`. Never `None`. The same `name` goes in the log header. Real Hermes raises `MoAPresetNotFoundError`, a `ValueError` — the test fake must mirror this (it does).
- Empty `reference_models` → `tool_error("… has no reference models configured")`.
- Fan-out: `_run_references_parallel(models, _advisor_messages(question, system_text), temperature=_reference_temperature(preset), max_tokens=None)`; `_reference_temperature` honours `0`, missing/None/bool/invalid → `0.9` where `_advisor_messages` prepends `{"role":"system","content":system}` when non-empty, else user-only.
- Advisor system: bundled `advisor_system.md` (exact text lives in that file — never quote it here); precedence env `CONSULT_COMMITTEE_ADVISOR_SYSTEM` > GUI `advisor_system` > bundled file > hardcoded fallback; `off` disables (user-only); read lazily via `__file__`, never raises, never breaks call. Hermes writes a plugin setting only when the user saves that field (desktop GUI posts changed keys only; `save_plugin_settings` writes the keys it is given; observed v0.23.1 configs hold just `logging`), so the file wins by default. The GUI field displays the `plugin.yaml` default, not the file.
- Shape-tolerant unpack: newer Hermes yields `(label, text, accounting)` 3-tuples, older yields `(label, text)`. Index `item[0], item[1]` only — never `a, b = item`.
- Failure detection per advisor: `_is_failed_output` — empty, or `lstrip().lower()` starts with `[failed:` / `[skipped:` (Hermes' real sentinels, same check as `moa_loop`) or legacy `[error`. Never substring-match `failed:` (legit answers say "build failed: …"). Partial failure OK; all-failed → `tool_error("all committee advisors failed …")`. Test fakes must use the real sentinel strings.
- Truncation: `_TRUNCATE_CHARS = 6000` per advisor + `\n[... truncated]`.
- Success payload: `{advisors: [{model, answer, failed}], answered, failed, note}` where `note` instructs caller to aggregate itself.
- Audit log (opt-in, OFF by default): asking profile's `logs/consult-committee.log` via profile-aware `get_hermes_home()` (+ `HERMES_HOME`/`~/.hermes` fallback); fixed profile-relative default `logs/consult-committee.log` in `config_schema.log_path.default`; relative custom/env paths resolve against profile home (`~`/env expansion, absolute passthrough); header `=== ts | seq N | conversation|session ID | profile P | model M | preset X ===` (ID from `_budget_log_label`, omitted for the process bucket; P/M omitted when unknown; X = preset actually used; `seq` is per process); `cancelled: …` line after the counts when stopped; log block adds `S: <system prompt>` when a system role is sent, else Q directly; enable via GUI logging=true or CONSULT_COMMITTEE_LOG path; GUI via `config_schema` (`logging` bool + `log_path` str), `CONSULT_COMMITTEE_LOG` overrides, `off`/empty disables; one text block per completed fan-out; log I/O never raises; early-error paths log nothing.

## Conventions

- One required param (`question`); `preset` optional. Keep `CONSULT_SCHEMA.description` policy-neutral: what the tool does, what advisors can't do, question format, budget, and a one-sentence fallback for when no system-prompt rule exists. When-to-call policy (triggers, tripwires, frequency) belongs in the caller tier blocks (`escalation-prompts.md`, explained in `GUIDE.md`), never in the schema: it is in context for every profile and would override a Low tier. `TestSchemaDescription` enforces this.
- Helpers that must never raise: plain logic inside ONE outer `try/except Exception` returning the documented fallback. No nested guards around operations that cannot raise. Reuse `_text()` (strip, None → ""), `_one_line()` (log fields) and `_hermes_home()` (get_hermes_home → $HERMES_HOME → ~/.hermes) instead of re-implementing them. The handler calls helpers directly; they own their own safety.
- No keys / model names in plugin. No new deps. European conventions, stdlib only.
- Never quote volatile file defaults (e.g. `advisor_system.md` text) in agent docs — reference the file, so wording tweaks don't touch `AGENTS.md`.
- Update `CHANGELOG.md` (Keep a Changelog) + `plugin.yaml` version on behaviour change; add tests for every fix (see `TestDefaultPresetResolution`, `TestTwoTupleBackCompat` as templates).
- Versioning: plain SemVer `MAJOR.MINOR.PATCH` from `0.2.0` on (no more `-fork.N`; it is invalid PEP 440, which Hermes uses to compare versions). Unreleased work goes under `## [Unreleased]`.
- Release: (1) move `[Unreleased]` entries to `## [X.Y.Z] — YYYY-MM-DD`, set `plugin.yaml` `version: X.Y.Z`, add the `[X.Y.Z]:` compare link and repoint `[Unreleased]:`; (2) `pytest` green (`tests/test_versioning.py` enforces version/heading/link consistency); (3) commit, then `git tag -a vX.Y.Z -m "consult-committee X.Y.Z"` on that commit; (4) push the commit and the tag. Never move or delete a pushed tag; fix forward with a new patch release.
