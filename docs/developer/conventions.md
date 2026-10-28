# Conventions

Rules not derivable from the code. Root [`CLAUDE.md`](../../CLAUDE.md) holds the non-negotiables.

## Code style

- **`logging` in library code; `print()` only where a human reads it** — the CLI (`presentation/cli/`), terminal views, the server banner, first-run setup, the origin gate, the maintenance verbs. In a live run every readout line goes through `ReadoutProjection._write`, the one funnel to the cycle's `readout.log`.
- **Direct field access** — `dict[key]` for a guaranteed field, never `.get(key, fallback)`.

## Prose in the source

- **None by default: no docstring, no `#`.** A line survives two gates or is deleted, not shortened: (1) *non-local* — with the prose covered, the reader would have to open another file to learn the fact; (2) *unowned* — a rule binding a set of symbols goes to the layer's `CLAUDE.md` or `docs/`. What survives is one present-tense line: a prohibition, a trap, a sentinel's absence semantics, a tiebreak, a security asymmetry. An `__init__.py` gets none. A `#` is a non-obvious *why* inside a body. **Past tense is a smell** — history is git's.
- **The webapp is in scope** (`.ts`, `.tsx`, `.css`, JSX comments): a component header says what it IS and which rule binds it, never what it replaced. `lib/api/types.generated.ts` is fixed at its source docstring.
- **A `CLAUDE.md` is billed to every session beneath it**: rules binding a set of symbols only; mechanism goes to the module docstring, an incident to the commit body; [`../CLAUDE.md`](../CLAUDE.md) § Editing a doc applies. Caps: gate `instruction-files`.
- **A cut fact goes to the cheapest rung that still reaches the reader who would get it wrong:** (1) the type or signature — prose defending against a bug the type prevents is deleted; (2) one line at the site; (3) the layer's `CLAUDE.md`; (4) `docs/`; (5) § Paid corrections, generalized to one line; (6) the commit body.
- **Three carve-outs are product surfaces a generator reads**: docstrings of every type a route's `response_model` reaches (→ TS JSDoc via `scripts/build_ts_types.py`; a class docstring's line 1 ships, so it is a full sentence; a `@computed_field` ships whole), FastAPI route docstrings, and Pydantic/enum class docstrings (→ OpenAPI). Optimizer response models in `dispatch/schemas.py` strip their class docstring; their `Field(description=)` is prompt text — regenerate via `scripts/build_optimizer_schemas.py`.

## Naming

- **A filler name its package path resolves is not a collision** (`session.py`, `state.py`, `base.py`). A real clash shows as an `import … as` between two colliding modules. Renames: root `CLAUDE.md` § STOP.
- **Four banned words**, identifiers and prose: **node** (never "building block" / "service"); **eval** (say loop / round / scoring / fitness; `Evaluator` excepted); **legacy** (delete the path or the word); **query ranking** (say PoBB, the Rasch sort, or `llm_ranking`). Nothing checks them.

## Code shape

- **No fallbacks in service code.** Three sanctioned: the synthetic-0 on `validation_failures`; the load-boundary deprecated-sample gate (`classify_result()` fatal codes); an owed adapter output re-run before the next walk, halting `PAUSED` past `OWED_WRITE_ATTEMPTS` (`runner/round.py::settle_owed_outputs`). A new one is listed here.
- **Where a return-value contract must let an exception escape, use `graceful()`** (`shared/errors.py`).
- **Absent is not zero.** A failed or missing measurement is ABSENT (`None`, an absent channel), never `0.0`; a real 0.0 is a measurement. Counts, rates and money are honest zeros. Render the state, never the number's truthiness. Every layer that emits or reads a measurement obeys this one rule.
- **Ask the typed predicate, never a hand-authored set of names.** A literal set of dataset / node / stop-reason names silently skips what it failed to list. Ask `stop_reason_outcome` (`domain/phases.py`), `backend_type_of_dataset`, or the registry; a needed set is derived from the typed table, and a curated subset is pinned by an `assert subset <= derived` (`infrastructure/store/family_ray_queries.py`). Enforcement is per-site, never a repo-wide scan.
- **Schema field order IS generation order.** Reasoning/evidence fields go above the fields they justify; a `description=` is prompt, measured like any prompt edit (`docs/concepts/structured-output.md`).
- **A parameter that changes what a number MEANS takes no default** — a required keyword (`open_walk` / `score_search_point` take `measured`). A default is fine only as a derivation every caller would repeat.
- **A query module reads a persisted document through its MODEL**, never as a dict; a dict walk survives only in a cross-cycle survey (`read_json_tolerant`, `infrastructure/store/CLAUDE.md`).
- **String-keyed *call* dispatch is a defect** — an internal key becomes a `match` with literal calls; a cross-file key a registration decorator (`@signal`); or an enum-keyed dict with an import-time completeness assert. String-keyed *data* is fine.
- **A function-local import of our own package goes to module scope**; the ledger counts `deferred_imports`, and `# extras: <name>` exempts a third-party extras import ([`../decisions.md`](../decisions.md) § 0006).
- **Simplify by subtracting a concept, never relocating one** — a simplification removes ≥1 named thing; moving code counts zero. A shared helper earns its place at ≥3 call sites, or when it removes a concept or holds an invariant callers must not diverge from.

