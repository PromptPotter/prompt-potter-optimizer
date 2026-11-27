# w11-config: area 07 to its Target, one typed node document, two resolution seams (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 07 and the debt doc's `## 07` section, which holds w10-survey's rows on `application/datasets/**` and
`pipeline_resolve.py`.

Links, deletion first:
1. **A dataset read is a third resolution**: `pipeline_resolve.py::resolve_pipeline_for_dataset` parses
   and narrows a schema beside `RunResolution`. It has been reported twice and never built. Make it one
   resolution: `resolve_dataset` answers which bytes, and `pipeline_resolve.py` which values, consuming
   the first.
2. **The node document is typed two ways, and the L2 steer is a side memory** (about 11 modules hold a view
   of it): one typed node document that every door parses. This closes **a check-in cannot switch an
   upload between its connector's pipelines**.
3. **The pipeline carrier is typed only at its door**, and **a campaign names its connector while five
   readers look it up again**: one lookup.
4. **Params are not one fold beside a dataset re-resolved by name** (w10-survey's row): through
   `ParamLayers`, re-tested first.
5. **Census:** all of `application/datasets/` (13 modules: `draft_patch.py`, `startability.py`,
   `ingest.py`, `draft_campaign.py`, `origin_resolve.py`, `loaders.py`, `origin_readiness.py`,
   `tabular_ingest.py`, …), `config_map.py`, `pipeline_responses.py`, `domain/pipeline_parsing.py`,
   `domain/pipeline_schema.py`, `campaign_config.py`, plus area 07's recovered items. Each gets a verdict.

Not yours: the four config knobs of decision 5 (w11-scoring deletes them; leave their rows to it). Held:
prompt-field order (decision 26, a feature) and whether an upload's bytes are kept before the parse
(decision 27, decided with anon).

OWNS: `application/pipeline_resolve.py`, `pipeline_responses.py`, `config_map.py`, `application/datasets/**`,
`domain/pipeline_*.py`, `domain/campaign.py`, `campaign_config.py`, `layer_fold.py`, `checkin_draft.py`,
`config/**` less `config/paths.py`, and the dataset stores.

DONE WHEN: `git grep -cE "cfg\.get\(\"(model|provider)\"" -- promptpotter` is 0 · `git grep -c
"CONNECTORS.get(.*campaign.connector)" -- promptpotter` is 1 · picking TermNorm's `default` on an upload
makes the draft's `active_steps` that chain (a test through a world door) · the report counts the modules
holding a view of the node document, before and after · keys: name each move. LEAN: `promptpotter/`
shrinks.
