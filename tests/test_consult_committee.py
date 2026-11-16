"""Unit tests for the consult_committee plugin handler.

The handler imports Hermes-internal modules lazily inside the function body,
so we mock those imports at the module level via sys.modules before each test
that reaches the fan-out path. This keeps the tests fully self-contained —
no live LLM calls, no Hermes installation required.

Test suite contributed by isgarlo (upstream PR #2); extended here. The fan-out
seam ``_run_advisors`` is faked for handler tests; ``TestOwnFanOut`` drives the
real one against fake ``call_llm`` / ``resolve_runtime_provider`` modules.
"""
from __future__ import annotations

import importlib
import json
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Load the plugin as a standalone module (it has no Hermes import-time deps)
# ---------------------------------------------------------------------------
_PLUGIN_PATH = __import__("os").path.join(
    __import__("os").path.dirname(__import__("os").path.dirname(__file__)),
    "__init__.py",
)
_spec = importlib.util.spec_from_file_location(
    "consult_committee", _PLUGIN_PATH
)
consult_committee = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(consult_committee)

_handle = consult_committee._handle_consult_committee
_REAL_RUN_ADVISORS = consult_committee._run_advisors


# ---------------------------------------------------------------------------
# Helpers: build a fake Hermes-internal module bundle
# ---------------------------------------------------------------------------
# Hermes fills a preset that lists no usable reference model with its built-in
# advisors; the fake mirrors that so the plugin's raw-config check is exercised.
_HERMES_BUILTIN_ADVISORS = [{"provider": "hermes-builtin", "model": "builtin-advisor"}]


def _with_provider(slot):
    """Fake presets may omit the provider; real slots always carry one."""
    return slot if not isinstance(slot, dict) or slot.get("provider") else {"provider": "fake", **slot}


def _install_fake_hermes_modules(
    *,
    preset_models=None,
    run_outputs=None,
    run_side_effect=None,
    default_preset_name=None,
    preset_name="default",
    presets=None,
    plugin_settings=None,
    model_value=None,
):
    """Inject mock Hermes modules into sys.modules and fake the advisor fan-out.

    ``presets`` maps preset name -> preset dict (stored under ``moa.presets``,
    the real config shape). The fan-out seam ``_run_advisors(slots, messages,
    *, temperature, timeout)`` is replaced by ``run_side_effect`` or a fake that
    returns ``run_outputs``; the autouse fixture restores the real one.
    """
    # tools.registry
    def _tool_result(payload):
        return json.dumps(payload)

    def _tool_error(msg):
        return json.dumps({"error": msg})

    tools_mod = types.ModuleType("tools")
    registry_mod = types.ModuleType("tools.registry")
    registry_mod.tool_result = _tool_result
    registry_mod.tool_error = _tool_error
    tools_mod.registry = registry_mod
    sys.modules["tools"] = tools_mod
    sys.modules["tools.registry"] = registry_mod

    # hermes_cli.config — real shape: moa.presets.<name>, moa.default_preset
    config_mod = types.ModuleType("hermes_cli.config")
    if presets is None:
        presets = {preset_name: {"reference_models": preset_models or []}}
    presets = {
        name: {**preset, "reference_models": [_with_provider(s) for s in preset.get("reference_models") or []]}
        for name, preset in presets.items()
    }
    moa_config_dict = {"presets": presets}
    if default_preset_name is not None:
        moa_config_dict["default_preset"] = default_preset_name

    def _load_config():
        cfg = {"moa": moa_config_dict}
        if model_value is not None:
            cfg["model"] = model_value
        if plugin_settings is not None:
            cfg["plugins"] = {"entries": {"consult-committee": {"settings": plugin_settings}}}
        return cfg
    config_mod.load_config = _load_config
    hermes_cli_mod = types.ModuleType("hermes_cli")
    hermes_cli_mod.config = config_mod
    sys.modules["hermes_cli"] = hermes_cli_mod
    sys.modules["hermes_cli.config"] = config_mod

    # hermes_cli.moa_config
    moa_config_mod = types.ModuleType("hermes_cli.moa_config")

    def _resolve(cfg, name):
        # Mirrors real resolve_moa_preset: None → moa.default_preset (→ 'default'
        # when unset); unknown preset raises ValueError (MoAPresetNotFoundError);
        # slots gain enabled=True; no usable slot → Hermes' built-in advisors.
        all_presets = cfg.get("presets") or {}
        resolved = str(name or cfg.get("default_preset") or "default").strip()
        if resolved not in all_presets:
            raise ValueError(f"MoA preset '{resolved}' was not found.")
        preset = dict(all_presets[resolved])
        refs = [
            {**s, "enabled": s.get("enabled", True)}
            for s in preset.get("reference_models") or []
            if isinstance(s, dict) and s.get("provider") and s.get("model")
        ]
        preset["reference_models"] = refs or [dict(s, enabled=True) for s in _HERMES_BUILTIN_ADVISORS]
        return preset

    moa_config_mod.resolve_moa_preset = _resolve
    hermes_cli_mod.moa_config = moa_config_mod
    sys.modules["hermes_cli.moa_config"] = moa_config_mod

    # The plugin's own fan-out seam.
    if run_side_effect is not None:
        run_mock = run_side_effect
    else:
        run_mock = lambda *a, **kw: run_outputs
    consult_committee._run_advisors = run_mock
    return run_mock


@pytest.fixture(autouse=True)
def _reset_escalation_counter():
    """Reset the module-level budget counters (and the fan-out seam) between tests."""
    consult_committee._consultations.clear()
    consult_committee._log_seq = 0
    yield
    consult_committee._consultations.clear()
    consult_committee._log_seq = 0
    consult_committee._run_advisors = _REAL_RUN_ADVISORS


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestAdvisorPayload:
    """Fan-out (label, text) pairs become the advisors list of the tool result."""

    def test_answers_unpacked_correctly(self):
        run_mock = _install_fake_hermes_modules(
            preset_models=[{"model": "claude-sonnet-4"}],
            run_outputs=[
                ("claude-sonnet-4", "The answer is 42."),
                ("gpt-5.5", "I agree, 42."),
            ],
        )
        result = _handle({"question": "What is 6 × 7?"})
        payload = json.loads(result)

        assert "advisors" in payload
        assert payload["answered"] == 2
        assert payload["failed"] == 0
        assert len(payload["advisors"]) == 2
        assert payload["advisors"][0]["model"] == "claude-sonnet-4"
        assert payload["advisors"][0]["answer"] == "The answer is 42."
        assert payload["advisors"][0]["failed"] is False

    def test_mixed_success_and_failure(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}, {"model": "m3"}],
            run_outputs=[
                ("m1", "Good answer"),
                ("m2", "[failed: AuthenticationError: 401]"),
                ("m3", ""),
            ],
        )
        result = _handle({"question": "hard question"})
        payload = json.loads(result)

        assert payload["answered"] == 1
        assert payload["failed"] == 2
        assert payload["advisors"][0]["failed"] is False
        assert payload["advisors"][1]["failed"] is True
        assert payload["advisors"][2]["failed"] is True


_OK = [("m1", "answer")]
_PROCESS = consult_committee._PROCESS_KEY


def _used(key=_PROCESS):
    return consult_committee._consultations.get(key, 0)


