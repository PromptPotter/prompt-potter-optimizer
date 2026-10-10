# presentation/ — entry-point adapters (read-only over application/)

The thin shells wiring the operator's entry points (`cli/`, the terminal-only
prints in `terminal/`, the FastAPI `api/` the webapp reads) to one orchestration
layer. **How many ways in there are, and the parity rule over them, is owned by
root [`CLAUDE.md`](../../CLAUDE.md) § Working principles** — this layer must give
every adapter it wires the same orchestration call, never a private path. Orchestration itself lives in
[`../application/CLAUDE.md`](../application/CLAUDE.md)'s tree and the disk
seams in [`../infrastructure/CLAUDE.md`](../infrastructure/CLAUDE.md)'s;
what may not happen here is § Out-of-bounds.

## Layout

| Module | Owns |
|---|---|
| `cli/` | `commands/verbs.py` (each verb, once), `campaign_runner.py` (`main()`), `session.py` (session-state plumbing — `SessionCtx` typed accessors), `parsers.py` (argparse off `VERBS`). Thin shells over `application/runner/` and `application/initialization`. **Two launch verbs:** `new` (dataset name *or* raw file) + `resume`, each the command its flags spell — `mint-campaign` / `start-checkin`, `start-run` / `fork-cycle` — dispatched under the terminal's run-invocation as DATA (`CommandDispatcher(inline=Inline(…))`, `jobs/launcher/launch.py`), whose outcome carries the held run this process then drives — so a launch from here is a `CommandRecord` like the browser's, and no callable crosses the seam. Every verb in `commands/lifecycle.py` and `commands/bench.py` is a thin shell over `CommandDispatcher` too, so a terminal interrupt lands on the ledger exactly as the webapp's does. **The terminal-only launch flags are declared, each with why no payload carries it:** `--no-wait` and the y/N prompts answer a person at THIS process (a web launch always queues and has its own confirm), `--verbose` and the readout sink are this process's stdout, and `--tenant` is the Identity adapter's. Which command kinds the terminal reaches, and by which verb, is `CommandKind.cli_verb` (§ Sample look-ahead). The raw-file form folds onto the durable check-in path — `new`'s `Path(arg).is_file()` branch (`commands/new.py::_hold_ingested_checkin`) ingests, then rides the same seams the web does: `--set`, the resolver turn and Start each dispatch through `application/commands/` (`draft_editing.py::dispatch_draft_patch` / `origin_resolving.py::dispatch_origin_resolution` / `launching.py::dispatch_start_checkin`), and the loop runs **inline**, its readout sunk to stdout, where the web Start (`/commands/start-checkin`) **detaches**. Run-invocation — `HeldRun.run_inline` where the web calls `HeldRun.detach` — is the only CLI↔web difference, and stays so only while none of those seams is CLI-private. There is no separate `ingest` verb. |
| `terminal/` | **What only a terminal prints**: `completion.py` (what a terminal or a notebook cell does with a finished cycle — the ANSI box + the IPython winner dump; the LAUNCH half is `application/embedded_run.py`), `startup_checklist.py`, `server_banner.py` (the API server's one-time startup banner, printed by `main.py`'s `lifespan()`). The run readout is NOT here: `ReadoutProjection`, the view builders (`ingress.py`) and every renderer — ANSI primitives, `to_text`, markdown — are the **application's emit contract** and live in [`application/views/`](../application/views/), because a cycle launched with no terminal writes the same readout. Presentation imports them UPWARD (`presentation → application`, the only direction `scripts/gate.py::_layering` admits, with no exemption). Disk-side reconstruction (`from_disk_log`) lives in `application/runner/output.py`. |
| `api/` | FastAPI. One module per router under `routers/` — **except where one resource carries several unrelated concerns**, which becomes a package whose `__init__` imports its route submodules so their decorators run (emptying it mounts zero routes) and whose `_router.py` owns the single `APIRouter` every submodule decorates. E.g. `datasets/` (`index` = what is here · `ingest` = the four writes · `leaderboard` = the measurement reads, composed by `application/scoring/measurement_log.py::measurement_log`). `deps.py` chains `resolve_identity` → `IdentityDep` → `build_stores_from_identity` → `StoresDep`. Dataset reads go through one gateway, `store/dataset_access.py::readable_dataset_dir` (tenant content, then install content), 404ing an unresolvable slug — a **resolver, not a capability gate**, since install content is git-tracked and gating it guards nothing. **Its campaign-scoped sibling is `application/pipeline_resolve.py::resolve_pipeline_for_campaign`**, the one gateway for what a campaign RUNS (`architecture.md` § Two resolution seams). It consumes `readable_dataset_dir` and is never bypassed: a route serving a dataset default where a caller asked about a campaign is the scope error that split exists to prevent, and unlike the dataset gateway this one IS ownership-gated (`infrastructure/store/stores.py::owned_campaign`, 404 on cross-user). Authorization lives elsewhere: host privilege is the ADR-0004 channel rather than any capability, and the command-verb gate is `_require_capability_for` (`application/commands/dispatcher.py`) over `CAMPAIGN_CAP_BY_NAME`. Both spell their denial once, in `shared/identity.py::require_capability` — so does the third gate, which is **not** on a route: the three ingresses that mint a durable check-in campaign (`/datasets/ingest`, `/datasets/{name}/draft`, `/origins/{id}/draft`) are gated one layer in, at `create_checkin_campaign`, because minting is the act `mint-campaign` already answers to `campaign.create` for and a per-route copy leaves the next door open. An OIDC swap replaces only `resolve_identity`; every route keeps consuming `IdentityDep` / `StoresDep` unchanged. |

