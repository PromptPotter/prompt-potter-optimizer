## What & why

<!-- One or two sentences. Link the issue / spec / milestone if there is one. -->

## Pre-flight gate

Does this PR add a new concept — a class, projection, injection, prompt,
field, dict, or file? Then answer all eight (`CLAUDE.md` → Pre-flight gate).
"I don't know" or "kind of" on any line is a hard block.

**If this PR adds no new concept, replace this list with `N/A — no new
concept` and skip to Checks.**

- [ ] 1. Which §0 bucket does this belong to? (central loop / escalation /
      errors-heal / dispatch / state+persistence / on-disk / tracing /
      archive). If none — stop.
- [ ] 2. Does an existing channel already do this? (Searched first — default
      answer is yes.)
- [ ] 3. Is the name distinct from every existing concept? (Grepped first.)
- [ ] 4. Is the name self-describing without opening another file?
- [ ] 5. Can this ride existing infrastructure (ledger, `INJECTIONS`,
      `OptSearchPoint`, dispatch hub) without adding a sidecar?
- [ ] 6. Can the AI / operator read this fact from a file without running the
      CLI?
- [ ] 7. Does §0 (`docs/architecture.md`) need updating? (If yes, that PR
      lands first.)
- [ ] 8. New LLM call or backend match? Then the call site is wrapped in
      `observed_node()`.

## Checks

- [ ] `python scripts/gate.py` green — every check CI runs, including the webapp half
- [ ] No backward-compat shim, re-export alias, fallback chain, or `# legacy`
      comment introduced
