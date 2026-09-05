---
status: accepted
date: 2026-07-15
deciders: [maintainer]
consulted: [identity-foundation, spend-and-tenancy, operator-admin-channels, m12-control-plane]
informed: []
relates:
  - docs/adr/0002-identity-foundation.md
  - docs/adr/0003-spend-and-tenancy.md
  - docs/adr/0004-operator-admin-channels.md
  - docs/adr/0001-m12-control-plane.md
supersedes: []
superseded-by: []
tags: [security, identity, authorization, capabilities, delegation, multi-tenant, zero-trust]
---

# Delegated principals & capability scoping — sub-users, channels, attenuation

> **STATUS: ACCEPTED — §§1,3,4,5,6 SHIPPED; §2 (channels) + the babysat *subtree*
> model remain proposal.**
> The **Open questions** at the end track what's left. Edit freely.
>
> **Shipped (2026-07-15): §3 enforcement seam + §1 sub-principal core.**
> **§3:** `CAP_FOR_KIND` + `_require_capability_for` at the dispatcher's single
> `_record_and_apply` chokepoint (`command_dispatcher.py`); an
> import-time exhaustiveness assert derives the closed kind set from the `*Kind`
> `Literal`s so the map cannot drift. Seven command capabilities
> (`CAMPAIGN_{STEP,RUN,CREATE,BUDGET,LIFECYCLE,BABYSIT,LOOKAHEAD}_CAP`, enumerated once as
> `CAMPAIGN_CAP_BY_NAME`) + `OWNER_COMMAND_CAPABILITIES` in `shared/identity.py`.
> Every first-class principal holds the full owner set, so the gate is a no-op for
> single-owner installs. Denial is 404 (existence-hiding). Closes gap #2.
> **§1:** the sealed grant store (`infrastructure/identity/grants.py`,
> `.promptpotter/identity/grants.json`) — a delegate authenticates via its own
> OIDC identity, `_identity_context_from_session` resolves its grant and rebinds
> it to act inside the delegator's tenant with `grant ∩ owner` capabilities
> (attenuation enforced at read, defense-in-depth), audited as itself
> (`claims["principal"]` → `issued_by_user_id`). A malformed/no-delegator grant
> fails secure (own tenant, no caps). Provisioned through the operator-admin
> channel (`admin_bot.py`: `/grant`, `/revoke`, `/grants`) — the identity zone a
> delegate cannot write. Security test in `tests/test_security.py`.
> **Also shipped (2026-07-15):** §5 per-grant spend-ceiling *enforcement*
> (`admit_launch` reads the declaration down to the claim ceiling); §4 babysit
> *minimal* (a fork seed whose `pipeline_overlay` sets a locked axis — model/provider —
> requires `campaign.babysit`, stamps the cycle babysat, and forces its runs to grade
> `C` via `grade_run(human_intervened=…)`; the trigger is the overlay edit, not any
> policy flag — `forbidden_axes_strict` was removed, `PARAM_FORBIDDEN_KEYS` is now an
> invariant the optimizer never searches);
> and one-level delegation now *enforced* at the grant writer (a delegator that is
> itself a sub-principal is rejected); §6 the bounded **`step-cycle`** verb (advance N
> rounds in place then auto-pause) wired onto the resume machinery via
> `RunMode.stop_after_rounds` — no new runner path.
> **Deferred still:** channel-scoping (§2) and the babysat *subtree* model + fork-clean
> escape (§4 target).

## Context and Problem Statement

Today PromptPotter has exactly two principal shapes: a **registered user** (an
`IdentityContext` with tenant-scoped capabilities) and a **host admin** (the same, whose
powers ride the ADR-0004 operator-admin channel rather than the API — there is no
admin-only capability).
That is enough for "one operator runs their own campaigns" and nothing more.

A user now wants to **delegate**. Concretely:

- Mint as many **sub-users** ("friendly sub-user things") as they like, each with a
  chosen slice of the user's own rights — e.g. "may start a campaign," "may only step
  it one round at a time."
- Give each sub-user a **spend ceiling**, so a delegate cannot burn the whole budget.
- Vary those rights **by channel**: the *same* sub-user reaching in from an AI
  assistant (Claude / an MCP client) gets one limit + permission set; reaching in from
  the company-associated PC gets another.

