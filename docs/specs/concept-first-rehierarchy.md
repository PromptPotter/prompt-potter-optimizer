# Concept-first re-hierarchy — axis flip for AI commit throughput

> Status: **INVESTIGATED — NOT PURSUED (2026-06-16).** Design retained below for the record.
> Two boundary-validation probes (read-only, zero files moved) both falsified the carve:
> see §7. The premise (the smear is real) holds; the *remedy* (slice it concept-first)
> does not pay in this repo's hot area, because that area's core is the shared spine.
> Premise from the measurement below, not intuition. This was the anti-hill-climb:
> carve one slice, re-measure, continue only if the number drops — it never got to carve.

## 0. Why (measured, not vibed)

The repo is cut **layer-first** (`application/ infrastructure/ presentation/ domain/`
— a Clean-Architecture horizontal cut). Every *change* an agent makes is a **vertical
concept** (a searchpoint field, an escalation state, a metric, an ingest step) that has
to be threaded through its type → store → projection → view → api → cli. One idea, six
layer-homes. So **every feature change is multi-directory by construction**, the agent
pays the read/edit token cost of finding the same concept in six neighborhoods, and
miss-a-layer is a bug generator.

Evidence pulled from git (last ~400 commits, `fix:` arc):

- Fix blast radius is wide: a large share of fixes touch **3–6 directories at once**;
  the tail runs to 16 and one to 32. Single-directory fixes are the minority.
- Fix hotspots co-occur in a fixed signature: `application/optimization` (23),
  `presentation/views` (14), `infrastructure/projections` (14), `presentation/api` (9),
  `infrastructure/store` (8), `application/scoring` (7). These are not independent —
  they are the **same concepts re-touched across layers**.
- Proof case — the m13 ingest feat touched **28 files across all six top-level layers**
  for *one* concept ("tenant dataset ingest"): bootstrap, datasets, jobs, runner,
  connectors, 3× domain, llm, 2× projections, 3× store, 4× api, shared.

The hierarchy is cut on the **wrong axis** for an AI editor. This is the local basin.

### 0.1 Premise verified against the fix log (not just the feat case)

Before committing to the flip, the `fix:` arc (last ~200 fixes) was re-read to separate
*one-concept-smeared* (which concept-first collapses) from *genuinely-multi-concept*
(which it will not, and should not, collapse):

- **The smear is real.** ~half of code-touching fixes hit **3+ top-level dirs**;
  single-dir fixes are the minority (~20%).
- **It is concentrated in one recurring signature.** The same cluster —
  `application/optimization` + `infrastructure/projections` + `presentation/views` + a
  `domain/` state type (`opt_search_point` / `results` / `run_records`) — recurs across
  *display, optimizer, sweep, and round-trace* fixes. That is **one idea — a round's
  state and how it is traced → projected → rendered — smeared across layers.** This is the
  genuine pain and the highest-value target.
- **Not every wide fix is a smear.** The security fix (CORS + upload-cap + CVE floors)
  and the identity fix span 4–6 dirs because they are *several unrelated concepts at
  once*. Concept-first leaves these wide **by design** — colocation cannot and must not
  fold genuinely-distinct concepts together. The gate must not count these as failures.

The takeaway redirects the first slice (see §3): the recurring pain is the
**round-state→projection→render thread**, *not* ingest.

## 1. The flip

**Concept-first on the outside, layer-clean on the inside.** Package-by-feature,
layer-within.

- Outer axis becomes the **concept** (`ingest/`, `searchpoint/`, `scoring/`,
  `escalation/`, `views/`…). A concept folder owns *everything that changes together*:
  its domain type, its store fragment, its projection, its render/view, its api glue.
- Inner axis keeps the **dependency direction** clean (type → store → projection →
  view → api). The layer rule is demoted from "what directory am I in" to "which
  direction may I import *within* a slice."
- **Read-one-place, edit-one-place.** That is AI token efficiency — measured by the
  per-edit-episode cost gate in §2 (not the demoted dir-count).

### What this is NOT

- Not a from-scratch redraw. Clusters that already exist (optimization, scoring, ingest,
  views) **stay** — we stop slicing each into six layer-shards, we don't reinvent them.
- Not abolishing the layers. The reason the layers exist — **backend-pluggable,
  read-only, dependency-inverted** (the core product pitch) — is preserved *inside* each
  slice. Genuinely-horizontal infrastructure that does **not** co-change with one concept
  stays shared: the llm client, the generic store kernel, the dispatch hub, identity.

### The shared-vs-owned boundary rule (the duplication firewall)

The whole risk of package-by-feature is copied **mechanism** — each slice re-implementing
persistence/projection/render primitives instead of riding the one canonical channel.
That would directly undo the seam-enforcement consolidation. So the line is explicit:

