# Conventions

Rules contributors follow that aren't derivable from reading the code.
Non-negotiables (the no-backward-compat pledge, five I/O kinds, vocabulary
discipline) live in the root [`CLAUDE.md`](../../CLAUDE.md); this page
collects everything else.

## Code style

- **PEP 604** type hints (`X | None`, `list[str]`); never `Optional[X]` /
  `List[str]`.
- **`logging` in library code; `print()` only where a human is the reader.** Setup
  via `promptpotter/config/logging.py`. A print is an operator-facing OUTPUT, never a
  debug aid, so it belongs to the CLI (`presentation/cli/`), the terminal views, the
  server banner, first-run setup, the interactive origin gate and the maintenance
  verbs (`restamp`, `reindex`, `compact-archive`) — anywhere else it writes to a stream nothing
  captures. Inside the live run readout it is narrower still: every line goes through
  `LiveDisplay._write`, the single stdout funnel that mirrors ANSI-stripped to the
  cycle's `readout.log`, so a bare `print()` there is a line no headless reader can recover.
- **Ruff line-length: 100.** Enforced by `scripts/gate.py`, which is what CI runs.
- **Direct field access** — `dict[key]` for guaranteed fields, not
  `.get(key, fallback)`. Fallbacks announce uncertainty; if you have a
  contract, lean on it.

## Prose in the source

- **A docstring or `#` never restates the code as
  semantic text.** Two gates, both must pass or it gets **deleted, not
  shortened**, and there is no route from a failed docstring to a surviving
  comment. (1) *Non-local* — cover the prose and read the name, signature,
  types, body, rest of file: is the fact still missing, such that a reader
  would have to open **other files** to learn it? Local dies, and that is the
  large majority — what the next line does, `Args:`/`Returns:`, what it raises
  when the `raise` is right there. (2) *Unowned* — a rule binding a **set** of
  symbols is layer documentation by definition: route it to the layer's
  CLAUDE.md or `docs/` and delete it here. What survives is the fact whose
  evidence lives in another subsystem, compressed to **≤2 lines**, present
  tense — a prohibition, a trap, a sentinel's absence semantics, a tiebreak, a
  security asymmetry. **An `__init__.py` gets none at all** — the path already
  names the namespace and the module map is one `ls`. A `#` is for a
  non-obvious *why* **inside** a body, aimed at the next editor.
  **Past tense is a smell** — "used to", "its predecessor", a date, a
  percentage, a run id, an `A -> B` tally: how the code got that way is git's
  job (commit body, `CHANGELOG.md`).
- **The webapp is in scope — `.ts`, `.tsx`, `.css`, and JSX `{/* */}` alike.** Same two gates,
  same ≤2 lines, same past-tense smell; a component header says what the component IS and which
  rule binds it, never what it replaced. The one generated file (`lib/api/types.generated.ts`) is
  not prose — fix its source docstring and regenerate.
- **A `CLAUDE.md` is billed to every session beneath it, so it holds rules binding a set of
  symbols** — mechanism goes to the module docstring, an incident to the commit body, and the
  Recompute Test in [`../CLAUDE.md`](../CLAUDE.md) § Editing a doc applies to these files too. No
  page grows past `scripts/gate.py::_CLAUDE_MD_MAX_WORDS`; one that reaches it is trimmed or split.
- **A cut fact has a DESTINATION, and the ladder is priced by who pays.** A line
  in a hot module is billed to every future session that opens it, needed or
  not, so a fact goes to the cheapest rung that still reaches the reader who
  would get it wrong. (1) **The type or signature** — free, and enforced: a
  required keyword-only arg, an `X | None`, a `Literal` over the whole state
  set, a derived property nobody can omit. **Prose defending against a bug the
  type now prevents is pure cost**, and it is the bulk of what gets written.
  (2) **≤2 lines at the site**, for a trap no type can hold. (3) **The layer's
  `CLAUDE.md`**, for a rule binding a *set* of symbols. (4) **`docs/`**, for
  what a reader goes looking for. (5) **§ Paid corrections**, for a failure
  *shape* that will recur — generalized past the incident, one line. (6) **The
  commit body**, for the incident. The test is one question: *delete this — what
  does a reader now get wrong?* "Nothing, the type stops them" is rung 1, and
  rung 1 means delete.
