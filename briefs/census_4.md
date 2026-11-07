# Wave 2 — census 4 (read-only, origin/batch/7 8394fe873, 2026-11-07)

Every grep below ran as `git grep -n <pattern> origin/batch/7 -- <paths>` unless stated. The extra
paths `paper scripts tests webapp docs .claude deploy-linux .env.example pyproject.toml README.md
examples` were swept for every fact. batch/7 holds main plus batches 6 and 7 (V–AE) and counts as
landed. The w2x6 fix pass is in flight; its files are not touched: `deploy-linux/{update.sh,
install-admin-bot.sh, bootstrap.sh, hardening.sh, README.md}`, `promptpotter/__init__.py` and
`store/private_root.py`. Arc letters continue at **AF**. `scratchpad` below is
`C:\Users\dsacc\AppData\Local\Temp\claude\1f721a7e745fea05\07707fc1-0bb0-43d8-9aea-e2fed072bf92\scratchpad`.

Digest reach was measured on a `git archive origin/batch/7 promptpotter` export
(`scratchpad/census4/export`). It used census 3's recorder with one fix,
`scratchpad/census4/probe_reach_per.py`: `nodes._source_digest.cache_clear()` runs before each
treatment, so reach is filed per treatment, not as one union under `?`.

| treatment | modules hashed whole | units reached |
|---|---|---|
| capo | 9 | 333 |
| capo-pobb | 15 | 1067 |
| gepa | 7 | 334 |
| levi | 7 | 335 |
| potter | 19 | 1011 |
| estimator | 5 | — |

The union is 26 modules and 1088 units, the same as census 3's.

**Digests.** `scratchpad/census4/digest_probe.py` (what `offline_run.py --digests` computes) ran twice:
- on the export;
- on `scratchpad/census4/edited`, a copy carrying every Python production edit of AF, AG, AH, AJ, AM
  and AN. That is salvage/m17's reader hunks (`af_ag.patch`, applied clean) plus `apply_c4.py`.
  Two edits differ from the arcs as briefed, both in `infrastructure`, which the walk never enters:
  - AG ran in the salvage's `TYPE_CHECKING` form, not the `held_tokens` move;
  - AN ran without inlining `_read_index`.

**All six digests are byte-equal** to each other and to census 3's:
`estimator 784922e722ef7a3a`, `capo e105a07bf147ca5e`, `capo-pobb 2992a36aa41aab5d`,
`gepa 933eef981a877549`, `levi 9e08a41bf5396428`, `potter 26598fc48b15541e`. AI is `scripts/`, and AK
and AL are webapp-only.

**Unreached, among the modules these arcs edit:**
- `application.maintenance.storage_report`, `application.jobs.install_spend`, `domain.activity`,
  `presentation.api.middleware.headers`;
- `domain.phases::ErrorRecord`: the reached units of `domain.phases` are its nine stop units, as on
  batch/5;
- all of `infrastructure`, which is plumbing and stops the walk: `store.dataset_access`,
  `tenant_dataset_store`, `wallet_store`, `read_model`, `data_root`, `lineage_queries`,
  `account_spend`, `identity.*`, `llm.spend_book`.

**Tests run.** On a `git archive origin/batch/7 tests scripts research pyproject.toml
paper/bbeh-comparison examples` export with the edited package copied in
(`scratchpad/census4/testroot`), the command was
`PROMPTPOTTER_HOME=<scratch> .venv/Scripts/python.exe -X utf8 -m pytest -p no:cacheprovider
tests/test_security.py tests/test_identity.py tests/test_archive.py`. These are every test file that
names a production path the arcs edit. Result: **11 passed, 0 failed**.

`scratchpad/census4/probe_c4.py` read each path on both copies:

| read | batch/7 | edited |
|---|---|---|
| torn `grant.json` | raw `JSONDecodeError` | `FileUnreadableError stored_config_invalid 500` |
| torn `api_keys.json` | raw `ValidationError` | the same |
| torn `default_claimed.json` | raw `JSONDecodeError` | the same |
| a 7-byte unrowed campaign file | `trace` 7, `loop` 7 | `unclassified` 7, `loop` 0 |
| `ErrorRecord.raised(ValueError("\nsecond line"))` | message `''` | message `'second line'` |

Line numbers in `docs/specs/code-debt-cleanup.md` (the ledger) are batch/7's. The batch-7 fix pass may
edit the ledger first, so grep each row's bold title.

---

## Chains (pairwise-disjoint files; only the ledger is shared, in disjoint hunks)

| Chain | Arcs, in order | Hand-edited files | Regenerated |
|---|---|---|---|
| 1 — the store's readers | **AF** `_named` deleted → **AG** the wallet and identity files through `read_file` → **AH** an unrowed file served as its own leaf | 12 | `types.generated.ts`, `openapi.generated.json` (AH) |
| 2 — M1's riders | **AI** the offline run's two readings → **AJ** `style-src 'self'` → **AK** `eslint-plugin-react` | 6 + AK's finding fixes | `package-lock.json` (AK) |
| 3 — reads and words | **AL** the check-in picklist's names → **AM** an error's message is never empty → **AN** a child course keeps the index it was filtered by | 4 | none |

**Every arc's files are disjoint from every other arc's.** The one exception is AK's finding fixes:
they can land in any webapp file, so AK runs after AL (see the order notes). The ledger hunks are
disjoint across chains. Within a chain, three pairs share lines: AF and AG, AJ and AK, AM and AN. So
each chain runs as a chain.

**Order by kind.**
- The deletions lead: AF (a second door), AI (a stale clause and a racing count), AL (a dead row), AM
  (a fallback).
- The consolidations follow: AG, AH, AN, AJ, AK.

