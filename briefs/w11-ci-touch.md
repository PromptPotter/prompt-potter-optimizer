# w11-ci-touch: CI's webapp check is red on release/next since group F (cloud · Opus)

`briefs/common.md` and `briefs/w11-shared.md` bind you. This is one red check, and it blocks the merge
to main. Keep the scope to its root.

**The failure.** CI's `webapp` job has been red on `release/next` at `0c6f59846`, `4322f6753` and
`dd4c0c085`. It was green at `ea94ddd3b`. The failing spec is `e2e/walk/responsive.spec.ts:26`, project
`touch`, `landscape-844` (844×390), "the zero-campaign shell fits its viewport":
`"Attach a dataset file" is under div.remote-control` (`harness.ts::everyTapLands`).
- It fails on Linux: CI, and w11-reads' cloud gate at `0c6f598`.
- It passes on the Windows desk, with the whole spec (37 tests) under `CI=1` included.
- On the desk at 844×390 no `.remote-control` is drawn on that shell, and the attach button sits at
  y 385–429.
- `RemoteControl` draws only with a viewed cycle and its dashboard (`useRemoteModel.ts`), so on Linux
  the zero-campaign shell is viewing a cycle.
- The range `ea94ddd3b..0c6f59846` changed no `webapp/` file. It holds w11-jobs2 (a cycle's and a draft's
  read serve the launch refusal; `RunAdmission.launches`) and w11-connectors2 (capability menu,
  `offline_world.py`).

**Do, in order:**
1. Reproduce it on Linux at `origin/release/next`, then at `ea94ddd3b` (green). Bisect the two landings'
   links if you need to.
2. Answer: why does the zero-campaign shell view a cycle on Linux and not on Windows? Name the read or
   the state that differs.
3. Fix the root. If the shell should view no cycle there, fix what views one. If it should, the
   layout reserves the dock's band in short landscape, as `.views` already does at a phone width
   (`shell.css`, `--remote-dock-h`). The spec does not change.

OWNS: the file the root is in, plus `webapp/app/styles/**` and `webapp/components/shell/**`. w11-tests4 is
running and owns `webapp/e2e/**` and `scripts/offline_world.py`, so if the root is in either, report
the exact change instead of making it.

DONE WHEN:
- The spec is green on Linux at the tip, and the full `CI=1 python scripts/gate.py` is green on Linux.
- The report names the cause from step 2, with the command that showed it.