## Out-of-bounds

- **No campaign-artifact writes from entry-point code.** Disk writes go
  through `CycleEventLog.append` (orchestration) or a declared projection
  (display); the per-cycle markdown writers (`log.md`, `review.md`) live
  in `application/runner/output.py`.
- **No business logic here** — `cli/` and `api/` parse, check, page and format. WHICH rows and in WHAT ORDER is `application/`'s, because the CLI, the skill, the embedded launch and any agent tool cannot import a router without FastAPI — the next entry point writes a copy. `CampaignStore.list_campaigns` is the sole lifecycle/owner filter: pass through, never re-filter.
  **A router imports no `promptpotter.infrastructure` module** (`routers/auth.py`, the Identity adapter, excepted): it receives `Stores` from `StoresDep`, hands it and the request's `CyclePath` to ONE application function, and names a response model from the application module that answers with it. A per-cycle read enters by `../application/cycle_reads.py::view_cycle`.
  One manifest, one parser: `application/pipeline_resolve.py::resolve_pipeline_for_optimizer` reads `resolve_optimizer(name, {}).schema`, the resolution every run shares, parsed by `parse_pipeline_response`. The prospective origin id is `application/origin_listing.py::prospective_origin_id` — deliberately NOT the campaign resolution: it answers for a dataset with no campaign yet, so it rides `resolve_pipeline_config_params`, never `resolve_pipeline_for_campaign`; collapsing the two is the scope error the split exists to prevent.
- **One orchestration layer under every adapter.** A behavior reachable from the CLI but not the notebook or webapp is a bug. **`seed-screen`, `noise-floor` and `decision-bank` are CLI-only and declared so**: each scores outside every cycle, so no command kind or control launches one — the diagnostics fence ([`persistence-and-state.md`](../../docs/operations/persistence-and-state.md)), not a parity gap.

## No ad-hoc mutating routes

**Add no mutating route touching campaign / cycle state** — that is
Control-remote highway territory, and out of charter here. Every campaign-state
write goes through `CommandDispatcher` and the sanctioned endpoints listed
below; a route that writes beside it is the bug this section exists to stop.
The webapp drives the loop through exactly those — it is a control plane, not
a viewer.

This is *not* a ban on every mutation in the codebase. **Identity-surface
administration** (editing the sign-in blocklist, provider config) is a
different I/O kind — it is delivered by an **operator-admin channel**
(a deployment-side, outbound-only companion such as
`presentation/admin_bot.py`), **not** an inbound API route and **not**
`/commands/{kind}`. Permanent contract:
[`../../docs/adr/0004-operator-admin-channels.md`](../../docs/adr/0004-operator-admin-channels.md).
Don't reach for the command highway when the right home is the
operator-admin channel.

**Sanctioned mutating endpoints:**

- `POST /commands/{kind}` — the Control-remote highway. Closed inbound
  set declared in `docs/specs/api-openapi.yaml`; sole writer is
  `CommandDispatcher` (`application/commands/`). Every
  command is appended to its target ledger (per-cycle, campaign root
  cycle, or workspace ledger at `projects/{tenant}/.workspace/events.jsonl`)
  as a `CommandRecord` and answered by a `CommandAckRecord`. **Who writes
  which ack** — owned by
  [`../../docs/adr/0001-m12-control-plane.md`](../../docs/adr/0001-m12-control-plane.md);
  this layer writes none.
