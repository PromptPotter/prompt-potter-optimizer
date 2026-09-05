# Prompt fields — a roster, not a constant

**Status: proposed, nothing built.** The operator signs off on § The decision before code moves.

The starting prompt is authored as six named boxes. Two things about them are hard-coded that
the design never meant to be: **which fields exist**, and **what order they render in**. This
spec says where each is pinned today, what it costs to unpin, and which half is worth buying.

## What is true today

The SET is a module constant and the ORDER is a class variable, and a check marries them:

- `config/settings.py::PROMPT_STRING_FIELDS` — six keys, module-level, `list[str]`.
- `domain/opt_search_point.py::PromptTemplate.RENDER_ORDER` — a `ClassVar[tuple[str, ...]]`.
  `render_fields()` walks it; `render()` joins the result with blank lines.
- `_check_render_order`, fired from `PromptTemplate.__init_subclass__`, raises if
  `sorted(RENDER_ORDER) != sorted(PROMPT_STRING_FIELDS)` — "a field the order omits renders
  nowhere — silently, in a prompt."

**The order is already per-class, and there are already two of them.** `PromptTemplate`'s is the
OPTIMIZER prompt's and ends at `l1_layout.py::VOLATILE_SLOT` — why, and what a layout may put ahead
of it, is owned by that constant and by `RENDER_ORDER`'s own docstring; do not restate it here.
`OptSearchPoint` restates its own — `PROMPT_STRING_FIELDS` order — for the TARGET prompt, because
that render is inside the measurement archive's key and moving it re-cuts every banked cell. So the
seam this spec asks for **exists for the order and is load-bearing**; what it does not have is a
per-CAMPAIGN value, only a per-class one. The set has no seam at all.

**The target prompt is ordered by `OptSearchPoint.RENDER_ORDER`.** `to_job_search_point` calls
`self.render()` and writes the result to `pipeline_params[prompt_node]["prompt"]`. So a reorder
that stays in the browser would change nothing the backend sends — the editor would show one order
and the model would read another. **Order has to reach the engine or it is decoration.** And it is
the OptSearchPoint order that a campaign-level layout would replace; the optimizer's own is not the
operator's to permute.

`JobSearchPoint.prompt_fields`, by contrast, is already `dict[str, Any]` — the wire to the
connector places no constraint on the field set at all. Everything that pins the six sits
upstream of it.

## What the set is pinned by

Seven readers, and they are not equally hard:

| Reader | What it does with the list | Cost of a dynamic roster |
|---|---|---|
| `dispatch/l1_wire_schema.py` (~L197) | builds the L1 response JSON Schema — `{field: {"type": "string"}}` for every key | **The expensive one.** The schema is prompt text (`<simplify-the-problem>`), so every added field is paid on every L1 call, every round, forever. |
| `application/datasets/origin_resolve.py` (~L293) | the check-in decomposition asks the LLM for exactly these keys | roster-driven; same token argument |
| `optimization/validators/l1_behavior.py`, `l1_invariants.py` | diff parent vs child over the set | mechanical — iterate the roster |
| `opt_search_point.py::mutate`, `prompt_fields`, `prompt_field_dict` | `getattr(self, f)` per field | **structural** — the six are Pydantic *attributes*. A dynamic set means a dict field, and `StrictModel` forbids extras. |
| `optimization/cycle.py` (~L540) | `setattr(opt_sp, f, …)` per field on resume | structural, with `mutate` |
| `dispatch/injections/panels.py` (~L252) | `prompt_axes` — which axes a panel may name | mechanical |
| `webapp/lib/prompt-fields.ts` | the TS half of the seam: LABELS only | **already generated, so nearly free.** The SET is emitted by `build_ts_types.py::_emit_prompt_string_fields` and re-exported from `types.generated.ts`; this file keeps the on-screen labels, which have no Python counterpart. A roster reaches the browser by regeneration, and only the label table needs a home. |

The structural one is the real gate: `persona: str = ""` and friends are model fields, and every
`getattr(sp, "persona")` in the tree assumes they exist. A roster turns them into
`fields: dict[str, str]`, and that is a rename touching every prompt-shaped read in the engine.

## Reordering re-cuts the archive key — and that is correct

`shared/hashing.py::content_hash` hashes `{"prompt": rendered_prompt, "pairs": …, "pipeline_params": …}`.
The rendered prompt is the joined, ORDERED text — under `OptSearchPoint.RENDER_ORDER`, which is
restated rather than inherited for exactly this reason. So:

- Reordering an origin's fields yields a different `sp_hash` / `prompt_fields_id`, and **every
  cached measurement under the old order misses.** That is honest — a different string went to
  the model — but on an established campaign it means re-paying for the whole origin.
