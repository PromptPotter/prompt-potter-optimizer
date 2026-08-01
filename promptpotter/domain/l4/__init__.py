"""The L4 law — everything the outer loop knows about an optimizer prompt, in one place.

- :mod:`proxies` — what ONE finished inner cycle says about the optimizer prompt that ran it
  (the floor / exclude / measure trichotomy, and the proxy vector itself).
- :mod:`verdict` — what a ROUND of them says about a variant (the blocked-paired decision).

Pure domain, so the law cannot grow a file read or a session dependency.

Import from the submodule, not this package: ``verdict`` is imported by ``domain.results``
(for ``OuterVerdict``) while ``proxies`` imports ``domain.results`` (for ``RoundResult``).
The two modules are acyclic; a re-exporting ``__init__`` would make them circular.
"""
