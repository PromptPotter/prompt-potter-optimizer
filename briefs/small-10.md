# small-10: one sandbox-store builder; a CLI refusal by field; a budget stop says its parts (cloud)

`briefs/common.md` binds you. Base: `origin/main` (426af7f43 or later). Rows are in
`docs/specs/code-debt-cleanup.md`; quote each row's title in the commit that closes it and delete
it. One link per row, in this order; a row that reads false at its line is reported, not built.

## Links
1. **Two builders of one sandbox store** (the row's Action: one builder from `(stores, hop)`; every
   caller that splits a `CycleHop` to call it passes the hop). Callers at base:
   `cycle_reads.py:213`, `hierarchy_subtree.py:373`, `jobs/reaper.py:68`,
   `runner/inner/transcript.py:107`, `store/lineage_queries.py:79`, `stores.py:295`, and
   `runner/inner/spawn.py::sandbox_stores` (5 callers in `spawn.py`). The writer
   (`spawn.py::sandbox_stores`) builds without the existence checks the reader needs: keep that
   difference as the one builder's input, not as a second builder. **Re-test:** the row's grep.
2. **A CLI payload refusal reads pydantic's union text** (the row's Action, for every verb, at the
   one place the CLI renders a `PayloadInvalidError`). **Re-test:** the row's, offline: the verb
   refuses before it reaches a campaign, so run it in a fresh home.
3. **The cap halts on money its served reading omits** — only its "Left open" part: a budget stop
   travels as its `StopLoop` from a kept cut (`runner/round.py`, `run_walks`' return) and a round
   boundary (`runner/loop.py::LoopEnd`), so the `run_phase` record's `detail` names the book's parts.
   The `SpendBook.refuse_unless_room` / `exhausted()` disagreement is the row's first half: report
   whether one reading closes it; do not build it. **Re-test:** the row's, on the offline world.

## Owns
`infrastructure/store/stores.py`, `infrastructure/store/lineage_queries.py`,
`application/cycle_reads.py`, `application/hierarchy_subtree.py`, `application/jobs/reaper.py`,
`application/runner/inner/spawn.py` (`sandbox_stores` and its callers only),
`application/runner/inner/transcript.py`, `presentation/cli/main.py`, `application/runner/round.py`,
`application/runner/loop.py`, `application/runner/termination.py`,
`domain/records/run_records.py` (`StopLoop` only), tests in modules of your choosing OTHER than
`tests/conftest.py`, `tests/factories.py`, `tests/test_spend.py`, `tests/test_identity.py`, generated
specs/types. NOT `application/optimizers/potter/` (a live L4 experiment keys on its code: a link
whose root is there is reported), `infrastructure/store/campaign_store/`, `application/jobs/`
other than `reaper.py`, `pipeline_resolve.py`, `infrastructure/llm/spend_book.py`.

## Target
Net ≤ 0 `promptpotter/` lines over links 1–2; link 3 may grow by what its carrier needs, named.
Run the `--against` legs and `--digests`; name every leg and digest that moves, old→new. A moved
`behaviour_keys.json` fingerprint or L4 digest is reported before it is pushed.

## Report
Per `common.md`.
