# xconn: PromptPotter reads TermNorm's served contract; named endpoints per connector (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `xconn`, arc `arc/xconn`.

- **Base:** `origin/main` at `ebe647669`, main's tip with webb-5a landed. Your `<base sha>` in common.md is this commit.
- **The code shrinks**, judged as common.md says. The probe's three-key guess on `/status` and the two code defaults go, and one contract read off the served pipeline replaces guesses at three sites. Run `git diff --shortstat <base> HEAD -- promptpotter` after each link.
- **Spec:** the ledger section `### batch/29 · xconn`, rows 1–3, plus "Two connectors still default in code" in `### batch/26 · rn-conn`. Re-run each Re-test first.
  - Rows 4 and 5 (TermNorm's warning wording, the relay's own retries) are TermNorm's: leave them.
  - Row 3 ("A moved capability or backend revision alone passes a resume", filed 2026-10-29) is stale. `wiring.py::_read_backend_revision`'s docstring decided against a revision gate the next day (79de39c70): a drift is served, never refused. Delete the row, naming that commit.

## TermNorm's side (its `xconn` branch, `11b1dbe`; you cannot read that repo, so it is stated here)

- `GET /version` answers `{"revision": "<sha256 hex>", "contract": <int>}` at the top level, not under `data`. `revision` digests the code, config, prompts and requirements an answer is keyed on, fixed at boot. `contract` is `WIRE_CONTRACT = 1`, bumped by the change that moves a `/matches` request key, reply key or error field.
- `/status` and `pipeline.json` no longer carry a version word.
- `GET /pipeline` serves `pipeline.json` whole, so each node carries `accepts` (the keys beyond `config` its step reads). The door's wire keys for a node are `config`'s keys ∪ `optimizer.param_keys` ∪ `accepts`; any other key is a 400 `wire_key_unknown`.
- On `11b1dbe`:
  - `entity_profiling` and `llm_ranking` accept `prompt`, `output_schema`, `structured_output_mode`, `route_order`, `seed`, `top_p` and `reasoning_effort`. Neither has `answer_fields` or `response_format` among its wire keys.
  - `llm_only` accepts `prompt`, `output_schema`, `answer_fields`, `structured_output_mode`, `route_order`, `seed` and `top_p`, and its `config` holds `response_format`.
  - The row's "and `top_p` on any node" is stale: say so.
- TermNorm's master (`ce4f15b`), which the box runs, serves no `accepts` and no `/version`.

## Decisions (orchestrator; build them, do not reopen them)

- **A node's wire keys bound what PromptPotter sends.**
  - `domain/pipeline_parsing.py` reads each node's wire keys off the served pipeline into `PipelineSchema`. A pipeline that serves no `accepts` (TermNorm master, every other connector today) bounds nothing: a declared absence, not a guess.
  - `datasets/draft_patch.py::_authored_contract` lays `answer_fields` and `response_format` only on a node whose wire keys hold them; anywhere else the edit is refused at the mint, naming the node and the key.
  - `parse_pipeline_response` offers the `response_format` axis only where the node's wire keys hold it.
  - Check-in refuses a node config the contract rejects, naming the node and the key.
- **Named endpoints per connector.**
  - A `remote_http` connector's operator setting is a map from name to URL (`TERMNORM_ENDPOINTS`, `N8N_HARNESS_ENDPOINTS`); the single-URL settings go. `Endpoint` reads the map.
  - A dataset names its `endpoint`. A map of one answers a dataset that names none; a map of more refuses it, naming the names.
  - Each answer's identity hashes its own endpoint's URL, so adding a second endpoint re-keys nothing of the first.
  - The credential stays operator config, per connector (`connectors/CLAUDE.md` § The credential rides the connector).
  - `dbllmbench.py::_DB_DEFAULT_URLS` and harbor's `skill_delivery` default go: each refuses when absent.
  - The `.env` keys change. `CHANGELOG.md` names the old key, the new one and the line an install writes. Any install's `.env`, the box's included, is the operator's step: name it in the report.
- **TermNorm's `/version`.**
  - `Connector` declares the `wire_contract` it speaks (TermNorm: 1). The probe reads `/version` and refuses another contract, naming both numbers; a backend with no `/version` refuses the same way, naming the route.
  - TermNorm's `version_check` reads `/version`'s `revision`, and the three-key guess on `/status` goes. A connector with a version check records what it reported on every launch, whether or not it expects one. `BackendRevision.expected` is harbor's written-against series (`EXPECTED_HARBOR_SERIES`), unchanged, and absent for TermNorm. A drift stays served, never refused.
  - The reported revision rides TermNorm's answer identity, as harbor's package digest rides its own, so a backend fix never replays a stale answer. Every banked TermNorm cell re-keys: name the move (a CACHE re-key).
  - **This link lands only after TermNorm's `xconn` merges to master and the box runs it**, both the operator's word. Build it last, on its own commit, so the first two links land without it.

## Links (one commit each, in order)

1. The wire keys: `PipelineSchema`, the edit, the axis, the check-in refusal.
2. Named endpoints and the two code defaults.
3. `/version`, `wire_contract`, the revision in TermNorm's answer identity; row 3 deleted.

## Beside you

Name every shared file in its link's commit body.

- **la** (cloud, older base) rewrites `application/initialization/wiring.py`'s registry joins and `connectors/__init__.py::_APPLICATION_BUILT_INS`. You share both: touch only your hunks (`_read_backend_revision` and its callers).
- **lite** (cloud) types the capabilities cache and `rates.json` in `llm/capabilities.py`, `llm/pricing.py` and `store/data_root.py`. Touch none of them.
- **faults-1** (cloud, later) gives `Refusal` its acts, and its rows reach `jobs/mint.py::ConfigDriftError`. Leave that class as it stands.
- **readings** (cloud) touches none of your files.

## Proof (beyond common.md)

- Links 1 and 2: equals-main UNMOVED on every leg, behaviour keys unchanged. The offline world serves `llm_only` with no `accepts`, so nothing is bounded there.
- Link 3: the offline world answers `/version` (`scripts/offline_world.py`). No decision moves; name each moved leaf (`tracediff.py`).
- A check-in schema edit naming `answer_fields` on `llm_ranking` against a pipeline serving `11b1dbe`'s `accepts` is refused at the mint, naming the node and the key; the same edit on `llm_only` mints. Use a fixture of that pipeline, not a live TermNorm.
- A fake backend that answers `contract: 2` refuses at the probe, naming 1 and 2.
- Two n8n datasets on two named endpoints both run with no `.env` edit between them, and the first's banked cells keep their keys.
- Each row's re-test, before and after.
