"""scripts/check_hermes_seams.py must list every Hermes import the plugin makes.

No Hermes needed: the plugin source is parsed, not imported.
"""
from __future__ import annotations

import ast
import importlib.util
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "check_hermes_seams", os.path.join(_ROOT, "scripts", "check_hermes_seams.py"))
seams = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seams)

_STDLIB = {"__future__", "concurrent", "contextvars", "datetime", "inspect", "json", "logging",
           "os", "re", "threading", "time"}


def _plugin_imports() -> tuple[set[tuple[str, str]], set[str]]:
    """((module, name) from-imports, plain-imported modules) outside the stdlib."""
    with open(os.path.join(_ROOT, "__init__.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names, modules = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] not in _STDLIB:
            names.update((node.module, alias.name) for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names if a.name.split(".")[0] not in _STDLIB)
    return names, modules


def _listed() -> set[tuple[str, str]]:
    out = set()
    for module, attr, _params in seams.SEAMS:
        for name in (attr if isinstance(attr, tuple) else (attr,)):
            out.add((module, name))
    return out


def test_every_from_import_is_listed():
    names, _ = _plugin_imports()
    missing = names - _listed()
    assert not missing, f"add to SEAMS in scripts/check_hermes_seams.py: {sorted(missing)}"


def test_every_plain_import_module_is_listed():
    _, modules = _plugin_imports()
    listed_modules = {module for module, _attr, _params in seams.SEAMS}
    assert modules <= listed_modules, f"add to SEAMS: {sorted(modules - listed_modules)}"


def test_no_stale_entries():
    names, modules = _plugin_imports()
    stale = {(m, n) for m, n in _listed() if (m, n) not in names and m not in modules}
    assert not stale, f"SEAMS lists names the plugin no longer imports: {sorted(stale)}"


def test_call_llm_parameters_match_the_call():
    """Every keyword the plugin passes to call_llm is checked."""
    params = next(p for m, a, p in seams.SEAMS if (m, a) == ("agent.auxiliary_client", "call_llm"))
    with open(os.path.join(_ROOT, "__init__.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    passed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "call_llm":
            passed.update(k.arg for k in node.keywords if k.arg)
    passed |= {"provider", "model", "base_url", "api_key", "api_mode", "extra_body"}  # via _slot_runtime
    passed |= {"route_info"}  # via `extra`, when accepted
    assert passed <= set(params), f"call_llm keywords not checked: {sorted(passed - set(params))}"
