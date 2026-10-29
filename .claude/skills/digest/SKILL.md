---
name: digest
description: >-
  Hands a large read to a cheap model so the content never enters this session's context. For a
  fact out of something you will not edit or quote: a saved paper or page, a log, a scratch dump.
  Not for a file you will edit, debugging, a rule file, or anything under ~400 lines. Fires on
  "/digest" and when the Read hook refuses a large read.
---

# Digest — a cheap model reads it, you get the answer

```bash
.venv/Scripts/python.exe .claude/digest.py --extract --question "<one narrow question>" --paths <file> [<file> ...]
.venv/Scripts/python.exe .claude/digest.py           --question "<one narrow question>" --paths <file> [<file> ...]
```

- **`--extract`, the default choice** — the worker names WHERE the answer is and the script prints
  those lines from disk (at most 120), so they can be quoted as read.
- **without it** — the worker answers in bullets citing `path:line`. It paraphrases and has invented
  values, so its facts are pointers until you open the cited lines.

| Use it | Do not |
|---|---|
| "What numbers does this paper report for X?" over a saved page | A file you are about to edit |
| "Where does this log first show an error?" | Debugging, a race, a subtle invariant |
| "What does each of these six modules export?" | An architecture or naming decision |
| A scratch dump you need one figure from | `CLAUDE.md`, a skill, an ADR — rules are read, never summarised |
| | Anything short, or one known symbol (`Grep`) |

A broad question ends `CUT OFF` on the receipt: narrow it. `.env` and settings files are refused;
never route a secret around that. **Print the `[digest] …` receipt line in your reply** and say
which statements rest on it.
