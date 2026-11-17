"""Tests for the ``escalation_tier`` setting: the plugin injects a tier block into new sessions.

Fakes only: Hermes modules are injected via sys.modules, block text is always parsed from
``escalation-prompts.md`` (never hardcoded).
"""
from __future__ import annotations

import contextvars
import importlib.util
import logging
import os
import re
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("consult_committee_tier", os.path.join(_ROOT, "__init__.py"))
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

LOGGER = cc._logger.name


def _blocks() -> dict:
    with open(os.path.join(_ROOT, "escalation-prompts.md"), encoding="utf-8") as fh:
        found = re.findall(r"```\n(## Committee Escalation[^\n]*\n.*?)\n```", fh.read(), re.S)
    return {b.splitlines()[0].split("(")[1].rstrip(")"): b for b in found}


BLOCKS = _blocks()


class Env:
    """Fake Hermes: per-home settings, home override ContextVar, profile dirs."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.ambient = tmp_path / "ambient"
        self.ambient.mkdir()
        self.var = contextvars.ContextVar("home", default=str(self.ambient))
        self.settings_by_home = {}
        self.profiles = {}
        self.load_raises = False
        self.raw_settings = None  # when set, returned verbatim as the settings value
        self.install()

    def home(self):
        return self.var.get()

    def set_settings(self, settings, home=None):
        self.settings_by_home[str(home or self.ambient)] = settings

    def install(self):
        env = self
        consts = types.ModuleType("hermes_constants")
        consts.get_hermes_home = lambda: env.var.get()
        consts.set_hermes_home_override = lambda p: env.var.set(p)
        consts.reset_hermes_home_override = lambda t: env.var.reset(t)
        cfg = types.ModuleType("hermes_cli.config")

        def load_config():
            if env.load_raises:
                raise RuntimeError("boom")
            s = env.raw_settings if env.raw_settings is not None else env.settings_by_home.get(env.var.get(), {})
            return {"plugins": {"entries": {"consult-committee": {"settings": s}}}}

        cfg.load_config = load_config
        prof = types.ModuleType("hermes_cli.profiles")

        def get_profile_dir(name):
            if name not in env.profiles:
                raise ValueError(name)
            return env.profiles[name]

        prof.get_profile_dir = get_profile_dir
        pkg = types.ModuleType("hermes_cli")
        for k, m in (("hermes_constants", consts), ("hermes_cli", pkg), ("hermes_cli.config", cfg),
                     ("hermes_cli.profiles", prof)):
            self.monkeypatch.setitem(sys.modules, k, m)


@pytest.fixture(autouse=True)
def _reset():
    cc._escalation_notes.clear()
    yield
    cc._escalation_notes.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def _records(caplog):
    return [r for r in caplog.records if r.name == LOGGER]


# --- tiers ---------------------------------------------------------------------------------

def test_blocks_parsed_and_within_limit():
    assert set(BLOCKS) == {"high", "medium", "low"}
    assert all(len(b) <= cc._ESCALATION_SECTION_MAX_CHARS for b in BLOCKS.values())


@pytest.mark.parametrize("value,tier", [("high", "high"), ("medium", "medium"), ("low", "low"),
                                        ("Medium", "medium"), (" LOW ", "low")])
def test_tier_injects_matching_block(env, value, tier):
    env.set_settings({"escalation_tier": value})
    assert cc._escalation_section({}) == BLOCKS[tier]


@pytest.mark.parametrize("value", ["off", "", None, "bogus", False, True, 0, 1, 2.5, ["high"]])
def test_off_values(env, value, caplog):
    caplog.set_level(logging.DEBUG)
    env.set_settings({"escalation_tier": value})
    assert cc._escalation_section({}) == ""
    assert _records(caplog) == []


def test_missing_key_and_bad_settings(env):
    env.set_settings({})
    assert cc._escalation_section({}) == ""
    env.raw_settings = "high"  # not a dict
    assert cc._escalation_section({}) == ""
    env.raw_settings = None
    env.set_settings({"escalation_tier": "high"})
    env.load_raises = True
    assert cc._escalation_section({}) == ""
    assert cc._escalation_tier_setting() == "off"


def test_tier_does_not_change_budget(env):
    env.set_settings({"escalation_tier": "high"})
    assert cc._max_consultations() == cc._DEFAULT_MAX_CONSULTATIONS == 5


# --- SOUL.md ---------------------------------------------------------------------------------

@pytest.mark.parametrize("heading", ["## Committee Escalation", "## Committee Escalation (medium)",
                                     "## Committee Escalation - High"])
def test_soul_heading_skips(env, caplog, heading):
    caplog.set_level(logging.INFO)
    env.set_settings({"escalation_tier": "high"})
    (env.ambient / "SOUL.md").write_text(f"# Soul\n\n{heading}\nrules\n", encoding="utf-8")
    assert cc._escalation_section({}) == ""
    recs = _records(caplog)
    assert len(recs) == 1
    assert str(env.ambient / "SOUL.md") in recs[0].getMessage()
    assert cc._escalation_section({}) == ""
    assert len(_records(caplog)) == 1


@pytest.mark.parametrize("soul", ["Mention Committee Escalation in prose.\n", "### Committee Escalation\n",
                                  "text ## Committee Escalation\n", None])
def test_soul_not_blocking(env, soul):
    env.set_settings({"escalation_tier": "low"})
    if soul is not None:
        (env.ambient / "SOUL.md").write_text(soul, encoding="utf-8")
    assert cc._escalation_section({}) == BLOCKS["low"]


# --- prompts file -----------------------------------------------------------------------------

def test_prompts_file_missing(env, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    env.set_settings({"escalation_tier": "high"})
    monkeypatch.setattr(cc, "_ESCALATION_PROMPTS_FILE", "no-such-file.md")
    assert cc._escalation_section({}) == ""
    assert [r.levelno for r in _records(caplog)] == [logging.WARNING]


def test_tier_heading_absent_in_stub(env, tmp_path, monkeypatch):
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    (stub_dir / "stub.md").write_text("```\n## Committee Escalation (high)\nx\n```\n", encoding="utf-8")
    monkeypatch.setattr(cc, "_ESCALATION_PROMPTS_FILE", str(stub_dir / "stub.md"))
    env.set_settings({"escalation_tier": "low"})
    assert cc._escalation_section({}) == ""
    env.set_settings({"escalation_tier": "high"})
    assert cc._escalation_section({}) == "## Committee Escalation (high)\nx"


def test_inject_logged_once_with_home(env, caplog):
    caplog.set_level(logging.INFO)
    env.set_settings({"escalation_tier": "medium"})
    cc._escalation_section({})
    cc._escalation_section({})
    recs = _records(caplog)
    assert len(recs) == 1 and str(env.ambient) in recs[0].getMessage()


# --- profile binding ---------------------------------------------------------------------------

def test_bind_profile_home(env, tmp_path):
    prof = tmp_path / "work"
    prof.mkdir()
    env.profiles["work"] = prof
    env.set_settings({"escalation_tier": "off"})
    env.set_settings({"escalation_tier": "high"}, home=prof)
    (prof / "SOUL.md").write_text("no heading\n", encoding="utf-8")
    seen = []
    orig = cc._tier_block
    cc._tier_block = lambda t: (seen.append(cc._hermes_home()), orig(t))[1]
    try:
        assert cc._escalation_section({"profile_name": "work"}) == BLOCKS["high"]
    finally:
        cc._tier_block = orig
    assert seen == [str(prof)]
    assert env.home() == str(env.ambient)
    assert cc._escalation_section({"profile_name": "default"}) == ""


def test_bind_profile_soul_from_profile(env, tmp_path):
    prof = tmp_path / "work"
    prof.mkdir()
    env.profiles["work"] = prof
    env.set_settings({"escalation_tier": "high"}, home=prof)
    (prof / "SOUL.md").write_text("## Committee Escalation\n", encoding="utf-8")
    assert cc._escalation_section({"profile_name": "work"}) == ""


def test_bind_no_binding_cases(env, tmp_path):
    prof = tmp_path / "work"
    prof.mkdir()
    env.profiles.update({"work": prof, "gone": tmp_path / "missing", "same": env.ambient})
    calls = []
    orig_set = sys.modules["hermes_constants"].set_hermes_home_override
    sys.modules["hermes_constants"].set_hermes_home_override = lambda p: (calls.append(p), orig_set(p))[1]
    for info in ({"profile_name": "default"}, {"profile_name": "DEFAULT"}, {"profile_name": ""}, {},
                 {"profile_name": "gone"}, {"profile_name": "unknown"}, {"profile_name": "same"}, None, 5):
        undo = cc._bind_profile_home(info)
        assert env.home() == str(env.ambient), info
        undo()
        assert env.home() == str(env.ambient)
    assert calls == []
    undo = cc._bind_profile_home({"profile_name": "work"})
    assert env.home() == str(prof)
    undo()
    undo()  # second undo must not raise
    assert env.home() == str(env.ambient)


def test_bind_module_missing(env, tmp_path, monkeypatch):
    prof = tmp_path / "work"
    prof.mkdir()
    env.profiles["work"] = prof
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", None)
    undo = cc._bind_profile_home({"profile_name": "work"})
    assert env.home() == str(env.ambient)
    undo()


# --- never raises ---------------------------------------------------------------------------------

@pytest.mark.parametrize("info", [None, {}, "text", 5, ["x"]])
def test_section_never_raises_on_bad_session_info(env, info):
    env.set_settings({"escalation_tier": "high"})
    assert cc._escalation_section(info) == BLOCKS["high"]


@pytest.mark.parametrize("name", ["_bind_profile_home", "_escalation_tier_setting", "_hermes_home",
                                  "_soul_has_escalation_block", "_tier_block", "_note_once"])
def test_section_never_raises_when_helper_raises(env, monkeypatch, name):
    env.set_settings({"escalation_tier": "high"})

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cc, name, boom)
    assert cc._escalation_section({"profile_name": "x"}) == ""


# --- register ---------------------------------------------------------------------------------------

class _Ctx:
    def __init__(self, section=None, raises=False):
        self.calls = []
        if section:
            def add(*a, **k):
                self.calls.append(("section", a, k))
                if raises:
                    raise ValueError("duplicate id")
            self.register_system_prompt_section = add

    def register_tool(self, **k):
        self.calls.append(("tool", k))


def test_register_without_section_api():
    ctx = _Ctx()
    cc.register(ctx)
    assert [c[0] for c in ctx.calls] == ["tool"]


def test_register_with_section_api():
    ctx = _Ctx(section=True)
    cc.register(ctx)
    assert [c[0] for c in ctx.calls] == ["tool", "section"]
    _, args, kwargs = ctx.calls[1]
    assert args == ("consult-committee.escalation-policy", cc._escalation_section)
    assert kwargs == {"position": "after_memory", "max_chars": 4000}
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", args[0])
    assert kwargs["max_chars"] <= 4000


def test_register_section_error_is_swallowed(caplog):
    caplog.set_level(logging.WARNING)
    ctx = _Ctx(section=True, raises=True)
    cc.register(ctx)
    assert ctx.calls[0][0] == "tool"
    assert [r.levelno for r in _records(caplog)] == [logging.WARNING]


# --- plugin.yaml ---------------------------------------------------------------------------------------

def test_plugin_yaml_entry():
    with open(os.path.join(_ROOT, "plugin.yaml"), encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^  escalation_tier:\n((?:    .*\n)+)", text, re.M)
    assert m, "escalation_tier entry missing"
    body = m.group(1)
    assert re.search(r"^    type: str$", body, re.M)
    assert re.search(r'^    default: "off"$', body, re.M)
    assert re.search(r'^    choices: \["off", "high", "medium", "low"\]$', body, re.M)


def test_soul_bom_and_invalid_bytes(env):
    env.set_settings({"escalation_tier": "high"})
    soul = env.ambient / "SOUL.md"
    soul.write_bytes(b"\xef\xbb\xbf## Committee Escalation (medium)\nrules\n")
    assert cc._escalation_section({}) == ""
    soul.write_bytes(b"intro \xff\xfe bad\n## Committee Escalation (medium)\nrules\n")
    assert cc._escalation_section({}) == ""


def test_bound_home_restored_when_helper_raises(env, tmp_path, monkeypatch):
    prof = tmp_path / "work"
    prof.mkdir()
    env.profiles["work"] = prof
    env.set_settings({"escalation_tier": "high"}, home=prof)
    seen = []

    def boom(tier):
        seen.append(env.home())
        raise RuntimeError("boom")

    monkeypatch.setattr(cc, "_tier_block", boom)
    assert cc._escalation_section({"profile_name": "work"}) == ""
    assert seen == [str(prof)]
    assert env.home() == str(env.ambient)
