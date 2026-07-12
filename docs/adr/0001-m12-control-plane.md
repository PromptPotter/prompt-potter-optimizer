---
status: accepted
date: 2026-05-26
deciders: [maintainer]
consulted: [identity-foundation, spend-and-tenancy]
informed: []
relates:
  - docs/adr/0002-identity-foundation.md
  - docs/adr/0003-spend-and-tenancy.md
supersedes: []
superseded-by: []
tags: [networking, control-plane, wire-contract, m12]
---

# M12 Control-remote — system-networking contract

## Context and Problem Statement

The optimizer ships today with a CLI + a read-only webapp (served at the domain root `/`) that polls `dashboard.json` every 2 s. M12 promotes the webapp into a fully interactive control plane: operators launch, pause, fork, rewind, and reconfigure cycles from the browser. Without a wire contract, every new screen invents an endpoint shape, a state mechanism, and a retry convention; the rich internals (PoBB, Rasch, CQRS ledger, four wound channels) leak through an unprincipled wire and the SaaS port becomes intractable.

How do we constrain the M12 interactivity envelope so that the wire surface is a closed, machine-readable contract that subsequent code measures against — without bulldozing the scientific richness inside the optimizer loop?

## Decision Drivers

* **§0 backbone is already CQRS + event-sourcing.** The per-cycle `.runtime/ledger.jsonl` ledger is the spine; projections are the read side; the §0 I/O kinds taxonomy names the seams. The wire surface must ride existing infrastructure, not add a sidecar.
* **Identity seam is already shipped.** Stage-0 `IdentityContext` (`shared/identity.py`) carries the trust boundary through to `Stores`; M12 commands consume the same seam.
* **Drift detection must be CI-checkable.** A contract that humans-only review is not a contract.
* **The contract must outlive M12.** Subsequent milestones (M13 chat-first user web, M14+ multi-user) inherit the wire surface unchanged.
* **Whitelabel ready.** Every brand / auth / capability element must be per-tenant; the contract must be Stage-1-friendly without grandfather clauses.

## Considered Options

* **A: OpenAPI 3.1 + AsyncAPI 3.0** declared YAMLs in `docs/specs/`. Industry-standard, Spectral + AsyncAPI Studio lintable.
* **B: Pydantic-exported JSON Schemas** generated from existing models. Lighter; no second declaration language.
* **C: Inline markdown tables** inside the ADR. Lightest; no machine-readable contract.
* **D: Statecharts (xstate / SCXML)** on the client side. Formal interaction contract; heavy adoption cost.
* **E: Ad-hoc REST.** No constraint; default trajectory if no ADR lands.

## Decision Outcome

Chosen option: **A — OpenAPI 3.1 + AsyncAPI 3.0**.

The contract bounds the entire interactivity envelope (what a client can send, what it can observe) with two industry-standard schemas. Drift is CI-detectable by off-the-shelf linters. The schemas constrain only the wire — internals remain free. Permanent — the contract stays alive after M12 ships; items move out into `docs/developer/` / `docs/operations/` as they get certified, with checkboxes flipping in this ADR.

The §0 amendment defining the Control-remote I/O kind is the precondition. Schemas are declared in `docs/specs/m12-api-openapi.yaml` (inbound) and `docs/specs/m12-events-asyncapi.yaml` (outbound). Adding a command or event kind requires updating the YAML first.

### Consequences

* **Good** — drift detectable by industry tooling (Spectral, AsyncAPI Studio CLI).
* **Good** — readers recognize the shape from any of 100+ projects using OpenAPI/AsyncAPI.
* **Good** — closed sets bound the interactivity envelope; new feature = YAML update first.
* **Good** — schemas constrain only the wire; the optimizer's internal richness is untouched.
* **Good** — Stage-1-ready: capability gates + identity scoping plug in at Profile C without touching handlers.
* **Neutral** — adds ~6 KB of YAML boilerplate at Profile −1.
* **Bad** — adds `pyyaml` + `types-PyYAML` as test-only dev deps.

### Confirmation

