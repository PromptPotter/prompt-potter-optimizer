from __future__ import annotations

import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ssl

__all__ = ["tls_context"]


@functools.cache
def tls_context() -> ssl.SSLContext:
    # Lazy: a module that only names a provider imports this file and must not load the transport.
    import httpx

    return httpx.create_ssl_context()
