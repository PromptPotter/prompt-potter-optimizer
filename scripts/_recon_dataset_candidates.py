"""Throwaway: measure 25-sample origin on three candidate datasets.

Fires `openai/gpt-oss-20b @ reasoning_effort=low` via Groq directly
(bypasses PromptPotter's optimization loop — just need raw origin
numbers to decide whether each candidate lands in the 30-40% recon
band). Mirrors what the dataset's `pipeline.json` would request, so
the projection is faithful.

Candidates:
- michaelchenkj/JustLogic         — 3-class TRUE/FALSE/Uncertain
- facebook/ExploreToM             — open-ended QA (non-adversarial slice)
- tasksource/Boardgame-QA         — 3-class proved/disproved/unknown (depth-2/3)

Run from repo root with `.env` populated (GROQ_API_KEY):
    python scripts/_recon_dataset_candidates.py
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable

from datasets import load_dataset
from dotenv import load_dotenv
from openai import OpenAI

N_PER_CANDIDATE = 25
PROVIDER = "openrouter"  # "groq" hits per-minute rate limits at N >= 30
MODEL = "openai/gpt-oss-20b:nitro" if PROVIDER == "openrouter" else "openai/gpt-oss-20b"
TEMPERATURE = 0.0
REASONING_EFFORT = "low"

load_dotenv()
if PROVIDER == "openrouter":
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
elif PROVIDER == "groq":
    client = OpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ["GROQ_API_KEY"],
    )
else:
    raise ValueError(f"unknown PROVIDER {PROVIDER!r}")


INTER_CALL_SLEEP_S = 0.0 if PROVIDER == "openrouter" else 0.4  # Groq needs throttle; OpenRouter doesn't


def call(
    system: str,
    user: str,
    *,
    effort: str | None = None,
    max_tokens: int | None = None,
) -> tuple[str, float]:
    time.sleep(INTER_CALL_SLEEP_S)
    t0 = time.perf_counter()
    kwargs: dict = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": TEMPERATURE,
        "reasoning_effort": effort or REASONING_EFFORT,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    resp = client.chat.completions.create(**kwargs)
    dt = time.perf_counter() - t0
    content = resp.choices[0].message.content or ""
    return content, dt


def _extract_label(text: str, vocab: list[str]) -> str | None:
    """Find the last lowercased vocab token in *text*; fall back to bold span."""
    t = (text or "").lower()
    bold = re.findall(r"\*\*([^*]+?)\*\*", t)
    if bold:
        last = bold[-1].strip().lower()
        for v in vocab:
            if v in last:
                return v
    positions = [(t.rfind(v), v) for v in vocab if v in t]
    positions = [p for p in positions if p[0] >= 0]
    if not positions:
        return None
    return max(positions)[1]


JUSTLOGIC_VOCAB = ["uncertain", "true", "false"]
BOARDGAME_VOCAB = ["disproved", "proved", "unknown"]
FOLIO_VOCAB = ["uncertain", "true", "false"]


def recon_justlogic() -> dict:
    ds = load_dataset("michaelchenkj/JustLogic", split="train")
    # Harder filter: depth >= 6 (paper's near-hardest; deeper than the prior depth>=4 recon
    # which landed at 52% with `uncertain` label-bias coast).
    rows = [r for r in ds if r["depth"] >= 6][:N_PER_CANDIDATE]
    system = (
        "You evaluate logical claims against given premises. "
        "Reply with exactly one word: TRUE, FALSE, or Uncertain."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, row in enumerate(rows):
        user = f"Premises:\n{row['paragraph']}\n\nClaim: {row['question']}\n\nIs the claim TRUE, FALSE, or Uncertain given the premises? Reply with one word."
        try:
            content, dt = call(system, user)
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", str(row["label"]).lower()))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)
        predicted = _extract_label(content, JUSTLOGIC_VOCAB)
        gold = str(row["label"]).lower()
        hit = predicted == gold
        if hit:
            hits += 1
        samples.append((hit, content[-200:], gold))
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} pred={predicted!r:>10} gold={gold!r}")
    return {"name": "JustLogic (depth>=6)", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def recon_exploretom() -> dict:
    ds = load_dataset("facebook/ExploreToM", split="train")
    # Non-adversarial slice per agent's recommendation
    rows = [r for r in ds if not r.get("sprop=is_false_belief_story_1st")][:N_PER_CANDIDATE]
    system = (
        "Answer the question about the story precisely and briefly. "
        "End your response with: Answer: <answer>"
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, row in enumerate(rows):
        user = f"Story:\n{row['infilled_story']}\n\nQuestion: {row['question']}"
        try:
            content, dt = call(system, user)
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", str(row["expected_answer"])))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)
        # Open-ended: look for the answer after "Answer:" else use last line.
        m = re.search(r"answer:\s*(.+?)(?:\.|$)", content.lower())
        predicted = (m.group(1).strip() if m else content.lower().splitlines()[-1].strip()).strip(".,!?")
        gold = str(row["expected_answer"]).lower().strip()
        hit = gold in predicted or predicted in gold
        if hit:
            hits += 1
        samples.append((hit, content[-200:], gold))
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} pred={predicted[:40]!r:>42} gold={gold[:40]!r}")
    return {"name": "ExploreToM (non-adversarial)", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def recon_boardgameqa() -> dict:
    ds = load_dataset("tasksource/Boardgame-QA", split="train")
    # Hardest filter: ManyDistractors-depth3 only. Prior depth-2/3 recon landed at 60% with
    # class-collapse (no `disproved` predictions); this strata adds distractors + caps depth.
    # Hardest available depth-3 config: 'Main-depth3' (1000 rows). 'ManyDistractors' only goes
    # to depth-2 in this dataset, so we substitute the depth-3 main config — same harder-strata
    # intent (depth-3 reasoning trees rather than depth-2).
    rows = [r for r in ds if str(r.get("config", "")) == "Main-depth3"][:N_PER_CANDIDATE]
    system = (
        "You evaluate whether a goal is provable from the given facts and rules. "
        "Reply with exactly one word: proved, disproved, or unknown."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, row in enumerate(rows):
        user = f"Game state:\n{row['example']}\n\nGoal: {row['goal']}\n\nIs the goal proved, disproved, or unknown? Reply with one word."
        try:
            content, dt = call(system, user)
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", str(row["label"]).lower()))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)
        predicted = _extract_label(content, BOARDGAME_VOCAB)
        gold = str(row["label"]).lower()
        hit = predicted == gold
        if hit:
            hits += 1
        samples.append((hit, content[-200:], gold))
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} pred={predicted!r:>12} gold={gold!r}")
    return {"name": "BoardgameQA (Main-depth3)", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def recon_popqa() -> dict:
    import json as _json
    from collections import defaultdict

    ds = load_dataset("akariasai/PopQA", split="test")
    # The naive "lowest s_pop globally" slice clusters by template — obscure
    # entities concentrate on a handful of (prop, obj) pairs (e.g. >50% of
    # the bottom quartile share `obj=Romania` for `prop=country_of_citizenship`).
    # The model then coasts by recognizing the question pattern rather than
    # recalling the answer. Stratify by `prop` (16 Wikidata relations) and
    # take the lowest-popularity subjects within each → balanced template
    # mix, no template-frequency coast, low-popularity signal preserved.
    by_prop: defaultdict[str, list] = defaultdict(list)
    for r in ds:
        by_prop[r["prop"]].append(r)
    # Ceil-divide so 25 samples across 16 props ⇒ 2 per prop (32 raw → cap to 25).
    per_prop = max(1, -(-N_PER_CANDIDATE // len(by_prop)))
    rows: list = []
    for prop in sorted(by_prop):
        bucket = sorted(by_prop[prop], key=lambda r: r["s_pop"] or 0)
        rows.extend(bucket[:per_prop])
    rows = rows[:N_PER_CANDIDATE]
    # Defensive: log obj/prop distribution so the operator can spot clustering.
    obj_counts = defaultdict(int)
    prop_counts = defaultdict(int)
    for r in rows:
        obj_counts[r["obj"]] += 1
        prop_counts[r["prop"]] += 1
    top_obj = sorted(obj_counts.items(), key=lambda x: -x[1])[:5]
    print(f"  stratified by prop (16 relations, {per_prop}/prop); "
          f"top-5 gold-obj: {top_obj}; #distinct props: {len(prop_counts)}")
    system = (
        "You answer factual questions. Reply with just the answer — a name, place, "
        "occupation, or short noun phrase. No explanation, no full sentence."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, row in enumerate(rows):
        try:
            content, dt = call(system, row["question"])
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", row["obj"]))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)
        aliases = _json.loads(row["possible_answers"])
        pred_norm = content.strip().strip(".").strip('"').lower()
        hit = any(
            a.lower() in pred_norm or pred_norm in a.lower()
            for a in aliases
            if a
        )
        if hit:
            hits += 1
        samples.append((hit, content[-200:], row["obj"]))
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} pred={pred_norm[:40]!r:>42} gold={row['obj'][:40]!r}")
    return {"name": "PopQA (low-pop subj quartile)", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def recon_satbench() -> dict:
    ds = load_dataset("LLM4Code/SATBench", split="train")
    # Harder slice: at least 5 variables AND 5 clauses (the dataset min is 2 vars / 1 clause).
    rows = [r for r in ds if r.get("num_vars", 0) >= 5 and r.get("num_clauses", 0) >= 5][:N_PER_CANDIDATE]
    system = (
        "You determine whether a set of natural-language conditions is jointly "
        "satisfiable. Reply with exactly one word: YES (satisfiable — some assignment "
        "makes all conditions true) or NO (unsatisfiable — no assignment works)."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, row in enumerate(rows):
        cond_lines = row["conditions"]
        if isinstance(cond_lines, list):
            cond_text = "\n".join(cond_lines)
        else:
            cond_text = str(cond_lines)
        user = (
            f"Scenario: {row['scenario']}\n\n"
            f"Variable meanings: {row['variable_mapping']}\n\n"
            f"Conditions:\n{cond_text}\n\n"
            f"Question: {row['question']}\n\n"
            f"Reply with YES or NO."
        )
        try:
            content, dt = call(system, user)
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", str(row["satisfiable"])))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)
        # Use last bold span (if any), else last YES/NO occurrence in lowercase.
        t = content.lower()
        bold = re.findall(r"\*\*([^*]+?)\*\*", t)
        last_chunk = (bold[-1] if bold else t).strip().strip(".,!?* ")
        if last_chunk.startswith("yes"):
            predicted_bool = True
        elif last_chunk.startswith("no"):
            predicted_bool = False
        else:
            yes_at = t.rfind("yes")
            no_at = t.rfind("no")
            predicted_bool = yes_at > no_at if (yes_at >= 0 or no_at >= 0) else None
        gold = bool(row["satisfiable"])
        hit = predicted_bool == gold
        if hit:
            hits += 1
        samples.append((hit, content[-200:], str(gold)))
        gold_str = "YES" if gold else "NO"
        pred_str = "YES" if predicted_bool is True else ("NO" if predicted_bool is False else "??")
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} pred={pred_str!r:>6} gold={gold_str!r} vars={row['num_vars']} clauses={row['num_clauses']}")
    return {"name": "SATBench (vars>=5, clauses>=5)", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def recon_mmluprox_sw() -> dict:
    from collections import defaultdict

    ds = load_dataset("li-lab/MMLU-ProX", "sw", split="test")
    # MMLU-ProX test rows are sorted by category. The naive [:N] slice is
    # always one category (e.g. 'business'). Stratify across the 14 MMLU-Pro
    # categories so the recon mirrors the eval distribution.
    by_cat: defaultdict[str, list] = defaultdict(list)
    for r in ds:
        by_cat[r.get("category") or "?"].append(r)
    per_cat = max(1, -(-N_PER_CANDIDATE // len(by_cat)))
    rows: list = []
    for cat in sorted(by_cat):
        rows.extend(by_cat[cat][:per_cat])
    rows = rows[:N_PER_CANDIDATE]
    print(f"  stratified by category ({len(by_cat)} categories, {per_cat}/cat): "
          + ", ".join(sorted(by_cat))[:120])
    system = (
        "You answer a multiple-choice question in Swahili. Read the question and "
        "options, then reply with exactly one letter (A-J) wrapped in double "
        "asterisks on its own line, e.g. **A**."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, row in enumerate(rows):
        options_block_lines = []
        for j in range(10):
            opt = row.get(f"option_{j}")
            if opt:
                options_block_lines.append(f"{chr(ord('A') + j)}) {opt}")
        user = f"Swali: {row['question']}\n\n" + "\n".join(options_block_lines)
        try:
            content, dt = call(system, user)
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", row["answer"]))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)
        bold = re.findall(r"\*\*([^*]+?)\*\*", content)
        predicted = None
        for span in reversed(bold):
            stripped = span.strip().upper().strip(".,!?")
            if len(stripped) == 1 and stripped.isalpha():
                predicted = stripped
                break
        if predicted is None:
            m = re.search(r"\b([A-J])\b", content.strip().split("\n")[-1].upper())
            predicted = m.group(1) if m else None
        gold = str(row["answer"]).upper()
        hit = predicted == gold
        if hit:
            hits += 1
        samples.append((hit, content[-200:], gold))
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} pred={predicted!r:>5} gold={gold!r} cat={row.get('category', '?')}")
    return {"name": "MMLU-ProX Swahili", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def recon_folio() -> dict:
    ds = load_dataset("tasksource/folio", split="train")
    rows = list(ds)[:N_PER_CANDIDATE]
    system = (
        "You evaluate whether a conclusion follows from the given premises. "
        "Reply with exactly one word: TRUE (conclusion provably follows), "
        "FALSE (conclusion provably contradicts the premises), or "
        "Uncertain (neither follows nor contradicts)."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, row in enumerate(rows):
        user = (
            f"Premises:\n{row['premises']}\n\n"
            f"Conclusion: {row['conclusion']}\n\n"
            f"Is the conclusion TRUE, FALSE, or Uncertain given the premises? Reply with one word."
        )
        try:
            content, dt = call(system, user)
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", str(row["label"]).lower()))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)
        predicted = _extract_label(content, FOLIO_VOCAB)
        gold = str(row["label"]).lower()
        hit = predicted == gold
        if hit:
            hits += 1
        samples.append((hit, content[-200:], gold))
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} pred={predicted!r:>10} gold={gold!r}")
    return {"name": "FOLIO (full train)", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def _recon_bbeh(effort: str) -> dict:
    """BBEH mini at gpt-oss-20b @ chosen reasoning_effort. No max_tokens override —
    we want to see what the wired-style call actually produces, not synthetic ceilings.

    Operator note 2026-05-19: don't trust the public ~14% floor reference; measure
    in-house at the meta-campaign-relevant effort settings (low + medium).
    """
    ds = load_dataset("BBEH/bbeh", split="train")
    rows = [r for r in ds if r.get("mini")][:N_PER_CANDIDATE]
    system = (
        "You solve a reasoning puzzle from BIG-Bench Extra Hard (BBEH). "
        "Read the problem, reason through it, and commit to exactly one final answer. "
        "End your response with the final answer wrapped in double asterisks on its own line, e.g. **unknown** or **42**."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, row in enumerate(rows):
        try:
            content, dt = call(system, row["input"], effort=effort)
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", row["target"]))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)
        bold = re.findall(r"\*\*([^*]+?)\*\*", content or "")
        predicted = (bold[-1] if bold else (content or "")).strip().lower()
        gold = str(row["target"]).strip().lower()
        gold_bold = re.findall(r"\*\*([^*]+?)\*\*", row["target"])
        if gold_bold:
            gold = gold_bold[-1].strip().lower()
        # Normalize parenthesized letter MC golds: `(a)` → `a`, since the model emits
        # `**a**` not `**(a)**` for that BBEH subtask family.
        predicted = predicted.strip("()[] ")
        gold = gold.strip("()[] ")
        hit = predicted == gold
        if hit:
            hits += 1
        samples.append((hit, (content or "")[-200:], gold))
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} pred={predicted[:30]!r:>32} gold={gold[:30]!r}")
    return {"name": f"BBEH mini @ 20b/{effort}", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def recon_bbeh_low() -> dict:
    return _recon_bbeh("low")


def recon_bbeh_medium() -> dict:
    return _recon_bbeh("medium")


if __name__ == "__main__":
    print(f"\n=== Recon: {MODEL} @ {REASONING_EFFORT}, temperature={TEMPERATURE}, N={N_PER_CANDIDATE} each ===\n")
    results = []
    # Scrapped (with measured numbers in dataset-selection-rationale.md):
    #   recon_popqa            — per-prop gold-skew coast (rock/Christianity/USA modal answers).
    #   recon_satbench         — 13/13 HITs at vars>=5 & clauses>=5; ceiling at every filter.
    #   recon_mmluprox_sw      — 36% but Swahili-comprehension signal not reasoning; operator wants English.
    #   recon_folio            — 80% on :nitro at low; ceiling.
    #   recon_bbeh_high        — empty-output trap at reasoning_effort=high on 20b (trace exhausts budget).
    #   recon_justlogic d>=6   — 44% in-band BUT hedge-bias; held as candidate.
    #   recon_boardgameqa d3   — 64% ceiling + class-collapse persists; fast/cheap, held for later.
    for fn in (recon_bbeh_low, recon_bbeh_medium):
        print(f"\n--- {fn.__name__} ---")
        results.append(fn())

    print("\n\n=== SUMMARY ===")
    print(f"{'Dataset':<40} {'N':>4} {'Hits':>6} {'Acc':>8} {'Latency':>10}")
    for r in results:
        in_band = "  in-band" if 0.15 <= r["acc"] <= 0.45 else (" CEILING" if r["acc"] > 0.45 else "   FLOOR")
        print(f"{r['name']:<40} {r['n']:>4} {r['hits']:>6} {r['acc']*100:>6.1f}% {r['mean_latency']:>8.1f}s {in_band}")
