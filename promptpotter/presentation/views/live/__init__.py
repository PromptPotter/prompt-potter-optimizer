"""Live ledger subscriber — CLI and notebook share one ``LiveDisplay``.

Single ingress: the display consumes ``CycleRecord``s from the per-cycle
``CycleEventLog`` via ``on_record``. The package splits across four
concerns:

* :mod:`display` — :class:`LiveDisplay` (the ledger subscriber).
* :mod:`sample` — per-sample HIT/MISS line (:func:`fmt_query_result`).
* :mod:`candidate` — per-candidate header + summary classification
  (:func:`fmt_individual_header`, :class:`IndividualSummary`).
* :mod:`phase` — round-summary renderers fired on ``L1_SCORE:exit``.

Post-hoc reads happen by opening ``campaigns/<cycle_id>/log.md``.
"""
