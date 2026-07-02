# Conventions

Rules contributors follow that aren't derivable from reading the code.
Non-negotiables (the no-backward-compat pledge, five I/O kinds, vocabulary
discipline) live in the root [`CLAUDE.md`](../../CLAUDE.md); this page
collects everything else.

## Code style

- **PEP 604** type hints (`X | None`, `list[str]`); never `Optional[X]` /
  `List[str]`.
- **`logging` module only** — no `print()` in `promptpotter/`. Setup via
  `promptpotter/config/logging.py`.
- **Ruff line-length: 100.** Run `ruff check . && ruff format --check .` in
  the CI chain.
- **Direct field access** — `dict[key]` for guaranteed fields, not
  `.get(key, fallback)`. Fallbacks announce uncertainty; if you have a
  contract, lean on it.
- **Comments default to none.** Only non-obvious *why*. Never explain *what*
  the code does — the names already do that.
- **Docstring trimming is out of charter for LOC-shrink work.** Existing
  module/class/function docstrings explain WHY (invariants, contracts,
  hidden constraints) and are user-facing value. Real LOC wins come from
  pattern unification, dead-code removal, inlining single-use helpers,
  fixing god-objects — never from shrinking explainers. If a docstring is
  genuinely an essay restating what the code does, ask first.
- **Pipeline components are nodes** — never "building blocks", never
  "services".

## Code shape

- **No fallbacks in service code.** Two sanctioned exceptions:
  `score_population()` synthetic-0 on `validation_failures`; load-boundary
  deprecated-sample gate (uses `classify_result()` fatal codes). Any new
  fallback must be documented alongside these.
- **Optimizer LLM calls go through `llm_call()`**
  (`application/optimization/dispatch/llm_call/call.py`), never `chat()`.
- **Escalation flows via return value** (`QueryLoopResult.escalation_signal`),
  not exception. Use `graceful()` (`shared/errors.py`) where exceptions must
  escape.
- **String-keyed *call* dispatch is a defect** — it hides the caller→handler
  edge from `grep`, so "is this method live?" costs a multi-hop tour.
  Fix by template: key is internal → explicit `match` with literal calls;
  key is a cross-file contract → registration decorator at the handler's
  definition site (the `@signal` `INJECTIONS` pattern); enum-keyed dict +
  import-time completeness assert is the third acceptable form. String-keyed
  *data* tables are fine.

## Tests

- **Subtractive.** Each guards a named invariant. No volume tests, ≤2–3
  monkeypatches per test. See [`tests/CLAUDE.md`](../../tests/CLAUDE.md).
- **Delete-don't-update.** When a contract is renamed/restructured, delete
  the old test and write a fresh one. Never port assertions.

## CLI / running

- **Timeouts: 30s default for ALL commands.** Increase only when explicitly
  told "ready for data collection".
- **Never run `campaign_runner` with `run_in_background`** — always
  foreground.

## Git

- **Conventional commits** — `feat:`, `fix:`, `docs:`, `refactor:`, etc.
- **Commit messages: hard cap 800 chars** total (incl. trailer); title <70.
  Terse bullets — no motivation essays. Over 800 → rewrite, do not
  commit-and-fix-later.

## Reasoning doctrine

Three situational guardrails against recurring AI blind spots — unlike the
universal gates in root `CLAUDE.md` (see the top of this page), each fires
only in the specific situation named before it.

**When an LLM call is slow / costly / token-heavy → `<simplify-the-problem>`:**

<simplify-the-problem>
When an LLM call is slow, costly, or timeout-prone because it emits a large number of tokens, treat the token volume as a **prompt-quality symptom, not a capacity problem**. A tightly-scoped prompt poses a *simpler* problem, so the model reasons less and answers shorter. The first move is to tighten the input — constrain the ask, cut open-ended or redundant injections (don't re-dump raw evidence a downstream node was already handed pre-digested), bound the output shape, lower reasoning effort to match the real difficulty — **not** to reach for a faster/bigger provider or raise the timeout/token cap. Simplify the problem so the model doesn't *need* the tokens; the deadline, the token cap, and the provider are safety rails, not the fix. (A specialization of <root-fix>: the cause is upstream in how we posed the problem.)
</simplify-the-problem>

**When simplifying / labelling a change "refactor" / doing LOC work → `<surface-ledger>`:**

<surface-ledger>
**The AI blind spot this guards against:** told to "simplify", an AI reaches for *additive-but-safe* moves — extract a helper, fold two copies into a `shared/` util, split a big file — each of which adds a module + an import line per call site, so the **total grows** while every commit says "refactor". The genuinely shrinking moves (delete a mechanism, re-inline a single-use module, drop a dead knob) are riskier, so they get skipped. Four rules counter the drift:

1. **Lower the ledger.** Run `python -m promptpotter.diagnostics.complexity_ledger`. A simplification/unification pass MUST move the total **down**; a pass that raises it is a *feature* and needs a feature justification, not a "refactor" label. `tests/test_complexity_ledger.py` ratchets this — it fails CI if any dimension rises.
2. **Subtract a concept, don't relocate one.** Every simplification commit removes ≥1 *named* thing (module, class, public symbol, config field, code path). Moving code between files counts as zero.
3. **Extraction threshold.** A shared helper is justified only at **≥3 call sites** AND when it removes a concept. ≤2 callers → inline. (The subtractive counterpart to the pre-flight "Reuse before adding" gate.)
4. **Finish line.** The ledger baseline in the ratchet test *is* the target. When a deletion lowers it, lower the baseline to lock the win. When no dimension can fall further without losing a load-bearing concept, the unification phase is **done** — stop.
</surface-ledger>

**When you changed what the engine *decides* (a gate, metric, or state) → `<reach-the-operator>`:**

<reach-the-operator>
**The AI blind spot this guards against:** an AI declares a task *done* the moment the engine logic is correct and the tests are green — it stops reasoning at the layer it edited and leaves the operator-facing half (does the webapp *show* this? can the operator *see, understand, and steer* it?) as a silent "later". Engine-correct is not product-complete. This project is whitelabeled and user-facing; **the webapp surface is the most-forgotten half precisely because it sits one layer past where the change was made.** Two rules:

1. **Parity is part of done.** When you change what the engine *decides* (a gate, a metric, a state), you owe the operator a legible surface for it in the same breath — or, if it can't land now, you **write it down as planned** (spec + memory) rather than leaving it unstated. "Done" includes: can the human who relies on this *see* it, and is it *user-friendly*? If not, the work is half-built. Hold UX as a first-class axis, not a footnote.
2. **Teach, don't dump — and never force jargon.** A new internal value (a θ, a new statistic, a new mode) reaches the operator *taught*: a plain-language explainer, riding an **existing** surfacing channel (the lens/formula seam, not a new toggle), and **operator-selectable** so it is never forced on someone who doesn't speak that vocabulary. The engine may *decide* on the expert metric; the human *reads* the metric they chose. Teach from **one corpus** that serves the operator and the next AI reader alike — don't fork the prose.
</reach-the-operator>
