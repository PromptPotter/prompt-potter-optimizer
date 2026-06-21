# Operations

Running, integrating, and operating PromptPotter.

| Page | Covers |
|------|--------|
| [Backend integration](backend-integration.md) | Contract a backend must implement (`/matches`, `/pipeline`, `/status`) and PromptPotter's REST API |
| [Persistence and state](persistence-and-state.md) | The `.promptpotter/` tree, active session, cycle directory, `new` / `resume` flags, resume / rewind / fork / sweep, scoring steer (env vars live in [`../manual/02-install.md`](../manual/02-install.md#environment-variables)) |
| [Observability](observability.md) | What gets traced, Langfuse integration, P(best) stream, display conventions |
| [Human in the loop](human-in-the-loop.md) | HITL = the operator-steered fork (CLI `resume --fork-on-divergence` + webapp Steer & fork); collapses into the fork primitive, no separate I/O kind |
| [Linux deploy](../../deploy-linux/README.md) | systemd + Cloudflare Tunnel + OIDC + allowlist |
| [Secure hosting](secure-hosting.md) | Managing the sign-in allowlist from Telegram via the on-box admin bot; secret hygiene; the no-inbound-door rule |

New to PromptPotter? Start in [`../manual/`](../manual/README.md).
