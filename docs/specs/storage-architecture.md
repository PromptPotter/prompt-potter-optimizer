# Storage architecture — store-once, recycle-bin archive, lean writers

> Store-once, the `measurements/` relocation, the `archive/` recycle-bin, the destructive
> `delete` + `--keep-results`, and the one-vocabulary MECE hover/cake/rollup (Connector / Loop /
> Dataset, Loop → State / Trace / History / Reports; rollup accounts for 100% of tenant disk)
> are the design in force. Arc 5 (legacy ledger-slim migration) ships as a standalone
> idempotent dry-run-default sweep, run on demand (~277 MiB reclaimable across the current
> legacy ledgers). Current-state operations reference:
> [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md).

## Why

The per-cycle tree lost control of its own size. Measured on a real cycle
(`data_justlogic_deductive_reasoning`, 400 samples):

| Surface | Bytes/cycle | Share | What it is |
|---|---|---|---|
| `.runtime/ledger.jsonl` | 2.0 MB | 49% | append-only event spine — **but 91% of it is two `phase=init` records each embedding the full 400-sample dataset (868 KB ×2) + a round-0 display record dumping `round_result` (142 KB)** |
| `langfuse/datasets/` | 1.8 MB | 44% | per-sample ground-truth mirror (400 files) — the langfuse observability shadow |
| `rounds/` | 164 KB | 4% | `round_NNNN.json` — serialized `OptSearchPoint`, the resume source of truth |
| `.runtime/cache/` | 76 KB | 2% | per-node I/O + pre-scoring checkpoint |
| reports + manifest | ~33 KB | 1% | `dashboard.json` / `index.json` / `log.md` / `review.md` |
| `langfuse/{traces,observations,scores}` | ~27 KB | 1% | the shallow optimizer-loop trace |

The unique loop signal is ~0.5 MB of 4.1 MB. The rest is the **same dataset stored four
times**: canonically in `datasets/{slug}/`, in the langfuse mirror, and **twice** inside
the ledger's init records. That is the defect this architecture removes.

## Decisions (the six, settled)

