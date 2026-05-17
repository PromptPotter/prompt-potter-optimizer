"""One-shot: set ``reasoning_effort: high`` on every optimizer node config.

Per operator authorization, the OPTIMIZER LLM (L1/L2/L3/critique meta-prompts)
runs at ``reasoning_effort: high`` for sharper axis selection and divergence
reasoning. This does NOT touch the job-search-point / target-pipeline node
config — that stays at the dataset overlay's setting (e.g. AIME's
``llm_only`` stays where ``datasets/aime_2025/pipeline.json::nodes.llm_only.config``
places it).

The optimizer's manifest at ``datasets/_optimizer/pipeline.json`` carries
each node's static ``config`` dict; ``llm_call`` merges that into the
chat request params via ``request_params.update(kwargs)``. Setting the
key there propagates to OpenAI/OpenRouter SDK calls without any code
threading — the same pattern AIME's overlay uses for the target side.
"""

import json
from pathlib import Path

P = Path("datasets/_optimizer/pipeline.json")
data = json.loads(P.read_text(encoding="utf-8"))

nodes = data.get("nodes", {})
assert isinstance(nodes, dict) and nodes, "expected `nodes` dict in optimizer pipeline.json"

changed: list[str] = []
for name, node in nodes.items():
    cfg = node.setdefault("config", {})
    before = cfg.get("reasoning_effort")
    cfg["reasoning_effort"] = "high"
    if before != "high":
        changed.append(f"{name}: {before!r} -> 'high'")

P.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"ok ({len(nodes)} optimizer nodes)")
for line in changed:
    print(f"  {line}")
if not changed:
    print("  (no change — every node already at reasoning_effort=high)")
