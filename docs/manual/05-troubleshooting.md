# Troubleshooting

Symptom-first reference. Each entry: what you see → why it happens → what to try.

---

## A provider returns 429 "rate limit"

**What you see:** The campaign crawls and the readout prints `⚠ RATE-LIMITED · <provider> …`, or it stops as **Provider rate-limited**.

**Why:** The model provider is refusing sends — your key's tier, or a shared upstream host on OpenRouter. PromptPotter waits, sends fewer at once, and stops only on an hourly or daily quota.

**What to try:**
- Wait, then `python -m promptpotter resume`. Nothing is lost.
- Use your own key with that provider, or route the node to another host (`route_order` in its config).
- Upgrade to a paid tier.

---

## Backend connection refused

**What you see:** The launch is refused with `Backend '<name>' at <url> is not reachable`, or a running campaign stops as **Backend unreachable**.

**Why:** The backend service isn't running, or isn't reachable at the URL you configured.

**What to try:**
- Check the backend is up: `curl http://127.0.0.1:8000/status` (substitute your URL).
- If running a local TermNorm backend, start it in its own terminal first.
- Check `.env`'s `TERMNORM_URL` points at it.
- Then `python -m promptpotter resume`: the samples it missed are measured on resume.

---

## Validation failures on many candidates every round

**What you see:** Many candidates score zero, each under a `⚠ <node>.<param> = <value>  ∉ [<allowed>]` line.

**Why:** The optimizer proposed a value outside the allowed set for a pipeline node. It is caught before any call runs.

**What to try:** It usually clears within 1–2 rounds — the next proposal is told which value was forbidden. If it persists, the allowed set may be misconfigured on the backend side.

---

## Degradation warnings on many candidates

**What you see:** `✂ broken (degradation)` lines appear often. Candidates are being eliminated and the campaign advances slowly.

**Why:** The pipeline itself is returning broken results for a region of configurations — not an optimizer issue.

**What to try:** Read the `{node}:{code}` at the end of each `✂ broken` line.
- `reasoning_budget_exhausted` or `output_truncated` — the model ran out of output tokens. Raise the node's `max_tokens`.
- `reasoning_only_response` — the model stopped after reasoning without answering. Pick a different model or provider for the node.
- `empty_response` — nothing parseable came back, often a refusal or an overloaded model. The optimizer replans away from it.

---

## Low candidate diversity / mode collapse

**What you see:** Every round produces candidates that look nearly identical. Accuracy has plateaued.

**Why:** The optimizer is over-exploiting a small region of the search space, often because the per-round budget is too small to explore.

**What to try:**
- Increase `n_variants` on the `l1_generate` node to widen the search per round.
- If prompt fields are the only axis: confirm the pipeline's LLM node exposes a prompt. If it doesn't, the optimizer has no prompt space to explore.

---

## Missing dependency on import

**What you see:** `ImportError`, or a message naming an extra such as `[excel]`.

**Why:** The core install is intentionally minimal. Optional features live under extras.

**What to try:**
- Install `[all]` if you haven't: `pip install -e ".[all]"`.
- Or install just the extra the message names.

Next: [Going deeper](06-going-deeper.md).
