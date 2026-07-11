---
name: potter-debt-sweep
description: The daily automated code-hygiene sweep for PromptPotter. Runs a five-lens verification audit (dead code / fallbacks+hidden-defaults / structural / webapp R-36 / doc-drift), verifies every finding before touching anything, applies only the gate-safe fixes, reconciles the debt backlog, and appends to one rolling PR. Use on the daily schedule, before any release, or whenever the operator says "run the debt sweep" / "do the daily cleanup" / "sweep for untracked debt". Edits code autonomously — so the verify-before-act discipline and the gate are non-negotiable. Distinct from spec-buddy (spec docs, not code).
model: opus
---

# potter-debt-sweep — the daily code-hygiene PR

**Goal:** every day, surface code debt the linters can't (dead code, hidden
defaults, single-caller indirection, client-side scoring, doc drift), apply
*only* the fixes that are provably safe and pass the full gate, file the rest
on the backlog, and **append to one rolling PR** so the operator reviews at leisure and `main`
never takes an unreviewed auto-edit. The operator must be able to trust this
running unattended — so the rigor below is the product, not the speed.

This skill is the **explicit exception** to "commit straight to main" (R-20)
and "never push unless told" (R-19): the operator has standing-authorized this
skill's runs to branch → commit → push → PR. It still NEVER commits to `main`
directly and NEVER touches a sibling repo (TermNorm, marketing).

---

## Phase 0 — Preconditions (abort cleanly if unmet)

1. **Working tree must be clean.** `git status --porcelain`. If dirty, **abort** —
   do not entangle the sweep with operator WIP. Report "tree dirty, skipped" and stop.
2. **On `main`, up to date.** `git fetch && git checkout main && git pull --ff-only`.
   If the pull isn't fast-forward, abort and report.
