# Fixture: a `campaign.json` frozen before today's `CampaignConfig`

A minted campaign manifest, pinned. It is **not** regenerated from the current models — that is
the whole point. `Campaign` and `CampaignConfig` are both `extra="forbid"`, so renaming or
deleting a field makes every campaign already on someone's disk raise `extra_forbidden` at load.
`resume`, `ab`, `verify`, `noise-floor` and L4's inner cycles all die there, before any scoring.

This has fired for real. Commit `5c0722a1` folded `deterministic_dominance` +
`equivalence_elimination` into `margin_elimination` and broke 50 of 177 local campaigns; nobody
noticed for days. A later sweep flattened the `optimization.exploration` sub-model and dropped
eight more knobs, breaking **156 of 169**. Both were found by hand, long after the fact.

Pre-release that costs a re-stamp of our own disk. A distributed `promptpotter-self` cannot
re-stamp a paying user's campaigns — those are measurements we don't own and can't rewrite. "No
backward compatibility" licenses breaking *code*, never a user's data.

**Shape that matters:**

- Every `Campaign` field is present, including the ones with defaults — a rename of any of them
  must fail here rather than on a user's disk.
- `config` is a **delta from the code defaults** (`freeze_campaign_config`), the shape the
  writers persist. A knob nobody set is absent, so renaming it is free.
- `config.optimization` carries **non-default** leaves (`max_rounds`, `n_variants`,
  `spend_budget_usd`, `token_budget`, `mechanisms.elimination.leader_lock_in`). These are the
  ones the delta *cannot* protect: the operator set them, so they are written down, so renaming
  one breaks this file. That is the case the fixture exists to catch.
- `pipeline_overrides` + `optimizer_narrowing` are the two fields the resume path actually reads
  back out of the snapshot (`apply_inherited_overlay`).

**When this test fails:** you renamed or removed a `CampaignConfig` / `Campaign` field. Do not
reach for `extra="allow"`, a field alias, or a migration shim — those are the forbidden shapes.
Ship the rename together with a re-stamp: `python -m promptpotter restamp --apply`,
and update this fixture in the same commit.

Identifiers (`campaign_id`, `root_cycle_id`) are deterministic placeholders, not anonymized real
values — the test asserts on load behaviour, not on identity.
