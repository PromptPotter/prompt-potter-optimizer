# Chat foundation — the first-class front door

> **Only the open half lives here.** Arc 1 (curated activity + loop control) shipped: its rules are [`../../webapp/CLAUDE.md`](../../webapp/CLAUDE.md) § The chat thread — rendered, never authored, the reusable core and its delete-list are [`../../webapp/components/chat/README.md`](../../webapp/components/chat/README.md), and the transport is [`events-asyncapi.yaml`](events-asyncapi.yaml). Read [`../architecture.md`](../architecture.md) §0 first.

## Positioning

The first tab **is a chat**, and — deliberately — an LLM wrapper. We own that rather than dress it up: it is the operator's front door to the Potter, a **human-in-the-loop copilot**, and a canonical agent-chat template that happens to drive an optimizer. Three things converge in **one ordered thread** — conversation (the operator talks to an assistant answering from campaign context), activity (the Potter posts what it is doing, Perplexity-style: the same stream the CLI writes to the terminal), and decisions (when a choice is warranted, the copilot raises **inline buttons**).

**The copilot cannot mutate state directly.** A button click *is* the action, and each button is a thin trigger for a control-plane command that already exists.

## Arc 2 — the conversational endpoint, open

The user ↔ assistant round-trip does not exist; chat input is disabled outside ingest.

**Served by the `checkin` optimizer node** — the check-in copilot IS the conversation surface, there is no separate `chat` node, so its provider/model lives per-node in `promptpotter/assets/optimizer/pipeline.yaml` and NOT in `campaign.json`, which carries no `optimizer_llm.provider`. The assistant gets **no new tools**; it answers from context and the live stream.

**A new HTTP surface, so it is declared in [`api-openapi.yaml`](api-openapi.yaml) first, in its own PR, before the handler lands** (the §0 schema-first gate). It is **not** a `/commands/{kind}` verb — it does not mutate cycle state — and its reply rides the endpoint's own response, touching no asyncapi event kind.

**Buttons add no command, and "declared" ≠ "wired".** The openapi declares commands ahead of their handlers by charter, so only `commands.py::_WIRED_KINDS` plus the four typed routes resolve; anything else 404s `command_kind_unknown`. Check that set before promising a button — an earlier draft of this spec advertised `endorse-candidate` as live on the strength of the yaml alone, and it has no handler.

**Persistence extends the check-in thread, campaign-scoped.** The thread is not a new concept beside the campaign; it *is* the check-in, continued — one durable thread per campaign, stored where the check-in artifacts already live. **No new store:** extend the existing message model and reuse the ingest draft-sync channel. The thread never gates the loop.

**No new I/O kind.** Assistant reply + buttons + activity items are **Display**, a click is **Control-remote** inbound, and the endpoint is a sanctioned non-command conversation surface, peer to `POST /datasets/ingest`. Build order: declare the endpoint, then the endpoint on the `checkin` node, then persistence.

## Deferred — assistant tool-use

The "Soon" toggles are Extended thinking · Web search · Code execution, with MCP a planned fourth. Each renders as a generic **step**; only a *richer per-tool field set* needs a new `ProjectionKind`, declared in [`events-asyncapi.yaml`](events-asyncapi.yaml) first. Backend (TermNorm) tool activity surfaces today only at the granularity the existing `snapshot` / `token_usage` records carry.
