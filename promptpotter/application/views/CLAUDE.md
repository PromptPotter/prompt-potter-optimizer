# application/views/ — the emit contract

**The view builders live here, the views in `promptpotter/domain/phase_views.py`**: a builder (`ingress.py`) needs the selected optimizer's model and scoring formula, a view does not. **An absent measurement renders `—`, never `0.0`**: a zero reads as a measured failure. **The readout is an artifact every cycle owns, never a terminal's** — `readout.py::ReadoutProjection` is built only by `build_run_observers`, and an entry point's whole say is the optional line sink it hands in.
