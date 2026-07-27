"""Injection renderers — uniform ``(InjectionBundle) -> str`` signature, layer-agnostic.

To add an input to any prompt, write a renderer in the module that matches its origin and
register it there with ``@signal``; anything else is drift. Callers import the leaf they
need — this ``__init__`` re-exports nothing.
"""
