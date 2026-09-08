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

> **ACCEPTED.** §§1, 3, 4, 5, 6 shipped; §2 (channel-scoping) and §4's babysat *subtree* model remain proposal. Each section below carries its own status.

## Context and Problem Statement

PromptPotter has exactly two principal shapes: a **registered user** (an `IdentityContext` with tenant-scoped capabilities) and a **host admin** (the same, whose powers ride the ADR-0004 operator-admin channel rather than the API — there is no admin-only capability). That is enough for "one operator runs their own campaigns" and nothing more.

A user now wants to **delegate**: mint sub-users each holding a chosen slice of their own rights, give each a spend ceiling, and vary those rights **by channel** — the same sub-user reaching in from an AI assistant gets one permission set, from the company PC another.

This generalizes a decision hit while building the L4 inner-optimizer model-unlock. We first framed the guard as "only a *human* may unlock it," and that is the wrong axis: the principal doing a privileged action is not human-vs-machine, it is an identity carrying or lacking a **capability**. The user's own AI acting on their behalf should carry the user's rights; an external assistant reaching in over MCP should carry a strictly smaller subset.

A second simplification followed. Do **not** build a *specific* unlock control per privileged value. Any such value is directly editable, and the act of editing one the optimizer normally owns is what carries the consequence: a warning, and a **babysat** tag on the remainder of that lineage branch. Babysat is not a new flag — it rides the existing measurement-provenance grade (`domain/measurement_provenance.py`), which already separates deliberate clean optimizer exploration (`A`) from incidental runs (`C`) and already keeps the latter out of the cross-cycle digest, out of reuse, and out of L4's graded-clean ingestion. **One generic mechanism — direct edit → warn → babysat — not N per-action toggles.**

Two gaps made this urgent. The identity model stops at user/admin, with no sub-principals, delegation, channel scoping or per-principal budget — **that half remains open**. The command highway applying privileged fields with no per-verb capability check is **closed** (§3).

## Decision Drivers

* **Attenuation, never escalation.** A delegate's authority is always a *subset* of the delegator's, and re-delegation can only narrow. The classic capability-security property, and the only safe default for user-minted sub-principals.
* **Defense in depth — the server is the boundary.** A client MAY reflect a capability by showing or hiding a control; it MUST NOT be the enforcement.
* **Least privilege + graduated actions.** Removing a high capability must drop the principal to a **more bounded** action — run a campaign becomes step one round at a time — not to nothing. Privilege is a ladder over the verbs, not a switch.
* **Build on what exists.** `IdentityContext.capabilities`, the closed command-verb set, the ADR-0003 spend-cap machinery and `measurement_provenance.py` are the primitives; this ADR composes them.
* **Secure methods only, now.** Design for channels, but ship only capabilities whose enforcement is secure. Anything depending on spoofable channel identification is designed-for and **not exposed to users** until a secure method exists.

## Decision

### 1. Principals & sub-principals, with an attenuation invariant — SHIPPED

A user may mint **sub-principals**, each an `IdentityContext` whose capabilities and spend ceiling the delegator chooses, subject to the invariant:

> **Attenuation:** a delegate's capability set ⊆ the delegator's, and its spend ceiling ≤ the delegator's remaining ceiling. Enforced at grant time and re-checked at use. Re-delegation only narrows further.

**The grant store is sealed** — a sub-principal's grants live in a store it **cannot write**, the identity zone rather than the tenant's own editable space, because a delegate that could edit its own grant would self-escalate and defeat attenuation. Same protected-zone rule as ADR-0004's sign-in blocklist: the file that decides authority is the most protected file in the install.

As shipped (`infrastructure/identity/grants.py`, `.promptpotter/identity/grants.json`): a delegate authenticates via its own OIDC identity, `_identity_context_from_session` resolves its grant and rebinds it to act inside the delegator's tenant with `grant ∩ owner` capabilities — attenuation enforced at read, defense in depth — audited as itself (`claims["principal"]` → `issued_by_user_id`). A malformed or delegator-less grant fails secure: own tenant, no caps. Provisioned through the operator-admin channel (`admin_bot.py`: `/grant`, `/revoke`, `/grants`), the identity zone a delegate cannot write. One-level delegation is enforced at the grant writer, which rejects a delegator that is itself a sub-principal.

The user's **own AI assistant**, in the host==user case, acts under the user's identity as a co-principal with full caps. An **external assistant or MCP client** is a distinct sub-principal holding an attenuated subset.

### 2. Channel-scoped grants — designed-for, not exposed yet

A grant is keyed on **(sub-principal, channel)** rather than on the principal alone, so the effective authority of a request is `grant(principal, channel)`.

**Not shipped to users, on the secure-only driver.** Channel-scoping is only as sound as channel *identification* is unspoofable, and that is unsolved. The data model reserves the `channel` key and the grant lookup takes it, but only a **single trusted channel** resolves — the authenticated session — and no user-facing configuration exposes per-channel grants. The seam is present so the secure version slots in without reshaping the model; the insecure version is never offered.

