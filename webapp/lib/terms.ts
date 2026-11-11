// Operator vocabulary for in-product tooltips — one short sentence each; longer goes to docs/manual/.


export const TERMS = {
  criterion_accuracy: "Whether the answer is right, as the matcher grades it. Every dial below scales this — a dial at 0 is off.",

  brand_live_preview: "This page re-reads dashboard.json whenever the server says the run moved.",

  masthead_best: "The run's selection against its origin: both rates on the ORIGIN's panel, over the cells the two both answered — the one pair the two may be compared on. Where the run stands as of its last closed round, never a high-water mark; a round's own accuracy is read on the subset that round bought, so this can sit above or below every round on the chart. '—' while the selection is the origin, or until it has been read against it.",
  remote_flight: "Calls the round has out now, how many its stop rules allow right now, and the most it could hold if nothing were cut — every candidate walking plus the parent's catch-ups. Between rounds, the most the next round could hold. Results are taken in order, so a slow call at a candidate's head holds the round; once one has run long it is named below.",
} satisfies Record<string, string>;
