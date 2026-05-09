"""Shared materialized view over historical search data.

The ``intelligence`` package is the common ground between the optional human
sensitivity scan and the optimization loop. It owns AxisIndex (axis-keyed
derived view), SampleIndex (per-sample index storage), the variant
library loader, and scoring-set adaptation — all shared primitives that
both loops consume.

Directionality: this package must NOT import from ``scan`` or ``optimization``.

Two index types — :class:`AxisIndex` and :class:`SampleIndex` — are the
dominant entry points; both are re-exported here from the ``indexes/``
subpackage which carries the full curated surface (also includes
``ConfigIndex``, ``AxisImpact``, ``ValueRecord``, ``SampleRecord``,
``FailureCluster``, ``NOISE_THRESHOLD``).

Other entry surfaces — Rasch exploration (``exploration``), hard-sample
sorter (``hard_sample_sorter``), hard-sample archive
(``hard_sample_archive``) — live in their own modules; import directly.
"""

from promptpotter.application.intelligence.indexes import AxisIndex, SampleIndex

__all__ = ["AxisIndex", "SampleIndex"]
