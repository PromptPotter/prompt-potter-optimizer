# LCA Terminology Normalization

Map BOM (Bill of Materials) entries — raw material names and manufacturing process descriptions from industrial parts lists — to standardized LCA database terms (ecoinvent, GaBi).

## Domain

- Input: free-text material/process names from engineering BOMs (e.g., "PA6-GF30", "Spritzgiessen", "SJRG0013-PA/molding")
- Output: standardized LCA database entry (e.g., "glass fibre reinforced plastic", "injection moulding")
- Challenge: abbreviations, trade names, Werkstoff numbers, mixed languages (DE/EN), ambiguous process descriptions

## Success criteria

- Top-ranked candidate matches the ground truth dataset_entry
- Scoring: hit@1 (is the correct term ranked first?)

## Key failure modes

- Trade name not decoded (e.g., "Makrolon" → polycarbonate)
- Process vs material confusion (e.g., "molding" as material vs manufacturing step)
- Language barriers (German technical terms)
- Overly specific matches (missing the generic LCA category)
