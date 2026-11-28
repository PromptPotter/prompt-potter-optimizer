# w11-web2: area 12, round 2: the webapp's lifecycle and read rows (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you.

- SPEC: `briefs/map.md` § 12 and the debt doc's `## 12` section.
- Target: no business logic in an adapter, and the browser derives nothing.
- Mobile first: the phone is the operator's surface.
- w11-web closed the walk's 11 rows. This round takes the rows that are not root E.

Links:
1. **A read's lifecycle, three rows, one root:**
   - "Sign-in clears the read cache it just filled".
   - "A pane's data read in flight reports nothing, where it draws no `Loading`": w11-web cut this to
     one pane. Finish every pane.
   - "A pane's acts gate on the dashboard read, not the entry they act on".

   A read is a state its pane reports, and an act gates on the read of what it acts on.
2. **"A pane's phone layout keys on the screen, not its column"** and **"The Account view's phone band
   and token chips are unproven"**:
   - The layout reads its container.
   - Add a phone spec beside an existing world door. `webapp/e2e/**` is w11-tests3's: add one spec
     file, never edit theirs, and name it.
   - Fold in the walk's symptom: RunLimitsControl's "Samples per candidate" strip clips "Ma 1" at
     390 px.
3. **Server-side surface rows:**
   - "App lifespan state outlives shutdown".
   - "REST 422 finds its model through request.scope".
   - Fix each on its root, in `presentation/api/**` and `main.py`.
4. **Test and style rows:**
   - "Vitest isolate:false makes vi.mock silently miss".
   - "A focus ring is re-skinned outside `base.css`".
   - "Node-surface prose hides in `title=`".
5. **The symptoms w11-web left:**
   - `IngestConversation.tsx` calls `useRead` before its unauthed return; the auth gate belongs in the
     mount.
   - `RunLimitsControl.tsx` widens a computed-key budget's payload type.
6. **"Unverified, for the next change to each file"**: re-test each of the row's five smells, then
   close it or fix it.

Not now:
- Root E and its four rows (adapters, recomputes, mirror, unread fields), and the three rows w11-web
  filed under root E or area 11: these wait for area 11's served reads.
- The MCP connector on the box is the operator's step.
- Never CSS anchor positioning or the overlay lag.

OWNS:
- `webapp/app/**`, `webapp/components/**`, and `webapp/lib/**` apart from its generated files and
  `derivations/`.
- `promptpotter/presentation/api/**` and `main.py`, apart from served shapes: a shape change is area
  11's.
- Their tests.

DONE WHEN:
- Each row is re-tested at 390×844, with before/after shots on `out/w11-web2-report`.
- `npm run test` and `gate.py --web` are green.
- No new file is under `webapp/lib/derivations`.
- The keys are unmoved.
