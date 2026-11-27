# w8-launch2: the mint, the queue and the job record (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. w8-launch closed root A's tail and cut these
rows for lack of Owns. You hold them now.

SPEC: `docs/specs/code-debt-cleanup.md`. One commit per row or per shared root:
1. **A partial head-to-head mint leaves its arms unstamped**, and **A mint takes no claim until
   `opening.py::claim_cycle`**: the mint answers each arm as it lands, and its opening owns and
   stamps it.
2. **A queued launch dies with the asking process**, and **A dead producer's lock and its containers
   are reclaimed by two owners**: one owner each.
3. **A job is a survivor that carries how its run ended**: the job SURVIVOR keeps only what the
   daily quota and a landing read. A job is a SURVIVOR, so it ships a one-shot migration under the
   landing (CLAUDE.md, decisions § 0011), proven on a job file of the current shape.
4. **A fork's share lands at its landing**: re-test it first, since w7-open3 may have closed it.
5. Small ones:
   - `command_kinds.py::BankSnippetPayload.experiment` re-spells an id pattern (`^[A-Za-z0-9_-]+$`)
     and refuses every campaign minted from a family dataset (`justlogic.d234__…`). Root: it reads
     the campaign id's own grammar.
   - `embedded_run.py:65` imports the landing inside a function, against `conventions.md`'s
     module-scope rule.
   - `head_to_head.py:178` sets a mode that `embedded_run.py` overwrites: `Hosted` carries the mode.

OWNS: `promptpotter/application/jobs/mint.py`, `application/jobs/registry.py`,
`application/jobs/launcher/`, `application/embedded_run.py`, `domain/job.py`,
`application/maintenance/landing.py` and `migrate.py` (the job migration), `docker_host.py`
(`reap_dead_producers`), `domain/command_kinds.py` (hub: `BankSnippetPayload` only), and their tests.

LEAN: `promptpotter/` shrinks. Equals-main, digests and behaviour keys unchanged. Run data: name
each wipe. The job migration is the one SURVIVOR move this wave allows.
