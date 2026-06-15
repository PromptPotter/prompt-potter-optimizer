# tests — Charter

Small, adversarial, subtractive. The goal of this suite is to guard a handful
of invariants that the rest of the codebase can lean on; everything else is
noise that an AI assistant (and a reviewer) has to read, skip, or discount.

Root CLAUDE.md already says *"No backward compatibility — freely break
signatures, rename, restructure"*. Tests must reflect that: if a contract is
renamed or restructured, **delete the test and start over** — do not port
assertions across versions.

## What gets a test

A test earns its place only if it guards one of these:

1. **Named invariants from root CLAUDE.md.** Persistence parity
   (`CAMPAIGN_ARTIFACTS` / `SESSION_ARTIFACTS`), rescore-on-load +
   decision-replay + fork, `score_search_point` as the single scoring gateway,
   nested-only `pipeline_params`, round-boundary dataset mutation, and the
   cadence rules engine (`decide_escalation` over `DEFAULT_ESCALATION_RULES`).
   Escalation rule firings ride `events.jsonl` directly — the canonical record.
2. **Statistical / numerical correctness.** Bayesian Posterior-of-Being-Best
   (joint Normal-CLT posterior, Monte Carlo argmax), composite scoring,
   per-dataset scorer formulas. The math must be wrong-reveal, not
   wrapper-smoke.
3. **Wire / schema contracts with external systems.** Backend `GET /pipeline`
   parse, LLM retry (503/429 vs. 400), Pydantic `extra='forbid'` on user
   config.
4. **Resume / mutation safety.** Resume-from-round invariants, zero-signal
   filter, dataset mutation side-effects.
5. **Frozen model shape.** `JobSearchPoint`, `OptSearchPoint`,
   `PipelineSchema` — the objects every service passes around.

## What never gets a test

Deleted on sight, no replacement:

- **Display / formatting.** Substring assertions on rendered headers, tags,
  leaderboards, summaries, journal/notes output. The format churns weekly;
  the test churns with it and catches nothing.
- **Trivial wrappers.** Inverse of a one-line math helper, obvious string
  templating (`{{var}}` → `value`), two-branch `if/else` fallbacks.
- **Bug-specific regression tests with stub forests.** If a past bug needed
  N monkeypatched internals to reproduce, the call graph will move and the
  test will become a liar. Encode the fix as a code-level invariant
  (assertion, type, dataclass field) instead.
- **UX affordances.** Journal/notes/HITL exchange surface. It is a habit, not
  a contract.
- **Volume or O(n) scaling tests.** No "runs 100 candidates", no "all 50
  datasets". One canonical case per contract.

## File layout — one file per charter category

A test lives where its *kind of guarantee* lives, not next to the module it
pokes. Six test files, full stop; adding coverage means adding a row or a
fixture, not a new file.

| File | Category | Guards |
|------|----------|--------|
| `test_structure.py` | lint | Declarative source-scan tables (banned calls / forbidden regex / layer-import edges) + the bespoke AST locks, run by the `_scan.py` engine. Adding a lint = adding a row. |
| `test_invariants.py` | C1 | Named invariants: artifact-band parity, cycle-identity-is-dir-name, typed-ledger roundtrips, escalation engine, dispatch-hub wiring + untrusted fencing, secret redaction, archive retrieval + dataset scoping, identity foundation, lifecycle, quota, event stream, run-phase, presentation view round-trip + projection routing. |
| `test_numerics.py` | C2 | Statistical / numerical correctness: PoBB, Rasch, composite scoring, scorer formulas, L1/L2/L3 validators. |
| `test_contracts.py` | C3 | Wire / schema: API envelopes, LLM retry, Pydantic `extra=forbid`, pipeline parse, control-plane drift, origin-gate, authored-dataset reader. |
| `test_resume.py` | C4 | Resume / mutation safety: rescore / replay / fork / rewind / merge / zero-signal filter / `DiffScope`. |
| `test_shapes.py` | C5 | Frozen model shape: `JobSearchPoint`, `OptSearchPoint`, `PipelineSchema`. |

