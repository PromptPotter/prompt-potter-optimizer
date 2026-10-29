# Developer — how the engine is wired

Implementation notes for seams no single file shows. What every change is placed on is
[`../architecture.md`](../architecture.md) §0 + §0.5; how the loop works is [`../concepts/`](../concepts/).

```bash
python scripts/gate.py                 # once first — it builds .venv from uv.lock
git config core.hooksPath .githooks    # once per clone
python scripts/gate.py --changed       # while editing
```

| Page | Owns |
|---|---|
| [conventions](conventions.md) | Style, code shape, naming, git |
| [dispatch-hub](dispatch-hub.md) | How an optimizer prompt is filled: injections, bounds, the L1 layout, L2 |
| [self-healing-internals](self-healing-internals.md) | Which failure reaches which layer |
| [node-standard](node-standard.md) | The `pipeline.yaml` contract and the node types |
| [adding-a-surface](adding-a-surface.md) | Every recipe: a record, an injection, a field, a verb, a command, a read |
| [stable-api](stable-api.md) | What an outside program may rely on |
| [event-stream](event-stream.md) | The SSE contract |
| [offline-run](offline-run.md) | Proving a change does not move what the loop decides |
| [dspy-optimizer](dspy-optimizer.md) | PromptPotter as a DSPy optimizer |
