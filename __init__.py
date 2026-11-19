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

On a Hermes with plugin system prompt sections, ``register`` also adds the
section ``consult-committee.escalation-policy``: with the setting
``escalation_tier`` (off by default) on high, medium or low, it injects that
tier's block from ``escalation-prompts.md`` into the session's system prompt,
unless the profile's SOUL.md already has a ``## Committee Escalation`` block.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import datetime
import inspect
import json
import logging
import os
import re
import threading
import time

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
_logger = logging.getLogger(__name__)
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
# Hermes abandons a tool call at its tool deadline (default 420 s); the fan-out
# returns what it has this long before that.
_DEFAULT_TOOL_DEADLINE_SECONDS = 420.0
_TOOL_DEADLINE_MARGIN_SECONDS = 20.0
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

    The session's live model first, so ``-m`` and ``/model`` show: Hermes'
    per-turn main-runtime binding, read as its own tools read it
    (``agent.auxiliary_client._read_main_model`` / ``_read_main_provider``,
    which fall back to config.yaml themselves). Without those (older Hermes,
    tests) the config's ``model``: a string, or a mapping ``provider:default``,
    else ``default``, else ``name``.
    """
    try:
        from agent.auxiliary_client import _read_main_model, _read_main_provider
        model = _text(_read_main_model())
        if model:
            provider = _text(_read_main_provider())
            return f"{provider}:{model}" if provider else model
    except Exception:
        pass
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

    Missing/None/other non-string → ``committee``; empty, an off-word or a YAML
    1.1 ``off``/``no``/``0`` that arrived as ``False``/zero → "" (use MoA's
    default preset instead).
    """
    try:
        if settings is None:
            settings = _plugin_log_settings()
        value = settings.get("preset")
        if isinstance(value, (int, float)) and value == 0:  # YAML off/no/0; True != 0
            return ""
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
    committee refuses to call models nobody chose. A missing or ``None``
    ``presets`` → False: Hermes keeps its built-in presets, even beside a flat
    top-level list. Any other value without usable names (``{}``, ``[]``, a
    string, ``False``) replaces them, and the flat top level is then the
    ``default`` preset. Names are matched stripped, as Hermes does. Never
    raises; True when undecidable.
    """
    try:
        presets = moa_cfg.get("presets")
        if presets is None:
            return False
        named = {}
        for key, value in (presets.items() if isinstance(presets, dict) else ()):  # Hermes: str(key or "").strip(), blanks dropped
            if str(key or "").strip():
                named[str(key).strip()] = value
        wanted = _text(name)
        if named:
            raw = named.get(wanted)
        else:  # nothing usable: Hermes reads the flat top level as ``default``
            raw = moa_cfg if wanted == "default" else None
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


def _accepts_route_info(func) -> bool:
    """True when ``func`` names a ``route_info`` parameter. Never raises."""
    try:
        return "route_info" in inspect.signature(func).parameters
    except Exception:
        return False


def _hermes_model_name(model: str, provider: str) -> str:
    """The id Hermes sends for *model* on *provider*; "" when Hermes' normaliser is unavailable. Never raises."""
    try:
        from hermes_cli.model_normalize import normalize_model_for_provider
        return _text(normalize_model_for_provider(model, provider)) or model
    except Exception:
        return ""


def _fallback_failure(route_info: dict, wanted_model: str = "", provider: str = "") -> str:
    """``[failed: …]`` sentinel when another model than the slot's answered, else "".

    Hermes sets ``fallback_label`` in its recovery ladder; two client-resolution
    branches switch models without it, so a recorded ``model`` that differs from
    the slot's (``default`` = unknown) counts too, unless Hermes merely stripped
    a vendor prefix (unverifiable → not flagged). Never raises.
    """
    try:
        fallback = _text(route_info.get("fallback_label"))
        reason = _one_line(route_info.get("failure")) or "no reason given"
        landed, wanted = _text(route_info.get("model")), _text(wanted_model)
        if not fallback and landed and wanted and wanted.lower() != "auto" and landed != "default" and landed != wanted \
                and not ("/" in wanted and _hermes_model_name(wanted, provider) in ("", landed)):
            fallback = f"{_text(route_info.get('provider')) or 'auto'}:{landed}"
            reason = "request was routed to another model"
        if not fallback:
            return ""
        return f"[failed: advisor unavailable ({reason}); Hermes fell back to {fallback}, answer discarded]"
    except Exception:
        return ""


