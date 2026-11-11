// No read is asked on a clock: the server's feed says what moved (`read-cache.ts`). These are the
// clocks left: a failed read's retry and the browser's own timings.

// A failed read that passes re-asks on it, a refused feed reopens on it, and the session probe
// retries on it.
export const RECONNECT_POLL_MS = 5000;

export const ELAPSED_TICK_MS = 1000;
export const MASK_LENS_DEBOUNCE_MS = 250;
export const GONE_NOTICE_MS = 8000;
// REACHABLE, hence `role="note"` rather than tooltip; WCAG 1.4.13 applies.
export const HOVER_CLOSE_GRACE_MS = 160;
export const COPY_FAILURE_NOTICE_MS = 5000;
export const COPIED_FLASH_MS = 1600;
