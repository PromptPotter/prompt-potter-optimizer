"""LLM client abstraction layer.

Nothing is re-exported here; import the leaf directly. Provider selection is **always
explicit** — the caller passes it to :func:`registry.get_llm_client`, sourced from the
optimizer node's ``config.provider``. No auto-detection, no env-var fallback: either
would make a finished run's provider unrecoverable from the config that declared it.
Retries and ``Retry-After`` are the provider SDK's job (``max_retries``), not ours.
"""
