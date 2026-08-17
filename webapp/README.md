The PromptPotter dashboard — a read-only Next.js app served at the domain root by
FastAPI, with the API as the carved-out `/api/v1` namespace.

**How to run, build and test it lives in [`CLAUDE.md`](CLAUDE.md) § Build + run and
§ Testing posture** — this file deliberately does not restate them. Short version:
`npm run dev` for visual work (:3000, HMR, proxies `/api/*` to :8001),
`python scripts/gate.py --web` from the repo root for the checks CI runs.

Layer contracts — scoring authority, display-data sources, the one viewed address —
are all in `CLAUDE.md`. The surface behaviour contract it is measured against is
[`../docs/specs/frontend-surface-contract.md`](../docs/specs/frontend-surface-contract.md).