class TestBudgetCap:
    def test_budget_exhausted_after_default_cap(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        for _ in range(consult_committee._DEFAULT_MAX_CONSULTATIONS):
            assert "error" not in json.loads(_handle({"question": "q"}))
        result = json.loads(_handle({"question": "q"}))
        assert "budget exhausted" in result["error"]
        assert "5 consultations this gateway process" in result["error"]

    def test_successful_call_consumes_one(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        assert _used() == 0
        _handle({"question": "q"})
        assert _used() == 1
        _handle({"question": "q"})
        assert _used() == 2

    def test_partial_success_consumes_one(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}],
            run_outputs=[("m1", "answer"), ("m2", "[failed: timeout]")],
        )
        assert json.loads(_handle({"question": "q"}))["answered"] == 1
        assert _used() == 1


class TestBudgetRefund:
    """A1: calls that deliver no advisor answer must not consume budget."""

    def _assert_never_exhausts(self, args):
        for _ in range(consult_committee._DEFAULT_MAX_CONSULTATIONS + 2):
            result = json.loads(_handle(args))
            assert "budget exhausted" not in result.get("error", "")
        assert _used() == 0
        assert consult_committee._consultations == {}

    def test_unknown_preset_does_not_consume(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        self._assert_never_exhausts({"question": "q", "preset": "nonexistent"})

    def test_misconfigured_default_preset_does_not_consume(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}], run_outputs=_OK, default_preset_name="missing"
        )
        self._assert_never_exhausts({"question": "q"})

    def test_no_reference_models_does_not_consume(self):
        _install_fake_hermes_modules(preset_models=[])
        self._assert_never_exhausts({"question": "q"})

    def test_all_advisors_failed_does_not_consume(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}],
            run_outputs=[("m1", "[failed: 401]"), ("m2", "")],
        )
        self._assert_never_exhausts({"question": "q"})

    def test_exception_does_not_consume(self):
        def boom(*a, **kw):
            raise RuntimeError("provider outage")

        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_side_effect=boom)
        self._assert_never_exhausts({"question": "q"})

    def test_refund_keeps_earlier_successes(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"})
        _handle({"question": "q", "preset": "nonexistent"})
        assert _used() == 1

    def test_empty_question_does_not_consume(self):
        _handle({"question": ""})
        assert consult_committee._consultations == {}


class TestBudgetScope:
    """A2: budget is per conversation, not per process."""

    def test_conversations_have_independent_budgets(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        cap = consult_committee._DEFAULT_MAX_CONSULTATIONS
        for _ in range(cap):
            assert "error" not in json.loads(_handle({"question": "q"}, conversation_id="c1"))
        exhausted = json.loads(_handle({"question": "q"}, conversation_id="c1"))
        assert "5 consultations this conversation" in exhausted["error"]
        assert "error" not in json.loads(_handle({"question": "q"}, conversation_id="c2"))
        assert "error" not in json.loads(_handle({"question": "q"}))  # process bucket untouched

    def test_conversation_id_wins_over_session_id(self):
        """session_id rotates on context compression; conversation_id does not."""
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"}, conversation_id="c1", session_id="s1")
        _handle({"question": "q"}, conversation_id="c1", session_id="s2")
        assert _used("conversation_id:c1") == 2
        assert _used("session_id:s1") == 0

    def test_session_id_used_when_no_conversation_id(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"}, conversation_id="", session_id="s1")
        assert _used("session_id:s1") == 1
        for _ in range(4):
            _handle({"question": "q"}, session_id="s1")
        exhausted = json.loads(_handle({"question": "q"}, session_id="s1"))
        assert "5 consultations this session" in exhausted["error"]

    def test_missing_empty_and_non_string_ids_share_process_bucket(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"})
        _handle({"question": "q"}, conversation_id="", session_id="  ")
        _handle({"question": "q"}, conversation_id=None, session_id=None)
        _handle({"question": "q"}, session_id=123)
        assert _used() == 4
        assert list(consult_committee._consultations) == [_PROCESS]

    def test_concurrent_calls_never_exceed_cap(self):
        import threading

        cap = consult_committee._DEFAULT_MAX_CONSULTATIONS
        gate = threading.Event()

        def slow(*a, **kw):
            gate.wait(5)
            return _OK

        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_side_effect=slow)
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(json.loads(_handle({"question": "q"}))))
            for _ in range(cap * 3)
        ]
        for th in threads:
            th.start()
        # Every thread either reserved (blocked in fan-out) or was rejected.
        import time
        deadline = time.monotonic() + 5
        while len(results) < cap * 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        gate.set()
        for th in threads:
            th.join(5)
        ok = [r for r in results if "error" not in r]
        exhausted = [r for r in results if "budget exhausted" in r.get("error", "")]
        assert len(ok) == cap
        assert len(exhausted) == cap * 2


    def test_log_seq_is_unique_across_conversations(self, tmp_path, monkeypatch):
        log = tmp_path / "c.log"
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(log))
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"}, conversation_id="c1")
        _handle({"question": "q"}, conversation_id="c2")
        text = log.read_text(encoding="utf-8")
        assert "| seq 1 |" in text and "| seq 2 |" in text


class TestMaxConsultationsSetting:
    def test_configured_cap_applies(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}], run_outputs=_OK, plugin_settings={"max_consultations": 8}
        )
        for _ in range(8):
            assert "error" not in json.loads(_handle({"question": "q"}))
        result = json.loads(_handle({"question": "q"}))
        assert "8 consultations" in result["error"]

    def test_zero_disables(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}], run_outputs=_OK, plugin_settings={"max_consultations": 0}
        )
        assert "budget exhausted" in json.loads(_handle({"question": "q"}))["error"]

    @pytest.mark.parametrize("value, expected", [
        (8, 8), (8.0, 8), ("3", 3), (0, 0),
        (-1, 5), (2.5, 5), (True, 5), (None, 5), ("lots", 5), ([3], 5),
    ])
    def test_cap_parsing(self, value, expected):
        assert consult_committee._max_consultations({"max_consultations": value}) == expected

    def test_missing_setting_uses_default(self):
        assert consult_committee._max_consultations({}) == 5

    def test_manifest_declares_options_page_field(self):
        """max_consultations is a config_schema int with label + description, so the
        Hermes plugin options page renders it as a number field."""
        import os
        import re

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "plugin.yaml"), encoding="utf-8") as f:
            text = f.read()
        schema = text.split("\nconfig_schema:\n", 1)[1]
        m = re.search(r"^  max_consultations:\n((?:    .*\n?)+)", schema, re.M)
        assert m, "config_schema.max_consultations missing"
        body = m.group(1)
        assert re.search(r"^    type: int$", body, re.M)
        assert re.search(r'^    label: "Consultations per conversation"$', body, re.M)
        assert re.search(r'^    description: ".+"$', body, re.M)

    def test_manifest_default_matches_code(self):
        import os
        import re

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "plugin.yaml"), encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^  max_consultations:\n(?:    .*\n)*?    default: (\d+)$", text, re.M)
        assert m, "plugin.yaml lacks config_schema.max_consultations.default"
        assert int(m.group(1)) == consult_committee._DEFAULT_MAX_CONSULTATIONS


