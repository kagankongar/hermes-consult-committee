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
    consult_committee._session_budget_keys.clear()
    consult_committee._log_seq = 0
    yield
    consult_committee._consultations.clear()
    consult_committee._session_budget_keys.clear()
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


class TestBudgetInPayload:
    """X2: a delivered result tells the model how much budget is left."""

    def test_budget_field_counts_this_call(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        first = json.loads(_handle({"question": "q"}, conversation_id="c1"))
        second = json.loads(_handle({"question": "q"}, conversation_id="c1"))
        cap = consult_committee._DEFAULT_MAX_CONSULTATIONS
        assert first["budget"] == {"used": 1, "cap": cap, "scope": "conversation"}
        assert second["budget"] == {"used": 2, "cap": cap, "scope": "conversation"}
        assert f"Consultations left this conversation: {cap - 2}." in second["note"]

    def test_budget_is_per_conversation(self):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"}, conversation_id="c1")
        other = json.loads(_handle({"question": "q"}, conversation_id="c2"))
        assert other["budget"]["used"] == 1

    def test_last_allowed_call_says_none_left(self, monkeypatch):
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        monkeypatch.setattr(consult_committee, "_max_consultations", lambda *a, **k: 1)
        payload = json.loads(_handle({"question": "q"}, session_id="s1"))
        assert payload["budget"] == {"used": 1, "cap": 1, "scope": "session"}
        assert payload["note"].endswith("Consultations left this session: 0.")


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
        assert "cancelled (stop request or Hermes' tool deadline)" in result["error"]
        assert "no budget was used" in result["error"]
        assert consult_committee._consultations == {}
        text = log.read_text(encoding="utf-8")
        assert "--- advisor 1 of 1: m1 ---\nstatus:   answered (6 chars)\nanswer" in text
        assert "outcome:  cancelled (stop request or Hermes' tool deadline; answers not delivered; no budget used)" in text
        assert "budget:   0 of 5 used in this conversation" in text

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
        assert "preset:   'cheap' (moa.default_preset)" in log.read_text(encoding="utf-8")
        log.unlink()
        _handle({"question": "q", "preset": "default"})  # explicit argument wins
        assert "preset:   'default'\n" in log.read_text(encoding="utf-8")

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
        assert header.startswith("=== ") and "| seq 1 | conversation local-5d24 ===" in header

    def test_session_fallback(self, tmp_path, monkeypatch):
        assert "| seq 1 | session s1 ===" in self._header(tmp_path, monkeypatch, session_id="s1")

    def test_no_id_omitted(self, tmp_path, monkeypatch):
        header = self._header(tmp_path, monkeypatch)
        assert "conversation" not in header and "session" not in header
        assert header.endswith("| seq 1 ===")


class TestLiveModelName:
    """N1: the log names the session's live model (``-m``, ``/model``), not only config's default."""

    def _setup(self, monkeypatch, model="", provider="", raises=False, config_model="deepseek-flash"):
        def read(value):
            def reader():
                if raises:
                    raise RuntimeError("runtime unavailable")
                return value
            return reader
        aux = types.ModuleType("agent.auxiliary_client")
        aux._read_main_model, aux._read_main_provider = read(model), read(provider)
        agent = types.ModuleType("agent")
        agent.auxiliary_client = aux
        monkeypatch.setitem(sys.modules, "agent", agent)
        monkeypatch.setitem(sys.modules, "agent.auxiliary_client", aux)
        cfg = types.ModuleType("hermes_cli.config")
        cfg.load_config = lambda: {"model": config_model}
        monkeypatch.setitem(sys.modules, "hermes_cli", types.ModuleType("hermes_cli"))
        monkeypatch.setitem(sys.modules, "hermes_cli.config", cfg)

    def test_live_binding_wins_over_config(self, monkeypatch):
        self._setup(monkeypatch, model="tencent/hy4-flash", provider="openrouter")
        assert consult_committee._current_model_name() == "openrouter:tencent/hy4-flash"

    def test_live_model_without_provider(self, monkeypatch):
        self._setup(monkeypatch, model=" hy4 ")
        assert consult_committee._current_model_name() == "hy4"

    def test_empty_live_model_falls_back_to_config(self, monkeypatch):
        self._setup(monkeypatch, model="", provider="openrouter")
        assert consult_committee._current_model_name() == "deepseek-flash"

    def test_reader_raising_falls_back_to_config(self, monkeypatch):
        self._setup(monkeypatch, model="x", raises=True)
        assert consult_committee._current_model_name() == "deepseek-flash"

    def test_readers_missing_fall_back_to_config(self, monkeypatch):
        self._setup(monkeypatch)
        del sys.modules["agent.auxiliary_client"]._read_main_model
        assert consult_committee._current_model_name() == "deepseek-flash"


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
        assert "Q (1 chars): q" in text
        assert "--- advisor 1 of 1: m1 ---" in text
        assert "outcome:  delivered (1 of 1 advisors answered; budget charged)" in text

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
        assert "--- advisor 1 of 2: m1 ---" in text
        assert "--- advisor 2 of 2: m2 ---\nstatus:   failed: down" in text
        assert "outcome:  delivered (1 of 2 advisors answered; budget charged)" in text

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
        assert "Q (1 chars): q" in text

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
        assert header.endswith("===") and "preset" not in header

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
        assert "Q (1 chars): q" in text.splitlines()
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
        q_lines = [ln for ln in text.splitlines() if ln.startswith("Q (")]
        assert len(q_lines) == 1
        assert q_lines[0] == "Q (22 chars): first line second line"

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
        assert lines[0].endswith(" ===")
        assert "preset:   'default'" in lines[2]
        assert lines[3] == "note:     committee preset 'committee' not found; used MoA's default preset"
        assert "Q (1 chars): q" in lines

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
        ({"preset": False}, ""),
        ({"preset": 0}, ""),
        ({"preset": 0.0}, ""),
        ({"preset": True}, "committee"),
        ({"preset": 1}, "committee"),
        ({"preset": ["x"]}, "committee"),
        ({"preset": {"a": 1}}, "committee"),
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


class TestAdvisorSystemManifest:
    """X4: the options page shows an empty override, so what it shows is what is sent."""

    def test_manifest_default_is_empty(self):
        import os
        manifest = open(os.path.join(os.path.dirname(_PLUGIN_PATH), "plugin.yaml"), encoding="utf-8").read()
        block = manifest.split("  advisor_system:\n", 1)[1].split("\n  max_consultations:", 1)[0]
        assert 'default: ""' in block and "override" in block.lower()

    def test_empty_setting_sends_the_bundled_file(self, monkeypatch):
        import os
        monkeypatch.delenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", raising=False)
        bundled = open(os.path.join(os.path.dirname(_PLUGIN_PATH), "advisor_system.md"), encoding="utf-8").read().strip()
        assert consult_committee._advisor_system_text("") == bundled


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

    def test_flat_only_raw_config_is_not_user_configured(self, monkeypatch):
        # Hermes ignores a flat top-level list when raw `moa` has no `presets`
        # key: the merged preset holds the built-ins, so refuse and refund.
        called = []
        _install_fake_hermes_modules(
            presets={"default": {"reference_models": [{"provider": "openai-codex", "model": "gpt-5.5"}]}},
            run_side_effect=lambda *a, **kw: called.append(a) or _OK)
        self._with_raw(monkeypatch, {"moa": {"reference_models": [{"provider": "p", "model": "mine"}]}})
        err = json.loads(_handle({"question": "q"}))["error"]
        assert "has no reference models configured" in err
        assert called == []
        assert _used() == 0

    def test_preset_false_setting_uses_moa_default_rule(self):
        seen = {}
        _install_fake_hermes_modules(
            presets={"committee": {"reference_models": [{"model": "c-advisor"}]},
                     "default": {"reference_models": [{"model": "d-advisor"}]}},
            run_side_effect=_recording_run(seen),
            plugin_settings={"preset": False}, default_preset_name="default")
        payload = json.loads(_handle({"question": "q"}))
        assert "error" not in payload
        assert [s["model"] for s in seen["slots"]] == ["d-advisor"]
        assert "preset_note" not in payload

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
        ({"reference_models": [{"provider": "p", "model": "m"}]}, "default", False),  # legacy flat: Hermes keeps built-ins
        ({"presets": {}, "reference_models": [{"provider": "p", "model": "m"}]}, "default", True),
        ({"presets": {}, "reference_models": [{"provider": "p", "model": "m"}]}, "c", False),
        ({"presets": {"c": {"reference_models": [{"provider": "p", "model": "m"}]},
                      }, "reference_models": [{"provider": "p", "model": "flat"}]}, "default", False),
        ({"presets": {"c": {}}, "reference_models": [{"provider": "p", "model": "m"}]}, "c", False),
        ({"presets": {"c": {"reference_models": [{"provider": "p", "model": "m"}]}},
          "reference_models": []}, "c", True),
        ({"presets": {" c ": {"reference_models": [{"provider": "p", "model": "m"}]}}}, "c", True),
        ({"presets": {1: {"reference_models": [{"provider": "p", "model": "m"}]}}}, "1", True),
        ({"presets": [], "reference_models": [{"provider": "p", "model": "m"}]}, "default", True),
        ({"presets": "x", "reference_models": [{"provider": "p", "model": "m"}]}, "default", True),
        ({"presets": False, "reference_models": [{"provider": "p", "model": "m"}]}, "default", True),
        ({"presets": None, "reference_models": [{"provider": "p", "model": "m"}]}, "default", False),
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


class TestFallbackDiscarded:
    """A fallen-back answer (Hermes route_info) is not the advisor's."""

    _MSGS = [{"role": "user", "content": "Q"}]

    def _run(self, slots):
        return consult_committee._run_advisors(slots, self._MSGS, temperature=0.3, timeout=5.0)

    @staticmethod
    def _routed(**info):
        """Fake call_llm naming ``route_info`` (as Hermes does) that records ``info`` in it."""
        def call(*, route_info=None, **kw):
            if route_info is not None:
                route_info.update(info)
            return _Resp("other model answer")
        return call

    def _install(self, monkeypatch, call):
        _install_fake_llm(monkeypatch)
        sys.modules["agent.auxiliary_client"].call_llm = call

    def test_fallback_answer_discarded(self, monkeypatch):
        self._install(monkeypatch, self._routed(fallback_label="main-agent(x)", failure="timeout\nafter 5s"))
        ((label, text),) = self._run([{"provider": "p", "model": "m"}])
        assert label == "p:m"
        assert text.startswith("[failed:") and "main-agent(x)" in text and "\n" not in text
        assert "other model answer" not in text
        assert consult_committee._is_failed_output(text)

    def test_all_fallbacks_refund_budget(self, monkeypatch):
        self._install(monkeypatch, self._routed(fallback_label="main-agent(x)"))
        _install_fake_hermes_modules(preset_models=[{"provider": "p", "model": "m"}])
        consult_committee._run_advisors = _REAL_RUN_ADVISORS
        result = json.loads(_handle({"question": "q"}, conversation_id="c1"))
        assert "all committee advisors failed" in result["error"]
        assert consult_committee._consultations == {}

    def test_other_model_without_fallback_label_discarded(self, monkeypatch):
        self._install(monkeypatch, self._routed(provider="openrouter", model="other-model"))
        ((_, text),) = self._run([{"provider": "p", "model": "m"}])
        assert text.startswith("[failed:") and "openrouter:other-model" in text
        assert "other model answer" not in text

    @pytest.mark.parametrize("landed, slot_model", [
        ("m", "m"),
        ("default", "m"),
        ("", "m"),
        ("m", " m "),
        ("anything", "auto"),
    ])
    def test_matching_or_unknown_model_delivered(self, monkeypatch, landed, slot_model):
        self._install(monkeypatch, self._routed(provider="p", model=landed))
        ((_, text),) = self._run([{"provider": "p", "model": slot_model}])
        assert text == "other model answer"

    def _normaliser(self, monkeypatch, func):
        mod = types.ModuleType("hermes_cli.model_normalize")
        mod.normalize_model_for_provider = func
        monkeypatch.setitem(sys.modules, "hermes_cli.model_normalize", mod)
        pkg = sys.modules.get("hermes_cli") or types.ModuleType("hermes_cli")
        monkeypatch.setitem(sys.modules, "hermes_cli", pkg)

    _SLASH = {"provider": "deepseek", "model": "deepseek/deepseek-flash"}

    def test_stripped_vendor_prefix_delivered(self, monkeypatch):
        self._normaliser(monkeypatch, lambda m, p: m.split("/", 1)[-1])
        self._install(monkeypatch, self._routed(provider="deepseek", model="deepseek-flash"))
        assert self._run([self._SLASH])[0][1] == "other model answer"

    def test_vendor_prefix_other_model_discarded(self, monkeypatch):
        self._normaliser(monkeypatch, lambda m, p: m.split("/", 1)[-1])
        self._install(monkeypatch, self._routed(provider="deepseek", model="other-model"))
        assert "deepseek:other-model" in self._run([self._SLASH])[0][1]

    def test_slash_id_without_normaliser_not_flagged(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "hermes_cli.model_normalize", None)
        self._install(monkeypatch, self._routed(provider="deepseek", model="deepseek-flash"))
        assert self._run([self._SLASH])[0][1] == "other model answer"

    def test_plain_id_without_normaliser_still_flagged(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "hermes_cli.model_normalize", None)
        self._install(monkeypatch, self._routed(provider="p", model="other-model"))
        assert self._run([{"provider": "p", "model": "m"}])[0][1].startswith("[failed:")

    def test_normaliser_not_consulted_without_slash(self, monkeypatch):
        asked = []
        self._normaliser(monkeypatch, lambda m, p: asked.append(m) or m)
        self._install(monkeypatch, self._routed(provider="p", model="other-model"))
        assert self._run([{"provider": "p", "model": "m"}])[0][1].startswith("[failed:")
        assert asked == []

    def test_strict_signature_without_route_info(self, monkeypatch):
        seen = []

        def strict(task, messages, temperature, timeout, reasoning_config, provider, model, base_url=None,
                   api_key=None, api_mode=None, extra_body=None):
            seen.append(model)
            return _Resp("plain")
        _install_fake_llm(monkeypatch)
        sys.modules["agent.auxiliary_client"].call_llm = strict
        assert self._run([{"provider": "p", "model": "m"}]) == [("p:m", "plain")]
        assert seen == ["m"]

    def test_bare_kwargs_wrapper_gets_no_route_info(self, monkeypatch):
        calls = _install_fake_llm(monkeypatch)  # fake takes **kw only: models an old Hermes
        assert self._run([{"provider": "p", "model": "m"}]) == [("p:m", "answer from m")]
        assert "route_info" not in calls[0]

    def test_normal_call_gets_route_info(self, monkeypatch):
        seen = []

        def call(*, route_info=None, **kw):
            seen.append(route_info)
            return _Resp("ok")
        self._install(monkeypatch, call)
        assert self._run([{"provider": "p", "model": "m"}]) == [("p:m", "ok")]
        assert seen == [{}]


class TestFanoutDeadline:
    _MSGS = [{"role": "user", "content": "Q"}]

    def _run(self):
        slots = [{"provider": "p", "model": "fast"}, {"provider": "p", "model": "slow"}]
        return consult_committee._run_advisors(slots, self._MSGS, temperature=0.3, timeout=5.0)

    def test_deadline_returns_partial_answers(self, monkeypatch):
        import threading
        import time
        release = threading.Event()

        def call(**kw):
            if kw["model"] == "slow":
                release.wait(5)
            return _Resp("done")
        _install_fake_llm(monkeypatch, call=call)
        _fake_interrupt(monkeypatch, False)
        monkeypatch.setattr(consult_committee, "_fanout_deadline_seconds", lambda: 1.0)
        started = time.monotonic()
        try:
            out = self._run()
        finally:
            release.set()
        assert time.monotonic() - started < 3
        assert out[0] == ("p:fast", "done")
        assert out[1][1].startswith("[skipped: committee deadline")
        assert consult_committee._is_failed_output(out[1][1])

    def test_stop_wins_over_deadline_wording(self, monkeypatch):
        import threading
        release = threading.Event()
        _install_fake_llm(monkeypatch, call=lambda **kw: release.wait(5) or _Resp("x"))
        _fake_interrupt(monkeypatch, True)
        monkeypatch.setattr(consult_committee, "_INTERRUPT_POLL_SECONDS", 0.01)
        monkeypatch.setattr(consult_committee, "_fanout_deadline_seconds", lambda: 60.0)
        try:
            out = self._run()
        finally:
            release.set()
        assert out[1] == ("p:slow", "[skipped: stop requested]")

    def test_deadline_is_not_a_cancellation(self, monkeypatch):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}],
            run_outputs=[("m1", "answer"), ("m2", "[skipped: committee deadline reached after 5s]")])
        _fake_interrupt(monkeypatch, False)
        result = json.loads(_handle({"question": "q"}, conversation_id="c1"))
        assert result["answered"] == 1 and result["failed"] == 1
        assert sum(consult_committee._consultations.values()) == 1

    def _resolver(self, monkeypatch, seq):
        te = types.ModuleType("agent.tool_executor")
        te._resolve_sequential_tool_timeout = seq
        agent = types.ModuleType("agent")
        agent.tool_executor = te
        monkeypatch.setitem(sys.modules, "agent", agent)
        monkeypatch.setitem(sys.modules, "agent.tool_executor", te)

    @pytest.mark.parametrize("value, expected", [
        (None, None),
        (0, None),
        (420, 400.0),
        (1200, 1180.0),
        (30, 15.0),
        (2.0, 1.0),
    ])
    def test_helper_sequential_resolver(self, monkeypatch, value, expected):
        self._resolver(monkeypatch, lambda: value)
        assert consult_committee._fanout_deadline_seconds() == expected

    def test_helper_default_without_hermes(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "agent", types.ModuleType("agent"))
        monkeypatch.delitem(sys.modules, "agent.tool_executor", raising=False)
        assert consult_committee._fanout_deadline_seconds() == 400.0

    def test_helper_resolver_raising(self, monkeypatch):
        self._resolver(monkeypatch, lambda: 1 / 0)
        assert consult_committee._fanout_deadline_seconds() == 400.0


