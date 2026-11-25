# tw-j, tw-x, tw-s: the test rework's second pass, in parallel (cloud)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base. Your dispatch line names
your id and your base, `<BASE>`:
- **tw-s:** `origin/land/test-world` at `3625a6687`. That is the combine `7dce00a96`, tw-0…tw-4, tw-m (the
  app's memory) and tw-r's links 1, 2 and 4. tw-s runs beside chain tw-r2 (`briefs/tw-r2.md`), which
  owns the press/memory root.
- **tw-j and tw-x:** `origin/land/test-world` at `<BASE-JX>`, which is `3625a6687` plus tw-r2.

Replace the ancestor check with `git merge-base --is-ancestor 9cd30b5a7 <BASE>`. Equals-main runs against
`<BASE>`'s offline home.

Your dispatch line names your id. Push to `arc/<id>`, one commit per link, with the full gate green at
the tip.

**`tests/CLAUDE.md` binds whole.** tw-0 wrote it: the admission rule, the three tiers (rules are tables,
journeys are offline legs, edges run on the `world`), and the per-test budget (≤ 60 lines, ≤ 2 s,
≤ 2 MB left behind).

## Why a second pass
The operator (2026-11-25): "can completely rework everything. old was dumb." Phase 1 was shallow. On this
machine, `7dce00a96` → `9cd30b5a7`:
- tests/ went from 30,451 to 28,945 lines (−5 %) and from 641 to 598 tests, so lines per test stayed
  near 47;
- the gate's pytest took 268 s before and 276 s after, so no time was gained;
- the pytest job peak went from 373 to 353 MB.

What phase 1 left:
- 39 journeys parked in `# Journeys awaiting a leg` blocks;
- archive §3's 31 tests, still hand-seated;
- test_spend (6,021 lines, 122 tests), never censused;
- `seat`, `close_round`, `set_cycle`, `minted_campaign`, `stored_campaign` and `loop_session`, still
  called across five files.

Each fact is rebuilt in the tier that owns it, or deleted. A count is a signal to hunt deletions, never a
target. Justify each line you add.

## A pin is proven by a mutation
"An existing leg already pins this" is a claim, and a mutation proves it:
1. revert or break the one production line the fact guards, in your scratch;
2. run that optimizer's legs DIRECTLY on a fresh home:
   `PROMPTPOTTER_HOME=$(mktemp -d) python scripts/offline_run.py --optimizer <name>`.
   - Add `--straight` when the fact sits on the straight leg.
   - Never use the gate's row: a verdict it caches is not this tree's.
3. record the verdict, which must be RED, and restore the line.

Each such delete names the leg, the trace field, the mutation and the red line. Without the red line, the
fact gets its own assertion.

## Rules every chain shares
- **Counts are re-derived at `<BASE>`.** Every count below was taken at `9cd30b5a7`. Since then tw-r has
  edited offline_run, and tw-r2 edits offline_run, world.py and test_archive. A parked journey can be `def` or `async def`: test_security parks 3.
- **`behaviour_keys.json` and the `surface-ledger` ceilings.** `package_lines` sits at its ceiling, and
  any production touch re-keys an optimizer through `unrun`'s AST half. Each chain may commit its own
  re-key and raise a ceiling, with the reason in the comment and the commit body, to get green. I discard
  all three and regenerate both once at the integrated tip.
- **`tests/world.py` imports from `scripts/offline_run.py`.** At `9cd30b5a7` those names are `DATASET`,
  `ByCallIdentity`, `campaign_config`, `cited_panel`, `stand_in_fetch`, `stated`, `synthetic_rows` and
  `text_templates`, plus `offline_world.FakeBackend` and `Router`. Re-read the import block at `<BASE>`.
  - Those names are FROZEN for tw-j: no rename, no removal, no signature change. A new keyword parameter
    with a default is allowed.
  - tw-x needs no other name from offline_run. A helper tw-x builds lives in `world.py`.
- **Production is owned by directory.** Outside your list, report it and do not edit it, even for a
  one-line fix. The rows to file carry it.

  | chain | production it may edit |
  |---|---|
  | tw-j | `application/bench/node_context.py`, `application/scoring/**`, `application/optimizers/**`, `application/runner/**` except `runner/inner/**`, `application/intelligence/**` |
  | tw-x | `application/bench/difficulty.py`, `domain/ruler.py`, `application/commands/**`, `application/runner/inner/**`, `infrastructure/store/**` except the three spend stores below |
  | tw-s | `infrastructure/llm/**`, `domain/spend.py`, `infrastructure/store/{wallet_store,account_spend,llm_reuse_cache}.py` |

## The three chains
| id | does | owns |
|---|---|---|
| tw-j | journeys → legs | below |
| tw-x | the world's doors, and the hand-seated edges onto it | below |
| tw-s | the test_spend census | below |

Files shared between chains are named. Each chain edits only its own hunks of a shared file, and I merge.
`tests/factories.py` is tw-x's. tw-j and tw-s list the builders they stopped using, and I delete every
builder left with zero callers at integration.

### tw-j: every parked journey becomes a leg (Opus)
**The facts.**
- The four `# Journeys awaiting a leg` blocks: test_scoring 26, test_identity 8, test_security 3 and
  test_archive 2. Their leg specs (optimizer, dimension, trace assertion) are in
  `out/tw-{1,3,2,4}-report:report.md` § Journeys awaiting a leg. They are a census's guess: re-judge each
  one.
- test_spend §2's walk journeys that tw-0 kept: the `_walk` and `_round` helpers and the 11 tests that call
  them (`test_a_replayed_grading_is_metered…` through `test_an_inner_runs_stop_reaches_its_outer_cell…`).
  tw-r restored the base's sp:1714, sp:1780 and sp:1925 as legs (`potter/arrived`, and the bills and
  priced keys the resumed legs hold), so those facts already have a home.

