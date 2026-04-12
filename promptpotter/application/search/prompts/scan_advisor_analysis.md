## Analysis Approach
Work through these analysis steps before producing your recommendation:
1. Trace data flow: UPSTREAM parameters have multiplier effects — poor upstream data cannot be compensated by downstream tuning.
2. Flag high-impact targets: empty or default string params (prefixes, suffixes, query modifiers) that shape what data enters the pipeline.
3. For *_schema axes: identify output fields that are redundant or missing for downstream consumption. Suggest mutations relative to the current output_schema shown in LLM Node Details.
4. Skip axes unlikely to affect accuracy. Prioritize axes with the highest expected impact on end-to-end accuracy.
5. Estimate a diagnostic budget (queries per axis).