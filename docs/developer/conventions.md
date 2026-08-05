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
- **Docstrings carry a budget: ≤3 lines, stating the one invariant,
  constraint, or caller obligation the signature cannot.** One that restates
  the name gets deleted, not shortened. **Past tense is a smell** — "used
  to", "its predecessor", a date: a docstring describes what the code *is*;
  how it got that way is git's job (commit message, `CHANGELOG.md`), and
  architecture facts belong in the layer's CLAUDE.md or `docs/`.
  `__init__.py` gets a one-line namespace marker plus a pointer, never a
  package essay. Three named carve-outs are **product surfaces with their
  own budget**, not documentation: the optimizer response models in
  `dispatch/schemas.py` (class docstring → JSON-Schema `description` → the
  LLM prompt; editing one is a prompt change — regenerate via
  `scripts/build_optimizer_schemas.py`), the `EXPORTED_MODELS` docstring
  FIRST lines (→ generated TS JSDoc — regenerate via
  `scripts/build_ts_types.py`), and FastAPI route docstrings (→ the OpenAPI
  descriptions the docs UI serves).
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
- **Schema field order IS generation order.** A response model's fields are
  emitted left-to-right, each becoming context for the next; a `description=`
  is prompt, not documentation (root `CLAUDE.md` forbids trimming them).
  Put reasoning/evidence fields *above* the fields they justify — below, they
  are structurally post-hoc. Which levers are free and which are wire contract:
  `docs/concepts/structured-output.md`.
