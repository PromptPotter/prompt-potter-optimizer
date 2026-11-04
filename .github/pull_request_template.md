## What & why

<!-- One or two sentences. Link the issue / spec / milestone if there is one. -->

## New concept?

<!-- A class, projection, injection, prompt, field, dict or file. If none: `N/A`. Otherwise:
the §0 bucket it maps onto (`docs/architecture.md`), the existing channel it rides instead of a
sidecar, where its facts land on disk, and what derives any state it adds (root `CLAUDE.md` § Working). -->

## Checks

- [ ] `python scripts/gate.py` green — every check CI runs, including the webapp half
- [ ] No backward-compat shim, re-export alias, fallback chain or `# legacy` comment introduced
