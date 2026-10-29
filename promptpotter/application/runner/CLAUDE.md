# application/runner/ — the optimize loop

**Writing a round's operator-facing artifacts is orchestration, not `presentation/`**: `output.py` writes `log.md`, `review.md` and the hard-samples files and reconstructs views from disk, session-scoped; `review_md.py` beside it renders `review.md` and stays pure. **A cycle writes its campaign's result only while it holds the campaign's line** (`campaign_result`); the facts the held-out pass banks are read through `read_bench`.
