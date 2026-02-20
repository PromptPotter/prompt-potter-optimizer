# TermNorm Pipeline Comparison: Variant A vs Variant B

- **Variant A:** LLM1-TokenMatching (No LLM2 reranking)
- **Variant B:** LLM1-TokenMatching-LLM2 (Full pipeline with LLM2 semantic reranking)
- **Queries:** 40
- **Session terms:** 93

## Accuracy

| Metric | Variant A | Variant B | p-value |
|--------|-----------|-----------|---------|
| Accuracy (exact match) | 9/40 (22.5%) | 2/40 (5.0%) | McNemar p=0.0233 |

## Recall

| Metric | Variant A | Variant B |
|--------|-----------|-----------|
| Recall@3 | 15/40 (37.5%) | n/a |
| Recall@5 | 24/40 (60.0%) | n/a |
| MRR | 0.3635 | n/a |

## Latency

| Metric | Variant A | Variant B | p-value |
|--------|-----------|-----------|---------|
| Mean | 13707ms | 25817ms | Wilcoxon p=0.0000 |
| Median | 14122ms | 26610ms | |
| P95 | 18769ms | 40720ms | |

## Per-Query Classification

- **Both correct:** 2
- **A-only correct (LLM2 hurt):** 7
- **B-only correct (LLM2 helped):** 0
- **Both wrong:** 31


### Where LLM2 Hurt

- **EN AW-AL99,5 H14/cold forming**
  - GT: `Aluminium, wrought alloy {GLO}| market for aluminium, wrought alloy | Cut-off, S`
  - A: `Aluminium, wrought alloy {GLO}| market for aluminium, wrought alloy | Cut-off, S`
  - B: `Metal working, average for aluminium product manufacturing {RER}| metal working, average for aluminium product manufacturing | Cut-off, S`
- **PA66-GF25 Ultramid A3UG5 black 23215 RAL 9005/molding**
  - GT: `Glass fibre reinforced plastic | 75% PA66 25% GF /RER`
  - A: `Glass fibre reinforced plastic | 75% PA66 25% GF /RER`
  - B: `Injection moulding {RER}| injection moulding | Cut-off, S`
- **PA66-GF25 ULTRAMID A3UG5 RAL7035 grey/molding**
  - GT: `Glass fibre reinforced plastic | 75% PA66 25% GF /RER`
  - A: `Glass fibre reinforced plastic | 75% PA66 25% GF /RER`
  - B: `Injection moulding {RER}| injection moulding | Cut-off, S`
- **PC/ABS Cycoloy C2950 RAL7035/molding**
  - GT: `Cycoloy C2950 /GLO`
  - A: `Cycoloy C2950 /GLO`
  - B: `Injection moulding {RER}| injection moulding | Cut-off, S`
- **PMC ISO 14530-UP (GF10+MD65),M,FR Ralupol UP 804 7035.00 M/Q E 8.4/molding**
  - GT: `Glass fibre reinforced plastic, polyester resin, hand lay-up {GLO}| market for glass fibre reinforced plastic, polyester resin, hand lay-up | Cut-off, S`
  - A: `Glass fibre reinforced plastic, polyester resin, hand lay-up {GLO}| market for glass fibre reinforced plastic, polyester resin, hand lay-up | Cut-off, S`
  - B: `Injection moulding {RER}| injection moulding | Cut-off, S`
- **Round EN 10277-3-11SMn30+C/cold forming**
  - GT: `Steel, low-alloyed {GLO}| market for steel, low-alloyed | Cut-off, S`
  - A: `Steel, low-alloyed {GLO}| market for steel, low-alloyed | Cut-off, S`
  - B: `Metal working, average for steel product manufacturing {RER}| metal working, average for steel product manufacturing | Cut-off, S`
- **Sleeving IEC 60684-3-123 silicone elastomer/extrude**
  - GT: `Silicone product {RER}| market for silicone product | Cut-off, S`
  - A: `Silicone product {RER}| market for silicone product | Cut-off, S`
  - B: `Extrusion, plastic pipes {RER}| extrusion, plastic pipes | Cut-off, S`
