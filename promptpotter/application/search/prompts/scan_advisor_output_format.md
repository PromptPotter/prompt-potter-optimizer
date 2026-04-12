## Output Format
For pipeline_param axes, suggest concrete values that meaningfully differ from current_values. For prompt_field axes, just flag them — the variant library already has values.

Return a JSON object with this structure:
{{
  "priority_axes": [
    {{
      "axis": "<param_name>",
      "source": "pipeline_param",
      "node": "<node_name>",
      "rationale": "...",
      "suggested_values": ["<val>", "..."],
      "importance": "<high|medium|low>"
    }},
    {{
      "axis": "<field_name>",
      "source": "prompt_field",
      "rationale": "...",
      "importance": "<high|medium|low>"
    }}
  ],
  "suggested_n_diagnostic": 6,
  "axes_to_skip": [
    {{"axis": "<name>", "reason": "..."}}
  ],
  "budget_breakdown": {{
    "<axis_name>": <n_queries>,
    "total": <sum>
  }},
  "reasoning": "Overall strategy explanation..."
}}

Rules:
- rationale: max 15 words
- reasoning: max 2 sentences
- axes_to_skip reason: max 10 words
- suggested_values: 2-4 values, numbers or short strings only
- importance: "high", "medium", or "low"
- source: "pipeline_param" or "prompt_field"
- For pipeline_param axes: include "node" and "suggested_values"
- CRITICAL: For pipeline_param axes, "axis" must be an EXACT key from the Tunable Parameters param_keys above. Do NOT invent names or combine node names with param names — copy the key exactly as listed.
- For prompt_field axes: omit "node" and "suggested_values"
- *_schema mutations: each suggested_value is a JSON array of mutation arrays. Ops: ["-","path"] remove | ["+","path","type",required,"desc"] add | ["~","old","new","type",required,"desc"] replace. Types: string|array|integer|number|boolean|object. required: true|false. Baseline included automatically — do NOT include it. Keep each variant to 1-2 mutations so individual effects are measurable.

Return ONLY the JSON object, no markdown fences or extra text.