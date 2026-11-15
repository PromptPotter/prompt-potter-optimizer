# ws-2: a chart's reading keyed, a candidate lit by its address, the freed tree held to its card (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `ws-2`, arc `arc/ws-2`.

- **Base:** `origin/main` at `6fa93bab0` (landed 93: addr-2, lineage spines keyed on a candidate's
  `address` and a served short label per subject and node). Your `<base sha>` in common.md is this commit.
- **Spec:**
  - `### batch/30 · ws` row 1 ("A painted chart has no keyboard path").
  - `### batch/26 · addr`'s row "The freed tree and the channel map still key a candidate on `facts.individual`".
  - `### batch/32 · tests-w`'s row "The freed lineage tree overflows its card under load", whose app side is yours.
  - **Row 2 of ws** (Compare cards for a 16:9 display) **is held, not yours**: the operator's rule is phone first, desktop later. Leave it.
  - Re-run each row's Re-test at your base first. A row already closed is deleted, with its evidence in the commit body. Delete only your own rows.
- **The code shrinks over `webapp/`**, as common.md's rule says for `promptpotter/`:
  - one key handler in the reading hook, not one per chart;
  - one subject key, the address, where a candidate id stood.
  - Report `git diff --shortstat <base> HEAD -- webapp promptpotter` per link.
- **Webapp only.** Touch no Python, no served type and no generated file. Read `webapp/components/CLAUDE.md`
  and `webapp/app/styles/foundation/tokens.css` first.
- **Beside you:** **tests-w** (cloud, the same base) owns `tests/`, `scripts/offline_world.py`,
  `scripts/offline_run.py` and `runner/entry.py`. You own `webapp/`, its `e2e/` included.

## Decisions (orchestrator; build them, do not reopen them)

- **The keyboard path lives in `ui/ChartReading`'s hook.**
  - The chart is focusable. ← and → step its reading column, and Escape clears it.
  - Every chart that reads through the hook gains this at once; neither chart adds a handler of its own.
  - The focus ring is the tokens' one. The reading line stays DOM text under the chart (`webapp/components/CLAUDE.md:8`).
- **A candidate is keyed by its `address` everywhere a pick or an arm is matched**, the way the
  spines are keyed since addr-2:
  - `Forest.tsx`'s `altIds`;
  - `ChannelMap.tsx::ArmedActions`'s subject;
  - the "Picked" fold's "reads at", which follows the pick, not the channel.
  - Delete the individual-keyed path; never keep both.
- **The overflow: the root first.**
  - Reproduce `webapp/e2e/walk/responsive.spec.ts` "the freed lineage tree fits its viewport" beside a busy
    `python scripts/offline_run.py` (it read `div.card.cand-card +593px` at desktop-1440 [touch]).
  - Name which of two things it is:
    - the card is wide until a late style or measure lands, which is a flash the user sees, so fix the app's layout;
    - `webapp/e2e/harness.ts::noSidewaysScroll` reads before the pane settles, so the walk waits on the settled
      state the app exposes, never on a timeout.
- **Phone first:** lay out at 390×844 first (single column, wrap, no horizontal scroll), then widen.
  Each link's shots are at 390×844 first, then 1280×800.

## Links (one commit each, in order)

1. The keyboard path in the reading hook, with a test.
2. The address key on the freed tree, the channel map and the Picked fold. Test the two branches
   that re-propose one arm key, and check that only the picked one lights.
3. The overflow: the repro, the named cause, the fix, and the walk 5/5 beside a busy offline run.

## Proof (beyond common.md)

- `vitest`, `tsc`, `eslint` and `playwright` green at the tip. Equals-main UNMOVED: there is no Python change.
- Shots at both widths, pushed with the report on `out/ws-2-report`:
  - a chart stepped by the keyboard;
  - one of two same-key arms lit in the freed tree and the channel map;
  - the freed tree inside its card.
- Each row's re-test, before and after.
