"""consult-committee — escalate a hard question to an advisor committee.

Registers a `consult_committee` tool. The acting model calls it when a
problem matches the trigger criteria; the handler fans the question out to the
reference models of an MoA preset (by default the dedicated `committee`
preset) in parallel and returns their labelled answers as the tool result. The
acting model then synthesizes — it plays the aggregator role itself, with the
full conversation context a detached aggregator would lack.

Only MoA's *settings* are reused (hermes_cli.moa_config). The fan-out is the
plugin's own: one hermes ``call_llm`` per advisor, with the provider resolved
by ``resolve_runtime_provider``. All Hermes imports are lazy, inside function
bodies, to avoid import-time cycles.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import datetime
import json
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
# MoA preset that holds the committee's advisors (plugin setting `preset`).
_DEFAULT_COMMITTEE_PRESET = "committee"
# Per-advisor timeout when the preset sets no reference_timeout: MoA's own
# default (auxiliary.moa_reference.timeout). call_llm's generic default (30 s)
# is far too short for reasoning advisors.
_DEFAULT_ADVISOR_TIMEOUT = 900.0
# call_llm task name: own auxiliary.<task> config namespace, and Hermes records
# the advisors' token usage/cost against the session (it skips MoA's tasks).
_ADVISOR_TASK = "consult_committee"
_MAX_ADVISOR_WORKERS = 8
_INTERRUPT_POLL_SECONDS = 1.0
# Advisor-output sentinels: "[failed: …]" (exception), "[skipped: …]" (stop
# request). Same check as Hermes' moa_loop; "[error" kept for old logs/fakes.
_FAILURE_PREFIXES = ("[failed:", "[skipped:", "[error")

CONSULT_SCHEMA = {
    "name": "consult_committee",
    "description": (
        # Policy-neutral on purpose (handover A4/C1): this text is in context for
        # every profile, so WHEN to call lives in the caller's system prompt
        # (High/Medium/Low tiers in escalation-prompts.md). The fallback sentence only applies
        # when the system prompt gives no rule.
        "Send one hard question to a committee of advisor models (the reference models "
        "of an MoA preset) and get their independent answers back; you then weigh them "
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
                    "MoA preset name (optional; omit to use the committee preset "
                    "configured for this plugin)."
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


def _committee_preset_setting(settings: dict | None = None) -> str:
    """Plugin setting ``preset``: the committee's MoA preset. Never raises.

    Missing/None/non-string → ``committee``; empty or an off-word → "" (use
    MoA's default preset instead).
    """
    try:
        if settings is None:
            settings = _plugin_log_settings()
        value = settings.get("preset")
        if not isinstance(value, str):
            return _DEFAULT_COMMITTEE_PRESET
        value = value.strip()
        return "" if value.lower() in _OFF_WORDS else value
    except Exception:
        return _DEFAULT_COMMITTEE_PRESET


def _user_moa_config(merged_moa):
    """The ``moa`` section of config.yaml as the user wrote it, without the
    defaults ``load_config()`` merges in (which seed a ``default`` preset with
    Hermes' built-in advisors). *merged_moa* when the raw reader is missing
    (older Hermes, test fakes) or fails. Never raises."""
    try:
        from hermes_cli.config import read_raw_config
        raw = read_raw_config()
        if isinstance(raw, dict):
            return raw.get("moa") or {}
    except Exception:
        pass
    return merged_moa


def _has_configured_advisors(moa_cfg, name: str) -> bool:
    """False when preset *name* lists no usable reference model in *moa_cfg*,
    the user's own ``moa`` section (see ``_user_moa_config``).

    Hermes fills an empty or missing preset with its built-in advisors;
    committee refuses to call models nobody chose. A legacy flat config (no
    ``presets``) is read from the top level. Never raises; True when undecidable.
    """
    try:
        presets = moa_cfg.get("presets")
        raw = presets.get(name) if isinstance(presets, dict) and presets else moa_cfg
        refs = raw.get("reference_models") if isinstance(raw, dict) else None
        if isinstance(refs, str):  # hand-edited JSON string, as Hermes accepts
            try:
                refs = json.loads(refs)
            except ValueError:
                refs = []
        if isinstance(refs, dict):
            refs = [refs]
        return any(
            isinstance(slot, dict)
            and _text(slot.get("model"))
            and _text(slot.get("provider")).lower() not in ("", "moa")
            for slot in (refs if isinstance(refs, list) else [])
        )
    except Exception:
        return True


def _slot_label(slot: dict) -> str:
    """``provider:model`` (+ ``[reasoning=x]``), as Hermes labels MoA advisors."""
    provider, model = _text(slot.get("provider")), _text(slot.get("model"))
    label = f"{provider}:{model}" if provider else model
    effort = _text(slot.get("reasoning_effort"))
    return f"{label}[reasoning={effort}]" if effort else label


def _slot_runtime(slot: dict) -> dict:
    """call_llm route kwargs for one advisor: provider/model plus the profile's
    resolved base_url / api_key / api_mode / extra_body. Bare provider/model
    when resolution fails (call_llm then resolves itself). Never raises."""
    out = {"provider": _text(slot.get("provider")), "model": _text(slot.get("model"))}
    try:
        from hermes_cli.runtime_provider import resolve_runtime_provider
        rt = resolve_runtime_provider(requested=out["provider"], target_model=out["model"]) or {}
        out.update({k: rt[k] for k in ("base_url", "api_key", "api_mode") if rt.get(k)})
        overrides = rt.get("request_overrides")
        extra_body = overrides.get("extra_body") if isinstance(overrides, dict) else None
        if isinstance(extra_body, dict) and extra_body:
            out["extra_body"] = dict(extra_body)
    except Exception:
        pass
    return out


def _slot_reasoning(slot: dict):
    """The slot's ``reasoning_effort`` as a call_llm reasoning_config; None when unset. Never raises."""
    try:
        effort = _text(slot.get("reasoning_effort"))
        if not effort:
            return None
        from hermes_constants import parse_reasoning_effort
        return parse_reasoning_effort(effort)
    except Exception:
        return None


def _response_text(response) -> str:
    """Assistant text of a completed response; "" when absent. Never raises.

    Hermes' transport normalizer first (as its own MoA does, so provider
    response shapes it adapts are covered), else ``choices[0].message``.
    """
    try:
        from agent.transports import get_transport
        text = get_transport("chat_completions").normalize_response(response).content
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:
        pass
    try:
        message = response.choices[0].message
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if isinstance(content, list):  # content parts
            content = "".join(p.get("text") or "" for p in content if isinstance(p, dict))
        return content.strip() if isinstance(content, str) else ""
    except Exception:
        return ""


def _reference_timeout(preset: dict) -> float:
    """preset.reference_timeout when a positive number, else the MoA default."""
    value = preset.get("reference_timeout")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return _DEFAULT_ADVISOR_TIMEOUT


def _call_advisor(slot: dict, messages: list, temperature: float, timeout: float) -> tuple[str, str]:
    """One advisor call → ``(label, text)``. Never raises: an error becomes a
    ``[failed: …]`` sentinel, an empty reply ""."""
    label = _slot_label(slot)
    try:
        from agent.auxiliary_client import call_llm
        response = call_llm(
            task=_ADVISOR_TASK,
            messages=messages,
            temperature=temperature,
            timeout=timeout,
            reasoning_config=_slot_reasoning(slot),
            **_slot_runtime(slot),
        )
        return label, _response_text(response)
    except Exception as exc:
        return label, f"[failed: {exc}]"


def _run_advisors(slots: list, messages: list, *, temperature: float, timeout: float) -> list[tuple[str, str]]:
    """Ask every advisor in parallel; ``(label, text)`` per slot, in slot order.

    Each worker runs in a copy of the caller's contextvars (Hermes' session
    accounting lives there). The wait polls for a stop request: on one, it
    returns at once and the unfinished advisors become ``[skipped: …]``
    (in-flight HTTP calls cannot be killed; they finish in the background).
    """
    if not slots:
        return []
    results: list = [None] * len(slots)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(_MAX_ADVISOR_WORKERS, len(slots)))
    stopped = False
    try:
        futures = {
            pool.submit(contextvars.copy_context().run, _call_advisor, slot, messages, temperature, timeout): idx
            for idx, slot in enumerate(slots)
        }
        pending = set(futures)
        while pending:
            done, pending = concurrent.futures.wait(pending, timeout=_INTERRUPT_POLL_SECONDS)
            for future in done:
                results[futures[future]] = future.result()
            if pending and _interrupted():
                stopped = True
                break
    finally:
        pool.shutdown(wait=not stopped, cancel_futures=stopped)
    return [
        result if result is not None else (_slot_label(slots[idx]), "[skipped: stop requested]")
        for idx, result in enumerate(results)
    ]


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
        lines = [f"=== {' | '.join(header)} ==="]
        note = _one_line(entry.get("preset_note"))
        if note:
            lines.append(f"note: {note}")
        lines.append(f"Q: {_one_line(entry.get('question'))}")
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

        # Preset: explicit argument > plugin setting (default 'committee') >
        # Hermes' rule (moa.default_preset, else the first preset listed).
        # A missing committee preset falls back to Hermes' rule, with a note.
        explicit = str(args.get("preset") or "").strip() or None
        moa_cfg = load_config().get("moa") or {}
        preset, preset_note = None, ""
        configured = None if explicit else _committee_preset_setting()
        if configured:
            try:
                preset = resolve_moa_preset(moa_cfg, configured)
                preset_used, preset_label = configured, f"{configured!r} (committee preset)"
            except (KeyError, ValueError):
                preset_note = f"committee preset {configured!r} not found; used MoA's default preset"
        if preset is None:
            preset_used, preset_label = _preset_in_use(moa_cfg, explicit)
            try:
                preset = resolve_moa_preset(moa_cfg, explicit)
            except (KeyError, ValueError):
                # Real Hermes raises MoAPresetNotFoundError (a ValueError); older
                # fakes/tests raised KeyError — tolerate both.
                return tool_error(f"unknown MoA preset: {preset_label}")

        # Hermes substitutes its built-in advisors for an empty preset; refuse.
        if not _has_configured_advisors(_user_moa_config(moa_cfg), preset_used) or not preset.get("reference_models"):
            return tool_error(f"MoA preset {preset_label} has no reference models configured")
        # Per-advisor `enabled: false` is honoured; the preset-level `enabled`
        # is MoA's own switch (hides the preset from MoA) and is ignored here.
        slots = [slot for slot in preset["reference_models"] if slot.get("enabled", True) is not False]
        if not slots:
            return tool_error(f"MoA preset {preset_label} has every reference model disabled")

        system_text = _advisor_system_text()
        outputs = _run_advisors(
            slots,
            _advisor_messages(question, system_text),
            temperature=_reference_temperature(preset),
            timeout=_reference_timeout(preset),
        )

        advisors = []
        failures = 0
        for label, text in outputs:
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
            "preset_note": preset_note,
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

        payload = {
            "advisors": advisors,
            "answered": answered,
            "failed": failures,
            "note": (
                "You are the aggregator. Weigh these independent advisor answers against "
                "your own analysis, resolve disagreements explicitly, and produce the "
                "final answer yourself."
            ),
        }
        if preset_note:
            payload["preset_note"] = preset_note
        result = tool_result(payload)
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
        description="Consult the advisor committee (an MoA preset's reference models) on a hard problem.",
        emoji="🏛️",
    )