**Per fact, in this order of preference:**
1. an assertion over a record an existing leg already writes, which costs no run time;
2. a dimension on `run_one`, as one new run carrying every fact on that dimension;
3. a world edge, on the world's API as it stands at `<BASE>` (`campaign`, `resume`). tw-x widens it in
   parallel, so a fact needing rewind or fork waits for that: say so in the report;
4. a rule exposed in production as its own function, with a table;
5. a delete, proven by a mutation.

A field a decision reads that moves on a leg FAILS the leg, as tw-r made the pressed leg's requests fail.
"Not a fail" is only for spend.

**Budget.** Measure `scripts/offline_run.py` directly, base vs tip, on the same machine: its wall time and
its child's max RSS. The cloud gate prints `-` for job peaks. A new run carries its reason in the commit
body, and its cost in seconds and MB.

**Owns:**
- `scripts/offline_run.py` and `scripts/offline_world.py`, within the frozen exports above;
- the four journeys blocks, each deleted when it is empty;
- test_spend's `_walk`, `_round` and their 11 callers;
- in production, the tw-j row of the table. Two rows are already filed there, both in
  `application/bench/node_context.py`:
  - tw-1's row: `closed_round` takes a whole `Cycle` and reads three fields, so narrow it to those three;
  - tw-4's row 1: expose the `Parent.own_rows` fold as a pure function over the round's cells, with a
    table.

### tw-x: the world's doors, and every hand-seat onto them (Opus)
**Widen `tests/world.py`** only with what the hand-seats below need, each a production door:
- **rewind:** `RunMode(from_round=N)`;
- **fork:** `RunMode(on_divergence=OnDivergence.FORK)` and the `fork_cycle` command door;
- **multi-round lines** across a campaign's cycles;
- **inner mint:** an L4 campaign whose inner sandbox (`inner_sandbox_store`) a fact reads;
- the supersede, family-tree, repair and halt paths archive §3 reaches.