3. **`main`'s own CI must already be green.** `gh run list --branch main --workflow CI --limit 1`
   — if the latest run is `failure` or still `in_progress`, **abort and report** ("main CI
   <state> — fix before sweeping"). A sweep never builds on a red main and never stacks a PR
   on top of one (this is exactly how the 2026-06-15 pile-up happened).
4. **Gate is green at HEAD, in the *pinned* environment** (so local-green ⇒ CI-green — the
   sweep must see what CI sees, not a stale local resolve):
   `pip install -q uv` (if absent) → `python -m uv sync --extra stats --extra dev --frozen` →
   `python -m uv run ruff check promptpotter/ tests/ && python -m uv run mypy promptpotter/ &&
   python -m uv run pytest -q`. The `--frozen` flag installs the exact `uv.lock` graph CI uses;
   never gate against an ad-hoc `pip install` set. If already red, abort and report.
5. **Establish the rolling branch.** `gh pr list --head debt-sweep/rolling --state open`. If a
   rolling PR is open → `git checkout -B debt-sweep/rolling origin/debt-sweep/rolling &&
   git merge --no-edit origin/main` (fold main in by **merge, never rebase/force-push**; on a
   conflict, abort and report — let the operator land it). If none is open → stay on `main`; the
   fresh `debt-sweep/rolling` branch is cut in Phase 7. The audit runs on this ref so prior-day
   fixes not yet merged aren't re-flagged.

## Phase 1 — Baseline

- Read **`docs/developer/conventions.md`** (the style + code-shape + git rules) and the per-layer
  `promptpotter/*/CLAUDE.md` for the seams you touch. Honor every convention. (Legacy `R-NN` tags below
  are shorthand for the named conventions in that file — e.g. "No data deletion", "~2 coherent commits",
  R-36 "scoring authority is backend-served"; map by name, there is no numbered-rule registry.)
- Read `docs/specs/code-debt-cleanup.md` end to end. Everything already tracked there
  (Active backlog, the held tiers, "Considered not debt", M13 placeholders, the
  anti-patterns-to-skip list) is the **exclusion set** — do NOT re-report it.

## Phase 2 — Five-lens fan-out (parallel)

Spawn **five `general-purpose` agents in one message** (one per lens). Each gets:
the high-confidence bar ("verified by tracing call sites + reading bodies, NOT a
spotted smell"), the exclusion set from Phase 1, and the output contract
(`file:line` · one-sentence verified why · proposed action · confidence). The lenses:

1. **Dead code & speculative surface** — params never read; `X | None` returns always non-None; default kwargs no caller overrides; Pydantic/dataclass fields declared-but-unpopulated; enum variants with no construction site; unreferenced symbols. *Verify: grep ALL refs across `promptpotter/` + `tests/` + `webapp/` (wire keys) + `datasets/` (JSON).*
2. **Fallbacks & hidden defaults (R-03/R-04/R-24/R-07)** — `.get(k, default)` on contract-guaranteed keys; `X or <default>` masking a contract value; try/except that swallows+defaults; default kwargs encoding an experiment knob; breadcrumb/back-compat comments + the shim they guard. *Skip the two sanctioned fallbacks (score_population synthetic-0; load-boundary deprecated-sample gate) and external-input boundary guards (file/JSON ingest, `extra='forbid'` user-config).*
3. **Structural** — single-caller indirection with no test + no layer-boundary reason (inline candidate); near-duplicate logic that should be one seam; misplaced responsibility; god-files that regrew. *Skip layer-boundary splits (application/intelligence ↮ application/optimization) and `_private` helpers used once in the same file.*
4. **Webapp R-36** — any scoring/fitness/accuracy/ordering/what-if math computed in TypeScript (must be a backend served projection); dead components / unused exports / dead branches; duplicate derivations. *Skip the M13 intentional UI placeholders (Topbar search, ChatPane attach/toggles, AccountModal buttons).*
5. **Doc/config drift** — doc/CLAUDE.md/spec claims about a symbol/path/signature the code contradicts; stale comments; config keys referenced-but-absent; vocabulary regressions (`baseline`/`query`/`service`/`building block` where the domain word belongs); broken cross-references. *Skip CHANGELOG history + archival meta-campaign snapshots.*

## Phase 3 — Synthesize into tiers

Group findings: **T1** dead code · **T2** hidden defaults/dead defensiveness ·
**T3** webapp R-36 · **T4** doc/config drift · **T5** medium / needs-operator-judgment.

## Phase 4 — VERIFY-BEFORE-ACT (the load-bearing phase)

The subagents produce false positives. **Re-verify every finding yourself**
before editing — grep the refs, read the bodies. Apply a fix ONLY when it is a
clean, mechanical, gate-safe change. **HOLD** (file to backlog, do not edit) anything that is:

- **Coupled to a live/known reader** — e.g. a field the notebook or an LLM
  schema round-trips. *(2026-06-11: `DatasetSummary.splits` + `TaskDecomposition.FIELDS`
  are read by the HITL notebook; `FewShotExample.explanation` round-trips via the L1
  `FewShotExample(**ex)` schema — all three looked "dead" and were NOT.)*
- **A feature / open lane**, not cleanup — e.g. the `whatif/` mask write-side is Lane C8.
- **A root-fix that lives upstream (R-08)** — e.g. a webapp fallback papering over a
  backend the projection should serve. File the root cause; don't patch the symptom.
- **A judgment call** — delete-vs-adopt a constant, a documented-public `__all__`
  entry, a behavior change. Surface it in T5 for the operator.
- **Cosmetic with contract risk** — `__all__` trims where the symbol is documented
  protocol/registry surface.

When in doubt, HOLD. A missed cleanup is free; a wrong auto-edit on `main`-bound code is not.

## Phase 5 — Gate (never ship red)

After applying the safe fixes, gate through the **pinned** env (same as Phase 0.4):
`python -m uv run ruff format promptpotter/ tests/` →
`python -m uv run ruff check promptpotter/ tests/` → `python -m uv run mypy promptpotter/` →
`python -m uv run pytest -q`. If any TypeScript changed: `cd webapp && npm run lint && npx tsc --noEmit && npm run test && npm run build`.
If the gate goes red, **revert the offending edit** (`git checkout -- <file>`) and
re-gate — do not open a PR with a red gate. A fix that can't pass the gate becomes a backlog entry instead.

## Phase 6 — Reconcile the backlog

In `docs/specs/code-debt-cleanup.md`: strip every item that shipped (git log is the
history layer — do not leave a done-log), and **add** the held + medium findings as
one-line entries under the **Ready** (no blocker) or **Blocked** (name the blocker)
bucket, in the file's required shape (file:symbol · why · action · blocker). Do NOT
open a new dated section — the chronological sweep-log shape was retired 2026-06-19 for
readiness buckets. Re-confirm any stale-looking existing entry against the code before
trusting it (entries decay); fix or drop a wrong one as part of the sweep.

## Phase 7 — Append to the rolling PR

One stable branch, `debt-sweep/rolling`, carries every sweep until the operator merges it —
daily runs **append**, they never open a second PR. (The 06-15 pile-up was fresh-branch-per-day;
rolling collapses the queue to one reviewable PR.)

- Branch: always `debt-sweep/rolling` (set up in Phase 0.5 — already checked out if a PR was open).
- Commit in **~2 coherent commits** (R-16): one for the code fixes
  (`refactor: drop dead code + hidden defaults (daily debt sweep)`), one for docs/backlog
  (`docs: fix <surface> drift + reconcile debt backlog`). Conventional prefixes, ≤800 chars,
  the `Co-Authored-By` trailer. `git add` only the paths you changed (R-37).
- Push (`git push -u origin debt-sweep/rolling`) — fast-forward only; **never force-push** (fold
  `main` in via the Phase 0.5 merge, never a rebase, so the operator's in-flight review survives).
- **PR body:** if the rolling PR is open, **append** a dated section via `gh pr edit` (R-27 —
  augment, never rewrite the existing body); else `gh pr create`, title `Debt sweep — rolling`.
  Each dated section mirrors the report: **Shipped** (tiered, verified why), **Held + reasons**,
  **Medium for operator** (T5). (Date passed in — do not call `Date.now()` inside a workflow.)
- **Confirm the PR's CI is green before declaring done.** After pushing, `gh pr checks
  debt-sweep/rolling --watch` (or poll). If the `check` job goes red, **report the failure** —
  do not claim a clean sweep. Local-green is necessary, the PR's own CI is the proof.
- **If nothing was safe to fix and nothing changed**: push nothing, open/append nothing; emit a
  one-line "swept YYYY-MM-DD — clean, nothing to fix" so the daily signal still lands. If only
  the backlog changed (new holds filed), append the docs commit alone.

## Guardrails (unattended-safe)

- **No data deletion** (R-29) — never a cycle dir / session / measurement.
- **No sibling-repo edits** (R-37) — TermNorm + marketing are off-limits; cross-repo
  items stay on the backlog as cross-repo notes.
- **Hold all feature/lane work** — this skill ships cleanup only.
- **One open rolling PR at a time** — every run appends to `debt-sweep/rolling` until the operator
  merges it; never open a second debt-sweep PR. Never push to `main`. Never force-push.
- **Never sweep on red, never declare done on red** — Phase 0.3 aborts if main's CI is red;
  Phase 7 won't claim a clean sweep until the rolling PR's CI is green.