> A file stays **shared residue** — never inside a slice — if it is **mechanism used by
> more than one concept**: the projection kernel, the generic store kernel, the ledger,
> tracing, identity, and the central state types (`opt_search_point`, `search_point`).
> A slice owns concept-specific **glue only**, and must keep **importing** the kernel —
> never inline or re-express it.

Operational test when carving: if a file's fix history shows it co-changing with **≥2
distinct concepts**, it is shared residue, not slice-owned. Drawing this line wrong is
exactly the "field smeared across N slices" failure in the trade-off below; drawing it
right keeps duplication near zero.

### The trade-off (named honestly)

Concept-first optimizes the **common** single-concept change but **taxes** two cases, and
the gate must judge them fairly rather than pretend they vanish:

- **The rare truly-horizontal change.** A field that genuinely flows through *every*
  projection/view now smears across N slices instead of living in one layer. The boundary
  rule keeps mechanism shared, but a cross-cutting *value* still pays. This is the cost we
  accept to make the common case cheap.
- **The half-migrated steady state.** Per §4 the repo is part concept-first, part
  layer-first **for a long stretch** — not a transient. A change crossing the seam carries
  two mental models at once. The gate measures the carved slice against a control *in that
  mixed state*, so the number reflects reality, not an idealized fully-migrated repo.

## 2. The metric (falsification gate)

Throughput is no longer a vibe — but the obvious metric is a trap. **"Distinct top-level
dirs touched" drops the instant you colocate the files**: that measures the *move itself*,
not whether the agent got cheaper (it still reads the same total code). It is circular,
and it has no control for task difficulty. So it is demoted to a secondary, explicitly
labelled *expected-to-drop-by-construction* signal — never the gate.

The gate is the **real edit cost**, measured against a control:

- **Primary proxy:** **files opened/read + files edited + read/edit token cost per
  edit-episode** that touches the carved concept — the cost of *finding* the concept and
  changing it, which is what colocation is supposed to cut.
- **Control:** the same metrics on an **un-carved concept** touched in the same window
  (e.g. the optimizer escalation thread). The carve passes only if the carved slice gets
  cheaper **relative to the control's trend** — this separates the carve's effect from
  drift in task difficulty over time.
- **Before:** baseline the proxy on the last N changes that hit the concept.
- **After:** re-measure the next M changes that hit the carved slice.
- **Pass:** carved-slice edit cost drops measurably below the control trend → carve the
  next slice.
- **Fail / stop:** if it does not, we **halt one slice in**, revert the single carve
  commit cheaply, and rethink — before the map is burned down. This is the whole point of
  going incremental.

The `fix:` blast-radius script that produced §0 still emits the secondary dir-count; the
primary proxy is read from the edit-episode token ledger. No new heavy tooling.

## 3. First slice — `round_display` (recommended)

Chosen first because §0.1's fix-log analysis puts the **recurring** pain here, not in
ingest. The round-state→projection→render thread is the cluster that gets re-edited
together across display, sweep, optimizer, and round-trace fixes — so it is the highest
real payoff. (Ingest, the earlier candidate, is demoted: its 28-file moment was *one
feature-add*, not a recurring fix pattern, and it is deeply tangled with shared
kernel/domain — a modest win that would not predict the real payoff. It can be a later
slice.)

But the **full** round concept touches 70+ files across every layer — too big and too
risky to move wholesale. So the first slice is deliberately the **narrow recurring-fix
sub-thread only**: how a round becomes a dashboard projection and a rendered view. Prove
the pattern here cheaply, then widen.

### Files the slice owns (the recurring-fix sub-thread, verified per §1 boundary rule)

Backend:
- `infrastructure/projections/live_dashboard/round_summary.py`, `round_buffer.py`,
  `render.py` — the round-specific projection fragments
- `presentation/views/render/sp_diff.py`, `text.py` — the round/searchpoint render
- round-display domain types: `domain/round_diagnostics.py` (clearly round-specific).
  **`domain/projection_envelope.py` and `domain/rendering.py` are candidates only** — the
  `72854e86` consolidation may have made `rendering` general mechanism. Phase B step 1
  applies the §1 ≥2-concept test: if either co-changes with another concept, it stays
  shared residue and the slice imports it instead.

**Stays shared (boundary rule, §1):** the projection kernel
(`infrastructure/projections/base.py`, `live_state.py`, `event_stream/`, and the
`live_dashboard` wiring `factory.py`/`state.py`/`view.py` that serves *all* surfaces),
plus the central state types `opt_search_point` / `search_point`. The slice imports
these; it never inlines them.

### Target shape (illustrative)

```
promptpotter/concepts/round_display/
  __init__.py        # the declared surface (barrel) — consumers import only this
  diagnostics.py     # round_diagnostics (+ projection_envelope/rendering only if they pass the §1 ≥2-concept test)
  project.py         # round_summary + round_buffer + render (the projection fragment)
  render.py          # sp_diff + text (the round/searchpoint render)
  CLAUDE.md          # the slice contract + inner import direction
```