- It follows that **order is a searchpoint property, not a display preference.** It cannot live in
  `view-memory` or `localStorage`; it belongs beside the fields it orders and travels with a fork.
- It also follows that reorder is an *edit*, subject to the same rule as any other: the honest
  render of a descendant reading under a changed order is `?`, never a recomputed number
  (`webapp/CLAUDE.md` § Scoring authority, "A number kept alive under a changed setup").

## The decision

Two independent halves. They can ship in either order; the order half is far cheaper.

### A — order (recommended first)

Move the order off the ClassVar and onto the searchpoint, as the target-prompt twin of the lever
the optimizer already has for its own prompt:

```
domain/l1_layout.py::L1Layout        # exists — L2-authored slot order for the OPTIMIZER prompt
domain/prompt_layout.py::PromptLayout  # proposed — the same idea for the TARGET prompt
```

- `PromptTemplate.render_fields()` walks the instance's layout, falling back to the CLASS's
  `RENDER_ORDER` when unset — which keeps the optimizer prompt's cache-shaped order intact while
  letting a target prompt carry its own. `_check_render_order` becomes a *validator*: a layout
  whose set differs from the roster is rejected at construction, where the message can name the
  offending campaign instead of killing the process.
- It rides `prompt_field_dict()` / `from_prompt_fields`, so a fork and a seed inherit it for free.
- The draft carries it (`DraftCampaign`), the editor drags it, `edit-draft-campaign` persists it.
- **Open, and the operator decides:** is layout an *optimizer axis* (L1 may permute it, like
  `L1Layout`) or an *operator setting* (authored once, held)? Held is the smaller change and the
  honest starting point — permuting it is a search-space widening that should be measured on its
  own, not smuggled in with the surface.

### B — extensible set

A campaign-scoped roster replacing the module constant:

```
PromptFieldRoster = list[PromptField]   # {key, label, hint, required}
```

- Declared where the dataset is declared (`datasets/{name}/`), defaulted to the six, resolved at
  mint into `CampaignConfig`, and served beside `origin_prompt_fields`.
- `PromptTemplate` loses its six attributes for one `fields: dict[str, str]`. Every `getattr`
  becomes a lookup. `PROMPT_STRING_FIELDS` survives only as the DEFAULT roster.
- `l1_wire_schema` builds its `properties` from the campaign's roster. **This is where the cost
  lands**: the response schema is input tokens on every L1 call — the second prompt nobody counts
  (`CLAUDE.md` § Working principles, `<simplify-the-problem>`: "count the response JSON Schema: it
  is prompt text"). A roster that grows is a prompt that grows, on every round of every campaign.
- Cross-campaign comparison degrades gracefully — the Compare tab already reports a key one side
  lacks as `oneSided` rather than as a disagreement (`components/compare/SearchpointPanels.tsx`).

## The drag surface

Whatever ships, the interaction is the same and it is small:

- **No dependency.** `CLAUDE.md` § Conventions is explicit — fewest dependencies possible. Pointer
  Events (`pointerdown` / `setPointerCapture` / `pointermove`) cover mouse, pen and touch in one
  handler; a drag-and-drop library is a package for a `.map()` and a transform.
- **A new `components/ui/` primitive**, with an RTL test — `webapp/CLAUDE.md` forbids hand-rolling
  a second one of anything, and a reorderable list is a thing several surfaces will want.
- **Keyboard parity is not optional.** `BRAND.md`'s accessibility floor covers painted surfaces;
  a handle takes `↑`/`↓` (or `Alt+↑`/`Alt+↓`) with an `aria-live` announcement of the new position.
- **Commit on drop, not per move** — the same discipline `ui/CommitInput` owns. One
  `edit-draft-campaign` per drop, never one per `pointermove`.
- Mobile: the handle is the drag target, not the box — a textarea that drags on touch cannot be
  scrolled or selected in.
- The wire type is **generated** (`scripts/build_ts_types.py::EXPORTED_MODELS`), never hand-declared
  in `lib/api/types.ts`.

## What this does NOT change

- `few_shot_examples` and `plan` stay outside the roster. They render on their own rules
  (`_render_few_shot_block`; `plan` renders nowhere and rides `prompt_field_dict` so a fork
  inherits the L3 frame). Folding them in would put a list and a prose block on a string grid.
- The `prompt` key written into `pipeline_params` stays a RENDER, never storage —
  `strip_rendered_prompt` remains the sole writer of that strip.

## Open

1. A (order) alone, or A then B? — the recommendation above is A first.
2. Layout as an operator setting, or an optimizer axis? — held, until measured.
3. Where a roster is declared if B lands: `datasets/{name}/pipeline.yaml` beside the node config,
   or its own file? The former rides an existing channel; the latter is a sidecar the pre-flight
   gate would reject.
