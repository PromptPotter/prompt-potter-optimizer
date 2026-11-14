# webb-5b: the output contract served flat; a lift's name served; the gate boots on any python3 (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `webb-5b`, arc `arc/webb-5b`.

- **Base:** `origin/main` at `b55125d2a`: main with narr-2 and small-2 landed. Your `<base sha>` in
  common.md is this commit.
- **The code shrinks**, judged as common.md says: two browser derivations go for served fields. Run
  `git diff --shortstat <base> HEAD -- promptpotter webapp scripts ':!webapp/lib/api/types.generated.ts' ':!docs/specs/openapi.generated.json'`
  after each link.
- **Spec:** `### webb-5`, these rows only: "The output contract is flattened in the browser", "A lift's
  name is composed twice", "The gate's bootstrap assumes the pinned interpreter". Re-run each Re-test
  first. **Not yours:** "Adapters over a flat served shape" (125 `.facts.` sites: re-point its
  **Rides with:** to webd, whose served-facts pass it is).
- **Webapp rules:** `webapp/CLAUDE.md`, `webapp/components/CLAUDE.md`, `webapp/lib/CLAUDE.md` bind you.
  Mobile first: the phone widths 375, 393 and 412.

## Decisions (orchestrator; build them, do not reopen them)

- **The output contract is served flat.** `domain/pipeline_schema.py::NodeOutputSchema` serves the
  contract's rows (path, type, required, description: whatever `output-contract.ts::outputContract`
  derives today), flattened server-side at the depth the schema has, not a fixed `MAX_DEPTH`.
  `node-surface/NodeSurface.tsx` draws the served rows; `lib/derivations/output-contract.ts` and its
  tests go. Census first: every importer of `outputContract`.
- **A reading serves its lift's name.** `paired_reading.py::lift_versus` already composes "vs {over}
  {words}"; the round read's reading (`RoundView.candidates[].reading.vs_reference`) serves that
  name, and `components/files/RoundFileView.tsx` and `searchpoint/SearchpointDrillIn.tsx` read it.
  `paired-reading.ts::liftVersus` goes, and so does `RoundFileView`'s fixed "on the cells both
  measured" hint: the column's name comes from each served reading, never the first arm's cell set.
- **The gate boots on any `python3` the hook names.** The code before `scripts/gate.py`'s re-exec
  (`_editable_target`'s `Path.from_uri`, 3.13+) runs on 3.11, or the gate refuses first, naming the
  floor. Pick the smaller; a re-exec that happens before any 3.13 call is the root.

## Links (one commit each, in order)

1. The output contract served flat.
2. The lift's served name; the gate's bootstrap.

## Beside you

Name every shared file in its link's commit body.

- **faults-1** (cloud, base dce5b7883) gives refusals their acts and adds a gate rule to
  `scripts/gate.py`, and deletes ~45 log lines across `application/`. If you meet its hunks in
  `scripts/gate.py`, keep yours to the bootstrap. The wire is regenerated (`types.generated.ts`,
  `openapi.generated.json`) through its scripts, never by hand: a conflict there is resolved by
  regenerating.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg, behaviour keys unchanged.
- `python scripts/gate.py --web` green at each link; the full gate once at the tip; `playwright` green.
- Phone shots at 375, plus one at 1280, of a node's output contract (NodeSurface) and of a round
  file's lift column, before and after. Commit them under `shots/` on `out/webb-5b-report`.
- `git grep -n "MAX_DEPTH\|liftVersus" -- webapp` is empty. Each row's re-test, before and after.
