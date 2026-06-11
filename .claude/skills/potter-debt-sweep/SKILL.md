---
name: potter-debt-sweep
description: The daily automated code-hygiene sweep for PromptPotter. Runs a five-lens verification audit (dead code / fallbacks+hidden-defaults / structural / webapp R-36 / doc-drift), verifies every finding before touching anything, applies only the gate-safe fixes, reconciles the debt backlog, and ends with a PR. Use on the daily schedule, before any release, or whenever the operator says "run the debt sweep" / "do the daily cleanup" / "sweep for untracked debt". Edits code autonomously — so the verify-before-act discipline and the gate are non-negotiable. Distinct from potter-dev (the imprinted playbook this skill loads) and spec-buddy (spec docs, not code).
model: opus
---

# potter-debt-sweep — the daily code-hygiene PR

**Goal:** every day, surface code debt the linters can't (dead code, hidden
defaults, single-caller indirection, client-side scoring, doc drift), apply
*only* the fixes that are provably safe and pass the full gate, file the rest
on the backlog, and **open a PR** so the operator reviews at leisure and `main`
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
3. **Gate is green at HEAD before you start** (so any red later is *yours*):
   `python -m ruff check promptpotter/ tests/ && python -m mypy promptpotter/ && python -m pytest -q`.
   If already red, abort and report — don't pile cleanup onto a broken main.

## Phase 1 — Baseline

- Invoke **potter-dev APPLY** (loads `rules.md` + the seams/conventions). Honor every rule.
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

After applying the safe fixes:
`python -m ruff format promptpotter/ tests/` →
`python -m ruff check promptpotter/ tests/` → `python -m mypy promptpotter/` →
`python -m pytest -q`. If any TypeScript changed: `cd webapp && npm run lint && npx tsc --noEmit && npm run test && npm run build`.
If the gate goes red, **revert the offending edit** (`git checkout -- <file>`) and
re-gate — do not open a PR with a red gate. A fix that can't pass the gate becomes a backlog entry instead.

## Phase 6 — Reconcile the backlog

In `docs/specs/code-debt-cleanup.md`: strip every item that shipped (git log is the
history layer — do not leave a done-log), and **add** the held + medium findings with
the file's required shape (file+line · why · action · blockers). Augment in place; never restructure (R-27).

## Phase 7 — End with a PR

- Branch: `debt-sweep/YYYY-MM-DD` (date from the run; pass it in — do not call `Date.now()` inside a workflow).
- Commit in **~2 coherent commits** (R-16): one for the code fixes
  (`refactor: drop dead code + hidden defaults (daily debt sweep)`), one for docs/backlog
  (`docs: fix <surface> drift + reconcile debt backlog`). Conventional prefixes, ≤800 chars,
  the `Co-Authored-By` trailer. `git add` only the paths you changed (R-37).
- Push the branch; `gh pr create` with a body that mirrors this skill's report:
  **Shipped** (tiered, with the verified why), **Held + reasons** (the verify-before-act
  catches), **Medium for operator** (T5). Title: `Daily debt sweep — YYYY-MM-DD`.
- **If nothing was safe to fix and nothing changed**: open no PR; emit a one-line
  "swept YYYY-MM-DD — clean, nothing to fix" so the daily signal still lands. If only
  the backlog changed (new holds filed), open the PR with just the docs commit.

## Guardrails (unattended-safe)

- **No data deletion** (R-29) — never a cycle dir / session / measurement.
- **No sibling-repo edits** (R-37) — TermNorm + marketing are off-limits; cross-repo
  items stay on the backlog as cross-repo notes.
- **Hold all feature/lane work** — this skill ships cleanup only.
- **One run = one PR** (or none). Never push to `main`. Never force-push.
- If a prior `debt-sweep/*` branch is still open/unmerged, note it in the new PR body and continue.
