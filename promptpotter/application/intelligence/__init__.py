"""Shared materialized views over historical search data. MUST NOT import from ``optimization``."""

from promptpotter.application.intelligence.indexes import AxisIndex, SampleIndex

__all__ = ["AxisIndex", "SampleIndex"]
