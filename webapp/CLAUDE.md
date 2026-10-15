# webapp — CLAUDE.md

Next.js + React + TypeScript, static export at `out/` mounted at the domain root by FastAPI: **the app owns `/`, the API is the carved-out `/api/v1` namespace.** A control-plane dashboard over served cycle state — it polls, it renders, it issues control verbs (pause/resume/fork/steer/set-limits/…) through `POST /commands/{kind}`; it never recomputes a score itself. Installed versions are in `package.json`.

## Load-bearing

The rules a change here breaks most often. Each names the section that states it.

- Never compute a score, ordering or mask → § Scoring authority
- One source per data class → [`lib/CLAUDE.md`](lib/CLAUDE.md) § Display-data sources
- One address, `viewedPath` → [`lib/CLAUDE.md`](lib/CLAUDE.md) § Viewed identity — one address (CyclePath)
- Classify a failure, never bucket it; one hook per direction — `useRead`, `useCommand` → [`lib/CLAUDE.md`](lib/CLAUDE.md) § Failure handling — classify, don't bucket
- No hand-rolled primitive; teaching prose is never `title=` → [`components/CLAUDE.md`](components/CLAUDE.md) § Component conventions
- The masthead owns "where is this run"; no pane repeats it → [`components/CLAUDE.md`](components/CLAUDE.md) § Component conventions
- Barrel order IS the cascade → [`app/CLAUDE.md`](app/CLAUDE.md) § Stylesheet organization (cascade order is load-bearing)
- No raw px for type or space, no new breakpoint → [`app/CLAUDE.md`](app/CLAUDE.md) § Stylesheet organization (cascade order is load-bearing)
- A wire type is generated, never written → [`lib/CLAUDE.md`](lib/CLAUDE.md) § A wire shape is GENERATED — never hand-declared

## Owned elsewhere

Each rule binds this layer but is stated by the file governing the artifact it constrains. Read the owner before changing anything it covers.

- **Surface behavior contract** — owned by [`../docs/specs/frontend-surface-contract.md`](../docs/specs/frontend-surface-contract.md); every user-facing PR here is measured against its `I*` invariants, and this file owns only the implementation side.
- **Scoring authority** — owned by [`../docs/architecture.md`](../docs/architecture.md) §0.5; here it lands as § Scoring authority.
- **Visual identity and copy register** — owned by [`BRAND.md`](../../promptpotter-web/BRAND.md) in the sibling site repo (`runfish5/promptpotter-web`); never introduce a parallel design spec, tokens doc or theme-decision file, and read every value as `var(--…)`.
- **What `dashboard.json` guarantees its readers** — owned by [`../promptpotter/infrastructure/CLAUDE.md`](../promptpotter/infrastructure/CLAUDE.md) § Persistence; poll it and trust it, never reconstruct a served field from a second source.
- **Which model a new field lands on to reach a panel** — owned by [`../docs/developer/adding-a-surface.md`](../docs/developer/adding-a-surface.md) § 3; a panel reading a field no writer sets is the half-wiring that recipe exists to prevent.
- **The lineage tree — forks, crowns, θ** — owned by [`../promptpotter/infrastructure/store/CLAUDE.md`](../promptpotter/infrastructure/store/CLAUDE.md) § The lineage tree; this layer renders what `/tree` serves and derives no lineage of its own.
- **The check list this layer's gate runs** — owned by `scripts/gate.py`; run `python scripts/gate.py --web`.
- **Where a distributor's brand values come from** — owned by [`../deploy-linux/README.md`](../deploy-linux/README.md) § Running it under your own name; a new `lib/brand.ts` field is unreachable until `deploy-linux/brand-env.sh` exports its `NEXT_PUBLIC_*`.

## Subdirectory contracts

| Subdirectory | Owns | CLAUDE.md |
|---|---|---|
| `app/` | The stylesheet barrel, tokens, overflow and box sizing. | [`app/CLAUDE.md`](app/CLAUDE.md) |
| `lib/` | Display-data sources, generated wire types, the viewed address, polling, failure handling, brand. | [`lib/CLAUDE.md`](lib/CLAUDE.md) |
| `components/` | Layout tiers, primitives, component conventions, render-cost guards. | [`components/CLAUDE.md`](components/CLAUDE.md) |

## Scoring authority

Shapes this layer must not introduce, each of which arrives looking reasonable:

