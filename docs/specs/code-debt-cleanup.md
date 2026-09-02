# Code-Debt Cleanup — Backlog

**Only what cannot be picked up now.** An item earns a line here by being
**blocked** (name the blocker) or **multi-arc** (too big to close in one pass).
Everything else — anything adjacent to work already in hand, anything one edit
closes — is **fixed in the pass that found it**, never filed. A register of
things someone could simply have done is what makes this file unreadable, and it
reads as a debt load the repo does not carry.

**One line per entry**, enough to pick up cold: `file:symbol — why — action —
blocker`. `git log` is the history layer; when an item ships, delete it.

**Not debt — goes elsewhere:** forward webapp perf/feature work →
[`roadmap.md`](roadmap.md); new milestones/specs → `docs/specs/`; architectural
decisions → [`../architecture.md`](../architecture.md).

> **Verify before trusting an entry.** This doc decays — claims drift as the code
> moves under them. Audits have found long-standing entries that were
> stale or outright wrong (a "dead" field that mlflow reads; an "always-False
> return slot" that's a live signal). Re-confirm call sites before acting; if an
> entry is wrong, fix or drop it as part of the work.

## Open — multi-arc, no blocker

- **An origin-relative cost term.** θ now carries whatever the composite says (`domain/scoring.py::
  CellScorer` — the per-cell `objective`), so a cost term finally binds on the election. What is still
  missing is the term worth writing: `mean_out(candidate) / mean_out(origin)` needs the origin's mean
  threaded to per-cell scoring, and the absolute form is inert as built — `OUTPUT_TOKEN_BUDGET = 12_000`
  against 186–822-token generations moves it 0.985 → 0.932. Weight deliberately unchosen; pick it against
  a measured run, not in advance.

- **The REST API is the intended external integration surface and isn't documented to a bar that
  makes it safe to lean on.** `GET /api/v1/campaigns` · `GET /campaigns/{id}` ·
  `POST /commands/{kind}` · `GET /backends/{id}/health`, in
  [`../operations/backend-integration.md`](../operations/backend-integration.md) § "PromptPotter's
  own REST API" — but stability guarantees per endpoint and worked request/response examples beyond
  the bare table don't exist. Campaigns are async, so **submit → poll → fetch result** is the shape
  a caller needs documented; keep it protocol-agnostic rather than coupling it to one gateway.

- **`datasets/bbeh/sweep/*.yaml` — 12 arms carrying a `reason:` and no lever.** The reader now refuses
  them at load (`OperatorSweepFile` requires a contrast lever), so nothing burns. What is left is a
  CONTENT call, not a task: delete the dir (`git log` holds the narratives) or author fresh `l1_layout`
  arms — 8 of the 12 narratives are prompt-CONTENT hypotheses `l1_layout` structurally cannot express.

- **The cycle-path codec agrees with Python only in prose.** `lib/ids.ts` re-implements
  `encode_cycle_path`'s separators and charset, and `cyclepath.test.ts` locks the TS side against itself.
  Action: generate the codec from `openapi.generated.json`, or accept the duplication and say so.
  Blocker: none; low priority — the browser only reads and the server re-validates every hop. **Do not
  re-file "fold the hop into the generated `CycleHop`"** — refused on inspection: the generated type is
  the wire element, `ids.ts`'s `PathHop` is the BROWSER's address (it encodes into `?path=` URLs and
  view-memory keys), so binding them lets a server-side rename invalidate persisted addresses.

- **Three diagnostics mint no cycle, so nothing they measure has a browser home.** `seed-screen`,
  `noise-floor` and `verify` write no cycle for the dashboard to address, and an inner L4 campaign
  lists only under `descend=`. Through an L4 run the outer campaign therefore reads idle while the
  work is one hop down — not wrong, and it reads as broken, which is the worse failure because
  nothing on screen says so. Action: name the surface each one writes to *before* adding a surface;
  `descend=` may already answer the L4 half. Blocker: none.

- **Absent-vs-zero is a rule, and only its named instances have been fixed.** Every number reaches
  the screen as measured / not-measured / not-applicable and may not lose which one on the way; the
  tell is a null branch in the browser that no Python writer can emit. The five sites filed as
  "Step 5" are closed (`git log`). Action: sweep for the *rule* — writers whose `0.0` default is
  indistinguishable from a measurement — rather than re-checking those five. Blocker: none.

- **Five `export *` barrels re-export symbols that look file-local to a naive grep** — `lib/api`, `lib/types`, `lib/derivations`, `components/ui`, `components/workflow`. Stripping an `export` there silently narrows the barrel's public surface. Action: decide per barrel whether the symbol is meant to be public, then strip or keep — don't script it blind. **Recount before acting and never re-cite a headcount as current**; two traps in the recount itself are that relative-path importers (`from "./api"`, `"../api"`) miss an `@/…` grep, and that `lib/api/reads.ts` / `components/ingest/*` importing `"./types"` resolve to LEAF files, not the barrel.
- **The mobile pass was verified at 375/1440 on chat/dashboard/files/verify only.** Unswept: 393, 412,
  768 and landscape; login, onboarding, l4, account modal, candidates, lineage. No Lighthouse number
  was recorded, so there is no before/after. Action: sweep + record one pass.

## Blocked — named blocker

**Entry-point parity — five wired verbs the browser can fire and the terminal cannot:**
- Each is already whole on the server: a payload model in `PAYLOAD_MODEL_FOR_KIND`, a capability in `CAP_FOR_KIND`, a declared operation in `m12-api-openapi.yaml`. Missing is only the CLI half — one `sub.add_parser` row in `cli/parsers.py`, one `COMMANDS` row in `cli/campaign_runner.py` (an import-time assert pins the two together, so they land in one commit), and one handler beside the existing shells in `cli/commands/lifecycle.py`, which already owns `_refused`, `_resolve_target` and `resolve_campaign_hint`. No new orchestration: every one is a `CommandDispatcher` call.
- `skip-searchpoint` — `SkipSearchpointPayload(campaign_id, cycle_id)` → `dispatch_cycle_command`. The sharpest of the five: an operator watching a candidate burn samples has no sanctioned way to cut it, and the in-run stdin reader (`runner/origin_gate.py`) is origin-gate-only.
- `set-allowed-models` — `SetAllowedModelsPayload(campaign_id, allowed_models)` → `dispatch_campaign_config`. Its absence has teeth: `resume --steer-model` refuses against exactly this list (`fork_siblings.py::steer_is_babysit`), so a terminal-only operator can hit the babysit refusal with no terminal way to widen it. Structural peer of `rename`, which does have a verb.
- `cleanup-empty-cycles` — `CleanupEmptyCyclesPayload(campaign_id, cycle_id)` → `dispatch_cycle_command`.
- `replace-dataset` — `ReplaceDatasetPayload(slug)` → `dispatch_workspace_command`. On a slug collision the CLI tells the operator to pick a different name (`new.py::SlugTakenError`) where the browser offers version-and-repoint.
- `step-cycle` — `StepCyclePayload(campaign_id, cycle_id, rounds=1)` → `dispatch_cycle_command`. Has NO client at all, browser included, so today it is reachable only by hand-rolled HTTP.
- ⚠️ **The mirror is narrow, and the entry that stood here was wrong in the direction that stops anyone re-looking.** It claimed `pause` was the only control-plane verb the webapp fires and named a budget-halted cloud operator as the sharp case. `webapp/lib/api/commands.ts` exports **fifteen** posters — `postChangeSpendBudget`, `postStartRun(…, "resume")` and the three lifecycle verbs among them — so that sharp case is closed. What is genuinely absent from the browser is **two arms**: in-place rewind (`postStartRun` carries no `from_round`, and `postForkCycle` mints a fork rather than rewinding) and `--fork-on-divergence`. Both are `resume` shapes, so they land with the same grouping decision. **Recount before re-citing any figure here** — the posters build their URL as `` `${API}/commands/${kind}` ``, so a literal `commands/<kind>` grep sees three routes and misses twelve.
- **Blocker: the UI grouping is being reworked, and a CLI verb should not be pinned to a coordinate the surface is about to move.** Land them once the grouping settles, so the terminal's vocabulary and the browser's read as one product rather than two.
- **Deliberately no test.** A missing verb fails LOUD — argparse answers "invalid choice" — which is the `tests/CLAUDE.md` bar for not writing one. This entry is the ledger.
- ⚠️ **`set-sample-lookahead` is NOT on this list and must never join it** — browser-only is the deliberate `<entry-point-parity>` inversion, and the absence IS the boundary (root `CLAUDE.md` § Conventions).

**Archive hygiene — needs an archive, and the trigger is the NEXT bulk delete, not a date:**
- Four items stood in § Open — attribution, an inventory surface, a dataset-scoped reclaim, and a two-pane reach-map selector. Every one was sized against a corpus that has since been deleted, and each carried its evidence inside it: the per-label replay rates that would have sized the inventory, the stranded bytes that motivated the reclaim, the rows a reach map would have had anything to reach. **Blocker: there is no archive — `ls .promptpotter/measurements` answers whether that is still true.** Build these BEFORE the next delete, never after: that is the one moment both halves exist at once, something to measure and a delete about to strand it. Deleting first destroys the evidence *and* the motivation in the same gesture, which is exactly what happened here.
- **Reclaim** — the destructive counterpart of `delete`, dataset-scoped, dry-run by default, refusing while a producer can append, and NAMING what it would strand for a dataset whose rows another dataset's inner runs may share. Nothing does this today: `delete` leaves the shared content-addressed rows standing (correctly — a sibling may replay them), `compact-archive` reaches only the fields a row does not read, and `reindex`'s GC is positive-identification-only, so every orphan is kept.
- **Attribution** — `LineageNode.sp_hash` is stamped forward-only, which is right and is enough for everything measured from here on. The DESIGN question outlives the data: an L4 outer cell should stamp the runs its inner campaign produced at the moment it spawns them, the only attribution that survives the sandbox being reclaimed. Decide it before the next L4 run banks rows nothing can name. ⚠️ **Do not re-file a backfill** — it was refused once on the merits (the schema a hash covers is persisted nowhere) and there is now nothing left to backfill from.
- **Inventory, then the map** — run counts, byte split and replay rate by dataset / label / age off `MeasurementArchive`; then the selector, whose shape is settled and is a REACH MAP rather than a tree of checkboxes: the campaign family on the LEFT (`candidates/Forest` over `iter_family_courses`, which already descends `.inner/`), the archive partitions that selection REACHES on the RIGHT, load-bearing column = what is SHARED with campaigns outside the selection, because an `sp_hash` is not owned by a campaign. Both are unverifiable against an empty store, which is why they wait rather than ship blind.

**Behavior change (needs explicit sign-off, not a blind swap) — scoring:**
- **All-errored candidate scores `accuracy = 0.0`, not the honest `None`** — `compute_accuracy` (evaluators.py) returns 0.0 when no scoreable row exists; for all-deprecated that IS the verdict, but for all-errored it fabricates one (declared stage-1 tolerance in its docstring, 2026-07-13). The honest `None` must propagate: `ScoredCandidate.accuracy`/`RoundResult.accuracy` → `float | None`, `compute_composite_fitness` handling a missing `accuracy` term without `ScoringTermMissingError` in `_running_scores` (an "unscoreable candidate" state, the outer sibling of `InnerCycleUnscoreableError`), `display_fitness` double-None, dashboard + `types.generated.ts` + chart null handling, `best_round_on_shared_cells`/`_apply_best` null-safety. Blocker: wide Optional-propagation across the served surface for a state PoBB DegradationCheck usually eliminates mid-round anyway; needs its own pass. **Smaller than filed:** its sibling `rescore_results` stamps errored rows `fitness = 0.0` citing `compute_accuracy`, which actually EXCLUDES them, and `_mean_fitness_by_cell` reads an absent key identically — so no cited reader depends on the stamp. Start from the audit that implies: every unguarded `r["fitness"]` subscript. Note the stamp makes a row's shape depend on replay (a freshly measured error row has no `fitness` key at all).

**Live L1 round (operator-gated):**
- **`*_override → *_updates` L1 delta-key rename.** `prompt_fields_override` / `task_context_override` / `pipeline_params_override` / `pp_override` are merges, not replacements, but named "override." **Decision (settle first):** unify the pipeline delta to **`pipeline_overlay`** everywhere (kills the short/long two-name tax); the prompt/context deltas become `*_updates`. Rename writer→reader in one commit (schema `dispatch/schemas.py::L1Variant` is the source of truth — the LLM contract auto-propagates). Full site map: grep `*_override`. **The old rider "collapse `searchPoint.ts` + `candidateSearchPoint.ts` into one `wireToCandidateSearchPoint`" is DROPPED (verified 2026-07-16):** they answer different questions over one served field — `ObserveConfig` is a read-only view (keeps `steps`, carries a label), `CandidateSearchPoint` is an editable fork seed (drops `steps`). `lib/derivations/searchPoint.ts`'s module comment says so in-code. Merging would fuse a read projection with a write seed; the only real overlap is two lines of `candidate_scores.find`. **Blocker:** invalidates on-disk cycles (round-file key + optimizer structured-output contract) — verify against a FRESH cycle that completes round 1, not a resume.

**Security posture / migration:**
- **Backend-registration dedup** — `webapp/lib/hooks/useConnector.ts` client-side `distinct`/`seenEndpoints` collapse is a back-compat shim for per-dataset `BackendConnection` rows minted before the `wiring.py` one-row-per-`(base_url, backend_type)` fix. NOT a row-delete: it's a 3-step migration — (1) rewrite each campaign's `campaign.yaml::backend_id` to the canonical `local` (8 stale ids across 82 campaigns, all → the same `127.0.0.1:8000` endpoint); (2) collapse the duplicate rows (needs a new `BackendStore.remove`); (3) make every re-wire path reuse the canonical id. Then delete the loop. Also: `wiring.py` `not backend_id` reuse block should guard on `existing.base_url == backend_url` (mint a distinct id on mismatch). Blocker: write + operator-run the idempotent migration on their data first — the loop is load-bearing until then.

**Cross-repo (TermNorm sibling at `OfficeAddinApps/TermNorm-excel/backend-api`):**
- **TermNorm wire `model`** — backend `spend.backend.model` reports a provider slug (`"openrouter"`), not the upstream model, so backend $ can't be derived from `lookup_rate(model)×tokens`. Add `model` to the per-request response + a `/version` endpoint; this repo then bumps `termnorm.py::_EXPECTED_REVISION`. (The connector revision-pin already exists; the old `auth.py` back-fill is already gone.)
  PromptPotter does not read that slug at all. `_compute_step_tokens` now stamps every step-token entry with the node's model — the backend's per-node `model` when it reports one, else the model the dataset overlay pinned (`pipeline.yaml::nodes.{n}.config.model`, which is mandatory for an LLM node). So per-node cost is derivable today, including for chars/4-estimated nodes, and the old `node_model or llm_provider` coalescing is gone. What remains genuinely owed on the TermNorm side is the **`/version` endpoint**; the per-request `model` is now a nicety (it wins over the overlay when present), not a blocker.
- **Backend fix isn't observable without clearing a cache** — PP's measurement cache + TermNorm's `match_database` both key on query/searchpoint, never on backend code/revision, so a co-owned backend fix replays stale results. Fold the connector revision-pin into the measurement-cache key (or add a `--fresh` flag); confirm the TermNorm `/matches` short-circuit only fires on `verified` aliases. Workaround: clear `measurements/`.

**Coupon + BYO build (Lane A2 — blocked on the build itself; ADR-0003 § Host coupon):**
- **Adopt-in-new-code for the coupon/BYO build:** the new `grant.json` / `api_keys.json` stores MUST ride `read_json_optional`/`write_json` (the `UserStore` template, `store/io.py`) from day one — don't add hand-rolled readers. `shared/pricing.py` still hand-rolls `json.loads(...)` at three sites (one of them decoding a fetched payload — same pattern, not previously filed); held separately from the JSON-read sweep (SHIPPED elsewhere) because `shared/` importing `infrastructure/store/io` is an unresolved layer-direction question — resolve it before or alongside this build. (`store/io.py` is now safe to import from anywhere: the eager `store/__init__` that made any leaf import drag in `CampaignStore` is gone, so this is a layer-*direction* question only, no longer an import-cycle one.)
- **Two host-wallet mechanisms** — `application/jobs/quota.py::admit_launch` + the `User.spend_budget_usd_total` / `token_budget_total` ceilings (lifetime, mint-time snapshot) vs the new coupon (`grant.json`, ledger-derived, live). Two guards on one concern (host's wallet) = the no-redundant-mechanism rule. Action: **delete the free-tier path**; coupon-remaining becomes the single host ceiling, read by the per-cycle `BudgetGate` every tick (D1/D2 in ADR-0003). Blocker: lands *with* the coupon, not before — deleting first leaves the wallet unguarded. ⚠️ Two things the replacement must carry or it is a regression: BOTH units (an all-USD coupon re-opens the unpriced-model blindness D1's token arm exists to cover), and the per-run **reservation** (`Job.cap_usd` / `cap_tokens`) — without it two concurrent launches are each admitted against the same remainder and the pair spends ~2× the ceiling.
- `domain/run_records.py::TokenUsageRecord` lacks `key_source` → `/auth/activity` `group_by=api_key` (`routers/auth.py`) fakes a *provider slug* as the key id. Once real `key_source: host|user` lands (declared on `TokenUsagePayload` in the asyncapi), replace the fake-slug derivation with the real dimension. Blocker: the coupon build adds the field.

**Needs a capability the closing directive does not open:**
- **Nothing probes whether a route implements `response_format` before a run spends money** — an unsupporting model is discovered by paying for it (HTTP 405 outright, or empty content plus a burned schema-repair re-prompt). Same shape: swapping a model means hand-editing two `pipeline.yaml` lines and remembering to revert both, and a leaked pin mislabels the next run. Blocker: a probe and a swap-verb are both new capabilities.
- **`infrastructure/llm/json_parse.py::try_groq_json_validate_repair` meters a fabricated ZERO** — it rebuilds `LLMResponse` with `usage` hardcoded to zeros after a `json_validate_failed` 400 that was already billed. The 400 body carries no `usage`, so the count is unrecoverable, and `unpriced_tokens` is the wrong home: it means price unknown, not count unknown. Never estimate from content length. Dormant — Groq-only, every configured provider is `openrouter`. Blocker: `TokenUsageRecord` has no unknown-count dimension, and the account gate leans on a count always being knowable.

**Names that stopped describing what they name:**
- **`sp_budget_ttest` names a t-test nothing has run since 2026-05-01.** Paired t-test → Wilcoxon (`689d5dec`) → Bayesian PoBB (`3fbaf215`); the knob survived all three. Live across `datasets/*/campaign.yaml`, `scripts/smoke_campaign.py`, `.claude/skills/potter-run/SKILL.md`, `test_numerics.py`, `test_integrity.py`. Action: rename writer→reader in one commit. Blocker: it is an on-disk config key under operator-curated `datasets/` — the rename is the operator's call, not a sweep's.
- **`docs/specs/m12-api-openapi.yaml` / `m12-events-asyncapi.yaml` carry a milestone the repo retired** (`16934d29`, `61cbf5d8`). Action: drop the prefix. Blocker: reaches root `CLAUDE.md`, `presentation/CLAUDE.md`, `test_integrity.py`, ADR-0001 and the generated-spec build in one commit — a contract-surface change, not a rename.

**Declared, no reader — each blocked on a served-surface decision, not on finding out:**
- **`manifests.py::ConfigCoupling.estimand` and `.knobs`** ride `openapi.generated.json` + `types.generated.ts`, and the one consumer (`ConfigMapPanel.tsx`) renders `severity`/`labels`/`relation`/`consequence`/`active`/`name` and neither of these; no CLI reader either (`check_couplings` takes `Coupling` objects direct). Action: drop from the response model. Blocker: deleting a served field is a wire decision.
- **`domain/ruler.py::DeltaRuler.anchored_at_round`** is required (no default), persisted in every `RulerRecord`, and read by nothing — the class docstring's own list of what an anchored extension needs omits it. Blocker: the per-candidate 0.0-floor state is in flight and touches `domain/ruler.py`; land that first or the two collide.
- **`domain/results.py::CycleResult.origin_level_se`** — the L4 reader its own field comment names explicitly refuses it (`l4/proxies.py::mean_adopted_level_se`: *"`origin_level` is deliberately absent … folding it into each side counts it twice"*). Blocker: it rides `cycle_result_command`'s `model_dump()` into CLI `--json`, so it wants a decision rather than a deletion.
- **`scoring/formula/matchers.py::SCORING_FUNCTIONS["relu"]` and `["smoothstep"]`** — no `campaign.yaml`, fixture or test uses either, and no doc names `SCORING_FUNCTIONS` or tabulates the DSL vocabulary, so an operator cannot discover them. Their five neighbours all land. Action: document the DSL or drop the two. Blocker: it is an operator-facing DSL — reach is a product call.

**One rule, two languages, kept in sync by hand:**
- **`config/settings.py::PROMPT_STRING_FIELDS` is hand-mirrored** at `webapp/lib/prompt-fields.ts` under a header saying it "MUST stay in sync" — the note beside a copy that `94d52a74` removed for `PipelineView`. `scripts/build_ts_types.py` already emits a non-model constant table (`STOP_REASON_LABELS`), so the machinery exists — and `ABORT_LENS_LABELS` is now a worked precedent for retiring a hand-mirror with it, key set asserted against its consumer at import. Riding with it: **`webapp/lib/derivations/allowedModels.ts::overlaySetsModelOutsideAllowed` re-implements `domain/pipeline_overlay.py`'s predicate line-for-line**, node-config walk included, with a Vitest suite locking the TS side against itself — and it has already drifted, its header citing the Python function at `domain/opt_search_point.py`, where it does not live. Action: generate the constant; serve the predicate's verdict beside the fork affordance. Blocker: the second half is a new served field.

**Concurrency semantics — a design question, not a wiring gap:**
- **Launch admission runs on the API paths only, so a terminal run is invisible to the machine.**
  `recover_pending_replacements` → `check_launch_quotas` → `_admit(job_registry.reserve(...))` is
  the prologue in `jobs/launcher/mint_and_start.py` and `jobs/launcher/checkin.py`; the CLI reaches
  `run_optimization` through `cli/commands/_shared.py::drive_cycle` and runs none of it. Verified
  consequences: a terminal run holds no slot and never appears in `JobRegistry.list_running`, so the
  browser will admit a second run on the same box — including **a second producer on the same
  cycle**, since `_apply_start_run` has no live-producer check (only `_dispatch_delete_cycle`
  consults `live_cycle_ids`); `User.max_concurrent_cycles` counts neither; and
  `recover_pending_replacements` — whose own call site is commented *"a resumed cycle must not
  resolve a pin a crashed Replace left dangling"* — never runs before a CLI `resume`.
  **Explicitly NOT part of it:** the host-wallet arm is CLI-exempt by design
  (`jobs/quota.py::lifetime_ceilings`, `::spends_the_hosts_own_key`), so this is the SLOT and the
  Replace-heal, not the money. Precedent that the class is real: the reaper had the identical shape
  and now runs from `campaign_runner.py::main::sweep_dead_cycles`.
  **Why it is not simply "call the prologue from `drive_cycle`":** the CLI is a foreground process
  the operator can Ctrl+C, and a reservation it fails to release wedges the box at capacity — so
  what a terminal run should hold, and for how long, is a decision about the concurrency model
  rather than a missing call. Settle that first.

**Needs a live run, not a decision:**
- **`_rebank_on_branch`'s re-bank has never been observed** — fixed to take each corrected round through the whole ingress, but the cycle it was measured on went with a store wipe, so the fix is reasoned, not seen. Repair a fork; confirm each corrected round carries its own `round:complete` on the branch.

## Standing — long-lived design holds

- **Every persisted `StrictModel` owes a row in `application/restamp.py::_SURFACES` — adding one without its row IS the bug.** It has already cost an outage: `5a69ca67` dropped `BackendConnection.last_synced_at`, `init_services` raised `extra_forbidden`, and every `new`/`resume` died at load. What the table deliberately excludes and why each absence is a decision — and why PRUNING never rewrites a round document while a recovering migration may — is stated in that module's own docstring; the tolerance rule and the reporting-vs-scoring boundary it stops at, by [`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md) § Tolerance is scoped by what a payload is FOR. Read the table as covering the typed documents, not everything the verb touches. **Outside the table is not outside all obligation, and reading it that way already cost the record once** — `extra="ignore"` forgives an extra key, not a missing one, and it does not reach the `extra="forbid"` models nested inside, where a stale key is fatal in the other direction.

- **Tier 3c — the web-launch process split — is DEFERRED, and the trigger is what to re-read, not the deferral.** A web-launched run executes in-process in the API worker, so it shares that process, its env and every provider key ([`../operations/access-model.md`](../operations/access-model.md) § Tier 3). The reason to wait is not cost: **the requirement is undefined until we know what is being isolated.** Audited 2026-08-15 across every tenant-controlled path into that worker — scoring formula (AST-allowlisted, no attribute/subscript), YAML (`safe_load` throughout), dataset slugs (regex-validated), provider + `base_url` (closed three-entry registry, never tenant-set), and no URL fetched from tenant input. Nobody can supply executable code, so a boundary built now is built against a guess, and the guess decides the shape: a custom pipeline node wants a subprocess, a plugin connector a different trust model, arbitrary Python a container. **It also fights L4 directly** — `runner/inner/spawn.py` spawns each inner campaign as an `asyncio.create_task` in this process and depends on it (per-task ContextVar copies for ledger / round / abort, the flat `.inner/` sandbox, `set_optimizer_prompt_overrides` bound in the child), so the split would mean rebuilding the recursion seam at every depth while L4 is the closing focus. **Waiting costs nothing because the launch seam is already single** (`application/embedded_run.py`, `jobs/launcher/mint_and_start.py`) — keep it that way and a later move is contained; let run-launch logic spread across call sites and this becomes expensive. **THE TRIGGER: the first time a tenant can supply anything executable** — a custom node, a plugin connector, arbitrary Python. That is a product decision, and when it is made 3c stops being deferred and becomes a prerequisite of shipping it. Until then, what bounds the blast radius is 3a's kernel wall plus `DATA_DIR` (`deploy-linux/install-service.sh`), which takes away the service's ability to rewrite its own source and `.env`.

- **Holistic reframes — larger chunks, noted so they aren't mistaken for done; don't slip one into a
  release.** (1) **Tooltip/overlay consolidation:** ~86 of the webapp's ~170 DOM `title=` attributes are
  teaching prose the browser renders as an unstyled, unselectable blob that dies on touch. Migrate **by
  string source, not by file** — `lib/terms.ts::TERMS` first (~7 call sites), then the `VerifyPane` /
  `RoundFileView` header glossaries (12 sites, two loops); leave the ~75 `title={same truncated string}`
  sites, where HoverCard is strictly worse. Five bespoke floating overlays sit beside it, of which
  `hs-heat-tip` is legitimately bespoke (its trigger is a `<canvas>` region with no element to anchor to).
  (2) Candidate-CI resolution is ONE seam and one estimator (`scoring/selection.py::mean_fitness_ci`); a
  second band beside it is what made the whisker appear and vanish by election gating — do not add one
  back. (3) **Never examined, and the one with real reach:** `application/optimization/CLAUDE.md` asserts
  L2/L3/L4 are one family, yet each is built from scratch. Whether they should share machinery has never
  been asked, only asserted — and the L2↔L4 hunt found one real collision underneath it
  (`NodeLayoutSpec.editor` claiming two owners of `l1_generate`'s layout, fixed `d1d792b0`).

- **THREE numbers are computed in the browser, against § Scoring authority** — each verified by tracing, not suspected. **Every re-check has moved a target, so fix the aim before the code:** the `cached_samples / n` division now lives in `candidates/series.ts` (the series registry owns it — it was in `FitnessChart.tsx` and, before that, was filed against `CandidatesCard.tsx`); and the `θ/$` chip is minted in `shell/RemoteControl.tsx`, NOT `chat/ChatPane.tsx`. **A fourth entry was struck, not fixed:** the searchpoint drill-in never subtracted anything — it renders served `matchedParentAccuracy` and served `matchedParentLift` with its interval. The rest: `HardSamplesHeatmap.tsx` folds per-sample measurements into a mean and thresholds it at **`>= 0.5`**, which matches neither `lib/fitness.ts::HIT_THRESHOLD = 1.0` nor `sample-walk.ts::sampleBucket`'s 0/1 boundaries — so the mini heat strip and the table row directly beneath it can colour one sample differently on a graded scorer, while the served `series.mean_fitness` is already read by two sibling files and `archivePerSample` is already a prop; `dashboard/scoring/OuterSignalPanel.tsx::leadingArm` falls back to a browser-side argmax over `composite_fitness` when the engine elects on **θ**, disagreeing with `forest-layout.ts::pickWinner` (deliberately no-fallback, and its comment says why) exactly on HELD rounds — so it can draw a lift interval attributed to an arm the round never crowned; and `shell/RemoteControl.tsx` mints `abilityDelta / usedUsd` and ships it as a headline `θ/$` KPI chip. All three need **serving**, not deleting, so each wants a backend field first.

- **`idea_fingerprint` cannot see a SEMANTIC re-proposal, and the gate built on it is the only
  cross-round one.** `domain/candidate_diff.py::idea_fingerprint` matches content-word overlap
  between mutated VALUES, so it catches a re-proposal only when the wording survives. Measured on
  the banked corpus: **0 of 15 real re-proposal pairs caught** — including the `justlogic-d234`
  case the gate was written for, one idea ("exhaust modus tollens before answering Uncertain")
  walking `instruction` → `thinking_style` → `output_schema_descriptions.reasoning` →
  `task_intent` across 8 rounds. So `repeat_variant` rejects a copy-paste and nothing else, and
  the generator can restate one hypothesis indefinitely while `l1_n_repeat` reads 0 — which is
  worse than no counter, because a 0 reads as hygiene. The docstring now says this (`d1d792b0`);
  the mechanism does not. **HELD, not deferred by accident:** every fix is a new mechanism
  (embeddings, or an LLM judge per pair), and the closing phase does not open new features. Its
  cheaper twin is already the plan — make candidates differ MORE (`l1_generate` semantic
  widening), which attacks the same failure from the generator side and is owed anyway for the
  panel's arm-resolution problem. Revisit only if widening lands and repeats persist.

- **Optimizer model repair-rate on heavy L2/L3 structured output — unmeasured.** Every optimizer node runs whatever `promptpotter/assets/optimizer/pipeline.yaml` pins, chosen for speed + schema obedience together. What is owed is the measurement: a live cycle reaching L3 to read the repair-retry rate under the *current* model — read the model off that file, never off this entry.

- **Correct today, each with a named trigger — none is debt until its trigger fires.** `/ray` re-parses the whole merged window each tick because every append moves the family validator (per-ledger byte cursor, if it ever shows in a profile) · `/tree` serves the whole subtree though collapsed sidebar rows render one tier (a `depth=` param is contract surface — don't add speculatively) · `lib/view-memory.tsx`'s restore sites read the store during render, which is safe; a future consumer restoring from an effect could seed from the pre-hydration empty store and record it back · `_conditional.py::weak_etag` folds the lens/samples mask (request identity) into the validator (resource state), correct until a URL-keyed cache layer exists to cross-serve variants.

## Considered, not debt — don't re-open

- **The probe-rounds section of [`../../promptpotter/application/optimization/CLAUDE.md`](../../promptpotter/application/optimization/CLAUDE.md) stays at full length — it is not stale description of an unwired lever.** It reads that way because `L2ContextOutput` carries no `action` field, so a doc-shrink pass scores it as prose about nothing. Operator-decided 2026-08-26: it is the written spec for a planned L2 capability — a probe round spends a whole round interrogating one axis, which is how L2 breaks out of the loop while still using the loop. Don't compress it, don't fold it into a code pointer, don't file the missing field as drift.
- **`best_round` two bases (composite-argmax winner export vs cumulative-accuracy index/dashboard headline)** — operator-decided correct-by-design: composite is the optimizer's objective (winner export + L2/L3 stall comparator, which also compares θ), accuracy is the formula-independent headline. Documented in `_apply_best`'s docstring + `architecture.md` §0.5. Don't flip either basis to match the other.
- **`l2_duplicate_insert` / `l2_task_context_stale_repeat` are GONE — don't re-file either** — both were `task_context` checks, and that framing is frozen for the run (`TaskDecomposition.merge` refuses a rewrite), so neither breach is representable. Owner: [`../developer/dispatch-hub.md`](../developer/dispatch-hub.md) § Wound 4.
- **Benchmarks are NOT gated from the distributed app** — settled the other way by `20d17ea8`: repo `datasets/` is *install content* (tracked in git, readable by anyone who has the install), so the `datasets.benchmarks.read` capability + `PROMPTPOTTER_ADMIN=1` gate were deleted and the tier is now `yours`/`install`. The gate existed to hide one gitignored scratch cut, and its cost was a blank pipeline hero + hard-sample leaderboard on every benchmark campaign. A private cut belongs in the tenant, where path isolation already protects it. Don't re-file the old "hide benchmarks from the default identity" entry.
- **Display-only recomputes do NOT breach scoring authority** — `headline-stats.ts::fitnessTrend` folds already-served values, and `presentation/views/live/phase.py` recomputes recall@k for the terminal readout. Serving either would add wire coupling for identical behaviour. (The bare `except: pass` around the recall block WAS the real smell, and was fixed.) **`hit_rate` sat on this list and should not have:** a fold is display-only only where its result is REACHABLE, and `is_hit` is `fitness >= 1.0` — unreachable on a graded scorer, so the column printed `0/N` on every row of a healthy campaign while this entry vouched for it. Now served (`SampleSeries.n_hits` / `mean_fitness`) and renamed `mean_fitness`. Check a proposed exemption at its threshold before granting one.
- **Sample look-ahead is LIVE, and every part of it looks removable** — it defaults off and no committed campaign enables it, so a reader concludes the branch never fires. It fires on every dataset the moment the operator presses the control — `promptpotter-self` included, where one press releases a GROUP of inner campaigns. Four pieces that must move together or not at all: the `.runtime/sample_lookahead.json` write/poll/consume triple, the acquire/absorb split in `query_loop.py`, `dashboard.json::sample_lookahead` + `sample_lookahead_discards` + the two connector declarations beside them, and the `campaign.lookahead` cap. **Never "recover" the discarded acquisition** — recording it makes the run's rows depend on in-flight depth, which forces a `human_intervened` stamp and devalues the campaign; that discard is the design, not an oversight. Why it is browser-only with no CLI verb: [`../operations/access-model.md`](../operations/access-model.md) § Tier 1a.
- **`session.py` ×3, `state.py` ×2, `base.py` ×2 keep their names — the package path already carries the concept.** Filed as a filler-name collision; the verification the entry asked for says no. Checked two ways and both come back clean: the three `session.py` are the run `Session` (`initialization/`), the CLI's accessor over that same noun (`cli/`), and the cookie→JSON auth store (`infrastructure/identity/`) — and **no module in the tree imports the auth one alongside either other**, so the only real ambiguity is never experienced; there is no disambiguating `import … as` anywhere, which is the friction a genuine clash produces. `escalation/state.py` vs `live_dashboard/state.py` and `llm/base.py` vs `projections/base.py` are each one concept per package, and `base.py`-holds-this-package's-ABC is a language-wide convention, not a filler name. Renaming any of them buys a longer import line and costs every citation that names it. Re-open only for a name whose *own package* cannot resolve it.
- **`shared/identity.py` (the capability/tier authz vocabulary) keeps its name beside `domain/identity.py`** — verified 2026-08-17 against the same test as `session.py ×3`: zero `import … as` aliasing in the tree, disjoint symbol sets, and only `store/stores.py` + `middleware/oidc.py` import both, so the ambiguity is never experienced. (A third `identity` — the `infrastructure/identity/` OIDC package — does not change that.) The access model is the operator's call, [`../operations/access-model.md`](../operations/access-model.md). Re-open only if a call site ever has to disambiguate.
- **The unreset ContextVars are NOT a leak class — do not re-file a sweep, and never fuse them into one
  settings object.** All eight traced 2026-08-15; the one genuine defect (`_ABORT_CHECK` chaining a
  predicate per rebase, keeping retired forks' `pause.flag` live) is fixed. Each remaining non-reset is
  load-bearing: `_MODE` must cover finalize or the archive reads, tracing sink, earned-blocks fence and
  optimizer clamp de-hermeticize mid-measurement; `_CURRENT_ROUND`'s first-token-only trick is a
  deliberate outer restore; `_INNER_SPAWN`'s stale path needs a cycle that would have prevented it; and
  clearing `_OPTIMIZER_PROMPT_OVERRIDES` would wipe the inner mutations `spawn.py` sets before calling
  `run_optimization`. A context manager restoring only the ContextVar desyncs the checkpoint poll from
  the rate-limit poll, because `session.pause_check` holds a second copy of `_ABORT_CHECK`'s composition.
- **θ and `matched_parent_*` do NOT belong on `ElectionRecord`** — refused on evidence when the crown moved there (2026-08-11), and the shape invites re-proposing both. θ is RESTAMPED when the ruler warms, which is what round 0's second close exists to deliver (`runner/loop.py`), so it stays on `round:complete`, which every close re-reads; only the crown never moves, and only the crown belongs to a record that does not replay. `matched_parent_*` is not merely unservable there but *unwanted*: nothing plots a floor on a bar (the sole renderer is the searchpoint drill-in, off the row its host selected), so serving it on the tree ships a writer with no reader — and by this package's own test (`infrastructure/CLAUDE.md`) a value the round document already carries per candidate earns no ledger payload at all.
- **The `score:` lens cannot be ranked BY the election, at any price worth paying** — refused 2026-08-11; the tempting entry reads as a one-line consistency fix and is not. θ under another formula must be re-fit from per-sample grades against a re-calibrated δ ruler, which is `ab_replay`'s substrate (`with_replay=True` plus an archive read). The lens and `ab` are **one mechanism at two prices**, not two rankings to reconcile, and the cheap one is polled by the tree route — so adopting the exact one puts a campaign-wide refit behind a 5 s poll. What was wrong was the claim, and it is fixed: `display_rank_key` (ex-`round_winner_key`) names the candidate a formula ranks first, nothing more.
- **Typing `index.json` — measured 2026-08-05, answered "not yet".** The only `dict[str, Any]` in the read path a static model could name (every other is the node-keyed overlay whose keys the backend invents at runtime). **43 files, 24 top-level keys, 0 unreadable**, against ~120–160 model lines / ~25 files / ~65 read sites — **net +60 to +100 LOC**. Refused because the ledger scores it **zero** (`any_params` excludes container values, `models_lax` moves ≤1) and `extra="forbid"` breaks the deliberately tolerant reads in `enumerate_cycles` / the lineage surveys. Re-verify 43/24/0 before re-opening; `domain_any_maps` is the dimension that would make it decidable.
- **The rate table's age does NOT earn a surface** — answered against an entry that proposed one. `_cache_fresh` ages off `CACHE_PATH`'s own mtime, so `ls -l` already answers it and the file tree is the dashboard; `refresh_rates_in_background()` fires on every launch path, bounding the age at the TTL plus one run; and the *actionable* consequence is already a served field a decision reads (`unpriced_tokens`, acted on through `jobs/quota.py`'s unpriced grace). `main.py::health_check` is static strings with no I/O — a `stat()` there makes a deploy liveness probe depend on the user data dir. Age would be a writer with no reader.
- **`RunCallbacks` ↔ `emit_*`** — two writer APIs by design; the "which do I use" rule is in [`../developer/adding-a-surface.md`](../developer/adding-a-surface.md) §1.
- **`from_disk_log`** — not a roundtrip shim; foreign fork-siblings + historical cycles have no live ledger, so the on-disk `index.json` is the only source. (Its round twin `from_disk_round` had zero callers and was deleted.)
- **`measurement_archive.py` `.get(…, default)` at `save()`** — looks dead (the production writer always sets the keys) but `save()` has direct test-fixture callers with partial dicts; live boundary guards.
- **`writers.py` `_load_p_best_trajectory` / `_fork_summary_from_index` / `_load_sibling_indices`, `axis.py::_collect`** — single-caller, but the caller is in the SAME file in every case; intra-file `_private` decomposition is not inter-file indirection.
- **Leader-lock-in mechanism** (`leader_lock_in` / `pobb_lock_in` / `pobb_lock_in_n_min` knobs + `PoBBConfig.lock_in` + the `LEADER_LOCKED` `EscalationTarget`/`CandidateOutcome` + the `abort:lock_in_off` lineage-overlay lens) — the config knobs default off and no committed campaign sets them, so it LOOKS like a dead mechanism, but the `LEADER_LOCKED` path is structurally LIVE: a domain escalation target, a candidate outcome, the mask/lineage-overlay `abort:lock_in_off` lens (the candidates card's Lens select, "No lock-in"), and exercised by `tests/test_numerics.py`. Deleting it removes a shipped analysis feature, not dead code. Investigated + KEPT. (The unreachable significance-gate beside it WAS deleted — it had no live surface.)
- **Check-in "ready" ≠ "mintable" for prompt template-vars** — `origin_readiness(draft)` gates columns/framing/node-models but not whether the committed prompt carries each node's required `{{template vars}}`; that check lives only at mint (`config.py::configure_and_apply_pipeline`, `pipeline_config_invalid` 422). Surfacing it earlier (at the resolve turn) needs the live `GET /pipeline` schema threaded into the deliberately-I/O-free resolve path (`origin_readiness` is pure-over-draft; `resolve_origin_turn`/`POST /resolve-origin` carry no backend client + the draft no base_url). **Operator decided: keep the 422 backstop, don't add pre-mint backend I/O.** It's non-destructive (draft preserved, retry) and names the exact missing vars + fix; a bad origin never runs. Revisit only if check-in UX timing becomes a felt pain.
- **`LLMResponse.reasoning` has no code reader, and that is the design — never file it as write-only surface.** It is the model's own thinking channel (`message.reasoning` on the OpenAI-compat wire). It looks exactly like the "fields declared/written never read" pattern in the hunt list below, and it has already been surfaced that way once (2026-07-26, by the audit that deleted the `llm_only` connector — whose `resp.reasoning[:4000]` had been its only reader). **A model with nowhere to put its internal process answers without one** — give it a bare classification slot and it emits the label with no reasoning behind it, measurably worse. So the slot is part of the ask, capturing what lands there is part of the contract, and the value of the field is not "who reads it in Python." It now rides the ledger payload → `nodes[*].output.reasoning` (audit twin + live dashboard) → the operator's node-detail "Thinking" pane. **Hard invariant: analytical only** — it must never reach a gate, metric, validator, scorer, escalation signal or cache key, because scoring narration teaches the loop to narrate. Full rationale is the field note in `infrastructure/llm/response.py`; the principle is `docs/concepts/structured-output.md` § A place to think is part of the ask. Same call applies to a `reasoning` slot in any node's `output_schema`.
- **MLflow + Langfuse sinks** — the observability-nexus *core capability*: PromptPotter drops into a team's EXISTING local MLflow / cloud Langfuse instance (flip a flag / add `.env` creds). Off-by-default ≠ dead. See `docs/architecture.md` §0.5 Tracing. Do not propose for deletion.
- **L2/L3's shared `fork_proposal` + `terminate_proposal` is ONE seam with two entry points** — L3's layout already teaches it both. Don't re-file as duplication. **The "`l3_plan` has never fired in a banked ledger" line that stood here is REFUTED** — re-measured against the audit twins (`.runtime/cache/rounds/`, the per-node LLM I/O), `l3_plan` made **43 real calls across 14.6% of banked rounds**, every one carrying a plan, reasoning and usage, and effectively all of them inside inner L4 campaigns. Two traps that made the old reading survive: the round document's `optimizer_prompt_hashes` names all five nodes on EVERY round (it is measurement identity, not a fire record), and the twin lives under a **dot-directory**, which a bare glob skips. L3-side claims are measurable now; measure them there. Two capability-gated peers are the reverse case — `rebase_capability` / `terminate_capability` render empty across the whole corpus because no banked campaign set the bit, which measures the corpus, not the code.

- **Citability is DERIVED — never re-introduce a citable-panel list.** `EVIDENCE_GROUNDING_FIELDS` was a hand-maintained frozenset the validator checked *set membership* against, so a variant could cite a panel the prompt never rendered and pass clean. It had already drifted twice: the phantom `parent_panel`/`sibling_yield` names were excised 2026-07-02, and by 2026-07-13 four of its nine names (`sample_transcripts`, `axis_memory`, `archive_top_runs`, `rare_hit_samples`) rendered nothing on `l1_generate`'s floor while two rendered panels (`l1_wounds`, `origin_strengths`) were uncitable. Deleted: `@signal(citable=…)` declares evidence-vs-menu at each renderer, and `citable_fields(layout, exploration_budget)` intersects that with the node's **live layout** — one derivation feeding the prompt's `{{citable_fields}}` menu, the wire-schema enum, and `evidence_grounding_present`. **A citable panel that never renders invites a fabricated citation; the only defence that holds is deriving one from the other.**

## Audit guidance — what to hunt for

Bar for an entry: **high confidence after verification** (call sites traced +
bodies read), not "I spotted a smell." Productive patterns:

- **Premature optimization with apologetic docstring** — protects a scenario that can't happen / fires never. Verify by reading call sites + measuring fire-rate.
- **Redundant double-protection** — two guards on one condition where one subsumes the other. Verify by writing the decision boundaries.
- **Single-caller indirection without architectural reason** — module/helper consumed by one caller, no own test, no layer-boundary justification. Skip splits across a load-bearing layer or with a dedicated test.
- **Dead exception paths / enum variants** — handler arms left after the raising path was deleted. Grep every variant for a construction site.
- **Speculative API surface** — params never read, `X | None` always non-None, default kwargs no caller overrides, fields declared/written never read.
- **Vibe-coded scaffolding** — `NotImplementedError` branches, comments referring to work the project doesn't plan. Verify the "future" really isn't on the roadmap first.

**Anti-patterns — NOT debt, skip on sight:** intentional UI placeholders
(below); per-injection `char_cap`; domain vocabulary policed elsewhere (`origin`
not `baseline`); the `application/intelligence/ ↮ application/optimization/`
layer-invariant split; ABC `@abstractmethod` / `Protocol` `...` bodies; `from
__future__ import annotations`; boundary guards at external-input sites (file
I/O, JSON ingest); validators on `extra='forbid'` user-config models; `_*`
private helpers used by one caller **in the same file**.

**Next-round angles:** `dict[str, Any]` payload soup in hot paths
(`RoundResult`/`CandidateResult`/`PipelineParams`); test-charter violations
(substring assertions, stub-forest tests — suite cap ≤200; count with
`pytest --collect-only -q` before treating the cap as binding);
drifted `Field(description=…)` on LLM-facing schemas; INFO/WARN logging nobody
surfaces; error-raising style diverging by layer (generic `Exception` vs bare
`raise` vs `HTTPException` for the same failure class — M-sized standardization).

## Intentional UI placeholders

UI affordances the product *intentionally* ships disabled today — they preview
the chat-first front door (Lane **C1**, [`chat-foundation.md`](chat-foundation.md)) + the
config-edit + analytics-search surfaces. **Not** scaffolding; not in scope for any "hide
non-functional controls" sweep.

| Placeholder | File | Future surface |
|---|---|---|
| Sidebar-footer search icon (disabled) | `webapp/components/shell/Sidebar.tsx` | analytics search (C4-adjacent) |
| ChatPane attach + textarea + send (disabled) | `webapp/components/chat/ChatPane.tsx` | **C1** chat-first front door ([`chat-foundation.md`](chat-foundation.md)) |
| ChatPane thinking / web-search / code-exec toggles (locked) | `webapp/components/chat/ChatPane.tsx` | assistant tool-use — deferred past **C1** (asyncapi-first; [`chat-foundation.md`](chat-foundation.md) § Deferred — assistant tool-use) |
| AccountModal "Update profile" (disabled) | `webapp/components/account/AccountModal.tsx` | profile editing |
| AccountModal "Remove account" (disabled) | `webapp/components/account/AccountModal.tsx` | multi-provider account mgmt |
| AccountModal "+ Connect account" (alerts, no-ops) | `webapp/components/account/AccountModal.tsx` | multi-provider account linking |

**Rule:** cleanup touching these must distinguish *intentional placeholder* from
*scaffolding*. Milestone-reference text inside them is OK (exempt from the "no
M-milestone references on operator surfaces" grep gate); other operator surfaces
must not leak milestone numbers.

Closed items are not tracked here — `git log` is the history layer.
