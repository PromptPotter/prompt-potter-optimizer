"use client";

import { useMemo, useState } from "react";
import type { NodeConfigParam } from "@/lib/api";
import { nodeConfigRows, seedOverlayFromRows } from "@/lib/derivations/nodeConfigRows";

// The FULL node-config editor for the operator-steered fork. Edits the flat
// `{node:{param:value}}` overlay the fork is seeded with, driven by the server's
// `node_config_schema` (built from `pipeline.json`): model (a <select> of
// `available_models`), temperature, reasoning_effort / thinking (a <select> of
// allowed values), max_tokens, and any other backend tunable the node declares.
// Model/provider are shown (with an "optimizer-locked" hint) — the operator may
// set them on a fork even though the optimizer can't. Values seed from the
// candidate's overlay, falling back to the node's current config. Emits the
// sparse overlay on every edit so the parent's confirm reads the latest.
//
// `readOnly` renders the same rows with disabled inputs — the read-only node
// detail (a stopped cycle's hero click, no candidate) reuses this so the
// inspect-the-node and steer-the-node surfaces read identically.
export function NodeConfigEditor({
  schema,
  seedOverlay,
  onChange,
  readOnly = false,
}: {
  schema: Record<string, NodeConfigParam[]> | null;
  seedOverlay: Record<string, unknown>;
  onChange?: (overlay: Record<string, Record<string, unknown>>) => void;
  readOnly?: boolean;
}) {
  // Rows are pure-derived from the schema + the candidate overlay; both are
  // stable for the life of a steer (the cycle is stopped/paused), so memo on
  // identity.
  const rows = useMemo(() => nodeConfigRows(schema, seedOverlay), [schema, seedOverlay]);
  const [edits, setEdits] = useState<Record<string, string>>({});

  const set = (key: string, v: string) => {
    const next = { ...edits, [key]: v };
    setEdits(next);
    onChange?.(seedOverlayFromRows(rows, next));
  };

  if (rows.length === 0) {
    return (
      <div className="node-config-editor">
        <span className="node-config-title">Model &amp; parameters</span>
        <small className="node-config-empty">
          {readOnly
            ? "This node declares no configurable params."
            : "No editable node params — the fork inherits the dataset config."}
        </small>
      </div>
    );
  }

  return (
    <div className="node-config-editor">
      <span className="node-config-title">Model &amp; parameters</span>
      <ul className="node-config-list">
        {rows.map((r) => {
          const key = `${r.node}.${r.param}`;
          const value = key in edits ? edits[key] : r.value;
          const isSelect = r.kind === "model" || r.kind === "enum";
          return (
            <li key={key} className="node-config-row" title={r.description || undefined}>
              <code className="node-config-key">
                {r.param}
                {r.fromCandidate ? (
                  <span className="node-config-evolved" title="Carried from this searchpoint">
                    ·evolved
                  </span>
                ) : null}
                {r.optimizerLocked ? (
                  <span
                    className="node-config-locked"
                    title="The optimizer can't change this — but you can, on this fork."
                  >
                    optimizer-locked
                  </span>
                ) : null}
              </code>
              {isSelect ? (
                <select
                  className="node-config-input"
                  value={value}
                  disabled={readOnly}
                  aria-label={`${r.node} ${r.param}`}
                  onChange={(e) => set(key, e.target.value)}
                >
                  {/* The seeded value may sit outside the declared set (the
                      candidate evolved past it) — keep it selectable so the
                      editor never silently rewrites it. */}
                  {!r.options.includes(value) && value !== "" ? (
                    <option value={value}>{value} (current)</option>
                  ) : null}
                  {r.options.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              ) : r.kind === "bool" ? (
                <input
                  type="checkbox"
                  className="node-config-check"
                  checked={value === "true"}
                  disabled={readOnly}
                  aria-label={`${r.node} ${r.param}`}
                  onChange={(e) => set(key, e.target.checked ? "true" : "false")}
                />
              ) : (
                <input
                  type={r.kind === "number" ? "number" : "text"}
                  inputMode={r.kind === "number" ? "decimal" : undefined}
                  className="node-config-input"
                  value={value}
                  disabled={readOnly}
                  placeholder={r.kind === "number" ? "inherit" : undefined}
                  aria-label={`${r.node} ${r.param}`}
                  onChange={(e) => set(key, e.target.value)}
                />
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
