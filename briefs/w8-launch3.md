# w8-launch3: the mint's claim and the launch's re-lookups (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. w8-launch2 and w8-resolve2 cut these rows
because their Owns missed the holders. This brief's Owns name every one.

SPEC: `docs/specs/code-debt-cleanup.md`. One commit per row or per shared root:
1. **A mint takes no claim until `opening.py::claim_cycle`**: the claim must come before
   `draft_store.py::DraftStore.become`, whose rename refuses an existing cycle dir. So the claim
   rides the draft's ledger (w8-launch2's census).
2. **A connector's name is looked up again where its `Connector` is held**.
3. **A check-in's cycle plan selects beside its landing's refusal**.
4. **A fork's share lands at its landing**, its in-run half: `runner/run_init.py::_rebase_held`
   lands the fork after the cut.
5. **A resume's wait is unattributed**: attribute the wait, then cut it.
6. Small ones:
   - `docker_host.py::claim_machine` swallows the reclaim's `OSError` into a debug log, so a machine
     step's failed reclaim is reported nowhere. It should surface the way the sweep's `orphans`
     fault does.
   - `infrastructure/store/layout.py::PathComponent` holds the id grammar's validator in
     infrastructure, so a domain payload re-reads `ID_COMPONENT_RE`. Move it to
     `domain/cycle_paths.py`.

OWNS: `promptpotter/application/jobs/launcher/` (`opening.py`, `admission.py`, `checkin.py`,
`head_to_head.py`, `mint_and_start.py`), `application/jobs/mint.py`,
`infrastructure/store/draft_store.py`, `infrastructure/docker_host.py`, `runner/run_init.py`,
`application/initialization/wiring.py`, `infrastructure/store/layout.py` (`PathComponent` only),
`domain/cycle_paths.py`, and their tests.

Not yours: `domain/job.py`, `jobs/registry.py` and `landing.py` are w8-job's. `runner/round.py` and
`domain/records/` are w8-spine2's. `pipeline_resolve.py` is w8-resolve3's. Each is a re-point only.
Grep every holder before your first link.

LEAN: `promptpotter/` shrinks. Equals-main, digests and behaviour keys unchanged. Run data: name
each wipe. No SURVIVOR moves.
