# OptimizationConfig — required vs. invariant fields

`promptpotter/application/config.py::OptimizationConfig` splits its fields into two contracts.

**Required per-dataset experiment knobs** (`Field(...)`, no default — Pydantic raises if missing): `improvement_threshold`, `max_failures`, `degradation_threshold`. Every `datasets/*/campaign.json` must declare them so the dataset config is self-describing.

**System invariants** (defaulted, not user-tunable per dataset; MUST NOT appear in any `campaign.json`, runner, or notebook block): `enable_l2=True`, `enable_l3=True`, `seed=42`, and `ScoringSetConfig.swap_out_delta_se=0.7` (sized to fire the round-1→2 swap on the typical 20-sample / 5-candidate budget — see [`../methods/exploration-exploitation.md`](../methods/exploration-exploitation.md)).

Everything else is a defaulted but tunable knob — per-dataset configs may override.

Guard test: `tests/test_campaign_config_validation.py::test_required_optimization_fields_must_be_explicit`.
