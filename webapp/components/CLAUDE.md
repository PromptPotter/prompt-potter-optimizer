# webapp/components — CLAUDE.md

## Component conventions

- **Three tiers, decided top-down:** a **primitive** (`ui/`), **chrome** (`shell/`: the frame `AppShell` mounts around every view — masthead, sidebar, tabs, dock, the remote control — and what only it mounts), or **one surface** (a folder each). A domain widget several surfaces host is a surface of its own (`candidates/`, `measurements/`), never chrome. A file answering two is mis-filed. A host owns only what its surface answers and hands a reading **IDENTITY props, never SOURCE props** ([`../lib/CLAUDE.md`](../lib/CLAUDE.md) § Display-data sources); shared state is a context in `lib/`.
- **Inside a surface, folders are domain regions** (`dashboard/samples/`, `dashboard/scoring/`, `dashboard/pipeline/`), never widget kinds; an unowned domain widget gets its own folder (`candidates/`).
- **No second modal, popover, dropdown, toggle-chip, segmented control, toolbar, clipboard copy, commit-on-blur input, read state or failure line** — use or add to `components/ui/*` (with an RTL test). A read's idle/loading/failed/empty is `ui/states::ReadState`; a failure object's sentence is `ui/states::FailureLine`. <!-- probe: git grep -cE ".note-failure." -- webapp/components/*.tsx <= 1 --> <!-- probe: git grep -cE "role=.dialog." -- webapp/components/*.tsx <= 1 --> <!-- probe: git grep -cE "navigator\.clipboard" -- webapp/*.tsx <= 1 --> `ui/VendorLogo` (who trained a model) and `account/providers.tsx::ProviderIcon` (who you signed in with) are the two brand marks; a `VendorLogo` is a `<use>` into `VendorSprite`, which `AppShell` mounts once.
- **Teaching prose goes through `ui/Term` or `ui/HoverCard`, never `title=`** — `title=` only repeats a truncated string or hints a control with its own `aria-label`. Operator vocabulary rides `lib/terms.ts::TERMS`.
- **No second table, measured-cell renderer or detail panel**: rows are `ui/DataTable`; <!-- probe: git grep -cE "<table" -- webapp/components/*.tsx == 0 --> measured cells are a PRESET of `measurements/MeasurementsPane` over `GET /datasets/{name}/cells`, opening in `measurements/CellPanel` inside `ui/SidePanel`. Grouping buckets served rows, never re-sorts them.
- **No second pipeline graph or lineage tree**: `dashboard/pipeline/PipelineFlow` and `candidates/Forest` (a `CladogramCtx`). **A channel's extent is the chain that produced its point** (`extentKeys`) — never a round-column test. **Every drawn edge is the served descent** (`descentOf`: `descends_from` solid, the declared `donor` dashed, each locus's first `gene_sources` dotted) — never chained from a round's winner, never a donor guessed.
- **What a panel copies is a derivation in `lib/derivations`** (`searchpointCopyChoices`), never the host's call; `CopyButton` hangs beside a label, never inside one.
- **An axis is one `ui/ValueList`**: start value = position 1, ticks = permitted set, so the lock is DERIVED (🔒/🔓 only where there is no set). Each part draws exactly when its channel exists; **gate on the fact (`onApply`), never one callback.** `NodeConfigEditor` is the single editor; `ConfigMode` picks only the transport.
- **A row answers two questions: who may SEARCH it (`never_axis`/`movable_by`) and which widget EXPRESSES it (`kind`)** — the search fence is server-side, never re-gated here. The SCHEMA is outside the rows (`OutputContract`; a check-in authors via `SchemaTreeEditor`). Exactly one 🔓/🔒 mark per row; a shut axis names its own remedy.
- **A card header is ONE row** — `Toolbar` + `ToolbarSep` + `ToolbarSpacer`, controls `flex: 0 0 auto`, rare ones in a `⋯` `Menu`; a control driving one region sits beside it.
- **"Where is this run" is `shell/masthead/RunMasthead`'s alone** — no pane below repeats it. Its chips read `dash` for the viewed leaf, except STATE and BENCH (`useViewedCourse`).
- **A run bound is a CAP on every label** (the account's is the ALLOWANCE); one parse, `lib/run-limits.ts::parseCap`.
- **Text and numeric controls commit on Enter or blur, never per keystroke** — `ui/CommitInput`.
- **A lit chip wears the ink of what it drew**, lit from the served value, never a local boolean.
- **A painted surface is a control** — SVG/tree nodes get keyboard operability and a `role`, a `<canvas>` an `aria-label`.

## Render-cost guards (do not regress)

**A chart consuming `dash` is `React.memo`-wrapped, its `useMemo`s keyed on the narrowest derivation** (`dash?.rounds`), never `dash`.

- **Geometry memoizes on structure** (`tree` + `expanded`); live overlays (`valueByKey` / `thetaByKey`) stay outside it.
- **Anything in a chart's `options` memo is a stable identity** — `onSelect` / `onGeometry` are `useCallback`s.
