# Troubleshooting

Symptom-first reference. Each entry: what you see → why it happens → what to try.

---

## Groq returns 429 "rate limit"

**What you see:** The campaign halts or slows dramatically. Logs mention `429` errors from Groq.

**Why:** You've hit Groq's free-tier rate limit. PromptPotter honors the `Retry-After` header and waits, but if the limit is tight the campaign will crawl.

**What to try:**
- Wait a few minutes and resume with `python -m promptpotter optimize`. No re-init needed.
- Switch to a smaller model by editing `.env`: `LLM_MODEL=meta-llama/llama-4-scout-17b-16e-instruct`.
- Upgrade to a paid tier if rate limits persist.

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
- Run `python -m promptpotter show-status` to see what the pointer thinks is active.
- Start a new campaign with `/potter-run` — init overwrites the pointer.
- See [`operations/persistence-and-state.md`](../operations/persistence-and-state.md) for the pointer's format and how to reset it manually.

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
- If the step is `llm_only` with code `empty_content_reasoning_fallback`: the LLM is producing empty outputs. This often means the model is overloaded or the prompt is causing it to refuse. L3 will eventually replan to avoid the failing region.
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
