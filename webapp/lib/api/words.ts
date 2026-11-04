import { WORD_ORDER } from "./types.generated";

// A served word table's keys in its display order, as scripts/build_ts_types.py exports it beside
// each `shared/worded.py::Worded` (`WORD_ORDER`): an object's own order would put integer-like keys
// first. The one walk over a table, never a local copy.
export function wordKeys<K extends string>(table: Partial<Record<K, unknown>>): K[] {
  const order = WORD_ORDER.get(table);
  if (order === undefined) throw new Error("wordKeys: not a served word table");
  return order as K[];
}

export function wordRows<K extends string, V>(table: Partial<Record<K, V>>): [K, V][] {
  // Every key of `WORD_ORDER` is one of the table's own.
  return wordKeys(table).map((k) => [k, table[k] as V]);
}