1. **Store-once.** Every datum has exactly **one canonical home**. The only permitted
   duplicates are (a) **folder-UI display views** — the human-readable files the file-tree
   dashboard is built from (`log.md`, `review.md`, `dashboard.json`), derived and safe to
   recompute; and (b) the **langfuse observability mirror** (see #5). **Nothing is
   triplicated.** Concretely: the dataset drops from 4× → 2× (canonical `datasets/{slug}/`
   + the sacred langfuse mirror) by removing the two ledger embeds.

2. **`archive/` is a recycle bin.** A new, separate directory that behaves like the OS
   trash: the `archive` lifecycle verb **moves** a campaign tree into
   `projects/{tenant}/archive/{campaign_id}/`, recoverable by `unarchive` moving it back.
   Recoverability is its only feature. The content-addressed measurement store — today
   mis-homed under `archive/measurements/` — moves out to its own canonical directory
   `projects/{tenant}/measurements/`: it is the DB core / cross-campaign cache, **not**
   trash. (Resolves a long-standing name collision: `archive` the verb and `archive/` the
   dir become the same concept.)

3. **Keep units that make sense; slim the writers.** No new export artifact and no forced
   splits. The keepsake survives a delete **in place** (the existing files, not a packaged
   bundle). The two round-file surfaces (`rounds/round_NNNN.json` = resume state vs
   `.runtime/cache/rounds/round_NNNN.json` = audit detail) stay because each unit has a
   distinct job — the work is purely **slimming the writers** so they stop embedding
   reconstructable bulk.

4. **No "compact" action.** This design makes campaigns born lean at write time, so there
   is nothing to reclaim afterward. A per-tenant **storage quota** is wanted later (the beta
   already gates spend + campaigns/day), but is not urgent — noted, not specced here.

5. **The langfuse mirror is sacred — do not touch it.** The whole `langfuse/` subtree
   (`traces`, `observations`, `scores`, **and** `datasets`) stays exactly as written. It is
   the permitted observability duplicate of #1. No broad filesystem restructure — the
   layout is fine. Only genuinely **unused or empty** surfaces get simplified, decided
   case-by-case at implementation.

6. **Migration is a one-time, optional backfill.** New campaigns are lean regardless. A
   single idempotent sweep can slim the ledgers of the ~147 legacy campaigns (strip the
   init dataset embeds); low priority, not blocking.

## The store-once ledger — what changes at the writer

The ledger stays in `.runtime/ledger.jsonl` and stays **load-bearing**: `EscalationFSM.from_ledger`
(`application/optimization/escalation/state.py:230`) rebuilds the optimizer's escalation
state on resume by folding every record, and forks inherit it via
`CycleEventLog.inherit_from`. It is **not** a display log and does **not** move to
`.goldmine`. What changes is only the **payload bulk** — content no reader consumes:

- **`phase=init` enter/exit records** embed the full resolved dataset (868 KB ×2). The
  escalation fold never reads it; the rewind scan (`scan_ledger_max_round_complete`) is
  payload-free. → Replace the embedded `dataset` list with a **reference**:
  `{slug, content_hash, n_samples}`. The canonical rows already live in `datasets/{slug}/`
  and the sacred langfuse mirror. *(Writer: the bootstrap init-phase emit; the init state is
  assembled in `application/bootstrap/session.py::new_session_state`. Pin the exact append
  site at implementation.)*
- **The round-0 `phase=round, event=display` record** dumps the full `round_result`
  (142 KB) — already persisted in `rounds/round_0000.json` and projected to `dashboard.json`.
  → Drop `round_result` from the display payload (keep the scalar round summary).
  *Verify at implementation that no projection rebuild reads it back — `LiveDashboardView`
  seeds from `dashboard.json` on resume, so this is expected to be write-only display.*

Net: ledger ~2.0 MB → ~50 KB; cycle ~4.1 MB → ~2.3 MB (the sacred 1.8 MB langfuse mirror
stays). Zero reader changes, zero resume risk — the escalation fold reads the small
`l2_context`/`l3_plan` exit payloads, which are untouched.

> **Deferred (not in this design): transient ledger.** Making the ledger fully transient
> (single-active, `.goldmine`-style) is possible but requires re-sourcing every durable
> reader — chiefly moving the escalation counters (`l1_stall_count`, `l3_round`,
> `*_at_entry`) into `rounds/round_NNNN.json` so `EscalationFSM` can rebuild from the round
> file instead of the ledger, plus the fork-inherit / lineage / decisions-display readers.
> The writer-slim above gets ~99% of the size win without that risk, so the transient-ledger
> arc is left for later and only makes sense **after** the ledger is already thin.

## Target tree

```
projects/{tenant}/
  campaigns/{campaign_id}/            # active campaigns
    campaign.json  log.md  hard_samples.json
    cycles/{cycle_id}/
      dashboard.json  index.json  log.md  review.md   # folder-UI display views (derived)
      rounds/round_NNNN.json                          # resume state (canonical)
      langfuse/{traces,observations,scores,datasets}/ # SACRED observability mirror — untouched
      prompts/                                        # rendered optimizer prompts
      .runtime/
        ledger.jsonl                                  # thin event spine (refs, not embeds)
        cache/rounds|candidates/                      # audit detail (full LLM I/O)
        streams/round_NNNN_p_best.jsonl               # PoBB telemetry
  archive/{campaign_id}/             # RECYCLE BIN — `archive` verb moves trees here, `unarchive` restores
  measurements/{run_id}.json         # content-addressed cross-campaign cache (was archive/measurements/)
    measurements.json  prompt_aliases.json
  sessions/{session_id}/session.json
  .workspace/events.jsonl            # workspace ledger — home for destructive-op audit records
```

## The measurement store — a cache, GC by sweep

`measurements/` is a **content-addressed cache**, shared across campaigns (the reuse that
lets a re-run replay instead of re-paying the LLM). It is keyed by content hash, carries no
campaign provenance, and needs none:

- **Delete a campaign → its measurements are not touched.** They belong to no single
  campaign; another campaign on the same `(dataset × config)` would reproduce the identical
  key.
- **Eviction is derivable, not tracked.** A GC sweep evicts any entry that **no live
  campaign's `(dataset × config)` would re-request** — computed by walking live campaigns'
  keys vs stored keys. No refcount, no provenance ledger, no orphan-tracking. The "stale
  data with no owner" worry dissolves: origin is never needed, only reproducibility.
- **Cost of a miss = re-measure.** Because it is a cache, eviction trades disk for a
  possible future re-pay. The sweep is therefore opt-in / bounded, never automatic on
  delete.

This is the resolution that retired the earlier refcount-and-provenance plan
(`.claude/plans/hazy-shimmying-truffle.md`, rejected).

## The lifecycle ladder

| Verb | Campaign tree | Measurements | Recoverable |
|---|---|---|---|
| **archive** | moved to `archive/{id}/` | untouched (cache) | yes — `unarchive` |
| **unarchive** | moved back to `campaigns/{id}/` | — | — |
| **delete** | **removed** | untouched (cache) | no |
| **delete --keep-results** | all tiers removed **except the keepsake**, dir marked `results_only` | untouched (cache) | no (but the ~100 KB keepsake stays in place) |

- **Destructive is the default.** `delete` removes the campaign tree outright (replacing
  today's soft `lifecycle=deleted` flag-flip). The operator confirms; the same gesture
  offers the off-by-default `--keep-results` opt-in, which spares only the **keepsake tier**
  (manifest + reports + the shallow `langfuse/{traces,observations,scores}` loop trace —
  not the heavy detail).
- **Destructive ops are audited to the workspace ledger** (`.workspace/events.jsonl`),
  since the campaign's own ledger is going away.

### The one MECE taxonomy — Connector / Loop / Dataset

There is **one** storage vocabulary, the operator's mental model. Every byte in a campaign
tree lands in exactly one of six leaves (mutually exclusive, exhaustive — they sum to the
on-disk total). The top-level axis is **Connector vs Loop vs Dataset**; **Loop** breaks into
four. Classifier + endpoints: `presentation/api/routers/campaigns/storage.py` (`_leaf` /
`_campaign_split`).

| Leaf | Parent | Contents |
|---|---|---|
| **Dataset** | — | `langfuse/datasets/` — the sacred ground-truth mirror (input-data copy; usually the biggest chunk) |
| **Connector** | — | `.runtime/cache/**` + the per-sample `results`/`all_candidate_results` arrays carved from the public `rounds/round_*.json` |
| **State** | Loop | the resume point — non-array remainder of `rounds/round_*.json` (the read-once cycle seed now rides the ledger, so it lands in **History**) |
| **Trace** | Loop | telemetry — `.runtime/streams/`, `prompts/`, `langfuse/{traces,observations,scores}/` |
| **History** | Loop | the durable event spine — `.runtime/ledger.jsonl` |
| **Reports** | Loop | readable output — `campaign.json`, `index.json`, `dashboard.json`, `log.md`, `review.md`, `hard_samples.json` |

**The keepsake is not a leaf.** What `delete --keep-results` spares (Reports + the langfuse
loop trace) is a cross-cutting subset, surfaced as a one-line UI note — never a summed
figure, so the partition stays MECE. The lifecycle ladder is a plain binary
(`keep_results: bool` → `_strip_to_keepsake`), independent of this taxonomy.

## Operator surfaces

- **Storage hover (`GET /campaigns/{id}/storage`).** The six MECE leaves as a hierarchy —
  On disk → Dataset / Connector / Loop, Loop → State / Trace / History / Reports — summing
  to the on-disk total, plus the keepsake note. One vocabulary, replacing the old overlapping
  `connector_bytes`/`results_bundle_bytes` triple.
- **Files-view cake (`GET /workspace/storage-by-dataset`).** One donut per leaf, sliced by
  dataset.
- **Workspace rollup (`GET /workspace/storage`).** Per-campaign totals (active + archived),
  fattest ranked, **plus** `shared_cache_bytes` (`measurements/` + `archive/optimizer_calls/`,
  the cross-campaign reuse caches) **plus** a residual `other_bytes` (sessions, the workspace
  ledger, dataset/backend stores) — so the grand total equals the tenant's *real* on-disk
  footprint, nothing excluded. Directly answers "I lost control of bucket sizes."
- **Storage quota (later, not urgent).** Per-tenant cap, alongside the existing
  `spend_budget_usd_daily` / `max_campaigns_per_day` in `user.json`.

## Implementation arcs — 1–4 shipped, 5 standalone

Arcs 1–4 landed on `main` (`4a97ae31`/`ffd4d36a`/`f544ed94`) and are the design in force
today, verified live against a real cycle's on-disk `ledger.jsonl` (the `phase=init` record
now carries `dataset_size: 400`, not a 400-row embed) and `campaign_store/store.py`
(`archive_campaign`/`unarchive_campaign` use `shutil.move`; `delete_campaign` implements the
`keep_results` tier-cut). Only arc 5 remains open, as an operator-run migration, not a design
gap:

1. **Writer-slim (the size win) — shipped.** Ledger init record stores a dataset reference;
   round-0 display record drops `round_result`. ~4.1 MB → ~2.3 MB/cycle, zero reader changes.
2. **`archive/` → recycle bin + `measurements/` relocation — shipped.** `archive`/`unarchive`
   physically move trees; the measurement store lives at `projects/{tenant}/measurements/`,
   a peer of `archive/`, not nested under it.
3. **Destructive delete + `--keep-results` — shipped.** Replaced the soft flag-flip; audits
   to the workspace ledger; tier-cuts the keepsake.
4. **Surfaces — shipped.** One MECE hierarchy (Connector / Loop / Dataset, Loop → State /
   Trace / History / Reports) across the hover, cake, and rollup;
   `presentation/api/routers/campaigns/storage.py` is the classifier + endpoints.
5. **Migration — open, operator decision.** One idempotent sweep slims legacy ledgers;
   dry-run reports ~277 MiB reclaimable across the current legacy ledgers. Ships standalone,
   run on demand — `--apply` is an operator call, not blocking.
