import type { NodeOutputSchema, NodeConfigParam } from "@/lib/api/types";
import type { ReadResult } from "@/lib/read-cache";

export interface NodeSchemaReading {
  // Never infer the read's state from a null map: an in-flight read and an empty node both hold no rows.
  read: ReadResult<unknown>;
  config: Record<string, NodeConfigParam[]> | null;
  output: Record<string, NodeOutputSchema | null> | null;
  // Served (`is_single_node`), never counted off the rows: they cover every DECLARED node.
  isSingleNode: boolean;
}
