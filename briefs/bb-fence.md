# bb-fence: moves N2 + N3, identity by bound names, then the member fence (cloud · Opus)

`briefs/common.md` and `briefs/bb-shared.md` bind you. Spec: `briefs/backbone.md` § 4 rows **N2** and
**N3**, § 2 (the contract, knobs and identity rows), § 5 V12, § 10 item 7, and fork F2 as settled in
`bb-shared.md`. Both links land as one commit and **one re-key** (the orchestrator squashes your arc).

Root: a member's behaviour key hashes every import statement by module path
(`scripts/offline_run.py::_unvouched`), so renaming any module a member imports re-keys every built-in.
And members reach past the contract into the bench, the runner and non-grading scoring (V12's counts), so
the plugin boundary is not where the contract says. The later backbone moves are free of re-keys only once
both are fixed.

Link 1, N2: **an import is keyed by the names it binds.** In `_unvouched`, an import keys only the names it
binds. An `ImportFrom` keys each alias's `name` and `asname`, never `module` or `level`. An `Import`
keys each alias's `asname`, or the first component of its dotted name when it has none, never the rest
of the path. Every other statement
keys as today. Regenerate `assets/behaviour_keys.json` and name each built-in's key old→new. Fix the
docstrings that describe the key (`optimizer_manifest.py`, `docs/architecture.md` § 0.5,
`docs/developer/offline-run.md` if it says so). Prove it: a scratch edit that renames a module a member
imports, with every import re-pointed, moves no key. Revert it, and report the result. Measure § 10 item
7: the share of each built-in's member lines the offline world runs (`ran_lines`), per built-in.

Link 2, N3: **the fence**, as `bb-shared.md` defines it (one definition; follow it exactly).
- The face a member types against becomes Protocols in the contract: `NodeContext`, `RoundContext` and
  the reads. `bench/node_context.py` stays their implementation.
- `StopLoop` becomes a `domain/stop.py` type.
- `append_record`, `run_scope` and `inner_sandbox_store` become methods of the face.
- What members take from `bench.llm_call`, `bench.resume_and_fork.replayers` and non-G scoring goes
  through the face.
- Knobs leave for `optimizer_knobs.py` and identity for `optimizer_identity.py`. The check-in manifest
  leaves `optimizer_contract.py` as § 4 says, and `optimizer_manifest.py` keeps find, parse and select.
- A setting a member reads from `config` comes through the face. No re-exports.

Reword the debt row "The plugin contract is spread over seven modules" (area 09): its Re-test becomes
"the example imports nothing under `bench/`, `runner/`, `initialization/` or a built-in's package". Close
it if that holds.

Owns: § 4 N2's and N3's whole file sets: `scripts/offline_run.py` (`_unvouched` and its callers only),
`assets/behaviour_keys.json`, the contract modules, `optimizer_manifest.py`, `config_map.py`,
`pipeline_responses.py` (the manifest's served half), `bench/{node_context,cycle,task_context,llm_call}.py`,
`bench/resume_and_fork/replayers.py`, `domain/{stop,treatment,failures}.py`, every file under
`application/optimizers/`, `examples/optimizer-plugin/**`, and their tests and docs. In `bench/cycle.py`
you change import lines only. bb-ladder owns `scripts/gate.py`: report your member-import edge count
(grep) before and after, and the orchestrator sets the count at landing.

Re-key: one (this landing). Equals-main must show **decisions and requests unmoved** for every built-in.
Only the keys move, each explained by link 1's rule or by a member line that changed.

Done when:
- `git grep -E "^\s*(from|import) promptpotter\." -- promptpotter/application/optimizers examples/optimizer-plugin`
  shows only the allowed modules.
- The rename probe moves no key.
- The full gate is green, and equals-main shows decisions and requests unmoved.
- The report gives `ran_lines` per built-in.
