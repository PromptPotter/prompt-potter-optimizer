// Shared callback contract for the ingest flow.

// What the ingest flow hands back when it mints a campaign. Every mint goes
// through `mint-campaign-from-draft`, which returns the new (campaign, cycle)
// synchronously, so the caller can select it at once.
export type MintedSelection = { campaignId: string; cycleId: string };

export type OnMinted = (selection: MintedSelection) => void;