This generalizes a decision we hit building the L4 inner-optimizer **model-unlock**
(first built as a `forbidden_axes_strict` flag, since removed — the model is now set by
a direct overlay edit on a fork). We first framed the guard as "only a *human* may unlock
it." That is the wrong axis. The principal doing a
privileged action isn't "human vs. machine" — it's an identity carrying (or lacking) a
**capability**. The user's own AI, acting on their behalf, should carry the user's
rights (co-principal); an external assistant reaching in over MCP should carry a
**strictly smaller** subset. Same action, different authority, decided by capability —
cleanly configurable at every interface.

A second simplification followed. Do **not** build a *specific* unlock control per
privileged value (no "model-unlock toggle"). Any such value is simply **directly
editable**; the act of editing a value the optimizer normally owns — or the origin
locks — is what carries the consequence: a warning, and a **babysat** tag on the
remainder of that **lineage branch** (not the whole campaign). "Babysat" is not a new
flag. It rides the existing
measurement-provenance grade (`domain/measurement_provenance.py`), which already
separates *deliberate, clean* optimizer exploration (`A`) from incidental runs (`C`) and
already keeps the latter out of the cross-cycle digest, out of reuse, and out of "a
future optimizer-in-an-optimizer (L4) ingests only graded-clean records." A human-steered
edit stamps subsequent runs so that same machinery excludes them. **One generic
mechanism — direct edit → warn → babysat — not N per-action toggles.**

Two gaps make this urgent, not merely nice:

1. **No sub-principals, no delegation, no channel scoping, no per-principal budget.**
   The identity model stops at user/admin.
2. **~~The command highway applies privileged fields with no per-verb capability
   check.~~ CLOSED (2026-07-15, §3).** `POST /commands/{kind}` used to deserialize and
   apply the payload for any authenticated principal who could reach the route — no
   per-verb capability check existed at the command seam. `CAP_FOR_KIND` + the dispatcher
   gate now enforce a per-verb capability at the one seam. (Gap #1 —
   sub-principals/delegation/channels — remains.)

## Decision Drivers

* **Attenuation, never escalation.** A delegate's authority is always a *subset* of the
  delegator's. Re-delegation can only narrow. This is the classic capability-security
  property (OAuth scopes / macaroons) and the only safe default for user-minted
  sub-principals.
* **Defense in depth — the server is the boundary.** The client MAY reflect a
  capability (show/hide a control); it MUST NOT be the enforcement. Every privileged
  action is checked server-side against the acting identity. (ADR-0004 zero-trust.)
* **Least privilege + graduated actions.** Removing a high capability must not remove
  *all* ability — it should drop the principal to a **more bounded** action (run a
  campaign → step one round at a time), not to nothing. Privilege is a ladder over the
  verbs, not a single switch.
* **Build on what exists.** `IdentityContext.capabilities`, `has_capability`,
  the closed command-verb set, the ADR-0003 spend-cap machinery, and
  `domain/measurement_provenance.py` are the primitives. This ADR *composes* them;
  it does not replace them.
* **Generic over specific.** One mechanism (direct edit → babysat), not a bespoke
  control per privileged value. The capability gates *who* may make such an edit; the
  provenance grade records *that* it happened and taints the run.
* **Secure methods only, now.** Build the framework so channels (own-AI, MCP, company
  PC) can slot in later, but ship **only capabilities whose enforcement is secure**. The
  parts that depend on unsolved, spoofable channel identification are designed-for but
  **not exposed to users** until a secure method exists (see Open questions). "Easy to
  host" and "easy to host securely" stay the same path (ADR-0004).
* **Cleanly configurable at every interface.** Own-AI, external MCP, browser, company
  PC — each is a channel with its own grant. The configuration surface is uniform.

## Decision (proposed)

### 1. Principals & sub-principals, with an attenuation invariant

A user may mint **sub-principals**. Each is an `IdentityContext` whose `capabilities`
and spend ceiling are chosen by the delegator, subject to the invariant:

> **Attenuation:** a delegate's capability set ⊆ the delegator's, and its spend ceiling
> ≤ the delegator's remaining ceiling. Enforced *at grant time* and re-checked at use.
> Re-delegation (a sub-principal minting its own) only narrows further.

> **Sealed grant store:** a sub-principal's grants (caps + ceiling) live in a store the
> sub-principal **cannot write** — the identity/config zone, never the tenant's own
> editable space. A delegate that could edit its own grant would self-escalate, defeating
> attenuation. Same protected-zone rule as the sign-in blocklist (ADR-0004): the file that
> decides authority is the most protected file in the install.

