# Prompt audit: violations and the restructure that removes them

## Context

The operator asked which parts of this repo violate Anthropic's prompt-audit guide
(https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/prompt-audit.md).
Three read-only audits ran, one per surface. 74 findings came back. They are not 74 edits: they
fall out of **six wrong shapes**, and the plan realigns each shape so its findings disappear.

Operator decisions (2026-10-08):
- All three surfaces. Roots, never patches; refactors and on-disk shape included; no backward compatibility.
- Workspace stays loadable through 2026-10-09 (demo). Anything that moves measurement identity or
  invalidates stored state runs from 2026-10-10, with the wipe.
- Live box: read-only is free; anything that pulls, starts, restarts or edits asks.
- The gate gains a path-resolution check for instruction files.

What I verified myself: R1 (read `anthropic.py`, `openai_compat.py:230-520`, `base.py`,
`llm_call.py:287-326`), R2's critique contract (`pipeline.yaml:215-266`, `schemas.py:274-303`,
`optimizer_prompt_text.py:24-45`), the `potter-run` frontmatter, the `potter-box` conflict. Every
other finding is an agent's report and is re-read at its site before it is touched.

Targets: instruction files → Claude Opus 5.5. Application prompts → `openai/gpt-6-luna`,
`qwen/qwen3-8b`, `google/gemini-3-flash-preview` via OpenRouter; no shipped default is a Claude
model, so only the guide's model-agnostic rows were applied there.

---

## The six shapes

### R1 — The request has no type, so each client guesses what the other sends

*Findings: request-code 1–8, 11, 13; prompt 28.*

`LLMClientBase.chat(..., **kwargs)` is the wrongness. Its symptoms:
- `llm_call.py:322-324` always passes `reasoning_effort=` and `seed=`; `anthropic.py:120` refuses on
  key presence. **Every optimizer node with `provider: anthropic` dies before send**, while
  `potter/pipeline.yaml:33` offers it.
- `anthropic.py:37` derives what it cannot send from the *other* client's param set.
- `llm_call.py:289-292` passes `route_order` "only when set" because a client might forward it.
- `capabilities.py:196` computes `unsupported_params`; no client reads it.
- `anthropic.py:104-116` never sends the schema, so field order and descriptions (the levers the
  schema axis optimizes) reach no model there. `openai_compat.py:328` sends `strict: False` on
  every route, so the repair ladder is needed everywhere.

Realignment:
1. `infrastructure/llm/request.py`: one frozen `ChatRequest` (messages, model, temperature,
   max_tokens, schema, reasoning_effort, top_p, seed, route_order, strict_schema). `chat(request, *, label)`
   on the base; `**kwargs` and both per-client signatures are deleted.
2. Each client declares `SENDS: frozenset[str]` over `ChatRequest` fields. The base refuses a
   **non-None** field outside it, once, before admission. `PROVIDER_REQUEST_PARAMS` becomes the
   OpenAI-compat client's `SENDS`; `_UNSENDABLE_HERE` and the route-kwargs dance are deleted.
3. `AnthropicClient` sends the schema natively and maps `reasoning_effort` to its effort field;
   `_schema_warned` and the 8192 comment go. **Per-model claims (structured-output syntax, which
   sampling params a given Claude model accepts, effort values) are unconfirmed** — load the
   `claude-api` skill's model-migration reference at this step and take them from there.
4. `strict_schema` is resolved upstream from the catalogue's `supported_parameters`
   (`capabilities.py`, alongside `unsupported_params`) and carried on the request. The repair
   ladder stays as the path for routes without it; it stops being the only path.
5. Callers: `llm_call.py::_provider_reply`, `judges/call.py:222`, `probe_reasoning.py:67` (drops its
   `type: ignore`), `paper_templates.py:84`.

Steps 1, 2, 5 change no wire bytes (pre-demo safe; `hash_call` unchanged — assert it). Steps 3, 4
change what the model receives → from 2026-10-10.

### R2 — A field's contract is stated three times and enforced by a string parse

*Findings: prompt 1, 2, 4, 5, 6, 7, 8, 9, 17, 22, 24, 27; request-code 12.*

`l1_critique` states its output contract in the instruction prose, again in the `answer_format`
skeleton, again in `Field(description=)`, then `BeforeValidator(_truncate…)` clips what the model
could not count and `_priority_fix_axis` recovers an axis from a string with `partition(":")`.
`schemas.py:278` admits it: "the two must stay in lock-step". They have not: the prose says the
axis "MUST be EXACTLY one of" six fields while `critique_axes()` accepts nodes and params and
`panels.py:121` tells the node to name a dead node there.

