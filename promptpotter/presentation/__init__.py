"""presentation/ — entry-point adapters (read-only over application/).

cli/ — campaign_runner.py (new / resume verbs), session.py, parsers.py;
  thin shells over runner/ + bootstrap.
views/ — terminal display only: display.py (ANSI primitives), render/ (to_text /
  sp_diff), live/ (LiveDisplay ledger subscriber), notebook_run.py,
  startup_checklist.py. The typed View models + markdown rendering are the
  application's emit contract (application/views/); the per-cycle markdown
  writers live in application/output.py.
api/ — FastAPI read-only surface + sanctioned mutating seams
  (POST /commands/{kind}, POST /datasets/ingest); deps.py resolves identity.

No campaign-artifact writes from here; no business logic in shells.
full contract: presentation/CLAUDE.md
"""
