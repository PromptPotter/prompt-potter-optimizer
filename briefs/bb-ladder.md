# bb-ladder: move N0, the import ladder becomes a gate count (cloud · Opus)

`briefs/common.md` and `briefs/bb-shared.md` bind you. Spec: `briefs/backbone.md` § 4 row **N0** and § 2
row 1. The ladder itself is `briefs/backbone-topdown.md` § 0: the rank map (`shared < domain < config <
infrastructure/… < G < A0 … A8`), the "outside the backbone" set that ranks above A7, and its table of
rightward edges at the tip.

Root: `application/` states no import order, so packages import each other both ways, and a file on such a
cycle is edited by every chain on either side of it. Nothing counts this today, so a merge that cuts an
edge can silently grow back.

Build:
1. **One rank table** in `scripts/gate.py` beside `_LAYERING` (census first: what `_LAYERING` and the
   `layering` check already hold; extend them, never add a second mechanism). Every module under
   `promptpotter/` maps to one rank, by package or by file as topdown § 0 lists. A module the map does not
   place fails the check by name, so a new file must be placed.
2. **One count, `ladder_edges`,** in `scripts/complexity_ledger.py::BASELINE`. It counts each import edge
   (module-level, lazy or under `TYPE_CHECKING`) from a module to a module on a higher rank, plus every
   import of an "outside" module by ranks G–A7. Set it to the measured count at your base. Print the
   edges grouped by topdown § 0's table rows, so a later move sees which edges it cut. Doors (rank A8)
   importing "outside" modules are not counted, as § 0 says. **The member fence is one more rule of the
   same table,** exactly as `bb-shared.md` defines it: members sit at A8, and each import outside the
   fence's allowed set counts as an edge. bb-fence removes those edges in parallel, so measure them at your
   base.
3. **State the ladder** in `promptpotter/application/CLAUDE.md`: the ranks in one short block, and a pointer
   to the table as the source of truth. Fix any layer `CLAUDE.md` or doc that states a contrary order.
4. **Re-measure topdown § 0's claim**: its table says the ladder at the tip is broken only by the edges it
   lists. Report every edge your count finds that the table does not list, by file and line. A listed edge
   that does not exist is refuted.

Owns: `scripts/gate.py` (the layering check), `scripts/complexity_ledger.py` (the new count and its
BASELINE line), `promptpotter/application/CLAUDE.md`, any `CLAUDE.md` or doc whose stated order you
correct, and the tests of the layering check.

The size estimate is S (+120 lines in `scripts/`). `promptpotter/` lines should not move. The code-shrink
rule applies to `promptpotter/`; the gate's growth here is this move's substance.

Done when:
- `python scripts/gate.py --only layering` (or the check you extend) fails when an edge is added against
  the ladder and when a module is unplaced; show both with a scratch edit you revert.
- `ladder_edges` is set to the measured count, with its comment line in BASELINE.
- The full gate is green.
- The report lists the edges by § 0 row, including the ones § 0 missed.
