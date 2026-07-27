"""L1/L2/L3 candidate-output validators.

Two postures, and the split is the point: ``*_strict`` / ``*_output`` REJECT (failures
route up as a ``ValidationFailure``), while ``*_behavior`` only SCORES conformance into
``review.md`` and the round file. Model/provider locking belongs to neither — it is
structural, because the param keys are never emitted for the LLM to set in the first place.
"""
