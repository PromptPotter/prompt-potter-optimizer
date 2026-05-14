"""Per-sample compact rendering for ``dashboard.json``.

One-line text per query so the live dashboard stays scannable instead of
bloating with full ~2 kB query strings. Used in the ``l1_score`` block
when ``live=True``.
"""

from __future__ import annotations

from typing import Any

# Per-sample terminator badge for the compact ``fmt_sample_line`` rendering;
# unmapped nodes render as the first two characters of the node name.
NODE_BADGES: dict[str, str] = {
    "llm_only": "ai",
    "llm_ranking": "ai",
    "entity_profiling": "ai",
    "cache_lookup": "cache",
    "fuzzy_matching": "fz",
    "token_matching": "tk",
    "web_search": "ws",
}


def trim(text: str, n: int) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def fmt_sample_line(s: dict[str, Any]) -> str:
    """One compact line per query for ``dashboard.json::current_round.nodes
    .l1_score.output.candidates[].samples`` — keeps the live dashboard
    scannable instead of bloating it with full ~2 kB query strings."""
    qi = int(s.get("qi", 0))
    hit = bool(s.get("hit"))
    cached = bool(s.get("cached"))
    time_s = float(s.get("time_s") or 0.0)
    badge = NODE_BADGES.get(s.get("terminated_at") or "", (s.get("terminated_at") or "?")[:2])
    cache_icon = "📖" if cached else " "
    mark = "HIT " if hit else "MISS"
    query = trim(s.get("query") or "", 42)
    pred = trim(s.get("prediction") or "", 28)
    gt = trim(s.get("ground_truth") or "", 20)
    in_tok = s.get("input_tokens")
    out_tok = s.get("output_tokens")
    tok_seg = ""
    if in_tok is not None or out_tok is not None:
        tok_seg = (
            f" io={in_tok if in_tok is not None else '-'}/{out_tok if out_tok is not None else '-'}"
        )
    return (
        f"  {time_s:4.1f}s #{qi:03d} {mark} [{badge}]{cache_icon}"
        f"{tok_seg} -> '{pred}' gt:'{gt}' q:'{query}'"
    )


__all__ = ["NODE_BADGES", "fmt_sample_line", "trim"]
