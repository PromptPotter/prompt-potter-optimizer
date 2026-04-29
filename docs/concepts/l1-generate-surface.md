# L1's Prompt Surface

The prompt L1 sees each round is built from a fixed catalogue of sections. The catalogue is defined in code; nothing the optimizer does at runtime can drop a section from the catalogue. L2 controls which sections are visible and what their text says, but never the catalogue itself.

For the implementation, see [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md).

---

## What "surface" means here

When we say *L1's surface*, we mean every text block the optimizer renders into L1's meta-prompt before sending it to the LLM. Each text block has a name. The set of names is closed.

A name comes from one of two groups:

- **Sections** — text blocks L2 can toggle off or replace. Eight today: pipeline schema, failure analysis, axis digest, task context, escalation probe, escalation alert, L2 directive, plan.
- **Scalars** — factual values that always render. Four today: number of variants L1 must produce, current accuracy, number of queries scored, and the rendered prompt being optimized.

```
┌─ L1's prompt surface ────────────────────────────────────┐
│                                                          │
│  CATALOGUE (closed, defined in code):                    │
│   8 sections + 4 scalars                                 │
│                                                          │
│   ↓                                                      │
│                                                          │
│  PER-ROUND OVERRIDES (set by L2, lives on the individual)│
│   • visibility toggles  — "hide section X this round"    │
│   • text overrides      — "replace section X's text"     │
│   • whole-body override — "use this template body"       │
│                                                          │
│   ↓                                                      │
│                                                          │
│  RENDERED L1 PROMPT (what the LLM actually sees)         │
└──────────────────────────────────────────────────────────┘
```

## Why the catalogue matters

Two scenarios it prevents:

1. **Silent capability loss.** If a future automation (the L4 meta-learner) edits L1's prompt template and accidentally drops the `failure_analysis` section, the optimizer would forever lose that signal — no record that the section ever existed. The catalogue is code-authoritative: dropping a section requires deleting its enum entry, which is a deliberate code change reviewed at PR time.

2. **Drift between what L1 sees and what L2 thinks L1 sees.** Without a catalogue, L2 would be guessing what variables are in play. With it, L2's prompt always includes the menu — every section L1 has, with current state attached.

## L2's three levers over the surface

L2 mutates the surface by writing to the optimizer's state record (the *individual*). Three fields:

| Field | What it does | Example |
|-------|--------------|---------|
| Visibility toggles | Hide a section for the next round (and onward). | `{"escalation_alert": false}` |
| Text overrides | Replace a section's auto-generated text with hand-written content. | `{"task_context": "Domain is medical billing codes..."}` |
| Whole-body override | Replace the whole `problem_description` body with a custom template. | A reasoning-framed body when retrieval framing fails. |

These persist across rounds — until L2 (or L3) flips them again.

## What L2 sees about the surface

When L2 runs, its own prompt receives a **catalogue block**: one line per registry entry showing the current state.

```
L1-GENERATE FIELD CATALOGUE (the menu of variables L1 currently sees):
  [ON]  pipeline_schema_text — Target pipeline + active steps + per-node schema.
  [ON]  failure_analysis — Latest round's clustered failure patterns.
  [OFF] escalation_alert — Aggregated pipeline-issue alert (non-probe).
    override: 'Domain hint: medical billing codes.'
  ...
  [scalar] accuracy_pct — Current accuracy of the parent SearchPoint.
  ...
```

So L2 always knows what L1 is receiving and what knobs it has. There is no hidden state.

## See also

- [what-is-l2.md](what-is-l2.md) — the layer that owns this surface.
- [l2-decision-tree.md](l2-decision-tree.md) — when L2 mutates the surface vs. writes a directive vs. stays quiet.
- [optsearchpoint-as-state.md](optsearchpoint-as-state.md) — the record carrying the overrides.
- [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md) — the registry, dataclass, and compile path.
