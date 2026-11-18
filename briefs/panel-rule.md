# panel-rule: a panel member folds by the rule its connector declares (cloud)

`briefs/common.md` binds you. Base: `origin/main` (2975f7225 or later). The row is in
`docs/specs/code-debt-cleanup.md`: **A panel's `inner_campaign` folds family→member by key, not by
`CAMPAIGN_RULE`** (cite-fold's report). Quote its title in the commit that closes it and delete it.

## Why now
The next L4 studies add panel members under `research/datasets/promptpotter-self/` that change an
inner node's `model` or `optimizer` (seat screens, then a mid-band panel). Under today's untyped
every-mapping-by-key rule such a member silently keeps the family's `route_order` or `nodes`, so the
inner campaign it runs is not the one its yaml reads as.

## Links
1. **The rule, named once**: the untyped every-mapping-by-key fold is spelled twice
   (`application/datasets/prompts.py:88`, `infrastructure/store/dataset_access.py`). Action:
   `domain/layer_fold.py` names it once and both read it. **Re-test:** `git grep` finds one spelling.
2. **The panel folds by its declared rule**: `runner/inner/tasks.py::InnerTasks` types
   `inner_campaign` so the connector declares the rule its panel folds by (a campaign block's own
   `CAMPAIGN_RULE`, which drops or clears `route_order` / `nodes` where a member changes `model` or
   `optimizer`), and `dataset_access.py::read_dataset_definition` folds by it. `source |= …`
   (`dataset_access.py:230`) is the same one-level merge: fold it the same way or say why not.
   **Re-test:** a member changing `l1_generate`'s `model` under a family that sets `route_order`
   resolves without that `route_order`; every member under `research/datasets/promptpotter-self/`
   (dev, dyck, e2e, run, wide, and any other) resolves byte-equal to today unless it relied on the
   wrong rule: name each that moves and why.

## Owns
`domain/layer_fold.py`, `infrastructure/store/dataset_access.py`, `application/datasets/prompts.py`,
`runner/inner/tasks.py` (the `InnerTasks` type only), the promptpotter-self member yamls if one must
restate, their tests. NOT `runner/inner/spawn.py`, `runner/termination.py`, `domain/phases.py`
(the `l4-words` chain), NOT `infrastructure/llm/spend_book.py`, `domain/spend.py` (the
`spend-served` chain). A file outside Owns that the root needs: report it, do not edit it.

## Target
Net ≤ 0 `promptpotter/` lines. Run the `--against` legs; name every leg and digest that moves,
old→new (a resolved panel that moves re-keys its cells: name it).

## Report
Per `common.md`.
