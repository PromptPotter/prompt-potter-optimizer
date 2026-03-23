"""Tests for backend advisory notification on repeated degradation resets."""

from api.models.opt_search_point import OptSearchPoint
from api.models.phase_event import PhaseEvent
from api.services.campaign.feedback_cycle import _maybe_emit_backend_warning
from api.services.campaign.models import CycleConfig, _LoopState


def _make_state(resets: int = 0, emitted: bool = False) -> _LoopState:
    state = _LoopState()
    state.opt_sp = OptSearchPoint(
        degradation_reset_count=resets,
        backend_warning_emitted=emitted,
        escalation_journal=[
            {"problem_step": "web_search", "warning_types": {"web_search:partial_scrape": 4}},
            {"problem_step": "web_search", "warning_types": {"web_search:timeout": 2}},
        ],
    )
    return state


def _make_config(threshold: int = 2) -> CycleConfig:
    return CycleConfig(backend_url="http://mock:8000", backend_warning_threshold=threshold)


def test_fires_at_threshold():
    events: list[PhaseEvent] = []
    state = _make_state(resets=2)
    _maybe_emit_backend_warning(state, _make_config(2), 5, events.append)

    assert len(events) == 1
    assert events[0].phase == "backend_warning"
    assert events[0].data["degradation_reset_count"] == 2
    assert "web_search" in events[0].data["problem_steps"]
    assert state.opt_sp.backend_warning_emitted is True


def test_one_shot_guard():
    events: list[PhaseEvent] = []
    state = _make_state(resets=3, emitted=True)
    _maybe_emit_backend_warning(state, _make_config(2), 10, events.append)
    assert len(events) == 0


def test_disabled_when_zero():
    events: list[PhaseEvent] = []
    state = _make_state(resets=5)
    _maybe_emit_backend_warning(state, _make_config(0), 10, events.append)
    assert len(events) == 0
