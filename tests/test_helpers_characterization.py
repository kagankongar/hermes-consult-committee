"""Characterization tests for the plugin's "never raises" helpers.

Written before the A10 cleanup to pin current observable behaviour of the
identity, log-path, advisor-system and log-writer helpers, so the refactor can
prove it changed nothing. Hermes modules are faked via sys.modules; a module
set to None makes its import fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("consult_committee_chr", os.path.join(_ROOT, "__init__.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

_ENV = (
    "HERMES_SESSION_PROFILE", "HERMES_PROFILE_NAME", "HERMES_PROFILE", "HERMES_HOME",
    "CONSULT_COMMITTEE_LOG", "CONSULT_COMMITTEE_ADVISOR_SYSTEM", "CC_TEST_VAR",
)
_MODS = ("hermes_cli", "hermes_cli.config", "hermes_cli.profiles", "hermes_constants")
_LOG = "consult-committee.log"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    for name in _MODS:
        monkeypatch.setitem(sys.modules, name, None)  # import fails unless a test installs a fake


def _install(monkeypatch, name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    if "." in name:
        parent_name, child = name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is None:
            parent = types.ModuleType(parent_name)
            monkeypatch.setitem(sys.modules, parent_name, parent)
        monkeypatch.setattr(parent, child, mod, raising=False)
    return mod


def _config(monkeypatch, cfg=None, raises=False):
    def load_config():
        if raises:
            raise RuntimeError("config broken")
        return cfg
    _install(monkeypatch, "hermes_cli.config", load_config=load_config)


def _home(monkeypatch, value=None, raises=False):
    def get_hermes_home():
        if raises:
            raise RuntimeError("no home")
        return value
    _install(monkeypatch, "hermes_constants", get_hermes_home=get_hermes_home)


def _boom():
    raise RuntimeError("boom")


class _Strish:
    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text


DEFAULT_HOME = os.path.expanduser("~/.hermes")


# ---------------------------------------------------------------------------
# _current_profile_name
# ---------------------------------------------------------------------------
class TestProfileName:
    def test_module_value_stripped(self, monkeypatch):
        _install(monkeypatch, "hermes_cli.profiles", current_profile_name=lambda: " seven ")
        assert cc._current_profile_name() == "seven"

    def test_empty_first_attr_falls_to_second(self, monkeypatch):
        _install(monkeypatch, "hermes_cli.profiles",
                 current_profile_name=lambda: "", get_active_profile_name=lambda: "kirk")
        assert cc._current_profile_name() == "kirk"

    def test_raising_first_attr_falls_to_second(self, monkeypatch):
        _install(monkeypatch, "hermes_cli.profiles",
                 current_profile_name=_boom, get_active_profile_name=lambda: "kirk")
        assert cc._current_profile_name() == "kirk"

    def test_non_callable_attr_skipped(self, monkeypatch):
        _install(monkeypatch, "hermes_cli.profiles",
                 current_profile_name="not callable", get_active_profile_name=lambda: "kirk")
        assert cc._current_profile_name() == "kirk"

    def test_non_str_value_is_str_stripped(self, monkeypatch):
        _install(monkeypatch, "hermes_cli.profiles", current_profile_name=lambda: _Strish(" geordi "))
        assert cc._current_profile_name() == "geordi"

    def test_non_str_blank_value_falls_through(self, monkeypatch):
        _install(monkeypatch, "hermes_cli.profiles",
                 current_profile_name=lambda: _Strish("  "), get_active_profile_name=lambda: "kirk")
        assert cc._current_profile_name() == "kirk"

    def test_none_values_fall_to_env(self, monkeypatch):
        _install(monkeypatch, "hermes_cli.profiles",
                 current_profile_name=lambda: None, get_active_profile_name=lambda: None)
        monkeypatch.setenv("HERMES_PROFILE", "envp")
        assert cc._current_profile_name() == "envp"

    def test_module_wins_over_env(self, monkeypatch):
        _install(monkeypatch, "hermes_cli.profiles", current_profile_name=lambda: "mod")
        monkeypatch.setenv("HERMES_SESSION_PROFILE", "env")
        assert cc._current_profile_name() == "mod"

    def test_env_precedence_and_blank_skipped(self, monkeypatch):
        monkeypatch.setenv("HERMES_SESSION_PROFILE", "  ")
        monkeypatch.setenv("HERMES_PROFILE_NAME", " b ")
        monkeypatch.setenv("HERMES_PROFILE", "c")
        assert cc._current_profile_name() == "b"
        monkeypatch.setenv("HERMES_SESSION_PROFILE", "a")
        assert cc._current_profile_name() == "a"

    def test_nothing_known(self):
        assert cc._current_profile_name() is None


# ---------------------------------------------------------------------------
# _current_model_name
# ---------------------------------------------------------------------------
class TestModelName:
    def test_no_config_module(self):
        assert cc._current_model_name() is None

    def test_load_config_raises(self, monkeypatch):
        _config(monkeypatch, raises=True)
        assert cc._current_model_name() is None

    @pytest.mark.parametrize("cfg", [None, [], "x", {}])
    def test_non_dict_or_missing(self, monkeypatch, cfg):
        _config(monkeypatch, cfg)
        assert cc._current_model_name() is None

    @pytest.mark.parametrize("model, expected", [
        (" m ", "m"),
        ("   ", None),
        ({"provider": "p", "default": "d"}, "p:d"),
        ({"provider": " p ", "default": " d ", "name": "n"}, "p:d"),
        ({"default": "d"}, "d"),
        ({"provider": "p"}, None),
        ({"name": " n "}, "n"),
        ({"provider": "p", "name": "n"}, "n"),
        ({"default": "d", "name": "n"}, "d"),
        ({"default": 5}, "5"),
        ({"provider": 7, "default": 8}, "7:8"),
        ({"provider": None, "default": "d"}, "d"),
        ({"provider": "", "default": "", "name": ""}, None),
        ({}, None),
        (3, None),
        (None, None),
    ])
    def test_model_shapes(self, monkeypatch, model, expected):
        _config(monkeypatch, {"model": model})
        assert cc._current_model_name() == expected


# ---------------------------------------------------------------------------
# _default_log_path
# ---------------------------------------------------------------------------
class TestDefaultLogPath:
    def test_hermes_home(self, monkeypatch):
        _home(monkeypatch, "/h/p")
        assert cc._default_log_path() == os.path.join("/h/p", "logs", _LOG)

    def test_hermes_home_path_object_and_whitespace(self, monkeypatch):
        _home(monkeypatch, Path("/h/q"))
        assert cc._default_log_path() == os.path.join("/h/q", "logs", _LOG)

    @pytest.mark.parametrize("value, raises", [("  ", False), ("", False), (None, False), (None, True)])
    def test_unusable_home_falls_to_env(self, monkeypatch, value, raises):
        _home(monkeypatch, value, raises=raises)
        monkeypatch.setenv("HERMES_HOME", " /e ")
        assert cc._default_log_path() == os.path.join("/e", "logs", _LOG)

    def test_no_module_no_env(self):
        assert cc._default_log_path() == os.path.join(DEFAULT_HOME, "logs", _LOG)

    def test_blank_env(self, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", "   ")
        assert cc._default_log_path() == os.path.join(DEFAULT_HOME, "logs", _LOG)


# ---------------------------------------------------------------------------
# _resolve_log_path_string
# ---------------------------------------------------------------------------
class TestResolveLogPathString:
    @pytest.mark.parametrize("raw", ["", None, "   "])
    def test_empty(self, raw):
        assert cc._resolve_log_path_string(raw) == ""

    def test_absolute_passthrough(self):
        assert cc._resolve_log_path_string(" /abs/x.log ") == "/abs/x.log"

    def test_tilde_expanded(self):
        assert cc._resolve_log_path_string("~/x.log") == os.path.expanduser("~/x.log")

    def test_env_var_expanded(self, monkeypatch):
        monkeypatch.setenv("CC_TEST_VAR", "/v")
        assert cc._resolve_log_path_string("$CC_TEST_VAR/x.log") == "/v/x.log"

    def test_relative_joins_hermes_home(self, monkeypatch):
        _home(monkeypatch, "/h")
        assert cc._resolve_log_path_string("rel/x.log") == "/h/rel/x.log"

    def test_relative_blank_home_falls_to_env(self, monkeypatch):
        _home(monkeypatch, "")
        monkeypatch.setenv("HERMES_HOME", "/e")
        assert cc._resolve_log_path_string("rel/x.log") == "/e/rel/x.log"

    def test_relative_home_raises_falls_to_env(self, monkeypatch):
        _home(monkeypatch, raises=True)
        monkeypatch.setenv("HERMES_HOME", "/e")
        assert cc._resolve_log_path_string("rel/x.log") == "/e/rel/x.log"

    def test_relative_no_module_no_env(self):
        assert cc._resolve_log_path_string("rel/x.log") == os.path.join(DEFAULT_HOME, "rel/x.log")

    def test_path_object(self, monkeypatch):
        _home(monkeypatch, "/h")
        assert cc._resolve_log_path_string(Path("rel/x.log")) == "/h/rel/x.log"

    def test_non_str_value(self, monkeypatch):
        _home(monkeypatch, "/h")
        assert cc._resolve_log_path_string(5) == "/h/5"

    @pytest.mark.parametrize("raw", [0, False])
    def test_falsy_non_str_is_empty(self, raw):
        assert cc._resolve_log_path_string(raw) == ""


# ---------------------------------------------------------------------------
# _plugin_log_settings / _resolve_log_path
# ---------------------------------------------------------------------------
def _settings(monkeypatch, settings):
    _config(monkeypatch, {"plugins": {"entries": {"consult-committee": {"settings": settings}}}})


class TestPluginSettings:
    @pytest.mark.parametrize("cfg", [None, {}, {"plugins": None}, {"plugins": {"entries": None}},
                                     {"plugins": {"entries": {"consult-committee": None}}},
                                     {"plugins": {"entries": {"consult-committee": {"settings": ["x"]}}}}])
    def test_missing_or_malformed(self, monkeypatch, cfg):
        _config(monkeypatch, cfg)
        assert cc._plugin_log_settings() == {}

    def test_raises(self, monkeypatch):
        _config(monkeypatch, raises=True)
        assert cc._plugin_log_settings() == {}

    def test_no_module(self):
        assert cc._plugin_log_settings() == {}

    def test_returns_settings(self, monkeypatch):
        _settings(monkeypatch, {"logging": True})
        assert cc._plugin_log_settings() == {"logging": True}


class TestResolveLogPath:
    @pytest.mark.parametrize("value", ["off", "OFF", " no ", "0", "false", "False", "", "   "])
    def test_env_disables(self, monkeypatch, value):
        _settings(monkeypatch, {"logging": True})
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", value)
        assert cc._resolve_log_path() is None

    def test_env_absolute(self, monkeypatch):
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", " /abs.log ")
        assert cc._resolve_log_path() == "/abs.log"

    def test_env_relative(self, monkeypatch):
        _home(monkeypatch, "/h")
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", "r.log")
        assert cc._resolve_log_path() == "/h/r.log"

    @pytest.mark.parametrize("settings", [{}, {"logging": "true"}, {"logging": 1}, {"logging": False}])
    def test_gui_not_strictly_true_is_off(self, monkeypatch, settings):
        _settings(monkeypatch, settings)
        assert cc._resolve_log_path() is None

    @pytest.mark.parametrize("log_path", [None, "", "   "])
    def test_gui_on_default_path(self, monkeypatch, log_path):
        _home(monkeypatch, "/h")
        settings = {"logging": True}
        if log_path is not None:
            settings["log_path"] = log_path
        _settings(monkeypatch, settings)
        assert cc._resolve_log_path() == os.path.join("/h", "logs", _LOG)

    def test_gui_on_custom_relative(self, monkeypatch):
        _home(monkeypatch, "/h")
        _settings(monkeypatch, {"logging": True, "log_path": "custom/x.log"})
        assert cc._resolve_log_path() == "/h/custom/x.log"

    def test_gui_on_custom_absolute(self, monkeypatch):
        _settings(monkeypatch, {"logging": True, "log_path": "/abs/y.log"})
        assert cc._resolve_log_path() == "/abs/y.log"

    def test_no_config_is_off(self):
        assert cc._resolve_log_path() is None


# ---------------------------------------------------------------------------
# _advisor_system_text / _advisor_messages
# ---------------------------------------------------------------------------
class TestAdvisorSystemText:
    @pytest.fixture
    def plugin_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cc, "__file__", str(tmp_path / "__init__.py"))
        return tmp_path

    def test_env_wins(self, monkeypatch, plugin_dir):
        monkeypatch.setenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", " env text ")
        assert cc._advisor_system_text("override") == "env text"

    @pytest.mark.parametrize("word", ["off", " OFF ", "0", "false", "No"])
    def test_env_off_words(self, monkeypatch, plugin_dir, word):
        monkeypatch.setenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", word)
        assert cc._advisor_system_text("override") == ""

    def test_blank_env_falls_through(self, monkeypatch, plugin_dir):
        monkeypatch.setenv("CONSULT_COMMITTEE_ADVISOR_SYSTEM", "   ")
        assert cc._advisor_system_text(" override ") == "override"

    def test_override_off_word(self, plugin_dir):
        assert cc._advisor_system_text(" Off ") == ""

    def test_override_non_str(self, plugin_dir):
        assert cc._advisor_system_text(123) == "123"

    def test_blank_override_falls_to_file(self, plugin_dir):
        (plugin_dir / "advisor_system.md").write_text("  file text \n", encoding="utf-8")
        assert cc._advisor_system_text("   ") == "file text"

    def test_gui_setting_used_when_no_override(self, monkeypatch, plugin_dir):
        _settings(monkeypatch, {"advisor_system": " gui text "})
        assert cc._advisor_system_text() == "gui text"

    def test_gui_off(self, monkeypatch, plugin_dir):
        _settings(monkeypatch, {"advisor_system": "no"})
        assert cc._advisor_system_text() == ""

    def test_empty_file_falls_to_default(self, plugin_dir):
        (plugin_dir / "advisor_system.md").write_text("  \n", encoding="utf-8")
        assert cc._advisor_system_text() == cc._ADVISOR_SYSTEM_DEFAULT

    def test_missing_file_falls_to_default(self, plugin_dir):
        assert cc._advisor_system_text() == cc._ADVISOR_SYSTEM_DEFAULT


class TestAdvisorMessages:
    def test_system_first(self):
        assert cc._advisor_messages("q", " sys ") == [
            {"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]

    @pytest.mark.parametrize("system", ["", "   ", None, 5])
    def test_user_only(self, system):
        assert cc._advisor_messages("q", system) == [{"role": "user", "content": "q"}]


# ---------------------------------------------------------------------------
# _maybe_log_call_text
# ---------------------------------------------------------------------------
class TestLogWriter:
    def _write(self, monkeypatch, tmp_path, **entry):
        log = tmp_path / "sub" / "c.log"
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(log))
        base = {"ts": "T", "seq": 3, "advisors": []}
        base.update(entry)
        cc._maybe_log_call_text(base)
        return log.read_text(encoding="utf-8")

    def test_full_block(self, monkeypatch, tmp_path):
        text = self._write(
            monkeypatch, tmp_path,
            preset="'cheap' (committee preset)", conversation="conversation c1", version="1.2.3",
            profile=" seven ", model=" p:m ", note="n1", budget="2 of 5 used in this conversation",
            timing="1.0 s elapsed", outcome="delivered (1 of 2 advisors answered; budget charged)",
            question="line1\r\nline2\nline3\rline4", system="s1\ns2",
            advisors=[{"model": "a", "answer": "ok", "chars": 2},
                      {"model": "b", "answer": "[failed: x]", "chars": 11}],
        )
        assert text == (
            "=== T | seq 3 | conversation c1 | profile seven | model p:m ===\n"
            "plugin:   consult-committee 1.2.3\n"
            "preset:   'cheap' (committee preset)\n"
            "note:     n1\n"
            "budget:   2 of 5 used in this conversation\n"
            "timing:   1.0 s elapsed\n"
            "outcome:  delivered (1 of 2 advisors answered; budget charged)\n"
            "\n"
            "Q (23 chars): line1 line2 line3 line4\n"
            "S: s1 s2\n"
            "\n"
            "--- advisor 1 of 2: a ---\nstatus:   answered (2 chars)\nok\n"
            "\n"
            "--- advisor 2 of 2: b ---\nstatus:   failed: x\n\n"
        )

    def test_minimal_block_defaults(self, monkeypatch, tmp_path):
        text = self._write(monkeypatch, tmp_path, seq=None, profile=None, model="  ", question=None, system=None)
        assert text == "=== T ===\nplugin:   consult-committee\n\n"

    def test_non_str_identity(self, monkeypatch, tmp_path):
        text = self._write(monkeypatch, tmp_path, profile=5, model=_Strish(" m "), question="q")
        assert text.startswith("=== T | seq 3 | profile 5 | model m ===\n")

    def test_appends(self, monkeypatch, tmp_path):
        self._write(monkeypatch, tmp_path, question="one")
        text = self._write(monkeypatch, tmp_path, question="two")
        assert text.count("=== T | seq 3") == 2

    def test_disabled_writes_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", "off")
        cc._maybe_log_call_text({"question": "q"})
        assert list(tmp_path.iterdir()) == []

    def test_unwritable_path_never_raises(self, monkeypatch, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        monkeypatch.setenv("CONSULT_COMMITTEE_LOG", str(blocker / "c.log"))
        cc._maybe_log_call_text({"question": "q", "advisors": []})
