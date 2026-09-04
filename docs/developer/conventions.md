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
  verbs (`restamp`, `diagnostics`) — anywhere else it writes to a stream nothing
  captures. Inside the live run readout it is narrower still: every line goes through
  `LiveDisplay._write`, the single stdout funnel that mirrors ANSI-stripped to
  `logs/latest.log`, so a bare `print()` there is a line no headless reader can recover.
- **Ruff line-length: 100.** Enforced by `scripts/gate.py`, which is what CI runs.
- **Direct field access** — `dict[key]` for guaranteed fields, not
  `.get(key, fallback)`. Fallbacks announce uncertainty; if you have a
  contract, lean on it.
- **Prose in the source — docstring or `#` alike — never restates the code as
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
- **A filler name whose PACKAGE PATH resolves it is not a collision.** `session.py` ×3,
  `state.py` ×2, `base.py` ×2 and `shared/identity.py` keep their names, and the refusal was
  bought by the verification it asked for: a genuine clash produces a disambiguating
  `import … as`, and there is **not one in the tree** — no module imports a colliding pair
  together. Re-open only for a name whose own package cannot resolve it. The two failures that ARE
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
  `score_population()` synthetic-0 on `validation_failures`; load-boundary
  deprecated-sample gate (uses `classify_result()` fatal codes). Any new
  fallback must be documented alongside these.
