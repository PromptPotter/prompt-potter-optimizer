import type { NodeConfigParam, NodeOutputSchema } from "@/lib/api";
import type { ReadResult } from "@/lib/hooks/useRead";

export interface NodeSchemaReading {
  // Never infer the read's state from a null map: an in-flight read and an empty node both hold no rows.
  read: ReadResult<unknown>;
  config: Record<string, NodeConfigParam[]> | null;
  output: Record<string, NodeOutputSchema | null> | null;
  // The chain's one node, served (`single_node`): never counted off the rows or the view, which
  // hold the whole running set, its phases included. Null on a longer chain.
  singleNode: string | null;
}
