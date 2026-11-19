# bkeys-4 review probes (ran on git-archive copies; adapt paths)

## pathdep.py
```
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/scripts")
import offline_run as O
from promptpotter.shared.hashing import stable_hash
for home in sys.argv[2:]:
    home = Path(home)
    t = O.BehaviourTrace.read(home / "potter")
    un = O.unrun("potter", home)
    print(home.name, "unrun lines", sum(map(len, un.values())), "potter fingerprint", stable_hash([t.decisions, t.requests, un]))
```

## holes.py
```
import ast, sys, os
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/scripts")
import offline_run as O
home = Path(sys.argv[2])
ran = O.ran_lines(home / "potter", recursive=False)
un = O.unrun("potter", home)
tot_un = sum(len(v) for v in un.values())
print("files", len(un), "unrun lines", tot_un)
# module-level names referenced only from function bodies never run
from promptpotter.config.paths import PACKAGE_ROOT
cands = []
for rel in un:
    path = PACKAGE_ROOT / rel
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fl = O._function_lines(path)
    r = ran[os.path.normcase(str(path))]
    names = {}
    for st in tree.body:
        if isinstance(st, (ast.Assign, ast.AnnAssign)):
            tg = st.targets if isinstance(st, ast.Assign) else [st.target]
            for t in tg:
                if isinstance(t, ast.Name) and st.value is not None and isinstance(st.value, ast.Constant):
                    names[t.id] = st.lineno
    uses = {n: set() for n in names}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in uses:
            uses[node.id].add(node.lineno)
    for n, ls in uses.items():
        if ls and all(l in fl and l not in r for l in ls):
            cands.append((rel, names[n], n, sorted(ls)))
print("module constants read only on unrun function lines:", len(cands))
for c in cands[:15]: print(c)
```

## retest2.py
```
import sys, os, re
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/scripts")
import offline_run as O
from promptpotter.shared.hashing import stable_hash
from promptpotter.config.paths import PACKAGE_ROOT
home = Path(sys.argv[2])
def h(): return stable_hash(O.unrun("potter", home))
base = h()
def trial(label, rel, edit):
    f = PACKAGE_ROOT / rel
    raw = f.read_bytes()
    orig = raw.decode("utf-8")
    new = edit(orig)
    assert new != orig and new.count("\n") == orig.count("\n"), label
    f.write_bytes(new.encode("utf-8"))
    try: print(label, "-> potter unrun-hash moved:", h() != base)
    finally: f.write_bytes(raw)
ran = O.ran_lines(home / "potter", recursive=False)
lf = PACKAGE_ROOT / "application/optimizers/pools/members.py"
r = ran[os.path.normcase(str(lf))]
print("initialize def 162 ran:", 162 in r, "body lines ran:", sorted(l for l in r if 162 < l < 175))
# rename the parameter ctx -> context on the def line and in the body (lines 162..174)
def ren(src):
    lines = src.split("\n")
    for i in range(161, min(175, len(lines))):
        lines[i] = re.sub(r"\bctx\b", "context", lines[i])
    return "\n".join(lines)
trial("rename run function's parameter ctx->context (def line 162 + body)", "application/optimizers/pools/members.py", ren)
f = PACKAGE_ROOT / "application/optimizers/potter/dispatch/injections/layer_state.py"
print("layer_state 283 ran:", 283 in ran[os.path.normcase(str(f))], "286 ran:", 286 in ran[os.path.normcase(str(f))])
trial("edit module constant _REBASE_CAPABILITY_TEXT", "application/optimizers/potter/dispatch/injections/layer_state.py", lambda s: s.replace("Default omit.", "Default: omit."))
```

## retest3.py
```
import sys, os
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/scripts")
import offline_run as O
from promptpotter.shared.hashing import stable_hash
from promptpotter.config.paths import PACKAGE_ROOT
home = Path(sys.argv[2])
def h(): return stable_hash(O.unrun("potter", home))
base = h()
rel = "application/optimizers/potter/dispatch/injections/catalogues.py"
f = PACKAGE_ROOT / rel
ran = O.ran_lines(home / "potter", recursive=False)[os.path.normcase(str(f))]
print("_pool_items lines 181-190 ran:", sorted(l for l in ran if 181 <= l <= 190))
raw = f.read_bytes(); s = raw.decode()
new = s.replace("so a child invents nothing.", "so a child may invent loci.")
assert new != s
f.write_bytes(new.encode())
try: print("edit recombination prompt text on line 187 -> potter unrun-hash moved:", h() != base)
finally: f.write_bytes(raw)
```

