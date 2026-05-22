# Dispatch prompt budget — the multi-modal self-healing unit

> Status: implemented 2026-05-20; caps re-derived against the distilled
> meta-prompts + a soft size warning + an optimizer-call deadline added
> the same day. Bucket (pre-flight §0): **dispatch** (+ a sliver of
> central-loop: three typed `StopReason`s).

## Why this is the tier-2 healing unit

The four wounds ([`self-healing-internals.md`](../developer/self-healing-internals.md))
are each *one* producer→nurse channel: a failure is detected, recorded on
`OptSearchPoint`, rendered into a nurse layer's prompt, healed by that
layer. One detector, one record, one nurse. Vanilla and uniform — that
uniformity is the point.

The prompt-budget unit is different in kind. It guards a single concern —
the size of a composed optimizer meta-prompt — with **four healing modes
stacked on one another**, escalating from silent to loud:

1. **Truncate** — a per-injection `char_cap`; an LLM-authored block over
   its cap is cut + warned.
2. **Shed** — the aggregate allocator; whole low-priority injections are
   dropped when the composed prompt exceeds budget.
3. **L2 self-heal** — L2 *sees* every cap and the live size of any
   overrun (`prompt_budget_status`) and trims the blocks it authors.
4. **Halt** — two distinct, operator-recoverable stops when nothing above
   resolved it: a renderer that *raised* (`RENDER_ERROR`) or a prompt
   that still won't fit (`PROMPT_BUDGET`).

Modes 1–2 are mechanical and deterministic. Mode 3 is LLM-routed (the
only place an LLM touches prompt size, and only for the blocks it
authored). Mode 4 is the escape hatch. A wound has none of this layering;
this unit is where mechanical healing, LLM-routed healing, and a graceful
halt meet on one concern. That is what "tier 2" means here.

## Problem

Optimizer meta-prompts (`l1_generate`, `l1_critique`, `l2_context`,
`l3_plan`, `checkin`) are composed by `DispatchHub` from a static
template body plus N injection renderers. On loaded mid-campaign rounds
the injection set grows large enough to push the rendered prompt past
~13k chars. Large meta-prompts cost latency, risk provider TPM caps, and
dilute signal for the optimizer LLM.

The `l1_generate` instruction-bloat incident (a 16,968-char static
`instruction` field of stale AIME lore) is already fixed by rewrite.
This unit addresses the *aggregate* — the sum of many individually
`*_RENDER_CAP`-bounded injections, which nothing previously bounded.

## Mode 1 — per-injection character caps (truncate)

The first line is a per-injection char cap on every **LLM-authored**
injection — `_Injection.char_cap`, enforced in `DispatchHub.render`:

| injection | author | `char_cap` |
|---|---|---|
| `rendered_prompt` | L1 (the prompt being optimized) | 2,500 |
| `plan` | L3 | 800 |
| `critique` | L1_CRITIQUE | 800 |
| `l1_situational_examples` | L2 | 1,000 |
| `l1_supplemental_rules` | L2 | 1,000 |
| `l3_to_l2_note` | L3 | 400 |
| `task_context` | L2 | per-field `TASK_CONTEXT_VALUE_CAP` (300) |

Every cap was re-derived on 2026-05-20 against the distilled `l1_generate`
floor — the allocator is the last resort, so each cap is the tightest
value that still carries its block's signal. An injection that renders
over its cap is **truncated + warned** — the
authoring LLM ignored its output budget (an LLM mistake → self-heal, not a
crash). Truncation is acceptable here because the content is LLM prose.
Derived / measurement injections pass `char_cap=None` — their
`*_RENDER_CAP` row limits already bound them. `char_cap` has no default: a
new injection that omits it is a `TypeError` (coding mistake → hard error).

## Mode 2 — the budget allocator (shed)

One place, because `fill_l1` and `fill_fixed` are the single composition
path for all five optimizer prompts. After rendering every injection for a
template, if `static_body + Σ injections > BUDGET`, the hub **sheds**
injections — drops them whole, lowest-priority first — until the total
fits. Mandatory injections are never dropped.

### Priority tiers

Each `_Injection` carries a `tier` (`InjectionTier` enum):

| Tier | Drops | Members |
|---|---|---|
| `MANDATORY` | never | `rendered_prompt`, `pipeline_param_catalogue`, `plan`, `task_context`, `critique`, `prompt_budget_status` |
| `CORE` | last | `diagnostics`, `validation_failures`, `runtime_failures`, `l2_guard_breaches`, `l3_guard_breaches` — this round's failure evidence |
| `OPTIONAL` | first | `axis_memory`, `archive_top_runs`, `rare_hit_samples`, `intractable_samples`, `origin_strengths`, `l1_supplemental_rules`, `l1_situational_examples`, `l1_signal_catalogue`, `l1_overrides`, `l3_to_l2_note` |

