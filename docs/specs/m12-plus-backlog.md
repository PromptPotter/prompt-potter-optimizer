# M12+: Backlog

**Version:** 0.2.0
**Date:** 2026-04-28
**Status:** Unscheduled
**Depends on:** M12 (Multi-Connector, Competitor Comparison, Webapp Phase 2)

---

## Context

M12+ is the opportunistic bucket. Items here ship after M12 as user demand, time, and research interest dictate. Nothing here is blocking. Each item is a candidate for its own spec when picked up.

## Items

### Polish & Operations

| Item | Notes |
|------|-------|
| Cost tracking | Token usage and $ cost per campaign / round / variant. Feeds cost-aware optimizer strategies and reproducibility manifests |
| API tests | Deferred from entry-point parity work |
| Health endpoint | `GET /api/v1/health` with dependency status (backend, store, LLM provider) |
| CONTRIBUTING.md | External contributor onboarding |
| Docker hardening | Non-root user, minimal base image, secrets handling |
| Notebook tests | Catch notebook import drift |
| Metrics | Prometheus exporter for campaign progress, queue depth, LLM latency |
| Fat-file splits | Each offender from M9 hierarchy refactor gets its own splitting spec. See `archive/m9-hierarchy-refactor.md` § "Fat Files" |
| Hard-Sample Sorter Phase 2 + 3 | Phase 2: compact ASCII heatmap inline in `log.md` at finalize + round boundaries (`presentation/views/log_md.py::render_hard_sample_heatmap`). Phase 3: webapp heatmap consuming the same `build_hard_samples_artifact` primitive. Both deferred from `archive/hard-sample-sorter.md`; Phase 1 (the primitive) is shipped |

### New Capabilities

| Item | Notes |
|------|-------|
| Multimodal / non-text modalities | RNAseq, X-ray, image, audio. Requires modality-specific evaluation, dataset formats, scoring functions |
| Pipeline variant comparison | Compare pipelines (not just searchpoints within a pipeline). Needs second connector from M12 |
| Non-prompt targets | Optimize scoring functions, fuzzy matchers, retrieval queries, GA settings — not just prompt strings |
| Evolutionary operators | Population-based search (GA / DE) as an alternative to critique-guided generation |
| MCP server mode | Expose PromptPotter as MCP tools to Claude Code and other MCP clients |
| ~~Self-optimization (L4) — completion~~ | **Promoted to M12** ([`m12-multi-connector.md`](m12-multi-connector.md) Track 4). M10 ships partial (`m10-prompt-iteration-framework.md` + `m10-cleanup.md` §3.5 contract pin + self-optimization fixture); M11 ships the connector ([`m11-publication-benchmarks.md`](m11-publication-benchmarks.md) Track 5); M12 ships the outer-loop closure run. The residual blocker that originally lived here (PromptPotter-as-backend adapter + `pipeline.json`) is now M11 Track 5 work. |
| Model comparison matrix | Same benchmark across multiple target LLMs |
| Web scrape ablation | Quality vs cost/latency tradeoff for backends that do web retrieval |
| Public service deployment | Auth, rate limiting, multi-tenancy hardening, billing. Builds on the [`ADR-0002 identity-foundation`](../adr/0002-identity-foundation.md) Stage 1 / Stage 2 contracts + M12's enforcement |
| User-editable `pipeline.json` + initial values in the UI | Operator-flagged 2026-05-07. Today `datasets/{name}/pipeline.json` is filesystem-edited only. Webapp surface for "define your own pipeline" — author the node graph + initial param values directly in the UI. Pairs with the M12 dataset preview view + connector-driven pipeline visualization. Out of M12 scope; logged here so the M12 webapp doesn't accidentally close the door on it |

### Webapp Perf

Discovered during the 2026-05-26 code-debt-cleanup drain. Not debt — forward-looking perf/feature work. Each lifts to its own spec when picked up.

| Item | Notes |
|------|-------|
| SSE client wire-up | Backend `GET /api/v1/campaigns/{c}/cycles/{cy}/events:subscribe` shipped (Profile A — `events.py::stream_cycle_events`). Frames are `ProjectionEnvelope`; snapshot-then-tail + 15 s heartbeat + sequence-gap detection per [`docs/developer/event-stream.md`](../developer/event-stream.md). Client still on 2 s polling. Profile E webapp cutover replaces `usePoll` with `EventSource`; assemble state from snapshot + tail, re-subscribe on sequence gap |
| SWR / TanStack Query migration | Replace hand-rolled `webapp/lib/{poll.tsx,useFetch.ts,usePoll.ts}` with library-native conditional polling + 304 plumbing + dedup. **Blocker:** no `vitest` harness over `lib/` today (webapp/CLAUDE.md "Testing posture"); smoke-only migration of polling logic is risky. Land the harness first |
| Virtualize `HardSamplesTable` | Workspace-scope `hard_samples_workspace*.json` files on disk hold 294–451 candidates today (3-4.5k cells per render). Gate is met for `@tanstack/react-virtual`. Capture a Chrome devtools Performance trace on a workspace-scope view first — React Compiler may have closed the practical gap |
| Strip redundant manual memos | React Compiler enabled (`reactCompiler: true`). `React.memo` / `useMemo` / `useCallback` wrappers are now mostly redundant. Keep the structural-fingerprint patterns (`l1RoundsKey`) — those encode domain knowledge the compiler can't infer. 65-file audit; benefits from runtime profiling under compiler |

### Research Extensions

| Item | Notes |
|------|-------|
| Further OptSearchPoint refinement | Advanced L1/L2/L3 strategies surfaced during M11 ablations |
| Diminishing returns detector | Critique (anomaly flag) + L2 (strategic context) — signal when optimization is plateauing |
| Candidate diversity monitor | L2 — detect mode collapse in candidate generation |
| Query improvement attribution | Critique (this-round) + L2 (cross-round patterns) — track which prompt changes drove which query flips |
| Cross-candidate failure diff | Critique — missed opportunities from non-winner candidates |
| Failure group refresh in loop | L2 — periodic recomputation of failure group × axis correlations during optimization |
| Additional benchmarks | Beyond HotPotQA + GSM8K — MMLU, BBH, LiveCodeBench, domain-specific |
| Longer-horizon ablations | SearchMemory value over N campaigns, learning curves |
| Human evaluation | Qualitative prompt quality assessment beyond task accuracy |

## Prioritization

None. M12+ items are pulled based on user demand, research direction, and who's asking. When an item becomes hot, promote it to its own spec in `docs/specs/`.
