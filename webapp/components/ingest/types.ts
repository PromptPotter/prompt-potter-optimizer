// `start-checkin` returns the (campaign, cycle) synchronously, so the caller can select it at once.
type MintedSelection = { campaignId: string; cycleId: string };

export type OnMinted = (selection: MintedSelection) => void;
