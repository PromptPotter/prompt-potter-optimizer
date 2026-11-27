# w8-launch: root A's tail (cloud · Opus)

`briefs/common.md` and `briefs/w8-shared.md` bind you. Root A (one `Opening` per run) landed in wave
7. Its re-test passes (`release(exc` is 0 in `application`), so delete the root's row in your first
commit and close its child rows.

SPEC: `docs/specs/code-debt-cleanup.md`. Read each row whole, then close it as its Root says, one
commit per row or per shared root:
- **The embedded launch is not a command**
- **A mint takes no claim…**
- **Three launch faults…**
- **A queued launch dies…**
- **A draft opened from the CLI dead-ends**

Parity is part of done: every one of the five entry points (CLI, `/potter-run`, `embedded_run.py`,
REST, webapp) reaches the changed decision on the existing channel (CLAUDE.md § entry-point-parity).

OWNS: `promptpotter/application/jobs/launcher/`, `application/embedded_run.py`,
`application/commands/`, `domain/command_kinds.py` (hub: your kinds only), the CLI's launch verbs
(`presentation/cli/` launch handlers only), and their tests.

LEAN: `promptpotter/` shrinks. Equals-main (every leg), digests and behaviour keys: unchanged, or each
move named and explained. Run data: name each wipe.
