"""Pair the two homes' ledgers (campaign suffix and fork id masked, campaigns ranked by mint order) and
report which ledgers differ in length or in their record-type sequence, and the first type that differs."""
import json, re, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from hash_derived import _campaign_names  # noqa: E402
FORK = re.compile(r"_fork_[0-9a-f]{8}")
def ledgers(home):
    names, out = _campaign_names(home), {}
    for p in sorted(home.rglob("ledger.jsonl")):
        owner = next((d for d in p.parents if d in names), None)
        rel = names[owner] + str(p)[len(str(owner)):] if owner else str(p.relative_to(home))
        rel = FORK.sub("_fork_F", rel)
        n = 0
        while f"{rel}#{n}" in out: n += 1
        out[f"{rel}#{n}"] = [json.loads(l)["record_type"] for l in p.read_text().splitlines() if l.strip()]
    return out
a, b = ledgers(Path(sys.argv[1])), ledgers(Path(sys.argv[2]))
same = 0; diffs = Counter()
for k in sorted(set(a) | set(b)):
    x, y = a.get(k), b.get(k)
    if x == y: same += 1; continue
    if x is None or y is None: print("UNPAIRED", k); continue
    i = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
    ca, cb = Counter(x), Counter(y)
    delta = {t: cb[t] - ca[t] for t in set(ca) | set(cb) if ca[t] != cb[t]}
    for t in delta: diffs[t] += 1
    print(f"{k.split('/data/')[0]:28s} {k.split('cycles/')[1][:34]:34s} len {len(x)}/{len(y)} first@{i} {x[i] if i < len(x) else '-'}/{y[i] if i < len(y) else '-'} counts{delta}")
print(f"ledgers paired={len(set(a) & set(b))} identical type sequence={same}; types whose count moves (ledgers): {dict(diffs)}")