class TestValidation:
    def test_empty_question_returns_error(self):
        # No need to install Hermes modules — validation happens first
        result = json.loads(_handle({"question": ""}))
        assert "error" in result
        assert "question is required" in result["error"]

    def test_missing_question_returns_error(self):
        result = json.loads(_handle({}))
        assert "error" in result
        assert "question is required" in result["error"]

    def test_unknown_preset_returns_error(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}])
        result = json.loads(_handle({"question": "q", "preset": "nonexistent"}))
        assert "error" in result
        assert "unknown MoA preset" in result["error"]

    def test_empty_reference_models_returns_error(self):
        _install_fake_hermes_modules(preset_models=[])
        result = json.loads(_handle({"question": "q"}))
        assert "error" in result
        assert "no reference models" in result["error"]


class TestAllAdvisorsFail:
    def test_all_failed_returns_error(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}],
            run_outputs=[
                ("m1", "[failed: timeout]"),
                ("m2", "[skipped: interrupted by user]"),
            ],
        )
        result = json.loads(_handle({"question": "q"}))
        assert "error" in result
        assert "all committee advisors failed" in result["error"]


class TestFailureDetection:
    """A7: match Hermes' real sentinels, not any 'failed:' near the start."""

    @pytest.mark.parametrize("text", [
        "",
        "[failed: RateLimitError: 429]",
        "  [FAILED: timeout]",
        "[skipped: interrupted by user]",
        "[skipped: MoA presets cannot recursively reference MoA]",
        "[error] legacy format",
    ])
    def test_sentinels_are_failures(self, text):
        assert consult_committee._is_failed_output(text) is True

    @pytest.mark.parametrize("text", [
        "Your build failed: missing header <atomic>. Add the include.",
        "The test failed: expected 4, got 5 — off-by-one in the loop.",
        "Answer: B. Option A failed: it ignores friction.",
        "[note] see below",
    ])
    def test_legit_answers_are_not_failures(self, text):
        assert consult_committee._is_failed_output(text) is False

    def test_answer_mentioning_failed_is_delivered(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "Your build failed: missing header <atomic>.")],
        )
        payload = json.loads(_handle({"question": "q"}))
        assert payload["answered"] == 1
        assert payload["advisors"][0]["failed"] is False

    def test_skipped_advisor_is_not_counted_as_answer(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}],
            run_outputs=[("m1", "real answer"), ("m2", "[skipped: interrupted by user]")],
        )
        payload = json.loads(_handle({"question": "q"}))
        assert payload["answered"] == 1
        assert payload["advisors"][1]["failed"] is True


class TestReferenceTemperature:
    """A3: reference_temperature 0 must reach the fan-out as 0, not 0.9."""

    def _temperature_for(self, preset_extra):
        seen = {}

        def run(models, messages, **kw):
            seen.update(kw)
            return [("m1", "answer")]

        _install_fake_hermes_modules(
            presets={"default": {"reference_models": [{"model": "m1"}], **preset_extra}},
            run_side_effect=run,
        )
        assert "error" not in json.loads(_handle({"question": "q"}))
        return seen["temperature"]

    @pytest.mark.parametrize("extra, expected", [
        ({"reference_temperature": 0}, 0.0),
        ({"reference_temperature": 0.0}, 0.0),
        ({"reference_temperature": 0.3}, 0.3),
        ({"reference_temperature": "0.5"}, 0.5),
        ({}, 0.9),
        ({"reference_temperature": None}, 0.9),
        ({"reference_temperature": "hot"}, 0.9),
        ({"reference_temperature": True}, 0.9),
    ])
    def test_temperature(self, extra, expected):
        assert self._temperature_for(extra) == expected


class TestPresetErrorMessages:
    """A9: no 'None' in preset errors when the default preset is used."""

    def test_unknown_explicit_preset_names_it(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}])
        err = json.loads(_handle({"question": "q", "preset": "nonexistent"}))["error"]
        assert err == "unknown MoA preset: 'nonexistent'"

    def test_missing_default_preset_names_config_value(self):
        _install_fake_hermes_modules(
            presets={"cheap": {"reference_models": [{"model": "m1"}]}},
            default_preset_name="gone",
        )
        err = json.loads(_handle({"question": "q"}))["error"]
        assert "None" not in err
        assert err == "unknown MoA preset: 'gone' (moa.default_preset)"

    def test_empty_default_preset_names_fallback(self):
        _install_fake_hermes_modules(preset_models=[])
        err = json.loads(_handle({"question": "q"}))["error"]
        assert "None" not in err
        assert err == "MoA preset 'default' (no moa.default_preset set) has no reference models configured"

    def test_empty_explicit_preset_names_it(self):
        _install_fake_hermes_modules(preset_models=[], preset_name="cheap")
        err = json.loads(_handle({"question": "q", "preset": "cheap"}))["error"]
        assert err == "MoA preset 'cheap' has no reference models configured"


def _fake_interrupt(monkeypatch, flagged):
    mod = types.ModuleType("tools.interrupt")
    mod.is_interrupted = lambda: flagged
    if "tools" not in sys.modules:
        monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
    monkeypatch.setitem(sys.modules, "tools.interrupt", mod)
    monkeypatch.setattr(sys.modules["tools"], "interrupt", mod, raising=False)


