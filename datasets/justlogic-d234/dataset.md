# JustLogic d234 — the live L4 inner instrument

3-class deductive reasoning (Chen 2025, `michaelchenkj/JustLogic`, arXiv 2501.14851): given a
paragraph of logical premises and a claim, answer `TRUE` / `FALSE` / `Uncertain`. Synthetic
and knowledge-independent — no factual recall.

## The cut

**An iid random mix of JustLogic depths 2, 3, and 4.** 200 rows per depth from the HF `train`
split (deterministic `seed=42` per depth), the three depths **interleaved before numbering**,
so any `n`-sample prefix is an iid draw across d2/d3/d4. The depths are derived from this
dataset's NAME — one `_load_justlogic` serves every cut (`justlogic_depths`,
`promptpotter/application/datasets/loaders.py`), so a new combination such as
`justlogic-d34` needs a dataset dir and no code. Each cut is a SEPARATE dataset name, never a re-cut of another (the archive keys a cell by
`(dataset_name, node_configs, sample_id)` with query text OUT of the key, so re-cutting in
place would serve the old cut's banked rows under the new sample_ids).

The authors' canonical test set is withheld (leakage control), so numbers here are NOT
leaderboard-comparable; HF `train` is the public training fold. Per-depth label distribution is
~balanced across the three classes, so a class-bias the pipeline shows is a reasoning failure,
not a label-skew artifact.

## Scoring

`exact_match(predicted, ground_truth)` after stripping the last `**…**` bold span
(case-insensitive), over `TRUE` / `FALSE` / `Uncertain`. Mode-prediction baseline = 33%. The
`answer_format` field instructs the model to end with `**TRUE**` / `**FALSE**` / `**Uncertain**`
on its own line — prose with no extractable label scores zero.

## Pinning

- Single `llm_only` node; target `openai/gpt-oss-20b:nitro` via OpenRouter, `reasoning_effort: low`.
- `model` / `provider` are operator-locked (not in `optimizer.param_keys`); `reasoning_effort`
  pinned to `low` via `param_allowed_values`. Swap only via the `nodes.llm_only.config` overlay,
  never L1 mutation.
- **The `:nitro` suffix is a deliberate speed trade.** Nitro routes each call to the fastest
  upstream, so a `seed` buys nothing across stacks and is not set — inner-run noise is drawn
  fresh per arm instead of cancelling as common random numbers in the paired (variant − origin)
  outer diff, raising the bar every optimizer prompt verdict must clear. Panel size is the lever that
  buys minimum-detectable-effect back. A true pin needs OpenRouter provider pass-through or a
  single-stack provider — both operator calls, not loop decisions.
