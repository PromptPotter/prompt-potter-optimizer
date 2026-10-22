# infrastructure/llm/ — LLM clients

## LLM client

`llm/openai_compat.py`: `OpenAICompatibleClient` serves Groq/OpenAI/OpenRouter
as instances (no subclasses) parameterized by a `ProviderSpec` registry.
`llm/anthropic.py::AnthropicClient` is its peer.

**No paid request is sent unadmitted, and `cost_usd` holds only what a provider REPORTED** — a
call it reported nothing for is priced at our rate in `rate_priced_usd`, which a ceiling counts
and no surface calls spent; the hold / bill / unreported rules are [ADR-0003](../../../docs/adr/0003-spend-and-tenancy.md)'s. Every paid path goes through it:
`LLMClientBase._admitted_send`, Harbor's in-process agent send by send (`llm/litellm_sends.py`, so
its cell only RESERVES the run it DECLARES), and `BackendClient.run_query` for a remote cell. **Every
attempt, ours or one a backend or library made for us, ends as ONE `SendOutcome` settled by
`Admission.close`** — a path reading a status or an exception itself is a second classifier. SDK
retries are off — each resend is admitted anew, and drawn from the ONE `llm/send_pacing.py::SendBudget`
its unit of work's OWNER sized and opened (a cell, an optimizer call, a grading, a probe): a loop
counting its own attempts multiplies under every loop around it, so a send under no budget raises
(`drawn_budget`). A caller meters
only a cache replay; a diagnostic verb binds its ledger through `application/initialization/loop_start.py::diagnostic_trace`
and its rate share through `application/jobs/launcher/admission.py::paid_pass` (or `application/jobs/quota.py::paid_call`), and a send missing either raises.

**Provider selection is always EXPLICIT** — the caller passes it to
`registry.get_llm_client`, sourced from the optimizer node's `config.provider`. No
auto-detection and no env-var fallback: either would make a finished run's provider
unrecoverable from the config that declared it.

**`provider` is the GATEWAY; `route_order` is the HOST behind it, and they are two decisions.**
A per-replica prefix cache pays only where one route is hit repeatedly, and a host that does not
cache is indistinguishable from a cold one until you read `served_by`. `ChatRequest.route_order`
names the hosts to try in order, with fallbacks on — each the gateway's `provider_name` as read off `served_by`, never its catalogue, whose `supports_implicit_caching` misreports caching hosts. It is in `hash_call` because it changes WHO answers, not what is
asked, and hosts of one model disagree systematically; and in `PARAM_FORBIDDEN_KEYS` because that
makes it an operator cost lever and never a search axis. **A node naming no route is keyed as the
host it gets**: a gateway model's default host is `ModelProfile.default_route`, written into the
node's config before it is keyed (`domain/pipeline_overlay.py::pin_default_routes`), so a node left
to the gateway and one naming that host file as one cell — and a changed default is a code change
that re-keys every unpinned node on that model.

**What a model ACCEPTS and what we MEASURED about it are two tables, kept apart and COMPOSED, never merged.** `llm/registry.py::_MODEL_PROFILES` is evidence and ships in the wheel; `llm/capabilities.py` is the provider's own claim, resolved highest first — the tenant's hand-authored `model_capabilities.yaml`, `_MODEL_PROFILES`, the fetched per-tenant snapshot, then nothing — in which **the measured evidence narrows the claim and can never widen it**. **A price is no layer of it**: the card's two prices are `llm/pricing.py::lookup_rate` for the node's provider, which is why `model_capabilities` is keyed `provider -> model`.

Two rules bind every reader: an absent answer is `None` and must render as UNKNOWN, never as unsupported — reading it as "no" silently deletes a real search axis; and where the model DOES answer, its ladder REPLACES the node's rather than intersecting with it. A node's list is a default authored before anyone knew which model would run there. **A CAMPAIGN narrowing is the exception and intersects** — that one is a deliberate closing an ADR-0005 capability gates, so the model may still strike a rung it refuses but may never hand back one the operator took away; `PipelineNode.param_values_narrowed` is what tells the two apart once `narrow()` has merged them into one dict. An axis left with NO legal value is `[]`, never `None`: a reader testing it falsy turns an over-narrowed axis into an unbounded one. **`PipelineSchema.param_options` is the ONE applier for the ENGINE** — the L1 wire enum, the param catalogue and `overlay_failures` all read it, and `model` stops being a special case at each. **The SCREEN must not apply it**: `NodeConfigParam.permitted` is what this campaign DECLARED, because the config editor emits it straight back as the narrowing (`webapp/lib/derivations/nodeConfig.ts::nodeOverlayPatch`) — serve the resolved space there and a repaint bakes one model's refusals into the operator's own declaration, while an axis resolving to nothing returns as a closed one. The browser shows the two facts apart instead: the ticks are the declaration, and a rung the model refuses renders ticked AND struck off `model_capabilities`. The ladder is one case of `unsupported_params` (over exactly the keys `openai_compat.chat` puts on the wire; a REFUSED key leaves the axis only the value it is running), and every producer resolves its menu through `resolve_schema_menu`, never `available_models`: a model the OPERATOR typed rides `param_allowed_values` and by construction reaches no admin catalogue, so asking that one draws a blank card on the surface where the spend is committed.

**`LLMResponse.reasoning` is a core field with no code reader — by design.** It captures
the model's own thinking channel for the operator's node-detail "Thinking" pane, and is **strictly analytical** — never a gate, metric, validator, scorer
or cache key. Do not delete it as write-only surface; read its field note first.
