# AGENTS.md — consult-committee

Hermes plugin. Registers one tool: `consult_committee(question, preset?)`.
Acting model escalates hard questions; handler fans out to MoA preset
reference models in parallel; model synthesises (it is the aggregator).

## Layout

- `__init__.py` — entire plugin (560 lines): `CONSULT_SCHEMA`, `_handle_consult_committee`, `register(ctx)`, profile/model identity + profile-aware log path + advisor system message.
- `tests/test_consult_committee.py` — 40 self-contained unit tests, no live LLM, no Hermes install.
- `pytest.ini` — `testpaths = tests`. Canonical command: `pytest` from repo root.
- `plugin.yaml` — name/version/tools. `CHANGELOG.md` — fork history. `README.md` — design rationale + escalation levers. `COMPARISON.md` — vs-alternatives (MoA, council, advisor, delegation) comparison. `ONBOARDING.md` — Muse routing standing order. `advisor_system.md` — bundled advisor system default.

## Run / verify

See `ONBOARDING.md` — all code-writing routes via Muse (`call-muse-code` skill); Hermes owns brief, verification, changelog, commit.

```bash
pytest            # from repo root (40 tests, all green 2026-10-28)
# when no pytest sits on PATH (never pip-install into Hermes venvs):
uv run --with pytest -- python -m pytest
```

Tests mock Hermes internals via `sys.modules` (`tools.registry`, `hermes_cli.config`,
`hermes_cli.moa_config`, `agent.moa_loop`, plus `hermes_constants` / `hermes_cli.profiles` fakes for log-path tests). Plugin has zero import-time Hermes deps —
keep it that way. Reset `_escalation_count` between tests (existing autouse fixture does this).

## Handler contract (`_handle_consult_committee`)

- Lazy imports only, inside function body (mirrors Hermes call sites, avoids import cycles).
- Never raise: all paths return `tool_result` / `tool_error` JSON strings; outer `except Exception` → `tool_error("committee consultation failed: …")`.
- Validation first: empty `question` → `tool_error("question is required")` before touching Hermes modules.
- Budget: `_MAX_ESCALATIONS = 5` per gateway process (thread-locked counter). Past cap → `tool_error("committee budget exhausted …")`. Note: the user-facing text says "this session"; the scope is the gateway process.
- Preset: `args.preset or None` → `resolve_moa_preset(load_config().get("moa") or {}, preset_name)`. `None` honours `moa.default_preset`, falls back to `default` only if config names none. Catch `(KeyError, ValueError)` → friendly `unknown MoA preset: '…'`. Real Hermes raises `MoAPresetNotFoundError`, a `ValueError` — the test fake must mirror this (it does).
- Empty `reference_models` → `tool_error("… has no reference models configured")`.
- Fan-out: `_run_references_parallel(models, _advisor_messages(question, system_text), temperature=float(preset.get("reference_temperature", 0.9) or 0.9), max_tokens=None)` where `_advisor_messages` prepends `{"role":"system","content":system}` when non-empty, else user-only.
- Advisor system: bundled `advisor_system.md` (exact text lives in that file — never quote it here); precedence env `CONSULT_COMMITTEE_ADVISOR_SYSTEM` > GUI `advisor_system` > bundled file > hardcoded fallback; `off` disables (user-only); read lazily via `__file__`, never raises, never breaks call.
- Shape-tolerant unpack: newer Hermes yields `(label, text, accounting)` 3-tuples, older yields `(label, text)`. Index `item[0], item[1]` only — never `a, b = item`.
- Failure detection per advisor: empty, `startswith("[error")`, or `"failed:" in text[:80].lower()`. Partial failure OK; all-failed → `tool_error("all committee advisors failed …")`.
- Truncation: `_TRUNCATE_CHARS = 6000` per advisor + `\n[... truncated]`.
- Success payload: `{advisors: [{model, answer, failed}], answered, failed, note}` where `note` instructs caller to aggregate itself.
- Audit log (opt-in, OFF by default): asking profile's `logs/consult-committee.log` via profile-aware `get_hermes_home()` (+ `HERMES_HOME`/`~/.hermes` fallback); fixed profile-relative default `logs/consult-committee.log` in `config_schema.log_path.default`; relative custom/env paths resolve against profile home (`~`/env expansion, absolute passthrough); header `=== ts | seq N | profile P | model M | preset X ===` (P/M omitted when unknown); log block adds `S: <system prompt>` when a system role is sent, else Q directly; enable via GUI logging=true or CONSULT_COMMITTEE_LOG path; GUI via `config_schema` (`logging` bool + `log_path` str), `CONSULT_COMMITTEE_LOG` overrides, `off`/empty disables; one text block per completed fan-out; log I/O never raises; early-error paths log nothing.

## Conventions

- One required param (`question`); `preset` optional. Keep schema descriptions concrete (mandatory triggers + twice-failed tripwire) — small models under-call vague tools.
- No keys / model names in plugin. No new deps. European conventions, stdlib only.
- Never quote volatile file defaults (e.g. `advisor_system.md` text) in agent docs — reference the file, so wording tweaks don't touch `AGENTS.md`.
- Update `CHANGELOG.md` (Keep a Changelog) + `plugin.yaml` version on behaviour change; add tests for every fix (see `TestDefaultPresetResolution`, `TestTwoTupleBackCompat` as templates).
