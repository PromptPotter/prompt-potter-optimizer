# Prompt fields — a roster, not a constant

**Status: the roster landed; the operator-authored order did not.** The operator signs off on
§ The decision before code moves.

## What is true today

A target prompt has no class. Each prompt node declares its fields IN RENDER ORDER
(`domain/pipeline_schema.py::NodePromptInfo.fields`, defaulting to
`domain/search_point.py::DEFAULT_PROMPT_FIELDS`), and the individual holds per prompt node a
`NodePrompt` — its fields by declared name plus its shots. The shape is owned by
[`../../promptpotter/domain/CLAUDE.md`](../../promptpotter/domain/CLAUDE.md) (the
`OptimizerPromptTemplate` and `OptSearchPoint` rows). So the SET is the node's, and so is the
ORDER — declared once, by whoever declares the node.

The optimizer's own prompt keeps a class, `OptimizerPromptTemplate`, whose `RENDER_ORDER` ends at
`application/optimizers/potter/dispatch/layout.py::VOLATILE_SLOT` — why is [`dispatch-hub.md`](../developer/dispatch-hub.md)
§ L1 layout's. It is not the operator's to permute.

## Reordering re-cuts the archive key — and that is correct

The rendered prompt is the joined, ORDERED text, and it sits inside every cell key the node
renders. So:

- Reordering a node's fields yields a different `sp_hash` / `prompt_fields_id`, and **every cached
  measurement under the old order misses.** That is honest — a different string went to the
  model — but on an established campaign it means re-paying for the whole origin.
- It follows that **order is a searchpoint property, not a display preference.** It cannot live in
  `view-memory` or `localStorage`; it belongs beside the fields it orders and travels with a fork.
- It also follows that reorder is an *edit*, subject to the same rule as any other: the honest
  render of a descendant reading under a changed order is `?`, never a recomputed number
  (`webapp/CLAUDE.md` § Scoring authority, "A number kept alive under a changed setup").

## The cost a roster carries

A field's cost depends on the CHANNEL it travels. Every open field is a property of the L1
response schema, and the schema is prompt text (`<simplify-the-problem>`): a roster that grows is
input tokens on every L1 call, every round. On an `artifact_body` connector the rendered prompt is
a file the agent must open, so growing it costs nothing until it is opened and buys nothing if it
never is.

## The decision

**Order authored by the operator.** Today the order is the node's declaration; a campaign cannot
reorder without re-declaring the node. The proposal: the check-in carries an order per prompt node
beside its fields (`DraftCampaign.origin_prompts`), the editor drags it, `edit-draft-campaign`
persists it, and a fork inherits it with the fields.

- **Open, and the operator decides:** is order an *optimizer axis* (L1 may permute it, like
  `L1Layout`) or an *operator setting* (authored once, held)? Held is the smaller change and the
  honest starting point — permuting it is a search-space widening that should be measured on its
  own, not smuggled in with the surface.

## The drag surface

- **No dependency.** Pointer Events (`pointerdown` / `setPointerCapture` / `pointermove`) cover
  mouse, pen and touch in one handler.
- **A new `components/ui/` primitive**, with an RTL test — `webapp/CLAUDE.md` forbids hand-rolling
  a second one of anything, and a reorderable list is a thing several surfaces will want.
- **Keyboard parity is not optional.** A handle takes `↑`/`↓` (or `Alt+↑`/`Alt+↓`) with an
  `aria-live` announcement of the new position.
- **Commit on drop, not per move** — the same discipline `ui/CommitInput` owns.
- Mobile: the handle is the drag target, not the box — a textarea that drags on touch cannot be
  scrolled or selected in.

## What this does NOT change

- **Few-shot shots are a locus of their own** (`node.shots`), never a field of the roster: a list
  of sample keys on a string grid is the wrong shape. They render into the prompt the archive key
  hashes, so a shot edit re-keys a cell like any other edit.
- **`plan` is not the target prompt's.** It is potter's L3 frame and rides potter's
  `optimizer_state`.
- The `prompt` key written into `pipeline_params` stays a RENDER, never storage —
  `strip_rendered_prompt` remains the sole writer of that strip.