Realignment — **the wire schema is the only owner of a field's contract**:
- Value spaces become types. `priority_fix` → `{axis, change}` with `axis` an enum grafted at
  runtime from `critique_axes(schema)` (same mechanism `l1_wire_schema.py` uses for the L1 enum).
  `_priority_fix_axis` and the "an axis you invent is dropped downstream" prose are deleted.
  `CheckinOutput` confidence / next_action become enums (`task_context.py:89, 116`).
  `judges/grounding.py` returns an A/B/C enum; its last-letter regex is deleted.
- Caps become `maxLength` / `maxItems` on the item; no prompt asks a model to count characters.
- Every wire field carries a description (five ship bare today). Descriptions say what the slot
  holds, including the L4 case (`schemas.py:157-172`); strategy leaves them for the instruction.
- `answer_format` keeps only what a schema cannot say (field order rationale). The
  `&l1_critique_answer` alias is deleted, so `l1_critique_self_optimizing` cannot inherit a
  skeleton its own instruction contradicts.
- Rules a validator already enforces are stated once, plainly (`pipeline.yaml:385-395` follows
  the `:203-205` form).

Nothing here trims `Field(description=)`, `l1_signal_catalogue` or a value space; descriptions grow.
Regenerate `resolved_schemas.json` via `scripts/build_optimizer_schemas.py`. Moves identity → 2026-10-10.

### R3 — The prompt is asked for what the dispatch already knows

*Findings: prompt 11, 12, 14; request-code 9.*

- `pipeline.yaml:186`: the model derives "parent misses over a third" from thinned panels. Dispatch
  computes it and renders a state line in `escalation_panel`; the template reads the flag.
- `layer_state.py:304-310`: rewritten as the stated fact plus the action, without asserting a
  cause `panels.py:120` gives only as an example.
- `panels.py:1191`: the "prefer one large edit" clause contradicts `pipeline.yaml:171-175`; removed.
- `budget_state` in context (`panels.py:1310`): deliberate per the potter contract. **Flag** —
  decided by an ablation inside the R5 measurement, not by this plan.

### R4 — Instruction files restate what the tree already says

*Findings: config 1, 3–11, 13, 24, 25, 27.*

Counts and rosters in prose rot: "Three shapes" over four bullets, "Five surfaces" over six rows,
directory lists naming `forms/`, `verify/`, `tree/`, a `ci.yml` grep step and a notebook function
that do not exist.

- Delete count words and enumerations wherever the listing is the roster (root `CLAUDE.md:154`
  already states the rule). Root's verb list and skills line point at their owners.
- Correct the path claims that remain (`RunMasthead`, `.promptpotter/projects/{tenant}/…`).
- **Gate:** `scripts/gate.py::_claude_md_size` becomes one `instruction-files` check — word cap plus
  every backticked repo-relative path resolves — over `**/CLAUDE.md` and `.claude/skills/**`.
  Gitignored roots (`.scratch/`, `.promptpotter/`, `logs/`) and `{placeholder}` paths are exempt.
  Run `complexity_ledger`; record the baseline move and its reason.
- `potter-run/SKILL.md:3`: the unquoted `one line: bug-hunting` breaks the frontmatter, so routing
  sees the H1 instead of the description and may drop `model: opus`. Fold the scalar (`>-`).

### R5 — Two files rule on one thing

*Findings: config 2, 12, 14–16, 20, 22, 23.*

- **anti-rot vs `code-debt-cleanup.md`:** the debt file's rule holds (blocked or multi-arc only,
  everything else fixed in the pass). The skill's Ready/Standing buckets and its filing step are
  rewritten to it.
- **potter-box vs root:** per the operator, read-only is free. `open-termnorm.sh` splits: opening
  the window and reading never ask; the pull-and-start half sits behind the ask. Root `CLAUDE.md:78`
  says "anything that changes the live box".
- **potter-self vs `l4-outer-loop.md`:** status, dated measurements and on-disk counts move to the
  spec (which root assigns "what is true"); the skill keeps procedure and says "count them".
- Incident narratives (`potter-anti-rot:160-166`, `potter-self:77-82`, `potter-run:190-192`) reduce
  to the rule they justify.
- TermNorm checkout: one location, relative, in `connectors/CLAUDE.md`; `onboarding.md` points at it.

### R6 — Wording written against a habit, not for a reader

*Findings: prompt 3, 10, 13, 15, 16, 18, 20, 21, 23, 25, 26; config 17–19, 26, 28, 29.*

One pass per file, no mechanism: migration-relative "now / today / THIS PHASE", grader vocabulary
(`l2_targets_l1_surface`, "maximally dirty"), a maintainer note sent to the model
(`checkin/pipeline.yaml:66` → YAML comment), the `thinking_style` example its own invariant forbids
(`checkin/pipeline.yaml:50`), emphasis that carries no information, the `potter-run` reply cap
restated as audience framing.

