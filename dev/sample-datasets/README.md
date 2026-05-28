# `dev/sample-datasets/` — checked-in demo CSVs

Hand-crafted miniature datasets used as drag-drop fodder for the M13 chat-first ingest flow (see [`docs/specs/m13-chat-first-user-web.md § Ingest`](../../docs/specs/m13-chat-first-user-web.md#ingest)). Precedent: [`dev/oidc-local/`](../oidc-local/) Dex harness — operator-facing fixtures live under `dev/` and are checked in. Drag `customer-tickets-eval.csv` onto the webapp ChatPane drop zone to exercise the slice-1 `POST /datasets/ingest` path end-to-end against a deterministic 5-row Origin.
