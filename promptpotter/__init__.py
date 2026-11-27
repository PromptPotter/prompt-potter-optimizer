import os

# The one act at import: OpenBLAS commits a buffer per logical core when numpy LOADS, and numpy can
# load before any entry point's start runs, here and in every child process (16 cores: ~640 MB).
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from promptpotter.shared.extras import require_core

require_core()
