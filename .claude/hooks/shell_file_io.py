"""PreToolUse on Bash and PowerShell: refuse a repo file read, edited or authored through the shell.

The repo is `CLAUDE_PROJECT_DIR`, which in a worktree session is the worktree. `--selftest` judges
`SAMPLES`, and the gate's `instruction-files` check runs it.
"""

import json
import os
import re
import shlex
import sys
import time
from typing import Literal, NamedTuple

LITERAL = "<literal>"
OPAQUE = "$"
STDIN_SCRIPT = "python -"
RUNTIME = (".scratch/", "logs/", ".promptpotter/")
DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
PYTHON = re.compile(r"python[\d.]*(\.exe)?|py(\.exe)?")
PAGERS = {"cat", "head", "tail", "sed", "grep", "egrep", "fgrep", "rg"}
EMITTERS = {"echo", "printf"}

SEPARATORS = set(";&|()\n")
REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([\w.-]+)\1")
# Options whose value is the next word, per pager; every other `-x` stands alone.
VALUED = {
    "grep": set("efmABCD") | {"--regexp", "--file", "--max-count", "--context"},
    "rg": set("efmABCgtTM") | {"--regexp", "--file", "--glob", "--type", "--max-count"},
    "sed": set("ef") | {"--expression", "--file"},
    "head": set("nc"),
    "tail": set("nc"),
}

PS_BREAK = set(" \t\r\n;|&(){},<>")
PS_REDIRECT = re.compile(r"[1-6*]?>>?(?:&[12])?")
PS_PARAM = re.compile(r"-([A-Za-z_]\w*)(?::(.*))?", re.DOTALL)
PS_ASSIGN = re.compile(r"[-+*/%]?=(.*)", re.DOTALL)
PS_ALIASES = {
    **dict.fromkeys(("gc", "cat", "type"), "get-content"),
    "sls": "select-string",
    "sc": "set-content",
    "ac": "add-content",
    "tee": "tee-object",
    "ni": "new-item",
    **dict.fromkeys(("echo", "write"), "write-output"),
    **dict.fromkeys(("cd", "sl", "chdir", "pushd", "push-location"), "set-location"),
}
PS_SWITCHES = {"force", "whatif", "confirm", "verbose", "debug", "passthru"}


class Cmdlet(NamedTuple):
    positional: tuple  # in binding order
    switches: set
    valued: tuple
    aliases: dict


LP = {"lp": "literalpath", "pspath": "literalpath"}
CONTENT = ("literalpath", "encoding", "filter", "include", "exclude", "stream")
WRITER = Cmdlet(("path", "value"), {"nonewline", "asbytestream"}, CONTENT, LP)
PS_SPECS = {
    "get-content": Cmdlet(
        ("path",),
        {"raw", "wait", "asbytestream"},
        (*CONTENT, "tail", "totalcount", "readcount", "delimiter"),
        {**LP, "last": "tail", "head": "totalcount", "first": "totalcount"},
    ),
    "select-string": Cmdlet(
        ("pattern", "path"),
        {"simplematch", "casesensitive", "quiet", "list", "notmatch", "allmatches", "raw"},
        ("literalpath", "include", "exclude", "encoding", "context", "culture", "inputobject"),
        LP,
    ),
    "set-content": WRITER,
    "add-content": WRITER,
    "out-file": Cmdlet(
        ("filepath", "encoding"),
        {"append", "noclobber", "nonewline"},
        ("literalpath", "width", "inputobject"),
        {**LP, "path": "filepath"},
    ),
    "tee-object": Cmdlet(
        ("filepath",),
        {"append"},
        ("literalpath", "variable", "inputobject", "encoding"),
        {**LP, "path": "filepath"},
    ),
    "new-item": Cmdlet(("path",), set(), ("itemtype", "name", "value"), {"type": "itemtype"}),
    "set-location": Cmdlet(("path",), set(), ("literalpath", "stackname"), LP),
}


class Stage(NamedTuple):
    program: str
    reads: tuple[tuple[str, str], ...]  # (word as typed, resolved path)
    writes: tuple[tuple[str, str], ...]
    stdin: Literal["literal", "program", "none"]
    position: int