class TestDaemonPool:
    _MSGS = [{"role": "user", "content": "Q"}]

    def test_uses_hermes_daemon_pool(self, monkeypatch):
        import concurrent.futures
        created = []

        class Recording(concurrent.futures.ThreadPoolExecutor):
            def __init__(self, *a, **kw):
                created.append(self)
                super().__init__(*a, **kw)
        mod = types.ModuleType("tools.daemon_pool")
        mod.DaemonThreadPoolExecutor = Recording
        monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
        monkeypatch.setitem(sys.modules, "tools.daemon_pool", mod)
        _install_fake_llm(monkeypatch)
        out = consult_committee._run_advisors([{"provider": "p", "model": "m"}], self._MSGS, temperature=0.3, timeout=5.0)
        assert out == [("p:m", "answer from m")]
        assert len(created) == 1

    def test_stdlib_fallback(self, monkeypatch):
        import concurrent.futures
        monkeypatch.setitem(sys.modules, "tools", types.ModuleType("tools"))
        monkeypatch.delitem(sys.modules, "tools.daemon_pool", raising=False)
        assert consult_committee._executor_class() is concurrent.futures.ThreadPoolExecutor
        _install_fake_llm(monkeypatch)
        out = consult_committee._run_advisors([{"provider": "p", "model": "m"}], self._MSGS, temperature=0.3, timeout=5.0)
        assert out == [("p:m", "answer from m")]


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