The user's **own AI assistant**, in the host==user case, acts under the user's
identity (co-principal, full caps) — which is why "you are the user and I allow it"
already works under `ADMIN=1`. An **external assistant / MCP** is a *distinct*
sub-principal holding an attenuated subset.

### 2. Channel-scoped grants — designed-for, not exposed yet

A grant is keyed on **(sub-principal, channel)**, not on the principal alone: the same
sub-user carries different (capabilities, spend ceiling) depending on the **channel** they
arrive through — an AI-assistant/MCP session vs. the company-PC OIDC session vs. a
browser. The effective authority of a request is `grant(principal, channel)`.

**Built-for, but not shipped to users now (driver: secure-only).** Channel-scoping is
only as sound as channel *identification* is unspoofable, and that is unsolved (Open
question #1). So the data model reserves the `channel` key and the grant lookup takes it,
but until a secure channel-identity method exists, only a **single trusted channel**
resolves (the authenticated session), and no user-facing configuration exposes
per-channel grants. The seam is present so the secure version slots in without reshaping
the model; the insecure version is never offered.

### 3. Capability → verb ladder (one enforcement seam) — SHIPPED

Every control-plane verb requires a capability. The check lives in **one place** — the
command dispatcher tests `has_capability(identity, CAP_FOR_KIND[kind])` at the single
`_record_and_apply` chokepoint every dispatch method funnels through (typed check-in
routes reach it too). As-shipped capabilities over the *real* verb set:

| Cap | Gates (real command kinds) | Kind |
|---|---|---|
| `campaign.step` | `skip-searchpoint`, `pause-cycle`, `origin-gate-decision`, `step-cycle` (SHIPPED — see §6) | stepwise / bounded |
| `campaign.run` | `start-run`, `fork-cycle`, `start-checkin` | autonomous |
| `campaign.create` | `mint-campaign`, `register-backend`, `edit-draft-campaign`, `resolve-origin` | create |
| `campaign.budget` | `change-spend-budget` (raise a ceiling) | budget |
| `campaign.lifecycle` | `archive-/delete-/unarchive-campaign`, `delete-cycle`, `cleanup-empty-cycles`, `set-allowed-models`, `set-campaign-label`, `replace-dataset` | destructive |
| `campaign.babysit` | a **direct edit** of an optimizer-owned / origin-locked value — wired to the `fork-cycle` axis-unlock (§4, SHIPPED) | privileged / provenance-tainting |
| `campaign.lookahead` | `set-sample-lookahead` (SHIPPED) — **its own rung, not a share of `babysit`**: it spends the BOX's shared provider rate bucket rather than the campaign's budget, which makes it the one power a host may withhold from a delegate while still granting the run. Not `babysit`, because it taints nothing — the overshoot sample is discarded and the recorded rows are identical at either depth. See [`../operations/access-model.md`](../operations/access-model.md) § host-admin ↔ user | `lookahead` |

The ladder is the point: a delegate with `campaign.step` but **not** `campaign.run` can
advance the search one bounded action at a time (each a small, checkable spend) but
cannot fire an autonomous loop.

Three deltas from the original strawman, all deliberate: `fork-cycle` sits at **run**, not
step — an operator fork mints *and launches* an autonomous continuation (the babysit grant
that gates *unlocking a locked axis in the fork seed* is a distinct future concern, §4);
`register-backend` folds into `campaign.create` rather than a separate `backend.register`
cap (one fewer capability; a delegate that may author campaigns may register the backend they
run against); and `replace-dataset` sits at **lifecycle**, not create, because a dataset
slug is part of the measurement cache key — repointing one re-addresses every campaign that
already measured against it, which is strictly stronger than authoring a new dataset.

**A route is the only way to add a verb, so the route set is what the ladder is checked
against.** `CAP_FOR_KIND`'s exhaustiveness raise can only see kinds that dispatch, so it
read as total while `replace-dataset` called `version_and_repoint` directly — gated by
nothing, recorded nowhere, and named as an open gap in this section for as long as it
lasted. `routers/commands.py` now raises at import when a typed route names a kind outside
`ALL_DISPATCHED_KINDS`, which is the assertion that makes the gap unwritable rather than
merely known.

### 4. Babysat — a lineage-subtree tag, escapable by forking clean

There is **no per-value unlock control**. A value the optimizer normally owns, or the
origin locks, is directly editable by a principal holding `campaign.babysit`. The edit is
*allowed in place* — it does not force a fork — but it triggers a warning and tags the
lineage **at the edit node**.

**Babysat is a lineage property, not a campaign-global flag.** The tag roots at the
human-edit node and **propagates to that node's subtree**: every descendant round is
babysat, because each is built on human-steered state. Rounds elsewhere in the tree stay
clean. A run is babysat-tainted iff its branch carries the tag (itself, or an ancestor up
to the branch's clean root).

**Escaping babysat is a fork decision.** Fork & steer from the last *clean* ancestor — the
node just **before** the edit — and choose whether to carry the edited (babysat)
information forward:

- **Exclude it** → the fork grows a fresh **un-babysat** branch from the clean point; its
  runs grade clean again.
- **Include it** → the new branch **inherits the babysat tag** — it is built on
  human-steered information, so it is honestly still babysat.

This *subsumes* the old "a comparability-breaking change MUST fork a clean sibling" rule
rather than contradicting it: editing in place is now allowed (its subtree is tagged), and
the clean sibling is simply the un-babysat fork you take from the pre-edit node when you
want clean provenance back. Both live on the one positional genealogy the loop and webapp
already read.

Babysat rides the existing provenance grade, **not** a new sidecar: a run in a babysat
subtree is stamped so it is **not** a `DELIBERATE_SOURCES` clean datapoint — `grade_run`
demotes it and the three existing consumers (the `AxisIndex` digest, `MeasurementArchive`
reuse, L4's graded-clean ingestion) exclude it exactly as they exclude an incidental `C`
run today. The clean-measurement contract is *protected*, not bypassed.

Warning copy is literal: "editing this marks this branch babysat — its runs won't count as
clean measurements; fork from before the edit to keep a clean branch."

**Minimal first slice — SHIPPED.** The smallest honest version is live: a fork seed whose
`pipeline_overlay` steers the inner-optimizer model OUTSIDE the origin's declared allow-list
(`CampaignConfig.allowed_models` — empty = nothing sanctioned = restrictive default;
`overlay_sets_model_outside_allowed`) requires `campaign.babysit` (checked in the `fork-cycle`
builder, above the `campaign.run` fork itself), stamps the cycle index babysat via
`mark_human_intervened` (`kind="disallowed_model_override"`), and forces every run that cycle
scores to grade **C** through a `human_intervened` argument to `grade_run` — so the three
existing consumers exclude it exactly as they exclude an incidental `C` run. A steer to a
SANCTIONED model (∈ `allowed_models`) is a clean human fork: no cap, no taint. The done C0 is
INHERITED either way (`try_inherit_fork_origin` accepts a model/provider-only overlay), never
re-measured — the fork gets the candidate's data, not a re-paid origin.
The trigger is the direct overlay edit against the origin's allow-list, not a policy flag:
`forbidden_axes_strict` was removed and `PARAM_FORBIDDEN_KEYS` is now an invariant (the
optimizer never searches model/provider), so "human sets the model" and "optimizer may search
it" are cleanly separate — only the former exists, and it is exactly what the babysit tag
records (when the model is outside what the origin sanctioned). The
cycle-level flag reaches the grade site through `Session.human_intervened` (read from the
index at init for resume, set at the runner seed seam on first run). The full model
above — subtree propagation down the lineage, and the fork-time *include-or-exclude the
babysat info* choice — remains the **target**, not this cut: the cycle-level flag is a valid
degenerate case of the subtree tag, underbuilt not contradicted. No edit-kind split: one
`campaign.babysit` capability covers every direct edit for now.

### 5. Per-grant spend ceiling — SHIPPED

Each grant carries a spend ceiling, enforced by the existing spend-cap probe (ADR-0003):
`admit_launch` reads a sub-principal's declaration down to `grant_ceiling` — the grant bounds what
it may DECLARE — and the host wallet then admits or refuses that declaration whole. The ceiling
comes from the identity claims the sub-principal carries; no new spend machinery, a narrower input
to the one that exists. (Per-*channel* ceilings await §2.)

**The grant is a bound, never a declaration, and both directions of that were wrong once.** A
launch declaring NOTHING declares the account's headroom bounded by the grant — composed the other
way the grant became the declaration, and a delegate whose headroom had fallen below its grant was
refused the last of its own allowance. And `clamp_budget_change` composes the grant only into an
arm the request SUPPLIED: folded into an absent one it wrote a ceiling the caller asked to leave
alone, which the `spend_cap` file merge then made stick for the rest of the run.

### 6. The bounded step verb — SHIPPED as `step-cycle`

`start-run` is all-or-nothing and `fork-cycle` mints a *sibling*, so there was no
discrete "advance N rounds in place" action. Rather than a new verb, this wired the
**already-declared-not-wired `step-cycle`** (`api-openapi.yaml`): resume the cycle
in place, run `rounds` clean rounds (payload, default 1), then auto-pause (the existing
`StopReason.PAUSED` — resumable, so the operator steps again). It reuses the resume
launcher wholesale plus one run-scoped field, `RunMode.stop_after_rounds`; no new runner
path. On the `campaign.step` rung, so a delegate holding step-but-not-run can advance
bounded work but cannot fire an autonomous loop. (While here, the router's three
kind-routing sets were de-duplicated — `get_args` off the dispatcher Literals, the SoT.)

## Consequences

**Positive.** Safe user-minted delegation; least-privilege by construction; every command
(and every direct edit) gains a real server-side gate, closing gap #2; the
own-AI / external-assistant distinction becomes a capability fact, not a special case;
privileged edits become *generic + honest* (direct edit → babysat) instead of N bespoke
unlock controls; babysat reuses existing provenance, so it costs no new
clean-measurement plumbing.

**Costs / negative.** Requires: a grant store (`(sub-principal[, channel]) → caps +
ceiling`); a `CAP_FOR_KIND` map + one dispatcher check; a UI to mint/manage sub-users; a
babysat provenance stamp threaded from the edit site to `grade_run`; the new `step-round`
verb. Attenuation checks add a step to every mint and every command. Channel
identification is deferred (not built now), so per-channel grants are unavailable until a
secure method exists — a *known, bounded* limitation, not a hidden one.

## Open questions

**Block the first (secure) slice:**

1. **Babysat encoding — RESOLVED + SHIPPED (minimal slice).** Reused the existing substrate
   (no new flag): `CampaignStore.mark_human_intervened` stamps the cycle index when the seed
   overlay steers the model outside the origin's `allowed_models` (`overlay_sets_model_outside_allowed`). Residual (a) — decided **cycle-level flag for the first cut**, true
   subtree propagation on the positional genealogy deferred. Residual (b) — decided **neither
   a magic `source` prefix nor a per-stamp index read**: `grade_run` gained an explicit
   `human_intervened: bool` argument that forces grade `C`, fed from `Session.human_intervened`
   (read from the index at init, set at the runner seed seam), passed through the one
   write path `build_dataset_run_data`. The three consumers already exclude `C`, so no consumer
   changed — the grade *is* the exclusion.
2. **Capability granularity — resolved for now.** One `campaign.babysit` capability
   covers every direct edit; no split by edit kind. (Splitting is a *future* refinement
   attenuation might want; the system works without it — do not build it now.)
3. **Where grants live + who edits them.** Decided in constraint (§1 sealed grant store):
   grants live in a store the sub-principal cannot write. Residual: *which* protected
   store — the identity zone (`.promptpotter/identity/`, alongside the blocklist) edited
   via an operator-admin channel (ADR-0004), or a tenant-scoped-but-owner-only surface for
   a user minting their own delegates? The write path must be one the delegate can't reach.
4. **`step-cycle` scope — RESOLVED + SHIPPED.** One full clean L1 round is the unit
   (same generate/score/escalation/PoBB machinery as an autonomous round, just bounded),
   `rounds` advances N of them (default 1), then auto-pause. Not sub-round (a candidate):
   the round is the atomic scored/selected unit, so a partial round has no clean stop.

**Deferred — do NOT block the first slice (driver: secure-only):**

5. **Channel identity.** How the server unspoofably knows a request arrived via "the
   external MCP" vs "the company PC" vs "the own-AI session." Until a secure method
   exists, only the single authenticated channel resolves and per-channel grants are not
   offered (§2). The load-bearing security question for *that* extension — parked, with
   the seam reserved.
6. **Is the own-AI truly full-cap, or itself attenuable?** Default: co-principal (full);
   a cautious user might bound even their own assistant. Ties to #5.
7. **Re-delegation depth.** Bounded (one level) or arbitrary (always-narrowing)?
8. **Interaction with ADR-0003 tenancy.** Is a sub-principal its own tenant, or a
   sub-identity within the delegator's tenant (sharing its `measurements/`)?