`L1_MANDATORY` (`rendered_prompt`, `pipeline_param_catalogue`, `plan`,
`task_context`, `critique`) must be tier `MANDATORY` — an import-time
check raises `RuntimeError` otherwise. `prompt_budget_status` is
`MANDATORY` too (never shed the block that tells L2 how to heal) without
being in `L1_MANDATORY` — it is L2-only.

A tier is a global property of the injection — it applies wherever the
injection is mounted; for a template that doesn't reference it, it is
simply moot.

### Algorithm — `_apply_budget(static_chars, rendered)`

1. Render all injections (empty ones already skipped — unchanged).
2. `total = static_body + Σ rendered`.
3. While `total > BUDGET` and a droppable injection remains: drop the
   **largest** rendered injection in the lowest non-empty tier
   (`OPTIONAL` before `CORE`); never `MANDATORY`.
4. If only `MANDATORY` remains and still over budget → **Mode 4
   budget halt** (`raise StopLoop(StopReason.PROMPT_BUDGET)`). The
   residual is content L2 cannot heal; the loop stops for operator review.
5. Log every shed at `logger.info` so the operator sees what the
   optimizer didn't.

Drop-whole, not truncate: a half-truncated `axis_memory` is worse than
its absence — the optimizer reads partial data as if it were complete.

## Mode 3 — L2 sees the caps and heals what it authors

The allocator is **mechanical** and stays that way — choosing which
injections to shed is deterministic allocation, not reasoning; an LLM adds
no judgement, and asking an already-loaded L2 to manage its own context
budget compounds the problem. L2 never runs the allocator.

But L2 *authors* three of the capped blocks — `task_context`,
`l1_supplemental_rules`, `l1_situational_examples` — and was previously
blind to their caps. The fix is **feedback, not allocator-management**:
the `prompt_budget_status` injection (DERIVED, `MANDATORY` tier, mounted
only in the `l2_context` template) shows L2

- the composed-prompt budget,
- every char-capped injection — `name | author | actual / cap`, flagged
  `OVER by N` when the raw (pre-truncation) render exceeds the cap; actual
  size is the injection's own renderer invoked and measured,
