# w9-trace: the offline trace names each decision it reads (cloud · Opus)

`briefs/common.md` and `briefs/w9-shared.md` bind you. Lands before w9-pass, so later re-keys name
decisions.

SPEC: `docs/specs/code-debt-cleanup.md`, four rows:
1. **The trace hashes a field's spelling**: `scripts/offline_run.py::behaviour_keys` hashes named
   decisions, never whole sub-documents. Three leftovers from w8-round ride here, as lines under this
   row (no row holds them): `ROUND_KEYS` and `ELECTION_KEYS` copy whole fields; near :1300 a check
   tests `r["round"] and r["ending"] == "crowned"`, where round 0 now ends `adopted`, so its round test
   is dead; `optimizers/potter/election.py::_verdict_reason` already matches on `RoundEnding`, so
   re-test it and delete the line if nothing is left.
2. **A pressed L4 outer run does not repeat**: the world names an inner campaign by its seat's
   `run_seed` (`_asker`, near :440–453, still reads `determinism.seed`).
3. **A rename on a run line re-keys potter**.
4. **The cycle index reads its chain beside the store's**: the index fold is handed its hop's chain.
   The row names only `offline_run.py` as blocking; `application/evidence/read.py:624,638` and
   `application/hierarchy.py:432` pass a bare dir too (re-points, in scope).

OWNS: `scripts/offline_run.py`, `application/optimizer_manifest.py`,
`infrastructure/projections/cycle_index.py`, `store/campaign_store/store.py`,
`promptpotter/assets/behaviour_keys.json`, `optimizers/potter/CLAUDE.md` (the rename line), and their
tests. Not yours: `scripts/offline_world.py` (held for a later tests chain); the bare-dir callers of
`read_cycle_index` in `evidence/read.py` and `hierarchy.py` (re-points only).

Keys: every key moves once by the trace's shape, and `estimator` moves again by the `run_seed` asker.
Name each old→new by its mechanism. Wipe: none expected.

LEAN: `scripts/` + `promptpotter/` shrink (expected about +150 / −200).
