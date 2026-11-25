"""From a cycle ledger: per round, the parent's scored rows that no rival answered that round, fresh vs cached.

Usage: parent_only.py <ledger.jsonl>...
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

for arg in sys.argv[1:]:
    recs = [json.loads(line) for line in Path(arg).read_text(encoding="utf-8").splitlines() if line.strip()]
    scored = [r for r in recs if r.get("record_type") == "sample_scored"]
    if scored:
        top = sorted(k for k in scored[-1] if k not in ("result",))
        res = sorted(scored[-1]["result"].keys())
    by = defaultdict(lambda: defaultdict(dict))  # round -> (ind, role) -> key -> cached
    for r in scored:
        cached = r.get("cached", r["result"].get("cached"))
        by[r["round"]][(r["individual_id"], r.get("role"))][r["result"]["sample_key"]] = cached
    print(f"== {Path(arg).parents[2].name}")
    print("   fields:", top, "| result:", [k for k in res if k in ("cached", "replayed", "sample_key")])
    tot = {"po": 0, "po_fresh": 0, "fresh": 0}
    for rnd in sorted(by):
        arms = by[rnd]
        parents = [a for a in arms if a[1] == "parent"]
        line = []
        for p in parents:
            rivals = set().union(*(set(v) for a, v in arms.items() if a != p))
            only = {k: c for k, c in arms[p].items() if k not in rivals}
            fresh = sum(1 for c in only.values() if not c)
            tot["po"] += len(only)
            tot["po_fresh"] += fresh
            line.append(f"parent {p[0][:6]}: {len(arms[p])} rows, {len(only)} no rival read ({fresh} fresh)")
        tot["fresh"] += sum(1 for v in arms.values() for c in v.values() if not c)
        print(f"   round {rnd}: " + "; ".join(line) + f" | rivals {[len(v) for a, v in arms.items() if a[1] != 'parent']}")
    print(f"   TOTAL parent rows no rival read {tot['po']} ({tot['po_fresh']} fresh) of {tot['fresh']} fresh cells")
