---
name: potter-dev
description: The self-improving PromptPotter dev + investigation playbook Claude owns. Two modes. APPLY — invoke before editing or adding code under `promptpotter/`, or when investigating ("why does X", "where does Y live", "how does Z work", "trace/find …", debugging the loop): loads the imprinted intuition (reuse-first seams, §0 buckets, conventions, per-layer contracts, a where-things-live fact map) plus the learned-rules ledger so you stop re-exploring and stop re-breaking known rules. LEARN — invoke whenever the operator corrects a mistaken behavior ("no", "don't", "you keep doing X", "that's wrong", "I told you", "stop doing", "why did you", "again"): distill the correction into one rule and append it to `rules.md` so it never recurs. Reads and writes `.claude/skills/potter-dev/rules.md`.
---

# potter-dev — the playbook Claude owns

**The ledger is the memory; disk is the truth.** One invocation runs one mode.
This skill exists so the operator stops re-teaching the same lessons: APPLY pulls
everything already imprinted about this codebase into the front of mind before you
touch it; LEARN writes the next lesson down so it sticks. Ten corrections in a row
produce ten durable rules, not ten forgotten conversations.

`rules.md` (sibling file) is the growing ledger. This SKILL.md stays stable — never
append lessons here; they go in `rules.md`.

## Mode select

- The operator just **corrected** a behavior, pointed out a mistake, or expressed
  "you keep doing X" / "I already told you" → **LEARN**.
- You're about to **edit/add code** under `promptpotter/`, or the operator asked you
  to **investigate / trace / explain** something in the codebase → **APPLY**.
- Both at once (a correction that also needs a fix) → fix first, then LEARN.

---

## APPLY mode

**Step 1 — read `rules.md` first.** The learned rules override every default below.
They are the corrections the operator already paid for; do not re-earn them.

**Step 2 — the imprinted intuition (pointers, not copies — depth lives in the docs).**

Pre-flight gate before adding ANY new concept (class, field, injection, prompt,
file): answer the six questions in root `CLAUDE.md`. "I don't know" is a hard block.
The first one carries the most weight: **reuse before adding.**

**Reuse-first seams** — almost everything already has a channel. Ride it; no sidecar:

| Need | Ride this seam | Lives at |
|---|---|---|
| Optimizer state (overrides, memory, wounds, task_context) | `OptSearchPoint` (no sidecar fields anywhere) | `promptpotter/domain/opt_search_point.py` |
| Render something into an L1/L2/L3 prompt | the `INJECTIONS` registry / dispatch hub | `application/optimization/dispatch/hub/injections/` |
| Any optimizer LLM call | `llm_call()` — **never** `chat()` directly | `application/optimization/dispatch/llm_call/call.py` |
| Wrap an LLM call or backend match for telemetry | `observed_node()` (unwrapped = auto-block) | per pre-flight gate |
| Score a searchpoint | `score_search_point()` — the single scoring gateway | `application/optimization/l1/score/` |
| Read/write any artifact | a `Stores` leaf (never raw `write_json`/`os.replace`) | `infrastructure/store/` |
| Persist a decision for resume/replay | the cycle ledger (`events.jsonl`), typed `ResumeCheckpointKind` | `infrastructure/projections/`, `domain/run_records.py` |
| Per-call telemetry from a deep chain | `emit_*` reading the `_CYCLE_LEDGER` ContextVar | `application/` |

**Five I/O buckets (§0).** Every change maps to Persistence, Display, Control-local,
Control-remote, or Identity. Fits none → stop: either §0 is incomplete (amend it in a
separate PR that lands first) or this is the wrong change. New Control-remote
command/event → declare the schema in `docs/specs/m12-*.yaml` *before* the handler.
Full shape: `docs/architecture.md` §0/§0.5.

**Conventions** (full list: `docs/developer/conventions.md`): PEP 604 hints; `logging`
not `print`; **no fallbacks** in service code; direct `dict[key]` not `.get(k, default)`;
"node" not "service"/"building block"; comments only for non-obvious *why*. And the two
non-negotiables from root `CLAUDE.md`: **delete on sight** (shim code, fallback chains,
breadcrumb comments — zero backward compatibility, ever) and **root-fix** (fix the
upstream structural cause, not the symptom site; name the cause before touching the surface).