class TestCancellation:
    """A stop request during the fan-out: Hermes abandons the call, so it must not
    use budget, and the log says so."""

    def test_stopped_call_is_refunded_and_reported(self, tmp_path, monkeypatch):
        log = tmp_path / "c.log"
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(log))
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _fake_interrupt(monkeypatch, True)
        result = json.loads(_handle({"question": "q"}, conversation_id="c1"))
        assert "cancelled by a stop request" in result["error"]
        assert "no budget was used" in result["error"]
        assert consult_committee._consultations == {}
        text = log.read_text(encoding="utf-8")
        assert "--- advisor: m1 ---\nanswer" in text
        assert "cancelled: stop requested; not delivered to the model, no budget used" in text

    def test_unflagged_call_counts(self, monkeypatch):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _fake_interrupt(monkeypatch, False)
        assert "error" not in json.loads(_handle({"question": "q"}))
        assert _used() == 1

    def test_no_interrupt_module_counts(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        assert "error" not in json.loads(_handle({"question": "q"}))
        assert _used() == 1

    def test_interrupt_check_raising_counts(self, monkeypatch):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        mod = types.ModuleType("tools.interrupt")
        mod.is_interrupted = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        monkeypatch.setitem(sys.modules, "tools.interrupt", mod)
        assert "error" not in json.loads(_handle({"question": "q"}))


def _fake_normalize(monkeypatch, fn):
    monkeypatch.setattr(sys.modules["hermes_cli.moa_config"], "normalize_moa_config", fn, raising=False)


class TestPresetInUse:
    """Without a preset argument Hermes uses moa.default_preset, else the FIRST
    preset listed; errors and the log must name that preset."""

    @pytest.fixture(autouse=True)
    def _fakes(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)

    def _first_preset_rule(self, cfg):
        presets = [k for k in cfg if k != "default_preset"]
        raw = cfg.get("default_preset")
        return {"default_preset": raw if raw in presets else presets[0]}

    def test_explicit_wins(self, monkeypatch):
        _fake_normalize(monkeypatch, self._first_preset_rule)
        assert consult_committee._preset_in_use({"a": {}}, "x") == ("x", "'x'")

    def test_configured_default(self, monkeypatch):
        _fake_normalize(monkeypatch, self._first_preset_rule)
        cfg = {"a": {}, "cheap": {}, "default_preset": "cheap"}
        assert consult_committee._preset_in_use(cfg, None) == ("cheap", "'cheap' (moa.default_preset)")

    def test_unset_default_uses_first_preset(self, monkeypatch):
        _fake_normalize(monkeypatch, self._first_preset_rule)
        assert consult_committee._preset_in_use({"cheap": {}, "default": {}}, None) == (
            "cheap", "'cheap' (first preset; moa.default_preset not set)")

    def test_missing_default_uses_first_preset(self, monkeypatch):
        _fake_normalize(monkeypatch, self._first_preset_rule)
        assert consult_committee._preset_in_use({"cheap": {}, "default_preset": "gone"}, None) == (
            "cheap", "'cheap' (first preset; moa.default_preset 'gone' not found)")

    def test_normalize_raising_falls_back(self, monkeypatch):
        _fake_normalize(monkeypatch, lambda cfg: 1 / 0)
        assert consult_committee._preset_in_use({"default_preset": "cheap"}, None) == (
            "cheap", "'cheap' (moa.default_preset)")
        assert consult_committee._preset_in_use({}, None) == (
            "default", "'default' (no moa.default_preset set)")

    def test_log_header_names_preset_in_use(self, tmp_path, monkeypatch):
        log = tmp_path / "c.log"
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(log))
        _install_fake_hermes_modules(
            presets={"cheap": {"reference_models": [{"model": "m1"}]},
                     "default": {"reference_models": [{"model": "m1"}]}},
            default_preset_name="cheap", run_outputs=_OK)
        _fake_normalize(monkeypatch, lambda cfg: {"default_preset": "cheap"})
        _handle({"question": "q"})
        assert "| preset cheap ===" in log.read_text(encoding="utf-8")
        log.unlink()
        _handle({"question": "q", "preset": "default"})  # explicit argument wins
        assert "| preset default ===" in log.read_text(encoding="utf-8")

    def test_empty_first_preset_error_names_it(self, monkeypatch):
        _install_fake_hermes_modules(preset_models=[])
        _fake_normalize(monkeypatch, lambda cfg: {"default_preset": "default"})
        err = json.loads(_handle({"question": "q"}))["error"]
        assert err == "MoA preset 'default' (first preset; moa.default_preset not set) has no reference models configured"


class TestLogConversation:
    def _header(self, tmp_path, monkeypatch, **ids):
        log = tmp_path / "c.log"
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(log))
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"}, **ids)
        return log.read_text(encoding="utf-8").splitlines()[0]

    def test_conversation_id(self, tmp_path, monkeypatch):
        header = self._header(tmp_path, monkeypatch, conversation_id="local-5d24", session_id="s1")
        assert header.startswith("=== ") and "| seq 1 | conversation local-5d24 | preset " in header

    def test_session_fallback(self, tmp_path, monkeypatch):
        assert "| seq 1 | session s1 | preset " in self._header(tmp_path, monkeypatch, session_id="s1")

    def test_no_id_omitted(self, tmp_path, monkeypatch):
        header = self._header(tmp_path, monkeypatch)
        assert "conversation" not in header and "session" not in header
        assert "| seq 1 | preset " in header


class TestSchemaDescription:
    def test_no_benchmark_residue(self):
        """A5: no exam-style or answer-key wording in the always-in-context description."""
        text = consult_committee.CONSULT_SCHEMA["description"].lower()
        for phrase in ("a/b/c/d", "multiple-choice", "benchmark", "answer key", "leaked"):
            assert phrase not in text, phrase

    def test_policy_neutral(self):
        """A4: when-to-call policy lives in the caller's system prompt, so the
        always-in-context description must not push call frequency."""
        text = consult_committee.CONSULT_SCHEMA["description"]
        for phrase in ("FREQUENTLY", "MANDATORY", "MUST", "cheap and fast", "FIRST"):
            assert phrase not in text, phrase

    def test_describes_contract_and_defers_to_system_prompt(self):
        text = consult_committee.CONSULT_SCHEMA["description"].lower()
        for phrase in (
            "cannot see this conversation",
            "self-contained",
            "do not include your own proposed answer",
            "per-conversation budget",
            "set by your system prompt",
            "if it gives no rule",
        ):
            assert phrase in text, phrase


class TestTruncation:
    def test_long_answer_truncated(self):
        long_text = "x" * (consult_committee._TRUNCATE_CHARS + 500)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", long_text)],
        )
        result = json.loads(_handle({"question": "q"}))
        answer = result["advisors"][0]["answer"]

        assert len(answer) <= consult_committee._TRUNCATE_CHARS + len("\n[... truncated]")
        assert answer.endswith("[... truncated]")


class TestExceptionHandling:
    def test_handler_never_raises(self):
        """The handler must catch all exceptions and return tool_error, never raise."""
        def boom(*a, **kw):
            raise RuntimeError("simulated LLM provider outage")

        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_side_effect=boom,
        )
        result = json.loads(_handle({"question": "q"}))
        assert "error" in result
        assert "committee consultation failed" in result["error"]


class TestDefaultPresetResolution:
    """No explicit preset arg → follow the config's moa.default_preset,
    not a hardcoded 'default'. Explicit preset arg still wins."""

    def test_no_preset_arg_uses_configured_default_preset(self):
        seen = {}

        def recording_run(*args, **kw):
            seen["models"] = args[0]
            return [("cheap-advisor", "answer")]

        # Only 'cheap' exists here — a hardcoded 'default' lookup would raise
        # and the handler would return "unknown MoA preset" instead.
        _install_fake_hermes_modules(
            presets={"cheap": {"reference_models": [{"model": "cheap-advisor"}]}},
            run_side_effect=recording_run,
            default_preset_name="cheap",
        )
        result = json.loads(_handle({"question": "q"}))

        assert "error" not in result
        assert seen["models"] == [{"provider": "fake", "model": "cheap-advisor", "enabled": True}]
        assert result["advisors"][0]["model"] == "cheap-advisor"

    def test_explicit_preset_overrides_configured_default(self):
        seen = {}

        def recording_run(*args, **kw):
            seen["models"] = args[0]
            return [("d", "answer")]

        _install_fake_hermes_modules(
            presets={
                "cheap": {"reference_models": [{"model": "cheap-advisor"}]},
                "default": {"reference_models": [{"model": "default-advisor"}]},
            },
            run_side_effect=recording_run,
            default_preset_name="cheap",
        )
        result = json.loads(_handle({"question": "q", "preset": "default"}))

        assert "error" not in result
        assert seen["models"] == [{"provider": "fake", "model": "default-advisor", "enabled": True}]

    def test_unknown_explicit_preset_friendly_error(self):
        # Real Hermes raises MoAPresetNotFoundError (a ValueError) — the
        # handler must map it to the friendly "unknown MoA preset" message.
        _install_fake_hermes_modules(
            presets={"cheap": {"reference_models": [{"model": "cheap-advisor"}]}},
            run_outputs=[("cheap-advisor", "answer")],
            default_preset_name="cheap",
        )
        result = json.loads(_handle({"question": "q", "preset": "nonexistent"}))

        assert "error" in result
        assert "unknown MoA preset" in result["error"]
        assert "'nonexistent'" in result["error"]


