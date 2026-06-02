"use client";

import type { NodeOutputSchema } from "@/lib/api";

// Read-only view of a node's structured-output contract (from the server's
// `node_output_schema`, resolved from `pipeline.json`). Shown beside the config
// + prompt so the operator sees the WHOLE node as one unit: model + params +
// prompt + the structured output it produces. Lists each output field with its
// description; nodes with no schema are skipped. Read-only by definition — the
// output contract is a backend fact, not an operator knob.
export function NodeOutputSchemaView({
  schema,
}: {
  schema: Record<string, NodeOutputSchema | null> | null;
}) {
  const entries = Object.entries(schema ?? {}).filter(
    (e): e is [string, NodeOutputSchema] => e[1] != null && e[1].fields.length > 0,
  );
  if (entries.length === 0) return null;

  return (
    <div className="node-output-schema">
      <span className="node-output-title">Structured output</span>
      {entries.map(([node, out]) => (
        <dl key={node} className="node-output-list" aria-label={`${node} output schema`}>
          {out.fields.map((field) => (
            <div key={field} className="node-output-row">
              <dt className="node-output-field">
                <code>{field}</code>
              </dt>
              {out.field_descriptions[field] ? (
                <dd className="node-output-desc">{out.field_descriptions[field]}</dd>
              ) : null}
            </div>
          ))}
        </dl>
      ))}
    </div>
  );
}
