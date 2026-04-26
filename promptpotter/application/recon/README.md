# `application/recon/` — DORMANT (revival queued)

The sensitivity-scan / recon pass is currently turned off. It has zero callers in the active optimization loop today, but is **queued for reactivation as the second UI feature after M11+**.

## Status

- **Zero callers in the active loop.** No CLI subcommand (`python -m promptpotter recon` does not exist). No notebook UI wrapper. No L1 parameter. No `CampaignConfig` field references it.
- **No runtime cost** — code is loaded but never invoked.
- **Stays on the same rails as everything else.** Lint, type, and test rules apply normally; mechanical refactors that ripple through the codebase (renames, signature changes, API migrations) must update recon too so it remains compilable and revivable.

## What to do (and not do)

- **Don't invest in it.** No new features, no rewrites for their own sake, no speculative redesigns.
- **Don't carve exceptions around it.** If a sweep updates an identifier, schema, or import shape used by the active loop, propagate the same change here.
- **Do read it.** The structure documents what a sensitivity-scan loop looks like; it'll be the basis of the post-M11+ UI feature.
- **Do file an issue** tagged `[recon-dormant]` if you find a problem you don't want to fix yet.

## To revive

Read the post-ship note in [`docs/specs/m9-stable-config-and-scaffolding.md`](../../../docs/specs/m9-stable-config-and-scaffolding.md) for the last known seams. The integration points were `recon_brief` flowing through `RunConfig` into L1, and the CLI `recon` / `show-recon` subcommands. Both were removed during M9 Track 7 cleanup.
