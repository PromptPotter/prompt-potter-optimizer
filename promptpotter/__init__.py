import os

# OpenBLAS commits a buffer per logical core when numpy LOADS, here and in every child process.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from promptpotter.shared.extras import require_core

require_core()

# Every process imports this before its first write, and the root binds at import.
from promptpotter.config.paths import user_data_root  # noqa: E402
from promptpotter.infrastructure.store.private_root import hold_private  # noqa: E402

hold_private(user_data_root())
