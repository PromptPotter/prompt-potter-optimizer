# gate-hold: a busy admission gate never kills a running cell (cloud)

`briefs/common.md` binds you. Base: `origin/main` (3784ed33b or later). No ledger row exists yet:
this brief is the row. File it in your commit as you close it (name the hunk).

## Symptom (observed on the desk, 2026-11-18)
An L4 outer (`promptpotter-self.dyck__2bd880`) was running inner cells abreast. Four `python -m
promptpotter new <dataset>` launches started at once beside it. Within a minute:
- two of the four launches refused with `admission_gate_stuck` ("The machine's admission gate is
  held by another process that has not released it. Nothing was started; retry shortly."), from
  `application/jobs/interlock.py::AdmissionGate.held` after `LOCK_TIMEOUT` (5 s,
  `config/settings.py`);
- an inner cell already 11 minutes into its run died on the same `ServiceUnavailableError`. The outer
  banked it as an errored sample of its arm (`ERR "ServiceUnavailableError: The machine's a…"`), so a
  paid cell was lost to lock contention.

## Links
1. **Census**: every `job_registry.admission_gate()` holder (`jobs/launcher/admission.py`,
   `jobs/quota.py`, `jobs/registry.py`), what each does under the gate (a scan of job files, a
   quota count, a write), and which of them a RUNNING cell reaches mid-run (a scoring pass, a paid
   call, a heartbeat or stamp). Measure the hold time of each with a local test under 4 concurrent
   launchers.
2. **The root**: the gate is held only for the count and the write, never across a scan that grows
   with the number of job files (the live-jobs index is what a count reads, per `application/CLAUDE.md`
   § Run admission). If a mid-run ask must take the gate, contention there waits (it runs off the
   event loop) instead of raising into the cell; a refusal is for a launch, never for work already
   admitted. No retry loop added beside the gate, no raised `LOCK_TIMEOUT` as the fix.
   Re-test: 8 concurrent launchers plus a running slotless job asking its mid-run admission 50 times
   see 0 `admission_gate_stuck` on the running job, and each launcher either starts, queues or
   refuses with the gate's words.

## Owns
`application/jobs/**`, `infrastructure/store/io.py` (the lock primitive only), their tests. NOT
`config/settings.py` (another chain edits it), not `application/runner/**`. Whether an errored cell
classifies infra and is re-measured belongs to the running `l4-valid` chain: report it, do not edit.

## Target
Net ≤ 0 `promptpotter/` lines.

## Report
Per `common.md`, plus the census table (holder, work under the gate, hold time before and after).