### 3. Capability → verb ladder (one enforcement seam) — SHIPPED

Every control-plane verb requires a capability, checked in **one place**: the command dispatcher tests `has_capability(identity, CAP_FOR_KIND[kind])` at the single `_record_and_apply` chokepoint every dispatch method funnels through. An import-time exhaustiveness assert derives the closed kind set from the `*Kind` `Literal`s so the map cannot drift. Seven capabilities are enumerated once as `CAMPAIGN_CAP_BY_NAME` in `shared/identity.py`, and every first-class principal holds the full owner set, so the gate is a no-op for single-owner installs. Denial is **404**, not 403 — existence-hiding.

| Cap | Gates (real command kinds) | Kind |
|---|---|---|
| `campaign.step` | `skip-searchpoint`, `pause-cycle`, `origin-gate-decision`, `step-cycle` | stepwise / bounded |
| `campaign.run` | `start-run`, `fork-cycle`, `start-checkin` | autonomous |
| `campaign.create` | `mint-campaign`, `register-backend`, `edit-draft-campaign`, `resolve-origin` | create |
| `campaign.budget` | `change-spend-budget` (raise a ceiling) | budget |
| `campaign.lifecycle` | `archive-/delete-/unarchive-campaign`, `delete-cycle`, `cleanup-empty-cycles`, `set-campaign-label`, `replace-dataset` | destructive |
| `campaign.babysit` | a **direct edit** of an optimizer-owned / origin-locked value — wired to the `fork-cycle` axis-unlock (§4) | privileged / provenance-tainting |
| `campaign.lookahead` | `set-sample-lookahead` — **its own rung, not a share of `babysit`**: it spends the BOX's shared provider rate bucket rather than the campaign's budget, which makes it the one power a host may withhold from a delegate while still granting the run. Not `babysit`, because it taints nothing. See [`../operations/access-model.md`](../operations/access-model.md) § host-admin ↔ user | `lookahead` |

The ladder is the point: a delegate with `campaign.step` but **not** `campaign.run` can advance the search one bounded action at a time, each a small checkable spend, but cannot fire an autonomous loop.

Three deltas from the original strawman, all deliberate. `fork-cycle` sits at **run**, not step, because an operator fork mints *and launches* an autonomous continuation. `register-backend` folds into `campaign.create` rather than earning its own cap — a delegate that may author campaigns may register the backend they run against. And `replace-dataset` sits at **lifecycle**, not create, because a dataset slug is part of the measurement cache key, so repointing one re-addresses every campaign that already measured against it.

**A route is the only way to add a verb, so the route set is what the ladder is checked against.** `CAP_FOR_KIND`'s exhaustiveness raise can only see kinds that dispatch, so it read as total while `replace-dataset` called `version_and_repoint` directly — gated by nothing and recorded nowhere. `routers/commands.py` now raises at import when a typed route names a kind outside `ALL_DISPATCHED_KINDS`, which is what makes the gap unwritable rather than merely known.

### 4. Babysat — a lineage-subtree tag, escapable by forking clean

There is **no per-value unlock control.** A value the optimizer normally owns, or the origin locks, is directly editable by a principal holding `campaign.babysit`. The edit is allowed in place — it does not force a fork — but it triggers a warning and tags the lineage at the edit node.

**Babysat is a lineage property, not a campaign-global flag.** The tag roots at the human-edit node and propagates to that node's subtree, because every descendant round is built on human-steered state; rounds elsewhere in the tree stay clean.

**Escaping babysat is a fork decision.** Fork and steer from the last *clean* ancestor — the node just before the edit — and choose whether to carry the edited information forward. Exclude it and the fork grows a fresh un-babysat branch whose runs grade clean again; include it and the new branch honestly inherits the tag. This *subsumes* the old "a comparability-breaking change MUST fork a clean sibling" rule rather than contradicting it.

Babysat rides the existing provenance grade, **not** a new sidecar: `grade_run` demotes a run in a babysat subtree, and the three existing consumers — the `AxisIndex` digest, `MeasurementArchive` reuse, L4's graded-clean ingestion — exclude it exactly as they exclude an incidental `C` run today. Warning copy is literal: "editing this marks this branch babysat — its runs won't count as clean measurements; fork from before the edit to keep a clean branch."

**The minimal first slice is SHIPPED, and it is a cycle-level flag rather than the subtree.** A fork seed whose `pipeline_overlay` steers the inner-optimizer model OUTSIDE what that NODE permits (`optimizer_narrowing[node].param_allowed_values["model"]`; nothing declared = nothing sanctioned = restrictive default, via `overlay_sets_model_outside_allowed`) requires `campaign.babysit`, checked in the `fork-cycle` builder above the `campaign.run` fork itself. It stamps the cycle index babysat via `mark_human_intervened` and forces every run that cycle scores to grade **C**, through an explicit `human_intervened: bool` argument on `grade_run` fed from `Session.human_intervened` — read from the index at init, set at the runner seed seam, passed through the one write path `build_dataset_run_data`. No consumer changed, because the grade *is* the exclusion.