**Layers** (load only the layer you touch: `promptpotter/*/CLAUDE.md`). Forbidden
runtime imports, locked by `tests/test_structure.py`: domain→anything, intelligence→
optimization, infrastructure→application/intelligence/optimization. A new seam or
invariant → add a **row** to `tests/test_structure.py` (`REGEX_BANS`/`CALL_BANS`), never a
hand-rolled `rglob`/`ast.walk`.

**Step 3 — where things live (fact map; skip the re-exploration).**

| Looking for | Read |
|---|---|
| Per-round scores, candidates, critiques, origin | `{cycle_dir}/rounds/round_NNNN.json` |
| Cycle outcome (authoritative completeness) | `{cycle_dir}/index.json::final.stop_reason ∈ {max_rounds, goal_reached, infinite_stall}` (NOT outer `status`) |
| Decision/escalation history (replay source) | `{cycle_dir}/events.jsonl` (the ledger) |
| Live operator dashboard | `{cycle_dir}/dashboard.json` (round-boundary flush, ≤0.25s — keep it live) |
| Target-side measurements | `archive/measurements/` (dataset-scoped) |
| Optimizer audit (L1 variants, validator outcomes) | `{cycle_dir}/.runtime/cache/rounds/round_NNNN.json` |
| Node tunables (the only knob) | `datasets/{name}/pipeline.json::nodes.{name}.config` overlay |
| Which doc answers a hot question | `docs/CLAUDE.md` anchor table |

When an answer is already in this map or `rules.md`, answer from it — don't re-grep the tree.

---

## LEARN mode

The operator corrected something. Capture it so it never recurs.

1. **Distill to one rule.** What you did wrong, what's right instead, and the root *why*.
   One correction → one rule. Don't bundle unrelated lessons.
2. **Dedup against `rules.md`.** Grep the ledger first. If a rule already covers this
   area, **update it in place** (sharpen the trigger / add the case) — don't append a
   near-duplicate. If the correction *contradicts* an existing rule, delete the old one
   and write the new (no-backcompat applies to the ledger too).
3. **Append** using the `rules.md` block format, add the one-line index entry, and link
   related rules with `[[R-NN]]`. Use the next free `R-NN`.
4. **Confirm in one line** what was recorded (e.g. `Recorded R-23 — origin not "baseline".`).

If the correction is broad life-of-the-project context rather than a how-to-work rule
(who the operator is, a project goal, an external resource), it belongs in auto-memory
(`MEMORY.md`), not here — say so and write it there instead.

---

## `rules.md` ledger format

Top of file: an **Index** — one line per rule (`- [R-01](#r-01) — hook`).
Body: rules grouped by area, each block:

```
### R-NN — short title
- **Trigger:** when this rule applies (the task shape / phrase that should fire it)
- **Rule:** do this, not that
- **Why:** the root reason (so it generalizes, not just the one case)
- **Origin:** YYYY-MM-DD — correction / source
```

## Boundaries

- LEARN writes only to `rules.md` — never silently edit code, configs, or other docs.
- One rule per correction; update-don't-duplicate; delete a contradicted rule.
- `rules.md` owns *how to work in this repo* (conventions, seams, workflow, investigation
  habits) — not domain data, not campaign state, not roadmap direction.
- APPLY reads; it does not gate. It surfaces the relevant rules + seams, then you act.
- Never restate doc depth in this skill — cite the doc. SKILL.md stays thin and stable.

## Relationship to memory

`rules.md` is the **active working ledger** Claude reads before coding/investigating and
writes on every correction — the thing the operator wanted so they stop taking notes.
The auto-memory (`MEMORY.md` + `*.md` files) stays the **broad cross-session recall**
layer (who/what/why of the project). Don't double-write the same fact; if it's a working
rule it lives here, if it's project context it lives in memory.
