ARC w2k: § K of census 2. Branch `arc/w2k` off `origin/batch/3`.

Your spec is § K of `briefs/census_2.md` on this brief branch (`git show origin/brief/wave-2:briefs/census_2.md`). Read its lines 1-78 (method, chains, ledger hunks) and your whole section; its Owns, Holders, served deltas and checks are this brief's. Its line numbers are origin/batch/2 702c7f2a4's: the ledger's lines past 152 sit 4 lower on your base and past 1081 6 lower (J and H landed), and an earlier link in your chain moved more; grep.

Also ship two ledger-only edits (census § Shared-file and order notes, "Orchestrator-only ledger edits", its first two bullets): delete the duplicate `LaunchState` row (batch/2 201-204; keep 1182-1185, which names the five readers), and drop the stale `capInput`/`parseCapIn` clause from the hand-built-controls row (batch/2 1014-1016) and `parseCapIn|capInput` from its re-test.

Commit: a conventional subject from the section's title, ≤72 chars.
