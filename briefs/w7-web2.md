# w7-web2: root E continued, the browser derives nothing (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7`'s tip** (main `2c7dff34e` plus the wave-7 landings, w7-web and w7-open2 among them).
It is not on main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-6 · arch review 2, **The served contract is generated
and the browser derives nothing (root E)**, with its still-open rows (**Adapters over a flat served
shape**, **The browser recomputes engine facts**, **Served fields the browser never reads**). Read
each whole, plus w7-web's report (`git show origin/out/w7-web-report:report.md`): its decided
presentation list stands, and its links are the pattern to follow.

DECIDED:
1. One link per served fact, LARGEST FIRST: measure each derivation file's non-test lines that a served
   fact would delete, and take them in that order. The engine serves the fact in the read that already
   carries its subject, the browser reads it, and the derivation and its tests go.
2. Every served field no browser or CLI reads is deleted: drive `surface-ledger`'s `p_served` down and
   lower its baseline in the same commit.
3. Presentation stays in the browser (w7-web's named list). Name each new one you keep, and why.
4. `test-fixtures.ts` stays cut: generating it belongs to root F.

OWNS: `webapp/lib/derivations/**` except `spend.ts`, the components that read a derivation you remove,
the served shapes that gain a fact and the reads that serve each, and their tests. Shared and named:
w7-rows2 adds a masthead count, a successor `Said`, the `warming_up` tag and `ActiveNode`'s scope in
`served_dashboard.py` and `live_dashboard.py`, so leave those fields alone and expect to rebase near
them. Not yours: `spend.ts` and the spend reads (w7-spend), `pipeline_resolve.py`,
`initialization/**` and `runner/output.py` (w7-res2), `scripts/`, `webapp/e2e/**`.

LEAN: webapp TypeScript falls clearly, and `promptpotter/` grows only by each served fact. Re-test:
`git ls-files webapp/lib/derivations | grep -v __tests__ | xargs wc -l` before and after. Digests,
behaviour keys and equals-main are unchanged. Run data: none (a served shape is never banked; if one is,
stop that link and report it, as w7-web did for `MeteredSpend`).