- **Three carve-outs are product surfaces**, not documentation, because a
  generator reads them: `EXPORTED_MODELS` docstrings
  (→ generated TS JSDoc — regenerate via `scripts/build_ts_types.py`; only a
  CLASS docstring's line 1 ships, so it must be a complete sentence, while a
  `@computed_field` property ships WHOLE), FastAPI route docstrings, and the
  Pydantic/enum **class**
  docstrings that become component-schema descriptions — the last two both
  landing in the OpenAPI the docs UI serves. The optimizer response models in
  `dispatch/schemas.py` are **not** a fourth: Pydantic hoists a class
  docstring into the wire JSON Schema, so `OptimizerResponseModel` strips it
  and an import-time guard keeps it stripped. What ships there is
  `Field(description=)` — editing one IS a prompt change, so regenerate via
  `scripts/build_optimizer_schemas.py`.

## Naming

- **A filler name whose PACKAGE PATH resolves it is not a collision.** `session.py` ×3,
  `state.py` ×2, `base.py` ×2 and `shared/identity.py` keep their names. **Verify rather than
  trust this line:** a genuine clash produces an `import … as` between two COLLIDING modules, and
  the `import … as` forms that do exist rename a generic name away from a local binding — the
  opposite evidence. Re-open only for a name whose own package cannot resolve it. The two failures that ARE
  renames — a second word for something the repo already names, and a name that stopped describing
  its contents — are owned by root [`CLAUDE.md`](../../CLAUDE.md) § STOP.
- **Four banned words**, in identifiers and prose alike. **node** — never
  "building block", never "service". **eval** — use loop / round / scoring /
  fitness (the `Evaluator` class is the sole exception). **legacy** — either the
  path is dead, so delete the path, or the word is wrong, so delete the word.
  **query ranking** — it names three different things, so pick the one you mean:
  PoBB (budget allocation), the Rasch sort (samples), or `llm_ranking` (a backend
  node). The positive rule these serve — evolutionary framing for anything new —
  is the root [`CLAUDE.md`](../../CLAUDE.md) § Conventions.

## Code shape

- **No fallbacks in service code.** Two sanctioned exceptions:
  the measurement's synthetic-0 on `validation_failures`; load-boundary
  deprecated-sample gate (uses `classify_result()` fatal codes). Any new
  fallback must be documented alongside these.
