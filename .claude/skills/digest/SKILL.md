---
name: digest
description: >-
  Hands a large read to a cheap model and returns a short digest, so the content never enters this
  session's context. Use when you need a fact or a summary out of something you will not edit or
  quote: a fetched paper or web page saved to disk, a log or readout, a scratch dump of tool output,
  or several source files read only to answer one question. Not for a file you are about to edit,
  debugging, a design decision, a rule file, or anything under about 400 lines. Fires on
  "/digest", and when the Read hook refuses a large read.
---

# Digest — a cheap model reads it, you get the answer

A whole-file read stays in the window and is re-read on every later call. When all you need from
the file is an answer, a small model can fetch it for a fraction of a cent.

```bash
.venv/Scripts/python.exe .claude/digest.py --extract --question "<one specific question>" --paths <file> [<file> ...]
.venv/Scripts/python.exe .claude/digest.py           --question "<one specific question>" --paths <file> [<file> ...]
```

**Two modes, and `--extract` is the default choice.**

- **`--extract`** — the worker only says WHERE the answer is; the script then prints those lines
  from disk (at most 120). Nothing you read was written by the worker, so a number in it can be
  quoted as it stands. Use it whenever the answer is somewhere in the text: a figure, a table, a
  definition, the place something happens.
- **without it** — the worker answers in its own words, in bullets citing `path:line`. Use it only
  when no passage holds the answer and one has to be composed: "what does each of these six
  modules do", "what pattern do these tests follow". It paraphrases, and on a broad question it has
  mislabelled a result and invented two values, so its numbers are pointers until you open the lines.

One model (`MODEL` in `.claude/digest.py`), one shot, a few seconds. Markup is stripped from `.html`
before sending, line numbers unchanged. Both modes end with a `[digest] …` receipt line.

## When it helps, and when it does not

Ask one thing: **would a model with no judgment get this right from the text alone?**

| Use it | Do not |
|---|---|
| "What numbers does this paper report for X?" over a saved page | A file you are about to edit — you need its exact text, so read it |
| "Where does this log first show an error, and what is it?" | Debugging, a race, a subtle invariant — the worker finds surface patterns and misses those |
| "What does each of these six modules export?" to pick the one to open | An architecture or naming decision |
| "What pattern do the tests in this file follow?" | `CLAUDE.md`, a skill, an ADR — rules are read, never summarised |
| A scratch dump you only need one figure from | Anything short. Under ~400 lines the wait costs more than the read |
| | One known symbol — `Grep` is instant and exact |

When in doubt, read. A wrong digest costs more than the tokens it saved.

## Asking well

- **One narrow question, with the shape of the answer.** "List the top-level functions with their
  line" beats "explain this file". A broad question fills the output cap, and the receipt then says
  `CUT OFF` — narrow it and ask again.
- **Follow-ups are cheap.** Re-send the same paths with the next question.
- **Never send secrets.** `.env` and the settings files are refused by the script; do not route
  around that, and do not send a dump that holds a key or a customer's data.

## What you owe the operator, every time

1. **Print the `[digest] …` receipt line verbatim in your reply**, and say which statements rest on
   it. No digest is used silently.
2. **Only quoted lines are evidence.** `--extract` output is the file's own text and can be used as
   read. A digest in the worker's words locates and does not verify: before one of its facts is
   written into a doc, quoted, or acted on, open the cited lines with a targeted `Read`
   (`offset` / `limit`) or re-ask with `--extract`. In `docs/research/` only what you saw quoted
   counts as read, and the entry says the rest of the source was not.
3. **Record the verdict** once you know whether it was enough:

   ```bash
   .venv/Scripts/python.exe .claude/digest.py --note "enough"
   .venv/Scripts/python.exe .claude/digest.py --note "re-read anyway because <reason>"
   ```

   The ledger (`.claude/notes/digest-ledger.jsonl`, shown by `--ledger`) is how this experiment is
   judged, so a missing verdict is a lost data point.
