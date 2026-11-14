# model-lines: the bench's optimizer model is one fact (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `model-lines`, arc `arc/model-lines`.

- **Base:** `origin/main` at `cf5050f67` (ren landed, 81). Your `<base sha>` in common.md is this commit.
- **The code shrinks**, judged as common.md says: 22 yaml lines and their comments go; name any growth
  that is the fix's substance. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** `### batch/31 · small`, the row "The bench's one optimizer model is spelled eleven times"
  only. Re-run its Re-test first. **Not yours:** archive hygiene, the paper harness, any other row.

## The census (orchestrator, at main dcbb0f66e; re-grep it on your base, the brief lists every owner)

- 11 `model: minimax/minimax-m3.1-flash` + 11 `provider: openrouter` in
  `assets/optimizers/{potter,levi,capo,gepa}/pipeline.yaml` (4+3+3+1 llm nodes). `assets/checkin/pipeline.yaml`
  (`openai/gpt-6-luna`, the bench's own check-in node) authors its own and keeps it.
- `domain/search_point.py::NodeConfig` requires `provider` and `model`; `optimizer_manifest.py`'s
  `model()` / `proposer` / `_node_digest` read them; `bench/node_context.py::Calls.model`.
- Readers that must keep reading the same value: `evidence/head_to_head.py` (`optimizer_models`),
  `free_tier.py` (`tier.optimizer_models.get(called.model)`), `runner/inner/tasks.py:425`
  (`node["model"] = spec.inner_model`, the L4 inner model laid per node), the yaml comments that
  explain the choice ("the bench runs every optimizer on one model", capo's "set `config.model` to the
  target's", levi's Gemini note).
- Re-key (small-2's census): a node's `model` reaches `optimizer_manifest.py::treatment`'s manifest
  hash, so all four treatment digests may move; `config_map.py::bench_defaults_pin` (ren renamed
  `application/knobs.py`) hashes
  `defaults.yaml` whole, so the pin moves if the fact lands there; no measurement key reads it.

## Decisions (orchestrator; build them, do not reopen them)

- **One spelling of the bench's optimizer call target** (provider + model), in the bench's defaults
  the fold already reads (`defaults.yaml` through `pipeline_resolve.py::ParamLayers`, or the layer the
  census shows every optimizer's llm nodes already fold under): every llm node of an optimizer reads it
  unless the node, its manifest or the campaign authors its own. A campaign swaps every optimizer
  node's model by setting it once. No second door: no per-optimizer copy, no swap verb, no fallback
  chain in a reader; `NodeConfig` keeps its fields required and is built from the fold.
- **The why moves with the fact**: "the bench runs every optimizer on one model" is said once, beside
  the fact; each yaml's paper deviation note keeps only what differs from its paper (the model line
  points to the one fact). capo's instruction to match the target's model names the one key.
- **L4's inner model** keeps one door: if `spec.inner_model` lays the same fact, it lays the one key,
  never eleven node keys. If the census shows it must stay per node (a node authors its own), say why
  in your report.

## Links (one commit each, in order)

1. The one fact and its fold; the 22 yaml lines go; readers unchanged in what they read.
2. (Only if the census finds one) the inner model's door onto the one key.

## Beside you

- **mkey** (cloud, same base) adds scoped API keys in the access and account layers. Disjoint files;
  if you meet `api/` or `account.py`, name it in your commit body and touch only your hunk. Both of you cut rows in
  `code-debt-cleanup.md`: delete only your own row.

## Proof (beyond common.md)

- Equals-main UNMOVED on every leg; behaviour keys unchanged. Name every digest that moves, old → new
  (treatment digests and the bench pin are expected; an arm id or a measurement key moving is a defect).
- The landing key: if the treatment or pin move makes stored campaigns unloadable, say which files
  wipe (`maintenance/landing.py::landing_key`); never a reader of two shapes.
- The row's re-test is 0, and `git grep -c "provider: openrouter" -- 'promptpotter/assets/optimizers/*/pipeline.yaml'` is 0.
- A test: a campaign that sets the one key moves every optimizer llm node's model; a node that
  authors its own keeps it.
