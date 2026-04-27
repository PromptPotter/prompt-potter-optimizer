# Developer

Implementation spec. Python class names, method signatures, module paths, and JSON schemas live here. Each page assumes you're reading the code alongside the docs.

| Page | What it covers |
|------|----------------|
| [Code layout](code-layout.md) | Hexagonal package layout, three-layer I/O rule, shared libraries |
| [Information flow](information-flow.md) | Data contract — what each optimizer layer reads and writes, inbox registry, retention lifecycle |
| [Node standard](node-standard.md) | Node capabilities, JSON declaration format, wiring a new node |
| [Prompt scheme internals](prompt-scheme-internals.md) | `PromptTemplate`, two prompt stores, rendering pipeline, `PROMPT_STRING_FIELDS` |
| [Search memory internals](search-memory-internals.md) | Accessor catalog, digest API, consumer mapping, watermark refresh |
| [Self-healing internals](self-healing-internals.md) | `ValidationFailure` vs `RuntimeFailure`, `classify_result()`, escalation wiring |
| [Display conventions](display-conventions.md) | `⚠ … ↳` rendering contract, entry-point adoption |
| [Code map](code-map.md) | Alphabetical Python symbol → file:line index |

Looking for concepts without code? See [`../concepts/`](../concepts/README.md).
