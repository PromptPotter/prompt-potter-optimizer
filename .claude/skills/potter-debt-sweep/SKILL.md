---
name: potter-debt-sweep
description: The code-hygiene sweep for PromptPotter. Runs a five-lens verification audit (dead code / fallbacks+hidden-defaults / structural / webapp R-36 / doc-drift), verifies every finding before touching anything, applies only the gate-safe fixes to the working tree, reconciles the debt backlog, and reports. Leaves the tree dirty for the operator to review — it never branches, commits, or pushes. Use before any release, or whenever the operator says "run the debt sweep" / "do the daily cleanup" / "sweep for untracked debt". Edits code autonomously — so the verify-before-act discipline and the gate are non-negotiable. Distinct from spec-buddy (spec docs, not code).
model: opus
---

# potter-debt-sweep — the code-hygiene sweep

**Goal:** surface code debt the linters can't (dead code, hidden defaults,
single-caller indirection, client-side scoring, doc drift), apply *only* the
fixes that are provably safe and pass the full gate, file the rest on the
backlog, and **leave the result in the working tree for the operator to review**.
The rigor below is the product, not the speed.

**The deliverable is a dirty tree plus a report — never a PR.** This skill does
not branch, commit, push, or open a PR. An earlier version pushed to a rolling
`debt-sweep/rolling` PR; it sat unmerged until it was 79 commits behind, and its
own doc line-ref fixes went stale *inside* it — the PR would have landed numbers
that were wrong by the time anyone read it. It was closed and the branch deleted.
A review artifact the operator has to leave their workflow to find does not get
reviewed. Report where they already are instead.

Because the sweep leaves edits uncommitted, **name every path you touched in the
report** — a concurrent session's `git add -A` would otherwise sweep them up.
It still NEVER commits to `main` and NEVER touches a sibling repo (TermNorm,
marketing).

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
5. **Stay on `main`.** There is no sweep branch — the audit runs on `main` itself and the
   fixes stay uncommitted in the tree. Prior fixes can't be re-flagged because they either
   landed (the operator committed them) or are still in the tree in front of you; anything
   deliberately *not* fixed lives on the backlog, which Phase 1 loads as the exclusion set.

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
(`file:line` · one-sentence verified why · proposed action · confidence).

**A zero from ripgrep is not a zero.** `rg` silently skips any file holding a NUL byte when
recursing — no warning, unlike `grep`'s "Binary file … matches" — and it cannot find such
files either, since searching for `\x00` skips exactly the files that contain one. The tool
cannot see its own blind spot, so a "zero call sites" verdict is only as good as `rg`'s
ability to read every candidate file. Two such files were found on 2026-07-17 (one of them
the backlog itself), each having manufactured false dead-code findings. Confirm any
zero-callers claim with a second shape — `git grep`, or a byte scan over `git ls-files`.

The lenses:

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
- **A feature / open lane**, not cleanup — e.g. the What-If mask write-side is Lane C8.
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
If the gate goes red, **revert the offending edit** and re-gate. A fix that can't pass the
gate becomes a backlog entry instead. Revert by restoring the specific hunk you wrote —
**never `git checkout -- <file>`**: it resets to HEAD, not to "before my edit", so on a tree
that already holds operator WIP or another sweep fix it destroys work you didn't write.

## Phase 6 — Reconcile the backlog

In `docs/specs/code-debt-cleanup.md`: strip every item that shipped (git log is the
history layer — do not leave a done-log), and **add** the held + medium findings as
one-line entries under the **Ready** (no blocker) or **Blocked** (name the blocker)
bucket, in the file's required shape (file:symbol · why · action · blocker). Do NOT
open a new dated section — the chronological sweep-log shape was retired 2026-06-19 for
readiness buckets. Re-confirm any stale-looking existing entry against the code before
trusting it (entries decay); fix or drop a wrong one as part of the sweep.

## Phase 7 — Report, leave the tree dirty

The fixes stay **uncommitted in the working tree**. Do not branch, commit, push, or open a PR.
The operator reads the report, reviews `git diff`, and commits what they want.

- **Report in chat**, mirroring the tiers: **Shipped** (each with its verified why),
  **Held + reasons**, **Medium for operator** (T5).
- **List every path you touched**, explicitly. This is the one thing the operator needs that
  they can't cheaply re-derive: a concurrent session's `git add -A` would otherwise sweep your
  edits into an unrelated commit, and a path list is what lets them `git add` by path instead.
- **Lead with the gate result.** Local-green is now the only proof there is — there's no PR CI
  behind it. If the gate is red, say so and name the failure; never report a clean sweep on red.
- **If nothing was safe to fix**: change nothing and say "swept — clean, nothing to fix". If only
  the backlog changed (new holds filed), say that; the backlog edit is itself left uncommitted.

## Guardrails (unattended-safe)

- **No data deletion** (R-29) — never a cycle dir / session / measurement.
- **No sibling-repo edits** (R-37) — TermNorm + marketing are off-limits; cross-repo
  items stay on the backlog as cross-repo notes.
- **Hold all feature/lane work** — this skill ships cleanup only.
- **Never branch, commit, push, or open a PR.** The deliverable is a reviewed-in-place diff.
- **Never sweep on red, never declare done on red** — Phase 0.3 aborts if main's CI is red;
  Phase 7 won't claim a clean sweep on a red gate.
