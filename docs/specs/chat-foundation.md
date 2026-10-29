# Chat foundation — the first-class front door

> **Only the open half lives here.** The built thread's rules are [`../../webapp/CLAUDE.md`](../../webapp/CLAUDE.md) § The chat thread — rendered, never authored; the reusable core and its delete-list are [`../../webapp/components/chat/README.md`](../../webapp/components/chat/README.md); the transport is [`events-asyncapi.generated.json`](events-asyncapi.generated.json).

**The copilot cannot mutate state directly.** A button click *is* the action, and each button is a thin trigger for a control-plane command that already exists.

## Arc 2 — the conversational endpoint, open

The user ↔ assistant round-trip does not exist; chat input is disabled outside ingest.

**Served by the `checkin` optimizer node** — the check-in copilot IS the conversation surface, there is no separate `chat` node, so its provider/model lives in the bench's `promptpotter/assets/checkin/pipeline.yaml` and NOT in `campaign.json`, which carries no `optimizer_llm.provider`. The assistant gets **no new tools**; it answers from context and the live stream.

**A new HTTP surface, so its request and response models are declared before the handler lands** (the §0 schema-first rule); the generated spec carries them. It is **not** a `/commands/{kind}` verb — it does not mutate cycle state — and its reply rides the endpoint's own response, touching no asyncapi event kind.

**Buttons add no command.** A kind exists exactly when its `domain/command_kinds.py::CommandKind` row names a handler; check that roster before promising a button.

**Persistence extends the check-in thread, campaign-scoped.** The thread *is* the check-in, continued — one durable thread per campaign, stored where the check-in artifacts already live. **No new store:** extend the existing message model and reuse the ingest draft-sync channel. The thread never gates the loop.

**No new I/O kind.** Assistant reply + buttons + activity items are **Display**, a click is **Control-remote** inbound, and the endpoint is a sanctioned non-command conversation surface, peer to `POST /datasets/ingest`. Build order: declare the endpoint, then the endpoint on the `checkin` node, then persistence.

## Open

- **The empty thread is a generic chat:** wordmark, composer, a row of starter chips (HuggingChat's new-chat screen is the reference). The pipeline hero (`ChatPane.tsx` `.wf-hero` → `PipelineStack`) mounts only once a dataset is bound, and then compact.
- **Assistant tool-use (deferred):** Extended thinking · Web search · Code execution, MCP a planned fourth. Each renders as a generic **step**; only a richer per-tool field set needs a new `ProjectionKind`, declared on its record model first.
