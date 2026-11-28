"""Hash every derived file (index.json, dashboard.json, rounds/round_NNNN.json, readout.log) under two
offline-world homes and diff them: raw bytes, then with the run's own entropy masked (the campaign dir's
random suffix, ISO timestamps, epoch floats, durations) so what remains is what the ledger alone does not fix.

    python probes/hash_derived.py HOME_A HOME_B [--show KIND]
"""

import difflib
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path

KINDS = {"index.json": "index", "dashboard.json": "dashboard", "readout.log": "readout"}
SUFFIX = re.compile(r"__[0-9a-f]{6,12}\b")
MASKS = [
    (SUFFIX, "__X"),
    (re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(\+00:00|Z)?"), "<ts>"),
    (re.compile(r"\b1[7-9]\d{8}(\.\d+)?\b"), "<epoch>"),
    (re.compile(r'"(duration_s|wall_clock_s|elapsed_s|[a-z_]*_seconds|[a-z_]*_s)": -?\d+(\.\d+)?(e-?\d+)?'), r'"\1": <dur>'),
    (re.compile(r"\b\d+(\.\d+)?s\b"), "<dur>s"),
    (re.compile(r"\b\d+m ?\d+s\b"), "<dur>"),
]


def kind(p: Path) -> str | None:
    if p.name in KINDS:
        return KINDS[p.name]
    if p.parent.name == "rounds" and re.fullmatch(r"round_\d+\.json", p.name):
        return "round"
    return None


def masked(text: str) -> str:
    for pat, rep in MASKS:
        text = pat.sub(rep, text)
    return text


def _campaign_names(home: Path) -> dict[Path, str]:
    """A campaign's dir carries a random suffix: two runs pair on the name before it, then on the
    order the world minted its campaigns (the earliest file each holds)."""
    groups: dict[str, list[tuple[float, Path]]] = {}
    for d in home.rglob("campaigns/*__*"):
        if d.is_dir() and SUFFIX.search(d.name):
            born = min((f.stat().st_mtime for f in d.rglob("*") if f.is_file()), default=0.0)
            groups.setdefault(SUFFIX.sub("__X", str(d.relative_to(home))), []).append((born, d))
    return {
        d: f"{name}{rank}" for name, held in groups.items() for rank, (_, d) in enumerate(sorted(held))
    }


def files(home: Path) -> dict[str, tuple[str, bytes]]:
    out: dict[str, tuple[str, bytes]] = {}
    names = _campaign_names(home)
    for p in sorted(home.rglob("*")):
        if p.is_file() and (k := kind(p)):
            owner = next((d for d in p.parents if d in names), None)
            rel = str(p.relative_to(home)) if owner is None else names[owner] + str(p)[len(str(owner)) :]
            assert rel not in out, f"two campaigns pair on {rel}"
            out[rel] = (k, p.read_bytes())
    return out


def main() -> None:
    a, b = files(Path(sys.argv[1])), files(Path(sys.argv[2]))
    show = sys.argv[sys.argv.index("--show") + 1] if "--show" in sys.argv else None
    tot, raw, norm = Counter(), Counter(), Counter()
    first: dict[str, tuple[str, list[str]]] = {}
    for rel in sorted(set(a) | set(b)):
        k = (a.get(rel) or b[rel])[0]
        tot[k] += 1
        if rel not in a or rel not in b:
            first.setdefault(k, (rel, [f"ONLY IN {'A' if rel in a else 'B'}"]))
            continue
        x, y = a[rel][1], b[rel][1]
        raw[k] += hashlib.sha256(x).digest() == hashlib.sha256(y).digest()
        mx, my = masked(x.decode()), masked(y.decode())
        if mx == my:
            norm[k] += 1
        elif k not in first or (show == k and len(first[k][1]) < 2):
            diff = [
                line
                for line in difflib.unified_diff(mx.splitlines(), my.splitlines(), lineterm="", n=0)
                if line[:1] in "+-" and line[:3] not in ("+++", "---")
            ]
            first[k] = (rel, diff[:6])
    print(f"{'kind':10s} {'files':>5s} {'raw-equal':>9s} {'masked-equal':>12s}")
    for k in sorted(tot):
        print(f"{k:10s} {tot[k]:5d} {raw[k]:9d} {norm[k]:12d}")
    for k, (rel, diff) in sorted(first.items()):
        print(f"--- first masked difference, {k}: {rel}")
        for line in diff:
            print("    " + line[:220])


if __name__ == "__main__":
    main()
