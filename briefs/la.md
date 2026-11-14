# la: the CLI as five modules, every registry complete at import, one run-state leg (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `la`, arc `arc/la`.

- **Base:** `origin/main` at `917861717`, main's tip with lw and rn-conn landed (`backend_type` is `connector`, `Registry.get` refuses typed). Your `<base sha>` in common.md is this commit.
- **The code shrinks**, judged as common.md says: expect about −300 over `promptpotter/`. A module merge pays for itself in deleted imports, re-exports and docstrings. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** the ledger section `### batch/27 · la`, every row. Re-run each Re-test first; a row its re-test already closes is named and cut, not built. Behaviour keys have landed, so a move outside an optimizer package re-keys nothing. Cut every "no digest re-key" clause the keys made stale, and name the hunk.

## Decisions (orchestrator; build them, do not reopen them)

- **One stream for a `--json` refusal:** the `Refusal` JSON on stdout, nothing on stderr.
- **Wave A, the moves:**
  - the CLI as five modules: `cli/campaign_runner.py`, `parsers.py` and `commands/verbs.py` become `cli/main.py`, the small read verbs become `commands/reads.py`, and the measure verbs become `commands/measure.py`;
  - `infrastructure/identity/` goes from 12 modules to 3 (`oidc`, `grants`, `accounts`);
  - `projections/live_dashboard/` becomes one module;
  - the LLM wire types move into `llm/base.py`.

  Each move re-points every import, `CLAUDE.md` row and doc citation in the same commit. `gate.py --only layering,help-imports,handler-tables,doc-claims,instruction-files` stays green.
- **Every registry completes at import.** `Verb.needs_registries` goes, and every verb lands. The fold roster completes with the registries: `wiring.py`'s `noqa: F401` joins go, and every entry checks the roster, not only `every_treatment=True`. `connectors/__init__.py::_APPLICATION_BUILT_INS` goes: the recursion's connector registers where the application completes its registries.
- **One run-state leg.**
  - `run_state.py` (the derivation, the stamps and `RECENT_STEP_S`) imports neither `store/stores.py` nor `campaign_store/`, and both read it.
  - `run_owed` goes: `derive_run_state` takes the reader's own hold as an input.
  - `producer_attached` goes, and its readers ask `producer_lock.cycle_held`. Its `scripts/offline_run.py` and `tests/test_security.py` readers are named shared.
- **`answer_operator` goes.** The admin bot reaches the ring through a sanctioned seam: the `scripts/gate.py::_PRESENTATION_IMPORTS_INFRASTRUCTURE` line, named shared.
- **CORS:** `If-Modified-Since` leaves `allow_headers`.

## Links (one commit each, in order)

1. The CLI's five modules; the one-stream refusal; `answer_operator`.
2. `identity/` to 3 and `live_dashboard` to one; the LLM wire types; the run-state leg (`run_owed`, `producer_attached`); CORS.
3. Every registry at import: `needs_registries`, the fold roster and `_APPLICATION_BUILT_INS`.

## Beside you

Name every shared file in its link's commit body. Two cloud chains run on an older base (f589cbe9a). In a file of theirs, touch only your hunks; where your move merges a file they edit, name it, and the landing carries their hunks into your module.

- **readings** folds the estimate types (`BandedValue`, `LiftEstimate` and the `*Reading` estimates become one `Estimate`) and changes the served shape.
  - It edits `presentation/cli/commands/read.py` and `verify.py`, which your link 1 merges into `commands/reads.py` / `measure.py`.
  - It edits `runner/round.py` and `shared/statistics.py`.
- **rec** makes an arm's act one record (`runner/round.py`, `scoring/query_loop.py`), turns state-fitting records into discriminated unions, deletes unread fields, and persists the cold fold snapshot (`store/data_root.py`'s CACHE row). It may reach `infrastructure/projections/live_dashboard/`, which your link 2 makes one module.
- **webb-5a** (cloud, your base) is webapp-only, but it shares `scripts/gate.py` with you: it adds a flag that answers the current walk world. Touch only your `_PRESENTATION_IMPORTS_INFRASTRUCTURE` hunk.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg, behaviour keys unchanged. These are moves and deletions: any moved decision is a bug.
- `python -m promptpotter resume --json --campaign nope 2>&1 >/dev/null` prints nothing.
- Each of the seven verbs that declared `needs_registries=False` runs against a data root one landing behind, and lands it first.
- `git ls-files 'promptpotter/presentation/cli/**/*.py' | wc -l` and `git ls-files 'promptpotter/infrastructure/identity/*.py' | wc -l`, before and after.
- Each row's re-test, before and after.