- **Where a return-value contract must let an exception escape, use `graceful()`**
  (`shared/errors.py`). The contracts themselves — optimizer calls through
  `llm_call()`, escalation through `QueryLoopResult.escalation_signal` — are owned
  by [`../../promptpotter/application/CLAUDE.md`](../../promptpotter/application/CLAUDE.md)
  § Conventions.
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
- **Absent collapsed into zero** — a `float = 0.0` default or an `or 0.0` coercion on a field carrying a MEASUREMENT. The tell is a `| None` sibling in the same model: the rule and its violation have twice appeared in one constructor call. Counts, rates and money are honest zeros; reporting-only models default by written rule ([`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md) § Tolerance is scoped by what a payload is FOR). Enforcement is per-site — [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md) forbids a repo-wide scan — which is why this is a hunt pattern and not a task.
- **Vibe-coded scaffolding** — `NotImplementedError` branches, comments about work the project does not plan. Check the roadmap before believing the "future".

**NOT debt — skip on sight:** intentional UI placeholders (each names itself in its own component
header); per-injection `char_cap`; domain vocabulary policed elsewhere (`origin` not `baseline`);
the `application/intelligence/ ↮ application/optimization/` layer split; ABC `@abstractmethod` /
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
- **Hand-written work carries `Hand-authored-by: operator`** in the trailer block. Provenance is metadata, not area, so it never takes the `type(scope)` slot — that keeps saying *where*. Grep it with `git log --grep='Hand-authored-by'`. Two pre-convention commits marked it in the subject instead (`docs: manual edit…`, `docs: maunal pass`); don't copy that — `manual` collides with `docs/manual/`, with the `docs(manual,…)` area scope, and with prose about the install manual, so it cannot be searched for.

## Paid corrections

Bought by getting it wrong. Tells, not theory.

- **One grep is not absence** — a second spelling, a second tool, a second channel.
- **A green suite after a signature change is a zero.** Break it on purpose and watch it fail.
- **A rename done twice leaves the MIDDLE name.** `anti-rot` checks that a claim resolves, not that a name exists.
- **A synonym reads fine from inside its own file.** Nothing is locally wrong about a second word for a concept the repo already names; grep the WORD across subsystems, because that is the only place the collision shows.
- **`.get()` on a `total=False` TypedDict cannot raise.** Delete the key and every guard reading it goes quietly falsy — the opposite decision, no error, green suite.
- **A glob skips `.inner/`** — dot-directories are absent, not empty. `os.walk`.
- **Ask what CHOSE the rows.** Pairing does not rescue an outcome-selected subset.
- **Ledger first, mtimes last.** From outside, a deliberate pause looks exactly like a crash.
- **Nothing scopes a commit to your edits** — `add` ships the INDEX, `--only` the WORKTREE. `git diff` every path.
- **Never `git checkout --` uncommitted work.** No reflog holds what was never committed.
- **Never regenerate `package-lock.json` on Windows** — it prunes the optional-platform graph and Linux CI dies.

## Reasoning doctrine

Six situational guardrails against recurring AI blind spots — unlike the
universal gates in root `CLAUDE.md` (see the top of this page), each fires
only in the specific situation named before it.

**When the operator bounds any budget axis → `<one-budget>`:**

<one-budget>
**The AI blind spot this guards against:** told "this must not exceed X", an AI treats every axis it was *not* handed a number for as free, and proposes an increase there — priced in the cheap axis and presented as costless. A run capped by patience came back with "only ~$0.50 more"; a per-cell measurement came back as "only ~12 extra calls, +3%". Both are budget increases the operator never agreed to, wearing the units they care least about.

**A limit stated on ONE axis binds ALL of them by default — wall-clock, dollars, tokens, calls, rounds, cells, samples — and the implication runs in every direction.** "Don't spend more" bounds the clock; "we don't have five hours" bounds the dollars. This is the ground assumption, not a reading to be argued out of, and it does not need restating per request.

So: **price a proposal in the axis the operator named AND in the ones they didn't**, in the same breath — the units they'd feel, not the flattering ones (an inner cell costs ~11 minutes *and* ~$0.05; quote both). Trading one axis for another is an increase and is asked for explicitly. And when the budget genuinely binds, the move is to get more out of the measurements already being paid for — a better estimator, a signal already recorded and thrown away, a larger effect to detect — never a bigger N in whichever unit currently looks cheap.
</one-budget>

**When an LLM call is slow / costly / token-heavy, OR you are adding anything to a prompt → `<simplify-the-problem>`:**

<simplify-the-problem>
When an LLM call is slow, costly, or timeout-prone because it emits a large number of tokens, treat the token volume as a **prompt-quality symptom, not a capacity problem**. A tightly-scoped prompt poses a *simpler* problem, so the model reasons less and answers shorter. The first move is to tighten the input — constrain the ask, cut open-ended or redundant injections (don't re-dump raw evidence a downstream node was already handed pre-digested), bound the output shape, lower reasoning effort to match the real difficulty — **not** to reach for a faster/bigger provider or raise the timeout/token cap. Simplify the problem so the model doesn't *need* the tokens; the deadline, the token cap, and the provider are safety rails, not the fix. (A specialization of <root-fix>: the cause is upstream in how we posed the problem.)

**Input length is a QUALITY tax, not only a bill — which is why this fires before anything is slow.** Every LLM degrades as its input grows: attention spreads, the middle is recalled worst, instructions compete. A block harmless at 200 chars is not harmless at 4000, so adding one obliges you to say what it displaces. And **attribute before you diagnose** — a prompt's weight is never where it feels like it is, and reasoning about the code cannot find it: dump ONE real payload from a ledger `llm_call` record and account for every character by block, counting the template fields, the dispatch panels inside them, and **the response JSON Schema, which is prompt text and is the one nobody counts** (`l1_generate` ran ~17.8k chars for months — 40% panels, 32% static prose, 23% wire schema, over half of that Python class docstrings Pydantic had hoisted into `description`).
</simplify-the-problem>

**When simplifying / labelling a change "refactor" / doing LOC work → `<surface-ledger>`:**

<surface-ledger>
**The AI blind spot this guards against:** told to "simplify", an AI reaches for *additive-but-safe* moves — extract a helper, fold two copies into a `shared/` util, split a big file — each of which adds a module + an import line per call site, so the **total grows** while every commit says "refactor". The genuinely shrinking moves (delete a mechanism, re-inline a single-use module, drop a dead knob) are riskier, so they get skipped. Four rules counter the drift:

1. **Lower the ledger.** Run `python -m promptpotter.complexity_ledger`. A pass *labelled* simplification/unification MUST move the total **down**. A pass that raises it isn't blocked — it just isn't a "refactor": justify it as a feature or as a shape that makes the codebase quicker to develop, and edit the baseline up with the reason in a comment **beside the raised number**, naming what the surface buys and why it folds into no neighbour — `git log -p tests/test_complexity_ledger.py` is the precedent. The reason rides the file rather than the commit body because the number is re-read every time the next raise is argued, and a body is not where anyone looks. **The TOTAL is comparable only across commits counting the same dimensions** — adding one jumps it by that dimension's whole magnitude with no surface moved, so read the rows, not the sum.
2. **Subtract a concept, don't relocate one.** Every simplification commit removes ≥1 *named* thing (module, class, public symbol, config field, code path). Moving code between files counts as zero.
3. **Extraction threshold.** Default: a shared helper earns its place at **≥3 call sites**, or when it removes a concept; at ≤2 callers inline is usually right. A default, not a bar — extract below it when the shared thing is an invariant callers must not diverge from, and say that's why. (The subtractive counterpart to the pre-flight "Reuse before adding" gate.)
4. **Lock the wins** — enforced, not advised. The ratchet asserts EQUALITY, so a deletion that lowers a dimension goes red until you lower the baseline in the same commit. Asserting only `<=` re-pins on raises alone: an unrecorded drop becomes silent headroom for the next raise, the baselines drift loose from the package they claim to measure, and the pass that earned the win has no number to show for it. The baseline records where the surface stands — it isn't a target to reach and halt at. When no dimension can fall further without losing a load-bearing concept, the unification *phase* is done; that says nothing about whether the next change may add.
</surface-ledger>

**When you changed what the engine *decides* (a gate, metric, or state), or added a capability at any entry point → `<entry-point-parity>`:**

<entry-point-parity>
**The AI blind spot this guards against:** an AI declares a task *done* the moment the engine logic is correct and the tests are green — it stops reasoning at the layer it edited and leaves every caller-facing half as a silent "later". Engine-correct is not product-complete. **There are five ways in — the CLI, an AI caller (the `/potter-run` skill), the embedded launch a host program drives (`application/embedded_run.py`: the notebooks, the BBEH harness, a packaged adapter), the REST API, and the webapp — and a capability that reaches only the one you happened to be editing is half-built.** This project is whitelabeled and user-facing; the forgotten surface is always the one sitting a layer past where the change was made, which is usually the webapp. Three rules:

1. **Parity is part of done.** When you change what the engine *decides* (a gate, a metric, a state), you owe every entry point that could ask for it a legible surface in the same breath — or, if it can't land now, you **write it down as planned** (spec + memory) rather than leaving it unstated. "Done" includes: can the human who relies on this *see* it from where they actually work, and is it *user-friendly*? If not, the work is half-built. Hold UX as a first-class axis, not a footnote.
2. **Teach, don't dump — and never force jargon.** A new internal value (a θ, a new statistic, a new mode) reaches the operator *taught*: a plain-language explainer, riding an **existing** surfacing channel (the lens/formula seam, not a new toggle), and **operator-selectable** so it is never forced on someone who doesn't speak that vocabulary. The engine may *decide* on the expert metric; the human *reads* the metric they chose. Teach from **one corpus** that serves the operator and the next AI reader alike — don't fork the prose.
3. **The reverse case is the urgent one — periphery instead of parity.** Rule 1 is about a capability you are ADDING. This is about one that already exists on one adapter and got written a second time on another, and it is the more expensive shape because nothing announces it: both copies work. **Treat a divergence the moment you see it, ahead of whatever else is queued** — every day it stands, the copies drift further on the parts nobody compares. The tell is one rule spelled twice; the cause is almost never carelessness. **It is a LAYER boundary: the shared piece sits where one adapter cannot import it** — orchestration parked in a `routers/` module, which a CLI verb can only reach by dragging FastAPI in — so the second adapter writes its own narrower copy instead. Fixing the copy leaves the cause in place and the next adapter writes a third. The cure is one-directional and the layering guard states it in its own allow-list comment (`scripts/gate.py::_LAYERING_ALLOW`): **move the shared piece down into `application/`, leave a thin shell at each entry point.** What the drift costs is not hypothetical — `edit-draft-campaign` lived in a router, so the CLI's `--set` grew a hand-written map that reached three of the twelve fields the patch model declared, carried its own bounds and its own error wording, judged a model steer against a different `allowed_models` list than the web did, and wrote the draft with no `CommandRecord` at all: an operator's origin edit was recorded nowhere and re-billed on every retry. **Where the second copy is a deliberate inversion, the ABSENCE is the boundary and must be stated as one** (`set-sample-lookahead`, root [`../../CLAUDE.md`](../../CLAUDE.md) § Conventions) — otherwise a later reader reads a deliberate hole as this bug and "fixes" it.
</entry-point-parity>

**When you are about to open a file you will WORK in, or run a search spanning more than three files → `<read-once>`:**

<read-once>
**The AI blind spot this guards against:** a narrow read *feels* frugal. It returns twenty lines instead of eight hundred, so it reads as the disciplined move — and the context window keeps every one of them, so twenty pokes cost twenty times. The AI optimizes the visible number (lines returned now) against the invisible one (lines resident for the rest of the session), and picks wrong every time. Measured across 133 session transcripts: **tool results are 83% of all content**, and **69% of the reading bill is RE-reading** — the same files, in the same session, a few lines at a time. On the ten hottest files, **265 of 276 reads were ranged**.

1. **Read whole, once, when you expect four or more touches.** A ranged read averages ~600 tokens and a whole one ~2,600, so the break-even is between four and five pokes — and `store.py` was poked thirty times in one session, for six times what reading it once would have cost. Open it, read it, work from what you have. The exception is a file read for *reference* rather than for work — `tests/` (the suite is fixed at six files by [`../../tests/CLAUDE.md`](../../tests/CLAUDE.md), so they grow unboundedly by charter) and the hand-authored contract YAMLs under `docs/specs/` — where you genuinely want one clause and will not return.
2. **Never read file CONTENT through `sed` / `cat` / `head`.** An identical `Read` is deduplicated by the harness; shell output never is, so the same bytes are billed in full every time — and it *looks* cheaper because it prints fewer lines. Use the file tools; keep the shell for things that are not file content.
3. **Delegate any search spanning more than three files.** A sub-agent reads in a window that is thrown away and hands back a conclusion; direct searching leaves every hit resident. This is measured, not stylistic: sub-agents were 3.3% of spend while direct `Grep` + shell `grep` was ten times that. Ask for the verdict and the paths, never the excerpts.
4. **Make a command assert, not display.** Header scaffolding and unconditional dumps around a check are pure cost — print on failure, and let success be silent. Same rule as rule 3 in miniature: return the conclusion, not the evidence for it.

**And the cheapest read is the one a correct pointer removes.** Half of all lookups in that corpus missed on the first hop and turned into a hunt — a search *plus* the reads it drags behind it. When a lookup fails, the debt is the navigation surface, not the search: fix the pointer that should have answered it — the owning `CLAUDE.md` row or doc index — in the same session, while you still know what you were looking for.
</read-once>

**When you reach for the shell, a sub-agent, or a wait → `<wall-clock>`:**

<wall-clock>
**The AI blind spot this guards against:** an AI prices its own loop in tokens, because tokens are the only cost it is ever shown. Seconds are invisible to it and are the only cost the operator actually watches — so it reaches for a shell pipeline where a file tool would do, sends sub-agents out one at a time, and waits by sleeping. Measured across the same corpus as <read-once>, over six days: **`Bash` spent 14.1 hours across 5,406 calls; `Read` + `Grep` + `Edit` + `Write` together spent 0.4 hours across 5,573.** Same order of work, thirty-five times the clock.

1. **A shell call carries a fixed toll the file tools do not** — `Bash` runs a median 2.4s and a mean 9.4s against ~0.1–0.4s for the file tools. This is the same conclusion <read-once> rule 2 reaches from the token side, and the two agree: the shell is for things that are not file content. When you do need it, put the whole errand in ONE call rather than five.
2. **Delegation is cheap in tokens and expensive in wall-clock.** A sub-agent is ~5 minutes. <read-once> rule 3 prices it at 3.3% of spend and is right about tokens, but **75 of 75 launches in that corpus went out alone**, so N searches cost N × 5 minutes when they could have cost one. Send them in a single message, or do the search yourself.
3. **Never `sleep`-poll in a shell.** Two polling loops in one recorded session burned seven minutes producing nothing. Run the thing in the background and let the notification arrive; a wait implemented as a loop is a wait nobody can interrupt.
4. **Iterate on the targeted check; run the gate once.** Gate commands were over half of all shell time. While editing, run the one tool — or the one test file — that owns what you touched; `scripts/gate.py` is the thing you run before you hand the work over, not between edits.

**And be honest about the context effect rather than reaching for it.** Turn latency does rise with the window — a median 1.4s at 50k against 4.4s at 700k — but that is seconds, and it is the whole of it: shell latency measured flat across a session, so nothing is degrading. It does not justify a five-minute delegation to save twenty thousand tokens.
</wall-clock>
