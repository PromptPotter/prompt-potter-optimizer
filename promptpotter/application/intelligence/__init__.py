"""Intelligence — shared materialized views over historical search data.

Pure derivation off measurements, consumed by both the optimization loop and the optional
sensitivity scan. **Layer rule: MUST NOT import from ``optimization``** — both consume
intelligence, intelligence depends on neither, and the violation fails loud at import.
Nothing is re-exported here; import each module directly.
"""
