# Suffix-Hash Cache

PromptPotter runs the same dataset through many near-identical pipeline configurations. Caching intermediate node outputs across runs is the single biggest lever on wall-clock cost. This document describes the cache design and why the current scheme replaced an inferior predecessor.

## Prior Scheme: Prefix-Chain Cache (Deprecated)

The original `IntermediateCache` keyed each node's output by a **chained hash**: `key_i = hash(node_i, config_i, key_{i-1})`. Lookup walked the chain left-to-right (`walk_prefix`) and stopped at the first miss, handing the cached upstream outputs to the backend as `precomputed` so only uncached nodes ran.

This was inferior in three ways:

1. **Upstream-only reuse.** Changing any node's config cascaded invalidation through every downstream key. Changing node D re-hashed the chain from D onward — fine — but changing node A re-hashed *everything*, losing B, C, and D even though their configs were identical.
2. **Sequential O(n) lookup.** Every query paid n disk reads in the best case (full hit), walking the chain one node at a time.
3. **No symmetry.** The scheme could answer "is the prefix through node i cached?" but not "given output_i, is the tail from here cached?" — which is the natural question when the backend streams intermediates.

## Current Scheme: Suffix-Hash Cache

One flat KV store, keyed by `suffix_key(input, [node_configs...])`. After every pipeline run we emit cache entries at **every cut point** of the pipeline — both partial prefixes from the query and partial tails from each intermediate output. Because the backend already returns all `node_outputs` in a single response, populating every cut point costs only hashing.

### Core Insight

Since the stored result already contains all node outputs
(`{out_A, out_B, out_C, out_D}`), you get partial reuse for
free:

Scenario: config_D changes to D'

1. Look up `suffix_key(q, A, B, C, D')` — miss (D' is new)
2. Look up `suffix_key(q, A, B, C)` — hit! → gives you out_C
3. Now compute `suffix_key(out_C, D')` — run only node D'

The key insight: you don't only store full-tail
suffixes. You store suffixes at **every cut point**, including
partial tails. So from one pipeline run you emit:

```
suffix_key(q,     A)          → {out_A}
suffix_key(q,     A, B)       → {out_A, out_B}
suffix_key(q,     A, B, C)    → {out_A, out_B, out_C}
suffix_key(q,     A, B, C, D) → {out_A, out_B, out_C, out_D}
suffix_key(out_A, B)          → {out_B}
suffix_key(out_A, B, C)       → {out_B, out_C}
suffix_key(out_A, B, C, D)    → {out_B, out_C, out_D}
...etc
```

### Complexity

- **Populate:** `n(n+1)/2` entries per query per run. For a realistic n=5–6 node pipeline, that is 15–21 entries — all derived from a single already-completed backend response by local hashing.
- **Lookup (identical pipeline):** O(1). A single hash check on the full-pipeline suffix key.
- **Lookup (one node changed):** O(1)–O(n), typically 1–2 checks. Either a prefix up to the changed node hits, or a tail from the unchanged upstream hits.
- **Lookup (backend streams intermediates):** O(1) per intermediate. As soon as the backend emits `out_i`, we check `suffix_key(out_i, tail)` and can short-circuit the remaining pipeline mid-call.

### Why It Subsumes the Prefix Chain

Every query the old scheme could answer, the suffix-hash scheme answers with a single lookup instead of an O(n) walk — *and* it additionally answers the symmetric question (is the tail cached given an intermediate?), enabling mid-call short-circuiting and reuse across upstream config changes that the old scheme discarded entirely.

## Implementation Pointer

The cache lives at `promptpotter/services/store/suffix_cache.py` and is injected via `ProjectStore.suffix_cache`. Its only consumer is `measure_sample()` in `promptpotter/application/scoring/sample_measurement.py` (lookup before the backend call, populate from the response). `DatasetRunStore` is a separate concern — it archives full dataset runs for SearchMemory, observability, and lineage, and is not part of the per-query cache path.

The suffix cache replaced the prior `IntermediateCache` during M9 as part of the pre-publication architecture cleanup.