**Report only, no edit:** prompt 19 (`THINK FIRST`), 29 (benchmark seed prompts — the optimizer
mutates them); config 21's trigger wording, 30; request-code 10, 14, 15 (third-party harness params).

**Clean, stays as is:** judges' rubrics, capo/gepa/levi templates, `l1_signal_catalogue`, cache
ordering, single-shot call architecture, spend accounting, nearly all reasoned emphasis in the
`CLAUDE.md` tree.

---

## Where it runs: cloud sessions, on pushed branches

The operator's local subscription tokens are spent; the work runs in cloud sessions against the
$250 cloud credit, which doubles as a dollar measurement of this kind of task. A cloud session sees
only what is pushed, so the handoff is the first step.

State of the tree (reported by the "Accumulated background tasks" session, 2026-10-08):
- `main` is pushed (`origin/main` = `166597f9b`); that session keeps committing to it in areas
  off this plan's list, and the ledger baseline moves with each pass.
- A third session's decision-bank WIP is loose and **stays uncommitted** by the operator's word.
  It touches root `CLAUDE.md` (one verb-list line) and `tests/test_complexity_ledger.py`.
- That session holds off `infrastructure/llm/`, `potter/dispatch/`, both `pipeline.yaml`,
  `resolved_schemas.json`, `gate.py`'s instruction check, `llm_call.py` and `judges/` until told
  the branch is merged.

Handoff (each push is the operator's go, asked at that moment):
1. `main` pushed — done.
2. Branch `prompt-audit` from `origin/main`, in its own git worktree so the shared tree never
   switches branch under the other sessions. Commit one file: this plan as
   `docs/specs/prompt-audit-restructure.md` (`.claude/` is gitignored; the guide is public and
   read from its URL). It is a handoff file: the last commit on the branch deletes it. Push the branch.
3. Note the cloud usage balance, then start **cloud session A** on `prompt-audit` with the brief below.
4. Note the balance again when it reports. The difference is the measurement.

Cloud session A — brief:
> Read `CLAUDE.md`, then `docs/specs/prompt-audit-restructure.md`. Do the "Now, cloud A" row of its
> sequence: R4, R5, R6 for instruction files, and R1 steps 1, 2 and 5. Re-read every cited site
> before editing; the findings are another agent's report. Leave the root `CLAUDE.md` verb-list
> line and the potter-box script's behaviour on the box untested and say so. Set up with
> `pip install -e ".[all,dev]"`; close on `python scripts/gate.py --py` and the plan's R1 checks.
> Commit to this branch in few commits, the last one deleting the plan file; do not touch `main`.
> Report what you could not verify.

Cloud session B (optional, same measurement idea) — branch `prompt-audit-prompts` off A: write R1
steps 3–4, R2, R3 and R6 for prompts as one revision, plus regenerated `resolved_schemas.json`.
Its output is a diff for review. It is **not merged before 2026-10-10** and not measured in the cloud.

Stays local, whatever the token state: the `seed-screen` (needs the archive and the OpenRouter
key), the wipe, anything on the live box, and the merges.

## Sequence

| When | Where | What | Why then |
|---|---|---|---|
| Now | cloud A | R4, R5, R6-config; R1 steps 1, 2, 5 | No wire bytes move; workspace stays loadable for the demo |
| Now | cloud B | R1 steps 3–4, R2, R3, R6-prompt, as a reviewable diff | Writing it costs no identity; merging it does |
| After A reports | local | Merge `prompt-audit`; re-run `complexity_ledger` to settle the baseline against the loose WIP; lift the other session's freeze | The ledger file is the one shared edit |
| From 2026-10-10 | local | Review B's diff, merge, wipe cycle state (keep `SHARED_CACHE_DIRS`), re-bank the origin | Moves measurement identity, voids banked outer origins. Count first: `ls .promptpotter/projects/*/campaigns` |
| Then | local | One `seed-screen` of the revision against the current templates, `budget_state` as an ablation arm | Under the daily cap |

## Verification

- `scripts/gate.py --only instruction-files` green; it fails when a path in a `CLAUDE.md` is broken on purpose.
- `/potter-run`'s description in the session skill list is the frontmatter text, not the H1.
- R1: a unit in `tests/` builds a `ChatRequest` with `provider: anthropic` and reaches the send
  (today it raises); a non-None field outside `SENDS` raises before admission; `hash_call` for an
  existing OpenRouter request is byte-identical before and after steps 1, 2, 5.
- R2: `resolved_schemas.json` regenerated; no field without a description; `_priority_fix_axis` and
  the grounding regex have no remaining reference (`Grep`); `l1_behavior.py`'s
  `HELD_PROMPT_FIELD_MARK` and `_citation_in_prompt` matches still pass.
- `scripts/gate.py` once at the end of each half; `complexity_ledger` baseline edited with its reason.
- The revision's `seed-screen` read on `rounds_to_separable`, not on `improved`.
