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

**Workflow / git**
- [R-16](#r-16) — one commit per arc
- [R-17](#r-17) — conventional commits, ≤800 chars, title <70
- [R-18](#r-18) — ruff format + check before commit
- [R-19](#r-19) — never commit or push unless told
- [R-20](#r-20) — solo dev: commit to `main`, no PR ceremony unless asked
- [R-21](#r-21) — CLI timeouts ≤30s; never background the runner
- [R-22](#r-22) — `--config` means mint-fresh, not "use this config"

**Investigation / interaction**
- [R-23](#r-23) — say "origin", never "baseline"
- [R-24](#r-24) — no hidden defaults
- [R-25](#r-25) — no cost / round predictions before a run
- [R-26](#r-26) — concise + declarative; replies under ~800 chars
- [R-27](#r-27) — don't trim or restructure reference docs unprompted
- [R-28](#r-28) — AskUserQuestion options vary on one axis only
- [R-29](#r-29) — no data deletion
- [R-30](#r-30) — CWD errors → tell the operator to `cd`, don't paper over
- [R-31](#r-31) — root `CLAUDE.md` is a thin entry point
- [R-32](#r-32) — canonical test set first
- [R-33](#r-33) — `dashboard.json` and on-disk surfaces stay live-written
- [R-34](#r-34) — drain the debt backlog before feature work
- [R-35](#r-35) — reuse the session-chosen asset; don't hardlock pre-launch brand assets

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

## Workflow / git

### R-16 — one commit per arc
- **Trigger:** finishing a feature + its refinements/fixes/docs.
- **Rule:** a whole arc = ONE commit. Amend follow-ups in, squash, force-push your own branch — never fragment into many small commits.
- **Why:** the history reads as intent, not keystrokes.
- **Origin:** 2026-06-07 — seeded from `feedback_one_commit_per_arc`.

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