def _call_advisor(slot: dict, messages: list, temperature: float, timeout: float) -> tuple[str, str]:
    """One advisor call → ``(label, text)``. Never raises: an error becomes a
    ``[failed: …]`` sentinel, an empty reply "". An answer Hermes produced by
    falling back to another model (main agent / fallback chain) is discarded."""
    label = _slot_label(slot)
    try:
        from agent.auxiliary_client import call_llm
        extra = {}
        route_info: dict = {}
        if _accepts_route_info(call_llm):
            extra["route_info"] = route_info
        response = call_llm(
            task=_ADVISOR_TASK,
            messages=messages,
            temperature=temperature,
            timeout=timeout,
            reasoning_config=_slot_reasoning(slot),
            **_slot_runtime(slot),
            **extra,
        )
        return label, _fallback_failure(route_info, _text(slot.get("model")), _text(slot.get("provider"))) or _response_text(response)
    except Exception as exc:
        return label, f"[failed: {exc}]"


def _fanout_deadline_seconds() -> float | None:
    """Seconds the fan-out may run: Hermes' sequential tool deadline minus a margin.

    Half the deadline when it is shorter than twice the margin; None when Hermes
    has the deadline disabled; the default deadline minus the margin when the
    resolver is unavailable. Never raises.
    """
    try:
        from agent.tool_executor import _resolve_sequential_tool_timeout
        value = _resolve_sequential_tool_timeout()  # None = deadline disabled
        if value is None:
            return None
        value = float(value)
        if value <= 0:
            return None
        return value - _TOOL_DEADLINE_MARGIN_SECONDS if value > 2 * _TOOL_DEADLINE_MARGIN_SECONDS else value / 2
    except Exception:
        return _DEFAULT_TOOL_DEADLINE_SECONDS - _TOOL_DEADLINE_MARGIN_SECONDS


def _executor_class():
    """Hermes' daemon-thread pool (workers never block process exit), else the
    stdlib pool. Never raises."""
    try:
        from tools.daemon_pool import DaemonThreadPoolExecutor
        return DaemonThreadPoolExecutor
    except Exception:
        return concurrent.futures.ThreadPoolExecutor


