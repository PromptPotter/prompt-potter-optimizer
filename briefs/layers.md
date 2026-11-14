# layers: type imports that close no cycle hoisted, RunScope below the application (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `layers`, arc `arc/layers`.

- **Base:** `origin/main` at `4c87ba0fe3a07d14e8054406a528c7e3c051294c`, main's tip with hubs landed (4c87ba0fe). Your `<base sha>` in common.md is this commit.
- **The code shrinks:** target ≤ −300 over `promptpotter/`. Expected per link: link 1 ≈ −250, link 2 ≈ −35, link 3 ≈ −45, link 4 ≈ −15. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link. A link that leaves the chain net positive gets one rebuild. If it is still positive, the chain stops there, and link 4 stops if it alone is positive.
- **Spec:** the ledger section `### layers` of `docs/specs/code-debt-cleanup.md`, every row. Its premise is stale (read-only survey at 1c8b006ab):
  - The package has no function-local imports. All 664 indented package imports sit in `if TYPE_CHECKING:` blocks (184 blocks in 182 files).
  - The module-level file graph is acyclic.
  - `deferred_imports` (6) counts `import_module` calls and is not this check.
  - 117 type-only imports lie on a file-level cycle: the strongly connected components hold 102, 26, 4 and 3 files, and 33 of those imports close a cycle alone.
  - Rewrite the row to this in link 1's commit, and name the hunk.
  - These counts are from 1c8b006ab; valid, rel and hubs have landed since (183 files hold a `TYPE_CHECKING` block at hubs' landing). Re-derive each one at your base, and build to those numbers.
- **This is a re-key window:** `_normalized_source` hashes the `TYPE_CHECKING` binding, so links 1 and 2 re-key all six optimizers. Name each digest move.
- **You run alone:** you touch 182 files.

## Decisions (orchestrator; build them, do not reopen them)

- **What the check counts.** It counts type-only imports that lie on a file-level cycle: a new `scripts/complexity_ledger.py` row, `type_cycles`, at the count your link 1 leaves (117 or less). It sits in the same `BASELINE` as `package_lines`, so it can only fall.
- **Hoist every type-only import that closes no cycle.** That is about 612 statements, emptying 107 blocks. Keep:
  - the 117 on a cycle;
  - the 19 that keep `cli --help` fast;
  - the 8 that cross a layer;
  - the 4 Harbor extras.
- **`RunScope` moves to `infrastructure/run_scope.py`, not `application/`.** Six infrastructure readers would break `_LAYERING` under `application/`. Rewrite the row's text to say so.
- **One run-scope type.**
  - `shared/run_scope.py` holds `RunScope` and `ScopeFields` with the same 12 fields: one goes.
  - Telemetry's three `ContextVar`s become `RunScope` fields: `at_round`, `filed_as` and `bind_priced` go, and `scoped` / `rescope` carry them.
  - `RunScope.optimizer` duplicates `Cycle.optimizer`: it goes, with `bound_optimizer` and its determinism shim.
- **Each fold method moves to its record's module, and each part takes only the stores it reads.** This cuts the domain/store/application knots.
  - hubs left one knot named in code: `domain/kinds.py::KindTable.complete` walks `domain/records/` lazily because walking at import closes `results` → `records.error` → `run_records` → `results`. Cut that cycle at its root, then walk at import as the brief for hubs meant.
- **Bench and optimizers stop importing each other:**
  - `optimizers/nodes.py::RoundContext` moves into `bench/`.
  - `scripts/last_green.py::_import_names` reads type-only imports too, so the gate's `layering` check sees types. Land this after presentation's 6 `Stores` imports are cut.

## Links (one commit each, in order)

1. Hoist the type-only imports that close no cycle; the `type_cycles` row.
2. `RunScope` into `infrastructure/`, one scope type, the `ContextVar`s as fields, `RunScope.optimizer` gone.
3. The domain/store/application knots.
4. Bench ↔ optimizers, and `layering` reading types. This link stops the chain if it alone is net positive.

## Beside you

Nothing runs beside you. hubs has landed, and its files are now yours. Re-read them at your base:
- `domain/records/` (the walked registry; `run_records.py` moved there whole), `domain/kinds.py`;
- `infrastructure/llm/telemetry.py` (no `emit_*` now; its three `ContextVar`s remain), `campaign_store/store.py`, `infrastructure/ledger.py`.

## Proof (beyond common.md)

- Equals-main: no decision moves on any leg. Digests move in links 1–2, every optimizer. Name each.
- `python scripts/gate.py --only layering,surface-ledger` is green at each link.
- At link 4, the `layering` check fails on a planted type-only import that crosses a layer. Plant it and remove it.
- Symptoms already seen. Close each in the link that touches its file, or file it:
  - `telemetry.py::bind_priced`: it never resets;
  - `source_scan.py:32`: `PLUMBING_MODULES` misses `shared.run_scope`;
  - `last_green.py:101` hides type-only imports from the gate.
