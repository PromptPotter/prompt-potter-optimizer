# rel: the tooling rows: the venv key, the hooks, one wheel smoke, the walk world's L4 leg (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `rel`, arc `arc/rel`.

- **Base:** `origin/main` at `de4c0cc47480f00b7b4e37fa015681c9ddddc0ad`. Your `<base sha>` in common.md is this commit.
- **The code shrinks:** target ≤ −40 over `promptpotter/`. Link 2 deletes `presentation/terminal/completion.py` (61 lines); every other link is near zero there. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link. A link that leaves the chain net positive gets one rebuild, then the chain stops there.
- **Spec:** the ledger section `### batch/32 · rel` of `docs/specs/code-debt-cleanup.md`, the rows of rel 1 and rel 2.
  - **Not yours:** "Run data is stat'ed bare outside `store/io.py`" (it lands local and last, on the assembled tip) and rel 3's unverified list.
  - **Stale at the base:** "`bbeh_potter_runner.py` closes a client the run already closed". `aclose` is gone from the script. Delete the row in link 2.

## Decisions (orchestrator; build them, do not reopen them)

### Link 1: one key for the venv, one interpreter for the hooks, one lock rule

- **`scripts/gate.py::_install_digest` hashes the resolved pinned set** (what `uv export --frozen` yields for `_PINNED_EXTRAS`), never the lock's bytes. A lock edit outside the pinned extras leaves it unmoved.
- **`.githooks/commit-msg`, `pre-commit` and `pre-push` call the pinned venv's interpreter**, the one `gate.py::_reexec_pinned` names (`.venv/Scripts/python.exe` or `.venv/bin/python`). No hook execs bare `python`.
- **The `CI` waiver goes:** `queue_behind_siblings(..., exempt=unqueued or bool(os.environ.get("CI")))`. A CI runner has no sibling to queue behind, so the lock costs it nothing. A desk `CI=1` gate then queues behind a pre-push gate.
  - If a CI job runs two gates in one clone, report it. Do not key a new waiver on another variable.

### Link 2: the `jupyter` extra and its one renderer go

- **`presentation/terminal/completion.py` goes**, with the `jupyter` extra (`pyproject.toml`'s `jupyter = [...]`, its `ipython` deptry note and the `jupyterlab` mapping).
- **`paper/bbeh-comparison/bbeh_potter_runner.py` is pinned to v0.8.20**, the last release that ships the module.
  - Its header and the folder's README say "runs against `promptpotter==0.8.20`".
  - It leaves `pyproject.toml`'s mypy `files` and `mypy_path`, and every gate check that imports it (`gate.py::_BBEH_DIR`: read what it runs).
  - The script stays; never edit its logic.
- **Regenerate `uv.lock` with the repo's pinned `uv`.**
  - Link 1's digest re-test is this link: the pinned set does not move, so no venv rebuilds.
  - If no `uv` can resolve the lock in your box, stop the link and report it. Never hand-edit the lock.

### Link 3: one wheel smoke, every generated schema written by the gate

- **`scripts/smoke_wheel.py` takes the wheel and makes its own venv.** `.github/workflows/ci.yml` and `publish.yml` call it once each, and the `RUNNER_TEMP/elsewhere` blocks go.
- **`examples/optimizer-plugin/example_optimizer/resolved_schemas.json` is written by the gate's `optimizer-schemas` check**, like the five under `promptpotter/assets/`. A hand edit turns the gate red.
- **`scripts/build_optimizer_schemas.py` prints repo-relative paths** (`wrote|unchanged <path>`), and so does every regenerator that writes into the checkout.

### Link 4: the walk world's L4 leg, the walk key's None path, `_git_head`

- **`scripts/gate.py::_walk_world` also builds an offline L4 leg** (`offline_run.py --l4`) into the walk world's data root. `webapp/e2e/walk/cells.spec.ts:40`'s `test.skip` becomes a failure.
  - Report the walk-world build time before and after.
- **A gate test runs the `walk-world` check with no key.**
- **`config/settings.py::_git_head` refuses** a ref found in neither the loose refs nor `packed-refs`, naming the checkout. It no longer answers `UNVERSIONED`. Grep every reader of `UNVERSIONED` and keep the constant only for a checkout with no `.git` at all.
- **The Vitest row:** re-test it against the gate DAG's pool-width cap (`gate.py`, the comment at `:1139`).
  - If the cap closes it, delete the row and name the cap.
  - If not, file what remains: its re-test needs this desk.

## Beside you

valid-2 runs at the same time on the store, mint, fork and door files, `scripts/complexity_ledger.py` and three webapp files (`useRunControl.ts`, `LiveSegment.tsx`, `SteerForkPanel.tsx`). Touch none of them.

`docs/specs/code-debt-cleanup.md` is shared by hunk: edit only the rel section's rows.

## Pushing workflow files

A push that changes `.github/workflows/` can be refused for a missing `workflow` token scope.
- If yours is refused, push the chain without that hunk.
- Put the hunk in your report as a fenced `diff` block. The orchestrator applies it at landing.

## Proof (beyond common.md)

- **Equals-main:** UNMOVED on every leg, and no digest moves.
- **Full gate** at the tip, `playwright` included: link 4's cells drill must pass 1, skip 0 (`npx playwright test --project=walk e2e/walk/cells.spec.ts`).
- **Each row's Re-test, before and after.**
- **Link 1:** the digest is unmoved across link 2's lock change.
