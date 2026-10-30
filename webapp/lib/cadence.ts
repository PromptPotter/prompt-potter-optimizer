// Every poll interval is a multiple of the beat.
export const READ_BEAT_MS = 1000;

export const DASHBOARD_LIVE_POLL_MS = 2000;
export const DASHBOARD_RECONNECT_POLL_MS = 5000;
// Matches the dashboard's live beat, so a CLI-minted cycle is followed without the registry's lag.
export const ACTIVE_POINTER_POLL_MS = 2000;
export const REGISTRY_POLL_MS = 10000;
export const LINEAGE_POLL_MS = 5000;
export const CELLS_LIVE_POLL_MS = 8000;
export const CONNECTOR_HEALTH_POLL_MS = 5000;
export const MACHINE_STATUS_POLL_MS = 5000;
export const LIVE_EVIDENCE_MS = 30000;
// The quota read sums every ledger the account owns: polled slowly, re-ticked when spend moves.
export const QUOTA_POLL_MS = 60_000;
export const VERIFY_PASS_POLL_MS = 5000;

export const ELAPSED_TICK_MS = 1000;
export const MASK_LENS_DEBOUNCE_MS = 250;
export const GONE_NOTICE_MS = 8000;
// REACHABLE, hence `role="note"` rather than tooltip; WCAG 1.4.13 applies.
export const HOVER_CLOSE_GRACE_MS = 160;
export const COPY_FAILURE_NOTICE_MS = 5000;
export const COPIED_FLASH_MS = 1600;
