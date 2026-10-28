"""Unit tests for the consult_committee plugin handler.

The handler imports Hermes-internal modules lazily inside the function body,
so we mock those imports at the module level via sys.modules before each test
that reaches the fan-out path. This keeps the tests fully self-contained —
no live LLM calls, no Hermes installation required.

Test suite contributed by isgarlo (upstream PR #2); extended here with
2-tuple backward-compat cases for the shape-tolerant unpack.
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


# ---------------------------------------------------------------------------
# Helpers: build a fake Hermes-internal module bundle
# ---------------------------------------------------------------------------
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
    """Inject mock Hermes modules into sys.modules and return the run mock."""
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

    # hermes_cli.config
    config_mod = types.ModuleType("hermes_cli.config")
    if presets is not None:
        moa_config_dict = presets
    else:
        moa_config_dict = {preset_name: {"reference_models": preset_models or []}}
    def _load_config():
        cfg = {"moa": moa_config_dict}
        if model_value is not None:
            cfg["model"] = model_value
        if plugin_settings is not None:
            cfg["plugins"] = {"entries": {"consult-committee": {"settings": plugin_settings}}}
        return cfg
    config_mod.load_config = _load_config
    config_mod.default_preset = default_preset_name
    hermes_cli_mod = types.ModuleType("hermes_cli")
    hermes_cli_mod.config = config_mod
    sys.modules["hermes_cli"] = hermes_cli_mod
    sys.modules["hermes_cli.config"] = config_mod

    # hermes_cli.moa_config
    moa_config_mod = types.ModuleType("hermes_cli.moa_config")

    def _resolve(cfg, name):
        # Mirrors real resolve_moa_preset: None → config's default_preset
        # (→ 'default' when unset); unknown preset raises ValueError.
        resolved = str(name or getattr(config_mod, "default_preset", None) or "default").strip()
        if resolved not in cfg:
            raise ValueError(f"MoA preset '{resolved}' was not found.")
        return cfg[resolved]

    moa_config_mod.resolve_moa_preset = _resolve
    hermes_cli_mod.moa_config = moa_config_mod
    sys.modules["hermes_cli.moa_config"] = moa_config_mod

    # agent.moa_loop
    agent_mod = types.ModuleType("agent")
    moa_loop_mod = types.ModuleType("agent.moa_loop")

    if run_side_effect is not None:
        run_mock = run_side_effect
    else:
        run_mock = lambda *a, **kw: run_outputs
    moa_loop_mod._run_references_parallel = run_mock
    agent_mod.moa_loop = moa_loop_mod
    sys.modules["agent"] = agent_mod
    sys.modules["agent.moa_loop"] = moa_loop_mod

    return run_mock


@pytest.fixture(autouse=True)
def _reset_escalation_counter():
    """Reset the module-level escalation counter between tests."""
    consult_committee._escalation_count = 0
    yield
    consult_committee._escalation_count = 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestThreeTupleUnpacking:
    """The core fix: _run_references_parallel returns 3-tuples (label, text, accounting)."""

    def test_three_tuples_unpacked_correctly(self):
        run_mock = _install_fake_hermes_modules(
            preset_models=[{"model": "claude-sonnet-4"}],
            run_outputs=[
                ("claude-sonnet-4", "The answer is 42.", {"tokens": 100}),
                ("gpt-5.5", "I agree, 42.", {"tokens": 80}),
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
        # The accounting dict was discarded by the handler and must NOT appear
        assert "accounting" not in payload["advisors"][0]

    def test_mixed_success_and_failure(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}, {"model": "m3"}],
            run_outputs=[
                ("m1", "Good answer", {"tokens": 10}),
                ("m2", "[error] auth failed", {"tokens": 0}),
                ("m3", "", {"tokens": 0}),
            ],
        )
        result = _handle({"question": "hard question"})
        payload = json.loads(result)

        assert payload["answered"] == 1
        assert payload["failed"] == 2
        assert payload["advisors"][0]["failed"] is False
        assert payload["advisors"][1]["failed"] is True
        assert payload["advisors"][2]["failed"] is True


class TestBudgetCap:
    def test_budget_exhausted_after_max_escalations(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer", {})],
        )

        # Use up all 5 allowed escalations
        for _ in range(consult_committee._MAX_ESCALATIONS):
            r = _handle({"question": "q"})
            assert "error" not in json.loads(r)

        # 6th call should be rejected
        result = json.loads(_handle({"question": "q"}))
        assert "error" in result
        assert "budget exhausted" in result["error"]

    def test_budget_counter_is_per_process(self):
        """The counter is module-level, not per-call — verify it increments."""
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer", {})],
        )
        assert consult_committee._escalation_count == 0
        _handle({"question": "q"})
        assert consult_committee._escalation_count == 1
        _handle({"question": "q"})
        assert consult_committee._escalation_count == 2


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
                ("m1", "[error] timeout", {"tokens": 0}),
                ("m2", "failed: quota exceeded", {"tokens": 0}),
            ],
        )
        result = json.loads(_handle({"question": "q"}))
        assert "error" in result
        assert "all committee advisors failed" in result["error"]


class TestTruncation:
    def test_long_answer_truncated(self):
        long_text = "x" * (consult_committee._TRUNCATE_CHARS + 500)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", long_text, {"tokens": 9999})],
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


class TestTwoTupleBackCompat:
    """Older Hermes releases yield 2-tuples (label, text) — still parse."""

    def test_two_tuples_unpacked_correctly(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}],
            run_outputs=[
                ("m1", "The answer is 42."),
                ("m2", "I agree, 42."),
            ],
        )
        result = _handle({"question": "What is 6 × 7?"})
        payload = json.loads(result)

        assert payload["answered"] == 2
        assert payload["failed"] == 0
        assert payload["advisors"][0]["answer"] == "The answer is 42."
        assert payload["advisors"][1]["answer"] == "I agree, 42."

    def test_mixed_two_and_three_tuples(self):
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}, {"model": "m2"}],
            run_outputs=[
                ("m1", "Old shape."),
                ("m2", "New shape.", {"tokens": 5}),
            ],
        )
        result = _handle({"question": "shapes?"})
        payload = json.loads(result)

        assert payload["answered"] == 2
        assert payload["advisors"][0]["answer"] == "Old shape."
        assert payload["advisors"][1]["answer"] == "New shape."


class TestDefaultPresetResolution:
    """No explicit preset arg → follow the config's moa.default_preset,
    not a hardcoded 'default'. Explicit preset arg still wins."""

    def test_no_preset_arg_uses_configured_default_preset(self):
        seen = {}

        def recording_run(*args, **kw):
            seen["models"] = args[0]
            return [("cheap-advisor", "answer", {})]

        # Only 'cheap' exists here — a hardcoded 'default' lookup would raise
        # and the handler would return "unknown MoA preset" instead.
        _install_fake_hermes_modules(
            presets={"cheap": {"reference_models": [{"model": "cheap-advisor"}]}},
            run_side_effect=recording_run,
            default_preset_name="cheap",
        )
        result = json.loads(_handle({"question": "q"}))

        assert "error" not in result
        assert seen["models"] == [{"model": "cheap-advisor"}]
        assert result["advisors"][0]["model"] == "cheap-advisor"

    def test_explicit_preset_overrides_configured_default(self):
        seen = {}

        def recording_run(*args, **kw):
            seen["models"] = args[0]
            return [("d", "answer", {})]

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
        assert seen["models"] == [{"model": "default-advisor"}]

    def test_unknown_explicit_preset_friendly_error(self):
        # Real Hermes raises MoAPresetNotFoundError (a ValueError) — the
        # handler must map it to the friendly "unknown MoA preset" message.
        _install_fake_hermes_modules(
            presets={"cheap": {"reference_models": [{"model": "cheap-advisor"}]}},
            run_outputs=[("cheap-advisor", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert not (tmp_path / "logs" / "consult-committee.log").exists()

    def test_gui_enable_writes_default_path(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "Good answer", {}), ("m2", "[error] down", {})],
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
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
            plugin_settings={"logging": True},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert not (tmp_path / "logs" / "consult-committee.log").exists()

    def test_log_failure_never_breaks_handler(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(tmp_path))
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
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

    def test_log_failure_with_profile_still_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(tmp_path))
        monkeypatch.setenv("HERMES_SESSION_PROFILE", "seven")
        monkeypatch.delitem(sys.modules, "hermes_cli.profiles", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "line1\nline2", {})],
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
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
            plugin_settings={"logging": True, "log_path": str(log)},
        )
        result = _handle({"question": "q"})
        assert '"error"' not in result
        assert log.is_file()
        assert consult_committee._resolve_log_path_string(str(log)) == str(log)


class TestAdvisorSystem:
    """System prompt prepended to every advisor call; env > GUI > file > fallback."""

    _DRAFT = "You are a reference advisor. Answer the question directly and concise. No tool calls."

    def test_bundled_file_loads(self, monkeypatch):
        import os

        monkeypatch.delenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", raising=False)
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
            plugin_settings={"advisor_system": "  Custom GUI prompt.  "},
        )
        assert consult_committee._advisor_system_text() == "Custom GUI prompt."
        assert consult_committee._advisor_system_text("Direct override.") == "Direct override."

    def test_env_wins_over_gui_and_off_disables(self, monkeypatch):
        monkeypatch.delenv("CONSULT_COMMITTEE_LOG", raising=False)
        _install_fake_hermes_modules(
            preset_models=[{"model": "m1"}],
            run_outputs=[("m1", "answer", {})],
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
            run_outputs=[("m1", "answer", {})],
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
            return [("m1", "answer", {})]

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
            return [("m1", "YES", {}), ("m2", "YES", {})]

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
