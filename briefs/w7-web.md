# w7-web: the browser derives nothing (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7` at `0fd79cba1`** (main `2c7dff34e` plus w7-small, w7-text, w7-host, w7-line; it is
not on main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`).

SPEC: `docs/specs/code-debt-cleanup.md` § wave-6 · arch review 2, **The served contract is generated
and the browser derives nothing (root E)**, with every row it names under **Closes**, plus the rows of
§ webd that are the same fact (the browser's copies of engine facts served instead, its test fixtures
generated from the served shapes, every served field read or deleted). Read each whole.

DECIDED:
1. One link per served fact: the engine serves the fact (in the read that already carries its
   subject), the browser reads it, and its derivation file and its tests go. Order the links by the
   lines they delete, largest first.
2. A served field no browser reads is deleted, not kept "for later".
3. `webapp/lib/test-fixtures.ts` stops being hand-kept: fixtures are built from the served shapes
   (`types.generated.ts`) or from a served answer the offline world records.
4. A derivation that is pure presentation (formatting, layout, colour) stays in the browser; name each
   one you keep and why in the report.

OWNS: `webapp/lib/derivations/**` (44 files), `webapp/lib/test-fixtures.ts`, the components that read a
derivation you remove, and the served shapes that gain a fact (`domain/dashboard_rows.py`,
`application/served_dashboard.py` field additions only, and the read that serves each). Not yours:
`webapp/lib/hooks/` (w7-ver's read cache; `useRead.ts` is w7-gate's), `webapp/e2e/harness.ts`
(w7-gate's), the version and ETag code (w7-ver's).

LEAN: about 4,500 TypeScript lines go (the row's estimate); `promptpotter/` grows only by each served
fact. Re-test: `git ls-files webapp/lib/derivations | grep -v __tests__ | xargs wc -l` falls by the
served facts. Run data: none; a served shape is no ledger record.
