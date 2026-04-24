# Troubleshooting

Symptom-first reference. Each entry: what you see → why it happens → what to try.

---

## Validation failures on many candidates every round

**What you see:** Many candidates receive a synthetic score of zero. The optimizer logs mention "validation failure" or "invalid proposal."

**Why:** L1 is proposing a parameter value outside the allowed set for that pipeline node. Rail 1 catches this before any backend call runs.

**What to try:**
- L2's directive should fix this within 1–2 rounds by naming the forbidden value explicitly. If it persists beyond that, check the `param_allowed_values` in your pipeline schema — the allowed set may be misconfigured on the backend side.

---

## Degradation warnings on many candidates

**What you see:** The `⚠` degradation lines appear frequently. Candidates are being eliminated. The campaign is advancing slowly.

**Why:** The backend pipeline is returning low-quality results for a consistent configuration region — not an optimizer issue.

**What to try:**
- Look at which `{step}:{code}` is firing. This is shown in the warning lines.
- If the step is `llm_only` with code `empty_content_reasoning_fallback`: the LLM is producing empty outputs. This often means the model is overloaded or the prompt is causing it to refuse. L3 will eventually replan to avoid the failing config region.
- If the step is a specific pipeline node: check that node's configuration in your `campaign.json` — the degradation threshold may be set too tight, or the node may have a bug.

---

## Low candidate diversity / mode collapse

**What you see:** Every round produces candidates that look nearly identical. Accuracy has plateaued.

**Why:** L1 is over-exploiting a small region of the search space. This can happen when the improvement threshold is set very low (making it easy to "win" with tiny gains) or when the candidate budget is too small to explore.

**What to try:**
- Increase `creativity` in the campaign config to push L1 toward more varied proposals.
- Increase `n_variants` to widen the search per round.
- If `thinking_styles` are cycling through the same few values, this is a sign the variant library is not being sampled broadly — check the `thinking_style` axis in `prompt_variants.json`.
- If prompt fields are the only axis: confirm the pipeline's LLM node has a prompt template exposed. If it doesn't, the optimizer has no prompt space to explore.