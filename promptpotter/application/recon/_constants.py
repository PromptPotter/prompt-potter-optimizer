"""Recon-local constants — diagnostic set thresholds.

Kept inside ``application/recon/`` so the dormant template is
self-contained: a future reviver can read and re-wire the module
without hunting through ``shared/``.
"""

DEFAULT_DIAGNOSTIC_QUERIES: int = 6
MIN_DIAGNOSTIC_QUERIES: int = 3
DIAGNOSTIC_HIT_RATIO: float = 0.75
RECON_TARGET_MDE: float = 0.15  # 15% minimum detectable effect for scan sizing
