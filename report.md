# addr-3 report: what an address hashes

Base `origin/land/w5i` d6ca65e3d (main + 19; `origin/main` dated Thu Nov 19 11:12:29 2026 +0100). Tip `arc/addr-3` b06e3be2d.

| link | commit | promptpotter +/− |
|---|---|---|
| 1 cfg: a declaration is a fold patch | c964f4cfc | +60 / −32 |
| 2 h2h: an arm is planned on its seat | 9405cb1c2 | +90 / −57 |
| 3 xconn: an arm's id is what it sends | **cut** (see Rows to file) | — |
| 4 potter: `k_max: 0` by default | 57b3a9e9b | +6 / −1 |
| 5 cache: one rule for the reply key | b06e3be2d | +5 / −7 |

**LEAN:** chain +160 / −96 (net +64), so the code grew. Most of the growth is the fix itself: `layer_fold.py::patch_between` (link 1) and the `RunSpec` / `ArmSpec` / `ArmSeat` split (link 2). I looked for dead code in the files I own and found none. `surface-ledger` is red: 112230 against main's 112145, and the base already stands at 112171.

**Re-tests (before → after):** `\*\*defaults\.` in authored.py 2 → 0 · `replicate=1` in promptpotter 1 → 0 · default potter `inert` couplings `shots_without_demo_pool` present → absent · `if request\.` in llm_reuse_cache.py 2 → 0 · link 3 not built.

**Gate:** full gate green except two checks. `surface-ledger` is red (above). `playwright` `e2e/walk/responsive.spec.ts` fails at the base too (3 failures at base, 2 at the tip; which cases fail changes from run to run). That matches the open § wave-5 · webapp row.

**Equals-main** (`--against` the base run): every optimizer's decisions and requests are UNMOVED except `potter/l4`. There, the only changes are the offline fake's critique nonce text in the inner runs' readouts. The request bodies are byte-identical apart from that echoed nonce. It moves at link 4, because the inner campaign's laid config now holds `k_max: 0`. No pick moved.

**Digest and id moves (old → new):**
- Root ids, link 1: capo 5ab1eeff68f0 → 4ca60e3eaa34; capo-pobb 3b9980bf45d5 → 1b625c9ba434; gepa 3aef52db8556 → c9d0bd805089; levi 0c4f463c704e → 502a5286f654; potter ffa35723c741 → bd7ca22c54c8.
- Root ids, link 4: potter bd7ca22c54c8 → a7196adab5a5.
- Digests, link 4: `estimator` 5df363abe6fd3105 → 739088ae320ebeb5; `behaviour_keys.json` estimator f252b1e3b5634f1f → 0364c2e03e843102. The L4 nonces move 6d697489 → 2ac07939 and so on. Link 4's commit body has this arrow backwards.
- Treatment digests are unmoved for every optimizer.
- Arm ids: none moved at link 2.
- Reply-cache keys, link 5: every banked reply whose request left a lever unset misses once.

**Wipes:** link 1 reshapes `RunDeclaration.loop` / `environment.target`, so run data is wiped. Link 5 empties the use of the banked reply cache (one miss each); there is no migration.

**Decided in-run:**
- `patch_between` emits a SUBSET/UNION list whole. That is correct while `defaults.yaml` holds none.
- `_pinned` drops an empty pinned map. This edits `domain/campaign.py`, outside my Owns and owned by no other chain.
- Link 2 also edits `domain/campaign.py`, `jobs/launcher/head_to_head.py` and `preflight.py`, all outside my Owns and owned by no other chain.
- The arm's record check now runs before numbering.

## Rows closed
- **A run declaration lays today's defaults by a second merge beside the one fold**
- **An arm is planned on a replicate it does not have yet**
- **Potter's demo axis is dead wherever a dataset holds out no demo pool**
- **An unset lever is spelled two ways in the reply-cache key**

## Rows to file
- **An identity re-key moves offline decisions: its holders sit outside `pipeline_resolve.py`** (cut from addr-3). An arm's id is `opt_search_point.py::OptSearchPoint.id`, which hashes `pipeline_params`. Those params carry the identity layer (`pipeline_resolve.py::ParamLayers._identity`), and the wire strips it again (`scoring/sample_measurement.py::_without_identity`, by `Session.identity_keys`). Root: a point's params hold what it sends. The identity is laid only into the archive key (`scoring/search_point_scorer.py`'s `node_configs`, `scoring/passes.py`, `datasets/loaders.py`), and `ParamLayers.recorded` keeps the identity a measured point ran under. Holders: `sample_measurement.py`, `initialization/session.py`, `search_point_scorer.py`, `passes.py`, `loaders.py`. **Rides with:** xconn 3. **Re-test:** `wip/xconn`'s run against its base moves only `backend_revision` leaves.

## Symptoms
- `assets/datasets/defaults.yaml`: a default campaign still raises the `inert` coupling `display_subset_relative_under_resubset`. The suspected root is the display defaults.
- `domain/layer_fold.py::patch_between`: a SUBSET list that `defaults.yaml` might one day hold cannot be widened by a declaration. The suspected root is that SUBSET has no replace.
