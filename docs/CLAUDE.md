# docs/ — documentation tree

This file is the **index for AI/agent readers** over `docs/`. The operator-facing index is [`README.md`](README.md). Load only the subtree you need.

> **Read [`architecture.md`](architecture.md) first.** §0 is the shape on one page; §0.5 is the load-bearing surface that every PR measures against. Everything below is progressive disclosure off §0.

## Top-level

| File | Role |
|---|---|
| [`architecture.md`](architecture.md) | **§0 + §0.5 — the single page every PR measures against.** AI entry point. |
| [`glossary.md`](glossary.md) | Domain vocabulary; one line per term with canonical implementation file. Read before introducing a new word. |
| [`README.md`](README.md) | Operator-facing index (the friendly door) + the short status/roadmap summary; the live forward plan is [`specs/roadmap.md`](specs/roadmap.md). |

## Subtrees

| Folder | When to load | Index |
|---|---|---|
| [`manual/`](manual/) | Operator onboarding: install → first campaign → reading output → troubleshooting → going deeper. Numbered chapters. | [`manual/README.md`](manual/README.md) |
| [`concepts/`](concepts/) | How the loop works conceptually — the three-layer loop, scoring + memory, candidate-elimination, **optimizer-of-the-optimizer (L4 recursion)**, campaign tree, paired-sample PoBB. Read before the developer docs. | [`concepts/README.md`](concepts/README.md) |
| [`developer/`](developer/) | Implementation specs — Python names, data contracts, node wiring, `pipeline.yaml` contract, dispatch hub + L1 layout, L1-candidate-analysis checklist (incl. the self-optimizing campaign lookup), self-healing internals, **`conventions.md` (full style + code-shape rules)**, `stable-api.md` (v1 fork-readiness surface). | [`developer/README.md`](developer/README.md) |
| [`operations/`](operations/) | Running it — CLI reference, env, persistence + recovery, observability, backend integration, **`access-model.md`** (the security map an audit opens — tiers, boundaries, enforcement, deploy checklist), **`secure-hosting.md`** (allowlist admin via the on-box bot), **`adding-a-dataset.md`**, **`dataset-selection-rationale.md`**, **`dataset-reasoning-matrix.md`** (per-dataset model + `reasoning_effort` + `max_tokens` defaults). | [`operations/README.md`](operations/README.md) |
| [`methods/`](methods/) | The two spend-control procedures: PoBB elimination + hard-sample leaderboard. | [`methods/README.md`](methods/README.md) |
| [`research/`](research/) | Benchmarks (BBEH comparison + the PEvol-Bench definition), metrics, related-work table (incl. MCTS comparison). | [`research/README.md`](research/README.md) |
| [`specs/`](specs/) | Forward direction in one [`roadmap.md`](specs/roadmap.md) + living contracts (verdict-resolution, frontend-surface-contract, chat-foundation, the two control-plane YAMLs) + the debt backlog. **Specs index has its own CLAUDE.md.** | [`specs/CLAUDE.md`](specs/CLAUDE.md) |
| `assets/` | Images and diagrams; no contract. | n/a |

## Anchor docs for hot questions

| Question | Read first |
|---|---|
| Where does concept X live? | [`developer/concept-map.md`](developer/concept-map.md) |
| What is the origin? When is it a *parent* instead? | [`architecture.md`](architecture.md) §0.5 (the start-definitions bullet) |
| Under which fitness formula? active / what-if / lens / replay, `composite_fitness` vs `accuracy` | [`architecture.md`](architecture.md) §0.5 (Composite-fitness resolution chain) + [`concepts/scoring-and-memory.md`](concepts/scoring-and-memory.md) |
| The situational reasoning doctrines (simplify-the-problem / surface-ledger / reach-the-operator)? | [`developer/conventions.md`](developer/conventions.md) § Reasoning doctrine (the two universal gates stay in root [`CLAUDE.md`](../CLAUDE.md)) |
| Debugging the PP↔TermNorm highway (async hygiene, `--reload` session wipe, latency)? | [`operations/backend-integration.md`](operations/backend-integration.md) § Debugging the highway |
| Why does a schema's field order / `description=` change what the model says? | [`concepts/structured-output.md`](concepts/structured-output.md) (the schema is a second prompt) |
| How does information flow through L1 / L2 / L3? How is L1's evidence surface built? | [`developer/dispatch-hub.md`](developer/dispatch-hub.md) (§ L1 layout for the latter) |
| How do I add a record / injection / view-field / connector without half-wiring it? | [`developer/adding-a-surface.md`](developer/adding-a-surface.md) (recipe + the CI guard per surface) |
| How does a layer heal a failure? | [`developer/self-healing-internals.md`](developer/self-healing-internals.md) |
| What datasets do we use? Why didn't we use Y? | [`operations/dataset-selection-rationale.md`](operations/dataset-selection-rationale.md) |
| What model + `reasoning_effort` for this dataset? | [`operations/dataset-reasoning-matrix.md`](operations/dataset-reasoning-matrix.md) (canonical — NOT self-optimizing campaign NOTES.md) |
| What's the canonical split for this benchmark? | [`operations/adding-a-dataset.md`](operations/adding-a-dataset.md) |
| How do I drive the authed/live UI locally (no OIDC, no spend)? | Relaunch with `PROMPTPOTTER_AUTH=off` → reads your real on-disk campaigns. Recipe: [`../webapp/CLAUDE.md`](../webapp/CLAUDE.md) § Testing posture. Only the real OIDC round-trip needs the Dex harness, [`developer/local-oidc.md`](developer/local-oidc.md) |
| What's the full access/security model — tiers, boundaries, enforcement, deploy checklist? | [`operations/access-model.md`](operations/access-model.md) |
| How do I manage the sign-in allowlist / host securely? | [`operations/secure-hosting.md`](operations/secure-hosting.md) + [`adr/0004-operator-admin-channels.md`](adr/0004-operator-admin-channels.md) |
| How do I freeze a buggy cycle as a test fixture? | [`developer/cycle-fixtures.md`](developer/cycle-fixtures.md) (`tests/fixtures/cycles/`) |