A world fixture shared by several facts is scoped to share its run, and its cost per fact is stated.
Update `tests/CLAUDE.md` § The world in the same link.

**Re-point onto it:**
- test_archive, everything above its journeys block. §3 "Replayed decisions, and forks" runs about
  2,150 lines, up to §4, and tw-4 kept 31 of its tests hand-seated. §4, the origin gate halted then
  resumed past, is yours too. The `SimpleNamespace` past `Admitted.held` goes.
- test_identity, every test or fixture above its journeys block that calls `minted_campaign`,
  `stored_campaign`, `mint_cycle`, `write_resolved_run`, a hand `ledger.append` or `loop_session`. That
  includes `tiered`, `steered`, the `ScoringLockedRecord` appends, the knob layers and the inner door.
  tw-3's rows to file list them.
- test_scoring's `_evidence_campaign` and the head-to-head door tests that call it.
- test_ruler's five `traced` tests. tw-4's row 2: expose the series fold from `DifficultyView`'s inputs
  (`application/bench/difficulty.py`) as a pure function, so those tests become a table.

A door the world lacks is built in production, in its own link, never as a test seam. tw-3 found that
`factories.loop_session` builds a Session the real opening refuses (an LLM node with no owned model).
Move its callers in your files to the real opening (tw-3's `_opened`, promoted), then delete the builder
if no caller is left.

**Owns:**
- `tests/world.py`, `tests/conftest.py`, `tests/factories.py` and `tests/CLAUDE.md`;
- `tests/test_archive.py` above its journeys block, and `tests/test_ruler.py`;
- the named hunks of test_identity and test_scoring;
- in production, the tw-x row of the table.

### tw-s: the test_spend census (Opus)
Use the per-test method of `briefs/tw-n.md` § The method on `tests/test_spend.py` (6,021 lines, 122 tests,
§1–§6), except tw-j's `_walk`/`_round` tests.

Its sections are:
1. what a call costs;
2. what a run is billed;
3. a campaign's ceiling;
4. what a send is held at;
5. the host coupon;
6. spend outlives what spent it.

Most are edges or rules:
- §1, §4 and §5 are mostly pure prices, holds and coupons, so they become tables over the function;
- §3 and §6 are store and door edges onto the `world`.

You use the world's API as it stands, plus production doors. Your fork-ceiling facts in §3 use the
`fork_cycle` command door.

You cannot edit `offline_run.py`, so a journey you find in test_spend is the one thing left in place. Move
it, unchanged, to one `# Journeys awaiting a leg` block at the end of the file, with its leg spec
(optimizer, dimension, trace assertion) in your report. A pass with `offline_run.py` makes those legs
right after this phase, so keep the block short: an edge or a rule is never filed there.

Your file has 11 `loop_session` callers; move them to the real opening.

**Owns:** `tests/test_spend.py`, except tw-j's hunks. In production, the tw-s row of the table.

## Proof (each chain)
- Before → after for your files and for the whole suite:
  - lines, test count and lines per test;
  - pytest wall per file and in total;
  - the pytest process's committed MB, start and end;
  - `offline_run.py`'s wall time and child peak.
- Every test still over 2 s or leaving over 2 MB, named.
- **Coverage diff** over the production modules your files reach (`sys.monitoring` PY_START in your
  scratch, base vs tip). Every body the base ran and the tip does not is deleted code, restored, or named
  with its reason.
- **Equals-main against `<BASE>`:** every optimizer and leg UNMOVED. A new leg has no base, so state its
  green record.
- Full gate green at the tip. The cloud prints `-` for the job peaks, and I re-derive the caps locally.
  State your direct RSS and wall time instead.

**Report:** follow common.md's FINAL REPORT on `out/<id>-report`, plus:
- the census table (test → tier or delete, with the reason; for a delete-as-pinned, the mutation);
- the factories builders you stopped using;
- the rows to file.
