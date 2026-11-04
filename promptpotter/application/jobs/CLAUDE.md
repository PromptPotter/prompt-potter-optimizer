# application/jobs/ — run admission

- **Every launch goes through `launcher/launch.py::launch`, a head-to-head's arms through `launcher/head_to_head.py::admit_head_to_head` (all admitted or none, priced before any key), from `commands/launching.py`**; `jobs/` imports nothing from `commands/`. <!-- probe: git grep -cE "promptpotter\.application\.commands" -- promptpotter/application/jobs/*.py == 0 --> A launch path reserving its own slot is the bug: the machine cannot report occupancy it was not told about.
- **An entry point hands DATA, never a callable**: a `LaunchRequest` arm, a `RunMode`, and `Inline` where a person watches the process.
- **A paid verb outside a run is admitted inside the application function**: a scoring pass through `launcher/admission.py::paid_pass` (a slot, refused `machine_busy`, never queued), one LLM call through `quota.py::paid_call` (slotless); both refuse a taken cycle.
- **A server-launched run executes in its own process** (`launcher/run_job.py`) and adopts the job. A queued job's intent never leaves the process that authenticated it, so a restart drops the queue.
