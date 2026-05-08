# M12 — New Job status bar

**Status:** v1 view-only shipped 2026-05-08 (`webapp/components/dashboard/ChatPane.tsx`,
the `chat-job-bar` block). Interactive piece pending.

## v1 (shipped)

Thin bar above the chat panel on the New Job tab. Collapsed shows
`dataset · Best · Round · Spend · Budget · Δ/$`. Chevron expands inline
(chat shifts down) into a four-section read-only panel. Spend chips
read `dashboard.json::spend` — render `—` until
[`m11-spend-tracking.md`](m11-spend-tracking.md) lands the aggregator.

## v2 direction (interactive — M12 Track 3)

Each adjustable row in the expanded panel gains a left-side checkbox:
unchecked = current value; checked = stage an override. Two footer
buttons:

- **Apply on next round** — POSTs to a new
  `POST /api/v1/campaigns/{cycle_id}/overrides`. Runner picks up the
  override at the next round boundary, no mid-round mutation.
- **Save to campaign.json** — same POST with `persist: true`; writes
  back to the dataset config.

Adjustable rows v2 ships with: `max_rounds`, `improvement_threshold`,
`spend_budget_usd`, `l1/l2/l3_patience`. Optimizer LLM swap stays
read-only mid-run.

## Not a launcher

This bar adjusts an existing run. Full new-campaign launcher (dataset
picker, command picker, scan-variant editor) is a separate surface in
M12 Track 3.

## Anchors

- v1 component: `webapp/components/dashboard/ChatPane.tsx`.
- v1 styles: `webapp/app/globals.css` — `.chat-job-*`.
- Schema dependency: `dashboard.json::spend` (see m11-spend-tracking.md).
- Config schema: `promptpotter/application/config.py::OptimizationConfig`.
