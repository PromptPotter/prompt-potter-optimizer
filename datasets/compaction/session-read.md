# session-read — keep what the reader needs

Member of the [compaction group](README.md). The framing below is the draft of what a
`task_description.md` would say once this member has rows.

An information artifact goes in, a smaller one comes out, and a downstream task is then solved from
the smaller one alone. The compaction is good when the downstream task still succeeds.

## Domain

- Input: one **source** (a paper page, a log, a dump, several files) and the **question** the
  downstream task will be asked about it
- Output: a **compaction** of the source, much shorter than the source
- The downstream task is solvable from the full source. Nothing here asks you to solve it

## Success criteria

- The downstream solver, given only the compaction and the question, reaches the gold answer
- The compaction is a small fraction of the source. Both are measured; neither alone is the score

## Key failure modes (the hard part)

- Dropping the one passage that answers because it looked like detail: a table row, a footnote, a
  number inside a caption
- Returning text *about* the answer (the paragraph that mentions the table) and not the answer
  (the table)
- Answering the question in place of compacting. The output must carry the source's own support,
  so that the solver can check it, never a bare conclusion
- Returning everything. A compaction as long as the source has kept the answer and done no work
- Inventing or rounding a value that the source states exactly

## Notes

- Several passages may be needed together; a question about two tables needs both
- When the source does not contain the answer, the right compaction is empty

## Traces

The compactor runs today as an operator-local script (`.claude/digest.py`, untracked, so a fresh
checkout does not have it). Every call appends one row to a ledger beside it: the question, the
worker's answer (the line ranges, or the digest text), a content hash per source with the source
itself kept once, and the session's verdict (`enough` or `re-read anyway because …`). The ledger
and the source copies are per-machine and never committed.

A row is a trace, not yet a sample: it has no gold answer. The verdict says whether the compaction
was enough for the session, which marks the hard cases; turning one into a row still needs the
answer written down and checked against the source. No data is synthesized for this group.
