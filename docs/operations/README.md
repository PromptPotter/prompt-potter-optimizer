# Operations

Running, integrating, and operating PromptPotter.

| Page | Covers |
|------|--------|
| [Access model](access-model.md) | **The security map an audit opens** — the three tiers (admin / user / loop), the three boundary kinds, every enforcement point by symbol, and the Linux-box deploy checklist |
| [Backend integration](backend-integration.md) | Contract a backend must implement (`/matches`, `/pipeline`, `/status`) and PromptPotter's REST API |
| [Persistence and state](persistence-and-state.md) | The `.promptpotter/` tree, active session, cycle directory, `new` / `resume` flags, resume / rewind / fork / sweep, human-in-the-loop steer & fork + pause, scoring steer (env vars live in [`../manual/02-install.md`](../manual/02-install.md#environment-variables)) |
| [Observability](observability.md) | What gets traced, Langfuse integration, P(best) stream, display conventions |
| [The mask](mask-projection.md) | How far a change to the criterion — a formula, a PoBB setting, the engine — reaches before the record stops carrying over, and where you have to fork instead. The `lens` params and the `ab` verb |
| [Linux deploy](../../deploy-linux/README.md) | systemd + Cloudflare Tunnel + OIDC + free-tier ceiling |
| [Secure hosting](secure-hosting.md) | Blocking an account from Telegram via the on-box admin bot; secret hygiene; the no-inbound-door rule |

New to PromptPotter? Start in [`../manual/`](../manual/README.md).