- `task_context` per-field actual-vs-`TASK_CONTEXT_VALUE_CAP`,
- split into **YOURS** (L2-authored — heal these) and **OTHER LAYERS**
  (flagged but not L2's to edit).

`l2_context`'s instruction points L2 at the block: trim any **YOURS**
entry flagged `OVER` — shorten the text or author fewer items. Overruns
L2 doesn't author and the aggregate self-heal via Modes 1–2; a prompt
that still won't fit is the Mode 4 halt. L1/L3/critique are not shown the
full block — they keep a one-line writer-budget note (see *Writer-stated
output limits*).

This is `feedback_layer_writer_vocabulary_rule` applied to size, taken to
its conclusion: the layer that writes a capped field doesn't just know a
cap exists — it sees the cap *and the current violation*.

## Mode 4 — the two halts

Both are graceful, operator-recoverable, and ride the existing round-loop
teardown. Neither is a crash.

### Budget-unhealable halt — `StopReason.PROMPT_BUDGET`

Raised by `_apply_budget` step 4: the composed prompt is still over
`OPTIMIZER_PROMPT_CHAR_BUDGET` after every `OPTIONAL` and `CORE`
injection has been shed. The residual is the static template + the parent
prompt + the other `MANDATORY` injections — none authored by L2, so L2
cannot heal it. `_apply_budget` logs the residual breakdown at
`logger.error` then `raise StopLoop(StopReason.PROMPT_BUDGET)`; the round
loop's existing `except StopLoop` returns the reason.

Recovery: the operator compacts the parent prompt (`resume --from N`
rewind, or a fork) or trims the static meta-prompt template, then
`resume`. This is the genuine "the prompt being optimized has grown too
large" signal — previously a `logger.warning` that ploughed on with an
oversized prompt; now a stop so a human actually acts.

### Render-error halt — `StopReason.RENDER_ERROR`

A renderer that *raises* (vs. returning oversized text) is code drift — a
renderer that diverged from the data model (`_r_diagnostics` raising
`AttributeError` because a `RoundDiagnostics` field was renamed). It is a
programmer mistake surfacing at runtime, not an LLM mistake.

`DispatchHub.render` wraps the renderer call; on any exception it
re-raises as `InjectionRenderError(name, cause)`. `runner/loop.py`
catches `InjectionRenderError` *before* the generic `except Exception` and
returns `StopReason.RENDER_ERROR` — distinct from `CRASHED` so the
operator immediately knows *a renderer broke*, with the failing injection
name + traceback on `index.json::final.crash_traceback`.

Two recovery paths, both on the CLI:

- `resume` — the operator fixed the renderer; the run continues.
- `resume --ignore-render-errors` — proceed without it: a renderer that
  raises renders `""` + warns instead of halting. The flag rides
  `InjectionBundle.ignore_render_errors` (stamped onto the `Cycle` by
  `run_optimization`, copied onto every bundle by `build_bundle`);
  `DispatchHub.render` reads it.

The deliberate middle path between a hard crash (loses the run) and a
silent skip (hides a real bug): the loop pauses so a human notices, then
*they* decide. Derived-block **size** is not in this path — it is already
self-healed by the `*_RENDER_CAP` row limits + the allocator; only a
renderer *raising* triggers the halt.

## Companion rails — soft size warning + optimizer-call deadline

Two reliability rails ship alongside the budget unit on the same
optimizer-call path. Neither is a healing mode; both make a slow or
oversized call visible / bounded rather than silent.

**Soft size warning — `OPTIMIZER_PROMPT_WARN_CHARS = 8,000`.** The hard
`OPTIMIZER_PROMPT_CHAR_BUDGET` allocator only fires when a prompt is
genuinely unhealable. The soft line catches the milder case — a prompt
above the real `l1_generate` floor but under the hard ceiling. A live
`justlogic` run measured a healthy mid-campaign `l1_generate` at ~7.9k
(distilled static body ~3k + a ~4.9k injected half), so 8,000 sits just
above the honest floor — quiet on healthy rounds, loud on genuine bloat.
When
`prompt_chars` exceeds it the CLI `↻ optimizer call` marker turns yellow
with a `⚠` (`presentation/views/live/display.py`) and the matching
`→ optimizer call` log line goes `logger.warning`
(`dispatch/llm_call.py`). Visibility only — the call still fires; the
cue is "distil the node template or re-tune its caps."

**Optimizer-call deadline — `OPTIMIZER_CALL_DEADLINE_S = 180s`,
`StopReason.OPTIMIZER_TIMEOUT`.** The provider SDK's `timeout` is a
per-read-gap timeout, not a total one: a reasoning model streaming a
large output slowly never trips it and the call can hang indefinitely (a
live `l1_generate` sat 315s+ with no response). `_chat_under_deadline`
(`dispatch/llm_call.py`) wraps each `llm_client.chat()` attempt in
`asyncio.timeout(OPTIMIZER_CALL_DEADLINE_S)` — a hard wall-clock bound
the SDK's timeout is not. A first timeout is retried once (transient
provider hiccup); a second raises `TimeoutError`, which `runner/loop.py`
catches before the generic `except Exception` and returns
`StopReason.OPTIMIZER_TIMEOUT` — the third operator-recoverable graceful
halt alongside `RENDER_ERROR` / `PROMPT_BUDGET`. Recovery is a plain
`resume`; no traceback is stashed (the cause is the deadline, already in
the warn-level log). Secondary hardening: every optimizer node config in
`datasets/_optimizer/pipeline.json` carries a bounded `max_tokens`, so a
runaway generation is capped independently of the deadline.

## Writer-stated output limits

Shedding and truncation are the safety nets; the better outcome is the
writer staying within budget so the hub rarely intervenes. Every optimizer
prompt that authors an injected element is told its output rides a
budget-limited downstream prompt:

- `l2_context` — sees the full `prompt_budget_status` block (Mode 3).
- `l3_plan` — the plan rides every downstream prompt; keep it short.
- `l1_generate` — the prompt it builds becomes next round's
  `rendered_prompt`; the additive / minimal-diff rules already bound it.
- `l1_critique` — the critique rides L1's and L2's next prompt; keep
  it compact.

The per-injection `*_RENDER_CAP` constants are kept tight — the allocator
is the last resort, not the first.

## Error policy — three failure sources, three responses

| failure | when | response |
|---|---|---|
| **Coding mistake, load time** — forgot `tier`/`char_cap`, mistiered an `L1_MANDATORY` injection, unknown `{{slot}}` | import / construction | **Hard error.** `TypeError` / `RuntimeError` / `KeyError` before the loop starts — nothing is lost, fix and re-run. |
| **Coding mistake, render time** — a renderer drifts from the data model and raises | mid-loop | **Render-error halt** (Mode 4) — distinct `StopReason.RENDER_ERROR`, traceback on disk, `resume` / `resume --ignore-render-errors`. |
| **LLM mistake** — an authoring LLM overruns its `char_cap`; injections collectively exceed budget | mid-loop | **Self-heal + warn** (Modes 1–3). Truncate over-cap injections; shed whole low-tier injections; show L2 its overruns. Deterministic; every heal logs. If still unhealable → budget halt (Mode 4). |

