# Review of the CCReview (2026-10-29) + improvement plan

Source under review: `CCReview.md` — external source-and-docs review of
`kagankongar/hermes-consult-committee` (no install, no test run, no live calls;
cross-checked against the Hermes MoA runner source). This document records
what I verified claim-by-claim against `__init__.py` (565 lines) and the
40-test suite, recalibrates severity, adds what the review missed, and turns
it into a prioritized work list. Live-call coverage the reviewer lacked has
since been done by me: `cheap` preset 2/2, `cheap-3` preset 3/3.

## 1. Claim verification

| # | Review claim | Code check | Verdict |
|---|---|---|---|
| P1a | Consultation is not enforced; model decides | By design; README reports measured 0-for-4 self-escalation | Confirmed, **not a defect** — the documented trade-off of a demand-driven tool. Fix belongs caller-side (harness policy), not in plugin |
| P1b | Budget cap is process-wide, message says "session" | `_escalation_count` module global (`threading.Lock`); error text says "this session" | Confirmed — but **already disclosed** in AGENTS.md handler contract. Review rediscovers it |
| P1c | Audit log stores sensitive content verbatim | `_maybe_log_call_text`: plain append, no redaction, no chmod, no rotation/retention | **Confirmed, the one true P1.** Opt-in ≠ safe |
| P1d | No enforced dissent resolution | Returns labelled answers + "weigh them yourself" aggregator note | Confirmed as design; P1 overstates. Synthesis contract = roadmap, not sev-1 |
| P2a | Private Hermes internals (`agent.moa_loop`, `hermes_cli.moa_config`) | Lazy imports in handler body | Confirmed. Tuple-regression history proves the coupling risk |
| P2b | Failure detection is string heuristics | `not text or startswith("[error") or "failed:" in text[:80].lower()`; `"[failed: …]"` caught only via substring clause | Confirmed. Legit answers discussing failure in first 80 chars false-positive |
| P2c | No committee-level timeout | `_run_references_parallel(models, messages, temperature, max_tokens)` — no timeout arg | Confirmed; relies on Hermes-internal timeouts |
| P3a | 6000-char truncation is not token-aware | `_TRUNCATE_CHARS = 6000` + `\n[... truncated]` | Confirmed, correctly P3 |
| P3b | Accounting tuple element discarded | `label, text = item[0], item[1]`; `item[2]` dropped | Confirmed, correctly P3 |
| T | 40 mocked tests; integration gap; 5 suggested test areas | Suite green (40/40); zero hits for concurrency/privacy/rotation tests | Confirmed. Real-Hermes compat test is highest value |

## 2. What the review missed (my additions)

1. **Budget is charged before dispatch.** The counter increments *before* preset
   resolution and fan-out, so unknown-preset errors, empty-reference errors, and
   crashed calls all consume budget. Sharper than the review's scope point; its
   "charge only after dispatch" fix is exactly right and should also define
   policy for failed calls (no charge) vs all-failed consultations (charge —
   the advisors ran).
2. **Sequence numbers skip on failure.** The log `seq` reads the same counter,
   which already incremented — failed dispatches leave gaps. Minor; matters for
   the review's proposed concurrency/log-integrity tests (assert monotonic, not
   contiguous).
3. **Counter ignores identity the handler already collects.** The log header
   resolves profile/model, but budgeting uses none of it. Per-session/profile
   counters with a global safety cap are implementable from data already on hand.

## 3. Recalibrated severity

- **P1:** log redaction + restrictive file permissions + rotation/retention.
- **P2:** budget increment placement + "this session" wording (or per-session
  counters + global cap); explicit `[failed:`-prefix check + failure-format
  tests; internals adapter + pinned-version compat test; committee-level
  deadline plumbing.
- **Roadmap (enhancements, not defects):** harness-side routing policy for
  high-risk tasks; lightweight synthesis contract (disagreements, unsupported
  claims, verification steps — description/schema tweak first, no new
  orchestration); token-aware truncation option; cost attribution from the
  accounting element.
- **Out of scope, keep as-is:** caller-as-aggregator; demand-driven invocation;
  partial-failure tolerance; zero import-time Hermes deps.

## 4. Proposed work order

1. Privacy: redact-before-write (secret-shaped patterns + configurable
   metadata-only mode), `0o600` on the log file, documented retention/rotation.
   Tests: redaction actually blocks secrets/PII from disk.
2. Budget: increment on successful dispatch; fix scope wording (or per-session
   counters + global cap). Tests: failed/early-error paths don't consume budget;
   exhaustion message matches actual scope.
3. Detection: explicit known-failure prefixes (incl. `[failed:`), case/whitespace
   variants; tests over exact result shapes per supported Hermes version
   (skipped refs, provider errors, 2- vs 3-tuples).
4. Compat: one real-Hermes test (resolve configured preset, run committee call,
   verify payload) per pinned version range; isolate internal-API access behind
   one adapter.
5. Deadline + attribution: expose committee-level timeout; record usage/cost
   metadata where the runtime provides it.
6. Measure, don't assume: escalation precision/recall benchmark over
   representative tasks (the review's best suggestion); diversity check before
   any move past 3 advisors.

## 5. Standing positions (agreed with reviewer)

- Keep this plugin as the consultation mechanism, not the whole oversight
  architecture: deterministic routing policy → committee → evidence-based
  verification (tests, analysis, diff review). Consensus is not proof.
- Diversity beats headcount: `cheap` (2 refs) for routine, `cheap-3` for hard
  calls; verify the third advisor adds independent information before expanding.
- The system-prompt escalation rule stays, treated as empirical improvement with
  measured rates — not a guarantee.