## Auditing for debt

**A review looks for changes that land broken, not old code that decayed.** Over 200 commits (2026-06-11→07-09, 49 fixes) the median gap from a `.py` file's previous change to its fix was 3 commits; 4 of 44 fixes touched code older than ten. The top modes tell one story, the engine reporting a number nobody can check: measurement validity 16/49, silent wrong value 11, exhaustion/hang 11, wrong-scope read 9, untrue reported number 8, fallback hid the bug 7, two owners 5. Ask in order: (1) what enforces the fitness contract (bounded, subset-invariant, candidate-varying)? (2) where does missing data become a plausible number? (3) what is unbounded? (4) what fact has two owners? Read a file's fix ratio, not its churn.

**Report only after verification** — call sites traced, bodies read — and fix it in the pass that found it; only blocked or multi-arc work is filed ([`../specs/code-debt-cleanup.md`](../specs/code-debt-cleanup.md)).

Hunt patterns: a guard for a scenario that cannot happen; two guards where one subsumes the other; single-caller indirection with no layer reason; handler arms outliving their raise; params never read, `X | None` always non-None, fields written never read; absent collapsed into zero (`= 0.0` / `or 0.0` on a measurement beside a `| None` sibling); a decision in a router (rank, filter, select) no other entry point can import; `NotImplementedError` scaffolding the roadmap does not plan.

**Not debt:** UI placeholders naming themselves; per-injection `char_cap`; the `application/intelligence/ ↮ application/bench/` split; abstract/`Protocol` bodies; `from __future__ import annotations`; guards at external-input sites; validators on `extra='forbid'` config models; a private helper with one caller in the same file.

## CLI / running

- **A foreground command gets 30 s**; anything longer is backgrounded and its notification awaited, never a longer timeout or `timeout N`.

## Git

- **Conventional commits** — `feat:`, `fix:`, `docs:`, `refactor:`; title <70 chars.
- **Body: terse bullets stating deltas** (ledger rows moved, not totals), aim ≤900 chars incl. trailer — a convention, nothing checks it.
- **Hand-written work carries `Hand-authored-by: operator`** in the trailer, never in the `type(scope)` slot and never spelled `manual` in the subject.

## Paid corrections

- **One grep is not absence** — a second spelling, tool, channel or tree (a codemod misses dynamic imports).
- **A green suite after a signature change is a zero.** Break it on purpose and watch it fail.
- **A rename done twice leaves the MIDDLE name.**
- **A synonym reads fine from inside its own file** — grep the WORD across subsystems.
- **`.get()` on a `total=False` TypedDict cannot raise** — delete the key and every guard goes quietly falsy.
- **A glob skips `.inner/`** — dot-directories are absent, not empty. `os.walk`.
- **Ask what CHOSE the rows.** Pairing does not rescue an outcome-selected subset.
- **Ledger first, mtimes last.** A deliberate pause looks exactly like a crash.
- **Nothing scopes a commit to your edits** — `add` ships the INDEX, `--only` the WORKTREE. `git diff` every path.
- **Never `git checkout --` uncommitted work.**
- **Never regenerate `package-lock.json` on Windows** — it prunes the optional-platform graph (gate `lockfile` catches it after the fact).
