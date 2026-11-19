# cfg-3-fix: the review's one fix pass on arc/cfg-3 (cloud)

`briefs/common.md` binds you (read BASE FIRST). Base: **`arc/cfg-3` (d397ab91f)**, itself on origin/main
e7a0ae4db. Push the fixed arc to `arc/cfg-3` (force-push of your own arc is fine), report to
`out/cfg-3-report` (replace it). `briefs/cfg-3.md` still binds: same row, same files held off
(`application/config_map.py`, `scripts/offline_world.py`).

## The finding (reproduced at the landing, not a guess)
On d397ab91f, an ACCOUNT-tier dataset no longer reads. 30+ tenant datasets on the desk (and on the
live box) declare a split as
```yaml
campaign:
  dataset_split:
    bench: 100
```
with no `demo` / `tolerance` (bbh-dyck, bbh-multistep-arithmetic-8b, banking77, clinc150, every
hotpotqa/legalbench/logic copy). Resolving `promptpotter-self/multistep` (whose seats are such a
dataset) now raises `FileUnreadableError: …/datasets/bbh-multistep-arithmetic-8b/dataset.yaml is
unreadable by this build (ValueError: campaign: dataset_split.demo: Field required)`; e7a0ae4db
resolves it to `DatasetSplit(bench=100, demo=0, tolerance=2)` (the panel's tolerance folded over the
file's bench). Your gate is green because no committed file still declares that shape: you edited the
two research copies, but a tenant tier is data you cannot edit, and a user's upload is the same shape.
Stored campaign records are NOT affected (all 125 desk records carry all three keys).

## What to build (the root your report's Symptoms already named)
A layer's nested model is a PARTIAL of it: a layer (a dataset file's `campaign:`, a family, a panel's
`inner_campaign`, a door's `CampaignLayer`) may state any subset of a nested instance's keys; only the
FOLDED whole is validated complete. The bottom layer, `defaults.yaml`, then states the instance knobs'
defaults, so `{bench: 100}` folds to `{bench: 100, demo: 0, tolerance: 0}` from the file and nowhere
else. Where `defaults.yaml` holds no instance today (`dataset_split: null`, `lives: null`, judge
stages), decide by the data: if null reads exactly as the zero instance (verify every reader of a null
split), retire null and state the zero instance in `defaults.yaml`; an instance whose absence means
something else keeps its fields required when declared, and you say why in the report. Revert the
`demo: 0` / `tolerance: 0` lines you added to research files whose values now come from the fold.

## Proof
- A fact (tests/CLAUDE.md admits it: an unreadable account dataset loses its runs) that a tenant-tier
  `dataset.yaml` declaring only `dataset_split: {bench: N}` reads, and folds `demo`/`tolerance` from
  `defaults.yaml`; and that a panel `inner_campaign` `{dataset_split: {bench: 100}}` over it keeps the
  family's `tolerance`.
- The row's re-test still holds (no nested field carries a valued code default).
- Equals-main per `common.md` (UNMOVED); every digest move named old → new. Full gate green.
