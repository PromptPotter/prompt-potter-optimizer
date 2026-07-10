// Shared callback contract for the ingest flow.

// What the ingest flow hands back when it starts a campaign. Every Start goes
// through `start-checkin`, which returns the (campaign, cycle) synchronously,
// so the caller can select it at once.
type MintedSelection = { campaignId: string; cycleId: string };

export type OnMinted = (selection: MintedSelection) => void;
