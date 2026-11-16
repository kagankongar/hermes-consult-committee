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

# Consultation budget per conversation. Literature says small models'
# escalation behavior is unpredictable; the cap bounds cloud-quota burn if the
# model over-calls. Returns a budget-exhausted error past the cap. Only calls
# that deliver at least one advisor answer consume budget (reserve, then refund
# on any failure). Cap comes from settings.max_consultations (default below).
_DEFAULT_MAX_CONSULTATIONS = 5
_TRUNCATE_CHARS = 6000
# budget key -> consultations delivered (or in flight). Key is Hermes'
# conversation_id (compression-stable, shared with delegated children), else
# session_id, else _PROCESS_KEY when the dispatcher passes no id.
_consultations: dict[str, int] = {}
_PROCESS_KEY = "\x00process"
# Process-wide monotonic call number for the log header (unique per process).
_log_seq = 0
_lock = threading.Lock()
_LOG_PATH_ENV = "CONSULT_COMMITTEE_LOG"
_LOG_FILE_NAME = "consult-committee.log"
_QUESTION_LOG_CHARS = 4000
_PLUGIN_ID = "consult-committee"
_ADVISOR_SYSTEM_ENV = "CONSULT_COMMITTEE_ADVISOR_SYSTEM"
_ADVISOR_SYSTEM_FILE = "advisor_system.md"
_ADVISOR_SYSTEM_DEFAULT = "You are a reference advisor. Only answer the question directly and concisely. No tool calls. No additional response."
_OFF_WORDS = ("off", "0", "false", "no")  # env/GUI values that disable a feature
_DEFAULT_REFERENCE_TEMPERATURE = 0.9
# Hermes' reference-output sentinels (agent.moa_loop: "[failed: …]" for an
# exception, "[skipped: …]" for recursion guard / user interrupt), matched the
# way Hermes itself does. "[error" kept for older Hermes / upstream fakes.
_FAILURE_PREFIXES = ("[failed:", "[skipped:", "[error")

