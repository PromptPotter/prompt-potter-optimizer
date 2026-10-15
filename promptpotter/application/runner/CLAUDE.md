# application/runner/ — the optimize loop

**Writing a round's operator-facing artifacts is orchestration, not `presentation/`**: `output.py` writes `log.md`, `review.md` and the hard-samples files and reconstructs views from disk, session-scoped; `review_md.py` beside it renders `review.md` and stays pure. **Only the cycle holding the campaign's line writes its result** (`campaign_result`), and `read_bench` is the one reading of the facts the held-out pass banks.
