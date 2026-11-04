# docs/ — rules for agent readers

The operator-facing index is [`README.md`](README.md). Read [`architecture.md`](architecture.md) §0 + §0.5 first; load only the subtree you need.

- **`specs/` holds direction of travel only.** A fact about shipped behavior belongs in `concepts/`, `developer/` or `operations/`; a fully shipped spec is deleted (`git log` keeps it). Why a boundary sits where it does lives in [`decisions.md`](decisions.md), never deleted on shipping. L4 has two owners: [`specs/l4-outer-loop.md`](specs/l4-outer-loop.md) says what is TRUE, `.claude/skills/potter-self/` what to DO.
- **One fact, one owner.** The doc governing the artifact a rule constrains owns it; elsewhere at most one obligation line linking the owner.
- **Docs hold rules, not state.** A line a reader could recompute differently later names the symbol (`<file>.py::<symbol>`), the enumerator or the counting command instead; a status word is a link to the status owner. A war story keeps only its decision.
- **Checked by `gate.py --only doc-claims`**: every backticked `path::symbol`, every link to a doc followed by `§ Heading` (exactly one heading there), and every `<!-- probe: git grep -cE "<pattern>" -- <path> == N -->` beside a one-X / no-second-Y claim. A claim the code does not keep yet goes in `gate.py::_KNOWN_FALSE`; a claim no probe can check is deleted. Point the probe path at source files (`promptpotter/*.py`) so its own comment never counts itself.
- **Code defines behavior; where a doc disagrees, the doc is fixed** — except [`architecture.md`](architecture.md) §0 / §0.5, a contract the code is fixed to.
