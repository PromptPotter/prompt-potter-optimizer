# infrastructure/llm/ — LLM clients

## LLM client

- **Clients are `ProviderSpec` instances, never subclasses** (`openai_compat.py::OpenAICompatibleClient`). <!-- probe: git grep -cE "class \w+\(OpenAICompatibleClient\)" -- promptpotter/*.py == 0 --> Every attempt ends as a `SendOutcome` settled by `Admission.close`, never a status classified at the call site.
- **Provider selection is always EXPLICIT** (`registry.get_llm_client`, from the node's `config.provider`): no auto-detect, no env fallback. <!-- probe: git grep -cE "os\.environ|getenv" -- promptpotter/infrastructure/llm/registry.py == 0 -->
- **`provider` is the GATEWAY, `route_order` the HOST behind it**, set in the dataset overlay and named as the gateway reads it off `served_by`; it is in `hash_call` (it changes who answers) and in `PARAM_FORBIDDEN_KEYS` (a cost lever, never a search axis).
- **What a model ACCEPTS and what we MEASURED are two tables, composed, never merged** (`capabilities.py`, `registry.py::_MODEL_PROFILES`): measured evidence narrows the claim and never widens it, and a price is no layer of it. An absent answer is `None` and renders UNKNOWN, never unsupported; a CAMPAIGN narrowing intersects.
- **`PipelineSchema.param_options` applies the model's answer for the ENGINE; the SCREEN must not apply it** <!-- probe: git grep -cE "param_options\(" -- promptpotter/presentation/*.py == 0 -->: `NodeConfigParam.permitted` is the campaign's declaration, emitted straight back.
- **`LLMResponse.reasoning` is strictly analytical**: never a gate, metric, validator, scorer or cache key.
