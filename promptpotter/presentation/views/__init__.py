"""Display + reporting renderers for CLI, notebook, and (future) webapp.

Pure functions only. No disk I/O, no logging, no global state. Callers load
the data and pass it in. Import directly from submodules — no package-level
re-exports.
"""
