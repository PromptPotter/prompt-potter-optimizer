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

## The delete-don't-update rule

When a contract is renamed, restructured, or replaced:

1. Delete the old test file (or function) outright.
2. If the new contract needs coverage under the rules above, write a fresh
   test for the new shape.
3. Never keep half-applicable assertions around. A zombie test that was right
   under the old names is worse than no test — it lies about what the code
   does today.

## Ceiling

Target **≤ 18 test files, ≤ 240 collected tests**. Above that, prune before
adding. Canary (the non-`-q` collect prints the grand total; `-q` only prints
per-file counts): `python -m pytest tests/ --collect-only 2>&1 | grep "tests collected"`.
Currently at 240 — AT the ceiling. Prune before adding any further test.

## Fixtures (`conftest.py`)

None currently. Add via `conftest.py` if a cross-cutting setup/teardown is genuinely needed.

## Helpers (`_helpers.py`)

| Helper | Purpose |
|--------|---------|
| `MockCompletion` | Fake OpenAI-compatible completion response |
| `make_http_error(status_code)` | Create a mock HTTP error exception with `status_code` attribute |

## Mock strategy

No pytest-mock plugin. Use `monkeypatch` for async, stdlib `unittest.mock`
when needed. If you find yourself stacking more than 2–3 monkeypatches in a
single test, that is a signal the test violates the "no stub forests" rule.
