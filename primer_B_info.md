Below is a clean, structured summary of the entire discussion, followed by the final recommended strategy, and then documentation you can drop directly into your repo.

1. Summary of What You Asked (Problem Statement)

You are building a second FastAPI server that:

Is not part of the original FastAPI project

Is logically and architecturally independent

Does not share code, models, DB, or business logic

Will live in a separate Git repository

Needs full visibility of the original API’s source code

Only for AI coding assistant context

Not for execution, imports, or dependencies

Needs a very easy way to refresh that reference when the original API changes

Must not pollute documentation or narrative of either project

Must avoid future migration, extraction, or cleanup work

This ruled out:

Co-locating both APIs permanently

Shared libraries

Manual copy-paste syncing

Pretending the services are related when they are not

2. Strategies Considered (and Why They Were Rejected)
❌ Single repository (monorepo)

Rejected because:

Creates narrative confusion

Pollutes documentation

Lies about architectural independence

Requires later extraction work

Conclusion: Not appropriate when services share nothing.

❌ Start together and split later

Rejected because:

No shared code or logic to justify incubation

Adds unnecessary complexity

Solves a problem that does not exist

Conclusion: Incubation only makes sense when real coupling exists.

❌ Copy code + .gitignore

Rejected because:

Manual syncing is error-prone

No provenance

Easy to forget updates

Encourages accidental reliance

No explicit versioning

Conclusion: Too fragile for ongoing use.

❌ Regularly “fetching” or syncing FastAPI code

Rejected because:

Creates hidden dependencies

Leads to merge conflicts

Breaks reproducibility

Becomes technical debt quickly

Conclusion: You sync libraries, not applications.

3. Final Strategy (Recommended Solution)
✅ Read-only Git Submodule as Reference

You will:

Keep two completely independent repositories

Add the original API as a Git submodule

Use it strictly as a read-only reference

Never import, execute, or depend on it

Update it with one command when needed

This gives you:

✅ Full source visibility

✅ Easy updates

✅ Explicit provenance

✅ No coupling

✅ No documentation pollution

✅ Zero future migration cost

4. Final Repository Structure
repo-new-api/
├── app/
│   └── main.py
├── vendor_reference/
│   └── original_api/   # Git submodule (read-only)
├── .gitmodules
├── pyproject.toml
└── README.md

5. How to Set It Up
Add the submodule
git submodule add https://github.com/you/original-api.git vendor_reference/original_api
git commit -m "Add original API as read-only reference"

Update the reference when needed
git submodule update --remote
git commit -am "Update original API reference snapshot"


✅ No merges
✅ No conflicts
✅ No ambiguity

6. Documentation (Drop-In)

You can copy the following directly into your README.md.

Reference Code Policy
vendor_reference/original_api

This directory contains a read-only Git submodule pointing to the original FastAPI project.

Purpose

Tooling context

AI coding assistant context

Architectural reference

This code is NOT:

A dependency

Imported at runtime

Used in tests

Required for this service to run

Rules

No imports from vendor_reference/

No assumptions about availability in production

No coupling to its implementation details

Updating the reference

git submodule update --remote
git commit -am "Update original API reference"

7. Guardrails (Strongly Recommended)
Prevent accidental imports (CI or local check)
! grep -R "vendor_reference" app/


Fail the build if violated.

8. Final Mental Model (Important)

The submodule is documentation, not code.
Treat it like a book on your desk, not a library you link against.

If that rule is followed, this setup remains clean forever.

✅ Final Verdict

This solution is correct for your constraints

It is normal and professional

It avoids all architectural and documentation pitfalls

It scales cleanly over time

If you ever want:

A stricter policy

A pre-commit hook

An AI-friendly indexing layout

Or a subtree variant

I can help with that too.