- **A parameter that changes what a number MEANS takes no default.** Make it a
  required keyword; the signature is the enforcement (a caller that omits it fails
  typecheck, so there is no standing test to keep). The bug class: the decision then
  lives in an *absent* argument, and reading the call site tells you nothing — you must
  notice the absence, jump to a distant default, and find a docstring clause naming the
  intended callers. `score_search_point` / `compute_composite_fitness` take `opt_sp` this
  way, having spent time on exactly that three-hop trail; the same function's per-sample
  callbacks were already required for the weaker reason of display honesty.
  A default is fine when it is a *derivation* every caller would repeat identically
  (`round_scorer=None` → the schema's own default formula), not when the right value
  genuinely differs per call site.
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
- **Hand-written work carries `Hand-authored-by: operator`** in the trailer block. Provenance is metadata, not area, so it never takes the `type(scope)` slot — that keeps saying *where*. Grep it with `git log --grep='Hand-authored-by'`. Two pre-convention commits marked it in the subject instead (`docs: manual edit…`, `docs: maunal pass`); don't copy that — `manual` collides with `docs/manual/`, with the `docs(manual,…)` area scope, and with prose about the install manual, so it cannot be searched for.

## Reasoning doctrine

Four situational guardrails against recurring AI blind spots — unlike the
universal gates in root `CLAUDE.md` (see the top of this page), each fires
only in the specific situation named before it.

**When the operator bounds any budget axis → `<one-budget>`:**

<one-budget>
**The AI blind spot this guards against:** told "this must not exceed X", an AI treats every axis it was *not* handed a number for as free, and proposes an increase there — priced in the cheap axis and presented as costless. A run capped by patience came back with "only ~$0.50 more"; a per-cell measurement came back as "only ~12 extra calls, +3%". Both are budget increases the operator never agreed to, wearing the units they care least about.

**A limit stated on ONE axis binds ALL of them by default — wall-clock, dollars, tokens, calls, rounds, cells, samples — and the implication runs in every direction.** "Don't spend more" bounds the clock; "we don't have five hours" bounds the dollars. This is the ground assumption, not a reading to be argued out of, and it does not need restating per request.

So: **price a proposal in the axis the operator named AND in the ones they didn't**, in the same breath — the units they'd feel, not the flattering ones (an inner cell costs ~11 minutes *and* ~$0.05; quote both). Trading one axis for another is an increase and is asked for explicitly. And when the budget genuinely binds, the move is to get more out of the measurements already being paid for — a better estimator, a signal already recorded and thrown away, a larger effect to detect — never a bigger N in whichever unit currently looks cheap.
</one-budget>

**When an LLM call is slow / costly / token-heavy → `<simplify-the-problem>`:**

<simplify-the-problem>
When an LLM call is slow, costly, or timeout-prone because it emits a large number of tokens, treat the token volume as a **prompt-quality symptom, not a capacity problem**. A tightly-scoped prompt poses a *simpler* problem, so the model reasons less and answers shorter. The first move is to tighten the input — constrain the ask, cut open-ended or redundant injections (don't re-dump raw evidence a downstream node was already handed pre-digested), bound the output shape, lower reasoning effort to match the real difficulty — **not** to reach for a faster/bigger provider or raise the timeout/token cap. Simplify the problem so the model doesn't *need* the tokens; the deadline, the token cap, and the provider are safety rails, not the fix. (A specialization of <root-fix>: the cause is upstream in how we posed the problem.)
</simplify-the-problem>

**When simplifying / labelling a change "refactor" / doing LOC work → `<surface-ledger>`:**

<surface-ledger>
**The AI blind spot this guards against:** told to "simplify", an AI reaches for *additive-but-safe* moves — extract a helper, fold two copies into a `shared/` util, split a big file — each of which adds a module + an import line per call site, so the **total grows** while every commit says "refactor". The genuinely shrinking moves (delete a mechanism, re-inline a single-use module, drop a dead knob) are riskier, so they get skipped. Four rules counter the drift:

1. **Lower the ledger.** Run `python -m promptpotter.diagnostics`. A pass *labelled* simplification/unification MUST move the total **down**. A pass that raises it isn't blocked — it just isn't a "refactor": justify it as a feature or as a shape that makes the codebase quicker to develop, edit the baseline up, and write the reason there. `tests/test_complexity_ledger.py` is where that reason lands; its log of prior raises is the precedent.
2. **Subtract a concept, don't relocate one.** Every simplification commit removes ≥1 *named* thing (module, class, public symbol, config field, code path). Moving code between files counts as zero.
3. **Extraction threshold.** Default: a shared helper earns its place at **≥3 call sites**, or when it removes a concept; at ≤2 callers inline is usually right. A default, not a bar — extract below it when the shared thing is an invariant callers must not diverge from, and say that's why. (The subtractive counterpart to the pre-flight "Reuse before adding" gate.)
4. **Lock the wins.** When a deletion lowers a dimension, lower the baseline in the same commit so it can't drift back. The baseline records where the surface stands — it isn't a target to reach and halt at. When no dimension can fall further without losing a load-bearing concept, the unification *phase* is done; that says nothing about whether the next change may add.
</surface-ledger>

**When you changed what the engine *decides* (a gate, metric, or state), or added a capability at any entry point → `<entry-point-parity>`:**

<entry-point-parity>
**The AI blind spot this guards against:** an AI declares a task *done* the moment the engine logic is correct and the tests are green — it stops reasoning at the layer it edited and leaves every caller-facing half as a silent "later". Engine-correct is not product-complete. **There are four ways in — the CLI, an AI caller (the `/potter-run` skill), the REST API, and the webapp — and a capability that reaches only the one you happened to be editing is half-built.** This project is whitelabeled and user-facing; the forgotten surface is always the one sitting a layer past where the change was made, which is usually the webapp. Two rules:

1. **Parity is part of done.** When you change what the engine *decides* (a gate, a metric, a state), you owe every entry point that could ask for it a legible surface in the same breath — or, if it can't land now, you **write it down as planned** (spec + memory) rather than leaving it unstated. "Done" includes: can the human who relies on this *see* it from where they actually work, and is it *user-friendly*? If not, the work is half-built. Hold UX as a first-class axis, not a footnote.
2. **Teach, don't dump — and never force jargon.** A new internal value (a θ, a new statistic, a new mode) reaches the operator *taught*: a plain-language explainer, riding an **existing** surfacing channel (the lens/formula seam, not a new toggle), and **operator-selectable** so it is never forced on someone who doesn't speak that vocabulary. The engine may *decide* on the expert metric; the human *reads* the metric they chose. Teach from **one corpus** that serves the operator and the next AI reader alike — don't fork the prose.
</entry-point-parity>