A steer to a PERMITTED model is a clean human fork: no cap, no taint. The done C0 is INHERITED either way (`try_inherit_fork_origin` accepts an overlay touching only `WHO_ANSWERS_KEYS`), never re-measured. The trigger is the direct overlay edit against what the node permits, not a policy flag.

**AMENDED — the permitted set is now ONE field, and `model` is a real search axis.** `CampaignConfig.allowed_models` is deleted, along with `/commands/set-allowed-models`, its CLI verb and its dashboard panel; `param_allowed_values["model"]` answers both questions this ADR kept apart, because with `model` out of `PARAM_FORBIDDEN_KEYS` they stopped being two. "Human sets the model" and "optimizer may search it" are no longer cleanly separate: whether the axis is open is that node's `optimizer.param_keys` answer, per dataset. **The confound guard goes with it** — a round-over-round comparison is no longer model-held, and a dataset that needs one closes the axis itself (`justlogic-d234`, `bbeh` do). `provider` and `route_order` stay forbidden engine-wide, and the babysit trigger and grade-C behaviour are unchanged; only the field they read moved. The terminal half of a widening is `resume --steer-model`, whose fork now DECLARES its own permitted set on the cycle seed — a fork act rather than an in-place manifest edit, which is why the verb could go. **Subtree propagation and the fork-time include-or-exclude choice remain the target**; the cycle-level flag is a valid degenerate case of it, underbuilt rather than contradicted.

### 5. Per-grant spend ceiling — SHIPPED

Each grant carries a spend ceiling enforced by the existing spend-cap probe (ADR-0003): `admit_launch` reads a sub-principal's declaration down to `grant_ceiling`, and the host wallet then admits or refuses that declaration whole. The ceiling comes from the identity claims the sub-principal carries — no new spend machinery, a narrower input to the one that exists. Per-*channel* ceilings await §2.

**The grant is a bound, never a declaration, and both directions of that were wrong once.** A launch declaring NOTHING declares the account's headroom bounded by the grant; composed the other way the grant became the declaration, and a delegate whose headroom had fallen below its grant was refused the last of its own allowance. And `clamp_budget_change` composes the grant only into an arm the request SUPPLIED — folded into an absent one it wrote a ceiling the caller asked to leave alone, which the `spend_cap` file merge then made stick for the rest of the run.

### 6. The bounded step verb — SHIPPED as `step-cycle`

`start-run` is all-or-nothing and `fork-cycle` mints a *sibling*, so there was no discrete "advance N rounds in place" action. Rather than a new verb, this wired the already-declared-not-wired `step-cycle`: resume the cycle in place, run `rounds` clean rounds (default 1), then auto-pause on the existing `StopReason.PAUSED`, resumable so the operator steps again. It reuses the resume launcher wholesale plus one run-scoped field, `RunMode.stop_after_rounds`; no new runner path. On the `campaign.step` rung, so a delegate holding step-but-not-run can advance bounded work without firing an autonomous loop.

One full clean L1 round is the unit — the same generate/score/escalation/PoBB machinery as an autonomous round, just bounded. Not sub-round: the round is the atomic scored and selected unit, so a partial round has no clean stop.

## Consequences

**Positive.** Safe user-minted delegation, least privilege by construction, and a real server-side gate on every command and every direct edit. The own-AI / external-assistant distinction becomes a capability fact rather than a special case, privileged edits become generic and honest instead of N bespoke unlock controls, and babysat reuses existing provenance so it costs no new clean-measurement plumbing.

**Costs.** Attenuation checks add a step to every mint and every command. Channel identification is deferred, so per-channel grants are unavailable until a secure method exists — a known, bounded limitation rather than a hidden one.

## Open questions

**Resolved but worth naming:** capability granularity stays coarse — one `campaign.babysit` covers every direct edit, with no split by edit kind. Splitting is a future refinement attenuation might want; the system works without it, so do not build it now.

**Open:**

1. **Which protected store holds grants, and who edits them.** §1 settles that a sub-principal cannot write its own; the residual is whether that is the identity zone edited via an operator-admin channel, or a tenant-scoped-but-owner-only surface for a user minting their own delegates. The write path must be one the delegate cannot reach.
2. **Channel identity.** How the server unspoofably knows a request arrived via the external MCP vs the company PC vs the own-AI session. The load-bearing security question for §2, parked with the seam reserved.
3. **Is the own-AI truly full-cap, or itself attenuable?** Default is co-principal with full caps; a cautious user might bound even their own assistant. Ties to the question above.
4. **Re-delegation depth** — bounded at one level as shipped, or arbitrary and always-narrowing?
5. **Interaction with ADR-0003 tenancy.** Is a sub-principal its own tenant, or a sub-identity within the delegator's tenant, sharing its `measurements/`?
