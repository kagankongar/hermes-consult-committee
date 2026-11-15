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
_ADVISOR_SYSTEM_OFF = ("off", "0", "false", "no")
_DEFAULT_REFERENCE_TEMPERATURE = 0.9
# Hermes' reference-output sentinels (agent.moa_loop: "[failed: …]" for an
# exception, "[skipped: …]" for recursion guard / user interrupt), matched the
# way Hermes itself does. "[error" kept for older Hermes / upstream fakes.
_FAILURE_PREFIXES = ("[failed:", "[skipped:", "[error")

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


def _current_profile_name() -> str | None:
    """Asking profile name, best-effort; None when unknown. Never raises."""
    try:
        try:
            import hermes_cli.profiles as _profiles  # type: ignore
        except Exception:
            _profiles = None  # type: ignore
        if _profiles is not None:
            for _attr in ("current_profile_name", "get_active_profile_name"):
                try:
                    _fn = getattr(_profiles, _attr, None)
                    if not callable(_fn):
                        continue
                    _val = _fn()
                    if isinstance(_val, str):
                        _val = _val.strip()
                        if _val:
                            return _val
                    elif _val is not None:
                        _text = str(_val).strip()
                        if _text:
                            return _text
                except Exception:
                    continue
    except Exception:
        pass
    try:
        for _key in ("HERMES_SESSION_PROFILE", "HERMES_PROFILE_NAME", "HERMES_PROFILE"):
            try:
                _env = os.environ.get(_key, "")
            except Exception:
                continue
            if isinstance(_env, str):
                _env = _env.strip()
                if _env:
                    return _env
    except Exception:
        pass
    return None


def _current_model_name() -> str | None:
    """Asking model name, best-effort; None when unknown. Never raises."""
    try:
        from hermes_cli.config import load_config
        try:
            cfg = load_config()
        except Exception:
            return None
        if not isinstance(cfg, dict):
            return None
        model = cfg.get("model")
        if isinstance(model, str):
            model = model.strip()
            return model or None
        if isinstance(model, dict):
            provider = model.get("provider")
            default = model.get("default")
            name = model.get("name")
            if isinstance(provider, str):
                provider = provider.strip()
            elif provider is not None:
                try:
                    provider = str(provider).strip()
                except Exception:
                    provider = ""
            else:
                provider = ""
            if isinstance(default, str):
                default = default.strip()
            elif default is not None:
                try:
                    default = str(default).strip()
                except Exception:
                    default = ""
            else:
                default = ""
            if isinstance(name, str):
                name = name.strip()
            elif name is not None:
                try:
                    name = str(name).strip()
                except Exception:
                    name = ""
            else:
                name = ""
            if provider and default:
                return f"{provider}:{default}"
            if default:
                return default
            if name:
                return name
            return None
        return None
    except Exception:
        return None


def _default_log_path() -> str:
    try:
        from hermes_constants import get_hermes_home
        try:
            home = get_hermes_home()
        except Exception:
            home = None
        if home:
            try:
                text = str(home).strip()
            except Exception:
                text = ""
            if text:
                return os.path.join(text, "logs", _LOG_FILE_NAME)
    except Exception:
        pass
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


def _resolve_log_path_string(raw: str) -> str:
    """Resolve a log path string; relative paths join the asking profile home. Never raises."""
    try:
        try:
            text = (raw or "").strip()
        except Exception:
            try:
                text = str(raw).strip()
            except Exception:
                return ""
        try:
            text = os.path.expanduser(text)
        except Exception:
            pass
        try:
            text = os.path.expandvars(text)
        except Exception:
            pass
        if not text:
            return ""
        try:
            if os.path.isabs(text):
                return text
        except Exception:
            pass
        base = ""
        try:
            from hermes_constants import get_hermes_home
            try:
                home = get_hermes_home()
            except Exception:
                home = None
            if home:
                try:
                    base = str(home).strip()
                except Exception:
                    base = ""
        except Exception:
            base = ""
        if not base:
            try:
                base = os.environ.get("HERMES_HOME", "").strip()
            except Exception:
                base = ""
            if not base:
                try:
                    base = os.path.expanduser("~/.hermes")
                except Exception:
                    base = ""
        if not base:
            return text
        try:
            return os.path.join(base, text)
        except Exception:
            return text
    except Exception:
        try:
            return str(raw)
        except Exception:
            return ""


