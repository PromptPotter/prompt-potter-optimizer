# application/jobs/ — run admission

- **One launch: `launcher/launch.py::launch`, called from `commands/launching.py` alone**; `jobs/` imports nothing from `commands/`. A launch path reserving its own slot is the bug: the machine cannot report occupancy it was not told about.
- **An entry point hands DATA, never a callable**: a `LaunchRequest` arm, a `RunMode`, and `Inline` where a person watches the process.
- **A paid verb outside a run is admitted inside the application function**: a scoring pass through `launcher/admission.py::paid_pass` (a slot, refused `machine_busy`, never queued), one LLM call through `quota.py::paid_call` (slotless). `_refuse_taken_cycle` is the one taken-cycle check for both.
- **Admission and liveness are OS file locks** (`jobs/interlock.py`, `infrastructure/producer_lock.py`), never process memory or a clock — else a terminal verb reaps a live campaign, or a killed run wedges the box.
- **A server-launched run executes in its own process** (`launcher/run_job.py`) and adopts the job. A queued job's intent never leaves the process that authenticated it, so a restart drops the queue.
