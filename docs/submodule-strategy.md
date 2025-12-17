# External Reference Strategy

## Overview

The `external/` directory is **gitignored** - it contains a local clone of [TermNorm-excel](https://github.com/runfish5/TermNorm-excel) for development context.

## Setup (for developers)

```bash
mkdir -p external
git clone https://github.com/runfish5/TermNorm-excel external/termnorm-excel
```

## Purpose

- AI coding assistant context (full visibility of both codebases)
- Architectural reference
- Understanding how optimized prompts will be used

## What This Code Is NOT

- A dependency
- Imported at runtime
- Used in tests
- Required for this service to run
- Tracked in git

## Rules

1. **No imports** from `external/`
2. **No assumptions** about availability in production
3. **No coupling** to its implementation details

## Updating the Reference

```bash
cd external/termnorm-excel
git pull
```

## Mental Model

> The external code is documentation, not a dependency.
> Treat it like a book on your desk, not a library you link against.
