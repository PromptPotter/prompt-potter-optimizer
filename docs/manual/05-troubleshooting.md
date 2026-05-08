# Troubleshooting

Symptom-first reference. Each entry: what you see → why it happens → what to try.

---

## Groq returns 429 "rate limit"

**What you see:** Campaign halts or crawls. Logs mention `429` errors from Groq.

**Why:** Groq's free-tier rate limit. PromptPotter honors `Retry-After`, but a tight limit makes the campaign crawl.

**What to try:**
- Wait a few minutes and resume: `python -m promptpotter optimize`. No re-init needed.
- Switch to a smaller model in `.env`: `LLM_MODEL=meta-llama/llama-4-scout-17b-16e-instruct`.
- Upgrade to a paid tier.

---

## Groq daily token limit exhausted on 120b

**What you see:** Daily volume on `openai/gpt-oss-120b` hits Groq's per-model ceiling and queries start failing.

**Why:** `120b` is the canonical default but has a tighter daily limit than `20b`.

**What to try:**
- Swap the `model` field in the relevant `datasets/<name>/pipeline.json` to `openai/gpt-oss-20b` and keep iterating. Flip back to `120b` for benchmarks.
- Each dataset's `reasoning_effort` default is tuned to keep both models clear of Groq's per-model output ceiling — `bbeh` ships `reasoning_effort: low` so `20b` doesn't burn its reasoning budget.
- `max_tokens` is **never** set as a numeric default in any dataset's `pipeline.json` — provider ceiling applies. Raise it per-cycle via `campaign.json::pipeline_overrides`.
- Target-layer model lives in `datasets/<name>/pipeline.json::llm_only.config` (and needs `provider` if not Groq); optimizer-layer model lives in `datasets/<name>/campaign.json::optimizer_llm`. They're independent.
- Full per-dataset matrix: [`.claude/skills/potter-run/reference/dataset-reasoning-matrix.md`](../../.claude/skills/potter-run/reference/dataset-reasoning-matrix.md).

---

## Backend connection refused

**What you see:** `[CONNECTION]` errors, or init fails with "could not connect to backend."

**Why:** The backend service isn't running, or isn't reachable at the URL you configured.

**What to try:**
- Check the backend is up: `curl http://127.0.0.1:8000/status` (substitute your URL).
- If running a local TermNorm backend, start it in its own terminal first.
- Check `campaign.json` or whatever config you're loading has the right URL.

---

## Campaign won't resume — "active session mismatch"

**What you see:** `ActiveSessionMismatchError` when you try to optimize.

**Why:** The active session pointer and the campaign on disk disagree about which cycle is current. Usually happens after editing session files by hand or copying a `.promptpotter/` tree between projects.

**What to try:**
- Open `.promptpotter/active_session.json` to see what the pointer thinks is active, and `campaigns/<cycle_id>/dashboard.json` to see what's actually on disk.
- Start a new campaign with `/potter-run` — init overwrites the pointer.
- See [`operations/persistence-and-state.md`](../operations/persistence-and-state.md) for the pointer's format and how to reset it manually.

---

## Validation failures on many candidates every round

**What you see:** Many candidates receive synthetic score zero. Logs mention "validation failure" or "invalid proposal."

**Why:** L1 proposed a parameter value outside the allowed set for a pipeline node. PromptPotter catches this before any backend call (the *schema-compliance check*; Wound 1 in [self-healing](../concepts/self-healing.md)).

**What to try:** Self-healing usually clears this within 1–2 rounds — the next outer layer (L2) sees the failure and rewrites L1's next prompt to name the forbidden value. If it persists, check `param_allowed_values` in your pipeline schema — the allowed set may be misconfigured backend-side. (Self-healing in one line: when the loop hits a recoverable failure, an outer layer rewrites the next prompt — see [self-healing.md](../concepts/self-healing.md).)

---

## Degradation warnings on many candidates

**What you see:** The `⚠` degradation lines appear frequently. Candidates are being eliminated. The campaign is advancing slowly.

**Why:** The backend pipeline is returning low-quality results for a consistent configuration region — not an optimizer issue.

**What to try:**
- Look at which `{step}:{code}` is firing. This is shown in the warning lines.
- If the step is `llm_only` with fatal code `reasoning_budget_exhausted`: the reasoning model spent its entire output budget on the hidden reasoning trace before emitting visible content. Raise `pipeline_params.llm_only.max_tokens` (look at `step_tokens.llm_only.output` and `reasoning` to size it). L2's brief should already point at `max_tokens` directly.
- If the fatal code is `empty_response` or `output_truncated`: the LLM returned empty or truncated content for a non-reasoning reason. Often a sign the prompt is causing a refusal or the model is overloaded. L3 will eventually replan to avoid the failing region.
- If the step is a specific pipeline node: check that node's configuration in your campaign config — the degradation threshold may be set too tight, or the node may have a bug.

---

## Low candidate diversity / mode collapse

**What you see:** Every round produces candidates that look nearly identical. Accuracy has plateaued.

**Why:** L1 is over-exploiting a small region of the search space. This can happen when the improvement threshold is set very low (making it easy to "win" with tiny gains) or when the candidate budget is too small to explore.

**What to try:**
- Increase `creativity` in the campaign config to push L1 toward more varied proposals.
- Increase `n_variants` to widen the search per round.
- If prompt fields are the only axis: confirm the pipeline's LLM node has a prompt template exposed. If it doesn't, the optimizer has no prompt space to explore.

---

## Missing dependency on import

**What you see:** `ImportError` or "install the `[stats]` extra" messages.

**Why:** Core install is intentionally minimal. Optional features (Wilson CI, Jupyter, Excel loaders, Langfuse, Anthropic) live under extras.

**What to try:**
- Install `[all]` if you haven't: `pip install -e ".[all]"`.
- If you started with the minimal install, the error message names the extra — install just that one.

Next: [Going deeper](06-going-deeper.md).