CONSULT_SCHEMA = {
    "name": "consult_committee",
    "description": (
        # Policy-neutral on purpose (handover A4/C1): this text is in context for
        # every profile, so WHEN to call lives in the caller's system prompt
        # (High/Medium/Low tiers in escalation-prompts.md). The fallback sentence only applies
        # when the system prompt gives no rule.
        "Send one hard question to a committee of advisor models (the MoA preset's "
        "reference models) and get their independent answers back; you then weigh them "
        "against your own analysis and write the final answer yourself. The advisors "
        "cannot see this conversation, run tools, or check facts, so the question must be "
        "self-contained: goal, constraints, relevant data, and what was tried and how it "
        "failed. Do not include your own proposed answer. Each answered call uses one "
        "consultation from a per-conversation budget; when the budget is exhausted the "
        "tool returns an error and you proceed on your own judgment. When to call this "
        "tool is set by your system prompt. If it gives no rule, call it for hard "
        "multi-step reasoning, high-stakes or irreversible decisions, or a step that has "
        "failed twice, not for chat, simple lookups, or facts a search or tool can confirm."
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


def _text(value) -> str:
    """``str(value).strip()``, or "" for None."""
    return "" if value is None else str(value).strip()


def _one_line(value) -> str:
    """``str(value or "")`` with CR/LF collapsed to spaces (log fields)."""
    return str(value or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _hermes_home() -> str:
    """Asking profile home: get_hermes_home(), else $HERMES_HOME, else ~/.hermes. Never raises."""
    try:
        from hermes_constants import get_hermes_home
        home = _text(get_hermes_home())
        if home:
            return home
    except Exception:
        pass
    return os.environ.get("HERMES_HOME", "").strip() or os.path.expanduser("~/.hermes")


def _current_profile_name() -> str | None:
    """Asking profile name, best-effort; None when unknown. Never raises."""
    try:
        import hermes_cli.profiles as profiles  # type: ignore
    except Exception:
        profiles = None
    for attr in ("current_profile_name", "get_active_profile_name"):
        try:
            name = _text(getattr(profiles, attr)())
        except Exception:  # module missing, attr missing/not callable, or it raised
            continue
        if name:
            return name
    for key in ("HERMES_SESSION_PROFILE", "HERMES_PROFILE_NAME", "HERMES_PROFILE"):
        name = os.environ.get(key, "").strip()
        if name:
            return name
    return None


def _current_model_name() -> str | None:
    """Asking model name, best-effort; None when unknown. Never raises.

    ``model`` may be a string, or a mapping: ``provider:default``, else
    ``default``, else ``name``.
    """
    try:
        from hermes_cli.config import load_config
        model = (load_config() or {}).get("model")
        if isinstance(model, str):
            return model.strip() or None
        if isinstance(model, dict):
            provider, default, name = (_text(model.get(k)) for k in ("provider", "default", "name"))
            if provider and default:
                return f"{provider}:{default}"
            return default or name or None
        return None
    except Exception:
        return None


def _default_log_path() -> str:
    return os.path.join(_hermes_home(), "logs", _LOG_FILE_NAME)


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


def _resolve_log_path_string(raw) -> str:
    """Resolve a log path string; relative paths join the asking profile home. Never raises."""
    try:
        text = os.path.expandvars(os.path.expanduser(_text(raw or "")))
        if not text or os.path.isabs(text):
            return text
        return os.path.join(_hermes_home(), text)
    except Exception:
        return ""


def _resolve_log_path() -> str | None:
    """Precedence: CONSULT_COMMITTEE_LOG env (incl. off) > GUI settings; default OFF. Never raises."""
    try:
        raw = os.environ.get(_LOG_PATH_ENV)
        if raw is not None:
            text = raw.strip()
            if not text or text.lower() in _OFF_WORDS:
                return None
            return _resolve_log_path_string(text)
        settings = _plugin_log_settings()
        if settings.get("logging") is not True:
            return None
        custom = _text(settings.get("log_path") or "")
        return _resolve_log_path_string(custom) if custom else _default_log_path()
    except Exception:
        return None


def _advisor_system_text(settings_override: str | None = None) -> str:
    """Resolve the advisor system prompt. Never raises.

    Precedence: CONSULT_COMMITTEE_ADVISOR_SYSTEM env > settings_override /
    GUI ``advisor_system`` > bundled ``advisor_system.md`` next to ``__file__``
    > hardcoded draft fallback. Returns the stripped text, or "" for disabled.

    Env semantics: unset or empty/whitespace-only means "not set" and falls
    through to the next layer (empty does NOT disable). The values
    ``off``/``0``/``false``/``no`` (case-insensitive, stripped) explicitly
    disable the system role and return "". The same off-words disable when
    given via GUI/override. A missing or unreadable bundled file falls back
    to the hardcoded draft.
    """
    try:
        text = _text(os.environ.get(_ADVISOR_SYSTEM_ENV))
        if text:
            return "" if text.lower() in _OFF_WORDS else text
        if settings_override is None:
            settings_override = _plugin_log_settings().get("advisor_system")
        text = _text(settings_override)
        if text:
            return "" if text.lower() in _OFF_WORDS else text
        try:
            bundled = os.path.join(os.path.dirname(__file__), _ADVISOR_SYSTEM_FILE)
            with open(bundled, encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                return text
        except Exception:
            pass
        return _ADVISOR_SYSTEM_DEFAULT
    except Exception:
        return _ADVISOR_SYSTEM_DEFAULT


def _max_consultations(settings: dict | None = None) -> int:
    """Cap from settings.max_consultations; invalid/missing → default. Never raises.

    Accepts a non-negative int (or an integral float, since the GUI field is a
    number). 0 disables the tool (every call reports the budget exhausted).
    """
    try:
        if settings is None:
            settings = _plugin_log_settings()
        value = settings.get("max_consultations")
        if isinstance(value, bool) or value is None:
            return _DEFAULT_MAX_CONSULTATIONS
        if isinstance(value, float):
            if not value.is_integer():
                return _DEFAULT_MAX_CONSULTATIONS
            value = int(value)
        elif isinstance(value, str):
            value = int(value.strip())
        if isinstance(value, int) and value >= 0:
            return value
    except Exception:
        pass
    return _DEFAULT_MAX_CONSULTATIONS


def _budget_key(kwargs: dict) -> tuple[str, str]:
    """(key, scope label) for the budget counter. Never raises."""
    try:
        for name, scope in (("conversation_id", "conversation"), ("session_id", "session")):
            value = kwargs.get(name)
            if isinstance(value, str) and value.strip():
                return f"{name}:{value.strip()}", scope
    except Exception:
        pass
    return _PROCESS_KEY, "gateway process"


def _reserve_consultation(key: str, cap: int) -> int | None:
    """Reserve one slot for *key*; returns the log seq, or None when exhausted."""
    global _log_seq
    with _lock:
        used = _consultations.get(key, 0)
        if used >= cap:
            return None
        _consultations[key] = used + 1
        _log_seq += 1
        return _log_seq


def _refund_consultation(key: str) -> None:
    """Give back a reserved slot (call delivered no answer). Never raises."""
    try:
        with _lock:
            used = _consultations.get(key, 0)
            if used <= 1:
                _consultations.pop(key, None)
            else:
                _consultations[key] = used - 1
    except Exception:
        pass


def _is_failed_output(text: str) -> bool:
    """True for an empty advisor output or a Hermes failure/skip sentinel."""
    return not text or text.lstrip().lower().startswith(_FAILURE_PREFIXES)


def _preset_in_use(moa_cfg, explicit: str | None) -> tuple[str, str]:
    """(name, label) of the MoA preset a call will use. Never raises.

    An explicit argument wins. Otherwise Hermes' own rule applies:
    ``moa.default_preset`` when it names an existing preset, else the FIRST
    preset listed. The name is asked of Hermes' ``normalize_moa_config`` when
    available; without it (older Hermes, tests) the configured name or
    ``default`` is assumed. The label explains where an implicit name came from.
    """
    try:
        if explicit:
            return explicit, repr(explicit)
        raw = _text(moa_cfg.get("default_preset")) if isinstance(moa_cfg, dict) else ""
        try:
            from hermes_cli.moa_config import normalize_moa_config
            name = _text(normalize_moa_config(moa_cfg).get("default_preset"))
        except Exception:
            name = ""
        if name:
            if name == raw:
                return name, f"{name!r} (moa.default_preset)"
            why = f"moa.default_preset {raw!r} not found" if raw else "moa.default_preset not set"
            return name, f"{name!r} (first preset; {why})"
        if raw:
            return raw, f"{raw!r} (moa.default_preset)"
    except Exception:
        pass
    return "default", "'default' (no moa.default_preset set)"


def _interrupted() -> bool:
    """True when Hermes has flagged this tool thread to stop (user stop, new
    message, shutdown). Hermes then stops waiting for the result after a short
    grace period, so a result built anyway is never delivered. Never raises."""
    try:
        from tools.interrupt import is_interrupted
        return bool(is_interrupted())
    except Exception:
        return False


def _budget_log_label(key: str, scope: str) -> str:
    """'conversation <id>' / 'session <id>' for the log header; "" for the process bucket."""
    if key == _PROCESS_KEY:
        return ""
    return f"{scope} {key.split(':', 1)[1]}"


def _reference_temperature(preset: dict) -> float:
    """preset.reference_temperature, honouring 0; missing/None/invalid → default."""
    value = preset.get("reference_temperature")
    if value is None or isinstance(value, bool):
        return _DEFAULT_REFERENCE_TEMPERATURE
    try:
        return float(value)
    except (TypeError, ValueError):
        return _DEFAULT_REFERENCE_TEMPERATURE


def _advisor_messages(question: str, system_text: str) -> list[dict]:
    """Build fan-out messages; system role first unless disabled. Pure, no imports."""
    text = system_text.strip() if isinstance(system_text, str) else ""
    if text:
        return [{"role": "system", "content": text}, {"role": "user", "content": question}]
    return [{"role": "user", "content": question}]


def _maybe_log_call_text(entry: dict) -> None:
    """Append one plain-text block unless disabled; never raise."""
    try:
        path = _resolve_log_path()
        if not path:
            return
        header = [f"{entry.get('ts')}", f"seq {entry.get('seq')}"]
        conversation = _text(entry.get("conversation"))
        if conversation:
            header.append(conversation)
        profile, model = _text(entry.get("profile")), _text(entry.get("model"))
        if profile:
            header.append(f"profile {profile}")
        if model:
            header.append(f"model {model}")
        header.append(f"preset {entry.get('preset') or 'default'}")
        lines = [f"=== {' | '.join(header)} ===", f"Q: {_one_line(entry.get('question'))}"]
        system = _one_line(entry.get("system"))
        if system:
            lines.append(f"S: {system}")
        for adv in entry.get("advisors") or []:
            marker = " [FAILED]" if adv.get("failed") else ""
            lines.append(f"--- advisor: {adv.get('model')}{marker} ---")
            lines.append(str(adv.get("answer") or ""))
        lines.append(f"answered: {entry.get('answered')} failed: {entry.get('failed')}")
        if entry.get("cancelled"):
            lines.append("cancelled: stop requested; not delivered to the model, no budget used")
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n\n")
    except Exception:
        pass


def _handle_consult_committee(args: dict, **kwargs) -> str:
    from tools.registry import tool_result, tool_error

    question = str(args.get("question") or "").strip()
    if not question:
        return tool_error("question is required")

    budget_key, budget_scope = _budget_key(kwargs)
    cap = _max_consultations()
    seq = _reserve_consultation(budget_key, cap)
    if seq is None:
        return tool_error(
            f"committee budget exhausted ({cap} consultations this {budget_scope}). "
            "Answer with your own best judgment and say the budget ran out."
        )

    # Budget is consumed only when at least one advisor answer is delivered;
    # every other exit (bad preset, no models, all failed, exception) refunds.
    delivered = False
    try:
        from hermes_cli.config import load_config
        from hermes_cli.moa_config import resolve_moa_preset
        from agent.moa_loop import _run_references_parallel

        # No explicit preset → Hermes picks moa.default_preset, else the first
        # preset listed (resolve_moa_preset(None) applies that rule).
        preset_name = str(args.get("preset") or "").strip() or None
        moa_cfg = load_config().get("moa") or {}
        preset_used, preset_label = _preset_in_use(moa_cfg, preset_name)
        try:
            preset = resolve_moa_preset(moa_cfg, preset_name)
        except (KeyError, ValueError):
            # Real Hermes raises MoAPresetNotFoundError (a ValueError); older
            # fakes/tests raised KeyError — tolerate both.
            return tool_error(f"unknown MoA preset: {preset_label}")

        reference_models = preset.get("reference_models") or []
        if not reference_models:
            return tool_error(f"MoA preset {preset_label} has no reference models configured")

        temperature = _reference_temperature(preset)
        system_text = _advisor_system_text()
        messages = _advisor_messages(question, system_text)
        outputs = _run_references_parallel(
            reference_models,
            messages,
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
            failed = _is_failed_output(text)
            if failed:
                failures += 1
            if len(text) > _TRUNCATE_CHARS:
                text = text[:_TRUNCATE_CHARS] + "\n[... truncated]"
            advisors.append({"model": label, "answer": text, "failed": failed})

        # A stop request while the advisors ran means Hermes has abandoned
        # (or is about to abandon) this call: don't count it against the budget.
        cancelled = _interrupted()
        _maybe_log_call_text({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "seq": seq,
            "conversation": _budget_log_label(budget_key, budget_scope),
            "preset": preset_used,
            "cancelled": cancelled,
            "profile": _current_profile_name(),
            "model": _current_model_name(),
            "question": question[:_QUESTION_LOG_CHARS],
            "system": system_text,
            "advisors": advisors,
            "answered": len(advisors) - failures,
            "failed": failures,
        })
        if cancelled:
            return tool_error(
                "committee consultation cancelled by a stop request; the advisors' "
                "answers were not delivered and no budget was used"
            )
        answered = len(advisors) - failures
        if answered == 0:
            return tool_error("all committee advisors failed — answer with your own best judgment")

        result = tool_result({
            "advisors": advisors,
            "answered": answered,
            "failed": failures,
            "note": (
                "You are the aggregator. Weigh these independent advisor answers against "
                "your own analysis, resolve disagreements explicitly, and produce the "
                "final answer yourself."
            ),
        })
        delivered = True
        return result
    except Exception as exc:  # never raise from a tool handler
        return tool_error(f"committee consultation failed: {exc!r}")
    finally:
        if not delivered:
            _refund_consultation(budget_key)


def register(ctx) -> None:
    ctx.register_tool(
        name="consult_committee",
        toolset="committee",
        schema=CONSULT_SCHEMA,
        handler=_handle_consult_committee,
        description="Consult the MoA advisor committee on a hard problem.",
        emoji="🏛️",
    )