Inner-axis import rule stays enforced: `render.py → project.py → diagnostics.py`, never
upward; and all three import the shared projection kernel, never re-express it.

## 4. Rollout

1. Carve `round_display` to a slice. The `__init__` barrel declares the slice's **internal
   surface**, but the path moves — so importers are **swept once** to the barrel path.
   **No re-export shim at the old paths** (the STOP / no-backward-compat rule forbids it).
   The churn is bounded and one-time (grep the importer set first); zero behavior change;
   full gate green; land as one revertible commit.
2. Re-measure **edit cost vs the control** on the next round-display-touching changes (§2)
   — not bare dir-count.
3. **Collapse confirmed →** widen: carve the rest of the round-state thread (the remaining
   `application/optimization` ↔ `projections` ↔ `views` fragments) — the §0.1 hotspot,
   highest payoff, only once the pattern is proven on the narrow sub-thread.
4. Repeat per concept. Shared horizontal infra (per the §1 boundary rule) is the residue
   that never slices.

## 5. The real cost (paid once)

Not the code move — the **mental-model rewrite**. The flip invalidates:
- the per-layer `promptpotter/*/CLAUDE.md` contracts (they describe the horizontal cut),
- the §0 layer framing in `docs/architecture.md`,
- the CI **layering guard** from `97e1f234` (`application/` must not import
  `presentation/`) — its *intent* survives as the inner-axis direction rule, but it
  re-expresses per-slice, not per-top-level-dir.

These doc rewrites land **with** each slice, not as a big-bang.

## 6. Relationship to the last three commits (the throughput-groundwork arc)

The arc `cd3c5ed7 → 72854e86 → 97e1f234` was groping toward this without the measurement.
Decision: **keep all three, squash to one clean base** — reverting is wasted churn (all
zero-behavior, green), and the flip subsumes the misaligned parts anyway. Per-commit fate:

- `cd3c5ed7` (orientation maps, `concept-map.md`, barrels, anti-rot CI guards, dead-code):
  **fully aligned.** "Concept home" *is* proto-concept-first; `concept-map.md` becomes the
  seed of the slice index.
- `72854e86` (CampaignStore 10→1, writers→`application/output`, render→`domain/rendering`,
  webapp inlining): **mixed.** Webapp inlining = concept colocation, kept. **Flag: do not
  extend the consolidated `CampaignStore`** — it was consolidated on the *layer* axis; the
  flip will redistribute its fragments into the concept slices that own them.
- `97e1f234` (`application/views` home + killed app→presentation inversion): **superseded
  direction.** Views move *into* slices, not a global `application/views` home. The value
  (inversion removed) survives; the location does not.

## 7. Outcome — two falsified probes (2026-06-16)

The plan's anti-hill-climb gate (§2) did its job: it killed the idea cheaply, before any
move. Both candidate slices were boundary-validated by import-tracing alone.

- **Probe 1 — narrow `round_display`** (§3). Failed. `live_dashboard/view.py` (the kernel
  writer that stays shared) *imports* `render` + `round_buffer` + `build_round_summary` —
  they are kernel-internal helpers split out only to thin `view.py`, so moving them into a
  slice inverts the kernel→slice dependency. Separately, `views/render/{sp_diff,text}` share
  **zero** references with the live_dashboard fragments — a distinct terminal-render concern
  the fix log bundled by co-occurrence alone. Exactly the §0.1/§2 trap: a display *change*
  spans read-shape + render, but those are two mechanisms, not one concept.
- **Probe 2 — larger round-state thread.** Failed harder. The core types
  `opt_search_point` / `results` / `run_records` are imported by **66 files across every
  layer** (l1, validators, escalation, dispatch, pobb, scoring, sweep, runner, bootstrap,
  origin, mask, review, jobs, store, ledger, llm, api, cli, views). That is the **system
  spine**, not a carvable concept — and §1's boundary rule already names `opt_search_point`
  as shared-by-definition. The recurring fix signature (`optimization` + `projections` +
  `views` + a `domain/` state type) co-occurs because it is the natural
  read→compute→project→render flow of the central object — a flow that is **correctly
  layered**. Carving it would drag 66 files or invert the dependency.

**Conclusion.** The smear §0 measured is real, but it is not a structural defect to be
sliced away — it is the inherent footprint of changing the system's central state object,
which every layer legitimately touches. The layer cut is the right cut here. Concept-first
colocation has no high-value target in this repo's hot area. Closed; not pursued.

## 8. Parked (later, optional)

**Point PromptPotter at its own GitHub repo** as the eventual measurement engine — turn the
repo into a campaign whose fitness is cost-to-green per edit-episode, and let the loop
*score* structural mutations instead of us intuiting them. This spec's §2 hand-measurement
is the manual stand-in until then. A fun experiment for when there's time; not a blocker.