## retest4.py
```
import sys, os
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/scripts")
import offline_run as O
from promptpotter.shared.hashing import stable_hash
from promptpotter.config.paths import PACKAGE_ROOT
home = Path(sys.argv[2])
def h(): return stable_hash(O.unrun("potter", home))
base = h()
R = O.ran_lines(home / "potter", recursive=False)
for rel, lines, old, new in (
    ("application/optimizers/potter/validators.py", (315, 322, 326), "PLAN_LENGTH_FLOOR_CHARS = 60", "PLAN_LENGTH_FLOOR_CHARS = 600"),
    ("application/optimizers/potter/dispatch/injections/layer_state.py", (347, 364), "terminate now, naming that node.", "terminate now, naming the node."),
):
    f = PACKAGE_ROOT / rel; raw = f.read_bytes(); s = raw.decode()
    print(rel, {l: l in R[os.path.normcase(str(f))] for l in lines})
    assert old in s, old
    f.write_bytes(s.replace(old, new).encode())
    try: print("  edit", repr(old[:30]), "-> unrun-hash moved:", h() != base)
    finally: f.write_bytes(raw)
```

## retest5.py
```
import sys, os, re
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/scripts")
import offline_run as O
from promptpotter.shared.hashing import stable_hash
from promptpotter.config.paths import PACKAGE_ROOT
home = Path(sys.argv[2])
def h(): return stable_hash(O.unrun("potter", home))
base = h()
R = O.ran_lines(home / "potter", recursive=False)
def trial(label, rel, lines, edit):
    f = PACKAGE_ROOT / rel; raw = f.read_bytes(); s = raw.decode()
    print(label, {l: l in R[os.path.normcase(str(f))] for l in lines})
    new = edit(s); assert new != s and new.count("\n") == s.count("\n")
    f.write_bytes(new.encode())
    try: print("   -> unrun-hash moved:", h() != base)
    finally: f.write_bytes(raw)
trial("edit unrun validators.py:322 (if len(text) < FLOOR -> <=)", "application/optimizers/potter/validators.py", (322,),
      lambda s: s.replace("    if len(text) < PLAN_LENGTH_FLOOR_CHARS:", "    if len(text) <= PLAN_LENGTH_FLOOR_CHARS:"))
def ren(s):
    L = s.split("\n")
    for i in (162, 163):  # 0-based: lines 163, 164
        L[i] = re.sub(r"\bdemo\b", "split", L[i])
    return "\n".join(L)
trial("body-local rename demo->split on run lines pools/members.py:163-164", "application/optimizers/pools/members.py", (162, 163, 164), ren)
```

## deflines.py
```
import sys, os, types, inspect
from pathlib import Path
sys.path.insert(0, sys.argv[1] + "/scripts")
import offline_run as O
from promptpotter.config.paths import PACKAGE_ROOT
home = Path(sys.argv[2])
ranall = O.ran_lines(home / "potter", recursive=False)
un = O.unrun("potter", home)
stats = dict(ran_funcs=0, def_in_fl=0, def_ran=0, def_unrun=0)
examples = []
for rel in un:
    path = PACKAGE_ROOT / rel
    src = path.read_text(encoding="utf-8").splitlines()
    ran = ranall[os.path.normcase(str(path))]
    fl = O._function_lines(path)
    todo = [compile(path.read_text(encoding="utf-8"), str(path), "exec")]
    while todo:
        c = todo.pop()
        todo += [k for k in c.co_consts if isinstance(k, types.CodeType)]
        if not (c.co_flags & inspect.CO_NEWLOCALS) or c.co_name.startswith("<"):
            continue
        body = {l for _, _, l in c.co_lines() if l is not None}
        if body & ran:
            stats["ran_funcs"] += 1
            d = c.co_firstlineno
            # the `def` line itself (decorators shift co_firstlineno to the first decorator)
            defline = next(i for i in range(d, d + 30) if src[i-1].lstrip().startswith(("def ", "async def ")))
            if defline in fl:
                stats["def_in_fl"] += 1
                if defline in ran: stats["def_ran"] += 1
                else:
                    stats["def_unrun"] += 1
                    if len(examples) < 4: examples.append((rel, defline, src[defline-1].strip()[:70]))
print(stats); [print(e) for e in examples]
```
