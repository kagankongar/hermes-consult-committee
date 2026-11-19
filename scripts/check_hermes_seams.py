"""Check that the Hermes internals consult-committee imports lazily still exist.

The plugin's helpers never raise: a Hermes import that fails falls back
quietly (default deadline, no fallback detection, config model in the log, …).
So a rename upstream degrades the plugin without an error. Run this before
each release, and after a Hermes update, with Hermes' own interpreter (the
first element of ``hermes --print-runtime-command``):

    <hermes-python> scripts/check_hermes_seams.py [--hermes-dir ~/.hermes/hermes-agent]

Read-only: it imports modules and inspects signatures; it calls only
``get_transport("chat_completions")``. Exit 0 when every seam is present,
1 otherwise. ``tests/test_seams.py`` keeps ``SEAMS`` in step with the plugin.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import os
import sys

# (module, attribute or tuple of alternatives, keyword parameters the plugin passes)
SEAMS: list[tuple[str, str | tuple[str, ...], tuple[str, ...]]] = [
    ("hermes_constants", "get_hermes_home", ()),
    ("hermes_constants", "parse_reasoning_effort", ()),
    ("hermes_constants", "set_hermes_home_override", ()),
    ("hermes_constants", "reset_hermes_home_override", ()),
    ("hermes_cli.profiles", ("current_profile_name", "get_active_profile_name"), ()),
    ("hermes_cli.profiles", "get_profile_dir", ()),
    ("hermes_cli.config", "load_config", ()),
    ("hermes_cli.config", "read_raw_config", ()),
    ("hermes_cli.moa_config", "normalize_moa_config", ()),
    ("hermes_cli.moa_config", "resolve_moa_preset", ()),
    ("hermes_cli.runtime_provider", "resolve_runtime_provider", ("requested", "target_model")),
    ("hermes_cli.model_normalize", "normalize_model_for_provider", ()),
    ("agent.auxiliary_client", "call_llm", (
        "task", "messages", "temperature", "timeout", "reasoning_config", "provider", "model",
        "base_url", "api_key", "api_mode", "extra_body", "route_info")),
    ("agent.auxiliary_client", "_read_main_model", ()),
    ("agent.auxiliary_client", "_read_main_provider", ()),
    ("agent.transports", "get_transport", ()),
    ("agent.tool_executor", "_resolve_sequential_tool_timeout", ()),
    ("tools.daemon_pool", "DaemonThreadPoolExecutor", ()),
    ("tools.interrupt", "is_interrupted", ()),
    ("tools.registry", "tool_result", ()),
    ("tools.registry", "tool_error", ()),
]


def _check_seam(module: str, attr, params: tuple[str, ...]) -> str:
    """"" when present with *params*, else a one-line problem."""
    try:
        mod = importlib.import_module(module)
    except Exception as exc:
        return f"{module}: import failed ({exc!r})"
    names = attr if isinstance(attr, tuple) else (attr,)
    found = next((getattr(mod, n) for n in names if hasattr(mod, n)), None)
    if found is None:
        return f"{module}.{'|'.join(names)}: missing"
    if params:
        try:
            have = inspect.signature(found).parameters
        except (TypeError, ValueError) as exc:
            return f"{module}.{names[0]}: signature unreadable ({exc!r})"
        lost = [p for p in params if p not in have]
        if lost:
            return f"{module}.{names[0]}: parameters missing: {', '.join(lost)}"
    return ""


def _extra_checks() -> list[str]:
    """Behaviour the plugin relies on beyond names and parameters."""
    problems = []
    try:  # the handler catches (KeyError, ValueError) for an unknown preset
        from agent.errors import MoAPresetNotFoundError
        if not issubclass(MoAPresetNotFoundError, (KeyError, ValueError)):
            problems.append("agent.errors.MoAPresetNotFoundError: no longer a KeyError/ValueError")
    except Exception as exc:
        problems.append(f"agent.errors.MoAPresetNotFoundError: {exc!r}")
    try:
        from agent.transports import get_transport
        if not callable(getattr(get_transport("chat_completions"), "normalize_response", None)):
            problems.append("agent.transports: chat_completions transport has no normalize_response")
    except Exception as exc:
        problems.append(f"agent.transports.get_transport('chat_completions'): {exc!r}")
    return problems


def run_checks() -> tuple[int, int, list[str]]:
    """(passed, total, problems) against the Hermes importable here.
    ``/committee doctor`` calls this inside the running Hermes."""
    problems = [p for p in (_check_seam(*seam) for seam in SEAMS) if p] + _extra_checks()
    total = len(SEAMS) + _EXTRA_CHECKS
    return total - len(problems), total, problems


_EXTRA_CHECKS = 2  # number of checks in _extra_checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hermes-dir", default=os.path.expanduser("~/.hermes/hermes-agent"),
                        help="Hermes install directory (default: ~/.hermes/hermes-agent)")
    args = parser.parse_args(argv)
    sys.path.insert(0, args.hermes_dir)
    sys._hermes_pin_default_home = True  # as hermes --print-runtime-command does
    try:
        import hermes_bootstrap  # noqa: F401
    except Exception:
        pass
    passed, total, problems = run_checks()
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"{passed} of {total} checks passed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
