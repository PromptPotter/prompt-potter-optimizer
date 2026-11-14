# bkeys: behaviour keys, the switch, one L4 cell key (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `bkeys`, arc `arc/bkeys`.

- **Base:** `origin/main` at `1e0db24787dbfffc326fc65585d407faa79b7c3a`, main's tip with layers landed (1e0db2478). Your `<base sha>` in common.md is this commit. This is the last re-key: after you, a refactor that changes no behaviour moves no key.
- **The code shrinks:** target ≤ −600 over `promptpotter/`.
  - Link 1 should come to about −660.
  - Link 2 is about 0.
  - Link 3 lands between about −25 and +20.
  - Run `git diff --shortstat <base> HEAD -- promptpotter` after each link. A link that leaves the chain net positive gets one rebuild, then the chain stops there.
  - Link 3 stops the chain if it alone is positive: file its live-backend half as a row.
- **Spec:**
  - the ledger section `### bkeys` of `docs/specs/code-debt-cleanup.md`, every row. That includes "A node's call settings carry its prompt fields" (valid-3 filed it), which rides your link 1: its digest move folds into the re-key;
  - `.scratch/wave-4/simplify/report.md` Part 1 (the shape R4) in the repo's history: `git log --all -- .scratch/wave-4/simplify/report.md`. If it is absent, the shape is restated below.
- **Operator, 2026-11-12:** behaviour keys are approved, and the §0.5 bullet is accepted verbatim: "An optimizer's behaviour key — L4 identity is its own package's code and prompts, its fingerprint on the offline world, and the world's version (generated `behaviour_keys`, re-derived by the gate); the whole-tree hash is recorded beside it, never keyed on."

## Stale premises (read-only survey at 1c8b006ab; re-grep, never trust a line)

- `draw_bank` and `draw_bench` no longer exist: the draw is inline in `runner/inner/spawn.py::_open_seat`.
- `n_samples` and `n_samples_origin` move the search draw (`resolve_resume`'s origin hash reads them), so they cannot be continuation-free.
- `docs/concepts/l4.md:59-61` is wrong on three counts:
  - a pin re-keys;
  - the panel resolves three times;
  - an edit made mid-run lands at the next spawn.

## Decisions (orchestrator; build them, do not reopen them)

- **The key** is the hash of three things: the optimizer's own package code and its prompt files, its `BehaviourTrace` fingerprint on the offline world, and the world's version.
  - The own-package hash is computed live. The fingerprint is a committed generated file (`behaviour_keys`); the gate regenerates it, and a diff is red.
  - A plugin optimizer's fingerprint is `None`.
  - There is no fallback: a stale key is a refused commit, never a second identity.
  - The whole-tree hash is recorded beside the key and never keyed on.
- **What goes with the switch:**
  - `infrastructure/store/source_scan.py`, whole;
  - `shared/hashing.py`'s source walk (≈ `:74-278`);
  - `optimizers/nodes.py::source_digest`'s AST walk;
  - the 11 `prompt_sources` ClassVars;
  - the 177 `shapes_optimizer_prompt` marker lines (237 uses in 44 files);
  - `PLUMBING_MODULES`;
  - `test_identity.py`'s `Path.read_text` edit probe;
  - their `data_root` rows.
- **The L4 instrument hashes what the inner session loads.** That is the seat's `MintPlan.origin_hash` plus its `measured_address`, resolved at the outer run's init: one async resolve and one backend read per seat. It is never a hand-picked subset of local inputs.
- **One cell key, with depth inside it.** The depth fields that define the measurand (`max_inner_rounds`, `n_samples`, `n_samples_origin`) join the outer cell key. Only the continuation is depth-free: it subtracts `budget`, `depth_nodes` and `n_samples_bench`. The inner campaign id derives from the outer cell key plus the edits, and `_purpose(role)` leaves every content id. The eight places that key a cell collapse into one `domain/l4` key.
- **The estimator** becomes a generated entry fingerprinted by the L4 leg. Its digest covers what decides a cell's number: `inner_instrument_config`, the seat's draw (name it) and the n rule. `max_inner_rounds` takes `ge=2`, as `l4.md` says; the code is fixed, not the doc.
- **The URGENT row rides with you**, because you edit `data_root.py`'s rows: "A row read by a function hashes the function's name, not its shape". Each reader row declares the model it parses, and `_schema` hashes that model. Build it in link 1, beside the rows you delete. Name the landing-key move it makes.
- **Wipes:** `source_facts.json` and `treatment_digests.json`, plus `campaign.json` if `Treatment.source` is renamed (rename it to what it now holds), plus every `.inner/` sandbox. Name each in the commit body.

## Links (one commit each, in order)

1. The generated `behaviour_keys` file, its gate check, and the switch: `source_digest` becomes the own-package hash, its readers take the key, and the machinery above goes. evidence's `arm_id` unifies on the new key.
2. The estimator as a generated entry with its full digest, `ge=2`, the draw named.
3. One `domain/l4` cell key with depth inside it, the instrument off the loaded object, and `_purpose` out of the ids.

## Beside you

Nothing runs beside you. hubs and layers have landed, and their files are now yours. Re-read them at your base:
- `infrastructure/run_scope.py` (the one `RunScope`; `shared/run_scope.py` is gone), `domain/record_kinds.py`, `domain/records/`, `domain/kinds.py`;
- `infrastructure/store/source_scan.py` and `shared/hashing.py`, which both changed under the hoist. You delete them anyway; re-count what goes at your base.
- Every module's imports moved in layers' hoist, so re-grep every count in this brief (`shapes_optimizer_prompt`, `prompt_sources`).

## Proof (beyond common.md)

- RE-KEY: every treatment digest and every L4 cell moves once. Name old → new per optimizer. Decisions and requests are UNMOVED on every leg: identity changes, behaviour does not.
- Planted refactor: re-run the key over a pure-plumbing change, and every key stays UNMOVED. Use the holds-6 pair `8e8ba56ff` → `7b462c987` if both are reachable, or else a type change in `infrastructure/run_scope.py::RunScope` you make and revert.
- Planted behaviour change: one changed prompt line moves its optimizer's key and no other.
- `l4.md` and `docs/specs/roadmap.md`'s "The re-keys end at the behaviour-key switch" stay true. Fix `l4.md`, `refactor-arcs.md`, `offline-run.md` and `potter/CLAUDE.md`. `git grep` every name you deleted.
