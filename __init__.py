"""consult-committee — escalate a hard question to the MoA advisor committee.

Registers a `consult_committee` tool. The acting model calls it when a
problem matches the trigger criteria; the handler fans the question out to the
MoA preset's reference models (the same advisors `/moa` uses) in parallel and
returns their labelled answers as the tool result. The acting model then
synthesizes — it plays the aggregator role itself, with the full
conversation context a detached aggregator would lack.

Reuses Hermes' own MoA machinery (agent.moa_loop._run_references_parallel,
hermes_cli.moa_config.resolve_moa_preset). All heavy imports are lazy inside
the handler, mirroring internal call sites, to avoid import-time cycles.
"""
from __future__ import annotations

import datetime
import os
import threading

# Hard escalation cap per gateway process. Literature says small models'
# escalation behavior is unpredictable; the cap bounds cloud-quota burn if the
# model over-calls. Returns a budget-exhausted error past the cap.
_MAX_ESCALATIONS = 5
_TRUNCATE_CHARS = 6000
_escalation_count = 0
_lock = threading.Lock()
_LOG_PATH_ENV = "CONSULT_COMMITTEE_LOG"
_LOG_FILE_NAME = "consult-committee.log"
_QUESTION_LOG_CHARS = 4000
_PLUGIN_ID = "consult-committee"

CONSULT_SCHEMA = {
    "name": "consult_committee",
    "description": (
        "Consult a committee of stronger advisor models (cloud frontier LLMs) on a hard "
        "problem and get their independent answers to synthesize. Use this tool FREQUENTLY "
        "— calling it is cheap and fast; getting a hard answer wrong is expensive. "
        "MANDATORY triggers — call this tool FIRST, before attempting your own answer, when: "
        "(1) the question is expert-level science, math, medicine, law, or engineering "
        "(especially multiple-choice questions with options like A/B/C/D); "
        "(2) the question involves multi-step technical reasoning (reaction mechanisms, "
        "derivations, proofs, protocol analysis); "
        "(3) you are weighing conflicting evidence or plausible-looking alternatives; "
        "(4) the decision is architectural or hard to undo; "
        "(5) your first attempt at something failed or you caught yourself unsure mid-answer. "
        "NEVER search the web for benchmark answer keys or leaked solutions — consult this "
        "committee instead. If you have failed twice at the same step, you MUST call this "
        "tool before trying a third time. Do NOT use it for simple lookups, casual "
        "conversation, or tasks you are completing without difficulty."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": (
                    "The full, self-contained question for the committee. Include all "
                    "relevant context, constraints, and what you have already tried — "
                    "the advisors cannot see the conversation."
                ),
            },
            "preset": {
                "type": "string",
                "description": (
                    "MoA preset name (optional; omit to use the configured "
                    "moa.default_preset, e.g. 'cheap')."
                ),
            },
        },
        "required": ["question"],
    },
}


def _default_log_path() -> str:
    home = os.environ.get("HERMES_HOME", "").strip() or os.path.expanduser("~/.hermes")
    return os.path.join(home, "logs", _LOG_FILE_NAME)


