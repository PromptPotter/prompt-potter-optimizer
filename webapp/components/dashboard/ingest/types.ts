// Shared callback contract for the ingest flows.

// What the ingest flow hands back when it mints a campaign. A fresh-CSV /
// from-draft mint returns the new (campaign, cycle) synchronously, so the
// caller can select it at once. The existing-Origin and benchmark paths
// dispatch the runner via a command ack with no ids yet → `null`; the caller
// falls back to the 2 s workspace poll, which snaps to the new cycle. The
// previous `("", "")` empty-id sentinel is gone — absence is `null`, not "".
export type MintedSelection = { campaignId: string; cycleId: string } | null;

export type OnMinted = (selection: MintedSelection) => void;