The principle the operator set: a mistake *I* (the programmer) make
should be a loud error; a mistake the LLM makes during generation should
self-heal or warn. Mode 4's two halts are where an unhealable LLM/state
condition is escalated back to a human deliberately, rather than guessed.

## The mandatory floor — honest constraint

The `MANDATORY` set is irreducible. For a mid-campaign `l1_generate`,
after the 2026-05-20 distillation pass:

- static template body ≈ 3,050 (`instruction` + `answer_format` +
  `persona` / `task_intent`) — down from ≈ 5,600 before distillation
- `rendered_prompt` (the parent prompt, grows additively, capped 2,500)
- `pipeline_param_catalogue` ≈ 400; `task_context` ≤ 1,500 (5 × 300);
  `critique` 0–800; `plan` 0–800

Floor ≈ **7,000–9,000 chars**. The allocator holds the *total* at
`BUDGET` by shedding `OPTIONAL`/`CORE`; it cannot push below the floor —
and when the floor itself exceeds `BUDGET`, that is exactly the Mode 4
budget halt firing as designed.

`OPTIMIZER_PROMPT_CHAR_BUDGET = 10_000` — above the typical floor with
shed headroom; the soft `OPTIMIZER_PROMPT_WARN_CHARS` line (8,000) flags
an oversized prompt before this hard ceiling.

## Code changes

- `dispatch/hub/bundle.py` — `InjectionTier` enum; required `tier` +
  `char_cap` on `_Injection`; `OPTIMIZER_PROMPT_CHAR_BUDGET` +
  `TASK_CONTEXT_VALUE_CAP`; `InjectionBundle.ignore_render_errors`.
- `dispatch/hub/injections.py` — `tier` + `char_cap` on every
  `INJECTIONS` entry; the `prompt_budget_status` injection +
  `_r_prompt_budget_status` + `_INJECTION_AUTHOR`; import-time
  `L1_MANDATORY ⊆ MANDATORY-tier` check; `_r_task_context` per-field
  truncation.
- `dispatch/hub/facade.py` — `InjectionRenderError`; `DispatchHub.render`
  enforces `char_cap` and honours `ignore_render_errors`; `_apply_budget`
  sheds and raises `StopLoop(PROMPT_BUDGET)` when unhealable.
- `dispatch/hub/builder.py` — `build_bundle` stamps `ignore_render_errors`.
- `dispatch/llm_call.py` — `_chat_under_deadline` (`asyncio.timeout`
  wrapper + retry-once); warn-level oversized-prompt log line.
- `config/settings.py` — `OPTIMIZER_CALL_DEADLINE_S`,
  `OPTIMIZER_PROMPT_WARN_CHARS`.
- `domain/phases.py` — `StopReason.RENDER_ERROR` + `PROMPT_BUDGET` +
  `OPTIMIZER_TIMEOUT`.
- `application/optimization/cycle.py` — `Cycle.ignore_render_errors`.
- `runner/loop.py` — `except InjectionRenderError` → `RENDER_ERROR`;
  `except TimeoutError` → `OPTIMIZER_TIMEOUT`.
- `runner/entry.py` — `run_optimization` `ignore_render_errors` param;
  `_finalize_run` handles all three new stop reasons.
- `presentation/cli/parsers.py` + `commands/resume.py` —
  `resume --ignore-render-errors`.
- `presentation/views/live/display.py` — yellow `⚠` marker for an
  oversized optimizer prompt.
- `datasets/_optimizer/pipeline.json` — `l2_context` gains the
  `{{prompt_budget_status}}` placeholder + a heal-directive sentence;
  distilled `l1_generate` / `l2_context` / `l1_critique` resolved_prompts;
  bounded `max_tokens` on every optimizer node.

Pre-flight: bucket **dispatch**; rides the existing hub + `INJECTIONS`
registry; no new file, no new I/O kind, no new LLM call site, no
escalation. The three `StopReason`s ride the existing round-loop
teardown — `PROMPT_BUDGET` via `StopLoop`, `RENDER_ERROR` via
`except InjectionRenderError`, `OPTIMIZER_TIMEOUT` via `except
TimeoutError`. No `docs/architecture.md` §0 change — §0 already names
errors-heal + central-loop, and three typed stop reasons do not change
its shape.

## Non-goals

- Token-exact budgeting (no tokenizer at compose time; chars are the
  proxy).
- Truncating `MANDATORY` content or the parent prompt.
- Any LLM-mediated or escalation-routed *aggregate* size healing — L2
  heals only the blocks it authors (Mode 3); the allocator owns the rest.
