"""Meta-champion — reduce the on-disk pp-self corpus to one ranked champion table.

The developer-facing L4 "which meta-prompt state is overall best?" reducer. Pure disk
read (zero LLM), recomputed per request by ``GET /champion-registry``. Ranking is the
whole surface: it names a winner, it never crowns or graduates one.
"""