def _run_advisors(slots: list, messages: list, *, temperature: float, timeout: float) -> list[tuple[str, str]]:
    """Ask every advisor in parallel; ``(label, text)`` per slot, in slot order.

    Each worker runs in a copy of the caller's contextvars (Hermes' session
    accounting lives there). The wait polls for a stop request and for the
    overall deadline (``_fanout_deadline_seconds``): on either it returns at
    once and the unfinished advisors become ``[skipped: …]`` (in-flight HTTP
    calls cannot be killed; they finish in the background).
    """
    if not slots:
        return []
    results: list = [None] * len(slots)
    pool = _executor_class()(max_workers=min(_MAX_ADVISOR_WORKERS, len(slots)))
    limit = _fanout_deadline_seconds()
    started = time.monotonic()
    abandoned = False
    skipped = "[skipped: stop requested]"
    try:
        futures = {
            pool.submit(contextvars.copy_context().run, _call_advisor, slot, messages, temperature, timeout): idx
            for idx, slot in enumerate(slots)
        }
        pending = set(futures)
        while pending:
            wait_for = _INTERRUPT_POLL_SECONDS
            if limit is not None:
                wait_for = max(0.0, min(wait_for, limit - (time.monotonic() - started)))
            done, pending = concurrent.futures.wait(pending, timeout=wait_for)
            for future in done:
                results[futures[future]] = future.result()
            if pending and _interrupted():
                abandoned = True
                break
            if pending and limit is not None and time.monotonic() - started >= limit:
                abandoned = True
                skipped = f"[skipped: committee deadline reached after {limit:g}s]"
                break
        # An advisor finishing between the last wait() and the break still counts.
        for future, idx in futures.items():
            if results[idx] is None and future.done() and not future.cancelled():
                results[idx] = future.result()
    finally:
        pool.shutdown(wait=not abandoned, cancel_futures=abandoned)
    return [
        result if result is not None else (_slot_label(slots[idx]), skipped)
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


_plugin_version: str | None = None  # cached; "" = looked up, unknown


def _plugin_version_text() -> str:
    """``version:`` from plugin.yaml beside this file (simple line parse); "" when unknown. Cached, never raises."""
    global _plugin_version
    if _plugin_version is None:
        _plugin_version = ""
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin.yaml"), encoding="utf-8") as f:
                for line in f:
                    if line.startswith("version:"):
                        _plugin_version = line.split(":", 1)[1].strip().strip("\"'")
                        break
        except Exception:
            pass
    return _plugin_version


def _advisor_status(answer, chars: int) -> tuple[str, bool]:
    """``(status line, print body)`` for one advisor, from the delivered answer text. Never raises."""
    try:
        text = _text(answer)
        if not text:
            return "empty reply", False
        low = text.lower()
        if not low.startswith(_FAILURE_PREFIXES):
            if chars > _TRUNCATE_CHARS:
                return f"answered ({_TRUNCATE_CHARS} chars, truncated from {chars} chars)", True
            return f"answered ({chars} chars)", True
        inner = text[1:-1] if text.endswith("]") else text[1:]
        if low.startswith("[failed:") and "Hermes fell back to" in text:
            return f"fallback discarded: {inner.split(':', 1)[1].strip()}", False
        if low.startswith("[skipped:"):
            return f"skipped: {inner.split(':', 1)[1].strip()}", False
        if low.startswith("[failed:"):
            return f"failed: {inner.split(':', 1)[1].strip()}", False
        return f"failed: {text}", False  # legacy "[error…"
    except Exception:
        return "unknown", False


def _format_log_block(entry: dict) -> str:
    """One log block (see GUIDE) from a prepared entry; ends with a blank line. Never raises."""
    try:
        header = [_text(entry.get("ts"))]
        if entry.get("seq") is not None:
            header.append(f"seq {entry['seq']}")
        for part in (_text(entry.get("conversation")),
                     f"profile {_text(entry.get('profile'))}" if _text(entry.get("profile")) else "",
                     f"model {_text(entry.get('model'))}" if _text(entry.get("model")) else ""):
            if part:
                header.append(part)
        version = _text(entry.get("version"))
        lines = [f"=== {' | '.join(header)} ===", f"plugin:   {_PLUGIN_ID}{' ' + version if version else ''}"]
        for key, label in (("preset", "preset:   "), ("note", "note:     "), ("budget", "budget:   "),
                           ("timing", "timing:   "), ("outcome", "outcome:  ")):
            value = _one_line(entry.get(key))
            if value:
                lines.append(label + value)
        question = _one_line(entry.get("question"))
        if question:
            shown = question[:_QUESTION_LOG_CHARS]
            counts = f"{len(question)} chars" if shown == question else f"{len(question)} chars, first {len(shown)} shown"
            lines += ["", f"Q ({counts}): {shown}"]
        system = _one_line(entry.get("system"))
        if system:
            lines.append(f"S: {system}")
        advisors = entry.get("advisors") or []
        gap = True
        for idx, adv in enumerate(advisors, 1):
            answer = adv.get("answer") or ""
            status, body = _advisor_status(answer, adv.get("chars", len(answer)))
            if gap:
                lines.append("")
            lines += [f"--- advisor {idx} of {len(advisors)}: {adv.get('model')} ---", f"status:   {status}"]
            if body:
                lines.append(str(answer))
            gap = body
        return "\n".join(lines) + "\n\n"
    except Exception:
        return ""


def _maybe_log_call_text(entry: dict, path: str | None = None) -> None:
    """Append one plain-text block unless disabled; never raise."""
    try:
        path = path or _resolve_log_path()
        if not path:
            return
        block = _format_log_block(entry)
        if not block:
            return
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(block)
    except Exception:
        pass


def _log_exit(state: dict) -> None:
    """Write the single log block for a tool call from the handler's *state*; never raises.

    Called once per call, at exit, after any budget refund (so the budget line is
    the true post-call count). Does nothing, and no work, when logging is off.
    """
    try:
        path = _resolve_log_path()
        if not path:
            return
        budget = ""
        key = state.get("budget_key")
        if key is not None:
            cap = state.get("cap")
            where = state.get("budget_scope")
            with _lock:
                used = _consultations.get(key, 0)
            if cap == 0:
                budget = "0 of 0 used (consult_committee disabled by max_consultations 0)"
            else:
                budget = f"{used} of {cap} used in this {where}"
        parts = [f"{time.monotonic() - state['started']:.1f} s elapsed"]
        if state.get("fanout"):
            deadline = _fanout_deadline_seconds()
            parts.append("no deadline" if deadline is None else f"deadline {deadline:g} s")
            if state.get("timeout") is not None:
                parts.append(f"advisor timeout {state['timeout']:g} s")
            if state.get("temperature") is not None:
                parts.append(f"temperature {state['temperature']:g}")
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "seq": state.get("seq"),
            "conversation": _budget_log_label(key, state.get("budget_scope")) if key is not None else "",
            "profile": _current_profile_name(),
            "model": _current_model_name(),
            "version": _plugin_version_text(),
            "preset": state.get("preset_label"),
            "note": state.get("preset_note"),
            "budget": budget,
            "timing": "; ".join(parts),
            "outcome": state.get("outcome"),
            "question": state.get("question"),
            "system": state.get("system"),
            "advisors": state.get("advisors"),
        }
        _maybe_log_call_text(entry, path)
    except Exception:
        pass