class TestCommitteeLogging:
    """Opt-in plain-text audit log: OFF by default, GUI switch or env enables."""

    def test_nothing_set_logs_nothing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert not (tmp_path / "logs" / "consult-committee.log").exists()

    def test_gui_enable_writes_default_path(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": True},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        log = tmp_path / "logs" / "consult-committee.log"
        assert log.is_file()
        text = log.read_text(encoding="utf-8")
        assert "Q:" in text
        assert "--- advisor: m1 ---" in text
        assert "answered: 1 failed: 0" in text

    def test_gui_custom_path(self, tmp_path, monkeypatch):
        log = tmp_path / "custom.log"
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}],
            run_outputs=[("m1", "Good answer"), ("m2", "[failed: down]")],
            plugin_settings={"logging": True, "log_path": str(log)},
        )
        result = _handle({"question": "what is 6x7?"})
        assert '"error"' not in result
        text = log.read_text(encoding="utf-8")
        assert "what is 6x7?" in text
        assert "--- advisor: m1 ---" in text
        assert "--- advisor: m2 [FAILED] ---" in text
        assert "[FAILED]" in text
        assert "answered: 1 failed: 1" in text

    def test_gui_explicit_disable(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": False},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert not (tmp_path / "logs" / "consult-committee.log").exists()

    def test_env_enables_when_gui_silent(self, tmp_path, monkeypatch):
        env_log = tmp_path / "env.log"
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(env_log))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert env_log.is_file()
        text = env_log.read_text(encoding="utf-8")
        assert "Q: q" in text

    def test_env_off_beats_gui_enable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", "off")
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": True},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert not (tmp_path / "logs" / "consult-committee.log").exists()

    def test_log_failure_never_breaks_handler(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(tmp_path))
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert '"answered":1' in result.replace(" ", "")

    def test_default_path_is_profile_aware(self, tmp_path, monkeypatch):
        profile_home = tmp_path / "profile-home"
        other_home = tmp_path / "other-home"
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(other_home))
        fake_const = types.ModuleType("hermes_constants")
        fake_const.get_hermes_home = lambda: profile_home
        monkeypatch.setitem(sys.modules, "hermes_constants", fake_const)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": True},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert (profile_home / "logs" / "consult-committee.log").is_file()
        assert not (other_home / "logs" / "consult-committee.log").exists()

    def test_header_includes_profile_and_model(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HERMES_SESSION_PROFILE", "seven")
        monkeypatch.delenv("HERMES_PROFILE_NAME", raising=False)
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        monkeypatch.delitem(sys.modules, "hermes_cli.profiles", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": True},
            model_value={"provider": "meta-ai", "default": "spark-1"},
        )
        monkeypatch.delitem(sys.modules, "hermes_cli.profiles", raising=False)
        result = _handle({"question": "q"})
        assert '"error"' not in result
        text = (tmp_path / "logs" / "consult-committee.log").read_text(encoding="utf-8")
        header = text.splitlines()[0]
        assert "| profile seven" in header
        assert "meta-ai:spark-1" in header
        assert "| model meta-ai:spark-1" in header

    def test_header_omits_missing_profile_and_model(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("HERMES_SESSION_PROFILE", raising=False)
        monkeypatch.delenv("HERMES_PROFILE_NAME", raising=False)
        monkeypatch.delenv("HERMES_PROFILE", raising=False)
        monkeypatch.delitem(sys.modules, "hermes_cli.profiles", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": True},
        )
        monkeypatch.delitem(sys.modules, "hermes_cli.profiles", raising=False)
        result = _handle({"question": "q"})
        assert '"error"' not in result
        text = (tmp_path / "logs" / "consult-committee.log").read_text(encoding="utf-8")
        header = text.splitlines()[0]
        assert "profile None" not in text
        assert "model None" not in text
        assert "| profile" not in header
        assert "| model" not in header
        assert "preset" in header

    def test_log_records_system_line(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": True},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        text = (tmp_path / "logs" / "consult-committee.log").read_text(encoding="utf-8")
        s_lines = [ln for ln in text.splitlines() if ln.startswith("S: ")]
        assert len(s_lines) == 1
        assert "reference advisor" in s_lines[0]

    def test_log_omits_system_when_off(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", "off")
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": True},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        text = (tmp_path / "logs" / "consult-committee.log").read_text(encoding="utf-8")
        assert "Q: q" in text.splitlines()
        assert not [ln for ln in text.splitlines() if ln.startswith("S: ")]

    def test_log_failure_with_profile_still_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(tmp_path))
        monkeypatch.setenv("HERMES_SESSION_PROFILE", "seven")
        monkeypatch.delitem(sys.modules, "hermes_cli.profiles", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            model_value={"provider": "p", "default": "m"},
        )
        monkeypatch.delitem(sys.modules, "hermes_cli.profiles", raising=False)
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert '"answered":1' in result.replace(" ", "")

    def test_multiline(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "line1\nline2")],
            plugin_settings={"logging": True},
        )
        result = _handle({"question": "first line\nsecond line"})
        assert '"error"' not in result
        text = (tmp_path / "logs" / "consult-committee.log").read_text(encoding="utf-8")
        assert "line1\nline2" in text
        q_lines = [ln for ln in text.splitlines() if ln.startswith("Q: ")]
        assert len(q_lines) == 1
        assert q_lines[0] == "Q: first line second line"

    def test_gui_relative_fixed_default_resolves_against_profile_home(
        self, tmp_path, monkeypatch
    ):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": True, "log_path": "logs/consult-committee.log"},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert (tmp_path / "logs" / "consult-committee.log").is_file()
        assert not (cwd / "logs" / "consult-committee.log").exists()

    def test_relative_gui_path_respects_get_hermes_home(self, tmp_path, monkeypatch):
        profile_home = tmp_path / "profile-home"
        other_home = tmp_path / "other-home"
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(other_home))
        fake_const = types.ModuleType("hermes_constants")
        fake_const.get_hermes_home = lambda: str(profile_home)
        monkeypatch.setitem(sys.modules, "hermes_constants", fake_const)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": True, "log_path": "logs/consult-committee.log"},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert (profile_home / "logs" / "consult-committee.log").is_file()
        assert not (other_home / "logs" / "consult-committee.log").exists()

    def test_env_relative_path_resolves_against_profile_home(
        self, tmp_path, monkeypatch
    ):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", "logs/env.log")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert (tmp_path / "logs" / "env.log").is_file()
        assert not (cwd / "logs" / "env.log").exists()

    def test_absolute_custom_path_verbatim(self, tmp_path, monkeypatch):
        log = tmp_path / "abs-custom.log"
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile-unused"))
        monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"logging": True, "log_path": str(log)},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert log.is_file()
        assert consult_committee._resolve_log_path_string(str(log)) == str(log)


