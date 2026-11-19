# Consultations per conversation

The **Consultations per conversation** setting (`max_consultations`) caps how many times the acting model may call the committee in one conversation. Its purpose is to bound spend: if the model over-calls, the advisors' tokens are billed each time, and the cap stops that. Back to the [README](README.md); the short reference is in the [GUIDE](GUIDE.md#budget).

## The setting

Set it per profile: Settings → Capabilities → Plugins → consult-committee gear → **Consultations per conversation**.

| Value | Meaning |
|---|---|
| `5` (default) | Five answered calls, then the tool refuses: "committee budget exhausted (5 consultations this conversation)". The model carries on alone. |
| any number above 0 | The same, with your number. |
| `0` | The tool stays registered, but every call is refused. |
| `-1` (0.5.1+) | No limit. Calls are still counted. |
| other negatives, non-numbers | Treated as `5`. |

`-1` and not `0` for "unlimited" because `0` already means disabled; changing it would silently turn a switched-off tool into an unlimited one.

Suggested value per [escalation tier](escalation-prompts.md): High 8, Medium 5, Low 3. The tier does not set the budget; set both.

## What counts

Only a call where at least one advisor answered costs 1. One answer or four, the price is the same.

| Outcome | Costs 1? |
|---|---|
| At least one advisor answered | yes |
| Cut by Hermes' tool deadline, with answers already in | yes (delivered and charged) |
| Every advisor failed or timed out | no |
| Missing or empty preset, all advisors disabled | no |
| Internal error | no |
| You stopped the call (stop button or a new message) | no |

The plugin reserves the slot before asking the advisors and gives it back for the free outcomes. A stopped call's advisor requests are already in flight, so they still finish and the provider bills them; you only get the slot back.

## What "conversation" means

The count is kept per Hermes `conversation_id`; if Hermes passes none, per `session_id`; if neither, in one bucket shared by the whole gateway process.

- It survives context compression and resuming a session.
- `/new` starts a fresh count.
- Delegated subagents share their parent's count.
- It lives in the gateway's memory: a gateway restart or a plugin reload (`hermes plugins update`, disable and enable) resets every count to zero.

## Where you see it

- **The model** gets `budget: {used, cap, scope}` in every delivered result, and the note ends with "Consultations left this conversation: N." (`unlimited.` with `-1`, together with `"cap": null`).
- **`/committee status`** shows `Budget: 2 of 5 used in this conversation`. Slash commands get the session id while the budget is per conversation, so the line is exact only after the first committee call in the session; before that it reads `Budget: 0 of 5 used (no committee call in this session yet)`. With `-1`: `Budget: 2 used in this conversation (no cap)`, or `Budget: no cap (no committee call in this session yet)` before the first call. With `0`: `Budget: disabled (Consultations per conversation is 0)`.
- **The call log** (if on) has a `budget:` line per call: `2 of 5 used in this conversation`, or `2 used in this conversation (no cap)` with `-1` ([call log](GUIDE.md#committee-call-log)).

## Limits

- The cap bounds spend on answered calls. It does not bound retries while every advisor is down; those calls are free, so they cannot exhaust it.
- With `-1`, nothing but the tier's triggers limits advisor spend. That is fine with cheap advisors (a call with a Luna and a Haiku advisor costs well under a cent). Think twice with a high-effort thinking advisor, which costs far more per call.

## Examples

**1. Default 5.** Calls 1 to 5 are answered. Call 6 returns "committee budget exhausted (5 consultations this conversation)" without asking anyone; the model answers on its own and, as its tier block says, tells you so.

**2. A failed call is free.** Both advisors time out. The model gets "all committee advisors failed" and the count stays where it was, so a retry is possible.

**3. One of two advisors answers.** The other failed. The call is delivered with `answered: 1, failed: 1` and costs 1, the same as two answers.

**4. Resume, new chat, restart.** Used 3 of 5 on Monday; resuming the same session on Tuesday continues at 3. `/new` starts at 0. A gateway restart sends every conversation back to 0.

**5. Subagents.** The model delegates a research task to a subagent, which consults the committee twice. Those two calls count against the parent conversation: the parent now has 2 fewer left.

**6. A long refactoring session with `-1`.** After 12 answered calls the last result carries:

```json
"budget": {"used": 12, "cap": null, "scope": "conversation"},
"note": "... Consultations left this conversation: unlimited."
```

and `/committee status` shows `Budget: 12 used in this conversation (no cap)`.

**7. Switching the tool off for one profile.** Set the value to `0` on that profile. The plugin stays installed and registered, every call is refused, and other profiles are unaffected. Set it back to turn it on again.

**8. Choosing per tier.** A profile on tier High (consults on most expert questions) gets 8; Medium keeps the default 5; Low (only before irreversible actions or after repeated failures) gets 3. Pair High with cheap advisors.