def _handle_consult_committee(args: dict, **kwargs) -> str:
    from tools.registry import tool_result, tool_error

    state: dict = {"started": time.monotonic()}  # collected for the one log block (_log_exit)
    question = str(args.get("question") or "").strip()
    if not question:
        state["outcome"] = "refused: question is required (no budget used)"
        _log_exit(state)
        return tool_error("question is required")
    state["question"] = question

    budget_key, budget_scope = _budget_key(kwargs)
    cap = _max_consultations()
    state.update(budget_key=budget_key, budget_scope=budget_scope, cap=cap)
    seq = _reserve_consultation(budget_key, cap)
    if seq is None:
        state["outcome"] = f"refused: budget exhausted ({cap} consultations this {budget_scope}; no budget used)"
        _log_exit(state)
        return tool_error(
            f"committee budget exhausted ({cap} consultations this {budget_scope}). "
            "Answer with your own best judgment and say the budget ran out."
        )
    state["seq"] = seq

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
        state["preset_note"] = preset_note
        if preset is None:
            preset_used, preset_label = _preset_in_use(moa_cfg, explicit)
            state["preset_label"] = preset_label
            try:
                preset = resolve_moa_preset(moa_cfg, explicit)
            except (KeyError, ValueError):
                # Real Hermes raises MoAPresetNotFoundError (a ValueError); older
                # fakes/tests raised KeyError — tolerate both.
                state["outcome"] = f"refused: unknown MoA preset {preset_label} (no budget used)"
                return tool_error(f"unknown MoA preset: {preset_label}")
        state["preset_label"] = preset_label

        # Hermes substitutes its built-in advisors for an empty preset; refuse.
        if not _has_configured_advisors(_user_moa_config(moa_cfg), preset_used) or not preset.get("reference_models"):
            state["outcome"] = f"refused: MoA preset {preset_label} has no reference models configured (no budget used)"
            return tool_error(f"MoA preset {preset_label} has no reference models configured")
        # Per-advisor `enabled: false` is honoured; the preset-level `enabled`
        # is MoA's own switch (hides the preset from MoA) and is ignored here.
        slots = [slot for slot in preset["reference_models"] if slot.get("enabled", True) is not False]
        if not slots:
            state["outcome"] = f"refused: MoA preset {preset_label} has every reference model disabled (no budget used)"
            return tool_error(f"MoA preset {preset_label} has every reference model disabled")

        system_text = _advisor_system_text()
        temperature, timeout = _reference_temperature(preset), _reference_timeout(preset)
        state.update(system=system_text, fanout=True, temperature=temperature, timeout=timeout)
        outputs = _run_advisors(
            slots,
            _advisor_messages(question, system_text),
            temperature=temperature,
            timeout=timeout,
        )

        advisors = []
        logged = state["advisors"] = []
        failures = 0
        for label, text in outputs:
            text = (text or "").strip()
            failed = _is_failed_output(text)
            if failed:
                failures += 1
            chars = len(text)
            if len(text) > _TRUNCATE_CHARS:
                text = text[:_TRUNCATE_CHARS] + "\n[... truncated]"
            advisors.append({"model": label, "answer": text, "failed": failed})
            logged.append({"model": label, "answer": text, "chars": chars})

        # A stop request while the advisors ran means Hermes has abandoned
        # (or is about to abandon) this call: don't count it against the budget.
        cancelled = _interrupted()
        answered = len(advisors) - failures
        counts = f"{answered} of {len(advisors)} advisors answered"
        if cancelled:
            state["outcome"] = ("cancelled (stop request or Hermes' tool deadline; "
                                "answers not delivered; no budget used)")
            return tool_error(
                "committee consultation cancelled (stop request or Hermes' tool deadline); the advisors' "
                "answers were not delivered and no budget was used"
            )
        if answered == 0:
            state["outcome"] = f"all advisors failed (0 of {len(advisors)} answered; no budget used)"
            return tool_error("all committee advisors failed — answer with your own best judgment")
        cut = any(a["answer"].lower().startswith("[skipped: committee deadline reached") for a in advisors)
        state["outcome"] = f"delivered{' after deadline cut' if cut else ''} ({counts}; budget charged)"

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
        state["outcome"] = f"error: {_one_line(repr(exc))} (no budget used)"
        return tool_error(f"committee consultation failed: {exc!r}")
    finally:
        if not delivered:
            _refund_consultation(budget_key)
        _log_exit(state)