- `POST /datasets/ingest` — multipart CSV upload; mints a durable `checkin` campaign and returns its `DraftCampaign` (`draft_id` IS the `campaign_id`; declared in `docs/specs/api-openapi.yaml`; spec at `docs/specs/roadmap.md` § Ingest + chat-first web). Workspace-scoped, identity-bound; the check-in shows in the sidebar + survives a restart, but nothing runs until the operator starts it via the separate `/commands/start-checkin` verb. Mutation verbs (`edit-draft-campaign`, `resolve-origin`) key on the `campaign_id` and persist to `campaigns/{id}/checkin/` via `CheckinDraftStore`.
- `POST /datasets/draft/candidate-library` and its `/from-column` sibling — **ingresses, not write paths.** A multipart upload and a column name are two ways to *derive* a `candidate_library`; both hand the derived terms to `draft_editing.py::dispatch_draft_patch` and dispatch as `edit-draft-campaign`, so the edit is a `CommandRecord` on the check-in ledger. **Any** ingress mutating a draft outside that function is a bug. It is not in a router, because a CLI verb cannot import one without FastAPI and writes a narrower copy instead; the RULES it applies live in `application/datasets/draft_patch.py`.

**A 200 body never justifies bypassing the dispatcher.** `edit-draft-campaign` / `resolve-origin` / `start-checkin` are typed routes because each answers a domain object rather than a `CommandAcceptedBody` — but they dispatch through `CommandDispatcher.dispatch_checkin_command`, whose `CommandOutcome.result` carries that object back. Applying inline instead records **no origin edit on disk, nor who made it** — a violation of `architecture.md` § Control-remote ("sole `CommandDispatcher`"). The target is the check-in cycle `cycle_chk_*`, which exists from the first ingest action and is retained across the flip to `active`; a fork inherits its records via `CycleEventLog.inherit_from`. If a future verb needs a bespoke response, give it a typed route — never its own write path.

## Sample look-ahead — the one entry point that is deliberately NOT at parity

**`/commands/set-sample-lookahead` is browser-only, and the ABSENCE is the boundary.** A missing CLI verb, config key or dataset knob is the gate here, never an oversight to fix — this is the one deliberate `<entry-point-parity>` inversion in the repo.

It is the sole `None` in `domain/command_kinds.py::CommandKind.cli_verb`, a column no row can omit, so the absence is **declared** rather than merely unimplemented.

Who may press it, what one press buys, and why the overshot sample is discarded and never recovered — owned by [`../../docs/operations/access-model.md`](../../docs/operations/access-model.md) § host-admin ↔ user.

## Everything on stdout is also on disk

**Emit nothing to stdout that is not also findable as a file someone — or
something — can open later**, and write markdown only to documented paths.
The general rule is owned by root [`CLAUDE.md`](../../CLAUDE.md) § Pre-flight gate;
this section owns the terminal stream's half of it.

`ReadoutProjection._write` (`application/views/readout.py`) is the single funnel for the
live readout: it writes every line to **its cycle's `readout.log`** (`CycleLayout.readout`),
bound where the ledger is at EVERY entry point, so N parallel runs write N files and a fork's
lines follow the fork. **Stdout is one optional sink of it** — this layer's whole part is the
sink the CLI hands `build_run_observers`, so a terminal can print nothing the file lacks.
**`logs/latest-readout-path.txt` only NAMES the newest terminal launch's file** — never a copy,
and written by the CLI seam alone (`cli/commands/launch.py`): relative to a CWD, it means
nothing for a server-launched or inner cycle. The file write is best-effort: a filesystem
error disables it, never aborts the campaign. It carries the **readout** stream only;
`logging`-level warnings are not in it.

**And the converse: a value already on disk is ADDRESSED here, never reprinted.** The
readout is a map — phase and round rules, candidate boxes, verdicts, the cost spine —
so a payload belongs in it only if nothing else holds one. Name the canonical home plus
the size and move on (`ansi.py`'s `Values:` legend and its L2 pointer are the pattern).
Reprinting is not merely bulk: a copy that re-emits per round, or strips the key→value
association the record keeps, is *worse* than the record it duplicates. A `[:N]` slice is
not the fix either; it makes the log neither readable nor addressable. If a value has no
on-disk home, that is the bug — give it one first.