class TestLogBlockPerExit:
    """Exactly one block per tool call, written at exit, for every exit."""

    def _log(self, tmp_path, monkeypatch):
        log = tmp_path / "c.log"
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(log))
        monkeypatch.setattr(consult_committee, "_fanout_deadline_seconds", lambda: 400.0)
        _fake_interrupt(monkeypatch, False)
        return log

    def _outcome(self, block):
        lines = [ln for ln in block.splitlines() if ln.startswith("outcome:  ")]
        assert len(lines) == 1
        return lines[0][len("outcome:  "):]

    def _one(self, log):
        text = log.read_text(encoding="utf-8")
        assert text.startswith("=== ") and "\n=== " not in text  # exactly one block
        return text

    def test_delivered_full_block(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        monkeypatch.setenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", "sys one")
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}], run_outputs=[("m1", "A" * 7000), ("m2", "[failed: down]")])
        _handle({"question": "q" * 4100}, conversation_id="c1")
        text = self._one(log)
        lines = text.splitlines()
        assert lines[0].startswith("=== 20") and " UTC | seq 1 | conversation c1" in lines[0]
        assert lines[1].startswith("plugin:   consult-committee ")
        assert lines[1].split()[-1][0].isdigit()
        assert lines[2].startswith("preset:   'default'")
        assert "budget:   1 of 5 used in this conversation" in lines
        timing = next(ln for ln in lines if ln.startswith("timing:   "))
        assert " s elapsed; deadline 400 s; advisor timeout 900 s; temperature 0.9" in timing
        assert "outcome:  delivered (1 of 2 advisors answered; budget charged)" in lines
        assert "Q (4100 chars, first 4000 shown): " + "q" * 4000 in lines
        assert "S: sys one" in lines
        assert "status:   answered (6000 chars, truncated from 7000 chars)" in lines
        assert "status:   failed: down" in lines
        assert "A" * 6000 + "\n[... truncated]" in text

    def test_deadline_cut(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}],
            run_outputs=[("m1", "ok"), ("m2", "[skipped: committee deadline reached after 14s]")])
        _handle({"question": "q"}, conversation_id="c1")
        text = self._one(log)
        assert "outcome:  delivered after deadline cut (1 of 2 advisors answered; budget charged)" in text
        assert "--- advisor 2 of 2: m2 ---\nstatus:   skipped: committee deadline reached after 14s\n" in text

    def test_no_deadline_and_missing_timing_parts(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        monkeypatch.setattr(consult_committee, "_fanout_deadline_seconds", lambda: None)
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"})
        assert "; no deadline; " in log.read_text(encoding="utf-8")

    def test_all_failed(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}], run_outputs=[("m1", "[failed: x]"), ("m2", "")])
        _handle({"question": "q"}, conversation_id="c1")
        text = self._one(log)
        assert "outcome:  all advisors failed (0 of 2 answered; no budget used)" in text
        assert "budget:   0 of 5 used in this conversation" in text
        assert "status:   empty reply" in text

    def test_cancelled(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _fake_interrupt(monkeypatch, True)
        _handle({"question": "q"}, conversation_id="c1")
        text = self._one(log)
        assert ("outcome:  cancelled (stop request or Hermes' tool deadline; "
                "answers not delivered; no budget used)") in text

    def test_stop_skipped_status(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=[("m1", "[skipped: stop requested]")])
        _handle({"question": "q"})
        assert "status:   skipped: stop requested\n" in log.read_text(encoding="utf-8")

    def test_fallback_discarded_and_legacy_error_status(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        fb = consult_committee._fallback_failure({"fallback_label": "main-agent(deepseek)", "failure": "boom"})
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}, {"model": "m3"}],
            run_outputs=[("m1", fb), ("m2", "[error: old]"), ("m3", "fine")])
        _handle({"question": "q"})
        text = log.read_text(encoding="utf-8")
        assert ("status:   fallback discarded: advisor unavailable (boom); Hermes fell back to "
                "main-agent(deepseek), answer discarded\n") in text
        assert "status:   failed: [error: old]\n" in text
        assert "status:   answered (4 chars)\nfine" in text

    def test_budget_exhausted_has_no_seq_and_consumes_no_seq(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        monkeypatch.setattr(consult_committee, "_max_consultations", lambda: 1)
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"}, conversation_id="c1")
        _handle({"question": "second"}, conversation_id="c1")
        _handle({"question": "third"}, conversation_id="c2")
        blocks = log.read_text(encoding="utf-8").split("=== ")[1:]
        assert len(blocks) == 3
        assert "| seq 1 |" in blocks[0] and "seq" not in blocks[1].splitlines()[0]
        assert blocks[1].splitlines()[0].endswith("| conversation c1 ===")
        assert "outcome:  refused: budget exhausted (1 consultations this conversation; no budget used)" in blocks[1]
        assert "budget:   1 of 1 used in this conversation" in blocks[1]
        assert "--- advisor" not in blocks[1] and "Q (6 chars): second" in blocks[1]
        assert "| seq 2 |" in blocks[2]

    def test_budget_cap_zero_and_process_bucket(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        monkeypatch.setattr(consult_committee, "_max_consultations", lambda: 0)
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"})
        text = log.read_text(encoding="utf-8")
        assert "budget:   0 of 0 used (consult_committee disabled by max_consultations 0)" in text
        assert "outcome:  refused: budget exhausted (0 consultations this gateway process; no budget used)" in text
        assert text.splitlines()[0].endswith(" UTC ===")

    def test_process_bucket_budget_wording(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q"})
        assert "budget:   1 of 5 used in this gateway process" in log.read_text(encoding="utf-8")

    def test_empty_question(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        for name in [m for m in sys.modules if m.startswith("hermes_cli")]:
            monkeypatch.delitem(sys.modules, name)
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        assert json.loads(_handle({"question": "  "})) == {"error": "question is required"}
        text = self._one(log)
        assert "outcome:  refused: question is required (no budget used)" in text
        assert "seq" not in text.splitlines()[0] and "\nQ" not in text and "budget:" not in text
        assert consult_committee._log_seq == 0

    def test_unknown_preset(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        _handle({"question": "q", "preset": "nope"}, conversation_id="c1")
        text = self._one(log)
        assert "outcome:  refused: unknown MoA preset 'nope' (no budget used)" in text
        assert "budget:   0 of 5 used in this conversation" in text and "| seq 1 |" in text
        assert "--- advisor" not in text and "timing:   " in text and "deadline" not in text

    def test_no_models_and_all_disabled(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        _install_fake_hermes_modules(preset_models=[])
        _handle({"question": "q"})
        _install_fake_hermes_modules(preset_models=[{"model": "m1", "enabled": False}])
        _handle({"question": "q"})
        first, second = log.read_text(encoding="utf-8").split("=== ")[1:]
        assert "outcome:  refused: MoA preset 'default'" in first
        assert "has no reference models configured (no budget used)" in first
        assert "has every reference model disabled (no budget used)" in second

    def test_exception(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        def boom(*a, **kw):
            raise RuntimeError("bad\nthing")
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_side_effect=boom)
        result = json.loads(_handle({"question": "q"}, conversation_id="c1"))
        assert result == {"error": "committee consultation failed: RuntimeError('bad\\nthing')"}
        text = self._one(log)
        assert "outcome:  error: RuntimeError('bad\\nthing') (no budget used)" in text
        assert "budget:   0 of 5 used" in text

    def test_committee_note_line(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        _install_fake_hermes_modules(
            presets={"default": {"reference_models": [{"model": "m1"}]}}, run_outputs=_OK)
        _handle({"question": "q"})
        assert "note:     committee preset 'committee' not found; used MoA's default preset\n" in log.read_text(encoding="utf-8")

    def test_logging_off_writes_nothing_and_skips_work(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", "off")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        def explode(*a, **kw):
            raise AssertionError("log work done while logging is off")
        monkeypatch.setattr(consult_committee, "_maybe_log_call_text", explode)
        monkeypatch.setattr(consult_committee, "_plugin_version_text", explode)
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        assert "error" not in json.loads(_handle({"question": "q"}))
        assert _handle({"question": ""}) == json.dumps({"error": "question is required"})
        assert list(tmp_path.iterdir()) == []

    def test_log_failure_keeps_result(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(tmp_path))  # a directory: unwritable
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        assert json.loads(_handle({"question": "q"}))["answered"] == 1

    def test_one_block_per_call(self, tmp_path, monkeypatch):
        log = self._log(tmp_path, monkeypatch)
        _install_fake_hermes_modules(preset_models=[{"model": "m1"}], run_outputs=_OK)
        for _ in range(3):
            _handle({"question": "q"}, conversation_id="c1")
        assert log.read_text(encoding="utf-8").count("\n=== ") + 1 == 3
        assert log.read_text(encoding="utf-8").count("outcome:  ") == 3

    def test_plugin_version_read_from_plugin_yaml(self):
        consult_committee._plugin_version = None
        version = consult_committee._plugin_version_text()
        assert version and version[0].isdigit()
        assert f"version: {version}" in open(
            __import__("os").path.join(__import__("os").path.dirname(_PLUGIN_PATH), "plugin.yaml")).read()

    def test_plugin_version_unknown_omitted(self, monkeypatch):
        monkeypatch.setattr(consult_committee, "_plugin_version", "")
        block = consult_committee._format_log_block({"ts": "T", "version": consult_committee._plugin_version_text()})
        assert "plugin:   consult-committee\n" in block


# ---------------------------------------------------------------------------
# /committee status | doctor (X1)
# ---------------------------------------------------------------------------
_LUNA = {"provider": "openrouter", "model": "openai/gpt-6-luna"}
_HAIKU = {"provider": "openrouter", "model": "anthropic/claude-haiku-5.5"}


class TestCommitteeCommand:
    @pytest.fixture(autouse=True)
    def _env(self, tmp_path, monkeypatch):
        """Profile home in tmp; no live runtime binding, no Hermes deadline resolver."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        for name in ("hermes_constants", "agent.auxiliary_client", "agent.tool_executor"):
            monkeypatch.setitem(sys.modules, name, None)
        monkeypatch.setattr(consult_committee, "_seam_check", lambda: ("OK", "Hermes internals: 23 of 23 present"))
        self.home = tmp_path

    def _setup(self, presets=None, settings=None, model="z-ai/glm-5.3-flash", default=None):
        presets = presets if presets is not None else {
            "default": {"reference_models": [_LUNA]},
            "committee": {"reference_models": [_LUNA, _HAIKU]},
        }
        _install_fake_hermes_modules(presets=presets, plugin_settings=settings or {},
                                     model_value=model, default_preset_name=default,
                                     run_outputs=[("a", "answer")])

    def test_usage_for_unknown_subcommand(self):
        self._setup()
        assert consult_committee._committee_command("frobnicate") == "Usage: /committee [status|doctor]"

    def test_status_is_the_default(self):
        self._setup()
        assert consult_committee._committee_command("").splitlines()[0].startswith("consult-committee ")
        assert consult_committee._committee_command("  STATUS ") == consult_committee._committee_command("")

    def test_status_lists_preset_advisors_tier_budget_log(self):
        self._setup(presets={"committee": {"reference_models": [_LUNA, {**_HAIKU, "enabled": False}],
                                           "reference_temperature": 0.4, "reference_timeout": 120}})
        text = consult_committee._committee_command("status", session_id="s1")
        assert "Preset: 'committee' (committee preset)" in text
        assert "Advisors: openrouter:openai/gpt-6-luna (disabled: openrouter:anthropic/claude-haiku-5.5)" in text
        assert "Advisor calls: temperature 0.4, timeout 120 s, committee deadline 400 s" in text
        assert "Escalation tier: off" in text
        assert "Budget: 0 of 5 used (no committee call in this session yet)" in text
        assert "Call log: off" in text

    def test_status_budget_follows_the_sessions_conversation(self):
        self._setup()
        _handle({"question": "q"}, session_id="s1", conversation_id="c1")
        _handle({"question": "q"}, session_id="s1", conversation_id="c1")
        assert "Budget: 2 of 5 used in this conversation" in consult_committee._committee_command("", session_id="s1")
        assert "no committee call in this session yet" in consult_committee._committee_command("", session_id="s2")

    def test_status_budget_disabled(self):
        self._setup(settings={"max_consultations": 0})
        assert "Budget: disabled (Consultations per conversation is 0)" in consult_committee._committee_command("")

    def test_status_names_the_fallback(self):
        self._setup(presets={"default": {"reference_models": [_LUNA]}}, default="default")
        text = consult_committee._committee_command("")
        assert "Preset: 'default' (moa.default_preset); committee preset 'committee' not found" in text

    def test_status_tier_skipped_by_soul(self):
        self._setup(settings={"escalation_tier": "high"})
        (self.home / "SOUL.md").write_text("## Committee Escalation (high)\nrules\n", encoding="utf-8")
        assert "Escalation tier: high (not injected: SOUL.md has a '## Committee Escalation' block)" \
            in consult_committee._committee_command("")

    def test_status_survives_a_broken_config(self):
        self._setup()
        def boom():
            raise RuntimeError("config broken")
        sys.modules["hermes_cli.config"].load_config = boom
        text = consult_committee._committee_command("status")
        assert "Advisors: none usable (could not read the MoA config" in text

    def test_doctor_clean_setup(self):
        self._setup(settings={"escalation_tier": "medium"})
        text = consult_committee._committee_command("doctor")
        assert "[OK] Preset 'committee' (committee preset)" in text
        assert "[OK] No advisor is the acting model (z-ai/glm-5.3-flash)" in text
        assert "[OK] Escalation policy: tier medium" in text
        assert text.endswith("No problems found")

    def test_doctor_warns_on_missing_committee_preset(self):
        self._setup(presets={"default": {"reference_models": [_LUNA]}})
        text = consult_committee._committee_command("doctor")
        assert "[WARN] committee preset 'committee' not found; used MoA's default preset" in text
        assert "hermes moa configure committee" in text

    def test_doctor_fails_on_preset_without_advisors(self):
        self._setup(presets={"committee": {"reference_models": [{**_LUNA, "enabled": False}]}})
        text = consult_committee._committee_command("doctor")
        assert "[FAIL] MoA preset 'committee' (committee preset) has every reference model disabled" in text
        assert "1 failed" in text

    def test_doctor_counts_one_warning_singular(self):
        self._setup(presets={"default": {"reference_models": [_LUNA]}}, settings={"escalation_tier": "low"})
        assert consult_committee._committee_command("doctor").endswith("0 failed, 1 warning")

    def test_doctor_warns_when_an_advisor_is_the_acting_model(self):
        self._setup(presets={"committee": {"reference_models": [
            {"provider": "meta-ai", "model": "muse-spark-1.3-contributor"}, _HAIKU]}},
            model={"provider": "meta-ai", "default": "muse-spark-1.3-contributor"})
        text = consult_committee._committee_command("doctor")
        assert "[WARN] Advisor meta-ai:muse-spark-1.3-contributor is the acting model " \
               "(muse-spark-1.3-contributor)" in text

    def test_doctor_ignores_a_disabled_same_model_advisor(self):
        self._setup(presets={"committee": {"reference_models": [{**_LUNA, "enabled": False}, _HAIKU]}},
                    model="openai/gpt-6-luna")
        assert "[OK] No advisor is the acting model" in consult_committee._committee_command("doctor")

    def test_doctor_warns_when_soul_overrides_the_tier(self):
        self._setup(settings={"escalation_tier": "high"})
        (self.home / "SOUL.md").write_text("intro\n## Committee Escalation - High\nold rule\n", encoding="utf-8")
        assert "[WARN] Escalation tier is high, but SOUL.md has a '## Committee Escalation' block" \
            in consult_committee._committee_command("doctor")

    def test_doctor_notes_missing_policy(self):
        self._setup()
        assert "[NOTE] No escalation policy" in consult_committee._committee_command("doctor")

    @pytest.mark.parametrize("a, b, same", [
        ("openai/gpt-6-luna", "gpt-6-luna", True),
        ("z-ai/glm-5.3-flash:US", "Z-AI/GLM-5.3-Flash", True),
        ("openai/gpt-6-luna-pro", "openai/gpt-6-luna", False),
        ("", "", True),
    ])
    def test_model_id(self, a, b, same):
        assert (consult_committee._model_id(a) == consult_committee._model_id(b)) is same


class TestSeamCheckLine:
    def test_missing_script_is_a_note(self, monkeypatch):
        monkeypatch.setattr(consult_committee, "_SEAMS_SCRIPT", "scripts/nope.py")
        level, text = consult_committee._seam_check()
        assert level == "NOTE" and "is not installed" in text

    def test_without_hermes_every_seam_is_reported(self, monkeypatch):
        for name in ("hermes_constants", "hermes_cli", "agent", "tools"):
            monkeypatch.setitem(sys.modules, name, None)
        level, text = consult_committee._seam_check()
        assert level == "WARN" and "Hermes internals changed" in text

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(consult_committee.importlib.util, "spec_from_file_location",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert consult_committee._seam_check() == ("NOTE", "Hermes internals not checked: RuntimeError('boom')")