- **A local re-sort.** An ordering *is* a score. `/datasets/{name}/cells` is a served ranking, not a list to `.sort()` on whichever field is in hand.
- **A recomputed mask.** A scoring or sample-set masked value comes down as a served overlay, as do the realized criterion's dials and the levels they are anchored at (`composite_fitness_weights`, `composite_fitness_anchors`); deriving either here re-answers the question under the client's guess at the formula. A lens is a `per_cell` formula, so its vocabulary is the served `CELL_TERM_META` and its value is exact on any sample subset. **A criterion is ONE value and ONE rendering wherever scoring surfaces** (`components/shell/scoring/Criterion`) — the check-in, the masthead, the live-state card, the candidates card's mask, a Compare channel — in three rungs of one frame: the line, the dials, then the formula. A new scoring surface hosts that component at a rung; it never draws its own. The value is a discriminated union over the two ways to say a criterion, with `lensOf` the single place either `?lens=`/`;lens=` spelling is minted. **A dial is a weight and nothing else here**: the browser sends `dials:<term=weight,…>` and the server realizes it against the campaign's own anchors, so the formula a fork carries is the served `lens_criterion`, never a string assembled from the sliders. Say **mask** (the alternative criterion) and **lens** (the selector), never "what-if". A served lens is a string the wire already collapsed, so it re-opens in Expression mode — decomposing one back into weights is the formula parse this rule forbids. Ownership does not collapse with the form: the dashboard's mask is module state, a Compare channel's rides its own address.
- **A number kept alive under a changed setup.** The two masks re-PROJECT the record from rows that exist. A node parameter, a model or a prompt field is not: nothing ever ran at the edited value, so every measurement at that searchpoint *and everything descending from it* describes a search that did not happen. The honest render is `?`, and the closure is `descendantsOf` over every served `parent_ids` edge — which crosses forks. Say **invalidated** (no reading exists), distinct from **divergent** (a counterfactual the server computed) and **retired** (the record of what ran); each wears its own class and its own words in the node's `<title>`. Diff a values-editor emission against its seed before reading it as an edit — `NodeSurface` emits the point's WHOLE running config.
- **A fabricated default.** `noUncheckedIndexedAccess` is ON, so an index access types as possibly-`undefined` — **never `?? <default>` to silence it.** `edits[key] ?? r.value` is not `edits[key] !== undefined ? … : …` the moment an operator clears a field to `""`. Handle the miss (skip / early-return); `!` only where the line above proves presence.

The pure helpers in `lib/derivations/*` are not an exception: they group, lay out and format served data, and introduce no number that was not already on the wire.

## The chat thread — rendered, never authored

Three rules a plausible edit undoes. Positioning and the open conversational endpoint are [`../docs/specs/chat-foundation.md`](../docs/specs/chat-foundation.md)'s; these are the implementation half.

- **The imprint is the generic `step`.** Everything the Potter *does* — an optimizer LLM call, a web search, a code execution, an MCP call, a backend match — is one `step`: icon + label + status + optional duration/cost. A new `ProjectionKind` reads into that same family; **no new item kind.** `candidate` and `round` are the optimizer specialization layered on top, and they are what a reusing team deletes (`components/chat/README.md`).
- **Activity and decision items are rendered, never authored by the client** — message items are the only ones the operator and assistant write, and that line is what keeps the thread honest. **One deliberate crossing:** a run that has ENDED is neither, so the client freezes a snapshot of *served values* into the list as a `run` item. Values rather than a pointer, because `resume` re-animates `dashboard.json` and `resume --from N` rewrites the round files beneath it.
- **A non-item is not the same as discarded.** `sample_order` yields no item, but `LiveDashboardProjection` absorbs it to `declared_sample_order`, and that one served copy drives the run card's "next in line" (the served `dash.walk`) — the stream carries no second one to read off a frame. A *declared* order, never a promise: PoBB can stop a candidate before its tail is reached, so no surface may word it as "will".

## State reset on prop change

When a component or context must drop derived state because an identity prop changed (the viewed `(campaignId, cycleId)` switched, etc.), use the **render-phase guarded reset** — React's sanctioned "adjusting state when a prop changes" recipe:

```tsx
const [prevKey, setPrevKey] = useState(key);
if (key !== prevKey) {
  setPrevKey(key);
  setDerived(EMPTY); // ...clear every key-scoped field
}
```