# ---------------------------------------------------------------------------
# Escalation tier: inject a caller tier block into new sessions' system prompt
# ---------------------------------------------------------------------------
_ESCALATION_PROMPTS_FILE = "escalation-prompts.md"
_ESCALATION_TIERS = ("high", "medium", "low")
_ESCALATION_SECTION_ID = "consult-committee.escalation-policy"
_ESCALATION_SECTION_MAX_CHARS = 4000
_SOUL_FILE = "SOUL.md"
_TIER_BLOCK_RE = re.compile(r"```\n(## Committee Escalation[^\n]*\n.*?)\n```", re.S)
_SOUL_HEADING_RE = re.compile(r"^##[ \t]+Committee Escalation\b", re.M | re.I)
_escalation_notes: set = set()  # keys already logged (once per process)


def _escalation_tier_setting(settings: dict | None = None) -> str:
    """Setting ``escalation_tier``: high | medium | low, else off. Never raises."""
    try:
        if settings is None:
            settings = _plugin_log_settings()
        value = settings.get("escalation_tier")
        if not isinstance(value, str):  # YAML off/no/0 arrive as False/0
            return "off"
        value = _text(value).lower()
        return value if value in _ESCALATION_TIERS else "off"
    except Exception:
        return "off"


def _tier_block(tier: str) -> str:
    """Prompt block for ``tier`` from escalation-prompts.md beside the plugin, else "". Never raises."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _ESCALATION_PROMPTS_FILE)
        with open(path, encoding="utf-8") as fh:
            blocks = _TIER_BLOCK_RE.findall(fh.read())
        wanted = f"## Committee Escalation ({tier})"
        return next((b for b in blocks if b.splitlines()[0] == wanted), "")
    except Exception:
        return ""


def _soul_has_escalation_block(home: str | None = None) -> bool:
    """True when the profile's SOUL.md already has a '## Committee Escalation' heading. Never raises."""
    try:
        if home is None:
            home = _hermes_home()
        with open(os.path.join(home, _SOUL_FILE), encoding="utf-8-sig", errors="replace") as fh:
            return bool(_SOUL_HEADING_RE.search(fh.read()))
    except Exception:
        return False


