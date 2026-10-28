# docs/ — index for agent readers

The operator-facing index is [`README.md`](README.md). Read [`architecture.md`](architecture.md) §0 + §0.5 first; load only the subtree you need. A question rather than a task: root [`../CLAUDE.md`](../CLAUDE.md) § First.

**L4 has two owners**: [`specs/l4-outer-loop.md`](specs/l4-outer-loop.md) says what is TRUE, `.claude/skills/potter-self/` says what to DO. No other file holds L4's status or open list.

## What may live in `specs/`

**Direction of travel only.** A fact about shipped behavior belongs in `concepts/`, `developer/` or `operations/`; a fully shipped spec is deleted (`git log` keeps it). Why a boundary sits where it does lives in [`decisions.md`](decisions.md), which is never deleted on shipping.

## Editing a doc

**One fact, one owner.** The doc governing the artifact a rule constrains owns it; elsewhere write at most an **obligation line** — the rule's name in bold, "owned by" + link, one clause of what THIS layer must do.

**The Recompute Test — docs hold rules, not state.** A line a reader could recompute differently later is state: name the symbol, the enumerator or the counting command instead. A line not recomputable at all is a decision, and belongs. A war story keeps only its decision; the narrative is git's.

Write instead of: line numbers → `<file>.py::<symbol>` · a count of a code-owned set → the enumerator · a status word → a link to the one status owner · a count of on-disk data → the command · `§ Name` → a string matching exactly one heading in the linked file.

**A one-X / no-second-Y claim carries its probe** — `<!-- probe: git grep -cE "<pattern>" -- <path> == 0 -->` (or `<=` a ratchet), run by `gate.py --only doc-claims`; a claim the code does not keep yet goes in `gate.py::_KNOWN_FALSE`. A claim no pattern can check is deleted. Point the path at source files (`promptpotter/*.py`), so the probe's own comment never counts itself.

**Code defines behavior; where a doc disagrees, the doc is fixed** — except [`architecture.md`](architecture.md) §0 / §0.5, a contract the code is fixed to.
