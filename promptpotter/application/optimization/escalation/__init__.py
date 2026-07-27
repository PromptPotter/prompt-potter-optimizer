"""Self-healing control law — the concept HOME (escalation + wounds).

This package owns the decision machinery: the control law *decides* (escalate L1→L2→L3)
and *grounds the decision in evidence* (wounds — the failures the next prompt must see).
Adding a rule is adding a row to :mod:`.rules`; the counters mutate only through
observation methods, so "signals from measurement, not the calendar" is structural rather
than a convention. **No barrel here** — a re-export would pull the firing driver, and
through it dispatch, llm_call and mask, into every consumer that wants one state type.
"""
