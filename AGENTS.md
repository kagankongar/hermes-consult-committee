# AGENTS.md — consult-committee

Hermes plugin. Registers one tool: `consult_committee(question, preset?)`.
Acting model escalates hard questions; handler fans out to MoA preset
reference models in parallel; model synthesises (it is the aggregator).

## Layout

- `__init__.py` — entire plugin (153 lines): `CONSULT_SCHEMA`, `_handle_consult_committee`, `register(ctx)`.
- `tests/test_consult_committee.py` — 16 self-contained unit tests, no live LLM, no Hermes install.
- `pytest.ini` — `testpaths = tests`. Canonical command: `pytest` from repo root.
- `plugin.yaml` — name/version/tools. `CHANGELOG.md` — fork history. `README.md` — design rationale + escalation levers.

## Run / verify

```bash
pytest            # from repo root (16 tests, all green 2026-10-26)
```

Tests mock Hermes internals via `sys.modules` (`tools.registry`, `hermes_cli.config`,
`hermes_cli.moa_config`, `agent.moa_loop`). Plugin has zero import-time Hermes deps —
keep it that way. Reset `_escalation_count` between tests (existing autouse fixture does this).

## Handler contract (`_handle_consult_committee`)

- Lazy imports only, inside function body (mirrors Hermes call sites, avoids import cycles).
- Never raise: all paths return `tool_result` / `tool_error` JSON strings; outer `except Exception` → `tool_error("committee consultation failed: …")`.
- Validation first: empty `question` → `tool_error("question is required")` before touching Hermes modules.
- Budget: `_MAX_ESCALATIONS = 5` per gateway process (thread-locked counter). Past cap → `tool_error("committee budget exhausted …")`.
- Preset: `args.preset or None` → `resolve_moa_preset(load_config().get("moa") or {}, preset_name)`. `None` honours `moa.default_preset`, falls back to `default` only if config names none. Catch `(KeyError, ValueError)` → friendly `unknown MoA preset: '…'`. Real Hermes raises `MoAPresetNotFoundError`, a `ValueError` — the test fake must mirror this (it does).
- Empty `reference_models` → `tool_error("… has no reference models configured")`.
- Fan-out: `_run_references_parallel(models, [{"role":"user","content":question}], temperature=preset.reference_temperature or 0.9)`.
- Shape-tolerant unpack: newer Hermes yields `(label, text, accounting)` 3-tuples, older yields `(label, text)`. Index `item[0], item[1]` only — never `a, b = item`.
- Failure detection per advisor: empty, `startswith("[error")`, or `"failed:" in text[:80].lower()`. Partial failure OK; all-failed → `tool_error("all committee advisors failed …")`.
- Truncation: `_TRUNCATE_CHARS = 6000` per advisor + `\n[... truncated]`.
- Success payload: `{advisors: [{model, answer, failed}], answered, failed, note}` where `note` instructs caller to aggregate itself.
- Audit log (opt-in, OFF by default): `$HERMES_HOME/logs/consult-committee.log` (plain text, one block per call); enable via GUI logging=true or CONSULT_COMMITTEE_LOG path; GUI via `config_schema` (`logging` bool + `log_path` str), `CONSULT_COMMITTEE_LOG` overrides, `off`/empty disables; one text block per completed fan-out; log I/O never raises; early-error paths log nothing.

## Conventions

- One required param (`question`); `preset` optional. Keep schema descriptions concrete (mandatory triggers + twice-failed tripwire) — small models under-call vague tools.
- No keys / model names in plugin. No new deps. European conventions, stdlib only.
- Update `CHANGELOG.md` (Keep a Changelog) + `plugin.yaml` version on behaviour change; add tests for every fix (see `TestDefaultPresetResolution`, `TestTwoTupleBackCompat` as templates).
