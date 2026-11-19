import os, sys, tempfile, _winapi, shutil
from pathlib import Path
from promptpotter.infrastructure.store import io
root = Path(tempfile.mkdtemp(prefix="rv2f-j-"))
target = root / "real"; target.mkdir()
link = root / "link"
_winapi.CreateJunction(str(target), str(link))
shutil.rmtree(target)
sys.setrecursionlimit(1000)
try:
    io.write_json(link / "projects" / "x.json", {})
    print("OK")
except BaseException as e:
    print("FAIL", type(e).__name__, str(e)[:150], "| is OSError:", isinstance(e, OSError))
