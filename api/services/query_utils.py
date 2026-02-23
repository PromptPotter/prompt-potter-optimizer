"""
Shared query-parsing utilities used by grid_search and backend_client.
"""


def parse_bom_material(query: str) -> tuple[str, str]:
    """Split a query string into (bom_material, process).

    TermNorm queries use the format ``bom_material / process``.
    If no slash is present, process is an empty string.
    """
    if "/" in query:
        last_slash = query.rfind("/")
        bom_material = query[:last_slash].strip()
        process = query[last_slash + 1:].strip()
    else:
        bom_material = query.strip()
        process = ""
    return bom_material, process
