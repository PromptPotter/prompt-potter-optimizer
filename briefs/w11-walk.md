# w11-walk: product walk #1, read-only, phone first (cloud · Opus)

`briefs/common.md` and `briefs/w11-shared.md` bind you. You change no code and commit no test.
The operator finds what no chain finds: nine of his last eleven finds showed only on a running system
seen on a phone. You are that walk.

1. **Run the product on the offline world**, through the existing e2e harness (`webapp/e2e`, the world's
   doors; `docs/developer/offline-run.md`). Use a phone viewport (390×844) first. A desktop width comes
   only where the layout differs.
2. **Walk what an operator does,** end to end:
   - sign in;
   - open a dataset draft and start a check-in;
   - launch, then watch a round live (the live indicator, the candidates, the arm in flight);
   - pause, resume, and fork on divergence;
   - read a candidate's evidence and the lineage tree;
   - compare two runs head to head;
   - open the spend and the wallet;
   - add, then remove, a provider key;
   - read the visits digest and bulletins;
   - delete an account.

   At each step, ask: is what the screen says true, derived from the backend, and readable on a phone?
3. **Each find becomes a row** in the debt doc, under the area whose files cause it, in the doc's entry
   format. **Rides with:** that area's chain. A find inside an area whose w11 chain is running is a row
   too: that chain re-tests it at landing. A find already in a row is one line in that row.

OWNS: new rows in `docs/specs/code-debt-cleanup.md`, in one commit. Nothing else.

DONE WHEN: every step above was walked or its block is named · each find has its route, its steps, and
what was expected against what was seen · the report lists the walk's path and screenshots by step,
stored as PNGs under `out/w11-walk-report` and never in the repo · `filed N`.
