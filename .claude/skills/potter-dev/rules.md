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

**Architecture / seams**
- [R-09](#r-09) — reuse before adding; no sidecar
- [R-10](#r-10) — optimizer LLM calls go through `llm_call()`, never `chat()`
- [R-11](#r-11) — wrap LLM calls / backend matches with `observed_node()`
- [R-12](#r-12) — `score_search_point()` is the single scoring gateway
- [R-13](#r-13) — per-dataset tunable → overlay; TermNorm structural root-cause → fix in TermNorm
- [R-14](#r-14) — respect the hexagonal layer-import rules
- [R-15](#r-15) — a new seam/invariant is a `tests/test_structure.py` row
- [R-41](#r-41) — "will changing a connector tunable re-score?" — `node_configs` is the key; campaign config is frozen; cycle id is config-blind

**Workflow / git**
- [R-16](#r-16) — a few coherent commits per arc (logical units, not one blob, not one-per-change)
- [R-17](#r-17) — conventional commits, ≤800 chars, title <70
- [R-18](#r-18) — ruff format + check before commit
- [R-19](#r-19) — never commit or push unless told
- [R-20](#r-20) — solo dev: commit to `main`, no PR ceremony unless asked
- [R-21](#r-21) — CLI timeouts ≤30s; never background the runner
- [R-22](#r-22) — `--config` means mint-fresh, not "use this config"
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
- [R-31](#r-31) — root `CLAUDE.md` is a thin entry point
- [R-32](#r-32) — canonical test set first
- [R-33](#r-33) — `dashboard.json` and on-disk surfaces stay live-written
- [R-34](#r-34) — drain the debt backlog before feature work
- [R-35](#r-35) — reuse the session-chosen asset; don't hardlock pre-launch brand assets
- [R-36](#r-36) — scoring/projection is backend; the webapp renders served scores, never recomputes them
- [R-38](#r-38) — overlay markers: one calm indicator where the operator points; line/color over icon/ring/flash
- [R-42](#r-42) — TermNorm pipeline wrong/empty output: trace the contract seam, not the model

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

## Workflow / git

### R-16 — a few coherent commits per arc (not one blob, not one-per-change)
- **Trigger:** committing finished work — a feature, refactor, fix, or its docs.
- **Rule:** an arc = **a handful of commits** (~2–4), one per **coherent logical phase**, each compiling + green on its own with a body that explains the *why*. Calibration (operator, 2026-06-10): **bundle, lean coarse.** A multi-phase feature is ~1–2 commits (foundation+serve+overlay together is fine); a cleanup pass is ~1–2 (backend vs frontend split only when typecheck forces it — a renamed API param lands with the backend, the caller next). Do **NOT** split per-`W`/per-file/per-step — that's over-atomizing (8 was too many; ~4 was right for the mask arc: 2 feat + 2 refactor). Do **NOT** squash an entire arc into one blob either — that loses resolution. Fold only WIP/"checkpoint"/"fix typo" into their unit. Conventional prefixes (`feat`/`refactor`/`fix`/`docs`).
- **Why:** git history is review **and future-training** signal — each commit a clean `(state→diff→why)` triple; one blob loses the reasoning sequence, one-per-change buries signal in noise. The sweet spot is *logical phase*. **Supersedes** the old "one commit per arc" (that fit the pre-foundation phase). **To re-grain LOCAL/unpushed commits without re-staging:** `git commit-tree <existing-commit>^{tree} -p <parent> -F -` to stitch a new history that reuses verified trees, then `git diff <old-HEAD> HEAD` must be **empty** (byte-identical) before trusting it.
- **Caveat (learned 2026-06-10 the hard way):** `git reset --hard` during a re-grain **wipes uncommitted tracked changes** (it ate this very rule's prior edit). Run `git status` first; stash or commit pending tracked edits before any `reset --hard`. [[R-37]] [[R-19]]
- **Origin:** 2026-06-10 — operator moved off one-per-arc toward atomic, then calibrated back ("not toooo many… roughly 3-4"); replaces the 2026-06-07 `feedback_one_commit_per_arc` seed.

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

### R-22 — `--config` means mint-fresh
- **Trigger:** operator edited a config and wants the run to pick it up.
- **Rule:** `optimize --config <path>` = mint a brand-new session+cycle from round 0; it is NOT "use this config". `campaign.json` is loaded on every `optimize`. Optimizer-policy edits (`pobb_*`, `exploration.*`, …) don't flip the cycle hash → resume diverges → recover with bare `optimize --fork-on-divergence` (no `--config`). Target-spec edits flip the hash → `--config` auto-mints. Read the error's `fork_hint:` literally; don't embellish.
- **Why:** the mental model "--config = which config to use" is wrong and the CLI rejects the combination.
- **Origin:** 2026-06-07 — seeded from `feedback_optimize_config_vs_resume`.

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
- **Trigger:** before an `optimize`/`new`/`resume` run.
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

### R-38 — overlay markers: one calm indicator where the operator points; scope edits to the named surface
- **Trigger:** adding OR removing a visual marker for a divergence / mask / overlay / counterfactual state on any dashboard surface (lineage tree, round axis, fitness chart, samples).
- **Rule:** use ONE calm indicator placed exactly where the operator asked — a colored line/divider or a line-glow in the operator's stated color — NOT decorative animated glyphs (◆), rings, sparks, or flashing circles around numbers. Don't scatter the same marker across multiple surfaces "for consistency"; put it in the single surface named. **And scope every edit to that one surface:** when the operator says "remove the flash from element Y / put it in Z instead", touch ONLY Y and Z — do NOT also drop the marker the operator liked on a *different* surface (the lineage config click-line glow stayed wanted while the round-tab flash was killed). Confirm which surface each marker lives on before editing; a removal request names a surface, not a feature. When the operator says "make the line glow red" or "a red vertical line before the divergent values", that is literal: line + color, not icon + ring, in that place only.
- **Why:** the operator reads divergence as a boundary/color on the relevant element, not as ornament; glyphs read "weird symbol", rings/sparks around numbers read as noise, multi-surface duplication reads as clutter — and an over-broad removal nukes a marker they explicitly approved. Color/line over icon/ring, one location, edit-only-what-was-named. [[R-26]]
- **Origin:** 2026-06-10 — operator corrected the mask visuals three times: killed the ◆ glyph ("rather have it in the color, surrounding the click line — make the line glow RED"), killed the round-tab flashing circle ("don't make that round flash circle red around the ROUND number… instead a red vertical line in the Per-candidate fitness"), then "you should not have dropped the red circle highlight around the click line of the config in lineage, only the one in the [round-tabs] element."

### R-42 — TermNorm pipeline wrong/empty output: trace the contract seam, not the model
- **Trigger:** a TermNorm / material-matching run returns all-`NO_RESULT`, wrong predictions, a prompt-compile crash, or `json_validate_failed` — and you're tempted to blame model capacity, bump the model, or call the schema "too hard."
- **Rule:** default to a **contract/config seam**, not the model. Check, in order: (1) **prediction key** — the scorer reads the *terminal ranker's* output; a `token_matching`-terminal pipeline emits `candidate_ranking`, NOT `final_ranking` (`terminal_ranking`, `pobb/elimination/classification.py`). All-`NO_RESULT` while the backend logs show real candidates = this. (2) **placeholder collision** — a node prompt's backend `{{query}}`/`{{combined_text}}` placeholders are *content*, not optimizer slots; `compile_prompt` leaves non-slot `{{…}}` literal, `validate_template` owns authored-slot typos. A "Unsubstituted template variables" crash = this, not a malformed prompt. (3) **reasoning vs native JSON** — Groq gpt-oss does native strict `json_schema` fine at `reasoning_effort ≤ medium`; `high` returns HTTP 400 `json_validate_failed` (empty `failed_generation`, unrecoverable by the repair loop). Cap `reasoning_effort: low`; the model is capable. **Probe the actual provider API** before concluding a capability limit — a ~6-line script against Groq settled all three this arc.
- **Why:** I blamed the model twice in one arc (gpt-oss-20b "too weak for JSON"; recommended a 120B bump) — both wrong; the real causes were a hardcoded prediction key, a placeholder-syntax collision, and an uncapped `reasoning_effort`. The *facts* live in `docs/operations/dataset-reasoning-matrix.md` + `docs/developer/pipeline-json-contract.md`; this rule is the *investigation order*. [[R-08]] [[R-13]] [[R-24]]
- **Origin:** 2026-06-11 — the lca-bom-termnorm NO_RESULT→native-JSON arc; operator: "is it not possible that the 20B also can get the structured output right?" + "I WANT to use the in-baked json output feature."