**The ledger hunks** per chain (batch/7 lines):
- Chain 1:
  - 1212-1217 (§ Batch E's census A, bullet 1): AF deletes its `load_task_context` and `_named`
    clauses. AG trims the rest to `hard_sample_sorter.py` and replaces its re-test.
  - 255-259: AG deletes "One torn `grant.json`".
  - 1021-1025: AH deletes `_leaf_of`.
- Chain 2:
  - 208-212: AI deletes the kept green.
  - 1485-1490: AI deletes the controlled count, with its stale "Separately" clause.
  - 913 and 918: AJ deletes the CSP sub-bullet and makes the parent's "five" read "four".
  - 913 and 917: AK deletes the ESLint sub-bullet, and "four" becomes "three".
- Chain 3:
  - 1591-1592: AL deletes the picklist sub-bullet.
  - 1600-1601: AL deletes the latch sub-bullet.
  - 1563-1564: AM deletes the `ActivityFeed` clause of "Unverified, for the next change to each file".
  - 1564-1565: AN deletes that bullet's `iter_family_courses` and `list_hierarchy` clauses.

The nearest cross-chain pair is chain 2's 208-212 and chain 1's 255-259, with 42 untouched lines
between them. Chain 2's 1485-1490 and chain 3's 1560 have 69 between them.

**No arc reshapes run data or a survivor.** `grant.json`, `api_keys.json`, `default_claimed.json`,
`blocklist.json` and `grants.json` are SURVIVOR rows. AF and AG change how a torn one is refused,
never its bytes, so nothing is migrated.

**Ports.**
- AF and AG take salvage/m17's reader hunks. Seven files apply clean to batch/7 (probed). AG's
  `read_model.py` hunk differs; see AG.
- AI's count and AJ take salvage/m1-2's hunks. Three files apply clean (`m12.patch`, probed).
- Neither tag empties. m17 keeps the landing, and m1-2 keeps the gate work.

---

## Chain 1

### AF — `dataset_access.py::_named` deleted; a dataset's framing, origin and task context read through `read_file`

**Row:** "**A. A file read past its store's one reader**", bullet 1 (1212-1217). Its
`tenant_dataset_store.py::load_task_context` and `dataset_access.py::_named` clauses are deleted. AG
takes the rest.

**Owns (2 hand-edited):** `promptpotter/infrastructure/store/dataset_access.py`,
`promptpotter/infrastructure/store/tenant_dataset_store.py`, and ledger hunk 1212-1217 (its two
clauses).

**Greps:**
- `_named` in the full path set: `dataset_access.py` only; no doc or test names it.
- `read_framing_file\|read_dataset_framing\|read_dataset_origin\|origin_prompts\|has_dataset_prompts\|load_task_context`
  in `promptpotter tests scripts`.
- `except.*ValueError` in every file holding a reader. Each match wraps another call:
  - `launching.py:98`, `draft_patch.py:128`: `narrowing_of`;
  - `ingest.py:92`, `dataset_access.py:498`, `tenant_dataset_store.py:87`: `validate_dataset_name`;
  - `tenant_dataset_store.py:95`: `dataset_dir`.
- `exception_handler` in `main.py`: `PotterError` (:224), `RequestValidationError`, `HTTPException`,
  and `Exception` (:272), which answers 500 `internal_error`. No handler maps a `ValueError`.
- `def parse_yaml` in `store/io.py`: it wraps `yaml.YAMLError` as a `ValueError` (:351-356), so
  `read_file`'s `_ROW_ERRORS` catches a bad YAML.

**Holders:**
- `dataset_access.py:122-130`, the `_named` def, is deleted. With it go the `Callable` import at :6
  and `read_bytes_optional` at :21.
- `dataset_access.py:291` (`read_framing_file`) becomes
  `read_file(path, dataset_framing, FileKind.STORED_CONFIG)`.
- `dataset_access.py:322` (`read_dataset_origin`) becomes
  `read_file(directory / ORIGIN_FILE, origin_layer, FileKind.STORED_CONFIG)`.
- `tenant_dataset_store.py:195-197` (`load_task_context`) becomes
  `read_file(self.task_context_path(name), read_task_context, FileKind.STORED_CONFIG)`. Its
  `read_bytes_optional` import at :21 goes; `read_file` is already imported at :32.
- Not holders:
  - `dataset_access.py:138` `_layers` already reads through `read_file` with `STORED_CONFIG`; after
    AF, a dataset's four files are refused one way.
  - `startability.py:142` catches `FileUnreadableError` and `ValueError` alike, and answers
    `StartBlock.UNREADABLE`.
  - The readers `prompts.py:27, 44`, `authored.py:133`, `jobs/mint.py:689`,
    `pipeline_resolve.py:458, 1149`, `origin.py:192` and `dataset_access.py:486-487` catch neither.

**Digest:** `infrastructure` is plumbing; equal (probed).

**Served deltas, named:** a malformed `task.md`, `origin.yaml` or task-context file:
- REST: was 500 `internal_error` "Internal server error". Becomes 500 `stored_config_invalid`,
  "`<path>` is unreadable by this build (`<reason>`). Fix the file.", with `details.path` and
  `details.reason`.
- CLI: was a traceback, because `campaign_runner.py:86` catches only `PotterError`. Becomes
  `Error: …`, with the `--json` envelope.
- MCP and `embedded_run` get the same typed refusal: it is raised below every door.
- Unchanged: the dataset listing still answers `UNREADABLE`.

**Equals-main checks:**
- Full gate.
- `--digests` byte-equal.
- `--against <main home>`: decisions and requests unmoved.
- `git grep -n "_named" -- promptpotter/infrastructure` is empty.

**Estimate:** +7 / −18 (salvage numstat); the ledger −2.

**Risks:** none known. Salvage overlap: m17 (the source), m14a-2 (`dataset_access.py`).

### AG — the wallet's and identity's files read through `read_file`, and the install survey serves a torn grant

**Rows:**
- "**A. A file read past its store's one reader**", bullet 1 (1212-1217). After AF and AG it names
  only `intelligence/hard_sample_sorter.py::read_hard_samples`. That holder is reached by capo-pobb and
  potter, so it moves to the re-key arc (inventory below). The bullet's re-test becomes
  `git grep -n "read_json_optional(path)" -- promptpotter/application/intelligence/hard_sample_sorter.py`
  is empty.
- "**One torn `grant.json` fails the install-spend survey**" (255-259). Deleted.

**Owns (8 hand-edited):**
- `promptpotter/infrastructure/store/wallet_store.py`, `promptpotter/infrastructure/store/read_model.py`,
  `promptpotter/infrastructure/store/account_spend.py`, `promptpotter/infrastructure/llm/spend_book.py`;
- `promptpotter/infrastructure/identity/{default_claim.py, blocklist.py, grants.py}`;
- `promptpotter/application/jobs/install_spend.py`;
- ledger hunks 1212-1217 and 255-259.

**Greps:**
- `GrantStore(\|TenantApiKeyStore(\|grant\.load()\|api_keys\.load()\|registered_user_id` in
  `promptpotter tests scripts`.
- `def _read\b` in `promptpotter`: 10 hits. Only `blocklist.py:33` and `grants.py:39` are copies of
  `read_file`. `jobs/registry.py:338` reads staged files through `open_text_robust`, a different read.
- `read_model import\|wallet_store import` in `promptpotter/infrastructure/store`: `read_model.py:34`
  imports `wallet_store.HeldCharge`. So once `wallet_store` imports `read_model`, the two import each
  other.
- `held_tokens` in the full path set: defined at `read_model.py:594`, read by `spend_book.py:52, 1094,
  1098` and `account_spend.py:41, 67`. Both of those already import `HeldCharge` from `wallet_store`.
- `FileKind.STORED_CONFIG\|FileKind.STORE\b` in `promptpotter`, and the `FileKind` block of
  `shared/errors.py:356-384`.
- `install_spend\|AccountUsage\|grant.json` in `tests`: none.

**Holders:**
- `wallet_store.py:51-53` (`GrantStore.load`) becomes
  `read_file(self.path, CouponGrant.model_validate_json, FileKind.STORED_CONFIG)`.
- `wallet_store.py:77-80` (`TenantApiKeyStore.load`) becomes the same over `HELD_KEYS.validate_json`.
  The comment "Optional, not tolerant" stays true. Its `read_bytes_optional` and `read_json_optional`
  imports at :21-22 go.
- `default_claim.py:40-46` (`registered_user_id`) reads
  `read_file(marker_path, DefaultClaim.model_validate_json, FileKind.STORED_CONFIG)`.
- `blocklist.py:33-44` `_read` becomes `_blocked`, which is the salvage's name. It is read at :51 and
  :95. Its body is the `read_file` call it hand-copied (byte-equal answer).
- `grants.py:39-50` `_read` becomes `_grants`, likewise. It is read at :54, :70, :104 and :134.
- `install_spend.py:38` catches `(OSError, LedgerUnreadableError, FileUnreadableError)`. The survey's
  bad-row mechanism already exists (`_unreadable_row`, :44). Census 3's "new mechanism first" for this
  row was wrong.
- **The import cycle, at its root:** `read_model.py:594 held_tokens` moves beside `HeldCharge` in
  `wallet_store.py`, and `read_model.py:34`'s import goes.
  - `spend_book.py:52` and `account_spend.py:41` drop `held_tokens` from their `read_model` import
    and add it to their `wallet_store` import, one line each.
  - Salvage/m17 instead put `HeldCharge` under `TYPE_CHECKING`, a type-only import standing in for a
    misplaced function. That is the patch, not ported.
- **The kind:** `STORED_CONFIG` (500, "Fix the file."), as the salvage chose and `blocklist.py`,
  `grants.py` and `dataset_access.py` already do. Its own comment fits: "the caller cannot fix a file
  the server persisted". `STORE` (409, "No reader repairs it.") would tell the operator that no fix
  exists.
- Not holders:
  - `account.py:325, 374`: surveys already catching `FileUnreadableError`.
  - The readers `coupon.py:98, 296`, `quota.py:325, 398, 772, 999`, `free_tier.py:43`,
    `probe_reasoning.py:83`, `routers/auth.py:216` and `admin_bot.py:119, 176`: none catches.
  - `stores.py:166-167`: the constructors.

**Digest:** all `infrastructure` but `install_spend`, which is unreached; equal (probed with the
salvage's `TYPE_CHECKING` form). The `held_tokens` move is `infrastructure` to `infrastructure`.

**Served deltas, named:**
- A torn `grant.json`, `api_keys.json` or `identity/default_claimed.json`:
  - REST: was 500 `internal_error`. Becomes 500 `stored_config_invalid` naming the path, on every read
    or command that loads it: the coupon face, admission, sign-in's host check, account reads.
  - CLI: was a traceback. Becomes `Error: …`.
  - MCP and `embedded_run` get the same typed refusal.
- The admin bot's install survey (`admin_bot.py:119`): one torn `grant.json` failed the whole survey.
  Now it is one row whose `unreadable` reads `FileUnreadableError: <path> is unreadable …`, and every
  other account is listed.
- Blocklist and grants: none. They are byte-equal: same kind, same `validation_reason(exc, shown=1)`.

**Equals-main checks:**
- Full gate.
- `--digests` byte-equal.
- `--against <main home>`: decisions and requests unmoved.
- `git grep -nE "read_json_optional|read_bytes_optional" -- promptpotter/infrastructure/store/wallet_store.py promptpotter/infrastructure/identity`
  is empty.
- `git grep -n "wallet_store" -- promptpotter/infrastructure/store/read_model.py` is empty.
- Proof run, scratch only: tear one `grant.json` in a scratch home with two accounts. `read_install_spend`
  lists both, one unreadable.
- No new test. A torn survivor fails loudly today, so tests/CLAUDE.md's rule 2 (SILENT) does not admit
  one.

**Estimate:** about +44 / −48:
- `wallet_store` +9 / −6;
- `read_model` +0 / −5;
- `default_claim` +6 / −5;
- `blocklist` +8 / −17;
- `grants` +11 / −17;
- `install_spend` +1 / −1;
- `spend_book` and `account_spend` ±2 each.

The ledger: −5, and the bullet rewritten ±0.

**Risks:**
- `spend_book.py` is M12's file. AG changes one import line, so M12 rebases on it.
- Salvage overlap: m17 (the source), ea-8 (`install_spend.py`, `default_claim.py`), m2-2
  (`read_model.py`).

### AH — an unrowed campaign file is served as its own leaf, never as loop trace

**Row:** "**`storage_report.py::_leaf_of` files an unrow'd campaign file under `trace`**" (1021-1025).
Deleted.
- Census 2 left it out only because w2x1 was filing it, and it is now filed.
- The row's second clause, "a row with no writer is never detected", has no action. It goes with the
  row. If the orchestrator wants it kept, that is a one-line row of its own (see the order notes).

**Owns (2 hand-edited, +2 regenerated):** `promptpotter/application/maintenance/storage_report.py`,
`promptpotter/infrastructure/store/data_root.py`, `webapp/lib/api/types.generated.ts` and
`docs/specs/openapi.generated.json` (both regenerated), and ledger hunk 1021-1025.

**Greps:**
- `Leaf\.\|in Leaf\b\|LEAF_INFO\|class Leaf` in `promptpotter scripts tests`.
- `LeafSplit\|LEAF_INFO\|leaf-split` in `webapp`.
- `"trace"\|"leaf"` in `docs/specs webapp/lib/api`.
- `-i unclassified` in the full path set: only the row; no collision.
- `campaign_row(\|no row names` in `promptpotter`.
- `campaign_storage\|workspace_storage` in `promptpotter/presentation promptpotter/application`.

**Holders:**
- `storage_report.py:31-34` `_leaf_of` answers `Leaf.UNCLASSIFIED` for a `None` row. Its docstring "A
  file no campaign row names counts as loop trace." becomes "… is unclassified".
- `data_root.py:297-305`: `Leaf` gains `UNCLASSIFIED = "unclassified"`, last.
- `data_root.py:315-327`: `LEAF_INFO` gains its row, with `loop` False. Its writer is unknown, so it is
  never claimed as the loop's.
- `data_root.py:328` (`assert LEAF_INFO.keys() == set(Leaf)`) holds.
- Regenerated:
  - `types.generated.ts`: the `Leaf` union, the `LEAF_INFO` mirror (:5514-5521) and its `WORD_ORDER`
    entry (:5582);
  - `openapi.generated.json:8588-8595`, the enum.
- Not holders:
  - `campaign_store/store.py:131-134 _keepsake` reads a `None` row as "not a keepsake". Unchanged: an
    unknown file is no result.
  - `landing.py:38, 52` and `reset.py:39`: `land-data` already refuses an unrowed file.
  - `LeafSplit.tsx` renders every served leaf through `LEAF_INFO`, so it needs no edit. Its readers
    are `AccountStorageTab.tsx:68` and `RowHoverCard.tsx:145`.
  - Every door serves the split through `read_params.py:721, 736`, so all five read one answer.

**Operator decision: the shape.** Two options:
- **Option A (recommended): a `Leaf` member**, worded server-side in `LEAF_INFO`, served last.
  - The webapp is untouched: only the generated pair moves.
  - Every split gains an "Unclassified 0 B" row on a landed root, the sidebar hover card included. That
    is the served contract, "every leaf once, an empty one at 0".
- **Option B: `LeafBytes.unclassified: int`** beside `leaves`, summed into `total`, and rendered by
  `LeafSplit` only when non-zero.
  - Its words would live in the webapp, a second home for a served word.
  - It is quieter on the phone.

**Wording decision (label, description):**
- **Option 1 (recommended):** `"Unclassified"`, `"A file no data-root row names: no leaf claims it,
  and `land-data` refuses it."` It is the row's own word, and it names the remedy.
- **Option 2:** `"Unknown files"`, `"Written by something the storage map does not name; never counted
  as the loop's."`

**Digest:** `storage_report` is unreached and `data_root` is plumbing; equal (probed).

**Served deltas, named:**
- Every `LeafBytes.leaves`, in the campaign-storage read and in each `WorkspaceStorageEntry.on_disk`,
  gains `{"leaf": "unclassified", "bytes": 0}` last.
- On a tree holding an unrowed file, its bytes move from `trace` and `loop` to `unclassified`. Probe:
  7 bytes, `trace` 7 and `loop` 7 before; `unclassified` 7 and `loop` 0 after.
- The `Leaf` / `LEAF_INFO` mirrors gain one entry, and the account storage tab and the hover card one
  row.

**Equals-main checks:**
- Full gate, the `--web` lane included.
- `--digests` byte-equal.
- `--against <main home>`: decisions and requests unmoved.
- `git grep -n "counts as loop trace" -- promptpotter` is empty: the row's re-test.
- A phone screenshot of the hover card and the account storage tab.

**Estimate:** +3 / −2, and about +4 / −1 regenerated; the ledger −5.

**Risks:** a phone layout tightened around five rows. Salvage overlap: m17, m2-2, ea-8 (`data_root.py`).

---

## Chain 2

### AI — the controlled leg prints a count no race moves, and a kept green is the key that built it

**Rows:**
- "**The controlled offline leg's arm-memory count moves between identical runs**" (1485-1490).
  Deleted, including its "Separately, `--controlled` stops at `_variant_edit`" clause. That clause is
  stale:
  - b5663dafc added the `TALLY` cite (`offline_run.py:224-228`, `cited_panel`);
  - `gate.py:1148` runs the `--controlled` leg, with the `controlled` workspace's foreign potter
    campaign (`foreign=args.child == CONTROLLED[0]`, `offline_run.py:1647`; the branch at :1050-1053),
    green on every arc of batches 6 and 7.
- "**A kept offline-run green counts a workspace by its directory**" (208-212). Deleted.

**Owns (1 hand-edited):** `scripts/offline_run.py`, and ledger hunks 208-212 and 1485-1490.

**Greps:**
- `still_green\|holds_run\|claim_home\|STAMP\|kept\.` in `scripts`.
- `class LastGreen` in `scripts/last_green.py` (read, not edited). Its docstring reads "One store per
  clone: a key is content, so a green any worktree recorded holds in every other". It keeps the
  newest 8 keys per check (`_KEPT`).
- `memory holds\|scan_ledger_answers\|file_answers` in `scripts promptpotter`.
- `controlled` in `scripts/gate.py` and `docs/developer/offline-run.md`.

**Holders:**
- `offline_run.py:1078-1085` (`run_controlled`): ports salvage/m1-2's hunk.
  - `walked = filed(cid)` serves both the assertion and the print.
  - The print reads `arm {name}: memory holds only its own {len(walked)} filed answers, none of
    {len(foreign_only)} foreign`.
  - A comment names the race.
- `offline_run.py:1405-1406` `still_green` also asks that the home's stamp names this key for the
  workspace.
  - Today worktree A's key stays among the clone's 8 greens after worktree B rebuilt the shared home,
    so A reads B's workspace as "unchanged since green".
  - `:1739` (the stamp dict) carries `keys: {name: key}`. `:1416-1424 claim_home` writes it.
  - `:1878` (the green record) re-stamps each workspace that finished green. The held branch at
    `:1744-1747` rebuilds without re-claiming.
  - `:1409-1413 holds_run` is unchanged.
- Not holders: `last_green.py` (excluded by the brief, and not edited: the key is right, the home's
  claim on it was missing); `docs/developer/offline-run.md` (names neither count nor stamp).

**Decided by measurement, not asked.** The arc first runs `--controlled` twice on one tree.
- If the filed count prints equal: ship the port.
- If it moves too (the row says a coalesced cell files nothing, so a race could still reach it): print
  no count. Keep the subset assertion, and refuse an empty memory and an empty foreign set, so the
  check is never vacuous.

**Digest:** `scripts/` is not hashed; equal.

**Served deltas, named:** none. The leg's stdout line changes. A kept green rebuilds where the home
holds another tree's workspace.

**Equals-main checks:**
- Full gate: both offline legs green.
- `--digests` byte-equal.
- Two `--controlled` runs of one tree print one count, per the measurement above.
- The row's re-test: two worktrees with different engine sources share one `PROMPTPOTTER_HOME`, and
  the second rebuilds. Main's checkout and the arc's worktree serve.

**Estimate:** about +14 / −6; the ledger −11.

**Risks:**
- The arc edits the instrument that proves equals-main. Its own proof is the two legs and the
  re-test, never `--against` alone.
- Salvage overlap: m1-2 (the source), m17, ea-8, en-9, m2-2, m3 (`offline_run.py`).

### AJ — the webapp's pages forbid inline style

**Row:** "**The Vite build leaves five things standing**" (913-918). Its CSP sub-bullet (918) is
deleted, and "five" reads "four".

**Owns (2 hand-edited):** `promptpotter/presentation/api/middleware/headers.py`,
`webapp/e2e/harness.ts`, and ledger lines 913 and 918.

**Greps:**
- `style-src\|unsafe-inline\|_WEBAPP_CSP\|Content-Security-Policy` in the full path set: `headers.py`
  and the row only. No doc, test or deploy file states the policy.
- `createElement("style")\|insertRule\|adoptedStyleSheets\|<style\|setAttribute("style"` in
  `webapp/components webapp/app webapp/lib`: none.
- `style` in the three `webapp/app/**/*.html` entries: none.
- `dangerouslySetInnerHTML`: `FileViewer.tsx:114` only, through `renderMarkdownSafe`.
- The runtime dependencies in `webapp/package.json`: `chart.js`, `react-chartjs-2`,
  `@tanstack/react-virtual`, `marked`, `react`, `react-dom`.

**Holders:**
- `headers.py:15-20` ports salvage/m1-2's hunk.
  - `_WEBAPP_CSP`'s `style-src 'self' 'unsafe-inline'` becomes `style-src 'self'`.
  - The comment "Styles stay 'unsafe-inline' for React's `style=` props" is false: a `style` prop
    sets the CSSOM, which `style-src` does not govern. It becomes the salvage's sentence.
  - `_DOCS_CSP` (Scalar) keeps `'unsafe-inline'`.
- `e2e/harness.ts:60` ports salvage/m1-2's 5 lines. The console listener files any
  `Content-Security-Policy` message as a `csp` problem, so the cold tier fails on a refused style
  from then on. `Problem.kind` is a `string` (:29), so it needs no type edit.

**Digest:** `presentation` is unreached; equal (probed).

**Served deltas, named:**
- Every webapp page's `Content-Security-Policy` header drops `'unsafe-inline'` from `style-src`.
  `/docs` and `/api/v1/` are unchanged.
- A `<style>` element or a `style` attribute arriving in markup, such as raw HTML in a rendered
  markdown file, is now refused by the browser.

**Equals-main checks:**
- Full gate: the `--web` lane and the cold Playwright tier, which the harness now fails on any
  violation.
- `--digests` byte-equal.
- Phone screenshots of the dashboard, the candidates surface, a chart and the account tab, against
  main's.

**Estimate:** +8 / −2; the ledger −1.

**Risks:** a dependency that injects a `<style>` element at runtime (none found in our source; the
`node_modules` trees were not read) breaks styling silently on the box. The harness check is the guard.
Salvage overlap: m1-2 (the source).

### AK — `eslint-plugin-react` restores the rules `eslint-config-next` took with it

**Row:** "**The Vite build leaves five things standing**" (913-918). Its ESLint sub-bullet (917) is
deleted, and "four" becomes "three". `@next/next/*` cannot return, since Next.js is gone, so the
sub-bullet goes whole.

**Owns (3 hand-edited, +1 regenerated, + finding fixes):** `webapp/eslint.config.mjs`,
`webapp/package.json`, `webapp/package-lock.json` (regenerated), any file a restored rule flags, and
ledger lines 913 and 917.

**Greps:**
- `eslint` in `webapp/package.json`: `eslint ^9`, `eslint-plugin-jsx-a11y`, `eslint-plugin-react-hooks`,
  `typescript-eslint`; no `eslint-plugin-react`.
- `target="_blank"` in `webapp/components webapp/app`: 4 sites, each with `rel`
  (`TopStrip.tsx:22-23`, `ConnectorInspector.tsx:78-79`, `ConsentGate.tsx:50, 57-58`,
  `Sidebar.tsx:146-147`).
- One-line `.map(… => <X` without `key=`: 0.
- `forwardRef\|memo(`: 11 sites for `react/display-name`.
- `react/no-unescaped-entities` cannot be counted without the plugin.

**Holders:**
- `eslint.config.mjs:1-10` imports `eslint-plugin-react` and turns on `react/jsx-key`,
  `react/no-unescaped-entities`, `react/display-name` and `react/jsx-no-target-blank`. It sets
  `settings.react.version` to `"detect"`.
- `package.json` adds the devDependency.
- `package-lock.json` is regenerated, in WSL with npm 12.0.2 (the lockfile rule).

**Decided:** `eslint-plugin-react` restores the dropped rules (operator, 2026-11-06; roadmap M1). The
decision names the package; the install commands run in the arc and are printed verbatim.

**Digest:** webapp-only; equal.

**Served deltas, named:** none, unless a finding's fix moves markup. Each such fix is shown by a
before/after screenshot.

**Equals-main checks:**
- Full gate, the `--web` lane.
- `npm run lint` green with the four rules at `error`.
- `npm ci` from the new lock is clean in WSL.

**Estimate:** about +8 / −1 in the config, +1 in `package.json`, a few hundred regenerated lock lines;
the finding fixes are unmeasured. The ledger −1.

**Risks:**
- **Finding count.** If the restored rules flag findings in more than 10 files, the arc lands the
  plugin with the rules that are clean. Each other rule becomes its own arc with its fixes, never a
  `warn`.
- **The lockfile.** It is written on Linux with npm 12.0.2. Never run `npm ci` in a junction worktree:
  its `node_modules` is the main checkout's. The main checkout needs one `npm ci` after the merge,
  which is the operator's.
- **Salvage:** m1-2 notes "eslint-plugin-react was never added". Nothing to port.

---

## Chain 3

### AL — the check-in picklist names each open check-in apart, and a dead latch row goes

**Rows:**
- "**Seen in the phone-first setup pass**", the sub-bullet "The check-in picklist button's
  accessible name runs slug and meta together; two open check-ins share one slug" (1591-1592).
  Deleted.
- "**Seen building the prompt workspace**", the sub-bullet "`PipelineSetupSection` picks a default
  node in a `useEffect` with a ref latch" (1600-1601). Deleted as a dead claim:
  - `PipelineSetupSection.tsx` (111 lines) holds no `useEffect` and no `useRef`, and
    `node-surface/` holds no `useEffect`;
  - c714ec217 and 73b501cec reshaped it.

**Owns (1 hand-edited):** `webapp/components/ingest/DatasetPickList.tsx`, and ledger hunks
1591-1592 and 1600-1601.

**Greps:**
- `aria-label\|<button\|ingest-pick` in `webapp/components/ingest/DatasetPickList.tsx`.
- `Discard the\|ingest-pick-btn\|Open check-ins\|ingest-pick-name` in `webapp tests docs .claude`.
- `useEffect\|useRef` in `PipelineSetupSection.tsx` and `webapp/components/shell/node-surface/`: none.
- `function fmtAgo` in `webapp/lib/format.ts:216`: it answers `just now`, `3m ago`, `2h ago`,
  `4 days ago`.

**Holders:**
- `DatasetPickList.tsx:71-79`: the reopen button's name is its two spans, `{d.label}` and
  `{fmtAgo(d.span.last_at)}`. Chromium separates flex items with a space; an AT that does not reads
  `termnorm-demo3m ago`. Two drafts on one dataset share `d.label`.
  - It gains an explicit `aria-label` that leads with the slug.
- `DatasetPickList.tsx:83`: the Discard button's `aria-label` gains the same distinguisher.
- Not holders:
  - `e2e/cold/pools-checkin.spec.ts:28-30` and `e2e/spend/run.spec.ts:45-47` find the reopen button
    by `name: new RegExp(`^${slug}\\b`)`. A slug-first name keeps them;
  - `chat.css:70-73`.

**Wording decision.** Two drafts:
- **Option 1 (recommended):**
  - reopen: `` `${d.label}, edited ${fmtAgo(d.span.last_at)}` ``;
  - discard: `` `Discard the ${d.label} check-in edited ${fmtAgo(d.span.last_at)}` ``.
- **Option 2:**
  - reopen: `` `Reopen ${d.label} (${fmtAgo(d.span.last_at)})` ``;
  - discard: `` `Discard ${d.label} (${fmtAgo(d.span.last_at)})` ``.
  - It breaks both e2e locators' `^slug`, so they move to `/${slug}\b/`: 2 more files.

Two drafts edited the same minute still share a name. Telling them apart beyond that needs the slug
minting to differ, a served change no row asks for.

**Digest:** webapp-only; equal.

**Served deltas, named:** none. Accessible names only; no visible text moves.

**Equals-main checks:**
- `--web` lane.
- The cold Playwright tier: both specs reopen by slug.

**Estimate:** +2 / −1; the ledger −4.

**Risks:** low weight; this is A4's area (the check-in). No salvage overlap.

### AM — an error's message is never empty, so the activity feed words no fallback

**Row:** "**Seen in the parsimony pass**" → "Unverified, for the next change to each file" (1560-1566).
Its clause "`domain/activity.py::ActivityFeed` falls back to `"run error"` where `run_said` has none"
(1563-1564) is deleted. It is verified: two readers answer one empty message two ways.

**Owns (2 hand-edited):** `promptpotter/domain/phases.py`, `promptpotter/domain/activity.py`, and
ledger lines 1563-1564.

**Greps:**
- `run error\|round degraded` in `promptpotter webapp/components webapp/lib tests docs scripts`:
  `activity.py:374, 384` only.
- `ErrorRecord(\|\.raised(` in `promptpotter tests scripts`: one constructor, `ErrorRecord.raised`. It
  is called from `admission.py:126` and `llm/telemetry.py:339`.
- `def run_said` in `served_dashboard.py:362`: it serves `error.message` as the label (:372).

**Holders:**
- `phases.py:887`: `message, _, detail = (str(exc) or type(exc).__name__).partition("\n")` mints
  `message == ""` for an exception whose text opens on a newline. It becomes `str(exc).strip()`, so the
  message is the first non-blank line or the class name.
- `activity.py:384`: `label=record.message or "run error"` becomes `label=record.message`.
- Not holders:
  - `activity.py:374` (`"round degraded"`) is a `RoundWarningRecord`'s, which the row does not name.
  - `run_said` needs no edit.

No `min_length` is added to `ErrorRecord.message`: that would refuse an old ledger line, a run-data
reshape.

**Digest:** `ErrorRecord` is not a reached unit of `domain.phases`, and `domain.activity` is
unreached; equal (probed).

**Served deltas, named:**
- For an exception whose text opens on a newline: the activity item's label was `run error` and the
  dashboard's pinned fault label `""`. Both now read the first non-blank line. Probe:
  `ValueError("\nsecond line")` gives `'second line'`.
- A first line with surrounding blanks loses them.

**Equals-main checks:**
- Full gate.
- `--digests` byte-equal.
- `--against <main home>`: decisions and requests unmoved.
- `git grep -n '"run error"' -- promptpotter` is empty.

**Estimate:** ±2; the ledger −1.

**Risks:** none known. Salvage overlap: ea-8 (`phases.py`), m2-2 (`activity.py`).

### AN — a child course keeps the index it was filtered by

**Row:** the same bullet (1560-1566). Two clauses are deleted:
- `iter_family_courses` reads each index twice (1564). Verified.
- `list_hierarchy` re-calls `line_config` per non-holder course (1564-1565). Verified not a defect:
  `hierarchy.py:449` reads each course's own `cycle_config`, and a fork's seed overlay can differ from
  its holder's (`pipeline_resolve.py:1055`).

**Owns (1 hand-edited):** `promptpotter/infrastructure/store/lineage_queries.py`, and ledger lines
1564-1565.

**Greps:**
- `iter_family_courses\|_read_index\|FamilyCourse` in `promptpotter tests scripts docs`.
- `def enumerate_cycles\|def _indexed` in `store/campaign_store/store.py:690, 876`. It is the same
  `read_cycle_index(cycle_dir, moment=None)` that `_read_index` (:62) calls.
- `def line_config\|line_config(` in `promptpotter/application`.

**Holders:**
- `lineage_queries.py:80` and `:98`: `manifest=_read_index(stores, hop)` and
  `manifest=_read_index(sandbox, hop)` become `manifest=index`, the index `reads.cycles` already holds
  and the filter above it read.
- `lineage_queries.py:102`: `[c for c in out if c.manifest is not None]` becomes `out`. A child now
  always holds its index.
- `lineage_queries.py:62` `_read_index`: its last reader is the root (:111), so it inlines there.
- Not holders: `cycle_reads.py:215` and `hierarchy_subtree.py:738` (the readers, unchanged);
  `family_ray_queries.py:161, 178` (reads `FamilyCourse`).

**Digest:** `infrastructure`; equal (probed).

**Served deltas, named:** none at one moment. A family read folds each child's ledger once, not twice.
A child whose ledger grows between the two reads now answers the index its filter tested.

**Equals-main checks:**
- Full gate.
- `--digests` byte-equal.
- `--against <main home>`.
- `git grep -n "_read_index(stores, hop)\|_read_index(sandbox, hop)" -- promptpotter` is empty.

**Estimate:** +3 / −7; the ledger −2.

**Risks:** the time-ray's ETag (`hierarchy_subtree.py:738`) hashes the same stores, so it is unmoved.
No salvage overlap.

---

## Rows left out, and why

- **Now picked, their blockers gone:**
  - Census 3's "new mechanism first" for the torn `grant.json` was wrong. `install_spend.py::_unreadable_row`
    already serves a bad row (AG).
  - `storage_report.py::_leaf_of`: census 2's only blocker was w2x1 filing it (AH).
  - Census A's `_read` copies: salvage/m17 holds the hunks, and they apply clean (AF, AG).
- **Unchanged since census 3:**
  - `embedded_run.__all__` wrappers and the typed REST routes: M6.
  - `useLeafDatasetName`, `ids.ts::rootCycleId`, `DefaultClaim.renamed`, `blocklist_audit` /
    `grants_audit`, `slug_exists`, the candidates-surface formatters: census 1's blockers.
  - `ActiveSessionResponse`: a served reshape.
  - `TokenUsageRecord.cached` vs `key_source is None`: M11's wipe.
  - DSPy pricing split: moves metered spend.
  - `index_terms`: moves request order.
  - TermNorm defaults: M18.
  - Unregistered `backend_type`: more than 10 files (M4b).
  - `Session.langfuse`: shares `tracing/bridge.py` with `OBS_ENABLED`.
  - `FAMILIES` null pane, `filesRead` URL, `.prompt-snippet-input`: a design fork, a descend, a render
    change.
  - The CLI's `ERROR:` prefix: Q3, at 20 sites.
  - Wave B forwarders: not dead.
- **→ re-key arc:** every row in the inventory below, now including census A's last holder,
  `hard_sample_sorter.py::read_hard_samples`.
- **A served-answer or decision change, not a cleanup:** `campaigns_on_dataset`, `turn_scalars`, the
  per-cell criterion, `_git_head`.
- **A new mechanism first:**
  - `quota.py::sends_on` raising `NoApiKeyError`: reached;
  - `ReadResult` literals: Q2;
  - `LaunchState.ledger`: M3.
- **Rides a medium arc's rows:**
  - the zero-cell origin-gate test: M7;
  - `RunStatus.of`'s `mark: str`: M11;
  - `CheckinLoadingWindow`'s `setInterval`: Q2.
- **A survivor field:** `CouponGrant.granted_at`: a landing migration, and M12 decided that a re-grant
  counts from it.
- **Census A, the rest:**
  - `tenant_dataset_store.py::banks_sharing` (§ A bullet 2): it shares AF's file. Its asked-for tests
    fail tests/CLAUDE.md rule 2, since a survey that fails whole is loud. It is M17's; next census,
    after AF lands.
  - Census B (`OIDCConfigError`, `PackRefusal`, `LedgerUnreadableError`, `SlugTakenError`): M14a, whose
    salvage tripped the stop rule.
- **The "Unverified" bullet's other five:**
  - `list_cycle_files` walks `.runtime/`. `iter_files(skip=)` prunes the top level only, so the fix is a
    second spelling of `_shown`'s hidden set beside it, for little gain.
  - `CriticalAlertBanner`, `ViewTabs.show`, `FileTree`'s 2 s poll and the `/hierarchy` replace: U5 /
    M16 / Q2.
- **Webapp, low weight or a design call:**
  - `lib/theme.ts`, `STORAGE_KEYS`, `.setup-step`, `CandidateRow` / `useActivePointer`;
  - `title=` as teaching prose (`NodeDetail`, `OptimizerCard`, `MenuCheck` from `CandidatesToolbar`):
    where teaching prose goes is a `/design` call;
  - the packs' `tags` repeating Source and Method: asset text across many pack files;
  - the `lib/` barrel dodge in `useRegistry.ts` / `workspace.tsx`: a cycle break of unknown reach.
- **Seen building the read catalogue:**
  - `GET /auth/me` arrives an account: an open box has no sign-in callback to move it to, which is a
    design fork;
  - the three `edit-draft-campaign` routes: M6.
- **Milestone-locked, small in themselves but named in a milestone's scope:**
  - the walk tier, the `--staged` checks, `Check.after` and the worktree push: gate rows, excluded;
  - the TS docstring generator: `build_ts_types.py`, excluded;
  - the parent-counting addresses: M15;
  - the scripts' and the store's two writers: M17, `scripts/processes.py` excluded.
- **Excluded by the brief:**
  - A, B, D1, D2, E–T, V, W, Y, Z, AA–AE;
  - every `scripts/gate.py`, `last_green.py`, `processes.py` and `build_*.py` row;
  - the w2x6 files.

## Shared-file and order notes

- **The ledger** is the only file two arcs touch, in the hunks above. AF and AG, AJ and AK, and AM and
  AN share lines, so each pair runs in its chain's order.
- **AK's finding fixes** may land in any webapp file. Run AK after:
  - AL, since `DatasetPickList.tsx` is AL's;
  - the batch-7 fix pass, which edits `PipelineFlow.tsx:494`.
- **The generated pair** is touched only by AH (`types.generated.ts`, `openapi.generated.json`).
  `package-lock.json` is AK's alone.
- **M12's file.** AG changes one import line of `spend_book.py`, so M12 rebases on it.
- **Orchestrator-only edits** (docs-only commits; rebase on the batch-7 fix pass, which edits
  `roadmap.md:164`):
  - `roadmap.md:160` (M1): drop "the controlled leg's count" once AI lands. The Vite leftovers stay
    in scope at three items once AJ and AK land.
  - `roadmap.md:178` (M17): drop "census A's `_read` copies" once AG lands. Its last holder rides the
    re-key arc.
  - Ledger line 239, "**Re-test:** a breaching and a floor arm get different inner campaign ids.", sits
    under the origin-label row. It belongs to the L4-arm row at 224-226.
  - Ledger 1246 (§ Batch E D) says `api/deps.py::_NAMED` restates `ReadKind`. It derives it
    (`deps.py:97`, `{k.value: k for k in (*CommandKind, *ReadKind)}`). Drop the name.
  - If wanted: AH's dropped clause as its own row. "**A `CAMPAIGN_ROWS` row with no writer is never
    detected**"; no action proposed yet.
- **Smells seen in passing.** One line each, for the orchestrator to file. The first three repeat
  census 3's and are still unfiled on batch/7:
  - `RunPhaseInfo.parked` is read for PAUSED (`run-phase.ts:43`) and TERMINAL/DETACHED
    (`phases.py:443`) only. QUEUED's, STARTING's and GATE's words are served and read by nothing.
  - `persistence-and-state.md` omits `jobs/ended/`.
  - `interlock.py::this_producer` mints a producer file on a read.
  - `llm/pricing.py:71` binds `CACHE_PATH` at import (batch-6 review, rides M17), not in the ledger.
  - `presentation/cli/commands/verbs.py:746`: `offer-extras` says "Read-only" but now creates a
    missing root (batch-6 review). w2x6 does not own it.
- **Salvage overlaps**, for the brief writer to check each tag before the arc:
  - m17: AF, AG, AH, AI;
  - m1-2: AI, AJ;
  - ea-8: AG, AH, AI, AM;
  - m2-2: AG, AH, AI, AM;
  - m14a-2: AF;
  - en-9 and m3: AI.
- **Batch suggestion:**
  - batch 8: AF, AI, AL (the deletions, one per chain);
  - batch 9: AG, AJ, AM;
  - batch 10: AH, AK, AN.
  - AK waits on AL and on the batch-7 fix pass.

## Re-key arc inventory

One line each: the row, its holder on batch/7, the digests it moves. "All five" means the five
treatment digests. The estimator moves in none.

- **A refusal edit re-keys** (183-190): `nodes.py:392, 405` (`source_digest`) and
  `source_scan.py:161` (`optimizer_prompt_shapers`). Moves potter, capo-pobb.
- **The errors' `.provider`** (arc/w2x3 7aeab5219's cut): `shared/errors.py:245, 282`. Moves potter,
  capo-pobb.
- **`OBS_ENABLED`** (1026-1029): `config/settings.py:202`, `tracing/bridge.py:51`, `.env.example:54`,
  `docs/operations/observability.md:3`. Moves potter, capo-pobb.
- **The stale-data outcome enum** (793-798): `scoring/sample_measurement.py:762-807`,
  `domain/scoring.py:267-269`, `views/render/sample.py:153-171`. Moves potter, capo-pobb.
- **`PipelineSchema` name/version** (123-128): `domain/pipeline_parsing.py:518-519` (hashed whole for
  all five), `domain/pipeline_schema.py:721-722`. Moves all five.
- **The unknown-slot refusal** (191-193): `potter/dispatch/injections/registry.py:76, 85`
  (`validate_template`), `bench/task_context.py:225`. Moves potter, capo-pobb.
- **The two word tables** (544-547):
  - `domain/paired_reading.py:71` (`READING_STATE_KIND`) and `domain/origin_panel.py:47`
    (`ROUND_ADVANCE_STALL`): both reached by all five;
  - `evidence/head_to_head.py:74, 94` (unreached) and `library.py:41`.
  - Moves all five.
- **`CatchUpPool`** (538-540): `potter/race.py:81`, `nodes.py:657`, `capo/members.py:256` (whole),
  `gepa/members.py:200` (whole), `domain/validators.py` (`CatchUps`, imported at `nodes.py:39`). Moves
  potter, capo-pobb, capo, gepa.
- **The `FileKind` remedies** (93-97): `shared/errors.py:373-379, 428`. Moves potter, capo-pobb.
- **The origin label** (232-238): `promptpotter/connectors/__init__.py:57`,
  `shared/plugin_registry.py:17, 77` (hashed whole for all five). Moves all five.
- **The fork-summary kind** (157-159): `runner/output.py:177, 190`. Moves potter, capo-pobb.
- **The swallowed round checkout** (260-262): `scoring/closed_rounds.py:173`. Moves potter, capo-pobb.
- **`CapoInit.refuse_origin`** (1192-1194): `capo/members.py:495` (whole). Moves capo, capo-pobb.
- **The `[#N]` slots** (227-231):
  - `potter/dispatch/injections/catalogues.py:154, 165`, `layer_state.py:234`,
    `panels.py:441, 606, 780, 884, 1022, 1043`;
  - all hashed whole.
  - Moves potter, and capo-pobb through its whole set.
- **The money text** (839-846): `panels.py:1174-1175` (whole). Moves potter, capo-pobb. It waits on
  the operator's sub-cent rule.
- **The three lift bars** (832-838): `escalation/state.py:105, 113`, `election.py:31`,
  `panels.py:1082, 1089`. Moves potter, capo-pobb, and decisions (M19).
- **The review.md / log.md writers' reach** (1138-1146): `runner/output.py` (`write_review_md`,
  `write_log_md`). Moves potter, capo-pobb.
- **The dispatch modules hash whole** (1593-1596): `dispatch/bundle.py:35`, `l1_wire_schema.py:39`,
  `layout.py:14`, `prompts.py:25`, `schemas.py:33`. Moves potter, capo-pobb.
- **A refused L4 arm's identity** (224-226): `potter/dispatch/layout.py:382` (`layout_levers`). Moves
  potter, capo-pobb, and decisions.
- **`prompt_edits`** (772-782): `domain/search_point.py:55`; `NodeConfig` is reached by all five.
  Moves all five.
- **Census A's last holder** (1212-1217, after AG):
  `intelligence/hard_sample_sorter.py:120` (`read_hard_samples`, `read_json_optional` then
  `model_validate`). Moves potter, capo-pobb.

**After census 4's arcs, about 256 top-level ledger rows stay open.** `grep -c "^- "` counts 260 on
batch/7, and AG, AH and AI delete 4.
- About 25 are small-arc-sized. Each is held by a named decision, a served change, a survivor
  migration, an excluded file or low weight (§ Rows left out).
- 21 ride the one re-key arc.
- 8 are Blocked.
- The other ~200 are milestone work: M1–M20 and the tuning and head-to-head boards.
