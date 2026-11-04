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
python .claude/digest.py --extract --question "<one narrow question>" --paths <file> [<file> ...]   # the lines themselves (≤120), quotable as read
python .claude/digest.py           --question "<one narrow question>" --paths <file> [<file> ...]   # bullets citing path:line — pointers until you open them
```

Prefer `--extract`. Not for a file you will edit, debugging, a design decision, a rule file (rules are
read, never summarised), anything short, or one known symbol (`Grep`).

A broad question ends `CUT OFF` on the receipt: narrow it. `.env` and settings files are refused; never
route a secret around that. **Print the `[digest] …` receipt line in your reply** and say which statements
rest on it.
