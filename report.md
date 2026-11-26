# tw-v2 — report

Base `77e13a563edb13a05804735879ad01bc7335f311` (BASE-V2, `origin/arc/tw-v`'s tip; `a1720cae0` is its ancestor); `git log -1 --format=%cd origin/main`: Wed Nov 25 15:18:06 2026 +0100. Arc: `arc/tw-v2` at `26751c1`.

**Verdict: NOT accepted. The gap is not closed.** In the final interleaved census the suite ran 65.3 / 67.0 s at `7dce00a96`, 67.5 / 72.2 s at BASE-V2 and 70.8 / 79.3 s at the tip. The tip's second run is slow in every file. Across three sessions the tip can't be told apart from BASE-V2. The gap is `test_archive.py`: 3.4–3.5 s at `7dce00a96` against 16.4–19.4 s at BASE-V2 and the tip. No link here touches it. Stopped as the brief says.

## Per link

| link | commit | +/− (all; `promptpotter`) | bought |
|---|---|---|---|
| 1 one normalization | `604b03e` | +113/−60; +107/−60 | 2nd `derived_landing_key()` 2.7–3.0 → 0.72–0.86 s; cold 3.45–3.7 → 2.8–3.0 s; `build_ts_types.py` 8.3–8.8 → 8.0–9.0 s (noise) |
| 2 checkout | `0810521` | +16/−4; +8/−4 | 47 of 937 dashboard writes skipped (~33 ms); fsync priced, all kept |
| 3 boot graph | `26751c1` | +13/−0; 0 | skipped: no misplaced module (ranking in the appendix) |
| 4 census | none | — | below |

LEAN: `git diff --shortstat 77e13a563 HEAD -- promptpotter` = 5 files, +114/−63 (net +51). The growth is the fix itself: the memo, its two record types (`Imported`, `ModuleCode`), the import walk moved beside the normalizer and the compressed holder. A hunt for dead code in the owned files found none.

## Proof
- Full `python scripts/gate.py` at the tip: 31 checks green, 12 reused from an identical green tree (pytest among them; the census ran it six more times, 0 failed).
- Equals-main against BASE-V2: 53 legs, all UNMOVED. `--digests` are identical at base and tip (6 keys), and `behaviour_keys.json` is unchanged in all three trees.
- `offline_run.py` wall and child max RSS: 79.5 s / 191 MB, 75.3 s / 183 MB, 77.2 s / 185 MB.
- Coverage (PY_START): base 8133 functions, tip 8139. Only two base bodies are gone: `hashing.py::normalized_tree` (deleted; its body is now `_parsed`) and `import_walk.py::_is_type_checking` (moved to `hashing.py`).
- No wipes, nothing installed, no fact cut, no test touched.

## Decided in-run
- The memo is keyed by the text's sha256, never the text. The first shape held 10.5 MB of source and code and raised the suite's end RSS by ~10 MB. The normalized code is held zlib-compressed (4.2 → 0.9 MB).
- Callers that only walk imports (`layering`, `surface-ledger`, offline_run) parse without the memo. Through it, `surface-ledger` went 14.6 → 32.1 s.
- Link 2's premise was wrong at this base: no `dashboard.json` write is ever fsynced. Each of the suite's 5087 fsyncs lands on a truth file.

## Cuts (holders outside Owns): 2
`store/data_root.py::row_schema` (link 1's re-test) and the six `durable=` callers (link 2's "never a flag a caller passes"). Both rows are filed in the ledger.

---

## Appendix: Census (one session, interleaved, final tip)

| tree | wall s | RSS start → end MB | tests |
|---|---|---|---|
| 7dce00a96 | 65.28 / 66.98 | 100.4 → 328.8 / 337.5 | 641 |
| BASE-V2 | 67.53 / 72.15 | 101.9 → 301.5 / 302.2 | 644 |
| tip | 70.81 / 79.28 | 101.9 → 310.3 / 305.1 | 644 |

Earlier sessions, BASE-V2 vs tip: 75.2 / 73.8 vs 76.5 / 71.0 (link 1's first shape), and 78.1 / 76.9 vs 80.3 / 73.3 (sha-keyed memo). End RSS was 307.1 / 304.1 vs 314.6 / 315.7, then 302.7 / 304.5 vs 304.6 / 308.0. The landing-key fact alone raises a fresh process's RSS by 20.8 / 21.1 / 21.1 MB at the tip and 20.6 / 21.1 / 21.4 MB at base, so the tip's end-RSS spread is allocator noise, not the memo. tracemalloc shows the fact retaining 4.57 MB at the tip vs 2.96 MB at base.

Wall per file (s, run 1 / run 2):

| file | 7dce00a96 | BASE-V2 | tip |
|---|---|---|---|
| test_security | 26.8 / 27.7 | 19.4 / 21.4 | 20.5 / 21.8 |
| test_archive | 3.5 / 3.4 | 16.4 / 16.8 | 17.6 / 19.4 |
| test_identity | 12.5 / 12.8 | 8.4 / 8.7 | 8.3 / 10.0 |
| test_scoring | 9.4 / 9.7 | 8.3 / 8.5 | 8.5 / 10.5 |
| test_spend | 8.8 / 8.8 | 8.1 / 9.9 | 9.0 / 10.0 |
| test_connectors | 1.1 / 1.1 | 3.2 / 3.3 | 3.6 / 3.7 |
| test_ruler | 1.1 / 1.3 | 1.7 / 1.5 | 1.4 / 1.6 |

Touched production files (lines, 7dce / base / tip): `shared/hashing.py` 103 / 103 / 170, `shared/import_walk.py` 123 / 123 / 103, `projections/live_dashboard.py` 1015 / 1015 / 1018.

## Appendix: Re-tests before → after
- Link 1: a second `derived_landing_key()` in one process went 2.7–3.0 → 0.72–0.86 s, against a target of 0.2 s, which is unmet. The closure went 2.16 → 0.12 s; `row_schema` is 0.6–0.85 s of what is left. `built_key.py` was regenerated and `behaviour_keys.json` did not move.
- Link 2: 5087 fsyncs a suite, 2.5 s, all on truth files: `charges.jsonl` 3087 (1.75 s), durable temporaries 941 (0.68 s: `.live-jobs`, live job files, `user.json`, `tokens.json`, grants) and directory syncs ~900. `consent.jsonl` 111 and `grants_audit.jsonl` 6 are appends. All are kept. `dashboard.json`: 937 writes, 0 fsynced, 47 that change no byte, now skipped.
- Link 3 ranking (`-X importtime`, cumulative): `promptpotter.main` 2.63 s, of which `application.health` 0.82, `presentation.mcp.server` 0.67 (`mcp` 0.64, mounted at `/mcp`, so called), `connectors.protocol` 0.44 and `routers.reads` 0.28. Third-party: `mcp` 0.64, `mcp_types` 0.17, `fastapi` 0.15, `sse_starlette` 0.13, `uvicorn` 0.13, `numpy` 0.07 (a core dependency). CLI `--help` 0.45 s and `campaigns` 0.68 s: `domain.command_kinds` 0.40–0.58 and `domain.campaign` 0.27–0.43. No fastapi, mcp or dspy is loaded. No extra loads into a boot that never calls it, so nothing moves.

## Appendix: Rows closed
- **fsync is ~2.5 s of real time a suite** (tw-v; it was never in the ledger): closed by the decision in `infrastructure/CLAUDE.md` § Persistence, with a probe.
- **`import_closure` re-normalizes 329 modules per landing key** (tw-v): closed for the closure. Its open half is filed as the `row_schema` row.

## Appendix: Rows to file
All of these are now in `docs/specs/code-debt-cleanup.md`, under wave 6:
- **`row_schema` rebuilds every row's JSON schema per landing key** (link 1's open half).
- **`durable=` is a caller's flag restating `Kind.durable`** (re-test: `git grep -cE "durable=(True|not )" -- promptpotter/*.py` is 0).
- **Child-process facts idle the parent ~8 s**, restated with link 3's ranking (door: fewer models built at import).
- **Archive's world runs cost ~14 s a session over its hand-seated file**: the suite's whole remaining gap.

## Appendix: Symptoms
- `promptpotter/infrastructure/store/data_root.py:806`: `row_schema` rebuilds pydantic JSON schemas on each call. Root: the schema has no content key that would let a memo avoid identity.
- `scripts/gate.py` reported "12 unchanged since green" and reused a pytest result from an identical tree. The census ran the suite six times at the tip with 0 failures.
