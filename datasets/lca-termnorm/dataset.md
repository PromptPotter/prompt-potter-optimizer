# LCA TermNorm — Dataset Context

The multi-node connector-validation set: a mixed BOM (Bill of Materials) corpus splitting into
**materials** (raw material names from engineering BOMs) and **processing** (manufacturing process
descriptions). It is the only dataset here that is not a single `llm_only` node, which is what makes
it the per-connector regression rather than an optimizer-iteration target.

**`dataset_name` is `train`, not `lca-termnorm`.** It is set in `campaign.yaml`, and the CLI
resolves positional → `--dataset-name` → config, so `new lca-termnorm` names the campaign
`lca-termnorm` while the rows come from `train`. Pass `--dataset-name` explicitly if you need them
to agree.

Row counts are not recorded here — there is no shipped bank, so count the resolved rows rather than
trusting a number in a doc. The node chain and every tunable are in `pipeline.yaml`; `llm_ranking`'s
prompt fields are a live optimization surface alongside the pipeline params.