def _plugin_log_settings() -> dict:
    """Read GUI settings (plugins.entries.consult-committee.settings); {} on any failure."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        entries = (cfg.get("plugins") or {}).get("entries") or {}
        settings = (entries.get(_PLUGIN_ID) or {}).get("settings") or {}
        return settings if isinstance(settings, dict) else {}
    except Exception:
        return {}


def _resolve_log_path() -> str | None:
    """Precedence: CONSULT_COMMITTEE_LOG env (incl. off) > GUI settings; default OFF."""
    raw = os.environ.get(_LOG_PATH_ENV)
    if raw is not None:
        text = raw.strip()
        if not text or text.lower() in ("off", "0", "false", "no"):
            return None
        return text
    try:
        settings = _plugin_log_settings()
    except Exception:
        settings = {}
    if settings.get("logging") is not True:
        return None
    custom = str(settings.get("log_path") or "").strip()
    return custom or _default_log_path()


def _maybe_log_call_text(entry: dict) -> None:
    """Append one plain-text block unless disabled; never raise."""
    path = _resolve_log_path()
    if not path:
        return
    try:
        preset = entry.get("preset_requested") or "default"
        question = str(entry.get("question") or "")
        question = question.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        lines = [
            f"=== {entry.get('ts')} | seq {entry.get('seq')} | preset {preset} ===",
            f"Q: {question}",
        ]
        for adv in entry.get("advisors") or []:
            if adv.get("failed"):
                lines.append(f"--- advisor: {adv.get('model')} [FAILED] ---")
            else:
                lines.append(f"--- advisor: {adv.get('model')} ---")
            lines.append(str(adv.get("answer") or ""))
        lines.append(f"answered: {entry.get('answered')} failed: {entry.get('failed')}")
        text = "\n".join(lines) + "\n\n"
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
    except Exception:
        pass


def _handle_consult_committee(args: dict, **kwargs) -> str:
    from tools.registry import tool_result, tool_error

    global _escalation_count
    question = str(args.get("question") or "").strip()
    if not question:
        return tool_error("question is required")

    with _lock:
        if _escalation_count >= _MAX_ESCALATIONS:
            return tool_error(
                f"committee budget exhausted ({_MAX_ESCALATIONS} consultations this "
                "session). Answer with your own best judgment and say the budget ran out."
            )
        _escalation_count += 1

    try:
        from hermes_cli.config import load_config
        from hermes_cli.moa_config import resolve_moa_preset
        from agent.moa_loop import _run_references_parallel

        # No explicit preset → defer to the user's configured moa.default_preset
        # (resolve_moa_preset(None) honors it; only falls back to 'default' when
        # the config names no default at all).
        preset_name = str(args.get("preset") or "").strip() or None
        try:
            preset = resolve_moa_preset(load_config().get("moa") or {}, preset_name)
        except (KeyError, ValueError):
            # Real Hermes raises MoAPresetNotFoundError (a ValueError); older
            # fakes/tests raised KeyError — tolerate both.
            return tool_error(f"unknown MoA preset: {args.get('preset')!r}")

        reference_models = preset.get("reference_models") or []
        if not reference_models:
            return tool_error(f"MoA preset {preset_name!r} has no reference models configured")

        temperature = float(preset.get("reference_temperature", 0.9) or 0.9)
        outputs = _run_references_parallel(
            reference_models,
            [{"role": "user", "content": question}],
            temperature=temperature,
            max_tokens=None,
        )

        advisors = []
        failures = 0
        for item in outputs:
            # fork: newer Hermes yields (label, text, accounting) 3-tuples,
            # older yields (label, text). Index the first two so both parse.
            label, text = item[0], item[1]
            text = (text or "").strip()
            failed = not text or text.startswith("[error") or "failed:" in text[:80].lower()
            if failed:
                failures += 1
            if len(text) > _TRUNCATE_CHARS:
                text = text[:_TRUNCATE_CHARS] + "\n[... truncated]"
            advisors.append({"model": label, "answer": text, "failed": failed})

        _maybe_log_call_text({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "seq": _escalation_count,
            "preset_requested": args.get("preset"),
            "question": question[:_QUESTION_LOG_CHARS],
            "advisors": advisors,
            "answered": len(advisors) - failures,
            "failed": failures,
        })
        answered = len(advisors) - failures
        if answered == 0:
            return tool_error("all committee advisors failed — answer with your own best judgment")

        return tool_result({
            "advisors": advisors,
            "answered": answered,
            "failed": failures,
            "note": (
                "You are the aggregator. Weigh these independent advisor answers against "
                "your own analysis, resolve disagreements explicitly, and produce the "
                "final answer yourself."
            ),
        })
    except Exception as exc:  # never raise from a tool handler
        return tool_error(f"committee consultation failed: {exc!r}")


def register(ctx) -> None:
    ctx.register_tool(
        name="consult_committee",
        toolset="committee",
        schema=CONSULT_SCHEMA,
        handler=_handle_consult_committee,
        description="Consult the MoA advisor committee on a hard problem.",
        emoji="🏛️",
    )
