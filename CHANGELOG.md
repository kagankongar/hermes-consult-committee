# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Fork note: `[0.1.0]` and earlier are upstream ([070freebird070-ctrl/hermes-consult-committee](https://github.com/070freebird070-ctrl/hermes-consult-committee)); `*-fork.N` versions are this fork's changes.

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