These are the contract requirements the two control-plane YAMLs must satisfy
(verified by review + schema lint — there is no standing `test_contracts.py`;
the structural/contract suite was cut to the silent-harm core, see
[`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)):

1. OpenAPI 3.1 + AsyncAPI 3.0 versions are declared.
2. Reusable trust-boundary parameters (`IdempotencyKey`, `ExpectedVersion`) are required headers.
3. Reusable schemas (`CommandEnvelope`, `CommandAcceptedBody`, `ErrorEnvelope`) exist with the closed error-code set.
4. AsyncAPI `cycleEvents` channel + `ProjectionEnvelope` schema with the required envelope fields exist.
5. Heartbeat shape is declared (security box 15).
6. **Closed outbound set parity** — every `record_type: Literal[...]` in `domain/run_records.py::CycleRecord` is present in the AsyncAPI `kind` enum; extra enum entries must be on the `_PROJECTION_ONLY_KINDS` allowlist.
7. **Anchors table integrity** — every file path in the ## Anchors section of this ADR exists on disk, and no anchor cites a test file (anchors name stable contracts; tests move freely).

CI runs the test on every PR. Spectral lint on the OpenAPI YAML and AsyncAPI Studio CLI on the AsyncAPI YAML wire in as the ADR's schemas accumulate operations / messages.

## Pros and Cons of the Options

### A — OpenAPI 3.1 + AsyncAPI 3.0

* **Good** — industry standard; broad tooling (Spectral, Redocly, AsyncAPI Studio, codegen).
* **Good** — operation-level surface (request/response pairs, status codes, idempotency headers) declared natively.
* **Good** — schemas survive milestone boundaries unchanged.
* **Neutral** — two declaration languages (OpenAPI for HTTP, AsyncAPI for SSE) rather than one.
* **Bad** — boilerplate for the empty Profile −1 scaffold.

### B — Pydantic-exported JSON Schemas

* **Good** — single declaration source (pydantic models in Python).
* **Bad** — no native operation-level surface; status codes, retry semantics, idempotency headers all need a sidecar declaration.
* **Bad** — drift detection becomes custom (no Spectral equivalent).

### C — Inline markdown tables

* **Good** — zero new files.
* **Bad** — not machine-readable; drift detection devolves into custom regex.
* **Bad** — operators reading the contract can't run lint or codegen against it.

### D — Statecharts

* **Good** — formal client-side interaction contract; every transition typed.
* **Bad** — heavy adoption cost; mandates a state-machine library on the client.
* **Bad** — solves the client-side problem but not the wire problem.

### E — Ad-hoc REST

* **Good** — zero upfront cost.
* **Bad** — drift becomes invisible; M12 ships with 6+ uncoordinated POST handlers; the SaaS port hits the wall the ADR is meant to prevent.

## More Information

### Highway architecture

The wire surface rides the canonical per-cycle `.runtime/ledger.jsonl` ledger alongside every other record. The "highway" is the existing Persistence stream; this contract promotes the path commands and events take through that highway to the optimal sequence by eliminating four middlemen (mirroring the spend-and-tenancy arc):

1. **No process global.** `emit_command` (inbound at the FastAPI seam) reads the active ledger from `_CYCLE_LEDGER: ContextVar[CycleEventLog | None]` and the cycle target from `_ACTIVE_CYCLE: ContextVar[CycleId | None]`. `emit_command_ack` (outbound at the runner) reads the same ContextVars. Per-asyncio-task isolation; concurrent commands across cycles get isolation for free.
2. **No wrapper dataclass.** `emit_command(*, command_id, kind, payload, idempotency_key, issued_by_user_id)` and `emit_command_ack(*, command_id, status, detail)` are kwargs-only; both build their `*Record` directly inside the helper. Mirrors `emit_token_usage` verbatim. (`expected_version` is a dispatch-time concurrency check against the ledger offset — it is consumed at the seam, not stored on the record.)
3. **Sole writer per surface.** ONE `CommandDispatcher` writes `CommandRecord` (at the FastAPI seam). ONE `RunnerCommandSubscriber` writes `CommandAckRecord` (at the runner). Outbound SSE frames have no writer at all — the in-process `EventStreamView` fan-out this ADR originally specified was replaced by `CycleLedgerTail`, which tails the on-disk ledger directly (cross-process; the API server, the CLI, or a spawned runner can all be the writer, any reader can subscribe). See [`../developer/event-stream.md`](../developer/event-stream.md).
4. **No dual ingress.** Commands ARE events. The runner subscribes to `CommandRecord` on the ledger as another driver — no in-memory queue, no `commands.jsonl`, no parallel pipeline. The 6 pre-M12 sanctioned POSTs (`POST /forks`, `POST /stop`, `DELETE /cycle`, `POST /cleanup-empty`, `POST /backends`, `POST /backends/{id}/sync`) migrate to ride this highway at Profile B (no-back-compat — they migrate, they don't shim).

Identity scope rides the ledger path (tenant prefix on the per-cycle directory) — no per-record `tenant_id` field. Outbound `ProjectionEnvelope{kind, version, cycle_id, sequence, payload}` is the only frame shape on the SSE channel. Mid-cycle subscribers receive a snapshot frame (matching current `dashboard.json`) followed by the live tail with strictly-increasing `sequence`; missed frames detectable via sequence gap; heartbeat fires every 15 s during idle.

### Closed sets

- **Inbound commands.** `docs/specs/m12-api-openapi.yaml` (OpenAPI 3.1).
- **Outbound events.** `docs/specs/m12-events-asyncapi.yaml` (AsyncAPI 3.0).

### Profile gradient

Each profile is a named, stable conformance level. Newer profiles compose with older ones. Once certified + on disk + tested + documented, a profile's guardrails promote to `docs/developer/` / `docs/operations/` and the checklist boxes flip below.

| Profile | Title | Direction | Auth posture |
|---|---|---|---|
| −1 | Pre-conformance scaffold | none | none |
| A | Outbound highway | server → client (SSE) | auth-off |
| B | Inbound highway | client → server (commands) | auth-off (single operator) |
| C | Signed-in client | bidirectional | OIDC ID Token |
| D | Hub mode | bidirectional | OIDC + per-tenant scoping |
| E | URL-as-truth client | client contract | OIDC + capability gates |

**Profile −1 deliverables (shipped this ADR):** §0 amended; this ADR landed in MADR shape; OpenAPI + AsyncAPI YAMLs scaffolded with empty closed sets; drift invariant test in place. No behavior change.

**Profile A** — `GET /campaigns/{c}/cycles/{cy}/events:subscribe` serves SSE frames by tailing the on-disk `.runtime/ledger.jsonl` (`CycleLedgerTail`) — no projection subscriber synthesizes frames; the ledger is the single medium (superseded the originally-specified in-process `EventStreamView` fan-out, which 404'd for any reader outside the runner's own process). Snapshot-then-tail; boundary sequence explicit; heartbeat every 15 s. Certified: `docs/developer/event-stream.md`; boxes 4, 13, 14, 15 flipped.

**Profile B** — `CommandRecord` + `CommandAckRecord` added to `domain/run_records.py::CycleRecord`. `emit_command` + `emit_command_ack` kwargs-only helpers. `CommandDispatcher` at API seam. `_ACTIVE_CYCLE` ContextVar wired. The 6 sanctioned POSTs migrate to ride the highway:

| Pre-M12 route | Command kind v0 |
|---|---|
| `POST /campaigns/{c}/cycles/{cy}/forks` | `fork-cycle` |
| `POST /campaigns/{c}/cycles/{cy}/stop` | `pause-cycle` (folded — pause is the single operator-interrupt; no `stop-cycle`) |
| `DELETE /campaigns/{c}/cycles/{cy}` | `delete-cycle` |
| `POST /campaigns/{c}/cycles/{cy}/cleanup-empty` | `cleanup-empty-cycles` |
| `POST /backends` | `register-backend` |
| `POST /backends/{id}/sync` | `sync-backend-experiments` |

**Closed inbound set draft (23 commands).** The full enumeration lives in `docs/specs/m12-api-openapi.yaml` and is the single source of truth for the inbound surface; the ADR keeps only the migration table above + the category map below. Categories (= OpenAPI `tags`): cycle-control (pause / step / rewind — `pause-cycle` is the single operator-interrupt, no separate stop/resume-cycle), cycle-lifecycle (fork / delete / cleanup-empty / archive / mint-campaign / start-run), budget (spend / halt / sample), pipeline-params (change-pipeline-param / reset-pipeline-overlay), scoring (change-scoring-composite), operator-feedback (mark / unmark hard-sample / annotate-round / endorse-candidate), backends (register / sync-experiments). All v0 — operator-redline cycle precedes any handler.

First end-to-end command: **`pause-cycle`**. On certification, the dispatcher + runner contracts promote to `docs/developer/command-dispatch.md`; boxes 1–10, 17 flip.

**Profile C** — Stage 1 of identity-foundation. `resolve_identity` swaps from Stage-0 default to OIDC verification. `presentation/api/middleware/oidc.py` populates `IdentityContext` from a verified ID Token. Session cookies are opaque server-side ids, NOT JWTs (identity-foundation no-drift gate #2). On certification, the capability matrix promotes to `docs/operations/auth-and-capabilities.md`; box 10 flips with OIDC-aware semantics.

**Profile D** — `JobRegistry` (new at `application/jobs/`) becomes identity-scoped. Control routes reject cross-tenant `job_id`; SSE fans only the caller's tenant. No bare `tenant_id` parameters (identity-foundation no-drift gate #3). `projects/{install_id}/tenant.json` carries brand. On certification, isolation guarantees promote to `docs/operations/multi-tenant.md`; box 12 flips.

**Profile E** — Webapp `usePoll` → SSE subscription. Every view state reachable via URL; every mutation routes through a command. No client-side optimistic mutations — clients believe only ack frames. Chat-panel launcher (`webapp/components/chat/ChatPane.tsx`) alongside the configuration form. On certification, the client contract promotes to `docs/developer/webapp-state-model.md`; boxes 16, 17 flip; `webapp/lib/usePoll.ts` is deleted.

### Security checklist (20 boxes)

Each box is unchecked at Profile −1 and flips when its enforcement is on disk, tested, and the certified prose is in `docs/developer/` / `docs/operations/`.

Wire-contract integrity:
1. ☐ Every command + event kind has a declared schema in OpenAPI/AsyncAPI YAML *before* any handler lands.
2. ☐ Closed-set policy: new command/event kind = YAML update in its own PR.
3. ☐ Canonical ledger only — commands and acks are `*Record` entries on `.runtime/ledger.jsonl`.

Sole writer / single emitter:
4. ☑ ONE `CommandDispatcher` writes `CommandRecord`. ONE `RunnerCommandSubscriber` writes `CommandAckRecord`. Outbound SSE frames have no writer — `CycleLedgerTail` reads the ledger directly, cross-process. *(Outbound half certified Profile A; inbound halves land at Profile B.)*
5. ☐ `emit_command(*, command_id, kind, payload, idempotency_key, issued_by_user_id)` is the sole inbound helper. `emit_command_ack(*, command_id, status, detail)` is the sole outbound helper. Kwargs-only.
6. ☐ Runner is the sole actuator. No API handler mutates optimizer state directly.

Ambient context:
7. ☐ `IdentityContext` (from `IdentityDep`) + `_ACTIVE_CYCLE: ContextVar[CycleId | None]` carry identity + cycle past the seam.

Trust boundary:
8. ☐ Idempotency mandatory. Every command carries `Idempotency-Key`; dispatcher dedupes via ledger hash lookup.
9. ☐ Optimistic concurrency mandatory on state mutations. `expected_version` required; mismatch = 409 `version_conflict`.
10. ☐ Capability gate on every handler. Even at Profile B (auth-off), handler asks `identity.capabilities`.
11. ☐ CORS policy declared per origin; no `*`.
12. ☐ Per-tenant request scoping verified — Profile D drift test asserts cross-tenant rejection.

Outbound envelope:
13. ☑ Every SSE frame is `ProjectionEnvelope{kind, version, cycle_id, sequence, payload}`. No raw payload variants. *(Profile A — closed envelope shipped; drift test asserts Python `ProjectionKind` Literal matches AsyncAPI enum exactly.)*
14. ☑ Snapshot-then-tail. Subscribers receive a snapshot frame, then live tail with strictly-increasing sequence; missed frames detectable via gap. *(Profile A — `stream_snapshot` frame carries `snapshot_at_offset`; live tail strictly greater.)*
15. ☑ Heartbeat frame every 15 s during idle. *(Profile A — SSE comment line every 15 s when subscriber queue is idle.)*

Client contract:
16. ☐ URL-as-truth: every view state reachable via URL.
17. ☐ No client-side optimistic mutations. Pending commands shown as pending; only ack frames flip to confirmed.

Architectural hygiene:
18. ☑ §0-first. Control-remote definition lands in `architecture.md` §0 before any handler. *(Profile −1: shipped this ADR cluster.)*
19. ☐ One §0 bucket per concept. Pre-flight gate Q1 enforced on every new type / projection / dispatcher.
20. ☐ Anchors table — every claim in this ADR names a file. Drift detector test reads the table and asserts each path exists.

### Pre-flight gate audit

The eight questions from root `CLAUDE.md`, answered:

1. **§0 bucket.** State + persistence (§0's I/O-kinds sub-bucket). Control-remote is the fourth named kind.
2. **Existing channel.** No — Control-remote is genuinely new. Per-cycle ledger Persistence already exists; this contract reuses it.
3. **Name distinctness.** "Control-remote" parallels "Control-local"; grep confirms no collision.
4. **Self-describing.** Yes. Q4 sub-rule (new I/O kind → amend §0 first) is satisfied by this PR cluster.
5. **Ride existing infrastructure.** Yes — commands ride `CycleEventLog.append` as `CommandRecord` on the same `.runtime/ledger.jsonl`.
6. **AI/operator reads from a file.** Yes — closed sets in YAMLs; checklist + promotion log in this ADR.
7. **§0 update.** Yes — landed in this PR cluster as a precondition.
8. **Langfuse trace event.** Commands and acks ride the canonical ledger (already traced). Future command handlers that invoke LLM calls MUST wrap them with `observed_node()`.

### Anchors

Every claim in this ADR names a file. The drift detector reads this table and
asserts each path exists. Anchors cite **stable contract artifacts** — specs and
the code/domain modules the ADR depends on — never test files: tests are
enforcement detail that must move freely, so they are named in prose (see
*Confirmation* above), never anchored here.

| Concern | File |
|---|---|
| §0 Control-remote definition | `docs/architecture.md` (§0 "State + persistence" — I/O kinds taxonomy) |
| §0.5 Control-remote load-bearing-surface entry | `docs/architecture.md` (§0.5 load-bearing surface) |
| Closed inbound command set | `docs/specs/m12-api-openapi.yaml` |
| Closed outbound event set | `docs/specs/m12-events-asyncapi.yaml` |
| Identity seam consumed | `promptpotter/presentation/api/deps.py::resolve_identity` |
| `emit_token_usage` template (mirrored by `emit_command`) | `promptpotter/infrastructure/llm/models.py::emit_token_usage` |
| Sole-writer template | `promptpotter/infrastructure/projections/live_dashboard/view.py::LiveDashboardView._handle_token_usage` |
| `CycleRecord` discriminated union (closed outbound record set) | `promptpotter/domain/run_records.py::CycleRecord` |
| `ProjectionEnvelope` Python wire type (Profile A) | `promptpotter/domain/projection_envelope.py` |
| Outbound highway ledger tail (Profile A; supersedes the originally-specified `EventStreamView` in-process projection) | `promptpotter/infrastructure/projections/event_stream/tail.py::CycleLedgerTail` |
| SSE endpoint handler (Profile A) | `promptpotter/presentation/api/routers/campaigns/events.py::stream_cycle_events` |
| Certified Profile A contract | `docs/developer/event-stream.md` |

### Promotion log

Chronological log of what's been certified out of this ADR into the docs layer. Entry shape: `(date, profile, security-box numbers, target doc path, PR link)`. Empty at Profile −1.

| Date | Profile | Boxes flipped | Target doc | PR |
|---|---|---|---|---|
| 2026-05-26 | A — outbound highway | 4 (sole writer, SSE half), 13 (envelope), 14 (snapshot-then-tail), 15 (heartbeat) | `docs/developer/event-stream.md` | (this commit) |

### Out of scope (forever, or for other documents)

- OIDC client + middleware implementation — Profile C consumes [`0002-identity-foundation.md`](0002-identity-foundation.md) Stage 1.
- `JobRegistry` internal data model — Profile D names the constraints; implementation lives elsewhere.
- Webapp redesign (component-level) — design surface in `BRAND.md` / `VOICE.md`.
- Multi-user merge / CRDT operations — identity-foundation Stage 2+.
- Per-tenant rate limiting / quotas — M13+ backlog.
- L4 inner-cycle execution path — [`roadmap.md`](../specs/roadmap.md) Track 1.5.

### Cross-refs

- [`0002-identity-foundation.md`](0002-identity-foundation.md) — foundation Profile C lights up Stage 1 of.
- [`0003-spend-and-tenancy.md`](0003-spend-and-tenancy.md) — the highway template this ADR mirrors verbatim (first consumer of the identity seam).
- [`docs/specs/roadmap.md`](../specs/roadmap.md) — orthogonal track (L4 self-recursion).
- [`docs/specs/roadmap.md`](../specs/roadmap.md) — Phase 1 prerequisite.
- [`docs/specs/roadmap.md`](../specs/roadmap.md) — end-state product surface.