def resolve(word, cwd):
    if not word or "$" in word or "`" in word:
        return None
    if word.startswith("~"):
        word = os.path.expanduser(word)
    if os.name == "nt":
        drive = re.match(r"^/([a-zA-Z])(/|$)", word)
        if drive:
            word = f"{drive.group(1)}:/{word[3:]}"
    if DRIVE.match(word) or os.path.isabs(word):
        return os.path.normcase(os.path.normpath(word))
    if cwd is None:
        return None
    return os.path.normcase(os.path.normpath(os.path.join(cwd, word)))


def stage(program, reads, writes, stdin, position, cwd):
    reads, writes = ([(w, p) for w in ws if (p := resolve(w, cwd))] for ws in (reads, writes))
    return Stage(program, tuple(reads), tuple(writes), stdin, position)


def under(path, root):
    if path != root and not path.startswith(root + os.sep):
        return None
    return os.path.relpath(path, root).replace("\\", "/").lower()


def literal_output(s):
    passed = s is not None and s.program in ("cat", "tee") and not s.reads
    return s is not None and (s.program == LITERAL or (passed and s.stdin == "literal"))


def piped(prev, position):
    if not position:
        return "none"
    return "literal" if literal_output(prev) else "program"


def python_program(args):
    i = 0
    while i < len(args):
        if args[i] == "-":
            return STDIN_SCRIPT
        if args[i][:2] in ("-c", "-m") or not args[i].startswith("-"):
            return "python"
        i += 2 if args[i] in ("-X", "-W") else 1
    return STDIN_SCRIPT


def edited(s, pipeline, root):
    read = {path for other in pipeline for _, path in other.reads}
    return next((w for w, p in s.writes if p in read and under(p, root) is not None), None)


def stdin_script(s, pipeline, root):
    return "python" if s.program == STDIN_SCRIPT and s.stdin != "none" else None


def authored(s, pipeline, root):
    if not literal_output(s):
        return None
    return next((w for w, p in s.writes if under(p, root) is not None), None)


def paged(s, root, records):
    if s.program not in PAGERS or s.position:
        return None
    for word, path in s.reads:
        rel = under(path, root)
        if rel is None:
            continue
        record = rel.startswith(RUNTIME) and rel.endswith((".json", ".jsonl"))
        followed = rel.endswith(".log") and (rel.startswith(RUNTIME) or rel.endswith("readout.log"))
        if record == records and not (s.program == "tail" and followed):
            return word
    return None


RULES = (
    ("edit", edited, "edits {} through the shell — use Edit"),
    (
        "py-stdin",
        stdin_script,
        "feeds {} its script on stdin, which hangs on this box — write the script to a file "
        "and run it",
    ),
    ("author", authored, "writes literal text into {} through the shell — use Write or Edit"),
    (
        "read-once",
        lambda s, pipeline, root: paged(s, root, records=True),
        "pages the record {} — Grep its key or Read a slice (root CLAUDE.md <read-once>)",
    ),
    (
        "read",
        lambda s, pipeline, root: paged(s, root, records=False),
        "reads {} through the shell — use Read (offset/limit for a slice), Grep or Glob",
    ),
)


def judge(pipelines, root):
    for pipeline in pipelines:
        for tag, rule, reason in RULES:
            for s in pipeline:
                if (word := rule(s, pipeline, root)) is not None:
                    return tag, reason.format(word)
    return None


def strip_heredocs(command):
    """Each heredoc's body goes, its `<<` stays: a body is data, never a command to read."""
    out, ends = [], []
    for line in command.split("\n"):
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            continue
        out.append(line)
        ends = [m.group(2) for m in HEREDOC.finditer(line)]
    return "\n".join(out)


