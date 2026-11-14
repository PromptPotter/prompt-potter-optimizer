# webb-5a: an L4 stack names its datasets; one theme key; the walk asks the gate for its world (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `webb-5a`, arc `arc/webb-5a`.

- **Base:** `origin/main` at `917861717`, main's tip with lw (and rn-conn) landed (webapp modules in their new homes: `components/pipeline/`, `lib/state/`, `lib/storage-keys.ts`). Your `<base sha>` in common.md is this commit.
- **The code shrinks**, judged as common.md says: over `webapp/` (excluding `lib/api/types.generated.ts`) and `scripts/` together, expect about 0. Run `git diff --shortstat <base> HEAD -- webapp scripts ':!webapp/lib/api/types.generated.ts'` after each link. `promptpotter/` is untouched.
- **Spec:**
  - From `### webb-5`: "An L4 panel draws its inner datasets as identical cards", "The theme key is spelled twice", and "`lib/address.ts:6` imports `./derivations/search-point` past the barrel".
  - From `### mob-3`: "The walk server takes its world by a hand-set hash".

  Every other row stays filed. Re-run each row's Re-test first.
- **Webapp rules:** `webapp/CLAUDE.md`, `webapp/components/CLAUDE.md` and `webapp/lib/CLAUDE.md` bind you. Mobile first: the phone widths 375, 393 and 412.

## Decisions (orchestrator; build them, do not reopen them)

- **A nested card names its dataset.** The served `NestedPipelineRef.dataset` reaches the card through its `PipelineLayer`, as a field of its own. It is never folded into `connector`, and never composed into one string.
  - The card's tag line reads the dataset; the connector rides beneath it in the secondary ink.
  - The campaign's own levels (optimizer, campaign) keep their tag as it is, since the masthead names their dataset.
  - The zoom button keeps its "3 datasets" label.
- **The theme key has one home, `lib/storage-keys.ts::STORAGE_KEYS.theme`.**
  - The pre-paint script stays a classic script outside the bundle, so it still runs before first paint.
  - Its key comes from the table at build and in dev. Vite emits the script with the key filled in and serves it the same way.
  - `index.html` keeps only `#root` and its entry (`webapp/CLAUDE.md` § Build + run). One build mode.
  - A drift test beside a hand-kept copy is the patch, and it is not this row.
- **`address.ts` imports through the barrel.** If importing the barrel closes a cycle, cut the cycle at its root, the way lw cut the hierarchy import's, and name it.
- **The walk asks the gate for its world.**
  - `scripts/gate.py` answers the current `walk-world` path for the tree, building it when absent, through a flag on the existing CLI. It is never a second script.
  - `playwright.config.ts` / `serve.mjs` ask it when `PP_E2E_WALK_WORLD` is unset, and the hand-set hash becomes optional.
  - `webapp/e2e/README.md` says so.

## Links (one commit each, in order)

1. The nested card's dataset; the barrel import.
2. The theme key's one home; the walk's world from the gate.

## Beside you

- **readings** (cloud) changes how estimates render: `lib/format.ts`, `types.generated.ts`, and the `ci_lo`/`ci_hi` readers. **rec** and **rn-conn** (cloud) are engine-only and may regenerate `types.generated.ts`. Never hand-edit it.
- **la** (cloud, your base) moves CLI and identity modules. It shares `scripts/gate.py` with you: its `_PRESENTATION_IMPORTS_INFRASTRUCTURE` line against your walk-world flag. Touch only your hunks, and name the file.
- Touch no `promptpotter/` file. A row that needs one is cut into "rows to file".

## Proof (beyond common.md)

- `python scripts/gate.py --web` green at each link; the full gate once at the tip.
- Phone shots at 375, plus one at 1280, of an L4 campaign's pipeline stack, before and after. The walk world's L4 fixture (`offline_run.py --straight --l4`) serves one. Commit them under `shots/` on `out/webb-5a-report`, beside `report.md`.
- With `PP_E2E_WALK_WORLD` unset and the walk world absent, `npm run e2e` builds the world through the gate and walks.
- Each row's re-test, before and after.
