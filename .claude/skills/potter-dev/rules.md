# potter-dev — learned rules ledger

The corrections the operator has already paid for. APPLY reads this first; LEARN appends here.
One rule per block. Update-don't-duplicate. Delete a rule the operator later contradicts.

## Index

**Conventions**
- [R-01](#r-01) — PEP 604 type hints only
- [R-02](#r-02) — `logging`, never `print`
- [R-03](#r-03) — no fallbacks in service code
- [R-04](#r-04) — direct field access, not `.get(k, default)`
- [R-05](#r-05) — pipeline components are "nodes"
- [R-06](#r-06) — don't trim load-bearing copy (docstrings, `Field(description=)`, registry `description=`)
- [R-07](#r-07) — delete on sight: shims, fallback chains, breadcrumb comments
- [R-08](#r-08) — root-fix, not symptom-patch
- [R-49](#r-49) — a divergence between twin code paths → unify into one helper, never copy the missing piece into the second
- [R-44](#r-44) — fail-loud is for contracts WE own; defensiveness at trust boundaries is correct
- [R-45](#r-45) — a "consolidation" must lower concept count; write the irreducible core first, delete toward it
- [R-46](#r-46) — tests earn their keep ONLY if they catch SILENT harm; downtime is cheap here
- [R-47](#r-47) — read surfaces are two clusters by cadence (live `dashboard.json` vs settled folder) over internal `.runtime/`; shared data isn't redundancy

**Architecture / seams**
- [R-09](#r-09) — reuse before adding; no sidecar
- [R-10](#r-10) — optimizer LLM calls go through `llm_call()`, never `chat()`
- [R-11](#r-11) — wrap LLM calls / backend matches with `observed_node()`
- [R-12](#r-12) — `score_search_point()` is the single scoring gateway
- [R-13](#r-13) — per-dataset tunable → overlay; TermNorm structural root-cause → fix in TermNorm
- [R-14](#r-14) — respect the hexagonal layer-import rules
- [R-15](#r-15) — a new seam/invariant is a `tests/test_structure.py` row
- [R-41](#r-41) — "will changing a connector tunable re-score?" — `node_configs` is the key; campaign config is frozen; cycle id is config-blind
- [R-48](#r-48) — termination authority → most-general reader; a backend-coupled deterministic check only WARNS, never stops

**Workflow / git**
- [R-16](#r-16) — a few coherent commits per arc (logical units, not one blob, not one-per-change)
- [R-17](#r-17) — conventional commits, ≤800 chars, title <70
- [R-18](#r-18) — ruff format + check before commit
- [R-19](#r-19) — never commit or push unless told
- [R-20](#r-20) — solo dev: commit to `main`, no PR ceremony unless asked
- [R-21](#r-21) — CLI timeouts ≤30s; never background the runner
- [R-22](#r-22) — `new` mints fresh; `resume` continues, not "use this config"
- [R-37](#r-37) — scope `git add` to changed files; never commit a sibling repo's WIP
- [R-39](#r-39) — end a substantial task with a compact recommendation

**Investigation / interaction**
- [R-23](#r-23) — say "origin", never "baseline"
- [R-24](#r-24) — no hidden defaults
- [R-25](#r-25) — no cost / round predictions before a run
- [R-26](#r-26) — concise + declarative; replies under ~800 chars
- [R-27](#r-27) — don't trim or restructure reference docs unprompted
- [R-28](#r-28) — AskUserQuestion options vary on one axis only
- [R-29](#r-29) — no data deletion
- [R-30](#r-30) — CWD errors → tell the operator to `cd`, don't paper over
- [R-40](#r-40) — large-scope dataset assembly: audit silent-drop hazards before proposing execution
- [R-43](#r-43) — operator in debug-mode → halt at round-1 + test-and-fix loop, don't run the full loop past known-broken rounds
- [R-31](#r-31) — root `CLAUDE.md` is a thin entry point
- [R-32](#r-32) — canonical test set first
- [R-33](#r-33) — `dashboard.json` and on-disk surfaces stay live-written
- [R-34](#r-34) — drain the debt backlog before feature work
- [R-35](#r-35) — reuse the session-chosen asset; don't hardlock pre-launch brand assets
- [R-36](#r-36) — scoring/projection is backend; the webapp renders served scores, never recomputes them
- [R-38](#r-38) — overlay markers: one calm indicator where the operator points; line/color over icon/ring/flash
- [R-42](#r-42) — TermNorm pipeline wrong/empty output: trace the contract seam, not the model
- [R-50](#r-50) — "tidy this <block>" scopes the edit to that block only; don't touch unrelated lines

---

## Conventions

### R-01 — PEP 604 type hints only
- **Trigger:** writing/annotating any Python.
- **Rule:** `X | None`, `list[str]`. Never `Optional[X]` / `List[str]`.
- **Why:** project default; mypy strict everywhere.
- **Origin:** 2026-06-07 — seeded from `docs/developer/conventions.md`.

### R-02 — `logging`, never `print`
- **Trigger:** adding output in `promptpotter/`.
- **Rule:** use the `logging` module (setup in `promptpotter/config/logging.py`); no `print()`.
- **Why:** material facts land on disk/log, not stdout.
- **Origin:** 2026-06-07 — seeded from conventions.

### R-03 — no fallbacks in service code
- **Trigger:** tempted to add `try/except` defaulting, `or <default>`, "if missing, use…".
- **Rule:** no fallbacks. Two sanctioned exceptions only (`score_population()` synthetic-0; load-boundary deprecated-sample gate); any new one must be documented alongside them.
- **Why:** fallbacks announce uncertainty; with a contract, lean on it. [[R-04]]
- **Origin:** 2026-06-07 — seeded from conventions / root CLAUDE.md.

### R-04 — direct field access, not `.get(k, default)`
- **Trigger:** reading a dict/config field that the contract guarantees.
- **Rule:** `d[key]`. Reserve `.get` for genuinely optional keys.
- **Why:** a default silently hides a broken contract. [[R-03]] [[R-24]]
- **Origin:** 2026-06-07 — seeded from conventions.

### R-05 — pipeline components are "nodes"
- **Trigger:** naming/describing a pipeline step.
- **Rule:** "node". Never "building block", never "service".
- **Why:** domain vocabulary is fixed; regressing it breaks shared language.
- **Origin:** 2026-06-07 — seeded from `feedback_node_terminology`.

### R-06 — don't trim load-bearing copy
- **Trigger:** comment/LOC-compression passes.
- **Rule:** leave alone: module/class/function docstrings (they explain *why* — invariants, contracts); `Field(description=...)` on any model in an LLM response schema or API response; registry `description=` (Evaluator, etc.) that has a JSON projection (`evaluators_meta`, `model_json_schema`). When unsure if a model crosses an API/LLM boundary, grep `model_json_schema`/`evaluators_meta` — when in doubt, keep. Internal-only docstrings/comments are still fair game.
- **Why:** these strings are operator-facing or LLM-facing product copy, not narration. LOC wins come from dead-code/inlining/god-object fixes, not shrinking explainers.
- **Origin:** 2026-06-07 — seeded from `feedback_field_description_load_bearing` + conventions.

### R-07 — delete on sight
- **Trigger:** you spot (or are about to write) shim code, a fallback chain, or a breadcrumb comment ("remove later", "temp until…", "we'll delete this").
- **Rule:** delete it now — don't ask, don't TODO, don't "remove later". Zero backward compatibility, ever (no released versions, no stale on-disk data).
- **Why:** the rule most often ignored; the repo stays clean only if it's enforced every time.
- **Origin:** 2026-06-07 — seeded from root CLAUDE.md "STOP — no backward compatibility".

### R-08 — root-fix, not symptom-patch
- **Trigger:** a fix would compensate for something an upstream layer should already have made true.
- **Rule:** name the structural cause and propose the upstream fix *before* touching the visible surface. Default to root. The operator may still pick the patch — but knowingly.
- **Why:** symptom patches accrete into the shim/fallback debt R-07 forbids.
- **Origin:** 2026-06-07 — seeded from root CLAUDE.md `<root-fix>`.

### R-49 — a divergence between twin code paths → unify into one mechanism, never patch the second
- **Trigger:** you're fixing a bug/divergence between two peer functions or code paths that do the same *kind* of work (match-by-id, validate, parse, project, guard) and you catch yourself ADDING a line/branch/guard that already exists verbatim in the sibling. The tell: your diff inserts logic that is a copy of code living elsewhere. Fires on any "make function B also do what A already does" fix, and on a "trim/tidy" pass that would leave the two copies in place.
- **Rule:** do NOT copy the missing piece into the second path — that's a patch-beside that leaves two divergent copies which will drift again (the drift is usually what caused the bug). Factor the shared logic into ONE helper both call, parameterized by what genuinely differs (which list, which return type). The guard/predicate/projection then lives exactly once. This is the **lateral** counterpart of [[R-08]]: R-08 is upstream/downstream (fix the cause, not the symptom site); R-49 is side-by-side (fold the twin into the canonical mechanism, never add beside it — root CLAUDE.md "Redundant mechanisms… fold it into the canonical mechanism, never add beside it"). Genuine consolidations that collapse N copies → 1 (a shared derivation two surfaces ride) are the *right* shape and pass; adding an (N+1)th copy is the violation.
- **Why:** `liveCandidate` and `liveInputCandidate` were twin id-matchers; only the output one carried the `Number.isFinite(idx)` guard. I "fixed" the input one by pasting the guard in — now two copies, the exact drift that bred the bug. The root fix was one generic `matchLiveCandidate<T>` both delegate to; the guard now lives once and the two functions are one-liners. Operator: "ugly patch, we cannot patch, why we always do that." [[R-08]] [[R-09]] [[R-45]] [[R-07]]
- **Origin:** 2026-06-18 — operator flagged the duplicated idx-guard fix in `poll.tsx` as a patch; root fix unified the two live-candidate lookups into one matcher.

### R-44 — fail-loud is for contracts WE own; defensiveness at a trust boundary is correct
- **Trigger:** a "strip the over-careful fallback" pass (operator feels the codebase is "too careful"), OR you're about to convert a `.get() or {}` / isinstance-guard / `or default` to direct access citing R-03/R-04/R-07.
- **Rule:** before stripping, classify the data source. **Strip** (fail-loud wins) only when the key/attr is guaranteed by an **internal contract we own** — a frozen domain model, our own `index.json::rounds` shape, a required config field — where a default silently hides our broken contract. **KEEP** the defensiveness when the value crosses a **trust boundary**: (a) **backend wire** responses (TermNorm `/matches`, per-sample diagnostics that may be omitted, cross-version per-node-vs-top-level reads); (b) **optional on-disk files** (a dataset's optional `pipeline.json`, `spend_cap.json`, partial overlays); (c) **foreign sibling-cycle round files** (wounds/runtime_failures legitimately absent = empty); (d) **absent-means-empty** view/event payloads; (e) **required-field boundary defaults** (`max_tokens or 8192` — provider mandates it); (f) **deliberate security posture** (last-mile log redaction, defensive regex). The litmus: *does removing it fail loud on a genuine bug in code WE control, or does it just crash on a shape an external producer is allowed to vary?* If the latter, it's not baggage — it's the boundary doing its job. **But default toward firm-then-delete for a boundary WE own.** Most "boundaries" here are self-owned (TermNorm co-owned, our own on-disk cycle/dataset files, our own dashboard/view payloads emitted by our Python). For those the clean move is root-fix ([[R-08]]): pin the source to emit a firm shape (a typed model / `extra="forbid"` / a generated TS type the consumer references), then the downstream guard is dead and deletes. Don't pre-excuse a self-owned guard as "correct defensiveness" and move on — surface it and firm the source. KEEP-as-is is only for a genuinely *foreign* producer you can't pin. AND verify the producer is single: a reader that serves TWO writer shapes (e.g. `round_summary` over both the scored payload and the sparse `generation_only` sweep dict) can't be firmed to one type — read directly only the keys BOTH writers guarantee, and delete keys NEITHER provides as dead output. Also: an intentional pluggability seam (ABC, Protocol over `.append`, connector dispatch, Langfuse→manifest CMS) is architecture, not a shim — `_DecisionSink` covering both `list` and `CycleEventLog` is structural typing, not dead generality.
- **Why:** R-03/R-04/R-07 are real but get over-applied. A whole "strip baggage" arc (~40 candidates) hand-verified down to **3** truly-dead sites (a double-default `dict(x or {}, {})`, a lambda-currying helper replaceable by `functools.partial`, one banned-phrase comment) — the other ~37 were correctly guarding genuinely-variable external inputs. The operator's "too careful" intuition mostly does NOT survive contact with the code, because most of the defensiveness sits at boundaries where it belongs. mypy is the cheap proof: turning `.get() or {}` into direct access surfaces any real optionality as a type error — a clean strict run means the guard was dead. [[R-03]] [[R-04]] [[R-07]] [[R-08]] [[R-13]] [[R-24]]
- **Origin:** 2026-06-15 — operator: "strip unnecessary careful code… I was too careful as the owner… heavy baggage." The honest finding was that the premise mostly didn't hold; the lesson is the internal-contract-vs-trust-boundary discernment, so future strip passes don't gut correct boundary defensiveness.

### R-45 — a "consolidation" must lower concept count; write the irreducible core first, delete toward it
- **Trigger:** the operator asks to consolidate / simplify / unify / "fewer moving parts" / "clarity gain" / "remove indirectness", OR you're proposing or *finishing* a refactor framed as cleanup, OR the operator asks **"is it really better than before?"** about a refactor, OR asks to **"refine <a refactor commit>"** / "disaggregate" / "completely disaggregated". Fires hardest right before you declare a refactor done. **The tell you've mis-fired: your plan to "refine"/"finish" the refactor ADDS concepts (a new feature, surface, operator-terminal, mermaid, docs section) instead of deleting toward the core — "refine a re-skin" means finish the dissolution (concepts DOWN), never bolt more on top.**
- **Rule:** measure the change by **concept-count delta**, not behavior added. A genuine unknot makes concepts go DOWN (fewer classes / fields / storage streams / renderers / enums / members). If the diff *adds* concepts (a new enum, map, fn, speculative members) and deletes ~nothing structural, it is a **RE-SKIN** — a clean layer bolted on top of the organic growth, not a dissolution. Process: (1) write the **irreducible-core paragraph FIRST** — the 2–3 axes that genuinely vary — then DELETE toward it; do NOT start from the existing taxonomy and tidy it (that path only ever re-skins). (2) The unifying axis you introduce (e.g. `corrective_surface`) must be *used to collapse* the duplication (N storage fields / N renderers → 1 partitioned), not bolted *alongside* it. (3) **Grep every consumer before claiming a field/path is dead / unread / unified** — "X is vestigial" recurs as the false step (`score` was display-read; `passed` was vacuously read); mypy is the cheap proof a removed field was truly unread. State the residual explicitly — a *silent* boundary reads as an oversight and draws the next correction. (4) An **escape-hatch clause** in a taxonomy ("tier 2 / beyond the four wounds") is the tell it's describing *accumulated differences*, not prescribing structure — that taxonomy IS the knot. (5) **Lock after the collapse, never before** — a `test_structure` ban on the organic shape (or speculative enum) cements the knot permanently. (6) But don't *over*-collapse either: merging genuinely-distinct lifecycles/payloads into one dict-typed blob is a *new* knot (filter-by-kind everywhere) — keep what an axis honestly encodes. [[R-08]] [[R-07]] [[R-44]] [[R-16]]
- **Why:** I "reframed" the four-wound self-healing taxonomy by adding `CorrectiveSurface` + `NurseOwner` + `route()` + 5 speculative members, deleted only the vestigial `nurse_target`, and called it a consolidation. Net concepts went UP. Operator: "A real unknot makes concepts go down… we re-skinned, we didn't dissolve it." The axis was correct; bolting it alongside the four storage fields / four renderers instead of collapsing them was the miss. Then a *sharper* follow-up plan still tripped the same wire — asserting `score` "never read" (it was) and missing its dead twin `passed` — because completeness was claimed un-grepped.
- **Origin:** 2026-06-15 — operator dissected the corrective-surface refactor: "improved by adding a cleaner layer on top of the organic growth, not by collapsing it. The tell is simple: nothing got deleted except one vestigial field, while the concept count went up… A real unknot makes concepts go down."

### R-46 — tests earn their keep ONLY if they catch SILENT harm; downtime is cheap here
- **Trigger:** writing/keeping/deleting a test, deciding whether something "needs coverage", OR catching yourself defending a test as "the last guard of contract X" / hand-wringing over dropping a guard. Fires hardest when the operator calls tests "strangulating" / "effectless" / "stupid" / "stop dragging" them.
- **Rule:** the bar is not "does this guard a contract" (almost everything does) — it is **"if this broke in production, would I SEE it?"** This repo can afford downtime (weeks). It cannot afford maintenance tax on refactors. So: **breaks loud → NO test** (wrong API envelope, layer-import drift, model-shape change, a 500, a stale dashboard, a failed mint — you notice in use/logs/the file tree and fix it then; a test for it is pure drag that breaks on every rename and catches nothing). **Breaks silent → test it** (wrong score, a leak, lost/corrupted measurement data — no error, no symptom, *cannot* be rediscovered in use). The whole suite is the three silent-harm classes: `test_numerics` (wrong score), `test_security` (leak/injection/path-escape), `test_resume` (silent data-loss/wrong-inheritance). Structural wiring invariants worth enforcing become **import-time asserts in the owning module** (loud at import, zero maintenance), never a `test_structure` scan. Do NOT invoke R-44/R-45 "never drop the last guard" to keep a loud-breakage test — that caution is about not gutting boundary *defensiveness in production code*, not about retaining effectless test scaffolding. When in doubt on a specific test, ask "silent or loud?" not "is it covered elsewhere?".
- **Why:** I twice resisted aggressive test deletion (R-44/R-45 "last guard" framing, detangle-don't-delete) when the operator's actual cost model is the opposite: the guards themselves are the liability. The suite went 263→97 (6 files→3) keeping only silent-harm; everything else was loud-breakage drag. The earlier charter's "delete don't update / no display tests / no stub forests" was the right instinct under-applied — this rule is the sharpened bar. [[R-44]] [[R-45]] [[R-07]] [[R-15]]
- **Origin:** 2026-06-15 — operator: "we can afford down time, weeks if it would occur, no problem. what is a problem is dragging stupid effectless tests along us… most of that is TOO strangulating."

### R-47 — read surfaces are two cadence-clusters over internal `.runtime/`; shared data isn't redundancy
- **Trigger:** about to flag a second on-disk surface as "redundant" / "vestigial" debt because its DATA overlaps another (two files both carrying round summaries, scores, status). Fires hardest when you're writing a code-debt entry to "retire" / "delete the writer" of one of `dashboard.json` / `index.json` / `rounds/round_NNNN.json` / `log.md`.
- **Rule:** **the project file tree IS the dashboard** (root CLAUDE.md) — a feature the operator built early and values. The read-out surfaces form **exactly two clusters split by CADENCE, not by reader** (canonical statement: `architecture.md` §0, "Read surfaces form exactly two clusters"): (1) **Live** = `dashboard.json` (one churning now-state file); (2) **Settled** = the rest of the cycle-dir top level (`index.json`, `rounds/round_NNNN.json`, `log.md`/`review.md`/`hard_samples.json`), written at boundaries. Everything under **`.runtime/`** (the `events.jsonl` ledger SoT, caches, PoBB streams, control flags) is the third, **internal** cluster — machinery, not a read-out. **The divide is cadence, NOT audience** — both the webapp *and* a human read across both clusters (the webapp polls `dashboard.json` live yet opens `index.json`/round files on drill-in; a human can tail `dashboard.json`). So data shared between the two read clusters (e.g. `index.json::rounds` settled vs `dashboard.json::rounds` live) is the multi-projection read model ([[R-09]]: one ingress → many projections), the *opposite* of a redundant mechanism. The fix when a CONSUMER reads the wrong cadence (a live view reading the settled file) is to **repoint the consumer** — never delete the other surface. A writer is debt only when **no** reader, the human file-tree included, consults it; "the webapp doesn't need it" is not "no one needs it." [[R-45]] [[R-33]] [[R-29]]
- **Why:** after repointing the lineage tree to read live round state from `dashboard.json` (correct — a live view was reading the completion-gated `index.json`), I logged a code-debt item to *delete the `index.json::rounds` writer* as "redundant." But `index.json::rounds` is the lean settled round digest a human reads by opening the cycle folder. Operator: "I really want to keep the folder UI system… I can use the full project without the web app." Then sharpened it to the model itself: "there should be exactly two: the dashboard.json and that folder, and otherwise the rest internal. two clusters!" — true, and physically encoded (top level = read-out, `.runtime/` = internal); I only had to fix the axis from reader→cadence so "webapp doesn't read it" can't be misread as "delete-safe."
- **Origin:** 2026-06-17 — operator defended file-tree-as-dashboard, then named the two-cluster model; both landed in `architecture.md` §0.

## Architecture / seams

### R-09 — reuse before adding; no sidecar
- **Trigger:** about to add a class/field/dict/file/injection/prompt.
- **Rule:** search first. Default to "an existing channel already does this": ride the ledger / `INJECTIONS` / `OptSearchPoint` / dispatch hub / `Stores`. Optimizer state flows only through `OptSearchPoint` — never a parallel sidecar field.
- **Why:** pre-flight gate Q1; the wrong shape should be hard to express, not policed later.
- **Origin:** 2026-06-07 — seeded from root CLAUDE.md pre-flight gate.

### R-10 — `llm_call()`, never `chat()`
- **Trigger:** making an optimizer LLM call (L1/L2/L3/critique).
- **Rule:** route through `llm_call()` (`application/optimization/dispatch/llm_call/call.py`). Direct `.chat()` outside that file is locked out by `tests/test_structure.py`.
- **Why:** one funnel for retry/deadline/telemetry. [[R-11]]
- **Origin:** 2026-06-07 — seeded from conventions / pre-flight gate Q8.

### R-11 — wrap with `observed_node()`
- **Trigger:** adding a new LLM call or backend match.
- **Rule:** wrap it with `observed_node()`. An unwrapped call is an automatic block.
- **Why:** every piece of state is traced at both layers; unobserved spend is invisible.
- **Origin:** 2026-06-07 — seeded from pre-flight gate.

### R-12 — `score_search_point()` is the single scoring gateway
- **Trigger:** scoring a candidate/searchpoint.
- **Rule:** go through `score_search_point()`; pass `on_sample_scored=` explicitly (a callback, or `None` for intentional silence). Locked by `tests/test_structure.py`.
- **Why:** one scoring path keeps measurement + visibility consistent.
- **Origin:** 2026-06-07 — seeded from architecture §0.5.

### R-13 — per-dataset tunable → overlay; TermNorm root-cause → fix in TermNorm
- **Trigger:** changing what a backend runs — a model/provider/param switch, OR a structural backend behaviour/bug.
- **Rule:** split on *which kind*. (a) A **per-dataset tunable switch** (this dataset should run model X / temp Y) → edit `datasets/{name}/pipeline.json::nodes.{name}.config` (the dataset OWNS its config), NEVER a backend repo. (b) A **genuine structural root cause that lives in TermNorm's code** → fix it IN TermNorm (`TermNorm-excel/backend-api`), coordinate explicitly, keep both sides simple — TermNorm is co-owned/same-project, NOT a read-only third party. Do NOT patch PromptPotter to paper over a TermNorm-root bug (that's the R-08 anti-pattern). The `llm_defaults` block is a non-authoritative display snapshot — never read for resolution, never a control. The optimizer's own meta-prompt LLM is separate + install-global (`datasets/_optimizer/pipeline.json`). The model is dataset-owned and a missing one is a loud error (see config.py), not a silent backend-default fall-through.
- **Why:** pipeline-agnostic is a §0 commitment for *config*, but root-fix (R-08) wins for *code* — and TermNorm is in-house, so its root is reachable. The earlier "never `cd` into a backend repo" framing over-applied the read-only rule to the one backend that isn't third-party.
- **Origin:** 2026-06-07 — seeded from `feedback_no_backend_edits`; sharpened 2026-06-07 after the operator corrected the absolute "never edit even co-owned TermNorm" framing during the model-knot gut.

### R-14 — hexagonal layer-import rules
- **Trigger:** adding an import across `promptpotter/` packages.
- **Rule:** forbidden runtime edges (locked by `tests/test_structure.py`): domain→anything, intelligence→optimization, infrastructure→application/intelligence/optimization.
- **Why:** the layering is the architecture; the test makes the wrong import fail loudly.
- **Origin:** 2026-06-07 — seeded from `tests/test_structure.py`.

### R-15 — a new seam/invariant is a `structure.py` row
- **Trigger:** you just introduced a seam ("X must only happen in file Y") or want to lock a pattern.
- **Rule:** add a `RegexBan`/`CallBan` row to `tests/test_structure.py` — never hand-roll an `rglob`/`ast.walk` lint. The engine is `tests/_scan.py`.
- **Why:** one scan engine, declarative bans; adding a lock = adding a row.
- **Origin:** 2026-06-07 — seeded from `tests/test_structure.py` design.

### R-41 — "will changing a connector tunable re-score?" — the identity/caching seam
- **Trigger:** the operator edits a connector tunable (model/temperature/a node param) and asks whether the next run re-measures, or you need to reason about cycle/campaign/measurement identity.
- **Rule:** answer from three facts, not eight files. (1) The measurement key is `node_configs` (the effective per-node config, **model included**) over the overlay-merged `session.pipeline_params` (`config.py` merge → `search_point.py::content_hash` → `measurement_archive.py::load_reusable_results`): a change at node N **re-measures** every sample whose pipeline ran past N; only upstream short-circuits (cache/fuzzy, `terminated_at` in the trusted prefix) replay. (2) A running/resumed campaign uses its **frozen `CampaignConfig`** snapshot — editing `datasets/{name}/{campaign,pipeline}.json` only applies on a fresh `new` (random `campaign_id`); that is how you mint a new origin on a changed model. (3) The **cycle id is config-aware** — `build_origin_cycle_id` hashes the SAME overlay-merged params as the measurement key, so `cycle_id`/`root_content_hash` agree with which config was measured: a connector-config edit yields a DISTINCT origin. (Resuming a campaign minted before this landed sees a hash mismatch with identical config — `DiffScope.NONE` — which the drift check treats as benign + re-stamps.) Full writeup: `docs/operations/persistence-and-state.md` § "Will a config change re-score?".
- **Why:** this took ~8 files / 3 hash schemes to derive once. The cycle-id↔measurement-key asymmetry that made it confusing is now dissolved (config-aware identity); the remaining follow-up is the pure dataset→effective-params resolver (`code-debt-cleanup.md`). [[R-12]] [[R-22]] [[R-29]]
- **Origin:** 2026-06-11 — operator: the re-score question "took so long… intrinsically messed up… not really a way to get the insight much more direct," asked to capture it in docs + skill + debt.

### R-48 — termination authority → most-general reader; a backend-coupled deterministic check only WARNS
- **Trigger:** designing a multi-tier detect→respond cascade for a fault (node-failure, evidence-starvation, any "the loop is producing noise" condition), OR deciding which tier gets to STOP the loop, OR you catch yourself proposing a deterministic tripwire (à la `backend_unreachable_tripped`) as the thing that halts. Fires hardest when you flip-flop between "hard halt" and "ladder."
- **Rule:** **termination authority belongs to the MOST GENERAL / most intelligent reader, not the narrowest deterministic check.** Order the cascade by generality, intelligent-first, and let those tiers terminate: `l1_critique` surfaces the signal → **L2 may terminate** → **L3 may terminate** → **the live-feed-reviewing skill** (Claude reading the CLI feed / `.goldmine/latest.log` via potter-run) terminates by halting + advising the operator. The **deterministic detector is the WEAKEST tier and must NOT stop the loop** — it only raises a loud, always-visible warning (red error-band on every live surface: `dashboard.json` + the drained log). Why it's weakest: a deterministic signal computed from one backend's diagnostics (TermNorm `step_statuses`/`warnings`) only fires when *that* backend is connected and emitting them — it's useless for any other backend, so it's too narrow/brittle to be trusted with the irreversible stop decision. Generality earns the stop; backend-coupling earns only a warning. Note this is NOT the `backend_unreachable` class (a transport-dead hard halt is correct *because* it's backend-agnostic — the socket is down for everyone); a backend-*specific* content signal is the opposite.
- **Why:** I inverted this twice in one session on the evidence-starvation design — first proposing a deterministic `StopReason.EVIDENCE_STARVED` tripwire (too eager to halt), then over-correcting to "nobody terminates, deterministic just warns." The operator's resolution: the intelligent/general layers terminate (they can reason about whether the fault is recoverable and generalize across backends), the narrow backend-coupled deterministic check is demoted to visibility-only precisely *because* it's backend-specific. Don't let the most-coupled, least-portable signal hold the most-consequential authority. [[R-44]] [[R-08]] [[R-09]] [[R-13]] [[R-36]]
- **Origin:** 2026-06-18 — operator on evidence-starvation detection: "the L2 and the L3 and… the live feed of the CLI through your special skill, you all terminate, but the deterministic doesn't — the one that is too specific… only helps if our TermNorm backend is connected, hence less useful."

## Workflow / git

### R-16 — a few coherent commits per arc (not one blob, not one-per-change)
- **Trigger:** committing finished work — a feature, refactor, fix, or its docs.
- **Rule:** an arc = **a handful of commits** (~2–4), one per **coherent logical phase**, each compiling + green on its own with a body that explains the *why*. Calibration (operator, 2026-06-10): **bundle, lean coarse.** A multi-phase feature is ~1–2 commits (foundation+serve+overlay together is fine); a cleanup pass is ~1–2 (backend vs frontend split only when typecheck forces it — a renamed API param lands with the backend, the caller next). Do **NOT** split per-`W`/per-file/per-step — that's over-atomizing (8 was too many; ~4 was right for the mask arc: 2 feat + 2 refactor). Do **NOT** squash an entire arc into one blob either — that loses resolution. Fold only WIP/"checkpoint"/"fix typo" into their unit. Conventional prefixes (`feat`/`refactor`/`fix`/`docs`). **Pick the split seam by coherent THEME, not by what's conveniently isolable.** When a working tree holds several *distinct* arcs (feature A + feature B + a fixes-bucket), enumerate the arcs FIRST and assign every file to one — then commit per arc. The anti-pattern (2026-06-16): isolating the one self-contained file and dumping the other three arcs into a single catch-all "everything-else" commit. That's not "grouped" — it's a blob with one chip off it. A `git diff --stat`/per-file glance to classify ambiguous files is worth it; a commit titled-by-arc must contain only that arc's files.
- **Why:** git history is review **and future-training** signal — each commit a clean `(state→diff→why)` triple; one blob loses the reasoning sequence, one-per-change buries signal in noise. The sweet spot is *logical phase*. **Supersedes** the old "one commit per arc" (that fit the pre-foundation phase). **To re-grain LOCAL/unpushed commits without re-staging:** `git commit-tree <existing-commit>^{tree} -p <parent> -F -` to stitch a new history that reuses verified trees, then `git diff <old-HEAD> HEAD` must be **empty** (byte-identical) before trusting it.
- **Caveat (learned 2026-06-10 the hard way):** `git reset --hard` during a re-grain **wipes uncommitted tracked changes** (it ate this very rule's prior edit). Run `git status` first; stash or commit pending tracked edits before any `reset --hard`. [[R-37]] [[R-19]]
- **Origin:** 2026-06-10 — operator moved off one-per-arc toward atomic, then calibrated back ("not toooo many… roughly 3-4"); replaces the 2026-06-07 `feedback_one_commit_per_arc` seed. Extended 2026-06-16 — operator: "no thats not a good distribution of comimts, refine" after I split off the one self-contained fix and blobbed origin-gate + candidate-library + robustness into one catch-all; redone as 3 arc-coherent commits.

### R-17 — conventional commits, ≤800 chars
- **Trigger:** writing a commit message.
- **Rule:** `feat:`/`fix:`/`docs:`/`refactor:`/`chore:` etc.; hard cap 800 chars total incl. trailer; title <70; terse bullets, no motivation essays. Over 800 → rewrite, don't commit-and-fix-later. End with the `Co-Authored-By` trailer.
- **Why:** scannable history; the cap forces signal.
- **Origin:** 2026-06-07 — seeded from conventions / `feedback_commit_message_length`.

### R-18 — ruff format + check before commit
- **Trigger:** any commit.
- **Rule:** `python -m ruff format promptpotter/ tests/ && python -m ruff check promptpotter/ tests/` first. CI fails on format drift. (`.claude/**/*.py` is also in `ruff check .` scope.)
- **Why:** CI runs the same chain; format drift is a guaranteed red.
- **Origin:** 2026-06-07 — seeded from root CLAUDE.md.

### R-19 — never commit or push unless told
- **Trigger:** work is done and looks committable.
- **Rule:** don't `git commit` or `git push` unless the operator says so. A commit ask is NOT a push ask.
- **Why:** explicit operator gate; non-reversible/outward action.
- **Origin:** 2026-06-07 — seeded from root CLAUDE.md.

### R-20 — solo dev: commit to `main`
- **Trigger:** the operator asks to commit.
- **Rule:** default is commit straight to `main` + push (when asked), no feature-branch/PR ceremony — unless the operator explicitly wants a PR. [[R-19]]
- **Why:** solo dev; ceremony is friction.
- **Origin:** 2026-06-07 — seeded from `feedback_solo_dev_commit_to_main`.

### R-21 — CLI timeouts ≤30s; never background the runner
- **Trigger:** running a `promptpotter` CLI command.
- **Rule:** 30s default for all commands; raise only when the operator says "ready for data collection". Never run `campaign_runner` with `run_in_background` — always foreground. Never set Bash timeouts >60s without explicit permission.
- **Why:** a long unattended run burns spend; foreground keeps the operator in control.
- **Origin:** 2026-06-07 — seeded from conventions / `feedback_cli_timeout`.

### R-22 — `new` mints fresh; `resume` continues
- **Trigger:** operator edited a config and wants the run to pick it up, or any question about CLI verbs.
- **Rule:** CLI verbs are `new` (mint fresh session+cycle from round 0) and `resume` (extend the active session). There is NO `optimize` verb. `campaign.json` is loaded on every `new`. Optimizer-policy edits (`pobb_*`, `exploration.*`, …) don't flip the cycle hash → `resume` diverges → recover with bare `resume --fork-on-divergence`. Target-spec edits flip the hash → `new` auto-mints a fresh cycle. Read the error's `fork_hint:` literally; don't embellish.
- **Why:** the old "optimize --config" framing was a stale CLI name; the actual commands are `new` and `resume`.
- **Origin:** 2026-06-07 — seeded from `feedback_optimize_config_vs_resume`; updated 2026-06-13 (doc drift — `optimize` verb removed, correct verbs are `new` + `resume`).

### R-37 — scope `git add` to changed files; never commit a sibling repo's WIP
- **Trigger:** committing while other uncommitted work (a concurrent agent's, or operator WIP) sits in the tree, OR committing in a sibling/separate repo (`promptpotter-web`, TermNorm).
- **Rule:** run `git status` FIRST, then `git add` only the exact paths you changed. `git add <path>` on an *untracked* file stages the WHOLE file, not a diff — so a one-line edit to an untracked file commits the entire file. And never commit in a separate repo that holds active operator WIP without explicit per-repo confirmation — the operator owns those commits ("I'll go there later").
- **Why:** add-by-path on an untracked file swept a 57-line operator page into a commit meant for a one-word headline swap; committing in the marketing repo crossed a boundary the operator manages. [[R-19]] [[R-29]]
- **Origin:** 2026-06-10 — operator: "don't commit anything over there, I'll go there later" + the untracked-file wholesale-commit slip.

### R-39 — end a substantial task with a compact recommendation
- **Trigger:** finishing a substantial / multi-step turn — a completed arc, a feature, a big refactor, a deep investigation — the kind that piled up context.
- **Rule:** end the reply with an explicit one-line verdict: **`Compact: yes`** (+ why it's a clean boundary) or **`Compact: no`** (+ why hold). When it's "yes", keep the preceding writeup short — the operator compacts instead of reading detail. A clean checkpoint = work verified green + next step well-scoped + nothing half-applied in the working tree.
- **Why:** the operator uses the verdict to decide whether to read the writeup or just `/compact`; saying "compact" is permission to be terse. [[R-26]]
- **Origin:** 2026-06-11 — operator: "always end such a task with the recommendation whether to compact or not. if you say compact, I don't need to read too much."

## Investigation / interaction

### R-23 — say "origin", never "baseline"
- **Trigger:** referring to the starting point of a campaign/cycle.
- **Rule:** "origin". The rename away from "baseline" is complete; regressing it breaks domain language.
- **Why:** domain vocabulary discipline. [[R-05]]
- **Origin:** 2026-06-07 — seeded from `feedback_no_baseline_word`.

### R-24 — no hidden defaults
- **Trigger:** wiring an experiment knob or service param.
- **Rule:** all experiment knobs live in the notebook/config, explicit; no silent fallbacks in service code. [[R-03]]
- **Why:** a hidden default makes results unreproducible and hides intent.
- **Origin:** 2026-06-07 — seeded from `feedback_no_hidden_defaults`.

### R-25 — no cost / round predictions
- **Trigger:** before a `new`/`resume` run.
- **Rule:** never predict rounds/samples/total LLM calls/cost ahead of the run.
- **Why:** the operator finds the guesses noise; spend is observed, not forecast.
- **Origin:** 2026-06-07 — seeded from `feedback_no_cost_predictions`.

### R-26 — concise + declarative
- **Trigger:** every chat reply, spec, plan, summary.
- **Rule:** under ~800 chars; trim ≥20% off prose; drop hedging; state the call directly. No headers/tables/recap sections unless asked.
- **Why:** the operator reads fast and wants signal.
- **Origin:** 2026-06-07 — seeded from `feedback_response_length_cap` + `feedback_concise_declarative`.

### R-27 — don't trim reference docs unprompted
- **Trigger:** touching an existing spec, design doc, or plan.
- **Rule:** augment in place; never condense or restructure existing reference docs unless explicitly asked.
- **Why:** they carry deliberate context the operator relies on.
- **Origin:** 2026-06-07 — seeded from `feedback_dont_trim_unprompted`.

### R-28 — AskUserQuestion: one axis per question
- **Trigger:** building an AskUserQuestion.
- **Rule:** options vary on exactly one axis; never piggyback an unrelated config change (e.g. a `max_rounds` bump) as a hidden default on every option.
- **Why:** bundled options force a choice the operator didn't intend.
- **Origin:** 2026-06-07 — seeded from `feedback_no_bundled_options`.

### R-29 — no data deletion
- **Trigger:** tempted to delete a cycle dir / session / measurement.
- **Rule:** never delete unless data is genuinely stale or compromised. "Fresh experiment" is NOT a delete trigger — fork or mint instead. Spell out the full path and ask before any wipe.
- **Why:** runs are expensive and irreplaceable.
- **Origin:** 2026-06-07 — seeded from `feedback_no_data_deletion`.

### R-30 — CWD errors → tell the operator to `cd`
- **Trigger:** a missing `.env`/dataset/config traces to running from a subdir.
- **Rule:** the diagnosis stops at "you're in the wrong directory — `cd <root>` and rerun." Do NOT patch the loader to resolve paths relative to the package.
- **Why:** project convention is "run from project root"; path magic removes a useful failure signal.
- **Origin:** 2026-06-07 — seeded from `feedback_cwd_errors_say_cd`.

### R-31 — root `CLAUDE.md` is a thin entry point
- **Trigger:** editing root `CLAUDE.md`.
- **Rule:** thin orienting door, not a knowledge base. Pointers (`see docs/X.md`) over restated depth; no mirror tables of facts that live elsewhere. Bar: would this line earn its place if written fresh today? Depth lives in `docs/architecture.md` §0/§0.5.
- **Why:** it must load fast and orient; accreted prose defeats its purpose.
- **Origin:** 2026-06-07 — seeded from `feedback_claude_md_style`.

### R-32 — canonical test set first
- **Trigger:** wiring any new dataset/task (public or private).
- **Rule:** first investigate whether an author-recommended split / canonical test set exists (README, dataset card, paper eval section, or ask the operator what slice is reserved). Never invent a split without saying so; follow `docs/operations/adding-a-dataset.md`.
- **Why:** inventing a split risks test contamination; authors usually specify a protocol.
- **Origin:** 2026-06-07 — seeded from `feedback_canonical_test_set_first`.

### R-33 — on-disk surfaces stay live-written
- **Trigger:** touching anything that writes `dashboard.json` or round-boundary state.
- **Rule:** keep it LIVE-written (≤0.25s, round-boundary flush) — never teardown-only/lazy. The file tree IS the live dashboard for headless debugging.
- **Why:** a §0 commitment; lazy writes break headless observability.
- **Origin:** 2026-06-07 — seeded from `feedback_folder_ui_live_dashboard`.

### R-34 — drain the debt backlog before feature work
- **Trigger:** session start / before starting a feature.
- **Rule:** check `docs/specs/code-debt-cleanup.md` (and state-sync items); drain cheap, verified items first. Only file new debt at high confidence after verification, with file+line, why, action, blockers.
- **Why:** keeps debt from accreting; cheap wins compound.
- **Origin:** 2026-06-07 — seeded from `feedback_backlog_hygiene_daily`.

### R-35 — reuse the session-chosen asset; don't hardlock pre-launch brand assets
- **Trigger:** a surface needs a brand asset (share-card/OG image, icon, favicon, splash) and you're about to pull in a separate file (e.g. copy `wizard.jpg` from the marketing repo).
- **Rule:** reuse the symbol/asset already chosen this session for a sibling surface (e.g. the tab emoji 🏺 → render it to the share-card PNG) instead of importing a distinct asset. Prefer the minimal, already-decided, trivially-regenerable option. Pre-launch brand art is not settled — don't commit the app to it.
- **Why:** the operator is pre-publishing and may move away from current brand art; hardlocking onto `wizard.jpg` (or any one asset) across surfaces creates churn when it changes. One source symbol per session = one place to swap later. [[R-09]]
- **Origin:** 2026-06-08 — operator: "render the emoji used in the tab, don't add wizard.jpg, we might move away from that, don't wanna hardlock."

### R-36 — scoring/projection is backend; the webapp renders served scores
- **Trigger:** about to compute an *alternative* score or projection anywhere in `webapp/` TS — a what-if/ablation, a re-weighting, a fixed-sample-set accuracy, an alternative ordering, any "what would the score be if…". Also when the operator frames a feature as a *scoring* or *mask* concept.
- **Rule:** scoring/projection is a **backend** concept. It lives behind `score_search_point()` / the measurement archive and is **served** to the frontend as a field/endpoint. The webapp is a thin consumer that renders served values — it NEVER re-implements scoring math in TypeScript. A new "scoring view" (mask) = a backend projection over stored measurements + an API field, then a render. Confirm the layer *before* building; if it computes a score, it's backend.
- **Why:** scoring authority is backend ([[R-12]]). Recomputing client-side forks the truth, drifts from the single gateway, and can't be reused by the CLI / headless / other consumers — the file tree is an equal consumer and gets nothing from TS-only math. [[R-09]]
- **Origin:** 2026-06-10 — operator stopped a frontend-only "Mask" build (lib/mask/ + a fitness-card menu + a `useFitnessBars` TS recompute): "the mask should be a concept in the backend, not really in the frontend… refactor, standardize, unify the backend."

### R-40 — large-scope dataset assembly: audit silent-drop hazards before proposing execution
- **Trigger:** assembling/curating a benchmark dataset spanning many sources/domains (e.g. lca-termnorm BOM→ecoinvent), especially when the operator signals scope/complexity ("large scope", "various domains", "don't get stuck at the end", "don't forget something").
- **Rule:** do NOT converge or call the build "mechanical." First ground a **real data audit** (load the actual rows, don't hand-wave) and surface the long tail explicitly: (1) **placeholder/no-match targets** (`--`/empty/`n/a`) that, scored as misses, silently cap accuracy; (2) **gold-string-vs-candidate-pool exactness** — abbreviated/alias golds that fail raw-string scoring even when correct; (3) **multi-target / ambiguous** rows; (4) **per-domain accuracy hiding under an aggregate** (90% mean can mask a science domain at 40% → stratify the eval slice + gate on the worst domain); (5) **short-circuit nodes** (cache/fuzzy) masking the path being tuned. Give every row a *defined fate*; reconcile every gold to its exact pool entry; THEN propose the plan + gates.
- **Why:** these are the items "pushed aside as harmless" that wreck the end-state — the operator twice pushed back on premature convergence here. Cleverness = nothing silently dropped, nothing forgotten. [[R-32]] [[R-24]] [[R-08]]
- **Origin:** 2026-06-11 — operator: "really LARGE scope… various science domains… otherwise we get stuck at the end with some item we push aside, wrongfully thinking it harmless. Or we forget something."

### R-43 — operator in debug-mode → halt at round-1 + test-and-fix loop
- **Trigger:** the operator frames a campaign run as iterative debugging/finetuning — "ping-pong", "this'll take all day", "tons of bugs", "won't work right away", "you do the unit testing and fixing", "lots of finetuning required". Especially on a dataset with a known-broken later-round signature (e.g. lca-bom-termnorm rounds 2–5 collapsing).
- **Rule:** do NOT push to run the full `max_rounds` loop unattended, and do NOT offer "accept the known failure and run to completion" as an option. Halt at the **round-1 gate** and enter the loop: reproduce → write a **failing unit test** that captures the bug → root-fix → green `pytest/ruff/mypy` → re-run the affected slice (origin / round 1). The deliverable each iteration is a fix backed by a test, not more rounds. The operator drives cadence; you fix.
- **Why:** in debug-mode, rounds past the first broken one are wasted spend that produce no new signal — the bug is already visible at round 1. The work is the fix, not the run. I proposed running the full 5-round loop and "accepting the round-2+ risk"; the operator rejected both. [[R-21]] [[R-08]] [[R-42]] [[R-25]]
- **Origin:** 2026-06-14 — operator: "no don't accept the round-2+ risk… you do unit testing and fixing, this will take us the whole day, ping pong work… tons of bugs and finetuning required… don't do directly 5 rounds."

### R-38 — overlay markers: one calm indicator where the operator points; scope edits to the named surface
- **Trigger:** adding OR removing a visual marker for a divergence / mask / overlay / counterfactual state on any dashboard surface (lineage tree, round axis, fitness chart, samples).
- **Rule:** use ONE calm indicator placed exactly where the operator asked — a colored line/divider or a line-glow in the operator's stated color — NOT decorative animated glyphs (◆), rings, sparks, or flashing circles around numbers. Don't scatter the same marker across multiple surfaces "for consistency"; put it in the single surface named. **And scope every edit to that one surface:** when the operator says "remove the flash from element Y / put it in Z instead", touch ONLY Y and Z — do NOT also drop the marker the operator liked on a *different* surface (the lineage config click-line glow stayed wanted while the round-tab flash was killed). Confirm which surface each marker lives on before editing; a removal request names a surface, not a feature. When the operator says "make the line glow red" or "a red vertical line before the divergent values", that is literal: line + color, not icon + ring, in that place only.
- **Why:** the operator reads divergence as a boundary/color on the relevant element, not as ornament; glyphs read "weird symbol", rings/sparks around numbers read as noise, multi-surface duplication reads as clutter — and an over-broad removal nukes a marker they explicitly approved. Color/line over icon/ring, one location, edit-only-what-was-named. [[R-26]]
- **Origin:** 2026-06-10 — operator corrected the mask visuals three times: killed the ◆ glyph ("rather have it in the color, surrounding the click line — make the line glow RED"), killed the round-tab flashing circle ("don't make that round flash circle red around the ROUND number… instead a red vertical line in the Per-candidate fitness"), then "you should not have dropped the red circle highlight around the click line of the config in lineage, only the one in the [round-tabs] element."

### R-42 — TermNorm pipeline wrong/empty output: trace the contract seam, not the model
- **Trigger:** a TermNorm / material-matching run returns all-`NO_RESULT`, wrong predictions, a prompt-compile crash, or `json_validate_failed` — and you're tempted to blame model capacity, bump the model, or call the schema "too hard."
- **Rule:** default to a **contract/config seam**, not the model. Check, in order: (1) **prediction key** — the scorer reads the *terminal ranker's* output; a `token_matching`-terminal pipeline emits `candidate_ranking`, NOT `final_ranking` (`terminal_ranking`, `pobb/elimination/classification.py`). All-`NO_RESULT` while the backend logs show real candidates = this. (2) **placeholder collision** — a node prompt's backend `{{query}}`/`{{combined_text}}` placeholders are *content*, not optimizer slots; `compile_prompt` leaves non-slot `{{…}}` literal, `validate_template` owns authored-slot typos. A "Unsubstituted template variables" crash = this, not a malformed prompt. (3) **reasoning vs native JSON** — Groq gpt-oss does native strict `json_schema` fine at `reasoning_effort ≤ medium`; `high` returns HTTP 400 `json_validate_failed` (empty `failed_generation`, unrecoverable by the repair loop). Cap `reasoning_effort: low`; the model is capable. (4) **budget-exhaustion masquerading as conformance** — a strict-decode 400 whose `failed_generation` is `'max completion tokens reached before generating a valid document'` is *not* a schema/prompt defect; the constrained decoder hit the token ceiling mid-document (reasoning models spend uncapped reasoning tokens under a small ceiling like gpt-oss-20b's ~2048). There's no partial output to repair, so it lands on the origin as a hard **structural** wound. The root is the **LLM-client error-recovery seam** (`core/llm_providers.py`), NOT the prompt: classify it (inspect `body.error.failed_generation` for "max completion tokens", not just `.message`) and **recover in one shot** — drop strict decoding (native→prompt_repair), force `reasoning_effort=low`, uncap `max_tokens` — each lever once, then `continue` without consuming transport-retry budget. Trimming the prompt to dodge the ceiling is the R-08 symptom-patch (it only changes *when* you hit the wall); the client self-recovering is the systemic fix that makes ANY schema node on a small-ceiling model survive. (5) **a degraded/critical-origin verdict naming a node is a HINT, not the diagnosis — read the kind-stamped per-sample diagnostics FIRST.** Before proposing a model swap or a config/setting tune, open `round_NNNN.json::results[].pipeline_data.diagnostics.{step_statuses, warnings[].{step,code,kind,message}}` for the flagged samples. The verdict's `dominant_node`/attribution can mislead: a "5% structural entity_profiling" verdict was actually (a) ONE sample with `code=step_error kind=structural msg="list indices must be integers or slices, not str"` — a Python TypeError (`result['_metadata']=` on a *list* the non-strict recovery returned, `web_generate_entity_profile.py`), fixed by unwrapping the list at the call site; and (b) the rest were `web_search/low_document_count/transient` (web scarcity, not entity_profiling, not abort-worthy). No model and no entity_profiling setting touched either. The `message` string names the real cause in one line — `step_error`+TypeError = code bug → fix in TermNorm; `low_document_count` = web scarcity → loop tunes web_search; `llm_retry` = recovered, transient. **Probe the actual provider API** before concluding a capability limit — a ~6-line script against Groq settled the first three this arc; the fourth surfaced from a round-1 trace + the 400 body; the fifth from the per-sample warning kinds.
- **Why:** I blamed the model twice in one arc (gpt-oss-20b "too weak for JSON"; recommended a 120B bump) — both wrong; the real causes were a hardcoded prediction key, a placeholder-syntax collision, and an uncapped `reasoning_effort`. Then (case 4) I patched the *prompt* (trimmed the output ask) to escape a budget-exhaustion 400 before going to the client layer — the operator invoked the root-fix doctrine: the recoverable 400 belongs in the client's classify-and-recover, not at the prompt site. The *facts* live in `docs/operations/dataset-reasoning-matrix.md` + `docs/developer/pipeline-json-contract.md`; this rule is the *investigation order*. [[R-08]] [[R-13]] [[R-24]]
- **Origin:** 2026-06-11 — the lca-bom-termnorm NO_RESULT→native-JSON arc; operator: "is it not possible that the 20B also can get the structured output right?" + "I WANT to use the in-baked json output feature." Extended 2026-06-15 — the entity_profiling token-blowout: operator invoked `<root-fix>`, the fix landed in `core/llm_providers.py` (budget-400 self-recovery), origin went structural→0. Extended again 2026-06-15 (case 5) — I proposed flipping 20b→120b AND tuning `raw_content_limit`/`max_tokens` to chase a "degraded origin / entity_profiling 5% structural" verdict; the operator twice redirected ("keep 20b", "find a setting"), then the per-sample `diagnostics.warnings` showed it was a code TypeError + web scarcity — neither model nor setting. Lesson: read the kind-stamped diagnostics before reaching for the model/config dials.

### R-50 — "tidy/fix this <block>" scopes the edit to that block ONLY
- **Trigger:** the operator says "tidy" / "fix" / "clean up" / "make this nicer" and points at a specific pasted block, section, or quoted text. Fires hardest when you notice an unrelated blemish elsewhere in the same file (a dangling semicolon, a stale line, a typo) and feel the urge to "also fix while I'm here."
- **Rule:** edit ONLY the block the operator named. Do NOT touch unrelated lines, even obviously-broken ones outside the indicated scope — a pre-existing blemish elsewhere is not part of "tidy this." If you spot something else worth fixing, mention it in one line and let the operator decide; don't bundle it into the edit. The indicated scope IS the scope.
- **Why:** I was asked to "tidy" a pasted Fitness section but first "fixed" a dangling semicolon on an unrelated line that predated the session; operator: "no not that, just this" + "its complete out of line." Scope-creep on a tidy request reads as not listening and forces a revert. This is the document-level counterpart of [[R-38]] (scope every edit to the named surface) and sits beside [[R-27]] (don't trim reference docs unprompted) and [[R-31]] (root CLAUDE.md is a thin entry point — the tidy target here).
- **Origin:** 2026-06-18 — operator interrupted a CLAUDE.md "tidy" to say I'd touched the wrong line; the only wanted edit was the one pasted block.
