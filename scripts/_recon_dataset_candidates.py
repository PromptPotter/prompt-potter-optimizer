"""Throwaway: measure 25-sample origin on three candidate datasets.

Fires `openai/gpt-oss-20b @ reasoning_effort=low` via Groq directly
(bypasses PromptPotter's optimization loop — just need raw origin
numbers to decide whether each candidate lands in the 30-40% recon
band). Mirrors what the dataset's `pipeline.json` would request, so
the projection is faithful.

Candidates (current driver — NaturalPlan + MuSiQue, 2026-05-19 colleague triage):
- google/natural-plan             — 3-subtask planning (trip / calendar / meeting)
- dgslibisey/MuSiQue              — multi-hop QA stratified by 2/3/4-hop

Prior candidates retained in this file (measured numbers in
`docs/operations/dataset-selection-rationale.md`):
- michaelchenkj/JustLogic         — 3-class TRUE/FALSE/Uncertain (WIRED d>=6)
- facebook/ExploreToM             — open-ended QA (non-adversarial slice)
- tasksource/Boardgame-QA         — 3-class proved/disproved/unknown (depth-2/3)
- akariasai/PopQA, LLM4Code/SATBench, li-lab/MMLU-ProX, tasksource/folio, BBEH/bbeh

Run from repo root with `.env` populated (OPENROUTER_API_KEY or GROQ_API_KEY):
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


def _load_naturalplan_subtask(subtask: str) -> list[dict]:
    """Fetch a NaturalPlan subtask from google-deepmind/natural-plan@main, with local cache.

    NaturalPlan is NOT on the HF Hub — Google publishes the data as raw JSON in
    the github.com/google-deepmind/natural-plan repo. Each file is a dict keyed
    by `{subtask}_example_{i}`, ~6-24 MB. We cache under `.cache/natural-plan/`
    so re-runs are instant.
    """
    import json as _json
    import pathlib
    import urllib.request

    cache_dir = pathlib.Path(".cache/natural-plan")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{subtask}.json"
    if not cache_file.exists():
        url = f"https://raw.githubusercontent.com/google-deepmind/natural-plan/main/data/{subtask}.json"
        print(f"  [{subtask}] downloading {url} -> {cache_file} ...")
        with urllib.request.urlopen(url) as resp:
            cache_file.write_bytes(resp.read())
    with cache_file.open("r", encoding="utf-8") as fh:
        data = _json.load(fh)
    # Top-level is dict {example_key: example_dict}; preserve insertion order.
    return list(data.values())


def recon_naturalplan() -> dict:
    """NaturalPlan (Google DeepMind 2024) — three planning subtasks stratified.

    Three subtask JSON files: `trip_planning`, `calendar_scheduling`,
    `meeting_planning`. Free-form plan emission (no class-hedge surface),
    structural grading per the paper. We stratify across the three so
    per-subtask origin is readable directly from the per-sample print.

    Scorer is COARSE for recon: token-overlap with `golden_plan` >= 0.7.
    Wire-time would replace with Google's published structural evaluator
    (path validation, calendar arithmetic). A token-overlap miss with low
    coverage is genuinely a miss; a hit at 0.7+ means the model emitted
    enough of the right plan content to be worth measuring properly.
    """
    subtasks = ("trip_planning", "calendar_scheduling", "meeting_planning")
    per_subtask = max(1, -(-N_PER_CANDIDATE // len(subtasks)))

    rows: list[tuple[str, dict]] = []
    first_keys_logged = False
    for subtask in subtasks:
        try:
            examples = _load_naturalplan_subtask(subtask)
        except Exception as e:
            print(f"  [{subtask}] LOAD ERROR {type(e).__name__}: {str(e)[:120]}")
            continue
        if not first_keys_logged and examples:
            print(f"  [{subtask}] first-example keys: {list(examples[0].keys())} (n={len(examples)})")
            first_keys_logged = True
        for r in examples[:per_subtask]:
            rows.append((subtask, r))
    rows = rows[:N_PER_CANDIDATE]

    print(f"  stratified across {len(subtasks)} subtasks ({per_subtask}/subtask) -> {len(rows)} total")

    system = (
        "You are a planning agent. Read the planning problem carefully and "
        "emit a plan that satisfies every stated constraint. Work the problem "
        "step-by-step. End your response with the final plan in plain text — "
        "no commentary after the plan."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, (subtask, row) in enumerate(rows):
        prompt = row.get("prompt_0shot") or row.get("prompt") or row.get("question") or ""
        gold = row.get("golden_plan") or row.get("answer") or ""
        if not prompt or not gold:
            samples.append((False, "[SKIP - missing prompt/gold]", str(gold)[:200]))
            print(f"  [{i+1:02d}]   SKIP   missing field; keys={list(row.keys())}  subtask={subtask}")
            continue
        try:
            content, dt = call(system, str(prompt))
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", str(gold)[:200]))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)
        pred_text = (content or "").lower()
        # Per-subtask scorer dispatch — the three subtasks have radically different
        # gold shapes (49-char string / list / 300-char string), so a single scorer
        # is unfair. Each function returns (hit_bool, score_str_for_print).
        if subtask == "calendar_scheduling":
            # Gold like "Here is the proposed time: Monday, 14:30 - 15:30 ".
            # Token-overlap is broken here (all golds share the same 4 boilerplate
            # tokens, time slot itself is unscoreable by alphanumeric regex).
            # Real signal: does the model emit the right day-of-week + time range?
            gold_str = str(gold).lower()
            day_m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", gold_str)
            time_m = re.findall(r"\d{1,2}:\d{2}", gold_str)
            day_ok = bool(day_m) and day_m.group(1) in pred_text
            times_ok = all(t in pred_text for t in time_m) if time_m else False
            hit = day_ok and times_ok
            score_str = f"day={day_ok} times={times_ok} ({len(time_m)} req)"
        elif subtask == "meeting_planning":
            # Gold is a list of waypoint strings; join then token-overlap >=0.7.
            gold_text = "\n".join(gold) if isinstance(gold, list) else str(gold)
            gold_tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", gold_text.lower()))
            overlap = (
                sum(1 for t in gold_tokens if t in pred_text) / len(gold_tokens)
                if gold_tokens
                else 0.0
            )
            hit = overlap >= 0.7
            score_str = f"overlap={overlap:.2f}"
        else:
            # trip_planning: 200-500 char string with 10-15 content tokens. Original
            # 0.7 overlap bar.
            gold_tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", str(gold).lower()))
            overlap = (
                sum(1 for t in gold_tokens if t in pred_text) / len(gold_tokens)
                if gold_tokens
                else 0.0
            )
            hit = overlap >= 0.7
            score_str = f"overlap={overlap:.2f}"
        if hit:
            hits += 1
        samples.append((hit, (content or "")[-200:], str(gold)[:200]))
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} {score_str:<28}  subtask={subtask}")
    return {"name": "NaturalPlan (3-subtask, per-subtask scorer)", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def recon_arlsat() -> dict:
    """AR-LSAT (Analytical Reasoning from LSAT, via AGIEval) — 5-option MC.

    230 test rows. Each problem is a constraint-satisfaction puzzle (e.g.
    seven students scheduling reports across days/times under constraints)
    with 5 candidate full-solution options. Frontier ~70%; smaller models
    typically land 30-50%. Deterministic MC scoring. Distinct L1 surface
    from anything wired today: constraint propagation + option scanning.

    Source: hails/agieval-lsat-ar
    """
    import ast

    ds = load_dataset("hails/agieval-lsat-ar", split="test")
    rows = list(ds)[:N_PER_CANDIDATE]
    print(f"  first-row keys: {list(rows[0].keys())} (n_total={len(ds)})")

    system = (
        "You solve an LSAT analytical-reasoning problem. Read the problem and "
        "the 5 candidate answers, identify which option satisfies all stated "
        "constraints, and end your response with the answer letter wrapped in "
        "double asterisks on its own line, e.g. **A** or **C**."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, row in enumerate(rows):
        # `choices` is a native list of "(A)..." strings (HF stores it as-is;
        # `ast.literal_eval` would only apply if it were a stringified list).
        raw_choices = row["choices"]
        if isinstance(raw_choices, list):
            choices = raw_choices
        else:
            try:
                choices = ast.literal_eval(raw_choices)
            except Exception:
                choices = []
        choices_block = "\n".join(choices) if isinstance(choices, list) else str(choices)
        user = f"{row['query']}\n\n{choices_block}"

        # `gold` is a native single-element list like `[2]`. Map to letter.
        try:
            raw_gold = row["gold"]
            gold_val = ast.literal_eval(raw_gold) if isinstance(raw_gold, str) else raw_gold
            gold_idx = gold_val[0] if isinstance(gold_val, list) else gold_val
            gold_letter = chr(ord("A") + int(gold_idx))
        except Exception:
            gold_letter = "?"

        try:
            content, dt = call(system, user)
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", gold_letter))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)

        # Parse last bold single letter A-E; fall back to last-line single letter.
        bold = re.findall(r"\*\*([^*]+?)\*\*", content or "")
        predicted = None
        for span in reversed(bold):
            stripped = span.strip().upper().strip(".,!?()[] ")
            if len(stripped) == 1 and stripped.isalpha() and stripped in "ABCDE":
                predicted = stripped
                break
        if predicted is None:
            last_line = (content or "").strip().split("\n")[-1].upper()
            m = re.search(r"\b([A-E])\b", last_line)
            predicted = m.group(1) if m else None

        hit = predicted == gold_letter
        if hit:
            hits += 1
        samples.append((hit, (content or "")[-200:], gold_letter))
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} pred={predicted!r:>5} gold={gold_letter!r}")
    return {"name": "AR-LSAT (AGIEval, 5-option MC)", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def recon_planbench() -> dict:
    """PlanBench task_1_plan_generation — multi-domain symbolic planning.

    PDDL-style planning across multiple domains (blocksworld, logistics, plus
    obfuscated variants where action names are nonsense words like `paltry`,
    `sip`, `wretched` — designed to test reasoning vs pattern-matching).
    Gold is a sequence of action calls like `(sip o8 o1 o7)\\n(memory o1 o7 o6)`.

    Scorer: extract action calls `(...)` from gold, HIT iff >=50% appear in
    the model output. Order-agnostic and coarse — wire-time would replace
    with PDDL plan-validator. A 50% bar is fair for these short plans
    (typically 3-7 action calls).

    Source: tasksource/planbench, config task_1_plan_generation
    """
    from collections import defaultdict

    ds = load_dataset("tasksource/planbench", "task_1_plan_generation", split="train")
    by_domain: defaultdict[str, list] = defaultdict(list)
    for r in ds:
        by_domain[str(r.get("domain", "?"))].append(r)
    domains = sorted(by_domain.keys())
    per_domain = max(1, -(-N_PER_CANDIDATE // len(domains)))
    rows: list[dict] = []
    for d in domains:
        rows.extend(by_domain[d][:per_domain])
    rows = rows[:N_PER_CANDIDATE]

    print(f"  first-row keys: {list(ds[0].keys())}")
    print(f"  stratified by domain ({len(domains)} domains, {per_domain}/domain) -> {len(rows)} total")
    print(f"  domains present: {domains}")

    system = (
        "You solve a symbolic planning problem. Read the available actions "
        "and their preconditions/effects, the initial state, and the goal. "
        "Emit a sequence of action calls (one per line) that achieves the "
        "goal. Format each action as `(action_name arg1 arg2 ...)`. End "
        "your response with the final plan only — no commentary after."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, row in enumerate(rows):
        try:
            content, dt = call(system, row["query"])
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", str(row.get("ground_truth_plan", ""))[:200]))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)

        gold_plan = str(row.get("ground_truth_plan", ""))
        gold_actions = re.findall(r"\([^)]+\)", gold_plan)
        pred_text = (content or "").lower()
        if gold_actions:
            overlap = sum(1 for a in gold_actions if a.lower() in pred_text) / len(gold_actions)
        else:
            overlap = 0.0
        hit = overlap >= 0.5
        if hit:
            hits += 1
        samples.append((hit, (content or "")[-200:], gold_plan[:200]))
        domain_tag = str(row.get("domain", "?"))[:30]
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} overlap={overlap:.2f}  "
              f"actions={len(gold_actions)}  domain={domain_tag}")
    return {"name": "PlanBench task_1 (multi-domain, overlap>=0.5)", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


def recon_musique() -> dict:
    """MuSiQue (AI2 2022) — multi-hop QA stratified by 2/3/4-hop.

    Reading-comprehension multi-hop — paragraphs supplied in user prompt to
    sidestep PopQA-style tail-entity-recall failure (colleague's flag). Public
    mirror used: `dgslibisey/MuSiQue` (the AI2 original is gated on HF).

    Scorer: substring match against `answer` + `answer_aliases` (same shape
    as recon_popqa, line 248). Coarse but consistent across all 25.
    """
    from collections import defaultdict

    ds = load_dataset("dgslibisey/MuSiQue", split="train")
    print(f"  first-row keys: {list(ds[0].keys())}")

    by_hop: defaultdict[str, list] = defaultdict(list)
    for r in ds:
        # Filter to answerable rows (some mirrors include unanswerable variants).
        if r.get("answerable") is False:
            continue
        # Hop count from `id` prefix: '2hop__...', '3hop1__...', '4hop1__...'.
        m = re.match(r"(\d+)hop", str(r.get("id", "")))
        hop = f"{m.group(1)}hop" if m else "unknown"
        by_hop[hop].append(r)

    hops_present = sorted([h for h in by_hop if h != "unknown"])
    if not hops_present:
        # Fallback: no parseable hop prefix; treat whole set as one bucket.
        hops_present = list(by_hop.keys()) or ["unknown"]
    per_hop = max(1, -(-N_PER_CANDIDATE // len(hops_present)))
    rows: list[tuple[str, dict]] = []
    for hop in hops_present:
        for r in by_hop[hop][:per_hop]:
            rows.append((hop, r))
    rows = rows[:N_PER_CANDIDATE]

    print(f"  stratified by hop ({len(hops_present)} buckets: {hops_present}, "
          f"{per_hop}/hop) -> {len(rows)} total")

    system = (
        "You answer a multi-hop question using ONLY the provided paragraphs. "
        "Do not use outside knowledge. Reply with just the answer — a short "
        "noun phrase, name, or number. No explanation."
    )
    hits = 0
    latencies: list[float] = []
    samples: list[tuple[bool, str, str]] = []
    for i, (hop, row) in enumerate(rows):
        paras = row.get("paragraphs") or []
        if isinstance(paras, list):
            para_lines = []
            for p in paras:
                if isinstance(p, dict):
                    title = p.get("title", "")
                    text = p.get("paragraph_text", "")
                    para_lines.append(f"[{title}] {text}")
                else:
                    para_lines.append(str(p))
            paras_text = "\n\n".join(para_lines)
        else:
            paras_text = str(paras)

        question = row.get("question", "")
        user = f"Paragraphs:\n{paras_text}\n\nQuestion: {question}"
        # Guard rather than silently truncate. ~32k chars ~= 8k tokens leaves
        # room for system prompt + reasoning trace under the 16k context budget.
        if len(user) > 32000:
            samples.append((False, "[SKIP - context too long]", str(row.get("answer", ""))))
            print(f"  [{i+1:02d}]   SKIP   context={len(user)} chars  hop={hop}")
            continue

        try:
            content, dt = call(system, user)
        except Exception as e:
            samples.append((False, f"[ERROR] {e}", str(row.get("answer", ""))))
            print(f"  [{i+1:02d}]   ERROR  {type(e).__name__}: {str(e)[:80]}")
            continue
        latencies.append(dt)

        gold = str(row.get("answer", "")).strip()
        aliases = row.get("answer_aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        candidates = [gold] + [str(a) for a in aliases if a]
        pred_norm = (content or "").strip().strip(".").strip('"').lower()
        hit = any(
            (c.lower() in pred_norm or pred_norm in c.lower())
            for c in candidates
            if c
        )
        if hit:
            hits += 1
        samples.append((hit, (content or "")[-200:], gold))
        print(f"  [{i+1:02d}] {dt:5.1f}s {'HIT ' if hit else 'MISS'} "
              f"pred={pred_norm[:40]!r:>42} gold={gold[:40]!r}  hop={hop}")
    return {"name": "MuSiQue (hop-stratified, RC w/ paragraphs)", "n": len(rows), "hits": hits,
            "acc": hits / max(1, len(rows)), "mean_latency": sum(latencies) / max(1, len(latencies)),
            "samples": samples}


if __name__ == "__main__":
    print(f"\n=== Recon: {MODEL} @ {REASONING_EFFORT}, temperature={TEMPERATURE}, N={N_PER_CANDIDATE} each ===\n")
    results = []
    # Scrapped (with measured numbers in dataset-selection-rationale.md):
    #   recon_popqa            — per-prop gold-skew coast (rock/Christianity/USA modal answers).
    #   recon_satbench         — 13/13 HITs at vars>=5 & clauses>=5; ceiling at every filter.
    #   recon_mmluprox_sw      — 36% but Swahili-comprehension signal not reasoning; operator wants English.
    #   recon_folio            — 80% on :nitro at low; ceiling.
    #   recon_bbeh_high        — empty-output trap at reasoning_effort=high on 20b (trace exhausts budget).
    #   recon_justlogic d>=6   — 44% in-band, WIRED 2026-05-19 as primary L1 meta-campaign dataset.
    #   recon_boardgameqa d3   — 64% ceiling + class-collapse persists; fast/cheap, held for later.
    #   recon_bbeh_low/medium  — low 28% in-band but trace-dominated; medium 44% at 24.7s latency.
    #   recon_naturalplan      — 36% macro in-band but Frankenstein avg: trip=0%, calendar=67%, meeting=43%.
    #   recon_musique          — 60% overall CEILING; per-hop 2hop=89%, 3hop=38%, 4hop=57%.
    # Current driver: AR-LSAT + PlanBench (last recon set 2026-05-19).
    for fn in (recon_arlsat, recon_planbench):
        print(f"\n--- {fn.__name__} ---")
        results.append(fn())

    print("\n\n=== SUMMARY ===")
    print(f"{'Dataset':<40} {'N':>4} {'Hits':>6} {'Acc':>8} {'Latency':>10}")
    for r in results:
        in_band = "  in-band" if 0.15 <= r["acc"] <= 0.45 else (" CEILING" if r["acc"] > 0.45 else "   FLOOR")
        print(f"{r['name']:<40} {r['n']:>4} {r['hits']:>6} {r['acc']*100:>6.1f}% {r['mean_latency']:>8.1f}s {in_band}")