## L4 — the recursion case (project's closing focus)

L4 (PromptPotter optimizing its own optimizer prompts) **recursion is SHIPPED & live-validated**; the project is now finishing it into a **distributable `promptpotter-self`**. An AI agent driving L4 reads **(2) first** — it is the living finish-line plan + the SoT.

1. [`concepts/optimizer-of-the-optimizer.md`](concepts/optimizer-of-the-optimizer.md) — why, the composed outer fitness (lift × quality × efficiency + the candidate-gradient law), cost realism (status note points back to the plan).
2. **[`specs/l4-outer-loop.md`](specs/l4-outer-loop.md) — the living finish-line plan: § Finish line (distributable goal), § Live-run learnings (MAX_PATH flat `.inner/` registry, gsm8k→`justlogic-d234` headroom), and the slice order.** Read first — and read item 7 before trusting any outer number: the panel's resolving power is served (`rank-optimizer-prompts`) and currently reads `UNKNOWN`.
3. [`../promptpotter/connectors/CLAUDE.md`](../promptpotter/connectors/CLAUDE.md) — the connector boundary + the shipped `in_process` seam + the flat sandbox registry.

The dataset side: [`../datasets/CLAUDE.md`](../datasets/CLAUDE.md) § L4 — `promptpotter-self`.

## Editing a doc

**One fact, one owner. If two files both claim it, that is two facts — split it before you pick.** Name the artifact the rule constrains; the doc governing that artifact owns it. Where it genuinely will not split, the deepest directory wins and everyone else writes an **obligation line**: the rule's name in bold, an em dash, "owned by" plus a link to the owning file, then one clause naming only what THIS layer must do. No rationale, no second filename, no number. The deletion test decides whether you wrote one: remove the link, and the line must read as *broken*, not merely unsourced. If it still teaches the rule, it is a copy, and copies are what drift.

**The Recompute Test — docs hold rules, not state.** Could a reader six months from now recompute this line from the repo and get a *different* answer? Then it is state: it will go wrong and nothing will say so. Same answer? The doc is quoting code — name the symbol instead. Not recomputable at all? It is a decision or a war story, and it belongs.

Write instead of — line numbers → `file.py::symbol` · a count of a code-owned set → the enumerator's name · a value that lives in config → the knob's name · a status word (`SHIPPED`, `gating`, `in flight`, `slice N`) → one link to the single status owner · a count of on-disk data → the command that counts it · an un-anchored `§ Name` → a heading that exists verbatim in the file just linked · a post-mortem of closed work → its standing rule, leaving the narrative in `git log`. A commit SHA or an event date may stay when all three hold: it is *provenance* (delete it and the rule still reads), *immutable*, and *subordinate* to the rule's own sentence.

**The shape a `CLAUDE.md` takes.** A title naming what the file owns, orientation carrying
links but no rules, then one section per owned concept — each **leading with its rule as the
first clause**, with the war story subordinate to that sentence rather than its own paragraph.
`## Conventions` holds house-style leftovers only: anything with a *why* is a concept and
earns a heading, because **the headings are the index**. Rules another file owns go last under
`## Owned elsewhere`, as obligation lines. A file whose heading list has stopped being
skimmable adds a `## Load-bearing` card of rule NAMES, each pointing at a verbatim heading in
that same file and holding nothing else — which is what stops the card becoming a second owner.

**Documentation does not define behavior — code does.** Where a doc and the code disagree, the code wins and the doc gets updated. The one exception is [`architecture.md`](architecture.md) §0 / §0.5, which IS a contract: code disagreeing with it gets fixed instead. And the per-layer `CLAUDE.md` files under `promptpotter/*/` are per-package *contracts*, not docs — this index says which one holds which fact and never restates the fact itself.
