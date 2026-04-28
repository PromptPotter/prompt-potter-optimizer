"""Shared materialized view over historical search data.

The ``intelligence`` package is the common ground between the optional human
sensitivity scan and the optimization loop. It owns AxisIndex (axis-keyed
derived view), SampleIndex (per-sample index storage), the variant
library loader, and scoring-set adaptation — all shared primitives that
both loops consume.

Directionality: this package must NOT import from ``scan`` or ``optimization``.
"""
