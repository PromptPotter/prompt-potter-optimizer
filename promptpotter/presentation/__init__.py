"""presentation/ — entry-point adapters (read-only over application/).

cli/ — campaign_runner.py (new / resume verbs), session.py, parsers.py;
  thin shells over runner/ + bootstrap.
views/ — display formatters: view_models.py, display.py, view_ingress.py,
  render/ (to_text / to_markdown), live/ (LiveDisplay ledger subscriber).
api/ — FastAPI read-only surface + sanctioned mutating seams
  (POST /commands/{kind}, POST /datasets/ingest); deps.py resolves identity.
writers.py — per-cycle markdown writers (log.md / review.md).

No campaign-artifact writes from here; no business logic in shells.
full contract: presentation/CLAUDE.md
"""
