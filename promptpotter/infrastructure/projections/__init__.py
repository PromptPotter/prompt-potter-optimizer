"""Projections — subscribers to the ``CycleEventLog`` spine.

Every view here extends :class:`base.DerivedView`, which owns the single
``isinstance(record, …)`` dispatch; there is no second dispatch path, so adding a record
subtype touches one file. The outbound SSE stream is NOT a subscriber. What each view
writes, and why the stream is exempt: ``infrastructure/CLAUDE.md``.
"""