def bash_words(command):
    lex = shlex.shlex(strip_heredocs(command), posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    words, position = [], 0
    for token in lex:
        if token and set(token) <= SEPARATORS:
            if words:
                yield words, position
            words = []
            position = position + 1 if token in ("|", "|&") else 0
        else:
            words.append(token)
    if words:
        yield words, position


def operands(name, args):
    valued, files, script_given, i = VALUED.get(name, set()), [], False, 0
    while i < len(args):
        word = args[i]
        if word == "--":
            files += args[i + 1 :]
            break
        if word.startswith("--"):
            if "=" not in word and word in valued:
                i += 1
            script_given |= word in ("--regexp", "--file", "--expression")
        elif word.startswith("-") and len(word) > 1:
            letters = word[1:]
            script_given |= bool(set(letters) & set("ef"))
            if letters[-1] in valued and not letters[-1].isdigit():
                i += 1
        else:
            files.append(word)
        i += 1
    if name in ("sed", "grep", "egrep", "fgrep", "rg") and not script_given and files:
        files = files[1:]  # the first bare word is the pattern or the script
    recursive = any(re.fullmatch(r"-[^-]*[rR]\w*", a) for a in args)
    if not files and (name == "rg" or (recursive and name in ("grep", "egrep", "fgrep"))):
        files = ["."]
    return files


def bash_pipelines(command, cwd):
    pipelines, prev = [], None
    for words, position in bash_words(command):
        if not position:
            pipelines.append([])
        while words and re.fullmatch(r"\w+=.*", words[0]):
            words = words[1:]
        if words[:1] == ["timeout"] and len(words) > 2:
            words = words[2:]
        if words[:1] == ["cd"]:
            cwd = resolve(words[1], cwd) if len(words) > 1 else None
            continue
        args, reads, writes, stdin, i = [], [], [], piped(prev, position), 0
        while i < len(words):
            word, after = words[i], words[i + 1] if i + 1 < len(words) else ""
            if word.isdigit() and after in (*REDIRECTS, ">&", "<"):
                i += 1
                continue
            if word in REDIRECTS:
                writes.append(after)
            elif word in ("<<", "<<-", "<<<"):
                stdin = "literal"
            elif word == "<":
                reads.append(after)
                stdin = "program"
            elif word != ">&":
                args.append(word)
                i += 1
                continue
            i += 2
        name = os.path.basename(args[0]).lower() if args else OPAQUE
        if name in EMITTERS and not any("$" in a or "`" in a for a in args):
            name = LITERAL
        elif PYTHON.fullmatch(name):
            name = python_program(args[1:])
        if name in PAGERS:
            files = operands(name, args[1:])
            reads += files
            if name == "sed" and any(re.fullmatch(r"-\w*i\w*|--in-place\S*", a) for a in args):
                writes += files
        if name == "tee":
            writes += [a for a in args[1:] if not a.startswith("-")]
        prev = stage(name, reads, writes, stdin, position, cwd)
        pipelines[-1].append(prev)
    return pipelines


def ps_statements(text):
    """Statements as pipelines of stages; a stage is a list of (kind, value) elements.

    Kinds: `word` (bare, resolvable), `str` (quoted, no `$`), `value` (cannot be resolved), `here`
    (a here-string body), `group` (nested statements), `redirect`, `comma`, `call` (the `&`).
    """
    at, end = 0, len(text)

    def quoted(quote):
        nonlocal at
        out, plain = [], True
        at += 1
        while at < end:
            c = text[at]
            if c == quote:
                if text[at + 1 : at + 2] != quote:
                    at += 1
                    break
                out.append(quote)
                at += 2
            elif quote == '"' and c == "`":
                out.append(text[at + 1 : at + 2])
                at += 2
            elif quote == '"' and c == "$":
                plain = False
                at += 1
                if text[at : at + 1] == "(":
                    at += 1
                    block()
            else:
                out.append(c)
                at += 1
        return "".join(out), plain

    def bare():
        nonlocal at
        out, plain = [], True
        while at < end and text[at] not in PS_BREAK:
            c = text[at]
            if c == "`":
                out.append(text[at + 1 : at + 2])
                at += 2
            elif c in "'\"":
                part, ok = quoted(c)
                out.append(part)
                plain &= ok
            elif c == "$":
                plain = False
                at += 1
                if text[at : at + 1] == "(":
                    at += 1
                    block()
            else:
                out.append(c)
                at += 1
        return ("word", "".join(out)) if plain else ("value", None)

    def block():
        nonlocal at
        statements, pipeline, stage = [], [], []
        while at < end:
            c, two = text[at], text[at : at + 2]
            if c in " \t\r<" and two != "<#":
                at += 1
            elif two in ("`\n", "`\r"):
                at += 3 if text[at : at + 3] == "`\r\n" else 2
            elif c in ")}":
                at += 1
                break
            elif c == "#":
                stop = text.find("\n", at)
                at = end if stop < 0 else stop
            elif two == "<#":
                stop = text.find("#>", at)
                at = end if stop < 0 else stop + 2
            elif c in ";\n" or two in ("&&", "||"):
                at += 1 if c in ";\n" else 2
                pipeline += [stage] if stage else []
                statements += [pipeline] if pipeline else []
                pipeline, stage = [], []
            elif c == "|":
                at += 1
                pipeline += [stage] if stage else []
                stage = []
            elif c == "&":
                at += 1
                stage += [] if stage else [("call", None)]
            elif c in "({" or two in ("$(", "@(", "@{"):
                at += 1 if c in "({" else 2
                stage.append(("group", block()))
            elif two in ("@'", '@"'):
                stop = text.find("\n" + two[1] + "@", at + 2)
                at = end if stop < 0 else stop + 3
                stage.append(("here", None))
            elif c in "'\"":
                content, plain = quoted(c)
                stage.append(("str", content) if plain else ("value", None))
            elif c == ",":
                at += 1
                stage.append(("comma", None))
            elif PS_REDIRECT.match(text, at):
                match = PS_REDIRECT.match(text, at)
                at = match.end()
                stage.append(("redirect", match.group()))
            elif c in "$@" and at + 1 < end and text[at + 1] not in PS_BREAK:
                at += 1
                if text[at] == "{":
                    stop = text.find("}", at)
                    at = end if stop < 0 else stop + 1
                while at < end and (text[at].isalnum() or text[at] in "_:?^$"):
                    at += 1
                while at < end and text[at] not in PS_BREAK and text[at] != "=":
                    at += 1
                stage.append(("value", None))
            else:
                stage.append(bare())
        pipeline += [stage] if stage else []
        statements += [pipeline] if pipeline else []
        return statements

    return block()


def ps_bind(elements, spec):
    """Each parameter's value as a list of elements (a comma joins an array), by canonical name."""
    positional, switches, valued, aliases = spec
    names = (*positional, *switches, *valued, *PS_SWITCHES)
    args, joined = [], False
    for element in elements:
        if element[0] == "comma":
            joined = True
        elif joined and args:
            args[-1].append(element)
            joined = False
        else:
            args.append([element])
    named, loose, i = {}, [], 0
    while i < len(args):
        arg = args[i]
        i += 1
        param = arg[0][0] == "word" and len(arg) == 1 and PS_PARAM.fullmatch(arg[0][1])
        if not param:
            loose.append(arg)
            continue
        name = param.group(1).lower()
        name = aliases.get(name, name)
        if name not in names:
            candidates = [n for n in names if n.startswith(name)]
            name = candidates[0] if len(candidates) == 1 else name
        if name in switches or name in PS_SWITCHES:
            named[name] = []
        elif param.group(2):
            named[name] = [("word", param.group(2))]
        elif i < len(args):
            named[name] = args[i]
            i += 1
    for name in positional:
        if name not in named and loose:
            named[name] = loose.pop(0)
    return named


def literals(named, *keys):
    return [text for key in keys for kind, text in named.get(key, ()) if kind in ("word", "str")]


def plain(elements):
    return all(kind in ("word", "str") for kind, _ in elements)


def ps_pipelines(statements, cwd):
    out = []
    for pipeline in statements:
        stages, prev = [], None
        out.append(stages)
        for position, elements in enumerate(pipeline):
            for kind, value in elements:
                if kind == "group":
                    for nested in ps_pipelines(value, cwd):
                        stages += nested
            first = elements[1][1] if len(elements) > 1 and elements[1][0] == "word" else ""
            if elements[0][0] == "value" and (assign := PS_ASSIGN.fullmatch(first)):
                elements = ([("word", assign.group(1))] if assign.group(1) else []) + elements[2:]
            called = elements[:1] == [("call", None)]
            kind, word = (elements[called:] or [("value", None)])[0]
            args, targets, rest = [], [], iter(elements[called + 1 :])
            for element in rest:
                if element[0] != "redirect":
                    args.append(("value", None) if element[0] == "group" else element)
                elif "&" not in element[1]:
                    target = next(rest, ("value", None))
                    targets += [target[1]] if target[0] in ("word", "str") else []
            if kind in ("str", "here") and not called:
                name = LITERAL
            elif kind in ("word", "str"):
                name = re.split(r"[\\/]", word)[-1].lower()
                name = PS_ALIASES.get(name, name)
            else:
                name = OPAQUE
            reads, writes, stdin = [], targets, piped(prev, position)
            named = ps_bind(args, PS_SPECS[name]) if name in PS_SPECS else {}
            if name == "set-location":
                places = literals(named, "path", "literalpath")
                cwd = resolve(places[0], cwd) if places else None
                continue
            if name == "write-output" and plain(args):
                name = LITERAL
            elif name == "get-content":
                reads = literals(named, "path", "literalpath")
                name = "tail" if "tail" in named or "wait" in named else "cat"
            elif name == "select-string":
                reads, name = literals(named, "path", "literalpath"), "grep"
            elif name in ("set-content", "add-content", "out-file", "tee-object"):
                writes = writes + literals(named, "path", "filepath", "literalpath")
                if "value" in named:
                    stdin = "literal" if plain(named["value"]) else "program"
                name = "tee"
            elif name == "new-item":
                if [t.lower() for t in literals(named, "itemtype")[:1]] in ([], ["file"]):
                    given = "path" in named or "literalpath" in named
                    for path in literals(named, "path", "literalpath") if given else ["."]:
                        path = os.path.join(path, *literals(named, "name")[:1])
                        target = resolve(path, cwd)
                        if "value" in named or ("force" in named and os.path.isfile(target or "")):
                            writes = [*writes, path]
                stdin = "program" if "value" in named and not plain(named["value"]) else "literal"
                name = "tee"
            elif PYTHON.fullmatch(name):
                name = python_program([v if k in ("word", "str") else OPAQUE for k, v in args])
            prev = stage(name, reads, writes, stdin, position, cwd)
            stages.append(prev)
    return out


DIALECTS = {
    "Bash": bash_pipelines,
    "PowerShell": lambda command, cwd: ps_pipelines(ps_statements(command), cwd),
}


def verdict(tool, command, cwd, root):
    return judge(DIALECTS[tool](command, cwd), root)


R, A = "refuse", "allow"
# Each row: dialect, command, verdict, and the RULES tag that refuses it (or the shape it allows).
SAMPLES = (
    ("Bash", "sed -n '1,40p' promptpotter/infrastructure/ledger.py", R, "read"),
    ("Bash", "cat scripts/gate.py", R, "read"),
    ("Bash", "head -50 docs/CLAUDE.md", R, "read"),
    ("Bash", "tail -n 20 promptpotter/main.py", R, "read"),
    ("Bash", 'grep -n "def main" scripts/gate.py', R, "read"),
    ("Bash", "grep -rn CycleEventLog promptpotter/", R, "read"),
    ("Bash", 'rg -n "useRead" webapp/lib', R, "read"),
    ("Bash", "cd promptpotter && grep -rn foo .", R, "read"),
    ("Bash", "timeout 30 grep -n x scripts/gate.py", R, "read"),
    ("Bash", "cat scripts/gate.py | grep def", R, "read"),
    ("Bash", "grep x < scripts/gate.py", R, "read"),
    ("Bash", "cat {root}/.claude/worktrees/agent-x/pyproject.toml", R, "read"),
    ("Bash", "tail -n 5 .promptpotter/projects/x/cycles/z/dashboard.json", R, "read-once"),
    ("Bash", "tail -f .scratch/events.jsonl", R, "read-once"),
    ("Bash", "sed -i 's/a/b/' README.md", R, "edit"),
    ("Bash", "cat > .scratch/notes.md <<'EOF'\nhello\nEOF", R, "author"),
    ("Bash", "cat <<EOF > docs/x.md\n# title\ncat secret.py\nEOF", R, "author"),
    ("Bash", 'echo "x = 1" > promptpotter/new.py', R, "author"),
    ("Bash", "printf '%s\\n' a b >> notes.txt", R, "author"),
    ("Bash", "echo '# t' | tee docs/x.md", R, "author"),
    ("Bash", ".venv/Scripts/python.exe - <<'EOF'\nimport json\nprint(1)\nEOF", R, "py-stdin"),
    ("Bash", "python - < .scratch/probe.py", R, "py-stdin"),
    ("Bash", "echo 'print(1)' | python -", R, "py-stdin"),
    ("Bash", "git show HEAD:x.py | python", R, "py-stdin"),
    ("Bash", "git log --oneline | grep fix", A, "filter"),
    ("Bash", "python scripts/gate.py --py 2>/dev/null | tail -15", A, "filter"),
    ("Bash", "tail -f .scratch/run.log", A, "follow"),
    ("Bash", "tail -n 100 .promptpotter/projects/x/cycles/z/readout.log", A, "follow"),
    ("Bash", "python -m promptpotter new bbeh > .scratch/bbeh.log 2>&1 &", A, "capture"),
    ("Bash", "echo $! > .scratch/pid", A, "capture"),
    ("Bash", "pytest -q | tee .scratch/pytest.log", A, "capture"),
    ("Bash", "$(git rev-parse HEAD) > .scratch/head", A, "capture"),
    ("Bash", "cp C:/Users/dsacc/AppData/Local/Temp/x.png docs/assets/x.png", A, "copy"),
    ("Bash", 'python -c "import sys; print(sys.version)"', A, "python-c"),
    ("Bash", "cat {root}/../sibling/pyproject.toml", A, "outside"),
    ("Bash", "head -c 600 C:/Users/dsacc/AppData/Local/Temp/x.json", A, "outside"),
    ("Bash", "cd /c/Users/dsacc/AppData/Local/Temp && grep -rn foo .", A, "outside"),
    ("Bash", 'cat "$LOG"', A, "unresolved"),
    ("Bash", "echo hello", A, "plain"),
    ("Bash", "echo warn >&2", A, "plain"),
    ("Bash", 'git commit -m "fix: cat foo.py\n\nhead -5 bar.py"', A, "plain"),
    ("PowerShell", "Get-Content scripts/gate.py", R, "read"),
    ("PowerShell", "gc scripts\\gate.py -TotalCount 40", R, "read"),
    ("PowerShell", "Get-Content -Path promptpotter/main.py -Tail 50", R, "read"),
    ("PowerShell", "Get-Content -Path:docs/CLAUDE.md -Encoding utf8", R, "read"),
    ("PowerShell", "gc .scratch/run.log", R, "read"),
    ("PowerShell", "Get-Content a.md, scripts/gate.py", R, "read"),
    ("PowerShell", "Microsoft.PowerShell.Management\\Get-Content pyproject.toml", R, "read"),
    ("PowerShell", "Select-String -Pattern 'def main' -Path scripts/gate.py", R, "read"),
    ("PowerShell", "$text = Get-Content scripts/gate.py -Raw", R, "read"),
    ("PowerShell", "cd promptpotter; Get-Content main.py", R, "read"),
    ("PowerShell", "if (Test-Path x) { gc scripts/gate.py }", R, "read"),
    ("PowerShell", "Get-Content {root}/.claude/worktrees/agent-x/pyproject.toml", R, "read"),
    ("PowerShell", "Get-Content -Tail 5 .promptpotter/x/dashboard.json", R, "read-once"),
    ("PowerShell", "(Get-Content README.md) -replace 'a','b' | Set-Content README.md", R, "edit"),
    ("PowerShell", "Set-Content -Path notes.md -Value 'hello'", R, "author"),
    ("PowerShell", 'sc .scratch/notes.md "text"', R, "author"),
    ("PowerShell", "Add-Content CHANGELOG.md 'line'", R, "author"),
    ("PowerShell", "'x = 1' | Out-File promptpotter/new.py -Encoding utf8", R, "author"),
    ("PowerShell", '"text" > docs/x.md', R, "author"),
    ("PowerShell", "Write-Output 'a' >> notes.txt", R, "author"),
    ("PowerShell", "@'\nhello\n'@ | Set-Content .scratch/notes.md", R, "author"),
    ("PowerShell", "'# t' | Tee-Object -FilePath docs/x.md", R, "author"),
    ("PowerShell", "New-Item -ItemType File -Force pyproject.toml", R, "author"),
    ("PowerShell", "ni -Path docs -Name new.md -Value '# t'", R, "author"),
    ("PowerShell", "@'\nimport json\n'@ | .venv/Scripts/python.exe -", R, "py-stdin"),
    ("PowerShell", "git show HEAD:x.py | python -", R, "py-stdin"),
    ("PowerShell", "git log --oneline | Select-String fix", A, "filter"),
    ("PowerShell", "gci promptpotter -Recurse | Select-String -Pattern TODO", A, "filter"),
    ("PowerShell", ".venv\\Scripts\\python.exe -m pytest | Select-Object -Last 15", A, "filter"),
    ("PowerShell", "Get-Content .scratch/run.log -Tail 50", A, "follow"),
    ("PowerShell", "gc logs\\latest.log -Wait", A, "follow"),
    ("PowerShell", "Get-Content -Tail 100 .promptpotter/projects/x/readout.log", A, "follow"),
    ("PowerShell", "python -m promptpotter new bbeh > .scratch/bbeh.log 2>&1", A, "capture"),
    ("PowerShell", "& $py -m pytest > .scratch/x.log", A, "capture"),
    ("PowerShell", "(python scripts/gate.py --py) > .scratch/x.log", A, "capture"),
    ("PowerShell", "& '.venv/Scripts/python.exe' -m pytest > .scratch/pytest.log", A, "capture"),
    ("PowerShell", "python scripts/gate.py 2>&1 | Out-File .scratch/gate.log", A, "capture"),
    ("PowerShell", "Get-ChildItem | Tee-Object -FilePath docs/listing.md", A, "capture"),
    ("PowerShell", "ac -Path .scratch/x.md -Value $text", A, "capture"),
    ("PowerShell", "Copy-Item C:/Users/dsacc/AppData/Local/Temp/x.toml pyproject.toml", A, "copy"),
    ("PowerShell", "Move-Item .scratch/a.log .scratch/b.log", A, "copy"),
    ("PowerShell", "python -c 'print(1)'", A, "python-c"),
    ("PowerShell", "Get-Content {root}/../sibling/pyproject.toml", A, "outside"),
    ("PowerShell", "Get-Content C:/Users/dsacc/AppData/Local/Temp/x.txt", A, "outside"),
    ("PowerShell", "gc ~/notes.txt", A, "outside"),
    ("PowerShell", "cd $env:TEMP; Get-Content x.txt", A, "unresolved"),
    ("PowerShell", "Get-Content $path", A, "unresolved"),
    ("PowerShell", 'Get-Content "$root\\x.py"', A, "unresolved"),
    ("PowerShell", "Get-ChildItem | ForEach-Object { Get-Content $_.FullName }", A, "unresolved"),
    ("PowerShell", '"done" > $null', A, "plain"),
    ("PowerShell", "Write-Output 'warn' 2>&1", A, "plain"),
    ("PowerShell", 'git commit -m "cat foo.py; gc bar.py"', A, "plain"),
    ("PowerShell", "New-Item -ItemType File .scratch/never-there-yet.md", A, "plain"),
)


def selftest():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    root, shown, misses, spent = resolve(here, here), here.replace("\\", "/"), [], []
    for tool, command, expected, tag in SAMPLES:
        command = command.replace("{root}", shown)
        began = time.perf_counter()
        found = verdict(tool, command, root, root)
        spent.append((time.perf_counter() - began) * 1000)
        if (R if found else A) != expected or (found and found[0] != tag):
            misses.append(f"MISS {tool} want {expected} {tag}: {command!r} -> {found}")
    spent.sort()
    counts = ", ".join(f"{t} {sum(s[0] == t for s in SAMPLES)}" for t in DIALECTS)
    for miss in misses:
        print(miss)
    print(
        f"shell_file_io selftest: {len(SAMPLES) - len(misses)}/{len(SAMPLES)} ({counts}); "
        f"decide p50 {spent[len(spent) // 2]:.3f} ms, max {spent[-1]:.3f} ms"
    )
    return 1 if misses else 0


def main():
    payload = json.load(sys.stdin)
    tool = payload["tool_name"]
    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = os.path.normcase(os.path.abspath(payload.get("cwd") or os.getcwd()))
    root = resolve(os.environ["CLAUDE_PROJECT_DIR"], cwd)
    found = verdict(tool, command, cwd, root)
    if found:
        print(
            f"{tool} refused: this command {found[1]}; filtering or capturing a program's output "
            "stays allowed.",
            file=sys.stderr,
        )
        sys.exit(2)


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        sys.exit(selftest())
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
