# `application/recon/` — DORMANT

This directory is a preserved reference implementation of the sensitivity-scan / recon pass. It is **not maintained** and is intentionally kept as a working shape for possible future revival.

## Status

- **Zero callers in the active optimization loop.** No CLI subcommand (`python -m promptpotter recon` does not exist). No notebook UI wrapper. No L1 parameter. No `CampaignConfig` field references it.
- **No tests are required to pass against this code.** If a test in this directory breaks, skip it with `@pytest.mark.skip(reason="recon is dormant")`. Do not fix the code.
- **Lint / type / format failures are not bugs.** If a lint or type rule fails, exclude the file from that rule. Do not edit the code to satisfy the linter.

## What you must NOT do

- Refactor, "improve," simplify, modernize, or update this code.
- Apply formatting or style changes from across-the-codebase sweeps.
- Add new tests for this code.
- Pull in dependency changes that flow through the rest of the codebase (e.g., a model rename) — let it stay frozen.
- Delete it. The directory's preservation is the spec.

## What you may do

- Read it. The structure documents what a sensitivity-scan loop looks like.
- Cite it as a template if building a new capability.
- File an issue tagged `[recon-dormant]` if you find a problem — do not open a PR.

## To future Claude sessions

If you are tempted to "fix" or "update" this directory because it looks broken, stale, or stylistically inconsistent: **stop**. Read this file. The dormancy is intentional. Do not touch the code.

## To revive

Read the post-ship note in [`docs/specs/m9-stable-config-and-scaffolding.md`](../../../docs/specs/m9-stable-config-and-scaffolding.md) for the last known seams. The integration points were `recon_brief` flowing through `RunConfig` into L1, and the CLI `recon` / `show-recon` subcommands. Both were removed during M9 Track 7 cleanup.
