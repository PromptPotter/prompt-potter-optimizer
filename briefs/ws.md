# ws: the charts' readings back, one lineage tree that pairs and frees (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `ws`, arc `arc/ws`.

- **Base:** `origin/land/wave-6` at `f22c87a41`. That is main plus the readings and xconn 1–2 landing commits, which reach main as they are. Your `<base sha>` in common.md is this commit.
- **Spec:** the ledger section `### batch/30 · ws`, rows 1 and 2. **Row 3** (Compare cards for a 16:9 display) **is held, not yours**: the operator's rule is phone first, desktop later. Leave it as it stands.
- **The code shrinks over `webapp/`** as common.md's rule says for `promptpotter/`: two layout derivations become one tree with two layouts. Report `git diff --shortstat <base> HEAD -- webapp promptpotter` per link.
- **Webapp only.** Touch no Python, no served type, no generated file. **Beside you:** narr-2 (cloud, the same base) owns `webapp/lib/derivations/run-phase.ts`, the chat thread and the readout's rendering; touch none of them. Read `webapp/components/CLAUDE.md` and `webapp/app/styles/foundation/tokens.css` first.

## Decisions (orchestrator; build them, do not reopen them)

- **A chart's hover reading is DOM text under the chart**, never painted over its marks (`webapp/components/CLAUDE.md:8`).
  - `EvidenceCharts` and `CostStrip` each gain one reading line under the chart.
  - The line names the hovered or tapped point's values by their served names. It is quieter than the data: small, muted, and a footnote's weight.
  - On a phone, a tap sets it and a second tap clears it. No tooltip and no new dependency.
- **One lineage tree, two layouts** (operator, 2026-11-10, "like OBS, where things can be paired and again unpaired").
  - **Paired:** `DendrogramStrip`, x-aligned under the candidates bar chart, whose order it is. **It stays.**
  - **Freed:** the stand-alone `Forest`.
  - Both are one tree derivation over one lineage, laid out two ways. One button detaches the tree from the bar chart and re-pairs it.
  - One pick, keyed on the candidate's `address` (`workspace.tsx::pick`), highlights it in both.
  - The ledger's re-test holds: one exported layout function in `webapp/components/candidates`.
- **Phone first:** lay out at 390×844 first (single column, wrap, no horizontal scroll), then widen. Each link's shots are at 390×844 first, then 1280×800.

## Links (one commit each, in order)

1. **The readings:** the DOM reading line under `EvidenceCharts` and `CostStrip`, with a test and its shots.
2. **One tree:** the one derivation, its two layouts, the pair/free button and the shared pick, with tests and shots. Delete what the second derivation leaves behind.

## Proof (beyond common.md)

- `vitest`, `tsc`, `eslint` and `playwright` green at the tip. Equals-main UNMOVED: there is no Python change.
- Shots, both widths, pushed with the report on `out/ws-report`:
  - each chart with a reading set;
  - the tree paired under the bars;
  - the tree freed;
  - one pick lit in both layouts.
- Each row's re-test, before and after.