class TestAdvisorSystem:
    """System prompt prepended to every advisor call; env > GUI > file > fallback."""

    _DRAFT = "You are a reference advisor. Only answer the question directly and concisely. No tool calls. No additional response."

    def test_bundled_file_loads(self, monkeypatch):
        import os

        monkeypatch.delenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", raising=False)
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
        )
        assert consult_committee._advisor_system_text() == self._DRAFT
        bundled = os.path.join(os.path.dirname(_PLUGIN_PATH), "advisor_system.md")
        with open(bundled, encoding="utf-8") as f:
            assert f.read().strip() == self._DRAFT

    def test_gui_override_wins_over_file(self, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", raising=False)
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"advisor_system": "  Custom GUI prompt.  "},
        )
        assert consult_committee._advisor_system_text() == "Custom GUI prompt."
        assert consult_committee._advisor_system_text("Direct override.") == "Direct override."

    def test_env_wins_over_gui_and_off_disables(self, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
            plugin_settings={"advisor_system": "GUI prompt"},
        )
        monkeypatch.setenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", "Env prompt")
        assert consult_committee._advisor_system_text() == "Env prompt"
        monkeypatch.setenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", "  Env padded  ")
        assert consult_committee._advisor_system_text() == "Env padded"
        monkeypatch.setenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", "")
        assert consult_committee._advisor_system_text() == "GUI prompt"
        monkeypatch.setenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", "   ")
        assert consult_committee._advisor_system_text() == "GUI prompt"
        for off in ("off", "OFF", "  off  ", "0", "false", "No"):
            monkeypatch.setenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", off)
            assert consult_committee._advisor_system_text() == ""
        assert consult_committee._advisor_messages("q", "") == [
            {"role": "user", "content": "q"}
        ]
        assert consult_committee._advisor_messages("q", "sys") == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
        ]

    def test_missing_file_falls_back_to_hardcoded(self, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", raising=False)
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer")],
        )
        monkeypatch.setattr(consult_committee, "__file__", "/nonexistent-dir-xyz/__init__.py")
        assert consult_committee._advisor_system_text() == self._DRAFT
        assert consult_committee._advisor_system_text("") == self._DRAFT

    def test_fanout_sends_system_role_first(self, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", raising=False)
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        seen = {}

        def recording_run(*args, **kw):
            seen["models"] = args[0]
            seen["messages"] = args[1]
            return [("m1", "answer")]

        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_side_effect=recording_run,
        )
        result = _handle({"question": "Is the sky blue?"})
        assert '"error"' not in result
        messages = seen["messages"]
        assert messages[0] == {"role": "system", "content": self._DRAFT}
        assert messages[1] == {"role": "user", "content": "Is the sky blue?"}

    def test_brevity_short_yes_with_draft_system(self, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", raising=False)
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        seen = {}

        def recording_run(*args, **kw):
            seen["messages"] = args[1]
            return [("m1", "YES"), ("m2", "YES")]

        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}],
            run_side_effect=recording_run,
        )
        payload = json.loads(_handle({"question": "Is the sky blue?"}))
        assert "error" not in payload
        assert seen["messages"][0]["role"] == "system"
        for adv in payload["advisors"]:
            assert adv["answer"] == "YES"
            assert len(adv["answer"].split()) <= 6


# ---------------------------------------------------------------------------
# 0.3.0: dedicated committee preset + the plugin's own fan-out
# ---------------------------------------------------------------------------
def _recording_run(seen, outputs=(("m1", "answer"),)):
    def run(slots, messages, **kw):
        seen.update(slots=slots, messages=messages, **kw)
        return list(outputs)
    return run


class TestCommitteePreset:
    """Preset: explicit argument > plugin setting `preset` (default 'committee')
    > Hermes' default rule; a missing committee preset falls back with a note."""

    _PRESETS = {
        "committee": {"reference_models": [{"model": "c-advisor"}]},
        "default": {"reference_models": [{"model": "d-advisor"}]},
    }

    def test_committee_preset_used_by_default(self):
        seen = {}
        _install_fake_hermes_modules(presets=self._PRESETS, run_side_effect=_recording_run(seen))
        payload = json.loads(_handle({"question": "q"}))
        assert "error" not in payload
        assert [s["model"] for s in seen["slots"]] == ["c-advisor"]
        assert "preset_note" not in payload

    def test_setting_names_another_preset(self):
        seen = {}
        _install_fake_hermes_modules(
            presets={**self._PRESETS, "panel": {"reference_models": [{"model": "p-advisor"}]}},
            run_side_effect=_recording_run(seen), plugin_settings={"preset": " panel "})
        assert "error" not in json.loads(_handle({"question": "q"}))
        assert [s["model"] for s in seen["slots"]] == ["p-advisor"]

    def test_explicit_argument_beats_setting(self):
        seen = {}
        _install_fake_hermes_modules(presets=self._PRESETS, run_side_effect=_recording_run(seen))
        assert "error" not in json.loads(_handle({"question": "q", "preset": "default"}))
        assert [s["model"] for s in seen["slots"]] == ["d-advisor"]

    def test_missing_committee_preset_falls_back_with_note(self, tmp_path, monkeypatch):
        log = tmp_path / "c.log"
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(log))
        seen = {}
        _install_fake_hermes_modules(
            presets={"default": self._PRESETS["default"]}, run_side_effect=_recording_run(seen))
        payload = json.loads(_handle({"question": "q"}))
        assert [s["model"] for s in seen["slots"]] == ["d-advisor"]
        assert payload["preset_note"] == "committee preset 'committee' not found; used MoA's default preset"
        lines = log.read_text(encoding="utf-8").splitlines()
        assert lines[0].endswith("| preset default ===")
        assert lines[1] == "note: committee preset 'committee' not found; used MoA's default preset"
        assert lines[2] == "Q: q"

    @pytest.mark.parametrize("value", ["", "  ", "off", "OFF"])
    def test_empty_or_off_setting_uses_moa_default_without_note(self, value):
        seen = {}
        _install_fake_hermes_modules(
            presets=self._PRESETS, run_side_effect=_recording_run(seen), plugin_settings={"preset": value})
        payload = json.loads(_handle({"question": "q"}))
        assert [s["model"] for s in seen["slots"]] == ["d-advisor"]
        assert "preset_note" not in payload

    def test_empty_committee_preset_errors_instead_of_falling_back(self):
        _install_fake_hermes_modules(
            presets={"committee": {"reference_models": []}, "default": self._PRESETS["default"]},
            run_outputs=_OK)
        err = json.loads(_handle({"question": "q"}))["error"]
        assert err == "MoA preset 'committee' (committee preset) has no reference models configured"

    @pytest.mark.parametrize("settings, expected", [
        ({}, "committee"),
        ({"preset": None}, "committee"),
        ({"preset": 5}, "committee"),
        ({"preset": " panel "}, "panel"),
        ({"preset": ""}, ""),
        ({"preset": "no"}, ""),
    ])
    def test_setting_parsing(self, settings, expected):
        assert consult_committee._committee_preset_setting(settings) == expected

    def test_manifest_declares_preset_field(self):
        import os
        manifest = open(os.path.join(os.path.dirname(_PLUGIN_PATH), "plugin.yaml"), encoding="utf-8").read()
        block = manifest.split("  preset:\n", 1)[1].split("\n  logging:", 1)[0]
        assert "type: str" in block
        assert f'default: "{consult_committee._DEFAULT_COMMITTEE_PRESET}"' in block