- **Where a return-value contract must let an exception escape, use `graceful()`**
  (`shared/errors.py`). The contracts themselves — optimizer calls through
  `llm_call()`, a stop rule's verdict through `QueryLoopResult.stop_signal` — are owned
  by [`../../promptpotter/application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md)
  § Conventions.
- **Schema field order IS generation order.** A response model's fields are
  emitted left-to-right, each becoming context for the next; a `description=`
  is prompt, not documentation, so it is never trimmed as prose.
  Put reasoning/evidence fields *above* the fields they justify — below, they
  are structurally post-hoc. Which levers are free and which are wire contract:
  `docs/concepts/structured-output.md`.
- **A parameter that changes what a number MEANS takes no default.** Make it a
  required keyword; the signature is the enforcement (a caller that omits it fails
  typecheck, so there is no standing test to keep). The bug class: the decision then
  lives in an *absent* argument, and reading the call site tells you nothing — you must
  notice the absence, jump to a distant default, and find a docstring clause naming the
  intended callers. `open_walk` / `score_search_point` take `measured` this way.
  A default is fine when it is a *derivation* every caller would repeat identically
  (`compile_scorer(per_sample, per_cell=None)` → the objective IS the fitness), not when the
  right value genuinely differs per call site.
- **A query module reads a persisted document through its MODEL, never as a dict.** A round file
  parses as `RoundResult`, `dashboard.json` as its projection model — then direct field access is
  the natural reading, not an aspiration defended by `.get()`/`isinstance` at every key. A dict
  walk survives only in a cross-cycle SURVEY that must outlive one corrupt neighbour
  (`read_json_tolerant`, infrastructure `CLAUDE.md` § Picking a JSON reader). Converting an
  existing walk is standing maintenance: do it when you touch the module.
- **String-keyed *call* dispatch is a defect** — it hides the caller→handler
  edge from `grep`, so "is this method live?" costs a multi-hop tour.
  Fix by template: key is internal → explicit `match` with literal calls;
  key is a cross-file contract → registration decorator at the handler's
  definition site (the `@signal` `injection_table()` pattern); enum-keyed dict +
  import-time completeness assert is the third acceptable form. String-keyed
  *data* tables are fine.
- **A function-local import of our OWN package goes to module scope.** All three
  reasons for deferring one were measured and none holds. *Startup:* `--help`
  costs 1.38 s warm against a 0.16 s bare interpreter, so the deferrals buy no
  fast CLI; a real startup fix is `-X importtime`, not scattered deferrals.
  *Extras gating* ([`ADR-0006`](../adr/0006-embeddable-core-and-extras.md)) is
  real but lives on the **third-party** import inside the function, never on a
  `promptpotter` → `promptpotter` one. *A cycle* is a layer boundary in the wrong
  place, so the fix is to move the shared piece down (root `CLAUDE.md`
  § `<entry-point-parity>`). All therefore count against
  `complexity_ledger::deferred_imports` — read how many survive off that
  baseline, never off this page; `# extras: <name>` on the import line
  exempts one that earns it. **The defect the rule ends is the ambiguity** — an
  unmarked deferral cannot be told from a load-bearing one, so nobody can hoist
  safely or add one knowingly.

## Auditing for debt

Bar for reporting one: **high confidence after verification** — call sites traced, bodies read —
never "I spotted a smell". Where it survives that bar, **fix it in the pass that found it**; only
blocked or multi-arc work is filed ([`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md)).

Productive patterns:

- **Premature optimization with an apologetic docstring** — guards a scenario that cannot happen. Verify by reading call sites and measuring fire-rate.
- **Redundant double-protection** — two guards on one condition where one subsumes the other. Verify by writing the decision boundaries.
- **Single-caller indirection with no architectural reason** — no own test, no layer boundary. Skip splits across a load-bearing layer.
- **Dead exception paths / enum variants** — handler arms outliving the raising path. Grep every variant for a construction site.
- **Speculative API surface** — params never read, an `X | None` always non-None, fields declared and written but never read.
- **Absent collapsed into zero** — a `float = 0.0` default or an `or 0.0` coercion on a field carrying a MEASUREMENT. The tell is a `| None` sibling in the same model — the rule and its violation sitting in one constructor call. Counts, rates and money are honest zeros; reporting-only models default by written rule ([`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md) § Tolerance is scoped by what a payload is FOR). Enforcement is per-site — [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md) forbids a repo-wide scan — which is why this is a hunt pattern and not a task.
- **A decision in a router** — a route that ranks, filters or selects rows rather than parsing, checking, paging and formatting. No other entry point can import it, so the next one writes a copy (`presentation/CLAUDE.md` § Out-of-bounds).
- **Vibe-coded scaffolding** — `NotImplementedError` branches, comments about work the project does not plan. Check the roadmap before believing the "future".

**NOT debt — skip on sight:** intentional UI placeholders (each names itself in its own component
header); per-injection `char_cap`; domain vocabulary policed elsewhere (`origin` not `baseline`);
the `application/intelligence/ ↮ application/bench/` layer split; ABC `@abstractmethod` /
`Protocol` `...` bodies; `from __future__ import annotations`; boundary guards at external-input
sites (file I/O, JSON ingest); validators on `extra='forbid'` user-config models; `_*` private
helpers used by one caller **in the same file**.

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
- **Commit messages: aim 900 chars** total (incl. trailer), 950 tolerated; title <70.
  Terse bullets — no motivation essays. Past 950 → rewrite, do not
  commit-and-fix-later.
- **Hand-written work carries `Hand-authored-by: operator`** in the trailer block. Provenance is metadata, not area, so it never takes the `type(scope)` slot — that keeps saying *where*. Grep it with `git log --grep='Hand-authored-by'`. **Never spell it in the subject as `manual`** — the word collides with `docs/manual/`, with the `docs(manual,…)` area scope, and with prose about the install manual, so it cannot be searched for.

## Paid corrections

Bought by getting it wrong. Tells, not theory.

- **One grep is not absence** — a second spelling, a second tool, a second channel.
- **A green suite after a signature change is a zero.** Break it on purpose and watch it fail.
- **A rename done twice leaves the MIDDLE name.** `anti-rot` checks that a claim resolves, not that a name exists.
- **A synonym reads fine from inside its own file.** Nothing is locally wrong about a second word for a concept the repo already names; grep the WORD across subsystems, because that is the only place the collision shows.
- **`.get()` on a `total=False` TypedDict cannot raise.** Delete the key and every guard reading it goes quietly falsy — the opposite decision, no error, green suite.
- **Suppressing a display on truthiness collapses every silence into one.** `if (share)` hides a real measured 0, an unanswerable `None` and a not-applicable arm identically, and the dominant arm is usually the one you did not mean. Render the state, not the number's truthiness.
- **A glob skips `.inner/`** — dot-directories are absent, not empty. `os.walk`.
- **Ask what CHOSE the rows.** Pairing does not rescue an outcome-selected subset.
- **Ledger first, mtimes last.** From outside, a deliberate pause looks exactly like a crash.
- **Nothing scopes a commit to your edits** — `add` ships the INDEX, `--only` the WORKTREE. `git diff` every path.
- **Never `git checkout --` uncommitted work.** No reflog holds what was never committed.
- **Never regenerate `package-lock.json` on Windows** — it prunes the optional-platform graph and Linux CI dies.