def _resolve_log_path() -> str | None:
    """Precedence: CONSULT_COMMITTEE_LOG env (incl. off) > GUI settings; default OFF."""
    try:
        raw = os.environ.get(_LOG_PATH_ENV)
    except Exception:
        raw = None
    if raw is not None:
        try:
            text = raw.strip()
        except Exception:
            try:
                text = str(raw).strip()
            except Exception:
                return None
        if not text or text.lower() in ("off", "0", "false", "no"):
            return None
        try:
            return _resolve_log_path_string(text)
        except Exception:
            return text
    try:
        settings = _plugin_log_settings()
    except Exception:
        settings = {}
    try:
        if settings.get("logging") is not True:
            return None
    except Exception:
        return None
    try:
        custom = str(settings.get("log_path") or "").strip()
    except Exception:
        custom = ""
    if not custom:
        return _default_log_path()
    try:
        return _resolve_log_path_string(custom)
    except Exception:
        return custom


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
        try:
            raw_env = os.environ.get(_ADVISOR_SYSTEM_ENV)
        except Exception:
            raw_env = None
        if raw_env is not None:
            try:
                if isinstance(raw_env, str):
                    env_text = raw_env.strip()
                else:
                    env_text = str(raw_env).strip()
            except Exception:
                env_text = ""
            if env_text.lower() in _ADVISOR_SYSTEM_OFF:
                return ""
            if env_text:
                return env_text
        candidate = settings_override
        if candidate is None:
            try:
                settings = _plugin_log_settings()
            except Exception:
                settings = {}
            try:
                candidate = settings.get("advisor_system")
            except Exception:
                candidate = None
        if candidate is not None:
            try:
                if isinstance(candidate, str):
                    gui_text = candidate.strip()
                else:
                    gui_text = str(candidate).strip()
            except Exception:
                gui_text = ""
            if gui_text.lower() in _ADVISOR_SYSTEM_OFF:
                return ""
            if gui_text:
                return gui_text
        try:
            bundled = os.path.join(os.path.dirname(__file__), _ADVISOR_SYSTEM_FILE)
            with open(bundled, encoding="utf-8") as f:
                file_text = f.read().strip()
            if file_text:
                return file_text
        except Exception:
            pass
        return _ADVISOR_SYSTEM_DEFAULT
    except Exception:
        try:
            return _ADVISOR_SYSTEM_DEFAULT
        except Exception:
            return ""


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


def _default_preset_label(moa_cfg) -> str:
    """Human label for the preset used when no preset argument is given. Never raises."""
    try:
        name = moa_cfg.get("default_preset") if isinstance(moa_cfg, dict) else None
        if isinstance(name, str) and name.strip():
            return f"{name.strip()!r} (moa.default_preset)"
    except Exception:
        pass
    return "'default' (no moa.default_preset set)"


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
    try:
        text = system_text.strip() if isinstance(system_text, str) else ""
    except Exception:
        text = ""
    if text:
        return [{"role": "system", "content": text}, {"role": "user", "content": question}]
    return [{"role": "user", "content": question}]


def _maybe_log_call_text(entry: dict) -> None:
    """Append one plain-text block unless disabled; never raise."""
    path = _resolve_log_path()
    if not path:
        return
    try:
        preset = entry.get("preset_requested") or "default"
        profile = entry.get("profile")
        model = entry.get("model")
        if isinstance(profile, str):
            profile = profile.strip()
        elif profile is not None:
            try:
                profile = str(profile).strip()
            except Exception:
                profile = ""
        else:
            profile = ""
        if isinstance(model, str):
            model = model.strip()
        elif model is not None:
            try:
                model = str(model).strip()
            except Exception:
                model = ""
        else:
            model = ""
        question = str(entry.get("question") or "")
        question = question.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        system = str(entry.get("system") or "")
        system = system.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        header_parts = [f"{entry.get('ts')}", f"seq {entry.get('seq')}"]
        if profile:
            header_parts.append(f"profile {profile}")
        if model:
            header_parts.append(f"model {model}")
        header_parts.append(f"preset {preset}")
        lines = [
            f"=== {' | '.join(header_parts)} ===",
            f"Q: {question}",
        ]
        if system:
            lines.append(f"S: {system}")
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

        # No explicit preset → defer to the user's configured moa.default_preset
        # (resolve_moa_preset(None) honors it; only falls back to 'default' when
        # the config names no default at all).
        preset_name = str(args.get("preset") or "").strip() or None
        moa_cfg = load_config().get("moa") or {}
        preset_label = repr(preset_name) if preset_name else _default_preset_label(moa_cfg)
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
        try:
            system_text = _advisor_system_text()
        except Exception:
            system_text = ""
        try:
            messages = _advisor_messages(question, system_text)
        except Exception:
            messages = [{"role": "user", "content": question}]
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

        try:
            profile_name = _current_profile_name()
        except Exception:
            profile_name = None
        try:
            model_name = _current_model_name()
        except Exception:
            model_name = None
        _maybe_log_call_text({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "seq": seq,
            "preset_requested": args.get("preset"),
            "profile": profile_name,
            "model": model_name,
            "question": question[:_QUESTION_LOG_CHARS],
            "system": system_text,
            "advisors": advisors,
            "answered": len(advisors) - failures,
            "failed": failures,
        })
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
