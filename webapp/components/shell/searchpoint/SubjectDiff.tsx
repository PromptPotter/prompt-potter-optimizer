import type { ReactNode } from "react";
import { FailureLine } from "@/components/ui";
import type { Evidence } from "@/lib/api/types";
import type { ObserveConfig } from "@/lib/derivations";
import type { OriginChanges } from "@/lib/hooks/useOriginEvidence";
import type { ReadResult } from "@/lib/read-cache";
import { useConnector } from "@/lib/hooks/useConnector";
import { promptFieldLabel } from "@/lib/prompt-fields";
import { NodeSurface } from "@/components/shell/node-surface/NodeSurface";

export function SubjectDiff({
  cfg,
  changes,
  read,
  verdict,
}: {
  cfg: ObserveConfig;
  changes: OriginChanges;
  read: ReadResult<Evidence>;
  verdict: ReactNode;
}) {
  const cv = useConnector();
  return (
    <details className="subject-diff">
      <summary>
        <span className="subject-diff-label">{cfg.label}</span>
        <span className="subject-diff-changes">
          <Changes changes={changes} read={read} />
        </span>
        {verdict}
      </summary>
      <NodeSurface
        node={null}
        point={{ origin_prompts: cfg.prompts, pipeline_overlay: {} }}
        overlay={cfg.config}
        schema={cv.schema}
        mode="values"
        compact
      />
    </details>
  );
}

function Changes({ changes, read }: { changes: OriginChanges; read: ReadResult<Evidence> }) {
  if (changes === null) {
    return read.status === "failed" ? (
      <FailureLine failure={read.failure} lead="The origin could not be compared." />
    ) : (
      "comparing to origin…"
    );
  }
  switch (changes.status) {
    case "unrecorded":
      return "no configuration is recorded to compare";
    case "compared":
      return changes.keys.length === 0
        ? "identical to the origin you submitted"
        : chips(changes.keys).map((c) => <DiffChip key={c.node ?? ""} chip={c} />);
  }
}

// The served key names its keyspace: a bare name is a prompt field, `node.param` a node's config.
interface Chip {
  node: string | null;
  names: string[];
}

function chips(keys: readonly string[]): Chip[] {
  const byNode = new Map<string | null, string[]>();
  for (const key of keys) {
    const dot = key.indexOf(".");
    const node = dot < 0 ? null : key.slice(0, dot);
    const name = dot < 0 ? promptFieldLabel(key) : key.slice(dot + 1);
    byNode.set(node, [...(byNode.get(node) ?? []), name]);
  }
  return [...byNode].map(([node, names]) => ({ node, names }));
}

function DiffChip({ chip }: { chip: Chip }) {
  return (
    <span
      className="subject-diff-group"
      aria-label={`${chip.node ?? "prompt"} changed: ${chip.names.join(", ")}`}
    >
      <span className="subject-diff-key" aria-hidden="true">
        {chip.node === null ? "✎" : "⚙"}
      </span>
      {chip.node ? (
        <span className="subject-diff-node" aria-hidden="true">
          {chip.node}
        </span>
      ) : null}
      <span aria-hidden="true">{chip.names.join(", ")}</span>
    </span>
  );
}
