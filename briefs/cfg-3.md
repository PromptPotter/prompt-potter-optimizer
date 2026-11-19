# cfg-3: a sub-model's knobs default in defaults.yaml, never in code (cloud)

`briefs/common.md` binds you (read BASE FIRST). Base: **the sha the dispatch names** (origin/main).
Push to `arc/cfg-3`. Your row: `docs/specs/code-debt-cleanup.md` § `cfg-2 (what cfg left at landing
50; the re-key window)`, ONLY the row **A sub-model's knobs still default in code** (read its full
text). The other two rows of that section are NOT yours: the node-document row is a larger design,
and the demo-axis row is the operator's call. Runs beside `rk-dec-3`, which owns
`scripts/offline_world.py`; do not touch it. Do not touch `application/config_map.py` (another
session holds it).

## The row
Every knob of a model `CampaignConfig` / `OptimizationConfig` nests (`LivesConfig` start / cap,
`JudgeSpec.temperature`, `campaign_config.py::over_the_default`'s budget, and every other one the
census finds) defaults in `assets/datasets/defaults.yaml` (`authored.py::defaults_config`), its field
required in code. Values do not change. Census first: every nested model and every valued default.
**Re-test:** no field of a model `CampaignConfig` nests carries a valued default (write it as a fact
that walks the model tree, so a new knob with a code default fails it).

## Proof
Equals-main per `common.md`: no decision or request moves (the values are the same); name any digest
move and why (a hashed `defaults.yaml` re-keys: give old → new). Full gate green. Report on
`out/cfg-3-report`.
