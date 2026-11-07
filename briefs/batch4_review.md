# Batch 4 review (`origin/batch/3b..origin/batch/4`, K L O S T)

**Verdict: land after one fix pass.** I found no code defects. Three introduced defects are all in wording: two in O's contract and one in S's note. K, L and T are clean. L's refusal is typed (`FileUnreadableError`, 409 `manifest_unreadable`) and goes through `EvidenceParams.answer`, so REST, the CLI, MCP and the webapp answer it the same way. The served diffs are exactly what S and T name.

## INTRODUCED

1. **`docs/operations/backend-integration.md:3`**: the contract names two sources for a pipeline: `GET /pipeline` and `pipeline_declaration`. It leaves out the third branch of `pipeline_resolve.py::served_pipeline`. A connector that declares neither (`dbllmbench`, `dspy_module`, `harbor`) gets `ServedPipeline.doc is None`, so its whole pipeline is `dataset.yaml::pipeline`.
   - Scenario: someone writing an in-process connector reads this page and builds a `pipeline_declaration` they don't need.
   - Fix: "…served at `GET /pipeline`, built by `pipeline_declaration`, or, where it declares neither, the dataset's own `dataset.yaml::pipeline`, which overlays the other two."
2. **`backend-integration.md:7` and `README.md:44`**: both say a `remote_http` connector that keeps `Connector`'s defaults speaks all three endpoints. Only two are defaults: `cell_path` gives `/matches`, and `serves_pipeline` gives `GET /pipeline`. `/status` comes from TermNorm's own `preflight` and `version_check` (`termnorm.py:95-122`). `Connector.preflight` defaults to `None` (`protocol.py:209`), so `unreachable` probes nothing.
   - Scenario: a FastAPI backend serves `/status` behind a connector that only uses the defaults. Nothing ever calls `/status`.
   - Fix: name `/matches` and `GET /pipeline` as the defaults, and `/status` as TermNorm's declared probe. The README decision was settled on the belief that all three endpoints are defaults, and that belief is wrong for `/status`.
3. **`webapp/components/account/AccountUsageTab.tsx:243-244`**: the bold lead "Set by whoever runs this server." is followed by "Whoever runs this server sets how many…", which says the same thing again.
   - Fix: "…</strong> It caps how many of this account's runs share the machine. Ask them to raise it."

## PRE-EXISTING (row proposals)

- `evidence/read.py:813-814` `_cycle_facts` loads again the manifest that `_at` already holds (`at.campaign`), behind an `assert`. If the manifest is deleted between the two reads, the read fails with a bare `AssertionError`, a 500. The census kept it as "on a hop", but it is the same subject's campaign. It is one edit in L's file: pass `at.campaign`. **Rides with:** M17.
- `sample_measurement.py:771`: when a pause is requested, `execute_stale_data_protocol` returns the degraded cell without marking any outcome. K's outcome-enum row should list this exit too. **Rides with:** M4 (re-key).
- `ConnectorInspector.tsx:8` links § Connection security for every connector. For an in-process one, that env does not apply. **Rides with:** M18.
- A dataset-wide evidence read skips a torn manifest with only a log line (`list_campaigns`), and the answer does not say so. This is already filed at ledger :1239, so no new row.

## Stale lines

- `evidence/read.py:521`: the `_Located` docstring ("sandbox, campaign dir, cycle dir — before any path is read") predates the `campaign` field, and the manifest is now read in `_at`.
- Ledger :798-800: L's narrowed row has no **Re-test**, which every entry needs. It also still sits under § B "Run-data payloads are models", although the census calls it a served-answer change.
- `roadmap.md:165` (M4c "with M8") and `:185` ("M8 with M4c"): the stated reason for pairing them was riding M8's `ConfigKnob.source`, and that is gone. Give a new reason or drop the pairing.
- `backend-integration.md:12` says "TermNorm's is read once at init". In fact `served_pipeline` reads it live at run init, on every draft read, and in `dataset_pipeline`. No connector behaves differently here.