Each category file carries the standard `test_` prefix, so pytest's default glob
collects all six with no `python_files` override. Support modules carry no tests
and are excluded by their underscore/`conftest` names: `conftest.py` (fixtures),
`_factories.py` (pure data builders + on-disk seeding), `_scan.py` (the
source-scan engine), `_helpers.py` (LLM-client mocks). The locked six-file shape
is enforced by `test_structure.py::test_category_files_are_collectible` — a stray
`test_*.py` or a missing category file fails loud.

Wiring-completeness guarantees (every-kind-gated, every-connector-whole,
every-renderer-wired, every-leaf-classified, every-record-dispatched) are NOT
tests — they are import-time assertions in the module that owns each registry,
so a malformed registry fails at startup everywhere, not just in CI.

## The delete-don't-update rule

When a contract is renamed, restructured, or replaced:

1. Delete the old test file (or function) outright.
2. If the new contract needs coverage under the rules above, write a fresh
   test for the new shape.
3. Never keep half-applicable assertions around. A zombie test that was right
   under the old names is worse than no test — it lies about what the code
   does today.

## Ceiling

Target **6 test files, ≤ 240 collected tests**. The six-file shape is the
contract — new coverage rides an existing category file, never a new one.
Canary (the non-`-q` collect prints the grand total; `-q` only prints per-file
counts): `python -m pytest tests/ --collect-only 2>&1 | grep "tests collected"`.
Currently **263** (+3: the fatal→operator escalation guard + the `nurse_target` retirement ban
— the self-healing owner reframe that dissolved the producer-keyed `nurse_target` into a single
`RuntimeFailure.owner` field; the routing-map test went away with the `CorrectiveSurface`/`route()`
tower it guarded).
The degradation-verdict feature (context-aware round health +
the source-stamped warning classifier — two of the cases lock "unknown/missing
``kind`` is skipped, never structural", the bug the shadow-taxonomy hid) added ~27
cases that resisted parametrize-merge —
each guards a distinct grade / track-record / attribution mode, and the charter's
"one invariant per test" rule makes mega-merging them across functions worse, not
better. So 240 is the standing aspiration, not a wall: prune genuine redundancy
before adding, but don't delete a distinct invariant to hit a number. The hard
contract is the **six-file shape**, which holds.

## Fixtures (`conftest.py`)

| Fixture | Purpose |
|---------|---------|
| `built_stores` | A real `Stores` rooted in `tmp_path` under the default identity. |
| `seeded_campaign_cycle` | `(stores, CycleHandle)` — one canonical campaign + cycle on disk (with ledger). |
| `patch_pointer_root` | `(stores, active-pointer path)` — redirects the module-level pointer root into the temp tree so bare fork-machinery `save_active_pointer` calls land in `tmp_path`. |

## Factories (`_factories.py`)

Pure data builders + on-disk seeding — every test rides these instead of inline
JSON or a `build_stores` + mkdir dance: `make_campaign_dict` / `make_index_dict`
/ `make_dashboard_dict` / `make_round` (dicts, override any field via kwargs)
and `seed_campaign_cycle(tenant_root, ...) -> CycleHandle` (lays a canonical
tree through the real store path helpers).

## Helpers (`_helpers.py`)

| Helper | Purpose |
|--------|---------|
| `MockCompletion` | Fake OpenAI-compatible completion response |
| `make_http_error(status_code)` | Create a mock HTTP error exception with `status_code` attribute |

## Mock strategy

No pytest-mock plugin. Use `monkeypatch` for async, stdlib `unittest.mock`
when needed. If you find yourself stacking more than 2–3 monkeypatches in a
single test, that is a signal the test violates the "no stub forests" rule.
