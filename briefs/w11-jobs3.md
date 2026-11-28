# w11-jobs3: area 06, round 3: a launch's ending is its command's; a job's hop exists from LAND (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 06 and the debt doc's `## 06` section. w11-jobs2 made a read serve the refusal its launch meets. This
round makes a launch's whole life, from queued to refused to ended, one state that its readers report.

Target: one launch path for all five entry points; a queued launch is a state its readers report; a
refusal is the one the read serves; a launch's ending is its command's.

Links, deletion first. Work each row along its root:
1. **A launch not yet landed carries a hop of two empty strings**, with **A mint's job names its hop
   before its cycle share lands** (filed under 05). One root: `Job.hop` is `None` until LAND, and the share
   is filed in the same write, or a job without one reads as zero. `UNRESOLVED_HOP` goes.
2. **A launch released refused reads as Detached, Start still offered**, **A queued launch's ending
   files where an unasked one does** and **A promoted launch's stderr is discarded until its cycle
   exists**. One root: a launch's ending is its command's, and its output has a home from its spawn,
   named by its job. w11-jobs2 named the join: a released refusal's job can be found from the cycle's
   last `Hold` record (`opening.py::claim_cycle`).
3. **A fork's preview does not serve the refusal its launch meets**: the jobs2 preflight, read at the fork
   (`mint_and_start.py::ForkLaunch.sends`).
4. **The run dock says a launch's refusal as a send that happened** and **A check-in offers Propose on a
   draft it would refuse, and a failed propose speaks the parser**. Both are walk finds: re-test them in
   the offline world's browser walk (`e2e/cold/launch.spec.ts`, w11-tests4), which now launches.
5. **Two jobs/ smells from w11-jobs2's census** and **`jobs/launcher/run_job.py` no longer runs a job**.

Leave open, each for its reason:
- **A command's settle and a retry's wait re-read on a clock**: a design fork for the operator.
- **A check-in's cycle plan selects beside its landing's refusal**: Held.
- **A fork cannot narrow a dead param axis**: tune 1.
- **Child-process facts idle the parent ~8 s**: wave 6.

OWNS: `application/jobs/**` less area 05's `quota.py`, `shares.py`, `install_spend.py`, `capacity.py` and
`free_tier.py`; `application/commands/**`; the check-in Start and Propose paths; `domain/job.py`; the run
dock and check-in components in `webapp/`; and their tests. Re-point importers anywhere, imports only.
w11-spend2 runs at the same time and owns area 05's files, `hierarchy.py` and `served_dashboard.py`. A
reader of `UNRESOLVED_HOP` in those files is a re-point you may make; leave the rest of their lines to
spend2.

DONE WHEN:
- Each row is re-tested through a door, before and after.
- `git grep -c UNRESOLVED_HOP -- promptpotter` is 0.
- Equals-main names each move, and the keys are named.
- `python scripts/gate.py` is green.
