# w8-job: the job SURVIVOR keeps only what its readers need (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. This is the one SURVIVOR move this batch
allows. A job ships a one-shot migration under the landing (CLAUDE.md, decisions § 0011), proven on
a job file of the current shape.

SPEC: `docs/specs/code-debt-cleanup.md`. One commit per row:
1. **A job is a survivor that carries how its run ended**. DECIDED: no new `data_root.py` Kind
   wherever an existing record holds the fact. The cycle's ledger carries how its run ended (its
   stop record). A refusal before any cycle exists files where the command's outcome is filed (the
   command log). The job keeps only what the daily quota, a landing and the live registry read.
   Re-point the readers (`command_reading.py`, `machine_status.py`) onto the record that holds each
   fact. If a fact has no home but a new Kind, name why and build the Kind.
2. **A queued launch dies with the asking process**: the queued request's intent lives under
   `jobs/` in the shape row 1 leaves.
3. **Run data is read bare outside `store/io.py`** (w8-store cut it here): the long-form data root
   moves the persisted `Job.workspace` strings, so it rides row 1's migration.
4. A symptom from w8-web: under the gate's parallel load,
   `tests/test_security.py::test_a_task_held_at_its_arms_gate_asks_its_host_and_its_answer_acts_once`
   reads `queue/jobs/<id>.json` mid-write (`Invalid JSON: EOF`). Suspected cause: a non-atomic
   `JobRegistry._stamp` or an unlocked read. Root it, then run the test 10× under `gate.py --only pytest`.

OWNS: `promptpotter/domain/job.py`, `application/jobs/registry.py`, `command_reading.py`,
`machine_status.py`, `application/maintenance/migrate.py`, `application/maintenance/landing.py` (the
migration's row), `infrastructure/store/data_root.py` (the job Kind), `config/paths.py` (the long
form), and their tests. `jobs/launcher/` and `jobs/mint.py` are w8-launch3's (re-point only).

LEAN: `promptpotter/` shrinks. Equals-main, digests and behaviour keys unchanged. Run data: name
each wipe. The migration's proof is a test over a job file of today's shape.
