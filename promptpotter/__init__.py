import os

# OpenBLAS commits a buffer per logical core when numpy LOADS, here and in every child process.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
