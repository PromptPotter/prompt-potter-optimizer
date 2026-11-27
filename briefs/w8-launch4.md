# w8-launch4: the launcher's re-lookups, the queue and the claim (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. w8-launch2, w8-launch3 and w8-resolve3 each cut
these rows, because their holders were split across chains. Those chains have landed, and you own
every holder now. Read the census on `origin/out/w8-launch3-report` and `origin/out/w8-job-report`
first.

SPEC: `docs/specs/code-debt-cleanup.md`. One commit per row, in this order:
1. **A connector's name is looked up again where its `Connector` is held** (holders on the jobs
   side and on the resolve side: `free_tier.py::free_fence`, `pipeline_resolve.py::laid_config`,
   `UploadBasis.connector`).
2. **A check-in's cycle plan selects beside its landing's refusal** (holders: `checkin.py::_land_draft`,
   `jobs/mint.py::resolve_cycle_plan`, `datasets/loaders.py::resolve_bank`/`build_panel`,
   `dataset_access.py::dataset_experiment`, `tabular_ingest.py::read_answer_terms_file`).
3. **A queued launch dies with the asking process**. Once the queued intent carries its command, a
   refusal before any cycle files on the command log, and w8-job's `launch refusal` Kind
   (`jobs/refused/`) goes.
4. **A mint takes no claim until `opening.py::claim_cycle`**: `LaunchRequest.open` takes the
   `Opening` across every kind and `run_job.py`; the landing half is at `landing.py:365`.
5. **A resume's wait is unattributed**: the detached resume opens `_session` twice
   (`launch.py::hold`, then `run_job.py::run_job`). Open it once.
6. A symptom from w8-job: `domain/cycle_result.py::CycleResult.stepped` has no reader in code (the
   stop's `STEP` cause holds it), yet it is still served on the events schema. Delete it.

OWNS: `promptpotter/application/jobs/` (all of it: `launcher/`, `mint.py`, `registry.py`,
`run_job.py`, `quota.py` excepted), `application/free_tier.py`, `application/pipeline_resolve.py`,
`application/datasets/` (`loaders.py`, `dataset_access.py`, `tabular_ingest.py`),
`application/maintenance/landing.py`, `application/commands/launching.py`,
`presentation/cli/commands/launch.py`, `domain/cycle_result.py`, `domain/job.py`, and their tests.
`jobs/quota.py` is w8-wallet's. `runner/` and `domain/records/` are w8-spine3's. Each is a re-point
only.

LEAN: `promptpotter/` shrinks. Equals-main, digests and behaviour keys unchanged. Run data: name
each wipe. If `launch refusal` goes, it is a wipe, not a SURVIVOR.
