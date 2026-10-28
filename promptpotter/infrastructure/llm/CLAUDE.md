# infrastructure/llm/ — LLM clients

## LLM client

**Clients are `ProviderSpec` instances, never subclasses** (`llm/openai_compat.py::OpenAICompatibleClient`; `llm/anthropic.py::AnthropicClient` its peer).

**No paid request is sent unadmitted, and `cost_usd` holds only what a provider REPORTED** — the hold / bill /
unreported rules are [ADR-0003](../../../docs/decisions.md) § 0003's. **Every attempt ends as ONE `SendOutcome`
settled by `Admission.close`**; a path classifying a status itself is a second classifier. A send under no
budget raises (`drawn_budget`).

**Provider selection is always EXPLICIT** (`registry.get_llm_client`, from the node's `config.provider`) — no
auto-detect, no env fallback, or a finished run's provider is unrecoverable.

**`provider` is the GATEWAY, `route_order` the HOST behind it; this file owns `route_order`.** It is the
dataset overlay's third key (`nodes.{name}.config.route_order`), named by the gateway's `provider_name` as read
off `served_by`, never a catalogue. It is in `hash_call` (it changes who answers) and in `PARAM_FORBIDDEN_KEYS`
(a cost lever, never a search axis). A node naming no route is keyed as its `ModelProfile.default_route`
(`domain/pipeline_overlay.py::pin_default_routes`).

**What a model ACCEPTS and what we MEASURED are two tables, composed, never merged** (`llm/capabilities.py`,
`llm/registry.py::_MODEL_PROFILES`): **the measured evidence narrows the claim and never widens it.** A price is
no layer of it (`llm/pricing.py::lookup_rate`).

**An absent answer is `None` and renders UNKNOWN, never unsupported** (absent is not zero — owned by
[`docs/developer/conventions.md`](../../../docs/developer/conventions.md) § Code shape); an axis with no legal
value is `[]`. Where the model answers, its ladder replaces the node's; **a CAMPAIGN narrowing intersects**
(`PipelineNode.param_values_narrowed`).

**`PipelineSchema.param_options` is the ONE applier for the ENGINE; the SCREEN must not apply it** —
`NodeConfigParam.permitted` is the campaign's declaration, emitted straight back as the narrowing, so serving the
resolved space there bakes one model's refusals into the operator's declaration.

**`LLMResponse.reasoning` is strictly analytical** — never a gate, metric, validator, scorer or cache key.
