"""L1 phase — the per-round generate → critique → score → execute loop.

The innermost optimizer layer; the agent contract — what L1 may mutate, and with what
cause — is ``../CLAUDE.md``. Out-of-bounds for every module here: no direct campaign-
artifact writes (persistence routes through ``CycleEventLog.append``), LLM calls only via
the generate / critique paths, and no dispatch-hub bypass for a prompt fill.
"""