def _bind_profile_home(session_info):
    """Scope HERMES_HOME to the session's profile when it is not already; returns an undo. Never raises."""
    try:
        name = _text(session_info.get("profile_name"))
        if not name or name.lower() == "default":
            return lambda: None
        from hermes_cli.profiles import get_profile_dir
        home = get_profile_dir(name)
        if not os.path.isdir(home) or os.path.realpath(home) == os.path.realpath(_hermes_home()):
            return lambda: None
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        token = set_hermes_home_override(str(home))
    except Exception:
        return lambda: None

    def _undo() -> None:
        try:
            reset_hermes_home_override(token)
        except Exception:
            pass

    return _undo


def _note_once(key, level: int, msg: str, *args) -> None:
    """Log through ``_logger`` once per key per process. Never raises."""
    try:
        with _lock:
            if key in _escalation_notes:
                return
            _escalation_notes.add(key)
        _logger.log(level, msg, *args)
    except Exception:
        pass


def _escalation_section(session_info) -> str:
    """System-prompt section callable for Hermes: the chosen tier block, or "". Never raises."""
    try:
        undo = _bind_profile_home(session_info)
        try:
            tier = _escalation_tier_setting()
            if tier == "off":
                return ""
            home = _hermes_home()
            if _soul_has_escalation_block(home):
                _note_once(("soul", home), logging.INFO,
                           "consult-committee: escalation_tier=%s not injected: %s already has a "
                           "'## Committee Escalation' block", tier, os.path.join(home, _SOUL_FILE))
                return ""
            block = _tier_block(tier)
            if not block:
                _note_once(("missing", tier), logging.WARNING,
                           "consult-committee: no '## Committee Escalation (%s)' block found in %s; "
                           "nothing injected", tier, _ESCALATION_PROMPTS_FILE)
                return ""
            _note_once(("inject", home, tier), logging.INFO,
                       "consult-committee: injecting escalation_tier=%s block (%d chars) into new "
                       "sessions of %s", tier, len(block), home)
            return block
        finally:
            undo()
    except Exception:
        return ""


def register(ctx) -> None:
    ctx.register_tool(
        name="consult_committee",
        toolset="committee",
        schema=CONSULT_SCHEMA,
        handler=_handle_consult_committee,
        description="Consult the advisor committee (an MoA preset's reference models) on a hard problem.",
        emoji="🏛️",
    )
    add = getattr(ctx, "register_system_prompt_section", None)
    if callable(add):
        try:
            add(_ESCALATION_SECTION_ID, _escalation_section, position="after_memory",
                max_chars=_ESCALATION_SECTION_MAX_CHARS)
        except Exception as exc:
            _logger.warning("consult-committee: could not register the escalation section: %r", exc)