It runs **during render**, so the reset and the re-render commit together — no stale frame. **A `useEffect` reset runs after paint and flashes one frame of the prior unit's data; never use it for this.** A hook owning a single state object may instead derive freshness purely: stamp the loaded data with the key it was fetched for and return `EMPTY` until the key matches.

**A reset may SEED from view memory instead of clearing** — `lib/view-memory.tsx`, one localStorage record per campaign with TTL and LRU applied in the codec so no caller can forget them. It is `useSyncExternalStore`-backed, so a restore rides the same render-phase reset rather than a post-paint effect. **A DISPLAY preference is not view memory** and must not be filed there: it is per device and permanent, so it rides `useLocalStorage` under one named key (`lib/theme.ts`, `lib/tree-prefs.ts`) and expires with neither the TTL nor the LRU. Nor is it a `UserSettings` field — no server logic reads how this browser draws.

**Nothing in that record is a measurement, and that is a hard rule** — ids, flags and UI keys only. The NAVIGATION axis (`viewedPath` + `viewedCandidateId`) is remembered — every field in it is an id. The INSPECTION axis (`SelectionContext.candidate`) is ids and a label too, and is not remembered: nothing restores it yet. A scoring or sample-set mask is excluded for the same reason: restoring one silently means the operator reads masked numbers as the record.

## Testing posture

The gate is `python scripts/gate.py --web`, which is what CI's `webapp` job runs; what to run between edits is [`reasoning-doctrine.md`](../docs/developer/reasoning-doctrine.md) `<wall-clock>`'s. Two things about it are this layer's: **the standalone typecheck is what makes `strict` real**, because `next build` alone does not hard-fail on every type error; and **Vitest is scoped to reader-side derivations and the two stores under them** — pure data → data helpers, plus `lib/read-cache.ts`'s clock and `useRead`'s gone verdict — with display components left to smoke. **A fixture is the SERVED shape, built from the generated types** by the typed builders in `lib/test-fixtures.ts`; a banked `dashboard.json` cast to a served type asserts fields the wire no longer carries.

**The third axis is the BROWSER.** `npm run e2e` builds the export and walks it through the real API, as far as a campaign taken from nothing to a measured round behind `PP_E2E_SPEND=1`. It owns "does it actually render against a served cycle" — so **a component-render test earns its place only for a regression class none of compile, smoke, the derivations and the walk can catch.** The gate's `playwright` check runs the `cold` tier; the other two are yours to run. Runbook, the three worlds and the rules for adding a spec: [`e2e/README.md`](e2e/README.md).

Smoke-test by hand at `http://localhost:8001/` for what the walk does not assert — how a thing LOOKS, and the states it cannot mint. **Two states, two harnesses:** open `:8001` as-is for **anon**, or relaunch with `PROMPTPOTTER_AUTH=off` for **authed + live**, where `deps.py::resolve_identity` short-circuits to the CLI's resolver so every auth-gated read resolves to your real on-disk campaigns at zero spend. Reserve the Dex harness ([`../dev/oidc-local/`](../dev/oidc-local/)) for the one thing it cannot reach, the real Google OIDC round-trip.

## Build + run

**One build mode (`next.config.ts`), and no env switch may add a second.** `npm run build` is what the gate, the release workflow and the deploy scripts all run: the React Compiler pass and full-bundle source maps are simply what a build is. Turbopack's persistent cache holds one configuration, so a same-mode rebuild is cheap and a second mode would evict it on every alternation. **The maps serve the deployed box only** — `build_release.py::stage_webapp` strips them. Type-check and lint run inside no build, being separate gate checks.

**For visual work run `npm run dev`, not a rebuild per change.** It serves :3000 with Turbopack HMR and proxies `/api/*` to :8001 via `next.config.ts::rewrites`, which exists for this and nothing else. **The port picks your harness:** :3000 covers the anon and `PROMPTPOTTER_AUTH=off` surfaces, while the OIDC redirect is bound to :8001, so a real login round-trip needs the built app.

`out/` is what FastAPI mounts at `/`, so that is what a built change reaches. **A rebuild swaps every chunk hash under every tab already open on `:8001`**, surfacing only when one asks for a lazy route and 404s; `ui/ErrorBoundary` matches that and reloads once. The "once" is a `sessionStorage` stamp, and where storage is blocked the boundary reloads **nothing** and asks instead — an unbounded auto-reload is a reload LOOP.

## Stack drift

Check `package.json` for what is installed rather than assuming a version. The block below is written and re-added by `next dev` — it says the rest.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
