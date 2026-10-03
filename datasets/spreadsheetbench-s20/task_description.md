# What the task is

A containerized terminal agent is given a spreadsheet and a natural-language instruction
describing an edit to make. It works in a Linux container with Python and `openpyxl` available,
opens the workbook, makes the edit, and saves the result to the output path the instruction
names. The task's own verifier then recalculates the formulas with LibreOffice and compares the
produced workbook against the expected one **cell by cell** — so a value that merely looks right
in a printout but was written as text, or into the wrong sheet, scores zero.

The agent runs unattended: nobody answers a clarifying question, and there is no partial credit.
One episode is one cell of the measurement, graded 0 or 1 by that comparison.

# What is being optimized

Not the answer — the **working habits**. The candidate under test is an Agent Skill (a `SKILL.md`)
injected into the container before the agent starts, which the agent reads before acting. It is
general guidance about how to approach the work: what to inspect first, how to verify a change,
when to stop.

It must stay general. A skill that named a specific task, workbook or cell range would be the
benchmark leaking into the arm every later lift is measured against — the number would rise and
mean nothing.

# What separates a good skill from a bad one

The failure modes worth steering are the ones this agent actually hits: declaring the task done
without reopening the file to confirm the edit landed; writing a value where a formula was asked
for (or the reverse); editing the input workbook instead of writing the output path; and burning
turns re-running a command that already failed instead of reading its error. Each of those is a
habit, which is why a skill can move them at all.
