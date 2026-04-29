# Developer

Implementation spec. Python class names, method signatures, module paths, and JSON schemas live here. Each page assumes you're reading the code alongside the docs.

| Page | What it covers |
|------|----------------|
| [Code layout](code-layout.md) | Hexagonal package layout, three-layer I/O rule, shared libraries |
| [Information flow](information-flow.md) | Data contract — what each optimizer layer reads and writes, surface registry, retention lifecycle |
| [L2 internals](l2-internals.md) | When L2 fires, `L2Surface` it sees, the flat-dict output it parses, OSP mutations it commits |
| [L1-generate surface internals](l1-generate-surface.md) | `L1GenerateField` registry, `L1GenerateSurface` dataclass, override application order |
| [Node standard](node-standard.md) | Node capabilities, JSON declaration format, wiring a new node |
| [Prompt scheme internals](prompt-scheme-internals.md) | `PromptTemplate`, two prompt stores, rendering pipeline, `PROMPT_STRING_FIELDS` |
| [Axis index internals](axis-index-internals.md) | Accessor catalog, digest API, consumer mapping, refresh mechanics |
| [Self-healing internals](self-healing-internals.md) | `ValidationFailure` vs `RuntimeFailure`, `classify_result()`, escalation wiring |
| [Display conventions](display-conventions.md) | `⚠ … ↳` rendering contract, entry-point adoption |
| [Code map](code-map.md) | Alphabetical Python symbol → file:line index |

**Understanding L2 (contributor track):**
1. [l2-internals.md](l2-internals.md)
2. [l1-generate-surface.md](l1-generate-surface.md)
3. [information-flow.md](information-flow.md)

Looking for concepts without code? See [`../concepts/`](../concepts/README.md).