class TestPresetSlots:
    """Disabled advisors are skipped; Hermes' built-in advisors are never called."""

    def test_disabled_advisors_skipped(self):
        seen = {}
        _install_fake_hermes_modules(
            presets={"committee": {"reference_models": [
                {"model": "on"}, {"model": "off", "enabled": False}, {"model": "on2", "enabled": True}]}},
            run_side_effect=_recording_run(seen))
        assert "error" not in json.loads(_handle({"question": "q"}))
        assert [s["model"] for s in seen["slots"]] == ["on", "on2"]

    def test_all_disabled_errors_and_refunds(self):
        _install_fake_hermes_modules(
            presets={"committee": {"reference_models": [{"model": "off", "enabled": False}]}},
            run_outputs=_OK)
        err = json.loads(_handle({"question": "q"}))["error"]
        assert err == "MoA preset 'committee' (committee preset) has every reference model disabled"
        assert _used() == 0

    def test_preset_level_enabled_false_still_used(self):
        seen = {}
        _install_fake_hermes_modules(
            presets={"committee": {"enabled": False, "reference_models": [{"model": "c"}]}},
            run_side_effect=_recording_run(seen))
        assert "error" not in json.loads(_handle({"question": "q"}))
        assert [s["model"] for s in seen["slots"]] == ["c"]

    def test_hermes_builtin_advisors_never_called(self):
        called = []
        _install_fake_hermes_modules(
            presets={"committee": {"reference_models": [{"provider": "moa", "model": "x"}]}},
            run_side_effect=lambda *a, **kw: called.append(a) or _OK)
        err = json.loads(_handle({"question": "q"}))["error"]
        assert "has no reference models configured" in err
        assert called == []

    def _with_raw(self, monkeypatch, raw):
        fn = raw if callable(raw) else (lambda: raw)
        monkeypatch.setattr(sys.modules["hermes_cli.config"], "read_raw_config", fn, raising=False)

    def test_seeded_default_preset_is_not_user_configured(self, monkeypatch):
        # load_config() merges a seeded 'default' preset holding Hermes' built-in
        # advisors when config.yaml has no moa section; read_raw_config() has none.
        called = []
        _install_fake_hermes_modules(
            presets={"default": {"reference_models": [{"provider": "openai-codex", "model": "gpt-5.5"}]}},
            run_side_effect=lambda *a, **kw: called.append(a) or _OK)
        self._with_raw(monkeypatch, {"model": "deepseek-flash"})
        err = json.loads(_handle({"question": "q"}))["error"]
        assert err == "MoA preset 'default' (no moa.default_preset set) has no reference models configured"
        assert called == []

    def test_raw_config_with_advisors_passes(self, monkeypatch):
        seen = {}
        _install_fake_hermes_modules(
            presets={"committee": {"reference_models": [{"model": "c"}]}}, run_side_effect=_recording_run(seen))
        self._with_raw(monkeypatch, {"moa": {"presets": {"committee": {
            "reference_models": [{"provider": "fake", "model": "c"}]}}}})
        assert "error" not in json.loads(_handle({"question": "q"}))
        assert [s["model"] for s in seen["slots"]] == ["c"]

    def test_raw_reader_failing_falls_back_to_merged(self, monkeypatch):
        seen = {}
        _install_fake_hermes_modules(
            presets={"committee": {"reference_models": [{"model": "c"}]}}, run_side_effect=_recording_run(seen))
        self._with_raw(monkeypatch, lambda: 1 / 0)
        assert "error" not in json.loads(_handle({"question": "q"}))

    @pytest.mark.parametrize("moa_cfg, name, expected", [
        ({"presets": {"c": {"reference_models": [{"provider": "p", "model": "m"}]}}}, "c", True),
        ({"presets": {"c": {"reference_models": []}}}, "c", False),
        ({"presets": {"c": {}}}, "c", False),
        ({"presets": {"c": {"reference_models": [{"provider": "p"}]}}}, "c", False),
        ({"presets": {"c": {"reference_models": [{"model": "m"}]}}}, "c", False),
        ({"presets": {"c": {"reference_models": [{"provider": "MoA", "model": "m"}]}}}, "c", False),
        ({"presets": {"c": {"reference_models": {"provider": "p", "model": "m"}}}}, "c", True),
        ({"presets": {"c": {"reference_models": '[{"provider": "p", "model": "m"}]'}}}, "c", True),
        ({"presets": {"c": {"reference_models": "not json"}}}, "c", False),
        ({"presets": {"c": {"reference_models": 5}}}, "c", False),
        ({"presets": {"other": {}}}, "c", False),
        ({"reference_models": [{"provider": "p", "model": "m"}]}, "default", True),  # legacy flat
        ({}, "default", False),
        (None, "c", True),  # undecidable → don't block
    ])
    def test_has_configured_advisors(self, moa_cfg, name, expected):
        assert consult_committee._has_configured_advisors(moa_cfg, name) is expected


class TestReferenceTimeout:
    @pytest.mark.parametrize("extra, expected", [
        ({"reference_timeout": 120}, 120.0),
        ({"reference_timeout": 2.5}, 2.5),
        ({}, 900.0),
        ({"reference_timeout": None}, 900.0),
        ({"reference_timeout": 0}, 900.0),
        ({"reference_timeout": -5}, 900.0),
        ({"reference_timeout": "60"}, 900.0),
        ({"reference_timeout": True}, 900.0),
    ])
    def test_timeout_reaches_fanout(self, extra, expected):
        seen = {}
        _install_fake_hermes_modules(
            presets={"committee": {"reference_models": [{"model": "m1"}], **extra}},
            run_side_effect=_recording_run(seen))
        assert "error" not in json.loads(_handle({"question": "q"}))
        assert seen["timeout"] == expected


class _Resp:
    """Minimal chat-completions response."""

    def __init__(self, content):
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=content))]


def _install_fake_llm(monkeypatch, *, call=None, resolve=None, reasoning=None):
    """Fake agent.auxiliary_client.call_llm, hermes_cli.runtime_provider and
    hermes_constants.parse_reasoning_effort; returns the recorded call kwargs."""
    calls = []

    def _call_llm(**kw):
        calls.append(kw)
        return call(**kw) if call else _Resp(f"answer from {kw['model']}")

    aux = types.ModuleType("agent.auxiliary_client")
    aux.call_llm = _call_llm
    agent = types.ModuleType("agent")
    agent.auxiliary_client = aux
    monkeypatch.setitem(sys.modules, "agent", agent)
    monkeypatch.setitem(sys.modules, "agent.auxiliary_client", aux)

    rp = types.ModuleType("hermes_cli.runtime_provider")
    rp.resolve_runtime_provider = resolve or (lambda requested, target_model: {
        "provider": requested, "base_url": f"https://{requested}.example/v1", "api_key": "k",
        "api_mode": "chat_completions", "request_overrides": {"extra_body": {"x": 1}}})
    monkeypatch.setitem(sys.modules, "hermes_cli.runtime_provider", rp)
    hc = sys.modules.get("hermes_cli") or types.ModuleType("hermes_cli")
    monkeypatch.setitem(sys.modules, "hermes_cli", hc)

    const = types.ModuleType("hermes_constants")
    const.parse_reasoning_effort = reasoning or (lambda effort: {"effort": effort})
    monkeypatch.setitem(sys.modules, "hermes_constants", const)
    return calls


class TestOwnFanOut:
    """The real _run_advisors / _call_advisor against a fake call_llm."""

    _MSGS = [{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}]

    def _run(self, slots, **kw):
        return consult_committee._run_advisors(slots, self._MSGS, temperature=0.3, timeout=42.0, **kw)

    def test_call_kwargs(self, monkeypatch):
        calls = _install_fake_llm(monkeypatch)
        out = self._run([{"provider": "openrouter", "model": "a/b", "reasoning_effort": "high"}])
        assert out == [("openrouter:a/b[reasoning=high]", "answer from a/b")]
        (kw,) = calls
        assert kw["task"] == "consult_committee"
        assert kw["messages"] == self._MSGS  # only the plugin's own system message
        assert kw["temperature"] == 0.3 and kw["timeout"] == 42.0
        assert kw["provider"] == "openrouter" and kw["model"] == "a/b"
        assert kw["base_url"] == "https://openrouter.example/v1"
        assert kw["api_key"] == "k" and kw["api_mode"] == "chat_completions"
        assert kw["extra_body"] == {"x": 1}
        assert kw["reasoning_config"] == {"effort": "high"}

    def test_no_reasoning_effort_sends_none(self, monkeypatch):
        calls = _install_fake_llm(monkeypatch)
        self._run([{"provider": "p", "model": "m"}])
        assert calls[0]["reasoning_config"] is None

    def test_resolution_failure_calls_bare_route(self, monkeypatch):
        def boom(requested, target_model):
            raise RuntimeError("no creds")
        calls = _install_fake_llm(monkeypatch, resolve=boom)
        assert self._run([{"provider": "p", "model": "m"}]) == [("p:m", "answer from m")]
        assert {k: calls[0][k] for k in ("provider", "model")} == {"provider": "p", "model": "m"}
        assert "base_url" not in calls[0] and "api_key" not in calls[0]

    def test_call_error_becomes_failed_sentinel(self, monkeypatch):
        def call(**kw):
            if kw["model"] == "bad":
                raise RuntimeError("HTTP 429")
            return _Resp("ok")
        _install_fake_llm(monkeypatch, call=call)
        out = self._run([{"provider": "p", "model": "good"}, {"provider": "p", "model": "bad"}])
        assert out == [("p:good", "ok"), ("p:bad", "[failed: HTTP 429]")]
        assert consult_committee._is_failed_output(out[1][1])

    def test_empty_reply_counts_as_failed(self, monkeypatch):
        _install_fake_llm(monkeypatch, call=lambda **kw: _Resp("   "))
        out = self._run([{"provider": "p", "model": "m"}])
        assert out == [("p:m", "")]
        assert consult_committee._is_failed_output(out[0][1])

    def test_order_kept_and_calls_parallel(self, monkeypatch):
        import threading
        import time
        barrier = threading.Barrier(3, timeout=5)

        def call(**kw):
            barrier.wait()  # deadlocks (BrokenBarrierError) unless all three run at once
            time.sleep({"a": 0.06, "b": 0.0, "c": 0.03}[kw["model"]])
            return _Resp(kw["model"].upper())
        _install_fake_llm(monkeypatch, call=call)
        out = self._run([{"provider": "p", "model": m} for m in "abc"])
        assert out == [("p:a", "A"), ("p:b", "B"), ("p:c", "C")]

    def test_contextvars_reach_workers(self, monkeypatch):
        import contextvars
        var = contextvars.ContextVar("acct", default=None)
        _install_fake_llm(monkeypatch, call=lambda **kw: _Resp(str(var.get())))
        var.set("session-db")
        assert self._run([{"provider": "p", "model": "m"}]) == [("p:m", "session-db")]

    def test_stop_request_returns_early(self, monkeypatch):
        import threading
        import time
        release = threading.Event()

        def call(**kw):
            if kw["model"] == "slow":
                release.wait(5)
            return _Resp("done")
        _install_fake_llm(monkeypatch, call=call)
        _fake_interrupt(monkeypatch, True)
        monkeypatch.setattr(consult_committee, "_INTERRUPT_POLL_SECONDS", 0.01)
        started = time.monotonic()
        out = self._run([{"provider": "p", "model": "fast"}, {"provider": "p", "model": "slow"}])
        release.set()
        assert time.monotonic() - started < 2
        assert out[1] == ("p:slow", "[skipped: stop requested]")
        assert consult_committee._is_failed_output(out[1][1])

    def test_empty_slots(self):
        assert consult_committee._run_advisors([], [], temperature=0.9, timeout=1.0) == []

    @pytest.mark.parametrize("response, expected", [
        (_Resp(" hi "), "hi"),
        (_Resp(None), ""),
        (_Resp([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}, "junk"]), "ab"),
        (types.SimpleNamespace(choices=[types.SimpleNamespace(message={"content": "dict"})]), "dict"),
        (types.SimpleNamespace(choices=[]), ""),
        (None, ""),
    ])
    def test_response_text(self, response, expected):
        assert consult_committee._response_text(response) == expected

    def test_response_text_prefers_hermes_normalizer(self, monkeypatch):
        tr = types.ModuleType("agent.transports")
        tr.get_transport = lambda mode: types.SimpleNamespace(
            normalize_response=lambda r: types.SimpleNamespace(content=f" {mode}:{r} "))
        monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
        monkeypatch.setitem(sys.modules, "agent.transports", tr)
        assert consult_committee._response_text("raw") == "chat_completions:raw"

    def test_response_text_falls_back_when_normalizer_empty(self, monkeypatch):
        tr = types.ModuleType("agent.transports")
        tr.get_transport = lambda mode: types.SimpleNamespace(
            normalize_response=lambda r: types.SimpleNamespace(content=None))
        monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
        monkeypatch.setitem(sys.modules, "agent.transports", tr)
        assert consult_committee._response_text(_Resp("from choices")) == "from choices"

    @pytest.mark.parametrize("slot, label", [
        ({"provider": "p", "model": "m"}, "p:m"),
        ({"model": "m"}, "m"),
        ({"provider": "p", "model": "m", "reasoning_effort": "low"}, "p:m[reasoning=low]"),
    ])
    def test_slot_label(self, slot, label):
        assert consult_committee._slot_label(slot) == label

    def test_handler_end_to_end_with_real_fanout(self, monkeypatch):
        _install_fake_hermes_modules(presets={"committee": {
            "reference_models": [{"provider": "p", "model": "m1"}, {"provider": "p", "model": "m2"}],
            "reference_temperature": 0.2, "reference_timeout": 30}})
        consult_committee._run_advisors = _REAL_RUN_ADVISORS
        calls = _install_fake_llm(monkeypatch)
        monkeypatch.delenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", raising=False)
        payload = json.loads(_handle({"question": "Q?"}))
        assert payload["answered"] == 2
        assert [a["model"] for a in payload["advisors"]] == ["p:m1", "p:m2"]
        assert all(kw["temperature"] == 0.2 and kw["timeout"] == 30.0 for kw in calls)
        assert calls[0]["messages"][-1] == {"role": "user", "content": "Q?"}
        assert sum(m["role"] == "system" for m in calls[0]["messages"]) == 1
